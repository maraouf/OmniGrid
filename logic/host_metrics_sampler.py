"""Per-host historical CPU / Memory / Disk / Network metrics sampler.

Sibling of :mod:`logic.host_net_sampler` — same architectural shape, same
skip-don't-synthesize discipline (see CLAUDE.md "Counter-rate samplers
must SKIP, not synthesize"), but covers the broader gauge metrics
(cpu_percent, mem_used/total, disk_used/total) plus net rx/tx rates so
node-exporter-only hosts (no Beszel agent) get a usable historical
charts surface in the host drawer.

Why a separate module from :mod:`logic.host_net_sampler`:
  - That sampler writes ONE metric pair (rx_bytes_per_s / tx_bytes_per_s)
    derived from monotonic counters; this one writes a denser row of
    point-in-time gauges PLUS the same net rates. Different schema, same
    cadence, same lifespan-task contract. Two siblings is fine; if a
    third lands, refactor to a shared base.
  - The net sampler exists primarily as a Beszel FALLBACK (patches an
    existing Beszel-derived series when the agent is misconfigured);
    this sampler exists as a Beszel SUBSTITUTE for hosts that never had
    a Beszel agent at all. Different consumer, different contract.

Counter rates (net_rx_bps / net_tx_bps) follow the same sanity bounds
host_net_sampler uses: ``60 ≤ Δs ≤ 900`` AND ``0 ≤ Δbytes ≤ 10 GB``. An
out-of-bounds delta SKIPS the row entirely rather than INSERT a 0 — a
stored zero would mask the very signal the chart should surface (host
reboot / counter wrap / clock skew). Gauges (CPU%, mem, disk) don't need
delta bounds because they're point-in-time, but they DO honour an
``is_meaningful`` check so a missing metric doesn't poison the series.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

import httpx

from logic import node_exporter as _ne
from logic import tuning
from logic.db import db_conn, get_setting


# Sanity bounds — same values, same rationale as host_net_sampler.
_MIN_DELTA_SECONDS = 60
_MAX_DELTA_SECONDS = 900
_MIN_DELTA_BYTES   = 0
_MAX_DELTA_BYTES   = 10 * 1024 * 1024 * 1024  # 10 GB


# Per-host previous absolute counters for delta math (net rx / net tx /
# disk read / disk written). Lives across ticks within one sampler-task
# lifetime; cleared on lifespan cancel/restart so the post-restart first
# delta is correctly SKIPPED. The disk pair was added in #339 — pre-#339
# entries had a 3-tuple shape; the in-memory cache is restart-only so no
# migration needed (a single tick after restart re-establishes baseline).
_last_counters: dict[str, tuple[float, int, int, int, int]] = {}  # host_id → (ts, rx, tx, dr_bytes, dw_bytes)


# Concurrency cap on parallel NE probes per tick. Matches the convention
# elsewhere (REGISTRY_CONCURRENCY=8, STATS_CONCURRENCY=16) — host probes
# are heavier than registry HEADs but lighter than container stats fans.
_PROBE_CONCURRENCY = 8


# Active-providers parser + curated-hosts walker live in logic/db.py —
# single source of truth shared with main.py / gather.py / both
# samplers (CONS-001 + CONS-004).
from logic.db import (
    active_host_stats_providers as _active_providers,
    curated_ne_hosts as _load_curated_hosts,
)


# Canonical strict-positive helper lives in logic/merge.py — alias it
# locally so existing call sites stay readable. Kept as a thin alias
# rather than a re-import-everywhere refactor; the behaviour contract
# is identical.
from logic.merge import is_positive_number as _is_meaningful_number


def _delta_seconds_ok(delta_seconds: float) -> bool:
    return _MIN_DELTA_SECONDS <= delta_seconds <= _MAX_DELTA_SECONDS


def _delta_bytes_ok(delta_bytes: int) -> bool:
    return _MIN_DELTA_BYTES <= delta_bytes <= _MAX_DELTA_BYTES


def _sanity_ok(delta_seconds: float, delta_rx: int, delta_tx: int) -> bool:
    """Net-pair sanity check (kept for the existing call sites). Returns
    True only when BOTH rx AND tx deltas are in bounds — a single
    out-of-bounds field skips the whole pair so the rates stay paired."""
    if not _delta_seconds_ok(delta_seconds):
        return False
    return _delta_bytes_ok(delta_rx) and _delta_bytes_ok(delta_tx)


def _compute_row(
    host_id: str,
    now: float,
    stats: dict,
    prev: Optional[tuple],
) -> tuple[Optional[dict], Optional[tuple[float, int, int, int, int]]]:
    """Pure-function core: turn a probe payload + previous counters into
    an INSERT-shaped row plus the next-tick counter cache.

    Returns ``(row_or_none, next_counter_or_none)``. The row is None when
    every gauge AND every rate are unmeaningful — there's nothing worth
    persisting. The next_counter is None when the current probe didn't
    return rx/tx counters at all (don't poison the cache with a zeroed
    entry — next tick should still treat itself as "first"). When net
    counters ARE present but disk counters AREN'T (older NE without the
    diskstats collector), the cache stores zeros for the missing pair —
    the disk-rate path is gated separately so a missing field stays NULL.

    Skip rules:
      - Net rate pair: SKIP (NULL both) when delta seconds out of bounds
        OR when either of the byte deltas is out of bounds OR no previous
        sample.
      - Disk rate pair: SAME, but evaluated independently of net (a host
        with stable net but rebooted disk counters keeps its net rates).
      - Gauges: SKIP an individual field (store NULL) when not meaningful.
        Whole row is dropped only when every field would be NULL.

    Backwards compatibility: ``prev`` may be the legacy 3-tuple
    ``(ts, rx, tx)`` from a process that started before #339 shipped.
    In-memory cache is wiped on restart so this only matters mid-process
    if a partial reload happened — handled by len()-checking ``prev``.
    """
    # Gauges — pull what node-exporter parsed; treat 0 / None as missing.
    mem_total = int(stats.get("host_mem_total") or 0)
    mem_used  = int(stats.get("host_mem_used") or 0)
    disk_total = int(stats.get("host_disk_total") or 0)
    disk_used  = int(stats.get("host_disk_used") or 0)

    cpu_percent: Optional[float] = None
    # node-exporter doesn't surface a single host_cpu_percent because the
    # raw counter is per-cpu seconds. We don't have load-average-derived
    # %CPU here either — leaving it None is correct; future work can plug
    # in a 1m derivation from node_cpu_seconds_total counters across two
    # ticks (own delta math, separate from net counters).
    raw_cpu = stats.get("host_cpu_percent")
    if _is_meaningful_number(raw_cpu):
        cpu_percent = float(raw_cpu)

    # Net counters — required to advance the cache.
    rx_total = stats.get("host_net_rx_total")
    tx_total = stats.get("host_net_tx_total")
    have_net_counters = (rx_total is not None) and (tx_total is not None)

    # Disk counters — independent of net; some exporters disable
    # diskstats, in which case rates stay NULL but net keeps working.
    dr_total = stats.get("host_disk_read_total")
    dw_total = stats.get("host_disk_write_total")
    have_disk_counters = (dr_total is not None) and (dw_total is not None)

    rx_rate: Optional[float] = None
    tx_rate: Optional[float] = None
    dr_rate: Optional[float] = None
    dw_rate: Optional[float] = None
    next_counter: Optional[tuple[float, int, int, int, int]] = None

    rx = tx = dr = dw = 0
    if have_net_counters:
        try:
            rx = int(rx_total)
            tx = int(tx_total)
        except (TypeError, ValueError):
            have_net_counters = False
    if have_disk_counters:
        try:
            dr = int(dr_total)
            dw = int(dw_total)
        except (TypeError, ValueError):
            have_disk_counters = False

    # Cache the current counters (zeros where unavailable) so the next
    # tick has a baseline. A rejected delta still advances the cache so
    # we don't keep diffing against a stale anchor forever.
    if have_net_counters or have_disk_counters:
        next_counter = (now, rx, tx, dr, dw)

    # Decompose `prev` tolerantly — pre-#339 entries are 3-tuples; new
    # ones are 5-tuples. Falling back to 0 for missing disk counters
    # means the first post-#339 tick treats disk as "first sample" and
    # skips the rate (correct).
    prev_ts = prev_rx = prev_tx = prev_dr = prev_dw = None
    if prev is not None:
        if len(prev) >= 3:
            prev_ts, prev_rx, prev_tx = prev[0], prev[1], prev[2]
        if len(prev) >= 5:
            prev_dr, prev_dw = prev[3], prev[4]

    if prev_ts is not None:
        delta_s = now - prev_ts
        if _delta_seconds_ok(delta_s):
            # Net rate pair — both fields must be in bounds.
            if have_net_counters and prev_rx is not None and prev_tx is not None:
                d_rx = rx - prev_rx
                d_tx = tx - prev_tx
                if _delta_bytes_ok(d_rx) and _delta_bytes_ok(d_tx):
                    rx_rate = d_rx / delta_s
                    tx_rate = d_tx / delta_s
            # Disk rate pair — evaluated INDEPENDENTLY of net so a
            # host that just rebooted its disk subsystem (e.g. zfs
            # remount) doesn't lose its net rates.
            if have_disk_counters and prev_dr is not None and prev_dw is not None:
                d_dr = dr - prev_dr
                d_dw = dw - prev_dw
                if _delta_bytes_ok(d_dr) and _delta_bytes_ok(d_dw):
                    dr_rate = d_dr / delta_s
                    dw_rate = d_dw / delta_s

    # If literally nothing meaningful — drop the row.
    nothing_to_write = (
        cpu_percent is None
        and not _is_meaningful_number(mem_used)
        and not _is_meaningful_number(mem_total)
        and not _is_meaningful_number(disk_used)
        and not _is_meaningful_number(disk_total)
        and rx_rate is None
        and tx_rate is None
        and dr_rate is None
        and dw_rate is None
    )
    if nothing_to_write:
        return None, next_counter

    row = {
        "ts": int(now),
        "host_id": host_id,
        "cpu_percent": cpu_percent,
        "mem_used":  mem_used  if _is_meaningful_number(mem_used)  else None,
        "mem_total": mem_total if _is_meaningful_number(mem_total) else None,
        "disk_used":  disk_used  if _is_meaningful_number(disk_used)  else None,
        "disk_total": disk_total if _is_meaningful_number(disk_total) else None,
        "net_rx_bps": rx_rate,
        "net_tx_bps": tx_rate,
        "disk_read_bps":  dr_rate,
        "disk_write_bps": dw_rate,
    }
    return row, next_counter


async def _probe_one(
    client: httpx.AsyncClient,
    host: dict,
    sem: asyncio.Semaphore,
) -> None:
    """Probe NE for one host; insert a row if there's anything worth
    storing. Per-host failures isolated — one dead exporter doesn't
    cascade to the rest of the fleet.
    """
    async with sem:
        hid = host["id"]
        ne_url = host["ne_url"]
        now = time.time()
        try:
            stats = await _ne.probe_node(client, ne_url, timeout=10.0)
        except Exception as e:
            print(f"[host_metrics_sampler] {hid!r} probe error: {e}")
            return
        if stats.get("exporter_error"):
            print(f"[host_metrics_sampler] {hid!r} exporter_error: {stats['exporter_error']}")
            return

        prev = _last_counters.get(hid)
        row, next_counter = _compute_row(hid, now, stats, prev)
        if next_counter is not None:
            _last_counters[hid] = next_counter
        if row is None:
            if prev is None and next_counter is not None:
                print(f"[host_metrics_sampler] {hid!r} first sample established "
                      "counter baseline; no row to write yet")
            else:
                print(f"[host_metrics_sampler] {hid!r} probe returned no meaningful "
                      "metrics; skipping INSERT")
            return

        try:
            with db_conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO host_metrics_samples "
                    "(ts, host_id, cpu_percent, mem_used, mem_total, "
                    "disk_used, disk_total, net_rx_bps, net_tx_bps, "
                    "disk_read_bps, disk_write_bps) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row["ts"], row["host_id"], row["cpu_percent"],
                        row["mem_used"], row["mem_total"],
                        row["disk_used"], row["disk_total"],
                        row["net_rx_bps"], row["net_tx_bps"],
                        row["disk_read_bps"], row["disk_write_bps"],
                    ),
                )
        except Exception as e:
            print(f"[host_metrics_sampler] {hid!r} DB insert failed: {e}")
            return
        net_blurb = (
            f"net rx={row['net_rx_bps']:.0f} tx={row['net_tx_bps']:.0f} B/s"
            if (row["net_rx_bps"] is not None and row["net_tx_bps"] is not None)
            else "net=skip"
        )
        disk_blurb = (
            f"diskio r={row['disk_read_bps']:.0f} w={row['disk_write_bps']:.0f} B/s"
            if (row["disk_read_bps"] is not None and row["disk_write_bps"] is not None)
            else "diskio=skip"
        )
        print(f"[host_metrics_sampler] {hid!r} wrote cpu={row['cpu_percent']} "
              f"mem={row['mem_used']}/{row['mem_total']} "
              f"disk={row['disk_used']}/{row['disk_total']} {net_blurb} {disk_blurb}")


def _prune_old_samples() -> int:
    days = tuning.tuning_int("tuning_stats_history_days")
    cutoff = int(time.time() - days * 86400)
    try:
        with db_conn() as c:
            cur = c.execute("DELETE FROM host_metrics_samples WHERE ts < ?", (cutoff,))
            return cur.rowcount or 0
    except Exception as e:
        print(f"[host_metrics_sampler] prune failed: {e}")
        return 0


async def host_metrics_sampler_loop() -> None:
    """Lifespan-managed sampler. One tick per
    ``tuning_stats_sample_interval_seconds`` (DB > env > default)."""
    _last_counters.clear()
    # Wait a beat so DB tables exist + hosts_config is loaded before the
    # first probe. Same pattern as host_net_sampler / stats_sampler.
    interval = tuning.tuning_int("tuning_stats_sample_interval_seconds")
    await asyncio.sleep(min(60, interval))
    tick = 0
    while True:
        try:
            active = _active_providers()
            if "node_exporter" not in active:
                pass  # dormant — keep ticking so toggle takes effect live
            else:
                hosts = _load_curated_hosts()
                if hosts:
                    sem = asyncio.Semaphore(_PROBE_CONCURRENCY)
                    async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
                        await asyncio.gather(
                            *(_probe_one(client, h, sem) for h in hosts),
                            return_exceptions=True,
                        )
            interval = tuning.tuning_int("tuning_stats_sample_interval_seconds")
            days = tuning.tuning_int("tuning_stats_history_days")
            if tick % max(1, 3600 // interval) == 0:
                n = _prune_old_samples()
                if n:
                    print(f"[host_metrics_sampler] pruned {n} rows older than "
                          f"{days}d")
        except Exception as e:
            print(f"[host_metrics_sampler] tick error: {e}")
        tick += 1
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def _shape_row(r) -> dict:
    """Turn one DB row (sqlite3.Row) into the dict shape both readers
    return. Centralised here so adding a new column is a one-line edit."""
    return {
        "ts": int(r["ts"]),
        "cpu_percent": (float(r["cpu_percent"]) if r["cpu_percent"] is not None else None),
        "mem_used":   (int(r["mem_used"])   if r["mem_used"]   is not None else None),
        "mem_total":  (int(r["mem_total"])  if r["mem_total"]  is not None else None),
        "disk_used":  (int(r["disk_used"])  if r["disk_used"]  is not None else None),
        "disk_total": (int(r["disk_total"]) if r["disk_total"] is not None else None),
        "net_rx_bps": (float(r["net_rx_bps"]) if r["net_rx_bps"] is not None else None),
        "net_tx_bps": (float(r["net_tx_bps"]) if r["net_tx_bps"] is not None else None),
        "disk_read_bps":  (float(r["disk_read_bps"])  if r["disk_read_bps"]  is not None else None),
        "disk_write_bps": (float(r["disk_write_bps"]) if r["disk_write_bps"] is not None else None),
    }


_SAMPLES_COLS = (
    "ts, cpu_percent, mem_used, mem_total, "
    "disk_used, disk_total, net_rx_bps, net_tx_bps, "
    "disk_read_bps, disk_write_bps"
)


def recent_samples(host_id: str, since_ts: int, limit: int = 500) -> list[dict]:
    """Return rows for one host back to ``since_ts`` (epoch s), oldest-first."""
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                f"SELECT {_SAMPLES_COLS} "
                "FROM host_metrics_samples "
                "WHERE host_id=? AND ts >= ? "
                "ORDER BY ts ASC LIMIT ?",
                (host_id, int(since_ts), int(limit)),
            ).fetchall()
    except Exception as e:
        print(f"[host_metrics_sampler] recent_samples({host_id!r}) failed: {e}")
        return []
    return [_shape_row(r) for r in rows]


def last_samples(host_id: str, limit: int = 5) -> list[dict]:
    """Newest-first recent rows for the debug endpoint."""
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                f"SELECT {_SAMPLES_COLS} "
                "FROM host_metrics_samples WHERE host_id=? "
                "ORDER BY ts DESC LIMIT ?",
                (host_id, int(limit)),
            ).fetchall()
    except Exception as e:
        print(f"[host_metrics_sampler] last_samples({host_id!r}) failed: {e}")
        return []
    return [_shape_row(r) for r in rows]


def series_collectors_present(host_id: str, hours: int) -> dict:
    """Did the sampler EVER record a non-null disk-I/O / net rate for
    this host in the given window?

    Used by the host drawer's empty-state copy to distinguish:
      - "No activity in this window" — collector IS reporting, just idle.
      - "Exporter doesn't expose `node_disk_*`" — collector is permanently
        missing on this host's node-exporter (operator needs to enable
        ``--collector.diskstats`` / ``--collector.netstats``).

    ``history_series`` masks NULL → 0 to keep the chart math simple, so
    the SPA can't tell the two cases apart from the series alone. This
    helper walks the raw rows and reports per-metric whether any
    non-null value exists. The frontend gates collector-missing copy on
    the result. False here means "every sample had NULL for that key" —
    a strong signal the collector isn't enabled (the alternative
    explanation is "two-tick window too short to compute any rate",
    but two ticks at 60–300s cadence is enough to rule that out).

    Filesystem and memory keys (`mem_total`, `disk_total`) round out
    the diagnostic: an exporter with ONLY the meminfo + filesystem
    collectors enabled produces a row where mem/disk gauges are set
    but rate fields are NULL — that's the canonical "stripped exporter"
    signal we want to surface.
    """
    hours = max(1, min(168, int(hours or 1)))
    since = int(time.time() - hours * 3600)
    raw = recent_samples(host_id, since, limit=hours * 60)
    has = {"disk_io": False, "net": False, "fs": False, "mem": False, "cpu": False}
    for r in raw:
        if r.get("disk_read_bps") is not None or r.get("disk_write_bps") is not None:
            has["disk_io"] = True
        if r.get("net_rx_bps") is not None or r.get("net_tx_bps") is not None:
            has["net"] = True
        if r.get("disk_total"):
            has["fs"] = True
        if r.get("mem_total"):
            has["mem"] = True
        if r.get("cpu_percent") is not None:
            has["cpu"] = True
    return has


def history_series(host_id: str, hours: int) -> list[dict]:
    """Read rows from the table and shape them as a Beszel-compatible series.

    Returns a list of points whose keys mirror what
    :func:`logic.beszel.fetch_system_history` emits, so the frontend's
    chart helpers (`hostChart`, `hostMetricStats`, `hostChartMax`) work
    against this path with no branching. Fields NE doesn't have are
    returned as 0; the chart-gates ``hostMetricStats(...).maxRaw > 0``
    on the SPA side hide those panels cleanly.
    """
    hours = max(1, min(168, int(hours or 1)))
    since = int(time.time() - hours * 3600)
    raw = recent_samples(host_id, since, limit=hours * 60)
    gib = 1024 ** 3
    series: list[dict] = []
    for r in raw:
        mem_total = r.get("mem_total") or 0
        mem_used  = r.get("mem_used")  or 0
        disk_total = r.get("disk_total") or 0
        disk_used  = r.get("disk_used")  or 0
        nr = r.get("net_rx_bps") or 0.0
        ns = r.get("net_tx_bps") or 0.0
        net = nr + ns
        # Disk I/O rates added in #339 — backfilled to 0 for rows
        # written before the column existed, so old history points
        # render flat until the new sampler ticks land.
        dr = r.get("disk_read_bps")  or 0.0
        dw = r.get("disk_write_bps") or 0.0
        series.append({
            "t":   r["ts"],
            "cpu": r.get("cpu_percent") or 0.0,
            "mp":  (100.0 * mem_used / mem_total) if mem_total else 0.0,
            "dp":  (100.0 * disk_used / disk_total) if disk_total else 0.0,
            "mu":  (mem_used  / gib) if mem_used  else 0.0,
            "du":  (disk_used / gib) if disk_used else 0.0,
            "b":   net,
            "nr":  nr,
            "ns":  ns,
            "net": net,
            "dr":  dr,
            "dw":  dw,
            # Swap + load avg still not surfaced by the NE sampler.
            # Future work could fold them in (gauges for `node_load1` /
            # `node_memory_Swap*`). Returning zeros keeps the frontend
            # gates honest — those cards stay hidden until real data
            # lands here.
            "la1": 0.0,
            "la5": 0.0,
            "la15": 0.0,
            "s":   0.0,
            "su":  0.0,
        })
    return series


# ---------------------------------------------------------------------------
# Smoke test — run this module directly to exercise _compute_row end-to-end
# against a hand-rolled minimal node-exporter response. Not a pytest fixture
# (project has no test runner); invocation is ``python -m logic.host_metrics_sampler``
# or ``python logic/host_metrics_sampler.py``. Exits 0 on pass.
# ---------------------------------------------------------------------------
def _smoke_test() -> int:
    fixture = """\
