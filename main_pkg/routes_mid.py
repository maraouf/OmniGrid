"""Second half of routes — split for line-count hygiene.
Chain: main_pkg.routes → main_pkg.routes_mid → main_pkg.routes_late
→ main_pkg.routes_late_b → main_pkg.routes_extra.
"""
"""Continuation of `main` (routes module under `main_pkg/`) — extracted to keep main.py under the
line-count "uncomfortable to navigate" threshold. Re-exported via
`from main_routes import *` at the bottom of `main.py`, which pulls
every public symbol (including FastAPI routes registered through
`@app.<verb>(...)`) back into the main namespace.

Loading order:
  1. main.py runs top-to-bottom, defining `app`, every helper,
     and roughly the first half of the routes.
  2. main.py end: `from main_routes import *` triggers main_routes load.
  3. main_routes.py top: `from main import *` pulls EVERY symbol
     main has defined so far (`app`, helpers, Pydantic models,
     etc.) into main_routes's namespace so the route decorators
     below can reference them.
  4. main_routes.py body runs; routes register against the shared
     `app` instance.
  5. main_routes.py finishes; control returns to main.py's star-
     import which now has every main_routes symbol available.
"""
"""
OmniGrid — Portainer-native update dashboard.

Endpoints:
  GET  /api/items                     - All services + containers with status
  GET  /api/item/{raw_id}             - Single item detail
  POST /api/update/stack/{id}         - Update stack (Prune+PullImage)
  POST /api/update/container/{id}     - Recreate standalone container
  POST /api/restart/service/{id}      - Force restart a Swarm service
  GET  /api/ops   /  /api/ops/{id}    - Live operation status
  GET  /api/history                   - Persisted history
  GET  /api/ignores  /  POST  /  DELETE
  GET  /api/settings /  POST
  POST /api/notify-test
  GET  /api/healthz
  GET  /metrics                       - Prometheus scrape endpoint
"""
# Module-wide suppression for the recurring project-pattern lint noise that
# the operator validates and accepts: defensive broad-except guards (project
# convention is to catch + log + continue at API-boundary sites so a single
# broken provider can't 500 the whole route); cross-module `_protected_member`
# access (helpers like `_node_attr` / `_node_matches` / `_load_mappings` /
# `_PROVIDER_PREFIXES` are deliberately shared by main.py without a public
# alias because the indirection isn't worth a re-export); local `e` / `_events`
# / `_gather_mod` / `_stats_mod` shadow names inside `except` clauses and
# lazy-import blocks; explicit `arg=default` kwargs at call sites kept for
# readability of the intended value; missing docstrings on internal FastAPI
# route handlers whose function name + signature is self-describing; the
# `Member 'None' of 'Any | None'` chain reported on every `_admin: auth.User
# = Depends(auth.require_admin)` parameter (PyCharm cannot narrow through
# FastAPI's Depends() injection). Real bugs OUTSIDE these noise classes are
# fixed inline.
import asyncio
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any, Iterable, Optional, Set, cast

# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).
from dotenv import load_dotenv

from logic.env_keys import EnvKey, env_get  # noqa: E402

# Re-import parent's namespace so decorators below find `app`,
# helpers, Pydantic models, etc.
from main import *  # noqa: E402,F401,F403

from main_pkg.routes import *  # noqa: E402,F401,F403



# noinspection PyShadowingBuiltins,PyTypeChecker,PyUnresolvedReferences
@app.get("/api/debug/subject")
async def api_debug_subject(
    kind: str = "",
    id: str = "",
    since_hours: int = 1,
    *,
    _u: AdminUser,
):
    """Admin-only diagnostic for the Stacks / Services / Nodes drawers.

    `kind=item` resolves against `_cache["items"]` (covers services,
    standalone containers, orphans).
    `kind=stack` resolves against `_cache["stacks"]` for the rollup
    view — surfaces the stack's item list + aggregate counts +
    per-stack diagnostic flags directly, without the legacy
    items-cache prefix-match quirk.
    `kind=node` resolves against the Swarm node map.

    Returns a JSON dump of the cached record + the per-item stats
    snapshot + a `samples_in_window` block (same shape as
    /api/hosts/debug for diagnostic-rendering reuse) + a
    human-readable `diagnostics` list explaining why drawer charts
    might be showing "Collecting data" or empty bars.

    Lightweight by design — no fresh probes against Portainer or
    providers. Reads the same `_cache` / `_stats_cache` / DB tables
    the live drawer already consumes.
    """
    if kind not in ("item", "stack", "node"):
        raise HTTPException(400, "kind must be 'item', 'stack', or 'node'")
    if not id:
        raise HTTPException(400, "id query param required")

    out: dict[str, Any] = {"kind": kind, "id": id}
    diagnostics: list[str] = []
    since_hours_clamped = max(1, min(168, int(since_hours or 1)))

    if kind == "item":
        items = _cache.get("items") or []

        # Prefix matching is intentional for `svc:<12hex>` / `ctn:<12hex>`
        # short forms the SPA passes in. But a short bare id (e.g. `s`)
        # would prefix-match the FIRST item whose raw_id starts with
        # `s` — operator clicks "Debug" on item A and the panel renders
        # item Z's data. Gate the prefix branch on (a) the `svc:` /
        # `ctn:` prefix (legitimate short form), OR (b) minimum length
        # ≥ 12 chars (long enough to be a real id hash). Bare-id
        # equality at line 12266-67 still works for the full hash.
        def _id_matches(it: dict) -> bool:
            if it.get("id") == id or it.get("raw_id") == id:
                return True
            rid = it.get("raw_id") or ""
            if not rid or not id:
                return False
            looks_short_form = id.startswith(("svc:", "ctn:"))
            long_enough = len(id) >= 12
            return (looks_short_form or long_enough) and rid.startswith(id)

        record = next((it for it in items if _id_matches(it)), None)
        if record is None:
            raise HTTPException(404, f"no item with id={id!r}")
        out["record"] = record

        # Stack rollup if this item is part of one
        stack_name = (record.get("stack") or "").strip()
        if stack_name:
            for st in (_cache.get("stacks") or []):
                if st.get("name") == stack_name:
                    out["stack"] = st
                    break

        # Live stats entry (cpu_percent / mem_usage / has_stats / has_size)
        live_stats = (_stats_cache.get("stats") or {}).get(record.get("id")) or {}
        out["live_stats"] = live_stats

        # Historical sample density — drives the "why is my chart empty"
        # diagnostic. stats_samples is item-keyed so we can answer per-row.
        sw = _item_samples_in_window(record.get("id"), since_hours_clamped)
        out["samples_in_window"] = sw

        if not live_stats:
            diagnostics.append(
                "live_stats is empty — _stats_cache has no entry for "
                "this item. Either the sampler has not ticked since the "
                "item appeared, OR the Portainer /stats call failed for "
                "this container (check the agent on the item's node)."
            )
        else:
            if live_stats.get("has_stats") is False:
                diagnostics.append(
                    "live_stats.has_stats=false — Portainer "
                    "/containers/{id}/stats returned no usable data. "
                    "Bar will show '—'; sparkline will be empty until "
                    "stats start returning."
                )
            if live_stats.get("has_size") is False:
                diagnostics.append(
                    "live_stats.has_size=false — Portainer ?size=1 "
                    "enrichment failed. Disk bar + sparkline are empty "
                    "because there's no size_root to plot."
                )

        if sw.get("count", 0) == 0:
            diagnostics.append(
                f"stats_samples has 0 rows for this item in the past "
                f"{since_hours_clamped}h. Sampler has not persisted any "
                f"history yet — drawer charts show 'Collecting data' "
                f"until at least 2 samples land (sampler interval ~"
                f"{sw.get('expected_interval_s', 300)}s)."
            )
        elif sw.get("count", 0) < 2:
            diagnostics.append(
                f"Only {sw['count']} sample in the past "
                f"{since_hours_clamped}h. Drawer charts need ≥2 points "
                f"to render a line — wait one more sampler tick "
                f"(~{sw.get('expected_interval_s', 300)}s)."
            )
        else:
            age = int(sw.get("newest_age_s") or 0)
            expected = int(sw.get("expected_interval_s") or 300)
            if age > expected * 3:
                diagnostics.append(
                    f"Newest stats_samples row is {age}s old "
                    f"(>3× expected interval {expected}s) — sampler "
                    f"may have stalled. Charts continue rendering the "
                    f"last-known data but will look 'frozen'."
                )
            gap = int(sw.get("median_gap_s") or 0)
            if gap > expected * 1.5:
                diagnostics.append(
                    f"Median gap between consecutive samples is {gap}s "
                    f"— above 1.5× the configured {expected}s interval. "
                    f"Sampler is ticking slower than expected; check "
                    f"Admin → Logs for [stats] warnings."
                )

        out["diagnostics"] = diagnostics
        return out

    if kind == "stack":
        # First-class stack rollup lookup — no items-cache prefix
        # quirk. `_cache["stacks"]` is the per-gather rolled-up list
        # of `{name, stack_id, items[], total, updates, errors, ...}`.
        # Match on both `name` and `stack_id` so the SPA can pass
        # either identifier.
        stacks = _cache.get("stacks") or []
        record = next(
            (st for st in stacks
             if st.get("name") == id
             or str(st.get("stack_id") or "") == str(id)),
            None,
        )
        if record is None:
            raise HTTPException(404, f"no stack with id={id!r}")
        out["record"] = record

        # Per-item samples-in-window aggregate. The stack rollup
        # carries an `items` array of full item records — sum the
        # sample counts so the operator can see "stack-wide, X
        # samples landed in the past hour" without drilling into
        # every member.
        item_count = len(record.get("items") or [])
        total_samples = 0
        items_with_samples = 0
        oldest_ts = None
        newest_ts = None
        for it in (record.get("items") or []):
            iid = it.get("id")
            if not iid:
                continue
            sw = _item_samples_in_window(iid, since_hours_clamped)
            n = int(sw.get("count") or 0)
            total_samples += n
            if n > 0:
                items_with_samples += 1
                if oldest_ts is None or (sw.get("oldest_ts") and sw["oldest_ts"] < oldest_ts):
                    oldest_ts = sw.get("oldest_ts")
                if newest_ts is None or (sw.get("newest_ts") and sw["newest_ts"] > newest_ts):
                    newest_ts = sw.get("newest_ts")
        out["samples_in_window"] = {
            "hours": since_hours_clamped,
            "total_samples_across_items": total_samples,
            "items_with_samples": items_with_samples,
            "items_total": item_count,
            "oldest_ts": oldest_ts,
            "newest_ts": newest_ts,
        }

        # Aggregate by-status / by-health for one-line stack health
        # summary — same shape the by_status / by_health blocks on
        # `kind=node` produce so the SPA can render either with one
        # template path.
        by_status: dict[str, int] = {}
        by_health: dict[str, int] = {}
        for it in (record.get("items") or []):
            s = str(it.get("status") or "unknown")
            by_status[s] = by_status.get(s, 0) + 1
            h = str(it.get("health") or "unknown")
            by_health[h] = by_health.get(h, 0) + 1
        out["aggregate"] = {
            "by_status": by_status,
            "by_health": by_health,
        }

        if item_count == 0:
            diagnostics.append(
                "Stack has no items in `_cache[\"stacks\"][i].items` "
                "— either every member was filtered out (ignored / "
                "wrong stack tag) OR the gather hasn't run since the "
                "stack was deployed."
            )
        elif items_with_samples == 0:
            diagnostics.append(
                f"None of the {item_count} items in this stack have "
                f"any stats_samples rows in the past "
                f"{since_hours_clamped}h. Either every container is "
                f"newly-started (sampler hasn't ticked yet) OR the "
                f"Portainer /stats call is failing across the stack "
                f"(agent unreachable on the items' nodes)."
            )
        elif items_with_samples < item_count:
            diagnostics.append(
                f"{items_with_samples} of {item_count} items have "
                f"samples; the other "
                f"{item_count - items_with_samples} are silent — "
                f"check the per-item drawer for which ones are "
                f"missing data."
            )

        out["diagnostics"] = diagnostics
        return out

    # ----- kind == "node" --------------------------------------------
    nodes_map = _cache.get("nodes") or {}
    # _cache["nodes"] is {NodeID: hostname}; accept either as the id.
    node_id = None
    hostname = None
    for nid, hn in nodes_map.items():
        if nid == id or hn == id:
            node_id = nid
            hostname = hn
            break
    if hostname is None:
        raise HTTPException(404, f"no node with id={id!r}")
    out["node_id"] = node_id
    out["hostname"] = hostname

    items = _cache.get("items") or []
    items_on_node = [it for it in items if it.get("node") == hostname]
    by_status: dict[str, int] = {}
    by_health: dict[str, int] = {}
    for it in items_on_node:
        s = str(it.get("status") or "unknown")
        by_status[s] = by_status.get(s, 0) + 1
        h = str(it.get("health") or "unknown")
        by_health[h] = by_health.get(h, 0) + 1
    out["items_on_node"] = {
        "count": len(items_on_node),
        "by_status": by_status,
        "by_health": by_health,
        "names": [it.get("name") for it in items_on_node[:50]],
    }

    # Merged host-stats blob (same dict the host drawer renders from)
    nodes_info = (_cache.get("nodes_info") or {}).get(hostname) or {}
    out["nodes_info"] = nodes_info

    if not items_on_node:
        diagnostics.append(
            f"No items mapped to hostname={hostname!r}. Portainer's "
            f"task list returned nothing for this node — Swarm may "
            f"have evicted it OR the Portainer agent there is "
            f"unreachable."
        )
    if not nodes_info:
        diagnostics.append(
            f"nodes_info has no entry for hostname={hostname!r}. No "
            f"host-stats provider (Beszel / Pulse / node-exporter / "
            f"Webmin / SNMP / Ping) reported data for this node, so "
            f"host CPU / Memory / Disk bars stay empty."
        )

    out["diagnostics"] = diagnostics
    return out


