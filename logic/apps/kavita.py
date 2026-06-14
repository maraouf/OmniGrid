"""Kavita per-app module.

Encapsulates everything Kavita-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Kavita is a self-hosted digital
library / reader (comics / manga / ebooks). It is a member of the per-app
family in SHAPE (SLUGS / requires_api_key / test_credential / fetch_data /
peek_latest / SKILLS / run_skill) but it is BESPOKE, NOT a *arr — it does NOT
reuse ``_servarr`` because its auth model is different:

  Auth model — JWT exchanged from an API key (NOT a static header).
    1. ``POST /api/Plugin/authenticate?apiKey=<key>&pluginName=OmniGrid``
       returns a user object containing a JWT ``token``.
    2. Every subsequent call carries ``Authorization: Bearer <token>``.
  The API key is per-user (Kavita → Settings → Account → API Key). The probe
  authenticates so a bad key fails loudly. Tokens are short-lived; we
  re-authenticate per fetch (cheap, single extra round-trip) rather than
  caching a token across the process — keeps the module stateless + correct
  when the key is rotated.

The expanded card answers "how big is the library" at a glance:

    libraries      — number of libraries          (GET /api/Library)
    series_count   — total series                 (GET /api/Stats/server/stats)
    volume_count   — total volumes                (server stats)
    chapter_count  — total chapters               (server stats)
    total_size     — total library size in bytes  (server stats)
    version        — Kavita version               (GET /api/Server/version)

The server-stats call is ADMIN-only; most homelab API keys are the admin's, so
it usually populates. When the key isn't an admin's, the stats fields come back
0 and the card still shows the library count + version (the library list is the
load-bearing call, not the admin stats).

AI / Telegram skills
--------------------
* ``kavita_status``     — library summary (live fetch).
* ``kavita_libraries``  — list configured libraries + their type.
* ``kavita_search``     — (arg, AI / Telegram only) search the whole library for
                          a term (``GET /api/Search/search``); returns top series.
* ``kavita_scan``       — (action) trigger a scan of EVERY library (loops the
                          confirmed library list → ``POST /api/Library/scan``).

There is deliberately NO destructive skill (deleting a library / series via the
AI is risky and wasn't requested). Single-instance app (NOT fleet) — one card
per pinned chip.

Upstream API reference: <kavita-host>/api (Swagger at /swagger). Endpoints:
    POST /api/Plugin/authenticate?apiKey=&pluginName= — JWT token (auth probe)
    GET  /api/Library          — configured libraries (count + names + type)
    GET  /api/Stats/server/stats — series / volume / chapter counts + total size (admin)
    GET  /api/Server/version   — Kavita version (best-effort footnote)
    GET  /api/Search/search?queryString= — global search (series / collections / …)
    POST /api/Library/scan?libraryId= — rescan one library
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, peek_cache, resolve_base_url, resolve_cache_ttl, resolve_credential_target)
from logic.coerce import as_dict, as_list, safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("kavita",)

# pluginName sent to the Plugin/authenticate endpoint — Kavita logs it as the
# 3rd-party client name, so a recognisable value helps the operator audit access.
_PLUGIN_NAME = "OmniGrid"

# Library type → human label (Kavita's LibraryType enum). Used by the
# kavita_libraries skill; unknown values fall back to the raw number.
_LIBRARY_TYPES = {0: "Manga", 1: "Comic", 2: "Book", 3: "Images",
                  4: "Light Novel", 5: "Comic (vine)"}

SKILLS: tuple[dict, ...] = (
    {
        "id": "kavita_status",
        "name": "Kavita status",
        "ai_phrases": ("kavita status, how big is my library, how many series, "
                       "how many books, comic library size, manga library, "
                       "kavita library summary, digital library size"),
        "destructive": False,
    },
    {
        "id": "kavita_libraries",
        "name": "List libraries",
        "ai_phrases": ("list my kavita libraries, what libraries do i have, "
                       "show kavita libraries, kavita library list, "
                       "what collections are in kavita"),
        "destructive": False,
    },
    {
        "id": "kavita_recently_added",
        "name": "Recently added",
        "ai_phrases": ("what's new on kavita, recently added to kavita, latest "
                       "additions, new chapters on kavita, what got added "
                       "recently, recently updated series, new manga, new comics"),
        "destructive": False,
    },
    {
        "id": "kavita_on_deck",
        "name": "On deck",
        "ai_phrases": ("what am i reading on kavita, continue reading, on deck, "
                       "my in-progress series, what should i read next, "
                       "kavita continue reading, resume reading"),
        "destructive": False,
    },
    {
        "id": "kavita_most_read",
        "name": "Most read",
        "ai_phrases": ("most read on kavita, most popular series, top read series, "
                       "what gets read the most, popular comics, popular manga, "
                       "kavita most read, top series by reads, reading activity"),
        "destructive": False,
    },
    {
        "id": "kavita_search",
        "name": "Search the library",
        "ai_phrases": ("search <title> on kavita, find <title> in my library, "
                       "do i have <title> in kavita, look up <title> kavita, "
                       "search my comics for <title>, search kavita <title>"),
        # arg-carrying → AI / Telegram only (the dispatch supplies the term from
        # natural language). `arg: True` keeps it OUT of the app-drawer button
        # list (a drawer button can't supply the term). Mirrors the *arr
        # info / look-up arg skills.
        "arg": True,
        "destructive": False,
    },
    {
        "id": "kavita_scan",
        "name": "Scan libraries",
        "ai_phrases": ("scan my kavita libraries, rescan kavita, kavita library "
                       "scan, scan for new books, refresh kavita library, "
                       "scan all libraries"),
        "destructive": False,
    },
    {
        "id": "kavita_scan_library",
        "name": "Scan one library",
        "ai_phrases": ("scan the <name> library, rescan <name> on kavita, scan "
                       "just the <name> library, refresh the <name> library, "
                       "scan my manga library, scan the comics library"),
        # arg-carrying → AI / Telegram + the per-row Scan button on the libraries
        # list (arg = the library id). Non-destructive (a scan is additive).
        "arg": True,
        "arg_hint": "the library name (or id) to rescan",
        "destructive": False,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. 60s default —
# matches the rest of the family.
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Kavita authenticates via an API key exchanged for a JWT; the editor MUST
    render the api_key input + Test-connection button."""
    return True


