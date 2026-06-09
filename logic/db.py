"""SQLite database helpers.

Just the infrastructure: a connection context manager and KV helpers for
the ``settings`` table. Table creation (``init_db()``) lives in
``logic/schema.py`` as the boot orchestrator — each logic module that
owns tables exposes its own ``init_schema(conn)`` hook called from there.

The path is read from ``DB_PATH`` at import time; parent directory is
created on import so callers don't have to. ``DB_PATH`` is REQUIRED —
main.py calls ``load_dotenv`` before importing this module. When the
value is missing we DON'T raise at import time (that would crash-loop
the container and hide the error behind Swarm restart noise) — instead
we expose ``DB_PATH_ERROR`` so main.py can install a config-error
middleware that keeps the app up and shows a diagnostic page to the
operator. Any caller that opens ``db_conn()`` without a configured path
still raises loudly, so silent-default drift is not possible.

``DB_TYPE`` selects the backend; today only ``sqlite`` is supported.
Adding a new adapter means: extend ``_SUPPORTED_DB_TYPES``, branch on
``DB_TYPE`` in ``db_conn``, and (likely) split the table-create
statements in ``logic/schema.py``'s ``init_db()`` to handle dialect
differences.
"""
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Optional, TypedDict

from logic.env_keys import EnvKey, env_get
from logic.settings_keys import Settings

_SUPPORTED_DB_TYPES = frozenset({"sqlite"})

DB_TYPE: str = (env_get(EnvKey.DB_TYPE) or "sqlite").strip().lower()

DB_PATH: Optional[str] = env_get(EnvKey.DB_PATH) or None
DB_PATH_ERROR: Optional[str] = None

if DB_TYPE not in _SUPPORTED_DB_TYPES:
    DB_PATH_ERROR = (
        f"DB_TYPE={DB_TYPE!r} is not supported. "
        f"Set DB_TYPE to one of: {', '.join(sorted(_SUPPORTED_DB_TYPES))}."
    )
elif not DB_PATH:
    DB_PATH_ERROR = (
        "DB_PATH is not set. Define it in /app/.env "
        "(e.g. DB_PATH=/app/data/omnigrid.db) and redeploy."
    )
else:
    # Create the parent dir at import (once per process). Safe on restart —
    # exist_ok. "" dirname falls back to "." so relative paths work in dev.
    _db_path_dir = os.path.dirname(DB_PATH) or "."
    os.makedirs(_db_path_dir, exist_ok=True)

# Per-connection SQLite tuning, applied on every db_conn() open.
#
# busy_timeout + synchronous=NORMAL are PER-CONNECTION settings that take NO
# lock, so they run on every connection. busy_timeout makes a contended open
# WAIT up to N ms instead of raising SQLITE_BUSY immediately (default 0);
# synchronous=NORMAL is the SQLite-recommended pairing with WAL (durable
# across an app crash, faster than the FULL default). busy_timeout is
# env-overridable (DB_BUSY_TIMEOUT_MS) but NOT a DB-backed TUNABLE: resolving
# a tunable opens a db_conn(), which would recurse through this function.
#
# journal_mode=WAL lets readers proceed while a writer commits (the default
# rollback journal takes an EXCLUSIVE lock that blocks all readers for the
# commit). It is PERSISTENT in the DB header, so it only needs setting ONCE
# per database — and SWITCHING it acquires a write lock. Re-issuing it on
# EVERY connection (the original mistake this comment guards against) created a thundering-herd of
# journal-switch lock attempts at startup (init + migrations + seed +
# samplers all opening connections at once) AND during a start-first deploy
# rollover (old + new container sharing the bind-mounted DB), which surfaced
# as "database is locked" and could stall the new container's startup enough
# to flap its healthcheck → 502. So the journal mode is set exactly ONCE per
# process, on the FIRST connection (init_db's, before the samplers start) —
# which is what makes WAL safe to keep ON by default (on an already-WAL DB the
# one attempt is a no-op, and ext4/xfs/btrfs host WAL fine). WAL is preferred
# because it REDUCES reader/writer contention, and that matters a lot here:
# the samplers + gather hit SQLite synchronously ON THE EVENT LOOP, so less
# contention = fewer event-loop stalls = fewer healthcheck flaps.
# DB_WAL_ENABLED is the kill-switch — set 0/false/no/off to disable, and that
# same once-per-process step then REVERTS the DB to the rollback journal
# (DELETE), self-healing a DB a prior deploy left in WAL on a filesystem that
# can't host it. Wrapped defensively so a volume that can't switch falls back
# to the existing mode instead of breaking the connection.
#
# busy_timeout is deliberately MODEST (2000 ms, not 5000): sqlite3 is
# synchronous, so a contended op BLOCKS the single event loop for the whole
# wait. A 5 s busy wait froze /api/healthz long enough for Swarm to mark the
# container unhealthy and SIGKILL it (exit 137) — the 502 flap. 2 s bounds the
# stall while still riding out brief contention.
_DB_BUSY_TIMEOUT_MS_DEFAULT = 2000
_wal_attempted = False
_WAL_ENABLED = (env_get(EnvKey.DB_WAL_ENABLED) or "1").strip().lower() not in (
    "0", "false", "no", "off",
)


def _apply_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    """Per-connection SQLite tuning — see the module comment above.

    Best-effort: any PRAGMA failure (e.g. a volume that can't host WAL's
    side files, or a rollover-window lock) is logged and swallowed so the
    connection still works on the existing journal mode.
    """
    global _wal_attempted
    raw = env_get(EnvKey.DB_BUSY_TIMEOUT_MS)
    try:
        busy_ms = int(raw) if raw else _DB_BUSY_TIMEOUT_MS_DEFAULT
    except (TypeError, ValueError):
        busy_ms = _DB_BUSY_TIMEOUT_MS_DEFAULT
    if busy_ms < 0:
        busy_ms = _DB_BUSY_TIMEOUT_MS_DEFAULT
    # Run the per-connection setup PRAGMAs through a CURSOR
    # (`conn.cursor().execute(...)`), NOT `conn.execute(...)`. `conn` is a
    # `_TimedConnection`, whose `execute` / `executemany` OVERRIDES time
    # every statement and call `_check_slow_query`; `_TimedConnection`
    # does NOT override `cursor()` or the Cursor's own `execute`, so the
    # cursor path bypasses the timing wrapper (see the Coverage caveat in
    # the `_TimedConnection` docstring — the un-intercepted cursor path is
    # exactly what we want here). It also keeps the call type-clean: the
    # earlier `sqlite3.Connection.execute(conn, ...)` unbound-method form
    # passed a `_TimedConnection` as the base method's `self`, which the
    # type checker flagged ("Expected 'Self@Connection', got 'Connection'").
    # These PRAGMAs are connection-SETUP statements that run on EVERY
    # db_conn() open, not operator queries:
    #   * Timing them measures connection-establishment + writer-lock
    #     wait (busy_timeout riding out a sampler's commit), NOT query
    #     cost — which floods Admin → Logs with bogus
    #     `[slow_query] PRAGMA busy_timeout=2000` lines under contention.
    #   * Worse, `_check_slow_query` calls `tuning_int(SLOW_QUERY_*)` ->
    #     `get_setting` -> (on a cold-cache refill) a NEW `db_conn()` ->
    #     whose setup PRAGMA re-enters `_check_slow_query` again. That
    #     feedback loop opens several connections per refill, each
    #     queuing on SQLite's single-writer lock (the cycling
    #     116→203→288→371ms latency operators saw) and each logging a
    #     bogus slow-query line. The cursor path breaks the loop here.
    # Real SELECT/INSERT/UPDATE still flow through `conn.execute` (the
    # timed override) elsewhere, so the slow-query diagnostic keeps
    # working for genuine queries.
    try:
        # PRAGMA can't bind params; busy_ms is a sanitized int so the
        # f-string is injection-safe. These two take no lock.
        conn.cursor().execute(f"PRAGMA busy_timeout={busy_ms}")
        conn.cursor().execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error as e:
        print(f"[db] busy_timeout/synchronous PRAGMA skipped ({e})")
    # Set the journal mode exactly once per process (set the flag BEFORE the
    # attempt so a failed switch during a rollover window isn't retried on
    # every subsequent connection). WAL when opted in; otherwise actively
    # REVERT to the rollback journal (DELETE) so disabling WAL self-heals a DB
    # a prior deploy left in WAL.
    if not _wal_attempted:
        _wal_attempted = True
        target_mode = "WAL" if _WAL_ENABLED else "DELETE"
        try:
            # Cursor path (not the timed override) — same rationale as
            # the busy_timeout / synchronous PRAGMAs above.
            row = conn.cursor().execute(f"PRAGMA journal_mode={target_mode}").fetchone()
            print(f"[db] journal_mode set once -> {row[0] if row else '?'} (target {target_mode})")
        except sqlite3.Error as e:
            print(f"[db] journal_mode={target_mode} skipped ({e}); using existing journal mode")


