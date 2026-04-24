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
import asyncio
import json
import os
import sqlite3
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).
from dotenv import load_dotenv
load_dotenv(os.getenv("ENV_FILE_PATH", "/app/.env"), override=False)

# Install the stdout/stderr tee as early as possible so uvicorn's own
# startup lines land in the in-memory buffer that powers Admin → Logs.
# Tee is idempotent + passthrough — Docker logs still see everything.
from logic import logs as _logs  # noqa: E402
_logs.install()

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from logic import auth, backups, metrics, oidc, schedules
from pydantic import BaseModel

# ============================================================================
# Version
# ----------------------------------------------------------------------------
# Source of truth is `version.txt` in the repo root (and in the bind-mount
# at /app/version.txt in production). The Forgejo Actions deploy pipeline
# rewrites PATCH to the workflow run_number before rsync, so every deploy
# gets a unique monotonically-increasing PATCH without manual bumps.
# Operator controls MAJOR.MINOR by hand-editing version.txt; PATCH is CI-
# managed and will be overwritten on every successful push to main.
# Rendered in the UI footer and returned by /api/version.
# ============================================================================
from logic.version import APP_VERSION, read_version

# ============================================================================
# Config
# ============================================================================
# Portainer connection config is DB-backed / UI-managed — see
# logic.portainer.get_portainer_settings(). The module still exposes
# PORTAINER_URL etc. as read-through module attributes for legacy call
# sites, so no other file needs to change. Concurrency tunables stay
# env-only.
from logic.portainer import (  # noqa: E402
    REGISTRY_CONCURRENCY as CONCURRENCY,
    STATS_CONCURRENCY,
)
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "900"))
STATS_CACHE_TTL = int(os.getenv("STATS_CACHE_TTL_SECONDS", "30"))
from logic import db as _db  # noqa: E402
from logic.db import DB_PATH, db_conn, get_setting, set_setting  # noqa: E402,F401
DOCKERHUB_USER = os.getenv("DOCKERHUB_USER", "")
DOCKERHUB_TOKEN = os.getenv("DOCKERHUB_TOKEN", "")
STATS_HISTORY_DAYS = int(os.getenv("STATS_HISTORY_DAYS", "7"))
STATS_SAMPLE_INTERVAL = int(os.getenv("STATS_SAMPLE_INTERVAL_SECONDS", "300"))  # 5 min

# Bootstrap-only env vars for seeding the first admin. Only consulted when
# the users table is empty at startup — safe to leave set or unset afterward.
BOOTSTRAP_ADMIN_USER = os.getenv("BOOTSTRAP_ADMIN_USER", "")
BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")

@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Lifespan-managed startup — per the single-replica rule in CLAUDE.md,
    # long-running workers live here so they stay at one-per-process.
    from logic import db as _db_mod
    if _db_mod.DB_PATH_ERROR:
        # Keep the app alive so the config-error middleware can serve a
        # readable diagnostic page. Skip every DB-dependent boot step and
        # every background worker — they'd all fail on the same missing
        # value. Operators see the error in the browser instead of a
        # crash-loop in `docker service ps`.
        print(f"[boot] CONFIG ERROR: {_db_mod.DB_PATH_ERROR}")
        print("[boot] Skipping DB init, schedulers, and samplers until "
              "DB_PATH is set. The app is serving the config-error page.")
        yield
        return
    init_db()
    warn = auth.auto_secret_warning()
    if warn:
        print(warn)
    # Seed DB-backed auth + Portainer settings on first boot. No-op for
    # keys that already exist in the `settings` table — the UI is
    # authoritative after first deploy. Portainer seeding also consults
    # PORTAINER_* env vars as a one-time transitional aid for existing
    # deploys migrating to the UI-managed model.
    from logic import portainer as _portainer
    with db_conn() as c:
        auth.bootstrap_auth_settings(c)
        _portainer.bootstrap_portainer_settings(c)
    _bootstrap_admin_if_needed()
    # Create /app/data/backups/ + /app/data/avatars/ if missing so endpoint
    # handlers don't each have to guard for first-boot state.
    backups.ensure_dirs()
    # Seed the schedules table with reasonable defaults on first boot.
    # Fleet-cache refresh is enabled; prune-node is disabled-by-default.
    # Pull the current node list (may be empty on a brand-new install —
    # seed_default_schedules handles the empty case).
    try:
        with db_conn() as c:
            node_names = sorted(set((_cache.get("nodes") or {}).values()))
            schedules.seed_default_schedules(c, node_names)
    except Exception as e:
        print(f"[scheduler] seed_default_schedules failed: {e}")
    sampler = asyncio.create_task(_stats_sampler_loop(), name="stats-sampler")
    scheduler = asyncio.create_task(schedules.scheduler_loop(), name="scheduler")
    # Net-I/O fallback sampler — scrapes node-exporter directly for any
    # curated host with a ne_url and writes derived rx/tx rates into
    # host_net_samples. Lets the Hosts chart show real numbers when the
    # Beszel agent isn't configured with NICS=<iface>.
    from logic import host_net_sampler as _host_net_sampler
    host_net_sampler = asyncio.create_task(
        _host_net_sampler.host_net_sampler_loop(), name="host-net-sampler",
    )
    try:
        yield
    finally:
        # Cancel in reverse-start order. Each cancel + await is wrapped so
        # one failing shutdown step can't starve the next one.
        for task in (host_net_sampler, scheduler, sampler):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[lifespan] shutdown error: {e}")


def _bootstrap_admin_if_needed() -> None:
    """Seed the first admin from env on empty databases.

    Runs once per process. If BOOTSTRAP_ADMIN_USER/PASSWORD are set and no
    users exist yet, creates that admin. Otherwise leaves the table empty and
    waits for the one-shot `/api/local-auth/bootstrap` endpoint.
    """
    if not (BOOTSTRAP_ADMIN_USER and BOOTSTRAP_ADMIN_PASSWORD):
        return
    with db_conn() as c:
        if auth.count_users(c) > 0:
            return
        auth.create_user(
            c, BOOTSTRAP_ADMIN_USER, None,
            BOOTSTRAP_ADMIN_PASSWORD, "admin", "local",
        )
    print(f"[auth] Seeded bootstrap admin '{BOOTSTRAP_ADMIN_USER}'. "
          "Change password after first login.")


app = FastAPI(title="OmniGrid", lifespan=_lifespan)

# Observe-mode auth middleware (step 1 of the auth rollout). Populates
# request.state.user when an identity can be resolved; never rejects. Write
# routes and global enforcement gate on this in later steps.
# The lambda defers `db_conn` lookup: the function is defined later in this
# module (SQLite section) but the middleware body only runs at request time.
app.middleware("http")(auth.make_auth_middleware(lambda: db_conn()))


# Config-error guard. Registered AFTER auth so Starlette runs it FIRST on
# each request (middleware is a LIFO stack). When a required config value
# like DB_PATH is missing, we keep uvicorn up (no crash-loop) and return a
# readable diagnostic instead of a raw sqlite error on every route.
# /api/healthz is let through so the container healthcheck keeps passing;
# static assets pass too, so the HTML error page can render with styles.
_CONFIG_PASSTHROUGH_PREFIXES = (
    "/api/healthz", "/api/version", "/css/", "/js/", "/img/",
    "/i18n/", "/node_modules/", "/fonts/", "/icon-", "/favicon",
)

_CONFIG_ERROR_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>OmniGrid — configuration error</title>
<link rel="stylesheet" href="/css/style.css">
</head><body class="login">
<div class="login-wrap">
  <div class="login-card" style="max-width:520px">
    <h1>OmniGrid is not configured</h1>
    <p style="color:var(--text-dim);margin-top:var(--s-2)">{detail}</p>
    <p style="color:var(--text-faint);margin-top:var(--s-3);font-size:0.9em">
      Container is up and healthy; fix the config and redeploy (or force a
      service update). This page is served by a fail-safe middleware so the
      error is visible instead of hidden behind a crash-loop.
    </p>
  </div>
</div></body></html>
"""


@app.middleware("http")
async def _config_error_guard(request: Request, call_next):
    err = _db.DB_PATH_ERROR
    if not err:
        return await call_next(request)
    path = request.url.path
    if any(path == p or path.startswith(p) for p in _CONFIG_PASSTHROUGH_PREFIXES):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse(
            {"error": "config_missing", "detail": err},
            status_code=503,
        )
    return Response(
        content=_CONFIG_ERROR_HTML.format(detail=err),
        media_type="text/html",
        status_code=503,
    )

# Prometheus metric definitions moved to logic/metrics.py. The cache-age
# collector is wired below (once _cache exists), and every remaining
# metric call site in this file references them via `metrics.NAME`.
metrics.register_cache_age_collector(lambda: _cache)


# ============================================================================
# SQLite persistence — db_conn / get_setting / set_setting live in logic/db.py.
# init_db() stays here as the boot orchestrator: it creates the core tables
# (history / ignores / settings / stats_samples) and delegates to module
# schema hooks (auth.init_auth_schema) for module-owned tables.
# ============================================================================
def init_db():
    with db_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL, op_type TEXT NOT NULL,
            target_name TEXT, target_id TEXT,
            status TEXT NOT NULL, duration REAL,
            events TEXT, error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_history_ts ON history(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_history_op_type ON history(op_type);
        CREATE INDEX IF NOT EXISTS idx_history_target_name ON history(target_name);
        CREATE INDEX IF NOT EXISTS idx_history_status ON history(status);

        CREATE TABLE IF NOT EXISTS ignores (
            pattern TEXT PRIMARY KEY, kind TEXT NOT NULL,
            reason TEXT, created REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        );

        -- Per-item CPU/memory time-series for 24h sparklines + drift graphs.
        -- Written by the lifespan-managed stats sampler every
        -- STATS_SAMPLE_INTERVAL seconds; pruned to STATS_HISTORY_DAYS.
        CREATE TABLE IF NOT EXISTS stats_samples (
            ts REAL NOT NULL,
            item_id TEXT NOT NULL,
            cpu REAL, mem_used REAL, mem_limit REAL
        );
        CREATE INDEX IF NOT EXISTS idx_stats_samples_item_ts
            ON stats_samples(item_id, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_stats_samples_ts
            ON stats_samples(ts);

        -- Net-I/O fallback series per curated host. Populated by
        -- logic/host_net_sampler.py when node-exporter is the only
        -- network-counter source (Beszel agents with NICS= unset emit
        -- all-zero nr/ns, which is what `isNetSeriesFlat` detects on
        -- the frontend). Rates are pre-computed across consecutive NE
        -- probes; counter jumps / rollovers are SKIPPED rather than
        -- recorded as synthesized zeros — see
        -- logic.host_net_sampler._sanity_bounds().
        CREATE TABLE IF NOT EXISTS host_net_samples (
            ts INTEGER NOT NULL,
            host_id TEXT NOT NULL,
            rx_bytes_per_s REAL NOT NULL,
            tx_bytes_per_s REAL NOT NULL,
            PRIMARY KEY (host_id, ts)
        );
        CREATE INDEX IF NOT EXISTS idx_host_net_samples_host_ts
            ON host_net_samples(host_id, ts DESC);
        """)
        # Idempotent column additions for existing deployments. SQLite pre-3.35
        # has no "ADD COLUMN IF NOT EXISTS", so we catch the OperationalError
        # that gets raised when the column already exists. Safe to re-run on
        # every boot.
        for ddl in (
            "ALTER TABLE history ADD COLUMN actor TEXT DEFAULT 'ui'",
            "ALTER TABLE history ADD COLUMN target_stack TEXT",
        ):
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_target_stack "
            "ON history(target_stack)"
        )
        # Auth schema — users / sessions / api_tokens. Owned by auth.py but
        # created here so there's a single init_db() entry point.
        auth.init_auth_schema(c)
        # Scheduler schema — admin-defined recurring jobs. Same pattern:
        # owned by logic/schedules.py, created here.
        schedules.init_schedules_schema(c)


# ============================================================================
# Portainer client moved to logic/portainer.py. Local aliases keep the old
# underscore-prefixed names as call-site shortcuts so the rest of this
# file reads unchanged. _node_for_container binds _cache through a thin
# wrapper since portainer.node_for_container takes the cache dict.
# ============================================================================
from logic import portainer  # noqa: E402
_headers = portainer.headers
_pg = portainer.pg


def _node_for_container(container_id: str) -> Optional[str]:
    return portainer.node_for_container(_cache, container_id)


# ============================================================================
# Registry digest checking moved to logic/registry.py. Local aliases keep
# the old underscore-prefixed names as call-site shortcuts so the rest of
# the file reads unchanged.
# ============================================================================
from logic import registry  # noqa: E402
_parse_image_ref = registry.parse_image_ref
_hub_link = registry.hub_link
_tag_of = registry.tag_of
_get_remote_digest = registry.get_remote_digest


# ============================================================================
# Data aggregation moved to logic/gather.py. Local aliases keep the old
# names (`_cache`, `_gather`, `_tag_of`, `_node_matches`) available to the
# rest of this file — `_cache` is the one other logic modules also need,
# so it's re-exported below with the cache-age collector wiring.
# ============================================================================
from logic import gather as _gather_mod  # noqa: E402
_cache = _gather_mod.get_cache()
_gather = _gather_mod.gather
_tag_of = registry.tag_of
_node_attr = _gather_mod._node_attr
_node_matches = _gather_mod._node_matches


# ============================================================================
# Operations moved to logic/ops.py. Shim aliases for the class + module
# state + every _do_* handler so route code keeps reading unchanged.
# ============================================================================
from logic import ops as _ops_mod  # noqa: E402
Operation = _ops_mod.Operation
ops = _ops_mod.ops
ops_order = _ops_mod.ops_order
new_op = _ops_mod.new_op
persist_history = _ops_mod.persist_history
notify = _ops_mod.notify
_do_update_stack = _ops_mod.do_update_stack
_do_update_container = _ops_mod.do_update_container
_do_restart_service = _ops_mod.do_restart_service
_do_restart_container = _ops_mod.do_restart_container
_do_remove_container = _ops_mod.do_remove_container
_do_prune_node = _ops_mod.do_prune_node


# ============================================================================
# Stats moved to logic/stats.py. Shim aliases so existing routes + lifespan
# keep working unchanged.
# ============================================================================
from logic import stats as _stats_mod  # noqa: E402
_stats_cache = _stats_mod.get_stats_cache()
_gather_stats = _stats_mod.gather_stats
_stats_history = _stats_mod.stats_history
_stats_sampler_loop = _stats_mod.stats_sampler_loop


# ============================================================================
# API endpoints
# ============================================================================
@app.get("/api/stats")
async def api_stats(force: bool = False):
    now = time.time()
    if force or not _stats_cache["stats"] or (now - _stats_cache["ts"] > STATS_CACHE_TTL):
        await _gather_stats()
    return {
        "stats": _stats_cache["stats"],
        "ts": _stats_cache["ts"],
        "age": int(now - _stats_cache["ts"]) if _stats_cache["ts"] else None,
    }


@app.get("/api/stats/history")
async def api_stats_history(item_id: str, hours: int = 24):
    """Return sparkline samples for one or more item IDs over the last N hours.

    `item_id` may be comma-separated to fetch multiple in one round-trip
    (the UI batches all visible stacks so it's not N requests per refresh).
    """
    hours = max(1, min(hours, STATS_HISTORY_DAYS * 24))
    ids = [s.strip() for s in item_id.split(",") if s.strip()]
    since = time.time() - hours * 3600
    return {
        "since": since,
        "hours": hours,
        "series": _stats_history(ids, since),
    }


@app.get("/api/items")
async def api_items(force: bool = False):
    now = time.time()
    if force or not _cache["items"] or (now - _cache["ts"] > CACHE_TTL):
        await _gather()
    return {
        "items": _cache["items"],
        "stacks": _cache["stacks"],
        "nodes": _cache["nodes"],
        # Capacity + uptime proxy per node — drives the Nodes view's
        # stat tiles. Keyed by hostname, matches _cache["nodes"]'s values.
        "nodes_info": _cache.get("nodes_info") or {},
        "cached": (now - _cache["ts"] > 1),
        "age": int(now - _cache["ts"]) if _cache["ts"] else None,
    }


@app.get("/api/item/{raw_id}")
async def api_item_detail(raw_id: str):
    for it in _cache["items"]:
        if it["raw_id"] == raw_id or it["raw_id"].startswith(raw_id):
            return it
    raise HTTPException(404, "Not found")


def _actor_from(request: Request) -> str:
    """Attribute an operation to a user.

    Priority:
      1. request.state.user.username — set by auth middleware from local
         session cookie, API bearer token, or verified Authentik header.
      2. X-Forwarded-User — legacy path for reverse proxies that stamp it
         directly (Authelia, oauth2-proxy, Traefik forward-auth).
      3. "ui" — dev mode / no-auth path (observe-mode only; step 2 gates
         write routes, so this should be unreachable on them post-step-2).

    Future: the scheduler will pass actor="system" explicitly.
    """
    user = getattr(request.state, "user", None)
    if user and getattr(user, "username", None):
        return user.username
    return (request.headers.get("x-forwarded-user") or "ui").strip() or "ui"


def _item_context(container_or_service_id: str) -> tuple[str, Optional[str]]:
    """Resolve (display_name, target_stack) for a cache item by raw or prefix id."""
    for it in _cache["items"]:
        rid = it.get("raw_id") or ""
        if rid.startswith(container_or_service_id) or container_or_service_id.startswith(rid):
            return (it.get("name") or container_or_service_id[:12], it.get("stack"))
    return (container_or_service_id[:12], None)


