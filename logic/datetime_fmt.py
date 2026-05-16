"""Server-side equivalent of the SPA's `_applyDateTimeFormat` token
parser. Renders a datetime against the same token grammar the SPA uses
so backend surfaces (Telegram replies, AI palette responses, log
exports) match what the operator sees in the UI.

Token grammar (longest-first matched at each position):

  yyyy   4-digit year (2026)
  yy     2-digit year (26)
  MMMM   full month name (January)
  MMM    short month name (Jan)
  MM     zero-padded month (01-12)
  M      month (1-12)
  dd     zero-padded day (01-31)
  d      day (1-31)
  HH     zero-padded hour 24h (00-23)
  H      hour 24h (0-23)
  hh     zero-padded hour 12h (01-12)
  h      hour 12h (1-12)
  mm     zero-padded minutes (00-59)
  m      minutes (0-59)
  ss     zero-padded seconds (00-59)
  s      seconds (0-59)
  a      AM / PM
  'lit'  single-quoted literal (passed through verbatim)

Everything else passes through (commas / colons / slashes / spaces).
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable


# Canonical default — matches the SPA's `DEFAULT_DATETIME_FORMAT` so a
# user who hasn't set a custom preference gets the same render in both
# surfaces.
DEFAULT_DATETIME_FORMAT = "dd/MM/yyyy, HH:mm:ss"

_MONTHS_LONG = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_MONTHS_SHORT = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

# Token list — longer tokens BEFORE shorter so `MM` doesn't get matched
# as two separate `M` tokens. Each entry is (token, accessor) where
# accessor is a callable `(now) -> str`.
_TOKENS: list[tuple[str, Callable[[datetime], str]]] = [
    ("yyyy", lambda d: f"{d.year:04d}"),
    ("yy",   lambda d: f"{d.year % 100:02d}"),
    ("MMMM", lambda d: _MONTHS_LONG[d.month - 1]),
    ("MMM",  lambda d: _MONTHS_SHORT[d.month - 1]),
    ("MM",   lambda d: f"{d.month:02d}"),
    ("M",    lambda d: str(d.month)),
    ("dd",   lambda d: f"{d.day:02d}"),
    ("d",    lambda d: str(d.day)),
    ("HH",   lambda d: f"{d.hour:02d}"),
    ("H",    lambda d: str(d.hour)),
    ("hh",   lambda d: f"{((d.hour + 11) % 12) + 1:02d}"),
    ("h",    lambda d: str(((d.hour + 11) % 12) + 1)),
    ("mm",   lambda d: f"{d.minute:02d}"),
    ("m",    lambda d: str(d.minute)),
    ("ss",   lambda d: f"{d.second:02d}"),
    ("s",    lambda d: str(d.second)),
    ("a",    lambda d: "PM" if d.hour >= 12 else "AM"),
]


def apply_datetime_format(d: datetime, fmt: str | None) -> str:
    """Render ``d`` (a tz-aware or naive ``datetime``) against ``fmt``.

    Falls back to ``DEFAULT_DATETIME_FORMAT`` when ``fmt`` is empty /
    None. Single-quoted segments are extracted first so token-replace
    doesn't see their content; placeholders are restored at the end.
    """
    if d is None:
        return "—"
    fmt_clean = (fmt or DEFAULT_DATETIME_FORMAT).strip() or DEFAULT_DATETIME_FORMAT

    # Extract literal-quoted segments so token-replace doesn't see
    # their content. Use NUL-bracketed placeholders matching the SPA's
    # implementation.
    literals: list[str] = []

    def _stash(match):
        literals.append(match.group(1))
        return f"\x00{len(literals) - 1}\x00"

    import re as _re
    work = _re.sub(r"'([^']*)'", _stash, fmt_clean)

    # Walk char-by-char, greedily matching the longest token at each
    # position. Anything not matched passes through verbatim.
    out: list[str] = []
    i = 0
    n = len(work)
    while i < n:
        ch = work[i]
        if ch == "\x00":
            end = work.find("\x00", i + 1)
            if end > 0:
                try:
                    idx = int(work[i + 1:end])
                    out.append(literals[idx])
                except (ValueError, IndexError):
                    pass
                i = end + 1
                continue
        matched = False
        for token, accessor in _TOKENS:
            if work.startswith(token, i):
                try:
                    out.append(accessor(d))
                except Exception:
                    pass
                i += len(token)
                matched = True
                break
        if not matched:
            out.append(ch)
            i += 1
    return "".join(out)


def strip_time_tokens(fmt: str) -> str:
    """Return a date-only variant of ``fmt`` by removing every
    time-related token (``H`` / ``h`` / ``m`` / ``s`` / ``a``) and
    tidying the separators (commas / colons / dashes / whitespace)
    the strip leaves behind. Mirrors the SPA's `fmtDateOnly` logic so
    a user with full-format ``dd/MM/yyyy, HH:mm:ss`` gets
    ``dd/MM/yyyy`` on the date-only surface.
    """
    import re as _re
    if not fmt:
        return "dd/MM/yyyy"
    # Strip every time token. Order: longer-first so `mm` isn't
    # matched as two `m`s.
    out = fmt
    for token in ("HH", "H", "hh", "h", "mm", "m", "ss", "s", "a"):
        out = out.replace(token, "")
    # Tidy: collapse multiple spaces, drop dangling separators around
    # the trimmed token gaps.
    out = _re.sub(r"\s+", " ", out)
    out = _re.sub(r"^[,\-:\s]+|[,\-:\s]+$", "", out)
    return out.strip() or "dd/MM/yyyy"


def get_user_datetime_format(username: str) -> str:
    """Read ``ui_prefs.datetime_format`` for one user. Returns the
    canonical default when unset / malformed. Read-only DB query;
    never raises."""
    import json
    from logic.db import db_conn
    if not username:
        return DEFAULT_DATETIME_FORMAT
    try:
        with db_conn() as c:
            row = c.execute(
                "SELECT ui_prefs FROM users WHERE username = ?", (username,)
            ).fetchone()
    except Exception:
        return DEFAULT_DATETIME_FORMAT
    if not row:
        return DEFAULT_DATETIME_FORMAT
    raw = row[0] if not hasattr(row, "keys") else row["ui_prefs"]
    if not raw:
        return DEFAULT_DATETIME_FORMAT
    try:
        prefs = json.loads(raw)
    except (ValueError, TypeError):
        return DEFAULT_DATETIME_FORMAT
    fmt = (prefs.get("datetime_format") or "").strip()
    return fmt or DEFAULT_DATETIME_FORMAT
