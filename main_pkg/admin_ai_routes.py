"""Admin → AI dashboard + sample-pruning endpoints —
`/api/admin/stats/samples/by-host` (GET + DELETE for orphan
prune), `/api/admin/ai/dashboard`, `/api/admin/ai/jobs`.

Loads via the star-import chain anchored at `main.py` — every
symbol re-exports into `main`'s namespace so route
decorators reach the shared `app` instance.
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
# Runtime contract: `from main import *` pulls every public + private
# symbol from main.py PLUS each main_pkg sibling that's already loaded
# by the time this module runs. Includes the stdlib re-exports
# (asyncio, json, os, re, sqlite3, time, Any, Optional, ...) that
# main.py imports at its own top, so this file doesn't need duplicate
# imports for those names.
from main import *  # noqa: E402,F401,F403

# IDE contract: PyCharm/Pyright can't statically trace `from X import *`
# so without this block every name resolved through the wildcard would
# be flagged as "Unresolved reference". The names below are the ones
# THIS file actually consumes; listing them inside a `TYPE_CHECKING`
# block tells the analyzer where they come from WITHOUT running the
# imports at runtime (TYPE_CHECKING evaluates to False then), so a
# name defined in a LATER sibling chunk can't fail at child-load time.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Names defined in main.py itself.
    import asyncio  # noqa: F401
    import re  # noqa: F401
    import time  # noqa: F401
    from typing import Any  # noqa: F401
    import httpx  # noqa: F401
    from pydantic import BaseModel  # noqa: F401
    from fastapi import HTTPException, Request  # noqa: F401
    from logic import oidc  # noqa: F401
    from logic import schedules  # noqa: F401  IDE-only; runtime via main.py's `from logic import schedules` re-exported through the star-import above. Used by the `schedules.UNKNOWN_ACTOR` fallback constant in admin-required write routes.
    from logic.ops import notify  # noqa: F401
    from logic.settings_keys import last_test_success_key  # noqa: F401
    from main import (  # noqa: F401  — IDE-only, runtime via the * above
        _NOTIFY_EVENT_NAMES,
        _actor_from,
        _cache,
        _coerce_int_local,
        _gather,
        _logs,
        _ops_mod,
        AdminUser,
        Request,
        Settings,
        Tunable,
        ai_provider_api_key_key,
        ai_provider_base_url_key,
        ai_provider_enabled_key,
        ai_provider_model_key,
        app,
        auth,
        db_conn,
        get_setting,
        get_setting_bool,
        set_setting,
        tuning,
    )
    # Names defined in sibling main_pkg/* modules that end up in main's
    # namespace at runtime via the tail star-import chain. PyCharm
    # can't trace that chain statically, so import each from its
    # canonical definition module.
    from main_pkg.admin_stats_routes import (  # noqa: F401
        _SAMPLES_TABLE_HOST_COL,
        _resolve_ai_fallback_chain,
    )
    from main_pkg.apps_routes import _populate_detected_ports  # noqa: F401
    from main_pkg.hosts_routes import _load_hosts_config  # noqa: F401


def _invalidate_apps_cache() -> None:
    """Best-effort: clear the ``_shape_host_apps`` catalog cache after a
    catalog mutation so the next ``/api/hosts/list`` / ``one`` fan-out sees
    the change instead of waiting up to 5s for the TTL. ``apps_routes`` may
    not be importable in every load order, so the import is guarded — on
    ImportError the cache simply ages out on its own (the early return keeps
    the imported name bound only on the success path)."""
    try:
        from main_pkg.apps_routes import _invalidate_shape_host_apps_catalog_cache
    except ImportError:
        return
    _invalidate_shape_host_apps_catalog_cache()


@app.get("/api/admin/stats/samples/by-host")
async def api_admin_stats_samples_by_host(
    table: str,
    _admin: AdminUser,
):
    """Admin-only — per-host (or per-item) row counts for ONE sample-
    bearing table. Drives the Stats → Samples drill-down popup: click
    a provider chip → modal lists every host with its row count sorted
    DESC. The footer total (SUM(rows)) cross-checks against the outer
    per-table row count rendered on the Samples page; a mismatch is a
    SQL bug, not an expected divergence.

    `table` MUST be in the canonical `_SAMPLES_TABLE_HOST_COL` map —
    arbitrary operator input is NOT interpolated into the SQL.
    """
    table = (table or "").strip()
    host_col = _SAMPLES_TABLE_HOST_COL.get(table)
    if not host_col:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sample table {table!r}. Allowed: "
                   + ", ".join(sorted(_SAMPLES_TABLE_HOST_COL.keys())),
        )
    out: dict = {
        "table": table,
        "host_col": host_col,
        "rows": [],
        "total": 0,
        # Backend-side fresh outer count — fetched in the SAME SELECT
        # snapshot as the per-host groupings so the SPA's cross-check
        # compares values from one consistent point in time. Pre-fix
        # the SPA used the outer count from the Samples page's earlier
        # `/api/admin/stats/samples` fetch; samplers writing rows
        # between the two HTTP calls drifted the totals + the modal
        # falsely flagged "TOTAL MISMATCH".
        "outer_count": 0,
        "error": None,
    }
    # Curated metadata lookup — operator-facing label + every per-
    # provider name alias for each host_id, so the drill-down popup
    # can show "Which physical host is this?" instead of a bare id.
    # Rows whose host_id ISN'T in `hosts_config` (orphaned samples
    # from a deleted curated row) get a null label so the SPA
    # renders an "(no longer curated)" marker.
    #
    # Special case: when host_col == "item_id" (the Portainer
    # `stats_samples` table), each row is a CONTAINER or SERVICE
    # not a host — look up against `_cache['items']` instead of
    # `hosts_config`. Operator gets a meaningful label
    # ("traefik" / "stack/service_name") instead of a bare
    # `svc:abc123` hex.
    curated_meta: dict[str, dict] = {}
    if host_col == "item_id":
        # Portainer items: id, name, stack, image, type. Live items
        # populate `_cache['items']` from `_gather()`; rows in
        # stats_samples whose id ISN'T in the live cache are stale
        # samples from containers / services that no longer exist.
        #
        # Defensive: if `_cache['items']` is empty (e.g. operator hit
        # Stats → Samples right after server restart, before the SPA's
        # `/api/items` has ever fired), every lookup misses and every
        # item shows as orphan. Block on a fresh `_gather()` first so
        # the cache populates before we walk it. Wall-clock cap via
        # `tuning_kick_gather_timeout_seconds` so an unreachable
        # Portainer (large stacks, slow registry probes) can't hang
        # the drill-down endpoint indefinitely. Default 30s.
        if not (_cache.get("items") or []):
            try:
                _kick_timeout = float(tuning.tuning_int(
                    Tunable.KICK_GATHER_TIMEOUT_SECONDS))
            except (ValueError, TypeError, KeyError):
                _kick_timeout = 30.0
            try:
                await asyncio.wait_for(_gather(), timeout=_kick_timeout)
            except (asyncio.TimeoutError, Exception):
                pass  # best-effort; the lookup below silently misses if gather failed
        try:
            for it in (_cache.get("items") or []):
                iid = (it.get("id") or "").strip()
                if not iid:
                    continue
                name = (it.get("name") or "").strip() or None
                stack = (it.get("stack") or "").strip() or None
                image = (it.get("image") or "").strip() or None
                itype = (it.get("type") or "").strip() or None
                # Composite label — prefix `stack/` when the item is
                # stack-managed; bare name otherwise. Operators reading
                # the modal see "monitoring/grafana" instead of
                # "svc:f3a2…", or just "watchtower" for a standalone
                # container.
                composite = name
                if stack and name:
                    composite = f"{stack}/{name}"
                curated_meta[iid] = {
                    "label": composite,
                    "address": image,  # show image where address would go for hosts
                    "beszel_name": None,
                    "pulse_name": None,
                    "snmp_name": None,
                    "webmin_name": None,
                    "_kind": itype,  # service / container / orphan
                }
        except (TypeError, KeyError, AttributeError):
            pass
    else:
        try:
            for h in _load_hosts_config():
                hid = (h.get("id") or "").strip()
                if not hid:
                    continue
                curated_meta[hid] = {
                    "label": (h.get("label") or "").strip() or None,
                    "address": (h.get("address") or "").strip() or None,
                    "beszel_name": (h.get("beszel_name") or "").strip() or None,
                    "pulse_name": (h.get("pulse_name") or "").strip() or None,
                    "snmp_name": (h.get("snmp_name") or "").strip() or None,
                    "webmin_name": (h.get("webmin_name") or "").strip() or None,
                }
        except (TypeError, KeyError, AttributeError):
            pass

    # Offload the COUNT(*) + GROUP BY to a worker thread. On a long-lived
    # fleet the sample tables are millions of rows and this whole-table
    # scan (no WHERE ts predicate, so no index can seek it) is the
    # heaviest query in the Stats surface, fired on every drill-down
    # click — running it inline blocked the event loop + the SSE
    # heartbeat / healthz (the documented 502-flap class). Mirrors the
    # _compute_admin_stats_samples / _run_net_queries offload pattern.
    # The closure mutates the enclosing `out` dict in place + reads the
    # already-built curated_meta / table / host_col. The _gather() await
    # above deliberately stays on the loop (it's async).
    def _compute_by_host():
        try:
            with db_conn() as c:
                # Table + host-col are validated against the canonical
                # whitelist above; safe to embed in the SQL string.
                # Fresh outer count first — same connection, same
                # transaction, so the per-host sum + outer total snapshot
                # at the same wall-clock instant. Eliminates the spurious
                # "TOTAL MISMATCH" warning operators saw when samplers
                # wrote rows between the page-load fetch and the drill-
                # down click.
                outer_row = c.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
                out["outer_count"] = int(outer_row[0] or 0) if outer_row else 0
                rows = c.execute(
                    f'SELECT "{host_col}" AS host_id, COUNT(*) AS rows '
                    f'  FROM "{table}" '
                    f' GROUP BY "{host_col}" '
                    f' ORDER BY COUNT(*) DESC, "{host_col}" ASC'
                ).fetchall()
                shaped = []
                for r in rows:
                    _hid = (r["host_id"] if hasattr(r, "keys") else r[0]) or ""
                    cnt = int(r["rows"] if hasattr(r, "keys") else r[1] or 0)
                    meta = curated_meta.get(_hid) or {}
                    shaped.append({
                        "host_id": _hid,
                        "rows": cnt,
                        "label": meta.get("label"),
                        "address": meta.get("address"),
                        "beszel_name": meta.get("beszel_name"),
                        "pulse_name": meta.get("pulse_name"),
                        "snmp_name": meta.get("snmp_name"),
                        "webmin_name": meta.get("webmin_name"),
                        "curated": bool(meta),
                    })
                out["rows"] = shaped
                out["total"] = sum(r["rows"] for r in shaped)
        except Exception as e:
            out["error"] = str(e)

    await asyncio.to_thread(_compute_by_host)
    return out


class _SamplesPruneIn(BaseModel):
    """Body for the orphan-prune endpoint. Both fields required."""
    table: str
    host_id: str


# noinspection DuplicatedCode
@app.delete("/api/admin/stats/samples/by-host")
async def api_admin_stats_samples_prune_orphan(
    body: _SamplesPruneIn,
    admin: AdminUser,
):
    """Admin-only — delete every row in <table> for one host_id /
    item_id. Used by the Stats → Samples drill-down "Delete orphan
    rows" button when a curated host has been removed from Admin →
    Hosts but the sampler-written rows remain.

    Table name validated against `_SAMPLES_TABLE_HOST_COL`. The
    host-col name comes from that map too, so neither value is
    operator input embedded raw into the SQL.
    """
    table = (body.table or "").strip()
    host_id = (body.host_id or "").strip()
    if not host_id:
        raise HTTPException(status_code=400, detail="host_id is required")
    host_col = _SAMPLES_TABLE_HOST_COL.get(table)
    if not host_col:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sample table {table!r}. Allowed: "
                   + ", ".join(sorted(_SAMPLES_TABLE_HOST_COL.keys())),
        )
    deleted = 0
    try:
        with db_conn() as c:
            cur = c.execute(
                f'DELETE FROM "{table}" WHERE "{host_col}" = ?',
                (host_id,),
            )
            deleted = int(cur.rowcount or 0)
            c.commit()
            _ops_mod.write_admin_audit(
                c, "samples_prune_orphan",
                target_kind="samples_table", target_name=table, target_id=host_id,
                actor=admin.username,
                message=f"Pruned {deleted} rows from {table} for {host_col}={host_id}",
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "deleted": deleted, "table": table, "host_id": host_id}


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/admin/ai/dashboard")
async def api_admin_ai_dashboard(
    hours: int = 24,
    *,
    _admin: AdminUser,
):
    """Dashboard aggregates for the AI tab. Default window is 24h —
    the SPA passes ``?hours=N`` for 1 / 24 / 168 / 720 / 2160 ranges
    (1h / 24h / 7d / 30d / 90d, unified with the Stats → AI Cost picker).
    Computes
    everything in one round-trip so the SPA's tile grid renders in a
    single fetch:

      summary   — total jobs / success / error / running counts;
                  pass_rate (success / non-running); total_tokens;
                  total_cost_usd; avg_response_time_ms;
                  avg_accuracy_score (NULL when no row has it).
      providers — per-provider rows with the same shape as summary
                  plus model breakdown (one entry per (provider,model)).
      trend     — bucketed-by-hour series of (cost_usd, total_tokens,
                  jobs, pass_rate, avg_accuracy_score) for the chart
                  cards.

    Empty schema returns zero / [] cleanly so the dashboard works
    on a fresh deploy with no recorded jobs yet.
    """
    try:
        hours = max(1, min(int(hours or 24), 24 * 90))
    except (TypeError, ValueError):
        hours = 24
    # Late import — `_ai_supported_providers` lives in
    # `main_pkg.admin_stats_routes`, which is imported AFTER this
    # module in main.py's chain. Module-level import would be a cycle;
    # function-body late-import resolves at call time when both
    # modules are loaded. Same pattern other consumers in this module
    # use for `_load_hosts_config` / `_populate_detected_ports`.
    from main_pkg.admin_stats_routes import _ai_supported_providers
    _provider_names = _ai_supported_providers()
    cutoff = int(time.time()) - hours * 3600

    summary = {
        "total_jobs": 0, "success": 0, "error": 0, "running": 0,
        "pass_rate": 0.0,
        "total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0,
        "total_cost_usd": 0.0,
        "avg_response_time_ms": None,
        "avg_accuracy_score": None,
        "active_provider": (get_setting(Settings.AI_ACTIVE_PROVIDER) or "claude"),
    }
    providers: dict[str, dict] = {
        n: {
            "name": n, "total_jobs": 0, "success": 0, "error": 0, "running": 0,
            "pass_rate": 0.0, "total_tokens": 0, "total_cost_usd": 0.0,
            "avg_response_time_ms": None, "avg_accuracy_score": None,
            "models": [],
            "enabled": (get_setting(ai_provider_enabled_key(n), "false") or "false").lower() == "true",
            "model": get_setting(ai_provider_model_key(n)) or "",
        }
        for n in _provider_names
    }
    trend: list[dict] = []
    try:
        with db_conn() as c:
            # Per-provider aggregates for the cards.
            rows = c.execute(
                """
                SELECT provider,
                       COUNT(*)                                            AS total,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS ok,
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)   AS err,
                       SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS run,
                       COALESCE(SUM(prompt_tokens), 0)                     AS p_tok,
                       COALESCE(SUM(completion_tokens), 0)                 AS c_tok,
                       COALESCE(SUM(total_tokens), 0)                      AS t_tok,
                       COALESCE(SUM(cost_usd), 0.0)                        AS cost,
                       AVG(response_time_ms)                               AS avg_rt,
                       AVG(accuracy_score)                                 AS avg_acc
                FROM ai_jobs
                WHERE ts >= ?
                GROUP BY provider
                """,
                (cutoff,),
            ).fetchall()
            for r in rows:
                p = r["provider"] or ""
                bucket = providers.setdefault(p, {
                    "name": p, "total_jobs": 0, "success": 0, "error": 0, "running": 0,
                    "pass_rate": 0.0, "total_tokens": 0, "total_cost_usd": 0.0,
                    "avg_response_time_ms": None, "avg_accuracy_score": None,
                    "models": [], "enabled": False, "model": "",
                })
                bucket["total_jobs"] = int(r["total"] or 0)
                bucket["success"] = int(r["ok"] or 0)
                bucket["error"] = int(r["err"] or 0)
                bucket["running"] = int(r["run"] or 0)
                non_running = bucket["success"] + bucket["error"]
                bucket["pass_rate"] = (bucket["success"] / non_running) if non_running else 0.0
                bucket["total_tokens"] = int(r["t_tok"] or 0)
                bucket["total_cost_usd"] = float(r["cost"] or 0.0)
                bucket["avg_response_time_ms"] = (float(r["avg_rt"]) if r["avg_rt"] is not None else None)
                bucket["avg_accuracy_score"] = (float(r["avg_acc"]) if r["avg_acc"] is not None else None)
                # Roll into summary too.
                summary["total_jobs"] += bucket["total_jobs"]
                summary["success"] += bucket["success"]
                summary["error"] += bucket["error"]
                summary["running"] += bucket["running"]
                summary["prompt_tokens"] += int(r["p_tok"] or 0)
                summary["completion_tokens"] += int(r["c_tok"] or 0)
                summary["total_tokens"] += bucket["total_tokens"]
                summary["total_cost_usd"] += bucket["total_cost_usd"]

            # Per-(provider, model) breakdown.
            mrows = c.execute(
                """
                SELECT provider,
                       model,
                       COUNT(*)                       AS total,
                       COALESCE(SUM(total_tokens), 0) AS t_tok,
                       COALESCE(SUM(cost_usd), 0.0)   AS cost
                FROM ai_jobs
                WHERE ts >= ?
                  AND model IS NOT NULL
                  AND model != ''
                GROUP BY provider, model
                """,
                (cutoff,),
            ).fetchall()
            for r in mrows:
                p = r["provider"] or ""
                if p in providers:
                    providers[p]["models"].append({
                        "model": r["model"] or "",
                        "total_jobs": int(r["total"] or 0),
                        "total_tokens": int(r["t_tok"] or 0),
                        "total_cost_usd": float(r["cost"] or 0.0),
                    })

            # Summary-wide pass rate + averages.
            non_running = int(summary["success"] or 0) + int(summary["error"] or 0)
            summary["pass_rate"] = (int(summary["success"] or 0) / non_running) if non_running else 0.0
            agg = c.execute(
                "SELECT AVG(response_time_ms) AS avg_rt, "
                "       AVG(accuracy_score)   AS avg_acc "
                "  FROM ai_jobs WHERE ts >= ?",
                (cutoff,),
            ).fetchone()
            if agg is not None:
                summary["avg_response_time_ms"] = (
                    float(agg["avg_rt"]) if agg["avg_rt"] is not None else None
                )
                summary["avg_accuracy_score"] = (
                    float(agg["avg_acc"]) if agg["avg_acc"] is not None else None
                )

            # Hourly trend buckets — drives the time-series cards.
            tr_rows = c.execute(
                """
                SELECT (ts / 3600) * 3600                                  AS bucket,
                       COUNT(*)                                            AS total,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS ok,
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)   AS err,
                       COALESCE(SUM(total_tokens), 0)                      AS t_tok,
                       COALESCE(SUM(cost_usd), 0.0)                        AS cost,
                       AVG(accuracy_score)                                 AS avg_acc
                FROM ai_jobs
                WHERE ts >= ?
                GROUP BY bucket
                ORDER BY bucket ASC
                """,
                (cutoff,),
            ).fetchall()
            for r in tr_rows:
                non_run = int(r["ok"] or 0) + int(r["err"] or 0)
                trend.append({
                    "ts": int(r["bucket"] or 0),
                    "jobs": int(r["total"] or 0),
                    "success": int(r["ok"] or 0),
                    "error": int(r["err"] or 0),
                    "total_tokens": int(r["t_tok"] or 0),
                    "total_cost_usd": float(r["cost"] or 0.0),
                    "pass_rate": (int(r["ok"] or 0) / non_run) if non_run else 0.0,
                    "avg_accuracy_score": (
                        float(r["avg_acc"]) if r["avg_acc"] is not None else None
                    ),
                })
    except Exception as e:
        # DB blip is non-fatal — fall back to the empty shape so the SPA
        # renders the empty-state instead of erroring.
        print(f"[ai] dashboard aggregate failed: {e}")

    return {
        "window_hours": hours,
        "summary": summary,
        "providers": [providers[n] for n in _provider_names
                      if n in providers] + [
                         providers[k] for k in sorted(providers.keys())
                         if k not in _provider_names
                     ],
        "trend": trend,
    }


@app.get("/api/admin/ai/jobs")
async def api_admin_ai_jobs(
    hours: int = 168,
    provider: str = "",
    status: str = "",
    limit: int = 100,
    offset: int = 0,
    *,
    _admin: AdminUser,
):
    """Paginated job log for the dashboard's "Jobs" modal. Supports
    optional ``?provider=`` and ``?status=`` filters. Newest first.
    Caps `limit` at 500 to keep the response payload bounded.
    """
    try:
        hours = max(1, min(int(hours or 168), 24 * 90))
    except (TypeError, ValueError):
        hours = 168
    try:
        limit = max(1, min(int(limit or 100), 500))
    except (TypeError, ValueError):
        limit = 100
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0
    cutoff = int(time.time()) - hours * 3600
    where = ["ts >= ?"]
    params: list = [cutoff]
    if provider:
        where.append("provider = ?")
        params.append(provider.strip().lower())
    if status:
        where.append("status = ?")
        params.append(status.strip().lower())
    sql = (
        "SELECT id, ts, provider, model, kind, status, "
        "       prompt_tokens, completion_tokens, total_tokens, "
        "       cost_usd, response_time_ms, accuracy_score, error "
        f"  FROM ai_jobs WHERE {' AND '.join(where)} "
        " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])
    rows: list[dict] = []
    total = 0
    try:
        with db_conn() as c:
            for r in c.execute(sql, params).fetchall():
                rows.append({
                    "id": int(r["id"]),
                    "ts": int(r["ts"]),
                    "provider": r["provider"],
                    "model": r["model"] or "",
                    "kind": r["kind"] or "",
                    "status": r["status"],
                    "prompt_tokens": (int(r["prompt_tokens"]) if r["prompt_tokens"] is not None else None),
                    "completion_tokens": (int(r["completion_tokens"]) if r["completion_tokens"] is not None else None),
                    "total_tokens": (int(r["total_tokens"]) if r["total_tokens"] is not None else None),
                    "cost_usd": (float(r["cost_usd"]) if r["cost_usd"] is not None else None),
                    "response_time_ms": (int(r["response_time_ms"]) if r["response_time_ms"] is not None else None),
                    "accuracy_score": (float(r["accuracy_score"]) if r["accuracy_score"] is not None else None),
                    "error": r["error"] or "",
                })
            count_sql = (
                f"SELECT COUNT(*) AS n FROM ai_jobs WHERE {' AND '.join(where[:-0] or where)}"
            ) if False else (
                f"SELECT COUNT(*) AS n FROM ai_jobs WHERE {' AND '.join(where)}"
            )
            row = c.execute(count_sql, params[:-2]).fetchone()
            total = int(row["n"] or 0) if row else 0
    except Exception as e:
        print(f"[ai] jobs query failed: {e}")
    return {
        "window_hours": hours,
        "limit": limit,
        "offset": offset,
        "total": total,
        "jobs": rows,
    }


@app.post("/api/admin/ai/{provider}/test")
async def api_admin_ai_test(
    provider: str,
    body: dict,
    _admin: AdminUser,
):
    """Per-provider Test connection probe — same admin-only contract
    as the Portainer / OIDC / Asset Inventory test endpoints. Sends
    a single one-token "ping" through the provider's API to verify
    the API key + model + base URL combine into a working call.

    Body:
        api_key  — optional. When non-empty, used as-is. When blank,
                   falls back to the saved ``ai_provider_<p>_api_key``
                   so the admin can re-test after first save without
                   re-typing the secret. Mirrors the Portainer Test
                   pattern.
        model    — optional. Falls back to the saved model id, then to
                   the canonical default for the provider.
        base_url — optional. Falls back to the saved base URL, then to
                   the canonical endpoint.

    Returns ``{ok, status, detail, response_time_ms, provider}``. The
    SPA renders ``detail`` inline next to the Test button so admins
    can see "Invalid API key" / "Model not found" / etc. straight from
    the upstream provider's error surface.
    """
    p = (provider or "").strip().lower()
    from logic import ai as _ai
    if p not in _ai.SUPPORTED_PROVIDERS:
        raise HTTPException(400, f"Unsupported AI provider: {provider}")
    body = body if isinstance(body, dict) else {}
    # API key — non-empty body wins; otherwise fall back to saved.
    api_key = (body.get("api_key") or "").strip()
    if not api_key:
        api_key = (get_setting(ai_provider_api_key_key(p)) or "").strip()
    model = (
        (body.get("model") or "").strip()
        or (get_setting(ai_provider_model_key(p)) or "").strip()
    )
    base_url = (
        (body.get("base_url") or "").strip()
        or (get_setting(ai_provider_base_url_key(p)) or "").strip()
    )
    result = await _ai.test_provider(
        p,
        api_key=api_key,
        model=model,
        base_url=base_url,
    )
    # Stamp last_test_success_ai_<provider> on ok so the per-provider
    # "Last tested" label persists cross-reload (surfaced via /api/me's
    # client_config.last_test_success). Key matches the SPA's
    # tcSuccessKey 'ai_' + name.
    return _stamp_test_success("ai_" + p, result, target=p)


class AiPaletteIn(BaseModel):
    """Request body for the Cmd-K palette's AI assistant.

    `query` is what the operator typed; `context` is a small SPA-side
    blob giving the model the available host names / item names /
    admin tabs / actions so the response can reference real OmniGrid
    surfaces. ``conversation`` carries prior turns as [{role, text}]
    pairs so multi-turn follow-ups land coherent — the backend
    prepends them to the prompt before the new query. The endpoint's
    system prompt orients the model as the OmniGrid command-palette
    assistant.
    """
    query: str
    context: Optional[dict] = None
    conversation: Optional[list] = None
    # Operator-granted approval for confirm-required tools (currently
    # ssh_diag + docker_container_du). The SPA sets this to True after
    # the inline-confirm chip's Yes click and re-POSTs with the same
    # query so the backend re-parses the AI's last reply, dispatches
    # the tools WITHOUT the short-circuit, and returns the second-round
    # AI reply composed from the tool results.
    tool_confirm_granted: Optional[bool] = None


class AiFeedbackIn(BaseModel):
    """Request body for `/api/ai/feedback` — operator-rated thumbs-up
    or thumbs-down on a specific assistant turn. ``job_id`` is the
    auto-incremented PK from the ai_jobs row stamped on the assistant
    turn at response time. Rating "up" → 1.0, "down" → 0.0 in
    accuracy_score so the dashboard's per-provider quality tile picks
    up real operator feedback over time.
    """
    job_id: Optional[int] = None
    rating: str = "up"


@app.post("/api/ai/palette")
async def api_ai_palette(
    body: AiPaletteIn,
    _admin: AdminUser,
):
    """Cmd-K palette AI assistant — admin-only.

    Routes the operator's query through the active AI provider with a
    system prompt that orients the model as a command-palette
    assistant for OmniGrid. The SPA renders the response in a modal.
    Master AI toggle + active provider come from the `ai_*` settings
    block; missing config returns a clear ok=False so the SPA can
    point the operator at the AI tab to set things up.

    Future stage: parse structured tool-call responses (e.g. "the
    operator wants to restart container X" → SPA executes that
    action). For now the response is plain text — the operator reads
    it and picks the next manual step.
    """
    # Endpoint orchestration only — system prompt, action whitelist,
    # parsers, user-prompt builder, and recorder helper all live in
    # `logic.ai`. This route reads settings → resolves the active
    # provider → calls `ask_provider` → splits the ACTION trailer →
    # records the call. Behaviour is unchanged; the strings + parsing
    # rules are reusable from any future AI-backed feature.
    from logic import ai as _ai
    if not get_setting_bool(Settings.AI_ENABLED):
        return {"ok": False, "status": 0, "provider": "",
                "detail": "AI integration is disabled. Enable it in Admin → AI Integration first.",
                "response_time_ms": 0}
    active = (get_setting(Settings.AI_ACTIVE_PROVIDER) or "").strip().lower()
    if active not in _ai.SUPPORTED_PROVIDERS:
        return {"ok": False, "status": 0, "provider": active,
                "detail": "No active AI provider is selected. Pick one in Admin → AI Integration.",
                "response_time_ms": 0}
    if not get_setting_bool(ai_provider_enabled_key(active)):
        return {"ok": False, "status": 0, "provider": active,
                "detail": f"Active provider '{active}' is not enabled. Enable it in Admin → AI Integration.",
                "response_time_ms": 0}
    # api_key / base_url are resolved via `_resolve_ai_fallback_chain`
    # below into `provider_creds`; model is read direct because the
    # endpoint stamps it on the recorded ai_jobs row.
    model = (get_setting(ai_provider_model_key(active)) or "").strip()

    query = (body.query or "").strip()
    if not query:
        raise HTTPException(400, "query is required")
    ctx = body.context if isinstance(body.context, dict) else {}
    # Forward the SPA's inline-confirm approval to the dispatcher so
    # the confirm-required tools (ssh_diag / docker_container_du) skip
    # their `_pending_confirm` short-circuit and actually fire on the
    # re-POST after the operator clicks Yes on the chip.
    if body.tool_confirm_granted:
        ctx["_tool_confirm_granted"] = True
    # Inject recent-log signals into the AI context so the model can
    # honestly answer "any errors I should fix in the past 7 days?" /
    # "check logs" instead of falsely claiming it has no log access.
    # Pre-fix this read only the in-memory ring buffer's last 30 lines
    # (covers minutes on a busy fleet, not days). Now reads the past
    # `tuning_ai_log_context_hours` (default 168 = 7 days) of error +
    # warn lines from the persistent log files, capped at
    # `tuning_ai_log_context_lines` (default 200) newest-last so the
    # most recent signals always survive trimming. Best-effort — a
    # missing / broken `logs` import skips the block silently.
    try:
        # `_logs` already at module level — local re-import would
        # shadow it for no functional benefit.
        _log_hours = tuning.tuning_int(Tunable.AI_LOG_CONTEXT_HOURS)
        _log_limit = tuning.tuning_int(Tunable.AI_LOG_CONTEXT_LINES)
        _raw_logs = _logs.recent_lines_window(
            hours=_log_hours,
            levels=["error", "warn"],
            limit=_log_limit,
        )
        # Redact common secret patterns (Bearer tokens, password /
        # api_key / token-shaped values, AWS access-key IDs) BEFORE
        # shipping log text to the third-party LLM. The
        # log buffer can carry sensitive values from misformatted
        # log lines (e.g. an upstream that prints `Bearer abc123` in
        # an error trace), and the AI palette ships these to an
        # external service. Admin → Logs continues to show the raw
        # text — only the outbound AI-bound path is redacted. Per
        # the project conventions "Operator-private hostnames" / data-handling
        # rules, this is defence-in-depth — operator-owned creds
        # SHOULDN'T appear in logs in the first place, but a typo'd
        # provider library can leak them, and we shouldn't propagate
        # the leak to the LLM.
        for entry in _raw_logs:
            if isinstance(entry, dict) and entry.get("text"):
                entry["text"] = _logs.redact_secrets(entry["text"])
        ctx["recent_logs"] = _raw_logs
        ctx["recent_logs_window_hours"] = _log_hours
    except (OSError, ValueError, KeyError, AttributeError, ImportError):
        pass

    # Inject the runnable per-app SKILL context server-side. The SPA's
    # client-built ctx carries only view / hosts / items / weather, so
    # without this the web AI sidebar never sees an `app_skills` block
    # and refuses app-skill requests ("integration not configured")
    # even when a skill IS runnable — exactly the symptom the Telegram
    # path avoids by injecting the same via its context-builder. This
    # is the web counterpart. available_app_skills_context() reads
    # hosts_config + the catalog and never raises (returns [] on any
    # failure), and build_palette_user_prompt renders the block.
    # noinspection PyBroadException
    try:
        from logic.apps.registry import available_app_skills_context  # noqa: PLC0415
        from logic.datetime_fmt import get_user_datetime_format  # noqa: PLC0415
        # Render app_skills `last` timestamps in the requesting admin's chosen
        # datetime_format (Settings → Profile → Formats) so the sidebar reply
        # matches the rest of the UI.
        _fmt = get_user_datetime_format(getattr(_admin, "username", None) or "")
        ctx["app_skills"] = available_app_skills_context(datetime_format=_fmt)
    except Exception as e:  # noqa: BLE001
        print(f"[ai] palette app_skills inject failed: {e}")
        ctx.setdefault("app_skills", [])

    # AI output-token cap is now a TUNABLE (DB > env > default with
    # bounds clamp). Legacy `ai_max_tokens` plain-settings row still
    # consulted by the writer for form-hydration parity, but the
    # actual call envelope reads via tuning_int.
    try:
        max_toks = tuning.tuning_int(Tunable.AI_MAX_TOKENS)
    except (TypeError, ValueError):
        max_toks = 1024
    max_toks = max(64, min(32000, max_toks))
    # Provider fallback chain — when enabled AND a transient overload
    # hits the active provider, walk the operator-ordered fallback list
    # and try the next provider's credentials. Filtered to the providers
    # that are master-enabled AND have an API key set; disabled / empty-
    # key entries are skipped silently. See `_resolve_ai_fallback_chain`
    # below for the resolver.
    fb_enabled, fb_chain, provider_creds, fb_max_depth = _resolve_ai_fallback_chain(active)
    conversation = body.conversation if isinstance(body.conversation, list) else None
    # Hydrate the system prompt with persisted AI memories (lessons
    # the AI has learned in prior turns about THIS deployment). Capped
    # at 200 most-recent rows so the prompt token budget stays
    # bounded even on long-running deployments.
    persisted_memories: list[str] = []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT text FROM ai_memory ORDER BY ts DESC LIMIT 200"
            ).fetchall()
            persisted_memories = [r[0] for r in rows if r and r[0]]
    except Exception as e:  # noqa: BLE001
        print(f"[ai] memory hydrate failed: {e}")
    sys_prompt = _ai.PALETTE_SYSTEM_PROMPT
    if persisted_memories:
        memo_block = (
            "\n\nLEARNED MEMORIES (from prior conversations — durable "
            "facts the AI has noted about this specific OmniGrid "
            "deployment; trust these unless contradicted by current "
            "data, and emit `MEMORY-FORGET: <exact text>` if you "
            "discover one is wrong):\n"
            + "\n".join(f" - {m}" for m in reversed(persisted_memories))
            + "\n"
        )
        sys_prompt += memo_block
    out = await _ai.ask_provider_with_fallback(
        active,
        fallback_chain=fb_chain,
        provider_creds=provider_creds,
        prompt=_ai.build_palette_user_prompt(query, ctx, conversation=conversation),
        system_prompt=sys_prompt,
        max_tokens=max_toks,
        fallback_enabled=fb_enabled,
        max_depth=fb_max_depth,
    )

    # Multi-round tool dispatch — when the AI's first-round reply
    # carries `TOOL: <name>` directives (diagnostic READS from the
    # `history` / logs / failure-events tables etc.), dispatch the
    # tools backend-side + re-invoke the AI with the results so it
    # can compose a real diagnostic answer. Hard-capped to ONE
    # round-trip to bound latency + token cost; the second-round
    # reply is treated as final regardless of any further TOOL
    # emissions. See `logic.ai.PALETTE_TOOL_CATALOGUE` for the
    # available tool surface.
    first_text = (out.get("text") or "") if isinstance(out, dict) else ""
    tool_calls, first_cleaned = _ai.parse_palette_tool_calls(first_text)
    if tool_calls and isinstance(out, dict):
        # Dispatch every tool call inline. Results land under
        # `ctx["tool_results"]` keyed by tool name; multiple calls to
        # the same tool merge into a list under that key. Errors are
        # captured per-call so a single bad tool doesn't blank the
        # whole batch.
        if not isinstance(ctx, dict):
            ctx = {}
        tool_results: dict = ctx.get("tool_results") or {}
        # Stamp the operator's username on the ctx so audit-row writes
        # AND ssh_diag's `actor_username` arg carry the right identity.
        try:
            ctx["actor"] = _admin.username or "ai_palette"
        except (AttributeError, TypeError):
            ctx["actor"] = "ai_palette"
        # Pending-confirm collation — tools that require the SPA's
        # inline-confirm chip (currently ssh_diag) short-circuit
        # without firing. The dispatcher returns a marker envelope
        # which the SPA reads to surface the chip; the backend will
        # re-invoke this endpoint AFTER the operator confirms with
        # `ctx["_tool_confirm_granted"] = True`.
        pending_tool_confirms: list = []
        for call in tool_calls:
            name = call.get("name") or ""
            result = await _ai.dispatch_palette_tool(call, ctx)
            if isinstance(result, dict) and result.get("_pending_confirm"):
                pending_tool_confirms.append(result)
                continue
            existing = tool_results.get(name)
            if existing is None:
                tool_results[name] = result
            elif isinstance(existing, list):
                existing.append(result)
            else:
                tool_results[name] = [existing, result]
        ctx["tool_results"] = tool_results
        # Surface any pending confirms back to the SPA — when present,
        # the SPA renders the inline-confirm chip + skips the second-
        # round AI call until the operator clicks Yes. The first-round
        # AI reply text stays in `out["text"]` so the operator sees
        # the conversational framing while the chip awaits approval.
        if pending_tool_confirms and isinstance(out, dict):
            out["pending_tool_confirms"] = pending_tool_confirms
            out["text"] = first_cleaned or first_text
            return out
        # Pass the cleaned first-round prose into the conversation so
        # the AI's second-round reply has the same context the
        # operator's question had + the tool's "here's what I just
        # fetched" annotation.
        second_round_prompt = _ai.build_palette_user_prompt(
            query, ctx, conversation=conversation,
        )
        out = await _ai.ask_provider_with_fallback(
            active,
            fallback_chain=fb_chain,
            provider_creds=provider_creds,
            prompt=second_round_prompt,
            system_prompt=sys_prompt,
            max_tokens=max_toks,
            fallback_enabled=fb_enabled,
            max_depth=fb_max_depth,
        )
        # Surface the tool-results in the response so the SPA can
        # render a small "diagnostic data fetched" affordance below
        # the reply (operator can click to inspect the raw query
        # results). Multi-tool replies get all results.
        if isinstance(out, dict):
            out["tool_calls"] = [
                {"name": c.get("name") or "", "args": c.get("args") or {}}
                for c in tool_calls
            ]
            out["tool_results"] = tool_results
            # Re-parse the SECOND-round reply for follow-up TOOL
            # directives. Common case: the first round identified a
            # container ID and the AI's second-round reply emits a NEW
            # TOOL: docker_container_du with the corrected name. Without
            # this re-parse, the TOOL: + TOOL_ARGS: lines would stay
            # visible in the chat and no follow-up dispatch would fire.
            # We strip the directives from the visible text AND surface
            # them as `pending_tool_confirms` so the SPA can chain the
            # next round (autonomous mode auto-dispatches; approval
            # mode renders the chip again). Hard-cap chain depth is
            # enforced SPA-side via `turn.tool_chain_depth` so a buggy
            # model can't infinite-loop us.
            second_text = (out.get("text") or "")
            second_tools, second_cleaned = _ai.parse_palette_tool_calls(second_text)
            if second_tools:
                # Run any non-confirm-required tools immediately so the
                # SPA only sees the truly-pending ones. (Future-proof
                # for read-only tools chained as part of an autonomous
                # diagnostic flow.) Confirm-required tools are returned
                # as the new pending list.
                new_pending: list = []
                for call in second_tools:
                    name2 = call.get("name") or ""
                    if name2 in _ai.PALETTE_TOOLS_REQUIRING_CONFIRM:
                        new_pending.append({
                            "tool": name2,
                            "args": call.get("args") or {},
                            "reason": (f"{name2} touches a target host (even for reads) "
                                       f"— operator must confirm via the inline chip "
                                       f"in the AI sidebar before this fires."),
                        })
                    else:
                        # Read-only tool — fire inline and merge result.
                        try:
                            result_inline = await _ai.dispatch_palette_tool(call, ctx)
                            tool_results[name2] = result_inline
                        except (RuntimeError, ValueError, TypeError, KeyError, httpx.HTTPError):
                            pass
                out["text"] = second_cleaned or second_text
                if new_pending:
                    out["pending_tool_confirms"] = new_pending

    # Split the optional `ACTION: <id>` trailer(s) off the visible
    # text. Multi-action queries ("refresh and cleanup") emit one
    # line per action; the parser returns them all in order so the
    # SPA can fire each sequentially. The `action` field carries
    # the FIRST action for backward-compatibility with any consumer
    # that only handles single-action responses; new consumers
    # iterate `actions` (list).
    text = (out.get("text") or "") if isinstance(out, dict) else ""
    # Preserve the ORIGINAL full reply: parse_palette_actions below cuts the
    # text from the first `ACTION:` line to end-of-text, which also removes the
    # `ACTION_DATA:` line the AI emits on the NEXT line — so `ACTION_DATA` must
    # be parsed from this original, not the progressively-cleaned `text`.
    orig_reply_text = text
    action_ids, cleaned_text = _ai.parse_palette_actions(text)
    if action_ids:
        out["text"] = cleaned_text
        out["action"] = action_ids[0]  # legacy single-action shape
        out["actions"] = action_ids  # full ordered list
    text = cleaned_text
    action_id = action_ids[0] if action_ids else ""

    # Split the optional `HOSTS: <id1>, <id2>, ...` trailer off too.
    # Validate against the curated host id set so the model can't
    # plant chart requests for hosts the SPA doesn't have a row for.
    known_host_ids: set[str] = set()
    if isinstance(ctx, dict):
        for h in (ctx.get("hosts") or []):
            if isinstance(h, dict):
                hid = (h.get("id") or "").strip()
                if hid:
                    known_host_ids.add(hid)
    host_ids, cleaned_text = _ai.parse_palette_hosts(text, known_host_ids or None)
    if host_ids:
        out["text"] = cleaned_text
        out["hosts"] = host_ids
    text = cleaned_text

    # Optional `CHART: <kind>` trailer pairs with HOSTS:. When omitted
    # OR unrecognised, the SPA defaults to `disk_projection` for back-
    # compat with the original behaviour. Recognised kinds:
    # `disk_projection` / `memory_history` / `cpu_history`. Strips the
    # CHART: line from `text` whether or not we keep the kind so it
    # never leaks into the visible prose.
    chart_kind, cleaned_text = _ai.parse_palette_chart_kind(text)
    if chart_kind:
        out["text"] = cleaned_text
        out["chart_kind"] = chart_kind
    text = cleaned_text

    # Split the optional `ACTION_HOSTS: <id1>, <id2>, ...` trailer
    # off too. Distinct from HOSTS: above — ACTION_HOSTS targets
    # specifically for the action(s) emitted in this turn, NOT the
    # disk-projection chart channel. So `ACTION: scan_ports` paired
    # with `ACTION_HOSTS: opnsense` fires a scan against opnsense
    # without rendering an unrelated chart.
    action_host_ids, cleaned_text = _ai.parse_palette_action_hosts(text, known_host_ids or None)
    if action_host_ids:
        out["text"] = cleaned_text
        out["action_hosts"] = action_host_ids
    text = cleaned_text

    # Parse the optional `ACTION_TAG: <new_tag>` trailer — used by
    # `ACTION: retag_image` to carry the destination tag (e.g. `2`,
    # `latest`, `v2-stable`). Validated against the Docker tag
    # charset by `parse_palette_action_tag`; invalid → empty string
    # so the SPA falls back to its own default. The directive line
    # is stripped from the rendered text either way.
    action_tag, cleaned_text = _ai.parse_palette_action_tag(text)
    if action_tag:
        out["text"] = cleaned_text
        out["action_tag"] = action_tag
    text = cleaned_text

    # Parse the optional `ACTION_ITEM: <name-or-id>` trailer — used by
    # `ACTION: retag_image` (and any future per-item action) to name
    # the target container/stack explicitly. The SPA resolves the
    # token by exact-match against item ids first, then by case-
    # insensitive name match; falls through to the open item drawer
    # when the token doesn't resolve.
    action_item, cleaned_text = _ai.parse_palette_action_item(text)
    if action_item:
        out["text"] = cleaned_text
        out["action_item"] = action_item
    text = cleaned_text

    # Parse the optional `ACTION_DATA: {<json>}` trailer — used by
    # parameterised actions whose payload is a structured dict
    # (currently `schedule_create` / `schedule_update` /
    # `schedule_delete`). Distinct from the single-value
    # `ACTION_TAG` / `ACTION_HOSTS` / `ACTION_ITEM` channels.
    # Validated as JSON object server-side; invalid → None so the
    # SPA falls back gracefully.
    # Parse ACTION_DATA from the ORIGINAL full reply — when an `ACTION:`
    # trailer is present, `parse_palette_actions` already cut the following
    # `ACTION_DATA:` line out of `text`, so parsing the cleaned text always
    # missed it (run_app_skill / schedule_* arrived with action_data=null and
    # failed client-side before any backend log fired).
    action_data, _ = _ai.parse_palette_action_data(orig_reply_text)
    if action_data is not None:
        out["action_data"] = action_data
    # Visible-text hygiene: strip a stray ACTION_DATA line still present in the
    # cleaned text (the no-`ACTION:` case, where nothing removed it above).
    _vis_data, cleaned_text = _ai.parse_palette_action_data(text)
    if _vis_data is not None:
        out["text"] = cleaned_text
    text = cleaned_text

    # Synthesise `action_data` for `send_notification` when the AI
    # emitted `ACTION: send_notification` but skipped the structured
    # ACTION_DATA payload (operator-flagged: AI replied "I'll send 'hi'
    # to your Telegram channel" without the JSON directive, so the SPA
    # got `data=null` and toasted "Pick a valid channel..."). Parse the
    # operator's ORIGINAL query for a "send to <medium> ..." / "tell
    # <medium> <text>" / "notify <medium> that <text>" shape and
    # synthesise `{medium, body}`. Stays a fallback so the AI's
    # native structured emission still wins when present.
    if (
        "send_notification" in (out.get("actions") or [])
        and out.get("action_data") is None
        and query
    ):
        import re as _re_synth
        _q = query.strip()
        # Match the channel name explicitly. Word-boundary so "telegrams"
        # doesn't false-match.
        _channel_match = _re_synth.search(
            r"\b(?P<channel>telegram|apprise|app)\b", _q, _re_synth.IGNORECASE,
        )
        _channel = _channel_match.group("channel").lower() if _channel_match else ""
        # Body extraction — look for the pattern "saying <text>" /
        # ": <text>" / "that <text>" / "send <text> to <channel>" or
        # fall back to "use everything that's not part of the
        # imperative or channel name". Greedy non-greedy: prefer the
        # explicit cue words.
        _body = ""
        for _pat in (
                r"\bsaying\s+(?P<body>.+?)(?:\s+to\s+\w+)?$",
                r"\bsay\s+(?P<body>.+?)(?:\s+to\s+\w+)?$",
                r"\bthat\s+(?P<body>.+?)$",
                r":\s*(?P<body>.+?)$",
                r"\bsend\s+(?:to\s+\w+\s+)?(?P<body>.+?)(?:\s+to\s+\w+)?$",
                r"\bnotify\s+\w+\s+(?P<body>.+?)$",
                r"\btell\s+\w+\s+(?P<body>.+?)$",
                r"\bmessage\s+\w+(?:\s*:)?\s+(?P<body>.+?)$",
        ):
            _bm = _re_synth.search(_pat, _q, _re_synth.IGNORECASE)
            if _bm:
                _body = _bm.group("body").strip().strip('"\'')
                # Strip the channel name + trailing "to" if present.
                _body = _re_synth.sub(
                    r"\s*(?:to\s+)?(?:telegram|apprise|app)\s*$",
                    "", _body, flags=_re_synth.IGNORECASE,
                ).strip()
                if _body:
                    break
        if _channel and _body:
            synth = {"medium": _channel, "body": _body}
            out["action_data"] = synth
            print(
                f"[ai] send_notification synth: channel={_channel!r} "
                f"body_len={len(_body)} (operator query parsed because "
                f"AI omitted ACTION_DATA)"
            )

    # Parse trailing MEMORY: / MEMORY-FORGET: directives. Each MEMORY:
    # line gets persisted into ai_memory immediately (the SPA toasts
    # the operator afterward); each MEMORY-FORGET: line is returned
    # unprocessed so the SPA can show a confirm dialog before deleting.
    memo_saves, memo_forgets, cleaned_text = _ai.parse_palette_memories(text)
    if memo_saves or memo_forgets:
        out["text"] = cleaned_text
        out["memories_saved"] = []
        out["memories_to_forget"] = memo_forgets
        if memo_saves:
            try:
                with db_conn() as c:
                    for m in memo_saves:
                        c.execute(
                            "INSERT INTO ai_memory (ts, text, source, actor) "
                            "VALUES (?, ?, ?, ?)",
                            (int(time.time()), m, "ai",
                             getattr(_admin, "username", None) or "ui"),
                        )
                    c.commit()
                out["memories_saved"] = list(memo_saves)
            except Exception as e:  # noqa: BLE001
                print(f"[ai] memory persist failed: {e}")
    text = cleaned_text

    # Best-effort recorder writes ai_jobs + history so the Admin → AI
    # Usage Dashboard tiles + the History tab pick up every call.
    ok_flag = bool(isinstance(out, dict) and out.get("ok"))
    response_ms = int((out.get("response_time_ms") or 0) if isinstance(out, dict) else 0)
    err_detail = (out.get("detail") or "") if (isinstance(out, dict) and not ok_flag) else None
    # Persistent-log triage line — every call lands in Admin → Logs
    # with provider / model / timing / tokens / action / fallback
    # context. Successful calls log as SUCCESS; transient overloads
    # as WARN; auth / model-not-found / etc. as ERROR. Full upstream
    # message stays in ai_jobs.error + history for failed calls.
    _toks = (out.get("tokens") if isinstance(out, dict) else None) or {}
    _fb_from = (out.get("fallback_from") if isinstance(out, dict) else None)
    _resolved_model = (out.get("model") if isinstance(out, dict) else None) or model
    _resolved_provider = (out.get("provider") if isinstance(out, dict) else None) or active
    _ai.log_ai_outcome(
        kind="palette", provider=_resolved_provider, model=_resolved_model,
        ok=ok_flag,
        status=(isinstance(out, dict) and out.get("status")) or None,
        detail=err_detail,
        response_time_ms=response_ms,
        prompt_tokens=int(_toks.get("prompt") or 0),
        completion_tokens=int(_toks.get("completion") or 0),
        actor=(getattr(_admin, "username", None) or "ui"),
        prompt_excerpt=query,
        action_id=action_id or None,
        fallback_from=_fb_from,
        hosts_count=(len(host_ids) if host_ids else None),
    )
    job_id = _ai.record_ai_call(
        db_conn_factory=db_conn,
        provider=active,
        model=model,
        kind="palette",
        ok=ok_flag,
        response_time_ms=response_ms,
        tokens=(out.get("tokens") if isinstance(out, dict) else None),
        error_detail=err_detail,
        history_actor=getattr(_admin, "username", "ui") or "ui",
        history_events={
            "prompt": query,
            "answer": text,
            "action_id": action_id or "",
            "hosts": host_ids,
            "context": {
                "view": ctx.get("view") if isinstance(ctx, dict) else "",
                "hosts_count": (len(ctx.get("hosts") or []) if isinstance(ctx, dict) else 0),
                "items_count": (len(ctx.get("items") or []) if isinstance(ctx, dict) else 0),
            },
        },
    )
    if isinstance(out, dict) and job_id is not None:
        out["job_id"] = job_id
    return out


@app.post("/api/ai/feedback")
async def api_ai_feedback(
    body: AiFeedbackIn,
    _admin: AdminUser,
):
    """Operator-rated feedback on a specific AI assistant turn.

    The SPA stamps the assistant turn with the ai_jobs row id at
    response time; clicking 👍 / 👎 next to the turn POSTs here with
    `{job_id, rating}`. Rating "up" → 1.0, "down" → 0.0 in
    accuracy_score so the dashboard's per-provider quality tile picks
    up real operator signal over time. Idempotent — clicking the same
    rating twice writes the same value.
    """
    rating = (body.rating or "").strip().lower()
    if rating not in ("up", "down"):
        raise HTTPException(400, "rating must be 'up' or 'down'")
    if not body.job_id:
        # No job_id (e.g. the recorder failed mid-call) — still
        # acknowledge so the SPA can render the chosen feedback chip
        # without flicker, but skip the DB write.
        return {"ok": True, "stored": False}
    score = 1.0 if rating == "up" else 0.0
    try:
        with db_conn() as c:
            cur = c.execute(
                "UPDATE ai_jobs SET accuracy_score = ? WHERE id = ?",
                (score, int(body.job_id)),
            )
            c.commit()
            stored = bool(cur.rowcount)
    except Exception as e:  # noqa: BLE001
        print(f"[ai] feedback update failed: {e}")
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "stored": stored}


@app.get("/api/ai/memory")
async def api_ai_memory_list(
    _admin: AdminUser,
):
    """List persisted AI memories — newest first.

    Each row is one durable lesson the AI has learned about this
    specific OmniGrid deployment. The SPA's Admin → AI → Memory tab
    surfaces these for operator review and pruning.
    """
    out: list[dict] = []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT id, ts, text, source, actor FROM ai_memory "
                "ORDER BY ts DESC LIMIT 500"
            ).fetchall()
            for r in rows:
                out.append({
                    "id": int(r[0]), "ts": int(r[1] or 0),
                    "text": r[2] or "", "source": r[3] or "ai",
                    "actor": r[4] or "",
                })
    except Exception as e:  # noqa: BLE001
        print(f"[ai] memory list failed: {e}")
    return {"ok": True, "memories": out}


class AiMemoryIn(BaseModel):
    text: str
    source: Optional[str] = "operator"


@app.post("/api/ai/memory")
async def api_ai_memory_add(
    body: AiMemoryIn,
    _request: Request,
    _admin: AdminUser,
):
    """Add a memory manually. Source defaults to 'operator' so admin
    -seeded memories are distinguishable from AI-emitted ones."""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    if len(text) > 500:
        text = text[:500]
    src = (body.source or "operator").strip().lower()
    if src not in ("ai", "operator", "system"):
        src = "operator"
    new_id: Optional[int] = None
    try:
        with db_conn() as c:
            cur = c.execute(
                "INSERT INTO ai_memory (ts, text, source, actor) "
                "VALUES (?, ?, ?, ?)",
                (int(time.time()), text, src,
                 getattr(_admin, "username", None) or "ui"),
            )
            c.commit()
            new_id = int(cur.lastrowid) if cur and cur.lastrowid else None
            preview = text if len(text) <= 80 else text[:77] + "…"
            _ops_mod.write_admin_audit(
                c, "ai_memory_create",
                target_kind="ai_memory",
                target_name=str(new_id) if new_id is not None else None,
                target_id=str(new_id) if new_id is not None else None,
                actor=_admin.username,
                message=f"Added AI memory (source={src}): {preview}",
            )
    except Exception as e:  # noqa: BLE001
        print(f"[ai] memory add failed: {e}")
        raise HTTPException(500, str(e))
    return {"ok": True, "id": new_id}


@app.delete("/api/ai/memory/{mem_id}")
async def api_ai_memory_delete(
    mem_id: int,
    _request: Request,
    _admin: AdminUser,
):
    """Delete one memory by id. Idempotent — already-gone returns ok."""
    try:
        with db_conn() as c:
            # Capture preview BEFORE delete so the audit row carries the
            # text that was forgotten — operator post-mortem reads better
            # than a bare numeric id.
            row = c.execute(
                "SELECT text FROM ai_memory WHERE id = ?", (int(mem_id),)
            ).fetchone()
            preview = ""
            if row:
                raw = row[0] or ""
                preview = raw if len(raw) <= 80 else raw[:77] + "…"
            c.execute("DELETE FROM ai_memory WHERE id = ?", (int(mem_id),))
            c.commit()
            _ops_mod.write_admin_audit(
                c, "ai_memory_delete",
                target_kind="ai_memory",
                target_name=str(mem_id), target_id=str(mem_id),
                actor=_admin.username,
                message=(f"Deleted AI memory id={mem_id}: {preview}"
                         if preview else f"Deleted AI memory id={mem_id}"),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[ai] memory delete failed: {e}")
        raise HTTPException(500, str(e))
    return {"ok": True}


@app.post("/api/ai/memory/forget")
async def api_ai_memory_forget(
    body: AiMemoryIn,
    _request: Request,
    _admin: AdminUser,
):
    """Delete every memory whose text MATCHES (exact) the provided
    body. Used when the AI emits ``MEMORY-FORGET: <exact text>`` and
    the SPA confirms with the operator before propagating the delete.
    """
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    deleted = 0
    try:
        with db_conn() as c:
            cur = c.execute("DELETE FROM ai_memory WHERE text = ?", (text,))
            c.commit()
            deleted = int(cur.rowcount or 0)
            if deleted > 0:
                preview = text if len(text) <= 80 else text[:77] + "…"
                _ops_mod.write_admin_audit(
                    c, "ai_memory_delete",
                    target_kind="ai_memory",
                    actor=_admin.username,
                    message=f"Forgot {deleted} AI memor{'y' if deleted == 1 else 'ies'} by exact text: {preview}",
                )
    except Exception as e:  # noqa: BLE001
        print(f"[ai] memory forget failed: {e}")
        raise HTTPException(500, str(e))
    return {"ok": True, "deleted": deleted}


# noinspection PyTypeChecker,PyUnresolvedReferences
# noinspection DuplicatedCode
@app.post("/api/ai/host-filter")
async def api_ai_host_filter(
    body: AiPaletteIn,
    _admin: AdminUser,
):
    """Bulk palette Phase 2 — translate a natural-language phrase into
    a Phase 1 bulk-palette DSL string. The SPA detects bulk-verb-leading
    queries the operator types into Cmd-K (e.g. "pause every host with
    low disk", "resume all the down hosts") and routes them through
    this endpoint; the response's `dsl` string is set as the palette
    query, which then re-renders into bulk mode (chip strip + confirm).
    The AI never directly invokes destructive ops — it just proposes a
    selector the operator reviews and confirms.

    Returns ``{ok: bool, dsl: str, explanation: str, provider, model,
    tokens, response_time_ms, detail?}``. Validation: `dsl` must start
    with a known verb (`pause:` / `resume:`) and be parseable by the
    Phase 1 DSL — invalid responses return ok=False with detail set.
    """
    # Endpoint orchestration only — system prompt + parser + recorder
    # all live in `logic.ai`. This route reads settings → resolves
    # the active provider → calls `ask_provider` → parses the DSL
    # response → records the call.
    from logic import ai as _ai
    if not get_setting_bool(Settings.AI_ENABLED):
        return {"ok": False, "detail": "AI integration is disabled. Enable it in Admin → AI Integration first.",
                "dsl": "", "explanation": "", "response_time_ms": 0}
    active = (get_setting(Settings.AI_ACTIVE_PROVIDER) or "").strip().lower()
    if active not in _ai.SUPPORTED_PROVIDERS:
        return {"ok": False, "detail": "No active AI provider is selected. Pick one in Admin → AI Integration.",
                "dsl": "", "explanation": "", "response_time_ms": 0}
    if not get_setting_bool(ai_provider_enabled_key(active)):
        return {"ok": False, "detail": f"Active provider '{active}' is not enabled. Enable it in Admin → AI Integration.",
                "dsl": "", "explanation": "", "response_time_ms": 0}
    # api_key / base_url are resolved via `_resolve_ai_fallback_chain`
    # below into `provider_creds`; model is read direct because the
    # endpoint stamps it on the recorded ai_jobs row.
    model = (get_setting(ai_provider_model_key(active)) or "").strip()

    query = (body.query or "").strip()
    if not query:
        raise HTTPException(400, "query is required")
    ctx = body.context if isinstance(body.context, dict) else {}

    # AI output-token cap is now a TUNABLE (DB > env > default with
    # bounds clamp). Legacy `ai_max_tokens` plain-settings row still
    # consulted by the writer for form-hydration parity, but the
    # actual call envelope reads via tuning_int.
    try:
        max_toks = tuning.tuning_int(Tunable.AI_MAX_TOKENS)
    except (TypeError, ValueError):
        max_toks = 1024
    max_toks = max(64, min(32000, max_toks))
    # Same fallback wiring as the palette path — see _resolve_ai_fallback_chain.
    fb_enabled, fb_chain, provider_creds, fb_max_depth = _resolve_ai_fallback_chain(active)
    out = await _ai.ask_provider_with_fallback(
        active,
        fallback_chain=fb_chain,
        provider_creds=provider_creds,
        prompt=_ai.build_host_filter_user_prompt(query, ctx),
        system_prompt=_ai.HOST_FILTER_SYSTEM_PROMPT,
        max_tokens=max_toks,
        fallback_enabled=fb_enabled,
        max_depth=fb_max_depth,
    )

    text = (out.get("text") or "") if isinstance(out, dict) else ""
    if isinstance(out, dict) and out.get("ok"):
        dsl, explanation, err_detail = _ai.parse_host_filter_response(text)
    else:
        dsl = ""
        explanation = ""
        err_detail = (isinstance(out, dict) and out.get("detail")) or "AI request failed."

    response_ms = int((out.get("response_time_ms") or 0) if isinstance(out, dict) else 0)
    # Persistent-log triage — same shape as the palette path. Note
    # ok=bool(dsl) here because for host_filter "ok" means we got a
    # parseable DSL out, not just HTTP 200. An HTTP-200 reply that
    # the parser rejected logs as ERROR (operator-actionable: model
    # is misbehaving on the prompt).
    _toks_hf = (out.get("tokens") if isinstance(out, dict) else None) or {}
    _fb_from_hf = (out.get("fallback_from") if isinstance(out, dict) else None)
    _resolved_model_hf = (out.get("model") if isinstance(out, dict) else None) or model
    _resolved_provider_hf = (out.get("provider") if isinstance(out, dict) else None) or active
    _ai.log_ai_outcome(
        kind="host_filter", provider=_resolved_provider_hf, model=_resolved_model_hf,
        ok=bool(dsl),
        status=(isinstance(out, dict) and out.get("status")) or None,
        detail=err_detail,
        response_time_ms=response_ms,
        prompt_tokens=int(_toks_hf.get("prompt") or 0),
        completion_tokens=int(_toks_hf.get("completion") or 0),
        actor=(getattr(_admin, "username", None) or "ui"),
        prompt_excerpt=query,
        dsl=dsl or None,
        fallback_from=_fb_from_hf,
    )
    _ai.record_ai_call(
        db_conn_factory=db_conn,
        provider=active,
        model=model,
        kind="host_filter",
        ok=bool(dsl),
        response_time_ms=response_ms,
        tokens=(out.get("tokens") if isinstance(out, dict) else None),
        error_detail=err_detail or None,
        history_actor=getattr(_admin, "username", "ui") or "ui",
        history_events={
            "prompt": query,
            "answer": text,
            "dsl": dsl,
            "explanation": explanation,
        },
    )

    return {
        "ok": bool(dsl),
        "dsl": dsl,
        "explanation": explanation,
        "provider": active,
        "model": model,
        "response_time_ms": int((out.get("response_time_ms") or 0) if isinstance(out, dict) else 0),
        "tokens": (out.get("tokens") if isinstance(out, dict) else None) or {},
        "detail": err_detail or None,
    }


# ----------------------------------------------------------------------------
# Process-level tunables. Admin-only read endpoint that surfaces
# the DB / env / default tier per knob plus the resolved effective value.
# Writes go through the existing POST /api/settings (additive pattern —
# no new POST per provider). The UI reads this once on tab open to
# render placeholders for the env-fallback / default behind each input.
# ----------------------------------------------------------------------------
@app.get("/api/admin/tuning")
async def api_admin_tuning(_admin: AdminUser):
    """Return per-tunable effective state (DB / env / default / resolved)."""
    return tuning.effective_state()


# ----------------------------------------------------------------------------
# Notification templates — admin-only editor surface.
# ----------------------------------------------------------------------------
# Each event in `NOTIFY_EVENT_NAMES` ships with hard-coded baseline
# templates (`logic.ops.NOTIFY_TEMPLATE_DEFAULTS`); admins can override
# the title or body via DB-backed settings (`notify_template_<event>_title`
# / `_body`). Three routes power the Admin → Notifications template
# editor + the Profile → Notifications read-only popup:
#
# GET  /api/admin/notify-templates                 — list every event +
#                                                     its current state.
# POST /api/admin/notify-templates/{event}         — write title/body
#                                                     (empty string =
#                                                     reset to default).
# POST /api/admin/notify-templates/{event}/preview — render with sample
#                                                     values for the
#                                                     live-preview pane.
# POST /api/admin/notify-templates/{event}/test    — fire one real
#                                                     notification through
#                                                     the live dispatcher
#                                                     so the admin can
#                                                     see the rendered
#                                                     output land in
#                                                     Apprise + the
#                                                     in-app inbox.
#
# Resolution order at fire time: DB setting (when non-empty) → hard-coded
# default → empty (defence in depth — the audit gate flags missing
# defaults so this branch is unreachable in practice). See the project conventions
# "How notification templates resolve" + "How to add a new notify event
# with a template default" for the canonical extension pattern.
# ----------------------------------------------------------------------------
class NotifyTemplateIn(BaseModel):
    """PUT/POST body for the per-event template editor.

    Both fields are optional — sending only ``title`` updates just that
    field. Empty string is a sentinel for "reset to default" (deletes
    the DB row); a non-empty string saves verbatim. Mirrors the
    keep-current-if-blank contract used elsewhere in the codebase
    (Webmin password, Portainer API key, etc.).
    """
    title: Optional[str] = None
    body: Optional[str] = None


class NotifyTemplatePreviewIn(BaseModel):
    """POST body for the live-preview pane. ``title`` / ``body`` are
    rendered against the sample placeholder values (see
    :data:`NOTIFY_TEMPLATE_SAMPLES`) and the response carries the
    resolved strings + metadata about which placeholders fired.
    """
    title: Optional[str] = None
    body: Optional[str] = None


def _shape_notify_template_row(event: str) -> dict:
    """Build the API JSON shape for ONE event's template state.

    Used by :func:`api_admin_notify_templates` (list endpoint) and
    :func:`api_admin_notify_templates_set` (single-event response).
    """
    title_key, body_key = _ops_mod.template_setting_keys(event)
    raw_title = (get_setting(title_key) or "")
    raw_body = (get_setting(body_key) or "")
    default_title = _ops_mod.template_default(event, "title")
    default_body = _ops_mod.template_default(event, "body")
    return {
        "event": event,
        "title": raw_title if raw_title else default_title,
        "body": raw_body if raw_body else default_body,
        "title_default": default_title,
        "body_default": default_body,
        "title_is_default": (not raw_title),
        "body_is_default": (not raw_body),
    }


@app.get("/api/admin/notify-templates")
async def api_admin_notify_templates(_admin: AdminUser):
    """List every registered event + its template state.

    Returns:
      - ``events``: list of per-event objects (see
        :func:`_shape_notify_template_row`).
      - ``available_placeholders``: tuple of placeholder names the
        editor surfaces as clickable chips. Curated whitelist —
        :data:`NOTIFY_PLACEHOLDERS`.
      - ``samples``: sample values used by the live-preview pane (so
        the SPA can render a hint label "{name} → example-stack" next
        to each chip without a separate round-trip).
      - ``unbound_events``: events that fire ``notify(event=...)`` in
        code but aren't in :data:`NOTIFY_EVENT_NAMES` (audit gate;
        empty when the codebase is consistent — surfaced as a warning
        chip in the SPA).
      - ``missing_defaults`` / ``unknown_defaults``: see
        :func:`audit_template_data`.
    """
    # Pure data variant — every Admin → Notifications visit calls this
    # endpoint, so the audit must NOT log. The boot path uses
    # `audit_template_and_log` for the one-time WARN trace.
    audit = _ops_mod.audit_template_data()
    return {
        "events": [
            _shape_notify_template_row(name) for name in _NOTIFY_EVENT_NAMES
        ],
        "available_placeholders": list(_ops_mod.NOTIFY_PLACEHOLDERS),
        "samples": dict(_ops_mod.NOTIFY_TEMPLATE_SAMPLES),
        "missing_defaults": audit.get("missing_defaults") or [],
        "unknown_defaults": audit.get("unknown_defaults") or [],
        # Reserved for the future "scan the codebase for unregistered
        # notify(event=...) calls" enforcement; currently always empty
        # because the audit gate runs against the static defaults map.
        "unbound_events": [],
    }


@app.post("/api/admin/notify-templates/{event}")
async def api_admin_notify_templates_set(
    event: str,
    body: NotifyTemplateIn,
    _admin: AdminUser,
):
    """Write one event's template title and/or body.

    Empty string is a sentinel: clears the DB row so the resolver
    falls back to the hard-coded default. Non-empty string saves
    verbatim (UTF-8 round-trip; emoji friendly).

    Validates the event name against :data:`NOTIFY_EVENT_NAMES` so a
    typo can't silently land a stray settings row that nothing reads.
    """
    if event not in _NOTIFY_EVENT_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown event '{event}'. Must be one of: "
                   f"{', '.join(sorted(_NOTIFY_EVENT_NAMES))}.",
        )
    title_key, body_key = _ops_mod.template_setting_keys(event)
    # Both fields use the keep-current-if-None contract (None ⇒ no-op);
    # explicit empty string ⇒ clear (fall back to default at resolve
    # time). Single defer-context so the cross-tab settings:updated
    # SSE event fires once even if both fields changed.
    from logic.db import defer_settings_version_bump
    touched: list[str] = []
    with defer_settings_version_bump():
        if body.title is not None:
            set_setting(title_key, body.title or "")
            touched.append("title")
        if body.body is not None:
            set_setting(body_key, body.body or "")
            touched.append("body")
    # Audit row — admin-edited templates change the copy that every
    # subsequent event firing emits. Touched-fields list lets the History
    # row show which half of the template moved without dumping the full
    # before/after to the events JSON.
    if touched:
        try:
            with db_conn() as c:
                _ops_mod.write_admin_audit(
                    c, "notify_template_update",
                    target_kind="notify_template",
                    target_name=event,
                    target_id=",".join(touched),
                    actor=_admin.username or schedules.UNKNOWN_ACTOR,
                    message=f"notification template {event!r} touched "
                            f"({', '.join(touched)}) by {_admin.username or 'operator'}",
                )
        except Exception as e:
            print(f"[notify] template-update audit-row write failed: {e}")
    return _shape_notify_template_row(event)


# noinspection DuplicatedCode
@app.post("/api/admin/notify-templates/{event}/preview")
async def api_admin_notify_templates_preview(
    event: str,
    body: NotifyTemplatePreviewIn,
    _admin: AdminUser,
):
    """Render an in-flight template against sample values.

    Drives the live-preview pane in the editor — the SPA debounces
    keystrokes and POSTs the in-progress title/body, displaying the
    rendered output as the operator types. Also surfaces:
      - ``used_placeholders``: every ``{key}`` token found in either
        template, in stable order — operator can confirm the chip
        clicks landed.
      - ``unknown_placeholders``: tokens NOT in
        :data:`NOTIFY_PLACEHOLDERS`. Renders as the verbatim ``{key}``
        in the output (no KeyError) but the editor highlights them so
        the operator sees the typo.

    Event name is still validated even though preview doesn't write
    state — keeps the 400-on-typo contract symmetrical.
    """
    if event not in _NOTIFY_EVENT_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown event '{event}'. Must be one of: "
                   f"{', '.join(sorted(_NOTIFY_EVENT_NAMES))}.",
        )
    samples = _ops_mod.samples_for_event(event)
    title_in = body.title or ""
    body_in = body.body or ""
    rendered_title = _ops_mod.render_template(title_in, samples)
    rendered_body = _ops_mod.render_template(body_in, samples)
    # Token analysis — find every {placeholder} occurrence. Curly braces
    # inside a single-quoted JSON value are rare in practice; the regex
    # tolerates whitespace inside the braces (`{ name }` → `name`) but
    # not nested braces (which str.format_map would reject anyway).
    token_re = re.compile(r"\{\s*(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\s*}")
    found_tokens: list[str] = []
    seen: set[str] = set()
    for src in (title_in, body_in):
        for m in token_re.finditer(src):
            t = m.group("name")
            if t in seen:
                continue
            seen.add(t)
            found_tokens.append(t)
    valid_set = set(_ops_mod.NOTIFY_PLACEHOLDERS)
    deprecated_map = dict(getattr(_ops_mod, "NOTIFY_DEPRECATED_PLACEHOLDERS", {}) or {})
    used = [t for t in found_tokens if t in valid_set]
    deprecated = [
        {"token": t, "replacement": deprecated_map[t]}
        for t in found_tokens
        if t in deprecated_map
    ]
    unknown = [
        t for t in found_tokens
        if t not in valid_set and t not in deprecated_map
    ]
    return {
        "rendered_title": rendered_title,
        "rendered_body": rendered_body,
        "used_placeholders": used,
        "unknown_placeholders": unknown,
        # Tokens that USED to be supported but have since been retired.
        # Editor SPA can render these inline with a warning marker +
        # replacement hint, distinct from genuine unknown/typo tokens.
        "deprecated_placeholders": deprecated,
        "samples": samples,
    }


# noinspection DuplicatedCode
@app.post("/api/admin/notify-templates/{event}/test")
async def api_admin_notify_templates_test(
    event: str,
    body: NotifyTemplatePreviewIn,
    request: Request,
    _admin: AdminUser,
):
    """Fire one real notification through the live dispatcher.

    The body's ``title`` / ``body`` are SAVED to the DB before the
    fire so the dispatcher resolves the in-progress template (matching
    what the admin is about to commit). After firing, the response
    carries the rendered strings + the per-medium fan-out outcome.

    Marked as a TEST run via metadata so the in-app row is visually
    distinguishable from a real op-fired notification (the SPA's
    notifications panel can highlight `metadata.test: true` rows).
    """
    if event not in _NOTIFY_EVENT_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown event '{event}'. Must be one of: "
                   f"{', '.join(sorted(_NOTIFY_EVENT_NAMES))}.",
        )
    # Stash whatever the admin has typed (if anything) so the
    # dispatcher's `resolve_template` picks it up. None → no write,
    # leaving the previously-saved value (or default) in play.
    title_key, body_key = _ops_mod.template_setting_keys(event)
    from logic.db import defer_settings_version_bump
    with defer_settings_version_bump():
        if body.title is not None:
            set_setting(title_key, body.title or "")
        if body.body is not None:
            set_setting(body_key, body.body or "")
    # Build sample-flavoured kwargs so the dispatcher's placeholder
    # resolver lands on the sample values (per-event overrides applied so
    # e.g. a prayer_reminder test reads as real prayer text). The SPA's
    # "Send test" button is admin-only, so the actor is whoever clicked it.
    samples = _ops_mod.samples_for_event(event)
    actor = getattr(getattr(request.state, "user", None), "username", None) or "system"
    # Determine target_kind from the event name — failure events
    # commonly target the same kind as their success siblings; we
    # don't introspect that here and just use the sample {host} as
    # the target_id so the in-app row's deep-link shape is sane.
    legacy_title = "🔔 Test: " + samples.get("name", "example")
    legacy_body = samples.get("error", "") if event.endswith("_failure") else ""
    severity = "error" if event.endswith("_failure") else "success"
    try:
        await notify(
            legacy_title,
            legacy_body,
            severity,
            event=event,
            actor_username=actor,
            target_kind="host",
            target_id=samples.get("host") or "",
            metadata={
                "test": True,
                "host": samples.get("host") or "",
            },
        )
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }
    return {
        "ok": True,
        "event": event,
        # Re-render against samples so the response carries what the
        # admin should expect to see in their inbox; saves a separate
        # /preview round-trip.
        "rendered_title": _ops_mod.render_template(
            _ops_mod.resolve_template(event, "title"), samples,
        ),
        "rendered_body": _ops_mod.render_template(
            _ops_mod.resolve_template(event, "body"), samples,
        ),
    }


# ----------------------------------------------------------------------------
# OIDC auth routes — see logic/oidc.py for the flow spec.
# ----------------------------------------------------------------------------
@app.get("/api/oidc/login")
async def api_oidc_login(request: Request):
    """Start the OIDC authorization-code + PKCE flow (redirects to IdP)."""
    return await oidc.login(request)


@app.get("/api/oidc/callback")
async def api_oidc_callback(request: Request):
    """Complete OIDC callback: validate id_token, auto-provision user, mint session."""
    return await oidc.callback(request)


def _log_provider_test_start(provider: str, target: str = "") -> None:
    """Emit a `[provider_test] START provider=X target=Y` line so
    operators can correlate a Test-button click with subsequent log
    activity. Pair with `_stamp_test_success` which logs the OK /
    FAILED outcome line. `target` is the human-recognisable
    destination (url / hostname / IP / chat_id) — pass empty when
    the endpoint hasn't validated input yet."""
    _t = f" target={target!r}" if target else ""
    print(f"[provider_test] START provider={provider!r}{_t}")