@contextmanager
def db_conn():
    """Context-managed SQLite connection with Row factory.

    Commits on clean exit, closes in finally. Every connection gets a
    non-zero busy_timeout + synchronous=NORMAL; WAL is enabled once per
    process on the first connection (see ``_apply_sqlite_pragmas``) so
    readers don't block behind a writer's commit and a contended open
    waits instead of raising SQLITE_BUSY.

    Raises ``RuntimeError`` (not ``sqlite3.OperationalError``) if
    ``DB_PATH`` is unset — lets the config-error middleware in main.py
    short-circuit with a readable message instead of surfacing a raw
    SQLite error on every request.
    """
    if DB_PATH_ERROR:
        raise RuntimeError(DB_PATH_ERROR)
    if DB_TYPE != "sqlite":
        # Defensive — _SUPPORTED_DB_TYPES gate at import should have
        # caught this. If it didn't, refuse to silently open SQLite for
        # a caller that asked for something else.
        raise RuntimeError(f"db_conn(): no adapter for DB_TYPE={DB_TYPE!r}")
    if DB_PATH is None:
        # Unreachable in practice — DB_PATH_ERROR is set when DB_PATH is
        # None, and we raised above. Explicit narrowing for the type
        # checker so the sqlite3.connect call below typechecks cleanly.
        raise RuntimeError("DB_PATH is None")
    conn = sqlite3.connect(DB_PATH, factory=_TimedConnection)
    conn.row_factory = sqlite3.Row
    _apply_sqlite_pragmas(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Conservative SQL identifier pattern for the chunked-prune helper. Table /
# column names passed to `prune_rows_older_than` MUST be trusted literals
# (never user input) — this is belt-and-braces so a future caller bug can't
# smuggle an injection through the interpolated DELETE.
_SQL_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def prune_rows_older_than(table: str, cutoff_ts: int, *,
                          chunk: int = 4000, ts_col: str = "ts",
                          max_iters: int = 100_000) -> int:
    """Delete rows with ``<ts_col> < cutoff_ts`` in bounded CHUNKS, committing
    after each chunk so a large retention prune never holds the single SQLite
    writer lock for seconds.

    A plain ``DELETE FROM t WHERE ts < ?`` is ONE transaction: under WAL the
    writer lock is held for the whole delete, queueing every other writer
    (samplers, per-request session writes, the tiny db_size prune) behind it —
    which is exactly the cascade that surfaces as a storm of multi-hundred-ms
    ``[slow_query]`` warnings (the db_size prune "taking 2s" is really 2s of
    lock-WAIT behind a big sample-table delete, not 2s of work). Chunking +
    per-chunk commit caps the contiguous lock-hold to one chunk's worth of
    rows, letting queued writers interleave between chunks.

    Uses ``DELETE ... WHERE rowid IN (SELECT rowid ... WHERE ts < ? LIMIT ?)``
    so it works without SQLite's optional ``DELETE ... LIMIT`` compile flag;
    the inner SELECT seeks the table's ``(ts)`` index. Returns the total rows
    deleted. ``table`` / ``ts_col`` MUST be trusted literals (validated against
    a conservative identifier pattern; never pass user input).
    """
    if not _SQL_IDENT_RE.fullmatch(table):
        raise ValueError(f"prune_rows_older_than: unsafe table name {table!r}")
    if not _SQL_IDENT_RE.fullmatch(ts_col):
        raise ValueError(f"prune_rows_older_than: unsafe ts column {ts_col!r}")
    chunk = max(1, int(chunk))
    sql = (f"DELETE FROM {table} WHERE rowid IN "
           f"(SELECT rowid FROM {table} WHERE {ts_col} < ? LIMIT ?)")
    total = 0
    with db_conn() as c:
        for _ in range(max_iters):
            cur = c.execute(sql, (cutoff_ts, chunk))
            n = cur.rowcount or 0
            # Commit each chunk so the writer lock is released between
            # chunks (other writers can interleave); the next execute starts
            # a fresh implicit transaction.
            c.commit()
            total += n
            if n < chunk:
                break
    return total


# Frames in the stack that are infrastructure (sqlite3 internals +
# this module's own wrappers) — skipped when identifying the actual
# operator-meaningful caller in `_resolve_caller_site`. Match by
# substring against the resolved file path (cross-platform tolerant).
_SLOW_QUERY_SKIP_PATH_SUBSTRINGS: tuple[str, ...] = (
    "/logic/db.py",  # this module's _TimedConnection wrappers
    "\\logic\\db.py",
    "/sqlite3/",  # CPython sqlite3 wrapper internals
    "\\sqlite3\\",
    "/contextlib.py",  # db_conn() context manager bookkeeping
    "\\contextlib.py",
)


def _resolve_caller_site() -> str:
    """Walk the call stack and return the first frame OUTSIDE this
    module + the sqlite3 internals + `contextlib` (the `db_conn()`
    context-manager bookkeeping). That's the operator-meaningful
    caller — typically a route handler, sampler tick, or schedule
    runner. Returns a short `module:function:lineno` string suitable
    for inline log-line interpolation.

    Falls back to ``"<unknown>"`` on any traversal failure so the
    diagnostic NEVER breaks the actual query path.

    Cost: `sys._getframe` is O(depth) but Python's frame walk is
    cheap (~microseconds for typical 20-frame stacks) — fired ONLY
    on queries that already crossed the operator-tunable threshold,
    so the steady-state cost is zero.
    """
    try:
        import sys as _sys
        # Start at the immediate caller of `_resolve_caller_site` (depth=1)
        # and walk outward until we find a frame that isn't in the skip
        # list. `_getframe` is private but stable across CPython releases
        # and avoids the much-more-expensive `inspect.stack()` (which
        # builds a list of FrameInfo objects for the full traversal).
        depth = 1
        while True:
            try:
                frame = _sys._getframe(depth)
            except ValueError:
                # Walked past the top of the stack without finding a
                # non-infrastructure frame. Should never happen in
                # practice (the route handler / sampler is always
                # somewhere up the stack) but defence-in-depth.
                return "<unknown>"
            path = frame.f_code.co_filename
            if not any(needle in path for needle in _SLOW_QUERY_SKIP_PATH_SUBSTRINGS):
                # Found the operator-meaningful frame. Strip the
                # repo prefix so the output reads as a relative path
                # (e.g. `main_pkg/hosts_routes.py:1234` not
                # `/app/main_pkg/hosts_routes.py:1234`).
                fn = frame.f_code.co_name
                lineno = frame.f_lineno
                # Best-effort relative path — try /app/ (production
                # container path) first, then the repo root marker.
                for prefix in ("/app/", "\\app\\"):
                    if prefix in path:
                        path = path.split(prefix, 1)[1]
                        break
                else:
                    # Repo-root fallback: take everything after the
                    # last `OmniGrid/` (works for both Windows dev
                    # checkouts + the production /app/ path).
                    for marker in ("OmniGrid/", "OmniGrid\\"):
                        if marker in path:
                            path = path.split(marker, 1)[1]
                            break
                # Normalise to forward slashes for consistent log
                # output across Windows dev + Linux prod.
                path = path.replace("\\", "/")
                return f"{path}:{fn}:{lineno}"
            depth += 1
    except (AttributeError, TypeError, ValueError, IndexError, OSError):
        # AttributeError: frame.f_code missing in some embedded
        # Python builds. TypeError / ValueError: string ops on a
        # path that decoded to a non-str (extremely unlikely on
        # CPython but harmless to guard). IndexError: the path
        # split chain on a malformed import path. OSError: env_get
        # / sys-module access in a sandboxed runtime. Cancellation
        # exceptions (asyncio.CancelledError / KeyboardInterrupt)
        # deliberately NOT caught so they propagate to the asyncio
        # runtime.
        return "<unknown>"


# Re-entrancy guard for `_check_slow_query`. The threshold lookup inside
# it (`tuning_int` -> `get_setting`) can itself run a TIMED statement —
# the settings-cache refill SELECT when the read-through cache is cold —
# which routes straight back into `_check_slow_query` -> `tuning_int` ->
# `get_setting` -> ... a feedback loop that opens a fresh connection per
# level and queues each on SQLite's single-writer lock (the cycling
# 116→203→288→371ms `PRAGMA busy_timeout` latency operators saw). The
# guard makes any nested invocation on the same call chain a no-op, so
# the timer measures the OUTER query exactly once and the threshold
# lookup's own infrastructure queries can't recurse. Plain module bool
# (not threading.local): the recursion is SYNCHRONOUS on one thread (see
# the RLock note on the settings cache), so a bool correctly catches
# re-entrancy; a rare cross-thread false-skip just leaves one concurrent
# slow query unlogged, which the streak dedup already tolerates.
_slow_query_check_active = False


def _check_slow_query(sql: str, t0: float) -> None:
    """Per-call timing check — emit `[slow_query] warning:` when the
    elapsed wall-clock for a SQLite execute / executemany exceeds the
    operator-tunable threshold (`tuning_slow_query_threshold_ms`,
    default 0 = disabled).

    Diagnostic must NEVER break the actual query. Every failure path
    (import errors during boot, tunable resolver edge case, log
    formatting failures, etc.) is swallowed silently — the query's
    result has already been returned to the caller by the time we
    reach here (the timing check runs in a `finally` block).

    Log-line shape:
        [slow_query] warning: 862.6ms (threshold 100ms)
            site=main_pkg/hosts_routes.py:api_hosts_list:1234
            sql=PRAGMA busy_timeout=2000

    The ``site=`` field carries the first operator-meaningful frame
    (route handler / sampler tick / schedule runner) so operators
    diagnosing a slow query can jump straight to the caller without
    reading the SQL and reverse-engineering which surface ran it.
    """
    global _slow_query_check_active
    if _slow_query_check_active:
        # Re-entrant call from the threshold lookup's own infrastructure
        # query — skip so the timer can't recurse into a
        # connection-per-level storm (see the guard declaration above).
        return
    _slow_query_check_active = True
    try:
        # Late-import to avoid a load-time cycle between
        # `logic/db.py` and `logic/tuning.py` (tuning depends on
        # db.get_setting at module init for its read-through resolver,
        # so importing tuning at db.py module-top would deadlock).
        # Cost: one dict lookup per call once `logic.tuning` is in
        # `sys.modules` (Python caches the import — repeat imports
        # are ~50ns).
        from logic.tuning import tuning_int, Tunable
        threshold = tuning_int(Tunable.SLOW_QUERY_THRESHOLD_MS)
        if threshold <= 0:
            # Default state — slow-query log is OFF. Zero per-call
            # overhead past the threshold lookup.
            return
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if elapsed_ms <= threshold:
            return
        # Truncate the SQL text to keep the log line readable; collapse
        # whitespace so a multi-line statement renders as one log line.
        sql_short = sql[:200] + "…" if len(sql) > 200 else sql
        sql_short = " ".join(sql_short.split())
        # Identify the operator-meaningful caller (route handler,
        # sampler tick, schedule runner) so the operator can jump
        # straight to the culprit. Frame walk runs ONLY on queries
        # that already crossed the threshold — zero steady-state cost.
        site = _resolve_caller_site()
        # Per-(site, sql) dedup so a burst of identical slow-query
        # warnings collapses to ONE verbose line + one streak-counted
        # WARN summary every ``_SLOW_QUERY_DEDUP_SUMMARY_EVERY`` hits.
        # Pre-fix: a Beszel/Pulse sampler tick holding the writer lock
        # for ~500ms could queue 100+ readers, each spamming the log
        # with the SAME `(site, PRAGMA busy_timeout=2000)` pair — the
        # actual signal (which site is queuing) was buried in N-deep
        # noise. The pattern mirrors `host_net_sampler._failure_streak`.
        sig = f"{site}|{sql_short}"
        prev = _slow_query_streak.get(sig)
        now_mono = time.monotonic()
        if prev is None:
            # First occurrence of this (site, sql) pair: log verbosely
            # so the operator sees full diagnostic context once.
            # Opportunistic cap-eviction — drop the OLDEST tracked
            # signature when at the cap so the dict stays bounded
            # under a long-running process with many distinct sites.
            if len(_slow_query_streak) >= _SLOW_QUERY_STREAK_CAP:
                oldest_sig = min(
                    _slow_query_streak,
                    key=lambda k: _slow_query_streak[k]["first_ts"],
                )
                _slow_query_streak.pop(oldest_sig, None)
            _slow_query_streak[sig] = {"streak": 1, "first_ts": now_mono}
            print(
                f"[slow_query] warning: {elapsed_ms:,.1f}ms "
                f"(threshold {threshold:,}ms) site={site} sql={sql_short}"
            )
        else:
            prev["streak"] += 1
            streak = prev["streak"]
            # Emit a streak-counted summary line every Nth repeat so the
            # operator sees the storm is ongoing AND its severity without
            # being flooded. First summary at streak==2 (so the second
            # hit is visible immediately) then every Nth after that.
            if streak == 2 or (streak % _SLOW_QUERY_DEDUP_SUMMARY_EVERY) == 0:
                age_s = int(now_mono - prev["first_ts"])
                print(
                    f"[slow_query] warning: streak={streak} "
                    f"first_hit {age_s}s ago, latest {elapsed_ms:,.1f}ms "
                    f"(threshold {threshold:,}ms) site={site} sql={sql_short}"
                )
    except (ImportError, KeyError, RuntimeError, TypeError,
            AttributeError, sqlite3.Error, OSError, ValueError):
        # Diagnostic MUST NEVER break the query path. Concrete
        # exception classes cover the realistic failure modes:
        # ImportError    — late `from logic.tuning import` cycle.
        # KeyError       — Tunable enum missing in TUNABLES.
        # RuntimeError   — `db_conn()` raises when DB_PATH unset.
        # TypeError      — sql not a str (caller bug).
        # AttributeError — prev["streak"] += 1 against a non-dict
        #                  if a future contributor swaps the value
        #                  shape without updating the TypedDict.
        # sqlite3.Error  — tunable resolver hit a corrupt DB.
        # OSError        — print to a broken stream / EBADF.
        # ValueError     — int(now_mono - ...) on a NaN delta.
        # Cancellation exceptions (asyncio.CancelledError /
        # KeyboardInterrupt) deliberately NOT caught so they
        # propagate to the asyncio runtime.
        return
    finally:
        # Always clear the re-entrancy guard, including on the early
        # `return` in the except branch above — otherwise one swallowed
        # exception would wedge the guard True and silence the
        # slow-query diagnostic for the rest of the process lifetime.
        _slow_query_check_active = False


# Typed shape for the streak-tracker values — without it PyCharm
# (correctly) can't narrow `prev["streak"]` past `Any`, which makes
# the `streak % N` modulo comparison and the `int(now_mono -
# prev["first_ts"])` arithmetic both raise type warnings.
class _SlowQueryStreakEntry(TypedDict):
    streak: int
    first_ts: float


# Per-(site, sql) streak tracker for `_check_slow_query` dedup. Keys
# are `f"{site}|{sql_short}"`; values are `{"streak": int, "first_ts":
# monotonic}`. Pruned opportunistically on each insert via the cap
# below — operator-visible storms typically have <50 distinct signatures
# so the dict stays small. Single-process single-replica makes the
# plain dict + GIL-atomic ops correct.
_slow_query_streak: dict[str, _SlowQueryStreakEntry] = {}
# Hard cap on streak-tracker entries. Beyond this the OLDEST entry
# (first_ts) is evicted so a long-running process with thousands of
# distinct (site, sql) pairs doesn't grow unbounded. Operator-visible
# storms are bounded (~50 sites × ~10 sql shapes); 256 leaves plenty
# of headroom.
_SLOW_QUERY_STREAK_CAP = 256
# Emit a streak-counted summary every Nth repeat after the first
# verbose log. Tuned so a 100-hit burst produces ~10 summary lines
# (visible signal without flooding) instead of 100 identical ones.
_SLOW_QUERY_DEDUP_SUMMARY_EVERY = 25


def _slow_query_reset_streak(sig: str) -> None:
    """Drop a (site, sql) streak — exposed for tests + manual clears."""
    _slow_query_streak.pop(sig, None)


class _TimedConnection(sqlite3.Connection):
    """sqlite3.Connection subclass that times every `execute` /
    `executemany` shortcut and emits a `[slow_query] warning:` log
    line when the elapsed wall-clock exceeds
    `tuning_slow_query_threshold_ms`.

    Default threshold is 0 = disabled, so the only steady-state cost
    is a `tuning_int` cached-dict lookup per call (~50ns) + a
    `perf_counter` pair (~100ns). Enabling the wrap (operator sets
    threshold > 0 in Admin → Config) adds the format + print cost
    ONLY on lines that exceed the threshold.

    Subclassing via `sqlite3.connect(path, factory=_TimedConnection)`
    is the canonical sqlite3 way to extend Connection behaviour —
    monkey-patching `conn.execute = ...` fails because sqlite3's C
    methods reject Python attribute assignment (the previous attempt
    crashed the container at boot with that exact error).

    Coverage caveat: this overrides ONLY the Connection.execute /
    executemany shortcuts. `c.cursor().execute(...)` is the Cursor
    object's own execute method which is NOT intercepted here.
    Audit recipe: `grep -rEn 'cursor\\(\\)\\.execute\\(' main.py
    main_pkg/ logic/` — every hit should be evaluated. There is exactly
    ONE intentional cursor-path site: `_apply_sqlite_pragmas` runs the
    connection-setup PRAGMAs (busy_timeout / synchronous / journal_mode)
    via `conn.cursor().execute(...)` SPECIFICALLY to bypass this timing
    wrapper — those are infrastructure statements run on every open, not
    operator queries, and timing them both flooded the log and created a
    `_check_slow_query` -> `tuning_int` -> `get_setting` -> `db_conn`
    re-entry storm. Do NOT "fix" those back to the Connection shortcut.
    Every OTHER query in the codebase uses the Connection shortcut shape,
    so timing coverage of real queries is complete. If a future
    contributor adds a NEW cursor-path site that DOES want timing, either
    switch it to the shortcut OR extend this with a `cursor(self)`
    override that returns a `_TimedCursor` subclass.
    """

    def execute(self, sql, *args, **kwargs):  # type: ignore[override]
        """Timed override of ``sqlite3.Connection.execute``. Forwards
        every arg to the C-implemented base method; the wrapping cost
        is one ``perf_counter`` pair + the slow-query check (early-
        return when the threshold is 0 = disabled)."""
        t0 = time.perf_counter()
        try:
            return super().execute(sql, *args, **kwargs)
        finally:
            _check_slow_query(sql, t0)

    def executemany(self, sql, *args, **kwargs):  # type: ignore[override]
        """Timed override of ``sqlite3.Connection.executemany``. Same
        wrapping shape as :meth:`execute` — the bulk-INSERT path used
        by every sampler's `_persist_tick` flows through here."""
        t0 = time.perf_counter()
        try:
            return super().executemany(sql, *args, **kwargs)
        finally:
            _check_slow_query(sql, t0)


# read-through cache for the settings KV. get_setting (and the
# get_setting_bool / load_settings_json / tuning_int / active_host_stats_
# providers / iter_curated_hosts helpers that all funnel through it) is read
# MANY times per gather / request / sampler tick, and each call was a fresh
# connect + SELECT + close. This loads the WHOLE (small) settings table once
# per TTL window and serves dict lookups. Correctness:
#   - INVALIDATED immediately by set_setting + the version bump, so a
#     read-after-write in the SAME request sees the new value (single-process
#     + synchronous set_setting → nothing runs between the write and the next
#     get, so mid-call invalidation is safe).
#   - a short TTL backstop self-corrects any write path that bypasses
#     set_setting (config-restore bulk INSERT, migrations) within a few seconds.
# The per-use tuning_int contract is preserved (a tunable edit shows on the
# next get within the TTL, and immediately after its set_setting). Single-
# process single-replica → a plain module dict is correct (GIL-atomic dict
# ops; worst case a redundant reload).
_SETTINGS_KV_CACHE: dict = {}
_SETTINGS_KV_CACHE_TS = 0.0
_SETTINGS_KV_CACHE_TTL = 3.0
# Refill mutex — single-flight guard around the cache reload so that
# N concurrent cache-miss callers don't all open N parallel SQLite
# connections that then queue on the file lock. Pre-fix: when the
# TTL expired during a hot moment (sampler tick + gather + several
# admin polls), every reader would hit `get_setting`, find the cache
# expired, and call `db_conn()`. With SQLite's single-writer model
# and a sampler holding the write lock, each connection-open's first
# `PRAGMA busy_timeout=2000` waited 100ms+ for the lock — surfacing
# as the linear-growth "slow_query" storm operators saw in Admin →
# Logs (101.1ms, 104.3ms, 107.3ms, ... each ~3ms longer because each
# caller waited one slot longer in the queue). Now: only the first
# caller refills the cache; subsequent callers wait briefly on this
# `threading.Lock` (held microseconds for the dict swap; milliseconds
# at worst for the SELECT) AND read the now-warm cache when they
# acquire. ONE connection opened per refill instead of N. Idiomatic
# single-flight pattern for read-through caches.
import threading as _threading  # noqa: E402,PLC0415

# RLock (not Lock) — the SELECT inside the refill goes through
# `_TimedConnection.execute` -> `_check_slow_query` ->
# `tuning_int(...)` -> `get_setting(...)`, which re-enters THIS
# function on the SAME thread while the lock is held. A plain
# `Lock` deadlocks on the recursive acquire (catastrophic:
# lifespan startup hangs at "Waiting for application startup",
# healthcheck misses, swarm SIGKILLs the container — exit 137
# crash-loop). `RLock` lets the same thread re-acquire freely;
# cross-thread contention is still serialized as designed.
_SETTINGS_KV_CACHE_LOCK = _threading.RLock()


def _invalidate_settings_cache() -> None:
    """Force the next ``get_setting()`` to reload the full settings table."""
    global _SETTINGS_KV_CACHE_TS
    _SETTINGS_KV_CACHE_TS = 0.0


def get_setting(key: str, default: str = "") -> str:
    """Read one settings row, returning `default` when the key isn't set.

    Served from the process-level read-through cache (see
    ``_SETTINGS_KV_CACHE``); reloads the whole (small) settings table when the
    cache is empty or older than the TTL. The reload is wrapped in
    ``_SETTINGS_KV_CACHE_LOCK`` so concurrent cache-miss callers share ONE
    refill instead of opening N parallel connections (which would queue on
    SQLite's writer lock during a sampler tick and surface as the
    slow-query storm operators saw pre-fix).
    """
    global _SETTINGS_KV_CACHE, _SETTINGS_KV_CACHE_TS
    # Multi-field-Save batch buffer wins for read-after-write inside the
    # batch context: a value the handler just wrote (still buffered, not yet
    # committed) must be visible to a same-request re-read. See
    # `_settings_write_buffer`.
    if _settings_write_buffer[0] is not None and key in _settings_write_buffer[0]:
        return _settings_write_buffer[0][key]
    now = time.monotonic()
    if not _SETTINGS_KV_CACHE_TS or (now - _SETTINGS_KV_CACHE_TS) >= _SETTINGS_KV_CACHE_TTL:
        # Single-flight refill — N concurrent cache-miss callers share ONE
        # connection-open + SELECT instead of opening N parallel connections
        # that all queue on SQLite's writer lock. Inside the lock, re-check
        # the TS so callers that lost the lock race read the now-warm cache
        # (someone else just refilled it) instead of refilling again.
        with _SETTINGS_KV_CACHE_LOCK:
            now2 = time.monotonic()
            if not _SETTINGS_KV_CACHE_TS or (now2 - _SETTINGS_KV_CACHE_TS) >= _SETTINGS_KV_CACHE_TTL:
                with db_conn() as c:
                    rows = c.execute("SELECT key, value FROM settings").fetchall()
                _SETTINGS_KV_CACHE = {r["key"]: r["value"] for r in rows}
                _SETTINGS_KV_CACHE_TS = now2
    return _SETTINGS_KV_CACHE.get(key, default)


def read_location_setting(lat_key: str, lon_key: str, label_key: str):
    """Read a ``{lat, lon, label}`` location triple from three settings
    keys, or ``None`` when either coordinate is unset / non-numeric.

    Shared by every "operator-configured default location" resolver
    (``logic.weather.default_location`` /
    ``logic.prayer_times.default_location``) so the parse + validate +
    shape lives in ONE place instead of being copied per feature."""
    lat_raw = (get_setting(lat_key) or "").strip()
    lon_raw = (get_setting(lon_key) or "").strip()
    if not lat_raw or not lon_raw:
        return None
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        return None
    return {
        "lat": lat,
        "lon": lon,
        "label": (get_setting(label_key) or "").strip(),
    }


def set_setting(key: str, value: str) -> None:
    """Upsert one row into the ``settings`` table.

    Bumps `_settings_version` (a synthetic monotonic int) so the SPA
    can poll a cheap version endpoint to detect cross-tab changes
    without re-fetching the full settings blob. Excluded: the version
    row itself (avoids recursion) AND every key in
    `_SETTINGS_VERSION_EXCLUDED` — high-frequency housekeeping rows
    (Telegram listener offset advances per inbound message, swarm
    autoheal cooldown anchors, etc.) that are NOT cross-tab-relevant
    and would otherwise produce a settings-reload storm on every
    connected SPA tab. Multi-field admin Saves can call this N times
    per request — wrap in `defer_settings_version_bump()` to collapse
    the N bumps into ONE end-of-request bump (the SPA sees one
    version mismatch instead of N reloads).
    """
    # Multi-field-Save batch buffer — collect non-excluded writes into one
    # transaction at context exit. Excluded keys (the version row itself + the
    # high-frequency background writes) bypass the buffer so they commit
    # immediately, which keeps background ticks (telegram listener offset,
    # swarm autoheal cooldown anchors) unaffected even if they race the
    # admin-save handler's `await`s. See `_settings_write_buffer`.
    if (_settings_write_buffer[0] is not None
        and key != _SETTINGS_VERSION_KEY
        and key not in _SETTINGS_VERSION_EXCLUDED):
        _settings_write_buffer[0][key] = value
        # Keep the read-through cache coherent so a same-request get_setting
        # returns the buffered value through the cache path too (the buffer
        # check in get_setting is the primary read-after-write guarantee;
        # this is belt-and-braces for any direct cache touch).
        if _SETTINGS_KV_CACHE_TS:
            _SETTINGS_KV_CACHE[key] = value
        # Mark version-bump pending so the outer defer context bumps once on
        # exit — same contract as the inline-write path's pending flag below.
        _settings_version_pending[0] = True
        return
    with db_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
            (key, value),
        )
        # Update the read-through cache IN PLACE with the new value rather
        # than invalidating the whole cache. Pre-fix every set_setting call
        # reset `_SETTINGS_KV_CACHE_TS = 0`, forcing the NEXT get_setting to
        # re-SELECT every settings row. Background writers (Telegram listener
        # offset advances on every inbound message, swarm autoheal cooldown
        # anchors per tick) fire many times per second; each invalidation
        # cascaded into a fresh DB connection from the first tuning_int caller
        # afterwards, which queued on SQLite's writer lock and surfaced as the
        # `_read_raw_tunable:30 sql=PRAGMA busy_timeout=2000` slow-query storm
        # (operator-reported: streak=3225 in 107s with linear latency growth
        # from 100ms to 500ms+). In-place update preserves cache freshness AND
        # avoids the per-write refill, eliminating the storm at its source.
        # The cache TS is untouched — the existing TTL still refreshes the
        # cache periodically to catch any drift from out-of-band writes (e.g.
        # a direct sqlite3 CLI session) without paying the per-write cost.
        if _SETTINGS_KV_CACHE_TS:
            _SETTINGS_KV_CACHE[key] = value
        if key == _SETTINGS_VERSION_KEY or key in _SETTINGS_VERSION_EXCLUDED:
            return
        if _settings_version_deferred_count[0] > 0:
            # A defer-context is active — accumulate one notional bump
            # and let the context exit issue a single `_bump_settings_version_in`
            # at the end. Multi-field admin Saves through `api_set_settings`
            # collapse N bumps into one this way.
            _settings_version_pending[0] = True
            return
        _bump_settings_version_in(c)