# noinspection PyShadowingBuiltins,PyTypeChecker,PyUnresolvedReferences
@app.get("/api/hosts/debug")
async def api_hosts_debug(
    id: str = "",
    since_hours: int = 1,
    *,
    _u: AdminUser,
):
    """Admin-only diagnostic: raw provider responses + normalized
    per-provider + merged + rendered for ONE curated host.

    Purpose: spot-check what each provider is actually emitting vs
    what OmniGrid keeps after the best-of merge vs what the UI
    ultimately sees. The four sections line up so dropped fields,
    shape mismatches, or coverage gaps are visible side-by-side.

    Heavyweight by design — runs fresh probes against each enabled
    provider. Intended for interactive debugging, not polled. The UI
    fetches it lazily when the "Debug" panel in the host drawer is
    opened.
    """
    if not id:
        raise HTTPException(400, "id query param required")

    from logic import beszel as _beszel
    from logic import pulse as _pulse
    from logic import node_exporter as _ne
    from logic import host_net_sampler as _host_net_sampler
    from logic import host_metrics_sampler as _host_metrics_sampler

    curated = _load_hosts_config()
    record = next((h for h in curated if h["id"] == id), None)
    if record is None:
        raise HTTPException(404, f"no curated host with id={id!r}")

    # Which providers are live? Same derivation as api_hosts.
    active = active_host_stats_providers()

    providers_raw: dict[str, Any] = {
        "pulse": None, "beszel": None, "node_exporter": None,
        "webmin": None, "snmp": None,
    }
    providers_normalized: dict[str, Any] = {
        "pulse": None, "beszel": None, "node_exporter": None,
        "webmin": None, "snmp": None,
    }

    # ---- SNMP kickoff (early launch) -----------------------------
    # SNMP is the slowest provider in the handler — at default
    # per-host walk concurrency=1, ~67 OID branches across base +
    # Dell + Cisco + APC + UCD + Synology + Printer MIBs serialise
    # to 30-50s on slow BMC-class agents. Pre-launching it as an
    # asyncio Task here means it runs concurrently with every
    # downstream provider's `await client.get(...)` (each httpx
    # call yields to the event loop, so the SNMP probe gets to
    # advance during their wait_for / sleep / read points). Total
    # wall-clock for the handler becomes roughly max(SNMP_budget,
    # other_providers_sum) instead of SNMP + others, fitting under
    # the upstream proxy_read_timeout (~60s default on Nginx Proxy
    # Manager) even when the iDRAC pushes against the SNMP budget.
    # Result is awaited at the bottom of the handler in the SNMP
    # block where the response shape is built.
    snmp_task = None
    snmp_meta: dict[str, Any] = {}
    if "snmp" in active:
        from logic import snmp as _snmp
        if not _snmp.has_snmp_support():
            providers_raw["snmp"] = {"_error": "pysnmp not installed"}
        else:
            _raw_row_snmp_kick = record.get("snmp")
            row_snmp_kick: dict = _raw_row_snmp_kick if isinstance(_raw_row_snmp_kick, dict) else {}
            try:
                sn_aliases_kick = json.loads(
                    get_setting(Settings.SNMP_ALIASES, "{}") or "{}"
                )
                if not isinstance(sn_aliases_kick, dict):
                    sn_aliases_kick = {}
            except ValueError:
                sn_aliases_kick = {}
            # Same HARD-GATE as `_merge_one_host` — alias OR snmp_name
            # resolves the target (no bare-id fallthrough), AND
            # `record.snmp.enabled === true` is required. Hosts without
            # SNMP enrolled leave snmp_task as None, the panel hides
            # the slot.
            # Canonical SNMP resolver chain — matches the live sampler /
            # `_merge_one_host` / `api_hosts_test` paths:
            # `aliases → snmp_name → address → SKIP`. Pre-fix this
            # debug-side kickoff stopped at `snmp_name` and never
            # consulted the curated `address` field, so address-only
            # SNMP hosts had no providers_raw.snmp output in /api/hosts/debug
            # even though the live sampler probed them correctly.
            target_kick = (
                sn_aliases_kick.get(record["id"])
                or (record.get("snmp_name") or "").strip()
                or (record.get("address") or "").strip()
                or ""
            )
            enabled_kick = row_snmp_kick.get("enabled") is True
            if target_kick and enabled_kick:
                community_kick = ((row_snmp_kick.get("community") or "").strip()
                                  or (get_setting(Settings.SNMP_DEFAULT_COMMUNITY) or "public"))
                version_kick = (((row_snmp_kick.get("version") or "").strip().lower())
                                or (get_setting(Settings.SNMP_DEFAULT_VERSION) or "v2c").lower()
                                or "v2c")
                try:
                    port_kick = int(row_snmp_kick.get("port")
                                    or tuning.tuning_int(Tunable.SNMP_DEFAULT_PORT))
                except (TypeError, ValueError):
                    port_kick = 161
                v3_user_kick = ((row_snmp_kick.get("v3_user") or "").strip()
                                or get_setting(Settings.SNMP_V3_USER) or "")
                v3_auth_kick = ((row_snmp_kick.get("v3_auth_key") or "").strip()
                                or get_setting(Settings.SNMP_V3_AUTH_KEY) or "")
                v3_priv_kick = ((row_snmp_kick.get("v3_priv_key") or "").strip()
                                or get_setting(Settings.SNMP_V3_PRIV_KEY) or "")
                # Per-host walk_concurrency override — Dell iDRAC9 /
                # iDRAC10 and other server-class BMCs handle parallel
                # queries fine and benefit dramatically from
                # concurrency > 1. The safety-floor concurrency=1
                # default is for low-power embedded snmpd's that drop
                # UDP packets at higher concurrency.
                walk_conc_kick = row_snmp_kick.get("walk_concurrency")
                try:
                    walk_conc_kick = int(walk_conc_kick) if walk_conc_kick else None
                except (TypeError, ValueError):
                    walk_conc_kick = None
                # Per-host vendor MIB selector. None = auto-detect from
                # sysDescr; explicit list = bypass auto-detect.
                vendors_kick = _clean_vendors_input(row_snmp_kick.get("vendors"))
                # Per-host wall_clock_budget override capped at
                # the debug-path ceiling. The DEBUG-PATH budget is
                # deliberately tighter than the sampler-path budget
                # because the debug panel traverses
                # browser → NPM → OmniGrid (NPM's `proxy_read_timeout`
                # default is 60s; raising the global SNMP budget above
                # that surfaces as HTTP 504 from NPM, NOT a useful
                # error). The internal sampler path runs lifespan-side,
                # never touches NPM, so its budget is uncapped via the
                # global tunable. Operators with a 120s+ global
                # tunable have set it for the sampler — the debug
                # panel ceiling stays at 50s so the proxied request
                # always completes within the NPM window. Per-host
                # override can DECREASE the budget below 50s but not
                # raise it above. The operator's recovery for slow
                # iDRAC chassis is to bump the per-host
                # `snmp.walk_concurrency` (the probe finishes faster),
                # NOT to raise the budget — the error message already
                # prompts that path.
                wcb_kick = row_snmp_kick.get("wall_clock_budget")
                try:
                    wcb_kick_f = float(wcb_kick) if wcb_kick else None
                except (TypeError, ValueError):
                    wcb_kick_f = None
                _DEBUG_BUDGET_CAP = 50.0
                wcb_resolved = (
                    min(_DEBUG_BUDGET_CAP, wcb_kick_f)
                    if wcb_kick_f else _DEBUG_BUDGET_CAP
                )
                snmp_task = asyncio.create_task(_snmp.probe_snmp(
                    target_kick,
                    community=community_kick,
                    version=version_kick,
                    port=port_kick,
                    v3_user=v3_user_kick,
                    v3_auth_key=v3_auth_kick,
                    v3_priv_key=v3_priv_kick,
                    walk_concurrency=walk_conc_kick,
                    vendors=vendors_kick,
                    timeout=8.0,
                    active_sources=active,
                    verbose=True,
                    bypass_cooldown=True,
                    wall_clock_budget=wcb_resolved,
                ))
                snmp_meta = {
                    "target": target_kick,
                    "community": community_kick,
                    "version": version_kick,
                    "port": port_kick,
                    "v3_user": v3_user_kick,
                    "v3_auth_set": bool(v3_auth_kick),
                    "v3_priv_set": bool(v3_priv_kick),
                    # Per-host override + global tunable so the operator
                    # can see WHICH value the probe used. None = "no
                    # per-host override, fell back to the global
                    # tunable" — the resolved field shows the actual
                    # value used inside probe_snmp.
                    "walk_concurrency": walk_conc_kick,
                    "walk_concurrency_global": int(
                        tuning.tuning_int(Tunable.SNMP_PER_HOST_WALK_CONCURRENCY)
                    ),
                }

    # ---- Beszel --------------------------------------------------
    if "beszel" in active and record.get("beszel_name"):
        hub_url = get_setting(Settings.BESZEL_HUB_URL) or ""
        ident = get_setting(Settings.BESZEL_IDENTITY) or ""
        passw = get_setting(Settings.BESZEL_PASSWORD) or ""
        verify = (get_setting(Settings.BESZEL_VERIFY_TLS, "true") or "true").lower() == "true"
        if hub_url and ident and passw:
            try:
                async with httpx.AsyncClient(verify=verify, timeout=8.0) as client:
                    token = await _beszel.get_token(client, hub_url, ident, passw)
                    try:
                        records = await _beszel.fetch_systems(client, hub_url, token)
                    except PermissionError:
                        token = await _beszel.get_token(
                            client, hub_url, ident, passw, force_refresh=True,
                        )
                        records = await _beszel.fetch_systems(client, hub_url, token)
                    latest_stats: dict = {}
                    try:
                        latest_stats = await _beszel.fetch_latest_stats(
                            client, hub_url, token,
                        )
                    except Exception as e:
                        latest_stats = {"_fetch_error": str(e)}
                target = (record["beszel_name"] or "").strip()
                match = None
                for rec in records:
                    info = rec.get("info") or {}
                    host_key = (
                        (rec.get("host") or "").strip()
                        or (info.get("h") or "").strip()
                        or (rec.get("name") or "").strip()
                    )
                    if host_key == target:
                        match = rec
                        break
                if match:
                    rec_id = match.get("id") or ""
                    stats_row = latest_stats.get(rec_id) if isinstance(latest_stats, dict) else None
                    providers_raw["beszel"] = {
                        "match_key": target,
                        "record": match,
                        "stats_row": stats_row,
                    }
                    providers_normalized["beszel"] = _beszel.extract_stats(
                        match.get("info") or {}, stats_row,
                    )
                else:
                    known = sorted((
                        (r.get("host") or (r.get("info") or {}).get("h") or r.get("name") or "")
                        for r in records
                    ), key=str.lower)
                    providers_raw["beszel"] = {
                        "_error": f"no record matched beszel_name={target!r}",
                        "known_host_keys": known[:25],
                    }
            except Exception as e:
                providers_raw["beszel"] = {"_error": str(e)}
        else:
            providers_raw["beszel"] = {"_error": "Beszel creds not configured"}

    # ---- Pulse ---------------------------------------------------
    if "pulse" in active and record.get("pulse_name"):
        pulse_url = get_setting(Settings.PULSE_URL) or ""
        pulse_tok = get_setting(Settings.PULSE_TOKEN) or ""
        verify = (get_setting(Settings.PULSE_VERIFY_TLS, "true") or "true").lower() == "true"
        if pulse_url and pulse_tok:
            try:
                async with httpx.AsyncClient(verify=verify, timeout=8.0) as client:
                    state = await _pulse.fetch_state(client, pulse_url, pulse_tok)
                probe = await _pulse.probe_pulse(
                    pulse_url, pulse_tok, verify_tls=verify,
                )
                normalized_match = _pulse.lookup(
                    probe.get("hosts") or {}, record["pulse_name"],
                )
                target_lc = (record["pulse_name"] or "").strip().lower()
                # Node-shaped match first (exact hostname). Then fall
                # through to any guest whose name / vmid matches.
                raw_match = None
                for n in (state.get("nodes") or []):
                    if not isinstance(n, dict):
                        continue
                    name = (n.get("node") or n.get("name") or "").strip().lower()
                    if name == target_lc:
                        raw_match = {"kind": "node", "data": n}
                        break
                if raw_match is None:
                    # Shallow walk of common guest containers — enough
                    # for a debug dump without reproducing probe_pulse's
                    # full recursive harvest.
                    candidates: list = []
                    for key in ("vms", "containers", "guests", "lxc", "qemu"):
                        v = state.get(key)
                        if isinstance(v, list):
                            candidates.extend(v)
                    _raw_pve = state.get("pve")
                    pve: dict = _raw_pve if isinstance(_raw_pve, dict) else {}
                    for key in ("vms", "containers", "guests", "lxc", "qemu"):
                        v = pve.get(key) if isinstance(pve, dict) else None
                        if isinstance(v, list):
                            candidates.extend(v)
                    for g in candidates:
                        if not isinstance(g, dict):
                            continue
                        name = (g.get("name") or g.get("hostname") or g.get("id") or "").strip().lower()
                        vmid = str(g.get("vmid") or "").strip().lower()
                        if name == target_lc or vmid == target_lc:
                            raw_match = {"kind": g.get("type") or "guest", "data": g}
                            break
                providers_raw["pulse"] = {
                    "match_key": record["pulse_name"],
                    "state_top_keys": sorted(state.keys()) if isinstance(state, dict) else [],
                    "nodes_count": len(state.get("nodes") or []),
                    "matched_raw": raw_match,
                }
                providers_normalized["pulse"] = normalized_match
            except Exception as e:
                providers_raw["pulse"] = {"_error": str(e)}
        else:
            providers_raw["pulse"] = {"_error": "Pulse creds not configured"}

    # ---- node-exporter -------------------------------------------
    if "node_exporter" in active and record.get("ne_url"):
        url_input = record["ne_url"]
        # Normalise the operator-supplied URL the same way probe_node()
        # does so the "Raw" debug dump shows real metric text, not the
        # HTML landing page that bare host:port returns.
        url_canonical = _ne.normalise_ne_url(url_input)
        # operator-tunable NE probe timeout.
        _ne_timeout = tuning.tuning_int(Tunable.NODE_EXPORTER_PROBE_TIMEOUT_SECONDS)
        try:
            async with httpx.AsyncClient(verify=False, timeout=float(_ne_timeout)) as client:
                r = await client.get(url_canonical)
                r.raise_for_status()
                text = r.text
                stats = await _ne.probe_node(client, url_input)
            lines = text.splitlines()
            # Cap the sample — a loaded node-exporter can emit thousands
            # of metric lines; operators want a taste, not a dump.
            providers_raw["node_exporter"] = {
                "url_input": url_input,
                "url_canonical": url_canonical,
                "size_bytes": len(text),
                "line_count": len(lines),
                "sample_lines": lines[:80],
                # Last 5 host_net_samples rows for this host. Lets an
                # operator confirm the NE-net fallback sampler is
                # filling the series at the expected cadence; if this
                # is empty but the exporter returns non-zero rx/tx
                # totals, the sampler hasn't run yet (first 5-min tick)
                # or every delta has been rejected by sanity bounds.
                "recent_net_samples": _host_net_sampler.last_samples(record["id"]),
                # Last 5 host_metrics_samples rows for this host. The
                # sampler writes one row per STATS_SAMPLE_INTERVAL
                # (default 5 min) when NE returns meaningful gauges or
                # sane-bounded counter deltas; see
                # logic.host_metrics_sampler._compute_row.
                "recent_metrics_samples": _host_metrics_sampler.last_samples(record["id"]),
            }
            providers_normalized["node_exporter"] = stats
        except Exception as e:
            providers_raw["node_exporter"] = {"_error": str(e)}

    # ---- Webmin --------------------------------------------------
    if "webmin" in active:
        try:
            wm_aliases = json.loads(get_setting(Settings.WEBMIN_ALIASES, "{}") or "{}")
            if not isinstance(wm_aliases, dict):
                wm_aliases = {}
        except ValueError:
            wm_aliases = {}
        wm_url = (wm_aliases.get(record["id"]) or "").strip().rstrip("/")
        user = get_setting(Settings.WEBMIN_USER) or ""
        passw = get_setting(Settings.WEBMIN_PASSWORD) or ""
        verify = (get_setting(Settings.WEBMIN_VERIFY_TLS, "false") or "false").lower() == "true"
        if not wm_url:
            # No Webmin URL mapped for this host — that's an
            # intentional "this host doesn't use Webmin" state, not an
            # error. Leave providers_raw["webmin"] as None so the
            # debug panel's hasDebugData() wrapper hides the block
            # entirely instead of surfacing a misleading error chip.
            pass
        elif not (user and passw):
            providers_raw["webmin"] = {"_error": "Webmin creds not configured"}
        else:
            from logic import webmin as _webmin
            try:
                r = await _webmin.probe_webmin(
                    wm_url, user, passw, verify_tls=verify, timeout=8.0,
                    active_sources=active,
                )
                providers_raw["webmin"] = {
                    "url": wm_url,
                    "hosts_keys": sorted((r.get("hosts") or {}).keys()),
                    "partial_errors": r.get("partial_errors") or [],
                    "error": r.get("error"),
                }
                if r.get("hosts"):
                    providers_normalized["webmin"] = next(iter(r["hosts"].values()))
            except Exception as e:
                providers_raw["webmin"] = {"_error": str(e)}

    # ---- Ping — most recent samples + the resolved sampler
    #    target so the operator can see exactly what address the
    #    probe is hitting (DNS failure debugging). Only renders
    #    when ping is in active AND this host is opted in. -------
    if "ping" in active and bool((record.get("ping") or {}).get("enabled", False)):
        try:
            from logic import ping_sampler as _ping_sampler_dbg
            from logic import ping as _ping_dbg
            samples = _ping_sampler_dbg.last_samples(record["id"]) or []
            # Replicate the sampler's target-resolution chain so the
            # debug surface shows the same `host` the probe is using.
            ping_cfg = (record.get("ping") or {}) if isinstance(record.get("ping"), dict) else {}
            ssh_cfg = (record.get("ssh") or {}) if isinstance(record.get("ssh"), dict) else {}
            target = (
                (record.get("address") or "").strip()
                or (ping_cfg.get("host") or "").strip()
                or (ssh_cfg.get("fqdn") or "").strip()
                or (ssh_cfg.get("host") or "").strip()
                or record["id"]
            )
            providers_raw["ping"] = {
                "target": target,
                "port": ping_cfg.get("port"),
                "transport": ping_cfg.get("transport") or "(global default)",
                "icmp_supported": _ping_dbg.has_icmp_support(),
                "samples_count": len(samples),
                "last_samples": samples,
            }
            if samples:
                last = samples[0]
                stats = _ping_dbg.to_host_stats({
                    "alive": last.get("alive"),
                    "rtt_ms": last.get("rtt_ms"),
                    "loss_pct": last.get("loss_pct"),
                })
                if stats:
                    providers_normalized["ping"] = stats
        except Exception as e:
            providers_raw["ping"] = {"_error": str(e)}

    # ---- SNMP (await the early-launched probe) -------------------
    # The probe was kicked off at the top of the handler (see "SNMP
    # kickoff (early launch)" block above) so it could run
    # concurrently with the Beszel / Pulse / NE / Webmin / Ping
    # awaits. Now we synchronise on the result and build the response
    # shape. Hosts without SNMP enrolled have snmp_task = None and
    # providers_raw["snmp"] was already set above — we just skip.
    if snmp_task is not None:
        try:
            r = await snmp_task
            providers_raw["snmp"] = {
                "target": snmp_meta["target"],
                "community": snmp_meta["community"],
                "version": snmp_meta["version"],
                "port": snmp_meta["port"],
                "v3_user": snmp_meta["v3_user"],
                "v3_auth_set": snmp_meta["v3_auth_set"],
                "v3_priv_set": snmp_meta["v3_priv_set"],
                "hosts_keys": sorted((r.get("hosts") or {}).keys()),
                "error": r.get("error"),
                # Full probed data: every parsed OID, per-row
                # storage table (RAM + disks), per-row interface
                # counters, plus a walk-summary header so operators
                # can see at a glance which OID families the agent
                # answered.
                "raw": r.get("raw") or {},
            }
            if r.get("hosts"):
                providers_normalized["snmp"] = next(iter(r["hosts"].values()))
        except Exception as e:  # noqa: BLE001
            providers_raw["snmp"] = {"_error": str(e)}

    # ---- Merged (best-of) ----------------------------------------
    merged: dict = {}
    # Order matches the runtime merge order in `_merge_one_host` /
    # `gather.py`: Pulse → SNMP → Beszel → node-exporter → Webmin.
    # Keeps the debug panel's "merged" view byte-identical to what the
    # SPA shows on the live row.
    for src in ("pulse", "snmp", "beszel", "node_exporter", "webmin"):
        stats = providers_normalized.get(src)
        if stats:
            _merge_best(merged, stats)

    # ---- Rendered — what `_shape_host_api_row` would emit for this
    # host given the merged dict we just built. Pre-fix this called
    # `api_hosts()` (full fleet re-probe, then `next(... if h.id == id)`)
    # which fired EVERY provider against EVERY curated host on every
    # debug request — a 200-host fleet then re-probed every neighbour
    # before returning, easily blowing past NPM's 60s proxy_read_timeout.
    # The shape helper is purely a synchronous projection of merged +
    # per-host providers_hit, so we can derive `rendered` without any
    # extra network probe.
    try:
        providers_hit = sorted(
            p for p, raw in providers_raw.items()
            if raw is not None and not (
                isinstance(raw, dict) and "_error" in raw and len(raw) == 1
            )
        )
        rendered = _shape_host_api_row(
            record, merged, providers_hit,
            any_provider_enabled=bool(active),
            active=active,
        )
    except Exception as e:
        rendered = {"_error": str(e)}

    # Per-host active providers — global `active` list intersected
    # with what's actually mapped on THIS host's curated config.
    # Without this, the debug panel's "Active providers" row showed
    # the operator the GLOBAL set even on a row that only had ping
    # enabled — misleading, because the other providers wouldn't
    # actually probe this host. Operator-reported on the ftth row
    # (ping-only) showing "beszel, node_exporter, ping, pulse".
    host_active = sorted(
        p for p in active
        if (p == "beszel" and (record.get("beszel_name") or "").strip())
        or (p == "pulse" and (record.get("pulse_name") or "").strip())
        or (p == "node_exporter" and (record.get("ne_url") or "").strip())
        or (p == "webmin" and (record.get("webmin_name") or "").strip())
        or (p == "ping" and bool((record.get("ping") or {}).get("enabled", False)))
        # SNMP is "active for this host" only when (a) the operator has
        # mapped a probe target (alias OR per-row `snmp_name` OR the
        # shared `address` field) AND (b) the per-row `snmp.enabled
        # === True` opt-in flag is set. The probe-side gate in
        # `_merge_one_host` uses the canonical `aliases → snmp_name →
        # address → SKIP` chain — this gate must accept the same
        # alternatives or the debug panel will hide SNMP rows for
        # address-only hosts even when the live sampler probes them
        # successfully.
        or (p == "snmp" and bool(
            isinstance(record.get("snmp"), dict)
            and record["snmp"].get("enabled") is True
            and (
                (record.get("snmp_name") or "").strip()
                or (record.get("address") or "").strip()
            )
        ))
    )
    # Per-host counters — operator-requested addition. Surfaces
    # failure-state retry counters, per-provider pause / last-ok rows,
    # and time-series row counts so operators can debug "why is my host
    # paused" / "why is my chart empty" without poking the SQLite DB
    # directly.
    counters: dict = {}
    try:
        counters["failure_state"] = _failure_state_for_host(id)
    except Exception as e:
        counters["failure_state"] = {"_error": str(e)}
    try:
        counters["provider_pause_state"] = _provider_pause_state_for_host(id)
    except Exception as e:
        counters["provider_pause_state"] = {"_error": str(e)}
    try:
        with db_conn() as c:
            # host_snmp_samples — SNMP probe history depth.
            row = c.execute(
                "SELECT COUNT(*), MAX(ts), MIN(ts) "
                "FROM host_snmp_samples WHERE host_id=?",
                (id,),
            ).fetchone()
            counters["snmp_samples"] = {
                "count": int(row[0] or 0),
                "newest_ts": (int(row[1]) if row[1] is not None else None),
                "oldest_ts": (int(row[2]) if row[2] is not None else None),
            }
            # host_snmp_iface_samples — per-port history depth.
            row2 = c.execute(
                "SELECT COUNT(*), COUNT(DISTINCT ifname), MAX(ts) "
                "FROM host_snmp_iface_samples WHERE host_id=?",
                (id,),
            ).fetchone()
            counters["snmp_iface_samples"] = {
                "rows": int(row2[0] or 0),
                "ifaces": int(row2[1] or 0),
                "newest_ts": (int(row2[2]) if row2[2] is not None else None),
            }
            # host_metrics_samples — node-exporter sampler history.
            row3 = c.execute(
                "SELECT COUNT(*), MAX(ts) "
                "FROM host_metrics_samples WHERE host_id=?",
                (id,),
            ).fetchone()
            counters["ne_samples"] = {
                "count": int(row3[0] or 0),
                "newest_ts": (int(row3[1]) if row3[1] is not None else None),
            }
            # ping_samples — TCP/ICMP probe history.
            row4 = c.execute(
                "SELECT COUNT(*), MAX(ts), "
                "       SUM(CASE WHEN alive=1 THEN 1 ELSE 0 END), "
                "       SUM(CASE WHEN alive=0 THEN 1 ELSE 0 END) "
                "FROM ping_samples WHERE host_id=?",
                (id,),
            ).fetchone()
            counters["ping_samples"] = {
                "count": int(row4[0] or 0),
                "newest_ts": (int(row4[1]) if row4[1] is not None else None),
                "alive": int(row4[2] or 0),
                "down": int(row4[3] or 0),
            }
            # host_snapshots — last persistence write for this host.
            row5 = c.execute(
                "SELECT ts, length(data) FROM host_snapshots WHERE host=?",
                (id,),
            ).fetchone()
            if row5:
                counters["snapshot"] = {
                    "ts": float(row5[0] or 0.0),
                    "size_bytes": int(row5[1] or 0),
                }
            else:
                # Try short-hostname fallback (mirrors the snapshot
                # lookup tolerance in apply_host_snapshot_fallback).
                # LIKE pattern needs ESCAPE so a hostname containing
                # `_` (e.g. `web_01`) doesn't match unrelated hosts via
                # the underscore-wildcard. Same security drift class
                # as the bulk-resume + timeline sites.
                short = (id or "").split(".", 1)[0]
                row5b = c.execute(
                    "SELECT host, ts, length(data) FROM host_snapshots "
                    "WHERE host=? OR host LIKE ? ESCAPE '\\'",
                    (short, _sqlite_like_escape(short) + ".%"),
                ).fetchone()
                if row5b:
                    counters["snapshot"] = {
                        "ts": float(row5b[1] or 0.0),
                        "size_bytes": int(row5b[2] or 0),
                        "host_key": row5b[0],
                    }
                else:
                    counters["snapshot"] = None
    except Exception as e:
        counters["_db_error"] = str(e)

    # ---- Samples in window — per-time-range diagnostic. -----------
    # Operator-flagged: charts can show "cut" data in the past hour
    # (gaps in the polyline, missing buckets at the head / tail).
    # The counters above show TOTAL row counts since the host's first
    # sample — not useful when diagnosing "why is the past hour
    # missing data?". This block answers exactly that question:
    # for each time-series table, how many rows landed within the
    # `since_hours` window, what's the most-recent / oldest
    # timestamp inside it, and the median gap between consecutive
    # samples (lets the operator see whether the sampler's been
    # ticking on cadence or skipping). Window mirrors the chart
    # range picker (1 / 6 / 24 / 168 hours) so the SPA passes the
    # same value the user has selected and the count matches
    # what's plotted.
    window_hours = max(1, min(168, int(since_hours or 1)))
    since_ts = int(time.time() - window_hours * 3600)
    samples_in_window: dict = {"hours": window_hours, "since_ts": since_ts}
    try:
        with db_conn() as c:
            for table in (
                    "host_snmp_samples", "host_snmp_iface_samples",
                    "host_metrics_samples", "ping_samples",
                    "host_net_samples",
                    # Pulse / Webmin / Beszel each write to their own
                    # per-provider sample tables. Beszel was added under
                    # the "every host-stats provider must have a local
                    # sample store" rule — pre-fix it was the read-
                    # through-only outlier and chart cuts followed.
                    "host_pulse_samples", "host_webmin_samples",
                    "host_beszel_samples",
            ):
                try:
                    row = c.execute(
                        f"SELECT COUNT(*), MIN(ts), MAX(ts) "
                        f"FROM {table} WHERE host_id = ? AND ts >= ?",
                        (id, since_ts),
                    ).fetchone()
                except Exception as e:
                    samples_in_window[table] = {"_error": str(e)}
                    continue
                count = int(row[0] or 0)
                oldest = int(row[1]) if row[1] is not None else None
                newest = int(row[2]) if row[2] is not None else None
                # Median gap between consecutive samples — a flat
                # cadence sampler should produce a near-constant gap
                # (~5 min for `host_metrics_samples`, ~1 min for
                # `ping_samples`, etc.). A median gap >> the
                # configured interval flags a sampler that's been
                # skipping ticks; a median gap == the interval is
                # healthy. SQLite doesn't have a built-in median, so
                # we lift up to 200 timestamps and compute it Python-
                # side. Cap at 200 to bound the read for a 7-day
                # ping window which can carry ~10000 rows.
                gaps_median: Optional[int] = None
                if count >= 2:
                    try:
                        ts_rows = c.execute(
                            f"SELECT ts FROM {table} "
                            f"WHERE host_id = ? AND ts >= ? "
                            f"ORDER BY ts ASC LIMIT 200",
                            (id, since_ts),
                        ).fetchall()
                        ts_list = [int(r[0]) for r in ts_rows]
                        gaps = [b - a for a, b in zip(ts_list, ts_list[1:])]
                        if gaps:
                            gaps.sort()
                            mid = len(gaps) // 2
                            gaps_median = (
                                gaps[mid] if len(gaps) % 2 == 1
                                else (gaps[mid - 1] + gaps[mid]) // 2
                            )
                    except (IndexError, TypeError, ValueError):
                        gaps_median = None
                samples_in_window[table] = {
                    "count": count,
                    "newest_ts": newest,
                    "oldest_ts": oldest,
                    "median_gap_s": gaps_median,
                    "newest_age_s": (
                        int(time.time() - newest) if newest is not None
                        else None
                    ),
                }
    except Exception as e:
        samples_in_window["_db_error"] = str(e)

    counters["samples_in_window"] = samples_in_window

    # EVERY tunable, surfaced live-resolved. Pre-fix this was an
    # explicit list of ~36 keys grouped by provider — discoverable but
    # incomplete: port-scan / SSH / AI / config tunables weren't
    # included, so an operator asking the AI "what's the AI fallback
    # max depth?" or "what's the SSH WS heartbeat?" got a non-answer
    # because the value never reached the AI palette context.
    # Reading the canonical TUNABLES table verbatim makes the panel
    # exhaustive: providers (Beszel / Pulse / NE / Webmin / SNMP /
    # Ping) + port-scan + SSH + AI integration + config knobs all
    # appear automatically. Adding a new tunable requires a single
    # entry in `logic/tuning.py:TUNABLES` and it's surfaced here on
    # the next request — no list-edit drift class.
    from logic.tuning import tuning_int as _tuning_int, TUNABLES as _TUNABLES
    counters["tunables"] = {}
    for key in _TUNABLES.keys():
        try:
            counters["tunables"][key] = _tuning_int(key)
        except (ValueError, TypeError, KeyError):
            # Bounds-clamp / DB error; skip silently rather than
            # poisoning the whole tunables map for one bad knob.
            pass

    # Strip the sampler-internal `host_services_raw` blob from the
    # merged dict before serialising. It's a 50-200-row systemd unit
    # list that the lifespan `host_beszel_sampler` consumes once-per-
    # tick and persists to `host_beszel_services`; downstream
    # consumers (this debug response, `_shape_host_api_row`, the AI
    # palette context) read the rolled summary or hit the dedicated
    # /api/hosts/{id}/beszel/services endpoint instead. Leaving the
    # raw list in the merged dict bloats the debug response by
    # several KB on hosts with many tracked units AND risks accidental
    # leak via any future code path that ships merged verbatim.
    merged_for_debug = (
        {k: v for k, v in merged.items() if k != "host_services_raw"}
        if isinstance(merged, dict) else merged
    )
    return {
        "host_record": record,
        "active_providers": host_active,
        "active_providers_global": sorted(active),
        "providers_raw": providers_raw,
        "providers_normalized": providers_normalized,
        "merged": merged_for_debug,
        "rendered": rendered,
        "counters": counters,
    }


