"""Tdarr per-app module.

Encapsulates everything Tdarr-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Tdarr (github.com/HaveAGitGat/
Tdarr) is a distributed media-transcode automation server — it walks your
libraries, transcodes / health-checks files across a pool of worker nodes, and
tracks how much space each transcode saved. Member of the per-app family in
SHAPE (SLUGS / requires_api_key / test_credential / fetch_data / peek_latest /
SKILLS / run_skill) but BESPOKE — its auth + API differ:

  Auth model — Tdarr is NO-AUTH by default (a trusted-LAN service). When the
    operator enables auth (Tdarr → Tools → API Keys), requests carry an
    ``x-api-key`` header. So the api_key is OPTIONAL here: ``requires_api_key()``
    is False (the card works without it), and the editor exposes an OPTIONAL
    key field that's sent as ``x-api-key`` when set. Stateless — the key (if
    any) goes on every request.

  API — the Tdarr v2 API at ``<base>/api/v2``:
    * ``GET  /api/v2/status``    → ``{status, version, os, uptime}`` (liveness).
    * ``POST /api/v2/cruddb``    → generic DB CRUD. The card's totals come from
      the ``StatisticsJSONDB`` doc; the bloated-file logic queries
      ``FileJSONDB``. Body shape: ``{"data": {collection, mode, docID?, obj?,
      filters?}}``.
    * ``GET  /api/v2/get-nodes`` → ``{nodeId: {nodeName, workers: {wId: {job,
      file, percentage, ...}}}}`` — the live worker status.

The expanded card answers "is Tdarr keeping up + how much space has it saved":

    total_files     — files Tdarr is tracking      (StatisticsJSONDB.totalFileCount)
    transcode_queue — files queued to transcode      (StatisticsJSONDB.table1Count)
    health_queue    — files queued to health-check    (StatisticsJSONDB.table4Count)
    space_saved_gb  — net space reclaimed (GB)         (StatisticsJSONDB.sizeDiff)
    workers_active  — workers currently processing      (get-nodes)
    nodes           — registered worker nodes            (get-nodes)
    version         — Tdarr server version                (status)

AI / Telegram skills (BLOATED handling ported from the operator's reference bot):
* ``tdarr_status``          — what each worker is transcoding right now (+ %).
* ``tdarr_bloated``         — "check bloated": files that got LARGER after
                              transcoding (``newVsOldRatio > 100``).
* ``tdarr_requeue_bloated`` — "queue bloated" (DESTRUCTIVE): requeue every
                              bloated file (reset its DB status to ``Queued``).

Single-instance app (NOT fleet) — one card per pinned chip.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, peek_cache, resolve_base_url, resolve_cache_ttl)
from logic.coerce import as_dict, as_list, safe_float, safe_int
from logic.db import get_setting, set_setting
from logic.settings_keys import Settings

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("tdarr",)

# Tdarr v2 API base path.
_API = "/api/v2"

# StatisticsJSONDB.tableNCount → queue meaning (Tdarr's UI grouping):
#   table0 = hold, table1 = transcode QUEUE, table2 = transcode success,
#   table3 = transcode failed, table4 = health-check QUEUE,
#   table5 = health-check success, table6 = health-check failed.

# Per-(host_id, service_idx) data cache for the expanded card. 60s default —
# matches the rest of the family.
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# Card fetch timeout. The load-bearing StatisticsJSONDB getById is a single doc,
# but Tdarr gets momentarily slow while a heavy bloated scan streams FileJSONDB
# in the background — a generous read keeps the card from erroring on every poll
# during a scan (and we serve the last-good card on a hard timeout anyway).
_CARD_TIMEOUT = httpx.Timeout(45.0, connect=10.0)

# The per-library get-pies aggregation (1 + up-to-12 calls) is ENRICHMENT only
# (the card hides the breakdowns when empty). Cap its total wall-clock so a slow
# library can never dominate / time out the whole card fetch — empty on timeout.
_PIES_BUDGET_S = 15.0

# A file is "bloated" when its transcode produced a LARGER file — Tdarr stores
# this as newVsOldRatio (a percentage; 100 = same size, >100 = bigger).
_BLOAT_RATIO = 100.0

# The bloated check/requeue does a cruddb `getAll` over FileJSONDB, whose
# response carries the FULL file records — on a large library that's hundreds of
# MB and can take ~100s to stream. A short timeout truncates the body mid-stream
# (httpx RemoteProtocolError). Connect stays short; read/total is generous so
# the big query completes. (Generous fixed cap rather than a tunable — it only
# gates two manual on-demand skills, not a hot path.)
#
# This is the SAME query the operator's reference Telegram bot uses (services/
# tdarr.py:list_bloated_files) — the query itself is correct and was proven
# working there. The difference is the TRANSPORT: a Telegram bot tolerates a
# ~100s operation (it streams a progress_callback while it runs), but OmniGrid's
# skill is a browser→app HTTP request that dies at the reverse proxy's
# proxy_read_timeout (~60s) long before the 100s scan finishes — that's the 504.
# So we keep the identical query but run it as a BACKGROUND task and serve a
# per-host result cache: the skill returns immediately ("scanning… re-run to see
# results") and the next invocation serves the completed list — OmniGrid's
# request/response analogue of the bot's progress_callback.
_BLOATED_TIMEOUT = httpx.Timeout(240.0, connect=15.0)

# How long a completed bloated scan is served without re-running (bloat state
# changes slowly). Fixed cap, not a tunable — matches _BLOATED_TIMEOUT's
# rationale (gates two manual on-demand skills, not a hot path).
_BLOATED_CACHE_TTL = 600.0  # 10 min

# Per-host_id background-scan state:
#   {"running": bool, "started": float, "ts": float|None, "files": list|None,
#    "error": str|None, "requeue": {"running","started","ts","done","total",
#    "error"}}
# Inner value type is Any (heterogeneous: bool / float / list / str / dict) —
# `dict[str, dict]` would (wrongly) type every `st["files"]` / `st["ts"]` as a
# dict, tripping the checker where a list / float is expected.
_bloated_state: dict[str, dict[str, Any]] = {}

# Strong refs to in-flight background tasks so asyncio's GC can't collect a
# running scan/requeue mid-execution (the _spawn_background_task contract,
# replicated locally to avoid a logic→main import cycle).
_BG_TASKS: set = set()

# Total wall-clock budget for the expanded-CARD fetch (fetch_data). The card's
# per-request timeout is generous (_CARD_TIMEOUT) but several sequential calls
# (cruddb + status + get-nodes + bounded pies) can add up — and while a bloated
# scan is streaming FileJSONDB, Tdarr is slow on ALL of them. This bounds the
# WHOLE fetch so the card serves its last-good cached value FAST instead of
# letting the request run long enough to trip the generic per-app route budget
# (tuning_apps_route_budget_seconds, default 50s) and 504 the card. Kept well
# under that default.
_CARD_TOTAL_BUDGET_S = 35.0

# Per-host bloated-scan wall-clock history (persisted JSON map host_id ->
# [seconds,...]) so the "Scanning…" message shows a MEASURED average ETA
# instead of static copy. Last N kept per host.
_SCAN_DURATION_HISTORY = 10


def _spawn(coro) -> "asyncio.Task":
    """Fire-and-forget a background coroutine, keeping a strong ref until done."""
    t = asyncio.create_task(coro)
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)
    return t


def _load_scan_durations() -> dict:
    """The persisted ``{host_id: [seconds,...]}`` scan-duration map (``{}`` on
    any error)."""
    try:
        raw = get_setting(Settings.TDARR_SCAN_DURATIONS)
        if not raw:
            return {}
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error):
        return {}


def _record_scan_duration(host_id: str, seconds: float) -> None:
    """Append a completed scan's wall-clock to the host's rolling history (last
    ``_SCAN_DURATION_HISTORY``) and persist. Best-effort."""
    if not host_id or seconds <= 0:
        return
    try:
        m = _load_scan_durations()
        hist = m.get(host_id)
        if not isinstance(hist, list):
            hist = []
        hist.append(round(float(seconds), 1))
        m[host_id] = hist[-_SCAN_DURATION_HISTORY:]
        set_setting(Settings.TDARR_SCAN_DURATIONS, json.dumps(m))
    except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error):
        pass


def _scan_eta(host_id: str) -> "tuple[Optional[float], int]":
    """``(average scan seconds, sample count)`` from the host's history, or
    ``(None, 0)`` when there's no measured history yet."""
    hist = _load_scan_durations().get(host_id)
    if not isinstance(hist, list):
        return None, 0
    vals = [safe_float(x) for x in hist if safe_float(x) > 0]
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def _fmt_duration(seconds: float) -> str:
    """Humanise a seconds figure: ``45s`` / ``1m 30s`` / ``2m``."""
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    return f"{m}m" + (f" {sec}s" if sec else "")


