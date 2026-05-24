"""Continuation of `main` — second chunk in the chain.

Loading order:
  1. main.py runs top half (defines `app`, helpers, models).
  2. main.py end: `from main_pkg.core import *` triggers load.
  3. main_pkg.core top: `from main import *` pulls main's
     top-half symbols. Body runs; more routes register.
  4. main_pkg.core finishes; main.py continues with the next
     star-import (main_pkg.routes), which now sees every
     core-defined symbol via main's namespace.
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

# Re-import parent's namespace so decorators below find every
# symbol from main's top half.
from main import *  # noqa: E402,F401,F403



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
                hid = (r["host_id"] if hasattr(r, "keys") else r[0]) or ""
                cnt = int(r["rows"] if hasattr(r, "keys") else r[1] or 0)
                meta = curated_meta.get(hid) or {}
                shaped.append({
                    "host_id": hid,
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
    return out


class _SamplesPruneIn(BaseModel):
    """Body for the orphan-prune endpoint. Both fields required."""
    table: str
    host_id: str


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
    the SPA passes ``?hours=N`` for 1 / 24 / 168 / 720 ranges. Computes
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
        hours = max(1, min(int(hours or 24), 24 * 30))
    except (TypeError, ValueError):
        hours = 24
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
        hours = max(1, min(int(hours or 168), 24 * 30))
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
    return await _ai.test_provider(
        p,
        api_key=api_key,
        model=model,
        base_url=base_url,
    )


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
        # CLAUDE.md "Operator-private hostnames" / data-handling
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
    action_data, cleaned_text = _ai.parse_palette_action_data(text)
    if action_data is not None:
        out["text"] = cleaned_text
        out["action_data"] = action_data
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
# defaults so this branch is unreachable in practice). See CLAUDE.md
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
                    actor=_admin.username or "operator",
                    message=f"notification template {event!r} touched "
                            f"({', '.join(touched)}) by {_admin.username or 'operator'}",
                )
        except Exception as e:
            print(f"[notify] template-update audit-row write failed: {e}")
    return _shape_notify_template_row(event)


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
    samples = dict(_ops_mod.NOTIFY_TEMPLATE_SAMPLES)
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
    # resolver lands on the sample values. The SPA's "Send test"
    # button is admin-only, so the actor is whoever clicked it.
    samples = dict(_ops_mod.NOTIFY_TEMPLATE_SAMPLES)
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


def _stamp_test_success(provider: str, result: dict) -> dict:
    """Stamp `last_test_success_<provider>` in the settings KV when the
    test result reports ok=True. Returns the result dict unchanged so
    handlers can use it as `return _stamp_test_success("portainer",
    {...})`. DB-backed (not localStorage) so every operator + browser
    sees the same value across machines. Best-effort — a stamping
    failure logs and does NOT break the response.

    Surfaced back to the SPA via `/api/me`'s `client_config.last_test_success`
    block. The stamped timestamp is epoch seconds at the moment the
    success was recorded.
    """
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
    return _stamp_test_success("oidc", await oidc.test_discovery(issuer, verify_tls=verify_tls))


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
    # Portainer's API key isn't in the `settings` table — it lives in
    # the Portainer-specific settings dict — so this one keeps a
    # purpose-built fallback. Every other test endpoint below uses
    # the shared `_resolve_field` helper.
    api_key = (body.get("api_key") or "").strip()
    if not api_key:
        api_key = str(_portainer.get_portainer_settings().get("portainer_api_key") or "")
    if not url or not api_key:
        return {"ok": False, "status": 0, "detail": "URL and API key are both required"}
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
        return {"ok": False, "status": 0,
                "detail": f"endpoint_id must be an integer, got {raw_eid!r}"}
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
                return {"ok": False, "status": r.status_code,
                        "detail": _humanise_probe_error(raw, "Portainer")}
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
            })
        if ep.status_code == 404:
            # Specific Portainer-shaped message — keep the bespoke copy
            # rather than humanising. Operators recognise this exact
            # phrasing from the related fix.
            return {"ok": False, "status": 404,
                    "detail": f"endpoint {endpoint_id} not found on this Portainer",
                    "endpoint_id": endpoint_id}
        raw = f"endpoint probe HTTP {ep.status_code}: {ep.text[:200]}"
        return {"ok": False, "status": ep.status_code,
                "detail": _humanise_probe_error(raw, "Portainer"),
                "endpoint_id": endpoint_id}
    except Exception as e:
        # Network-level failures (DNS / refused / TLS / timeout) are
        # the cases the humaniser was designed for — let them flow
        # through it instead of surfacing the raw exception repr.
        raw = f"{type(e).__name__}: {e}"
        return {"ok": False, "status": 0,
                "detail": _humanise_probe_error(raw, "Portainer")}


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
    if not url or not token:
        return {"ok": False, "detail": "URL and API token are both required"}
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
    ))


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
    if not url or not user or not password:
        return {"ok": False,
                "detail": "URL, user and password are all required"}
    result = await _webmin.probe_webmin(
        url, user, password, verify_tls=verify_tls, timeout=10.0,
    )
    if result.get("error") and not result.get("hosts"):
        # follow-up: route Webmin's verbatim probe error
        # through the humaniser too. Common Webmin failure modes (auth
        # cool-down / module timeout / TLS handshake) all map cleanly.
        return {"ok": False,
                "detail": _humanise_probe_error(result["error"], "Webmin")}
    hosts = result.get("hosts") or {}
    if not hosts:
        return {"ok": False,
                "detail": "No host_key resolved — Webmin responded "
                          "but couldn't extract a hostname"}
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
    })


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
    if not hub_url or not identity or not password:
        return {"ok": False, "detail": "Hub URL, identity and password are all required"}
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
    ))


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
    if not bot_token:
        return {"ok": False, "detail": "Bot token is required", "status": 0}
    if not chat_id:
        return {"ok": False, "detail": "Chat ID is required", "status": 0}
    result = await _tg.probe(
        bot_token=bot_token,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    return _stamp_test_success("telegram", result)


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
    if not host:
        return {"ok": False, "detail": "host is required"}
    if not _snmp.has_snmp_support():
        return {"ok": False,
                "detail": "pysnmp not installed (pip install pysnmp)"}
    community = _resolve_field(body, "community", "snmp_default_community", "public")
    version = (_resolve_field(body, "version", "snmp_default_version", "v2c")
               .strip().lower() or "v2c")
    if version not in ("v2c", "v3"):
        return {"ok": False,
                "detail": f"unsupported version {version!r} — use v2c or v3"}
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
        return {"ok": False,
                "detail": _humanise_probe_error(result["error"], "SNMP"),
                **diag}
    hosts = result.get("hosts") or {}
    if not hosts:
        return {"ok": False,
                "detail": "no parseable response — check community / version / port",
                **diag}
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
    })


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
            return {"ok": False,
                    "detail": "base_url and lifetime_token are both required"}
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
            })
        out = {"ok": False, "detail": result.get("error") or "auth failed"}
        if "error_code" in result:
            out["error_code"] = result["error_code"]
            out["error_params"] = result.get("error_params", {})
        return out
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
        return {"ok": False,
                "detail": "token_url, client_id and client_secret are all required"}
    result = await _ai.probe_token(
        token_url, client_id, client_secret, scope=scope, verify_tls=verify_tls,
    )
    if result.get("ok"):
        expires_in = result.get("expires_in") or 0
        return _stamp_test_success("asset_inventory", {
            "ok": True,
            "detail": (f"OK — got {result.get('token_type') or 'Bearer'} token"
                       + (f", expires in {expires_in}s" if expires_in else "")),
        })
    return {"ok": False, "detail": result.get("error") or "auth failed"}


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
                actor=admin.username or "operator",
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
from logic.merge import merge_best as _merge_best  # noqa: E402


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
async def api_services_catalog_create(payload: dict[str, Any], _admin: AdminUser):
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
    return {"ok": True, "entry": entry}


@app.patch("/api/services/catalog/{cid}")
async def api_services_catalog_update(cid: int, payload: dict[str, Any], _admin: AdminUser):
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
    return {"ok": True, "entry": entry}


@app.delete("/api/services/catalog/{cid}")
async def api_services_catalog_delete(cid: int, _admin: AdminUser):
    """Admin-only: delete a catalog template (builtin or operator-authored).

    Per-host chips linked via `catalog_id` are NOT cascade-removed — they
    just lose their catalog binding and continue to render from their own
    `name` / `icon` / probe fields. The operator can re-link to another
    template later via Admin → Hosts.
    """
    from logic import service_catalog as _sc
    ok = _sc.delete_catalog_entry(cid)
    if not ok:
        raise HTTPException(404, "catalog entry not found")
    return {"ok": True}


@app.post("/api/services/catalog/seed")
async def api_services_catalog_seed(_admin: AdminUser):
    """Admin-only: re-seed missing built-in templates (operator deleted
    one and wants it back). Builtin slugs already present in the DB are
    NOT touched — operator edits survive."""
    from logic import service_catalog as _sc
    added = _sc.seed_builtins(force=True)
    return {"ok": True, "added": added}


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
    proposals = _propose(
        host_id,
        detected_ports=detected,
        host_label=host_label,
        existing_catalog_ids=existing_catalog_ids,
    )
    return {
        "host_id": host_id,
        "host_label": host_label,
        "detected_ports": sorted(set(detected)),
        "scanned_at": scanned_at,
        "existing_catalog_ids": sorted(existing_catalog_ids),
        "proposals": proposals,
    }


@app.post("/api/services/discover/{host_id}/apply")
async def api_services_discover_apply(host_id: str, payload: dict[str, Any], _admin: AdminUser):
    """Admin-only: bulk-bind a set of catalog templates to a host.

    Body shape:
        {
            "catalog_ids":   [1, 5, 7],   # required; templates to pin
            "probe_enabled": true         # optional; default true
        }

    Iterates the requested catalog_ids and creates one chip per
    template by reusing the same pin logic. Returns a per-template
    result list so the SPA can show "3 of 4 pinned" toasts:

        {
            "host_id": str,
            "applied": [{catalog_id, name, service_idx}, ...],
            "skipped": [{catalog_id, reason}, ...]
        }

    Idempotent on already-bound catalog_ids (skipped with
    ``reason="already_bound"``). Validation goes through
    ``_clean_host_services`` via ``set_setting`` so the contract stays
    uniform with the Admin → Hosts editor save path.
    """
    raw_ids = payload.get("catalog_ids") or []
    if not isinstance(raw_ids, list):
        raise HTTPException(400, "catalog_ids must be a list")
    probe_enabled = bool(payload.get("probe_enabled", True))
    # Load host + existing chips ONCE; mutate locally + persist ONCE at the
    # end so we don't write the settings row N times for an N-pin apply.
    hosts = _load_hosts_config()
    target_idx = -1
    for i, row in enumerate(hosts):
        if (row.get("id") or "").strip() == host_id:
            target_idx = i
            break
    if target_idx < 0:
        raise HTTPException(404, f"host not found: {host_id}")
    from logic.service_catalog import get_catalog_by_id as _get_cat
    existing_services = hosts[target_idx].get("services") or []
    if not isinstance(existing_services, list):
        existing_services = []
    existing_catalog_ids = {_coerce_int_local(chip.get("catalog_id")) for chip in existing_services
                            if isinstance(chip, dict)}
    existing_catalog_ids.discard(None)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for raw_cid in raw_ids:
        cid = _coerce_int_local(raw_cid)
        if not cid:
            skipped.append({"catalog_id": raw_cid, "reason": "invalid_id"})
            continue
        if cid in existing_catalog_ids:
            skipped.append({"catalog_id": cid, "reason": "already_bound"})
            continue
        tpl = _get_cat(cid)
        if not tpl:
            skipped.append({"catalog_id": cid, "reason": "not_found"})
            continue
        new_chip: dict[str, Any] = {
            "name": tpl.get("name") or "",
            "catalog_id": cid,
        }
        icon_resolved = tpl.get("icon") or tpl.get("slug") or ""
        if icon_resolved:
            new_chip["icon"] = icon_resolved
        default_ports = list(tpl.get("default_ports") or [])
        probe: dict[str, Any] = {"enabled": probe_enabled, "type": "tcp"}
        if default_ports:
            probe["ports"] = default_ports
        new_chip["probe"] = probe
        new_idx = len(existing_services)
        existing_services.append(new_chip)
        existing_catalog_ids.add(cid)
        applied.append({
            "catalog_id": cid,
            "name": tpl.get("name") or "",
            "service_idx": new_idx,
        })
    hosts[target_idx]["services"] = existing_services
    set_setting(Settings.HOSTS_CONFIG, json.dumps(hosts))
    return {
        "host_id": host_id,
        "applied": applied,
        "skipped": skipped,
    }


@app.post("/api/services/catalog/{cid}/pin")
async def api_services_catalog_pin(cid: int, payload: dict[str, Any], _admin: AdminUser):
    """Admin-only: pin a catalog template to a host.

    Creates a new chip in the target host's ``services[]`` array
    pre-filled from the template's defaults (name / icon / ports +
    `catalog_id` linkage so future template edits propagate). Operator
    overrides via `name` / `url` / `icon` / `probe_enabled` /
    `probe_type` flow through `_clean_host_services` for validation.

    Body shape:
        {
            "host_id":       str,           # required — target curated host
            "name":          str | null,    # optional override; defaults to template name
            "url":           str | null,    # optional clickable link
            "icon":          str | null,    # optional override; defaults to template icon
            "probe_enabled": bool,          # default true
            "probe_type":    "tcp" | "http" # default tcp
        }

    Returns the new chip's `service_idx` so the SPA can highlight /
    scroll-to / launch a manual probe immediately.
    """
    from logic import service_catalog as _sc
    template = _sc.get_catalog_by_id(cid)
    if template is None:
        raise HTTPException(404, f"catalog template {cid} not found")
    host_id = (payload.get("host_id") or "").strip()
    if not host_id:
        raise HTTPException(400, "host_id is required")
    # Verify the host exists in the curated list.
    hosts = _load_hosts_config()
    target_idx = -1
    for i, row in enumerate(hosts):
        if (row.get("id") or "").strip() == host_id:
            target_idx = i
            break
    if target_idx < 0:
        raise HTTPException(404, f"host not found in hosts_config: {host_id}")
    # Build the chip dict from template defaults + operator overrides.
    override_name = (payload.get("name") or "").strip()
    override_url = (payload.get("url") or "").strip()
    override_icon = (payload.get("icon") or "").strip()
    probe_enabled = bool(payload.get("probe_enabled", True))
    probe_type_raw = (payload.get("probe_type") or "tcp").strip().lower()
    probe_type = probe_type_raw if probe_type_raw in ("tcp", "http") else "tcp"
    new_chip: dict[str, Any] = {
        "name": override_name or template.get("name") or "",
        "catalog_id": cid,
    }
    icon_resolved = override_icon or template.get("icon") or template.get("slug") or ""
    if icon_resolved:
        new_chip["icon"] = icon_resolved
    if override_url:
        new_chip["url"] = override_url
    # Probe sub-dict — copy template's default_ports verbatim so the
    # chip starts in multi-port mode when the template carries multiple
    # entries. Operator can edit per-chip ports via Admin → Hosts.
    default_ports = list(template.get("default_ports") or [])
    probe: dict[str, Any] = {"enabled": probe_enabled, "type": probe_type}
    if default_ports:
        probe["ports"] = default_ports
    new_chip["probe"] = probe
    # Append to the target host's services[] (preserve every other
    # chip already pinned).
    existing_services = hosts[target_idx].get("services") or []
    if not isinstance(existing_services, list):
        existing_services = []
    new_idx = len(existing_services)
    existing_services.append(new_chip)
    hosts[target_idx]["services"] = existing_services
    # Persist via the SAME save path the Admin → Hosts editor uses —
    # this runs every chip through `_clean_host_services` so the
    # validation contract stays uniform.
    set_setting(Settings.HOSTS_CONFIG, json.dumps(hosts))
    return {
        "ok": True,
        "host_id": host_id,
        "service_idx": new_idx,
        "chip": new_chip,
        "catalog": template,
    }


@app.get("/api/apps")
async def api_apps_list(_admin: AdminUser):
    """Admin-only: cross-host aggregate view. Returns one row per
    distinct app (grouped by catalog_id or name) with every host that
    runs an instance + per-instance status."""
    from logic import service_catalog as _sc
    return {"apps": _sc.list_apps()}


@app.get("/api/apps/instances")
async def api_apps_instances(_admin: AdminUser):
    """Admin-only: flat per-instance iterator — every chip across every
    host. Used by the Admin → Apps tab's instance list."""
    from logic import service_catalog as _sc
    return {"instances": list(_sc.iter_instances())}


