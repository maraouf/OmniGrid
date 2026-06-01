"""Self-service WebAuthn + admin user-management endpoints —
`/api/me/webauthn/*` (self-service), `/api/users`
(admin CRUD).

Loads via the star-import chain anchored at `main.py` — every
symbol re-exports into `main`'s namespace so route
decorators reach the shared `app` instance.
"""
"""Continuation of `main_pkg.hosts_routes` — third chunk in the
main → main_pkg.hosts_routes → main_pkg.auth_routes star-import chain.

Loading order:
  1. main.py runs (defines `app`, helpers, models, early routes).
  2. main.py end → `from main_pkg.hosts_routes import *`.
  3. main_pkg.hosts_routes top → `from main import *` (pulls main's
     symbols). Body runs; routes register.
  4. main_pkg.hosts_routes end → `from main_pkg.auth_routes import *`.
  5. main_pkg.auth_routes top → `from main_pkg.hosts_routes import *`
     (transitively pulls main via routes' own star-import).
  6. main_pkg.auth_routes body runs; routes register.
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
from main import *  # noqa: E402,F401,F403
# IDE contract: PyCharm/Pyright can't trace `from X import *`, so
# every name resolved through the wildcard above would be flagged as
# "Unresolved reference". The explicit imports below resolve at
# runtime too (Python's import system caches; second-import is a dict
# lookup), so they're safe + they silence the IDE in every scope.
from main import (  # noqa: E402,F401 — explicit for IDE; runtime via the * above
    AdminUser,
    BaseModel,
    CurrentUser,
    DB_PATH,
    FastApiPath,
    FileResponse,
    HTTPException,
    Request,
    Response,
    StaticFiles,
    Tunable,
    _err,
    _ops_mod,
    _resolve_totp_policy,
    app,
    auth,
    backups,
    config_export,
    db_conn,
    metrics,
    read_version,
    schedules,
    tuning,
    webauthn_h,
)

# `_set_webauthn_register_challenge` / `_consume_webauthn_register_challenge`
# live in main_pkg.scan_routes which loads BEFORE users_routes via the
# chain, so a real import resolves cleanly.
from main_pkg.scan_routes import (  # noqa: E402,F401
    _consume_webauthn_register_challenge,
    _set_webauthn_register_challenge,
)

# `_AVATAR_DIR` / `_request_origin` / `_request_rp_id` are defined in
# main_pkg.auth_routes which loads AFTER users_routes via main's
# chain. A real import here would cycle; TYPE_CHECKING is False at
# runtime so the import silences the IDE without triggering the cycle.
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from main_pkg.auth_routes import (  # noqa: F401
        _AVATAR_DIR,
        _request_origin,
        _request_rp_id,
    )
import json  # noqa: F401,F811
import os  # noqa: F401,F811
import re  # noqa: F401,F811  (used at runtime; star-import shadow flags as duplicate)
import sqlite3  # noqa: F401,F811
import tempfile  # noqa: F401,F811
import time  # noqa: F401,F811  (used at runtime; star-import shadow flags as duplicate)
from typing import Optional, Set

# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).


# Re-import parent's namespace so decorators below find `app`,
# helpers, Pydantic models, etc.

# Re-import parent's namespace so decorators below find every
# symbol from main + main_pkg.hosts_routes.
from main_pkg.hosts_routes import *  # noqa: E402,F401,F403


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
# Registered BEFORE the StaticFiles "/" mount per the project conventions mount-order
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

# Directory-prefix allowances for packages that legitimately need MANY
# files served (an exact-match entry per file would be unmaintainable).
# A request is allowed when its subpath starts with one of these AND
# ends with `.svg` — keeps the served surface to the asset type the SPA
# actually consumes, while the realpath guard below still confines every
# hit to the node_modules root. `flag-icons` ships 271 country-flag SVGs
# (4x3 + 1x1); the Public-IP widget builds the src dynamically from the
# geolocated 2-letter country code, so the exact filename isn't known
# ahead of time.
_NPM_ALLOWED_SVG_PREFIXES: tuple[str, ...] = (
    "flag-icons/flags/",
)


def _npm_path_allowed(subpath: str) -> bool:
    """True when `subpath` is an exact allowlisted file OR a `.svg`
    under an allowed directory prefix (e.g. flag-icons flags)."""
    if subpath in _NPM_ALLOWED:
        return True
    return subpath.endswith(".svg") and subpath.startswith(_NPM_ALLOWED_SVG_PREFIXES)


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
    if ".." in subpath or subpath.startswith("/") or not _npm_path_allowed(subpath):
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


async def serve_app_js_apps_module(name: str = FastApiPath(...)):
    """Serve a per-app SPA module (`static/js/apps/*.js`) with
    `__APP_VERSION__` substitution + no-cache revalidation.

    Why this exists separate from `serve_app_js_module`: the per-app
    modules live in the `apps/` SUBDIRECTORY, so `/js/{name}` (single
    path segment) never matches `/js/apps/<file>` — those requests fell
    through to raw StaticFiles, which does NOT substitute. The result:
    `apps/_registry.js`'s `import './apc.js?v=__APP_VERSION__'` shipped
    the LITERAL token, the browser cached `apc.js?v=__APP_VERSION__` once
    under that constant URL, and EVERY subsequent edit to a per-app module
    (apc.js / speedtest_tracker.js / _registry.js) was invisible to the
    client — frozen in cache forever. Substituting here means each deploy's
    version bump rewrites the import URLs, busting the cache the same way
    the top-level `app-*.js` modules already do. Path-traversal guarded;
    `.js` only.
    """
    js_apps_dir = os.path.join("static", "js", "apps")
    file_path = os.path.realpath(os.path.join(js_apps_dir, name))
    js_apps_root = os.path.realpath(js_apps_dir)
    if file_path != js_apps_root and not file_path.startswith(js_apps_root + os.sep):
        raise HTTPException(404, "Not found")
    if not name.endswith(".js") or not os.path.isfile(file_path):
        raise HTTPException(404, "Not found")
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


app.add_api_route("/js/apps/{name}", serve_app_js_apps_module, methods=["GET"])


# noinspection DuplicatedCode
def __getattr__(name):
    """Module-level resolver for cross-module underscore-prefixed leaks.
    Delegates to the shared helper so the 33-line PEP 562 implementation
    lives in one place. See main_pkg._resolver for the full rationale.
    The 5-line delegator IS duplicated across 12 files — PEP 562 requires
    one __getattr__ per module; suppress the duplicated-code hint."""
    # noinspection PyProtectedMember
    from main_pkg._resolver import resolve
    return resolve(__name__, name)
