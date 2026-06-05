"""Seerr (Overseerr / Jellyseerr) per-app module.

Encapsulates everything Seerr-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``bazarr.py`` shape:

    SLUGS               — catalog slugs this module handles ("seerr").
    requires_api_key()  — True (Seerr authenticates via the X-Api-Key header).
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read-only) + suggest-a-movie (read-only)
                          + request-a-movie (takes a free-form ``arg``).

Seerr (Overseerr or its Jellyfin fork Jellyseerr) is a media-request
manager in front of Sonarr / Radarr. The most impactful, lowest-cost
endpoint is ``GET /api/v1/request/count`` — it returns the request queue
breakdown Seerr shows on its own dashboard:

    pending     — requests awaiting approval
    approved    — approved, handed to Radarr/Sonarr
    processing  — currently downloading
    available   — fulfilled (now in the library)
    declined    — rejected
    total       — all requests

``GET /api/v1/issue/count`` adds the open-issue count (reported problems);
``GET /api/v1/status`` adds the Seerr version (both tolerated-on-failure).

AI / Telegram skills (the headline feature)
-------------------------------------------
* ``seerr_status``         — read-only request-queue summary.
* ``seerr_request_movie``  — request a movie BY TITLE (or by a numeric
  TMDB id). Resolves the title via Seerr's own ``/api/v1/search`` (which
  is TMDB-backed), picks the top movie hit, and POSTs ``/api/v1/request``.
  Takes a free-form ``arg`` (the title / id) threaded from the AI's
  ``ACTION_DATA`` / the Telegram slash command.
* ``seerr_suggest_movie``  — suggest a RANDOM movie the user can then
  request. Pulls from TMDB directly when a TMDB API key is configured on
  the chip (genuine variety via ``/discover/movie`` on a random page),
  else falls back to Seerr's own ``/api/v1/discover/movies``. Returns a
  poster image URL (built from the configured TMDB image base) so the
  chat can preview it.

Auth model: every authenticated Seerr endpoint takes the ``X-Api-Key``
header (Settings → General → API Key in Seerr). ``/api/v1/status`` is
unauthenticated; the credential probe hits the auth-required
``/api/v1/request/count`` so a bad key fails loudly. Single-instance app
(NOT fleet) — one card per pinned chip.

TMDB config is per-chip (so the app stays self-contained — no global
settings surface): ``tmdb_api_key`` (secret), ``tmdb_base_url`` (default
``https://api.themoviedb.org``), ``tmdb_image_base_url`` (default
``https://image.tmdb.org/t/p``). A classic v3 key is sent as the
``api_key`` query param; a v4 read-access token (JWT, starts ``eyJ``) is
sent as ``Authorization: Bearer``.

Upstream API reference: <seerr-host>/api-docs. Endpoints used:
    GET  /api/v1/status                       — version (test + card footnote)
    GET  /api/v1/request/count                — queue breakdown (credential probe + card)
    GET  /api/v1/issue/count                   — open-issue count (tolerated)
    GET  /api/v1/search?query=<t>              — TMDB-backed title search
    GET  /api/v1/discover/movies?page=<n>      — discover fallback for suggestions
    POST /api/v1/request                       — create a media request
    GET  {tmdb}/3/discover/movie               — random-movie suggestions
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_cache_ttl,
    resolve_credential_target)
from logic.coerce import safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("seerr",)

# Read-only AI / drawer skills + the request-a-movie action skill.
# ``arg``-carrying skills declare ``arg: True`` (+ an ``arg_hint``) so the
# prompt layer tells the model to supply the free-form argument and the
# dispatch surfaces thread it through to ``run_skill``.
SKILLS: tuple[dict, ...] = (
    {
        "id": "seerr_status",
        "name": "Seerr status",
        "ai_phrases": ("seerr status, overseerr status, jellyseerr status, "
                       "media requests, how many requests are pending, "
                       "request queue, what's downloading, pending approvals, "
                       "how many movies are available"),
        "destructive": False,
    },
    {
        "id": "seerr_request_movie",
        "name": "Request a movie",
        "ai_phrases": ("request a movie, request <title>, add <title> to "
                       "the library, can you get <title>, download <title>, "
                       "ask seerr for <title>, request the movie <title>, "
                       "i want to watch <title>"),
        "destructive": False,
        "arg": True,
        "arg_hint": "the movie title (or a numeric TMDB id)",
    },
    {
        "id": "seerr_suggest_movie",
        "name": "Suggest a movie",
        "ai_phrases": ("suggest a movie, recommend a movie, what should i "
                       "watch, random movie, pick a movie for me, suggest "
                       "something to watch, give me a movie recommendation"),
        "destructive": False,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. Default TTL
# overridable per chip via the editor's `cache_ttl` field. 30s default —
# the request counts move slowly.
DEFAULT_CACHE_TTL_S = 30
_data_cache: dict[str, tuple[float, dict]] = {}

# TMDB defaults — used when the chip leaves the field blank. The API base
# conventionally includes the ``/3`` version segment (matching the value
# operators copy from other projects, e.g. `https://api.themoviedb.org/3`).
# `_tmdb_api_url` is tolerant of EITHER form (with or without the trailing
# `/3`) so a host-root base works too. The image base + a width segment
# build a poster URL: `{image_base}/w500{path}`.
_TMDB_BASE_DEFAULT = "https://api.themoviedb.org/3"
_TMDB_IMAGE_BASE_DEFAULT = "https://image.tmdb.org/t/p"
_TMDB_POSTER_SIZE = "w500"
# Random-page ceiling for the discover suggestion — pages past this start
# returning long-tail obscure titles; 15 keeps suggestions watchable.
_TMDB_DISCOVER_MAX_PAGE = 15


def requires_api_key() -> bool:
    """Seerr authenticates every data endpoint via X-Api-Key; the editor
    MUST render the api_key input + Test-connection button."""
    return True


def _headers(key: str) -> dict:
    return {"X-Api-Key": key, "Accept": "application/json"}


def _tmdb_cfg(chip: dict) -> "tuple[str, str, str]":
    """Resolve the chip's TMDB config: ``(api_key, base_url, image_base)``.
    Blank fields fall back to the public TMDB defaults so suggestions work
    out of the box once the operator pastes only the API key."""
    chip = chip if isinstance(chip, dict) else {}
    key = (chip.get("tmdb_api_key") or "").strip()
    base = (chip.get("tmdb_base_url") or "").strip().rstrip("/") or _TMDB_BASE_DEFAULT
    img = (chip.get("tmdb_image_base_url") or "").strip().rstrip("/") or _TMDB_IMAGE_BASE_DEFAULT
    return key, base, img


def _tmdb_auth(key: str) -> "tuple[dict, dict]":
    """Split a TMDB key into ``(headers, query_params)``. A v4 read-access
    token is a JWT (starts ``eyJ``) sent as a Bearer header; a classic v3
    key goes in the ``api_key`` query param."""
    if key.startswith("eyJ"):
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}, {}
    return {"Accept": "application/json"}, {"api_key": key}


def _tmdb_api_url(tmdb_base: str, path: str) -> str:
    """Build a TMDB v3 API URL from the configured base + an endpoint
    ``path`` (without a leading slash, e.g. ``"discover/movie"``).

    Tolerant of BOTH base conventions: operators paste either the host
    root (``https://api.themoviedb.org``) OR the version-qualified base
    (``https://api.themoviedb.org/3``). We strip a trailing ``/3`` if
    present, then re-append exactly one ``/3/`` so the result is always
    ``…/3/<path>`` — never a doubled ``/3/3/``."""
    b = (tmdb_base or "").strip().rstrip("/")
    if b.endswith("/3"):
        b = b[:-2].rstrip("/")
    return f"{b}/3/{path.lstrip('/')}"


def _poster_url(image_base: str, poster_path: str) -> str:
    """Build a full poster URL from the configured image base + the
    relative ``poster_path`` TMDB / Seerr return. Empty when no path."""
    p = (poster_path or "").strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    return f"{image_base.rstrip('/')}/{_TMDB_POSTER_SIZE}{p}"


def _year_of(date_str: str) -> str:
    """Extract the 4-digit year from a ``YYYY-MM-DD`` release date; ''
    when absent / malformed."""
    s = (date_str or "").strip()
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else ""


def _version_from(resp) -> str:
    """Extract ``version`` from a ``/api/v1/status`` response. Returns ''
    on any non-200 / parse failure (version is never load-bearing)."""
    try:
        if getattr(resp, "status_code", 0) != 200:
            return ""
        body = resp.json() or {}
        return str(body.get("version") or "").strip()
    except (ValueError, TypeError, AttributeError):
        return ""


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Seerr's auth-required ``/api/v1/request/count`` with the
    supplied X-Api-Key. Returns ``{ok, detail, status}`` for direct SPA
    consumption. Falls back to the chip's stored ``api_key`` when
    ``candidate_key`` is blank so the operator can re-test after first
    save without retyping."""
    key, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + "/api/v1/request/count"
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(key))
            ver = ""
            try:
                ver = _version_from(await cli.get(base + "/api/v1/status",
                                                  headers=_headers(key)))
            except (httpx.HTTPError, OSError):
                ver = ""
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        return {"ok": True, "detail": f"OK (Seerr {ver})" if ver else "OK",
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check api_key)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Seerr's request-queue counts (+ open issues + version) for the
    expanded card.

    Returns ``{available, total, pending, approved, processing,
    available_count, declined, issues_open, version, fetched_at}``. Raises
    ``ValueError`` / ``RuntimeError`` (caller maps to HTTPException) when
    the chip's api_key is unset / the base URL won't resolve / the upstream
    errors."""
    api_key = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=api_key, log_tag="seerr")
    if hit is not None:
        return hit
    count_url = base + "/api/v1/request/count"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(count_url, headers=_headers(api_key))
            # Open-issue count + version are nice-to-haves; a failure on
            # either must NOT fail the card.
            issues_open = 0
            try:
                ir = await cli.get(base + "/api/v1/issue/count",
                                   headers=_headers(api_key))
                if ir.status_code == 200:
                    issues_open = safe_int((ir.json() or {}).get("open"))
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                issues_open = 0
            ver = ""
            try:
                ver = _version_from(await cli.get(base + "/api/v1/status",
                                                  headers=_headers(api_key)))
            except (httpx.HTTPError, OSError):
                ver = ""
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[seerr] error: fetch host={host_id} url={count_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        print(f"[seerr] error: fetch host={host_id} url={r.request.url} "
              f"returned HTTP {r.status_code} (check the chip URL points at "
              f"the Seerr root, e.g. https://requests.example.com)")
        if r.status_code in (401, 403):
            raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                               f"(check api_key) — {count_url}")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {count_url}")
    try:
        body = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    if not isinstance(body, dict):
        body = {}
    out: dict[str, Any] = {
        "available": True,
        "total": safe_int(body.get("total")),
        "pending": safe_int(body.get("pending")),
        "approved": safe_int(body.get("approved")),
        "processing": safe_int(body.get("processing")),
        "available_count": safe_int(body.get("available")),
        "declined": safe_int(body.get("declined")),
        "issues_open": safe_int(issues_open),
        "version": ver,
        "fetched_at": int(now),
    }
    print(f"[seerr] INFO fetched host={host_id} pending={out['pending']} "
          f"approved={out['approved']} processing={out['processing']} "
          f"available={out['available_count']} issues={out['issues_open']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``. Returns the last fetched queue counts or
    ``None`` when nothing is cached yet."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "pending": safe_int(data.get("pending")),
        "approved": safe_int(data.get("approved")),
        "processing": safe_int(data.get("processing")),
        "available_count": safe_int(data.get("available_count")),
        "total": safe_int(data.get("total")),
        "issues_open": safe_int(data.get("issues_open")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``
    (+ ``image_url`` for the suggest skill). Raises ValueError on an unknown
    skill id (route maps to HTTP 404). ``arg`` carries the free-form
    argument (movie title / TMDB id) for ``seerr_request_movie``."""
    if skill_id == "seerr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "seerr_request_movie":
        return await _request_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "seerr_suggest_movie":
        return await _suggest_skill(host_row, chip, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only skill: live-fetch the current request-queue counts
    (force-bypasses the cache) and return a formatted ``detail``. Never
    raises — upstream / config failures come back as ``{ok: False, detail}``."""
    print(f"[seerr] INFO seerr_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[seerr] warning: seerr_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    pend = safe_int(data.get("pending"))
    appr = safe_int(data.get("approved"))
    proc = safe_int(data.get("processing"))
    avail = safe_int(data.get("available_count"))
    total = safe_int(data.get("total"))
    issues = safe_int(data.get("issues_open"))
    lines = [
        f"⏳ Pending approval: {pend:,}",
        f"✅ Approved: {appr:,}",
        f"⬇️ Processing: {proc:,}",
        f"🎬 Available: {avail:,}",
        f"📋 Total requests: {total:,}",
    ]
    if issues:
        lines.append(f"⚠️ Open issues: {issues:,}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "pending": pend, "approved": appr, "processing": proc,
        "available_count": avail, "total": total, "issues_open": issues,
    }


async def _seerr_search_movie(base: str, api_key: str, query: str) -> Optional[dict]:
    """Resolve a movie by title via Seerr's TMDB-backed search. Returns the
    top ``mediaType == 'movie'`` result as ``{id, title, year, overview,
    poster_path, status}`` or ``None`` when nothing matches / the call
    fails."""
    url = base + "/api/v1/search"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(api_key),
                              params={"query": query, "page": 1})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[seerr] warning: search failed for {query!r} — {type(e).__name__}: {e}")
        return None
    if r.status_code != 200:
        print(f"[seerr] warning: search HTTP {r.status_code} for {query!r}")
        return None
    try:
        results = (r.json() or {}).get("results") or []
    except (ValueError, TypeError):
        return None
    for item in results:
        if not isinstance(item, dict):
            continue
        if (item.get("mediaType") or "").strip().lower() != "movie":
            continue
        media_info = item.get("mediaInfo") if isinstance(item.get("mediaInfo"), dict) else {}
        return {
            "id": safe_int(item.get("id")),
            "title": str(item.get("title") or item.get("originalTitle") or "").strip(),
            "year": _year_of(str(item.get("releaseDate") or "")),
            "overview": str(item.get("overview") or "").strip(),
            "poster_path": str(item.get("posterPath") or "").strip(),
            "status": safe_int((media_info or {}).get("status")),
        }
    return None


# Seerr `mediaInfo.status` enum (1 unknown · 2 pending · 3 processing ·
# 4 partially available · 5 available). 3+ means "already in the pipeline".
_SEERR_STATUS_LABEL = {
    2: "already requested (pending)",
    3: "already processing",
    4: "partially available",
    5: "already available",
}


async def _request_skill(host_row: dict, chip: dict, *,
                         arg: Optional[str] = None,
                         host_id: Optional[str] = None) -> dict:
    """Action skill: request a movie BY TITLE (or numeric TMDB id). Resolves
    the title via Seerr search, then POSTs ``/api/v1/request``. Never raises
    — every failure comes back as ``{ok: False, detail}``. Treats an
    already-requested / available movie as a friendly (ok=True) outcome."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0,
                "detail": "no movie title given — tell me which movie to request"}
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        return {"ok": False, "status": 0, "detail": "Seerr api_key not set"}
    from logic.apps._common import resolve_base_url  # noqa: PLC0415
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    # Numeric arg → request that TMDB id directly (the suggest skill hands
    # the model a tmdb id it can re-use for an exact request). Otherwise
    # search by title.
    title = query
    year = ""
    if query.isdigit():
        tmdb_id = int(query)
        already_status = 0
    else:
        match = await _seerr_search_movie(base, api_key, query)
        if not match or not match.get("id"):
            return {"ok": False, "status": 404,
                    "detail": f"no movie found matching “{query}”"}
        tmdb_id = safe_int(match.get("id"))
        title = match.get("title") or query
        year = match.get("year") or ""
        already_status = safe_int(match.get("status"))
    label = f"{title}" + (f" ({year})" if year else "")
    # Already in the pipeline → friendly no-op instead of a 409 error.
    if already_status in _SEERR_STATUS_LABEL:
        return {"ok": True, "status": 200,
                "detail": f"🎬 {label} is {_SEERR_STATUS_LABEL[already_status]} on Seerr.",
                "tmdb_id": tmdb_id}
    print(f"[seerr] INFO seerr_request_movie host={host_id} title={label!r} "
          f"tmdb_id={tmdb_id}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.post(base + "/api/v1/request",
                               headers=_headers(api_key),
                               json={"mediaType": "movie", "mediaId": tmdb_id})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[seerr] warning: request failed for {label!r} — {type(e).__name__}: {e}")
        return {"ok": False, "status": 0,
                "detail": f"request failed: {type(e).__name__}: {e}"}
    if r.status_code in (200, 201):
        return {"ok": True, "status": r.status_code,
                "detail": f"🎬 Requested {label} on Seerr — it'll start downloading once approved.",
                "tmdb_id": tmdb_id}
    if r.status_code == 409:
        # 409 = the request already exists (race with the status check).
        return {"ok": True, "status": 409,
                "detail": f"🎬 {label} was already requested on Seerr.",
                "tmdb_id": tmdb_id}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check Seerr api_key)"}
    _body = ""
    try:
        _body = (r.text or "")[:160]
    except (ValueError, TypeError):
        _body = ""
    return {"ok": False, "status": r.status_code,
            "detail": f"Seerr returned HTTP {r.status_code} requesting {label}"
                      + (f" — {_body}" if _body else "")}


