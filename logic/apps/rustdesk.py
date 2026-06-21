"""RustDesk Server (Pro) per-app module.

Wires a self-hosted RustDesk Server Pro console into the OmniGrid Apps surface
following the per-app contract (``adguardhome.py`` two-field-credential shape +
``kavita.py`` re-auth-per-fetch shape):

    SLUGS               — catalog slugs this module handles.
    requires_api_key()  — True. RustDesk Pro authenticates the admin console
                          login (username + password) and returns a Bearer
                          access token. The chip stores the password in
                          ``api_key`` and the username in the plain ``username``
                          chip field (same two-field shape as AdGuard Home).
    test_credential(host_row, chip, candidate_key, *, payload) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + devices (read, rich list) + users
                          (read count). Read-only — no remote-control surface.

Edition note
-----------
This integration targets **RustDesk Server Pro** (rustdesk-server-pro), which
ships a web console + REST API on port 21114. The **OSS** server (hbbs/hbbr) has
NO HTTP API — only the relay / rendezvous TCP ports — so against an OSS server
the login probe fails and the card degrades to a clear "needs RustDesk Server
Pro" message (the chip still shows reachability via its port probe).

Auth model: ``POST /api/login {username, password, id, uuid}`` returns an
``AuthBody {access_token, type, tfa_type, secret, user}``; the token is sent as
``Authorization: Bearer <token>`` on every call. We re-authenticate per fetch
(one cheap extra round-trip) rather than caching the token (Pro's login tokens
are in-memory and drop on a server restart — stateless re-auth is correct), same
rationale as qBittorrent / Kavita. RustDesk Pro may serve the console over HTTPS
with an internal cert, so TLS verification defaults OFF (per-chip ``verify_tls``
toggle). Single-instance app (NOT fleet). No image proxy (no thumbnails).

2FA (TOTP): when the console user has 2FA enabled, the first ``/api/login``
returns an ``AuthBody`` with ``tfa_type`` set + a challenge ``secret`` (and no
``access_token``). We complete it the same way the RustDesk client does — POST
``/api/login`` again with ``{username, id, uuid, tfa_code: <6-digit>, secret:
<echoed challenge>}`` where the code is generated from the operator-supplied
base32 TOTP seed (stored in the secret ``totp_secret`` chip field, like NPM).
If 2FA is required but no seed is configured, the card surfaces an actionable
"paste the 2FA secret" message; a dedicated non-2FA user is still the fallback.
NOTE: RustDesk Server Pro is closed-source, so the 2FA submission shape is
reconstructed from the open client — validate live against your server.

The expanded card answers "is my RustDesk fleet healthy":

    devices / online      — registered peers + how many are online now
    users                 — console user accounts
    version               — RustDesk server version

Upstream API reference (RustDesk Server Pro, base ``<console-url>``):
``POST /api/login`` · ``GET /api/peers`` (``{total, data:[{status, last_online,
info:{hostname, os, version, …}}]}``) · ``GET /api/users`` (``{total}``) ·
``GET /api/software/version/server`` (``{server, client}``).
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl,
    resolve_userpass)
from logic.coerce import as_dict, as_list, safe_int
from logic.tuning import Tunable as _Tunable
from logic.tuning import tuning_int as _tuning_int

# Catalog template slugs handled by this module. The built-in template is
# `rustdesk`; the aliases catch operator-renamed chips.
SLUGS: tuple[str, ...] = ("rustdesk", "rustdesk-server", "rustdesk-server-pro")

DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# Cap on rich-item rows a list skill returns.
_MAX_ROWS = 50

SKILLS: tuple[dict, ...] = (
    {
        "id": "rustdesk_status",
        "name": "RustDesk status",
        "ai_phrases": ("rustdesk status, how many rustdesk devices, remote desktop "
                       "status, rustdesk peers online, rustdesk overview, rustdesk "
                       "server health, how many remote machines"),
        "destructive": False,
    },
    {
        "id": "rustdesk_devices",
        "name": "List RustDesk devices",
        "ai_phrases": ("list rustdesk devices, show my remote machines, which "
                       "rustdesk peers are online, rustdesk device list, what "
                       "machines are registered, rustdesk computers"),
        "destructive": False,
    },
    {
        "id": "rustdesk_users",
        "name": "RustDesk users",
        "ai_phrases": ("how many rustdesk users, rustdesk user count, console "
                       "users, rustdesk accounts"),
        "destructive": False,
    },
    {
        "id": "rustdesk_stale",
        "name": "Stale RustDesk devices",
        "ai_phrases": ("stale rustdesk devices, rustdesk machines not checked in, "
                       "rustdesk devices to clean up, which rustdesk peers are "
                       "offline for a long time, old rustdesk machines, abandoned "
                       "remote machines"),
        "destructive": False,
    },
)


def requires_api_key() -> bool:
    """RustDesk Pro authenticates with the console username + password (exchanged
    for a Bearer token); the editor MUST render the username + password inputs
    + Test."""
    return True


def _verify(chip: dict) -> bool:
    """Whether to verify the upstream TLS certificate. Default False — a
    self-hosted RustDesk console often runs an internal / self-signed cert; the
    operator flips the per-chip ``verify_tls`` toggle ON for a real cert."""
    return bool(chip.get("verify_tls"))


def _hdr(token: str) -> dict:
    """Bearer-token + JSON-Accept header for an authenticated call."""
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


# Actionable connection message — the most common cause of a bare
# "ConnectError: All connection attempts failed" is the chip URL pointing at a
# relay/rendezvous TCP port (21115–21119) or the wrong scheme, NOT the web
# console (21114). Surface that instead of the raw httpx string.
_CONNECT_HINT = ("couldn't connect — point the chip URL at the RustDesk Server "
                 "Pro WEB CONSOLE (default port 21114, over http/https), NOT the "
                 "relay ports 21115–21119, and make sure OmniGrid can reach the "
                 "host")

# 2FA is enabled but no TOTP seed configured — guide the operator to paste it
# (or, failing that, use a dedicated non-2FA user).
_TFA_NEED_SECRET = ("this RustDesk account has 2FA (TOTP) enabled — paste the "
                    "2FA secret (the base32 setup key shown when you turned on "
                    "2FA) in the editor so OmniGrid can generate the codes, or "
                    "create a dedicated RustDesk user WITHOUT 2FA and use that.")

# 2FA submission with a seed still failed — the seed is wrong, the clock is off,
# or the account uses a non-TOTP 2FA method (email). Fall back to the dedicated
# user.
_TFA_FAILED = ("2FA verification failed — check the 2FA secret + the server "
               "clock, or create a dedicated RustDesk user WITHOUT 2FA (web "
               "console → Settings → Users) and use those credentials.")

# No API at the URL — OSS server / wrong path.
_NO_API_HINT = ("404 — no RustDesk API here. This needs RustDesk Server Pro (the "
                "OSS server has no API); point the chip URL at the Pro web "
                "console (port 21114)")


def _totp_now(secret: str) -> str:
    """Current 6-digit TOTP code from a base32 ``secret`` (the console user's 2FA
    seed). '' on any failure (no secret / bad seed / pyotp missing)."""
    s = (secret or "").strip().replace(" ", "")
    if not s:
        return ""
    try:
        import pyotp  # noqa: PLC0415
        return str(pyotp.TOTP(s).now())
    except (ValueError, TypeError, ImportError):
        return ""


def _device_ident(base: str) -> "tuple[str, str]":
    """Stable pseudo device ``(id, uuid)`` for the login body. RustDesk's
    ``/api/login`` expects a device id + uuid; we derive deterministic ones from
    the upstream base URL so the same "device" is presented each poll (which
    matters for the 2FA challenge correlation).

    The hash input is ONLY the non-secret base URL — never any credential — so
    this is not password / credential hashing, and SHA-256 is the correct choice
    because the id MUST be byte-for-byte deterministic across polls (a salted /
    expensive KDF would defeat that). The username is deliberately NOT part of
    the input: it reaches this module via ``resolve_userpass``'s
    ``(username, password)`` tuple, which a static-analysis taint tracker can't
    distinguish from the password — feeding it here produces a false
    ``py/weak-sensitive-data-hashing`` alert — and a per-server device id needs
    no per-user component. ``usedforsecurity=False`` documents the non-security
    intent."""
    h = hashlib.sha256(f"omnigrid-rustdesk|{base}".encode(),
                       usedforsecurity=False).hexdigest()
    dev_id = str(int(h[:12], 16) % 1_000_000_000).zfill(9)
    return dev_id, h[:32]


async def _login_2fa(cli: "httpx.AsyncClient", base: str, username: str,
                     dev_id: str, dev_uuid: str, challenge_secret: str,
                     totp_secret: str) -> "tuple[str, str]":
    """Second-step 2FA submission: POST ``/api/login`` again with a generated
    ``tfa_code`` + the echoed challenge ``secret``. Returns ``(token, '')`` or
    ``('', reason)``."""
    if not totp_secret:
        return "", _TFA_NEED_SECRET
    code = _totp_now(totp_secret)
    if not code:
        return "", "the 2FA secret isn't a valid base32 TOTP key"
    body: dict[str, Any] = {"username": username, "id": dev_id, "uuid": dev_uuid,
                            "tfa_code": code}
    if challenge_secret:
        body["secret"] = challenge_secret
    try:
        r = await cli.post(base + "/api/login", json=body,
                           headers={"Content-Type": "application/json",
                                    "Accept": "application/json"})
    except (httpx.HTTPError, OSError) as e:
        return "", f"2FA submission failed: {type(e).__name__}"
    if 200 <= r.status_code < 300:
        try:
            tok = str(as_dict(r.json()).get("access_token") or "")
        except (ValueError, TypeError):
            tok = ""
        if tok:
            return tok, ""
    return "", _TFA_FAILED


async def _login(cli: "httpx.AsyncClient", base: str, username: str,
                 password: str, totp_secret: str = "") -> "tuple[str, str]":
    """Exchange console ``username`` + ``password`` for a Bearer access token
    via ``POST /api/login``. Completes a 2FA (TOTP) challenge when the response
    carries ``tfa_type`` and a ``totp_secret`` is configured. Returns
    ``(token, '')`` on success or ``('', reason)`` on failure — the reason is an
    actionable human string (connection / 2FA / bad-creds) so callers can show
    WHY instead of a generic "login failed"."""
    dev_id, dev_uuid = _device_ident(base)
    body = {"username": username, "password": password,
            "id": dev_id, "uuid": dev_uuid}
    try:
        r = await cli.post(base + "/api/login", json=body,
                           headers={"Content-Type": "application/json",
                                    "Accept": "application/json"})
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return "", _CONNECT_HINT
    except (httpx.HTTPError, OSError) as e:
        return "", f"unreachable: {type(e).__name__}"
    if 200 <= r.status_code < 300:
        try:
            ab = as_dict(r.json())
        except (ValueError, TypeError):
            ab = {}
        tok = str(ab.get("access_token") or "")
        if tok:
            return tok, ""
        if str(ab.get("tfa_type") or "").strip():  # 2FA challenge
            return await _login_2fa(cli, base, username, dev_id, dev_uuid,
                                    str(ab.get("secret") or ""), totp_secret)
        return "", ("connected but no access token (check credentials / that "
                    "this is RustDesk Server Pro)")
    if r.status_code == 400:
        # Some Pro builds reject the first login with 400 when 2FA is on. If a
        # seed is configured, attempt the 2FA submission (no echoed challenge);
        # otherwise prompt for the seed.
        if totp_secret:
            tok, reason = await _login_2fa(cli, base, username, dev_id, dev_uuid,
                                           "", totp_secret)
            return tok, (reason or "")
        return "", _TFA_NEED_SECRET
    if r.status_code in (401, 403):
        return "", "auth failed (check the console username + password)"
    if r.status_code == 404:
        return "", _NO_API_HINT
    return "", f"HTTP {r.status_code}"


async def _get(cli: "httpx.AsyncClient", url: str, token: str) -> Any:
    """GET an authenticated endpoint; parsed JSON or None on non-2xx / parse
    failure."""
    r = await cli.get(url, headers=_hdr(token))
    if not (200 <= r.status_code < 300):
        return None
    try:
        return r.json()
    except (ValueError, TypeError):
        return None


def _peer_online(peer: dict) -> bool:
    """True when a peer is currently online. RustDesk reports a live ``status``
    flag (1 = online); fall back to a recent ``last_online`` heartbeat
    (≤5 min) when status is absent."""
    st = peer.get("status")
    if isinstance(st, bool):
        return st
    if safe_int(st) == 1:
        return True
    if str(st).strip().lower() == "online":
        return True
    lo = safe_int(peer.get("last_online"))
    # last_online is unix seconds (some builds ms — both pass the 5-min test for
    # a genuinely-recent heartbeat; an old offline peer fails either way).
    if lo:
        now = time.time()
        if lo > 1e12:  # milliseconds
            lo /= 1000.0
        return (now - lo) <= 300
    return False


def _peer_last_online_s(peer: dict) -> float:
    """Last-seen age in SECONDS for a peer (now - last_online), or -1 when the
    peer carries no usable ``last_online`` (an unknown age is NOT counted as
    stale). ``last_online`` is unix seconds (some builds milliseconds — both are
    normalised)."""
    lo = safe_int(peer.get("last_online"))
    if not lo:
        return -1.0
    if lo > 1e12:  # milliseconds
        lo /= 1000.0
    return max(0.0, time.time() - lo)


# Canonical OS-family buckets for the fleet-composition stat. Substring match on
# the peer's ``info.os`` string (RustDesk reports e.g. "Windows 10 Pro",
# "macOS 14.1", "Linux", "Android 13") — longer/more-specific phrases first.
_OS_FAMILIES: tuple[tuple[str, str], ...] = (
    ("windows", "windows"),
    ("macos", "macos"), ("mac os", "macos"), ("darwin", "macos"), ("osx", "macos"),
    ("android", "android"),
    ("ios", "ios"), ("ipados", "ios"),
    ("linux", "linux"), ("ubuntu", "linux"), ("debian", "linux"),
    ("fedora", "linux"), ("arch", "linux"), ("centos", "linux"),
)

# Display labels for the OS-family buckets (backend skill text — English per the
# "don't translate backend strings" rule; the SPA has its own i18n labels).
_OS_LABELS: dict[str, str] = {
    "windows": "Windows", "macos": "macOS", "android": "Android",
    "ios": "iOS", "linux": "Linux", "other": "Other",
}


def _os_family(os_str: str) -> str:
    """Bucket a peer's ``info.os`` string into a canonical family key
    (windows / macos / android / ios / linux / other)."""
    s = (os_str or "").strip().lower()
    if not s:
        return "other"
    for needle, fam in _OS_FAMILIES:
        if needle in s:
            return fam
    return "other"


# noinspection DuplicatedCode
async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe ``POST /api/login`` with the candidate username + password.
    ``candidate_key`` is the password; the username comes from the test payload
    (pre-save) or the stored chip. Returns ``{ok, detail, status}``."""
    pay = payload or {}
    username, password = resolve_userpass(
        chip,
        password=(candidate_key or "").strip() or None,
        username=(pay.get("username") or "").strip() or None,
    )
    if not username:
        return {"ok": False, "detail": "console username required", "status": 0}
    if not password:
        return {"ok": False, "detail": "password required", "status": 0}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    # Honour the LIVE editor toggles (test-before-save) when present, else the
    # stored chip values.
    verify = bool(pay.get("verify_tls")) if "verify_tls" in pay else _verify(chip)
    totp_secret = ((pay.get("totp_secret") or "").strip()
                   or str(chip.get("totp_secret") or ""))
    try:
        async with httpx.AsyncClient(verify=verify, timeout=10.0,
                                     follow_redirects=True) as cli:
            token, reason = await _login(cli, base, username, password, totp_secret)
            if not token:
                return {"ok": False, "status": 0,
                        "detail": reason or "login failed"}
            ver = ""
            vbody = await _get(cli, base + "/api/software/version/server", token)
            if isinstance(vbody, dict):
                ver = str(vbody.get("server") or "").strip()
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    return {"ok": True, "detail": f"OK{(' — RustDesk ' + ver) if ver else ''}",
            "status": 200}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch the RustDesk fleet summary for the card: log in, then fan out
    peers + users + server version. Returns the card payload (see the module
    docstring). Raises ``ValueError`` / ``RuntimeError`` (caller maps to
    HTTPException) when the password is unset / the base URL won't resolve / the
    API isn't reachable (OSS server)."""
    username, password = resolve_userpass(chip)
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=password, log_tag="rustdesk")
    if hit is not None:
        return hit
    totp_secret = str(chip.get("totp_secret") or "")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            token, reason = await _login(cli, base, username, password, totp_secret)
            if not token:
                raise RuntimeError(reason or (
                    "RustDesk login failed — check the console username + "
                    "password, and that this is RustDesk Server Pro (the OSS "
                    "server has no API)"))
            peers_body, users_body, ver_body = await asyncio.gather(
                _get(cli, base + "/api/peers", token),
                _get(cli, base + "/api/users", token),
                _get(cli, base + "/api/software/version/server", token),
            )
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[rustdesk] error: fetch host={host_id} base={base} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")

    peers = [p for p in as_list(as_dict(peers_body).get("data")) if isinstance(p, dict)]
    online = sum(1 for p in peers if _peer_online(p))
    # `total` field is authoritative for the count; fall back to len(data).
    peers_total = safe_int(as_dict(peers_body).get("total")) or len(peers)
    users_total = safe_int(as_dict(users_body).get("total"))
    if not users_total:
        users_total = len([u for u in as_list(as_dict(users_body).get("data")) if u])
    version = str(as_dict(ver_body).get("server") or "").strip()

    # Stale machines: registered peers that haven't checked in for >= the
    # operator-tunable window (default 30 days) — an actionable "clean these up"
    # signal. A peer with no usable last_online (age -1) is NOT counted stale.
    stale_after_s = max(1, _tuning_int(_Tunable.RUSTDESK_STALE_DAYS)) * 86400
    stale = 0
    os_breakdown: dict[str, int] = {}
    for p in peers:
        age = _peer_last_online_s(p)
        if 0 <= age and age >= stale_after_s:
            stale += 1
        fam = _os_family(str(as_dict(p.get("info")).get("os") or ""))
        os_breakdown[fam] = os_breakdown.get(fam, 0) + 1

    out: dict[str, Any] = {
        "available": True,
        "version": version,
        "devices": peers_total,
        "devices_online": online,
        "devices_offline": max(0, peers_total - online),
        "users": users_total,
        "stale_devices": stale,
        "stale_days": _tuning_int(_Tunable.RUSTDESK_STALE_DAYS),
        "os_breakdown": os_breakdown,
        "fetched_at": int(now),
    }
    # Online-peers trend from the lifespan sampler (the Pro API exposes only
    # current state, so this sampled history is the only "peak concurrent
    # devices" signal). Best-effort — never let a trend read break the card.
    try:
        from logic.apps import rustdesk_sampler as _rd_sampler  # noqa: PLC0415
        out["usage"] = _rd_sampler.usage_summary(
            str(host_id or ""), int(service_idx or 0),
            days=_tuning_int(_Tunable.RUSTDESK_HISTORY_DAYS))
    except Exception as e:  # noqa: BLE001
        print(f"[rustdesk] usage_summary skipped: {type(e).__name__}: {e}")
    print(f"[rustdesk] INFO fetched host={host_id} devices={out['devices']} "
          f"online={out['devices_online']} stale={stale} users={out['users']} "
          f"ver={version or '-'}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "version": data.get("version") or "",
        "devices": safe_int(data.get("devices")),
        "devices_online": safe_int(data.get("devices_online")),
        "devices_offline": safe_int(data.get("devices_offline")),
        "stale_devices": safe_int(data.get("stale_devices")),
        "users": safe_int(data.get("users")),
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def _resolve_skill_target(host_row: dict, chip: dict) -> "tuple[str, str, str, Optional[dict]]":
    """Resolve ``(username, password, base)`` or a ready ``{ok: False, detail}``
    error dict for a RustDesk skill."""
    username, password = resolve_userpass(chip)
    if not password:
        return "", "", "", {"ok": False, "status": 0,
                            "detail": "RustDesk password not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", "", {"ok": False, "status": 0,
                            "detail": "no upstream URL configured"}
    return username, password, base, None


# noinspection DuplicatedCode
def _attach_items(out: dict, items: list, count_i18n: str) -> dict:
    """Attach the rich-item list + count + count-i18n key to a skill result
    (no-op when empty). Returns ``out`` for one-line use. Shape shared verbatim
    with other per-app modules (NPM / proxmox / …) by design — the per-app
    encapsulation convention accepts the duplication."""
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = count_i18n
    return out


# noinspection DuplicatedCode
async def _live_data(host_row: dict, chip: dict, host_id: Optional[str],
                     service_idx: Optional[int]) -> "tuple[Optional[dict], Optional[dict]]":
    """Force-fetch the card payload for a skill (cache-bypassing). Returns
    ``(data, None)`` on success or ``(None, error_dict)`` when ``fetch_data``
    raised."""
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return None, {"ok": False, "detail": str(e), "status": 0}
    return data, None


async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404)."""
    if skill_id == "rustdesk_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "rustdesk_devices":
        return await _devices_skill(host_row, chip, host_id=host_id)
    if skill_id == "rustdesk_users":
        return await _users_skill(host_row, chip, host_id=host_id,
                                  service_idx=service_idx)
    if skill_id == "rustdesk_stale":
        return await _stale_skill(host_row, chip, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the fleet summary. Never raises."""
    print(f"[rustdesk] INFO rustdesk_status host={host_id} svc_idx={service_idx} "
          f"(live fetch)")
    data, err = await _live_data(host_row, chip, host_id, service_idx)
    if data is None:
        return err or {"ok": False, "detail": "fetch failed", "status": 0}
    dev = safe_int(data.get("devices"))
    online = safe_int(data.get("devices_online"))
    offline = safe_int(data.get("devices_offline"))
    stale = safe_int(data.get("stale_devices"))
    stale_days = safe_int(data.get("stale_days"))
    lines = [
        f"🖥️ Devices: {online}/{dev} online" + (f" · {offline} offline" if offline else ""),
        f"👤 Users: {safe_int(data.get('users'))}",
    ]
    if stale:
        lines.append(f"🧹 {stale} stale (no check-in {stale_days}+ days)")
    os_bd = data.get("os_breakdown")
    if isinstance(os_bd, dict) and os_bd:
        parts = [f"{_OS_LABELS.get(k, k)} {v}"
                 for k, v in sorted(os_bd.items(), key=lambda kv: -kv[1]) if v]
        if parts:
            lines.append("💻 " + " · ".join(parts))
    ver = str(data.get("version") or "").strip()
    if ver:
        lines.append(f"· RustDesk {ver}")
    return {"ok": True, "detail": "\n".join(lines), "status": 200,
            "devices": dev, "online": online, "stale": stale}


# noinspection DuplicatedCode
async def _devices_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None) -> dict:
    """Read-only: list registered peers as rich rows (hostname + OS + online
    dot). Online first, then by hostname. Never raises."""
    username, password, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[rustdesk] INFO rustdesk_devices host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            token, reason = await _login(cli, base, username, password,
                                         str(chip.get("totp_secret") or ""))
            if not token:
                return {"ok": False, "status": 401,
                        "detail": reason or ("RustDesk login failed (check username "
                                             "+ password / that this is RustDesk "
                                             "Server Pro)")}
            peers_body = await _get(cli, base + "/api/peers", token)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    peers = [p for p in as_list(as_dict(peers_body).get("data")) if isinstance(p, dict)]
    if not peers:
        return {"ok": True, "status": 200, "detail": "🖥️ No RustDesk devices found."}
    rows = []
    for p in peers:
        info = as_dict(p.get("info"))
        name = str(info.get("hostname") or "").strip() or str(p.get("id") or "").strip()
        if not name:
            continue
        rows.append((_peer_online(p), name, info, str(p.get("id") or "").strip(),
                     _peer_last_online_s(p)))
    rows.sort(key=lambda r: (0 if r[0] else 1, r[1].lower()))
    items: list = []
    lines: list = []
    for is_on, name, info, pid, age in rows[:_MAX_ROWS]:
        dot = "🟢 online" if is_on else "⚪ offline"
        bits = [dot]
        osname = str(info.get("os") or "").strip()
        if osname:
            bits.append(osname)
        if pid:
            bits.append(f"ID {pid}")
        # Per-device last-seen ("last connected") — only for OFFLINE devices with
        # a known last_online (online ones are connected right now).
        if not is_on and age > 0:
            bits.append(f"last seen {_fmt_age(age)}")
        sub = " · ".join(bits)
        items.append({"title": name, "subtitle": sub})
        lines.append(f"• {name}  ({sub})")
    out: dict = {"ok": True, "status": 200,
                 "detail": "🖥️ RustDesk devices:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.rustdesk.devices_count")


