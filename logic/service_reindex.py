"""Keep a chip's stored history attached to the chip when its position shifts.

An app chip is identified by ``(host_id, service_idx)`` — its POSITION in the
host's ``services`` list — and about thirty tables key their per-tick samples on
that pair. Deleting a chip renumbers every chip after it, so without this each
of them silently inherits the stored results of the one before it: the Apps
badge counted a healthy Veeam Agent as down because it had moved into the slot
a removed Readarr left, and the stale ``ConnectError`` came with it. Until the
next probe overwrote the newest row, and permanently in the history charts.

``drop_and_shift`` removes the deleted chip's rows and moves every later chip's
rows down by one. The tables are DISCOVERED (any table with both ``host_id`` and
``service_idx``) rather than listed, so a per-app sample table added later is
covered without anyone remembering to add it here.
"""
from __future__ import annotations

import re
import sqlite3

# Table names come out of sqlite_master (this database's own), but they are
# interpolated into SQL, so anything that is not a plain identifier is skipped.
_IDENT = re.compile(r"^\w+$")


def chip_keyed_tables(conn: sqlite3.Connection) -> list[str]:
    """Every table whose rows belong to a chip, found from the schema."""
    out: list[str] = []
    for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
        name = row[0]
        if not _IDENT.match(name):
            continue
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
        if {"host_id", "service_idx"} <= cols:
            out.append(name)
    return sorted(out)


def drop_and_shift(conn: sqlite3.Connection, host_id: str, removed_idx: int) -> dict[str, tuple[int, int]]:
    """Delete ``removed_idx``'s rows on ``host_id`` and renumber the later chips.

    Returns ``{table: (rows deleted, rows moved)}`` for the tables it changed.
    The move goes through negative numbers: the primary keys include
    ``service_idx``, and a plain ``service_idx - 1`` would collide with a
    neighbour that has not moved yet. The caller owns the transaction.
    """
    changed: dict[str, tuple[int, int]] = {}
    for table in chip_keyed_tables(conn):
        deleted = conn.execute(
            f'DELETE FROM "{table}" WHERE host_id = ? AND service_idx = ?',
            (host_id, removed_idx)).rowcount
        conn.execute(
            f'UPDATE "{table}" SET service_idx = -service_idx '
            f"WHERE host_id = ? AND service_idx > ?", (host_id, removed_idx))
        moved = conn.execute(
            f'UPDATE "{table}" SET service_idx = -service_idx - 1 '
            f"WHERE host_id = ? AND service_idx < 0", (host_id,)).rowcount
        if deleted or moved:
            changed[table] = (max(deleted, 0), max(moved, 0))
    return changed
