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

from logic.db import db_conn, prune_rows_older_than
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


def _prayer_reminder_opted_in(prefs: dict) -> bool:
    """True only when the user EXPLICITLY enabled the ``prayer_reminder``
    event in Profile -> Notifications.

    Prayer reminders are a PERSONAL, location-specific notification, so
    they require explicit per-user opt-in — UNLIKE a system event (e.g.
    ``user_login``) that fires for every user once the admin enables it.
    Without this gate the reminder fired for EVERY active account (and for
    users with no location, via the Admin-default-location fallback), and
    because Telegram + the in-app store are SHARED single-destination
    channels, all of those piled into one chat as duplicates (operator saw
    the same prayer twice — one per account / location).

    Matches ``logic.ops.notify``'s per-user pref shapes so the two agree:
    a bare ``True`` OR a ``{medium: bool}`` dict with at least one medium
    enabled = opted in; absent / ``False`` / an all-false dict = NOT opted
    in (the default — privacy + no-spam, restoring the original intent)."""
    events = prefs.get("notify_events")
    if not isinstance(events, dict):
        return False
    pref = events.get("prayer_reminder")
    if isinstance(pref, dict):
        # {medium: bool} — opted in if at least one medium is enabled.
        return any(bool(v) for v in pref.values())
    # A bare boolean True = opted in; absent / False / anything else = not.
    return pref is True


def _located_users() -> list[dict]:
    """Every active user who EXPLICITLY opted into prayer reminders AND has
    a resolvable location. Returns ``[{username, lat, lon, label}]``.

    Opt-in gate: only users with an explicit ``prayer_reminder`` enable in
    Profile -> Notifications are included (see ``_prayer_reminder_opted_in``).
    This is the load-bearing fix for the "reminder fires for every active
    account into one shared Telegram chat" duplicate — a personal event
    must be per-user opt-in, not default-on once the admin enables it.

    Location precedence mirrors the prayer widget: the user's saved
    location (``ui_prefs.userLat/userLon/userLabel``) -> the
    Admin -> Prayer Times default location. A user with no resolvable
    location is skipped.

    Delivery + per-medium routing is STILL ``logic.ops.notify``'s job (it
    re-applies the global Admin -> Notifications gate AND the same per-user
    routing) — this pre-filter just stops the default-on flood before it
    reaches the shared channels.
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
        # Explicit per-user opt-in gate — the fix for the duplicate-into-
        # one-shared-chat bug. A user who never enabled prayer_reminder in
        # their Profile -> Notifications no longer fires (default-on flood
        # removed); this also suppresses the Admin-default-location fallback
        # firing for accounts that never asked for reminders.
        if not _prayer_reminder_opted_in(prefs):
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
        # Chunked delete (writer lock released per chunk) — seeks
        # idx_prayer_reminders_sent_ts.
        return prune_rows_older_than("prayer_reminders_sent", cutoff)
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


def _format_prayer_time(username: str, hhmm: str) -> str:
    """Reformat the location-local ``HH:MM`` prayer time to the requesting
    user's datetime preference (12h/24h + AM/PM), preserving the EXACT
    time — only the display format changes, no timezone conversion (the
    ``hhmm`` is already the location's local prayer time). Falls back to the
    raw ``hhmm`` on any parse issue so a reminder never blanks its time."""
    s = (hhmm or "").strip()
    if not s:
        return s
    try:
        import re as _re  # noqa: PLC0415
        from datetime import datetime as _dt  # noqa: PLC0415
        from logic.datetime_fmt import (  # noqa: PLC0415
            apply_datetime_format, strip_date_tokens, get_user_datetime_format)
        m = _re.match(r"\s*(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?", s)
        if not m:
            return s
        hh, mm, ss = int(m.group("h")), int(m.group("m")), int(m.group("s") or 0)
        time_fmt = strip_date_tokens(get_user_datetime_format(username))
        rendered = apply_datetime_format(_dt(2000, 1, 1, hh, mm, ss), time_fmt)
        return rendered or s
    except (ValueError, TypeError, IndexError):
        return s


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
    detail = f"{name} at {_format_prayer_time(username, hhmm)}"
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
