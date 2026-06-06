"""Radarr per-app module.

Encapsulates everything Radarr-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``bazarr.py`` / ``seerr.py`` shape:

    SLUGS               — catalog slugs this module handles ("radarr").
    requires_api_key()  — True (Radarr authenticates via the X-Api-Key header).
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read-only) + search-missing + refresh
                          (both non-destructive background commands).

Radarr is a movie collection manager (the *arr companion to Sonarr).
The expanded card answers "how big is the library, how much is missing,
what's downloading, and is the disk OK" at a glance:

    movies_total   — every movie in the library  (GET /api/v3/movie)
    monitored      — movies Radarr is actively managing
    missing        — monitored movies with no file on disk yet
    queue          — items currently downloading  (GET /api/v3/queue/status)
    disk_free_gb   — free space on the largest library disk (GET /api/v3/diskspace)
    health_issues  — active health warnings        (GET /api/v3/health)
    version        — Radarr version                (GET /api/v3/system/status)

AI / Telegram skills
--------------------
* ``radarr_status``          — read-only library summary (live fetch).
* ``radarr_search_missing``  — trigger a search for every monitored
  missing movie (POST /api/v3/command {name: MissingMoviesSearch}).
* ``radarr_refresh``         — refresh + disk-scan the whole library
  (POST /api/v3/command {name: RefreshMovie}).
Both command skills are NON-destructive — they queue a background task
on Radarr (nothing is deleted), so no typed-confirm is required.

Auth model: every authenticated Radarr v3 endpoint takes the
``X-Api-Key`` header (the value from Radarr → Settings → General → API
Key). The credential probe hits the auth-required
``/api/v3/system/status`` so a bad key fails loudly. Single-instance app
(NOT fleet) — one card per pinned chip.

Upstream API reference: <radarr-host>/api/v3 (Swagger at /api). Endpoints:
    GET  /api/v3/system/status   — version (test-credential probe + footnote)
    GET  /api/v3/movie           — library list (total / monitored / missing)
    GET  /api/v3/queue/status    — downloading count
    GET  /api/v3/diskspace       — per-mount free / total bytes
    GET  /api/v3/health          — active health issues
    POST /api/v3/command         — MissingMoviesSearch / RefreshMovie
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl,
    resolve_credential_target)
from logic.coerce import safe_float, safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("radarr",)

# Read-only status skill + two non-destructive background-command skills.
# None take a free-form arg (they act on the whole library), so all three
# surface as one-click drawer buttons AND AI / Telegram actions.
SKILLS: tuple[dict, ...] = (
    {
        "id": "radarr_status",
        "name": "Radarr status",
        "ai_phrases": ("radarr status, movie library, how many movies, "
                       "how many movies are missing, missing movies, "
                       "what's downloading on radarr, radarr queue, "
                       "radarr health, movie collection size, disk space radarr"),
        "destructive": False,
    },
    {
        "id": "radarr_search_missing",
        "name": "Search for missing movies",
        "ai_phrases": ("search for missing movies, find missing movies, "
                       "search radarr for missing, download missing movies, "
                       "grab missing movies, look for missing movies"),
        "destructive": False,
    },
    {
        "id": "radarr_refresh",
        "name": "Refresh movie library",
        "ai_phrases": ("refresh radarr, rescan the movie library, refresh "
                       "movies, update radarr library, rescan radarr, "
                       "refresh and scan movies"),
        "destructive": False,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. Default TTL
# overridable per chip via the editor's `cache_ttl` field. 60s default —
# the movie-library list is the heaviest call and changes slowly, so a
# longer cache window than the badge-style apps keeps the fetch light.
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# 1 GiB in bytes — Radarr reports disk space in bytes; the card shows GiB
# (matching Radarr's own UI).
_GIB = 1024 ** 3


def requires_api_key() -> bool:
    """Radarr authenticates every v3 endpoint via X-Api-Key; the editor
    MUST render the api_key input + Test-connection button."""
    return True


def _headers(key: str) -> dict:
    return {"X-Api-Key": key, "Accept": "application/json"}


def _version_from(resp) -> str:
    """Extract ``version`` from a ``/api/v3/system/status`` response.
    Returns ``""`` on any non-200 / parse failure (version is a
    nice-to-have, never load-bearing)."""
    try:
        if getattr(resp, "status_code", 0) != 200:
            return ""
        body = resp.json() or {}
        return str(body.get("version") or "").strip()
    except (ValueError, TypeError, AttributeError):
        return ""


async def _fetch_version(cli: httpx.AsyncClient, base: str, key: str) -> str:
    """Best-effort Radarr version via ``GET /api/v3/system/status`` on an
    already-open client — shared by the credential probe + the card fetch.
    ``''`` on any failure (version is never load-bearing)."""
    try:
        return _version_from(await cli.get(base + "/api/v3/system/status",
                                           headers=_headers(key)))
    except (httpx.HTTPError, OSError):
        return ""


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Radarr's auth-required ``/api/v3/system/status`` with the
    supplied X-Api-Key. Returns ``{ok, detail, status}`` for direct SPA
    consumption. Falls back to the chip's stored ``api_key`` when
    ``candidate_key`` is blank so the operator can re-test after first save
    without retyping."""
    key, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + "/api/v3/system/status"
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        ver = _version_from(r)
        return {"ok": True, "detail": f"OK (Radarr {ver})" if ver else "OK",
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check api_key)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


