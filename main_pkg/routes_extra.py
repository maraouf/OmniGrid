"""Continuation of `main_pkg.routes` — third chunk in the
main → main_pkg.routes → main_pkg.routes_extra star-import chain.

Loading order:
  1. main.py runs (defines `app`, helpers, models, early routes).
  2. main.py end → `from main_pkg.routes import *`.
  3. main_pkg.routes top → `from main import *` (pulls main's
     symbols). Body runs; routes register.
  4. main_pkg.routes end → `from main_pkg.routes_extra import *`.
  5. main_pkg.routes_extra top → `from main_pkg.routes import *`
     (transitively pulls main via routes' own star-import).
  6. main_pkg.routes_extra body runs; routes register.
  7. Chain unwinds. All three files share one `app`.

Cut placement (~70% through routes.py) keeps every cross-chunk
helper consumer in the kept portion — the symbols that move
here only reference earlier definitions, never each other.
"""
"""Continuation of `main` (routes module under `main_pkg/`) — extracted to keep main.py under the
line-count "uncomfortable to navigate" threshold. Re-exported via
`from main_routes import *` at the bottom of `main.py`, which pulls
every public symbol (including FastAPI routes registered through
`@app.<verb>(...)`) back into the main namespace.

Loading order:
  1. main.py runs top-to-bottom, defining `app`, every helper,
     and roughly the first half of the routes.
  2. main.py end: `from main_routes import *` triggers main_routes load.
  3. main_routes.py top: `from main import *` pulls EVERY symbol
     main has defined so far (`app`, helpers, Pydantic models,
     etc.) into main_routes's namespace so the route decorators
     below can reference them.
  4. main_routes.py body runs; routes register against the shared
     `app` instance.
  5. main_routes.py finishes; control returns to main.py's star-
     import which now has every main_routes symbol available.
"""
"""
OmniGrid — Portainer-native update dashboard.

Endpoints:
  GET  /api/items                     - All services + containers with status
  GET  /api/item/{raw_id}             - Single item detail
  POST /api/update/stack/{id}         - Update stack (Prune+PullImage)
  POST /api/update/container/{id}     - Recreate standalone container
  POST /api/restart/service/{id}      - Force restart a Swarm service
  GET  /api/ops   /  /api/ops/{id}    - Live operation status
  GET  /api/history                   - Persisted history
  GET  /api/ignores  /  POST  /  DELETE
  GET  /api/settings /  POST
  POST /api/notify-test
  GET  /api/healthz
  GET  /metrics                       - Prometheus scrape endpoint
"""
# Module-wide suppression for the recurring project-pattern lint noise that
# the operator validates and accepts: defensive broad-except guards (project
# convention is to catch + log + continue at API-boundary sites so a single
# broken provider can't 500 the whole route); cross-module `_protected_member`
# access (helpers like `_node_attr` / `_node_matches` / `_load_mappings` /
# `_PROVIDER_PREFIXES` are deliberately shared by main.py without a public
# alias because the indirection isn't worth a re-export); local `e` / `_events`
# / `_gather_mod` / `_stats_mod` shadow names inside `except` clauses and
# lazy-import blocks; explicit `arg=default` kwargs at call sites kept for
# readability of the intended value; missing docstrings on internal FastAPI
# route handlers whose function name + signature is self-describing; the
# `Member 'None' of 'Any | None'` chain reported on every `_admin: auth.User
# = Depends(auth.require_admin)` parameter (PyCharm cannot narrow through
# FastAPI's Depends() injection). Real bugs OUTSIDE these noise classes are
# fixed inline.
import asyncio
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any, Iterable, Optional, Set, cast

# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).
from dotenv import load_dotenv

from logic.env_keys import EnvKey, env_get  # noqa: E402

# Re-import parent's namespace so decorators below find `app`,
# helpers, Pydantic models, etc.
from main import *  # noqa: E402,F401,F403

# Re-import parent's namespace so decorators below find every
# symbol from main + main_pkg.routes.
from main_pkg.routes import *  # noqa: E402,F401,F403



def _request_rp_id(request: Request) -> str:
    """Derive the WebAuthn RP ID from the incoming request.

    RP ID is the hostname (no port, no scheme) the SPA hit, AS THE
    BROWSER SEES IT — has to be a registrable suffix of the page's
    actual origin or `navigator.credentials.create()` rejects with
    SecurityError. Behind a reverse proxy (NPM in OmniGrid's deploy)
    the upstream connection's URL has the internal hostname (typically
    ``localhost`` or the Docker stack name), which would mismatch the
    public domain the browser sees and break enrolment.

    Resolution order: ``X-Forwarded-Host`` header (what proxies set
    when they want the backend to know the original Host), then the
    ``Host`` header (NPM forwards this verbatim), then
    ``request.url.hostname`` as a last resort for direct (non-proxied)
    dev runs. Strip the ``:port`` suffix in every case — RP IDs are
    hostname-only.

    the WebAuthn register-finish path calls this
    twice (directly + via `_request_origin`); cache the resolved value
    on `request.state.rp_id` so the second call is a dict lookup.
    """
    cached = getattr(request.state, "rp_id", None)
    if isinstance(cached, str):
        return cached
    candidates = [
        request.headers.get("x-forwarded-host", ""),
        request.headers.get("host", ""),
        request.url.hostname or "",
    ]
    for raw in candidates:
        host = (raw or "").split(",")[0].strip().lower()
        if ":" in host:
            host = host.split(":", 1)[0]
        if host:
            try:
                request.state.rp_id = host
            except (AttributeError, RuntimeError):
                # `WebSocket` doesn't expose `state` like Request — the
                # cache is best-effort; just skip when unavailable.
                pass
            return host
    raise HTTPException(
        status_code=400,
        detail=_err.message_for(_err.AUTH_WEBAUTHN_RP_ID_UNRESOLVABLE),
    )


def _request_origin(request) -> str:
    """Full origin used for WebAuthn assertion verification AND for the
    WebSocket admin-route Origin gate.

    Accepts either a Starlette ``Request`` or a ``WebSocket``; both
    expose ``.headers`` and ``.url`` with the shape we need so the
    helper duck-types cleanly.

    Resolution order matches ``_request_rp_id`` — ``X-Forwarded-Host``
    (what the public-facing reverse proxy sets to convey the original
    Host), then the ``Host`` header, then ``request.url.netloc /
    .hostname`` as a final fallback. Some NPM setups rewrite the Host
    header to the internal upstream hostname while preserving the
    public hostname in X-Forwarded-Host — if origin disagrees with
    rp_id, the WebAuthn verifier rejects with "Unexpected client data
    origin" because the browser-signed clientDataJSON.origin (the
    public URL) doesn't match the server-computed expected_origin
    (the internal one). Honouring X-Forwarded-Host on this side keeps
    rp_id + origin in lock-step.

    Also trusts ``X-Forwarded-Proto`` so HTTPS termination at NPM is
    visible to the verifier.
    """
    proto = (request.headers.get("x-forwarded-proto", "")
             or request.url.scheme or "http").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        # reject bogus X-Forwarded-Proto values
        # (e.g. "ftp", "file") instead of silently flipping to https.
        # Falls back to the actual request scheme; logs once so a
        # mis-configured proxy is debuggable from Admin → Logs.
        bad = proto
        proto = (request.url.scheme or "http").lower()
        if proto not in ("http", "https"):
            proto = "http"
        print(
            f"[webauthn] rejecting X-Forwarded-Proto={bad!r} "
            f"(not http/https) — falling back to scheme={proto!r}"
        )
    host_candidates = [
        request.headers.get("x-forwarded-host", ""),
        request.headers.get("host", ""),
        request.url.netloc or "",
        request.url.hostname or "",
    ]
    host_header = ""
    for raw in host_candidates:
        cand = (raw or "").split(",")[0].strip()
        if cand:
            host_header = cand
            break
    return f"{proto}://{host_header}"