# noinspection PyProtectedMember
@app.post("/api/services/{host_id}/{service_idx}/probe")
async def api_service_probe_now(host_id: str, service_idx: int, _admin: AdminUser):
    """Admin-only: run a one-shot probe against a specific chip and
    persist the result to ``service_samples`` so the SPA picks it up
    on the next refresh. Returns the probe outcome inline so the SPA
    can render the result without waiting for the next API poll.

    Routes through the existing TCP / HTTP probe helpers in
    ``logic.service_sampler`` so the manual path uses the same code
    as the lifespan sampler — one probe verb, one persistence shape,
    one history pipeline.
    """
    import time as _time
    from urllib.parse import urlparse as _urlparse
    from logic import service_sampler as _ss
    # Resolve the chip from hosts_config.
    hosts = _load_hosts_config()
    target_host = None
    for row in hosts:
        if (row.get("id") or "").strip() == host_id:
            target_host = row
            break
    if target_host is None:
        raise HTTPException(404, f"host not found: {host_id}")
    chips = target_host.get("services") or []
    if not isinstance(chips, list) or service_idx < 0 or service_idx >= len(chips):
        raise HTTPException(404, f"service_idx {service_idx} out of range for host {host_id}")
    chip = chips[service_idx]
    if not isinstance(chip, dict):
        raise HTTPException(400, "service entry malformed")
    probe_cfg = chip.get("probe") or {}
    if not probe_cfg.get("enabled"):
        raise HTTPException(400, "probe is not enabled for this chip")
    probe_type = (probe_cfg.get("type") or "tcp").strip().lower()
    if probe_type not in ("tcp", "http"):
        probe_type = "tcp"
    url = (chip.get("url") or "").strip()
    parsed_host = ""
    parsed_port = None
    if url:
        try:
            pu = _urlparse(url if "://" in url else "tcp://" + url)
            parsed_host = (pu.hostname or "").strip()
            parsed_port = pu.port
        except (ValueError, AttributeError):
            pass
    # `_coerce_int_local` (imported at module top from service_catalog)
    # narrows the Any-typed `probe.port` cell before the min/max
    # comparison so static analysis doesn't flag Any|None → int.
    override_port_int = _coerce_int_local(probe_cfg.get("port"))
    port = override_port_int if (override_port_int and override_port_int > 0) else parsed_port
    if not parsed_host:
        raise HTTPException(400, "unable to resolve probe target host from chip url")
    if probe_type == "tcp" and not port:
        lc = url.lower()
        if lc.startswith("https://"):
            port = 443
        elif lc.startswith("http://"):
            port = 80
        else:
            raise HTTPException(400, "no probe port resolvable; set chip url or probe.port")
    expected_status = _coerce_int_local(probe_cfg.get("expected_status")) or 0
    timeout_s = float(tuning.tuning_int(Tunable.SERVICE_PROBE_TIMEOUT_SECONDS))
    # Multi-port chip — fan out per-port probe + roll up. Same shape as
    # the sampler's _probe_target so the manual + scheduled paths
    # converge on identical persistence output. When `probe.ports[]`
    # is set, the legacy single-port `probe.port` is ignored.
    ports_raw = probe_cfg.get("ports")
    sub_ports: list[dict] = []
    if isinstance(ports_raw, list):
        for p in ports_raw:
            if not isinstance(p, dict):
                continue
            pi = _coerce_int_local(p.get("port"))
            if pi is None or not (1 <= pi <= 65535):
                continue
            sub_proto = (p.get("protocol") or "tcp")
            sub_proto = sub_proto.strip().lower() if isinstance(sub_proto, str) else "tcp"
            sub_path = (p.get("probe_path") or "").strip() or "/"
            sub_label = (p.get("label") or "").strip()
            sub_status = _coerce_int_local(p.get("probe_status")) or 0
            sub_type = ("http" if sub_proto in ("http", "https") or sub_path != "/" else "tcp")
            sub_ports.append({"port": pi, "protocol": sub_proto, "label": sub_label,
                              "probe_path": sub_path, "probe_status": sub_status,
                              "probe_type": sub_type})
    ts = int(_time.time())
    port_results: list[dict] = []
    if sub_ports:
        any_alive = False
        min_rtt: Optional[int] = None
        first_error = None
        for sp in sub_ports:
            # `sp_port` / `sp_status` narrow from dict-Any access to
            # concrete ints so the downstream `probe_tcp` / persistence
            # signatures don't flag Any|None on every call.
            sp_port = _coerce_int_local(sp.get("port")) or 0
            sp_status = _coerce_int_local(sp.get("probe_status")) or 0
            if sp["probe_type"] == "http":
                scheme = "https" if sp["protocol"] == "https" else "http"
                sub_url = f"{scheme}://{parsed_host}:{sp_port}{sp['probe_path']}"
                r = await _ss.probe_http(sub_url, sp_status, timeout_s)
            else:
                r = await _ss.probe_tcp(parsed_host, sp_port, timeout_s)
            r_rtt = _coerce_int_local(r.get("rtt_ms"))
            pr = {"port": sp_port, "label": sp["label"],
                  "alive": bool(r.get("alive")), "rtt_ms": r_rtt,
                  "error": r.get("error")}
            port_results.append(pr)
            # Per-port row persistence.
            _ss.persist_row(host_id, service_idx,
                            bool(r.get("alive")), r_rtt,
                            r.get("error"), ts, port=sp_port)
            if r.get("alive"):
                any_alive = True
                if r_rtt is not None and (min_rtt is None or r_rtt < min_rtt):
                    min_rtt = r_rtt
            elif first_error is None:
                first_error = r.get("error")
        result = {"alive": any_alive, "rtt_ms": min_rtt,
                  "error": None if any_alive else (first_error or "all ports down")}
    elif probe_type == "http" and url:
        result = await _ss.probe_http(url, expected_status, timeout_s)
    else:
        result = await _ss.probe_tcp(parsed_host, int(port or 0), timeout_s)
    # Rollup row (port=0) — always written so the chip-level status
    # updates regardless of single-port vs multi-port shape. The
    # explicit `port=0` matches the sentinel-rollup contract; spelling
    # it out at every call site beats relying on the default.
    # Narrow `rtt_ms` + `error` from the result dict's Any-typed cells
    # to concrete Optional[int] / Optional[str] so persist_row's
    # parameter types match without an Any|None fallthrough.
    result_rtt = _coerce_int_local(result.get("rtt_ms"))
    result_error_raw = result.get("error")
    result_error = result_error_raw if isinstance(result_error_raw, str) else None
    # noinspection PyArgumentEqualDefault
    _ss.persist_row(
        host_id, service_idx,
        bool(result.get("alive")),
        result_rtt,
        result_error,
        ts,
        port=0,
    )
    return {
        "ok": True,
        "host_id": host_id,
        "service_idx": service_idx,
        "ts": ts,
        "alive": bool(result.get("alive")),
        "rtt_ms": result.get("rtt_ms"),
        "error": result.get("error"),
        # Per-port detail — empty list for single-port chips.
        "port_results": port_results,
    }


@app.get("/api/services/{host_id}/{service_idx}/history")
async def api_service_history(host_id: str, service_idx: int, hours: int = 24,
                              port: Optional[int] = None,
                              _admin: AdminUser = None):  # type: ignore[assignment]
    """Admin-only: per-(host, service_idx) probe history for the host
    drawer's Apps sub-tab. Returns up to N hours of samples ordered
    oldest-first so a sparkline can render directly.

    ``port`` query param filters the returned rows:
      - omitted (None): returns the ROLLUP series (chip-level any-port-up
        history) — equivalent to ``port=0``.
      - 0: explicit rollup-only.
      - >0: per-port history for that specific port number.
      - -1: return every row (rollup + every per-port) — useful for the
        Apps drawer's expanded multi-port chart.
    """
    import time as _time
    hours_clamped = max(1, min(int(hours or 24), 24 * 30))
    cutoff = int(_time.time() - hours_clamped * 3600)
    # Resolve port filter — None defaults to rollup (port=0).
    if port is None:
        port_filter: Optional[int] = 0
    elif port == -1:
        port_filter = None  # No filter — every row
    else:
        port_filter = int(port)
    base_sql = ("SELECT ts, alive, rtt_ms, error, port "
                "FROM service_samples "
                "WHERE host_id = ? AND service_idx = ? AND ts >= ?")
    params: list[Any] = [host_id, service_idx, cutoff]
    if port_filter is not None:
        base_sql += " AND port = ?"
        params.append(port_filter)
    base_sql += " ORDER BY ts ASC, port ASC"
    try:
        with db_conn() as c:
            rows = c.execute(base_sql, tuple(params)).fetchall()
    except sqlite3.OperationalError as oe:
        # Pre-migration-005 schemas don't have the `port` column yet;
        # fall back to a SELECT without it so the endpoint stays useful
        # on a database that hasn't run the additive ALTER.
        if "no such column" in str(oe).lower():
            with db_conn() as c:
                rows = c.execute(
                    "SELECT ts, alive, rtt_ms, error, 0 AS port "
                    "FROM service_samples "
                    "WHERE host_id = ? AND service_idx = ? AND ts >= ? "
                    "ORDER BY ts ASC",
                    (host_id, service_idx, cutoff),
                ).fetchall()
        else:
            raise
    return {
        "samples": [
            {
                "ts": int(r[0]),
                "alive": bool(r[1]),
                "rtt_ms": r[2],
                "error": r[3],
                "port": r[4],
            }
            for r in rows
        ],
        "hours": hours_clamped,
        "port_filter": port_filter,  # None = every row; 0 = rollup; >0 = that port
    }


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/hosts")
async def api_hosts(force: bool = False):
    """Hosts view — returns the CURATED host list merged with live
    stats from every enabled provider.

    Source of truth is ``hosts_config`` (Settings → Hosts). If it's
    empty, falls back to auto-discovering from the Beszel / Pulse
    batch maps so the view isn't blank for fresh installs.

    NOTE — refactored from the original ~975-line inline
    duplication of the Beszel / Pulse / NE / Webmin probe logic to
    compose the protected helper chain (``_get_host_provider_state`` +
    ``_merge_one_host``) per row. Bearer-token scrapers (Homarr widget,
    Grafana, custom dashboards) hitting THIS endpoint now share the
    SPA's single-flight lock on hub probes AND the per-host
    Webmin success-cache + 5s fail-cache, so a burst of /api/hosts
    calls can no longer recreate the 504-storm pattern. Response shape
    is byte-for-byte identical to the pre-refactor inline version
    (same ``_shape_host_api_row`` per row, same top-level keys,
    same ``provider_errors`` aggregation).

    Each curated host entry specifies its per-provider name:
      - ``ne_url``      — node-exporter scrape URL
      - ``beszel_name`` — Beszel ``host`` field to match
      - ``pulse_name``  — Pulse PVE node name
    For each enabled provider, we fetch once (Beszel + Pulse via the
    cached batch maps; NE + Webmin per-host inside ``_merge_one_host``)
    and merge with the best-of rule — non-zero values win over zeros —
    so flaky providers never erase good data.
    """
    # ---- Provider state (single-flight, cached) -------------------
    # `_get_host_provider_state` does the Beszel + Pulse batch probes
    # once, gated by the lock + cache. On a cache hit it's a dict
    # lookup; on a miss exactly ONE caller pays the probe cost while
    # the rest queue. `force=True` bypasses the TTL but still goes
    # through the lock.
    state = await _get_host_provider_state(force=force)
    active = state["active"]
    beszel_map = state["beszel_map"]
    pulse_map = state["pulse_map"]
    errors: dict[str, str] = dict(state["errors"])

    curated = _load_hosts_config()

    # ---- Fallback: auto-discover from Beszel / Pulse when no curated list ----
    # Same shape the inline path emitted; uses the cached batch maps
    # rather than re-probing.
    if not curated:
        if beszel_map:
            curated = [
                {
                    "id": k,
                    "label": (v or {}).get("beszel_name") or k,
                    "ne_url": "",
                    "beszel_name": k,
                    "pulse_name": "",
                    "enabled": True,
                }
                for k, v in sorted(beszel_map.items(), key=lambda kv: kv[0].lower())
            ]
        elif pulse_map:
            curated = [
                {
                    "id": k,
                    "label": (v or {}).get("pulse_name") or k,
                    "ne_url": "",
                    "beszel_name": "",
                    "pulse_name": k,
                    "enabled": True,
                }
                for k, v in sorted(pulse_map.items(), key=lambda kv: kv[0].lower())
            ]

    # ---- Per-host merge via the protected helper chain ------------
    # Each enabled curated host gets its OWN `_merge_one_host` call.
    # NE + Webmin probes happen per-host inside the helper (Webmin
    # behind the per-host success-cache + 5s fail-cache). Beszel /
    # Pulse hits are dict lookups against the cached batch maps the
    # outer state carries. Run the per-host merges in parallel — same
    # behaviour as the previous inline path's `asyncio.gather` over
    # NE + Webmin probes, just composed via the helper.
    enabled_hosts = [h for h in curated if h.get("enabled", True)]
    if enabled_hosts:
        merge_results = await asyncio.gather(*(
            _merge_one_host(h, state, force=force) for h in enabled_hosts
        ))
    else:
        merge_results = []

    out: list[dict] = []
    for h, (merged, providers_hit) in zip(enabled_hosts, merge_results):
        # If a Webmin probe surfaced an error string in the merged
        # dict (the helper stamps `exporter_error` on full-failure),
        # aggregate it into the top-level provider_errors map so
        # bearer-token clients keep getting the same coarse signal
        # the inline path emitted. First-error-per-provider wins,
        # mirroring the legacy behaviour.
        wm_err = (merged or {}).get("exporter_error")
        if wm_err and "webmin" not in errors and "webmin" in active:
            # Match the legacy "<host_id>: <message>" prefix so
            # downstream dashboards' regex parsers don't break.
            errors["webmin"] = f"{h.get('id')}: {wm_err}"
        out.append({
            "_host_record": h,
            "_merged": merged,
            "_providers": providers_hit,
        })

    # ---- Shape the response ---------------------------------------
    # Snapshot fallback — apply ONCE for every entry whose probes
    # left holes. Loads snapshots in a single DB read, then mutates each
    # entry's merged dict in place, stamping `_stale_fields` /
    # `_stale_ts` on whichever entries had missing fields filled from
    # the snapshot. Same call shape `_merge_one_host` uses for the
    # /api/hosts/one path so both endpoints honour the fallback
    # uniformly.
    try:
        from logic.gather import (
            apply_host_snapshot_fallback as _fallback,
            load_host_snapshots as _load_snaps,
        )
        snaps = _load_snaps()
        if snaps:
            _fallback(
                {entry["_host_record"]["id"]: entry["_merged"] for entry in out},
                snapshots=snaps,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] snapshot fallback failed: {e}")
    # Short debug spew for core/arch/kernel only — helps diagnose the
    # common "all three columns are empty" complaint by showing each
    # curated host's merged values + which providers contributed.
    hosts = []
    # `docker_node` gating moved to the module-level `_is_swarm_node`
    # helper so `/api/hosts/list` + `/api/hosts/one/{id}` (via
    # `_shape_host_api_row`) and this endpoint share one
    # implementation. No inline set rebuild needed here anymore.
    for entry in out:
        h = entry["_host_record"]
        s = entry["_merged"]
        mounts = s.get("mounts") or []
        nics = s.get("network_ifaces") or []
        print(
            f"[hosts] merged id={h.get('id')!r} "
            f"providers={entry['_providers']} "
            f"cores={s.get('host_cores')!r} "
            f"arch={s.get('host_arch')!r} "
            f"kernel={(s.get('host_kernel') or '')[:40]!r} "
            f"platform={s.get('host_platform')!r} "
            f"os={s.get('host_os')!r} "
            f"mounts={len(mounts)} ({[m.get('n') or m.get('name') for m in mounts]}) "
            f"nics={len(nics)}"
        )
        # Share `_shape_host_api_row` with the new endpoints.
        # Pre-fix the legacy `/api/hosts` built its own inline dict that
        # (a) omitted the `_failure_state_for_host()` spread (sampling_paused
        # + last_failure_ts + consecutive_failures + last_error never
        # reached bearer-token clients), and (b) used a 3-tier status
        # taxonomy that diverged from the canonical six-tier one in
        # `_shape_host_api_row` (paused→down normalisation, `unconfigured`
        # for "no provider mapped", `unknown` only for "providers mapped
        # but no answer"). Scrapers saw false `unknown` → false `down`
        # alerts in Grafana / Apprise. Calling the helper here keeps the
        # legacy endpoint a strict superset of the new ones for any
        # bearer-token client still using it (Homarr widget, scrapers,
        # external automation). Note the helper's `any_provider_enabled`
        # arg — pass True since this endpoint only fires when at least
        # one provider IS active (the early-return at the top of
        # `api_hosts` short-circuits the no-provider case).
        hosts.append(
            _shape_host_api_row(
                h, s, entry["_providers"],
                active=active,
            )
        )

    # Aggregate error — non-fatal; UI shows the first one per provider.
    agg_error = "; ".join(f"{k}: {v}" for k, v in errors.items()) or None

    return {
        "configured": bool(active),
        "active": sorted(active),
        "error": agg_error,
        "provider_errors": errors,
        "hub_url": get_setting(Settings.BESZEL_HUB_URL) or "",
        "hosts": hosts,
        # Counts that let the frontend pick the right empty-state
        # copy — "no curated hosts yet" vs "all curated hosts are
        # disabled" vs "curated hosts exist but no provider matched
        # any of them". Without these the view used to blanket-say
        # "no hosts yet" even when the operator had rows configured.
        "curated_count": len(curated),
        "enabled_count": sum(1 for h in curated if h.get("enabled", True)),
    }


# ---------------------------------------------------------------------------
# Per-host async loading
#
# The monolithic /api/hosts waits until every provider probe for every
# host has returned. With Webmin / Pulse / slow node-exporter scrapes
# this can take 10+ seconds even with the existing parallelisation —
# long enough that the page feels frozen.
#
# The split model:
# GET /api/hosts/list         — skeleton: curated list + global
#                               state (active sources, provider
#                               errors, hub URL). No per-host
#                               probes. Fast (<200ms).
# GET /api/hosts/one/{id}     — single host's merged data. Runs
#                               NE + Webmin probes for THAT host
#                               only; reuses Beszel / Pulse batch
#                               maps from a short-lived cache so a
#                               burst of N parallel calls doesn't
#                               incur N × batch-probe cost.
#
# Legacy /api/hosts still works (metric scrapers / dashboards that
# want one round-trip to see the whole fleet). The SPA calls the
# split pair.
# ---------------------------------------------------------------------------
# cache TTL is now operator-tunable via
# `tuning_host_provider_cache_ttl_seconds`. Default preserved at 10s.
# Resolved at every consumer site (NOT cached at module import) per
# the strict-rule contract.
_host_provider_cache: dict = {"ts": 0.0, "state": None}
# Single-flight guard for ``_get_host_provider_state``. Without
# this, a parallel SPA fan-out of 6 ``/api/hosts/one/<id>`` calls on a
# cold cache fires 6 independent Beszel hub + Pulse probes (each 15-20s),
# saturating the event loop AND the upstream NPM connection pool —
# manifests as 504s on unrelated static-asset requests because they
# queue behind the in-flight probe traffic. With the lock, the first
# caller does the probe; the rest await its result and the cache fills
# from a SINGLE round-trip per provider. Same pattern applies under
# `force=true` (settings-save → forced refresh): the lock prevents 6
# parallel forced calls from each re-running the probe.
_host_provider_lock = asyncio.Lock()