# Kavita cover endpoints that the image-proxy hook will serve. The api_key rides
# the query string (Kavita's Image controller accepts it so covers work in an
# <img src>), resolved SERVER-SIDE so it never reaches the browser.
_COVER_PREFIXES = ("series-cover", "volume-cover", "chapter-cover",
                   "collection-cover", "reading-list-cover", "cover-upload")


def image_proxy_url(host_row: dict, chip: dict, path: str) -> "tuple[str, dict]":
    """Per-app image-proxy hook — turn a Kavita cover reference (e.g.
    ``series-cover?seriesId=123``) into the authenticated upstream URL
    ``<base>/api/Image/<path>&apiKey=<key>``. The api_key rides the query string
    and is resolved SERVER-SIDE (OmniGrid fetches the bytes), so it never reaches
    the browser. SSRF-guarded: the path must be a bare relative reference to a
    known cover endpoint (no scheme, no traversal)."""
    from urllib.parse import quote  # noqa: PLC0415
    api_key = (chip.get("api_key") or "").strip()
    p = (path or "").strip().lstrip("/")
    if not p or "://" in p or ".." in p:
        raise ValueError("invalid image path")
    if not p.startswith(_COVER_PREFIXES):
        raise ValueError("image path not allowed")
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured")
    sep = "&" if "?" in p else "?"
    return f"{base.rstrip('/')}/api/Image/{p}{sep}apiKey={quote(api_key)}", {}


def _recent_item(s: Any) -> Optional[dict]:
    """One recently-updated series as a rich skill-result item (poster via the
    image proxy + 'N new chapters' subtitle). ``None`` for a malformed row."""
    if not isinstance(s, dict):
        return None
    sid = safe_int(s.get("seriesId") or s.get("id"))
    name = str(s.get("seriesName") or s.get("name") or "?").strip()
    count = safe_int(s.get("count"))
    out: dict = {"title": name}
    if count:
        out["subtitle"] = (f"{count:,} new chapter" + ("s" if count != 1 else ""))
    if sid:
        out["poster"] = f"series-cover?seriesId={sid}"
        out["poster_proxy"] = True
    return out


def _ondeck_item(s: Any) -> Optional[dict]:
    """One on-deck (continue-reading) series as a rich skill-result item (poster +
    a read-progress bar). ``None`` for a malformed row."""
    if not isinstance(s, dict):
        return None
    sid = safe_int(s.get("id"))
    name = str(s.get("name") or "?").strip()
    read = safe_int(s.get("pagesRead"))
    total = safe_int(s.get("pages"))
    out: dict = {"title": name}
    if total:
        out["progress"] = min(100, max(0, round(read / total * 100)))
        out["subtitle"] = f"{read:,}/{total:,} pages"
    if sid:
        out["poster"] = f"series-cover?seriesId={sid}"
        out["poster_proxy"] = True
    return out


