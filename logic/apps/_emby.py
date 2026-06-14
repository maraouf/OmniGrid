"""Shared Emby / Jellyfin per-app base.

Jellyfin is a fork of Emby, so the two media servers share ~95% of the REST
API surface (``/Items/Counts``, ``/Sessions``, ``/System/Info``,
``/Library/VirtualFolders``, ``/Items?searchTerm=``, ``/Library/Refresh``,
``/Items/<id>/Images/Primary``). This module owns every byte of that shared
logic ONCE; the per-app modules (``jellyfin.py`` / ``emby.py``) are thin
binders that pass a :class:`Config` (brand label + auth scheme + slug / log
tag) — the same de-duplication discipline the ``*arr`` family uses via
``_servarr.py``.

The ONLY real divergence between Emby and Jellyfin is the auth header:

  * Jellyfin: ``Authorization: MediaBrowser Token="<key>"`` (the MediaBrowser
    scheme — ``scheme="mediabrowser"``).
  * Emby:     ``X-Emby-Token: <key>`` (the native Emby header —
    ``scheme="emby"``).

Both are server-issued API keys (Jellyfin: Dashboard → API Keys; Emby:
Settings → Advanced → API Keys). Runtimes / positions are in TICKS
(100-nanosecond units), so a play percentage is
``PositionTicks / RunTimeTicks * 100``.

Dependency-free leaf (imports only ``_common`` + ``coerce`` + ``httpx``) so a
binder can import it without a cycle.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, NamedTuple, Optional
from urllib.parse import urlencode

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl,
    resolve_credential_target)
from logic.coerce import as_dict, as_list, safe_int

# Default per-instance data-cache TTL (overridable per chip via the editor's
# `cache_ttl` field). 30s — the library counts move slowly; now-playing is
# fetched live by its own skill.
DEFAULT_CACHE_TTL_S = 30


class Config(NamedTuple):
    """Per-brand binding config. ``brand`` is the display label ("Jellyfin" /
    "Emby"); ``scheme`` selects the auth header; ``log_tag`` prefixes the
    ``[tag]`` log lines; ``slug`` is the catalog slug (skill-id prefix +
    i18n namespace)."""
    brand: str
    scheme: str
    log_tag: str
    slug: str


def headers(key: str, scheme: str) -> dict:
    """Auth header + JSON Accept, in the brand's native scheme."""
    if scheme == "emby":
        return {"X-Emby-Token": key, "Accept": "application/json"}
    return {"Authorization": f'MediaBrowser Token="{key}"',
            "Accept": "application/json"}


def img_headers(key: str, scheme: str) -> dict:
    """Headers for a BINARY image fetch (poster / avatar) — the credential
    header ONLY plus ``Accept: */*`` (a JSON Accept can 406 a binary fetch
    behind a strict upstream / proxy; the project's image-hook rule)."""
    if scheme == "emby":
        return {"X-Emby-Token": key, "Accept": "*/*"}
    return {"Authorization": f'MediaBrowser Token="{key}"', "Accept": "*/*"}


def image_proxy_url(host_row: dict, chip: dict, path: str, *,
                    scheme: str) -> "tuple[str, dict]":
    """Per-app image-proxy hook — fetch a poster / thumb / user avatar
    server-side so the API key never reaches the browser. ``path`` is a
    relative image path (``/Items/<id>/Images/Primary`` or ``/Users/<id>/
    Images/Primary``); we size it down for the card. SSRF guard: only a clean
    relative ``/...`` path is accepted (no absolute host — Emby / Jellyfin serve
    all their art off their own base)."""
    key = (chip.get("api_key") or "").strip()
    p = (path or "").strip()
    if not p:
        raise ValueError("empty image path")
    if "://" in p or not p.startswith("/") or ".." in p:
        raise ValueError("image must be a relative server path")
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured")
    sep = "&" if "?" in p else "?"
    url = base.rstrip("/") + p + sep + urlencode({"maxHeight": 450, "quality": 90})
    return url, img_headers(key, scheme)


# Per-type count fields in /Items/Counts — summed as the fallback total when
# the server doesn't report a usable ItemCount.
_COUNT_FIELDS = ("MovieCount", "SeriesCount", "EpisodeCount", "AlbumCount",
                 "SongCount", "ArtistCount", "MusicVideoCount", "BoxSetCount",
                 "BookCount", "TrailerCount", "ProgramCount")

# Library CollectionType → the top-grid stat it feeds. Only these three map to
# a grid cell; every other type (homevideos / photos / books / boxsets / mixed /
# custom) still counts toward the grand total + shows in the per-library list.
_LIB_TYPE_TO_KIND = {"movies": "movies", "tvshows": "series", "music": "songs"}


def _items_total(counts: dict, library_list: list) -> int:
    """Total tracked items across the WHOLE server. Prefer the SUM of the
    per-library Recursive counts (the reliable figure — it covers custom /
    mixed libraries) and fall back to ``/Items/Counts``' ``ItemCount`` then the
    per-type sum only when no per-library data is available. ``/Items/Counts``'
    ``ItemCount`` is NOT trusted first: on servers whose libraries are custom-
    typed it under-reports badly (one operator saw ItemCount=1 against ~3,600
    real items)."""
    lib_sum = sum(safe_int(lib.get("count")) for lib in library_list
                  if isinstance(lib, dict))
    if lib_sum > 0:
        return lib_sum
    n = safe_int(counts.get("ItemCount"))
    if n > 0:
        return n
    return sum(safe_int(counts.get(k)) for k in _COUNT_FIELDS)


def _counts_from_libraries(library_list: list) -> dict:
    """Derive ``{movies, series, songs}`` by summing the per-library Recursive
    counts by CollectionType — more reliable than ``/Items/Counts`` when a
    server's media live in custom-typed libraries (which report MovieCount=0
    etc.). Empty when no library is of the matching type (the caller then falls
    back to the ``/Items/Counts`` figure)."""
    out = {"movies": 0, "series": 0, "songs": 0}
    for lib in library_list:
        if not isinstance(lib, dict):
            continue
        kind = _LIB_TYPE_TO_KIND.get(str(lib.get("type") or "").lower())
        if kind:
            out[kind] += safe_int(lib.get("count"))
    return out


async def _virtual_folders(cli: "httpx.AsyncClient", base: str, key: str,
                           scheme: str) -> list:
    """Every library as ``[{id, name, type}]`` from ``/Library/VirtualFolders``
    (the library's ``ItemId`` is needed to fetch its items by ``ParentId``).
    Best-effort: ``[]`` on a non-200 / parse failure. Capped at 30 libraries."""
    try:
        vr = await cli.get(base + "/Library/VirtualFolders",
                           headers=headers(key, scheme))
        if vr.status_code != 200:
            return []
        folders = as_list(vr.json())
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return []
    out: list = []
    for vf in folders[:30]:
        if not isinstance(vf, dict):
            continue
        name = str(vf.get("Name") or "").strip()
        if not name:
            continue
        out.append({"id": str(vf.get("ItemId") or "").strip(),
                    "name": name,
                    "type": str(vf.get("CollectionType") or "").strip()})
    return out


