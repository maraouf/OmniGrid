"""Schema migration infrastructure.

Today's `init_db()` uses two patterns to evolve the schema:

  1. ``CREATE TABLE IF NOT EXISTS ...`` — additive, safe to re-run on
     every boot. Works for new tables and indexes.
  2. ``ALTER TABLE ADD COLUMN ... except OperationalError pass`` —
     additive column adds. Works because SQLite raises a recognisable
     error when the column already exists, and we swallow it.

Both are fine for additive changes. They CAN'T express:

  - Column renames (SQLite needs a copy-table dance).
  - Column type changes (same — copy + rename).
  - Data migrations (e.g. moving secrets from one table to another).
  - Conditional fix-ups (e.g. backfill a default value for rows that
    pre-date a column with a NOT-NULL default).

Adding the version table now is cheap; retro-fitting one AFTER a
non-additive migration is needed (and you've already shipped the
broken DDL) is expensive — that's exactly the case CLAUDE.md's
"forward-looking" note warns against.

Contract:

  - One row per applied migration in ``schema_migrations(version, name,
    applied_at)``. Version is a monotonic integer; gaps are not allowed.
  - Each migration is a Python function ``(conn) -> None`` that runs
    in its own ``BEGIN ... COMMIT`` so a failure rolls back without
    leaving a half-applied schema. Failure halts the boot — corrupt
    schema state is worse than "won't start".
  - Migrations are listed below in ``MIGRATIONS`` as
    ``(version, name, fn)`` tuples in version order.
  - ``init_migrations_schema(conn)`` creates the tracking table.
    ``apply_pending(conn)`` reads ``max(version)`` and runs every
    migration above it.

Adding a new migration:

  1. Append a new tuple to ``MIGRATIONS`` with the next version
     integer, a short descriptive name, and a function that takes
     a connection.
  2. The function does whatever DDL/DML it needs. It does NOT need to
     manage its own transaction — ``apply_pending`` wraps each call.
  3. Make the migration self-contained — don't import from main.py
     (circular). Use raw SQL or import from logic/* modules only.
  4. Once shipped, NEVER rewrite the function — operators in the
     wild may have already applied it. Bug fixes go in a NEW
     migration.

Versions in this file are PERMANENT once shipped. A version applied
on one operator's DB cannot retroactively change.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Callable, List, Tuple

MigrationFn = Callable[[sqlite3.Connection], None]


def init_migrations_schema(conn: sqlite3.Connection) -> None:
    """Create the ``schema_migrations`` tracking table.

    Idempotent — safe to re-run on every boot. Called from main.py's
    ``init_db()`` before ``apply_pending``.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations
        (
            version
            INTEGER
            PRIMARY
            KEY,
            name
            TEXT
            NOT
            NULL,
            applied_at
            REAL
            NOT
            NULL
        );
        """
    )


def _current_version(conn: sqlite3.Connection) -> int:
    """Highest applied migration version, or 0 if the table is empty.

    Reads from ``schema_migrations``. Treats a missing row count as
    "fresh database" — version 0 means no migrations yet applied.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(row[0] if row and row[0] is not None else 0)


