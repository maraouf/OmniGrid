"""Per-host Net I/O time-series sampler (node-exporter fallback).

The problem this solves: Beszel agents that aren't started with
``NICS=eth0`` (or equivalent) emit zero-valued ``nr`` / ``ns`` in every
``system_stats`` row. The Hosts tab's Net In/Out chart then renders as
a flat line at zero — technically "correct" but useless. When those same
hosts have a node-exporter :9100 endpoint available, OmniGrid can fill
the gap by scraping ``node_network_receive_bytes_total`` /
``node_network_transmit_bytes_total`` at a steady cadence and computing
rates across consecutive samples.

Design:
  - One lifespan-managed task (see CLAUDE.md's "Long-running tasks
    belong in `_lifespan`" rule).
  - Ticks on ``STATS_SAMPLE_INTERVAL_SECONDS`` (reused — no new tunable).
  - Per curated host with a ``ne_url`` and node-exporter in
    ``host_stats_source``, probe NE directly (bypassing the main gather
    cache — this is an independent sampler). Read the counter totals.
  - Look up the previous sample for the same host. If deltas are within
    sanity bounds, INSERT a new row with the derived per-second rate.
    Otherwise SKIP — counter rollovers, host reboots, long outages, and
    clock skew MUST NOT be stored as synthesized zeros; that would
    contaminate the series and hide real signal.
  - Prune hourly to ``STATS_HISTORY_DAYS``.

The sampler writes rates (bytes/s), NOT raw counters, because counter
bytes are only meaningful as a delta. Storing pre-computed rates makes
the read path trivial (fetch rows, no math) and keeps the `host_net_samples`
table append-only without per-row previous-lookup churn on the hot path
(the frontend's `/api/hosts/history` call).

Why a separate module from `logic/stats.py`? That sampler writes one row
per *Docker item* keyed off the live stats cache. This sampler writes one
row per *curated host* keyed off a DB setting (``hosts_config``) and its
own independent probe. Merging them would muddy both APIs.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Optional

import httpx

from logic import node_exporter as _ne
from logic import tuning
from logic.tuning import Tunable
from logic.db import db_conn
from logic.sampler_loop import lifespan_sampler_loop

# Sanity bounds for accepting a counter delta as a valid rate.
# - ``delta_seconds`` between 60s and 900s catches clock skew (negative or
# near-zero) and long outages (missed ticks where a "rate" would smear
# hours of traffic over one sample).
# - ``delta_bytes`` between 0 (monotonic) and 10 GB filters counter
# rollovers (negative deltas on a restart) and implausible spikes that
# almost always mean the kernel counter wrapped. 10 GB over 5 minutes
# is ~34 MB/s — well above any realistic homelab link — so any sample
# above that is almost certainly a rollover, not real traffic.
_MIN_DELTA_SECONDS = 60
_MAX_DELTA_SECONDS = 900
_MIN_DELTA_BYTES = 0
_MAX_DELTA_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB

# Active-providers parser + curated-hosts walker live in logic/db.py —
# single source of truth shared with main.py / gather.py / both
# samplers .
from logic.db import (  # noqa: E402
    active_host_stats_providers as _active_providers,
    curated_ne_hosts as _load_curated_hosts,
)


def _previous_sample(host_id: str) -> Optional[dict]:
    """Most recent ``host_net_samples`` row for one host, or None."""
    with db_conn() as c:
        r = c.execute(
            "SELECT ts, rx_bytes_per_s, tx_bytes_per_s "
            "FROM host_net_samples WHERE host_id=? "
            "ORDER BY ts DESC LIMIT 1",
            (host_id,),
        ).fetchone()
    if not r:
        return None
    return {"ts": int(r["ts"]),
            "rx_bytes_per_s": float(r["rx_bytes_per_s"]),
            "tx_bytes_per_s": float(r["tx_bytes_per_s"])}


# Previous RAW counter totals per host. The DB stores derived rates, not
# absolute counters, so we cache the last absolute reading in-process for
# delta math on the NEXT tick. Survives the lifetime of the sampler task;
# reset on container restart (which is correct — counter may have reset
# too, and the first post-restart delta should be SKIPPED).
_last_counters: dict[str, tuple[float, int, int]] = {}  # host_id → (ts, rx, tx)


def _is_paused(host_id: str) -> bool:
    """Read host_failure_state.paused for a host. True iff the host has
    been auto-paused by host_metrics_sampler's permanent-fail tracker
    . Same short-circuit host_metrics_sampler uses on its own
    _probe_one — without this, the net sampler keeps slamming a dead
    NE endpoint long after the metrics sampler has stopped, and the
    operator sees the [host_net_sampler] error spam they thought they
    silenced. Defensive: any DB error returns False so we don't
    accidentally suppress polling for ALL hosts on a transient SQLite
    BUSY.
    """
    try:
        with db_conn() as c:
            r = c.execute(
                "SELECT paused FROM host_failure_state "
                "WHERE host_id = ? AND provider = ''",
                (host_id,),
            ).fetchone()
    except (sqlite3.Error, OSError):
        return False
    return bool(r and r[0])


def _sanity_ok(delta_seconds: float, delta_rx: int, delta_tx: int) -> bool:
    """Return True iff the delta pair should be stored as a rate."""
    if not (_MIN_DELTA_SECONDS <= delta_seconds <= _MAX_DELTA_SECONDS):
        return False
    for d in (delta_rx, delta_tx):
        if d < _MIN_DELTA_BYTES or d > _MAX_DELTA_BYTES:
            return False
    return True


async def _probe_one(client: httpx.AsyncClient, host: dict) -> None:
    """Probe NE for one host; insert a sample if sanity checks pass."""
    hid = host["id"]
    ne_url = host["ne_url"]
    # Permanent-fail short-circuit. Mirror what
    # host_metrics_sampler._probe_one already does — paused hosts skip
    # the probe entirely until the operator resumes via the API. Without
    # this, the net sampler keeps emitting [host_net_sampler] error
    # lines for hosts the operator already silenced.
    if _is_paused(hid):
        return
    now = time.time()
    # Per-use read so Admin → Config edits to the NE probe timeout
    # land on the next sampler tick. Defensive fallback to the legacy
    # hardcoded 10s if `tuning_int` raises (corrupt DB state).
    try:
        _ne_to = float(tuning.tuning_int(
            Tunable.NODE_EXPORTER_PROBE_TIMEOUT_SECONDS))
    except (KeyError, ValueError, TypeError):
        _ne_to = 10.0
    try:
        stats = await _ne.probe_node(client, ne_url, timeout=_ne_to)
    except Exception as e:
        # Per-host failure isolation — log and move on. Next tick
        # retries; no cumulative state to clean up beyond the cached
        # counter pair, which we intentionally leave in place so a
        # transient blip doesn't force a "first sample" skip.
        print(f"[host_net_sampler] {hid!r} target={ne_url} probe error: {e}")
        return
    if stats.get("exporter_error"):
        # Include the resolved ne_url in the log so the operator can
        # verify the sampler is probing the RIGHT address (the curated
        # `id` is often a short alias).
        print(f"[host_net_sampler] {hid!r} target={ne_url} "
              f"exporter_error: {stats['exporter_error']}")
        return
    rx = int(stats.get("host_net_rx_total") or 0)
    tx = int(stats.get("host_net_tx_total") or 0)

    prev = _last_counters.get(hid)
    # Always update the cached counter before deciding — a counter we
    # reject as out-of-bounds should still become the next "previous" so
    # the following tick computes a fresh delta instead of forever
    # re-diffing against a stale baseline.
    _last_counters[hid] = (now, rx, tx)

    if prev is None:
        print(f"[host_net_sampler] {hid!r} first sample (rx={rx} tx={tx}); "
              f"skipping INSERT — delta needs a predecessor")
        return
    prev_ts, prev_rx, prev_tx = prev
    delta_s = now - prev_ts
    delta_rx = rx - prev_rx
    delta_tx = tx - prev_tx
    if not _sanity_ok(delta_s, delta_rx, delta_tx):
        # Counter rollover / host reboot / clock skew / long outage →
        # SKIP, don't synthesize. Storing 0 here would blend an hours-
        # long outage into the series as a flat zero (which is exactly
        # what the fallback is supposed to fix); storing a negative
        # would break every downstream chart assumption.
        print(f"[host_net_sampler] {hid!r} out-of-bounds delta "
              f"(Δs={delta_s:.1f} Δrx={delta_rx} Δtx={delta_tx}); skipping INSERT")
        return
    rx_rate = delta_rx / delta_s
    tx_rate = delta_tx / delta_s
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO host_net_samples "
                "(ts, host_id, rx_bytes_per_s, tx_bytes_per_s) "
                "VALUES (?, ?, ?, ?)",
                (int(now), hid, float(rx_rate), float(tx_rate)),
            )
    except Exception as e:
        print(f"[host_net_sampler] {hid!r} DB insert failed: {e}")
        return
    print(f"[host_net_sampler] {hid!r} wrote rx={rx_rate:.0f} tx={tx_rate:.0f} bytes/s")


def _prune_old_samples() -> int:
    days = tuning.tuning_int(Tunable.STATS_HISTORY_DAYS)
    cutoff = int(time.time() - days * 86400)
    try:
        with db_conn() as c:
            cur = c.execute("DELETE FROM host_net_samples WHERE ts < ?", (cutoff,))
            return cur.rowcount or 0
    except Exception as e:
        print(f"[host_net_sampler] prune failed: {e}")
        return 0


def _net_sampler_interval() -> int:
    """Resolve the net-sampler tick interval — reuses the global stats
    tunable so a NE-fallback chart stays in lockstep with the
    Beszel-native path's cadence.
    """
    return tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS)


async def _net_tick(tick: int) -> None:
    """Per-tick body — gate on NE active, walk curated hosts, hourly
    retention prune. CancelledError / KeyboardInterrupt propagate
    via the helper.
    """
    active = _active_providers()
    if "node_exporter" in active:
        hosts = _load_curated_hosts()
        if hosts:
            # Outer AsyncClient timeout is the ceiling for any
            # request that doesn't carry its own per-request override.
            # `_probe_one` uses an explicit per-call timeout from the
            # NE-probe TUNABLE; the outer ceiling is defence-in-depth
            # at the SAME tunable + a 50% headroom so the outer never
            # trips before the inner per-call cap. Defensive fallback
            # to 15s on tunable resolver failure.
            try:
                _outer_to = float(tuning.tuning_int(
                    Tunable.NODE_EXPORTER_PROBE_TIMEOUT_SECONDS)) * 1.5
            except (KeyError, ValueError, TypeError):
                _outer_to = 15.0
            async with httpx.AsyncClient(verify=False, timeout=_outer_to) as client:
                # Sequential over hosts — NE probes are already cheap
                # and this keeps the sampler's load on each host to at
                # most one request per interval.
                for host in hosts:
                    try:
                        await _probe_one(client, host)
                    except (asyncio.CancelledError, KeyboardInterrupt):
                        raise
                    except Exception as exc:  # noqa: BLE001
                        print(f"[host_net_sampler] {host.get('id')!r} unexpected: {exc}")
    interval = _net_sampler_interval()
    days = tuning.tuning_int(Tunable.STATS_HISTORY_DAYS)
    if tick % max(1, 3600 // interval) == 0:
        n = _prune_old_samples()
        if n:
            print(f"[host_net_sampler] pruned {n} rows older than {days}d")


async def host_net_sampler_loop() -> None:
    """Lifespan-managed sampler. One tick per
    ``tuning_stats_sample_interval_seconds`` (DB > env > default).

    Cadence matches the stats sampler so a ``hostHistory[]`` chart backed
    by NE fallback samples the same way the Beszel-native path does.
    """
    # Clear the in-process counter cache before the first tick. The
    # module-level dict survives a lifespan cancel/restart cycle
    # (tests, future hot-reload), and a stale "previous counter"
    # carried across the gap could yield an inflated rate on the
    # first new sample. The sanity-bounds checks would catch most of
    # these (Δs > 900 → skip), but a restart inside the window would
    # still write a wrong rate. Clearing here makes the first tick
    # after any restart establish a fresh baseline.
    _last_counters.clear()
    await lifespan_sampler_loop(
        "host_net_sampler",
        _net_tick,
        _net_sampler_interval,
        first_tick_delay=min(60, _net_sampler_interval()),
    )


def recent_samples(host_id: str, since_ts: int) -> list[dict]:
    """Return ``[{ts, rx_bytes_per_s, tx_bytes_per_s}, ...]`` for one host
    back to ``since_ts`` (epoch seconds), oldest-first.
    """
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, rx_bytes_per_s, tx_bytes_per_s "
                "FROM host_net_samples WHERE host_id=? AND ts >= ? "
                "ORDER BY ts ASC",
                (host_id, int(since_ts)),
            ).fetchall()
    except Exception as e:
        print(f"[host_net_sampler] recent_samples({host_id!r}) failed: {e}")
        return []
    return [
        {"ts": int(r["ts"]),
         "rx_bytes_per_s": float(r["rx_bytes_per_s"]),
         "tx_bytes_per_s": float(r["tx_bytes_per_s"])}
        for r in rows
    ]


def last_samples(host_id: str, limit: int = 5) -> list[dict]:
    """Newest-first recent rows for the debug endpoint."""
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, rx_bytes_per_s, tx_bytes_per_s "
                "FROM host_net_samples WHERE host_id=? "
                "ORDER BY ts DESC LIMIT ?",
                (host_id, int(limit)),
            ).fetchall()
    except Exception as e:
        print(f"[host_net_sampler] last_samples({host_id!r}) failed: {e}")
        return []
    return [
        {"ts": int(r["ts"]),
         "rx_bytes_per_s": float(r["rx_bytes_per_s"]),
         "tx_bytes_per_s": float(r["tx_bytes_per_s"])}
        for r in rows
    ]
