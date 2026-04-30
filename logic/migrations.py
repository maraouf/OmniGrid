"""Schema migration infrastructure (ARCH-002).

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
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at REAL NOT NULL
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


MIGRATIONS: List[Tuple[int, str, MigrationFn]] = [
    (1, "flip_ssh_per_host_to_opt_in", _migration_001_flip_ssh_per_host_to_opt_in),
]
