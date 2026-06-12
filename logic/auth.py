"""Auth module for OmniGrid.

Two identity sources, one authorization layer:
  - API bearer token (for machine clients; highest precedence)
  - Local session cookie (HMAC-signed, backed by ``sessions`` table)

OIDC SSO users land here as cookie-holders too — the OIDC callback route
(see :mod:`logic.oidc`) calls ``auto_provision_authentik()`` to map the
id_token claims onto a local user record, then mints a normal
``og_session`` cookie. From the middleware's perspective they look
identical to a local login.

All DB-backed settings (Portainer connection, OIDC provider config, the
admin group) follow the same pattern: seed defaults in the ``settings``
table on first boot, cache the values in-process, invalidate the cache
on UI writes. No env-var reads for these settings — env is only used as
a transitional bootstrap for Portainer, and never for OIDC.
"""
import base64
import hmac
import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

import bcrypt
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from logic.env_keys import EnvKey, env_get

# ----------------------------------------------------------------------------
# Config (read once at import)
# ----------------------------------------------------------------------------
SESSION_LIFETIME = 8 * 3600  # 8h hard cap
SESSION_SLIDE_WITHIN = 3600  # re-issue cookie if less than 1h left
# Throttle for the per-request `last_seen_at` write. Pre-fix EVERY
# authenticated request issued an `UPDATE sessions SET last_seen_at`, which
# takes the DB write lock — so the SPA's steady-state polls + the per-host
# fan-out serialized against each other AND the samplers, making clicks feel
# laggy. We now write at most once per this many seconds PER SESSION, tracked
# in the process-local `_LAST_SEEN_WRITE_TS` map (single-replica → a process
# dict is authoritative). `last_seen_at` is a UI nicety (Admin → Sessions
# "last seen"), so minute granularity is plenty. Env-overridable via
# SESSION_LAST_SEEN_THROTTLE_SECONDS; 0 = write on every request (legacy).
# Deliberately NOT a DB-backed tunable: resolving one would issue a SELECT on
# this same per-request hot path, reintroducing the per-request DB I/O this
# change removes. The slide-window write (rare, near expiry) stays
# unconditional. The map holds ~one entry per active cookie session (8h cap),
# so its cardinality is bounded by the live session count.
_LAST_SEEN_THROTTLE_DEFAULT = 60
try:
    _LAST_SEEN_THROTTLE_SECONDS = max(
        0, int(env_get(EnvKey.SESSION_LAST_SEEN_THROTTLE_SECONDS) or _LAST_SEEN_THROTTLE_DEFAULT)
    )
except (TypeError, ValueError):
    _LAST_SEEN_THROTTLE_SECONDS = _LAST_SEEN_THROTTLE_DEFAULT
_LAST_SEEN_WRITE_TS: dict[str, int] = {}
COOKIE_NAME = "og_session"
CSRF_COOKIE = "og_csrf"

SESSION_SECRET_ENV = env_get(EnvKey.SESSION_SECRET)
# Auto-generate an ephemeral secret when one isn't provided so fresh installs
# don't fail to start. Sessions won't survive process restarts in that case —
# operators should set SESSION_SECRET explicitly in prod for persistence.
_AUTO_SECRET = False
if not SESSION_SECRET_ENV:
    SESSION_SECRET_ENV = secrets.token_urlsafe(48)
    _AUTO_SECRET = True
SESSION_SECRET = SESSION_SECRET_ENV.encode()

# Auth settings — DB-backed, UI-managed. Every entry below is the seed
# default used when the `settings` table is empty on first boot. After
# seeding the DB is authoritative; env is NOT consulted on subsequent
# reads. The admin UI writes new values via POST /api/settings;
# invalidate_auth_settings_cache() picks them up on the next request.
#
# `oidc_admin_group` is shared with local users — a user is an admin in
# OmniGrid iff they belong to this Authentik group on OIDC login.
# Local users keep whatever role their admin assigned in the Users UI.
_AUTH_DEFAULTS = {
    # Group name whose members become admin when they sign in via OIDC.
    # Kept editable for homelabs that rename groups.
    "oidc_admin_group": "omnigrid-admins",
    # OIDC provider settings. Everything blank by default — the dashboard
    # works fine without SSO configured; /api/oidc/login just 503s.
    "oidc_enabled": False,
    "oidc_issuer_url": "",
    "oidc_client_id": "",
    "oidc_client_secret": "",
    "oidc_redirect_uri": "",
    "oidc_scopes": "openid email profile groups",
    # TLS verification for calls OmniGrid makes TO the issuer (discovery,
    # JWKS, token exchange). Leave on when the issuer has a publicly-trusted
    # cert; turn OFF for homelab installs behind an internal CA whose root
    # isn't in certifi's bundle. Mirrors the behaviour of Portainer's
    # verify_tls setting.
    "oidc_verify_tls": True,
    # When True (legacy / default), the admin-group claim must match
    # `oidc_admin_group` byte-for-byte. When False, both are lowered
    # before comparison so operators don't have to chase Authentik's
    # mixed-case group names. .
    "oidc_group_case_sensitive": True,
}

# In-memory cache for the three auth-setting values. First read after an
# invalidation hits SQLite; subsequent reads are a plain dict lookup. The
# cache is keyed by the same strings that live in the `settings` table.
_auth_settings_cache: dict = {}
_auth_settings_cache_valid = False


# Rate limit: failed local logins per IP within the window → lockout.
# operator-tunable via Admin → Process tunables. Resolved per
# call (NOT cached at import) so a Save in Admin → Config takes effect
# on the next failed-login attempt without a restart. The historical
# defaults (5 failures / 15 min / 15 min lockout) live in TUNABLES.


def auto_secret_warning() -> Optional[str]:
    """Return a one-line warning string if SESSION_SECRET was auto-generated, else None."""
    if _AUTO_SECRET:
        return ("[auth] SESSION_SECRET not set — generated an ephemeral one. "
                "Local sessions will not survive a restart. Set SESSION_SECRET in prod.")
    return None


def is_session_secret_auto_generated() -> bool:
    """Boolean accessor for the auto-generated state — surfaced on the
    `/api/me` client_config block so the SPA can render a dismissable
    banner in Admin → Authentication for admins. Beats the print-only
    warning (operators routinely don't read container logs) and the
    consequence is severe (every session + every TOTP enrolment dies
    on the next restart)."""
    return _AUTO_SECRET


# ----------------------------------------------------------------------------
# Auth settings (DB-backed, env-seeded)
# ----------------------------------------------------------------------------
_AUTH_SETTING_KEYS = (
    "oidc_admin_group",
    "oidc_enabled",
    "oidc_issuer_url",
    "oidc_client_id",
    "oidc_client_secret",
    "oidc_redirect_uri",
    "oidc_scopes",
    "oidc_verify_tls",
    "oidc_group_case_sensitive",
)


def bootstrap_auth_settings(conn: sqlite3.Connection) -> None:
    """Seed the OIDC / admin-group settings into the ``settings`` table on
    first boot with blank / disabled defaults. No-op for keys that
    already exist — the UI is authoritative after first deploy, so
    operator edits survive restarts.

    Called once from main.py's lifespan handler, after init_db().
    """
    for key in _AUTH_SETTING_KEYS:
        existing = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,),
        ).fetchone()
        if existing is None:
            default = _AUTH_DEFAULTS[key]
            value = "true" if default is True else ("false" if default is False else str(default))
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )


# Bool-typed auth settings — every other key stores its value verbatim as a
# string. Keep this set in sync when adding new boolean settings so the
# cache refresh coerces them back from their stringified form.
_BOOL_AUTH_KEYS = ("oidc_enabled", "oidc_verify_tls", "oidc_group_case_sensitive")


def _refresh_auth_settings_cache(conn: sqlite3.Connection) -> None:
    """Pull every auth-relevant settings row in ONE round-trip and
    repopulate the module-level cache. Called lazily from
    `get_auth_settings` when the cache is invalid (post-write or
    post-restart). Single SELECT keeps the request-path overhead at
    one DB round-trip per N requests rather than one per setting."""
    global _auth_settings_cache, _auth_settings_cache_valid
    # IN (?,?,...) placeholder list built dynamically so this doesn't need
    # a manual edit every time a new auth setting gets added.
    placeholders = ",".join("?" for _ in _AUTH_SETTING_KEYS)
    rows = conn.execute(
        f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
        _AUTH_SETTING_KEYS,
    ).fetchall()
    fresh = {key: _AUTH_DEFAULTS[key] for key in _AUTH_SETTING_KEYS}
    for r in rows:
        key = r["key"]
        raw = r["value"] or ""
        if key in _BOOL_AUTH_KEYS:
            fresh[key] = raw.lower() == "true"
        else:
            fresh[key] = raw
    _auth_settings_cache = fresh
    _auth_settings_cache_valid = True


def get_auth_settings(conn: sqlite3.Connection) -> dict:
    """Return the current auth settings dict (OIDC + admin group).
    Cached in-process; invalidated by UI writes.
    """
    if not _auth_settings_cache_valid:
        _refresh_auth_settings_cache(conn)
    return _auth_settings_cache


