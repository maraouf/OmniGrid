"""Bazarr per-app module.

Encapsulates everything Bazarr-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``speedtest_tracker.py`` shape:

    SLUGS               — catalog slugs this module handles.
    requires_api_key()  — True (Bazarr authenticates via X-API-KEY).
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — one read-only "Bazarr status" AI skill.

Bazarr is a subtitle manager (companion to Sonarr / Radarr). The single
most impactful, lowest-cost endpoint is ``GET /api/badges`` — it returns
exactly the actionable counts Bazarr surfaces in its own nav badges:

    episodes   — TV episodes still MISSING subtitles (wanted)
    movies     — movies still MISSING subtitles (wanted)
    providers  — subtitle providers currently THROTTLED / rate-limited
    status     — active health issues

so the expanded card answers "how much is still missing subtitles, and is
anything wrong" at a glance. ``GET /api/system/status`` adds the Bazarr
version (one extra, tolerated-on-failure call).

Auth model: every Bazarr API endpoint requires the ``X-API-KEY`` header
(NOT Bearer). The key is the value from Bazarr's Settings → General → API
key. Single-instance app (NOT fleet) — one card per pinned chip.

Upstream API reference: <bazarr-host>/api/ (Swagger). Endpoints used:
    GET /api/system/status  — test-credential probe + version
    GET /api/badges         — the missing-subtitle / health counts
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl,
    resolve_credential_target)
from logic.coerce import safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("bazarr",)

# Bazarr skills. All no-arg → all surface as one-click drawer buttons AND
# AI / Telegram actions.
#   bazarr_status        — read: missing-subtitle counts + health (badges).
#   bazarr_search_wanted — action: trigger Bazarr's "search for wanted
#                          subtitles" tasks (movies + series) now.
#   bazarr_wanted        — read: list the items currently missing subtitles.
SKILLS: tuple[dict, ...] = (
    {
        "id": "bazarr_status",
        "name": "Bazarr status",
        "ai_phrases": ("bazarr status, how many subtitles are missing, "
                       "missing subtitles, subtitle backlog, how many episodes "
                       "missing subtitles, how many movies missing subtitles, "
                       "bazarr health, throttled subtitle providers"),
        "destructive": False,
    },
    {
        "id": "bazarr_search_wanted",
        "name": "Search for missing subtitles",
        "ai_phrases": ("search for missing subtitles, find missing subtitles, "
                       "download missing subtitles, search wanted subtitles, "
                       "grab subtitles now, bazarr search subtitles, "
                       "look for subtitles"),
        "destructive": False,
    },
    {
        "id": "bazarr_wanted",
        "name": "List missing subtitles",
        "ai_phrases": ("what's missing subtitles, list missing subtitles, "
                       "which movies are missing subtitles, which episodes "
                       "need subtitles, show subtitle backlog, wanted subtitles"),
        "destructive": False,
    },
)

# Per-(host_id, service_idx) data cache. Default TTL overridable per chip
# via the editor's `cache_ttl` field (resolve_cache_ttl). 30s default —
# the badge counts move slowly (a subtitle search runs on a schedule).
DEFAULT_CACHE_TTL_S = 30
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Bazarr authenticates every API endpoint via X-API-KEY; the editor
    MUST render the api_key input + Test-connection button."""
    return True


def _headers(key: str) -> dict:
    return {"X-API-KEY": key, "Accept": "application/json"}