def _disk_free_total_gib(raw: Any) -> "tuple[float, float]":
    """From a ``/api/v3/diskspace`` payload (list of
    ``{path, freeSpace, totalSpace}``), pick the disk with the LARGEST
    total space (the library volume) and return ``(free_gib, total_gib)``.
    ``(0.0, 0.0)`` on an empty / malformed payload."""
    if not isinstance(raw, list):
        return 0.0, 0.0
    best_total = -1.0
    best_free = 0.0
    for d in raw:
        if not isinstance(d, dict):
            continue
        total = safe_float(d.get("totalSpace"))
        if total > best_total:
            best_total = total
            best_free = safe_float(d.get("freeSpace"))
    if best_total < 0:
        return 0.0, 0.0
    return round(best_free / _GIB, 1), round(best_total / _GIB, 1)


# noinspection DuplicatedCode
# The upstream-error guard + JSON-parse block below is structurally shared
# with every other per-app module's fetch_data (bazarr / seerr / …) — the
# deliberate per-app encapsulation pattern (CLAUDE.md). The content differs
# (app name, endpoint, fields), so it stays inline rather than coupling the
# modules through a parameterised _common helper.
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Radarr's library summary for the expanded card.

    Returns ``{available, movies_total, monitored, missing, queue,
    disk_free_gb, disk_total_gb, health_issues, version, fetched_at}``.
    Raises ``ValueError`` / ``RuntimeError`` (caller maps to HTTPException)
    when the chip's api_key is unset / the base URL won't resolve / the
    primary upstream call errors. The library list is the load-bearing
    call; queue / disk / health / version are tolerated-on-failure."""
    api_key = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=api_key, log_tag="radarr")
    if hit is not None:
        return hit
    movies_url = base + "/api/v3/movie"
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(movies_url, headers=_headers(api_key))
            # queue / disk / health / version are nice-to-haves; a failure
            # on any of them must NOT fail the card.
            queue = 0
            try:
                qr = await cli.get(base + "/api/v3/queue/status",
                                   headers=_headers(api_key))
                if qr.status_code == 200:
                    queue = safe_int((qr.json() or {}).get("totalCount"))
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                queue = 0
            disk_free_gb = disk_total_gb = 0.0
            try:
                dr = await cli.get(base + "/api/v3/diskspace",
                                   headers=_headers(api_key))
                if dr.status_code == 200:
                    disk_free_gb, disk_total_gb = _disk_free_total_gib(dr.json())
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                disk_free_gb = disk_total_gb = 0.0
            health_issues = 0
            try:
                hr = await cli.get(base + "/api/v3/health",
                                   headers=_headers(api_key))
                if hr.status_code == 200:
                    _hj = hr.json()
                    health_issues = len(_hj) if isinstance(_hj, list) else 0
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                health_issues = 0
            ver = await _fetch_version(cli, base, api_key)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[radarr] error: fetch host={host_id} url={movies_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        print(f"[radarr] error: fetch host={host_id} url={r.request.url} "
              f"returned HTTP {r.status_code} (check the chip URL points at "
              f"the Radarr root, e.g. https://radarr.example.com)")
        if r.status_code in (401, 403):
            raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                               f"(check api_key) — {movies_url}")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {movies_url}")
    try:
        movies = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    if not isinstance(movies, list):
        movies = []
    total = len(movies)
    monitored = 0
    missing = 0
    for m in movies:
        if not isinstance(m, dict):
            continue
        is_monitored = bool(m.get("monitored"))
        if is_monitored:
            monitored += 1
            if not m.get("hasFile"):
                missing += 1
    out: dict[str, Any] = {
        "available": True,
        "movies_total": total,
        "monitored": monitored,
        "missing": missing,
        "queue": safe_int(queue),
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
        "health_issues": safe_int(health_issues),
        "version": ver,
        "fetched_at": int(now),
    }
    print(f"[radarr] INFO fetched host={host_id} movies={total} "
          f"monitored={monitored} missing={missing} queue={out['queue']} "
          f"disk_free_gb={disk_free_gb} health={out['health_issues']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``. Returns the last fetched library summary or
    ``None`` when nothing is cached yet."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "movies_total": safe_int(data.get("movies_total")),
        "monitored": safe_int(data.get("monitored")),
        "missing": safe_int(data.get("missing")),
        "queue": safe_int(data.get("queue")),
        "disk_free_gb": safe_float(data.get("disk_free_gb")),
        "health_issues": safe_int(data.get("health_issues")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404)."""
    if skill_id == "radarr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "radarr_search_missing":
        return await _command_skill(host_row, chip, command="MissingMoviesSearch",
                                    started_msg="🔍 Started a search for all monitored "
                                                "missing movies on Radarr.",
                                    host_id=host_id)
    if skill_id == "radarr_refresh":
        return await _command_skill(host_row, chip, command="RefreshMovie",
                                    started_msg="🔄 Started a library refresh & disk "
                                                "scan on Radarr.",
                                    host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


# noinspection DuplicatedCode
# The force-fetch-then-format shape is shared with every per-app module's
# status skill (bazarr / seerr / …) — the deliberate per-app encapsulation
# pattern (CLAUDE.md). The formatted output is app-specific, so it stays
# inline rather than being factored into a shared helper.
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only skill: live-fetch the current library summary
    (force-bypasses the cache) and return a formatted ``detail`` for the AI
    / drawer. Never raises — upstream / config failures come back as
    ``{ok: False, detail}``."""
    print(f"[radarr] INFO radarr_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[radarr] warning: radarr_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    total = safe_int(data.get("movies_total"))
    monitored = safe_int(data.get("monitored"))
    missing = safe_int(data.get("missing"))
    queue = safe_int(data.get("queue"))
    free_gb = safe_float(data.get("disk_free_gb"))
    health = safe_int(data.get("health_issues"))
    lines = [
        f"🎬 Movies: {total:,}",
        f"📁 Monitored: {monitored:,}",
        f"{'❓' if missing else '✅'} Missing: {missing:,}",
        f"⬇️ Downloading: {queue:,}",
    ]
    if free_gb > 0:
        lines.append(f"💾 Disk free: {free_gb:,.1f} GB")
    lines.append(f"{'⚠️' if health else '✅'} Health issues: {health:,}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "movies_total": total, "monitored": monitored, "missing": missing,
        "queue": queue, "disk_free_gb": free_gb, "health_issues": health,
    }


async def _command_skill(host_row: dict, chip: dict, *, command: str,
                         started_msg: str,
                         host_id: Optional[str] = None) -> dict:
    """Action skill: POST a non-destructive background command to Radarr's
    ``/api/v3/command`` endpoint (e.g. ``MissingMoviesSearch`` /
    ``RefreshMovie``). Never raises — every failure comes back as
    ``{ok: False, detail}``."""
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        return {"ok": False, "status": 0, "detail": "Radarr api_key not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    print(f"[radarr] INFO command host={host_id} name={command!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.post(base + "/api/v3/command",
                               headers=_headers(api_key),
                               json={"name": command})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[radarr] warning: command {command!r} failed — {type(e).__name__}: {e}")
        return {"ok": False, "status": 0,
                "detail": f"command failed: {type(e).__name__}: {e}"}
    if r.status_code in (200, 201):
        return {"ok": True, "status": r.status_code, "detail": started_msg}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check Radarr api_key)"}
    _body = ""
    try:
        _body = (r.text or "")[:160]
    except (ValueError, TypeError):
        _body = ""
    return {"ok": False, "status": r.status_code,
            "detail": f"Radarr returned HTTP {r.status_code} for {command}"
                      + (f" — {_body}" if _body else "")}
