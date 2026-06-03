"""Weather provider — dispatches between Open-Meteo and WeatherAPI.com.

Two providers in one module, selected via the operator-configured
``weather_provider`` setting:

  - ``open-meteo`` (default) — free, no API key required, NO moon data.
    Calls https://api.open-meteo.com/v1/forecast?... and returns the
    normalised schema with ``supports_moon: False`` so downstream
    consumers (Apps moon widget, AI palette moon-question handling,
    Telegram /moon command) auto-disable cleanly.

  - ``weatherapi`` — requires a free API key (1M calls/month from
    weatherapi.com), returns ``supports_moon: True`` AND populates
    each forecast day's ``moon_phase`` / ``moon_illumination`` /
    ``moonrise`` / ``moonset`` fields.

Persistence: hourly samples are written to ``weather_samples`` by
``logic.weather_sampler``. The same table holds rows from EITHER
provider — the ``moon_phase`` / ``moon_illumination`` columns are
NULL on Open-Meteo rows.

This module owns the master gate (``is_enabled()``), the per-coord
in-process cache (so an operator switching providers mid-deploy hits
the new endpoint immediately), and the response-shape normaliser
(both providers return the same shape so the SPA / AI / Telegram
don't branch on provider name).
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

from logic.db import get_setting, get_setting_bool, read_location_setting
from logic.env_keys import EnvKey, env_get
from logic.settings_keys import Settings
from logic.tuning import Tunable, tuning_int

# Per-provider endpoint URLs come from ops config (env vars or the
# admin form), NOT from baked-in Python constants. Operator sets the
# URL in their `.env` file (``WEATHER_OPEN_METEO_ENDPOINT`` /
# ``WEATHER_WEATHERAPI_ENDPOINT``) OR pastes one in Admin → Weather
# → Base URL (per-deployment override). The dispatcher resolves the
# effective URL via the ``base_url()`` helper in priority order:
# DB setting → env var → empty (caller surfaces a "configure URL"
# error). Trailing slash stripped so the per-endpoint formatter
# (``base + "/forecast.json"``) composes cleanly.

# In-process cache keyed by (provider, lat, lon) at 2-decimal
# quantisation so minor coord variants for the same city share a
# cache entry. A provider switch invalidates the cache via
# ``invalidate_cache()`` so the next call fires the new endpoint
# immediately. Value is ``(fetched_ts: float, body: dict)``.
_cache: dict[tuple[str, float, float], tuple[float, dict[str, object]]] = {}

# WMO weather code → (description, local sprite key) for Open-Meteo's
# response. WeatherAPI.com uses its own numeric codes; the
# ``_local_icon_for_weatherapi`` helper handles that separately.
_WMO_CODES = {
    0: ("Clear sky", "theme-sun"),
    1: ("Mainly clear", "theme-sun"),
    2: ("Partly cloudy", "weather-partly-cloudy"),
    3: ("Overcast", "weather-cloud"),
    45: ("Foggy", "weather-fog"),
    48: ("Depositing rime fog", "weather-fog"),
    51: ("Light drizzle", "weather-rain"),
    53: ("Moderate drizzle", "weather-rain"),
    55: ("Dense drizzle", "weather-rain"),
    56: ("Light freezing drizzle", "weather-rain"),
    57: ("Dense freezing drizzle", "weather-rain"),
    61: ("Light rain", "weather-rain"),
    63: ("Moderate rain", "weather-rain"),
    65: ("Heavy rain", "weather-rain"),
    66: ("Light freezing rain", "weather-rain"),
    67: ("Heavy freezing rain", "weather-rain"),
    71: ("Light snow", "weather-snow"),
    73: ("Moderate snow", "weather-snow"),
    75: ("Heavy snow", "weather-snow"),
    77: ("Snow grains", "weather-snow"),
    80: ("Light rain showers", "weather-rain"),
    81: ("Moderate rain showers", "weather-rain"),
    82: ("Violent rain showers", "weather-rain"),
    85: ("Light snow showers", "weather-snow"),
    86: ("Heavy snow showers", "weather-snow"),
    95: ("Thunderstorm", "weather-thunder"),
    96: ("Thunderstorm with light hail", "weather-thunder"),
    99: ("Thunderstorm with heavy hail", "weather-thunder"),
}


def provider() -> str:
    """Active provider name. Falls back to ``open-meteo`` so a fresh
    deploy without operator action still gets a usable weather widget
    (the public Open-Meteo endpoint requires no key)."""
    raw = (get_setting(Settings.WEATHER_PROVIDER) or "").strip().lower()
    if raw in ("weatherapi", "weather-api", "weather_api"):
        return "weatherapi"
    return "open-meteo"


def is_enabled() -> bool:
    """Master gate. WeatherAPI requires a non-empty API key OR the
    call is guaranteed to 401; Open-Meteo just needs the master toggle
    on (no key needed)."""
    if not get_setting_bool(Settings.WEATHER_ENABLED):
        return False
    if provider() == "weatherapi":
        return bool((get_setting(Settings.WEATHER_API_KEY) or "").strip())
    return True


def supports_moon() -> bool:
    """True when the active provider returns moon-phase / illumination
    data. Drives the Apps moon-widget gate, the Telegram /moon
    command's "feature unavailable" message, and the AI palette
    context-block — when False, the moon block is omitted from the
    AI prompt so the model doesn't hallucinate phases it can't see."""
    return provider() == "weatherapi"


def base_url() -> str:
    """Per-provider base URL — resolved from ops config.

    Priority order:
      1. DB setting (``weather_api_base_url``) — operator's per-deploy
         override pasted in Admin → Weather.
      2. Env var (``WEATHER_WEATHERAPI_ENDPOINT`` or
         ``WEATHER_OPEN_METEO_ENDPOINT`` depending on active provider)
         — operator's `.env`-level default.
      3. Empty string — caller surfaces a "Configure the base URL in
         Admin → Weather (or set the env var)" error.

    Trailing slash stripped so ``base + "/forecast.json"`` composes
    cleanly regardless of how the operator typed it.
    """
    raw = (get_setting(Settings.WEATHER_API_BASE_URL) or "").strip().rstrip("/")
    if raw:
        return raw
    env_key = (EnvKey.WEATHER_WEATHERAPI_ENDPOINT
               if provider() == "weatherapi"
               else EnvKey.WEATHER_OPEN_METEO_ENDPOINT)
    return env_get(env_key).strip().rstrip("/")


def default_location() -> Optional[dict]:
    """Legacy operator-configured fallback location.

    Kept for back-compat — the admin Weather tab no longer surfaces
    these fields, but the seed values still round-trip on the wire
    and a deploy that hasn't been re-saved keeps using its old
    default. The active sampler + AI palette code path is now
    ``user_locations()`` — this default is consulted only when the
    user-location iterator returns NO locations (no users have
    configured a weather location in Settings → Profile yet).
    """
    return read_location_setting(
        Settings.WEATHER_DEFAULT_LAT,
        Settings.WEATHER_DEFAULT_LON,
        Settings.WEATHER_DEFAULT_LABEL,
    )


def user_locations() -> list[dict]:
    """Every distinct user-configured weather location across the
    `users.ui_prefs.weather_*` blocks. Deduplicated by quantised
    (lat, lon) so two users in the same city contribute one sample
    per tick instead of two. Returns a list of
    ``{lat, lon, label}`` dicts; empty when no user has configured
    a location yet.

    The lifespan sampler iterates this list per tick — one row per
    location per tick — so the AI palette + Telegram + per-user
    UI surfaces all have historical samples to draw from.
    """
    import json
    from logic.db import db_conn
    seen: set[tuple[float, float]] = set()
    locations: list[dict] = []
    try:
        with db_conn() as c:
            cur = c.execute(
                "SELECT ui_prefs FROM users WHERE disabled IS NULL OR disabled = 0"
            )
            for (raw,) in cur.fetchall():
                if not raw:
                    continue
                try:
                    prefs = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(prefs, dict):
                    continue
                # The SPA writes the user's weather pref under the
                # camelCase `headerWeather*` keys (see
                # `static/js/app-admin.js` save-prefs payload). Read
                # those FIRST + fall back to legacy `weather_*` keys
                # so older preferences-format installs still work.
                # Coerce via str() so Pyright narrows from `Any | None`
                # cleanly — empty / None values are caught by the
                # subsequent try/except. Skip the row when either coord
                # can't parse as a float (operator typed garbage,
                # blank, or never configured a location).
                lat_raw = (
                    prefs.get("headerWeatherLat")
                    if prefs.get("headerWeatherLat") not in (None, "")
                    else prefs.get("weather_lat")
                )
                lon_raw = (
                    prefs.get("headerWeatherLon")
                    if prefs.get("headerWeatherLon") not in (None, "")
                    else prefs.get("weather_lon")
                )
                if lat_raw in (None, "") or lon_raw in (None, ""):
                    continue
                try:
                    lat = float(str(lat_raw))
                    lon = float(str(lon_raw))
                except (TypeError, ValueError):
                    continue
                key = (round(lat, 2), round(lon, 2))
                if key in seen:
                    continue
                seen.add(key)
                label_raw = (
                    prefs.get("headerWeatherLabel")
                    or prefs.get("weather_label")
                    or ""
                )
                locations.append({
                    "lat": lat,
                    "lon": lon,
                    "label": str(label_raw).strip(),
                })
    except Exception as e:  # noqa: BLE001
        print(f"[weather] user_locations read failed: {e}")
    return locations


def _quantise_key(lat: float, lon: float) -> tuple[float, float]:
    return round(float(lat), 2), round(float(lon), 2)


def _cache_ttl() -> float:
    try:
        return float(tuning_int(Tunable.WEATHER_CACHE_TTL_SECONDS))
    except (KeyError, ValueError, TypeError):
        return 600.0


def _fetch_timeout() -> float:
    try:
        return float(tuning_int(Tunable.WEATHER_FETCH_TIMEOUT_SECONDS))
    except (KeyError, ValueError, TypeError):
        return 8.0


def invalidate_cache() -> None:
    """Drop every cached entry. Called from the Admin → Weather Save
    handler so a re-configured provider OR a switched provider takes
    effect immediately."""
    _cache.clear()


# Per-(provider, coord, error-signature) throttle for the fetch-error log
# line below. Errors are NOT cached (see fetch()), so a multi-tab refresh
# storm during an upstream outage re-probes on every render — without a
# throttle the [weather] error line would flood the log. ~60s window means
# a real outage logs promptly (first hit) then stays quiet until it changes
# or recurs after the window. Cap the dict so a long uptime cycling through
# many coords / messages can't grow it unbounded.
_error_log_throttle: dict = {}
_ERROR_LOG_THROTTLE_SECONDS = 60.0


def _log_fetch_error(provider_name: str, qkey, label: str, err, upstream: str) -> None:
    """Print a weather-fetch failure to stdout — which the persistent-log
    capture routes to the daily log file + Admin → Logs (the ``[weather]``
    tag). Makes an upstream key / quota / location / HTTP failure
    DIAGNOSABLE instead of silently producing empty widgets + null history
    rows. Throttled per (provider, coord, error-signature). The word
    "error" in the message intentionally lands it in the ERROR severity
    bucket — a failed weather fetch IS a real failure the operator should
    see."""
    try:
        sig = (provider_name, qkey[0], qkey[1], str(err)[:120])
        now = time.time()
        if (now - _error_log_throttle.get(sig, 0.0)) < _ERROR_LOG_THROTTLE_SECONDS:
            return
        if len(_error_log_throttle) > 200:
            _error_log_throttle.clear()
        _error_log_throttle[sig] = now
        loc = label or f"{qkey[0]},{qkey[1]}"
        print(
            f"[weather] {provider_name} fetch error for {loc}: {err}"
            + (f" (upstream={upstream})" if upstream else ""),
            flush=True,
        )
    except Exception:  # noqa: BLE001 — logging must never break the fetch
        pass


async def fetch(lat: float, lon: float, *, label: str = "", force: bool = False) -> dict:
    """Fetch current + forecast (+ astronomy when WeatherAPI) for
    one lat/lon pair. Dispatches by ``provider()``.

    ``force=True`` bypasses the per-coord TTL cache and fetches a fresh
    response from the upstream — used by the explicit per-widget Refresh
    button so the operator gets current data on demand (the result is
    still written back to the cache for subsequent reads)."""
    if not is_enabled():
        return {"configured": False, "supports_moon": supports_moon(),
                "provider": provider()}
    active = provider()
    qkey = _quantise_key(lat, lon)
    key = (active, qkey[0], qkey[1])
    now = time.time()
    cached = _cache.get(key)
    if not force and cached and (now - cached[0]) < _cache_ttl():
        body = dict(cached[1])
        body["label"] = label or body.get("label") or ""
        body["cached"] = True
        return body
    if active == "weatherapi":
        body = await _fetch_weatherapi(
            qkey[0], qkey[1], label=label, fetched_at=int(now),
        )
    else:
        body = await _fetch_open_meteo(
            qkey[0], qkey[1], label=label, fetched_at=int(now),
        )
    if not body.get("error"):
        _cache[key] = (now, dict(body))
    else:
        # Surface the failure to stdout / log file / Admin → Logs (throttled)
        # — without this the request-path fetch returns the error silently
        # and the operator sees empty widgets with nothing in the logs.
        _log_fetch_error(active, qkey, label, body.get("error"), body.get("upstream") or "")
    body["cached"] = False
    return body


# --------------------------------------------------------------------
# Open-Meteo (default — no API key required, NO moon data)
# --------------------------------------------------------------------
async def _fetch_open_meteo(
    lat: float, lon: float, *, label: str, fetched_at: int,
) -> dict:
    upstream = base_url()
    if not upstream:
        return {"configured": False, "supports_moon": False, "provider": "open-meteo",
                "error": "Configure the Open-Meteo base URL in Admin → Weather "
                         "(or set the WEATHER_OPEN_METEO_ENDPOINT env var)"}
    params = {
        "latitude": str(lat),
        "longitude": str(lon),
        "current": "temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m",
        "daily": (
            "temperature_2m_max,temperature_2m_min,weather_code,"
            "precipitation_sum,sunrise,sunset"
        ),
        "forecast_days": "7",
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=_fetch_timeout()) as client:
            r = await client.get(upstream, params=params)
            r.raise_for_status()
            j = r.json() or {}
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "supports_moon": False, "provider": "open-meteo",
                "error": str(e), "label": label, "upstream": upstream}
    cur = j.get("current") or {}
    code = int(cur.get("weather_code") or 0)
    desc, icon = _WMO_CODES.get(code, ("Unknown", "weather-cloud"))
    forecast = []
    _raw_daily = j.get("daily")
    daily = _raw_daily if isinstance(_raw_daily, dict) else {}
    times = daily.get("time") or []
    tmaxes = daily.get("temperature_2m_max") or []
    tmines = daily.get("temperature_2m_min") or []
    dcodes = daily.get("weather_code") or []
    precips = daily.get("precipitation_sum") or []
    sunrises = daily.get("sunrise") or []
    sunsets = daily.get("sunset") or []
    for i in range(min(len(times), 7)):
        try:
            d_code = int(dcodes[i]) if i < len(dcodes) else 0
        except (TypeError, ValueError):
            d_code = 0
        d_desc, _d_icon = _WMO_CODES.get(d_code, ("Unknown", "weather-cloud"))
        forecast.append({
            "date": times[i],
            "temp_max_c": tmaxes[i] if i < len(tmaxes) else None,
            "temp_min_c": tmines[i] if i < len(tmines) else None,
            "code": d_code,
            "condition": d_desc,
            "precip_mm": precips[i] if i < len(precips) else None,
            "sunrise": sunrises[i] if i < len(sunrises) else None,
            "sunset": sunsets[i] if i < len(sunsets) else None,
            # NO moon data on Open-Meteo — surface as None so downstream
            # consumers gate cleanly via `supports_moon`.
            "moonrise": None,
            "moonset": None,
            "moon_phase": None,
            "moon_illumination": None,
        })
    return {
        "configured": True,
        "supports_moon": False,
        "provider": "open-meteo",
        "label": label,
        "temp_c": cur.get("temperature_2m"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind_kmh": cur.get("wind_speed_10m"),
        "code": code,
        "condition": desc,
        "icon": icon,
        "forecast": forecast,
        "upstream": upstream,
        "fetched_at": fetched_at,
        "timezone": j.get("timezone") or "",
        "timezone_abbrev": j.get("timezone_abbreviation") or "",
        "utc_offset_seconds": j.get("utc_offset_seconds") or 0,
    }


# --------------------------------------------------------------------
# WeatherAPI.com — requires API key, returns full astronomy block
# --------------------------------------------------------------------
async def _fetch_weatherapi(
    lat: float, lon: float, *, label: str, fetched_at: int,
) -> dict:
    api_key = (get_setting(Settings.WEATHER_API_KEY) or "").strip()
    if not api_key:
        return {"configured": False, "supports_moon": True, "provider": "weatherapi"}
    base = base_url()
    if not base:
        return {"configured": False, "supports_moon": True, "provider": "weatherapi",
                "error": "Configure the WeatherAPI base URL in Admin → Weather "
                         "(or set the WEATHER_WEATHERAPI_ENDPOINT env var)"}
    upstream = base + "/forecast.json"
    params = {
        "key": api_key,
        "q": f"{lat},{lon}",
        "days": "7",
        "aqi": "no",
        "alerts": "no",
    }
    try:
        async with httpx.AsyncClient(timeout=_fetch_timeout()) as client:
            r = await client.get(upstream, params=params)
            r.raise_for_status()
            j = r.json() or {}
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "supports_moon": True, "provider": "weatherapi",
                "error": str(e), "label": label, "upstream": upstream}
    # WeatherAPI signals key / quota / location failures via an in-body
    # ``{"error": {"code", "message"}}`` object — frequently with HTTP 200
    # (so raise_for_status above does NOT catch it). Without this check the
    # missing ``current`` block parses as temp_c=None and we'd report a
    # FALSE success: the sampler would write a useless null ("—") history
    # row and the widget would render an empty body with no explanation.
    # Surface it as a real error so callers skip it + the reason (e.g.
    # "API key has exceeded calls per month") reaches the logs + the
    # widget's empty-state. Common codes: 1002/2006/2008 (key invalid /
    # disabled), 2007 (monthly quota), 2009 (key has no access).
    if isinstance(j.get("error"), dict):
        err = j["error"]
        emsg = (str(err.get("message") or "").strip()) or "WeatherAPI returned an error"
        ecode = err.get("code")
        return {"configured": True, "supports_moon": True, "provider": "weatherapi",
                "error": (f"WeatherAPI error {ecode}: {emsg}" if ecode else emsg),
                "label": label, "upstream": upstream}
    current = (j.get("current") or {}) if isinstance(j.get("current"), dict) else {}
    cond_obj = (current.get("condition") or {}) if isinstance(current.get("condition"), dict) else {}
    code = int(cond_obj.get("code") or 0)
    desc = (cond_obj.get("text") or "Unknown").strip()
    location = (j.get("location") or {}) if isinstance(j.get("location"), dict) else {}
    forecast_obj = (j.get("forecast") or {}) if isinstance(j.get("forecast"), dict) else {}
    days_raw = forecast_obj.get("forecastday") or []
    forecast = []
    if isinstance(days_raw, list):
        for d in days_raw[:7]:
            if not isinstance(d, dict):
                continue
            day_block = d.get("day") or {}
            astro = d.get("astro") or {}
            day_cond = day_block.get("condition") or {}
            forecast.append({
                "date": d.get("date") or "",
                "temp_max_c": day_block.get("maxtemp_c"),
                "temp_min_c": day_block.get("mintemp_c"),
                "code": int(day_cond.get("code") or 0),
                "condition": (day_cond.get("text") or "").strip(),
                "precip_mm": day_block.get("totalprecip_mm"),
                "humidity": day_block.get("avghumidity"),
                "uv_index": day_block.get("uv"),
                "sunrise": astro.get("sunrise") or "",
                "sunset": astro.get("sunset") or "",
                "moonrise": astro.get("moonrise") or "",
                "moonset": astro.get("moonset") or "",
                "moon_phase": astro.get("moon_phase") or "",
                "moon_illumination": _to_float(astro.get("moon_illumination")),
            })
    return {
        "configured": True,
        "supports_moon": True,
        "provider": "weatherapi",
        "label": label or (location.get("name") or "").strip(),
        "temp_c": current.get("temp_c"),
        "humidity": current.get("humidity"),
        "wind_kmh": current.get("wind_kph"),
        "code": code,
        "condition": desc,
        "icon": _local_icon_for_weatherapi(code, cond_obj.get("text") or ""),
        "forecast": forecast,
        "upstream": upstream,
        "fetched_at": fetched_at,
        "location": {
            "name": location.get("name") or "",
            "region": location.get("region") or "",
            "country": location.get("country") or "",
            "tz_id": location.get("tz_id") or "",
        },
    }


def _to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _local_icon_for_weatherapi(code: int, condition_text: str) -> str:
    """Map WeatherAPI condition code → local sprite key. Reference:
    https://www.weatherapi.com/docs/weather_conditions.json"""
    c = int(code or 0)
    text = (condition_text or "").lower()
    if c == 1000:
        return "theme-sun"
    if c == 1003:
        return "weather-partly-cloudy"
    if c in (1006, 1009):
        return "weather-cloud"
    if c in (1030, 1135, 1147):
        return "weather-fog"
    if c in (1063, 1150, 1153, 1168, 1171, 1180, 1183, 1186, 1189,
             1192, 1195, 1198, 1201, 1240, 1243, 1246):
        return "weather-rain"
    if c in (1066, 1069, 1072, 1114, 1117, 1204, 1207, 1210, 1213,
             1216, 1219, 1222, 1225, 1237, 1249, 1252, 1255, 1258,
             1261, 1264):
        return "weather-snow"
    if c in (1087, 1273, 1276, 1279, 1282):
        return "weather-thunder"
    if "sun" in text or "clear" in text:
        return "theme-sun"
    if "thunder" in text:
        return "weather-thunder"
    if "snow" in text or "blizzard" in text or "ice" in text:
        return "weather-snow"
    if "rain" in text or "drizzle" in text or "shower" in text:
        return "weather-rain"
    if "fog" in text or "mist" in text or "haze" in text:
        return "weather-fog"
    return "weather-cloud"