@app.post("/api/local-auth/login")
async def api_local_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Local-auth login: validate password + 2FA gate; mint session cookie on success."""
    ip = auth.client_ip(request)
    # check both the IP-only bucket AND the
    # (ip, username) bucket. The latter scopes lockout to the actual
    # user being typo'd at, so a corporate-NAT'd office isn't
    # collateral-damaged by one user's bad password.
    auth.rate_limit_check(ip, username)
    with db_conn() as c:
        u = auth.get_user_by_username(c, username)
        # split the failure cases for clearer operator-facing
        # error messages without disclosing username existence.
        # SECURITY: only specialise the message AFTER a successful
        # password verification; otherwise an attacker could enumerate
        # disabled accounts by probing for the "Account disabled"
        # response without knowing the password.
        password_ok = (
            u is not None
            and u.auth_source == "local"
            and auth.verify_password(password, _get_user_password_hash(c, u.id))
        )
        if not password_ok:
            auth.rate_limit_record_failure(ip, username)
            raise HTTPException(status_code=401, detail="Invalid credentials")
        # `password_ok` true implies `u is not None` (the `and u is not
        # None` short-circuit above), but the type-checker doesn't carry
        # that narrowing across the boolean expression. Explicit assert
        # so every `u.<field>` access below is well-typed.
        assert u is not None
        if u.disabled:
            # Password is verified correct; the user just can't log in.
            # Safe to disclose because we already proved the caller
            # holds the credentials. Use 403 so the SPA's login page
            # can branch on the status code if it ever wants per-case
            # styling. NOTE: we do NOT record a rate-limit failure
            # here — the credentials were CORRECT; the lockout exists
            # to slow down brute-force, not to punish a re-enable
            # attempt by a legitimate user.
            raise HTTPException(
                status_code=403,
                detail="Account is disabled. Contact your administrator.",
            )
        # ----------------------------------------------------------------
        # 2FA gate. Branches before any
        # session cookie is issued:
        # (a) user has TOTP enabled OR passkeys enrolled -> respond
        #     200 with step="totp_required" and methods=[...] so the
        #     SPA renders one of (or both) "Authenticator code" /
        #     "Use a passkey" inputs at the second-factor screen.
        # (b) policy requires 2FA for this role AND user has neither
        #     TOTP nor passkeys -> respond step="totp_setup_required"
        #     (forced TOTP enrolment; passkey-only enrolment-on-login
        #     isn't offered because it requires a roundtrip the
        #     legacy login form can't host).
        # (c) no 2FA, no requirement -> issue cookie (legacy path).
        # ----------------------------------------------------------------
        policy = _resolve_totp_policy()
        state = auth.get_user_totp_state(c, u.id)
        passkey_count = auth.count_user_credentials(c, u.id)
        # Master-toggle gates. When admin disables a method,
        # treat enrolled credentials of that type as if they don't
        # exist for login purposes — the method drops from `methods`
        # and is skipped in the has_2fa check. The user's enrolment
        # rows stay in the DB so flipping the toggle back on restores
        # the login path. If admin disables BOTH and the user has
        # nothing else, they fall through to single-factor (this is
        # the admin's explicit choice).
        totp_login_enabled = bool(state["enabled"]) and policy["totp_allowed"]
        passkey_login_enabled = (
            passkey_count > 0
            and policy["passkeys_allowed"]
            and webauthn_h.WEBAUTHN_AVAILABLE
        )
        has_2fa = totp_login_enabled or passkey_login_enabled
        # Lockout check happens BEFORE we mint a challenge so a locked
        # user gets a clear 423 rather than a stale challenge_id. Lockout
        # state is TOTP-only for now -- passkeys have their own per-IP
        # rate-limit on webauthn-finish failures. Skip when totp_allowed
        # is off (no point locking out a method we won't honour anyway).
        if totp_login_enabled and state["locked_until"]:
            if state["locked_until"] > int(time.time()):
                retry = state["locked_until"] - int(time.time())
                raise HTTPException(
                    status_code=423,
                    detail=(
                        "Account locked due to too many failed 2FA attempts. "
                        f"Try again in {max(1, retry // 60)} minute(s)."
                    ),
                    headers={"Retry-After": str(retry)},
                )
            # Lockout expired -- clear the state so the next failure
            # starts a fresh counter.
            auth.clear_totp_lockout(c, u.id)
        if has_2fa:
            methods: list[str] = []
            if totp_login_enabled:
                methods.append("totp")
            if passkey_login_enabled:
                methods.append("webauthn")
            cid, exp = _create_totp_challenge({
                "user_id": u.id,
                "kind": "totp_required",
                "ip": ip,
            })
            auth.rate_limit_clear(ip, username)
            return JSONResponse({
                "step": "totp_required",
                "challenge_id": cid,
                "expires_at": exp,
                "username": u.username,
                "methods": methods,
            })
        if policy["totp_allowed"] and _totp_required_for(u.role, policy):
            secret_plain = totp.generate_secret()
            uri = totp.provisioning_uri(secret_plain, u.username)
            cid, exp = _create_totp_challenge({
                "user_id": u.id,
                "kind": "totp_setup_required",
                "secret": secret_plain,
                "ip": ip,
            })
            auth.rate_limit_clear(ip, username)
            return JSONResponse({
                "step": "totp_setup_required",
                "challenge_id": cid,
                "expires_at": exp,
                "username": u.username,
                "secret": secret_plain,
                "provisioning_uri": uri,
            })
        # Legacy single-factor path.
        auth.rate_limit_clear(ip, username)
        auth.touch_last_login(c, u.id)
        cookie_value, expires_at = auth.create_session(
            c, u.id, ip, request.headers.get("user-agent"),
        )
        # Audit-trail row — first-class forensic record of the sign-in
        # (the Apprise notification above is a SEPARATE side-channel
        # opt-in; the history row is the canonical "who signed in
        # when" audit anchor).
        _ops_mod.write_admin_audit(
            c, "user_login",
            target_kind="user", target_name=u.username, target_id=u.username,
            actor=u.username,
            message=f"Signed in via local-auth from {ip}",
        )
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({"username": u.username, "role": u.role, "source": u.auth_source})
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    # Security event — opt-in via Admin → Notifications. Fire-and-
    # forget via the shared retry helper so a
    # transient Apprise blip doesn't drop the audit notification on
    # the floor.
    spawn_background_task(
        notify_with_retry(
            f"🔓 {u.username} signed in",
            f"via local from {ip}",
            event="user_login",
            actor_username=u.username,
            target_kind="user", target_id=u.username,
            metadata={"ip": ip, "method": "local"},
            label=f"user_login (local) {u.username!r}",
        ),
        label=f"user_login_notify {u.username!r}",
    )
    return resp


@app.post("/api/local-auth/totp")
async def api_local_login_totp(
    request: Request,
    challenge_id: str = Form(...),
    code: str = Form(...),
):
    """Step 2 of the multi-step login for users with TOTP enrolled.

    Verifies the 6-digit TOTP (or a backup code) against the user's
    stored secret, increments the per-user failure counter on miss,
    locks on threshold, and issues the og_session cookie on success.
    """
    ip = auth.client_ip(request)
    auth.rate_limit_check(ip)
    challenge = _peek_totp_challenge(challenge_id)
    if not challenge or challenge.get("kind") != "totp_required":
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    user_id = challenge["user_id"]
    policy = _resolve_totp_policy()
    # Master toggle. When admin disables TOTP, refuse to verify
    # codes from already-enrolled users — defence in depth alongside
    # the api_local_login `methods` filter that already drops 'totp'
    # from the login response. A stale client could still POST here.
    if not policy["totp_allowed"]:
        _consume_totp_challenge(challenge_id)
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=403,
            detail=_err.message_for(_err.AUTH_TOTP_DISABLED_BY_ADMIN),
        )
    with db_conn() as c:
        u = auth.get_user(c, user_id)
        if not u or u.disabled or u.auth_source != "local":
            _consume_totp_challenge(challenge_id)
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=400, detail="User not eligible.")
        state = auth.get_user_totp_state(c, user_id)
        if state["locked_until"] and state["locked_until"] > int(time.time()):
            retry = state["locked_until"] - int(time.time())
            raise HTTPException(
                status_code=423,
                detail=(
                    "Account locked due to too many failed 2FA attempts. "
                    f"Try again in {max(1, retry // 60)} minute(s)."
                ),
                headers={"Retry-After": str(retry)},
            )
        secret_ct = auth.get_user_totp_secret(c, user_id)
        if not secret_ct:
            _consume_totp_challenge(challenge_id)
            raise HTTPException(status_code=400, detail="TOTP not enrolled.")
        try:
            secret_plain = totp.decrypt_secret(secret_ct)
        except Exception as e:
            print(f"[totp] decrypt secret FAILED for user {u.username}: {e}")
            raise HTTPException(status_code=500, detail="TOTP decrypt failed.")
        verified = False
        used_backup = False
        if totp.verify_code(secret_plain, code):
            verified = True
        else:
            matched, new_blob = totp.consume_backup_code(
                state["backup_codes_json"], code,
            )
            if matched and new_blob is not None:
                verified = True
                used_backup = True
                auth.update_user_totp_backup_codes(c, user_id, new_blob)
        if not verified:
            n, locked = auth.record_totp_failure(
                c, user_id,
                policy["totp_lockout_max_failures"],
                policy["totp_lockout_minutes"] * 60,
            )
            auth.rate_limit_record_failure(ip)
            print(f"[totp] {u.username} verify FAILED ({n}/{policy['totp_lockout_max_failures']})")
            if locked:
                print(f"[totp] {u.username} locked out for {policy['totp_lockout_minutes']}m")
                raise HTTPException(
                    status_code=423,
                    detail=(
                        "Account locked due to too many failed 2FA attempts. "
                        f"Try again in {policy['totp_lockout_minutes']} minute(s)."
                    ),
                )
            raise HTTPException(status_code=401, detail="Invalid code.")
        # Success path -- consume the challenge, clear lockout, issue cookie.
        _consume_totp_challenge(challenge_id)
        auth.clear_totp_lockout(c, user_id)
        auth.rate_limit_clear(ip)
        auth.touch_last_login(c, user_id)
        cookie_value, expires_at = auth.create_session(
            c, user_id, ip, request.headers.get("user-agent"),
            auth_method="totp",
        )
        # Audit-trail row — same shape as the legacy single-factor
        # path. The Apprise notification below is a side-channel.
        _ops_mod.write_admin_audit(
            c, "user_login",
            target_kind="user", target_name=u.username, target_id=u.username,
            actor=u.username,
            message=f"Signed in via local-auth (2FA TOTP{' + backup code' if used_backup else ''}) from {ip}",
        )
    if used_backup:
        print(f"[totp] {u.username} used backup code")
    else:
        print(f"[totp] {u.username} verified successfully")
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({
        "username": u.username, "role": u.role, "source": u.auth_source,
    })
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    try:
        await notify(
            f"🔓 {u.username} signed in",
            f"via local (2FA) from {ip}",
            event="user_login",
            actor_username=u.username,
            target_kind="user", target_id=u.username,
            metadata={"ip": ip, "method": "local_totp"},
        )
    except Exception as _e:
        print(f"[notify] user_login (totp) dropped: {_e}")
    return resp


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/local-auth/totp-setup-confirm")
async def api_local_login_totp_setup_confirm(
    request: Request,
    challenge_id: str = Form(...),
    code: str = Form(...),
):
    """Step 2 of the multi-step login when policy is forcing enrolment.

    Verifies the freshly-typed 6-digit code against the secret we
    issued in step 1, persists the secret + backup codes, then issues
    the cookie. Returns the 10 plaintext backup codes (one-time reveal).
    """
    ip = auth.client_ip(request)
    auth.rate_limit_check(ip)
    challenge = _peek_totp_challenge(challenge_id)
    if not challenge or challenge.get("kind") != "totp_setup_required":
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    user_id = challenge["user_id"]
    secret_plain = challenge.get("secret") or ""
    if not totp.verify_code(secret_plain, code):
        auth.rate_limit_record_failure(ip)
        raise HTTPException(status_code=401, detail="Invalid code.")
    backup_plain = totp.generate_backup_codes()
    encrypted_secret = totp.encrypt_secret(secret_plain)
    encrypted_codes_json = totp.encrypt_backup_codes(backup_plain)
    with db_conn() as c:
        u = auth.get_user(c, user_id)
        if not u or u.disabled or u.auth_source != "local":
            _consume_totp_challenge(challenge_id)
            raise HTTPException(status_code=400, detail="User not eligible.")
        auth.set_user_totp_secret(
            c, user_id, encrypted_secret, encrypted_codes_json,
        )
        _consume_totp_challenge(challenge_id)
        auth.rate_limit_clear(ip)
        auth.touch_last_login(c, user_id)
        cookie_value, expires_at = auth.create_session(
            c, user_id, ip, request.headers.get("user-agent"),
            auth_method="totp",
        )
        _ops_mod.write_admin_audit(
            c, "user_login",
            target_kind="user", target_id=u.username,
            actor=u.username,
            events_dict={
                "method": "local_totp_setup",
                "auth_source": u.auth_source,
                "ip": ip,
            },
        )
    print(f"[totp] {u.username} enrolled (forced by policy)")
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({
        "username": u.username, "role": u.role, "source": u.auth_source,
        "backup_codes": backup_plain,
    })
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    try:
        await notify(
            f"🔓 {u.username} signed in",
            f"via local (2FA enrolled) from {ip}",
            event="user_login",
            actor_username=u.username,
            target_kind="user", target_id=u.username,
            metadata={"ip": ip, "method": "local_totp_setup"},
        )
    except Exception as _e:
        print(f"[notify] user_login (totp setup) dropped: {_e}")
    return resp


# ============================================================================
# Login passkey routes. Pair with the existing TOTP routes above —
# both consume the same challenge-id minted in api_local_login. The login
# flow's "second factor" pivots on which method the SPA POSTs back:
# /api/local-auth/totp for a 6-digit code, /api/local-auth/webauthn-* for
# a passkey assertion. CSRF is exempt because the caller doesn't have a
# session cookie yet (auth-optional path).
# ============================================================================
class WebauthnLoginStartIn(BaseModel):
    challenge_id: str


class WebauthnLoginFinishIn(BaseModel):
    challenge_id: str
    credential: dict  # raw PublicKeyCredential JSON from the SPA


@app.post("/api/local-auth/webauthn-start")
async def api_local_login_webauthn_start(
    body: WebauthnLoginStartIn,
    request: Request,
):
    """Step 2A of the multi-step login: hand the SPA a WebAuthn
    challenge to feed into ``navigator.credentials.get()``.

    Reads the user_id from the in-memory TOTP challenge (minted by
    api_local_login). Allows the user to switch between TOTP and
    passkey on the same screen without re-entering the password --
    the challenge_id is shared.

    Returns ``{options: <PublicKeyCredentialRequestOptions>, login_id}``.
    The SPA POSTs the assertion back via webauthn-finish with the
    same login_id.
    """
    if not webauthn_h.WEBAUTHN_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=_err.message_for(_err.AUTH_WEBAUTHN_LIBRARY_MISSING),
        )
    # Master toggle. Defence-in-depth — the SPA won't offer
    # the passkey method when this is off (login response omits
    # 'webauthn' from `methods`), but a stale client could still try.
    if not _resolve_totp_policy()["passkeys_allowed"]:
        raise HTTPException(
            status_code=403,
            detail=_err.message_for(_err.AUTH_PASSKEYS_DISABLED_BY_ADMIN),
        )
    ip = auth.client_ip(request)
    auth.rate_limit_check(ip)
    challenge = _peek_totp_challenge(body.challenge_id)
    if not challenge or challenge.get("kind") != "totp_required":
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    user_id = challenge["user_id"]
    with db_conn() as c:
        u = auth.get_user(c, user_id)
        if not u or u.disabled or u.auth_source != "local":
            raise HTTPException(status_code=400, detail="User not eligible.")
        creds = auth.list_user_credentials(c, user_id)
    if not creds:
        raise HTTPException(
            status_code=400,
            detail="No passkeys enrolled for this account.",
        )
    rp_id = _request_rp_id(request)
    # detect credentials registered under a different domain.
    # WebAuthn binds credentials to their RP ID; if the operator
    # migrated OmniGrid between domains, stored credentials are still
    # in the DB but the browser correctly refuses to offer them on the
    # new domain — falling through to the QR / hybrid flow with no
    # explanation. Compute the orphaned set so the SPA can surface a
    # clear "re-enrol from Profile" hint above the Passkey button.
    # Empty `rp_id` on a credential row means "registered before this
    # column landed" — treat as unknown rather than
    # mismatched so the legacy creds don't fire spurious banners.
    orphaned = []
    matching = []
    # Loop variable `cred`, NOT `c`. The convention in `main.py`
    # is `c` = sqlite connection (the outer `with db_conn() as c:`
    # has exited, but reusing the name in the loop body would shadow
    # that and add reader hazard). Renamed throughout the loop + the
    # allowCredentials list comprehension below.
    for cred in creds:
        cred_rp = (cred.get("rp_id") or "").strip().lower()
        if cred_rp and cred_rp != rp_id.lower():
            orphaned.append({
                "id": cred["id"],
                "friendly_name": cred.get("friendly_name") or "",
                "rp_id": cred_rp,
            })
        else:
            matching.append(cred)
    rp_id_mismatch = len(orphaned) > 0 and len(matching) == 0
    # Build the assertion options against ALL stored credentials. Even
    # when every credential is orphaned, we still send the options so
    # the browser tries — the spec-correct outcome is still QR-fallback,
    # but the SPA surfaces the banner explaining WHY based on the
    # `rp_id_mismatch` flag below. If at least one matching credential
    # exists, restrict allowCredentials to those so the picker doesn't
    # waste a click on a stale credential.
    _allow_set = matching if matching else creds
    options, raw_challenge = webauthn_h.make_authentication_options(
        rp_id=rp_id,
        allowed_credentials=[
            {
                "credential_id": cred["credential_id"],
                "transports": cred["transports"],
            }
            for cred in _allow_set
        ],
    )
    login_id, expires_at = _create_webauthn_login_challenge({
        "user_id": user_id,
        "challenge_bytes": raw_challenge,
        "rp_id": rp_id,
        "origin": _request_origin(request),
        "ip": ip,
    })
    # surface the per-credential transports being sent so the
    # operator can grep server logs to verify the assertion-options
    # payload includes 'internal' (without it, macOS Safari/Chrome
    # default to the QR/hybrid flow regardless of `hints`).
    _allow = (options.get("allowCredentials") or []) if isinstance(options, dict) else []
    _transports_summary = [
        {"id_prefix": (c.get("id") or "")[:8], "transports": c.get("transports")}
        for c in _allow
    ]
    print(
        f"[webauthn] {u.username} login-start (rp_id={rp_id}) "
        f"hints={options.get('hints') if isinstance(options, dict) else None} "
        f"allow={_transports_summary}"
    )
    if orphaned:
        print(
            f"[webauthn] {u.username} login-start RP-ID mismatch "
            f"current={rp_id!r} orphaned={[(o['friendly_name'], o['rp_id']) for o in orphaned]} "
            f"matching={len(matching)} "
        )
    return JSONResponse({
        "options": options,
        "login_id": login_id,
        "expires_at": expires_at,
        "username": u.username,
        # surface the RP-ID mismatch state so the SPA's login
        # form can render a clear hint instead of letting the browser
        # silently fall through to QR. Only fires when EVERY stored
        # credential's rp_id differs from the current rp_id (any
        # matching cred → operator can still authenticate normally,
        # no banner needed). `orphaned_credentials` lists the
        # friendly names + their original rp_ids for context.
        "rp_id_mismatch": rp_id_mismatch,
        "orphaned_credentials": orphaned,
        "current_rp_id": rp_id,
    })


@app.post("/api/local-auth/webauthn-finish")
async def api_local_login_webauthn_finish(
    body: WebauthnLoginFinishIn,
    request: Request,
):
    """Step 2B: verify the passkey assertion + mint the session cookie.

    Same success path as ``/api/local-auth/totp``: ``touch_last_login``,
    ``create_session``, ``set_session_cookie`` + ``set_csrf_cookie``,
    fire the user_login notification. Failures land in the per-IP
    rate-limit counter so a stolen credential_id can't be brute-forced.
    """
    if not webauthn_h.WEBAUTHN_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Passkey support is not available on this server.",
        )
    ip = auth.client_ip(request)
    auth.rate_limit_check(ip)
    challenge = _peek_webauthn_login_challenge(body.challenge_id)
    if not challenge:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    user_id = challenge["user_id"]
    expected_challenge: bytes = challenge["challenge_bytes"]
    expected_rp_id: str = challenge["rp_id"]
    expected_origin: str = challenge["origin"]
    cred_payload = body.credential or {}
    raw_id = cred_payload.get("rawId") or cred_payload.get("id") or ""
    if not raw_id or not isinstance(raw_id, str):
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400,
            detail="Malformed assertion payload.",
        )
    try:
        credential_id_bytes = webauthn_h.b64u_decode(raw_id)
    except Exception:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Malformed credential id.",
        )
    with db_conn() as c:
        u = auth.get_user(c, user_id)
        if not u or u.disabled or u.auth_source != "local":
            _consume_webauthn_login_challenge(body.challenge_id)
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=400, detail="User not eligible.")
        stored = auth.get_credential_by_credential_id(c, credential_id_bytes)
        if not stored or stored["user_id"] != user_id:
            _consume_webauthn_login_challenge(body.challenge_id)
            auth.rate_limit_record_failure(ip)
            raise HTTPException(
                status_code=401, detail="Unknown credential.",
            )
        try:
            verified = webauthn_h.verify_authentication(
                credential_json=cred_payload,
                expected_challenge=expected_challenge,
                expected_origin=expected_origin,
                expected_rp_id=expected_rp_id,
                public_key=stored["public_key"],
                current_sign_count=stored["sign_count"],
            )
        except Exception as e:
            _consume_webauthn_login_challenge(body.challenge_id)
            auth.rate_limit_record_failure(ip)
            print(f"[webauthn] {u.username} verify FAILED: {e}")
            raise HTTPException(
                status_code=401, detail="Passkey verification failed.",
            )
        # Success path -- consume both challenges, bump sign-count, issue cookie.
        _consume_webauthn_login_challenge(body.challenge_id)
        # Also drop the paired TOTP challenge so the user can't replay
        # it via the TOTP path. We don't know the challenge_id used for
        # webauthn-start (login_id is its own), but the TOTP one was
        # never consumed in webauthn-start -- prune by user_id.
        _prune_totp_challenges()
        for k, v in list(_totp_challenges.items()):
            if v.get("user_id") == user_id and v.get("kind") == "totp_required":
                _totp_challenges.pop(k, None)
        auth.update_credential_after_use(
            c, stored["id"], verified["new_sign_count"],
        )
        auth.rate_limit_clear(ip)
        auth.touch_last_login(c, user_id)
        cookie_value, expires_at = auth.create_session(
            c, user_id, ip, request.headers.get("user-agent"),
            auth_method="passkey",
        )
        # Audit-trail row — same shape as the legacy single-factor +
        # TOTP paths. The Apprise notification is a side-channel.
        _ops_mod.write_admin_audit(
            c, "user_login",
            target_kind="user", target_name=u.username, target_id=u.username,
            actor=u.username,
            message=f"Signed in via local-auth (2FA passkey/WebAuthn cred {stored['id']}) from {ip}",
        )
    print(f"[webauthn] {u.username} verified successfully (cred {stored['id']})")
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({
        "username": u.username, "role": u.role, "source": u.auth_source,
    })
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    try:
        await notify(
            f"🔓 {u.username} signed in",
            f"via local (passkey) from {ip}",
            event="user_login",
            actor_username=u.username,
            target_kind="user", target_id=u.username,
            metadata={"ip": ip, "method": "local_passkey"},
        )
    except Exception as _e:
        print(f"[notify] user_login (webauthn) dropped: {_e}")
    return resp


def _get_user_password_hash(conn, user_id: int):
    """Fetch password_hash directly — not exposed via the User dataclass."""
    r = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
    return r["password_hash"] if r else None


@app.post("/api/local-auth/change-password")
async def api_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    *,
    user: CurrentUser,
):
    """Let a logged-in local user rotate their own password.

    - Authentik users are directed to Authentik (no password stored here).
    - Invalidates every other session for this user; keeps the caller's.
    - Rate-limited via the shared login limiter so brute-forcing the current
      password from a compromised session is bounded.
    """
    if user.auth_source != "local":
        raise HTTPException(
            status_code=400,
            detail="Authentik users must change their password in Authentik.",
        )
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="New passwords do not match.")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be 8+ characters.")
    if new_password == current_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current one.")

    ip = auth.client_ip(request)
    auth.rate_limit_check(ip)

    with db_conn() as c:
        stored = _get_user_password_hash(c, user.id)
        if not auth.verify_password(current_password, stored):
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=401, detail="Current password is incorrect.")
        auth.rate_limit_clear(ip)
        # Preserve the caller's own session while invalidating others.
        current_token_id = None
        cookie = request.cookies.get(auth.COOKIE_NAME)
        if cookie:
            current_token_id = auth.parse_session_cookie(cookie)
        auth.change_password(c, user.id, new_password, keep_session_token=current_token_id)

    return {"status": "ok"}


@app.post("/api/local-auth/logout")
async def api_local_logout(request: Request):
    """Revoke the caller's session cookie + clear the browser cookie."""
    cookie = request.cookies.get(auth.COOKIE_NAME)
    actor = _actor_from(request)
    if cookie:
        token_id = auth.parse_session_cookie(cookie)
        if token_id:
            with db_conn() as c:
                auth.delete_session(c, token_id)
                # Audit row — first-class forensic record of the
                # self-logout. `session_revoke` covers admin-initiated
                # session kills; this op_type covers user-initiated
                # ones so both flow into the same audit surface.
                _ops_mod.write_admin_audit(
                    c, "user_logout",
                    target_kind="user", target_name=actor, target_id=actor,
                    actor=actor,
                    message="Signed out via local-auth logout",
                )
    resp = JSONResponse({"ok": True})
    auth.clear_session_cookies(resp, request)
    return resp


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/local-auth/bootstrap")
async def api_local_bootstrap(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """One-shot: only works while the users table is empty.

    Lets operators claim the first admin on a fresh install without having
    to set BOOTSTRAP_ADMIN_* env vars. Self-disables as soon as any user
    exists — every subsequent call returns 403.
    """
    ip = auth.client_ip(request)
    auth.rate_limit_check(ip)
    with db_conn() as c:
        if auth.count_users(c) > 0:
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=403, detail="Bootstrap already consumed")
        if not username or not password or len(password) < 8:
            raise HTTPException(status_code=400, detail="Username required; password must be 8+ chars")
        u = auth.create_user(c, username, None, password, "admin", "local")
        auth.touch_last_login(c, u.id)
        cookie_value, expires_at = auth.create_session(
            c, u.id, ip, request.headers.get("user-agent"),
            auth_method="bootstrap",
        )
        _ops_mod.write_admin_audit(
            c, "user_login",
            target_kind="user", target_id=u.username,
            actor=u.username,
            events_dict={
                "method": "bootstrap",
                "auth_source": "local",
                "ip": ip,
            },
        )
    csrf = auth.generate_csrf_token()
    resp = JSONResponse(
        {"ok": True, "username": u.username, "role": u.role},
        status_code=201,
    )
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    return resp