def _most_read_item(row: Any) -> Optional[dict]:
    """One most-read series (Kavita ``StatCount<SeriesDto>`` → ``{value, count}``)
    as a rich skill-result item (poster via the image proxy + 'N reads'
    subtitle). ``None`` for a malformed row."""
    rd = as_dict(row)
    val = as_dict(rd.get("value")) or rd  # some builds inline the series fields
    name = str(val.get("name") or val.get("seriesName") or "?").strip()
    sid = safe_int(val.get("id") or val.get("seriesId"))
    count = safe_int(rd.get("count"))
    if name == "?" and not sid:
        return None
    out: dict = {"title": name}
    if count:
        out["subtitle"] = f"{count:,} read" + ("s" if count != 1 else "")
    if sid:
        out["poster"] = f"series-cover?seriesId={sid}"
        out["poster_proxy"] = True
    return out


def _reading_activity(stats: Any) -> dict:
    """Reading-activity summary from a Kavita ServerStatistics payload (the SAME
    ``/api/Stats/server/stats`` response the card already fetches — NO extra
    call): the top-read series + its read count, recently-read count, active-
    reader count, total reading minutes. All best-effort — fields a non-admin
    key / older Kavita omits come back empty / 0 so the card hides those chips."""
    sd = as_dict(stats)
    most_read = as_list(sd.get("mostReadSeries"))
    top = _most_read_item(most_read[0]) if most_read else None
    return {
        "top_read_series": (top or {}).get("title", ""),
        "top_read_count": safe_int(as_dict(most_read[0]).get("count")) if most_read else 0,
        "recently_read_count": len(as_list(sd.get("recentlyRead"))),
        "active_readers": len(as_list(sd.get("mostActiveUsers"))),
        "reading_minutes": safe_int(sd.get("totalReadingTime")),
    }


def _fmt_reading_time(minutes: Any) -> str:
    """Render a reading-time minute count as a compact 'Xh' / 'Xh Ym' / 'Nm'
    label. ``""`` for missing / non-positive."""
    m = safe_int(minutes)
    if m <= 0:
        return ""
    h, mm = divmod(m, 60)
    if h and mm:
        return f"{h:,}h {mm}m"
    if h:
        return f"{h:,}h"
    return f"{mm}m"


def _bearer(token: str) -> dict:
    """Auth header for a Bearer-token Kavita call."""
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


async def _authenticate(cli: httpx.AsyncClient, base: str, api_key: str) -> "tuple[str, str]":
    """Exchange the API key for a JWT via ``POST /api/Plugin/authenticate``.

    Returns ``(token, version)`` — ``version`` is whatever the auth response
    carries (best-effort; ``""`` when absent). Raises ``RuntimeError`` on any
    failure (bad key / unreachable / non-JSON) so callers surface it loudly."""
    url = base + "/api/Plugin/authenticate"
    try:
        r = await cli.post(url, params={"apiKey": api_key, "pluginName": _PLUGIN_NAME})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"auth request failed: {type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError(f"auth failed: HTTP {r.status_code} (check api_key)")
    if r.status_code != 200:
        raise RuntimeError(f"auth returned HTTP {r.status_code} for {url}")
    try:
        body = r.json() or {}
    except (ValueError, TypeError):
        raise RuntimeError("auth returned non-JSON")
    token = str(body.get("token") or "").strip()
    if not token:
        raise RuntimeError("auth response had no token")
    # Some Kavita versions stamp the server version on the user payload.
    version = str(body.get("kavitaVersion") or body.get("version") or "").strip()
    return token, version


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Kavita by authenticating with the supplied API key. Returns
    ``{ok, detail, status}`` for direct SPA consumption. Falls back to the
    chip's stored ``api_key`` when ``candidate_key`` is blank so a re-test after
    first save doesn't need a retype."""
    key, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            token, version = await _authenticate(cli, base, key)
            if not version:
                version = await _fetch_version(cli, base, token)
    except RuntimeError as e:
        return {"ok": False, "detail": str(e), "status": 0}
    return {"ok": True,
            "detail": f"OK (Kavita {version})" if version else "OK",
            "status": 200}