# How many candidates to library-check per suggestion (bounds the Seerr
# lookups when sourcing from TMDB). One discover page is ~20 movies.
_SUGGEST_CHECK_LIMIT = 14
# Seerr mediaInfo.status >= this = already requested / processing / partially
# available / available — i.e. already in (or on its way into) the library.
_SEERR_IN_LIBRARY_MIN_STATUS = 2


async def _tmdb_candidate_movies(tmdb_key: str, tmdb_base: str,
                                 image_base: str) -> list[dict]:
    """A SHUFFLED list of popular-movie candidates from TMDB's
    ``/discover/movie`` (a random page). Each entry is ``{id, title, year,
    overview, poster_url}``. Returns ``[]`` on failure / no key. The caller
    library-checks these against Seerr and picks the first not already there."""
    if not tmdb_key:
        return []
    headers, params = _tmdb_auth(tmdb_key)
    page = random.randint(1, _TMDB_DISCOVER_MAX_PAGE)
    params = dict(params)
    params.update({"sort_by": "popularity.desc", "vote_count.gte": "150",
                   "include_adult": "false", "page": str(page)})
    url = _tmdb_api_url(tmdb_base, "discover/movie")
    try:
        async with httpx.AsyncClient(verify=True, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=headers, params=params)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[seerr] warning: TMDB discover failed — {type(e).__name__}: {e}")
        return []
    if r.status_code != 200:
        print(f"[seerr] warning: TMDB discover HTTP {r.status_code} (check tmdb_api_key)")
        return []
    try:
        results = (r.json() or {}).get("results") or []
    except (ValueError, TypeError):
        return []
    out = [{
        "id": safe_int(m.get("id")),
        "title": str(m.get("title") or m.get("original_title") or "").strip(),
        "year": _year_of(str(m.get("release_date") or "")),
        "overview": str(m.get("overview") or "").strip(),
        "poster_url": _poster_url(image_base, str(m.get("poster_path") or "")),
    } for m in results if isinstance(m, dict) and m.get("id")]
    random.shuffle(out)
    return out


