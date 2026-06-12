"""OPNsense per-app module (firewall / router).

Encapsulates everything OPNsense-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``proxmox.py`` / ``adguardhome.py`` shape:

    SLUGS               — catalog slugs this module handles.
    requires_api_key()  — True (the chip stores the API SECRET in ``api_key``
                          and the API KEY in the plain ``username`` field).
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + list gateways (read) + list services
                          (read) + restart a service (action, arg, destructive).

Auth model: OPNsense's REST API authenticates with an **API key + API secret**
pair (System → Access → Users → edit a user → "API keys" → "+"). They are sent
as HTTP **Basic** auth — the KEY is the username and the SECRET is the password.
OmniGrid stores the KEY in the chip's plain ``username`` field (returned to the
SPA editor) and the SECRET in the write-only ``api_key`` field (the ``_set`` flag
pattern), exactly like AdGuard Home's username + password. OPNsense ships a
self-signed cert by default, so every client defaults to ``verify=False`` (the
operator flips the per-chip ``verify_tls`` toggle ON for a real cert). The
credential probe hits the auth-required ``GET /api/core/firmware/status`` so a
bad / missing key fails loudly (401). Single-instance app (NOT fleet).

Every sub-call is TOLERATED — a slow / unsupported diagnostics endpoint (the
paths moved from camelCase to snake_case in 24.x; older builds differ) must
NEVER fail the whole card. ``fetch_data`` gathers what it can and zeroes the rest.

Upstream API reference (HTTP Basic key:secret) — https://<host>/api :
    GET  /api/core/firmware/status                 — version + update available
    GET  /api/diagnostics/system/system_resources  — memory used / total
    GET  /api/diagnostics/system/system_time        — uptime + load averages
    GET  /api/diagnostics/system/system_temperature — per-sensor temperatures
    GET  /api/diagnostics/firewall/pf_states        — pf state-table count
    GET  /api/routes/gateway/status                 — per-gateway RTT / loss
    POST /api/core/service/search                   — running services
    GET  /api/dhcpv4/leases/searchLease             — DHCP lease count
    POST /api/core/service/restart/{id}             — restart a service (action)
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, peek_cache, resolve_base_url, resolve_cache_ttl, resolve_userpass)
from logic.coerce import as_dict, as_list, safe_float, safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("opnsense",)

# Per-(host_id, service_idx) data cache for the expanded card. 30s default — a
# firewall's counts move slowly and the fetch fans out several diagnostics calls.
DEFAULT_CACHE_TTL_S = 30
_data_cache: dict[str, tuple[float, dict]] = {}

# Cap on the rich-item rows a list skill returns.
_MAX_ROWS = 50

# Gateway status word → emoji for the rich rows.
_GW_EMOJI = {"online": "🟢", "up": "🟢", "none": "🟢",
             "loss": "🟡", "delay": "🟡", "down": "🔴", "force_down": "🔴"}

SKILLS: tuple[dict, ...] = (
    {
        "id": "opnsense_status",
        "name": "OPNsense status",
        "ai_phrases": ("opnsense status, firewall status, is my firewall healthy, "
                       "opnsense overview, router status, opnsense cpu memory, "
                       "wan gateway status, is the internet up, opnsense health"),
        "destructive": False,
    },
    {
        "id": "opnsense_gateways",
        "name": "List OPNsense gateways",
        "ai_phrases": ("list opnsense gateways, show my gateways, wan gateway "
                       "health, gateway latency and loss, which gateway is down, "
                       "is my wan up, opnsense gateway status"),
        "destructive": False,
    },
    {
        "id": "opnsense_services",
        "name": "List OPNsense services",
        "ai_phrases": ("list opnsense services, show running services, which "
                       "services are running on the firewall, opnsense service "
                       "status, is unbound running, opnsense daemons"),
        "destructive": False,
    },
    {
        "id": "opnsense_restart_service",
        "name": "Restart an OPNsense service",
        "ai_phrases": ("restart the <name> service, restart unbound, restart "
                       "the dhcp service, bounce the <name> daemon on opnsense, "
                       "restart opnsense service"),
        "arg": True,
        "arg_hint": "the service name to restart (e.g. unbound, dhcpd)",
        # DESTRUCTIVE: bounces a live firewall service (brief interruption).
        "destructive": True,
    },
)


def requires_api_key() -> bool:
    """True — OPNsense's API needs a key + secret on every call; the editor
    MUST render the API-key (username) + API-secret (password) inputs + Test."""
    return True


def _verify(chip: dict) -> bool:
    """Per-chip TLS verification (default OFF — OPNsense ships a self-signed
    cert; the operator flips it ON for a real one)."""
    return bool((chip or {}).get("verify_tls"))


async def _get_json(cli: httpx.AsyncClient, base: str, path: str) -> Any:
    """One tolerated GET → parsed JSON, or ``None`` on any failure (auth /
    transport / non-200 / non-JSON). A single diagnostics endpoint being
    unsupported on this OPNsense version must not fail the whole card."""
    try:
        r = await cli.get(base + path, headers={"Accept": "application/json"})
    except (httpx.HTTPError, OSError):
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except (ValueError, TypeError):
        return None


def _loadavg(system_time: dict) -> "tuple[float, float, float]":
    """Parse OPNsense ``system_time.loadavg`` ("0.12, 0.34, 0.56" — or a list)
    into ``(l1, l5, l15)``. Zeros when absent / unparseable."""
    la = system_time.get("loadavg")
    parts: list = []
    if isinstance(la, str):
        parts = [p.strip() for p in la.replace(";", ",").split(",") if p.strip()]
    elif isinstance(la, list):
        parts = list(la)
    vals = [safe_float(p) for p in parts[:3]]
    while len(vals) < 3:
        vals.append(0.0)
    return vals[0], vals[1], vals[2]


def _uptime_seconds(system_time: dict) -> int:
    """OPNsense ``system_time.uptime`` is usually an integer-seconds string;
    some builds give a human "N days HH:MM:SS". Parse both → seconds."""
    up = system_time.get("uptime")
    if isinstance(up, (int, float)):
        return int(up)
    s = str(up or "").strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    # "12 days 03:04:05" style.
    total = 0
    import re as _re  # noqa: PLC0415
    dm = _re.search(r"(?P<d>\d+)\s*day", s, _re.I)
    if dm:
        total += int(dm.group("d")) * 86400
    tm = _re.search(r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})", s)
    if tm:
        total += (int(tm.group("h")) * 3600 + int(tm.group("m")) * 60
                  + int(tm.group("s")))
    return total


def _firmware_shape(fw: dict) -> "tuple[str, bool]":
    """``(version, update_available)`` from ``/api/core/firmware/status``. The
    version lives under ``product_version`` (newer) or ``product.product_version``;
    an update is signalled by a non-empty ``upgrade_packages`` / a ``status`` of
    ``update`` / ``status_upgrade_action``."""
    ver = str(fw.get("product_version")
              or as_dict(fw.get("product")).get("product_version") or "").strip()
    upgrades = as_list(fw.get("upgrade_packages"))
    new_packages = as_list(fw.get("new_packages"))
    status = str(fw.get("status") or "").strip().lower()
    update = bool(upgrades or new_packages
                  or status in ("update", "upgrade")
                  or str(fw.get("status_upgrade_action") or "").strip().lower() == "all")
    return ver, update


def _gateways_shape(gw: dict) -> "tuple[list, int, int, dict]":
    """Parse ``/api/routes/gateway/status`` ``items`` into a compact list +
    ``(total, online, worst)`` where worst is the gateway with the highest
    packet loss (or the first down one)."""
    items = as_list(gw.get("items"))
    out: list = []
    online = 0
    worst: dict = {}
    worst_loss = -1.0
    for g in items:
        if not isinstance(g, dict):
            continue
        name = str(g.get("name") or "?").strip()
        status = str(g.get("status") or g.get("status_translated") or "").strip().lower()
        # OPNsense reports loss / delay as e.g. "0.0 %" / "12.3 ms".
        loss = safe_float(str(g.get("loss") or "0").replace("%", "").strip())
        delay = safe_float(str(g.get("delay") or "0").replace("ms", "").strip())
        ok = status in ("online", "up", "none", "")
        if ok:
            online += 1
        row = {"name": name, "status": status or "online",
               "loss": round(loss, 1), "delay": round(delay, 1),
               "address": str(g.get("address") or "").strip()}
        out.append(row)
        score = loss + (1000.0 if not ok else 0.0)
        if score > worst_loss:
            worst_loss = score
            worst = row
    return out[:_MAX_ROWS], len(out), online, worst


def _services_shape(svc: dict) -> "tuple[list, int, int]":
    """Parse ``/api/core/service/search`` ``rows`` into a compact list +
    ``(total, running)``."""
    rows = as_list(svc.get("rows"))
    out: list = []
    running = 0
    for s in rows:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or s.get("id") or "?").strip()
        desc = str(s.get("description") or "").strip()
        is_run = bool(safe_int(s.get("running")))
        if is_run:
            running += 1
        out.append({"name": name, "description": desc, "running": is_run,
                    "id": str(s.get("id") or s.get("name") or "").strip()})
    return out[:_MAX_ROWS], len(out), running


def _temp_max(temps: Any) -> float:
    """Highest temperature (°C) across ``/api/diagnostics/system/system_temperature``
    (a list of ``{device, temperature, type}``). 0 when none."""
    best = 0.0
    for t in as_list(temps):
        if isinstance(t, dict):
            c = safe_float(t.get("temperature"))
            if c > best:
                best = c
    return round(best, 1)


async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe the auth-required ``GET /api/core/firmware/status`` with the
    supplied API key + secret (Basic auth). ``candidate_key`` is the SECRET; the
    KEY (username) comes from ``payload`` (the pre-save form) or the stored chip.
    Returns ``{ok, detail, status}``."""
    pay = payload or {}
    username, password = resolve_userpass(
        chip, password=(candidate_key or "").strip() or None,
        username=(pay.get("username") or "").strip() or None)
    if not username or not password:
        return {"ok": False, "detail": "API key + secret required", "status": 0}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    url = base + "/api/core/firmware/status"
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=15.0,
                                     follow_redirects=True,
                                     auth=httpx.BasicAuth(username, password)) as cli:
            r = await cli.get(url, headers={"Accept": "application/json"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check API key / secret)",
                "status": r.status_code}
    if r.status_code != 200:
        return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}
    try:
        ver, _ = _firmware_shape(as_dict(r.json()))
    except (ValueError, TypeError):
        ver = ""
    return {"ok": True, "detail": f"OK{(' — OPNsense ' + ver) if ver else ''}",
            "status": 200}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fan out the OPNsense diagnostics reads for the expanded card. Every
    sub-call is tolerated (zeroes on failure). Raises ``ValueError`` (base URL
    won't resolve) / ``RuntimeError`` (auth rejected on the load-bearing call)."""
    now = time.time()
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured for this instance")
    ttl = resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S)
    ck = cache_key(host_id, service_idx)
    if not force:
        cached = _data_cache.get(ck)
        if cached is not None and (now - cached[0]) < ttl:
            return cached[1]
    username, password = resolve_userpass(chip)
    if not username or not password:
        raise ValueError("API key / secret not set for this instance")
    print(f"[opnsense] INFO fetch host={host_id} svc_idx={service_idx} base={base}")
    auth = httpx.BasicAuth(username, password)
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True, auth=auth) as cli:
            # The load-bearing call (firmware status) doubles as the auth probe.
            fw = await cli.get(base + "/api/core/firmware/status",
                               headers={"Accept": "application/json"})
            if fw.status_code in (401, 403):
                raise RuntimeError(f"auth failed: HTTP {fw.status_code} "
                                   f"(check the API key / secret)")
            if fw.status_code != 200:
                raise RuntimeError(f"upstream returned HTTP {fw.status_code} for "
                                   f"/api/core/firmware/status (check the chip URL "
                                   f"points at the OPNsense web UI root)")
            try:
                fw_json = as_dict(fw.json())
            except (ValueError, TypeError):
                fw_json = {}
            # Everything else is tolerated + fetched in parallel.
            res, stime, temp, gw, svc, pf, leases = await asyncio.gather(
                _get_json(cli, base, "/api/diagnostics/system/system_resources"),
                _get_json(cli, base, "/api/diagnostics/system/system_time"),
                _get_json(cli, base, "/api/diagnostics/system/system_temperature"),
                _get_json(cli, base, "/api/routes/gateway/status"),
                _post_json(cli, base, "/api/core/service/search"),
                _get_json(cli, base, "/api/diagnostics/firewall/pf_states"),
                _get_json(cli, base, "/api/dhcpv4/leases/searchLease"),
            )
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[opnsense] error: fetch host={host_id} base={base} failed — "
              f"{type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    version, update_available = _firmware_shape(fw_json)
    res_d = as_dict(res)
    mem = as_dict(res_d.get("memory"))
    mem_used = safe_int(mem.get("used"))
    mem_total = safe_int(mem.get("total"))
    mem_percent = round(mem_used / mem_total * 100, 1) if mem_total else 0.0
    stime_d = as_dict(stime)
    load_1m, load_5m, load_15m = _loadavg(stime_d)
    uptime_s = _uptime_seconds(stime_d)
    gw_list, gw_total, gw_online, worst_gw = _gateways_shape(as_dict(gw))
    svc_list, svc_total, svc_running = _services_shape(as_dict(svc))
    pf_d = as_dict(pf)
    pf_current = safe_int(pf_d.get("current") or as_dict(pf_d.get("details")).get("current"))
    pf_limit = safe_int(pf_d.get("limit") or as_dict(pf_d.get("details")).get("limit"))
    leases_total = safe_int(as_dict(leases).get("total"))
    temp_max_c = _temp_max(temp)
    out: dict[str, Any] = {
        "available": True,
        "version": version,
        "update_available": update_available,
        "mem_used": mem_used,
        "mem_total": mem_total,
        "mem_percent": mem_percent,
        "load_1m": round(load_1m, 2),
        "load_5m": round(load_5m, 2),
        "load_15m": round(load_15m, 2),
        "uptime_s": uptime_s,
        "gateways_total": gw_total,
        "gateways_online": gw_online,
        "worst_gateway": worst_gw,
        "gateways": gw_list,
        "services_total": svc_total,
        "services_running": svc_running,
        "services": svc_list,
        "pf_states_current": pf_current,
        "pf_states_limit": pf_limit,
        "dhcp_leases": leases_total,
        "temp_max_c": temp_max_c,
        "fetched_at": int(now),
    }
    print(f"[opnsense] INFO fetched host={host_id} v={version} "
          f"upd={update_available} mem={mem_percent}% load={out['load_1m']} "
          f"gw={gw_online}/{gw_total} svc={svc_running}/{svc_total} "
          f"pf={pf_current} leases={leases_total} temp={temp_max_c}C")
    _data_cache[ck] = (now, out)
    return out


