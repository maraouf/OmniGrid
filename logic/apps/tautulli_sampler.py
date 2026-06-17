"""Tautulli concurrent-stream retention sampler.

Tautulli IS a statistics database for a Plex server, but its history has to be
round-tripped on every card render. This lifespan sampler snapshots each
configured Tautulli chip's concurrent-stream count (+ transcodes + bandwidth)
per tick into ``tautulli_samples`` so the card can draw a streams-over-time
sparkline plus a "peak N concurrent streams today" stat WITHOUT re-querying
Tautulli's history each render — an OmniGrid-owned trend that reads consistently
with the Plex / Emby concurrent-stream samplers (the one media app that had none).

The columns are point-in-time GAUGES. Streams are bursty, so the daily roll-up
for the stream count is the day's MAX (peak concurrency), while bandwidth rolls
up as a daily mean. Cadence ``tuning_tautulli_sample_interval_seconds`` (0 =
inherit the global stats interval, default 300s — finer than the slow samplers
to catch peaks); retention ``tuning_tautulli_history_days`` (default 30).
Dormant-cheap when no Tautulli chip is configured. Generic tick / instance-enum /
cadence resolve delegate to the shared ``logic/apps/_common.py`` sampler
scaffolding; the per-app probe-write + trend math are Tautulli-specific.
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

_SLUG = "tautulli"


def _instances() -> list:
    """Configured Tautulli chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "tautulli_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.TAUTULLI_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one Tautulli host's concurrent-stream count (+ transcodes +
    bandwidth). A host that's down / unreachable / has no api_key skips the write
    (no phantom 0 row); a code bug also skips."""
    try:
        from logic.apps import tautulli as _tautulli  # noqa: PLC0415
        data = await _tautulli.fetch_data(host_row, chip, host_id=host_id,
                                          service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[tautulli_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[tautulli_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("streams")), safe_int(data.get("transcodes")),
           safe_int(data.get("bandwidth_kbps")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO tautulli_samples "
                "(ts, host_id, service_idx, streams, transcodes, "
                "bandwidth_kbps) VALUES (?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[tautulli_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    Tautulli host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="tautulli_sampler",
        prune_table="tautulli_samples",
        history_days_tunable=_Tunable.TAUTULLI_HISTORY_DAYS)


async def tautulli_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Tautulli chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "tautulli_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Concurrent-stream trend for one Tautulli chip. Returns ``{days, samples,
    peak_streams, today_peak, latest_streams, series_streams, series_bandwidth}``
    where ``series_streams`` is the per-day MAX concurrent streams (streams are
    bursty — a peak matters more than a mean) and ``series_bandwidth`` is the
    per-day MEAN bandwidth (kbps); both oldest-first, days WITH data only.
    ``peak_streams`` is the window max; ``today_peak`` is the max over the last
    24h. Zeroed shape when no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.TAUTULLI_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "peak_streams": 0,
                 "today_peak": 0, "latest_streams": 0,
                 "series_streams": [], "series_bandwidth": []}
    if not host_id:
        return out
    now = int(time.time())
    cutoff = now - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, streams, bandwidth_kbps FROM tautulli_samples "
                "WHERE host_id=? AND service_idx=? AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[tautulli_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["latest_streams"] = safe_int(rows[-1]["streams"])
    out["peak_streams"] = max(safe_int(r["streams"]) for r in rows)
    day_ago = now - 86400
    today_vals = [safe_int(r["streams"]) for r in rows if int(r["ts"]) >= day_ago]
    out["today_peak"] = max(today_vals) if today_vals else 0
    # Per-day MAX streams (peak concurrency) + per-day MEAN bandwidth.
    day_max: dict = defaultdict(int)
    bw_sum: dict = defaultdict(int)
    bw_cnt: dict = defaultdict(int)
    for r in rows:
        day = int(r["ts"]) // 86400
        day_max[day] = max(day_max[day], safe_int(r["streams"]))
        bw_sum[day] += safe_int(r["bandwidth_kbps"])
        bw_cnt[day] += 1
    ordered = sorted(day_max)
    series_streams = [day_max[d] for d in ordered]
    series_bandwidth = [round(bw_sum[d] / max(1, bw_cnt[d])) for d in ordered]
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_streams = [series_streams[i] for i in idx]
        series_bandwidth = [series_bandwidth[i] for i in idx]
    out["series_streams"] = series_streams
    out["series_bandwidth"] = series_bandwidth
    return out
