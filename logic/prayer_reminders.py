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
_VALID_MEDIUMS = ("app", "telegram", "apprise")
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


def _enabled_users() -> list[dict]:
    """Active users who opted into prayer reminders with >=1 medium, each
    resolved to a concrete location. Returns
    ``[{username, lat, lon, label, mediums}]``.

    Location precedence mirrors the prayer widget: the user's saved
    location (``ui_prefs.userLat/userLon/userLabel``) -> the
    Admin -> Prayer Times default location. A user with reminders enabled
    but no resolvable location is skipped.
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
        pr = prefs.get("prayer_reminders")
        if not isinstance(pr, dict) or not pr.get("enabled"):
            continue
        mediums_pref = pr.get("mediums") or {}
        mediums = [m for m in _VALID_MEDIUMS
                   if isinstance(mediums_pref, dict) and mediums_pref.get(m)]
        if not mediums:
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
                    "label": label, "mediums": mediums})
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


async def _deliver(username: str, mediums: list[str], *,
                   title: str, body: str, prayer_key: str) -> list[str]:
    """Fire the reminder to each selected medium via the public
    ``notify_one_medium`` primitive (honours each medium's global master
    switch). Returns the list of mediums that actually delivered."""
    from logic.ops import notify_one_medium
    metadata = {"kind": "prayer_reminder", "prayer": prayer_key}
    delivered: list[str] = []
    for m in mediums:
        try:
            res = await notify_one_medium(
                m, title, body,
                actor_username=username, metadata=metadata,
            )
        except Exception as e:  # noqa: BLE001
            if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
                raise
            print(f"[prayer_reminders] medium '{m}' for {username} "
                  f"errored: {e}", flush=True)
            continue
        if isinstance(res, dict) and res.get("ok"):
            delivered.append(m)
        else:
            detail = (res or {}).get("detail") if isinstance(res, dict) else None
            print(f"[prayer_reminders] medium '{m}' for {username}/"
                  f"{prayer_key} not delivered: {detail}", flush=True)
    return delivered


def _reminder_text(name: str, hhmm: str, lead: int, label: str) -> tuple[str, str]:
    """English title/body (backend notification convention). Title carries
    the lead countdown; body the exact time + location. ``name`` is the
    already-resolved display name of the prayer (from the fetch's timing
    row) so this helper doesn't reach into prayer_times' internals."""
    minutes = "minute" if lead == 1 else "minutes"
    title = f"🕌 {name} in {lead} {minutes}"
    body = f"{name} at {hhmm}" + (f" · {label}" if label else "")
    return title, body


async def reminder_loop() -> None:
    """Lifespan-managed loop. Started via ``_lifespan`` in main.py;
    cancelled cleanly in the matching ``finally`` block."""
    await asyncio.sleep(_FIRST_TICK_DELAY_SECONDS)
    last_prune_ts = 0.0
    while True:
        interval = _check_interval_seconds()
        lead = _lead_minutes()
        # 0 lead disables the feature; sleep at the configured cadence.
        if lead <= 0 or not _pt.is_enabled():
            await asyncio.sleep(max(interval, 15))
            continue
        lead_seconds = lead * 60
        now = int(time.time())
        users = _enabled_users()
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
                    title, b = _reminder_text(
                        row.get("name") or pk.title(),
                        row.get("time") or "", lead, u.get("label") or "")
                    delivered = await _deliver(
                        u["username"], u["mediums"],
                        title=title, body=b, prayer_key=pk)
                    # One attempt per (user, day, prayer) regardless of
                    # per-medium outcome — avoids re-attempt spam every tick
                    # when a selected medium is globally disabled.
                    _mark_sent(u["username"], greg, pk)
                    print(f"[prayer_reminders] {u['username']}: {pk} "
                          f"reminder (lead {lead}m) -> "
                          f"{delivered or 'no medium delivered'}", flush=True)
        # Periodic dedup prune.
        nowf = time.time()
        if (nowf - last_prune_ts) >= _PRUNE_EVERY_SECONDS:
            removed = await asyncio.to_thread(_prune_dedup)
            last_prune_ts = nowf
            if removed:
                print(f"[prayer_reminders] pruned {removed} old dedup row(s)",
                      flush=True)
        await asyncio.sleep(max(interval, 15))
