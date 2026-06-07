"""Prowlarr per-app module.

Encapsulates everything Prowlarr-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Prowlarr is the INDEXER
manager of the *arr stack — it doesn't manage a media library, it manages
a set of indexers (trackers / Usenet) and syncs them out to the connected
*arr apps (Radarr / Sonarr / Lidarr / Readarr). So its API is ``/api/v1``
(like Lidarr / Readarr) but its DOMAIN is different: NO calendar / upcoming,
NO download queue, NO media disks.

Public surface mirrors the rest of the *arr family
(``lidarr.py`` / ``readarr.py``):

    SLUGS               — catalog slugs this module handles ("prowlarr").
    requires_api_key()  — True (Prowlarr authenticates via the X-Api-Key header).
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + indexers (read) + app-sync (action)
                          + search (arg, read).

The expanded card answers "how many indexers are enabled, how many apps are
synced, and how busy has Prowlarr been" at a glance:

    indexers_total    — every indexer configured   (GET /api/v1/indexer)
    indexers_enabled  — indexers currently enabled
    apps_synced       — connected *arr applications (GET /api/v1/applications)
    queries           — lifetime indexer queries    (GET /api/v1/indexerstats)
    grabs             — lifetime indexer grabs       (GET /api/v1/indexerstats)
    health_issues     — active health warnings       (GET /api/v1/health)
    version           — Prowlarr version             (GET /api/v1/system/status)

AI / Telegram skills
--------------------
* ``prowlarr_status``     — summary (live fetch): indexers / apps / queries / grabs.
* ``prowlarr_indexers``   — list configured indexers + their enabled state.
* ``prowlarr_app_sync``   — trigger ``ApplicationIndexerSync`` (push indexers to
                            every connected app). Non-destructive background command.
* ``prowlarr_search``     — (arg) manual search a term across every indexer
                            (``GET /api/v1/search``); returns the top results.

There is deliberately NO destructive skill — deleting an indexer / application
via the AI is risky and wasn't requested; the family pattern allows per-app
variation, and Prowlarr's safe surface is read + sync.

Auth model: every authenticated Prowlarr v1 endpoint takes the ``X-Api-Key``
header (Prowlarr → Settings → General → API Key). The credential probe hits the
auth-required ``/api/v1/system/status`` so a bad key fails loudly.
Single-instance app (NOT fleet) — one card per pinned chip.

Upstream API reference: <prowlarr-host>/api/v1 (Swagger at /api). Endpoints:
    GET  /api/v1/system/status — version (test-credential probe + footnote)
    GET  /api/v1/indexer       — configured indexers (total / enabled)
    GET  /api/v1/applications  — connected *arr apps (synced count)
    GET  /api/v1/indexerstats  — per-indexer query / grab counters
    GET  /api/v1/health        — active health issues
    GET  /api/v1/search?query= — manual search across indexers
    POST /api/v1/command       — ApplicationIndexerSync
"""
from __future__ import annotations

import time
from functools import partial as _partial
from typing import Any, Optional

import httpx

from logic.apps import _servarr
from logic.apps._common import cache_key, fetch_gate, peek_cache, resolve_cache_ttl
from logic.coerce import safe_int

# Servarr-family shared helpers (logic/apps/_servarr.py) bound to Prowlarr's
# api version (v1) + brand, aliased to the historical underscore names so the
# skill bodies' call sites stay unchanged. Prowlarr manages no media library /
# disks, so it does NOT bind parse_disks / primary_disk / find_in_library.
_headers = _servarr.headers
_version_from = _servarr.version_from
_fetch_version = _partial(_servarr.fetch_version, api_version="v1")
_resolve_skill_target = _partial(_servarr.resolve_skill_target, app_label="Prowlarr")
_command_skill = _partial(_servarr.command_skill, app_label="Prowlarr", api_version="v1")

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("prowlarr",)

