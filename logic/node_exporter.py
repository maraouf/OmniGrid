"""node-exporter integration — host-level stats (disk, RAM, uptime).

Portainer's API only exposes Docker's view of a node. For anything
host-wide (real disk usage across /, /mnt, /root; real host memory;
host uptime vs. oldest-running-task proxy), OmniGrid queries each
node's ``prom/node-exporter`` on :9100/metrics. The exporter is the
homelab-standard path for this; operators deploy it once per host
(usually as a Swarm global service) and OmniGrid scrapes it
during gather.

What we parse:
  - ``node_filesystem_size_bytes`` / ``node_filesystem_avail_bytes``
    → per-mount + aggregated host disk totals. Excludes pseudo-fs
    (tmpfs, overlay, squashfs, procfs, sysfs) and Docker/k8s bind-
    mounts so the number matches what ``df -h`` would show.
  - ``node_memtotal_bytes`` / ``node_memory_MemAvailable_bytes`` →
    true host memory (vs. the sum of container limits we had before).
  - ``node_boot_time_seconds`` → host uptime — the real signal, not
    "oldest running task on this node".
  - ``node_cpu_seconds_total`` → optional, for future host-CPU tile.

Caller contract: :func:`probe_node` returns a dict with host_* fields
or an ``exporter_error`` string on failure. Failures are per-node and
never raise — a dead exporter on one host shouldn't blank the fleet.
"""
from __future__ import annotations

import re
from typing import Optional

import httpx


# Filesystems we don't count toward host disk totals. These are either
# virtual (procfs, sysfs, tmpfs, overlay) or Docker-internal mounts
# that'd double-count the Docker footprint we already show separately.
_EXCLUDED_FSTYPES = {
    "tmpfs", "devtmpfs", "squashfs", "overlay", "overlay2", "aufs",
    "fuse.gvfsd-fuse", "fuse.lxcfs", "nsfs", "proc", "sysfs", "cgroup",
    "cgroup2", "ramfs", "rpc_pipefs", "mqueue", "devpts", "securityfs",
    "configfs", "debugfs", "hugetlbfs", "pstore", "tracefs", "autofs",
    "binfmt_misc", "fusectl", "bpf",
}

# Mountpoints we skip regardless of fstype — Docker's own dirs, k8s
# bind-mounts, snap squashes. Expressed as startswith() prefixes.
_EXCLUDED_MOUNT_PREFIXES = (
    "/proc", "/sys", "/dev", "/run",
    "/var/lib/docker", "/var/lib/containerd", "/var/lib/kubelet",
    "/snap/", "/var/snap",
    "/host/proc", "/host/sys",  # if exporter is containerised with --pid=host
)


# Lenient Prometheus exposition-format matcher. We only care about
# labelled and unlabelled samples for a specific whitelist of metric
# names; counters / gauges / histograms are all single-value lines for
# the ones we query, so a simple line-regex is enough — no need to
# vendor a full exposition parser for five metrics.
_LINE_RE = re.compile(
    r"""^
    (?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)       # metric name
    (?:\{(?P<labels>[^}]*)\})?                # optional {k="v",k2="v2"}
    \s+
    (?P<value>[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?|NaN|\+Inf|-Inf)
    (?:\s+\d+)?                               # optional timestamp (ignored)
    \s*$
    """,
    re.VERBOSE,
)