# minimal node-exporter response covering the fields _compute_row reads
node_memtotal_bytes 8589934592
node_memory_MemAvailable_bytes 4294967296
node_filesystem_size_bytes{mountpoint="/",fstype="ext4",device="/dev/sda1"} 107374182400
node_filesystem_avail_bytes{mountpoint="/",fstype="ext4",device="/dev/sda1"} 53687091200
node_network_receive_bytes_total{device="eth0"} 1000000
node_network_transmit_bytes_total{device="eth0"} 500000
node_disk_read_bytes_total{device="sda"} 4000000
node_disk_written_bytes_total{device="sda"} 2000000
node_disk_read_bytes_total{device="sda1"} 4000000
node_disk_written_bytes_total{device="sda1"} 2000000
node_disk_read_bytes_total{device="loop0"} 99999999
node_disk_written_bytes_total{device="loop0"} 99999999
node_cpu_seconds_total{cpu="0",mode="idle"} 1234.5
node_cpu_seconds_total{cpu="1",mode="idle"} 2345.6
node_uname_info{sysname="Linux",release="5.15.0",machine="x86_64"} 1
node_boot_time_seconds 1700000000
"""
    parsed = _ne.parse_exporter_text(fixture)
    net = _ne.parse_network_counters(fixture)
    parsed["host_net_rx_total"] = net["total_rx"]
    parsed["host_net_tx_total"] = net["total_tx"]
    disk = _ne.parse_disk_counters(fixture)
    parsed["host_disk_read_total"]  = disk["total_read"]
    parsed["host_disk_write_total"] = disk["total_written"]

    # Disk parser sanity — sda1 is a partition of sda → MUST be excluded
    # from the totals (else we double-count). loop0 is excluded as a
    # synthetic device. Only sda's 4 MB / 2 MB should land in totals.
    assert disk["total_read"]    == 4000000, f"disk total_read={disk['total_read']}"
    assert disk["total_written"] == 2000000, f"disk total_written={disk['total_written']}"
    dev_names = [d["name"] for d in disk["devices"]]
    assert dev_names == ["sda"], f"expected only sda, got {dev_names}"

    # dm-* / md* are NO LONGER excluded after #343 (Synology / NAS use
    # them as the user-facing volumes). Verify they're counted now.
    nas_fixture = """\
