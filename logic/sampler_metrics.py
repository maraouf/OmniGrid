"""In-process per-sampler health metrics.

Lightweight observability for the lifespan-managed sampler tasks
(host_metrics / host_net / host_pulse / host_webmin / host_beszel /
host_http / service / ping / host_baseline / weather). Records each
tick's wall-clock duration + prune-sweep outcome so operators can
spot a sampler that started taking 5 seconds when it used to take
50 ms — the kind of regression that's invisible until it triggers
healthcheck flap or executor saturation.

Storage is a single module-level dict keyed on the sampler's
operator-facing name (the same `name` the lifespan_sampler_loop
helper uses for log lines). Single-process single-replica
deployment makes the plain dict correct (same justification as
`_host_provider_cache_diag` and `_per_node_unreachable`).

Per-tick cost: two `time.perf_counter()` calls + a small dict
update. Zero allocation on the hot path; the dict is mutated
in place. Safe to call from concurrent tasks (single-threaded
asyncio → no race on dict updates).

Surfaced via :func:`get_snapshot` to the
``GET /api/admin/stats/samplers`` endpoint, which the Stats →
Samplers sub-tab consumes.
"""
from __future__ import annotations

import time
from typing import Any, Optional

# Per-sampler state. Shape:
#   {name: {
#       "tick_count": int,            # total ticks since process start
#       "last_tick_started_ts": float,# epoch seconds (None until first tick)
#       "last_tick_duration_ms": float,
#       "last_tick_ok": bool,         # True when tick_fn returned cleanly
#       "last_tick_error": str,       # error class name when not ok
#       "tick_error_count": int,
#       "last_prune_ts": float,       # None until first prune
#       "last_prune_duration_ms": float,
#       "last_prune_rows": int,
#       "prune_count": int,
#   }}
# Operator-facing names match the log-prefix tag (host_metrics_sampler,
# host_net_sampler, etc.) so a slow log line + a slow row in the
# admin panel cross-reference without translation.
_metrics: dict[str, dict[str, Any]] = {}


def _row(name: str) -> dict[str, Any]:
    """Lazy-init the per-sampler row. Default values match the shape
    `get_snapshot()` emits so callers can read fields directly without
    None-checking every field."""
    row = _metrics.get(name)
    if row is None:
        row = {
            "tick_count": 0,
            "last_tick_started_ts": None,
            "last_tick_duration_ms": 0.0,
            "last_tick_ok": True,
            "last_tick_error": "",
            "tick_error_count": 0,
            "last_prune_ts": None,
            "last_prune_duration_ms": 0.0,
            "last_prune_rows": 0,
            "prune_count": 0,
        }
        _metrics[name] = row
    return row


def record_tick(
    name: str,
    duration_ms: float,
    *,
    ok: bool = True,
    error: str = "",
) -> None:
    """Stamp the per-tick outcome on the sampler's row. Called from
    the shared ``lifespan_sampler_loop`` helper so EVERY sampler
    using the helper gets metrics for free. Direct callers (samplers
    that don't go through the helper) can call this manually.

    ``duration_ms`` is the wall-clock time the tick body took (the
    caller measures via ``time.perf_counter``).
    ``ok=False`` + ``error=<class_name>`` when tick_fn raised; the
    row's ``tick_error_count`` increments so operators can see a
    misbehaving sampler at a glance from the panel even if the
    log noise has rolled out of the window.
    """
    row = _row(name)
    row["tick_count"] += 1
    row["last_tick_started_ts"] = time.time()
    row["last_tick_duration_ms"] = float(duration_ms)
    row["last_tick_ok"] = bool(ok)
    if not ok:
        row["tick_error_count"] += 1
        row["last_tick_error"] = error or "unknown"
    else:
        row["last_tick_error"] = ""


def record_prune(
    name: str,
    rows: int,
    duration_ms: float,
) -> None:
    """Stamp the most-recent prune outcome on the sampler's row.
    Called from each sampler's `_prune_old_*` wrapper site. ``rows``
    is the count returned by the prune (the number of DELETE'd
    sample rows); ``duration_ms`` is the wall-clock time the prune
    took. Pruning runs hourly per sampler so a regression that
    pushes prune duration from 50ms to 5s shows up here within an
    hour."""
    row = _row(name)
    row["prune_count"] += 1
    row["last_prune_ts"] = time.time()
    row["last_prune_duration_ms"] = float(duration_ms)
    row["last_prune_rows"] = int(rows)


def get_snapshot() -> list[dict[str, Any]]:
    """Return a sorted-by-name list of per-sampler health rows for
    the ``GET /api/admin/stats/samplers`` endpoint. Each row is a
    shallow copy so the consumer can mutate it (e.g. to add
    derived fields like "tick_age_seconds") without touching our
    internal state."""
    out: list[dict[str, Any]] = []
    now = time.time()
    for name in sorted(_metrics.keys()):
        row = dict(_metrics[name])
        row["name"] = name
        # Derive tick + prune age (seconds since the most-recent
        # tick / prune) for the SPA's "last tick X seconds ago"
        # rendering. None when never-ticked so the SPA can hide
        # the field cleanly. Local-bind to Optional[float] explicitly
        # so the float() call doesn't get flagged with the row dict's
        # Any | None value type.
        row["tick_age_seconds"] = _age_seconds(now, row.get("last_tick_started_ts"))
        row["prune_age_seconds"] = _age_seconds(now, row.get("last_prune_ts"))
        out.append(row)
    return out


def _age_seconds(now: float, ts: Any) -> Optional[int]:
    """Return integer seconds between `now` and `ts`, or None when
    `ts` is None / unparseable. Helper exists so the type narrowing
    happens in ONE place — callers don't repeat the
    `if ts is not None: int(now - float(ts))` dance."""
    if ts is None:
        return None
    try:
        return int(now - float(ts))
    except (TypeError, ValueError):
        return None


async def prune_with_metrics(name: str, prune_fn) -> int:
    """Run a sampler's sync prune function on a worker thread with
    timing + metric recording. Same shape every sampler uses
    (``await asyncio.to_thread(_prune_old_samples)``); wrapping in
    this helper saves the boilerplate of timing + record at each
    call site AND keeps the metric format consistent.

    ``prune_fn`` is the sync prune callable (typically
    ``_prune_old_samples`` / ``_prune_old_rows_sync``). Should return
    the deleted-row count as an int; non-int / None coerces to 0.

    Returns the prune's row count so callers that print a summary
    line (``[<sampler>] pruned N rows older than Xd``) still have
    the value.
    """
    import asyncio as _asyncio
    t0 = time.perf_counter()
    try:
        result = await _asyncio.to_thread(prune_fn)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception: # noqa: BLE001
        # Still record the duration even on failure so the panel
        # surfaces "prune crashed at <ts> after Nms" instead of a
        # silently-frozen prune metric.
        record_prune(name, 0, (time.perf_counter() - t0) * 1000.0)
        raise
    try:
        rows = int(result or 0)
    except (TypeError, ValueError):
        rows = 0
    record_prune(name, rows, (time.perf_counter() - t0) * 1000.0)
    return rows


def reset(name: Optional[str] = None) -> None:
    """Drop metrics. ``name=None`` clears everything (used by the
    admin "reset" button + tests); a specific name drops one row.
    The next tick / prune re-populates the cleared row via
    ``_row``'s lazy-init path."""
    if name is None:
        _metrics.clear()
    else:
        _metrics.pop(name, None)