def _stamp_test_success(provider: str, result: dict, target: str = "") -> dict:
    """Stamp `last_test_success_<provider>` in the settings KV when the
    test result reports ok=True. Returns the result dict unchanged so
    handlers can use it as `return _stamp_test_success("portainer",
    {...})`. DB-backed (not localStorage) so every operator + browser
    sees the same value across machines. Best-effort — a stamping
    failure logs and does NOT break the response.

    Surfaced back to the SPA via `/api/me`'s `client_config.last_test_success`
    block. The stamped timestamp is epoch seconds at the moment the
    success was recorded.

    ALSO logs the test outcome via the `[provider_test]` family so
    operators can pinpoint which provider's Test-button click failed
    + what the failure detail was. `warning:` token on failure routes
    the line into the WARN bucket (per logic/logs.py:_severity_for);
    success is INFO-level diagnostic (NOT the SUCCESS bucket which
    is reserved for operator-visible state changes — stack updates,
    container restarts, etc.). `target` is an optional context
    string (url / hostname / chat_id) — empty falls through cleanly.
    """
    # Outcome log line — fires for BOTH success and failure shapes
    # so operators see every Test-button outcome in Admin → Logs.
    if isinstance(result, dict):
        ok = bool(result.get("ok"))
        detail = str(result.get("detail") or "")
        status = result.get("status")
        _t = f" target={target!r}" if target else ""
        _s = f" status={status}" if status not in (None, "", 0) else ""
        # Truncate detail in the log — full string still flows in
        # the JSON response back to the operator's UI.
        _d = detail[:200] + ("…" if len(detail) > 200 else "")
        if ok:
            # Verb is "passed" not "OK" so the line classifies as
            # INFO not SUCCESS (per the project conventions "pick verbs carefully"
            # — `_RE_OK` matches `\bsuccess\b|\bok —|→ ok\b`).
            # ALSO scrub the echoed detail: many test_discovery
            # paths return `"OK — issuer: ..."` which (when echoed
            # verbatim into the log line's body) triggers the
            # full-text OK scan downstream of the early-position
            # check. Replacing `OK —` with `OK:` in the echo
            # preserves operator-readability of the JSON response
            # body (which surfaces in the SPA's test-result panel
            # untouched) while neutralising the classifier match
            # ONLY in the log line. Same goes for `→ ok` and a
            # bare `success` token in the body.
            _d_clean = _d.replace("OK —", "OK:").replace("→ ok", "-> reached")
            print(f"[provider_test] passed provider={provider!r}{_t}{_s} detail={_d_clean!r}")
        else:
            print(f"[provider_test] warning: FAILED provider={provider!r}"
                  f"{_t}{_s} detail={_d!r}")
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    try:
        set_setting(last_test_success_key(provider), str(int(time.time())))
    except Exception as e:
        print(f"[test] last_test_success stamp failed for {provider}: {e}")
    return result


