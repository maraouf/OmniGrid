"""Seerr (Overseerr / Jellyseerr) request-backlog history sampler.

Seerr's own dashboard shows the CURRENT request-queue counts only, so a
"pending has been stuck at 5 for a week" / "processing spiked when I shared the
server" view isn't available from the live API. This lifespan sampler snapshots
each configured Seerr chip's queue gauges (pending / processing / available /
open issues) per tick into ``seerr_samples``.

``trend_summary`` rolls them into a daily-AVG pending-backlog sparkline (+ peak /
latest) so the card can show the request backlog over time. The columns are
GAUGES (current depth), not cumulative counters, so the daily roll-up is an
AVERAGE (not a max-before-reset like the DNS-blocker samplers).

Cadence ``tuning_seerr_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_seerr_history_days`` (default 90). Dormant-cheap
when no Seerr chip is configured. Generic tick / instance-enum / cadence resolve
delegate to the shared `logic/apps/_common.py` sampler scaffolding; only the
per-app probe-write + trend math are Seerr-specific.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import (
    resolve_sample_interval, run_sampler_tick, sampler_instances)
from logic.db import db_conn
from logic.tuning import Tunable as _Tunable

_SLUG = "seerr"


def _instances() -> list:
    """Configured Seerr chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "seerr_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global
    stats interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.SEERR_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one Seerr host's queue gauges (pending / processing / available
    / open issues). A host that's down / unreachable skips the write (no phantom
    0 row); a code bug also skips. The fetch-and-write shape is shared with the
    other per-app samplers by design — only the columns differ (gauges, not the
    blocker queries/blocked/clients), so it can't reuse probe_blocker_sample."""
    try:
        from logic.apps import seerr as _seerr  # noqa: PLC0415
        data = await _seerr.fetch_data(host_row, chip, host_id=host_id,
                                       service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[seerr_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[seerr_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    row = (int(time.time()), host_id, int(service_idx),
           int(data.get("pending") or 0), int(data.get("processing") or 0),
           int(data.get("available_count") or 0), int(data.get("issues_open") or 0))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO seerr_samples "
                "(ts, host_id, service_idx, pending, processing, available, "
                "issues_open) VALUES (?,?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[seerr_sampler] write {host_id}#{service_idx} failed: {e}")


async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    Seerr host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="seerr_sampler",
        prune_table="seerr_samples",
        history_days_tunable=_Tunable.SEERR_HISTORY_DAYS)


async def seerr_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Seerr chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "seerr_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Request-backlog trend for one Seerr chip. Returns ``{days, samples,
    peak_pending, latest_pending, series}`` where ``series`` is up to
    ``max_points`` daily-AVERAGE pending-depth points (oldest-first, days WITH
    data only — pending is a gauge, so a 0-fill day would be a false "drained"
    reading). Zeroed shape when no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.SEERR_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "peak_pending": 0,
                 "latest_pending": 0, "series": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, pending FROM seerr_samples "
                "WHERE host_id=? AND service_idx=? AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[seerr_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    pend = [int(r["pending"] or 0) for r in rows]
    out["samples"] = len(rows)
    out["peak_pending"] = max(pend)
    out["latest_pending"] = pend[-1]
    # Daily-AVERAGE pending depth (gauge → mean per day), days with data only.
    day_sum: dict = defaultdict(int)
    day_cnt: dict = defaultdict(int)
    for r in rows:
        d = int(r["ts"]) // 86400
        day_sum[d] += int(r["pending"] or 0)
        day_cnt[d] += 1
    series = [round(day_sum[d] / max(1, day_cnt[d]), 1) for d in sorted(day_sum)]
    if len(series) > max_points:
        stride = len(series) / float(max_points)
        series = [series[int(i * stride)] for i in range(max_points)]
    out["series"] = series
    return out