_SETTINGS_VERSION_KEY = "_settings_version"
# High-frequency housekeeping keys that should NOT bump the
# settings-version + fire a cross-tab SSE event. These are written
# many-times-per-second under normal load (Telegram listener offset,
# swarm autoheal cooldown anchors) and the value isn't operator-
# relevant — broadcasting `settings:updated` for them would force
# every connected SPA tab to re-fetch `/api/settings` + `/api/me`,
# producing a reload storm on chatty deploys. Adding a key here is
# safe iff: (a) no SPA surface reads it via `/api/settings`, (b) the
# value is timestamp / counter / offset semantics (write-mostly).
_SETTINGS_VERSION_EXCLUDED = frozenset({
    "telegram_last_update_id",
    "swarm_autoheal_last_restart_ts",
    "swarm_autoheal_last_notify_ts",
    "swarm_autoheal_last_notify_set",
    "swarm_autoheal_bootstrap_done",
    # First-boot-only seed flag — written ONCE by the schedule
    # seeder on a fresh DB, never read by any SPA surface. No tab
    # cares when it flips so the version bump + SSE fan-out is wasted
    # work. Same housekeeping category as the swarm_autoheal flags
    # above.
    "default_schedules_seeded",
    # Per-host Tdarr bloated-scan wall-clock history (JSON map host_id ->
    # [seconds,...]) used to show a measured "this usually takes ~Xs" ETA
    # instead of static copy. Written on every completed scan; no SPA tab
    # reads it, so the version bump + SSE fan-out would be wasted work.
    "tdarr_scan_durations",
})
# Single-element lists used as nullable-int / nullable-bool boxes so the
# context manager + nested-defer support work without globals + lock
# ceremony. OmniGrid runs single-process single-replica by deployment
# invariant; an asyncio re-entrant defer would still write the right
# count because the context manager's enter/exit is synchronous.
_settings_version_deferred_count = [0]
_settings_version_pending = [False]
# Multi-field admin Save batch buffer — when active (set by
# `batch_settings_writes()`) the OUTER context manager collects non-excluded
# `set_setting` calls into one dict and flushes them in ONE INSERT-OR-REPLACE
# transaction on exit (instead of N separate db_conn open/insert/commit/close
# cycles). Excluded keys (high-frequency background writes — telegram offset,
# swarm autoheal cooldowns) bypass the buffer and commit immediately, so a
# background tick during the save handler's `await`s can't mis-buffer.
# Single-element list mirrors the version-bump defer pattern above.
_settings_write_buffer: list = [None]