def _scan_eta_phrase(host_id: str) -> str:
    """The ETA sentence for a 'Scanning…' message — a MEASURED average when this
    host has history, else the static fallback."""
    avg, n = _scan_eta(host_id)
    if avg:
        return (f"This usually takes ~{_fmt_duration(avg)} "
                f"(average of the last {n} scan{'s' if n != 1 else ''})")
    return "This takes ~1–2 minutes on a large library"


def requires_api_key() -> bool:
    """Tdarr is no-auth by default; the key is OPTIONAL (x-api-key when set)."""
    return False


def _headers(api_key: str) -> dict:
    """JSON headers + the optional ``x-api-key`` (only when auth is enabled)."""
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    key = (api_key or "").strip()
    if key:
        h["x-api-key"] = key
    return h


async def _get(cli: httpx.AsyncClient, base: str, api_key: str, path: str) -> Any:
    """One Tdarr GET (``/status`` / ``/get-nodes``). Raises ``RuntimeError`` on
    transport / auth / non-200 / non-JSON."""
    try:
        r = await cli.get(base.rstrip("/") + _API + path, headers=_headers(api_key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"request failed: {type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError("auth failed: Tdarr requires an API key (set it in the editor)")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for {path}")
    try:
        return r.json()
    except (ValueError, TypeError):
        raise RuntimeError("non-JSON from upstream")


async def _cruddb(cli: httpx.AsyncClient, base: str, api_key: str, data: dict,
                  *, parse_json: bool = True) -> Any:
    """One ``POST /api/v2/cruddb`` with the ``{"data": <data>}`` envelope.
    Raises ``RuntimeError`` on transport / auth / non-200.

    ``parse_json=True`` (the default, for READS — getById / getAll) parses + returns
    the JSON body, raising on a non-JSON body. ``parse_json=False`` is for WRITES
    (``mode=update``): Tdarr answers a successful cruddb update with a 200 and a
    NON-JSON / empty body, so requiring JSON there would misread every successful
    update as a failure (the reference bot just checks the 200 status). With
    ``parse_json=False`` a 200 is success regardless of body."""
    try:
        r = await cli.post(base.rstrip("/") + _API + "/cruddb",
                           headers=_headers(api_key), json={"data": data})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"request failed: {type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError("auth failed: Tdarr requires an API key (set it in the editor)")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for cruddb")
    if not parse_json:
        return None
    try:
        return r.json()
    except (ValueError, TypeError):
        raise RuntimeError("non-JSON from upstream")


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Tdarr by calling ``GET /api/v2/status``. The api_key is OPTIONAL —
    a blank key still probes (Tdarr is usually open); a set key is sent as
    ``x-api-key``. Returns ``{ok, detail, status}``."""
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    key = (candidate_key or "").strip() or (chip.get("api_key") or "").strip()
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            status = await _get(cli, base, key, "/status")
    except RuntimeError as e:
        return {"ok": False, "detail": str(e), "status": 0}
    version = str(as_dict(status).get("version") or "").strip()
    return {"ok": True, "detail": (f"OK (Tdarr {version})" if version else "OK"),
            "status": 200}


def _stats_doc(raw: Any) -> dict:
    """``StatisticsJSONDB.getById`` returns the statistics doc (a dict), but
    some Tdarr builds wrap it in a 1-element list — normalise to the dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    return {}