# noinspection PyTypeChecker,PyDictCreation,PyUnresolvedReferences
@app.get("/api/me")
async def api_me(request: Request):
    """Return the current identity if any. Auth-optional — returns
    {authenticated: false} instead of 401 so the SPA can decide whether
    to redirect to /login. For real users, includes the full profile
    (display_name, bio, avatar_url, timestamps) so the profile page can
    render from a single fetch.
    """
    user = getattr(request.state, "user", None)
    if not user:
        return {"authenticated": False}
    user = cast(auth.User, user)  # rebind with non-Optional type — PyCharm honors cast across the function body
    # API-token "users" have negative ids (see _resolve_user) — skip the
    # profile read for them, there's nothing in the users table.
    profile = None
    if user.id >= 0:
        with db_conn() as c:
            profile = auth.get_user_profile(c, user.id)
    out = {
        "authenticated": True,
        "username": user.username,
        "role": user.role,
        "source": user.auth_source,
        # Client-side runtime knobs — read once on init, applied to the
        # next poll iteration. Resolved per-request so an Admin → Config
        # save takes effect on the next /api/me round-trip without a
        # page reload. Add new client-tunables here rather than via a
        # separate endpoint.
        "client_config": {
            # Tunable is stored as integer seconds for operator-
            # friendly UI; multiply by 1000 here so the SPA's setTimeout
            # consumer keeps its existing ms-based contract. Renaming
            # the SPA field would touch every call site for no gain.
            "ops_poll_ms": tuning.tuning_int(Tunable.OPS_POLL_INTERVAL_SECONDS) * 1000,
            # SPA's loadHosts() reads this and uses it as the cap on
            # parallel /api/hosts/one/<id> calls during fan-out. Resolved
            # per /api/me round-trip so an Admin → Config save takes
            # effect on the next call.
            "hosts_parallel_fetch": tuning.tuning_int(Tunable.HOSTS_PARALLEL_FETCH),
            # Idle-time progressive fill cadence (seconds). When the
            # operator is on the Hosts view and stays at the top
            # without scrolling, a background ticker trickles
            # not-yet-loaded host rows through the shared refresh
            # queue at this cadence so by the time they scroll, the
            # data is already there. 0 disables (scroll-only lazy
            # load). Goes through the same `hosts_parallel_fetch`
            # cap so backend pressure stays bounded.
            "hosts_idle_fill_seconds": tuning.tuning_int(Tunable.HOSTS_IDLE_FILL_INTERVAL_SECONDS),
            # AI Assistant sidebar drawer width (px). SPA's
            # ai-sidebar-drawer reads this and applies via inline
            # style on the <aside> root. Mobile layout ignores it.
            "ai_sidebar_width_px": tuning.tuning_int(Tunable.AI_SIDEBAR_WIDTH_PX),
            # AI sidebar conversation-persist cadence (ms). Consumed by
            # the SPA's `_aiPersistInterval` setup — see static/js/app.js.
            "ai_conversation_persist_ms": tuning.tuning_int(
                Tunable.AI_CONVERSATION_PERSIST_INTERVAL_MS
            ),
            # AI conversation export — gates the "Export TXT" /
            # "Export JSON" buttons in the AI sidebar header.
            # 0 = hide buttons, 1 = show. Default 1.
            "ai_conversation_export_enabled": bool(tuning.tuning_int(Tunable.AI_CONVERSATION_EXPORT_ENABLED)),
            # SSE freshness-watchdog idle threshold. Stored as
            # seconds; SPA's `_sseIdleThresholdMs` consumer wants ms.
            "sse_idle_threshold_ms": tuning.tuning_int(Tunable.SSE_IDLE_THRESHOLD_SECONDS) * 1000,
            # pollOps SSE-up keep-alive cadence. Same ms-conversion
            # pattern as ops_poll_ms and sse_idle_threshold_ms.
            "pollops_sse_keepalive_ms": tuning.tuning_int(Tunable.POLLOPS_SSE_KEEPALIVE_SECONDS) * 1000,
            # SPA-side load-busy watchdog cap (ms). `_runWithBusy` and
            # the topbar `refresh()` / `loadHosts()` flow + the SSE-pill
            # refreshing flags (`cacheRefreshing` / `hubProbing` /
            # `statsRefreshing`) cap any individual "busy" indicator at
            # this many ms. Stored as seconds, multiplied here so the
            # SPA setTimeout call keeps its ms contract.
            "load_busy_max_ms": tuning.tuning_int(Tunable.LOAD_BUSY_MAX_SECONDS) * 1000,
            # stat-bar warn / crit cutovers. SPA's barLevel /
            # barColor helpers read these per-call so an Admin → Config
            # save lands on the next render. Stored as integer percent
            # (30..90 / 50..99).
            "stat_bar_warn_pct": tuning.tuning_int(Tunable.STAT_BAR_WARN_PCT),
            "stat_bar_crit_pct": tuning.tuning_int(Tunable.STAT_BAR_CRIT_PCT),
            # HTTP-probe TLS cert expiry warning threshold (days). SPA's
            # drawer card paints the expiry pill amber when remaining-
            # days < this; red when ≤ 0. Per-call read so an Admin →
            # Host stats save lands on the next drawer render.
            "http_probe_cert_warning_days": tuning.tuning_int(Tunable.HTTP_PROBE_CERT_WARNING_DAYS),
            # Notifications panel page size — SPA reads this as the
            # initial value of `notificationsLimit`. Operator-tunable
            # via Admin → Notifications. Range 5..200 enforced at
            # both write-time (TUNABLES bounds) and read-time
            # (`tuning_int` clamps).
            "notifications_page_size": tuning.tuning_int(Tunable.NOTIFICATION_PAGE_SIZE),
            # Notifications popup polling fallback cadence (seconds).
            # Consumed by the SPA's $watch on showNotificationsPopup —
            # only used when SSE is disconnected AND the popup is open.
            "notifications_poll_seconds": tuning.tuning_int(
                Tunable.NOTIFICATIONS_POLL_INTERVAL_SECONDS
            ),
            # Sampler tick cadence (used by the SNMP "warming up" banner
            # so the "~N min" hint reflects the operator's configured
            # interval rather than a stale literal). Stored as seconds;
            # the SPA renders minutes for display.
            "stats_sample_interval_seconds": tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS),
            # SNMP-specific sampler cadence. When > 0, the SNMP
            # sampler runs at this interval instead of inheriting the
            # global stats_sample_interval. SPA's `snmpWarmingUpText`
            # uses this when non-zero so the "~N min" hint matches the
            # SNMP-specific cadence on operators who run SNMP at a
            # different cadence than Beszel/NE.
            "snmp_sample_interval_seconds": tuning.tuning_int(Tunable.SNMP_SAMPLE_INTERVAL_SECONDS),
            # Global SNMP per-host walk concurrency. Surfaced so the
            # Admin → Hosts editor can render the per-host
            # walk_concurrency input's placeholder as the resolved
            # global value (instead of a hardcoded "1") — operator
            # immediately sees what value the row will use when blank
            # vs the override they're typing.
            "snmp_per_host_walk_concurrency": tuning.tuning_int(Tunable.SNMP_PER_HOST_WALK_CONCURRENCY),
            # Global SNMP wall-clock budget — surfaced so the per-host
            # `wall_clock_budget` input's placeholder can render the
            # resolved global default ("Inherited: 60") instead of a
            # hardcoded literal.
            "snmp_wall_clock_budget_seconds": tuning.tuning_int(Tunable.SNMP_WALL_CLOCK_BUDGET_SECONDS),
            # Global ping defaults. Used by the SPA's metricSource()
            # tooltip so "Ping probe (TCP :443)" / "Ping probe (ICMP)"
            # falls back cleanly when a host has no per-host
            # ping_port / ping_transport override. Mirrors the SNMP
            # global-default surface pattern above.
            "ping": {
                "default_port": tuning.tuning_int(Tunable.PING_DEFAULT_PORT),
                "use_icmp": get_setting_bool(Settings.PING_USE_ICMP),
            },
            # Per-host drift baseline metric roster — single source of
            # truth for the SPA's drift-chip rendering. Backend's
            # `logic/host_baseline.py:METRICS` is canonical; surfacing
            # the tuple here lets the SPA iterate the API contract
            # instead of hardcoding a parallel literal. Adding a new
            # metric to METRICS (e.g. `swap_pct`) now propagates to
            # the SPA on the next `/api/me` round-trip without a
            # paired SPA edit.
            "baseline_metrics": list(_host_baseline.METRICS),
            # AI integration master state — surfaced so the SPA's
            # Cmd-K palette can decide whether to render the "Ask AI"
            # synthetic row. SPA gates on
            # `me.client_config.ai.enabled === true` AND
            # `me.client_config.ai.active_provider` being non-empty.
            "ai": {
                "enabled": get_setting_bool(Settings.AI_ENABLED),
                "active_provider": (get_setting(Settings.AI_ACTIVE_PROVIDER) or "").strip().lower(),
                "max_tokens": tuning.tuning_int(Tunable.AI_MAX_TOKENS),
                # Canonical provider list — the SPA's `aiProviderNames`
                # reads from this so adding a fifth provider is a
                # one-line edit to `logic.ai.SUPPORTED_PROVIDERS` and
                # every consumer (provider grid, settings form, the
                # active-provider dropdown) picks it up automatically
                # without a parallel SPA literal to keep in sync.
                "provider_names": list(_ai_supported_providers()),
            },
            # Last-Test-success timestamps per provider (DB-backed,
            # cross-browser / cross-machine). Stamped at the END of every
            # successful test endpoint via `_stamp_test_success`. Surfaced
            # here so the SPA's `lastTestSuccessLabel(key)` helper can
            # render "Last connected: <relative time>" next to every
            # Test connection button. Missing keys = no successful test
            # ever recorded; the SPA's `x-show` on the label collapses
            # cleanly. epoch seconds.
            "last_test_success": {
                key: int(get_setting(last_test_success_key(key), "0") or "0") or None
                for key in (
                    "portainer", "oidc", "beszel", "pulse",
                    "webmin", "snmp", "ping", "asset_inventory",
                )
            },
            # Scheduler-tz state so the admin Schedules tab can badge
            # "TZ: <name> → falling back to UTC" when the operator typed
            # an invalid IANA name. ``configured`` = raw setting,
            # ``resolved`` = active TZ (None on blank or invalid),
            # ``fallback`` = True only when configured was non-empty
            # but ZoneInfo rejected it.
            "scheduler_tz": schedules.scheduler_tz_state(),
            # per-provider chip colours. Hex string per provider,
            # falls back to the SPA's built-in default when the operator
            # hasn't customised. Read once on /api/me and applied to the
            # provider chip via inline `:style` (--chip-bg/-br/-fg).
            "provider_colors": {
                "beszel": get_setting(Settings.PROVIDER_COLOR_BESZEL) or "",
                "pulse": get_setting(Settings.PROVIDER_COLOR_PULSE) or "",
                "node_exporter": get_setting(Settings.PROVIDER_COLOR_NODE_EXPORTER) or "",
                "webmin": get_setting(Settings.PROVIDER_COLOR_WEBMIN) or "",
                "ping": get_setting(Settings.PROVIDER_COLOR_PING) or "",
            },
            # Canonical SNMP vendor key set — single source of truth at
            # ``logic.snmp._VALID_VENDOR_KEYS``. Surfaced so the Admin →
            # Hosts editor renders one checkbox per vendor without the
            # SPA hardcoding the list (was duplicated at three sites).
            # Adding a vendor in `_VENDOR_SIGNATURES` automatically
            # surfaces a checkbox here on the next /api/me round-trip.
            "snmp_vendor_keys": _snmp_vendor_keys_sorted(),
        },
    }
    # Surface the SESSION_SECRET-auto-generated state to admins.
    # When SESSION_SECRET isn't set in the env, logic/auth.py generates an
    # ephemeral one at boot — every container restart invalidates every
    # session. Today the only signal is a one-line print at boot, buried
    # in logs. Exposing this boolean lets the SPA render a dismissible
    # warning banner so operators know their sessions die on every redeploy.
    # Boolean only (no message string) — i18n surface lives in en.json.
    # Always included so the SPA can also clear a stale "dismissed" flag
    # once SESSION_SECRET is finally set in the env.
    out["session_secret_auto"] = (auth.auto_secret_warning() is not None)
    # bootstrap admin env vars still set in `.env` AFTER the
    # users table has been seeded. The bootstrap path is then a harmless
    # no-op on every restart, but two operational risks remain: (a) wiping
    # the DB and restarting would silently re-seed an admin from the env
    # values (surprise), (b) the password is sitting plaintext in `.env`.
    # Surfacing this boolean lets the SPA show a dismissible banner so
    # the operator clears the env vars before the next deploy.
    if BOOTSTRAP_ADMIN_USER and BOOTSTRAP_ADMIN_PASSWORD:
        with db_conn() as _bc:
            _user_n = auth.count_users(_bc)
        out["bootstrap_env_still_set"] = (_user_n > 0)
    else:
        out["bootstrap_env_still_set"] = False
    if profile:
        out.update({
            "id": profile["id"],
            "email": profile.get("email") or "",
            "display_name": profile.get("display_name") or "",
            "bio": profile.get("bio") or "",
            "created_at": profile.get("created_at"),
            "last_login_at": profile.get("last_login_at"),
            "avatar_url": f"/api/avatars/{profile['avatar_path']}" if profile.get("avatar_path") else None,
            # Per-user UI prefs. JSON dict — currently carries
            # `headerWeatherEnabled` / `headerClockEnabled` so toggling
            # them on desktop survives the trip to iPhone (or any other
            # browser) for the same login. Empty `{}` for users who've
            # never set anything; SPA falls back to its own per-toggle
            # defaults in that case.
            "ui_prefs": profile.get("ui_prefs") or {},
        })
        # Per-user notification opt-in map. Two-layer scoping:
        # the admin gate is shared via ``notify_events_admin`` so the
        # SPA can grey out toggles for events admin has globally
        # disabled; ``notify_events`` is the user's own resolved map
        # (defaults to admin state until the user opts out).
        admin_map = {
            name: get_setting_bool(
                notify_event_key(name), _NOTIFY_EVENT_DEFAULTS.get(name, True),
            )
            for name in _NOTIFY_EVENT_NAMES
        }
        with db_conn() as _c:
            user_prefs = auth.get_user_notify_prefs(_c, profile["id"])
        # Per-medium roster — every medium with a global enable toggle
        # AND a NOTIFY_MEDIUMS sender registered. Surfaced so the SPA
        # can render one Profile→Notifications column per available
        # medium without a separate /api/notify-mediums round-trip.
        from logic.ops import NOTIFY_MEDIUMS as _OPS_MEDIUMS
        from logic.ops import is_medium_enabled as _ops_medium_enabled
        notify_mediums = [
            {"name": m, "enabled": bool(_ops_medium_enabled(m))}
            for m in _OPS_MEDIUMS.keys()
        ]
        # Resolved per-event map: now `{event: bool | {medium: bool}}`
        # to mirror the per-medium granularity introduced for Profile→
        # Notifications. Three resolution shapes per event:
        # - User has stored a per-medium dict → return the dict (the
        #   SPA renders one checkbox per medium, defaults missing
        #   keys to True client-side).
        # - User has stored a bare bool (legacy, OR they opted out
        #   across every medium via the SPA's Disable-all bulk
        #   button) → return the bool.
        # - User has no stored value → fall back to the admin gate
        #   (the legacy "default to admin state" contract). Returned
        #   as a bare bool so the SPA renders the admin state across
        #   every medium column uniformly.
        resolved: dict = {}
        for name in _NOTIFY_EVENT_NAMES:
            if name in user_prefs:
                resolved[name] = user_prefs[name]
            else:
                resolved[name] = admin_map[name]
        out["notify_events"] = resolved
        out["notify_events_admin"] = admin_map
        out["notify_mediums"] = notify_mediums
        # Telegram link state — `null` when no Telegram user_id maps
        # to this username, otherwise the int user_id. The Profile
        # partial reads this to render either the "Generate link
        # code" button OR the "Linked as ..." chip + Unlink button.
        try:
            from logic import telegram_listener as _tg_listener
            _tg_mappings = _tg_listener.load_mappings()
            _tg_link_id: Optional[int] = None
            _tg_linked_at_ms: int = 0
            for _tg_id, _entry in _tg_mappings.items():
                if not isinstance(_entry, dict):
                    continue
                if _entry.get("username") == user.username:
                    try:
                        _tg_link_id = int(_tg_id)
                    except (TypeError, ValueError):
                        continue
                    _tg_linked_at_ms = int(_entry.get("linked_at_ms") or 0)
                    break
            out["telegram_link"] = (
                {
                    "telegram_user_id": _tg_link_id,
                    "linked_at_ms": _tg_linked_at_ms,
                }
                if _tg_link_id is not None else None
            )
        except Exception as _e:
            print(f"[me] telegram_link lookup failed: {_e}")
            out["telegram_link"] = None
        # TOTP / 2FA summary. Surfaced on /api/me so the SPA can
        # render the Profile section + the "Required by policy" banner
        # without a follow-up round-trip on every page load. Detailed
        # backup-codes payload still ships separately via /api/me/totp.
        _totp_policy = _resolve_totp_policy()
        with db_conn() as _c2:
            _totp_state = auth.get_user_totp_state(_c2, profile["id"])
            _passkey_count = auth.count_user_credentials(_c2, profile["id"])
        out["totp"] = {
            "enabled": bool(_totp_state["enabled"]),
            "allowed": bool(_totp_policy["totp_allowed"]),
            "required": (
                user.auth_source == "local"
                and _totp_required_for(user.role, _totp_policy)
            ),
        }
        # Passkeys. The SPA uses ``count`` as a quick hint
        # (e.g. show "+ Add passkey" when 0; show the list inline when
        # >0) without the full /api/me/webauthn round-trip. ``supported``
        # is the server-side capability flag (False when the webauthn
        # library is missing).
        out["passkeys"] = {
            "count": int(_passkey_count),
            "supported": (
                user.auth_source == "local"
                and webauthn_h.WEBAUTHN_AVAILABLE
            ),
            # Admin master toggle. When false, the SPA hides /
            # disables the "Add a passkey" button. Existing enrolments
            # remain visible + login-eligible until each user revokes.
            "allowed": bool(_totp_policy["passkeys_allowed"]),
        }
    return out