@contextmanager
def defer_settings_version_bump():
    """Context manager that collapses N `set_setting` calls into ONE
    `_settings_version` bump on context exit.

    Usage:

        with defer_settings_version_bump():
            for k, v in fields.items():
                set_setting(k, v)
        # → exactly one version bump after the with-block exits

    Multi-field admin Saves through `api_set_settings` would otherwise
    bump the counter N times and trigger N cross-tab `settings:updated`
    SSE events, each prompting a `/api/settings` reload on every other
    tab. The defer context ensures other tabs see ONE version mismatch
    per logical operation. Nestable — only the outermost exit triggers
    the actual bump.
    """
    _settings_version_deferred_count[0] += 1
    try:
        yield
    finally:
        _settings_version_deferred_count[0] -= 1
        if _settings_version_deferred_count[0] == 0 and _settings_version_pending[0]:
            _settings_version_pending[0] = False
            try:
                with db_conn() as c:
                    _bump_settings_version_in(c)
            except (sqlite3.Error, RuntimeError, OSError):
                # Defence-in-depth: a defer-exit bump failure must NOT
                # propagate out of the context manager and break the
                # caller's request. SPA loses one cross-tab notification
                # at worst — recoverable on next poll.
                pass


@contextmanager
def batch_settings_writes():
    """Buffer non-excluded ``set_setting`` calls and flush them in ONE
    ``INSERT OR REPLACE`` transaction at context exit.

    Usage:

        with batch_settings_writes():
            for k, v in fields.items():
                set_setting(k, v)
        # → one transaction commits every buffered write

    A multi-field admin Save would otherwise open N db_conn / commit / close
    cycles. The cross-tab ``settings:updated`` storm is already collapsed by
    :func:`defer_settings_version_bump`; this complements it by collapsing
    the row writes themselves. Excluded keys (the version row +
    ``_SETTINGS_VERSION_EXCLUDED`` housekeeping rows) bypass the buffer and
    commit immediately, so a high-frequency background tick (telegram
    listener offset, swarm autoheal cooldowns) that races the save handler's
    ``await``s can't mis-buffer into this request's flush.

    Read-after-write inside the context is correct: :func:`get_setting`
    checks the buffer FIRST before falling back to the cache + DB.

    Nestable — only the outermost exit flushes. The flush runs in ``finally``
    so writes the handler made before raising still persist (preserves the
    pre-batch "partial writes persist on a mid-way exception" behaviour
    closely; the writes commit at exit instead of incrementally during the
    handler). A flush failure is swallowed — same defence-in-depth as the
    version-bump defer's flush-failure handling — so the caller's request
    isn't broken by a transient DB error on the way out.
    """
    if _settings_write_buffer[0] is not None:
        # nested — reuse the outer buffer; only the outermost flushes
        yield
        return
    _settings_write_buffer[0] = {}
    try:
        yield
    finally:
        buf = _settings_write_buffer[0]
        # Deactivate BEFORE the flush so the flush's db_conn + any get_setting
        # called from the flush path (none today, defence-in-depth) see normal
        # mode — the buffered values are about to land in the DB anyway.
        _settings_write_buffer[0] = None
        if buf:
            try:
                with db_conn() as c:
                    c.executemany(
                        "INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
                        list(buf.items()),
                    )
            except (sqlite3.Error, RuntimeError, OSError):
                pass
            # Invalidate the read-through cache so post-context get_setting
            # calls reload from the committed DB state.
            _invalidate_settings_cache()