# ============================================================================
# SSH console — admin-only remote-command runner for the host drawer.
#
# Surface:
# GET  /api/hosts/{host_id}/ssh/status  — resolved connection params
# POST /api/hosts/{host_id}/ssh/test    — runs `whoami` with a short timeout
# POST /api/hosts/{host_id}/ssh/run     — body {command, dry_run}
#
# Every runner call lands in the history table as op_type='ssh_run' so
# Admin → History carries a complete audit trail. Destructive-command
# typed-confirm (hostname echo) is enforced on the UI — the backend
# merely returns a ``destructive`` flag + matched patterns so the UI
# knows to raise the bar. Backend still always runs dry-run safely.
# ============================================================================
def _ssh_write_audit_row(
    *,
    op_id: str,
    actor: str,
    host_id: str,
    command: str,
    result: dict,
) -> None:
    """Persist one SSH run into the ``history`` table.

    Uses ``op_type='ssh_run'`` so the History view (which filters by
    op_type) naturally surfaces the audit trail alongside updates /
    restarts. The command is sanitised via
    :func:`logic.ssh.sanitize_command_for_audit` before landing — not a
    security boundary (sshd on the target still sees the raw line) but
    keeps long one-liners readable in the UI and masks obvious secret
    flags so a History export isn't a liability on its own.

    Mirrors the direct-insert pattern used by the scheduler's
    gather_refresh / backup runners (see ``logic/schedules.py``) — we
    don't route through ops.persist_history because that bumps a
    Prometheus counter whose label set is keyed to the fixed op_type
    enum. Keep ssh_run out of that counter until we decide the
    dashboards want it.
    """
    from logic import ssh as _ssh
    started = time.time()
    status = "success" if result.get("ok") and not result.get("error") else "error"
    if result.get("dry_run"):
        status = "dry_run"
    error = result.get("error")
    duration = (result.get("duration_ms") or 0) / 1000.0
    events = [
        {
            "ts": time.time(),
            "level": "info" if status in ("success", "dry_run") else "error",
            "msg": (
                f"ssh_run dry_run={bool(result.get('dry_run'))} "
                f"exit={result.get('exit_code')} "
                f"stdout_bytes={len(result.get('stdout') or '')} "
                f"stderr_bytes={len(result.get('stderr') or '')}"
            ),
        }
    ]
    _ops_mod.assert_op_type("ssh_run")
    try:
        with db_conn() as c:
            c.execute(
                "INSERT INTO history "
                "(ts, op_type, target_kind, target_name, target_id, "
                " target_stack, status, duration, events, error, actor) "
                "VALUES (?, ?, 'ssh', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    started, "ssh_run",
                    _ssh.sanitize_command_for_audit(command) or "(empty)",
                    f"{host_id}:{op_id}",
                    None,
                    status, duration,
                    json.dumps(events),
                    error, actor,
                ),
            )
    except Exception as e:
        # Never let audit-log failure break the response — an operator
        # needs to see the result even if the history write blew up.
        print(f"[ssh] audit-log insert failed: {e}")


