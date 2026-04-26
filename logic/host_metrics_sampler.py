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
import os
import time
from typing import Optional

import httpx

from logic import node_exporter as _ne
from logic.db import db_conn, get_setting


STATS_HISTORY_DAYS = int(os.getenv("STATS_HISTORY_DAYS", "7"))
STATS_SAMPLE_INTERVAL = int(os.getenv("STATS_SAMPLE_INTERVAL_SECONDS", "300"))  # 5 min


# Sanity bounds — same values, same rationale as host_net_sampler.
_MIN_DELTA_SECONDS = 60
_MAX_DELTA_SECONDS = 900
_MIN_DELTA_BYTES   = 0
_MAX_DELTA_BYTES   = 10 * 1024 * 1024 * 1024  # 10 GB


# Per-host previous absolute net counters for delta math. Lives across
# ticks within one sampler-task lifetime; cleared on lifespan
# cancel/restart so the post-restart first delta is correctly SKIPPED.
_last_counters: dict[str, tuple[float, int, int]] = {}  # host_id → (ts, rx, tx)


# Concurrency cap on parallel NE probes per tick. Matches the convention
# elsewhere (REGISTRY_CONCURRENCY=8, STATS_CONCURRENCY=16) — host probes
# are heavier than registry HEADs but lighter than container stats fans.
_PROBE_CONCURRENCY = 8


def _active_providers() -> set[str]:
    """Which host-stats providers are live? Mirrors host_net_sampler."""
    raw = (get_setting("host_stats_source", "") or "").strip()
    if not raw:
        enabled = (get_setting("node_exporter_enabled", "false") or "false").lower() == "true"
        raw = "node_exporter" if enabled else ""
    return {
        s.strip().lower()
        for s in raw.split(",")
        if s.strip() and s.strip().lower() != "none"
    }


def _load_curated_hosts() -> list[dict]:
    """Curated hosts with a usable ``ne_url`` and ``enabled=True``."""
    raw = get_setting("hosts_config", "") or ""
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict] = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        if not row.get("enabled", True):
            continue
        ne_url = (row.get("ne_url") or "").strip()
        if not ne_url:
            continue
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        out.append({"id": hid, "ne_url": ne_url})
    return out


def _is_meaningful_number(v) -> bool:
    """Treat None / non-numeric / 0 as 'no signal'."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return False
    return n > 0


def _sanity_ok(delta_seconds: float, delta_rx: int, delta_tx: int) -> bool:
    if not (_MIN_DELTA_SECONDS <= delta_seconds <= _MAX_DELTA_SECONDS):
        return False
    for d in (delta_rx, delta_tx):
        if d < _MIN_DELTA_BYTES or d > _MAX_DELTA_BYTES:
            return False
    return True


def _compute_row(
    host_id: str,
    now: float,
    stats: dict,
    prev: Optional[tuple[float, int, int]],
) -> tuple[Optional[dict], Optional[tuple[float, int, int]]]:
    """Pure-function core: turn a probe payload + previous counters into
    an INSERT-shaped row plus the next-tick counter cache.

    Returns ``(row_or_none, next_counter_or_none)``. The row is None when
    every gauge AND the rate pair are unmeaningful — there's nothing
    worth persisting. The next_counter is None when the current probe
    didn't return rx/tx counters at all (don't poison the cache with a
    zeroed entry — next tick should still treat itself as "first").

    Skip rules:
      - Net rates: SKIP (don't insert 0) when delta is out of bounds OR
        when there's no previous sample.
      - Gauges: SKIP an individual field (store NULL) when not meaningful.
        Whole row is dropped only when every field would be NULL.
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

    # Net rates — derived from monotonic counters across two ticks.
    rx_total = stats.get("host_net_rx_total")
    tx_total = stats.get("host_net_tx_total")
    have_counters = (rx_total is not None) and (tx_total is not None)

    rx_rate: Optional[float] = None
    tx_rate: Optional[float] = None
    next_counter: Optional[tuple[float, int, int]] = None

    if have_counters:
        try:
            rx = int(rx_total)
            tx = int(tx_total)
        except (TypeError, ValueError):
            rx = tx = 0
            have_counters = False

    if have_counters:
        # Always update the cached counter even when we reject the delta
        # — same logic as host_net_sampler. A rejected delta still
        # establishes the next baseline so we don't keep diffing against
        # a stale anchor forever.
        next_counter = (now, rx, tx)
        if prev is not None:
            prev_ts, prev_rx, prev_tx = prev
            delta_s = now - prev_ts
            delta_rx = rx - prev_rx
            delta_tx = tx - prev_tx
            if _sanity_ok(delta_s, delta_rx, delta_tx):
                rx_rate = delta_rx / delta_s
                tx_rate = delta_tx / delta_s

    # If literally nothing meaningful — drop the row.
    nothing_to_write = (
        cpu_percent is None
        and not _is_meaningful_number(mem_used)
        and not _is_meaningful_number(mem_total)
        and not _is_meaningful_number(disk_used)
        and not _is_meaningful_number(disk_total)
        and rx_rate is None
        and tx_rate is None
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
                    "disk_used, disk_total, net_rx_bps, net_tx_bps) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row["ts"], row["host_id"], row["cpu_percent"],
                        row["mem_used"], row["mem_total"],
                        row["disk_used"], row["disk_total"],
                        row["net_rx_bps"], row["net_tx_bps"],
                    ),
                )
        except Exception as e:
            print(f"[host_metrics_sampler] {hid!r} DB insert failed: {e}")
            return
        rate_blurb = (
            f"rx={row['net_rx_bps']:.0f} tx={row['net_tx_bps']:.0f} bytes/s"
            if (row["net_rx_bps"] is not None and row["net_tx_bps"] is not None)
            else "rates=skip"
        )
        print(f"[host_metrics_sampler] {hid!r} wrote cpu={row['cpu_percent']} "
              f"mem={row['mem_used']}/{row['mem_total']} "
              f"disk={row['disk_used']}/{row['disk_total']} {rate_blurb}")


