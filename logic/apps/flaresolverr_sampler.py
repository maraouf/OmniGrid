"""FlareSolverr usage sampler — lifespan-managed.

FlareSolverr exposes only the CURRENT open sessions (``sessions.list``); there is
no historical / request-volume API. This sampler records each configured
FlareSolverr chip's live open-session count per tick into
``flaresolverr_session_samples`` so the card can show a 30-day usage trend.

One row per ``(host_id, service_idx, tick)``: ``sessions`` (count) + ``ready``
(0/1 — was the solver up at sample time, so the trend distinguishes downtime
from genuinely-idle). Cadence ``tuning_flaresolverr_sample_interval_seconds``
(0 = inherit the global stats interval); retention
``tuning_flaresolverr_history_days`` (default 30). Dormant-cheap when no
FlareSolverr chip is configured — keeps ticking so the operator can pin one
without a restart.

NOTE: open-session count is a coarse activity proxy, NOT request volume —
FlareSolverr publishes no request counter. The ``ready`` column lets the card
distinguish "solver was down" from "up but idle (0 sessions)".
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

_SLUG = "flaresolverr"


def _instances() -> list:
    """Configured FlareSolverr chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "flaresolverr_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global
    stats interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.FLARESOLVERR_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Record one chip's sample: live session count + ready flag. A solver
    that's down / unreachable / mis-configured records ``ready=0, sessions=0``
    (a real downtime state the trend should show — NOT a skip). A code bug
    (unexpected exception) skips the write so it can't poison the series with
    a misleading zero."""
    sessions = 0
    ready = 0
    try:
        from logic.apps import flaresolverr as _fs  # noqa: PLC0415
        data = await _fs.fetch_data(host_row, chip, host_id=host_id,
                                    service_idx=int(service_idx), force=True)
        sessions = int(data.get("sessions") or 0)
        ready = 1 if data.get("ready") else 0
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        # Down / unreachable / bad URL — record the downtime sample below.
        print(f"[flaresolverr_sampler] probe {host_id}#{service_idx} down: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"[flaresolverr_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO flaresolverr_session_samples "
                "(ts, host_id, service_idx, sessions, ready) VALUES (?,?,?,?,?)",
                (int(time.time()), host_id, int(service_idx),
                 int(sessions), int(ready)),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[flaresolverr_sampler] write {host_id}#{service_idx} failed: {e}")


def _prune_old_samples() -> int:
    """Delete rows older than the retention window. Returns the deleted-row
    count (chunked DELETE via the shared helper)."""
    days = _tuning.tuning_int(_Tunable.FLARESOLVERR_HISTORY_DAYS)
    cutoff = int(time.time()) - days * 86400
    return prune_rows_older_than("flaresolverr_session_samples", cutoff)


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body: probe every configured FlareSolverr chip in parallel,
    then run the hourly retention prune (offloaded to a worker thread)."""
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
        n = await prune_with_metrics("flaresolverr_sampler", _prune_old_samples)
        if n:
            days = _tuning.tuning_int(_Tunable.FLARESOLVERR_HISTORY_DAYS)
            print(f"[flaresolverr_sampler] pruned {n} rows older than {days}d")


async def flaresolverr_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no FlareSolverr chip is configured (keeps ticking so a
    runtime pin takes effect without a restart)."""
    await lifespan_sampler_loop(
        "flaresolverr_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def usage_summary(host_id: str, service_idx: int,
                  days: int = 30, *, max_points: int = 30) -> dict:
    """30-day open-session usage trend for one chip. Returns
    ``{series, peak, avg, active_days, samples, current, days}`` where
    ``series`` is up to ``days`` daily-MAX points (oldest-first, missing days
    filled with 0) for a sparkline. Zeroed shape when no samples yet — never
    raises."""
    out: dict = {"series": [], "peak": 0, "avg": 0.0, "active_days": 0,
                 "samples": 0, "current": 0, "days": int(days)}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(days) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, sessions FROM flaresolverr_session_samples "
                "WHERE host_id=? AND service_idx=? AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[flaresolverr_sampler] usage_summary({host_id}#{service_idx}) "
              f"failed: {e}")
        return out
    if not rows:
        return out
    sess = [int(r["sessions"]) for r in rows]
    out["samples"] = len(sess)
    out["peak"] = max(sess)
    out["avg"] = round(sum(sess) / len(sess), 1)
    out["current"] = sess[-1]
    # Daily-max bucket (day index = ts // 86400) → distinct active days +
    # a contiguous fixed-length series (0-filled) for a clean sparkline.
    day_max: dict = defaultdict(int)
    for r in rows:
        d = int(r["ts"]) // 86400
        s = int(r["sessions"])
        if s > day_max[d]:
            day_max[d] = s
    out["active_days"] = sum(1 for v in day_max.values() if v > 0)
    today = int(time.time()) // 86400
    span = max(1, min(int(days), int(max_points)))
    start_day = today - span + 1
    out["series"] = [int(day_max.get(d, 0)) for d in range(start_day, today + 1)]
    return out