@app.get("/api/hosts/{host_id}/ssh/status")
async def api_ssh_status(
    host_id: str,
    _admin: AdminUser,
):
    """Return the resolved SSH connection params for one host.

    Does NOT initiate a TCP connection — safe to poll on drawer open.
    Surfaces ``configured`` + ``enabled`` flags the UI uses to gate
    the Run button.
    """
    from logic import ssh as _ssh
    return _ssh.ssh_status(host_id, _load_hosts_config())


@app.post("/api/hosts/{host_id}/ssh/test")
async def api_ssh_test(
    host_id: str,
    request: Request,
    _admin: AdminUser,
):
    """Admin-only: run `whoami` on the host to verify connectivity.

    Persists a history row (``op_type='ssh_run'``) so repeated failed
    tests are visible in the audit trail. Body is ignored — everything
    is keyed off the persisted settings + curated hosts_config row.
    """
    from logic import ssh as _ssh
    result = await _ssh.test_connection(host_id, _load_hosts_config())
    actor = getattr(request.state, "user", None)
    actor_name = getattr(actor, "username", None) or "unknown"
    _ssh_write_audit_row(
        op_id=uuid.uuid4().hex[:8],
        actor=actor_name,
        host_id=host_id,
        command="whoami  # ssh test",
        result=result,
    )
    return result


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/hosts/{host_id}/ssh/run")
async def api_ssh_run(
    host_id: str,
    body: dict,
    request: Request,
    _admin: AdminUser,
):
    """Admin-only: run one command over SSH.

    Body:
        command (str, required)
        dry_run (bool, default true) — false to actually execute

    Always dry-run-safe: the frontend is expected to preflight with
    ``dry_run: true`` and surface the resolved connection before
    offering a "Run for real" button. Backend enforcement is a
    length-cap + destructive-pattern detection; typed-hostname confirm
    is a UI concern. Every call lands in the history table as
    ``op_type='ssh_run'``.
    """
    from logic import ssh as _ssh
    command = (body or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        raise HTTPException(400, "command is required")
    if len(command) > _ssh.MAX_COMMAND_LEN:
        raise HTTPException(
            400,
            f"command exceeds {_ssh.MAX_COMMAND_LEN}-byte cap "
            f"({len(command)} bytes)",
        )
    dry_run = bool((body or {}).get("dry_run", True))
    timeout = (body or {}).get("timeout")
    try:
        timeout_f = float(timeout) if timeout is not None else 30.0
    except (TypeError, ValueError):
        timeout_f = 30.0
    timeout_f = max(1.0, min(timeout_f, 120.0))

    destructive_hits = _ssh.command_is_destructive(command)
    result = await _ssh.run_command(
        host_id=host_id,
        command=command,
        hosts_config=_load_hosts_config(),
        timeout=timeout_f,
        dry_run=dry_run,
    )
    result["destructive"] = destructive_hits
    actor = getattr(request.state, "user", None)
    actor_name = getattr(actor, "username", None) or "unknown"
    _ssh_write_audit_row(
        op_id=uuid.uuid4().hex[:8],
        actor=actor_name,
        host_id=host_id,
        command=command,
        result=result,
    )
    return result


# ----------------------------------------------------------------------------
# Interactive SSH terminal
# Browser <—WSS—> OmniGrid backend <—asyncssh shell—> target host.
#
# Auth: same og_session cookie as every other admin-only API path. The WS
# upgrade is rejected with code=4401 when the cookie is missing / invalid /
# the user isn't admin. Bearer-token auth is intentionally NOT supported
# here — interactive shells are operator workflows; machine clients use
# /api/hosts/{id}/ssh/run.
#
# Audit: a row is written to ``history`` at session-OPEN with status
# ``running`` and updated to ``success`` / ``failed`` at session-CLOSE.
# Keystrokes / shell I/O are NEVER logged (privacy + audit volume) — only
# the open / close events.
#
# Keep-alive: the route pings the WS every ~25s so NPM / Cloudflare idle
# timeouts don't drop a quiet shell. ``open_shell`` already passes
# ``keepalive_interval=15`` to asyncssh on the upstream side.
# ----------------------------------------------------------------------------
def _ssh_terminal_audit_open(
    *,
    host_id: str,
    actor: str,
    resolved: dict,
) -> Optional[int]:
    """Insert the session-OPEN history row. Returns the new rowid or
    ``None`` if the insert failed (audit-log breakage must never block
    the session itself — operator visibility is best-effort by design).
    """
    _ops_mod.assert_op_type("ssh_terminal")
    try:
        with db_conn() as c:
            cur = c.execute(
                "INSERT INTO history "
                "(ts, op_type, target_kind, target_name, target_id, "
                " target_stack, status, duration, events, error, actor) "
                "VALUES (?, ?, 'ssh', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    "ssh_terminal",
                    f"{resolved.get('user') or '?'}@{resolved.get('host') or host_id}",
                    f"{host_id}",
                    None,
                    "running",
                    0.0,
                    json.dumps([{
                        "ts": time.time(),
                        "level": "info",
                        "msg": (
                            f"ssh_terminal start "
                            f"target={resolved.get('user')}@{resolved.get('host')}:{resolved.get('port')}"
                        ),
                    }]),
                    None,
                    actor,
                ),
            )
            return cur.lastrowid
    except Exception as e:
        print(f"[ssh] terminal audit-open insert failed: {e}")
        return None