async def _library_list(cli: "httpx.AsyncClient", base: str, key: str,
                        scheme: str) -> list:
    """Every library as ``[{name, type, count}]`` — the ``_virtual_folders``
    name + collection type, plus a bounded per-library Recursive item count
    (``/Items?ParentId=<id>&Limit=0&EnableTotalRecordCount=true`` →
    ``TotalRecordCount``). Best-effort: a failed per-library count yields 0."""
    out: list = []
    for vf in await _virtual_folders(cli, base, key, scheme):
        item_id = vf.get("id") or ""
        count = 0
        if item_id:
            try:
                ir = await cli.get(base + "/Items", headers=headers(key, scheme),
                                   params={"ParentId": item_id, "Recursive": "true",
                                           "Limit": "0",
                                           "EnableTotalRecordCount": "true"})
                if ir.status_code == 200:
                    count = safe_int(as_dict(ir.json()).get("TotalRecordCount"))
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                count = 0
        out.append({"name": vf.get("name") or "?",
                    "type": vf.get("type") or "",
                    "count": count})
    return out


# Bounded recently-added pull for the "items added this week" stat. 256 items
# is plenty for a home server's weekly additions; when every one of the 256 is
# within the 7-day window the SPA renders the count as "256+".
_RECENT_CREATED_CAP = 256
_WEEK_SECONDS = 7 * 86400


async def _recent_created(cli: "httpx.AsyncClient", base: str, key: str,
                          scheme: str) -> list:
    """The ``DateCreated`` ISO strings of the most-recently-added real items
    (movies / episodes / albums / etc.), newest-first, bounded to
    ``_RECENT_CREATED_CAP``. Best-effort: ``[]`` on a non-200 / parse failure."""
    try:
        r = await cli.get(base + "/Items", headers=headers(key, scheme), params={
            "Recursive": "true", "SortBy": "DateCreated", "SortOrder": "Descending",
            "IncludeItemTypes": "Movie,Episode,MusicAlbum,Audio,MusicVideo,Video",
            "Fields": "DateCreated", "Limit": str(_RECENT_CREATED_CAP),
            "ImageTypeLimit": "0", "EnableImages": "false"})
        if r.status_code != 200:
            return []
        items = as_list(as_dict(r.json()).get("Items"))
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return []
    return [str(as_dict(it).get("DateCreated") or "") for it in items
            if isinstance(it, dict)]


def _parse_iso_epoch(s: str) -> int:
    """Parse an Emby / Jellyfin ``DateCreated`` ISO string to epoch seconds
    (0 on any parse failure). Handles the trailing ``Z`` + fractional seconds."""
    import datetime as _dt  # noqa: PLC0415
    raw = (s or "").strip()
    if not raw:
        return 0
    raw = raw.replace("Z", "+00:00")
    # Trim over-precise fractional seconds (Emby emits 7 digits; fromisoformat
    # accepts at most 6 before Python 3.11 — clamp to be safe).
    if "." in raw:
        head, _, tail = raw.partition(".")
        frac = ""
        tz = ""
        for ch in tail:
            if ch.isdigit() and len(frac) < 6:
                frac += ch
            elif ch in "+-":
                tz = tail[tail.index(ch):]
                break
        raw = head + ("." + frac if frac else "") + tz
    try:
        dt = _dt.datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return int(dt.timestamp())


def _items_added_this_week(created: list, now: float) -> int:
    """Count of items whose ``DateCreated`` is within the last 7 days. ``created``
    is the newest-first ISO list from ``_recent_created`` — since it's sorted, we
    stop at the first out-of-window item. Returns up to ``_RECENT_CREATED_CAP``
    (the SPA renders the cap value as "N+")."""
    cutoff = int(now) - _WEEK_SECONDS
    n = 0
    for s in created:
        if _parse_iso_epoch(s) >= cutoff:
            n += 1
        else:
            break  # newest-first → the rest are older
    return n


async def _users_count(cli: "httpx.AsyncClient", base: str, key: str,
                       scheme: str) -> int:
    """Total user count from ``GET /Users`` (the user list). Best-effort: 0 on a
    non-200 / parse failure."""
    try:
        r = await cli.get(base + "/Users", headers=headers(key, scheme))
        if r.status_code != 200:
            return 0
        return len([u for u in as_list(r.json()) if isinstance(u, dict)])
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return 0


async def _resume_count(cli: "httpx.AsyncClient", base: str, key: str,
                        scheme: str) -> int:
    """Server-wide "Continue watching" / on-deck count — items the server marks
    resumable (``Filters=IsResumable``). Returns the ``TotalRecordCount``.
    Best-effort: 0 on a non-200 / parse failure OR on versions that scope
    ``IsResumable`` to a per-user context (the card hides the chip then)."""
    try:
        r = await cli.get(base + "/Items", headers=headers(key, scheme), params={
            "Recursive": "true", "Filters": "IsResumable",
            "IncludeItemTypes": "Movie,Episode,Video", "Limit": "0",
            "EnableTotalRecordCount": "true", "EnableImages": "false"})
        if r.status_code != 200:
            return 0
        return safe_int(as_dict(r.json()).get("TotalRecordCount"))
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return 0


def version_from(resp) -> str:
    """Server version from a ``GET /System/Info`` body ('' on any non-200 /
    parse failure — version is never load-bearing)."""
    try:
        if getattr(resp, "status_code", 0) != 200:
            return ""
        return str(as_dict(resp.json()).get("Version") or "").strip()
    except (ValueError, TypeError, AttributeError):
        return ""


