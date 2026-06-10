"""Lidarr per-app module.

Encapsulates everything Lidarr-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``sonarr.py`` / ``radarr.py`` shape (Lidarr is the MUSIC companion to
Radarr / Sonarr — same *arr design, but its API is ``/api/v1`` (NOT v3)
and it manages ARTISTS + ALBUMS rather than movies / series):

    SLUGS               — catalog slugs this module handles ("lidarr").
    requires_api_key()  — True (Lidarr authenticates via the X-Api-Key header).
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + upcoming + queue + artist-info (arg)
                          + add-artist (arg) + remove-artist (arg, destructive)
                          + search-missing + refresh.

The expanded card answers "how big is the library, how many albums are
missing, what's downloading, and is the disk OK" at a glance:

    artists_total  — every artist in the library  (GET /api/v1/artist)
    monitored      — artists Lidarr is actively managing
    missing        — monitored albums with no file yet
                     (GET /api/v1/wanted/missing — totalRecords)
    queue          — items currently downloading  (GET /api/v1/queue/status)
    disk_free_gb   — free space on the largest library disk (GET /api/v1/diskspace)
    health_issues  — active health warnings        (GET /api/v1/health)
    version        — Lidarr version                (GET /api/v1/system/status)

AI / Telegram skills
--------------------
* ``lidarr_status``          — library summary (live fetch).
* ``lidarr_upcoming``        — next ~30 days of upcoming album releases.
* ``lidarr_queue``           — what's downloading + progress.
* ``lidarr_artist_info``     — (arg) "do I have <artist>?" — library lookup.
* ``lidarr_add_artist``      — (arg) add an artist by name.
* ``lidarr_remove_artist``   — (arg, DESTRUCTIVE) remove an artist; KEEPS files.
* ``lidarr_search_missing``  — trigger a search for all monitored missing albums.
* ``lidarr_refresh``         — refresh + disk-scan the whole library.

Auth model: every authenticated Lidarr v1 endpoint takes the ``X-Api-Key``
header (Lidarr → Settings → General → API Key). The credential probe hits
the auth-required ``/api/v1/system/status`` so a bad key fails loudly.
Single-instance app (NOT fleet) — one card per pinned chip.

Add-artist caveat: Lidarr's add REQUIRES a ``metadataProfileId`` (the
album-type / release-status filter) ON TOP of the ``qualityProfileId`` —
Radarr / Sonarr have no metadata profile. We fetch
``/api/v1/metadataprofile`` and use the first id.

Upstream API reference: <lidarr-host>/api/v1 (Swagger at /api). Endpoints:
    GET  /api/v1/system/status   — version (test-credential probe + footnote)
    GET  /api/v1/artist          — library list (total / monitored)
    GET  /api/v1/wanted/missing  — missing-album count (totalRecords)
    GET  /api/v1/queue/status    — downloading count
    GET  /api/v1/diskspace       — per-mount free / total bytes
    GET  /api/v1/health          — active health issues
    GET  /api/v1/calendar        — upcoming album releases
    GET  /api/v1/artist/lookup   — MusicBrainz-backed artist search (add)
    GET  /api/v1/qualityprofile  — quality profiles (add)
    GET  /api/v1/metadataprofile — metadata profiles (add, Lidarr-specific)
    GET  /api/v1/rootfolder      — root folders (add)
    POST /api/v1/artist          — add an artist
    DELETE /api/v1/artist/{id}   — remove an artist
    POST /api/v1/command         — MissingAlbumSearch / RefreshArtist
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from functools import partial as _partial

from logic.apps import _servarr
from logic.apps._common import cache_key, fetch_gate, peek_cache, resolve_cache_ttl
from logic.coerce import as_dict, as_list, safe_float, safe_int
from logic.external_urls import ExternalURL


def _album_poster(alb: dict, art: dict) -> str:
    """Resolve a Lidarr album's poster, MOST-RELIABLE-FIRST. Album / artist art
    frequently has NO allowlisted public CDN ``remoteUrl`` in the queue embed,
    and the local ``/MediaCover`` path 415s on auth-mismatched setups — so when
    there's no public remote cover, derive one from the MusicBrainz Cover Art
    Archive using the album's ``foreignAlbumId`` (release-group MBID), which is
    a PUBLIC, no-auth image. Order: remote(album) → remote(artist) →
    coverart(MBID) → local(album) → local(artist)."""
    remote = _servarr.remote_poster_url(alb) or _servarr.remote_poster_url(art)
    if remote:
        return remote
    mbid = str(alb.get("foreignAlbumId") or "").strip()
    if mbid:
        # front-500: the 500px front cover (307-redirects to archive.org; the
        # per-app proxy follows redirects). 404s gracefully when no art exists.
        return f"{ExternalURL.COVERART_ARCHIVE}/release-group/{mbid}/front-500"
    return (_servarr.local_poster_path_only(alb)
            or _servarr.local_poster_path_only(art))


# Servarr-family shared helpers (logic/apps/_servarr.py) bound to Lidarr's
# api version (v1) + brand, aliased to the historical underscore names so the
# skill bodies' call sites stay unchanged. Lidarr matches a STRING
# foreignArtistId + artistName, so it keeps its own _norm_name / _find_in_library.
_headers = _servarr.headers
_version_from = _servarr.version_from
_fmt_size_gib = _servarr.fmt_size_gib
_parse_disks = _servarr.parse_disks
_primary_disk = _servarr.primary_disk
_storage_summary_line = _servarr.storage_summary_line
_GIB = _servarr.GIB
_fetch_version = _partial(_servarr.fetch_version, api_version="v1")
_resolve_skill_target = _partial(_servarr.resolve_skill_target, app_label="Lidarr")
_command_skill = _partial(_servarr.command_skill, app_label="Lidarr", api_version="v1")
# Per-app image-proxy hook (local MediaCover via X-Api-Key, server-side).
image_proxy_url = _servarr.image_proxy_url

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("lidarr",)

# Read-only skills + free-form-arg artist skills + background-command skills.
# No-arg skills surface as one-click drawer buttons AND AI / Telegram actions;
# the ``arg``-carrying artist skills are AI / Telegram only (the dispatch
# supplies the name from natural language) — mirrors Sonarr / Radarr.
SKILLS: tuple[dict, ...] = (
    {
        "id": "lidarr_status",
        "name": "Lidarr status",
        "ai_phrases": ("lidarr status, music library, how many artists, how "
                       "many albums are missing, missing albums, lidarr health, "
                       "music collection size, disk space lidarr"),
        "destructive": False,
    },
    {
        "id": "lidarr_upcoming",
        "name": "Upcoming albums",
        "ai_phrases": ("upcoming albums, what albums are coming out, lidarr "
                       "calendar, new album releases, upcoming music, "
                       "albums releasing soon, what's releasing on lidarr"),
        "destructive": False,
    },
    {
        "id": "lidarr_queue",
        "name": "Download queue",
        "ai_phrases": ("what's downloading on lidarr, lidarr queue, lidarr "
                       "downloads, what music is downloading, "
                       "download progress lidarr, queue details"),
        "destructive": False,
    },
    {
        "id": "lidarr_queue_delete",
        "name": "Remove from queue",
        "ai_phrases": ("remove from lidarr queue, cancel a lidarr download, "
                       "delete from download queue, cancel this download, "
                       "remove queued download"),
        "destructive": True,
        "arg": True,
        "arg_hint": ("the queue record id to remove (also removes it from the "
                     "download client); the drawer's per-row trash button supplies it"),
    },
    {
        "id": "lidarr_artist_info",
        "name": "Look up an artist",
        "ai_phrases": ("do i have <artist>, is <artist> in my library, "
                       "look up <artist>, artist info <artist>, "
                       "status of <artist>, do i have music by <artist>, "
                       "is <artist> monitored, how many albums of <artist>"),
        "destructive": False,
        "arg": True,
        "arg_hint": "the artist name to look up in the Lidarr library",
    },
    {
        "id": "lidarr_add_artist",
        "name": "Add an artist",
        "ai_phrases": ("add an artist, add <artist>, add <artist> to lidarr, "
                       "add <artist> to the library, get <artist> on lidarr, "
                       "i want music by <artist>, put <artist> in lidarr"),
        "destructive": False,
        "arg": True,
        "arg_hint": "the artist name to add",
    },
    {
        "id": "lidarr_remove_artist",
        "name": "Remove an artist",
        "ai_phrases": ("remove an artist, remove <artist>, delete <artist>, "
                       "remove <artist> from lidarr, take <artist> off lidarr, "
                       "delete <artist> from the library"),
        "destructive": True,
        "arg": True,
        "arg_hint": "the artist name to remove from the Lidarr library",
    },
    {
        "id": "lidarr_search_missing",
        "name": "Search for missing albums",
        "ai_phrases": ("search for missing albums, find missing albums, "
                       "search lidarr for missing, download missing albums, "
                       "grab missing albums, look for missing music"),
        "destructive": False,
    },
    {
        "id": "lidarr_refresh",
        "name": "Refresh music library",
        "ai_phrases": ("refresh lidarr, rescan the music library, refresh "
                       "artists, update lidarr library, rescan lidarr, "
                       "refresh and scan music"),
        "destructive": False,
    },
    # Manual-update skills — only for instances NOT linked to Docker (updates
    # for a native / non-Docker install are applied by hand).
    {
        "id": "lidarr_check_update",
        "name": "Check for updates",
        "ai_phrases": ("is lidarr up to date, check lidarr version, latest lidarr "
                       "version, is there a lidarr update, lidarr update available, "
                       "check for lidarr updates, what version of lidarr is running"),
        "destructive": False,
        "non_docker_only": True,
    },
    {
        "id": "lidarr_update",
        "name": "Update Lidarr",
        "ai_phrases": ("update lidarr, upgrade lidarr, install the lidarr update, "
                       "run the lidarr updater, update lidarr to the latest version, "
                       "apply the lidarr update"),
        "destructive": True,
        "non_docker_only": True,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. 60s default —
# the artist list is the heaviest call and changes slowly (matches Sonarr).
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Lidarr authenticates every v1 endpoint via X-Api-Key; the editor MUST
    render the api_key input + Test-connection button."""
    return True


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Lidarr's auth-required ``/api/v1/system/status`` — delegates to the
    shared Servarr probe bound to Lidarr's brand + api version."""
    return await _servarr.test_credential(host_row, chip, candidate_key,
                                          app_label="Lidarr", api_version="v1")


