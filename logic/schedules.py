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
import json
import secrets
import sqlite3
import time
from typing import Any, Awaitable, Callable, Optional

from logic import gather, ops as _ops
from logic.db import db_conn


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


def create_schedule(
    conn: sqlite3.Connection,
    name: str,
    kind: str,
    params: dict,
    interval_seconds: int,
    enabled: bool = True,
) -> dict:
    """Insert one schedule row and return its freshly-read representation.

    Validates kind + interval; callers are expected to have already
    validated name non-emptiness. ``IntegrityError`` on duplicate name
    is allowed to bubble — the API route translates it to HTTP 409.
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
    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO schedules "
        "(name, kind, params, interval_seconds, enabled, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (name, kind, json.dumps(params), int(interval_seconds),
         1 if enabled else 0, now, now),
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
    allowed = {"name", "kind", "params", "interval_seconds", "enabled"}
    sets = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "kind" and value not in SCHEDULE_KINDS:
            raise ValueError(f"unknown schedule kind: {value!r}")
        if key == "interval_seconds" and int(value) < MIN_INTERVAL_SECONDS:
            raise ValueError(
                f"interval_seconds must be >= {MIN_INTERVAL_SECONDS}"
            )
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


async def _run_gather_refresh(params: dict) -> tuple[str, Awaitable[tuple[int, str]]]:
    """Force-refresh the gather cache.

    Doesn't go through the ops.py system — :func:`logic.gather.gather`
    has no Operation wrapper, it's just a cache-refresh function. We
    still synthesize an op_id so the schedule row has something to
    point at in the history UI (though there's no history entry for
    this kind — see note on queue filtering).
    """
    op_id = "sched-" + secrets.token_hex(4)

    async def runner() -> tuple[int, str]:
        started = time.time()
        try:
            await gather.gather()
            return (int(time.time() - started), "success")
        except Exception as e:
            print(f"[scheduler] gather_refresh failed: {e}")
            return (int(time.time() - started), "error")

    task = asyncio.create_task(runner())
    return op_id, task  # type: ignore[return-value]


SCHEDULE_KINDS: dict[str, KindRunner] = {
    "prune_node":     _run_prune_node,
    "gather_refresh": _run_gather_refresh,
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