def _count_workers(nodes: Any) -> "tuple[int, int]":
    """``(active_workers, node_count)`` from a ``get-nodes`` payload — a worker
    is active when it has a ``job``."""
    nd = as_dict(nodes)
    active = 0
    for node in nd.values():
        if not isinstance(node, dict):
            continue
        for w in as_dict(node.get("workers")).values():
            if isinstance(w, dict) and w.get("job"):
                active += 1
    return active, len(nd)


def _worker_list(nodes: Any) -> list:
    """Per-active-worker detail ``[{node, file, pct, type}]`` from a ``get-nodes``
    payload — what each worker is processing right now (basename + %)."""
    out = []
    for node in as_dict(nodes).values():
        if not isinstance(node, dict):
            continue
        node_name = str(node.get("nodeName") or "node").strip()
        for w in as_dict(node.get("workers")).values():
            if not isinstance(w, dict) or not w.get("job"):
                continue
            out.append({
                "node": node_name,
                "file": os.path.basename(str(w.get("file") or "").strip()) or "?",
                "pct": round(safe_float(w.get("percentage")), 1),
                "type": str(w.get("workerType") or w.get("type") or "").strip(),
            })
    return out


async def _post(cli: httpx.AsyncClient, base: str, api_key: str,
                path: str, body: dict) -> Any:
    """Generic Tdarr POST (used for ``/stats/get-pies``). Raises ``RuntimeError``
    on transport / auth / non-200 / non-JSON."""
    try:
        r = await cli.post(base.rstrip("/") + _API + path,
                           headers=_headers(api_key), json=body)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"request failed: {type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError("auth failed: Tdarr requires an API key (set it in the editor)")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for {path}")
    try:
        return r.json()
    except (ValueError, TypeError):
        raise RuntimeError("non-JSON from upstream")


def _agg_slices(target: dict, slices: Any) -> None:
    """Sum a pie's ``[{name, value}]`` slices into ``target`` (name → count)."""
    for s in as_list(slices):
        if isinstance(s, dict):
            name = str(s.get("name") or "?").strip() or "?"
            target[name] = target.get(name, 0) + safe_int(s.get("value"))


def _top_slices(d: dict, n: int = 6) -> list:
    """``[{name, count}]`` for the top-``n`` aggregated slices (count desc)."""
    return [{"name": k, "count": v}
            for k, v in sorted(d.items(), key=lambda x: x[1], reverse=True)[:n]]


async def _fetch_pies(cli: httpx.AsyncClient, base: str, api_key: str) -> dict:
    """Aggregate the per-library ``stats/get-pies`` breakdowns into global top
    VIDEO resolutions / codecs / containers. The library list comes from
    ``LibrarySettingsJSONDB``; each library is one ``get-pies`` POST
    (``{"data": {"libraryId": <id>}}``) whose response wraps the breakdown in
    ``pieStats.video.{resolutions,codecs,containers}`` as ``[{name, value}]``.
    Best-effort — returns empty lists on any failure (the card hides them)."""
    try:
        libs = as_list(await _cruddb(cli, base, api_key, {
            "collection": "LibrarySettingsJSONDB", "mode": "getAll",
            "docID": "", "obj": {}}))
    except RuntimeError:
        return {"resolutions": [], "codecs": [], "containers": []}
    res: dict = {}
    codecs: dict = {}
    containers: dict = {}
    for lib in libs[:12]:
        lid = str(as_dict(lib).get("_id") or "").strip()
        if not lid:
            continue
        try:
            raw = await _post(cli, base, api_key, "/stats/get-pies",
                              {"data": {"libraryId": lid}})
        except RuntimeError:
            continue
        # Response wraps the stat in `pieStats`; tolerate an unwrapped shape too.
        pie = as_dict(as_dict(raw).get("pieStats") or raw)
        video = as_dict(pie.get("video"))
        _agg_slices(res, video.get("resolutions"))
        _agg_slices(codecs, video.get("codecs"))
        _agg_slices(containers, video.get("containers"))
    return {"resolutions": _top_slices(res), "codecs": _top_slices(codecs),
            "containers": _top_slices(containers)}