async def _fetch_version(cli: httpx.AsyncClient, base: str, token: str) -> str:
    """Best-effort Kavita version via ``GET /api/Server/version`` on an
    already-authenticated client. ``""`` on any failure (never load-bearing)."""
    try:
        r = await cli.get(base + "/api/Server/version", headers=_bearer(token))
        if r.status_code != 200:
            return ""
        # The endpoint returns a bare JSON string OR a {version} object.
        try:
            body = r.json()
        except (ValueError, TypeError):
            return (r.text or "").strip().strip('"')
        if isinstance(body, str):
            return body.strip()
        if isinstance(body, dict):
            return str(body.get("version") or "").strip()
        return ""
    except (httpx.HTTPError, OSError):
        return ""


def _fmt_bytes(n: Any) -> str:
    """Render a byte count as a human size (MiB / GiB / TiB). ``""`` for
    missing / non-positive."""
    b = safe_int(n)
    if b <= 0:
        return ""
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    val = float(b)
    idx = 0
    while val >= 1024 and idx < len(units) - 1:
        val /= 1024
        idx += 1
    return f"{val:,.1f} {units[idx]}"


# noinspection DuplicatedCode
# The upstream-error guard + JSON-parse block below is structurally shared with
# every other per-app module's fetch_data — the deliberate per-app encapsulation
# pattern (CLAUDE.md). Content differs (Kavita JWT auth, endpoints, fields), so
# it stays inline rather than coupling modules.
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Kavita's library summary for the expanded card.

    Returns ``{available, libraries, series_count, volume_count, chapter_count,
    total_size, version, fetched_at}``. Raises ``ValueError`` / ``RuntimeError``
    when the chip's api_key is unset / the base URL won't resolve / auth fails /
    the primary upstream call errors. The library list is load-bearing; the
    admin server-stats + version are tolerated (0 / "" when unavailable)."""
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
    lib_url = base + "/api/Library"
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            token, version = await _authenticate(cli, base, api_key)
            r = await cli.get(lib_url, headers=_bearer(token))
            # Admin-only server stats — best-effort (0 fields for a non-admin key).
            # The same response ALSO carries reading-activity (most-read series /
            # recently-read / active readers / total reading time), parsed in ONE
            # pass via _reading_activity — no extra call.
            series = volumes = chapters = total_size = 0
            reading: dict = {}
            try:
                sr = await cli.get(base + "/api/Stats/server/stats",
                                   headers=_bearer(token))
                if sr.status_code == 200:
                    _sj = sr.json() or {}
                    if isinstance(_sj, dict):
                        series = safe_int(_sj.get("seriesCount"))
                        volumes = safe_int(_sj.get("volumeCount"))
                        chapters = safe_int(_sj.get("chapterCount"))
                        total_size = safe_int(_sj.get("totalSize"))
                        reading = _reading_activity(_sj)
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                series = volumes = chapters = total_size = 0
                reading = {}
            if not version:
                version = await _fetch_version(cli, base, token)
    except RuntimeError as e:
        # auth failure — surface as the upstream error the card renders.
        print(f"[kavita] error: fetch host={host_id} — {e}")
        raise RuntimeError(str(e))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[kavita] error: fetch host={host_id} url={lib_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        print(f"[kavita] error: fetch host={host_id} url={lib_url} returned "
              f"HTTP {r.status_code} (auth — check api_key)")
        raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} — {lib_url}")
    libraries: list = []
    if r.status_code == 204:
        # 204 No Content — a reachable Kavita with no libraries configured (or
        # the endpoint answering empty). Treat as an empty list, NOT an error.
        print(f"[kavita] INFO fetch host={host_id} url={lib_url} -> HTTP 204 "
              f"(no libraries configured)")
    elif r.status_code == 200:
        try:
            libraries = r.json()
        except (ValueError, TypeError):  # noqa: BLE001
            raise RuntimeError("upstream returned non-JSON")
        if not isinstance(libraries, list):
            libraries = []
    else:
        print(f"[kavita] error: fetch host={host_id} url={lib_url} returned "
              f"HTTP {r.status_code} (check the chip URL points at the Kavita "
              f"root, e.g. https://kavita.example.com)")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {lib_url}")
    out: dict[str, Any] = {
        "available": True,
        "libraries": len(libraries),
        "series_count": safe_int(series),
        "volume_count": safe_int(volumes),
        "chapter_count": safe_int(chapters),
        "total_size": safe_int(total_size),
        # Reading-activity (P1) — from the same server-stats response; 0 / "" for
        # a non-admin key or older Kavita (the card just hides those chips).
        "top_read_series": str(reading.get("top_read_series") or ""),
        "top_read_count": safe_int(reading.get("top_read_count")),
        "recently_read_count": safe_int(reading.get("recently_read_count")),
        "active_readers": safe_int(reading.get("active_readers")),
        "reading_minutes": safe_int(reading.get("reading_minutes")),
        "version": version,
        "fetched_at": int(now),
        # Library-growth retention trend — best-effort; the sampler may have no
        # rows yet (fresh pin) → zeroed shape.
        "trend": _safe_trend(str(host_id or ""), int(service_idx or 0)),
    }
    print(f"[kavita] INFO fetched host={host_id} libraries={out['libraries']} "
          f"series={out['series_count']} volumes={out['volume_count']} "
          f"chapters={out['chapter_count']} size={out['total_size']}")
    _data_cache[ck] = (now, out)
    return out


def _safe_trend(host_id: str, service_idx: int) -> dict:
    """Best-effort ``kavita_sampler.trend_summary`` — a zeroed shape on any error
    (a fresh pin with no samples, or an import hiccup) so the card never fails on
    the trend embed."""
    try:
        from logic.apps import kavita_sampler as _s  # noqa: PLC0415
        return _s.trend_summary(host_id, service_idx)
    except (ImportError, RuntimeError, ValueError):
        return {"days": 0, "samples": 0, "latest_series": 0, "latest_size": 0,
                "series_added": 0, "series_series": [], "size_series": []}


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "libraries": safe_int(data.get("libraries")),
        "series_count": safe_int(data.get("series_count")),
        "volume_count": safe_int(data.get("volume_count")),
        "chapter_count": safe_int(data.get("chapter_count")),
        "total_size": safe_int(data.get("total_size")),
        "top_read_series": str(data.get("top_read_series") or ""),
        "recently_read_count": safe_int(data.get("recently_read_count")),
        "active_readers": safe_int(data.get("active_readers")),
        "reading_minutes": safe_int(data.get("reading_minutes")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
# noinspection PyUnusedLocal
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None,
                    actor_username: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown
    skill id. ``arg`` carries the free-form search term (kavita_search)."""
    if skill_id == "kavita_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "kavita_libraries":
        return await _libraries_skill(host_row, chip, host_id=host_id)
    if skill_id == "kavita_recently_added":
        return await _recently_added_skill(host_row, chip, host_id=host_id)
    if skill_id == "kavita_on_deck":
        return await _on_deck_skill(host_row, chip, host_id=host_id)
    if skill_id == "kavita_most_read":
        return await _most_read_skill(host_row, chip, host_id=host_id)
    if skill_id == "kavita_search":
        return await _search_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "kavita_scan":
        return await _scan_skill(host_row, chip, host_id=host_id)
    if skill_id == "kavita_scan_library":
        return await _scan_library_skill(host_row, chip, arg=arg, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _resolve_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(api_key, base)`` or return a ready ``{ok: False, detail}`` —
    the Kavita analogue of the shared ``resolve_skill_target`` (Kavita doesn't
    use ``_servarr``)."""
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        return "", "", {"ok": False, "status": 0, "detail": "Kavita api_key not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return api_key, base, None


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the current library summary (force-bypasses the
    cache). Never raises."""
    print(f"[kavita] INFO kavita_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[kavita] warning: kavita_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    libraries = safe_int(data.get("libraries"))
    series = safe_int(data.get("series_count"))
    volumes = safe_int(data.get("volume_count"))
    chapters = safe_int(data.get("chapter_count"))
    size = _fmt_bytes(data.get("total_size"))
    lines = [
        f"📚 Libraries: {libraries:,}",
        f"📖 Series: {series:,}",
        f"📦 Volumes: {volumes:,}",
        f"📄 Chapters: {chapters:,}",
    ]
    if size:
        lines.append(f"💾 Size: {size}")
    # Reading activity (P1) — only when the admin-stats response carried it.
    top_read = str(data.get("top_read_series") or "").strip()
    top_count = safe_int(data.get("top_read_count"))
    active = safe_int(data.get("active_readers"))
    read_time = _fmt_reading_time(data.get("reading_minutes"))
    if top_read:
        lines.append(f"🏆 Most read: {top_read}"
                     + (f" ({top_count:,} reads)" if top_count else ""))
    if active:
        lines.append(f"👥 Active readers: {active:,}")
    if read_time:
        lines.append(f"⏱️ Reading time: {read_time}")
    # Reading-activity RATE — avg minutes/day from the kavita_sampler's diffed
    # cumulative reading time (only once the sampler has ≥ 2 days of history).
    _tr = as_dict(data.get("trend"))
    rd_avg = round(float(_tr.get("reading_avg") or 0))
    if rd_avg > 0:
        lines.append(f"📈 Reading ~{rd_avg:,} min/day "
                     f"(last {safe_int(_tr.get('days')) or 30}d)")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "libraries": libraries, "series_count": series, "volume_count": volumes,
        "chapter_count": chapters, "total_size": safe_int(data.get("total_size")),
        "top_read_series": top_read, "active_readers": active,
        "reading_minutes": safe_int(data.get("reading_minutes")),
        "reading_avg_per_day": rd_avg,
    }


async def _libraries_skill(host_row: dict, chip: dict, *,
                           host_id: Optional[str] = None) -> dict:
    """Read-only: list configured libraries + their type from ``GET /api/Library``.
    Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[kavita] INFO kavita_libraries host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            token, _ = await _authenticate(cli, base, api_key)
            r = await cli.get(base + "/api/Library", headers=_bearer(token))
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"library fetch failed: {type(e).__name__}: {e}"}
    # 204 No Content = reachable Kavita with no libraries (the empty init falls
    # through to the "No libraries configured" reply below), not an error.
    items: list = []
    if r.status_code not in (200, 204):
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    if r.status_code == 200:
        try:
            items = r.json()
        except (ValueError, TypeError):
            return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
        if not isinstance(items, list):
            items = []
    lines = []
    rich: list[dict] = []
    for lib in items[:25]:
        if not isinstance(lib, dict):
            continue
        name = str(lib.get("name") or "?").strip()
        ltype = _LIBRARY_TYPES.get(safe_int(lib.get("type")), "")
        lines.append(f"• {name}" + (f" ({ltype})" if ltype else ""))
        row: dict = {"title": name, "subtitle": ltype}
        lid = safe_int(lib.get("id"))
        if lid:
            # Per-row 🔄 Scan button → rescan THIS library only (non-destructive —
            # a scan is additive). `lib:<id>` is the exact-id arg the scan skill
            # resolves; AI / Telegram call it by name.
            row["row_action"] = {
                "skill_id": "kavita_scan_library", "arg": f"lib:{lid}",
                "icon": "refresh-cw", "destructive": False,
                "title_i18n": "apps.kavita.scan_row"}
        rich.append(row)
    if not lines:
        return {"ok": True, "status": 200, "detail": "📚 No libraries configured."}
    return {"ok": True, "status": 200,
            "detail": f"📚 Libraries ({len(lines):,}):\n" + "\n".join(lines),
            "count": len(rich), "count_i18n": "apps.kavita.libraries_count",
            "items": rich}


#   The auth + httpx-client + error-branch scaffolding below is shape-similar to
#   the sibling list skills — the sanctioned per-app encapsulation pattern; each
#   differs in endpoint + parse, so it stays inline.
# noinspection DuplicatedCode
async def _recently_added_skill(host_row: dict, chip: dict, *,
                                host_id: Optional[str] = None) -> dict:
    """Read-only: recently-updated series (Kavita's stable "what's new" feed) via
    ``POST /api/Series/recently-updated-series``, rendered as a rich poster list
    (covers through the image proxy). Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[kavita] INFO kavita_recently_added host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            token, _ = await _authenticate(cli, base, api_key)
            r = await cli.post(base + "/api/Series/recently-updated-series",
                               headers=_bearer(token))
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    if r.status_code not in (200, 204):
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    series: list = []
    if r.status_code == 200:
        try:
            body = r.json()
        except (ValueError, TypeError):
            return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
        if isinstance(body, list):
            series = body
    if not series:
        return {"ok": True, "status": 200,
                "detail": "📚 Nothing has been added to Kavita recently."}
    lines: list = []
    items: list = []
    for s in series[:20]:
        it = _recent_item(s)
        if not it:
            continue
        cnt = safe_int(s.get("count"))  # s is a dict (else _recent_item returned None)
        lines.append(f"• {it['title']}" + (f" ({cnt:,} new)" if cnt else ""))
        items.append(it)
    out: dict = {"ok": True, "status": 200,
                 "detail": "🆕 Recently added to Kavita:\n" + "\n".join(lines)}
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = "apps.kavita.recently_added_count"
    return out


async def _on_deck_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None) -> dict:
    """Read-only: on-deck / continue-reading series via ``POST
    /api/Series/on-deck``, a rich poster list with read-progress bars. The
    endpoint shape is version-volatile (bare list vs paginated wrapper, and some
    builds reject the body) — degrades gracefully on a non-200. Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[kavita] INFO kavita_on_deck host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            token, _ = await _authenticate(cli, base, api_key)
            r = await cli.post(base + "/api/Series/on-deck", headers=_bearer(token),
                               params={"pageNumber": 1, "pageSize": 20}, json={})
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    if r.status_code not in (200, 204):
        return {"ok": True, "status": 200,
                "detail": (f"📖 Couldn't read your on-deck list (Kavita returned "
                           f"HTTP {r.status_code}) — the continue-reading endpoint "
                           "varies by Kavita version.")}
    series: list = []
    if r.status_code == 200:
        try:
            body = r.json()
        except (ValueError, TypeError):
            return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
        if isinstance(body, list):
            series = body
        elif isinstance(body, dict):
            for key in ("result", "results", "items", "data"):
                v = body.get(key)
                if isinstance(v, list):
                    series = v
                    break
    if not series:
        return {"ok": True, "status": 200,
                "detail": "📖 Nothing on deck — you're all caught up on Kavita."}
    lines: list = []
    items: list = []
    for s in series[:20]:
        it = _ondeck_item(s)
        if not it:
            continue
        sub = it.get("subtitle")
        lines.append(f"• {it['title']}" + (f" — {sub}" if sub else ""))
        items.append(it)
    out: dict = {"ok": True, "status": 200,
                 "detail": "📖 On deck (continue reading):\n" + "\n".join(lines)}
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = "apps.kavita.on_deck_count"
    return out


# noinspection DuplicatedCode
async def _most_read_skill(host_row: dict, chip: dict, *,
                           host_id: Optional[str] = None) -> dict:
    """Read-only: the most-read series (from the admin ``/api/Stats/server/stats``
    response), rendered as a rich poster list (covers via the image proxy). Needs
    an admin api_key; a non-admin key returns the friendly 'no data' reply. Never
    raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[kavita] INFO kavita_most_read host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            token, _ = await _authenticate(cli, base, api_key)
            r = await cli.get(base + "/api/Stats/server/stats", headers=_bearer(token))
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": True, "status": 200,
                "detail": ("📊 The most-read stats need an admin Kavita api_key "
                           "(this key isn't an admin's).")}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        body = r.json() or {}
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    most_read = as_list(as_dict(body).get("mostReadSeries"))
    if not most_read:
        return {"ok": True, "status": 200,
                "detail": "📊 No most-read data yet on Kavita."}
    lines: list = []
    items: list = []
    for row in most_read[:15]:
        it = _most_read_item(row)
        if not it:
            continue
        sub = it.get("subtitle")
        lines.append(f"• {it['title']}" + (f" — {sub}" if sub else ""))
        items.append(it)
    out: dict = {"ok": True, "status": 200,
                 "detail": "🏆 Most read on Kavita:\n" + "\n".join(lines)}
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = "apps.kavita.most_read_count"
    return out


