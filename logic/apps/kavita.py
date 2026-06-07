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
from logic.coerce import safe_int

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
)

# Per-(host_id, service_idx) data cache for the expanded card. 60s default —
# matches the rest of the family.
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Kavita authenticates via an API key exchanged for a JWT; the editor MUST
    render the api_key input + Test-connection button."""
    return True


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
            series = volumes = chapters = total_size = 0
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
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                series = volumes = chapters = total_size = 0
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
        "version": version,
        "fetched_at": int(now),
    }
    print(f"[kavita] INFO fetched host={host_id} libraries={out['libraries']} "
          f"series={out['series_count']} volumes={out['volume_count']} "
          f"chapters={out['chapter_count']} size={out['total_size']}")
    _data_cache[ck] = (now, out)
    return out


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
    skill id. ``arg`` carries the free-form search term (kavita_search)."""
    if skill_id == "kavita_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "kavita_libraries":
        return await _libraries_skill(host_row, chip, host_id=host_id)
    if skill_id == "kavita_search":
        return await _search_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "kavita_scan":
        return await _scan_skill(host_row, chip, host_id=host_id)
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
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "libraries": libraries, "series_count": series, "volume_count": volumes,
        "chapter_count": chapters, "total_size": safe_int(data.get("total_size")),
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
    # 204 No Content = reachable Kavita with no libraries (falls through to the
    # "No libraries configured" reply below), not an error.
    items: list = []
    if r.status_code == 204:
        items = []
    elif r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    else:
        try:
            items = r.json()
        except (ValueError, TypeError):
            return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
        if not isinstance(items, list):
            items = []
    lines = []
    for lib in items[:25]:
        if not isinstance(lib, dict):
            continue
        name = str(lib.get("name") or "?").strip()
        ltype = _LIBRARY_TYPES.get(safe_int(lib.get("type")), "")
        lines.append(f"• {name}" + (f" ({ltype})" if ltype else ""))
    if not lines:
        return {"ok": True, "status": 200, "detail": "📚 No libraries configured."}
    return {"ok": True, "status": 200,
            "detail": f"📚 Libraries ({len(lines):,}):\n" + "\n".join(lines)}


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