async def _seerr_movie_status(cli: httpx.AsyncClient, base: str, api_key: str,
                              tmdb_id: int) -> int:
    """Seerr's ``mediaInfo.status`` for a TMDB movie id via
    ``GET /api/v1/movie/<id>`` — ``0`` when the movie isn't in Seerr (no
    mediaInfo) / unknown, ``>=2`` when it's already requested / processing /
    available. Never raises (a lookup failure reads as "not in library", so
    a transient blip just means we might suggest one that's already there —
    the request path then reports it gracefully)."""
    if not tmdb_id:
        return 0
    try:
        r = await cli.get(f"{base}/api/v1/movie/{tmdb_id}",
                          headers=_headers(api_key))
    except (httpx.HTTPError, OSError):
        return 0
    if getattr(r, "status_code", 0) != 200:
        return 0
    try:
        mi = (r.json() or {}).get("mediaInfo")
    except (ValueError, TypeError):
        return 0
    return safe_int(mi.get("status")) if isinstance(mi, dict) else 0


async def _seerr_discover_candidates(base: str, api_key: str,
                                     image_base: str) -> list[dict]:
    """Fallback candidate source when no TMDB key is set — Seerr's own
    ``/api/v1/discover/movies``, whose results ALREADY carry each movie's
    library status via ``mediaInfo`` (so no per-movie lookup needed). Returns
    a SHUFFLED list of ``{id, title, year, overview, poster_url, status}``."""
    page = random.randint(1, _TMDB_DISCOVER_MAX_PAGE)
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/discover/movies",
                              headers=_headers(api_key), params={"page": page})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[seerr] warning: Seerr discover failed — {type(e).__name__}: {e}")
        return []
    if r.status_code != 200:
        return []
    try:
        results = (r.json() or {}).get("results") or []
    except (ValueError, TypeError):
        return []
    out = []
    for m in results:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        mi = m.get("mediaInfo")
        out.append({
            "id": safe_int(m.get("id")),
            "title": str(m.get("title") or m.get("originalTitle") or "").strip(),
            "year": _year_of(str(m.get("releaseDate") or "")),
            "overview": str(m.get("overview") or "").strip(),
            "poster_url": _poster_url(image_base, str(m.get("posterPath") or "")),
            "status": safe_int(mi.get("status")) if isinstance(mi, dict) else 0,
        })
    random.shuffle(out)
    return out


