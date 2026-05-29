"""Scheduled jobs — admin-defined recurring operations.

A tiny cron-less scheduler that lives in-process alongside the rest of
OmniGrid. Admins create named schedules (kind + params + interval)
through the admin UI; a lifespan-managed tick loop checks every minute
whether any are due, and fires them through the existing
:mod:`logic.ops` system so their runs appear in the live ops panel and
the persisted ``history`` table exactly like a user-triggered click.

Design notes:

  - Cadence is a plain ``interval_seconds`` column, NOT a cron
    expression. Cron semantics (calendar-aware, timezone-aware) are
    non-trivial; the homelab use case is "every N minutes/hours/days"
    which an integer nails without a parser dependency. If someone
    genuinely needs cron, wire the `croniter` library and add a
    `cron_expr` column alongside `interval_seconds`.

  - Each fire reuses :func:`logic.ops.new_op` with ``actor="scheduler"``
    so UI + Apprise + history + metrics all get the same treatment as
    user-triggered ops. There is NO parallel run-tracking system.

  - No concurrency limit. If a schedule's previous fire is still in
    flight and the next tick comes up, we fire it again. Good enough
    for homelabs; a future upgrade would skip-if-running per kind.

  - ``last_run_at`` is stamped the moment we fire, not when the op
    completes. This prevents a long-running op from causing a burst
    of back-to-back re-fires on the next tick. Duration + status are
    stamped by an async waiter coroutine when the op finishes.
"""
import asyncio
import calendar
import json
import re
import secrets
import sqlite3
import time
from typing import Any, Awaitable, Callable, Optional

from logic import backups, gather, ops as _ops
from logic.db import db_conn
from logic.settings_keys import Settings

# Clock-time schedules use "HH:MM" — 24-hour, container local time. Matches
# what a human types when they say "run at 1 AM". We don't accept seconds;
# per-second precision is meaningless against a 60-second tick loop.
_HHMM_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

# Cadence modes — mutually exclusive. 'interval' is the legacy path
# (interval_seconds); the others all pin to a clock-time anchor
# (run_at_hhmm) plus a calendar filter. Day-of-week uses Python's
# tm_wday convention: Mon=0 .. Sun=6. The frontend maps the ints to
# localised labels via i18n.
CADENCE_MODES = ("interval", "daily", "weekly", "monthly")


def _parse_hhmm(s: Optional[str]) -> Optional[tuple[int, int]]:
    """Return (hh, mm) for a valid "HH:MM" string, else None.

    Called from both the API validator and the tick loop; keep the
    format contract in exactly one place.
    """
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    if not _HHMM_RE.match(s):
        raise ValueError("run_at_hhmm must be in 'HH:MM' 24-hour format")
    hh, mm = s.split(":")
    return int(hh), int(mm)


def _scheduler_tz():
    """Return the timezone the operator set via Settings → scheduler_timezone,
    or None to fall back to the container's local clock (the legacy
    behaviour). Looks up lazily on every call so changing the setting
    takes effect without a restart — schedule rows are infrequent.

    Accepts any IANA name (``Africa/Cairo``, ``America/New_York``, ...).
    Invalid names silently fall back to None with a one-time log so a
    typo doesn't break the scheduler loop.
    """
    try:
        from logic.db import get_setting
        tz_name = (get_setting(Settings.SCHEDULER_TIMEZONE) or "").strip()
    except (sqlite3.Error, RuntimeError, ImportError):
        return None
    if not tz_name:
        return None
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as e:
        # Log once per process — otherwise a bad TZ spams the tick loop.
        global _tz_warn_logged
        if not globals().get("_tz_warn_logged"):
            print(f"[scheduler] invalid scheduler_timezone={tz_name!r}: {e} — "
                  f"falling back to container-local time")
            _tz_warn_logged = True
        return None


_tz_warn_logged = False

# Public alias for cross-module use (main.py resolves the scheduler tz
# in two date-aware endpoints).
scheduler_tz = _scheduler_tz


def scheduler_tz_state() -> dict:
    """Structured snapshot of the scheduler-timezone resolution.

    Returns ``{configured, resolved, fallback}``. ``configured`` is the
    raw setting string (empty when unset), ``resolved`` is the IANA
    name actually used (None when blank or invalid), ``fallback`` is
    True when the operator typed something but ZoneInfo rejected it
    (so the scheduler is running on container-local time despite the
    operator's intent). Surfaced in ``/api/me``'s ``client_config``
    so the admin Schedules tab can badge the mismatch — without it, the once-per-
    process invalid-TZ log line is invisible unless the operator
    grep's Admin → Logs at the right moment.
    """
    try:
        from logic.db import get_setting
        configured = (get_setting(Settings.SCHEDULER_TIMEZONE) or "").strip()
    except (sqlite3.Error, RuntimeError, ImportError):
        return {"configured": "", "resolved": None, "fallback": False}
    if not configured:
        return {"configured": "", "resolved": None, "fallback": False}
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(configured)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return {"configured": configured, "resolved": None, "fallback": True}
    return {"configured": configured, "resolved": configured, "fallback": False}


def _today_anchor_ts(hh: int, mm: int, now: Optional[float] = None) -> float:
    """Epoch seconds for today's HH:MM.

    If the operator has set ``scheduler_timezone`` in Settings (an IANA
    name like ``Africa/Cairo``), the anchor is computed in THAT zone
    so "01:00" means "01:00 in the operator's wall clock", not "01:00
    container-local" (containers run UTC by default). If no TZ is set,
    the legacy behaviour (container localtime via mktime) applies.

    DST-safe either way: zoneinfo + datetime handles transitions
    natively; the legacy mktime path uses isdst=-1 to let libc decide.
    """
    import datetime
    # Concrete-float local so the type checker doesn't see `Optional[float]`
    # flow into `datetime.fromtimestamp` + `time.localtime` below.
    # Declare-then-assign so the annotation applies BEFORE the if/else;
    # otherwise Pyright sees `now_ts: float` only on the `if` branch and
    # infers the merge as `float | float | None` from the `else` re-bind.
    now_ts: float
    if now is None:
        now_ts = time.time()
    else:
        now_ts = float(now)
    tz = _scheduler_tz()
    if tz is not None:
        now_local = datetime.datetime.fromtimestamp(now_ts, tz=tz)
        anchor = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return anchor.timestamp()
    # Legacy path — container-local clock via time.mktime.
    t = time.localtime(now_ts)
    return time.mktime((
        t.tm_year, t.tm_mon, t.tm_mday,
        hh, mm, 0, 0, 0, -1,
    ))


def _next_fixed_time_run(
    hh: int, mm: int, last_run_at: Optional[int], now: Optional[float] = None,
) -> int:
    """Next epoch-seconds moment at which a daily HH:MM schedule should fire.

    Due-check contract: ``next <= now`` → fire. Four cases:
      1. Today's anchor is still in the future → next = today's anchor.
      2. We're inside the grace window past today's anchor AND there's
         no recorded run for it → next = today's anchor (fire now).
         Grace = ``TICK_INTERVAL_SECONDS * 2`` (≈120s by default) so a
         scheduler tick that lands ~30 seconds after the anchor still
         catches today's fire window.
         the grace window, daily / weekly / monthly schedules never
         fired because the strictly-less-than check skipped the
         anchor on every tick that landed after it.
      3. Today's anchor already passed AND we're past the grace
         window → next = tomorrow's anchor. We DON'T catch up on missed
         runs beyond the grace window; an operator who creates a
         "nightly 01:00" schedule at noon shouldn't have it fire
         immediately — they can click Run now to backfill.
      4. Today's anchor already passed AND a run is recorded → next =
         tomorrow's anchor.

    Uses calendar-date arithmetic (not ``+ 86400`` seconds) so DST
    transitions in the host timezone don't drift the wall-clock anchor.
    """
    import datetime
    # Declare-then-assign so the annotation applies BEFORE the if/else;
    # otherwise Pyright sees `now_ts: float` only on the `if` branch and
    # infers the merge as `float | float | None` from the `else` re-bind.
    now_ts: float
    if now is None:
        now_ts = time.time()
    else:
        now_ts = float(now)
    anchor = _today_anchor_ts(hh, mm, now_ts)
    last = int(last_run_at or 0)
    # Grace window: the tick interval is 60s, so the tick that lands
    # right after a fixed-time anchor (e.g. anchor=01:00:00, tick runs
    # at 01:00:30) was previously seeing `now > anchor` and skipping
    # to tomorrow — the daily/weekly/monthly schedule never fired in
    # practice. We allow up to 2× the tick interval as a "we just
    # crossed the anchor, fire now" window. Beyond that → tomorrow
    # (preserves the original no-catch-up contract for restarts /
    # late-edited schedules).
    grace = TICK_INTERVAL_SECONDS * 2
    if last < anchor and now_ts < anchor + grace:
        return int(anchor)
    # Derive "tomorrow" in the same zone the anchor was computed in —
    # using container-local here would drift the date boundary by the
    # TZ-offset difference (e.g. 22:00 Cairo = next day in Los_Angeles
    # container, so we'd skip a day). _scheduler_tz() returns None on
    # legacy deploys, falling back to time.localtime.
    tz = _scheduler_tz()
    if tz is not None:
        now_local = datetime.datetime.fromtimestamp(now_ts, tz=tz)
        tomorrow = now_local.date() + datetime.timedelta(days=1)
    else:
        t = time.localtime(now_ts)
        tomorrow = datetime.date(t.tm_year, t.tm_mon, t.tm_mday) + datetime.timedelta(days=1)
    return int(_day_anchor_ts(hh, mm, tomorrow.year, tomorrow.month, tomorrow.day))


def _day_anchor_ts(hh: int, mm: int, y: int, m: int, d: int) -> float:
    """Epoch seconds for a given Y/M/D at HH:MM.

    Honours the same ``scheduler_timezone`` setting as
    :func:`_today_anchor_ts` — critical for the tomorrow / next-week
    / next-month anchors used by the weekly and monthly cadence
    helpers. Without this, tomorrow's 01:00 would be computed in
    container-local time even when ``today's`` 01:00 was computed in
    the operator's TZ, producing a drift on the very next fire.
    """
    import datetime
    tz = _scheduler_tz()
    if tz is not None:
        anchor = datetime.datetime(y, m, d, hh, mm, tzinfo=tz)
        return anchor.timestamp()
    return time.mktime((y, m, d, hh, mm, 0, 0, 0, -1))


def _next_weekly_run(
    hh: int, mm: int, days_of_week: list[int],
    last_run_at: Optional[int], now: Optional[float] = None,
) -> int:
    """Next HH:MM anchor on any day in ``days_of_week`` (Python Mon=0..Sun=6).

    Same no-catch-up contract as :func:`_next_fixed_time_run` — an
    anchor that already passed today without running is NOT returned;
    we jump ahead to the next qualifying day. Scans up to 8 days so a
    full week is always covered regardless of where ``last_run_at`` sits.
    """
    # Declare-then-assign so the annotation applies BEFORE the if/else;
    # otherwise Pyright sees `now_ts: float` only on the `if` branch and
    # infers the merge as `float | float | None` from the `else` re-bind.
    now_ts: float
    if now is None:
        now_ts = time.time()
    else:
        now_ts = float(now)
    last = int(last_run_at or 0)
    if not days_of_week:
        # No days selected — fall back to daily so a misconfigured row
        # doesn't silently never fire.
        return _next_fixed_time_run(hh, mm, last_run_at, now_ts)
    dow_set = {int(d) for d in days_of_week if 0 <= int(d) <= 6}
    import datetime
    # `base_date` MUST come from the same timezone the anchor calc
    # uses (`_day_anchor_ts` honours `_scheduler_tz()`). Without
    # alignment, container-local time near midnight in operator-TZ
    # can produce the wrong day-of-week and either fire a day early
    # / late or skip the firing day entirely.
    tz = _scheduler_tz()
    if tz is not None:
        nowdt = datetime.datetime.fromtimestamp(now_ts, tz=tz)
        base_date = nowdt.date()
    else:
        today = time.localtime(now_ts)
        base_date = datetime.date(today.tm_year, today.tm_mon, today.tm_mday)
    # Same grace window as `_next_fixed_time_run` — the tick that lands
    # 30s after a weekly anchor needs to still recognise today as the
    # firing day, otherwise it skips to next week.
    grace = TICK_INTERVAL_SECONDS * 2
    for offset in range(8):
        d = base_date + datetime.timedelta(days=offset)
        # Python weekday(): Mon=0..Sun=6 — matches our storage convention.
        if d.weekday() not in dow_set:
            continue
        anchor = _day_anchor_ts(hh, mm, d.year, d.month, d.day)
        if anchor + grace <= now_ts:  # past anchor + grace → skip
            continue
        if anchor <= last:  # already fired for this anchor
            continue
        return int(anchor)
    # Defensive fallback — shouldn't happen since at least one day is valid
    return int(now_ts + 86400)


def _next_monthly_run(
    hh: int, mm: int, day_of_month: int,
    last_run_at: Optional[int], now: Optional[float] = None,
) -> int:
    """Next HH:MM anchor on ``day_of_month`` (clamped to last day of month).

    Day 31 on a 30-day month clamps to 30; Feb 31 clamps to 28/29. Scans
    up to 13 months. Same no-catch-up contract as the daily/weekly
    helpers: a passed anchor today with no run skips to next month.
    """
    # Declare-then-assign so the annotation applies BEFORE the if/else;
    # otherwise Pyright sees `now_ts: float` only on the `if` branch and
    # infers the merge as `float | float | None` from the `else` re-bind.
    now_ts: float
    if now is None:
        now_ts = time.time()
    else:
        now_ts = float(now)
    last = int(last_run_at or 0)
    dom = max(1, min(int(day_of_month), 31))
    # `y, m` MUST come from the same timezone the anchor calc uses —
    # at month boundaries, container-local UTC can disagree with
    # operator-TZ and pick the wrong calendar month.
    tz = _scheduler_tz()
    if tz is not None:
        import datetime
        nowdt = datetime.datetime.fromtimestamp(now_ts, tz=tz)
        y, m = nowdt.year, nowdt.month
    else:
        t = time.localtime(now_ts)
        y, m = t.tm_year, t.tm_mon
    # Same grace window as the daily/weekly helpers — accept the tick
    # that lands shortly AFTER the anchor as still firing this month
    # rather than punting to next month.
    grace = TICK_INTERVAL_SECONDS * 2
    for _ in range(14):
        last_day = calendar.monthrange(y, m)[1]
        target_day = min(dom, last_day)
        anchor = _day_anchor_ts(hh, mm, y, m, target_day)
        if anchor + grace > now_ts and anchor > last:
            return int(anchor)
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    return int(now_ts + 86400)


