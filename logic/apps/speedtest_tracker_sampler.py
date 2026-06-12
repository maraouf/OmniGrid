"""Speedtest Tracker long-horizon history sampler — lifespan-managed.

Speedtest Tracker keeps its OWN results history, but prunes it on the operator's
configured retention schedule. This sampler ingests every result it sees into
``speedtest_samples`` so OmniGrid has an INDEPENDENT long-term trend that
survives the upstream ageing its own data out ("your median download dropped
15% over 90 days").

Ingest model: each tick force-fetches the chip's results series (up to the last
~60 tests) and INSERT OR IGNOREs each point keyed on the UPSTREAM result's own
``created_at`` epoch (the sample ``ts`` IS that epoch, NOT the sampler
wall-clock). Re-ingesting a result already recorded is therefore a no-op, and
new results are picked up automatically — so the sampler self-heals + back-fills
the whole window on first run. Cadence ``tuning_speedtest_sample_interval_seconds``
(0 = inherit the global stats interval); retention
``tuning_speedtest_history_days`` (default 365). Dormant-cheap when no Speedtest
chip is configured.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from statistics import median
from typing import Any, Optional

from logic import tuning as _tuning
from logic.apps._common import resolve_sample_interval, sampler_instances
from logic.db import db_conn, prune_rows_older_than
from logic.sampler_loop import lifespan_sampler_loop
from logic.tuning import Tunable as _Tunable

_SLUG = "speedtest-tracker"


def _instances() -> list:
    """Configured Speedtest Tracker chips (delegates to the shared helper)."""
    return sampler_instances(_SLUG, "speedtest_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global
    stats interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.SPEEDTEST_SAMPLE_INTERVAL_SECONDS)


def _ts_epoch(ts: Any) -> int:
    """Parse a Speedtest Tracker ``created_at`` (ISO-8601, may carry a trailing
    ``Z``) into a unix epoch int. 0 when unparseable / empty."""
    s = str(ts or "").strip()
    if not s:
        return 0
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Ingest one chip's results series into ``speedtest_samples`` (INSERT OR
    IGNORE on each result's created_at epoch). A chip that's down / unreachable /
    mis-configured skips the write (a transient outage shouldn't write phantom
    rows). A code bug (unexpected exception) also skips."""
    try:
        from logic.apps import speedtest_tracker as _st  # noqa: PLC0415
        data = await _st.fetch_data(host_row, chip, host_id=host_id,
                                    service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[speedtest_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[speedtest_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    series = data.get("series") if isinstance(data, dict) else None
    if not isinstance(series, list):
        return
    rows = []
    for p in series:
        if not isinstance(p, dict):
            continue
        ts = _ts_epoch(p.get("ts"))
        if ts <= 0:
            continue
        rows.append((ts, host_id, int(service_idx),
                     float(p.get("download") or 0), float(p.get("upload") or 0),
                     float(p.get("ping") or 0), float(p.get("jitter") or 0),
                     float(p.get("packet_loss") or 0)))
    if not rows:
        return
    try:
        with db_conn() as c:
            c.executemany(
                "INSERT OR IGNORE INTO speedtest_samples "
                "(ts, host_id, service_idx, download, upload, ping, jitter, "
                "packet_loss) VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[speedtest_sampler] write {host_id}#{service_idx} failed: {e}")


def _prune_old_samples() -> int:
    """Delete rows older than the retention window. Returns the deleted-row
    count (chunked DELETE via the shared helper)."""
    days = _tuning.tuning_int(_Tunable.SPEEDTEST_HISTORY_DAYS)
    cutoff = int(time.time()) - days * 86400
    return prune_rows_older_than("speedtest_samples", cutoff)


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body: ingest every configured Speedtest chip in parallel, then
    run the hourly retention prune (offloaded to a worker thread)."""
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
        n = await prune_with_metrics("speedtest_sampler", _prune_old_samples)
        if n:
            days = _tuning.tuning_int(_Tunable.SPEEDTEST_HISTORY_DAYS)
            print(f"[speedtest_sampler] pruned {n} rows older than {days}d")


async def speedtest_tracker_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no Speedtest chip is configured (keeps ticking so a
    runtime pin takes effect without a restart)."""
    await lifespan_sampler_loop(
        "speedtest_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Long-horizon download/upload/ping trend for one chip from the local
    history. Returns ``{days, samples, median_download, median_upload,
    median_ping, series, first_ts, last_ts}`` where ``series`` is up to
    ``max_points`` daily-MEDIAN download points (oldest-first, days WITH data
    only — gaps collapse, since a 0-filled day would read as a misleading
    outage on a Mbps line). Zeroed shape when no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.SPEEDTEST_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "median_download": 0.0,
                 "median_upload": 0.0, "median_ping": 0.0, "series": [],
                 "first_ts": 0, "last_ts": 0}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, download, upload, ping FROM speedtest_samples "
                "WHERE host_id=? AND service_idx=? AND ts >= ? ORDER BY ts ASC",
                (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[speedtest_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["first_ts"] = int(rows[0]["ts"])
    out["last_ts"] = int(rows[-1]["ts"])
    out["median_download"] = round(float(median([float(r["download"]) for r in rows])), 2)
    out["median_upload"] = round(float(median([float(r["upload"]) for r in rows])), 2)
    out["median_ping"] = round(float(median([float(r["ping"]) for r in rows])), 1)
    # Daily-MEDIAN download — bucket by day, median per day, oldest-first. Only
    # days with data (gaps collapse rather than 0-fill). Downsample to
    # max_points by striding so a multi-year window stays a tidy sparkline.
    by_day: dict = {}
    for r in rows:
        d = int(r["ts"]) // 86400
        by_day.setdefault(d, []).append(float(r["download"]))
    daily = [round(float(median(by_day[d])), 2) for d in sorted(by_day)]
    if len(daily) > max_points:
        stride = len(daily) / float(max_points)
        daily = [daily[int(i * stride)] for i in range(max_points)]
    out["series"] = daily
    return out