async def _pick_not_in_library(base: str, api_key: str,
                               candidates: list[dict]) -> Optional[dict]:
    """From a candidate list, return the first movie NOT already in Seerr's
    library (``mediaInfo.status < 2``). Candidates that already carry a
    ``status`` key (the Seerr-discover source) are filtered inline; TMDB
    candidates are status-checked against Seerr in PARALLEL (capped at
    ``_SUGGEST_CHECK_LIMIT``). Returns ``None`` when every checked candidate
    is already in the library (caller surfaces an honest "you've got them
    all" message rather than suggesting a dup)."""
    if not candidates:
        return None
    check = candidates[:_SUGGEST_CHECK_LIMIT]
    # Seerr-discover candidates already carry library status — no extra call.
    if all("status" in c for c in check):
        for c in check:
            if safe_int(c.get("status")) < _SEERR_IN_LIBRARY_MIN_STATUS:
                return c
        return None
    # TMDB candidates → can't filter without Seerr. If Seerr isn't reachable
    # we can't check, so suggest the first (the request path still guards).
    if not (base and api_key):
        return check[0]
    async with httpx.AsyncClient(verify=False, timeout=15.0,
                                 follow_redirects=True) as cli:
        statuses = await asyncio.gather(
            *[_seerr_movie_status(cli, base, api_key, safe_int(c.get("id")))
              for c in check],
            return_exceptions=True)
    in_lib = 0
    for c, st in zip(check, statuses):
        stv = st if isinstance(st, int) else 0
        if stv < _SEERR_IN_LIBRARY_MIN_STATUS:
            return c
        in_lib += 1
    print(f"[seerr] INFO suggest: all {in_lib} checked candidates already in "
          f"the Seerr library")
    return None


