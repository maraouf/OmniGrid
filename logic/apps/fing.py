"""Fing per-app module (network device inventory + presence).

Encapsulates everything Fing-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``ddns_updater.py`` / ``opnsense.py`` shape (single-instance, rich card backed
by a fetch + a lifespan history sampler):

    SLUGS               — catalog slugs this module handles ("fing").
    requires_api_key()  — True (the chip's ``api_key`` stores the Fing Local
                          API key; it is sent as a QUERY PARAMETER ``?auth=<key>``,
                          NOT a header).
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + list devices (read) + is-a-device-
                          online lookup (read, arg).

Auth model: Fing exposes a documented, FREE, LOCAL HTTP API on a Fing Agent /
Fing Desktop / Fingbox — ``http://<host>:49090/1/`` — guarded by an API KEY that
is passed as the ``?auth=<key>`` QUERY PARAMETER (this is the qBittorrent /
Tautulli "secret in the query" shape, NOT the ``X-Api-Key`` header shape). The
chip stores the key in the write-only ``api_key`` field (``_set`` flag pattern);
the GET helper appends ``?auth=<key>`` to each call. READ-ONLY — the local API
has no block / wake-on-LAN write surface (those are cloud-only), so this is a
read-mostly card like APC / netboot.xyz. Single-instance app (NOT fleet).

The expanded card answers "what's on my network right now" at a glance, and the
lifespan ``fing_sampler`` records the online-device count per tick into
``fing_samples`` so OmniGrid surfaces an occupancy trend Fing itself doesn't show
on a glance — the FlareSolverr-sampler pattern (current-state-only upstream).

Upstream API reference — http://<host>:49090/1 :
    GET /1/devices?auth=<key>  — every discovered device: mac / ip / state
                                 (UP/DOWN) / name / type / make / model /
                                 first_seen / last_changed.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, peek_cache, resolve_base_url, resolve_cache_ttl)
from logic.coerce import as_dict, as_list, safe_int

# Catalog template slugs — the per-app Fing card handles both the "fing" Local
# API template (port 49090) and the legacy "fing-agent" template (the Fing Agent
# whose published port 44444 is UPnP-only); both expose the same ?auth Local API
# once enabled, so one module serves either chip.
SLUGS: tuple[str, ...] = ("fing", "fing-agent")

DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# A device first seen within this window counts as "new" (the actionable
# security signal — "an unknown device joined your network"). Display heuristic,
# read from the operator-tunable below.
_MAX_ROWS = 60

SKILLS: tuple[dict, ...] = (
    {
        "id": "fing_status",
        "name": "Fing network status",
        "ai_phrases": ("fing status, how many devices are on my network, network "
                       "device count, how many devices online, fing overview, "
                       "any new devices on my network, network presence"),
        "destructive": False,
    },
    {
        "id": "fing_scan",
        "name": "Scan now",
        "ai_phrases": ("fing scan now, rescan my network, refresh the network scan, "
                       "scan for new devices now, run a fing scan, refresh fing, "
                       "check for new devices now, update the device list"),
        "destructive": False,
    },
    {
        "id": "fing_devices",
        "name": "List Fing devices",
        "ai_phrases": ("list fing devices, show devices on my network, what's "
                       "connected to my network, fing device list, who is online, "
                       "show network devices, what devices does fing see"),
        "destructive": False,
    },
    {
        "id": "fing_new_devices",
        "name": "New devices (Fing)",
        "ai_phrases": ("what new devices joined, any unknown devices on my network, "
                       "new device alert, what just connected to my network, show "
                       "new devices, did anything new join my network, unknown "
                       "device joined, who joined my network recently"),
        "destructive": False,
    },
    {
        "id": "fing_device",
        "name": "Is a device online (Fing)",
        "ai_phrases": ("is the <name> online, is my <name> connected, is the "
                       "living-room tv online, is <name> on the network, check if "
                       "<name> is online, fing look up device"),
        "arg": True,
        "arg_hint": "the device name / IP / MAC to look up (e.g. living-room TV)",
        "destructive": False,
    },
)


def requires_api_key() -> bool:
    """True — the Fing Local API needs the ``?auth=<key>`` key on every call;
    the editor MUST render the api_key input + the Test-connection button."""
    return True


def _new_window_seconds() -> int:
    """Hours-old threshold (in seconds) under which a device counts as NEW —
    operator-tunable (``tuning_fing_new_device_hours``, default 24)."""
    from logic.tuning import tuning_int, Tunable  # noqa: PLC0415
    return max(1, tuning_int(Tunable.FING_NEW_DEVICE_HOURS)) * 3600


def _device_online(d: dict) -> bool:
    """True when a Fing device's ``state`` reads as up/online."""
    s = str(d.get("state") or d.get("status") or "").strip().lower()
    return s in ("up", "online", "active", "1", "true")


