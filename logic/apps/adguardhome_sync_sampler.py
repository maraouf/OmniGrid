"""AdGuard Home Sync reliability history sampler — lifespan-managed.

The sync tool's status API is current-state only (no history), so this sampler
records each configured AGS chip's replica in-sync count per tick into
``adguardhome_sync_samples``. The expanded card draws a **sync-reliability
trend** — a daily-MIN in-sync-% sparkline (a dip on any day a replica fell out
of sync) — plus a reliability headline ("in sync N% of the time over the window")
that survives the tool keeping no history of its own.

One row per ``(host_id, service_idx, tick)``. Cadence
``tuning_adguardsync_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_adguardsync_history_days`` (default 90).
Dormant-cheap when no AGS chip is configured (keeps ticking so a runtime pin
takes effect without a restart).
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import fleet_instances, resolve_sample_interval
from logic.db import db_conn, prune_rows_older_than
from logic.sampler_loop import lifespan_sampler_loop
from logic.tuning import Tunable as _Tunable


def _instances() -> list:
    """Configured AdGuard Home Sync chips across every catalog slug the module
    handles (deduped by host/chip)."""
    try:
        from logic.apps import adguardhome_sync as _sync  # noqa: PLC0415
        return fleet_instances(_sync.SLUGS)
    except Exception as e:  # noqa: BLE001
        print(f"[adguardsync_sampler] instance enum failed: {e}")
        return []


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.ADGUARDSYNC_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Record one chip's sample: replica total / in-sync counts + origin-ok flag.
    A chip that's down / unreachable / mis-configured skips the write (a transient
    outage shouldn't write a phantom all-failed row). A code bug also skips."""
    try:
        from logic.apps import adguardhome_sync as _sync  # noqa: PLC0415
        data = await _sync.fetch_data(host_row, chip, host_id=host_id,
                                      service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[adguardsync_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[adguardsync_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    replicas_total = int(data.get("replicas_total") or 0)
    replicas_ok = int(data.get("replicas_ok") or 0)
    origin_ok = 1 if data.get("origin_ok") else 0
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO adguardhome_sync_samples "
                "(ts, host_id, service_idx, replicas_total, replicas_ok, "
                "origin_ok) VALUES (?,?,?,?,?,?)",
                (int(time.time()), host_id, int(service_idx),
                 replicas_total, replicas_ok, origin_ok),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[adguardsync_sampler] write {host_id}#{service_idx} failed: {e}")


def _prune_old_samples() -> int:
    """Delete rows older than the retention window. Returns the deleted-row count
    (chunked DELETE via the shared helper)."""
    days = _tuning.tuning_int(_Tunable.ADGUARDSYNC_HISTORY_DAYS)
    cutoff = int(time.time()) - days * 86400
    return prune_rows_older_than("adguardhome_sync_samples", cutoff)


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body: probe every configured AGS chip in parallel, then run the
    hourly retention prune (offloaded to a worker thread)."""
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
        n = await prune_with_metrics("adguardsync_sampler", _prune_old_samples)
        if n:
            days = _tuning.tuning_int(_Tunable.ADGUARDSYNC_HISTORY_DAYS)
            print(f"[adguardsync_sampler] pruned {n} rows older than {days}d")


async def adguardhome_sync_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no AGS chip is configured (keeps ticking so a runtime pin
    takes effect without a restart)."""
    await lifespan_sampler_loop(
        "adguardsync_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def history_summary(host_id: str, service_idx: int,
                    days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Sync-reliability trend for one AGS chip. Returns ``{days, samples,
    sync_pct_series, reliability_pct, out_of_sync_days, current_ok,
    current_total}`` where:

    * ``sync_pct_series`` is up to ``max_points`` daily-MIN in-sync percentages
      (oldest-first, missing days filled with 100) — a dip on any day a replica
      fell out of sync. Per sample, ``replicas_ok / replicas_total * 100`` (100
      when no replicas are configured — trivially in sync).
    * ``reliability_pct`` is the share of samples where EVERY replica was in sync
      AND the origin was reachable.
    * ``out_of_sync_days`` is the count of distinct days a replica was out of
      sync (or the origin was unreachable).

    Zeroed shape when no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.ADGUARDSYNC_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "sync_pct_series": [],
                 "reliability_pct": 0, "out_of_sync_days": 0,
                 "current_ok": 0, "current_total": 0,
                 # OmniGrid-native staleness (the upstream status API carries NO
                 # last-sync timestamp — replicaStatus is host/url/status/error/
                 # protection only): currently_in_sync = the latest sample had
                 # every replica + origin in sync; last_full_sync_ts = the ts of
                 # the most recent fully-in-sync sample (0 when none in window).
                 "currently_in_sync": False, "last_full_sync_ts": 0}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, replicas_total, replicas_ok, origin_ok "
                "FROM adguardhome_sync_samples WHERE host_id=? AND service_idx=? "
                "AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[adguardsync_sampler] history_summary({host_id}#{service_idx}) "
              f"failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["current_ok"] = int(rows[-1]["replicas_ok"] or 0)
    out["current_total"] = int(rows[-1]["replicas_total"] or 0)

    def _pct(tot: int, okn: int) -> float:
        return 100.0 if tot <= 0 else max(0.0, min(100.0, okn / tot * 100.0))

    day_min: dict = defaultdict(lambda: 100.0)
    bad_days: set = set()
    all_in_sync = 0
    last_full_sync_ts = 0
    for r in rows:
        ts_i = int(r["ts"])
        d = ts_i // 86400
        total = int(r["replicas_total"] or 0)
        ok = int(r["replicas_ok"] or 0)
        origin = int(r["origin_ok"] or 0)
        pct = _pct(total, ok)
        if pct < day_min[d]:
            day_min[d] = pct
        in_sync = (ok >= total) and origin == 1
        if in_sync:
            all_in_sync += 1
            if ts_i > last_full_sync_ts:
                last_full_sync_ts = ts_i
        else:
            bad_days.add(d)
    out["reliability_pct"] = round(all_in_sync / len(rows) * 100.0, 1)
    out["out_of_sync_days"] = len(bad_days)
    out["last_full_sync_ts"] = last_full_sync_ts
    # The latest sample's in-sync state — drives the card's "out of sync for X"
    # warning (shown only when this is False).
    _last = rows[-1]
    out["currently_in_sync"] = (
        (int(_last["replicas_ok"] or 0) >= int(_last["replicas_total"] or 0))
        and int(_last["origin_ok"] or 0) == 1)
    today = int(time.time()) // 86400
    span = max(1, min(int(win), int(max_points)))
    start_day = today - span + 1
    out["sync_pct_series"] = [round(day_min.get(d, 100.0), 1)
                              for d in range(start_day, today + 1)]
    return out
