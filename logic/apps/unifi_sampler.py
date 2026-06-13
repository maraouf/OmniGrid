"""UniFi client-occupancy retention sampler.

UniFi's Integration API is CURRENT-STATE-only — it exposes the connected-client
count right now but keeps no client-count history to chart. This lifespan
sampler snapshots each configured UniFi chip's connected-client count (+ the
wireless split + devices-online) per tick into ``unifi_samples`` so the card can
draw a "clients over 24h" occupancy line + a "peak N clients" stat.

The columns are point-in-time GAUGES, so the daily roll-up is an AVERAGE.
Cadence ``tuning_unifi_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_unifi_history_days`` (default 30). Dormant-cheap
when no UniFi chip is configured. Generic tick / instance-enum / cadence resolve
delegate to the shared ``logic/apps/_common.py`` sampler scaffolding; the per-app
probe-write + trend math are UniFi-specific.
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

_SLUG = "unifi"


def _instances() -> list:
    """Configured UniFi chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "unifi_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.UNIFI_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one UniFi host's connected-client count (+ wireless split +
    devices-online). A host that's down / unreachable / has no key skips the
    write (no phantom 0 row); a code bug also skips."""
    try:
        from logic.apps import unifi as _unifi  # noqa: PLC0415
        data = await _unifi.fetch_data(host_row, chip, host_id=host_id,
                                       service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[unifi_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[unifi_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("clients")), safe_int(data.get("clients_wireless")),
           safe_int(data.get("devices_online")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO unifi_samples "
                "(ts, host_id, service_idx, clients, clients_wireless, "
                "devices_online) VALUES (?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[unifi_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    UniFi host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="unifi_sampler",
        prune_table="unifi_samples",
        history_days_tunable=_Tunable.UNIFI_HISTORY_DAYS)


async def unifi_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no UniFi chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "unifi_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Client-occupancy trend for one UniFi chip. Returns ``{days, samples,
    peak_clients, latest_clients, latest_wireless, series_clients,
    series_wireless}`` where each ``series_*`` is up to ``max_points`` daily-
    AVERAGE points (oldest-first, days WITH data only — the gauges are current
    counts, so a 0-fill day would be a false reading). Zeroed shape when no
    samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.UNIFI_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "peak_clients": 0,
                 "latest_clients": 0, "latest_wireless": 0,
                 "series_clients": [], "series_wireless": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, clients, clients_wireless FROM unifi_samples "
                "WHERE host_id=? AND service_idx=? AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[unifi_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["peak_clients"] = max(safe_int(r["clients"]) for r in rows)
    out["latest_clients"] = safe_int(rows[-1]["clients"])
    out["latest_wireless"] = safe_int(rows[-1]["clients_wireless"])
    # Daily-AVERAGE per metric (gauge → mean per day), days with data only.
    cli_sum: dict = defaultdict(int)
    wl_sum: dict = defaultdict(int)
    cnt: dict = defaultdict(int)
    for r in rows:
        day = int(r["ts"]) // 86400
        cli_sum[day] += safe_int(r["clients"])
        wl_sum[day] += safe_int(r["clients_wireless"])
        cnt[day] += 1
    ordered = sorted(cnt)
    series_clients = [round(cli_sum[d] / max(1, cnt[d])) for d in ordered]
    series_wireless = [round(wl_sum[d] / max(1, cnt[d])) for d in ordered]
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_clients = [series_clients[i] for i in idx]
        series_wireless = [series_wireless[i] for i in idx]
    out["series_clients"] = series_clients
    out["series_wireless"] = series_wireless
    return out
