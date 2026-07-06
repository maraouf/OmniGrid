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
import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from logic import auth
from logic import errors as _err
from logic import oidc_providers as _providers
from logic.db import db_conn

# ----------------------------------------------------------------------------
# Flow cookie (PKCE verifier + state + nonce). The post-login
# redirect target (`next` path) is NOT stored in the cookie — it
# lives in a server-side ``_flow_paths`` dict keyed by ``state``
# (which is a server-generated 24-byte nonce). This keeps the user-
# controllable ``?next=`` value entirely off the request → response
# round-trip path that CodeQL's ``py/url-redirection`` taint tracker
# follows: the cookie is admin-grade signed but CodeQL doesn't model
# HMAC verification as a sanitiser, so previous rounds of fixes
# inside ``_safe_next`` (regex match → m.group(0)) still got
# flagged. The dict lookup at callback breaks the dataflow chain
# because the value came from server storage, not from the request.
# ----------------------------------------------------------------------------
FLOW_COOKIE = "og_oidc_flow"
FLOW_COOKIE_TTL = 300  # 5 minutes — enough for the user to click Approve on
# Authentik's consent screen, short enough to limit the
# blast radius of a stolen in-flight cookie.

# state token → validated next-path. Populated at login start (after
# ``_safe_next`` has whitelisted the path); consumed at callback via
# ``.pop()`` so each entry is one-shot. Also pruned opportunistically
# at every login start when the dict grows past a small cap so a
# burst of half-finished flows can't accumulate forever.
_flow_paths: dict[str, tuple[str, float]] = {}
_FLOW_PATHS_MAX = 256  # opportunistic cap; cleanup walks this on overflow.


def _flow_paths_remember(state: str, path: str) -> None:
    """Stash a server-validated next-path under the server-generated
    state token. Idempotent + bounded — opportunistically prunes
    expired entries when the dict crosses ``_FLOW_PATHS_MAX``.
    """
    now = time.time()
    if len(_flow_paths) >= _FLOW_PATHS_MAX:
        cutoff = now - FLOW_COOKIE_TTL
        for k in [k for k, (_, ts) in _flow_paths.items() if ts < cutoff]:
            _flow_paths.pop(k, None)
    _flow_paths[state] = (path, now)


def _flow_paths_consume(state: str) -> str:
    """Pop the stored path for ``state`` and return it. Falls back to
    ``"/"`` if the entry is missing or expired. Returns a literal
    constant on every reject path so the value passed downstream to
    ``RedirectResponse(url=...)`` is either a server-stashed path
    (already vetted by ``_safe_next`` at login start) or a literal.
    Re-validates through ``_safe_next`` defensively in case the dict
    is ever populated by something other than the login route.
    """
    entry = _flow_paths.pop(state, None)
    if not entry:
        return "/"
    path, ts = entry
    if time.time() - ts > FLOW_COOKIE_TTL:
        return "/"
    return _safe_next(path)


# Discovery + JWKS caches. Keyed by issuer URL so a config change to a new
# issuer invalidates naturally. Swap in LRU if we ever serve multiple IdPs.
_discovery_cache: dict[str, tuple[dict, float]] = {}  # issuer -> (doc, expires_at)
_jwks_cache: dict[str, tuple[dict, float]] = {}  # issuer -> (jwks, expires_at)

DISCOVERY_TTL = 3600  # 1 hour — long enough to avoid hammering the IdP, short
# enough that an endpoint URL change propagates the same
# day. Admins who need instant refresh should click the
# "Test connection" button which calls invalidate_cache().
JWKS_TTL = 3600  # Same rationale. Unknown `kid` on a token forces a refresh
# mid-flow, so key rotation takes effect immediately
# without waiting out the TTL.

# Asymmetric signing algorithms allowed for id_token verification
# . Listed in the OIDC core spec as the set providers may use
# for id_tokens. Symmetric algorithms (HS256/HS384/HS512) are NOT in
# this set: they'd require the operator to share the client_secret as
# the verification key, and the spec prefers asymmetric so the JWKS
# remains the public source of truth. ``alg=none`` is never on the
# whitelist — even if PyJWT regresses, ``_validate_id_token`` rejects
# the token before reaching ``jwt.decode``.
_ALLOWED_ID_TOKEN_ALGORITHMS = frozenset({
    "RS256", "RS384", "RS512",
    "ES256", "ES384", "ES512",
    "PS256", "PS384", "PS512",
    "EdDSA",
})


def invalidate_cache() -> None:
    """Drop both the discovery and JWKS caches — call from POST /api/settings
    when OIDC keys change so the next flow picks up the new values."""
    _discovery_cache.clear()
    _jwks_cache.clear()


# ----------------------------------------------------------------------------
# Settings accessor — thin wrapper so callers don't have to thread a conn.
# ----------------------------------------------------------------------------
def _settings() -> dict:
    """Load the auth settings dict (opens its own DB connection)."""
    with db_conn() as c:
        return auth.get_auth_settings(c)


def _pget(provider, name: str, default=None):
    """Read a provider's namespaced setting by leaf ``name`` from the auth
    settings dict (e.g. ``issuer_url`` → ``oidc_issuer_url`` for Authentik,
    ``oidc_unifiedsso_issuer_url`` for UnifiedSSO)."""
    return _settings().get(_providers.setting_key(provider, name), default)


