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

import asyncio
import re
import time
from functools import partial as _partial
from typing import Any, Optional

import httpx

from logic.apps import _servarr
from logic.apps._common import cache_key, fetch_gate, peek_cache, resolve_cache_ttl
from logic.coerce import as_list, safe_int

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
        "id": "prowlarr_indexer_stats",
        "name": "Indexer stats",
        "ai_phrases": ("indexer stats, which indexer is failing, indexer failure "
                       "rate, slowest indexer, fastest indexer, which indexer is "
                       "slow, indexer performance, most failing indexer, indexer "
                       "queries and grabs, indexer response times, failing indexers"),
        "destructive": False,
    },
    {
        "id": "prowlarr_enable_indexer",
        "name": "Enable an indexer",
        "ai_phrases": ("enable indexer <name>, turn on <name>, enable <name> on "
                       "prowlarr, re-enable <name>, switch on the <name> indexer"),
        "arg": True,
        "arg_hint": "the configured indexer name to enable",
        "destructive": False,
    },
    {
        "id": "prowlarr_disable_indexer",
        "name": "Disable an indexer",
        "ai_phrases": ("disable indexer <name>, turn off <name>, disable <name> on "
                       "prowlarr, switch off the <name> indexer, disable the failing "
                       "indexer, disable the dead indexer <name>, stop using <name>"),
        # Disabling an indexer reduces search coverage, so it is gated like a
        # destructive op (typed-confirm in the SPA) — the inverse of enable.
        "arg": True,
        "arg_hint": "the configured indexer name to disable",
        "destructive": True,
    },
    {
        "id": "prowlarr_test_indexer",
        "name": "Test an indexer",
        "ai_phrases": ("test indexer <name>, test <name> on prowlarr, check if "
                       "<name> is working, is <name> reachable, test the <name> "
                       "indexer connection, verify <name>"),
        "arg": True,
        "arg_hint": "the configured indexer name to run a live connectivity test against",
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
    {
        "id": "prowlarr_available_indexers",
        "name": "Find indexers to add",
        "ai_phrases": ("what indexers can i add, available indexers, find "
                       "indexers to add, list addable indexers, search the "
                       "indexer catalog for <term>, english indexers, indexers "
                       "in english, <language> indexers, indexers in <language>, "
                       "english language indexers, en indexers, what "
                       "indexers are available to add, public indexers, private "
                       "indexers, semi-private indexers, public english indexers, "
                       "english public indexers, what public indexers can i add, "
                       "add all the english public indexers, list public "
                       "torrent indexers, which private indexers are available"),
        # arg = optional filter term. AI / Telegram only. Combine a privacy
        # facet (public / private / semi-private), a language (english / en /
        # en-US), and/or an indexer name — e.g. "english public", "public",
        # "torrentgalaxy". The skill parses the facets out of the term.
        "arg": True,
        "arg_hint": ("optional filter — a privacy (public / private / "
                     "semi-private), a language (english / en), a combination "
                     "(e.g. \"english public\"), or an indexer name"),
        "destructive": False,
    },
    {
        "id": "prowlarr_add_indexer",
        "name": "Add an indexer",
        "ai_phrases": ("add indexer <name>, add <name> to prowlarr, set up "
                       "indexer <name>, add <name> with flaresolverr, configure "
                       "indexer <name>, enable indexer <name>"),
        # arg = indexer name (+ optional "with flaresolverr"). A live write to
        # Prowlarr's config (creating an indexer) — arg-carrying so AI / Telegram
        # only. Left non-destructive (frictionless add; the inverse — removing an
        # indexer — would be the destructive op) so the AI can add on request.
        "arg": True,
        "destructive": False,
    },
    {
        "id": "prowlarr_add_indexers_bulk",
        "name": "Add indexers in bulk",
        "ai_phrases": ("add all the english public indexers, add all public "
                       "indexers, add every english indexer, bulk add indexers, "
                       "mass add indexers, add all the missing public indexers, "
                       "add all <language> <privacy> indexers, add all indexers "
                       "matching <term>, add all the public english trackers, "
                       "add the rest of the english indexers, add all <privacy> "
                       "indexers not already added, add every public tracker"),
        # arg = the SAME facet filter as prowlarr_available_indexers (privacy /
        # language / name, or "all"). Adds EVERY matching definition that isn't
        # already configured, auto-assigning FlareSolverr to any indexer Prowlarr
        # rejects with a Cloudflare / challenge error. A live MULTI write to
        # Prowlarr's config — arg-carrying so AI / Telegram only. Non-destructive
        # like the single add (adding is frictionless; removing is the
        # destructive inverse) so the AI can run it on request without per-
        # indexer friction.
        "arg": True,
        "arg_hint": ("a facet filter selecting WHICH catalog indexers to add — a "
                     "privacy (public / private / semi-private), a language "
                     "(english / en), a combination (e.g. \"public english\"), an "
                     "indexer-name fragment, or \"all\". Only definitions not "
                     "already configured are added; FlareSolverr is auto-assigned "
                     "to any indexer that needs it"),
        "destructive": False,
    },
    {
        "id": "prowlarr_fix_flaresolverr",
        "name": "Fix FlareSolverr tags",
        "ai_phrases": ("fix flaresolverr, add flaresolverr tags, tag cloudflare "
                       "indexers, which indexers need flaresolverr, assign "
                       "flaresolverr to the indexers that need it, link "
                       "flaresolverr to my indexers, apply flaresolverr proxy, "
                       "fix the cloudflare indexers, check indexers for "
                       "flaresolverr, flaresolverr tag"),
        # No arg — tests each not-yet-tagged indexer (individually, bounded +
        # budgeted under the proxy timeout) and tags the ones that need
        # FlareSolverr. A live write (PUT tags) to Prowlarr config, arg-less so
        # it surfaces as a one-click drawer button too. Non-destructive (adds a
        # proxy tag, removes nothing).
        "destructive": False,
    },
    # Manual-update skills — only for instances NOT linked to Docker (updates
    # for a native / non-Docker install are applied by hand).
    {
        "id": "prowlarr_check_update",
        "name": "Check for updates",
        "ai_phrases": ("is prowlarr up to date, check prowlarr version, latest "
                       "prowlarr version, is there a prowlarr update, prowlarr "
                       "update available, check for prowlarr updates, what version "
                       "of prowlarr is running"),
        "destructive": False,
        "non_docker_only": True,
    },
    {
        "id": "prowlarr_update",
        "name": "Update Prowlarr",
        "ai_phrases": ("update prowlarr, upgrade prowlarr, install the prowlarr "
                       "update, run the prowlarr updater, update prowlarr to the "
                       "latest version, apply the prowlarr update"),
        "destructive": True,
        "non_docker_only": True,
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


# Minimum lifetime queries before an indexer's failure-rate / slowness counts as
# "actionable" — a 1-of-1 failure (100%) shouldn't dominate the "most failing"
# pick over a busy indexer at 35% of thousands.
_STATS_MIN_QUERIES = 10


def _indexer_stats_detail(raw: Any) -> dict:
    """Per-indexer + aggregate breakdown from a ``/api/v1/indexerstats`` payload.

    Returns ``{queries, grabs, failed, fail_rate_pct, per_indexer, worst_failing,
    slowest}`` where ``per_indexer`` is one ``{name, queries, grabs, failed,
    fail_rate_pct, avg_response_ms}`` row per indexer (sorted worst-failure-rate
    first, then busiest), ``worst_failing`` is the highest-failure-rate indexer
    with a meaningful query volume (the single most actionable Prowlarr insight —
    'indexer X: 40% failure rate'), and ``slowest`` is the highest-average-
    response indexer. Empty / zeroed shape on any parse failure (stats are a
    nice-to-have, never load-bearing)."""
    out: dict = {"queries": 0, "grabs": 0, "failed": 0, "fail_rate_pct": 0.0,
                 "per_indexer": [], "worst_failing": None, "slowest": None}
    if not isinstance(raw, dict):
        return out
    rows = raw.get("indexers")
    if not isinstance(rows, list):
        return out
    per: list[dict] = []
    tq = tg = tf = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        q = safe_int(r.get("numberOfQueries"))
        g = safe_int(r.get("numberOfGrabs"))
        fq = safe_int(r.get("numberOfFailedQueries"))
        avg = safe_int(r.get("averageResponseTime"))  # ms
        name = str(r.get("indexerName") or r.get("name") or "?").strip()
        tq += q
        tg += g
        tf += fq
        per.append({
            "name": name, "queries": q, "grabs": g, "failed": fq,
            "fail_rate_pct": round(fq / q * 100, 1) if q > 0 else 0.0,
            "avg_response_ms": avg,
        })
    out["queries"] = tq
    out["grabs"] = tg
    out["failed"] = tf
    out["fail_rate_pct"] = round(tf / tq * 100, 1) if tq > 0 else 0.0
    per.sort(key=lambda p: (p["fail_rate_pct"], p["queries"]), reverse=True)
    out["per_indexer"] = per
    failing = [p for p in per if p["queries"] >= _STATS_MIN_QUERIES and p["failed"] > 0]
    out["worst_failing"] = failing[0] if failing else None
    busy = [p for p in per if p["queries"] >= _STATS_MIN_QUERIES and p["avg_response_ms"] > 0]
    out["slowest"] = max(busy, key=lambda p: p["avg_response_ms"]) if busy else None
    return out


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
            apps_names: list[str] = []
            try:
                ar = await cli.get(base + "/api/v1/applications",
                                   headers=_headers(api_key))
                if ar.status_code == 200:
                    _aj = ar.json()
                    if isinstance(_aj, list):
                        apps_synced = len(_aj)
                        # Capture the REAL connected-app names so the AI
                        # reports what's actually synced (Sonarr / Radarr /
                        # Whisparr / …) instead of guessing. Prefer the
                        # operator-set name; fall back to the *arr type
                        # (`implementation`, e.g. "Sonarr").
                        for _a in _aj:
                            if not isinstance(_a, dict):
                                continue
                            _nm = (_a.get("name") or _a.get("implementation") or "")
                            if isinstance(_nm, str) and _nm.strip():
                                apps_names.append(_nm.strip())
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                apps_synced = 0
                apps_names = []
            stats: dict = {}
            try:
                sr = await cli.get(base + "/api/v1/indexerstats",
                                   headers=_headers(api_key))
                if sr.status_code == 200:
                    stats = _indexer_stats_detail(sr.json())
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                stats = {}
            queries = safe_int(stats.get("queries"))
            grabs = safe_int(stats.get("grabs"))
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
        "apps_names": apps_names,
        "queries": safe_int(queries),
        "grabs": safe_int(grabs),
        "failed_queries": safe_int(stats.get("failed")),
        "fail_rate_pct": float(stats.get("fail_rate_pct") or 0.0),
        "worst_failing": stats.get("worst_failing"),
        "slowest_indexer": stats.get("slowest"),
        # Capped per-indexer breakdown (worst-failure-rate first) for the
        # rich indexer-stats skill + the AI context.
        "indexer_stats": (stats.get("per_indexer") or [])[:15],
        "health_issues": safe_int(health_issues),
        "version": ver,
        "fetched_at": int(now),
        # Counter-rate retention trend (per-day query/grab throughput + daily
        # failure-rate) — best-effort; the sampler may have no rows yet (fresh pin).
        "trend": _safe_trend(str(host_id or ""), int(service_idx or 0)),
    }
    _worst = out["worst_failing"]
    print(f"[prowlarr] INFO fetched host={host_id} indexers={enabled}/{total} "
          f"apps={out['apps_synced']}{('=' + ','.join(apps_names)) if apps_names else ''} "
          f"queries={out['queries']} grabs={out['grabs']} "
          f"fail_rate={out['fail_rate_pct']}% "
          f"worst={(str(_worst.get('name', '')) + ' ' + str(_worst.get('fail_rate_pct', 0)) + '%') if isinstance(_worst, dict) else 'none'} "
          f"health={out['health_issues']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def _safe_trend(host_id: str, service_idx: int) -> dict:
    """Best-effort ``prowlarr_sampler.trend_summary`` — a zeroed shape on any
    error (a fresh pin with no samples, or an import hiccup) so the card never
    fails on the trend embed."""
    try:
        from logic.apps import prowlarr_sampler as _s  # noqa: PLC0415
        return _s.trend_summary(host_id, service_idx)
    except (ImportError, RuntimeError, ValueError):
        return {"days": 0, "samples": 0, "window_queries": 0, "window_grabs": 0,
                "latest_fail_rate": 0.0, "avg_fail_rate": 0.0, "series_queries": [],
                "series_grabs": [], "series_fail_rate": []}


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
        "apps_names": as_list(data.get("apps_names")),
        "queries": safe_int(data.get("queries")),
        "grabs": safe_int(data.get("grabs")),
        "failed_queries": safe_int(data.get("failed_queries")),
        "fail_rate_pct": float(data.get("fail_rate_pct") or 0.0),
        "worst_failing": data.get("worst_failing"),
        "slowest_indexer": data.get("slowest_indexer"),
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
                    arg: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown
    skill id. ``arg`` carries the free-form search term (prowlarr_search) /
    indexer name (prowlarr_add_indexer). ``actor_username`` (passed by the
    route, absorbed by ``**_kw``) is unused — Prowlarr has no date-rendering
    skill, so it needs no per-user format."""
    if skill_id == "prowlarr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "prowlarr_indexers":
        return await _indexers_skill(host_row, chip, host_id=host_id)
    if skill_id == "prowlarr_indexer_stats":
        return await _indexer_stats_skill(host_row, chip, host_id=host_id,
                                          service_idx=service_idx)
    if skill_id == "prowlarr_enable_indexer":
        return await _toggle_indexer_skill(host_row, chip, arg=arg, enable=True,
                                           host_id=host_id)
    if skill_id == "prowlarr_disable_indexer":
        return await _toggle_indexer_skill(host_row, chip, arg=arg, enable=False,
                                           host_id=host_id)
    if skill_id == "prowlarr_test_indexer":
        return await _test_indexer_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "prowlarr_app_sync":
        return await _command_skill(host_row, chip, command="ApplicationIndexerSync",
                                    started_msg="🔄 Started syncing indexers to every "
                                                "connected app on Prowlarr.",
                                    host_id=host_id)
    if skill_id == "prowlarr_search":
        return await _search_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "prowlarr_available_indexers":
        return await _available_indexers_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "prowlarr_add_indexer":
        return await _add_indexer_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "prowlarr_add_indexers_bulk":
        return await _bulk_add_indexers_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "prowlarr_fix_flaresolverr":
        return await _fix_flaresolverr_skill(host_row, chip, host_id=host_id)
    if skill_id == "prowlarr_check_update":
        return await _servarr.check_update_skill(host_row, chip, app_label="Prowlarr",
                                                 api_version="v1", host_id=host_id,
                                                 actor_username=_kw.get("actor_username"))
    if skill_id == "prowlarr_update":
        return await _servarr.app_update_skill(host_row, chip, app_label="Prowlarr",
                                               api_version="v1", host_id=host_id)
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
    apps_names = as_list(data.get("apps_names"))
    queries = safe_int(data.get("queries"))
    grabs = safe_int(data.get("grabs"))
    fail_rate = float(data.get("fail_rate_pct") or 0.0)
    worst = data.get("worst_failing")
    slowest = data.get("slowest_indexer")
    health = safe_int(data.get("health_issues"))
    # Spell out the actual connected apps so the AI never invents names.
    apps_line = f"🔗 Apps synced: {apps:,}"
    if apps_names:
        apps_line += f" ({', '.join(apps_names)})"
    lines = [
        f"🔍 Indexers: {enabled:,} / {total:,} enabled",
        apps_line,
        f"📊 Queries: {queries:,}  ·  📥 Grabs: {grabs:,}",
        f"{'⚠️' if fail_rate >= 10 else '✅'} Query failure rate: {fail_rate}%",
    ]
    # The single most actionable Prowlarr insight: which indexer is failing /
    # slow. Only shown when there's a meaningful offender.
    if isinstance(worst, dict) and worst.get("name"):
        lines.append(f"🛑 Most failing: {worst.get('name')} "
                     f"({worst.get('fail_rate_pct')}% of {safe_int(worst.get('queries')):,} queries)")
    if isinstance(slowest, dict) and slowest.get("name"):
        lines.append(f"🐢 Slowest: {slowest.get('name')} "
                     f"({safe_int(slowest.get('avg_response_ms')):,}ms avg)")
    lines.append(f"{'⚠️' if health else '✅'} Health issues: {health:,}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "indexers_total": total, "indexers_enabled": enabled,
        "apps_synced": apps, "apps_names": apps_names,
        "queries": queries, "grabs": grabs,
        "fail_rate_pct": fail_rate, "worst_failing": worst,
        "slowest_indexer": slowest,
        "health_issues": health,
    }


# noinspection DuplicatedCode
# The auth-fail / non-200 / non-JSON guard block below is the deliberate
# per-app encapsulation twin shared with the other read skills (CLAUDE.md) —
# content differs only by app/endpoint, so it stays inline, not factored out.
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
    rows.sort(key=lambda row: (not row.get("enable"), str(row.get("name") or "").lower()))
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


# noinspection DuplicatedCode
async def _indexer_stats_skill(host_row: dict, chip: dict, *,
                               host_id: Optional[str] = None,
                               service_idx: Optional[int] = None) -> dict:
    """Read-only: per-indexer query / grab / failure-rate / avg-response
    breakdown (worst-failure-rate first). Live-fetches via fetch_data (force).
    Never raises — the single most actionable Prowlarr view ('which indexer is
    failing / slow')."""
    print(f"[prowlarr] INFO prowlarr_indexer_stats host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[prowlarr] warning: prowlarr_indexer_stats host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    per = as_list(data.get("indexer_stats"))
    if not per:
        return {"ok": True, "status": 200,
                "detail": "📊 No indexer stats yet — Prowlarr records these once your "
                          "apps start querying indexers."}
    overall = float(data.get("fail_rate_pct") or 0.0)
    lines = [f"📊 Indexer stats (overall query failure rate {overall}%):"]
    for p in per[:20]:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "?").strip()
        q = safe_int(p.get("queries"))
        g = safe_int(p.get("grabs"))
        fr = float(p.get("fail_rate_pct") or 0.0)
        avg = safe_int(p.get("avg_response_ms"))
        flag = "🛑" if fr >= 25 else ("⚠️" if fr >= 10 else "✅")
        meta = ", ".join(part for part in (
            f"{q:,} queries", f"{g:,} grabs",
            (f"{fr}% fail" if fr > 0 else ""),
            (f"{avg:,}ms" if avg > 0 else ""),
        ) if part)
        lines.append(f"{flag} {name}: {meta}")
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "fail_rate_pct": overall, "indexer_stats": per}


async def _get_configured_indexers(cli: httpx.AsyncClient, base: str,
                                   key: str) -> "tuple[Optional[list], Optional[dict]]":
    """GET ``/api/v1/indexer`` → ``(indexers_list, None)`` on success, or
    ``(None, error_skill_dict)`` when auth fails / non-200 / non-JSON. Shared by
    the enable / disable / test skills (their guard blocks were identical)."""
    r = await cli.get(base + "/api/v1/indexer", headers=_headers(key))
    if r.status_code in (401, 403):
        return None, {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
    if r.status_code != 200:
        return None, {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        items = r.json()
    except (ValueError, TypeError):
        return None, {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    return (items if isinstance(items, list) else []), None


def _match_indexer(items: list, name: str) -> "tuple[Optional[dict], str]":
    """Find ONE configured indexer by ``name`` — exact (case-insensitive) then a
    UNIQUE substring. Returns ``(indexer, "")`` on a single match, or
    ``(None, reason)`` when nothing / several match (the reason is a
    ready-to-show message)."""
    nl = (name or "").strip().lower()
    if not nl:
        return None, "no indexer name given"
    rows = [i for i in items if isinstance(i, dict)]
    exact = [i for i in rows if str(i.get("name") or "").strip().lower() == nl]
    if exact:
        return exact[0], ""
    subs = [i for i in rows if nl in str(i.get("name") or "").strip().lower()]
    if len(subs) == 1:
        return subs[0], ""
    if len(subs) > 1:
        names = ", ".join(sorted(str(i.get("name") or "") for i in subs[:8]))
        return None, f"“{name}” matches several indexers ({names}…) — be more specific."
    return None, f"no configured indexer named “{name}”. (Say “list indexers” to see them.)"


async def _resolve_configured_indexer(cli: httpx.AsyncClient, base: str, key: str,
                                      name: str) -> "tuple[Optional[dict], Optional[dict]]":
    """GET the configured indexers + match ONE by ``name`` in one step. Returns
    ``(indexer, None)`` on a unique match, or ``(None, err_skill_dict)`` when the
    list fetch fails / nothing / several match. Shared opening for the enable /
    disable / test skills (their GET-then-match block was identical)."""
    items, gerr = await _get_configured_indexers(cli, base, key)
    if gerr is not None:
        return None, gerr
    idx, reason = _match_indexer(items or [], name)
    if idx is None:
        return None, {"ok": False, "status": 404, "detail": reason}
    return idx, None


async def _toggle_indexer_skill(host_row: dict, chip: dict, *,
                                arg: Optional[str] = None, enable: bool,
                                host_id: Optional[str] = None) -> dict:
    """Live WRITE: enable / disable ONE configured indexer by name
    (``PUT /api/v1/indexer/{id}`` with ``enable`` flipped). Already-in-the-target-
    state is a friendly no-op. Never raises."""
    verb = "enable" if enable else "disable"
    name = (arg or "").strip()
    if not name:
        return {"ok": False, "status": 0,
                "detail": f"no indexer name given — which indexer should I {verb}?"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            idx, ierr = await _resolve_configured_indexer(cli, base, api_key, name)
            if ierr is not None:
                return ierr
            idx = idx or {}
            rid = safe_int(idx.get("id"))
            label = str(idx.get("name") or name)
            if bool(idx.get("enable")) == enable:
                return {"ok": True, "status": 200,
                        "detail": f"{'✅' if enable else '⛔'} “{label}” is already {verb}d."}
            body = dict(idx)
            body["enable"] = enable
            print(f"[prowlarr] INFO prowlarr_{verb}_indexer host={host_id} id={rid} name={label!r}")
            pr = await cli.put(base + f"/api/v1/indexer/{rid}",
                               headers=_headers(api_key), json=body)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"{verb} failed: {type(e).__name__}: {e}"}
    if pr.status_code in (200, 202):
        return {"ok": True, "status": 200,
                "detail": f"{'✅ Enabled' if enable else '⛔ Disabled'} “{label}” on Prowlarr."}
    if pr.status_code in (401, 403):
        return {"ok": False, "status": pr.status_code, "detail": "auth failed (check api_key)"}
    return {"ok": False, "status": pr.status_code,
            "detail": f"Prowlarr returned HTTP {pr.status_code} trying to {verb} “{label}”."}


# noinspection DuplicatedCode
async def _test_indexer_skill(host_row: dict, chip: dict, *,
                              arg: Optional[str] = None,
                              host_id: Optional[str] = None) -> dict:
    """Live connectivity test for ONE configured indexer by name
    (``POST /api/v1/indexer/test`` with the indexer body). Reports reachable /
    the upstream validation reason, and flags a Cloudflare/challenge block.
    Never raises."""
    name = (arg or "").strip()
    if not name:
        return {"ok": False, "status": 0,
                "detail": "no indexer name given — which indexer should I test?"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0,
                                     follow_redirects=True) as cli:
            idx, ierr = await _resolve_configured_indexer(cli, base, api_key, name)
            if ierr is not None:
                return ierr
            idx = idx or {}
            label = str(idx.get("name") or name)
            print(f"[prowlarr] INFO prowlarr_test_indexer host={host_id} name={label!r} (live test)")
            tr = await cli.post(base + "/api/v1/indexer/test",
                                headers=_headers(api_key), json=idx)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"test failed: {type(e).__name__}: {e}"}
    if tr.status_code in (200, 201):
        return {"ok": True, "status": 200,
                "detail": f"✅ “{label}” tested OK — reachable and authenticated."}
    if tr.status_code in (401, 403):
        return {"ok": False, "status": tr.status_code, "detail": "auth failed (check api_key)"}
    body_text = _resp_text(tr)
    reason = _short_reason(body_text)
    flare = (" — looks like a Cloudflare/challenge block; try “fix flaresolverr”."
             if _FLARE_ERROR_RE.search(body_text) else "")
    return {"ok": False, "status": tr.status_code,
            "detail": f"⚠️ “{label}” test failed" + (f": {reason}" if reason else "") + flare}


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


def _release_seeders(it: Any) -> int:
    """Seeder count for a search-result release (0 when absent / wrong shape).
    Module-level (not nested) so the search-skill body stays flat."""
    return safe_int(it.get("seeders")) if isinstance(it, dict) else 0


# noinspection DuplicatedCode
# Auth-fail / non-200 / non-JSON guard is the shared per-app read-skill twin
# (see _indexers_skill) — kept inline per the encapsulation convention.
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
    rows = [it for it in items if isinstance(it, dict)]
    rows.sort(key=lambda rel: (_release_seeders(rel), safe_int(rel.get("size"))), reverse=True)
    lines = []
    for it in rows[:10]:
        title = str(it.get("title") or "?").strip()
        indexer = str(it.get("indexer") or "").strip()
        size = _fmt_bytes(it.get("size"))
        seeders = _release_seeders(it)
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


# ---------------------------------------------------------------------------
# Add-indexer skills (live WRITE to Prowlarr config)
# ---------------------------------------------------------------------------
# FlareSolverr is configured in Prowlarr as a tag-based indexer PROXY
# (Settings → Indexers → Indexer Proxies). An indexer "uses" FlareSolverr when
# it shares a TAG with the proxy. So "assign FlareSolverr to this indexer" =
# add the FlareSolverr proxy's tag to the indexer's `tags` array.
_FLARE_HINT_RE = re.compile(r"flare\s*solverr|flaresolver", re.I)


def _strip_flare_hint(arg: str) -> "tuple[str, bool]":
    """Split a free-form add-indexer arg into (indexer_name, want_flaresolverr).
    Recognises trailing/inline "with flaresolverr" / "+ flaresolverr" phrasing
    and strips it + common connector words so the remainder is the bare name."""
    want = bool(_FLARE_HINT_RE.search(arg or ""))
    name = _FLARE_HINT_RE.sub("", arg or "")
    # Drop the connector left behind ("X with", "X using", "X and") + tidy.
    name = re.sub(r"\b(?:with|using|and|plus|\+|via)\b\s*$", "", name.strip(), flags=re.I)
    return re.sub(r"\s+", " ", name).strip(" ,+-"), want


async def _flaresolverr_tag_ids(cli: httpx.AsyncClient, base: str, key: str) -> "tuple[list, str]":
    """Resolve the tag id(s) that map an indexer onto the FlareSolverr proxy.

    Returns ``(tag_ids, note)``. ``tag_ids`` is empty when no FlareSolverr proxy
    exists OR it carries no tag (Prowlarr applies a proxy to an indexer via a
    SHARED tag, so a tagless proxy can't be auto-assigned); ``note`` explains
    why so the caller can surface it. Best-effort — never raises."""
    try:
        r = await cli.get(base + "/api/v1/indexerProxy", headers=_headers(key))
        if r.status_code != 200:
            return [], f"could not read indexer proxies (HTTP {r.status_code})"
        proxies = r.json()
    except (httpx.HTTPError, OSError, ValueError, TypeError) as e:  # noqa: BLE001
        return [], f"indexer-proxy lookup failed: {type(e).__name__}"
    if not isinstance(proxies, list):
        return [], "indexer-proxy list had an unexpected shape"
    for px in proxies:
        if not isinstance(px, dict):
            continue
        impl = str(px.get("implementation") or px.get("implementationName") or "").lower()
        if "flaresolverr" in impl:
            tags = as_list(px.get("tags"))
            if tags:
                return tags, ""
            return [], ("a FlareSolverr proxy exists but has no tag — add a tag to "
                        "it in Prowlarr (Settings → Indexer Proxies) so indexers "
                        "can be mapped onto it")
    return [], ("no FlareSolverr proxy is configured in Prowlarr — add one under "
                "Settings → Indexer Proxies first, then I can assign it")


def _indexer_lang(defn: dict) -> str:
    """Best-effort language label for an indexer schema definition (the field
    name varies across Prowlarr versions)."""
    for k in ("language", "indexerLanguage", "lang"):
        v = defn.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


# Common language WORDS → ISO-639-1 primary code, so an operator who types
# "english" matches indexers tagged en / en-US / en-GB / enAU / etc. (the
# locale field varies wildly across Prowlarr's ~500 indexer definitions).
_LANG_WORD_TO_CODE = {
    "english": "en", "french": "fr", "german": "de", "spanish": "es",
    "italian": "it", "portuguese": "pt", "russian": "ru", "dutch": "nl",
    "polish": "pl", "japanese": "ja", "chinese": "zh", "korean": "ko",
    "arabic": "ar", "turkish": "tr", "swedish": "sv", "norwegian": "no",
    "danish": "da", "finnish": "fi", "czech": "cs", "greek": "el",
    "hungarian": "hu", "romanian": "ro", "ukrainian": "uk", "hebrew": "he",
    "hindi": "hi", "thai": "th", "vietnamese": "vi", "bulgarian": "bg",
    "croatian": "hr", "serbian": "sr", "slovak": "sk", "slovenian": "sl",
}


def _lang_primary(s: str) -> str:
    """Primary 2-letter language subtag from a locale CODE or a language WORD.
    `en-US` / `en_us` / `enAU` / `engb` / `English` / `eng` / `en` all → `en`.
    Returns "" when the string isn't language-shaped (so a free-text indexer
    name like `torrentgalaxy` doesn't get mistaken for a locale)."""
    t = (s or "").strip().lower()
    if not t:
        return ""
    if t in _LANG_WORD_TO_CODE:
        return _LANG_WORD_TO_CODE[t]
    # head subtag = chars before any separator (`en-US` → `en`)
    head = re.split(r"[-_ /]", t, maxsplit=1)[0]
    # collapse a no-separator locale (`enau` / `engb`) to its first 2 letters
    if re.fullmatch(r"[a-z]{4,6}", head):
        head = head[:2]
    if re.fullmatch(r"[a-z]{2,3}", head):
        return head[:2]
    return ""


# Privacy facets Prowlarr's indexer schema reports in its ``privacy`` field
# ("public" / "private" / "semiPrivate"). A free-text term like "public" /
# "private" / "semi-private" should filter by privacy; the longer / compound
# words are checked FIRST so "private" doesn't pre-match inside "semi-private".
_PRIVACY_WORDS = (
    ("semi-private", "semiprivate"), ("semi private", "semiprivate"),
    ("semiprivate", "semiprivate"), ("private", "private"), ("public", "public"),
)
_PRIVACY_LABELS = {"public": "public", "private": "private",
                   "semiprivate": "semi-private"}


def _extract_privacy(term: str) -> "tuple[str, str]":
    """Split a free-text term into ``(privacy, remainder)``. ``privacy`` is the
    normalised facet ("public" / "private" / "semiprivate") when a privacy WORD
    is present, else "". The matched word is removed from ``remainder`` so the
    rest can still be matched as a language / name — so ``"english public"`` ->
    ``("public", "english")``, ``"public"`` -> ``("public", "")``,
    ``"torrentgalaxy"`` -> ``("", "torrentgalaxy")``. Word-boundary matched so a
    name like "PublicHD" isn't mistaken for a privacy filter."""
    t = (term or "").strip().lower()
    if not t:
        return "", ""
    for word, norm in _PRIVACY_WORDS:
        # `word` is a trusted constant ([a-z], space, hyphen) — no regex
        # metacharacters that need escaping (a hyphen is literal outside a char
        # class), so a plain \b...\b pattern is safe AND avoids re.escape's
        # AnyStr (str | bytes) return widening the concatenation type.
        pat = rf"\b{word}\b"
        if re.search(pat, t):
            return norm, re.sub(r"\s+", " ", re.sub(pat, " ", t)).strip()
    return "", t


def _indexer_privacy(defn: dict) -> str:
    """Normalised privacy facet for a schema definition: "public" / "private" /
    "semiprivate" (lower-cased, hyphen-stripped so Prowlarr's 'semiPrivate'
    compares). "" when the field is absent."""
    return str(defn.get("privacy") or "").strip().lower().replace("-", "")


# noinspection DuplicatedCode
# Auth-fail / non-200 / non-JSON guard is the shared per-app read-skill twin
# (see _indexers_skill) — kept inline per the encapsulation convention.
async def _available_indexers_skill(host_row: dict, chip: dict, *,
                                    arg: Optional[str] = None,
                                    host_id: Optional[str] = None) -> dict:
    """Read-only: list indexer definitions that CAN be added, from
    ``GET /api/v1/indexer/schema``, optionally filtered by an arg term matched
    against the name + language. Never raises. The catalog is large (~500), so
    results are capped; the operator/AI narrows with a term."""
    term = (arg or "").strip().lower()
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[prowlarr] INFO prowlarr_available_indexers host={host_id} term={term!r} (schema fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/indexer/schema", headers=_headers(api_key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"schema fetch failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        defs = r.json()
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not isinstance(defs, list):
        defs = []
    # Facet-aware matching. A term can combine a PRIVACY facet (public /
    # private / semi-private), a LANGUAGE facet ("english" / "en" / "en-US"
    # matches the whole en family — en-US / en-GB / enAU / ...), and / or a free
    # text NAME fragment. "english public" -> privacy=public + lang=en; "public"
    # -> privacy only; "torrentgalaxy" -> name only (a bare name search still
    # works unchanged). `_extract_privacy` pulls the privacy word OUT so the
    # remainder is matched as language / name.
    privacy, lang_term = _extract_privacy(term)
    want_lang = _lang_primary(lang_term) if lang_term else ""
    rows = []
    for d in defs:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "").strip()
        if not name:
            continue
        lang = _indexer_lang(d)
        priv = _indexer_privacy(d)
        if privacy and priv != privacy:
            continue
        if lang_term:
            name_hit = lang_term in name.lower()
            lang_sub = lang_term in lang.lower()
            lang_fam = bool(want_lang) and _lang_primary(lang) == want_lang
            if not (name_hit or lang_sub or lang_fam):
                continue
        rows.append((name, lang, str(d.get("protocol") or "").strip(), priv))
    rows.sort(key=lambda t: t[0].lower())
    total = len(rows)
    lines = []
    for name, lang, proto, priv in rows[:25]:
        meta = ", ".join(p for p in (proto, lang, _PRIVACY_LABELS.get(priv, priv)) if p)
        lines.append(f"• {name}" + (f" ({meta})" if meta else ""))
    # Describe the active facets in the header. A language facet reads clearest
    # as "in language en (incl. en-US / en-GB / enAU / ...)"; a free-text
    # remainder reads as "matching <term>".
    priv_label = _PRIVACY_LABELS.get(privacy, "")
    priv_prefix = f"{priv_label} " if priv_label else ""
    lang_desc = ""
    if lang_term:
        if want_lang:
            lang_desc = f" in language “{want_lang}” (incl. {want_lang}-US / {want_lang}-GB / {want_lang}AU etc.)"
        else:
            lang_desc = f" matching “{lang_term}”"
    if not lines:
        empty = (f"🔍 No addable {priv_prefix}indexers{lang_desc}." if term
                 else "🔍 No indexer definitions returned.")
        return {"ok": True, "status": 200, "detail": empty}
    head = (f"🧩 {total} addable {priv_prefix}indexer{'s' if total != 1 else ''}"
            + lang_desc
            + (f" (showing first {len(lines)})" if total > len(lines) else "") + ":")
    head += "\n" + "\n".join(lines)
    head += "\n\nTo add one, say “add <name> on prowlarr” (append “with flaresolverr” to route it through the FlareSolverr proxy)."
    return {"ok": True, "status": 200, "detail": head}


async def _fetch_indexer_schema(cli: httpx.AsyncClient, base: str,
                                key: str) -> "tuple[Optional[list], Optional[dict]]":
    """GET ``/api/v1/indexer/schema`` → ``(definitions, None)`` on success, or
    ``(None, error_response)`` when auth fails / non-200 / non-JSON. The error
    response is the ready-to-return ``{ok, status, detail}`` skill dict. Shared
    by the single-add + bulk-add skills (their guard blocks were identical)."""
    sr = await cli.get(base + "/api/v1/indexer/schema", headers=_headers(key))
    if sr.status_code in (401, 403):
        return None, {"ok": False, "status": sr.status_code, "detail": "auth failed (check api_key)"}
    if sr.status_code != 200:
        return None, {"ok": False, "status": sr.status_code,
                      "detail": f"could not read the indexer catalog (HTTP {sr.status_code})"}
    try:
        defs = sr.json()
    except (ValueError, TypeError):
        return None, {"ok": False, "status": 502, "detail": "non-JSON indexer catalog from upstream"}
    return (defs if isinstance(defs, list) else []), None


async def _add_indexer_skill(host_row: dict, chip: dict, *,
                             arg: Optional[str] = None,
                             host_id: Optional[str] = None) -> dict:
    """Live WRITE: add an indexer to Prowlarr by name.

    Resolves the named definition from ``GET /api/v1/indexer/schema``, fills the
    essentials (``enable=true``, the default app profile, a default priority) +
    optionally the FlareSolverr proxy tag when the arg mentions FlareSolverr,
    then ``POST /api/v1/indexer``. Never raises. Per-indexer required fields are
    left at the schema defaults Prowlarr supplies — an indexer that needs extra
    config (login, a base-URL pick) will report the upstream validation error so
    the operator finishes it in the Prowlarr UI; that's surfaced verbatim."""
    raw = (arg or "").strip()
    name, want_flare = _strip_flare_hint(raw)
    if not name:
        return {"ok": False, "status": 0,
                "detail": "no indexer name given — say e.g. “add 1337x on prowlarr”"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[prowlarr] INFO prowlarr_add_indexer host={host_id} name={name!r} "
          f"flaresolverr={want_flare} (live write)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0,
                                     follow_redirects=True) as cli:
            # 1) Resolve the indexer definition (the schema) by name.
            defs, schema_err = await _fetch_indexer_schema(cli, base, api_key)
            if schema_err is not None:
                return schema_err
            defs = defs or []
            nl = name.lower()
            match = next((d for d in defs if isinstance(d, dict)
                          and str(d.get("name") or "").strip().lower() == nl), None)
            if match is None:  # fall back to a unique substring match
                subs = [d for d in defs if isinstance(d, dict)
                        and nl in str(d.get("name") or "").strip().lower()]
                if len(subs) == 1:
                    match = subs[0]
                elif len(subs) > 1:
                    names = ", ".join(sorted(
                        d["name"] for d in subs[:8] if isinstance(d.get("name"), str)))
                    return {"ok": False, "status": 0,
                            "detail": f"“{name}” matches several indexers ({names}…) — "
                                      f"be more specific."}
            if match is None:
                return {"ok": False, "status": 0,
                        "detail": f"no indexer named “{name}” in the catalog — try "
                                  f"“what indexers can I add matching {name}”."}
            # 2) Default app profile (required on create) — first one.
            app_profile_id = await _default_app_profile_id(cli, base, api_key)
            # 3) Optional FlareSolverr proxy tag.
            flare_note = ""
            tag_ids: list = []
            if want_flare:
                tag_ids, flare_note = await _flaresolverr_tag_ids(cli, base, api_key)
            # 4) Build the create body from the schema + essentials.
            body = _build_indexer_body(match, app_profile_id, tag_ids)
            # 5) Create.
            cr = await cli.post(base + "/api/v1/indexer", headers=_headers(api_key), json=body)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[prowlarr] warning: add_indexer host={host_id} name={name!r} failed — {type(e).__name__}: {e}")
        return {"ok": False, "status": 0, "detail": f"add failed: {type(e).__name__}: {e}"}
    if cr.status_code in (200, 201):
        detail = f"➕ Added “{name}” to Prowlarr."
        if want_flare:
            detail += (" 🛡️ FlareSolverr proxy assigned." if tag_ids
                       else f" (FlareSolverr NOT assigned — {flare_note}.)")
        detail += " Run “sync indexers to my apps” to push it to Radarr/Sonarr/etc."
        print(f"[prowlarr] INFO add_indexer host={host_id} name={name!r} -> ok "
              f"(flaresolverr_tags={tag_ids})")
        return {"ok": True, "status": cr.status_code, "detail": detail}
    if cr.status_code in (401, 403):
        return {"ok": False, "status": cr.status_code, "detail": "auth failed (check api_key)"}
    # Surface the upstream validation error verbatim — an indexer needing extra
    # config (login / base-URL pick) fails here with a useful message.
    try:
        _body = (cr.text or "")[:300]
    except (ValueError, TypeError):
        _body = ""
    print(f"[prowlarr] warning: add_indexer host={host_id} name={name!r} -> HTTP "
          f"{cr.status_code} {_body}")
    return {"ok": False, "status": cr.status_code,
            "detail": f"Prowlarr rejected the add (HTTP {cr.status_code})"
                      + (f": {_body}" if _body else "")
                      + " — it may need extra config; finish it in the Prowlarr UI."}


# ---------------------------------------------------------------------------
# Bulk add (one command adds EVERY matching not-yet-configured indexer)
# ---------------------------------------------------------------------------
# Add-failure messages that indicate a Cloudflare / anti-bot CHALLENGE — the
# class of block FlareSolverr exists to solve. When a bulk add fails with one of
# these AND a FlareSolverr proxy is configured, the add is auto-retried WITH the
# proxy tag. Kept focused on the challenge family — deliberately NOT a bare
# "403" (which also covers private-tracker-without-credentials failures that
# FlareSolverr can't fix).
_FLARE_ERROR_RE = re.compile(
    r"cloudflare|flaresolverr|ddos.?guard|just a moment|attention required|"
    r"cf[-_]?(?:clearance|ray|chl)|jschl|challenge|captcha|"
    r"browser.{0,20}check", re.I)

# Bulk-add safety bounds. Each add runs a live connectivity TEST against the
# tracker, so concurrency stays low + a total cap + an overall wall-clock
# budget keep one command from hammering dozens of trackers for minutes.
_BULK_ADD_CONCURRENCY = 4
_BULK_ADD_MAX = 60  # candidates attempted per run (rest deferred)
_BULK_ADD_BUDGET_S = 150  # overall wall-clock budget for the whole batch

# FlareSolverr-tag check bounds. Each candidate is TESTED individually via
# POST /api/v1/indexer/test (one tracker round-trip server-side) — NOT the
# fleet-wide POST /api/v1/indexer/testall, which on a large indexer set blows
# past the reverse-proxy read timeout (~60s) and returns a raw 504 to the SPA.
# Bounded concurrency + an overall wall-clock budget UNDER the proxy window
# keep the skill returning a clean (possibly partial) result instead of 504ing;
# untested candidates are deferred to the next run.
_FLARE_TEST_CONCURRENCY = 5
_FLARE_TEST_BUDGET_S = 45  # < typical proxy_read_timeout (60s) so we never 504


async def _default_app_profile_id(cli: httpx.AsyncClient, base: str, key: str) -> int:
    """Resolve the default Prowlarr app-profile id (required on indexer create).
    Returns the first profile's id, falling back to 1 on any failure."""
    try:
        ar = await cli.get(base + "/api/v1/appprofile", headers=_headers(key))
        if ar.status_code == 200:
            aps = ar.json()
            if isinstance(aps, list) and aps and isinstance(aps[0], dict) and aps[0].get("id"):
                return safe_int(aps[0].get("id")) or 1
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        pass
    return 1


def _build_indexer_body(defn: dict, app_profile_id: int, extra_tag_ids: list) -> dict:
    """Construct the ``POST /api/v1/indexer`` create body from a schema
    definition + the resolved app-profile id + any extra tag ids (e.g. the
    FlareSolverr proxy tag). Fills the create essentials (enable, appProfileId,
    a default priority) and unions the extra tags onto the definition's own."""
    body = dict(defn)
    body["enable"] = True
    body["appProfileId"] = app_profile_id
    if not safe_int(body.get("priority")):
        body["priority"] = 25
    existing = body.get("tags")
    if not isinstance(existing, list):
        existing = []
    # Coerce to ints before set/sort — Prowlarr tag ids are integers, but a
    # malformed upstream `tags` (mixed str/int, or an unhashable element) would
    # otherwise raise TypeError out of the `httpx`-only try in the caller and
    # surface as an unhandled 500.
    tag_ids = {safe_int(t) for t in existing if isinstance(t, (int, str))}
    tag_ids |= {safe_int(t) for t in (extra_tag_ids or []) if isinstance(t, (int, str))}
    body["tags"] = sorted(tag_ids)
    return body


def _resp_text(r) -> str:
    """First 400 chars of a response body (or '' on any failure)."""
    try:
        return (r.text or "")[:400]
    except (ValueError, TypeError):
        return ""


def _short_reason(text: str) -> str:
    """Condense a Prowlarr validation-error body into a short human reason.
    Prowlarr returns a JSON array of ``{errorMessage}`` objects — pull the first
    ``errorMessage``; else a trimmed snippet."""
    t = (text or "").strip()
    if not t:
        return ""
    m = re.search(r'"errorMessage"\s*:\s*"(?P<msg>[^"]{1,160})"', t)
    if m:
        return m.group("msg")
    return t[:120]


def _failed_reason(r: dict) -> str:
    """Short human reason for a bulk-add result row — the upstream reason when
    present, else ``HTTP <status>``. ``safe_int`` coerces the status so the
    f-string formats a plain int (avoids ``str(object)`` on the loosely-typed
    result dict's value)."""
    return str(r.get("reason") or "") or f"HTTP {safe_int(r.get('status'))}"


def _def_keys(defn: dict) -> "set[str]":
    """Lowercased identifier keys for a schema definition (name + the cardigann
    definitionName when present), used to test against the already-configured
    set."""
    keys = set()
    for k in ("name", "definitionName"):
        v = defn.get(k)
        if isinstance(v, str) and v.strip():
            keys.add(v.strip().lower())
    return keys


async def _added_indexer_keys(cli: httpx.AsyncClient, base: str, key: str) -> "set[str]":
    """Lowercased identifiers of indexers ALREADY configured in Prowlarr (so the
    bulk add can skip them). Collects both the cardigann ``definitionName`` AND
    the (possibly renamed) display ``name`` from ``GET /api/v1/indexer``. Empty
    set on failure — then nothing is pre-skipped, but a per-indexer add still
    4xx's on a duplicate, so we never double-add."""
    out: "set[str]" = set()
    try:
        r = await cli.get(base + "/api/v1/indexer", headers=_headers(key))
        if r.status_code != 200:
            return out
        items = r.json()
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return out
    if not isinstance(items, list):
        return out
    for it in items:
        if isinstance(it, dict):
            out |= _def_keys(it)
    return out


def _created_id(resp) -> int:
    """Created indexer's ``id`` from a successful POST /api/v1/indexer response
    body (0 on any parse failure)."""
    try:
        return safe_int((resp.json() or {}).get("id"))
    except (ValueError, TypeError, AttributeError):
        return 0


async def _add_one_indexer(cli: httpx.AsyncClient, base: str, key: str, defn: dict,
                           app_profile_id: int, flare_tag_ids: list) -> dict:
    """Add ONE indexer definition. Tries WITHOUT FlareSolverr first; if Prowlarr
    rejects it with a Cloudflare / challenge error AND a FlareSolverr proxy tag
    is available, retries the add WITH the proxy tag. Returns
    ``{name, ok, flare, status, reason, id}`` — ``id`` is the created indexer's
    id (0 when the add failed). Never raises."""
    name = str(defn.get("name") or "?").strip()
    try:
        cr = await cli.post(base + "/api/v1/indexer", headers=_headers(key),
                            json=_build_indexer_body(defn, app_profile_id, []))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"name": name, "ok": False, "flare": False, "status": 0,
                "reason": type(e).__name__, "id": 0}
    if cr.status_code in (200, 201):
        return {"name": name, "ok": True, "flare": False, "status": cr.status_code,
                "reason": "", "id": _created_id(cr)}
    text = _resp_text(cr)
    # Cloudflare / challenge → retry WITH the FlareSolverr proxy tag.
    if flare_tag_ids and _FLARE_ERROR_RE.search(text):
        try:
            cr2 = await cli.post(base + "/api/v1/indexer", headers=_headers(key),
                                 json=_build_indexer_body(defn, app_profile_id, flare_tag_ids))
        except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
            return {"name": name, "ok": False, "flare": True, "status": 0,
                    "reason": f"flare retry {type(e).__name__}", "id": 0}
        if cr2.status_code in (200, 201):
            return {"name": name, "ok": True, "flare": True, "status": cr2.status_code,
                    "reason": "", "id": _created_id(cr2)}
        return {"name": name, "ok": False, "flare": True, "status": cr2.status_code,
                "reason": _short_reason(_resp_text(cr2)), "id": 0}
    return {"name": name, "ok": False, "flare": False, "status": cr.status_code,
            "reason": _short_reason(text), "id": 0}


async def _apply_flaresolverr_tags(cli: httpx.AsyncClient, base: str, key: str,
                                   flare_tag_ids: list, *,
                                   only_ids: "Optional[set]" = None) -> dict:
    """Find configured indexers that NEED FlareSolverr (Prowlarr's own
    ``POST /api/v1/indexer/testall`` reports the "may use Cloudflare DDoS
    Protection, therefore Prowlarr requires FlareSolverr" warning for them) and,
    for any that don't already carry the proxy tag, PUT the tag onto them so they
    link to the FlareSolverr indexer-proxy. ``only_ids`` (when given) scopes the
    tagging to that id set (e.g. the just-bulk-added indexers); ``None`` = every
    indexer. Returns ``{checked, needed, tagged, already_tagged, errs,
    timed_out, remaining}``. Never raises — best-effort, each step degrades to
    empty on failure.

    Tests each candidate INDIVIDUALLY via ``POST /api/v1/indexer/test`` (one
    tracker round-trip server-side) under a concurrency cap + an overall
    wall-clock budget UNDER the reverse-proxy read timeout — NOT the fleet-wide
    ``testall``, which on a large indexer set exceeds the proxy window and 504s
    the SPA. Already-tagged indexers are skipped (cheap, no test). Candidates not
    reached before the budget expires are reported via ``remaining`` + a
    ``timed_out`` flag so the operator can re-run for the rest."""
    flare_set = set(flare_tag_ids)
    # 1) GET all indexers (full bodies — needed for the test + PUT + current tags).
    idx_by_id: dict = {}
    try:
        ir = await cli.get(base + "/api/v1/indexer", headers=_headers(key))
        items = ir.json() if ir.status_code == 200 else []
        for it in (items if isinstance(items, list) else []):
            if isinstance(it, dict) and safe_int(it.get("id")):
                idx_by_id[safe_int(it.get("id"))] = it
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        idx_by_id = {}
    # 2) Candidates = scoped, NOT already flare-tagged. Already-tagged ones are
    #    counted + skipped (no test needed).
    already = 0
    candidates: list[tuple] = []  # (rid, body)
    for rid, body in idx_by_id.items():
        if only_ids is not None and rid not in only_ids:
            continue
        _tags = body.get("tags")
        cur_tags = _tags if isinstance(_tags, list) else []
        if flare_set & set(cur_tags):
            already += 1
            continue
        candidates.append((rid, body))
    # 3) Test each candidate (bounded concurrency + overall budget) — those whose
    #    test reports the FlareSolverr requirement get the tag.
    sem = asyncio.Semaphore(_FLARE_TEST_CONCURRENCY)
    deadline = time.time() + _FLARE_TEST_BUDGET_S
    tagged: list[str] = []
    errs: list[str] = []
    # `_`-prefixed so the nested _check_one's nonlocal references don't read as
    # shadowing the enclosing scope (they ARE the enclosing accumulators by
    # design); the output keys below stay the plain names.
    _checked = 0
    _needed = 0
    _remaining = 0

    async def _check_one(_rid: int, _body: dict) -> None:
        nonlocal _checked, _needed, _remaining
        async with sem:
            if time.time() >= deadline:
                _remaining += 1
                return
            nm = str(_body.get("name") or _rid)
            # Test the existing indexer config — the response (200 valid, or 400
            # with validationFailures incl. warnings) carries the FlareSolverr
            # requirement message when the site is Cloudflare-protected.
            try:
                tr = await cli.post(base + "/api/v1/indexer/test",
                                    headers=_headers(key), json=_body)
            except (httpx.HTTPError, OSError):
                errs.append(nm)
                return
            _checked += 1
            if not _FLARE_ERROR_RE.search(_resp_text(tr)):
                return  # doesn't need FlareSolverr
            _needed += 1
            _raw_tags = _body.get("tags")
            _cur_tags = _raw_tags if isinstance(_raw_tags, list) else []
            new_body = dict(_body)
            new_body["tags"] = sorted(set(_cur_tags) | flare_set)
            try:
                pr = await cli.put(base + f"/api/v1/indexer/{_rid}",
                                   headers=_headers(key), json=new_body)
            except (httpx.HTTPError, OSError):
                errs.append(nm)
                return
            if pr.status_code in (200, 202):
                tagged.append(nm)
            else:
                errs.append(nm)

    await asyncio.gather(*[_check_one(rid, body) for rid, body in candidates])
    return {"checked": _checked, "needed": _needed, "tagged": tagged,
            "already_tagged": already, "errs": errs,
            "timed_out": _remaining > 0, "remaining": _remaining}


def _facet_label(privacy: str, lang_term: str, want_lang: str, all_mode: bool) -> str:
    """Human descriptor for the active bulk-add facets ('public english',
    'public', 'addable', …) used in the summary header / empty message."""
    if all_mode:
        return "addable"
    bits = []
    if privacy:
        bits.append(_PRIVACY_LABELS.get(privacy, privacy))
    if want_lang:
        bits.append(want_lang)
    elif lang_term:
        bits.append(lang_term)
    return " ".join(bits) if bits else "matching"


# noinspection DuplicatedCode
async def _bulk_add_indexers_skill(host_row: dict, chip: dict, *,
                                   arg: Optional[str] = None,
                                   host_id: Optional[str] = None) -> dict:
    """Live MULTI write: add EVERY catalog indexer matching the facet ``arg``
    (privacy / language / name, or "all") that isn't already configured — in one
    command. Auto-assigns FlareSolverr to any indexer Prowlarr rejects with a
    Cloudflare / challenge error. Returns a summary (added / added-with-flare /
    already-configured / failed). Never raises."""
    term = (arg or "").strip().lower()
    if not term:
        return {"ok": False, "status": 0,
                "detail": "tell me which indexers to add — e.g. “add all the public "
                          "english indexers”, “add all public indexers”, or “add all "
                          "indexers matching torrent”. (Say “all” to add every "
                          "addable indexer not yet configured.)"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    all_mode = term in ("all", "everything", "every", "all of them", "the rest")
    privacy, lang_term = ("", "") if all_mode else _extract_privacy(term)
    want_lang = _lang_primary(lang_term) if lang_term else ""
    print(f"[prowlarr] INFO prowlarr_add_indexers_bulk host={host_id} term={term!r} "
          f"privacy={privacy!r} lang={want_lang!r} all={all_mode} (live BULK write)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            defs, schema_err = await _fetch_indexer_schema(cli, base, api_key)
            if schema_err is not None:
                return schema_err
            defs = defs or []
            added_keys = await _added_indexer_keys(cli, base, api_key)
            app_profile_id = await _default_app_profile_id(cli, base, api_key)
            flare_tag_ids, flare_note = await _flaresolverr_tag_ids(cli, base, api_key)
            # Candidate selection: facet-match THEN drop already-configured.
            facet_matched = 0
            candidates: list[dict] = []
            for d in defs:
                if not isinstance(d, dict):
                    continue
                name = str(d.get("name") or "").strip()
                if not name:
                    continue
                if privacy and _indexer_privacy(d) != privacy:
                    continue
                if lang_term and not all_mode:
                    lang = _indexer_lang(d)
                    name_hit = lang_term in name.lower()
                    lang_sub = lang_term in lang.lower()
                    lang_fam = bool(want_lang) and _lang_primary(lang) == want_lang
                    if not (name_hit or lang_sub or lang_fam):
                        continue
                facet_matched += 1
                if _def_keys(d) & added_keys:
                    continue
                candidates.append(d)
            candidates.sort(key=lambda cand: str(cand.get("name") or "").lower())
            skipped_existing = facet_matched - len(candidates)
            total_to_add = len(candidates)
            truncated = total_to_add > _BULK_ADD_MAX
            candidates = candidates[:_BULK_ADD_MAX]
            facet = _facet_label(privacy, lang_term, want_lang, all_mode)
            if not candidates:
                if skipped_existing:
                    return {"ok": True, "status": 200,
                            "detail": f"✅ Nothing to add — all {skipped_existing:,} {facet} "
                                      f"indexers are already configured."}
                return {"ok": True, "status": 200,
                        "detail": f"🔍 No addable {facet} indexers matched. Try “what "
                                  f"indexers can I add matching {term}” to see the catalog."}
            # Bulk add: bounded concurrency + an overall wall-clock budget.
            sem = asyncio.Semaphore(_BULK_ADD_CONCURRENCY)
            deadline = time.time() + _BULK_ADD_BUDGET_S
            not_attempted: list[str] = []

            async def _worker(cand: dict) -> Optional[dict]:
                async with sem:
                    if time.time() >= deadline:
                        not_attempted.append(str(cand.get("name") or "?").strip())
                        return None
                    return await _add_one_indexer(cli, base, api_key, cand,
                                                  app_profile_id, flare_tag_ids)

            res = await asyncio.gather(*[_worker(cand) for cand in candidates])
            results = [r for r in res if isinstance(r, dict)]
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[prowlarr] warning: bulk add host={host_id} failed — {type(e).__name__}: {e}")
        return {"ok": False, "status": 0, "detail": f"bulk add failed: {type(e).__name__}: {e}"}
    added = [r for r in results if r.get("ok")]
    added_flare = [r for r in added if r.get("flare")]
    failed = [r for r in results if not r.get("ok")]
    # An indexer can ADD cleanly (HTTP 201) yet still NEED FlareSolverr — Prowlarr
    # surfaces "this site may use Cloudflare DDoS Protection, therefore Prowlarr
    # requires FlareSolverr" as a TEST warning, not an add error, so the
    # retry-on-failure path above misses it. Run a per-indexer test pass scoped
    # to the just-added indexers and PUT the proxy tag onto the ones that need it.
    flare_tagged: list[str] = []
    added_ids = {safe_int(r.get("id")) for r in added if safe_int(r.get("id"))}
    if flare_tag_ids and added_ids:
        try:
            async with httpx.AsyncClient(verify=False, timeout=20.0,
                                         follow_redirects=True) as fcli:
                fix = await _apply_flaresolverr_tags(fcli, base, api_key,
                                                     flare_tag_ids, only_ids=added_ids)
            flare_tagged = fix.get("tagged") or []
        except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
            print(f"[prowlarr] warning: bulk flare-tag host={host_id} skipped — "
                  f"{type(e).__name__}: {e}")
    # 'errs=' (not 'failed=') in this INFO summary so the persistent-log
    # severity classifier doesn't mis-file a normal success line as ERROR
    # (it matches \bfail(ed|ure)?\b / \berror\b in the message text).
    print(f"[prowlarr] INFO bulk add host={host_id} facet={facet!r} added={len(added)} "
          f"(flare={len(added_flare)}, flare_tagged={len(flare_tagged)}) "
          f"errs={len(failed)} skipped={skipped_existing} "
          f"not_attempted={len(not_attempted)}")
    lines = [
        f"📦 Bulk-add ({facet} indexers):",
        f"✅ Added: {len(added):,}"
        + (f" ({len(added_flare):,} via FlareSolverr)" if added_flare else ""),
    ]
    if skipped_existing:
        lines.append(f"⏭️ Already configured: {skipped_existing:,}")
    if failed:
        lines.append(f"⚠️ Failed: {len(failed):,}")
    if not_attempted:
        lines.append(f"⏳ Not attempted (time budget): {len(not_attempted):,}")
    if added:
        names = ", ".join(r["name"] + (" 🛡️" if r.get("flare") else "") for r in added[:30])
        lines.append("➕ " + names + ("…" if len(added) > 30 else ""))
    if flare_tagged:
        lines.append(f"🛡️ FlareSolverr tag applied to {len(flare_tagged):,} cloudflare-"
                     f"protected indexer(s): " + ", ".join(flare_tagged[:20])
                     + ("…" if len(flare_tagged) > 20 else ""))
    if failed:
        det = [f"  • {r['name']}: {_failed_reason(r)}" for r in failed[:12]]
        lines.append("Failed:\n" + "\n".join(det) + ("\n  …" if len(failed) > 12 else ""))
    # When some failures look like Cloudflare blocks but no proxy could be used,
    # tell the user how to enable the auto-FlareSolverr path.
    if not flare_tag_ids and any(_FLARE_ERROR_RE.search(r.get("reason") or "") for r in failed):
        lines.append(f"ℹ️ Some failures look like Cloudflare blocks but {flare_note}. "
                     f"Add a FlareSolverr proxy in Prowlarr (Settings → Indexer "
                     f"Proxies), then re-run to auto-assign it.")
    if truncated:
        lines.append(f"… {total_to_add - len(candidates):,} more matched but weren't "
                     f"attempted this run (cap {_BULK_ADD_MAX}). Run again to add the rest.")
    if added:
        lines.append("Run “sync indexers to my apps” to push the new indexers to Radarr/Sonarr/etc.")
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "added": len(added), "added_flaresolverr": len(added_flare),
            "flare_tagged": len(flare_tagged),
            "skipped_existing": skipped_existing, "failed": len(failed),
            "not_attempted": len(not_attempted)}


async def _fix_flaresolverr_skill(host_row: dict, chip: dict, *,
                                  host_id: Optional[str] = None) -> dict:
    """Live WRITE: test each NOT-yet-tagged configured indexer (individually, via
    ``POST /api/v1/indexer/test``), find the ones Prowlarr says need FlareSolverr
    ("this site may use Cloudflare DDoS Protection, therefore Prowlarr requires
    FlareSolverr"), and PUT the FlareSolverr proxy tag onto them so they link to
    the proxy. Bounded concurrency + an overall budget UNDER the reverse-proxy
    timeout so it returns a clean (possibly partial) result instead of a 504;
    untested candidates are deferred to the next run. Never raises. Use after a
    bulk add, or to fix indexers added before FlareSolverr was configured."""
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[prowlarr] INFO prowlarr_fix_flaresolverr host={host_id} (per-indexer test + tag)")
    try:
        # client timeout > the in-loop budget so the budget (not the socket) is
        # what bounds us; both are well under the reverse-proxy read window.
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            flare_tag_ids, flare_note = await _flaresolverr_tag_ids(cli, base, api_key)
            if not flare_tag_ids:
                return {"ok": False, "status": 0,
                        "detail": f"🛡️ Can't fix FlareSolverr tags — {flare_note}. Add a "
                                  f"FlareSolverr proxy in Prowlarr (Settings → Indexer "
                                  f"Proxies) first, then re-run."}
            fix = await _apply_flaresolverr_tags(cli, base, api_key, flare_tag_ids)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[prowlarr] warning: fix_flaresolverr host={host_id} — {type(e).__name__}: {e}")
        return {"ok": False, "status": 0,
                "detail": f"FlareSolverr fix could not run: {type(e).__name__}: {e}"}
    tagged = fix.get("tagged") or []
    already = safe_int(fix.get("already_tagged"))
    errs = fix.get("errs") or []
    needed = safe_int(fix.get("needed"))
    checked = safe_int(fix.get("checked"))
    remaining = safe_int(fix.get("remaining"))
    timed_out = bool(fix.get("timed_out"))
    # NOTE: 'errs'/'untagged' word choice avoids the persistent-log ERROR-severity
    # classifier (which matches \bfail(ed|ure)?\b / \berror\b in the message text).
    print(f"[prowlarr] INFO fix_flaresolverr host={host_id} checked={checked} "
          f"needed={needed} tagged={len(tagged)} already={already} "
          f"untagged={len(errs)} remaining={remaining}")
    # Nothing actionable: distinguish "all already tagged" from "tested, none need it".
    if not tagged and not needed and not timed_out:
        if already and not checked:
            return {"ok": True, "status": 200,
                    "detail": f"✅ All {already:,} indexer(s) that need FlareSolverr are "
                              f"already tagged — nothing to do."}
        return {"ok": True, "status": 200,
                "detail": f"✅ Tested {checked:,} indexer(s) — none need FlareSolverr."
                          + (f" ({already:,} already tagged.)" if already else "")}
    lines = [f"🛡️ FlareSolverr check (tested {checked:,} indexer(s)):"]
    if tagged:
        lines.append(f"✅ Tagged: {len(tagged):,} — " + ", ".join(tagged[:25])
                     + ("…" if len(tagged) > 25 else ""))
    if already:
        lines.append(f"⏭️ Already tagged: {already:,}")
    if errs:
        lines.append(f"⚠️ Couldn't tag: {len(errs):,} — " + ", ".join(errs[:12])
                     + ("…" if len(errs) > 12 else ""))
    if timed_out and remaining:
        lines.append(f"⏳ {remaining:,} indexer(s) not checked this run (time budget) "
                     f"— run “fix flaresolverr” again to continue.")
    if tagged:
        lines.append("Run “sync indexers to my apps” to push the change to Radarr/Sonarr/etc.")
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "needed": needed, "tagged": len(tagged), "checked": checked,
            "already_tagged": already, "errs": len(errs), "remaining": remaining}
