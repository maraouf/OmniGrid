"""Lifespan-managed public-IP change sampler.

Force-probes ``logic.public_ip.fetch(force=True)`` at
``tuning_public_ip_sample_interval_seconds`` cadence (default 300s
= 5 min) so a public-IP CHANGE lands in ``public_ip_history`` even
when nobody is looking at the Public-IP widget.

WHY this exists: ``public_ip.fetch()`` records a change (via
``_record_ip_change``) ONLY on a cache miss that returns a NEW ip,
and its only callers are incidental — the SPA widget load, the
Telegram ``/ip`` command, and the AI palette context-builder. Those
are gated by the positive cache TTL (600s default), so a short-lived
WAN flap (e.g. an ISP failover that lasts a few minutes and then
reverts to the prior address) is never sampled: the system only ever
observes the stable before/after value, sees no change against the
last recorded row, and the "changed Xd ago" label goes stale even
though the IP genuinely flapped. A periodic force-probe closes that
gap — every tick re-probes the live IP and records any change.

Master gate: ``logic.public_ip.is_enabled()`` short-circuits when
the ``tuning_public_ip_enabled`` toggle is off (default OFF — the
feature is opt-in for privacy; see logic/public_ip.py). The interval
tunable = 0 disables the sampler entirely (change-detection falls
back to incidental fetches only).

Skip-don't-synthesize discipline: a failed probe writes nothing —
``fetch`` returns None on error and the change-log only gains a row
on a real, differing IP. The next successful tick is the next sample.
No prune: ``public_ip_history`` is intentionally never pruned (IP
changes are low-volume + high-value; operators want a long history).
"""
from __future__ import annotations

import asyncio

from logic.tuning import Tunable, tuning_int
from logic import public_ip as _public_ip


# Give boot-time schema migrations time to land before the first probe,
# matching the weather / host_baseline samplers' first-tick delay.
_FIRST_TICK_DELAY_SECONDS = 30


def _sampler_interval_seconds() -> int:
    """Per-use read so an Admin → Config edit applies on the next tick
    without a restart."""
    try:
        return int(tuning_int(Tunable.PUBLIC_IP_SAMPLE_INTERVAL_SECONDS))
    except (KeyError, ValueError, TypeError):
        return 300


async def sampler_loop() -> None:
    """Lifespan-managed loop. Started via ``_lifespan`` in main.py;
    cancelled cleanly in the matching ``finally`` block.

    Idle behaviour: when the interval tunable is 0 OR the master toggle
    is off, the loop SLEEPS and re-reads both per-cycle instead of
    exiting — so an operator who later enables the feature / sampler via
    Admin → Config (or Admin → Public IP) doesn't have to restart the
    container.
    """
    await asyncio.sleep(_FIRST_TICK_DELAY_SECONDS)
    while True:
        interval = _sampler_interval_seconds()
        if interval <= 0:
            # Sampler disabled — re-check every 60s so a tunable flip is
            # picked up promptly without burning CPU.
            await asyncio.sleep(60)
            continue
        # Master-gate each tick — operator may flip the public-IP toggle
        # off without restart, and a disabled fetch() must not make an
        # outbound call to the third-party lookup service.
        if not _public_ip.is_enabled():
            await asyncio.sleep(interval)
            continue
        try:
            # force=True bypasses the positive cache so every tick is a
            # real re-probe (the whole point — the cache TTL is what hides
            # short flaps from the incidental callers). The negative cache
            # still applies, so a sustained upstream outage doesn't hammer
            # ifconfig.co. fetch() records any change via _record_ip_change.
            await _public_ip.fetch(force=True)
        except Exception as e:  # noqa: BLE001
            if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
                raise
            print(f"[public_ip_sampler] tick failed: {e}")
        await asyncio.sleep(interval)