def _verify_tls(provider) -> bool:
    """True when OmniGrid should verify this provider's issuer TLS cert (DB-backed
    per provider; defaults on so a public issuer isn't silently downgraded).
    Homelab installs behind an internal CA flip it off in the provider's panel."""
    return bool(_pget(provider, "verify_tls", True))


def _http_timeout_seconds() -> float:
    """Resolve the outbound HTTP wall-clock for every oidc.py call site.

    Per-use read of the ``tuning_oidc_http_timeout_seconds`` TUNABLE so
    a Save in Admin → Authentik OIDC takes effect on the next call
    without restart. Defensive fallback to legacy 15s on tunable-
    resolver failure — keeps OIDC working if the tuning module is
    misconfigured rather than blocking auth entirely.
    """
    try:
        from logic.tuning import Tunable, tuning_int as _tuning_int
        return float(_tuning_int(Tunable.OIDC_HTTP_TIMEOUT_SECONDS))
    except (ImportError, AttributeError, KeyError, TypeError, ValueError):
        return 15.0


def is_configured(provider_id: str = _providers.DEFAULT_PROVIDER_ID) -> bool:
    """True when the given provider is enabled AND its three mandatory values
    are set. The login button / SSO routes gate on this — a half-configured
    provider should 503 rather than blow up mid-flow with a cryptic error.
    An unknown provider id is treated as not-configured.
    """
    p = _providers.get(provider_id)
    if p is None:
        return False
    if not _pget(p, "enabled"):
        return False
    return bool(_pget(p, "issuer_url")) and bool(_pget(p, "client_id")) \
        and bool(_pget(p, "client_secret"))


# ----------------------------------------------------------------------------
# Discovery / JWKS fetching
# ----------------------------------------------------------------------------
async def _fetch_discovery(issuer: str, verify_tls: bool = True) -> dict:
    """GET ``{issuer}/.well-known/openid-configuration``. Cached per-issuer.

    Returns the full discovery document — callers pick out the endpoints
    they need (authorization_endpoint, token_endpoint, jwks_uri).
    ``verify_tls`` is the calling provider's TLS-verify setting.
    """
    now = time.time()
    cached = _discovery_cache.get(issuer)
    if cached and cached[1] > now:
        return cached[0]
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=_http_timeout_seconds(), verify=verify_tls) as client:
        r = await client.get(url)
        if r.status_code != 200:
            # Log before raising so a discovery failure (e.g. an upstream 502
            # from the IdP's proxy) shows in Admin → Logs — otherwise the
            # HTTPException surfaces only in the HTTP response, never the log.
            lvl = "error"
            print(f"[oidc] {lvl} discovery HTTP {r.status_code} from {url} "
                  f"— body {r.text[:200]!r}")
            raise HTTPException(
                status_code=502,
                detail=f"OIDC discovery failed: HTTP {r.status_code} from {url}",
            )
        doc = r.json()
    _discovery_cache[issuer] = (doc, now + DISCOVERY_TTL)
    return doc