def apply_pending(conn: sqlite3.Connection) -> List[Tuple[int, str]]:
    """Apply every migration whose version > current head, in order.

    Each migration runs in its own ``BEGIN ... COMMIT`` so a failure
    halts the boot without leaving a half-applied schema. The matching
    ``schema_migrations`` row is inserted as part of the same
    transaction — either both happen or neither does.

    Returns the list of ``(version, name)`` tuples that were just
    applied this run. Empty list when the database is already at
    head. Logs each application via ``print('[migrations] ...')``.
    Boot continues normally even when no migrations need to apply.

    Raises whatever the migration function raised (after rollback)
    so the operator sees a clear error message instead of a silent
    boot with a broken schema.
    """
    head = _current_version(conn)
    applied: List[Tuple[int, str]] = []
    for version, name, fn in MIGRATIONS:
        if version <= head:
            continue
        # Each migration is its own transaction. The DDL + the
        # tracking row are atomic — either both land or neither.
        try:
            conn.execute("BEGIN")
            fn(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) "
                "VALUES (?, ?, ?)",
                (version, name, time.time()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        applied.append((version, name))
        print(f"[migrations] applied #{version} '{name}'")
    if not applied:
        print(f"[migrations] schema at head (version {head}); nothing to apply")
    return applied


# ----------------------------------------------------------------------
# Migration registry. Append new tuples in version order. NEVER rewrite
# a shipped migration — bug fixes go into a NEW numbered migration.
#
# Format: (version: int, name: str, fn: (sqlite3.Connection) -> None)
#
# Empty for now — the existing additive schema (CREATE TABLE IF NOT
# EXISTS + ALTER TABLE ADD COLUMN with try/except) stays where it is in
# main.py:init_db(). Future non-additive changes (column renames, data
# migrations, etc.) get registered here.
# ----------------------------------------------------------------------
def _migration_001_flip_ssh_per_host_to_opt_in(conn: sqlite3.Connection) -> None:
    """Flip per-host SSH from opt-out (`ssh.disabled=true`) to opt-in
    (`ssh.enabled=true`) so host-level features default to OFF unless
    the operator explicitly enables them. Consistent with `ping.enabled`.

    Pre-flip:
      - `ssh` sub-dict absent OR `ssh.disabled=false` → SSH was
        implicitly enabled (inherited global Admin → SSH master switch).
      - `ssh.disabled=true` → SSH was explicitly disabled for the host.

    Post-flip:
      - `ssh.enabled=true` → SSH is enabled for the host.
      - `ssh.enabled=false` OR `ssh.enabled` absent OR `ssh` sub-dict
        absent → SSH is disabled for the host (default).

    Preserves CURRENT operator intent across the migration:
      - `disabled=true` → `enabled=false` (was off, stays off).
      - `disabled=false` or absent (with other ssh fields) → `enabled=true`
        (was on, stays on).
      - No `ssh` sub-dict at all → write `ssh={"enabled": true}` so the
        host doesn't silently flip off post-migration.

    Drops the legacy `disabled` key from each ssh sub-dict so the data
    settles cleanly on the new shape.
    """
    import json

    row = conn.execute(
        "SELECT value FROM settings WHERE key=?", ("hosts_config",)
    ).fetchone()
    if not row or not row[0]:
        return  # no curated hosts yet — nothing to migrate

    try:
        cfg = json.loads(row[0])
    except (ValueError, TypeError):
        return  # corrupt — leave as-is rather than blow up boot

    if not isinstance(cfg, list):
        return

    for h in cfg:
        if not isinstance(h, dict):
            continue
        ssh = h.get("ssh")
        if not isinstance(ssh, dict):
            # No ssh sub-dict — host was implicitly enabled. Write
            # explicit enabled=true so it stays enabled post-migration.
            h["ssh"] = {"enabled": True}
            continue
        # ssh sub-dict exists. Compute new flag from old; drop legacy key.
        was_disabled = bool(ssh.get("disabled"))
        ssh["enabled"] = not was_disabled
        if "disabled" in ssh:
            del ssh["disabled"]

    conn.execute(
        "UPDATE settings SET value=? WHERE key=?",
        (json.dumps(cfg, separators=(",", ":")), "hosts_config"),
    )


def _migration_002_split_provider_host_pk(conn: sqlite3.Connection) -> None:
    """Split the prefixed ``<provider>:<host_id>`` PK in
    ``host_provider_last_ok`` and ``host_failure_state`` into a clean
    ``(host_id, provider)`` composite PK with provider as a separate
    column.

    Pre-fix the PK column was named ``host_id`` but stored prefixed
    keys like ``snmp:web01`` for per-provider rows (and bare ``web01``
    for whole-host pauses on ``host_failure_state``). Reads relied on
    full-table-scan ``WHERE host_id LIKE '%:hid'`` patterns; on a
    200-host fleet every chip-state probe paid a ~400-row LIKE-scan.

    Post-migration:

    * ``host_provider_last_ok`` — every row is per-provider, so
      ``provider`` is NOT NULL.
    * ``host_failure_state`` — bare-id rows (whole-host
      ``/api/hosts/{id}/pause-sampling`` clicks) become ``provider=''``;
      per-provider rows become ``provider=<name>``. Empty string used
      instead of NULL so the composite PK is always non-NULL (SQLite
      treats NULL columns as distinct in PK comparisons).

    Backfill rule for both tables: parse the legacy string. If it
    contains a colon, split on the FIRST one (right side is the bare
    host_id, left is the provider). Otherwise keep the bare value as
    host_id with empty-string provider.

    Indexes:
    * ``idx_host_provider_last_ok_provider`` on ``provider`` — speeds
      up "every host for one provider" reads (rare but covered).
    * ``idx_host_failure_state_provider`` on ``provider`` — same.

    Existing rows with malformed legacy keys (no host_id portion)
    are dropped on backfill.
    """
    # ----- host_provider_last_ok ---------------------------------------
    conn.execute(
        """
        CREATE TABLE host_provider_last_ok_v2
        (
            host_id    TEXT    NOT NULL,
            provider   TEXT    NOT NULL,
            last_ok_ts INTEGER NOT NULL,
            PRIMARY KEY (host_id, provider)
        )
        """
    )
    rows = conn.execute(
        "SELECT host_id, last_ok_ts FROM host_provider_last_ok"
    ).fetchall()
    for legacy_key, ts in rows:
        if not legacy_key or ":" not in legacy_key:
            # Bare keys in last_ok wouldn't have made sense (the table is
            # explicitly per-provider) — drop.
            continue
        provider, _, host_id = legacy_key.partition(":")
        if not provider or not host_id:
            continue
        try:
            conn.execute(
                "INSERT OR REPLACE INTO host_provider_last_ok_v2 "
                "(host_id, provider, last_ok_ts) VALUES (?, ?, ?)",
                (host_id, provider, int(ts or 0)),
            )
        except sqlite3.Error:
            pass
    conn.execute("DROP TABLE host_provider_last_ok")
    conn.execute("ALTER TABLE host_provider_last_ok_v2 RENAME TO host_provider_last_ok")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_host_provider_last_ok_provider "
        "ON host_provider_last_ok(provider)"
    )

    # ----- host_failure_state ------------------------------------------
    conn.execute(
        """
        CREATE TABLE host_failure_state_v2
        (
            host_id              TEXT    NOT NULL,
            provider             TEXT    NOT NULL DEFAULT '',
            first_failure_ts     REAL    NOT NULL,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            paused               INTEGER NOT NULL DEFAULT 0,
            paused_at            REAL,
            last_error           TEXT,
            last_failure_ts      REAL,
            PRIMARY KEY (host_id, provider)
        )
        """
    )
    # Read every column the old table might carry — additive ALTERs
    # over the table's life mean some installs have last_failure_ts +
    # paused_at, others don't. Use a defensive SELECT * walk.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(host_failure_state)").fetchall()]
    has_last_failure_ts = "last_failure_ts" in cols
    has_paused_at = "paused_at" in cols
    select_cols = (
        "host_id, first_failure_ts, consecutive_failures, paused, last_error, "
        + ("last_failure_ts" if has_last_failure_ts else "NULL")
        + ", "
        + ("paused_at" if has_paused_at else "NULL")
    )
    fail_rows = conn.execute(f"SELECT {select_cols} FROM host_failure_state").fetchall()
    for legacy_key, first_ts, cf, paused, err, lf_ts, paused_at in fail_rows:
        if not legacy_key:
            continue
        if ":" in legacy_key:
            provider, _, host_id = legacy_key.partition(":")
            if not host_id:
                continue
        else:
            # Bare key — whole-host pause. Empty-string provider sentinel.
            host_id = legacy_key
            provider = ""
        try:
            conn.execute(
                "INSERT OR REPLACE INTO host_failure_state_v2 "
                "(host_id, provider, first_failure_ts, consecutive_failures, "
                " paused, last_error, last_failure_ts, paused_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (host_id, provider, float(first_ts or 0.0), int(cf or 0),
                 int(paused or 0), err, lf_ts, paused_at),
            )
        except sqlite3.Error:
            pass
    conn.execute("DROP TABLE host_failure_state")
    conn.execute("ALTER TABLE host_failure_state_v2 RENAME TO host_failure_state")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_host_failure_state_provider "
        "ON host_failure_state(provider)"
    )