async def _users_skill(host_row: dict, chip: dict, *,
                       host_id: Optional[str] = None,
                       service_idx: Optional[int] = None) -> dict:
    """Read-only: console user count. Never raises."""
    print(f"[rustdesk] INFO rustdesk_users host={host_id} (live fetch)")
    data, err = await _live_data(host_row, chip, host_id, service_idx)
    if data is None:
        return err or {"ok": False, "detail": "fetch failed", "status": 0}
    users = safe_int(data.get("users"))
    return {"ok": True, "status": 200,
            "detail": f"👤 {users} RustDesk console user(s).", "users": users}


def _fmt_age(age_s: float) -> str:
    """Human "Nd / Nh ago" for a last-seen age in seconds (backend skill text —
    English per the 'don't translate backend strings' rule)."""
    days = int(age_s // 86400)
    if days >= 1:
        return f"{days}d ago"
    hours = int(age_s // 3600)
    if hours >= 1:
        return f"{hours}h ago"
    return "recently"


# noinspection DuplicatedCode
async def _stale_skill(host_row: dict, chip: dict, *,
                       host_id: Optional[str] = None) -> dict:
    """Read-only: list registered peers that haven't checked in for >= the
    stale window (default 30 days) — machines to clean up. Oldest-first.
    Never raises."""
    username, password, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[rustdesk] INFO rustdesk_stale host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            token, reason = await _login(cli, base, username, password,
                                         str(chip.get("totp_secret") or ""))
            if not token:
                return {"ok": False, "status": 401,
                        "detail": reason or ("RustDesk login failed (check username "
                                             "+ password / that this is RustDesk "
                                             "Server Pro)")}
            peers_body = await _get(cli, base + "/api/peers", token)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    stale_days = max(1, _tuning_int(_Tunable.RUSTDESK_STALE_DAYS))
    stale_after_s = stale_days * 86400
    peers = [p for p in as_list(as_dict(peers_body).get("data")) if isinstance(p, dict)]
    rows = []
    for p in peers:
        age = _peer_last_online_s(p)
        if age < 0 or age < stale_after_s:
            continue
        info = as_dict(p.get("info"))
        name = str(info.get("hostname") or "").strip() or str(p.get("id") or "").strip()
        if not name:
            continue
        rows.append((age, name, info, str(p.get("id") or "").strip()))
    if not rows:
        return {"ok": True, "status": 200,
                "detail": f"🧹 No stale devices — every RustDesk peer has checked "
                          f"in within {stale_days} days."}
    rows.sort(key=lambda r: -r[0])  # oldest (largest age) first
    items: list = []
    lines: list = []
    for age, name, info, pid in rows[:_MAX_ROWS]:
        bits = [f"last seen {_fmt_age(age)}"]
        osname = str(info.get("os") or "").strip()
        if osname:
            bits.append(osname)
        if pid:
            bits.append(f"ID {pid}")
        sub = " · ".join(bits)
        items.append({"title": name, "subtitle": sub})
        lines.append(f"• {name}  ({sub})")
    head = (f"🧹 {len(rows)} stale RustDesk device(s) (no check-in {stale_days}+ "
            f"days):")
    out: dict = {"ok": True, "status": 200, "detail": head + "\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.rustdesk.stale_count")