@app.post("/api/update/stack/{stack_id}")
async def api_update_stack(
    stack_id: int, bg: BackgroundTasks, request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    name = f"stack-{stack_id}"
    for s in _cache["stacks"]:
        if s.get("stack_id") == stack_id:
            name = s["name"]
            break
    op = new_op("update_stack", str(stack_id), name,
                target_stack=name, actor=_actor_from(request))
    bg.add_task(_do_update_stack, op, stack_id)
    return {"op_id": op.id}


@app.post("/api/update/container/{container_id}")
async def api_update_container(
    container_id: str, bg: BackgroundTasks, request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    name, stack = _item_context(container_id)
    op = new_op("update_container", container_id, name,
                target_stack=stack, actor=_actor_from(request))
    bg.add_task(_do_update_container, op, container_id)
    return {"op_id": op.id}


@app.post("/api/restart/service/{service_id}")
async def api_restart_service(
    service_id: str, bg: BackgroundTasks, request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    name, stack = _item_context(service_id)
    op = new_op("restart_service", service_id, name,
                target_stack=stack, actor=_actor_from(request))
    bg.add_task(_do_restart_service, op, service_id)
    return {"op_id": op.id}


@app.post("/api/restart/container/{container_id}")
async def api_restart_container(
    container_id: str, bg: BackgroundTasks, request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    name, stack = _item_context(container_id)
    op = new_op("restart_container", container_id, name,
                target_stack=stack, actor=_actor_from(request))
    bg.add_task(_do_restart_container, op, container_id)
    return {"op_id": op.id}


@app.post("/api/remove/container/{container_id}")
async def api_remove_container(
    container_id: str, bg: BackgroundTasks, request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    name, stack = _item_context(container_id)
    op = new_op("remove_container", container_id, name,
                target_stack=stack, actor=_actor_from(request))
    bg.add_task(_do_remove_container, op, container_id)
    return {"op_id": op.id}


@app.post("/api/prune/node/{hostname}")
async def api_prune_node(
    hostname: str, bg: BackgroundTasks, request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    """Run a Docker-system-prune equivalent on a specific Swarm node.

    Matches `docker system prune -f --volumes` — stopped containers,
    dangling images, unused networks + volumes, build cache. Same model
    as the existing update/restart ops: kicks off a BackgroundTask,
    returns the op id, UI polls /api/ops for progress. Admin-only.
    """
    # Light sanity on the hostname so we don't send garbage through to
    # Portainer's agent-target header. node_for_container validates against
    # the cache; do the same for explicit hostnames.
    known = set(_cache.get("nodes", {}).values())
    if known and hostname not in known:
        raise HTTPException(status_code=400, detail=f"Unknown node: {hostname}")
    op = new_op(
        "prune_node", hostname, hostname,
        target_stack=None, actor=_actor_from(request),
    )
    bg.add_task(_do_prune_node, op, hostname)
    return {"op_id": op.id}


@app.get("/api/ops")
async def api_ops():
    return {"ops": [ops[oid].to_dict() for oid in ops_order if oid in ops]}


@app.get("/api/ops/{op_id}")
async def api_op(op_id: str):
    op = ops.get(op_id)
    if not op:
        raise HTTPException(404, "Op not found")
    return op.to_dict()


def _history_query(
    stack: Optional[str], op_type: Optional[str], status: Optional[str],
    actor: Optional[str], q: Optional[str],
    since: Optional[float], until: Optional[float],
    limit: int,
):
    """Shared builder for filterable history queries. All filters are
    optional; missing ones degrade gracefully to an unfiltered scan."""
    where, params = [], []
    if stack:
        # Match ops whose recorded target_stack is this stack, plus historical
        # rows (pre-column) where target_name happens to equal it.
        where.append("(target_stack = ? OR target_name = ?)")
        params.extend([stack, stack])
    if op_type:
        where.append("op_type = ?")
        params.append(op_type)
    if status:
        where.append("status = ?")
        params.append(status)
    if actor:
        where.append("actor = ?")
        params.append(actor)
    if q:
        like = f"%{q}%"
        where.append("(target_name LIKE ? OR target_id LIKE ? OR error LIKE ?)")
        params.extend([like, like, like])
    if since is not None:
        where.append("ts >= ?")
        params.append(since)
    if until is not None:
        where.append("ts <= ?")
        params.append(until)
    sql = "SELECT * FROM history"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(max(1, min(limit, 5000)))
    with db_conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/history")
async def api_history(
    limit: int = 100,
    stack: Optional[str] = None,
    op_type: Optional[str] = None,
    status: Optional[str] = None,
    actor: Optional[str] = None,
    q: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
):
    return {
        "history": _history_query(stack, op_type, status, actor, q, since, until, limit),
    }


@app.get("/api/history.json")
async def api_history_json_export(
    limit: int = 5000,
    stack: Optional[str] = None,
    op_type: Optional[str] = None,
    status: Optional[str] = None,
    actor: Optional[str] = None,
    q: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
):
    rows = _history_query(stack, op_type, status, actor, q, since, until, limit)
    return Response(
        content=json.dumps(rows, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="omnigrid-history.json"'},
    )


@app.get("/api/history.csv")
async def api_history_csv_export(
    limit: int = 5000,
    stack: Optional[str] = None,
    op_type: Optional[str] = None,
    status: Optional[str] = None,
    actor: Optional[str] = None,
    q: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
):
    import csv
    import io

    rows = _history_query(stack, op_type, status, actor, q, since, until, limit)
    # Fixed column order — stable for spreadsheet pivots. `events` is
    # omitted from CSV (multi-line JSON doesn't round-trip cleanly); users
    # needing full event logs should export JSON.
    cols = ["ts", "op_type", "status", "actor", "target_stack",
            "target_name", "target_id", "duration", "error"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c, "") for c in cols])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="omnigrid-history.csv"'},
    )


@app.delete("/api/history")
async def api_history_clear(_admin: auth.User = Depends(auth.require_admin)):
    with db_conn() as c:
        c.execute("DELETE FROM history")
    return {"status": "cleared"}


class IgnoreIn(BaseModel):
    pattern: str
    kind: str
    reason: Optional[str] = ""


@app.get("/api/ignores")
async def api_ignores():
    with db_conn() as c:
        rows = c.execute("SELECT * FROM ignores ORDER BY created DESC").fetchall()
    return {"ignores": [dict(r) for r in rows]}


@app.post("/api/ignores")
async def api_add_ignore(
    ig: IgnoreIn,
    _admin: auth.User = Depends(auth.require_admin),
):
    with db_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO ignores(pattern,kind,reason,created) VALUES (?,?,?,?)",
            (ig.pattern, ig.kind, ig.reason or "", time.time()),
        )
    _cache["ts"] = 0
    return {"status": "ok"}


@app.delete("/api/ignores/{pattern:path}")
async def api_del_ignore(
    pattern: str,
    _admin: auth.User = Depends(auth.require_admin),
):
    with db_conn() as c:
        c.execute("DELETE FROM ignores WHERE pattern=?", (pattern,))
    _cache["ts"] = 0
    return {"status": "ok"}


class SettingsIn(BaseModel):
    apprise_url: Optional[str] = None
    apprise_tag: Optional[str] = None
    portainer_public_url: Optional[str] = None
    # Portainer connection (DB-backed, UI-managed). API key follows the
    # write-only / "keep current if blank" contract: the browser never
    # receives the current value, only whether it's set. Pass a non-
    # empty string to overwrite.
    portainer_url: Optional[str] = None
    portainer_api_key: Optional[str] = None
    portainer_endpoint_id: Optional[int] = None
    portainer_verify_tls: Optional[bool] = None
    # OIDC provider settings (DB-backed, UI-managed). Client secret uses
    # the same keep-current-if-blank contract as portainer_api_key.
    oidc_enabled: Optional[bool] = None
    oidc_issuer_url: Optional[str] = None
    oidc_client_id: Optional[str] = None
    oidc_client_secret: Optional[str] = None
    oidc_redirect_uri: Optional[str] = None
    oidc_scopes: Optional[str] = None
    oidc_admin_group: Optional[str] = None
    oidc_verify_tls: Optional[bool] = None
    # Backup retention: keep the N newest .zip files in /app/data/backups;
    # 0 disables retention (keep everything). Applied after every successful
    # create, whether user-triggered or scheduled.
    backup_retention_count: Optional[int] = None
    # Host-stats integration via node-exporter. When enabled, OmniGrid
    # scrapes each node's /metrics endpoint during gather to surface real
    # host disk / memory / uptime (vs. the Docker-only numbers Portainer
    # exposes). URL template uses {host} → Docker hostname; default
    # http://{host}:9100/metrics works for a typical Swarm global-mode
    # node-exporter deploy.
    node_exporter_enabled: Optional[bool] = None
    node_exporter_url_template: Optional[str] = None
    # Per-hostname URL overrides for nodes where the default template's
    # {host} substitution doesn't resolve (e.g. a node whose Docker
    # hostname isn't reachable via DNS from the OmniGrid container).
    # Stored as a JSON object: {"hostname": "http://explicit:9100/metrics"}.
    node_exporter_overrides: Optional[dict] = None
    # Host-stats source selector — mutually exclusive. "none" disables
    # host-stats entirely, "node_exporter" uses the scrape path, and
    # "beszel" consumes a Beszel Hub's PocketBase API. Kept alongside
    # the per-source settings rather than auto-inferred so an operator
    # can temporarily flip sources without blanking their config.
    host_stats_source: Optional[str] = None
    # Beszel Hub — URL, identity (usually email), password. Password
    # is write-only on the wire like the other secret fields (empty
    # string "keep current", non-empty "replace").
    beszel_hub_url: Optional[str] = None
    beszel_identity: Optional[str] = None
    beszel_password: Optional[str] = None
    beszel_verify_tls: Optional[bool] = None
    # Per-node name aliases — Docker hostname → Beszel system name. Use
    # when the name the operator gave a system in Beszel doesn't match
    # the Docker Swarm hostname. Example:
    #   {"debian13docker": "docker.home.lan"}
    # Nodes not listed here fall back to identity mapping.
    beszel_aliases: Optional[dict] = None
    # Pulse (rcourtman/Pulse) — third host-stats provider. PVE-only.
    # Token is write-only on the wire like beszel_password.
    pulse_url: Optional[str] = None
    pulse_token: Optional[str] = None
    pulse_verify_tls: Optional[bool] = None
    # Docker hostname → Pulse node name. Separate from beszel_aliases
    # because Pulse uses PVE node names (``pve-1``, ``dockerpve``) which
    # tend to differ from Beszel hostnames.
    pulse_aliases: Optional[dict] = None
    # Webmin — fourth host-stats provider. Each target host runs its
    # own Miniserv instance so the probe URL is per-host, not a hub.
    # ``webmin_aliases`` maps Docker hostname → full Miniserv base URL
    # (e.g. ``{"debian13docker": "https://docker.home.lan:10000"}``).
    # ``webmin_url`` is retained as an optional default/template for
    # future use. Password is write-only like every other secret.
    webmin_url: Optional[str] = None
    webmin_user: Optional[str] = None
    webmin_password: Optional[str] = None
    webmin_verify_tls: Optional[bool] = None
    webmin_aliases: Optional[dict] = None
    # Scheduler timezone — IANA name (e.g. "Africa/Cairo"). When set,
    # daily/weekly/monthly schedule anchors are computed in THIS zone
    # instead of the container's localtime. Containers default to UTC;
    # operators in other zones would otherwise see "Daily @ 01:00" fire
    # at the wrong wall-clock moment. Blank = container-local (legacy).
    scheduler_timezone: Optional[str] = None
    # Topbar widgets — lightweight decorative info in the header.
    # ``weather_label`` is what the UI renders alongside the temp
    # ("Cairo"); lat/lon feed Open-Meteo (no API key required). Clock
    # is client-side only — no persistence needed beyond a show/hide.
    weather_label: Optional[str] = None
    weather_lat: Optional[float] = None
    weather_lon: Optional[float] = None
    show_header_clock: Optional[bool] = None
    show_header_weather: Optional[bool] = None
    # Open-Meteo upstream — blank uses the public endpoint; admins
    # can point at a self-hosted instance without touching .env.
    open_meteo_url: Optional[str] = None
    # Host grouping — JSON array of {name, range_start, range_end, order}
    # that buckets curated hosts into collapsible sections in the Hosts
    # view by their custom_number. Operator-managed under Admin → Hosts.
    host_groups: Optional[list] = None
    # Asset inventory V1 — OAuth2 client_credentials against oufa.co.
    # Secret is write-only (see api_set_settings keep-if-blank rule);
    # admin clears via clear_asset_inventory_client_secret flag.
    asset_inventory_base_url: Optional[str] = None
    asset_inventory_token_url: Optional[str] = None
    asset_inventory_client_id: Optional[str] = None
    asset_inventory_client_secret: Optional[str] = None
    asset_inventory_scope: Optional[str] = None
    clear_asset_inventory_client_secret: Optional[bool] = None
    # Auth mode selector: "oauth2" (existing client_credentials flow)
    # or "lifetime_token" (static key POSTed to services.php with
    # X-Authorization header). Lifetime key follows the secret suffix
    # + `_set` flag + `clear_*` contract like every other write-only
    # secret (see CLAUDE.md "Secrets in the settings table follow a
    # naming convention").
    asset_inventory_auth_mode: Optional[str] = None
    asset_inventory_lifetime_token: Optional[str] = None
    clear_asset_inventory_lifetime_token: Optional[bool] = None
    # Mandatory `service` and `action` form parameters for the
    # lifetime-token flavour. oufa.co's services.php routes by these
    # ("service=scheduler&action=run_schedule" is the documented pair
    # for asset fetch). Plain text — these are routing keys, not
    # credentials.
    asset_inventory_service: Optional[str] = None
    asset_inventory_action: Optional[str] = None
    # Range bounds for the `get_assets_custom_number_range` action.
    # String-typed so "" can round-trip as "clear the bound"; field
    # omitted means "don't touch". Pagination kicks in when both are
    # supplied AND the action matches — see
    # logic.asset_inventory.fetch_assets_lifetime_token.
    asset_inventory_min_value: Optional[str] = None
    asset_inventory_max_value: Optional[str] = None
    # -----------------------------------------------------------------
    # SSH console — admin-only remote command runner wired into the
    # host drawer. Global defaults; per-host overrides live in
    # ``hosts_config[].ssh`` (user / port / disabled). Secret fields
    # follow the suffix + ``_set`` flag convention — the browser only
    # learns whether they're set, never the material. See logic/ssh.py.
    # -----------------------------------------------------------------
    ssh_default_user: Optional[str] = None
    ssh_default_port: Optional[int] = None
    ssh_default_private_key: Optional[str] = None
    ssh_default_private_key_passphrase: Optional[str] = None
    # Password auth as an alternative to private key. When both are
    # set, the key wins. Allows operators on hosts that only accept
    # password auth (routers / NAS boxes / vanilla VM images) to still
    # use the SSH console. Write-only on the wire via `_set` flag.
    ssh_default_password: Optional[str] = None
    # FQDN suffix appended to bare hostnames (hosts_config[].id) when
    # SSH resolves the target. Example: id="webserver" +
    # ssh_fqdn_suffix=".home.lan" → "webserver.home.lan". Host IDs that
    # already contain a dot are used as-is. Blank = no suffix.
    ssh_fqdn_suffix: Optional[str] = None
    ssh_default_known_hosts: Optional[str] = None
    ssh_destructive_patterns: Optional[str] = None
    # Explicit CLEAR flags for SSH secrets. The keep-current-if-blank
    # contract (used by all other secrets) makes it impossible to
    # ERASE a stored secret — blank means "don't change". These bool
    # flags are the escape hatch: when true, the corresponding secret
    # is deleted from the settings table regardless of the paired
    # string field. Admin UI surfaces them as "Clear" buttons.
    clear_ssh_private_key: Optional[bool] = None
    clear_ssh_passphrase: Optional[bool] = None
    clear_ssh_password: Optional[bool] = None
    # JSON array of SSH custom actions. Each element:
    #   {"id": "restart-beszel", "title": "Restart Beszel agent",
    #    "command": "systemctl restart beszel-agent"}
    # Empty array or missing = fall back to the hardcoded default
    # action list in the drawer (same 5 presets). {host} placeholder
    # in the command template is substituted at run time.
    ssh_custom_actions: Optional[list] = None


@app.get("/api/settings")
async def api_get_settings(request: Request):
    from logic import portainer as _portainer
    with db_conn() as c:
        a = auth.get_auth_settings(c)
    p = _portainer.get_portainer_settings()
    return {
        "apprise_url": get_setting("apprise_url", ""),
        "apprise_tag": get_setting("apprise_tag", ""),
        # Open-Meteo upstream (Admin → Notifications). Returned in the
        # clear so the input round-trips and reloads persisted. Blank
        # disables the topbar weather widget (see _open_meteo_url).
        "open_meteo_url": get_setting("open_meteo_url", "") or "",
        # Host groups — returned as a parsed list of dicts. Admin →
        # Hosts editor round-trips this to build the group editor UI.
        "host_groups": (lambda raw: (
            json.loads(raw) if (raw or "").strip() else []
        ))(get_setting("host_groups", "") or ""),
        # Asset inventory (oufa.co). Secret is write-only — UI sees
        # a `_set` flag only. Other fields round-trip in the clear.
        "asset_inventory": {
            "auth_mode":          (get_setting("asset_inventory_auth_mode", "") or "oauth2"),
            "base_url":           get_setting("asset_inventory_base_url", "") or "",
            "token_url":          get_setting("asset_inventory_token_url", "") or "",
            "client_id":          get_setting("asset_inventory_client_id", "") or "",
            "client_secret_set":  bool(get_setting("asset_inventory_client_secret", "")),
            "scope":              get_setting("asset_inventory_scope", "") or "",
            "lifetime_token_set": bool(get_setting("asset_inventory_lifetime_token", "")),
            "service":            get_setting("asset_inventory_service", "") or "",
            "action":             get_setting("asset_inventory_action", "") or "",
            "min_value":          (lambda v: int(v) if (v or "").strip().lstrip("-").isdigit() else None)(
                                     get_setting("asset_inventory_min_value", "")),
            "max_value":          (lambda v: int(v) if (v or "").strip().lstrip("-").isdigit() else None)(
                                     get_setting("asset_inventory_max_value", "")),
        },
        "portainer_public_url": get_setting("portainer_public_url", str(p.get("portainer_url") or "")),
        "backup_retention_count": int(get_setting("backup_retention_count", "0") or "0"),
        "scheduler_timezone": get_setting("scheduler_timezone", "") or "",
        "node_exporter": {
            "enabled": (get_setting("node_exporter_enabled", "false") or "false").lower() == "true",
            "url_template": get_setting("node_exporter_url_template", "http://{host}:9100/metrics"),
            "overrides": json.loads(get_setting("node_exporter_overrides", "{}") or "{}"),
        },
        # Host-stats sources — stored as CSV so multiple providers can
        # be enabled at once (Beszel for cross-platform real-time stats
        # + node-exporter for Linux-host detail). The read-back shim
        # keeps single-value legacy rows ("beszel" / "node_exporter")
        # working without a migration; upgrades with only
        # ``node_exporter_enabled`` set default to that one source.
        "host_stats_source": (
            get_setting("host_stats_source", "")
            or ("node_exporter"
                if (get_setting("node_exporter_enabled", "false") or "false").lower() == "true"
                else "none")
        ),
        "host_stats_sources": [
            s.strip() for s in (
                get_setting("host_stats_source", "")
                or ("node_exporter"
                    if (get_setting("node_exporter_enabled", "false") or "false").lower() == "true"
                    else "")
            ).split(",") if s.strip() and s.strip().lower() != "none"
        ],
        # Beszel Hub — password is write-only. UI only learns "is it set".
        "beszel": {
            "hub_url": get_setting("beszel_hub_url", ""),
            "identity": get_setting("beszel_identity", ""),
            "password_set": bool(get_setting("beszel_password", "")),
            "verify_tls": (get_setting("beszel_verify_tls", "true") or "true").lower() == "true",
            "aliases": json.loads(get_setting("beszel_aliases", "{}") or "{}"),
        },
        # Pulse — token is write-only like Beszel's password.
        "pulse": {
            "url": get_setting("pulse_url", ""),
            "token_set": bool(get_setting("pulse_token", "")),
            "verify_tls": (get_setting("pulse_verify_tls", "true") or "true").lower() == "true",
            "aliases": json.loads(get_setting("pulse_aliases", "{}") or "{}"),
        },
        # Webmin — password is write-only (same _set-flag convention as
        # beszel_password / pulse_token / portainer_api_key). ``aliases``
        # is Docker hostname → Miniserv base URL per host.
        "webmin": {
            "url": get_setting("webmin_url", ""),
            "user": get_setting("webmin_user", ""),
            "password_set": bool(get_setting("webmin_password", "")),
            "verify_tls": (get_setting("webmin_verify_tls", "false") or "false").lower() == "true",
            "aliases": json.loads(get_setting("webmin_aliases", "{}") or "{}"),
        },
        # SSH console — global defaults (Admin → SSH). Secrets
        # redacted per CLAUDE.md's ``_set`` flag contract: the browser
        # learns only whether a private key / passphrase has been set.
        # Known-hosts is non-secret (paste-and-forget public data) so
        # the full blob round-trips. Destructive patterns are operator-
        # editable regex — shown verbatim for the textarea.
        "ssh": {
            "user":            get_setting("ssh_default_user", "") or "",
            "port":            int(get_setting("ssh_default_port", "22") or "22"),
            "private_key_set": bool(get_setting("ssh_default_private_key", "")),
            "passphrase_set":  bool(get_setting("ssh_default_private_key_passphrase", "")),
            "password_set":    bool(get_setting("ssh_default_password", "")),
            "fqdn_suffix":     get_setting("ssh_fqdn_suffix", "") or "",
            "known_hosts":     get_setting("ssh_default_known_hosts", "") or "",
            "custom_actions":  (lambda raw:
                (json.loads(raw) if (raw or "").strip() else [])
            )(get_setting("ssh_custom_actions", "")),
            "destructive_patterns": (
                get_setting("ssh_destructive_patterns", "") or ""
            ),
        },
        # Back-compat: older UI bits read this top-level field.
        "endpoint_id": p.get("portainer_endpoint_id", 1),
        # Portainer: URL / endpoint / TLS are returned in the clear so
        # the Settings form can prefill them. API key is write-only —
        # only the _set flag is reported.
        "portainer": {
            "url": p.get("portainer_url") or "",
            "endpoint_id": p.get("portainer_endpoint_id", 1),
            "verify_tls": bool(p.get("portainer_verify_tls", True)),
            "api_key_set": bool(p.get("portainer_api_key")),
            "configured": _portainer.is_configured(),
        },
        # OIDC: issuer / client_id / scopes / admin group / enabled are
        # returned in the clear; client secret is write-only. Redirect
        # URI falls back to the computed default so the Settings panel
        # can show a Copy button populated with what the IdP needs.
        "oidc": {
            "enabled": bool(a.get("oidc_enabled")),
            "issuer_url": a.get("oidc_issuer_url") or "",
            "client_id": a.get("oidc_client_id") or "",
            "client_secret_set": bool(a.get("oidc_client_secret")),
            "redirect_uri": a.get("oidc_redirect_uri") or "",
            "redirect_uri_default": oidc.public_redirect_uri(request),
            "scopes": a.get("oidc_scopes") or "openid email profile groups",
            "admin_group": a.get("oidc_admin_group") or "",
            "verify_tls": bool(a.get("oidc_verify_tls", True)),
        },
    }


@app.post("/api/settings")
async def api_set_settings(
    s: SettingsIn,
    _admin: auth.User = Depends(auth.require_admin),
):
    from logic import portainer as _portainer
    if s.apprise_url is not None: set_setting("apprise_url", s.apprise_url)
    if s.apprise_tag is not None: set_setting("apprise_tag", s.apprise_tag)
    # Open-Meteo upstream — strips trailing slashes so `<base>/v1/...`
    # composition in api_weather stays stable whether the operator
    # typed a trailing slash or not.
    if s.open_meteo_url is not None:
        set_setting("open_meteo_url", (s.open_meteo_url or "").strip().rstrip("/"))
    if s.portainer_public_url is not None: set_setting("portainer_public_url", s.portainer_public_url)
    if s.backup_retention_count is not None:
        n = max(0, int(s.backup_retention_count))
        set_setting("backup_retention_count", str(n))
    if s.scheduler_timezone is not None:
        # Validate the IANA name up-front so a typo doesn't silently
        # fall back to UTC on every tick. Blank is accepted = reset.
        tz_name = (s.scheduler_timezone or "").strip()
        if tz_name:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(tz_name)  # raises on invalid
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"scheduler_timezone {tz_name!r} is not a valid IANA name: {e}",
                )
        set_setting("scheduler_timezone", tz_name)
    if s.node_exporter_enabled is not None:
        set_setting("node_exporter_enabled", "true" if s.node_exporter_enabled else "false")
    if s.node_exporter_url_template is not None:
        # Validate the template minimally — must contain AT LEAST ONE
        # of the two supported placeholders: {host} (Docker hostname)
        # or {ip} (Swarm-advertised IP). Operators on flat LAN setups
        # where DNS doesn't resolve containers-by-name want the {ip}
        # form; Swarm-managed fleets typically use {host}. Empty
        # template resets to the default on the read side.
        tpl = s.node_exporter_url_template.strip()
        if tpl and "{host}" not in tpl and "{ip}" not in tpl:
            raise HTTPException(
                status_code=400,
                detail="node_exporter_url_template must contain '{host}' or '{ip}'.",
            )
        set_setting("node_exporter_url_template", tpl)
    if s.node_exporter_overrides is not None:
        # Normalise: reject non-dict, drop blank keys/values. The DB
        # stores the JSON verbatim; gather.py reads + applies it.
        if not isinstance(s.node_exporter_overrides, dict):
            raise HTTPException(
                status_code=400,
                detail="node_exporter_overrides must be a JSON object.",
            )
        clean = {
            str(k).strip(): str(v).strip()
            for k, v in s.node_exporter_overrides.items()
            if str(k).strip() and str(v).strip()
        }
        set_setting("node_exporter_overrides", json.dumps(clean))
    if s.host_stats_source is not None:
        # Accept a CSV ("beszel,node_exporter") or a single legacy value.
        # Empty / "none" / unknown tokens collapse to "none" so the
        # gather skips the whole block.
        raw = (s.host_stats_source or "").strip()
        parts = {t.strip().lower() for t in raw.split(",") if t.strip()}
        parts.discard("none")
        valid = {"beszel", "node_exporter", "pulse", "webmin"}
        unknown = parts - valid
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    "host_stats_source must be a CSV of 'beszel' / "
                    "'node_exporter' / 'pulse' / 'webmin' (or 'none'). "
                    f"Unknown: {sorted(unknown)}"
                ),
            )
        normalized = ",".join(sorted(parts)) if parts else "none"
        set_setting("host_stats_source", normalized)
    if s.beszel_hub_url is not None:
        # Trim trailing slash so downstream concatenation stays clean.
        set_setting("beszel_hub_url", (s.beszel_hub_url or "").strip().rstrip("/"))
    if s.beszel_identity is not None:
        set_setting("beszel_identity", (s.beszel_identity or "").strip())
    # Password: same keep-current-if-blank contract as other secrets.
    if s.beszel_password is not None and s.beszel_password.strip() != "":
        set_setting("beszel_password", s.beszel_password)
    if s.beszel_verify_tls is not None:
        set_setting("beszel_verify_tls", "true" if s.beszel_verify_tls else "false")
    if s.pulse_url is not None:
        set_setting("pulse_url", (s.pulse_url or "").strip().rstrip("/"))
    if s.pulse_token is not None and s.pulse_token.strip() != "":
        set_setting("pulse_token", s.pulse_token)
    if s.pulse_verify_tls is not None:
        set_setting("pulse_verify_tls", "true" if s.pulse_verify_tls else "false")
    if s.pulse_aliases is not None:
        clean = {
            str(k).strip(): str(v).strip()
            for k, v in (s.pulse_aliases or {}).items()
            if str(k).strip() and str(v).strip()
        }
        set_setting("pulse_aliases", json.dumps(clean))
    if s.beszel_aliases is not None:
        # Filter to string→string, trim, drop empty entries so a blank
        # row in the UI doesn't persist as a ghost mapping.
        clean = {
            str(k).strip(): str(v).strip()
            for k, v in (s.beszel_aliases or {}).items()
            if str(k).strip() and str(v).strip()
        }
        set_setting("beszel_aliases", json.dumps(clean))
    # Webmin — same suffix / _set conventions as every other provider's
    # secret. ``webmin_aliases`` is Docker hostname → Miniserv base URL.
    if s.webmin_url is not None:
        set_setting("webmin_url", (s.webmin_url or "").strip().rstrip("/"))
    if s.webmin_user is not None:
        set_setting("webmin_user", (s.webmin_user or "").strip())
    if s.webmin_password is not None and s.webmin_password.strip() != "":
        set_setting("webmin_password", s.webmin_password)
    if s.webmin_verify_tls is not None:
        set_setting("webmin_verify_tls", "true" if s.webmin_verify_tls else "false")
    if s.webmin_aliases is not None:
        clean = {
            str(k).strip(): str(v).strip().rstrip("/")
            for k, v in (s.webmin_aliases or {}).items()
            if str(k).strip() and str(v).strip()
        }
        set_setting("webmin_aliases", json.dumps(clean))
    # SSH console — mirrors the webmin / beszel / pulse suffix contract.
    # Private key + passphrase use "keep current if blank". Known hosts
    # and destructive patterns are plain strings (operator clears by
    # passing an empty string explicitly).
    if s.ssh_default_user is not None:
        set_setting("ssh_default_user", (s.ssh_default_user or "").strip())
    if s.ssh_default_port is not None:
        try:
            p = int(s.ssh_default_port)
        except (TypeError, ValueError):
            p = 22
        if not (1 <= p <= 65535):
            raise HTTPException(
                status_code=400,
                detail="ssh_default_port must be 1-65535",
            )
        set_setting("ssh_default_port", str(p))
    if s.ssh_default_private_key is not None and s.ssh_default_private_key.strip() != "":
        # Minimal validation — parse the key to catch malformed input at
        # save time rather than at first run. Passphrase is unknown at
        # this point (it may be saved in the SAME request), so we try
        # the currently-persisted passphrase and a blank as a fallback.
        # Any ImportError gets surfaced as HTTP 400.
        try:
            import asyncssh as _asyncssh
            pw_candidate = (
                s.ssh_default_private_key_passphrase
                if s.ssh_default_private_key_passphrase is not None
                else (get_setting("ssh_default_private_key_passphrase", "") or "")
            ) or None
            _asyncssh.import_private_key(
                s.ssh_default_private_key, passphrase=pw_candidate,
            )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"ssh_default_private_key failed to parse: {type(e).__name__}: {e}",
            )
        set_setting("ssh_default_private_key", s.ssh_default_private_key)
    if s.ssh_default_private_key_passphrase is not None \
            and s.ssh_default_private_key_passphrase.strip() != "":
        set_setting(
            "ssh_default_private_key_passphrase",
            s.ssh_default_private_key_passphrase,
        )
    # SSH password auth. Blank = keep-current (matches the _set flag
    # convention). Non-empty replaces. No validation needed at save
    # time — asyncssh raises on connect if the password is wrong.
    if s.ssh_default_password is not None \
            and s.ssh_default_password.strip() != "":
        set_setting("ssh_default_password", s.ssh_default_password)
    if s.ssh_fqdn_suffix is not None:
        # Normalise — operator might paste with or without leading dot.
        # Store canonical form: leading dot, no trailing dot, trimmed.
        raw = (s.ssh_fqdn_suffix or "").strip().rstrip(".")
        if raw and not raw.startswith("."):
            raw = "." + raw
        set_setting("ssh_fqdn_suffix", raw)
    if s.ssh_default_known_hosts is not None:
        set_setting("ssh_default_known_hosts", s.ssh_default_known_hosts or "")
    if s.ssh_destructive_patterns is not None:
        # Validate each pattern compiles as regex — one bad line would
        # otherwise silently exempt every destructive command on the
        # very first eval in logic/ssh.py.
        import re as _re
        raw = s.ssh_destructive_patterns or ""
        for part in _re.split(r"[\n,]+", raw):
            p = part.strip()
            if not p:
                continue
            try:
                _re.compile(p)
            except _re.error as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"invalid destructive regex {p!r}: {e}",
                )
        set_setting("ssh_destructive_patterns", raw)
    # Clear flags — operator clicked "Clear" on an SSH secret. Delete
    # the underlying setting outright (not just set to ""); downstream
    # code treats missing / empty identically, but the flag-driven path
    # is the only way to erase a value that the keep-current-if-blank
    # contract otherwise preserves forever.
    if s.clear_ssh_private_key:
        set_setting("ssh_default_private_key", "")
        set_setting("ssh_default_private_key_passphrase", "")  # orphaned passphrase is noise
    if s.clear_ssh_passphrase:
        set_setting("ssh_default_private_key_passphrase", "")
    if s.clear_ssh_password:
        set_setting("ssh_default_password", "")
    # Custom SSH actions — JSON array replaces the whole list wholesale.
    # Full-replace semantics match how Admin → Hosts saves hosts_config.
    # Shape validation lives here so the runner can trust what it reads.
    if s.ssh_custom_actions is not None:
        if not isinstance(s.ssh_custom_actions, list):
            raise HTTPException(400, "ssh_custom_actions must be a list")
        clean_actions: list[dict] = []
        for a in s.ssh_custom_actions:
            if not isinstance(a, dict):
                continue
            title = (a.get("title") or "").strip()
            cmd = (a.get("command") or "").strip()
            if not title or not cmd:
                continue
            clean_actions.append({
                "id":      (a.get("id") or "").strip() or _slugify_action(title),
                "title":   title[:80],
                "command": cmd[:2048],
            })
        set_setting("ssh_custom_actions", json.dumps(clean_actions))

    # --- Host groups (ticket #93) -----------------------------------------
    # Each entry: {name, range_start, range_end, order?}. Malformed
    # entries are silently dropped so a stray UI row doesn't fail the
    # whole save. Name capped at 60 chars (prevents layout breakage in
    # the collapsible group heading). range_end >= range_start.
    if s.host_groups is not None:
        if not isinstance(s.host_groups, list):
            raise HTTPException(400, "host_groups must be a list")
        clean_groups: list[dict] = []
        for i, g in enumerate(s.host_groups):
            if not isinstance(g, dict):
                continue
            name = (g.get("name") or "").strip()[:60]
            if not name:
                continue
            try:
                rs = int(g.get("range_start"))
                re_ = int(g.get("range_end"))
            except (TypeError, ValueError):
                continue
            if rs < 0 or re_ < rs:
                continue
            try:
                order = int(g.get("order", i))
            except (TypeError, ValueError):
                order = i
            clean_groups.append({
                "name":        name,
                "range_start": rs,
                "range_end":   re_,
                "order":       order,
            })
        # Reject overlapping ranges — two groups whose [range_start,
        # range_end] intervals intersect cause first-match-wins
        # ordering in `groupedHosts()`, which is undefined from the
        # operator's perspective (invisible until they open the
        # "wrong" group and wonder why a host landed there). Pair-wise
        # scan on a range-start-sorted copy keeps the check O(n log n)
        # and surfaces the specific conflicting pair so the error is
        # actionable.
        by_start = sorted(clean_groups, key=lambda g: (g["range_start"], g["range_end"]))
        for a, b in zip(by_start, by_start[1:]):
            if a["range_end"] >= b["range_start"]:
                raise HTTPException(
                    400,
                    f"host_groups: '{a['name']}' ({a['range_start']}–{a['range_end']}) "
                    f"overlaps '{b['name']}' ({b['range_start']}–{b['range_end']}). "
                    f"Ranges must be disjoint.",
                )
        # Persist in order-field order so render iteration doesn't have to re-sort.
        clean_groups.sort(key=lambda g: (g["order"], g["name"]))
        set_setting("host_groups", json.dumps(clean_groups))

    # --- Asset inventory (ticket #78) -------------------------------------
    # Secret follows the keep-current-if-blank + clear-flag contract.
    if s.asset_inventory_base_url is not None:
        set_setting("asset_inventory_base_url",
                    (s.asset_inventory_base_url or "").strip().rstrip("/"))
    if s.asset_inventory_token_url is not None:
        set_setting("asset_inventory_token_url",
                    (s.asset_inventory_token_url or "").strip())
    if s.asset_inventory_client_id is not None:
        set_setting("asset_inventory_client_id",
                    (s.asset_inventory_client_id or "").strip())
    if s.asset_inventory_scope is not None:
        set_setting("asset_inventory_scope",
                    (s.asset_inventory_scope or "").strip())
    if s.asset_inventory_client_secret is not None \
            and s.asset_inventory_client_secret.strip() != "":
        set_setting("asset_inventory_client_secret",
                    s.asset_inventory_client_secret)
    if s.clear_asset_inventory_client_secret:
        set_setting("asset_inventory_client_secret", "")
    if s.asset_inventory_auth_mode is not None:
        mode = (s.asset_inventory_auth_mode or "").strip().lower()
        if mode not in ("oauth2", "lifetime_token"):
            mode = "oauth2"
        set_setting("asset_inventory_auth_mode", mode)
    if s.asset_inventory_lifetime_token is not None \
            and s.asset_inventory_lifetime_token.strip() != "":
        set_setting("asset_inventory_lifetime_token",
                    s.asset_inventory_lifetime_token.strip())
    if s.clear_asset_inventory_lifetime_token:
        set_setting("asset_inventory_lifetime_token", "")
    if s.asset_inventory_service is not None:
        set_setting("asset_inventory_service",
                    (s.asset_inventory_service or "").strip())
    if s.asset_inventory_action is not None:
        set_setting("asset_inventory_action",
                    (s.asset_inventory_action or "").strip())
    if s.asset_inventory_min_value is not None:
        # Blank = clear; non-blank = parse as int. Anything malformed
        # falls back to clear so a typo doesn't poison the setting.
        v = (s.asset_inventory_min_value or "").strip()
        try:
            set_setting("asset_inventory_min_value", str(int(v)) if v else "")
        except ValueError:
            set_setting("asset_inventory_min_value", "")
    if s.asset_inventory_max_value is not None:
        v = (s.asset_inventory_max_value or "").strip()
        try:
            set_setting("asset_inventory_max_value", str(int(v)) if v else "")
        except ValueError:
            set_setting("asset_inventory_max_value", "")

    _cache["ts"] = 0  # force the next gather to re-read alias settings

    auth_changed = False
    portainer_changed = False
    with db_conn() as c:
        # --- Portainer connection -----------------------------------------
        if s.portainer_url is not None:
            set_setting("portainer_url", (s.portainer_url or "").rstrip("/"))
            portainer_changed = True
        if s.portainer_endpoint_id is not None:
            set_setting("portainer_endpoint_id", str(int(s.portainer_endpoint_id)))
            portainer_changed = True
        if s.portainer_verify_tls is not None:
            set_setting("portainer_verify_tls", "true" if s.portainer_verify_tls else "false")
            portainer_changed = True
        # Empty / whitespace-only = "keep current" (same pattern as
        # oidc_client_secret). Admins clear the value by a different
        # route if ever needed.
        if s.portainer_api_key is not None and s.portainer_api_key.strip() != "":
            set_setting("portainer_api_key", s.portainer_api_key)
            portainer_changed = True

        # --- OIDC ---------------------------------------------------------
        if s.oidc_enabled is not None:
            auth.set_auth_setting(c, "oidc_enabled",
                                  "true" if s.oidc_enabled else "false")
            auth_changed = True
        if s.oidc_issuer_url is not None:
            auth.set_auth_setting(c, "oidc_issuer_url", s.oidc_issuer_url.strip())
            auth_changed = True
        if s.oidc_client_id is not None:
            auth.set_auth_setting(c, "oidc_client_id", s.oidc_client_id.strip())
            auth_changed = True
        if s.oidc_redirect_uri is not None:
            auth.set_auth_setting(c, "oidc_redirect_uri", s.oidc_redirect_uri.strip())
            auth_changed = True
        if s.oidc_scopes is not None:
            auth.set_auth_setting(c, "oidc_scopes", s.oidc_scopes.strip())
            auth_changed = True
        if s.oidc_admin_group is not None:
            auth.set_auth_setting(c, "oidc_admin_group", s.oidc_admin_group.strip())
        if s.oidc_verify_tls is not None:
            auth.set_auth_setting(c, "oidc_verify_tls",
                                  "true" if s.oidc_verify_tls else "false")
            auth_changed = True
        # Client secret: keep-current-if-blank.
        if s.oidc_client_secret is not None and s.oidc_client_secret.strip() != "":
            auth.set_auth_setting(c, "oidc_client_secret", s.oidc_client_secret)
            auth_changed = True

    if auth_changed:
        auth.invalidate_auth_settings_cache()
        # Discovery / JWKS cache also drops so the next flow picks up the
        # new issuer URL without waiting out the TTL.
        oidc.invalidate_cache()
    if portainer_changed:
        _portainer.invalidate_portainer_cache()
        # Force a fresh gather on the next /api/items so the dashboard
        # reflects the new Portainer target without a manual refresh.
        _cache["ts"] = 0
    return {"status": "ok"}


