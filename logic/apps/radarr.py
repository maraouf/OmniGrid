"""Radarr per-app module.

Encapsulates everything Radarr-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``bazarr.py`` / ``seerr.py`` shape:

    SLUGS               — catalog slugs this module handles ("radarr").
    requires_api_key()  — True (Radarr authenticates via the X-Api-Key header).
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
* ``radarr_search_cutoff_unmet`` — search for quality upgrades of
  below-cutoff movies (POST /api/v3/command {name: CutOffUnmetMoviesSearch}).
* ``radarr_rss_sync``        — check the indexer RSS feeds now
  (POST /api/v3/command {name: RssSync}).
* ``radarr_refresh``         — refresh + disk-scan the whole library
  (POST /api/v3/command {name: RefreshMovie}).
These command skills are NON-destructive — they queue a background task
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
    GET  /api/v3/history/since   — today's grabbed / imported activity
    POST /api/v3/command         — MissingMoviesSearch / CutOffUnmetMoviesSearch
                                   / RssSync / RefreshMovie
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from functools import partial as _partial

from logic.apps import _servarr
from logic.apps._common import cache_key, fetch_gate, peek_cache, resolve_cache_ttl
from logic.coerce import as_dict, as_list, safe_float, safe_int

# Servarr-family shared helpers (logic/apps/_servarr.py) bound to Radarr's
# api version (v3) + brand + id field, aliased to the historical underscore
# names so the skill bodies' call sites stay unchanged.
# noinspection DuplicatedCode
_headers = _servarr.headers
_version_from = _servarr.version_from
_fmt_size_gib = _servarr.fmt_size_gib
_parse_disks = _servarr.parse_disks
_primary_disk = _servarr.primary_disk
_storage_summary_line = _servarr.storage_summary_line
_year_suffix = _servarr.year_suffix
_norm_title = _servarr.norm_title
_GIB = _servarr.GIB
_fetch_version = _partial(_servarr.fetch_version, api_version="v3")
_resolve_skill_target = _partial(_servarr.resolve_skill_target, app_label="Radarr")
_find_in_library = _partial(_servarr.find_in_library_titled, id_field="tmdbId")
_command_skill = _partial(_servarr.command_skill, app_label="Radarr", api_version="v3")
# Per-app image-proxy hook (local MediaCover via X-Api-Key, server-side) — the
# registry looks this up by name on the module, so re-export it here.
image_proxy_url = _servarr.image_proxy_url
# Cross-host redirect guard for the per-app image proxy (coverartarchive
# -> ia*.archive.org is the load-bearing case; everything off-allowlist is
# rejected). Re-exported from the shared base alongside the image hook.
image_redirect_allowed = _servarr.image_redirect_allowed

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("radarr",)

# Read-only status skill + two non-destructive background-command skills +
# two free-form-arg movie skills. The no-arg skills surface as one-click
# drawer buttons AND AI / Telegram actions; the ``arg``-carrying add / remove
# skills are AI / Telegram only (the dispatch surfaces supply the title from
# natural language) and follow Seerr's request-a-movie pattern.
SKILLS: tuple[dict, ...] = (
    {
        "id": "radarr_status",
        "name": "Radarr status",
        "ai_phrases": ("radarr status, movie library, how many movies, "
                       "how many movies are missing, missing movies, "
                       "radarr health, movie collection size, disk space radarr"),
        "destructive": False,
    },
    {
        "id": "radarr_upcoming",
        "name": "Upcoming movies",
        "ai_phrases": ("upcoming movies, what movies are coming out, "
                       "radarr calendar, what's releasing soon, "
                       "new movie releases, upcoming radarr, movies coming soon"),
        "destructive": False,
    },
    {
        "id": "radarr_queue",
        "name": "Download queue",
        "ai_phrases": ("what's downloading on radarr, radarr queue, "
                       "radarr downloads, what movies are downloading, "
                       "download progress radarr, queue details"),
        "destructive": False,
    },
    {
        "id": "radarr_queue_delete",
        "name": "Remove from queue",
        "ai_phrases": ("remove from radarr queue, cancel a radarr download, "
                       "delete from download queue, cancel this download, "
                       "remove queued download"),
        "destructive": True,
        "arg": True,
        "arg_hint": ("the queue record id to remove (also removes it from the "
                     "download client); the drawer's per-row trash button supplies it"),
    },
    {
        "id": "radarr_queue_blocklist_search",
        "name": "Blocklist & search a stuck download",
        "ai_phrases": ("blocklist and search radarr, blocklist a stuck download, "
                       "this download is stuck try another release, "
                       "blocklist and re-search, force a new release radarr"),
        "destructive": True,
        "arg": True,
        "arg_hint": ("the queue record id (the drawer's per-row blocklist button "
                     "supplies it as '<queue_id>:<movie_id>')"),
    },
    {
        "id": "radarr_movie_info",
        "name": "Look up a movie",
        "ai_phrases": ("do i have <title>, is <title> in my library, "
                       "is <title> downloaded, look up <title>, "
                       "movie info <title>, status of <title>, "
                       "do i have the movie <title>, is <title> monitored"),
        "destructive": False,
        "arg": True,
        "arg_hint": "the movie title to look up in the Radarr library",
    },
    {
        "id": "radarr_add_movie",
        "name": "Add a movie",
        "ai_phrases": ("add a movie, add <title>, add <title> to radarr, "
                       "add <title> to the library, get <title> on radarr, "
                       "download <title>, i want to watch <title>, "
                       "put <title> in radarr"),
        "destructive": False,
        "arg": True,
        "arg_hint": "the movie title (or a numeric TMDB id)",
    },
    {
        "id": "radarr_remove_movie",
        "name": "Remove a movie",
        "ai_phrases": ("remove a movie, remove <title>, delete <title>, "
                       "remove <title> from radarr, take <title> off radarr, "
                       "delete <title> from the library, remove the movie <title>"),
        "destructive": True,
        "arg": True,
        "arg_hint": "the movie title to remove from the Radarr library",
    },
    {
        "id": "radarr_search_movie",
        "name": "Search for a movie",
        "ai_phrases": ("search for <title>, find <title> now, grab <title>, "
                       "search radarr for <title>, look for a release of <title>, "
                       "download <title> now, search for the movie <title>"),
        "destructive": False,
        "arg": True,
        "arg_hint": "the movie title to search for a release now (must already "
                    "be in the Radarr library)",
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
        "id": "radarr_search_cutoff_unmet",
        "name": "Search cutoff-unmet movies",
        "ai_phrases": ("search cutoff unmet movies, upgrade my movies, find "
                       "quality upgrades, search for better releases, grab "
                       "quality upgrades radarr, search below-cutoff movies, "
                       "upgrade movies below quality cutoff"),
        "destructive": False,
    },
    {
        "id": "radarr_rss_sync",
        "name": "RSS sync",
        "ai_phrases": ("rss sync radarr, check the rss feeds, sync radarr rss, "
                       "check indexers for new releases now, run an rss sync, "
                       "force radarr to check feeds"),
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
    # Manual-update skills — only for instances NOT linked to Docker (updates
    # for a native / non-Docker install are applied by hand).
    {
        "id": "radarr_check_update",
        "name": "Check for updates",
        "ai_phrases": ("is radarr up to date, check radarr version, latest radarr "
                       "version, is there a radarr update, radarr update available, "
                       "check for radarr updates, what version of radarr is running"),
        "destructive": False,
        "non_docker_only": True,
    },
    {
        "id": "radarr_update",
        "name": "Update Radarr",
        "ai_phrases": ("update radarr, upgrade radarr, install the radarr update, "
                       "run the radarr updater, update radarr to the latest version, "
                       "apply the radarr update"),
        "destructive": True,
        "non_docker_only": True,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. Default TTL
# overridable per chip via the editor's `cache_ttl` field. 60s default —
# the movie-library list is the heaviest call and changes slowly, so a
# longer cache window than the badge-style apps keeps the fetch light.
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Radarr authenticates every v3 endpoint via X-Api-Key; the editor
    MUST render the api_key input + Test-connection button."""
    return True


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Radarr's auth-required ``/api/v3/system/status`` — delegates to
    the shared Servarr probe bound to Radarr's brand + api version."""
    return await _servarr.test_credential(host_row, chip, candidate_key,
                                          app_label="Radarr", api_version="v3")


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
            disks: list[dict] = []
            try:
                dr = await cli.get(base + "/api/v3/diskspace",
                                   headers=_headers(api_key))
                if dr.status_code == 200:
                    disks = _parse_disks(dr.json())
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                disks = []
            disk_free_gb, disk_total_gb = _primary_disk(disks)
            health_issues = 0
            health_messages: list[str] = []
            try:
                hr = await cli.get(base + "/api/v3/health",
                                   headers=_headers(api_key))
                if hr.status_code == 200:
                    _hj = hr.json()
                    if isinstance(_hj, list):
                        health_issues = len(_hj)
                        # Surface the actual messages (cap 4) — the count alone
                        # doesn't tell the operator what's wrong.
                        health_messages = [
                            str(h.get("message") or "").strip()
                            for h in _hj[:4] if isinstance(h, dict) and h.get("message")]
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                health_issues = 0
            # Cutoff-unmet — movies that HAVE a file but below the quality
            # cutoff (distinct from "missing"). totalRecords from a 1-row page.
            cutoff_unmet = 0
            try:
                cr = await cli.get(base + "/api/v3/wanted/cutoff",
                                   headers=_headers(api_key),
                                   params={"pageSize": "1"})
                if cr.status_code == 200:
                    cutoff_unmet = safe_int((cr.json() or {}).get("totalRecords"))
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                cutoff_unmet = 0
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
    size_bytes = 0.0
    for m in movies:
        if not isinstance(m, dict):
            continue
        size_bytes += safe_float(m.get("sizeOnDisk"))
        is_monitored = bool(m.get("monitored"))
        if is_monitored:
            monitored += 1
            if not m.get("hasFile"):
                missing += 1
    library_size_gb = round(size_bytes / _GIB, 1)
    # Movies releasing TODAY (digital/physical/cinema) — one cheap calendar
    # call, surfaced as the card's "Today" chip. Tolerated on failure (0).
    calendar_today = await _servarr.fetch_today_calendar_count(
        host_row, chip, api_version="v3", app_label="Radarr")
    # Grabbed / imported TODAY — "what has the downloader done today" activity
    # from the history feed. Tolerated on failure (0, 0).
    grabbed_today, imported_today = await _servarr.fetch_today_activity(
        host_row, chip, api_version="v3", app_label="Radarr")
    # "Wanted" rollup — everything Radarr still wants to fetch/upgrade:
    # missing (no file) + below-cutoff (has a file, wants a better release).
    wanted = missing + safe_int(cutoff_unmet)
    out: dict[str, Any] = {
        "available": True,
        "movies_total": total,
        "monitored": monitored,
        "missing": missing,
        "cutoff_unmet": safe_int(cutoff_unmet),
        "wanted": safe_int(wanted),
        "grabbed_today": safe_int(grabbed_today),
        "imported_today": safe_int(imported_today),
        "calendar_today": safe_int(calendar_today),
        "library_size_gb": library_size_gb,
        "queue": safe_int(queue),
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
        "disks": disks,
        "health_issues": safe_int(health_issues),
        "health_messages": health_messages,
        "version": ver,
        "fetched_at": int(now),
        # Library-growth + missing-backlog + disk-free-runway trend from the
        # shared servarr_samples retention table (drawer-only chart). Tolerated
        # on failure — the card renders fine without it.
        "trend": _safe_trend(host_id, service_idx),
    }
    print(f"[radarr] INFO fetched host={host_id} movies={total} "
          f"monitored={monitored} missing={missing} cutoff_unmet={out['cutoff_unmet']} "
          f"size_gb={library_size_gb} queue={out['queue']} "
          f"mounts={len(disks)} disk_free_gb={disk_free_gb} "
          f"health={out['health_issues']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def _safe_trend(host_id: str, service_idx: int) -> dict:
    """Best-effort library / backlog / disk trend from the shared *arr sampler.
    Returns the ``trend_summary`` dict, or ``{}`` on any failure (a missing
    sampler / empty table must never fail the card)."""
    try:
        from logic.apps import servarr_sampler  # noqa: PLC0415
        return servarr_sampler.trend_summary(str(host_id or ""), int(service_idx or 0))
    except Exception as e:  # noqa: BLE001
        print(f"[radarr] warning: trend_summary failed — {type(e).__name__}: {e}")
        return {}


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
        "cutoff_unmet": safe_int(data.get("cutoff_unmet")),
        "wanted": safe_int(data.get("wanted")),
        "grabbed_today": safe_int(data.get("grabbed_today")),
        "imported_today": safe_int(data.get("imported_today")),
        "library_size_gb": safe_float(data.get("library_size_gb")),
        "queue": safe_int(data.get("queue")),
        "disk_free_gb": safe_float(data.get("disk_free_gb")),
        "disks": as_list(data.get("disks")),
        "health_issues": safe_int(data.get("health_issues")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None,
                    actor_username: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404). ``arg``
    carries the free-form argument (movie title / TMDB id) for the
    add / remove / look-up skills. ``actor_username`` is the invoking user
    (web: authed user; Telegram: linked user) — used to render dates in their
    Settings -> Profile -> Formats date format."""
    if skill_id == "radarr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "radarr_upcoming":
        return await _upcoming_skill(host_row, chip, host_id=host_id,
                                     actor_username=actor_username)
    if skill_id == "radarr_queue":
        return await _queue_skill(host_row, chip, host_id=host_id)
    if skill_id == "radarr_queue_delete":
        return await _servarr.queue_delete_skill(host_row, chip, arg=arg,
                                                 app_label="Radarr", api_version="v3",
                                                 host_id=host_id)
    if skill_id == "radarr_queue_blocklist_search":
        return await _servarr.queue_blocklist_search_skill(
            host_row, chip, arg=arg, app_label="Radarr", api_version="v3",
            parent_id_field="movieId", search_command="MoviesSearch",
            search_ids_field="movieIds", host_id=host_id)
    if skill_id == "radarr_movie_info":
        return await _movie_info_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "radarr_add_movie":
        return await _add_movie_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "radarr_remove_movie":
        return await _remove_movie_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "radarr_search_movie":
        return await _search_movie_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "radarr_search_missing":
        return await _command_skill(host_row, chip, command="MissingMoviesSearch",
                                    started_msg="🔍 Started a search for all monitored "
                                                "missing movies on Radarr.",
                                    host_id=host_id)
    if skill_id == "radarr_search_cutoff_unmet":
        return await _command_skill(host_row, chip, command="CutOffUnmetMoviesSearch",
                                    started_msg="📈 Started a search for quality upgrades "
                                                "(cutoff-unmet movies) on Radarr.",
                                    host_id=host_id)
    if skill_id == "radarr_rss_sync":
        return await _command_skill(host_row, chip, command="RssSync",
                                    started_msg="📡 Started an RSS sync on Radarr — "
                                                "checking the indexer feeds for new releases.",
                                    host_id=host_id)
    if skill_id == "radarr_refresh":
        return await _command_skill(host_row, chip, command="RefreshMovie",
                                    started_msg="🔄 Started a library refresh & disk "
                                                "scan on Radarr.",
                                    host_id=host_id)
    if skill_id == "radarr_check_update":
        return await _servarr.check_update_skill(host_row, chip, app_label="Radarr",
                                                 api_version="v3", host_id=host_id,
                                                 actor_username=actor_username)
    if skill_id == "radarr_update":
        return await _servarr.app_update_skill(host_row, chip, app_label="Radarr",
                                               api_version="v3", host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


async def calendar_items(host_row: dict, chip: dict, *,
                         start: str, end: str) -> list[dict]:
    """Normalised upcoming-MOVIE rows for the release-calendar widget — one row
    per Radarr ``/api/v3/calendar`` entry in the [start, end] window:
    ``{date, title, subtitle, type, service_slug, poster, poster_proxy}``. The
    release date is the soonest of digital / physical / cinema. Never raises
    (returns [] on any failure)."""
    raw = await _servarr.fetch_calendar(host_row, chip, api_version="v3",
                                        start=start, end=end, app_label="Radarr")
    web = _servarr.resolve_base_url(host_row, chip)
    out: list[dict] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        when_full = str(m.get("digitalRelease") or m.get("physicalRelease")
                        or m.get("inCinemas") or "")
        when = when_full[:10]
        title = str(m.get("title") or "").strip()
        if not when or not title:
            continue
        tmdb = m.get("tmdbId")
        app_path = (f"/movie/{tmdb}" if tmdb else "")
        out.append({
            "date": when,
            "title": f"{title}{_year_suffix(m.get('year'))}",
            "subtitle": "",
            "type": "movie",
            "service_slug": "radarr",
            "poster": _servarr.poster_proxy_path(m, id_fallback=True),
            "poster_proxy": True,
            "overview": _servarr.clamp_overview(m.get("overview")),
            "runtime": max(0, _servarr.safe_int(m.get("runtime"))),
            "time": _servarr.release_time(when_full),
            # Full UTC ISO datetime when it carries a real (non-midnight) time,
            # so a tz-aware consumer (the Telegram /upcoming command) can render
            # the air time in the operator's timezone. Movie releases are
            # date-only (midnight), so this is "" for movies — no broadcast time.
            "airdate_utc": when_full if _servarr.release_time(when_full) else "",
            # `app_url` is the integration-base deep link (machine host:port);
            # `app_path` lets the widget rebuild the link against an operator's
            # friendly reverse-proxy URL override without touching the probe.
            "app_url": ((web + app_path) if (web and app_path) else web),
            "app_path": app_path,
            "imdb_url": _servarr.imdb_url(m.get("imdbId")),
            "tmdb_url": _servarr.tmdb_movie_url(tmdb),
        })
    return out


# noinspection DuplicatedCode
async def _upcoming_skill(host_row: dict, chip: dict, *,
                          host_id: Optional[str] = None,
                          actor_username: Optional[str] = None) -> dict:
    """Read-only: the next ~14 days of upcoming movie releases from
    ``/api/v3/calendar``. Never raises."""
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    params = {
        "start": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": (now + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "unmonitored": "false",
    }
    print(f"[radarr] INFO radarr_upcoming host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v3/calendar",
                              headers=_headers(api_key), params=params)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"calendar fetch failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        items = r.json()
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not isinstance(items, list):
        items = []
    lines = []
    # Structured rows for the SPA's rich skill-result card — each carries the
    # title, the formatted release date (subtitle) and a poster URL so the
    # drawer can render a small thumbnail next to the name. The plain-text
    # `detail` is kept verbatim for AI / Telegram (no image surface there).
    rich: list[dict] = []
    for m in items[:12]:
        if not isinstance(m, dict):
            continue
        title = str(m.get("title") or "?").strip()
        # cinema / physical / digital release dates — pick the soonest present.
        when = (str(m.get("digitalRelease") or m.get("physicalRelease")
                    or m.get("inCinemas") or "")[:10])
        when_fmt = _servarr.fmt_release_date(when, actor_username)
        name = f"{title}{_year_suffix(m.get('year'))}"
        lines.append(f"• {name}" + (f" — {when_fmt}" if when_fmt else ""))
        rich.append({"title": name, "subtitle": when_fmt,
                     "poster": _servarr.poster_proxy_path(m, id_fallback=True),
                     "poster_proxy": True})
    if not lines:
        return {"ok": True, "status": 200,
                "detail": "🎬 No upcoming movie releases in the next 14 days."}
    return {"ok": True, "status": 200,
            "detail": "🎬 Upcoming movies (next 14 days):\n" + "\n".join(lines),
            "items": rich}


# noinspection DuplicatedCode
async def _queue_skill(host_row: dict, chip: dict, *,
                       host_id: Optional[str] = None) -> dict:
    """Read-only: what's currently downloading + progress from
    ``/api/v3/queue``. Never raises."""
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[radarr] INFO radarr_queue host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v3/queue", headers=_headers(api_key),
                              params={"pageSize": "20", "includeMovie": "true"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"queue fetch failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        body = r.json()
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    records = body.get("records") if isinstance(body, dict) else None
    records = records if isinstance(records, list) else []
    if not records:
        return {"ok": True, "status": 200, "detail": "⬇️ Nothing is downloading right now."}
    lines = []
    # Structured rows for the SPA's rich skill-result card — SAME
    # {title, subtitle, poster} contract the upcoming skill uses, so the one
    # generic renderer (poster thumbnail + title + subtitle) draws both with
    # no per-skill UI. The queue record embeds the `movie` (includeMovie=true),
    # so the poster comes from the same _servarr.poster_url helper.
    rich: list[dict] = []
    for q in records[:12]:
        if not isinstance(q, dict):
            continue
        mv = as_dict(q.get("movie"))
        title = str(mv.get("title") or q.get("title") or "?").strip()
        total = safe_float(q.get("size"))
        left = safe_float(q.get("sizeleft"))
        pct = int(round((1 - left / total) * 100)) if total > 0 else 0
        st = str(q.get("status") or "").strip().lower()
        name = f"{title}{_year_suffix(mv.get('year'))}"
        st_suffix = f" ({st})" if st and st != "downloading" else ""
        lines.append(f"• {name} — {pct}%{st_suffix}")
        row: "dict[str, Any]" = {
            "title": name,
            "subtitle": f"{pct}%" + (f" · {st}" if st and st != "downloading" else ""),
            "poster": _servarr.poster_proxy_path(mv, id_fallback=True), "poster_proxy": True,
            "progress": pct}
        qid = safe_int(q.get("id"))
        if qid:
            # Two per-row actions: remove-from-queue (trash) AND blocklist &
            # re-search (the stuck-grab fix). The blocklist arg carries the
            # parent movieId so the re-search needs no extra queue lookup.
            pid = safe_int(q.get("movieId"))
            row["row_actions"] = [
                {"skill_id": "radarr_queue_delete", "arg": str(qid),
                 "icon": "trash-2", "destructive": True,
                 "confirm_i18n": "apps.radarr.queue_delete_confirm",
                 "title_i18n": "apps.radarr.queue_delete_title"},
                {"skill_id": "radarr_queue_blocklist_search", "arg": f"{qid}:{pid}",
                 "icon": "refresh-cw", "destructive": True,
                 "confirm_i18n": "apps.radarr.blocklist_search_confirm",
                 "title_i18n": "apps.radarr.blocklist_search_title"},
            ]
        rich.append(row)
    return {"ok": True, "status": 200,
            "detail": f"⬇️ Downloading ({len(records)}):\n" + "\n".join(lines),
            "count": len(records), "count_i18n": "apps.skills.downloading_count",
            "items": rich}


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
    cutoff_unmet = safe_int(data.get("cutoff_unmet"))
    wanted = safe_int(data.get("wanted"))
    grabbed_today = safe_int(data.get("grabbed_today"))
    imported_today = safe_int(data.get("imported_today"))
    library_size_gb = safe_float(data.get("library_size_gb"))
    queue = safe_int(data.get("queue"))
    free_gb = safe_float(data.get("disk_free_gb"))
    health = safe_int(data.get("health_issues"))
    disks = as_list(data.get("disks"))
    health_messages = as_list(data.get("health_messages"))
    lines = [
        f"🎬 Movies: {total:,}",
        f"📁 Monitored: {monitored:,}",
        f"{'❓' if missing else '✅'} Missing: {missing:,}",
    ]
    if cutoff_unmet:
        lines.append(f"📉 Below quality cutoff: {cutoff_unmet:,}")
    if wanted:
        lines.append(f"🎯 Wanted (missing + below-cutoff): {wanted:,}")
    if library_size_gb > 0:
        lines.append(f"🎞️ Library size: {_fmt_size_gib(library_size_gb)}")
    lines.append(f"⬇️ Downloading: {queue:,}")
    if grabbed_today or imported_today:
        lines.append(f"📆 Today: {grabbed_today:,} grabbed · {imported_today:,} imported")
    # Compact storage summary for the text surfaces (AI / Telegram); the web
    # drawer renders the per-mount CARDS from the result's `disks` field.
    storage_line = _storage_summary_line(disks, free_gb)
    if storage_line:
        lines.append(storage_line)
    lines.append(f"{'⚠️' if health else '✅'} Health issues: {health:,}")
    # Surface the actual health messages (not just the count) so the AI can act.
    for msg in health_messages[:3]:
        if msg:
            lines.append(f"   • {msg}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "movies_total": total, "monitored": monitored, "missing": missing,
        "cutoff_unmet": cutoff_unmet, "library_size_gb": library_size_gb,
        "queue": queue, "disk_free_gb": free_gb, "disks": disks,
        "health_issues": health, "health_messages": health_messages,
    }


# noinspection DuplicatedCode
async def _radarr_lookup(cli: httpx.AsyncClient, base: str, api_key: str,
                         query: str) -> Optional[dict]:
    """Resolve a movie via Radarr's TMDB-backed lookup. A numeric ``query``
    hits ``/api/v3/movie/lookup/tmdb`` (single object); a title hits
    ``/api/v3/movie/lookup`` (list — top hit). Returns the movie dict (which
    carries ``id > 0`` when it's already in the library) or ``None``."""
    q = (query or "").strip()
    try:
        if q.isdigit():
            r = await cli.get(base + "/api/v3/movie/lookup/tmdb",
                              headers=_headers(api_key), params={"tmdbId": q})
            if r.status_code != 200:
                return None
            obj = r.json()
            return obj if isinstance(obj, dict) and obj.get("tmdbId") else None
        r = await cli.get(base + "/api/v3/movie/lookup",
                          headers=_headers(api_key), params={"term": q})
        if r.status_code != 200:
            return None
        arr = r.json()
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return None
    if not isinstance(arr, list):
        return None
    for m in arr:
        if isinstance(m, dict) and m.get("tmdbId"):
            return m
    return None


# noinspection DuplicatedCode
async def _movie_info_skill(host_row: dict, chip: dict, *,
                            arg: Optional[str] = None,
                            host_id: Optional[str] = None) -> dict:
    """Read-only: is ``<title>`` in the library, monitored, downloaded? Looks
    the movie up in ``/api/v3/movie``. Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0, "detail": "no movie title given — which movie?"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[radarr] INFO radarr_movie_info host={host_id} query={query!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v3/movie", headers=_headers(api_key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"lookup failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        movies = r.json()
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    m = _find_in_library(movies, query)
    if not m:
        return {"ok": True, "status": 200,
                "detail": f"❓ “{query}” is not in your Radarr library. (Ask me to add it.)"}
    label = f"{str(m.get('title') or query)}{_year_suffix(m.get('year'))}"
    monitored = bool(m.get("monitored"))
    has_file = bool(m.get("hasFile"))
    lines = [
        f"🎬 {label}",
        "📁 Monitored" if monitored else "🚫 Not monitored",
        "✅ Downloaded" if has_file else "❓ Missing (no file yet)",
    ]
    if has_file:
        mf = as_dict(m.get("movieFile"))
        q = as_dict(mf.get("quality"))
        qq = as_dict(q.get("quality"))
        qname = str(qq.get("name") or "").strip()
        size_gib = safe_float(m.get("sizeOnDisk")) / _GIB
        extra = " · ".join(p for p in (qname, _fmt_size_gib(size_gib) if size_gib > 0 else "") if p)
        if extra:
            lines.append(f"💾 {extra}")
    return {"ok": True, "status": 200, "detail": "\n".join(lines)}


# noinspection DuplicatedCode
async def _add_movie_skill(host_row: dict, chip: dict, *,
                           arg: Optional[str] = None,
                           host_id: Optional[str] = None) -> dict:
    """Action skill: add a movie BY TITLE (or TMDB id). Looks it up, resolves
    a quality profile + the most-free root folder, then POSTs
    ``/api/v3/movie`` with ``addOptions.searchForMovie``. Already-in-library
    is a friendly ok. Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0,
                "detail": "no movie title given — tell me which movie to add"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    label = query
    try:
        async with httpx.AsyncClient(verify=False, timeout=25.0,
                                     follow_redirects=True) as cli:
            movie = await _radarr_lookup(cli, base, api_key, query)
            if not movie:
                return {"ok": False, "status": 404,
                        "detail": f"no movie found matching “{query}”"}
            label = f"{str(movie.get('title') or query)}{_year_suffix(movie.get('year'))}"
            if safe_int(movie.get("id")) > 0:
                return {"ok": True, "status": 200,
                        "detail": f"🎬 {label} is already in your Radarr library."}
            # Resolve a quality profile (first) + the most-free root folder.
            qp = await cli.get(base + "/api/v3/qualityprofile", headers=_headers(api_key))
            profiles = qp.json() if qp.status_code == 200 else []
            if not isinstance(profiles, list) or not profiles:
                return {"ok": False, "status": 0,
                        "detail": "no quality profile configured in Radarr"}
            profile_id = safe_int((profiles[0] or {}).get("id"))
            rf = await cli.get(base + "/api/v3/rootfolder", headers=_headers(api_key))
            folders = rf.json() if rf.status_code == 200 else []
            folders = [f for f in folders if isinstance(f, dict) and f.get("path")] \
                if isinstance(folders, list) else []
            if not folders:
                return {"ok": False, "status": 0,
                        "detail": "no root folder configured in Radarr"}
            best = max(folders, key=lambda f: safe_float(f.get("freeSpace")))
            root_path = str(best.get("path") or "").strip()
            payload = dict(movie)
            payload.update({
                "qualityProfileId": profile_id,
                "rootFolderPath": root_path,
                "monitored": True,
                "minimumAvailability": "released",
                "addOptions": {"searchForMovie": True},
            })
            print(f"[radarr] INFO radarr_add_movie host={host_id} title={label!r} "
                  f"profile={profile_id} root={root_path!r}")
            pr = await cli.post(base + "/api/v3/movie",
                                headers=_headers(api_key), json=payload)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"add failed: {type(e).__name__}: {e}"}
    if pr.status_code in (200, 201):
        return {"ok": True, "status": pr.status_code,
                "detail": f"🎬 Added {label} to Radarr — searching for a release now."}
    if pr.status_code in (401, 403):
        return {"ok": False, "status": pr.status_code, "detail": "auth failed (check api_key)"}
    try:
        _body = (pr.text or "")[:200]
    except (ValueError, TypeError):
        _body = ""
    if pr.status_code == 400 and "exist" in _body.lower():
        return {"ok": True, "status": 200,
                "detail": f"🎬 {label} is already in your Radarr library."}
    return {"ok": False, "status": pr.status_code,
            "detail": f"Radarr returned HTTP {pr.status_code} adding {label}"
                      + (f" — {_body}" if _body else "")}


# noinspection DuplicatedCode
async def _remove_movie_skill(host_row: dict, chip: dict, *,
                              arg: Optional[str] = None,
                              host_id: Optional[str] = None) -> dict:
    """DESTRUCTIVE action skill: remove a movie BY TITLE from the Radarr
    library. Files on disk are KEPT (``deleteFiles=false``) — removing the
    library entry is the action; deleting media is not. Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0,
                "detail": "no movie title given — tell me which movie to remove"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v3/movie", headers=_headers(api_key))
            if r.status_code in (401, 403):
                return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
            if r.status_code != 200:
                return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
            try:
                movies = r.json()
            except (ValueError, TypeError):
                return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
            m = _find_in_library(movies, query)
            if not m:
                return {"ok": False, "status": 404,
                        "detail": f"no movie matching “{query}” in your Radarr library"}
            mid = safe_int(m.get("id"))
            label = f"{str(m.get('title') or query)}{_year_suffix(m.get('year'))}"
            print(f"[radarr] INFO radarr_remove_movie host={host_id} id={mid} title={label!r}")
            dr = await cli.delete(base + f"/api/v3/movie/{mid}",
                                  headers=_headers(api_key),
                                  params={"deleteFiles": "false",
                                          "addImportExclusion": "false"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"remove failed: {type(e).__name__}: {e}"}
    if dr.status_code in (200, 202, 204):
        return {"ok": True, "status": 200,
                "detail": f"🗑️ Removed {label} from Radarr (files on disk kept)."}
    if dr.status_code in (401, 403):
        return {"ok": False, "status": dr.status_code, "detail": "auth failed (check api_key)"}
    return {"ok": False, "status": dr.status_code,
            "detail": f"Radarr returned HTTP {dr.status_code} removing {label}"}


# noinspection DuplicatedCode
async def _search_movie_skill(host_row: dict, chip: dict, *,
                              arg: Optional[str] = None,
                              host_id: Optional[str] = None) -> dict:
    """Action skill: trigger a release search for ONE movie already in the
    library (``POST /api/v3/command {name: MoviesSearch, movieIds: [id]}``).
    Looks the movie up by title first; not-in-library is a friendly hint to add
    it. Non-destructive (queues a background search). Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0,
                "detail": "no movie title given — which movie should I search for?"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v3/movie", headers=_headers(api_key))
            if r.status_code in (401, 403):
                return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
            if r.status_code != 200:
                return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
            try:
                movies = r.json()
            except (ValueError, TypeError):
                return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
            m = _find_in_library(movies, query)
            if not m:
                return {"ok": True, "status": 200,
                        "detail": f"❓ “{query}” is not in your Radarr library yet. "
                                  f"(Ask me to add it — that searches automatically.)"}
            mid = safe_int(m.get("id"))
            label = f"{str(m.get('title') or query)}{_year_suffix(m.get('year'))}"
            print(f"[radarr] INFO radarr_search_movie host={host_id} id={mid} title={label!r}")
            pr = await cli.post(base + "/api/v3/command",
                                headers=_headers(api_key),
                                json={"name": "MoviesSearch", "movieIds": [mid]})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"search failed: {type(e).__name__}: {e}"}
    if pr.status_code in (200, 201):
        return {"ok": True, "status": pr.status_code,
                "detail": f"🔍 Started a release search for {label} on Radarr."}
    if pr.status_code in (401, 403):
        return {"ok": False, "status": pr.status_code, "detail": "auth failed (check api_key)"}
    return {"ok": False, "status": pr.status_code,
            "detail": f"Radarr returned HTTP {pr.status_code} searching for {label}"}
