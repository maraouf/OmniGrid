"""Scheduled jobs — admin-defined recurring operations.

A tiny cron-less scheduler that lives in-process alongside the rest of
PortaUpdate. Admins create named schedules (kind + params + interval)
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


# Clock-time schedules use "HH:MM" — 24-hour, container local time. Matches
# what a human types when they say "run at 1 AM". We don't accept seconds;
# per-second precision is meaningless against a 60-second tick loop.
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

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


def _today_anchor_ts(hh: int, mm: int, now: Optional[float] = None) -> float:
    """Epoch seconds for today's HH:MM in local time.

    DST transitions: we set isdst=-1 so mktime picks the right offset
    for the target day. A schedule whose HH:MM lands inside a spring-
    forward gap (e.g. 02:30 in the US spring) effectively shifts by an
    hour that day; we accept that over adding a tz library dependency.
    """
    now = now if now is not None else time.time()
    t = time.localtime(now)
    return time.mktime((
        t.tm_year, t.tm_mon, t.tm_mday,
        hh, mm, 0, 0, 0, -1,
    ))


def _next_fixed_time_run(
    hh: int, mm: int, last_run_at: Optional[int], now: Optional[float] = None,
) -> int:
    """Next epoch-seconds moment at which a daily HH:MM schedule should fire.

    Due-check contract: ``next <= now`` → fire. Three cases:
      1. Today's anchor is still in the future → next = today's anchor.
      2. Today's anchor already passed WITH a run recorded → next =
         tomorrow's anchor.
      3. Today's anchor already passed WITHOUT a run → next = tomorrow's
         anchor (we DON'T catch up on missed runs). An operator who
         creates a "nightly 01:00" schedule at noon shouldn't have it
         fire immediately; they can click Run now to backfill.

    Uses calendar-date arithmetic (not ``+ 86400`` seconds) so DST
    transitions in the host timezone don't drift the wall-clock anchor.
    """
    import datetime
    now = now if now is not None else time.time()
    anchor = _today_anchor_ts(hh, mm, now)
    last = int(last_run_at or 0)
    if now < anchor and last < anchor:
        return int(anchor)
    t = time.localtime(now)
    tomorrow = datetime.date(t.tm_year, t.tm_mon, t.tm_mday) + datetime.timedelta(days=1)
    return int(_day_anchor_ts(hh, mm, tomorrow.year, tomorrow.month, tomorrow.day))


def _day_anchor_ts(hh: int, mm: int, y: int, m: int, d: int) -> float:
    """Epoch seconds for a given Y/M/D at local HH:MM."""
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
    now = now if now is not None else time.time()
    last = int(last_run_at or 0)
    if not days_of_week:
        # No days selected — fall back to daily so a misconfigured row
        # doesn't silently never fire.
        return _next_fixed_time_run(hh, mm, last_run_at, now)
    dow_set = {int(d) for d in days_of_week if 0 <= int(d) <= 6}
    today = time.localtime(now)
    import datetime
    base_date = datetime.date(today.tm_year, today.tm_mon, today.tm_mday)
    for offset in range(8):
        d = base_date + datetime.timedelta(days=offset)
        # Python weekday(): Mon=0..Sun=6 — matches our storage convention.
        if d.weekday() not in dow_set:
            continue
        anchor = _day_anchor_ts(hh, mm, d.year, d.month, d.day)
        if anchor <= now:          # today-or-earlier and already passed
            continue
        if anchor <= last:         # already fired for this anchor
            continue
        return int(anchor)
    # Defensive fallback — shouldn't happen since at least one day is valid
    return int(now + 86400)


def _next_monthly_run(
    hh: int, mm: int, day_of_month: int,
    last_run_at: Optional[int], now: Optional[float] = None,
) -> int:
    """Next HH:MM anchor on ``day_of_month`` (clamped to last day of month).

    Day 31 on a 30-day month clamps to 30; Feb 31 clamps to 28/29. Scans
    up to 13 months. Same no-catch-up contract as the daily/weekly
    helpers: a passed anchor today with no run skips to next month.
    """
    now = now if now is not None else time.time()
    last = int(last_run_at or 0)
    dom = max(1, min(int(day_of_month), 31))
    t = time.localtime(now)
    y, m = t.tm_year, t.tm_mon
    for _ in range(14):
        last_day = calendar.monthrange(y, m)[1]
        target_day = min(dom, last_day)
        anchor = _day_anchor_ts(hh, mm, y, m, target_day)
        if anchor > now and anchor > last:
            return int(anchor)
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    return int(now + 86400)


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
    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        kind TEXT NOT NULL,
        params TEXT,
        interval_seconds INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_run_at INTEGER,
        last_duration INTEGER,
        last_status TEXT,
        last_op_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_schedules_enabled
        ON schedules(enabled, last_run_at);
    """)
    # Idempotent column adds for deployments upgrading from earlier schemas.
    # - run_at_hhmm: time-of-day anchor for daily/weekly/monthly modes.
    # - cadence_mode: which of the four modes the row is using. Legacy rows
    #   with NULL are treated as 'daily' if run_at_hhmm is set, else 'interval'.
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
            [int(x) for x in json.loads(dow_raw) if 0 <= int(x) <= 6]
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
    last_run = d.get("last_run_at")
    if mode == "interval" or not hhmm:
        base = d.get("last_run_at") or d.get("created_at") or 0
        d["next_run_at"] = int(base) + int(d.get("interval_seconds") or 0)
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
        base = d.get("last_run_at") or d.get("created_at") or 0
        d["next_run_at"] = int(base) + int(d.get("interval_seconds") or 0)
    return d


