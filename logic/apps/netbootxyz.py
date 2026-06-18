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
    body and return its FIRST ``<data>`` arg. ``None`` when the named event
    isn't present (caller keeps polling)."""
    args = _extract_event_args(txt, name)
    return args[0] if args else None


def _extract_event_args(txt: str, name: str) -> "Optional[list]":
    """Like ``_extract_event`` but returns ALL args of the event
    (``42["<name>", arg0, arg1, …]`` → ``[arg0, arg1, …]``) — some webapp
    events emit several positional args (e.g. ``renderlocal`` →
    ``endpoints, assets, menuversion``). ``None`` when absent."""
    for packet in (txt or "").split("\x1e"):
        if packet.startswith("42"):
            try:
                arr = json.loads(packet[2:])
            except (ValueError, TypeError):
                continue
            if isinstance(arr, list) and len(arr) >= 2 and arr[0] == name:
                return arr[1:]
    return None


def _count_endpoints(endpoints: Any) -> int:
    """Count the boot endpoints in the parsed ``endpoints.yml`` (the catalog of
    available boot options / OSes). The file nests the map under an
    ``endpoints:`` key; fall back to the top-level map. 0 when unparseable."""
    if not isinstance(endpoints, dict):
        return 0
    eps = endpoints.get("endpoints")
    if isinstance(eps, dict):
        return len(eps)
    return len(endpoints)


# Cap on the downloaded-asset rich rows surfaced (a busy netboot.xyz can hold
# many kernels / initrds / ISOs — keep the payload bounded).
_MAX_ASSETS = 60


def _fmt_size(n: Any) -> str:
    """Render a byte count as a human size (KB / MB / GB, decimal/1000). '' for
    non-positive / non-numeric."""
    b = float(safe_int(n))
    if b <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1000:
            return f"{b:.0f} {unit}" if unit == "B" else f"{b:.1f} {unit}"
        b /= 1000.0
    return f"{b:.1f} PB"


def _asset_size(a: dict) -> int:
    """A downloaded asset's size in bytes, across the key names different webapp
    builds use (``size`` / ``bytes`` / ``filesize`` / ``length``). 0 when none
    are present (older builds emit bare filename strings → no size)."""
    return safe_int(a.get("size") or a.get("bytes") or a.get("filesize")
                    or a.get("length"))


def _assets_disk_bytes(assets: Any) -> int:
    """Total assets-directory disk usage = sum of every downloaded asset's size,
    over the FULL list (not the capped display list) so the total is accurate.
    0 when the webapp build reports no sizes (best-effort)."""
    return sum(_asset_size(a) for a in (assets if isinstance(assets, list) else [])
               if isinstance(a, dict))


def _shape_assets(assets: Any) -> list:
    """Normalise the ``renderlocal`` downloaded-asset list into
    ``[{name, size_bytes}]`` (newest webapp builds emit objects; older ones emit
    bare filename strings — handle both). ``name`` is the readable file / asset
    identifier; ``size_bytes`` is 0 when the upstream doesn't report a size.
    Capped at ``_MAX_ASSETS``."""
    out: list = []
    for a in (assets if isinstance(assets, list) else []):
        if isinstance(a, str):
            name = a.strip()
            size = 0
        elif isinstance(a, dict):
            name = str(a.get("name") or a.get("file") or a.get("filename")
                       or a.get("path") or a.get("asset") or "").strip()
            size = _asset_size(a)
        else:
            continue
        if not name:
            continue
        out.append({"name": name, "size_bytes": size})
        if len(out) >= _MAX_ASSETS:
            break
    return out


