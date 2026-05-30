"""Public-IP + ISP / ASN lookup (standalone subsystem).

Operator-opt-in fetch from ifconfig.co (a free, no-API-key public-IP
JSON service). Result is cached in-process for the
``tuning_public_ip_cache_ttl_seconds`` window — a single deploy hits
the upstream at most ~144 times/day at the 600s default even under
heavy usage. The cache is shared across SPA palette + Telegram
listener so both context-builders get one result per cache window.

Privacy: the operator must explicitly enable this in Admin → Public IP
(``tuning_public_ip_enabled = 1``) before this runs. Default is OFF
because fetching reveals the deployment is making an outbound request
to ifconfig.co (a third-party service) and the result includes IP /
ASN / geolocation that some operators consider sensitive.

This module is intentionally NOT AI-coupled — the AI palette and the
Telegram ``/ip`` command both consume it, but the feature stands on
its own with its own admin section. Other consumers (status pages,
diagnostic exports) can call ``fetch()`` directly.

Returns ``{ip, isp, asn, country, city}`` on success, ``None`` on any
failure (network error, parse failure, gate-off). Callers should
gate-check ``is_enabled()`` BEFORE awaiting ``fetch()`` so the
disabled-by-default path doesn't even create an HTTP client.
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

from logic.tuning import Tunable, tuning_int

_cache: dict = {"ts": 0.0, "data": None, "neg_until": 0.0}
# Negative-cache window for failed fetches (network error / non-200 / parse
# failure). Capped at 60s so a transient outage can't lock the chip on "no
# data" for the FULL positive TTL (600s default) — operators expect a 1-min
# recovery, not a 10-min one. min() against the operator-set positive TTL
# preserves the contract that the negative window is never LONGER than the
# success window even when an operator dials the success TTL down.
_NEG_TTL_CAP_SECONDS = 60.0

# Lookup endpoint URL — resolved from ops config (env var) at call
# time rather than baked here as a Python constant, matching the
# weather provider's "no static URLs in the code" discipline.
# Operator sets ``PUBLIC_IP_LOOKUP_URL`` in `.env` to override the
# default. ifconfig.co accepts an Accept: application/json header.
# Other free alternatives if this ever needs replacing: ipinfo.io/json
# (rate-limited without an API key), api.ipify.org (no ASN),
# ip-api.com (no HTTPS without paid tier). ifconfig.co is the
# simplest no-key option with ASN + ISP + country in one call.
_DEFAULT_LOOKUP_URL = "https://ifconfig.co/json"


def _lookup_url() -> str:
    """Resolve the lookup URL — operator env-var override first, then
    the well-known ifconfig.co fallback. Trailing whitespace stripped
    so a typo'd `.env` line with extra space doesn't break HTTP."""
    from logic.env_keys import EnvKey, env_get
    raw = env_get(EnvKey.PUBLIC_IP_LOOKUP_URL).strip()
    return raw or _DEFAULT_LOOKUP_URL


def is_enabled() -> bool:
    """Master gate. Operator flips ``tuning_public_ip_enabled``
    (Admin → Public IP) to authorise outbound calls to ifconfig.co.
    Encoded as an int tunable (1 = on, 0 = off) per the canonical
    TUNABLES pattern — see the project conventions "Plain-settings escape hatch is
    a drift class"."""
    try:
        return bool(tuning_int(Tunable.PUBLIC_IP_ENABLED))
    except (KeyError, ValueError, TypeError):
        return False


