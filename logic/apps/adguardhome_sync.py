"""AdGuard Home Sync per-app module (bakito/adguardhome-sync).

Encapsulates everything sync-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
per-app contract documented in ``logic/apps/registry.py``:

    SLUGS               — catalog slugs this module handles.
    requires_api_key()  — False (the sync API's HTTP Basic auth is
                          OPTIONAL; the card + skills work against an
                          unauthenticated instance too).
    resolve_base_url(host_row, chip) -> str        (shared helper)
    test_credential(host_row, chip, candidate_key, *, payload) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + sync-now (action) +
                          logs (read) + clear-logs (action).

What this is
------------
adguardhome-sync keeps a PRIMARY AdGuard Home instance's config in sync
with one or more REPLICAS. The expanded card answers "is the sync
healthy, and are all the replicas in sync" at a glance:

    sync_running     — a sync is in progress right now
    origin_status    — the primary's last sync status ("success" = OK)
    origin_ok        — origin_status == "success"
    replicas_total   — number of configured replicas
    replicas_ok      — replicas whose status == "success"
    replicas_failed  — replicas that aren't "success"
    failed_names     — host names of the failing replicas

Auth model
----------
The sync API (default port 8080) applies HTTP Basic auth ONLY when
``api.username`` + ``api.password`` are configured upstream — otherwise
it's open. So auth is OPTIONAL here: the username lives in the plain
``username`` chip field, the password in the secret ``api_key`` chip
field (keep-current / ``_set`` plumbing applies). When BOTH are blank the
probe runs unauthenticated. Single-instance app (NOT fleet) — one card
per pinned chip.

Endpoints used (adguardhome-sync API):
    GET  /api/v1/status      — sync status (origin + replicas) [card + test]
    POST /api/v1/sync        — trigger a sync now
    GET  /api/v1/logs        — recent log entries (plain text)
    POST /api/v1/clear-logs  — clear the in-memory log buffer
    GET  /healthz            — health check (no auth)

API reference: https://github.com/bakito/adguardhome-sync
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

import httpx

# The sync API exposes NO version field (status / metrics carry none — the
# version lives ONLY in the root web-UI page, rendered as
# ``<p class="h6 text-muted mb-0">{{ .Version }} ({{ .Build }})</p>``). We
# best-effort scrape it from ``GET /`` so the card shows a version like every
# other app; a scrape miss just drops the version line (never load-bearing).
_VERSION_RE = re.compile(r'class="h6 text-muted mb-0"\s*>\s*(?P<version>[^<(]+?)\s*\(', re.I)

from logic.apps._common import (
    cache_key, fetch_preamble, peek_cache, resolve_base_url, resolve_cache_ttl)
from logic.coerce import safe_int

# Catalog template slug + brand-variant aliases (operator-edited chips
# that kept the brand but dropped the catalog link).
SLUGS: tuple[str, ...] = ("adguardhome-sync", "adguard-home-sync",
                          "adguardhomesync")

# Read-only status + three action / read skills. All no-arg, so each
# surfaces as a one-click drawer button AND an AI / Telegram action.
# ``adguardsync_sync`` triggers a sync (non-destructive — it pushes the
# primary's config to the replicas, which is the tool's whole job);
# ``adguardsync_clear_logs`` clears the in-memory log buffer (low-stakes,
# not typed-confirm-gated).
SKILLS: tuple[dict, ...] = (
    {
        "id": "adguardsync_status",
        "name": "AdGuard sync status",
        "ai_phrases": ("adguard sync status, is adguard sync working, "
                       "are the adguard replicas in sync, adguardhome sync "
                       "status, dns config sync status, sync health"),
        "destructive": False,
    },
    {
        "id": "adguardsync_sync",
        "name": "Sync now",
        "ai_phrases": ("sync adguard now, run adguard sync, trigger adguard "
                       "sync, sync the dns config now, push adguard config to "
                       "replicas, force an adguard sync"),
        "destructive": False,
    },
    {
        "id": "adguardsync_logs",
        "name": "Recent sync logs",
        "ai_phrases": ("adguard sync logs, recent sync log, why did the sync "
                       "fail, show adguardhome sync log, last sync output"),
        "destructive": False,
    },
    {
        "id": "adguardsync_clear_logs",
        "name": "Clear sync logs",
        "ai_phrases": ("clear adguard sync logs, reset the sync log, wipe the "
                       "sync log buffer, clear adguardhome sync log"),
        "destructive": False,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. Default TTL
# overridable per chip via the editor's `cache_ttl` field. 30s default —
# the status call is cheap and the sync state changes on each sync run.
DEFAULT_CACHE_TTL_S = 30
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """False — the sync API's Basic auth is OPTIONAL, so the card + skills
    work against an unauthenticated instance. The editor still offers the
    username + password inputs for instances that DO require auth."""
    return False


def _auth(chip: dict, *, password: Optional[str] = None,
          username: Optional[str] = None) -> Optional[httpx.BasicAuth]:
    """Resolve an ``httpx.BasicAuth`` for a chip, or ``None`` when neither a
    username nor a password is set (the sync API is open in that case).
    Explicit args win (a pre-save test passes candidate values); else fall
    back to the stored chip fields."""
    u = (username if username is not None else "").strip() or (chip.get("username") or "").strip()
    p = (password if password is not None else "").strip() or (chip.get("api_key") or "").strip()
    if u or p:
        return httpx.BasicAuth(u, p)
    return None


def _status_ok(status: Any) -> bool:
    """An instance's sync is healthy when its status is ``"success"`` (the
    same ground truth the sync API's ``/healthz`` uses)."""
    return str(status or "").strip().lower() == "success"


