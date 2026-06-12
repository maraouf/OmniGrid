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
    single uvicorn worker (see the project conventions "single-replica" invariant).
"""
from __future__ import annotations

import os
import re
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, TextIO
from logic.env_keys import EnvKey, env_get
from logic.settings_keys import Settings

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
LOG_DIR = env_get(EnvKey.LOG_DIR, "/app/data/logs")

# Log filename matches ``omnigrid-YYYY-MM-DD.log`` so the date is
# parseable by the prune sweeper without hitting filesystem mtime.
_LOG_NAME_RE = re.compile(r"^omnigrid-(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\.log$")
_LOG_FAILED_ONCE = False  # latch so a sustained write failure doesn't spam stderr

# Kept-open log file handle — avoids the per-line `open()/close()`
# overhead that pushed `/api/healthz` past the 10s healthcheck
# timeout under bursty per-tick logging (50+ ping samples → 50+
# sync file opens in a single event-loop iteration). The handle
# is opened lazily on the first write + rotated when the date
# rolls over (next-day's filename). Pair of (path, handle) so we
# can detect a date-rollover by comparing paths cheaply.
_LOG_FH_PATH: Optional[str] = None
# Explicit Optional[IO] annotation — without it Pyright infers the
# literal `None` type from the initial assignment and flags every
# subsequent `.write(...)` / `.close()` call as "Unresolved attribute
# reference on class 'None'". `_persist_line` reassigns this to a
# real TextIO via `open(...)`; the narrowing pattern there relies on
# the Optional being the declared type.
_LOG_FH: Optional[TextIO] = None  # actual file handle


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
        tz_name = (get_setting(Settings.SCHEDULER_TIMEZONE) or "").strip()
        if tz_name:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tz_name)
    except (ImportError, ValueError, OSError):
        pass
    try:
        # Container-local TZ via the libc-resolved zone (TZ env +
        # /etc/localtime bind mount that docker-compose.yml sets up).
        # `datetime.now().astimezone()` returns the local zone object
        # without needing the IANA name.
        return datetime.now().astimezone().tzinfo
    except (ValueError, OSError):
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
    except (ValueError, OSError):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"omnigrid-{today}.log")


# Severity classifier — content-based, mirrors the SPA's `logSeverity()`
# helper in static/js/app.js so the file format and the live UI agree
# on which lines are which level. Stream alone is too coarse (backend
# error prints go to stdout too via plain print()), so we scan the body
# for tell-tale tokens. Falls back to INFO.
# ERROR-classifier regex. Matches:
#   - bare keywords ("error" / "failed" / "traceback" / "critical" / "fatal")
#   - PascalCase exception names ending in `Error` or `Exception`
#     (NameError, ValueError, TypeError, RuntimeError, HTTPException, ...)
#     so the actual exception line of a traceback lands as ERROR instead
#     of INFO (the bare `error` word-boundary missed `NameError` because
#     there's no word boundary before the second `e`).
#   - traceback frame lines starting with `  File "..."` so every frame
#     of a multi-line traceback carries the same severity as the header.
#     Also matches the Python 3.11+ ExceptionGroup-wrapped variant
#     `  | File "..."` (the leading pipe + space is the sub-traceback
#     continuation marker). And the bare `  | ` / `  + ` / `  | NameError`
#     continuation lines so the entire ExceptionGroup body lands as
#     ERROR uniformly. Without this, the operator's ERROR-filtered
#     log viewer showed only the `Traceback (most recent call last):`
#     header + the bare exception body, with the per-frame `File "..."`
#     lines buried under INFO.
_RE_ERROR = re.compile(
    r"\berror\b|\bfail(?:ed|ure)?\b|\btraceback\b|\bcritical\b|\bfatal\b"
    r"|\w(?:Error|Exception)\b"
    # Traceback frame lines + ExceptionGroup continuation markers — match
    # case-insensitively because the surrounding flag is IGNORECASE
    # anyway; the previous `[A-Z]` constraint on the Error/Exception
    # alternation was moot under the flag (matched `someerror`,
    # `whatevererror`), so the simpler `\w` is honest about intent.
    r"|^\s+(?:[|+]\s+)?File \"[^\"]+\", line \d+"
    r"|^\s+[|+][-+\s]*\d*[-+\s]*$"
    r"|^\s+\|\s+\S",
    re.IGNORECASE | re.MULTILINE,
)
_RE_WARN = re.compile(r"\bwarn(?:ing)?\b|deprecat", re.IGNORECASE)
_RE_OK = re.compile(r"\bsuccess\b|\bok —|→ ok\b", re.IGNORECASE)

# Uvicorn access-log status-code extractor — matches the canonical
# `<host:port> - "<method> <path> <proto>" <code> <reason>` shape
# emitted by uvicorn's default access logger. The capture group pulls
# the 3-digit status code so _severity_for can promote 5xx → ERROR
# and 4xx → WARN. Without this, every 504 / 502 / 500 was bucketed
# INFO purely because uvicorn doesn't distinguish access-log status
# in its own log-level choice.
_RE_HTTP_ACCESS = re.compile(
    r'"\s*[A-Z]+\s+[^"]+\s+HTTP/[0-9.]+"\s+(?P<status>\d{3})\b',
)
# Lines that ALREADY carry a level prefix — uvicorn's
# `INFO:     <body>` / `WARNING:    <body>` / `ERROR:     <body>` /
# `DEBUG:    <body>` / `CRITICAL:    <body>` shape, OR an ISO 8601
# timestamp followed by a level (our own persistent-log shape that
# could get fed back in if something re-reads + re-writes a log
# file). `_TeeStream._prefixed()` uses this to skip already-classified
# lines so we never end up with `INFO:    INFO:    <body>` double-
# prefixes.
_RE_ALREADY_PREFIXED = re.compile(
    r"^(?:INFO|WARN|WARNING|ERROR|DEBUG|CRITICAL|SUCCESS|FATAL|TRACE|NOTICE)\s*:|"
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?\s+(?:INFO|WARN|ERROR|SUCCESS|DEBUG|CRITICAL)\b",
    re.IGNORECASE,
)
# OmniGrid app-log convention: a line whose `[tag]` prefix is IMMEDIATELY
# followed by an explicit level word — `[tdarr] INFO fetched ... failed=5/0`,
# `[seerr] error: ...`, `[snmp] warning: ...` — has self-declared its severity.
# That declared level is AUTHORITATIVE over the body-keyword scan, so a stat
# summary that happens to contain a count label like `failed=5` / `errors=0`
# isn't mis-bucketed as ERROR. Only the level token RIGHT AFTER the tag counts
# (so `[ssh] run ERROR ...` — where "run" follows the tag — still falls through
# to the body scan and is correctly classified ERROR).
_RE_TAG_LEVEL = re.compile(
    r"^\[[a-z0-9_]+]\s+(?P<lvl>INFO|DEBUG|TRACE|WARN|WARNING|ERROR|"
    r"CRITICAL|FATAL|SUCCESS)\b",
    re.IGNORECASE,
)


def _severity_for(text: str, _stream: str) -> str:
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
    # Structured `[tag] LEVEL ...` prefix wins over every body scan — the app
    # explicitly declared this line's severity (see _RE_TAG_LEVEL). Trusting it
    # stops a count label like `failed=5` / `errors=0` in an INFO stat summary
    # from being mis-bucketed ERROR.
    _tag_level = _RE_TAG_LEVEL.match(text)
    if _tag_level is not None:
        _lvl = _tag_level.group("lvl").upper()
        if _lvl in ("ERROR", "CRITICAL", "FATAL"):
            return "ERROR"
        if _lvl in ("WARN", "WARNING"):
            return "WARN"
        if _lvl == "SUCCESS":
            return "SUCCESS"
        return "INFO"  # INFO / DEBUG / TRACE
    # Early-position scan (first 120 chars) — explicit-marker tokens
    # near the start of the line take precedence over body-keyword
    # matches. Without this, a WARN-intent line whose BODY mentions
    # a word like "failed"/"error"/"traceback" (e.g. "[boot] DNS
    # startup check warning: 58 of 172 targets failed to resolve")
    # would be misclassified as ERROR by the broad body-keyword
    # scan below. The "explicit" marker is `warning:` / `warn:` /
    # `success:` / `ok:` in the line's first ~120 chars (typically
    # right after the `[tag]` prefix). Operators control this by
    # word choice; the body-keyword fallback still wins when no
    # explicit marker is present.
    head = text[:120]
    if _RE_OK.search(head):
        return "SUCCESS"
    if _RE_WARN.search(head):
        return "WARN"
    # HTTP access-log lines from uvicorn carry a status code that's
    # the AUTHORITATIVE outcome signal. Default uvicorn classifies
    # every access log as INFO regardless of status — a 504/502/500
    # buried in INFO is invisible to operators scanning Admin → Logs
    # for failures. Detect the canonical uvicorn access format
    # `<host:port> - "<method> <path> <proto>" <code> <reason>` AND
    # promote 4xx/5xx into the appropriate severity bucket. 5xx → ERROR
    # (the backend itself failed / timed out), 4xx → WARN (client /
    # auth / not-found — informational but worth attention).
    _http_match = _RE_HTTP_ACCESS.search(text)
    if _http_match is not None:
        try:
            _code = int(_http_match.group("status"))
        except (TypeError, ValueError):
            _code = 0
        if 500 <= _code < 600:
            return "ERROR"
        if 400 <= _code < 500:
            return "WARN"
    if _RE_ERROR.search(text):
        return "ERROR"
    if _RE_WARN.search(text):
        return "WARN"
    if _RE_OK.search(text):
        return "SUCCESS"
    return "INFO"


# noinspection DuplicatedCode
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
    global _LOG_FAILED_ONCE, _LOG_FH_PATH, _LOG_FH
    try:
        # ISO 8601 with seconds precision + Z suffix for explicit UTC.
        # Matches the format Python's stdlib logging uses with
        # ``datefmt="%Y-%m-%dT%H:%M:%SZ"`` and what most log viewers
        # parse out of the box.
        ts = datetime.fromtimestamp(record["ts"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        level = _severity_for(record["text"], record.get("stream", "stdout"))
        # 5-char fixed-width level so columns align in a terminal /
        # tail viewer regardless of content length.
        line = f"{ts} {level:<5} {record['text']}\n"
        # Reuse the open file handle across writes — was previously
        # `with open(...) as f: f.write(...)` PER LINE which became
        # the event-loop bottleneck under bursty per-tick logging
        # (the ping sampler alone writes 50+ lines per tick across
        # ~30+ hosts; the resulting sync file-open burst starved the
        # event loop enough that `/api/healthz` missed the 10s
        # Docker healthcheck timeout → container restart).
        target = _today_log_path()
        if _LOG_FH is None or _LOG_FH_PATH != target:
            # First write OR the date rolled over → close the old
            # handle (if any) and open the new one. Local-bind so
            # the type checker narrows from `TextIOWrapper | None`
            # to `TextIOWrapper` for the .close() call. Narrow except
            # to the file-IO failure shapes that can realistically
            # surface here.
            _to_close = _LOG_FH
            if _to_close is not None:
                try:
                    _to_close.close()
                except (OSError, ValueError):
                    # OSError: filesystem-level close failure (EBADF,
                    # EIO). ValueError: handle already closed by a
                    # racy GC. Either way we move on + reopen below.
                    pass
                _LOG_FH = None
            os.makedirs(LOG_DIR, exist_ok=True)
            _LOG_FH = open(target, "a", encoding="utf-8", buffering=1)
            _LOG_FH_PATH = target
        _LOG_FH.write(line)
    except Exception as e:
        # Close the kept-open handle on ANY error so the next call
        # retries opening fresh (e.g. transient EBADF / EIO survives
        # one bad write instead of forever-failing). Same local-bind
        # narrowing as the rollover path above; same narrow-except
        # rationale.
        _to_close = _LOG_FH
        if _to_close is not None:
            try:
                _to_close.close()
            except (OSError, ValueError):
                pass
            _LOG_FH = None
            _LOG_FH_PATH = None
        if not _LOG_FAILED_ONCE:
            _LOG_FAILED_ONCE = True
            try:
                # Direct write to the original stderr — bypass our own
                # tee since that's the layer that's failing.
                if sys.__stderr__ is not None:
                    sys.__stderr__.write(f"[logs] persistent-log write failed (suppressed): {e}\n")
            except OSError:
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
            "ts": float(rec.get("ts") or 0.0),
            "level": lvl,
            "text": text,
        })
    if limit is not None and 0 < limit < len(out):
        out = out[-limit:]
    return out


_SECRET_PATTERNS: tuple = (
    # Authorization: Bearer <token> — the canonical OAuth2 / token
    # header shape. Match case-insensitive on the keyword + any
    # subsequent token characters (alphanumeric / `.` / `-` / `_` /
    # `+` / `/` for base64-padded values, plus `=` so JWTs don't
    # leak partials).
    (r"(?i)(bearer\s+)([A-Za-z0-9._\-+/=]{6,})",
     r"\1[REDACTED]"),
    # password=<value>, password: <value>, password = <value>
    (r"(?i)(password\s*[:=]\s*)([^\s,;'\"&]+)",
     r"\1[REDACTED]"),
    # api_key / apikey / token / secret / x-api-key / authorization
    # — the cluster of names that downstream tools use.
    (r"(?i)((?:api[_-]?key|apikey|token|secret|x-api-key|authorization)\s*[:=]\s*)([^\s,;'\"&]+)",
     r"\1[REDACTED]"),
    # AWS-style access keys (AKIA...) and secret keys (40-char base64).
    (r"\b(AKIA[0-9A-Z]{16})\b", r"[REDACTED-AWS-AKID]"),
)


def redact_secrets(text: str) -> str:
    """Replace common secret patterns in ``text`` with [REDACTED].

    Used by callers that ship log text outside the operator's trust
    boundary — primarily the AI palette which sends `recent_logs` to
    a third-party LLM. Admin → Logs and other in-app readers see the
    raw text; only the outbound-shipping path applies redaction.

    Patterns covered: ``Bearer <token>``, ``password=<v>``,
    ``api_key=<v>`` / ``apikey=<v>`` / ``token=<v>`` / ``secret=<v>`` /
    ``x-api-key=<v>``, AWS access-key IDs (AKIA...). Each match
    leaves the keyword in place so the operator can still see WHAT
    was redacted (e.g. ``password=[REDACTED]`` makes "the password
    field WAS in this line" visible without exposing the value).

    The list is intentionally narrow — false-positive redactions in
    log lines that get sent to the AI degrade the AI's ability to
    diagnose real issues. We only redact patterns that are
    unambiguously credential-shaped.
    """
    if not text:
        return text or ""
    import re as _re
    out = text
    for pat, repl in _SECRET_PATTERNS:
        try:
            out = _re.sub(pat, repl, out)
        except (re.error, TypeError):
            continue
    return out


# noinspection DuplicatedCode
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
    # parse halves stay consistent per the project's TZ-aware paths
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
        if not path or not os.path.isfile(path):  # type: ignore[attr-defined]
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
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
    if limit is not None and 0 < limit < len(out):
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
            "name": name,
            "size": int(st.st_size),
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
    ``LOG_DIR`` using ``os.path.commonpath`` (CodeQL's documented
    sanitiser for ``py/path-injection`` — `startswith` works too but
    the static analyser doesn't trace through string-method
    confinement checks reliably; ``commonpath`` is what the rule's
    docs cite).

    Catches any future regex relaxation (operator-customisable
    suffix, alternate naming, etc.).
    """
    if not _LOG_NAME_RE.match(name or ""):
        return None
    # Resolve symlinks + collapse `..` segments before the
    # confinement check. `realpath` follows links — important so a
    # symlinked attack file pointing OUT of LOG_DIR fails the
    # commonpath guard rather than silently leaking.
    root = os.path.realpath(LOG_DIR)
    candidate = os.path.realpath(os.path.join(root, name))
    # `os.path.commonpath([root, candidate])` raises ValueError when
    # the two paths share no common base (e.g. different drives on
    # Windows). We wrap in try/except so that case maps to "reject"
    # rather than propagating the exception. Equal common-path
    # ensures the candidate is contained within `root` — the
    # documented CodeQL sanitiser for `py/path-injection`.
    try:
        if os.path.commonpath([root, candidate]) != root:
            return None
    except ValueError:
        return None
    return candidate


def read_persistent_log(name: str, tail_lines: Optional[int] = None) -> Optional[str]:
    """Read the file's contents, optionally just the last ``tail_lines``
    lines. Returns None when the filename is invalid or the file is
    missing. Errors are propagated for the caller to surface — unlike
    the write path, the read path doesn't have a "best effort" mode
    because the operator explicitly asked for this view.

    Path validation is intentionally INLINED here (rather than
    delegated entirely to `safe_log_path()`) so the static analyser
    sees the sanitiser chain — regex shape check → `os.path.basename`
    canonicalisation → `os.path.realpath` symlink resolution →
    `os.path.commonpath` confinement — directly in the data flow
    between the user-controlled `name` argument and the `open()`
    sink. CodeQL's `py/path-injection` rule doesn't trace taint
    through a delegating helper's return value, so the validator
    has to live on the same call-stack as the file API.
    """
    # Regex shape gate first — anchored `^omnigrid-YYYY-MM-DD.log$`
    # rejects every separator / traversal char up front. Catches the
    # 99% case without touching the filesystem.
    if not name or not _LOG_NAME_RE.match(name):
        return None
    # `os.path.basename` strips any leading path component — defence
    # in depth so a future regex relaxation can't slip through. The
    # equality check confirms the input WAS already a bare filename.
    safe_name = os.path.basename(name)
    if safe_name != name:
        return None
    # Resolve symlinks + collapse `..` segments. Different drives on
    # Windows raise ValueError out of `commonpath`; map both that and
    # the contained-outside-root case to "reject".
    root = os.path.realpath(LOG_DIR)
    candidate = os.path.realpath(os.path.join(root, safe_name))
    try:
        if os.path.commonpath([root, candidate]) != root:
            return None
    except ValueError:
        return None
    if not os.path.isfile(candidate):
        return None
    with open(candidate, encoding="utf-8", errors="replace") as f:
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
    except (ValueError, OSError, OverflowError):
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
                int(m.group("year")), int(m.group("month")), int(m.group("day")),
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

    @staticmethod
    def _prefixed(line: str) -> str:
        """Return `line` with a uvicorn-style `<LEVEL>:` prefix iff it
        doesn't already start with one. Matches uvicorn's
        `INFO:     <message>` format so external log processors
        (Dozzle, docker logs viewers, Loki) can classify the line
        without reading the persistent log file separately. Empty
        lines pass through unchanged. The level is derived via the
        same `_severity_for` classifier that drives the persistent
        log + the SPA's Logs tab so classification stays consistent
        across every surface."""
        stripped = line.lstrip()
        if not stripped:
            return line
        # Already prefixed with a level — uvicorn-style `INFO:` /
        # `WARNING:` / `ERROR:` / `DEBUG:` / `CRITICAL:` (with the
        # optional spaces uvicorn pads with) OR our own
        # `<ISO 8601> <LEVEL>` shape from a previous tee write that
        # got fed back in. Pass through unchanged.
        if _RE_ALREADY_PREFIXED.match(stripped):
            return line
        level = _severity_for(line, "stdout")
        # Uvicorn pads its level to 8 chars total (`INFO:    `) so
        # all messages align in the terminal. Match that format.
        prefix = f"{level}:".ljust(9)
        return prefix + line

    def write(self, data: str) -> int:
        """Forward `data` to the wrapped stream and tee into the ring buffer + log file.

        Lines without an existing level prefix get one prepended
        (uvicorn-style `INFO:    <body>`) BEFORE being forwarded to
        the real stream so external log viewers (Dozzle, docker
        logs, Loki) can classify them without reading the persistent
        log file separately. The ring buffer + persistent log get
        the ORIGINAL (un-prefixed) text because they have their own
        per-record `level` field — no need to embed it in the body.

        Buffering contract: ONLY complete (newline-terminated) lines
        get forwarded to the wrapped stream. A partial tail (no
        newline yet) is stored in `self._partial` and held back
        until the next call brings the terminating newline — at
        which point it's assembled, prefixed, and written ONCE as
        a complete line. This prevents the "partial-then-prefixed"
        double-write bug where a partial would appear in the stream
        first as raw text, then the next call would re-write the
        same content as `INFO:    <body>`, producing
        `<body>INFO:    <body>` on a single visible line.
        """
        try:
            if not data:
                return 0
            buf = self._partial + data
            lines = buf.split("\n")
            # All but the last are complete lines; the last is carry-over
            # (either the next partial or the empty string after a
            # trailing newline). Store the partial for the next call
            # to assemble; only forward complete lines.
            self._partial = lines[-1]
            complete_lines = lines[:-1]
            if not complete_lines:
                # Data didn't bring any newline — nothing complete to
                # forward yet. Return the input length so the caller's
                # write-count accounting stays correct.
                return len(data)
            prefixed_lines = [self._prefixed(line) for line in complete_lines]
            forwarded = "\n".join(prefixed_lines) + "\n"
            # Discard the wrapped-stream's return value — we report
            # `len(data)` to the caller (per the file-object contract;
            # see the return statement below). The actual bytes
            # written to the stream differ because of the prefix
            # bytes; honouring the bytes-written contract here would
            # break callers that compare `write()` return == input
            # length.
            self._stream.write(forwarded)
            # Per-line timestamp (not batch-shared) so the SPA's Logs
            # tab renders each line with its own arrival time even
            # when uvicorn / sampler batches several writes in one
            # `write()` call. `time.time()` is monotonic within the
            # same call so the timestamps still preserve arrival
            # order — important for the renderer's ts-sort.
            for line in complete_lines:
                rec = {"ts": time.time(), "stream": self._label, "text": line}
                _buf.append(rec)
                # Persist to today's daily file. Best-effort — see
                # _persist_line() for the failure-mode contract.
                _persist_line(rec)
            # Return the original input length per the file-object
            # contract; the actual bytes written to the stream differ
            # because of the prefix, but callers measure success
            # against what they asked us to write.
            return len(data)
        except (OSError, ValueError, TypeError):
            # Never let the tee break real logging. Forward the raw
            # data + swallow + move on.
            try:
                return self._stream.write(data)
            except OSError:
                return 0

    def flush(self) -> None:
        """Flush the wrapped stream; swallow OS-level flush failures."""
        try:
            self._stream.flush()
        except OSError:
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
    """Return the current ring-buffer length (count of in-memory log records)."""
    return len(_buf)
