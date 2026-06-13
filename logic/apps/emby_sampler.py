"""Emby / Jellyfin streaming retention sampler (SHARED by both brands).

Neither the Emby nor the Jellyfin card keeps any history of concurrent streams
— both render an instantaneous "Now playing: N" snapshot. This lifespan sampler
snapshots each configured media-server chip per tick into ``emby_samples`` (the
SAME table for both brands — a chip's ``host_id``+``service_idx`` is unique
across the two), recording the active-stream / transcoding-stream / total-
bandwidth gauges so the card can draw:

  * a "peak N streams today" stat (the day's MAX concurrent streams), and
  * a daily peak-streams sparkline over the retention window.

The sampler probes BOTH the ``emby`` and ``jellyfin`` slugs and dispatches each
instance to its own brand module's ``fetch_data`` (which already returns
``sessions_active`` / ``transcodes`` / ``bandwidth_bps``). Cadence
``tuning_emby_sample_interval_seconds`` (0 = inherit the global stats interval);
retention ``tuning_emby_history_days`` (default 30). Dormant-cheap when no Emby
/ Jellyfin chip is configured. Generic tick / instance-enum / cadence resolve
delegate to the shared ``logic/apps/_common.py`` sampler scaffolding; the
per-app probe-write + trend math are media-server-specific.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import resolve_sample_interval, run_sampler_tick
from logic.coerce import safe_int
from logic.db import db_conn
from logic.tuning import Tunable as _Tunable

# Both media-server slugs share ONE sampler + ONE table.
_SLUGS: tuple[str, ...] = ("emby", "jellyfin")


def _instances() -> list:
    """Configured Emby AND Jellyfin chips as ``[(host_id, service_idx, host_row,
    chip)]`` (both slugs combined). ``[]`` on any failure — the sampler stays
    dormant. Lazy registry import avoids an import cycle at module load."""
    try:
        from logic.apps import registry as _registry  # noqa: PLC0415
        out: list = []
        for slug in _SLUGS:
            out.extend(_registry.instances_for_slug(slug))
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[emby_sampler] instance enum failed: {e}")
        return []


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.EMBY_SAMPLE_INTERVAL_SECONDS)


async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one media-server host's active-stream / transcode / bandwidth
    gauges. Dispatches to the chip's OWN brand module (emby or jellyfin) so the
    correct auth scheme is used. A host that's down / unreachable skips the
    write (no phantom 0 row); a code bug also skips."""
    try:
        from logic.apps import registry as _registry  # noqa: PLC0415
        slug = _registry._chip_slug(chip)
        mod = _registry.module_for_slug(slug)
        if mod is None or not hasattr(mod, "fetch_data"):
            return
        data = await mod.fetch_data(host_row, chip, host_id=host_id,
                                    service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[emby_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[emby_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("sessions_active")), safe_int(data.get("transcodes")),
           safe_int(data.get("bandwidth_bps")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO emby_samples "
                "(ts, host_id, service_idx, sessions_active, transcodes, "
                "bandwidth_bps) VALUES (?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[emby_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    Emby / Jellyfin host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="emby_sampler",
        prune_table="emby_samples",
        history_days_tunable=_Tunable.EMBY_HISTORY_DAYS)


async def emby_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Emby / Jellyfin chip is configured (keeps ticking so a
    runtime pin takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "emby_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Active-stream trend for one media-server chip. Returns ``{days, samples,
    latest_streams, peak_streams, peak_streams_today, peak_transcodes,
    series_streams, series_transcodes}``.

    ``series_streams`` is each day's MAX concurrent ``sessions_active`` (the
    peak-streams line); ``series_transcodes`` is each day's MAX transcoding
    streams. ``peak_streams`` is the window max; ``peak_streams_today`` is the
    max since local midnight (00:00 in the host's wall-clock, approximated via
    the sampler's UTC day bucket). Each series is up to ``max_points`` points
    (oldest-first, days WITH data only). Zeroed shape when no samples yet —
    never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.EMBY_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "latest_streams": 0,
                 "peak_streams": 0, "peak_streams_today": 0, "peak_transcodes": 0,
                 "series_streams": [], "series_transcodes": []}
    if not host_id:
        return out
    now = int(time.time())
    cutoff = now - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, sessions_active, transcodes "
                "FROM emby_samples WHERE host_id=? AND service_idx=? AND ts >= ? "
                "ORDER BY ts ASC", (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[emby_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["latest_streams"] = safe_int(rows[-1]["sessions_active"])
    out["peak_streams"] = max(safe_int(r["sessions_active"]) for r in rows)
    out["peak_transcodes"] = max(safe_int(r["transcodes"]) for r in rows)
    # Peak streams since the start of today's UTC day bucket.
    today_start = (now // 86400) * 86400
    today_vals = [safe_int(r["sessions_active"]) for r in rows if int(r["ts"]) >= today_start]
    out["peak_streams_today"] = max(today_vals) if today_vals else 0
    # Per-day roll-up: MAX concurrent streams / transcodes (a gauge → the day's
    # peak is the interesting figure, not the average).
    s_max: dict = defaultdict(int)
    t_max: dict = defaultdict(int)
    for r in rows:
        day = int(r["ts"]) // 86400
        s_max[day] = max(s_max[day], safe_int(r["sessions_active"]))
        t_max[day] = max(t_max[day], safe_int(r["transcodes"]))
    ordered = sorted(s_max)
    series_streams = [s_max[d] for d in ordered]
    series_transcodes = [t_max[d] for d in ordered]
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_streams = [series_streams[i] for i in idx]
        series_transcodes = [series_transcodes[i] for i in idx]
    out["series_streams"] = series_streams
    out["series_transcodes"] = series_transcodes
    return out
