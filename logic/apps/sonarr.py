"""Sonarr per-app module.

Encapsulates everything Sonarr-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``radarr.py`` shape (Sonarr is the TV-series companion to Radarr — same
*arr v3 API, same X-Api-Key auth):

    SLUGS               — catalog slugs this module handles ("sonarr").
    requires_api_key()  — True (Sonarr authenticates via the X-Api-Key header).
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + upcoming + queue + series-info (arg)
                          + add-series (arg) + remove-series (arg, destructive)
                          + search-missing + refresh.

The expanded card answers "how big is the library, how many episodes are
missing, what's downloading, and is the disk OK" at a glance:

    series_total   — every series in the library  (GET /api/v3/series)
    monitored      — series Sonarr is actively managing
    missing        — monitored episodes with no file yet
                     (GET /api/v3/wanted/missing — totalRecords)
    queue          — items currently downloading  (GET /api/v3/queue/status)
    disk_free_gb   — free space on the largest library disk (GET /api/v3/diskspace)
    health_issues  — active health warnings        (GET /api/v3/health)
    version        — Sonarr version                (GET /api/v3/system/status)

AI / Telegram skills
--------------------
* ``sonarr_status``          — library summary (live fetch).
* ``sonarr_upcoming``        — next 14 days of airing episodes (calendar).
* ``sonarr_queue``           — what's downloading + progress.
* ``sonarr_series_info``     — (arg) "do I have <show>?" — library lookup.
* ``sonarr_add_series``      — (arg) add a series by title (or TVDB id).
* ``sonarr_remove_series``   — (arg, DESTRUCTIVE) remove a series; KEEPS files.
* ``sonarr_search_missing``  — trigger a search for all monitored missing eps.
* ``sonarr_refresh``         — refresh + disk-scan the whole library.

Auth model: every authenticated Sonarr v3 endpoint takes the ``X-Api-Key``
header (Sonarr → Settings → General → API Key). The credential probe hits
the auth-required ``/api/v3/system/status`` so a bad key fails loudly.
Single-instance app (NOT fleet) — one card per pinned chip.

Add-series caveat: Sonarr v3 (pre-v4) requires a ``languageProfileId`` on
the POST; Sonarr v4 removed language profiles. We fetch
``/api/v3/languageprofile`` and include the first id ONLY when that
endpoint returns profiles, so the add works on both major versions.

Upstream API reference: <sonarr-host>/api/v3 (Swagger at /api). Endpoints:
    GET  /api/v3/system/status     — version (test-credential probe + footnote)
    GET  /api/v3/series            — library list (total / monitored)
    GET  /api/v3/wanted/missing    — missing-episode count (totalRecords)
    GET  /api/v3/queue/status      — downloading count
    GET  /api/v3/diskspace         — per-mount free / total bytes
    GET  /api/v3/health            — active health issues
    GET  /api/v3/calendar          — upcoming episodes
    GET  /api/v3/series/lookup     — TVDB-backed series search (add)
    GET  /api/v3/qualityprofile    — quality profiles (add)
    GET  /api/v3/languageprofile   — language profiles (add, v3 only)
    GET  /api/v3/rootfolder        — root folders (add)
    POST /api/v3/series            — add a series
    DELETE /api/v3/series/{id}     — remove a series
    POST /api/v3/command           — MissingEpisodeSearch / RefreshSeries
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from functools import partial as _partial

from logic.apps import _servarr
from logic.apps._common import cache_key, fetch_gate, peek_cache, resolve_cache_ttl
from logic.coerce import as_dict, as_list, safe_float, safe_int

# Servarr-family shared helpers (logic/apps/_servarr.py) bound to Sonarr's
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
_resolve_skill_target = _partial(_servarr.resolve_skill_target, app_label="Sonarr")
_find_in_library = _partial(_servarr.find_in_library_titled, id_field="tvdbId")
_command_skill = _partial(_servarr.command_skill, app_label="Sonarr", api_version="v3")
# Per-app image-proxy hook (local MediaCover via X-Api-Key, server-side).
image_proxy_url = _servarr.image_proxy_url

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("sonarr",)

# Read-only skills + free-form-arg series skills + background-command skills.
# No-arg skills surface as one-click drawer buttons AND AI / Telegram actions;
# the ``arg``-carrying series skills are AI / Telegram only (the dispatch
# supplies the title from natural language) — mirrors Radarr.
SKILLS: tuple[dict, ...] = (
    {
        "id": "sonarr_status",
        "name": "Sonarr status",
        "ai_phrases": ("sonarr status, tv library, how many series, how many "
                       "shows, how many episodes are missing, missing episodes, "
                       "sonarr health, tv collection size, disk space sonarr"),
        "destructive": False,
    },
    {
        "id": "sonarr_upcoming",
        "name": "Upcoming episodes",
        "ai_phrases": ("upcoming episodes, what's airing soon, sonarr calendar, "
                       "what episodes are coming, new episodes, tv schedule, "
                       "upcoming shows, what airs this week"),
        "destructive": False,
    },
    {
        "id": "sonarr_queue",
        "name": "Download queue",
        "ai_phrases": ("what's downloading on sonarr, sonarr queue, sonarr "
                       "downloads, what episodes are downloading, "
                       "download progress sonarr, queue details"),
        "destructive": False,
    },
    {
        "id": "sonarr_queue_delete",
        "name": "Remove from queue",
        "ai_phrases": ("remove from sonarr queue, cancel a sonarr download, "
                       "delete from download queue, cancel this download, "
                       "remove queued download"),
        "destructive": True,
        "arg": True,
        "arg_hint": ("the queue record id to remove (also removes it from the "
                     "download client); the drawer's per-row trash button supplies it"),
    },
    {
        "id": "sonarr_series_info",
        "name": "Look up a series",
        "ai_phrases": ("do i have <show>, is <show> in my library, "
                       "look up <show>, series info <show>, status of <show>, "
                       "do i have the show <show>, is <show> monitored, "
                       "how many episodes of <show> do i have"),
        "destructive": False,
        "arg": True,
        "arg_hint": "the series title to look up in the Sonarr library",
    },
    {
        "id": "sonarr_add_series",
        "name": "Add a series",
        "ai_phrases": ("add a series, add a show, add <show>, add <show> to "
                       "sonarr, add <show> to the library, get <show> on sonarr, "
                       "start watching <show>, put <show> in sonarr"),
        "destructive": False,
        "arg": True,
        "arg_hint": "the series title (or a numeric TVDB id)",
    },
    {
        "id": "sonarr_remove_series",
        "name": "Remove a series",
        "ai_phrases": ("remove a series, remove a show, remove <show>, "
                       "delete <show>, remove <show> from sonarr, "
                       "take <show> off sonarr, delete <show> from the library"),
        "destructive": True,
        "arg": True,
        "arg_hint": "the series title to remove from the Sonarr library",
    },
    {
        "id": "sonarr_search_missing",
        "name": "Search for missing episodes",
        "ai_phrases": ("search for missing episodes, find missing episodes, "
                       "search sonarr for missing, download missing episodes, "
                       "grab missing episodes, look for missing episodes"),
        "destructive": False,
    },
    {
        "id": "sonarr_refresh",
        "name": "Refresh series library",
        "ai_phrases": ("refresh sonarr, rescan the tv library, refresh series, "
                       "update sonarr library, rescan sonarr, "
                       "refresh and scan series"),
        "destructive": False,
    },
    # Manual-update skills — only for instances NOT linked to Docker (updates
    # for a native / non-Docker install are applied by hand).
    {
        "id": "sonarr_check_update",
        "name": "Check for updates",
        "ai_phrases": ("is sonarr up to date, check sonarr version, latest sonarr "
                       "version, is there a sonarr update, sonarr update available, "
                       "check for sonarr updates, what version of sonarr is running"),
        "destructive": False,
        "non_docker_only": True,
    },
    {
        "id": "sonarr_update",
        "name": "Update Sonarr",
        "ai_phrases": ("update sonarr, upgrade sonarr, install the sonarr update, "
                       "run the sonarr updater, update sonarr to the latest version, "
                       "apply the sonarr update"),
        "destructive": True,
        "non_docker_only": True,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. 60s default —
# the series list is the heaviest call and changes slowly (matches Radarr).
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Sonarr authenticates every v3 endpoint via X-Api-Key; the editor MUST
    render the api_key input + Test-connection button."""
    return True


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Sonarr's auth-required ``/api/v3/system/status`` — delegates to the
    shared Servarr probe bound to Sonarr's brand + api version."""
    return await _servarr.test_credential(host_row, chip, candidate_key,
                                          app_label="Sonarr", api_version="v3")