# ----------------------------------------------------------------------------
# OIDC auth routes — see logic/oidc.py for the flow spec.
# ----------------------------------------------------------------------------
@app.get("/api/oidc/login")
async def api_oidc_login(request: Request):
    return await oidc.login(request)


@app.get("/api/oidc/callback")
async def api_oidc_callback(request: Request):
    return await oidc.callback(request)


@app.post("/api/oidc/test")
async def api_oidc_test(
    request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    """Admin-only: probe the issuer's discovery endpoint. Used by the
    "Test connection" button in the Settings panel. No state changes.
    """
    body = await request.json()
    issuer = (body.get("issuer_url") or "").strip()
    return await oidc.test_discovery(issuer)


@app.post("/api/portainer/test")
async def api_portainer_test(
    request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    """Admin-only: probe ``{url}/api/status`` with the given API key.
    Supports both already-saved creds (empty api_key means "use current")
    and unsaved form values (api_key populated). No state changes.
    """
    from logic import portainer as _portainer
    body = await request.json()
    url = (body.get("url") or "").strip().rstrip("/")
    endpoint_id = int(body.get("endpoint_id") or 1)
    verify_tls = bool(body.get("verify_tls", True))
    api_key = body.get("api_key") or ""
    if not api_key:
        # Fall back to the stored value so Test can work without
        # retyping the key every time.
        api_key = str(_portainer.get_portainer_settings().get("portainer_api_key") or "")
    if not url or not api_key:
        return {"ok": False, "status": 0, "detail": "URL and API key are both required"}
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(verify=verify_tls, timeout=10.0) as client:
            r = await client.get(
                f"{url}/api/status",
                headers={"X-API-Key": api_key},
            )
        if r.status_code == 200:
            detail = "OK"
            try:
                data = r.json()
                version = data.get("Version") or data.get("version")
                if version:
                    detail = f"OK — Portainer {version}"
            except Exception:
                pass
            return {"ok": True, "status": 200, "detail": detail, "endpoint_id": endpoint_id}
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "status": 0, "detail": f"{type(e).__name__}: {e}"}


@app.post("/api/pulse/test")
async def api_pulse_test(
    request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    """Admin-only: probe a Pulse instance with the given (or saved)
    credentials. Mirrors :func:`api_beszel_test` — accepts unsaved form
    values or falls back to the persisted token so Test works after
    first save without re-typing the secret."""
    from logic import pulse as _pulse
    body = await request.json()
    url = (body.get("url") or "").strip().rstrip("/")
    token = body.get("token") or ""
    verify_tls = bool(body.get("verify_tls", True))
    if not token:
        token = get_setting("pulse_token", "") or ""
    if not url or not token:
        return {"ok": False, "detail": "URL and API token are both required"}
    result = await _pulse.probe_pulse(
        url, token, verify_tls=verify_tls, timeout=10.0,
    )
    if result.get("error"):
        return {"ok": False, "detail": result["error"]}
    hosts = result.get("hosts") or {}
    names = sorted(hosts.keys())
    detail = (f"OK — reached Pulse, {len(hosts)} node(s) visible: "
              + (", ".join(names[:5]) or "none"))
    if len(names) > 5:
        detail += f" (+{len(names) - 5} more)"
    return {"ok": True, "detail": detail, "node_count": len(hosts),
            "nodes": names}


@app.post("/api/webmin/test")
async def api_webmin_test(
    request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    """Admin-only: probe a Webmin Miniserv instance.

    Accepts ``{url, user, password, verify_tls}``. Password is keep-
    current-if-blank (same contract as Portainer / Beszel / Pulse
    test endpoints). Returns ``{ok, detail}`` with a short summary.
    """
    from logic import webmin as _webmin
    body = await request.json()
    url = (body.get("url") or "").strip().rstrip("/")
    user = (body.get("user") or "").strip()
    password = body.get("password") or ""
    verify_tls = bool(body.get("verify_tls", False))
    if not password:
        password = get_setting("webmin_password", "") or ""
    if not user:
        user = get_setting("webmin_user", "") or ""
    if not url or not user or not password:
        return {"ok": False,
                "detail": "URL, user and password are all required"}
    result = await _webmin.probe_webmin(
        url, user, password, verify_tls=verify_tls, timeout=10.0,
    )
    if result.get("error") and not result.get("hosts"):
        return {"ok": False, "detail": result["error"]}
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
              f"mem={mem // (1024**3) if mem else '?'} GB · "
              f"mounts={mounts} · nics={nics}")
    partial = result.get("partial_errors") or []
    if partial:
        detail += f" · partial: {len(partial)} module(s) failed"
    return {"ok": True, "detail": detail, "host_key": host_key,
            "partial_errors": partial}


@app.post("/api/beszel/test")
async def api_beszel_test(
    request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    """Admin-only: probe a Beszel Hub with the given (or saved) creds.

    Mirrors :func:`api_portainer_test` — accepts unsaved form values OR
    falls back to the persisted password so Test works after first save
    without re-typing it. Returns ``{ok, detail, system_count}``.
    """
    from logic import beszel as _beszel
    body = await request.json()
    hub_url = (body.get("hub_url") or "").strip().rstrip("/")
    identity = (body.get("identity") or "").strip()
    password = body.get("password") or ""
    verify_tls = bool(body.get("verify_tls", True))
    if not password:
        # Same keep-current-if-blank contract as Portainer's API key.
        password = get_setting("beszel_password", "") or ""
    if not hub_url or not identity or not password:
        return {"ok": False, "detail": "Hub URL, identity and password are all required"}
    result = await _beszel.probe_hub(
        hub_url, identity, password, verify_tls=verify_tls, timeout=10.0,
    )
    if result.get("error"):
        return {"ok": False, "detail": result["error"]}
    systems = result.get("systems") or {}
    detail = (f"OK — reached hub, {len(systems)} system(s) visible: "
              + (", ".join(sorted(systems.keys())[:5]) or "none"))
    if len(systems) > 5:
        detail += f" (+{len(systems) - 5} more)"
    return {"ok": True, "detail": detail, "system_count": len(systems),
            "systems": sorted(systems.keys())}


# ----------------------------------------------------------------------------
# Asset inventory (ticket #78) — oufa.co OAuth2 client_credentials. Manual
# refresh only; reads go through the file cache at /app/data/asset_inventory.json.
# ----------------------------------------------------------------------------
@app.get("/api/asset-inventory")
async def api_asset_inventory(_admin: auth.User = Depends(auth.require_admin)):
    """Admin-only: return the cached asset inventory snapshot.

    Returns the shape ``{ok, ts, count, assets, upstream, error}``. An
    empty / missing cache is reported via ``ok=false`` + ``error`` so the
    UI can render an empty state without special-casing HTTP 404.
    """
    from logic import asset_inventory as _ai
    return _ai.load_cache()


@app.post("/api/asset-inventory/test")
async def api_asset_inventory_test(
    request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    """Admin-only: validate asset-inventory credentials end-to-end.

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
    except Exception:
        body = {}
    auth_mode = (body.get("auth_mode") or "").strip().lower() \
        or (get_setting("asset_inventory_auth_mode", "") or "oauth2")
    if auth_mode not in ("oauth2", "lifetime_token"):
        auth_mode = "oauth2"
    if auth_mode == "lifetime_token":
        base_url = (body.get("base_url") or "").strip().rstrip("/") \
            or (get_setting("asset_inventory_base_url", "") or "").strip().rstrip("/")
        lifetime_token = body.get("lifetime_token") or ""
        if not lifetime_token:
            lifetime_token = get_setting("asset_inventory_lifetime_token", "") or ""
        service = (body.get("service") or "").strip() \
            or (get_setting("asset_inventory_service", "") or "").strip()
        action = (body.get("action") or "").strip() \
            or (get_setting("asset_inventory_action", "") or "").strip()

        def _bound(from_body, setting_key):
            raw = from_body
            if raw is None or str(raw).strip() == "":
                raw = get_setting(setting_key, "") or ""
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
            verify_tls=True,
        )
        if result.get("ok"):
            count = len(result.get("assets") or [])
            return {"ok": True,
                    "detail": f"OK — fetched {count} asset(s) from {endpoint}"}
        out = {"ok": False, "detail": result.get("error") or "auth failed"}
        if "error_code" in result:
            out["error_code"] = result["error_code"]
            out["error_params"] = result.get("error_params", {})
        return out
    # Default: OAuth2 client_credentials.
    token_url = (body.get("token_url") or "").strip() \
        or (get_setting("asset_inventory_token_url", "") or "")
    client_id = (body.get("client_id") or "").strip() \
        or (get_setting("asset_inventory_client_id", "") or "")
    scope = (body.get("scope") or "").strip() \
        or (get_setting("asset_inventory_scope", "") or "")
    client_secret = body.get("client_secret") or ""
    if not client_secret:
        client_secret = get_setting("asset_inventory_client_secret", "") or ""
    if not token_url or not client_id or not client_secret:
        return {"ok": False,
                "detail": "token_url, client_id and client_secret are all required"}
    result = await _ai.probe_token(
        token_url, client_id, client_secret, scope=scope, verify_tls=True,
    )
    if result.get("ok"):
        expires_in = result.get("expires_in") or 0
        return {"ok": True,
                "detail": (f"OK — got {result.get('token_type') or 'Bearer'} token"
                           + (f", expires in {expires_in}s" if expires_in else ""))}
    return {"ok": False, "detail": result.get("error") or "auth failed"}


@app.post("/api/asset-inventory/refresh")
async def api_asset_inventory_refresh(
    _admin: auth.User = Depends(auth.require_admin),
):
    """Admin-only: probe auth + fetch assets + overwrite the cache.

    Manual refresh only — there is no lifespan loop. Branches on the
    persisted ``asset_inventory_auth_mode`` setting. Returns the
    summary from ``refresh_cache`` so the UI can show a toast with the
    new count and timestamp.
    """
    from logic import asset_inventory as _ai
    base_url = (get_setting("asset_inventory_base_url", "") or "").strip().rstrip("/")
    auth_mode = (get_setting("asset_inventory_auth_mode", "") or "oauth2").strip().lower()
    if auth_mode not in ("oauth2", "lifetime_token"):
        auth_mode = "oauth2"
    if auth_mode == "lifetime_token":
        lifetime_token = get_setting("asset_inventory_lifetime_token", "") or ""
        service = (get_setting("asset_inventory_service", "") or "").strip()
        action = (get_setting("asset_inventory_action", "") or "").strip()
        min_raw = (get_setting("asset_inventory_min_value", "") or "").strip()
        max_raw = (get_setting("asset_inventory_max_value", "") or "").strip()
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
        return await _ai.refresh_cache(
            base_url,
            verify_tls=True,
            auth_mode=_ai.AUTH_MODE_LIFETIME_TOKEN,
            lifetime_token=lifetime_token,
            service=service,
            action=action,
            min_value=min_value,
            max_value=max_value,
        )
    token_url = (get_setting("asset_inventory_token_url", "") or "").strip()
    client_id = (get_setting("asset_inventory_client_id", "") or "").strip()
    client_secret = get_setting("asset_inventory_client_secret", "") or ""
    scope = (get_setting("asset_inventory_scope", "") or "").strip()
    if not base_url or not token_url or not client_id or not client_secret:
        return {"ok": False, "count": 0, "ts": 0,
                "error": "asset_inventory_* settings are incomplete — "
                         "configure base_url / token_url / client_id / client_secret"}
    return await _ai.refresh_cache(
        base_url,
        token_url=token_url,
        client_id=client_id,
        client_secret=client_secret,
        scope=scope,
        verify_tls=True,
    )


def _meaningful(v) -> bool:
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def _merge_best(dst: dict, src: dict) -> None:
    """Copy meaningful values from src into dst; leave dst's existing
    non-zero fields intact when src is empty/zero. Same helper as
    :mod:`logic.gather` uses — kept local here so the Hosts endpoint
    and gather stay in sync without a cross-module dependency on a
    private helper."""
    if not src:
        return
    for k, v in src.items():
        if _meaningful(v):
            dst[k] = v
        elif k not in dst:
            dst[k] = v


@app.get("/api/hosts")
async def api_hosts():
    """Hosts view — returns the CURATED host list merged with live
    stats from every enabled provider.

    Source of truth is ``hosts_config`` (Settings → Hosts). If it's
    empty, falls back to auto-discovering from Beszel so the view
    isn't blank for fresh installs.

    Each curated host entry specifies its per-provider name:
      - ``ne_url``      — node-exporter scrape URL
      - ``beszel_name`` — Beszel ``host`` field to match
      - ``pulse_name``  — Pulse PVE node name
    For each enabled provider, we fetch once (all hosts in one call
    where possible) and look up this host's stats by its provider-
    specific name. Fields are merged with the best-of rule — non-zero
    values win over zeros — so flaky providers never erase good data.
    """
    from logic import beszel as _beszel
    from logic import pulse as _pulse
    from logic import node_exporter as _ne

    # Which providers are live?
    raw_source = (get_setting("host_stats_source", "") or "").strip()
    if not raw_source:
        raw_source = ("node_exporter"
                      if (get_setting("node_exporter_enabled", "false") or "false").lower() == "true"
                      else "")
    active = {
        s.strip().lower()
        for s in raw_source.split(",")
        if s.strip() and s.strip().lower() != "none"
    }

    curated = _load_hosts_config()
    errors: dict[str, str] = {}

    # ---- Batch-fetch each enabled provider once -------------------
    beszel_map: dict[str, dict] = {}
    if "beszel" in active:
        hub_url = get_setting("beszel_hub_url", "") or ""
        ident = get_setting("beszel_identity", "") or ""
        passw = get_setting("beszel_password", "") or ""
        verify = (get_setting("beszel_verify_tls", "true") or "true").lower() == "true"
        if hub_url and ident and passw:
            r = await _beszel.probe_hub(hub_url, ident, passw, verify_tls=verify)
            if r.get("error"):
                errors["beszel"] = r["error"]
            beszel_map = r.get("systems") or {}
        else:
            errors["beszel"] = "missing url / identity / password"

    pulse_map: dict[str, dict] = {}
    if "pulse" in active:
        pulse_url = get_setting("pulse_url", "") or ""
        pulse_token = get_setting("pulse_token", "") or ""
        verify = (get_setting("pulse_verify_tls", "true") or "true").lower() == "true"
        if pulse_url and pulse_token:
            r = await _pulse.probe_pulse(pulse_url, pulse_token, verify_tls=verify)
            if r.get("error"):
                errors["pulse"] = r["error"]
            pulse_map = r.get("hosts") or {}
        else:
            errors["pulse"] = "missing url / token"

    webmin_creds_ok = False
    webmin_user = ""
    webmin_password = ""
    webmin_verify = False
    webmin_aliases: dict[str, str] = {}
    if "webmin" in active:
        from logic import webmin as _webmin  # noqa: F401 — used below
        webmin_user = get_setting("webmin_user", "") or ""
        webmin_password = get_setting("webmin_password", "") or ""
        webmin_verify = (get_setting("webmin_verify_tls", "false")
                         or "false").lower() == "true"
        try:
            wm_aliases_raw = json.loads(
                get_setting("webmin_aliases", "{}") or "{}"
            )
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

    # ---- Fallback: auto-discover from Beszel when no curated list ----
    if not curated:
        if beszel_map:
            curated = [
                {
                    "id":          k,
                    "label":       v.get("beszel_name") or k,
                    "ne_url":      "",
                    "beszel_name": k,
                    "pulse_name":  "",
                    "enabled":     True,
                }
                for k in sorted(beszel_map.keys(), key=str.lower)
            ]
        elif pulse_map:
            curated = [
                {
                    "id":          k,
                    "label":       v.get("pulse_name") or k,
                    "ne_url":      "",
                    "beszel_name": "",
                    "pulse_name":  k,
                    "enabled":     True,
                }
                for k in sorted(pulse_map.keys(), key=str.lower)
            ]

    # ---- Per-host merge -------------------------------------------
    out: list[dict] = []
    ne_probes: list[tuple[dict, str]] = []  # (host_record, url)

    for h in curated:
        if not h.get("enabled", True):
            continue
        merged: dict = {}
        providers_hit: list[str] = []

        # Merge order: Pulse (fallback / coarse detail) → Beszel
        # (cleaner short forms override Pulse) → node-exporter
        # (richest Linux detail). Each provider's ``_merge_best``
        # only overwrites when the new value is meaningful, so
        # Pulse-only hosts keep Pulse's platform/kernel/arch, while
        # hosts covered by Beszel get Beszel's tidier values.
        pulse_key = h.get("pulse_name") or h.get("id") or ""
        if "pulse" in active and pulse_key:
            print(f"[hosts] host id={h.get('id')!r} label={h.get('label')!r} "
                  f"pulse_name={h.get('pulse_name')!r} "
                  f"→ lookup key={pulse_key!r} "
                  f"(pulse map has {len(pulse_map)} entries)")
            pstats = _pulse.lookup(pulse_map, pulse_key)
            if pstats:
                _merge_best(merged, pstats)
                providers_hit.append("pulse")
                print(f"[hosts] host id={h.get('id')!r} pulse MATCH "
                      f"(kind={pstats.get('pulse_kind')!r} "
                      f"name={pstats.get('pulse_name')!r})")
            else:
                print(f"[hosts] host id={h.get('id')!r} pulse NO MATCH")

        # Match order for Beszel: explicit per-provider name → host
        # id. When the operator leaves a mapping blank but the row's
        # id happens to match what Beszel reports, treat that as a
        # hit — saves typing the same hostname in two fields.
        beszel_key = h.get("beszel_name") or h.get("id") or ""
        if "beszel" in active and beszel_key:
            # Beszel is case-sensitive on its ``host`` field; the
            # lookup still requires an exact key. For forgiving
            # matching, users should populate the field explicitly.
            bstats = beszel_map.get(beszel_key)
            if bstats:
                _merge_best(merged, bstats)
                providers_hit.append("beszel")

        if "node_exporter" in active and h.get("ne_url"):
            ne_probes.append((h, h["ne_url"]))

        out.append({
            "_host_record": h,
            "_merged":      merged,
            "_providers":   providers_hit,
        })

    # Parallel node-exporter probes for hosts that had a ne_url.
    if ne_probes:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as ne_client:
            results = await asyncio.gather(*(
                _ne.probe_node(ne_client, url) for _, url in ne_probes
            ))
        # Zip back into the out list by reference.
        by_id = {o["_host_record"]["id"]: o for o in out}
        for (host_rec, _), stats in zip(ne_probes, results):
            entry = by_id.get(host_rec["id"])
            if entry is None:
                continue
            _merge_best(entry["_merged"], stats or {})
            if stats and not (stats.get("exporter_error")):
                entry["_providers"].append("node_exporter")

    # Webmin — per-host probes, runs LAST in the merge chain. Each
    # curated row that has a ``webmin_aliases`` URL (or explicit
    # ``webmin_url``) fires one probe in parallel. Hosts without a
    # mapping are silently skipped so the existing Pulse/Beszel/NE
    # pipeline keeps working unchanged.
    if "webmin" in active and webmin_creds_ok:
        from logic import webmin as _webmin
        probe_targets: list[tuple[dict, str]] = []
        for entry in out:
            hid = entry["_host_record"]["id"]
            wm_url = webmin_aliases.get(hid) or entry["_host_record"].get("webmin_url") or ""
            if wm_url:
                probe_targets.append((entry, wm_url))
        if probe_targets:
            # Per-host overall budget — Webmin does 5 sequential HTTP
            # calls (session login + 4 module fetches). At the default
            # 15s per-request timeout, a slow / hung Miniserv can push
            # one host to 75s, which trips NPM's 60s proxy_read_timeout
            # and the operator sees a 504. 20s is a soft ceiling that
            # still allows a healthy Webmin to complete its 5 calls.
            _WEBMIN_PROBE_BUDGET = 20.0

            async def _one_probe(wm_url: str) -> dict:
                try:
                    return await asyncio.wait_for(
                        _webmin.probe_webmin(
                            wm_url, webmin_user, webmin_password,
                            verify_tls=webmin_verify,
                            active_sources=active,
                        ),
                        timeout=_WEBMIN_PROBE_BUDGET,
                    )
                except asyncio.TimeoutError:
                    return {
                        "hosts": {},
                        "error": f"webmin probe timeout after {int(_WEBMIN_PROBE_BUDGET)}s",
                    }
                except Exception as e:  # noqa: BLE001 — surface to UI
                    return {"hosts": {}, "error": f"webmin probe failed: {e}"}

            webmin_results = await asyncio.gather(*(
                _one_probe(u) for _, u in probe_targets
            ), return_exceptions=False)
            for (entry, _url), result in zip(probe_targets, webmin_results):
                if result.get("error") and not result.get("hosts"):
                    errors.setdefault(
                        "webmin",
                        f"{entry['_host_record']['id']}: {result['error']}",
                    )
                    continue
                hosts_map = result.get("hosts") or {}
                if not hosts_map:
                    continue
                stats = next(iter(hosts_map.values()))
                _merge_best(entry["_merged"], stats)
                entry["_providers"].append("webmin")

    # ---- Shape the response ---------------------------------------
    # Short debug spew for core/arch/kernel only — helps diagnose the
    # common "all three columns are empty" complaint by showing each
    # curated host's merged values + which providers contributed.
    hosts = []
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
        hosts.append({
            "id":              h["id"],
            "name":            h["id"],
            "host":            h["id"],
            "label":           h.get("label") or h["id"],
            "beszel_name":     h.get("beszel_name") or "",
            "pulse_name":      h.get("pulse_name") or "",
            "ne_url":          h.get("ne_url") or "",
            "url":             h.get("url") or "",
            "icon":            h.get("icon") or "",
            "providers":       entry["_providers"],
            # Status priority: explicit Beszel → Pulse → fallback to
            # "up" when any provider returned non-zero data at all
            # (node-exporter and Webmin don't emit a status field; if
            # they answered, the host is clearly alive). Last resort
            # "unknown" only for hosts with NO provider response.
            "status":          (
                s.get("beszel_status")
                or s.get("pulse_status")
                or ("up" if entry["_providers"] else "unknown")
            ),
            "docker_node":     h["id"],  # curated list IS the docker-node-like mapping
            "platform":        s.get("host_platform") or "",
            "os":              s.get("host_os") or "",
            "kernel":          s.get("host_kernel") or "",
            "arch":            s.get("host_arch") or "",
            "agent":           s.get("host_agent") or "",
            # cores falls back to threads — Beszel container-mode
            # agents skip ``info.c`` (cores) but still emit
            # ``info.t`` (threads). Pulse's older state endpoint
            # sometimes omits ``maxcpu`` too. For a host overview,
            # either number answers the "how much compute does this
            # machine have" question, so we'd rather show one than
            # neither.
            "cores":           s.get("host_cores") or s.get("host_threads") or 0,
            "threads":         s.get("host_threads") or 0,
            "cpu_model":       s.get("host_cpu_model") or "",
            "cpu_percent":     s.get("host_cpu_percent") or 0,
            "mem_percent":     s.get("host_mem_percent") or 0,
            "disk_percent":    s.get("host_disk_percent") or 0,
            "mem_used":        s.get("host_mem_used") or 0,
            "mem_total":       s.get("host_mem_total") or 0,
            "disk_used":       s.get("host_disk_used") or 0,
            "disk_total":      s.get("host_disk_total") or 0,
            "mounts":          s.get("mounts") or [],
            "network_ifaces":  s.get("network_ifaces") or [],
            "bandwidth":       s.get("host_bandwidth") or 0,
            "containers":      s.get("host_containers") or 0,
            "uptime_s":        s.get("host_uptime_s") or 0,
            "boot_ts":         s.get("host_boot_ts"),
            "beszel_id":       s.get("beszel_id") or "",
            "beszel_updated":  s.get("beszel_updated") or "",
            # Pulse-specific metadata for rendering a "Proxmox" facet
            # in the SYSTEM / HARDWARE card when Pulse contributed.
            "pulse_kind":      s.get("pulse_kind") or "",        # "node" / "lxc" / "qemu"
            "pulse_vmid":      s.get("pulse_vmid") or 0,
            "pulse_node":      s.get("pulse_node") or "",        # PVE host the guest lives on
            "pulse_status":    s.get("pulse_status") or "",
            # Webmin — pending + security update counts. Both default
            # to 0 when Webmin didn't match this host so the UI's
            # ``x-show="h.updates_pending > 0"`` gate works without
            # needing to check provider membership.
            "updates_pending":  int(s.get("host_updates_pending") or 0),
            "updates_security": int(s.get("host_updates_security") or 0),
            # Operator-assigned catalogue number from hosts_config — feeds
            # the "Custom #" sort option in the Hosts view and is the
            # eventual key for Asset-inventory lookups at oufa.co.
            "custom_number":    h.get("custom_number"),
        })

    # Aggregate error — non-fatal; UI shows the first one per provider.
    agg_error = "; ".join(f"{k}: {v}" for k, v in errors.items()) or None

    return {
        "configured":  bool(active),
        "active":      sorted(active),
        "error":       agg_error,
        "provider_errors": errors,
        "hub_url":     get_setting("beszel_hub_url", "") or "",
        "hosts":       hosts,
        # Counts that let the frontend pick the right empty-state
        # copy — "no curated hosts yet" vs "all curated hosts are
        # disabled" vs "curated hosts exist but no provider matched
        # any of them". Without these the view used to blanket-say
        # "no hosts yet" even when the operator had rows configured.
        "curated_count": len(curated),
        "enabled_count": sum(1 for h in curated if h.get("enabled", True)),
    }


# ---------------------------------------------------------------------------
# Per-host async loading (see note_todo #79)
#
# The monolithic /api/hosts waits until every provider probe for every
# host has returned. With Webmin / Pulse / slow node-exporter scrapes
# this can take 10+ seconds even with the parallelisation in #85 —
# long enough that the page feels frozen.
#
# The split model:
#   GET /api/hosts/list         — skeleton: curated list + global
#                                 state (active sources, provider
#                                 errors, hub URL). No per-host
#                                 probes. Fast (<200ms).
#   GET /api/hosts/one/{id}     — single host's merged data. Runs
#                                 NE + Webmin probes for THAT host
#                                 only; reuses Beszel / Pulse batch
#                                 maps from a short-lived cache so a
#                                 burst of N parallel calls doesn't
#                                 incur N × batch-probe cost.
#
# Legacy /api/hosts still works (metric scrapers / dashboards that
# want one round-trip to see the whole fleet). The SPA calls the
# split pair.
# ---------------------------------------------------------------------------
_HOST_PROVIDER_CACHE_TTL = 10.0
_host_provider_cache: dict = {"ts": 0.0, "state": None}

# Per-host Webmin result cache. Webmin probes are the slowest link in
# the /api/hosts/one/{id} path (up to 20s each on slow Miniserv); a
# 30s TTL means repeated drawer opens / refresh ticks within half a
# minute skip the probe entirely and reuse the last known-good stats.
# Cache key is the host_id (one Webmin per host — unlike Beszel/Pulse
# which are multi-tenant). Value is the raw dict returned by
# probe_webmin so _merge_one_host can fold it the same way.
_WEBMIN_HOST_CACHE_TTL = 30.0
_webmin_host_cache: dict[str, tuple[float, dict]] = {}


async def _get_host_provider_state(force: bool = False) -> dict:
    """Fetch + cache the provider state needed to merge any host.

    The "batch" providers (Beszel, Pulse) expose one endpoint that
    returns every host in one call, so we memoise them for
    ``_HOST_PROVIDER_CACHE_TTL`` seconds. A burst of /api/hosts/one/{id}
    calls from the SPA hits the cache; settings changes auto-clear
    after the TTL expires (no explicit invalidation needed).
    """
    from logic import beszel as _beszel
    from logic import pulse as _pulse

    now = time.time()
    cached = _host_provider_cache.get("state")
    if (not force and cached
            and (now - _host_provider_cache.get("ts", 0.0)) < _HOST_PROVIDER_CACHE_TTL):
        return cached

    raw_source = (get_setting("host_stats_source", "") or "").strip()
    if not raw_source:
        raw_source = ("node_exporter"
                      if (get_setting("node_exporter_enabled", "false") or "false").lower() == "true"
                      else "")
    active = {
        s.strip().lower()
        for s in raw_source.split(",")
        if s.strip() and s.strip().lower() != "none"
    }

    errors: dict[str, str] = {}
    beszel_map: dict[str, dict] = {}
    if "beszel" in active:
        hub_url = get_setting("beszel_hub_url", "") or ""
        ident = get_setting("beszel_identity", "") or ""
        passw = get_setting("beszel_password", "") or ""
        verify = (get_setting("beszel_verify_tls", "true") or "true").lower() == "true"
        if hub_url and ident and passw:
            r = await _beszel.probe_hub(hub_url, ident, passw, verify_tls=verify)
            if r.get("error"):
                errors["beszel"] = r["error"]
            beszel_map = r.get("systems") or {}
        else:
            errors["beszel"] = "missing url / identity / password"

    pulse_map: dict[str, dict] = {}
    if "pulse" in active:
        pulse_url = get_setting("pulse_url", "") or ""
        pulse_token = get_setting("pulse_token", "") or ""
        verify = (get_setting("pulse_verify_tls", "true") or "true").lower() == "true"
        if pulse_url and pulse_token:
            r = await _pulse.probe_pulse(pulse_url, pulse_token, verify_tls=verify)
            if r.get("error"):
                errors["pulse"] = r["error"]
            pulse_map = r.get("hosts") or {}
        else:
            errors["pulse"] = "missing url / token"

    webmin_creds_ok = False
    webmin_user = ""
    webmin_password = ""
    webmin_verify = False
    webmin_aliases: dict[str, str] = {}
    if "webmin" in active:
        webmin_user = get_setting("webmin_user", "") or ""
        webmin_password = get_setting("webmin_password", "") or ""
        webmin_verify = (get_setting("webmin_verify_tls", "false") or "false").lower() == "true"
        try:
            wm_aliases_raw = json.loads(get_setting("webmin_aliases", "{}") or "{}")
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

    state = {
        "active":           active,
        "beszel_map":       beszel_map,
        "pulse_map":        pulse_map,
        "errors":           errors,
        "webmin_user":      webmin_user,
        "webmin_password":  webmin_password,
        "webmin_verify":    webmin_verify,
        "webmin_creds_ok":  webmin_creds_ok,
        "webmin_aliases":   webmin_aliases,
    }
    _host_provider_cache["ts"] = now
    _host_provider_cache["state"] = state
    return state


async def _merge_one_host(h: dict, state: dict) -> tuple[dict, list[str]]:
    """Merge one curated host with provider data. Runs NE + Webmin
    probes inline for THIS host only; Beszel/Pulse lookups hit the
    cached batch maps. Returns (merged_dict, providers_hit)."""
    from logic import node_exporter as _ne
    from logic import pulse as _pulse
    from logic import webmin as _webmin

    merged: dict = {}
    providers_hit: list[str] = []
    active = state["active"]

    # Pulse — coarse fallback layer.
    pulse_key = h.get("pulse_name") or h.get("id") or ""
    if "pulse" in active and pulse_key:
        pstats = _pulse.lookup(state["pulse_map"], pulse_key)
        if pstats:
            _merge_best(merged, pstats)
            providers_hit.append("pulse")

    # Beszel.
    beszel_key = h.get("beszel_name") or h.get("id") or ""
    if "beszel" in active and beszel_key:
        bstats = state["beszel_map"].get(beszel_key)
        if bstats:
            _merge_best(merged, bstats)
            providers_hit.append("beszel")

    # Node-exporter (per-host probe).
    if "node_exporter" in active and h.get("ne_url"):
        try:
            async with httpx.AsyncClient(verify=False, timeout=10.0) as ne_client:
                stats = await _ne.probe_node(ne_client, h["ne_url"])
            _merge_best(merged, stats or {})
            if stats and not stats.get("exporter_error"):
                providers_hit.append("node_exporter")
        except Exception as e:  # noqa: BLE001
            print(f"[hosts] NE probe failed for {h.get('id')!r}: {e}")

    # Webmin (per-host probe, 20s outer budget matching api_hosts).
    # Consults a 30s per-host result cache — Webmin is the slowest
    # provider, so burst-refreshes (e.g. the SPA fanning out
    # /api/hosts/one/{id} twice in a minute) skip the repeat probe.
    if "webmin" in active and state["webmin_creds_ok"]:
        wm_url = state["webmin_aliases"].get(h["id"]) or h.get("webmin_url") or ""
        if wm_url:
            now = time.time()
            cached = _webmin_host_cache.get(h["id"])
            if cached and (now - cached[0]) < _WEBMIN_HOST_CACHE_TTL:
                result = cached[1]
            else:
                try:
                    result = await asyncio.wait_for(
                        _webmin.probe_webmin(
                            wm_url, state["webmin_user"], state["webmin_password"],
                            verify_tls=state["webmin_verify"],
                            active_sources=active,
                        ),
                        timeout=20.0,
                    )
                except asyncio.TimeoutError:
                    result = {"hosts": {}, "error": "webmin probe timeout after 20s"}
                except Exception as e:  # noqa: BLE001
                    result = {"hosts": {}, "error": f"webmin probe failed: {e}"}
                # Only cache successful probes (non-empty hosts map).
                # Caching a timeout/auth-fail would mask recovery for
                # a full TTL window — we'd rather retry quickly.
                if (result.get("hosts") or {}):
                    _webmin_host_cache[h["id"]] = (now, result)
            hosts_map = result.get("hosts") or {}
            if hosts_map:
                stats = next(iter(hosts_map.values()))
                _merge_best(merged, stats)
                providers_hit.append("webmin")

    return merged, providers_hit


def _shape_host_api_row(
    h: dict,
    merged: dict,
    providers_hit: list[str],
    any_provider_enabled: bool = True,
) -> dict:
    """Shape a (curated_host, merged_stats) pair into the wire format.

    ``any_provider_enabled`` — false when NO provider is enabled
    globally (``state.active`` is empty). In that case a host with
    provider fields mapped can't be probed at all, so we report
    `status: 'unconfigured'` instead of `'unknown'` — grey dot, no
    "no data" banner, because there's literally nothing OmniGrid
    could have done. Operators see a clear "configure a provider"
    path instead of a false red alert.
    """
    s = merged or {}
    return {
        "id":              h["id"],
        "name":            h["id"],
        "host":            h["id"],
        "label":           h.get("label") or h["id"],
        "beszel_name":     h.get("beszel_name") or "",
        "pulse_name":      h.get("pulse_name") or "",
        "ne_url":          h.get("ne_url") or "",
        "url":             h.get("url") or "",
        "icon":            h.get("icon") or "",
        "providers":       providers_hit or [],
        # Status precedence:
        #   1. Explicit Beszel / Pulse status when the provider
        #      contributed (canonical "up/down/paused" signal).
        #   2. "up" when any provider returned data at all.
        #   3. "unconfigured" when EITHER the curated row has NO
        #      provider fields set OR no provider is enabled
        #      globally — nothing to probe, grey (not red).
        #   4. "unknown" when providers ARE mapped AND globally
        #      active, but none answered. Frontend escalates this
        #      to red (real outage signal).
        "status":          (
            s.get("beszel_status")
            or s.get("pulse_status")
            or ("up" if providers_hit else (
                "unconfigured"
                if (not any_provider_enabled) or not (
                    (h.get("beszel_name") or "").strip()
                    or (h.get("pulse_name") or "").strip()
                    or (h.get("webmin_name") or "").strip()
                    or (h.get("ne_url") or "").strip()
                )
                else "unknown"
            ))
        ),
        "docker_node":     h["id"],
        "platform":        s.get("host_platform") or "",
        "os":              s.get("host_os") or "",
        "kernel":          s.get("host_kernel") or "",
        "arch":            s.get("host_arch") or "",
        "agent":           s.get("host_agent") or "",
        "cores":           s.get("host_cores") or s.get("host_threads") or 0,
        "threads":         s.get("host_threads") or 0,
        "cpu_model":       s.get("host_cpu_model") or "",
        "cpu_percent":     s.get("host_cpu_percent") or 0,
        "mem_percent":     s.get("host_mem_percent") or 0,
        "disk_percent":    s.get("host_disk_percent") or 0,
        "mem_used":        s.get("host_mem_used") or 0,
        "mem_total":       s.get("host_mem_total") or 0,
        "disk_used":       s.get("host_disk_used") or 0,
        "disk_total":      s.get("host_disk_total") or 0,
        "mounts":          s.get("mounts") or [],
        "network_ifaces":  s.get("network_ifaces") or [],
        "bandwidth":       s.get("host_bandwidth") or 0,
        "containers":      s.get("host_containers") or 0,
        "uptime_s":        s.get("host_uptime_s") or 0,
        "boot_ts":         s.get("host_boot_ts"),
        "beszel_id":       s.get("beszel_id") or "",
        "beszel_updated":  s.get("beszel_updated") or "",
        "pulse_kind":      s.get("pulse_kind") or "",
        "pulse_vmid":      s.get("pulse_vmid") or 0,
        "pulse_node":      s.get("pulse_node") or "",
        "pulse_status":    s.get("pulse_status") or "",
        "updates_pending":  int(s.get("host_updates_pending") or 0),
        "updates_security": int(s.get("host_updates_security") or 0),
        "custom_number":    h.get("custom_number"),
        # Per-host SSH-disabled flag, exposed so the drawer can hide
        # the SSH card / common-actions panel BEFORE sshStatus has
        # loaded. Reads the hosts_config `ssh.disabled` field — if the
        # operator explicitly opted out, the card never renders.
        "ssh_disabled":     bool((h.get("ssh") or {}).get("disabled", False)),
        # Load averages (primarily node-exporter; Beszel doesn't emit
        # these). Frontend only renders the row when any of the three
        # is > 0.
        "load_1m":          float(s.get("host_load_1m") or 0),
        "load_5m":          float(s.get("host_load_5m") or 0),
        "load_15m":         float(s.get("host_load_15m") or 0),
        # DMI / hardware identity (node-exporter only — Linux /
        # FreeBSD with the DMI collector). Empty strings = no DMI.
        "dmi_vendor":       (s.get("host_dmi_vendor") or ""),
        "dmi_product":      (s.get("host_dmi_product") or ""),
        "dmi_serial":       (s.get("host_dmi_serial") or ""),
        "dmi_bios_version": (s.get("host_dmi_bios_version") or ""),
    }


@app.get("/api/hosts/list")
async def api_hosts_list():
    """Skeleton endpoint — curated host list + global state, NO
    per-host probes. Paired with /api/hosts/one/{id} for progressive
    loading: the SPA paints rows immediately from this response, then
    fans out per-host fetches to fill in the stats.
    """
    curated = _load_hosts_config()
    state = await _get_host_provider_state()
    any_enabled = bool(state["active"])

    hosts = [
        _shape_host_api_row(h, {}, [], any_provider_enabled=any_enabled)
        for h in curated
        if h.get("enabled", True)
    ]
    agg_error = "; ".join(f"{k}: {v}" for k, v in state["errors"].items()) or None
    return {
        "configured":      bool(state["active"]),
        "active":          sorted(state["active"]),
        "error":           agg_error,
        "provider_errors": state["errors"],
        "hub_url":         get_setting("beszel_hub_url", "") or "",
        "hosts":           hosts,
        "curated_count":   len(curated),
        "enabled_count":   sum(1 for h in curated if h.get("enabled", True)),
    }


@app.get("/api/hosts/one/{host_id}")
async def api_hosts_one(host_id: str):
    """Merge ONE curated host with provider data.

    Called N times in parallel by the SPA after /api/hosts/list
    returns the skeleton. The shared Beszel/Pulse cache ensures the
    batch probes run at most once per TTL window.
    """
    curated = _load_hosts_config()
    h = next((x for x in curated if x.get("id") == host_id), None)
    if h is None or not h.get("enabled", True):
        raise HTTPException(404, f"Host not found: {host_id}")
    state = await _get_host_provider_state()
    merged, providers = await _merge_one_host(h, state)
    any_enabled = bool(state["active"])
    return {
        "host": _shape_host_api_row(
            h, merged, providers, any_provider_enabled=any_enabled,
        ),
    }


def _load_hosts_config() -> list[dict]:
    """Parse the ``hosts_config`` JSON setting into a validated list.

    Empty / invalid values return an empty list. Caller treats an empty
    list as "no curated hosts — fall back to auto-discovery where
    applicable."
    """
    raw = get_setting("hosts_config", "") or ""
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    clean: list[dict] = []
    for i, h in enumerate(parsed):
        if not isinstance(h, dict):
            continue
        hid = (h.get("id") or h.get("name") or "").strip()
        if not hid:
            continue
        clean.append({
            "id":          hid,
            "label":       (h.get("label") or hid).strip() or hid,
            "ne_url":      (h.get("ne_url") or "").strip(),
            "beszel_name": (h.get("beszel_name") or "").strip(),
            "pulse_name":  (h.get("pulse_name") or "").strip(),
            # Webmin per-host name — currently unused for lookup (every
            # Webmin install has its own Miniserv URL), but retained so
            # the admin editor has a slot to tag which row a discovered
            # Webmin host maps to. The actual probe URL lives in the
            # webmin_aliases map.
            "webmin_name": (h.get("webmin_name") or "").strip(),
            # Optional external URL the operator picks (e.g. the host's
            # web UI). Rendered as a clickable link in the Hosts view's
            # SYSTEM card, matches Beszel's "+ Add URL" affordance.
            "url":         (h.get("url") or "").strip(),
            # Optional icon override — a slug like "opnsense" (resolved
            # to /img/icons/opnsense.svg) or a full URL. Empty = let
            # the frontend's iconUrlFor() auto-resolve from the host's
            # id / label.
            "icon":        (h.get("icon") or "").strip(),
            # Operator-assigned catalogue number. Used today for sort
            # ordering + grouping in the Hosts view; future scope is
            # the primary key for PersonalSite inventory lookups so
            # hardware / location / NIC metadata can be pulled back in.
            # Empty / invalid values → None so "no number" sorts last.
            "custom_number": _coerce_int(h.get("custom_number")),
            # Per-host SSH override sub-dict. Optional user / port /
            # disabled / host override — the key material itself lives
            # in the GLOBAL ssh_default_private_key setting (V1 scope:
            # single global key). Missing or non-dict values collapse
            # to {} so downstream code can always do dict.get(...).
            "ssh":         _clean_host_ssh(h.get("ssh")),
            "enabled":     bool(h.get("enabled", True)),
        })
    return clean


def _clean_host_ssh(raw: Any) -> dict:
    """Normalise the per-host ``ssh`` sub-dict.

    Accepts only the four keys that make sense at V1 (``user`` / ``port``
    / ``disabled`` / ``host``) and coerces their types. Unknown keys
    are dropped so a malformed import can't smuggle arbitrary fields
    into the persisted JSON. Empty → empty dict, which the SSH module
    treats as "use global defaults".
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    user = str(raw.get("user") or "").strip()
    if user:
        out["user"] = user
    host = str(raw.get("host") or "").strip()
    if host:
        out["host"] = host
    # `fqdn` is an alias for `host` (operator-facing naming). The
    # resolve function reads both, preferring whichever is set;
    # persist as-typed so the editor round-trips the operator's
    # choice.
    fqdn = str(raw.get("fqdn") or "").strip()
    if fqdn:
        out["fqdn"] = fqdn
    port = raw.get("port")
    if port not in (None, "", 0):
        try:
            p = int(port)
            if 1 <= p <= 65535:
                out["port"] = p
        except (TypeError, ValueError):
            pass
    # Per-host password override. Stored in the hosts_config JSON
    # (which already contains other secrets implicitly, e.g. webmin
    # URLs with credentials). The admin-only /api/hosts/config
    # endpoint gates access; /api/hosts/debug masks the ssh sub-dict
    # so per-host passwords don't leak to the debug panel.
    password = str(raw.get("password") or "")
    if password:
        out["password"] = password
    if bool(raw.get("disabled")):
        out["disabled"] = True
    return out


def _slugify_action(title: str) -> str:
    """Derive a stable slug from a user-typed action title.

    Used as the `id` for SSH custom actions when the operator didn't
    supply one explicitly. Kept permissive — lowercase letters /
    numbers / hyphens only, truncated to 40 chars. Collisions aren't
    checked (two actions titled identically will produce the same
    slug — same behaviour as schedule names, operator's problem).
    """
    import re as _re
    s = (title or "").strip().lower()
    s = _re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40] or "action"


def _coerce_int(v) -> Optional[int]:
    """Accept an int, a numeric string, or empty/garbage — return int or None."""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _save_hosts_config(hosts: list[dict]) -> list[dict]:
    """Persist the curated hosts list and return what we saved.

    Rejects bad shapes at the boundary so downstream code can trust the
    result. Duplicates by ``id`` collapse to the last-wins record.
    """
    if not isinstance(hosts, list):
        raise HTTPException(400, "hosts must be a list")
    seen: dict[str, dict] = {}
    for h in hosts:
        if not isinstance(h, dict):
            raise HTTPException(400, "every host entry must be an object")
        hid = (h.get("id") or h.get("name") or "").strip()
        if not hid:
            raise HTTPException(400, "host entry is missing 'id'")
        seen[hid] = {
            "id":            hid,
            "label":         (h.get("label") or hid).strip() or hid,
            "ne_url":        (h.get("ne_url") or "").strip(),
            "beszel_name":   (h.get("beszel_name") or "").strip(),
            "pulse_name":    (h.get("pulse_name") or "").strip(),
            "webmin_name":   (h.get("webmin_name") or "").strip(),
            "url":           (h.get("url") or "").strip(),
            "icon":          (h.get("icon") or "").strip(),
            # Operator-assigned catalogue number. Persisted so the
            # Hosts-view "Custom #" sort + future asset-inventory
            # lookups find the right row. Blank / non-numeric → None
            # via _coerce_int (same path _load_hosts_config uses).
            "custom_number": _coerce_int(h.get("custom_number")),
            # Per-host SSH override block — see _clean_host_ssh for
            # the shape contract. {} when no override is set.
            "ssh":           _clean_host_ssh(h.get("ssh")),
            "enabled":       bool(h.get("enabled", True)),
        }
    ordered = list(seen.values())
    set_setting("hosts_config", json.dumps(ordered))
    return ordered


@app.get("/api/hosts/config")
async def api_hosts_config_get(_u: auth.User = Depends(auth.require_admin)):
    """Admin-only: return the curated host list used by the Hosts tab."""
    return {"hosts": _load_hosts_config()}


@app.post("/api/hosts/config")
async def api_hosts_config_set(
    body: dict,
    _u: auth.User = Depends(auth.require_admin),
):
    """Admin-only: replace the curated host list.

    Full-replace rather than per-row CRUD — the list is small (one row
    per physical host) and the UI saves the whole table on each edit.
    Keeps the backend state machine trivial.
    """
    hosts = body.get("hosts")
    saved = _save_hosts_config(hosts if isinstance(hosts, list) else [])
    _cache["ts"] = 0  # force next gather to pick up new mappings
    return {"hosts": saved, "count": len(saved)}


@app.post("/api/hosts/test")
async def api_hosts_test(
    body: dict,
    _u: auth.User = Depends(auth.require_admin),
):
    """Admin-only: probe each provider for a single host-config row.

    Body: ``{beszel_name, pulse_name, ne_url}`` — any field blank is
    skipped. Returns ``{beszel: {ok, detail}, pulse: {...},
    node_exporter: {...}}`` with per-provider pass/fail + a short
    description the UI shows beside the row.

    Shares probes with the live Hosts-view code path so a pass here
    guarantees the main page will render data for this host.
    """
    from logic import beszel as _beszel
    from logic import pulse as _pulse
    from logic import node_exporter as _ne
    from logic import webmin as _webmin

    beszel_name = (body.get("beszel_name") or "").strip()
    pulse_name = (body.get("pulse_name") or "").strip()
    ne_url = (body.get("ne_url") or "").strip()
    # webmin_url takes precedence over the row's webmin_aliases entry —
    # per-row test fields beat global settings, same pattern as ne_url.
    webmin_url = (body.get("webmin_url") or "").strip().rstrip("/")
    row_id = (body.get("host_id") or "").strip()
    if not webmin_url and row_id:
        try:
            aliases = json.loads(get_setting("webmin_aliases", "{}") or "{}")
            if isinstance(aliases, dict):
                webmin_url = str(aliases.get(row_id, "") or "").strip().rstrip("/")
        except ValueError:
            webmin_url = ""
    out = {
        "beszel": {"ok": False, "skipped": True, "detail": "not set"},
        "pulse":  {"ok": False, "skipped": True, "detail": "not set"},
        "node_exporter": {"ok": False, "skipped": True, "detail": "not set"},
        "webmin": {"ok": False, "skipped": True, "detail": "not set"},
    }

    if beszel_name:
        hub_url = get_setting("beszel_hub_url", "") or ""
        ident = get_setting("beszel_identity", "") or ""
        passw = get_setting("beszel_password", "") or ""
        verify = (get_setting("beszel_verify_tls", "true") or "true").lower() == "true"
        if hub_url and ident and passw:
            r = await _beszel.probe_hub(hub_url, ident, passw, verify_tls=verify)
            if r.get("error"):
                out["beszel"] = {"ok": False, "skipped": False,
                                 "detail": f"hub error: {r['error']}"}
            elif beszel_name in (r.get("systems") or {}):
                st = r["systems"][beszel_name]
                mem = st.get("host_mem_total") or 0
                disk = st.get("host_disk_total") or 0
                out["beszel"] = {
                    "ok": True, "skipped": False,
                    "detail": (f"matched · mem={mem // (1024**3) if mem else '?'}"
                               + f" GB · disk={disk // (1024**3) if disk else '?'} GB"),
                }
            else:
                names = sorted((r.get("systems") or {}).keys(), key=str.lower)
                hint = ", ".join(names[:3])
                if len(names) > 3: hint += f" (+{len(names)-3} more)"
                out["beszel"] = {"ok": False, "skipped": False,
                                 "detail": f"no match in hub. Known: {hint or 'none'}"}
        else:
            out["beszel"] = {"ok": False, "skipped": False,
                             "detail": "Beszel creds not configured"}

    if pulse_name:
        pulse_url = get_setting("pulse_url", "") or ""
        pulse_tok = get_setting("pulse_token", "") or ""
        verify = (get_setting("pulse_verify_tls", "true") or "true").lower() == "true"
        if pulse_url and pulse_tok:
            r = await _pulse.probe_pulse(pulse_url, pulse_tok, verify_tls=verify)
            if r.get("error"):
                out["pulse"] = {"ok": False, "skipped": False,
                                "detail": f"pulse error: {r['error']}"}
            elif _pulse.lookup(r.get("hosts") or {}, pulse_name):
                st = _pulse.lookup(r.get("hosts") or {}, pulse_name)
                kind = st.get("pulse_kind") or "host"
                out["pulse"] = {"ok": True, "skipped": False,
                                "detail": f"matched ({kind})"}
            else:
                names = sorted((r.get("hosts") or {}).keys(), key=str.lower)
                hint = ", ".join(names[:3])
                if len(names) > 3: hint += f" (+{len(names)-3} more)"
                out["pulse"] = {"ok": False, "skipped": False,
                                "detail": f"no match in Pulse. Known: {hint or 'none'}"}
        else:
            out["pulse"] = {"ok": False, "skipped": False,
                            "detail": "Pulse creds not configured"}

    if ne_url:
        try:
            async with httpx.AsyncClient(verify=False, timeout=8.0) as client:
                stats = await _ne.probe_node(client, ne_url)
        except Exception as e:
            stats = {"exporter_error": str(e)}
        if stats.get("exporter_error"):
            out["node_exporter"] = {"ok": False, "skipped": False,
                                    "detail": stats["exporter_error"]}
        else:
            mem = stats.get("host_mem_total") or 0
            out["node_exporter"] = {
                "ok": True, "skipped": False,
                "detail": f"reachable · mem={mem // (1024**3) if mem else '?'} GB",
            }

    if webmin_url:
        user = get_setting("webmin_user", "") or ""
        passw = get_setting("webmin_password", "") or ""
        verify = (get_setting("webmin_verify_tls", "false") or "false").lower() == "true"
        if not user or not passw:
            out["webmin"] = {"ok": False, "skipped": False,
                             "detail": "Webmin creds not configured"}
        else:
            r = await _webmin.probe_webmin(
                webmin_url, user, passw, verify_tls=verify, timeout=8.0,
            )
            if r.get("error") and not r.get("hosts"):
                out["webmin"] = {"ok": False, "skipped": False,
                                 "detail": f"webmin error: {r['error']}"}
            elif r.get("hosts"):
                host_key, stats = next(iter(r["hosts"].items()))
                pending = stats.get("host_updates_pending") or 0
                security = stats.get("host_updates_security") or 0
                detail = (f"matched · {host_key} · "
                          f"{pending} updates ({security} sec)")
                if r.get("partial_errors"):
                    detail += f" · {len(r['partial_errors'])} module(s) failed"
                out["webmin"] = {"ok": True, "skipped": False, "detail": detail}
            else:
                out["webmin"] = {"ok": False, "skipped": False,
                                 "detail": "webmin responded with no parseable host"}

    return out


@app.get("/api/hosts/discover")
async def api_hosts_discover(_u: auth.User = Depends(auth.require_admin)):
    """Admin-only: pull every known host name from each enabled
    provider. Used by the Admin → Hosts editor as autocomplete source
    so operators don't have to type provider-side names from memory.

    Returns ``{beszel: [names], pulse: [names], errors: {...}}``. Empty
    lists mean either the provider is disabled or its credentials
    aren't set — the UI treats both the same.
    """
    from logic import beszel as _beszel
    from logic import pulse as _pulse
    errors: dict[str, str] = {}

    beszel_names: list[str] = []
    hub_url = get_setting("beszel_hub_url", "") or ""
    b_id = get_setting("beszel_identity", "") or ""
    b_pw = get_setting("beszel_password", "") or ""
    if hub_url and b_id and b_pw:
        verify = (get_setting("beszel_verify_tls", "true") or "true").lower() == "true"
        r = await _beszel.probe_hub(hub_url, b_id, b_pw, verify_tls=verify)
        if r.get("error"):
            errors["beszel"] = r["error"]
        else:
            beszel_names = sorted((r.get("systems") or {}).keys(), key=str.lower)

    pulse_names: list[str] = []
    pulse_url = get_setting("pulse_url", "") or ""
    pulse_tok = get_setting("pulse_token", "") or ""
    if pulse_url and pulse_tok:
        verify = (get_setting("pulse_verify_tls", "true") or "true").lower() == "true"
        r = await _pulse.probe_pulse(pulse_url, pulse_tok, verify_tls=verify)
        if r.get("error"):
            errors["pulse"] = r["error"]
        else:
            pulse_names = sorted((r.get("hosts") or {}).keys(), key=str.lower)

    # Webmin discovery — one Miniserv per host, so instead of a flat
    # list of names we surface the URL → extracted-hostname map from
    # ``webmin_aliases``. Each alias URL gets probed once so the
    # hostname returned by the target's system-status module can be
    # offered as the ``webmin_name`` autocomplete value.
    webmin_names: list[str] = []
    try:
        wm_aliases_raw = json.loads(get_setting("webmin_aliases", "{}") or "{}")
    except ValueError:
        wm_aliases_raw = {}
    wm_urls = (
        sorted({str(v).strip().rstrip("/")
                for v in wm_aliases_raw.values() if str(v).strip()})
        if isinstance(wm_aliases_raw, dict) else []
    )
    wm_user = get_setting("webmin_user", "") or ""
    wm_pass = get_setting("webmin_password", "") or ""
    if wm_urls and wm_user and wm_pass:
        from logic import webmin as _webmin
        verify = (get_setting("webmin_verify_tls", "false") or "false").lower() == "true"
        wm_results = await asyncio.gather(*(
            _webmin.probe_webmin(u, wm_user, wm_pass, verify_tls=verify,
                                 timeout=8.0)
            for u in wm_urls
        ), return_exceptions=False)
        seen: set[str] = set()
        failed = 0
        for r in wm_results:
            if r.get("hosts"):
                for k in r["hosts"]:
                    if k:
                        seen.add(k)
            elif r.get("error"):
                failed += 1
        webmin_names = sorted(seen, key=str.lower)
        if failed and not webmin_names:
            errors["webmin"] = f"{failed} Webmin URL(s) failed to probe"

    return {
        "beszel": beszel_names,
        "pulse":  pulse_names,
        "webmin": webmin_names,
        "errors": errors,
    }


@app.get("/api/hosts/debug")
async def api_hosts_debug(
    id: str = "",
    _u: auth.User = Depends(auth.require_admin),
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

    curated = _load_hosts_config()
    record = next((h for h in curated if h["id"] == id), None)
    if record is None:
        raise HTTPException(404, f"no curated host with id={id!r}")

    # Which providers are live? Same derivation as api_hosts.
    raw_source = (get_setting("host_stats_source", "") or "").strip()
    if not raw_source:
        raw_source = ("node_exporter"
                      if (get_setting("node_exporter_enabled", "false") or "false").lower() == "true"
                      else "")
    active = {
        s.strip().lower()
        for s in raw_source.split(",")
        if s.strip() and s.strip().lower() != "none"
    }

    providers_raw: dict[str, Any] = {
        "pulse": None, "beszel": None, "node_exporter": None, "webmin": None,
    }
    providers_normalized: dict[str, Any] = {
        "pulse": None, "beszel": None, "node_exporter": None, "webmin": None,
    }

    # ---- Beszel --------------------------------------------------
    if "beszel" in active and record.get("beszel_name"):
        hub_url = get_setting("beszel_hub_url", "") or ""
        ident = get_setting("beszel_identity", "") or ""
        passw = get_setting("beszel_password", "") or ""
        verify = (get_setting("beszel_verify_tls", "true") or "true").lower() == "true"
        if hub_url and ident and passw:
            try:
                async with httpx.AsyncClient(verify=verify, timeout=15.0) as client:
                    token = await _beszel._get_token(client, hub_url, ident, passw)
                    try:
                        records = await _beszel._fetch_systems(client, hub_url, token)
                    except PermissionError:
                        token = await _beszel._get_token(
                            client, hub_url, ident, passw, force_refresh=True,
                        )
                        records = await _beszel._fetch_systems(client, hub_url, token)
                    latest_stats: dict = {}
                    try:
                        latest_stats = await _beszel._fetch_latest_stats(
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
        pulse_url = get_setting("pulse_url", "") or ""
        pulse_tok = get_setting("pulse_token", "") or ""
        verify = (get_setting("pulse_verify_tls", "true") or "true").lower() == "true"
        if pulse_url and pulse_tok:
            try:
                async with httpx.AsyncClient(verify=verify, timeout=15.0) as client:
                    state = await _pulse._fetch_state(client, pulse_url, pulse_tok)
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
                    pve = state.get("pve") if isinstance(state.get("pve"), dict) else {}
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
        url_canonical = _ne._normalise_ne_url(url_input)
        try:
            async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
                r = await client.get(url_canonical)
                r.raise_for_status()
                text = r.text
                stats = await _ne.probe_node(client, url_input)
            lines = text.splitlines()
            # Cap the sample — a loaded node-exporter can emit thousands
            # of metric lines; operators want a taste, not a dump.
            providers_raw["node_exporter"] = {
                "url_input":     url_input,
                "url_canonical": url_canonical,
                "size_bytes":    len(text),
                "line_count":    len(lines),
                "sample_lines":  lines[:80],
                # Last 5 host_net_samples rows for this host. Lets an
                # operator confirm the NE-net fallback sampler is
                # filling the series at the expected cadence; if this
                # is empty but the exporter returns non-zero rx/tx
                # totals, the sampler hasn't run yet (first 5-min tick)
                # or every delta has been rejected by sanity bounds.
                "recent_net_samples": _host_net_sampler.last_samples(record["id"], limit=5),
            }
            providers_normalized["node_exporter"] = stats
        except Exception as e:
            providers_raw["node_exporter"] = {"_error": str(e)}

    # ---- Webmin --------------------------------------------------
    if "webmin" in active:
        try:
            wm_aliases = json.loads(get_setting("webmin_aliases", "{}") or "{}")
            if not isinstance(wm_aliases, dict):
                wm_aliases = {}
        except ValueError:
            wm_aliases = {}
        wm_url = (wm_aliases.get(record["id"]) or "").strip().rstrip("/")
        user = get_setting("webmin_user", "") or ""
        passw = get_setting("webmin_password", "") or ""
        verify = (get_setting("webmin_verify_tls", "false") or "false").lower() == "true"
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
                    wm_url, user, passw, verify_tls=verify, timeout=10.0,
                    active_sources=active,
                )
                providers_raw["webmin"] = {
                    "url":            wm_url,
                    "hosts_keys":     sorted((r.get("hosts") or {}).keys()),
                    "partial_errors": r.get("partial_errors") or [],
                    "error":          r.get("error"),
                }
                if r.get("hosts"):
                    providers_normalized["webmin"] = next(iter(r["hosts"].values()))
            except Exception as e:
                providers_raw["webmin"] = {"_error": str(e)}

    # ---- Merged (best-of) ----------------------------------------
    merged: dict = {}
    for src in ("pulse", "beszel", "node_exporter", "webmin"):
        stats = providers_normalized.get(src)
        if stats:
            _merge_best(merged, stats)

    # ---- Rendered — what /api/hosts would return for this host ---
    try:
        live = await api_hosts()
        rendered = next(
            (h for h in (live.get("hosts") or []) if h.get("id") == id),
            None,
        )
    except Exception as e:
        rendered = {"_error": str(e)}

    return {
        "host_record":          record,
        "active_providers":     sorted(active),
        "providers_raw":        providers_raw,
        "providers_normalized": providers_normalized,
        "merged":               merged,
        "rendered":             rendered,
    }


# ============================================================================
# SSH console — admin-only remote-command runner for the host drawer.
#
# Surface:
#   GET  /api/hosts/{host_id}/ssh/status  — resolved connection params
#   POST /api/hosts/{host_id}/ssh/test    — runs `whoami` with a short timeout
#   POST /api/hosts/{host_id}/ssh/run     — body {command, dry_run}
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
    try:
        with db_conn() as c:
            c.execute(
                "INSERT INTO history "
                "(ts, op_type, target_name, target_id, target_stack, "
                " status, duration, events, error, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
    _admin: auth.User = Depends(auth.require_admin),
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
    _admin: auth.User = Depends(auth.require_admin),
):
    """Admin-only: run `whoami` on the host to verify connectivity.

    Persists a history row (``op_type='ssh_run'``) so repeated failed
    tests are visible in the audit trail. Body is ignored — everything
    is keyed off the persisted settings + curated hosts_config row.
    """
    from logic import ssh as _ssh
    result = await _ssh.test_connection(host_id, _load_hosts_config())
    actor = getattr(request.state, "user", None)
    actor_name = actor.username if actor else "unknown"
    _ssh_write_audit_row(
        op_id=uuid.uuid4().hex[:8],
        actor=actor_name,
        host_id=host_id,
        command="whoami  # ssh test",
        result=result,
    )
    return result


@app.post("/api/hosts/{host_id}/ssh/run")
async def api_ssh_run(
    host_id: str,
    body: dict,
    request: Request,
    _admin: auth.User = Depends(auth.require_admin),
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
    actor_name = actor.username if actor else "unknown"
    _ssh_write_audit_row(
        op_id=uuid.uuid4().hex[:8],
        actor=actor_name,
        host_id=host_id,
        command=command,
        result=result,
    )
    return result


@app.get("/api/hosts/history")
async def api_hosts_history(system_id: str, hours: int = 1, host_id: str = ""):
    """Return time-series stats for one Beszel system.

    Powers the Hosts tab's per-row charts (CPU / Memory / Disk / Net).
    The system_id is Beszel's PocketBase record id — the frontend pulls
    it off the host row returned by :func:`api_hosts`.

    ``host_id`` is the OmniGrid curated hosts_config id for the same
    machine. When supplied, the Beszel history layer uses it as a
    fallback key to fill in ``nr`` / ``ns`` from ``host_net_samples``
    (populated by ``logic.host_net_sampler``) when the Beszel agent's
    NIC tracking is disabled and every point's nr/ns is zero. Optional;
    omitting it keeps the legacy Beszel-only behaviour.
    """
    from logic import beszel as _beszel
    hub_url = get_setting("beszel_hub_url", "") or ""
    ident = get_setting("beszel_identity", "") or ""
    passw = get_setting("beszel_password", "") or ""
    verify = (get_setting("beszel_verify_tls", "true") or "true").lower() == "true"
    if not (hub_url and ident and passw):
        return {"series": [], "error": "Beszel not configured"}
    h = max(1, min(168, int(hours)))
    return await _beszel.fetch_system_history(
        hub_url, ident, passw, system_id, hours=h, verify_tls=verify,
        host_id=(host_id.strip() or None),
    )


@app.get("/api/auth/providers")
async def api_auth_providers():
    """Public endpoint: advertises which login paths are live. The login
    page queries this before rendering the SSO button so unconfigured
    deployments don't show a dead button that 503s.
    """
    return {
        "local": True,
        "oidc": oidc.is_configured(),
    }


@app.post("/api/notify-test")
async def api_notify_test(_admin: auth.User = Depends(auth.require_admin)):
    await notify("🔔 OmniGrid test", "Notifications are wired up correctly!", "success")
    return {"status": "sent"}


@app.get("/api/healthz")
async def healthz():
    # Re-read VERSION.txt per request so operator edits on the server
    # (e.g. hand-bumping MAJOR/MINOR) show up without restarting the
    # container. File is tiny — a couple-microsecond stat+read each call.
    #
    # The container healthcheck only cares about HTTP 200 vs non-200, so
    # we intentionally keep returning 200 when config is broken — that
    # way Swarm doesn't crash-loop the task and the config-error page
    # stays reachable for the operator. The `ok` and `config_error`
    # fields let any JSON caller (Grafana, Uptime Kuma) distinguish
    # healthy from degraded.
    return {
        "ok": _db.DB_PATH_ERROR is None,
        "version": read_version(),
        "cache_age": int(time.time() - _cache["ts"]) if _cache["ts"] else None,
        "config_error": _db.DB_PATH_ERROR,
    }


@app.get("/api/version")
async def api_version():
    return {"version": read_version()}


# ----------------------------------------------------------------------------
# Topbar weather widget — proxies an Open-Meteo-compatible instance so
# the browser dodges CORS and the same coordinate pair gets cached
# across tabs / reloads.
#
# URL is stored in the DB ``settings`` table under ``open_meteo_url``
# and is admin-authoritative (Admin → Notifications). There is NO
# hardcoded fallback — leaving the setting blank disables the weather
# endpoint entirely (returns ``{configured: false}``) so the operator
# isn't silently forwarded to api.open-meteo.com without opting in.
# ----------------------------------------------------------------------------
def _open_meteo_url() -> str:
    """Read the weather-upstream URL from settings.

    Returns the stored URL (trailing slash stripped) or the empty
    string when unset. Callers must treat `""` as "not configured"
    rather than falling back to a default.
    """
    return (get_setting("open_meteo_url", "") or "").strip().rstrip("/")

_weather_cache: dict[tuple[float, float], tuple[float, dict]] = {}
_WEATHER_CACHE_TTL = 600.0  # 10 minutes — weather changes slowly

# WMO code → (short description, icon slug). Backend owns the mapping
# so i18n of condition strings has ONE source of truth.
_WMO_CODES: dict[int, tuple[str, str]] = {
    0:  ("Clear",            "sun"),
    1:  ("Mainly clear",     "sun"),
    2:  ("Partly cloudy",    "cloud-sun"),
    3:  ("Cloudy",           "cloud"),
    45: ("Fog",              "fog"),
    48: ("Freezing fog",     "fog"),
    51: ("Light drizzle",    "drizzle"),
    53: ("Drizzle",          "drizzle"),
    55: ("Heavy drizzle",    "drizzle"),
    56: ("Freezing drizzle", "sleet"),
    57: ("Freezing drizzle", "sleet"),
    61: ("Light rain",       "rain"),
    63: ("Rain",             "rain"),
    65: ("Heavy rain",       "rain"),
    66: ("Freezing rain",    "sleet"),
    67: ("Freezing rain",    "sleet"),
    71: ("Light snow",       "snow"),
    73: ("Snow",             "snow"),
    75: ("Heavy snow",       "snow"),
    77: ("Snow grains",      "snow"),
    80: ("Rain showers",     "rain"),
    81: ("Rain showers",     "rain"),
    82: ("Heavy showers",    "rain"),
    85: ("Snow showers",     "snow"),
    86: ("Snow showers",     "snow"),
    95: ("Thunderstorm",     "thunder"),
    96: ("Thunder + hail",   "thunder"),
    99: ("Thunder + hail",   "thunder"),
}


@app.get("/api/weather")
async def api_weather(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    label: str = "",
):
    """Fetch current conditions from Open-Meteo for one lat/lon.

    Caller persists label + coords in localStorage; this endpoint is
    stateless apart from an in-memory 10-min cache keyed by (lat, lon).
    Network errors degrade to ``{configured, error}`` so the topbar
    never breaks when the upstream is unreachable.
    """
    if lat is None or lon is None:
        return {"configured": False}
    upstream = _open_meteo_url()
    if not upstream:
        # Admin → Notifications stores `open_meteo_url`; blank disables
        # the widget entirely rather than forwarding to a hardcoded
        # public endpoint the operator didn't opt into.
        return {
            "configured": False,
            "error": "open_meteo_url not configured",
            "label": label,
        }
    # Quantise to 2 decimals so minor coord differences for the same
    # city hit one cache entry.
    key = (round(float(lat), 2), round(float(lon), 2))
    now = time.time()
    cached = _weather_cache.get(key)
    if cached and (now - cached[0]) < _WEATHER_CACHE_TTL:
        body = dict(cached[1])
        body["label"] = label or body.get("label") or ""
        body["cached"] = True
        return body

    params = {
        "latitude":  str(key[0]),
        "longitude": str(key[1]),
        "current":   "temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m",
        "timezone":  "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(upstream, params=params)
            r.raise_for_status()
            j = r.json() or {}
    except Exception as e:
        return {"configured": True, "error": str(e), "label": label}

    cur = j.get("current") or {}
    code = int(cur.get("weather_code") or 0)
    desc, icon = _WMO_CODES.get(code, ("Unknown", "cloud"))
    body = {
        "configured":  True,
        "label":       label,
        "temp_c":      cur.get("temperature_2m"),
        "humidity":    cur.get("relative_humidity_2m"),
        "wind_kmh":    cur.get("wind_speed_10m"),
        "code":        code,
        "condition":   desc,
        "icon":        icon,
        "provider":    "open-meteo",
        "upstream":    upstream,
        "fetched_at":  int(now),
    }
    _weather_cache[key] = (now, body)
    return body


# ============================================================================
# App logs — in-memory ring buffer of recent stdout/stderr lines.
# Admin-only. Frontend polls /api/logs?since=<ts> to incrementally
# fetch new lines; DELETE clears the buffer (does not affect Docker logs).
# Buffer lives in logic/logs.py; the tee is installed at module-import
# time so uvicorn's own lines are captured too.
# ============================================================================
@app.get("/api/logs")
async def api_logs(
    limit: int = 500,
    since: float = 0.0,
    _admin: auth.User = Depends(auth.require_admin),
):
    # Clamp limit to a sane upper bound so a misconfigured client can't
    # pull the whole buffer repeatedly at poll rate.
    limit = max(1, min(int(limit), _logs.MAX_LINES))
    return {
        "logs": _logs.get_recent(limit=limit, since_ts=float(since)),
        "size": _logs.size(),
        "max": _logs.MAX_LINES,
    }


@app.delete("/api/logs")
async def api_logs_clear(_admin: auth.User = Depends(auth.require_admin)):
    _logs.clear()
    return {"ok": True}


# ============================================================================
# Auth routes (step 1: local login, logout, one-shot bootstrap, /api/me).
# Registered here — above the StaticFiles catch-all — per CLAUDE.md.
# ============================================================================
@app.post("/api/local-auth/login")
async def api_local_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    ip = auth._client_ip(request)
    auth.rate_limit_check(ip)
    with db_conn() as c:
        u = auth.get_user_by_username(c, username)
        if not u or u.auth_source != "local" or u.disabled or not auth.verify_password(password, _get_user_password_hash(c, u.id)):
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=401, detail="Invalid credentials")
        auth.rate_limit_clear(ip)
        auth.touch_last_login(c, u.id)
        cookie_value, expires_at = auth.create_session(
            c, u.id, ip, request.headers.get("user-agent"),
        )
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({"username": u.username, "role": u.role, "source": u.auth_source})
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    return resp


def _get_user_password_hash(conn, user_id: int):
    """Fetch password_hash directly — not exposed via the User dataclass."""
    r = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
    return r["password_hash"] if r else None


@app.post("/api/local-auth/change-password")
async def api_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: auth.User = Depends(auth.current_user),
):
    """Let a logged-in local user rotate their own password.

    - Authentik users are directed to Authentik (no password stored here).
    - Invalidates every other session for this user; keeps the caller's.
    - Rate-limited via the shared login limiter so brute-forcing the current
      password from a compromised session is bounded.
    """
    if user.auth_source != "local":
        raise HTTPException(
            status_code=400,
            detail="Authentik users must change their password in Authentik.",
        )
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="New passwords do not match.")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be 8+ characters.")
    if new_password == current_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current one.")

    ip = auth._client_ip(request)
    auth.rate_limit_check(ip)

    with db_conn() as c:
        stored = _get_user_password_hash(c, user.id)
        if not auth.verify_password(current_password, stored):
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=401, detail="Current password is incorrect.")
        auth.rate_limit_clear(ip)
        # Preserve the caller's own session while invalidating others.
        current_token_id = None
        cookie = request.cookies.get(auth.COOKIE_NAME)
        if cookie:
            current_token_id = auth.parse_session_cookie(cookie)
        auth.change_password(c, user.id, new_password, keep_session_token=current_token_id)

    return {"status": "ok"}


@app.post("/api/local-auth/logout")
async def api_local_logout(request: Request):
    cookie = request.cookies.get(auth.COOKIE_NAME)
    if cookie:
        token_id = auth.parse_session_cookie(cookie)
        if token_id:
            with db_conn() as c:
                auth.delete_session(c, token_id)
    resp = JSONResponse({"ok": True})
    auth.clear_session_cookies(resp, request)
    return resp


@app.post("/api/local-auth/bootstrap")
async def api_local_bootstrap(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """One-shot: only works while the users table is empty.

    Lets operators claim the first admin on a fresh install without having
    to set BOOTSTRAP_ADMIN_* env vars. Self-disables as soon as any user
    exists — every subsequent call returns 403.
    """
    ip = auth._client_ip(request)
    auth.rate_limit_check(ip)
    with db_conn() as c:
        if auth.count_users(c) > 0:
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=403, detail="Bootstrap already consumed")
        if not username or not password or len(password) < 8:
            raise HTTPException(status_code=400, detail="Username required; password must be 8+ chars")
        u = auth.create_user(c, username, None, password, "admin", "local")
        auth.touch_last_login(c, u.id)
        cookie_value, expires_at = auth.create_session(
            c, u.id, ip, request.headers.get("user-agent"),
        )
    csrf = auth.generate_csrf_token()
    resp = JSONResponse(
        {"ok": True, "username": u.username, "role": u.role},
        status_code=201,
    )
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    return resp


@app.get("/api/me")
async def api_me(request: Request):
    """Return the current identity if any. Auth-optional — returns
    {authenticated: false} instead of 401 so the SPA can decide whether
    to redirect to /login. For real users, includes the full profile
    (display_name, bio, avatar_url, timestamps) so the profile page can
    render from a single fetch.
    """
    user = getattr(request.state, "user", None)
    if not user:
        return {"authenticated": False}
    # API-token "users" have negative ids (see _resolve_user) — skip the
    # profile read for them, there's nothing in the users table.
    profile = None
    if user.id >= 0:
        with db_conn() as c:
            profile = auth.get_user_profile(c, user.id)
    out = {
        "authenticated": True,
        "username": user.username,
        "role": user.role,
        "source": user.auth_source,
    }
    if profile:
        out.update({
            "id":           profile["id"],
            "email":        profile.get("email") or "",
            "display_name": profile.get("display_name") or "",
            "bio":          profile.get("bio") or "",
            "created_at":   profile.get("created_at"),
            "last_login_at": profile.get("last_login_at"),
            "avatar_url":   f"/api/avatars/{profile['avatar_path']}" if profile.get("avatar_path") else None,
        })
    return out


class ProfileIn(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    email: Optional[str] = None


@app.patch("/api/me/profile")
async def api_update_profile(
    p: ProfileIn,
    user: auth.User = Depends(auth.current_user),
):
    """Update the caller's own display_name / bio / email. Authentik users
    CAN edit these locally — those values don't round-trip to Authentik,
    they're OmniGrid's own overlay for display purposes.
    """
    # Keep the fields bounded so someone can't store a MB of biography.
    if p.display_name is not None and len(p.display_name) > 80:
        raise HTTPException(status_code=400, detail="display_name must be 80 chars or less")
    if p.bio is not None and len(p.bio) > 500:
        raise HTTPException(status_code=400, detail="bio must be 500 chars or less")
    if p.email is not None and p.email and len(p.email) > 200:
        raise HTTPException(status_code=400, detail="email must be 200 chars or less")
    with db_conn() as c:
        auth.update_user_profile(
            c, user.id,
            display_name=p.display_name,
            bio=p.bio,
            email=p.email,
        )
    return {"ok": True}


# Avatars live on the data volume next to the SQLite DB — persists across
# container restarts and redeploys. Keep the path out of user control:
# filename is derived from user id + content-type extension only.
_AVATAR_DIR = os.path.join(os.path.dirname(DB_PATH), "avatars")
os.makedirs(_AVATAR_DIR, exist_ok=True)
_AVATAR_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
    "image/gif": "gif", "image/webp": "webp",
}
_AVATAR_MAX_BYTES = 1_000_000  # 1 MB — avatars are small, reject uploads above


@app.post("/api/me/avatar")
async def api_upload_avatar(
    request: Request,
    user: auth.User = Depends(auth.current_user),
):
    """Accept a multipart image upload and store it under /app/data/avatars/.

    Validates content-type against an allowlist, caps at 1 MB, and writes
    a filename of the form `u<id>.<ext>` so the same user always overwrites
    their previous avatar (no stale files left around).
    """
    form = await request.form()
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        raise HTTPException(status_code=400, detail="Field 'file' missing")
    ct = (file.content_type or "").lower()
    ext = _AVATAR_EXT.get(ct)
    if not ext:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image type. Allowed: PNG / JPEG / GIF / WEBP.",
        )
    data = await file.read()
    if len(data) > _AVATAR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 1 MB)")
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty upload")
    # Clean up any existing avatar at a different extension.
    with db_conn() as c:
        old = auth.get_user_profile(c, user.id)
    if old and old.get("avatar_path"):
        old_full = os.path.join(_AVATAR_DIR, old["avatar_path"])
        if os.path.exists(old_full) and old["avatar_path"] != f"u{user.id}.{ext}":
            try: os.remove(old_full)
            except OSError: pass
    fname = f"u{user.id}.{ext}"
    with open(os.path.join(_AVATAR_DIR, fname), "wb") as f:
        f.write(data)
    with db_conn() as c:
        auth.set_user_avatar_path(c, user.id, fname)
    return {"ok": True, "avatar_url": f"/api/avatars/{fname}"}