def set_auth_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Write one auth setting to the DB. Caller is responsible for calling
    invalidate_auth_settings_cache() after the transaction commits so the
    middleware picks it up on the next request.
    """
    if key not in _AUTH_SETTING_KEYS:
        raise ValueError(f"unknown auth setting: {key}")
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def invalidate_auth_settings_cache() -> None:
    """Drop the in-process auth-settings cache so the next read pulls
    fresh values from the DB. Call after any `set_auth_setting` write
    (or any out-of-band SQL UPDATE) so a UI change takes effect on
    the very next request."""
    global _auth_settings_cache_valid
    _auth_settings_cache_valid = False


# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------
# noinspection DuplicatedCode
def init_auth_schema(conn: sqlite3.Connection) -> None:
    """Idempotent CREATE TABLE / CREATE INDEX for the auth tables —
    `users` / `sessions` / `api_tokens` / `user_credentials`. Safe to
    re-run on every boot — schema changes here MUST stay additive
    (CREATE TABLE IF NOT EXISTS, ALTER ADD COLUMN). Non-additive
    changes (renames, type changes, data migrations) belong in
    `logic/migrations.py` instead."""
    conn.executescript("""
                       CREATE TABLE IF NOT EXISTS users
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           username
                           TEXT
                           UNIQUE
                           NOT
                           NULL,
                           email
                           TEXT,
                           password_hash
                           TEXT,
                           role
                           TEXT
                           NOT
                           NULL
                           CHECK (
                           role
                           IN
                       (
                           'admin',
                           'readonly'
                       )),
                           auth_source TEXT NOT NULL CHECK
                       (
                           auth_source
                           IN
                       (
                           'local',
                           'authentik'
                       )),
                           disabled INTEGER NOT NULL DEFAULT 0,
                           created_at INTEGER NOT NULL,
                           last_login_at INTEGER
                           );
                       CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

                       CREATE TABLE IF NOT EXISTS sessions
                       (
                           token_id
                           TEXT
                           PRIMARY
                           KEY,
                           user_id
                           INTEGER
                           NOT
                           NULL
                           REFERENCES
                           users
                       (
                           id
                       ),
                           issued_at INTEGER NOT NULL,
                           last_seen_at INTEGER NOT NULL,
                           expires_at INTEGER NOT NULL,
                           ip TEXT,
                           user_agent TEXT
                           );
                       CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                       CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

                       CREATE TABLE IF NOT EXISTS api_tokens
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           name
                           TEXT
                           UNIQUE
                           NOT
                           NULL,
                           token_hash
                           TEXT
                           NOT
                           NULL,
                           role
                           TEXT
                           NOT
                           NULL
                           CHECK (
                           role
                           IN
                       (
                           'admin',
                           'readonly'
                       )),
                           created_at INTEGER NOT NULL,
                           last_used_at INTEGER,
                           created_by INTEGER REFERENCES users
                       (
                           id
                       )
                           );

                       -- WebAuthn / FIDO2 passkey credentials. One row per
                       -- enrolled key; users can have multiple. credential_id is the
                       -- raw bytes returned by the authenticator (NOT base64-encoded
                       -- here; the API layer encodes for the wire). public_key is the
                       -- COSE-encoded public-key blob that verify_authentication_response
                       -- needs as input. sign_count is monotonic per-key (cloned
                       -- authenticator detection).  transports is a CSV of WebAuthn
                       -- transport hints (`usb`, `nfc`, `ble`, `internal`, `hybrid`).
                       -- friendly_name is operator-supplied (e.g. "YubiKey 5C"); empty
                       -- means the SPA renders a default. last_used_at is wallclock of
                       -- the most recent successful login assertion -- powers the
                       -- Profile UI's "last used 3h ago" hint. CASCADE on user delete.
                       CREATE TABLE IF NOT EXISTS user_credentials
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           user_id
                           INTEGER
                           NOT
                           NULL
                           REFERENCES
                           users
                       (
                           id
                       ),
                           credential_id BLOB NOT NULL UNIQUE,
                           public_key BLOB NOT NULL,
                           sign_count INTEGER NOT NULL DEFAULT 0,
                           transports TEXT NOT NULL DEFAULT '',
                           friendly_name TEXT NOT NULL DEFAULT '',
                           created_at INTEGER NOT NULL,
                           last_used_at INTEGER
                           );
                       CREATE INDEX IF NOT EXISTS idx_user_credentials_user
                           ON user_credentials(user_id);
                       """)
    # Idempotent column additions for existing deployments. SQLite pre-3.35
    # has no "ADD COLUMN IF NOT EXISTS", so we catch the OperationalError
    # that gets raised when the column already exists. Safe to re-run.
    for ddl in (
            "ALTER TABLE users ADD COLUMN display_name TEXT",
            "ALTER TABLE users ADD COLUMN bio TEXT",
            "ALTER TABLE users ADD COLUMN avatar_path TEXT",
            # Per-user UI preferences. JSON blob for cross-device
            # sync of toggles like headerWeatherEnabled / headerClockEnabled
            # that previously lived only in browser localStorage. Server
            # is the source of truth; localStorage is a fast-path cache.
            # Default '{}' so existing rows hydrate to "no overrides" and
            # the SPA falls back to its own per-toggle defaults.
            "ALTER TABLE users ADD COLUMN ui_prefs TEXT DEFAULT '{}'",
            # TOTP-based 2FA. Five additive columns; secret + backup
            # codes are Fernet-encrypted at rest (see logic/totp.py). Authentik
            # users never set these fields -- their IdP handles MFA. Lockout
            # state mirrors the per-IP rate-limit pattern but is per-user.
            "ALTER TABLE users ADD COLUMN totp_secret_encrypted TEXT",
            "ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN totp_backup_codes_json TEXT",
            "ALTER TABLE users ADD COLUMN totp_failed_attempts INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN totp_locked_until INTEGER",
            # Per-user force-2FA override. Admin-only flag that
            # overrides the global totp_required_for_admins / _users
            # policy: when 1, this specific user MUST have 2FA on, even
            # if the global policy doesn't require it for their role.
            # Authentik users still skip — auth_source='local' gate
            # short-circuits TOTP everywhere.
            "ALTER TABLE users ADD COLUMN totp_force_required INTEGER NOT NULL DEFAULT 0",
            # Per-session auth-method tag. Surfaces in Admin → Sessions
            # so the operator can see at a glance which factor each active
            # session was authenticated with: 'password' (single-factor
            # local), 'totp' (local + TOTP code), 'passkey' (local +
            # WebAuthn assertion), 'oidc' (Authentik SSO), 'bootstrap'
            # (one-shot first-admin seed). Existing rows keep the default
            # 'password' which is the right behaviour for any session
            # minted before this column existed (pre-2FA era).
            "ALTER TABLE sessions ADD COLUMN auth_method TEXT NOT NULL DEFAULT 'password'",
            # Per-credential RP-ID. Stamped at registration from the
            # request's effective hostname so we can detect "credential
            # registered under a different domain" at login time and surface
            # a clearer error than the browser's silent QR fallback. Existing
            # rows default to '' — read-side treats blank as "unknown"
            # (assume current rp_id matches; mismatch detection only fires
            # on rows that have an explicit non-empty value). Operators
            # who migrate domains between this rollout and the first
            # re-enrolment will see a no-op for the legacy rows; only NEW
            # registrations after this column lands carry the marker.
            "ALTER TABLE user_credentials ADD COLUMN rp_id TEXT NOT NULL DEFAULT ''",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass


# ----------------------------------------------------------------------------
# User model + CRUD
# ----------------------------------------------------------------------------
@dataclass
class User:
    """In-memory user record returned by every auth-resolution path.
    Mirrors the `users` table 1:1 plus a few TOTP / passkey-related
    flags. Pure data class — no behaviour, no DB binding."""
    id: int
    username: str
    email: Optional[str]
    role: str
    auth_source: str
    disabled: bool
    # TOTP-based 2FA. ``totp_enabled`` is the canonical "is 2FA
    # active for this user" flag. The encrypted secret is NEVER on the
    # User dataclass -- callers fetch via get_user_totp_secret to reduce
    # the surface where it could leak into a serialised payload.
    totp_enabled: bool = False
    # Per-user force-2FA override. Defaults False so existing
    # User constructions don't need updating.
    totp_force_required: bool = False


def _row_to_user(r: sqlite3.Row) -> User:
    """Convert a `users`-table SQLite row to a :class:`User` dataclass.
    Defensively probes TOTP / WebAuthn columns added by later
    migrations via try/except so pre-migration rows don't crash the
    converter (every callable reads through this single shim)."""
    # Older rows (pre-fix / pre-fix) won't have these columns; sqlite3.Row's
    # keys work like dict keys, so probe via Index lookup with a try/except.
    try:
        totp_on = bool(r["totp_enabled"])
    except (KeyError, IndexError):
        totp_on = False
    try:
        totp_forced = bool(r["totp_force_required"])
    except (KeyError, IndexError):
        totp_forced = False
    return User(
        id=r["id"], username=r["username"], email=r["email"],
        role=r["role"], auth_source=r["auth_source"], disabled=bool(r["disabled"]),
        totp_enabled=totp_on,
        totp_force_required=totp_forced,
    )


def hash_password(pw: str) -> str:
    """bcrypt-hash a plaintext password with cost 12 (the OmniGrid
    standard — matches the WebAuthn-era enrolment + every reset path).
    Returns the standard `$2b$...` ASCII string for storage."""
    # bcrypt.gensalt defaults to rounds=12 — drop the explicit kwarg.
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, stored_hash: Optional[str]) -> bool:
    """Constant-time bcrypt compare. Returns False when `stored_hash`
    is None / empty (account has no password — passkey-only / SSO)
    or when bcrypt rejects the hash for shape reasons. Never raises."""
    if not stored_hash:
        return False
    try:
        return bcrypt.checkpw(pw.encode(), stored_hash.encode())
    except (ValueError, TypeError):
        return False


def count_users(conn: sqlite3.Connection) -> int:
    """Total user rows including disabled. Used by the bootstrap-path
    gate (`POST /api/local-auth/bootstrap` 403s when this > 0)."""
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_user(conn: sqlite3.Connection, user_id: int) -> Optional[User]:
    """Lookup one user by primary key; returns None on miss. `_row_to_user`
    handles the SQLite row → dataclass shape."""
    r = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _row_to_user(r) if r else None


def get_user_by_username(conn: sqlite3.Connection, username: str) -> Optional[User]:
    """Case-insensitive username lookup.

    SQLite's default ``=`` is binary, so without ``COLLATE NOCASE`` an
    operator who created the account as ``Admin`` couldn't log in by
    typing ``admin``. We compare in a case-insensitive way at the
    query layer (no schema migration required) — the stored username
    keeps its original case for display. The UNIQUE constraint on
    ``users.username`` is still binary, but pre-existing rows are
    distinct by case in practice; future creates also get folded
    against existing rows via this same helper, so a duplicate ``ADMIN``
    can't be created when ``admin`` already exists.
    """
    r = conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
        (username,),
    ).fetchone()
    return _row_to_user(r) if r else None


def get_user_by_email(conn: sqlite3.Connection, email: str) -> Optional[User]:
    """Lookup one user by email (case-sensitive). Returns None on
    miss. Used by the OIDC auto-provision path to detect an existing
    local-auth user whose email matches the IdP's claim."""
    r = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    return _row_to_user(r) if r else None


