"""In-process stdout/stderr ring buffer + persistent daily files.

Design: OmniGrid's codebase uses plain ``print()`` for diagnostics plus
uvicorn's access/error logs (which also go to stdout/stderr). Rather
than force a switch to the stdlib ``logging`` module everywhere, this
module tees the two standard streams into:
  1. A bounded in-memory ``deque`` — backs the Admin → Logs tab, fast
     polling, wiped on container restart.
  2. A daily file under ``/app/data/logs/omnigrid-YYYY-MM-DD.log`` —
     backs persistent retention across restarts. The ``/app/data``
     bind mount in compose puts the file under
     ``/opt/omnigrid/data/logs/`` on the host. Retention is enforced
     by ``prune_old_logs(days)`` called from a lifespan-managed task
     in ``main.py``, with the ``days`` value tunable via
     ``tuning_log_retention_days`` (#424).

Contract:
  - ``install()`` is idempotent. Safe to call multiple times (the first
    wins; subsequent calls return early). Always called from the main
    module at import time so uvicorn's own startup noise is captured.
  - Pass-through is preserved: lines still appear in Docker logs /
    stdout as before. We tee, we don't swallow.
  - Buffer is capped at ``MAX_LINES`` (2000). Oldest lines drop when full.
  - File writes are best-effort: a disk-full or permission failure
    silently drops the persistent copy but never breaks the tee. The
    in-memory ring is the source of truth for the live UI.
  - Only the main process buffer matters — the codebase runs as a
    single uvicorn worker (see CLAUDE.md "single-replica" invariant).
"""
from __future__ import annotations

import os
import re
import sys
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterable, TextIO


# How many lines to retain in-memory. 2000 at ~150 bytes/line is ~300 KB —
# negligible for a long-running single-replica process. Bump if operators
# ask for more than what's visible; shrink if memory becomes a concern.
MAX_LINES = 2000

# Module-level ring. Each entry is {ts, stream, text}. Deques are
# append-thread-safe in CPython; readers take a snapshot with list().
_buf: deque[dict[str, Any]] = deque(maxlen=MAX_LINES)
_installed = False


# Persistent log directory. Override with LOG_DIR env if your deploy
# doesn't bind ``/app/data``. The ``data/logs`` subdir is created on
# first write so a fresh deploy doesn't need any pre-provisioning.
LOG_DIR = os.environ.get("LOG_DIR", "/app/data/logs")

# Log filename matches ``omnigrid-YYYY-MM-DD.log`` so the date is
# parseable by the prune sweeper without hitting filesystem mtime.
_LOG_NAME_RE = re.compile(r"^omnigrid-(\d{4})-(\d{2})-(\d{2})\.log$")
_LOG_FAILED_ONCE = False  # latch so a sustained write failure doesn't spam stderr


