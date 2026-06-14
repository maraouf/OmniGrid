"""Rundeck execution-outcome sampler — lifespan-managed.

Rundeck keeps its own execution history, but a glanceable LOCAL rollup of recent
success/failure counts gives the "is my automation getting flakier" failure-rate
trend at a glance. This sampler records each configured Rundeck chip's recent
success/failure tally (+ job / running counts) per tick into ``rundeck_samples``
so the card can draw a daily failure-rate strip over the retention window.

One row per ``(host_id, service_idx, tick)``: ``jobs`` / ``running`` (current
counts) + ``recent_failed`` / ``recent_total`` (the finished-execution tally
``fetch_data`` already computes for the failure-rate stat). Cadence
``tuning_rundeck_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_rundeck_history_days`` (default 30).
Dormant-cheap when no Rundeck chip is configured.

A fetch failure SKIPS the write (a zeroed tally would be a misleading "all
green" point — the trend should simply gap).
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from logic import tuning as _tuning
from logic.apps._common import resolve_sample_interval, sampler_instances
from logic.db import db_conn, prune_rows_older_than
from logic.sampler_loop import lifespan_sampler_loop
from logic.tuning import Tunable as _Tunable

_SLUG = "rundeck"


def _instances() -> list:
    """Configured Rundeck chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "rundeck_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global
    stats interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.RUNDECK_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Record one chip's sample: job / running counts + recent failed/total
    execution tally. A fetch failure SKIPS the write (a zeroed tally would be a
    misleading 'all green' point — the trend should just gap)."""
    try:
        from logic.apps import rundeck as _rd  # noqa: PLC0415
        data = await _rd.fetch_data(host_row, chip, host_id=host_id,
                                    service_idx=int(service_idx), force=True)
        jobs = int(data.get("jobs") or 0)
        running = int(data.get("running") or 0)
        recent_failed = int(data.get("recent_failed") or 0)
        recent_total = int(data.get("recent_completed") or 0)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[rundeck_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[rundeck_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO rundeck_samples "
                "(ts, host_id, service_idx, jobs, running, recent_failed, "
                "recent_total) VALUES (?,?,?,?,?,?,?)",
                (int(time.time()), host_id, int(service_idx),
                 int(jobs), int(running), int(recent_failed), int(recent_total)),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[rundeck_sampler] write {host_id}#{service_idx} failed: {e}")


def _prune_old_samples() -> int:
    """Delete rows older than the retention window. Returns the deleted-row
    count (chunked DELETE via the shared helper)."""
    days = _tuning.tuning_int(_Tunable.RUNDECK_HISTORY_DAYS)
    cutoff = int(time.time()) - days * 86400
    return prune_rows_older_than("rundeck_samples", cutoff)


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body: probe every configured Rundeck chip in parallel, then run
    the hourly retention prune (offloaded to a worker thread)."""
    instances = _instances()
    if instances:
        await asyncio.gather(
            *(_probe_one(hid, idx, h, chip)
              for (hid, idx, h, chip) in instances),
            return_exceptions=True,
        )
    interval = _resolve_interval()
    if tick % max(1, 3600 // max(1, interval)) == 0:
        from logic.sampler_metrics import prune_with_metrics  # noqa: PLC0415
        n = await prune_with_metrics("rundeck_sampler", _prune_old_samples)
        if n:
            days = _tuning.tuning_int(_Tunable.RUNDECK_HISTORY_DAYS)
            print(f"[rundeck_sampler] pruned {n} rows older than {days}d")


async def rundeck_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Rundeck chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    await lifespan_sampler_loop(
        "rundeck_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: int = 30, *, max_points: int = 30) -> dict:
    """Daily failure-rate trend for one chip. Returns ``{series, peak, avg,
    samples, current, days}`` where ``series`` is up to ``days`` daily failure-
    RATE points (0-100, percent; daily-MAX failed/total over that day, 0-filled
    for missing days) for a sparkline. Zeroed shape when no samples yet — never
    raises."""
    out: dict = {"series": [], "peak": 0.0, "avg": 0.0, "samples": 0,
                 "current": 0.0, "days": int(days)}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(days) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, recent_failed, recent_total FROM rundeck_samples "
                "WHERE host_id=? AND service_idx=? AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[rundeck_sampler] trend_summary({host_id}#{service_idx}) "
              f"failed: {e}")
        return out
    if not rows:
        return out
    # Per-sample failure-rate %, then daily-MAX bucket for the series.
    rates: list = []
    day_max: dict = defaultdict(float)
    for r in rows:
        total = int(r["recent_total"] or 0)
        failed = int(r["recent_failed"] or 0)
        rate = round(failed / total * 100, 1) if total else 0.0
        rates.append(rate)
        d = int(r["ts"]) // 86400
        if rate > day_max[d]:
            day_max[d] = rate
    out["samples"] = len(rates)
    out["peak"] = max(rates)
    out["avg"] = round(sum(rates) / len(rates), 1)
    out["current"] = rates[-1]
    today = int(time.time()) // 86400
    span = max(1, min(int(days), int(max_points)))
    start_day = today - span + 1
    out["series"] = [round(float(day_max.get(d, 0.0)), 1)
                     for d in range(start_day, today + 1)]
    return out
