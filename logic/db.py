"""SQLite database helpers.

Just the infrastructure: a connection context manager and KV helpers for
the ``settings`` table. Table creation (``init_db()``) stays in main.py
as the boot orchestrator — each logic module that owns tables exposes
its own ``init_schema(conn)`` hook there.

The path is read from ``DB_PATH`` at import time; parent directory is
created on import so callers don't have to. ``DB_PATH`` is REQUIRED —
main.py calls ``load_dotenv`` before importing this module, so a missing
value means the operator's ``.env`` is broken. Fail loud at boot rather
than silently fall back to a default path that drifts from the bind
mount.
"""
import os
import sqlite3
from contextlib import contextmanager


_db_path_env = os.getenv("DB_PATH")
if not _db_path_env:
    raise RuntimeError(
        "DB_PATH is not set. Define it in /app/.env (e.g. "
        "DB_PATH=/app/data/omnigrid.db) — main.py loads that file "
        "before importing logic.db."
    )
DB_PATH: str = _db_path_env

# Create the parent dir at import (once per process). Safe on restart —
# exist_ok. "" dirname falls back to "." so relative paths work in dev.
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)


@contextmanager
def db_conn():
    """Context-managed SQLite connection with Row factory.

    Commits on clean exit, closes in finally. Fine for our write volume
    (a few ops per minute); if we ever grow a hot write path we can
    switch to WAL + autocommit, but SQLite's default is enough today.
    """
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