async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          cfg: Config) -> dict:
    """Probe the auth-required ``/System/Info`` with the supplied API key.
    Returns ``{ok, detail, status}``. Falls back to the chip's stored
    ``api_key`` when ``candidate_key`` is blank so the operator can re-test
    after first save without retyping."""
    key, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + "/System/Info"
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=headers(key, cfg.scheme))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        ver = version_from(r)
        return {"ok": True, "detail": f"OK ({cfg.brand} {ver})" if ver else "OK",
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": f"auth failed (check the {cfg.brand} API key)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


async def fetch_data(host_row: dict, chip: dict, *, host_id: str,
                     service_idx: int, force: bool, cfg: Config,
                     cache: dict) -> dict:
    """Fetch the library summary (+ active sessions + version) for the card.

    Returns ``{available, movies, series, episodes, songs, libraries,
    sessions_active, version, fetched_at}``. Raises ``ValueError`` /
    ``RuntimeError`` (caller maps to HTTPException) when the API key is unset /
    the base URL won't resolve / the load-bearing counts call errors."""
    key = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=key, log_tag=cfg.log_tag)
    if hit is not None:
        return hit
    counts_url = base + "/Items/Counts"
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            cr = await cli.get(counts_url, headers=headers(key, cfg.scheme))
            if cr.status_code != 200:
                print(f"[{cfg.log_tag}] error: fetch host={host_id} url={cr.request.url} "
                      f"returned HTTP {cr.status_code} (check the chip URL points at the "
                      f"{cfg.brand} root, e.g. http://{cfg.log_tag}.example.com:8096)")
                if cr.status_code in (401, 403):
                    raise RuntimeError(f"upstream auth failed: HTTP {cr.status_code} "
                                       f"(check the {cfg.brand} API key) — {counts_url}")
                raise RuntimeError(f"upstream returned HTTP {cr.status_code} for {counts_url}")
            try:
                counts = as_dict(cr.json())
            except (ValueError, TypeError):
                raise RuntimeError("upstream returned non-JSON")
            # Active playback sessions — count sessions WITH a NowPlayingItem
            # (``/Sessions`` returns idle sessions too). Nice-to-have; a failure
            # must NOT fail the card.
            sessions_active = 0
            transcodes = 0
            bandwidth_bps = 0
            active_users: set = set()
            active_devices: set = set()
            try:
                sr = await cli.get(base + "/Sessions", headers=headers(key, cfg.scheme))
                if sr.status_code == 200:
                    for s in as_list(sr.json()):
                        if not (isinstance(s, dict) and s.get("NowPlayingItem")):
                            continue
                        sessions_active += 1
                        ti = as_dict(s.get("TranscodingInfo"))
                        method = str(as_dict(s.get("PlayState")).get("PlayMethod") or "").lower()
                        if "transcode" in method or ti:
                            transcodes += 1
                        # Bandwidth (bps): the transcode TARGET bitrate when
                        # transcoding, else the source item's own bitrate.
                        bandwidth_bps += (safe_int(ti.get("Bitrate"))
                                          or safe_int(as_dict(s.get("NowPlayingItem")).get("Bitrate")))
                        # Distinct watchers + distinct devices among the active
                        # sessions (a user on 2 devices = 1 user / 2 devices).
                        uid = str(s.get("UserId") or s.get("UserName") or "").strip()
                        if uid:
                            active_users.add(uid)
                        did = str(s.get("DeviceId") or s.get("DeviceName") or "").strip()
                        if did:
                            active_devices.add(did)
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                sessions_active = transcodes = bandwidth_bps = 0
                active_users = set()
                active_devices = set()
            # Items added in the last 7 days — a bounded recently-added pull
            # (DateCreated desc) counted against a 7-day cutoff. Best-effort: a
            # failure leaves the count at 0 (the card hides the chip then). Capped
            # so a huge library can't bloat the payload; the count shows "N+" in
            # the SPA when the cap is hit.
            items_this_week = _items_added_this_week(
                await _recent_created(cli, base, key, cfg.scheme), now)
            # Total user count (GET /Users) — best-effort, 0 on failure.
            users_total = await _users_count(cli, base, key, cfg.scheme)
            # On-deck / resume count (items the server marks resumable, i.e.
            # "Continue watching"). Best-effort: 0 on versions that scope
            # IsResumable to a user context (the card hides the chip then).
            resume_count = await _resume_count(cli, base, key, cfg.scheme)
            # Version (nice-to-have).
            try:
                version = version_from(
                    await cli.get(base + "/System/Info", headers=headers(key, cfg.scheme)))
            except (httpx.HTTPError, OSError):
                version = ""
            # All libraries (name + collection type + item count) — the card
            # LISTS each so a multi-library server shows every library + a
            # grand-total item count, not just Movies/Series/Songs. Best-effort.
            library_list = await _library_list(cli, base, key, cfg.scheme)
            libraries = len(library_list)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[{cfg.log_tag}] error: fetch host={host_id} url={counts_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    # Top-grid stats: PREFER the per-library Recursive counts (by CollectionType)
    # over /Items/Counts — on a server whose media live in custom-typed libraries
    # /Items/Counts reports MovieCount=1 / SeriesCount=0 etc. (wrong), while the
    # per-library counts are real. Fall back to /Items/Counts only when no
    # library of that type exists.
    lib_counts = _counts_from_libraries(library_list)
    out: dict[str, Any] = {
        "available": True,
        "movies": lib_counts["movies"] or safe_int(counts.get("MovieCount")),
        "series": lib_counts["series"] or safe_int(counts.get("SeriesCount")),
        "episodes": safe_int(counts.get("EpisodeCount")),
        "songs": lib_counts["songs"] or safe_int(counts.get("SongCount")),
        "items_total": _items_total(counts, library_list),
        "libraries": libraries,
        "library_list": library_list,
        "sessions_active": sessions_active,
        "transcodes": transcodes,
        "direct_streams": max(0, sessions_active - transcodes),
        "bandwidth_bps": bandwidth_bps,
        "active_users": len(active_users),
        "active_devices": len(active_devices),
        "items_this_week": items_this_week,
        "items_this_week_capped": items_this_week >= _RECENT_CREATED_CAP,
        "users_total": users_total,
        "resume_count": resume_count,
        "version": version,
        "fetched_at": int(now),
    }
    # Best-effort streaming trend from the shared lifespan emby_sampler (peak-
    # streams-today + the daily peak-streams sparkline). A missing sampler / no
    # samples yet leaves the card's instantaneous stats untouched.
    out["trend"] = _safe_trend(host_id, service_idx)
    print(f"[{cfg.log_tag}] INFO fetched host={host_id} movies={out['movies']} "
          f"series={out['series']} episodes={out['episodes']} songs={out['songs']} "
          f"libraries={libraries} sessions={sessions_active} "
          f"transcodes={transcodes} bw={bandwidth_bps}bps "
          f"users={out['active_users']} week={items_this_week}")
    cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def _safe_trend(host_id: str, service_idx: int) -> Optional[dict]:
    """Best-effort streaming trend for the card — the shared emby_sampler's
    per-chip ``trend_summary``. Returns ``None`` (never raises) when the sampler
    isn't importable / errors, so a trend hiccup can't fail the card."""
    try:
        from logic.apps import emby_sampler as _sampler  # noqa: PLC0415
        return _sampler.trend_summary(host_id, int(service_idx))
    except Exception as e:  # noqa: BLE001
        print(f"[emby] trend_summary({host_id}#{service_idx}) skipped: {e}")
        return None


def peek_latest(host_id: str, service_idx: int, *, cache: dict) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "movies": safe_int(data.get("movies")),
        "series": safe_int(data.get("series")),
        "episodes": safe_int(data.get("episodes")),
        "songs": safe_int(data.get("songs")),
        "items_total": safe_int(data.get("items_total")),
        "libraries": safe_int(data.get("libraries")),
        "sessions_active": safe_int(data.get("sessions_active")),
        "transcodes": safe_int(data.get("transcodes")),
        "direct_streams": safe_int(data.get("direct_streams")),
        "bandwidth_bps": safe_int(data.get("bandwidth_bps")),
        "active_users": safe_int(data.get("active_users")),
        "active_devices": safe_int(data.get("active_devices")),
        "items_this_week": safe_int(data.get("items_this_week")),
        "users_total": safe_int(data.get("users_total")),
        "resume_count": safe_int(data.get("resume_count")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


def _resolve_skill_target(host_row: dict, chip: dict, *,
                          cfg: Config) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(key, base)`` or a ready ``{ok: False, detail}`` error dict."""
    key = (chip.get("api_key") or "").strip()
    if not key:
        return "", "", {"ok": False, "status": 0,
                        "detail": f"{cfg.brand} API key not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return key, base, None


async def _skill_request(method: str, base: str, path: str, *, key: str,
                         cfg: Config, timeout: float, verb: str,
                         params: Optional[dict] = None,
                         guard: bool = True) -> "httpx.Response | dict":
    """Shared HTTP call (+ optional 401 / 403 / non-200 guard) for a read /
    action skill. Returns the 200 OK response, or a ready ``{ok: False, ...}``
    error dict when the call itself fails OR (when ``guard``) the status isn't
    200. ``guard=False`` returns the raw response so a caller that accepts other
    codes (scan's POST → 202 / 204) does its own status check. The caller
    discriminates with one ``isinstance(r, dict)`` (which also narrows the
    response type for the type checker)."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=timeout,
                                     follow_redirects=True) as cli:
            if method == "POST":
                r = await cli.post(base + path, headers=headers(key, cfg.scheme))
            else:
                r = await cli.get(base + path, headers=headers(key, cfg.scheme),
                                  params=params)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"{verb} failed: {type(e).__name__}: {e}"}
    return (_status_guard(r, cfg) or r) if guard else r


def _auth_error(r: "httpx.Response", cfg: Config) -> Optional[dict]:
    """The shared 401 / 403 auth-failed error dict, or None when the response
    isn't an auth failure."""
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": f"auth failed (check the {cfg.brand} API key)"}
    return None


def _status_guard(r: "httpx.Response", cfg: Config) -> Optional[dict]:
    """Shared 401 / 403 + non-200 guard for a GET read skill. Returns a ready
    error dict, or None when the response is 200 OK."""
    ae = _auth_error(r, cfg)
    if ae:
        return ae
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    return None


def _parse_items(r: "httpx.Response") -> "list | dict":
    """Parse the ``Items`` array from a ``/Items`` response. Returns the list on
    success, or a ready non-JSON error dict on a parse failure (caller
    discriminates with ``isinstance(meta, dict)``)."""
    try:
        return as_list(as_dict(r.json()).get("Items"))
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}


