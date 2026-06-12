"""AdGuard Home blocked-% history sampler — lifespan-managed.

AdGuard keeps only a short rolling stats window (operator-configurable, often
24h-90d) AND its today-counters reset on a restart, so a "blocked % is trending
up over 30 days" view isn't available from the live API. This sampler snapshots
every configured AdGuard host's current ``queries_today`` / ``blocked_today`` /
``num_clients`` counters per tick into ``adguard_samples``.

``trend_summary`` buckets the samples by day, takes the daily MAX per host (the
cumulative today-counter peaks just before the daily reset ≈ that day's total),
sums across the fleet per day, and derives a daily blocked-% trend that outlives
AdGuard's own retention. This is a FLEET app, so the trend aggregates across
EVERY AdGuard instance.

Cadence ``tuning_adguard_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_adguard_history_days`` (default 90). Dormant-cheap
when no AdGuard chip is configured.
"""
from __future__ import annotations

from logic.apps._common import (
    fleet_blocker_trend_summary, probe_blocker_sample, resolve_sample_interval,
    run_sampler_tick, sampler_instances)
from logic.sampler_loop import lifespan_sampler_loop
from logic.tuning import Tunable as _Tunable

_SLUG = "adguard-home"


def _instances() -> list:
    """Configured AdGuard chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "adguard_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global
    stats interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.ADGUARD_SAMPLE_INTERVAL_SECONDS)


async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one AdGuard host's queries/blocked/clients counters (delegates
    to the shared fleet-blocker probe-and-write helper)."""
    from logic.apps import adguardhome as _ag  # noqa: PLC0415
    await probe_blocker_sample(_ag.fetch_data, "adguard_samples", host_id,
                               int(service_idx), host_row, chip,
                               "adguard_sampler")


async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    AdGuard host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="adguard_sampler",
        prune_table="adguard_samples",
        history_days_tunable=_Tunable.ADGUARD_HISTORY_DAYS)


async def adguardhome_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no AdGuard chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    await lifespan_sampler_loop(
        "adguard_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


def trend_summary(days: int = 0, *, max_points: int = 90) -> dict:
    """Fleet-wide daily blocked-% trend from the local history (delegates to the
    shared ``fleet_blocker_trend_summary`` helper — only the table + retention
    tunable differ from Pi-hole)."""
    return fleet_blocker_trend_summary("adguard_samples",
                                       _Tunable.ADGUARD_HISTORY_DAYS, days,
                                       max_points=max_points)
