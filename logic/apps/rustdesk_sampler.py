"""RustDesk Server (Pro) usage sampler — lifespan-managed.

RustDesk Server Pro exposes only the CURRENT peer state (``/api/peers``); there
is no historical / online-count API. This sampler records each configured
RustDesk chip's live registered-device + online-device + user count per tick
into ``rustdesk_samples`` so the card can show a "peak concurrent devices"
online-peers trend + fleet-growth over the retention window.

One row per ``(host_id, service_idx, tick)``: ``devices`` (registered peers),
``devices_online`` (online now), ``users`` (console accounts). Cadence
``tuning_rustdesk_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_rustdesk_history_days`` (default 30).
Dormant-cheap when no RustDesk chip is configured — keeps ticking so the
operator can pin one without a restart.

A login that fails / a server that's down records NOTHING (skip — unlike
FlareSolverr's ``ready`` downtime flag, a zeroed device count here would be a
misleading "fleet vanished" point; the trend should simply have a gap).
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from logic import tuning as _tuning
from logic.apps._common import resolve_sample_interval, sampler_instances
from logic.db import db_conn, prune_rows_older_than
from logic.sampler_loop import lifespan_sampler_loop
from logic.tuning import Tunable as _Tunable

_SLUG = "rustdesk"


def _instances() -> list:
    """Configured RustDesk chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "rustdesk_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global
    stats interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.RUSTDESK_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Record one chip's sample: registered + online device count + user count.
    A login failure / unreachable server SKIPS the write (a zeroed device count
    would be a misleading 'fleet vanished' point — the trend should just have a
    gap)."""
    try:
        from logic.apps import rustdesk as _rd  # noqa: PLC0415
        data = await _rd.fetch_data(host_row, chip, host_id=host_id,
                                    service_idx=int(service_idx), force=True)
        devices = int(data.get("devices") or 0)
        online = int(data.get("devices_online") or 0)
        users = int(data.get("users") or 0)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        # Down / unreachable / bad creds / OSS server — skip (no downtime row).
        print(f"[rustdesk_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[rustdesk_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO rustdesk_samples "
                "(ts, host_id, service_idx, devices, devices_online, users) "
                "VALUES (?,?,?,?,?,?)",
                (int(time.time()), host_id, int(service_idx),
                 int(devices), int(online), int(users)),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[rustdesk_sampler] write {host_id}#{service_idx} failed: {e}")


def _prune_old_samples() -> int:
    """Delete rows older than the retention window. Returns the deleted-row
    count (chunked DELETE via the shared helper)."""
    days = _tuning.tuning_int(_Tunable.RUSTDESK_HISTORY_DAYS)
    cutoff = int(time.time()) - days * 86400
    return prune_rows_older_than("rustdesk_samples", cutoff)


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body: probe every configured RustDesk chip in parallel, then
    run the hourly retention prune (offloaded to a worker thread)."""
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
        n = await prune_with_metrics("rustdesk_sampler", _prune_old_samples)
        if n:
            days = _tuning.tuning_int(_Tunable.RUSTDESK_HISTORY_DAYS)
            print(f"[rustdesk_sampler] pruned {n} rows older than {days}d")


async def rustdesk_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no RustDesk chip is configured (keeps ticking so a
    runtime pin takes effect without a restart)."""
    await lifespan_sampler_loop(
        "rustdesk_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def usage_summary(host_id: str, service_idx: int,
                  days: int = 30, *, max_points: int = 30) -> dict:
    """Online-peers usage trend for one chip. Returns
    ``{series, peak, avg, active_days, samples, current, days}`` where
    ``series`` is up to ``days`` daily-MAX online-device points (oldest-first,
    missing days filled with 0) for a sparkline. Zeroed shape when no samples
    yet — never raises."""
    out: dict = {"series": [], "peak": 0, "avg": 0.0, "active_days": 0,
                 "samples": 0, "current": 0, "days": int(days)}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(days) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, devices_online FROM rustdesk_samples "
                "WHERE host_id=? AND service_idx=? AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[rustdesk_sampler] usage_summary({host_id}#{service_idx}) "
              f"failed: {e}")
        return out
    if not rows:
        return out
    vals = [int(r["devices_online"]) for r in rows]
    out["samples"] = len(vals)
    out["peak"] = max(vals)
    out["avg"] = round(sum(vals) / len(vals), 1)
    out["current"] = vals[-1]
    # Daily-max bucket (day index = ts // 86400) → distinct active days +
    # a contiguous fixed-length series (0-filled) for a clean sparkline.
    day_max: dict = defaultdict(int)
    for r in rows:
        d = int(r["ts"]) // 86400
        v = int(r["devices_online"])
        if v > day_max[d]:
            day_max[d] = v
    out["active_days"] = sum(1 for v in day_max.values() if v > 0)
    today = int(time.time()) // 86400
    span = max(1, min(int(days), int(max_points)))
    start_day = today - span + 1
    out["series"] = [int(day_max.get(d, 0)) for d in range(start_day, today + 1)]
    return out