def _bump_settings_version_in(c) -> None:
    """Increment `_settings_version` inside an existing connection.
    Caller is responsible for the commit (the surrounding `db_conn()`
    context manager handles it). Treats a missing row as starting from
    0 → 1; a malformed value rolls back to 0 → 1 too so corrupt state
    self-heals."""
    try:
        row = c.execute(
            "SELECT value FROM settings WHERE key=?",
            (_SETTINGS_VERSION_KEY,),
        ).fetchone()
        cur = 0
        if row and row["value"]:
            raw_val = row["value"]
            try:
                cur = int(str(raw_val))
            except (TypeError, ValueError):
                cur = 0
        c.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
            (_SETTINGS_VERSION_KEY, str(cur + 1)),
        )
        # the version row changed via a direct write — invalidate so
        # get_settings_version() (and any get_setting) reloads.
        _invalidate_settings_cache()
    except (sqlite3.Error, RuntimeError):
        # Defence-in-depth: a version-bump failure must NOT roll back
        # the operator's actual settings write. Worst case the SPA
        # misses a cross-tab notification — recoverable on next poll.
        pass


def get_settings_version() -> int:
    """Return the current `_settings_version`. 0 when never written.
    Used by `/api/settings/version` so the SPA can detect cross-tab
    changes without re-fetching the full settings blob."""
    raw = get_setting(_SETTINGS_VERSION_KEY)
    if not raw:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


