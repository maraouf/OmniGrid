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

from logic.apps._common import (
    cache_key, peek_cache, resolve_base_url, resolve_cache_ttl, resolve_credential_target)
from logic.coerce import safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("tautulli",)

# Plex/Tautulli media_type → group bucket. Movies are `movie`; TV is
# show/season/episode; music is artist/album/track. Used to bucket the
# recently-added list into Movies / Series / Music (so music isn't lumped in
# with Series).
_SERIES_TYPES = frozenset({"show", "season", "episode"})
_MUSIC_TYPES = frozenset({"artist", "album", "track"})
# Per-library section_type → emoji + friendly label for the libraries list.
_LIB_EMOJI = {"movie": "🎬", "show": "📺", "artist": "🎵",
              "photo": "🖼️", "video": "🎞️", "clip": "🎞️"}

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


_AVATAR_PROXY_HOSTS = ("plex.tv", "plex.direct")


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
        "version": version,
        "fetched_at": int(now),
    }
    print(f"[tautulli] INFO fetched host={host_id} streams={out['streams']} "
          f"transcodes={out['transcodes']} bw={out['bandwidth_kbps']}kbps "
          f"libraries={out['libraries']} items={out['total_items']}")
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
)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown id."""
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
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "streams": streams, "transcodes": transcodes, "direct_play": direct,
        "bandwidth_kbps": safe_int(data.get("bandwidth_kbps")),
        "libraries": libraries, "total_items": items,
    }


# noinspection DuplicatedCode
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
    return {"ok": True, "status": 200, "detail": "\n".join(lines)}


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
    for lib in libs[:25]:
        if not isinstance(lib, dict):
            continue
        name = str(lib.get("section_name") or "?").strip()
        ltype = str(lib.get("section_type") or "").strip().lower()
        n = safe_int(lib.get("count"))
        emoji = _LIB_EMOJI.get(ltype, "📁")
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
        group_key = ("apps.tautulli.group_movies" if grp == "movie"
                     else "apps.tautulli.group_music" if grp == "music"
        else "apps.tautulli.group_series")
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