# ----------------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------------
# How often the tick loop wakes up to look for due schedules. Finer than
# this is wasted work — interval granularity is already capped by
# MIN_INTERVAL_SECONDS below and operators rarely care about <1-min
# precision for "refresh cache" / "prune node" / etc.
TICK_INTERVAL_SECONDS = 60

# Hard floor on how often a schedule may fire. Prevents a misconfigured
# interval from creating a tight loop that hammers Portainer or the DB.
MIN_INTERVAL_SECONDS = 60

# How long (in seconds) the waiter coroutine polls for a fired op to
# finish before giving up and leaving last_duration NULL. Schedules
# whose underlying op_type genuinely takes longer than this should
# bump it per-kind rather than globally; for now a shared cap is fine.
WAITER_TIMEOUT_SECONDS = 30 * 60  # 30 min

# Actor string stamped onto ops produced by this module. Used by
# /api/schedules/queue to filter the history table, and by anyone
# grepping the ops panel to tell "me clicked it" vs "scheduler did it".
SCHEDULER_ACTOR = "scheduler"


# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------
def init_schedules_schema(conn: sqlite3.Connection) -> None:
    """Create the ``schedules`` table if missing.

    Called once from main.py's ``init_db()`` alongside the other
    module-owned schema hooks (auth, etc.).
    """
    conn.executescript("""
                       CREATE TABLE IF NOT EXISTS schedules
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           name
                           TEXT
                           UNIQUE
                           NOT
                           NULL,
                           kind
                           TEXT
                           NOT
                           NULL,
                           params
                           TEXT,
                           interval_seconds
                           INTEGER
                           NOT
                           NULL,
                           enabled
                           INTEGER
                           NOT
                           NULL
                           DEFAULT
                           1,
                           last_run_at
                           INTEGER,
                           last_duration
                           INTEGER,
                           last_status
                           TEXT,
                           last_op_id
                           TEXT,
                           created_at
                           INTEGER
                           NOT
                           NULL,
                           updated_at
                           INTEGER
                           NOT
                           NULL
                       );
                       CREATE INDEX IF NOT EXISTS idx_schedules_enabled
                           ON schedules(enabled, last_run_at);
                       """)
    # Idempotent column adds for deployments upgrading from earlier schemas.
    # - run_at_hhmm: time-of-day anchor for daily/weekly/monthly modes.
    # - cadence_mode: which of the four modes the row is using. Legacy rows
    # with NULL are treated as 'daily' if run_at_hhmm is set, else 'interval'.
    # - days_of_week: JSON int array (Mon=0..Sun=6) — weekly mode only.
    # - day_of_month: 1..31 (clamped to the month's last day) — monthly only.
    for ddl in (
            "ALTER TABLE schedules ADD COLUMN run_at_hhmm TEXT",
            "ALTER TABLE schedules ADD COLUMN cadence_mode TEXT",
            "ALTER TABLE schedules ADD COLUMN days_of_week TEXT",
            "ALTER TABLE schedules ADD COLUMN day_of_month INTEGER",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass


# ----------------------------------------------------------------------------
# Serialisation helpers
# ----------------------------------------------------------------------------
def _row_to_dict(row: sqlite3.Row) -> dict:
    """Turn one ``schedules`` row into a JSON-ready dict.

    Derives ``next_run_at`` so the UI can render a "Next execution"
    column without re-computing. A never-run schedule's next run is
    based on ``created_at`` — this gives newly-added schedules a sane
    deadline before the first tick fires them.
    """
    d = dict(row)
    # Decode params JSON; tolerate legacy NULLs and malformed values by
    # returning an empty dict so the UI doesn't explode.
    try:
        d["params"] = json.loads(d.get("params") or "{}")
        if not isinstance(d["params"], dict):
            d["params"] = {}
    except (TypeError, ValueError):
        d["params"] = {}
    d["enabled"] = bool(d.get("enabled"))
    # Surface calendar-cadence fields even when legacy rows lack the
    # columns, so the UI's schema contract stays stable during upgrade.
    d["run_at_hhmm"] = d.get("run_at_hhmm") or None
    d["day_of_month"] = d.get("day_of_month") if d.get("day_of_month") else None
    # days_of_week is stored as JSON; decode and tolerate bad values.
    try:
        dow_raw = d.get("days_of_week")
        d["days_of_week"] = (
            [int(x) for x in json.loads(str(dow_raw)) if 0 <= int(x) <= 6]
            if dow_raw else []
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        d["days_of_week"] = []
    # Resolve the active cadence mode. Pre-column legacy rows land here
    # with NULL; infer from run_at_hhmm so upgraded deploys keep working.
    mode = (d.get("cadence_mode") or "").strip()
    if mode not in CADENCE_MODES:
        mode = "daily" if d["run_at_hhmm"] else "interval"
    d["cadence_mode"] = mode
    # Compute next_run_at per mode. Tick loop treats ``next <= now`` as
    # due uniformly, so baking the mode-specific math here keeps the
    # scheduler loop itself mode-agnostic.
    try:
        hhmm = _parse_hhmm(d["run_at_hhmm"])
    except ValueError:
        hhmm = None
    # Narrow `last_run_at` to int — `dict.get` types as `Any | None` so
    # arithmetic on `None or 0` confuses Pyright into seeing `None.__add__`.
    last_run: int = int(d.get("last_run_at") or 0)
    if mode == "interval" or not hhmm:
        base: int = int(d.get("last_run_at") or d.get("created_at") or 0)
        interval: int = int(d.get("interval_seconds") or 0)
        d["next_run_at"] = base + interval
    elif mode == "daily":
        d["next_run_at"] = _next_fixed_time_run(hhmm[0], hhmm[1], last_run)
    elif mode == "weekly":
        d["next_run_at"] = _next_weekly_run(
            hhmm[0], hhmm[1], d["days_of_week"], last_run,
        )
    elif mode == "monthly":
        d["next_run_at"] = _next_monthly_run(
            hhmm[0], hhmm[1], d["day_of_month"] or 1, last_run,
        )
    else:  # defensive: unknown mode → behave like interval
        base = int(d.get("last_run_at") or d.get("created_at") or 0)
        interval = int(d.get("interval_seconds") or 0)
        d["next_run_at"] = base + interval
    return d


# ----------------------------------------------------------------------------
# CRUD helpers
# ----------------------------------------------------------------------------
def list_schedules(conn: sqlite3.Connection) -> list[dict]:
    """
    Return every persisted schedule row as a list of dicts.

    Sorted by name (case-insensitive collation). Each dict carries the
    legacy schema fields PLUS computed ``cadence_mode`` + ``next_run_at``
    via :func:`_row_to_dict`, so callers don't have to redo the mode-
    specific due-time math.

    :param conn: open SQLite connection.
    :returns: list of dicts, one per schedule row, newest by name first.
    """
    rows = conn.execute(
        "SELECT * FROM schedules ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_schedule(conn: sqlite3.Connection, schedule_id: int) -> Optional[dict]:
    """Return one schedule row decoded as a dict by primary-key id.

    Returns ``None`` when no row matches. Output dict is identical to
    :func:`list_schedules` per-row shape (carries computed
    ``cadence_mode`` + ``next_run_at`` via :func:`_row_to_dict`).
    """
    r = conn.execute(
        "SELECT * FROM schedules WHERE id=?", (schedule_id,),
    ).fetchone()
    return _row_to_dict(r) if r else None


def get_schedule_by_name(conn: sqlite3.Connection, name: str) -> Optional[dict]:
    """Return one schedule row decoded as a dict, looked up by name.

    Match is case-sensitive (matches the unique-index contract on the
    ``schedules.name`` column). Returns ``None`` when no row matches.
    Used by the bootstrap helpers (``bootstrap_swarm_agent_health_schedule``
    + ``seed_default_schedules``) to decide whether the canonical row
    already exists before INSERT.
    """
    r = conn.execute(
        "SELECT * FROM schedules WHERE name=?", (name,),
    ).fetchone()
    return _row_to_dict(r) if r else None


def _validate_cadence(
    mode: str,
    run_at_hhmm: Optional[str],
    days_of_week: Optional[list[int]],
    day_of_month: Optional[int],
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Cross-field validation for the cadence bundle.

    Returns the (normalised_hhmm, days_of_week_json, day_of_month) that
    should be written to the DB. Raises ``ValueError`` on any
    inconsistency so the API route can return a 400.
    """
    if mode not in CADENCE_MODES:
        raise ValueError(
            f"cadence_mode must be one of {CADENCE_MODES!r}, got {mode!r}"
        )
    # HH:MM normalisation — required for daily/weekly/monthly, ignored
    # for interval (we still accept and drop).
    hhmm_stored: Optional[str] = (run_at_hhmm or "").strip() or None
    if hhmm_stored:
        _parse_hhmm(hhmm_stored)  # raises on malformed
    if mode in ("daily", "weekly", "monthly") and not hhmm_stored:
        raise ValueError(f"{mode} cadence requires run_at_hhmm")
    # days_of_week — weekly requires at least one day; other modes drop it.
    dow_json: Optional[str] = None
    if mode == "weekly":
        dow = list(days_of_week or [])
        normalised: list[int] = []
        for d in dow:
            try:
                di = int(d)
            except (TypeError, ValueError):
                raise ValueError(f"days_of_week entries must be integers: {d!r}")
            if not (0 <= di <= 6):
                raise ValueError(
                    "days_of_week entries must be 0 (Mon) .. 6 (Sun)"
                )
            if di not in normalised:
                normalised.append(di)
        if not normalised:
            raise ValueError("weekly cadence requires at least one day_of_week")
        dow_json = json.dumps(sorted(normalised))
    # day_of_month — monthly requires 1..31.
    dom_stored: Optional[int] = None
    if mode == "monthly":
        if day_of_month is None:
            raise ValueError("monthly cadence requires day_of_month")
        try:
            dom = int(day_of_month)
        except (TypeError, ValueError):
            raise ValueError("day_of_month must be an integer")
        if not (1 <= dom <= 31):
            raise ValueError("day_of_month must be 1..31")
        dom_stored = dom
    return hhmm_stored, dow_json, dom_stored


def create_schedule(
    conn: sqlite3.Connection,
    name: str,
    kind: str,
    params: dict,
    interval_seconds: int,
    enabled: bool = True,
    run_at_hhmm: Optional[str] = None,
    cadence_mode: str = "interval",
    days_of_week: Optional[list[int]] = None,
    day_of_month: Optional[int] = None,
) -> dict:
    """Insert one schedule row and return its freshly-read representation.

    Validates kind + interval + cadence. Callers are expected to have
    already validated name non-emptiness. ``IntegrityError`` on duplicate
    name is allowed to bubble — the API route translates it to 409.

    ``interval_seconds`` is always persisted (legal fallback) even when
    a non-interval mode is active, so an operator can flip back later
    without re-entering the value. The tick loop consults ``cadence_mode``
    to decide which set of fields matters.
    """
    if kind not in SCHEDULE_KINDS:
        raise ValueError(f"unknown schedule kind: {kind!r}")
    if interval_seconds < MIN_INTERVAL_SECONDS:
        raise ValueError(
            f"interval_seconds must be >= {MIN_INTERVAL_SECONDS}"
        )
    params = params or {}
    if not isinstance(params, dict):
        raise ValueError("params must be a dict")
    hhmm_stored, dow_json, dom_stored = _validate_cadence(
        cadence_mode, run_at_hhmm, days_of_week, day_of_month,
    )
    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO schedules "
        "(name, kind, params, interval_seconds, enabled, run_at_hhmm, "
        " cadence_mode, days_of_week, day_of_month, "
        " created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (name, kind, json.dumps(params), int(interval_seconds),
         1 if enabled else 0, hhmm_stored,
         cadence_mode, dow_json, dom_stored,
         now, now),
    )
    return get_schedule(conn, cur.lastrowid)  # type: ignore[return-value]


def update_schedule(
    conn: sqlite3.Connection,
    schedule_id: int,
    **fields: Any,
) -> Optional[dict]:
    """Patch a schedule row with the provided fields.

    Accepts: ``name``, ``kind``, ``params``, ``interval_seconds``,
    ``enabled``. Unknown keys are silently ignored so the API route can
    pass its whole pydantic model through without worrying about extra
    fields. Returns the refreshed row.
    """
    # Cadence fields are validated as a bundle: if ANY of them are in
    # the patch we re-validate the target mode against the merged row,
    # otherwise we leave them alone. This avoids partial-update holes
    # like "clear HH:MM while still in daily mode".
    cadence_keys = {"cadence_mode", "run_at_hhmm", "days_of_week", "day_of_month"}
    if cadence_keys & fields.keys():
        existing = get_schedule(conn, schedule_id)
        if existing is None:
            raise ValueError(f"schedule {schedule_id} not found")
        merged = {k: fields.get(k, existing.get(k)) for k in cadence_keys}
        # Interpret None on run_at_hhmm as "clear" only when the caller
        # sent it explicitly; if it wasn't in the patch at all we'll
        # fall back to the existing value via dict.get above.
        hhmm_stored, dow_json, dom_stored = _validate_cadence(
            str(merged.get("cadence_mode") or existing.get("cadence_mode") or "interval"),
            merged.get("run_at_hhmm"),
            merged.get("days_of_week"),
            merged.get("day_of_month"),
        )
        # Overwrite the raw entries so the generic loop below persists
        # the normalised values rather than the caller's raw input.
        fields = {
            **fields,
            "cadence_mode": str(merged.get("cadence_mode") or "interval"),
            "run_at_hhmm": hhmm_stored,
            "days_of_week": dow_json,
            "day_of_month": dom_stored,
        }

    allowed = {
        "name", "kind", "params", "interval_seconds", "enabled",
        "run_at_hhmm", "cadence_mode", "days_of_week", "day_of_month",
    }
    # These are the fields where explicit None means "clear the column"
    # rather than "don't touch". Everything else follows the
    # exclude_none-style "None = skip" convention.
    clearable_on_none = {"run_at_hhmm", "days_of_week", "day_of_month"}
    sets = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in clearable_on_none:
            if value is None:
                values.append(None)
            elif key == "days_of_week" and isinstance(value, list):
                values.append(json.dumps(value))
            else:
                values.append(value)
            sets.append(f"{key}=?")
            continue
        if value is None:
            continue
        if key == "kind" and value not in SCHEDULE_KINDS:
            raise ValueError(f"unknown schedule kind: {value!r}")
        if key == "interval_seconds" and int(value) < MIN_INTERVAL_SECONDS:
            raise ValueError(
                f"interval_seconds must be >= {MIN_INTERVAL_SECONDS}"
            )
        if key == "cadence_mode" and value not in CADENCE_MODES:
            raise ValueError(f"unknown cadence_mode: {value!r}")
        if key == "params":
            if not isinstance(value, dict):
                raise ValueError("params must be a dict")
            values.append(json.dumps(value))
        elif key == "enabled":
            values.append(1 if value else 0)
        else:
            values.append(value)
        sets.append(f"{key}=?")
    if not sets:
        return get_schedule(conn, schedule_id)
    sets.append("updated_at=?")
    values.append(int(time.time()))
    values.append(schedule_id)
    conn.execute(
        f"UPDATE schedules SET {', '.join(sets)} WHERE id=?",
        values,
    )
    return get_schedule(conn, schedule_id)


def delete_schedule(conn: sqlite3.Connection, schedule_id: int) -> None:
    """
    Remove one schedule row by primary-key id.

    Idempotent — safe to call on a non-existent id (the underlying
    ``DELETE`` is a no-op when no row matches). The admin UI's delete
    button relies on this contract so a double-click can't surface a
    confusing "schedule not found" toast after the first click already
    removed the row.

    :param conn: open SQLite connection.
    :param schedule_id: primary-key id of the row to remove.
    :returns: None. Side effect only — the row is gone (or already was).
    """
    conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))


def record_run(
    conn: sqlite3.Connection,
    schedule_id: int,
    op_id: str,
    duration: Optional[int],
    status: Optional[str],
    update_run_at: bool = True,
) -> None:
    """Stamp the outcome of a fired schedule.

    Called twice per fire: once by the tick loop with ``duration=None,
    status=None`` immediately after kicking the op off (so the row's
    last_run_at moves forward even if the op hangs), and again by the
    waiter coroutine when the op completes (with the real duration +
    status). Passing ``None`` for either field means "don't touch it".

    ``update_run_at`` (default True) controls whether ``last_run_at``
    gets overwritten with ``int(time.time())``. The fire-time call
    leaves it at True (the canonical "this is when we fired"); the
    waiter-completion call AND the ghost-clear sweep at startup pass
    False because their stamping moment is NOT the schedule's actual
    fire moment:

    - **Waiter:** runs when the op completes, which could be seconds
      to minutes after fire-time. Drifting ``last_run_at`` forward by
      the op duration would make "Last execution" lie by the op's
      runtime — a Daily-@-01:30 schedule whose op took 3 minutes
      would display last_run_at = 01:33 instead of 01:30.
    - **Ghost-clear sweep:** runs at startup AFTER a container
      restart to clear duration-NULL rows whose waiter died mid-op.
      Stamping ``last_run_at`` here would move the fire-time to the
      container restart moment — operator-visible as "Last execution:
      X minutes ago" where X = how long the container has been up,
      regardless of when the schedule actually fired (operator-flagged
      as the canonical "Daily @ 01:30 shows 48 minutes ago at 08:17"
      bug when the container restart was at 07:29).

    The error-path stamp at fire-failure (in scheduler_loop's
    per-schedule try/except) keeps the default True because a failed
    fire attempt IS a fire-time event — without it the schedule would
    re-fire every tick forever.
    """
    sets: list[str] = ["last_op_id=?", "updated_at=?"]
    now = int(time.time())
    values: list[Any] = [op_id, now]
    if update_run_at:
        sets.insert(0, "last_run_at=?")
        values.insert(0, now)
    if duration is not None:
        sets.append("last_duration=?")
        values.append(int(duration))
    if status is not None:
        sets.append("last_status=?")
        values.append(status)
    values.append(schedule_id)
    conn.execute(
        f"UPDATE schedules SET {', '.join(sets)} WHERE id=?",
        values,
    )


# ----------------------------------------------------------------------------
# Kind registry — maps a schedule's `kind` onto the async callable that
# fires it. Each callable is responsible for creating a new Operation
# (or a synthetic op_id if there's no underlying ops.py handler) and
# returning (op_id, awaitable_done).
#
# The awaitable_done must resolve to (duration_seconds, "success"|"error")
# so the waiter coroutine can stamp the schedule row. For ops.py-backed
# kinds, the awaitable wraps a poll of the in-memory `ops` dict; for
# synthetic ops (e.g. gather_refresh), the callable awaits directly.
# ----------------------------------------------------------------------------
KindRunner = Callable[[dict], Awaitable[tuple[str, Awaitable[tuple[int, str]]]]]


async def _await_op_completion(op_id: str) -> tuple[int, str]:
    """Poll the in-memory ops dict until this op finishes.

    The ops system stamps ``ended`` + ``status`` on completion; we
    sample every couple seconds rather than hook into
    :func:`persist_history` to keep this module one-way-dependent on
    ops.py (no circular imports, no monkey-patching).

    On timeout or if the op disappears (ring-buffer eviction) we
    return ``(0, "error")`` so the schedule row reflects the lost
    trail instead of staying stuck on the previous status.
    """
    deadline = time.time() + WAITER_TIMEOUT_SECONDS
    while time.time() < deadline:
        op = _ops.ops.get(op_id)
        if op is None:
            # Ring-buffer eviction beat us — still technically done.
            return 0, "error"
        if op.status != "running":
            duration = int((op.ended or time.time()) - op.started)
            return duration, op.status
        await asyncio.sleep(2)
    return 0, "error"


async def _run_prune_node(params: dict) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Fire a ``docker system prune`` on a named node.

    params: {"hostname": str}. Hostname is NOT validated against the
    live node cache here because the scheduler might legitimately be
    configured for a node that's currently offline — let ops.do_prune_node
    fail loudly at fire time, that error is more useful than silent
    skipping.
    """
    hostname = str((params or {}).get("hostname") or "").strip()
    if not hostname:
        raise ValueError("prune_node requires a 'hostname' param")
    op = _ops.new_op(
        "prune_node", hostname, hostname,
        actor=SCHEDULER_ACTOR,
    )
    # Lazy main import — avoids the circular dependency. Routes through
    # `spawn_background_task` so the strong-ref + done-callback contract
    # (see CLAUDE.md "Background-task lifecycle") protects the spawn
    # from asyncio GC mid-execution.
    import main as _main
    _main.spawn_background_task(
        _ops.do_prune_node(op, hostname),
        label=f"schedule prune_node {hostname!r}",
    )
    return op.id, _await_op_completion(op.id)


async def _run_prune_all_nodes(_params: dict) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Fan out ``docker system prune`` across every known Swarm node.

    params: {} (ignored). Hostnames are read from the latest gather
    snapshot at fire time — so one schedule row auto-adopts new nodes
    as they join the swarm and silently drops nodes that leave. Empty
    cache raises, because stamping 'success' on a no-op fire would
    hide a broken Portainer connection.

    Each host gets its own ops.do_prune_node — they inherit the live
    ops panel, Apprise notify, and history-row treatment individually,
    so the operator sees N rows in the Queue tab per fire (all tagged
    actor='scheduler'). The schedule row's last_op_id is a synthetic
    parent id; skip-if-running still works because the waiter only
    stamps last_duration after every child resolves.

    Aggregate: duration = longest child's wall time (children run in
    parallel, durations don't add); status = 'success' iff every child
    succeeded, otherwise 'error'.
    """
    # noinspection PyProtectedMember
    nodes_info = (gather._cache.get("nodes_info") or {})
    # Filter to ACTUAL Swarm nodes — `nodes_info` is the shared host
    # inventory that ALSO accumulates non-Docker curated hosts (WiFi
    # routers / managed switches / UPSes seen via SNMP / Ping / Pulse).
    # Swarm nodes carry a `role` field (manager / worker) stamped by
    # the Portainer /nodes walk in gather; non-Swarm curated hosts
    # populate only the host_* telemetry keys and have no `role`.
    # Pre-fix the prune fan-out fired `docker system prune` on every
    # key in `nodes_info` — operators saw "🧹 Prune complete on
    # <wifi-router-hostname>" notifications with "Reclaimed 0 B across
    # 0 containers / 0 images" output (the smoking gun: nothing to
    # prune because the host doesn't run Docker).
    hostnames = sorted(
        h for h, info in nodes_info.items()
        if isinstance(info, dict) and (info.get("role") or "").strip()
    )
    if not hostnames:
        raise ValueError(
            "prune_all_nodes: no Swarm nodes visible in cache — is Portainer reachable?"
        )

    parent_id = "sched-" + secrets.token_hex(4)
    child_ops: list[_ops.Operation] = []
    # Lazy main import — avoids the circular dependency. Routes every
    # fan-out spawn through `spawn_background_task` so the strong-ref +
    # done-callback contract (see CLAUDE.md "Background-task lifecycle")
    # protects the per-host tasks from asyncio GC mid-execution. This
    # matters MORE here than the single-node path: 5 nodes pruning
    # concurrently means 5 tasks share the GC-collection risk surface
    # under the bare-create_task shape.
    import main as _main
    for host in hostnames:
        op = _ops.new_op(
            "prune_node", host, host,
            actor=SCHEDULER_ACTOR,
        )
        child_ops.append(op)
        _main.spawn_background_task(
            _ops.do_prune_node(op, host),
            label=f"schedule prune_all_nodes child {host!r}",
        )

    async def waiter() -> tuple[int, str]:
        """Await every child prune-op in parallel; aggregate into
        (longest-duration, status). Children run in parallel so
        durations don't sum — the longest is the wall-clock cost.
        Status is success iff every child succeeded; otherwise error."""
        results = await asyncio.gather(
            *(_await_op_completion(o.id) for o in child_ops),
        )
        longest = max((d for d, _s in results), default=0)
        all_ok = all(s == "success" for _d, s in results)
        return longest, "success" if all_ok else "error"

    return parent_id, waiter()


async def _run_gather_refresh(_params: dict) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Force-refresh the gather cache.

    Doesn't go through the ops.py system — :func:`logic.gather.gather`
    has no Operation wrapper, it's just a cache-refresh function. We
    still synthesize an op_id and write a row into the ``history``
    table on completion so the scheduler Queue UI shows this kind
    alongside ops.py-backed kinds (prune_node etc).
    """
    op_id = "sched-" + secrets.token_hex(4)

    async def runner() -> tuple[int, str]:
        """Inner coroutine spawned by this runner; returns (duration_seconds, status). Fire-and-forget — caller spawns via asyncio.create_task and the schedule loop awaits the resolved task."""
        started = time.time()
        status = "success"
        err: Optional[str] = None
        try:
            await gather.gather()
        except (RuntimeError, OSError, ValueError) as e:
            status = "error"
            err = str(e)
            print(f"[scheduler] gather_refresh failed: {e}")
        duration = int(time.time() - started)
        # Mirror the op into history so the Queue tab (which filters by
        # actor='scheduler') picks it up. We don't involve ops.persist_history
        # because that also bumps a Prometheus counter tied to op_type names
        # — keep gather_refresh out of that bucket so it doesn't inflate
        # omnigrid_ops_total with cache-refresh noise.
        _ops.assert_op_type("gather_refresh")
        try:
            with db_conn() as c:
                c.execute(
                    "INSERT INTO history "
                    "(ts, op_type, target_kind, target_name, target_id, "
                    " target_stack, status, duration, events, error, actor) "
                    "VALUES (?, ?, 'schedule', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        started, "gather_refresh",
                        "fleet cache", op_id, None,
                        status, duration,
                        "[]", err, SCHEDULER_ACTOR,
                    ),
                )
        except sqlite3.Error as e:
            print(f"[scheduler] gather_refresh history write failed: {e}")
        return duration, status

    task = asyncio.create_task(runner())
    return op_id, task  # type: ignore[return-value]


async def _run_backup(_params: dict) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Create a full backup zip via :func:`logic.backups.create_backup`.

    No ops.py Operation — backups don't have a per-target context worth
    showing in the live ops panel. We synthesize an op_id and write a
    history row directly when done, mirroring the gather_refresh pattern
    so the Queue tab picks it up alongside other scheduler-driven work.

    Backup I/O is blocking (sqlite .backup + zip write), so we hand it
    to the default executor to keep the event loop responsive during
    the few seconds a backup typically takes.
    """
    op_id = "sched-" + secrets.token_hex(4)

    async def runner() -> tuple[int, str]:
        """Inner coroutine spawned by this runner; returns (duration_seconds, status). Fire-and-forget — caller spawns via asyncio.create_task and the schedule loop awaits the resolved task."""
        started = time.time()
        status = "success"
        err: Optional[str] = None
        backup_name: Optional[str] = None
        try:
            result = await asyncio.to_thread(backups.create_backup)
            backup_name = result.get("name")
            print(f"[scheduler] backup created: {backup_name}")
            # Apply retention right after a successful create — matches
            # the behaviour of the manual "Create backup" button so a
            # scheduled nightly backup doesn't blow past the keep-N.
            # Now reads via tuning_int (DB > env > default with bounds
            # clamp) — same canonical resolution path as the manual
            # "Create backup" button uses.
            try:
                from logic.tuning import Tunable, tuning_int as _tuning_int
                keep = _tuning_int(Tunable.BACKUP_RETENTION_COUNT)
            except (TypeError, ValueError):
                keep = 0
            if keep > 0:
                pruned = await asyncio.to_thread(backups.prune_backups, keep)
                if pruned:
                    print(
                        f"[scheduler] backup retention: pruned {len(pruned)} older "
                        f"file(s), kept {keep} newest"
                    )
        except (sqlite3.Error, OSError, RuntimeError, ValueError) as e:
            status = "error"
            err = str(e)
            print(f"[scheduler] backup failed: {e}")
        duration = int(time.time() - started)
        _ops.assert_op_type("backup")
        try:
            with db_conn() as c:
                c.execute(
                    "INSERT INTO history "
                    "(ts, op_type, target_kind, target_name, target_id, "
                    " target_stack, status, duration, events, error, actor) "
                    "VALUES (?, ?, 'schedule', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        started, "backup",
                        backup_name or "backup", op_id, None,
                        status, duration,
                        "[]", err, SCHEDULER_ACTOR,
                    ),
                )
        except sqlite3.Error as e:
            print(f"[scheduler] backup history write failed: {e}")
        return duration, status

    task = asyncio.create_task(runner())
    return op_id, task  # type: ignore[return-value]


async def _run_asset_inventory_refresh(
    _params: dict,
) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Refresh the <asset-api-host> asset inventory cache.

    No ops.py Operation — like :func:`_run_gather_refresh` and
    :func:`_run_backup`, this kind writes a ``history`` row directly
    when done so the Queue tab picks it up. Reads the persisted
    settings (``asset_inventory_auth_mode`` + the flavour-specific
    fields) and defers all the auth / fetch / cache-write work to
    :func:`logic.asset_inventory.refresh_cache`.

    params: {} (ignored) — everything comes from persisted settings so
    the operator's schedule row doesn't need a duplicate copy of the
    credentials. If the settings are incomplete when the schedule
    fires, we stamp ``status='error'`` with a descriptive message and
    move on — same shape as a manual refresh button clicked without
    config.
    """
    op_id = "sched-" + secrets.token_hex(4)

    async def runner() -> tuple[int, str]:
        """Inner coroutine spawned by this runner; returns (duration_seconds, status). Fire-and-forget — caller spawns via asyncio.create_task and the schedule loop awaits the resolved task."""
        from logic import asset_inventory as _ai
        from logic.db import get_setting

        started = time.time()
        status = "success"
        err: Optional[str] = None
        count = 0
        # Master switch. When the operator flips Asset
        # Inventory off in Admin → Asset Inventory, scheduled refreshes
        # no-op without erasing the cache or the persisted credentials.
        if (get_setting(Settings.ASSET_INVENTORY_ENABLED, "true") or "true").lower() != "true":
            return 0, "skipped (asset_inventory disabled)"
        base_url = (get_setting(Settings.ASSET_INVENTORY_BASE_URL) or "").strip().rstrip("/")
        auth_mode = (get_setting(Settings.ASSET_INVENTORY_AUTH_MODE) or "oauth2").strip().lower()
        if auth_mode not in ("oauth2", "lifetime_token"):
            auth_mode = "oauth2"
        try:
            if auth_mode == "lifetime_token":
                lifetime_token = get_setting(Settings.ASSET_INVENTORY_LIFETIME_TOKEN) or ""
                service = (get_setting(Settings.ASSET_INVENTORY_SERVICE) or "").strip()
                action = (get_setting(Settings.ASSET_INVENTORY_ACTION) or "").strip()
                min_raw = (get_setting(Settings.ASSET_INVENTORY_MIN_VALUE) or "").strip()
                max_raw = (get_setting(Settings.ASSET_INVENTORY_MAX_VALUE) or "").strip()
                try:
                    min_value = int(min_raw) if min_raw else None
                except ValueError:
                    min_value = None
                try:
                    max_value = int(max_raw) if max_raw else None
                except ValueError:
                    max_value = None
                if not base_url or not lifetime_token:
                    raise RuntimeError(
                        "asset_inventory base_url and lifetime_token are required "
                        "for the lifetime-token auth mode"
                    )
                # / — honour the asset_inventory_verify_tls
                # setting (default True) so operators with a self-signed
                # asset API can opt out without monkey-patching.
                _verify_tls_raw = (get_setting(Settings.ASSET_INVENTORY_VERIFY_TLS, "true") or "true").strip().lower()
                _verify_tls = _verify_tls_raw != "false"
                result = await _ai.refresh_cache(
                    base_url,
                    verify_tls=_verify_tls,
                    auth_mode=_ai.AUTH_MODE_LIFETIME_TOKEN,
                    lifetime_token=lifetime_token,
                    service=service,
                    action=action,
                    min_value=min_value,
                    max_value=max_value,
                )
            else:
                token_url = (get_setting(Settings.ASSET_INVENTORY_TOKEN_URL) or "").strip()
                client_id = (get_setting(Settings.ASSET_INVENTORY_CLIENT_ID) or "").strip()
                client_secret = get_setting(Settings.ASSET_INVENTORY_CLIENT_SECRET) or ""
                scope = (get_setting(Settings.ASSET_INVENTORY_SCOPE) or "").strip()
                if not base_url or not token_url or not client_id or not client_secret:
                    raise RuntimeError(
                        "asset_inventory OAuth2 credentials incomplete — "
                        "configure base_url / token_url / client_id / client_secret"
                    )
                _verify_tls_raw = (get_setting(Settings.ASSET_INVENTORY_VERIFY_TLS, "true") or "true").strip().lower()
                _verify_tls = _verify_tls_raw != "false"
                result = await _ai.refresh_cache(
                    base_url,
                    token_url=token_url,
                    client_id=client_id,
                    client_secret=client_secret,
                    scope=scope,
                    verify_tls=_verify_tls,
                )
            if not result.get("ok"):
                status = "error"
                err = result.get("error") or "asset refresh failed"
            count = int(result.get("count") or 0)
        except Exception as e:
            status = "error"
            err = str(e)
            print(f"[scheduler] asset_inventory_refresh failed: {e}")

        duration = int(time.time() - started)
        _ops.assert_op_type("asset_inventory_refresh")
        try:
            with db_conn() as c:
                c.execute(
                    "INSERT INTO history "
                    "(ts, op_type, target_kind, target_name, target_id, "
                    " target_stack, status, duration, events, error, actor) "
                    "VALUES (?, ?, 'schedule', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        started, "asset_inventory_refresh",
                        f"{count} asset(s)" if status == "success" else "asset inventory",
                        op_id, None,
                        status, duration,
                        "[]", err, SCHEDULER_ACTOR,
                    ),
                )
        except Exception as e:
            print(f"[scheduler] asset_inventory_refresh history write failed: {e}")
        return duration, status

    task = asyncio.create_task(runner())
    return op_id, task  # type: ignore[return-value]


async def _run_prune_logs(params: dict) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Sweep `/app/data/logs/` and delete daily files older than the
    persisted ``tuning_log_retention_days`` (or ``params.days`` if the
    schedule row carries an explicit override). Idempotent — running
    twice in a minute just produces a second history row with 0 files
    deleted. Mirrors the gather_refresh / asset_inventory_refresh
    pattern: no Operation, writes a history row directly when done so
    it shows up in the History tab + the schedules queue.
    """
    op_id = "sched-" + secrets.token_hex(4)

    async def runner() -> tuple[int, str]:
        """Inner coroutine spawned by this runner; returns (duration_seconds, status). Fire-and-forget — caller spawns via asyncio.create_task and the schedule loop awaits the resolved task."""
        from logic import logs as _logs_mod
        from logic import tuning as _tuning_mod
        from logic.tuning import Tunable

        started = time.time()
        status = "success"
        err: Optional[str] = None
        removed = 0
        days: int = 0
        try:
            # Match the unified Tuning Config bounds for log retention so
            # an admin-supplied schedule param can't silently disable the
            # prune (huge days = effectively never), starve the disk
            # (days=0 / negative = same-as-no-op which masks intent), or
            # crash on a non-int. Clamping to the same
            # [1, 365] range as TUNABLES["tuning_log_retention_days"]
            # keeps the schedule UI consistent with Admin → Config.
            _, _, _lo, _hi = _tuning_mod.TUNABLES[Tunable.LOG_RETENTION_DAYS]
            override = params.get("days") if isinstance(params, dict) else None
            if override is not None and str(override).strip():
                try:
                    days = int(str(override).strip())
                except ValueError:
                    days = _tuning_mod.tuning_int(Tunable.LOG_RETENTION_DAYS)
            else:
                days = _tuning_mod.tuning_int(Tunable.LOG_RETENTION_DAYS)
            days = max(int(_lo), min(int(_hi), days))
            removed = _logs_mod.prune_old_logs(days)
        except Exception as e:
            status = "error"
            err = str(e)
            print(f"[scheduler] prune_logs failed: {e}")

        duration = int(time.time() - started)
        # Surface the resolved retention window in the history row's
        # target_name so the operator can audit which `days` value
        # actually fired (param vs tuning fallback vs clamped) without
        # cross-referencing settings + the schedule row. Format `(days=N)` only when
        # the resolution succeeded — exception path before the clamp
        # leaves `days=None`, in which case the suffix drops cleanly.
        target_suffix = f" (days={days})" if days is not None else ""
        target_name = f"{removed} log file(s){target_suffix}"
        _ops.assert_op_type("prune_logs")
        try:
            with db_conn() as c:
                c.execute(
                    "INSERT INTO history "
                    "(ts, op_type, target_kind, target_name, target_id, "
                    " target_stack, status, duration, events, error, actor) "
                    "VALUES (?, ?, 'schedule', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        started, "prune_logs",
                        target_name,
                        op_id, None,
                        status, duration,
                        "[]", err, SCHEDULER_ACTOR,
                    ),
                )
        except Exception as e:
            print(f"[scheduler] prune_logs history write failed: {e}")
        return duration, status

    task = asyncio.create_task(runner())
    return op_id, task  # type: ignore[return-value]


async def _run_prune_notifications(
    params: dict,
) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Sweep the ``notifications`` table and delete rows older than
    ``tuning_notification_retention_days`` (or ``params.days`` if the
    schedule row carries an explicit override). Idempotent — running
    twice in a minute just produces a second history row with 0 rows
    deleted. Mirrors the prune_logs pattern: no Operation, writes a
    history row directly so the run shows up in the History tab + the
    schedules queue.
    """
    op_id = "sched-" + secrets.token_hex(4)

    async def runner() -> tuple[int, str]:
        """Inner coroutine spawned by this runner; returns (duration_seconds, status). Fire-and-forget — caller spawns via asyncio.create_task and the schedule loop awaits the resolved task."""
        from logic import tuning as _tuning_mod
        from logic.tuning import Tunable

        started = time.time()
        status = "success"
        err: Optional[str] = None
        removed = 0
        days: int = 0
        try:
            _, _, _lo, _hi = _tuning_mod.TUNABLES[Tunable.NOTIFICATION_RETENTION_DAYS]
            override = params.get("days") if isinstance(params, dict) else None
            if override is not None and str(override).strip():
                try:
                    days = int(str(override).strip())
                except ValueError:
                    days = _tuning_mod.tuning_int(Tunable.NOTIFICATION_RETENTION_DAYS)
            else:
                days = _tuning_mod.tuning_int(Tunable.NOTIFICATION_RETENTION_DAYS)
            days = max(int(_lo), min(int(_hi), days))
            cutoff = int(time.time()) - days * 86400
            with db_conn() as c:
                cur = c.execute(
                    "DELETE FROM notifications WHERE ts < ?", (cutoff,),
                )
                removed = int(cur.rowcount or 0)
        except Exception as e:
            status = "error"
            err = str(e)
            print(f"[scheduler] prune_notifications failed: {e}")

        duration = int(time.time() - started)
        target_suffix = f" (days={days})" if days is not None else ""
        target_name = f"{removed} notification(s){target_suffix}"
        _ops.assert_op_type("prune_notifications")
        try:
            with db_conn() as c:
                c.execute(
                    "INSERT INTO history "
                    "(ts, op_type, target_kind, target_name, target_id, "
                    " target_stack, status, duration, events, error, actor) "
                    "VALUES (?, ?, 'schedule', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        started, "prune_notifications",
                        target_name,
                        op_id, None,
                        status, duration,
                        "[]", err, SCHEDULER_ACTOR,
                    ),
                )
        except Exception as e:
            print(f"[scheduler] prune_notifications history write dropped: {e}")
        return duration, status

    task = asyncio.create_task(runner())
    return op_id, task  # type: ignore[return-value]


# Module-level cooldown anchors for the swarm-agent-health autoheal
# kind. Persisted across container restarts via `set_setting` so an
# attacker (or buggy schedule cron) can't bypass the cooldown by
# repeatedly restarting OmniGrid. The in-memory floats below mirror
# the persisted values and are loaded lazily via
# `_load_swarm_autoheal_anchors()` on first read each tick — the
# runner only fires from the lifespan-managed scheduler tick (single-
# replica invariant), so single-process state is safe.
#
# Setting keys (DB):
# swarm_autoheal_last_restart_ts — float epoch seconds
# swarm_autoheal_last_notify_ts  — float epoch seconds
# swarm_autoheal_last_notify_set — JSON-encoded sorted list of host ids
_swarm_autoheal_last_restart_ts: float = 0.0
# Notify-only-path de-dup state. Without this a once-per-minute
# schedule paged 60×/hour while an agent stayed down. Two gates
# combine: (a) cooldown anchor identical to the restart path so an
# operator-tunable knob silences repeats over the cool-down window;
# (b) transition gate — if the unhealthy host SET is unchanged since
# the last fire, skip. Either gate clearing fires the next event.
_swarm_autoheal_last_notify_ts: float = 0.0
_swarm_autoheal_last_notify_set: frozenset = frozenset()
_swarm_autoheal_anchors_loaded: bool = False
# Skip-if-no-change history-write guard. On a healthy fleet the
# `swarm_agent_health` schedule kind fires every N minutes and walks
# through to action_taken="noop_healthy" — at default 5-min cadence
# that's 288 rows/day per scheduler with zero diagnostic value. Track
# the last action_taken we ACTUALLY persisted so the next tick can
# decide whether the row is a state-transition marker (worth keeping)
# or a duplicate idle ping (skip the INSERT). The FIRST noop_healthy
# AFTER a non-noop_healthy run still writes — that's the "system
# returned to healthy" marker.
_swarm_autoheal_last_persisted_action: str = ""


def _load_swarm_autoheal_anchors() -> None:
    """Lazy-load the persisted cooldown anchors from settings on
    first read after a process start. Subsequent reads use the
    in-memory mirror. Failure-safe: any DB error leaves the anchors
    at their zero defaults (consistent with "fresh start, no
    cooldown" semantics).
    """
    global _swarm_autoheal_last_restart_ts
    global _swarm_autoheal_last_notify_ts, _swarm_autoheal_last_notify_set
    global _swarm_autoheal_anchors_loaded
    if _swarm_autoheal_anchors_loaded:
        return
    _swarm_autoheal_anchors_loaded = True
    from logic.db import get_setting
    try:
        rt = get_setting(Settings.SWARM_AUTOHEAL_LAST_RESTART_TS) or ""
        if rt:
            _swarm_autoheal_last_restart_ts = float(rt)
    except (ValueError, TypeError):
        pass
    try:
        nt = get_setting(Settings.SWARM_AUTOHEAL_LAST_NOTIFY_TS) or ""
        if nt:
            _swarm_autoheal_last_notify_ts = float(nt)
    except (ValueError, TypeError):
        pass
    try:
        ns_raw = get_setting(Settings.SWARM_AUTOHEAL_LAST_NOTIFY_SET) or ""
        if ns_raw:
            _swarm_autoheal_last_notify_set = frozenset(json.loads(ns_raw))
    except (ValueError, TypeError, json.JSONDecodeError):
        pass


def _persist_swarm_autoheal_restart_ts(ts: float) -> None:
    """Write the restart anchor to settings. Best-effort."""
    from logic.db import set_setting
    try:
        set_setting(Settings.SWARM_AUTOHEAL_LAST_RESTART_TS, str(float(ts)))
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] persist swarm_autoheal_last_restart_ts failed: {e}")