_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:[^"\\]|\\.)*)"')


def _parse_labels(raw: str) -> dict[str, str]:
    """Parse a ``k="v",k2="v2"`` label blob into a dict."""
    out: dict[str, str] = {}
    for m in _LABEL_RE.finditer(raw):
        # Unescape the few characters Prometheus allows backslash-escaped.
        v = m.group(2).replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n')
        out[m.group(1)] = v
    return out


def parse_exporter_text(text: str) -> dict:
    """Extract host_* stats from a node-exporter /metrics payload.

    Returns a dict with keys:
      host_disk_total, host_disk_used, host_disk_free,
      host_mem_total, host_mem_used, host_mem_avail,
      host_boot_ts,
      mounts: [{mountpoint, fstype, size, used}]

    Missing metrics degrade gracefully to 0 / None — a kernel without
    filesystem_exporter enabled (rare) still yields useful memory /
    uptime numbers instead of aborting.
    """
    # Per-mount accumulators so we can both aggregate and expose the
    # breakdown. Keyed by mountpoint so the same-labelled "size" and
    # "avail" samples can be zipped into a single entry.
    fs: dict[str, dict] = {}
    mem_total = 0
    mem_avail = 0
    # host_* identity fields, populated from ``node_uname_info``
    # (``sysname`` / ``machine`` / ``release``) and the distinct
    # ``cpu=`` labels on ``node_cpu_seconds_total``. These make it so
    # Linux / FreeBSD hosts monitored only by node-exporter still
    # fill in the SYSTEM + HARDWARE cards rather than leaving every
    # row empty on the Hosts tab.
    uname_sysname = ""
    uname_machine = ""
    uname_release = ""
    cpu_labels: set[str] = set()
    # FreeBSD / OPNsense fallback buckets — the exporter on those
    # systems emits ``node_memory_size_bytes`` for total and splits
    # "available" across free + inactive + laundry. We accumulate
    # whichever buckets appear and derive the Linux-shaped values
    # after the scan if the direct metrics were absent.
    bsd_mem_total = 0
    bsd_mem_free = 0
    bsd_mem_inactive = 0
    bsd_mem_laundry = 0
    bsd_mem_cache = 0
    # Device labels seen so we can dedup ZFS subdatasets that share
    # the same underlying pool (every ``zroot/...`` dataset reports
    # the pool's size/avail, so naive summing multiplies the real
    # total by the number of datasets).
    fs_labels: dict[str, dict] = {}
    boot_ts = 0.0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        if name == "node_memtotal_bytes":
            mem_total = int(value)
        elif name == "node_memory_MemAvailable_bytes":
            mem_avail = int(value)
        # --- FreeBSD memory buckets ---
        elif name == "node_memory_size_bytes":
            bsd_mem_total = int(value)
        elif name == "node_memory_free_bytes":
            bsd_mem_free = int(value)
        elif name == "node_memory_inactive_bytes":
            bsd_mem_inactive = int(value)
        elif name == "node_memory_laundry_bytes":
            bsd_mem_laundry = int(value)
        elif name == "node_memory_cache_bytes":
            bsd_mem_cache = int(value)
        elif name == "node_boot_time_seconds" or name == "node_boot_time":
            # Pre-0.16 node-exporter used "node_boot_time" without the
            # _seconds suffix. Accept both so older deploys work.
            boot_ts = value
        elif name == "node_uname_info":
            labels = _parse_labels(m.group("labels") or "")
            uname_sysname = labels.get("sysname") or uname_sysname
            uname_machine = labels.get("machine") or uname_machine
            uname_release = labels.get("release") or uname_release
        elif name == "node_cpu_seconds_total":
            labels = _parse_labels(m.group("labels") or "")
            cpu = labels.get("cpu")
            if cpu:
                cpu_labels.add(cpu)
        elif name in ("node_filesystem_size_bytes", "node_filesystem_avail_bytes"):
            labels = _parse_labels(m.group("labels") or "")
            mount = labels.get("mountpoint") or ""
            fstype = labels.get("fstype") or ""
            device = labels.get("device") or ""
            if not mount:
                continue
            if fstype in _EXCLUDED_FSTYPES:
                continue
            if any(mount.startswith(p) for p in _EXCLUDED_MOUNT_PREFIXES):
                continue
            entry = fs.setdefault(mount, {"mountpoint": mount, "fstype": fstype,
                                         "device": device, "size": 0, "avail": 0})
            entry["fstype"] = fstype or entry.get("fstype") or ""
            entry["device"] = device or entry.get("device") or ""
            if name == "node_filesystem_size_bytes":
                entry["size"] = int(value)
            else:
                entry["avail"] = int(value)

    # FreeBSD fallbacks — if the Linux-shaped metrics never appeared,
    # derive the same shape from the BSD buckets so downstream code
    # doesn't care which OS the agent ran on.
    if mem_total == 0 and bsd_mem_total > 0:
        mem_total = bsd_mem_total
    if mem_avail == 0 and (bsd_mem_free or bsd_mem_inactive or bsd_mem_laundry or bsd_mem_cache):
        # "Reclaimable memory" ≈ free + inactive + laundry + cache, the
        # FreeBSD analogue of Linux's MemAvailable.
        mem_avail = bsd_mem_free + bsd_mem_inactive + bsd_mem_laundry + bsd_mem_cache

    # Finalise mount list — compute used, drop any rows where size is 0
    # (kernel readahead race / unreadable mount).
    mounts: list[dict] = []
    total_size = 0
    total_free = 0
    for entry in fs.values():
        size = entry.get("size", 0)
        avail = entry.get("avail", 0)
        if size <= 0:
            continue
        used = max(0, size - avail)
        mounts.append({
            "mountpoint": entry["mountpoint"],
            "fstype": entry.get("fstype") or "",
            "size": size,
            "used": used,
        })
        total_size += size
        total_free += avail
    mounts.sort(key=lambda m: m["mountpoint"])
    total_used = max(0, total_size - total_free)
    # Normalise machine label → common arch name so the UI's
    # "Architecture" row reads the same whether the host runs FreeBSD
    # (``amd64``), Linux (``x86_64``), or ARM (``aarch64`` /
    # ``armv7l``). We keep the source-agreeing labels verbatim; only
    # map obvious aliases.
    arch = uname_machine
    if arch == "amd64":
        arch = "x86_64"  # harmonise with how Beszel + most Linux tools label it
    return {
        "host_disk_total": total_size,
        "host_disk_used": total_used,
        "host_disk_free": total_free,
        "host_mem_total": mem_total,
        "host_mem_used": max(0, mem_total - mem_avail) if mem_total else 0,
        "host_mem_avail": mem_avail,
        "host_boot_ts": boot_ts or None,
        "mounts": mounts,
        # Identity / hardware — all optional. node-exporter runs LAST
        # in the merge so these values are authoritative for Linux /
        # FreeBSD hosts.
        "host_kernel":    uname_release,
        "host_arch":      arch,
        "host_platform":  uname_sysname,   # "Linux" / "FreeBSD" / ...
        "host_cores":     len(cpu_labels),
    }


async def probe_node(
    client: httpx.AsyncClient,
    url: str,
    timeout: float = 10.0,
) -> dict:
    """Fetch + parse a single node-exporter endpoint.

    On any failure returns ``{"exporter_error": <str>}`` — callers then
    merge this into nodes_info so the frontend can show "stats
    unavailable" next to that node instead of dropping it.
    """
    try:
        r = await client.get(url, timeout=timeout)
    except Exception as e:
        return {"exporter_error": str(e)}
    if r.status_code >= 400:
        return {"exporter_error": f"HTTP {r.status_code}"}
    try:
        stats = parse_exporter_text(r.text)
    except Exception as e:
        return {"exporter_error": f"parse: {e}"}
    stats["exporter_error"] = None
    return stats