async def _missing_album_count(cli: httpx.AsyncClient, base: str, key: str) -> int:
    """Total monitored-missing albums via ``/api/v1/wanted/missing``
    (``totalRecords`` with ``pageSize=1`` — cheap). 0 on any failure."""
    try:
        r = await cli.get(base + "/api/v1/wanted/missing",
                          headers=_headers(key),
                          params={"page": "1", "pageSize": "1",
                                  "includeArtist": "false"})
        if r.status_code != 200:
            return 0
        return safe_int((r.json() or {}).get("totalRecords"))
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return 0


# noinspection DuplicatedCode
# The upstream-error guard + JSON-parse block below is structurally shared
# with every other per-app module's fetch_data (radarr / sonarr / …) — the
# deliberate per-app encapsulation pattern (CLAUDE.md). Content differs (app
# name, endpoint, fields), so it stays inline rather than coupling modules.
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Lidarr's library summary for the expanded card.

    Returns ``{available, artists_total, monitored, missing, queue,
    disk_free_gb, disk_total_gb, disks, health_issues, version,
    fetched_at}``. Raises ``ValueError`` / ``RuntimeError`` when the chip's
    api_key is unset / the base URL won't resolve / the primary upstream call
    errors. The artist list is load-bearing; the rest are tolerated."""
    api_key = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=api_key, log_tag="lidarr")
    if hit is not None:
        return hit
    artist_url = base + "/api/v1/artist"
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(artist_url, headers=_headers(api_key))
            missing = await _missing_album_count(cli, base, api_key)
            queue = 0
            try:
                qr = await cli.get(base + "/api/v1/queue/status",
                                   headers=_headers(api_key))
                if qr.status_code == 200:
                    queue = safe_int((qr.json() or {}).get("totalCount"))
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                queue = 0
            disks: list[dict] = []
            try:
                dr = await cli.get(base + "/api/v1/diskspace",
                                   headers=_headers(api_key))
                if dr.status_code == 200:
                    disks = _parse_disks(dr.json())
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                disks = []
            health_issues = 0
            try:
                hr = await cli.get(base + "/api/v1/health",
                                   headers=_headers(api_key))
                if hr.status_code == 200:
                    _hj = hr.json()
                    health_issues = len(_hj) if isinstance(_hj, list) else 0
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                health_issues = 0
            ver = await _fetch_version(cli, base, api_key)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[lidarr] error: fetch host={host_id} url={artist_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        print(f"[lidarr] error: fetch host={host_id} url={r.request.url} "
              f"returned HTTP {r.status_code} (check the chip URL points at "
              f"the Lidarr root, e.g. https://lidarr.example.com)")
        if r.status_code in (401, 403):
            raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                               f"(check api_key) — {artist_url}")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {artist_url}")
    try:
        artists = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    if not isinstance(artists, list):
        artists = []
    total = len(artists)
    monitored = sum(1 for a in artists if isinstance(a, dict) and a.get("monitored"))
    disk_free_gb, disk_total_gb = _primary_disk(disks)
    out: dict[str, Any] = {
        "available": True,
        "artists_total": total,
        "monitored": monitored,
        "missing": safe_int(missing),
        "queue": safe_int(queue),
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
        "disks": disks,
        "health_issues": safe_int(health_issues),
        "version": ver,
        "fetched_at": int(now),
    }
    print(f"[lidarr] INFO fetched host={host_id} artists={total} "
          f"monitored={monitored} missing={out['missing']} queue={out['queue']} "
          f"mounts={len(disks)} disk_free_gb={disk_free_gb} "
          f"health={out['health_issues']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "artists_total": safe_int(data.get("artists_total")),
        "monitored": safe_int(data.get("monitored")),
        "missing": safe_int(data.get("missing")),
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
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown
    skill id. ``arg`` carries the free-form artist name. ``actor_username`` is
    the invoking user — used to render dates in their Settings -> Profile ->
    Formats date format."""
    if skill_id == "lidarr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "lidarr_upcoming":
        return await _upcoming_skill(host_row, chip, host_id=host_id,
                                     actor_username=actor_username)
    if skill_id == "lidarr_queue":
        return await _queue_skill(host_row, chip, host_id=host_id)
    if skill_id == "lidarr_queue_delete":
        return await _servarr.queue_delete_skill(host_row, chip, arg=arg,
                                                 app_label="Lidarr", api_version="v1",
                                                 host_id=host_id)
    if skill_id == "lidarr_artist_info":
        return await _artist_info_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "lidarr_add_artist":
        return await _add_artist_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "lidarr_remove_artist":
        return await _remove_artist_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "lidarr_search_missing":
        return await _command_skill(host_row, chip, command="MissingAlbumSearch",
                                    started_msg="🔍 Started a search for all monitored "
                                                "missing albums on Lidarr.",
                                    host_id=host_id)
    if skill_id == "lidarr_refresh":
        return await _command_skill(host_row, chip, command="RefreshArtist",
                                    started_msg="🔄 Started a library refresh & disk "
                                                "scan on Lidarr.",
                                    host_id=host_id)
    if skill_id == "lidarr_check_update":
        return await _servarr.check_update_skill(host_row, chip, app_label="Lidarr",
                                                 api_version="v1", host_id=host_id,
                                                 actor_username=actor_username)
    if skill_id == "lidarr_update":
        return await _servarr.app_update_skill(host_row, chip, app_label="Lidarr",
                                               api_version="v1", host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


async def calendar_items(host_row: dict, chip: dict, *,
                         start: str, end: str) -> list[dict]:
    """Normalised upcoming-ALBUM rows for the release-calendar widget — one row
    per Lidarr ``/api/v1/calendar`` entry (``includeArtist``) in the window:
    ``{date, title (artist), subtitle (album), type, ...}``. Never raises
    (returns [] on any failure)."""
    raw = await _servarr.fetch_calendar(host_row, chip, api_version="v1",
                                        start=start, end=end, app_label="Lidarr",
                                        extra_params={"includeArtist": "true"})
    web = _servarr.web_base(chip)
    out: list[dict] = []
    for alb in raw:
        if not isinstance(alb, dict):
            continue
        when = str(alb.get("releaseDate") or "")[:10]
        art = as_dict(alb.get("artist"))
        artist = str(art.get("artistName") or "").strip()
        album = str(alb.get("title") or "").strip()
        if not when or not (artist or album):
            continue
        fid = str(art.get("foreignArtistId") or "").strip()
        out.append({
            "date": when,
            "title": artist or album,
            "subtitle": album if artist else "",
            "type": "album",
            "service_slug": "lidarr",
            "poster": _servarr.poster_proxy_path(alb),
            "poster_proxy": True,
            "overview": _servarr.clamp_overview(alb.get("overview") or art.get("overview")),
            "runtime": 0,
            "time": "",
            "app_url": (f"{web}/artist/{fid}" if (web and fid) else web),
            "imdb_url": "",
            "tmdb_url": "",
        })
    return out


def _norm_name(s: Any) -> str:
    """Normalise an artist name / query for matching: lowercase, collapse
    whitespace. (Artists have no year suffix, unlike movies / series.)"""
    import re as _re
    return _re.sub(r"\s+", " ", str(s or "").strip().lower()).strip()


def _find_in_library(artists: Any, query: str) -> Optional[dict]:
    """Find an artist in the library list by foreignArtistId (MusicBrainz MBID
    exact), then normalised exact ``artistName``, then BIDIRECTIONAL substring.
    Returns the artist dict or ``None``."""
    if not isinstance(artists, list):
        return None
    raw = (query or "").strip()
    q = _norm_name(raw)
    if not q:
        return None
    for a in artists:
        if isinstance(a, dict) and str(a.get("foreignArtistId") or "").strip().lower() == q:
            return a
    for a in artists:
        if isinstance(a, dict) and _norm_name(a.get("artistName")) == q:
            return a
    for a in artists:
        if not isinstance(a, dict):
            continue
        t = _norm_name(a.get("artistName"))
        if t and (q in t or t in q):
            return a
    return None


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the current library summary (force-bypasses the
    cache). Never raises."""
    print(f"[lidarr] INFO lidarr_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[lidarr] warning: lidarr_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    total = safe_int(data.get("artists_total"))
    monitored = safe_int(data.get("monitored"))
    missing = safe_int(data.get("missing"))
    queue = safe_int(data.get("queue"))
    free_gb = safe_float(data.get("disk_free_gb"))
    health = safe_int(data.get("health_issues"))
    disks = as_list(data.get("disks"))
    lines = [
        f"🎵 Artists: {total:,}",
        f"📁 Monitored: {monitored:,}",
        f"{'❓' if missing else '✅'} Missing albums: {missing:,}",
        f"⬇️ Downloading: {queue:,}",
    ]
    # Compact storage summary for the text surfaces (AI / Telegram); the web
    # drawer renders the per-mount CARDS from the result's `disks` field.
    storage_line = _storage_summary_line(disks, free_gb)
    if storage_line:
        lines.append(storage_line)
    lines.append(f"{'⚠️' if health else '✅'} Health issues: {health:,}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "artists_total": total, "monitored": monitored, "missing": missing,
        "queue": queue, "disk_free_gb": free_gb, "disks": disks,
        "health_issues": health,
    }


# noinspection DuplicatedCode
async def _upcoming_skill(host_row: dict, chip: dict, *,
                          host_id: Optional[str] = None,
                          actor_username: Optional[str] = None) -> dict:
    """Read-only: the next ~30 days of upcoming album releases from
    ``/api/v1/calendar``. Never raises."""
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    params = {
        "start": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "unmonitored": "false", "includeArtist": "true",
    }
    print(f"[lidarr] INFO lidarr_upcoming host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/calendar",
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
    for alb in items[:12]:
        if not isinstance(alb, dict):
            continue
        art = as_dict(alb.get("artist"))
        artist = str(art.get("artistName") or "?").strip()
        album = str(alb.get("title") or "?").strip()
        when = str(alb.get("releaseDate") or "")[:10]
        when_fmt = _servarr.fmt_release_date(when, actor_username)
        lines.append(f"• {artist} — {album}" + (f" ({when_fmt})" if when_fmt else ""))
    if not lines:
        return {"ok": True, "status": 200,
                "detail": "🎵 No album releases in the next 30 days."}
    return {"ok": True, "status": 200,
            "detail": "🎵 Upcoming albums (next 30 days):\n" + "\n".join(lines)}


# noinspection DuplicatedCode
async def _queue_skill(host_row: dict, chip: dict, *,
                       host_id: Optional[str] = None) -> dict:
    """Read-only: what's currently downloading + progress from
    ``/api/v1/queue``. Never raises."""
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[lidarr] INFO lidarr_queue host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/queue", headers=_headers(api_key),
                              params={"pageSize": "20", "includeArtist": "true",
                                      "includeAlbum": "true"})
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
    # {title, subtitle, poster, progress} contract Radarr queue uses. The queue
    # record embeds the `album` (includeAlbum=true); _servarr.poster_url falls
    # back to the album's `cover` art (coverartarchive / fanart remoteUrl,
    # public CDN → loads direct).
    rich: list[dict] = []
    for q in records[:12]:
        if not isinstance(q, dict):
            continue
        art = as_dict(q.get("artist"))
        alb = as_dict(q.get("album"))
        artist = str(art.get("artistName") or "?").strip()
        album = str(alb.get("title") or q.get("title") or "").strip()
        total = safe_float(q.get("size"))
        left = safe_float(q.get("sizeleft"))
        pct = int(round((1 - left / total) * 100)) if total > 0 else 0
        st = str(q.get("status") or "").strip().lower()
        label = f"{artist}" + (f" — {album}" if album else "")
        lines.append(f"• {label} — {pct}%"
                     + (f" ({st})" if st and st != "downloading" else ""))
        row: "dict[str, Any]" = {
            "title": label,
            "subtitle": f"{pct}%" + (f" · {st}" if st and st != "downloading" else ""),
            "poster": _album_poster(alb, art),
            "poster_proxy": True,
            "progress": pct}
        qid = safe_int(q.get("id"))
        if qid:
            row["row_action"] = {
                "skill_id": "lidarr_queue_delete", "arg": str(qid),
                "icon": "trash-2", "destructive": True,
                "confirm_i18n": "apps.lidarr.queue_delete_confirm",
                "title_i18n": "apps.lidarr.queue_delete_title"}
        rich.append(row)
    if rich:
        _a0 = as_dict(records[0].get("album"))
        print(f"[lidarr] INFO queue posters host={host_id} "
              f"first_poster={rich[0].get('poster') or 'none'!r} "
              f"album_images=[{_servarr.image_debug(_a0)}]")
    return {"ok": True, "status": 200,
            "detail": f"⬇️ Downloading ({len(records)}):\n" + "\n".join(lines),
            "count": len(records), "count_i18n": "apps.skills.downloading_count",
            "items": rich}


# noinspection DuplicatedCode
async def _lidarr_lookup(cli: httpx.AsyncClient, base: str, api_key: str,
                         query: str) -> Optional[dict]:
    """Resolve an artist via Lidarr's MusicBrainz-backed lookup
    (``/api/v1/artist/lookup?term=<name>``). Returns the artist dict (which
    carries ``id > 0`` when already in the library) or ``None``."""
    q = (query or "").strip()
    try:
        r = await cli.get(base + "/api/v1/artist/lookup",
                          headers=_headers(api_key), params={"term": q})
        if r.status_code != 200:
            return None
        arr = r.json()
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return None
    if not isinstance(arr, list):
        return None
    for a in arr:
        if isinstance(a, dict) and a.get("foreignArtistId"):
            return a
    return None


# noinspection DuplicatedCode
async def _artist_info_skill(host_row: dict, chip: dict, *,
                             arg: Optional[str] = None,
                             host_id: Optional[str] = None) -> dict:
    """Read-only: is ``<artist>`` in the library, monitored, how complete? Looks
    it up in ``/api/v1/artist``. Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0, "detail": "no artist name given — which artist?"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[lidarr] INFO lidarr_artist_info host={host_id} query={query!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/artist", headers=_headers(api_key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"lookup failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        artists = r.json()
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    a = _find_in_library(artists, query)
    if not a:
        return {"ok": True, "status": 200,
                "detail": f"❓ “{query}” is not in your Lidarr library. (Ask me to add it.)"}
    label = str(a.get("artistName") or query)
    monitored = bool(a.get("monitored"))
    stats = as_dict(a.get("statistics"))
    albums = safe_int(stats.get("albumCount"))
    have = safe_int(stats.get("trackFileCount"))
    total_tracks = safe_int(stats.get("trackCount"))
    pct = safe_int(stats.get("percentOfTracks"))
    size_gib = safe_float(stats.get("sizeOnDisk")) / _GIB
    lines = [
        f"🎵 {label}",
        "📁 Monitored" if monitored else "🚫 Not monitored",
        f"💿 Albums: {albums:,}",
        f"🎶 Tracks: {have:,} / {total_tracks:,}" + (f" ({pct}%)" if total_tracks else ""),
    ]
    if size_gib > 0:
        lines.append(f"💾 {_fmt_size_gib(size_gib)}")
    return {"ok": True, "status": 200, "detail": "\n".join(lines)}


# noinspection DuplicatedCode
async def _add_artist_skill(host_row: dict, chip: dict, *,
                            arg: Optional[str] = None,
                            host_id: Optional[str] = None) -> dict:
    """Action skill: add an artist BY NAME. Looks it up, resolves a quality
    profile + a metadata profile (Lidarr-specific) + the most-free root folder,
    then POSTs ``/api/v1/artist`` with ``addOptions.searchForMissingAlbums``.
    Already-in-library is a friendly ok. Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0,
                "detail": "no artist name given — tell me which artist to add"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    label = query
    try:
        async with httpx.AsyncClient(verify=False, timeout=25.0,
                                     follow_redirects=True) as cli:
            artist = await _lidarr_lookup(cli, base, api_key, query)
            if not artist:
                return {"ok": False, "status": 404,
                        "detail": f"no artist found matching “{query}”"}
            label = str(artist.get("artistName") or query)
            if safe_int(artist.get("id")) > 0:
                return {"ok": True, "status": 200,
                        "detail": f"🎵 {label} is already in your Lidarr library."}
            qp = await cli.get(base + "/api/v1/qualityprofile", headers=_headers(api_key))
            profiles = qp.json() if qp.status_code == 200 else []
            if not isinstance(profiles, list) or not profiles:
                return {"ok": False, "status": 0,
                        "detail": "no quality profile configured in Lidarr"}
            profile_id = safe_int((profiles[0] or {}).get("id"))
            # Lidarr REQUIRES a metadata profile on add (Radarr / Sonarr don't).
            mp = await cli.get(base + "/api/v1/metadataprofile", headers=_headers(api_key))
            mprofiles = mp.json() if mp.status_code == 200 else []
            if not isinstance(mprofiles, list) or not mprofiles:
                return {"ok": False, "status": 0,
                        "detail": "no metadata profile configured in Lidarr"}
            metadata_id = safe_int((mprofiles[0] or {}).get("id"))
            rf = await cli.get(base + "/api/v1/rootfolder", headers=_headers(api_key))
            folders = rf.json() if rf.status_code == 200 else []
            folders = [f for f in folders if isinstance(f, dict) and f.get("path")] \
                if isinstance(folders, list) else []
            if not folders:
                return {"ok": False, "status": 0,
                        "detail": "no root folder configured in Lidarr"}
            best = max(folders, key=lambda f: safe_float(f.get("freeSpace")))
            root_path = str(best.get("path") or "").strip()
            payload = dict(artist)
            payload.update({
                "qualityProfileId": profile_id,
                "metadataProfileId": metadata_id,
                "rootFolderPath": root_path,
                "monitored": True,
                "addOptions": {"searchForMissingAlbums": True, "monitor": "all"},
            })
            print(f"[lidarr] INFO lidarr_add_artist host={host_id} name={label!r} "
                  f"profile={profile_id} metadata={metadata_id} root={root_path!r}")
            pr = await cli.post(base + "/api/v1/artist",
                                headers=_headers(api_key), json=payload)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"add failed: {type(e).__name__}: {e}"}
    if pr.status_code in (200, 201):
        return {"ok": True, "status": pr.status_code,
                "detail": f"🎵 Added {label} to Lidarr — searching for albums now."}
    if pr.status_code in (401, 403):
        return {"ok": False, "status": pr.status_code, "detail": "auth failed (check api_key)"}
    try:
        _body = (pr.text or "")[:200]
    except (ValueError, TypeError):
        _body = ""
    if pr.status_code == 400 and "exist" in _body.lower():
        return {"ok": True, "status": 200,
                "detail": f"🎵 {label} is already in your Lidarr library."}
    return {"ok": False, "status": pr.status_code,
            "detail": f"Lidarr returned HTTP {pr.status_code} adding {label}"
                      + (f" — {_body}" if _body else "")}


# noinspection DuplicatedCode
async def _remove_artist_skill(host_row: dict, chip: dict, *,
                               arg: Optional[str] = None,
                               host_id: Optional[str] = None) -> dict:
    """DESTRUCTIVE action skill: remove an artist BY NAME from the Lidarr
    library. Files on disk are KEPT (``deleteFiles=false``). Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0,
                "detail": "no artist name given — tell me which artist to remove"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/artist", headers=_headers(api_key))
            if r.status_code in (401, 403):
                return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
            if r.status_code != 200:
                return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
            try:
                artists = r.json()
            except (ValueError, TypeError):
                return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
            a = _find_in_library(artists, query)
            if not a:
                return {"ok": False, "status": 404,
                        "detail": f"no artist matching “{query}” in your Lidarr library"}
            aid = safe_int(a.get("id"))
            label = str(a.get("artistName") or query)
            print(f"[lidarr] INFO lidarr_remove_artist host={host_id} id={aid} name={label!r}")
            dr = await cli.delete(base + f"/api/v1/artist/{aid}",
                                  headers=_headers(api_key),
                                  params={"deleteFiles": "false",
                                          "addImportListExclusion": "false"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"remove failed: {type(e).__name__}: {e}"}
    if dr.status_code in (200, 202, 204):
        return {"ok": True, "status": 200,
                "detail": f"🗑️ Removed {label} from Lidarr (files on disk kept)."}
    if dr.status_code in (401, 403):
        return {"ok": False, "status": dr.status_code, "detail": "auth failed (check api_key)"}
    return {"ok": False, "status": dr.status_code,
            "detail": f"Lidarr returned HTTP {dr.status_code} removing {label}"}