@app.post("/api/oidc/test")
async def api_oidc_test(
    request: Request,
    _admin: AdminUser,
):
    """Admin-only: probe the issuer's discovery endpoint. Used by the
    "Test connection" button in the Settings panel. No state changes.

    Honours an in-flight ``verify_tls`` from the form when supplied so
    an admin can flip the checkbox OFF and Test a self-signed issuer
    before saving. Missing key falls back to the saved DB
    value via ``oidc._verify_tls()``.
    """
    body = await request.json()
    issuer = (body.get("issuer_url") or "").strip()
    verify_tls = body.get("verify_tls")
    if verify_tls is not None:
        verify_tls = bool(verify_tls)
    _log_provider_test_start("oidc", target=issuer or "(unset)")
    return _stamp_test_success(
        "oidc", await oidc.test_discovery(issuer, verify_tls=verify_tls),
        target=issuer,
    )


@app.post("/api/portainer/test")
async def api_portainer_test(
    request: Request,
    _admin: AdminUser,
):
    """Admin-only: probe ``{url}/api/status`` with the given API key.
    Supports both already-saved creds (empty api_key means "use current")
    and unsaved form values (api_key populated). No state changes.
    """
    from logic import portainer as _portainer
    body = await request.json()
    url = (body.get("url") or "").strip().rstrip("/")
    verify_tls = bool(body.get("verify_tls", True))
    _log_provider_test_start("portainer", target=url or "(unset)")
    # Portainer's API key isn't in the `settings` table — it lives in
    # the Portainer-specific settings dict — so this one keeps a
    # purpose-built fallback. Every other test endpoint below uses
    # the shared `_resolve_field` helper.
    api_key = (body.get("api_key") or "").strip()
    if not api_key:
        api_key = str(_portainer.get_portainer_settings().get("portainer_api_key") or "")
    if not url or not api_key:
        return _stamp_test_success("portainer", {
            "ok": False, "status": 0,
            "detail": "URL and API key are both required",
        }, target=url or "(unset)")
    # Endpoint id: probe `/api/endpoints/{id}` after
    # /api/status to surface a misconfigured endpoint id at Test time
    # rather than have it 404 on the next gather. Falls back to the
    # saved value so an operator who hits Test before re-typing still
    # validates the live config.
    raw_eid = body.get("endpoint_id")
    if raw_eid in (None, ""):
        raw_eid = _portainer.get_portainer_settings().get("portainer_endpoint_id") or 1
    try:
        endpoint_id = int(raw_eid)
    except (TypeError, ValueError):
        return _stamp_test_success("portainer", {
            "ok": False, "status": 0,
            "detail": f"endpoint_id must be an integer, got {raw_eid!r}",
        }, target=url)
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(verify=verify_tls, timeout=10.0) as client:
            headers = {"X-API-Key": api_key}
            r = await client.get(f"{url}/api/status", headers=headers)
            if r.status_code != 200:
                # Route the upstream failure through the humaniser
                # so the operator sees
                # "Portainer rejected the credentials (HTTP 401 — ...)"
                # instead of a bare body dump.
                raw = f"HTTP {r.status_code}: {r.text[:200]}"
                return _stamp_test_success("portainer", {
                    "ok": False, "status": r.status_code,
                    "detail": _humanise_probe_error(raw, "Portainer"),
                }, target=url)
            version = ""
            try:
                data = r.json()
                version = data.get("Version") or data.get("version") or ""
            except (ValueError, KeyError, AttributeError):
                pass
            # Endpoint probe — best-effort; only fails the test if the
            # specific id is missing. Non-200/404 responses surface as
            # diagnostic detail without blocking the success path.
            ep = await client.get(
                f"{url}/api/endpoints/{endpoint_id}", headers=headers,
            )
        prefix = f"OK — Portainer {version}" if version else "OK"
        if ep.status_code == 200:
            try:
                name = ep.json().get("Name") or f"#{endpoint_id}"
            except (ValueError, KeyError, AttributeError):
                name = f"#{endpoint_id}"
            return _stamp_test_success("portainer", {
                "ok": True, "status": 200,
                "detail": f"{prefix}, endpoint {name} reachable",
                "endpoint_id": endpoint_id,
            }, target=url)
        if ep.status_code == 404:
            # Specific Portainer-shaped message — keep the bespoke copy
            # rather than humanising. Operators recognise this exact
            # phrasing from the related fix.
            return _stamp_test_success("portainer", {
                "ok": False, "status": 404,
                "detail": f"endpoint {endpoint_id} not found on this Portainer",
                "endpoint_id": endpoint_id,
            }, target=url)
        raw = f"endpoint probe HTTP {ep.status_code}: {ep.text[:200]}"
        return _stamp_test_success("portainer", {
            "ok": False, "status": ep.status_code,
            "detail": _humanise_probe_error(raw, "Portainer"),
            "endpoint_id": endpoint_id,
        }, target=url)
    except Exception as e:
        # Network-level failures (DNS / refused / TLS / timeout) are
        # the cases the humaniser was designed for — let them flow
        # through it instead of surfacing the raw exception repr.
        raw = f"{type(e).__name__}: {e}"
        return _stamp_test_success("portainer", {
            "ok": False, "status": 0,
            "detail": _humanise_probe_error(raw, "Portainer"),
        }, target=url)