async def fetch(force: bool = False) -> Optional[dict]:
    """Return ``{ip, isp, asn, country, city}`` or None.

    Cache-aware: a fresh entry (TTL configurable via
    ``tuning_public_ip_cache_ttl_seconds``) returns immediately
    without hitting the upstream. Errors are logged + cached as None
    for a short window so a transient outage doesn't hammer
    ifconfig.co on every call.

    ``force=True`` bypasses the positive TTL cache (used by the explicit
    per-widget Refresh button) so the operator gets a fresh lookup on
    demand; the result is still written back to the cache for subsequent
    reads. The negative cache still applies under force so a Refresh
    spammed during an upstream outage doesn't hammer ifconfig.co.
    """
    if not is_enabled():
        return None
    now = time.time()
    try:
        ttl = float(tuning_int(Tunable.PUBLIC_IP_CACHE_TTL_SECONDS))
    except (KeyError, ValueError, TypeError):
        ttl = 600.0
    if not force and now - _cache["ts"] < ttl and _cache["data"] is not None:
        return _cache["data"]
    # Negative cache — a recent failure short-circuits ifconfig.co under
    # sustained outages so we don't hammer the upstream on every call.
    if now < _cache["neg_until"]:
        return None
    try:
        timeout = float(tuning_int(Tunable.PUBLIC_IP_FETCH_TIMEOUT_SECONDS))
    except (KeyError, ValueError, TypeError):
        timeout = 8.0
    neg_ttl = min(_NEG_TTL_CAP_SECONDS, ttl)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(_lookup_url(), headers={"Accept": "application/json"})
            if r.status_code != 200:
                print(f"[public_ip] ifconfig.co HTTP {r.status_code} — negative-cached for {neg_ttl:.0f}s")
                _cache["neg_until"] = now + neg_ttl
                return None
            j = r.json() or {}
    except (httpx.HTTPError, ValueError) as e:
        print(f"[public_ip] fetch failed: {e} — negative-cached for {neg_ttl:.0f}s")
        _cache["neg_until"] = now + neg_ttl
        return None
    # ifconfig.co schema: ip, country, country_iso, city, asn, asn_org.
    # `asn` is the AS number string ("AS15169"); `asn_org` is the
    # operator-readable ISP name ("Google LLC"). Normalise into the
    # `isp` alias most operators expect. `country_iso` is the 2-letter
    # ISO 3166-1 alpha-2 country code ("EG", "US", "DE") — the SPA
    # uses it to render a 🇪🇬 flag emoji via Unicode regional-indicator
    # mapping (no flag-image asset bundle needed).
    out: dict = {
        "ip": str(j.get("ip") or "").strip(),
        "isp": str(j.get("asn_org") or j.get("hostname") or "").strip(),
        "asn": str(j.get("asn") or "").strip(),
        "country": str(j.get("country") or "").strip(),
        "country_code": str(j.get("country_iso") or "").strip().upper(),
        "city": str(j.get("city") or "").strip(),
    }
    # Cache even when the upstream returned partial data — the AI
    # prompt block gates per-field on truthy so missing fields just
    # don't render. Clear any prior negative-cache window so a recovered
    # ifconfig.co immediately gets credit (don't keep returning stale
    # None after success lands).
    _cache["ts"] = now
    _cache["data"] = out
    _cache["neg_until"] = 0.0
    # Diagnostic: log per-cache-miss what the upstream actually
    # returned so the user can correlate the SPA widget's render
    # state against the real backend payload without needing
    # DevTools. The line is shape-only (which fields are populated)
    # rather than the raw IP — IP is operator-private and shouldn't
    # be repeated in stdout on every refresh; the per-field
    # populated/empty markers are enough to debug "country flag
    # not rendering" (missing country_code) vs "ISP icon broken"
    # (unrecognised isp brand). Fires only on cache miss (not on
    # every consumer call), so retention is bounded by the TTL.
    print(
        f"[public_ip] fetched: ip_set={bool(out['ip'])} "
        f"isp={out['isp']!r} asn={out['asn']!r} "
        f"country_code={out['country_code']!r} "
        f"country={out['country']!r} city_set={bool(out['city'])}"
    )
    # Persist to public_ip_history when the IP changed from the most
    # recently-recorded row. Best-effort — a DB blip doesn't block the
    # fetch flow. Skip when IP is empty (partial-data hit; nothing
    # meaningful to record). Same row deduped via "INSERT only on
    # change against the latest row" so a stable IP doesn't accumulate
    # a row every cache miss.
    try:
        ip_val = (out.get("ip") or "").strip()
        if ip_val:
            _record_ip_change(int(now), ip_val, out)
    # noinspection PyBroadException
    except Exception as e:
        print(f"[public_ip] history write failed: {e}")
    return out


def _record_ip_change(ts: int, ip: str, payload: dict) -> None:
    """INSERT a row into ``public_ip_history`` IFF ``ip`` differs from
    the most recently-stored row's ip. No-op when unchanged. Lazy
    import on logic.db to keep public_ip.py importable from contexts
    where the DB isn't ready (e.g. one-shot CLI tools). Caller catches
    + logs."""
    from logic.db import db_conn
    with db_conn() as c:
        row = c.execute(
            "SELECT ip FROM public_ip_history ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        prev_ip = (row[0] if row else "") or ""
        if prev_ip == ip:
            return
        c.execute(
            "INSERT OR REPLACE INTO public_ip_history "
            "(ts, ip, isp, asn, country, city) VALUES (?, ?, ?, ?, ?, ?)",
            (
                ts, ip,
                (payload.get("isp") or "") or None,
                (payload.get("asn") or "") or None,
                (payload.get("country") or "") or None,
                (payload.get("city") or "") or None,
            ),
        )
        print(f"[public_ip] recorded IP change: {prev_ip or '(first)'} -> {ip}")


def invalidate_cache() -> None:
    """Force the next fetch() to re-probe. Call after the operator
    toggles tuning_public_ip_enabled so a freshly-enabled deploy
    doesn't serve a stale None from an earlier gate-off call. Also
    drops any active negative-cache window so a re-enable doesn't have
    to wait out a prior failure's negative-TTL."""
    _cache["ts"] = 0.0
    _cache["data"] = None
    _cache["neg_until"] = 0.0
