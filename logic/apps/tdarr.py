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

import os
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, peek_cache, resolve_base_url, resolve_cache_ttl)
from logic.coerce import as_dict, as_list, safe_float, safe_int

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

# 1 GiB in bytes (sizeDiff is reported in GB by Tdarr; we render GB → TB).
_GIB = 1024 ** 3

# A file is "bloated" when its transcode produced a LARGER file — Tdarr stores
# this as newVsOldRatio (a percentage; 100 = same size, >100 = bigger).
_BLOAT_RATIO = 100.0


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


async def _cruddb(cli: httpx.AsyncClient, base: str, api_key: str, data: dict) -> Any:
    """One ``POST /api/v2/cruddb`` with the ``{"data": <data>}`` envelope.
    Raises ``RuntimeError`` on transport / auth / non-200 / non-JSON."""
    try:
        r = await cli.post(base.rstrip("/") + _API + "/cruddb",
                           headers=_headers(api_key), json={"data": data})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"request failed: {type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError("auth failed: Tdarr requires an API key (set it in the editor)")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for cruddb")
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
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            stats = _stats_doc(await _cruddb(cli, base, api_key, {
                "collection": "StatisticsJSONDB", "mode": "getById",
                "docID": "statistics", "obj": {}}))
            version = ""
            try:
                version = str(as_dict(await _get(cli, base, api_key, "/status")).get("version") or "").strip()
            except RuntimeError:
                version = ""
            workers_active, nodes = 0, 0
            try:
                workers_active, nodes = _count_workers(await _get(cli, base, api_key, "/get-nodes"))
            except RuntimeError:
                workers_active, nodes = 0, 0
    except RuntimeError as e:
        print(f"[tdarr] error: fetch host={host_id} — {e}")
        raise RuntimeError(str(e))
    out: dict[str, Any] = {
        "available": True,
        "total_files": safe_int(stats.get("totalFileCount")),
        "transcodes": safe_int(stats.get("totalTranscodeCount")),
        "transcode_queue": safe_int(stats.get("table1Count")),
        "health_queue": safe_int(stats.get("table4Count")),
        "space_saved_gb": round(safe_float(stats.get("sizeDiff")), 1),
        "workers_active": workers_active,
        "nodes": nodes,
        "version": version,
        "fetched_at": int(now),
    }
    print(f"[tdarr] INFO fetched host={host_id} files={out['total_files']} "
          f"tq={out['transcode_queue']} hq={out['health_queue']} "
          f"saved={out['space_saved_gb']}GB workers={workers_active}/{nodes}")
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
    return {"ok": True, "status": 200, "detail": body}


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


async def _bloated_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None) -> dict:
    """Read-only "check bloated": list files that got bigger after transcoding.
    Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[tdarr] INFO tdarr_bloated host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0,
                                     follow_redirects=True) as cli:
            bloated = await _find_bloated(cli, base, api_key)
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    if not bloated:
        return {"ok": True, "status": 200, "detail": "✅ No bloated files found."}
    lines = [f"• {os.path.basename(str(f.get('file') or '?'))}  "
             f"{safe_float(f.get('newVsOldRatio')):.1f}%"
             for f in bloated[:25]]
    more = f"\n…and {len(bloated) - 25:,} more" if len(bloated) > 25 else ""
    detail = (f"🐘 {len(bloated):,} bloated file(s) (larger after transcode):\n"
              + "\n".join(lines) + more
              + "\n\nUse \"Requeue bloated files\" to re-transcode them.")
    return {"ok": True, "status": 200, "detail": detail,
            "count": len(bloated), "count_i18n": "apps.tdarr.bloated_count"}


async def _requeue_bloated_skill(host_row: dict, chip: dict, *,
                                 host_id: Optional[str] = None) -> dict:
    """Destructive "queue bloated": reset every bloated file's DB status to
    ``Queued`` so Tdarr re-transcodes it. The backend route already gated the
    destructive-confirm. Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[tdarr] INFO tdarr_requeue_bloated host={host_id} (live)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=60.0,
                                     follow_redirects=True) as cli:
            bloated = await _find_bloated(cli, base, api_key)
            if not bloated:
                return {"ok": True, "status": 200, "detail": "✅ No bloated files to requeue."}
            total = len(bloated)
            done = 0
            for f in bloated:
                fid = f.get("_id")
                if not fid:
                    continue
                try:
                    await _cruddb(cli, base, api_key, {
                        "collection": "FileJSONDB", "mode": "update", "docID": fid,
                        "obj": {"TranscodeDecisionMaker": "Queued", "HealthCheck": "Queued"}})
                    done += 1
                except RuntimeError as e:
                    print(f"[tdarr] warning: requeue failed for {fid} — {e}")
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    if done == total:
        return {"ok": True, "status": 200,
                "detail": f"✅ Requeued {done:,} bloated file(s) for re-transcode."}
    return {"ok": True, "status": 200,
            "detail": f"⚠️ Requeued {done:,} of {total:,} bloated file(s); "
                      f"the rest failed (check Admin → Logs)."}


def _fmt_gb(gb: Any) -> str:
    """Render a GB figure as a human size (GB → TB at >= 1024). ``""`` for
    non-positive."""
    g = safe_float(gb)
    if g <= 0:
        return "0 GB"
    if g >= 1024:
        return f"{g / 1024:,.1f} TB"
    return f"{g:,.1f} GB"