def _ssh_terminal_audit_close(
    *,
    row_id: Optional[int],
    started_at: float,
    status: str,
    error: Optional[str],
    bytes_in: int,
    bytes_out: int,
) -> None:
    """Update the session-OPEN row to its final state. Fire-and-forget;
    failures are logged but never raised.
    """
    if not row_id:
        return
    duration = max(0.0, time.time() - started_at)
    events = [{
        "ts": time.time(),
        "level": "info" if status == "success" else "error",
        "msg": (
            f"ssh_terminal end status={status} "
            f"bytes_in={bytes_in} bytes_out={bytes_out} "
            f"duration={duration:.1f}s"
        ),
    }]
    try:
        with db_conn() as c:
            c.execute(
                "UPDATE history SET status=?, duration=?, events=?, error=? "
                "WHERE id=?",
                (status, duration, json.dumps(events), error, row_id),
            )
    except Exception as e:
        print(f"[ssh] terminal audit-close update failed: {e}")


# Registered BEFORE the StaticFiles "/" catch-all per CLAUDE.md mount-order
# rule — the catch-all responds to every path and would shadow the
# WebSocket route otherwise.
# noinspection PyTypeChecker,PyUnresolvedReferences
@app.websocket("/api/hosts/{host_id}/ssh/terminal")
async def ws_ssh_terminal(websocket: WebSocket, host_id: str):
    """Bridge a browser WebSocket to a live PTY-backed SSH shell.

    Frame protocol (browser → backend):
      - **binary**   — raw stdin bytes (forwarded verbatim to the shell).
      - **text JSON**  — control message:
            ``{"type": "resize", "cols": N, "rows": M}``
            ``{"type": "ping"}``  (no-op; server pings are separate)

    Frame protocol (backend → browser):
      - **binary**   — raw stdout bytes from the shell.
      - **text JSON**  — control message:
            ``{"type": "ready", "resolved": {...}}``  on shell open.
            ``{"type": "error", "code": "...", "message": "..."}``  fatal.
            ``{"type": "exit",  "code": N}``  shell exited cleanly.

    Cookie auth is enforced at the upgrade — the route REJECTS the
    handshake before ``accept()`` if the caller isn't an admin. Bearer
    tokens are not supported.
    """
    from logic import ssh as _ssh
    # ---- 1) Cookie auth — manual because Depends(require_admin) doesn't
    #       apply to WebSocket routes.
    user = None
    cookie = websocket.cookies.get(auth.COOKIE_NAME)
    if cookie:
        token_id = auth.parse_session_cookie(cookie)
        if token_id:
            try:
                with db_conn() as c:
                    sess = auth.get_active_session(c, token_id)
                    if sess:
                        u = auth.get_user(c, sess["user_id"])
                        if u and not u.disabled:
                            user = u
            except Exception as e:
                print(f"[ssh] terminal auth lookup failed: {e}")
    if user is None:
        # 4401 — RFC-6455 application close-code (4xxx is private use).
        # Starlette rejects the upgrade with HTTP 403 when ``close()`` is
        # called before ``accept()``; that's fine — the browser reads
        # the failed-handshake event and we never burn an audit row on a
        # bogus session. The SPA maps either signal to "session expired".
        await websocket.close(code=4401, reason="auth required")
        return
    if user.role != "admin":
        await websocket.close(code=4403, reason="admin required")
        return

    # ---- 1.5) Origin gate — defence-in-depth against CSWSH. FastAPI's
    #        WebSocket upgrades skip the HTTP middleware's CSRF path,
    #        so admin-only WS routes can't rely on the same
    #        double-submit cookie protection HTTP routes get. The
    #        session cookie's ``SameSite=lax`` attribute blocks most
    #        cross-site WS upgrades on Chromium / Firefox, but
    #        subdomain attacks and custom proxy setups can still
    #        leak the cookie. Reject the upgrade when the browser-
    #        supplied Origin doesn't match the resolved server
    #        origin. ``Origin`` may be empty for some non-browser
    #        callers (e.g. command-line tools that explicitly bypass
    #        it); we treat empty as "no claim made" and accept it
    #        since the admin cookie + role gate already rejected
    #        unauthenticated callers — the Origin gate is purely a
    #        browser-CSWSH defence and a missing header isn't one of
    #        those attack shapes.
    browser_origin = (websocket.headers.get("origin") or "").strip().lower()
    if browser_origin:
        expected_origin = _request_origin(websocket).strip().lower()
        if browser_origin != expected_origin:
            print(
                f"[ssh] terminal Origin mismatch: browser={browser_origin!r} "
                f"expected={expected_origin!r} host_id={host_id!r} user={user.username!r}"
            )
            await websocket.close(code=4403, reason="origin mismatch")
            return

    actor = user.username

    # ---- 2) Resolve SSH params + open the shell.
    hosts_config = _load_hosts_config()
    # Optional initial geometry from the upgrade query string. xterm.js
    # ships a saner first-frame with "actual cols/rows" once it mounts,
    # so this is just a best-guess so the prompt isn't 80x24 for the
    # first redraw on widescreen monitors.
    try:
        init_cols = int(websocket.query_params.get("cols") or 80)
    except (TypeError, ValueError):
        init_cols = 80
    try:
        init_rows = int(websocket.query_params.get("rows") or 24)
    except (TypeError, ValueError):
        init_rows = 24

    await websocket.accept()
    started_at = time.time()
    audit_row_id: Optional[int] = None
    bytes_in = 0  # browser -> shell
    bytes_out = 0  # shell -> browser
    final_status = "success"
    final_error: Optional[str] = None
    conn = None
    proc = None

    try:
        try:
            conn, proc, resolved = await _ssh.open_shell(
                host_id, hosts_config,
                term_cols=init_cols, term_rows=init_rows,
            )
        except _ssh.TerminalConfigError as e:
            await websocket.send_json({
                "type": "error",
                "code": getattr(e, "code", "config"),
                "message": str(e),
            })
            await websocket.close(code=4400, reason=getattr(e, "code", "config"))
            return
        except _ssh.TerminalAuthError as e:
            await websocket.send_json({
                "type": "error",
                "code": getattr(e, "code", "auth_failed"),
                "message": str(e),
            })
            await websocket.close(code=4401, reason="auth_failed")
            return
        except (asyncio.TimeoutError, TimeoutError):
            await websocket.send_json({
                "type": "error", "code": "timeout",
                "message": "SSH connection timed out",
            })
            await websocket.close(code=4500, reason="timeout")
            return
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "code": "connect_failed",
                "message": f"{type(e).__name__}: {e}",
            })
            await websocket.close(code=4500, reason="connect_failed")
            return

        audit_row_id = _ssh_terminal_audit_open(
            host_id=host_id, actor=actor, resolved=resolved,
        )

        # Surface the resolved target back to the SPA so the modal
        # footer can render "user@host:port · SHA256:abc..."
        await websocket.send_json({
            "type": "ready",
            "resolved": {
                "user": resolved.get("user"),
                "host": resolved.get("host"),
                "port": resolved.get("port"),
                "key_fingerprint": resolved.get("key_fingerprint", ""),
                "server_key_fingerprint": resolved.get("server_key_fingerprint", ""),
            },
        })

        # ---- 3) Pump bytes both ways + heartbeat ping. ----
        stop_event = asyncio.Event()

        # noinspection PyUnresolvedReferences
        async def upstream_to_ws():
            """Read shell stdout, send as binary WS frames."""
            nonlocal bytes_out
            try:
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        # EOF — shell exited.
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode(errors="replace")
                    bytes_out += len(chunk)
                    await websocket.send_bytes(chunk)
            except (asyncio.CancelledError, asyncssh.DisconnectError,
                    asyncssh.Error, BrokenPipeError, ConnectionResetError):
                pass
            except (RuntimeError, OSError, ValueError) as upstream_err:
                print(f"[ssh] terminal upstream_to_ws error: "
                      f"{type(upstream_err).__name__}: {upstream_err}")
            finally:
                stop_event.set()

        # noinspection PyTypeChecker,PyUnresolvedReferences
        async def ws_to_upstream():
            """Read WS frames, write to shell stdin or handle controls."""
            nonlocal bytes_in
            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    if "bytes" in msg and msg["bytes"] is not None:
                        data: bytes = msg["bytes"]
                        bytes_in += len(data)
                        try:
                            proc.stdin.write(data)
                        except (BrokenPipeError, ConnectionResetError):
                            break
                    elif "text" in msg and msg["text"] is not None:
                        # Control message — JSON-decoded.
                        try:
                            ctl = json.loads(msg["text"])
                        except (TypeError, ValueError):
                            continue
                        kind = (ctl or {}).get("type")
                        if kind == "resize":
                            _ssh.resize_shell(
                                proc,
                                ctl.get("cols", 80),
                                ctl.get("rows", 24),
                            )
                        elif kind == "ping":
                            # No-op — server pings are separate.
                            continue
                        elif kind == "stdin":
                            # Optional text-mode stdin (some clients
                            # prefer encoding via JSON). Keys "data".
                            data_s = (ctl or {}).get("data") or ""
                            data_b = data_s.encode(errors="replace")
                            bytes_in += len(data_b)
                            try:
                                proc.stdin.write(data_b)
                            except (BrokenPipeError, ConnectionResetError):
                                break
            except WebSocketDisconnect:
                pass
            except (asyncio.CancelledError, asyncssh.DisconnectError,
                    asyncssh.Error, BrokenPipeError, ConnectionResetError):
                pass
            except (RuntimeError, OSError, ValueError) as ws_err:
                print(f"[ssh] terminal ws_to_upstream error: "
                      f"{type(ws_err).__name__}: {ws_err}")
            finally:
                stop_event.set()

        async def heartbeat():
            """WS ping cadence (TUNABLE — `tuning_ssh_ws_heartbeat_seconds`)
            so idle proxies don't drop us. Resolved per-iteration so an
            Admin → Config save takes effect on the NEXT tick without a
            terminal reconnect."""
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(tuning.tuning_int(Tunable.SSH_WS_HEARTBEAT_SECONDS))
                    if stop_event.is_set():
                        break
                    try:
                        # Starlette's WebSocket doesn't expose a public
                        # ping; fall back to a JSON keepalive frame the
                        # client can ignore. Keeps any L7 proxy from
                        # dropping the idle TCP socket.
                        await websocket.send_json({"type": "keepalive", "ts": time.time()})
                    except (RuntimeError, OSError, WebSocketDisconnect):
                        break
            except asyncio.CancelledError:
                pass

        t1 = asyncio.create_task(upstream_to_ws(), name="ssh-term-up")
        t2 = asyncio.create_task(ws_to_upstream(), name="ssh-term-dn")
        t3 = asyncio.create_task(heartbeat(), name="ssh-term-hb")
        try:
            await stop_event.wait()
        finally:
            for t in (t1, t2, t3):
                if not t.done():
                    t.cancel()
            # Drain cancellations.
            for t in (t1, t2, t3):
                try:
                    await t
                except (asyncio.CancelledError, RuntimeError, OSError):
                    pass

        # Try to harvest the shell's exit code so the close frame can
        # surface "exit 0" vs "exit 1". asyncssh exposes this on the
        # process once the channel closes.
        try:
            exit_code: int | None = proc.exit_status
        except AttributeError:
            exit_code = None
        if exit_code not in (None, 0):
            final_error = f"shell exited with code {exit_code}"
        try:
            await websocket.send_json({"type": "exit", "code": exit_code})
        except (RuntimeError, OSError, WebSocketDisconnect):
            pass
        try:
            await websocket.close(reason="shell exited")
        except (RuntimeError, OSError, WebSocketDisconnect):
            pass
    except WebSocketDisconnect:
        # Normal browser-side close (tab closed / network blip). Not an
        # error; final_status stays "success".
        pass
    except (asyncssh.Error, RuntimeError, OSError, ValueError) as sess_err:
        final_status = "failed"
        final_error = f"{type(sess_err).__name__}: {sess_err}"
        print(f"[ssh] terminal session ERROR host={host_id!r}: {sess_err}")
        try:
            await websocket.close(code=4500, reason="internal_error")
        except (RuntimeError, OSError, WebSocketDisconnect):
            pass
    finally:
        # Always close the upstream SSH connection.
        if proc is not None:
            try:
                proc.close()
            except (RuntimeError, OSError, AttributeError):
                pass
        if conn is not None:
            try:
                conn.close()
            except (RuntimeError, OSError, AttributeError):
                pass
            # Per-use read of the SSH conn-close timeout TUNABLE so a
            # Save in Admin → SSH takes effect on the next session
            # teardown without restart. Defensive fallback to legacy 5s
            # on tunable-resolver failure.
            try:
                _ssh_close_to = float(tuning.tuning_int(Tunable.SSH_CLOSE_TIMEOUT_SECONDS))
            except (ValueError, TypeError, KeyError):
                _ssh_close_to = 5.0
            try:
                await asyncio.wait_for(conn.wait_closed(), timeout=_ssh_close_to)
            except (asyncio.TimeoutError, OSError, RuntimeError):
                pass
        _ssh_terminal_audit_close(
            row_id=audit_row_id,
            started_at=started_at,
            status=final_status,
            error=final_error,
            bytes_in=bytes_in,
            bytes_out=bytes_out,
        )
        print(
            f"[ssh] terminal CLOSE host_id={host_id!r} actor={actor!r} "
            f"status={final_status} bytes_in={bytes_in} bytes_out={bytes_out} "
            f"duration={time.time() - started_at:.1f}s"
        )


