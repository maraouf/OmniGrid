"""OIDC (OpenID Connect) SSO for OmniGrid.

Authorization-code flow + PKCE (S256). Authentik is the reference IdP
but the implementation is issuer-agnostic — any compliant provider that
advertises ``.well-known/openid-configuration`` and signs id_tokens
with keys reachable via ``jwks_uri`` should work.

Config is UI-managed (DB-backed via :mod:`logic.auth`'s auth-settings
cache). No env-var reads for OIDC — first-time admins configure the
provider from the Settings panel after bootstrap login.

Library choice: ``PyJWT[crypto]`` + ``httpx``. Lighter than authlib and
we already own the discovery + token-exchange httpx calls. PyJWT handles
signature verification against the JWKS; we do issuer / audience /
expiry / nonce checks ourselves.

Flow:
  1. Browser hits /api/oidc/login?next=<path>. Server generates state,
     nonce, PKCE verifier, stores them in a short-lived HTTP-only
     cookie, 302s to the issuer's authorization_endpoint with the PKCE
     challenge.
  2. IdP prompts user, redirects back to /api/oidc/callback?code&state.
  3. Server verifies state + nonce + PKCE against the flow cookie,
     POSTs to token_endpoint with the code + verifier, receives
     id_token + access_token.
  4. Server validates the id_token (signature, issuer, audience, exp,
     nonce), extracts email / preferred_username / groups, calls
     ``auth.auto_provision_authentik()``, mints a normal ``og_session``
     cookie, 302s the browser to the validated ``next`` path.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from logic import auth
from logic.db import db_conn


# ----------------------------------------------------------------------------
# Flow cookie (PKCE verifier + state + nonce + next path)
# ----------------------------------------------------------------------------
FLOW_COOKIE = "og_oidc_flow"
FLOW_COOKIE_TTL = 300  # 5 minutes — enough for the user to click Approve on
                      # Authentik's consent screen, short enough to limit the
                      # blast radius of a stolen in-flight cookie.

# Discovery + JWKS caches. Keyed by issuer URL so a config change to a new
# issuer invalidates naturally. Swap in LRU if we ever serve multiple IdPs.
_discovery_cache: dict[str, tuple[dict, float]] = {}  # issuer -> (doc, expires_at)
_jwks_cache: dict[str, tuple[dict, float]] = {}       # issuer -> (jwks, expires_at)

DISCOVERY_TTL = 3600  # 1 hour — long enough to avoid hammering the IdP, short
                    # enough that an endpoint URL change propagates the same
                    # day. Admins who need instant refresh should click the
                    # "Test connection" button which calls invalidate_cache().
JWKS_TTL = 3600     # Same rationale. Unknown `kid` on a token forces a refresh
                    # mid-flow, so key rotation takes effect immediately
                    # without waiting out the TTL.


def invalidate_cache() -> None:
    """Drop both the discovery and JWKS caches — call from POST /api/settings
    when OIDC keys change so the next flow picks up the new values."""
    _discovery_cache.clear()
    _jwks_cache.clear()


# ----------------------------------------------------------------------------
# Settings accessor — thin wrapper so callers don't have to thread a conn.
# ----------------------------------------------------------------------------
def _settings() -> dict:
    with db_conn() as c:
        return auth.get_auth_settings(c)


def _verify_tls() -> bool:
    # True when OmniGrid should verify the issuer's TLS cert against its
    # trust store. Homelab installs behind an internal CA flip this off via
    # the Settings → Authentik OIDC panel; the default stays on so
    # public issuers aren't silently downgraded.
    return bool(_settings().get("oidc_verify_tls", True))


def is_configured() -> bool:
    """True when OIDC is enabled AND the three mandatory values are set.

    The login button / SSO routes gate on this — a half-configured OIDC
    block should 503 rather than blow up mid-flow with a cryptic error.
    """
    s = _settings()
    if not s.get("oidc_enabled"):
        return False
    return bool(s.get("oidc_issuer_url")) and bool(s.get("oidc_client_id")) \
        and bool(s.get("oidc_client_secret"))


# ----------------------------------------------------------------------------
# Discovery / JWKS fetching
# ----------------------------------------------------------------------------
async def _fetch_discovery(issuer: str) -> dict:
    """GET ``{issuer}/.well-known/openid-configuration``. Cached per-issuer.

    Returns the full discovery document — callers pick out the endpoints
    they need (authorization_endpoint, token_endpoint, jwks_uri).
    """
    now = time.time()
    cached = _discovery_cache.get(issuer)
    if cached and cached[1] > now:
        return cached[0]
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=15.0, verify=_verify_tls()) as client:
        r = await client.get(url)
        if r.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"OIDC discovery failed: HTTP {r.status_code} from {url}",
            )
        doc = r.json()
    _discovery_cache[issuer] = (doc, now + DISCOVERY_TTL)
    return doc


async def _fetch_jwks(issuer: str, jwks_uri: str, force: bool = False) -> dict:
    """Fetch JWKS; cached per-issuer. ``force=True`` bypasses the TTL — used
    when we encounter a `kid` that isn't in the cached set (key rotation).
    """
    now = time.time()
    if not force:
        cached = _jwks_cache.get(issuer)
        if cached and cached[1] > now:
            return cached[0]
    async with httpx.AsyncClient(timeout=15.0, verify=_verify_tls()) as client:
        r = await client.get(jwks_uri)
        if r.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"OIDC JWKS fetch failed: HTTP {r.status_code}",
            )
        jwks = r.json()
    _jwks_cache[issuer] = (jwks, now + JWKS_TTL)
    return jwks


# ----------------------------------------------------------------------------
# "Test connection" helper — pure discovery GET, no side effects.
# ----------------------------------------------------------------------------
async def test_discovery(issuer_url: str) -> dict:
    """GET the discovery doc and return a compact {ok, status, detail}.

    Called by the admin "Test connection" button in the Settings panel.
    Never raises — returns ok=False with the HTTP status / error for the
    admin to read. Bypasses the cache because the admin clicked Test to
    check the live endpoint.
    """
    if not issuer_url:
        return {"ok": False, "status": 0, "detail": "Issuer URL is empty"}
    url = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=_verify_tls()) as client:
            r = await client.get(url)
        if r.status_code == 200:
            # Basic sanity check: discovery doc must advertise the three
            # endpoints we rely on, otherwise the flow will 500 later.
            doc = r.json()
            required = ("authorization_endpoint", "token_endpoint", "jwks_uri")
            missing = [k for k in required if not doc.get(k)]
            if missing:
                return {
                    "ok": False,
                    "status": 200,
                    "detail": f"Discovery doc missing required keys: {', '.join(missing)}",
                }
            return {"ok": True, "status": 200, "detail": f"OK — issuer: {doc.get('issuer', 'unknown')}"}
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code} from {url}"}
    except Exception as e:
        return {"ok": False, "status": 0, "detail": f"{type(e).__name__}: {e}"}


# ----------------------------------------------------------------------------
# Flow cookie: HMAC-signed JSON blob. We can't use the normal session
# cookie because the user isn't authenticated yet.
# ----------------------------------------------------------------------------
def _sign_flow(payload: str) -> str:
    sig = hmac.new(auth.SESSION_SECRET, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")


def _encode_flow(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":"))
    b64 = base64.urlsafe_b64encode(raw.encode("utf-8")).rstrip(b"=").decode("ascii")
    return f"{b64}.{_sign_flow(b64)}"


def _decode_flow(cookie: str) -> Optional[dict]:
    try:
        b64, sig = cookie.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign_flow(b64)):
        return None
    pad = "=" * (-len(b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(b64 + pad)
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    # Enforce TTL server-side too — cookies with expired TTLs shouldn't
    # survive a slow network hop that prevents the browser from clearing.
    if data.get("exp", 0) < int(time.time()):
        return None
    return data


# ----------------------------------------------------------------------------
# PKCE helpers (S256)
# ----------------------------------------------------------------------------
def _pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge). verifier is 43-128 url-safe chars,
    challenge is base64url-nopad of SHA-256(verifier)."""
    # 64 bytes → 86 chars url-safe; well within the 43-128 RFC range.
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ----------------------------------------------------------------------------
# `next` validation — open-redirect defense.
# ----------------------------------------------------------------------------
def _safe_next(value: Optional[str]) -> str:
    """Return a same-origin relative path or ``/``. Rejects schemes and
    protocol-relative URLs that could send the user off-site after login.
    """
    if not value:
        return "/"
    # Reject anything that isn't a plain same-origin path.
    if not value.startswith("/"):
        return "/"
    if value.startswith("//") or value.startswith("/\\"):
        return "/"
    # Windows back-slash path-escape shouldn't be possible on Unix but
    # cheap to reject anyway — some user-agents normalise.
    if "\\" in value:
        return "/"
    return value