def _device_first_seen(d: dict) -> int:
    """Best-effort first-seen epoch (Fing emits ``first_seen`` / ``firstSeen`` —
    an epoch int, an epoch-ms int, or an ISO string). 0 when absent."""
    raw = d.get("first_seen")
    if raw is None:
        raw = d.get("firstSeen")
    if isinstance(raw, (int, float)):
        v = int(raw)
        return v // 1000 if v > 10_000_000_000 else v  # ms → s heuristic
    s = str(raw or "").strip()
    if not s:
        return 0
    if s.isdigit():
        v = int(s)
        return v // 1000 if v > 10_000_000_000 else v
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        from datetime import datetime, timezone  # noqa: PLC0415
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return 0


def _fmt_age(seconds: Any) -> str:
    """Humanise an age in seconds → ``Nd`` / ``Nh`` / ``Nm`` / ``just now``
    ('' for non-positive). Backend skill text (English)."""
    s = max(0, safe_int(seconds))
    if s <= 0:
        return ""
    days = s // 86400
    hrs = (s % 86400) // 3600
    mins = (s % 3600) // 60
    if days:
        return f"{days}d"
    if hrs:
        return f"{hrs}h"
    return f"{mins}m" if mins else "just now"


def _device_ip(d: dict) -> str:
    """First IP of a Fing device (``ip`` is a list, or a bare string)."""
    ip = d.get("ip")
    if isinstance(ip, list):
        for x in ip:
            if str(x or "").strip():
                return str(x).strip()
        return ""
    return str(ip or "").strip()


def _shape(devices: list[dict]) -> dict:
    """Roll the Fing device list into the card shape: totals + online/offline +
    type / vendor breakdowns + a NEW-device count + a compact per-device list."""
    now = int(time.time())
    new_cut = now - _new_window_seconds()
    total = len(devices)
    online = 0
    new_count = 0
    by_type: dict = {}
    by_vendor: dict = {}
    compact: list = []
    # Focused breakdowns derivable from the same loop: the NEW devices (the
    # "unknown device joined" security signal — names + MACs, not just a count)
    # and the OFFLINE-but-known devices (a known device that dropped off).
    new_list: list = []
    offline_list: list = []
    for d in devices:
        if not isinstance(d, dict):
            continue
        is_on = _device_online(d)
        if is_on:
            online += 1
        first_seen = _device_first_seen(d)
        is_new = first_seen > 0 and first_seen >= new_cut
        if is_new:
            new_count += 1
        dtype = str(d.get("type") or "other").strip() or "other"
        by_type[dtype] = by_type.get(dtype, 0) + 1
        vendor = str(d.get("make") or d.get("vendor") or "").strip()
        if vendor:
            by_vendor[vendor] = by_vendor.get(vendor, 0) + 1
        row = {
            "name": str(d.get("name") or _device_ip(d) or d.get("mac") or "?").strip(),
            "ip": _device_ip(d),
            "mac": str(d.get("mac") or "").strip(),
            "type": dtype,
            "vendor": vendor,
            "online": is_on,
            "new": is_new,
            "first_seen": first_seen,
        }
        if len(compact) < _MAX_ROWS:
            compact.append(row)
        if is_new and len(new_list) < _MAX_ROWS:
            new_list.append(row)
        if (not is_on) and len(offline_list) < _MAX_ROWS:
            offline_list.append(row)

    def _top(counts: dict, n: int = 6) -> list:
        return [{"name": k, "count": v}
                for k, v in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n]]

    # Online devices first, then by name — most-relevant rows lead the list.
    compact.sort(key=lambda r: (not r["online"], r["name"].lower()))
    # New devices newest-first (most-recent join leads); offline known by name.
    new_list.sort(key=lambda r: (-r["first_seen"], r["name"].lower()))
    offline_list.sort(key=lambda r: r["name"].lower())
    return {
        "devices_total": total,
        "devices_online": online,
        "devices_offline": max(0, total - online),
        "new_devices": new_count,
        "by_type": _top(by_type),
        "by_vendor": _top(by_vendor),
        "devices": compact,
        # P1: the actual new devices (names + MACs + first-seen) — the killer
        # "unknown device joined" surface; P2: the known devices that dropped off.
        "new_device_list": new_list,
        "offline_device_list": offline_list,
    }