def _attach_items(out: dict, items: list, count_i18n: str) -> dict:
    """Attach the rich-item list + count + count-i18n key to a skill result dict
    (no-op when there are no items). Returns ``out`` for one-line use."""
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = count_i18n
    return out


async def _fetch_items(base: str, path: str, *, key: str, cfg: Config,
                       timeout: float, verb: str, params: dict) -> "list | dict":
    """GET ``path`` + status guard + parse the ``Items`` array in one. Returns
    the list on success, or a ready error dict (request failure / non-200 /
    non-JSON). Shared by the recently-added + search skills (both hit ``/Items``
    then read ``Items``)."""
    r = await _skill_request("GET", base, path, key=key, cfg=cfg,
                             timeout=timeout, verb=verb, params=params)
    if isinstance(r, dict):
        return r
    return _parse_items(r)


def _lib_icon(coll_type: str) -> str:
    """A per-library-type emoji for the status breakdown."""
    t = (coll_type or "").lower()
    if t == "movies":
        return "🎬"
    if t == "tvshows":
        return "📺"
    if t == "music":
        return "🎵"
    if t in ("homevideos", "photos"):
        return "📷"
    if t == "books":
        return "📚"
    return "🗂️"


async def status_skill(host_row: dict, chip: dict, *,
                       host_id: Optional[str], service_idx: Optional[int],
                       cfg: Config, cache: dict) -> dict:
    """Read-only: live-fetch the library summary (force-bypasses the cache) and
    return a formatted ``detail`` — the aggregate counts PLUS every library
    listed individually (name + item count). Never raises."""
    print(f"[{cfg.log_tag}] INFO {cfg.slug}_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True,
                                cfg=cfg, cache=cache)
    except (ValueError, RuntimeError) as e:
        print(f"[{cfg.log_tag}] warning: {cfg.slug}_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    movies = safe_int(data.get("movies"))
    series = safe_int(data.get("series"))
    episodes = safe_int(data.get("episodes"))
    songs = safe_int(data.get("songs"))
    sessions = safe_int(data.get("sessions_active"))
    items_total = safe_int(data.get("items_total"))
    lines = [
        f"🎬 Movies: {movies:,}",
        f"📺 Series: {series:,} ({episodes:,} episodes)",
    ]
    if songs:
        lines.append(f"🎵 Songs: {songs:,}")
    lines.append(f"{'▶️' if sessions else '⏸️'} Now playing: {sessions:,}")
    # Per-library breakdown — list EVERY library (name + item count), not just
    # the three aggregate buckets, so custom / mixed libraries are visible.
    lib_list = as_list(data.get("library_list"))
    if lib_list:
        lines.append(f"📚 Libraries ({len(lib_list)}, {items_total:,} items total):")
        for lib in lib_list:
            if not isinstance(lib, dict):
                continue
            nm = str(lib.get("name") or "?").strip()
            cnt = safe_int(lib.get("count"))
            coll = str(lib.get("type") or "").strip()
            lines.append(f"  {_lib_icon(coll)} {nm}: {cnt:,}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "movies": movies, "series": series, "episodes": episodes,
        "songs": songs, "sessions_active": sessions,
    }


def _session_line(s: dict) -> str:
    """One now-playing line: ``▶️ <user> — <title> (<pct>%) on <device>``."""
    if not isinstance(s, dict):
        return ""
    now = as_dict(s.get("NowPlayingItem"))
    if not now:
        return ""
    user = str(s.get("UserName") or "someone").strip()
    device = str(s.get("DeviceName") or s.get("Client") or "").strip()
    name = str(now.get("Name") or "").strip()
    series = str(now.get("SeriesName") or "").strip()
    label = (f"{series} — {name}" if series else name) or "?"
    runtime = safe_int(now.get("RunTimeTicks"))
    pos = safe_int(as_dict(s.get("PlayState")).get("PositionTicks"))
    pct = f" ({round(pos / runtime * 100)}%)" if (runtime and pos) else ""
    where = f" on {device}" if device else ""
    return f"▶️ {user} — {label}{pct}{where}"


def _session_item(s: dict, cfg: Config) -> Optional[dict]:
    """One now-playing session as a rich skill-result item: title poster (series
    poster for episodes, else the item primary — proxied), the watching user +
    their avatar (proxied, when set), a device / state / play-method subtitle,
    a play-progress bar, and a per-row ⏹ Stop ``row_action`` (terminate THIS
    session by its id). Parallel to ``_session_line``."""
    if not isinstance(s, dict):
        return None
    now = as_dict(s.get("NowPlayingItem"))
    if not now:
        return None
    user = str(s.get("UserName") or "").strip()
    user_id = str(s.get("UserId") or "").strip()
    avatar_tag = str(s.get("UserPrimaryImageTag") or "").strip()
    device = str(s.get("DeviceName") or s.get("Client") or "").strip()
    ps = as_dict(s.get("PlayState"))
    name = str(now.get("Name") or "").strip()
    series = str(now.get("SeriesName") or "").strip()
    label = (f"{series} — {name}" if series else name) or "?"
    series_id = str(now.get("SeriesId") or "").strip()
    item_id = str(now.get("Id") or "").strip()
    poster_id = series_id or item_id
    runtime = safe_int(now.get("RunTimeTicks"))
    pos = safe_int(ps.get("PositionTicks"))
    state = "paused" if ps.get("IsPaused") else "playing"
    method = str(ps.get("PlayMethod") or "").strip()  # DirectPlay / Transcode
    out: dict = {"title": label,
                 "subtitle": " · ".join(p for p in (device, state, method) if p)}
    if poster_id:
        out["poster"] = f"/Items/{poster_id}/Images/Primary"
        out["poster_proxy"] = True
    if runtime and pos:
        out["progress"] = round(pos / runtime * 100)
    if user:
        out["byline"] = user
        if user_id and avatar_tag:
            out["avatar"] = f"/Users/{user_id}/Images/Primary?tag={avatar_tag}"
            out["avatar_proxy"] = True
    # Per-row ⏹ Stop button → terminate THIS session by its id, confirm-gated
    # (it kicks the viewer off).
    sess_id = str(s.get("Id") or "").strip()
    if sess_id:
        out["row_action"] = {
            "skill_id": f"{cfg.slug}_terminate_session",
            "arg": sess_id,
            "destructive": True,
            "icon": "x",
            "title_i18n": f"apps.{cfg.slug}.stop_stream",
            "confirm_i18n": f"apps.{cfg.slug}.stop_stream_confirm",
            "confirm_text_i18n": f"apps.{cfg.slug}.stop_stream",
        }
    return out


# noinspection DuplicatedCode
#   The two-list build below (text lines + rich items per active session) is
#   shape-similar to the other list skills but has no clean shared helper —
#   each skill's loop body differs (session vs media-item vs search-result).
async def now_playing_skill(host_row: dict, chip: dict, *,
                            host_id: Optional[str], cfg: Config) -> dict:
    """Read-only: list the active playback sessions (who's watching what) from
    ``GET /Sessions`` (filtered to sessions with a NowPlayingItem). Never
    raises."""
    key, base, err = _resolve_skill_target(host_row, chip, cfg=cfg)
    if err:
        return err
    print(f"[{cfg.log_tag}] INFO {cfg.slug}_now_playing host={host_id} (live fetch)")
    r = await _skill_request("GET", base, "/Sessions", key=key, cfg=cfg,
                             timeout=15.0, verb="fetch")
    if isinstance(r, dict):
        return r
    try:
        sessions = [s for s in as_list(r.json())
                    if isinstance(s, dict) and s.get("NowPlayingItem")]
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not sessions:
        return {"ok": True, "status": 200,
                "detail": f"⏸️ Nothing is playing on {cfg.brand} right now."}
    lines = [f"▶️ {len(sessions):,} stream(s) playing:"]
    items: list[dict] = []
    for s in sessions[:10]:
        ln = _session_line(s)
        if ln:
            lines.append("  " + ln)
        it = _session_item(s, cfg)
        if it:
            items.append(it)
    out: dict = {"ok": True, "status": 200, "detail": "\n".join(lines)}
    return _attach_items(out, items, f"apps.{cfg.slug}.now_playing_count")


async def terminate_session_skill(host_row: dict, chip: dict, *,
                                  arg: Optional[str], host_id: Optional[str],
                                  cfg: Config) -> dict:
    """DESTRUCTIVE (arg): stop ONE active playback session. Resolves the target
    from ``GET /Sessions`` — an exact session ``Id`` (the per-row Stop button)
    first, else a substring match on the watcher / title (the AI / Telegram
    free-text path) — then ``POST /Sessions/{id}/Playing/Stop`` (the Playstate
    Stop command, supported by both Emby and Jellyfin). Never raises."""
    needle = (arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no stream given (say e.g. \"stop John's stream\")"}
    key, base, err = _resolve_skill_target(host_row, chip, cfg=cfg)
    if err:
        return err
    nl = needle.lower()
    print(f"[{cfg.log_tag}] INFO {cfg.slug}_terminate_session host={host_id} target={needle!r}")
    r = await _skill_request("GET", base, "/Sessions", key=key, cfg=cfg,
                             timeout=15.0, verb="fetch")
    if isinstance(r, dict):
        return r
    try:
        sessions = as_list(r.json())
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    target_id = ""
    target_label = ""
    for s in sessions:
        if not (isinstance(s, dict) and s.get("NowPlayingItem")):
            continue
        sid = str(s.get("Id") or "").strip()
        now = as_dict(s.get("NowPlayingItem"))
        title = str(now.get("SeriesName") or now.get("Name") or "").strip()
        user = str(s.get("UserName") or "").strip()
        if sid and sid == needle:  # exact id — the per-row Stop button
            target_id, target_label = sid, f"{user} — {title}".strip(" —")
            break
        if not target_id and (nl in title.lower() or nl in user.lower()):
            target_id, target_label = sid, f"{user} — {title}".strip(" —")
    if not target_id:
        return {"ok": False, "status": 404,
                "detail": f"no active {cfg.brand} stream matched \"{needle}\""}
    sr = await _skill_request("POST", base, f"/Sessions/{target_id}/Playing/Stop",
                              key=key, cfg=cfg, timeout=15.0, verb="stop", guard=False)
    if isinstance(sr, dict):
        return sr
    ae = _auth_error(sr, cfg)
    if ae:
        return ae
    if sr.status_code not in (200, 202, 204):
        return {"ok": False, "status": sr.status_code, "detail": f"HTTP {sr.status_code}"}
    return {"ok": True, "status": 200,
            "detail": f"🛑 Stopped {target_label or ('the ' + cfg.brand + ' stream')}."}


# Recently-added: per-library fetch breadth. Items are pulled PER library (by
# ParentId) so each is correctly attributed to the library it lives in — a flat
# /Items pull only knows an item's TYPE (movie/episode/…), not which (possibly
# custom-named) library it belongs to.
_RECENT_PER_LIB = 6  # items pulled per library
_RECENT_LIB_CAP = 12  # max libraries queried (bounds the fan-out)
_RECENT_TOTAL_CAP = 30  # max rich items returned across all libraries
_RECENT_ITEM_TYPES = "Movie,Episode,Series,MusicAlbum,MusicVideo,Video,Audio"


def _recent_row(item: dict) -> Optional[dict]:
    """Build one recently-added rich row (poster + title + subtitle) from a
    media item. ``None`` for a container / unnamed item. The caller stamps the
    library ``group``."""
    if not isinstance(item, dict):
        return None
    typ = str(item.get("Type") or "").lower()
    yr = str(item.get("ProductionYear") or "").strip()
    sub_parts: list[str] = []
    if typ in ("episode", "season"):
        title = str(item.get("SeriesName") or item.get("Name") or "?").strip()
        ep = str(item.get("Name") or "").strip()
        if ep and ep != title:
            sub_parts.append(ep)
        poster_id = str(item.get("SeriesId") or item.get("Id") or "").strip()
    elif typ in ("musicalbum", "audio", "musicartist"):
        title = str(item.get("AlbumArtist") or item.get("Name") or "?").strip()
        album = str(item.get("Name") or "").strip()
        if album and album != title:
            sub_parts.append(album)
        poster_id = str(item.get("Id") or "").strip()
    else:  # movie / series / video / musicvideo / anything else
        title = str(item.get("Name") or "?").strip()
        if yr:
            sub_parts.append(yr)
        poster_id = str(item.get("Id") or "").strip()
    if not title or title == "?":
        return None
    row: "dict[str, Any]" = {
        "title": title + (f" ({yr})" if typ == "movie" and yr else ""),
        "subtitle": " · ".join(sub_parts)}
    if poster_id:
        row["poster"] = f"/Items/{poster_id}/Images/Primary"
        row["poster_proxy"] = True
    return row


async def _fetch_lib_recent(cli: "httpx.AsyncClient", base: str, key: str,
                            cfg: Config, vf: dict) -> Optional[dict]:
    """Most-recently-added items for ONE library (``ParentId=<lib id>`` sorted by
    DateCreated desc). Returns ``{name, rows, newest}`` or ``None`` (no id / no
    items / a per-library failure — best-effort)."""
    lib_id = (vf.get("id") or "").strip()
    name = str(vf.get("name") or "").strip()
    if not lib_id or not name:
        return None
    try:
        r = await cli.get(base + "/Items", headers=headers(key, cfg.scheme), params={
            "ParentId": lib_id, "Recursive": "true",
            "SortBy": "DateCreated,SortName", "SortOrder": "Descending",
            "Limit": str(_RECENT_PER_LIB),
            "IncludeItemTypes": _RECENT_ITEM_TYPES,
            "Fields": "ProductionYear,SeriesName,DateCreated",
            "ImageTypeLimit": "1"})
        if r.status_code != 200:
            return None
        items = as_list(as_dict(r.json()).get("Items"))
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return None
    rows: list = []
    newest = ""
    for item in items:
        row = _recent_row(item)
        if not row:
            continue
        row["group"] = name  # the actual library name (raw text divider)
        row["group_raw"] = True  # render verbatim, not via t()
        rows.append(row)
        dc = str(as_dict(item).get("DateCreated") or "")
        if dc > newest:
            newest = dc
    if not rows:
        return None
    return {"name": name, "rows": rows, "newest": newest}


async def _recent_flat(cli: "httpx.AsyncClient", base: str, key: str,
                       cfg: Config) -> dict:
    """Fallback recently-added — a single flat date-sorted list (no library
    grouping) used only when ``/Library/VirtualFolders`` is unavailable."""
    try:
        r = await cli.get(base + "/Items", headers=headers(key, cfg.scheme), params={
            "SortBy": "DateCreated,SortName", "SortOrder": "Descending",
            "Recursive": "true", "Limit": str(_RECENT_TOTAL_CAP),
            "IncludeItemTypes": _RECENT_ITEM_TYPES,
            "Fields": "ProductionYear,SeriesName,DateCreated", "ImageTypeLimit": "1"})
        if r.status_code != 200:
            return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
        items = as_list(as_dict(r.json()).get("Items"))
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return {"ok": False, "status": 0, "detail": "fetch failed"}
    rows: list = []
    for item in items:
        row = _recent_row(item)
        if row:
            rows.append(row)
        if len(rows) >= _RECENT_TOTAL_CAP:
            break
    if not rows:
        return {"ok": True, "status": 200, "detail": f"🆕 Nothing recently added to {cfg.brand}."}
    lines = [f"  • {r['title']}" + (f" — {r['subtitle']}" if r.get("subtitle") else "")
             for r in rows]
    out: dict = {"ok": True, "status": 200,
                 "detail": f"🆕 Recently added to {cfg.brand}:\n" + "\n".join(lines)}
    return _attach_items(out, rows, "apps.tautulli.recent_count")


async def recently_added_skill(host_row: dict, chip: dict, *,
                               host_id: Optional[str], cfg: Config) -> dict:
    """Read-only: the most recently added items, grouped by the actual LIBRARY
    they belong to (each library queried by ParentId so the attribution is
    correct), newest-library first, with poster thumbnails. Never raises."""
    key, base, err = _resolve_skill_target(host_row, chip, cfg=cfg)
    if err:
        return err
    print(f"[{cfg.log_tag}] INFO {cfg.slug}_recently_added host={host_id} (live fetch, per-library)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            folders = await _virtual_folders(cli, base, key, cfg.scheme)
            if not folders:
                # No library list — fall back to a flat ungrouped pull.
                return await _recent_flat(cli, base, key, cfg)
            results = await asyncio.gather(
                *[_fetch_lib_recent(cli, base, key, cfg, vf)
                  for vf in folders[:_RECENT_LIB_CAP]],
                return_exceptions=True)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"fetch failed: {type(e).__name__}: {e}"}
    libs = [x for x in results if isinstance(x, dict) and x.get("rows")]
    if not libs:
        return {"ok": True, "status": 200, "detail": f"🆕 Nothing recently added to {cfg.brand}."}
    # Newest-library first (the library with the most recent addition leads).
    libs.sort(key=lambda x: x.get("newest") or "", reverse=True)
    rich: list = []
    text_lines: list = []
    for lib in libs:
        if len(rich) >= _RECENT_TOTAL_CAP:
            break
        rows = lib["rows"][:_RECENT_TOTAL_CAP - len(rich)]
        if not rows:
            continue
        rich.extend(rows)
        text_lines.append(f"📚 {lib['name']}:")
        text_lines += [f"  • {r['title']}" + (f" — {r['subtitle']}" if r.get("subtitle") else "")
                       for r in rows]
    out: dict = {"ok": True, "status": 200,
                 "detail": f"🆕 Recently added to {cfg.brand}:\n" + "\n".join(text_lines)}
    return _attach_items(out, rich, "apps.tautulli.recent_count")


def _search_title(item: dict) -> str:
    """Display title for a search result: ``Title (Year)`` for movies, the show
    name for episodes/series, else the bare name."""
    if not isinstance(item, dict):
        return ""
    typ = str(item.get("Type") or "").lower()
    if typ == "episode":
        series = str(item.get("SeriesName") or "").strip()
        name = str(item.get("Name") or "").strip()
        return (f"{series} — {name}" if series else name).strip()
    name = str(item.get("Name") or "").strip()
    yr = safe_int(item.get("ProductionYear"))
    return name + (f" ({yr})" if yr and typ == "movie" else "")


async def search_skill(host_row: dict, chip: dict, *,
                       arg: Optional[str], host_id: Optional[str],
                       cfg: Config) -> dict:
    """Read-only (arg): search the library via ``GET /Items?searchTerm=`` and
    return the top results. Never raises."""
    term = (arg or "").strip()
    if not term:
        return {"ok": False, "status": 0,
                "detail": f"no search term given — say e.g. 'search {cfg.slug} for Inception'"}
    key, base, err = _resolve_skill_target(host_row, chip, cfg=cfg)
    if err:
        return err
    print(f"[{cfg.log_tag}] INFO {cfg.slug}_search host={host_id} term={term!r} (live search)")
    params = {
        "searchTerm": term,
        "Recursive": "true",
        "Limit": "10",
        "IncludeItemTypes": "Movie,Series,Episode,MusicAlbum",
        "Fields": "ProductionYear,SeriesName",
    }
    meta = await _fetch_items(base, "/Items", key=key, cfg=cfg,
                              timeout=20.0, verb="search", params=params)
    if isinstance(meta, dict):
        return meta
    seen: set = set()
    results: list[str] = []
    for item in meta:
        if not isinstance(item, dict):
            continue
        t = _search_title(item)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            typ = str(item.get("Type") or "").lower()
            icon = "📺" if typ in ("episode", "series", "season") else "🎬"
            results.append(f"  {icon} {t}")
        if len(results) >= 10:
            break
    if not results:
        return {"ok": True, "status": 200,
                "detail": f"🔍 No {cfg.brand} library matches for “{term}”."}
    return {"ok": True, "status": 200,
            "detail": f"🔍 {cfg.brand} results for “{term}”:\n" + "\n".join(results)}


async def scan_skill(host_row: dict, chip: dict, *,
                     host_id: Optional[str], cfg: Config) -> dict:
    """Action skill: trigger a scan (re-index) of every library via
    ``POST /Library/Refresh``. Non-destructive. Never raises."""
    key, base, err = _resolve_skill_target(host_row, chip, cfg=cfg)
    if err:
        return err
    print(f"[{cfg.log_tag}] INFO {cfg.slug}_scan host={host_id} (library refresh)")
    r = await _skill_request("POST", base, "/Library/Refresh", key=key, cfg=cfg,
                             timeout=20.0, verb="scan", guard=False)
    if isinstance(r, dict):
        return r
    ae = _auth_error(r, cfg)
    if ae:
        return ae
    if r.status_code not in (200, 202, 204):
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    return {"ok": True, "status": 200,
            "detail": f"🔄 Started a {cfg.brand} library scan."}


async def scan_library_skill(host_row: dict, chip: dict, *,
                             arg: Optional[str], host_id: Optional[str],
                             cfg: Config) -> dict:
    """Action skill (arg): re-scan ONE library by name. Resolves the term
    against the live library list (exact name first, else substring) then
    ``POST /Items/{libraryId}/Refresh`` (the per-library re-index, supported by
    both Emby and Jellyfin). Non-destructive. Never raises."""
    needle = (arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": f"no library given — say e.g. 'scan the Movies library on {cfg.slug}'"}
    key, base, err = _resolve_skill_target(host_row, chip, cfg=cfg)
    if err:
        return err
    nl = needle.lower()
    print(f"[{cfg.log_tag}] INFO {cfg.slug}_scan_library host={host_id} target={needle!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            folders = await _virtual_folders(cli, base, key, cfg.scheme)
            target_id = ""
            target_name = ""
            # Exact name match first, then substring.
            for vf in folders:
                if str(vf.get("name") or "").strip().lower() == nl:
                    target_id, target_name = vf.get("id") or "", vf.get("name") or ""
                    break
            if not target_id:
                for vf in folders:
                    if nl in str(vf.get("name") or "").strip().lower():
                        target_id, target_name = vf.get("id") or "", vf.get("name") or ""
                        break
            if not target_id:
                return {"ok": False, "status": 404,
                        "detail": f"no {cfg.brand} library matched \"{needle}\""}
            rr = await cli.post(base + f"/Items/{target_id}/Refresh",
                                headers=headers(key, cfg.scheme))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"scan failed: {type(e).__name__}: {e}"}
    ae = _auth_error(rr, cfg)
    if ae:
        return ae
    if rr.status_code not in (200, 202, 204):
        return {"ok": False, "status": rr.status_code, "detail": f"HTTP {rr.status_code}"}
    return {"ok": True, "status": 200,
            "detail": f"🔄 Started a scan of the “{target_name or needle}” library on {cfg.brand}."}


# noinspection DuplicatedCode
#   The sessions-fetch + parse + active-filter preamble is shape-similar to
#   now_playing / terminate_session but each skill's body differs after it.
async def send_message_skill(host_row: dict, chip: dict, *,
                             arg: Optional[str], host_id: Optional[str],
                             cfg: Config) -> dict:
    """Action skill (arg): broadcast a short message to every ACTIVE session
    (a toast on each watcher's client) via ``POST /Sessions/{id}/Message``
    (the DisplayMessage command, supported by both Emby and Jellyfin).
    Non-destructive — it only shows a notification, never interrupts playback.
    ``arg`` is the message text. Never raises."""
    text = (arg or "").strip()
    if not text:
        return {"ok": False, "status": 0,
                "detail": "no message given — say e.g. 'tell everyone dinner's ready'"}
    key, base, err = _resolve_skill_target(host_row, chip, cfg=cfg)
    if err:
        return err
    print(f"[{cfg.log_tag}] INFO {cfg.slug}_send_message host={host_id} text={text!r}")
    r = await _skill_request("GET", base, "/Sessions", key=key, cfg=cfg,
                             timeout=15.0, verb="fetch")
    if isinstance(r, dict):
        return r
    try:
        sessions = [s for s in as_list(r.json())
                    if isinstance(s, dict) and s.get("NowPlayingItem")]
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not sessions:
        return {"ok": True, "status": 200,
                "detail": f"⏸️ No active {cfg.brand} sessions to message right now."}
    body = {"Header": "OmniGrid", "Text": text, "TimeoutMs": 8000}
    sent = 0
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            for s in sessions:
                sid = str(s.get("Id") or "").strip()
                if not sid:
                    continue
                try:
                    mr = await cli.post(base + f"/Sessions/{sid}/Message",
                                        headers=headers(key, cfg.scheme), json=body)
                    if mr.status_code in (200, 202, 204):
                        sent += 1
                except (httpx.HTTPError, OSError):
                    continue
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"send failed: {type(e).__name__}: {e}"}
    if sent == 0:
        return {"ok": False, "status": 502,
                "detail": f"couldn't deliver the message to any {cfg.brand} session"}
    return {"ok": True, "status": 200,
            "detail": f"💬 Sent “{text}” to {sent} {cfg.brand} session{'s' if sent != 1 else ''}."}


async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str], service_idx: Optional[int],
                    arg: Optional[str], cfg: Config, cache: dict) -> dict:
    """Dispatch one of the brand's SKILLS (ids are ``<slug>_<verb>``). Raises
    ValueError on an unknown id (route maps to HTTP 404)."""
    if skill_id == f"{cfg.slug}_status":
        return await status_skill(host_row, chip, host_id=host_id,
                                  service_idx=service_idx, cfg=cfg, cache=cache)
    if skill_id == f"{cfg.slug}_now_playing":
        return await now_playing_skill(host_row, chip, host_id=host_id, cfg=cfg)
    if skill_id == f"{cfg.slug}_terminate_session":
        return await terminate_session_skill(host_row, chip, arg=arg,
                                             host_id=host_id, cfg=cfg)
    if skill_id == f"{cfg.slug}_recently_added":
        return await recently_added_skill(host_row, chip, host_id=host_id, cfg=cfg)
    if skill_id == f"{cfg.slug}_search":
        return await search_skill(host_row, chip, arg=arg, host_id=host_id, cfg=cfg)
    if skill_id == f"{cfg.slug}_scan":
        return await scan_skill(host_row, chip, host_id=host_id, cfg=cfg)
    if skill_id == f"{cfg.slug}_scan_library":
        return await scan_library_skill(host_row, chip, arg=arg, host_id=host_id, cfg=cfg)
    if skill_id == f"{cfg.slug}_send_message":
        return await send_message_skill(host_row, chip, arg=arg, host_id=host_id, cfg=cfg)
    raise ValueError(f"unknown skill: {skill_id!r}")