def _today_log_path() -> str:
    """Today's log file path. Daily UTC rotation — the file name
    advances at 00:00 UTC regardless of container TZ, so a deploy that
    crosses midnight produces two files cleanly.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"omnigrid-{today}.log")


# Severity classifier — content-based, mirrors the SPA's `logSeverity()`
# helper in static/js/app.js so the file format and the live UI agree
# on which lines are which level. Stream alone is too coarse (backend
# error prints go to stdout too via plain print()), so we scan the body
# for tell-tale tokens. Falls back to INFO.
_RE_ERROR = re.compile(r"\berror\b|\bfail(?:ed|ure)?\b|\btraceback\b|\bcritical\b|\bfatal\b", re.IGNORECASE)
_RE_WARN  = re.compile(r"\bwarn(?:ing)?\b|deprecat",                                          re.IGNORECASE)
_RE_OK    = re.compile(r"\bsuccess\b|\bok —|→ ok\b",                                          re.IGNORECASE)


def _severity_for(text: str, stream: str) -> str:
    """Classify a log line into INFO / WARN / ERROR / SUCCESS. Content
    wins over stream so stderr lines without negative keywords stay at
    INFO (uvicorn's startup banners + our own [tag] info prints all go
    to stderr).
    """
    if not text:
        return "INFO"
    if _RE_ERROR.search(text):
        return "ERROR"
    if _RE_WARN.search(text):
        return "WARN"
    if _RE_OK.search(text):
        return "SUCCESS"
    return "INFO"


def _persist_line(record: dict[str, Any]) -> None:
    """Append one line to today's file in a standard log format:

        <ISO 8601 UTC timestamp> <LEVEL> <message>

    Example:
        2026-04-27T12:34:56Z INFO  [beszel] probe sample RAW=...

    Best-effort: any I/O error gets swallowed (and noisily printed to
    the underlying stderr ONCE via the latch) so the in-memory tee keeps
    working even when the disk is unwritable. ISO 8601 + uppercase
    fixed-width level keeps the file grep-friendly and parseable by
    standard log aggregators (Promtail, Vector, Fluent Bit, etc.).
    """
    global _LOG_FAILED_ONCE
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        # ISO 8601 with seconds precision + Z suffix for explicit UTC.
        # Matches the format Python's stdlib logging uses with
        # ``datefmt="%Y-%m-%dT%H:%M:%SZ"`` and what most log viewers
        # parse out of the box.
        ts = datetime.fromtimestamp(record["ts"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        level = _severity_for(record["text"], record.get("stream", "stdout"))
        # 5-char fixed-width level so columns align in a terminal /
        # tail viewer regardless of content length.
        line = f"{ts} {level:<5} {record['text']}\n"
        with open(_today_log_path(), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        if not _LOG_FAILED_ONCE:
            _LOG_FAILED_ONCE = True
            try:
                # Direct write to the original stderr — bypass our own
                # tee since that's the layer that's failing.
                sys.__stderr__.write(f"[logs] persistent-log write failed (suppressed): {e}\n")
            except Exception:
                pass


def prune_old_logs(retention_days: int) -> int:
    """Delete log files whose date in the filename is older than
    ``retention_days`` from now. Returns the count of files removed.
    Called from the lifespan-managed pruner loop in main.py.

    Files we can't parse (operator-dropped notes, unrelated `.log`
    files) are LEFT ALONE — only the canonical ``omnigrid-YYYY-MM-DD``
    pattern is in scope. Errors per file are swallowed so one
    permission issue doesn't stop the whole sweep.
    """
    if retention_days <= 0:
        return 0
    if not os.path.isdir(LOG_DIR):
        return 0
    cutoff_ts = time.time() - (retention_days * 86400)
    cutoff_dt = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).date()
    removed = 0
    try:
        names = os.listdir(LOG_DIR)
    except OSError:
        return 0
    for name in names:
        m = _LOG_NAME_RE.match(name)
        if not m:
            continue
        try:
            file_date = datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                tzinfo=timezone.utc,
            ).date()
        except ValueError:
            continue
        if file_date < cutoff_dt:
            try:
                os.remove(os.path.join(LOG_DIR, name))
                removed += 1
            except OSError:
                continue
    return removed


class _TeeStream:
    """Wraps a real stream (stdout or stderr). Every write is forwarded
    to the underlying stream and appended to the ring buffer, split on
    newlines so multi-line writes produce one entry per line.

    Partial-line writes (no trailing newline) are buffered locally and
    flushed when the next write brings a newline. This matches what
    docker logs / terminals display.
    """

    def __init__(self, stream: TextIO, label: str):
        self._stream = stream
        self._label = label
        self._partial = ""

    def write(self, data: str) -> int:
        # Forward to the real stream FIRST so any exception on our side
        # can't suppress real diagnostics. The ring buffer is an
        # auxiliary view, not the source of truth.
        n = self._stream.write(data) if data else 0
        try:
            if not data:
                return n
            buf = self._partial + data
            lines = buf.split("\n")
            # All but the last are complete lines; the last is carry-over
            # (either the next partial or the empty string after a
            # trailing newline).
            self._partial = lines[-1]
            now = time.time()
            for line in lines[:-1]:
                rec = {"ts": now, "stream": self._label, "text": line}
                _buf.append(rec)
                # Persist to today's daily file. Best-effort — see
                # _persist_line() for the failure-mode contract. (#424)
                _persist_line(rec)
        except Exception:
            # Never let the tee break real logging. Swallow and move on.
            pass
        return n

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass

    # Passthrough misc attributes (isatty, fileno, encoding, etc.) so
    # code that inspects the stream sees the real one.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def install() -> None:
    """Tee sys.stdout / sys.stderr into the ring buffer. Idempotent."""
    global _installed
    if _installed:
        return
    # Guard against re-entrant install that wraps a wrapper: if we've
    # been installed and then python reloaded the module, don't double-wrap.
    if isinstance(sys.stdout, _TeeStream) or isinstance(sys.stderr, _TeeStream):
        _installed = True
        return
    sys.stdout = _TeeStream(sys.stdout, "stdout")
    sys.stderr = _TeeStream(sys.stderr, "stderr")
    _installed = True


def get_recent(limit: int = 500, since_ts: float = 0.0) -> list[dict]:
    """Return up to ``limit`` most recent lines, optionally since ``ts``.

    Ordered oldest-first so the UI can append-only render. Filtering by
    ``since_ts`` lets the frontend poll cheaply (send the last-seen ts
    and only get new lines back).
    """
    snapshot = list(_buf)  # atomic enough for our purposes
    if since_ts > 0:
        snapshot = [r for r in snapshot if r["ts"] > since_ts]
    if limit and len(snapshot) > limit:
        snapshot = snapshot[-limit:]
    return snapshot


def clear() -> None:
    """Drop everything in the ring. Used only by the "Clear logs" button."""
    _buf.clear()


def size() -> int:
    return len(_buf)