# ----------------------------------------------------------------------------
# Route bodies — wired up in main.py.
# ----------------------------------------------------------------------------
async def login(request: Request):
    """Start the OIDC flow. Generates state/nonce/PKCE, stashes them in a
    flow cookie, 302s to the IdP's authorization_endpoint."""
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="OIDC is not configured. Ask an admin to set it up in Settings → Authentik OIDC.",
        )
    s = _settings()
    issuer = s["oidc_issuer_url"]
    client_id = s["oidc_client_id"]
    configured_redirect = (s.get("oidc_redirect_uri") or "").strip()
    redirect_uri = configured_redirect or _default_redirect_uri(request)
    scopes = s.get("oidc_scopes") or "openid email profile groups"

    # Diagnostic log — prints to docker service logs so we can see
    # EXACTLY what redirect_uri we're sending to the IdP and whether it
    # came from the DB override or the request-origin auto-compute.
    # Paste this into Authentik's Redirect URIs allowlist byte-for-byte
    # if they don't match.
    source = "DB override" if configured_redirect else "auto-computed from request origin"
    host_hdr = request.headers.get("host") or "(no host)"
    fwd_host = request.headers.get("x-forwarded-host") or "(none)"
    fwd_proto = request.headers.get("x-forwarded-proto") or "(none)"
    print(
        f"[oidc] /login redirect_uri={redirect_uri!r} source={source} "
        f"host={host_hdr!r} x-forwarded-host={fwd_host!r} x-forwarded-proto={fwd_proto!r}"
    )

    try:
        doc = await _fetch_discovery(issuer)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OIDC discovery error: {e}")

    auth_ep = doc.get("authorization_endpoint")
    if not auth_ep:
        raise HTTPException(status_code=502, detail="OIDC discovery missing authorization_endpoint")

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    next_path = _safe_next(request.query_params.get("next"))

    flow = {
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "next": next_path,
        "exp": int(time.time()) + FLOW_COOKIE_TTL,
    }
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    resp = RedirectResponse(url=f"{auth_ep}?{urlencode(params)}", status_code=302)
    resp.set_cookie(
        key=FLOW_COOKIE,
        value=_encode_flow(flow),
        max_age=FLOW_COOKIE_TTL,
        httponly=True,
        secure=_is_https(request),
        samesite="lax",  # lax so the IdP redirect back carries the cookie
        path="/api/oidc/",  # scoped to the callback path — no other route needs it
    )
    return resp


