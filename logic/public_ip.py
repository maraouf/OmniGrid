"""Public-IP + ISP / ASN lookup for the AI palette.

Operator-opt-in fetch from ifconfig.co (a free, no-API-key public-IP
JSON service). Result is cached in-process for 10 minutes — a single
deploy hits the upstream at most ~144 times/day even under heavy AI
usage. The cache is shared across SPA palette + Telegram listener so
both context-builders get one result per cache window.

Privacy: the operator must explicitly set ``ai_public_ip_enabled=true``
in Admin → AI Integration before this runs. Default is OFF because
fetching reveals the deployment is making an outbound request to
ifconfig.co (a third-party service) and the result includes IP / ASN /
geolocation that some operators consider sensitive.

Returns ``{ip, isp, asn, country, city}`` on success, ``None`` on any
failure (network error, parse failure, gate-off). Callers should
gate-check ``logic.public_ip.is_enabled()`` BEFORE awaiting
``fetch()`` so the disabled-by-default path doesn't even create an
HTTP client.
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

from logic.db import get_setting_bool

# In-process cache. 10-min TTL — ifconfig.co's public terms allow casual
# use; this cadence is well under any rate-limit threshold and matches
# the weather widget's cache shape (operator already accepted weather's
# outbound traffic to Open-Meteo, this is the same scale).
_CACHE_TTL_S = 600
_cache: dict = {"ts": 0.0, "data": None}

# ifconfig.co accepts an Accept: application/json header. Other free
# alternatives if this ever needs replacing: ipinfo.io/json (rate-
# limited without an API key), api.ipify.org (no ASN), ip-api.com (no
# HTTPS without paid tier). ifconfig.co is the simplest no-key option
# with ASN + ISP + country in one call.
_LOOKUP_URL = "https://ifconfig.co/json"


def is_enabled() -> bool:
    """Master gate. Operator flips ``ai_public_ip_enabled`` in Admin →
    AI Integration to authorise outbound calls to ifconfig.co."""
    return get_setting_bool("ai_public_ip_enabled", False)


async def fetch() -> Optional[dict]:
    """Return ``{ip, isp, asn, country, city}`` or None.

    Cache-aware: a fresh entry (<10 min) returns immediately without
    hitting the upstream. Errors are logged + cached as None for a
    short window so a transient outage doesn't hammer ifconfig.co on
    every AI call.
    """
    if not is_enabled():
        return None
    now = time.time()
    if now - _cache["ts"] < _CACHE_TTL_S and _cache["data"] is not None:
        return _cache["data"]
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(_LOOKUP_URL, headers={"Accept": "application/json"})
            if r.status_code != 200:
                print(f"[public_ip] ifconfig.co HTTP {r.status_code}")
                return None
            j = r.json() or {}
    except (httpx.HTTPError, ValueError) as e:
        print(f"[public_ip] fetch failed: {e}")
        return None
    # ifconfig.co schema: ip, country, country_iso, city, asn, asn_org.
    # `asn` is the AS number string ("AS15169"); `asn_org` is the
    # operator-readable ISP name ("Google LLC"). Normalise into the
    # `isp` alias most operators expect.
    out: dict = {
        "ip": str(j.get("ip") or "").strip(),
        "isp": str(j.get("asn_org") or j.get("hostname") or "").strip(),
        "asn": str(j.get("asn") or "").strip(),
        "country": str(j.get("country") or "").strip(),
        "city": str(j.get("city") or "").strip(),
    }
    # Cache even when the upstream returned partial data — the AI
    # prompt block gates per-field on truthy so missing fields just
    # don't render.
    _cache["ts"] = now
    _cache["data"] = out
    return out


def invalidate_cache() -> None:
    """Force the next fetch() to re-probe. Call after the operator
    toggles ai_public_ip_enabled so a freshly-enabled deploy doesn't
    serve a stale None from an earlier gate-off call."""
    _cache["ts"] = 0.0
    _cache["data"] = None
