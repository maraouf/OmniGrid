"""SQLite database helpers.

Just the infrastructure: a connection context manager and KV helpers for
the ``settings`` table. Table creation (``init_db()``) stays in main.py
as the boot orchestrator — each logic module that owns tables exposes
its own ``init_schema(conn)`` hook there.

The path is read from ``DB_PATH`` at import time; parent directory is
created on import so callers don't have to. ``DB_PATH`` is REQUIRED —
main.py calls ``load_dotenv`` before importing this module. When the
value is missing we DON'T raise at import time (that would crash-loop
the container and hide the error behind Swarm restart noise) — instead
we expose ``DB_PATH_ERROR`` so main.py can install a config-error
middleware that keeps the app up and shows a diagnostic page to the
operator. Any caller that opens ``db_conn()`` without a configured path
still raises loudly, so silent-default drift is not possible.
"""
import os
import sqlite3
from contextlib import contextmanager
from typing import Optional


DB_PATH: Optional[str] = os.getenv("DB_PATH") or None
DB_PATH_ERROR: Optional[str] = None
if not DB_PATH:
    DB_PATH_ERROR = (
        "DB_PATH is not set. Define it in /app/.env "
        "(e.g. DB_PATH=/app/data/omnigrid.db) and redeploy."
    )
else:
    # Create the parent dir at import (once per process). Safe on restart —
    # exist_ok. "" dirname falls back to "." so relative paths work in dev.
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)


@contextmanager
def db_conn():
    """Context-managed SQLite connection with Row factory.

    Commits on clean exit, closes in finally. Fine for our write volume
    (a few ops per minute); if we ever grow a hot write path we can
    switch to WAL + autocommit, but SQLite's default is enough today.

    Raises ``RuntimeError`` (not ``sqlite3.OperationalError``) if
    ``DB_PATH`` is unset — lets the config-error middleware in main.py
    short-circuit with a readable message instead of surfacing a raw
    SQLite error on every request.
    """
    if not DB_PATH:
        raise RuntimeError(DB_PATH_ERROR or "DB_PATH is not configured")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_setting(key: str, default: str = "") -> str:
    """Read one row from the ``settings`` table, returning `default`
    when the key isn't set.
    """
    with db_conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    """Upsert one row into the ``settings`` table."""
    with db_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
            (key, value),
        )
