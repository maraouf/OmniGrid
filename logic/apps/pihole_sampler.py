"""Pi-hole blocked-% history sampler — lifespan-managed.

Pi-hole's FTL keeps its own long query DB, but the today-counters reset on a
restart, so a cross-restart "blocked % over 30 days" view isn't reliable from
the live counters. This sampler snapshots every configured Pi-hole host's
current ``queries_today`` / ``blocked_today`` / ``num_clients`` counters per
tick into ``pihole_samples``.

``trend_summary`` derives the same fleet blocked-% daily trend as AdGuard: per
(day, host) daily-MAX of the cumulative today-counters, summed across the fleet,
then ``blocked/queries*100`` per day. This is a FLEET app, so the trend
aggregates across EVERY Pi-hole instance.

Cadence ``tuning_pihole_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_pihole_history_days`` (default 90). Dormant-cheap
when no Pi-hole chip is configured.
"""
from __future__ import annotations

from logic.apps._common import (
    fleet_blocker_trend_summary, probe_blocker_sample, resolve_sample_interval,
    run_sampler_tick, sampler_instances)
from logic.sampler_loop import lifespan_sampler_loop
from logic.tuning import Tunable as _Tunable

_SLUG = "pihole"


def _instances() -> list:
    """Configured Pi-hole chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "pihole_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global
    stats interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.PIHOLE_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one Pi-hole host's queries/blocked/clients counters (delegates
    to the shared fleet-blocker probe-and-write helper)."""
    from logic.apps import pihole as _ph  # noqa: PLC0415
    await probe_blocker_sample(_ph.fetch_data, "pihole_samples", host_id,
                               int(service_idx), host_row, chip,
                               "pihole_sampler")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    Pi-hole host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="pihole_sampler",
        prune_table="pihole_samples",
        history_days_tunable=_Tunable.PIHOLE_HISTORY_DAYS)


async def pihole_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Pi-hole chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    await lifespan_sampler_loop(
        "pihole_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(days: int = 0, *, max_points: int = 90) -> dict:
    """Fleet-wide daily blocked-% trend from the local history (delegates to the
    shared ``fleet_blocker_trend_summary`` helper — only the table + retention
    tunable differ from AdGuard)."""
    return fleet_blocker_trend_summary("pihole_samples",
                                       _Tunable.PIHOLE_HISTORY_DAYS, days,
                                       max_points=max_points)