async def _search_skill(host_row: dict, chip: dict, *,
                        arg: Optional[str] = None,
                        host_id: Optional[str] = None) -> dict:
    """Read-only (arg): global search for a term via ``GET /api/Search/search``;
    returns the top series matches. Never raises."""
    term = (arg or "").strip()
    if not term:
        return {"ok": False, "status": 0,
                "detail": "no search term given — say e.g. 'search dune on kavita'"}
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[kavita] INFO kavita_search host={host_id} term={term!r} (live search)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            token, _ = await _authenticate(cli, base, api_key)
            r = await cli.get(base + "/api/Search/search", headers=_bearer(token),
                              params={"queryString": term})
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"search failed: {type(e).__name__}: {e}"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        body = r.json() or {}
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    series = body.get("series") if isinstance(body, dict) else None
    if not isinstance(series, list):
        series = []
    lines = []
    for s in series[:10]:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or s.get("seriesName") or "?").strip()
        lib = str(s.get("libraryName") or "").strip()
        lines.append(f"• {name}" + (f" ({lib})" if lib else ""))
    if not lines:
        return {"ok": True, "status": 200,
                "detail": f"🔍 No series in your library match “{term}”."}
    return {"ok": True, "status": 200,
            "detail": f"🔍 Top matches for “{term}”:\n" + "\n".join(lines)}