# Truthy strings accepted by :func:`get_setting_bool`. The save path
# always normalises to literal "true"/"false", but DB-direct edits or
# restored backups from older schemas may carry "True", "1", "yes"
# etc. — accept those too so a stray edit doesn't silently flip a
# master toggle off.
_TRUTHY_STRINGS = frozenset({"true", "1", "yes", "y", "on"})
_FALSY_STRINGS = frozenset({"false", "0", "no", "n", "off"})


# noinspection SpellCheckingInspection
def active_host_stats_providers() -> set[str]:
    """Parse the ``host_stats_source`` setting → set of active providers.

    Single source of truth for "which host-stats providers should
    OmniGrid probe right now". Returns a set drawn from the same
    ``valid`` allow-list enforced server-side by ``api_set_settings``'s
    ``host_stats_source`` validator — currently the 6 telemetry providers
    (``beszel``, ``node_exporter``, ``pulse``, ``webmin``, ``ping``,
    ``snmp``) plus 2 probe-result providers (``http_probe``,
    ``service_probe``). Empty set means "host-stats globally off".
    Legacy installs that only ever set the ``node_exporter_enabled``
    flag (pre-CSV settings) auto-fall-back to ``{"node_exporter"}``.

    Replaces 6 duplicate copies of the same parse block scattered
    across main.py + the gather/host_*_sampler modules. New providers
    only need to update this helper plus the validation list in
    ``api_set_settings`` — see ``main_pkg/settings_routes.py``'s
    ``host_stats_source`` block for the canonical allow-list.
    """
    raw = (get_setting(Settings.HOST_STATS_SOURCE) or "").strip()
    if not raw:
        if (get_setting(Settings.NODE_EXPORTER_ENABLED, "false") or "false").lower() == "true":
            return {"node_exporter"}
        return set()
    out: set[str] = set()
    for token in raw.split(","):
        t = token.strip().lower()
        if t and t != "none":
            out.add(t)
    return out