async def _fetch_jwks(issuer: str, jwks_uri: str, verify_tls: bool = True, force: bool = False) -> dict:
    """Fetch JWKS; cached per-issuer. ``force=True`` bypasses the TTL — used
    when we encounter a `kid` that isn't in the cached set (key rotation).
    ``verify_tls`` is the calling provider's TLS-verify setting.
    """
    now = time.time()
    if not force:
        cached = _jwks_cache.get(issuer)
        if cached and cached[1] > now:
            return cached[0]
    async with httpx.AsyncClient(timeout=_http_timeout_seconds(), verify=verify_tls) as client:
        r = await client.get(jwks_uri)
        if r.status_code != 200:
            lvl = "error"
            print(f"[oidc] {lvl} JWKS HTTP {r.status_code} from {jwks_uri} "
                  f"— body {r.text[:200]!r}")
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
async def test_discovery(issuer_url: str, verify_tls: Optional[bool] = None) -> dict:
    """GET the discovery doc and return a compact {ok, status, detail}.

    Called by the admin "Test connection" button in the Settings panel.
    Never raises — returns ok=False with the HTTP status / error for the
    admin to read. Bypasses the cache because the admin clicked Test to
    check the live endpoint.

    ``verify_tls`` overrides the default when supplied — the form sends the
    in-flight checkbox state so an admin can flip "Verify TLS" OFF, paste a
    self-signed issuer, and Test before saving. Default ``None`` verifies TLS
    (safe default); this is a provider-agnostic probe so it doesn't read any
    provider's saved setting.
    """
    if not issuer_url:
        return {"ok": False, "status": 0, "detail": "Issuer URL is empty"}
    # Defence-in-depth on the admin-only OIDC issuer URL. CodeQL
    # py/full-ssrf flags `client.get(url)` below — see
    # ``logic/url_safety.py`` for the threat-model rationale.
    from logic.url_safety import is_safe_http_url as _safe_url
    if not _safe_url(issuer_url):
        return {"ok": False, "status": 0,
                "detail": "Issuer URL must be http:// or https:// with a hostname"}
    url = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    effective_verify = True if verify_tls is None else bool(verify_tls)
    try:
        async with httpx.AsyncClient(timeout=_http_timeout_seconds(), verify=effective_verify) as client:
            r = await client.get(url)  # lgtm[py/full-ssrf]
        if r.status_code == 200:
            # Basic sanity check: discovery doc must advertise the three
            # endpoints we rely on, otherwise the flow will 500 later.
            # JSONDecodeError gets its own clearer branch — the SPA
            # showed a bare ✗ with no message on a 200-but-HTML
            # response (user pointed at the IdP login page instead of
            # the issuer root); now we tell them why.
            try:
                doc = r.json()
            except (ValueError, json.JSONDecodeError) as je:
                return {
                    "ok": False,
                    "status": 200,
                    "detail": (
                        f"Issuer URL returned HTTP 200 but the body is not JSON "
                        f"({type(je).__name__}). Likely pointing at the IdP "
                        f"login page or app dashboard instead of the issuer "
                        f"root. The OmniGrid backend appends "
                        f"`/.well-known/openid-configuration` automatically — "
                        f"paste just the issuer (e.g. "
                        f"`https://auth.example.com/application/o/<slug>/`)."
                    ),
                }
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
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        # httpx ConnectTimeout / ConnectError frequently stringify to an
        # empty string, leaving the SPA with `detail: "ConnectTimeout: "`
        # — same anti-pattern that bit telegram_listener / registry.py
        # before they were patched. Fall back to the class name when
        # str(e) is empty so the user always sees an exception NAME at
        # minimum. Also detect the common DNS-failure pattern and
        # surface a copy-paste-ready remediation hint.
        msg = str(e).strip()
        klass = type(e).__name__
        lower = (msg + " " + klass).lower()
        # DNS resolution failure — matches the libc / asyncio resolver
        # error strings seen on Docker bridge networks.
        if ("name or service not known" in lower
                or "getaddrinfo failed" in lower
                or "temporary failure in name resolution" in lower
                or "nodename nor servname provided" in lower):
            return {"ok": False, "status": 0, "detail": (
                f"{klass}: DNS resolution failed for {url}. The container's "
                "libc resolver couldn't find this hostname. Use the FQDN "
                "(e.g. `auth.example.com`) instead of a short name, OR add "
                "a Docker `--dns` / compose `extra_hosts:` entry."
            )}
        # TCP connect timeout — host unreachable / firewall dropping
        # packets. node_exporter.py has the same diagnosis text.
        if "connecttimeout" in lower:
            return {"ok": False, "status": 0, "detail": (
                f"{klass}: TCP connect timeout to {url}. The kernel got no "
                "SYN-ACK back — host is offline, port is wrong, or a firewall "
                "is silently dropping packets from the OmniGrid container's "
                "network to this host."
            )}
        # Connection refused — host reachable, port closed.
        if "connectionrefused" in lower or "connection refused" in lower:
            return {"ok": False, "status": 0, "detail": (
                f"{klass}: connection refused at {url}. Host is reachable "
                "but NOTHING is listening on this port — wrong port number, "
                "or the IdP is bound to a different address."
            )}
        # Catch-all: include both class name AND msg when msg is
        # populated; fall back to class name alone when msg is empty.
        return {"ok": False, "status": 0,
                "detail": f"{klass}: {msg}" if msg else klass}


