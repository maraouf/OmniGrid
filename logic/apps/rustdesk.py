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

Auth model: ``POST /api/login {username, password}`` returns
``{access_token, user, type}``; the token is sent as ``Authorization: Bearer
<token>`` on every call. We re-authenticate per fetch (one cheap extra
round-trip) rather than caching the token (Pro's login tokens are in-memory and
drop on a server restart — stateless re-auth is correct), same rationale as
qBittorrent / Kavita. RustDesk Pro may serve the console over HTTPS with an
internal cert, so TLS verification defaults OFF (per-chip ``verify_tls`` toggle).
Single-instance app (NOT fleet). No image proxy (no thumbnails).

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
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl,
    resolve_userpass)
from logic.coerce import as_dict, as_list, safe_int

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


async def _login(cli: "httpx.AsyncClient", base: str,
                 username: str, password: str) -> str:
    """Exchange console ``username`` + ``password`` for a Bearer access token
    via ``POST /api/login``. Returns '' on any failure (bad creds / no API /
    unreachable)."""
    try:
        r = await cli.post(base + "/api/login",
                           json={"username": username, "password": password},
                           headers={"Content-Type": "application/json",
                                    "Accept": "application/json"})
    except (httpx.HTTPError, OSError):
        return ""
    if r.status_code != 200:
        return ""
    try:
        return str(as_dict(r.json()).get("access_token") or "")
    except (ValueError, TypeError):
        return ""


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
            lo = lo / 1000.0
        return (now - lo) <= 300
    return False


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
    verify = bool(pay.get("verify_tls")) if "verify_tls" in pay else _verify(chip)
    try:
        async with httpx.AsyncClient(verify=verify, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.post(base + "/api/login",
                               json={"username": username, "password": password},
                               headers={"Content-Type": "application/json",
                                        "Accept": "application/json"})
            if r.status_code in (401, 403):
                return {"ok": False,
                        "detail": "auth failed (check the RustDesk console username "
                                  "+ password)",
                        "status": r.status_code}
            if r.status_code == 404:
                return {"ok": False,
                        "detail": "404 — no RustDesk API here. This needs RustDesk "
                                  "Server Pro (the OSS server has no API); point the "
                                  "chip URL at the Pro web console (port 21114)",
                        "status": 404}
            if not (200 <= r.status_code < 300):
                return {"ok": False, "detail": f"HTTP {r.status_code}",
                        "status": r.status_code}
            try:
                token = str(as_dict(r.json()).get("access_token") or "")
            except (ValueError, TypeError):
                token = ""
            if not token:
                return {"ok": False, "status": r.status_code,
                        "detail": "connected but no access token — check the "
                                  "credentials, or that this is a RustDesk Server "
                                  "Pro console"}
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
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            token = await _login(cli, base, username, password)
            if not token:
                raise RuntimeError(
                    "RustDesk login failed — check the console username + "
                    "password, and that this is RustDesk Server Pro (the OSS "
                    "server has no API)")
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

    out: dict[str, Any] = {
        "available": True,
        "version": version,
        "devices": peers_total,
        "devices_online": online,
        "devices_offline": max(0, peers_total - online),
        "users": users_total,
        "fetched_at": int(now),
    }
    print(f"[rustdesk] INFO fetched host={host_id} devices={out['devices']} "
          f"online={out['devices_online']} users={out['users']} "
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


def _attach_items(out: dict, items: list, count_i18n: str) -> dict:
    """Attach the rich-item list + count + count-i18n key to a skill result
    (no-op when empty). Returns ``out`` for one-line use."""
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = count_i18n
    return out


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
    raise ValueError(f"unknown skill: {skill_id!r}")


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
    lines = [
        f"🖥️ Devices: {online}/{dev} online" + (f" · {offline} offline" if offline else ""),
        f"👤 Users: {safe_int(data.get('users'))}",
    ]
    ver = str(data.get("version") or "").strip()
    if ver:
        lines.append(f"· RustDesk {ver}")
    return {"ok": True, "detail": "\n".join(lines), "status": 200,
            "devices": dev, "online": online}


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
            token = await _login(cli, base, username, password)
            if not token:
                return {"ok": False, "status": 401,
                        "detail": "RustDesk login failed (check username + password "
                                  "/ that this is RustDesk Server Pro)"}
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
        rows.append((_peer_online(p), name, info, str(p.get("id") or "").strip()))
    rows.sort(key=lambda r: (0 if r[0] else 1, r[1].lower()))
    items: list = []
    lines: list = []
    for is_on, name, info, pid in rows[:_MAX_ROWS]:
        dot = "🟢 online" if is_on else "⚪ offline"
        bits = [dot]
        osname = str(info.get("os") or "").strip()
        if osname:
            bits.append(osname)
        if pid:
            bits.append(f"ID {pid}")
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