# noinspection DuplicatedCode
async def _post_json(cli: httpx.AsyncClient, base: str, path: str) -> Any:
    """Tolerated POST (OPNsense ``*/search`` endpoints are POST) → parsed JSON,
    or ``None`` on any failure. Sends an empty search body (defaults to the full
    first page)."""
    try:
        r = await cli.post(base + path, headers={"Accept": "application/json"},
                           json={"current": 1, "rowCount": 200, "searchPhrase": ""})
    except (httpx.HTTPError, OSError):
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except (ValueError, TypeError):
        return None


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "version": data.get("version") or "",
        "update_available": bool(data.get("update_available")),
        "mem_percent": safe_float(data.get("mem_percent")),
        "load_1m": safe_float(data.get("load_1m")),
        "gateways_online": safe_int(data.get("gateways_online")),
        "gateways_total": safe_int(data.get("gateways_total")),
        "services_running": safe_int(data.get("services_running")),
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown id."""
    if skill_id == "opnsense_status":
        return await _status_skill(host_row, chip, host_id=host_id, service_idx=service_idx)
    if skill_id == "opnsense_gateways":
        return await _gateways_skill(host_row, chip, host_id=host_id, service_idx=service_idx)
    if skill_id == "opnsense_services":
        return await _services_skill(host_row, chip, host_id=host_id, service_idx=service_idx)
    if skill_id == "opnsense_restart_service":
        return await _restart_service_skill(host_row, chip, arg=_kw.get("arg"), host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _fmt_uptime(s: int) -> str:
    """Humanise an uptime in seconds → "Xd Yh" / "Yh Zm" / "Zm"."""
    s = max(0, int(s))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _fmt_gib(n: Any) -> str:
    """Render a byte count as GiB (1 decimal). '' for non-positive."""
    b = float(safe_int(n))
    if b <= 0:
        return ""
    return f"{b / 1073741824:.1f} GiB"


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch + format the firewall summary. Never raises."""
    print(f"[opnsense] INFO opnsense_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[opnsense] warning: opnsense_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    gw_total = safe_int(data.get("gateways_total"))
    gw_online = safe_int(data.get("gateways_online"))
    worst = as_dict(data.get("worst_gateway"))
    lines = [
        f"🛡️ OPNsense {data.get('version') or '?'}"
        + ("  ⬆️ update available" if data.get("update_available") else ""),
        f"📡 Gateways: {gw_online}/{gw_total} online"
        + (f" · worst {worst.get('name')} {worst.get('loss')}% loss / "
           f"{worst.get('delay')}ms" if worst and (gw_total != gw_online or safe_float(worst.get('loss')) > 0) else ""),
        f"⚙️ Services: {safe_int(data.get('services_running'))}/"
        f"{safe_int(data.get('services_total'))} running",
        f"🧠 Memory: {safe_float(data.get('mem_percent'))}%"
        + (f" ({_fmt_gib(data.get('mem_used'))} / {_fmt_gib(data.get('mem_total'))})"
           if safe_int(data.get('mem_total')) else "")
        + f" · ⚖️ load {data.get('load_1m')} / {data.get('load_5m')} / {data.get('load_15m')}",
        f"🔥 Firewall states: {safe_int(data.get('pf_states_current')):,}"
        + (f" / {safe_int(data.get('pf_states_limit')):,}" if safe_int(data.get('pf_states_limit')) else "")
        + f" · 📇 DHCP leases: {safe_int(data.get('dhcp_leases'))}",
    ]
    if safe_int(data.get("uptime_s")):
        lines.append(f"⏱️ Uptime: {_fmt_uptime(safe_int(data.get('uptime_s')))}")
    if safe_float(data.get("temp_max_c")) > 0:
        lines.append(f"🌡️ Max temp: {safe_float(data.get('temp_max_c'))}°C")
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "version": data.get("version") or "",
            "gateways_online": gw_online, "gateways_total": gw_total,
            "services_running": safe_int(data.get("services_running"))}


