"""Prowlarr counter-rate retention sampler.

Prowlarr's ``/api/v1/indexerstats`` carries LIFETIME cumulative counters
(``numberOfQueries`` / ``numberOfGrabs`` / ``numberOfFailedQueries``, summed
across indexers in ``fetch_data``) but no time series. This lifespan sampler
snapshots those CUMULATIVE counters per tick into ``prowlarr_samples``; the trend
then DIFFS consecutive daily-last values to derive:

  * per-day query + grab THROUGHPUT ("Prowlarr ran N searches today"), and
  * a daily FAILURE-RATE trend (failed_delta / queries_delta) — which days the
    indexers were struggling.

Counter-rate discipline (the host_net_sampler rule): a NEGATIVE day-over-day
delta (a Prowlarr stats reset / DB wipe) is CLAMPED to 0, never stored as a
false spike. Cadence ``tuning_prowlarr_sample_interval_seconds`` (0 = inherit the
global stats interval); retention ``tuning_prowlarr_history_days`` (default 90 —
a rate trend doesn't need a full year). Dormant-cheap when no Prowlarr chip is
configured. Generic tick / instance-enum / cadence resolve delegate to the
shared ``logic/apps/_common.py`` sampler scaffolding; the per-app probe-write +
counter-diff math are Prowlarr-specific.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import (
    resolve_sample_interval, run_sampler_tick, sampler_instances)
from logic.coerce import safe_int
from logic.db import db_conn
from logic.tuning import Tunable as _Tunable

_SLUG = "prowlarr"


def _instances() -> list:
    """Configured Prowlarr chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "prowlarr_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.PROWLARR_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one Prowlarr host's cumulative query / grab / failed counters. A
    host that's down skips the write (no phantom 0 row); a code bug also skips.
    A genuinely-empty stats payload (queries == 0) is still written — it's a real
    'idle so far' baseline the first diff needs."""
    try:
        from logic.apps import prowlarr as _prowlarr  # noqa: PLC0415
        data = await _prowlarr.fetch_data(host_row, chip, host_id=host_id,
                                          service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[prowlarr_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[prowlarr_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("queries")), safe_int(data.get("grabs")),
           safe_int(data.get("failed_queries")), safe_int(data.get("avg_response_ms")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO prowlarr_samples "
                "(ts, host_id, service_idx, total_queries, total_grabs, "
                "total_failed, response_ms) VALUES (?,?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[prowlarr_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    Prowlarr host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="prowlarr_sampler",
        prune_table="prowlarr_samples",
        history_days_tunable=_Tunable.PROWLARR_HISTORY_DAYS)


async def prowlarr_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Prowlarr chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "prowlarr_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Counter-rate trend for one Prowlarr chip. Returns ``{days, samples,
    window_queries, window_grabs, latest_fail_rate, avg_fail_rate,
    series_queries, series_grabs, series_fail_rate}``.

    The cumulative counters are rolled up to each day's LAST value, then DIFFED
    day-over-day: ``series_queries`` / ``series_grabs`` are per-day throughput
    (first day 0; a negative delta — a stats reset — clamps to 0);
    ``series_fail_rate`` is the per-day ``failed_delta / queries_delta * 100``
    (0 on a no-query day). ``latest_fail_rate`` is the point-in-time lifetime
    rate; ``avg_fail_rate`` averages the days that had queries. Zeroed shape when
    no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.PROWLARR_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "window_queries": 0,
                 "window_grabs": 0, "latest_fail_rate": 0.0, "avg_fail_rate": 0.0,
                 "series_queries": [], "series_grabs": [], "series_fail_rate": [],
                 "series_response_ms": [], "latest_response_ms": 0,
                 "peak_response_ms": 0}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, total_queries, total_grabs, total_failed, response_ms "
                "FROM prowlarr_samples WHERE host_id=? AND service_idx=? AND ts >= ? "
                "ORDER BY ts ASC", (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[prowlarr_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    # Per-day LAST cumulative counter (monotonic → latest sample of the day).
    # response_ms is a GAUGE → accumulate the day's POSITIVE samples for a mean.
    q_last: dict = {}
    g_last: dict = {}
    f_last: dict = {}
    rm_sum: dict = {}
    rm_cnt: dict = {}
    for r in rows:
        day = int(r["ts"]) // 86400
        q_last[day] = safe_int(r["total_queries"])  # rows are ts ASC
        g_last[day] = safe_int(r["total_grabs"])
        f_last[day] = safe_int(r["total_failed"])
        rm = safe_int(r["response_ms"])
        if rm > 0:
            rm_sum[day] = rm_sum.get(day, 0) + rm
            rm_cnt[day] = rm_cnt.get(day, 0) + 1
    ordered = sorted(q_last)
    last_day = ordered[-1]
    out["latest_fail_rate"] = (round(f_last[last_day] / q_last[last_day] * 100, 1)
                               if q_last[last_day] > 0 else 0.0)
    # Day-over-day DIFF → per-day throughput + fail rate (skip-don't-synthesize:
    # a negative delta is a counter reset → clamp 0, never a false spike).
    series_q: list = []
    series_g: list = []
    series_fr: list = []
    prev_q = prev_g = prev_f = None
    for d in ordered:
        if prev_q is None:
            series_q.append(0)
            series_g.append(0)
            series_fr.append(0.0)
        else:
            dq = max(0, q_last[d] - prev_q)
            dg = max(0, g_last[d] - prev_g)
            df = max(0, f_last[d] - prev_f)
            series_q.append(dq)
            series_g.append(dg)
            series_fr.append(round(df / dq * 100, 1) if dq > 0 else 0.0)
        prev_q, prev_g, prev_f = q_last[d], g_last[d], f_last[d]
    out["window_queries"] = sum(series_q)
    out["window_grabs"] = sum(series_g)
    vol = [fr for fr, q in zip(series_fr, series_q) if q > 0]
    out["avg_fail_rate"] = round(sum(vol) / len(vol), 1) if vol else 0.0
    # Daily-MEAN response time (gauge), 0 on days with no positive sample.
    series_rm = [round(rm_sum[d] / rm_cnt[d]) if rm_cnt.get(d) else 0 for d in ordered]
    positive_rm = [v for v in series_rm if v > 0]
    out["latest_response_ms"] = next((v for v in reversed(series_rm) if v > 0), 0)
    out["peak_response_ms"] = max(positive_rm) if positive_rm else 0
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_q = [series_q[i] for i in idx]
        series_g = [series_g[i] for i in idx]
        series_fr = [series_fr[i] for i in idx]
        series_rm = [series_rm[i] for i in idx]
    out["series_queries"] = series_q
    out["series_grabs"] = series_g
    out["series_fail_rate"] = series_fr
    out["series_response_ms"] = series_rm
    return out
