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
  - One lifespan-managed task (see the project's "Long-running tasks
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
from typing import Any, Optional

import httpx

from logic import node_exporter as _ne
from logic import tuning
from logic.tuning import Tunable
from logic.db import db_conn, prune_rows_older_than
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

# Per-host log-dedup state — when a host fails N times in a row with the
# SAME error signature, downgrade subsequent log lines from ERROR to WARN
# AND collapse the verbose multi-line ConnectTimeout / DNS diagnosis to
# a one-line summary. Operators have ALREADY seen the verbose hint on
# the FIRST failure; the second through N-th add zero diagnostic value
# but flood the log. The error keeps being recorded — just at lower
# severity + condensed shape. Latches off on first success.
# Schema: host_id → {"last_sig": str, "streak": int, "first_ts": float}.
# "last_sig" captures a stable signature of the error (klass + first 60
# chars of message) so a DIFFERENT failure mode still logs verbosely.
_failure_streak: dict[str, dict[str, Any]] = {}
# After this many consecutive identical failures, downgrade to WARN +
# one-line summary. Picked at 2 (default — first failure verbose ERROR,
# second onwards downgraded) so the diagnosis appears at least once
# before being suppressed but doesn't flood for 25 minutes waiting on
# the host_metrics_sampler auto-pause threshold (default 5 rounds × 5
# min interval = 25 min). Operators can spot a freshly-down host in the
# first log line; the auto-pause then takes over for the long term.
_FAILURE_VERBOSE_THRESHOLD = 2


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
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        # Per-host failure isolation — log and move on. Next tick
        # retries; no cumulative state to clean up beyond the cached
        # counter pair, which we intentionally leave in place so a
        # transient blip doesn't force a "first sample" skip.
        print(f"[host_net_sampler] {hid!r} target={ne_url} probe error: {e}")
        return
    if stats.get("exporter_error"):
        # Per-host failure-streak dedup. First failure → verbose
        # ERROR with full diagnosis (DNS hint, copy-paste python
        # probe, socket-fallback result). Second+ identical failure
        # → one-line WARN summary (the verbose hint adds no new
        # info — operators have already read it; spamming it every
        # 5 min for every dead host floods Admin → Logs). DIFFERENT
        # error signature (ConnectTimeout → ReadTimeout, or
        # ConnectTimeout against IP-A → ConnectTimeout against IP-B
        # for the same alias) re-logs verbosely. Latches off on
        # first success — next failure starts fresh.
        _err_full = str(stats["exporter_error"])
        _sig = _err_full[:120]  # stable signature
        _state = _failure_streak.get(hid)
        _first_ts: float = now
        if _state is None or _state.get("last_sig") != _sig:
            _streak = 1
            _failure_streak[hid] = {"last_sig": _sig, "streak": 1, "first_ts": now}
        else:
            # Explicit int() narrowing — dict[str, Any] makes the
            # static type Any | int, which trips Pyright's "int + Any"
            # check even though we know `streak` is always int (we
            # only ever store int in it). Belt + braces.
            _streak = int(_state.get("streak", 1) or 1) + 1
            _state["streak"] = _streak
            # Same narrowing for first_ts — only the previous-state
            # branch reads it; falls back to `now` defensively.
            try:
                _first_ts = float(_state.get("first_ts", now) or now)
            except (TypeError, ValueError):
                _first_ts = now
        # Above the threshold → condensed log line, WARN bucket
        # (still searchable, still actionable, but doesn't drown
        # the log). Use the `warning:` token so _severity_for routes
        # cleanly. Include the streak count so the operator sees
        # "this has failed N times in a row" at a glance.
        if _streak >= _FAILURE_VERBOSE_THRESHOLD:
            # Strip the verbose hint — keep only the leading error
            # type + URL for grep-ability. Pattern: split at first
            # " — " (em-dash with spaces, our standard separator
            # between "what failed" and "what to do about it").
            _short = _err_full.split(" — ", 1)[0]
            _first_age = int(now - _first_ts)
            print(f"[host_net_sampler] {hid!r} target={ne_url} "
                  f"warning: still failing (streak={_streak}, "
                  f"first_failure {_first_age}s ago): {_short}")
            return
        # First failure (or first failure after a SUCCESS / signature
        # change) — full verbose log with the socket-fallback
        # diagnostic. Subsequent identical failures will be suppressed
        # by the streak guard above.
        #
        # Include the resolved ne_url in the log so the operator can
        # verify the sampler is probing the RIGHT address (the curated
        # `id` is often a short alias).
        #
        # Diagnostic socket-fallback: when httpx reports a connection
        # failure, immediately try a raw TCP `socket.connect` to the
        # SAME target from the SAME process (asyncio thread pool, so
        # it doesn't block the event loop). The user-reported
        # confusion class is: httpx says ConnectTimeout, then the
        # user runs `python -c "socket.connect()"` from inside the
        # container and it succeeds — which reads as a contradiction
        # but is usually one of: (a) transient blip at the httpx
        # call moment that cleared by the time the user typed the
        # manual probe (sampler interval default 300s = log could be
        # 5min stale); (b) httpx connection-pool weirdness when the
        # PREVIOUS host's probe left a half-open connection; (c)
        # libc / asyncio getaddrinfo difference (unlikely for IP
        # literals). The fallback's outcome is appended to the error
        # line so operators get the comparison in ONE log entry
        # instead of needing to run a manual probe to find out.
        _diag = ""
        try:
            from urllib.parse import urlparse
            import socket
            _u = urlparse(ne_url)
            _host = _u.hostname or ""
            _port = _u.port or {"http": 80, "https": 443}.get(_u.scheme, 9100)
            if _host and _port:
                # 3s socket-connect — same parameters operators
                # type manually. asyncio.to_thread keeps the event
                # loop hot. Only runs in the error path so the
                # success path stays zero-cost.
                def _probe_socket():
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(3)
                    try:
                        s.connect((_host, _port))
                        return "OK"
                    except (TimeoutError, ConnectionRefusedError, OSError) as sock_err:
                        return f"{type(sock_err).__name__}: {sock_err}"
                    finally:
                        try:
                            s.close()
                        except OSError:
                            pass

                _r = await asyncio.to_thread(_probe_socket)
                if _r == "OK":
                    _diag = (
                        f"; raw socket.connect({_host}:{_port}) SUCCEEDED in "
                        f"≤3s — httpx and raw socket disagree. Likely causes: "
                        f"(a) transient blip at httpx call moment "
                        f"(sampler ticks every ~5 min, this log can be stale); "
                        f"(b) httpx connection-pool weirdness from a prior "
                        f"probe in the same tick; (c) IPv6 / DNS resolution "
                        f"differences (less likely for IP literals). Next "
                        f"sampler tick will retry."
                    )
                else:
                    _diag = (
                        f"; raw socket.connect({_host}:{_port}) ALSO failed "
                        f"({_r}) — the host is genuinely unreachable from "
                        f"this container right now. Diagnosis text in the "
                        f"exporter_error above is accurate."
                    )
        except Exception as _diag_err:  # noqa: BLE001
            # Diagnostic must not itself break the error path —
            # log + continue with the original exporter_error.
            _diag = f"; diag socket-probe failed: {type(_diag_err).__name__}"
        print(f"[host_net_sampler] {hid!r} target={ne_url} "
              f"exporter_error: {stats['exporter_error']}{_diag}")
        return
    # Successful probe — latch off any prior failure-streak so the
    # NEXT failure (if any) re-logs verbosely with the full diagnosis.
    # Mirrors the pattern in stats.py's _per_node_unreachable.pop(h).
    _failure_streak.pop(hid, None)
    rx = int(stats.get("host_net_rx_total") or 0)
    tx = int(stats.get("host_net_tx_total") or 0)

    prev = _last_counters.get(hid)
    # Always update the cached counter before deciding — a counter we
    # reject as out-of-bounds should still become the next "previous" so
    # the following tick computes a fresh delta instead of forever
    # re-diffing against a stale baseline.
    _last_counters[hid] = (now, rx, tx)

    if prev is None:
        # Include resolved ne_url so the operator can verify WHICH
        # exporter URL the sampler is establishing a baseline against
        # — the curated `id` is often a short alias and a wrong
        # `ne_url` will silently produce wrong counters from a
        # completely different host.
        print(f"[host_net_sampler] {hid!r} target={ne_url} "
              f"first sample (rx={rx} tx={tx}); "
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
        print(f"[host_net_sampler] {hid!r} target={ne_url} "
              f"out-of-bounds delta "
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
    except Exception as e: # noqa: BLE001
        print(f"[host_net_sampler] {hid!r} target={ne_url} DB insert failed: {e}")
        return
    print(f"[host_net_sampler] {hid!r} target={ne_url} "
          f"wrote rx={rx_rate:.0f} tx={tx_rate:.0f} bytes/s")


def _prune_old_samples() -> int:
    """Delete net-rate samples older than the retention window; returns the deleted-row count."""
    days = tuning.tuning_int(Tunable.STATS_HISTORY_DAYS)
    cutoff = int(time.time() - days * 86400)
    try:
        # Chunked delete (writer lock released per chunk) instead of one big
        # DELETE — same predicate, bounded lock-hold, seeks idx_host_net_samples_ts.
        return prune_rows_older_than("host_net_samples", cutoff)
    except Exception as e: # noqa: BLE001
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
        # Offload prune to worker thread so the event loop stays
        # responsive during the DELETE (same pattern as
        # host_metrics_sampler).
        from logic.sampler_metrics import prune_with_metrics
        n = await prune_with_metrics("host_net_sampler", _prune_old_samples)
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
    except Exception as e: # noqa: BLE001
        print(f"[host_net_sampler] recent_samples({host_id!r}) failed: {e}")
        return []
    return [
        {"ts": int(r["ts"]),
         "rx_bytes_per_s": float(r["rx_bytes_per_s"]),
         "tx_bytes_per_s": float(r["tx_bytes_per_s"])}
        for r in rows
    ]


# noinspection DuplicatedCode
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
    except Exception as e: # noqa: BLE001
        print(f"[host_net_sampler] last_samples({host_id!r}) failed: {e}")
        return []
    return [
        {"ts": int(r["ts"]),
         "rx_bytes_per_s": float(r["rx_bytes_per_s"]),
         "tx_bytes_per_s": float(r["tx_bytes_per_s"])}
        for r in rows
    ]
