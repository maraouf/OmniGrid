"""Lifespan-managed weather sampler.

Calls ``logic.weather.fetch(lat, lon)`` at
``tuning_weather_sampler_interval_seconds`` cadence (default 3600s
= hourly) for the operator-configured DEFAULT location, then writes
one row per tick to the ``weather_samples`` table so the AI palette
+ Telegram ``/weather`` command + future weather-history UI can
answer "what was the temperature here yesterday afternoon", "when
was the last full moon", etc.

Pruned hourly to ``tuning_weather_history_retention_days`` (default
90; 0 disables pruning entirely for "keep every sample forever"
climate-trend deployments). Pruning lives in the same loop body as
the writer — same shape as the host_net_sampler / public_ip
machinery so adding a new metric to the sampler doesn't require
a separate prune task.

Master gate: ``logic.weather.is_enabled()`` short-circuits when the
``weather_enabled`` master toggle is off OR when the API key is
unset. ``tuning_weather_sampler_interval_seconds = 0`` disables the
sampler entirely (the on-demand cache stays active for SPA / Telegram
calls but no historical rows accumulate).

Skip-don't-synthesize discipline: a failed fetch (network error,
gate-off, missing default coords) does NOT write a placeholder row.
The next successful tick is the next sample.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from logic.db import db_conn
from logic.tuning import Tunable, tuning_int
from logic import weather as _weather
from logic.sampler_metrics import record_tick as _record_tick, record_prune as _record_prune


# Same first-tick delay shape as host_baseline_sampler — gives boot-time
# schema migrations time to land before the first INSERT.
_FIRST_TICK_DELAY_SECONDS = 30
# Cap an unbounded retention pass at a reasonable upper bound so the
# DELETE never scans the entire table by accident (the WHERE ts<? on
# an unindexed ts predicate is O(N) per the SQLite-discipline rule).
_PRUNE_BATCH_LIMIT = 10000


def _sampler_interval_seconds() -> int:
    """Per-use read so an Admin → Config edit applies on the next
    tick without restart."""
    try:
        return int(tuning_int(Tunable.WEATHER_SAMPLER_INTERVAL_SECONDS))
    except (KeyError, ValueError, TypeError):
        return 3600


def _retention_days() -> int:
    try:
        return int(tuning_int(Tunable.WEATHER_HISTORY_RETENTION_DAYS))
    except (KeyError, ValueError, TypeError):
        return 90


def _quantise(v: float) -> float:
    return round(float(v), 2)


def write_sample(body: dict, *, loc: Optional[dict] = None) -> bool:
    """Persist ONE successful weather fetch into ``weather_samples``.

    Returns True on a real INSERT, False when the body is missing
    required fields (gate-off / fetch error / unknown coords).
    Idempotent on the composite primary key — a tick that fires
    twice in the same second collapses to one row. The ``loc``
    kwarg carries the (lat, lon, label) used for THIS fetch — the
    sampler iterates over multiple user-locations per tick, so the
    canonical "where did this sample come from" can't be derived
    from a single global default.
    """
    if not body or not isinstance(body, dict):
        return False
    if body.get("error") or not body.get("configured"):
        return False
    # Skip-don't-synthesize: a fetch that came back configured + error-free
    # but with no current temperature (e.g. an upstream in-body error the
    # parser surfaced as null fields, or a malformed 200) must NOT write a
    # null ("—") history row — that pollutes the samples table + the
    # AI / charts read it as real data. The next fetch with actual current
    # data is the next sample.
    if body.get("temp_c") is None:
        return False
    upstream_label = body.get("label") or ""
    if loc is None:
        # Back-compat: caller didn't pass a loc. Fall through to the
        # operator-configured default so single-call writers from
        # pre-multi-user paths still work.
        loc = _weather.default_location()
    if not loc:
        return False
    lat = _quantise(loc["lat"])
    lon = _quantise(loc["lon"])
    ts = int(body.get("fetched_at") or time.time())
    # Carry only the forecast block in raw_json — current/wind/etc
    # already have first-class columns and re-storing them in JSON
    # would double the row size.
    raw = {
        "forecast": body.get("forecast") or [],
        "location": body.get("location") or {},
    }
    moon_phase = ""
    moon_illum: Optional[float] = None
    fcast = body.get("forecast") or []
    if isinstance(fcast, list) and fcast:
        today = fcast[0] or {}
        moon_phase = today.get("moon_phase") or ""
        moon_illum = today.get("moon_illumination")
    try:
        with db_conn() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO weather_samples
                  (ts, lat, lon, label, temp_c, humidity, wind_kmh,
                   condition, code, moon_phase, moon_illumination, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    lat,
                    lon,
                    upstream_label or loc.get("label") or "",
                    body.get("temp_c"),
                    body.get("humidity"),
                    body.get("wind_kmh"),
                    body.get("condition") or "",
                    body.get("code"),
                    moon_phase,
                    moon_illum,
                    json.dumps(raw, separators=(",", ":")),
                ),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[weather_sampler] WRITE failed at ts={ts} lat={lat} lon={lon}: {e}")
        return False
    return True


def prune_old_samples() -> int:
    """DELETE rows older than the retention window. Returns the row
    count actually removed (capped at ``_PRUNE_BATCH_LIMIT`` per call
    so the loop body never burns the event loop for minutes on a
    long-running deploy that's been accumulating samples forever)."""
    days = _retention_days()
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86400
    try:
        with db_conn() as c:
            cur = c.execute(
                "DELETE FROM weather_samples WHERE ts < ? LIMIT ?",
                (cutoff, _PRUNE_BATCH_LIMIT),
            )
            return int(cur.rowcount or 0)
    except Exception as e:  # noqa: BLE001
        # SQLite without DELETE...LIMIT compile-time enable falls back
        # to the unbounded form. Same shape as the host_metrics_sampler
        # prune — better one slow tick than no prune at all.
        try:
            with db_conn() as c:
                cur = c.execute("DELETE FROM weather_samples WHERE ts < ?", (cutoff,))
                return int(cur.rowcount or 0)
        except Exception as e2:  # noqa: BLE001
            print(f"[weather_sampler] PRUNE failed cutoff={cutoff}: {e} / {e2}")
            return 0


async def sampler_loop() -> None:
    """Lifespan-managed loop. Started via ``_lifespan`` in main.py;
    cancelled cleanly in the matching ``finally`` block.

    Idle behaviour: when the sampler-interval tunable is 0 the loop
    SLEEPS in a long-poll loop and re-reads the tunable per-cycle
    instead of exiting — so an operator who later enables the sampler
    via Admin → Config doesn't have to restart the container.
    """
    await asyncio.sleep(_FIRST_TICK_DELAY_SECONDS)
    last_prune_ts = 0.0
    while True:
        interval = _sampler_interval_seconds()
        if interval <= 0:
            # Disabled — re-check every 60s so a tunable flip is
            # picked up promptly without burning CPU.
            await asyncio.sleep(60)
            continue
        # Master-gate consultation each tick — operator may flip the
        # weather toggle off / clear the API key without restart.
        if not _weather.is_enabled():
            await asyncio.sleep(interval)
            continue
        # Iterate every distinct user-configured location across all
        # active users. Falls back to the legacy operator-set default
        # ONLY when no user has configured a weather location yet
        # (first-deploy / pre-bootstrap state).
        locations = _weather.user_locations()
        if not locations:
            legacy_default = _weather.default_location()
            if legacy_default:
                locations = [legacy_default]
        if not locations:
            print("[weather_sampler] skipped — no user-configured weather "
                  "locations (Settings → Profile → Weather) and no legacy "
                  "operator default")
            await asyncio.sleep(interval)
            continue
        # Per-tick health metric (Stats → Samplers). Timed around the
        # actual fetch+write work; the gate / idle short-circuits above
        # don't count as ticks. `ok=False` on the first raising location
        # so a misbehaving upstream surfaces in the panel.
        _tick_t0 = time.perf_counter()
        _tick_ok = True
        _tick_err = ""
        wrote = 0
        try:
            for loc in locations:
                try:
                    body = await _weather.fetch(loc["lat"], loc["lon"],
                                                 label=loc.get("label") or "")
                    if body and not body.get("error") and body.get("configured"):
                        if write_sample(body, loc=loc):
                            wrote += 1
                    elif body and body.get("error"):
                        print(f"[weather_sampler] skipped {loc.get('label') or ''} "
                              f"({loc['lat']},{loc['lon']}) — upstream "
                              f"error: {body.get('error')}")
                except Exception as e:  # noqa: BLE001
                    if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
                        raise
                    _tick_ok = False
                    _tick_err = type(e).__name__
                    print(f"[weather_sampler] tick failed for "
                          f"{loc.get('label') or ''} "
                          f"({loc['lat']},{loc['lon']}): {e}")
        finally:
            _record_tick(
                "weather_sampler",
                (time.perf_counter() - _tick_t0) * 1000.0,
                ok=_tick_ok,
                error=_tick_err,
            )
        if wrote:
            print(f"[weather_sampler] wrote {wrote} sample(s) across "
                  f"{len(locations)} location(s)")
        # Hourly prune sweep — gated independently from the tick so
        # a sub-hourly tunable doesn't multiply prune cost.
        now = time.time()
        if (now - last_prune_ts) >= 3600.0:
            _prune_t0 = time.perf_counter()
            removed = prune_old_samples()
            _record_prune("weather_sampler", removed,
                          (time.perf_counter() - _prune_t0) * 1000.0)
            last_prune_ts = now
            if removed:
                print(f"[weather_sampler] pruned {removed} sample(s) "
                      f"older than {_retention_days()}d")
        await asyncio.sleep(interval)


def recent_samples(
    limit: int = 50,
    *,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> list[dict]:
    """Read the most-recent samples from `weather_samples`.

    Used by the AI palette context-builder + Admin → Weather history
    table + Telegram `/weather` historical-comparison feature. The
    optional ``lat`` / ``lon`` arguments narrow to one quantised
    coordinate; omitted = "every coordinate" (operator may run
    multiple sampler tracks in future).
    """
    rows: list[dict] = []
    try:
        with db_conn() as c:
            if lat is not None and lon is not None:
                cur = c.execute(
                    """
                    SELECT ts, lat, lon, label, temp_c, humidity, wind_kmh,
                           condition, code, moon_phase, moon_illumination
                      FROM weather_samples
                     WHERE lat = ? AND lon = ?
                     ORDER BY ts DESC LIMIT ?
                    """,
                    (_quantise(lat), _quantise(lon), int(max(1, min(limit, 5000)))),
                )
            else:
                cur = c.execute(
                    """
                    SELECT ts, lat, lon, label, temp_c, humidity, wind_kmh,
                           condition, code, moon_phase, moon_illumination
                      FROM weather_samples
                     ORDER BY ts DESC LIMIT ?
                    """,
                    (int(max(1, min(limit, 5000))),),
                )
            for r in cur.fetchall():
                rows.append({
                    "ts": r[0],
                    "lat": r[1],
                    "lon": r[2],
                    "label": r[3] or "",
                    "temp_c": r[4],
                    "humidity": r[5],
                    "wind_kmh": r[6],
                    "condition": r[7] or "",
                    "code": r[8],
                    "moon_phase": r[9] or "",
                    "moon_illumination": r[10],
                })
    except Exception as e:  # noqa: BLE001
        print(f"[weather_sampler] recent_samples read failed: {e}")
    return rows