def create_user(
    conn: sqlite3.Connection,
    username: str,
    email: Optional[str],
    password: Optional[str],
    role: str,
    auth_source: str,
) -> User:
    """Insert a new user row + return the populated :class:`User`.
    `role` MUST be `"admin"` or `"readonly"`; `auth_source` MUST be
    `"local"` or `"authentik"` — any other value raises ValueError.
    `password=None` is the canonical "SSO / passkey-only" shape; the
    `password_hash` column stays NULL and `verify_password` returns
    False for that account."""
    if role not in ("admin", "readonly"):
        raise ValueError(f"invalid role: {role}")
    if auth_source not in ("local", "authentik"):
        raise ValueError(f"invalid auth_source: {auth_source}")
    pw_hash = hash_password(password) if password else None
    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO users(username,email,password_hash,role,auth_source,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (username, email, pw_hash, role, auth_source, now),
    )
    return get_user(conn, cur.lastrowid)  # type: ignore[return-value]


def touch_last_login(conn: sqlite3.Connection, user_id: int) -> None:
    """Stamp `last_login_at` on the user row. Called from every
    successful login path (local / OIDC / passkey / TOTP) so the
    Admin → Users table reflects activity recency."""
    conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (int(time.time()), user_id))


def change_password(
    conn: sqlite3.Connection,
    user_id: int,
    new_password: str,
    keep_session_token: Optional[str] = None,
) -> None:
    """Rotate a local user's password hash and invalidate every other session.

    Authentik users have no password_hash — callers must check
    auth_source before invoking this. Only meaningful for local accounts.

    Session invalidation on change is the standard defense: if an attacker
    had a session, rotating the password should kick them. The caller's
    own session is preserved (via keep_session_token) so the user doesn't
    have to re-login immediately after a password change from the profile UI.
    """
    conn.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (hash_password(new_password), user_id),
    )
    if keep_session_token:
        conn.execute(
            "DELETE FROM sessions WHERE user_id=? AND token_id<>?",
            (user_id, keep_session_token),
        )
    else:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def list_users(conn: sqlite3.Connection) -> list[dict]:
    """Return every user as a dict for the admin UI.

    Includes ``totp_enabled`` (rendered as the 2FA column's on/off pill)
    and ``totp_force_required`` (the per-user policy override
    that flips the pill to "Required" + enables the Force/Unforce
    button). The encrypted secret + backup codes are deliberately NOT
    returned — they never need to leave the server.

    Adds ``passkey_count`` so the admin view shows how many
    WebAuthn credentials each user has enrolled. Cheap aggregate join
    -- single SQL statement, indexed on ``user_credentials(user_id)``.
    """
    rows = conn.execute("""
                        SELECT u.id,
                               u.username,
                               u.email,
                               u.role,
                               u.auth_source,
                               u.disabled,
                               u.created_at,
                               u.last_login_at,
                               u.totp_enabled,
                               u.totp_force_required,
                               (SELECT COUNT(*)
                                FROM user_credentials c
                                WHERE c.user_id = u.id) AS passkey_count
                        FROM users u
                        ORDER BY u.username COLLATE NOCASE
                        """).fetchall()
    return [dict(r) for r in rows]


def set_user_totp_force_required(conn: sqlite3.Connection, user_id: int, force: bool) -> None:
    """Admin-only: flip the per-user force-2FA flag.

    No effect on Authentik users — the call sites guard on auth_source
    upstream so we don't need to re-check here. Doesn't touch any other
    TOTP state; the user's existing secret / backup codes stay intact
    when the force is toggled OFF.
    """
    conn.execute(
        "UPDATE users SET totp_force_required=? WHERE id=?",
        (1 if force else 0, user_id),
    )


def count_active_admins(conn: sqlite3.Connection) -> int:
    """Used as a guard against demoting or disabling the last active admin."""
    return conn.execute(
        "SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0"
    ).fetchone()[0]


def set_user_role(conn: sqlite3.Connection, user_id: int, role: str) -> None:
    """Promote / demote a user. `role` MUST be `"admin"` or `"readonly"`;
    any other value raises ValueError. Callers are responsible for the
    "last-active-admin" guard via `count_active_admins`."""
    if role not in ("admin", "readonly"):
        raise ValueError(f"invalid role: {role}")
    conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    # `role` is in the cached profile dict — drop it so the demoted /
    # promoted user's next /api/me reflects the change immediately rather
    # than after the 5s TTL (the read-through-cache defence-in-depth hook).
    invalidate_user_profile(user_id)


def set_user_disabled(conn: sqlite3.Connection, user_id: int, disabled: bool) -> None:
    """Flip the disabled flag. Disabling ALSO deletes every active
    session for the user — a disabled account whose cookie still
    worked wouldn't really be disabled."""
    conn.execute(
        "UPDATE users SET disabled=? WHERE id=?",
        (1 if disabled else 0, user_id),
    )
    # `disabled` is in the cached profile dict — drop it so the change is
    # read-through immediately (the docstring's defence-in-depth promise).
    invalidate_user_profile(user_id)
    if disabled:
        delete_user_sessions(conn, user_id)


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    """Remove a user and cascade sessions + null-out api-token ownership.

    SQLite doesn't enforce the REFERENCES clause without PRAGMA
    foreign_keys=ON, so we do the cascade manually. api_tokens keep
    working (just lose the "created_by" backpointer) — revoking the
    token is a separate admin action. Passkeys cascade because
    they're identity material — leaving them dangling would let a
    recycled user_id silently inherit them.
    """
    delete_user_sessions(conn, user_id)
    conn.execute("UPDATE api_tokens SET created_by=NULL WHERE created_by=?", (user_id,))
    delete_all_user_credentials(conn, user_id)
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))


# Per-process short-TTL cache for get_user_profile. /api/me is called
# on every page load, every auth-state change, every cross-tab SSE
# settings:updated handler, and every cross-tab tab:activity heartbeat
# — on a busy multi-tab session that's dozens of calls per minute,
# each hitting a fresh SQLite connection + JSON parse of ui_prefs.
# Cache TTL is intentionally short (5s) so operator edits propagate
# quickly without explicit invalidation on every mutating path; the
# explicit invalidate_user_profile(user_id) hook below is defence-
# in-depth for sites that need immediate read-through (admin password
# reset, role change, disable). Single-process single-replica → plain
# module dict is correct.
_user_profile_cache: dict[int, tuple[float, dict]] = {}
_USER_PROFILE_CACHE_TTL_SECONDS = 5.0


def invalidate_user_profile(user_id: Optional[int] = None) -> None:
    """Drop a cached user-profile row so the next get_user_profile reads
    fresh from the DB. Pass ``None`` to clear every cached entry — used
    after admin bulk-edit paths (e.g. password reset on N users) where
    enumerating individual ids is wasted work."""
    if user_id is None:
        _user_profile_cache.clear()
    else:
        _user_profile_cache.pop(user_id, None)


