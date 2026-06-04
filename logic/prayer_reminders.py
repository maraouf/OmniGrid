"""Lifespan-managed prayer-reminder loop.

Sends each user a notification ``tuning_prayer_times_reminder_lead_minutes``
minutes BEFORE every obligatory prayer. Per-user opt-in + per-user medium
selection live in Profile -> Notifications, persisted as
``ui_prefs.prayer_reminders = {enabled: bool, mediums: {app, telegram,
apprise}}`` (default OFF — privacy + no-spam). The lead time is the global
Admin -> Prayer Times tunable; 0 disables reminders entirely.

Delivery rides the existing notification mediums via
``logic.ops.notify_one_medium`` so each medium's global master switch
(Admin -> Notifications) is still honoured, Apprise routes to the user's
e-mail (``actor_username`` -> e-mail lookup), Telegram goes to the
configured chat, and the in-app store writes a global row (acceptable —
the in-app notifications table is intentionally global).

Dedup: ``prayer_reminders_sent (username, greg_date, prayer_key)`` records
each delivered reminder so the frequent check ticks AND a container
restart mid-window can never double-fire. One attempt per (user, day,
prayer); old rows are pruned.

Gated on ``logic.prayer_times.is_enabled()`` — when Prayer Times is off,
the loop sleeps cheaply. Skip-don't-synthesize: a fetch failure for a
user this tick simply means no reminder for that prayer this tick; the
next tick retries until the prayer passes.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from logic.db import db_conn
from logic.tuning import Tunable, tuning_int
from logic import prayer_times as _pt

# Obligatory prayers, in order — Sunrise is informational, never reminded.
_PRAYER_KEYS = ("fajr", "dhuhr", "asr", "maghrib", "isha")
# First-tick delay — let boot-time schema migrations land before the
# first read, same shape as the samplers.
_FIRST_TICK_DELAY_SECONDS = 30
# Keep the dedup ledger small — the loop only cares about today; a couple
# of days of history is plenty of restart-safety margin.
_DEDUP_RETENTION_DAYS = 3
_PRUNE_EVERY_SECONDS = 3600.0


def _lead_minutes() -> int:
    try:
        return int(tuning_int(Tunable.PRAYER_TIMES_REMINDER_LEAD_MINUTES))
    except (KeyError, ValueError, TypeError):
        return 10


def _check_interval_seconds() -> int:
    try:
        return int(tuning_int(Tunable.PRAYER_TIMES_REMINDER_CHECK_INTERVAL_SECONDS))
    except (KeyError, ValueError, TypeError):
        return 30


def _located_users() -> list[dict]:
    """Every active user with a resolvable location. Returns
    ``[{username, lat, lon, label}]``.

    Location precedence mirrors the prayer widget: the user's saved
    location (``ui_prefs.userLat/userLon/userLabel``) -> the
    Admin -> Prayer Times default location. A user with no resolvable
    location is skipped.

    NOTE: this does NOT filter by an opt-in flag — delivery + per-user
    routing is the job of ``logic.ops.notify(event='prayer_reminder',
    actor_username=...)``, which applies the global Admin -> Notifications
    gate AND each user's per-medium routing from Profile -> Notifications
    (exactly like every other notify event). The whole feature is
    short-circuited up front by the global gate, so this only runs when an
    admin has enabled the prayer-reminder event.
    """
    out: list[dict] = []
    default_loc: Optional[dict] = None
    default_loaded = False
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT username, ui_prefs FROM users "
                "WHERE (disabled IS NULL OR disabled = 0)"
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[prayer_reminders] user read failed: {e}", flush=True)
        return out
    for r in rows:
        username = r[0] if not isinstance(r, dict) else r["username"]
        raw = r[1] if not isinstance(r, dict) else r["ui_prefs"]
        if not username or not raw:
            continue
        try:
            prefs = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(prefs, dict):
            continue
        lat_raw = prefs.get("userLat")
        lon_raw = prefs.get("userLon")
        label = str(prefs.get("userLabel") or "").strip()
        lat: Optional[float] = None
        lon: Optional[float] = None
        if lat_raw not in (None, "") and lon_raw not in (None, ""):
            try:
                lat = float(str(lat_raw))
                lon = float(str(lon_raw))
            except (TypeError, ValueError):
                lat = lon = None
        if lat is None or lon is None:
            if not default_loaded:
                default_loc = _pt.default_location()
                default_loaded = True
            if not default_loc:
                continue
            lat = float(default_loc["lat"])
            lon = float(default_loc["lon"])
            if not label:
                label = str(default_loc.get("label") or "").strip()
        out.append({"username": username, "lat": lat, "lon": lon,
                    "label": label})
    return out


def _already_sent(username: str, greg_date: str, prayer_key: str) -> bool:
    try:
        with db_conn() as c:
            row = c.execute(
                "SELECT 1 FROM prayer_reminders_sent "
                "WHERE username = ? AND greg_date = ? AND prayer_key = ?",
                (username, greg_date, prayer_key),
            ).fetchone()
            return row is not None
    except Exception as e:  # noqa: BLE001
        # Fail "already sent" so a DB blip can't turn into a spam loop —
        # a missed reminder is better than a flood.
        print(f"[prayer_reminders] dedup read failed for "
              f"{username}/{greg_date}/{prayer_key}: {e}", flush=True)
        return True


def _mark_sent(username: str, greg_date: str, prayer_key: str) -> None:
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO prayer_reminders_sent "
                "(username, greg_date, prayer_key, ts) VALUES (?, ?, ?, ?)",
                (username, greg_date, prayer_key, int(time.time())),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[prayer_reminders] dedup write failed for "
              f"{username}/{greg_date}/{prayer_key}: {e}", flush=True)


def _prune_dedup() -> int:
    cutoff = int(time.time()) - _DEDUP_RETENTION_DAYS * 86400
    try:
        with db_conn() as c:
            cur = c.execute(
                "DELETE FROM prayer_reminders_sent WHERE ts < ?", (cutoff,)
            )
            return int(cur.rowcount or 0)
    except Exception as e:  # noqa: BLE001
        print(f"[prayer_reminders] prune failed: {e}", flush=True)
        return 0


def _event_enabled() -> bool:
    """Global Admin -> Notifications gate for the ``prayer_reminder`` event.
    Default OFF (admin opt-in, like ``user_login``) so the loop does no work
    + fires nothing until an admin enables it."""
    try:
        from logic.db import get_setting_bool  # noqa: PLC0415
        from logic.settings_keys import notify_event_key  # noqa: PLC0415
        # get_setting_bool defaults to False — the prayer_reminder event is
        # admin-opt-in, so an unset setting reads as OFF.
        return get_setting_bool(notify_event_key("prayer_reminder"))
    except (KeyError, ValueError, TypeError):
        return False


async def _fire_reminder(username: str, *, name: str, hhmm: str, lead: int,
                         label: str, prayer_key: str) -> None:
    """Fire ONE prayer reminder through the standard notify() event system.

    Delivery + per-user routing (which mediums) is entirely notify()'s job:
    it applies the global Admin -> Notifications gate AND the user's
    per-medium routing for the ``prayer_reminder`` event from
    Profile -> Notifications — exactly like every other notify event. The
    title/body feed the template placeholders: the ``"Prayer: <name>"``
    title becomes ``{name}`` (the notify template engine splits on ": ");
    the detail line becomes ``{message}``."""
    from logic.ops import notify  # noqa: PLC0415
    minutes = "minute" if lead == 1 else "minutes"
    detail = f"{name} at {hhmm}"
    if label:
        detail += f" · {label}"
    detail += f" — in {lead} {minutes}"
    await notify(
        f"Prayer: {name}",
        detail,
        event="prayer_reminder",
        actor_username=username,
        target_kind="prayer",
        target_id=prayer_key,
        metadata={"prayer": prayer_key, "host": label},
    )


async def reminder_loop() -> None:
    """Lifespan-managed loop. Started via ``_lifespan`` in main.py;
    cancelled cleanly in the matching ``finally`` block."""
    await asyncio.sleep(_FIRST_TICK_DELAY_SECONDS)
    last_prune_ts = 0.0
    while True:
        interval = _check_interval_seconds()
        lead = _lead_minutes()
        # Three gates, all cheap, all re-read per tick so an Admin change
        # applies without restart: 0 lead disables the feature; Prayer Times
        # must be enabled (to fetch); the prayer_reminder notify event must
        # be enabled globally (Admin -> Notifications, default OFF). The
        # event gate short-circuits ALL per-user work + delivery.
        if lead <= 0 or not _pt.is_enabled() or not _event_enabled():
            await asyncio.sleep(max(interval, 15))
            continue
        lead_seconds = lead * 60
        now = int(time.time())
        users = _located_users()
        if users:
            # Cache fetched bodies per (lat,lon) within this tick so two
            # users in the same city share one upstream call. Keyed by a
            # quantised "lat,lon" string so the dict's key type stays a
            # plain str (avoids the tuple-key inference noise).
            body_cache: dict[str, dict] = {}
            for u in users:
                key = f"{round(u['lat'], 3)},{round(u['lon'], 3)}"
                pbody = body_cache.get(key)
                if pbody is None:
                    try:
                        pbody = await _pt.fetch(u["lat"], u["lon"],
                                                label=u.get("label") or "")
                    except Exception as e:  # noqa: BLE001
                        if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
                            raise
                        print(f"[prayer_reminders] fetch failed for "
                              f"{u['username']} ({key}): {e}", flush=True)
                        pbody = {}
                    body_cache[key] = pbody
                if not pbody or pbody.get("error") or not pbody.get("configured"):
                    continue
                greg = (pbody.get("gregorian") or {}).get("date") or ""
                timings = pbody.get("timings") or {}
                if not greg or not isinstance(timings, dict):
                    continue
                for pk in _PRAYER_KEYS:
                    row = timings.get(pk) or {}
                    ts = row.get("ts")
                    if not isinstance(ts, int):
                        continue
                    reminder_ts = ts - lead_seconds
                    # Inside the lead window, prayer not yet passed.
                    if not (reminder_ts <= now < ts):
                        continue
                    if _already_sent(u["username"], greg, pk):
                        continue
                    await _fire_reminder(
                        u["username"],
                        name=row.get("name") or pk.title(),
                        hhmm=row.get("time") or "",
                        lead=lead, label=u.get("label") or "", prayer_key=pk)
                    # One notify() per (user, day, prayer); notify() itself
                    # applies the per-user routing / per-medium gates, so the
                    # dedup mark is unconditional (no re-attempt spam).
                    _mark_sent(u["username"], greg, pk)
                    print(f"[prayer_reminders] {u['username']}: {pk} "
                          f"reminder fired (lead {lead}m)", flush=True)
        # Periodic dedup prune.
        nowf = time.time()
        if (nowf - last_prune_ts) >= _PRUNE_EVERY_SECONDS:
            removed = await asyncio.to_thread(_prune_dedup)
            last_prune_ts = nowf
            if removed:
                print(f"[prayer_reminders] pruned {removed} old dedup row(s)",
                      flush=True)
        await asyncio.sleep(max(interval, 15))