async def _missing_episode_count(cli: httpx.AsyncClient, base: str, key: str) -> int:
    """Total monitored-missing episodes via ``/api/v3/wanted/missing``
    (``totalRecords`` with ``pageSize=1`` — cheap). 0 on any failure."""
    try:
        r = await cli.get(base + "/api/v3/wanted/missing",
                          headers=_headers(key),
                          params={"page": "1", "pageSize": "1",
                                  "includeSeries": "false"})
        if r.status_code != 200:
            return 0
        return safe_int((r.json() or {}).get("totalRecords"))
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return 0


# noinspection DuplicatedCode
# The upstream-error guard + JSON-parse block below is structurally shared
# with every other per-app module's fetch_data (radarr / seerr / …) — the
# deliberate per-app encapsulation pattern (CLAUDE.md). Content differs (app
# name, endpoint, fields), so it stays inline rather than coupling modules.
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Sonarr's library summary for the expanded card.

    Returns ``{available, series_total, monitored, missing, queue,
    disk_free_gb, disk_total_gb, disks, health_issues, version,
    fetched_at}``. Raises ``ValueError`` / ``RuntimeError`` when the chip's
    api_key is unset / the base URL won't resolve / the primary upstream call
    errors. The series list is load-bearing; the rest are tolerated."""
    api_key = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=api_key, log_tag="sonarr")
    if hit is not None:
        return hit
    series_url = base + "/api/v3/series"
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(series_url, headers=_headers(api_key))
            missing = await _missing_episode_count(cli, base, api_key)
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
        print(f"[sonarr] error: fetch host={host_id} url={series_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        print(f"[sonarr] error: fetch host={host_id} url={r.request.url} "
              f"returned HTTP {r.status_code} (check the chip URL points at "
              f"the Sonarr root, e.g. https://sonarr.example.com)")
        if r.status_code in (401, 403):
            raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                               f"(check api_key) — {series_url}")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {series_url}")
    try:
        series = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    if not isinstance(series, list):
        series = []
    total = len(series)
    monitored = sum(1 for s in series if isinstance(s, dict) and s.get("monitored"))
    disk_free_gb, disk_total_gb = _primary_disk(disks)
    out: dict[str, Any] = {
        "available": True,
        "series_total": total,
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
    print(f"[sonarr] INFO fetched host={host_id} series={total} "
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
        "series_total": safe_int(data.get("series_total")),
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
    skill id. ``arg`` carries the free-form series title / TVDB id.
    ``actor_username`` is the invoking user — used to render dates in their
    Settings -> Profile -> Formats date format."""
    if skill_id == "sonarr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "sonarr_upcoming":
        return await _upcoming_skill(host_row, chip, host_id=host_id,
                                     actor_username=actor_username)
    if skill_id == "sonarr_queue":
        return await _queue_skill(host_row, chip, host_id=host_id)
    if skill_id == "sonarr_queue_delete":
        return await _servarr.queue_delete_skill(host_row, chip, arg=arg,
                                                 app_label="Sonarr", api_version="v3",
                                                 host_id=host_id)
    if skill_id == "sonarr_series_info":
        return await _series_info_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "sonarr_add_series":
        return await _add_series_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "sonarr_remove_series":
        return await _remove_series_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "sonarr_search_missing":
        return await _command_skill(host_row, chip, command="MissingEpisodeSearch",
                                    started_msg="🔍 Started a search for all monitored "
                                                "missing episodes on Sonarr.",
                                    host_id=host_id)
    if skill_id == "sonarr_refresh":
        return await _command_skill(host_row, chip, command="RefreshSeries",
                                    started_msg="🔄 Started a library refresh & disk "
                                                "scan on Sonarr.",
                                    host_id=host_id)
    if skill_id == "sonarr_check_update":
        return await _servarr.check_update_skill(host_row, chip, app_label="Sonarr",
                                                 api_version="v3", host_id=host_id,
                                                 actor_username=actor_username)
    if skill_id == "sonarr_update":
        return await _servarr.app_update_skill(host_row, chip, app_label="Sonarr",
                                               api_version="v3", host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the current library summary (force-bypasses the
    cache). Never raises."""
    print(f"[sonarr] INFO sonarr_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[sonarr] warning: sonarr_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    total = safe_int(data.get("series_total"))
    monitored = safe_int(data.get("monitored"))
    missing = safe_int(data.get("missing"))
    queue = safe_int(data.get("queue"))
    free_gb = safe_float(data.get("disk_free_gb"))
    health = safe_int(data.get("health_issues"))
    disks = as_list(data.get("disks"))
    lines = [
        f"📺 Series: {total:,}",
        f"📁 Monitored: {monitored:,}",
        f"{'❓' if missing else '✅'} Missing episodes: {missing:,}",
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
        "series_total": total, "monitored": monitored, "missing": missing,
        "queue": queue, "disk_free_gb": free_gb, "disks": disks,
        "health_issues": health,
    }


# noinspection DuplicatedCode
async def _upcoming_skill(host_row: dict, chip: dict, *,
                          host_id: Optional[str] = None,
                          actor_username: Optional[str] = None) -> dict:
    """Read-only: the next ~14 days of airing episodes from
    ``/api/v3/calendar``. Never raises."""
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    params = {
        "start": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": (now + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "unmonitored": "false", "includeSeries": "true",
    }
    print(f"[sonarr] INFO sonarr_upcoming host={host_id} (live fetch)")
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
    # Rich rows for the drawer's poster-thumbnail card — the series poster
    # (local MediaCover via the per-app image proxy) + the episode label +
    # air date. Mirrors Radarr's upcoming card.
    rich: list[dict] = []
    for ep in items[:12]:
        if not isinstance(ep, dict):
            continue
        ser = as_dict(ep.get("series"))
        title = str(ser.get("title") or ep.get("title") or "?").strip()
        sxe = ""
        sn = safe_int(ep.get("seasonNumber"))
        en = safe_int(ep.get("episodeNumber"))
        if sn or en:
            sxe = f" S{sn:02d}E{en:02d}"
        when = str(ep.get("airDateUtc") or ep.get("airDate") or "")[:10]
        when_fmt = _servarr.fmt_release_date(when, actor_username)
        lines.append(f"• {title}{sxe}" + (f" — {when_fmt}" if when_fmt else ""))
        sub = " · ".join(p for p in (sxe.strip(), when_fmt) if p)
        rich.append({"title": title, "subtitle": sub,
                     "poster": _servarr.poster_proxy_path(ser), "poster_proxy": True})
    if not lines:
        return {"ok": True, "status": 200,
                "detail": "📺 No episodes airing in the next 14 days."}
    return {"ok": True, "status": 200,
            "detail": "📺 Upcoming episodes (next 14 days):\n" + "\n".join(lines),
            "count": len(rich), "items": rich}


# noinspection DuplicatedCode
async def _queue_skill(host_row: dict, chip: dict, *,
                       host_id: Optional[str] = None) -> dict:
    """Read-only: what's currently downloading + progress from
    ``/api/v3/queue``. Never raises."""
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[sonarr] INFO sonarr_queue host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v3/queue", headers=_headers(api_key),
                              params={"pageSize": "20", "includeSeries": "true",
                                      "includeEpisode": "true"})
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
    # Rich rows — same {title, subtitle, poster, progress, row_action} shape as
    # Radarr's queue: series poster (local MediaCover via the per-app proxy) +
    # progress bar + a per-row delete (trash) button.
    rich: list[dict] = []
    for q in records[:12]:
        if not isinstance(q, dict):
            continue
        ser = as_dict(q.get("series"))
        title = str(ser.get("title") or q.get("title") or "?").strip()
        total = safe_float(q.get("size"))
        left = safe_float(q.get("sizeleft"))
        pct = int(round((1 - left / total) * 100)) if total > 0 else 0
        st = str(q.get("status") or "").strip().lower()
        lines.append(f"• {title} — {pct}%"
                     + (f" ({st})" if st and st != "downloading" else ""))
        row: "dict[str, Any]" = {
            "title": title,
            "subtitle": f"{pct}%" + (f" · {st}" if st and st != "downloading" else ""),
            "poster": _servarr.poster_proxy_path(ser, id_fallback=True),
            "poster_proxy": True, "progress": pct}
        qid = safe_int(q.get("id"))
        if qid:
            row["row_action"] = {
                "skill_id": "sonarr_queue_delete", "arg": str(qid),
                "icon": "trash-2", "destructive": True,
                "confirm_i18n": "apps.sonarr.queue_delete_confirm",
                "title_i18n": "apps.sonarr.queue_delete_title"}
        rich.append(row)
    return {"ok": True, "status": 200,
            "detail": f"⬇️ Downloading ({len(records)}):\n" + "\n".join(lines),
            "count": len(records), "count_i18n": "apps.skills.downloading_count",
            "items": rich}


async def _sonarr_lookup(cli: httpx.AsyncClient, base: str, api_key: str,
                         query: str) -> Optional[dict]:
    """Resolve a series via Sonarr's TVDB-backed lookup. A numeric ``query``
    uses ``term=tvdb:<id>``; else the raw title. Returns the series dict (which
    carries ``id > 0`` when already in the library) or ``None``."""
    q = (query or "").strip()
    term = f"tvdb:{q}" if q.isdigit() else q
    try:
        r = await cli.get(base + "/api/v3/series/lookup",
                          headers=_headers(api_key), params={"term": term})
        if r.status_code != 200:
            return None
        arr = r.json()
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return None
    if not isinstance(arr, list):
        return None
    for s in arr:
        if isinstance(s, dict) and s.get("tvdbId"):
            return s
    return None


# noinspection DuplicatedCode
async def _series_info_skill(host_row: dict, chip: dict, *,
                             arg: Optional[str] = None,
                             host_id: Optional[str] = None) -> dict:
    """Read-only: is ``<show>`` in the library, monitored, how complete? Looks
    it up in ``/api/v3/series``. Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0, "detail": "no series title given — which show?"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[sonarr] INFO sonarr_series_info host={host_id} query={query!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v3/series", headers=_headers(api_key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"lookup failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        series = r.json()
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    s = _find_in_library(series, query)
    if not s:
        return {"ok": True, "status": 200,
                "detail": f"❓ “{query}” is not in your Sonarr library. (Ask me to add it.)"}
    label = f"{str(s.get('title') or query)}{_year_suffix(s.get('year'))}"
    monitored = bool(s.get("monitored"))
    stats = as_dict(s.get("statistics"))
    have = safe_int(stats.get("episodeFileCount"))
    total_eps = safe_int(stats.get("episodeCount"))
    pct = safe_int(stats.get("percentOfEpisodes"))
    size_gib = safe_float(stats.get("sizeOnDisk")) / _GIB
    lines = [
        f"📺 {label}",
        "📁 Monitored" if monitored else "🚫 Not monitored",
        f"🎞️ Episodes: {have:,} / {total_eps:,}" + (f" ({pct}%)" if total_eps else ""),
    ]
    if size_gib > 0:
        lines.append(f"💾 {_fmt_size_gib(size_gib)}")
    return {"ok": True, "status": 200, "detail": "\n".join(lines)}


# noinspection DuplicatedCode
async def _add_series_skill(host_row: dict, chip: dict, *,
                            arg: Optional[str] = None,
                            host_id: Optional[str] = None) -> dict:
    """Action skill: add a series BY TITLE (or TVDB id). Looks it up, resolves
    a quality profile + the most-free root folder (+ a language profile on
    Sonarr v3), then POSTs ``/api/v3/series`` with
    ``addOptions.searchForMissingEpisodes``. Already-in-library is a friendly
    ok. Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0,
                "detail": "no series title given — tell me which show to add"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    label = query
    try:
        async with httpx.AsyncClient(verify=False, timeout=25.0,
                                     follow_redirects=True) as cli:
            series = await _sonarr_lookup(cli, base, api_key, query)
            if not series:
                return {"ok": False, "status": 404,
                        "detail": f"no series found matching “{query}”"}
            label = f"{str(series.get('title') or query)}{_year_suffix(series.get('year'))}"
            if safe_int(series.get("id")) > 0:
                return {"ok": True, "status": 200,
                        "detail": f"📺 {label} is already in your Sonarr library."}
            qp = await cli.get(base + "/api/v3/qualityprofile", headers=_headers(api_key))
            profiles = qp.json() if qp.status_code == 200 else []
            if not isinstance(profiles, list) or not profiles:
                return {"ok": False, "status": 0,
                        "detail": "no quality profile configured in Sonarr"}
            profile_id = safe_int((profiles[0] or {}).get("id"))
            rf = await cli.get(base + "/api/v3/rootfolder", headers=_headers(api_key))
            folders = rf.json() if rf.status_code == 200 else []
            folders = [f for f in folders if isinstance(f, dict) and f.get("path")] \
                if isinstance(folders, list) else []
            if not folders:
                return {"ok": False, "status": 0,
                        "detail": "no root folder configured in Sonarr"}
            best = max(folders, key=lambda f: safe_float(f.get("freeSpace")))
            root_path = str(best.get("path") or "").strip()
            payload = dict(series)
            payload.update({
                "qualityProfileId": profile_id,
                "rootFolderPath": root_path,
                "monitored": True,
                "addOptions": {"searchForMissingEpisodes": True, "monitor": "all"},
            })
            # Sonarr v3 (pre-v4) requires a languageProfileId; v4 removed it.
            # Include it ONLY when the endpoint returns profiles.
            try:
                lp = await cli.get(base + "/api/v3/languageprofile", headers=_headers(api_key))
                lprofiles = lp.json() if lp.status_code == 200 else []
                if isinstance(lprofiles, list) and lprofiles:
                    payload["languageProfileId"] = safe_int((lprofiles[0] or {}).get("id"))
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                pass
            print(f"[sonarr] INFO sonarr_add_series host={host_id} title={label!r} "
                  f"profile={profile_id} root={root_path!r}")
            pr = await cli.post(base + "/api/v3/series",
                                headers=_headers(api_key), json=payload)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"add failed: {type(e).__name__}: {e}"}
    if pr.status_code in (200, 201):
        return {"ok": True, "status": pr.status_code,
                "detail": f"📺 Added {label} to Sonarr — searching for episodes now."}
    if pr.status_code in (401, 403):
        return {"ok": False, "status": pr.status_code, "detail": "auth failed (check api_key)"}
    try:
        _body = (pr.text or "")[:200]
    except (ValueError, TypeError):
        _body = ""
    if pr.status_code == 400 and "exist" in _body.lower():
        return {"ok": True, "status": 200,
                "detail": f"📺 {label} is already in your Sonarr library."}
    return {"ok": False, "status": pr.status_code,
            "detail": f"Sonarr returned HTTP {pr.status_code} adding {label}"
                      + (f" — {_body}" if _body else "")}


# noinspection DuplicatedCode
async def _remove_series_skill(host_row: dict, chip: dict, *,
                               arg: Optional[str] = None,
                               host_id: Optional[str] = None) -> dict:
    """DESTRUCTIVE action skill: remove a series BY TITLE from the Sonarr
    library. Files on disk are KEPT (``deleteFiles=false``). Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0,
                "detail": "no series title given — tell me which show to remove"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v3/series", headers=_headers(api_key))
            if r.status_code in (401, 403):
                return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
            if r.status_code != 200:
                return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
            try:
                series = r.json()
            except (ValueError, TypeError):
                return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
            s = _find_in_library(series, query)
            if not s:
                return {"ok": False, "status": 404,
                        "detail": f"no series matching “{query}” in your Sonarr library"}
            sid = safe_int(s.get("id"))
            label = f"{str(s.get('title') or query)}{_year_suffix(s.get('year'))}"
            print(f"[sonarr] INFO sonarr_remove_series host={host_id} id={sid} title={label!r}")
            dr = await cli.delete(base + f"/api/v3/series/{sid}",
                                  headers=_headers(api_key),
                                  params={"deleteFiles": "false",
                                          "addImportListExclusion": "false"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"remove failed: {type(e).__name__}: {e}"}
    if dr.status_code in (200, 202, 204):
        return {"ok": True, "status": 200,
                "detail": f"🗑️ Removed {label} from Sonarr (files on disk kept)."}
    if dr.status_code in (401, 403):
        return {"ok": False, "status": dr.status_code, "detail": "auth failed (check api_key)"}
    return {"ok": False, "status": dr.status_code,
            "detail": f"Sonarr returned HTTP {dr.status_code} removing {label}"}