def get_user_profile(conn: sqlite3.Connection, user_id: int) -> Optional[dict]:
    """Full profile row (all columns) as a dict — used by /api/me and the
    profile page. Returns None for missing users.

    `ui_prefs` is parsed from JSON into a dict; defaults to `{}` for
    rows where it's NULL or invalid (older deployments before the
    column was added, or DB rows hand-tampered).
    """
    cached = _user_profile_cache.get(user_id)
    now = time.time()
    if cached is not None and (now - cached[0]) < _USER_PROFILE_CACHE_TTL_SECONDS:
        return cached[1]
    r = conn.execute("""
                     SELECT id,
                            username,
                            email,
                            role,
                            auth_source,
                            disabled,
                            created_at,
                            last_login_at,
                            display_name,
                            bio,
                            avatar_path,
                            ui_prefs,
                            totp_enabled
                     FROM users
                     WHERE id = ?
                     """, (user_id,)).fetchone()
    if not r:
        # Negative result NOT cached — a freshly-created user should be
        # visible on the very next /api/me, not after the TTL.
        return None
    out = dict(r)
    raw = out.pop("ui_prefs", None)
    try:
        import json as _json
        prefs = _json.loads(raw) if raw else {}
        if not isinstance(prefs, dict):
            prefs = {}
    except (ValueError, TypeError):
        prefs = {}
    out["ui_prefs"] = prefs
    _user_profile_cache[user_id] = (now, out)
    return out


def _load_user_ui_prefs(conn: sqlite3.Connection, user_id: int) -> dict:
    """Read + parse the ui_prefs JSON blob for one user.

    Raises ``LookupError`` when the user doesn't exist. Returns an
    empty dict when the column is NULL / blank / malformed (defensive
    — JSON parse errors are tolerated since the column is operator-
    written and a malformed write shouldn't permanently brick the
    pref-store). Shared by ``update_ui_prefs`` + ``set_user_notify_prefs``
    so the lookup-validate-parse prelude lives in one place rather
    than being copy-pasted (was an 11-line duplicate before
    extraction)."""
    import json as _json
    row = conn.execute(
        "SELECT ui_prefs FROM users WHERE id=?", (user_id,),
    ).fetchone()
    if not row:
        raise LookupError(f"user {user_id} not found")
    try:
        cur = _json.loads(row["ui_prefs"]) if row["ui_prefs"] else {}
        if not isinstance(cur, dict):
            cur = {}
    except (ValueError, TypeError):
        cur = {}
    return cur


# Defence-in-depth cap on the single `users.ui_prefs` TEXT column. It's a
# shared blob — ai_conversation (50-turn capped) + apps_custom_layout
# (bounded by app count) + assorted toggles — none individually validated
# for size. 256 KiB is far above any legitimate prefs payload; the cap just
# stops a tampered / misbehaving client bloating the row unbounded.
_UI_PREFS_MAX_BYTES = 256 * 1024


def update_ui_prefs(
    conn: sqlite3.Connection,
    user_id: int,
    new_prefs: dict,
) -> dict:
    """Merge `new_prefs` into the user's stored ui_prefs, write back, and
    return the merged result.

    Last-write-wins on key collisions — clients PATCH the partial dict
    they want to change. To delete a pref, send the value `None` (the
    merge drops null values so the dict stays compact). Validation is
    intentionally lenient — these are UI toggles, not security state.

    Raises `ValueError` when the merged blob would exceed
    ``_UI_PREFS_MAX_BYTES`` (the routes translate that to HTTP 413).
    """
    import json as _json
    if not isinstance(new_prefs, dict):
        raise ValueError("ui_prefs payload must be a dict")
    cur = _load_user_ui_prefs(conn, user_id)
    merged = dict(cur)
    for k, v in new_prefs.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    merged_json = _json.dumps(merged)
    size = len(merged_json.encode())
    if size > _UI_PREFS_MAX_BYTES:
        raise ValueError(
            "ui_prefs payload too large (%d bytes > %d cap)"
            % (size, _UI_PREFS_MAX_BYTES)
        )
    conn.execute(
        "UPDATE users SET ui_prefs=? WHERE id=?",
        (merged_json, user_id),
    )
    # Drop the cached profile row so the next /api/me reads the
    # fresh ui_prefs without waiting for the TTL window.
    invalidate_user_profile(user_id)
    return merged


# noinspection DuplicatedCode
def get_user_notify_prefs(conn: sqlite3.Connection, user_id: int) -> dict:
    """Return the per-user notification opt-in map.

    Stored as a top-level ``notify_events`` key inside ``users.ui_prefs``
    (no new column — keeps the schema-migration footprint zero). Returns
    an empty dict when the user has never made a per-event choice; the
    caller resolves missing keys against the admin defaults.

    Per-medium granularity: each event's value is EITHER a bare ``bool``
    (legacy "enabled across every globally-enabled medium") OR a dict
    ``{medium: bool}`` for per-medium routing (e.g. success-events to
    Apprise only, failures to In-app only, combinations). Both shapes
    coexist freely — the dispatcher in ``logic/ops.py:notify`` accepts
    either. Legacy stored bools survive untouched until the user re-saves
    the Profile → Notifications panel, at which point the SPA round-trips
    them through the per-medium shape.
    """
    import json as _json
    row = conn.execute(
        "SELECT ui_prefs FROM users WHERE id=?", (user_id,),
    ).fetchone()
    if not row:
        return {}
    try:
        prefs = _json.loads(row["ui_prefs"]) if row["ui_prefs"] else {}
        if not isinstance(prefs, dict):
            return {}
    except (ValueError, TypeError):
        return {}
    raw = prefs.get("notify_events")
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for k, v in raw.items():
        if isinstance(v, bool):
            out[str(k)] = v
        elif isinstance(v, dict):
            # Per-medium dict — keep only bool values (defensive against
            # a future tampered ui_prefs blob with non-bool sub-values).
            cleaned_med = {str(mk): bool(mv) for mk, mv in v.items() if isinstance(mv, bool)}
            out[str(k)] = cleaned_med
        # Skip any other shape (str, list, None) — equivalent to "no
        # explicit choice for this event".
    return out


def set_user_notify_prefs(
    conn: sqlite3.Connection, user_id: int, prefs: dict,
) -> dict:
    """Replace the user's ``notify_events`` map with ``prefs`` (read-
    modify-write that preserves every other key in ``ui_prefs``).

    ``prefs`` accepts a flat ``{event_name: <bool|dict>}`` map. Each
    value is either a bare ``bool`` (legacy "enabled across every
    medium") or a ``{medium: bool}`` dict for per-medium routing.
    Mixed shapes coexist — the SPA may persist some events as dicts and
    others as legacy bools without confusing the dispatcher.

    Returns the persisted map so the caller can echo it in the response.
    """
    import json as _json
    if not isinstance(prefs, dict):
        raise ValueError("notify prefs payload must be a dict")
    cur = _load_user_ui_prefs(conn, user_id)
    clean: dict = {}
    for k, v in prefs.items():
        ks = str(k)
        if isinstance(v, bool):
            clean[ks] = v
        elif isinstance(v, dict):
            cleaned_med = {str(mk): bool(mv) for mk, mv in v.items() if isinstance(mv, bool)}
            # Drop empty dicts — equivalent to "no explicit choice"
            # so we don't pollute storage with empty objects on edge
            # cases (e.g. SPA submits {} when every medium box is
            # cleared then re-checked back to defaults).
            if cleaned_med:
                clean[ks] = cleaned_med
        # Other shapes are dropped — same as "no explicit choice".
    cur["notify_events"] = clean
    conn.execute(
        "UPDATE users SET ui_prefs=? WHERE id=?",
        (_json.dumps(cur), user_id),
    )
    invalidate_user_profile(user_id)
    return clean


def update_user_profile(
    conn: sqlite3.Connection,
    user_id: int,
    display_name: Optional[str] = None,
    bio: Optional[str] = None,
    email: Optional[str] = None,
) -> None:
    """Update display_name / bio / email on the user's own profile.

    Each field is independently optional — None means "don't touch". Empty
    string clears the field. Caller enforces whatever length / validation
    rules apply; this helper just writes what it's given.
    """
    fields: list[str] = []
    values: list = []
    if display_name is not None:
        fields.append("display_name=?")
        values.append(display_name or None)
    if bio is not None:
        fields.append("bio=?")
        values.append(bio or None)
    if email is not None:
        fields.append("email=?")
        values.append(email or None)
    if not fields:
        return
    values.append(user_id)
    conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=?", values)
    invalidate_user_profile(user_id)


def set_user_avatar_path(
    conn: sqlite3.Connection, user_id: int, path: Optional[str],
) -> None:
    """Store the relative avatar path (under /app/data/avatars/) or clear it.

    Path is the basename only — the filesystem directory is owned by main.py.
    Callers pass None to clear.
    """
    conn.execute("UPDATE users SET avatar_path=? WHERE id=?", (path, user_id))
    invalidate_user_profile(user_id)


def admin_reset_password(
    conn: sqlite3.Connection, user_id: int, new_password: str,
) -> None:
    """Overwrite a local user's password from the admin UI. Unlike
    change_password, no current-password check — the acting admin already
    has that authority. Invalidates every session for the target user.

    ALSO clears any TOTP enrolment AND every passkey :
    operators reset passwords when a user has lost access; that
    usually means their authenticator device is gone too. The user
    re-enrols via Profile after the next login if 2FA is still
    required by policy.
    """
    conn.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (hash_password(new_password), user_id),
    )
    clear_user_totp(conn, user_id)
    delete_all_user_credentials(conn, user_id)
    delete_user_sessions(conn, user_id)


# ----------------------------------------------------------------------------
# TOTP (2FA) helpers
# ----------------------------------------------------------------------------
def get_user_totp_secret(
    conn: sqlite3.Connection, user_id: int,
) -> Optional[str]:
    """Return the at-rest-encrypted secret blob (or None).

    Decryption happens at the call site via logic.totp.decrypt_secret —
    this helper deliberately returns the raw ciphertext so a leaky log
    or accidental serialisation can't expose plaintext.
    """
    r = conn.execute(
        "SELECT totp_secret_encrypted FROM users WHERE id=?", (user_id,),
    ).fetchone()
    if not r:
        return None
    return r["totp_secret_encrypted"]


