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
import asyncio

import time
from typing import Callable, Optional

import httpx

from logic.merge import normalize_arch as _normalize_arch


def _headers(token: str) -> dict:
    """Pulse API auth headers (``X-API-Token``); empty dict when no token."""
    if not token:
        return {}
    return {"X-API-Token": token, "Accept": "application/json"}


def _num(v) -> float:
    """Coerce a value to float; ``0.0`` on failure."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# Pulse-version-aware unit detection. Older Pulse v3
# emits memory/disk in GiB; newer versions (v4+) emit them in bytes.
# The previous "values < 10M are GiB" magnitude sniff misclassified
# legitimately-tiny volumes (a 9 MiB embedded LXC volume looked like
# 9 GiB). We now probe `/api/version` once per (base_url, token) pair
# at probe-time, cache the result, and use the version map below to
# pick units. The magnitude heuristic remains as a fallback when the
# version probe fails (older Pulse builds 404 on the endpoint).
_version_cache: dict[str, str] = {}  # base_url → version string
# Set by `probe_pulse` at the start of each fetch so the synchronous
# extract_*_stats helpers (called from `gather.py` AND legacy direct
# call sites) can consult the version cache without changing every
# call signature. Single-process; one probe at a time per gather =
# safe global.
_current_probe_base_url: str = ""

# Versions whose payload uses bytes for mem/disk fields. Anything not
# matching this list (including unknown versions) falls through to the
# magnitude heuristic.
_PULSE_BYTES_VERSIONS_PREFIX = ("4.", "5.")


def _pulse_uses_bytes(base_url: str) -> Optional[bool]:
    """Resolve the unit-policy for a base_url. Returns True when we
    KNOW Pulse emits bytes, False when we KNOW it emits GiB, None when
    we have no version info (caller falls back to magnitude heuristic)."""
    v = _version_cache.get(base_url or "")
    if not v:
        return None
    if v.startswith(_PULSE_BYTES_VERSIONS_PREFIX):
        return True
    if v.startswith("3."):
        return False
    return None


def _value_is_gib(value: float, base_url: str = "") -> bool:
    """Decide whether ``value`` is in GiB (vs bytes) for the given Pulse
    base_url. Version-driven when known; magnitude heuristic otherwise.
    """
    pref = _pulse_uses_bytes(base_url)
    if pref is not None:
        # pref True → bytes → not GiB; pref False → GiB.
        return not pref
    return 0 < value < 10_000_000


async def _fetch_version(client: httpx.AsyncClient, base_url: str, token: str) -> Optional[str]:
    """Probe `/api/version` once per (base_url) pair. Best-effort —
    older Pulse builds may 404 here; we silently move on and let the
    magnitude heuristic kick in. Returns the version string (e.g.
    ``"4.2.1"``) or ``None``."""
    cached = _version_cache.get(base_url)
    if cached is not None:
        return cached
    try:
        # ``base_url`` is admin-set (validated at the probe_pulse entry
        # point via ``is_safe_http_url``) and not public input — see
        # ``logic/url_safety.py`` for the threat-model note backing the
        # CodeQL suppression below.
        url = base_url.rstrip("/") + "/api/version"
        r = await client.get(url, headers=_headers(token))  # lgtm[py/full-ssrf]
        if r.status_code != 200:
            return None
        body = r.json()
        if isinstance(body, dict):
            v = (body.get("version") or body.get("pulse")
                 or body.get("app") or "")
        else:
            v = ""
        v = str(v).strip()
        if v:
            _version_cache[base_url] = v
            return v
    except (httpx.HTTPError, OSError, ValueError, KeyError):
        pass
    return None


def _pulse_mounts(guest: dict, gib: float) -> list[dict]:
    """Extract per-mount disk entries from a Pulse guest record.

    Pulse exposes multi-mount data under several keys depending on
    version + guest type:
      - ``disks`` / ``filesystems`` — arrays of ``{name/mountpoint,
        used, total}``.
      - ``storage`` / ``mountpoints`` — similar arrays from LXC
        config.
      - ``mountpoints`` inside ``config`` — newer PVE output.

    We walk all the known paths and return a list in the same
    GiB-float shape Beszel's ``extra filesystems`` produce, so the
    frontend's DISKS card iterates one consistent schema.
    """
    out: list[dict] = []
    containers = []
    for k in ("disks", "filesystems", "mountpoints", "storage"):
        v = guest.get(k)
        if isinstance(v, list):
            containers.append(v)
        elif isinstance(v, dict):
            containers.append(list(v.values()))
    cfg = guest.get("config")
    if isinstance(cfg, dict):
        mp = cfg.get("mountpoints")
        if isinstance(mp, list):
            containers.append(mp)
        elif isinstance(mp, dict):
            containers.append(list(mp.values()))
    for arr in containers:
        for m in arr:
            if not isinstance(m, dict):
                continue
            name = str(
                m.get("mountpoint") or m.get("mp")
                or m.get("name") or m.get("path") or ""
            ).strip()
            if not name:
                continue
            total = _num(m.get("total") or m.get("max") or m.get("maxdisk") or m.get("size"))
            used = _num(m.get("used") or m.get("disk") or m.get("diskUsed"))
            # version-driven; magnitude fallback when
            # Pulse `/api/version` couldn't be reached.
            if _value_is_gib(total, _current_probe_base_url):
                total_gib = total
                used_gib = used
            else:
                total_gib = total / gib if total else 0
                used_gib = used / gib if used else 0
            out.append({
                "n": name,
                "d": total_gib,
                "du": used_gib,
                "dp": (used_gib / total_gib * 100) if total_gib > 0 else 0,
                "dr": 0, "dw": 0,
            })
    # Most-full first, matching Beszel's sort order.
    out.sort(key=lambda r: r.get("dp", 0), reverse=True)
    return out


def _pulse_net_ifaces(guest: dict) -> list[dict]:
    """Turn Pulse's network-interface payload into the shape the
    Hosts-view NETWORK card expects: ``[{name, mac, addrs: []}]``.

    Covers three variants seen across Pulse versions / guest types:
      - ``networkInterfaces`` (qemu guest-agent list; each entry has
        ``name`` + ``mac-address`` / ``mac`` + ``ip-addresses`` /
        ``addresses`` / ``ips``).
      - ``net`` (LXC config — dict keyed ``net0`` / ``net1`` / ... or
        a list; each entry's ``ip`` is a comma-separated string, MAC
        under ``hwaddr`` / ``mac``).
      - ``ips`` (flat list of strings — no MAC / NIC name, rendered
        under a single blank-named interface so nothing's hidden).
    """
    out: list[dict] = []
    nis = guest.get("networkInterfaces")
    if isinstance(nis, list):
        for ni in nis:
            if not isinstance(ni, dict):
                continue
            name = str(ni.get("name") or ni.get("n") or "").strip()
            if not name:
                continue
            mac = str(
                ni.get("mac-address") or ni.get("mac")
                or ni.get("hwaddr") or ni.get("m") or ""
            ).strip()
            raw_addrs = (
                ni.get("ip-addresses")
                or ni.get("addresses")
                or ni.get("ips")
                or ni.get("a")
                or []
            )
            addrs: list = []
            if isinstance(raw_addrs, list):
                for a in raw_addrs:
                    if isinstance(a, str):
                        addrs.append(a.strip())
                    elif isinstance(a, dict):
                        ip = a.get("ip-address") or a.get("ip") or a.get("addr")
                        prefix = a.get("prefix") or a.get("prefix-len")
                        if ip:
                            addrs.append(f"{ip}/{prefix}" if prefix else str(ip))
            out.append({
                "name": name,
                "mac": mac,
                "addrs": [a for a in addrs if a],
            })

    # LXC ``net`` dict variant — {"net0": {"name":"eth0","ip":"...","hwaddr":"..."}}
    nets = guest.get("net")
    if isinstance(nets, dict):
        nets = list(nets.values())
    if isinstance(nets, list):
        for ni in nets:
            if not isinstance(ni, dict):
                continue
            name = str(ni.get("name") or "eth0").strip()
            mac = str(ni.get("hwaddr") or ni.get("mac") or "").strip()
            raw_ip = ni.get("ip") or ni.get("ip6") or ""
            addrs = [p.strip() for p in str(raw_ip).split(",") if p.strip()]
            if name or addrs:
                out.append({"name": name, "mac": mac, "addrs": addrs})

    # Bare ``ips`` flat list — last-resort fallback.
    if not out:
        ips = guest.get("ips")
        if isinstance(ips, list) and ips:
            out.append({
                "name": "",
                "mac": "",
                "addrs": [str(a) for a in ips if a],
            })
    return out


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
    # ``base_url`` is admin-set (validated at the probe_pulse entry
    # point via ``is_safe_http_url``) and not public input — see
    # ``logic/url_safety.py`` for the threat-model note backing the
    # CodeQL suppression below.
    for p in paths:
        url = base_url.rstrip("/") + p
        try:
            r = await client.get(url, headers=_headers(token))  # lgtm[py/full-ssrf]
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e:  # noqa: BLE001
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
        except Exception as e:  # noqa: BLE001
            last_err = f"{p}: {e}"
            continue
        # /api/nodes returns a bare list; wrap it so the caller has a
        # uniform shape.
        if isinstance(j, list):
            return {"nodes": j}
        return j
    raise RuntimeError(f"pulse: no compatible endpoint responded — {last_err or '?'}")


def _extract_common_resource_fields(obj):
    """Shared mem / disk / uptime / cpu extraction used by BOTH
    ``extract_guest_stats`` and ``extract_node_stats``. Pulse's guest
    and node payloads carry the same top-level keys for these fields,
    so the unit-detection (bytes vs GiB) + magnitude-coercion math
    deserves one implementation.

    Returns ``(mem, mem_max, disk, disk_max, uptime, host_boot_ts,
    cpu_pct)`` with numeric values in BYTES (mem / disk),
    SECONDS (uptime), epoch SECONDS (host_boot_ts or None),
    PERCENT 0..100 (cpu_pct).
    """
    if not isinstance(obj, dict):
        obj = {}
    gib = 1024 ** 3
    mem = _num(obj.get("mem"))
    mem_max = _num(obj.get("maxmem"))
    disk = _num(obj.get("disk"))
    disk_max = _num(obj.get("maxdisk"))
    # version-driven; magnitude fallback.
    if _value_is_gib(mem_max, _current_probe_base_url):
        mem, mem_max = mem * gib, mem_max * gib
    if _value_is_gib(disk_max, _current_probe_base_url):
        disk, disk_max = disk * gib, disk_max * gib
    uptime = int(_num(obj.get("uptime")))
    host_boot_ts = (time.time() - uptime) if uptime > 0 else None
    cpu_pct = _num(obj.get("cpu")) * 100
    return mem, mem_max, disk, disk_max, uptime, host_boot_ts, cpu_pct


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
    mem, mem_max, disk, disk_max, uptime, host_boot_ts, cpu_pct = (
        _extract_common_resource_fields(guest)
    )
    # Try every key Pulse versions use for "is this a VM or LXC" —
    # ``type`` (common), ``kind``, ``vmtype`` (newer). Empty means
    # unknown and the UI hides the Proxmox row rather than rendering
    # a placeholder like "GUEST".
    kind = str(
        guest.get("type") or guest.get("kind") or guest.get("vmtype") or ""
    ).lower()

    # OS-family hints from Pulse, used as a FALLBACK layer — merged
    # BEFORE Beszel so the cleaner short forms override these when
    # Beszel matches too. Pulse's exact field layout varies between
    # versions: older v3 puts everything at the top of the guest
    # record; v4+ often nests the OS/agent info under sub-objects
    # like ``info`` / ``agent`` / ``config``. We look in all of
    # those so a VM with a QEMU guest-agent reporting via the
    # nested envelope still populates the SYSTEM card.
    def _looks_uuid(s: str) -> bool:
        """True when ``s`` looks like a PVE VM UUID (8-4-4-4-12 hex).
        Used to reject UUID-shaped values picked up from generic
        keys like ``id`` that aren't actually OS identity strings."""
        if len(s) < 32 or s.count("-") < 4:
            return False
        return all(c in "0123456789abcdef-" for c in s.lower())

    def _g(*keys):
        """Look up ``keys`` across the guest record and its common
        sub-objects, returning the first string-typed non-empty
        match. Rejects UUID-shaped values so generic field names
        don't smuggle VM IDs into identity rows."""
        sources = [guest]
        for nest in ("info", "agent", "config", "details", "stats", "osinfo"):
            nested = guest.get(nest)
            if isinstance(nested, dict):
                sources.append(nested)
        for src in sources:
            for k in keys:
                v = src.get(k)
                if v in (None, "", 0):
                    continue
                s = str(v).strip()
                if not s or _looks_uuid(s):
                    continue
                return s
        return ""

    # Only reach for keys that are actually OS identity strings —
    # avoid generic ones like ``os`` / ``distro`` / ``machine`` /
    # ``k`` that could be overloaded by PVE config. The _looks_uuid
    # guard above catches the remaining misfires.
    os_hint = _g("osName", "pretty_name", "prettyName", "osVersion",
                 "os_version")
    kernel_hint = _g("kernel", "kernelVersion", "kernel_version",
                     "kernel-release")
    arch_hint = _g("arch", "architecture", "cpuArch",
                   "cpu_arch", "platform_arch")
    # Platform field candidates — purposefully conservative. Pulse's
    # guest records include an ``id`` field that is often the PVE
    # VM UUID (e.g. ``"431e7b83-..."``), which is NOT a distro name.
    # Accepting any "id" key would wrongly surface the UUID as the
    # Platform value. Restrict to keys that are always string-typed
    # distro identifiers.
    platform_hint = _g("platform", "distro", "distroName", "distro_name")
    # Validate: anything that looks UUID-shaped or longer than 20
    # characters is almost certainly not a distro short-name. Drop
    # it so the osName-first-word fallback below kicks in.
    if platform_hint and (
        len(platform_hint) > 24 or
        (platform_hint.count("-") >= 4 and
         all(c in "0123456789abcdef-" for c in platform_hint.lower()))
    ):
        platform_hint = ""
    # osName is often the long PRETTY_NAME ("Debian GNU/Linux 13 (trixie)").
    # If we have that but no platform, derive "debian" from the first
    # word so the Platform row isn't blank. The SYSTEM card hides
    # redundant platform-prefix-of-os anyway.
    if not platform_hint and os_hint:
        first = os_hint.split()[0] if os_hint.split() else ""
        platform_hint = first
    # Some Pulse versions emit ``osName`` like "debian 13.4"; others
    # like "Debian GNU/Linux 13 (trixie)". Prefer the shorter form
    # when both platform-ish and os-ish values are available.
    if platform_hint and os_hint and os_hint.lower().startswith(
        platform_hint.lower()):
        # The UI hides the duplicate row via startsWith comparison.
        pass
    # Network interfaces — Pulse emits ``networkInterfaces`` (qemu
    # guest-agent) or ``net`` / ``ip`` fields (LXC config). Normalise
    # into the same {name, mac, addrs:[]} shape Beszel uses so the
    # frontend's NETWORK card renders identically regardless of
    # which provider supplied the data.
    net_ifaces = _pulse_net_ifaces(guest)
    # Mounts — Pulse sometimes exposes per-filesystem breakdown via
    # ``disks`` / ``filesystems`` / ``mountpoints`` (exact key varies
    # between versions and between qemu vs lxc). We harvest from
    # every known shape, then fall back to a synthesised ``/`` entry
    # built from the aggregate ``disk`` / ``maxdisk`` numbers so the
    # DISKS card always has something to render when Pulse matched.
    synth_mounts: list = _pulse_mounts(guest, gib)
    if not synth_mounts and disk_max > 0:
        synth_mounts.append({
            "n": "/",
            "d": disk_max / gib,  # extract_stats-shape: GiB float
            "du": disk / gib,
            "dp": (disk / disk_max * 100) if disk_max > 0 else 0,
            "dr": 0, "dw": 0,
        })
    return {
        "host_mem_total": int(mem_max),
        "host_mem_used": int(mem),
        "host_mem_avail": max(0, int(mem_max - mem)),
        "host_disk_total": int(disk_max),
        "host_disk_used": int(disk),
        "host_disk_free": max(0, int(disk_max - disk)),
        "host_uptime_s": uptime,
        "host_boot_ts": host_boot_ts,
        "host_cpu_percent": cpu_pct,
        "host_mem_percent": (mem / mem_max * 100) if mem_max > 0 else 0,
        "host_disk_percent": (disk / disk_max * 100) if disk_max > 0 else 0,
        "host_cores": int(_num(guest.get("maxcpu"))),
        # Fallback layer — Beszel / node-exporter override these
        # when they match the same host. Empty on pre-schema Pulse
        # guests (host_platform derived from osName first word).
        "host_platform": platform_hint,
        "host_agent": "",
        "host_kernel": kernel_hint,
        "host_arch": _normalize_arch(arch_hint) if arch_hint else "",
        # host_os: only the Pulse guest's osName (when present) as a
        # best-effort hint for Beszel-less hosts; Beszel's real value
        # still wins via _merge_best when both providers match.
        "host_os": os_hint,
        # Single synthesised "/" entry from Pulse's aggregate disk —
        # superseded by Beszel's real per-mount list when both
        # providers match the same host.
        "mounts": synth_mounts,
        # Network interfaces extracted from Pulse guest config — same
        # shape as Beszel's network_ifaces so the frontend doesn't
        # care which provider filled the array.
        "network_ifaces": net_ifaces,
        "exporter_error": None,
        "pulse_status": str(guest.get("status") or "unknown"),
        # Empty when we can't determine kind — template's ``x-if``
        # hides the Proxmox row in that case so we never show a
        # placeholder like "GUEST" next to "Proxmox".
        "pulse_kind": kind,
        "pulse_vmid": int(_num(guest.get("vmid"))),
        "pulse_node": str(guest.get("node") or ""),
    }


# noinspection DuplicatedCode
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
    mem, mem_max, disk, disk_max, uptime, host_boot_ts, cpu_pct = (
        _extract_common_resource_fields(node)
    )
    # Pulse emits CPU as 0..1; the helper already scales × 100.
    kernel = str(node.get("kernel") or "")
    # Pulse's node payload doesn't carry arch, so the extractor
    # used to return empty. Infer `x86_64` when the kernel ends with
    # `-pve` (Proxmox stock kernels are almost always x86_64). NE /
    # Beszel still override when the operator runs an agent on the
    # hypervisor itself; this is purely the PVE-only-host fallback.
    inferred_arch = ""
    if kernel.lower().rstrip().endswith("-pve"):
        inferred_arch = _normalize_arch("x86_64")
    return {
        "host_mem_total": int(mem_max),
        "host_mem_used": int(mem),
        "host_mem_avail": max(0, int(mem_max - mem)),
        "host_disk_total": int(disk_max),
        "host_disk_used": int(disk),
        "host_disk_free": max(0, int(disk_max - disk)),
        "host_uptime_s": uptime,
        "host_boot_ts": host_boot_ts,
        "host_cpu_percent": cpu_pct,
        "host_mem_percent": (mem / mem_max * 100) if mem_max > 0 else 0,
        "host_disk_percent": (disk / disk_max * 100) if disk_max > 0 else 0,
        "host_cores": int(_num(node.get("maxcpu"))),
        "host_kernel": kernel,
        "host_platform": str(node.get("pveversion") or "Proxmox VE"),
        "host_os": str(node.get("os") or ""),
        "host_arch": inferred_arch,
        "host_agent": str(node.get("pveversion") or ""),
        "mounts": [],
        "exporter_error": None,
        "pulse_status": str(node.get("status") or "unknown"),
    }


def _register_pulse_host_keys(
    record: dict,
    stats: dict,
    add: Callable,
    *,
    name_keys: tuple[str, ...],
    id_keys: tuple[str, ...],
) -> list[str]:
    """Index a Pulse host record under every name + id key the operator
    might use to look it up. Shared between the ``hosts`` and
    ``dockerHosts`` array walkers — they only differ in which name /
    id keys their schema uses. Sets ``stats["pulse_name"]`` to the
    first key registered, mirroring the original inline behaviour.
    """
    keys_added: list[str] = []
    for alt in name_keys:
        v = (record.get(alt) or "").strip() if isinstance(record.get(alt), str) else ""
        if v and v not in keys_added:
            add(v, stats)
            keys_added.append(v)
    host_id_raw = None
    for k in id_keys:
        host_id_raw = record.get(k)
        if host_id_raw:
            break
    if isinstance(host_id_raw, str) and host_id_raw.strip():
        hid = host_id_raw.strip()
        if hid not in keys_added:
            add(hid, stats)
            keys_added.append(hid)
    stats["pulse_name"] = keys_added[0] if keys_added else ""
    return keys_added


# Latches True when the most recent probe attempt raised a
# ConnectError / Timeout (hub unreachable) — the next call uses the
# SHORT timeout (`tuning_pulse_probe_timeout_unreachable_seconds`)
# to fail fast instead of blocking the cold-cache caller for the
# full 15s. Latches back to False on next success so a recovered
# hub immediately gets its normal timeout. Single-process single-
# replica → plain module flag is correct. Mirrors the Beszel shape.
_last_probe_unreachable: list[bool] = [False]


def _mark_unreachable() -> None:
    """Flag the most recent Pulse hub probe as unreachable."""
    _last_probe_unreachable[0] = True


def _mark_reachable() -> None:
    """Clear the Pulse hub unreachable flag."""
    _last_probe_unreachable[0] = False


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

    When the most recent probe was unreachable, the caller-supplied
    timeout is overridden with the short
    `tuning_pulse_probe_timeout_unreachable_seconds` (default 3s) so
    cold-cache callers fail fast instead of all paying the full
    15s during an outage. Latches back to the caller's timeout on
    the next successful probe.
    """
    if not base_url or not token:
        return {"hosts": {}, "error": "pulse: missing url or token"}
    if _last_probe_unreachable[0]:
        try:
            from logic.tuning import Tunable as _Tunable, tuning_int as _tuning_int
            timeout = float(_tuning_int(_Tunable.PULSE_PROBE_TIMEOUT_UNREACHABLE_SECONDS))
        except (KeyError, ValueError, TypeError, ImportError):
            timeout = 3.0
    # Defence-in-depth on the admin-only Pulse URL setting. CodeQL
    # py/full-ssrf flags `client.get(url, ...)` inside `_fetch_state` —
    # see ``logic/url_safety.py`` for the threat-model rationale and
    # the suppression markers on the call sites.
    from logic.url_safety import is_safe_http_url as _safe_url
    if not _safe_url(base_url):
        return {
            "hosts": {},
            "error": "pulse: invalid url — must be http:// or https:// with a hostname",
        }
    # set the module-level base-url hint BEFORE we
    # call any extractor so `_value_is_gib(..., base_url)` consults
    # the right cache entry. Probe `/api/version` once per (base_url)
    # pair to populate `_version_cache`; the magnitude heuristic is a
    # silent fallback when the endpoint is unreachable / older Pulse.
    global _current_probe_base_url
    _current_probe_base_url = base_url
    try:
        async with httpx.AsyncClient(verify=verify_tls, timeout=timeout) as client:
            await _fetch_version(client, base_url, token)
            state = await _fetch_state(client, base_url, token)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        # Surface the failure in stdout so it lands in Admin → Logs.
        # Pre-fix the error string was returned silently in the API
        # response and users saw "Pulse: down" on the Hosts page
        # with no log entry explaining why. Print BEFORE
        # returning so the same error reaches the persistent log
        # capture even when callers don't surface the response field.
        print(f"[pulse] probe failed: {type(e).__name__}: {e} "
              f"url={base_url!r} verify_tls={verify_tls}")
        # Latch unreachable on ConnectError / Timeout so the next
        # cold-cache caller uses the SHORT timeout. Same shape as
        # the Beszel unreachable-latch pattern.
        if isinstance(e, (
                httpx.ConnectError, httpx.ConnectTimeout,
                httpx.ReadTimeout, httpx.RemoteProtocolError,
                OSError,
        )):
            _mark_unreachable()
        return {"hosts": {}, "error": str(e)}
    # Nodes live at ``state.nodes`` in every Pulse version we've seen.
    nodes = state.get("nodes") or []

    def _looks_like_guest(item) -> bool:
        """Heuristic — a dict with a ``vmid`` or ``id`` and one of the
        PVE-style status keys looks like a guest record regardless of
        which array we found it in.

        REVERTED on 2026-05-09 from a tighter ``cpu + maxmem``
        requirement. The tightening correctly excluded backup /
        storage records but ALSO excluded Docker container records
        from Pulse v4's ``containers`` / ``dockerHosts`` arrays
        (which lack the PVE-guest cpu/maxmem fingerprint). Operators
        whose curated `pulse_name` mapped to a Docker container saw
        every Pulse probe miss → 5 consecutive failures → fleet-wide
        auto-pause cascade. Net behaviour with this looser gate:
        non-guest records still get harvested + extracted as empty
        host_* fields (chart shows zero) but the lookup HITS so the
        host doesn't auto-pause. Proper fix tracked separately:
        teach extract_guest_stats to handle Docker container shape.
        """
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
        collected: list = []
        if isinstance(container, list):
            for item in container:
                if _looks_like_guest(item):
                    if inherited_node and not item.get("node"):
                        item = {**item, "node": inherited_node}
                    collected.append(item)
                elif isinstance(item, (list, dict)):
                    collected.extend(_harvest(item, inherited_node))
        elif isinstance(container, dict):
            # When walking the state root, don't recurse into
            # ``nodes`` again (we handle those separately) so a guest
            # that somehow contains a node-shaped sub-object doesn't
            # double-count.
            for k, _v in container.items():
                if k == "nodes":
                    continue
                collected.extend(_harvest(_v, inherited_node))
        return collected

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
    # Schema-discovery diagnostic for Pulse v4's `hosts` / `containers`
    # top-level arrays. Pulse v4 hosts records (Pulse-agent-tracked Linux
    # hosts, not PVE guests) live in this array. Currently
    # `extract_guest_stats` is shaped for PVE-guest
    # schema (cpu top-level / mem / maxmem / disk / maxdisk) and reads
    # zeros from Pulse hosts records because their schema is different.
    # This dump captures hosts[0] + containers[0] sample shapes so we
    # can write a proper extractor.
    import json as _dbg_json
    for top_key in ("hosts", "containers", "dockerHosts"):
        arr = state.get(top_key) or []
        if isinstance(arr, list) and arr:
            sample = arr[0] or {}
            if isinstance(sample, dict):
                print(f"[pulse] probe: state.{top_key}[0] fields={sorted(sample.keys())} "
                      f"count={len(arr)}")
                try:
                    raw = _dbg_json.dumps(sample, default=str)[:1200]
                except (TypeError, ValueError):
                    raw = repr(sample)[:1200]
                print(f"[pulse] probe: state.{top_key}[0] RAW={raw}")
    # Dump the raw shape of the first node + guest so operators can
    # see what fields Pulse actually emits.
    if nodes:
        print(f"[pulse] probe: sample node fields={sorted((nodes[0] or {}).keys())}")
    if guests:
        g0 = guests[0] or {}
        print(f"[pulse] probe: sample guest fields={sorted(g0.keys())} "
              f"name={g0.get('name')!r} vmid={g0.get('vmid')!r} "
              f"type={g0.get('type')!r} node={g0.get('node')!r}")
        # Dump the full raw record (truncated to 1200 chars per line
        # to keep the log navigable) so we can see WHICH fields carry
        # osName / kernel / arch / platform on this Pulse version.
        # Only fires on the first guest so the log isn't flooded.
        import json as _dbg_json
        try:
            raw = _dbg_json.dumps(g0, default=str)[:1200]
        except (TypeError, ValueError):
            raw = repr(g0)[:1200]
        print(f"[pulse] probe: sample guest RAW={raw}")
        # Also dump sub-object keys since Pulse sometimes nests the
        # OS-family data under ``info`` / ``agent`` / ``config``.
        for nest_key in ("info", "agent", "config", "stats", "details"):
            nested = g0.get(nest_key)
            if isinstance(nested, dict):
                print(f"[pulse] probe: sample guest.{nest_key} keys="
                      f"{sorted(nested.keys())}")
    # Keyed by display name (preserves case). We also maintain a parallel
    # lowercased-trimmed index so the caller's ``_lookup`` helper can
    # match ``Docker`` / ``  docker `` / the guest's vmid without the
    # operator having to type it pixel-perfect.
    out: dict[str, dict] = {}

    def _add(key: str, entry_stats: dict):
        """Index `entry_stats` under `key` in the per-host result map (skip blanks)."""
        key = (key or "").strip()
        if not key:
            return
        out[key] = entry_stats

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
    # Dump per-mount detection on the first guest so operators can
    # see whether Pulse is even emitting multi-mount data. The
    # ``_pulse_mounts`` helper walks disks / filesystems /
    # mountpoints / storage / config.mountpoints — empty output
    # means Pulse isn't reporting those keys for this guest type,
    # and we fall back to the synthesised single "/" entry.
    if guests:
        g0 = guests[0] or {}
        mp_keys: list[str] = [k for k in ("disks", "filesystems", "mountpoints",
                                          "storage") if k in g0]
        if isinstance(g0.get("config"), dict) and "mountpoints" in g0["config"]:
            mp_keys.append("config.mountpoints")
        parsed = _pulse_mounts(g0, 1024 ** 3)
        print(f"[pulse] sample guest mount keys present={mp_keys} "
              f"parsed_count={len(parsed)} "
              f"names={[m.get('n') for m in parsed[:5]]}")

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
    # Pulse v4 `hosts` array — Pulse-agent-tracked Linux hosts (NOT
    # PVE guests). Real curated hosts are tracked here, not in `vms`.
    # Walked separately from the generic `_harvest` because (a) these have
    # a different schema than PVE guests/nodes; (b) extract via
    # `extract_pulse_host_stats` instead of `extract_guest_stats`
    # which assumes PVE shape and would return zeros.
    pulse_hosts_arr = state.get("hosts") or []
    pulse_hosts_added = 0
    if isinstance(pulse_hosts_arr, list):
        for ph in pulse_hosts_arr:
            if not isinstance(ph, dict):
                continue
            stats = extract_pulse_host_stats(ph)
            # Identity keys: prefer the agent-reported `host`
            # (operator's curated `pulse_name` typically matches
            # this), then `hostname`, then `name`. Each non-empty
            # alias gets registered so lookup is forgiving.
            keys_added = _register_pulse_host_keys(
                ph, stats, _add,
                name_keys=("host", "hostname", "name"),
                id_keys=("id",),
            )
            if keys_added:
                pulse_hosts_added += 1
    print(f"[pulse] probe: pulse_hosts_array_added={pulse_hosts_added} "
          f"(of {len(pulse_hosts_arr) if isinstance(pulse_hosts_arr, list) else 0} entries in state.hosts)")

    # Pulse v4 `dockerHosts` array — Docker daemons (separate from
    # the `containers` array which is per-container PVE LXCs).
    # Schema is similar to `hosts` but uses `cpuUsagePercent` /
    # `totalMemoryBytes` / `cpus` instead of `cpuUsage` / `memory.total`
    # / `cpuCount`. extract_pulse_host_stats handles both via the
    # multi-path probes.
    pulse_docker_hosts_arr = state.get("dockerHosts") or []
    pulse_docker_hosts_added = 0
    if isinstance(pulse_docker_hosts_arr, list):
        for dh in pulse_docker_hosts_arr:
            if not isinstance(dh, dict):
                continue
            stats = extract_pulse_host_stats(dh)
            stats["pulse_kind"] = "docker_host"  # distinguishable from plain "host"
            keys_added = _register_pulse_host_keys(
                dh, stats, _add,
                name_keys=("hostname", "displayName", "name"),
                id_keys=("id", "agentId", "machineId"),
            )
            if keys_added:
                pulse_docker_hosts_added += 1
    print(f"[pulse] probe: pulse_docker_hosts_added={pulse_docker_hosts_added} "
          f"(of {len(pulse_docker_hosts_arr) if isinstance(pulse_docker_hosts_arr, list) else 0} entries in state.dockerHosts)")
    print(f"[pulse] probe: indexed keys={sorted(out.keys())}")
    # Latch reachable on success so the next probe gets its full
    # timeout back. Mirrors the Beszel reachable-latch pattern.
    _mark_reachable()
    return {"hosts": out, "error": None}


def extract_pulse_host_stats(host: dict) -> dict:
    """Shape one Pulse v4 ``hosts[*]`` or ``dockerHosts[*]`` record
    into ``host_*`` fields.

    Distinct from :func:`extract_guest_stats` (PVE LXC/VM in the
    ``vms`` / ``containers`` arrays — uses ``cpu`` / ``maxmem`` /
    ``maxdisk`` shape) and :func:`extract_node_stats` (PVE
    hypervisor in the ``nodes`` array). Pulse v4 added top-level
    ``hosts`` (Pulse-agent-tracked Linux + Windows hosts) and
    ``dockerHosts`` (Docker daemons) arrays. Both share a similar
    object shape but the field names differ:

    * ``hosts[*]``: ``cpuUsage`` (0..100 %, NOT fraction),
      ``memory.{total,used,free,usage}``, ``disks[].{total,used}``,
      ``diskIO[].{readBytes,writeBytes}``,
      ``networkInterfaces[].{rxBytes,txBytes}``,
      ``uptimeSeconds``, ``cpuCount``, ``status`` ("online"),
      ``hostname``, ``displayName``, ``osName``, ``osVersion``,
      ``kernelVersion``, ``architecture``, ``platform``,
      ``agentVersion``, ``loadAverage``.
    * ``dockerHosts[*]``: ``cpuUsagePercent`` (different field
      name!), ``totalMemoryBytes`` (alongside ``memory.total``),
      ``cpus`` (count), ``runtimeVersion``,
      otherwise mostly the same shape.

    Defensive multi-path probing covers both schemas. Returns a
    skeleton with `host_*` keys populated (or 0 / "" when missing).
    """
    if not isinstance(host, dict):
        host = {}

    def _walk(path_keys):
        """Walk a path of dict keys + list indexes through the host
        record. Returns the value or None on any miss."""
        cursor = host
        for k in path_keys:
            if isinstance(k, int) and isinstance(cursor, list):
                if k < 0 or k >= len(cursor):
                    return None
                cursor = cursor[k]
            elif isinstance(cursor, dict) and k in cursor:
                cursor = cursor[k]
            else:
                return None
        return cursor

    def _first_non_zero(*paths):
        for path_keys in paths:
            cursor = _walk(path_keys)
            if cursor is None:
                continue
            v = _num(cursor)
            if v != 0:
                return v
        return 0.0

    def _first_str(*paths):
        for path_keys in paths:
            cursor = _walk(path_keys)
            if isinstance(cursor, str) and cursor.strip():
                return cursor.strip()
        return ""

    # CPU% — Pulse v4 emits as percent (0..100) under multiple
    # field names depending on whether it's a Linux/Windows host
    # (`cpuUsage`) or a Docker host (`cpuUsagePercent`). PVE-style
    # nested fallbacks (`info.cpu`, `stats.cpu`) only kick in if
    # neither v4 path matched.
    raw_cpu = _first_non_zero(["cpuUsage"], ["cpuUsagePercent"],
                              ["cpu"], ["info", "cpu"], ["stats", "cpu"])
    # If the value sneaks in as a 0..1 fraction (PVE-style legacy),
    # promote to percent. Anything > 1.5 is already a percent.
    cpu_pct = raw_cpu * 100 if 0 < raw_cpu <= 1.5 else raw_cpu

    # Memory — Pulse v4 nests under `memory` (bytes) AND emits
    # `totalMemoryBytes` as a top-level fallback on dockerHosts.
    mem_max = _first_non_zero(
        ["memory", "total"], ["totalMemoryBytes"],
        ["maxmem"], ["memTotal"], ["mem", "total"],
    )
    mem_used = _first_non_zero(
        ["memory", "used"], ["mem"], ["memUsed"], ["mem", "used"],
    )
    # Free is sometimes the only path provided; derive used from total-free.
    if mem_used == 0 and mem_max > 0:
        mem_free = _first_non_zero(["memory", "free"])
        if 0 < mem_free < mem_max:
            mem_used = mem_max - mem_free

    # Disk — Pulse v4's `disks[]` is an array; first entry is the
    # primary mount. PVE-style top-level `maxdisk` / `disk.total`
    # fallbacks for legacy schemas.
    disk_max = _first_non_zero(
        ["disks", 0, "total"], ["maxdisk"], ["diskTotal"], ["disk", "total"],
    )
    disk_used = _first_non_zero(
        ["disks", 0, "used"], ["disk"], ["diskUsed"], ["disk", "used"],
    )

    # Uptime — Pulse v4 uses `uptimeSeconds` (long form). PVE-style
    # `uptime` is the legacy fallback.
    uptime = int(_first_non_zero(["uptimeSeconds"], ["uptime"], ["info", "u"]))
    host_boot_ts = (time.time() - uptime) if uptime > 0 else None

    # Cores — `cpuCount` (Linux/Windows hosts), `cpus` (Docker hosts).
    cores = int(_first_non_zero(["cpuCount"], ["cpus"], ["info", "t"]))

    # Identity / metadata — first non-empty string across plausible paths.
    hostname = _first_str(["hostname"], ["displayName"], ["host"], ["name"])
    platform = _first_str(["platform"], ["osName"]) or "Linux"
    osname = _first_str(["osName"], ["osVersion"], ["os"])
    kernel = _first_str(["kernelVersion"], ["kernel"])
    arch = _first_str(["architecture"], ["arch"])
    agent = _first_str(["agentVersion"], ["agent"], ["dockerVersion"], ["runtimeVersion"])

    # Mem / disk percent — pre-computed if Pulse supplies, else derive.
    mem_pct = _first_non_zero(["memory", "usage"])
    if mem_pct == 0 and mem_max > 0:
        mem_pct = (mem_used / mem_max) * 100
    disk_pct = _first_non_zero(["disks", 0, "usage"])
    if disk_pct == 0 and disk_max > 0:
        disk_pct = (disk_used / disk_max) * 100

    # Network counter aggregates (sum across non-loopback interfaces).
    # Used by the host-net rate fallback path. v4 emits `rxBytes` /
    # `txBytes` per-iface (camelCase).
    rx_total = 0
    tx_total = 0
    nis = host.get("networkInterfaces") if isinstance(host, dict) else None
    if isinstance(nis, list):
        for ni in nis:
            if not isinstance(ni, dict):
                continue
            name = str(ni.get("name") or "").lower()
            # Skip loopback / docker bridges to align with the
            # node-exporter convention used by host_net_sampler.
            if (name.startswith("lo") or name.startswith("docker")
                or name.startswith("br-") or name.startswith("veth")
                or name.startswith("cni") or name.startswith("flannel")
                or name.startswith("vmnet") or name.startswith("vEthernet")):
                continue
            rx_total += int(_num(ni.get("rxBytes")))
            tx_total += int(_num(ni.get("txBytes")))

    # Load average — Pulse hosts emit a 3-element array.
    la = host.get("loadAverage")
    load_1m = float(la[0]) if isinstance(la, list) and len(la) > 0 else 0.0
    load_5m = float(la[1]) if isinstance(la, list) and len(la) > 1 else 0.0
    load_15m = float(la[2]) if isinstance(la, list) and len(la) > 2 else 0.0

    return {
        "host_mem_total": int(mem_max),
        "host_mem_used": int(mem_used),
        "host_mem_avail": max(0, int(mem_max - mem_used)),
        "host_disk_total": int(disk_max),
        "host_disk_used": int(disk_used),
        "host_disk_free": max(0, int(disk_max - disk_used)),
        "host_uptime_s": uptime,
        "host_boot_ts": host_boot_ts,
        "host_cpu_percent": cpu_pct,
        "host_mem_percent": mem_pct,
        "host_disk_percent": disk_pct,
        "host_cores": cores,
        "host_platform": platform,
        "host_agent": agent,
        "host_kernel": kernel,
        "host_arch": _normalize_arch(arch) if arch else "",
        "host_os": osname,
        "host_load_1m": load_1m,
        "host_load_5m": load_5m,
        "host_load_15m": load_15m,
        "host_net_rx_total_bytes": rx_total if rx_total > 0 else None,
        "host_net_tx_total_bytes": tx_total if tx_total > 0 else None,
        "mounts": [],
        "network_ifaces": [],
        "exporter_error": None,
        "pulse_status": _first_str(["status"]) or "unknown",
        "pulse_kind": "host",
        "pulse_vmid": 0,
        "pulse_node": "",
        "pulse_hostname": hostname,
    }


# noinspection DuplicatedCode
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


# Public alias for cross-module use (main.py's debug endpoint reaches
# for the raw state envelope to dump alongside the matched record).
fetch_state = _fetch_state
