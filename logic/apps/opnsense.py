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

# Per-(host_id, service_idx) previous interface byte-counters for throughput
# rate-diffing. ``traffic/interface`` returns CUMULATIVE per-NIC byte counters
# (not rates), so we diff consecutive samples — {ck: (epoch, {iface: (rx, tx)})}.
_iface_counters: dict[str, tuple[float, dict]] = {}

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
        "id": "opnsense_interfaces",
        "name": "OPNsense interface throughput",
        "ai_phrases": ("opnsense interfaces, interface throughput, uplink and "
                       "downlink per interface, how much bandwidth per nic, "
                       "wan lan throughput, interface upload download speed, "
                       "which interface is busiest, opnsense bandwidth per port"),
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


# noinspection DuplicatedCode
async def _post_json(cli: httpx.AsyncClient, base: str, path: str,
                     body: Optional[dict] = None) -> Any:
    """One tolerated POST → parsed JSON, or ``None`` on any failure. OPNsense's
    grid endpoints (``*/search``) are POST with a JSON body; some builds also
    accept GET. Used by the search-style fetches that returned nothing on GET."""
    try:
        r = await cli.post(base + path, json=(body or {}),
                           headers={"Accept": "application/json"})
    except (httpx.HTTPError, OSError):
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except (ValueError, TypeError):
        return None


async def _probe_raw(cli: httpx.AsyncClient, base: str, method: str, path: str,
                     body: Optional[dict] = None) -> dict:
    """Debug-capturing single request → ``{status, ok, snippet, json}``.

    Unlike ``_get_json`` / ``_post_json`` (which discard the status + body on a
    non-200 so the card can't say WHY an endpoint returned nothing), this keeps
    the HTTP status code + a short raw-body snippet so the per-instance debug
    panel can surface exactly what the firewall answered for each diagnostics /
    search endpoint. ``json`` is the parsed body on a 200, else ``None``."""
    try:
        if method == "POST":
            r = await cli.post(base + path, json=(body or {}),
                               headers={"Accept": "application/json"})
        else:
            r = await cli.get(base + path, headers={"Accept": "application/json"})
    except (httpx.HTTPError, OSError) as e:
        return {"status": 0, "ok": False, "snippet": type(e).__name__, "json": None}
    snippet = (r.text or "").strip().replace("\n", " ")[:160]
    parsed = None
    if r.status_code == 200:
        try:
            parsed = r.json()
        except (ValueError, TypeError):
            parsed = None
    return {"status": r.status_code, "ok": r.status_code == 200,
            "snippet": snippet, "json": parsed}


def _dbg_entry(label: str, method: str, path: str, probe: dict, rows: int) -> dict:
    """One structured row for the ``_debug.endpoints`` list the drawer renders.
    ``probe`` is a ``_probe_raw`` result; ``rows`` is the parsed row count (or
    -1 when not applicable)."""
    return {"label": label, "method": method, "path": path,
            "status": safe_int(probe.get("status")), "rows": int(rows),
            "ok": bool(probe.get("ok")),
            "snippet": str(probe.get("snippet") or "")}


# Actionable privilege hint per problem-endpoint category — what to grant the
# OPNsense API user (System → Access → Users → edit → Effective Privileges) when
# the endpoint 403s. Homarr only uses Dashboard/Diagnostics:System endpoints, so
# a key scoped for Homarr can read CPU/mem but NOT these three.
_PRIV_HINTS = {
    "services": "grant the API user the 'Status: Services' privilege",
    "pf": "grant the API user the 'Diagnostics: pf info' / 'Firewall: states' privilege",
    "dhcp": "grant the API user the 'Services: Kea DHCP' (or legacy 'Status: DHCPv4 leases') privilege",
}
_CAT_NAMES = {"services": "Services", "pf": "Firewall states", "dhcp": "DHCP leases"}