def _persist_swarm_autoheal_notify_state(ts: float, host_set: frozenset) -> None:
    """Write the notify-side anchors to settings. Best-effort."""
    from logic.db import set_setting
    try:
        set_setting(Settings.SWARM_AUTOHEAL_LAST_NOTIFY_TS, str(float(ts)))
        set_setting(Settings.SWARM_AUTOHEAL_LAST_NOTIFY_SET,
                    json.dumps(sorted(host_set)))
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] persist swarm_autoheal_last_notify_state failed: {e}")


async def _run_swarm_agent_health(
    _params: dict,
) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Periodic Portainer-agent health probe + autoheal action.

    Detection: reuses the `_agent_health` map populated by every
    `gather_stats()` cycle. A node is "unhealthy" when its consecutive
    bad-gather count meets or exceeds
    ``tuning_swarm_agent_unhealthy_threshold``. The map is populated
    naturally by the existing /api/stats path; the schedule kind
    snapshots the current state and acts on it.

    Action branches on the ``swarm_autoheal_action`` setting:

    * ``"notify"`` (default) — fires the existing
      ``swarm_agent_unhealthy`` Apprise event ONCE per detection.
      Cheap; no cooldown.
    * ``"restart"`` — additionally spawns a `do_restart_swarm_agent`
      Operation. Cooldown gate: refuses to act when the last restart
      fired within ``tuning_swarm_autoheal_cooldown_minutes``. Without
      the gate a thrashing agent service could pin the manager in a
      restart loop.

    Per-run history row carries the resolved action + node count in
    ``target_name`` so the operator can audit decisions without
    cross-referencing settings + the live `_agent_health` map.

    Skip-if-no-unhealthy: when the map shows zero unhealthy nodes,
    the runner writes a no-op history row and returns success. This
    keeps the schedule's run history honest about how often the
    detection fired vs how often it actually did anything.
    """
    op_id = "sched-" + secrets.token_hex(4)

    async def runner() -> tuple[int, str]:
        """Inner coroutine spawned by this runner; returns (duration_seconds, status). Fire-and-forget — caller spawns via asyncio.create_task and the schedule loop awaits the resolved task."""
        from logic import stats as _stats_mod
        from logic import tuning as _tuning_mod
        from logic.tuning import Tunable
        from logic.db import get_setting

        global _swarm_autoheal_last_restart_ts
        # Lazy-load persisted cooldown anchors on first run after
        # process start so a container restart doesn't reset them.
        # Idempotent — flips a sentinel after first load.
        _load_swarm_autoheal_anchors()
        started = time.time()
        status = "success"
        err: Optional[str] = None
        action_taken = "noop"
        unhealthy_hosts: list[str] = []
        # When the autoheal fires a restart, surface the spawned Op's id
        # in the schedule's history events JSON so the operator (or AI
        # palette / post-mortem query) can cross-reference the schedule
        # row → restart Operation row without scanning every history row
        # with op_type=restart_swarm_agent in the same time window. None
        # for non-restart actions (noop / skipped_cooldown / notified).
        triggered_op_id: Optional[str] = None
        try:
            threshold = _tuning_mod.tuning_int(
                Tunable.SWARM_AGENT_UNHEALTHY_THRESHOLD,
            )
            health = _stats_mod.get_agent_health() or {}
            unhealthy_hosts = sorted([
                host for host, h in health.items()
                if isinstance(h, dict) and int(h.get("fails") or 0) >= threshold
            ])
            if not unhealthy_hosts:
                action_taken = "noop_healthy"
            else:
                action = (get_setting(Settings.SWARM_AUTOHEAL_ACTION, "notify")
                          or "notify").lower()
                if action == "restart":
                    cooldown_min = _tuning_mod.tuning_int(
                        Tunable.SWARM_AUTOHEAL_COOLDOWN_MINUTES,
                    )
                    elapsed_s = started - _swarm_autoheal_last_restart_ts
                    if _swarm_autoheal_last_restart_ts > 0 \
                        and elapsed_s < cooldown_min * 60:
                        action_taken = "skipped_cooldown"
                        print(
                            f"[scheduler] swarm_agent_health: cooldown "
                            f"({int(elapsed_s)}s < {cooldown_min * 60}s); "
                            f"unhealthy={unhealthy_hosts}",
                        )
                    else:
                        # Spawn the restart op + stamp the cooldown
                        # anchor only after the create_task call
                        # returns. Pre-fix this set the anchor BEFORE
                        # the spawn — if `_ops.do_restart_swarm_agent`
                        # raised synchronously (op_id collision,
                        # cancellation during construction) the
                        # anchor was locked in for the cooldown
                        # window even though no real restart
                        # happened, so the next tick wouldn't retry.
                        # Now: stamp the anchor only on a successful
                        # `create_task` call (the task is scheduled;
                        # whether its body succeeds is the op
                        # handler's responsibility — this matches
                        # the "create_task succeeded → cooldown
                        # consumed" semantic). If create_task itself
                        # raises, action_taken stays "restart_failed"
                        # and the anchor is unchanged so the next
                        # tick can retry immediately.
                        try:
                            op = _ops.new_op(
                                "restart_swarm_agent",
                                "",
                                "<portainer-agent>",
                                actor=SCHEDULER_ACTOR,
                            )
                            # Lazy main import — same circular-break +
                            # strong-ref + done-callback contract as the
                            # other schedule kinds (prune_node /
                            # prune_all_nodes / Telegram cleanups). Pre-fix
                            # the spawn was a bare `asyncio.create_task`
                            # bound to a local `_restart_task` that fell
                            # out of scope, exposing the running task to
                            # asyncio GC mid-execution per the canonical
                            # background-task lifecycle rule.
                            import main as _main
                            _main.spawn_background_task(
                                _ops.do_restart_swarm_agent(op),
                                label="schedule swarm_autoheal_restart",
                            )
                            _swarm_autoheal_last_restart_ts = started
                            _persist_swarm_autoheal_restart_ts(started)
                            action_taken = "restart_triggered"
                            triggered_op_id = op.id
                            print(
                                f"[scheduler] swarm_agent_health: restart "
                                f"triggered op_id={op.id}; "
                                f"unhealthy={unhealthy_hosts}",
                            )
                        except (asyncio.CancelledError, KeyboardInterrupt):
                            raise
                        except Exception as ce:  # noqa: BLE001
                            action_taken = "restart_failed"
                            print(
                                f"[scheduler] swarm_agent_health: restart "
                                f"spawn failed (cooldown NOT consumed): "
                                f"{ce}",
                            )
                else:
                    # notify-only path. Fire the dedicated
                    # `swarm_agent_unhealthy` Apprise event so the
                    # operator hears about the detection through the
                    # configured notification mediums (in-app +
                    # Apprise external) without auto-restarting.
                    #
                    # Transitions-only de-dup: fire ONE
                    # `swarm_agent_unhealthy` per host that just became
                    # unhealthy AND ONE `swarm_agent_recovered` per host
                    # that just recovered. A sustained outage no longer
                    # pages periodically; instead the operator gets one
                    # alert per incident + one auto-recovered signal.
                    # State plane: per-host membership in the previous
                    # unhealthy set (`_swarm_autoheal_last_notify_set`)
                    # is the gate. New members → unhealthy event. Lost
                    # members → recovered event. Identical sets between
                    # ticks → silent.
                    global _swarm_autoheal_last_notify_ts, _swarm_autoheal_last_notify_set
                    current_set = frozenset(unhealthy_hosts)
                    newly_unhealthy = sorted(current_set - _swarm_autoheal_last_notify_set)
                    newly_recovered = sorted(_swarm_autoheal_last_notify_set - current_set)
                    if not (newly_unhealthy or newly_recovered):
                        action_taken = "notify_skipped_no_change"
                        print(
                            f"[scheduler] swarm_agent_health: notify "
                            f"skipped (no transitions); "
                            f"unhealthy={unhealthy_hosts}",
                        )
                    else:
                        action_taken = "notified"
                        # Fire the unhealthy event when one or more
                        # hosts transitioned into the bad set this tick.
                        # The host list in title / body / metadata is
                        # the NEWLY-unhealthy ones — not every currently-
                        # unhealthy host — so each host shows up in
                        # exactly one unhealthy notification per
                        # incident.
                        if newly_unhealthy:
                            host_list_str = ", ".join(newly_unhealthy) or "<none>"
                            try:
                                await _ops.notify(
                                    f"⚠️ Portainer agent(s) unhealthy: {host_list_str}",
                                    f"Newly unhealthy nodes (>={threshold} "
                                    f"consecutive bad gathers): {host_list_str}. "
                                    f"Action: notify-only (configurable in "
                                    f"Admin → Notifications).",
                                    "warning",
                                    event="swarm_agent_unhealthy",
                                    actor_username=SCHEDULER_ACTOR,
                                    target_kind="schedule",
                                    target_id="swarm_agent_health",
                                    metadata={"unhealthy": newly_unhealthy,
                                              "threshold": threshold},
                                )
                            except Exception as ne:
                                print(
                                    f"[scheduler] swarm_agent_health "
                                    f"unhealthy notify failed: {ne}",
                                )
                        # Fire the recovered event when one or more
                        # hosts dropped out of the bad set. Severity is
                        # `success` so the notification reads as a
                        # positive signal in the in-app store + Apprise
                        # inbox.
                        if newly_recovered:
                            host_list_str = ", ".join(newly_recovered) or "<none>"
                            try:
                                await _ops.notify(
                                    f"✅ Portainer agent(s) recovered: {host_list_str}",
                                    f"Nodes that had been unhealthy are now "
                                    f"healthy again: {host_list_str}.",
                                    "success",
                                    event="swarm_agent_recovered",
                                    actor_username=SCHEDULER_ACTOR,
                                    target_kind="schedule",
                                    target_id="swarm_agent_health",
                                    metadata={"recovered": newly_recovered},
                                )
                            except Exception as ne:
                                print(
                                    f"[scheduler] swarm_agent_health "
                                    f"recovered notify failed: {ne}",
                                )
                        # Stamp the new state regardless of which event
                        # fired (or both) so the next tick sees the
                        # correct baseline.
                        _swarm_autoheal_last_notify_ts = started
                        _swarm_autoheal_last_notify_set = current_set
                        _persist_swarm_autoheal_notify_state(started, current_set)
                    print(
                        f"[scheduler] swarm_agent_health: {action_taken} "
                        f"action; unhealthy={unhealthy_hosts} "
                        f"newly_unhealthy={newly_unhealthy} "
                        f"newly_recovered={newly_recovered}",
                    )
        except Exception as e:
            status = "error"
            err = str(e)
            print(f"[scheduler] swarm_agent_health failed: {e}")

        duration = int(time.time() - started)
        target_name = f"{action_taken} ({len(unhealthy_hosts)} unhealthy)"
        # Skip-if-no-change — when the action is the steady-state
        # "noop_healthy" and the LAST persisted action was ALSO
        # "noop_healthy", drop the INSERT. The operator-visible value
        # of duplicate idle pings is zero (288 rows/day at 5-min
        # cadence on a healthy fleet); the first noop_healthy AFTER a
        # non-noop_healthy state still writes (state-transition
        # marker), so "system returned to healthy" stays visible.
        # `status != "ok"` always writes (errors / timeouts are
        # diagnostic regardless of repeat count).
        global _swarm_autoheal_last_persisted_action
        if (action_taken == "noop_healthy"
            and status == "ok"
            and _swarm_autoheal_last_persisted_action == "noop_healthy"):
            # Skip persistence; preserve the last-persisted-action so
            # the NEXT non-noop tick still sees the right baseline.
            return duration, status
        _ops.assert_op_type("swarm_agent_health")
        try:
            with db_conn() as c:
                c.execute(
                    "INSERT INTO history "
                    "(ts, op_type, target_kind, target_name, target_id, "
                    " target_stack, status, duration, events, error, actor) "
                    "VALUES (?, ?, 'schedule', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        started, "swarm_agent_health",
                        target_name,
                        op_id, None,
                        status, duration,
                        json.dumps([
                            {
                                "action": action_taken,
                                "unhealthy": unhealthy_hosts,
                                # `triggered_op_id` is the spawned restart
                                # Operation's id when action_taken is
                                # `restart_triggered`; None otherwise. Lets
                                # the operator jump from schedule row →
                                # restart Op row with one click instead of
                                # hunting through history.
                                "triggered_op_id": triggered_op_id,
                            },
                        ]),
                        err, SCHEDULER_ACTOR,
                    ),
                )
            _swarm_autoheal_last_persisted_action = action_taken
        except Exception as e:
            print(f"[scheduler] swarm_agent_health history write failed: {e}")
        return duration, status

    task = asyncio.create_task(runner())
    return op_id, task  # type: ignore[return-value]


async def _run_port_scan_refresh(
    _params: dict,
) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Periodically re-scan port-scan-enabled hosts whose last scan is
    older than ``tuning_port_scan_schedule_min_age_seconds``.

    Each fire picks at most ``tuning_port_scan_schedule_max_hosts_per_tick``
    hosts (oldest-scanned-first), and runs them through the SAME scan
    helper the on-demand admin path uses (``main._run_port_scan_async``)
    so persistence + SSE + new-port notify all behave identically. The
    only difference is the actor stamp (``scheduler`` instead of the
    admin's username) and that the schedule fires the runner — there
    is no separate code path for "scheduled vs manual" scans.

    Concurrency:

      * Within ONE fire, ``tuning_port_scan_schedule_per_host_concurrency``
        caps how many hosts run in parallel (default 1 = strictly
        sequential).
      * ACROSS fires, the existing scheduler ``_is_previous_run_active``
        gate prevents overlap — a frequent ticker (every 2 minutes)
        whose previous fire is still in flight skips silently rather
        than stacking.

    Skip rules (audit-trail visible in the history row's ``events`` JSON):

      * Master toggle off (``port_scan_enabled = false``) → no-op,
        ``status='success'``, ``selected=0``, ``skipped_master=True``.
        We don't error because the schedule itself is still operating
        correctly; an admin temporarily disabling port scan shouldn't
        spam every fire with red.
      * Per-host ``hosts_config[].port_scan.enabled`` not true → skipped.
      * Curated ``address`` field empty → skipped (a scan against
        a bare host_id would target an unresolvable alias).
      * Whole-host paused (``host_failure_state.paused_at`` set with
        no resolved_at) → skipped, honouring the auto-pause contract.
      * Last scan younger than ``min_age_seconds`` → skipped.

    No ops.py Operation — like ``_run_gather_refresh`` /
    ``_run_prune_logs``, this kind writes its own history row when
    done. Each individual host scan also writes ITS OWN
    ``op_type='port_scan'`` history row from inside
    ``_run_port_scan_async``, so the History tab shows BOTH the
    aggregate ``port_scan_refresh`` row AND the per-host scan rows.
    """
    op_id = "sched-" + secrets.token_hex(4)

    async def runner() -> tuple[int, str]:
        """Inner coroutine spawned by this runner; returns (duration_seconds, status). Fire-and-forget — caller spawns via asyncio.create_task and the schedule loop awaits the resolved task."""
        # Late-import main to avoid the circular dependency
        # (main.py imports logic.schedules at module load).
        import main as _main
        from logic import tuning as _tuning_mod
        from logic.tuning import Tunable
        from logic.db import get_setting_bool

        started = time.time()
        status = "success"
        err: Optional[str] = None
        selected: list[str] = []
        skipped: dict[str, list[str]] = {
            "disabled": [], "no_address": [],
            "paused": [], "too_recent": [],
        }
        first_skip_reason = ""

        try:
            if not get_setting_bool(Settings.PORT_SCAN_ENABLED):
                first_skip_reason = "master_toggle_off"
                duration = int(time.time() - started)
                _record_history_row(
                    op_id, started, duration, "success",
                    None, "0 selected (master toggle off)",
                    {
                        "selected": [],
                        "skipped_master": True,
                        "skipped": skipped,
                    },
                )
                print(
                    "[scheduler] port_scan_refresh skipped — master toggle off"
                )
                return duration, "success"

            max_hosts = _tuning_mod.tuning_int(
                Tunable.PORT_SCAN_SCHEDULE_MAX_HOSTS_PER_TICK
            )
            min_age = _tuning_mod.tuning_int(
                Tunable.PORT_SCAN_SCHEDULE_MIN_AGE_SECONDS
            )
            per_host_conc = _tuning_mod.tuning_int(
                Tunable.PORT_SCAN_SCHEDULE_PER_HOST_CONCURRENCY
            )

            # 1. Read curated host list — source of truth for "which
            #    hosts are eligible for scanning right now".
            # noinspection PyProtectedMember
            curated = _main._load_hosts_config()
            now_ts = int(time.time())

            # 2. Per-host last-scan-ts map (one DB read for the whole
            #    fleet). Hosts with no scan rows return None and are
            #    treated as MAX-age (always eligible — the schedule's
            #    "spread out the first crawl" behaviour).
            with db_conn() as c:
                rows = c.execute(
                    "SELECT host_id, MAX(ts) AS last_ts "
                    "FROM host_port_scans GROUP BY host_id"
                ).fetchall()
            last_scan_map = {
                str(r["host_id"]): int(r["last_ts"] or 0) for r in rows
            }

            # 3. Whole-host pause map. Mirrors the
            #    `host_failure_state.paused_at IS NOT NULL AND resolved_at
            #    IS NULL` predicate the resume endpoint uses; bare host_id
            #    keys (the per-provider prefixed keys are read by
            #    record_provider_outcome elsewhere).
            paused_ids: set[str] = set()
            try:
                with db_conn() as c:
                    # The composite-PK migration moved the per-(provider,
                    # host) shape from prefixed `<provider>:<host_id>`
                    # legacy keys to a dedicated `provider` column where
                    # whole-host pauses use an empty-string provider
                    # sentinel. Filter by `provider = ''` instead of the
                    # legacy LIKE-leading-wildcard pattern so the
                    # idx_host_failure_state_provider index gets used.
                    paused_rows = c.execute(
                        "SELECT host_id FROM host_failure_state "
                        "WHERE paused_at IS NOT NULL AND resolved_at IS NULL "
                        "AND provider = ''"
                    ).fetchall()
                paused_ids = {str(r["host_id"]) for r in paused_rows}
            except (sqlite3.Error, KeyError, TypeError):
                # Schema drift defence — first-deploy / pre-migration
                # path returns empty pause set rather than crashing the
                # whole tick. The runner still proceeds; ALL hosts are
                # treated as un-paused.
                pass

            # 4. Build the eligible-pool. Order by last-scan-ts ascending
            #    so hosts that have NEVER been scanned (last_ts=0) come
            #    first, then progressively newer.
            eligible: list[tuple[int, dict]] = []
            for h in curated:
                hid = str(h.get("id") or "").strip()
                if not hid:
                    continue
                ps_cfg = h.get("port_scan") if isinstance(h.get("port_scan"), dict) else {}
                # Per-host gate — match the on-demand handler's
                # forgiving semantics: only skip when the per-host
                # `enabled` key is EXPLICITLY False. Absent / null
                # inherits the master toggle (which is already true
                # at this point — the early return above bails if
                # the master is off). This avoids the trap where the
                # schedule fires every tick but skips EVERY host
                # because there's no SPA UI to opt-in per-host, so
                # `hosts_config[].port_scan` is `{}` or missing on
                # every row by default. Operators relying on the
                # master toggle + on-demand button now also benefit
                # from the schedule.
                if "enabled" in ps_cfg and not ps_cfg.get("enabled"):
                    skipped["disabled"].append(hid)
                    continue
                # Address check — the schedule only scans hosts with an
                # explicit address set, since scanning a bare host_id
                # against an unresolvable alias produces misleading
                # results (the user might think the firewall blocked
                # everything when actually DNS failed). The on-demand
                # path falls through to the host_id as a last-resort
                # target, but the schedule is more conservative.
                if not (h.get("address") or "").strip():
                    skipped["no_address"].append(hid)
                    continue
                if hid in paused_ids:
                    skipped["paused"].append(hid)
                    continue
                last_ts = last_scan_map.get(hid, 0)
                if last_ts and (now_ts - last_ts) < min_age:
                    skipped["too_recent"].append(hid)
                    continue
                eligible.append((last_ts, h))

            # Sort oldest-scanned first so the cap (`max_hosts` per
            # tick) always picks the hosts furthest behind. `last_ts=0`
            # (never-scanned) hosts get priority over any scanned ones
            # by the natural number ordering — operators adding a new
            # host see it scanned on the next eligible tick. Within
            # the scanned cohort: smallest ts (oldest scan) first,
            # working forward in time. Operator-flagged: "even if
            # they stop at 5, oldest hosts with port scans get
            # updated first" — confirming yes, this is the prevailing
            # behaviour; the log line below makes it visible per
            # tick so operators can verify the picked set matches
            # their expectation.
            eligible.sort(key=lambda t: t[0])  # oldest-first
            picks_with_ts = eligible[:max_hosts]
            picks = [h for _ts, h in picks_with_ts]

            # Eligible-but-not-picked queue depth — the rotation tail.
            # Each tick selects only `max_hosts` so on a fleet > max_hosts
            # the remaining eligible hosts wait their turn. Operators
            # complained "I have hosts not updated for 2 days" — the
            # answer is usually "they're in the rotation queue behind
            # other older-scanned hosts", but the previous log only
            # showed totals so there was no way to verify that vs an
            # actual skip-condition silent drop. Surface the QUEUE
            # tail count + the oldest-waiting host's age so an
            # operator can see "next 3 ticks will get hosts X, Y, Z".
            queue_tail = eligible[max_hosts:]
            oldest_waiting_age_s = (
                (now_ts - queue_tail[0][0]) if queue_tail and queue_tail[0][0] else 0
            )
            print(
                f"[scheduler] port_scan_refresh fire — selected={len(picks)} "
                f"queue_tail={len(queue_tail)} "
                f"oldest_waiting_s={oldest_waiting_age_s} "
                f"max_per_tick={max_hosts} min_age_s={min_age} "
                f"per_host_conc={per_host_conc} "
                f"skipped_disabled={len(skipped['disabled'])} "
                f"skipped_no_address={len(skipped['no_address'])} "
                f"skipped_paused={len(skipped['paused'])} "
                f"skipped_too_recent={len(skipped['too_recent'])}"
            )
            # Per-skip-reason host lists — only emit when the bucket is
            # non-empty so the steady-state log stays quiet. Bucket
            # `no_address` is the most operator-actionable (host won't
            # scan until they set the Address field in Admin → Hosts).
            # `paused` means the auto-pause kicked in; operator clicks
            # Resume in the host drawer. `disabled` means the per-host
            # port_scan.enabled is explicitly False. `too_recent` is
            # benign — the host just got scanned within min_age, will
            # rotate next time. Cap each list at 8 ids to keep the log
            # line readable; "+N more" suffix when truncated.
            def _fmt_skip_list(label: str, ids: list) -> None:
                if not ids:
                    return
                shown = ids[:8]
                more = len(ids) - len(shown)
                tail = f" (+{more} more)" if more > 0 else ""
                print(
                    f"[scheduler] port_scan_refresh skipped[{label}]: "
                    f"{', '.join(shown)}{tail}"
                )
            _fmt_skip_list("no_address", skipped["no_address"])
            _fmt_skip_list("disabled", skipped["disabled"])
            _fmt_skip_list("paused", skipped["paused"])
            # `too_recent` is intentionally NOT logged — on a happy
            # fleet that's the majority of the curated list every tick
            # and would drown the log. Operators chasing "why isn't
            # this host updating" can correlate via the `queue_tail`
            # count above + per-host `host_port_scans` newest_ts.

            # Per-pick visibility — emit the ordered list of picked
            # host ids + their last-scan age so operators can verify
            # the oldest-first prioritisation is actually selecting
            # the hosts they expect. Format:
            # `<hid>(<age>s)` where age is the seconds since the
            # last successful scan (0 = never-scanned, surfaces as
            # `<hid>(NEW)` for clarity). Same 8-id cap as the skip
            # lists so the log line stays scannable.
            if picks_with_ts:
                shown_picks = picks_with_ts[:8]
                more_picks = len(picks_with_ts) - len(shown_picks)
                pick_tail = f" (+{more_picks} more)" if more_picks > 0 else ""

                def _fmt_pick(item: tuple) -> str:
                    _ts, _h = item
                    _hid = str(_h.get("id") or "")
                    if not _ts:
                        return f"{_hid}(NEW)"
                    return f"{_hid}({now_ts - _ts}s)"

                pick_str = ", ".join(_fmt_pick(p) for p in shown_picks)
                print(
                    f"[scheduler] port_scan_refresh picked (oldest-first): "
                    f"{pick_str}{pick_tail}"
                )

            if not picks:
                duration = int(time.time() - started)
                _record_history_row(
                    op_id, started, duration, "success",
                    None, "0 selected (nothing eligible)",
                    {
                        "selected": [],
                        "skipped": skipped,
                        "max_hosts": max_hosts,
                        "min_age": min_age,
                    },
                )
                return duration, "success"

            # 5. Fire scans. Each call into _run_port_scan_async runs the
            #    full scan inline (no fire-and-forget) so we can wait for
            #    the whole tick to settle and stamp an aggregate history
            #    row. Per-host concurrency caps in-flight scans within
            #    THIS tick. The scan internals have their own asyncio
            #    semaphore on probe-level concurrency
            #    (tuning_port_scan_default_concurrency) so we don't
            #    double-cap.
            sem = asyncio.Semaphore(max(1, per_host_conc))
            from logic import port_scanner as _ps
            from logic.db import get_setting

            async def _scan_one(scan_host: dict) -> str:
                """Run one port-scan tick for `scan_host`; returns the scan_id string."""
                scan_hid = str(scan_host["id"])
                scan_ps_cfg_raw = scan_host.get("port_scan")
                scan_ps_cfg: dict = scan_ps_cfg_raw if isinstance(scan_ps_cfg_raw, dict) else {}
                target = (
                    (scan_host.get("address") or "").strip()
                    or scan_hid
                )
                ports_csv = (
                    (scan_ps_cfg.get("ports") or "").strip()
                    or (get_setting(Settings.PORT_SCAN_DEFAULT_PORTS) or "").strip()
                )
                ports_list = (
                    _ps.parse_port_csv(ports_csv) if ports_csv
                    else list(_ps.DEFAULT_PORTS)
                )
                # Union the host's configured app/service ports onto the
                # scan list (same as the on-demand /port-scan route) so a
                # pinned-app port is never missed by the scheduled scan.
                _app_ports: list[int] = []
                for _svc in (scan_host.get("services") or []):
                    if not isinstance(_svc, dict):
                        continue
                    for _cand in [_svc.get("port"), *(
                        (_pp.get("port") for _pp in ((_svc.get("probe") or {}).get("ports") or [])
                         if isinstance(_pp, dict))
                    )]:
                        if not isinstance(_cand, (int, str)):
                            continue
                        try:
                            _pn = int(_cand)
                        except (TypeError, ValueError):
                            continue
                        if 1 <= _pn <= 65535:
                            _app_ports.append(_pn)
                if _app_ports:
                    _seen = set(ports_list)
                    for _pn in _app_ports:
                        if _pn not in _seen:
                            ports_list.append(_pn)
                            _seen.add(_pn)
                timeout_s = (
                    scan_ps_cfg.get("timeout_s")
                    if scan_ps_cfg.get("timeout_s") is not None
                    else _tuning_mod.tuning_int(
                        Tunable.PORT_SCAN_DEFAULT_TIMEOUT_SECONDS
                    )
                )
                concurrency = (
                    scan_ps_cfg.get("concurrency")
                    if scan_ps_cfg.get("concurrency") is not None
                    else _tuning_mod.tuning_int(
                        Tunable.PORT_SCAN_DEFAULT_CONCURRENCY
                    )
                )
                # Schedule never enables UDP — UDP probes are louder and
                # operators who want UDP can run an on-demand scan from
                # the host drawer. Per-host UDP-on schedule support can
                # land in Stage 2 if the user asks for it.
                max_seconds = _tuning_mod.tuning_int(
                    Tunable.PORT_SCAN_MAX_SECONDS
                )
                snmp_cfg_raw = scan_host.get("snmp")
                snmp_cfg: dict = snmp_cfg_raw if isinstance(snmp_cfg_raw, dict) else {}
                snmp_community = (
                    snmp_cfg.get("community")
                    or get_setting(Settings.SNMP_DEFAULT_COMMUNITY)
                    or "public"
                )
                import uuid as _uuid
                scan_id = str(_uuid.uuid4())
                async with sem:
                    # noinspection PyProtectedMember
                    await _main._run_port_scan_async(
                        hid=scan_hid,
                        target=target,
                        ports_list=ports_list,
                        timeout_s=int(timeout_s) if timeout_s is not None else 0,
                        concurrency=int(concurrency) if concurrency is not None else 0,
                        banner_grab=False,
                        udp_enabled=False,
                        udp_ports_list=[],
                        udp_timeout_s=0,
                        udp_concurrency=0,
                        snmp_community=str(snmp_community),
                        max_seconds=int(max_seconds),
                        scan_id=scan_id,
                        started=time.time(),
                        h=scan_host,
                        actor=SCHEDULER_ACTOR,
                    )
                return scan_hid

            scan_results = await asyncio.gather(
                *[_scan_one(h) for h in picks],
                return_exceptions=True,
            )
            for h, r in zip(picks, scan_results):
                if isinstance(r, Exception):
                    print(
                        f"[scheduler] port_scan_refresh per-host scan failed "
                        f"host_id={h.get('id')!r} error={type(r).__name__}: {r}"
                    )
                else:
                    selected.append(h["id"])

        except Exception as e:  # noqa: BLE001
            status = "error"
            err = str(e)
            print(f"[scheduler] port_scan_refresh failed: {e}")

        duration = int(time.time() - started)
        # Aggregate history row — covers the WHOLE tick. Per-host scans
        # also wrote individual `op_type=port_scan` rows from inside
        # _run_port_scan_async, so the operator sees both: the tick
        # summary at the top, and one row per scan below it.
        target_name = f"{len(selected)} host(s) scanned"
        events_payload = {
            "selected": selected,
            "skipped": skipped,
            "skipped_first": first_skip_reason or None,
        }
        _record_history_row(
            op_id, started, duration, status,
            err, target_name, events_payload,
        )
        return duration, status

    task = asyncio.create_task(runner())
    return op_id, task  # type: ignore[return-value]


