"""Shared Servarr-family (*arr) retention sampler — Radarr / Sonarr / Lidarr /
Readarr.

The *arr apps surface no upstream history for "how big is the library", "how
deep is the missing backlog", or "how much disk is left" — the live API only
answers the CURRENT value. This single lifespan sampler snapshots EVERY pinned
*arr instance (across all four apps) per tick into ``servarr_samples``, so one
table + one sampler powers the per-app trend charts for the whole family.

``trend_summary`` rolls the rows into:
  * a daily-AVERAGE LIBRARY-GROWTH curve (total items over time),
  * a daily-AVERAGE MISSING-BACKLOG curve, and
  * a DISK-FREE-RUNWAY projection — a linear fit over the daily ``disk_free_gb``
    points giving "at this fill rate the library disk is full in ~N days" (the
    standout, distinctive *arr feature the assessment called out).

The gauges are CURRENT depth (not cumulative counters), so the daily roll-up is
an AVERAGE (not a max-before-reset like the DNS-blocker samplers).

Cadence ``tuning_servarr_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_servarr_history_days`` (default 365). Dormant-cheap
when no *arr chip is configured. Generic tick / cadence resolve delegate to the
shared ``logic/apps/_common.py`` sampler scaffolding; the per-instance probe-write
+ trend math are *arr-specific (one table, four apps).
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import (
    disk_runway_days, resolve_sample_interval, run_sampler_tick)
from logic.coerce import safe_float, safe_int
from logic.db import db_conn
from logic.tuning import Tunable as _Tunable

# Every *arr slug this one sampler covers — adding a fifth *arr (Whisparr) is a
# one-line edit here + a `trend` embed in that module's fetch_data.
_SLUGS: tuple[str, ...] = ("radarr", "sonarr", "lidarr", "readarr")

# Per-slug "library total" field name in each *arr's fetch_data output. The
# missing / queue / disk_free_gb fields are already uniformly named across the
# family, so only the total varies.
_TOTAL_FIELD = {
    "radarr": "movies_total",
    "sonarr": "series_total",
    "lidarr": "artists_total",
    "readarr": "authors_total",
}


def _instances() -> list:
    """Every pinned *arr instance across all four apps as
    ``[(host_id, service_idx, host_row, chip, slug)]`` — the slug is appended so
    the probe knows which module's ``fetch_data`` to call. ``[]`` on any failure
    (the sampler stays dormant)."""
    try:
        from logic.apps import registry as _registry  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print(f"[servarr_sampler] instance enum failed: {e}")
        return []
    out: list = []
    for slug in _SLUGS:
        for (hid, sidx, hrow, chip) in _registry.instances_for_slug(slug):
            out.append((hid, sidx, hrow, chip, slug))
    return out


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.SERVARR_SAMPLE_INTERVAL_SECONDS)


def _total_of(slug: str, data: dict) -> int:
    """The library-total gauge for an *arr ``fetch_data`` payload — reads the
    per-slug total field (movies / series / artists / authors), falling back to
    a generic ``total`` key for forward-compat."""
    field = _TOTAL_FIELD.get(slug, "")
    val = data.get(field) if field else None
    if val is None:
        val = data.get("total")
    return safe_int(val)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int, host_row: dict,
                     chip: dict, slug: str) -> None:
    """Snapshot one *arr instance's normalised gauges (library total / missing
    backlog / download queue / free disk on the largest mount). A host that's
    down / unreachable skips the write (no phantom 0 row); a code bug also skips.
    Delegates the live fetch to the matching per-app module so the *arr-specific
    parsing stays in one place."""
    import asyncio as _asyncio  # noqa: PLC0415
    try:
        from logic.apps import registry as _registry  # noqa: PLC0415
        mod = _registry.module_for_slug(slug)
        if mod is None or not hasattr(mod, "fetch_data"):
            return
        data = await mod.fetch_data(host_row, chip, host_id=host_id,
                                    service_idx=int(service_idx), force=True)
    except (_asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[servarr_sampler] probe {slug} {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[servarr_sampler] probe {slug} {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    row = (int(time.time()), host_id, int(service_idx), slug,
           _total_of(slug, data), safe_int(data.get("missing")),
           safe_int(data.get("queue")), safe_float(data.get("disk_free_gb")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO servarr_samples "
                "(ts, host_id, service_idx, slug, total, missing, queue, "
                "disk_free_gb) VALUES (?,?,?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[servarr_sampler] write {slug} {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    *arr instance + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="servarr_sampler",
        prune_table="servarr_samples",
        history_days_tunable=_Tunable.SERVARR_HISTORY_DAYS)


async def servarr_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no *arr chip is configured (keeps ticking so a runtime pin
    takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "servarr_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Library-growth + missing-backlog + disk-free trend for one *arr chip.

    Returns ``{days, samples, latest_total, peak_missing, latest_missing,
    latest_disk_free_gb, disk_runway_days, series_total, series_missing,
    series_disk_free}`` where each ``series_*`` is up to ``max_points`` daily-
    AVERAGE points (oldest-first, days WITH data only — the gauges are current
    depth, so a 0-fill day would be a false reading). ``disk_runway_days`` is the
    projected days until the library disk is full (``None`` when free space is
    flat / growing or there's too little history). Zeroed shape when no samples
    yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.SERVARR_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "latest_total": 0,
                 "peak_missing": 0, "latest_missing": 0,
                 "latest_disk_free_gb": 0.0, "disk_runway_days": None,
                 "series_total": [], "series_missing": [], "series_disk_free": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, total, missing, disk_free_gb FROM servarr_samples "
                "WHERE host_id=? AND service_idx=? AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[servarr_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["latest_total"] = safe_int(rows[-1]["total"])
    out["latest_missing"] = safe_int(rows[-1]["missing"])
    out["latest_disk_free_gb"] = round(safe_float(rows[-1]["disk_free_gb"]), 1)
    out["peak_missing"] = max(safe_int(r["missing"]) for r in rows)
    # Daily-AVERAGE per metric (gauge → mean per day), days with data only.
    t_sum: dict = defaultdict(int)
    m_sum: dict = defaultdict(int)
    d_sum: dict = defaultdict(float)
    cnt: dict = defaultdict(int)
    for r in rows:
        day = int(r["ts"]) // 86400
        t_sum[day] += safe_int(r["total"])
        m_sum[day] += safe_int(r["missing"])
        d_sum[day] += safe_float(r["disk_free_gb"])
        cnt[day] += 1
    ordered = sorted(cnt)
    series_total = [round(t_sum[d] / max(1, cnt[d]), 1) for d in ordered]
    series_missing = [round(m_sum[d] / max(1, cnt[d]), 1) for d in ordered]
    series_disk = [round(d_sum[d] / max(1, cnt[d]), 1) for d in ordered]
    # Disk-runway projection uses the FULL (unsampled) daily free-space series so
    # the linear fit sees every point regardless of the chart's max_points cap.
    out["disk_runway_days"] = disk_runway_days(series_disk)
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_total = [series_total[i] for i in idx]
        series_missing = [series_missing[i] for i in idx]
        series_disk = [series_disk[i] for i in idx]
    out["series_total"] = series_total
    out["series_missing"] = series_missing
    out["series_disk_free"] = series_disk
    return out