async def _fetch_devices(cli: httpx.AsyncClient, base: str, api_key: str) -> "tuple[int, list]":
    """GET ``/1/devices?auth=<key>`` → ``(http_status, devices_list)``. The
    devices live under ``devices`` (or a bare top-level list on some agents)."""
    r = await cli.get(base + "/1/devices", params={"auth": api_key},
                      headers={"Accept": "application/json"})
    if r.status_code != 200:
        return r.status_code, []
    try:
        body = r.json()
    except (ValueError, TypeError):
        return 200, []
    if isinstance(body, list):
        return 200, body
    return 200, as_list(as_dict(body).get("devices"))


# noinspection PyUnusedLocal
async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe ``GET /1/devices?auth=<key>`` with the supplied Local API key.
    ``candidate_key`` is the key; falls back to the stored chip ``api_key`` so
    the operator can re-test after first save without retyping. Returns
    ``{ok, detail, status}``."""
    key = (candidate_key or "").strip() or (chip.get("api_key") or "").strip()
    if not key:
        return {"ok": False, "detail": "Fing Local API key required", "status": 0}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            status, devices = await _fetch_devices(cli, base, key)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if status in (401, 403):
        return {"ok": False, "detail": "auth failed (check the Local API key)",
                "status": status}
    if status != 200:
        return {"ok": False, "detail": f"HTTP {status}", "status": status}
    n = len(devices)
    return {"ok": True, "detail": f"OK ({n} device{'s' if n != 1 else ''})",
            "status": 200}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch + shape the Fing device inventory for the expanded card. Embeds the
    online-device-count history from the lifespan sampler. Raises ``ValueError``
    (base URL won't resolve / no key) / ``RuntimeError`` (upstream error)."""
    now = time.time()
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured for this instance")
    key = (chip.get("api_key") or "").strip()
    if not key:
        raise ValueError("Fing Local API key not set for this instance")
    ttl = resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S)
    ck = cache_key(host_id, service_idx)
    if not force:
        cached = _data_cache.get(ck)
        if cached is not None and (now - cached[0]) < ttl:
            return cached[1]
    print(f"[fing] INFO fetch host={host_id} svc_idx={service_idx} base={base}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            status, devices = await _fetch_devices(cli, base, key)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[fing] error: fetch host={host_id} base={base} failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if status in (401, 403):
        raise RuntimeError(f"auth failed: HTTP {status} (check the Local API key)")
    if status != 200:
        raise RuntimeError(f"upstream returned HTTP {status} for /1/devices "
                           f"(check the chip URL points at the Fing agent, default :49090)")
    shaped = _shape([d for d in devices if isinstance(d, dict)])
    out: dict[str, Any] = {"available": True, "fetched_at": int(now), **shaped}
    # Embed the online-device-count trend from the lifespan sampler (best-effort
    # — a sampler / DB hiccup must not fail the card; zeroed until rows accrue).
    try:
        from logic.apps import fing_sampler as _fing_sampler  # noqa: PLC0415
        out["history"] = _fing_sampler.history_summary(host_id, int(service_idx))
    except Exception as e:  # noqa: BLE001
        print(f"[fing] warning: history_summary({host_id}#{service_idx}) failed: {e}")
    print(f"[fing] INFO fetched host={host_id} devices={out['devices_total']} "
          f"online={out['devices_online']} new={out['new_devices']} "
          f"types={len(out['by_type'])}")
    _data_cache[ck] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "devices_total": safe_int(data.get("devices_total")),
        "devices_online": safe_int(data.get("devices_online")),
        "devices_offline": safe_int(data.get("devices_offline")),
        "new_devices": safe_int(data.get("new_devices")),
        # The actual new-device NAMES (capped) so the AI can say WHICH joined,
        # not just how many.
        "new_device_names": [str(as_dict(d).get("name") or "").strip()
                             for d in as_list(data.get("new_device_list"))
                             if as_dict(d).get("name")][:10],
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown id."""
    if skill_id == "fing_status":
        return await _status_skill(host_row, chip, host_id=host_id, service_idx=service_idx)
    if skill_id == "fing_scan":
        return await _scan_skill(host_row, chip, host_id=host_id, service_idx=service_idx)
    if skill_id == "fing_devices":
        return await _devices_skill(host_row, chip, host_id=host_id, service_idx=service_idx)
    if skill_id == "fing_new_devices":
        return await _new_devices_skill(host_row, chip, host_id=host_id, service_idx=service_idx)
    if skill_id == "fing_device":
        return await _device_skill(host_row, chip, arg=_kw.get("arg"),
                                   host_id=host_id, service_idx=service_idx)
    raise ValueError(f"unknown skill: {skill_id!r}")


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch + format the network summary. Never raises."""
    print(f"[fing] INFO fing_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[fing] warning: fing_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    total = safe_int(data.get("devices_total"))
    online = safe_int(data.get("devices_online"))
    new_count = safe_int(data.get("new_devices"))
    lines = [f"📡 Devices: {online}/{total} online"]
    if new_count:
        new_names = [str(as_dict(d).get("name") or "?").strip()
                     for d in as_list(data.get("new_device_list"))][:5]
        line = f"🆕 New (recent): {new_count}"
        if new_names:
            line += " — " + ", ".join(new_names)
        lines.append(line)
    offline = safe_int(data.get("devices_offline"))
    if offline:
        lines.append(f"⚪ Offline (known): {offline}")
    types = as_list(data.get("by_type"))[:4]
    if types:
        lines.append("   " + " · ".join(f"{as_dict(t).get('name')} {as_dict(t).get('count')}"
                                        for t in types))
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "devices_total": total, "devices_online": online, "new_devices": new_count}


# noinspection DuplicatedCode
async def _scan_skill(host_row: dict, chip: dict, *,
                      host_id: Optional[str] = None,
                      service_idx: Optional[int] = None) -> dict:
    """"Scan now" — force a LIVE re-fetch of the agent's device list. The Fing
    Local API is read-only (it can't trigger an active re-scan), but the Fing
    agent scans the network continuously, so this pulls its freshest state on
    demand and reports online / total + any new devices. Never raises."""
    print(f"[fing] INFO fing_scan host={host_id} svc_idx={service_idx} (force refresh)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[fing] warning: fing_scan host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    total = safe_int(data.get("devices_total"))
    online = safe_int(data.get("devices_online"))
    new_count = safe_int(data.get("new_devices"))
    lines = [f"🔄 Refreshed — {online}/{total} devices online"]
    if new_count:
        lines.append(f"🆕 {new_count} new device(s) recently joined")
    lines.append("Fing scans your network continuously; this pulled its latest state.")
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "devices_total": total, "devices_online": online, "new_devices": new_count}


# noinspection DuplicatedCode
async def _devices_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None,
                         service_idx: Optional[int] = None) -> dict:
    """Read-only: list devices (online first) with type / vendor / IP. Never
    raises."""
    print(f"[fing] INFO fing_devices host={host_id} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "detail": str(e), "status": 0}
    devs = as_list(data.get("devices"))
    if not devs:
        return {"ok": True, "status": 200, "detail": "No devices found by Fing."}
    lines = []
    for d in devs[:40]:
        if not isinstance(d, dict):
            continue
        emoji = "🟢" if d.get("online") else "⚪"
        name = str(d.get("name") or "?").strip()
        meta = " · ".join(b for b in (str(d.get("type") or "").strip(),
                                      str(d.get("vendor") or "").strip(),
                                      str(d.get("ip") or "").strip()) if b)
        new_tag = " 🆕" if d.get("new") else ""
        lines.append(f"{emoji} {name}{(' (' + meta + ')') if meta else ''}{new_tag}")
    online = safe_int(data.get("devices_online"))
    total = safe_int(data.get("devices_total"))
    head = f"📡 {online}/{total} devices online"
    return {"ok": True, "status": 200, "detail": head + "\n" + "\n".join(lines)}


# noinspection DuplicatedCode
async def _new_devices_skill(host_row: dict, chip: dict, *,
                            host_id: Optional[str] = None,
                            service_idx: Optional[int] = None) -> dict:
    """Read-only: list the recently-joined (NEW) devices with name + MAC + IP +
    when they first appeared — the "unknown device joined your network" security
    surface (the killer Fing question). Never raises."""
    print(f"[fing] INFO fing_new_devices host={host_id} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "detail": str(e), "status": 0}
    new_list = [d for d in as_list(data.get("new_device_list")) if isinstance(d, dict)]
    win_h = _new_window_seconds() // 3600
    if not new_list:
        return {"ok": True, "status": 200,
                "detail": f"✅ No new devices joined in the last {win_h}h.",
                "new_devices": 0}
    now = int(time.time())
    lines = [f"🆕 {len(new_list)} new device(s) in the last {win_h}h:"]
    for d in new_list:
        name = str(d.get("name") or "?").strip()
        fs = safe_int(d.get("first_seen"))
        age = _fmt_age(now - fs) if fs else ""
        on = "🟢" if d.get("online") else "⚪"
        bits = [b for b in (str(d.get("ip") or "").strip(), str(d.get("mac") or "").strip(),
                            (f"joined {age} ago" if age else "")) if b]
        lines.append(f"{on} {name}" + (f" ({' · '.join(bits)})" if bits else ""))
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "new_devices": len(new_list)}


async def _device_skill(host_row: dict, chip: dict, *,
                        arg: Optional[str] = None,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only (arg): "is <device> online" — match the term against device
    name / IP / MAC / vendor and report its state. Never raises."""
    needle = str(arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no device given — say e.g. \"is the living-room TV online\""}
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "detail": str(e), "status": 0}
    nl = needle.lower()
    for d in as_list(data.get("devices")):
        if not isinstance(d, dict):
            continue
        hay = " ".join(str(d.get(k) or "") for k in ("name", "ip", "mac", "vendor")).lower()
        if nl in hay:
            emoji = "🟢 online" if d.get("online") else "⚪ offline"
            bits = [str(d.get("name") or needle).strip(), "is", emoji]
            tail = " · ".join(b for b in (str(d.get("ip") or "").strip(),
                                          str(d.get("type") or "").strip(),
                                          str(d.get("vendor") or "").strip()) if b)
            return {"ok": True, "status": 200,
                    "detail": "📡 " + " ".join(bits) + (f"\n   {tail}" if tail else "")}
    return {"ok": True, "status": 200,
            "detail": f"📡 No device on the network matched \"{needle}\"."}
