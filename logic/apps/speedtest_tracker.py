"""Speedtest Tracker per-app module.

Encapsulates everything Speedtest-Tracker-specific so the route
layer (``main_pkg/apps_routes.py``) stays generic. Public surface:

    SLUGS               — catalog slugs this module handles.
    requires_api_key()  — True (Speedtest Tracker needs Bearer auth).
    resolve_base_url(host_row, chip) -> str
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip) -> dict

The fetch path memoises results per (host_id, service_idx) for
``CACHE_TTL_S`` seconds so a busy Apps view doesn't hammer the
upstream. The cache is process-local; the single-replica deploy
constraint makes the dict-cache correct.

Upstream API reference: https://docs.speedtest-tracker.dev/api/authorization
Endpoints used:
    GET /api/v1/speedtests/latest  — test-credential probe
    GET /api/v1/speedtests?perPage=30 — data fetch (latest + series + avg)
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx


# Catalog template slugs handled by this module. The registry maps
# each slug to this module's exports; adding an alias slug here is
# enough to wire a second template (e.g. a community fork).
SLUGS: tuple[str, ...] = ("speedtest-tracker", "speedtest")


# Bounded per-(host_id, service_idx) cache so repeat reads within
# the TTL window skip the upstream round-trip. Tunable would be
# overkill for a single-app cache — 60s matches the typical scan
# cadence + is short enough that an operator force-refresh covers
# any "I just rebuilt the upstream and want fresh data now" case.
CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Speedtest Tracker requires Bearer-auth on every endpoint
    documented for OmniGrid's use; the editor MUST render the
    api_key input + Test-connection button."""
    return True


def resolve_base_url(host_row: dict, chip: dict) -> str:
    """Resolve the upstream base URL for one Speedtest Tracker chip.

    Priority order:
      1. Chip's own ``url`` field (operator-set; includes scheme
         + optional port).
      2. ``<proto>://<host.address>:<chip.probe.ports[0].port>``
         when the chip carries a single http/https port.

    Returns the URL with trailing slashes stripped so the caller
    can append ``/api/v1/...`` directly. Empty string when nothing
    resolves.
    """
    url = (chip.get("url") or "").strip()
    if url:
        return url.rstrip("/")
    address = (host_row.get("address") or "").strip()
    if not address:
        return ""
    probe = chip.get("probe") or {}
    ports = probe.get("ports") or []
    if isinstance(ports, list):
        for p in ports:
            if not isinstance(p, dict):
                continue
            port_n = p.get("port")
            proto = (p.get("protocol") or "").strip().lower()
            if isinstance(port_n, int) and 1 <= port_n <= 65535 and proto in ("http", "https"):
                return f"{proto}://{address}:{port_n}".rstrip("/")
    return ""


async def test_credential(host_row: dict, chip: dict, candidate_key: str) -> dict:
    """Probe Speedtest Tracker's auth-required endpoint with the
    supplied Bearer key. Returns ``{ok, detail, status}`` shaped
    for direct SPA consumption.

    Falls back to the chip's stored ``api_key`` when
    ``candidate_key`` is blank so the operator can re-test after
    first save without re-typing the secret.
    """
    key = (candidate_key or "").strip() or (chip.get("api_key") or "").strip()
    if not key:
        return {"ok": False, "detail": "api_key required", "status": 0}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    url = base + "/api/v1/speedtests/latest"
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as cli:
            r = await cli.get(url, headers=headers)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code in (200, 204):
        return {"ok": True, "detail": "OK", "status": r.status_code}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check api_key)", "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