node_disk_read_bytes_total{device="dm-0"} 1000000
node_disk_written_bytes_total{device="dm-0"} 500000
node_disk_read_bytes_total{device="md0"}  2000000
node_disk_written_bytes_total{device="md0"} 1000000
node_disk_read_bytes_total{device="loop0"} 99999999
node_disk_written_bytes_total{device="loop0"} 99999999
"""
    nas_disk = _ne.parse_disk_counters(nas_fixture)
    assert nas_disk["total_read"]    == 3000000, f"NAS dm+md should be counted: {nas_disk}"
    assert nas_disk["total_written"] == 1500000
    nas_names = [d["name"] for d in nas_disk["devices"]]
    assert "dm-0" in nas_names and "md0" in nas_names and "loop0" not in nas_names

    # No-devices case (#343 follow-up): exporter without the diskstats
    # collector returns no node_disk_* lines → parser returns None
    # totals → probe_node leaves the host_disk_*_total keys absent →
    # sampler stores NULL rate (not 0). Critical: distinguishing
    # "missing data" from "zero activity" so the chart correctly
    # displays "no data" instead of a flat zero line.
    no_disk_fixture = """\
node_memtotal_bytes 8589934592
node_filesystem_size_bytes{mountpoint="/",device="/dev/sda1"} 100
node_filesystem_avail_bytes{mountpoint="/",device="/dev/sda1"} 50
node_network_receive_bytes_total{device="eth0"} 1000
node_network_transmit_bytes_total{device="eth0"} 500
"""
    no_disk_parsed = _ne.parse_disk_counters(no_disk_fixture)
    assert no_disk_parsed["total_read"]    is None, "no-devices must return None"
    assert no_disk_parsed["total_written"] is None

    # FreeBSD fallback (#352): hosts running the FreeBSD node-exporter
    # port emit `node_devstat_bytes_total{device,type}` instead of
    # `node_disk_*`. Real opnsense scrape from 10.0.0.1: ada0 = 4.12 GB
    # read / 14.82 TB write totals, plus a synthetic md98 (memdisk) and
    # pass0 (SCSI passthrough) that MUST be excluded. Verifies the
    # parser falls through to the FreeBSD branch when the Linux family
    # returns no devices, AND that the correct synthetic-device
    # exclusion list is applied (pass*/md*/cd*).
    bsd_fixture = """\
