"""Proxmox cluster-resource retention sampler — lifespan-managed.

Proxmox VE exposes the current cluster CPU / memory / storage utilisation but
keeps no history of its own to chart. This lifespan sampler snapshots each
configured Proxmox chip's cluster ``cpu_percent`` / ``mem_percent`` /
``storage_percent`` per tick into ``proxmox_samples`` so the card can draw a
"cluster load over time" trend (the hypervisor's resource trend is the headline
"is my cluster trending toward saturation" signal).

The columns are point-in-time GAUGES, so the daily roll-up is an AVERAGE.
Cadence ``tuning_proxmox_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_proxmox_history_days`` (default 30). Dormant-cheap
when no Proxmox chip is configured. Generic tick / instance-enum / cadence
resolve delegate to the shared ``logic/apps/_common.py`` sampler scaffolding; the
per-app probe-write + trend math are Proxmox-specific.

A fetch failure / permission-limited read SKIPS the write (a zeroed point would
be a misleading "idle cluster" reading — the trend should simply gap).
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import resolve_sample_interval, sampler_instances
from logic.coerce import safe_int
from logic.db import db_conn, prune_rows_older_than
from logic.sampler_loop import lifespan_sampler_loop
from logic.tuning import Tunable as _Tunable

_SLUG = "proxmox"


def _instances() -> list:
    """Configured Proxmox chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "proxmox_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.PROXMOX_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one Proxmox chip's cluster CPU% / memory% / storage%. A host
    that's down / unreachable / has no token / is permission-limited SKIPS the
    write (no phantom 0 row); a code bug also skips."""
    try:
        from logic.apps import proxmox as _pve  # noqa: PLC0415
        data = await _pve.fetch_data(host_row, chip, host_id=host_id,
                                     service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[proxmox_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[proxmox_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available") or data.get("perm_limited"):
        return
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("cpu_percent")), safe_int(data.get("mem_percent")),
           safe_int(data.get("storage_percent")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO proxmox_samples "
                "(ts, host_id, service_idx, cpu_percent, mem_percent, "
                "storage_percent) VALUES (?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[proxmox_sampler] write {host_id}#{service_idx} failed: {e}")


def _prune_old_samples() -> int:
    """Delete rows older than the retention window. Returns the deleted-row
    count (chunked DELETE via the shared helper)."""
    days = _tuning.tuning_int(_Tunable.PROXMOX_HISTORY_DAYS)
    cutoff = int(time.time()) - days * 86400
    return prune_rows_older_than("proxmox_samples", cutoff)


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body: probe every configured Proxmox chip in parallel, then run
    the hourly retention prune (offloaded to a worker thread)."""
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
        n = await prune_with_metrics("proxmox_sampler", _prune_old_samples)
        if n:
            days = _tuning.tuning_int(_Tunable.PROXMOX_HISTORY_DAYS)
            print(f"[proxmox_sampler] pruned {n} rows older than {days}d")


async def proxmox_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Proxmox chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    await lifespan_sampler_loop(
        "proxmox_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Cluster-resource trend for one Proxmox chip. Returns ``{days, samples,
    peak_cpu, peak_mem, peak_storage, latest_cpu, latest_mem, latest_storage,
    series_cpu, series_mem, series_storage}`` where each ``series_*`` is up to
    ``max_points`` daily-AVERAGE points (oldest-first, days WITH data only — the
    gauges are current percentages, so a 0-fill day would be a false reading).
    Zeroed shape when no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.PROXMOX_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0,
                 "peak_cpu": 0, "peak_mem": 0, "peak_storage": 0,
                 "latest_cpu": 0, "latest_mem": 0, "latest_storage": 0,
                 "series_cpu": [], "series_mem": [], "series_storage": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, cpu_percent, mem_percent, storage_percent "
                "FROM proxmox_samples WHERE host_id=? AND service_idx=? AND ts >= ? "
                "ORDER BY ts ASC", (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[proxmox_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["peak_cpu"] = max(safe_int(r["cpu_percent"]) for r in rows)
    out["peak_mem"] = max(safe_int(r["mem_percent"]) for r in rows)
    out["peak_storage"] = max(safe_int(r["storage_percent"]) for r in rows)
    out["latest_cpu"] = safe_int(rows[-1]["cpu_percent"])
    out["latest_mem"] = safe_int(rows[-1]["mem_percent"])
    out["latest_storage"] = safe_int(rows[-1]["storage_percent"])
    # Daily-AVERAGE per metric (gauge → mean per day), days with data only.
    cpu_sum: dict = defaultdict(int)
    mem_sum: dict = defaultdict(int)
    sto_sum: dict = defaultdict(int)
    cnt: dict = defaultdict(int)
    for r in rows:
        day = int(r["ts"]) // 86400
        cpu_sum[day] += safe_int(r["cpu_percent"])
        mem_sum[day] += safe_int(r["mem_percent"])
        sto_sum[day] += safe_int(r["storage_percent"])
        cnt[day] += 1
    ordered = sorted(cnt)
    series_cpu = [round(cpu_sum[d] / max(1, cnt[d])) for d in ordered]
    series_mem = [round(mem_sum[d] / max(1, cnt[d])) for d in ordered]
    series_storage = [round(sto_sum[d] / max(1, cnt[d])) for d in ordered]
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_cpu = [series_cpu[i] for i in idx]
        series_mem = [series_mem[i] for i in idx]
        series_storage = [series_storage[i] for i in idx]
    out["series_cpu"] = series_cpu
    out["series_mem"] = series_mem
    out["series_storage"] = series_storage
    return out
