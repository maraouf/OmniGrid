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
     ``tuning_log_retention_days``.

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
from typing import Any, Iterable, Optional, TextIO


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


def _resolved_tz():
    """Return the ZoneInfo OmniGrid uses for log-file dates.

    Resolution mirrors `_today_log_path()` exactly so rotation,
    pruning, and filename-date parsing all agree on what "today"
    means (fixes the desync where rotation moved to
    local-tz but the pruner stayed on UTC, producing a
    one-day-late delete window in non-UTC offsets). Returns None
    when neither the DB setting nor a usable local clock are
    available; callers MUST treat None as "fall back to UTC" so
    every consumer reproduces the same fallback ladder.
    """
    try:
        from logic.db import get_setting
        tz_name = (get_setting("scheduler_timezone", "") or "").strip()
        if tz_name:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tz_name)
    except Exception:
        pass
    try:
        # Container-local TZ via the libc-resolved zone (TZ env +
        # /etc/localtime bind mount that docker-compose.yml sets up).
        # `datetime.now().astimezone()` returns the local zone object
        # without needing the IANA name.
        return datetime.now().astimezone().tzinfo
    except Exception:
        return None


def _today_log_path() -> str:
    """Today's log file path. Rotation advances at the operator's
    local midnight — consults the ``scheduler_timezone`` setting (the
    canonical "what day is it for OmniGrid?" knob, same as the
    scheduler's tick anchors). Falls back to the container's local
    clock when the setting is blank, and to UTC as a last resort if
    even that fails (e.g. during very-early-boot before the DB exists).

    Pre-fix this used UTC unconditionally, which was confusing for
    operators in non-zero offsets: an operator in TZ=Africa/Cairo
    (UTC+2) sees writes at local 00:00–01:59 land in the previous
    UTC-day file, even though the local mtime says "today".
    """
    tz = _resolved_tz()
    try:
        today = datetime.now(tz).strftime("%Y-%m-%d") if tz \
            else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
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

    **Structured success prefix wins over body keywords.** When a line
    has the canonical OmniGrid shape `[<tag>] <subject> ok — ...` or
    `[<tag>] ... → ok ...`, the success marker reliably appears in
    the first ~80 chars of the line (right after the tag + subject).
    The classifier checks for that EARLY-position success marker
    before running the ERROR / WARN body scans so user-controlled
    content downstream (an operator's AI query, an SSH command's
    stdout, an Apprise webhook's body) can't poison the bucket
    classification. Without this guard:
    `[ai] palette ok — ... q="how to solve this error..."` was
    classified ERROR because the body contained "error" — even
    though the prefix unambiguously marks the call as successful.
    Real failures say `[ai] palette failed — ...` / `[ssh] run
    ERROR ...` near the start, NOT `ok —`.
    """
    if not text:
        return "INFO"
    # Early-position success-marker scan (first 80 chars).
    head = text[:80]
    if _RE_OK.search(head):
        return "SUCCESS"
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


def recent_lines(*, levels: Optional[Iterable[str]] = None,
                 limit: int = 50) -> list[dict]:
    """Return the most-recent in-memory log lines, newest-last,
    filtered by severity level. ``levels`` is an iterable of
    lowercase level names (`'error'` / `'warn'` / `'info'` /
    `'success'`); ``None`` means every level. ``limit`` caps the
    returned list at the most-recent N matches.

    Used by the AI palette to surface recent error/warn signals
    into the user_prompt so the assistant can honestly answer
    "any errors I should fix?" without claiming it has no log
    access. Each entry is `{ts, level, text}` so the prompt can
    render compact log digests without re-classifying.

    Same severity classifier as `_persist_line` to keep what the
    AI sees consistent with what shows in Admin → Logs.
    """
    if not _buf:
        return []
    levels_set = None
    if levels is not None:
        levels_set = {str(lv).strip().lower() for lv in levels if lv}
        if not levels_set:
            return []
    out: list[dict] = []
    snap = list(_buf)
    for rec in snap:
        text = rec.get("text") or ""
        if not text:
            continue
        lvl = _severity_for(text, rec.get("stream", "stdout")).lower()
        if levels_set is not None and lvl not in levels_set:
            continue
        out.append({
            "ts":    float(rec.get("ts") or 0.0),
            "level": lvl,
            "text":  text,
        })
    if limit and limit > 0 and len(out) > limit:
        out = out[-limit:]
    return out


def recent_lines_window(*, hours: int = 24,
                        levels: Optional[Iterable[str]] = None,
                        limit: int = 200) -> list[dict]:
    """Return log lines from the past ``hours`` of PERSISTENT log
    files (NOT just the in-memory ring buffer), newest-last, filtered
    by severity level. Used by the AI palette so the assistant can
    answer "any issues in the past 24 hours?" honestly instead of
    being capped at the ring-buffer's ~last-N-minutes window.

    ``hours``  — how far back to scan. Default 24h. Anything ≤ 0
                  reads only today's file. The function reads at most
                  ``ceil(hours/24) + 1`` daily files (today's + the
                  N previous days' files) so a 24h window touches
                  today + yesterday.
    ``levels`` — iterable of lowercase level names; ``None`` returns
                  every level. The persistent file's level prefix is
                  trusted (no re-classification) since files are
                  written by `_persist_line` using `_severity_for`.
    ``limit``  — cap on returned matches, newest-last. Defaults to
                  200 — enough for an AI to summarise a noisy day
                  without ballooning the prompt budget. Pass 0 for
                  uncapped (use sparingly — a busy fleet writes
                  thousands of lines per hour).

    Each entry: ``{ts: float, level: str, text: str}`` matching the
    in-memory `recent_lines` shape.

    Format expected (from `_persist_line`):
       ``2026-05-08T12:34:56Z ERROR <message>\\n``
    Lines that don't parse cleanly are skipped (rotation seam corrupt
    bytes, operator-dropped notes, etc.).
    """
    if not os.path.isdir(LOG_DIR):
        return []
    levels_set = None
    if levels is not None:
        levels_set = {str(lv).strip().lower() for lv in levels if lv}
        if not levels_set:
            return []
    # cut-off in epoch seconds. hours <= 0 → only today's file
    # (cut-off = midnight today). hours > 0 → now - hours.
    now = time.time()
    if hours and hours > 0:
        cutoff_ts = now - (hours * 3600)
    else:
        cutoff_ts = 0.0  # read everything in today's file
    # Walk back day-by-day until the file's date predates the cutoff.
    # Files are named `omnigrid-YYYY-MM-DD.log`. Resolved-tz-aware so
    # the day boundary matches `_today_log_path()` (the rotation +
    # parse halves stay consistent per CLAUDE.md's TZ-aware paths
    # rule).
    tz = _resolved_tz() or timezone.utc
    cutoff_date = datetime.fromtimestamp(cutoff_ts, tz=tz).date() if cutoff_ts > 0 else None
    # How many calendar days back to walk. ``hours / 24`` rounded UP +
    # 1 covers the rotation seam where a 24h window straddles
    # midnight (today's file + yesterday's file).
    days_back = max(1, (hours + 23) // 24 + 1) if hours and hours > 0 else 1
    today = datetime.fromtimestamp(now, tz=tz).date()
    out: list[dict] = []
    for delta in range(days_back):
        day = today - timedelta(days=delta)
        # Skip files older than cutoff. The cutoff_date is the
        # earliest day we still care about.
        if cutoff_date is not None and day < cutoff_date:
            break
        name = f"omnigrid-{day.isoformat()}.log"
        path = safe_log_path(name)
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                file_lines = f.readlines()
        except OSError:
            continue
        # Parse newest-last. Format: ``ISO_TS LEVEL <message>\n``.
        # ISO_TS is 20 chars + space; LEVEL is 5 chars + space.
        for raw in file_lines:
            raw = raw.rstrip("\n")
            if len(raw) < 28:  # ts(20) + space + level(5) + space + at-least-1
                continue
            ts_str = raw[:20]
            level_str = raw[21:26].strip().lower()
            text = raw[27:]
            try:
                # parse the trailing-Z UTC timestamp into epoch seconds
                ts_dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                ts_epoch = ts_dt.timestamp()
            except ValueError:
                continue
            if cutoff_ts > 0 and ts_epoch < cutoff_ts:
                continue
            if levels_set is not None and level_str not in levels_set:
                continue
            out.append({"ts": ts_epoch, "level": level_str, "text": text})
    # Sort newest-last across the day-spanning collection (within a
    # single file, lines are already chronological — but spanning
    # files we walked newest-day-first, so we'd otherwise emit in
    # reverse).
    out.sort(key=lambda r: r["ts"])
    if limit and limit > 0 and len(out) > limit:
        out = out[-limit:]
    return out


def list_persistent_logs() -> list[dict]:
    """Return metadata for every persisted daily log file. One entry
    per ``omnigrid-YYYY-MM-DD.log``: ``{name, size, mtime}``. Sorted
    newest-first by filename (which is sort-equivalent to mtime since
    the date is in the name). Used by the Admin → Logs "Files" tab.
    Files we can't parse against the canonical regex are skipped.
    """
    out: list[dict] = []
    if not os.path.isdir(LOG_DIR):
        return out
    try:
        names = os.listdir(LOG_DIR)
    except OSError:
        return out
    for name in names:
        if not _LOG_NAME_RE.match(name):
            continue
        try:
            st = os.stat(os.path.join(LOG_DIR, name))
        except OSError:
            continue
        out.append({
            "name":  name,
            "size":  int(st.st_size),
            "mtime": float(st.st_mtime),
        })
    out.sort(key=lambda r: r["name"], reverse=True)
    return out


def safe_log_path(name: str) -> Optional[str]:
    """Validate ``name`` against the canonical filename regex and
    return its full path. Returns None on any non-match — guards the
    download / view endpoints against path-traversal attempts (``..``,
    absolute paths, symlinks). The regex is the only allowed shape so
    even basename-encoded traversal can't slip through.

    Defence-in-depth: even though `_LOG_NAME_RE` is anchored
    (`^omnigrid-YYYY-MM-DD.log$`) and rejects every separator/
    traversal char, also normalise the joined path via
    ``os.path.realpath`` and confirm the result is contained within
    ``LOG_DIR``. Catches any future regex relaxation (operator-
    customisable suffix, alternate naming, etc.) AND silences static-
    analysis path-injection findings that won't trust regex-shape
    validation alone.
    """
    if not _LOG_NAME_RE.match(name or ""):
        return None
    # Resolve symlinks + collapse `..` segments before the
    # confinement check. `realpath` follows links — important so a
    # symlinked attack file pointing OUT of LOG_DIR fails the prefix
    # guard rather than silently leaking.
    root = os.path.realpath(LOG_DIR)
    candidate = os.path.realpath(os.path.join(root, name))
    # Prefix-with-separator guard prevents a sibling directory whose
    # name starts with the same prefix (e.g. `/var/log/omnigrid_evil`
    # against root `/var/log/omnigrid`) from passing the check.
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    return candidate


def read_persistent_log(name: str, tail_lines: Optional[int] = None) -> Optional[str]:
    """Read the file's contents, optionally just the last ``tail_lines``
    lines. Returns None when the filename is invalid or the file is
    missing. Errors are propagated for the caller to surface — unlike
    the write path, the read path doesn't have a "best effort" mode
    because the operator explicitly asked for this view.
    """
    path = safe_log_path(name)
    if not path or not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        if tail_lines is None or tail_lines <= 0:
            return f.read()
        # Lazy tail — grab everything then slice. The biggest log file
        # we'd ever read is one day's worth at typical OmniGrid log
        # rates (a few MB max), so a full read is fine.
        lines = f.readlines()
    if tail_lines and len(lines) > tail_lines:
        lines = lines[-tail_lines:]
    return "".join(lines)


def prune_old_logs(retention_days: int, *, tz=None) -> int:
    """Delete log files whose date in the filename is older than
    ``retention_days`` from now. Returns the count of files removed.
    Called from the lifespan-managed pruner loop in main.py.

    ``tz``: timezone for the cutoff math + filename-date
    parse. ``None`` (default) resolves through `_resolved_tz()` — the
    same chain `_today_log_path` uses, so the rotation, prune, and
    parser halves all agree on what "today" means. Pass an explicit
    `ZoneInfo` to pin the behaviour for tests
    (``prune_old_logs(7, tz=ZoneInfo("UTC"))``) or for callers that
    have already resolved the zone and want to avoid the second
    settings round-trip.

    Files we can't parse (operator-dropped notes, unrelated `.log`
    files) are LEFT ALONE — only the canonical ``omnigrid-YYYY-MM-DD``
    pattern is in scope. Errors per file are swallowed so one
    permission issue doesn't stop the whole sweep.
    """
    if retention_days <= 0:
        return 0
    if not os.path.isdir(LOG_DIR):
        return 0
    # Cutoff + filename-date interpretation must use the SAME zone the
    # rotation half (`_today_log_path`) uses, otherwise non-UTC operators
    # see a one-day-late delete window (). Both halves
    # route through `_resolved_tz()`; None means "fall back to UTC" and
    # both halves reproduce that same fallback.
    if tz is None:
        tz = _resolved_tz() or timezone.utc
    cutoff_ts = time.time() - (retention_days * 86400)
    try:
        cutoff_dt = datetime.fromtimestamp(cutoff_ts, tz=tz).date()
    except Exception:
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
                tzinfo=tz,
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
                # _persist_line() for the failure-mode contract. 
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