def _record_history_row(
    op_id: str,
    started: float,
    duration: int,
    status: str,
    err: Optional[str],
    target_name: str,
    events: dict,
) -> None:
    """Shared history-row writer for the port_scan_refresh tick.

    Same shape as the gather_refresh / prune_logs / asset_inventory_refresh
    direct INSERT pattern — the runner doesn't go through ops.py because
    it has no per-target Operation context (each fire scans a BATCH of
    hosts, the per-host scans have their own port_scan rows).
    """
    # Honour the canonical op_type registry rule — direct `INSERT INTO
    # history` paths must validate explicitly because they bypass
    # `new_op`. A typo here would silently land in the DB and surface
    # as untranslated raw text in the History tab.
    _ops.assert_op_type("port_scan_refresh")
    try:
        events_json = json.dumps(events, ensure_ascii=False)
    except (TypeError, ValueError):
        events_json = "{}"
    try:
        with db_conn() as c:
            c.execute(
                "INSERT INTO history "
                "(ts, op_type, target_kind, target_name, target_id, "
                " target_stack, status, duration, events, error, actor) "
                "VALUES (?, ?, 'schedule', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    started, "port_scan_refresh",
                    target_name, op_id, None,
                    status, duration,
                    events_json, err, SCHEDULER_ACTOR,
                ),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] port_scan_refresh history write failed: {e}")


