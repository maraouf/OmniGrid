"""Canonical lifespan-sampler loop helper.

Wraps the outer ``while True`` + cancellation-discipline + sleep
envelope that every lifespan-managed sampler must implement. Per
the project conventions "Background-task startup rule" + "Background-task
lifecycle" + "Broad except Exception MUST carve out CancelledError"
rules, the envelope has multiple non-obvious correctness constraints:

* ``asyncio.CancelledError`` MUST re-raise from BOTH the tick body's
  outer except AND the inter-tick ``asyncio.sleep`` — without that
  the lifespan cleanup hangs waiting for a "cancelled" task that
  swallowed its cancel.
* ``KeyboardInterrupt`` likewise must re-raise so a dev-shell
  Ctrl+C reaches the event-loop teardown.
* Per-tick exceptions are LOGGED, not propagated — a single
  malformed payload mustn't kill the always-on sampler.
* The outer try/except wrapping the whole loop must catch
  ``CancelledError`` to log a clean "lifespan cancelled" line for
  Admin → Logs visibility, then re-raise.

The tick body is passed in as an ``async`` callable receiving the
tick number; sampler-specific gating (active-provider check, curated
host walk, per-iter logging) stays inside that closure where the
caller already has the provider-specific context. Interval is
resolved per-tick via the supplied callable so Admin → Config edits
take effect on the next tick without a sampler restart.

Why a helper and not a base class:

* Coroutines compose cleaner as callables — the alternative is a
  ``BaseSampler`` class with an abstract ``async def tick(self)`` and
  the sampler module would inherit / instantiate. Net LOC is the
  same, but the closure form keeps the sampler module a flat namespace
  of functions instead of a class soup. Matches the existing pattern.

Migration discipline:

* Only migrate samplers whose per-tick body is a single coherent
  block. Samplers with complex ``continue``-on-gate skip patterns OR
  per-iter logging that PRECEDES the try-block should stay on their
  per-sampler boilerplate — forcing them into the helper would require
  behaviour changes for marginal DRY win.
* New samplers SHOULD use this helper from day one.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

TickFn = Callable[[int], Awaitable[None]]
IntervalFn = Callable[[], int]


async def lifespan_sampler_loop(
    name: str,
    tick_fn: TickFn,
    interval_fn: IntervalFn,
    *,
    first_tick_delay: Optional[int] = None,
) -> None:
    """Run ``tick_fn`` in a lifespan-managed loop until cancelled.

    ``name`` is the operator-facing log prefix (e.g. ``"host_net_sampler"``);
    appears in the boot / shutdown banners + per-tick error lines. Keep
    it stable across releases — operators grep Admin → Logs for these
    tags when triaging.

    ``tick_fn`` is the per-iteration async closure. Receives the
    current tick number (1-indexed). Should handle its own
    per-provider gating (active-source check, curated-host walk, etc.)
    + per-iter logging. Exceptions raised from ``tick_fn`` (other than
    ``CancelledError`` / ``KeyboardInterrupt`` — those propagate) are
    caught and logged, then the loop sleeps and continues.

    ``interval_fn`` returns the sleep interval in seconds AFTER each
    tick. Resolved per-tick so the operator's Admin → Config edits
    take effect on the next tick without a restart. Must return a
    positive int; values ≤ 0 are clamped to 1 to keep the loop
    advancing under any tunable resolver failure.

    ``first_tick_delay`` is an optional pre-loop sleep (seconds).
    Most samplers want a short delay (~30s) so DB migrations land +
    the rest of the lifespan boot lines emit before the first probe.
    Pass ``None`` to skip (e.g. the host_baseline sampler uses its
    own tunable-driven delay outside the helper).
    """
    # Lazy import keeps the metrics module out of the cold-import
    # graph for samplers that don't use the shared loop helper —
    # they call sampler_metrics.record_* directly when they want
    # the same observability surface.
    from logic.sampler_metrics import record_tick as _record_tick
    import time as _time
    print(f"[{name}] lifespan started")
    if first_tick_delay and first_tick_delay > 0:
        try:
            await asyncio.sleep(first_tick_delay)
        except asyncio.CancelledError:
            print(f"[{name}] lifespan cancelled during first-tick delay")
            raise
    tick = 0
    try:
        while True:
            tick += 1
            # Wall-clock timing around the tick body so the Stats →
            # Samplers panel surfaces per-tick duration trends.
            # perf_counter for the diff; record_tick stamps the
            # epoch ts via time.time() for operator-friendly display.
            _tick_t0 = _time.perf_counter()
            _tick_ok = True
            _tick_err = ""
            try:
                await tick_fn(tick)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as exc:  # noqa: BLE001
                _tick_ok = False
                _tick_err = type(exc).__name__
                print(f"[{name}] tick {tick} error: {exc}")
            finally:
                _record_tick(
                    name,
                    (_time.perf_counter() - _tick_t0) * 1000.0,
                    ok=_tick_ok,
                    error=_tick_err,
                )
            interval = interval_fn()
            if interval <= 0:
                interval = 1
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
    except asyncio.CancelledError:
        print(f"[{name}] lifespan cancelled")
        raise
