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
    # Nodes live at ``state.nodes`` in every Pulse version we've seen.
    nodes = state.get("nodes") or []

    def _looks_like_guest(item) -> bool:
        """Heuristic — a dict with a ``vmid`` or ``id`` and one of the
        PVE-style status keys looks like a guest record regardless of
        which array we found it in."""
        if not isinstance(item, dict):
            return False
        if item.get("vmid") in (None, "", 0) and not item.get("id"):
            return False
        # A few marker fields PVE guest records always carry.
        marks = ("type", "status", "maxmem", "maxdisk", "cpu", "uptime", "node")
        return any(m in item for m in marks)

    def _harvest(container, inherited_node: str = "") -> list:
        """Walk any object/dict/list and collect anything that looks
        like a guest record. Handles the diverging Pulse schemas
        (``guests``/``vms``/``lxc``/``qemu`` at top level, nested under
        ``pve``, or hanging off each node) without hard-coding key
        names."""
        out: list = []
        if isinstance(container, list):
            for item in container:
                if _looks_like_guest(item):
                    if inherited_node and not item.get("node"):
                        item = {**item, "node": inherited_node}
                    out.append(item)
                elif isinstance(item, (list, dict)):
                    out.extend(_harvest(item, inherited_node))
        elif isinstance(container, dict):
            # When walking the state root, don't recurse into
            # ``nodes`` again (we handle those separately) so a guest
            # that somehow contains a node-shaped sub-object doesn't
            # double-count.
            for k, v in container.items():
                if k == "nodes":
                    continue
                out.extend(_harvest(v, inherited_node))
        return out

    guests: list = _harvest(state)
    # Also walk each node's sub-dicts — some builds attach guests
    # there. Stamp the parent node name so ``extract_guest_stats``
    # can report "on pve-1" even when the guest lacks ``node``.
    for n in nodes:
        if not isinstance(n, dict):
            continue
        inherited = (n.get("node") or n.get("name") or "")
        guests.extend(_harvest(
            {k: v for k, v in n.items() if k not in ("cpu", "mem", "uptime")},
            inherited,
        ))

    # De-dup — the recursive scan can pick the same guest up via
    # multiple paths (e.g. state.vms AND state.pve.vms).
    seen_ids: set = set()
    unique_guests: list = []
    for g in guests:
        gid = str(g.get("vmid") or g.get("id") or "") + "|" + str(g.get("node") or "")
        if gid in seen_ids:
            continue
        seen_ids.add(gid)
        unique_guests.append(g)
    guests = unique_guests

    print(f"[pulse] probe: state top-level keys={sorted(state.keys())} "
          f"nodes={len(nodes)} guests={len(guests)}")
    # Dump the raw shape of the first node + guest so operators can
    # see what fields Pulse actually emits.
    if nodes:
        print(f"[pulse] probe: sample node fields={sorted((nodes[0] or {}).keys())}")
    if guests:
        g0 = guests[0] or {}
        print(f"[pulse] probe: sample guest fields={sorted(g0.keys())} "
              f"name={g0.get('name')!r} vmid={g0.get('vmid')!r} "
              f"type={g0.get('type')!r} node={g0.get('node')!r}")
    # Keyed by display name (preserves case). We also maintain a parallel
    # lowercased-trimmed index so the caller's ``_lookup`` helper can
    # match ``Docker`` / ``  docker `` / the guest's vmid without the
    # operator having to type it pixel-perfect.
    out: dict[str, dict] = {}

    def _add(key: str, stats: dict):
        key = (key or "").strip()
        if not key:
            return
        out[key] = stats

    for n in nodes:
        if not isinstance(n, dict):
            continue
        name = (n.get("node") or n.get("name") or "").strip()
        if not name:
            continue
        stats = extract_node_stats(n)
        stats["pulse_name"] = name
        stats["pulse_kind"] = "node"
        _add(name, stats)
    # Guests come second so their keys don't collide with node keys —
    # if a guest happens to share a name with a node (rare), the guest
    # wins because it has more specific stats.
    for g in guests:
        if not isinstance(g, dict):
            continue
        # Display name: Pulse versions use ``name`` (general),
        # ``hostname`` (LXCs in some releases), or ``description``
        # (fallback when neither is set). First non-empty wins.
        gname = ((g.get("name") or g.get("hostname")
                  or g.get("description") or "")).strip()
        if not gname:
            continue
        stats = extract_guest_stats(g)
        stats["pulse_name"] = gname
        _add(gname, stats)
        # Alternate display-name aliases — if ``name`` and ``hostname``
        # differ (LXC friendly-name vs unix hostname), make both
        # resolvable so the operator can type whichever they see.
        for alt in ("name", "hostname"):
            v = (g.get(alt) or "").strip()
            if v and v != gname:
                _add(v, stats)
        # VM/LXC id — ``vmid`` classic, ``id`` sometimes, both may
        # be numbers or strings.
        for id_key in ("vmid", "id"):
            vid = g.get(id_key)
            if vid not in (None, "", 0):
                _add(str(vid), stats)
    print(f"[pulse] probe: indexed keys={sorted(out.keys())}")
    return {"hosts": out, "error": None}


def lookup(pulse_hosts: dict, needle: str) -> Optional[dict]:
    """Find a Pulse host record by name, tolerating case + whitespace.

    Used by :func:`main.api_hosts` and the per-row test endpoint so
    operators can type ``Docker`` / ``docker`` / ``  docker ``
    interchangeably. Falls through to a stripped+lowercased scan when
    an exact-key hit misses; returns ``None`` on no match.
    """
    if not pulse_hosts or not needle:
        print(f"[pulse] lookup: short-circuit "
              f"(hosts={len(pulse_hosts) if pulse_hosts else 0} "
              f"needle={needle!r})")
        return None
    if needle in pulse_hosts:
        print(f"[pulse] lookup: exact hit {needle!r}")
        return pulse_hosts[needle]
    key = needle.strip().lower()
    if not key:
        print(f"[pulse] lookup: needle normalised to empty {needle!r}")
        return None
    for k, v in pulse_hosts.items():
        if k.strip().lower() == key:
            print(f"[pulse] lookup: fuzzy hit {needle!r} → {k!r}")
            return v
    print(f"[pulse] lookup: MISS needle={needle!r} normalised={key!r} "
          f"known_keys={sorted(pulse_hosts.keys())}")
    return None