def _diagnose_endpoints(dbg: list) -> str:
    """Derive an actionable hint from the per-endpoint diagnostics so the card
    can SAY why a count reads 0 (instead of the operator having to share logs).
    Fires only on UNAMBIGUOUS failure signals — 401/403 (privilege), all-404
    (endpoint not on this version), or all-unreachable — NOT on a reachable
    200-with-0-rows (which the per-endpoint snippet list already exposes and
    could be a legitimate zero). '' when nothing actionable."""
    cats: dict[str, list] = {}
    for e in dbg:
        lbl = str(e.get("label") or "")
        cat = "pf" if lbl.startswith("pf") else lbl
        cats.setdefault(cat, []).append(e)
    out: list[str] = []
    for cat, entries in cats.items():
        # Skip a category that got real data on any attempt.
        if any(e.get("ok") and safe_int(e.get("rows")) > 0 for e in entries):
            continue
        statuses = [safe_int(e.get("status")) for e in entries]
        if not statuses:
            continue
        nm = _CAT_NAMES.get(cat, cat)
        if any(s in (401, 403) for s in statuses):
            out.append(f"{nm}: HTTP 403 — {_PRIV_HINTS.get(cat, 'grant the API user the matching privilege')}")
        elif all(s == 404 for s in statuses):
            out.append(f"{nm}: HTTP 404 — this endpoint isn't on your OPNsense version")
        elif all(s == 0 for s in statuses):
            out.append(f"{nm}: unreachable (connection / TLS error)")
    return " · ".join(out)


def _extract_grid_rows(raw: Any) -> list:
    """Pull the bootgrid ``rows`` out of an OPNsense ``*/search`` response,
    tolerant of every shape builds have used: a top-level ``{rows: [...]}``, a
    bare list, or a nested ``{service: {rows}}`` / ``{leases: [...]}`` block.
    Returns a list of dict rows ([] when none)."""
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    d = as_dict(raw)
    for key in ("rows", "leases", "items"):
        rows = [r for r in as_list(d.get(key)) if isinstance(r, dict)]
        if rows:
            return rows
    for nest_key in ("service", "services", "data"):
        nested = as_dict(d.get(nest_key))
        rows = [r for r in as_list(nested.get("rows")) if isinstance(r, dict)]
        if rows:
            return rows
    return []


async def _fetch_services(cli: httpx.AsyncClient, base: str,
                          dbg: Optional[list] = None) -> Any:
    """Running-services grid. OPNsense's ``/api/core/service/search`` shape has
    drifted across versions: some builds want a POST bootgrid body, some answer
    a bare GET, some need the explicit ``current``/``rowCount`` query. Try all
    three and return the first that carries rows. When NONE return rows, log the
    status of each attempt (privilege / endpoint diagnosis) and return {}.
    ``dbg`` (when supplied) collects a structured per-attempt diagnostic row for
    the drawer debug panel."""
    body = {"current": 1, "rowCount": -1, "sort": {}, "searchPhrase": ""}
    attempts: tuple[tuple[str, str, Optional[dict]], ...] = (
        ("POST", "/api/core/service/search", body),
        ("GET", "/api/core/service/search", None),
        ("GET", "/api/core/service/search?current=1&rowCount=1000", None),
    )
    diag: list[str] = []
    for method, path, b in attempts:
        probe = await _probe_raw(cli, base, method, path, b)
        rows = _extract_grid_rows(probe.get("json"))
        if dbg is not None:
            dbg.append(_dbg_entry("services", method, path, probe, len(rows)))
        if rows:
            return {"rows": rows}
        if not probe.get("ok"):
            diag.append(f"{method} {path}=HTTP{probe.get('status')}")
        else:
            keys = ",".join(sorted(as_dict(probe.get("json")).keys())[:6]) or "no-keys"
            diag.append(f"{method} {path}=0rows({keys})")
    print("[opnsense] warning: service search returned no rows — "
          + "; ".join(diag) + " (check the API user's 'Status: Services' privilege)")
    return {}


