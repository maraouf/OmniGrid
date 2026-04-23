"""
PortaUpdate — Portainer-native update dashboard.

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
from typing import Optional

# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).
from dotenv import load_dotenv
load_dotenv(os.getenv("ENV_FILE_PATH", "/app/.env"), override=False)

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from logic import auth, backups, metrics, oidc
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
    sampler = asyncio.create_task(_stats_sampler_loop(), name="stats-sampler")
    try:
        yield
    finally:
        sampler.cancel()
        try:
            await sampler
        except asyncio.CancelledError:
            pass


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


app = FastAPI(title="PortaUpdate", lifespan=_lifespan)

# Observe-mode auth middleware (step 1 of the auth rollout). Populates
# request.state.user when an identity can be resolved; never rejects. Write
# routes and global enforcement gate on this in later steps.
# The lambda defers `db_conn` lookup: the function is defined later in this
# module (SQLite section) but the middleware body only runs at request time.
app.middleware("http")(auth.make_auth_middleware(lambda: db_conn()))

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
        headers={"Content-Disposition": 'attachment; filename="portaupdate-history.json"'},
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
        headers={"Content-Disposition": 'attachment; filename="portaupdate-history.csv"'},
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


@app.get("/api/settings")
async def api_get_settings(request: Request):
    from logic import portainer as _portainer
    with db_conn() as c:
        a = auth.get_auth_settings(c)
    p = _portainer.get_portainer_settings()
    return {
        "apprise_url": get_setting("apprise_url", ""),
        "apprise_tag": get_setting("apprise_tag", ""),
        "portainer_public_url": get_setting("portainer_public_url", str(p.get("portainer_url") or "")),
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
    if s.portainer_public_url is not None: set_setting("portainer_public_url", s.portainer_public_url)

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
    await notify("🔔 PortaUpdate test", "Notifications are wired up correctly!", "success")
    return {"status": "sent"}


@app.get("/api/healthz")
async def healthz():
    # Re-read VERSION.txt per request so operator edits on the server
    # (e.g. hand-bumping MAJOR/MINOR) show up without restarting the
    # container. File is tiny — a couple-microsecond stat+read each call.
    return {
        "ok": True,
        "version": read_version(),
        "cache_age": int(time.time() - _cache["ts"]) if _cache["ts"] else None,
    }


@app.get("/api/version")
async def api_version():
    return {"version": read_version()}


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
    they're PortaUpdate's own overlay for display purposes.
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
    return backups.create_backup()


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
