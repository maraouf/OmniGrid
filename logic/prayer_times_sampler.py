"""Lifespan-managed prayer-times sampler.

Calls ``logic.prayer_times.fetch(lat, lon)`` at
``tuning_prayer_times_sampler_interval_seconds`` cadence (default 21600s
= every 6h) for every distinct user-configured weather location (falling
back to the operator-set default location), then writes ONE row per day
per location into ``prayer_times_samples`` so the Admin → Prayer Times
history table + the AI palette can answer "what time was Fajr last
Friday" without re-hitting api.aladhan.com.

Prayer timings are daily-STATIC for a given (location, method, school),
so the default cadence is 6h rather than the weather sampler's hourly —
the new day's timings still appear within 6h of midnight, and the
composite primary key ``(greg_date, lat, lon, method, school)`` collapses
repeated same-day fetches to one row (an INSERT OR REPLACE just refreshes
``ts``).

Pruned hourly to ``tuning_prayer_times_history_retention_days`` (default
90; 0 disables pruning entirely). Pruning lives in the same loop body as
the writer — same shape as the weather_sampler / public_ip machinery.

Master gate: ``logic.prayer_times.is_enabled()`` short-circuits when the
``prayer_times_enabled`` toggle is off. ``…_sampler_interval_seconds = 0``
disables the sampler entirely (the on-demand cache for SPA / Telegram /
AI calls stays active but no historical rows accumulate).

Skip-don't-synthesize discipline: a failed fetch (network error, gate-off,
missing coords, empty timings) does NOT write a placeholder row. The next
successful tick is the next sample.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from logic.db import db_conn, prune_rows_older_than
from logic.tuning import Tunable, tuning_int
from logic import prayer_times as _pt
from logic import weather as _weather
from logic.sampler_metrics import record_tick as _record_tick, record_prune as _record_prune

# Same first-tick delay shape as weather_sampler — gives boot-time
# schema migrations time to land before the first INSERT.
_FIRST_TICK_DELAY_SECONDS = 30
# Cap an unbounded retention pass so the DELETE never scans the whole
# table by accident (the WHERE ts<? on the ts predicate is O(N)).
_PRUNE_BATCH_LIMIT = 10000


def _sampler_interval_seconds() -> int:
    """Per-use read so an Admin → Config edit applies on the next tick
    without restart."""
    try:
        return int(tuning_int(Tunable.PRAYER_TIMES_SAMPLER_INTERVAL_SECONDS))
    except (KeyError, ValueError, TypeError):
        return 21600


def _retention_days() -> int:
    try:
        return int(tuning_int(Tunable.PRAYER_TIMES_HISTORY_RETENTION_DAYS))
    except (KeyError, ValueError, TypeError):
        return 90


def _quantise(v: float) -> float:
    # 3 decimals — matches logic.prayer_times's cache key (round(lat, 3)).
    return round(float(v), 3)


def write_sample(body: dict, *, loc: Optional[dict] = None) -> bool:
    """Persist ONE successful prayer-times fetch into
    ``prayer_times_samples``.

    Returns True on a real INSERT, False when the body is missing
    required fields (gate-off / fetch error / empty timings). Idempotent
    on the composite primary key ``(greg_date, lat, lon, method,
    school)`` — a re-fetch within the same day collapses to one row.
    """
    if not body or not isinstance(body, dict):
        return False
    if body.get("error") or not body.get("configured"):
        return False
    timings = body.get("timings") or {}
    if not isinstance(timings, dict) or not timings:
        return False
    greg = body.get("gregorian") or {}
    greg_date = str(greg.get("date") or "").strip()
    if not greg_date:
        # No canonical day for this fetch — skip-don't-synthesize.
        return False
    loc_blk = body.get("location") or {}
    loc = loc or {}
    raw_lat = loc_blk.get("lat")
    if raw_lat is None:
        raw_lat = loc.get("lat")
    raw_lon = loc_blk.get("lon")
    if raw_lon is None:
        raw_lon = loc.get("lon")
    if raw_lat in (None, "") or raw_lon in (None, ""):
        return False
    try:
        lat = _quantise(float(str(raw_lat)))
        lon = _quantise(float(str(raw_lon)))
    except (TypeError, ValueError):
        return False
    label = str(loc_blk.get("label") or loc.get("label") or "").strip()
    try:
        method = int(body.get("method") or 0)
        school = int(body.get("school") or 0)
    except (TypeError, ValueError):
        method = 0
        school = 0

    def _time_of(key: str) -> str:
        row = timings.get(key) or {}
        return str(row.get("time") or "").strip() if isinstance(row, dict) else ""

    hijri = body.get("hijri") or {}
    hijri_text = str(hijri.get("text") or "").strip()
    tz = str(body.get("timezone") or "").strip()
    ts = int(body.get("fetched_at") or time.time())
    try:
        with db_conn() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO prayer_times_samples
                  (ts, greg_date, lat, lon, label, method, school,
                   fajr, sunrise, dhuhr, asr, maghrib, isha,
                   hijri_text, timezone)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts, greg_date, lat, lon, label, method, school,
                    _time_of("fajr"), _time_of("sunrise"), _time_of("dhuhr"),
                    _time_of("asr"), _time_of("maghrib"), _time_of("isha"),
                    hijri_text, tz,
                ),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[prayer_times_sampler] WRITE failed at greg_date={greg_date} "
              f"lat={lat} lon={lon}: {e}", flush=True)
        return False
    return True


def prune_old_samples() -> int:
    """DELETE rows older than the retention window. Returns the row count
    removed. Uses the chunked ``prune_rows_older_than`` helper (rowid-IN-SELECT-
    LIMIT shape — works WITHOUT SQLite's optional ``DELETE ... LIMIT`` compile
    flag, and releases the writer lock per ``_PRUNE_BATCH_LIMIT``-row chunk so
    it never holds the lock for the whole backlog)."""
    days = _retention_days()
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86400
    try:
        return prune_rows_older_than("prayer_times_samples", cutoff,
                                     chunk=_PRUNE_BATCH_LIMIT)
    except Exception as e:  # noqa: BLE001
        print(f"[prayer_times_sampler] PRUNE failed cutoff={cutoff}: {e}",
              flush=True)
        return 0


async def sampler_loop() -> None:
    """Lifespan-managed loop. Started via ``_lifespan`` in main.py;
    cancelled cleanly in the matching ``finally`` block.

    Idle behaviour: when the sampler-interval tunable is 0 the loop
    SLEEPS in a long-poll loop and re-reads the tunable per-cycle instead
    of exiting — so an operator who later enables the sampler via Admin →
    Config doesn't have to restart the container.
    """
    await asyncio.sleep(_FIRST_TICK_DELAY_SECONDS)
    last_prune_ts = 0.0
    while True:
        interval = _sampler_interval_seconds()
        if interval <= 0:
            await asyncio.sleep(60)
            continue
        # Master-gate consultation each tick — operator may flip the
        # prayer-times toggle off without restart.
        if not _pt.is_enabled():
            await asyncio.sleep(interval)
            continue
        # Prayer times use each user's existing weather location (same as
        # the widget). Fall back to the operator-set prayer default ONLY
        # when no user has configured a weather location yet. The
        # resolve-or-fall-back step is the shared sampler helper; only the
        # skip-log text below is provider-specific.
        locations = _weather.resolve_sampler_locations(_pt.default_location)
        if not locations:
            print("[prayer_times_sampler] skipped — no user weather "
                  "locations and no Admin → Prayer Times default location",
                  flush=True)
            await asyncio.sleep(interval)
            continue
        _tick_t0 = time.perf_counter()
        _tick_ok = True
        _tick_err = ""
        wrote = 0
        try:
            for loc in locations:
                try:
                    body = await _pt.fetch(loc["lat"], loc["lon"],
                                           label=loc.get("label") or "")
                    if body and not body.get("error") and body.get("configured"):
                        if write_sample(body, loc=loc):
                            wrote += 1
                    elif body and body.get("error"):
                        print(f"[prayer_times_sampler] skipped "
                              f"{loc.get('label') or ''} "
                              f"({loc['lat']},{loc['lon']}) — upstream "
                              f"error: {body.get('error')}", flush=True)
                except Exception as e:  # noqa: BLE001
                    if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
                        raise
                    _tick_ok = False
                    _tick_err = type(e).__name__
                    print(f"[prayer_times_sampler] tick failed for "
                          f"{loc.get('label') or ''} "
                          f"({loc['lat']},{loc['lon']}): {e}", flush=True)
        finally:
            _record_tick(
                "prayer_times_sampler",
                (time.perf_counter() - _tick_t0) * 1000.0,
                ok=_tick_ok,
                error=_tick_err,
            )
        if wrote:
            print(f"[prayer_times_sampler] wrote {wrote} sample(s) across "
                  f"{len(locations)} location(s)", flush=True)
        # Hourly prune sweep — gated independently from the tick.
        now = time.time()
        if (now - last_prune_ts) >= 3600.0:
            _prune_t0 = time.perf_counter()
            removed = await asyncio.to_thread(prune_old_samples)
            _record_prune("prayer_times_sampler", removed,
                          (time.perf_counter() - _prune_t0) * 1000.0)
            last_prune_ts = now
            if removed:
                print(f"[prayer_times_sampler] pruned {removed} sample(s) "
                      f"older than {_retention_days()}d", flush=True)
        await asyncio.sleep(interval)


# noinspection DuplicatedCode
def recent_samples(
    limit: int = 50,
    *,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> list[dict]:
    """Read the most-recent rows from ``prayer_times_samples`` (newest
    first). Used by the Admin → Prayer Times history table + AI palette.

    Optional ``lat`` / ``lon`` narrow to one quantised coordinate;
    omitted = every coordinate.
    """
    rows: list[dict] = []
    cap = int(max(1, min(limit, 5000)))
    try:
        with db_conn() as c:
            if lat is not None and lon is not None:
                cur = c.execute(
                    """
                    SELECT ts,
                           greg_date,
                           lat,
                           lon,
                           label,
                           method,
                           school,
                           fajr,
                           sunrise,
                           dhuhr,
                           asr,
                           maghrib,
                           isha,
                           hijri_text,
                           timezone
                    FROM prayer_times_samples
                    WHERE lat = ?
                      AND lon = ?
                    ORDER BY ts DESC LIMIT ?
                    """,
                    (_quantise(lat), _quantise(lon), cap),
                )
            else:
                cur = c.execute(
                    """
                    SELECT ts,
                           greg_date,
                           lat,
                           lon,
                           label,
                           method,
                           school,
                           fajr,
                           sunrise,
                           dhuhr,
                           asr,
                           maghrib,
                           isha,
                           hijri_text,
                           timezone
                    FROM prayer_times_samples
                    ORDER BY ts DESC LIMIT ?
                    """,
                    (cap,),
                )
            for r in cur.fetchall():
                rows.append({
                    "ts": r[0],
                    "greg_date": r[1] or "",
                    "lat": r[2],
                    "lon": r[3],
                    "label": r[4] or "",
                    "method": r[5],
                    "school": r[6],
                    "fajr": r[7] or "",
                    "sunrise": r[8] or "",
                    "dhuhr": r[9] or "",
                    "asr": r[10] or "",
                    "maghrib": r[11] or "",
                    "isha": r[12] or "",
                    "hijri_text": r[13] or "",
                    "timezone": r[14] or "",
                })
    except Exception as e:  # noqa: BLE001
        print(f"[prayer_times_sampler] recent_samples read failed: {e}",
              flush=True)
    return rows