from typing import Iterator

# cache the PARSED hosts_config alongside the cached settings string.
# get_setting(HOSTS_CONFIG) is now a cheap dict lookup, but every
# curated-host helper still ran json.loads() on the full (tens-of-KB on a large
# fleet) blob, and the gather + 6 samplers call one or more of them ~a dozen
# times per tick cycle. This memoizes the json.loads result keyed on the raw
# string: get_setting returns the SAME cached string object within its TTL (and
# a settings write changes the string + bumps the version), so the `==` check
# short-circuits on identity in the common case and the parse only re-runs when
# the blob actually changes. Single-process single-replica -> a plain module
# cache is correct (same justification as the settings cache). The cached list
# (and its row dicts) is SHARED across callers and READ-ONLY by contract —
# iter_curated_hosts yields rows for reading/filtering, never mutation (every
# curated_*_hosts helper builds fresh output dicts; never assigns into a row).
_hosts_config_parsed_raw: Optional[str] = None
_hosts_config_parsed_value: list = []


def _parse_hosts_config(raw: str) -> list:
    """Return the cached parse of the hosts_config JSON list for `raw`.

    Returns ``[]`` for malformed / non-list input. See the module-cache comment
    above; callers MUST treat the result (and its row dicts) as read-only.
    """
    global _hosts_config_parsed_raw, _hosts_config_parsed_value
    if raw == _hosts_config_parsed_raw:
        return _hosts_config_parsed_value
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    _hosts_config_parsed_raw = raw
    _hosts_config_parsed_value = parsed
    return parsed


def iter_curated_hosts(*, require_enabled: bool = True) -> Iterator[dict]:
    """Yield validated ``hosts_config`` rows.

    Canonical generator over the ``hosts_config`` settings row, replacing
    the ~25-line `raw = get_setting → strip → json.loads → isinstance(list)
    → iterate → isinstance(dict) → id-empty-skip → enabled-gate` boilerplate
    that was copy-pasted across 10+ sampler / consumer sites.

    Each yielded row is GUARANTEED to:
      - be a ``dict`` (non-dict rows are skipped silently);
      - have a non-empty ``.get('id').strip()`` value (rows without an
        id are skipped — every downstream consumer needs the id as
        primary key anyway);
      - pass the ``enabled`` gate when ``require_enabled=True`` (the
        default — matches the canonical "ignore disabled rows" contract
        every sampler uses). Pass ``require_enabled=False`` to walk
        EVERY row (e.g. when an admin endpoint needs to surface disabled
        hosts in a count or audit context).

    Per-provider field filtering (``beszel_name`` non-empty, ``ne_url``
    set, ``snmp.enabled=True``, etc.) is the CALLER's responsibility —
    different samplers care about different fields, and a single helper
    that tried to express every per-provider variant would be a worse
    abstraction than the per-sampler one-liner that wraps this generator.

    Returns empty iterator on missing / malformed settings — forgiving
    contract matches the previous per-helper behaviour so a stale
    settings blob can't crash a lifespan task.
    """
    raw = (get_setting(Settings.HOSTS_CONFIG) or "").strip()
    if not raw:
        return
    parsed = _parse_hosts_config(raw)
    for row in parsed:
        if not isinstance(row, dict):
            continue
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        if require_enabled and not row.get("enabled", True):
            continue
        yield row


# noinspection DuplicatedCode
def _walk_hosts_config() -> list[dict]:
    """Walk the ``hosts_config`` JSON settings row → list of enabled
    host dicts.

    Back-compat wrapper around :func:`iter_curated_hosts` (kept because
    the four ``curated_*_hosts`` helpers below still consume a list).
    Net behaviour change vs the pre-helper shape: rows without a
    non-empty ``id`` are now filtered out here too (every downstream
    consumer rejected them anyway, so the gate just moved upstream).

    Returns empty list on missing / malformed settings — forgiving
    contract matches what the helpers had before, so a stale settings
    blob can't crash the lifespan tasks.
    """
    return list(iter_curated_hosts())


# noinspection DuplicatedCode
def curated_ne_hosts() -> list[dict]:
    """Curated ``hosts_config`` rows that the NE samplers can probe.

    Single source of truth for "which hosts have a usable node-exporter
    URL right now". Walks the JSON ``hosts_config`` setting, returns
    one ``{id, ne_url}`` row per ENABLED entry with a non-empty
    ``ne_url`` and ``id``. Replaces the byte-for-byte duplicate
    ``_load_curated_hosts`` helpers that lived in
    ``logic/host_net_sampler.py`` and ``logic/host_metrics_sampler.py``
   .

    Malformed rows (non-dict, missing id, blank ne_url) are silently
    skipped — same forgiving contract the samplers had before, so a
    stale settings blob can't crash the lifespan task.
    """
    out: list[dict] = []
    # noinspection DuplicatedCode
    for row in _walk_hosts_config():
        ne_url = (row.get("ne_url") or "").strip()
        if not ne_url:
            continue
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        out.append({"id": hid, "ne_url": ne_url})
    return out