# Per-host Webmin result cache. Webmin probes are the slowest link in
# the /api/hosts/one/{id} path (up to 20s each on slow Miniserv); a
# 30s TTL means repeated drawer opens / refresh ticks within half a
# minute skip the probe entirely and reuse the last known-good stats.
# Cache key is the host_id (one Webmin per host — unlike Beszel/Pulse
# which are multi-tenant). Value is the raw dict returned by
# probe_webmin so _merge_one_host can fold it the same way.
# both Webmin cache TTLs are now operator-tunable via
# `tuning_webmin_host_cache_ttl_seconds` (default 30s, success cache)
# and `tuning_webmin_host_fail_cache_ttl_seconds` (default 5s, negative
# cache). Resolved per consumer-site read.
_webmin_host_cache: dict[str, tuple[float, dict]] = {}
_webmin_host_fail_cache: dict[str, tuple[float, dict]] = {}

# Per-host SNMP result caches — same pattern as the Webmin caches.
# Success cache for 30s, fail cache for 5s. SNMP probes are bounded by
# UDP timeout (default 5s × ~13 OID walks fanned in parallel ≈ 5-8s
# wall-clock on a healthy host) so caching the result for the burst
# fan-out is the same win Webmin gets. Per-host id keying matches the
# Webmin cache; SNMP is per-host, no central hub.
_snmp_host_cache: dict[tuple[Any, frozenset[str] | None], tuple[float, dict]] = {}
_snmp_host_fail_cache: dict[tuple[Any, frozenset[str] | None], tuple[float, dict]] = {}

# Per-host HTTP probe result cache — same pattern as the Webmin / SNMP
# pairs but single-dict with a `had_data` flag in the tuple driving
# which TTL to apply (success vs failure). The cached value is the
# subset of `host_http_*` fields the helper stamped, so a hit reuses
# them without re-running the SELECT. TTLs operator-tunable via
# `tuning_http_probe_host_cache_ttl_seconds` (default 30s) +
# `tuning_http_probe_host_fail_cache_ttl_seconds` (default 5s).
_http_probe_host_cache: dict[str, tuple[float, dict, bool]] = {}


def invalidate_host_provider_cache() -> None:
    """Drop the cached provider state + per-host Webmin results.

    Called from every settings-write path that would change provider
    behaviour: host_stats_source / beszel_* / pulse_* / webmin_* /
    hosts_config. Without this, the SPA's "Save → reload Hosts tab"
    flow keeps showing stale auth_failed states for up to
    ``_HOST_PROVIDER_CACHE_TTL`` seconds (10s) — and stale Webmin
    probe results for up to ``_WEBMIN_HOST_CACHE_TTL`` (30s) — because
    /api/hosts/one/{id} reuses the cached error map. Mirrors the
    invalidation pattern already in place for Portainer / auth /
    OIDC discovery caches.
    """
    _host_provider_cache["ts"] = 0.0
    _host_provider_cache["state"] = None
    _webmin_host_cache.clear()
    _webmin_host_fail_cache.clear()
    # SNMP shares the per-host success / failure cache pattern with
    # Webmin. Bust on every settings-save touching SNMP creds /
    # aliases so the next probe picks up the new community / version /
    # port without waiting out the 30s TTL.
    _snmp_host_cache.clear()
    _snmp_host_fail_cache.clear()
    # HTTP probe per-host cache — same invalidation contract.
    _http_probe_host_cache.clear()


def _compute_host_provider_cache_key() -> tuple[set[str], tuple]:
    """Return (active_sources, cache_key) — the active providers as a
    set + the cache-bust key (sorted-active-tuple + cred-blob-hash).
    Module-level so both ``_get_host_provider_state`` and the cheap
    ``_peek_cached_host_provider_state`` helper share one definition;
    a divergence between the two would mean the peek helper says
    "cache warm" while the get helper recomputes a different key and
    refires the probe. Re-callable so the post-lock path can refresh
    the key after a settings save during the lock-wait without
    risking the queued caller using a pre-save snapshot.
    """
    active_set = active_host_stats_providers()
    # Cache key includes the active-sources tuple so a settings
    # change like flipping `host_stats_source` from "beszel" to
    # "beszel,pulse" auto-busts the cache. Save paths also call
    # `invalidate_host_provider_cache()` directly for instant
    # feedback; the key match is defence-in-depth.
    # Credential-blob hash folded into the key so changing
    # `beszel_password` (without flipping `host_stats_source`)
    # busts the cache too.
    cred_blob = "|".join((
        get_setting(Settings.BESZEL_HUB_URL) or "",
        get_setting(Settings.BESZEL_IDENTITY) or "",
        get_setting(Settings.BESZEL_PASSWORD) or "",
        get_setting(Settings.BESZEL_VERIFY_TLS, "true") or "true",
        get_setting(Settings.PULSE_URL) or "",
        get_setting(Settings.PULSE_TOKEN) or "",
        get_setting(Settings.PULSE_VERIFY_TLS, "true") or "true",
        get_setting(Settings.WEBMIN_URL) or "",
        get_setting(Settings.WEBMIN_USER) or "",
        get_setting(Settings.WEBMIN_PASSWORD) or "",
        get_setting(Settings.WEBMIN_VERIFY_TLS, "true") or "true",
        get_setting(Settings.NODE_EXPORTER_URL_TEMPLATE) or "",
        get_setting(Settings.NODE_EXPORTER_OVERRIDES) or "",
        # SNMP — every credential / default that affects
        # what the probe sees. v3 keys are the security-sensitive
        # ones; the community + port + version + aliases also
        # belong here so a global default change auto-busts the
        # cache without waiting on the explicit invalidate path.
        get_setting(Settings.SNMP_DEFAULT_COMMUNITY) or "",
        get_setting(Settings.SNMP_DEFAULT_VERSION) or "",
        # Default port migrated to TUNABLES — read via tuning_int so a
        # change to `tuning_snmp_default_port` busts this cache too.
        str(tuning.tuning_int(Tunable.SNMP_DEFAULT_PORT)),
        get_setting(Settings.SNMP_V3_USER) or "",
        get_setting(Settings.SNMP_V3_AUTH_KEY) or "",
        get_setting(Settings.SNMP_V3_PRIV_KEY) or "",
        get_setting(Settings.SNMP_ALIASES) or "",
    ))
    cred_hash = hashlib.sha256(cred_blob.encode()).hexdigest()[:16]
    return active_set, (tuple(sorted(active_set)), cred_hash)


def _peek_cached_host_provider_state() -> dict | None:
    """Return the cached host provider state IF warm — else None.

    Cheap, never blocks, never fires a probe. ``api_hosts_list`` uses
    this to decide whether to await ``_get_host_provider_state`` (warm
    case — instant) or serve snapshot rows immediately and kick the
    probe in the background (cold case — Fix A from the cold-load
    analysis). Cache is "warm" iff (a) state object exists, (b) TTL
    not expired, AND (c) the stored cache key still matches the
    current active-providers + cred-hash signature (a settings save
    invalidates the key even before the explicit
    ``invalidate_host_provider_cache`` call lands, so we can't trust a
    stale-key cache to mirror current settings).
    """
    cached = _host_provider_cache.get("state")
    cached_key = _host_provider_cache.get("key")
    if not cached or not cached_key:
        return None
    cache_ttl = tuning.tuning_int(Tunable.HOST_PROVIDER_CACHE_TTL_SECONDS)
    if (time.time() - _host_provider_cache.get("ts", 0.0)) >= cache_ttl:
        return None
    _, current_key = _compute_host_provider_cache_key()
    if cached_key != current_key:
        return None
    return cached


# Single-flight guard for background gather kicks. ``_kick_background_gather``
# fires ``_gather`` as a fire-and-forget task to refresh the items / stacks /
# nodes cache without blocking the response. Without the guard a poll burst
# (auto-refresh every 30s × N tabs open) would fire N concurrent gathers,
# each fanning out to Portainer with the same payload. The guard tracks the
# current task and ignores subsequent kicks while it's still running.
_background_gather_task: "asyncio.Task | None" = None
# Mirror single-flight guard for ``_gather_stats``. Same rationale:
# without it a burst of /api/stats calls would each fire a parallel
# stats gather while the previous one is still running, multiplying
# Portainer + container fan-out cost. The guard tracks the current
# task and ignores subsequent kicks while it's in flight.
_background_stats_task: "asyncio.Task | None" = None


def _kick_background_gather() -> "asyncio.Task | None":
    """Schedule ``_gather`` as a background task if none is running.

    Returns the in-flight task (newly-scheduled OR already-running) so
    a cold-cache caller that genuinely needs fresh data can ``await``
    the same task instead of issuing a parallel gather. Returns
    ``None`` when scheduling failed (no event loop). Callers that
    only need the boolean "is something running?" check
    ``result is not None``.

    Single-flight: if a prior task is still pending the existing task
    is returned unchanged — never spawns two concurrent gathers.
    """
    global _background_gather_task
    try:
        if _background_gather_task is not None and not _background_gather_task.done():
            return _background_gather_task
        loop = asyncio.get_running_loop()
        _background_gather_task = loop.create_task(_gather())
        return _background_gather_task
    except RuntimeError:
        # No running event loop (called from a sync context that isn't
        # inside a request handler) — caller can fall back to awaiting
        # ``_gather()`` directly if they really need fresh data.
        return None


def _kick_background_stats_gather() -> bool:
    """Same single-flight pattern as ``_kick_background_gather`` but
    for the stats cache. Used by ``/api/stats`` to serve the warm
    cache instantly + refresh in background. Returns True when a task
    is running (just-scheduled OR already in flight); False on no-loop.

    The seed-from-DB path stamps ``_stats_cache["ts"] = 0.0`` so the
    legacy TTL check at the top of ``api_stats`` would always fall
    through to a synchronous ``_gather_stats()`` and block the response
    on a fresh page load — even though cached values were already
    available to serve. Routing through this kick instead serves the
    seeded cache first, then refreshes in the background; the next
    poll cycle picks up the live values.
    """
    global _background_stats_task
    try:
        if _background_stats_task is not None and not _background_stats_task.done():
            return True
        loop = asyncio.get_running_loop()
        _background_stats_task = loop.create_task(_gather_stats())
        return True
    except RuntimeError:
        return False


# noinspection PyTypeChecker,PyUnresolvedReferences
async def _get_host_provider_state(force: bool = False) -> dict:
    """Fetch + cache the provider state needed to merge any host.

    The "batch" providers (Beszel, Pulse) expose one endpoint that
    returns every host in one call, so we memoise them for
    ``_HOST_PROVIDER_CACHE_TTL`` seconds. A burst of /api/hosts/one/{id}
    calls from the SPA hits the cache; settings changes auto-clear
    after the TTL expires (no explicit invalidation needed).
    """
    now = time.time()
    active, cache_key = _compute_host_provider_cache_key()
    # cache TTL is operator-tunable; resolve once at the top of
    # the function and reuse for both the pre-lock and post-lock checks
    # (within the same call, the value can't legitimately change).
    cache_ttl = tuning.tuning_int(Tunable.HOST_PROVIDER_CACHE_TTL_SECONDS)
    cached = _host_provider_cache.get("state")
    cached_key = _host_provider_cache.get("key")
    if (
        not force and cached and cached_key == cache_key
        and (now - _host_provider_cache.get("ts", 0.0)) < cache_ttl
    ):
        return cached

    # Single-flight — only ONE concurrent caller does the cold-
    # cache probe; the rest await on the lock and pick up the populated
    # cache via the post-lock re-check below. Pre-fix N parallel
    # /api/hosts/one/<id> calls fired N independent Beszel hub + Pulse
    # probes, saturating the event loop. Force=true requests still
    # serialise here so a SPA settings-save fan-out doesn't 6× the
    # upstream load either.
    # measure the wait so operators can see whether contention
    # is the cause of elevated /api/hosts/one latency vs slow upstreams.
    # First caller bucket-counts in sub-ms (zero wait); subsequent
    # callers in the same fan-out bucket-count in seconds.
    _lock_wait_start = time.monotonic()
    async with _host_provider_lock:
        metrics.HOST_PROVIDER_LOCK_WAIT.observe(time.monotonic() - _lock_wait_start)
        # fix — RE-COMPUTE active + cache_key inside the
        # lock. A settings save during the lock-wait could have changed
        # `host_stats_source` or any credential, so the pre-lock values
        # are stale. Without this re-compute, a queued caller would run
        # a probe under a snapshot that no longer matches the current
        # settings (e.g. probing Beszel after the operator turned it
        # off). Generalisable rule: when single-flighting via lock-then-
        # recheck, re-COMPUTE the cache key inside the lock, don't just
        # re-read the cache.
        active, cache_key = _compute_host_provider_cache_key()
        # Re-check inside the lock: another caller may have populated
        # the cache while we were waiting. ``force`` requests always
        # re-probe but only the FIRST forced caller pays the cost —
        # subsequent forced callers within the same lock-acquire window
        # see a fresh cache (now < TTL) and reuse it.
        now2 = time.time()
        cached2 = _host_provider_cache.get("state")
        cached_key2 = _host_provider_cache.get("key")
        if (
            cached2 and cached_key2 == cache_key
            and (now2 - _host_provider_cache.get("ts", 0.0)) < cache_ttl
        ):
            return cached2

        return await _do_host_provider_probe(active, cache_key)


async def _do_host_provider_probe(active: set[str], cache_key: tuple) -> dict:
    """Inner — runs the Beszel + Pulse probes and writes the result
    cache. Always called under ``_host_provider_lock``. Split from
    the outer function so the lock-acquire path stays narrow.
    """
    from logic import beszel as _beszel
    from logic import pulse as _pulse

    errors: dict[str, str] = {}

    # Beszel + Pulse hub probes run in PARALLEL. Prior sequential
    # version made the cold-cache cost Beszel + Pulse = up to 30s alone,
    # exhausting the 30s `/api/hosts/one/<id>` budget before NE + Webmin
    # even started. With `asyncio.gather`, cold-cache cost drops to
    # max(B, P) ≈ 15s — leaving ~15s for the per-host slice. Both are
    # independent probes hitting different hubs; no shared state, safe
    # to fan out. Each builds its own (config-fetch + probe) coroutine
    # so missing credentials short-circuit cleanly.
    async def _probe_beszel() -> tuple[dict, str | None]:
        if "beszel" not in active:
            return {}, None
        hub_url = get_setting(Settings.BESZEL_HUB_URL) or ""
        ident = get_setting(Settings.BESZEL_IDENTITY) or ""
        passw = get_setting(Settings.BESZEL_PASSWORD) or ""
        verify = (get_setting(Settings.BESZEL_VERIFY_TLS, "true") or "true").lower() == "true"
        if not (hub_url and ident and passw):
            return {}, "missing url / identity / password"
        r = await _beszel.probe_hub(hub_url, ident, passw, verify_tls=verify)
        return r.get("systems") or {}, r.get("error")

    async def _probe_pulse() -> tuple[dict, str | None]:
        if "pulse" not in active:
            return {}, None
        pulse_url = get_setting(Settings.PULSE_URL) or ""
        pulse_token = get_setting(Settings.PULSE_TOKEN) or ""
        verify = (get_setting(Settings.PULSE_VERIFY_TLS, "true") or "true").lower() == "true"
        if not (pulse_url and pulse_token):
            return {}, "missing url / token"
        r = await _pulse.probe_pulse(pulse_url, pulse_token, verify_tls=verify)
        return r.get("hosts") or {}, r.get("error")

    (beszel_map, beszel_err), (pulse_map, pulse_err) = await asyncio.gather(
        _probe_beszel(), _probe_pulse(),
    )
    if beszel_err:
        errors["beszel"] = beszel_err
    if pulse_err:
        errors["pulse"] = pulse_err

    webmin_creds_ok = False
    webmin_user = ""
    webmin_password = ""
    webmin_verify = False
    webmin_aliases: dict[str, str] = {}
    if "webmin" in active:
        webmin_user = get_setting(Settings.WEBMIN_USER) or ""
        webmin_password = get_setting(Settings.WEBMIN_PASSWORD) or ""
        webmin_verify = (get_setting(Settings.WEBMIN_VERIFY_TLS, "false") or "false").lower() == "true"
        try:
            wm_aliases_raw = json.loads(get_setting(Settings.WEBMIN_ALIASES, "{}") or "{}")
            if isinstance(wm_aliases_raw, dict):
                webmin_aliases = {
                    str(k).strip(): str(v).strip()
                    for k, v in wm_aliases_raw.items()
                    if str(k).strip() and str(v).strip()
                }
        except ValueError:
            webmin_aliases = {}
        if webmin_user and webmin_password:
            webmin_creds_ok = True
        else:
            errors["webmin"] = "missing user / password"

    # SNMP — settings-derived defaults flow through state so
    # `_merge_one_host` doesn't re-read them per host. v3 keys are
    # secrets but stay in the in-process state dict (not the wire); the
    # admin-only `/api/snmp/test` endpoint is the only path that lets
    # operators surface them and even there they're write-only via
    # `_set` flags. Per-host overrides on `hosts_config[].snmp` are
    # consulted INSIDE _merge_one_host so a row's own community wins.
    snmp_default_community = ""
    snmp_default_version = "v2c"
    snmp_default_port = 161
    snmp_v3_user = ""
    snmp_v3_auth_key = ""
    snmp_v3_priv_key = ""
    snmp_aliases: dict[str, str] = {}
    if "snmp" in active:
        snmp_default_community = get_setting(Settings.SNMP_DEFAULT_COMMUNITY) or "public"
        snmp_default_version = (
                                   get_setting(Settings.SNMP_DEFAULT_VERSION) or "v2c"
                               ).strip().lower() or "v2c"
        try:
            snmp_default_port = tuning.tuning_int(Tunable.SNMP_DEFAULT_PORT)
        except (TypeError, ValueError):
            snmp_default_port = 161
        snmp_v3_user = get_setting(Settings.SNMP_V3_USER) or ""
        snmp_v3_auth_key = get_setting(Settings.SNMP_V3_AUTH_KEY) or ""
        snmp_v3_priv_key = get_setting(Settings.SNMP_V3_PRIV_KEY) or ""
        try:
            sn_aliases_raw = json.loads(get_setting(Settings.SNMP_ALIASES, "{}") or "{}")
            if isinstance(sn_aliases_raw, dict):
                snmp_aliases = {
                    str(k).strip(): str(v).strip()
                    for k, v in sn_aliases_raw.items()
                    if str(k).strip() and str(v).strip()
                }
        except ValueError:
            snmp_aliases = {}

    state = {
        "active": active,
        "beszel_map": beszel_map,
        "pulse_map": pulse_map,
        "errors": errors,
        "webmin_user": webmin_user,
        "webmin_password": webmin_password,
        "webmin_verify": webmin_verify,
        "webmin_creds_ok": webmin_creds_ok,
        "webmin_aliases": webmin_aliases,
        # SNMP — defaults + aliases. Per-host overrides land
        # later via `hosts_config[].snmp`.
        "snmp_default_community": snmp_default_community,
        "snmp_default_version": snmp_default_version,
        "snmp_default_port": snmp_default_port,
        "snmp_v3_user": snmp_v3_user,
        "snmp_v3_auth_key": snmp_v3_auth_key,
        "snmp_v3_priv_key": snmp_v3_priv_key,
        "snmp_aliases": snmp_aliases,
    }
    _host_provider_cache["ts"] = time.time()
    _host_provider_cache["state"] = state
    _host_provider_cache["key"] = cache_key
    return state