def build_skills(slug: str, brand: str) -> "tuple[dict, ...]":
    """The shared 5-skill set for a media-server brand, parameterised by slug
    (id prefix) + brand (display + AI phrases). Identical shape for Emby /
    Jellyfin; only the brand word differs."""
    b = brand
    return (
        {
            "id": f"{slug}_status",
            "name": f"{b} status",
            "ai_phrases": (f"{slug} status, {slug} library, how many movies on "
                           f"{slug}, how many shows, library size, what's on "
                           f"{slug}, {slug} media count, is anyone watching {slug}"),
            "destructive": False,
        },
        {
            "id": f"{slug}_now_playing",
            "name": f"What's playing on {b}",
            "ai_phrases": (f"what's playing on {slug}, who's watching {slug}, "
                           f"what is streaming on {slug}, active {slug} streams, "
                           f"now playing on {slug}, current {slug} sessions, "
                           f"is anyone streaming {slug}"),
            "destructive": False,
        },
        {
            "id": f"{slug}_terminate_session",
            "name": f"Stop a {b} stream",
            "ai_phrases": (f"stop the {slug} stream, kill <name>'s stream, "
                           f"terminate <title> on {slug}, stop <user>'s playback, "
                           f"end the {slug} stream, stop streaming <title>, "
                           f"kick someone off {slug}"),
            "arg": True,
            "arg_hint": "the watcher name or title of the stream to stop",
            "destructive": True,
        },
        {
            "id": f"{slug}_recently_added",
            "name": f"Recently added to {b}",
            "ai_phrases": (f"recently added to {slug}, what's new on {slug}, "
                           f"latest additions on {slug}, new movies on {slug}, "
                           f"new shows on {slug}, {slug} recently added"),
            "destructive": False,
        },
        {
            "id": f"{slug}_search",
            "name": f"Search {b}",
            "ai_phrases": (f"search {slug} for <title>, do i have <title> on "
                           f"{slug}, find <title> on {slug}, is <title> in my "
                           f"{slug} library, look up <title> on {slug}, {slug} "
                           f"search <title>"),
            "arg": True,
            "arg_hint": "the title (movie / show / album) to search the library for",
            "destructive": False,
        },
        {
            "id": f"{slug}_scan",
            "name": f"Scan {b} libraries",
            "ai_phrases": (f"scan {slug} libraries, refresh {slug}, update {slug} "
                           f"library, rescan {slug}, scan for new media on {slug}, "
                           f"{slug} library scan"),
            "destructive": False,
        },
        {
            "id": f"{slug}_scan_library",
            "name": f"Scan one {b} library",
            "ai_phrases": (f"scan the <name> library on {slug}, rescan my movies "
                           f"library, refresh the <name> library, re-index the "
                           f"<name> {slug} library, scan just the <name> library"),
            "arg": True,
            "arg_hint": "the name of the library to scan (e.g. Movies, TV Shows)",
            "destructive": False,
        },
        {
            "id": f"{slug}_send_message",
            "name": f"Send a message to {b} viewers",
            "ai_phrases": (f"send a message to {slug} viewers, tell everyone on "
                           f"{slug} <message>, broadcast a message on {slug}, "
                           f"notify {slug} watchers, message all {slug} sessions, "
                           f"pop a message on {slug} screens"),
            "arg": True,
            "arg_hint": "the message text to show on every active session's screen",
            "destructive": False,
        },
    )
