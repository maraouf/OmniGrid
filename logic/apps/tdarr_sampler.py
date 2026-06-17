"""Tdarr transcode-pipeline retention sampler.

Tdarr's StatisticsJSONDB carries the CURRENT cumulative totals (``sizeDiff`` =
net GB reclaimed, ``totalTranscodeCount``) + the current queue depths, but no
built-in time series for the card. This lifespan sampler snapshots each
configured Tdarr chip per tick into ``tdarr_samples`` so the card can draw:

  * a CUMULATIVE space-saved line — "reclaimed X TB and counting" (the most
    satisfying Tdarr visual; ``space_saved_gb`` is a monotonic running total, so
    the daily series is each day's LAST value),
  * a transcode-queue BURN-DOWN (``transcode_queue`` is a gauge → daily avg), and
  * per-day THROUGHPUT (``transcodes`` is a cumulative counter → diff between
    consecutive days).

Cadence ``tuning_tdarr_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_tdarr_history_days`` (default 365 — the space-
saved story is most satisfying over a long window). Dormant-cheap when no Tdarr
chip is configured. Generic tick / instance-enum / cadence resolve delegate to
the shared ``logic/apps/_common.py`` sampler scaffolding; the per-app probe-write
+ trend math are Tdarr-specific.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import (
    resolve_sample_interval, run_sampler_tick, sampler_instances)
from logic.coerce import safe_float, safe_int
from logic.db import db_conn
from logic.tuning import Tunable as _Tunable

_SLUG = "tdarr"


def _instances() -> list:
    """Configured Tdarr chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "tdarr_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.TDARR_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one Tdarr host's cumulative space-saved + transcode count +
    queue + file count. A host that's down / unreachable skips the write (no
    phantom 0 row); a code bug also skips."""
    try:
        from logic.apps import tdarr as _tdarr  # noqa: PLC0415
        data = await _tdarr.fetch_data(host_row, chip, host_id=host_id,
                                       service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[tdarr_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[tdarr_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("total_files")), safe_int(data.get("transcode_queue")),
           safe_float(data.get("space_saved_gb")), safe_int(data.get("transcodes")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO tdarr_samples "
                "(ts, host_id, service_idx, total_files, transcode_queue, "
                "space_saved_gb, transcodes) VALUES (?,?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[tdarr_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    Tdarr host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="tdarr_sampler",
        prune_table="tdarr_samples",
        history_days_tunable=_Tunable.TDARR_HISTORY_DAYS)


async def tdarr_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Tdarr chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "tdarr_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Transcode-pipeline trend for one Tdarr chip. Returns ``{days, samples,
    latest_saved_gb, latest_queue, peak_queue, window_throughput,
    series_saved, series_queue, series_throughput}``.

    ``series_saved`` is each day's LAST cumulative ``space_saved_gb`` (the
    reclaimed-space line); ``series_queue`` is each day's AVG ``transcode_queue``
    (the burn-down); ``series_throughput`` is the per-day DELTA of the cumulative
    ``transcodes`` counter (negatives — a Tdarr stats reset — are dropped). Each
    series is up to ``max_points`` points (oldest-first, days WITH data only).
    ``window_throughput`` is the total transcodes completed across the window;
    ``throughput_per_day`` is the RECENT completion rate (mean of the last up-to-7
    days, dropping the first-day sentinel) — drives the "time to empty queue" ETA.
    Zeroed shape when no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.TDARR_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "latest_saved_gb": 0.0,
                 "latest_queue": 0, "peak_queue": 0, "window_throughput": 0,
                 "throughput_per_day": 0.0,
                 "series_saved": [], "series_queue": [], "series_throughput": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, transcode_queue, space_saved_gb, transcodes "
                "FROM tdarr_samples WHERE host_id=? AND service_idx=? AND ts >= ? "
                "ORDER BY ts ASC", (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[tdarr_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["latest_saved_gb"] = round(safe_float(rows[-1]["space_saved_gb"]), 1)
    out["latest_queue"] = safe_int(rows[-1]["transcode_queue"])
    out["peak_queue"] = max(safe_int(r["transcode_queue"]) for r in rows)
    # Per-day roll-up: LAST cumulative space-saved + transcodes (monotonic →
    # take the latest sample of the day) + AVG queue (a gauge).
    q_sum: dict = defaultdict(int)
    q_cnt: dict = defaultdict(int)
    saved_last: dict = {}
    tc_last: dict = {}
    for r in rows:
        day = int(r["ts"]) // 86400
        q_sum[day] += safe_int(r["transcode_queue"])
        q_cnt[day] += 1
        saved_last[day] = safe_float(r["space_saved_gb"])  # rows are ts ASC
        tc_last[day] = safe_int(r["transcodes"])
    ordered = sorted(q_cnt)
    series_saved = [round(saved_last[d], 1) for d in ordered]
    series_queue = [round(q_sum[d] / max(1, q_cnt[d])) for d in ordered]
    # Per-day throughput = diff of the cumulative transcode counter between
    # consecutive days-with-data. First day has no predecessor → 0; a negative
    # delta (Tdarr stats reset / DB wipe) is clamped to 0 (never a false spike).
    series_throughput = []
    prev_tc = None
    for d in ordered:
        if prev_tc is None:
            series_throughput.append(0)
        else:
            series_throughput.append(max(0, tc_last[d] - prev_tc))
        prev_tc = tc_last[d]
    out["window_throughput"] = sum(series_throughput)
    # Recent completion rate (transcodes/day) for the queue burn-down ETA — mean
    # of the last up-to-7 days of throughput, dropping the first-day 0 sentinel
    # (it has no predecessor). 0 when < 2 days of data (no ETA possible). Computed
    # from the FULL series, before the max_points downsample below.
    _rates = series_throughput[1:]
    _recent = _rates[-7:]
    out["throughput_per_day"] = (round(sum(_recent) / len(_recent), 1)
                                 if _recent else 0.0)
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_saved = [series_saved[i] for i in idx]
        series_queue = [series_queue[i] for i in idx]
        series_throughput = [series_throughput[i] for i in idx]
    out["series_saved"] = series_saved
    out["series_queue"] = series_queue
    out["series_throughput"] = series_throughput
    return out
