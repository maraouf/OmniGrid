"""Prayer Times + Hijri date (standalone subsystem).

Operator-opt-in fetch from the AlAdhan API (api.aladhan.com — a free,
no-API-key Islamic prayer-times service). ONE call to
``/v1/timings/{date}`` returns the five daily prayers (Fajr / Dhuhr /
Asr / Maghrib / Isha) PLUS Sunrise PLUS the full ``date.hijri`` block,
so prayer times AND the Hijri calendar date come from the same fetch.

Mirrors the ``logic.public_ip`` / ``logic.weather`` shape: this module
owns the master gate (``is_enabled()``), the per-(coord, method,
school) in-process cache, the operator-configured defaults
(``default_location`` / ``default_method`` / ``default_school``), and
the response-shape normaliser. The custom-dashboard widget tile, the
AI palette context-builder, and the Telegram ``/prayer`` + ``/hijri``
commands all consume ``fetch()`` so they share one result per cache
window.

LOCATION comes from the logged-in user's existing weather location
(the SPA passes the user's ``headerWeatherLat/Lon`` to the endpoint);
``default_location()`` is only the fallback when no user location is
available. METHOD + Asr SCHOOL are operator settings in Admin → Prayer
Times (defaults: Egyptian General Authority = AlAdhan method 5; Asr
school Standard/Shafi = 0) — never hardcoded magic numbers downstream.

Privacy: default OFF. The operator must flip ``prayer_times_enabled``
(Admin → Prayer Times) before any outbound call to api.aladhan.com runs.

Returns a normalised dict (``configured`` False when gated off / no
location); callers gate-check ``is_enabled()`` before awaiting so the
disabled path never creates an HTTP client.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from logic.db import get_setting, get_setting_bool, read_location_setting
from logic.env_keys import EnvKey, env_get
from logic.settings_keys import Settings
from logic.tuning import Tunable, tuning_int

# AlAdhan calculation methods (id → human name). The Admin → Prayer
# Times method dropdown renders this map, and ``fetch()`` stamps the
# resolved ``method_name`` onto the response so the SPA / AI / Telegram
# don't have to carry the table. Source: api.aladhan.com method list.
METHODS: dict[int, str] = {
    0: "Shia Ithna-Ashari, Leva Institute, Qum",
    1: "University of Islamic Sciences, Karachi",
    2: "Islamic Society of North America (ISNA)",
    3: "Muslim World League",
    4: "Umm Al-Qura University, Makkah",
    5: "Egyptian General Authority of Survey",
    7: "Institute of Geophysics, University of Tehran",
    8: "Gulf Region",
    9: "Kuwait",
    10: "Qatar",
    11: "Majlis Ugama Islam Singapura, Singapore",
    12: "Union Organization Islamic de France",
    13: "Diyanet İşleri Başkanlığı, Turkey",
    14: "Spiritual Administration of Muslims of Russia",
    15: "Moonsighting Committee Worldwide",
    16: "Dubai (unofficial)",
    17: "Jabatan Kemajuan Islam Malaysia (JAKIM)",
    18: "Tunisia",
    19: "Algeria",
    20: "Kementerian Agama Republik Indonesia",
    21: "Morocco",
    22: "Comunidade Islamica de Lisboa",
    23: "Ministry of Awqaf, Islamic Affairs and Holy Places, Jordan",
}

# Default method (Egyptian General Authority) + Asr school (Standard /
# Shafi'i) when the operator hasn't picked one in Admin → Prayer Times.
# These are FALLBACKS for the settings resolvers below — the actual
# operator-chosen values live in the ``prayer_times_method`` /
# ``prayer_times_school`` settings.
DEFAULT_METHOD = 5
DEFAULT_SCHOOL = 0

# AlAdhan REST base — resolved from ops config at call time (DB setting
# → env var → well-known default), matching the weather provider's "no
# static URLs baked as Python constants" discipline.
_DEFAULT_BASE_URL = "https://api.aladhan.com/v1"

# Display order of the five obligatory prayers (for the card + the
# next-prayer computation). Sunrise is rendered as an informational row
# but is NOT a prayer, so it's excluded from ``prayers`` / ``next``.
_PRAYER_KEYS = ("fajr", "dhuhr", "asr", "maghrib", "isha")
# AlAdhan response timing keys (TitleCase) → our lowercase keys.
_TIMING_MAP = (
    ("fajr", "Fajr"),
    ("sunrise", "Sunrise"),
    ("dhuhr", "Dhuhr"),
    ("asr", "Asr"),
    ("maghrib", "Maghrib"),
    ("isha", "Isha"),
)
_PRAYER_NAMES = {
    "fajr": "Fajr",
    "sunrise": "Sunrise",
    "dhuhr": "Dhuhr",
    "asr": "Asr",
    "maghrib": "Maghrib",
    "isha": "Isha",
}

# In-process cache keyed by (qlat, qlon, method, school). Value is
# ``(fetched_ts: float, body: dict)``. The ``next`` block is volatile
# (time-relative) so it is recomputed on every read from the cached
# per-prayer epochs — only the static timings + Hijri block are cached.
_cache: dict[tuple[float, float, int, int], tuple[float, dict]] = {}
_NEG_TTL_CAP_SECONDS = 60.0
# Negative-cache window: a recent fetch failure short-circuits the
# upstream so a multi-tab refresh storm during an api.aladhan.com outage
# doesn't hammer it. Capped at 60s so recovery is prompt.
_neg_until: dict[tuple[float, float, int, int], float] = {}


def is_enabled() -> bool:
    """Master gate. Operator flips ``prayer_times_enabled`` (Admin →
    Prayer Times) to authorise outbound calls to api.aladhan.com.
    A plain DB-backed setting (like ``weather_enabled``) — NOT a tunable —
    so the admin toggle loads with the rest of the settings (default OFF
    for privacy)."""
    try:
        return get_setting_bool(Settings.PRAYER_TIMES_ENABLED, False)
    except (KeyError, ValueError, TypeError):
        return False


def base_url() -> str:
    """AlAdhan base URL — DB setting → env var → well-known default.
    Trailing slash stripped so ``base + "/timings/..."`` composes
    cleanly."""
    raw = (get_setting(Settings.PRAYER_TIMES_API_BASE_URL) or "").strip().rstrip("/")
    if raw:
        return raw
    env_raw = env_get(EnvKey.PRAYER_TIMES_API_BASE_URL).strip().rstrip("/")
    return env_raw or _DEFAULT_BASE_URL


def default_method() -> int:
    """Operator-configured AlAdhan calculation method (Admin → Prayer
    Times). Falls back to Egyptian General Authority (5). Clamped to a
    known method id."""
    raw = (get_setting(Settings.PRAYER_TIMES_METHOD) or "").strip()
    try:
        m = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_METHOD
    return m if m in METHODS else DEFAULT_METHOD


def default_school() -> int:
    """Operator-configured Asr school (0 = Standard/Shafi'i, 1 =
    Hanafi). Falls back to Standard (0)."""
    raw = (get_setting(Settings.PRAYER_TIMES_SCHOOL) or "").strip()
    try:
        s = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SCHOOL
    return 1 if s == 1 else 0


def default_location() -> Optional[dict]:
    """Operator-configured fallback location (Admin → Prayer Times).
    Consulted only when no per-user weather location is available.
    Returns ``{lat, lon, label}`` or None when unset / malformed."""
    return read_location_setting(
        Settings.PRAYER_TIMES_DEFAULT_LAT,
        Settings.PRAYER_TIMES_DEFAULT_LON,
        Settings.PRAYER_TIMES_DEFAULT_LABEL,
    )


def _cache_ttl() -> float:
    try:
        return float(tuning_int(Tunable.PRAYER_TIMES_CACHE_TTL_SECONDS))
    except (KeyError, ValueError, TypeError):
        return 3600.0


def _fetch_timeout() -> float:
    try:
        return float(tuning_int(Tunable.PRAYER_TIMES_FETCH_TIMEOUT_SECONDS))
    except (KeyError, ValueError, TypeError):
        return 8.0


def invalidate_cache() -> None:
    """Drop every cached entry + negative-cache window. Called from the
    Admin → Prayer Times Save handler so a changed method / school /
    enable-toggle takes effect immediately."""
    _cache.clear()
    _neg_until.clear()


def _strip_tz(t: str) -> str:
    """AlAdhan timings can carry a tz suffix — ``"05:12 (EET)"``. Keep
    the ``HH:MM`` head only."""
    return (t or "").strip().split(" ", 1)[0].strip()


def _zone(tzname: str):
    """Resolve an IANA tz name to a ZoneInfo, falling back to UTC so a
    bad / unknown name never crashes the epoch computation."""
    try:
        return ZoneInfo(tzname) if tzname else timezone.utc
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return timezone.utc


def _prayer_epoch(date_iso: str, hhmm: str, tz) -> Optional[int]:
    """Combine the response's gregorian date (``YYYY-MM-DD``) + an
    ``HH:MM`` timing in the location's tz into a Unix epoch. Returns None
    on any parse failure so the caller can skip that row."""
    try:
        y, mo, d = (int(x) for x in date_iso.split("-"))
        hh, mm = (int(x) for x in hhmm.split(":")[:2])
        dt = datetime(y, mo, d, hh, mm, tzinfo=tz)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return None


def _compute_next(timings: dict, now_epoch: int) -> Optional[dict]:
    """First obligatory prayer whose epoch is still in the future. When
    every prayer today has passed (after Isha), roll to tomorrow's Fajr
    (today's Fajr epoch + 24h — accurate to ~1 min/day, corrected on the
    next fetch)."""
    upcoming = []
    for k in _PRAYER_KEYS:
        row = timings.get(k) or {}
        ts = row.get("ts")
        if isinstance(ts, int) and ts > now_epoch:
            upcoming.append((ts, k))
    if upcoming:
        upcoming.sort()
        ts, k = upcoming[0]
        return {
            "key": k,
            "name": _PRAYER_NAMES.get(k, k.title()),
            "at_ts": ts,
            "in_seconds": max(0, ts - now_epoch),
            "tomorrow": False,
        }
    fajr = timings.get("fajr") or {}
    fts = fajr.get("ts")
    if isinstance(fts, int):
        nts = fts + 86400
        return {
            "key": "fajr",
            "name": _PRAYER_NAMES["fajr"],
            "at_ts": nts,
            "in_seconds": max(0, nts - now_epoch),
            "tomorrow": True,
        }
    return None


def _normalise(j: dict, *, lat: float, lon: float, label: str,
               method: int, school: int, fetched_at: int) -> dict:
    """Turn the AlAdhan ``data`` block into our compact response shape."""
    data = (j.get("data") or {}) if isinstance(j.get("data"), dict) else {}
    raw_timings = (data.get("timings") or {}) if isinstance(data.get("timings"), dict) else {}
    date_blk = (data.get("date") or {}) if isinstance(data.get("date"), dict) else {}
    greg = (date_blk.get("gregorian") or {}) if isinstance(date_blk.get("gregorian"), dict) else {}
    hijri = (date_blk.get("hijri") or {}) if isinstance(date_blk.get("hijri"), dict) else {}
    meta = (data.get("meta") or {}) if isinstance(data.get("meta"), dict) else {}

    tzname = str(meta.get("timezone") or "").strip()
    tz = _zone(tzname)
    date_iso = str(greg.get("date") or "").strip()  # "DD-MM-YYYY"
    # AlAdhan gregorian.date is DD-MM-YYYY; normalise to YYYY-MM-DD for
    # the epoch builder + the response.
    iso_ymd = date_iso
    if date_iso and "-" in date_iso:
        parts = date_iso.split("-")
        if len(parts) == 3 and len(parts[0]) == 2:
            iso_ymd = f"{parts[2]}-{parts[1]}-{parts[0]}"

    timings: dict = {}
    for key, src in _TIMING_MAP:
        hhmm = _strip_tz(str(raw_timings.get(src) or ""))
        if not hhmm:
            continue
        ts = _prayer_epoch(iso_ymd, hhmm, tz)
        timings[key] = {
            "key": key,
            "name": _PRAYER_NAMES.get(key, key.title()),
            "time": hhmm,
            "ts": ts,
            "prayer": key in _PRAYER_KEYS,
        }

    hijri_month = (hijri.get("month") or {}) if isinstance(hijri.get("month"), dict) else {}
    hijri_weekday = (hijri.get("weekday") or {}) if isinstance(hijri.get("weekday"), dict) else {}
    hijri_design = (hijri.get("designation") or {}) if isinstance(hijri.get("designation"), dict) else {}
    greg_weekday = (greg.get("weekday") or {}) if isinstance(greg.get("weekday"), dict) else {}

    try:
        month_num = int(hijri_month.get("number") or 0)
    except (TypeError, ValueError):
        month_num = 0

    hijri_out = {
        "day": str(hijri.get("day") or "").strip(),
        "month_en": str(hijri_month.get("en") or "").strip(),
        "month_ar": str(hijri_month.get("ar") or "").strip(),
        "month_num": month_num,
        "year": str(hijri.get("year") or "").strip(),
        "weekday_en": str(hijri_weekday.get("en") or "").strip(),
        "weekday_ar": str(hijri_weekday.get("ar") or "").strip(),
        "designation": str(hijri_design.get("abbreviated") or "AH").strip(),
        "text": str(hijri.get("date") or "").strip(),
    }

    meta_method = (meta.get("method") or {}) if isinstance(meta.get("method"), dict) else {}
    method_name = str(meta_method.get("name") or METHODS.get(method, "")).strip()

    body = {
        "configured": True,
        "timings": timings,
        "prayers": list(_PRAYER_KEYS),
        "hijri": hijri_out,
        "gregorian": {
            "date": iso_ymd,
            "weekday": str(greg_weekday.get("en") or "").strip(),
        },
        "method": method,
        "method_name": method_name,
        "school": school,
        "location": {"lat": lat, "lon": lon, "label": label},
        "timezone": tzname,
        "fetched_at": fetched_at,
    }
    return body


def _with_next(body: dict, *, label: str = "", cached: bool = False) -> dict:
    """Return a copy of a (fresh or cached) body with a freshly-computed
    ``next`` block + the caller's label applied. ``next`` is volatile so
    it's recomputed on every read — the countdown stays correct between
    upstream fetches."""
    out = dict(body)
    out["timings"] = body.get("timings") or {}
    out["next"] = _compute_next(out["timings"], int(time.time()))
    if label:
        loc = dict(out.get("location") or {})
        loc["label"] = label
        out["location"] = loc
    out["cached"] = cached
    return out


async def fetch(lat: float, lon: float, *, label: str = "",
                method: Optional[int] = None, school: Optional[int] = None,
                force: bool = False, bypass_gate: bool = False) -> dict:
    """Fetch today's prayer times + Hijri date for one lat/lon.

    ``method`` / ``school`` default to the operator's Admin → Prayer
    Times settings when None. ``force=True`` bypasses the positive TTL
    cache (the per-widget Refresh button); the negative cache still
    applies so a Refresh spammed during an upstream outage doesn't
    hammer api.aladhan.com. ``next`` is recomputed on every call (fresh
    or cached) so the countdown stays accurate.

    ``bypass_gate=True`` skips the ``is_enabled()`` master-gate check —
    used ONLY by the admin Test-connection route, where the operator has
    explicitly authorised this one outbound probe (mirrors Weather /
    Portainer / OIDC tests, which probe regardless of their enable
    toggle, so the admin can verify before enabling + saving). Every
    other caller leaves it False so the privacy default (no outbound
    call until enabled) holds.
    """
    if not bypass_gate and not is_enabled():
        return {"configured": False}
    m = default_method() if method is None else int(method)
    s = default_school() if school is None else (1 if int(school) == 1 else 0)
    if m not in METHODS:
        m = default_method()
    qlat = round(float(lat), 3)
    qlon = round(float(lon), 3)
    key = (qlat, qlon, m, s)
    now = time.time()

    cached = _cache.get(key)
    if not force and cached and (now - cached[0]) < _cache_ttl():
        return _with_next(cached[1], label=label, cached=True)
    if now < _neg_until.get(key, 0.0):
        return {"configured": True, "error": "temporarily unavailable",
                "location": {"lat": qlat, "lon": qlon, "label": label}}

    base = base_url()
    upstream = f"{base}/timings"
    params = {
        "latitude": str(qlat),
        "longitude": str(qlon),
        "method": str(m),
        "school": str(s),
    }
    neg_ttl = min(_NEG_TTL_CAP_SECONDS, _cache_ttl())
    try:
        async with httpx.AsyncClient(timeout=_fetch_timeout(), follow_redirects=True) as client:
            r = await client.get(
                upstream, params=params,
                headers={"Accept": "application/json"},
            )
            r.raise_for_status()
            j = r.json() or {}
    except (httpx.HTTPError, ValueError) as e:
        _neg_until[key] = now + neg_ttl
        print(f"[prayer_times] fetch error for {label or f'{qlat},{qlon}'}: {e} "
              f"— negative-cached for {neg_ttl:.0f}s", flush=True)
        return {"configured": True, "error": str(e),
                "location": {"lat": qlat, "lon": qlon, "label": label}}

    if str(j.get("code")) not in ("200", "200.0") and j.get("code") != 200:
        _neg_until[key] = now + neg_ttl
        status = str(j.get("status") or j.get("data") or "unexpected response")
        print(f"[prayer_times] api.aladhan.com error for {label or f'{qlat},{qlon}'}: "
              f"{status} — negative-cached for {neg_ttl:.0f}s", flush=True)
        return {"configured": True, "error": status,
                "location": {"lat": qlat, "lon": qlon, "label": label}}

    body = _normalise(j, lat=qlat, lon=qlon, label=label,
                      method=m, school=s, fetched_at=int(now))
    if not body.get("timings"):
        _neg_until[key] = now + neg_ttl
        print(f"[prayer_times] empty timings for {label or f'{qlat},{qlon}'} "
              f"— negative-cached for {neg_ttl:.0f}s", flush=True)
        return {"configured": True, "error": "no timings returned",
                "location": {"lat": qlat, "lon": qlon, "label": label}}

    _cache[key] = (now, dict(body))
    _neg_until.pop(key, None)
    nxt = _compute_next(body["timings"], int(now))
    print(f"[prayer_times] fetched {label or f'{qlat},{qlon}'}: "
          f"method={m} school={s} hijri={body['hijri'].get('text')!r} "
          f"next={(nxt or {}).get('name')!r}", flush=True)
    return _with_next(body, label=label)