@app.post("/api/pulse/test")
async def api_pulse_test(
    request: Request,
    _admin: AdminUser,
):
    """Admin-only: probe a Pulse instance with the given (or saved)
    credentials. Mirrors :func:`api_beszel_test` — accepts unsaved form
    values or falls back to the persisted token so Test works after
    first save without re-typing the secret."""
    from logic import pulse as _pulse
    body = await request.json()
    url = _resolve_field(body, "url", "pulse_url").rstrip("/")
    token = _resolve_field(body, "token", "pulse_token")
    verify_tls = bool(body.get("verify_tls", True))
    _log_provider_test_start("pulse", target=url or "(unset)")
    if not url or not token:
        return _stamp_test_success("pulse", {
            "ok": False,
            "detail": "URL and API token are both required",
        }, target=url or "(unset)")
    result = await _pulse.probe_pulse(
        url, token, verify_tls=verify_tls, timeout=10.0,
    )
    return _stamp_test_success("pulse", _format_provider_test_summary(
        result,
        target_label="Pulse",
        item_singular="node",
        item_plural="node(s)",
        count_key="node_count",
        items_key="nodes",
    ), target=url)


@app.post("/api/webmin/test")
async def api_webmin_test(
    request: Request,
    _admin: AdminUser,
):
    """Admin-only: probe a Webmin Miniserv instance.

    Accepts ``{url, user, password, verify_tls}``. Password is keep-
    current-if-blank (same contract as Portainer / Beszel / Pulse
    test endpoints). Returns ``{ok, detail}`` with a short summary.
    """
    from logic import webmin as _webmin
    body = await request.json()
    url = _resolve_field(body, "url", "webmin_url").rstrip("/")
    user = _resolve_field(body, "user", "webmin_user")
    password = _resolve_field(body, "password", "webmin_password")
    verify_tls = bool(body.get("verify_tls", False))
    _log_provider_test_start("webmin", target=url or "(unset)")
    if not url or not user or not password:
        return _stamp_test_success("webmin", {
            "ok": False,
            "detail": "URL, user and password are all required",
        }, target=url or "(unset)")
    result = await _webmin.probe_webmin(
        url, user, password, verify_tls=verify_tls, timeout=10.0,
    )
    if result.get("error") and not result.get("hosts"):
        # follow-up: route Webmin's verbatim probe error
        # through the humaniser too. Common Webmin failure modes (auth
        # cool-down / module timeout / TLS handshake) all map cleanly.
        return _stamp_test_success("webmin", {
            "ok": False,
            "detail": _humanise_probe_error(result["error"], "Webmin"),
        }, target=url)
    hosts = result.get("hosts") or {}
    if not hosts:
        return _stamp_test_success("webmin", {
            "ok": False,
            "detail": "No host_key resolved — Webmin responded "
                      "but couldn't extract a hostname",
        }, target=url)
    host_key = next(iter(hosts))
    stats = hosts[host_key]
    pending = stats.get("host_updates_pending") or 0
    security = stats.get("host_updates_security") or 0
    mem = stats.get("host_mem_total") or 0
    mounts = len(stats.get("mounts") or [])
    nics = len(stats.get("network_ifaces") or [])
    detail = (f"OK — {host_key} · "
              f"{pending} updates ({security} sec) · "
              f"mem={mem // (1024 ** 3) if mem else '?'} GB · "
              f"mounts={mounts} · nics={nics}")
    partial = result.get("partial_errors") or []
    if partial:
        detail += f" · partial: {len(partial)} module(s) failed"
    return _stamp_test_success("webmin", {
        "ok": True, "detail": detail, "host_key": host_key,
        "partial_errors": partial,
    }, target=url)