# ----------------------------------------------------------------------------
# Flow cookie: HMAC-signed JSON blob. We can't use the normal session
# cookie because the user isn't authenticated yet.
# ----------------------------------------------------------------------------
def _sign_flow(payload: str) -> str:
    """HMAC-SHA256 sign a flow-state payload with ``SESSION_SECRET`` (base64url,
    padding stripped)."""
    sig = hmac.new(auth.SESSION_SECRET, payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")


def _encode_flow(data: dict) -> str:
    """Serialise + sign the OIDC flow-state dict into a ``<b64>.<sig>`` cookie
    value (carried across the redirect to the IdP and back)."""
    raw = json.dumps(data, separators=(",", ":"))
    b64 = base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode("ascii")
    return f"{b64}.{_sign_flow(b64)}"


def _decode_flow(cookie: str) -> Optional[dict]:
    """Verify + decode a signed flow-state cookie; ``None`` on a bad signature /
    malformed value."""
    try:
        b64, sig = cookie.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign_flow(b64)):
        return None
    pad = "=" * (-len(b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(b64 + pad)
        data = json.loads(raw.decode())
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
_SAFE_NEXT_PATH_RE = re.compile(r"/[A-Za-z0-9/_.~%?#&=:@!$'()*+,;\[\]\-]*")
_SAFE_NEXT_FALLBACK = "/"


def _safe_next(value: Optional[str]) -> str:
    """Return a same-origin relative path or ``/``.

    Three-stage validation chosen to satisfy CodeQL's
    ``py/url-redirection`` taint tracker (regex-allow-list-then-take-
    Match.group is the contract it recognises; previous attempts
    using `re.match` + return value verbatim were still flagged):

      1. Reject obvious off-site forms by prefix (no leading slash,
         protocol-relative ``//``, Windows-style ``/`` followed by a
         backslash, or any backslash anywhere — some user-agents
         normalise backslashes to forward slashes mid-flight, which
         can re-introduce a ``//attacker.example`` netloc after a
         pure prefix check has already passed).
      2. ``re.fullmatch`` the value against an allow-list of RFC 3986
         path / query / fragment characters. Anything outside that set
         — including a smuggled second-segment netloc like
         ``/foo/../@evil.example`` — fails closed.
      3. Return ``m.group(0)`` rather than the original ``value``, so
         the returned string flows from a Match object instead of the
         raw user input. This is the canonical CodeQL sanitiser
         pattern and matches the JS-side ``login.js:nextPath()`` fix.

    Defensive fallback constant ``_SAFE_NEXT_FALLBACK`` (``"/"``) is
    used for every reject path so the return values are either a
    Match-derived substring or a literal — never the raw input.
    """
    if not value:
        return _SAFE_NEXT_FALLBACK
    if not value.startswith("/"):
        return _SAFE_NEXT_FALLBACK
    if value.startswith("//") or value.startswith("/\\"):
        return _SAFE_NEXT_FALLBACK
    if "\\" in value:
        return _SAFE_NEXT_FALLBACK
    m = _SAFE_NEXT_PATH_RE.fullmatch(value)
    if m is None:
        return _SAFE_NEXT_FALLBACK
    return m.group()


# ----------------------------------------------------------------------------
# Route bodies — wired up in main.py.
# ----------------------------------------------------------------------------
def _sso_login_error_redirect(request: Request, provider, code: str, reason: str = "") -> RedirectResponse:
    """Redirect the browser back to ``/login`` with a readable SSO error instead
    of raising a bare 502 — which a fronting proxy (Cloudflare / NPM) masks into
    an opaque "Bad gateway" page with no clue what failed. The login page renders
    an i18n message keyed on ``sso_error`` plus the short, sanitised ``sso_detail``
    (the full reason is already in the ``[oidc]`` logs).

    ``code`` is a stable slug the login page maps to an i18n string
    (``provider_unreachable`` / ``provider_misconfigured`` / ``not_configured``).
    """
    safe_next = _safe_next(request.query_params.get("next"))
    # Sanitise the reason to a short printable token so nothing weird lands in
    # the query string or the rendered page (login.js renders via textContent).
    short = "".join(ch for ch in (reason or "") if ch.isprintable())[:160].strip()
    params = {"sso_error": code, "sso_provider": provider.label}
    if safe_next and safe_next != "/":
        params["next"] = safe_next
    if short:
        params["sso_detail"] = short
    return RedirectResponse(url=f"/login?{urlencode(params)}", status_code=302)


async def login(request: Request, provider_id: str = _providers.DEFAULT_PROVIDER_ID):
    """Start the OIDC flow. Generates state/nonce/PKCE, stashes them in a
    flow cookie, 302s to the IdP's authorization_endpoint.

    On a login-START failure (provider unreachable / misconfigured / not
    configured) we redirect back to ``/login`` with a readable message rather
    than returning a 502 the fronting proxy would mask — see
    :func:`_sso_login_error_redirect`.
    """
    provider = _providers.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Unknown OIDC provider {provider_id!r}")
    if not is_configured(provider.id):
        return _sso_login_error_redirect(request, provider, "not_configured")
    issuer = _pget(provider, "issuer_url")
    client_id = _pget(provider, "client_id")
    configured_redirect = (_pget(provider, "redirect_uri") or "").strip()
    redirect_uri = configured_redirect or _default_redirect_uri(request, provider)
    scopes = _pget(provider, "scopes") or provider.default_scopes

    # Diagnostic log — prints to docker service logs so we can see
    # EXACTLY what redirect_uri we're sending to the IdP and whether it
    # came from the DB override or the request-origin auto-compute.
    # Paste this into the IdP's Redirect URIs allowlist byte-for-byte
    # if they don't match.
    source = "DB override" if configured_redirect else "auto-computed from request origin"
    host_hdr = request.headers.get("host") or "(no host)"
    fwd_host = request.headers.get("x-forwarded-host") or "(none)"
    fwd_proto = request.headers.get("x-forwarded-proto") or "(none)"
    print(
        f"[oidc] /login provider={provider.id} redirect_uri={redirect_uri!r} source={source} "
        f"host={host_hdr!r} x-forwarded-host={fwd_host!r} x-forwarded-proto={fwd_proto!r}"
    )

    try:
        doc = await _fetch_discovery(issuer, _verify_tls(provider))
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except HTTPException as he:
        # Discovery returned a non-200 (already logged in _fetch_discovery).
        # Redirect back to /login with a readable reason instead of bubbling a
        # 502 the fronting proxy would turn into an opaque Bad-gateway page.
        return _sso_login_error_redirect(request, provider, "provider_unreachable", str(he.detail))
    except Exception as e: # noqa: BLE001
        # Network-level failure (connect timeout / DNS / TLS / connection
        # reset) reaching the issuer — log it so the operator sees WHY the
        # login failed, then redirect back to /login with the reason.
        print(f"[oidc] error discovery fetch to {issuer!r} failed for provider "
              f"{provider.id}: {type(e).__name__}: {e}")
        return _sso_login_error_redirect(request, provider, "provider_unreachable", f"{type(e).__name__}: {e}")

    auth_ep = doc.get("authorization_endpoint")
    if not auth_ep:
        print(f"[oidc] error discovery for provider {provider.id} has no "
              f"authorization_endpoint (issuer={issuer!r})")
        return _sso_login_error_redirect(request, provider, "provider_misconfigured",
                                         "discovery has no authorization_endpoint")
    # Log the resolved authorization endpoint we're about to 302 the browser
    # to. If the browser then shows the IdP's own "502 Bad gateway" page, this
    # is the exact URL to reproduce against — the redirect leaves OmniGrid so
    # a provider-side failure there can't otherwise appear in these logs.
    print(f"[oidc] /login provider={provider.id} authorization_endpoint={auth_ep!r}")

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    # `next` path is stashed server-side under the state token —
    # never round-trips through the cookie / browser. The callback
    # consumes it via `_flow_paths_consume(state)` after verifying
    # state matches the cookie. Keeps the post-login redirect target
    # off the request → response path that CodeQL's url-redirection
    # taint tracker walks. Defensive `_safe_next` validates the raw
    # `?next=` value before stashing.
    _flow_paths_remember(state, _safe_next(request.query_params.get("next")))

    flow = {
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "provider": provider.id,
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
        # `samesite` defaults to "lax" — the IdP redirect back carries the cookie
        path="/api/oidc/",  # scoped to the callback path — no other route needs it
    )
    return resp


async def callback(request: Request, provider_id: str = _providers.DEFAULT_PROVIDER_ID):
    """Complete the OIDC flow and mint an og_session cookie."""
    provider = _providers.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Unknown OIDC provider {provider_id!r}")
    if not is_configured(provider.id):
        raise HTTPException(status_code=503, detail=f"{provider.label} SSO is not configured")

    # Rate-limit per IP the same way /api/local-auth/login does. Stops a
    # runaway loop from hammering the token endpoint on misconfigured
    # deploys, and makes bruteforcing the state/nonce pair pointless.
    # noinspection PyProtectedMember
    ip = auth._client_ip(request)
    auth.rate_limit_check(ip)

    # Pre-built Set-Cookie header that expires the flow cookie on every
    # error path. Pre-fix only the success branch ran
    # `delete_cookie`; failure paths (state mismatch, token-exchange
    # 401, id_token validation error, missing email claim) left a
    # dangling 5-min cookie that could confuse a subsequent flow if the
    # operator clicked through Authentik again before the TTL expired.
    # Passing this via `HTTPException(headers=...)` makes FastAPI's
    # default exception handler emit the Set-Cookie alongside the 4xx
    # response, so every error path now clears the cookie just like the
    # success path does.
    _flow_clear_headers = {
        "Set-Cookie": f"{FLOW_COOKIE}=; Path=/api/oidc/; Max-Age=0; HttpOnly; SameSite=Lax"
                      + ("; Secure" if _is_https(request) else "")
    }

    flow_cookie = request.cookies.get(FLOW_COOKIE)
    if not flow_cookie:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400,
            detail="Missing OIDC flow cookie (did the flow time out?)",
            headers=_flow_clear_headers,
        )
    flow = _decode_flow(flow_cookie)
    if not flow:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Invalid OIDC flow cookie",
            headers=_flow_clear_headers,
        )

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Missing code/state in callback",
            headers=_flow_clear_headers,
        )
    if not hmac.compare_digest(state, flow["state"]):
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="OIDC state mismatch",
            headers=_flow_clear_headers,
        )
    # Defence-in-depth: the flow cookie records which provider started the
    # flow; it must match the callback route's provider so a cookie minted
    # for provider A can't be replayed against provider B's callback.
    if flow.get("provider", _providers.DEFAULT_PROVIDER_ID) != provider.id:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="OIDC provider mismatch",
            headers=_flow_clear_headers,
        )

    issuer = _pget(provider, "issuer_url")
    client_id = _pget(provider, "client_id")
    client_secret = _pget(provider, "client_secret")
    redirect_uri = (_pget(provider, "redirect_uri") or "").strip() or _default_redirect_uri(request, provider)
    verify_tls = _verify_tls(provider)

    doc = await _fetch_discovery(issuer, verify_tls)
    _token_ep_raw = doc.get("token_endpoint")
    _jwks_uri_raw = doc.get("jwks_uri")
    if not isinstance(_token_ep_raw, str) or not isinstance(_jwks_uri_raw, str):
        raise HTTPException(
            status_code=502,
            detail="OIDC discovery missing token_endpoint / jwks_uri",
            headers=_flow_clear_headers,
        )
    token_ep: str = _token_ep_raw
    jwks_uri: str = _jwks_uri_raw

    # Token exchange. Providers accept client credentials in either the
    # Authorization header or the body; we use the body for clarity.
    try:
        async with httpx.AsyncClient(timeout=_http_timeout_seconds(), verify=verify_tls) as client:
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
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        # Network-level failure reaching the token endpoint (connect timeout,
        # connection reset, TLS error) — log it so it shows in Admin → Logs
        # instead of surfacing only as a bare 500.
        auth.rate_limit_record_failure(ip)
        print(f"[oidc] error token exchange to {token_ep!r} failed for provider "
              f"{provider.id}: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"OIDC token exchange error: {type(e).__name__}: {e}",
            headers=_flow_clear_headers,
        )
    if token_resp.status_code != 200:
        auth.rate_limit_record_failure(ip)
        # Log the token-exchange failure — this is a server-side call OmniGrid
        # makes, so an upstream 502 / 5xx here belongs in Admin → Logs (5xx =
        # infra/error; 4xx = usually a config issue like a redirect_uri or
        # client_secret mismatch = warning).
        lvl = "error"
        print(f"[oidc] {lvl} token exchange HTTP {token_resp.status_code} for "
              f"provider {provider.id} at {token_ep!r} — body {token_resp.text[:200]!r}")
        raise HTTPException(
            status_code=401,
            detail=f"OIDC token exchange failed: HTTP {token_resp.status_code} — {token_resp.text[:300]}",
            headers=_flow_clear_headers,
        )
    tok = token_resp.json()
    id_token = tok.get("id_token")
    if not id_token:
        auth.rate_limit_record_failure(ip)
        print(f"[oidc] error token response for provider {provider.id} missing "
              f"id_token (keys={list(tok.keys())})")
        raise HTTPException(
            status_code=502, detail="OIDC token response missing id_token",
            headers=_flow_clear_headers,
        )

    # --- Validate id_token -------------------------------------------------
    # Per RFC 8414 / OIDC Core, the `iss` claim MUST EXACTLY match the
    # value the discovery doc advertises — byte-for-byte, trailing slash
    # included. Do NOT strip anything from it; whatever the IdP publishes
    # in `openid-configuration.issuer` is what the id_token will carry.
    # Fall back to the admin-typed URL only if the doc is non-compliant.
    _iss_raw = doc.get("issuer")
    expected_iss: str = _iss_raw if isinstance(_iss_raw, str) and _iss_raw else issuer
    try:
        claims = await _validate_id_token(
            id_token, issuer=issuer, jwks_uri=jwks_uri,
            expected_iss=expected_iss, verify_tls=verify_tls,
            client_id=client_id, expected_nonce=flow["nonce"],
        )
    except jwt.InvalidIssuerError:
        # Dig out the actual iss in the token so the operator can spot
        # trailing-slash / host mismatches without reaching for jwt.io.
        # route through the errors catalog so Apprise +
        # UI tone come from the structured code instead of raw PyJWT
        # text.
        try:
            actual = jwt.decode(id_token, options={"verify_signature": False}).get("iss", "?")
        except (jwt.PyJWTError, ValueError, TypeError):
            actual = "?"
        auth.rate_limit_record_failure(ip)
        print(f"[oidc] error id_token issuer mismatch for provider {provider.id} "
              f"— expected {expected_iss!r}, got {actual!r}")
        raise HTTPException(
            status_code=401,
            detail=(f"[{_err.AUTH_OIDC_ISSUER_INVALID}] "
                    f"{_err.message_for(_err.AUTH_OIDC_ISSUER_INVALID)} "
                    f"Expected {expected_iss!r}, got {actual!r}."),
            headers=_flow_clear_headers,
        )
    except jwt.PyJWTError as e:
        auth.rate_limit_record_failure(ip)
        print(f"[oidc] error id_token validation failed for provider "
              f"{provider.id}: {type(e).__name__}: {e}")
        # Pattern-match on PyJWT's exception class to pick the most
        # specific code; falls back to the generic "validation failed"
        # bucket. .
        e_type = type(e).__name__
        e_msg = str(e)
        if e_type == "InvalidSignatureError":
            code = _err.AUTH_OIDC_SIGNATURE_INVALID
        elif e_type == "InvalidAudienceError":
            code = _err.AUTH_OIDC_AUDIENCE_MISMATCH
        elif e_type in ("ExpiredSignatureError", "ImmatureSignatureError"):
            code = _err.AUTH_OIDC_TOKEN_EXPIRED
        elif "nonce mismatch" in e_msg.lower():
            code = _err.AUTH_OIDC_NONCE_MISMATCH
        else:
            code = _err.AUTH_OIDC_TOKEN_VALIDATION_FAILED
        raise HTTPException(
            status_code=401,
            detail=f"[{code}] {_err.message_for(code)} ({e_msg})",
            headers=_flow_clear_headers,
        )

    email = (claims.get("email") or "").strip().lower()
    if not email:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400,
            detail="id_token missing 'email' claim — ensure the 'email' scope is granted",
            headers=_flow_clear_headers,
        )
    username = (claims.get("preferred_username") or claims.get("nickname") or email).strip()

    # Clear the rate-limit bucket only on fully-successful validation.
    auth.rate_limit_clear(ip)

    # --- Provision user + mint session ------------------------------------
    # `auto_provision_oidc` decides admin/readonly from the id_token claims
    # per this provider's admin mode (group membership for Authentik, the
    # `role` claim for UnifiedSSO) and keys the user by auth_source=provider.id.
    with db_conn() as c:
        u = auth.auto_provision_oidc(c, provider, email, username, claims)
        if u.disabled:
            raise HTTPException(
                status_code=403, detail="Account disabled",
                headers=_flow_clear_headers,
            )
        auth.touch_last_login(c, u.id)
        cookie_value, expires_at = auth.create_session(
            c, u.id, ip, request.headers.get("user-agent"),
            auth_method="oidc",
        )
        # Audit-trail row — first-class forensic record of the
        # SSO sign-in. The Apprise `user_login` notification below
        # is a SEPARATE side-channel; the history row is the
        # canonical audit anchor. `oidc_login` op_type distinguishes
        # this from local-auth `user_login` rows so the History tab
        # can filter / report on SSO vs local separately.
        from logic.ops import write_admin_audit as _write_admin_audit
        _write_admin_audit(
            c, "oidc_login",
            target_kind="user", target_name=u.username, target_id=u.username,
            actor=u.username,
            message=f"Signed in via {provider.label} SSO from {ip}",
        )

    # Server-side path retrieval — `state` is the server-generated
    # nonce already verified against the cookie above (line ~446's
    # `hmac.compare_digest(state, flow["state"])`). The path comes
    # from `_flow_paths` (populated at login start, validated by
    # `_safe_next` at stash-time + again on consume) — NOT from the
    # cookie or any other request-derived source. Breaks the dataflow
    # chain CodeQL's `py/url-redirection` walks: previous rounds
    # stored the path INSIDE the flow cookie, which CodeQL still saw
    # as request-derived even after HMAC verification (the analyser
    # doesn't model HMAC as a sanitiser).
    next_path = _flow_paths_consume(state)
    resp = RedirectResponse(url=next_path, status_code=302)
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, auth.generate_csrf_token(), expires_at, request)
    # Clear the flow cookie — one-shot.
    resp.delete_cookie(FLOW_COOKIE, path="/api/oidc/")
    # Security event — opt-in via Admin → Notifications. Fire-and-forget;
    # never let a notify exception break the redirect.
    try:
        from logic.ops import notify as _notify
        await _notify(
            f"🔓 {u.username} signed in",
            f"via {provider.label} SSO from {ip}",
            event="user_login",
            actor_username=u.username,
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as _e: # noqa: BLE001
        print(f"[notify] user_login (oidc) failed: {_e}")
    return resp


async def _validate_id_token(
    id_token: str, *, issuer: str, jwks_uri: str,
    expected_iss: Optional[str] = None, verify_tls: bool = True,
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
    # Whitelist asymmetric signing algorithms . The unverified header is
    # attacker-controlled — feeding it straight into PyJWT's
    # ``algorithms=[alg]`` kwarg is fragile: modern PyJWT (>=2.0)
    # refuses ``none`` even when listed, but a regression or pinned
    # pre-2.0 install would silently accept an unsigned token. Reject
    # anything outside the OIDC-spec asymmetric set BEFORE the decode
    # call so the contract holds independent of PyJWT's defaults.
    # Symmetric algorithms (HS256/HS384/HS512) deliberately excluded —
    # OIDC providers MUST sign id_tokens with their JWKS-published
    # asymmetric key, never the operator-side client_secret.
    if alg not in _ALLOWED_ID_TOKEN_ALGORITHMS:
        raise jwt.InvalidTokenError(
            f"id_token uses disallowed alg {alg!r}; expected one of "
            f"{sorted(_ALLOWED_ID_TOKEN_ALGORITHMS)}"
        )

    jwks = await _fetch_jwks(issuer, jwks_uri, verify_tls)
    key = _find_key(jwks, kid)
    if key is None:
        # Key rotation — bypass cache once. Log the refresh so operators
        # can see in Admin → Logs that key rotation actually hit this
        # path; without the line the cache-bypass is invisible
        print(f"[oidc] kid={kid!r} not in cached jwks — bypassing cache to refresh")
        jwks = await _fetch_jwks(issuer, jwks_uri, verify_tls, force=True)
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
    """Select the JWKS key whose ``kid`` matches; ``None`` when ``kid`` is absent
    or unmatched (rejecting a missing ``kid`` avoids a rotation-downgrade attack
    where a suppressed header forces verification against ``keys[0]``)."""
    # Reject `kid is None`. Pre-fix this returned
    # `keys[0]` blindly, which during a multi-key JWKS rotation lets an
    # attacker who suppresses the `kid` header force verification
    # against whichever key happens to be first in the array — typically
    # the older key. Authentik always emits `kid`, but a future IdP swap
    # or a misconfigured one would silently downgrade key selection.
    # `kid` is OPTIONAL per RFC 7515, but for an OIDC id_token signed by
    # an IdP that publishes a JWKS, requiring it is the conservative
    # choice — every spec-compliant IdP we'd integrate with does emit it.
    if not kid:
        return None
    keys = jwks.get("keys") or []
    for k in keys:
        if k.get("kid") == kid:
            return k
    return None


# ----------------------------------------------------------------------------
# Helpers used by both routes
# ----------------------------------------------------------------------------
def _is_https(request: Request) -> bool:
    """True when the request arrived over HTTPS (honours ``X-Forwarded-Proto``
    from the reverse proxy)."""
    proto = request.headers.get("x-forwarded-proto", "").lower()
    return proto == "https" or request.url.scheme == "https"


def _callback_path(provider) -> str:
    """The callback route path for a provider. The legacy Authentik provider
    keeps the bare ``/api/oidc/callback``; every other provider is namespaced
    under ``/api/oidc/<id>/callback``."""
    if provider.id == _providers.DEFAULT_PROVIDER_ID:
        return "/api/oidc/callback"
    return f"/api/oidc/{provider.id}/callback"


def _default_redirect_uri(request: Request, provider) -> str:
    """Compute a provider's callback URL from the request origin. Only used
    when the admin hasn't pinned one in Settings — pinning is strongly
    recommended because IdPs require redirect URIs to be exact-match
    allowlisted."""
    scheme = "https" if _is_https(request) else "http"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{scheme}://{host}{_callback_path(provider)}"


def public_redirect_uri(request: Request, provider_id: str = _providers.DEFAULT_PROVIDER_ID) -> str:
    """What to display in the "Redirect URI" field of a provider's Settings
    panel when the admin hasn't pinned one. Computed from the request origin
    so the Copy button writes exactly what IdPs need in their allowlist.
    Falls back to the default provider's path for an unknown id."""
    provider = _providers.get(provider_id) or _providers.require(_providers.DEFAULT_PROVIDER_ID)
    return _default_redirect_uri(request, provider)


# ----------------------------------------------------------------------------
# RFC 7591 Dynamic Client Registration
# ----------------------------------------------------------------------------
async def register_client(provider_id: str, initial_access_token: str, request: Request) -> dict:
    """Register OmniGrid as a client with ``provider_id`` via RFC 7591 dynamic
    client registration, returning the issued ``client_id`` / ``client_secret``.

    The admin pastes an initial-access-token (issued by the IdP admin); OmniGrid
    reads the ``registration_endpoint`` from the provider's discovery doc and
    POSTs a registration request (client_name, this provider's redirect URI,
    authorization_code grant, code response type, client_secret_post auth, the
    provider's scopes). The route handler persists the returned credentials —
    this function only performs the exchange and never raises for a normal
    error path other than via ``HTTPException`` with an operator-readable
    detail. ``offline_access`` is intentionally not requested — OmniGrid mints
    its own session and never uses the OIDC refresh token.
    """
    provider = _providers.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Unknown OIDC provider {provider_id!r}")
    if not provider.supports_registration:
        raise HTTPException(
            status_code=400,
            detail=f"{provider.label} does not support dynamic client registration.",
        )
    issuer = (_pget(provider, "issuer_url") or "").strip()
    if not issuer:
        raise HTTPException(
            status_code=400,
            detail="Set the issuer URL and run Test connection before auto-registering.",
        )
    verify = _verify_tls(provider)
    doc = await _fetch_discovery(issuer, verify)
    reg_ep = doc.get("registration_endpoint")
    if not isinstance(reg_ep, str) or not reg_ep:
        raise HTTPException(
            status_code=502,
            detail=("The issuer's discovery document has no registration_endpoint — "
                    "dynamic client registration isn't enabled on this provider. "
                    "Register the client manually and paste the client_id / secret."),
        )
    redirect_uri = (_pget(provider, "redirect_uri") or "").strip() or _default_redirect_uri(request, provider)
    scopes = _pget(provider, "scopes") or provider.default_scopes
    body = {
        "client_name": "OmniGrid",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
        "scope": scopes,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    tok = (initial_access_token or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        async with httpx.AsyncClient(timeout=_http_timeout_seconds(), verify=verify) as client:
            r = await client.post(reg_ep, json=body, headers=headers)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        print(f"[oidc] error client registration POST to {reg_ep!r} failed for "
              f"provider {provider.id}: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Client registration error: {type(e).__name__}: {e}",
        )
    if r.status_code not in (200, 201):
        # Detect the common misconfiguration: the Issuer URL was set to the
        # registration endpoint (…/reg) instead of the base issuer. Providers
        # like node-oidc-provider serve discovery at ANY path and compute
        # endpoints relative to it, so an issuer of `…/oidc/reg` yields a
        # doubled `registration_endpoint = …/oidc/reg/reg` that 404s.
        hint = ""
        if r.status_code == 404 or reg_ep.rstrip("/").endswith("/reg/reg"):
            hint = (f" — the registration endpoint resolved to a doubled path ({reg_ep}). "
                    "The Issuer URL is likely set to the registration endpoint; use the "
                    "BASE issuer (e.g. https://auth.example.com/oidc), NOT the …/reg endpoint.")
        print(f"[oidc] error client registration HTTP {r.status_code} for provider "
              f"{provider.id} at {reg_ep!r} — body {r.text[:200]!r}{hint}")
        raise HTTPException(
            status_code=502,
            detail=f"Client registration failed: HTTP {r.status_code} — {r.text[:300]}{hint}",
        )
    try:
        data = r.json()
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=502, detail="Client registration returned a non-JSON body")
    client_id = data.get("client_id")
    if not client_id:
        raise HTTPException(status_code=502, detail="Registration response missing client_id")
    return {
        "client_id": str(client_id),
        "client_secret": str(data.get("client_secret") or ""),
        "registration_access_token": str(data.get("registration_access_token") or ""),
        "registration_client_uri": str(data.get("registration_client_uri") or ""),
    }
