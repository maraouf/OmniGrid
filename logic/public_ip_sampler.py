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
the ``public_ip_enabled`` toggle is off (default OFF — the
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
import time

from logic.tuning import Tunable, tuning_int
from logic import public_ip as _public_ip
from logic.sampler_metrics import record_tick as _record_tick

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
    # Startup confirmation so an operator triaging "IP changes aren't
    # recorded" can verify in Admin → Logs that the sampler is actually
    # armed (vs. silently never started). Logged once.
    print(
        f"[public_ip_sampler] armed: interval={_sampler_interval_seconds()}s "
        f"enabled={_public_ip.is_enabled()}"
    )
    # Per-tick diagnostic state. `_prev_ip` is the IP the LAST successful
    # tick observed (process-local, NOT the DB's latest row — fetch()'s
    # _record_ip_change owns the persistent dedup). `_prev_enabled` tracks
    # the master-toggle state so we log only on a transition rather than
    # spamming a "disabled" line every tick. The steady state (enabled +
    # unchanged IP) stays silent.
    _prev_ip = None
    _prev_enabled = None
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
        enabled = _public_ip.is_enabled()
        if enabled != _prev_enabled:
            # Log ONLY the transition so the operator sees WHY no probes
            # happen when the feature is off — the most common "not
            # recording" cause is the master toggle being off.
            print(f"[public_ip_sampler] feature {'enabled' if enabled else 'disabled'} "
                  f"(public_ip_enabled={'1' if enabled else '0'})")
            _prev_enabled = enabled
        if not enabled:
            await asyncio.sleep(interval)
            continue
        # Per-tick health metric (Stats → Samplers). Timed around the
        # actual force-probe; the gate / idle short-circuits above don't
        # count as ticks. `ok=False` when the probe raises.
        _tick_t0 = time.perf_counter()
        _tick_ok = True
        _tick_err = ""
        try:
            # force=True bypasses the positive cache so every tick is a
            # real re-probe (the whole point — the cache TTL is what hides
            # short flaps from the incidental callers). The negative cache
            # still applies, so a sustained upstream outage doesn't hammer
            # ifconfig.co. fetch() records any change via _record_ip_change.
            result = await _public_ip.fetch(force=True)
            # Diagnostic: surface which of the "not recording" modes this
            # tick hit, so the operator can pinpoint without DB access:
            #   (a) no data  -> upstream error / negative-cache window
            #   (b) empty ip -> partial upstream payload
            #   (c) IP differs from last tick -> fetch() should have
            #       recorded it; if no "[public_ip] recorded IP change"
            #       line follows, the record path is the bug. If this line
            #       NEVER fires across a known WAN change, the egress IP the
            #       container reaches ifconfig.co with simply isn't changing
            #       (NAT / tunnel / VPN egress) — not a code defect.
            # The steady state (enabled + same IP as last tick) stays silent.
            cur_ip = (result.get("ip") if isinstance(result, dict) else "") or ""
            if result is None:
                print("[public_ip_sampler] tick: probe returned no data "
                      "(upstream error or negative-cache window) — skipped")
            elif not cur_ip:
                print("[public_ip_sampler] tick: probe returned empty IP "
                      "(partial upstream payload) — skipped")
            elif _prev_ip is not None and cur_ip != _prev_ip:
                print("[public_ip_sampler] tick: observed a different egress IP "
                      "than the previous tick — fetch() handles the history record")
            if cur_ip:
                _prev_ip = cur_ip
        except Exception as e:  # noqa: BLE001
            if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
                raise
            _tick_ok = False
            _tick_err = type(e).__name__
            print(f"[public_ip_sampler] tick failed: {e}")
        finally:
            _record_tick(
                "public_ip_sampler",
                (time.perf_counter() - _tick_t0) * 1000.0,
                ok=_tick_ok,
                error=_tick_err,
            )
        await asyncio.sleep(interval)