def _shape_status(body: Any) -> dict:
    """Shape a ``/api/v1/status`` payload into the card's fields. Defensive
    over every key (a malformed body yields zeros, never raises)."""
    body = body if isinstance(body, dict) else {}
    _origin = body.get("origin")
    origin = _origin if isinstance(_origin, dict) else {}
    _replicas = body.get("replicas")
    replicas = _replicas if isinstance(_replicas, list) else []

    rep_total = 0
    rep_ok = 0
    failed_names: list[str] = []
    for r in replicas:
        if not isinstance(r, dict):
            continue
        rep_total += 1
        if _status_ok(r.get("status")):
            rep_ok += 1
        else:
            name = str(r.get("host") or r.get("url") or "?").strip()
            if name:
                failed_names.append(name)
    return {
        "sync_running": bool(body.get("syncRunning")),
        "origin_status": str(origin.get("status") or "").strip(),
        "origin_ok": _status_ok(origin.get("status")),
        "origin_host": str(origin.get("host") or origin.get("url") or "").strip(),
        "origin_protection": bool(origin.get("protection_enabled")) if origin.get("protection_enabled") is not None else None,
        "replicas_total": rep_total,
        "replicas_ok": rep_ok,
        "replicas_failed": rep_total - rep_ok,
        "failed_names": failed_names[:8],
    }


def _scrape_version(html: Any) -> str:
    """Best-effort: pull the app version out of the root web-UI page (the
    only place the sync tool exposes it). Returns ``""`` on any miss — the
    version line is never load-bearing."""
    if not isinstance(html, str) or not html:
        return ""
    m = _VERSION_RE.search(html)
    if not m:
        return ""
    return (m.group("version") or "").strip()[:40]


async def _fetch_version(cli: httpx.AsyncClient, base: str) -> str:
    """Best-effort version scrape from ``GET /`` on an already-open client.
    ``""`` on any failure (auth / non-200 / no match) — never raises."""
    try:
        rr = await cli.get(base + "/")
    except (httpx.HTTPError, OSError):
        return ""
    if getattr(rr, "status_code", 0) != 200:
        return ""
    try:
        return _scrape_version(rr.text)
    except (ValueError, TypeError):
        return ""