node_devstat_bytes_total{device="ada0",type="read"} 4119181824
node_devstat_bytes_total{device="ada0",type="write"} 14823682183168
node_devstat_bytes_total{device="md98",type="read"} 76800
node_devstat_bytes_total{device="md98",type="write"} 0
node_devstat_bytes_total{device="pass0",type="read"} 0
node_devstat_bytes_total{device="pass0",type="write"} 0
"""
    bsd_disk = _ne.parse_disk_counters(bsd_fixture)
    assert bsd_disk["total_read"]    == 4119181824, f"BSD read mismatch: {bsd_disk}"
    assert bsd_disk["total_written"] == 14823682183168, f"BSD write mismatch: {bsd_disk}"
    bsd_names = [d["name"] for d in bsd_disk["devices"]]
    assert bsd_names == ["ada0"], f"only ada0 should pass BSD exclusion, got {bsd_names}"

    # FreeBSD-fallback hand-off into the sampler: rate calc must work
    # the same way regardless of which metric family produced the
    # totals — `probe_node` writes `host_disk_*_total` keys identically
    # for both, so the sampler's `_compute_row` is family-agnostic. We
    # exercise that here with synthetic before/after BSD totals.
    bsd_t0 = 1700000000.0
    bsd_stats_before = _ne.parse_exporter_text(bsd_fixture)
    bsd_stats_before["host_net_rx_total"] = 0
    bsd_stats_before["host_net_tx_total"] = 0
    bsd_stats_before["host_disk_read_total"]  = bsd_disk["total_read"]
    bsd_stats_before["host_disk_write_total"] = bsd_disk["total_written"]
    bsd_baseline_row, bsd_prev = _compute_row(
        "bsd_host", bsd_t0, bsd_stats_before, None,
    )
    assert bsd_prev[3] == 4119181824 and bsd_prev[4] == 14823682183168, bsd_prev
    bsd_stats_after = dict(bsd_stats_before)
    bsd_stats_after["host_disk_read_total"]  += 6 * 1024 * 1024
    bsd_stats_after["host_disk_write_total"] += 3 * 1024 * 1024
    bsd_row, _ = _compute_row("bsd_host", bsd_t0 + 300, bsd_stats_after, bsd_prev)
    assert bsd_row is not None
    assert abs(bsd_row["disk_read_bps"]  - (6 * 1024 * 1024) / 300) < 0.001, bsd_row
    assert abs(bsd_row["disk_write_bps"] - (3 * 1024 * 1024) / 300) < 0.001, bsd_row

    # Linux pass takes precedence: a host that emits BOTH families
    # (rare — would have to be a hand-written exporter) must NOT
    # double-count. The FreeBSD branch only runs when the Linux pass
    # produces zero devices.
    mixed_fixture = """\
