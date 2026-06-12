"""qBittorrent transfer-speed + free-disk retention sampler.

qBittorrent's WebUI exposes only the CURRENT transfer speeds + free disk — there
is no built-in speed history to chart. This lifespan sampler snapshots each
configured qBittorrent chip's dl/up speed + free-disk + torrent count per tick
into ``qbittorrent_samples`` so the card can draw:

  * a daily-AVERAGE transfer-speed sparkline (dl + up), and
  * a DISK-FREE-RUNWAY projection — a linear fit over the daily ``free_space_gb``
    points giving "at this fill rate the download disk is full in ~N days".

The columns are CURRENT-rate / current-free GAUGES (not cumulative counters), so
the daily roll-up is an AVERAGE. Cadence
``tuning_qbittorrent_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_qbittorrent_history_days`` (default 30).
Dormant-cheap when no qBittorrent chip is configured. Generic tick / instance-
enum / cadence resolve delegate to the shared ``logic/apps/_common.py`` sampler
scaffolding; the per-app probe-write + trend math are qBittorrent-specific.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import (
    disk_runway_days, resolve_sample_interval, run_sampler_tick, sampler_instances)
from logic.coerce import safe_float, safe_int
from logic.db import db_conn
from logic.tuning import Tunable as _Tunable

_SLUG = "qbittorrent"


def _instances() -> list:
    """Configured qBittorrent chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "qbittorrent_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.QBITTORRENT_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one qBittorrent host's dl/up speed + free-disk + torrent count.
    A host that's down / unreachable / has no password skips the write (no
    phantom 0 row); a code bug also skips."""
    try:
        from logic.apps import qbittorrent as _qbit  # noqa: PLC0415
        data = await _qbit.fetch_data(host_row, chip, host_id=host_id,
                                      service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[qbittorrent_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[qbittorrent_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("dl_speed")), safe_int(data.get("up_speed")),
           safe_float(data.get("free_space_gb")), safe_int(data.get("torrents_total")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO qbittorrent_samples "
                "(ts, host_id, service_idx, dl_speed, up_speed, free_space_gb, "
                "torrents) VALUES (?,?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[qbittorrent_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    qBittorrent host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="qbittorrent_sampler",
        prune_table="qbittorrent_samples",
        history_days_tunable=_Tunable.QBITTORRENT_HISTORY_DAYS)


async def qbittorrent_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no qBittorrent chip is configured (keeps ticking so a
    runtime pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "qbittorrent_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Transfer-speed + free-disk trend for one qBittorrent chip. Returns
    ``{days, samples, peak_dl, latest_dl, latest_up, latest_free_gb,
    disk_runway_days, series_dl, series_up, series_free}`` where each ``series_*``
    is up to ``max_points`` daily-AVERAGE points (oldest-first, days WITH data
    only — the gauges are current rates, so a 0-fill day would be a false
    reading). Speeds are bytes/s; free space is GiB. ``disk_runway_days`` is the
    projected days until the download disk is full (``None`` when free space is
    flat / growing or there's too little history). Zeroed shape when no samples
    yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.QBITTORRENT_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "peak_dl": 0, "latest_dl": 0,
                 "latest_up": 0, "latest_free_gb": 0.0, "disk_runway_days": None,
                 "series_dl": [], "series_up": [], "series_free": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, dl_speed, up_speed, free_space_gb FROM qbittorrent_samples "
                "WHERE host_id=? AND service_idx=? AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[qbittorrent_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["peak_dl"] = max(safe_int(r["dl_speed"]) for r in rows)
    out["latest_dl"] = safe_int(rows[-1]["dl_speed"])
    out["latest_up"] = safe_int(rows[-1]["up_speed"])
    out["latest_free_gb"] = round(safe_float(rows[-1]["free_space_gb"]), 1)
    # Daily-AVERAGE per metric (gauge → mean per day), days with data only.
    dl_sum: dict = defaultdict(int)
    up_sum: dict = defaultdict(int)
    free_sum: dict = defaultdict(float)
    cnt: dict = defaultdict(int)
    for r in rows:
        day = int(r["ts"]) // 86400
        dl_sum[day] += safe_int(r["dl_speed"])
        up_sum[day] += safe_int(r["up_speed"])
        free_sum[day] += safe_float(r["free_space_gb"])
        cnt[day] += 1
    ordered = sorted(cnt)
    series_dl = [round(dl_sum[d] / max(1, cnt[d])) for d in ordered]
    series_up = [round(up_sum[d] / max(1, cnt[d])) for d in ordered]
    series_free = [round(free_sum[d] / max(1, cnt[d]), 1) for d in ordered]
    # Disk-runway uses the FULL (unsampled) daily free-space series.
    out["disk_runway_days"] = disk_runway_days(series_free)
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_dl = [series_dl[i] for i in idx]
        series_up = [series_up[i] for i in idx]
        series_free = [series_free[i] for i in idx]
    out["series_dl"] = series_dl
    out["series_up"] = series_up
    out["series_free"] = series_free
    return out