def _publish_provider_probe_event(host_id: str, provider: str, kind: str,
                                  started_at: float | None = None,
                                  *, client_id: str | None = None,
                                  ok: bool | None = None) -> None:
    """Fire a per-(provider, host) probe-status SSE event.

    ``kind`` is either ``probing`` (slice entered, real fetch about to
    run) or ``done`` (slice complete — success OR failure). The SPA's
    ``host:provider_probing`` / ``host:provider_done`` handlers track
    ``h._polling[provider] = bool`` per row so the chip pulses ONLY
    while ITS probe is in flight (not the row-wide `_loading` window).

    Cache-hit paths skip these events — no actual fetch happened, the
    chip shouldn't pulse for a microsecond dict lookup. Slow probes
    (SNMP walks, Webmin three-tier fallback) are the operators' real
    interest.

    ``client_id`` threads the originating tab's UUID into the event so
    the SPA's `_isSelfEvent` self-filter works on this event the same
    way it does on every other write-handler-published event. Without
    it the originating tab still receives + processes its own events
    (cost is microscopic, but inconsistent with the rest of the
    publish surface). Caller passes `_request_client_id(request)` from
    the request-scoped header.

    Errors are logged + swallowed; a failed publish must never break
    the probe path.
    """
    try:
        payload: dict[str, Any] = {
            "host_id": host_id,
            "provider": provider,
        }
        if kind == "probing":
            payload["started_at"] = started_at if started_at is not None else time.time()
        elif kind == "done":
            payload["finished_at"] = time.time()
            if started_at is not None:
                payload["duration_ms"] = int((time.time() - started_at) * 1000)
            # Outcome hint — lets the SPA's `host:provider_done`
            # handler flip the chip to its known-good (ok=True) /
            # known-failed (ok=False) state from the SSE event itself,
            # without waiting for the next /api/hosts/one/{id} round-
            # trip. Snappier on slow networks. Caller passes ok=True
            # on success branch, ok=False on failure branch, leaves
            # None when the outcome isn't yet decided (e.g. cache-hit
            # paths skip the events entirely so this is rare).
            if ok is not None:
                payload["ok"] = bool(ok)
        _events.publish(f"host:provider_{kind}", payload, client_id=client_id)
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] provider_{kind} publish failed for "
              f"{host_id!r}/{provider!r}: {e}")


def _populate_detected_ports(host_id: str, merged: dict) -> bool:
    """Fill `merged.detected_ports[]` + `merged.last_port_scan_ts` from
    the latest scan in `host_port_scans` for this host. Returns True
    when at least one row was found (caller bumps `providers_hit`).

    Reads UNCONDITIONALLY — port-scan history visibility is independent
    of (a) the master `port_scan_enabled` toggle, (b) the per-host
    `port_scan.enabled` flag, AND (c) the live-provider reachability
    state of the host's other providers (Beszel / NE / Pulse / Webmin
    / SNMP). Operator-flagged: "what was open last time we scanned"
    must remain viewable on hosts where the providers are unreachable
    AND on hosts where port-scanning has been disabled. Disabling the
    toggle should stop NEW scans, not erase the historical view.

    Used by both `_merge_one_host` (the live-provider path) and
    `api_hosts_list` (the snapshot-only skeleton path) so detected
    ports surface uniformly regardless of which endpoint the SPA
    is hitting.
    """
    if not host_id:
        return False
    try:
        with db_conn() as c:
            head = c.execute(
                "SELECT scan_id, MAX(ts) AS ts FROM host_port_scans "
                "WHERE host_id = ? GROUP BY scan_id ORDER BY ts DESC LIMIT 1",
                (host_id,),
            ).fetchone()
            if not head or not head["scan_id"]:
                return False
            rows = c.execute(
                "SELECT port, service_hint, banner_excerpt, protocol "
                "FROM host_port_scans WHERE scan_id = ? "
                "ORDER BY protocol ASC, port ASC",
                (head["scan_id"],),
            ).fetchall()
            merged["detected_ports"] = [{
                "port": int(r["port"]),
                "protocol": (r["protocol"] or "tcp"),
                "service_hint": r["service_hint"] or "",
                "banner_excerpt": r["banner_excerpt"] or "",
                "scanned_at": int(head["ts"] or 0),
            } for r in rows]
            merged["last_port_scan_ts"] = int(head["ts"] or 0)
            return True
    except Exception as e:  # noqa: BLE001
        print(f"[port_scan] read failed for {host_id!r}: {e}")
        return False


