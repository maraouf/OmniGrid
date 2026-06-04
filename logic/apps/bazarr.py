"""Bazarr per-app module.

Encapsulates everything Bazarr-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``speedtest_tracker.py`` shape:

    SLUGS               — catalog slugs this module handles.
    requires_api_key()  — True (Bazarr authenticates via X-API-KEY).
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — one read-only "Bazarr status" AI skill.

Bazarr is a subtitle manager (companion to Sonarr / Radarr). The single
most impactful, lowest-cost endpoint is ``GET /api/badges`` — it returns
exactly the actionable counts Bazarr surfaces in its own nav badges:

    episodes   — TV episodes still MISSING subtitles (wanted)
    movies     — movies still MISSING subtitles (wanted)
    providers  — subtitle providers currently THROTTLED / rate-limited
    status     — active health issues

so the expanded card answers "how much is still missing subtitles, and is
anything wrong" at a glance. ``GET /api/system/status`` adds the Bazarr
version (one extra, tolerated-on-failure call).

Auth model: every Bazarr API endpoint requires the ``X-API-KEY`` header
(NOT Bearer). The key is the value from Bazarr's Settings → General → API
key. Single-instance app (NOT fleet) — one card per pinned chip.

Upstream API reference: <bazarr-host>/api/ (Swagger). Endpoints used:
    GET /api/system/status  — test-credential probe + version
    GET /api/badges         — the missing-subtitle / health counts
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_cache_ttl,
    resolve_credential_target)
from logic.coerce import safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("bazarr",)

# Read-only AI / drawer skill — lets the AI (sidebar + Telegram) answer
# "how many subtitles are missing on Bazarr?" by fetching live counts.
# Non-destructive (a read), gated on the api_key being set.
SKILLS: tuple[dict, ...] = (
    {
        "id": "bazarr_status",
        "name": "Bazarr status",
        "ai_phrases": ("bazarr status, how many subtitles are missing, "
                       "missing subtitles, subtitle backlog, how many episodes "
                       "missing subtitles, how many movies missing subtitles, "
                       "bazarr health, throttled subtitle providers"),
        "destructive": False,
    },
)

# Per-(host_id, service_idx) data cache. Default TTL overridable per chip
# via the editor's `cache_ttl` field (resolve_cache_ttl). 30s default —
# the badge counts move slowly (a subtitle search runs on a schedule).
DEFAULT_CACHE_TTL_S = 30
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Bazarr authenticates every API endpoint via X-API-KEY; the editor
    MUST render the api_key input + Test-connection button."""
    return True


def _headers(key: str) -> dict:
    return {"X-API-KEY": key, "Accept": "application/json"}


def _version_from(resp) -> str:
    """Extract ``data.bazarr_version`` from an ``/api/system/status``
    response. Returns ``""`` on any non-200 / parse failure (version is
    always a nice-to-have, never load-bearing)."""
    try:
        if getattr(resp, "status_code", 0) != 200:
            return ""
        data = (resp.json() or {}).get("data") or {}
        return str(data.get("bazarr_version") or "").strip()
    except (ValueError, TypeError, AttributeError):
        return ""


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Bazarr's auth-required ``/api/system/status`` with the supplied
    X-API-KEY. Returns ``{ok, detail, status}`` for direct SPA consumption.
    Falls back to the chip's stored ``api_key`` when ``candidate_key`` is
    blank so the operator can re-test after first save without retyping."""
    key, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + "/api/system/status"
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        ver = _version_from(r)
        return {"ok": True, "detail": f"OK (Bazarr {ver})" if ver else "OK",
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check api_key)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Bazarr's badge counts (+ version) for the expanded card.

    Returns ``{available, episodes_missing, movies_missing,
    providers_throttled, health_issues, version, fetched_at}``. Raises
    ``ValueError`` / ``RuntimeError`` (caller maps to HTTPException) when
    the chip's api_key is unset / the base URL won't resolve / the upstream
    errors."""
    api_key = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=api_key, log_tag="bazarr")
    if hit is not None:
        return hit
    badges_url = base + "/api/badges"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(badges_url, headers=_headers(api_key))
            # Version is a nice-to-have; a failure here must NOT fail the card.
            ver = ""
            try:
                ver = _version_from(await cli.get(base + "/api/system/status",
                                                  headers=_headers(api_key)))
            except (httpx.HTTPError, OSError):
                ver = ""
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[bazarr] error: fetch host={host_id} url={badges_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        print(f"[bazarr] error: fetch host={host_id} url={r.request.url} "
              f"returned HTTP {r.status_code} (check the chip URL points at "
              f"the Bazarr root, e.g. https://bazarr.example.com)")
        if r.status_code in (401, 403):
            raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                               f"(check api_key) — {badges_url}")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {badges_url}")
    try:
        body = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    if not isinstance(body, dict):
        body = {}
    out: dict[str, Any] = {
        "available": True,
        "episodes_missing": safe_int(body.get("episodes")),
        "movies_missing": safe_int(body.get("movies")),
        "providers_throttled": safe_int(body.get("providers")),
        "health_issues": safe_int(body.get("status")),
        "version": ver,
        "fetched_at": int(now),
    }
    print(f"[bazarr] INFO fetched host={host_id} episodes_missing="
          f"{out['episodes_missing']} movies_missing={out['movies_missing']} "
          f"throttled={out['providers_throttled']} health={out['health_issues']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``. Returns the last fetched badge counts or
    ``None`` when nothing is cached yet."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "episodes_missing": safe_int(data.get("episodes_missing")),
        "movies_missing": safe_int(data.get("movies_missing")),
        "providers_throttled": safe_int(data.get("providers_throttled")),
        "health_issues": safe_int(data.get("health_issues")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404)."""
    if skill_id == "bazarr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    raise ValueError(f"unknown skill: {skill_id!r}")


async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only skill: live-fetch the current badge counts (force-bypasses
    the cache) and return a formatted ``detail`` for the AI / drawer. Never
    raises — upstream / config failures come back as ``{ok: False, detail}``."""
    print(f"[bazarr] INFO bazarr_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[bazarr] warning: bazarr_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    em = safe_int(data.get("episodes_missing"))
    mm = safe_int(data.get("movies_missing"))
    thr = safe_int(data.get("providers_throttled"))
    hi = safe_int(data.get("health_issues"))
    lines = [
        f"📺 Episodes missing subtitles: {em:,}",
        f"🎬 Movies missing subtitles: {mm:,}",
    ]
    if thr:
        lines.append(f"⏳ Throttled providers: {thr:,}")
    lines.append(f"{'⚠️' if hi else '✅'} Health issues: {hi:,}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "episodes_missing": em,
        "movies_missing": mm,
        "providers_throttled": thr,
        "health_issues": hi,
    }