def _prune_old_samples() -> int:
    cutoff = int(time.time() - STATS_HISTORY_DAYS * 86400)
    try:
        with db_conn() as c:
            cur = c.execute("DELETE FROM host_metrics_samples WHERE ts < ?", (cutoff,))
            return cur.rowcount or 0
    except Exception as e:
        print(f"[host_metrics_sampler] prune failed: {e}")
        return 0


async def host_metrics_sampler_loop() -> None:
    """Lifespan-managed sampler. One tick per ``STATS_SAMPLE_INTERVAL``."""
    _last_counters.clear()
    # Wait a beat so DB tables exist + hosts_config is loaded before the
    # first probe. Same pattern as host_net_sampler / stats_sampler.
    await asyncio.sleep(min(60, STATS_SAMPLE_INTERVAL))
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
            if tick % max(1, 3600 // STATS_SAMPLE_INTERVAL) == 0:
                n = _prune_old_samples()
                if n:
                    print(f"[host_metrics_sampler] pruned {n} rows older than "
                          f"{STATS_HISTORY_DAYS}d")
        except Exception as e:
            print(f"[host_metrics_sampler] tick error: {e}")
        tick += 1
        try:
            await asyncio.sleep(STATS_SAMPLE_INTERVAL)
        except asyncio.CancelledError:
            raise


def recent_samples(host_id: str, since_ts: int, limit: int = 500) -> list[dict]:
    """Return rows for one host back to ``since_ts`` (epoch s), oldest-first."""
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, cpu_percent, mem_used, mem_total, "
                "disk_used, disk_total, net_rx_bps, net_tx_bps "
                "FROM host_metrics_samples "
                "WHERE host_id=? AND ts >= ? "
                "ORDER BY ts ASC LIMIT ?",
                (host_id, int(since_ts), int(limit)),
            ).fetchall()
    except Exception as e:
        print(f"[host_metrics_sampler] recent_samples({host_id!r}) failed: {e}")
        return []
    return [
        {
            "ts": int(r["ts"]),
            "cpu_percent": (float(r["cpu_percent"]) if r["cpu_percent"] is not None else None),
            "mem_used":   (int(r["mem_used"])   if r["mem_used"]   is not None else None),
            "mem_total":  (int(r["mem_total"])  if r["mem_total"]  is not None else None),
            "disk_used":  (int(r["disk_used"])  if r["disk_used"]  is not None else None),
            "disk_total": (int(r["disk_total"]) if r["disk_total"] is not None else None),
            "net_rx_bps": (float(r["net_rx_bps"]) if r["net_rx_bps"] is not None else None),
            "net_tx_bps": (float(r["net_tx_bps"]) if r["net_tx_bps"] is not None else None),
        }
        for r in rows
    ]