async def callback(request: Request):
    """Complete the OIDC flow and mint an og_session cookie."""
    if not is_configured():
        raise HTTPException(status_code=503, detail="OIDC is not configured")

    # Rate-limit per IP the same way /api/local-auth/login does. Stops a
    # runaway loop from hammering the token endpoint on misconfigured
    # deploys, and makes bruteforcing the state/nonce pair pointless.
    ip = auth._client_ip(request)
    auth.rate_limit_check(ip)

    flow_cookie = request.cookies.get(FLOW_COOKIE)
    if not flow_cookie:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(status_code=400, detail="Missing OIDC flow cookie (did the flow time out?)")
    flow = _decode_flow(flow_cookie)
    if not flow:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(status_code=400, detail="Invalid OIDC flow cookie")

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(status_code=400, detail="Missing code/state in callback")
    if not hmac.compare_digest(state, flow["state"]):
        auth.rate_limit_record_failure(ip)
        raise HTTPException(status_code=400, detail="OIDC state mismatch")

    s = _settings()
    issuer = s["oidc_issuer_url"]
    client_id = s["oidc_client_id"]
    client_secret = s["oidc_client_secret"]
    redirect_uri = s.get("oidc_redirect_uri") or _default_redirect_uri(request)
    admin_group = s.get("oidc_admin_group") or ""

    doc = await _fetch_discovery(issuer)
    token_ep = doc.get("token_endpoint")
    jwks_uri = doc.get("jwks_uri")
    if not token_ep or not jwks_uri:
        raise HTTPException(status_code=502, detail="OIDC discovery missing token_endpoint / jwks_uri")

    # Token exchange. Authentik accepts client credentials in either the
    # Authorization header or the body; we use the body for clarity.
    async with httpx.AsyncClient(timeout=15.0, verify=_verify_tls()) as client:
        token_resp = await client.post(
            token_ep,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": flow["verifier"],
            },
            headers={"Accept": "application/json"},
        )
    if token_resp.status_code != 200:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=401,
            detail=f"OIDC token exchange failed: HTTP {token_resp.status_code} — {token_resp.text[:300]}",
        )
    tok = token_resp.json()
    id_token = tok.get("id_token")
    if not id_token:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(status_code=502, detail="OIDC token response missing id_token")

    # --- Validate id_token -------------------------------------------------
    # Per RFC 8414 / OIDC Core, the `iss` claim MUST EXACTLY match the
    # value the discovery doc advertises — byte-for-byte, trailing slash
    # included. Do NOT strip anything from it; whatever the IdP publishes
    # in `openid-configuration.issuer` is what the id_token will carry.
    # Fall back to the admin-typed URL only if the doc is non-compliant.
    expected_iss = doc.get("issuer") or issuer
    try:
        claims = await _validate_id_token(
            id_token, issuer=issuer, jwks_uri=jwks_uri,
            expected_iss=expected_iss,
            client_id=client_id, expected_nonce=flow["nonce"],
        )
    except jwt.InvalidIssuerError as e:
        # Dig out the actual iss in the token so the operator can spot
        # trailing-slash / host mismatches without reaching for jwt.io.
        try:
            actual = jwt.decode(id_token, options={"verify_signature": False}).get("iss", "?")
        except Exception:
            actual = "?"
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=401,
            detail=(f"id_token validation failed: Invalid issuer. "
                    f"Expected {expected_iss!r}, got {actual!r}."),
        )
    except jwt.PyJWTError as e:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(status_code=401, detail=f"id_token validation failed: {e}")

    email = (claims.get("email") or "").strip().lower()
    if not email:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(status_code=400, detail="id_token missing 'email' claim — ensure the 'email' scope is granted")
    username = (claims.get("preferred_username") or claims.get("nickname") or email).strip()
    groups = claims.get("groups") or []
    if isinstance(groups, str):
        # Some providers emit a space- or comma-separated string instead of a list.
        groups = [g.strip() for g in groups.replace(",", " ").split() if g.strip()]

    # Clear the rate-limit bucket only on fully-successful validation.
    auth.rate_limit_clear(ip)

    # --- Provision user + mint session ------------------------------------
    with db_conn() as c:
        u = auth.auto_provision_authentik(c, email, username, list(groups))
        if u.disabled:
            raise HTTPException(status_code=403, detail="Account disabled")
        auth.touch_last_login(c, u.id)
        cookie_value, expires_at = auth.create_session(
            c, u.id, ip, request.headers.get("user-agent"),
        )

    next_path = _safe_next(flow.get("next"))
    resp = RedirectResponse(url=next_path, status_code=302)
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, auth.generate_csrf_token(), expires_at, request)
    # Clear the flow cookie — one-shot.
    resp.delete_cookie(FLOW_COOKIE, path="/api/oidc/")
    return resp