node_disk_read_bytes_total{device="sda"} 1000
node_disk_written_bytes_total{device="sda"} 500
node_devstat_bytes_total{device="ada0",type="read"}  9999999
node_devstat_bytes_total{device="ada0",type="write"} 9999999
"""
    mixed_disk = _ne.parse_disk_counters(mixed_fixture)
    assert mixed_disk["total_read"]    == 1000, f"Linux pass must win: {mixed_disk}"
    assert mixed_disk["total_written"] == 500
    mixed_names = [d["name"] for d in mixed_disk["devices"]]
    assert mixed_names == ["sda"], f"BSD branch must not run: {mixed_names}"

    # Tick 1 — no previous counter, should establish baseline. Gauges
    # are meaningful so a row IS produced; rates simply absent.
    t0 = 1700000000.0
    row1, next1 = _compute_row("h1", t0, parsed, None)
    assert next1 == (t0, 1000000, 500000, 4000000, 2000000), f"baseline mismatch: {next1}"
    assert row1 is not None and row1["net_rx_bps"] is None and row1["net_tx_bps"] is None
    assert row1["disk_read_bps"] is None and row1["disk_write_bps"] is None
    assert row1["mem_total"] == 8589934592
    assert row1["mem_used"] == 8589934592 - 4294967296
    assert row1["disk_total"] == 107374182400
    assert row1["disk_used"] == 107374182400 - 53687091200

    # Tick 2 — net counters bumped by 5 MB rx / 1 MB tx, disk bumped by
    # 6 MB read / 3 MB write, all over 5 minutes.
    bumped = dict(parsed)
    bumped["host_net_rx_total"]    = 1000000 + 5 * 1024 * 1024
    bumped["host_net_tx_total"]    = 500000  + 1 * 1024 * 1024
    bumped["host_disk_read_total"]  = 4000000 + 6 * 1024 * 1024
    bumped["host_disk_write_total"] = 2000000 + 3 * 1024 * 1024
    t1 = t0 + 300
    row2, next2 = _compute_row("h1", t1, bumped, next1)
    assert row2 is not None
    assert abs(row2["net_rx_bps"] - (5 * 1024 * 1024) / 300) < 0.001, row2["net_rx_bps"]
    assert abs(row2["net_tx_bps"] - (1 * 1024 * 1024) / 300) < 0.001, row2["net_tx_bps"]
    assert abs(row2["disk_read_bps"]  - (6 * 1024 * 1024) / 300) < 0.001, row2["disk_read_bps"]
    assert abs(row2["disk_write_bps"] - (3 * 1024 * 1024) / 300) < 0.001, row2["disk_write_bps"]

    # Tick 3 — net counter rollback (reboot) but disk counters keep
    # advancing normally. Disk rates should compute; net rates skip.
    # Validates the INDEPENDENCE of the two rate pairs.
    mixed = dict(parsed)
    mixed["host_net_rx_total"] = 100   # post-reboot
    mixed["host_net_tx_total"] = 50
    mixed["host_disk_read_total"]  = next2[3] + 1024 * 1024  # +1 MB read
    mixed["host_disk_write_total"] = next2[4] + 512 * 1024   # +512 KB write
    t2 = t1 + 300
    row3, next3 = _compute_row("h1", t2, mixed, next2)
    assert row3 is not None
    assert row3["net_rx_bps"] is None, "net rollback must skip"
    assert row3["net_tx_bps"] is None
    assert row3["disk_read_bps"]  is not None, "disk pair must compute when its delta is in bounds"
    assert row3["disk_write_bps"] is not None
    assert next3 == (t2, 100, 50, next2[3] + 1024 * 1024, next2[4] + 512 * 1024)

    # Tick 4 — disk counter wrap (50 GB jump). Disk rates must skip;
    # net rates ALSO skip because we just rebaselined them in tick 3
    # (so prev_ts is t2 → delta_s ok, but prev_rx=100 → +very small ok).
    # Actually net deltas WILL compute small positive values here; we
    # only assert disk pair behaviour.
    wrap = dict(parsed)
    wrap["host_net_rx_total"] = 100 + 2048
    wrap["host_net_tx_total"] = 50  + 1024
    wrap["host_disk_read_total"]  = next3[3] + (50 * 1024 * 1024 * 1024)  # 50 GB
    wrap["host_disk_write_total"] = next3[4] + 1024
    t3 = t2 + 300
    row4, next4 = _compute_row("h1", t3, wrap, next3)
    assert row4 is not None
    assert row4["disk_read_bps"] is None, "out-of-bounds disk delta must skip"
    assert row4["disk_write_bps"] is None, (
        "single out-of-bounds field must skip BOTH disk rates"
    )
    # net should still have computed
    assert row4["net_rx_bps"] is not None and row4["net_tx_bps"] is not None

    # Tick 5 — short delta (60s window underflow). All four rates skip.
    short = dict(parsed)
    short["host_net_rx_total"] = next4[1] + 1024
    short["host_net_tx_total"] = next4[2] + 1024
    short["host_disk_read_total"]  = next4[3] + 1024
    short["host_disk_write_total"] = next4[4] + 1024
    t4 = t3 + 30  # below _MIN_DELTA_SECONDS=60
    row5, _ = _compute_row("h1", t4, short, next4)
    assert row5 is not None
    assert row5["net_rx_bps"]   is None and row5["net_tx_bps"]   is None
    assert row5["disk_read_bps"] is None and row5["disk_write_bps"] is None

    # Pre-#339 cache shape (3-tuple) — backwards compat. Disk rates
    # should skip (no prev disk anchor), net rates compute normally.
    legacy_prev = (t0, 1000000, 500000)  # missing disk fields
    legacy_bumped = dict(parsed)
    legacy_bumped["host_net_rx_total"] = 1000000 + 1 * 1024 * 1024
    legacy_bumped["host_net_tx_total"] = 500000  + 512 * 1024
    legacy_bumped["host_disk_read_total"]  = 5 * 1024 * 1024
    legacy_bumped["host_disk_write_total"] = 3 * 1024 * 1024
    row6, next6 = _compute_row("h2", t0 + 300, legacy_bumped, legacy_prev)
    assert row6 is not None
    assert row6["net_rx_bps"] is not None, "legacy 3-tuple prev still drives net rate"
    assert row6["disk_read_bps"]  is None, "no disk anchor → skip first disk rate"
    assert row6["disk_write_bps"] is None
    assert len(next6) == 5, "next_counter must be the new 5-tuple shape"

    # Empty probe — no fields at all.
    row7, next7 = _compute_row("h3", time.time(), {}, None)
    assert row7 is None and next7 is None

    # No-disk probe with a previous-tick anchor (#343 follow-up): the
    # current probe lacks host_disk_*_total entirely. Disk rates MUST
    # stay null even though prev had disk anchors. Other fields keep
    # working (net rate computes normally).
    no_disk_stats = _ne.parse_exporter_text(no_disk_fixture)
    no_disk_net_x = _ne.parse_network_counters(no_disk_fixture)
    no_disk_stats["host_net_rx_total"] = no_disk_net_x["total_rx"]
    no_disk_stats["host_net_tx_total"] = no_disk_net_x["total_tx"]
    # Deliberately NOT setting host_disk_*_total — mirrors what
    # probe_node does when parse_disk_counters returns None totals.
    fake_prev = (t0, 1, 1, 100, 50)  # has disk anchor from previous tick
    nd_row, _ = _compute_row("hno", t0 + 300, no_disk_stats, fake_prev)
    assert nd_row is not None
    assert nd_row["disk_read_bps"]  is None, "missing disk metrics → null rate"
    assert nd_row["disk_write_bps"] is None

    print("[host_metrics_sampler] smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_smoke_test())