class UiPrefsIn(BaseModel):
    """Partial-update payload for PATCH /api/me/ui-prefs.

    Free-form dict — keys are SPA-defined (e.g. headerWeatherEnabled).
    Send `null` for a key to delete it from the stored prefs (so the
    SPA falls back to its default).
    """
    prefs: dict


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.patch("/api/me/ui-prefs")
async def api_me_ui_prefs(body: UiPrefsIn, request: Request):
    """Merge `body.prefs` into the calling user's `ui_prefs`.

    Auth required (cookie or token). API-token "users" (negative ids)
    can't store prefs — return 400. Returns the merged prefs so the
    SPA can confirm what's persisted.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Authentication required")
    user = cast(auth.User, user)  # rebind with non-Optional type — PyCharm honors cast across the function body
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot store UI prefs")
    with db_conn() as c:
        merged = auth.update_ui_prefs(c, user.id, body.prefs)
    return {"ui_prefs": merged}


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/me/telegram-link-code")
async def api_me_telegram_link_code(request: Request):
    """Mint a one-time, 15-minute, single-use code the user pastes into
    Telegram's `/link <code>` to bind their Telegram user_id to their
    OmniGrid account.

    Code is 6 digits (zero-padded) for easy typing on mobile. Stored in
    `users.ui_prefs.telegram_link_code` + `_expires_ms`. Calling this
    endpoint again before expiry replaces the previous code with a
    fresh one (operator-visible "Regenerate" semantics).

    Auth required — cookie or token. API tokens (negative ids) cannot
    link a Telegram account (no `ui_prefs` to read).
    """
    import secrets as _secrets
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Authentication required")
    user = cast(auth.User, user)  # rebind with non-Optional type — PyCharm honors cast across the function body
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot link Telegram accounts")
    # 6-digit numeric — easy to type on mobile, ~1M-entry space is
    # plenty when codes expire in 15 minutes and are single-use.
    code = f"{_secrets.randbelow(1_000_000):06d}"
    expires_ms = int(time.time() * 1000) + 15 * 60 * 1000  # +15 minutes
    with db_conn() as c:
        merged = auth.update_ui_prefs(c, user.id, {
            "telegram_link_code": code,
            "telegram_link_code_expires_ms": expires_ms,
        })
    return {
        "code": code,
        "expires_ms": expires_ms,
        "ui_prefs": merged,
    }


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.delete("/api/me/telegram-link")
async def api_me_telegram_unlink(request: Request):
    """Remove the calling user's Telegram mapping (operator-side
    counterpart to `/unlink` issued from Telegram). Walks the
    `telegram_user_mappings` JSON and drops every entry mapping any
    Telegram user_id to this OmniGrid username.

    Auth required.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Authentication required")
    user = cast(auth.User, user)  # rebind with non-Optional type — PyCharm honors cast across the function body
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage Telegram links")
    from logic import telegram_listener as _tg_listener
    mappings = _tg_listener.load_mappings()
    target_username = user.username
    removed: list[str] = []
    for tg_id, entry in list(mappings.items()):
        if isinstance(entry, dict) and entry.get("username") == target_username:
            mappings.pop(tg_id, None)
            removed.append(tg_id)
    if removed:
        _tg_listener.save_mappings(mappings)
    return {"removed": removed}


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/me/ui-prefs/beacon")
async def api_me_ui_prefs_beacon(body: UiPrefsIn, request: Request):
    """Beacon-friendly variant of PATCH /api/me/ui-prefs.

    `navigator.sendBeacon` only supports POST, can't set custom
    headers (so CSRF tokens via header don't work), and the request
    is fire-and-forget on the page-unload path. This endpoint accepts
    the same body shape but is registered as POST and is added to
    the CSRF exemption set in the auth middleware so unload-time
    chat-conversation saves land cleanly.

    Same auth gate as the PATCH variant — cookie session must be
    valid; API tokens can't write prefs. Same merge semantics.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Authentication required")
    user = cast(auth.User, user)  # rebind with non-Optional type — PyCharm honors cast across the function body
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot store UI prefs")
    with db_conn() as c:
        merged = auth.update_ui_prefs(c, user.id, body.prefs)
    return {"ui_prefs": merged}


@app.patch("/api/me/notify-prefs")
async def api_me_notify_prefs(
    request: Request,
    user: CurrentUser,
):
    """Per-user opt-in/out for the per-event notification preferences.

    Layered ON TOP of the admin-side ``notify_event_*`` gates: a
    notification fires only when (admin enabled) AND (user opted-in,
    or hasn't expressed a pref → defaults to admin state). Refuses to
    set a pref to True (or any per-medium bool=True) for an event the
    admin has globally disabled — the model only narrows DOWN.

    Payload shapes (free-form JSON dict — Pydantic validation is
    bypassed because the per-medium dict shape is operator-extensible
    via ``NOTIFY_MEDIUMS`` and a rigid model would require a deploy
    every time a medium lands):

      - ``{"event": true|false}`` — legacy bare-bool; sets the event
        across every globally-enabled medium.
      - ``{"event": {"app": true, "apprise": false}}`` — per-medium
        routing. Missing medium keys default to True (medium added
        after the user's last save still fires by default; explicit
        opt-out is the only way to silence a medium).
      - Mixed shapes per call are fine — some events as bare bool,
        others as per-medium dicts.

    Unknown event names are rejected (400) so a SPA-side typo doesn't
    silently land a malformed pref.

    API-token "users" (negative ids) can't store prefs.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot store notify prefs")
    try:
        payload = await request.json()
    except (ValueError, TypeError):
        raise HTTPException(400, "request body must be JSON")
    if not isinstance(payload, dict):
        raise HTTPException(400, "request body must be a JSON object")
    # Admin gate snapshot — refuse opt-IN for admin-disabled events.
    admin_map = {
        name: get_setting_bool(
            notify_event_key(name), _NOTIFY_EVENT_DEFAULTS.get(name, True),
        )
        for name in _NOTIFY_EVENT_NAMES
    }
    # Validate every event + value-shape BEFORE writing so a partial
    # save can't leave the user's prefs in a half-applied state.
    valid_event_names = set(_NOTIFY_EVENT_NAMES)
    cleaned: dict = {}
    for ev_name, value in payload.items():
        if ev_name not in valid_event_names:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown event '{ev_name}'.",
            )
        if value is None:
            # Skip — same as "leave unchanged", per the legacy contract.
            continue
        if isinstance(value, bool):
            if value is True and admin_map.get(ev_name) is False:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Event '{ev_name}' is disabled by admin; "
                        f"cannot enable per-user."
                    ),
                )
            cleaned[ev_name] = value
        elif isinstance(value, dict):
            per_medium: dict = {}
            for med_name, med_val in value.items():
                if not isinstance(med_val, bool):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Per-medium value for '{ev_name}.{med_name}' "
                            f"must be a boolean."
                        ),
                    )
                if med_val is True and admin_map.get(ev_name) is False:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Event '{ev_name}' is disabled by admin; "
                            f"cannot enable per-user (any medium)."
                        ),
                    )
                per_medium[str(med_name)] = bool(med_val)
            if per_medium:
                cleaned[ev_name] = per_medium
            else:
                # Empty per-medium dict is treated as "no explicit
                # choice" and dropped from the merge below — equivalent
                # to clearing the event's pref. Log it explicitly so an
                # operator investigating "why did my notify pref not
                # save?" has a breadcrumb in the persistent log without
                # having to instrument the SPA. The actor's username is
                # included so the log line is grep-friendly per user.
                print(
                    f"[notify] empty per-medium dict for "
                    f"'{user.username}'.'{ev_name}' — treated as "
                    f"'no explicit choice' (event pref unchanged)"
                )
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Value for '{ev_name}' must be a boolean OR an "
                    f"object mapping medium → boolean."
                ),
            )
    # Read-modify-write so unspecified events keep their stored value.
    with db_conn() as c:
        current = auth.get_user_notify_prefs(c, user.id)
        merged = dict(current)
        for ev_name, value in cleaned.items():
            merged[ev_name] = value
        auth.set_user_notify_prefs(c, user.id, merged)
    # Per-medium roster echoed back so the SPA can re-render the grid
    # without a separate /api/me round-trip.
    from logic.ops import NOTIFY_MEDIUMS as _OPS_MEDIUMS
    from logic.ops import is_medium_enabled as _ops_medium_enabled
    notify_mediums = [
        {"name": m, "enabled": bool(_ops_medium_enabled(m))}
        for m in _OPS_MEDIUMS.keys()
    ]
    # Resolved map mirrors api_get_me's shape exactly so the SPA can
    # drop the response straight into state.
    resolved: dict = {}
    for name in _NOTIFY_EVENT_NAMES:
        if name in merged:
            resolved[name] = merged[name]
        else:
            resolved[name] = admin_map[name]
    return {
        "notify_events": resolved,
        "notify_events_admin": admin_map,
        "notify_mediums": notify_mediums,
    }


class ProfileIn(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    email: Optional[str] = None


@app.patch("/api/me/profile")
async def api_update_profile(
    p: ProfileIn,
    user: CurrentUser,
):
    """Update the caller's own display_name / bio / email. Authentik users
    CAN edit these locally — those values don't round-trip to Authentik,
    they're OmniGrid's own overlay for display purposes.
    """
    # Keep the fields bounded so someone can't store a MB of biography.
    if p.display_name is not None and len(p.display_name) > 80:
        raise HTTPException(status_code=400, detail="display_name must be 80 chars or less")
    if p.bio is not None and len(p.bio) > 500:
        raise HTTPException(status_code=400, detail="bio must be 500 chars or less")
    if p.email is not None and p.email and len(p.email) > 200:
        raise HTTPException(status_code=400, detail="email must be 200 chars or less")
    with db_conn() as c:
        auth.update_user_profile(
            c, user.id,
            display_name=p.display_name,
            bio=p.bio,
            email=p.email,
        )
    return {"ok": True}


# Avatars live on the data volume next to the SQLite DB — persists across
# container restarts and redeploys. Keep the path out of user control:
# filename is derived from user id + content-type extension only.
_AVATAR_DIR = os.path.join(os.path.dirname(DB_PATH), "avatars")
os.makedirs(_AVATAR_DIR, exist_ok=True)
_AVATAR_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
    "image/gif": "gif", "image/webp": "webp",
}
_AVATAR_MAX_BYTES = 1_000_000  # 1 MB — avatars are small, reject uploads above


@app.post("/api/me/avatar")
async def api_upload_avatar(
    request: Request,
    user: CurrentUser,
):
    """Accept a multipart image upload and store it under /app/data/avatars/.

    Validates content-type against an allowlist, caps at 1 MB, and writes
    a filename of the form `u<id>.<ext>` so the same user always overwrites
    their previous avatar (no stale files left around).
    """
    form = await request.form()
    file_field = form.get("file")
    # Starlette's `form.get` returns `UploadFile | str | None`. Narrow
    # to UploadFile via duck-typing on `.read` so the type-checker
    # accepts `.content_type` / `.read()` access below.
    if file_field is None or isinstance(file_field, str) or not hasattr(file_field, "read"):
        raise HTTPException(status_code=400, detail="Field 'file' missing")
    file = file_field  # type: ignore[assignment]  # narrowed to UploadFile via the isinstance + hasattr guard above
    ct = (file.content_type or "").lower()
    ext = _AVATAR_EXT.get(ct)
    if not ext:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image type. Allowed: PNG / JPEG / GIF / WEBP.",
        )
    data = await file.read()
    if len(data) > _AVATAR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 1 MB)")
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty upload")
    # Clean up any existing avatar at a different extension.
    with db_conn() as c:
        old = auth.get_user_profile(c, user.id)
    if old and old.get("avatar_path"):
        old_full = os.path.join(_AVATAR_DIR, old["avatar_path"])
        if os.path.exists(old_full) and old["avatar_path"] != f"u{user.id}.{ext}":
            try:
                os.remove(old_full)
            except OSError:
                pass
    fname = f"u{user.id}.{ext}"
    with open(os.path.join(_AVATAR_DIR, fname), "wb") as f:
        f.write(data)
    with db_conn() as c:
        auth.set_user_avatar_path(c, user.id, fname)
    return {"ok": True, "avatar_url": f"/api/avatars/{fname}"}


_AVATAR_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
# Strict canonical-form regex for avatar filenames the upload path
# emits (`u<int_id>.<ext>` where ext is one of the allowlist values
# in `_AVATAR_EXT.values()`). Used by `_avatar_path_from_fname` to
# parse the URL-segment into primitives (int + allowlisted string)
# so the joined path has NO operator-controlled string taint flowing
# into it — CodeQL's path-injection tracker is happier with primitive
# rebuilding than with a regex-+-realpath sanitizer chain.
_AVATAR_FNAME_CANONICAL = re.compile(r"^u(?P<uid>\d+)\.(?P<ext>png|jpg|jpeg|gif|webp)$")


def _avatar_path_from_fname(fname: str) -> Optional[str]:
    """Parse a strict canonical avatar URL segment (`u<id>.<ext>`)
    and rebuild the on-disk path from PRIMITIVES — int user_id plus
    an allowlisted extension string. Returns None when the input
    doesn't match the canonical shape.

    This is the CodeQL-friendly sanitizer for avatar serving: the
    returned path is built from `_AVATAR_DIR` (constant) + an int
    converted via `int()` (CodeQL drops the string-taint label on
    type conversion) + a string drawn from a closed allowlist (the
    second regex group can ONLY be one of the literal alternation
    branches). Any non-canonical input — including all operator-
    typed escapes, separators, ``..``, NUL bytes, etc. — fails the
    regex up-front and returns None.
    """
    if not isinstance(fname, str) or not fname:
        return None
    m = _AVATAR_FNAME_CANONICAL.fullmatch(fname)
    if not m:
        return None
    # int() conversion is the canonical taint-stripper for numeric
    # operator input — CodeQL recognises it as a barrier.
    try:
        uid = int(m.group("uid"))
    except (TypeError, ValueError):
        return None
    if uid <= 0:
        return None
    ext = m.group("ext")
    # Defence-in-depth: re-validate against the closed allowlist
    # even though the regex above already enforces it. CodeQL sees
    # `ext` as a regex group result; a `value in CONSTANT_SET` check
    # is one of its sanitiser patterns.
    if ext not in _AVATAR_EXT.values():
        return None
    return os.path.join(_AVATAR_DIR, f"u{uid}.{ext}")