# noinspection DuplicatedCode
# The upstream-error guard + cache block below is structurally shared with every
# other per-app module's fetch_data — the deliberate per-app encapsulation
# pattern (CLAUDE.md). Content differs (Tdarr cruddb stats), so it stays inline.
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Tdarr's transcode-pipeline summary for the expanded card.

    Returns ``{available, total_files, transcode_queue, health_queue,
    space_saved_gb, transcodes, workers_active, nodes, version, fetched_at}``.
    Raises ``ValueError`` / ``RuntimeError`` when the base URL won't resolve /
    the stats call errors. The cruddb stats call is load-bearing; ``/status``
    + ``/get-nodes`` are tolerated (0 / "" when unavailable)."""
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured")
    api_key = (chip.get("api_key") or "").strip()
    now = time.time()
    ttl = resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S)
    ck = cache_key(host_id, service_idx)
    cached = _data_cache.get(ck)
    if cached and not force and (now - cached[0]) < ttl:
        return cached[1]

    async def _collect():
        """The network section — bounded as a whole by _CARD_TOTAL_BUDGET_S
        (below) so a Tdarr that's slow on every call while a bloated scan
        streams can't run the card past the per-app route budget."""
        async with httpx.AsyncClient(verify=False, timeout=_CARD_TIMEOUT,
                                     follow_redirects=True) as cli:
            _stats = _stats_doc(await _cruddb(cli, base, api_key, {
                "collection": "StatisticsJSONDB", "mode": "getById",
                "docID": "statistics", "obj": {}}))
            try:
                _version = str(as_dict(await _get(cli, base, api_key, "/status")).get("version") or "").strip()
            except RuntimeError:
                _version = ""
            _wa, _nodes, _wl = 0, 0, []
            try:
                nd = await _get(cli, base, api_key, "/get-nodes")
                _wa, _nodes = _count_workers(nd)
                _wl = _worker_list(nd)
            except RuntimeError:
                _wa, _nodes, _wl = 0, 0, []
            # Library breakdowns (resolutions / codecs / containers) — best-effort
            # per-library get-pies aggregation, BOUNDED so a slow library can't
            # time out the whole card; empty on timeout (the card hides them).
            try:
                _pies = await asyncio.wait_for(
                    _fetch_pies(cli, base, api_key), timeout=_PIES_BUDGET_S)
            except (asyncio.TimeoutError, RuntimeError):
                _pies = {"resolutions": [], "codecs": [], "containers": []}
            return _stats, _version, _wa, _nodes, _wl, _pies

    try:
        stats, version, workers_active, nodes, worker_list, pies = \
            await asyncio.wait_for(_collect(), timeout=_CARD_TOTAL_BUDGET_S)
    except (RuntimeError, asyncio.TimeoutError) as e:
        # Transient upstream slowness (e.g. Tdarr busy while a bloated scan
        # streams FileJSONDB) — serve the LAST-GOOD card instead of erroring on
        # every poll. Only hard-fail when there's nothing cached to fall back to.
        timed_out = isinstance(e, asyncio.TimeoutError)
        if cached is not None:
            why = (f"timed out (> {_CARD_TOTAL_BUDGET_S:.0f}s)" if timed_out
                   else f"failed ({e})")
            print(f"[tdarr] warning: fetch host={host_id} {why} — serving "
                  f"cached card ({int(now - cached[0])}s old)")
            return cached[1]
        if timed_out:
            print(f"[tdarr] error: fetch host={host_id} — exceeded "
                  f"{_CARD_TOTAL_BUDGET_S:.0f}s total budget (Tdarr too slow, "
                  f"likely a bloated scan in progress)")
            raise RuntimeError(f"card fetch exceeded {_CARD_TOTAL_BUDGET_S:.0f}s budget")
        print(f"[tdarr] error: fetch host={host_id} — {e}")
        raise RuntimeError(str(e))
    out: dict[str, Any] = {
        "available": True,
        "total_files": safe_int(stats.get("totalFileCount")),
        "transcodes": safe_int(stats.get("totalTranscodeCount")),
        "health_checks": safe_int(stats.get("totalHealthCheckCount")),
        "transcode_queue": safe_int(stats.get("table1Count")),
        "health_queue": safe_int(stats.get("table4Count")),
        "space_saved_gb": round(safe_float(stats.get("sizeDiff")), 1),
        "workers_active": workers_active,
        "nodes": nodes,
        "workers": worker_list,
        "resolutions": pies.get("resolutions", []),
        "codecs": pies.get("codecs", []),
        "containers": pies.get("containers", []),
        "version": version,
        "fetched_at": int(now),
    }
    print(f"[tdarr] INFO fetched host={host_id} files={out['total_files']} "
          f"tq={out['transcode_queue']} hq={out['health_queue']} "
          f"transcodes={out['transcodes']} healthchecks={out['health_checks']} "
          f"saved={out['space_saved_gb']}GB workers={workers_active}/{nodes} "
          f"res={len(out['resolutions'])} codecs={len(out['codecs'])}")
    _data_cache[ck] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "total_files": safe_int(data.get("total_files")),
        "transcodes": safe_int(data.get("transcodes")),
        "health_checks": safe_int(data.get("health_checks")),
        "transcode_queue": safe_int(data.get("transcode_queue")),
        "health_queue": safe_int(data.get("health_queue")),
        "space_saved_gb": safe_float(data.get("space_saved_gb")),
        "workers_active": safe_int(data.get("workers_active")),
        "nodes": safe_int(data.get("nodes")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


SKILLS: tuple[dict, ...] = (
    {
        "id": "tdarr_status",
        "name": "Tdarr status",
        "ai_phrases": ("tdarr status, what is tdarr transcoding, tdarr workers, "
                       "is tdarr busy, transcode progress, what's processing on "
                       "tdarr, tdarr queue, tdarr summary"),
        "destructive": False,
    },
    {
        "id": "tdarr_bloated",
        "name": "Check bloated files",
        "ai_phrases": ("check bloated, list bloated files, files that got "
                       "bigger after transcode, bloated transcodes, files larger "
                       "than original, tdarr bloated"),
        "destructive": False,
    },
    {
        "id": "tdarr_requeue_bloated",
        "name": "Requeue bloated files",
        "ai_phrases": ("requeue bloated, queue bloated, re-transcode bloated "
                       "files, fix bloated files, requeue the bloated transcodes, "
                       "re-process bloated"),
        "destructive": True,
    },
    {
        "id": "tdarr_requeue_failed",
        "name": "Requeue failed transcodes",
        "ai_phrases": ("requeue failed, requeue cancelled, retry failed "
                       "transcodes, re-queue errored files, requeue transcode "
                       "errors, fix failed transcodes, retry cancelled transcodes, "
                       "requeue all failed, re-process failed transcodes"),
        "destructive": True,
    },
)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown id."""
    if skill_id == "tdarr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "tdarr_bloated":
        return await _bloated_skill(host_row, chip, host_id=host_id)
    if skill_id == "tdarr_requeue_bloated":
        return await _requeue_bloated_skill(host_row, chip, host_id=host_id)
    if skill_id == "tdarr_requeue_failed":
        return await _requeue_failed_skill(host_row, chip, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _resolve_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(api_key, base)`` or a ready ``{ok: False, detail}``. The key
    may be blank (Tdarr is often open) — only the base URL is required."""
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return (chip.get("api_key") or "").strip(), base, None


async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: what each worker is transcoding right now (+ %), plus a queue
    summary line. Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[tdarr] INFO tdarr_status host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            nodes = await _get(cli, base, api_key, "/get-nodes")
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    lines: list[str] = []
    # Rich items for the web skill-result renderer — each active worker as a
    # row with a 2-colour progress bar (apps-skill-item-bar). The text `lines`
    # / `detail` stay for the AI / Telegram surfaces (no visual progress bar).
    items: list[dict] = []
    for node in as_dict(nodes).values():
        if not isinstance(node, dict):
            continue
        node_name = str(node.get("nodeName") or "node").strip()
        for w in as_dict(node.get("workers")).values():
            if not isinstance(w, dict) or not w.get("job"):
                continue
            fname = os.path.basename(str(w.get("file") or "").strip()) or "?"
            pct = w.get("percentage")
            pct_txt = f" ({safe_float(pct):.1f}%)" if pct is not None else ""
            lines.append(f"⚙️ {node_name}: {fname}{pct_txt}")
            wtype = str(w.get("workerType") or w.get("type") or "").strip()
            items.append({
                "title": fname,
                "subtitle": (f"{node_name} · {wtype}" if wtype else node_name),
                "poster": "",
                "progress": round(safe_float(pct), 1),
            })
    # Queue summary from the card data (cheap second source).
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0))
        tq = safe_int(data.get("transcode_queue"))
        hq = safe_int(data.get("health_queue"))
        saved = safe_float(data.get("space_saved_gb"))
        summary = f"📊 Transcode queue: {tq:,} · Health queue: {hq:,} · Saved: {_fmt_gb(saved)}"
    except (ValueError, RuntimeError):
        summary = ""
    if not lines:
        body = "✅ Tdarr is idle — no workers are processing right now."
    else:
        body = f"▶️ {len(lines)} worker(s) processing:\n" + "\n".join(lines)
    if summary:
        body += "\n" + summary
    out: dict = {"ok": True, "status": 200, "detail": body}
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = "apps.tdarr.now_processing_count"
    return out