@app.post("/api/beszel/test")
async def api_beszel_test(
    request: Request,
    _admin: AdminUser,
):
    """Admin-only: probe a Beszel Hub with the given (or saved) creds.

    Mirrors :func:`api_portainer_test` — accepts unsaved form values OR
    falls back to the persisted password so Test works after first save
    without re-typing it. Returns ``{ok, detail, system_count}``.
    """
    from logic import beszel as _beszel
    body = await request.json()
    hub_url = _resolve_field(body, "hub_url", "beszel_hub_url").rstrip("/")
    identity = _resolve_field(body, "identity", "beszel_identity")
    password = _resolve_field(body, "password", "beszel_password")
    verify_tls = bool(body.get("verify_tls", True))
    _log_provider_test_start("beszel", target=hub_url or "(unset)")
    if not hub_url or not identity or not password:
        return _stamp_test_success("beszel", {
            "ok": False,
            "detail": "Hub URL, identity and password are all required",
        }, target=hub_url or "(unset)")
    result = await _beszel.probe_hub(
        hub_url, identity, password, verify_tls=verify_tls, timeout=10.0,
    )
    # `probe_hub` returns ``{systems: {...}}`` — adapt to the shared
    # ``hosts`` shape so the helper can produce the standard summary.
    adapted = {"hosts": result.get("systems") or {},
               "error": result.get("error")}
    return _stamp_test_success("beszel", _format_provider_test_summary(
        adapted,
        target_label="hub",
        item_singular="system",
        item_plural="system(s)",
        count_key="system_count",
        items_key="systems",
    ), target=hub_url)


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/telegram/links")
async def api_telegram_links_list(
    _admin: AdminUser,
):
    """Admin-only: list every persisted Telegram → OmniGrid user
    mapping. Returns ``{links: [{telegram_user_id, username, role}]}``
    sorted by username for the admin datatable. Role is joined from
    `users.role` so the table can show admin / readonly badges next
    to each linked account.
    """
    from logic import telegram_listener as _tg
    mappings = _tg.load_mappings()
    out: list[dict] = []
    for tg_id_str, entry in mappings.items():
        if not isinstance(entry, dict):
            continue
        username = entry.get("username")
        if not username:
            continue
        try:
            tg_id = int(tg_id_str)
        except (TypeError, ValueError):
            continue
        role = _tg.lookup_user_role(username) or "unknown"
        out.append({
            "telegram_user_id": tg_id,
            "username": username,
            "role": role,
            "linked_at_ms": int(entry.get("linked_at_ms") or 0),
        })
    out.sort(key=lambda r: (r["username"] or "", r["telegram_user_id"]))
    return {"links": out}


