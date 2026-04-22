"""Auth module for PortaUpdate.

Three identity sources, one authorization layer:
  - Authentik Forward Auth header (X-Authentik-Email + group claim)
  - Local session cookie (HMAC-signed, backed by `sessions` table)
  - API bearer token (for machine clients)

Step-1 mode: the middleware only populates request.state.user — it never
rejects. Step-2 will gate write routes via `require_admin`; step-3 will
gate everything via `require_user`.
"""
import base64
import hmac
import hashlib
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

import bcrypt
from fastapi import HTTPException, Request


# ----------------------------------------------------------------------------
# Config (read once at import)
# ----------------------------------------------------------------------------
SESSION_LIFETIME = 8 * 3600          # 8h hard cap
SESSION_SLIDE_WITHIN = 3600          # re-issue cookie if less than 1h left
COOKIE_NAME = "pu_session"
CSRF_COOKIE = "pu_csrf"

SESSION_SECRET_ENV = os.getenv("SESSION_SECRET", "")
# Auto-generate an ephemeral secret when one isn't provided so fresh installs
# don't fail to start. Sessions won't survive process restarts in that case —
# operators should set SESSION_SECRET explicitly in prod for persistence.
_AUTO_SECRET = False
if not SESSION_SECRET_ENV:
    SESSION_SECRET_ENV = secrets.token_urlsafe(48)
    _AUTO_SECRET = True
SESSION_SECRET = SESSION_SECRET_ENV.encode("utf-8")

NPM_AUTH_SECRET = os.getenv("NPM_AUTH_SECRET", "")
AUTHENTIK_ENABLED = os.getenv("AUTHENTIK_ENABLED", "false").lower() == "true"
AUTHENTIK_ADMIN_GROUP = os.getenv("AUTHENTIK_ADMIN_GROUP", "portaupdate-admins")

# Rate limit: 5 failed local logins per IP within the window → 15-minute lockout.
RATE_LIMIT_MAX_FAILURES = 5
RATE_LIMIT_WINDOW = 15 * 60
RATE_LIMIT_LOCKOUT = 15 * 60


def auto_secret_warning() -> Optional[str]:
    """Return a one-line warning string if SESSION_SECRET was auto-generated, else None."""
    if _AUTO_SECRET:
        return ("[auth] SESSION_SECRET not set — generated an ephemeral one. "
                "Local sessions will not survive a restart. Set SESSION_SECRET in prod.")
    return None


# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------
def init_auth_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT,
        password_hash TEXT,
        role TEXT NOT NULL CHECK(role IN ('admin','readonly')),
        auth_source TEXT NOT NULL CHECK(auth_source IN ('local','authentik')),
        disabled INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        last_login_at INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

    CREATE TABLE IF NOT EXISTS sessions (
        token_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        issued_at INTEGER NOT NULL,
        last_seen_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        ip TEXT,
        user_agent TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

    CREATE TABLE IF NOT EXISTS api_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        token_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin','readonly')),
        created_at INTEGER NOT NULL,
        last_used_at INTEGER,
        created_by INTEGER REFERENCES users(id)
    );
    """)


# ----------------------------------------------------------------------------
# User model + CRUD
# ----------------------------------------------------------------------------
@dataclass
class User:
    id: int
    username: str
    email: Optional[str]
    role: str
    auth_source: str
    disabled: bool


def _row_to_user(r: sqlite3.Row) -> User:
    return User(
        id=r["id"], username=r["username"], email=r["email"],
        role=r["role"], auth_source=r["auth_source"], disabled=bool(r["disabled"]),
    )


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(pw: str, stored_hash: Optional[str]) -> bool:
    if not stored_hash:
        return False
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), stored_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def count_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_user(conn: sqlite3.Connection, user_id: int) -> Optional[User]:
    r = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _row_to_user(r) if r else None


def get_user_by_username(conn: sqlite3.Connection, username: str) -> Optional[User]:
    r = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return _row_to_user(r) if r else None


def get_user_by_email(conn: sqlite3.Connection, email: str) -> Optional[User]:
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
    conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (int(time.time()), user_id))


def auto_provision_authentik(
    conn: sqlite3.Connection,
    email: str,
    username: Optional[str],
    groups: list[str],
) -> User:
    """Find or create the Authentik user, refreshing role from group claim.

    Group membership is authoritative every time: if the user is in
    AUTHENTIK_ADMIN_GROUP they become admin; otherwise readonly. This means
    removing someone from the group in Authentik demotes them on next request.
    Local users aren't touched by this path.
    """
    target_role = "admin" if AUTHENTIK_ADMIN_GROUP in (groups or []) else "readonly"
    u = get_user_by_email(conn, email)
    if u is None:
        # Username collisions with a local user get a suffix so we never
        # conflate identities. Email is the real key for Authentik users.
        uname = username or email
        base = uname
        n = 1
        while get_user_by_username(conn, uname) is not None:
            n += 1
            uname = f"{base}#{n}"
        u = create_user(conn, uname, email, None, target_role, "authentik")
        return u
    if u.auth_source != "authentik" or u.role != target_role:
        conn.execute(
            "UPDATE users SET auth_source='authentik', role=? WHERE id=?",
            (target_role, u.id),
        )
        u.role = target_role
        u.auth_source = "authentik"
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
    msg = f"{token_id}.{expires_at}".encode("utf-8")
    sig = hmac.new(SESSION_SECRET, msg, hashlib.sha256).digest()
    return _b64e(sig)


def issue_session_cookie(token_id: str, expires_at: int) -> str:
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
) -> tuple[str, int]:
    """Create a new session row and return (cookie_value, expires_at)."""
    token_id = secrets.token_urlsafe(24)
    now = int(time.time())
    expires_at = now + SESSION_LIFETIME
    conn.execute(
        "INSERT INTO sessions(token_id,user_id,issued_at,last_seen_at,expires_at,ip,user_agent) "
        "VALUES (?,?,?,?,?,?,?)",
        (token_id, user_id, now, now, expires_at, ip, user_agent),
    )
    return issue_session_cookie(token_id, expires_at), expires_at


def get_active_session(conn: sqlite3.Connection, token_id: str) -> Optional[sqlite3.Row]:
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
        conn.execute("UPDATE sessions SET last_seen_at=? WHERE token_id=?", (now, token_id))
        return None
    new_expires_at = now + SESSION_LIFETIME
    conn.execute(
        "UPDATE sessions SET last_seen_at=?, expires_at=? WHERE token_id=?",
        (now, new_expires_at, token_id),
    )
    return issue_session_cookie(token_id, new_expires_at), new_expires_at


def delete_session(conn: sqlite3.Connection, token_id: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_id=?", (token_id,))


def delete_user_sessions(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


# ----------------------------------------------------------------------------
# API tokens (SHA-256 at rest; raw shown once on create)
# ----------------------------------------------------------------------------
def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_api_token(
    conn: sqlite3.Connection, name: str, role: str, created_by: Optional[int]
) -> str:
    if role not in ("admin", "readonly"):
        raise ValueError(f"invalid role: {role}")
    raw = "pu_" + secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO api_tokens(name,token_hash,role,created_at,created_by) "
        "VALUES (?,?,?,?,?)",
        (name, _hash_token(raw), role, int(time.time()), created_by),
    )
    return raw


def verify_api_token(conn: sqlite3.Connection, raw: str) -> Optional[dict]:
    r = conn.execute(
        "SELECT id,name,role FROM api_tokens WHERE token_hash=?",
        (_hash_token(raw),),
    ).fetchone()
    if not r:
        return None
    conn.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (int(time.time()), r["id"]))
    return {"id": r["id"], "name": r["name"], "role": r["role"]}


# ----------------------------------------------------------------------------
# Login rate limiting (in-memory — single-replica deploy, see CLAUDE.md)
# ----------------------------------------------------------------------------
_login_attempts: dict[str, dict] = {}


def rate_limit_check(ip: str) -> None:
    """Raise 429 if this IP is locked out."""
    rec = _login_attempts.get(ip)
    if not rec:
        return
    if rec.get("locked_until", 0) > time.time():
        retry = int(rec["locked_until"] - time.time())
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed logins. Try again in {retry}s.",
            headers={"Retry-After": str(retry)},
        )


def rate_limit_record_failure(ip: str) -> None:
    now = time.time()
    rec = _login_attempts.get(ip) or {"failures": 0, "window_start": now, "locked_until": 0.0}
    # Roll the window if the oldest failure is beyond RATE_LIMIT_WINDOW.
    if now - rec["window_start"] > RATE_LIMIT_WINDOW:
        rec = {"failures": 0, "window_start": now, "locked_until": 0.0}
    rec["failures"] += 1
    if rec["failures"] >= RATE_LIMIT_MAX_FAILURES:
        rec["locked_until"] = now + RATE_LIMIT_LOCKOUT
    _login_attempts[ip] = rec


def rate_limit_clear(ip: str) -> None:
    _login_attempts.pop(ip, None)


# ----------------------------------------------------------------------------
# Middleware + deps (observe mode for step 1)
# ----------------------------------------------------------------------------
# Paths that never run through identity resolution. Keep this narrow —
# everything else goes through the middleware so the identity is available
# even when a route chooses not to enforce.
PUBLIC_PATH_PREFIXES = (
    "/metrics",
    "/api/healthz",
    "/api/local-auth/",
    "/login",
    "/static/",
    "/vendor/",
    "/img/",
    "/favicon",
    "/icon-",
)


def _is_public(path: str) -> bool:
    if path in ("/", "/index.html"):
        # The SPA shell itself stays public in observe mode; step-3 will gate it.
        return True
    return any(path.startswith(p) for p in PUBLIC_PATH_PREFIXES)


def _client_ip(request: Request) -> str:
    # NPM sets X-Forwarded-For; take the left-most entry (original client).
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _authentik_trusted(request: Request) -> bool:
    """Only trust X-Authentik-* headers when NPM forwarded them with our secret."""
    if not (AUTHENTIK_ENABLED and NPM_AUTH_SECRET):
        return False
    return hmac.compare_digest(
        request.headers.get("x-forward-auth-verify", ""),
        NPM_AUTH_SECRET,
    )


def _resolve_user(request: Request, db_conn_factory) -> tuple[Optional[User], Optional[tuple[str, int]]]:
    """Try each identity source in priority order.

    Returns (user, session_reissue). session_reissue is (cookie_value, expires_at)
    when a sliding-window reissue happened, so the caller can set the cookie.
    """
    # 1. Authentik Forward Auth header (highest trust when NPM verifies it)
    if _authentik_trusted(request):
        email = request.headers.get("x-authentik-email")
        if email:
            username = request.headers.get("x-authentik-username")
            groups_raw = request.headers.get("x-authentik-groups", "")
            groups = [g.strip() for g in groups_raw.split("|") if g.strip()]
            with db_conn_factory() as c:
                u = auto_provision_authentik(c, email, username, groups)
                touch_last_login(c, u.id)
            if not u.disabled:
                return u, None

    # 2. API bearer token
    auth_h = request.headers.get("authorization", "")
    if auth_h.startswith("Bearer "):
        raw = auth_h[7:].strip()
        with db_conn_factory() as c:
            tok = verify_api_token(c, raw)
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

    # 3. Local session cookie
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
        if _is_public(request.url.path):
            return await call_next(request)
        user, reissue = _resolve_user(request, db_conn_factory)
        request.state.user = user
        response = await call_next(request)
        if reissue is not None:
            cookie_value, expires_at = reissue
            set_session_cookie(response, cookie_value, expires_at, request)
        return response

    return auth_middleware


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
    # NPM terminates TLS — trust X-Forwarded-Proto it sets upstream.
    proto = request.headers.get("x-forwarded-proto", "").lower()
    return proto == "https" or request.url.scheme == "https"


def set_session_cookie(response, cookie_value: str, expires_at: int, request: Request) -> None:
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


def clear_session_cookies(response, request: Request) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
