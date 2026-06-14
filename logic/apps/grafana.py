"""Grafana (metrics + observability dashboard) per-app module.

Encapsulates everything Grafana-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Grafana speaks a REST API at
``<base>/api``. Public surface mirrors the per-app contract (``forgejo.py`` /
``seerr.py`` shape):

    SLUGS               — catalog slugs this module handles ("grafana").
    requires_api_key()  — True (the chip's ``api_key`` field stores a Grafana
                          service-account token — Administration → Service
                          accounts → Add service account token. A legacy API key
                          works too. Any role can read ``/api/org``; an Admin /
                          server-admin token unlocks the richer counts.)
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + dashboards (read) + datasources (read)
                          + search (arg, read). All read-only.

The expanded card answers "what's in my Grafana right now":

    org           — the org the token belongs to (name)
    dashboards    — number of dashboards (admin/stats when available, else the
                    search count)
    folders       — number of dashboard folders
    datasources   — number of configured datasources (Admin token)
    users / orgs  — server-wide counts (server-admin token only; 0 otherwise)
    version       — Grafana server version

Auth model: a service-account / API token sent on the
``Authorization: Bearer <token>`` header. The token is the secret and lives in
the chip's ``api_key`` field. The credential probe hits the auth-required
``/api/org`` so a bad / missing token fails loudly (401). Version comes from the
no-auth ``/api/health``. Single-instance app (NOT fleet). No image proxy —
Grafana dashboards have no thumbnail surface.
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
SLUGS: tuple[str, ...] = ("grafana",)

# Grafana REST API base path.
_API = "/api"

# Per-(host_id, service_idx) data cache for the expanded card. Default TTL
# overridable per chip via the editor's `cache_ttl` field. 60s — dashboard /
# datasource counts move slowly.
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# Cap on the rich-item rows a list skill returns.
_MAX_ROWS = 12

# Grafana's /api/search default limit is 1000, max 5000 — used to count
# dashboards when the (server-admin only) /api/admin/stats isn't available.
_DASH_COUNT_LIMIT = 5000

# Datasource-health probing (P1). Each datasource's /health endpoint proxies to
# the real backend, so it can be slow when a backend is down — bound the fan-out
# with a concurrency cap + a per-probe timeout + an overall wall-clock budget
# UNDER the fetch_data client timeout so a few dead datasources can't stall the
# card. (Per-app probe constants, mirroring the Prowlarr sweep — not TUNABLES.)
_DS_HEALTH_CONCURRENCY = 5
_DS_HEALTH_TIMEOUT_S = 6.0
_DS_HEALTH_BUDGET_S = 9.0

# Firing-alerts fetch (P2) — one call to the unified-alerting rules API.
_ALERTS_TIMEOUT_S = 8.0

# Grafana skills — all read-only. `grafana_search` is arg-carrying (AI /
# Telegram only — a drawer button can't supply the query).
SKILLS: tuple[dict, ...] = (
    {
        "id": "grafana_status",
        "name": "Grafana status",
        "ai_phrases": ("grafana status, how many dashboards, dashboard count, "
                       "grafana overview, grafana summary, how many datasources, "
                       "grafana version, observability status"),
        "destructive": False,
    },
    {
        "id": "grafana_dashboards",
        "name": "List dashboards",
        "ai_phrases": ("grafana dashboards, list dashboards, what dashboards do i "
                       "have, show grafana dashboards, my dashboards, recent "
                       "dashboards"),
        "destructive": False,
    },
    {
        "id": "grafana_datasources",
        "name": "List datasources",
        "ai_phrases": ("grafana datasources, list datasources, what datasources, "
                       "data sources on grafana, prometheus datasource, show "
                       "grafana sources"),
        "destructive": False,
    },
    {
        "id": "grafana_search",
        "name": "Search dashboards",
        "ai_phrases": ("search grafana for <name>, find dashboard <name>, do i "
                       "have a dashboard called <name>, look up <name> on grafana, "
                       "grafana search <name>, search dashboards <name>"),
        # arg-carrying → AI / Telegram only (the dispatch supplies the term).
        "arg": True,
        "arg_hint": "the dashboard title (or part of it) to search for",
        "destructive": False,
    },
    {
        "id": "grafana_alerts",
        "name": "Firing alerts",
        "ai_phrases": ("grafana alerts, what alerts are firing, firing alerts, "
                       "which grafana alerts are active, list firing alerts, "
                       "is anything alerting, grafana alert status, pending alerts"),
        "destructive": False,
    },
    {
        "id": "grafana_pause_alert",
        "name": "Pause an alert rule",
        "ai_phrases": ("pause the <name> alert, pause alert rule <name>, mute "
                       "<name> alert, silence the <name> grafana alert, stop the "
                       "<name> alert firing, pause grafana alert"),
        "arg": True,
        "arg_hint": "the alert rule title (or part of it) to pause",
        # DESTRUCTIVE: pausing an alert rule stops it evaluating (it won't fire).
        "destructive": True,
    },
    {
        "id": "grafana_unpause_alert",
        "name": "Resume an alert rule",
        "ai_phrases": ("unpause the <name> alert, resume alert rule <name>, "
                       "un-mute <name> alert, re-enable the <name> grafana alert, "
                       "resume grafana alert, unpause grafana alert"),
        "arg": True,
        "arg_hint": "the alert rule title (or part of it) to resume",
        "destructive": False,
    },
)


def requires_api_key() -> bool:
    """Grafana authenticates via a service-account / API token; the editor MUST
    render the token input (stored in the chip's api_key) + Test."""
    return True


def _headers(token: str) -> dict:
    """Grafana Bearer auth header + JSON Accept."""
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _version_from(resp) -> str:
    """Grafana version from ``GET /api/health`` ('' on any non-200 / parse
    failure — version is never load-bearing)."""
    try:
        if getattr(resp, "status_code", 0) != 200:
            return ""
        return str(as_dict(resp.json()).get("version") or "").strip()
    except (ValueError, TypeError, AttributeError):
        return ""


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Grafana's auth-required ``/api/org`` with the supplied token.
    Returns ``{ok, detail, status}``. Falls back to the chip's stored
    ``api_key`` when ``candidate_key`` is blank so the operator can re-test
    after first save without retyping."""
    token, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + _API + "/org"
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(token))
            try:
                ver = _version_from(await cli.get(base + _API + "/health",
                                                  headers=_headers(token)))
            except (httpx.HTTPError, OSError):
                ver = ""
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        try:
            org = str(as_dict(r.json()).get("name") or "").strip()
        except (ValueError, TypeError):
            org = ""
        who = f" (org: {org})" if org else ""
        return {"ok": True,
                "detail": (f"OK{who} (Grafana {ver})" if ver else f"OK{who}"),
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check the Grafana token)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


async def _count_array(cli: httpx.AsyncClient, base: str, token: str, path: str,
                       params: Optional[dict] = None) -> Optional[int]:
    """GET a list endpoint and return ``len(array)``, or ``None`` when the call
    failed / wasn't 200 / wasn't a list. Best-effort — a non-200 (e.g. a Viewer
    token 403 on /api/datasources) yields None so the caller seeds 0. Thin
    ``len`` wrapper over ``_fetch_array``."""
    rows = await _fetch_array(cli, base, token, path, params)
    return None if rows is None else len(rows)


async def _fetch_array(cli: httpx.AsyncClient, base: str, token: str, path: str,
                       params: Optional[dict] = None) -> Optional[list]:
    """GET a list endpoint and return the ARRAY (sibling of ``_count_array`` that
    yields the rows, not just ``len``), or ``None`` when the call failed / wasn't
    200 / wasn't a list. Best-effort — a Viewer-token 403 yields ``None``."""
    try:
        r = await cli.get(base + _API + path, headers=_headers(token), params=params or {})
        if r.status_code != 200:
            return None
        body = r.json()
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return None
    return as_list(body)


async def _probe_datasource_health(cli: httpx.AsyncClient, base: str, token: str,
                                   datasources: list) -> "tuple[int, list[str]]":
    """Probe each datasource's ``GET /api/datasources/uid/{uid}/health``
    concurrently (bounded concurrency + per-probe timeout + an overall budget).
    Returns ``(checked, unhealthy_names)`` where ``checked`` counts the
    datasources that gave a DEFINITIVE answer (status OK or ERROR) and
    ``unhealthy_names`` are those that returned ``status == "ERROR"``. Datasource
    types without a health check (404 / not-implemented / unknown) are counted as
    NEITHER — never flagged unhealthy. Never raises."""
    sem = asyncio.Semaphore(_DS_HEALTH_CONCURRENCY)
    deadline = time.time() + _DS_HEALTH_BUDGET_S
    checked = 0
    unhealthy: list[str] = []

    async def _one(ds: Any) -> None:
        nonlocal checked
        if not isinstance(ds, dict):
            return
        uid = str(ds.get("uid") or "").strip()
        if not uid:
            return
        name = str(ds.get("name") or uid).strip()
        async with sem:
            if time.time() >= deadline:
                return  # budget spent — leave the rest 'unknown'
            try:
                hr = await cli.get(base + _API + f"/datasources/uid/{uid}/health",
                                   headers=_headers(token), timeout=_DS_HEALTH_TIMEOUT_S)
            except (httpx.HTTPError, OSError):
                return  # unreachable probe — treat as unknown, not unhealthy
            try:
                status = str(as_dict(hr.json()).get("status") or "").strip().upper()
            except (ValueError, TypeError):
                status = ""
            if status == "OK":
                checked += 1
            elif status == "ERROR":
                checked += 1
                unhealthy.append(name)
            # any other shape (404 / "not implemented" / blank) → unknown, skip

    await asyncio.gather(*[_one(ds) for ds in datasources])
    return checked, unhealthy


async def _fetch_firing_alerts(cli: httpx.AsyncClient, base: str,
                               token: str) -> "tuple[Optional[int], Optional[int], list[str]]":
    """Count Grafana-managed alert rules currently FIRING (+ pending) via
    ``GET /api/prometheus/grafana/api/v1/rules`` (unified alerting). Returns
    ``(firing, pending, firing_names)``; ``(None, None, [])`` when alerting is
    unavailable / the token lacks access / the call fails — so the card hides the
    stat rather than showing a misleading 0. Best-effort; never raises."""
    try:
        r = await cli.get(base + "/api/prometheus/grafana/api/v1/rules",
                          headers=_headers(token), timeout=_ALERTS_TIMEOUT_S)
    except (httpx.HTTPError, OSError):
        return None, None, []
    if r.status_code != 200:
        return None, None, []
    try:
        groups = as_list(as_dict(as_dict(r.json()).get("data")).get("groups"))
    except (ValueError, TypeError):
        return None, None, []
    firing = pending = 0
    firing_names: list[str] = []
    for g in groups:
        for rule in as_list(as_dict(g).get("rules")):
            rd = as_dict(rule)
            state = str(rd.get("state") or "").strip().lower()
            if state == "firing":
                firing += 1
                nm = str(rd.get("name") or "").strip()
                if nm:
                    firing_names.append(nm)
            elif state == "pending":
                pending += 1
    return firing, pending, firing_names


# noinspection DuplicatedCode
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Grafana's dashboard / datasource / org summary for the card.

    Returns ``{available, org, dashboards, folders, datasources, users, orgs,
    version, fetched_at}``. Raises ``ValueError`` / ``RuntimeError`` (caller maps
    to HTTPException) when the token is unset / the base URL won't resolve / the
    load-bearing ``/api/org`` call errors. Every enrichment beyond ``/api/org``
    is best-effort — a Viewer token still produces a useful card."""
    token = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=token, log_tag="grafana")
    if hit is not None:
        return hit
    org_url = base + _API + "/org"
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            orr = await cli.get(org_url, headers=_headers(token))
            if orr.status_code != 200:
                print(f"[grafana] error: fetch host={host_id} url={orr.request.url} "
                      f"returned HTTP {orr.status_code} (check the chip URL points at "
                      f"the Grafana root, e.g. http://grafana.example.com:3000)")
                if orr.status_code in (401, 403):
                    raise RuntimeError(f"upstream auth failed: HTTP {orr.status_code} "
                                       f"(check the Grafana token) — {org_url}")
                raise RuntimeError(f"upstream returned HTTP {orr.status_code} for {org_url}")
            try:
                org = str(as_dict(orr.json()).get("name") or "").strip()
            except (ValueError, TypeError):
                org = ""
            # Version (nice-to-have, no auth needed).
            try:
                version = _version_from(
                    await cli.get(base + _API + "/health", headers=_headers(token)))
            except (httpx.HTTPError, OSError):
                version = ""
            # Server-admin stats give exact dashboards / datasources / users /
            # orgs in ONE call — prefer them when the token is a server admin.
            users = orgs = 0
            stats_dash: Optional[int] = None
            stats_ds: Optional[int] = None
            try:
                sr = await cli.get(base + _API + "/admin/stats", headers=_headers(token))
                if sr.status_code == 200:
                    st = as_dict(sr.json())
                    users = safe_int(st.get("users"))
                    orgs = safe_int(st.get("orgs"))
                    stats_dash = safe_int(st.get("dashboards"))
                    stats_ds = safe_int(st.get("datasources"))
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                users = orgs = 0
            # Dashboards — admin/stats value when available, else count the search
            # array (capped at the page limit).
            dashboards = stats_dash
            if dashboards is None:
                dashboards = await _count_array(
                    cli, base, token, "/search",
                    {"type": "dash-db", "limit": str(_DASH_COUNT_LIMIT)})
            # Folders (best-effort).
            folders = await _count_array(cli, base, token, "/folders",
                                         {"limit": str(_DASH_COUNT_LIMIT)})
            # Datasources — fetch the FULL list (Admin token) so we can both count
            # them AND probe per-source health (403s for a Viewer token → None).
            ds_list = await _fetch_array(cli, base, token, "/datasources")
            datasources = stats_ds
            if datasources is None:
                datasources = len(ds_list) if ds_list is not None else None
            # P1 datasource-health + P2 firing-alerts enrichment, in parallel.
            # Both best-effort + wrapped so an enrich bug can never turn the whole
            # card into an error (the cancellation contract is preserved first).
            ds_unhealthy_names: list[str] = []
            alerts_firing: Optional[int] = None
            alerts_pending: Optional[int] = None
            alerts_firing_names: list[str] = []
            try:
                ds_health, alerts = await asyncio.gather(
                    _probe_datasource_health(cli, base, token, ds_list or []),
                    _fetch_firing_alerts(cli, base, token))
                _ds_checked, ds_unhealthy_names = ds_health
                alerts_firing, alerts_pending, alerts_firing_names = alerts
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[grafana] warning: health/alerts enrich host={host_id} "
                      f"skipped — {type(e).__name__}: {e}")
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[grafana] error: fetch host={host_id} url={org_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    out: dict[str, Any] = {
        "available": True,
        "org": org,
        "dashboards": safe_int(dashboards),
        "folders": safe_int(folders),
        "datasources": safe_int(datasources),
        # P1: per-datasource health (Admin token only).
        "datasources_unhealthy": len(ds_unhealthy_names),
        "datasources_unhealthy_names": ds_unhealthy_names,
        # P2: unified-alerting firing / pending rule counts (None when alerting
        # is unavailable → the card hides the stat).
        "alerts_firing": alerts_firing,
        "alerts_pending": alerts_pending,
        "alerts_firing_names": alerts_firing_names,
        "users": users,
        "orgs": orgs,
        "version": version,
        "fetched_at": int(now),
    }
    # Best-effort firing-alert + dashboard meta-monitor trend from the shared
    # lifespan grafana_sampler. Missing sampler / no samples yet leaves the
    # card's instantaneous stats untouched.
    out["trend"] = _safe_trend(host_id, service_idx)
    print(f"[grafana] INFO fetched host={host_id} org={org!r} "
          f"dashboards={out['dashboards']} folders={out['folders']} "
          f"datasources={out['datasources']} ds_unhealthy={out['datasources_unhealthy']} "
          f"alerts_firing={alerts_firing} alerts_pending={alerts_pending} "
          f"users={users} orgs={orgs}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def _safe_trend(host_id: str, service_idx: int) -> Optional[dict]:
    """Best-effort firing-alert + dashboard trend for the card — the shared
    grafana_sampler's per-chip ``trend_summary``. Returns ``None`` (never raises)
    when the sampler isn't importable / errors, so a trend hiccup can't fail the
    card."""
    try:
        from logic.apps import grafana_sampler as _sampler  # noqa: PLC0415
        return _sampler.trend_summary(host_id, int(service_idx))
    except Exception as e:  # noqa: BLE001
        print(f"[grafana] trend_summary({host_id}#{service_idx}) skipped: {e}")
        return None


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "org": data.get("org") or "",
        "dashboards": safe_int(data.get("dashboards")),
        "folders": safe_int(data.get("folders")),
        "datasources": safe_int(data.get("datasources")),
        "datasources_unhealthy": safe_int(data.get("datasources_unhealthy")),
        "alerts_firing": data.get("alerts_firing"),
        "alerts_pending": data.get("alerts_pending"),
        "users": safe_int(data.get("users")),
        "orgs": safe_int(data.get("orgs")),
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
    carries the free-form search term for ``grafana_search``."""
    if skill_id == "grafana_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "grafana_dashboards":
        return await _dashboards_skill(host_row, chip, host_id=host_id)
    if skill_id == "grafana_datasources":
        return await _datasources_skill(host_row, chip, host_id=host_id)
    if skill_id == "grafana_search":
        return await _search_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "grafana_alerts":
        return await _alerts_skill(host_row, chip, host_id=host_id)
    if skill_id == "grafana_pause_alert":
        return await _pause_alert_skill(host_row, chip, arg=arg, pause=True, host_id=host_id)
    if skill_id == "grafana_unpause_alert":
        return await _pause_alert_skill(host_row, chip, arg=arg, pause=False, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _resolve_skill_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(token, base)`` or a ready ``{ok: False, detail}`` error dict
    for a Grafana skill."""
    token = (chip.get("api_key") or "").strip()
    if not token:
        return "", "", {"ok": False, "status": 0, "detail": "Grafana token not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return token, base, None


def _status_guard(r: "httpx.Response") -> Optional[dict]:
    """Shared 401 / 403 + non-200 guard for a Grafana read skill. Returns a ready
    error dict, or None when the response is 200 OK."""
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check the Grafana token)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    return None


# noinspection DuplicatedCode
async def _skill_get(base: str, path: str, *, token: str, params: dict,
                     timeout: float, verb: str) -> "httpx.Response | dict":
    """Shared GET + status guard for a Grafana read skill. Returns the 200 OK
    response, or a ready ``{ok: False, ...}`` error dict (the call failed OR the
    status wasn't 200). The caller discriminates with one ``isinstance(r, dict)``
    (which also narrows the response type)."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=timeout,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + path, headers=_headers(token), params=params)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"{verb} failed: {type(e).__name__}: {e}"}
    return _status_guard(r) or r


def _items_and_lines(rows: list, row_fn) -> "tuple[list[dict], list[str]]":
    """Map the first ``_MAX_ROWS`` raw rows through ``row_fn`` into the rich-item
    list + the matching ``• title (subtitle)`` text lines, skipping rows the
    builder rejects. Shared by every list skill."""
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
    """Read-only: live-fetch the dashboard / datasource / org summary
    (force-bypasses the cache) and return a formatted ``detail``. Never raises."""
    print(f"[grafana] INFO grafana_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[grafana] warning: grafana_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    dashboards = safe_int(data.get("dashboards"))
    folders = safe_int(data.get("folders"))
    datasources = safe_int(data.get("datasources"))
    users = safe_int(data.get("users"))
    orgs = safe_int(data.get("orgs"))
    org = str(data.get("org") or "").strip()
    unhealthy = safe_int(data.get("datasources_unhealthy"))
    unhealthy_names = as_list(data.get("datasources_unhealthy_names"))
    firing = data.get("alerts_firing")
    pending = data.get("alerts_pending")
    firing_names = as_list(data.get("alerts_firing_names"))
    lines = [f"📊 Dashboards: {dashboards:,}"]
    if folders:
        lines.append(f"📁 Folders: {folders:,}")
    ds_line = f"🔌 Datasources: {datasources:,}"
    if unhealthy > 0:
        ds_line += f" ({unhealthy:,} unhealthy)"
    lines.append(ds_line)
    if unhealthy_names:
        lines.append("⚠️ Unhealthy: " + ", ".join(str(n) for n in unhealthy_names[:8]))
    if isinstance(firing, int) and firing > 0:
        line = f"🚨 Alerts firing: {firing:,}"
        if firing_names:
            line += " — " + ", ".join(str(n) for n in firing_names[:8])
        lines.append(line)
    if isinstance(pending, int) and pending > 0:
        lines.append(f"⏳ Alerts pending: {pending:,}")
    if org:
        lines.append(f"🏢 Org: {org}")
    if users or orgs:
        lines.append(f"👥 Users: {users:,} · Orgs: {orgs:,}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "dashboards": dashboards, "folders": folders,
        "datasources": datasources, "datasources_unhealthy": unhealthy,
        "alerts_firing": firing if isinstance(firing, int) else None,
        "alerts_pending": pending if isinstance(pending, int) else None,
    }


def _dash_row(d: dict) -> Optional[dict]:
    """One dashboard as a rich skill-result item: the title, with a folder + tags
    subtitle. No poster — Grafana has no thumbnail surface."""
    if not isinstance(d, dict):
        return None
    title = str(d.get("title") or "").strip()
    if not title:
        return None
    bits = []
    folder = str(d.get("folderTitle") or "").strip()
    if folder:
        bits.append(f"📁 {folder}")
    tags = [str(t).strip() for t in as_list(d.get("tags")) if str(t).strip()]
    if tags:
        bits.append(" ".join(f"#{t}" for t in tags[:4]))
    if d.get("isStarred"):
        bits.append("⭐")
    return {"title": title, "subtitle": " · ".join(bits)}


async def _dashboards_skill(host_row: dict, chip: dict, *,
                            host_id: Optional[str] = None) -> dict:
    """Read-only: list dashboards (``GET /api/search?type=dash-db``) as rich rows.
    Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[grafana] INFO grafana_dashboards host={host_id} (live fetch)")
    r = await _skill_get(base, _API + "/search", token=token,
                         params={"type": "dash-db", "limit": str(_MAX_ROWS)},
                         timeout=15.0, verb="fetch")
    if isinstance(r, dict):
        return r
    try:
        rows = as_list(r.json())
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not rows:
        return {"ok": True, "status": 200, "detail": "📊 No dashboards found."}
    items, lines = _items_and_lines(rows, _dash_row)
    out: dict = {"ok": True, "status": 200,
                 "detail": "📊 Dashboards:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.grafana.dashboards_count")


def _ds_row(ds: dict) -> Optional[dict]:
    """One datasource as a rich skill-result item: the name, with a type +
    default-flag subtitle."""
    if not isinstance(ds, dict):
        return None
    name = str(ds.get("name") or "").strip()
    if not name:
        return None
    typ = str(ds.get("typeName") or ds.get("type") or "").strip()
    bits = [typ] if typ else []
    if ds.get("isDefault"):
        bits.append("default")
    return {"title": name, "subtitle": " · ".join(bits)}


# noinspection DuplicatedCode
async def _datasources_skill(host_row: dict, chip: dict, *,
                             host_id: Optional[str] = None) -> dict:
    """Read-only: list configured datasources (``GET /api/datasources``) as rich
    rows. Requires an Admin token (a Viewer token 403s → surfaced cleanly). Never
    raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[grafana] INFO grafana_datasources host={host_id} (live fetch)")
    r = await _skill_get(base, _API + "/datasources", token=token,
                         params={}, timeout=15.0, verb="fetch")
    if isinstance(r, dict):
        return r
    try:
        rows = as_list(r.json())
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not rows:
        return {"ok": True, "status": 200, "detail": "🔌 No datasources configured."}
    items, lines = _items_and_lines(rows, _ds_row)
    out: dict = {"ok": True, "status": 200,
                 "detail": "🔌 Datasources:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.grafana.datasources_count")


# noinspection DuplicatedCode
async def _search_skill(host_row: dict, chip: dict, *,
                        arg: Optional[str] = None,
                        host_id: Optional[str] = None) -> dict:
    """Read-only (arg): search dashboards by title via
    ``GET /api/search?query=<term>&type=dash-db`` and return the top matches as
    rich rows. Never raises."""
    term = (arg or "").strip()
    if not term:
        return {"ok": False, "status": 0,
                "detail": "no search term given — say e.g. 'search grafana for node exporter'"}
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[grafana] INFO grafana_search host={host_id} term={term!r} (live search)")
    r = await _skill_get(base, _API + "/search", token=token,
                         params={"query": term, "type": "dash-db",
                                 "limit": str(_MAX_ROWS)},
                         timeout=20.0, verb="search")
    if isinstance(r, dict):
        return r
    try:
        rows = as_list(r.json())
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not rows:
        return {"ok": True, "status": 200,
                "detail": f"🔍 No Grafana dashboards match “{term}”."}
    items, lines = _items_and_lines(rows, _dash_row)
    out: dict = {"ok": True, "status": 200,
                 "detail": f"🔍 Grafana dashboards matching “{term}”:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.grafana.dashboards_count")


# State → emoji for the firing-alerts rich rows.
_ALERT_STATE_EMOJI = {"firing": "🚨", "pending": "⏳", "inactive": "✅", "normal": "✅"}


def _alert_rich_rows(groups: list) -> "tuple[list[dict], list[str]]":
    """Build the firing-then-pending alert rich rows + text lines from the
    unified-alerting ``/rules`` groups. Each firing row carries a ⏸ Pause
    ``row_action`` (destructive, arg = the rule name). Inactive rules are
    skipped — the list is "what needs attention"."""
    collected: list[dict] = []  # (state_order, name, group, since) tuples shaped as dicts
    for g in groups:
        gd = as_dict(g)
        gname = str(gd.get("name") or gd.get("file") or "").strip()
        for rule in as_list(gd.get("rules")):
            rd = as_dict(rule)
            state = str(rd.get("state") or "").strip().lower()
            if state not in ("firing", "pending"):
                continue
            name = str(rd.get("name") or "").strip()
            if not name:
                continue
            collected.append({"state": state, "name": name, "group": gname,
                              "active_at": str(rd.get("activeAt") or "").strip()})
    # Firing first, then pending; stable within each by name.
    collected.sort(key=lambda _c: (0 if _c["state"] == "firing" else 1, _c["name"].lower()))
    items: list[dict] = []
    lines: list[str] = []
    for r in collected[:_MAX_ROWS]:
        emoji = _ALERT_STATE_EMOJI.get(r["state"], "•")
        bits = [r["state"]]
        if r["group"]:
            bits.append(f"📁 {r['group']}")
        sub = " · ".join(bits)
        row: dict = {"title": f"{emoji} {r['name']}", "subtitle": sub}
        if r["state"] == "firing":
            row["row_actions"] = [{
                "skill_id": "grafana_pause_alert", "arg": r["name"], "icon": "pause",
                "title_i18n": "apps.grafana.row_pause_alert", "destructive": True,
                "confirm_i18n": "apps.grafana.row_pause_alert_confirm",
                "confirm_text_i18n": "apps.grafana.row_pause_alert",
            }]
        items.append(row)
        lines.append(f"{emoji} {r['name']}" + (f"  ({sub})" if sub else ""))
    return items, lines


async def _alerts_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None) -> dict:
    """Read-only: list the currently FIRING (+ pending) Grafana alert rules as
    rich rows via ``GET /api/prometheus/grafana/api/v1/rules``. Each firing row
    gets a ⏸ Pause button. Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[grafana] INFO grafana_alerts host={host_id} (live fetch)")
    r = await _skill_get(base, "/api/prometheus/grafana/api/v1/rules", token=token,
                         params={}, timeout=_ALERTS_TIMEOUT_S, verb="fetch")
    if isinstance(r, dict):
        return r
    try:
        groups = as_list(as_dict(as_dict(r.json()).get("data")).get("groups"))
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    items, lines = _alert_rich_rows(groups)
    if not items:
        return {"ok": True, "status": 200, "detail": "✅ No Grafana alerts firing."}
    out: dict = {"ok": True, "status": 200,
                 "detail": "🚨 Firing / pending alerts:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.grafana.alerts_count")


async def _resolve_alert_rule(cli: httpx.AsyncClient, base: str, token: str,
                              name: str) -> "tuple[Optional[dict], Optional[dict]]":
    """Resolve an alert rule by TITLE via ``GET /api/v1/provisioning/alert-rules``
    (exact title, case-insensitive, then substring). Returns ``(rule, None)`` or
    ``(None, error_dict)`` where the error lists the available rule titles."""
    r = await cli.get(base + "/api/v1/provisioning/alert-rules", headers=_headers(token))
    guard = _status_guard(r)
    if guard:
        # Provisioning API needs an Admin / alerting-writer token — clarify.
        if r.status_code in (401, 403):
            return None, {"ok": False, "status": r.status_code,
                          "detail": "auth failed — pausing alerts needs a Grafana "
                                    "token with alerting write access (Admin)"}
        return None, guard
    try:
        rules = as_list(r.json())
    except (ValueError, TypeError):
        return None, {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    want = (name or "").strip().lower()
    for rule in rules:
        if isinstance(rule, dict) and str(rule.get("title") or "").strip().lower() == want:
            return rule, None
    for rule in rules:
        if isinstance(rule, dict) and want and want in str(rule.get("title") or "").strip().lower():
            return rule, None
    titles = ", ".join(str(rd.get("title") or "").strip()
                       for rd in rules if isinstance(rd, dict) and str(rd.get("title") or "").strip())
    return None, {"ok": False, "status": 404,
                  "detail": f"no alert rule titled “{name}” — available: {titles or '(none)'}"}


async def _pause_alert_skill(host_row: dict, chip: dict, *, arg: Optional[str],
                             pause: bool, host_id: Optional[str] = None) -> dict:
    """Action (arg): pause / unpause one alert rule by title via the provisioning
    API. Resolves the rule, flips ``isPaused``, and PUTs it back (with
    ``X-Disable-Provenance`` so a file-provisioned rule can still be toggled).
    Pausing is DESTRUCTIVE (the rule stops evaluating). Never raises."""
    name = (arg or "").strip()
    if not name:
        verb = "pause" if pause else "unpause"
        return {"ok": False, "status": 0,
                "detail": f"no alert given — say e.g. '{verb} the High CPU alert'"}
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    verb = "pause" if pause else "unpause"
    print(f"[grafana] INFO grafana_{verb}_alert host={host_id} rule={name!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            rule, rerr = await _resolve_alert_rule(cli, base, token, name)
            if rerr:
                return rerr
            assert rule is not None
            uid = str(rule.get("uid") or "").strip()
            title = str(rule.get("title") or name).strip()
            if not uid:
                return {"ok": False, "status": 404,
                        "detail": f"alert rule “{title}” has no uid (cannot toggle)"}
            if bool(rule.get("isPaused")) == pause:
                state = "paused" if pause else "active"
                return {"ok": True, "status": 200,
                        "detail": f"ℹ️ Alert “{title}” is already {state}."}
            body = dict(rule)
            body["isPaused"] = pause
            pr = await cli.put(base + f"/api/v1/provisioning/alert-rules/{uid}",
                               headers={**_headers(token),
                                        "Content-Type": "application/json",
                                        "X-Disable-Provenance": "true"},
                               json=body)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"{verb} failed: {type(e).__name__}: {e}"}
    if pr.status_code in (401, 403):
        return {"ok": False, "status": pr.status_code,
                "detail": "auth failed — pausing alerts needs an Admin token"}
    if not (200 <= pr.status_code < 300):
        return {"ok": False, "status": pr.status_code, "detail": f"HTTP {pr.status_code}"}
    icon = "⏸️" if pause else "▶️"
    state = "paused" if pause else "resumed"
    return {"ok": True, "status": 200, "detail": f"{icon} Alert “{title}” {state}."}