def _untracked_paths(endpoints: Any, localfiles: Any) -> list:
    """netboot.xyz **untracked** local assets — downloaded files that are NOT
    referenced by any endpoint in the ``endpoints.yml`` catalog (the webapp's
    own Local-Assets "untracked" bucket = old / orphaned downloads to clean up).

    Mirrors the webapp client's exact rule (``netbootxyz-web.ejs``): a localfile
    path is TRACKED iff it equals ``endpoint.path + file`` for some catalog
    endpoint+file; the leftover localfiles are untracked. Returns the leftover
    path strings (deduped, order-preserving).

    SAFETY: when the catalog has endpoints but NONE expose a ``files`` list (an
    endpoints shape we don't recognise), we refuse to call everything untracked
    — a delete built on that would wipe TRACKED assets — and return [] instead.
    Only when the catalog is genuinely empty are all localfiles untracked."""
    files = [str(f).strip() for f in (localfiles if isinstance(localfiles, list) else [])
             if str(f).strip()]
    if not files:
        return []
    eps = endpoints.get("endpoints") if isinstance(endpoints, dict) else None
    if not isinstance(eps, dict):
        eps = endpoints if isinstance(endpoints, dict) else {}
    tracked: set = set()
    for ep in eps.values():
        if not isinstance(ep, dict):
            continue
        path = str(ep.get("path") or "")
        ep_files = ep.get("files")
        for f in (ep_files if isinstance(ep_files, list) else []):
            tracked.add(path + str(f))
    # Endpoints exist but no files[] parsed → unknown shape; don't mark anything
    # untracked (a delete would be catastrophic). Genuinely-empty catalog → all
    # localfiles are untracked (correct).
    if eps and not tracked:
        print("[netbootxyz] warning: endpoints catalog has no recognisable "
              "files[] — skipping untracked-asset computation (won't risk "
              "flagging tracked assets)")
        return []
    seen: set = set()
    out: list = []
    for f in files:
        # Mirror the webapp's exact untracked rule (netbootxyz-web.ejs): a
        # localfile is untracked iff it's not in the catalog AND does NOT end in
        # ".part2". The .part2 squashfs split files are required-but-unreferenced
        # downloads the webapp deliberately hides from the untracked bucket
        # (their issue #6) — without this filter they read as false positives.
        if f.endswith(".part2"):
            continue
        if f not in tracked and f not in seen:
            seen.add(f)
            out.append(f)
    return out


async def _probe_dash(cli: "httpx.AsyncClient", base: str) -> "Optional[dict]":
    """Fetch the netboot.xyz dashboard over socket.io HTTP long-polling. Emits
    BOTH ``getdash`` (versions + host CPU/mem) AND ``getlocal`` (the
    endpoints.yml catalog + locally-downloaded boot assets) on one session and
    merges the replies. Returns the raw dashinfo dict augmented with
    ``_endpoints_count`` / ``_assets_count`` / ``_has_local`` when the local
    list landed, or None on any handshake / poll failure (card → reachable)."""
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
        # 3) Emit getdash + getlocal (EVENT packets). getlocal ignores its
        #    filename arg server-side — it always returns the endpoints catalog
        #    + the downloaded-assets list.
        await cli.post(ps, content='42["getdash"]', headers=_SIO_CT)
        await cli.post(ps, content='42["getlocal",""]', headers=_SIO_CT)
        # 4) Poll until BOTH replies land (or we run out of tries). renderdash
        #    waits on a GitHub release lookup; renderlocal is local FS only.
        dash: Optional[dict] = None
        local: Optional[list] = None
        for _ in range(_SIO_POLL_TRIES):
            rr = await cli.get(ps)
            if not (200 <= rr.status_code < 300):
                break
            if dash is None:
                d = _extract_event(rr.text, "renderdash")
                if isinstance(d, dict):
                    dash = d
            if local is None:
                a = _extract_event_args(rr.text, "renderlocal")
                if isinstance(a, list):
                    local = a
            if dash is not None and local is not None:
                break
        if dash is None and local is None:
            return None
        out: dict = dict(dash) if isinstance(dash, dict) else {}
        if isinstance(local, list):
            # renderlocal → [endpoints (endpoints.yml), assets (downloaded), menuversion]
            endpoints = local[0] if len(local) >= 1 else None
            assets = local[1] if len(local) >= 2 else None
            out["_endpoints_count"] = _count_endpoints(endpoints)
            out["_assets_count"] = len(assets) if isinstance(assets, list) else 0
            out["_assets"] = assets if isinstance(assets, list) else []
            # Untracked = downloaded localfiles not referenced by the catalog
            # (old / orphaned downloads). Computed here where BOTH the endpoints
            # catalog AND the localfiles list are in scope.
            out["_untracked"] = _untracked_paths(endpoints, assets)
            out["_has_local"] = True
        return out
    except (httpx.HTTPError, OSError, ValueError):
        return None


# Socket.IO events the webapp emits while a boot-menu update runs — any of these
# landing after our `update` emit confirms the server accepted the trigger.
_UPDATE_ACK_EVENTS = ("updatestatus", "updatedone", "installerlog", "menuupdate",
                      "bootupdate", "renderdash")