def _version_from(resp) -> str:
    """Extract ``data.bazarr_version`` from an ``/api/system/status``
    response. Returns ``""`` on any non-200 / parse failure (version is
    always a nice-to-have, never load-bearing)."""
    try:
        if getattr(resp, "status_code", 0) != 200:
            return ""
        data = (resp.json() or {}).get("data") or {}
        return str(data.get("bazarr_version") or "").strip()
    except (ValueError, TypeError, AttributeError):
        return ""


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Bazarr's auth-required ``/api/system/status`` with the supplied
    X-API-KEY. Returns ``{ok, detail, status}`` for direct SPA consumption.
    Falls back to the chip's stored ``api_key`` when ``candidate_key`` is
    blank so the operator can re-test after first save without retyping."""
    key, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + "/api/system/status"
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        ver = _version_from(r)
        return {"ok": True, "detail": f"OK (Bazarr {ver})" if ver else "OK",
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check api_key)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


# noinspection DuplicatedCode
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Bazarr's badge counts (+ version) for the expanded card.

    Returns ``{available, episodes_missing, movies_missing,
    providers_throttled, health_issues, version, fetched_at}``. Raises
    ``ValueError`` / ``RuntimeError`` (caller maps to HTTPException) when
    the chip's api_key is unset / the base URL won't resolve / the upstream
    errors."""
    api_key = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=api_key, log_tag="bazarr")
    if hit is not None:
        return hit
    badges_url = base + "/api/badges"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(badges_url, headers=_headers(api_key))
            # Version is a nice-to-have; a failure here must NOT fail the card.
            try:
                ver = _version_from(await cli.get(base + "/api/system/status",
                                                  headers=_headers(api_key)))
            except (httpx.HTTPError, OSError):
                ver = ""
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[bazarr] error: fetch host={host_id} url={badges_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        print(f"[bazarr] error: fetch host={host_id} url={r.request.url} "
              f"returned HTTP {r.status_code} (check the chip URL points at "
              f"the Bazarr root, e.g. https://bazarr.example.com)")
        if r.status_code in (401, 403):
            raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                               f"(check api_key) — {badges_url}")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {badges_url}")
    try:
        body = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    if not isinstance(body, dict):
        body = {}
    out: dict[str, Any] = {
        "available": True,
        "episodes_missing": safe_int(body.get("episodes")),
        "movies_missing": safe_int(body.get("movies")),
        "providers_throttled": safe_int(body.get("providers")),
        "health_issues": safe_int(body.get("status")),
        "version": ver,
        "fetched_at": int(now),
    }
    print(f"[bazarr] INFO fetched host={host_id} episodes_missing="
          f"{out['episodes_missing']} movies_missing={out['movies_missing']} "
          f"throttled={out['providers_throttled']} health={out['health_issues']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``. Returns the last fetched badge counts or
    ``None`` when nothing is cached yet."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "episodes_missing": safe_int(data.get("episodes_missing")),
        "movies_missing": safe_int(data.get("movies_missing")),
        "providers_throttled": safe_int(data.get("providers_throttled")),
        "health_issues": safe_int(data.get("health_issues")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404)."""
    if skill_id == "bazarr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "bazarr_search_wanted":
        return await _search_wanted_skill(host_row, chip, host_id=host_id)
    if skill_id == "bazarr_wanted":
        return await _wanted_skill(host_row, chip, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _resolve_skill_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(api_key, base)`` or a ready ``{ok: False, detail}`` error
    dict for a Bazarr action skill."""
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        return "", "", {"ok": False, "status": 0, "detail": "Bazarr api_key not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return api_key, base, None


# noinspection DuplicatedCode
async def _search_wanted_skill(host_row: dict, chip: dict, *,
                               host_id: Optional[str] = None) -> dict:
    """Action skill: trigger Bazarr's "search for wanted subtitles" tasks
    (movies + series). Discovers the task ids via ``GET /api/system/tasks``
    and matches their NAME on "wanted" — robust across Bazarr versions where
    the job ids drift — then ``POST /api/system/tasks?taskid=<id>`` each.
    Never raises."""
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[bazarr] INFO bazarr_search_wanted host={host_id} (discover + run tasks)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            tr = await cli.get(base + "/api/system/tasks", headers=_headers(api_key))
            if tr.status_code in (401, 403):
                return {"ok": False, "status": tr.status_code, "detail": "auth failed (check api_key)"}
            if tr.status_code != 200:
                return {"ok": False, "status": tr.status_code, "detail": f"HTTP {tr.status_code}"}
            try:
                body = tr.json()
            except (ValueError, TypeError):
                return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
            tasks = body.get("data") if isinstance(body, dict) else body
            tasks = tasks if isinstance(tasks, list) else []
            wanted = []
            for t in tasks:
                if not isinstance(t, dict):
                    continue
                name = str(t.get("name") or "").lower()
                jid = t.get("job_id") or t.get("id")
                jid_l = str(jid or "").lower()
                # Bazarr's task NAME is "Search for Missing <Series|Movies>
                # Subtitles" (the word "wanted" lives only in the JOB_ID,
                # "wanted_search_missing_subtitles_<series|movies>"). Match
                # EITHER side, version-tolerantly — the old name-only "wanted"
                # match found nothing because the name says "Missing".
                hay = name + " " + jid_l
                is_wanted = ("subtitle" in hay
                             and ("wanted" in hay or "missing" in hay)
                             and ("serie" in hay or "movie" in hay))
                if jid and is_wanted:
                    wanted.append((str(jid), str(t.get("name") or jid)))
            if not wanted:
                return {"ok": False, "status": 404,
                        "detail": "couldn't find Bazarr's wanted-subtitle search tasks "
                                  "(check the Bazarr version / that Series + Movies are enabled)"}
            ran = []
            for jid, nm in wanted:
                pr = await cli.post(base + "/api/system/tasks",
                                    headers=_headers(api_key), params={"taskid": jid})
                if pr.status_code in (200, 201, 204):
                    ran.append(nm)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"search failed: {type(e).__name__}: {e}"}
    if not ran:
        return {"ok": False, "status": 502, "detail": "Bazarr rejected the search task(s)"}
    return {"ok": True, "status": 200,
            "detail": "🔍 Started Bazarr subtitle search:\n"
                      + "\n".join(f"  • {n}" for n in ran)}


def _wanted_title(item: dict) -> str:
    """Best display title for a wanted-subtitle row across Bazarr's movie
    (``title``) and episode (``seriesTitle`` + ``episode_number`` +
    ``episodeTitle``) shapes."""
    if not isinstance(item, dict):
        return ""
    series = str(item.get("seriesTitle") or "").strip()
    if series:
        epn = str(item.get("episode_number") or "").strip()
        et = str(item.get("episodeTitle") or "").strip()
        tail = " ".join(p for p in (epn, ("- " + et) if et else "") if p).strip()
        return f"{series}" + (f" {tail}" if tail else "")
    return str(item.get("title") or item.get("radarrTitle") or "").strip()


def _missing_langs(item: dict) -> str:
    """Compact list of the languages still missing for a wanted row, from the
    ``missing_subtitles`` list ([{name, code2, ...}]). '' when none."""
    if not isinstance(item, dict):
        return ""
    ms = item.get("missing_subtitles")
    if not isinstance(ms, list):
        return ""
    names: list[str] = []
    for s in ms:
        if isinstance(s, dict):
            n = str(s.get("name") or s.get("code2") or "").strip()
            if n and n not in names:
                names.append(n)
    return ", ".join(names[:4])


async def _poster_map(client, base: str, api_key: str, endpoint: str,
                      id_param: str, id_field: str, ids: list) -> dict:
    """Batch-fetch ``{id: {poster, year}}`` for a set of ids from a Bazarr
    metadata endpoint. The /api/movies/wanted + /api/episodes/wanted endpoints
    carry NO poster (or year) — only the radarrId / sonarrSeriesId — so we
    resolve them in ONE call via ``GET <endpoint>?<id_param>[]=...`` which DOES
    return ``poster`` + ``year``. ``{}`` on any failure (posters are
    best-effort; the row still renders title + missing-langs)."""
    if not ids:
        return {}
    try:
        params = [(id_param + "[]", i) for i in ids]
        params.append(("length", "500"))
        r = await client.get(base + endpoint, headers=_headers(api_key), params=params)
        if r.status_code != 200:
            return {}
        body = r.json()
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return {}
    rows = body.get("data") if isinstance(body, dict) else body
    rows = rows if isinstance(rows, list) else []
    out: dict = {}
    for m in rows:
        if not isinstance(m, dict):
            continue
        mid = safe_int(m.get(id_field))
        if not mid:
            continue
        poster = str(m.get("poster") or "").strip()
        yr = str(m.get("year") or "").strip()
        out[mid] = {"poster": poster,
                    "year": yr[:4] if len(yr) >= 4 and yr[:4].isdigit() else ""}
    return out


def _stamp_poster(row: dict, meta: dict) -> None:
    """Stamp a poster path from a metadata-map entry onto a rich item, routed
    through the per-app image proxy (Bazarr serves the poster behind the
    X-API-KEY). No-op when the entry has no poster."""
    p = str((meta or {}).get("poster") or "").strip()
    if p:
        row["poster"] = p
        row["poster_proxy"] = True


def image_proxy_url(host_row: dict, chip: dict, path: str) -> "tuple[str, dict]":
    """Per-app image-proxy hook — resolve a Bazarr poster path to an absolute
    upstream URL + the X-API-KEY header. Bazarr posters are relative paths
    served off the chip's own host behind auth; we join them to the configured
    base and attach the key server-side. SSRF guard: only a clean relative
    ``/...`` path (no scheme, no traversal) is accepted."""
    api_key = (chip.get("api_key") or "").strip()
    p = (path or "").strip()
    if not p:
        raise ValueError("empty poster path")
    if "://" in p or not p.startswith("/") or ".." in p:
        raise ValueError("poster must be a clean absolute path")
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured")
    return base.rstrip("/") + p, _headers(api_key)


async def _wanted_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None) -> dict:
    """Read-only skill: list the items currently missing subtitles (top few
    movies + episodes) from ``/api/movies/wanted`` + ``/api/episodes/wanted``.
    Never raises."""
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[bazarr] INFO bazarr_wanted host={host_id} (live fetch)")

    async def _fetch_wanted(client, sub_path):
        try:
            r = await client.get(base + sub_path, headers=_headers(api_key),
                                 params={"length": "50", "start": "0"})
            if r.status_code != 200:
                return [], 0, r.status_code
            body = r.json()
        except (httpx.HTTPError, OSError, ValueError, TypeError):
            return [], 0, 0
        rows = body.get("data") if isinstance(body, dict) else body
        rows = rows if isinstance(rows, list) else []
        total = safe_int(body.get("total")) if isinstance(body, dict) else len(rows)
        return rows, (total or len(rows)), 200

    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            movies, m_total, m_code = await _fetch_wanted(cli, "/api/movies/wanted")
            eps, e_total, e_code = await _fetch_wanted(cli, "/api/episodes/wanted")
            if m_code in (401, 403) or e_code in (401, 403):
                return {"ok": False, "status": 401, "detail": "auth failed (check api_key)"}
            if m_total == 0 and e_total == 0:
                return {"ok": True, "status": 200, "detail": "✅ Nothing is missing subtitles."}
            # The wanted endpoints carry NO poster/year — only radarrId /
            # sonarrSeriesId. Resolve posters + (movie) year in ONE batch call
            # each from the metadata endpoints that DO return them.
            shown_movies = [m for m in movies[:8] if isinstance(m, dict)]
            shown_eps = [e for e in eps[:8] if isinstance(e, dict)]
            movie_ids = [i for i in (safe_int(m.get("radarrId")) for m in shown_movies) if i]
            series_ids = list({i for i in
                               (safe_int(e.get("sonarrSeriesId")) for e in shown_eps) if i})
            movie_meta = await _poster_map(cli, base, api_key, "/api/movies",
                                           "radarrid", "radarrId", movie_ids)
            series_meta = await _poster_map(cli, base, api_key, "/api/series",
                                            "seriesid", "sonarrSeriesId", series_ids)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    lines = []
    # Rich rows for the drawer's poster-thumbnail card — grouped Movies /
    # Episodes. Posters route through the per-app image proxy (poster_proxy=true)
    # so the X-API-KEY stays server-side. Subtitle = movie year + the languages
    # still missing; episode title carries the series + SxxEyy label.
    rich: list[dict] = []
    if m_total:
        lines.append(f"🎬 Movies missing subtitles: {m_total:,}")
        for m in shown_movies:
            t = _wanted_title(m)
            if not t:
                continue
            meta = movie_meta.get(safe_int(m.get("radarrId"))) or {}
            yr = str(meta.get("year") or "").strip()
            langs = _missing_langs(m)
            lines.append(f"  • {t}" + (f" ({yr})" if yr else ""))
            sub = " · ".join(p for p in (yr, (f"missing {langs}" if langs else "")) if p)
            row: "dict[str, Any]" = {"title": t, "subtitle": sub,
                                     "group": "apps.bazarr.group_movies"}
            _stamp_poster(row, meta)
            rich.append(row)
    if e_total:
        lines.append(f"📺 Episodes missing subtitles: {e_total:,}")
        for ep in shown_eps:
            t = _wanted_title(ep)
            if not t:
                continue
            langs = _missing_langs(ep)
            lines.append(f"  • {t}")
            row = {"title": t, "subtitle": (f"missing {langs}" if langs else ""),
                   "group": "apps.bazarr.group_episodes"}
            _stamp_poster(row, series_meta.get(safe_int(ep.get("sonarrSeriesId"))) or {})
            rich.append(row)
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "count": (m_total + e_total),
            "count_i18n": "apps.bazarr.missing_count", "items": rich}


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only skill: live-fetch the current badge counts (force-bypasses
    the cache) and return a formatted ``detail`` for the AI / drawer. Never
    raises — upstream / config failures come back as ``{ok: False, detail}``."""
    print(f"[bazarr] INFO bazarr_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[bazarr] warning: bazarr_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    em = safe_int(data.get("episodes_missing"))
    mm = safe_int(data.get("movies_missing"))
    thr = safe_int(data.get("providers_throttled"))
    hi = safe_int(data.get("health_issues"))
    lines = [
        f"📺 Episodes missing subtitles: {em:,}",
        f"🎬 Movies missing subtitles: {mm:,}",
    ]
    if thr:
        lines.append(f"⏳ Throttled providers: {thr:,}")
    lines.append(f"{'⚠️' if hi else '✅'} Health issues: {hi:,}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "episodes_missing": em,
        "movies_missing": mm,
        "providers_throttled": thr,
        "health_issues": hi,
    }