async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe ``GET /api/v1/status`` with the candidate Basic-auth creds (or
    unauthenticated when none are set). ``candidate_key`` is the password;
    the username comes from the test payload (pre-save) or the stored chip.
    Returns ``{ok, detail, status}`` for direct SPA consumption."""
    pay = payload or {}
    auth = _auth(chip,
                 password=(candidate_key or "").strip() or None,
                 username=(pay.get("username") or "").strip() or None)
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    url = base + "/api/v1/status"
    try:
        # follow_redirects=True: a reverse proxy in front of the sync UI may
        # 307/308 (http->https / trailing-slash); without following, that
        # surfaces as a bare "HTTP 307".
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True, auth=auth) as cli:
            r = await cli.get(url, headers={"Accept": "application/json"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[adguardsync] warning: test-connection {url} failed — {type(e).__name__}: {e}")
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    redirects = " <- ".join(str(h.url) for h in r.history) if r.history else ""
    print(f"[adguardsync] INFO test-connection url={url} -> HTTP {r.status_code} "
          f"final={r.url}{(' via ' + redirects) if redirects else ''}")
    if r.status_code == 200:
        try:
            shaped = _shape_status(r.json())
        except (ValueError, TypeError):
            shaped = {}
        n = safe_int(shaped.get("replicas_total"))
        return {"ok": True,
                "detail": f"OK ({n} replica{'s' if n != 1 else ''})",
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check username / password)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch the sync status for the expanded card.

    Returns ``{available, sync_running, origin_status, origin_ok,
    replicas_total, replicas_ok, replicas_failed, failed_names,
    origin_protection, fetched_at}``. Raises ``ValueError`` (base URL won't
    resolve) / ``RuntimeError`` (upstream error) — the caller maps to an
    HTTPException; the SPA card shows the matching error branch."""
    now = time.time()
    base, hit = fetch_preamble(host_row, chip, host_id, service_idx, _data_cache,
                               resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force)
    if hit is not None:
        return hit
    auth = _auth(chip)
    url = base + "/api/v1/status"
    print(f"[adguardsync] INFO fetch host={host_id} svc_idx={service_idx} url={url}")
    version = ""
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True, auth=auth) as cli:
            r = await cli.get(url, headers={"Accept": "application/json"})
            # Best-effort version scrape from the root web-UI page (the only
            # place the sync tool exposes it) — reuse the open client; a miss
            # just drops the version line.
            if getattr(r, "status_code", 0) == 200:
                version = await _fetch_version(cli, base)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[adguardsync] error: fetch host={host_id} url={url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                           f"(check username / password) — {url}")
    if r.status_code != 200:
        print(f"[adguardsync] error: fetch host={host_id} url={r.request.url} "
              f"returned HTTP {r.status_code} (check the chip URL points at the "
              f"sync UI root, e.g. https://adguardhome-sync.example.com:8080)")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {url}")
    try:
        body = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    shaped = _shape_status(body)
    out: dict[str, Any] = {"available": True, "fetched_at": int(now),
                           "version": version, **shaped}
    print(f"[adguardsync] INFO fetched host={host_id} running={out['sync_running']} "
          f"origin_ok={out['origin_ok']} replicas_ok={out['replicas_ok']}/"
          f"{out['replicas_total']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``. Returns the last fetched status or ``None``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "sync_running": bool(data.get("sync_running")),
        "origin_ok": bool(data.get("origin_ok")),
        "origin_status": data.get("origin_status") or "",
        "replicas_ok": safe_int(data.get("replicas_ok")),
        "replicas_total": safe_int(data.get("replicas_total")),
        "replicas_failed": safe_int(data.get("replicas_failed")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def _resolve_target(host_row: dict, chip: dict) -> "tuple[str, Optional[httpx.BasicAuth], Optional[dict]]":
    """Resolve ``(base_url, auth)`` for an action / read skill, or a ready
    ``{ok: False, detail}`` when the URL won't resolve. Auth is ``None`` when
    the instance is unauthenticated (the common case)."""
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", None, {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return base, _auth(chip), None


async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404)."""
    if skill_id == "adguardsync_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "adguardsync_sync":
        return await _sync_skill(host_row, chip, host_id=host_id)
    if skill_id == "adguardsync_logs":
        return await _logs_skill(host_row, chip, host_id=host_id)
    if skill_id == "adguardsync_clear_logs":
        return await _clear_logs_skill(host_row, chip, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


# noinspection DuplicatedCode
# The live-fetch-then-format shape (print + try/fetch_data force=True +
# ValueError/RuntimeError guard) is structurally shared with every per-app
# module's status skill (radarr / sonarr / …) — the deliberate per-app
# encapsulation pattern (CLAUDE.md). The formatted output is app-specific,
# so it stays inline rather than being factored into a shared helper.
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the sync status (cache-bypass) + format a detail
    block for the AI / drawer. Never raises."""
    print(f"[adguardsync] INFO adguardsync_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[adguardsync] warning: adguardsync_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    running = bool(data.get("sync_running"))
    origin_ok = bool(data.get("origin_ok"))
    origin_status = str(data.get("origin_status") or "").strip() or "unknown"
    rep_total = safe_int(data.get("replicas_total"))
    rep_ok = safe_int(data.get("replicas_ok"))
    failed = data.get("failed_names") if isinstance(data.get("failed_names"), list) else []
    lines = [
        f"{'🔄' if running else '✅'} Sync: {'running now' if running else 'idle'}",
        f"{'✅' if origin_ok else '❌'} Origin: {origin_status}",
        f"{'✅' if rep_total and rep_ok == rep_total else '⚠️'} Replicas: "
        f"{rep_ok}/{rep_total} in sync",
    ]
    if failed:
        lines.append("❌ Failing: " + ", ".join(str(f) for f in failed))
    version = str(data.get("version") or "").strip()
    if version:
        lines.append(f"🏷️ Version: {version}")
    return {
        "ok": True, "status": 200, "detail": "\n".join(lines),
        "sync_running": running, "origin_ok": origin_ok,
        "replicas_ok": rep_ok, "replicas_total": rep_total, "version": version,
    }


async def _sync_skill(host_row: dict, chip: dict, *,
                      host_id: Optional[str] = None) -> dict:
    """Action: trigger a sync (POST /api/v1/sync). Never raises."""
    base, auth, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[adguardsync] INFO adguardsync_sync host={host_id}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0,
                                     follow_redirects=True, auth=auth) as cli:
            r = await cli.post(base + "/api/v1/sync")
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[adguardsync] warning: sync host={host_id} failed — {type(e).__name__}: {e}")
        return {"ok": False, "status": 0, "detail": f"sync failed: {type(e).__name__}: {e}"}
    if r.status_code in (200, 201, 202, 204):
        return {"ok": True, "status": r.status_code,
                "detail": "🔄 Started an AdGuard Home sync — pushing the primary's "
                          "config to the replicas now."}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check username / password)"}
    return {"ok": False, "status": r.status_code,
            "detail": f"sync returned HTTP {r.status_code}"}