@app.delete("/api/me/avatar")
async def api_clear_avatar(user: auth.User = Depends(auth.current_user)):
    with db_conn() as c:
        p = auth.get_user_profile(c, user.id)
    if p and p.get("avatar_path"):
        full = os.path.join(_AVATAR_DIR, p["avatar_path"])
        if os.path.exists(full):
            try: os.remove(full)
            except OSError: pass
    with db_conn() as c:
        auth.set_user_avatar_path(c, user.id, None)
    return {"ok": True}


@app.get("/api/avatars/{fname}")
async def api_serve_avatar(fname: str, _user: auth.User = Depends(auth.current_user)):
    """Serve an uploaded avatar. Authed — avatars are user data, shouldn't
    be browsable anonymously. Path-traversal-guarded: only basenames are
    accepted, and the final path is re-rooted under _AVATAR_DIR.
    """
    # Reject anything with a slash or path-escape attempt — we only store
    # flat basenames of the form u<id>.<ext>, nothing else is valid.
    if "/" in fname or ".." in fname or not fname:
        raise HTTPException(status_code=404, detail="Not found")
    full = os.path.join(_AVATAR_DIR, fname)
    if not os.path.exists(full) or not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Not found")
    # Derive content-type from the stored extension.
    ext = fname.rsplit(".", 1)[-1].lower()
    ct = next((k for k, v in _AVATAR_EXT.items() if v == ext), "application/octet-stream")
    return FileResponse(full, media_type=ct)


