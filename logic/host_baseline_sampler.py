"""Lifespan-managed baseline sampler — recomputes per-host baselines
once an hour for drift-from-baseline detection.

Per the project conventions "Background-task startup rule" this is started inside
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
from logic.db import iter_curated_hosts
from logic.sampler_loop import lifespan_sampler_loop
from logic.coerce import as_dict
from logic.tuning import Tunable, tuning_int as _tuning_int


# Operator-tunable cadence + first-tick delay. Per-use reads (not
# module-import-time) so Admin → Config edits take effect on the
# next tick without a restart.
def _interval_seconds() -> int:
    return _tuning_int(Tunable.HOST_BASELINE_RECOMPUTE_INTERVAL_SECONDS)


def _first_tick_delay() -> int:
    return _tuning_int(Tunable.HOST_BASELINE_FIRST_TICK_DELAY_SECONDS)


async def _baseline_tick(tick: int) -> None:
    """Per-tick body — walk every curated host id and recompute its
    baseline. Per-host failures are caught + logged so one bad host
    doesn't fail the whole tick. ``CancelledError`` /
    ``KeyboardInterrupt`` re-raise so the lifespan cancel propagates
    cleanly through the helper's outer envelope.
    """
    # Walk every curated host AND surface its resolved target so the
    # baseline-diagnostic log line includes WHICH address the
    # underlying samplers were pointed at. Operator-flagged: chasing
    # "no samples for host X" needs to see at a glance whether X's
    # probe targets are correct in the first place.
    host_targets: dict[str, str] = {}
    for h in iter_curated_hosts():
        hid = (h.get("id") or "").strip()
        if not hid:
            continue
        # Resolution chain mirrors `_resolve_ping_target` /
        # ping_sampler / SNMP / SSH per the canonical contract:
        # address → ssh.fqdn → ssh.host → bare host_id.
        _ssh = as_dict(h.get("ssh"))
        host_targets[hid] = (
            (h.get("address") or "").strip()
            or (_ssh.get("fqdn") or "").strip()
            or (_ssh.get("host") or "").strip()
            or hid
        )
    hosts = list(host_targets.keys())
    start = time.time()

    # Single shared `db_conn()` for the whole tick — collapses N
    # per-host connection-opens into ONE. Pre-fix each
    # `compute_baselines` opened its own connection; on a 200-host
    # fleet that was 200 connection-opens per tick, each waiting
    # on the writer-lock's PRAGMA setup -- the visible smoking-gun
    # source of the slow-query storm (`compute_baselines:237 sql=
    # PRAGMA synchronous=NORMAL` 481ms). Now: ONE conn open per
    # tick, one PRAGMA chain, ONE commit at the end. The worker
    # thread offload still applies (the whole walk runs off the
    # event loop) so /api/healthz + /api/* stay responsive.

    def _walk(hosts, host_targets):
        """Single-threaded walk against one shared connection."""
        from logic.db import db_conn as _db_conn
        failures: list[tuple[str, str]] = []
        with _db_conn() as conn:
            for hid in hosts:
                try:
                    _baseline.compute_baselines(
                        hid, host_targets.get(hid, ""), conn=conn,
                    )
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:  # noqa: BLE001
                    failures.append((hid, str(exc)))
            conn.commit()
        return failures

    try:
        failures = await asyncio.to_thread(_walk, hosts, host_targets)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as exc:  # noqa: BLE001
        # Whole-tick failure (rare — usually a connection-open
        # error). Log + continue so the next tick can retry; don't
        # let one bad tick kill the sampler lifetime.
        print(f"[host_baseline_sampler] tick {tick} aborted: {exc}")
        failures = []
    for hid, exc_msg in failures:
        print(f"[host_baseline_sampler] tick {tick} {hid!r} failed: {exc_msg}")
    elapsed = time.time() - start
    print(
        f"[host_baseline_sampler] tick {tick}: walked {len(hosts)} curated "
        f"hosts in {elapsed:.2f}s"
    )


async def host_baseline_sampler_loop() -> None:
    """Lifespan-managed loop. Walks every curated host once per
    ``tuning_host_baseline_recompute_interval_seconds`` and refreshes
    their baselines.

    Cancellation + per-tick error handling live in the shared
    :func:`lifespan_sampler_loop` envelope; the per-host failure
    isolation stays inside :func:`_baseline_tick`.
    """
    await lifespan_sampler_loop(
        "host_baseline_sampler",
        _baseline_tick,
        _interval_seconds,
        first_tick_delay=_first_tick_delay(),
    )