# noinspection PyTypeChecker,PyUnresolvedReferences
async def _merge_one_host(h: dict, state: dict, *, force: bool = False,
                          client_id: str | None = None) -> tuple[dict, list[str]]:
    """Merge one curated host with provider data. Runs NE + Webmin
    probes inline for THIS host only; Beszel/Pulse lookups hit the
    cached batch maps. Returns (merged_dict, providers_hit).

    when ``force=True``, drop this host's per-host Webmin
    caches (success + failure) before the probe block so the next
    `probe_webmin` call hits the wire. Pre-fix `?force=true` only
    bypassed the OUTER `_host_provider_cache`; the 30s success cache
    + 5s failure cache still served the previously-cached entry.
    Operators expect "force = re-probe everything for THIS host".
    Settings-save paths already invalidate every cache via
    `invalidate_host_provider_cache()`; this is the per-host force-
    refresh path (drawer reopen with `?force=true`).

    Per-provider polling SSE events: each per-host probe slice that
    actually hits the wire (cache miss) is bracketed by
    ``host:provider_probing`` / ``host:provider_done`` events keyed
    on (host_id, provider). The SPA's chip pulse driver consumes them
    so a chip pulses ONLY while ITS probe is in flight, not the
    whole row-wide `_loading` window. Cache hits skip the events
    (no real fetch happened) so the chip stays at rest.
    """
    from logic import node_exporter as _ne
    from logic import pulse as _pulse
    from logic import webmin as _webmin

    merged: dict = {}
    providers_hit: list[str] = []
    active = state["active"]
    if force:
        _webmin_host_cache.pop(h["id"], None)
        _webmin_host_fail_cache.pop(h["id"], None)
        # SNMP per-host caches — same force=true contract as
        # Webmin. Drop both success + fail entries so the next probe
        # block hits the wire and produces a fresh sample.
        _snmp_host_cache.pop(h["id"], None)
        _snmp_host_fail_cache.pop(h["id"], None)

    # Pulse — coarse fallback layer.
    # HARD-GATE on explicit `pulse_name`. Pre-fix the lookup fell through to `h["id"]` when no
    # alias was set, so every host got probed against the Pulse hub
    # using its host_id; the lookup always missed for non-Pulse hosts
    # and the "host not found in Pulse hub map" failure incremented
    # consecutive_failures until auto-pause. Operators saw "Pulse
    # paused" on hosts they'd never configured for Pulse. Strict gate:
    # operator must set `pulse_name` explicitly to opt this host into
    # the Pulse probe.
    pulse_key = (h.get("pulse_name") or "").strip()
    if "pulse" in active and pulse_key:
        # Per-(pulse, host) auto-pause short-circuit.
        if not _is_provider_paused(h["id"], "pulse"):
            pstats = _pulse.lookup(state["pulse_map"], pulse_key)
            # Hub-fetch-OK gate: only count as a per-host failure when
            # the hub fetch itself succeeded (errors map has no entry).
            # Without this guard a single hub outage would auto-pause
            # every host with a pulse_name.
            hub_ok = "pulse" not in (state.get("errors") or {})
            if pstats:
                # status=down/paused on a hub-OK probe = real failure.
                pst = (pstats.get("pulse_status") or "").lower()
                if pst in ("down", "paused", "unreachable"):
                    if hub_ok:
                        from logic.host_metrics_sampler import record_provider_outcome
                        await record_provider_outcome(
                            h["id"], "pulse", False,
                            error=f"pulse status={pst}",
                            round_threshold=tuning.tuning_int(Tunable.PULSE_FAILURE_PAUSE_ROUNDS),
                        )
                else:
                    _merge_best(merged, pstats)
                    providers_hit.append("pulse")
                    from logic.host_metrics_sampler import record_provider_outcome
                    await record_provider_outcome(h["id"], "pulse", True)
            elif hub_ok:
                from logic.host_metrics_sampler import record_provider_outcome
                await record_provider_outcome(
                    h["id"], "pulse", False,
                    error="host not found in Pulse hub map",
                    round_threshold=tuning.tuning_int(Tunable.PULSE_FAILURE_PAUSE_ROUNDS),
                )

    # SNMP — runs AFTER Pulse but BEFORE Beszel so the unix-
    # style providers can override SNMP's coarser data wherever they
    # have visibility. Each curated row can override community / port
    # / version / v3 keys via `hosts_config[].snmp`; falls through to
    # the global defaults from state otherwise. Per-host alias map
    # (Docker hostname → SNMP target) wins over the row's snmp_name.
    # Per-host enable gate : the row's `snmp.enabled` is an
    # explicit OPT-IN, parallel to ping.enabled. Default-OFF when the
    # flag is missing — the operator must check the per-host SNMP
    # enable box for the probe to fire, even when snmp_name is set.
    if "snmp" in active:
        from logic import snmp as _snmp
        _raw_row_snmp = h.get("snmp")
        row_snmp: dict = _raw_row_snmp if isinstance(_raw_row_snmp, dict) else {}
        snmp_enabled = row_snmp.get("enabled") is True
        # HARD-GATE: probe ONLY when an alias OR a curated `snmp_name`
        # resolves a target. The previous bare-`h["id"]` fallthrough fanned
        # out probes to every host on fleet-enable, ~all-but-mapped of which
        # timed out. Resolution chain: alias > snmp_name > SKIP.
        # Resolution chain for the SNMP probe target:
        #   1. snmp_aliases[h["id"]]   — global Docker-hostname → SNMP-target map
        #   2. h["snmp_name"]          — per-host SNMP-specific target override
        #   3. h["address"]            — curated dedicated probe target (used by
        #                                 port-scan + ping + SSH too — single
        #                                 source of truth for "the LAN address
        #                                 of this host" so disabling other
        #                                 providers doesn't leave SNMP without
        #                                 a target)
        #   4. ""                      — SKIP (no target → no probe)
        snmp_target = (
            (state.get("snmp_aliases") or {}).get(h["id"])
            or (h.get("snmp_name") or "").strip()
            or (h.get("address") or "").strip()
            or ""
        )
        # Per-(snmp, host) auto-pause short-circuit. When the
        # operator-set threshold has been hit on the sampler path the
        # probe is SKIPPED entirely — no cool-down arming, no log spam,
        # no token spend. Operator clears via POST
        # /api/hosts/{id}/provider/snmp/resume; until then the SPA
        # renders the SNMP chip in its Paused state via
        # `provider_pause_state.snmp.paused`.
        snmp_paused = _is_provider_paused(h["id"], "snmp")
        if snmp_target and snmp_enabled and not snmp_paused:
            now = time.time()
            # SNMP per-host caches use SNMP-specific TTLs (was reusing
            # the Webmin pair; operator changing Webmin TTL silently changed
            # SNMP cache behaviour).
            snmp_success_ttl = tuning.tuning_int(Tunable.SNMP_HOST_CACHE_TTL_SECONDS)
            snmp_fail_ttl = tuning.tuning_int(Tunable.SNMP_HOST_FAIL_CACHE_TTL_SECONDS)
            # Resolve the per-host vendor override BEFORE the cache
            # lookup so the cache key includes it. Without that, an
            # operator changing `row.snmp.vendors` from `["dell"]` to
            # `["dell", "cisco"]` keeps serving the cached `["dell"]`
            # result for `tuning_snmp_host_cache_ttl_seconds` (default
            # 30s) so the new Cisco walks don't kick in until expiry.
            # Including the frozenset in the key auto-invalidates on
            # edit. None vendors (auto-detect) hash distinctly.
            snmp_vendors = _clean_vendors_input(row_snmp.get("vendors"))
            cache_key = (
                h["id"],
                frozenset(snmp_vendors) if snmp_vendors else None,
            )
            cached = _snmp_host_cache.get(cache_key)
            if cached and (now - cached[0]) < snmp_success_ttl:
                result = cached[1]
            else:
                fail_cached = _snmp_host_fail_cache.get(cache_key)
                if fail_cached and (now - fail_cached[0]) < snmp_fail_ttl:
                    result = fail_cached[1]
                else:
                    community = (row_snmp.get("community") or "").strip() \
                                or state.get("snmp_default_community") or "public"
                    version = ((row_snmp.get("version") or "").strip().lower()
                               or state.get("snmp_default_version") or "v2c")
                    try:
                        port = int(row_snmp.get("port")
                                   or state.get("snmp_default_port") or 161)
                    except (TypeError, ValueError):
                        port = 161
                    v3_user = ((row_snmp.get("v3_user") or "").strip()
                               or state.get("snmp_v3_user") or "")
                    v3_auth = ((row_snmp.get("v3_auth_key") or "").strip()
                               or state.get("snmp_v3_auth_key") or "")
                    v3_priv = ((row_snmp.get("v3_priv_key") or "").strip()
                               or state.get("snmp_v3_priv_key") or "")
                    # consume tuning_snmp_probe_timeout_seconds.
                    snmp_timeout = float(tuning.tuning_int(Tunable.SNMP_PROBE_TIMEOUT_SECONDS))
                    # Per-host walk_concurrency override — let server-
                    # class BMCs (Dell iDRAC, Cisco IMC, Supermicro IPMI)
                    # opt out of the safety-floor concurrency=1 default
                    # without affecting flaky low-end agents on the
                    # same fleet.
                    snmp_walk_conc = row_snmp.get("walk_concurrency")
                    try:
                        snmp_walk_conc = int(snmp_walk_conc) if snmp_walk_conc else None
                    except (TypeError, ValueError):
                        snmp_walk_conc = None
                    # Per-host wall-clock budget override. Same
                    # contract as walk_concurrency — None = use the
                    # global tunable; explicit int = override.
                    snmp_wcb = row_snmp.get("wall_clock_budget")
                    try:
                        snmp_wcb = float(snmp_wcb) if snmp_wcb else None
                    except (TypeError, ValueError):
                        snmp_wcb = None
                    # Per-host mount-exclusion list. SNMP agents can
                    # mis-classify pseudo-filesystems as fixed disks
                    # (dd-wrt's `/opt` shows up as a 232 GB
                    # hrStorageFixedDisk on a 16 MB router); the
                    # operator opts those paths out by listing them
                    # here. `_DEFAULT_EXCLUDE_MOUNT_PREFIXES` in
                    # logic/snmp.py covers the universal pseudo-fs
                    # paths automatically; this list adds anything
                    # device-specific.
                    snmp_excludes = row_snmp.get("exclude_mounts") or []
                    if not isinstance(snmp_excludes, list):
                        snmp_excludes = []
                    # Per-provider probing event — fires only on cache
                    # MISS (we're inside the cache-miss branch). Cache
                    # hits skip the event entirely so the chip stays at
                    # rest for the microsecond dict lookup.
                    _probe_started = time.time()
                    _publish_provider_probe_event(h["id"], "snmp", "probing", _probe_started, client_id=client_id)
                    # Pre-init so the finally block's `result.get(...)`
                    # is safe even if the await raises a BaseException
                    # (KeyboardInterrupt / asyncio.CancelledError) that
                    # the broad `except Exception` doesn't catch.
                    result: dict = {"hosts": {}}
                    try:
                        result = await _snmp.probe_snmp(
                            snmp_target,
                            community=community,
                            version=version,
                            port=port,
                            v3_user=v3_user,
                            v3_auth_key=v3_auth,
                            v3_priv_key=v3_priv,
                            active_sources=active,
                            timeout=snmp_timeout,
                            walk_concurrency=snmp_walk_conc,
                            vendors=snmp_vendors,
                            wall_clock_budget=snmp_wcb,
                            exclude_mounts=snmp_excludes,
                        )
                    except Exception as e:  # noqa: BLE001
                        result = {"hosts": {}, "error": f"snmp probe failed: {e}"}
                    finally:
                        _publish_provider_probe_event(
                            h["id"], "snmp", "done", _probe_started,
                            client_id=client_id,
                            ok=bool(result.get("hosts") or {}),
                        )
                    if result.get("hosts") or {}:
                        _snmp_host_cache[cache_key] = (now, result)
                        _snmp_host_fail_cache.pop(cache_key, None)
                        # Per-(snmp, host) success path. Routes through
                        # `record_provider_outcome` so the
                        # `host_provider_last_ok` UPSERT lands — the
                        # chip's "Updated Xm ago" subtitle reads from
                        # that table. Mirrors the Webmin sister block.
                        try:
                            from logic.host_metrics_sampler import (
                                record_provider_outcome as _snmp_outcome,
                            )
                            await _snmp_outcome(h["id"], "snmp", True)
                        except Exception as ex:
                            print(f"[hosts] snmp success-record "
                                  f"failed for {h.get('id')!r}: {ex}")
                    else:
                        _snmp_host_fail_cache[cache_key] = (now, result)
                        _snmp_host_cache.pop(cache_key, None)
                        err = result.get("error") or "empty hosts map"
                        err_str = str(err)
                        # Cool-down responses are SKIPS, not real
                        # failures. Pre-fix the log line read
                        # "[hosts] snmp probe failed for 'idrac': ..."
                        # which the persistent-log severity classifier
                        # in `logic/logs.py:_severity_for` matched on
                        # the word "failed" → painted as ERROR in
                        # Admin → Logs. Cool-down on every drawer poll
                        # then floods the ERROR bucket with red lines
                        # despite nothing actually going wrong. Branch
                        # the log: cool-down skips use the verb
                        # "skipped" (no "fail/error" keywords → INFO
                        # severity); real failures keep "failed" →
                        # ERROR. Both include the resolved SNMP target
                        # alongside the host id so operators tracing
                        # back-off can see what hostname / IP was
                        # being probed without knowing the host_id →
                        # snmp_name mapping by heart.
                        skipped = (
                            result.get("skipped_cooldown")
                            or ("cool-down" in err_str)
                        )
                        target_str = snmp_target or h.get("id") or "?"
                        if skipped:
                            print(
                                f"[hosts] snmp probe skipped (cool-down) "
                                f"for {h.get('id')!r} → {target_str}: {err}"
                            )
                        else:
                            print(
                                f"[hosts] snmp probe failed "
                                f"for {h.get('id')!r} → {target_str}: {err}"
                            )
                        # Per-(snmp, host) auto-pause counter — gated
                        # on `not skipped` so cool-down skips don't
                        # count toward the round threshold (the probe
                        # wasn't actually attempted). Mirrors the
                        # Webmin sister block. Real failures (timeout,
                        # auth, no response) DO count.
                        if not skipped:
                            try:
                                from logic.host_metrics_sampler import (
                                    record_provider_outcome as _snmp_outcome,
                                )
                                _snmp_threshold = tuning.tuning_int(
                                    Tunable.SNMP_FAILURE_PAUSE_ROUNDS
                                )
                                await _snmp_outcome(
                                    h["id"], "snmp", False,
                                    error=err_str,
                                    round_threshold=_snmp_threshold,
                                )
                            except Exception as ex:
                                print(f"[hosts] snmp failure-record "
                                      f"failed for {h.get('id')!r}: {ex}")
            hosts_map = result.get("hosts") or {}
            if hosts_map:
                stats = next(iter(hosts_map.values()))
                _merge_best(merged, stats)
                providers_hit.append("snmp")
                # Capture the auto-detected vendor set from the probe
                # diagnostic so the Admin → Hosts editor can render
                # "Auto-detect last result: <vendors>" below the
                # vendor checkbox group. Helps operators new to SNMP
                # decide between trusting auto-detect vs setting an
                # explicit override.
                av = result.get("active_vendors")
                if isinstance(av, list) and av:
                    merged["host_snmp_active_vendors"] = list(av)
                avs = result.get("active_vendors_source")
                if isinstance(avs, str) and avs:
                    merged["host_snmp_active_vendors_source"] = avs

    # Beszel.
    # HARD-GATE on explicit `beszel_name`. Pre-fix the lookup fell through to `h["id"]` when no
    # alias was set, so non-Beszel hosts accumulated "host not found
    # in Beszel hub map" failures and auto-paused on a provider they
    # were never configured for.
    beszel_key = (h.get("beszel_name") or "").strip()
    if "beszel" in active and beszel_key:
        # Per-(beszel, host) auto-pause short-circuit. Same
        # hub-fetch-OK gate as Pulse so a global hub blip doesn't
        # cascade-pause every host.
        if not _is_provider_paused(h["id"], "beszel"):
            bstats = state["beszel_map"].get(beszel_key)
            hub_ok = "beszel" not in (state.get("errors") or {})
            if bstats:
                bst = (bstats.get("beszel_status") or "").lower()
                if bst in ("down", "paused", "unreachable"):
                    if hub_ok:
                        from logic.host_metrics_sampler import record_provider_outcome
                        await record_provider_outcome(
                            h["id"], "beszel", False,
                            error=f"beszel status={bst}",
                            round_threshold=tuning.tuning_int(Tunable.BESZEL_FAILURE_PAUSE_ROUNDS),
                        )
                else:
                    _merge_best(merged, bstats)
                    providers_hit.append("beszel")
                    from logic.host_metrics_sampler import record_provider_outcome
                    await record_provider_outcome(h["id"], "beszel", True)
            elif hub_ok:
                from logic.host_metrics_sampler import record_provider_outcome
                await record_provider_outcome(
                    h["id"], "beszel", False,
                    error="host not found in Beszel hub map",
                    round_threshold=tuning.tuning_int(Tunable.BESZEL_FAILURE_PAUSE_ROUNDS),
                )

    # Node-exporter (per-host probe).
    # operator-tunable timeout via `tuning_node_exporter_probe_timeout_seconds`.
    if "node_exporter" in active and h.get("ne_url"):
        # Per-(node_exporter, host) auto-pause short-circuit.
        if not _is_provider_paused(h["id"], "node_exporter"):
            _ne_timeout = tuning.tuning_int(Tunable.NODE_EXPORTER_PROBE_TIMEOUT_SECONDS)
            _ne_pause_rounds = tuning.tuning_int(Tunable.NODE_EXPORTER_FAILURE_PAUSE_ROUNDS)
            from logic.host_metrics_sampler import record_provider_outcome
            # Per-provider probing SSE event — NE has no per-host
            # cache (each call hits the wire), so every entry to this
            # block fires the start/done pair.
            _probe_started = time.time()
            _publish_provider_probe_event(h["id"], "node_exporter", "probing", _probe_started, client_id=client_id)
            # Track outcome so the `done` event carries the ok hint.
            ne_ok = False
            try:
                async with httpx.AsyncClient(verify=False, timeout=float(_ne_timeout)) as ne_client:
                    stats = await _ne.probe_node(ne_client, h["ne_url"])
                _merge_best(merged, stats or {})
                if stats and not stats.get("exporter_error"):
                    providers_hit.append("node_exporter")
                    await record_provider_outcome(h["id"], "node_exporter", True)
                    ne_ok = True
                else:
                    err = (stats or {}).get("exporter_error") or "no response"
                    await record_provider_outcome(
                        h["id"], "node_exporter", False,
                        error=str(err),
                        round_threshold=_ne_pause_rounds,
                    )
            except Exception as e:  # noqa: BLE001
                print(f"[hosts] NE probe failed for {h.get('id')!r}: {e}")
                await record_provider_outcome(
                    h["id"], "node_exporter", False,
                    error=str(e),
                    round_threshold=_ne_pause_rounds,
                )
            finally:
                _publish_provider_probe_event(
                    h["id"], "node_exporter", "done", _probe_started,
                    client_id=client_id, ok=ne_ok,
                )

    # Webmin (per-host probe, 20s outer budget matching api_hosts).
    # Consults a 30s per-host result cache — Webmin is the slowest
    # provider, so burst-refreshes (e.g. the SPA fanning out
    # /api/hosts/one/{id} twice in a minute) skip the repeat probe.
    if "webmin" in active and state["webmin_creds_ok"]:
        wm_url = state["webmin_aliases"].get(h["id"]) or h.get("webmin_url") or ""
        # Per-(webmin, host) auto-pause short-circuit. Same
        # contract as the SNMP block above — operator clears via POST
        # /api/hosts/{id}/provider/webmin/resume.
        webmin_paused = _is_provider_paused(h["id"], "webmin")
        if wm_url and webmin_paused:
            wm_url = ""  # signal "skip" without re-indenting the rest
        if wm_url:
            now = time.time()
            # both cache TTLs are operator-tunable. Resolved
            # once per call (the same TTLs apply across both branches
            # of the if/else below).
            wm_success_ttl = tuning.tuning_int(Tunable.WEBMIN_HOST_CACHE_TTL_SECONDS)
            wm_fail_ttl = tuning.tuning_int(Tunable.WEBMIN_HOST_FAIL_CACHE_TTL_SECONDS)
            cached = _webmin_host_cache.get(h["id"])
            if cached and (now - cached[0]) < wm_success_ttl:
                result = cached[1]
            else:
                # Negative-result cache — short-circuit a recently-
                # failed probe so a SPA fan-out burst doesn't burn 20s ×
                # PARALLEL on an unreachable Webmin. Tunable TTL means
                # recovery is felt within one Hosts-tab refresh cycle
                # at the default 5s.
                fail_cached = _webmin_host_fail_cache.get(h["id"])
                if fail_cached and (now - fail_cached[0]) < wm_fail_ttl:
                    result = fail_cached[1]
                else:
                    # Webmin probe budget is operator-tunable;
                    # shared with the legacy `api_hosts` consumer.
                    _wm_budget = tuning.tuning_int(Tunable.WEBMIN_PROBE_BUDGET_SECONDS)
                    # Per-provider probing SSE event (cache miss only).
                    _probe_started = time.time()
                    _publish_provider_probe_event(h["id"], "webmin", "probing", _probe_started, client_id=client_id)
                    # Pre-init for the finally's `result.get(...)` so a
                    # BaseException (CancelledError / KeyboardInterrupt)
                    # doesn't crash the SSE publish.
                    result: dict = {"hosts": {}}
                    try:
                        result = await asyncio.wait_for(
                            _webmin.probe_webmin(
                                wm_url, state["webmin_user"], state["webmin_password"],
                                verify_tls=state["webmin_verify"],
                                active_sources=active,
                            ),
                            timeout=_wm_budget,
                        )
                    except asyncio.TimeoutError:
                        result = {"hosts": {}, "error": f"webmin probe timeout after {_wm_budget}s"}
                    except Exception as e:  # noqa: BLE001
                        result = {"hosts": {}, "error": f"webmin probe failed: {e}"}
                    finally:
                        _publish_provider_probe_event(
                            h["id"], "webmin", "done", _probe_started,
                            client_id=client_id,
                            ok=bool(result.get("hosts") or {}),
                        )
                    # Cache the OUTCOME — successes go in the long-lived
                    # cache (30s TTL), failures go in the negative cache
                    # (5s TTL) so a hung Webmin doesn't re-burn 20s on
                    # every parallel call. Recovery is felt within 5s
                    # because the fail cache is short.
                    if result.get("hosts") or {}:
                        _webmin_host_cache[h["id"]] = (now, result)
                        _webmin_host_fail_cache.pop(h["id"], None)
                        # Per-(webmin, host) success path. Routes through
                        # `record_provider_outcome` (NOT bare _clear_failure)
                        # so the `host_provider_last_ok` UPSERT lands —
                        # the chip's "Updated Xm ago" subtitle reads from
                        # that table. Pre-fix the bypass left the subtitle
                        # invisible forever for Webmin chips.
                        try:
                            from logic.host_metrics_sampler import (
                                record_provider_outcome as _wm_outcome,
                            )
                            await _wm_outcome(h["id"], "webmin", True)
                        except Exception as ex:
                            print(f"[hosts] webmin success-record "
                                  f"failed for {h.get('id')!r}: {ex}")
                    else:
                        _webmin_host_fail_cache[h["id"]] = (now, result)
                        # Drop any stale success entry so the negative-cache's
                        # "fast failure detection" claim actually holds. Pre-fix
                        # a host whose success cache was populated 25s ago + has
                        # just gone down would keep serving the stale success
                        # for 5 more seconds (until the success cache's 30s TTL
                        # expired) because the success cache lookup
                        # short-circuits before the fail cache is even
                        # consulted.
                        _webmin_host_cache.pop(h["id"], None)
                        err = result.get("error") or "empty hosts map"
                        # Same severity / target-clarity branch as the
                        # SNMP block. Cool-down skips use the
                        # verb "skipped" so the persistent-log
                        # severity classifier doesn't flag them as
                        # ERROR; real failures keep "failed". Both
                        # include the resolved Webmin URL alongside
                        # the host id so operators tracing back-off
                        # can see WHAT was being probed.
                        err_str = str(err)
                        skipped = result.get("skipped_cooldown") or ("cool-down" in err_str)
                        wm_target = wm_url or h.get("id") or "?"
                        if skipped:
                            print(f"[hosts] webmin probe skipped (cool-down) "
                                  f"for {h.get('id')!r} → {wm_target}: {err}")
                        else:
                            print(f"[hosts] webmin probe failed "
                                  f"for {h.get('id')!r} → {wm_target}: {err}")
                        # Per-(webmin, host) auto-pause counter.
                        # Cool-down responses are SKIPPED (probe wasn't
                        # actually attempted) so they don't count toward
                        # the round threshold. Structured-skip detection:
                        # prefer `result.get("skipped_cooldown")` when the
                        # probe wires it; fall back to substring match for
                        # legacy. Real failures (HTTP 5xx, timeout,
                        # connection refused, agent rejection) DO count.
                        if not skipped:
                            try:
                                from logic.host_metrics_sampler import (
                                    record_provider_outcome as _wm_outcome,
                                )
                                _wm_threshold = tuning.tuning_int(
                                    Tunable.WEBMIN_FAILURE_PAUSE_ROUNDS
                                )
                                await _wm_outcome(
                                    h["id"], "webmin", False,
                                    error=err_str,
                                    round_threshold=_wm_threshold,
                                )
                            except Exception as ex:
                                print(f"[hosts] webmin failure-record "
                                      f"failed for {h.get('id')!r}: {ex}")
            hosts_map: dict = result.get("hosts") or {} if isinstance(result, dict) else {}
            if hosts_map:
                stats = next(iter(hosts_map.values()))
                _merge_best(merged, stats)
                providers_hit.append("webmin")

    # Ping — fifth provider, runs LAST in the merge chain. Only
    # consults the LATEST stored sample (the sampler does the actual
    # probing on its own cadence). When this host is opted-out
    # (``hosts_config[].ping.enabled == False``), we deliberately skip —
    # no row, no chip, no banner.
    _raw_pcfg = h.get("ping")
    pcfg: dict = _raw_pcfg if isinstance(_raw_pcfg, dict) else {}
    if "ping" in active and pcfg.get("enabled"):
        from logic import ping_sampler as _ping_sampler
        from logic import ping as _ping_mod
        recent = _ping_sampler.last_samples(h["id"], limit=1)
        if recent:
            last = recent[0]
            stats = _ping_mod.to_host_stats({
                "alive": last.get("alive"),
                "rtt_ms": last.get("rtt_ms"),
                "loss_pct": last.get("loss_pct"),
            })
            if stats:
                _merge_best(merged, stats)
                # Count ping as a "provider hit" whenever we got a sample
                # back, regardless of alive/down. The alive flag is
                # surfaced separately on the row so the SPA can render
                # the right chip + status colour. Pre-fix this only
                # appended when alive=True, which meant a ping-only host
                # that was currently DOWN got filtered out as "no
                # provider returned data" and rendered grey/unconfigured
                # instead of the red "down" the operator expected.
                providers_hit.append("ping")

    # Port-scan history fold-in — populate `merged.detected_ports`
    # from `host_port_scans`. Does NOT bump `providers_hit`: port-scan
    # is a curated-config / on-demand surface, not a continuous
    # monitoring provider, and the providers count below the host
    # name on the drawer was reading "+1" on every host with prior
    # scan rows because of an earlier inclusion of "port_scan" here.
    # The detected-ports chip strip + the optional port_scan_refresh
    # schedule kind are surfaced via their own UI paths; the merged-
    # row `providers` array stays scoped to the live-telemetry
    # providers (Beszel / NE / Pulse / Webmin / SNMP / Ping).
    _populate_detected_ports(h["id"], merged)

    # Re-derive `host_mem_percent` and `host_disk_percent` from the
    # MERGED used + total values so the percent stays consistent with
    # the bytes regardless of which providers contributed which field.
    # Without this: SNMP reports `host_mem_percent: 90.87` (computed
    # from `total - free`, FreeBSD-naive — doesn't subtract cache /
    # inactive), while NE reports `host_mem_used: 13.58 GB` /
    # `host_mem_total: 16.56 GB` (FreeBSD-aware: free + inactive +
    # laundry + cache as available). NE comes AFTER SNMP in the merge
    # order, but NE's `extract_stats` doesn't emit `host_mem_percent`
    # — only used/total/avail — so SNMP's percent survives the merge
    # while NE's bytes win, producing an inconsistent merged shape:
    # 90% in `host_mem_percent` (used in the host card label) vs
    # ~82% the chart history shows (sampler computes from NE's
    # bytes). Reported on a FreeBSD / OPNsense host where the outside
    # card reads "90% (13 GB / 15 GB)" while the drawer's memory chart
    # reads 82% — same data, two answers. Recomputing from the
    # merged bytes gives one truth.
    _t = merged.get("host_mem_total") or 0
    _u = merged.get("host_mem_used") or 0
    if _t > 0 and _u > 0:
        merged["host_mem_percent"] = round(min(100.0, (_u / _t) * 100.0), 2)
    # Post-merge mounts-aggregate override — handles the case where
    # ANY provider supplied a `mounts[]` list (Beszel EFS, Pulse via
    # PVE guest-agent fsinfo, NE per-mount, Webmin per-mount) that
    # sums to substantially MORE than the merged `host_disk_total`.
    # When that happens, the operator-visible reality is the per-mount
    # list (which the SPA renders as DISKS rows), so the chip aggregate
    # should agree with it. Catches the TrueNAS case where Pulse's
    # guest-agent fsinfo populates `mounts[/mnt/POOL1, /]` with 5.3 TB
    # total but `host_disk_total` is the agent's overlay disk
    # (~802 GiB) — the chip used to read 0.1% used because the
    # numerator was the overlay's used and the denominator was its
    # total, while the per-mount list correctly showed 84% used on
    # the actual data pool. Threshold: only override when the mounts
    # sum is at least 1.5× the existing total — protects against
    # spurious mount entries (loop devices, tmpfs leaking through)
    # tipping the aggregate by a small margin. The mounts list `d` /
    # `du` fields are GiB floats per the schema in `_flatten_efs` and
    # `_pulse_mounts`; convert × 1024^3 to bytes for the byte-totals.
    mounts = merged.get("mounts") or []
    if isinstance(mounts, list) and mounts:
        gib = 1024 ** 3
        m_total = 0.0
        m_used = 0.0
        for _m in mounts:
            if not isinstance(_m, dict):
                continue
            try:
                m_total += float(_m.get("d") or 0)
                m_used += float(_m.get("du") or 0)
            except (TypeError, ValueError):
                continue
        if m_total > 0:
            m_total_b = int(m_total * gib)
            m_used_b = int(m_used * gib)
            cur_total = int(merged.get("host_disk_total") or 0)
            # Override when the mounts aggregate is meaningfully bigger
            # than the merged total. 1.5× threshold catches the
            # overlay-vs-real-disk case (5300/802 ≈ 6.6×) without
            # flapping on small per-mount additions.
            if cur_total <= 0 or m_total_b > cur_total * 1.5:
                merged["host_disk_total"] = m_total_b
                merged["host_disk_used"] = m_used_b
                merged["host_disk_free"] = max(0, m_total_b - m_used_b)
                try:
                    print(
                        f"[hosts] mounts-aggregate {h.get('id')!r}: "
                        f"replaced host_disk_total={cur_total} "
                        f"with mounts-summed {m_total_b} "
                        f"({m_total:.1f} GiB total / {m_used:.1f} GiB used "
                        f"across {len(mounts)} mounts)"
                    )
                except (ValueError, TypeError, AttributeError):
                    pass
    _t = merged.get("host_disk_total") or 0
    _u = merged.get("host_disk_used") or 0
    if _t > 0 and _u > 0:
        merged["host_disk_percent"] = round(min(100.0, (_u / _t) * 100.0), 2)

    # HTTP / TLS / DNS probe — seventh host-stats provider. Reads
    # persisted samples from `host_http_samples` via the shared helper
    # so this endpoint AND `/api/hosts/list` (skeleton) surface the
    # same on-disk state without duplicating the SELECT. No live probe
    # here — the lifespan sampler owns the wire calls; this endpoint
    # just stamps the latest persisted result onto the merged dict.
    # Per-host enable gate AND master toggle: the row's
    # `http_probe.enabled` must be True AND `http_probe_enabled` master
    # setting must be on. When neither is set, skip entirely so the
    # merged dict doesn't carry stale fields from a previous enrolment.
    # Per-host in-memory cache mirrors the Webmin / SNMP pattern: a
    # success TTL avoids re-running the SELECT on every fan-out call,
    # a shorter failure TTL keeps recovery snappy. Reads via
    # `tuning_int` per-call so an Admin → Config edit lands without
    # restart.
    try:
        _raw_http = h.get("http_probe")
        _http_cfg: dict = _raw_http if isinstance(_raw_http, dict) else {}
        _http_master = get_setting_bool(Settings.HTTP_PROBE_ENABLED)
        if (
            "http_probe" in active
            and _http_master
            and _http_cfg.get("enabled") is True
            and not _is_provider_paused(h["id"], "http_probe")
        ):
            from logic.host_http_sampler import populate_host_http_merge
            now_http = time.time()
            http_success_ttl = tuning.tuning_int(Tunable.HTTP_PROBE_HOST_CACHE_TTL_SECONDS)
            http_fail_ttl = tuning.tuning_int(Tunable.HTTP_PROBE_HOST_FAIL_CACHE_TTL_SECONDS)
            cache_key = h["id"]
            cached = _http_probe_host_cache.get(cache_key)
            if cached and (now_http - cached[0]) < (http_success_ttl if cached[2] else http_fail_ttl):
                cached_fields: dict = cached[1]
            else:
                pre_keys = {
                    k: merged.get(k) for k in (
                        "host_http_status_ok", "host_http_status_code",
                        "host_http_content_match_ok", "host_http_tls_expires_in_days",
                        "host_http_tls_subject", "host_http_dns_resolved",
                        "host_http_latency_ms", "host_http_error",
                        "host_http_url_count_total", "host_http_url_count_ok",
                        "host_http_urls", "host_http_ts",
                    )
                }
                populate_host_http_merge(h["id"], merged)
                post = {k: merged.get(k) for k in pre_keys}
                # Cache the just-stamped fields so the next fan-out call
                # reuses them. `had_data` flag drives the TTL choice
                # (success = longer reuse, failure = shorter recovery).
                had_data = bool(merged.get("host_http_url_count_total"))
                _http_probe_host_cache[cache_key] = (now_http, post, had_data)
                cached_fields = post
            for k, v in cached_fields.items():
                if v is not None:
                    merged[k] = v
            if merged.get("host_http_url_count_total"):
                providers_hit.append("http_probe")
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] http_probe merge failed for {h.get('id')!r}: {e}")

    # Per-service reachability probe — stamps `last_probe` on every
    # `services[]` entry that has a recent sample. Gate on master
    # toggle so the merged dict doesn't carry stale per-chip status
    # when the operator has flipped the feature off globally.
    try:
        if get_setting_bool(Settings.SERVICE_PROBE_ENABLED):
            from logic.service_sampler import populate_host_service_merge
            populate_host_service_merge(h["id"], merged)
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] service_probe merge failed for {h.get('id')!r}: {e}")

    # Snapshot fallback — when a provider went down mid-session,
    # fill missing host_* fields from the previous gather's persisted
    # snapshot and tag them in `_stale_fields` so the SPA can dim those
    # values. Only fills MISSING fields — live values from this run
    # always win. `apply_host_snapshot_fallback` is a no-op when no
    # snapshot exists for this host.
    try:
        from logic.gather import apply_host_snapshot_fallback as _fallback
        _fallback({h["id"]: merged})
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] snapshot fallback failed for {h.get('id')!r}: {e}")

    # Persist the just-merged dict as the snapshot for this host
    # . Pre-fix, snapshots were only written by the legacy
    # _gather_impl path (the one /api/items uses) which builds
    # nodes_info from Swarm-node hostnames — curated SNMP-only hosts
    # like UPSes / managed switches that aren't Swarm nodes never
    # appeared in nodes_info, so save_host_snapshots never wrote a
    # row for them. Fallback then had nothing to restore when SNMP
    # stopped returning data → operator-reported "UPS card disappears
    # 5 minutes after the last probe even though SNMP says Updated 7m
    # ago". Write the snapshot from the per-host probe path AS WELL
    # so any host that ever has a successful probe gets a fallback
    # source.
    #
    # Gate: persist ONLY when at least one snapshot-
    # eligible field is LIVE (not from fallback). Pre-fix the gate
    # was "any meaningful host_* field present" — that fired even
    # when EVERY field came from `apply_host_snapshot_fallback`
    # above, so the snapshot's `ts` kept refreshing to "now" on every
    # 15s drawer poll even when no live data had been recorded for
    # minutes. Operator-reported: printer card freshness label
    # ("3m ago") disagreed with the SNMP chart freshness label
    # ("6m ago") because the snapshot ts kept getting touched while
    # the underlying samples table (which the chart freshness reads)
    # tracked the actual last-live-probe correctly. Now the snapshot
    # only persists when at least one snapshot-eligible field came
    # from a LIVE provider — i.e. is meaningful AND not in
    # `_stale_fields`. Entirely-fallback merges skip the write so
    # the existing snapshot's `ts` stays at the genuine last-live
    # timestamp.
    try:
        from logic.gather import (
            save_host_snapshots as _save_snaps,
            is_snapshot_key as _snap_key,
        )
        from logic.merge import is_meaningful as _is_mean
        stale_set = set(merged.get("_stale_fields") or [])
        has_live_field = any(
            _snap_key(k) and _is_mean(v) and k not in stale_set
            for k, v in merged.items()
        )
        if has_live_field:
            _save_snaps({h["id"]: merged})
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] snapshot save failed for {h.get('id')!r}: {e}")

    # Per-host per-provider sample row counts — surfaces under the
    # "Updated Xs ago" subtitle in the host drawer's enabled-agents
    # chip strip so the user can see at a glance how much history is
    # available per provider for THIS host. Single multi-table SELECT
    # per host so the fan-out cost stays bounded; failures swallowed
    # since this is a UX hint, not load-bearing.
    try:
        merged["provider_sample_counts"] = _provider_sample_counts(h["id"])
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] provider_sample_counts failed for {h.get('id')!r}: {e}")
        merged["provider_sample_counts"] = {}

    # Per-provider effective sampler interval (seconds) — surfaces as
    # the third subtitle line below the chip ("Every Ns" / "Every Nm").
    # Resolves the "0 = inherit" sentinel each sampler uses so the user
    # sees the actual applied value, not the raw tunable. Host-id is
    # accepted for future per-host override knobs; current intervals
    # are global.
    try:
        merged["provider_sample_intervals"] = _provider_sample_intervals(h["id"])
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] provider_sample_intervals failed for {h.get('id')!r}: {e}")
        merged["provider_sample_intervals"] = {}

    # Drift-from-baseline enrichment. Reads the cached
    # `host_baselines` row for THIS host and classifies each live
    # metric (CPU% / mem% / disk% / ping RTT) as ▲ / ▼ / ━ vs the
    # 30-day rolling median ± IQR. Returns {} when no baseline has
    # been computed yet (<50 samples in the window OR sampler hasn't
    # run yet) — the SPA hides the chip in that case. Cheap read
    # (single SELECT keyed on host_id); failures swallowed.
    try:
        from logic import host_baseline as _baseline
        merged["drift"] = _baseline.host_drift_for_api(h["id"], merged)
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] drift compute failed for {h.get('id')!r}: {e}")
        merged["drift"] = {}

    return merged, providers_hit


