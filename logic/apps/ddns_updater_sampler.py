"""ddns-updater history sampler — lifespan-managed.

ddns-updater exposes NO JSON API and keeps no history of its own, so this
sampler records each configured ddns-updater chip's current public IP +
record totals + failing count per tick into ``ddns_samples``. Two derived
views drive the expanded card:

* a **public-IP-change timeline** — diff consecutive ``public_ip`` values
  (the headline: "when did my WAN IP last change?");
* a **failing-count sparkline** — daily-MAX ``fail_count`` so a flapping
  record set is visible at a glance.

One row per ``(host_id, service_idx, tick)``. Cadence
``tuning_ddns_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_ddns_history_days`` (default 90 — IP changes
are rare and worth a quarter of history). Dormant-cheap when no ddns-updater
chip is configured (keeps ticking so the operator can pin one without a
restart).
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import resolve_sample_interval, sampler_instances
from logic.db import db_conn, prune_rows_older_than
from logic.sampler_loop import lifespan_sampler_loop
from logic.tuning import Tunable as _Tunable

_SLUG = "ddns-updater"


def _instances() -> list:
    """Configured ddns-updater chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "ddns_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global
    stats interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.DDNS_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Record one chip's sample: public IP + record totals + failing count. A
    chip that's down / unreachable / mis-configured skips the write so it can't
    poison the timeline with a misleading empty-IP row (a transient outage
    shouldn't read as "IP cleared"). A code bug (unexpected exception) also
    skips."""
    try:
        from logic.apps import ddns_updater as _ddns  # noqa: PLC0415
        data = await _ddns.fetch_data(host_row, chip, host_id=host_id,
                                      service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[ddns_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[ddns_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    public_ip = str(data.get("public_ip") or "").strip()
    records_total = int(data.get("records_total") or 0)
    fail_count = int(data.get("fail_count") or 0)
    up_count = int(data.get("up_count") or 0)
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO ddns_samples "
                "(ts, host_id, service_idx, public_ip, records_total, fail_count, up_count) "
                "VALUES (?,?,?,?,?,?,?)",
                (int(time.time()), host_id, int(service_idx),
                 public_ip, records_total, fail_count, up_count),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[ddns_sampler] write {host_id}#{service_idx} failed: {e}")


def _prune_old_samples() -> int:
    """Delete rows older than the retention window. Returns the deleted-row
    count (chunked DELETE via the shared helper)."""
    days = _tuning.tuning_int(_Tunable.DDNS_HISTORY_DAYS)
    cutoff = int(time.time()) - days * 86400
    return prune_rows_older_than("ddns_samples", cutoff)


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body: probe every configured ddns-updater chip in parallel,
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
        n = await prune_with_metrics("ddns_sampler", _prune_old_samples)
        if n:
            days = _tuning.tuning_int(_Tunable.DDNS_HISTORY_DAYS)
            print(f"[ddns_sampler] pruned {n} rows older than {days}d")


async def ddns_updater_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no ddns-updater chip is configured (keeps ticking so a
    runtime pin takes effect without a restart)."""
    await lifespan_sampler_loop(
        "ddns_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


def history_summary(host_id: str, service_idx: int,
                    days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Public-IP-change timeline + failing-count sparkline for one chip.

    Returns ``{days, samples, current_ip, current_ip_since, ip_change_count,
    ip_changes, fail_series, fail_peak}`` where:

    * ``ip_changes`` is the chronological list of distinct public IPs observed
      (``{ts, ip}``, oldest-first, newest-relevant) — derived by diffing
      consecutive samples so a stable IP collapses to one entry. Capped to the
      most recent 20.
    * ``current_ip_since`` is the ts the current IP was first observed.
    * ``fail_series`` is up to ``days`` daily-MAX ``fail_count`` points
      (oldest-first, missing days filled with 0) for a sparkline.

    Zeroed shape when no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.DDNS_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "current_ip": "",
                 "current_ip_since": 0, "ip_change_count": 0,
                 "ip_changes": [], "fail_series": [], "fail_peak": 0,
                 # Up-vs-fail stacked-trend companion series (daily-MAX up_count,
                 # same buckets as fail_series).
                 "up_series": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, public_ip, fail_count, up_count FROM ddns_samples "
                "WHERE host_id=? AND service_idx=? AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[ddns_sampler] history_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    # Public-IP-change timeline: record an entry whenever the (non-empty) IP
    # differs from the previously-seen IP. The first observed IP is the
    # baseline; each subsequent differing IP is a real change.
    changes: list[dict] = []
    prev_ip = ""
    for r in rows:
        ip = str(r["public_ip"] or "").strip()
        if not ip:
            continue
        if ip != prev_ip:
            changes.append({"ts": int(r["ts"]), "ip": ip})
            prev_ip = ip
    if changes:
        out["current_ip"] = changes[-1]["ip"]
        out["current_ip_since"] = changes[-1]["ts"]
        # ip_change_count excludes the baseline (first observation).
        out["ip_change_count"] = max(0, len(changes) - 1)
        out["ip_changes"] = changes[-20:]
    # Failing-count sparkline: daily-MAX bucket (day index = ts // 86400) →
    # a contiguous fixed-length, 0-filled series. up_series is the daily-MAX
    # healthy-record count over the same buckets for the stacked trend.
    day_max: dict = defaultdict(int)
    up_day_max: dict = defaultdict(int)
    for r in rows:
        d = int(r["ts"]) // 86400
        f = int(r["fail_count"] or 0)
        if f > day_max[d]:
            day_max[d] = f
        u = int(r["up_count"] or 0)
        if u > up_day_max[d]:
            up_day_max[d] = u
    out["fail_peak"] = max(day_max.values()) if day_max else 0
    today = int(time.time()) // 86400
    span = max(1, min(int(win), int(max_points)))
    start_day = today - span + 1
    out["fail_series"] = [int(day_max.get(d, 0)) for d in range(start_day, today + 1)]
    out["up_series"] = [int(up_day_max.get(d, 0)) for d in range(start_day, today + 1)]
    return out