async def _run_config_backup(_params: dict) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Snapshot current admin configuration to ``CONFIG_BACKUP_DIR``.

    Mirrors ``_run_backup``'s shape (no per-target Operation; synth op_id
    + history row at completion). Difference vs the full SQLite zip: this
    snapshot is settings + schedules + ai_memory only — no users / no
    sessions / no avatars / no time-series. Operators commit it to a
    private git repo for change tracking; on a fresh deploy they re-apply
    via Admin → Config backup → Import.

    Retention is the operator-tunable
    ``tuning_config_backup_retention_count`` (DB > env > default with
    bounds clamp). 0 = unlimited (matches the backup-zip default).
    """
    op_id = "sched-" + secrets.token_hex(4)

    async def runner() -> tuple[int, str]:
        """Inner coroutine spawned by this runner; returns (duration_seconds, status). Fire-and-forget — caller spawns via asyncio.create_task and the schedule loop awaits the resolved task."""
        from logic import config_export as _cfg_export
        started = time.time()
        status = "success"
        err: Optional[str] = None
        snap_name: Optional[str] = None
        try:
            result = await asyncio.to_thread(_cfg_export.save_snapshot_to_disk)
            snap_name = result.get("name")
            print(f"[scheduler] config_backup created: {snap_name}")
            try:
                from logic.tuning import Tunable, tuning_int as _tuning_int
                keep = _tuning_int(Tunable.CONFIG_BACKUP_RETENTION_COUNT)
            except (TypeError, ValueError):
                keep = 0
            if keep > 0:
                pruned = await asyncio.to_thread(_cfg_export.prune_snapshots, keep)
                if pruned:
                    print(
                        f"[scheduler] config_backup retention: pruned "
                        f"{len(pruned)} older file(s), kept {keep} newest"
                    )
        except (sqlite3.Error, OSError, RuntimeError, ValueError) as e:
            status = "error"
            err = str(e)
            print(f"[scheduler] config_backup failed: {e}")
        duration = int(time.time() - started)
        _ops.assert_op_type("config_backup")
        try:
            with db_conn() as c:
                c.execute(
                    "INSERT INTO history "
                    "(ts, op_type, target_kind, target_name, target_id, "
                    " target_stack, status, duration, events, error, actor) "
                    "VALUES (?, ?, 'schedule', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        started, "config_backup",
                        snap_name or "config_backup", op_id, None,
                        status, duration,
                        "[]", err, SCHEDULER_ACTOR,
                    ),
                )
        except sqlite3.Error as e:
            print(f"[scheduler] config_backup history write failed: {e}")
        return duration, status

    task = asyncio.create_task(runner())
    return op_id, task  # type: ignore[return-value]


async def _run_prune_config_backups(_params: dict) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Prune older config-backup snapshots down to
    ``tuning_config_backup_retention_count``. Mirrors the ``prune_logs``
    runner pattern: no Operation; writes a history row directly when done.

    Distinct from ``config_backup`` (which CREATES a new snapshot + runs
    retention as a side-effect): this runner ONLY prunes, so the operator
    can split "snapshot daily" from "retention sweep weekly" if they want
    a tighter retention enforcement cadence than the snapshot cadence.
    Idempotent — if nothing exceeds the retention count, removes 0 files.

    Retention=0 disables (matches ``prune_logs`` semantics; running with
    retention=0 logs a no-op history row so the operator can audit the
    schedule fired without surprising them with mass deletion).
    """
    op_id = "sched-" + secrets.token_hex(4)

    async def runner() -> tuple[int, str]:
        """Inner coroutine spawned by this runner; returns (duration_seconds, status). Fire-and-forget — caller spawns via asyncio.create_task and the schedule loop awaits the resolved task."""
        from logic import config_export as _cfg_export
        from logic.tuning import Tunable, tuning_int as _tuning_int

        started = time.time()
        status = "success"
        err: Optional[str] = None
        removed_count = 0
        keep = 0
        try:
            keep = _tuning_int(Tunable.CONFIG_BACKUP_RETENTION_COUNT)
            if keep > 0:
                pruned = await asyncio.to_thread(_cfg_export.prune_snapshots, keep)
                removed_count = len(pruned) if pruned else 0
        except (sqlite3.Error, OSError, RuntimeError, ValueError) as e:
            status = "error"
            err = str(e)
            print(f"[scheduler] prune_config_backups failed: {e}")
        duration = int(time.time() - started)
        # History row target_name shape mirrors prune_logs:
        # "<N> config backup(s) (keep=<retention>)"
        target_name = f"{removed_count} config backup(s) (keep={keep})"
        _ops.assert_op_type("prune_config_backups")
        try:
            with db_conn() as c:
                c.execute(
                    "INSERT INTO history "
                    "(ts, op_type, target_kind, target_name, target_id, "
                    " target_stack, status, duration, events, error, actor) "
                    "VALUES (?, ?, 'schedule', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        started, "prune_config_backups",
                        target_name,
                        op_id, None,
                        status, duration,
                        "[]", err, SCHEDULER_ACTOR,
                    ),
                )
        except Exception as e:
            print(f"[scheduler] prune_config_backups history write failed: {e}")
        return duration, status

    task = asyncio.create_task(runner())
    return op_id, task  # type: ignore[return-value]