def _provider_sample_intervals(host_id: str) -> dict:
    """Return ``{<provider>: int seconds}`` for each curated provider's
    effective sampler cadence. Resolves the "0 = inherit" sentinel each
    sampler uses so the value matches what the operator's sampler is
    ACTUALLY ticking at, not the raw tunable. Provider names match
    `agent.name` in the SPA's `hostEnabledAgents`.

    `host_id` is accepted for future per-host override knobs; current
    intervals are global, so the value is identical across hosts in
    this implementation.
    """
    _ = host_id  # placeholder for future per-host override resolution
    from logic import tuning as _tuning
    out: dict[str, int] = {}
    global_iv = max(30, int(_tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS)) or 300)
    # Each provider's tunable: 0 = inherit global, > 0 = explicit override.
    # The sampler floor (typically 10s or 30s) is also applied so the
    # surfaced value matches what the sampler loop actually sleeps for.
    inheritors = (
        ("ping", Tunable.PING_INTERVAL_SECONDS, 10),
        ("snmp", Tunable.SNMP_SAMPLE_INTERVAL_SECONDS, 30),
        ("beszel", Tunable.BESZEL_SAMPLE_INTERVAL_SECONDS, 30),
        ("pulse", Tunable.PULSE_SAMPLE_INTERVAL_SECONDS, 30),
        ("node_exporter", Tunable.NODE_EXPORTER_SAMPLE_INTERVAL_SECONDS, 30),
        # Probe-result providers — also follow the inherit-or-override
        # contract via their dedicated sample-interval tunables. Floor
        # matches each sampler's own ``max(floor, raw)`` clamp so the
        # surfaced value is what the sampler loop actually sleeps for.
        ("http_probe", Tunable.HTTP_PROBE_SAMPLE_INTERVAL_SECONDS, 30),
        ("service_probe", Tunable.SERVICE_PROBE_SAMPLE_INTERVAL_SECONDS, 30),
    )
    for name, key, floor in inheritors:
        try:
            raw = int(_tuning.tuning_int(key) or 0)
        except (ValueError, TypeError, KeyError):
            raw = 0
        effective = max(floor, raw) if raw > 0 else global_iv
        out[name] = effective
    # Webmin has no dedicated sample-interval knob — its sampler shares
    # the global cadence directly (see logic/host_webmin_sampler.py).
    out["webmin"] = global_iv
    return out


def _provider_sample_counts(host_id: str) -> dict:
    """Return ``{<provider>: count}`` for the curated provider lineup,
    keyed by the same provider names ``hostEnabledAgents`` emits on the
    SPA side. Each value is the raw row count in the matching
    `host_<provider>_samples` table (or `ping_samples` / etc.) for
    THIS host. Best-effort — missing tables (fresh deploy, schema not
    yet created) yield 0; never raises.
    """
    out: dict[str, int] = {}
    # `(provider_name, sql)` pairs. Provider name matches `agent.name`
    # in `hostEnabledAgents`. SQL is parameterised on `host_id`.
    queries = (
        ("ping", "SELECT COUNT(*) FROM ping_samples WHERE host_id = ?"),
        ("snmp", "SELECT COUNT(*) FROM host_snmp_samples WHERE host_id = ?"),
        ("beszel", "SELECT COUNT(*) FROM host_beszel_samples WHERE host_id = ?"),
        ("pulse", "SELECT COUNT(*) FROM host_pulse_samples WHERE host_id = ?"),
        ("webmin", "SELECT COUNT(*) FROM host_webmin_samples WHERE host_id = ?"),
        ("node_exporter", "SELECT COUNT(*) FROM host_metrics_samples WHERE host_id = ?"),
        # Probe-result providers — same per-host count surface as the
        # six telemetry providers above. host_http_samples carries one
        # row per (host, url, ts); service_samples carries one row per
        # (host, service_idx, ts).
        ("http_probe", "SELECT COUNT(*) FROM host_http_samples WHERE host_id = ?"),
        ("service_probe", "SELECT COUNT(*) FROM service_samples WHERE host_id = ?"),
    )
    try:
        with db_conn() as c:
            for name, sql in queries:
                try:
                    row = c.execute(sql, (host_id,)).fetchone()
                    out[name] = int(row[0]) if row else 0
                except (sqlite3.Error, ValueError, TypeError):
                    # Missing table on fresh deploy / sampler never ran.
                    out[name] = 0
    except (sqlite3.Error, OSError):
        return {}
    return out


# True when a host id matches a Swarm node hostname (long-form OR
# short-form). Used to gate the `docker_node` field — non-Swarm hosts
# (VMs / appliances / routers / 5G modems) get an empty value so the
# drawer's misleading "Docker node: <id>" row hides for them.
def _is_swarm_node(host_id) -> bool:
    if not host_id:
        return False
    hid = str(host_id).strip().lower()
    if not hid:
        return False
    short = hid.split(".", 1)[0]
    for n in (_cache.get("nodes") or {}).values():
        if not n:
            continue
        ns = str(n).strip().lower()
        if not ns:
            continue
        if ns == hid or ns == short or ns.split(".", 1)[0] == hid \
            or ns.split(".", 1)[0] == short:
            return True
    return False


# Module-level asset-index cache, keyed on the cache file's mtime so
# we re-build only when the on-disk snapshot actually changes. Hot
# path: every `_shape_host_api_row` call. Cold path: refresh adds
# ~10ms (file read + dict build).
_asset_idx_cache: dict = {"mtime": None, "index": {}}


def _resolve_asset_for_host(cn) -> Optional[dict]:
    """Look up the cached asset row for a host's custom_number and
    return the compact `shape_asset` dict (or None when no match).

    Re-reads the cache file when its mtime advances, otherwise reuses
    the indexed map. Resilient to a missing / unreadable cache —
    returns None on any error so `_shape_host_api_row` can still
    build a row for hosts whose asset data isn't available yet.

    Sentinel handling: ``mtime`` is ``None`` for "no readable cache
    file yet". Comparing a real mtime (any float, including 0.0) to
    None is always non-equal, so we rebuild on the first successful
    read; subsequent calls with a missing file stay at ``mtime=None``
    and DO NOT rebuild the empty index every call.
    """
    if cn is None:
        return None
    try:
        cn_int = int(cn)
    except (TypeError, ValueError):
        return None
    from logic import asset_inventory as _ai
    try:
        mtime: Optional[float] = os.path.getmtime(_ai.DEFAULT_CACHE_PATH)
    except OSError:
        mtime = None
    if mtime != _asset_idx_cache["mtime"]:
        try:
            cache = _ai.load_cache()
            _asset_idx_cache["index"] = _ai.index_by_custom_number(cache.get("assets") or [])
        except (OSError, ValueError, KeyError, AttributeError):
            _asset_idx_cache["index"] = {}
        _asset_idx_cache["mtime"] = mtime
    raw = _asset_idx_cache["index"].get(cn_int)
    return _ai.shape_asset(raw) if raw else None


