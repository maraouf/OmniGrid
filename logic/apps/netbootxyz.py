"""netboot.xyz per-app module (netbootxyz/webapp).

Encapsulates everything netboot.xyz-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic.

What this is
------------
netboot.xyz is a network-boot menu manager — a web UI that downloads + serves
iPXE boot menus / kernels over TFTP + HTTP so a machine can PXE-boot installers
and live OSes. The management webapp (``ghcr.io/netbootxyz/netbootxyz``, default
port 3000) has NO authentication and its dynamic data (local / remote boot
assets, menus) flows over **socket.io**, not a rich REST API. The reliable HTTP
surface is just:

    GET  /          — the web UI (reachability)
    GET  /version   — the running version (best-effort: JSON or a plain string)

So this module is a defensive STATUS + VERSION card, mirroring the no-auth
``ddns_updater`` shape: ``requires_api_key()`` is False — the editor only needs
the instance URL (the generic chip URL field, pointing at the webapp root) + a
cache TTL. The expanded card answers "is netboot.xyz up, and what version is it
running (is an update available)" at a glance.

    available         — reached + parsed
    version           — the running version (when /version exposes it)
    latest            — the latest version (when /version reports a remote)
    update_available  — version != latest (only when both are known)

AI / Telegram skills
--------------------
* ``netbootxyz_status`` — reachability + version (+ update-available).

Single-instance app (NOT fleet) — one card per pinned chip.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl)
from logic.coerce import as_dict, safe_float, safe_int

# Catalog template slugs handled by this module (the built-in template is
# `netboot-xyz`; `netbootxyz` covers an operator-renamed chip).
SLUGS: tuple[str, ...] = ("netboot-xyz", "netbootxyz")

DEFAULT_CACHE_TTL_S = 120
_data_cache: dict[str, tuple[float, dict]] = {}

# The netboot.xyz webapp serves NOTHING useful over plain HTTP — its only routes
# are `/`, `/netbootxyz-web.js` and `/public` (verified against the webapp
# source). Every stat lives behind its socket.io dashboard event: the client
# emits `getdash` and the server replies `renderdash` with a dashinfo blob
# (webapp version, LOCAL boot-menu version, the latest upstream menu release,
# and host CPU / memory). We drive socket.io's HTTP long-polling transport
# (Socket.IO 4.x → Engine.IO protocol v4) to fetch it — no websocket needed.
_SIO_PATH = "/socket.io/?EIO=4&transport=polling"
# Plain-text content type the EIO polling POST body expects.
_SIO_CT = {"content-type": "text/plain;charset=UTF-8"}
# How many times to poll for the renderdash reply (the server-side handler does
# a GitHub release lookup + systeminformation calls, so it lands after ~1-2s).
_SIO_POLL_TRIES = 6


def _parse_sid(txt: str) -> str:
    """Pull the session id out of an Engine.IO open packet. The polling body is
    one-or-more packets joined by the record-separator (0x1e); the open packet
    is ``0{"sid":...}``. Returns '' when absent."""
    for packet in (txt or "").split("\x1e"):
        if packet.startswith("0") and "{" in packet:
            try:
                obj = json.loads(packet[1:])
            except (ValueError, TypeError):
                continue
            sid = as_dict(obj).get("sid")
            if sid:
                return str(sid)
    return ""


def _extract_event(txt: str, name: str) -> Optional[Any]:
    """Find a Socket.IO EVENT packet (``42["<name>", <data>]``) in a polling
    body and return its ``<data>`` payload. ``None`` when the named event isn't
    present (caller keeps polling)."""
    for packet in (txt or "").split("\x1e"):
        if packet.startswith("42"):
            try:
                arr = json.loads(packet[2:])
            except (ValueError, TypeError):
                continue
            if isinstance(arr, list) and len(arr) >= 2 and arr[0] == name:
                return arr[1]
    return None


async def _probe_dash(cli: "httpx.AsyncClient", base: str) -> "Optional[dict]":
    """Fetch the netboot.xyz dashboard payload (``getdash`` → ``renderdash``)
    over socket.io HTTP long-polling. Returns the dashinfo dict, or None on any
    handshake / poll failure (the card then degrades to reachable-only)."""
    p = base + _SIO_PATH
    try:
        # 1) Handshake — the open packet carries the session id.
        r = await cli.get(p)
        if not (200 <= r.status_code < 300):
            return None
        sid = _parse_sid(r.text)
        if not sid:
            return None
        ps = f"{p}&sid={sid}"
        # 2) CONNECT to the default namespace (Socket.IO packet '40').
        await cli.post(ps, content="40", headers=_SIO_CT)
        # 3) Emit getdash (EVENT packet '42' + JSON arg array).
        await cli.post(ps, content='42["getdash"]', headers=_SIO_CT)
        # 4) Poll until renderdash lands (or we run out of tries).
        for _ in range(_SIO_POLL_TRIES):
            rr = await cli.get(ps)
            if not (200 <= rr.status_code < 300):
                break
            dash = _extract_event(rr.text, "renderdash")
            if isinstance(dash, dict):
                return dash
    except (httpx.HTTPError, OSError, ValueError):
        return None
    return None


def _shape_dash(dash: dict) -> dict:
    """Normalise the raw ``renderdash`` dashinfo blob into the card payload's
    stat fields. Every field is best-effort — a missing key just drops that
    stat. ``menuversion`` is the LOCAL installed boot-menu version;
    ``remotemenuversion`` is the latest upstream release tag."""
    d = as_dict(dash)
    web = str(d.get("webversion") or "").strip()
    menu = str(d.get("menuversion") or "").strip()
    remote = str(d.get("remotemenuversion") or "").strip()
    mem = as_dict(d.get("mem"))
    mem_total = safe_int(mem.get("total"))
    # systeminformation reports `active` as the genuinely-used memory (used
    # includes buffers/cache); fall back to `used` when active is absent.
    mem_used = safe_int(mem.get("active")) or safe_int(mem.get("used"))
    return {
        "version": web,                       # webapp version
        "menu_version": menu,                 # local installed boot-menu version
        "latest_menu_version": remote,        # latest upstream release tag
        "update_available": bool(menu and remote and menu != remote),
        "cpu_percent": round(safe_float(d.get("CPUpercent")), 1),
        "mem_used": mem_used,
        "mem_total": mem_total,
    }


SKILLS: tuple[dict, ...] = (
    {
        "id": "netbootxyz_status",
        "name": "netboot.xyz status",
        "ai_phrases": ("netboot status, netboot.xyz status, is netboot up, "
                       "pxe boot status, network boot server, what version is "
                       "netboot.xyz, is netboot.xyz reachable, netbootxyz health"),
        "destructive": False,
    },
)


def requires_api_key() -> bool:
    """False — the netboot.xyz webapp has NO authentication; the editor only
    needs the instance URL (its web UI root) + a cache TTL."""
    return False


# noinspection PyUnusedLocal
async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe the netboot.xyz web UI (``GET /``). No auth — ``candidate_key`` /
    ``payload`` are part of the generic route contract but unused. Returns
    ``{ok, detail, status}``."""
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    try:
        async with httpx.AsyncClient(verify=False, timeout=12.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/")
            if not (200 <= r.status_code < 400):
                return {"ok": False, "detail": f"HTTP {r.status_code}",
                        "status": r.status_code}
            dash = await _probe_dash(cli, base)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    menu = str(as_dict(dash).get("menuversion") or "").strip() if dash else ""
    detail = f"OK (boot menu {menu})" if menu else "OK (reachable)"
    return {"ok": True, "detail": detail, "status": 200}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Probe the netboot.xyz webapp for the expanded card. GETs ``/`` for
    reachability, then drives the socket.io ``getdash`` event for the real
    stats (webapp + boot-menu versions, update-available, host CPU / memory).
    Returns ``{available, version, menu_version, latest_menu_version,
    update_available, cpu_percent, mem_used, mem_total, fetched_at}``. Raises
    ``ValueError`` (base URL won't resolve) / ``RuntimeError`` (upstream error
    on the reachability GET — the socket.io stats are best-effort on top)."""
    now = time.time()
    # No-auth app — pass credential=True so the gate never raises on a missing
    # secret (it folds the URL-resolve + cache-miss-log shape shared with the
    # other fetch_data openers, so this isn't a structural twin of them).
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=True, log_tag="netbootxyz")
    if hit is not None:
        return hit
    url = base + "/"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url)
            if not (200 <= r.status_code < 400):
                print(f"[netbootxyz] error: fetch host={host_id} url={url} returned "
                      f"HTTP {r.status_code} (check the chip URL points at the "
                      f"netboot.xyz webapp root, e.g. http://host:3000)")
                raise RuntimeError(f"upstream returned HTTP {r.status_code} for {url}")
            dash = await _probe_dash(cli, base)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[netbootxyz] error: fetch host={host_id} url={url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    out: dict = {"available": True, "fetched_at": int(now)}
    out.update(_shape_dash(dash) if dash else {})
    print(f"[netbootxyz] INFO fetched host={host_id} "
          f"web={out.get('version') or '-'} menu={out.get('menu_version') or '-'} "
          f"latest={out.get('latest_menu_version') or '-'} "
          f"update={out.get('update_available')} dash={'ok' if dash else 'none'}")
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
        "menu_version": data.get("menu_version") or "",
        "latest_menu_version": data.get("latest_menu_version") or "",
        "update_available": bool(data.get("update_available")),
        "cpu_percent": safe_float(data.get("cpu_percent")),
        "mem_used": safe_int(data.get("mem_used")),
        "mem_total": safe_int(data.get("mem_total")),
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown id."""
    if skill_id == "netbootxyz_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    raise ValueError(f"unknown skill: {skill_id!r}")


# noinspection DuplicatedCode
# The live-fetch-then-format opening (print + try/fetch_data force=True +
# ValueError/RuntimeError guard) is the deliberate per-app status-skill twin
# shared with every other module (radarr / ddns / … — CLAUDE.md). The formatted
# output is app-specific, so it stays inline.
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch + format the reachability + version summary. Never
    raises."""
    print(f"[netbootxyz] INFO netbootxyz_status host={host_id} svc_idx={service_idx} "
          f"(live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[netbootxyz] warning: netbootxyz_status host={host_id} could not "
              f"fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    web = str(data.get("version") or "").strip()
    menu = str(data.get("menu_version") or "").strip()
    latest = str(data.get("latest_menu_version") or "").strip()
    if menu:
        lines = [f"🥾 netboot.xyz is up — boot menu {menu}"]
    elif web:
        lines = [f"🥾 netboot.xyz is up — webapp {web}"]
    else:
        lines = ["🥾 netboot.xyz is up and reachable."]
    if web and menu:
        lines.append(f"🧰 Webapp {web}")
    if data.get("update_available") and latest:
        lines.append(f"⬆️ Boot-menu update available: {latest} (installed {menu})")
    cpu = safe_float(data.get("cpu_percent"))
    mem_used = safe_int(data.get("mem_used"))
    mem_total = safe_int(data.get("mem_total"))
    if mem_total:
        pct = round(mem_used / mem_total * 100)
        lines.append(f"📊 CPU {cpu:.0f}% · RAM {pct}%")
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "version": web, "menu_version": menu, "latest_menu_version": latest,
            "update_available": bool(data.get("update_available"))}