# ----------------------------------------------------------------------------
# CRUD helpers
# ----------------------------------------------------------------------------
def list_schedules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM schedules ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_schedule(conn: sqlite3.Connection, schedule_id: int) -> Optional[dict]:
    r = conn.execute(
        "SELECT * FROM schedules WHERE id=?", (schedule_id,),
    ).fetchone()
    return _row_to_dict(r) if r else None


def get_schedule_by_name(conn: sqlite3.Connection, name: str) -> Optional[dict]:
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
    conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))


def record_run(
    conn: sqlite3.Connection,
    schedule_id: int,
    op_id: str,
    duration: Optional[int],
    status: Optional[str],
) -> None:
    """Stamp the outcome of a fired schedule.

    Called twice per fire: once by the tick loop with ``duration=None,
    status=None`` immediately after kicking the op off (so the row's
    last_run_at moves forward even if the op hangs), and again by the
    waiter coroutine when the op completes (with the real duration +
    status). Passing ``None`` for either field means "don't touch it".
    """
    sets = ["last_run_at=?", "last_op_id=?", "updated_at=?"]
    now = int(time.time())
    values: list[Any] = [now, op_id, now]
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
            return (0, "error")
        if op.status != "running":
            duration = int((op.ended or time.time()) - op.started)
            return (duration, op.status)
        await asyncio.sleep(2)
    return (0, "error")


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
        target_stack=None, actor=SCHEDULER_ACTOR,
    )
    asyncio.create_task(_ops.do_prune_node(op, hostname))
    return op.id, _await_op_completion(op.id)


async def _run_prune_all_nodes(params: dict) -> tuple[str, Awaitable[tuple[int, str]]]:
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
    nodes_info = (gather._cache.get("nodes_info") or {})
    hostnames = sorted(nodes_info.keys())
    if not hostnames:
        raise ValueError(
            "prune_all_nodes: no nodes visible in cache — is Portainer reachable?"
        )

    parent_id = "sched-" + secrets.token_hex(4)
    child_ops: list[_ops.Operation] = []
    for host in hostnames:
        op = _ops.new_op(
            "prune_node", host, host,
            target_stack=None, actor=SCHEDULER_ACTOR,
        )
        child_ops.append(op)
        asyncio.create_task(_ops.do_prune_node(op, host))

    async def waiter() -> tuple[int, str]:
        results = await asyncio.gather(
            *(_await_op_completion(o.id) for o in child_ops),
        )
        longest = max((d for d, _s in results), default=0)
        all_ok = all(s == "success" for _d, s in results)
        return (longest, "success" if all_ok else "error")

    return parent_id, waiter()