def _resolve_ping_target(h: dict) -> Optional[str]:
    """Mirror of `logic.ping_sampler._curated_ping_hosts`'s target chain.

    Returns the resolved hostname / IP that `probe_ping` will actually
    use, or None when ping isn't enabled on the row. Surfacing this in
    the API row (`ping_target`) lets the SPA's `?` info-bubble tooltip
    name the actual probe target instead of the curated host_id (which
    is often a label like "ftth" that doesn't resolve via DNS).

    Resolution chain (FIRST non-empty wins):
      1. `address` (curated dedicated probe target — independent of
         any provider, operator-set as "the LAN address for this host")
      2. `ping.host` (per-host override on the ping provider)
      3. `ssh.fqdn` (per-host SSH FQDN — most curated rows have this)
      4. `ssh.host` (alternate SSH-target spelling, legacy)
      5. `h.id` (last-resort fallback)

    The curated `url` field is DELIBERATELY excluded — it carries the
    clickable web-UI link the operator wants to surface on the host
    card. Probing it would target the public service relay instead of
    the LAN host (wrong data + privacy concern).

    The `address` field is the canonical dedicated probe target —
    independent of any provider so disabling SNMP / ping / SSH never
    leaves the other probes without a target. Operators set it in
    Admin → Hosts. If left blank, the chain falls through to provider-
    specific overrides then the bare host_id.
    """
    _raw_ping_cfg = h.get("ping")
    ping_cfg: dict = _raw_ping_cfg if isinstance(_raw_ping_cfg, dict) else {}
    if not bool(ping_cfg.get("enabled", False)):
        return None
    _raw_ssh_cfg = h.get("ssh")
    ssh_cfg: dict = _raw_ssh_cfg if isinstance(_raw_ssh_cfg, dict) else {}
    candidate = (
        (h.get("address") or "").strip()
        or (ping_cfg.get("host") or "").strip()
        or (ssh_cfg.get("fqdn") or "").strip()
        or (ssh_cfg.get("host") or "").strip()
        or (h.get("id") or "").strip()
    )
    return candidate or (h.get("id") or "")


def _is_host_unconfigured(
    active: Optional[Iterable[str]],
    active_set: frozenset[str],
    h: dict,
    *,
    ping_enabled: bool,
    snmp_mapped: bool,
    any_provider_enabled: bool,
) -> bool:
    """Decide whether a curated host has NO globally-enabled provider
    mapped to one of its per-row fields. Extracted from the
    `_shape_host_api_row` host-status fallthrough chain so the nested-
    paren boolean expression doesn't trip PyCharm's continuation-indent
    inspection.

    Two branches: when caller supplies `active` (the canonical case),
    each per-row field must align with a globally-enabled provider for
    the field to count as "mapped". When `active is None` (legacy
    callers), fall back to the whole-row OR-chain.
    """
    if active is not None:
        return not (
            ("beszel" in active_set and (h.get("beszel_name") or "").strip())
            or ("pulse" in active_set and (h.get("pulse_name") or "").strip())
            or ("webmin" in active_set and (h.get("webmin_name") or "").strip())
            or ("node_exporter" in active_set and (h.get("ne_url") or "").strip())
            or ("ping" in active_set and ping_enabled)
            or ("snmp" in active_set and snmp_mapped)
        )
    # Legacy gate — back-compat callers see the whole-row OR-chain.
    return (not any_provider_enabled) or not (
        (h.get("beszel_name") or "").strip()
        or (h.get("pulse_name") or "").strip()
        or (h.get("webmin_name") or "").strip()
        or (h.get("ne_url") or "").strip()
        or ping_enabled
        or snmp_mapped
    )


def _sync_host_stats_source(provider: str, enabled: bool) -> None:
    """Add or remove `provider` from the `host_stats_source` CSV
    setting so the merge gate (which requires both the per-provider
    master flag AND the CSV membership) stays consistent with the
    operator-visible toggle.

    Without this sync the operator could flip `http_probe_enabled` or
    `service_probe_enabled` ON in Admin → Providers, see the master
    switch confirmed, but the merge gate would still skip the provider
    because the CSV didn't include the token. The host drawer card
    then renders empty + the chip strip never gets the provider — a
    "looks right but doesn't work" failure mode.

    Called from `api_set_settings` whenever an http_probe / service_probe
    master toggle landed. Idempotent: re-adding an existing token is a
    no-op; removing one that isn't there is a no-op.
    """
    token = provider.strip().lower()
    if not token:
        return
    current_raw = (get_setting(Settings.HOST_STATS_SOURCE) or "").strip()
    parts: list[str] = []
    for t in current_raw.split(","):
        t_clean = t.strip().lower()
        if t_clean and t_clean != "none" and t_clean not in parts:
            parts.append(t_clean)
    if enabled:
        if token not in parts:
            parts.append(token)
    else:
        if token in parts:
            parts = [p for p in parts if p != token]
    normalized = ",".join(sorted(parts)) if parts else "none"
    if normalized != current_raw:
        set_setting(Settings.HOST_STATS_SOURCE, normalized)


def _shape_host_apps(h: dict) -> list[dict]:
    """Return the Apps feature's per-chip array for the API row.

    Walks ``h["services"]`` (operator-curated chip array on the
    ``hosts_config[]`` entry), stamps each entry with:

    - ``service_idx``  — stable position in the source list; consumed
      by the manual probe endpoint and per-port history queries.
    - ``last_probe``   — most-recent sample from ``service_samples``
      for this (host_id, service_idx). ``None`` when no sample yet.
    - ``status``       — derived ``up`` / ``down`` / ``unknown`` from
      ``last_probe.alive`` (None when no sample).
    - ``catalog``      — resolved catalog template dict when the chip
      carries ``catalog_id``; ``None`` otherwise.

    Source list entries are NOT mutated — the returned shape is a
    fresh dict per chip so frontend mutations during render can't
    propagate back. Empty list when ``h["services"]`` is missing /
    not a list. Catalog lookup is cached per call so a 30-chip host
    doesn't hit the DB 30 times.
    """
    chips_raw = h.get("services")
    if not isinstance(chips_raw, list) or not chips_raw:
        return []
    host_id = (h.get("id") or "").strip()
    if not host_id:
        return []
    try:
        from logic.service_sampler import (
            latest_for_host as _latest_for_host,
            latest_per_port_for_host as _latest_per_port,
        )
        latest = _latest_for_host(host_id)
    except Exception as e:  # noqa: BLE001
        print(f"[apps] latest_for_host({host_id!r}) skipped: {e}")
        latest = {}
        _latest_per_port = None  # type: ignore[assignment]
    # Catalog lookup cache for the call. Resolves catalog_id -> dict.
    # Optional[dict] value type so a catalog lookup that resolves to
    # None (the FK pointed at a deleted template) can still be cached
    # — avoids re-querying the DB for the same dead id on every chip.
    catalog_cache: dict[int, Optional[dict]] = {}
    try:
        from logic.service_catalog import get_catalog_by_id as _get_catalog
    except ImportError:
        _get_catalog = None  # type: ignore[assignment]
    out: list[dict] = []
    for idx, chip in enumerate(chips_raw):
        if not isinstance(chip, dict):
            continue
        sample = latest.get(idx) if isinstance(latest, dict) else None
        status = "unknown"
        if isinstance(sample, dict):
            status = "up" if sample.get("alive") else "down"
        catalog_block: Optional[dict] = None
        # `_coerce_int_local` (imported at module top from
        # service_catalog) narrows the Any-typed `catalog_id` cell to
        # Optional[int] without the legacy try/except + `in (None, "")`
        # ladder that type checkers couldn't see through.
        cid_int = _coerce_int_local(chip.get("catalog_id"))
        if cid_int and _get_catalog is not None:
            if cid_int in catalog_cache:
                catalog_block = catalog_cache[cid_int]
            else:
                try:
                    catalog_block = _get_catalog(cid_int)
                except Exception as e:  # noqa: BLE001
                    print(f"[apps] catalog lookup {cid_int} skipped: {e}")
                    catalog_block = None
                catalog_cache[cid_int] = catalog_block
        # Per-port latest results — populated only when this chip is
        # multi-port AND the sampler has run at least once for it. Empty
        # list for single-port chips OR multi-port chips with no history.
        port_results: list[dict] = []
        probe_block = chip.get("probe") or {}
        is_multi_port = isinstance(probe_block.get("ports"), list) and bool(probe_block.get("ports"))
        if is_multi_port and _latest_per_port is not None:
            try:
                port_results = _latest_per_port(host_id, idx)
            except Exception as e:  # noqa: BLE001
                print(f"[apps] latest_per_port({host_id!r}/{idx}) skipped: {e}")
                port_results = []
        out.append({
            "service_idx": idx,
            "name": (chip.get("name") or "").strip(),
            "url": (chip.get("url") or "").strip(),
            "icon": (chip.get("icon") or "").strip(),
            "catalog_id": cid_int,
            "catalog": catalog_block,
            "probe": probe_block,
            "status": status,
            "last_probe": sample,
            "port_results": port_results,
        })
    return out


