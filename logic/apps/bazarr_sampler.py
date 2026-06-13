"""Bazarr subtitle-backlog retention sampler.

Bazarr's ``GET /api/badges`` exposes only the CURRENT missing-subtitle counts
(episodes + movies) — there is no wanted-count history to chart. This lifespan
sampler snapshots each configured Bazarr chip's backlog per tick into
``bazarr_samples`` so the card can draw a "subtitle backlog over 30d" sparkline
plus a "backlog down N this week" stat.

The columns are point-in-time GAUGES, so the daily roll-up is an AVERAGE. Cadence
``tuning_bazarr_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_bazarr_history_days`` (default 30). Dormant-cheap
when no Bazarr chip is configured. Generic tick / instance-enum / cadence resolve
delegate to the shared ``logic/apps/_common.py`` sampler scaffolding; the per-app
probe-write + trend math are Bazarr-specific.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import (
    resolve_sample_interval, run_sampler_tick, sampler_instances)
from logic.coerce import safe_int
from logic.db import db_conn
from logic.tuning import Tunable as _Tunable

_SLUG = "bazarr"


def _instances() -> list:
    """Configured Bazarr chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "bazarr_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.BAZARR_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one Bazarr host's missing-subtitle backlog (episodes + movies).
    A host that's down / unreachable / has no key skips the write (no phantom 0
    row); a code bug also skips."""
    try:
        from logic.apps import bazarr as _bazarr  # noqa: PLC0415
        data = await _bazarr.fetch_data(host_row, chip, host_id=host_id,
                                        service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[bazarr_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[bazarr_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("episodes_missing")), safe_int(data.get("movies_missing")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO bazarr_samples "
                "(ts, host_id, service_idx, episodes_missing, movies_missing) "
                "VALUES (?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[bazarr_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    Bazarr host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="bazarr_sampler",
        prune_table="bazarr_samples",
        history_days_tunable=_Tunable.BAZARR_HISTORY_DAYS)


async def bazarr_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Bazarr chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "bazarr_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Subtitle-backlog trend for one Bazarr chip. Returns ``{days, samples,
    latest_backlog, peak_backlog, week_change, series_backlog}`` where
    ``series_backlog`` is up to ``max_points`` daily-AVERAGE TOTAL-backlog points
    (episodes + movies; oldest-first, days WITH data only — the gauges are
    current counts, so a 0-fill day would be a false reading). ``week_change`` is
    ``latest - (the backlog ~7 days ago)`` — negative means the backlog shrank.
    Zeroed shape when no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.BAZARR_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "latest_backlog": 0,
                 "peak_backlog": 0, "week_change": 0, "series_backlog": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, episodes_missing, movies_missing FROM bazarr_samples "
                "WHERE host_id=? AND service_idx=? AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[bazarr_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["latest_backlog"] = (safe_int(rows[-1]["episodes_missing"])
                             + safe_int(rows[-1]["movies_missing"]))
    # Daily-AVERAGE total backlog (gauge → mean per day), days with data only.
    day_sum: dict = defaultdict(int)
    day_cnt: dict = defaultdict(int)
    for r in rows:
        day = int(r["ts"]) // 86400
        day_sum[day] += safe_int(r["episodes_missing"]) + safe_int(r["movies_missing"])
        day_cnt[day] += 1
    ordered = sorted(day_cnt)
    series = [round(day_sum[d] / max(1, day_cnt[d])) for d in ordered]
    out["peak_backlog"] = max(series) if series else 0
    # Week-over-week change: latest day's mean vs the day closest to 7 days back.
    last_day = ordered[-1]
    target = last_day - 7
    ref_day = min(ordered, key=lambda d: abs(d - target))
    out["week_change"] = series[-1] - series[ordered.index(ref_day)]
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series = [series[i] for i in idx]
    out["series_backlog"] = series
    return out