@app.delete("/api/telegram/links/{telegram_user_id}")
async def api_telegram_links_unlink(
    telegram_user_id: int,
    _admin: AdminUser,
):
    """Admin-only: drop one Telegram → OmniGrid mapping by Telegram
    user_id. Used by the admin datatable's row-action Unlink button.
    Returns ``{removed: <username> | null}``.
    """
    from logic import telegram_listener as _tg
    mappings = _tg.load_mappings()
    key = str(int(telegram_user_id))
    removed_entry = mappings.pop(key, None)
    removed_username = None
    if isinstance(removed_entry, dict):
        removed_username = removed_entry.get("username")
        _tg.save_mappings(mappings)
        # Audit row — an admin revoking a user's Telegram access must be
        # forensically visible (the Telegram-side /unlink audits separately).
        with db_conn() as _c:
            _ops_mod.write_admin_audit(
                _c, "telegram_unlink",
                target_kind="telegram", target_name=str(removed_username or key),
                target_id=key, actor=getattr(_admin, "username", "ui"),
                message=f"Admin unlinked Telegram user {key}"
                        + (f" ({removed_username})" if removed_username else ""),
            )
    return {"removed": removed_username}


@app.post("/api/telegram/test")
async def api_telegram_test(
    request: Request,
    _admin: AdminUser,
):
    """Admin-only: send a Telegram test message.

    Accepts unsaved form values (bot_token / chat_id / thread_id) so
    the admin can verify a new config before saving. Falls back to the
    persisted values when fields are blank (keep-current contract).
    Returns ``{ok, detail, status}``.
    """
    from logic import notify_telegram as _tg
    body = await request.json()
    bot_token = _resolve_field(body, "bot_token", "telegram_bot_token")
    chat_id = _resolve_field(body, "chat_id", "telegram_chat_id")
    thread_id_raw = body.get("thread_id")
    if thread_id_raw is None or str(thread_id_raw).strip() == "":
        thread_id = (get_setting(Settings.TELEGRAM_THREAD_ID) or "").strip()
    else:
        thread_id = str(thread_id_raw).strip()
    # A "Test connection" is a DIAGNOSTIC, not a broadcast — scope it to
    # the PRIMARY (first) chat only. `telegram_chat_id` is a CSV so a
    # deploy serving a group PLUS individual operator DMs fans REAL
    # notifications out to every entry; but the operator clicking Test
    # only wants to verify token+chat wiring, NOT spam a test message to
    # every recipient (operator-reported: the test reached people who'd
    # DM'd the bot but weren't meant to receive a manual probe). Take the
    # first non-empty CSV entry; note any skipped chats in the detail so
    # the operator understands the scope.
    _all_chats = [p.strip() for p in str(chat_id or "").split(",") if p.strip()]
    primary_chat = _all_chats[0] if _all_chats else ""
    _skipped = len(_all_chats) - 1 if len(_all_chats) > 1 else 0
    # `target` for the log = primary chat (operator-recognisable; the bot
    # token is a secret + bot identity is constant across all chats).
    _tg_target = f"chat={primary_chat}" if primary_chat else "(no chat_id)"
    if thread_id:
        _tg_target += f"/thread={thread_id}"
    _log_provider_test_start("telegram", target=_tg_target)
    if not bot_token:
        return _stamp_test_success("telegram", {
            "ok": False, "detail": "Bot token is required", "status": 0,
        }, target=_tg_target)
    if not primary_chat:
        return _stamp_test_success("telegram", {
            "ok": False, "detail": "Chat ID is required", "status": 0,
        }, target=_tg_target)
    result = await _tg.probe(
        bot_token=bot_token,
        chat_id=primary_chat,
        thread_id=thread_id,
    )
    # Append a note when additional chats were intentionally NOT probed,
    # so the operator knows the test only pinged the primary destination.
    if _skipped and isinstance(result, dict) and result.get("ok"):
        _base = str(result.get("detail") or "").strip()
        result["detail"] = (
            f"{_base} (primary chat only; {_skipped} other configured "
            f"chat(s) not pinged — real notifications still reach all)"
        ).strip()
    return _stamp_test_success("telegram", result, target=_tg_target)


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/snmp/test")
async def api_snmp_test(
    request: Request,
    _admin: AdminUser,
):
    """Admin-only: probe one SNMP host.

    Body fields are all optional — missing values fall through to the
    persisted defaults via ``_resolve_field``, mirroring the test-
    connection contract every other provider implements:

      * ``host``      — required (no global default)
      * ``community`` — falls back to ``snmp_default_community``
      * ``version``   — falls back to ``snmp_default_version``
      * ``port``      — falls back to ``snmp_default_port``
      * ``v3_user``    — falls back to ``snmp_v3_user``
      * ``v3_auth_key``/``v3_priv_key`` — keep-current-if-blank
                                          (write-only secret contract)

    Returns ``{ok, detail, host_key}`` with a short summary suitable
    for the Settings panel's Test button + the Admin → Hosts editor's
    per-row test column.
    """
    from logic import snmp as _snmp
    body = await request.json()
    host = (body.get("host") or "").strip()
    _log_provider_test_start("snmp", target=host or "(unset)")
    if not host:
        return _stamp_test_success("snmp", {
            "ok": False, "detail": "host is required",
        }, target="(unset)")
    if not _snmp.has_snmp_support():
        return _stamp_test_success("snmp", {
            "ok": False,
            "detail": "pysnmp not installed (pip install pysnmp)",
        }, target=host)
    community = _resolve_field(body, "community", "snmp_default_community", "public")
    version = (_resolve_field(body, "version", "snmp_default_version", "v2c")
               .strip().lower() or "v2c")
    if version not in ("v2c", "v3"):
        return _stamp_test_success("snmp", {
            "ok": False,
            "detail": f"unsupported version {version!r} — use v2c or v3",
        }, target=host)
    try:
        port = int(_resolve_field(body, "port", "snmp_default_port", "161") or "161")
    except (TypeError, ValueError):
        port = 161
    v3_user = _resolve_field(body, "v3_user", "snmp_v3_user")
    v3_auth = _resolve_field(body, "v3_auth_key", "snmp_v3_auth_key")
    v3_priv = _resolve_field(body, "v3_priv_key", "snmp_v3_priv_key")
    # Per-host walk_concurrency override — Test connection respects
    # the same per-host knob as the sampler / debug paths so the
    # operator's smoke test runs at the SAME concurrency the live
    # probe will use. Falls back to None (= use the global tunable)
    # when the body doesn't carry a value.
    walk_conc_test = body.get("walk_concurrency") if isinstance(body, dict) else None
    try:
        walk_conc_test = int(walk_conc_test) if walk_conc_test else None
    except (TypeError, ValueError):
        walk_conc_test = None
    # Per-host vendor MIB selector — same payload key the sampler reads.
    # None = auto-detect; explicit list = bypass auto-detect.
    vendors_test = _clean_vendors_input(
        body.get("vendors") if isinstance(body, dict) else None
    )
    # Per-host wall_clock_budget — Test runs the probe with the
    # operator's per-host override (if set) so the smoke test runs
    # under the SAME budget the live probe will use. Same NPM
    # ceiling as the debug panel: Test traverses
    # browser → NPM → OmniGrid, so the global tunable's higher value
    # (operators commonly set 120s for the internal sampler) is
    # capped at the proxy-safe ceiling here. Per-host override can
    # decrease but not raise above the cap.
    wcb_test_raw = body.get("wall_clock_budget") if isinstance(body, dict) else None
    try:
        wcb_test = float(wcb_test_raw) if wcb_test_raw else None
    except (TypeError, ValueError):
        wcb_test = None
    _TEST_BUDGET_CAP = 50.0
    wcb_resolved = (
        min(_TEST_BUDGET_CAP, wcb_test) if wcb_test else _TEST_BUDGET_CAP
    )

    # consume tuning_snmp_probe_timeout_seconds. Test endpoint uses
    # max(tunable, 10s) so a tiny tunable doesn't cripple manual smoke probes.
    snmp_timeout = max(10.0, float(tuning.tuning_int(Tunable.SNMP_PROBE_TIMEOUT_SECONDS)))
    result = await _snmp.probe_snmp(
        host,
        community=community,
        version=version,
        port=port,
        v3_user=v3_user,
        v3_auth_key=v3_auth,
        v3_priv_key=v3_priv,
        timeout=snmp_timeout,
        # Operator clicked Test — bypass the unreachable-cool-down so
        # they can validate connectivity NOW even if the last automatic
        # probe failed and armed the 5-min throttle. Without this, an
        # operator fixing an SNMP misconfig (community / port / v3
        # creds) could never re-test until the cool-down expired.
        bypass_cooldown=True,
        walk_concurrency=walk_conc_test,
        vendors=vendors_test,
        wall_clock_budget=wcb_resolved,
    )
    # If the operator-initiated probe succeeded, clear any pending
    # cool-down so the next automatic sampler tick picks up the host
    # immediately instead of waiting another 5 min for the throttle
    # to age out. The cool-down clear inside probe_snmp itself only
    # fires when the probe actually got data — so a 200-but-empty
    # response doesn't reset the throttle by accident.
    if result.get("hosts") and not result.get("error"):
        try:
            _snmp.clear_cooldown(host, port)
        except (AttributeError, KeyError):
            pass
    # Diagnostics surface for operators retesting after a per-host
    # walk_concurrency / wall_clock_budget edit — confirm the new value
    # was actually picked up without opening the debug panel. probe_snmp
    # builds these on both success and timeout paths so they're
    # available regardless of outcome.
    diag_keys = (
        "walk_concurrency_resolved", "walk_concurrency_source",
        "walk_concurrency_global",
        "wall_clock_budget_resolved", "wall_clock_budget_source",
        "wall_clock_budget_global",
        "active_vendors", "active_vendors_source",
    )
    diag = {k: result[k] for k in diag_keys if k in result}
    if result.get("error") and not result.get("hosts"):
        return _stamp_test_success("snmp", {
            "ok": False,
            "detail": _humanise_probe_error(result["error"], "SNMP"),
            **diag,
        }, target=host)
    hosts = result.get("hosts") or {}
    if not hosts:
        return _stamp_test_success("snmp", {
            "ok": False,
            "detail": "no parseable response — check community / version / port",
            **diag,
        }, target=host)
    host_key = next(iter(hosts))
    stats = hosts[host_key]
    cpu = stats.get("host_cpu_percent")
    mem = stats.get("host_mem_total") or 0
    disk = stats.get("host_disk_total") or 0
    nics = len(stats.get("network_ifaces") or [])
    detail_bits = [f"OK — {host_key}"]
    if cpu is not None:
        try:
            detail_bits.append(f"cpu={int(cpu)}%")
        except (TypeError, ValueError):
            pass
    if mem:
        detail_bits.append(f"mem={mem // (1024 ** 3)} GB")
    if disk:
        detail_bits.append(f"disk={disk // (1024 ** 3)} GB")
    if nics:
        detail_bits.append(f"nics={nics}")
    return _stamp_test_success("snmp", {
        "ok": True, "detail": " · ".join(detail_bits),
        "host_key": host_key, **diag,
    }, target=host)


