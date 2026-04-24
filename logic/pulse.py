"""Pulse integration — read-only consumer of a Pulse instance.

Pulse (github.com/rcourtman/Pulse) is a Proxmox VE + PBS monitoring
dashboard. It exposes a REST API at ``{base}/api/state`` that returns
the full fleet snapshot: nodes, guests (VMs/CTs), storage, backup jobs.

For OmniGrid's Hosts tab we only care about the ``nodes`` slice — one
entry per PVE host with cpu / memory / uptime / kernel info. That
slice maps neatly onto the same ``host_*`` fields that Beszel and
node-exporter populate, so ``gather.py`` can merge all three sources
with node-exporter > Pulse > Beszel precedence.

Auth: Pulse uses an API token set in its own admin UI. We send it as
``X-API-Token`` (Pulse v3+). Older versions accepted raw session
cookies; if we ever need them, extend :func:`_headers` with fallback
logic. Credentials live in the ``settings`` table (admin creates a
dedicated read-only token for OmniGrid).

Limits: Pulse only knows about hosts Proxmox knows about. Non-PVE
machines (laptops, ARM boxes, Mac minis) are invisible here — that's
why ``host_stats_source`` stays multi-select rather than replacing
Beszel.
"""
from __future__ import annotations

import time
from typing import Optional

import httpx


def _headers(token: str) -> dict:
    if not token:
        return {}
    return {"X-API-Token": token, "Accept": "application/json"}


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


async def _fetch_state(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
) -> dict:
    """Single call — returns the Pulse state envelope (``{nodes, ...}``).

    Pulse renamed endpoints between majors; we try the current path first
    and fall back to the legacy one so both v3+ and older installs work.
    """
    paths = ("/api/state", "/api/v1/state", "/api/nodes")
    last_err: Optional[str] = None
    for p in paths:
        url = base_url.rstrip("/") + p
        try:
            r = await client.get(url, headers=_headers(token))
        except Exception as e:
            last_err = f"{p}: {e}"
            continue
        if r.status_code == 401 or r.status_code == 403:
            last_err = f"{p}: HTTP {r.status_code} — check token"
            continue
        if r.status_code >= 400:
            last_err = f"{p}: HTTP {r.status_code}"
            continue
        try:
            j = r.json() or {}
        except Exception as e:
            last_err = f"{p}: {e}"
            continue
        # /api/nodes returns a bare list; wrap it so the caller has a
        # uniform shape.
        if isinstance(j, list):
            return {"nodes": j}
        return j
    raise RuntimeError(f"pulse: no compatible endpoint responded — {last_err or '?'}")


def extract_guest_stats(guest: dict) -> dict:
    """Shape one Pulse guest (VM / LXC) record into ``host_*`` fields.

    Pulse guest schema (v3+):
        {"vmid": 100, "name": "docker", "type": "lxc" | "qemu",
         "status": "running" | "stopped",
         "cpu": 0.12, "maxcpu": 4,
         "mem": 2800000000, "maxmem": 8000000000,
         "disk": 15000000000, "maxdisk": 100000000000,
         "uptime": 86400, "node": "pve-1"}

    This lets a Docker VM that runs under Proxmox show up in the Hosts
    tab alongside bare hosts monitored by Beszel / node-exporter. The
    ``pulse_status`` field carries running/stopped so the UI chip is
    accurate for guests too.
    """
    if not isinstance(guest, dict):
        guest = {}
    gib = 1024 ** 3
    mem = _num(guest.get("mem"))
    mem_max = _num(guest.get("maxmem"))
    disk = _num(guest.get("disk"))
    disk_max = _num(guest.get("maxdisk"))
    if 0 < mem_max < 10_000_000:
        mem, mem_max = mem * gib, mem_max * gib
    if 0 < disk_max < 10_000_000:
        disk, disk_max = disk * gib, disk_max * gib
    uptime = int(_num(guest.get("uptime")))
    host_boot_ts = (time.time() - uptime) if uptime > 0 else None
    cpu_pct = _num(guest.get("cpu")) * 100
    kind = str(guest.get("type") or "").lower()
    platform_label = {
        "lxc":  "Proxmox LXC",
        "qemu": "Proxmox VM",
    }.get(kind, "Proxmox guest")
    return {
        "host_mem_total":    int(mem_max),
        "host_mem_used":     int(mem),
        "host_mem_avail":    max(0, int(mem_max - mem)),
        "host_disk_total":   int(disk_max),
        "host_disk_used":    int(disk),
        "host_disk_free":    max(0, int(disk_max - disk)),
        "host_uptime_s":     uptime,
        "host_boot_ts":      host_boot_ts,
        "host_cpu_percent":  cpu_pct,
        "host_mem_percent":  (mem / mem_max * 100) if mem_max > 0 else 0,
        "host_disk_percent": (disk / disk_max * 100) if disk_max > 0 else 0,
        "host_cores":        int(_num(guest.get("maxcpu"))),
        "host_platform":     platform_label,
        "host_agent":        "",
        "host_kernel":       "",
        "host_arch":         "",
        "mounts":            [],
        "exporter_error":    None,
        "pulse_status":      str(guest.get("status") or "unknown"),
        "pulse_kind":        kind or "guest",
        "pulse_vmid":        int(_num(guest.get("vmid"))),
        "pulse_node":        str(guest.get("node") or ""),
    }