async def _scan_skill(host_row: dict, chip: dict, *,
                      host_id: Optional[str] = None) -> dict:
    """Action: trigger a scan of EVERY library. Kavita scans per-library, so we
    fetch the library list and ``POST /api/Library/scan?libraryId=`` each. Never
    raises — reports how many libraries were queued."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[kavita] INFO kavita_scan host={host_id} (scan all libraries)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            token, _ = await _authenticate(cli, base, api_key)
            lr = await cli.get(base + "/api/Library", headers=_bearer(token))
            # 204 No Content = no libraries configured — nothing to scan.
            if lr.status_code == 204:
                return {"ok": True, "status": 200,
                        "detail": "📚 No libraries configured — nothing to scan."}
            if lr.status_code != 200:
                return {"ok": False, "status": lr.status_code,
                        "detail": f"could not list libraries: HTTP {lr.status_code}"}
            try:
                libs = lr.json()
            except (ValueError, TypeError):
                libs = []
            if not isinstance(libs, list):
                libs = []
            queued = 0
            for lib in libs:
                if not isinstance(lib, dict):
                    continue
                lib_id = lib.get("id")
                if lib_id is None:
                    continue
                try:
                    sr = await cli.post(base + "/api/Library/scan",
                                        headers=_bearer(token),
                                        params={"libraryId": lib_id, "force": "false"})
                    if 200 <= sr.status_code < 300:
                        queued += 1
                except (httpx.HTTPError, OSError):
                    continue
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"scan failed: {type(e).__name__}: {e}"}
    if queued <= 0:
        return {"ok": False, "status": 0,
                "detail": "no libraries could be scanned (check the api_key has access)"}
    return {"ok": True, "status": 200,
            "detail": f"🔄 Started a scan of {queued:,} "
                      f"librar{'y' if queued == 1 else 'ies'} on Kavita."}


async def _resolve_library(cli: httpx.AsyncClient, base: str, token: str,
                           needle: str) -> "tuple[int, str] | dict":
    """Resolve a scan arg to ``(library_id, name)``. ``needle`` is either the
    per-row button's exact ``lib:<id>`` form OR a free-text library name (the AI /
    Telegram path) matched against the configured library list. Returns the tuple,
    or a ready ``{ok, status, detail}`` error dict for the caller to return."""
    n = needle.strip()
    libs: list = []
    try:
        lr = await cli.get(base + "/api/Library", headers=_bearer(token))
        if lr.status_code == 200:
            _body = lr.json()
            libs = _body if isinstance(_body, list) else []
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        libs = []
    if n.lower().startswith("lib:"):
        wanted = safe_int(n.split(":", 1)[1])
        for lib in libs:
            if isinstance(lib, dict) and safe_int(lib.get("id")) == wanted:
                return wanted, str(lib.get("name") or f"library #{wanted}").strip()
        return {"ok": False, "status": 404,
                "detail": "that library isn't configured on Kavita anymore"}
    nl = n.lower()
    for lib in libs:
        if isinstance(lib, dict) and nl in str(lib.get("name") or "").strip().lower():
            return safe_int(lib.get("id")), str(lib.get("name") or "").strip()
    return {"ok": False, "status": 404,
            "detail": f"no Kavita library matched “{n}”"}


async def _scan_library_skill(host_row: dict, chip: dict, *,
                              arg: Optional[str] = None,
                              host_id: Optional[str] = None) -> dict:
    """Action (arg): rescan ONE library by id (the per-row button) or name (the
    AI / Telegram path), via ``POST /api/Library/scan?libraryId=<id>``.
    Non-destructive — a scan is additive. Never raises."""
    needle = (arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no library given — say e.g. 'scan the manga library'"}
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[kavita] INFO kavita_scan_library host={host_id} target={needle!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            token, _ = await _authenticate(cli, base, api_key)
            target = await _resolve_library(cli, base, token, needle)
            if isinstance(target, dict):  # not-found error dict
                return target
            lib_id, name = target
            sr = await cli.post(base + "/api/Library/scan", headers=_bearer(token),
                                params={"libraryId": lib_id, "force": "false"})
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"scan failed: {type(e).__name__}: {e}"}
    if 200 <= sr.status_code < 300:
        return {"ok": True, "status": 200,
                "detail": f"🔄 Started a scan of the “{name}” library on Kavita."}
    if sr.status_code in (401, 403):
        return {"ok": False, "status": sr.status_code, "detail": "auth failed (check api_key has access)"}
    return {"ok": False, "status": sr.status_code,
            "detail": f"scan of “{name}” returned HTTP {sr.status_code}"}
