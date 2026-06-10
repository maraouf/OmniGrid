"""GitSync Connector per-app module.

GitSync Connector (a Forgejo → GitHub / GCSR repo mirror + issue / wiki / release
sync service) exposes a small bearer-token REST API at ``<base>/api/v1``. This
module wires it into the OmniGrid Apps surface following the per-app contract
(``grafana.py`` / ``forgejo.py`` shape):

    SLUGS               — catalog slug this module handles ("gitsync").
    requires_api_key()  — True (the chip's ``api_key`` stores a GitSync API
                          token — GitSync UI → API → "Create token". The token
                          looks like ``gsc_…`` and grants full read + control
                          access; treat it like a password.)
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + pairs (read, rich list) + sync-all /
                          pause-all / unpause-all (fleet control) + per-pair
                          sync / pause / unpause (arg). The PAUSE skills are
                          DESTRUCTIVE (they halt syncing — reversible but
                          impactful, so the confirm gate applies); sync / unpause
                          are non-destructive.

The expanded card answers "is my repo mirroring healthy right now":

    pairs / enabled / paused     — pair counts
    issues / commits / releases  — fleet-wide mapping counts
    refs                         — tracked synced refs
    alerts (error/warn/info)     — unacknowledged alert counts
    version + last sync          — connector version + most-recent sync finish

Auth model: a bearer token sent on the ``Authorization: Bearer <token>`` header.
The token is the secret and lives in the chip's ``api_key`` field. The credential
probe hits the auth-required ``/api/v1/metrics`` so a bad / missing token fails
loudly (401). Single-instance app (NOT fleet — the connector's own sync PAIRS are
its internal Forgejo→destination pairs, not OmniGrid instances). No image proxy.

Upstream API reference: GitSync Connector "API Integration Guide" — bearer
``gsc_…`` token, ``/api/v1`` prefix, ``GET /metrics`` + ``GET /pairs`` +
``POST /pairs/{id}/{pause,unpause,sync}`` + ``POST /{pause,unpause}-all``.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl,
    resolve_credential_target)
from logic.coerce import as_dict, as_list, safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("gitsync",)

# GitSync Connector REST API base path (the version marker).
_API = "/api/v1"

# Per-(host_id, service_idx) data cache for the expanded card. Default TTL
# overridable per chip via the editor's `cache_ttl` field. 60s — the connector
# metrics endpoint hits its DB on every call, so a longer cache keeps it light.
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# Cap on the rich-item rows a list skill returns.
_MAX_ROWS = 20

# Sync-run / side-test status → emoji for the rich pair rows + the AI text.
_STATUS_EMOJI = {"ok": "✅", "error": "❌", "skipped": "⏭️", "fail": "❌"}

# GitSync skills — two read-only + three fleet-control + three per-pair (arg).
# The arg-carrying per-pair skills surface to AI / Telegram only (a drawer
# button can't supply the pair name); the rest also render as one-click buttons.
SKILLS: tuple[dict, ...] = (
    {
        "id": "gitsync_status",
        "name": "GitSync status",
        "ai_phrases": ("gitsync status, repo sync status, mirror status, is my "
                       "repo syncing, how many sync pairs, gitsync overview, "
                       "gitsync summary, are there sync alerts, gitsync version"),
        "destructive": False,
    },
    {
        "id": "gitsync_pairs",
        "name": "List sync pairs",
        "ai_phrases": ("list gitsync pairs, what repos are syncing, show sync "
                       "pairs, which pairs are paused, my mirror pairs, repo sync "
                       "pairs, gitsync pair status"),
        "destructive": False,
    },
    {
        "id": "gitsync_sync_all",
        "name": "Sync all pairs now",
        "ai_phrases": ("sync all gitsync pairs, sync everything now, trigger a "
                       "sync, mirror all repos now, run gitsync, push all syncs, "
                       "sync now"),
        "destructive": False,
    },
    {
        "id": "gitsync_pause_all",
        "name": "Pause all syncing",
        "ai_phrases": ("pause all gitsync, pause all syncing, halt the mirror, "
                       "stop syncing, pause every pair, freeze gitsync"),
        "destructive": True,
    },
    {
        "id": "gitsync_unpause_all",
        "name": "Resume all syncing",
        "ai_phrases": ("unpause all gitsync, resume syncing, unpause every pair, "
                       "resume the mirror, re-enable syncing, continue gitsync"),
        "destructive": False,
    },
    {
        "id": "gitsync_sync",
        "name": "Sync one pair now",
        "ai_phrases": ("sync the <name> pair, sync <name> now, trigger a sync for "
                       "<name>, mirror <name> now, run gitsync for <name>"),
        "arg": True,
        "arg_hint": "the sync pair name (usually the repo name) to sync",
        "destructive": False,
    },
    {
        "id": "gitsync_pause",
        "name": "Pause one pair",
        "ai_phrases": ("pause the <name> pair, pause syncing for <name>, halt "
                       "<name>, stop syncing <name>, freeze the <name> mirror"),
        "arg": True,
        "arg_hint": "the sync pair name to pause",
        "destructive": True,
    },
    {
        "id": "gitsync_unpause",
        "name": "Resume one pair",
        "ai_phrases": ("unpause the <name> pair, resume syncing <name>, unpause "
                       "<name>, re-enable <name>, continue the <name> mirror"),
        "arg": True,
        "arg_hint": "the sync pair name to resume",
        "destructive": False,
    },
)


def requires_api_key() -> bool:
    """GitSync authenticates every /api/v1 endpoint via a bearer token; the
    editor MUST render the token input (stored in the chip's api_key) + Test."""
    return True


def _headers(token: str) -> dict:
    """GitSync bearer auth header + JSON Accept."""
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


# noinspection DuplicatedCode
async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe GitSync's auth-required ``GET /api/v1/metrics`` with the supplied
    token. Returns ``{ok, detail, status}``. Falls back to the chip's stored
    ``api_key`` when ``candidate_key`` is blank so the operator can re-test after
    first save without retyping."""
    token, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + _API + "/metrics"
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        try:
            body = as_dict(r.json())
        except (ValueError, TypeError):
            body = {}
        ver = str(body.get("version") or "").strip()
        pairs = safe_int(as_dict(body.get("totals")).get("pairs"))
        bits = []
        if ver:
            bits.append(f"GitSync {ver}")
        bits.append(f"{pairs} pair(s)")
        return {"ok": True, "detail": "OK (" + ", ".join(bits) + ")", "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check the GitSync token)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


def _shape_pair_rows(pairs_raw: list) -> list[dict]:
    """Reduce the verbose ``metrics.pairs[]`` objects to the per-pair card / skill
    shape: ``{name, enabled, paused, configured, last_status}``."""
    rows: list[dict] = []
    for p in pairs_raw:
        if not isinstance(p, dict):
            continue
        last = as_dict(p.get("last_run"))
        name = str(p.get("name") or "").strip() or f"#{safe_int(p.get('id'))}"
        rows.append({
            "name": name,
            "enabled": bool(p.get("enabled")),
            "paused": bool(p.get("paused")),
            "configured": bool(p.get("configured")),
            "last_status": str(last.get("status") or "").strip(),
        })
    return rows


# noinspection DuplicatedCode
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch the GitSync fleet summary for the card from ``GET /api/v1/metrics``.

    Returns ``{available, version, pairs, enabled, paused, issue_mappings,
    commit_mappings, release_mappings, comment_mappings, synced_refs,
    alerts_error, alerts_warn, alerts_info, last_sync_at, pair_rows,
    fetched_at}``. Raises ``ValueError`` / ``RuntimeError`` (caller maps to
    HTTPException) when the token is unset / the base URL won't resolve / the
    ``/metrics`` call errors."""
    token = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=token, log_tag="gitsync")
    if hit is not None:
        return hit
    url = base + _API + "/metrics"
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(token))
            if r.status_code != 200:
                print(f"[gitsync] error: fetch host={host_id} url={r.request.url} "
                      f"returned HTTP {r.status_code} (check the chip URL points at "
                      f"the GitSync root, e.g. http://gitsync.example.com:8020)")
                if r.status_code in (401, 403):
                    raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                                       f"(check the GitSync token) — {url}")
                raise RuntimeError(f"upstream returned HTTP {r.status_code} for {url}")
            body = as_dict(r.json())
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[gitsync] error: fetch host={host_id} url={url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    totals = as_dict(body.get("totals"))
    alerts = as_dict(totals.get("alerts_unacknowledged"))
    pair_rows = _shape_pair_rows(as_list(body.get("pairs")))
    out: dict[str, Any] = {
        "available": True,
        "version": str(body.get("version") or "").strip(),
        "pairs": safe_int(totals.get("pairs")),
        "enabled": safe_int(totals.get("enabled")),
        "paused": safe_int(totals.get("paused")),
        "issue_mappings": safe_int(totals.get("issue_mappings")),
        "commit_mappings": safe_int(totals.get("commit_mappings")),
        "release_mappings": safe_int(totals.get("release_mappings")),
        "comment_mappings": safe_int(totals.get("comment_mappings")),
        "synced_refs": safe_int(totals.get("synced_refs")),
        "alerts_error": safe_int(alerts.get("error")),
        "alerts_warn": safe_int(alerts.get("warn")),
        "alerts_info": safe_int(alerts.get("info")),
        "last_sync_at": str(totals.get("last_sync_at") or "").strip(),
        "pair_rows": pair_rows,
        "fetched_at": int(now),
    }
    print(f"[gitsync] INFO fetched host={host_id} pairs={out['pairs']} "
          f"enabled={out['enabled']} paused={out['paused']} "
          f"issues={out['issue_mappings']} commits={out['commit_mappings']} "
          f"refs={out['synced_refs']} alerts="
          f"{out['alerts_error']}/{out['alerts_warn']}/{out['alerts_info']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "pairs": safe_int(data.get("pairs")),
        "enabled": safe_int(data.get("enabled")),
        "paused": safe_int(data.get("paused")),
        "issue_mappings": safe_int(data.get("issue_mappings")),
        "commit_mappings": safe_int(data.get("commit_mappings")),
        "synced_refs": safe_int(data.get("synced_refs")),
        "alerts_error": safe_int(data.get("alerts_error")),
        "version": data.get("version") or "",
        "last_sync_at": data.get("last_sync_at") or "",
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
    carries the free-form pair name for the per-pair sync / pause / unpause
    skills."""
    if skill_id == "gitsync_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "gitsync_pairs":
        return await _pairs_skill(host_row, chip, host_id=host_id,
                                  service_idx=service_idx)
    if skill_id == "gitsync_sync_all":
        return await _sync_all_skill(host_row, chip, host_id=host_id)
    if skill_id == "gitsync_pause_all":
        return await _pause_all_skill(host_row, chip, host_id=host_id, pause=True)
    if skill_id == "gitsync_unpause_all":
        return await _pause_all_skill(host_row, chip, host_id=host_id, pause=False)
    if skill_id == "gitsync_sync":
        return await _pair_action_skill(host_row, chip, arg=arg, action="sync",
                                        host_id=host_id)
    if skill_id == "gitsync_pause":
        return await _pair_action_skill(host_row, chip, arg=arg, action="pause",
                                        host_id=host_id)
    if skill_id == "gitsync_unpause":
        return await _pair_action_skill(host_row, chip, arg=arg, action="unpause",
                                        host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _resolve_skill_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(token, base)`` or a ready ``{ok: False, detail}`` error dict for
    a GitSync skill."""
    token = (chip.get("api_key") or "").strip()
    if not token:
        return "", "", {"ok": False, "status": 0, "detail": "GitSync token not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return token, base, None


def _status_guard(r: "httpx.Response") -> Optional[dict]:
    """Shared 401 / 403 + non-2xx guard for a GitSync read / control skill.
    Returns a ready error dict, or None when the response is a 2xx."""
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check the GitSync token)"}
    if not (200 <= r.status_code < 300):
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    return None


# noinspection DuplicatedCode
def _items_and_lines(rows: list, row_fn) -> "tuple[list[dict], list[str]]":
    """Map the first ``_MAX_ROWS`` raw rows through ``row_fn`` into the rich-item
    list + the matching ``• title (subtitle)`` text lines, skipping rejected
    rows."""
    items: list[dict] = []
    for raw in rows[:_MAX_ROWS]:
        row = row_fn(raw)
        if row:
            items.append(row)
    lines = [f"• {it['title']}" + (f"  ({it['subtitle']})" if it.get("subtitle") else "")
             for it in items]
    return items, lines


def _attach_items(out: dict, items: list[dict], count_i18n: str) -> dict:
    """Attach the rich-item list + count + count-i18n key to a skill result dict
    (no-op when there are no items). Returns ``out`` for one-line use."""
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = count_i18n
    return out


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the fleet summary (force-bypasses the cache) and
    return a formatted ``detail``. Never raises."""
    print(f"[gitsync] INFO gitsync_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[gitsync] warning: gitsync_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    pairs = safe_int(data.get("pairs"))
    enabled = safe_int(data.get("enabled"))
    paused = safe_int(data.get("paused"))
    issues = safe_int(data.get("issue_mappings"))
    commits = safe_int(data.get("commit_mappings"))
    releases = safe_int(data.get("release_mappings"))
    refs = safe_int(data.get("synced_refs"))
    a_err = safe_int(data.get("alerts_error"))
    a_warn = safe_int(data.get("alerts_warn"))
    a_info = safe_int(data.get("alerts_info"))
    lines = [f"🔗 Pairs: {pairs} ({enabled} enabled, {paused} paused)",
             f"🐛 Issues: {issues:,} · 📦 Commits: {commits:,} · 🏷️ Releases: {releases:,}",
             f"🔀 Synced refs: {refs:,}"]
    if a_err or a_warn or a_info:
        lines.append(f"🚨 Alerts: {a_err} error · {a_warn} warn · {a_info} info")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "pairs": pairs, "paused": paused, "alerts_error": a_err,
    }


def _pair_row(p: dict) -> Optional[dict]:
    """One sync pair as a rich skill-result item: the pair name + a state /
    last-run-status subtitle. No poster — GitSync has no thumbnail surface."""
    if not isinstance(p, dict):
        return None
    name = str(p.get("name") or "").strip()
    if not name:
        return None
    if p.get("paused"):
        state = "⏸️ paused"
    elif not p.get("enabled"):
        state = "⏹️ disabled"
    else:
        state = "▶️ active"
    bits = [state]
    if not p.get("configured"):
        bits.append("⚠️ not configured")
    ls = str(p.get("last_status") or "").strip()
    if ls:
        bits.append(_STATUS_EMOJI.get(ls, ls) + " last run")
    return {"title": name, "subtitle": " · ".join(bits)}


# noinspection DuplicatedCode
async def _pairs_skill(host_row: dict, chip: dict, *,
                       host_id: Optional[str] = None,
                       service_idx: Optional[int] = None) -> dict:
    """Read-only: list the configured sync pairs with per-pair state + last-run
    status as rich rows. Never raises."""
    print(f"[gitsync] INFO gitsync_pairs host={host_id} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[gitsync] warning: gitsync_pairs host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    rows = as_list(data.get("pair_rows"))
    if not rows:
        return {"ok": True, "status": 200, "detail": "🔗 No sync pairs configured."}
    items, lines = _items_and_lines(rows, _pair_row)
    out: dict = {"ok": True, "status": 200,
                 "detail": "🔗 Sync pairs:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.gitsync.pairs_count")


async def _sync_all_skill(host_row: dict, chip: dict, *,
                          host_id: Optional[str] = None) -> dict:
    """Action: trigger a sync (``kind=all``) on EVERY configured pair. GitSync
    has no fleet-wide sync endpoint, so this lists the pairs and POSTs
    ``/pairs/{id}/sync`` for each. Non-destructive (the connector de-duplicates
    in-flight runs + gates paused / unconfigured pairs). Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[gitsync] INFO gitsync_sync_all host={host_id}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            pr = await cli.get(base + _API + "/pairs", headers=_headers(token))
            guard = _status_guard(pr)
            if guard:
                return guard
            pairs = as_list(as_dict(pr.json()).get("pairs"))
            if not pairs:
                return {"ok": True, "status": 200, "detail": "🔗 No sync pairs configured."}
            triggered = 0
            names: list[str] = []
            for p in pairs:
                if not isinstance(p, dict):
                    continue
                pid = safe_int(p.get("id"))
                if not pid:
                    continue
                sr = await cli.post(base + _API + f"/pairs/{pid}/sync",
                                    headers=_headers(token), params={"kind": "all"})
                if 200 <= sr.status_code < 300:
                    triggered += 1
                    nm = str(p.get("name") or "").strip()
                    if nm:
                        names.append(nm)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"sync failed: {type(e).__name__}: {e}"}
    if not triggered:
        return {"ok": False, "status": 502, "detail": "no pairs accepted the sync trigger"}
    suffix = (" (" + ", ".join(names[:6]) + ")") if names else ""
    return {"ok": True, "status": 200,
            "detail": f"🔁 Triggered a sync on {triggered} pair(s){suffix}. Syncs run "
                      f"asynchronously — check status again in a moment."}


async def _pause_all_skill(host_row: dict, chip: dict, *, pause: bool,
                           host_id: Optional[str] = None) -> dict:
    """Action: pause / unpause EVERY pair via ``POST /api/v1/{pause,unpause}-all``.
    Idempotent on the connector side. Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    path = "/pause-all" if pause else "/unpause-all"
    verb = "pause" if pause else "unpause"
    print(f"[gitsync] INFO gitsync_{verb}_all host={host_id}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.post(base + _API + path, headers=_headers(token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"{verb} failed: {type(e).__name__}: {e}"}
    guard = _status_guard(r)
    if guard:
        return guard
    try:
        body = as_dict(r.json())
    except (ValueError, TypeError):
        body = {}
    total = safe_int(body.get("total"))
    changed = len(as_list(body.get("changed")))
    icon = "⏸️" if pause else "▶️"
    state = "Paused" if pause else "Resumed"
    return {"ok": True, "status": 200,
            "detail": f"{icon} {state} syncing on {changed} of {total} pair(s)."}


async def _resolve_pair(cli: httpx.AsyncClient, base: str, token: str,
                        name: str) -> "tuple[Optional[dict], Optional[dict]]":
    """Resolve a pair NAME (or numeric id) to its ``pairs[]`` object via
    ``GET /api/v1/pairs``. Matches exact name (case-insensitive), then numeric
    id, then name substring. Returns ``(pair, None)`` or ``(None, error_dict)``
    where the error lists the available pair names."""
    pr = await cli.get(base + _API + "/pairs", headers=_headers(token))
    guard = _status_guard(pr)
    if guard:
        return None, guard
    pairs = as_list(as_dict(pr.json()).get("pairs"))
    want = (name or "").strip().lower()
    for p in pairs:
        if isinstance(p, dict) and str(p.get("name") or "").strip().lower() == want:
            return p, None
    if want.isdigit():
        for p in pairs:
            if isinstance(p, dict) and safe_int(p.get("id")) == int(want):
                return p, None
    for p in pairs:
        if isinstance(p, dict) and want and want in str(p.get("name") or "").strip().lower():
            return p, None
    avail = ", ".join(str(p.get("name") or "").strip()
                      for p in pairs if isinstance(p, dict) and str(p.get("name") or "").strip())
    return None, {"ok": False, "status": 404,
                  "detail": f"no sync pair named “{name}” — available: {avail or '(none)'}"}


async def _pair_action_skill(host_row: dict, chip: dict, *, arg: Optional[str],
                             action: str, host_id: Optional[str] = None) -> dict:
    """Action (arg): resolve a pair by name then ``sync`` / ``pause`` / ``unpause``
    just that one pair. ``action`` is one of ``"sync"`` / ``"pause"`` /
    ``"unpause"``. Never raises."""
    name = (arg or "").strip()
    if not name:
        return {"ok": False, "status": 0,
                "detail": f"no pair name given — say e.g. '{action} the OmniGrid pair'"}
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[gitsync] INFO gitsync_{action} host={host_id} pair={name!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            pair, perr = await _resolve_pair(cli, base, token, name)
            if perr:
                return perr
            assert pair is not None  # _resolve_pair returns (pair, None) here
            pid = safe_int(pair.get("id"))
            pname = str(pair.get("name") or "").strip() or f"#{pid}"
            if action == "sync":
                r = await cli.post(base + _API + f"/pairs/{pid}/sync",
                                   headers=_headers(token), params={"kind": "all"})
            else:
                r = await cli.post(base + _API + f"/pairs/{pid}/{action}",
                                   headers=_headers(token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"{action} failed: {type(e).__name__}: {e}"}
    guard = _status_guard(r)
    if guard:
        return guard
    try:
        body = as_dict(r.json())
    except (ValueError, TypeError):
        body = {}
    if action == "sync":
        return {"ok": True, "status": 200,
                "detail": f"🔁 Triggered a sync on “{pname}”. It runs asynchronously "
                          f"— check status again in a moment."}
    changed = bool(body.get("changed"))
    icon = "⏸️" if action == "pause" else "▶️"
    state = "paused" if action == "pause" else "resumed"
    note = "" if changed else " (already in that state)"
    return {"ok": True, "status": 200, "detail": f"{icon} “{pname}” {state}{note}."}