async def _validate_id_token(
    id_token: str, *, issuer: str, jwks_uri: str,
    expected_iss: Optional[str] = None,
    client_id: str, expected_nonce: str,
) -> dict:
    """Verify signature + standard claims. Returns decoded claims on
    success, raises jwt.PyJWTError otherwise.

    ``issuer`` is the configured URL (used as the JWKS cache key).
    ``expected_iss`` is what PyJWT checks the `iss` claim against — the
    discovery doc's `issuer` field, which the provider guarantees will
    match the id_token's `iss` byte-for-byte. Falls back to the raw
    ``issuer`` string if the caller didn't resolve one; never strip a
    trailing slash from it — the spec requires exact equality.

    JWKS is refreshed on unknown `kid` so mid-rotation tokens are
    accepted without waiting out the cache TTL.
    """
    # Peek at the header to find the `kid` without verifying yet.
    header = jwt.get_unverified_header(id_token)
    kid = header.get("kid")
    alg = header.get("alg")
    if not alg:
        raise jwt.InvalidTokenError("id_token missing 'alg' in header")

    jwks = await _fetch_jwks(issuer, jwks_uri)
    key = _find_key(jwks, kid)
    if key is None:
        # Key rotation — bypass cache once.
        jwks = await _fetch_jwks(issuer, jwks_uri, force=True)
        key = _find_key(jwks, kid)
    if key is None:
        raise jwt.InvalidTokenError(f"No matching JWKS key for kid={kid!r}")

    # Build a public key object PyJWT can verify with.
    public_key = jwt.PyJWK(key).key  # type: ignore[attr-defined]

    claims = jwt.decode(
        id_token,
        key=public_key,
        algorithms=[alg],
        audience=client_id,
        issuer=(expected_iss or issuer),
        options={"require": ["exp", "iat", "iss", "aud"]},
        leeway=30,  # small clock-skew tolerance — tokens usually live 5+ minutes anyway
    )
    # Nonce is not checked by PyJWT — it's an OIDC-specific claim.
    nonce = claims.get("nonce")
    if not nonce or not hmac.compare_digest(str(nonce), expected_nonce):
        raise jwt.InvalidTokenError("Nonce mismatch")
    return claims


def _find_key(jwks: dict, kid: Optional[str]) -> Optional[dict]:
    keys = jwks.get("keys") or []
    if kid is None:
        return keys[0] if keys else None
    for k in keys:
        if k.get("kid") == kid:
            return k
    return None


# ----------------------------------------------------------------------------
# Helpers used by both routes
# ----------------------------------------------------------------------------
def _is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "").lower()
    return proto == "https" or request.url.scheme == "https"


def _default_redirect_uri(request: Request) -> str:
    """Compute the callback URL from the request origin. Only used when
    the admin hasn't pinned one in Settings — pinning is strongly
    recommended because IdPs require redirect URIs to be exact-match
    allowlisted."""
    scheme = "https" if _is_https(request) else "http"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{scheme}://{host}/api/oidc/callback"


def public_redirect_uri(request: Request) -> str:
    """What to display in the "Redirect URI" field of the Settings panel
    when the admin hasn't pinned one. Computed from the request origin
    so the Copy button writes exactly what IdPs need in their allowlist.
    """
    return _default_redirect_uri(request)