# noinspection DuplicatedCode
async def _gateways_skill(host_row: dict, chip: dict, *,
                          host_id: Optional[str] = None,
                          service_idx: Optional[int] = None) -> dict:
    """Read-only: list every gateway with its status, latency and packet loss.
    Never raises."""
    print(f"[opnsense] INFO opnsense_gateways host={host_id} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "detail": str(e), "status": 0}
    gws = as_list(data.get("gateways"))
    if not gws:
        return {"ok": True, "status": 200, "detail": "No gateways configured on OPNsense."}
    lines = []
    for g in gws:
        if not isinstance(g, dict):
            continue
        emoji = _GW_EMOJI.get(str(g.get("status") or "").lower(), "•")
        bits = [f"{emoji} {g.get('name') or '?'}"]
        addr = str(g.get("address") or "").strip()
        if addr:
            bits.append(f"({addr})")
        bits.append(f"· {safe_float(g.get('delay'))}ms · {safe_float(g.get('loss'))}% loss")
        lines.append(" ".join(bits))
    head = f"📡 {len(gws)} gateway{'s' if len(gws) != 1 else ''}"
    return {"ok": True, "status": 200, "detail": head + "\n" + "\n".join(lines)}


# noinspection DuplicatedCode
async def _services_skill(host_row: dict, chip: dict, *,
                          host_id: Optional[str] = None,
                          service_idx: Optional[int] = None) -> dict:
    """Read-only: list OPNsense services (running first). Never raises."""
    print(f"[opnsense] INFO opnsense_services host={host_id} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "detail": str(e), "status": 0}
    svcs = sorted(as_list(data.get("services")),
                  key=lambda _x: (not bool(as_dict(_x).get("running")),
                                  str(as_dict(_x).get("name") or "")))
    if not svcs:
        return {"ok": True, "status": 200, "detail": "No services reported by OPNsense."}
    lines = []
    for s in svcs[:40]:
        if not isinstance(s, dict):
            continue
        emoji = "🟢" if s.get("running") else "⏹️"
        label = str(s.get("description") or s.get("name") or "?").strip()
        lines.append(f"{emoji} {label}")
    running = sum(1 for s in svcs if isinstance(s, dict) and s.get("running"))
    head = f"⚙️ {running}/{len(svcs)} services running"
    return {"ok": True, "status": 200, "detail": head + "\n" + "\n".join(lines)}


async def _restart_service_skill(host_row: dict, chip: dict, *,
                                 arg: Optional[str] = None,
                                 host_id: Optional[str] = None) -> dict:
    """DESTRUCTIVE (arg): restart ONE OPNsense service by name. Resolves the
    term against the live service list, then POST /api/core/service/restart/{id}.
    Never raises."""
    needle = str(arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no service given — say e.g. \"restart unbound\""}
    username, password = resolve_userpass(chip)
    base = resolve_base_url(host_row, chip)
    if not (username and password and base):
        return {"ok": False, "status": 0, "detail": "OPNsense credentials / URL not set"}
    nl = needle.lower()
    print(f"[opnsense] INFO opnsense_restart_service host={host_id} target={needle!r}")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True,
                                     auth=httpx.BasicAuth(username, password)) as cli:
            svc = await _post_json(cli, base, "/api/core/service/search")
            rows = as_list(as_dict(svc).get("rows"))
            match = None
            for s in rows:
                if not isinstance(s, dict):
                    continue
                if (nl == str(s.get("name") or "").lower()
                    or nl in str(s.get("description") or "").lower()
                    or nl in str(s.get("name") or "").lower()):
                    match = s
                    break
            if match is None:
                return {"ok": False, "status": 404,
                        "detail": f"no OPNsense service matched \"{needle}\""}
            sid = str(match.get("id") or match.get("name") or "").strip()
            label = str(match.get("description") or match.get("name") or needle).strip()
            rr = await cli.post(base + f"/api/core/service/restart/{sid}",
                                headers={"Accept": "application/json"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"restart failed: {type(e).__name__}: {e}"}
    if rr.status_code in (401, 403):
        return {"ok": False, "status": rr.status_code, "detail": "auth failed (check API key / secret)"}
    if rr.status_code in (200, 201, 202, 204):
        if host_id is not None:
            _data_cache.pop(cache_key(str(host_id), 0), None)
        return {"ok": True, "status": 200,
                "detail": f"🔄 Restarted the “{label}” service on OPNsense."}
    return {"ok": False, "status": rr.status_code,
            "detail": f"restart of “{label}” returned HTTP {rr.status_code}"}