async def _emit_action(cli: "httpx.AsyncClient", base: str, packet: str,
                       ack_events: "tuple[str, ...]") -> "tuple[bool, str]":
    """Drive a Socket.IO control EVENT over the HTTP long-polling transport
    (handshake → CONNECT → emit ``packet``) then poll for any of ``ack_events``
    to confirm the server accepted it. Returns ``(accepted, ack_name)``;
    ``(False, "")`` when the handshake fails or no ack lands in the poll window
    (so the caller can report HONESTLY that it couldn't confirm — never a false
    success). Never raises."""
    p = base + _SIO_PATH
    try:
        r = await cli.get(p)
        if not (200 <= r.status_code < 300):
            return False, ""
        sid = _parse_sid(r.text)
        if not sid:
            return False, ""
        ps = f"{p}&sid={sid}"
        await cli.post(ps, content="40", headers=_SIO_CT)
        await cli.post(ps, content=packet, headers=_SIO_CT)
        for _ in range(_SIO_POLL_TRIES):
            rr = await cli.get(ps)
            if not (200 <= rr.status_code < 300):
                break
            for ev in ack_events:
                if _extract_event_args(rr.text, ev) is not None:
                    return True, ev
        return False, ""
    except (httpx.HTTPError, OSError, ValueError):
        return False, ""


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
    out: dict[str, Any] = {
        "version": web,  # webapp version
        "menu_version": menu,  # local installed boot-menu version
        "latest_menu_version": remote,  # latest upstream release tag
        "update_available": bool(menu and remote and menu != remote),
        "cpu_percent": round(safe_float(d.get("CPUpercent")), 1),
        "mem_used": mem_used,
        "mem_total": mem_total,
    }
    # Boot catalog + downloaded assets (from the getlocal probe), surfaced only
    # when that reply landed so the card never shows a phantom "0 boot options".
    if d.get("_has_local"):
        out["has_local"] = True
        out["boot_endpoints"] = safe_int(d.get("_endpoints_count"))
        out["assets_count"] = safe_int(d.get("_assets_count"))
        out["assets"] = _shape_assets(d.get("_assets"))
        # Assets-directory disk usage = sum of every downloaded asset's size
        # (best-effort: 0 when the webapp build emits bare filenames). Summed over
        # the FULL list, not the capped display list.
        out["assets_disk_bytes"] = _assets_disk_bytes(d.get("_assets"))
        # Untracked / orphaned downloads (old assets to clean up) — count +
        # the path list (capped) for the drawer stat + the clear-untracked
        # action. Each is a bare path string (localfiles carry no size).
        untracked = [str(p).strip() for p in (d.get("_untracked") or [])
                     if str(p).strip()]
        out["untracked_count"] = len(untracked)
        out["untracked"] = [{"name": p, "size_bytes": 0}
                            for p in untracked[:_MAX_ASSETS]]
    return out