def get_user_totp_state(
    conn: sqlite3.Connection, user_id: int,
) -> dict:
    """Return per-user 2FA state for login + admin views.

    Shape: ``{enabled, has_backup_codes, failed_attempts, locked_until,
    backup_codes_json}``. ``backup_codes_json`` is the raw stored blob
    (encrypted). Caller decrypts via logic.totp.decrypt_backup_codes.
    """
    r = conn.execute(
        "SELECT totp_enabled, totp_backup_codes_json, "
        "totp_failed_attempts, totp_locked_until "
        "FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    if not r:
        return {
            "enabled": False, "has_backup_codes": False,
            "failed_attempts": 0, "locked_until": None,
            "backup_codes_json": None,
        }
    raw = r["totp_backup_codes_json"]
    return {
        "enabled": bool(r["totp_enabled"]),
        "has_backup_codes": bool(raw),
        "failed_attempts": int(r["totp_failed_attempts"] or 0),
        "locked_until": r["totp_locked_until"],
        "backup_codes_json": raw,
    }


def set_user_totp_secret(
    conn: sqlite3.Connection,
    user_id: int,
    encrypted_secret: str,
    encrypted_backup_codes_json: str,
) -> None:
    """Persist a fresh enrolment. Resets lockout state."""
    conn.execute(
        "UPDATE users SET "
        "  totp_secret_encrypted=?, "
        "  totp_enabled=1, "
        "  totp_backup_codes_json=?, "
        "  totp_failed_attempts=0, "
        "  totp_locked_until=NULL "
        "WHERE id=?",
        (encrypted_secret, encrypted_backup_codes_json, user_id),
    )


def update_user_totp_backup_codes(
    conn: sqlite3.Connection, user_id: int, encrypted_backup_codes_json: str,
) -> None:
    """Replace just the backup-codes blob (used after consuming one OR
    when the user regenerates the set)."""
    conn.execute(
        "UPDATE users SET totp_backup_codes_json=? WHERE id=?",
        (encrypted_backup_codes_json, user_id),
    )


def clear_user_totp(conn: sqlite3.Connection, user_id: int) -> None:
    """Blank every TOTP column. Used by self-disable + admin override +
    the password-reset cascade."""
    conn.execute(
        "UPDATE users SET "
        "  totp_secret_encrypted=NULL, "
        "  totp_enabled=0, "
        "  totp_backup_codes_json=NULL, "
        "  totp_failed_attempts=0, "
        "  totp_locked_until=NULL "
        "WHERE id=?",
        (user_id,),
    )


def record_totp_failure(
    conn: sqlite3.Connection,
    user_id: int,
    max_failures: int,
    lockout_seconds: int,
) -> tuple[int, Optional[int]]:
    """Increment failure counter, lock if threshold hit.

    Returns ``(new_failure_count, locked_until_or_None)``. Caller
    surfaces the lockout in a 423 response so the SPA can render
    a "try again in N minutes" message.
    """
    state = get_user_totp_state(conn, user_id)
    n = (state["failed_attempts"] or 0) + 1
    locked_until = None
    if n >= max(1, max_failures):
        locked_until = int(time.time()) + max(60, lockout_seconds)
    conn.execute(
        "UPDATE users SET "
        "  totp_failed_attempts=?, "
        "  totp_locked_until=? "
        "WHERE id=?",
        (n, locked_until, user_id),
    )
    return n, locked_until


def clear_totp_lockout(conn: sqlite3.Connection, user_id: int) -> None:
    """Reset the TOTP failure counter + clear the lockout timestamp.
    Called on a successful TOTP / backup-code / passkey verify so a
    user who just got back in doesn't carry forward stale strikes."""
    conn.execute(
        "UPDATE users SET totp_failed_attempts=0, totp_locked_until=NULL "
        "WHERE id=?",
        (user_id,),
    )


# ----------------------------------------------------------------------------
# WebAuthn / FIDO2 passkey credentials
# ----------------------------------------------------------------------------
def list_user_credentials(
    conn: sqlite3.Connection, user_id: int,
) -> list[dict]:
    """Return every passkey enrolled for ``user_id``.

    Each row is shaped ``{id, credential_id (bytes), transports
    (list[str]), friendly_name, created_at, last_used_at, sign_count}``.
    The public_key is omitted -- it never leaves the server. The login
    flow fetches the public_key separately via
    ``get_credential_by_credential_id`` after a verified assertion.
    """
    rows = conn.execute(
        "SELECT id, credential_id, transports, friendly_name, "
        "created_at, last_used_at, sign_count, rp_id "
        "FROM user_credentials WHERE user_id=? "
        "ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        ts = (r["transports"] or "").strip()
        out.append({
            "id": r["id"],
            "credential_id": bytes(r["credential_id"]),
            "transports": [t for t in ts.split(",") if t],
            "friendly_name": r["friendly_name"] or "",
            "created_at": r["created_at"],
            "last_used_at": r["last_used_at"],
            "sign_count": int(r["sign_count"] or 0),
            # empty string means "registered before the column
            # was added" (no rp_id stamp); read-side treats blank as
            # "unknown — assume current rp_id matches" so legacy rows
            # don't trigger spurious mismatch banners.
            "rp_id": (r["rp_id"] or "") if "rp_id" in r.keys() else "",
        })
    return out


def count_user_credentials(
    conn: sqlite3.Connection, user_id: int,
) -> int:
    """Cheap "does the user have any passkey?" check used by the login
    multi-step gate to decide whether to advertise the webauthn method.
    """
    r = conn.execute(
        "SELECT COUNT(*) FROM user_credentials WHERE user_id=?",
        (user_id,),
    ).fetchone()
    return int(r[0] if r else 0)


def get_credential_by_credential_id(
    conn: sqlite3.Connection, credential_id: bytes,
) -> Optional[dict]:
    """Look up a credential row by the authenticator-supplied
    credential_id (bytes).

    Returns the full row including ``public_key`` and ``sign_count``
    so the caller can run ``verify_authentication_response`` against
    the stored key. Returns None when no row matches (the caller
    surfaces this as "credential unknown" -> 401).
    """
    r = conn.execute(
        "SELECT id, user_id, credential_id, public_key, sign_count, "
        "transports, friendly_name, created_at, last_used_at, rp_id "
        "FROM user_credentials WHERE credential_id=?",
        (credential_id,),
    ).fetchone()
    if not r:
        return None
    ts = (r["transports"] or "").strip()
    # rp_id added so the helper stays in sync with the schema
    # post-fix's additive ALTER. Login-finish doesn't currently consume
    # the field (expected_rp_id comes from the challenge dict), but a
    # future caller wanting to detect "credential matches login but not
    # current page" should read this rather than a separate query.
    return {
        "id": r["id"],
        "user_id": r["user_id"],
        "credential_id": bytes(r["credential_id"]),
        "public_key": bytes(r["public_key"]),
        "sign_count": int(r["sign_count"] or 0),
        "transports": [t for t in ts.split(",") if t],
        "friendly_name": r["friendly_name"] or "",
        "created_at": r["created_at"],
        "last_used_at": r["last_used_at"],
        "rp_id": (r["rp_id"] or "").strip(),
    }


def add_user_credential(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    credential_id: bytes,
    public_key: bytes,
    sign_count: int,
    transports: list[str],
    friendly_name: str,
    rp_id: str = "",
) -> int:
    """Persist a new passkey.

    Returns the inserted row id. Raises ``sqlite3.IntegrityError`` on
    duplicate ``credential_id`` (covered by the UNIQUE constraint --
    the WebAuthn excludeCredentials list ALSO catches this in the
    browser, but a malicious / quirky client could still POST a
    duplicate). The caller maps this to 409 Conflict.

    ``rp_id`` — the effective hostname the credential was
    registered under, copied from the request's ``request.url.hostname``
    (or X-Forwarded-Host) at registration time. Stored so login can
    detect "credential registered under a different domain" and
    surface a clearer error than the browser's silent QR fallback.
    """
    transports_csv = ",".join(
        sorted({(t or "").strip().lower() for t in transports if t})
    )
    cur = conn.execute(
        "INSERT INTO user_credentials("
        "  user_id, credential_id, public_key, sign_count, "
        "  transports, friendly_name, created_at, rp_id"
        ") VALUES (?,?,?,?,?,?,?,?)",
        (
            user_id, credential_id, public_key, int(sign_count),
            transports_csv, friendly_name or "",
            int(time.time()),
            (rp_id or "").strip().lower(),
        ),
    )
    return int(cur.lastrowid or 0)


def update_credential_after_use(
    conn: sqlite3.Connection,
    credential_row_id: int,
    new_sign_count: int,
) -> None:
    """Bump ``sign_count`` + stamp ``last_used_at`` after a successful
    authentication assertion.
    """
    conn.execute(
        "UPDATE user_credentials SET sign_count=?, last_used_at=? "
        "WHERE id=?",
        (int(new_sign_count), int(time.time()), credential_row_id),
    )


def delete_user_credential(
    conn: sqlite3.Connection, user_id: int, credential_row_id: int,
) -> bool:
    """Revoke ONE passkey. Returns True if a row was deleted, False if
    the (user_id, credential_row_id) pair didn't match (404 path at
    the route layer).

    The user_id filter prevents user A from revoking user B's keys via
    ID guessing -- routes resolve credential_row_id from the URL but
    the user_id from the session.
    """
    cur = conn.execute(
        "DELETE FROM user_credentials WHERE id=? AND user_id=?",
        (credential_row_id, user_id),
    )
    return (cur.rowcount or 0) > 0


def delete_all_user_credentials(
    conn: sqlite3.Connection, user_id: int,
) -> int:
    """Wipe every passkey for a user. Used by the user-delete cascade
    AND by the admin password-reset path (which already cascades TOTP
    -- a lost device usually means lost passkey too).

    Returns the number of rows removed (callers may want to surface
    "N keys revoked" in audit logs).
    """
    cur = conn.execute(
        "DELETE FROM user_credentials WHERE user_id=?",
        (user_id,),
    )
    return cur.rowcount or 0


def list_sessions(conn: sqlite3.Connection) -> list[dict]:
    """Active (non-expired) sessions with usernames resolved for display."""
    rows = conn.execute("""
                        SELECT s.token_id,
                               s.user_id,
                               u.username,
                               s.issued_at,
                               s.last_seen_at,
                               s.expires_at,
                               s.ip,
                               s.user_agent,
                               s.auth_method
                        FROM sessions s
                                 LEFT JOIN users u ON u.id = s.user_id
                        WHERE s.expires_at > ?
                        ORDER BY s.last_seen_at DESC
                        """, (int(time.time()),)).fetchall()
    return [dict(r) for r in rows]


def list_api_tokens(conn: sqlite3.Connection) -> list[dict]:
    """Every API token with the creator's username (if still present)."""
    rows = conn.execute("""
                        SELECT t.id,
                               t.name,
                               t.role,
                               t.created_at,
                               t.last_used_at,
                               u.username AS created_by_username
                        FROM api_tokens t
                                 LEFT JOIN users u ON u.id = t.created_by
                        ORDER BY t.name COLLATE NOCASE
                        """).fetchall()
    return [dict(r) for r in rows]


def delete_api_token(conn: sqlite3.Connection, token_id: int) -> None:
    """Revoke a bearer token by primary key. Subsequent requests with
    that token resolve to no user (the middleware returns 401)."""
    conn.execute("DELETE FROM api_tokens WHERE id=?", (token_id,))


def auto_provision_authentik(
    conn: sqlite3.Connection,
    email: str,
    username: Optional[str],
    groups: list[str],
) -> User:
    """Find or create an SSO user, refreshing role from the group claim.

    Group membership is authoritative every time: if the user is in the
    configured admin group they become admin; otherwise readonly. Removing
    someone from the group in Authentik demotes them on the next OIDC
    login. Local users aren't touched by this path.

    Name retained for historical reasons — any OIDC IdP fits the same
    shape (email + username + list-of-groups claim).
    """
    settings = get_auth_settings(conn)
    admin_group = settings.get("oidc_admin_group", "")
    case_sensitive = bool(settings.get("oidc_group_case_sensitive", True))
    if admin_group:
        if case_sensitive:
            in_admin_group = admin_group in (groups or [])
        else:
            needle = admin_group.lower()
            in_admin_group = needle in [str(g).lower() for g in (groups or [])]
    else:
        in_admin_group = False
    target_role = "admin" if in_admin_group else "readonly"
    # Only look up an existing AUTHENTIK-sourced user by this email. Local
    # accounts sharing the same email MUST NOT be matched here — otherwise
    # we'd silently flip their auth_source to 'authentik' and the local
    # username/password login path (which gates on auth_source='local')
    # would start rejecting correct credentials with "Invalid username or
    # password". Email is not a unique column in the users table; both a
    # local and an SSO record can coexist cleanly.
    row = conn.execute(
        "SELECT * FROM users WHERE email=? AND auth_source='authentik' LIMIT 1",
        (email,),
    ).fetchone()
    u = _row_to_user(row) if row else None
    if u is None:
        # Username collisions with a local user get a suffix so we never
        # conflate identities. Email is the real key for Authentik users.
        # First check the bare username; on collision use a random 4-digit
        # suffix instead of linear `#2`/`#3`/... probing.
        # Linear probing was O(N) — three local users named `alice`,
        # `alice#2`, `alice#3` cost a fresh Authentik `alice` four DB
        # round-trips. Random suffix is O(1) in expectation; bounded retry
        # against the 1-in-9000 collision case.
        uname = username or email
        base = uname
        if get_user_by_username(conn, uname) is not None:
            import random
            for _ in range(8):  # 8 random tries before falling back
                candidate = f"{base}#{random.randint(1000, 9999)}"
                if get_user_by_username(conn, candidate) is None:
                    uname = candidate
                    break
            else:
                # Statistically unreachable on homelab fleets, but keep
                # the linear-probe escape hatch so a fully-saturated
                # 9000-user namespace still lands somewhere unique.
                n = 1
                uname = f"{base}#{n}"
                while get_user_by_username(conn, uname) is not None:
                    n += 1
                    uname = f"{base}#{n}"
        u = create_user(conn, uname, email, None, target_role, "authentik")
        return u
    if u.role != target_role:
        conn.execute(
            "UPDATE users SET role=? WHERE id=?",
            (target_role, u.id),
        )
        u.role = target_role
    return u


# ----------------------------------------------------------------------------
# Session cookies (HMAC-signed, server-side record for revocation)
# ----------------------------------------------------------------------------
def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _sign(token_id: str, expires_at: int) -> str:
    """HMAC-SHA256 the `(token_id, expires_at)` payload with
    `SESSION_SECRET` → base64url string. Used as the cookie's
    tamper-resistance signature; recomputed + compared via
    `hmac.compare_digest` on every request."""
    msg = f"{token_id}.{expires_at}".encode()
    sig = hmac.new(SESSION_SECRET, msg, hashlib.sha256).digest()
    return _b64e(sig)


def issue_session_cookie(token_id: str, expires_at: int) -> str:
    """Compose the tri-part cookie value: `<token_id>.<expires_at>.<sig>`.
    Caller stamps as the `og_session` cookie via `set_session_cookie`."""
    return f"{token_id}.{expires_at}.{_sign(token_id, expires_at)}"


def parse_session_cookie(cookie: str) -> Optional[str]:
    """Return token_id if signature valid and not expired, else None."""
    try:
        token_id, expires_s, sig = cookie.split(".", 2)
        expires_at = int(expires_s)
    except (ValueError, AttributeError):
        return None
    if expires_at <= int(time.time()):
        return None
    expected = _sign(token_id, expires_at)
    if not hmac.compare_digest(sig, expected):
        return None
    return token_id


def create_session(
    conn: sqlite3.Connection,
    user_id: int,
    ip: Optional[str],
    user_agent: Optional[str],
    auth_method: str = "password",
) -> tuple[str, int]:
    """Create a new session row and return (cookie_value, expires_at).

    ``auth_method`` tags the session with the factor used to authenticate
    so Admin → Sessions can show "password" / "totp" / "passkey" /
    "oidc" / "bootstrap" per row. Default is "password" so
    callers that don't care (legacy paths) get the safe value without
    needing to be touched.
    """
    token_id = secrets.token_urlsafe(24)
    now = int(time.time())
    expires_at = now + SESSION_LIFETIME
    conn.execute(
        "INSERT INTO sessions(token_id,user_id,issued_at,last_seen_at,expires_at,ip,user_agent,auth_method) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (token_id, user_id, now, now, expires_at, ip, user_agent, auth_method),
    )
    # Diagnostic line so the operator can verify auth_method propagation
    # in Admin → Logs without inspecting the SQLite directly. The
    # session_method column on Admin → Sessions reads from the same
    # row this print line attests to.
    print(f"[auth] session created user_id={user_id} method={auth_method!r} ip={ip!r}")
    return issue_session_cookie(token_id, expires_at), expires_at


def get_active_session(conn: sqlite3.Connection, token_id: str) -> Optional[sqlite3.Row]:
    """Return the session row when it exists AND hasn't expired, else
    None. The expiry check is server-side so a stale cookie that
    survived a clock-skew or `expires_at` rollback can't resurrect a
    revoked session."""
    r = conn.execute(
        "SELECT * FROM sessions WHERE token_id=? AND expires_at>?",
        (token_id, int(time.time())),
    ).fetchone()
    return r


def slide_session_if_needed(
    conn: sqlite3.Connection, token_id: str, current_expires_at: int
) -> Optional[tuple[str, int]]:
    """If within SESSION_SLIDE_WITHIN of expiry, extend by SESSION_LIFETIME.

    Returns (new_cookie_value, new_expires_at) when a reissue happened, else None.
    """
    now = int(time.time())
    if current_expires_at - now > SESSION_SLIDE_WITHIN:
        # Throttle the last_seen_at write to at most once per
        # _LAST_SEEN_THROTTLE_SECONDS per session (see the constant's
        # comment). Skipping it just leaves last_seen_at up to the throttle
        # window stale, which is fine for the UI. 0 = write every request.
        last_write = _LAST_SEEN_WRITE_TS.get(token_id, 0)
        if _LAST_SEEN_THROTTLE_SECONDS <= 0 or (now - last_write) >= _LAST_SEEN_THROTTLE_SECONDS:
            conn.execute("UPDATE sessions SET last_seen_at=? WHERE token_id=?", (now, token_id))
            _LAST_SEEN_WRITE_TS[token_id] = now
            # Bound the map's memory — drop entries for sessions older than
            # the hard lifetime cap (expired / revoked) when it grows large.
            if len(_LAST_SEEN_WRITE_TS) > 4096:
                cutoff = now - SESSION_LIFETIME
                for tid in [k for k, v in _LAST_SEEN_WRITE_TS.items() if v < cutoff]:
                    del _LAST_SEEN_WRITE_TS[tid]
        return None
    new_expires_at = now + SESSION_LIFETIME
    conn.execute(
        "UPDATE sessions SET last_seen_at=?, expires_at=? WHERE token_id=?",
        (now, new_expires_at, token_id),
    )
    # Keep the throttle map consistent — we just wrote last_seen_at here, so
    # the next common-path request won't immediately re-write it.
    _LAST_SEEN_WRITE_TS[token_id] = now
    # publish a session:renewed event so the SPA tab
    # can update its "session expires in X" tooltip in real time
    # without polling. Best-effort: never let a publish failure block
    # the slide. Resolve the user_id from the session row so the SPA
    # can filter to "is this MY session?" (operators rarely have two
    # tabs as different users on the same browser, but worth doing
    # right).
    try:
        from logic import events as _events
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE token_id=?", (token_id,),
        ).fetchone()
        user_id = row["user_id"] if row else None
        _events.publish("session:renewed", {
            "user_id": user_id,
            "expires_at": new_expires_at,
            "ts": now,
        })
    except Exception as e:
        print(f"[auth] session:renewed publish failed: {e}")
    return issue_session_cookie(token_id, new_expires_at), new_expires_at


def delete_session(conn: sqlite3.Connection, token_id: str) -> None:
    """Revoke one session by its token_id. Caller's cookie keeps the
    value but every subsequent request resolves to no user."""
    conn.execute("DELETE FROM sessions WHERE token_id=?", (token_id,))


def delete_user_sessions(conn: sqlite3.Connection, user_id: int) -> None:
    """Revoke EVERY active session for one user. Called from disable,
    role-demote, password-reset, force-2FA-enrol, and any other
    "log them all out" path."""
    conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


# ----------------------------------------------------------------------------
# API tokens (SHA-256 at rest; raw shown once on create)
# ----------------------------------------------------------------------------
def _hash_token(raw: str) -> str:
    """SHA-256 hex digest of the raw token string — stored at rest so
    the DB never carries the plaintext token. The raw token is
    surfaced exactly ONCE on create (one-time reveal modal); after
    that only the hash is queryable."""
    return hashlib.sha256(raw.encode()).hexdigest()


def create_api_token(
    conn: sqlite3.Connection, name: str, role: str, created_by: Optional[int]
) -> str:
    """Mint a new `og_...` bearer token. Returns the raw token EXACTLY
    ONCE — the caller (POST /api/tokens) surfaces it in a one-time
    reveal modal. The DB stores only the SHA-256 hash. `role` must be
    `"admin"` or `"readonly"`; any other value raises ValueError."""
    if role not in ("admin", "readonly"):
        raise ValueError(f"invalid role: {role}")
    raw = "og_" + secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO api_tokens(name,token_hash,role,created_at,created_by) "
        "VALUES (?,?,?,?,?)",
        (name, _hash_token(raw), role, int(time.time()), created_by),
    )
    return raw


