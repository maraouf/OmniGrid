"""Fing online-device history sampler — lifespan-managed.

Fing's Local API is current-state-only (no history), so this sampler records
each configured Fing chip's device totals + online count per tick into
``fing_samples``. The expanded card draws:

* an **online-device count trend** — a daily-MAX online-count sparkline (network
  occupancy: "how many devices are typically on the network through the day");
* a **peak / current** occupancy readout + a **new-device** signal (the headline
  security flavour — a device first-seen recently).

One row per ``(host_id, service_idx, tick)``. Cadence
``tuning_fing_sample_interval_seconds`` (0 = inherit the global stats interval);
retention ``tuning_fing_history_days`` (default 90). Dormant-cheap when no Fing
chip is configured (keeps ticking so a runtime pin takes effect without a
restart).
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import resolve_sample_interval, sampler_instances
from logic.db import db_conn, prune_rows_older_than
from logic.sampler_loop import lifespan_sampler_loop
from logic.tuning import Tunable as _Tunable

_SLUG = "fing"


def _instances() -> list:
    """Configured Fing chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "fing_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.FING_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Record one chip's sample: total / online device counts + the new-device
    count. A chip that's down / unreachable / mis-configured skips the write (a
    transient outage shouldn't write a phantom zero-occupancy row). A code bug
    (unexpected exception) also skips."""
    try:
        from logic.apps import fing as _fing  # noqa: PLC0415
        data = await _fing.fetch_data(host_row, chip, host_id=host_id,
                                      service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[fing_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[fing_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    total = int(data.get("devices_total") or 0)
    online = int(data.get("devices_online") or 0)
    new_devices = int(data.get("new_devices") or 0)
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO fing_samples "
                "(ts, host_id, service_idx, devices_total, devices_online, "
                "new_devices) VALUES (?,?,?,?,?,?)",
                (int(time.time()), host_id, int(service_idx),
                 total, online, new_devices),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[fing_sampler] write {host_id}#{service_idx} failed: {e}")


def _prune_old_samples() -> int:
    """Delete rows older than the retention window. Returns the deleted-row
    count (chunked DELETE via the shared helper)."""
    days = _tuning.tuning_int(_Tunable.FING_HISTORY_DAYS)
    cutoff = int(time.time()) - days * 86400
    return prune_rows_older_than("fing_samples", cutoff)


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body: probe every configured Fing chip in parallel, then run the
    hourly retention prune (offloaded to a worker thread)."""
    instances = _instances()
    if instances:
        await asyncio.gather(
            *(_probe_one(hid, idx, h, chip)
              for (hid, idx, h, chip) in instances),
            return_exceptions=True,
        )
    interval = _resolve_interval()
    if tick % max(1, 3600 // max(1, interval)) == 0:
        from logic.sampler_metrics import prune_with_metrics  # noqa: PLC0415
        n = await prune_with_metrics("fing_sampler", _prune_old_samples)
        if n:
            days = _tuning.tuning_int(_Tunable.FING_HISTORY_DAYS)
            print(f"[fing_sampler] pruned {n} rows older than {days}d")


async def fing_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Fing chip is configured (keeps ticking so a runtime pin
    takes effect without a restart)."""
    await lifespan_sampler_loop(
        "fing_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def history_summary(host_id: str, service_idx: int,
                    days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Online-device occupancy trend for one Fing chip. Returns ``{days,
    samples, online_series, peak_online, current_online, current_total,
    new_event_days}`` where:

    * ``online_series`` is up to ``max_points`` daily-MAX online-device counts
      (oldest-first, missing days filled with 0) for a sparkline.
    * ``peak_online`` is the highest online count across the window.
    * ``new_event_days`` is the count of distinct days a NEW device appeared
      (any row with ``new_devices > 0``).

    Zeroed shape when no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.FING_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "online_series": [],
                 "peak_online": 0, "current_online": 0, "current_total": 0,
                 "new_event_days": 0}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, devices_total, devices_online, new_devices "
                "FROM fing_samples WHERE host_id=? AND service_idx=? AND ts >= ? "
                "ORDER BY ts ASC", (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[fing_sampler] history_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["current_online"] = int(rows[-1]["devices_online"] or 0)
    out["current_total"] = int(rows[-1]["devices_total"] or 0)
    # Online-count daily-MAX bucket → a contiguous fixed-length 0-filled series.
    day_max: dict = defaultdict(int)
    new_days: set = set()
    for r in rows:
        d = int(r["ts"]) // 86400
        on = int(r["devices_online"] or 0)
        if on > day_max[d]:
            day_max[d] = on
        if int(r["new_devices"] or 0) > 0:
            new_days.add(d)
    out["peak_online"] = max(day_max.values()) if day_max else 0
    out["new_event_days"] = len(new_days)
    today = int(time.time()) // 86400
    span = max(1, min(int(win), int(max_points)))
    start_day = today - span + 1
    out["online_series"] = [int(day_max.get(d, 0)) for d in range(start_day, today + 1)]
    return out
