"""Tracearr fleet retention sampler.

Tracearr's Public API exposes only POINT-IN-TIME counts — ``recentViolations``
is the rolling 7-day account-sharing count and ``activeStreams`` is the current
concurrency. Neither answers "is abuse trending UP" or "how busy was the fleet
overnight" on its own. This lifespan sampler snapshots each configured Tracearr
chip's ``active_streams`` + ``recent_violations`` + ``total_sessions`` per tick
into ``tracearr_samples`` so the card can draw:

  - a VIOLATION trend (the daily-peak 7d-rolling violation count) + a
    violations-per-100-plays RATE stat — the P1 "abuse trending up" signal; and
  - a CONCURRENCY trend (the daily-peak active-stream count) + a "peak N · now M"
    stat — the P2 fleet-busy signal, independent of Tracearr's own retention.

The columns are point-in-time GAUGES; the daily roll-up for BOTH stream +
violation counts is the day's MAX (peaks matter more than means for bursty
counts). Cadence ``tuning_tracearr_sample_interval_seconds`` (0 = inherit the
global stats interval, default 900s — violation/concurrency counts move slowly);
retention ``tuning_tracearr_history_days`` (default 30). Dormant-cheap when no
Tracearr chip is configured. Generic tick / instance-enum / cadence resolve
delegate to the shared ``logic/apps/_common.py`` sampler scaffolding; the per-app
probe-write + trend math are Tracearr-specific.
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

_SLUG = "tracearr"


def _instances() -> list:
    """Configured Tracearr chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "tracearr_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.TRACEARR_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one Tracearr host's fleet counts (active streams + 7d violations
    + 30d plays). A host that's down / unreachable / has no token skips the write
    (no phantom 0 row); a code bug also skips."""
    try:
        from logic.apps import tracearr as _tracearr  # noqa: PLC0415
        data = await _tracearr.fetch_data(host_row, chip, host_id=host_id,
                                          service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[tracearr_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[tracearr_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("active_streams")),
           safe_int(data.get("recent_violations")),
           safe_int(data.get("total_sessions")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO tracearr_samples "
                "(ts, host_id, service_idx, active_streams, recent_violations, "
                "total_sessions) VALUES (?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[tracearr_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    Tracearr host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="tracearr_sampler",
        prune_table="tracearr_samples",
        history_days_tunable=_Tunable.TRACEARR_HISTORY_DAYS)


async def tracearr_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Tracearr chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "tracearr_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Violation + concurrency trend for one Tracearr chip. Returns ``{days,
    samples, series_streams, series_violations, peak_streams, today_peak,
    latest_streams, latest_violations, latest_sessions, violation_rate,
    week_change_violations}`` where ``series_streams`` is the per-day MAX active
    streams (concurrency) and ``series_violations`` is the per-day MAX rolling
    7d violation count, both oldest-first, days WITH data only.
    ``violation_rate`` is the latest violations per 100 plays
    (``recent_violations / total_sessions * 100``, 1 dp); ``week_change_
    violations`` is the latest violation count minus the count ~7 days ago
    (positive ⇒ abuse trending up). Zeroed shape when no samples — never
    raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.TRACEARR_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "peak_streams": 0,
                 "today_peak": 0, "latest_streams": 0, "latest_violations": 0,
                 "latest_sessions": 0, "violation_rate": 0.0,
                 "week_change_violations": 0,
                 "series_streams": [], "series_violations": []}
    if not host_id:
        return out
    now = int(time.time())
    cutoff = now - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, active_streams, recent_violations, total_sessions "
                "FROM tracearr_samples WHERE host_id=? AND service_idx=? AND ts >= ? "
                "ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[tracearr_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["latest_streams"] = safe_int(rows[-1]["active_streams"])
    out["latest_violations"] = safe_int(rows[-1]["recent_violations"])
    out["latest_sessions"] = safe_int(rows[-1]["total_sessions"])
    out["peak_streams"] = max(safe_int(r["active_streams"]) for r in rows)
    day_ago = now - 86400
    today_vals = [safe_int(r["active_streams"]) for r in rows if int(r["ts"]) >= day_ago]
    out["today_peak"] = max(today_vals) if today_vals else 0
    # Violations-per-100-plays rate from the latest sample (the rolling 7d
    # violations over the 30d play count — a normalised "how abusive is the
    # traffic" figure, distinct from the bare count).
    sessions = out["latest_sessions"]
    if sessions > 0:
        out["violation_rate"] = round(out["latest_violations"] * 100.0 / sessions, 1)
    # Week-over-week violation delta: latest minus the sample closest to 7d ago.
    week_ago = now - 7 * 86400
    prior = [r for r in rows if int(r["ts"]) <= week_ago]
    if prior:
        out["week_change_violations"] = out["latest_violations"] - safe_int(prior[-1]["recent_violations"])
    # Per-day MAX streams (peak concurrency) + per-day MAX violations.
    day_max_s: dict = defaultdict(int)
    day_max_v: dict = defaultdict(int)
    for r in rows:
        day = int(r["ts"]) // 86400
        day_max_s[day] = max(day_max_s[day], safe_int(r["active_streams"]))
        day_max_v[day] = max(day_max_v[day], safe_int(r["recent_violations"]))
    ordered = sorted(day_max_s)
    series_streams = [day_max_s[d] for d in ordered]
    series_violations = [day_max_v[d] for d in ordered]
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_streams = [series_streams[i] for i in idx]
        series_violations = [series_violations[i] for i in idx]
    out["series_streams"] = series_streams
    out["series_violations"] = series_violations
    return out
