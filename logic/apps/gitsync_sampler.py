"""GitSync Connector mirror retention sampler.

GitSync surfaces only CURRENT totals (synced refs, mapping counts, alert
counters) — it charts no history, so a self-host glance can't answer "how much
has been mirrored over time?" or "did a pair start failing 3 days ago?". This
lifespan sampler snapshots each configured GitSync chip per tick into
``gitsync_samples`` so the card can draw:

  * a mappings-growth line ("GitSync mirrored 240 commits + 12 releases this
    week" — the cumulative total's daily-LAST value + per-week throughput), and
  * an alert-error trend (each day's MAX — spot a degrading mirror).

Cadence ``tuning_gitsync_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_gitsync_history_days`` (default 90). Dormant-cheap
when no GitSync chip is configured. Generic tick / instance-enum / cadence
resolve delegate to the shared ``logic/apps/_common.py`` sampler scaffolding;
the per-app probe-write + trend math are GitSync-specific.
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

_SLUG = "gitsync"


def _instances() -> list:
    """Configured GitSync chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "gitsync_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.GITSYNC_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one GitSync host's synced-refs + total-mappings + alert-error +
    paused counts. A host that's down / unreachable skips the write (no phantom
    0 row); a code bug also skips."""
    try:
        from logic.apps import gitsync as _gitsync  # noqa: PLC0415
        data = await _gitsync.fetch_data(host_row, chip, host_id=host_id,
                                         service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[gitsync_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[gitsync_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    total_mappings = (safe_int(data.get("issue_mappings"))
                      + safe_int(data.get("commit_mappings"))
                      + safe_int(data.get("release_mappings"))
                      + safe_int(data.get("comment_mappings")))
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("synced_refs")), total_mappings,
           safe_int(data.get("alerts_error")), safe_int(data.get("paused")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO gitsync_samples "
                "(ts, host_id, service_idx, synced_refs, total_mappings, "
                "alerts_error, paused) VALUES (?,?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[gitsync_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    GitSync host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="gitsync_sampler",
        prune_table="gitsync_samples",
        history_days_tunable=_Tunable.GITSYNC_HISTORY_DAYS)


async def gitsync_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no GitSync chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "gitsync_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Mirror trend for one GitSync chip. Returns ``{days, samples,
    latest_mappings, week_throughput, peak_alerts, series_mappings,
    series_alerts}``.

    ``series_mappings`` is each day's LAST cumulative total_mappings (the growth
    line); ``series_alerts`` is each day's MAX alerts_error (the health trend).
    ``week_throughput`` is the mappings created across the window (latest minus
    earliest cumulative, clamped at 0 so a connector reset can't show negative).
    Each series is up to ``max_points`` points (oldest-first, days WITH data
    only). Zeroed shape when no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.GITSYNC_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "latest_mappings": 0,
                 "week_throughput": 0, "peak_alerts": 0,
                 "series_mappings": [], "series_alerts": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, total_mappings, alerts_error "
                "FROM gitsync_samples WHERE host_id=? AND service_idx=? AND ts >= ? "
                "ORDER BY ts ASC", (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[gitsync_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["latest_mappings"] = safe_int(rows[-1]["total_mappings"])
    out["peak_alerts"] = max(safe_int(r["alerts_error"]) for r in rows)
    # Per-day roll-up: LAST cumulative mappings (monotonic → take the latest
    # sample of the day) + MAX alerts_error (a gauge).
    map_last: dict = {}
    alert_max: dict = defaultdict(int)
    for r in rows:
        day = int(r["ts"]) // 86400
        map_last[day] = safe_int(r["total_mappings"])  # rows are ts ASC
        alert_max[day] = max(alert_max[day], safe_int(r["alerts_error"]))
    ordered = sorted(map_last)
    series_mappings = [map_last[d] for d in ordered]
    series_alerts = [alert_max[d] for d in ordered]
    # Window throughput = newest cumulative minus oldest (clamped at 0 so a
    # connector DB reset / re-init doesn't show a negative).
    out["week_throughput"] = max(0, series_mappings[-1] - series_mappings[0])
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_mappings = [series_mappings[i] for i in idx]
        series_alerts = [series_alerts[i] for i in idx]
    out["series_mappings"] = series_mappings
    out["series_alerts"] = series_alerts
    return out