def _safe_avatar_path(name: str) -> Optional[str]:
    """Resolve `<_AVATAR_DIR>/<name>` and confirm the result stays
    within `_AVATAR_DIR`. Returns the realpath on success or None
    when the input fails the strict shape regex OR the resolved path
    escapes the root (symlink / corrupt DB row).

    Four-layer defence-in-depth so CodeQL's taint tracker recognises
    every barrier (the prior `startswith(root + os.sep)` check was
    correct but not in CodeQL's sanitizer list, and CodeQL kept
    flagging downstream filesystem reads as ``py/path-injection``):

    1. **Type + emptiness guard** — bail on anything that isn't a
       non-empty string before touching any path API.
    2. **Strict allowlist regex** ``^[A-Za-z0-9._-]+$`` — no
       slashes, no backslashes, no leading dots that could form
       ``..``, no NUL bytes, no path separators of any flavour.
    3. **`os.path.basename`** — CodeQL recognises this as a
       canonical path-component sanitizer. The regex above already
       guarantees the input has no separators so the basename is a
       no-op on well-formed values, but the explicit call is what
       convinces the taint tracker the value is path-safe.
    4. **`os.path.commonpath` confinement** — re-canonicalises the
       joined path via `realpath` and confirms the joined result
       shares the avatar root as its common prefix. CodeQL
       recognises ``commonpath == root`` as an explicit barrier
       (versus the older ``startswith(root + os.sep)`` shape, which
       is correct semantically but isn't in the sanitizer list).
    """
    if not isinstance(name, str) or not name:
        return None
    # Layer 2 — strict regex.
    if not _AVATAR_SAFE_NAME.fullmatch(name):
        return None
    # Reject standalone `.` / `..` / leading dots (regex above
    # allows dots in the middle, e.g. `u5.png`, but `..` and `.`
    # would also match — extra explicit guard).
    if name in (".", "..") or name.startswith(".."):
        return None
    # Layer 3 — basename strip. No-op on regex-valid inputs but
    # registered as a sanitizer barrier in CodeQL's path-injection
    # query.
    safe_name = os.path.basename(name)
    if safe_name != name or not safe_name:
        return None
    root = os.path.realpath(_AVATAR_DIR)
    candidate = os.path.realpath(os.path.join(root, safe_name))
    # Layer 4 — commonpath confinement (recognised barrier).
    try:
        common = os.path.commonpath([root, candidate])
    except ValueError:
        # Different drives / mount points (Windows) / mixed separators
        # → can't share a common path → reject.
        return None
    if common != root:
        return None
    return candidate


@app.delete("/api/me/avatar")
async def api_clear_avatar(user: CurrentUser):
    """Clear the caller's avatar (deletes the file on disk)."""
    with db_conn() as c:
        p = auth.get_user_profile(c, user.id)
    if p and p.get("avatar_path"):
        # `avatar_path` originates as a user-uploaded basename; even
        # though the upload path stores only `u<id>.<ext>`, route
        # through the realpath-guarded resolver so a corrupt DB row
        # can't trick this delete into removing a file outside
        # `_AVATAR_DIR`.
        full = _safe_avatar_path(p["avatar_path"])
        if full and os.path.exists(full):
            try:
                os.remove(full)
            except OSError:
                pass
    with db_conn() as c:
        auth.set_user_avatar_path(c, user.id, None)
    return {"ok": True}


@app.get("/api/avatars/{fname}")
async def api_serve_avatar(fname: str, _user: CurrentUser):
    """Serve an uploaded avatar. Authed — avatars are user data, shouldn't
    be browsable anonymously.

    Path-traversal-guarded via `_avatar_path_from_fname` which parses
    the URL segment into PRIMITIVES (int user_id + allowlisted ext
    drawn from a closed regex-alternation set) and rebuilds the
    on-disk path from those — no operator-controlled string flows
    into the path-construction expression. Any non-canonical fname
    (separators, `..`, NUL bytes, escape sequences, anything outside
    the strict `u<int>.<ext>` shape) fails the regex up-front and
    returns 404.
    """
    full = _avatar_path_from_fname(fname)
    if not full or not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Not found")
    # Derive content-type from the parsed extension. We re-parse here
    # rather than threading the ext through `_avatar_path_from_fname`
    # so the function stays single-return-typed (path-or-None);
    # parsing twice is cheap and keeps the API surface narrow.
    m = _AVATAR_FNAME_CANONICAL.fullmatch(fname)
    ext = m.group("ext") if m else "octet-stream"
    ct = next((k for k, v in _AVATAR_EXT.items() if v == ext), "application/octet-stream")
    return FileResponse(full, media_type=ct)


# ============================================================================
# Profile -> Two-factor authentication (TOTP) —.
# ============================================================================
class TotpEnrollConfirmIn(BaseModel):
    secret: str
    code: str


class TotpDisableIn(BaseModel):
    password: str


def _totp_authentik_guard(user: auth.User) -> None:
    if user.auth_source == "authentik":
        raise HTTPException(
            status_code=400,
            detail="Authentik users manage 2FA in their IdP.",
        )


def _totp_required_for_user(user: auth.User) -> bool:
    """Convenience wrapper around _totp_required_for() given a User.

    Honours the global role-based policy AND the per-user
    `totp_force_required` admin override. Either one is enough
    to require 2FA for this user. Authentik users always return False
    here — their auth_source short-circuits TOTP at the call sites.
    """
    if getattr(user, "auth_source", "local") != "local":
        return False
    if getattr(user, "totp_force_required", False):
        return True
    return _totp_required_for(user.role)


@app.get("/api/me/totp")
async def api_me_totp_status(user: CurrentUser):
    """Return the caller's 2FA status + decrypted backup codes.

    Backup codes are returned in plaintext (with a ``used_at`` flag per
    code) so the Profile page can render them under a hide/unhide
    eye toggle. Authentik users get a short-circuited reply that the
    SPA renders as "managed by IdP". API tokens (negative id) get 400.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    policy = _resolve_totp_policy()
    if user.auth_source == "authentik":
        return {
            "auth_source": user.auth_source,
            "allowed": False,
            "enabled": False,
            "required": False,
            "backup_codes": [],
            "policy": policy,
        }
    with db_conn() as c:
        state = auth.get_user_totp_state(c, user.id)
    codes = totp.decrypt_backup_codes(state["backup_codes_json"])
    return {
        "auth_source": user.auth_source,
        "allowed": bool(policy["totp_allowed"]),
        "enabled": bool(state["enabled"]),
        "required": _totp_required_for_user(user),
        "backup_codes": codes,
        "policy": policy,
    }


@app.post("/api/me/totp/enroll-start")
async def api_me_totp_enroll_start(user: CurrentUser):
    """Generate a fresh secret + provisioning_uri for the caller.

    The secret is NOT persisted at this stage -- the SPA echoes it back
    via /api/me/totp/enroll-confirm so the user proves they captured
    it correctly before we lock it in.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    _totp_authentik_guard(user)
    policy = _resolve_totp_policy()
    if not policy["totp_allowed"]:
        raise HTTPException(
            403, "Two-factor authentication is disabled by admin policy.",
        )
    secret_plain = totp.generate_secret()
    uri = totp.provisioning_uri(secret_plain, user.username)
    print(f"[totp] {user.username} enroll-start (secret prepared, awaiting confirm)")
    return {
        "secret": secret_plain,
        "provisioning_uri": uri,
        "username": user.username,
        "issuer": "OmniGrid",
    }


@app.post("/api/me/totp/enroll-confirm")
async def api_me_totp_enroll_confirm(
    body: TotpEnrollConfirmIn,
    user: CurrentUser,
):
    """Persist the secret + generate backup codes after a successful
    verification.

    Returns the 10 plaintext backup codes ONCE in this response. The
    Profile page also keeps them recoverable via /api/me/totp afterwards
    (encrypted at rest with the same Fernet key).
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    _totp_authentik_guard(user)
    policy = _resolve_totp_policy()
    if not policy["totp_allowed"]:
        raise HTTPException(
            403, "Two-factor authentication is disabled by admin policy.",
        )
    if not body.secret or len(body.secret) < 16:
        raise HTTPException(400, "Missing or malformed secret.")
    if not totp.verify_code(body.secret, body.code):
        raise HTTPException(401, "Invalid verification code.")
    backup_plain = totp.generate_backup_codes()
    encrypted_secret = totp.encrypt_secret(body.secret)
    encrypted_codes_json = totp.encrypt_backup_codes(backup_plain)
    with db_conn() as c:
        auth.set_user_totp_secret(
            c, user.id, encrypted_secret, encrypted_codes_json,
        )
        # Audit — user self-service TOTP enrolment is a security-sensitive
        # state change that admin-side ops can't see otherwise.
        try:
            _ops_mod.write_admin_audit(
                c, "totp_self_enroll",
                target_kind="auth", target_name=user.username, target_id=str(user.id),
                actor=user.username,
                message=f"TOTP enrolled by user {user.username}",
            )
        except Exception as e:
            print(f"[totp] self-enroll audit-row write failed: {e}")
    print(f"[totp] {user.username} enrolled")
    return {
        "ok": True,
        "backup_codes": backup_plain,
    }


@app.post("/api/me/totp/regenerate-codes")
async def api_me_totp_regenerate_codes(
    user: CurrentUser,
):
    """Replace the backup codes with a fresh batch of 10. Existing
    codes are discarded (used + unused alike). One-time reveal of the
    new plaintext list."""
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    _totp_authentik_guard(user)
    with db_conn() as c:
        state = auth.get_user_totp_state(c, user.id)
        if not state["enabled"]:
            raise HTTPException(400, "Two-factor authentication is not enabled.")
        backup_plain = totp.generate_backup_codes()
        encrypted = totp.encrypt_backup_codes(backup_plain)
        auth.update_user_totp_backup_codes(c, user.id, encrypted)
        try:
            _ops_mod.write_admin_audit(
                c, "totp_self_regenerate_codes",
                target_kind="auth", target_name=user.username, target_id=str(user.id),
                actor=user.username,
                message=f"TOTP backup codes regenerated by user {user.username}",
            )
        except Exception as e:
            print(f"[totp] self-regenerate audit-row write failed: {e}")
    print(f"[totp] {user.username} regenerated backup codes")
    return {"ok": True, "backup_codes": backup_plain}


@app.post("/api/me/totp/disable")
async def api_me_totp_disable(
    body: TotpDisableIn,
    user: CurrentUser,
):
    """Self-disable 2FA after re-confirming the password.

    Refused when the admin policy currently requires TOTP for the
    user's role -- the operator must lift the policy first OR an
    admin must override. Authentik users 400.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    _totp_authentik_guard(user)
    # 2FA is satisfied if EITHER TOTP OR a passkey is enrolled. So
    # a user with a passkey can self-disable TOTP even when policy
    # requires 2FA. Block ONLY when removing TOTP would leave the user
    # with no 2FA at all under a required-2FA policy.
    if _totp_required_for_user(user):
        with db_conn() as c:
            passkeys = auth.count_user_credentials(c, user.id)
        if passkeys == 0:
            raise HTTPException(
                403,
                "Admin policy requires 2FA for your role; "
                "enrol a passkey first or ask an admin to lift the policy.",
            )
    with db_conn() as c:
        stored = _get_user_password_hash(c, user.id)
        if not auth.verify_password(body.password, stored):
            raise HTTPException(401, "Current password is incorrect.")
        auth.clear_user_totp(c, user.id)
        try:
            _ops_mod.write_admin_audit(
                c, "totp_self_disable",
                target_kind="auth", target_name=user.username, target_id=str(user.id),
                actor=user.username,
                message=f"TOTP self-disabled by user {user.username}",
            )
        except Exception as e:
            print(f"[totp] self-disable audit-row write failed: {e}")
    print(f"[totp] {user.username} disabled")
    return {"ok": True}


# ============================================================================
# Profile -> WebAuthn / passkey management. Cookie-authed; CSRF
# enforced globally by the middleware. Authentik users 400 (their IdP
# manages MFA). API-token "users" (negative ids) 400.
# ============================================================================
class WebauthnRegisterStartIn(BaseModel):
    """Empty body -- the route reads username + user_id from the
    session. Kept as a model for future fields (e.g. preferred
    transports filter)."""
    pass


class WebauthnRegisterFinishIn(BaseModel):
    credential: dict
    friendly_name: Optional[str] = None


def _webauthn_self_guard(user: auth.User) -> None:
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage passkeys")
    if user.auth_source == "authentik":
        raise HTTPException(
            status_code=400,
            detail="Authentik users manage 2FA in their IdP.",
        )
    if not webauthn_h.WEBAUTHN_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=_err.message_for(_err.AUTH_WEBAUTHN_LIBRARY_MISSING),
        )


class WebauthnClientErrorIn(BaseModel):
    """Body for /api/me/webauthn/client-error — the SPA POSTs this when
    `navigator.credentials.create()` or `.get()` rejects with a
    DOMException so the failure reason lands in Admin → Logs.
    Fields are all best-effort strings; capped server-side to keep a
    misbehaving client from spamming the buffer.
    """
    phase: Optional[str] = None  # "register" | "login"
    error_name: Optional[str] = None  # DOMException.name
    error_message: Optional[str] = None
    rp_id: Optional[str] = None
    origin: Optional[str] = None


@app.post("/api/me/webauthn/client-error")
async def api_me_webauthn_client_error(
    body: WebauthnClientErrorIn,
    request: Request,
    user: CurrentUser,
):
    """Surface a client-side WebAuthn ceremony failure into the server
    log buffer. Pure logging — no DB write, no state change. Caps each
    field at 200 chars so a flooding client can't spam the ring."""

    def _trim(s: Optional[str]) -> str:
        s = (s or "").strip()
        return s[:200]

    phase = _trim(body.phase) or "?"
    err_name = _trim(body.error_name) or "?"
    err_msg = _trim(body.error_message)
    rp_id = _trim(body.rp_id) or _request_rp_id(request)
    origin = _trim(body.origin) or _request_origin(request)
    server_origin = _request_origin(request)
    server_rp_id = _request_rp_id(request)
    msg = (
        f"[webauthn] CLIENT ERROR — user={user.username} phase={phase} "
        f"error_name={err_name}"
    )
    if err_msg:
        msg += f" error_message={err_msg!r}"
    msg += (
        f" client_rp_id={rp_id} client_origin={origin} "
        f"server_rp_id={server_rp_id} server_origin={server_origin}"
    )
    print(msg)
    return {"ok": True}