# noinspection DuplicatedCode,SpellCheckingInspection
def curated_ping_hosts() -> list[dict]:
    """Curated ``hosts_config`` rows opted-in for ping probing.

    Mirror of :func:`curated_ne_hosts` but gates on ``ping.enabled``
    rather than ``ne_url``. Returns one ``{id, host, port, transport}``
    row per ENABLED entry whose ``ping.enabled`` flag is true. Defaults
    pulled from the per-row ``ping`` sub-dict; resolution of global
    defaults (``ping_default_port`` / ``ping_use_icmp``) lives in the
    sampler so this helper stays I/O-free beyond the one settings read.

    Single-source-of-truth for "which hosts is OmniGrid ping-probing
    right now" — consumed by the sampler + the gather merge path. New
    consumers (a future debug endpoint, a UI count badge) should use
    this rather than re-walking ``hosts_config``.
    """
    out: list[dict] = []
    for row in _walk_hosts_config():
        _ping_raw = row.get("ping")
        ping_cfg: dict = _ping_raw if isinstance(_ping_raw, dict) else {}
        if not ping_cfg.get("enabled"):
            continue
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        _ssh_raw = row.get("ssh")
        ssh_cfg: dict = _ssh_raw if isinstance(_ssh_raw, dict) else {}
        # Resolution chain MUST consult the canonical `address` field
        # FIRST per the address-fallback contract:
        # `address → ssh.fqdn → ssh.host → SKIP`. Pre-fix this skipped
        # `address` entirely, so a host that had `address: web01.example.com`
        # but no ssh sub-dict fell through to the bare `id` (`web01`), which
        # DNS can't resolve on most fleets — the ping sampler then probed
        # the wrong target (typically the local hostname inside the
        # container's own network). Operator-flagged via the boot DNS
        # check warning "N of M curated host targets are unresolved".
        host_target = (
            (row.get("address") or "").strip()
            or (ssh_cfg.get("fqdn") or "").strip()
            or (ssh_cfg.get("host") or "").strip()
            or hid
        )
        port_override = ping_cfg.get("port")
        if port_override in (None, "", 0):
            port = 0
        else:
            try:
                port = int(port_override)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                port = 0
        transport_raw = (ping_cfg.get("transport") or "").strip().lower()
        transport = transport_raw if transport_raw in ("tcp", "icmp") else ""
        out.append({
            "id": hid,
            "host": host_target,
            "port": port,  # 0 = use ping_default_port
            "transport": transport,  # "" = use ping_use_icmp global
        })
    return out


# noinspection DuplicatedCode
def curated_beszel_hosts() -> list[dict]:
    """Curated ``hosts_config`` rows opted-in for Beszel — one row per
    enabled host whose ``beszel_name`` field resolves a target.

    Mirrors :func:`curated_ne_hosts` / :func:`curated_ping_hosts` /
    :func:`curated_snmp_hosts`. Public consumer interface — the
    sampler module also keeps a local `_curated_beszel_hosts()` for
    its own row-shape needs (parallel to how `host_pulse_sampler`
    and `host_webmin_sampler` keep local helpers); both walk the
    same `hosts_config` setting and apply the same gates, so the two
    paths stay in lock-step.

    Returns ``{id, beszel_name}`` per row. Caller resolves global
    Beszel hub credentials so this helper stays I/O-free beyond the
    one settings read.

    Single source of truth for "which hosts is OmniGrid Beszel-
    sampling right now" — consumed by future debug surfaces, count
    badges, and external tooling that wants to enumerate Beszel-
    tracked hosts without round-tripping through the sampler module.
    """
    out: list[dict] = []
    # noinspection DuplicatedCode
    for row in _walk_hosts_config():
        hid = (row.get("id") or "").strip()
        beszel_name = (row.get("beszel_name") or "").strip()
        if not hid or not beszel_name:
            continue
        out.append({"id": hid, "beszel_name": beszel_name})
    return out


# noinspection DuplicatedCode
def curated_snmp_hosts() -> list[dict]:
    """Curated ``hosts_config`` rows opted-in for SNMP probing.

    Mirror of :func:`curated_ne_hosts` / :func:`curated_ping_hosts` but
    gates on ``snmp.enabled === True`` AND a non-empty ``snmp_name`` (or
    a global ``snmp_aliases`` mapping that resolves the host id — caller
    layers the alias lookup on top). Per-host opt-in matches the SPA's
    contract from `enabled is True` is the read-side gate.

    Returns ``{id, snmp_name, ssh}`` per row. The caller resolves global
    SNMP defaults (community / version / port / v3 keys) so this helper
    stays I/O-free beyond the one settings read.

    Single source of truth for "which hosts is OmniGrid SNMP-probing
    right now" — consumed by the per-host probe path and the
    host_metrics_sampler's permanent-fail tracking pass.
    """
    out: list[dict] = []
    for row in _walk_hosts_config():
        _snmp_raw = row.get("snmp")
        snmp_cfg: dict = _snmp_raw if isinstance(_snmp_raw, dict) else {}
        if not snmp_cfg.get("enabled"):
            continue
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        snmp_name = (row.get("snmp_name") or "").strip()
        # snmp_aliases lookup is the caller's job — this helper stays
        # I/O-free beyond the one settings read. The shared `address`
        # field rides along so the SNMP probe path can fall through
        # to it via the canonical chain aliases → snmp_name → address
        # → SKIP. Address-only SNMP hosts (snmp.enabled=true with
        # snmp_name blank, address populated) must reach the probe
        # path or the sampler returns early and host_snmp_samples
        # never writes.
        out.append({
            "id": hid,
            "snmp_name": snmp_name,
            "address": (row.get("address") or "").strip(),
            "snmp": dict(snmp_cfg),
        })
    return out


def get_setting_bool(key: str, default: bool = False) -> bool:
    """Read a boolean settings row tolerantly.

    Falls back to ``default`` for unrecognised values so a typo
    ("ture") doesn't pretend to be False. Replaces the per-call site
    `get_setting(...).lower() == "true"` pattern that's case-fragile
    and silently treats any non-"true" string as False.
    """
    raw = get_setting(key)
    if not raw:
        return default
    s = str(raw).strip().lower()
    if s in _TRUTHY_STRINGS:
        return True
    if s in _FALSY_STRINGS:
        return False
    return default


# Memoize the json.loads result keyed on (key, raw-string-identity).
# get_setting() returns the SAME cached string object within its
# 3s read-through TTL, so the `==` check short-circuits on identity
# in the common case and the parse only re-runs when the blob
# actually changes. Mirrors the _parse_hosts_config cache shape one
# level higher — every load_settings_json caller benefits without
# touching their call sites. Single-process single-replica → plain
# module dict is correct (same justification as the settings cache
# itself). The cached value (and its row dicts) is SHARED across
# callers and READ-ONLY by contract — callers MUST NOT mutate the
# returned list / dict; build fresh output instead.
_settings_json_cache: dict[str, tuple[str, Any]] = {}


def load_settings_json(
    key: str,
    default: Any = None,
    expected_type: Any = (list, dict),
) -> Any:
    """Read a JSON-serialised settings row tolerantly.

    Replaces the recurring four-step idiom across every operator-
    facing JSON setting:

        raw = (get_setting(KEY) or "").strip()
        if not raw: return <empty>
        try: parsed = json.loads(raw)
        except (ValueError, TypeError): return <empty>
        if not isinstance(parsed, <shape>): return <empty>
        return parsed

    Args:
        key: The settings table key to read.
        default: Returned on missing / empty / corrupt / wrong-type row.
            Caller picks the appropriate empty shape (``[]`` for list-
            valued settings, ``{}`` for dict-valued, ``None`` for
            optional).
        expected_type: One or more concrete types the parsed JSON's
            top-level value MUST be an instance of. Default accepts
            either ``list`` OR ``dict`` (the two top-level JSON
            containers). Pass a single type (``list``) to require a
            specific shape.

    Returns the parsed JSON value when valid, else ``default``. NEVER
    raises — callers can rely on the return type matching either
    ``default`` or ``expected_type``.
    """
    raw = (get_setting(key) or "").strip()
    if not raw:
        return default
    # Short-circuit on cached raw-string identity. get_setting returns
    # the SAME string object within its TTL window, so on a hot path
    # (samplers / gather reading aliases per-host) the cache hit skips
    # json.loads entirely.
    cached = _settings_json_cache.get(key)
    if cached is not None and cached[0] is raw:
        candidate = cached[1]
        if isinstance(candidate, expected_type):
            return candidate
        return default
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return default
    _settings_json_cache[key] = (raw, value)
    if not isinstance(value, expected_type):
        return default
    return value
