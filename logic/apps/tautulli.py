"""Tautulli per-app module.

Encapsulates everything Tautulli-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Tautulli is a monitoring +
statistics front-end for a Plex Media Server (who's watching what, bandwidth,
play history, library counts). It is a member of the per-app family in SHAPE
(SLUGS / requires_api_key / test_credential / fetch_data / peek_latest /
SKILLS / run_skill) but it is BESPOKE, NOT a *arr — its auth model differs:

  Auth model — API key as a QUERY PARAM (not a header, not a JWT exchange).
    Every call is ``GET <base>/api/v2?apikey=<key>&cmd=<command>``. The key is
    Tautulli → Settings → Web Interface → API key. Tautulli returns HTTP 200
    even on an auth / command error and signals the real outcome in the body:
    ``{"response": {"result": "success"|"error", "message": ..., "data": ...}}``
    — so a bad key comes back 200 with ``result="error"``. Every call therefore
    checks ``response.result``, NOT just the HTTP status. No token caching: the
    key goes on every request, so the module is stateless + correct on rotation.

The expanded card answers "what's my Plex doing right now" at a glance:

    streams        — active stream count          (cmd=get_activity)
    transcodes     — streams being transcoded      (get_activity)
    bandwidth_kbps — total stream bandwidth (kbps)  (get_activity)
    libraries      — number of Plex libraries       (cmd=get_libraries)
    total_items    — sum of library item counts      (get_libraries)
    version        — Tautulli version (best-effort)   (cmd=get_tautulli_info)

The activity call is the load-bearing one (confirms the key works); libraries +
version are tolerated (0 / "" when unavailable).

AI / Telegram skills
--------------------
* ``tautulli_status``         — activity + library summary (live fetch).
* ``tautulli_activity``       — who's watching what right now (per-stream detail).
* ``tautulli_libraries``      — list Plex libraries + their item counts.
* ``tautulli_recently_added`` — the most recently added items.
* ``tautulli_history``        — the most recent watch history.

All read-only / non-destructive (Tautulli is a monitor — there's nothing to add
or delete). Single-instance app (NOT fleet) — one card per pinned chip.

Upstream API reference: <tautulli-host>/api/v2?apikey=<key>&cmd=<command>
    cmd=get_activity        — active sessions + stream_count + total_bandwidth
    cmd=get_libraries       — configured Plex libraries (count + item counts)
    cmd=get_tautulli_info   — Tautulli version (best-effort footnote)
    cmd=get_recently_added  — recently added items (&count=N)
    cmd=get_history         — watch history (&length=N)
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx
from logic.external_urls import ExternalURL

from logic.apps._common import (
    cache_key, peek_cache, resolve_base_url, resolve_cache_ttl, resolve_credential_target)
from logic.coerce import as_dict, as_list, safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("tautulli",)

# Plex/Tautulli media_type → group bucket. Movies are `movie`; TV is
# show/season/episode; music is artist/album/track. Used to bucket the
# recently-added list into Movies / Series / Music (so music isn't lumped in
# with Series).
_SERIES_TYPES = frozenset({"show", "season", "episode"})
_MUSIC_TYPES = frozenset({"artist", "album", "track"})
# Recently-added group → i18n key. Anything not movie / music buckets as Series.
_RECENT_GROUP_KEY = {"movie": "apps.tautulli.group_movies",
                     "music": "apps.tautulli.group_music"}
# Per-library section_type → emoji fallback for the libraries list. Used only
# when the NAME-keyword scan below finds nothing — many Plex libraries share a
# type (e.g. "Music Videos" + "Personal" are both ``movie``), so keying purely
# on type repeats the icon. Name keywords give each library a distinct glyph.
_LIB_EMOJI = {"movie": "🎬", "show": "📺", "artist": "🎵",
              "photo": "🖼️", "video": "🎞️", "clip": "🎞️"}

# NAME-keyword → emoji (longer / more-specific phrases FIRST; first match wins,
# same load-bearing ordering rule as the host-icon keyword resolver). Lets
# "Music Videos" / "Personal" / "Anime" / "4K" read distinctly even when their
# Plex section_type is the same generic ``movie``.
_LIB_NAME_EMOJI = (
    ("music video", "🎤"), ("home video", "🏠"), ("audiobook", "🎧"),
    ("documentar", "🎓"), ("stand", "🎭"), ("comedy", "🎭"),
    ("podcast", "🎙️"), ("concert", "🎤"), ("workout", "🏋️"),
    ("fitness", "🏋️"), ("course", "🎓"), ("tutorial", "🎓"),
    ("anime", "🍥"), ("cartoon", "🧸"), ("kid", "🧸"), ("child", "🧸"),
    ("family", "🧸"), ("holiday", "🎄"), ("christmas", "🎄"),
    ("sport", "🏟️"), ("news", "📰"), ("personal", "🏠"), ("home", "🏠"),
    ("4k", "📀"), ("uhd", "📀"), ("music", "🎵"), ("photo", "🖼️"),
    ("picture", "🖼️"), ("book", "📚"), ("video", "🎞️"), ("clip", "🎞️"),
    ("movie", "🎬"), ("film", "🎬"), ("series", "📺"), ("show", "📺"),
    ("tv", "📺"),
)

# Distinct-glyph fallback pool — when two libraries would otherwise resolve to
# the SAME emoji (e.g. two unlabelled ``movie`` libraries), the second+ get the
# next UNUSED glyph from here so no icon repeats within one list render.
_LIB_DEDUP_POOL = ("🗂️", "🎯", "🔖", "📦", "🧩", "🗃️", "📂", "🏷️", "💿", "🎲")


def _lib_emoji_for(name: str, ltype: str) -> str:
    """Pick a per-library emoji: NAME keyword first (so "Music Videos" ≠
    "Personal" even though both are ``movie``), then the section_type fallback,
    then a generic folder. De-dup across a list is the caller's job."""
    low = (name or "").lower()
    for kw, emoji in _LIB_NAME_EMOJI:
        if kw in low:
            return emoji
    return _LIB_EMOJI.get((ltype or "").strip().lower(), "📁")