@app.get("/api/me/webauthn")
async def api_me_webauthn_list(
    request: Request,
    user: CurrentUser,
):
    """Return every passkey enrolled for the caller.

    Each row is shaped ``{id, friendly_name, transports, created_at,
    last_used_at, sign_count, credential_id, rp_id}`` -- credential_id is
    base64url for display purposes only (stable identifier for the
    revoke button). public_key never leaves the server.

    ``rp_id`` lets the SPA flag credentials registered under a
    different domain (orphaned passkeys that the browser will refuse
    to offer at login). Profile → Security renders an inline badge
    when ``pk.rp_id !== current_rp_id``.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage passkeys")
    if user.auth_source == "authentik":
        return {"auth_source": user.auth_source, "supported": False, "credentials": []}
    if not webauthn_h.WEBAUTHN_AVAILABLE:
        return {
            "auth_source": user.auth_source,
            "supported": False,
            "credentials": [],
            "error": "webauthn library not installed on the server.",
        }
    with db_conn() as c:
        rows = auth.list_user_credentials(c, user.id)
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "credential_id": webauthn_h.b64u_encode(r["credential_id"]),
            "friendly_name": r["friendly_name"],
            "transports": r["transports"],
            "created_at": r["created_at"],
            "last_used_at": r["last_used_at"],
            "sign_count": r["sign_count"],
            "rp_id": r.get("rp_id", "") or "",
        })
    return {
        "auth_source": user.auth_source,
        "supported": True,
        "credentials": out,
        # current effective rp_id so the SPA can compare each
        # credential's rp_id against the live page's domain WITHOUT
        # the SPA having to re-derive it (the SPA's `location.hostname`
        # would skip X-Forwarded-Host edge cases that `_request_rp_id`
        # handles).
        "current_rp_id": _request_rp_id(request),
    }


@app.post("/api/me/webauthn/register-start")
async def api_me_webauthn_register_start(
    request: Request,
    user: CurrentUser,
):
    """Hand the SPA ``PublicKeyCredentialCreationOptions``.

    The challenge is stashed in-memory keyed by user_id (5-min TTL).
    The SPA echoes back the authenticator response via register-finish
    -- if the user starts a second enrolment without finishing the
    first, the challenge is overwritten (last-wins; safe -- challenges
    are per-user and not consumable across users).
    """
    _webauthn_self_guard(user)
    # Admin master toggle. Only register-start is gated — list /
    # revoke / login still work for already-enrolled keys, mirroring
    # the totp_allowed shape (admin can flip enrolment off without
    # breaking active logins).
    if not _resolve_totp_policy()["passkeys_allowed"]:
        raise HTTPException(
            status_code=403,
            detail=_err.message_for(_err.AUTH_PASSKEYS_DISABLED_BY_ADMIN),
        )
    rp_id = _request_rp_id(request)
    rp_name = "OmniGrid"
    with db_conn() as c:
        creds = auth.list_user_credentials(c, user.id)
    existing_ids = [c["credential_id"] for c in creds]
    # WebAuthn user-handle: 1..64 bytes, opaque to the RP. Use the
    # numeric user id as a left-padded 4-byte blob -- stable per user,
    # never leaks PII.
    user_handle = f"omnigrid-user-{user.id}".encode()
    options, raw_challenge = webauthn_h.make_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=user_handle,
        username=user.username,
        display_name=user.username,
        existing_credential_ids=existing_ids,
    )
    expires_at = _set_webauthn_register_challenge(user.id, {
        "challenge_bytes": raw_challenge,
        "rp_id": rp_id,
        "origin": _request_origin(request),
    })
    print(
        f"[webauthn] {user.username} register-start "
        f"(rp_id={rp_id}, origin={_request_origin(request)})"
    )
    return {
        "options": options,
        "expires_at": expires_at,
        "rp_id": rp_id,
    }


@app.post("/api/me/webauthn/register-finish")
async def api_me_webauthn_register_finish(
    body: WebauthnRegisterFinishIn,
    _request: Request,
    user: CurrentUser,
):
    """Verify the attestation + persist the new credential row.

    Friendly name validation: 0-64 visible chars; empty -> default
    "Passkey N" where N = (existing count + 1) so the operator gets
    a sensible label even when the SPA forgot to prompt.
    """
    _webauthn_self_guard(user)
    state = _consume_webauthn_register_challenge(user.id)
    if not state:
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    cred_payload = body.credential or {}
    if not isinstance(cred_payload, dict):
        raise HTTPException(
            status_code=400, detail="Malformed credential payload.",
        )
    try:
        result = webauthn_h.verify_registration(
            credential_json=cred_payload,
            expected_challenge=state["challenge_bytes"],
            expected_origin=state["origin"],
            expected_rp_id=state["rp_id"],
        )
    except Exception as e:
        print(f"[webauthn] {user.username} register verify FAILED: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Could not verify passkey: {e}",
        )
    try:
        friendly = webauthn_h.validate_friendly_name(body.friendly_name or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with db_conn() as c:
        existing = auth.list_user_credentials(c, user.id)
        if not friendly:
            friendly = f"Passkey {len(existing) + 1}"
        # Duplicate check (UNIQUE on credential_id catches it too --
        # mapped to 409 here for the friendlier shape).
        for r in existing:
            if r["credential_id"] == result["credential_id"]:
                raise HTTPException(
                    status_code=409,
                    detail="This passkey is already enrolled.",
                )
        try:
            row_id = auth.add_user_credential(
                c,
                user_id=user.id,
                credential_id=result["credential_id"],
                public_key=result["public_key"],
                sign_count=result["sign_count"],
                transports=result["transports"],
                friendly_name=friendly,
                # stamp the rp_id this credential was registered
                # under so login can detect "credential registered under
                # a different domain" later. ``state["rp_id"]`` came
                # from `_request_rp_id(request)` at register-start
                # time, so it tracks the effective hostname the user
                # was on when they enrolled.
                rp_id=state.get("rp_id", "") or "",
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="This passkey is already enrolled.",
            )
        try:
            _ops_mod.write_admin_audit(
                c, "passkey_self_register",
                target_kind="auth", target_name=user.username, target_id=str(row_id),
                actor=user.username,
                message=(f"passkey {friendly!r} registered by user {user.username} "
                         f"(rp_id={state.get('rp_id') or '?'})"),
            )
        except Exception as e:
            print(f"[webauthn] self-register audit-row write failed: {e}")
    print(f"[webauthn] {user.username} enrolled passkey "
          f"id={row_id} name={friendly!r}")
    return {
        "ok": True,
        "id": row_id,
        "friendly_name": friendly,
    }


@app.delete("/api/me/webauthn/{credential_row_id}")
async def api_me_webauthn_delete(
    credential_row_id: int,
    user: CurrentUser,
):
    """Revoke ONE passkey owned by the caller.

    The DB delete is gated on ``(user_id, id)`` so passing another
    user's credential id 404s instead of revoking it.
    """
    _webauthn_self_guard(user)
    with db_conn() as c:
        ok = auth.delete_user_credential(c, user.id, credential_row_id)
        if ok:
            try:
                _ops_mod.write_admin_audit(
                    c, "passkey_self_delete",
                    target_kind="auth", target_name=user.username,
                    target_id=str(credential_row_id),
                    actor=user.username,
                    message=f"passkey id={credential_row_id} revoked by user {user.username}",
                )
            except Exception as e:
                print(f"[webauthn] self-delete audit-row write failed: {e}")
    if not ok:
        raise HTTPException(status_code=404, detail="Passkey not found.")
    print(f"[webauthn] {user.username} revoked passkey id={credential_row_id}")
    return {"ok": True}


# ============================================================================
# Admin: user / session / API-token management (step 5).
# ============================================================================
class UserCreate(BaseModel):
    username: str
    role: str  # "admin" | "readonly"
    auth_source: str = "local"  # "local" | "authentik"
    password: Optional[str] = None  # required when auth_source == "local"
    email: Optional[str] = None


class UserPatch(BaseModel):
    role: Optional[str] = None
    disabled: Optional[bool] = None


class PasswordResetIn(BaseModel):
    new_password: str


class TokenCreate(BaseModel):
    name: str
    role: str  # "admin" | "readonly"


@app.get("/api/users")
async def api_list_users(_admin: AdminUser):
    """Return every user row (admin-only)."""
    with db_conn() as c:
        return {"users": auth.list_users(c)}


@app.post("/api/users")
async def api_create_user(
    u: UserCreate,
    _admin: AdminUser,
):
    """Create a new user with the supplied role + password."""
    name = (u.username or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Username is required.")
    if u.role not in ("admin", "readonly"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'readonly'.")
    if u.auth_source not in ("local", "authentik"):
        raise HTTPException(status_code=400, detail="auth_source must be 'local' or 'authentik'.")
    if u.auth_source == "local":
        if not u.password or len(u.password) < 8:
            raise HTTPException(status_code=400, detail="Local users need a password with 8+ characters.")
    with db_conn() as c:
        if auth.get_user_by_username(c, name):
            raise HTTPException(status_code=409, detail="That username is already taken.")
        user = auth.create_user(
            c, name, u.email or None,
            u.password if u.auth_source == "local" else None,
            u.role, u.auth_source,
        )
        _ops_mod.write_admin_audit(
            c, "user_create",
            target_kind="user", target_name=user.username, target_id=str(user.id),
            actor=_admin.username,
            message=f"Created {u.auth_source} user '{user.username}' with role '{u.role}'",
        )
    return {"ok": True, "id": user.id, "username": user.username, "role": user.role}


@app.patch("/api/users/{user_id}")
async def api_update_user(
    user_id: int,
    p: UserPatch,
    admin: AdminUser,
):
    """Patch one user's mutable fields (role / disabled / display name)."""
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if p.role is not None and p.role not in ("admin", "readonly"):
            raise HTTPException(status_code=400, detail="Role must be 'admin' or 'readonly'.")
        # Guard: can't demote or disable the last active admin — that
        # would lock everyone out of admin functions.
        new_role = p.role if p.role is not None else target.role
        new_disabled = p.disabled if p.disabled is not None else target.disabled
        losing_admin = target.role == "admin" and not target.disabled and (
            new_role != "admin" or new_disabled
        )
        if losing_admin and auth.count_active_admins(c) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot demote or disable the last active admin.",
            )
        changes = []
        if p.role is not None:
            auth.set_user_role(c, user_id, p.role)
            changes.append(f"role -> {p.role}")
        if p.disabled is not None:
            auth.set_user_disabled(c, user_id, bool(p.disabled))
            changes.append(f"disabled -> {bool(p.disabled)}")
        if changes:
            _ops_mod.write_admin_audit(
                c, "user_update",
                target_kind="user", target_name=target.username, target_id=str(user_id),
                actor=admin.username,
                message=f"Updated user '{target.username}': {', '.join(changes)}",
            )
    return {"ok": True}


@app.delete("/api/users/{user_id}")
async def api_delete_user(
    user_id: int,
    admin: AdminUser,
):
    """Delete a user by id — refuses to delete self or the last active admin."""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You can't delete yourself.")
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target.role == "admin" and not target.disabled and auth.count_active_admins(c) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the last active admin.",
            )
        # Capture the avatar path BEFORE the delete so we can unlink
        # the file on disk afterwards. Without this the file lingers
        # under /app/data/avatars/ and a recycled user-id (rare —
        # autoincrement reset / restore-from-backup) would silently
        # inherit the orphan. in the code review.
        profile = auth.get_user_profile(c, user_id) or {}
        avatar_path = (profile.get("avatar_path") or "").strip()
        target_username = target.username
        auth.delete_user(c, user_id)
        _ops_mod.write_admin_audit(
            c, "user_delete",
            target_kind="user", target_name=target_username, target_id=str(user_id),
            actor=admin.username,
            message=f"Deleted user '{target_username}' (id={user_id})",
        )
    if avatar_path:
        try:
            full = os.path.join(_AVATAR_DIR, avatar_path)
            if os.path.exists(full):
                os.remove(full)
        except OSError:
            pass  # best-effort cleanup; the orphan is cosmetic
    return {"ok": True}