# ----------------------------------------------------------------------------
# Asset inventory — <asset-api-host> OAuth2 client_credentials. Manual
# refresh only; reads go through the file cache at /app/data/asset_inventory.json.
# ----------------------------------------------------------------------------
def _is_asset_inventory_enabled() -> bool:
    """Master-switch gate for the Asset Inventory integration. Default
    True so existing deploys don't change behaviour. When false the
    three /api/asset-inventory endpoints short-circuit and the
    asset_inventory_refresh schedule kind no-ops — the persisted
    credentials stay in the settings table so the operator can flip
    back on without re-typing. Mirrors the apprise / portainer / ssh /
    open_meteo gate pattern."""
    return (get_setting(Settings.ASSET_INVENTORY_ENABLED, "true") or "true").lower() == "true"


@app.get("/api/asset-inventory")
async def api_asset_inventory(_admin: AdminUser):
    """Admin-only: return the cached asset inventory snapshot.

    Returns the shape ``{ok, ts, count, assets, upstream, error}``. An
    empty / missing cache is reported via ``ok=false`` + ``error`` so the
    UI can render an empty state without special-casing HTTP 404.
    """
    from logic import asset_inventory as _ai
    if not _is_asset_inventory_enabled():
        # Short-circuit when the master switch is off — the SPA's host
        # drawer + Admin → Hosts auto-fill paths consume `ok` and treat
        # disabled / failed identically (empty assets list).
        return {"ok": False, "ts": 0, "count": 0, "assets": [],
                "error": "asset_inventory_disabled"}
    return _ai.load_cache()


@app.post("/api/asset-inventory/test")
async def api_asset_inventory_test(
    request: Request,
    _admin: AdminUser,
):
    """Admin-only: validate asset-inventory credentials end-to-end.

    Test still runs even when the master switch is off — operators
    need to verify credentials BEFORE flipping the switch back on.
    Refresh / read paths honour the gate; this one doesn't.

    Accepts unsaved form values or falls back to the persisted settings
    when a field is blank. Branches on ``auth_mode``:

      - ``oauth2`` — runs the OAuth2 token exchange (``probe_token``)
        and reports the resulting token type / expiry.
      - ``lifetime_token`` — does ONE POST to ``{base_url}/services.php``
        with ``X-Authorization: Bearer <token>`` and reports the asset
        count it got back. A successful fetch here means the exact
        same request the refresh path makes will also work.
    """
    from logic import asset_inventory as _ai
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    auth_mode = (body.get("auth_mode") or "").strip().lower() \
                or (get_setting(Settings.ASSET_INVENTORY_AUTH_MODE) or "oauth2")
    if auth_mode not in ("oauth2", "lifetime_token"):
        auth_mode = "oauth2"
    # The log target is the auth-mode-specific endpoint — for
    # lifetime_token it's the base_url + the lifetime list path; for
    # oauth2 it's the token_url. Compute below; default to the mode
    # name so the START log line always has something useful.
    _ai_target_for_log = f"mode={auth_mode}"
    _log_provider_test_start("asset_inventory", target=_ai_target_for_log)
    # honour the `asset_inventory_verify_tls` toggle here too.
    # Body wins (so admins can flip the form's checkbox OFF and Test
    # a self-signed asset API before saving); otherwise the persisted
    # setting (default True) applies. Mirrors the OIDC-test shape.
    body_verify_tls = body.get("verify_tls")
    if body_verify_tls is None:
        verify_tls = _asset_inventory_verify_tls()
    else:
        verify_tls = bool(body_verify_tls)
    if auth_mode == "lifetime_token":
        base_url = (
            (body.get("base_url") or "").strip().rstrip("/")
            or (get_setting(Settings.ASSET_INVENTORY_BASE_URL) or "").strip().rstrip("/")
        )
        lifetime_token = body.get("lifetime_token") or ""
        if not lifetime_token:
            lifetime_token = get_setting(Settings.ASSET_INVENTORY_LIFETIME_TOKEN) or ""
        service = (
            (body.get("service") or "").strip()
            or (get_setting(Settings.ASSET_INVENTORY_SERVICE) or "").strip()
        )
        action = (
            (body.get("action") or "").strip()
            or (get_setting(Settings.ASSET_INVENTORY_ACTION) or "").strip()
        )

        def _bound(from_body, setting_key):
            raw = from_body
            if raw is None or str(raw).strip() == "":
                raw = get_setting(setting_key) or ""
            s = str(raw).strip()
            try:
                return int(s) if s else None
            except ValueError:
                return None

        min_value = _bound(body.get("min_value"), "asset_inventory_min_value")
        max_value = _bound(body.get("max_value"), "asset_inventory_max_value")

        if not base_url or not lifetime_token:
            return _stamp_test_success("asset_inventory", {
                "ok": False,
                "detail": "base_url and lifetime_token are both required",
            }, target=base_url or "(unset)")
        endpoint = base_url.rstrip("/") + _ai.DEFAULT_LIFETIME_LIST_PATH
        result = await _ai.fetch_assets_lifetime_token(
            endpoint, lifetime_token,
            service=service, action=action,
            min_value=min_value, max_value=max_value,
            verify_tls=verify_tls,
        )
        if result.get("ok"):
            count = len(result.get("assets") or [])
            return _stamp_test_success("asset_inventory", {
                "ok": True,
                "detail": f"OK — fetched {count} asset(s) from {endpoint}",
            }, target=endpoint)
        out = {"ok": False, "detail": result.get("error") or "auth failed"}
        if "error_code" in result:
            out["error_code"] = result["error_code"]
            out["error_params"] = result.get("error_params", {})
        return _stamp_test_success("asset_inventory", out, target=endpoint)
    # Default: OAuth2 client_credentials.
    token_url = (
        (body.get("token_url") or "").strip()
        or (get_setting(Settings.ASSET_INVENTORY_TOKEN_URL) or "")
    )
    client_id = (
        (body.get("client_id") or "").strip()
        or (get_setting(Settings.ASSET_INVENTORY_CLIENT_ID) or "")
    )
    scope = (body.get("scope") or "").strip() \
            or (get_setting(Settings.ASSET_INVENTORY_SCOPE) or "")
    client_secret = body.get("client_secret") or ""
    if not client_secret:
        client_secret = get_setting(Settings.ASSET_INVENTORY_CLIENT_SECRET) or ""
    if not token_url or not client_id or not client_secret:
        return _stamp_test_success("asset_inventory", {
            "ok": False,
            "detail": "token_url, client_id and client_secret are all required",
        }, target=token_url or "(unset)")
    result = await _ai.probe_token(
        token_url, client_id, client_secret, scope=scope, verify_tls=verify_tls,
    )
    if result.get("ok"):
        expires_in = result.get("expires_in") or 0
        return _stamp_test_success("asset_inventory", {
            "ok": True,
            "detail": (f"OK — got {result.get('token_type') or 'Bearer'} token"
                       + (f", expires in {expires_in}s" if expires_in else "")),
        }, target=token_url)
    return _stamp_test_success("asset_inventory", {
        "ok": False, "detail": result.get("error") or "auth failed",
    }, target=token_url)


@app.post("/api/asset-inventory/refresh")
async def api_asset_inventory_refresh(
    _admin: AdminUser,
):
    """Admin-only: probe auth + fetch assets + overwrite the cache.

    Manual refresh only — there is no lifespan loop. Branches on the
    persisted ``asset_inventory_auth_mode`` setting. Returns the
    summary from ``refresh_cache`` so the UI can show a toast with the
    new count and timestamp.
    """
    from logic import asset_inventory as _ai
    if not _is_asset_inventory_enabled():
        return {"ok": False, "count": 0, "ts": 0,
                "error": "asset_inventory_disabled"}
    base_url = (get_setting(Settings.ASSET_INVENTORY_BASE_URL) or "").strip().rstrip("/")
    auth_mode = (get_setting(Settings.ASSET_INVENTORY_AUTH_MODE) or "oauth2").strip().lower()
    if auth_mode not in ("oauth2", "lifetime_token"):
        auth_mode = "oauth2"
    if auth_mode == "lifetime_token":
        lifetime_token = get_setting(Settings.ASSET_INVENTORY_LIFETIME_TOKEN) or ""
        service = (get_setting(Settings.ASSET_INVENTORY_SERVICE) or "").strip()
        action = (get_setting(Settings.ASSET_INVENTORY_ACTION) or "").strip()
        min_raw = (get_setting(Settings.ASSET_INVENTORY_MIN_VALUE) or "").strip()
        max_raw = (get_setting(Settings.ASSET_INVENTORY_MAX_VALUE) or "").strip()
        try:
            min_value = int(min_raw) if min_raw else None
        except ValueError:
            min_value = None
        try:
            max_value = int(max_raw) if max_raw else None
        except ValueError:
            max_value = None
        if not base_url or not lifetime_token:
            return {"ok": False, "count": 0, "ts": 0,
                    "error": "asset_inventory base_url and lifetime_token are required "
                             "for the lifetime-token auth mode"}
        result = await _ai.refresh_cache(
            base_url,
            verify_tls=_asset_inventory_verify_tls(),
            auth_mode=_ai.AUTH_MODE_LIFETIME_TOKEN,
            lifetime_token=lifetime_token,
            service=service,
            action=action,
            min_value=min_value,
            max_value=max_value,
        )
        _audit_asset_refresh(_admin, result, "lifetime_token")
        return result
    token_url = (get_setting(Settings.ASSET_INVENTORY_TOKEN_URL) or "").strip()
    client_id = (get_setting(Settings.ASSET_INVENTORY_CLIENT_ID) or "").strip()
    client_secret = get_setting(Settings.ASSET_INVENTORY_CLIENT_SECRET) or ""
    scope = (get_setting(Settings.ASSET_INVENTORY_SCOPE) or "").strip()
    if not base_url or not token_url or not client_id or not client_secret:
        return {"ok": False, "count": 0, "ts": 0,
                "error": "asset_inventory_* settings are incomplete — "
                         "configure base_url / token_url / client_id / client_secret"}
    result = await _ai.refresh_cache(
        base_url,
        token_url=token_url,
        client_id=client_id,
        client_secret=client_secret,
        scope=scope,
        verify_tls=_asset_inventory_verify_tls(),
    )
    _audit_asset_refresh(_admin, result, "oauth2")
    return result


def _audit_asset_refresh(admin: auth.User, result: dict, auth_mode: str) -> None:
    """Write a `history` row for an operator-initiated asset-inventory
    refresh. Reuses the existing scheduler-kind `asset_inventory_refresh`
    op_type so both paths land in one filter row; actor field distinguishes
    operator-vs-scheduler-driven runs.
    """
    try:
        ok = bool(result and result.get("ok"))
        with db_conn() as c:
            _ops_mod.write_admin_audit(
                c, "asset_inventory_refresh",
                target_kind="asset_inventory", target_name=auth_mode,
                actor=admin.username or schedules.UNKNOWN_ACTOR,
                status="success" if ok else "error",
                message=(f"asset_inventory manual refresh by {admin.username or 'operator'}: "
                         f"ok={ok} count={result.get('count') if result else 0} "
                         f"auth_mode={auth_mode}"),
                error=(result.get("error") if result else None) or None,
            )
    except Exception as e:
        print(f"[asset_inventory] manual-refresh audit-row write failed: {e}")


def _asset_inventory_verify_tls() -> bool:
    """Read the operator-controlled `asset_inventory_verify_tls` setting
    on every refresh. Default True so first-boot deploys
    keep validating TLS — homelab operators with self-signed asset APIs
    flip the toggle in Admin → Asset Inventory."""
    raw = (get_setting(Settings.ASSET_INVENTORY_VERIFY_TLS, "true") or "true").strip().lower()
    return raw != "false"