def verify_api_token(conn: sqlite3.Connection, raw: str) -> Optional[dict]:
    """Look up a bearer token by hash. Returns `{id, name, role}` on
    match, None on miss. Constant-time enough for our threat model
    (the lookup is keyed on the hash, not the plaintext)."""
    r = conn.execute(
        "SELECT id,name,role FROM api_tokens WHERE token_hash=?",
        (_hash_token(raw),),
    ).fetchone()
    if not r:
        return None
    conn.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (int(time.time()), r["id"]))
    return {"id": r["id"], "name": r["name"], "role": r["role"]}


# ----------------------------------------------------------------------------
# Login rate limiting (in-memory — single-replica deploy)
# ----------------------------------------------------------------------------
# Bucket shape — keep concrete so pyright can narrow `rec[...]` reads
# at every call site. The three field types stay stable: failures is
# always an int counter, window_start + locked_until are float epoch
# seconds (`time.time()` is float).
_login_attempts: dict[str, dict[str, float]] = {}


def _username_key(ip: str, username: Optional[str]) -> Optional[str]:
    """Composite (ip, username) bucket key — None when username isn't
    known yet (e.g. /api/local-auth/login pre-form-parse)."""
    if not username:
        return None
    return f"{ip}|{(username or '').lower().strip()}"


