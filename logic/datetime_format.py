"""Server-side user-datetime formatter.

A Python mirror of the SPA's ``_applyDateTimeFormat`` token grammar
(``static/js/app-utils.js``) so backend surfaces that compose
operator-facing text — most notably the AI replies on Telegram + the
web sidebar — can render a timestamp in the SAME format the operator
picked under Settings → Profile → Formats (``ui_prefs.datetime_format``).

Token grammar (longest-first match, identical to the SPA):

    yyyy / yy        — 4- / 2-digit year
    MMMM / MMM       — full / short English month name
    MM / M           — zero-padded / bare month number
    dd / d           — zero-padded / bare day
    HH / H           — 24h hour (padded / bare)
    hh / h           — 12h hour (padded / bare)
    mm / m           — minute
    ss / s           — second
    a                — AM / PM
    'literal'        — single-quoted literal passthrough

Anything not a token (``/`` ``:`` ``,`` spaces) passes through verbatim.

Timezone: epoch / naive-ISO values are rendered in the operator's
``scheduler_timezone`` (the canonical "what day/time is it for OmniGrid"
knob — see ``logic/schedules.py``), falling back to container-local time
when that setting is blank/invalid. This matches the operator-visible
date-boundary rule in CLAUDE.md.

Month + AM/PM names are English here (the server has no per-request
browser locale; the SPA uses ``Intl`` for localized names and the
English arrays are its documented fallback path too).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union


DEFAULT_DATETIME_FORMAT = "dd/MM/yyyy, HH:mm:ss"

_MONTHS_LONG = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_MONTHS_SHORT = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _scheduler_tz():
    """Resolve the operator's scheduler timezone (or None = container-local).
    Lazy import so this leaf module never creates an import cycle."""
    try:
        from logic.schedules import scheduler_tz  # noqa: PLC0415
        return scheduler_tz()
    except Exception:  # noqa: BLE001 — never break formatting on a settings error
        return None


def _to_datetime(value: Union[int, float, str, datetime, None]):
    """Coerce an epoch / ISO string / datetime into a tz-aware (or naive)
    ``datetime`` in the operator's scheduler timezone. Returns None when the
    value can't be parsed."""
    if value is None:
        return None
    tz = _scheduler_tz()
    # Already a datetime.
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        # Epoch seconds → tz-aware in scheduler tz (or local when tz is None).
        try:
            return datetime.fromtimestamp(float(value), tz=tz)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Numeric-string epoch ("1730000000" / "1730000000.5").
        try:
            return datetime.fromtimestamp(float(s), tz=tz)
        except (ValueError, OverflowError, OSError):
            pass
        # ISO 8601. Python 3.11+ fromisoformat handles a trailing 'Z' and a
        # space date/time separator; normalise defensively for older shapes.
        iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            # Last-ditch: drop fractional seconds / trailing tokens.
            try:
                dt = datetime.fromisoformat(iso[:19].replace(" ", "T"))
            except ValueError:
                return None
    else:
        return None
    # Make the parsed datetime tz-aware: assume UTC when the source carried no
    # offset (Speedtest Tracker / most APIs emit UTC), then convert to the
    # operator's scheduler tz so the rendered wall-clock matches the rest of
    # the UI.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if tz is not None:
        try:
            dt = dt.astimezone(tz)
        except (ValueError, OSError):
            pass
    return dt


def format_user_datetime(value: Union[int, float, str, datetime, None],
                         fmt: Optional[str] = None) -> str:
    """Render ``value`` (epoch / ISO string / datetime) using the operator's
    ``datetime_format`` token grammar. Returns "" when the value can't be
    parsed (caller decides on a placeholder). ``fmt`` defaults to
    ``DEFAULT_DATETIME_FORMAT`` when blank."""
    dt = _to_datetime(value)
    if dt is None:
        return ""
    work = (fmt or "").strip() or DEFAULT_DATETIME_FORMAT

    y = dt.year
    mo = dt.month
    d = dt.day
    hh24 = dt.hour
    mi = dt.minute
    sec = dt.second
    h12 = ((hh24 + 11) % 12) + 1
    ampm = "PM" if hh24 >= 12 else "AM"

    # Extract single-quoted literals to placeholders so token-replace doesn't
    # match inside them (mirrors the SPA's NUL-sentinel approach).
    literals: list[str] = []

    def _stash(m) -> str:
        literals.append(m.group(1))
        return f"\x00{len(literals) - 1}\x00"

    import re  # noqa: PLC0415
    work = re.sub(r"'([^']*)'", _stash, work)

    # Longest token first so MM isn't split into two M, etc.
    replacements = (
        ("yyyy", str(y)),
        ("yy", f"{y % 100:02d}"),
        ("MMMM", _MONTHS_LONG[mo - 1]),
        ("MMM", _MONTHS_SHORT[mo - 1]),
        ("MM", f"{mo:02d}"),
        ("M", str(mo)),
        ("dd", f"{d:02d}"),
        ("d", str(d)),
        ("HH", f"{hh24:02d}"),
        ("H", str(hh24)),
        ("hh", f"{h12:02d}"),
        ("h", str(h12)),
        ("mm", f"{mi:02d}"),
        ("m", str(mi)),
        ("ss", f"{sec:02d}"),
        ("s", str(sec)),
        ("a", ampm),
    )

    out: list[str] = []
    i = 0
    n = len(work)
    while i < n:
        ch = work[i]
        if ch == "\x00":
            end = work.find("\x00", i + 1)
            if end > 0:
                try:
                    out.append(literals[int(work[i + 1:end])])
                except (ValueError, IndexError):
                    pass
                i = end + 1
                continue
        matched = False
        for tok, val in replacements:
            if work.startswith(tok, i):
                out.append(val)
                i += len(tok)
                matched = True
                break
        if not matched:
            out.append(ch)
            i += 1
    return "".join(out)


def user_datetime_format(username: Optional[str]) -> str:
    """Resolve one OmniGrid user's preferred ``datetime_format`` from
    ``ui_prefs.datetime_format``. Falls back to ``DEFAULT_DATETIME_FORMAT``
    when the user is unknown / has no preference / on any lookup error
    (so the caller always gets a usable format string)."""
    if not username:
        return DEFAULT_DATETIME_FORMAT
    try:
        from logic.db import db_conn  # noqa: PLC0415
        from logic.auth import get_user_by_username, get_user_profile  # noqa: PLC0415
        with db_conn() as conn:
            user = get_user_by_username(conn, str(username))
            if user is None:
                return DEFAULT_DATETIME_FORMAT
            prof = get_user_profile(conn, user.id)
    except Exception:  # noqa: BLE001 — never break formatting on a lookup error
        return DEFAULT_DATETIME_FORMAT
    prefs = (prof or {}).get("ui_prefs") if isinstance(prof, dict) else None
    if isinstance(prefs, dict):
        fmt = prefs.get("datetime_format")
        if isinstance(fmt, str) and fmt.strip():
            return fmt.strip()
    return DEFAULT_DATETIME_FORMAT