SCHEDULE_KINDS: dict[str, KindRunner] = {
    "prune_node": _run_prune_node,
    "prune_all_nodes": _run_prune_all_nodes,
    "gather_refresh": _run_gather_refresh,
    "backup": _run_backup,
    "config_backup": _run_config_backup,
    "asset_inventory_refresh": _run_asset_inventory_refresh,
    "prune_logs": _run_prune_logs,
    "prune_notifications": _run_prune_notifications,
    "prune_config_backups": _run_prune_config_backups,
    "swarm_agent_health": _run_swarm_agent_health,
    "port_scan_refresh": _run_port_scan_refresh,
}


def bootstrap_swarm_agent_health_schedule(conn: sqlite3.Connection) -> dict:
    """First-boot helper — auto-create a default ``swarm_agent_health``
    schedule when Portainer is configured AND no equivalent row exists
    yet.

    Pre-fix the ``swarm_agent_health`` kind required an admin-created
    schedule row to fire — the underlying detection (`_agent_health`
    map populated by every gather_stats cycle) was active but had no
    runner triggering on it, so operators who never set up the
    schedule miss the autoheal feature entirely. This helper runs at
    boot inside ``_lifespan`` and creates a sensible default
    (5-minute cadence, ``notify`` action via the existing
    ``swarm_autoheal_action`` setting which defaults to ``notify``).

    Gated by THREE conditions, all of which must hold:

    1. ``swarm_autoheal_bootstrap_enabled`` setting is not ``"false"``.
       Operators who do NOT want auto-bootstrap flip this to false in
       Admin → Portainer before the Portainer settings save (or
       before the next restart, post-config). Default true.
    2. ``swarm_autoheal_bootstrap_done`` setting is unset. Latched to
       ``"true"`` after the first attempt regardless of outcome
       (created OR skipped because something already existed) so the
       helper never re-creates a deleted-on-purpose row.
    3. Portainer is configured (``logic.portainer.is_configured()``).
       Without it the schedule kind has nothing to detect against.
       This branch does NOT latch the flag — try again on the next
       boot once the operator has set up Portainer.

    Returns a status dict for diagnostic logging from the caller.
    """
    from logic.db import get_setting, set_setting
    from logic import portainer as _portainer

    bootstrap_enabled = (get_setting(Settings.SWARM_AUTOHEAL_BOOTSTRAP_ENABLED)
                         or "").strip().lower()
    bootstrap_done = (get_setting(Settings.SWARM_AUTOHEAL_BOOTSTRAP_DONE)
                      or "").strip().lower() == "true"

    if bootstrap_done:
        return {"status": "skipped_already_done"}
    if bootstrap_enabled == "false":
        # Operator opt-out — latch the flag so we don't keep retrying.
        set_setting(Settings.SWARM_AUTOHEAL_BOOTSTRAP_DONE, "true")
        return {"status": "skipped_operator_opt_out"}
    if not _portainer.is_configured():
        return {"status": "skipped_portainer_unconfigured"}

    # Already-exists check. Idempotent — operators who manually
    # created an equivalent row before this helper landed get a
    # latched flag without a duplicate.
    existing = conn.execute(
        "SELECT 1 FROM schedules WHERE kind='swarm_agent_health' LIMIT 1"
    ).fetchone()
    if existing is not None:
        set_setting(Settings.SWARM_AUTOHEAL_BOOTSTRAP_DONE, "true")
        return {"status": "skipped_existing_row"}

    # Create the default. Same shape as the seeds in
    # :func:`seed_default_schedules` — `enabled=True` because the
    # action default is `notify` (cheap / non-destructive); operators
    # who want `restart` flip the action knob in Admin → Portainer.
    name = "Swarm agent health (auto)"
    try:
        create_schedule(
            conn,
            name=name,
            kind="swarm_agent_health",
            params={},
            interval_seconds=300,  # 5 min — matches the runbook's docstring.
        )
    except sqlite3.IntegrityError:
        # Race with another bootstrap caller, or a row with the same
        # name was deleted then immediately re-created — treat as
        # already-exists for latch purposes.
        pass
    set_setting(Settings.SWARM_AUTOHEAL_BOOTSTRAP_DONE, "true")
    return {"status": "created", "name": name}