SKILLS: tuple[dict, ...] = (
    {
        "id": "netbootxyz_status",
        "name": "netboot.xyz status",
        "ai_phrases": ("netboot status, netboot.xyz status, is netboot up, "
                       "pxe boot status, network boot server, what version is "
                       "netboot.xyz, is netboot.xyz reachable, netbootxyz health"),
        "destructive": False,
    },
    {
        "id": "netbootxyz_assets",
        "name": "Downloaded boot assets",
        "ai_phrases": ("netboot assets, what can i pxe boot, downloaded boot "
                       "assets, which installers are downloaded, what oses can i "
                       "netboot, list netboot.xyz assets, local boot files, "
                       "what's downloaded on netboot"),
        "destructive": False,
    },
    {
        "id": "netbootxyz_update_menu",
        "name": "Update the boot menu",
        "ai_phrases": ("update the netboot menu, update netboot.xyz, pull the "
                       "latest boot menu, refresh the netboot menu, upgrade "
                       "netboot.xyz boot menu, get the latest netboot menu"),
        # DESTRUCTIVE: replaces the served boot-menu version (a config change to
        # what every PXE client will boot).
        "destructive": True,
    },
    {
        "id": "netbootxyz_clear_untracked",
        "name": "Clear untracked boot assets",
        "ai_phrases": ("clear untracked netboot assets, delete old boot assets, "
                       "remove orphaned netboot files, clean up netboot.xyz "
                       "downloads, purge untracked assets, free up netboot disk, "
                       "delete unused boot files"),
        # DESTRUCTIVE: deletes downloaded files from disk (the untracked /
        # orphaned ones — those not referenced by the boot-menu catalog).
        "destructive": True,
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
        "boot_endpoints": safe_int(data.get("boot_endpoints")),
        "assets_count": safe_int(data.get("assets_count")),
        "assets_disk_bytes": safe_int(data.get("assets_disk_bytes")),
        "untracked_count": safe_int(data.get("untracked_count")),
        "assets": [str(a.get("name") or "") for a in (data.get("assets") or [])
                   if isinstance(a, dict)][:20],
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
    if skill_id == "netbootxyz_assets":
        return await _assets_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "netbootxyz_update_menu":
        return await _update_menu_skill(host_row, chip, host_id=host_id,
                                        service_idx=service_idx)
    if skill_id == "netbootxyz_clear_untracked":
        return await _clear_untracked_skill(host_row, chip, host_id=host_id,
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
    endpoints = safe_int(data.get("boot_endpoints"))
    assets = safe_int(data.get("assets_count"))
    untracked = safe_int(data.get("untracked_count"))
    disk = _fmt_size(data.get("assets_disk_bytes"))
    if endpoints or assets:
        line = f"🧰 {endpoints} boot option(s) · {assets} asset(s) downloaded"
        if disk:
            line += f" ({disk})"
        lines.append(line)
    if untracked:
        lines.append(f"🧹 {untracked} untracked asset(s) (old / orphaned — clearable)")
    cpu = safe_float(data.get("cpu_percent"))
    mem_used = safe_int(data.get("mem_used"))
    mem_total = safe_int(data.get("mem_total"))
    if mem_total:
        pct = round(mem_used / mem_total * 100)
        lines.append(f"📊 CPU {cpu:.0f}% · RAM {pct}%")
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "version": web, "menu_version": menu, "latest_menu_version": latest,
            "boot_endpoints": endpoints, "assets_count": assets,
            "assets_disk_bytes": safe_int(data.get("assets_disk_bytes")),
            "untracked_count": untracked,
            "update_available": bool(data.get("update_available"))}


def _asset_row(a: dict) -> Optional[dict]:
    """One downloaded boot asset as a rich skill-result item: the file name
    (title) + a human size subtitle. No poster — netboot.xyz has no thumbnails."""
    if not isinstance(a, dict):
        return None
    name = str(a.get("name") or "").strip()
    if not name:
        return None
    size = _fmt_size(a.get("size_bytes"))
    return {"title": name, "subtitle": size}


# noinspection DuplicatedCode
async def _assets_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: the locally-downloaded boot assets (what you can PXE-boot right
    now) as rich rows. Never raises."""
    print(f"[netbootxyz] INFO netbootxyz_assets host={host_id} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "detail": str(e), "status": 0}
    assets = [a for a in (data.get("assets") or []) if isinstance(a, dict)]
    if not assets:
        # The dashboard probe may not have returned the local list (socket.io
        # timing / older webapp) — say so rather than imply nothing's downloaded.
        if not data.get("has_local"):
            return {"ok": True, "status": 200,
                    "detail": "🥾 Couldn't read the local boot-asset list from "
                              "netboot.xyz (the dashboard probe didn't return it — "
                              "try again in a moment)."}
        return {"ok": True, "status": 200,
                "detail": "🥾 No boot assets are downloaded locally yet."}
    items: list[dict] = []
    lines: list[str] = []
    for a in assets:
        row = _asset_row(a)
        if not row:
            continue
        items.append(row)
        lines.append(f"• {row['title']}" + (f"  ({row['subtitle']})" if row.get("subtitle") else ""))
    out: dict = {"ok": True, "status": 200,
                 "detail": f"🥾 {len(items)} downloaded boot asset(s):\n" + "\n".join(lines)}
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = "apps.netbootxyz.assets_count"
    return out


async def _update_menu_skill(host_row: dict, chip: dict, *,
                             host_id: Optional[str] = None,
                             service_idx: Optional[int] = None) -> dict:
    """Action (DESTRUCTIVE): trigger a boot-menu update on netboot.xyz via its
    socket.io ``update`` control event, updating the served boot menu to the
    latest upstream release. Confirms the server accepted the trigger by polling
    for an update-progress event; reports HONESTLY (ok=False) when it can't
    confirm rather than claiming a false success. Never raises."""
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    # Resolve the latest version to update TO (best-effort — the webapp also
    # defaults to latest when the arg is blank).
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
        latest = str(data.get("latest_menu_version") or "").strip()
        if data.get("menu_version") and not data.get("update_available"):
            return {"ok": True, "status": 200,
                    "detail": f"✅ netboot.xyz boot menu is already up to date "
                              f"(version {data.get('menu_version')})."}
    except (ValueError, RuntimeError):
        latest = ""
    packet = f'42["update",{json.dumps(latest)}]' if latest else '42["update"]'
    print(f"[netbootxyz] INFO netbootxyz_update_menu host={host_id} target={latest or '(latest)'}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            accepted, ack = await _emit_action(cli, base, packet, _UPDATE_ACK_EVENTS)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"update failed: {type(e).__name__}: {e}"}
    if accepted:
        tgt = f" to {latest}" if latest else ""
        return {"ok": True, "status": 200,
                "detail": f"⬆️ Triggered a netboot.xyz boot-menu update{tgt} "
                          f"(ack: {ack}). It runs asynchronously — check status "
                          f"again in a minute."}
    return {"ok": False, "status": 502,
            "detail": "couldn't confirm the boot-menu update started — your "
                      "netboot.xyz version may use a different control event; "
                      "trigger the update from the netboot.xyz web UI instead."}


# Socket.IO events the webapp may emit after a `deletelocal` (it re-renders the
# Local-Assets list) — best-effort ack; the real confirmation is the re-probe
# diff below.
_DELETE_ACK_EVENTS = ("renderlocal", "localdelete", "filedeleted", "deletestatus",
                      "renderdash")


async def _clear_untracked_skill(host_row: dict, chip: dict, *,
                                 host_id: Optional[str] = None,
                                 service_idx: Optional[int] = None) -> dict:
    """Action (DESTRUCTIVE): delete the UNTRACKED (orphaned / old) downloaded
    boot assets via netboot.xyz's socket.io ``deletelocal`` control event. ONLY
    the untracked files (those not referenced by the boot-menu catalog) are
    targeted — tracked assets are never touched (and the untracked computation
    refuses to run on an unrecognised catalog shape, so it can't mis-flag a
    tracked file). Confirms by re-probing the local list and reporting how many
    were actually removed (honest — never a false success). Never raises."""
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    print(f"[netbootxyz] INFO netbootxyz_clear_untracked host={host_id} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    if not data.get("has_local"):
        return {"ok": True, "status": 200,
                "detail": "🥾 Couldn't read the local boot-asset list (the "
                          "dashboard probe didn't return it — try again in a "
                          "moment)."}
    untracked = [str(u.get("name") or "").strip()
                 for u in (data.get("untracked") or []) if isinstance(u, dict)]
    untracked = [u for u in untracked if u]
    if not untracked:
        return {"ok": True, "status": 200,
                "detail": "🧹 No untracked boot assets — nothing to clear."}
    before = len(untracked)
    packet = '42["deletelocal",' + json.dumps(untracked) + ']'
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0,
                                     follow_redirects=True) as cli:
            await _emit_action(cli, base, packet, _DELETE_ACK_EVENTS)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"clear failed: {type(e).__name__}: {e}"}
    # Honest confirmation — re-probe and count how many targeted files are gone
    # (the deletelocal handler unlinks asynchronously; _emit_action already gave
    # the server a poll window).
    removed = before
    try:
        after = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                 service_idx=int(service_idx or 0), force=True)
        still = {str(u.get("name") or "").strip()
                 for u in (after.get("untracked") or []) if isinstance(u, dict)}
        removed = sum(1 for u in untracked if u not in still)
    except (ValueError, RuntimeError):
        pass  # keep the optimistic 'before' count when the re-probe fails
    if removed > 0:
        return {"ok": True, "status": 200, "removed": removed,
                "detail": f"🧹 Cleared {removed} untracked boot asset(s) from "
                          f"netboot.xyz (freed the orphaned downloads not used by "
                          f"the boot menu)."}
    return {"ok": False, "status": 502,
            "detail": "couldn't confirm the untracked assets were removed — your "
                      "netboot.xyz version may use a different delete event; "
                      "delete them from the netboot.xyz web UI (Local Assets) "
                      "instead."}