@app.post("/api/users/{user_id}/reset-password")
async def api_reset_password(
    user_id: int,
    r: PasswordResetIn,
    admin: AdminUser,
):
    """Admin password-reset for a local user.

    Note: this ALSO clears any TOTP enrolment. Operators reset
    passwords when a user has lost access; that usually means their
    authenticator device is gone too. The user re-enrols via Profile
    after the next login if 2FA is still required by policy.
    """
    if not r.new_password or len(r.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be 8+ characters.")
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target.auth_source != "local":
            raise HTTPException(
                status_code=400,
                detail="Authentik-managed users must change their password in Authentik.",
            )
        auth.admin_reset_password(c, user_id, r.new_password)
        _ops_mod.write_admin_audit(
            c, "user_pw_reset",
            target_kind="user", target_name=target.username, target_id=str(user_id),
            actor=admin.username,
            message=f"Admin reset password for '{target.username}' (also clears TOTP)",
        )
    return {"ok": True}


@app.post("/api/users/{user_id}/disable-totp")
async def api_admin_disable_totp(
    user_id: int,
    _request: Request,
    admin: AdminUser,
):
    """Admin override: clear a user's TOTP enrolment + lockout state.

    Useful when a user has lost their authenticator device. The user
    re-enrols via Profile on the next login if policy still requires
    2FA for their role. Audited via the history table with
    op_type='totp_admin_disabled'.
    """
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target.auth_source != "local":
            raise HTTPException(
                status_code=400,
                detail="Authentik users manage 2FA in their IdP.",
            )
        state = auth.get_user_totp_state(c, user_id)
        if not state["enabled"]:
            return {"ok": True, "already_disabled": True}
        auth.clear_user_totp(c, user_id)
        # Audit row -- mirrors the ssh_run pattern above.
        try:
            # `write_admin_audit` calls `assert_op_type` internally and
            # uses the same column shape so the audit row lands
            # identically to the previous direct-INSERT.
            _ops_mod.write_admin_audit(
                c, "totp_admin_disabled",
                target_kind="auth",
                target_name=target.username, target_id=str(user_id),
                actor=admin.username,
                message=f"2FA disabled for {target.username} by {admin.username}",
            )
        except Exception as e:
            # Defensive log + continue is correct (don't roll back the
            # credential change just because the audit row failed), but
            # a silent `print` to stderr meant the operator looking at
            # History saw no record of the change. Escalate to a
            # notification so the operator sees the missing audit trail
            # in-app + Apprise. The credential change ITSELF persisted
            # via `auth.disable_totp` at line ~16266 — the notification
            # carries the disabled-target + the SQL failure detail.
            print(f"[totp] audit-log insert failed: {e}")
            try:
                from logic.ops import notify as _notify
                await _notify(
                    f"⚠ TOTP audit-row missing for {target.username}",
                    f"2FA was disabled for {target.username} by {admin.username}, "
                    f"but the History audit-row INSERT failed: {e!r}. "
                    f"The credential change DID persist; only the audit "
                    f"trail is missing.",
                    "warning",
                    event="totp_audit_log_failed",
                    actor_username=admin.username,
                    target_kind="auth",
                    target_id=str(user_id),
                )
            except Exception as _nerr:  # noqa: BLE001
                print(f"[totp] audit-failure notification ALSO failed: {_nerr}")
    print(f"[totp] {target.username} disabled BY ADMIN ({admin.username})")
    return {"ok": True}


class TotpForceIn(BaseModel):
    force: bool


@app.post("/api/users/{user_id}/totp-force")
async def api_admin_totp_force(
    user_id: int,
    body: TotpForceIn,
    admin: AdminUser,
):
    """Admin override: per-user force-2FA flag.

    Layers ON TOP of the global totp_required_for_admins / _users
    policy — flipping this ON forces 2FA for THIS user even when
    the global policy doesn't require it for their role. Forcing
    OFF reverts to whatever the global policy says (if global policy
    requires 2FA for the role, the user still has to use it).

    Forcing 2FA on a user who hasn't enrolled yet causes their next
    login to land in the forced-enrolment QR flow — already handled
    by api_local_login's multi-step path.

    Audited via the history table with op_type='totp_force_set'.
    """
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target.auth_source != "local":
            raise HTTPException(
                status_code=400,
                detail="Authentik users manage 2FA in their IdP.",
            )
        if bool(target.totp_force_required) == bool(body.force):
            return {"ok": True, "force_required": bool(body.force), "no_change": True}
        auth.set_user_totp_force_required(c, user_id, bool(body.force))
        try:
            _ops_mod.write_admin_audit(
                c, "totp_force_set",
                target_kind="auth",
                target_name=target.username, target_id=str(user_id),
                actor=admin.username,
                message=(f"2FA force-required {'enabled' if body.force else 'cleared'} "
                         f"for {target.username} by {admin.username}"),
            )
        except Exception as e:
            # Same escalation as totp_admin_disabled — surface the
            # audit-row failure to the operator via in-app notification
            # so they know the History trail is missing for this
            # admin action even though the credential change itself
            # persisted.
            print(f"[totp] audit-log insert failed: {e}")
            try:
                from logic.ops import notify as _notify
                await _notify(
                    f"⚠ TOTP force-set audit-row missing for {target.username}",
                    f"TOTP force-required was {'enabled' if body.force else 'cleared'} "
                    f"for {target.username} by {admin.username}, but the History "
                    f"audit-row INSERT failed: {e!r}. The flag DID persist; "
                    f"only the audit trail is missing.",
                    "warning",
                    event="totp_audit_log_failed",
                    actor_username=admin.username,
                    target_kind="auth",
                    target_id=str(user_id),
                )
            except Exception as _nerr:  # noqa: BLE001
                print(f"[totp] audit-failure notification ALSO failed: {_nerr}")
    print(
        f"[totp] {target.username} force-2FA "
        f"{'ENABLED' if body.force else 'CLEARED'} BY ADMIN ({admin.username})"
    )
    return {"ok": True, "force_required": bool(body.force)}


@app.get("/api/sessions")
async def api_list_sessions(_admin: AdminUser):
    """Return every active session across every user (admin-only)."""
    with db_conn() as c:
        return {"sessions": auth.list_sessions(c)}


@app.delete("/api/sessions/{token_id}")
async def api_revoke_session(
    token_id: str,
    admin: AdminUser,
):
    """Revoke one session by token-id (admin-only)."""
    with db_conn() as c:
        auth.delete_session(c, token_id)
        _ops_mod.write_admin_audit(
            c, "session_revoke",
            target_kind="session", target_name=token_id, target_id=token_id,
            actor=admin.username,
            message=f"Revoked session token {token_id}",
        )
    return {"ok": True}


@app.get("/api/tokens")
async def api_list_tokens(_admin: AdminUser):
    """List every API token (raw value never shown — hash-only at rest)."""
    with db_conn() as c:
        return {"tokens": auth.list_api_tokens(c)}


@app.post("/api/tokens")
async def api_create_token(
    t: TokenCreate,
    admin: AdminUser,
):
    """Mint a new API token. The raw token is returned EXACTLY ONCE on create."""
    name = (t.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    if t.role not in ("admin", "readonly"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'readonly'.")
    try:
        with db_conn() as c:
            raw = auth.create_api_token(c, name, t.role, admin.id)
            _ops_mod.write_admin_audit(
                c, "token_create",
                target_kind="api_token", target_name=name, target_id=name,
                actor=admin.username,
                message=f"Created API token '{name}' with role '{t.role}'",
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="A token with that name already exists.")
    # Raw token returned ONCE. UI shows a one-time reveal modal; we store
    # only the SHA-256 hash. If lost, the operator must rotate.
    return {"ok": True, "name": name, "role": t.role, "token": raw}


@app.delete("/api/tokens/{token_id}")
async def api_delete_token(
    token_id: int,
    admin: AdminUser,
):
    """Revoke an API token by id (idempotent — 404 is success)."""
    with db_conn() as c:
        auth.delete_api_token(c, token_id)
        _ops_mod.write_admin_audit(
            c, "token_revoke",
            target_kind="api_token", target_name=str(token_id), target_id=str(token_id),
            actor=admin.username,
            message=f"Revoked API token id={token_id}",
        )
    return {"ok": True}


# ============================================================================
# Backups — zip containing the full SQLite DB + avatars directory.
# Admin-only; list/create/download/delete/restore. See logic/backups.py for
# the safety dance (consistent .backup() snapshot, pre-restore auto-snapshot,
# path-traversal guards).
# ============================================================================
@app.get("/api/backups")
async def api_list_backups(_admin: AdminUser):
    """List every SQLite + avatars snapshot in the backups directory."""
    return {"backups": backups.list_backups()}


@app.post("/api/backups")
async def api_create_backup(admin: AdminUser):
    """Create a new SQLite + avatars snapshot via SQLite's online .backup() API."""
    result = backups.create_backup()
    # Retention — surfaced to the operator in the response so they can
    # see what got pruned without re-listing. Zero/empty setting means
    # "keep all", which is the safe default for a fresh install.
    # `backup_retention_count` is now a TUNABLE (DB > env > default
    # with bounds clamp); legacy plain-settings row still hydrates
    # the form for parity.
    try:
        keep = tuning.tuning_int(Tunable.BACKUP_RETENTION_COUNT)
    except (TypeError, ValueError):
        keep = 0
    pruned = backups.prune_backups(keep) if keep > 0 else []
    if pruned:
        result = {**result, "pruned": pruned}
    backup_name = str((result or {}).get("name", "") or "")
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "backup_create",
            target_kind="backup", target_name=backup_name, target_id=backup_name,
            actor=admin.username,
            message=f"Created backup '{backup_name}'" + (f" (pruned {len(pruned)})" if pruned else ""),
        )
    return result


@app.get("/api/backups/{name}")
async def api_download_backup(
    name: str, _admin: AdminUser,
):
    """Stream a named backup zip to the operator."""
    try:
        path = backups.backup_path(name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid backup name")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, filename=name, media_type="application/zip")


@app.delete("/api/backups/{name}")
async def api_delete_backup(
    name: str, admin: AdminUser,
):
    """Delete a named backup file (idempotent — already-gone is success)."""
    try:
        backups.delete_backup(name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid backup name")
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "backup_delete",
            target_kind="backup", target_name=name, target_id=name,
            actor=admin.username,
            message=f"Deleted backup '{name}'",
        )
    return {"ok": True}


@app.post("/api/backups/{name}/restore")
async def api_restore_backup_named(
    name: str, admin: AdminUser,
):
    """Restore the named backup over the live DB (audit-row written first)."""
    try:
        result = backups.restore_by_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "backup_restore",
            target_kind="backup", target_name=name, target_id=name,
            actor=admin.username,
            message=f"Restored backup '{name}'",
        )
    return result


@app.post("/api/backups/restore")
async def api_restore_backup_upload(
    request: Request, _admin: AdminUser,
):
    """Upload a zip file and restore from it. 200 MB cap."""
    form = await request.form()
    file_field = form.get("file")
    if file_field is None or isinstance(file_field, str) or not hasattr(file_field, "read"):
        raise HTTPException(status_code=400, detail="Field 'file' missing")
    file = file_field  # type: ignore[assignment]  # narrowed via isinstance + hasattr guard
    data = await file.read()
    if len(data) > backups.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Upload too large (max {backups.MAX_UPLOAD_BYTES // 1_000_000} MB)",
        )
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty upload")
    # Persist the uploaded zip to a temp file on the data volume so the
    # restore function (which expects a filesystem path) can work on it.
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".zip",
        dir=os.path.dirname(DB_PATH) or ".",
    ) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        result = backups.restore_from_file(tmp_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid backup: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    # noinspection PyUnboundLocalVariable
    return result  # `result` is bound iff neither except branch fired (both raise — terminal).


# ============================================================================
# Settings-as-Code — export / import the operator-tunable admin config as
# a single human-readable JSON document. See `logic/config_export.py` for
# the snapshot shape, secret-redaction contract, and apply semantics.
# Admin-only — every endpoint gates on require_admin.
# ============================================================================


@app.get("/api/admin/config-backup/export")
async def api_config_backup_export(_admin: AdminUser):
    """Build a fresh snapshot and stream it as a JSON download.

    Operators commit the file to a private git repo for change tracking.
    Secrets (api keys / passwords / tokens / private keys) are redacted
    to the literal sentinel string `"__OMITTED__"`; on import those
    entries are skipped so the live DB's secret material is preserved.
    """
    snap = config_export.build_snapshot()
    blob = json.dumps(snap, indent=2, sort_keys=True)
    ts = time.strftime("%Y.%m.%d_%H.%M.%S", time.localtime())
    fname = f"omnigrid-config_{ts}.json"
    return Response(
        content=blob,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/admin/config-backup/preview")
async def api_config_backup_preview(_admin: AdminUser):
    """Return the current snapshot as a JSON object (NOT a download).

    Used by the Admin → Config backup tab to show the operator what
    they're about to download / commit / restore. Same shape as the
    download endpoint; just no Content-Disposition header.
    """
    return config_export.build_snapshot()


class ConfigBackupImportIn(BaseModel):
    """Body for the import endpoint — single `payload` field carries
    the full snapshot dict the operator uploaded. Pydantic accepts
    arbitrary nested JSON via `dict`."""
    payload: dict


@app.post("/api/admin/config-backup/import")
async def api_config_backup_import(
    body: ConfigBackupImportIn,
    admin: AdminUser,
):
    """Apply an uploaded snapshot to the live DB. See
    `logic.config_export.apply_snapshot` for the per-surface semantics
    (settings: per-key UPSERT skipping redacted; schedules + ai_memory:
    replace-all).

    Returns the apply-result counters + warnings so the operator's
    toast can summarise what changed.
    """
    try:
        result = config_export.apply_snapshot(body.payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "config_backup_import",
            target_kind="config_backup",
            actor=admin.username,
            message=f"Imported config-backup snapshot ({len(result.get('warnings') or [])} warning(s))",
        )
    return result


@app.get("/api/admin/config-backup/list")
async def api_config_backup_list(_admin: AdminUser):
    """List saved snapshot files written by the `config_backup`
    schedule kind (or any future manual save-to-disk path)."""
    return {"files": config_export.list_snapshots()}


@app.post("/api/admin/config-backup/save")
async def api_config_backup_save(admin: AdminUser):
    """Write a fresh snapshot to disk on demand. Same path the
    `config_backup` schedule kind uses. Returns the saved file's
    {name, size, mtime}."""
    result = config_export.save_snapshot_to_disk()
    fname = (result or {}).get("name", "") or ""
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "config_backup_save",
            target_kind="config_backup", target_name=fname, target_id=fname,
            actor=admin.username,
            message=f"Saved config-backup snapshot to disk: '{fname}'",
        )
    return result


@app.get("/api/admin/config-backup/saved/{name}")
async def api_config_backup_download_saved(
    name: str, _admin: AdminUser,
):
    """Download a previously-saved snapshot file."""
    try:
        full = config_export.safe_path(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(full, media_type="application/json", filename=name)


@app.post("/api/admin/config-backup/saved/{name}/restore")
async def api_config_backup_restore_saved(
    name: str, admin: AdminUser,
):
    """Read a saved snapshot file and apply it. Same as POSTing the
    file's contents to `/api/admin/config-backup/import`, just routed
    through the disk path so the operator doesn't have to re-upload."""
    try:
        snap = config_export.read_snapshot(name)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        result = config_export.apply_snapshot(snap)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "config_backup_restore",
            target_kind="config_backup", target_name=name, target_id=name,
            actor=admin.username,
            message=f"Restored config-backup snapshot '{name}' from disk",
        )
    return result


@app.delete("/api/admin/config-backup/saved/{name}")
async def api_config_backup_delete_saved(
    name: str, admin: AdminUser,
):
    """Delete a saved snapshot file."""
    try:
        config_export.delete_snapshot(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "config_backup_delete",
            target_kind="config_backup", target_name=name, target_id=name,
            actor=admin.username,
            message=f"Deleted config-backup snapshot '{name}'",
        )
    return {"ok": True}


# ============================================================================
# Scheduler — admin-defined recurring jobs. See logic/schedules.py for the
# tick loop + kind registry. Admin-only CRUD; POST .../run fires manually.
# ============================================================================
class ScheduleIn(BaseModel):
    name: str
    kind: str
    params: Optional[dict] = None
    interval_seconds: int
    enabled: bool = True
    # Cadence bundle — cadence_mode picks which of the fields below the
    # tick loop consults. See logic.schedules.CADENCE_MODES.
    cadence_mode: str = "interval"
    run_at_hhmm: Optional[str] = None  # daily/weekly/monthly anchor
    days_of_week: Optional[list[int]] = None  # weekly, Mon=0..Sun=6
    day_of_month: Optional[int] = None  # monthly, 1..31 clamped to EOM


class SchedulePatch(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    params: Optional[dict] = None
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None
    cadence_mode: Optional[str] = None
    # For these three, None in the wire payload means "don't touch";
    # explicit empty ("" / []) means "clear" — handled by
    # schedules.update_schedule().
    run_at_hhmm: Optional[str] = None
    days_of_week: Optional[list[int]] = None
    day_of_month: Optional[int] = None


@app.get("/api/schedules")
async def api_list_schedules(_admin: AdminUser):
    """Return every schedule row + its next-fire timestamp."""
    with db_conn() as c:
        return {
            "schedules": schedules.list_schedules(c),
            "kinds": sorted(schedules.SCHEDULE_KINDS.keys()),
            "min_interval_seconds": schedules.MIN_INTERVAL_SECONDS,
        }


@app.post("/api/schedules")
async def api_create_schedule(
    s: ScheduleIn,
    admin: AdminUser,
):
    """Create a new schedule row (validates kind + cron / interval expression)."""
    name = (s.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    if s.kind not in schedules.SCHEDULE_KINDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown schedule kind '{s.kind}'. "
                f"Known: {', '.join(sorted(schedules.SCHEDULE_KINDS.keys()))}"
            ),
        )
    if s.interval_seconds < schedules.MIN_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"interval_seconds must be >= {schedules.MIN_INTERVAL_SECONDS}"
            ),
        )
    params = s.params or {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be a JSON object.")
    try:
        with db_conn() as c:
            row = schedules.create_schedule(
                c, name, s.kind, params, int(s.interval_seconds),
                bool(s.enabled),
                run_at_hhmm=s.run_at_hhmm,
                cadence_mode=s.cadence_mode or "interval",
                days_of_week=s.days_of_week,
                day_of_month=s.day_of_month,
            )
            _ops_mod.write_admin_audit(
                c, "schedule_create",
                target_kind="schedule", target_name=name, target_id=str(row.get("id") or ""),
                actor=admin.username,
                message=f"Created schedule '{name}' (kind={s.kind}, interval={s.interval_seconds}s)",
            )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="A schedule with that name already exists.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "schedule": row}


@app.patch("/api/schedules/{schedule_id}")
async def api_update_schedule(
    schedule_id: int,
    p: SchedulePatch,
    admin: AdminUser,
):
    """Patch one schedule's mutable fields by id."""
    # exclude_unset keeps explicit None values so "clear this field" works
    # via wire-level null (e.g. flipping back to interval mode by sending
    # {cadence_mode:"interval", run_at_hhmm:null, days_of_week:null,
    # day_of_month:null}). update_schedule() knows which fields are
    # clearable-on-None; the rest still ignore None as before.
    patch_fields = p.model_dump(exclude_unset=True)
    if "name" in patch_fields and patch_fields["name"] is not None:
        patch_fields["name"] = patch_fields["name"].strip()
        if not patch_fields["name"]:
            raise HTTPException(status_code=400, detail="Name cannot be blank.")
    try:
        with db_conn() as c:
            existing = schedules.get_schedule(c, schedule_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Schedule not found.")
            row = schedules.update_schedule(c, schedule_id, **patch_fields)
            sched_name = (row or {}).get("name") or existing.get("name") or str(schedule_id)
            _ops_mod.write_admin_audit(
                c, "schedule_update",
                target_kind="schedule", target_name=sched_name, target_id=str(schedule_id),
                actor=admin.username,
                message=f"Updated schedule '{sched_name}': {', '.join(sorted(patch_fields.keys()))}",
            )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="A schedule with that name already exists.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "schedule": row}


@app.delete("/api/schedules/{schedule_id}")
async def api_delete_schedule(
    schedule_id: int,
    admin: AdminUser,
):
    """Delete a schedule by id (idempotent — already-gone is success)."""
    with db_conn() as c:
        existing = schedules.get_schedule(c, schedule_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        sched_name = existing.get("name") or str(schedule_id)
        schedules.delete_schedule(c, schedule_id)
        _ops_mod.write_admin_audit(
            c, "schedule_delete",
            target_kind="schedule", target_name=sched_name, target_id=str(schedule_id),
            actor=admin.username,
            message=f"Deleted schedule '{sched_name}' (kind={existing.get('kind') or 'unknown'})",
        )
    return {"ok": True}


@app.post("/api/schedules/{schedule_id}/run")
async def api_run_schedule(
    schedule_id: int,
    admin: AdminUser,
):
    """Fire a schedule immediately, bypassing its interval.

    Uses the same kind-callable path as the tick loop, so the resulting
    op flows through ops.py exactly as if the schedule had been due.
    Returns the op id so the UI can deep-link the ops panel.
    """
    with db_conn() as c:
        s = schedules.get_schedule(c, schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    try:
        op_id = await schedules.fire_schedule(s)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fire failed: {e}")
    sched_name = s.get("name") or str(schedule_id)
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "schedule_run_now",
            target_kind="schedule", target_name=sched_name, target_id=str(schedule_id),
            actor=admin.username,
            message=f"Manually fired schedule '{sched_name}' (op_id={op_id or 'unknown'})",
        )
    return {"ok": True, "op_id": op_id}


@app.get("/api/schedules/queue")
async def api_schedule_queue(
    limit: int = 50,
    page: int = 1,
    page_size: int = 0,
    search: str = "",
    *,
    _admin: AdminUser,
):
    """Recent scheduler-driven ops from the history table.

    Filtered to ``actor='scheduler'`` so user-triggered runs of the
    same op types don't clutter the view.

    Pagination contract: when ``page_size`` is passed the response
    returns ONE page of rows plus `total` / `page` / `page_size` so
    the UI can render "Page N of M" without double-fetching. When
    ``page_size`` is 0 (or omitted), the endpoint falls back to the
    legacy flat-list shape (`limit` rows, no `total`) so older
    clients keep working.

    Optional ``search`` param does a case-insensitive substring
    match on ``target_name`` / ``op_type`` / ``status``. Backend
    filtering keeps the page count accurate when the operator is
    searching across thousands of rows.
    """
    # Build a reusable WHERE-clause + bind args. Backend search lives
    # entirely in SQL so the page count + slice are correct against
    # the filtered set, not the unfiltered total.
    actor = schedules.SCHEDULER_ACTOR
    where = "actor = ?"
    args: list = [actor]
    s = (search or "").strip().lower()
    if s:
        where += (" AND ("
                  "LOWER(COALESCE(target_name, '')) LIKE ? OR "
                  "LOWER(COALESCE(op_type, '')) LIKE ? OR "
                  "LOWER(COALESCE(status, '')) LIKE ?"
                  ")")
        like = f"%{s}%"
        args.extend([like, like, like])

    # Legacy single-query path — keep until every caller is migrated.
    if page_size <= 0:
        limit = max(1, min(int(limit), 500))
        with db_conn() as c:
            rows = c.execute(
                f"SELECT * FROM history WHERE {where} "
                f"ORDER BY ts DESC LIMIT ?",
                args + [limit],
            ).fetchall()
        return {"queue": [dict(r) for r in rows]}

    # Paginated path — count + slice. Cap page_size at 100 to guard
    # against accidentally-huge queries.
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    offset = (page - 1) * page_size
    with db_conn() as c:
        total_row = c.execute(
            f"SELECT COUNT(*) FROM history WHERE {where}", args,
        ).fetchone()
        total = int((total_row[0] if total_row else 0) or 0)
        rows = c.execute(
            f"SELECT * FROM history WHERE {where} "
            f"ORDER BY ts DESC LIMIT ? OFFSET ?",
            args + [page_size, offset],
        ).fetchall()
    return {
        "queue": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "search": search or "",
    }


# Login HTML page. Served as a discrete route (not via StaticFiles) because
# /login has no trailing slash and we want it to map to static/login.html
# directly without relying on html=True directory-index behaviour. Also
# listed in auth.FULLY_PUBLIC_PREFIXES so the middleware never gates it.
@app.get("/login")
async def login_page():
    """Serve the login HTML shell (anonymous; redirects already-authed users)."""
    return _render_shell("static/login.html")


# UI icon sprite. Served as a discrete route (not via the catch-all
# StaticFiles mount) so we can attach a long-cache header — every
# `<use href="/img/ui-sprite.svg?v=__APP_VERSION__#icon-..."/>` site
# is version-busted by the shell renderer at request time, so the URL
# itself changes on every PATCH bump. With `immutable` + a one-year
# max-age the browser parks a single sprite copy across navigations
# (no per-page revalidation round-trip) and the `?v=...` change forces
# a fresh fetch the next time the SPA shell ships a new version.
# Registered BEFORE the StaticFiles "/" mount per CLAUDE.md mount-order
# rule.
@app.get("/img/ui-sprite.svg")
async def serve_ui_sprite():
    """Serve the SVG sprite that ships every Lucide icon used by the SPA."""
    path = "static/img/ui-sprite.svg"
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="UI sprite not found")
    return FileResponse(
        path,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# Shell-HTML cache — tiny map keyed by file path. Each entry stores the
# assembled file bytes (with `<!-- INCLUDE: ... -->` markers expanded) and
# the combined mtime tuple of the master file + every referenced partial;
# a disk change to ANY of them invalidates the entry lazily on the next
# request. `str.replace` runs on every hit (cheap — the two HTMLs together
# are <200 KB) so `__APP_VERSION__` marker references pick up a new PATCH
# as soon as VERSION.txt changes, without any restart.
_SHELL_CACHE: dict = {}

# Partial-include marker. Matches `<!-- INCLUDE: <path> -->` with
# arbitrary leading whitespace preserved (via the `.sub` callback below
# that re-emits the original indent). The path is resolved relative to
# `static/_partials/` and a path-traversal guard refuses anything that
# would escape the partials root. One level of inlining only — partials
# don't recursively include each other today (keeps the contract simple
# and the cache-key audit shallow).
_INCLUDE_RE = re.compile(r"<!--\s*INCLUDE:\s*(?P<rel>\S+)\s*-->")
_PARTIALS_BASE = os.path.join("static", "_partials")


def _expand_includes(body: str, path: str) -> tuple[str, tuple]:
    """Expand `<!-- INCLUDE: <rel-path> -->` markers in `body`.

    Returns `(assembled_body, mtime_signature)` where `mtime_signature`
    is a tuple of `(master_mtime_ns, *(partial_path, partial_mtime_ns)...)`
    that the caller uses as the cache key. Any partial that fails to
    read collapses to an empty string in the output (visible visual
    regression but the page still renders) and contributes its
    attempted-mtime to the signature so the next disk change invalidates.

    Multi-pass: an included partial can ITSELF carry INCLUDE markers
    pointing at other partials (e.g. an admin sub-tab template
    embedding the shared `_components/og-range-picker.html`). The
    expander iterates until the body stabilises with no remaining
    markers OR `_MAX_INCLUDE_DEPTH` is reached (safety net against a
    pathological self-referential include loop — collapses any
    still-unresolved markers to empty strings rather than spinning).
    """
    base = os.path.abspath(_PARTIALS_BASE)
    sig: list = []
    try:
        sig.append(os.stat(path).st_mtime_ns)
    except OSError:
        sig.append(0)

    def _replace(m: "re.Match[str]") -> str:
        rel = m.group("rel")
        candidate = os.path.abspath(os.path.join(_PARTIALS_BASE, rel))
        # Path-traversal guard: refuse anything that escapes _partials/.
        if candidate != base and not candidate.startswith(base + os.sep):
            sig.append((rel, 0))
            return ""
        try:
            mt = os.stat(candidate).st_mtime_ns
            with open(candidate, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            sig.append((rel, 0))
            return ""
        sig.append((rel, mt))
        return content

    _MAX_INCLUDE_DEPTH = 8
    expanded = body
    for _depth in range(_MAX_INCLUDE_DEPTH):
        if not _INCLUDE_RE.search(expanded):
            break
        expanded = _INCLUDE_RE.sub(_replace, expanded)
    else:
        # Hit the depth cap with markers still unresolved — strip any
        # remaining markers so they don't render as literal HTML comments
        # in the operator's browser. Diagnostic print so a future
        # contributor sees the loop in Admin → Logs instead of a silent
        # truncation.
        if _INCLUDE_RE.search(expanded):
            print(
                f"[_expand_includes] WARN: include depth {_MAX_INCLUDE_DEPTH} "
                f"exceeded for {path!r} — remaining markers stripped; "
                f"check for a self-referential INCLUDE loop."
            )
            expanded = _INCLUDE_RE.sub("", expanded)
    return expanded, tuple(sig)


# noinspection PyTypeChecker,PyUnresolvedReferences
def _render_shell(path: str) -> Response:
    """Serve an HTML shell with `__APP_VERSION__` → current version.

    Used for `/` and `/login` — both reference external JS/CSS as
    `src="/js/app.js?v=__APP_VERSION__"`, and this is the substitution
    point that turns that literal into an actual cache-bustable URL.
    Any other entry-point HTML that references versioned assets should
    be served through this too; the bare StaticFiles mount at "/" won't
    run the substitution.

    Also expands `<!-- INCLUDE: admin/<tab>.html -->` markers so the
    admin sub-tabs can live in `static/_partials/admin/` instead of one
    14k-line `index.html`. Cache key tracks every partial's mtime so a
    partial edit is picked up on the next request without restart.
    """
    try:
        master_mtime = os.stat(path).st_mtime_ns
    except OSError:
        raise HTTPException(status_code=404, detail=f"{path} not found")
    cached = _SHELL_CACHE.get(path)
    # Pre-bind `body` so the linter can prove it's always assigned. The
    # control-flow below sets it in BOTH branches (cache hit + cache
    # miss), but type-checkers can't trace through the `cached = None`
    # reassignment that bridges the two; the empty initial value is
    # never observed at runtime because the substitution call always
    # follows one of the two write paths.
    body: str = ""
    # Quick path: cached entry's signature still matches every disk file
    # we depend on. The master mtime alone isn't enough — a partial edit
    # leaves the master untouched so we re-walk the partial mtimes too.
    if cached is not None and cached[0][0] == master_mtime:
        # Re-stat every partial referenced by the cached signature; if
        # they all match, serve from cache. Cheap: ~18 stat() calls for
        # the admin partials, each <1 µs.
        ok = True
        for entry in cached[0][1:]:
            rel, prev_mt = entry
            cand = os.path.abspath(os.path.join(_PARTIALS_BASE, rel))
            try:
                if os.stat(cand).st_mtime_ns != prev_mt:
                    ok = False
                    break
            except OSError:
                if prev_mt != 0:
                    ok = False
                    break
        if ok:
            body = cached[1]
        else:
            cached = None
    if cached is None:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        body, sig = _expand_includes(raw, path)
        _SHELL_CACHE[path] = (sig, body)
    # Use the LIVE version, not the import-time snapshot. This lets an
    # operator edit /app/VERSION.txt on the server and have cache-busting
    # URLs follow without restarting the container.
    body = body.replace("__APP_VERSION__", read_version())
    # Cache-Control: no-cache, must-revalidate — the SPA shell is the
    # entry point that references EVERY versioned asset (`/js/app.js?v=...`,
    # `/css/style.css`, the inline `window.__APP_VERSION__` global), so a
    # browser-cached shell would freeze the whole asset chain at a stale
    # PATCH and the `?v=` bust scheme falls apart. `no-cache` doesn't
    # disable caching — it forces revalidation on every navigation so a
    # 304 is allowed when nothing changed; only the body bytes are
    # skipped, the headers (including the freshly-substituted version)
    # are re-served. Safe for the SPA shell; do NOT copy onto static
    # assets (they SHOULD cache by the URL-versioning contract).
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


# SPA shell. Served through _render_shell so the version substitution
# applies — StaticFiles at "/" would hand back the raw file with the
# literal "__APP_VERSION__" marker still in the script srcs. Registered
# BEFORE the StaticFiles mount below (mount-order rule applies).
@app.get("/")
async def spa_shell():
    """Serve the SPA master HTML for every non-/api path (catch-all route)."""
    return _render_shell("static/index.html")


# Deep-link routes for every SPA view. The Alpine front-end calls
# `history.replaceState('/nodes')` when you switch tabs so reloading
# a deep link drops you back on the same tab; without a matching
# server route, `GET /nodes` would fall through to the StaticFiles
# mount and 404. The shell itself is identical to `/` — Alpine's
# `_applyRouteFromPath()` picks the view based on `location.pathname`
# once the page boots. Settings / Admin accept a sub-path segment
# (`/settings/oidc`, `/admin/users`) so those deep links work too.
# Strict rule: every entry in `navItems()` (static/js/app.js) must
# have a matching entry here, otherwise a refresh / direct-URL visit
# returns the StaticFiles 404 `{"detail":"Not Found"}`.
_SPA_ROUTES = ("stacks", "services", "nodes", "hosts", "apps", "history")

for _view in _SPA_ROUTES:
    app.add_api_route(f"/{_view}", spa_shell, methods=["GET"])


@app.get("/settings")
@app.get("/settings/{section}")
async def spa_settings_shell(section: str = ""):
    """SPA shell route for /settings and /settings/<section> deep links.
    Section is consumed client-side by `_applyRouteFromPath()`; this
    handler only needs to return the master HTML."""
    _ = section
    return _render_shell("static/index.html")


@app.get("/admin")
@app.get("/admin/{tab}")
async def spa_admin_shell(tab: str = ""):
    """SPA shell route for /admin and /admin/<tab> deep links.
    Tab is consumed client-side; this handler only returns the master
    HTML."""
    _ = tab
    return _render_shell("static/index.html")


@app.get("/stats")
@app.get("/stats/{tab}")
async def spa_stats_shell(tab: str = ""):
    """SPA shell route for /stats and /stats/<tab> deep links.
    Tab is consumed client-side; this handler only returns the master
    HTML."""
    _ = tab
    return _render_shell("static/index.html")


# Prometheus scrape endpoint.
# Implemented as a regular route (not app.mount) because Starlette's
# Mount only matches the mount path WITH a trailing slash — bare GET
# /metrics (what every Prometheus scraper sends by default) falls
# through to the StaticFiles catch-all and returns 404. Using a route
# sidesteps the trailing-slash foot-gun entirely.
@app.get("/metrics")
async def prometheus_metrics():
    """Return the Prometheus exposition format for every registered metric."""
    return Response(
        content=metrics.generate_latest(metrics.REGISTRY),
        media_type=metrics.CONTENT_TYPE_LATEST,
    )


# Serve node_modules directly — but only the specific files that
# index.html / login.html / alpine-gate.js actually reference.
# Earlier this was a wildcard `app.mount("/node_modules", StaticFiles(...))`
# which served EVERY file in the tree (readmes, sourcemaps, TS sources,
# unused locales, package metadata) even though only ~7 files are
# actually requested. A prior code review flagged this as
# unnecessary surface bloat — not a security hole (the files are public
# on npm anyway) but tidy + faster to audit.
#
# Adding a new dep that needs serving = add its path to _NPM_ALLOWED.
# Anything outside the allowlist 404s; anything inside is served
# straight from the on-disk file with the correct media-type.
_NPM_ALLOWED: Set[str] = {
    "@tailwindcss/browser/dist/index.global.js",
    "alpinejs/dist/cdn.min.js",
    "sweetalert2/dist/sweetalert2.all.min.js",
    "@xterm/xterm/css/xterm.css",
    "@xterm/xterm/lib/xterm.js",
    "@xterm/addon-fit/lib/addon-fit.js",
    "@xterm/addon-web-links/lib/addon-web-links.js",
    "qrcode-generator/dist/qrcode.js",
}


# FastAPI `{subpath:path}` route-converter accepts segments with slashes —
# required so a request like `/node_modules/@xterm/xterm/lib/xterm.js`
# binds the whole tail to `subpath`. Registered via ``add_api_route``
# instead of ``@app.get`` so PyCharm's FastAPI inspector doesn't try to
# match the ``{subpath:path}`` converter literal against the function's
# parameter list (it parses the whole literal as a parameter name and
# raises a spurious mismatch warning). Programmatic registration is the
# same FastAPI primitive the decorator builds on top of — no behavioural
# difference, just no inspector confusion.
async def api_node_modules(subpath: str = FastApiPath(...)):
    """Allowlist-gated static server for the 7 npm files the SPA actually
    uses. Everything else returns 404 — keeps the served surface tight.
    """
    # Path-traversal guard: no `..` segments, no leading slashes, must
    # match an entry in the allowlist exactly. Belt-and-braces — FastAPI's
    # path converter wouldn't let `..` through in practice, but the
    # explicit check makes the security property obvious.
    if ".." in subpath or subpath.startswith("/") or subpath not in _NPM_ALLOWED:
        raise HTTPException(404, "Not found")
    # Defence-in-depth: even though `_NPM_ALLOWED` is a closed set of
    # 8 known-safe relative paths, also normalise the joined result
    # via `os.path.realpath` and confirm it stays within the
    # node_modules root. Catches any future relaxation of the
    # allowlist (operator adds a new entry that happens to traverse
    # via a symlink) AND silences static-analysis path-injection
    # findings that won't trust enum-allowlist validation alone.
    # Mirrors the `safe_log_path` pattern in `logic/logs.py`.
    root = os.path.realpath("node_modules")
    file_path = os.path.realpath(os.path.join(root, subpath))
    if file_path != root and not file_path.startswith(root + os.sep):
        raise HTTPException(404, "Not found")
    if not os.path.isfile(file_path):
        raise HTTPException(404, "Not found")
    return FileResponse(file_path)


app.add_api_route(
    "/node_modules/{subpath:path}",
    api_node_modules,
    methods=["GET"],
)

# Translation bundles. Mounted at /i18n/ (before the "/" catch-all, same
# ordering rule as /metrics / /node_modules) so the SPA can fetch
# /i18n/en.json, /i18n/ar.json, /i18n/index.json at boot. Anonymous-
# readable: language files are UI strings, not secrets.
if os.path.isdir("static/i18n"):
    app.mount("/i18n", StaticFiles(directory="static/i18n"), name="i18n")

# SPA JavaScript entry + ES-module siblings.
#
# `static/js/app.js` is now an ES module that imports sibling
# `static/js/app-*.js` files. Each `import` URL inside app.js uses
# `?v=__APP_VERSION__` for cache-busting on deploy. StaticFiles serves
# `.js` files raw, so the literal marker would never get substituted —
# this route does the same `__APP_VERSION__` → live version replacement
# `_render_shell()` does for the HTML shell, scoped to the app.js entry
# point + its sibling modules. The substitution is text-level (cheap,
# no parser), bounded by the closed `_APP_JS_MODULES` set so a typo'd
# module path 404s instead of fishing arbitrary files.
#
# Cache-Control: no-cache, must-revalidate — same shape as the SPA
# shell. The `?v=` query string only changes on deploy, so the browser
# revalidates per-tab-open but a 304 is fine in steady state. The
# underlying file bytes change on every deploy regardless because every
# `__APP_VERSION__` site gets substituted with the current PATCH.
_APP_JS_MODULES: Set[str] = set()


def _refresh_app_js_modules() -> None:
    """Discover every `static/js/app*.js` file at startup.
    The set populates `_APP_JS_MODULES`; the route below allows any
    name in this set. Re-scan on container restart only — adding a new
    module file requires a new deploy (which restarts the process)."""
    _APP_JS_MODULES.clear()
    js_dir = os.path.join("static", "js")
    if not os.path.isdir(js_dir):
        return
    for name in os.listdir(js_dir):
        if name.startswith("app") and name.endswith(".js"):
            _APP_JS_MODULES.add(name)


_refresh_app_js_modules()


async def serve_app_js_module(name: str = FastApiPath(...)):
    """Serve a SPA-side JS module with `__APP_VERSION__` substitution.

    Scope: `static/js/app.js` and `static/js/app-*.js` (the ES-module
    refactor of the SPA's top-level component). Other JS files under
    `static/js/` (i18n.js, auth-fetch.js, alpine-gate.js, login.js)
    are served raw — no module imports to cache-bust, the SPA shell's
    own `?v=__APP_VERSION__` query on each `<script>` tag is sufficient.
    """
    js_dir = os.path.join("static", "js")
    file_path = os.path.realpath(os.path.join(js_dir, name))
    js_root = os.path.realpath(js_dir)
    if file_path != js_root and not file_path.startswith(js_root + os.sep):
        raise HTTPException(404, "Not found")
    if not os.path.isfile(file_path):
        raise HTTPException(404, "Not found")
    if name in _APP_JS_MODULES:
        try:
            with open(file_path, encoding="utf-8") as f:
                body = f.read()
        except OSError:
            raise HTTPException(404, "Not found")
        body = body.replace("__APP_VERSION__", read_version())
        return Response(
            content=body,
            media_type="application/javascript; charset=utf-8",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
    # Non-app JS files — serve raw via FileResponse so StaticFiles
    # semantics (mtime-based ETag) still work.
    return FileResponse(
        file_path,
        media_type="application/javascript; charset=utf-8",
    )


app.add_api_route("/js/{name}", serve_app_js_module, methods=["GET"])

# Keep this line LAST — StaticFiles at "/" is a catch-all.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
