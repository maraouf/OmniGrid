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

# Item Type → group bucket for recently-added. Movie is `Movie`; TV is
# Series/Season/Episode; music is MusicArtist/MusicAlbum/Audio.
_SERIES_TYPES = frozenset({"series", "season", "episode"})
_MUSIC_TYPES = frozenset({"musicartist", "musicalbum", "audio"})

# group bucket → i18n heading key (shared with the Tautulli recently-added view).
_GROUP_I18N = {
    "movie": "apps.tautulli.group_movies",
    "music": "apps.tautulli.group_music",
    "series": "apps.tautulli.group_series",
}


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


async def _library_list(cli: "httpx.AsyncClient", base: str, key: str,
                        scheme: str) -> list:
    """Every library as ``[{name, type, count}]`` — ``/Library/VirtualFolders``
    for the name + collection type, plus a bounded per-library Recursive item
    count (``/Items?ParentId=<id>&Limit=0&EnableTotalRecordCount=true`` →
    ``TotalRecordCount``). Best-effort: a failed per-library count yields 0; an
    errored / empty VirtualFolders yields []. Capped at 30 libraries."""
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
        item_id = str(vf.get("ItemId") or "").strip()
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
        out.append({"name": name,
                    "type": str(vf.get("CollectionType") or "").strip(),
                    "count": count})
    return out


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
            try:
                sr = await cli.get(base + "/Sessions", headers=headers(key, cfg.scheme))
                if sr.status_code == 200:
                    sessions_active = sum(
                        1 for s in as_list(sr.json())
                        if isinstance(s, dict) and s.get("NowPlayingItem"))
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                sessions_active = 0
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
        "version": version,
        "fetched_at": int(now),
    }
    print(f"[{cfg.log_tag}] INFO fetched host={host_id} movies={out['movies']} "
          f"series={out['series']} episodes={out['episodes']} songs={out['songs']} "
          f"libraries={libraries} sessions={sessions_active}")
    cache[cache_key(host_id, service_idx)] = (now, out)
    return out


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


async def status_skill(host_row: dict, chip: dict, *,
                       host_id: Optional[str], service_idx: Optional[int],
                       cfg: Config, cache: dict) -> dict:
    """Read-only: live-fetch the library summary (force-bypasses the cache) and
    return a formatted ``detail``. Never raises."""
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
    lines = [
        f"🎬 Movies: {movies:,}",
        f"📺 Series: {series:,} ({episodes:,} episodes)",
    ]
    if songs:
        lines.append(f"🎵 Songs: {songs:,}")
    lines.append(f"{'▶️' if sessions else '⏸️'} Now playing: {sessions:,}")
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


def _session_item(s: dict) -> Optional[dict]:
    """One now-playing session as a rich skill-result item: title poster (series
    poster for episodes, else the item primary — proxied), the watching user +
    their avatar (proxied, when set), a device / state / play-method subtitle,
    and a play-progress bar. Parallel to ``_session_line``."""
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
        it = _session_item(s)
        if it:
            items.append(it)
    out: dict = {"ok": True, "status": 200, "detail": "\n".join(lines)}
    return _attach_items(out, items, f"apps.{cfg.slug}.now_playing_count")


def _item_group(typ: str) -> str:
    """Bucket an item Type into movie / series / music."""
    t = (typ or "").lower()
    if t == "movie":
        return "movie"
    if t in _MUSIC_TYPES:
        return "music"
    if t in _SERIES_TYPES:
        return "series"
    return "movie"


def _group_lines(heading: str, rows: list, *, with_subtitle: bool) -> list:
    """One recently-added group's text lines: ``heading`` then a ``• title``
    bullet per row (appending `` — subtitle`` when ``with_subtitle`` and the row
    has one). [] for an empty group. Shared by the Movies / Series / Music
    sections so they don't repeat the bullet-building."""
    if not rows:
        return []
    out = [heading]
    if with_subtitle:
        out += [f"  • {r['title']}" + (f" — {r['subtitle']}" if r.get("subtitle") else "")
                for r in rows]
    else:
        out += [f"  • {r['title']}" for r in rows]
    return out