def rate_limit_check(ip: str, username: Optional[str] = None) -> None:
    """Raise 429 if THIS IP or the (ip, username) tuple is locked out.

    pre-fix the limiter keyed solely on IP, so a
    single corporate-NAT'd office got locked out for ANY user's typo.
    Now both buckets are checked; lockout fires when either trips.
    """
    now = time.time()
    keys = [ip]
    uk = _username_key(ip, username)
    if uk:
        keys.append(uk)
    for k in keys:
        rec = _login_attempts.get(k)
        if not rec:
            continue
        locked_until = float(rec.get("locked_until", 0.0))
        if locked_until > now:
            retry = int(locked_until - now)
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed logins. Try again in {retry}s.",
                headers={"Retry-After": str(retry)},
            )


# noinspection DuplicatedCode
def rate_limit_record_failure(ip: str, username: Optional[str] = None) -> None:
    """Increment both the IP bucket AND the (ip, username) bucket.
    Lockouts on either bucket independently — same window + threshold."""
    now = time.time()
    keys = [ip]
    uk = _username_key(ip, username)
    if uk:
        keys.append(uk)
    # resolve per-call so a Save in Admin → Config takes effect
    # immediately without a restart. tuning_int caches via the
    # auth-settings cache, so this is sub-microsecond per call.
    from logic import tuning as _tuning
    from logic.tuning import Tunable
    window = _tuning.tuning_int(Tunable.RATE_LIMIT_WINDOW_SECONDS)
    max_failures = _tuning.tuning_int(Tunable.RATE_LIMIT_MAX_FAILURES)
    lockout = _tuning.tuning_int(Tunable.RATE_LIMIT_LOCKOUT_SECONDS)
    for k in keys:
        rec = _login_attempts.get(k) or {"failures": 0, "window_start": now, "locked_until": 0.0}
        # Roll the window if the oldest failure is beyond the window.
        if now - rec["window_start"] > window:
            rec = {"failures": 0, "window_start": now, "locked_until": 0.0}
        rec["failures"] += 1
        if rec["failures"] >= max_failures:
            rec["locked_until"] = now + lockout
        _login_attempts[k] = rec


def rate_limit_clear(ip: str, username: Optional[str] = None) -> None:
    """Clear BOTH buckets on success so a successful login resets the
    user's bucket too — otherwise an attacker who knows the username
    could keep tripping the (ip, username) bucket from a clean IP."""
    _login_attempts.pop(ip, None)
    uk = _username_key(ip, username)
    if uk:
        _login_attempts.pop(uk, None)


# ----------------------------------------------------------------------------
# Middleware + deps (step-3 enforcement)
# ----------------------------------------------------------------------------
# Classification is deliberately coarse:
# - Everything NOT under /api/ is fully public. That covers the SPA shell,
#   the login page, every static asset, vendor bundles, images, CSS. The
#   SPA handles its own redirect to /login via /api/me, so there's no
#   need for the middleware to gate HTML/CSS/JS.
# - Paths under /api/ split into two groups:
#     * public: /api/healthz, /api/version, /metrics (scrape)
#     * auth-optional: /api/local-auth/*, /api/me — user is resolved so
#       handlers can behave differently when logged in, but no rejection
#       if the request is unauthenticated.
#     * everything else: 401 on missing identity.
PUBLIC_API_PATHS = frozenset({"/api/healthz", "/api/version", "/metrics"})

AUTH_OPTIONAL_API_PREFIXES = (
    "/api/local-auth/",  # login / logout / bootstrap
    "/api/me",  # identity introspection — must return
    # {authenticated: false} rather than 401
    "/api/oidc/",  # OIDC login/callback — the whole point is that
    # the caller isn't authenticated yet when they
    # start the flow. Callback must also be
    # reachable anonymously (the browser follows
    # Authentik's 302 back to us without any
    # OmniGrid cookie).
    "/api/auth/providers",  # advertises which SSO paths are enabled — the
    # login page queries this before rendering the
    # SSO button.
)


def _is_fully_public(path: str) -> bool:
    """True when the path bypasses identity resolution entirely (static
    assets / SPA shell / `/login` / explicit public-API allowlist).
    The middleware short-circuits BEFORE touching the DB on these paths."""
    if not path.startswith("/api/") and path != "/metrics":
        # Every non-API path is public. Static assets (CSS, JS, images,
        # vendor bundles), the SPA shell, and the /login HTML page all
        # reach the StaticFiles mount or their dedicated route without
        # any identity lookup.
        return True
    return path in PUBLIC_API_PATHS