def _migration_003_history_target_kind(conn: sqlite3.Connection) -> None:
    """Add ``target_kind`` to ``history`` and backfill existing rows by
    inferring from ``op_type``.

    Pre-fix only ``notifications`` carried a ``target_kind`` column;
    ``history`` rows had ``op_type`` (the action) but no kind taxonomy
    for future filters (Admin → History bucketing by schedule vs op vs
    ssh, etc.). This migration adds the column, backfills every
    existing row using a best-effort op_type → kind map, and adds the
    index that ``init_db()`` already declares for fresh deploys.

    Backfill rules (op_type → target_kind):
      * ``update_stack`` / ``update_container`` / ``restart_service`` /
        ``restart_container`` / ``remove_container`` → ``op``
      * ``ssh_run`` → ``ssh``
      * Schedule-fired kinds (``gather_refresh`` / ``prune_node`` /
        ``prune_all_nodes`` / ``backup`` / ``asset_inventory_refresh``
        / ``prune_logs`` / ``prune_notifications`` /
        ``swarm_agent_health``) → ``schedule``
      * ``hosts_bulk_*`` (future bulk-action audit rows) → ``hosts``
      * Anything else → ``system`` (catch-all so the Admin → History
        filter never drops a legacy row).

    Idempotent — uses ``ALTER TABLE ADD COLUMN`` inside a try/except
    so the second migration run is a no-op. The backfill UPDATE only
    touches rows where ``target_kind IS NULL`` so re-running is safe.
    """
    try:
        conn.execute("ALTER TABLE history ADD COLUMN target_kind TEXT")
    except sqlite3.OperationalError:
        # Column already exists (rare — only if the operator ran a
        # half-broken pre-release with the schema add already in
        # init_db). Skip the ADD; the backfill below still runs.
        pass

    # Backfill in one UPDATE per kind. Cheaper than per-row Python
    # because SQLite's CASE expression handles every match in a single
    # table scan.
    conn.execute(
        """
        UPDATE history
        SET target_kind = CASE
                              WHEN op_type IN (
                                               'update_stack', 'update_container',
                                               'restart_service', 'restart_container',
                                               'remove_container'
                                  ) THEN 'op'
                              WHEN op_type = 'ssh_run' THEN 'ssh'
                              WHEN op_type IN (
                                               'gather_refresh', 'prune_node', 'prune_all_nodes',
                                               'backup', 'asset_inventory_refresh', 'prune_logs',
                                               'prune_notifications', 'swarm_agent_health'
                                  ) THEN 'schedule'
                              WHEN op_type LIKE 'hosts_bulk_%' THEN 'hosts'
                              ELSE 'system'
            END
        WHERE target_kind IS NULL
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_target_kind "
        "ON history(target_kind)"
    )


def _migration_004_port_scan_protocol_column(conn: sqlite3.Connection) -> None:
    """Add ``protocol`` column to ``host_port_scans`` so Stage 2 UDP
    scans can land in the same table as Stage 1 TCP scans.

    Stage 1 only persisted TCP probe results; Stage 2 introduces UDP
    via `logic/port_scanner_udp.py`. Both protocol families share
    the same logical schema (port + service hint + banner excerpt
    per detected open port) — the only delta is the protocol
    family. Rather than fork into a parallel `host_port_scans_udp`
    table (which would split the History detail viewer + the merge
    layer's "latest open ports for this host" lookup), we add a
    single nullable ``protocol`` column. Legacy rows (NULL) are
    treated as 'tcp' downstream for backwards-compatibility; new
    rows explicitly stamp 'tcp' or 'udp'.

    Idempotent — ALTER inside try/except so a re-run is a no-op.
    """
    try:
        conn.execute(
            "ALTER TABLE host_port_scans ADD COLUMN protocol TEXT"
        )
    except sqlite3.OperationalError:
        # Column already exists.
        pass
    # Backfill legacy NULL rows with 'tcp' so downstream readers can
    # group cleanly without coalescing in every query.
    conn.execute(
        "UPDATE host_port_scans SET protocol = 'tcp' "
        "WHERE protocol IS NULL"
    )


def _migration_005_service_samples_port_column(conn: sqlite3.Connection) -> None:
    """Add ``port`` column to ``service_samples`` with port-aware PK so
    per-port + rollup samples can coexist for multi-port chips.

    Pre-fix the table's PK was ``(ts, host_id, service_idx)`` — one
    row per chip per tick. Multi-port chips lost per-port history;
    the sampler rolled up "any port up = chip alive" and only the
    chip-level result hit the table.

    Post-migration the PK is ``(ts, host_id, service_idx, port)``
    where ``port=0`` is the rollup sentinel (port 0 is reserved per
    RFC, not a valid TCP/UDP port) and per-port rows carry the actual
    port number. Single-port chips continue to emit only the rollup
    row; multi-port chips emit one rollup row PLUS one row per port.

    SQLite treats NULL as distinct in PK comparisons, which would
    break ``INSERT OR REPLACE`` idempotency on rollup rows. The
    sentinel-0 approach keeps the upsert pattern working without
    NULL coercion at every read site.

    Idempotent: detects the new schema via PRAGMA table_info and
    skips when the ``port`` column is already present.
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(service_samples)").fetchall()]
    if "port" in cols:
        # New schema already in place (fresh install via init_db's
        # updated CREATE TABLE, OR a previous run of this migration).
        return
    # Rebuild: CREATE NEW → COPY → DROP OLD → RENAME.
    conn.execute(
        """
        CREATE TABLE service_samples_v2
        (
            ts          INTEGER NOT NULL,
            host_id     TEXT    NOT NULL,
            service_idx INTEGER NOT NULL,
            port        INTEGER NOT NULL DEFAULT 0,
            alive       INTEGER NOT NULL,
            rtt_ms      INTEGER,
            error       TEXT,
            PRIMARY KEY (ts, host_id, service_idx, port)
        )
        """
    )
    # Backfill every legacy row as a rollup row (port=0). Pre-migration
    # there was no per-port concept so EVERY existing row is logically a
    # rollup; the sentinel-0 stamp preserves that meaning.
    conn.execute(
        "INSERT INTO service_samples_v2 "
        "(ts, host_id, service_idx, port, alive, rtt_ms, error) "
        "SELECT ts, host_id, service_idx, 0, alive, rtt_ms, error "
        "FROM service_samples"
    )
    conn.execute("DROP TABLE service_samples")
    conn.execute("ALTER TABLE service_samples_v2 RENAME TO service_samples")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_service_samples_host_ts "
        "ON service_samples(host_id, ts DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_service_samples_host_idx_ts "
        "ON service_samples(host_id, service_idx, ts DESC)"
    )


MIGRATIONS: List[Tuple[int, str, MigrationFn]] = [
    (1, "flip_ssh_per_host_to_opt_in", _migration_001_flip_ssh_per_host_to_opt_in),
    (2, "split_provider_host_pk", _migration_002_split_provider_host_pk),
    (3, "history_target_kind", _migration_003_history_target_kind),
    (4, "port_scan_protocol_column", _migration_004_port_scan_protocol_column),
    (5, "service_samples_port_column", _migration_005_service_samples_port_column),
]
