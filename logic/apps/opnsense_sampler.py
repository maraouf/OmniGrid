"""OPNsense interface-throughput retention sampler.

The firewall's own UI keeps no long-horizon throughput history, so the only way
to answer "how much did this uplink move over the last 30 days / what was the
peak" is to snapshot it ourselves. This lifespan sampler records each configured
OPNsense chip's TOTAL download / upload rate (bytes/sec, summed across NICs) per
tick into ``opnsense_samples``.

``usage_summary`` then turns those instantaneous rate samples into a period view:
the total data volume (integrating rate × gap, each gap capped so a downtime
window can't multiply a stale rate into a fake spike — the host_net_sampler
skip-don't-synthesize discipline), the peak + average throughput, and a
daily-average sparkline. Cadence ``tuning_opnsense_sample_interval_seconds``
(0 = inherit the global stats interval); retention
``tuning_opnsense_history_days`` (default 90). Dormant-cheap when no OPNsense
chip is configured. Generic tick / instance-enum / cadence resolve delegate to
the shared ``logic/apps/_common.py`` sampler scaffolding; the per-app probe-write
+ aggregation math are OPNsense-specific.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import (
    resolve_sample_interval, run_sampler_tick, sampler_instances)
from logic.coerce import safe_float, safe_int
from logic.db import db_conn
from logic.tuning import Tunable as _Tunable

_SLUG = "opnsense"


def _instances() -> list:
    """Configured OPNsense chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "opnsense_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.OPNSENSE_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one OPNsense firewall's total download / upload throughput. A
    host that's down / unreachable skips the write (no phantom 0 row); a code
    bug also skips."""
    try:
        from logic.apps import opnsense as _opn  # noqa: PLC0415
        data = await _opn.fetch_data(host_row, chip, host_id=host_id,
                                     service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[opnsense_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[opnsense_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict):
        return
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("net_rx_bps")), safe_int(data.get("net_tx_bps")),
           safe_int(data.get("pf_states_current")),
           safe_float(data.get("cpu_percent")),
           safe_float(data.get("mem_percent")),
           safe_float(data.get("load_1m")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO opnsense_samples "
                "(ts, host_id, service_idx, rx_bps, tx_bps, "
                "pf_states, cpu_pct, mem_pct, load_1m) "
                "VALUES (?,?,?,?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[opnsense_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    OPNsense host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="opnsense_sampler",
        prune_table="opnsense_samples",
        history_days_tunable=_Tunable.OPNSENSE_HISTORY_DAYS)


async def opnsense_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no OPNsense chip is configured (keeps ticking so a runtime
    pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "opnsense_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


def _human_bps(v: float) -> str:
    """bytes/sec → compact human string (B/s, KB/s, MB/s, GB/s)."""
    v = float(v or 0)
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if v < 1024 or unit == "GB/s":
            return f"{v:.0f} {unit}" if unit == "B/s" else f"{v:.1f} {unit}"
        v /= 1024.0
    return f"{v:.1f} GB/s"


# noinspection DuplicatedCode
def usage_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Period throughput aggregate for one OPNsense chip. Returns ``{days,
    samples, total_rx_bytes, total_tx_bytes, peak_rx_bps, peak_tx_bps,
    avg_rx_bps, avg_tx_bps, active_days, series_rx, series_tx}``.

    ``total_*_bytes`` integrate the instantaneous rate over each inter-sample gap
    (gap capped at ~2× the sample interval so a downtime window can't multiply a
    stale rate into a fake volume — counter-rate skip-don't-synthesize). ``peak``
    / ``avg`` are over the rate samples; ``series_*`` are each day's AVERAGE
    throughput (oldest-first, days WITH data only), up to ``max_points`` points.
    Zeroed shape when no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.OPNSENSE_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0,
                 "total_rx_bytes": 0, "total_tx_bytes": 0,
                 "peak_rx_bps": 0, "peak_tx_bps": 0,
                 "avg_rx_bps": 0, "avg_tx_bps": 0, "active_days": 0,
                 "series_rx": [], "series_tx": [],
                 # System-health trend (pf state-table count + CPU/mem%/1-min
                 # load), daily-average series + peak, from the same samples.
                 "series_pf": [], "series_cpu": [], "series_mem": [],
                 "series_load": [], "peak_pf_states": 0, "peak_cpu_pct": 0,
                 "peak_mem_pct": 0, "peak_load_1m": 0}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, rx_bps, tx_bps, pf_states, cpu_pct, mem_pct, load_1m "
                "FROM opnsense_samples WHERE host_id=? AND service_idx=? "
                "AND ts >= ? ORDER BY ts ASC", (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[opnsense_sampler] usage_summary({host_id}#{service_idx}) "
              f"failed: {e}")
        return out
    if not rows:
        return out
    n = len(rows)
    out["samples"] = n
    rx_vals = [safe_int(r["rx_bps"]) for r in rows]
    tx_vals = [safe_int(r["tx_bps"]) for r in rows]
    out["peak_rx_bps"] = max(rx_vals)
    out["peak_tx_bps"] = max(tx_vals)
    out["avg_rx_bps"] = int(sum(rx_vals) / n)
    out["avg_tx_bps"] = int(sum(tx_vals) / n)
    # Integrate rate × gap into a data volume — cap each gap so a downtime hole
    # can't multiply a stale rate into a fake spike.
    gap_cap = max(2 * _resolve_interval(), 600)
    total_rx = 0.0
    total_tx = 0.0
    for i in range(1, n):
        gap = int(rows[i]["ts"]) - int(rows[i - 1]["ts"])
        if gap < 1:
            continue
        g = min(gap, gap_cap)
        total_rx += rx_vals[i - 1] * g
        total_tx += tx_vals[i - 1] * g
    out["total_rx_bytes"] = int(total_rx)
    out["total_tx_bytes"] = int(total_tx)
    # Per-day AVERAGE throughput, oldest-first (days WITH data only).
    rx_day: "defaultdict[int, list[int]]" = defaultdict(list)
    tx_day: "defaultdict[int, list[int]]" = defaultdict(list)
    for i, r in enumerate(rows):
        day = int(r["ts"]) // 86400
        rx_day[day].append(rx_vals[i])
        tx_day[day].append(tx_vals[i])
    ordered = sorted(rx_day)
    out["active_days"] = len(ordered)
    series_rx = [int(sum(rx_day[d]) / len(rx_day[d])) for d in ordered]
    series_tx = [int(sum(tx_day[d]) / len(tx_day[d])) for d in ordered]
    # System-health daily-average series (same day buckets as rx/tx).
    pf_vals = [safe_int(r["pf_states"]) for r in rows]
    cpu_vals = [safe_float(r["cpu_pct"]) for r in rows]
    mem_vals = [safe_float(r["mem_pct"]) for r in rows]
    load_vals = [safe_float(r["load_1m"]) for r in rows]
    out["peak_pf_states"] = max(pf_vals) if pf_vals else 0
    out["peak_cpu_pct"] = round(max(cpu_vals), 1) if cpu_vals else 0
    out["peak_mem_pct"] = round(max(mem_vals), 1) if mem_vals else 0
    out["peak_load_1m"] = round(max(load_vals), 2) if load_vals else 0
    pf_day: "defaultdict[int, list[int]]" = defaultdict(list)
    cpu_day: "defaultdict[int, list[float]]" = defaultdict(list)
    mem_day: "defaultdict[int, list[float]]" = defaultdict(list)
    load_day: "defaultdict[int, list[float]]" = defaultdict(list)
    for i, r in enumerate(rows):
        day = int(r["ts"]) // 86400
        pf_day[day].append(pf_vals[i])
        cpu_day[day].append(cpu_vals[i])
        mem_day[day].append(mem_vals[i])
        load_day[day].append(load_vals[i])
    series_pf = [int(sum(pf_day[d]) / len(pf_day[d])) for d in ordered]
    series_cpu = [round(sum(cpu_day[d]) / len(cpu_day[d]), 1) for d in ordered]
    series_mem = [round(sum(mem_day[d]) / len(mem_day[d]), 1) for d in ordered]
    series_load = [round(sum(load_day[d]) / len(load_day[d]), 2) for d in ordered]
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_rx = [series_rx[i] for i in idx]
        series_tx = [series_tx[i] for i in idx]
        series_pf = [series_pf[i] for i in idx]
        series_cpu = [series_cpu[i] for i in idx]
        series_mem = [series_mem[i] for i in idx]
        series_load = [series_load[i] for i in idx]
    out["series_rx"] = series_rx
    out["series_tx"] = series_tx
    out["series_pf"] = series_pf
    out["series_cpu"] = series_cpu
    out["series_mem"] = series_mem
    out["series_load"] = series_load
    return out