def extract_node_stats(node: dict) -> dict:
    """Shape one Pulse node record into OmniGrid's ``host_*`` fields.

    Pulse's node schema (v3+ approx):
        {"node": "pve-1", "status": "online", "cpu": 0.12,
         "maxcpu": 16, "mem": 4800000000, "maxmem": 33000000000,
         "disk": 8000000000, "maxdisk": 200000000000,
         "uptime": 950000, "kernel": "6.8.8-1-pve",
         "pveversion": "8.1", "cgroup_mode": 2}

    Missing fields degrade to 0 / "". Memory / disk are bytes in newer
    Pulse versions, bytes or GiB in older ones — we detect which by
    looking at the magnitude (anything under 10M is assumed to be GiB
    and multiplied up).
    """
    if not isinstance(node, dict):
        node = {}
    gib = 1024 ** 3
    mem = _num(node.get("mem"))
    mem_max = _num(node.get("maxmem"))
    disk = _num(node.get("disk"))
    disk_max = _num(node.get("maxdisk"))
    if 0 < mem_max < 10_000_000:  # heuristic: values under 10M are GiB
        mem, mem_max = mem * gib, mem_max * gib
    if 0 < disk_max < 10_000_000:
        disk, disk_max = disk * gib, disk_max * gib
    uptime = int(_num(node.get("uptime")))
    host_boot_ts = (time.time() - uptime) if uptime > 0 else None
    cpu_pct = _num(node.get("cpu")) * 100  # Pulse emits 0..1
    return {
        "host_mem_total":   int(mem_max),
        "host_mem_used":    int(mem),
        "host_mem_avail":   max(0, int(mem_max - mem)),
        "host_disk_total":  int(disk_max),
        "host_disk_used":   int(disk),
        "host_disk_free":   max(0, int(disk_max - disk)),
        "host_uptime_s":    uptime,
        "host_boot_ts":     host_boot_ts,
        "host_cpu_percent": cpu_pct,
        "host_mem_percent": (mem / mem_max * 100) if mem_max > 0 else 0,
        "host_disk_percent": (disk / disk_max * 100) if disk_max > 0 else 0,
        "host_cores":       int(_num(node.get("maxcpu"))),
        "host_kernel":      str(node.get("kernel") or ""),
        "host_platform":    str(node.get("pveversion") or "Proxmox VE"),
        "host_os":          str(node.get("os") or ""),
        "host_arch":        "",  # Pulse doesn't surface arch
        "host_agent":       str(node.get("pveversion") or ""),
        "mounts":           [],
        "exporter_error":   None,
        "pulse_status":     str(node.get("status") or "unknown"),
    }


async def probe_pulse(
    base_url: str,
    token: str,
    verify_tls: bool = True,
    timeout: float = 15.0,
) -> dict:
    """Fetch every PVE node from Pulse, keyed by node name.

    Returns ``{"hosts": {name: stats, ...}, "error": None}`` on success,
    ``{"hosts": {}, "error": "..."}`` on failure. Never raises — lets
    :mod:`logic.gather` keep going on any Pulse hiccup.
    """
    if not base_url or not token:
        return {"hosts": {}, "error": "pulse: missing url or token"}
    try:
        async with httpx.AsyncClient(verify=verify_tls, timeout=timeout) as client:
            state = await _fetch_state(client, base_url, token)
    except Exception as e:
        return {"hosts": {}, "error": str(e)}
    nodes = state.get("nodes") or []
    # Pulse v3+ returns ``guests``; older builds use ``vms``. Take both
    # and merge so we handle the schema either way. If both are present
    # and somehow overlap, latest wins — unlikely in practice.
    guests: list = list(state.get("guests") or [])
    guests.extend(state.get("vms") or [])
    out: dict[str, dict] = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        name = (n.get("node") or n.get("name") or "").strip()
        if not name:
            continue
        stats = extract_node_stats(n)
        stats["pulse_name"] = name
        stats["pulse_kind"] = "node"
        out[name] = stats
    # Guests come second so their keys don't collide with node keys —
    # if a guest happens to share a name with a node (rare), the guest
    # wins because it has more specific stats.
    for g in guests:
        if not isinstance(g, dict):
            continue
        gname = (g.get("name") or "").strip()
        if not gname:
            continue
        stats = extract_guest_stats(g)
        stats["pulse_name"] = gname
        out[gname] = stats
    return {"hosts": out, "error": None}