# Per-(host_id, service_idx) data cache for the expanded card. 60s default —
# matches the rest of the family.
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Tautulli authenticates via an API key (query param); the editor MUST
    render the api_key input + Test-connection button."""
    return True


async def _call(cli: httpx.AsyncClient, base: str, api_key: str, cmd: str,
                **extra: Any) -> Any:
    """One Tautulli API call: ``GET <base>/api/v2?apikey=&cmd=<cmd>`` + extras.

    Returns the ``response.data`` payload on success. Raises ``RuntimeError``
    on a transport error, a non-200 status, non-JSON, or a body whose
    ``response.result`` isn't ``success`` (Tautulli returns 200 + result=error
    for a bad apikey / command, so the body — not the status — is the truth)."""
    url = base + "/api/v2"
    params = {"apikey": api_key, "cmd": cmd}
    params.update({k: v for k, v in extra.items() if v is not None})
    try:
        r = await cli.get(url, params=params)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"request failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for cmd={cmd}")
    try:
        body = r.json() or {}
    except (ValueError, TypeError):
        raise RuntimeError("non-JSON from upstream")
    resp = body.get("response") if isinstance(body, dict) else None
    if not isinstance(resp, dict):
        raise RuntimeError("unexpected response shape")
    result = str(resp.get("result") or "").strip().lower()
    if result != "success":
        msg = str(resp.get("message") or "").strip() or "unknown error"
        # A bad apikey is the common case — surface it as an auth failure so
        # the caller can phrase it like the rest of the family.
        if "apikey" in msg.lower() or "api key" in msg.lower() or "auth" in msg.lower():
            raise RuntimeError(f"auth failed: {msg} (check api_key)")
        raise RuntimeError(f"cmd={cmd} failed: {msg}")
    return resp.get("data")


_AVATAR_PROXY_HOSTS = (ExternalURL.PLEX_TV_HOST, ExternalURL.PLEX_DIRECT_HOST)


def image_proxy_url(host_row: dict, chip: dict, path: str) -> "tuple[str, dict]":
    """Per-app image-proxy hook — turn a Plex image reference into a Tautulli
    ``pms_image_proxy`` URL. The api_key rides the query string and is resolved
    SERVER-SIDE (the OmniGrid server fetches it), so it never reaches the
    browser. Tautulli then fetches the art internally.

    Accepts either a relative Plex metadata path (``/library/...`` poster /
    thumb) OR an absolute Plex avatar URL on a known Plex host (``plex.tv`` /
    ``plex.direct`` — the watch-history ``user_thumb`` is a plex.tv avatar that
    the browser can't hotlink). Both are passed to ``pms_image_proxy`` as the
    ``img`` param. SSRF guard: an absolute URL on any OTHER host is rejected so
    Tautulli can't be turned into an open image proxy."""
    from urllib.parse import urlencode, urlsplit  # noqa: PLC0415
    api_key = (chip.get("api_key") or "").strip()
    p = (path or "").strip()
    if not p:
        raise ValueError("empty image path")
    if "://" in p:
        host = (urlsplit(p).hostname or "").lower()
        if not any(host == h or host.endswith("." + h) for h in _AVATAR_PROXY_HOSTS):
            raise ValueError(f"image host not allowed: {host}")
    elif not p.startswith("/") or ".." in p:
        raise ValueError("image must be a clean Plex path")
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured")
    qs = urlencode({"apikey": api_key, "cmd": "pms_image_proxy", "img": p,
                    "width": 300, "height": 450, "fallback": "poster"})
    return base.rstrip("/") + "/api/v2?" + qs, {}


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Tautulli by calling ``cmd=get_activity`` with the supplied API key.
    Returns ``{ok, detail, status}`` for direct SPA consumption. Falls back to
    the chip's stored ``api_key`` when ``candidate_key`` is blank so a re-test
    after first save doesn't need a retype."""
    key, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            data = await _call(cli, base, key, "get_activity")
            version = await _fetch_version(cli, base, key)
    except RuntimeError as e:
        return {"ok": False, "detail": str(e), "status": 0}
    streams = safe_int((data or {}).get("stream_count"))
    detail = f"OK (Tautulli {version})" if version else "OK"
    detail += f" — {streams} active stream{'s' if streams != 1 else ''}"
    return {"ok": True, "detail": detail, "status": 200}


async def _fetch_version(cli: httpx.AsyncClient, base: str, api_key: str) -> str:
    """Best-effort Tautulli version via ``cmd=get_tautulli_info``. ``""`` on any
    failure (never load-bearing)."""
    try:
        data = await _call(cli, base, api_key, "get_tautulli_info")
    except RuntimeError:
        return ""
    if isinstance(data, dict):
        return str(data.get("tautulli_version") or data.get("version") or "").strip()
    return ""


def _fmt_bandwidth(kbps: Any) -> str:
    """Render a kbps bandwidth figure as a human rate (kbps / Mbps / Gbps).
    ``""`` for missing / non-positive."""
    k = safe_int(kbps)
    if k <= 0:
        return ""
    if k < 1000:
        return f"{k:,} kbps"
    mbps = k / 1000.0
    if mbps < 1000:
        return f"{mbps:,.1f} Mbps"
    return f"{mbps / 1000.0:,.1f} Gbps"


def _sum_series(data: Any) -> "tuple[list, list]":
    """``(categories, per-category totals)`` summed across a ``get_plays_by_*``
    payload's Movies / TV / Music series. ``([], [])`` on an unexpected shape.
    Shared by the plays-over-time line + the day-of-week / hour-of-day
    distributions (all are the same Highcharts ``{categories, series}`` shape)."""
    d = as_dict(data)
    cats = [str(c).strip() for c in as_list(d.get("categories"))]
    series = as_list(d.get("series"))
    if not cats or not series:
        return [], []
    totals = [0] * len(cats)
    for s in series:
        pts = as_list(as_dict(s).get("data"))
        for i in range(min(len(pts), len(totals))):
            totals[i] += safe_int(pts[i])
    return cats, totals


def _shape_plays_series(data: Any) -> list:
    """Per-day TOTAL play counts (across the Movies / TV / Music series) — the
    plays-over-time chart series. ``[]`` on any unexpected shape."""
    return _sum_series(data)[1]


def _shape_distribution(data: Any) -> dict:
    """A ``get_plays_by_dayofweek`` / ``get_plays_by_hourofday`` payload as
    ``{labels, values}`` — total plays per category (day name / hour).
    ``{labels: [], values: []}`` on any unexpected shape."""
    cats, totals = _sum_series(data)
    return {"labels": cats, "values": totals}


def _shape_home_stats(data: Any) -> dict:
    """Parse a ``cmd=get_home_stats`` payload into ``{top_users, top_media}``.
    ``top_users`` is ``[{name, plays, avatar}]`` (the most-active watchers);
    ``top_media`` is ``[{title, plays, type}]`` merged across the top-movies +
    top-tv stat groups (plays desc). Best-effort over the stat groups — empty
    lists on an unexpected shape."""
    top_users: list = []
    top_media: list = []
    for g in as_list(data):
        gd = as_dict(g)
        sid = str(gd.get("stat_id") or "").strip()
        rows = as_list(gd.get("rows"))
        if sid == "top_users":
            for r in rows[:5]:
                rd = as_dict(r)
                top_users.append({
                    "name": str(rd.get("friendly_name") or rd.get("user") or "?").strip(),
                    "plays": safe_int(rd.get("total_plays")),
                    "avatar": str(rd.get("user_thumb") or "").strip(),
                })
        elif sid in ("top_movies", "top_tv"):
            for r in rows[:5]:
                rd = as_dict(r)
                top_media.append({
                    "title": str(rd.get("title") or "?").strip(),
                    "plays": safe_int(rd.get("total_plays")),
                    "type": "movie" if sid == "top_movies" else "tv",
                    # Plex art path for the rich-item poster (proxied server-side
                    # via image_proxy_url). grandparent_thumb is the show poster
                    # for episode rows; thumb is the movie poster.
                    "thumb": str(rd.get("thumb")
                                 or rd.get("grandparent_thumb") or "").strip(),
                })
    top_media.sort(key=lambda m: m["plays"], reverse=True)
    return {"top_users": top_users, "top_media": top_media[:5]}


# noinspection DuplicatedCode
# The upstream-error guard + cache block below is structurally shared with every
# other per-app module's fetch_data — the deliberate per-app encapsulation
# pattern (CLAUDE.md). Content differs (Tautulli apikey-query auth, cmds,
# fields), so it stays inline rather than coupling modules.
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Tautulli's activity + library summary for the expanded card.

    Returns ``{available, streams, direct_play, transcodes, bandwidth_kbps,
    libraries, total_items, version, fetched_at}``. Raises ``ValueError`` /
    ``RuntimeError`` when the chip's api_key is unset / the base URL won't
    resolve / auth fails / the activity call errors. Activity is load-bearing;
    libraries + version are tolerated (0 / "" when unavailable)."""
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("api_key not set")
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured")
    now = time.time()
    ttl = resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S)
    ck = cache_key(host_id, service_idx)
    cached = _data_cache.get(ck)
    if cached and not force and (now - cached[0]) < ttl:
        return cached[1]
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            activity = await _call(cli, base, api_key, "get_activity")
            # Libraries — best-effort (an old Tautulli / odd key may 0 it).
            libraries = 0
            total_items = 0
            try:
                libs = await _call(cli, base, api_key, "get_libraries")
                if isinstance(libs, list):
                    libraries = len(libs)
                    total_items = sum(safe_int(lib.get("count"))
                                      for lib in libs if isinstance(lib, dict))
            except RuntimeError:
                libraries = 0
                total_items = 0
            version = await _fetch_version(cli, base, api_key)
            # Plays-over-time (last 30 days) — drives the card chart. Best-effort
            # (nice-to-have; a failure must NOT fail the card).
            plays_series: list = []
            try:
                pbd = await _call(cli, base, api_key, "get_plays_by_date",
                                  time_range=30)
                plays_series = _shape_plays_series(pbd)
            except RuntimeError:
                plays_series = []
            # Home stats — top watchers + most-played media (last 30d). The
            # signature Tautulli insight; drawer-only, best-effort.
            try:
                home = _shape_home_stats(await _call(
                    cli, base, api_key, "get_home_stats", time_range=30, stats_count=5))
            except RuntimeError:
                home = {"top_users": [], "top_media": []}
            # Play distribution by day-of-week + hour-of-day (last 30d) — the
            # "when is the server busy" charts. Best-effort.
            dayofweek = {"labels": [], "values": []}
            hourofday = {"labels": [], "values": []}
            try:
                dayofweek = _shape_distribution(await _call(
                    cli, base, api_key, "get_plays_by_dayofweek", time_range=30))
            except RuntimeError:
                dayofweek = {"labels": [], "values": []}
            try:
                hourofday = _shape_distribution(await _call(
                    cli, base, api_key, "get_plays_by_hourofday", time_range=30))
            except RuntimeError:
                hourofday = {"labels": [], "values": []}
    except RuntimeError as e:
        print(f"[tautulli] error: fetch host={host_id} — {e}")
        raise RuntimeError(str(e))
    act = activity if isinstance(activity, dict) else {}
    out: dict[str, Any] = {
        "available": True,
        "streams": safe_int(act.get("stream_count")),
        "direct_play": safe_int(act.get("stream_count_direct_play")),
        "transcodes": safe_int(act.get("stream_count_transcode")),
        "bandwidth_kbps": safe_int(act.get("total_bandwidth")),
        "libraries": safe_int(libraries),
        "total_items": safe_int(total_items),
        "plays_series": plays_series,
        "plays_30d": sum(plays_series),
        "top_users": home["top_users"],
        "top_media": home["top_media"],
        "dayofweek": dayofweek,
        "hourofday": hourofday,
        "version": version,
        "fetched_at": int(now),
    }
    print(f"[tautulli] INFO fetched host={host_id} streams={out['streams']} "
          f"transcodes={out['transcodes']} bw={out['bandwidth_kbps']}kbps "
          f"libraries={out['libraries']} items={out['total_items']} "
          f"plays30d={out['plays_30d']} top_users={len(out['top_users'])} "
          f"dow={len(dayofweek['values'])} hod={len(hourofday['values'])}")
    _data_cache[ck] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "streams": safe_int(data.get("streams")),
        "transcodes": safe_int(data.get("transcodes")),
        "bandwidth_kbps": safe_int(data.get("bandwidth_kbps")),
        "libraries": safe_int(data.get("libraries")),
        "total_items": safe_int(data.get("total_items")),
        "plays_30d": safe_int(data.get("plays_30d")),
        "top_user": (as_dict(as_list(data.get("top_users"))[0]).get("name")
                     if as_list(data.get("top_users")) else ""),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


SKILLS: tuple[dict, ...] = (
    {
        "id": "tautulli_status",
        "name": "Tautulli status",
        "ai_phrases": ("tautulli status, plex activity, what's playing on plex, "
                       "how many streams, plex stream count, plex bandwidth, "
                       "is anyone watching plex, tautulli summary"),
        "destructive": False,
    },
    {
        "id": "tautulli_activity",
        "name": "Who's watching now",
        "ai_phrases": ("who is watching plex, current plex streams, what is "
                       "playing right now, plex now playing, active streams, "
                       "who's streaming, current activity"),
        "destructive": False,
    },
    {
        "id": "tautulli_libraries",
        "name": "List Plex libraries",
        "ai_phrases": ("list my plex libraries, what libraries are on plex, "
                       "plex library counts, how many movies / shows, "
                       "show plex libraries, tautulli libraries"),
        "destructive": False,
    },
    {
        "id": "tautulli_recently_added",
        "name": "Recently added",
        "ai_phrases": ("recently added to plex, what's new on plex, latest "
                       "additions, new movies / shows on plex, recently added "
                       "media, what was added recently"),
        "destructive": False,
    },
    {
        "id": "tautulli_history",
        "name": "Watch history",
        "ai_phrases": ("plex watch history, what was watched recently, recent "
                       "plays, who watched what, plex history, recently played"),
        "destructive": False,
    },
    {
        "id": "tautulli_most_watched",
        "name": "Most watched",
        "ai_phrases": ("who watches plex the most, top users on plex, most "
                       "active watchers, most played movies, most watched shows, "
                       "top media on plex, plex top stats, who streams the most, "
                       "what's most popular on plex"),
        "destructive": False,
    },
    {
        "id": "tautulli_terminate_session",
        "name": "Stop a Plex stream",
        "ai_phrases": ("stop the plex stream, kill <name>'s stream, terminate "
                       "session, stop playback for <name>, end the stream playing "
                       "<title>, who can i kick off plex"),
        # DESTRUCTIVE: ends an active playback session via Tautulli's
        # terminate_session. arg = a session_key (the per-row Stop button) OR a
        # user / title to match against the active sessions.
        "arg": True,
        "arg_hint": "the watcher's name or the title to stop (or the session key)",
        "destructive": True,
    },
)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown id.
    ``arg`` carries the target (session key / user / title) for the terminate
    skill."""
    if skill_id == "tautulli_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "tautulli_activity":
        return await _activity_skill(host_row, chip, host_id=host_id)
    if skill_id == "tautulli_libraries":
        return await _libraries_skill(host_row, chip, host_id=host_id)
    if skill_id == "tautulli_recently_added":
        return await _recently_added_skill(host_row, chip, host_id=host_id)
    if skill_id == "tautulli_history":
        return await _history_skill(host_row, chip, host_id=host_id)
    if skill_id == "tautulli_most_watched":
        return await _most_watched_skill(host_row, chip, host_id=host_id,
                                         service_idx=service_idx)
    if skill_id == "tautulli_terminate_session":
        return await _terminate_session_skill(host_row, chip, arg=arg, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _resolve_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(api_key, base)`` or return a ready ``{ok: False, detail}`` —
    the Tautulli analogue of the shared ``resolve_skill_target`` (Tautulli
    doesn't use ``_servarr``)."""
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        return "", "", {"ok": False, "status": 0, "detail": "Tautulli api_key not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return api_key, base, None


async def _most_watched_skill(host_row: dict, chip: dict, *,
                              host_id: Optional[str] = None,
                              service_idx: Optional[int] = None) -> dict:
    """Read-only: the top watchers + most-played media (last 30d) from
    ``cmd=get_home_stats`` (served via fetch_data's cache). Never raises."""
    print(f"[tautulli] INFO tautulli_most_watched host={host_id} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    top_users = as_list(data.get("top_users"))
    top_media = as_list(data.get("top_media"))
    if not top_users and not top_media:
        return {"ok": True, "status": 200,
                "detail": ("📊 No watch stats for the last 30 days "
                           "(Tautulli's home stats are empty).")}
    lines: list = []
    # Rich rows for the SPA's generic skill-result renderer: top watchers carry
    # their Plex avatar (user_thumb) and most-played media carry their poster
    # (thumb), both proxied server-side via image_proxy_url + grouped with a
    # divider. `detail` is kept verbatim for AI / Telegram (no image surface).
    items: list = []
    if top_users:
        lines.append("👥 Top watchers (30d):")
        for u in top_users[:5]:
            ud = as_dict(u)
            name = str(ud.get("name") or "?").strip()
            plays = safe_int(ud.get("plays"))
            lines.append(f"  • {name} — {plays:,} plays")
            row: dict = {"title": name, "subtitle": f"{plays:,} plays",
                         "group": "apps.tautulli.top_watchers"}
            avatar = str(ud.get("avatar") or "").strip()
            if avatar:
                row["poster"] = avatar
                row["poster_proxy"] = True
            items.append(row)
    if top_media:
        lines.append("🎬 Most played (30d):")
        for m in top_media[:5]:
            md = as_dict(m)
            title = str(md.get("title") or "?").strip()
            plays = safe_int(md.get("plays"))
            lines.append(f"  • {title} — {plays:,} plays")
            row = {"title": title, "subtitle": f"{plays:,} plays",
                   "group": "apps.tautulli.most_played"}
            thumb = str(md.get("thumb") or "").strip()
            if thumb:
                row["poster"] = thumb
                row["poster_proxy"] = True
            items.append(row)
    return {"ok": True, "status": 200, "detail": "\n".join(lines), "items": items}


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the current activity + library summary
    (force-bypasses the cache). Never raises."""
    print(f"[tautulli] INFO tautulli_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[tautulli] warning: tautulli_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    streams = safe_int(data.get("streams"))
    transcodes = safe_int(data.get("transcodes"))
    direct = safe_int(data.get("direct_play"))
    bw = _fmt_bandwidth(data.get("bandwidth_kbps"))
    libraries = safe_int(data.get("libraries"))
    items = safe_int(data.get("total_items"))
    lines = [
        f"▶️ Active streams: {streams:,}"
        + (f" ({direct:,} direct · {transcodes:,} transcode)" if streams else ""),
    ]
    if bw:
        lines.append(f"📶 Bandwidth: {bw}")
    lines.append(f"📚 Libraries: {libraries:,}")
    if items:
        lines.append(f"🎬 Items: {items:,}")
    plays_30d = safe_int(data.get("plays_30d"))
    if plays_30d:
        lines.append(f"📈 Plays (30d): {plays_30d:,}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "streams": streams, "transcodes": transcodes, "direct_play": direct,
        "bandwidth_kbps": safe_int(data.get("bandwidth_kbps")),
        "libraries": libraries, "total_items": items,
        "plays_30d": plays_30d,
    }


# noinspection DuplicatedCode
def _activity_item(s: dict) -> Optional[dict]:
    """One active session as a rich skill-result item: the title poster (show
    poster for episodes, else the item thumb — proxied via pms_image_proxy),
    the watching user + their Plex avatar (proxied), a device/state/decision
    subtitle, and a play-progress bar. Parallel to the text line built inline in
    ``_activity_skill`` (the form AI / Telegram get)."""
    if not isinstance(s, dict):
        return None
    title = str(s.get("full_title") or s.get("title") or "?").strip()
    user = str(s.get("friendly_name") or s.get("user") or "").strip()
    avatar = str(s.get("user_thumb") or "").strip()
    player = str(s.get("player") or "").strip()
    state = str(s.get("state") or "").strip()
    mode = str(s.get("transcode_decision") or "").strip()
    prog = safe_int(s.get("progress_percent"))
    # Prefer the show / season poster for episodes; fall back to the item thumb.
    poster = str(s.get("grandparent_thumb") or s.get("parent_thumb")
                 or s.get("thumb") or "").strip()
    out: dict = {"title": title,
                 "subtitle": " · ".join(p for p in (player, state, mode) if p)}
    if poster:
        out["poster"] = poster
        out["poster_proxy"] = True
    if prog:
        out["progress"] = prog
    if user:
        out["byline"] = user
        if avatar:
            out["avatar"] = avatar
            out["avatar_proxy"] = True
    # Per-row ⏹ Stop button → terminate THIS stream by its session_key,
    # confirm-gated (it kicks the viewer off).
    skey = str(s.get("session_key") or "").strip()
    if skey:
        out["row_action"] = {
            "skill_id": "tautulli_terminate_session",
            "arg": skey,
            "destructive": True,
            "icon": "x",
            "title_i18n": "apps.tautulli.stop_stream",
            "confirm_i18n": "apps.tautulli.stop_stream_confirm",
            "confirm_text_i18n": "apps.tautulli.stop_stream",
        }
    return out


async def _activity_skill(host_row: dict, chip: dict, *,
                          host_id: Optional[str] = None) -> dict:
    """Read-only: who's watching what right now (per-stream detail) from
    ``cmd=get_activity``. Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[tautulli] INFO tautulli_activity host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            data = await _call(cli, base, api_key, "get_activity")
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    act = data if isinstance(data, dict) else {}
    _sessions = act.get("sessions")
    sessions = _sessions if isinstance(_sessions, list) else []
    count = safe_int(act.get("stream_count"))
    if not sessions:
        return {"ok": True, "status": 200, "detail": "▶️ Nothing is playing on Plex right now."}
    lines = [f"▶️ {count:,} active stream{'s' if count != 1 else ''}:"]
    items: list[dict] = []
    for s in sessions[:10]:
        if not isinstance(s, dict):
            continue
        title = str(s.get("full_title") or s.get("title") or "?").strip()
        user = str(s.get("friendly_name") or s.get("user") or "?").strip()
        state = str(s.get("state") or "").strip()
        mode = str(s.get("transcode_decision") or "").strip()
        prog = safe_int(s.get("progress_percent"))
        bits = [f"{user} — {title}"]
        meta = " · ".join(p for p in (state, mode, f"{prog}%" if prog else "") if p)
        if meta:
            bits.append(f"({meta})")
        lines.append("• " + " ".join(bits))
        it = _activity_item(s)
        if it:
            items.append(it)
    out: dict = {"ok": True, "status": 200, "detail": "\n".join(lines)}
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = "apps.tautulli.now_watching_count"
    return out


# noinspection DuplicatedCode
async def _terminate_session_skill(host_row: dict, chip: dict, *,
                                   arg: Optional[str] = None,
                                   host_id: Optional[str] = None) -> dict:
    """DESTRUCTIVE (arg): stop ONE active playback session. Resolves the target
    from ``cmd=get_activity`` — an exact ``session_key`` (the per-row Stop
    button) first, else a substring match on the watcher / title (the AI /
    Telegram free-text path) — then ``cmd=terminate_session``. Never raises."""
    needle = (arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no stream given (say e.g. \"stop John's stream\")"}
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    nl = needle.lower()
    print(f"[tautulli] INFO tautulli_terminate_session host={host_id} target={needle!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            data = await _call(cli, base, api_key, "get_activity")
            sessions = as_list(as_dict(data).get("sessions"))
            target_key = ""
            target_label = ""
            for s in sessions:
                if not isinstance(s, dict):
                    continue
                skey = str(s.get("session_key") or "").strip()
                title = str(s.get("full_title") or s.get("title") or "").strip()
                user = str(s.get("friendly_name") or s.get("user") or "").strip()
                if skey and skey == needle:  # exact key — the per-row Stop button
                    target_key, target_label = skey, f"{user} — {title}".strip(" —")
                    break
                if not target_key and (nl in title.lower() or nl in user.lower()):
                    target_key, target_label = skey, f"{user} — {title}".strip(" —")
            if not target_key:
                return {"ok": False, "status": 404,
                        "detail": f"no active Plex stream matched \"{needle}\""}
            await _call(cli, base, api_key, "terminate_session",
                        session_key=target_key, message="Stopped from OmniGrid")
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": f"stop failed: {e}"}
    return {"ok": True, "status": 200,
            "detail": f"🛑 Stopped {target_label or 'the stream'}."}


async def _libraries_skill(host_row: dict, chip: dict, *,
                           host_id: Optional[str] = None) -> dict:
    """Read-only: list Plex libraries + their item counts from
    ``cmd=get_libraries``. Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[tautulli] INFO tautulli_libraries host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            data = await _call(cli, base, api_key, "get_libraries")
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    libs = data if isinstance(data, list) else []
    lines = []
    used: set[str] = set()  # glyphs already taken — keep each library distinct
    pool = iter(_LIB_DEDUP_POOL)
    for lib in libs[:25]:
        if not isinstance(lib, dict):
            continue
        name = str(lib.get("section_name") or "?").strip()
        ltype = str(lib.get("section_type") or "").strip().lower()
        n = safe_int(lib.get("count"))
        emoji = _lib_emoji_for(name, ltype)
        # De-dup: if this glyph is already used, take the next unused pool glyph
        # so no two libraries share an icon (falls back to the original on
        # pool exhaustion — better a rare repeat than a blank).
        if emoji in used:
            for cand in pool:
                if cand not in used:
                    emoji = cand
                    break
        used.add(emoji)
        # "<emoji> <name>  <N items>" — the 2-space gap makes the count a
        # right-hand segment in the drawer's detail renderer (a clean two-col
        # read) while staying one readable line for AI / Telegram.
        seg = f"{emoji} {name}"
        if n:
            seg += f"  {n:,} item" + ("" if n == 1 else "s")
        lines.append(seg)
    if not lines:
        return {"ok": True, "status": 200, "detail": "📚 No Plex libraries reported."}
    return {"ok": True, "status": 200,
            "detail": f"📚 Plex libraries ({len(lines):,}):\n" + "\n".join(lines)}


# noinspection DuplicatedCode
async def _recently_added_skill(host_row: dict, chip: dict, *,
                                host_id: Optional[str] = None) -> dict:
    """Read-only: the most recently added items from ``cmd=get_recently_added``.
    Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[tautulli] INFO tautulli_recently_added host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            data = await _call(cli, base, api_key, "get_recently_added", count=10)
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    items = []
    if isinstance(data, dict):
        _ra = data.get("recently_added")
        items = _ra if isinstance(_ra, list) else []
    elif isinstance(data, list):
        items = data
    # Group into Movies / Series / Music, preserving recency order within each
    # group. Plex/Tautulli media_type is movie | show/season/episode | artist/
    # album/track — bucket all three (the old code put everything non-movie into
    # Series, so music showed under Series). Rich rows carry the Plex thumb
    # (routed through the per-app image proxy — pms_image_proxy needs the
    # api_key, which stays server-side) + the year on the title's subtitle.
    movies: list[dict] = []
    series: list[dict] = []
    music: list[dict] = []
    for it in items[:20]:
        if not isinstance(it, dict):
            continue
        mtype = str(it.get("media_type") or "").strip().lower()
        if mtype == "movie":
            grp = "movie"
        elif mtype in _MUSIC_TYPES:
            grp = "music"
        elif mtype in _SERIES_TYPES:
            grp = "series"
        else:
            grp = "movie"  # photo / clip / other → own-title style
        yr = str(it.get("year") or "").strip()
        yr = yr[:4] if len(yr) >= 4 and yr[:4].isdigit() else ""
        sub_parts: list = []
        if grp == "movie":
            title = str(it.get("title") or it.get("full_title") or "?").strip()
            thumb = str(it.get("thumb") or it.get("parent_thumb") or "").strip()
            if yr:
                sub_parts.append(yr)
        elif grp == "music":
            # Lead with the artist; the album / track is the subtitle.
            title = str(it.get("grandparent_title") or it.get("parent_title")
                        or it.get("title") or "?").strip()
            detail_name = str(it.get("title") or "").strip()
            if detail_name and detail_name != title:
                sub_parts.append(detail_name)
            thumb = str(it.get("thumb") or it.get("parent_thumb")
                        or it.get("grandparent_thumb") or "").strip()
        else:  # series — lead with the show name, episode title as subtitle.
            title = str(it.get("grandparent_title") or it.get("parent_title")
                        or it.get("title") or it.get("full_title") or "?").strip()
            ep = str(it.get("title") or "").strip()
            if ep and ep != title:
                sub_parts.append(ep)
            if yr:
                sub_parts.append(yr)
            thumb = str(it.get("grandparent_thumb") or it.get("parent_thumb")
                        or it.get("thumb") or "").strip()
        group_key = _RECENT_GROUP_KEY.get(grp, "apps.tautulli.group_series")
        row: "dict[str, Any]" = {
            "title": title + (f" ({yr})" if grp == "movie" and yr else ""),
            "subtitle": " · ".join(sub_parts),
            "group": group_key}
        if thumb:
            row["poster"] = thumb
            row["poster_proxy"] = True
        (movies if grp == "movie" else music if grp == "music" else series).append(row)
    rich = movies + series + music
    if not rich:
        return {"ok": True, "status": 200, "detail": "🆕 Nothing recently added."}
    # Plain text (AI / Telegram): grouped, with year.
    lines: list[str] = []
    if movies:
        lines.append("🎬 Movies:")
        lines += [f"  • {r['title']}" for r in movies]
    if series:
        lines.append("📺 Series:")
        lines += [f"  • {r['title']}"
                  + (f" — {r['subtitle']}" if r.get("subtitle") else "") for r in series]
    if music:
        lines.append("🎵 Music:")
        lines += [f"  • {r['title']}"
                  + (f" — {r['subtitle']}" if r.get("subtitle") else "") for r in music]
    return {"ok": True, "status": 200,
            "detail": "🆕 Recently added:\n" + "\n".join(lines),
            "count": len(rich), "count_i18n": "apps.tautulli.recent_count",
            "items": rich}


# noinspection DuplicatedCode
async def _history_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None) -> dict:
    """Read-only: the most recent watch history from ``cmd=get_history``.
    Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[tautulli] INFO tautulli_history host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            data = await _call(cli, base, api_key, "get_history", length=10)
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    rows = []
    if isinstance(data, dict):
        _hist = data.get("data")
        rows = _hist if isinstance(_hist, list) else []
    lines = []
    # Rich rows: the media poster thumbnail + the title, with the watching USER
    # as a byline + their Plex avatar (both via the per-app image proxy so the
    # api_key / a cross-origin-blocked plex.tv avatar stays server-side).
    rich: list[dict] = []
    for h in rows[:12]:
        if not isinstance(h, dict):
            continue
        mtype = str(h.get("media_type") or "").strip().lower()
        if mtype == "episode":
            title = str(h.get("grandparent_title") or h.get("full_title")
                        or h.get("title") or "?").strip()
        else:
            title = str(h.get("full_title") or h.get("title") or "?").strip()
        user = str(h.get("friendly_name") or h.get("user") or "?").strip()
        lines.append(f"• {user} — {title}")
        # Movie poster / show poster (not the episode still).
        if mtype == "movie":
            thumb = str(h.get("thumb") or "").strip()
        else:
            thumb = str(h.get("grandparent_thumb") or h.get("parent_thumb")
                        or h.get("thumb") or "").strip()
        avatar = str(h.get("user_thumb") or "").strip()
        row: "dict[str, Any]" = {"title": title, "subtitle": ""}
        if user and user != "?":
            row["byline"] = user
        if thumb:
            row["poster"] = thumb
            row["poster_proxy"] = True
        if avatar:
            row["avatar"] = avatar
            row["avatar_proxy"] = True
        rich.append(row)
    if not lines:
        return {"ok": True, "status": 200, "detail": "🕑 No watch history yet."}
    return {"ok": True, "status": 200,
            "detail": "🕑 Recent watch history:\n" + "\n".join(lines),
            "count": len(rich), "items": rich}
