"""Kavita library-growth retention sampler.

Kavita exposes the CURRENT library totals (series / volume / chapter counts +
total size) but no time series. This lifespan sampler snapshots each configured
Kavita chip per tick into ``kavita_samples`` so the card can draw a library-
growth line — "your library grew by N series this month" — plus a total-size
growth trend.

The columns are CUMULATIVE running totals (a library only grows), so the daily
roll-up is each day's LAST value. Cadence ``tuning_kavita_sample_interval_seconds``
(0 = inherit the global stats interval); retention ``tuning_kavita_history_days``
(default 365 — the growth story is most satisfying over a long window). Dormant-
cheap when no Kavita chip is configured. Generic tick / instance-enum / cadence
resolve delegate to the shared ``logic/apps/_common.py`` sampler scaffolding; the
per-app probe-write + trend math are Kavita-specific.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import (
    resolve_sample_interval, run_sampler_tick, sampler_instances)
from logic.coerce import safe_int
from logic.db import db_conn
from logic.tuning import Tunable as _Tunable

_SLUG = "kavita"


def _instances() -> list:
    """Configured Kavita chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "kavita_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.KAVITA_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one Kavita host's library totals. A host that's down / has no
    api_key skips the write (no phantom 0 row); a code bug also skips. The
    server-stats are admin-only — when the key isn't an admin's the counts are 0,
    so we SKIP the write (a 0 row would read as a false library wipe)."""
    try:
        from logic.apps import kavita as _kavita  # noqa: PLC0415
        data = await _kavita.fetch_data(host_row, chip, host_id=host_id,
                                        service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[kavita_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[kavita_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    series = safe_int(data.get("series_count"))
    if series <= 0:
        # Non-admin key (server-stats came back 0) — nothing meaningful to trend.
        return
    row = (int(time.time()), host_id, int(service_idx), series,
           safe_int(data.get("volume_count")), safe_int(data.get("chapter_count")),
           safe_int(data.get("total_size")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO kavita_samples "
                "(ts, host_id, service_idx, series_count, volume_count, "
                "chapter_count, total_size) VALUES (?,?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[kavita_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    Kavita host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="kavita_sampler",
        prune_table="kavita_samples",
        history_days_tunable=_Tunable.KAVITA_HISTORY_DAYS)


async def kavita_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Kavita chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "kavita_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Library-growth trend for one Kavita chip. Returns ``{days, samples,
    latest_series, latest_size, series_added, series_series, size_series}`` where
    ``series_series`` / ``size_series`` are each day's LAST cumulative value
    (oldest-first, days WITH data only) and ``series_added`` is the net series
    growth across the window. Zeroed shape when no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.KAVITA_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "latest_series": 0,
                 "latest_size": 0, "series_added": 0, "series_series": [],
                 "size_series": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, series_count, total_size FROM kavita_samples "
                "WHERE host_id=? AND service_idx=? AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[kavita_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["latest_series"] = safe_int(rows[-1]["series_count"])
    out["latest_size"] = safe_int(rows[-1]["total_size"])
    # Per-day LAST cumulative value (monotonic → take the latest sample of day).
    series_last: dict = {}
    size_last: dict = {}
    for r in rows:
        day = int(r["ts"]) // 86400
        series_last[day] = safe_int(r["series_count"])  # rows are ts ASC
        size_last[day] = safe_int(r["total_size"])
    ordered = sorted(series_last)
    series_series = [series_last[d] for d in ordered]
    size_series = [size_last[d] for d in ordered]
    out["series_added"] = max(0, series_series[-1] - series_series[0])
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_series = [series_series[i] for i in idx]
        size_series = [size_series[i] for i in idx]
    out["series_series"] = series_series
    out["size_series"] = size_series
    return out