async def recently_added_skill(host_row: dict, chip: dict, *,
                               host_id: Optional[str], cfg: Config) -> dict:
    """Read-only: list the most recently added items (``GET /Items`` sorted by
    DateCreated desc), grouped Movies / Series / Music with poster thumbnails.
    Never raises."""
    key, base, err = _resolve_skill_target(host_row, chip, cfg=cfg)
    if err:
        return err
    print(f"[{cfg.log_tag}] INFO {cfg.slug}_recently_added host={host_id} (live fetch)")
    params = {
        "SortBy": "DateCreated,SortName",
        "SortOrder": "Descending",
        "Recursive": "true",
        "Limit": "20",
        # Broad set so items from custom-typed libraries (home video / mixed /
        # music-video) show too — not just Movie/Episode/MusicAlbum. _item_group
        # buckets anything unrecognised into Movies so they still render.
        "IncludeItemTypes": "Movie,Episode,Series,MusicAlbum,MusicVideo,Video,Audio",
        "Fields": "ProductionYear,SeriesName",
        "ImageTypeLimit": "1",
    }
    meta = await _fetch_items(base, "/Items", key=key, cfg=cfg,
                              timeout=15.0, verb="fetch", params=params)
    if isinstance(meta, dict):
        return meta
    if not meta:
        return {"ok": True, "status": 200, "detail": f"🆕 Nothing recently added to {cfg.brand}."}
    movies: list[dict] = []
    series: list[dict] = []
    music: list[dict] = []
    for item in meta[:20]:
        if not isinstance(item, dict):
            continue
        grp = _item_group(str(item.get("Type") or ""))
        yr = str(item.get("ProductionYear") or "").strip()
        sub_parts: list[str] = []
        if grp == "series":
            title = str(item.get("SeriesName") or item.get("Name") or "?").strip()
            ep = str(item.get("Name") or "").strip()
            if ep and ep != title:
                sub_parts.append(ep)
            poster_id = str(item.get("SeriesId") or item.get("Id") or "").strip()
        elif grp == "music":
            title = str(item.get("AlbumArtist") or item.get("Name") or "?").strip()
            album = str(item.get("Name") or "").strip()
            if album and album != title:
                sub_parts.append(album)
            poster_id = str(item.get("Id") or "").strip()
        else:  # movie
            title = str(item.get("Name") or "?").strip()
            if yr:
                sub_parts.append(yr)
            poster_id = str(item.get("Id") or "").strip()
        if not title or title == "?":
            continue
        group_key = _GROUP_I18N.get(grp, "apps.tautulli.group_movies")
        row: "dict[str, Any]" = {
            "title": title + (f" ({yr})" if grp == "movie" and yr else ""),
            "subtitle": " · ".join(sub_parts),
            "group": group_key}
        if poster_id:
            row["poster"] = f"/Items/{poster_id}/Images/Primary"
            row["poster_proxy"] = True
        (movies if grp == "movie" else music if grp == "music" else series).append(row)
    rich = movies + series + music
    if not rich:
        return {"ok": True, "status": 200, "detail": f"🆕 Nothing recently added to {cfg.brand}."}
    lines = (_group_lines("🎬 Movies:", movies, with_subtitle=False)
             + _group_lines("📺 Series:", series, with_subtitle=True)
             + _group_lines("🎵 Music:", music, with_subtitle=True))
    out: dict = {"ok": True, "status": 200,
                 "detail": f"🆕 Recently added to {cfg.brand}:\n" + "\n".join(lines)}
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
    if skill_id == f"{cfg.slug}_recently_added":
        return await recently_added_skill(host_row, chip, host_id=host_id, cfg=cfg)
    if skill_id == f"{cfg.slug}_search":
        return await search_skill(host_row, chip, arg=arg, host_id=host_id, cfg=cfg)
    if skill_id == f"{cfg.slug}_scan":
        return await scan_skill(host_row, chip, host_id=host_id, cfg=cfg)
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
    )