async def _logs_skill(host_row: dict, chip: dict, *,
                      host_id: Optional[str] = None) -> dict:
    """Read-only: the most recent sync log lines (GET /api/v1/logs, plain
    text). Returns the last ~25 lines. Never raises."""
    base, auth, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[adguardsync] INFO adguardsync_logs host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True, auth=auth) as cli:
            r = await cli.get(base + "/api/v1/logs")
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"logs fetch failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check username / password)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        text = (r.text or "").strip()
    except (ValueError, TypeError):
        text = ""
    if not text:
        return {"ok": True, "status": 200, "detail": "📜 No recent sync log entries."}
    tail = "\n".join(text.splitlines()[-25:])
    return {"ok": True, "status": 200, "detail": "📜 Recent sync log:\n" + tail}


async def _clear_logs_skill(host_row: dict, chip: dict, *,
                            host_id: Optional[str] = None) -> dict:
    """Action: clear the in-memory log buffer (POST /api/v1/clear-logs).
    Never raises."""
    base, auth, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[adguardsync] INFO adguardsync_clear_logs host={host_id}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True, auth=auth) as cli:
            r = await cli.post(base + "/api/v1/clear-logs")
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"clear-logs failed: {type(e).__name__}: {e}"}
    if r.status_code in (200, 201, 202, 204):
        return {"ok": True, "status": r.status_code, "detail": "🧹 Cleared the sync log buffer."}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check username / password)"}
    return {"ok": False, "status": r.status_code, "detail": f"clear-logs returned HTTP {r.status_code}"}