# Read skills surface as one-click drawer buttons AND AI / Telegram actions;
# the ``arg``-carrying search skill is AI / Telegram only (the dispatch supplies
# the term from natural language) — mirrors the *arr "info / lookup" arg skills.
SKILLS: tuple[dict, ...] = (
    {
        "id": "prowlarr_status",
        "name": "Prowlarr status",
        "ai_phrases": ("prowlarr status, indexer manager status, how many "
                       "indexers, indexer health, prowlarr health, how many "
                       "apps synced, indexer queries, indexer grabs"),
        "destructive": False,
    },
    {
        "id": "prowlarr_indexers",
        "name": "List indexers",
        "ai_phrases": ("list my indexers, what indexers do i have, show "
                       "prowlarr indexers, which indexers are enabled, "
                       "disabled indexers, prowlarr indexer list"),
        "destructive": False,
    },
    {
        "id": "prowlarr_app_sync",
        "name": "Sync indexers to apps",
        "ai_phrases": ("sync indexers to my apps, prowlarr app sync, push "
                       "indexers to radarr and sonarr, sync prowlarr, "
                       "application indexer sync, resync indexers"),
        "destructive": False,
    },
    {
        "id": "prowlarr_search",
        "name": "Search indexers",
        "ai_phrases": ("search <term> on prowlarr, search my indexers for "
                       "<term>, manual search <term>, find <term> across "
                       "indexers, prowlarr search <term>"),
        # arg-carrying → AI / Telegram only (the dispatch supplies the term from
        # natural language). `arg: True` keeps it OUT of the app-drawer button
        # list (app-apps-drawer.js filters `sk.arg === true`) — a drawer button
        # has no way to provide the search term, so clicking it would just error
        # "no search term given". Mirrors the *arr info / add / remove arg skills.
        "arg": True,
        "destructive": False,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. 60s default —
# the indexer + stats calls change slowly (matches the rest of the family).
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Prowlarr authenticates every v1 endpoint via X-Api-Key; the editor MUST
    render the api_key input + Test-connection button."""
    return True


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Prowlarr's auth-required ``/api/v1/system/status`` — delegates to the
    shared Servarr probe bound to Prowlarr's brand + api version."""
    return await _servarr.test_credential(host_row, chip, candidate_key,
                                          app_label="Prowlarr", api_version="v1")


def _sum_indexer_stats(raw: Any) -> "tuple[int, int]":
    """Sum lifetime ``numberOfQueries`` + ``numberOfGrabs`` across every indexer
    in a ``/api/v1/indexerstats`` payload. Returns ``(queries, grabs)`` — both 0
    on any shape / parse failure (stats are a nice-to-have, never load-bearing)."""
    if not isinstance(raw, dict):
        return 0, 0
    rows = raw.get("indexers")
    if not isinstance(rows, list):
        return 0, 0
    queries = grabs = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        queries += safe_int(r.get("numberOfQueries"))
        grabs += safe_int(r.get("numberOfGrabs"))
    return queries, grabs


# noinspection DuplicatedCode
# The upstream-error guard + JSON-parse block below is structurally shared
# with every other per-app module's fetch_data (radarr / sonarr / …) — the
# deliberate per-app encapsulation pattern (CLAUDE.md). Content differs (app
# name, endpoints, fields), so it stays inline rather than coupling modules.
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Prowlarr's indexer / app / stats summary for the expanded card.

    Returns ``{available, indexers_total, indexers_enabled, apps_synced,
    queries, grabs, health_issues, version, fetched_at}``. Raises ``ValueError``
    / ``RuntimeError`` when the chip's api_key is unset / the base URL won't
    resolve / the primary upstream call errors. The indexer list is
    load-bearing; the rest are tolerated."""
    api_key = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=api_key, log_tag="prowlarr")
    if hit is not None:
        return hit
    indexer_url = base + "/api/v1/indexer"
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(indexer_url, headers=_headers(api_key))
            apps_synced = 0
            try:
                ar = await cli.get(base + "/api/v1/applications",
                                   headers=_headers(api_key))
                if ar.status_code == 200:
                    _aj = ar.json()
                    apps_synced = len(_aj) if isinstance(_aj, list) else 0
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                apps_synced = 0
            queries = grabs = 0
            try:
                sr = await cli.get(base + "/api/v1/indexerstats",
                                   headers=_headers(api_key))
                if sr.status_code == 200:
                    queries, grabs = _sum_indexer_stats(sr.json())
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                queries = grabs = 0
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
        print(f"[prowlarr] error: fetch host={host_id} url={indexer_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        print(f"[prowlarr] error: fetch host={host_id} url={r.request.url} "
              f"returned HTTP {r.status_code} (check the chip URL points at "
              f"the Prowlarr root, e.g. https://prowlarr.example.com)")
        if r.status_code in (401, 403):
            raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                               f"(check api_key) — {indexer_url}")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {indexer_url}")
    try:
        indexers = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    if not isinstance(indexers, list):
        indexers = []
    total = len(indexers)
    enabled = sum(1 for i in indexers if isinstance(i, dict) and i.get("enable"))
    out: dict[str, Any] = {
        "available": True,
        "indexers_total": total,
        "indexers_enabled": enabled,
        "apps_synced": safe_int(apps_synced),
        "queries": safe_int(queries),
        "grabs": safe_int(grabs),
        "health_issues": safe_int(health_issues),
        "version": ver,
        "fetched_at": int(now),
    }
    print(f"[prowlarr] INFO fetched host={host_id} indexers={enabled}/{total} "
          f"apps={out['apps_synced']} queries={out['queries']} "
          f"grabs={out['grabs']} health={out['health_issues']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "indexers_total": safe_int(data.get("indexers_total")),
        "indexers_enabled": safe_int(data.get("indexers_enabled")),
        "apps_synced": safe_int(data.get("apps_synced")),
        "queries": safe_int(data.get("queries")),
        "grabs": safe_int(data.get("grabs")),
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
    skill id. ``arg`` carries the free-form search term (prowlarr_search)."""
    if skill_id == "prowlarr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "prowlarr_indexers":
        return await _indexers_skill(host_row, chip, host_id=host_id)
    if skill_id == "prowlarr_app_sync":
        return await _command_skill(host_row, chip, command="ApplicationIndexerSync",
                                    started_msg="🔄 Started syncing indexers to every "
                                                "connected app on Prowlarr.",
                                    host_id=host_id)
    if skill_id == "prowlarr_search":
        return await _search_skill(host_row, chip, arg=arg, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the current summary (force-bypasses the cache).
    Never raises."""
    print(f"[prowlarr] INFO prowlarr_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[prowlarr] warning: prowlarr_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    total = safe_int(data.get("indexers_total"))
    enabled = safe_int(data.get("indexers_enabled"))
    apps = safe_int(data.get("apps_synced"))
    queries = safe_int(data.get("queries"))
    grabs = safe_int(data.get("grabs"))
    health = safe_int(data.get("health_issues"))
    lines = [
        f"🔍 Indexers: {enabled:,} / {total:,} enabled",
        f"🔗 Apps synced: {apps:,}",
        f"📊 Queries: {queries:,}",
        f"📥 Grabs: {grabs:,}",
        f"{'⚠️' if health else '✅'} Health issues: {health:,}",
    ]
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "indexers_total": total, "indexers_enabled": enabled,
        "apps_synced": apps, "queries": queries, "grabs": grabs,
        "health_issues": health,
    }


async def _indexers_skill(host_row: dict, chip: dict, *,
                          host_id: Optional[str] = None) -> dict:
    """Read-only: list configured indexers + their enabled state from
    ``/api/v1/indexer``. Never raises."""
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[prowlarr] INFO prowlarr_indexers host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/indexer", headers=_headers(api_key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"indexer fetch failed: {type(e).__name__}: {e}"}
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
    # Enabled first, then alphabetical — the disabled ones are the actionable tail.
    rows = [i for i in items if isinstance(i, dict)]
    rows.sort(key=lambda i: (not i.get("enable"), str(i.get("name") or "").lower()))
    enabled = sum(1 for i in rows if i.get("enable"))
    lines = []
    for i in rows[:25]:
        name = str(i.get("name") or "?").strip()
        on = bool(i.get("enable"))
        proto = str(i.get("protocol") or "").strip()
        suffix = f" [{proto}]" if proto else ""
        lines.append(f"{'✅' if on else '⛔'} {name}{suffix}")
    if not lines:
        return {"ok": True, "status": 200, "detail": "🔍 No indexers configured."}
    head = f"🔍 Indexers ({enabled:,} / {len(rows):,} enabled):"
    return {"ok": True, "status": 200, "detail": head + "\n" + "\n".join(lines)}


def _fmt_bytes(n: Any) -> str:
    """Render a byte count as a human size (MiB / GiB / TiB). ``""`` for
    missing / non-positive — release sizes come straight from the indexer."""
    b = safe_int(n)
    if b <= 0:
        return ""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    val = float(b)
    idx = 0
    while val >= 1024 and idx < len(units) - 1:
        val /= 1024
        idx += 1
    return f"{val:,.1f} {units[idx]}"


async def _search_skill(host_row: dict, chip: dict, *,
                        arg: Optional[str] = None,
                        host_id: Optional[str] = None) -> dict:
    """Read-only (arg): manual search a term across every indexer via
    ``GET /api/v1/search``; returns the top results. Never raises. This hits the
    real indexers (generates tracker traffic), so it is operator-initiated only
    and capped to the top results."""
    term = (arg or "").strip()
    if not term:
        return {"ok": False, "status": 0,
                "detail": "no search term given — say e.g. 'search ubuntu on prowlarr'"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[prowlarr] INFO prowlarr_search host={host_id} term={term!r} (live search)")
    try:
        # Manual searches fan out to every indexer, so allow a generous budget.
        async with httpx.AsyncClient(verify=False, timeout=30.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/search", headers=_headers(api_key),
                              params={"query": term, "type": "search"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"search failed: {type(e).__name__}: {e}"}
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
    # Best results first — most seeders, then largest. Grabs/seeders live on the
    # release dict (key varies by protocol); fall back to 0 when absent.
    def _seeders(it):
        return safe_int(it.get("seeders")) if isinstance(it, dict) else 0
    rows = [it for it in items if isinstance(it, dict)]
    rows.sort(key=lambda it: (_seeders(it), safe_int(it.get("size"))), reverse=True)
    lines = []
    for it in rows[:10]:
        title = str(it.get("title") or "?").strip()
        indexer = str(it.get("indexer") or "").strip()
        size = _fmt_bytes(it.get("size"))
        seeders = _seeders(it)
        meta = ", ".join(p for p in (
            indexer,
            size,
            (f"{seeders:,} seeders" if seeders else ""),
        ) if p)
        lines.append(f"• {title}" + (f" ({meta})" if meta else ""))
    if not lines:
        return {"ok": True, "status": 200,
                "detail": f"🔍 No results across your indexers for “{term}”."}
    return {"ok": True, "status": 200,
            "detail": f"🔍 Top results for “{term}”:\n" + "\n".join(lines)}