async def _suggest_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None) -> dict:
    """Read-only skill: suggest a random movie the user can then request —
    and that is NOT already in their Seerr library (requested / processing /
    available are all skipped). Prefers TMDB (when a TMDB key is configured
    on the chip) for genuine variety; falls back to Seerr's own discover
    endpoint. Returns ``{ok, detail, image_url, tmdb_id, title, followup}``
    so the AI / button can offer to request it."""
    api_key = (chip.get("api_key") or "").strip()
    from logic.apps._common import resolve_base_url  # noqa: PLC0415
    base = resolve_base_url(host_row, chip)
    tmdb_key, tmdb_base, image_base = _tmdb_cfg(chip)
    print(f"[seerr] INFO seerr_suggest_movie host={host_id} "
          f"source={'tmdb' if tmdb_key else 'seerr-discover'}")
    candidates: list[dict] = []
    if tmdb_key:
        candidates = await _tmdb_candidate_movies(tmdb_key, tmdb_base, image_base)
    if not candidates and base and api_key:
        candidates = await _seerr_discover_candidates(base, api_key, image_base)
    if not candidates:
        return {"ok": False, "status": 0,
                "detail": "couldn't fetch a suggestion (set a TMDB API key on the "
                          "Seerr app for movie suggestions, or check the Seerr URL)"}
    pick = await _pick_not_in_library(base, api_key, candidates)
    if pick is None:
        # Every checked candidate is already requested / in the library —
        # be honest rather than suggesting a movie they already have.
        return {"ok": True, "status": 200,
                "detail": "🎬 Every popular movie I checked is already requested "
                          "or in your Seerr library — ask again for a fresh batch."}
    if not pick.get("title"):
        return {"ok": False, "status": 0,
                "detail": "couldn't fetch a suggestion (check the Seerr / TMDB config)"}
    title = pick.get("title") or ""
    year = pick.get("year") or ""
    label = title + (f" ({year})" if year else "")
    overview = (pick.get("overview") or "").strip()
    if len(overview) > 300:
        overview = overview[:297].rstrip() + "…"
    tmdb_id = safe_int(pick.get("id"))
    lines = [f"🎬 How about: {label}"]
    if overview:
        lines.append(overview)
    lines.append(f"Say “request {title}” and I'll add it to Seerr. (TMDB id {tmdb_id})")
    poster = pick.get("poster_url") or ""
    if poster:
        lines.append(poster)
    return {
        "ok": True,
        "status": 200,
        "detail": "\n".join(lines),
        "image_url": poster,
        "tmdb_id": tmdb_id,
        "title": title,
        "year": year,
        # Follow-up action the UI can offer as a one-click button (the AI
        # sidebar renders it after a suggestion). Requesting by the exact
        # TMDB id avoids a re-search. Generic shape: {skill_id, arg, label}.
        "followup": {
            "skill_id": "seerr_request_movie",
            "arg": str(tmdb_id) if tmdb_id else title,
            "label": f"Request {label} on Seerr",
        } if (tmdb_id or title) else None,
    }