def _is_auth_optional(path: str) -> bool:
    """True for paths that RESOLVE identity (so handlers can read
    `request.state.user`) but DON'T 401 when none is found. The
    `/api/me` + `/api/local-auth/*` + `/api/oidc/*` family lives here
    so the SPA can ask "am I logged in?" without crashing."""
    return any(path.startswith(p) for p in AUTH_OPTIONAL_API_PREFIXES)


def client_ip(request: Request) -> str:
    """Resolve the client IP — left-most `X-Forwarded-For` entry when the
    reverse proxy (NPM) injected it, otherwise the direct socket peer.
    Returns "?" when neither is available (rare — only synthetic test
    requests with no `request.client` set).
    """
    # NPM sets X-Forwarded-For; take the left-most entry (original client).
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    client = request.client
    if client is None:
        return "?"
    return client.host


# Back-compat alias for callers that import the underscore-prefixed
# legacy name (`auth._client_ip`). New code uses `client_ip` directly.
_client_ip = client_ip


def _resolve_user(request: Request, db_conn_factory) -> tuple[Optional[User], Optional[tuple[str, int]]]:
    """Try each identity source in priority order.

    Returns (user, session_reissue). session_reissue is (cookie_value, expires_at)
    when a sliding-window reissue happened, so the caller can set the cookie.

    OIDC SSO users arrive here via the standard ``og_session`` cookie —
    the OIDC callback mints one after validating the id_token, so the
    middleware doesn't need a dedicated branch for them.
    """
    # 1. API bearer token (highest precedence — machine clients)
    auth_h = request.headers.get("authorization", "")
    if auth_h.startswith("Bearer "):
        raw = auth_h[7:].strip()
        # defensive try/except so a transient SQLite
        # BUSY / OperationalError doesn't escape the middleware as a
        # 500. Treat any DB failure as "auth failed" — the caller's
        # request gets a clean 401 (or proceeds anonymously if the
        # path is auth-optional). Mirrors the cookie branch's pattern
        # via `get_active_session`'s exception swallowing below.
        try:
            with db_conn_factory() as c:
                tok = verify_api_token(c, raw)
        except Exception as e:
            print(f"[auth] bearer-token DB read failed: {e} — treating as auth-failed")
            tok = None
        if tok:
            return (
                User(
                    id=-tok["id"],  # negative marker so it can't collide with a real user id
                    username=f"token:{tok['name']}",
                    email=None,
                    role=tok["role"],
                    auth_source="local",
                    disabled=False,
                ),
                None,
            )

    # 2. Local session cookie (covers local logins AND OIDC SSO users)
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        token_id = parse_session_cookie(cookie)
        if token_id:
            with db_conn_factory() as c:
                sess = get_active_session(c, token_id)
                if sess:
                    u = get_user(c, sess["user_id"])
                    if u and not u.disabled:
                        reissue = slide_session_if_needed(c, token_id, sess["expires_at"])
                        return u, reissue
                    # Stale (user deleted/disabled): drop session.
                    delete_session(c, token_id)

    return None, None


def make_auth_middleware(db_conn_factory):
    """Build the ASGI middleware with an injected DB-connection factory.

    Passed in rather than imported to avoid a circular import with main.py,
    which owns `db_conn()`.
    """

    async def auth_middleware(request: Request, call_next):
        """ASGI middleware — resolve identity (Bearer / session cookie),
        gate non-public `/api/*` paths on auth, attach `request.state.user`
        for downstream handlers, reissue sliding-window cookies, enforce
        CSRF on cookie-authed write methods, and auto-issue the CSRF
        cookie when missing."""
        path = request.url.path
        if _is_fully_public(path):
            return await call_next(request)
        user, reissue = _resolve_user(request, db_conn_factory)
        request.state.user = user
        # Auth enforcement: missing identity on a non-optional /api path → 401.
        # Auth-optional paths (/api/me, /api/local-auth/*) still run so the
        # SPA can ask "am I logged in?" and handlers can behave per-caller.
        if user is None and not _is_auth_optional(path):
            return JSONResponse(
                {"detail": "Authentication required"}, status_code=401,
            )
        # CSRF enforcement (step 6): double-submit cookie on state-changing
        # methods for cookie-authed callers. Bearer tokens don't use
        # cookies, so cross-origin attackers can't forge them → exempt.
        # Auth-optional endpoints (login/logout/bootstrap) are exempt
        # because they run before the user has a CSRF cookie in the first
        # place; CSRF on logout is a non-issue (attacker can log you out
        # but not do anything as you).
        if (
            request.method in ("POST", "PUT", "PATCH", "DELETE")
            and user is not None
            and not _is_auth_optional(path)
            and not _is_bearer_request(request)
            # Beacon endpoints — `navigator.sendBeacon` cannot set
            # custom headers, so the X-CSRF-Token double-submit
            # defence breaks. Beacon paths are restricted to the
            # caller's OWN ui_prefs (/api/me/ui-prefs/beacon) so a
            # cross-site attacker forging a beacon can only ever
            # mutate their own session-cookie owner's prefs — same
            # capability the cookie already grants. Document new
            # beacon endpoints here when added so the CSRF gate
            # stays explicit about the bypass.
            and path != "/api/me/ui-prefs/beacon"
        ):
            header = request.headers.get("x-csrf-token", "")
            cookie = request.cookies.get(CSRF_COOKIE, "")
            if not header or not cookie or not hmac.compare_digest(header, cookie):
                return JSONResponse(
                    {"detail": "CSRF token mismatch"}, status_code=403,
                )
        response = await call_next(request)
        if reissue is not None:
            cookie_value, expires_at = reissue
            set_session_cookie(response, cookie_value, expires_at, request)
        # Issue an og_csrf cookie when an authed caller doesn't already have
        # one — covers any edge case where the cookie got cleared (local
        # login and the OIDC callback both mint one themselves). Stable-
        # per-browser: we just need it to match what the client sends
        # back as X-CSRF-Token (double-submit defense).
        if (
            user is not None
            and not _is_bearer_request(request)
            and not request.cookies.get(CSRF_COOKIE)
        ):
            set_csrf_cookie(
                response,
                generate_csrf_token(),
                int(time.time()) + SESSION_LIFETIME,
                request,
            )
        return response

    return auth_middleware


def _is_bearer_request(request: Request) -> bool:
    """True when the request carries an `Authorization: Bearer ...`
    header. Used to short-circuit the CSRF enforcement (bearer auth
    can't be CSRF'd — no cookie was used)."""
    return request.headers.get("authorization", "").startswith("Bearer ")


def current_user(request: Request) -> User:
    """FastAPI dep: require any authenticated user. 401 otherwise."""
    user: Optional[User] = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_admin(request: Request) -> User:
    """FastAPI dep: require admin role. 403 otherwise."""
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


# ----------------------------------------------------------------------------
# CSRF (double-submit cookie)
# ----------------------------------------------------------------------------
def generate_csrf_token() -> str:
    """Return a fresh URL-safe CSRF token (24 random bytes → 32 chars).
    Caller stamps it as the double-submit cookie + sends back via the
    `X-CSRF-Token` header on state-changing requests."""
    return secrets.token_urlsafe(24)


def require_csrf(request: Request) -> None:
    """FastAPI dep for state-changing routes (browser cookie auth path only).

    Bearer-token requests bypass this because they aren't reachable via CSRF.
    """
    # Bearer tokens aren't CSRF-vulnerable — no cookie was used for auth.
    if request.headers.get("authorization", "").startswith("Bearer "):
        return
    header = request.headers.get("x-csrf-token", "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not header or not cookie or not hmac.compare_digest(header, cookie):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")


# ----------------------------------------------------------------------------
# Cookie helpers
# ----------------------------------------------------------------------------
def _is_https(request: Request) -> bool:
    """True when the original client request was HTTPS. Reads
    `X-Forwarded-Proto` from the reverse proxy (NPM terminates TLS so
    the inner request scheme is `http`) AND falls back to the inline
    URL scheme for direct-attach setups. Drives the `Secure` cookie
    flag in `set_session_cookie` / `set_csrf_cookie`."""
    proto = request.headers.get("x-forwarded-proto", "").lower()
    return proto == "https" or request.url.scheme == "https"


def set_session_cookie(response, cookie_value: str, expires_at: int, request: Request) -> None:
    """Stamp the session cookie on `response`. `httponly=True` so JS
    can't read it; `secure` derived from the reverse-proxy
    `X-Forwarded-Proto` header so dev (HTTP) works without TLS while
    prod (HTTPS via NPM) gets the strict flag."""
    max_age = max(0, expires_at - int(time.time()))
    response.set_cookie(
        key=COOKIE_NAME,
        value=cookie_value,
        max_age=max_age,
        httponly=True,
        secure=_is_https(request),
        samesite="lax",
        path="/",
    )


def set_csrf_cookie(response, token: str, expires_at: int, request: Request) -> None:
    """Stamp the CSRF double-submit cookie on `response`. `httponly=False`
    is INTENTIONAL — JS reads the value to attach it as the
    `X-CSRF-Token` header on state-changing requests. Token rotation
    matches the session cookie's expiry so both refresh together."""
    max_age = max(0, expires_at - int(time.time()))
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        max_age=max_age,
        httponly=False,
        secure=_is_https(request),
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response, _request: Request) -> None:
    """Delete the session + CSRF cookies on logout / forced-revoke
    paths. `_request` is accepted for caller-shape parity (the matching
    `issue_session_cookies` reads from request to derive Secure/Domain
    attributes); we don't read it here but keep it for symmetry."""
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