async def _find_bloated(cli: httpx.AsyncClient, base: str, api_key: str) -> list:
    """Every file whose transcode produced a LARGER output (``newVsOldRatio >
    100``), de-duped by ``_id`` and sorted worst-first. Ported from the
    operator's reference bot: query ``FileJSONDB`` for the two
    ``TranscodeDecisionMaker`` statuses that can carry a finished ratio."""
    raw: list = []
    for status in ("Transcode success", "Not required"):
        r = await _cruddb(cli, base, api_key, {
            "collection": "FileJSONDB", "mode": "getAll",
            "filters": [{"id": f"filter-{status.replace(' ', '-')}",
                         "key": "TranscodeDecisionMaker", "value": status}]})
        raw.extend(as_list(r))
    uniq = {f.get("_id"): f for f in raw if isinstance(f, dict) and f.get("_id")}
    bloated = [f for f in uniq.values() if safe_float(f.get("newVsOldRatio")) > _BLOAT_RATIO]
    bloated.sort(key=lambda f: safe_float(f.get("newVsOldRatio")), reverse=True)
    return bloated


async def _run_bloated_scan(base: str, api_key: str, host_id: str) -> None:
    """Background scan — runs the heavy ``_find_bloated`` getAll on its OWN
    client (the skill that launched it has already returned, so its client is
    closed) and stows the result in ``_bloated_state[host_id]`` for the next
    skill invocation to serve. Never raises out (logged + recorded on the
    state)."""
    st = _bloated_state.setdefault(host_id, {})
    started = safe_float(st.get("started")) or time.time()
    try:
        async with httpx.AsyncClient(verify=False, timeout=_BLOATED_TIMEOUT,
                                     follow_redirects=True) as cli:
            bloated = await _find_bloated(cli, base, api_key)
        st.update({"files": bloated, "ts": time.time(), "error": None})
        # Record the measured wall-clock so the next "Scanning…" message can show
        # a real average ETA instead of static "~1–2 minutes" copy.
        _record_scan_duration(host_id, time.time() - started)
        print(f"[tdarr] INFO bloated scan done host={host_id} found={len(bloated):,} "
              f"in {_fmt_duration(time.time() - started)}")
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (RuntimeError, httpx.HTTPError, OSError) as e:  # noqa: BLE001
        st.update({"ts": time.time(), "error": str(e)})
        print(f"[tdarr] warning: bloated scan failed host={host_id} — {e}")
    finally:
        st["running"] = False


