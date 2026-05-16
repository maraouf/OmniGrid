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

_cache: dict = {"ts": 0.0, "data": None}

# ifconfig.co accepts an Accept: application/json header. Other free
# alternatives if this ever needs replacing: ipinfo.io/json (rate-
# limited without an API key), api.ipify.org (no ASN), ip-api.com (no
# HTTPS without paid tier). ifconfig.co is the simplest no-key option
# with ASN + ISP + country in one call.
_LOOKUP_URL = "https://ifconfig.co/json"


def is_enabled() -> bool:
    """Master gate. Operator flips ``tuning_public_ip_enabled``
    (Admin → Public IP) to authorise outbound calls to ifconfig.co.
    Encoded as an int tunable (1 = on, 0 = off) per the canonical
    TUNABLES pattern — see CLAUDE.md "Plain-settings escape hatch is
    a drift class"."""
    try:
        return bool(tuning_int(Tunable.PUBLIC_IP_ENABLED))
    except (KeyError, ValueError, TypeError):
        return False


async def fetch() -> Optional[dict]:
    """Return ``{ip, isp, asn, country, city}`` or None.

    Cache-aware: a fresh entry (TTL configurable via
    ``tuning_public_ip_cache_ttl_seconds``) returns immediately
    without hitting the upstream. Errors are logged + cached as None
    for a short window so a transient outage doesn't hammer
    ifconfig.co on every call.
    """
    if not is_enabled():
        return None
    now = time.time()
    try:
        ttl = float(tuning_int(Tunable.PUBLIC_IP_CACHE_TTL_SECONDS))
    except (KeyError, ValueError, TypeError):
        ttl = 600.0
    if now - _cache["ts"] < ttl and _cache["data"] is not None:
        return _cache["data"]
    try:
        timeout = float(tuning_int(Tunable.PUBLIC_IP_FETCH_TIMEOUT_SECONDS))
    except (KeyError, ValueError, TypeError):
        timeout = 8.0
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
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
    toggles tuning_public_ip_enabled so a freshly-enabled deploy
    doesn't serve a stale None from an earlier gate-off call."""
    _cache["ts"] = 0.0
    _cache["data"] = None
