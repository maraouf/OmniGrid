"""Grafana meta-monitor retention sampler.

Grafana is the chart tool, so an OmniGrid sparkline ON the Grafana card is ironic
— but it surfaces META signals Grafana doesn't chart about ITSELF: the
firing-alert count over time (a monitor of the monitor — "alerts have been firing
more this week") + dashboard-count growth. This lifespan sampler snapshots each
configured Grafana chip per tick into ``grafana_samples``.

``firing_alerts`` / ``datasources_unhealthy`` are gauges read as each day's MAX;
``dashboards`` is a slow-growing count read as each day's LAST. Cadence
``tuning_grafana_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_grafana_history_days`` (default 90). Dormant-cheap
when no Grafana chip is configured. Generic tick / instance-enum / cadence
resolve delegate to the shared ``logic/apps/_common.py`` sampler scaffolding; the
per-app probe-write + trend math are Grafana-specific.
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

_SLUG = "grafana"


def _instances() -> list:
    """Configured Grafana chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "grafana_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.GRAFANA_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one Grafana host's firing-alert / dashboard / unhealthy-datasource
    counts. A host that's down / unreachable skips the write (no phantom 0 row);
    a code bug also skips. ``firing_alerts`` is NULL-coalesced to 0 (the chip
    stores None when Grafana alerting is unavailable)."""
    try:
        from logic.apps import grafana as _grafana  # noqa: PLC0415
        data = await _grafana.fetch_data(host_row, chip, host_id=host_id,
                                         service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[grafana_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[grafana_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    firing = data.get("alerts_firing")
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(firing) if firing is not None else 0,
           safe_int(data.get("dashboards")), safe_int(data.get("datasources_unhealthy")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO grafana_samples "
                "(ts, host_id, service_idx, firing_alerts, dashboards, "
                "datasources_unhealthy) VALUES (?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[grafana_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    Grafana host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="grafana_sampler",
        prune_table="grafana_samples",
        history_days_tunable=_Tunable.GRAFANA_HISTORY_DAYS)


async def grafana_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Grafana chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "grafana_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Meta-monitor trend for one Grafana chip. Returns ``{days, samples,
    latest_firing, peak_firing, latest_dashboards, series_firing,
    series_dashboards}``.

    ``series_firing`` is each day's MAX firing-alert count (the "alerts firing
    more this week" meta-monitor); ``series_dashboards`` is each day's LAST
    dashboard count (a growth line). Each series is up to ``max_points`` points
    (oldest-first, days WITH data only). Zeroed shape when no samples yet —
    never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.GRAFANA_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "latest_firing": 0,
                 "peak_firing": 0, "latest_dashboards": 0,
                 "series_firing": [], "series_dashboards": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, firing_alerts, dashboards "
                "FROM grafana_samples WHERE host_id=? AND service_idx=? AND ts >= ? "
                "ORDER BY ts ASC", (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[grafana_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["latest_firing"] = safe_int(rows[-1]["firing_alerts"])
    out["peak_firing"] = max(safe_int(r["firing_alerts"]) for r in rows)
    out["latest_dashboards"] = safe_int(rows[-1]["dashboards"])
    # Per-day roll-up: MAX firing (a gauge → the day's peak is the signal) +
    # LAST dashboards (monotonic-ish → latest sample of the day).
    fire_max: dict = defaultdict(int)
    dash_last: dict = {}
    for r in rows:
        day = int(r["ts"]) // 86400
        fire_max[day] = max(fire_max[day], safe_int(r["firing_alerts"]))
        dash_last[day] = safe_int(r["dashboards"])  # rows are ts ASC
    ordered = sorted(dash_last)
    series_firing = [fire_max[d] for d in ordered]
    series_dashboards = [dash_last[d] for d in ordered]
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_firing = [series_firing[i] for i in idx]
        series_dashboards = [series_dashboards[i] for i in idx]
    out["series_firing"] = series_firing
    out["series_dashboards"] = series_dashboards
    return out