def _ensure_bloated_scan(base: str, api_key: str, host_id: str) -> bool:
    """Launch a background bloated scan unless one is already running for this
    host. Returns True when a new scan was started."""
    st = _bloated_state.setdefault(host_id, {})
    if st.get("running"):
        return False
    st["running"] = True
    st["started"] = time.time()
    _spawn(_run_bloated_scan(base, api_key, host_id))
    return True


def _bloat_severity_emoji(ratio: float) -> str:
    """Severity dot for a bloat ratio (100 = same size): red ≥200% / amber
    ≥150% / yellow otherwise."""
    if ratio >= 200:
        return "🔴"
    if ratio >= 150:
        return "🟠"
    return "🟡"


def _fmt_bloated_result(files: list, note: str = "", pending: bool = False) -> dict:
    """Shape a completed bloated scan (list of file docs) into a skill result.

    Returns BOTH a text ``detail`` (AI / Telegram — no visual surface) AND rich
    ``items`` so the web drawer renders one clean row per file (filename +
    a severity-coloured ratio subtitle + a "how bloated" bar) instead of a wall
    of wrapped text. ``pending=True`` marks an in-progress scan so the SPA keeps
    auto-polling."""
    if not files:
        out: dict = {"ok": True, "status": 200,
                     "detail": "✅ No bloated files found." + note}
        if pending:
            out["pending"] = True
        return out
    lines = [f"• {os.path.basename(str(f.get('file') or '?'))}  "
             f"{safe_float(f.get('newVsOldRatio')):.1f}%"
             for f in files[:25]]
    more = f"\n…and {len(files) - 25:,} more" if len(files) > 25 else ""
    detail = (f"🐘 {len(files):,} bloated file(s) (larger after transcode):\n"
              + "\n".join(lines) + more + note
              + "\n\nUse \"Requeue bloated files\" to re-transcode them.")
    items = []
    for f in files[:25]:
        ratio = safe_float(f.get("newVsOldRatio"))
        items.append({
            "title": os.path.basename(str(f.get("file") or "?")),
            "subtitle": f"{_bloat_severity_emoji(ratio)} {ratio:.0f}% of original size",
            # Bar = how far OVER the original size (clamped) — 0 at 100% (same
            # size), full at ≥200% (double). Gives an at-a-glance bloat gauge.
            "progress": min(100, max(0, round(ratio - 100))),
        })
    out = {"ok": True, "status": 200, "detail": detail, "items": items,
           "count": len(files), "count_i18n": "apps.tdarr.bloated_count",
           # One-click follow-up: requeue every bloated file straight from the
           # result (web AI button / Telegram inline button). `destructive` so
           # the web surface threads the confirm flag; the explicit labelled
           # button / tap IS the confirmation. Generic shape {skill_id, arg,
           # label, destructive, emoji}.
           "followup": {
               "skill_id": "tdarr_requeue_bloated",
               "arg": "",
               "label": f"Requeue {len(files):,} bloated file(s)",
               "destructive": True,
               "emoji": "♻️",
           }}
    if pending:
        out["pending"] = True
    return out


