"""Forgejo review-queue retention sampler.

Forgejo / Gitea charts no aggregate history of the open-PR / open-issue backlog,
so a self-host glance can't answer "has my review queue been climbing for two
weeks?". This lifespan sampler snapshots each configured Forgejo chip per tick
into ``forgejo_samples`` so the card can draw an open-backlog burn-down
sparkline + a "backlog up/down N this week" stat.

``open_prs`` / ``open_issues`` / ``notifications`` are point-in-time gauges, so
the trend reads each day's MEAN. Cadence ``tuning_forgejo_sample_interval_seconds``
(0 = inherit the global stats interval); retention ``tuning_forgejo_history_days``
(default 90). Dormant-cheap when no Forgejo chip is configured. Generic tick /
instance-enum / cadence resolve delegate to the shared ``logic/apps/_common.py``
sampler scaffolding; the per-app probe-write + trend math are Forgejo-specific.
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

_SLUG = "forgejo"


def _instances() -> list:
    """Configured Forgejo chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "forgejo_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.FORGEJO_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one Forgejo host's open-PR / open-issue / notification backlog.
    A host that's down / unreachable skips the write (no phantom 0 row); a code
    bug also skips."""
    try:
        from logic.apps import forgejo as _forgejo  # noqa: PLC0415
        data = await _forgejo.fetch_data(host_row, chip, host_id=host_id,
                                         service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[forgejo_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[forgejo_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("open_prs")), safe_int(data.get("open_issues")),
           safe_int(data.get("notifications")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO forgejo_samples "
                "(ts, host_id, service_idx, open_prs, open_issues, notifications) "
                "VALUES (?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[forgejo_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    Forgejo host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="forgejo_sampler",
        prune_table="forgejo_samples",
        history_days_tunable=_Tunable.FORGEJO_HISTORY_DAYS)


async def forgejo_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Forgejo chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "forgejo_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Open-backlog trend for one Forgejo chip. Returns ``{days, samples,
    latest_backlog, peak_backlog, week_change, series_backlog}``.

    ``series_backlog`` is each day's MEAN (open_prs + open_issues) — the review-
    queue burn-down line. ``week_change`` is the latest backlog minus the backlog
    ~7 days ago (positive = climbing). Each series is up to ``max_points`` points
    (oldest-first, days WITH data only). Zeroed shape when no samples yet —
    never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.FORGEJO_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "latest_backlog": 0,
                 "peak_backlog": 0, "week_change": 0, "series_backlog": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, open_prs, open_issues "
                "FROM forgejo_samples WHERE host_id=? AND service_idx=? AND ts >= ? "
                "ORDER BY ts ASC", (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[forgejo_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    # Per-day MEAN backlog (open_prs + open_issues), oldest-first.
    day_sum: dict = defaultdict(int)
    day_cnt: dict = defaultdict(int)
    for r in rows:
        day = int(r["ts"]) // 86400
        day_sum[day] += safe_int(r["open_prs"]) + safe_int(r["open_issues"])
        day_cnt[day] += 1
    ordered = sorted(day_cnt)
    series = [round(day_sum[d] / max(1, day_cnt[d])) for d in ordered]
    out["latest_backlog"] = series[-1]
    out["peak_backlog"] = max(series)
    # Week-change: latest day's backlog vs the day closest to ~7 days earlier.
    last_day = ordered[-1]
    target = last_day - 7
    prior_day = min(ordered, key=lambda d: abs(d - target))
    prior = round(day_sum[prior_day] / max(1, day_cnt[prior_day]))
    out["week_change"] = series[-1] - prior
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series = [series[i] for i in idx]
    out["series_backlog"] = series
    return out