# ============================================================================
# Admin: user / session / API-token management (step 5).
# ============================================================================
class UserCreate(BaseModel):
    username: str
    role: str                      # "admin" | "readonly"
    auth_source: str = "local"     # "local" | "authentik"
    password: Optional[str] = None # required when auth_source == "local"
    email: Optional[str] = None


class UserPatch(BaseModel):
    role: Optional[str] = None
    disabled: Optional[bool] = None


class PasswordResetIn(BaseModel):
    new_password: str


class TokenCreate(BaseModel):
    name: str
    role: str                      # "admin" | "readonly"


@app.get("/api/users")
async def api_list_users(_admin: auth.User = Depends(auth.require_admin)):
    with db_conn() as c:
        return {"users": auth.list_users(c)}


@app.post("/api/users")
async def api_create_user(
    u: UserCreate,
    _admin: auth.User = Depends(auth.require_admin),
):
    name = (u.username or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Username is required.")
    if u.role not in ("admin", "readonly"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'readonly'.")
    if u.auth_source not in ("local", "authentik"):
        raise HTTPException(status_code=400, detail="auth_source must be 'local' or 'authentik'.")
    if u.auth_source == "local":
        if not u.password or len(u.password) < 8:
            raise HTTPException(status_code=400, detail="Local users need a password with 8+ characters.")
    with db_conn() as c:
        if auth.get_user_by_username(c, name):
            raise HTTPException(status_code=409, detail="That username is already taken.")
        user = auth.create_user(
            c, name, u.email or None,
            u.password if u.auth_source == "local" else None,
            u.role, u.auth_source,
        )
    return {"ok": True, "id": user.id, "username": user.username, "role": user.role}


@app.patch("/api/users/{user_id}")
async def api_update_user(
    user_id: int,
    p: UserPatch,
    admin: auth.User = Depends(auth.require_admin),
):
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if p.role is not None and p.role not in ("admin", "readonly"):
            raise HTTPException(status_code=400, detail="Role must be 'admin' or 'readonly'.")
        # Guard: can't demote or disable the last active admin — that
        # would lock everyone out of admin functions.
        new_role = p.role if p.role is not None else target.role
        new_disabled = p.disabled if p.disabled is not None else target.disabled
        losing_admin = target.role == "admin" and not target.disabled and (
            new_role != "admin" or new_disabled
        )
        if losing_admin and auth.count_active_admins(c) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot demote or disable the last active admin.",
            )
        if p.role is not None:
            auth.set_user_role(c, user_id, p.role)
        if p.disabled is not None:
            auth.set_user_disabled(c, user_id, bool(p.disabled))
    return {"ok": True}


@app.delete("/api/users/{user_id}")
async def api_delete_user(
    user_id: int,
    admin: auth.User = Depends(auth.require_admin),
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You can't delete yourself.")
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target.role == "admin" and not target.disabled and auth.count_active_admins(c) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the last active admin.",
            )
        auth.delete_user(c, user_id)
    return {"ok": True}


@app.post("/api/users/{user_id}/reset-password")
async def api_reset_password(
    user_id: int,
    r: PasswordResetIn,
    _admin: auth.User = Depends(auth.require_admin),
):
    if not r.new_password or len(r.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be 8+ characters.")
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target.auth_source != "local":
            raise HTTPException(
                status_code=400,
                detail="Authentik-managed users must change their password in Authentik.",
            )
        auth.admin_reset_password(c, user_id, r.new_password)
    return {"ok": True}


@app.get("/api/sessions")
async def api_list_sessions(_admin: auth.User = Depends(auth.require_admin)):
    with db_conn() as c:
        return {"sessions": auth.list_sessions(c)}


@app.delete("/api/sessions/{token_id}")
async def api_revoke_session(
    token_id: str,
    _admin: auth.User = Depends(auth.require_admin),
):
    with db_conn() as c:
        auth.delete_session(c, token_id)
    return {"ok": True}


@app.get("/api/tokens")
async def api_list_tokens(_admin: auth.User = Depends(auth.require_admin)):
    with db_conn() as c:
        return {"tokens": auth.list_api_tokens(c)}


@app.post("/api/tokens")
async def api_create_token(
    t: TokenCreate,
    admin: auth.User = Depends(auth.require_admin),
):
    name = (t.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    if t.role not in ("admin", "readonly"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'readonly'.")
    try:
        with db_conn() as c:
            raw = auth.create_api_token(c, name, t.role, admin.id)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="A token with that name already exists.")
    # Raw token returned ONCE. UI shows a one-time reveal modal; we store
    # only the SHA-256 hash. If lost, the operator must rotate.
    return {"ok": True, "name": name, "role": t.role, "token": raw}


@app.delete("/api/tokens/{token_id}")
async def api_delete_token(
    token_id: int,
    _admin: auth.User = Depends(auth.require_admin),
):
    with db_conn() as c:
        auth.delete_api_token(c, token_id)
    return {"ok": True}


# ============================================================================
# Backups — zip containing the full SQLite DB + avatars directory.
# Admin-only; list/create/download/delete/restore. See logic/backups.py for
# the safety dance (consistent .backup() snapshot, pre-restore auto-snapshot,
# path-traversal guards).
# ============================================================================
@app.get("/api/backups")
async def api_list_backups(_admin: auth.User = Depends(auth.require_admin)):
    return {"backups": backups.list_backups()}


@app.post("/api/backups")
async def api_create_backup(_admin: auth.User = Depends(auth.require_admin)):
    result = backups.create_backup()
    # Retention — surfaced to the operator in the response so they can
    # see what got pruned without re-listing. Zero/empty setting means
    # "keep all", which is the safe default for a fresh install.
    try:
        keep = int(get_setting("backup_retention_count", "0") or "0")
    except (TypeError, ValueError):
        keep = 0
    pruned = backups.prune_backups(keep) if keep > 0 else []
    if pruned:
        result = {**result, "pruned": pruned}
    return result


@app.get("/api/backups/{name}")
async def api_download_backup(
    name: str, _admin: auth.User = Depends(auth.require_admin),
):
    try:
        path = backups._backup_path(name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid backup name")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, filename=name, media_type="application/zip")


@app.delete("/api/backups/{name}")
async def api_delete_backup(
    name: str, _admin: auth.User = Depends(auth.require_admin),
):
    try:
        backups.delete_backup(name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid backup name")
    return {"ok": True}


@app.post("/api/backups/{name}/restore")
async def api_restore_backup_named(
    name: str, _admin: auth.User = Depends(auth.require_admin),
):
    try:
        return backups.restore_by_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")


@app.post("/api/backups/restore")
async def api_restore_backup_upload(
    request: Request, _admin: auth.User = Depends(auth.require_admin),
):
    """Upload a zip file and restore from it. 200 MB cap."""
    form = await request.form()
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        raise HTTPException(status_code=400, detail="Field 'file' missing")
    data = await file.read()
    if len(data) > backups.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Upload too large (max {backups.MAX_UPLOAD_BYTES // 1_000_000} MB)",
        )
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty upload")
    # Persist the uploaded zip to a temp file on the data volume so the
    # restore function (which expects a filesystem path) can work on it.
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".zip",
        dir=os.path.dirname(DB_PATH) or ".",
    ) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        result = backups.restore_from_file(tmp_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid backup: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")
    finally:
        try: os.remove(tmp_path)
        except OSError: pass
    return result


# ============================================================================
# Scheduler — admin-defined recurring jobs. See logic/schedules.py for the
# tick loop + kind registry. Admin-only CRUD; POST .../run fires manually.
# ============================================================================
class ScheduleIn(BaseModel):
    name: str
    kind: str
    params: Optional[dict] = None
    interval_seconds: int
    enabled: bool = True
    # Cadence bundle — cadence_mode picks which of the fields below the
    # tick loop consults. See logic.schedules.CADENCE_MODES.
    cadence_mode: str = "interval"
    run_at_hhmm: Optional[str] = None   # daily/weekly/monthly anchor
    days_of_week: Optional[list[int]] = None  # weekly, Mon=0..Sun=6
    day_of_month: Optional[int] = None  # monthly, 1..31 clamped to EOM


class SchedulePatch(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    params: Optional[dict] = None
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None
    cadence_mode: Optional[str] = None
    # For these three, None in the wire payload means "don't touch";
    # explicit empty ("" / []) means "clear" — handled by
    # schedules.update_schedule().
    run_at_hhmm: Optional[str] = None
    days_of_week: Optional[list[int]] = None
    day_of_month: Optional[int] = None


@app.get("/api/schedules")
async def api_list_schedules(_admin: auth.User = Depends(auth.require_admin)):
    with db_conn() as c:
        return {
            "schedules": schedules.list_schedules(c),
            "kinds": sorted(schedules.SCHEDULE_KINDS.keys()),
            "min_interval_seconds": schedules.MIN_INTERVAL_SECONDS,
        }


@app.post("/api/schedules")
async def api_create_schedule(
    s: ScheduleIn,
    _admin: auth.User = Depends(auth.require_admin),
):
    name = (s.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    if s.kind not in schedules.SCHEDULE_KINDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown schedule kind '{s.kind}'. "
                f"Known: {', '.join(sorted(schedules.SCHEDULE_KINDS.keys()))}"
            ),
        )
    if s.interval_seconds < schedules.MIN_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"interval_seconds must be >= {schedules.MIN_INTERVAL_SECONDS}"
            ),
        )
    params = s.params or {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be a JSON object.")
    try:
        with db_conn() as c:
            row = schedules.create_schedule(
                c, name, s.kind, params, int(s.interval_seconds),
                bool(s.enabled),
                run_at_hhmm=s.run_at_hhmm,
                cadence_mode=s.cadence_mode or "interval",
                days_of_week=s.days_of_week,
                day_of_month=s.day_of_month,
            )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="A schedule with that name already exists.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "schedule": row}


@app.patch("/api/schedules/{schedule_id}")
async def api_update_schedule(
    schedule_id: int,
    p: SchedulePatch,
    _admin: auth.User = Depends(auth.require_admin),
):
    # exclude_unset keeps explicit None values so "clear this field" works
    # via wire-level null (e.g. flipping back to interval mode by sending
    # {cadence_mode:"interval", run_at_hhmm:null, days_of_week:null,
    # day_of_month:null}). update_schedule() knows which fields are
    # clearable-on-None; the rest still ignore None as before.
    patch_fields = p.model_dump(exclude_unset=True)
    if "name" in patch_fields and patch_fields["name"] is not None:
        patch_fields["name"] = patch_fields["name"].strip()
        if not patch_fields["name"]:
            raise HTTPException(status_code=400, detail="Name cannot be blank.")
    try:
        with db_conn() as c:
            existing = schedules.get_schedule(c, schedule_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Schedule not found.")
            row = schedules.update_schedule(c, schedule_id, **patch_fields)
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="A schedule with that name already exists.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "schedule": row}


@app.delete("/api/schedules/{schedule_id}")
async def api_delete_schedule(
    schedule_id: int,
    _admin: auth.User = Depends(auth.require_admin),
):
    with db_conn() as c:
        if not schedules.get_schedule(c, schedule_id):
            raise HTTPException(status_code=404, detail="Schedule not found.")
        schedules.delete_schedule(c, schedule_id)
    return {"ok": True}


@app.post("/api/schedules/{schedule_id}/run")
async def api_run_schedule(
    schedule_id: int,
    _admin: auth.User = Depends(auth.require_admin),
):
    """Fire a schedule immediately, bypassing its interval.

    Uses the same kind-callable path as the tick loop, so the resulting
    op flows through ops.py exactly as if the schedule had been due.
    Returns the op id so the UI can deep-link the ops panel.
    """
    with db_conn() as c:
        s = schedules.get_schedule(c, schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    try:
        op_id = await schedules.fire_schedule(s)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fire failed: {e}")
    return {"ok": True, "op_id": op_id}


@app.get("/api/schedules/queue")
async def api_schedule_queue(
    limit: int = 50,
    page: int = 1,
    page_size: int = 0,
    _admin: auth.User = Depends(auth.require_admin),
):
    """Recent scheduler-driven ops from the history table.

    Filtered to ``actor='scheduler'`` so user-triggered runs of the
    same op types don't clutter the view.

    Pagination contract: when ``page_size`` is passed the response
    returns ONE page of rows plus `total` / `page` / `page_size` so
    the UI can render "Page N of M" without double-fetching. When
    ``page_size`` is 0 (or omitted), the endpoint falls back to the
    legacy flat-list shape (`limit` rows, no `total`) so older
    clients keep working.
    """
    # Legacy single-query path — keep until every caller is migrated.
    if page_size <= 0:
        limit = max(1, min(int(limit), 500))
        rows = _history_query(
            stack=None, op_type=None, status=None,
            actor=schedules.SCHEDULER_ACTOR, q=None,
            since=None, until=None, limit=limit,
        )
        return {"queue": rows}
    # Paginated path — count + slice via explicit SQL so we don't
    # have to teach `_history_query` about OFFSET (its other callers
    # treat the full result as "the N most recent"). Cap page_size
    # at 100 to guard against accidentally-huge queries.
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    actor = schedules.SCHEDULER_ACTOR
    offset = (page - 1) * page_size
    with db_conn() as c:
        total_row = c.execute(
            "SELECT COUNT(*) FROM history WHERE actor = ?", (actor,),
        ).fetchone()
        total = int((total_row[0] if total_row else 0) or 0)
        rows = c.execute(
            "SELECT * FROM history WHERE actor = ? "
            "ORDER BY ts DESC LIMIT ? OFFSET ?",
            (actor, page_size, offset),
        ).fetchall()
    return {
        "queue":     [dict(r) for r in rows],
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "pages":     max(1, (total + page_size - 1) // page_size),
    }


# Login HTML page. Served as a discrete route (not via StaticFiles) because
# /login has no trailing slash and we want it to map to static/login.html
# directly without relying on html=True directory-index behaviour. Also
# listed in auth.FULLY_PUBLIC_PREFIXES so the middleware never gates it.
@app.get("/login")
async def login_page():
    return _render_shell("static/login.html")


# Shell-HTML cache — tiny map keyed by file path. Each entry stores the
# raw file bytes and the mtime we last saw; a disk change invalidates the
# entry lazily on the next request. `str.replace` runs on every hit
# (cheap — the two HTMLs together are <200 KB) so `__APP_VERSION__` marker
# references pick up a new PATCH as soon as VERSION.txt changes, without
# any restart.
_SHELL_CACHE: dict = {}


def _render_shell(path: str) -> Response:
    """Serve an HTML shell with `__APP_VERSION__` → current version.

    Used for `/` and `/login` — both reference external JS/CSS as
    `src="/js/app.js?v=__APP_VERSION__"`, and this is the substitution
    point that turns that literal into an actual cache-bustable URL.
    Any other entry-point HTML that references versioned assets should
    be served through this too; the bare StaticFiles mount at "/" won't
    run the substitution.
    """
    try:
        st = os.stat(path)
        mtime_ns = st.st_mtime_ns
    except OSError:
        raise HTTPException(status_code=404, detail=f"{path} not found")
    cached = _SHELL_CACHE.get(path)
    if cached is None or cached[1] != mtime_ns:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
        _SHELL_CACHE[path] = (body, mtime_ns)
    else:
        body = cached[0]
    # Use the LIVE version, not the import-time snapshot. This lets an
    # operator edit /app/VERSION.txt on the server and have cache-busting
    # URLs follow without restarting the container.
    body = body.replace("__APP_VERSION__", read_version())
    return Response(content=body, media_type="text/html; charset=utf-8")


# SPA shell. Served through _render_shell so the version substitution
# applies — StaticFiles at "/" would hand back the raw file with the
# literal "__APP_VERSION__" marker still in the script srcs. Registered
# BEFORE the StaticFiles mount below (mount-order rule applies).
@app.get("/")
async def spa_shell():
    return _render_shell("static/index.html")


# Deep-link routes for every SPA view. The Alpine front-end calls
# `history.replaceState('/nodes')` when you switch tabs so reloading
# a deep link drops you back on the same tab; without a matching
# server route, `GET /nodes` would fall through to the StaticFiles
# mount and 404. The shell itself is identical to `/` — Alpine's
# `_applyRouteFromPath()` picks the view based on `location.pathname`
# once the page boots. Settings / Admin accept a sub-path segment
# (`/settings/oidc`, `/admin/users`) so those deep links work too.
_SPA_ROUTES = ("stacks", "services", "nodes", "hosts", "history")

for _view in _SPA_ROUTES:
    app.add_api_route(f"/{_view}", spa_shell, methods=["GET"])

@app.get("/settings")
@app.get("/settings/{section}")
async def spa_settings_shell(section: str = ""):
    return _render_shell("static/index.html")

@app.get("/admin")
@app.get("/admin/{tab}")
async def spa_admin_shell(tab: str = ""):
    return _render_shell("static/index.html")


# Prometheus scrape endpoint.
# Implemented as a regular route (not app.mount) because Starlette's
# Mount only matches the mount path WITH a trailing slash — bare GET
# /metrics (what every Prometheus scraper sends by default) falls
# through to the StaticFiles catch-all and returns 404. Using a route
# sidesteps the trailing-slash foot-gun entirely.
@app.get("/metrics")
async def prometheus_metrics():
    return Response(
        content=metrics.generate_latest(metrics.REGISTRY),
        media_type=metrics.CONTENT_TYPE_LATEST,
    )


# Serve node_modules directly — HTML references Alpine / SweetAlert2
# from /node_modules/<pkg>/dist/…, so we expose the whole tree as static.
# Mounted before the "/" catch-all so the path routes here first. Only
# paths prefixed with /node_modules/ land here; it's auth.FULLY_PUBLIC
# by virtue of not starting with /api/.
if os.path.isdir("node_modules"):
    app.mount("/node_modules", StaticFiles(directory="node_modules"), name="node_modules")


# Translation bundles. Mounted at /i18n/ (before the "/" catch-all, same
# ordering rule as /metrics / /node_modules) so the SPA can fetch
# /i18n/en.json, /i18n/ar.json, /i18n/index.json at boot. Anonymous-
# readable: language files are UI strings, not secrets.
if os.path.isdir("static/i18n"):
    app.mount("/i18n", StaticFiles(directory="static/i18n"), name="i18n")


# Keep this line LAST — StaticFiles at "/" is a catch-all.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
