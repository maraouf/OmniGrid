"""Plex (Plex Media Server) per-app module.

Encapsulates everything Plex-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``bazarr.py`` / ``seerr.py`` shape:

    SLUGS               — catalog slugs this module handles ("plex").
    requires_api_key()  — True (Plex authenticates via the X-Plex-Token header;
                          the chip's ``api_key`` field stores the Plex token).
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + now-playing (read) +
                          recently-added (read) + search (arg, read) +
                          scan libraries (action).

The expanded card answers "how big is the library, and what's streaming right
now" at a glance:

    libraries        — number of library sections      (GET /library/sections)
    movies           — total movies across movie sections
    shows            — total series across show sections
    music            — total artists across artist sections
    sessions_active  — current playback sessions        (GET /status/sessions)
    version          — Plex Media Server version         (GET /)

Auth model: Plex authenticates every server endpoint via the ``X-Plex-Token``
header (or ``?X-Plex-Token=`` query param — we use the header). The token is the
one from any authenticated Plex web-app URL or Account → … → "Get token"; it is
NOT exchanged (unlike Kavita's JWT). Passing ``Accept: application/json`` makes
Plex return JSON instead of its default XML. The credential probe hits the
auth-required ``/library/sections`` so a bad / missing token fails loudly (401).
Single-instance app (NOT fleet) — one card per pinned chip.

Plex's JSON wraps every response in a top-level ``MediaContainer`` object;
``_mc(body)`` unwraps it. Per-section item counts come from
``/library/sections/<key>/all`` with ``X-Plex-Container-Size=0`` (returns the
``totalSize`` WITHOUT fetching the items — cheap).

Upstream API reference: <plex-host>:32400 (the PMS HTTP API). Endpoints used:
    GET /                                    — server info + version (probe-adjacent)
    GET /library/sections                    — list library sections (credential probe)
    GET /library/sections/<key>/all          — per-section totalSize (Container-Size=0)
    GET /status/sessions                     — active playback sessions (now playing)
    GET /library/recentlyAdded               — recently added items
    GET /hubs/search?query=<q>               — search across the library
    GET /library/sections/<key>/refresh      — trigger a library scan
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl,
    resolve_credential_target)
from logic.coerce import as_dict, as_list, safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("plex",)

# Plex skills. The read skills surface as one-click drawer buttons AND AI /
# Telegram actions; ``plex_search`` is arg-carrying (AI / Telegram only — a
# drawer button can't supply the search term). ``plex_scan`` is the one action
# (triggers a library scan; non-destructive — it only re-indexes).
SKILLS: tuple[dict, ...] = (
    {
        "id": "plex_status",
        "name": "Plex status",
        "ai_phrases": ("plex status, plex library, how many movies, how many "
                       "shows, library size, what's on plex, plex media count, "
                       "how big is my plex library, is anyone watching plex"),
        "destructive": False,
    },
    {
        "id": "plex_now_playing",
        "name": "What's playing on Plex",
        "ai_phrases": ("what's playing on plex, who's watching plex, what is "
                       "streaming, active plex streams, now playing, current "
                       "plex sessions, what's being watched, is anyone "
                       "streaming, who is watching"),
        "destructive": False,
    },
    {
        "id": "plex_recently_added",
        "name": "Recently added to Plex",
        "ai_phrases": ("recently added to plex, what's new on plex, latest "
                       "additions, new movies on plex, new shows on plex, "
                       "what was added recently, plex recently added"),
        "destructive": False,
    },
    {
        "id": "plex_search",
        "name": "Search Plex",
        "ai_phrases": ("search plex for <title>, do i have <title> on plex, "
                       "find <title> on plex, is <title> in my plex library, "
                       "look up <title> on plex, plex search <title>"),
        # arg-carrying → AI / Telegram only (the dispatch supplies the term).
        "arg": True,
        "arg_hint": "the title (movie / show / artist) to search the library for",
        "destructive": False,
    },
    {
        "id": "plex_scan",
        "name": "Scan Plex libraries",
        "ai_phrases": ("scan plex libraries, refresh plex, update plex library, "
                       "rescan plex, scan for new media, plex library scan, "
                       "look for new files on plex"),
        # Action: triggers a re-index of every library section. Non-destructive
        # (re-scans for new / changed files; removes nothing).
        "destructive": False,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. Default TTL
# overridable per chip via the editor's `cache_ttl` field. 30s default — the
# library counts move slowly; now-playing is fetched live by its own skill.
DEFAULT_CACHE_TTL_S = 30
_data_cache: dict[str, tuple[float, dict]] = {}

# Cap on the per-section count calls in fetch_data — a Plex server usually has a
# handful of sections; this bounds a pathological config.
_MAX_SECTIONS = 30


def requires_api_key() -> bool:
    """Plex authenticates every server endpoint via X-Plex-Token; the editor
    MUST render the token input (stored in the chip's api_key) + Test button."""
    return True


def _headers(token: str) -> dict:
    """Plex auth header + JSON Accept (Plex defaults to XML without it)."""
    return {"X-Plex-Token": token, "Accept": "application/json"}


def _mc(body: Any) -> dict:
    """Unwrap Plex's top-level ``MediaContainer`` envelope ({} on any shape)."""
    if not isinstance(body, dict):
        return {}
    return as_dict(body.get("MediaContainer"))


def _version_from(resp) -> str:
    """Plex Media Server version from a ``GET /`` MediaContainer ('' on any
    non-200 / parse failure — version is never load-bearing)."""
    try:
        if getattr(resp, "status_code", 0) != 200:
            return ""
        return str(_mc(resp.json()).get("version") or "").strip()
    except (ValueError, TypeError, AttributeError):
        return ""


async def _fetch_version(cli: httpx.AsyncClient, base: str, token: str) -> str:
    """Best-effort PMS version via ``GET /`` on an already-open client. ``''``
    on any failure (version is a nice-to-have, never load-bearing)."""
    try:
        return _version_from(await cli.get(base + "/", headers=_headers(token)))
    except (httpx.HTTPError, OSError):
        return ""


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Plex's auth-required ``/library/sections`` with the supplied
    X-Plex-Token. Returns ``{ok, detail, status}`` for direct SPA consumption.
    Falls back to the chip's stored ``api_key`` when ``candidate_key`` is blank
    so the operator can re-test after first save without retyping."""
    token, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + "/library/sections"
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(token))
            ver = await _fetch_version(cli, base, token)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        return {"ok": True, "detail": f"OK (Plex {ver})" if ver else "OK",
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check the Plex token)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


async def _section_total(cli: httpx.AsyncClient, base: str, token: str,
                         key: str) -> int:
    """``totalSize`` of one library section WITHOUT fetching its items
    (``X-Plex-Container-Size=0``). 0 on any failure."""
    try:
        r = await cli.get(base + f"/library/sections/{key}/all",
                          headers=_headers(token),
                          params={"X-Plex-Container-Start": "0",
                                  "X-Plex-Container-Size": "0"})
        if r.status_code != 200:
            return 0
        return safe_int(_mc(r.json()).get("totalSize"))
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return 0


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Plex's library summary (+ active sessions + version) for the card.

    Returns ``{available, libraries, movies, shows, music, sections,
    sessions_active, version, platform, fetched_at}``. Raises ``ValueError`` /
    ``RuntimeError`` (caller maps to HTTPException) when the chip's token is
    unset / the base URL won't resolve / the upstream errors."""
    token = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=token, log_tag="plex")
    if hit is not None:
        return hit
    sections_url = base + "/library/sections"
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(sections_url, headers=_headers(token))
            if r.status_code != 200:
                print(f"[plex] error: fetch host={host_id} url={r.request.url} "
                      f"returned HTTP {r.status_code} (check the chip URL points at "
                      f"the Plex root, e.g. http://plex.example.com:32400)")
                if r.status_code in (401, 403):
                    raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                                       f"(check the Plex token) — {sections_url}")
                raise RuntimeError(f"upstream returned HTTP {r.status_code} for {sections_url}")
            try:
                body = r.json()
            except (ValueError, TypeError):
                raise RuntimeError("upstream returned non-JSON")
            dirs = as_list(_mc(body).get("Directory"))[:_MAX_SECTIONS]
            # Per-section counts (bounded fan-out — usually a handful of sections).
            keyed = [(str(d.get("key") or ""), str(d.get("type") or "").lower())
                     for d in dirs if isinstance(d, dict) and d.get("key")]
            totals = await asyncio.gather(
                *[_section_total(cli, base, token, k) for k, _t in keyed])
            movies = shows = music = 0
            for (_k, typ), total in zip(keyed, totals):
                if typ == "movie":
                    movies += total
                elif typ == "show":
                    shows += total
                elif typ == "artist":
                    music += total
            # Active playback sessions — nice-to-have; a failure must NOT fail the card.
            sessions_active = 0
            try:
                sr = await cli.get(base + "/status/sessions", headers=_headers(token))
                if sr.status_code == 200:
                    sessions_active = safe_int(_mc(sr.json()).get("size"))
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                sessions_active = 0
            ver_r = await cli.get(base + "/", headers=_headers(token))
            ver = _version_from(ver_r)
            try:
                platform = str(_mc(ver_r.json()).get("platform") or "").strip()
            except (ValueError, TypeError, AttributeError):
                platform = ""
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[plex] error: fetch host={host_id} url={sections_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    out: dict[str, Any] = {
        "available": True,
        "libraries": len(keyed),
        "movies": movies,
        "shows": shows,
        "music": music,
        "sections": len(keyed),
        "sessions_active": sessions_active,
        "version": ver,
        "platform": platform,
        "fetched_at": int(now),
    }
    print(f"[plex] INFO fetched host={host_id} libraries={out['libraries']} "
          f"movies={movies} shows={shows} music={music} "
          f"sessions={sessions_active}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "libraries": safe_int(data.get("libraries")),
        "movies": safe_int(data.get("movies")),
        "shows": safe_int(data.get("shows")),
        "music": safe_int(data.get("music")),
        "sessions_active": safe_int(data.get("sessions_active")),
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
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404). ``arg``
    carries the free-form search term for ``plex_search``."""
    if skill_id == "plex_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "plex_now_playing":
        return await _now_playing_skill(host_row, chip, host_id=host_id)
    if skill_id == "plex_recently_added":
        return await _recently_added_skill(host_row, chip, host_id=host_id)
    if skill_id == "plex_search":
        return await _search_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "plex_scan":
        return await _scan_skill(host_row, chip, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _resolve_skill_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(token, base)`` or a ready ``{ok: False, detail}`` error dict
    for a Plex skill."""
    token = (chip.get("api_key") or "").strip()
    if not token:
        return "", "", {"ok": False, "status": 0, "detail": "Plex token not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return token, base, None


async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the library summary (force-bypasses the cache) and
    return a formatted ``detail``. Never raises."""
    print(f"[plex] INFO plex_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[plex] warning: plex_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    libs = safe_int(data.get("libraries"))
    movies = safe_int(data.get("movies"))
    shows = safe_int(data.get("shows"))
    music = safe_int(data.get("music"))
    sessions = safe_int(data.get("sessions_active"))
    lines = [
        f"📚 Libraries: {libs:,}",
        f"🎬 Movies: {movies:,}",
        f"📺 Shows: {shows:,}",
    ]
    if music:
        lines.append(f"🎵 Music artists: {music:,}")
    lines.append(f"{'▶️' if sessions else '⏸️'} Now playing: {sessions:,}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "libraries": libs, "movies": movies, "shows": shows,
        "music": music, "sessions_active": sessions,
    }


def _session_line(item: dict) -> str:
    """One now-playing line: ``▶️ <user> — <title> (<pct>%) on <player>``.
    Handles movies (``title``) + episodes (``grandparentTitle - title``)."""
    if not isinstance(item, dict):
        return ""
    user = str(as_dict(item.get("User")).get("title") or "").strip()
    player = str(as_dict(item.get("Player")).get("title") or "").strip()
    gp = str(item.get("grandparentTitle") or "").strip()
    title = str(item.get("title") or "").strip()
    label = (f"{gp} — {title}" if gp else title) or "?"
    offset = safe_int(item.get("viewOffset"))
    duration = safe_int(item.get("duration"))
    pct = f" ({round(offset / duration * 100)}%)" if (offset and duration) else ""
    who = user or "someone"
    where = f" on {player}" if player else ""
    return f"▶️ {who} — {label}{pct}{where}"


async def _now_playing_skill(host_row: dict, chip: dict, *,
                             host_id: Optional[str] = None) -> dict:
    """Read-only: list the active playback sessions (who's watching what) from
    ``GET /status/sessions``. Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[plex] INFO plex_now_playing host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/status/sessions", headers=_headers(token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check the Plex token)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        meta = as_list(_mc(r.json()).get("Metadata"))
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not meta:
        return {"ok": True, "status": 200, "detail": "⏸️ Nothing is playing on Plex right now."}
    lines = [f"▶️ {len(meta):,} stream(s) playing:"]
    for item in meta[:10]:
        ln = _session_line(item)
        if ln:
            lines.append("  " + ln)
    return {"ok": True, "status": 200, "detail": "\n".join(lines)}


def _media_title(item: dict) -> str:
    """Display title for a library item: ``Title (Year)`` for movies, or
    ``Show — SxxEyy Title`` for episodes."""
    if not isinstance(item, dict):
        return ""
    gp = str(item.get("grandparentTitle") or "").strip()
    title = str(item.get("title") or "").strip()
    if gp:  # episode
        season = safe_int(item.get("parentIndex"))
        ep = safe_int(item.get("index"))
        tag = f" S{season:02d}E{ep:02d}" if (season or ep) else ""
        return f"{gp}{tag} — {title}".strip()
    year = safe_int(item.get("year"))
    return title + (f" ({year})" if year else "")


async def _recently_added_skill(host_row: dict, chip: dict, *,
                                host_id: Optional[str] = None) -> dict:
    """Read-only: list the most recently added items from
    ``GET /library/recentlyAdded``. Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[plex] INFO plex_recently_added host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/library/recentlyAdded", headers=_headers(token),
                              params={"X-Plex-Container-Start": "0",
                                      "X-Plex-Container-Size": "15"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check the Plex token)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        meta = as_list(_mc(r.json()).get("Metadata"))
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not meta:
        return {"ok": True, "status": 200, "detail": "🆕 Nothing recently added to Plex."}
    lines = ["🆕 Recently added to Plex:"]
    for item in meta[:12]:
        t = _media_title(item)
        if t:
            icon = "📺" if str(item.get("type") or "").lower() in ("episode", "show", "season") else "🎬"
            lines.append(f"  {icon} {t}")
    return {"ok": True, "status": 200, "detail": "\n".join(lines)}


async def _search_skill(host_row: dict, chip: dict, *,
                        arg: Optional[str] = None,
                        host_id: Optional[str] = None) -> dict:
    """Read-only (arg): search the library via ``GET /hubs/search`` and return
    the top results. Never raises."""
    term = (arg or "").strip()
    if not term:
        return {"ok": False, "status": 0,
                "detail": "no search term given — say e.g. 'search plex for Inception'"}
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[plex] INFO plex_search host={host_id} term={term!r} (live search)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/hubs/search", headers=_headers(token),
                              params={"query": term, "limit": "10"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"search failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check the Plex token)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        hubs = as_list(_mc(r.json()).get("Hub"))
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    # Flatten the per-hub Metadata; only library-content hubs (movie / show /
    # episode / artist), skipping people / tag hubs.
    _content = ("movie", "show", "episode", "artist", "album", "track", "season")
    seen: set = set()
    results: list[str] = []
    for hub in hubs:
        if not isinstance(hub, dict):
            continue
        for item in as_list(hub.get("Metadata")):
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").lower() not in _content:
                continue
            t = _media_title(item)
            if t and t.lower() not in seen:
                seen.add(t.lower())
                icon = "📺" if str(item.get("type") or "").lower() in ("episode", "show", "season") else "🎬"
                results.append(f"  {icon} {t}")
            if len(results) >= 10:
                break
        if len(results) >= 10:
            break
    if not results:
        return {"ok": True, "status": 200,
                "detail": f"🔍 No Plex library matches for “{term}”."}
    return {"ok": True, "status": 200,
            "detail": f"🔍 Plex results for “{term}”:\n" + "\n".join(results)}


async def _scan_skill(host_row: dict, chip: dict, *,
                      host_id: Optional[str] = None) -> dict:
    """Action skill: trigger a scan (re-index) of EVERY library section via
    ``GET /library/sections/<key>/refresh``. Non-destructive. Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[plex] INFO plex_scan host={host_id} (refresh all sections)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            sr = await cli.get(base + "/library/sections", headers=_headers(token))
            if sr.status_code in (401, 403):
                return {"ok": False, "status": sr.status_code, "detail": "auth failed (check the Plex token)"}
            if sr.status_code != 200:
                return {"ok": False, "status": sr.status_code, "detail": f"HTTP {sr.status_code}"}
            try:
                dirs = as_list(_mc(sr.json()).get("Directory"))
            except (ValueError, TypeError):
                return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
            scanned = 0
            for d in dirs[:_MAX_SECTIONS]:
                if not isinstance(d, dict) or not d.get("key"):
                    continue
                rr = await cli.get(base + f"/library/sections/{d.get('key')}/refresh",
                                   headers=_headers(token))
                if rr.status_code in (200, 201, 204):
                    scanned += 1
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"scan failed: {type(e).__name__}: {e}"}
    if not scanned:
        return {"ok": False, "status": 502, "detail": "Plex didn't accept the scan request"}
    return {"ok": True, "status": 200,
            "detail": f"🔄 Started a Plex library scan across {scanned:,} section(s)."}