def _cache_key(host_id: str, service_idx: int) -> str:
    return f"{host_id}:{service_idx}"


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch the upstream's recent results, derive latest +
    averages + series for the expanded-card render.

    Returns ``{latest, averages, series, fetched_at}``. Raises
    ``ValueError`` (caller should map to HTTPException) when the
    chip's api_key is unset / the base URL won't resolve.
    """
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("api_key not set for this instance")
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured for this instance")
    ck = _cache_key(host_id, service_idx)
    now = time.time()
    if not force:
        cached = _data_cache.get(ck)
        if cached and (now - cached[0]) < CACHE_TTL_S:
            return cached[1]
    list_url = base + "/api/v1/speedtests"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    # Diagnostic: every fetch logs the host + resolved upstream URL so a
    # failure (404 / auth / timeout) is traceable to a specific host +
    # base URL in stdout / Admin -> Logs WITHOUT exposing the api_key.
    # Operator-flagged: "Speedtest fetch failed: upstream returned HTTP
    # 404 — why no log to tell us which host or the nature of the error?"
    # Severity convention: lines carry an explicit INFO / warning: /
    # error: marker near the start so `logic/logs.py:_severity_for`
    # buckets them deterministically (operator-requested consistent
    # logging) rather than relying on incidental body keywords. The
    # api_key is NEVER logged.
    print(f"[speedtest] INFO fetch host={host_id} svc_idx={service_idx} url={list_url}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0) as cli:
            r = await cli.get(list_url, headers=headers, params={"perPage": 30})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[speedtest] error: fetch host={host_id} url={list_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        # 404 is almost always a base-URL / path issue: the chip's `url`
        # points at the host but not the Speedtest Tracker root (e.g. a
        # reverse-proxy sub-path, or http-vs-https, or a trailing
        # `/dashboard`). Log the full URL + status so the operator can
        # curl it directly. `request.url` carries the final URL incl. the
        # perPage query so it's copy-pasteable.
        print(f"[speedtest] error: fetch host={host_id} url={r.request.url} "
              f"returned HTTP {r.status_code} "
              f"(check the chip URL points at the Speedtest Tracker root, "
              f"e.g. https://speedtest.example.com — not a sub-page)")
        if r.status_code in (401, 403):
            raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                               f"(check api_key) — {list_url}")
        if r.status_code == 404:
            raise RuntimeError(f"upstream returned HTTP 404 for {list_url} — "
                               f"the chip URL may not point at the Speedtest "
                               f"Tracker root (no /api/v1/speedtests there)")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {list_url}")
    try:
        body = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    # Speedtest Tracker v1.x response shape:
    # {"data": [{id, ping, download, upload, service_name, server_name,
    #            status, scheduled, created_at, ...}, ...]}
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        rows = []
    series: list[dict[str, Any]] = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        try:
            download = float(entry.get("download") or 0)
            upload = float(entry.get("upload") or 0)
            ping = float(entry.get("ping") or 0)
        except (TypeError, ValueError):
            continue
        ts_str = (entry.get("created_at") or entry.get("scheduled") or "").strip()
        series.append({
            "ts": ts_str,
            "download": download,
            "upload": upload,
            "ping": ping,
            "status": (entry.get("status") or "").strip(),
            "server": (entry.get("server_name") or "").strip(),
        })
    # Latest = first row (Speedtest Tracker returns newest-first).
    latest: Optional[dict[str, Any]] = series[0] if series else None
    # Averages over the last 10 points — windowed so a single
    # anomalous spike doesn't dominate the badge.
    sample = series[:10]
    n = len(sample)
    if n > 0:
        avg = {
            "download": sum(p["download"] for p in sample) / n,
            "upload": sum(p["upload"] for p in sample) / n,
            "ping": sum(p["ping"] for p in sample) / n,
            "sample_size": n,
        }
    else:
        avg = {"download": 0.0, "upload": 0.0, "ping": 0.0, "sample_size": 0}
    # SPA chart expects oldest-first so the line walks left → right.
    series.reverse()
    out: dict = {
        "latest": latest,
        "averages": avg,
        "series": series,
        "fetched_at": int(now),
    }
    _data_cache[ck] = (now, out)
    return out