async def _bloated_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None) -> dict:
    """Read-only "check bloated": list files that got bigger after transcoding.

    The scan itself (~100s, hundreds of MB) runs as a BACKGROUND task so the
    browser→app request returns well under the reverse-proxy timeout. First
    invocation kicks the scan + returns "scanning…"; a later invocation serves
    the completed list (cached for ``_BLOATED_CACHE_TTL``). Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    hid = str(host_id or "")
    now = time.time()
    st = _bloated_state.get(hid) or {}
    running = bool(st.get("running"))
    have = st.get("files") is not None and st.get("ts")
    print(f"[tdarr] INFO tdarr_bloated host={hid} running={running} "
          f"cached={'yes' if have else 'no'}")
    if have:
        age = now - st["ts"]
        fresh = age < _BLOATED_CACHE_TTL and not st.get("error")
        if fresh and not running:
            return _fmt_bloated_result(st["files"])
        if running:
            note = (f"\n\n⏳ Refreshing… (scan started "
                    f"{int(now - st.get('started', now))}s ago)")
            return _fmt_bloated_result(st["files"], note, pending=True)
        # Stale (or last scan errored) and nothing running → kick a refresh and
        # serve what we have with a note.
        _ensure_bloated_scan(base, api_key, hid)
        if st.get("error"):
            return {"ok": True, "status": 200, "pending": True,
                    "detail": (f"⚠️ Last scan errored: {st['error']}\n\n"
                               "🔍 Re-scanning now — results will appear here "
                               "automatically.")}
        return _fmt_bloated_result(
            st["files"], "\n\n⏳ Data may be stale — re-scanning…", pending=True)
    if running:
        return {"ok": True, "status": 200, "pending": True,
                "detail": (f"🔍 Scanning for bloated files… started "
                           f"{int(now - st.get('started', now))}s ago. "
                           f"{_scan_eta_phrase(hid)} — results will appear "
                           "here automatically when ready.")}
    _ensure_bloated_scan(base, api_key, hid)
    return {"ok": True, "status": 200, "pending": True,
            "detail": (f"🔍 Scanning for bloated files… {_scan_eta_phrase(hid)}. "
                       "Results will appear here automatically when ready.")}


async def _run_requeue(base: str, api_key: str, host_id: str,
                       files: Optional[list]) -> None:
    """Background requeue — scans for bloated files first when ``files`` is None
    (so a single confirmed click does the whole job), then resets each one's DB
    status to ``Queued``. Records progress on ``_bloated_state[host_id]
    ['requeue']`` and invalidates the bloated cache so the next check re-scans.
    Never raises out."""
    st = _bloated_state.setdefault(host_id, {})
    rq = st.setdefault("requeue", {})
    done = 0
    failed = 0
    last_err = ""
    try:
        async with httpx.AsyncClient(verify=False, timeout=_BLOATED_TIMEOUT,
                                     follow_redirects=True) as cli:
            if files is None:
                files = await _find_bloated(cli, base, api_key)
                st.update({"files": files, "ts": time.time(), "error": None,
                           "running": False})
            rq["total"] = len(files)
            for f in files:
                fid = f.get("_id")
                if not fid:
                    continue
                try:
                    # WRITE — a successful Tdarr cruddb update returns a 200 with
                    # a non-JSON / empty body, so parse_json=False (else every
                    # successful update misreads as "non-JSON from upstream").
                    await _cruddb(cli, base, api_key, {
                        "collection": "FileJSONDB", "mode": "update", "docID": fid,
                        "obj": {"TranscodeDecisionMaker": "Queued",
                                "HealthCheck": "Queued"}}, parse_json=False)
                    done += 1
                    rq["done"] = done
                except RuntimeError as e:
                    # Per-file update failure — track + surface (was silently
                    # swallowed, so a fleet-wide failure reported "Requeued 0"
                    # with no cause). The whole-batch error path below stays for
                    # a hard transport failure.
                    failed += 1
                    last_err = str(e)
                    rq["failed"] = failed
                    rq["last_err"] = last_err
                    print(f"[tdarr] warning: requeue update failed for {fid} — {e}")
        rq.update({"ts": time.time(), "error": None, "failed": failed,
                   "last_err": last_err})
        # Invalidate the bloated cache — the requeued files are no longer bloated.
        st["files"] = None
        st["ts"] = None
        print(f"[tdarr] INFO requeue done host={host_id} {done:,}/{rq.get('total', 0):,} "
              f"(failed={failed})")
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (RuntimeError, httpx.HTTPError, OSError) as e:  # noqa: BLE001
        rq.update({"ts": time.time(), "error": str(e)})
        print(f"[tdarr] warning: requeue scan failed host={host_id} — {e}")
    finally:
        rq["running"] = False


def _ensure_requeue(base: str, api_key: str, host_id: str,
                    files: Optional[list]) -> bool:
    """Launch a background requeue unless one is already running for this host."""
    st = _bloated_state.setdefault(host_id, {})
    rq = st.setdefault("requeue", {})
    if rq.get("running"):
        return False
    rq.update({"running": True, "started": time.time(), "done": 0,
               "total": (len(files) if files else 0), "error": None})
    _spawn(_run_requeue(base, api_key, host_id, list(files) if files else None))
    return True


async def _requeue_bloated_skill(host_row: dict, chip: dict, *,
                                 host_id: Optional[str] = None) -> dict:
    """Destructive "queue bloated": reset every bloated file's DB status to
    ``Queued`` so Tdarr re-transcodes it. Runs the scan + the per-file updates
    as a BACKGROUND task (both can exceed the reverse-proxy timeout), so the
    confirmed click returns immediately and the next invocation reports
    progress. The backend route already gated the destructive-confirm. Never
    raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    hid = str(host_id or "")
    now = time.time()
    st = _bloated_state.get(hid) or {}
    rq = st.get("requeue") or {}
    if rq.get("running"):
        done = safe_int(rq.get("done"))
        total = safe_int(rq.get("total"))
        prog = f"{done:,}/{total:,}" if total else f"{done:,}"
        out: dict = {"ok": True, "status": 200, "pending": True,
                     "detail": (f"⏳ Requeueing… {prog} done (started "
                                f"{int(now - rq.get('started', now))}s ago). This "
                                "updates here automatically.")}
        if total:
            out["progress"] = min(100, round(done / total * 100)) if total else 0
        return out
    if rq.get("ts") and (now - rq["ts"]) < 30 and not rq.get("error"):
        done = safe_int(rq.get("done"))
        total = safe_int(rq.get("total"))
        failed = safe_int(rq.get("failed"))
        last_err = str(rq.get("last_err") or "")
        if total and done == 0:
            # Every update failed — surface WHY (was a misleading "Requeued 0").
            msg = f"⚠️ Requeued 0 of {total:,} bloated file(s) — every update failed."
            if last_err:
                msg += f" Tdarr said: {last_err}."
            msg += (" Check that Tdarr isn't read-only / that the API key (if auth "
                    "is on) has write access.")
            return {"ok": False, "status": 200, "detail": msg}
        if failed:
            tail = f" ({failed:,} failed: {last_err})" if last_err else f" ({failed:,} failed)"
            return {"ok": True, "status": 200,
                    "detail": f"✅ Requeued {done:,} of {total:,} bloated file(s){tail}."}
        return {"ok": True, "status": 200,
                "detail": f"✅ Requeued {done:,} bloated file(s) for re-transcode."}
    if rq.get("ts") and (now - rq["ts"]) < 30 and rq.get("error"):
        return {"ok": True, "status": 200,
                "detail": f"⚠️ Requeue errored: {rq['error']}"}
    # Use a fresh cached bloated list when we have one (skips a re-scan);
    # otherwise the background task scans first.
    fresh = (st.get("files") is not None and st.get("ts")
             and (now - st["ts"]) < _BLOATED_CACHE_TTL and not st.get("error"))
    files = as_list(st.get("files")) if fresh else None
    if fresh and not files:
        return {"ok": True, "status": 200, "detail": "✅ No bloated files to requeue."}
    print(f"[tdarr] INFO tdarr_requeue_bloated host={hid} "
          f"(background, {'cached list' if files else 'scan-then-requeue'})")
    _ensure_requeue(base, api_key, hid, files)
    n = f"{len(files):,} " if files else ""
    return {"ok": True, "status": 200, "pending": True,
            "detail": (f"⏳ Requeueing {n}bloated file(s) in the background… progress "
                       "will update here automatically.")}