# Re-export asyncssh for the WS handler's exception handling without
# forcing every other module to import the whole package.
import asyncssh  # noqa: E402,F401  (used inside ws_ssh_terminal handlers)


def _bucket_drawer_series(series: list, hours: int, target_points: int = 120) -> list:
    """Generic time-series bucketing for drawer-chart endpoints.

    Takes a list of dicts where each dict has a `t` (or `ts`) epoch-
    seconds field plus arbitrary metric fields. Returns a bucketed list
    of ~``target_points`` points evenly spread across the window. Field
    handling per-bucket:

    - **scalar numeric** (int/float, not bool): averaged across samples.
    - **dict of numeric leaves** (e.g. Beszel `temps: {cpu_thermal: 49}`):
      averaged per-leaf so the chart's per-sensor lines still render.
      Sensor keys that appear in only some samples in the bucket are
      averaged across the samples that have them.
    - **list of numeric elements** (e.g. `cpus: [10, 10, 8]`,
      `la: [0.19, 0.3, 0.43]`): element-wise averaged; output list
      length = max input length, missing positions averaged across the
      samples that had them.
    - **anything else** (list of dicts, dict of dicts, strings, bools,
      JSON blobs): last-in-bucket wins so the structural shape survives
      and the chart's downstream consumers still find their nested keys.
      Slight fidelity loss vs averaging but the chart STAYS FUNCTIONAL
      across 24h / 7d windows (pre-fix the field was dropped entirely
      and the chart fell through to "Collecting data").

    Short windows (≤2h) AND already-small series (len ≤ target) skip
    the bucket pass entirely — they're already chart-friendly. Buckets
    with no scalar-numeric data AND no dict/list metric data are
    dropped so the SPA's time-based gap detection renders them as real
    breaks in the line.

    Sampler-floor + min-bucket-width is 60s so we never produce a
    bucket smaller than a typical sampler tick.
    """
    if not series or hours <= 2 or len(series) <= target_points:
        return series
    bucket_s = max(60, int((hours * 3600) / target_points))

    # Discover field-kind by scanning the WHOLE series for the first
    # non-empty value per key. Pre-fix this only looked at sample[0] —
    # fields that are sparse-populated (Beszel `temps` / `gpus` — agent
    # omits the field on ticks where the sensor wasn't readable) got
    # classified as "other" when sample[0] happened to be empty / None,
    # then last-in-bucket wins routed empty {} into output buckets and
    # the chart saw zero sensors.
    #
    # Per-key kind classification:
    #   "scalar" → sum + count, AVG at emit
    #   "dict"   → per-leaf sum + count, AVG dict at emit
    #   "list"   → per-index sum + count, AVG list at emit
    #   "other"  → last-in-bucket wins (kept for structural fields like
    #              list-of-dicts e.g. `gpus`, bool flags, strings)
    def _classify_key(series_local: list, key: str) -> str:
        """Walk series_local sample-by-sample looking for the first
        non-null value at `key`; return one of scalar/dict/list/other."""
        for sample in series_local:
            if not isinstance(sample, dict):
                continue
            val = sample.get(key)
            if val is None:
                continue
            if isinstance(val, bool):
                return "other"
            if isinstance(val, (int, float)):
                return "scalar"
            if isinstance(val, dict):
                # Need at least ONE leaf to classify as dict-of-numerics.
                # All values must be numeric.
                if not val:
                    continue  # empty dict — keep scanning for a populated tick
                if all(isinstance(x, (int, float)) and not isinstance(x, bool)
                       for x in val.values()):
                    return "dict"
                return "other"
            if isinstance(val, list):
                if not val:
                    continue  # empty list — keep scanning
                if all(isinstance(x, (int, float)) and not isinstance(x, bool)
                       for x in val):
                    return "list"
                return "other"
            return "other"
        # Entirely empty / null across the whole series — "other" so an
        # empty value lands in `other_last` and the row stays consistent
        # with the source.
        return "other"

    # Collect every key that appears in ANY sample, not just sample[0].
    all_keys: set[str] = set()
    for r in series:
        if isinstance(r, dict):
            for k in r.keys():
                if k not in ("t", "ts"):
                    all_keys.add(k)
    kinds: dict[str, str] = {k: _classify_key(series, k) for k in all_keys}
    # Homogeneous accumulator: every value in `b` is itself a dict, so
    # PyCharm narrows sub-key accesses to dict (not the dict|int union
    # the mixed-shape version inferred). last_ts lives in a parallel
    # dict keyed on bucket-start-ts so it can stay typed as int without
    # bleeding union types into `b`.
    # Unannotated bare-dict (rather than dict[int, dict[str, Any]])
    # because PyCharm's strict-mode inference produced spurious
    # "Expected type 'int', got 'str'" warnings on the sub-key writes
    # — the annotation propagated the outer-key int type into the
    # inner accumulator's key inference. Bare `dict` keeps inference
    # off entirely. Runtime correctness is unchanged.
    buckets: dict = {}
    bucket_last_ts: dict[int, int] = {}
    for r in series:
        ts = int(r.get("t") or r.get("ts") or 0)
        if not ts:
            continue
        bts = (ts // bucket_s) * bucket_s
        b = buckets.get(bts)
        if b is None:
            b = {
                "scalar_sum": {}, "scalar_n": {},
                "dict_sum": {}, "dict_n": {},
                "list_sum": {}, "list_n": {},
                "other_last": {},
            }
            buckets[bts] = b
        # last_ts tracks the latest raw ts inside the bucket so the
        # emitted point lands at the newest sample inside the bucket
        # rather than the bucket center — chartFreshness on the SPA
        # reads the chart's tail to decide age; centre-emit made
        # wider windows misleadingly stale even when the most recent
        # raw sample was seconds old.
        prev_last = bucket_last_ts.get(bts, 0)
        if ts > prev_last:
            bucket_last_ts[bts] = ts
        for k, kind in kinds.items():
            v = r.get(k)
            if v is None:
                continue
            if kind == "scalar":
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                b["scalar_sum"][k] = b["scalar_sum"].get(k, 0.0) + fv
                b["scalar_n"][k] = b["scalar_n"].get(k, 0) + 1
            elif kind == "dict" and isinstance(v, dict):
                ds = b["dict_sum"].setdefault(k, {})
                dn = b["dict_n"].setdefault(k, {})
                for leaf, lv in v.items():
                    try:
                        flv = float(lv)
                    except (TypeError, ValueError):
                        continue
                    ds[leaf] = ds.get(leaf, 0.0) + flv
                    dn[leaf] = dn.get(leaf, 0) + 1
            elif kind == "list" and isinstance(v, list):
                ls = b["list_sum"].setdefault(k, [])
                ln = b["list_n"].setdefault(k, [])
                for idx, lv in enumerate(v):
                    try:
                        flv = float(lv)
                    except (TypeError, ValueError):
                        continue
                    while len(ls) <= idx:
                        ls.append(0.0)
                        ln.append(0)
                    ls[idx] += flv
                    ln[idx] += 1
            else:
                # "other" → last-in-bucket wins. Iteration order through
                # `series` is oldest-first so the final write IS the
                # latest sample in the bucket.
                b["other_last"][k] = v
    out: list = []
    for bts in sorted(buckets.keys()):
        b = buckets[bts]
        # Drop fully-empty buckets — every kind contributed zero data.
        # Allows the SPA's gap-detection to surface the gap honestly.
        if (
            not any(n > 0 for n in b["scalar_n"].values())
            and not b["dict_sum"]
            and not b["list_sum"]
            and not b["other_last"]):
            continue
        row: dict = {}
        for k, n in b["scalar_n"].items():
            row[k] = (b["scalar_sum"][k] / n) if n > 0 else 0
        for k, ds in b["dict_sum"].items():
            dn = b["dict_n"].get(k, {})
            row[k] = {
                leaf: (ds[leaf] / dn[leaf]) if dn.get(leaf, 0) > 0 else 0
                for leaf in ds.keys()
            }
        for k, ls in b["list_sum"].items():
            ln = b["list_n"].get(k, [])
            row[k] = [
                (ls[i] / ln[i]) if i < len(ln) and ln[i] > 0 else 0
                for i in range(len(ls))
            ]
        for k, v in b["other_last"].items():
            row[k] = v
        # Emit at the latest raw ts inside the bucket (NOT bucket
        # center) so the chart's tail = age of the freshest sample.
        # See the bucket-init comment above for the why.
        emit_ts = bucket_last_ts.get(bts, 0) or bts
        row["t"] = emit_ts
        row["ts"] = emit_ts
        out.append(row)
    return out


@app.get("/api/hosts/history")
async def api_hosts_history(system_id: str = "", hours: int = 1, host_id: str = ""):
    """Return time-series stats for one host.

    Powers the Hosts tab's per-row charts (CPU / Memory / Disk / Net).
    Two paths:

    1. ``system_id`` non-empty → BESZEL path. The system_id is Beszel's
       PocketBase record id — the frontend pulls it off the host row
       returned by :func:`api_hosts`. ``host_id`` (the curated
       hosts_config id) is used as a fallback key to layer in
       ``nr``/``ns`` from ``host_net_samples`` when Beszel's nr/ns are
       all zero (operator forgot ``NICS=eth0`` on the agent).

    2. ``system_id`` empty AND ``host_id`` non-empty → NODE-EXPORTER
       path. Reads pre-sampled rows from ``host_metrics_samples``
       (populated by ``logic.host_metrics_sampler``) and shapes them
       into the same series envelope Beszel returns, so the SPA's chart
       helpers work unchanged. Lets node-exporter-only hosts (no Beszel
       agent at all) get historical CPU / Memory / Disk / Network
       charts in the host drawer.
    """
    h = max(1, min(168, int(hours)))
    sid = (system_id or "").strip()
    hid = (host_id or "").strip()

    if not sid and hid:
        # NE / Pulse path — dispatch on which sampler has rows for
        # this host. Beszel-only hosts come through the system_id
        # branch below; the host_id branch is for hosts whose
        # primary surface is node-exporter OR Pulse.
        #
        # Resolution order:
        # 1. Try host_metrics_sampler first (NE-only host) — most
        #    common case on this branch.
        # 2. Fall through to host_pulse_sampler when the curated
        #    row has a `pulse_name` AND the NE table has no rows
        #    for this host. Pulse-only hosts (Proxmox VMs without
        #    a Beszel agent or node-exporter) land here so the
        #    SPA's chart helpers + inline sparkline see the same
        #    Beszel-compatible series envelope.
        from logic import host_metrics_sampler as _hms
        try:
            series = _hms.history_series(hid, h)
            collectors = _hms.series_collectors_present(hid, h)
        except Exception as e:
            series = []
            collectors = {}
            ne_err: Optional[str] = f"host_metrics_sampler: {e}"
        else:
            ne_err = None
        # Provider fallback chain — only consult downstream samplers
        # when NE has nothing AND the curated row carries the matching
        # provider's identifier. Avoids unnecessary queries on an
        # NE-only host that's temporarily empty (no need to mask
        # "host is idle" with a confusing Pulse / Webmin zero).
        # Order: Pulse → Webmin. Pulse first because Pulse-only hosts
        # are more common (Proxmox VMs); Webmin-only hosts are rare.
        if not series:
            try:
                curated = _load_hosts_config()
            except (json.JSONDecodeError, ValueError, OSError):
                curated = []
            row = next((r for r in curated if r.get("id") == hid), None)
            if row and (row.get("pulse_name") or "").strip():
                from logic import host_pulse_sampler as _hps
                try:
                    pseries = _hps.history_series(hid, h)
                except Exception as e:
                    return {"series": [], "error": f"host_pulse_sampler: {e}"}
                if pseries:
                    return {
                        "series": _bucket_drawer_series(pseries, h),
                        "collectors": {"cpu": True, "mem": True, "fs": True, "net": True, "disk_io": False},
                        "source": "pulse",
                        "error": None,
                    }
            # Webmin fallback. Curated row carries `webmin_name`
            # OR has a `webmin_url` mapped via `webmin_aliases`. Either
            # signal qualifies the host for Webmin history lookup.
            try:
                webmin_aliases = json.loads(get_setting(Settings.WEBMIN_ALIASES, "{}") or "{}")
                if not isinstance(webmin_aliases, dict):
                    webmin_aliases = {}
            except ValueError:
                webmin_aliases = {}
            if row and (
                (row.get("webmin_name") or "").strip()
                or (webmin_aliases.get(hid) or "").strip()
            ):
                from logic import host_webmin_sampler as _hws
                try:
                    wseries = _hws.history_series(hid, h)
                except Exception as e:
                    return {"series": [], "error": f"host_webmin_sampler: {e}"}
                if wseries:
                    return {
                        "series": _bucket_drawer_series(wseries, h),
                        "collectors": {"cpu": True, "mem": True, "fs": True, "net": True, "disk_io": False},
                        "source": "webmin",
                        "error": None,
                    }
        if ne_err:
            return {"series": [], "error": ne_err}
        return {"series": _bucket_drawer_series(series, h), "collectors": collectors, "error": None}

    if not sid:
        return {"series": [], "error": "system_id or host_id required"}

    # Beszel chart series — LOCAL ONLY.
    #
    # Architectural alignment with every other provider (Pulse /
    # Webmin / NE / SNMP / Ping): charts read exclusively from the
    # local sample table. The lifespan `host_beszel_sampler` is the
    # ETL — it reads from the Beszel hub on its tick cadence and
    # writes to `host_beszel_samples`. The chart endpoint never
    # touches the hub; it just queries the local DB.
    #
    # Trade-offs explicitly accepted with this design:
    #   1. Granularity. Local sampler ticks every
    #      `tuning_stats_sample_interval_seconds` (default 300s = 5
    #      min); hub's `1m` aggregation tier had 1-minute samples.
    #      Result: a 1h chart shows ~12 points (local) instead of 60
    #      (hub). Operator can lower the sampler interval to 60s for
    #      1m granularity at the cost of 5x DB writes — change via
    #      `tuning_beszel_sample_interval_seconds` (per-Beszel
    #      override) OR `tuning_stats_sample_interval_seconds` (the
    #      global fallback).
    #   2. Warm-up gap. A fresh deploy / fresh provider enable means
    #      `host_beszel_samples` is empty until the sampler ticks
    #      enough times to fill the requested window. Charts show
    #      the partial range that local covers (no hub fallback).
    #      Same behaviour as Pulse / Webmin / NE / SNMP / Ping local-
    #      only paths — operator-validated as the right design over
    #      the live-hub-fetch fallback (which created visible chart
    #      cuts when the hub's `1m` aggregator lagged independently
    #      of the agent's pushes).
    #   3. Long-range windows (> `tuning_stats_history_days`,
    #      default 7). Local retention is bounded; the hub had
    #      higher-tier aggregations (`120m`) with longer history.
    #      For windows beyond the local retention, the chart returns
    #      empty. If long-range becomes a real ask, reintroduce the
    #      hub fetch as an opt-in for windows > local retention.
    #      `logic/beszel.py:fetch_system_history` is kept in-tree as
    #      dead code for that future need.
    if not hid:
        return {"series": [], "error": "host_id required for Beszel local-only path"}
    from logic import host_beszel_sampler as _hbs
    try:
        local_series = _hbs.history_series(hid, h)
    except Exception as e:  # noqa: BLE001
        return {"series": [], "error": f"host_beszel_sampler: {e}"}
    return {
        "series": _bucket_drawer_series(local_series, h),
        "source": "beszel_local",
        "error": None,
    }


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/hosts/{host_id}/ping/history")
async def api_hosts_ping_history(
    host_id: str, hours: int = 1,
    *,
    _admin: AdminUser,
):
    """Ping reachability time-series for one curated host.

    Mirrors :func:`api_hosts_history` shape — returns
    ``{points: [...], error: None}``. Empty list when this host has
    never been probed (sampler hasn't run yet, or the host isn't opted
    in). Window clamped to 1..168 hours like the Beszel path.

    **Bucketing** — raw `ping_samples` rows at 60s cadence produce
    ~1440 points in a 24h window, far more than the 420px-wide drawer
    chart can render usefully. The chart compresses to ~3 points per
    pixel and every sampler-blip-driven micro-gap surfaces as a broken
    line. Server-side bucketing produces a uniform ~120-point series
    regardless of window: bucket size = max(60s, ceil(hours×3600/120)).
    The bucket aggregator emits AVG(rtt_ms) for alive samples in the
    bucket (None when the whole bucket is dead, so the polyline's
    skip-don't-synthesize logic renders it as a real gap), majority
    `alive` flag, and AVG(loss_pct). Bucket midpoint timestamp lets
    the SPA's gap-detection adapt — at 12min buckets the gap threshold
    auto-derives to ~30min, hiding sub-bucket sampler noise that
    isn't actionable. Small windows (≤2h) skip the bucket pass since
    raw samples are already chart-friendly.
    """
    h = max(1, min(168, int(hours or 1)))
    hid = (host_id or "").strip()
    if not hid:
        return {"points": [], "error": "host_id required"}
    from logic import ping_sampler as _ping_sampler
    since = int(time.time() - h * 3600)
    # Read enough raw samples to cover the window cleanly even when the
    # sampler ran below its cadence (e.g. operator turned ping interval
    # down to 30s). 90 samples/hour × hours = headroom for the bucket
    # aggregator without truncating recent data.
    raw_limit = max(120, h * 90)
    try:
        rows = _ping_sampler.recent_samples(hid, since, limit=raw_limit)
    except Exception as e:
        return {"points": [], "error": f"ping_sampler: {e}"}
    # Small windows (≤2h) — return raw. 1h = ~60 points, 2h = ~120.
    # Below the target density anyway; bucketing would round-trip-distort
    # without helping rendering.
    target_points = 120
    if h <= 2 or len(rows) <= target_points:
        return {"points": rows, "error": None}
    bucket_s = max(60, int((h * 3600) / target_points))
    buckets: dict[int, dict] = {}
    for r in rows:
        ts = int(r.get("ts") or 0)
        if not ts:
            continue
        # Floor to bucket-start.
        bts = (ts // bucket_s) * bucket_s
        b = buckets.get(bts)
        if b is None:
            b = {"rtt_sum": 0.0, "rtt_n": 0,
                 "alive_n": 0, "total_n": 0,
                 "loss_sum": 0.0,
                 "rtt_min_min": None, "rtt_max_max": None,
                 # Track the latest raw ts that landed in this bucket
                 # so the emitted point lands at "newest sample
                 # inside the bucket" rather than the bucket midpoint.
                 # chartFreshness(h) on the SPA walks every cache slot
                 # picking MAX — midpoint-emit made the wider-window
                 # Ping series read stale even when the latest raw
                 # probe was seconds old.
                 "last_ts": 0}
            buckets[bts] = b
        if ts > b["last_ts"]:
            b["last_ts"] = ts
        b["total_n"] += 1
        if r.get("alive"):
            b["alive_n"] += 1
            rtt = r.get("rtt_ms")
            if rtt is not None:
                b["rtt_sum"] += float(rtt)
                b["rtt_n"] += 1
        rmin = r.get("rtt_min_ms")
        rmax = r.get("rtt_max_ms")
        if rmin is not None:
            b["rtt_min_min"] = rmin if b["rtt_min_min"] is None else min(b["rtt_min_min"], rmin)
        if rmax is not None:
            b["rtt_max_max"] = rmax if b["rtt_max_max"] is None else max(b["rtt_max_max"], rmax)
        b["loss_sum"] += float(r.get("loss_pct") or 0.0)
    # Bucket emit timestamp = latest raw ts inside the bucket so
    # chartFreshness reflects the actual freshness of the source
    # regardless of window. Falls back to bucket-start when the
    # bucket somehow has no raw ts (shouldn't happen — every raw row
    # contributes its ts to last_ts in the accumulator loop above).
    points = []
    for bts in sorted(buckets.keys()):
        b = buckets[bts]
        total = b["total_n"] or 1
        # All-dead bucket — no alive sample to average. DROP from the
        # response entirely (don't emit `rtt_ms: null`): the absent
        # bucket creates a time-gap that the SPA's polyline gap-detection
        # picks up, rendering the period as a real break in the line.
        # This is symmetric with the "sampler missed N ticks" case —
        # both yield gaps; the operator reads either as "no usable
        # latency reading for this window."
        if b["rtt_n"] <= 0:
            continue
        rtt_avg = b["rtt_sum"] / b["rtt_n"]
        # Majority alive flag — bucket considered alive iff > 50% of
        # its samples reported alive. Mixed-alive buckets still emit
        # the rtt_avg (computed from alive samples only) so the line
        # reflects "average latency when reachable" across the window.
        alive_majority = b["alive_n"] * 2 > total
        points.append({
            "ts": b["last_ts"] or bts,
            "alive": alive_majority,
            "rtt_ms": rtt_avg,
            "rtt_min_ms": b["rtt_min_min"],
            "rtt_max_ms": b["rtt_max_max"],
            "loss_pct": b["loss_sum"] / total,
            # Surface bucket metadata for the SPA's gap-aware renderer
            # + future tooltip "average over N samples in this 12min
            # bucket" copy. Optional — consumers fall back gracefully.
            "_bucket_seconds": bucket_s,
            "_samples_in_bucket": total,
        })
    return {"points": points, "error": None, "bucket_seconds": bucket_s}

from main_pkg.routes_late import *  # noqa: E402,F401,F403