async def _run_gather_refresh(params: dict) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Force-refresh the gather cache.

    Doesn't go through the ops.py system — :func:`logic.gather.gather`
    has no Operation wrapper, it's just a cache-refresh function. We
    still synthesize an op_id and write a row into the ``history``
    table on completion so the scheduler Queue UI shows this kind
    alongside ops.py-backed kinds (prune_node etc).
    """
    op_id = "sched-" + secrets.token_hex(4)

    async def runner() -> tuple[int, str]:
        started = time.time()
        status = "success"
        err: Optional[str] = None
        try:
            await gather.gather()
        except Exception as e:
            status = "error"
            err = str(e)
            print(f"[scheduler] gather_refresh failed: {e}")
        duration = int(time.time() - started)
        # Mirror the op into history so the Queue tab (which filters by
        # actor='scheduler') picks it up. We don't involve ops.persist_history
        # because that also bumps a Prometheus counter tied to op_type names
        # — keep gather_refresh out of that bucket so it doesn't inflate
        # portaupdate_ops_total with cache-refresh noise.
        try:
            with db_conn() as c:
                c.execute(
                    "INSERT INTO history "
                    "(ts, op_type, target_name, target_id, target_stack, "
                    " status, duration, events, error, actor) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        started, "gather_refresh",
                        "fleet cache", op_id, None,
                        status, duration,
                        "[]", err, SCHEDULER_ACTOR,
                    ),
                )
        except Exception as e:
            print(f"[scheduler] gather_refresh history write failed: {e}")
        return (duration, status)

    task = asyncio.create_task(runner())
    return op_id, task  # type: ignore[return-value]


async def _run_backup(params: dict) -> tuple[str, Awaitable[tuple[int, str]]]:
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
        started = time.time()
        status = "success"
        err: Optional[str] = None
        backup_name: Optional[str] = None
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, backups.create_backup)
            backup_name = result.get("name")
            print(f"[scheduler] backup created: {backup_name}")
            # Apply retention right after a successful create — matches
            # the behaviour of the manual "Create backup" button so a
            # scheduled nightly backup doesn't blow past the keep-N.
            try:
                from logic.db import get_setting
                keep = int(get_setting("backup_retention_count", "0") or "0")
            except (TypeError, ValueError):
                keep = 0
            if keep > 0:
                pruned = await loop.run_in_executor(
                    None, backups.prune_backups, keep,
                )
                if pruned:
                    print(
                        f"[scheduler] backup retention: pruned {len(pruned)} older "
                        f"file(s), kept {keep} newest"
                    )
        except Exception as e:
            status = "error"
            err = str(e)
            print(f"[scheduler] backup failed: {e}")
        duration = int(time.time() - started)
        try:
            with db_conn() as c:
                c.execute(
                    "INSERT INTO history "
                    "(ts, op_type, target_name, target_id, target_stack, "
                    " status, duration, events, error, actor) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        started, "backup",
                        backup_name or "backup", op_id, None,
                        status, duration,
                        "[]", err, SCHEDULER_ACTOR,
                    ),
                )
        except Exception as e:
            print(f"[scheduler] backup history write failed: {e}")
        return (duration, status)

    task = asyncio.create_task(runner())
    return op_id, task  # type: ignore[return-value]


SCHEDULE_KINDS: dict[str, KindRunner] = {
    "prune_node":       _run_prune_node,
    "prune_all_nodes":  _run_prune_all_nodes,
    "gather_refresh":   _run_gather_refresh,
    "backup":           _run_backup,
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

    # Waiter: completes the record_run row with the real duration and
    # status when the op finishes. Fire-and-forget — we don't await it.
    async def _await_and_record():
        try:
            duration, status = await done_awaitable
        except Exception as e:
            print(f"[scheduler] waiter for {op_id} failed: {e}")
            duration, status = 0, "error"
        try:
            with db_conn() as c:
                record_run(c, int(schedule["id"]), op_id, duration, status)
        except Exception as e:
            print(f"[scheduler] record_run update for {op_id} failed: {e}")

    asyncio.create_task(_await_and_record())
    return op_id


# ----------------------------------------------------------------------------
# Seed defaults
# ----------------------------------------------------------------------------
def seed_default_schedules(conn: sqlite3.Connection, nodes: list[str]) -> None:
    """Seed reasonable starter schedules on first boot.

    Skipped entirely once any row exists in ``schedules`` — this is a
    first-install aid, not a migration. Destructive defaults
    (prune_node) ship disabled; benign defaults (gather_refresh) ship
    enabled. Operators take it from there via the UI.
    """
    count = conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0]
    if count > 0:
        return

    # A periodic cache refresh is benign and matches what a curious
    # operator would configure first anyway. Interval lines up with
    # CACHE_TTL's default so it's invisible in steady state.
    try:
        create_schedule(
            conn,
            name="Refresh fleet cache",
            kind="gather_refresh",
            params={},
            interval_seconds=900,
            enabled=True,
        )
    except sqlite3.IntegrityError:
        pass

    # Prune the first-known node daily, DISABLED. Operators explicitly
    # opt in — we don't want a scheduler to start deleting volumes on
    # first boot without consent. If no nodes are visible yet (empty
    # cache) we skip this entirely; the UI can always add it later.
    if nodes:
        host = nodes[0]
        try:
            create_schedule(
                conn,
                name=f"Prune {host}",
                kind="prune_node",
                params={"hostname": host},
                interval_seconds=86400,
                enabled=False,
            )
        except sqlite3.IntegrityError:
            pass


# ----------------------------------------------------------------------------
# Tick loop — lifespan task
# ----------------------------------------------------------------------------
def _is_previous_run_active(schedule: dict) -> bool:
    """True when the schedule's previous fire hasn't recorded completion yet.

    Two signals:
      1. `last_op_id` is set but `last_duration` is NULL — the waiter
         hasn't stamped the outcome yet. That's the authoritative
         in-memory signal regardless of kind.
      2. If the op is still in the ops.py live dict with status='running',
         belt-and-braces for ops.py-backed kinds.
    """
    last_op_id = schedule.get("last_op_id")
    if not last_op_id:
        return False
    if schedule.get("last_duration") is None:
        return True
    live = _ops.ops.get(last_op_id)
    return bool(live and getattr(live, "status", None) == "running")


async def scheduler_loop() -> None:
    """Check once per minute for due schedules and fire them.

    Startup behaviour: sleeps ``TICK_INTERVAL_SECONDS`` BEFORE the first
    pass so we don't immediately re-fire schedules that were due at
    process-restart time. Operators who want a "fire on restart"
    behaviour should bump last_run_at down manually, or click Run now.
    """
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