def audit_schedule_kinds() -> dict:
    """Audit gate — single source of truth for "is the schedule-kind
    plumbing consistent right now?"

    STATIC checks only — does NOT fire any runner. Firing every kind on
    boot would legitimately spawn operations (prune jobs, agent
    restarts, backups) which is unsafe even with sample params; the
    safer contract is "verify the surface is consistent" and trust
    each runner's own try/except + history-write to catch behaviour
    bugs at fire time.

    Checks:
      - Every entry in ``SCHEDULE_KINDS`` maps to a coroutine function
        (``async def``). A non-coroutine slipped into the dispatch
        table would explode at fire time when ``await runner(params)``
        runs against a regular function — better to catch it at boot.
      - Every entry's name follows the ``_run_<kind>`` convention.
        Catches typos where a kind named ``foo`` was wired to the
        ``_run_bar`` runner (silently broken on rename).
      - Every runner exposes a non-empty docstring. Conventions matter:
        new kinds without prose make the operator-facing
        ``docs/guidelines/scheduler.md`` "Current kinds" table drift.

    Returns ``{kinds_audited, missing_async, name_mismatches,
    missing_docstrings}``. Logged as one WARN line on first call (boot)
    AND surfaced via a future ``/api/admin/schedules/audit`` endpoint
    so the admin UI can render a warning chip if the codebase drifts.
    """
    import inspect
    audited = sorted(SCHEDULE_KINDS.keys())
    missing_async: list[str] = []
    name_mismatches: list[str] = []
    missing_docstrings: list[str] = []
    for kind, runner in SCHEDULE_KINDS.items():
        if not inspect.iscoroutinefunction(runner):
            missing_async.append(kind)
        # Convention check: callable name should be `_run_<kind>`.
        # Every shipping runner follows this; future contributors who
        # wire a kind to a callable with a different name should pick
        # one OR the other consistent (rename the function or alias
        # the kind).
        expected_name = f"_run_{kind}"
        actual_name = getattr(runner, "__name__", "")
        if actual_name != expected_name:
            name_mismatches.append(
                f"kind={kind!r} → {actual_name!r} (expected {expected_name!r})"
            )
        if not (runner.__doc__ or "").strip():
            missing_docstrings.append(kind)
    if missing_async:
        print(
            f"[schedules] WARN — kinds wired to non-async callables: "
            f"{missing_async}"
        )
    if name_mismatches:
        print(
            f"[schedules] WARN — kind/runner name mismatches: "
            f"{name_mismatches}"
        )
    if missing_docstrings:
        print(
            f"[schedules] WARN — kinds without runner docstrings: "
            f"{missing_docstrings}"
        )
    return {
        "kinds_audited": audited,
        "missing_async": sorted(missing_async),
        "name_mismatches": sorted(name_mismatches),
        "missing_docstrings": sorted(missing_docstrings),
    }