# noinspection PyTypeChecker,PyUnresolvedReferences
def _shape_host_api_row(
    h: dict,
    merged: dict,
    providers_hit: list[str],
    any_provider_enabled: bool = True,
    active: Optional[Iterable[str]] = None,
) -> dict:
    """Shape a (curated_host, merged_stats) pair into the wire format.

    ``any_provider_enabled`` — false when NO provider is enabled
    globally (``state.active`` is empty). In that case a host with
    provider fields mapped can't be probed at all, so we report
    `status: 'unconfigured'` instead of `'unknown'` — grey dot, no
    "no data" banner, because there's literally nothing OmniGrid
    could have done. Operators see a clear "configure a provider"
    path instead of a false red alert.

    ``active`` — when supplied (recommended), the set of provider
    names currently enabled globally. Used to intersect the per-row
    field gate so a stale field for a globally-disabled provider
    (e.g. `beszel_name` left over from a prior config when Beszel is
    no longer enabled) does NOT flip the row to `status: "unknown"`.
    Same drift class as the SNMP `enabled` flag gate above, but at
    the per-field-per-provider layer. Legacy bool gate retained for
    back-compat — when ``active`` is None the OLD whole-row OR-chain
    fires (matches pre-fix behaviour).
    """
    s = merged or {}
    # Status precedence (revised — see operator complaint that hosts
    # were marked "down" purely because Beszel was paused/down even
    # when Pulse + node-exporter + Webmin were happily scraping):
    # 1. ANY non-Beszel provider returning data → "up". Beszel's
    #    self-reported status is suggestive but its agent can be
    #    paused/down while the host is still reachable — pulse /
    #    NE / webmin all probe via different paths/ports/protocols
    #    and a successful scrape from any of them proves the host
    #    is alive. SSH and other "is this host reachable" gates
    #    depend on this status, so a single failing provider must
    #    not lock other features out.
    # 2. Beszel's explicit status (with paused → down normalisation)
    #    when Beszel is the ONLY signal we have. Operator pauses
    #    hosts in Beszel deliberately when they're offline; "down"
    #    here reflects reality.
    # 3. Pulse's explicit status as a secondary fallback.
    # 4. "up" if any provider hit at all (covers Beszel-only
    #    hosts where Beszel returned data with no explicit status).
    # 5. "unconfigured" when no provider is mapped/enabled — grey.
    # 6. "unknown" when providers ARE mapped + active but none
    #    answered — surfaced red as a real outage signal.
    beszel_st = s.get("beszel_status")
    if beszel_st == "paused":
        beszel_st = "down"
    pulse_st = s.get("pulse_status")
    # Ping is excluded from `non_beszel_hit` because — unlike the other
    # providers — a ping "hit" doesn't prove the host is alive. Ping IS
    # the alive/down signal, so a ping sample that says alive=False
    # means the host is down. The dedicated ping branch below derives
    # "up" / "down" from `host_ping_alive`; the other providers
    # implicitly mean "alive" when they return data at all.
    # SNMP slots in here as a "real telemetry hit" (alongside pulse /
    # node_exporter / webmin) — when SNMP successfully returns data,
    # the host is alive on the network even if Beszel hasn't reached
    # it yet.
    non_beszel_hit = any(
        p in providers_hit for p in ("pulse", "node_exporter", "webmin", "snmp")
    )
    ping_hit = "ping" in providers_hit
    ping_alive = s.get("host_ping_alive")
    ping_enabled = bool((h.get("ping") or {}).get("enabled", False))
    # SNMP mapping gate — matches the per-host probe path
    # (`_merge_one_host`) and the debug panel's `host_active`
    # computation: SNMP is mapped iff (a) the per-host opt-in flag
    # `snmp.enabled === True` AND (b) at least one resolvable target
    # (`snmp_name` OR shared `address`). Without the `enabled` gate
    # any stale override left in the `snmp` sub-dict (`{community,
    # version, port}` typed once, then the operator unchecked the
    # enable box) makes a row read as "snmp mapped" and the status
    # flips from `unconfigured` (grey, no problem) to `unknown` (red,
    # real outage signal) — false-positive that the operator can only
    # cure by wiping the whole sub-dict in Admin → Hosts.
    snmp_block = h.get("snmp")
    snmp_mapped = bool(
        isinstance(snmp_block, dict)
        and snmp_block.get("enabled") is True
        and (
            (h.get("snmp_name") or "").strip()
            or (h.get("address") or "").strip()
        )
    )
    # Frozenset for the refined gate below. Empty when caller didn't
    # supply `active` — the legacy bool gate handles that branch.
    _active_set: frozenset[str] = frozenset(active) if active is not None else frozenset()
    if non_beszel_hit:
        host_status = "up"
    elif beszel_st in ("up", "down"):
        host_status = beszel_st
    elif pulse_st:
        host_status = pulse_st
    elif ping_hit:
        host_status = "up" if ping_alive else "down"
    elif providers_hit:
        host_status = "up"
    elif s.get("_stale_fields"):
        # Cold-load /api/hosts/list path: probes haven't run yet
        # (providers_hit is empty by design — that endpoint is the
        # fast skeleton, the per-host fan-out via /api/hosts/one
        # fills live status afterwards). The snapshot-fallback at
        # apply_host_snapshot_fallback restored host_* fields from
        # the persisted snapshot AND stamped _stale_fields, which
        # is evidence the previous gather successfully reached this
        # host. Promote status='up' provisionally so the SPA's bar
        # gates (which require h.status === 'up') render snapshot-
        # derived bars + sparklines on cold-load instead of staying
        # empty until the per-host probe lands. The _stale_fields
        # marker stays set so the UI dims the values + tooltips
        # them with "X minutes ago" via the existing stale-rendering
        # pipeline. Pre-fix the gate ALSO required one of four
        # specific host_* fields (cpu / mem_total / disk_total /
        # uptime_s) — a healthy host whose snapshot carried a
        # different subset (identity-only `host_platform` / `host_
        # kernel` / `host_arch`; SNMP-derived metrics under different
        # keys; Webmin's coarser snapshots) fell through to the
        # `else: "unknown"` branch and got flagged as a problem host.
        # The fallback's `_is_meaningful` filter already prevents
        # zero / empty values from polluting `_stale_fields`, so the
        # mere existence of any entry there is sufficient evidence
        # of past liveness. Live status overwrites this on the next
        # /api/hosts/one
        # response.
        host_status = "up"
    elif _is_host_unconfigured(
        active, _active_set, h,
        ping_enabled=ping_enabled,
        snmp_mapped=snmp_mapped,
        any_provider_enabled=any_provider_enabled,
    ):
        host_status = "unconfigured"
    else:
        host_status = "unknown"
    _ping_port_raw = (h.get("ping") or {}).get("port")
    _ping_port: Optional[int] = int(_ping_port_raw) if _ping_port_raw is not None else None
    return {
        "id": h["id"],
        "name": h["id"],
        "host": h["id"],
        # Empty label is INTENTIONAL post-frontend's
        # `hostDisplayName(h)` falls back to the asset inventory's
        # name when this is blank. The previous `or h["id"]` fallback
        # silently overrode the operator's "use asset name" intent on
        # every API response. Pass the literal stored value through.
        "label": h.get("label") or "",
        "beszel_name": h.get("beszel_name") or "",
        "pulse_name": h.get("pulse_name") or "",
        "ne_url": h.get("ne_url") or "",
        # SNMP target alias. Surfaced on the API row so
        # `providerStates(h)` and `hostHasAgent(h)` can decide whether
        # to render the SNMP chip + count this host as having an agent.
        "snmp_name": h.get("snmp_name") or "",
        # Webmin target alias. Surfaced on the API row so the SPA's
        # toolbar `hostsProviderState('webmin')` can gate on
        # "configured for Webmin" rather than "Webmin probe succeeded",
        # which would hide the chip during a hub outage and lose
        # visibility on the real failure. Aligns Webmin with the
        # other `<provider>_name` fields that have always been on the
        # row shape.
        "webmin_name": h.get("webmin_name") or "",
        # Per-host SNMP opt-in flag. The bug: the SPA's
        # SNMP chip iterators were gating on `h.snmp_name` alone, so a
        # host with snmp_name set but `snmp.enabled === false` STILL
        # rendered the SNMP chip on the Hosts page. The frontend gates
        # now read `snmp_enabled === true && snmp_name` per         # explicit opt-in contract.
        "snmp_enabled": bool((h.get("snmp") or {}).get("enabled", False)),
        "url": h.get("url") or "",
        "icon": h.get("icon") or "",
        # Dedicated probe target — surfaced on the API row so the SPA
        # can gate the host-drawer port-scan button on "address is
        # set". Empty value = port-scan disabled with helper toast
        # asking the operator to set it in Admin → Hosts.
        "address": h.get("address") or "",
        "providers": providers_hit or [],
        "status": host_status,
        # Raw per-provider status surfaced so the SPA's `providerStates(h)`
        # helper can mark a chip red when Beszel/Pulse self-reports
        # paused/down even if it returned data (otherwise the chip
        # stays green because the provider was technically "hit").
        "beszel_status": s.get("beszel_status") or "",
        "docker_node": (h["id"] if _is_swarm_node(h.get("id")) else ""),
        "platform": s.get("host_platform") or "",
        "os": s.get("host_os") or "",
        "kernel": s.get("host_kernel") or "",
        "arch": s.get("host_arch") or "",
        "agent": s.get("host_agent") or "",
        "cores": s.get("host_cores") or s.get("host_threads") or 0,
        "threads": s.get("host_threads") or 0,
        "cpu_model": s.get("host_cpu_model") or "",
        "cpu_percent": s.get("host_cpu_percent") or 0,
        "mem_percent": s.get("host_mem_percent") or 0,
        "disk_percent": s.get("host_disk_percent") or 0,
        "mem_used": s.get("host_mem_used") or 0,
        "mem_total": s.get("host_mem_total") or 0,
        "disk_used": s.get("host_disk_used") or 0,
        "disk_total": s.get("host_disk_total") or 0,
        "mounts": s.get("mounts") or [],
        # `network_ifaces` is set further down (the SNMP-extras-aware
        # site at the bottom of this dict literal) — keeping a second
        # identical key here would be a PyCharm "duplicate dict keys"
        # warning AND the second assignment silently wins regardless.
        "bandwidth": s.get("host_bandwidth") or 0,
        "containers": s.get("host_containers") or 0,
        "uptime_s": s.get("host_uptime_s") or 0,
        "boot_ts": s.get("host_boot_ts"),
        "beszel_id": s.get("beszel_id") or "",
        "beszel_updated": s.get("beszel_updated") or "",
        "pulse_kind": s.get("pulse_kind") or "",
        "pulse_vmid": s.get("pulse_vmid") or 0,
        "pulse_node": s.get("pulse_node") or "",
        "pulse_status": s.get("pulse_status") or "",
        "updates_pending": int(s.get("host_updates_pending") or 0),
        "updates_security": int(s.get("host_updates_security") or 0),
        "custom_number": h.get("custom_number"),
        # Asset-inventory snapshot — null when no match. Resolved
        # lazily here (vs. eagerly in the loop above) so each
        # _shape_host_api_row call is self-contained. The cache read
        # is fast (file → JSON) but the index build is O(N), so
        # repeated calls in /api/hosts/one/{id} fanouts pay it once
        # per call. If that becomes a hotspot we can stash the
        # index on the request via FastAPI Depends().
        "asset": _resolve_asset_for_host(h.get("custom_number")),
        # Per-host SSH-enabled flag (opt-in semantics post
        # migration 001). True only when the operator explicitly ticked
        # "Enable SSH for this host" in Admin → Hosts. The drawer's SSH
        # card + common-actions panel render only when this is true.
        "ssh_enabled": bool((h.get("ssh") or {}).get("enabled", False)),
        # Ping. `ping_enabled` is the per-host opt-in flag (the
        # SPA uses it to gate the latency chip + drawer chart). The
        # alive / RTT / loss values come from the merged provider
        # dict — empty when the sampler hasn't run yet OR ping isn't
        # enabled for this host. Booleans coerced safely so a
        # null-from-snapshot doesn't crash the spread.
        "ping_enabled": bool((h.get("ping") or {}).get("enabled", False)),
        # Per-host ping override values surfaced for the SPA's metricSource
        # tooltip — pre-fix the tooltip read "Ping probe (this host)" for
        # every ping-enabled host with no indication of which port /
        # transport was actually being probed. Empty / null = inherit
        # global default. Transport is one of `tcp` / `icmp` / null.
        "ping_port": _ping_port,
        "ping_transport": ((h.get("ping") or {}).get("transport") or None),
        # Resolved ping TARGET — what `logic.ping_sampler._probe_one`
        # actually feeds to `probe_ping`. Resolution chain mirrors
        # `logic.ping_sampler._curated_ping_hosts` EXACTLY: per-host
        # `ping.host` override → `ssh.fqdn` → `ssh.host` → curated
        # `url`'s hostname → curated `id` as last-resort fallback.
        # Pre-fix the chain skipped `ping.host` (highest-priority
        # override) AND the URL-hostname fallback, so the SPA tooltip
        # reported `h.id` (e.g. "ftth") on rows whose actual probe
        # target was the URL's hostname (e.g. "ftth.example.com")
        # parsed from the curated `url` field.
        "ping_target": _resolve_ping_target(h),
        "ping_alive": bool(s.get("host_ping_alive")) if s.get("host_ping_alive") is not None else None,
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "ping_rtt_ms": (float(_prtt) if (_prtt := s.get("host_ping_rtt_ms")) is not None else None),
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "ping_loss_pct": (float(_plp) if (_plp := s.get("host_ping_loss_pct")) is not None else None),
        # Load averages (node-exporter primary, Beszel agents emit
        # `la=[1m,5m,15m]` which `extract_stats` now also surfaces here
        # so the load-average chart works for Beszel-only hosts too).
        # Frontend only renders the row when any of the three is > 0.
        "load_1m": float(s.get("host_load_1m") or 0),
        "load_5m": float(s.get("host_load_5m") or 0),
        "load_15m": float(s.get("host_load_15m") or 0),
        # Per-sensor temperatures. `host_temperatures` is a
        # `{sensor: celsius}` dict from the Beszel agent's `stats.t`
        # (only present when the agent exposes thermal data — Pi has
        # `cpu_thermal`, Intel/AMD has `package_id_0`, NVMe has
        # `nvme_composite`). Hosts without thermal sensors get an
        # empty dict and the frontend chart card hides via the
        # length-gate. The whitelist on this row was the reason the
        # field was being silently dropped before — extract_stats
        # produced it but it never reached the SPA without this line.
        "host_temperatures": dict(s.get("host_temperatures") or {}),
        # Per-GPU stats — Beszel agents emit `stats.g` as a per-GPU
        # dict; `_flatten_gpus` normalises into a list of
        # `{index, name, vram_used_bytes, vram_total_bytes,
        # usage_percent, power_watts}`. Empty list when the host has
        # no discrete GPU; frontend GPU chart cards gate on
        # `host_gpus.length > 0`. Same drift class as
        # `host_temperatures` — extract_stats produced the field but
        # it was being silently dropped on the API boundary because
        # this whitelist didn't include it.
        "host_gpus": list(s.get("host_gpus") or []),
        # Service summary — Beszel agents that run with the
        # systemd extension emit a list of service objects. The
        # extractor normalises into `{total, failed, failed_names}`.
        # Hosts whose agent doesn't track services get
        # `{total: 0, failed: 0, failed_names: []}` and the drawer
        # badge gates on `services.total > 0` to hide cleanly.
        "services": (s.get("host_services") or {"total": 0, "failed": 0, "failed_names": []}),
        # DMI / hardware identity (node-exporter only — Linux /
        # FreeBSD with the DMI collector). Empty strings = no DMI.
        "dmi_vendor": (s.get("host_dmi_vendor") or ""),
        "dmi_product": (s.get("host_dmi_product") or ""),
        "dmi_serial": (s.get("host_dmi_serial") or ""),
        "dmi_bios_version": (s.get("host_dmi_bios_version") or ""),
        # SNMP vendor-specific fields. All of
        # these are populated by `extract_vendor_info` only when the
        # corresponding vendor MIB returned data — non-vendor hosts get
        # empty / None / 0 here and the frontend cards gate on the
        # presence of the field so they don't render empty. Without
        # this whitelist the fields were silently dropped on the API
        # boundary (same drift class as the host_temperatures fix
        # earlier in this row — extract_stats produced them but they
        # never reached the SPA).
        # Universal identity (Dell / Cisco / APC / Synology / printer):
        "host_model": s.get("host_model") or "",
        "host_serial": s.get("host_serial") or "",
        "host_firmware": s.get("host_firmware") or "",
        "host_health": s.get("host_health") or "",
        "host_contact": s.get("host_contact") or "",
        "host_location": s.get("host_location") or "",
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "host_temp_c": (float(_htc) if (_htc := s.get("host_temp_c")) is not None else None),
        "host_upgrade_status": s.get("host_upgrade_status") or "",
        # per-core CPU + UCD memory breakdown for the new
        # SNMP time-series charts. Empty list / 0 when the host
        # didn't return UCD or hrProcessorLoad walks; frontend gate
        # on length so non-SNMP hosts don't see the cards.
        "host_cpu_per_core": list(s.get("host_cpu_per_core") or []),
        "host_mem_buffers": int(s.get("host_mem_buffers") or 0),
        "host_mem_cached": int(s.get("host_mem_cached") or 0),
        "host_mem_free": int(s.get("host_mem_free") or 0),
        # APC PowerNet-MIB UPS. Present only when the
        # host responded to upsBasicIdentModel / upsBasicOutputStatus.
        "host_ups_status": s.get("host_ups_status") or "",
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "host_battery_percent": (float(_hbp) if (_hbp := s.get("host_battery_percent")) is not None else None),
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "host_battery_runtime_s": (int(_hbrs) if (_hbrs := s.get("host_battery_runtime_s")) is not None else None),
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "host_battery_temp_c": (float(_hbtc) if (_hbtc := s.get("host_battery_temp_c")) is not None else None),
        "host_battery_status": s.get("host_battery_status") or "",
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "host_load_percent": (float(_hlp) if (_hlp := s.get("host_load_percent")) is not None else None),
        # Printer-MIB. Empty list / 0 / "" → frontend cards hide.
        "printer_page_count": int(s.get("printer_page_count") or 0),
        "printer_supplies": list(s.get("printer_supplies") or []),
        "printer_console_msg": s.get("printer_console_msg") or "",
        # Dell server-health. Populated by
        # `extract_vendor_info` only when the SNMP probe walked back
        # non-empty DELL-RAC-MIB rows — non-Dell agents return empty
        # lists / 0 / "" and the SPA's "Server health" card render
        # gate hides cleanly. Same drift class as `host_temperatures`
        # / `host_gpus` above: extract_vendor_info populated these,
        # the snapshot fallback restored them, the SPA's
        # `CURATED_REFRESH_FIELDS` whitelist tracked them, but they
        # never reached the SPA without this explicit row entry —
        # which is exactly what the operator hit (the card never
        # rendered for their iDRAC host).
        "host_dell_fans": list(s.get("host_dell_fans") or []),
        "host_dell_temps": list(s.get("host_dell_temps") or []),
        "host_dell_psus": list(s.get("host_dell_psus") or []),
        "host_dell_voltages": list(s.get("host_dell_voltages") or []),
        "host_dell_amperages": list(s.get("host_dell_amperages") or []),
        "host_dell_phys_disks": list(s.get("host_dell_phys_disks") or []),
        "host_dell_virt_disks": list(s.get("host_dell_virt_disks") or []),
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "host_dell_power_watts": (float(_hdpw) if (_hdpw := s.get("host_dell_power_watts")) is not None else None),
        "host_bios_version": s.get("host_bios_version") or "",
        "host_bios_date": s.get("host_bios_date") or "",
        # Last-observed SNMP auto-detect result — captured from the
        # most recent successful probe's diagnostic. Drives the
        # "Auto-detect last result: <vendors>" hint below the Vendor
        # MIBs checkbox group in the Admin → Hosts editor so operators
        # can see what auto-detect picked before deciding whether to
        # set an explicit override. Empty list when the host has never
        # been probed successfully or no SNMP override is set.
        "host_snmp_active_vendors": list(s.get("host_snmp_active_vendors") or []),
        "host_snmp_active_vendors_source": s.get("host_snmp_active_vendors_source") or "",
        # Network interfaces — already populated by extract_interfaces;
        # added explicitly here so the SNMP path's rx_bytes / tx_bytes /
        # oper_status make it through to the SPA. node-exporter / Beszel
        # / Pulse populate the same field with name + mac + addrs and
        # those merge cleanly via _merge_best (the per-iface dict shape
        # is the same; SNMP just adds the extra rx/tx/oper keys).
        "network_ifaces": list(s.get("network_ifaces") or []),
        # Stale-marker bookkeeping. Populated by
        # apply_host_snapshot_fallback when a provider went down and we
        # filled missing host_* fields from the persisted snapshot.
        # SPA's isStale / isStaleField / staleAge helpers consult these
        # to dim the corresponding bars / fields and surface the
        # "Showing cached data" drawer banner. Empty list / 0 when
        # everything is live so the frontend's reconcile clears the
        # markers cleanly when a provider recovers.
        "_stale_fields": list(s.get("_stale_fields") or []),
        "_stale_ts": float(s.get("_stale_ts") or 0.0),
        # Per-provider sample row counts — populated by
        # `_provider_sample_counts(host_id)` from inside `_merge_one_host`
        # (per-host probe path only; bulk /api/hosts/list path leaves it
        # empty to keep that query cheap). Surfaces under the
        # "Updated Xs ago" subtitle in the host drawer's enabled-agents
        # chip strip — gives the user a snapshot of how much history
        # is available per provider for THIS host. {} when the per-host
        # probe hasn't run yet (cold-load `/api/hosts/list` skeleton).
        "provider_sample_counts": dict(s.get("provider_sample_counts") or {}),
        # Per-provider effective sampler interval (seconds) — third
        # subtitle line in the chip strip. Each value is the post-floor,
        # post-inherit-resolution cadence the sampler actually sleeps
        # for, so the operator sees "Every 5m" not "interval=0 (inherit)".
        "provider_sample_intervals": dict(s.get("provider_sample_intervals") or {}),
        # Permanent-fail tracking. All four fields are non-zero
        # only when the host_metrics_sampler has recorded consecutive
        # failures for this host. `sampling_paused: true` triggers the
        # frontend banner + table icon; the operator clears via POST
        # /api/hosts/{id}/resume-sampling.
        **_failure_state_for_host(h["id"]),
        # Per-provider auto-pause state. Populated only when one
        # or more providers (currently SNMP + Webmin) have a failure-
        # state row keyed `<provider>:<host_id>`. Empty dict for healthy
        # hosts. SPA reads this to render the Paused badge on the
        # provider chip + the Resume button. Operator clears via POST
        # /api/hosts/{id}/provider/{name}/resume.
        "provider_pause_state": _provider_pause_state_for_host(h["id"]),
        # Port-scan provider — latest-scan rollup. Populated by
        # `_merge_one_host` after reading `host_port_scans` for the
        # most recent scan_id; null/empty for hosts with no scan
        # history yet. SPA's host-drawer Port Scan card consumes
        # `detected_ports` for the open-ports chip strip, and
        # `last_port_scan_ts` for the "Scanned X ago" subtitle.
        # Without surfacing these here, the host row sees the fields
        # as undefined → the "No scans yet" message stuck on
        # forever even after a successful scan.
        "detected_ports": s.get("detected_ports") or [],
        "last_port_scan_ts": int(s.get("last_port_scan_ts") or 0),
        # Drift-from-baseline classification. Populated by
        # `_merge_one_host` from logic.host_baseline.host_drift_for_api,
        # keyed by metric (cpu_pct / mem_pct / disk_pct / ping_rtt_ms).
        # Each value carries `indicator` (▲/▼/━), `value` (live), and
        # the cached `median` / `iqr` / `sample_count` / `computed_ts`.
        # Empty dict when no baseline exists for this host yet (<50
        # samples in window OR sampler hasn't run yet) — SPA hides
        # the chip in that case. `_merge_one_host` is the only writer
        # and it always stamps a dict, so no defensive coerce needed.
        "drift": s.get("drift") or {},
        # HTTP / TLS / DNS probe — seventh host-stats provider. Per-host
        # opt-in flag + the resolved URL list are surfaced so the SPA's
        # `providerStates(h)` helper can decide whether to render the
        # http_probe chip + `_hostsConfiguredForProvider('http_probe')`
        # gates the toolbar filter visibility. The probe-result fields
        # (`host_http_*`) are stamped by `populate_host_http_merge` AND
        # surfaced here so the drawer card has the data without a
        # parallel lookup.
        "http_probe_enabled": bool((h.get("http_probe") or {}).get("enabled", False)),
        "http_probe_urls": list((h.get("http_probe") or {}).get("urls") or []),
        # Resolved boolean: does this host have ANY URL source the
        # sampler would actually probe? Mirrors `host_http_sampler`'s
        # URL-resolution chain (http_probe.urls → row.url → row.services[].url)
        # so the SPA's `providerStates(h)` chip-gate can hide the
        # http_probe chip cleanly when the operator enabled the toggle
        # but didn't supply any URLs. Computed backend-side because the
        # API row's `services` field carries the Beszel systemd-rollup
        # OBJECT (see L11377 above), NOT the curated services array
        # (`hosts_config[].services`) — `Array.isArray(h.services)` is
        # always false on the SPA, so the chip-gate can't check the
        # third URL source itself.
        "http_probe_has_targets": (
            bool((h.get("http_probe") or {}).get("urls"))
            or bool((h.get("url") or "").strip())
            or any(
            isinstance(svc, dict) and (svc.get("url") or "").strip()
            for svc in (h.get("services") if isinstance(h.get("services"), list) else [])
        )
        ),
        # Latest sample roll-up. Renders the drawer card + the chip
        # state. None / empty when no sample has landed yet (cold-load
        # before the first tick); the snapshot fallback restores these
        # via `_stale_fields` markers if available.
        "host_http_status_ok": s.get("host_http_status_ok"),
        "host_http_status_code": s.get("host_http_status_code"),
        "host_http_content_match_ok": s.get("host_http_content_match_ok"),
        "host_http_tls_expires_in_days": s.get("host_http_tls_expires_in_days"),
        "host_http_tls_subject": s.get("host_http_tls_subject") or "",
        "host_http_tls_issuer": s.get("host_http_tls_issuer") or "",
        "host_http_dns_resolved": s.get("host_http_dns_resolved"),
        "host_http_dns_error": s.get("host_http_dns_error") or "",
        "host_http_latency_ms": s.get("host_http_latency_ms"),
        "host_http_error": s.get("host_http_error") or "",
        "host_http_url_count_total": int(s.get("host_http_url_count_total") or 0),
        "host_http_url_count_ok": int(s.get("host_http_url_count_ok") or 0),
        "host_http_urls": list(s.get("host_http_urls") or []),
        "host_http_ts": int(s.get("host_http_ts") or 0),
        # Apps feature — curated per-host service chip array surfaced
        # on the API row so the host drawer's Apps sub-tab + chip strip
        # can render them. Each entry carries `name / url / icon /
        # catalog_id / probe / service_idx` plus a stamped `last_probe`
        # block when a service_sampler tick has produced data for that
        # chip. `service_idx` is the position in the source array; the
        # manual probe endpoint (`POST /api/services/{host_id}/{idx}/
        # probe`) keys off it. Empty list = host has no chips pinned.
        "apps": _shape_host_apps(h),
    }