def last_samples(host_id: str, limit: int = 5) -> list[dict]:
    """Newest-first recent rows for the debug endpoint."""
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, cpu_percent, mem_used, mem_total, "
                "disk_used, disk_total, net_rx_bps, net_tx_bps "
                "FROM host_metrics_samples WHERE host_id=? "
                "ORDER BY ts DESC LIMIT ?",
                (host_id, int(limit)),
            ).fetchall()
    except Exception as e:
        print(f"[host_metrics_sampler] last_samples({host_id!r}) failed: {e}")
        return []
    return [
        {
            "ts": int(r["ts"]),
            "cpu_percent": (float(r["cpu_percent"]) if r["cpu_percent"] is not None else None),
            "mem_used":   (int(r["mem_used"])   if r["mem_used"]   is not None else None),
            "mem_total":  (int(r["mem_total"])  if r["mem_total"]  is not None else None),
            "disk_used":  (int(r["disk_used"])  if r["disk_used"]  is not None else None),
            "disk_total": (int(r["disk_total"]) if r["disk_total"] is not None else None),
            "net_rx_bps": (float(r["net_rx_bps"]) if r["net_rx_bps"] is not None else None),
            "net_tx_bps": (float(r["net_tx_bps"]) if r["net_tx_bps"] is not None else None),
        }
        for r in rows
    ]


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
            # NE doesn't surface per-mount disk I/O, swap, or load avg in
            # a sampler-friendly way today. Future work could fold them
            # in (counter math for `node_disk_*_total`, gauges for
            # `node_load1`/`5`/`15`). Returning zeros keeps the frontend
            # gates honest — the cards stay hidden until something real
            # lands here.
            "dr":  0.0,
            "dw":  0.0,
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
node_cpu_seconds_total{cpu="0",mode="idle"} 1234.5
node_cpu_seconds_total{cpu="1",mode="idle"} 2345.6
node_uname_info{sysname="Linux",release="5.15.0",machine="x86_64"} 1
node_boot_time_seconds 1700000000
"""
    parsed = _ne.parse_exporter_text(fixture)
    net = _ne.parse_network_counters(fixture)
    parsed["host_net_rx_total"] = net["total_rx"]
    parsed["host_net_tx_total"] = net["total_tx"]

    # Tick 1 — no previous counter, should establish baseline + drop row
    # (no rates yet, gauges meaningful but rate skip is the load-bearing
    # behaviour we're checking).
    t0 = 1700000000.0
    row1, next1 = _compute_row("h1", t0, parsed, None)
    assert next1 == (t0, 1000000, 500000), f"baseline mismatch: {next1}"
    # gauges are meaningful so a row IS produced (rates simply absent)
    assert row1 is not None and row1["net_rx_bps"] is None and row1["net_tx_bps"] is None
    assert row1["mem_total"] == 8589934592
    assert row1["mem_used"] == 8589934592 - 4294967296
    assert row1["disk_total"] == 107374182400
    assert row1["disk_used"] == 107374182400 - 53687091200

    # Tick 2 — counters bumped by 5 MB rx and 1 MB tx over 5 minutes.
    bumped = dict(parsed)
    bumped["host_net_rx_total"] = 1000000 + 5 * 1024 * 1024
    bumped["host_net_tx_total"] = 500000  + 1 * 1024 * 1024
    t1 = t0 + 300
    row2, next2 = _compute_row("h1", t1, bumped, next1)
    assert row2 is not None
    expected_rx_rate = (5 * 1024 * 1024) / 300
    expected_tx_rate = (1 * 1024 * 1024) / 300
    assert abs(row2["net_rx_bps"] - expected_rx_rate) < 0.001, row2["net_rx_bps"]
    assert abs(row2["net_tx_bps"] - expected_tx_rate) < 0.001, row2["net_tx_bps"]
    assert row2["ts"] == int(t1)
    assert row2["host_id"] == "h1"

    # Tick 3 — counter ROLLBACK (host reboot). Delta is negative → SKIP
    # rates; cache must still update so the NEXT tick treats this as the
    # new baseline.
    rolled_back = dict(parsed)
    rolled_back["host_net_rx_total"] = 100   # tiny number = post-reboot
    rolled_back["host_net_tx_total"] = 50
    t2 = t1 + 300
    row3, next3 = _compute_row("h1", t2, rolled_back, next2)
    # gauges still meaningful → row produced; rates MUST be None
    assert row3 is not None
    assert row3["net_rx_bps"] is None, "rollback rate must skip, not synthesize"
    assert row3["net_tx_bps"] is None
    assert next3 == (t2, 100, 50), f"cache should advance even on skip: {next3}"

    # Tick 4 — short delta (60s window underflow). Fixture-style: call
    # _compute_row 30s after the previous baseline, both counters bumped.
    short_bumped = dict(parsed)
    short_bumped["host_net_rx_total"] = 100 + 1024
    short_bumped["host_net_tx_total"] = 50  + 1024
    t3 = t2 + 30  # 30s gap — below _MIN_DELTA_SECONDS=60
    row4, next4 = _compute_row("h1", t3, short_bumped, next3)
    assert row4 is not None
    assert row4["net_rx_bps"] is None, "short delta must skip"
    assert row4["net_tx_bps"] is None
    assert next4 == (t3, 100 + 1024, 50 + 1024)

    # Tick 5 — implausibly large delta (counter wrap). 50 GB jump in 5min.
    huge_bumped = dict(parsed)
    huge_bumped["host_net_rx_total"] = next4[1] + (50 * 1024 * 1024 * 1024)
    huge_bumped["host_net_tx_total"] = next4[2] + 1024
    t4 = t3 + 300
    row5, _ = _compute_row("h1", t4, huge_bumped, next4)
    assert row5 is not None
    assert row5["net_rx_bps"] is None, "out-of-bounds delta must skip rate"
    # tx delta is in bounds though, so it should still compute
    assert row5["net_tx_bps"] is None, (
        "single out-of-bounds field must skip BOTH rates "
        "(_sanity_ok is all-or-nothing)"
    )

    # Empty probe — no fields at all.
    row6, next6 = _compute_row("h2", time.time(), {}, None)
    assert row6 is None and next6 is None

    print("[host_metrics_sampler] smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_smoke_test())
