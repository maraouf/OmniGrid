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

from logic.merge import normalize_arch as _normalize_arch

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
    (?:\{(?P<labels>[^}]*)})?                 # optional {k="v",k2="v2"}
    \s+
    (?P<value>[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?|NaN|\+Inf|-Inf)
    (?:\s+\d+)?                               # optional timestamp (ignored)
    \s*$
    """,
    re.VERBOSE,
)

_LABEL_RE = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"(?P<value>(?:[^"\\]|\\.)*)"')

# Network interfaces we exclude from host-total RX/TX counters. These are
# either loopback, Docker's NAT bridges / veth pairs, Calico / Flannel /
# CNI plumbing, or VMware vmnet synthetic adapters — none of them
# represent "real" traffic leaving the host. Prefix match when the glob
# ends with ``*``; exact match otherwise.
_EXCLUDED_NIC_PREFIXES = (
    "docker",  # docker0, docker_gwbridge, ...
    "br-",  # docker-compose user-defined bridges
    "veth",  # docker / k8s veth pairs
    "cali",  # Calico (calixxxx, cali-vxlan, ...)
    "flannel",  # flannel.1, flannel.vxlan
    "cni",  # cni0 / cnixxxx
    "vmnet",  # VMware synthetic (vmnet1 / vmnet8)
)
_EXCLUDED_NIC_EXACT = {"lo"}


def _is_excluded_nic(name: str) -> bool:
    if name in _EXCLUDED_NIC_EXACT:
        return True
    return any(name.startswith(p) for p in _EXCLUDED_NIC_PREFIXES)


def _iter_metric_samples(text: str, prefixes: tuple[str, ...]):
    """Yield ``(metric_name, value, labels)`` tuples for every line that
    starts with one of ``prefixes`` AND has a parseable float value.
    Shared by all counter parsers — they only differ in the prefix set
    and the per-label filtering, which each caller applies."""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not any(line.startswith(p) for p in prefixes):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        labels = _parse_labels(m.group("labels") or "")
        yield m.group("name"), value, labels


def _iter_device_counters(text: str, prefixes: tuple[str, ...]):
    """Yield ``(metric_name, value, device)`` tuples for every line that
    starts with one of ``prefixes`` AND has a parseable float value AND
    a non-empty ``device`` label. Shared by ``parse_network_counters``
    and ``parse_disk_counters`` — they only differ in the prefix set
    and the exclusion check, which the caller applies."""
    for name, value, labels in _iter_metric_samples(text, prefixes):
        device = (labels.get("device") or "").strip()
        if not device:
            continue
        yield name, value, device


def parse_network_counters(text: str) -> dict:
    """Extract ``node_network_{receive,transmit}_bytes_total`` counters.

    Parses the pairs:

        node_network_receive_bytes_total{device="eth0"}  1234567
        node_network_transmit_bytes_total{device="eth0"} 2345678

    Excludes loopback, Docker bridges (``docker*`` / ``br-*`` / ``veth*``),
    Calico / Flannel / CNI plumbing, and VMware synthetic adapters so the
    ``total_rx`` / ``total_tx`` match the "real" interfaces an operator
    thinks of (physical NICs + bonds + VLANs on top of them).

    Returns:
        {"interfaces": [{"name": "eth0", "rx_bytes": int, "tx_bytes": int}],
         "total_rx": int, "total_tx": int}

    Absolute counter bytes — callers (e.g. the host_net_sampler) derive
    rates across consecutive samples. Single-sample callers cannot turn
    these into bytes/s on their own.
    """
    per_iface: dict[str, dict] = {}
    _prefixes = ("node_network_receive_bytes_total",
                 "node_network_transmit_bytes_total")
    for name, value, device in _iter_device_counters(text, _prefixes):
        if _is_excluded_nic(device):
            continue
        entry = per_iface.setdefault(device, {"name": device, "rx_bytes": 0, "tx_bytes": 0})
        if name.endswith("receive_bytes_total"):
            entry["rx_bytes"] = int(value)
        else:
            entry["tx_bytes"] = int(value)

    interfaces = sorted(per_iface.values(), key=lambda r: r["name"])
    total_rx = sum(r["rx_bytes"] for r in interfaces)
    total_tx = sum(r["tx_bytes"] for r in interfaces)
    return {
        "interfaces": interfaces,
        "total_rx": total_rx,
        "total_tx": total_tx,
    }


# Block devices we don't want to count toward host disk I/O. We exclude
# only TRULY synthetic devices: ramdisks, loop mounts, floppy / CD-ROM
# noise, zram. We deliberately KEEP `dm-*` (LVM / encryption) and `md*`
# (mdadm RAID) because on NAS / appliance hosts (Synology, TrueNAS,
# OPNsense) the user-facing volume IS the dm/md device — excluding them
# left those hosts with zero "real" devices and a perpetual 0 disk I/O
# rate. Trade-off: hosts that
# expose BOTH the underlying physical disks AND the dm/md layer on top
# will double-count. That's a known limitation; better to over-report
# than to silently report zero.
_EXCLUDED_DISK_PREFIXES = (
    "loop",  # loop0, loop1, ... (virtual loop mounts)
    "ram",  # ram0, ram1 (kernel ramdisks)
    "fd",  # legacy floppy
    "sr",  # cd-rom (sr0)
    "zram",  # in-memory swap
)

# FreeBSD's `node_devstat_*` family uses different device naming. md*
# is FreeBSD's memory disk (synthetic) — exclude here even though Linux
# md0 (RAID) is now KEPT. pass* is the SCSI passthrough
# device — synthetic, exclude. cd* is cd-rom — exclude. Real FreeBSD
# storage devices come through as ada0 / ada1 (SATA), da0 / da1 (USB /
# SCSI), nvd0 / nvme0 (NVMe), mfid0 (MFI RAID), zfs* — none match.
# Linux md0 isn't reachable via this fallback because the Linux pass
# always wins when `node_disk_*` is present, so md exclusion here only
# applies to FreeBSD output.
_EXCLUDED_DEVSTAT_PREFIXES = (
    "pass",  # SCSI passthrough (pass0, pass1)
    "md",  # FreeBSD memory disk (md98, md99 are stock)
    "cd",  # cd-rom (cd0)
)


def _is_excluded_disk(name: str) -> bool:
    return any(name.startswith(p) for p in _EXCLUDED_DISK_PREFIXES)


def _is_excluded_devstat(name: str) -> bool:
    return any(name.startswith(p) for p in _EXCLUDED_DEVSTAT_PREFIXES)


def parse_disk_counters(text: str) -> dict:
    """Extract per-device read/write byte counters.

    Tries two metric families in order:

    1. Linux ``node_disk_{read,written}_bytes_total`` (the diskstats
       collector — present on every modern Linux node-exporter):

           node_disk_read_bytes_total{device="sda"}    1234567
           node_disk_written_bytes_total{device="sda"} 2345678

    2. FreeBSD ``node_devstat_bytes_total`` (the devstat collector —
       opnsense / pfSense / TrueNAS / FreeBSD;):

           node_devstat_bytes_total{device="ada0",type="read"}  4119181824
           node_devstat_bytes_total{device="ada0",type="write"} 14823682183168

       The FreeBSD pass runs ONLY when the Linux pass produced no
       eligible devices, so a Linux host that legitimately has no
       diskstats (rare — collector disabled) doesn't get accidentally
       fed devstat data from an unrelated source. Different exclusion
       list: ``pass*`` (SCSI passthrough), ``md*`` (FreeBSD memory
       disk — distinct from Linux RAID md0 which the Linux pass
       handles upstream), ``cd*`` (cd-rom).

    Excludes loop / ram / floppy / cd-rom / zram synthetic devices on
    Linux so totals reflect the host's "real" storage activity.
    Per-partition rows (``sda1``, ``sda2``, etc.) are KEPT only as
    parents — node-exporter emits both and summing would double-count,
    so children whose name is parent + digits are dropped.

    Returns:
        {"devices": [{"name": "sda", "read_bytes": int, "written_bytes": int}],
         "total_read": int, "total_written": int}

    Absolute counter bytes — same contract as parse_network_counters.
    Callers (`host_metrics_sampler`) compute rates across consecutive
    samples; a single probe cannot.
    """
    per_dev: dict[str, dict] = {}
    _prefixes = ("node_disk_read_bytes_total",
                 "node_disk_written_bytes_total")
    for name, value, device in _iter_device_counters(text, _prefixes):
        if _is_excluded_disk(device):
            continue
        entry = per_dev.setdefault(device, {"name": device, "read_bytes": 0, "written_bytes": 0})
        if name.endswith("read_bytes_total"):
            entry["read_bytes"] = int(value)
        else:
            entry["written_bytes"] = int(value)

    # Parent/partition de-duplication: drop any device whose name is the
    # prefix of another device + at least one trailing digit (sda → sda1).
    # Keep parent (sda) and exclude partitions (sda1, sda2). Without this
    # totals double-count the same bytes.
    names = sorted(per_dev.keys())
    dropped: set[str] = set()
    for parent in names:
        for child in names:
            if child == parent or child in dropped:
                continue
            # child looks like parent + digits → it's a partition of parent.
            if child.startswith(parent) and child[len(parent):].isdigit():
                dropped.add(child)
    devices = sorted(
        (per_dev[n] for n in per_dev if n not in dropped),
        key=lambda r: r["name"],
    )

    # FreeBSD fallback : Linux pass found nothing → try the
    # `node_devstat_bytes_total{device,type}` family. Same shape out
    # so the caller (`probe_node` → sampler) doesn't care which family
    # produced the bytes.
    if not devices:
        bsd_per_dev: dict[str, dict] = {}
        for _name, value, labels in _iter_metric_samples(
            text, ("node_devstat_bytes_total",),
        ):
            device = (labels.get("device") or "").strip()
            kind = (labels.get("type") or "").strip().lower()
            if not device or _is_excluded_devstat(device):
                continue
            if kind not in ("read", "write"):
                continue
            entry = bsd_per_dev.setdefault(
                device, {"name": device, "read_bytes": 0, "written_bytes": 0},
            )
            if kind == "read":
                entry["read_bytes"] = int(value)
            else:
                entry["written_bytes"] = int(value)
        devices = sorted(bsd_per_dev.values(), key=lambda r: r["name"])

    if not devices:
        # Distinguish "exporter doesn't expose the diskstats collector"
        # (no matching lines at all) from "exporter exposes them but the
        # host happened to do zero I/O" (devices present but with 0
        # values). Returning None totals lets the caller surface NULL
        # downstream so the chart renders as "no data" rather than a
        # flat 0 line. Numeric 0 here would be ambiguous and produce
        # exactly the "always-zero rate" footgun hit on the
        # Synology / RAID hosts before #_EXCLUDED_DISK_PREFIXES was
        # loosened.
        return {"devices": [], "total_read": None, "total_written": None}
    total_read = sum(r["read_bytes"] for r in devices)
    total_written = sum(r["written_bytes"] for r in devices)
    return {
        "devices": devices,
        "total_read": total_read,
        "total_written": total_written,
    }


def _parse_labels(raw: str) -> dict[str, str]:
    """Parse a ``k="v",k2="v2"`` label blob into a dict."""
    out: dict[str, str] = {}
    for m in _LABEL_RE.finditer(raw):
        # Unescape the few characters Prometheus allows backslash-escaped.
        v = m.group("value").replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n')
        out[m.group("key")] = v
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
    uname_nodename = ""
    cpu_labels: set[str] = set()
    # CPU-seconds counters for %CPU derivation. Sum across all
    # CPUs, ALL modes for total; only mode=idle for idle. The sampler
    # delta-maths these against the previous tick to compute %CPU =
    # 100 * (1 - (delta_idle / delta_total)).
    cpu_seconds_total = 0.0
    cpu_seconds_idle = 0.0
    # Load averages — one gauge each, no labels. FreeBSD + Linux both
    # emit ``node_load1`` / ``node_load5`` / ``node_load15`` from the
    # `loadavg` collector; OPNsense ships this by default.
    load_1m = 0.0
    load_5m = 0.0
    load_15m = 0.0
    # DMI / hardware identity — populated from ``node_dmi_info``'s
    # label set (all info is in the labels; the metric value is 1).
    # Not every host / container has DMI; empty strings are fine.
    dmi_vendor = ""
    dmi_product = ""
    dmi_serial = ""
    dmi_bios_version = ""
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
        # Both metric names are observed in the wild:
        # `node_memory_MemTotal_bytes` is the canonical
        # node-exporter v1.x camelCase form (mirrors
        # `node_memory_MemAvailable_bytes`); `node_memtotal_bytes` is
        # the lowercase form some forks / older builds emit. Accept
        # both. Operator-reported on a Linux VM running standard
        # node-exporter where `host_mem_total` was 0 while
        # `host_mem_avail` was correctly populated — caused by the
        # previous lowercase-only check missing the canonical name.
        if name == "node_memtotal_bytes" or name == "node_memory_MemTotal_bytes":
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
            # `nodename` is the kernel-reported hostname (what `uname -n`
            # / `hostname` returns). Distinct from the user's curated
            # `host.id` / `host.label` — typically the actual machine
            # name (e.g. `host01.example.com`) while the curated id might
            # be a role / alias (e.g. `web01`). Surfaced as
            # `host_hostname` so SSH-target resolution + AI grounding
            # can match against either form. Aligns with SNMP +
            # Webmin which already emit `host_hostname`.
            uname_nodename = labels.get("nodename") or uname_nodename
        elif name == "node_load1":
            load_1m = value
        elif name == "node_load5":
            load_5m = value
        elif name == "node_load15":
            load_15m = value
        elif name == "node_dmi_info":
            labels = _parse_labels(m.group("labels") or "")
            # Every vendor labels these slightly differently; accept
            # both the ``bios_*`` and ``system_*`` prefix families.
            dmi_vendor = (
                labels.get("system_vendor")
                or labels.get("board_vendor")
                or labels.get("bios_vendor")
                or dmi_vendor
            )
            dmi_product = (
                labels.get("product_name")
                or labels.get("system_product_name")
                or labels.get("product")
                or dmi_product
            )
            dmi_serial = (
                labels.get("system_serial_number")
                or labels.get("chassis_serial_number")
                or labels.get("serial")
                or dmi_serial
            )
            dmi_bios_version = (
                labels.get("bios_version")
                or labels.get("firmware_version")
                or dmi_bios_version
            )
        elif name == "node_cpu_seconds_total":
            labels = _parse_labels(m.group("labels") or "")
            cpu = labels.get("cpu")
            if cpu:
                cpu_labels.add(cpu)
            # Sum cumulative seconds across all CPUs and ALL modes
            # for the total; only mode=idle for idle. The sampler
            # delta-maths these against the previous tick to derive
            # %CPU = 100 * (1 - (delta_idle / delta_total)).
            try:
                cpu_seconds_total += value
                if labels.get("mode") == "idle":
                    cpu_seconds_idle += value
            except (TypeError, ValueError):
                pass
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
    #
    # ZFS pool dedup: datasets in the same pool report
    # size_i  = used_i + pool_avail
    # avail_i = pool_avail   (identical across siblings)
    # so a naive sum across N datasets inflates the apparent total by
    # (N-1) * pool_avail. Operator on a 14-dataset OPNsense `zroot`
    # pool saw host_disk_total ≈ 8.7 TB instead of the true 817 GB
    # (14 × 807 GB avail + sum_used ≈ 11 TB; with the >0 mountpoint
    # exclusion landing somewhere around 8.7 TB). Fix: count each
    # ZFS pool's `avail` ONCE (pool keyed by the first segment of the
    # ZFS dataset name), then sum only the per-dataset `used`. Per-
    # mount display still shows every dataset's own size/used/avail
    # so /var/log etc. retain their individual numbers.
    mounts: list[dict] = []
    total_size = 0
    total_free = 0
    zfs_pool_avail_seen: dict[str, int] = {}
    gib = 1024 ** 3
    for entry in fs.values():
        _size_raw = entry.get("size", 0)
        _avail_raw = entry.get("avail", 0)
        size = int(_size_raw) if isinstance(_size_raw, (int, float)) else 0
        avail = int(_avail_raw) if isinstance(_avail_raw, (int, float)) else 0
        if size <= 0:
            continue
        used = max(0, size - avail)
        size_gib = size / gib
        used_gib = used / gib
        dp = (used / size * 100) if size > 0 else 0.0
        _fstype_raw = entry.get("fstype")
        fstype = _fstype_raw if isinstance(_fstype_raw, str) else ""
        _mp_raw = entry.get("mountpoint", "")
        mountpoint = _mp_raw if isinstance(_mp_raw, str) else ""
        mounts.append({
            "n": mountpoint,
            "fs": fstype,
            "d": size_gib,
            "du": used_gib,
            "dp": dp,
            # Legacy keys — don't break older consumers.
            "mountpoint": mountpoint,
            "fstype": fstype,
            "size": size,
            "used": used,
        })
        if fstype.lower() == "zfs":
            _device_raw = entry.get("device")
            device = _device_raw if isinstance(_device_raw, str) else ""
            pool = device.split("/", 1)[0] if "/" in device else (device or mountpoint)
            if pool not in zfs_pool_avail_seen:
                # First dataset of this pool — counts the pool's
                # avail + its own used toward the totals.
                zfs_pool_avail_seen[pool] = avail
                total_size += avail + used
                total_free += avail
            else:
                # Subsequent datasets — only their own used, the
                # pool's avail was already counted.
                total_size += used
        else:
            total_size += size
            total_free += avail
    mounts.sort(key=lambda m_entry: m_entry["n"])
    total_used = max(0, total_size - total_free)
    # Normalise machine label → canonical arch name so the UI's
    # "Architecture" row reads the same whether the host runs FreeBSD
    # (``amd64``), Linux (``x86_64``), or ARM (``aarch64`` /
    # ``armv7l``). Routed through ``logic.merge.normalize_arch`` —
    # the same helper Beszel's `_derive_arch` uses — so all providers
    # converge on a single canonical spelling.
    arch = _normalize_arch(uname_machine)
    # Derive uptime from boot_ts — callers (and the frontend) expect
    # ``host_uptime_s`` alongside ``host_boot_ts`` because Beszel emits
    # uptime directly; NE only emits boot time.
    import time as _time
    uptime_s = int(_time.time() - boot_ts) if boot_ts else 0
    return {
        "host_disk_total": total_size,
        "host_disk_used": total_used,
        "host_disk_free": total_free,
        "host_mem_total": mem_total,
        "host_mem_used": max(0, mem_total - mem_avail) if mem_total else 0,
        "host_mem_avail": mem_avail,
        "host_boot_ts": boot_ts or None,
        "host_uptime_s": uptime_s,
        "mounts": mounts,
        # Identity / hardware — all optional. node-exporter runs LAST
        # in the merge so these values are authoritative for Linux /
        # FreeBSD hosts.
        "host_kernel": uname_release,
        "host_arch": arch,
        "host_platform": uname_sysname,  # "Linux" / "FreeBSD" / ...
        # Kernel-reported hostname (uname -n). Surfaced so SSH targets
        # + AI grounding match on the actual machine name, not just
        # the curated id / label. SNMP + Webmin emit the same field.
        "host_hostname": uname_nodename,
        "host_cores": len(cpu_labels),
        # Load averages — gauge copies of /proc/loadavg (Linux) or
        # getloadavg(3) (FreeBSD). Zero-values mean "collector didn't
        # run" (filter in the UI, not here).
        "host_load_1m": load_1m,
        "host_load_5m": load_5m,
        "host_load_15m": load_15m,
        # DMI / hardware identity — surfaces what hypervisor / NUC /
        # OEM box this host actually runs on. Blank values mean "DMI
        # collector disabled" (containers / some VMs) — UI hides the
        # row when the field is empty.
        "host_dmi_vendor": dmi_vendor,
        "host_dmi_product": dmi_product,
        "host_dmi_serial": dmi_serial,
        "host_dmi_bios_version": dmi_bios_version,
        # CPU-seconds counters for sampler %CPU derivation.
        # Zero-values mean "node_cpu_seconds_total absent" — the
        # sampler skips %CPU computation in that case (chart stays
        # empty rather than showing nonsense).
        "host_cpu_seconds_total": cpu_seconds_total,
        "host_cpu_seconds_idle": cpu_seconds_idle,
    }


def _normalise_ne_url(url: str) -> str:
    """Accept a variety of operator-typed URLs and return a canonical
    ``.../metrics`` form.

    Accepted inputs (all resolve to the same canonical URL):
      - ``http://host:9100``
      - ``http://host:9100/``
      - ``http://host:9100/metrics``
      - ``http://host:9100/metrics/``
      - ``host:9100``           (scheme-less; http:// is assumed)
      - ``host``                (scheme-less, port-less; http:// + :9100)

    Rule: strip trailing slashes; default scheme to http; default port
    to 9100 when missing; append ``/metrics`` unless already present.
    """
    from urllib.parse import urlparse, urlunparse
    s = (url or "").strip()
    if not s:
        return s
    # Strip trailing slashes up-front — the most common operator typo.
    s = s.rstrip("/")
    # Ensure we have a scheme so urlparse doesn't dump the host into path.
    if "://" not in s:
        s = "http://" + s
    p = urlparse(s)
    netloc = p.netloc
    # Default port 9100 for bare hostname — the standard node_exporter port.
    if ":" not in netloc and netloc:
        netloc += ":9100"
    path = p.path or ""
    # Strip trailing slash from path (already done for whole URL but be
    # explicit so /metrics/ doesn't become /metrics//metrics on append).
    path = path.rstrip("/")
    if not path.endswith("/metrics"):
        path = (path + "/metrics") if path else "/metrics"
    return urlunparse((p.scheme or "http", netloc, path, "", "", ""))


async def probe_node(
    client: httpx.AsyncClient,
    url: str,
    timeout: float = 10.0,
) -> dict:
    """Fetch + parse a single node-exporter endpoint.

    Forgiving URL handling via :func:`_normalise_ne_url` — accepts bare
    hostnames, URLs without ``/metrics``, trailing slashes, and missing
    scheme. If the normalised URL still returns HTML (operator pointed
    us at a landing page on a different path), log the 'got HTML'
    signal clearly so the Admin → Hosts Test button can surface it.

    On any failure returns ``{"exporter_error": <str>}`` — callers then
    merge this into nodes_info so the frontend can show "stats
    unavailable" next to that node instead of dropping it.
    """

    async def _fetch(u: str) -> tuple[Optional[str], Optional[str]]:
        """(body_text, error_str). One of the pair is always None.

        Error messages are operator-readable: classify the exception
        into one of {DNS failure / TCP-connect timeout / TCP-connect
        refused / generic} so the operator gets a hint targeted at
        the actual failure mode, not a blanket "could be anything"
        line. Empty `str(e)` falls back to the class name + the
        class-name-derived hint (httpx's ConnectTimeout / ReadTimeout
        / ConnectError carry semantically-useful class names even
        when the message body is empty).
        """
        try:
            r = await client.get(u, timeout=timeout)
        except (httpx.HTTPError, OSError) as fetch_err:
            raw = str(fetch_err).strip()
            klass = type(fetch_err).__name__
            lower = raw.lower()
            # DNS-resolution failure — libc / aiohttp / httpx all
            # surface these with one of these substrings. Match
            # ONLY the explicit DNS markers; do NOT treat empty
            # exception strings as DNS (httpx ConnectTimeout often
            # has `str(e) == ''`, but the class name correctly says
            # "ConnectTimeout" — that's a network/firewall issue,
            # NOT DNS).
            is_dns = (
                "name or service not known" in lower
                or "nodename nor servname" in lower
                or "getaddrinfo failed" in lower
                or "temporary failure in name resolution" in lower
                or "could not resolve" in lower
                or "no address associated" in lower
            )
            if is_dns:
                return None, (
                    f"{raw} — DNS resolution failed; the container's "
                    "resolver couldn't find this hostname. Use the FQDN "
                    "(e.g. `host.example.com`) instead of a short name, OR "
                    "add a Docker `--dns` / compose `extra_hosts:` entry "
                    "that covers this name."
                )
            # TCP-connect timeout — the kernel sent SYN and never got
            # SYN-ACK. Either the host isn't on the network, the IP
            # is wrong, or a firewall is silently dropping the
            # packets. Distinct from "Connection refused" (host
            # reachable, port closed).
            if klass == "ConnectTimeout" or "connecttimeout" in lower:
                # Pull concrete host:port from the URL + container
                # short-hostname so the troubleshooting command is
                # copy-paste-ready instead of placeholder-bearing.
                # `socket.gethostname()` inside the container returns
                # the container ID prefix (a valid `docker exec`
                # target); `urlparse` handles missing port (falls back
                # to the URL's scheme default 80/443).
                #
                # Note on tooling: the image is `python:3.14-slim`
                # which deliberately ships NO `nc` / `ping` / `curl` /
                # `telnet` / `ss`. The suggested command uses `python`
                # (always present in the image) to do an equivalent
                # TCP-connect probe. Earlier revisions suggested
                # `nc -vz` which 100% of the time produced
                # `exec: "nc": executable file not found in $PATH`
                # when the user tried to copy-paste it. Same rule for
                # the "connection refused" + "ReadTimeout" branches
                # below — Python first, falling back to a host-side
                # `ss` suggestion if the user SSHes into the target
                # rather than the OmniGrid container.
                from urllib.parse import urlparse as _urlparse
                import socket as _socket
                _u = _urlparse(u)
                _host = _u.hostname or "<host>"
                _port = _u.port or ({"http": 80, "https": 443}.get(_u.scheme, ""))
                try:
                    _container = _socket.gethostname() or "<omnigrid-container>"
                except OSError:
                    _container = "<omnigrid-container>"
                return None, (
                    f"{raw or klass} — TCP connect timeout to {u}; "
                    "the kernel got no SYN-ACK back. Check: (a) is the "
                    "host actually on the network? (b) is the port "
                    "correct? (c) is a firewall dropping packets from "
                    "the OmniGrid container's network to this host? "
                    f"Run `docker exec {_container} python -c \""
                    f"import socket; s=socket.socket(); s.settimeout(3); "
                    f"s.connect(('{_host}', {_port or 0})); print('OK')\"` "
                    "to confirm (ConnectionRefusedError means port closed, "
                    "TimeoutError means firewall/unreachable)."
                )
            # TCP-connect refused — kernel returned RST.  Host is
            # reachable but nothing is listening on the port.
            if "connection refused" in lower or "econnrefused" in lower:
                from urllib.parse import urlparse as _urlparse
                _u_cr = _urlparse(u)
                _port_cr = _u_cr.port or ({"http": 80, "https": 443}.get(_u_cr.scheme, ""))
                return None, (
                    f"{raw} — the host is reachable on the network "
                    "but NOTHING is listening on this port. The service "
                    "(node_exporter) is probably stopped or running on "
                    f"a different port. SSH to the host and run "
                    f"`ss -tlnp | grep {_port_cr}` (or "
                    f"`netstat -tlnp | grep {_port_cr}` on older boxes) "
                    "to confirm."
                )
            # Read timeout — TCP connect succeeded but the server
            # didn't send the full response within `timeout`.
            if klass == "ReadTimeout" or "readtimeout" in lower:
                return None, (
                    f"{raw or klass} — TCP connect succeeded but the "
                    "server didn't respond within the probe timeout. "
                    "node_exporter is slow / overloaded, OR the URL "
                    "points at a different service that doesn't reply."
                )
            return None, raw or klass
        if r.status_code >= 400:
            return None, f"HTTP {r.status_code}"
        return r.text, None

    canonical = _normalise_ne_url(url)

    text, err = await _fetch(canonical)
    # If /metrics returned an error, try the raw user-supplied URL as a
    # fallback — maybe the exporter lives at a non-standard path.
    if err and canonical != url and url:
        text, err2 = await _fetch(url)
        if err2:
            return {"exporter_error":
                        f"{canonical} → {err}; fallback {url} → {err2}"}
    if err and text is None:
        return {"exporter_error": err}
    if text is None:
        return {"exporter_error": err or "no response body"}

    # Detect HTML landing pages. Exporters expose metrics ONLY as
    # plain text; any HTML response means we hit the wrong endpoint.
    lead = text.lstrip().lower()
    if lead.startswith("<!doctype") or lead.startswith("<html"):
        return {"exporter_error":
                    f"endpoint returned HTML, not Prometheus text — "
                    f"tried {canonical}; check the URL resolves to "
                    f"node_exporter's /metrics output"}

    try:
        stats = parse_exporter_text(text)
    except Exception as e:
        return {"exporter_error": f"parse: {e}"}
    # Separate pass for network counters. Two fields are added to the
    # returned dict — both ABSOLUTE counter values, not rates. The
    # host_net_sampler computes rates across consecutive samples; a
    # single probe cannot. Per-interface detail lives in ``ne_net_ifaces``
    # for the debug endpoint. Kept separate from ``network_ifaces`` (the
    # Beszel/Pulse NIC-list shape) because the counter payload is a
    # different shape and the merge pipeline MUST NOT pass these
    # bytes-counters through ``_merge_best`` as NIC lists.
    try:
        net = parse_network_counters(text)
        stats["host_net_rx_total"] = int(net.get("total_rx") or 0)
        stats["host_net_tx_total"] = int(net.get("total_tx") or 0)
        stats["ne_net_ifaces"] = net.get("interfaces") or []
    except Exception as e:
        # Non-fatal — a malformed exporter line shouldn't blank the rest
        # of the dict. Log once so operators can find it in Admin → Logs.
        print(f"[node_exporter] network counter parse failed: {e}")
    # Disk counter pass — same contract as the network pass: ABSOLUTE
    # counter bytes; the host_metrics_sampler computes rates across two
    # ticks. Single-probe callers cannot turn these into bytes/s.
    # Only set host_disk_*_total when the parser ACTUALLY found
    # eligible devices — None totals (no diskstats collector / all
    # devices excluded) leave the keys absent so the sampler treats
    # the metric as missing (NULL row) instead of a flat-zero rate.
    try:
        disk = parse_disk_counters(text)
        if disk.get("total_read") is not None:
            stats["host_disk_read_total"] = int(disk["total_read"])
        if disk.get("total_written") is not None:
            stats["host_disk_write_total"] = int(disk["total_written"])
        stats["ne_disk_devices"] = disk.get("devices") or []
    except Exception as e:
        print(f"[node_exporter] disk counter parse failed: {e}")
    stats["exporter_error"] = None
    return stats


# Public alias for cross-module use (main.py's debug endpoint canonicalises
# the operator-supplied URL the same way `probe_node` does).
normalise_ne_url = _normalise_ne_url