# Local aliases for the canonical merge helpers in `logic/merge.py`.
# Was a duplicated private implementation here AND in logic/gather.py;
# centralised so the merge semantics stay byte-
# identical across the two call sites without a "don't import private
# helpers across modules" caveat.


def _resolve_field(body: dict, body_key: str,
                   setting_key: str, default: str = "") -> str:
    """Pick a field value from ``body`` first, falling back to the
    persisted ``settings`` table when ``body[body_key]`` is blank.

    Standard contract for the test-connection endpoints (in
    notes/code_review_2026-04-25.md): operators can hit Test BEFORE
    Save without re-typing every field — Test reuses whatever the
    previous Save committed. Empty / whitespace-only bodies, missing
    keys, and explicit None all fall through to the saved value;
    only a non-empty operator-typed string overrides.
    """
    raw = body.get(body_key)
    if raw is not None:
        s = str(raw).strip()
        if s:
            return s
    return get_setting(setting_key, default) or default


def _humanise_probe_error(raw: str, target_label: str) -> str:
    """Pattern-match common upstream-failure shapes into operator-readable
    one-liners.

    Probes (Beszel / Pulse / Webmin) catch exceptions internally and return
    a stringified error in their ``error`` field. The raw text is sometimes
    a multi-line JSON dump from PocketBase, a bare ``EOF`` from an
    unreachable host, or an httpx repr — none of which the operator can act
    on. This helper compresses the common cases into a short
    "what-happened + what-to-do" summary, keeping the original tail in
    parentheses so the diagnostic is still discoverable.

    Falls through to the original string when no pattern matches.
    """
    if not raw:
        return raw
    text = str(raw).strip()
    if not text:
        return raw
    low = text.lower()
    # Multi-line dumps — keep first line, hint where the rest is.
    if "\n" in text:
        first = text.splitlines()[0].strip()
        text = f"{first} (see Admin → Logs for the full upstream payload)"
        low = first.lower()

    # HTTP-status patterns.
    if "401" in low or "unauthorized" in low or "unauthorised" in low:
        return f"{target_label} rejected the credentials (HTTP 401 — token / password expired or missing required scope)"
    if "403" in low or "forbidden" in low:
        return f"{target_label} returned HTTP 403 — credentials lack the required scope or the user is disabled"
    if "404" in low or "not found" in low:
        return f"{target_label} returned HTTP 404 — URL path / endpoint id may be wrong"
    if "500" in low or "internal server error" in low:
        return f"{target_label} returned HTTP 500 — upstream is broken; check the {target_label} logs ({text})"
    if "503" in low or "unavailable" in low:
        return f"{target_label} returned HTTP 503 — upstream is starting / overloaded; retry shortly"
    # Network-level failures (httpx wraps these in ConnectError / ReadTimeout).
    if "name or service not known" in low or "nodename nor servname" in low or "getaddrinfo" in low:
        return f"DNS resolution failed for the {target_label} URL — check the hostname"
    if "connection refused" in low:
        return f"{target_label} refused the connection — host unreachable or wrong port"
    if "connection reset" in low:
        return f"{target_label} reset the connection mid-request — TLS / network issue"
    if "timeout" in low or "timed out" in low:
        return f"{target_label} did not respond in time — host slow or unreachable"
    if "certificate" in low or "ssl" in low or "tls" in low:
        return f"TLS handshake failed against {target_label} — disable verify_tls if the cert is self-signed"
    if low == "eof" or "eof " in low or low.endswith(" eof"):
        return f"{target_label} closed the connection unexpectedly (EOF) — host crashed mid-request or wrong port"
    # Webmin-specific patterns the probe surfaces verbatim. Catch them
    # so the operator gets actionable copy ("locked out for X seconds")
    # instead of a `webmin: ...` raw prefix.
    if "auth cool-down" in low or "auth cooldown" in low:
        # Pull "<N>s remaining" via plain string scanning — no regex.
        # The previous `re.search(r"(\d+)s remaining", low)` (and even
        # the bounded `\d{1,10}` follow-up) tripped CodeQL
        # py/polynomial-redos because the haystack flows from
        # /api/snmp/test's body. Walking the string by hand and
        # capping the digit run at 10 chars eliminates the regex
        # entirely; same behaviour for legitimate inputs, fixed-time
        # for pathological ones.
        idx = low.find("s remaining")
        digits = ""
        if idx > 0:
            end = idx
            start = idx
            # Cap the walk so a million-byte digit run can't degrade.
            while start > 0 and low[start - 1].isdigit() and (end - start) < 10:
                start -= 1
            digits = low[start:end]
        if digits.isdigit():
            return (f"{target_label} auth is in cool-down ({digits}s remaining) — "
                    f"a previous Test failed; wait it out before retrying")
        return f"{target_label} auth is in cool-down — wait a few minutes before retrying"
    if "all modules failed" in low:
        return f"{target_label} reached the host but every probed module ({target_label} system-status / package-updates / mount / net) failed — likely module-permission misconfig on the upstream"
    return text


def _format_provider_test_summary(
    probe_result: dict,
    *,
    target_label: str,
    item_singular: str,
    item_plural: str,
    count_key: str,
    items_key: str,
) -> dict:
    """Standard ``{ok, detail, ...}`` shape for the provider test
    endpoints whose ``probe_*`` helpers return ``{hosts: {key: stats}, error}``.

    Pulse + Beszel both produce identical "OK — reached <X>, N
    <thing>(s) visible: a, b, c (+rest)" summaries from the same
    ``hosts`` map. One helper keeps the wording, truncation
    threshold, and key ordering identical so a future copy-paste isn't
    needed; Webmin and Portainer keep their bespoke shapes because
    their probe contracts are different (Webmin returns a single
    host_key; Portainer inspects ``Version``).

    Returns the exact dict the route should return.
    """
    err = probe_result.get("error")
    if err:
        return {"ok": False, "detail": _humanise_probe_error(str(err), target_label)}
    hosts = probe_result.get("hosts") or {}
    names = sorted(hosts.keys())
    label = item_singular if len(hosts) == 1 else item_plural
    detail = (f"OK — reached {target_label}, {len(hosts)} {label} visible: "
              + (", ".join(names[:5]) or "none"))
    if len(names) > 5:
        detail += f" (+{len(names) - 5} more)"
    return {"ok": True, "detail": detail,
            count_key: len(hosts), items_key: names}


# ============================================================================
# Apps feature — top-level Apps view + reusable service catalog templates.
# Admin-only across the board: catalog edits + the aggregate view both gate
# on `require_admin`. Reads the existing `hosts_config[].services[]` array
# (per-host instances) + the new `service_catalog` table (templates). The
# cross-host aggregate (`/api/apps`) groups instances by catalog_id or name
# so an operator can see "Plex is up on 2 of 3 hosts" at a glance without
# walking every host card.
# ============================================================================
@app.get("/api/services/catalog")
async def api_services_catalog_list(_admin: AdminUser):
    """Admin-only: list every catalog template (builtin + operator) ordered by name."""
    from logic import service_catalog as _sc
    return {"entries": _sc.list_catalog()}


@app.post("/api/services/catalog")
async def api_services_catalog_create(payload: dict[str, Any], request: Request, _admin: AdminUser):
    """Admin-only: create a new operator-authored catalog template.

    Body shape:
        {
            "name": str,                # required
            "slug": str,                # optional, derived from name if blank
            "icon": str,                # optional, defaults to slug
            "description": str,         # optional
            "default_ports": [...]      # optional list of port dicts
        }
    """
    from logic import service_catalog as _sc
    try:
        # Build kwargs explicitly so the IDE's default-arg-redundancy
        # check doesn't fire on the operator-empty fields. The
        # `name` field is required; every other field flows through
        # `create_catalog_entry`'s own defaults when the payload omits
        # it. Operator-provided values still propagate through the
        # `or ""` / `or []` falsy-fallbacks identically to the legacy
        # form. `source="operator"` is the default but spelled out at
        # the call site as documentation that this endpoint creates
        # operator-authored templates (not builtins).
        entry = _sc.create_catalog_entry(
            name=payload.get("name") or "",
            slug=(payload.get("slug") or "").strip(),
            icon=(payload.get("icon") or "").strip(),
            description=(payload.get("description") or "").strip(),
            default_ports=payload.get("default_ports") or [],
        )
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except sqlite3.IntegrityError as ie:
        raise HTTPException(409, f"slug already exists: {ie}")
    with db_conn() as _c:
        _ops_mod.write_admin_audit(
            _c, "services_catalog_create",
            target_kind="apps_catalog",
            target_name=entry.get("name") or "",
            target_id=str(entry.get("id") or ""),
            actor=_actor_from(request),
        )
    # Invalidate the `_shape_host_apps` catalog cache so the next
    # /api/hosts/list / one fan-out sees the new template instead of
    # waiting up to 5s for the TTL.
    _invalidate_apps_cache()
    return {"ok": True, "entry": entry}


@app.patch("/api/services/catalog/{cid}")
async def api_services_catalog_update(cid: int, payload: dict[str, Any], request: Request, _admin: AdminUser):
    """Admin-only: update a catalog template. Partial — only non-None
    fields are written."""
    from logic import service_catalog as _sc
    try:
        entry = _sc.update_catalog_entry(
            cid,
            name=payload.get("name"),
            slug=payload.get("slug"),
            icon=payload.get("icon"),
            description=payload.get("description"),
            default_ports=payload.get("default_ports"),
        )
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except sqlite3.IntegrityError as ie:
        raise HTTPException(409, f"slug conflict: {ie}")
    if entry is None:
        raise HTTPException(404, "catalog entry not found")
    with db_conn() as _c:
        _ops_mod.write_admin_audit(
            _c, "services_catalog_update",
            target_kind="apps_catalog",
            target_name=entry.get("name") or "",
            target_id=str(cid),
            actor=_actor_from(request),
        )
    _invalidate_apps_cache()
    return {"ok": True, "entry": entry}


@app.delete("/api/services/catalog/{cid}")
async def api_services_catalog_delete(cid: int, request: Request, _admin: AdminUser):
    """Admin-only: delete a catalog template (builtin or operator-authored).

    Per-host chips linked via `catalog_id` are NOT cascade-removed — they
    just lose their catalog binding and continue to render from their own
    `name` / `icon` / probe fields. The operator can re-link to another
    template later via Admin → Hosts.
    """
    from logic import service_catalog as _sc
    # Snapshot the template name BEFORE delete so the audit row carries
    # a human-readable label (template lookup is None after the row is
    # gone). Cheap — one DB hit upstream of the actual delete.
    pre_template = _sc.get_catalog_by_id(cid) or {}
    ok = _sc.delete_catalog_entry(cid)
    if not ok:
        raise HTTPException(404, "catalog entry not found")
    with db_conn() as _c:
        _ops_mod.write_admin_audit(
            _c, "services_catalog_delete",
            target_kind="apps_catalog",
            target_name=pre_template.get("name") or "",
            target_id=str(cid),
            actor=_actor_from(request),
        )
    _invalidate_apps_cache()
    return {"ok": True}


@app.post("/api/services/catalog/seed")
async def api_services_catalog_seed(request: Request, _admin: AdminUser):
    """Admin-only: re-seed missing built-in templates (operator deleted
    one and wants it back). Builtin slugs already present in the DB are
    NOT touched — operator edits survive."""
    from logic import service_catalog as _sc
    added = _sc.seed_builtins(force=True)
    with db_conn() as _c:
        _ops_mod.write_admin_audit(
            _c, "services_catalog_seeded",
            target_kind="apps_catalog",
            target_name="builtins",
            actor=_actor_from(request),
            message=f"Re-seeded {added} built-in templates",
        )
    _invalidate_apps_cache()
    return {"ok": True, "added": added}


@app.get("/api/services/catalog/export")
async def api_services_catalog_export(_admin: AdminUser):
    """Admin-only: export the whole catalog as a portable JSON pack
    (community-pack sharing / backup). Read-only — no audit row. The
    pack drops install-specific id/timestamps and keys on slug, so it
    re-imports cleanly on any install via the import endpoint."""
    from logic import service_catalog as _sc
    return _sc.export_catalog()


@app.post("/api/services/catalog/import")
async def api_services_catalog_import(payload: dict[str, Any], request: Request, _admin: AdminUser):
    """Admin-only: import a catalog pack. Accepts either a full export
    pack ({"entries": [...]}) or a bare list under ``entries``. Upserts
    by slug (existing slug updated, new slug created as an operator
    template); per-entry errors are collected, not fatal. Returns
    {created, updated, errors}."""
    from logic import service_catalog as _sc
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if entries is None and isinstance(payload, list):
        entries = payload
    result = _sc.import_catalog_entries(entries)
    with db_conn() as _c:
        _ops_mod.write_admin_audit(
            _c, "services_catalog_import",
            target_kind="apps_catalog",
            target_name="import",
            actor=_actor_from(request),
            message=f"Imported catalog pack: {result.get('created', 0)} created, "
                    f"{result.get('updated', 0)} updated, "
                    f"{len(result.get('errors') or [])} error(s)",
        )
    _invalidate_apps_cache()
    return {"ok": True, **result}


@app.post("/api/services/discover/{host_id}")
async def api_services_discover(host_id: str, _admin: AdminUser):
    """Admin-only: scan a host's known ports against every catalog
    template and return ranked binding proposals.

    Reads:
      - latest ``host_port_scans`` entry for the host (open-port list)
      - host's curated row from ``hosts_config`` (label + existing
        chips' catalog_ids — already-bound templates are skipped)

    Returns:
        {
            "host_id":         str,
            "host_label":      str,
            "detected_ports":  [int, ...],     # open ports from the latest scan
            "scanned_at":      int | 0,        # epoch seconds, 0 = no scan yet
            "existing_catalog_ids": [int, ...], # templates already bound
            "proposals":       [{...}, ...]    # see propose_bindings
        }
    """
    from logic.service_catalog import propose_bindings as _propose
    from logic.service_catalog import host_claimed_ports as _claimed_ports
    # Verify host exists in curated list + read its label + existing chips.
    hosts = _load_hosts_config()
    target = None
    for row in hosts:
        if (row.get("id") or "").strip() == host_id:
            target = row
            break
    if target is None:
        raise HTTPException(404, f"host not found: {host_id}")
    host_label = (target.get("label") or "").strip() or host_id
    # Existing catalog_ids on this host's chips — skip templates already bound.
    existing_catalog_ids: set[int] = set()
    for chip in (target.get("services") or []):
        if not isinstance(chip, dict):
            continue
        cid_int = _coerce_int_local(chip.get("catalog_id"))
        if cid_int:
            existing_catalog_ids.add(cid_int)
    # Read the latest port-scan results — reuse the same helper the
    # host drawer + API row use so we get the same data.
    scan_blob: dict[str, Any] = {}
    _populate_detected_ports(host_id, scan_blob)
    detected = [int(p["port"]) for p in (scan_blob.get("detected_ports") or [])
                if isinstance(p, dict) and p.get("port")]
    scanned_at = int(scan_blob.get("last_port_scan_ts") or 0)
    # Ports already owned by this host's existing chips — the wizard
    # won't propose a second app for a port another app already claims
    # (e.g. no Pi-hole / Nextcloud on 80 / 443 when AdGuard owns them).
    claimed_ports = _claimed_ports(target)
    proposals = _propose(
        host_id,
        detected_ports=detected,
        host_label=host_label,
        existing_catalog_ids=existing_catalog_ids,
        claimed_ports=claimed_ports,
    )
    return {
        "host_id": host_id,
        "host_label": host_label,
        "detected_ports": sorted(set(detected)),
        "scanned_at": scanned_at,
        "existing_catalog_ids": sorted(existing_catalog_ids),
        "proposals": proposals,
    }


# Split continuation:
from main_pkg.apps_routes import *  # noqa: E402,F401,F403


# noinspection DuplicatedCode
def __getattr__(name):
    """Module-level resolver for cross-module underscore-prefixed leaks.
    Delegates to the shared helper so the 33-line PEP 562 implementation
    lives in one place. See main_pkg._resolver for the full rationale.
    The 5-line delegator IS duplicated across 12 files — PEP 562 requires
    one __getattr__ per module; suppress the duplicated-code hint."""
    # noinspection PyProtectedMember
    from main_pkg._resolver import resolve
    return resolve(__name__, name)