# Tdarr FileJSONDB `TranscodeDecisionMaker` values that mean a transcode did
# NOT complete cleanly — the StatisticsJSONDB `table3` ("transcode failed")
# bucket. Cancelling a worker in Tdarr lands the file here too, so this covers
# both "failed" and "cancelled" transcodes. Tuple so it's trivially extensible
# if a Tdarr build uses an additional error label.
_FAILED_STATUSES = ("Transcode error",)
# Max files requeued per failed-requeue invocation — bounds the synchronous
# operation under the per-app route budget. The failed set is normally small (a
# filtered getAll returns only error files, NOT the whole library like the
# bloated scan), so this rarely bites; a larger backlog clears over repeat runs.
_FAILED_REQUEUE_CAP = 1000


async def _find_failed(cli: httpx.AsyncClient, base: str, api_key: str) -> list:
    """Every file whose transcode FAILED / was cancelled (``TranscodeDecisionMaker``
    in ``_FAILED_STATUSES``), de-duped by ``_id``. A FILTERED ``getAll`` — Tdarr
    returns only the matching error files (small), so this is fast (unlike
    ``_find_bloated`` which walks the full library to compute ratios)."""
    raw: list = []
    for status in _FAILED_STATUSES:
        r = await _cruddb(cli, base, api_key, {
            "collection": "FileJSONDB", "mode": "getAll",
            "filters": [{"id": f"filter-{status.replace(' ', '-')}",
                         "key": "TranscodeDecisionMaker", "value": status}]})
        raw.extend(as_list(r))
    uniq = {f.get("_id"): f for f in raw if isinstance(f, dict) and f.get("_id")}
    return list(uniq.values())


async def _requeue_failed_skill(host_row: dict, chip: dict, *,
                                host_id: Optional[str] = None) -> dict:
    """Destructive "requeue failed": reset every FAILED / cancelled transcode's
    DB status back to ``Queued`` so Tdarr retries it. Synchronous (the failed set
    is small — a filtered getAll), bounded by ``_FAILED_REQUEUE_CAP`` per call.
    The backend route already gated the destructive-confirm. Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    hid = str(host_id or "")
    print(f"[tdarr] INFO tdarr_requeue_failed host={hid}")
    done = 0
    failed = 0
    last_err = ""
    try:
        async with httpx.AsyncClient(verify=False, timeout=_BLOATED_TIMEOUT,
                                     follow_redirects=True) as cli:
            files = await _find_failed(cli, base, api_key)
            total = len(files)
            if total == 0:
                return {"ok": True, "status": 200,
                        "detail": "✅ No failed transcodes to requeue."}
            capped = files[:_FAILED_REQUEUE_CAP]
            for f in capped:
                fid = f.get("_id")
                if not fid:
                    continue
                try:
                    # WRITE — a successful Tdarr cruddb update returns 200 with a
                    # non-JSON / empty body, so parse_json=False (else every
                    # successful update misreads as "non-JSON from upstream").
                    await _cruddb(cli, base, api_key, {
                        "collection": "FileJSONDB", "mode": "update", "docID": fid,
                        "obj": {"TranscodeDecisionMaker": "Queued",
                                "HealthCheck": "Queued"}}, parse_json=False)
                    done += 1
                except RuntimeError as e:
                    failed += 1
                    last_err = str(e)
                    print(f"[tdarr] warning: requeue-failed update failed for {fid} — {e}")
    except (RuntimeError, httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"⚠️ Requeue-failed errored: {type(e).__name__}: {e}"}
    print(f"[tdarr] INFO requeue-failed done host={hid} {done:,}/{total:,} (failed={failed})")
    if done == 0:
        # Every update failed — surface WHY (mirrors the bloated-requeue path).
        msg = f"⚠️ Requeued 0 of {total:,} failed transcode(s) — every update failed."
        if last_err:
            msg += f" Tdarr said: {last_err}."
        msg += (" Check that Tdarr isn't read-only / that the API key (if auth is "
                "on) has write access.")
        return {"ok": False, "status": 200, "detail": msg}
    more = total - len(capped)
    tail = (f" ({failed:,} failed: {last_err})" if failed and last_err
            else (f" ({failed:,} failed)" if failed else ""))
    extra = f" {more:,} more remain — run again to continue." if more > 0 else ""
    return {"ok": True, "status": 200,
            "detail": f"♻️ Requeued {done:,} failed transcode(s) for retry{tail}.{extra}"}


def _fmt_gb(gb: Any) -> str:
    """Render a GB figure as a human size (GB → TB at >= 1024). ``""`` for
    non-positive."""
    g = safe_float(gb)
    if g <= 0:
        return "0 GB"
    if g >= 1024:
        return f"{g / 1024:,.1f} TB"
    return f"{g:,.1f} GB"