async def _fetch_pf_states(cli: httpx.AsyncClient, base: str,
                           dbg: Optional[list] = None) -> "tuple[int, int]":
    """pf state-table ``(current, limit)``. Tries ``firewall/pf_states`` (a
    ``{current, limit}`` summary OR a ``{total, rows}`` list) then the
    ``firewall/pf_statistics`` counters endpoint (``current_entries`` /
    ``state_limit`` under various nestings). 0/0 when neither responds. ``dbg``
    collects a per-endpoint diagnostic row for the drawer debug panel."""
    p1 = await _probe_raw(cli, base, "GET", "/api/diagnostics/firewall/pf_states")
    pf = as_dict(p1.get("json"))
    cur = (safe_int(pf.get("current")) or safe_int(pf.get("total"))
           or len(as_list(pf.get("rows"))))
    lim = safe_int(pf.get("limit"))
    if dbg is not None:
        dbg.append(_dbg_entry("pf_states", "GET",
                              "/api/diagnostics/firewall/pf_states", p1,
                              cur if cur else -1))
    if cur or lim:
        return cur, lim
    # Fallback: the statistics endpoint (counter-style shape).
    p2 = await _probe_raw(cli, base, "GET", "/api/diagnostics/firewall/pf_statistics")
    st = as_dict(p2.get("json"))
    # The count + limit live under a few possible keys / a nested 'state' block.
    nest = as_dict(st.get("state")) or as_dict(st.get("states")) or st
    cur = (safe_int(nest.get("current_entries")) or safe_int(nest.get("current"))
           or safe_int(nest.get("entries")) or safe_int(st.get("current_entries")))
    lim = (safe_int(nest.get("limit")) or safe_int(nest.get("state_limit"))
           or safe_int(st.get("state_limit")))
    if dbg is not None:
        dbg.append(_dbg_entry("pf_statistics", "GET",
                              "/api/diagnostics/firewall/pf_statistics", p2,
                              cur if cur else -1))
    return cur, lim


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
    rows = _extract_grid_rows(svc)
    out: list = []
    running = 0
    for s in rows:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or s.get("id") or "?").strip()
        desc = str(s.get("description") or "").strip()
        # ``running`` is normally 0/1, but some builds report a string status
        # ("running" / "stopped") instead — treat both as the run flag.
        is_run = bool(safe_int(s.get("running"))) or (
            str(s.get("status") or "").strip().lower() in ("running", "up", "1", "true"))
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
            # Everything else is tolerated + fetched in parallel. NOTE: the
            # SystemController actions are camelCase in the URL
            # (systemResources / systemTime / systemTemperature) — the
            # snake_case forms return the login HTML, which silently zeroed
            # memory / load / uptime. service/search is a GET (an empty POST
            # body returned no rows). DHCP is Kea-first (the default backend on
            # 25.x/26.x) with an ISC fallback. traffic/interface gives
            # cumulative per-NIC byte counters we rate-diff for throughput.
            # Per-endpoint diagnostics for the 3 problem fetches (services /
            # pf-states / DHCP) — captured so the drawer debug panel can show
            # exactly what the firewall answered (status + body snippet) when a
            # count reads 0 despite the data being present.
            dbg: list[dict] = []
            res, stime, temp, gw, svc, pf, leases, traffic, activity = await asyncio.gather(
                _get_json(cli, base, "/api/diagnostics/system/systemResources"),
                _get_json(cli, base, "/api/diagnostics/system/systemTime"),
                _get_json(cli, base, "/api/diagnostics/system/systemTemperature"),
                _get_json(cli, base, "/api/routes/gateway/status"),
                _fetch_services(cli, base, dbg),
                _fetch_pf_states(cli, base, dbg),
                _fetch_dhcp_leases(cli, base, dbg),
                _get_json(cli, base, "/api/diagnostics/traffic/interface"),
                _get_json(cli, base, "/api/diagnostics/activity/getActivity"),
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
    # systemResources returns the login page (zeroed memory) on some boxes /
    # privilege sets — fall back to the FreeBSD `top` Mem: line from getActivity,
    # the same header source CPU% reads from (proven readable on this host).
    if mem_total <= 0:
        mem_used, mem_total = _mem_from_activity(activity)
    mem_percent = round(mem_used / mem_total * 100, 1) if mem_total else 0.0
    stime_d = as_dict(stime)
    load_1m, load_5m, load_15m = _loadavg(stime_d)
    # Same fallback for load — systemTime can return the login page (zeroed
    # load); getActivity's header carries the load averages too.
    if load_1m <= 0 and load_5m <= 0 and load_15m <= 0:
        load_1m, load_5m, load_15m = _loadavg_from_activity(activity)
    uptime_s = _uptime_seconds(stime_d)
    gw_list, gw_total, gw_online, worst_gw = _gateways_shape(as_dict(gw))
    svc_list, svc_total, svc_running = _services_shape(as_dict(svc))
    # pf state-table count + limit — _fetch_pf_states already tries pf_states
    # (summary / list shapes) then the pf_statistics counters fallback.
    pf_current, pf_limit = pf
    leases_total = safe_int(leases)
    temp_max_c = _temp_max(temp)
    cpu_percent = _cpu_from_activity(activity)
    # CPU% has no clean snapshot endpoint; when getActivity isn't readable
    # (privilege not granted) fall back to a load-vs-cores proxy.
    cores = _activity_cores(activity)
    if cpu_percent <= 0 < cores:
        cpu_percent = round(min(100.0, load_1m / cores * 100), 1)
    net_rx_bps, net_tx_bps, iface_list = _throughput(host_id, service_idx,
                                                     as_dict(traffic), now)
    out: dict[str, Any] = {
        "available": True,
        "version": version,
        "update_available": update_available,
        "cpu_percent": cpu_percent,
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
        "net_rx_bps": net_rx_bps,
        "net_tx_bps": net_tx_bps,
        "interfaces": iface_list,
        # Per-endpoint diagnostics for the services / pf-states / DHCP fetches —
        # surfaced in the drawer debug panel so a "0" count is traceable to the
        # firewall's actual HTTP status + body (privilege / endpoint / backend).
        # ``hint`` self-diagnoses the unambiguous failures (403 → which privilege
        # to grant) so the card SAYS why a count is 0 without sharing logs.
        "_debug": {"endpoints": dbg, "hint": _diagnose_endpoints(dbg)},
        "fetched_at": int(now),
    }
    print(f"[opnsense] INFO fetched host={host_id} v={version} "
          f"upd={update_available} cpu={cpu_percent}% mem={mem_percent}% "
          f"load={out['load_1m']} gw={gw_online}/{gw_total} "
          f"svc={svc_running}/{svc_total} pf={pf_current} leases={leases_total} "
          f"net={net_rx_bps}/{net_tx_bps}B/s temp={temp_max_c}C")
    _data_cache[ck] = (now, out)
    return out


async def _fetch_dhcp_leases(cli: httpx.AsyncClient, base: str,
                             dbg: Optional[list] = None) -> int:
    """Active DHCP lease count. OPNsense 25.x/26.x defaults to **Kea** DHCP, so
    try ``/api/kea/leases4/search`` first and fall back to the legacy **ISC**
    ``/api/dhcpv4/leases/search_lease`` (and its camelCase alias). All are GET
    bootgrid endpoints. Active-state differs by backend: Kea uses a numeric
    ``state`` (``"0"`` = active), ISC a string (``"active"``); both are matched
    below. Falls back to the row count / upstream ``total`` when no per-row state
    is present. 0 on any failure. ``dbg`` collects a per-attempt diagnostic row
    for the drawer debug panel."""
    active_words = ("active", "online", "bound", "0", "")
    body = {"current": 1, "rowCount": -1, "searchPhrase": ""}
    # Kea grid is GET on most builds but POST on some; ISC's real method name is
    # camelCase ``searchLease`` (snake_case kept as a last-ditch alias).
    attempts: tuple[tuple[str, str], ...] = (
        ("GET", "/api/kea/leases4/search"),
        ("POST", "/api/kea/leases4/search"),
        ("GET", "/api/dhcpv4/leases/searchLease"),
        ("GET", "/api/dhcpv4/leases/search_lease"),
    )
    diag: list[str] = []
    for method, path in attempts:
        probe = await _probe_raw(cli, base, method, path,
                                 body if method == "POST" else None)
        rows = _extract_grid_rows(probe.get("json"))
        total = safe_int(as_dict(probe.get("json")).get("total"))
        if dbg is not None:
            dbg.append(_dbg_entry("dhcp", method, path, probe,
                                  len(rows) if rows else (total or -1)))
        if rows:
            active = sum(
                1 for r in rows
                if str(r.get("state") or r.get("status") or "active").strip().lower()
                in active_words
            )
            return active or len(rows)
        if total:
            return total
        if not probe.get("ok"):
            diag.append(f"{method} {path}=HTTP{probe.get('status')}")
        else:
            keys = ",".join(sorted(as_dict(probe.get("json")).keys())[:6]) or "no-keys"
            diag.append(f"{method} {path}=0rows({keys})")
    print("[opnsense] warning: DHCP lease search returned no rows — "
          + "; ".join(diag) + " (check the Kea/ISC backend + API user privilege)")
    return 0


def _cpu_from_activity(activity: Any) -> float:
    """CPU% from ``/api/diagnostics/activity/getActivity`` — there is no clean
    snapshot CPU endpoint, so parse the ``top``-style header rows the same way
    Homarr / homepage do: find the ``CPU: … X% idle`` line and return
    ``100 - idle``. 0.0 when the activity payload isn't readable (the privilege
    may not be granted) so the caller can fall back to a load-based proxy."""
    headers = as_list(as_dict(activity).get("headers"))
    import re as _re  # noqa: PLC0415
    for h in headers:
        m = _re.search(r"(?P<idle>[0-9.]+)%\s*idle", str(h or ""))
        if m:
            try:
                return round(max(0.0, min(100.0, 100.0 - float(m.group("idle")))), 1)
            except (ValueError, TypeError):
                return 0.0
    return 0.0


def _activity_cores(activity: Any) -> int:
    """Best-effort CPU-core count from the getActivity header rows (``N CPUs:``)
    — used to scale the load-average CPU% fallback. 0 when not found."""
    headers = as_list(as_dict(activity).get("headers"))
    import re as _re  # noqa: PLC0415
    for h in headers:
        m = _re.search(r"(?P<n>\d+)\s+CPUs?:", str(h or ""))
        if m:
            return safe_int(m.group("n"))
    return 0


def _loadavg_from_activity(activity: Any) -> "tuple[float, float, float]":
    """Load averages from the ``getActivity`` ``top``-style header rows — the
    same proven-readable source CPU% comes from. The first header line reads
    ``... load averages: 0.45, 0.52, 0.48 up 5+12:34:56 ...``. Used as the
    fallback when ``systemTime`` returns the login page (zeroed load). Zeros
    when no load line is present."""
    headers = as_list(as_dict(activity).get("headers"))
    import re as _re  # noqa: PLC0415
    for h in headers:
        m = _re.search(r"load averages?:\s*(?P<l1>[0-9.]+)[,\s]+(?P<l5>[0-9.]+)"
                       r"[,\s]+(?P<l15>[0-9.]+)", str(h or ""), _re.IGNORECASE)
        if m:
            return (safe_float(m.group("l1")), safe_float(m.group("l5")),
                    safe_float(m.group("l15")))
    return 0.0, 0.0, 0.0


def _top_size_bytes(num: str, unit: str) -> int:
    """A FreeBSD ``top`` size token (``412M`` / ``5G`` / ``128K``) → bytes."""
    mult = {"": 1, "B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
    try:
        return int(float(num) * mult.get(unit.upper(), 1))
    except (ValueError, TypeError):
        return 0


def _mem_from_activity(activity: Any) -> "tuple[int, int]":
    """Memory ``(used_bytes, total_bytes)`` parsed from the ``getActivity``
    ``Mem:`` header row — the FreeBSD ``top`` fallback for when
    ``systemResources`` returns the login page (zeroed memory). The line reads
    e.g. ``Mem: 412M Active, 1024M Inact, 64M Laundry, 890M Wired, 128M Buf,
    5400M Free``. Total is the sum of the physical categories (Buf excluded —
    it overlaps the others); ``available`` follows the FreeBSD convention
    (Free + Inact + Laundry + Cache are reclaimable), so ``used = total -
    available``. ``(0, 0)`` when no ``Mem:`` line is present."""
    headers = as_list(as_dict(activity).get("headers"))
    import re as _re  # noqa: PLC0415
    for h in headers:
        s = str(h or "")
        if not _re.match(r"\s*Mem:", s, _re.IGNORECASE):
            continue
        total = 0
        avail = 0
        for num, unit, cat in _re.findall(
            r"(?P<num>[0-9.]+)\s*(?P<unit>[KMGTB]?)\s+(?P<cat>[A-Za-z]+)", s):
            cl = cat.lower()
            if cl == "buf":  # buffer overlaps the other categories — don't sum
                continue
            b = _top_size_bytes(num, unit)
            total += b
            if cl in ("free", "inact", "laundry", "cache"):
                avail += b
        if total:
            return max(0, total - avail), total
    return 0, 0


def _throughput(host_id: str, service_idx: int, traffic: dict,
                now: float) -> "tuple[int, int, list]":
    """Rate-diff ``traffic/interface``'s CUMULATIVE per-NIC byte counters into
    bytes-per-second. Returns ``(total_rx_bps, total_tx_bps, per_iface_list)``
    where each iface row is ``{name, rx_bps, tx_bps}``. The first sample (no
    predecessor) yields zeros — the next tick establishes the baseline.
    Counter resets / reboots (negative delta) and absurd gaps are skipped, never
    synthesized as a spike (the host_net_sampler discipline)."""
    ck = cache_key(host_id, service_idx)
    ifaces = as_dict(traffic.get("interfaces"))
    cur: dict = {}
    for key, info in ifaces.items():
        d = as_dict(info)
        cur[key] = (safe_int(d.get("bytes received")), safe_int(d.get("bytes transmitted")))
    prev = _iface_counters.get(ck)
    _iface_counters[ck] = (now, cur)
    if not prev:
        return 0, 0, []
    prev_ts, prev_cnts = prev
    dt = now - prev_ts
    if dt < 1 or dt > 900:  # too-short / stale gap — skip this rate
        return 0, 0, []
    total_rx = 0
    total_tx = 0
    rows: list = []
    for key, (rx, tx) in cur.items():
        if key not in prev_cnts:
            continue
        prx, ptx = prev_cnts[key]
        d_rx = rx - prx
        d_tx = tx - ptx
        if d_rx < 0 or d_tx < 0:  # counter reset / reboot — skip
            continue
        rx_bps = int(d_rx / dt)
        tx_bps = int(d_tx / dt)
        total_rx += rx_bps
        total_tx += tx_bps
        name = str(as_dict(ifaces.get(key)).get("name") or key).strip() or key
        rows.append({"name": name, "rx_bps": rx_bps, "tx_bps": tx_bps})
    # Busiest interfaces first (rx+tx), capped for the card.
    rows.sort(key=lambda r: (r["rx_bps"] + r["tx_bps"]), reverse=True)
    return total_rx, total_tx, rows[:_MAX_ROWS]


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "version": data.get("version") or "",
        "update_available": bool(data.get("update_available")),
        "cpu_percent": safe_float(data.get("cpu_percent")),
        "mem_percent": safe_float(data.get("mem_percent")),
        "load_1m": safe_float(data.get("load_1m")),
        "gateways_online": safe_int(data.get("gateways_online")),
        "gateways_total": safe_int(data.get("gateways_total")),
        "services_running": safe_int(data.get("services_running")),
        "net_rx_bps": safe_int(data.get("net_rx_bps")),
        "net_tx_bps": safe_int(data.get("net_tx_bps")),
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
    if skill_id == "opnsense_interfaces":
        return await _interfaces_skill(host_row, chip, host_id=host_id, service_idx=service_idx)
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


def _fmt_bps(n: Any) -> str:
    """Render a bytes-per-second rate as human-readable bandwidth
    (B/s · KB/s · MB/s · GB/s, decimal/1000). ``0`` for non-positive."""
    b = float(safe_int(n))
    if b <= 0:
        return "0 B/s"
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if b < 1000:
            return f"{b:.0f} {unit}" if unit == "B/s" else f"{b:.1f} {unit}"
        b /= 1000.0
    return f"{b:.1f} TB/s"


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
        f"🖥️ CPU: {safe_float(data.get('cpu_percent'))}%"
        + f" · 🧠 Memory: {safe_float(data.get('mem_percent'))}%"
        + (f" ({_fmt_gib(data.get('mem_used'))} / {_fmt_gib(data.get('mem_total'))})"
           if safe_int(data.get('mem_total')) else "")
        + f" · ⚖️ load {data.get('load_1m')} / {data.get('load_5m')} / {data.get('load_15m')}",
        f"🔥 Firewall states: {safe_int(data.get('pf_states_current')):,}"
        + (f" / {safe_int(data.get('pf_states_limit')):,}" if safe_int(data.get('pf_states_limit')) else "")
        + f" · 📇 DHCP leases: {safe_int(data.get('dhcp_leases'))}",
    ]
    if safe_int(data.get("net_rx_bps")) or safe_int(data.get("net_tx_bps")):
        lines.append(f"🌐 Throughput: ↓ {_fmt_bps(data.get('net_rx_bps'))} · "
                     f"↑ {_fmt_bps(data.get('net_tx_bps'))}")
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
    items: list[dict] = []
    for s in svcs[:40]:
        if not isinstance(s, dict):
            continue
        running_now = bool(s.get("running"))
        emoji = "🟢" if running_now else "⏹️"
        name = str(s.get("name") or "").strip()
        label = str(s.get("description") or name or "?").strip()
        lines.append(f"{emoji} {label}")
        row: dict = {"title": label,
                     "subtitle": "running" if running_now else "stopped"}
        if name:
            # Per-row 🔄 Restart button → bounce THIS service (DESTRUCTIVE — a
            # brief interruption; the SPA confirms first). The arg is the service
            # name (exact match in the restart skill's resolver).
            row["row_action"] = {
                "skill_id": "opnsense_restart_service", "arg": name,
                "icon": "rotate-cw", "destructive": True,
                "confirm_i18n": "apps.opnsense.restart_confirm",
                "title_i18n": "apps.opnsense.restart_row"}
        items.append(row)
    running = sum(1 for s in svcs if isinstance(s, dict) and s.get("running"))
    head = f"⚙️ {running}/{len(svcs)} services running"
    out: dict = {"ok": True, "status": 200, "detail": head + "\n" + "\n".join(lines)}
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = "apps.opnsense.services_count"
    return out


# noinspection DuplicatedCode
async def _interfaces_skill(host_row: dict, chip: dict, *,
                            host_id: Optional[str] = None,
                            service_idx: Optional[int] = None) -> dict:
    """Read-only: per-interface uplink (↑ tx) / downlink (↓ rx) throughput.

    Throughput is a RATE diffed from two cumulative ``traffic/interface``
    samples, so a cold invocation (no prior baseline) yields an empty list on
    the first fetch — we then take a second sample ~2s later to produce real
    rates. Renders rich items (busiest-first) + a text block for AI / Telegram.
    Never raises."""
    print(f"[opnsense] INFO opnsense_interfaces host={host_id} (live fetch)")
    hid = str(host_id or "")
    sidx = int(service_idx or 0)
    try:
        data = await fetch_data(host_row, chip, host_id=hid, service_idx=sidx, force=True)
        ifaces = as_list(data.get("interfaces"))
        if not ifaces:
            # No baseline yet — sample again so the diff produces real rates.
            await asyncio.sleep(2)
            data = await fetch_data(host_row, chip, host_id=hid, service_idx=sidx, force=True)
            ifaces = as_list(data.get("interfaces"))
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "detail": str(e), "status": 0}
    if not ifaces:
        return {"ok": True, "status": 200,
                "detail": "No interface throughput reported by OPNsense "
                          "(the traffic diagnostics endpoint may be unsupported "
                          "or warming up — try again in a few seconds)."}
    total_rx = safe_int(data.get("net_rx_bps"))
    total_tx = safe_int(data.get("net_tx_bps"))
    lines = [f"🌐 Total: ↓ {_fmt_bps(total_rx)} · ↑ {_fmt_bps(total_tx)}"]
    items: list[dict] = []
    for nic in ifaces:
        if not isinstance(nic, dict):
            continue
        name = str(nic.get("name") or "?").strip() or "?"
        rx = safe_int(nic.get("rx_bps"))
        tx = safe_int(nic.get("tx_bps"))
        lines.append(f"🔌 {name}: ↓ {_fmt_bps(rx)} · ↑ {_fmt_bps(tx)}")
        items.append({"title": name,
                      "subtitle": f"↓ {_fmt_bps(rx)}  ·  ↑ {_fmt_bps(tx)}"})
    out: dict = {"ok": True, "status": 200, "detail": "\n".join(lines)}
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = "apps.opnsense.interfaces_count"
    return out


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
            svc = await _get_json(cli, base, "/api/core/service/search?current=1&rowCount=1000")
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