# ----------------------------------------------------------------------------
# Firing
# ----------------------------------------------------------------------------
async def fire_schedule(schedule: dict) -> str:
    """Fire one schedule immediately and return its op_id.

    Shared path for both the tick loop and the manual "Run now" button.
    Records the fire-time against the schedule row up-front; spawns a
    background waiter that records the final duration + status once
    the op completes.
    """
    kind = schedule.get("kind")
    runner = SCHEDULE_KINDS.get(kind or "")
    if runner is None:
        raise ValueError(f"unknown schedule kind: {kind!r}")
    params = schedule.get("params") or {}
    op_id, done_awaitable = await runner(params)

    # Stamp the fire time + op_id right now so the next tick doesn't
    # re-fire this schedule while it's still running.
    with db_conn() as c:
        record_run(c, int(schedule["id"]), op_id, duration=None, status=None)

    # SSE — fire START event so the Schedules tab can flip the row's
    # last_op_id + spinner without waiting for the next 5s poll cycle.
    try:
        from logic import events as _events
        _events.publish("schedule:fired", {
            "schedule_id": int(schedule["id"]),
            "name": schedule.get("name"),
            "kind": kind,
            "op_id": op_id,
            "phase": "start",
        })
    except (RuntimeError, ValueError, TypeError) as e:
        print(f"[events] schedule:fired (start) publish failed: {e}")

    # Waiter: completes the record_run row with the real duration and
    # status when the op finishes. Fire-and-forget — we don't await it.
    async def _await_and_record():
        """Wait for the spawned op to finish, then stamp record_run + publish end SSE."""
        try:
            duration, status = await done_awaitable
        except (asyncio.CancelledError, asyncio.InvalidStateError, RuntimeError, ValueError) as wait_err:
            if isinstance(wait_err, asyncio.CancelledError):
                raise
            print(f"[scheduler] waiter for {op_id} failed: {wait_err}")
            duration, status = 0, "error"
        try:
            with db_conn() as record_conn:
                # Pass `update_run_at=False` — the fire-time was
                # already stamped at fire moment (line 2366). The
                # waiter only records the duration + status of the
                # completed op; bumping `last_run_at` here would
                # drift the operator-visible "Last execution" time
                # forward by the op's runtime.
                record_run(record_conn, int(schedule["id"]), op_id, duration, status,
                           update_run_at=False)
        except sqlite3.Error as rec_err:
            print(f"[scheduler] record_run update for {op_id} failed: {rec_err}")
        # SSE — fire END event with the resolved duration + status so
        # the SPA can update the row in place and append the queue
        # entry without polling.
        try:
            from logic import events as _events_end
            _events_end.publish("schedule:fired", {
                "schedule_id": int(schedule["id"]),
                "name": schedule.get("name"),
                "kind": kind,
                "op_id": op_id,
                "phase": "end",
                "duration": duration,
                "status": status,
            })
        except (RuntimeError, ValueError, TypeError) as pub_err:
            print(f"[events] schedule:fired (end) publish failed: {pub_err}")

    # Lazy main import — strong-ref + done-callback contract via
    # `spawn_background_task` (see CLAUDE.md "Background-task lifecycle").
    import main as _main
    _main.spawn_background_task(
        _await_and_record(),
        label=f"schedule waiter id={schedule.get('id')} kind={kind!r}",
    )
    return op_id


# ----------------------------------------------------------------------------
# Seed defaults
# ----------------------------------------------------------------------------
def _schedule_name_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Cheap existence check — used to gate idempotent seed calls."""
    r = conn.execute(
        "SELECT 1 FROM schedules WHERE name=? LIMIT 1", (name,),
    ).fetchone()
    return r is not None


def seed_default_schedules(conn: sqlite3.Connection, nodes: list[str]) -> None:
    """Seed reasonable starter schedules on first boot only.

    Gated on the ``default_schedules_seeded`` setting — once true, no
    re-seeding ever happens, even if the operator has deleted the
    seeded rows in the meantime. Previously each seed gated on
    its own name not already existing, which meant deleting the rows
    just brought them back on the next boot. Operators with their own
    custom-named equivalents (RefreshCache, ScheduledPruneAllNodes,
    etc.) found the auto-seeded duplicates regenerating endlessly.

    Destructive defaults (``prune_node``) ship disabled; benign defaults
    (``gather_refresh``) ship enabled. Operators take it from there via
    the UI. To re-seed (e.g. after wiping the schedules table), the
    operator clears the ``default_schedules_seeded`` setting via SQL
    or the settings API.
    """
    from logic.db import get_setting, set_setting

    if (get_setting(Settings.DEFAULT_SCHEDULES_SEEDED) or "").lower() == "true":
        return

    # `seed_default_schedules` is called from BOTH
    # `_lifespan` (with empty nodes) AND the first `gather()` (with
    # nodes). On a fast-booting Swarm both calls can pass the gate
    # check above and double-INSERT before either reaches
    # `set_setting(Settings.DEFAULT_SCHEDULES_SEEDED, "true")`. Wrap the
    # entire seed sequence in `BEGIN IMMEDIATE` so SQLite serialises
    # the second caller — when it eventually acquires the lock the
    # gate-check above will already have run inside the previous
    # transaction's commit-visible snapshot, so it short-circuits and
    # the duplicate INSERT never fires. Falls through silently when
    # BEGIN IMMEDIATE fails (some test fixtures use a connection in
    # autocommit-only mode); in that case the legacy unique-name
    # check inside `create_schedule` is the safety net.
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        # Either already in a transaction, or autocommit isn't honored
        # — the per-row IntegrityError catch below handles the race.
        pass
    # Re-check inside the transaction in case the other caller won the
    # serialisation race + flipped the flag mid-flight.
    if (get_setting(Settings.DEFAULT_SCHEDULES_SEEDED) or "").lower() == "true":
        try:
            conn.commit()
        except sqlite3.Error:
            pass
        return

    seeded = False
    # A periodic cache refresh is benign and matches what a curious
    # operator would configure first anyway. Interval lines up with
    # CACHE_TTL's default so it's invisible in steady state.
    if not _schedule_name_exists(conn, "Refresh fleet cache"):
        try:
            create_schedule(
                conn,
                name="Refresh fleet cache",
                kind="gather_refresh",
                params={},
                interval_seconds=900,
            )
            seeded = True
        except sqlite3.IntegrityError:
            pass

    # Prune the first-known node daily, DISABLED. Operators explicitly
    # opt in — we don't want a scheduler to start deleting volumes on
    # first boot without consent. Empty `nodes` (first-boot before any
    # gather has run) is a no-op; the deferred call from gather() picks
    # this up once the node list is populated.
    if nodes:
        host = nodes[0]
        name = f"Prune {host}"
        if not _schedule_name_exists(conn, name):
            try:
                create_schedule(
                    conn,
                    name=name,
                    kind="prune_node",
                    params={"hostname": host},
                    interval_seconds=86400,
                    enabled=False,
                )
                seeded = True
            except sqlite3.IntegrityError:
                pass

    # Set the one-shot flag only AFTER the prune-node row exists too,
    # so the lifespan-time call (when nodes is still empty) doesn't
    # latch the flag prematurely and prevent the deferred gather-time
    # call from seeding the prune-node row.
    if nodes or _schedule_name_exists(conn, "Refresh fleet cache"):
        # We've at least had a chance to seed the prune row (either it
        # exists now, or we're past the point where it ever could).
        # On the lifespan-only path (nodes empty + cache row missing)
        # we deliberately leave the flag UNSET so the deferred gather
        # call gets a turn.
        if nodes:
            set_setting(Settings.DEFAULT_SCHEDULES_SEEDED, "true")
            if seeded:
                print("[scheduler] default schedules seeded; flag latched")

    # close the BEGIN IMMEDIATE transaction. Commit
    # whether seeded or not so the flag write (if any) lands and the
    # write lock is released for the other concurrent caller.
    try:
        conn.commit()
    except sqlite3.Error:
        pass


# ----------------------------------------------------------------------------
# Tick loop — lifespan task
# ----------------------------------------------------------------------------
def _stuck_run_threshold_seconds() -> int:
    """Seconds a fire may sit with last_duration=NULL before it's treated
    as a wedged ghost (waiter died / op hung / lifespan cancelled mid-run)
    and allowed to re-fire. Per-use read so an Admin -> Config change takes
    effect on the next tick without a restart."""
    from logic.tuning import Tunable, tuning_int
    return tuning_int(Tunable.SCHEDULE_STUCK_RUN_THRESHOLD_SECONDS)


def _is_previous_run_active(schedule: dict) -> bool:
    """True when the schedule's previous fire hasn't recorded completion yet.

    Signals:
      1. `last_op_id` is set but `last_duration` is NULL — the waiter
         hasn't stamped the outcome yet. Normally means genuinely
         in-flight, regardless of kind.
      2. If the op is still in the ops.py live dict with status='running',
         belt-and-braces for ops.py-backed kinds.

    Self-heal for the NULL-duration case: if the fire was recorded longer
    ago than the stuck-run threshold, the waiter almost certainly died
    (op hung forever, process killed mid-run, lifespan cancelled before
    the second record_run) — last_duration would otherwise stay NULL
    FOREVER and the schedule would be skipped on every tick until the
    next restart re-runs the startup ghost-sweep. Treat it as a wedged
    ghost and allow the next tick to re-fire. This is TIME-based (not
    _ops.ops-based) on purpose: synthetic-op kinds (gather_refresh) never
    enter the live ops dict, so a dict-membership check can't tell a
    running synthetic op from a wedged one — elapsed wall-clock can.
    """
    last_op_id = schedule.get("last_op_id")
    if not last_op_id:
        return False
    if schedule.get("last_duration") is None:
        last_run = int(schedule.get("last_run_at") or 0)
        if last_run and (int(time.time()) - last_run) > _stuck_run_threshold_seconds():
            print(
                f"[scheduler] '{schedule.get('name')}' previous run wedged "
                f"(last_duration NULL for > threshold) — allowing re-fire"
            )
            return False
        return True
    live = _ops.ops.get(str(last_op_id))
    return bool(live and getattr(live, "status", None) == "running")


async def scheduler_loop() -> None:
    """Check once per minute for due schedules and fire them.

    Startup behaviour: sleeps ``TICK_INTERVAL_SECONDS`` BEFORE the first
    pass so we don't immediately re-fire schedules that were due at
    process-restart time. Operators who want a "fire on restart"
    behaviour should bump last_run_at down manually, or click Run now.
    """
    # ghost-clear  — fire
    # records ``(last_op_id, last_duration=NULL)`` synchronously, then
    # spawns a fire-and-forget waiter that rewrites the row with the
    # real duration + status when the op finishes. If the lifespan is
    # cancelled mid-run (container restart, hot reload), the waiter
    # dies before its second ``record_run`` call and the NULL-duration
    # sentinel sticks forever. ``_is_previous_run_active`` reads NULL
    # as "still running" so the tick loop skips the schedule on every
    # subsequent pass — locked until the operator hand-clears the row.
    # Sweep at startup: any row whose ``last_op_id`` isn't in
    # ``_ops.ops`` (the live in-memory dict only carries currently-
    # running ops; a restart wipes it) is a ghost — stamp ``(0,
    # "error")`` so the next tick can fire normally.
    try:
        live_op_ids = set(_ops.ops.keys())
        with db_conn() as c:
            ghosts = c.execute(
                "SELECT id, name, last_op_id FROM schedules "
                "WHERE last_op_id IS NOT NULL AND last_duration IS NULL"
            ).fetchall()
            for row in ghosts:
                last_op_id = row["last_op_id"]
                if last_op_id and last_op_id not in live_op_ids:
                    # Pass `update_run_at=False` — ghost-clear runs at
                    # restart and MUST preserve the schedule's original
                    # fire-time. Without this guard the "Last execution"
                    # column displays the container restart moment, not
                    # the actual schedule fire moment (operator-flagged).
                    record_run(c, int(row["id"]), last_op_id,
                               duration=0, status="error",
                               update_run_at=False)
                    print(
                        f"[scheduler] cleared ghost run for "
                        f"'{row['name']}' (op {last_op_id} not live "
                        f"post-restart)"
                    )
    except Exception as e:
        print(f"[scheduler] ghost-clear sweep failed: {e}")

    # Initial sleep BEFORE first check. Mirrors stats_sampler_loop.
    try:
        await asyncio.sleep(TICK_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise

    while True:
        try:
            now = int(time.time())
            due: list[dict] = []
            with db_conn() as c:
                rows = c.execute(
                    "SELECT * FROM schedules WHERE enabled=1"
                ).fetchall()
                for r in rows:
                    s = _row_to_dict(r)
                    if int(s["next_run_at"]) <= now:
                        due.append(s)
            for s in due:
                # Skip-if-running: if this schedule's previous fire is
                # still in-flight (ops.py op exists with status='running',
                # OR we've stamped a last_op_id without a last_duration yet
                # so the waiter hasn't recorded completion), don't spawn
                # a second one. Overlapping prune_nodes in particular
                # would compete for the same Docker daemon.
                if _is_previous_run_active(s):
                    print(f"[scheduler] skipping '{s['name']}' — previous run still in flight")
                    continue
                # Per-schedule try/except: one broken kind or bad param
                # must not stop the rest of the tick from firing.
                try:
                    op_id = await fire_schedule(s)
                    print(f"[scheduler] fired '{s['name']}' → op {op_id}")
                except Exception as e:
                    print(f"[scheduler] '{s['name']}' fire failed: {e}")
                    # Still stamp last_run_at so a persistently-broken
                    # schedule doesn't re-fire every tick forever.
                    try:
                        with db_conn() as c:
                            record_run(
                                c, int(s["id"]),
                                f"err-{secrets.token_hex(4)}",
                                duration=0, status="error",
                            )
                    except Exception as ee:
                        print(f"[scheduler] record_run(error) failed: {ee}")
        except Exception as e:
            # Top-level guard so an unexpected error (DB locked, etc.)
            # doesn't kill the lifespan task.
            print(f"[scheduler] tick error: {e}")

        try:
            await asyncio.sleep(TICK_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
