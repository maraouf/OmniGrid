"""Lifespan-managed baseline sampler — recomputes per-host baselines
once an hour for drift-from-baseline detection.

Per CLAUDE.md "Background-task startup rule" this is started inside
FastAPI's `lifespan` handler, not at module import. Cancellation is
honoured via the standard `asyncio.CancelledError` re-raise so a
container shutdown / hot-reload doesn't leave the loop running.

Cadence: hourly. Baselines move slowly (30-day rolling window), so
finer cadence would burn DB cycles for a value that barely moves.
First tick fires after a 60s startup delay so the schema migration
has time to land before the sampler reads `host_baselines`.
"""
from __future__ import annotations

import asyncio
import time

from logic import host_baseline as _baseline
from logic.db import db_conn, get_setting
from logic.tuning import Tunable, tuning_int as _tuning_int


# Operator-tunable cadence + first-tick delay. Per-use reads (not
# module-import-time) so Admin → Config edits take effect on the
# next tick without a restart.
def _interval_seconds() -> int:
    return _tuning_int(Tunable.HOST_BASELINE_RECOMPUTE_INTERVAL_SECONDS)


def _first_tick_delay() -> int:
    return _tuning_int(Tunable.HOST_BASELINE_FIRST_TICK_DELAY_SECONDS)


def _curated_host_ids() -> list[str]:
    """Resolve every curated host's id from `hosts_config`. Mirrors
    the pattern other samplers use for the per-host fan-out."""
    raw = (get_setting("hosts_config", "") or "").strip()
    if not raw:
        return []
    try:
        import json as _json
        cfg = _json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(cfg, list):
        return []
    out = []
    for h in cfg:
        if not isinstance(h, dict):
            continue
        hid = (h.get("id") or "").strip()
        if hid:
            out.append(hid)
    return out


async def host_baseline_sampler_loop() -> None:
    """Lifespan-managed loop. Walks every curated host once per
    `tuning_host_baseline_recompute_interval_seconds` and refreshes
    their baselines.

    Cancellation: re-raises `asyncio.CancelledError` so the lifespan
    cleanup completes promptly. Per-host failures don't fail the
    whole tick — `compute_baselines` swallows + logs internally.
    """
    print("[host_baseline_sampler] lifespan started")
    await asyncio.sleep(_first_tick_delay())
    tick = 0
    try:
        while True:
            tick += 1
            try:
                hosts = _curated_host_ids()
                start = time.time()
                for hid in hosts:
                    try:
                        _baseline.compute_baselines(hid)
                    except (asyncio.CancelledError, KeyboardInterrupt):
                        raise
                    except Exception as e:
                        print(f"[host_baseline_sampler] tick {tick} {hid!r} failed: {e}")
                elapsed = time.time() - start
                print(
                    f"[host_baseline_sampler] tick {tick}: walked {len(hosts)} curated "
                    f"hosts in {elapsed:.2f}s"
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[host_baseline_sampler] tick {tick} error: {e}")
            try:
                await asyncio.sleep(_interval_seconds())
            except asyncio.CancelledError:
                raise
    except asyncio.CancelledError:
        print("[host_baseline_sampler] lifespan cancelled")
        raise
