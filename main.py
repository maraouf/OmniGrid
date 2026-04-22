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

from logic import auth, metrics
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
from logic.version import APP_VERSION

# ============================================================================
# Config
# ============================================================================
PORTAINER_URL = os.getenv("PORTAINER_URL", "").rstrip("/")
PORTAINER_API_KEY = os.getenv("PORTAINER_API_KEY", "")
PORTAINER_ENDPOINT_ID = int(os.getenv("PORTAINER_ENDPOINT_ID", "1"))
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "900"))
STATS_CACHE_TTL = int(os.getenv("STATS_CACHE_TTL_SECONDS", "30"))
VERIFY_TLS = os.getenv("VERIFY_TLS", "true").lower() == "true"
CONCURRENCY = int(os.getenv("REGISTRY_CONCURRENCY", "8"))
STATS_CONCURRENCY = int(os.getenv("STATS_CONCURRENCY", "16"))
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
    # Seed the three auth settings (authentik_enabled, npm_auth_secret,
    # authentik_admin_group) from env into the DB on first boot. On every
    # subsequent start, this is a no-op — the UI is authoritative once the
    # keys exist. Admins edit from Settings → Authentik panel.
    with db_conn() as c:
        auth.bootstrap_auth_settings(c)
    _bootstrap_admin_if_needed()
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
# Portainer client
# ============================================================================
def _headers(agent_target: Optional[str] = None):
    # `X-PortainerAgent-Target: <hostname>` routes the request through the
    # Portainer agent to a specific Swarm node's Docker daemon. Required for
    # container-level actions (delete, restart, recreate) when the container
    # lives on a worker node — the manager's daemon would otherwise 404.
    # Skip the header for synthetic fallback values.
    h = {"X-API-Key": PORTAINER_API_KEY}
    if agent_target and agent_target not in ("local", "?", ""):
        h["X-PortainerAgent-Target"] = agent_target
    return h


def _node_for_container(container_id: str) -> Optional[str]:
    """Return the hostname of the Swarm node hosting `container_id`, if known.

    Reads the last gathered `_cache`. Returns None for standalone containers
    whose node can't be determined from Swarm metadata (those stay routed to
    the manager, same as before). Accepts either a prefixed id (`ctn:abc...`)
    or the raw Docker ID.
    """
    for it in _cache.get("items", []):
        if it.get("raw_id") == container_id or it.get("id") == container_id:
            node = it.get("node")
            if node and node not in ("local", "?", ""):
                return node
            break
    return None


async def _pg(client: httpx.AsyncClient, path: str):
    r = await client.get(f"{PORTAINER_URL}{path}", headers=_headers())
    r.raise_for_status()
    return r.json()


# ============================================================================
# Registry digest checking
# ============================================================================
_token_cache: dict[str, tuple[str, float]] = {}


def _parse_image_ref(ref: str) -> tuple[str, str, str]:
    if "@" in ref:
        ref = ref.split("@", 1)[0]
    parts = ref.split("/", 1)
    first = parts[0]
    is_reg = "." in first or ":" in first or first == "localhost"
    if is_reg and len(parts) == 2:
        registry, repo = first, parts[1]
    else:
        registry = "registry-1.docker.io"
        repo = ref if "/" in ref else f"library/{ref}"
    if ":" in repo.rsplit("/", 1)[-1]:
        repo, tag = repo.rsplit(":", 1)
    else:
        tag = "latest"
    return registry, repo, tag


def _hub_link(image: str) -> Optional[str]:
    try:
        reg, repo, _ = _parse_image_ref(image)
    except Exception:
        return None
    if reg == "lscr.io" and repo.startswith("linuxserver/"):
        return f"https://github.com/linuxserver/docker-{repo.split('/', 1)[1]}"
    if reg == "ghcr.io":
        return f"https://github.com/{repo}"
    if reg == "registry-1.docker.io":
        if repo.startswith("library/"):
            return f"https://hub.docker.com/_/{repo.split('/', 1)[1]}/tags"
        return f"https://hub.docker.com/r/{repo}/tags"
    return None


async def _get_bearer(client: httpx.AsyncClient, www_auth: str, repo: str) -> Optional[str]:
    if not www_auth.lower().startswith("bearer "):
        return None
    params: dict[str, str] = {}
    for part in www_auth[7:].split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip()] = v.strip().strip('"')
    realm = params.get("realm")
    if not realm:
        return None
    service = params.get("service", "")
    scope = params.get("scope", f"repository:{repo}:pull")
    key = f"{realm}|{service}|{scope}"
    if key in _token_cache:
        t, exp = _token_cache[key]
        if exp > time.time():
            return t
    auth = None
    if "docker.io" in realm and DOCKERHUB_USER and DOCKERHUB_TOKEN:
        auth = (DOCKERHUB_USER, DOCKERHUB_TOKEN)
    try:
        r = await client.get(realm, params={"service": service, "scope": scope}, auth=auth)
        r.raise_for_status()
        j = r.json()
        tok = j.get("token") or j.get("access_token")
        if tok:
            _token_cache[key] = (tok, time.time() + int(j.get("expires_in", 300)) - 30)
        return tok
    except Exception as e:
        print(f"[auth] {e}")
        return None


async def _get_remote_digest(client: httpx.AsyncClient, image: str) -> Optional[str]:
    # Parse OUTSIDE the timed block — we need the registry host for the
    # histogram label, and we shouldn't charge parse-only failures to
    # registry latency.
    try:
        reg, repo, tag = _parse_image_ref(image)
    except Exception as e:
        print(f"[digest] parse {image}: {e}")
        return None
    _t0 = time.monotonic()
    digest: Optional[str] = None
    try:
        accept = ", ".join([
            "application/vnd.docker.distribution.manifest.v2+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.oci.image.index.v1+json",
        ])
        url = f"https://{reg}/v2/{repo}/manifests/{tag}"
        h = {"Accept": accept}
        r = await client.head(url, headers=h, follow_redirects=True)
        if r.status_code == 401:
            tok = await _get_bearer(client, r.headers.get("www-authenticate", ""), repo)
            if tok:
                h["Authorization"] = f"Bearer {tok}"
                r = await client.head(url, headers=h, follow_redirects=True)
        if r.status_code == 200:
            digest = r.headers.get("docker-content-digest")
        elif r.status_code in (404, 405):
            r = await client.get(url, headers=h, follow_redirects=True)
            if r.status_code == 200:
                digest = r.headers.get("docker-content-digest")
        if digest is None:
            metrics.REGISTRY_ERRORS.labels(registry=reg).inc()
        return digest
    except Exception as e:
        metrics.REGISTRY_ERRORS.labels(registry=reg).inc()
        print(f"[digest] {image}: {e}")
        return None
    finally:
        metrics.REGISTRY_LATENCY.labels(registry=reg).observe(time.monotonic() - _t0)


# ============================================================================
# Data aggregation
# ============================================================================
_cache: dict = {"items": [], "ts": 0.0, "nodes": {}, "stacks": [], "task_node_by_id": {}}


def _tag_of(image: str) -> str:
    last = image.split("/")[-1]
    return last.rsplit(":", 1)[1] if ":" in last else "latest"


def _node_attr(node: dict, key: str):
    """Resolve a Swarm placement-constraint attribute against a raw node dict."""
    spec = node.get("Spec") or {}
    desc = node.get("Description") or {}
    if key == "node.id":
        return node.get("ID")
    if key == "node.role":
        return spec.get("Role")
    if key == "node.hostname":
        return desc.get("Hostname")
    if key == "node.platform.os":
        return (desc.get("Platform") or {}).get("OS")
    if key == "node.platform.arch":
        return (desc.get("Platform") or {}).get("Architecture")
    if key.startswith("node.labels."):
        return (spec.get("Labels") or {}).get(key[len("node.labels."):])
    if key.startswith("engine.labels."):
        return ((desc.get("Engine") or {}).get("Labels") or {}).get(key[len("engine.labels."):])
    return None


def _node_matches(node: dict, constraints: list[str]) -> bool:
    """Return True if the node satisfies every Swarm placement constraint."""
    for c in constraints or []:
        op = None
        for candidate in ("==", "!="):
            if candidate in c:
                op = candidate
                break
        if not op:
            continue  # unrecognised — don't filter it out
        left, right = c.split(op, 1)
        actual = _node_attr(node, left.strip())
        equal = (str(actual) == right.strip())
        if op == "==" and not equal:
            return False
        if op == "!=" and equal:
            return False
    return True


async def _gather():
    _gather_t0 = time.monotonic()
    try:
        await _gather_impl()
    finally:
        metrics.GATHER_DURATION.observe(time.monotonic() - _gather_t0)
        metrics.populate_from_cache(_cache)


async def _gather_impl():
    async with httpx.AsyncClient(verify=VERIFY_TLS, timeout=60.0) as client:
        ep = f"/api/endpoints/{PORTAINER_ENDPOINT_ID}/docker"

        async def safe(coro, fb):
            try:
                return await coro
            except Exception as e:
                print(f"[gather] {e}")
                return fb

        services = await safe(_pg(client, f"{ep}/services"), [])
        containers = await safe(_pg(client, f"{ep}/containers/json?all=1"), [])
        tasks = await safe(_pg(client, f"{ep}/tasks"), [])
        nodes = await safe(_pg(client, f"{ep}/nodes"), [])
        stacks_list = await safe(_pg(client, "/api/stacks"), [])

        node_map = {n["ID"]: n["Description"]["Hostname"] for n in nodes}
        stack_by_name = {s["Name"]: s for s in stacks_list}
        tasks_by_service: dict[str, list] = {}
        # task.ID → hostname — used later to pin orphan Swarm task containers
        # to their actual worker node. Without this, `/api/containers/{id}`
        # routes to the manager's Docker daemon and 404s for containers that
        # live on a worker. Sending `X-PortainerAgent-Target: <node>` fixes it.
        task_node_by_id: dict[str, str] = {}
        for t in tasks:
            sid = t.get("ServiceID")
            if sid:
                tasks_by_service.setdefault(sid, []).append(t)
            tid = t.get("ID")
            nid = t.get("NodeID")
            if tid and nid and nid in node_map:
                task_node_by_id[tid] = node_map[nid]

        # Build service-id → running containers map. Swarm stamps every task
        # container with `com.docker.swarm.service.id`, so we can go from service
        # → container → image → RepoDigests when neither the service spec nor the
        # task spec carries a digest pin.
        containers_by_service: dict[str, list] = {}
        for c in containers:
            sid = (c.get("Labels") or {}).get("com.docker.swarm.service.id")
            if sid:
                containers_by_service.setdefault(sid, []).append(c)

        # Cache image-inspect results within this gather so services sharing an
        # image don't trigger N image-inspect calls.
        image_digest_cache: dict[str, Optional[str]] = {}

        async def _digest_for_image_id(image_id: str) -> Optional[str]:
            if not image_id:
                return None
            if image_id in image_digest_cache:
                return image_digest_cache[image_id]
            try:
                img = await _pg(client, f"{ep}/images/{image_id}/json")
                for rd in img.get("RepoDigests") or []:
                    if "@" in rd:
                        digest = rd.split("@", 1)[1]
                        image_digest_cache[image_id] = digest
                        return digest
            except Exception as e:
                print(f"[digest-fallback] {image_id[:12]}: {e}")
            image_digest_cache[image_id] = None
            return None

        with db_conn() as c:
            ignores = [dict(r) for r in c.execute("SELECT * FROM ignores").fetchall()]

        def is_ignored(image, stack):
            for ig in ignores:
                p = ig["pattern"]
                if ig["kind"] == "image" and p and p in (image or ""):
                    return True
                if ig["kind"] == "stack" and p and p == (stack or ""):
                    return True
            return False

        items: list[dict] = []

        # --- Swarm services ---
        for svc in services:
            spec = svc.get("Spec", {}) or {}
            cs = (spec.get("TaskTemplate") or {}).get("ContainerSpec") or {}
            full_image = cs.get("Image", "") or ""
            image_name_tag = full_image.split("@", 1)[0] if "@" in full_image else full_image
            current_digest = full_image.split("@", 1)[1] if "@" in full_image else None
            labels = spec.get("Labels") or {}
            stack_name = labels.get("com.docker.stack.namespace")
            stack = stack_by_name.get(stack_name) if stack_name else None

            svc_tasks = tasks_by_service.get(svc["ID"], [])
            # If the service-level spec isn't digest-pinned (common when the image
            # failed to resolve at deploy time), fall back to a task-level digest.
            # Swarm stamps each dispatched task's ContainerSpec.Image with the digest
            # it actually scheduled, so a running task is authoritative for "what's
            # deployed right now."
            if not current_digest:
                for t in svc_tasks:
                    t_img = ((t.get("Spec") or {}).get("ContainerSpec") or {}).get("Image", "") or ""
                    if "@" in t_img:
                        # Prefer a running task, else take the first digest we see.
                        if (t.get("Status") or {}).get("State") == "running":
                            current_digest = t_img.split("@", 1)[1]
                            break
                        if not current_digest:
                            current_digest = t_img.split("@", 1)[1]
            if not current_digest:
                # Final fallback: inspect the running container for this service on
                # any node. The container's image ID (sha256:...) maps to the image's
                # RepoDigests, which gives us the actual `@sha256:...` that this
                # service is currently executing. This covers services deployed
                # with an unpinned tag that Swarm never resolved.
                svc_containers = containers_by_service.get(svc["ID"], [])
                for c in svc_containers:
                    if (c.get("State") or "").lower() == "running":
                        current_digest = await _digest_for_image_id(c.get("ImageID") or c.get("Image"))
                        if current_digest:
                            break
                if not current_digest:
                    # Even a stopped/crashlooping container's image tells us what
                    # the service last tried to run.
                    for c in svc_containers:
                        current_digest = await _digest_for_image_id(c.get("ImageID") or c.get("Image"))
                        if current_digest:
                            break
            running = sum(
                1 for t in svc_tasks
                if (t.get("Status") or {}).get("State") == "running"
                and t.get("DesiredState") == "running"
            )
            mode = spec.get("Mode", {}) or {}
            if "Replicated" in mode:
                desired = mode["Replicated"].get("Replicas", 1)
            elif "Global" in mode:
                # Only count nodes that actually satisfy the service's placement
                # constraints, so a manager-pinned global service isn't flagged as
                # degraded just because worker nodes exist.
                placement = ((spec.get("TaskTemplate") or {}).get("Placement") or {})
                constraints = placement.get("Constraints") or []
                eligible = [n for n in nodes if _node_matches(n, constraints)]
                desired = len(eligible) or 1
            else:
                desired = 1
            placements = []
            for t in svc_tasks:
                if t.get("DesiredState") == "shutdown":
                    continue
                st = t.get("Status") or {}
                placements.append({
                    "node": node_map.get(t.get("NodeID"), "?"),
                    "state": st.get("State"),
                    "err": st.get("Err"),
                })

            if desired == 0:
                health = "offline"
            elif running == 0:
                health = "offline"
            elif running < desired:
                health = "degraded"
            else:
                health = "healthy"

            items.append({
                "id": f"svc:{svc['ID'][:12]}",
                "raw_id": svc["ID"],
                "name": spec.get("Name", ""),
                "type": "service",
                "image": image_name_tag,
                "tag": _tag_of(image_name_tag),
                "current_digest": current_digest,
                "stack": stack_name,
                "stack_id": stack["Id"] if stack else None,
                "replicas": {"desired": desired, "running": running},
                "placements": placements,
                "health": health,
                "state": "running" if running > 0 else "stopped",
                "removable": False,
                "hub_link": _hub_link(image_name_tag),
                "ignored": is_ignored(image_name_tag, stack_name),
                "created": spec.get("CreatedAt") or svc.get("CreatedAt"),
                "updated": spec.get("UpdatedAt") or svc.get("UpdatedAt"),
            })

        # --- Standalone / compose (non-Swarm) containers + orphan Swarm task containers ---
        # We intentionally include Swarm task containers that are NOT currently
        # running (exited / dead). Swarm often leaves these behind after replacing
        # a task and they accumulate over time. Listing them here lets the user
        # bulk-remove the orphans. Running Swarm task containers are still skipped
        # because they're already represented via their parent service.
        for cont in containers:
            labels = cont.get("Labels") or {}
            state = (cont.get("State") or "").lower()
            is_swarm_task = bool(labels.get("com.docker.swarm.service.id"))
            if is_swarm_task and state == "running":
                continue
            image_ref = cont.get("Image", "") or ""
            # Orphan Swarm task containers report their image as
            # `repo:tag@sha256:...` — keep just the `repo:tag` for display so the
            # UI cell doesn't overflow. The digest goes into current_digest.
            if "@" in image_ref:
                head, _, digest_suffix = image_ref.partition("@")
                image_ref = head
                # If the container's Image field already carried a digest, use it
                # as a fallback for current_digest (the RepoDigests lookup below
                # is the primary source).
                if digest_suffix.startswith("sha256:"):
                    cont.setdefault("_pu_fallback_digest", digest_suffix)
            compose_project = (
                labels.get("com.docker.compose.project")
                or labels.get("com.docker.stack.namespace")
            )
            stack = stack_by_name.get(compose_project) if compose_project else None

            current_digest = None
            try:
                img = await _pg(client, f"{ep}/images/{cont['ImageID']}/json")
                for rd in img.get("RepoDigests") or []:
                    if "@" in rd:
                        current_digest = rd.split("@", 1)[1]
                        break
                # Recover a real image name when Docker reports the Image field as a raw
                # sha256 digest (happens when the image was pulled by digest or later untagged)
                if image_ref.startswith("sha256:") or (image_ref and "/" not in image_ref and ":" not in image_ref):
                    real_tags = [t for t in (img.get("RepoTags") or []) if t and "<none>" not in t]
                    if real_tags:
                        image_ref = real_tags[0]
            except Exception:
                pass
            # Fallback digest from the Image field (e.g. orphan task containers
            # whose image was purged and image-inspect now 404s).
            if not current_digest and cont.get("_pu_fallback_digest"):
                current_digest = cont["_pu_fallback_digest"]

            name = (cont.get("Names") or ["?"])[0].lstrip("/")
            state = (cont.get("State") or "").lower()
            if state == "running":
                health = "healthy"
            elif state in ("restarting", "paused"):
                health = "degraded"
            else:
                health = "offline"

            # Resolve the real node. Swarm task containers carry their task
            # ID as a label — look it up in task_node_by_id. Fallback "local"
            # covers plain compose / standalone containers where the node is
            # unknowable from the Swarm metadata.
            swarm_task_id = labels.get("com.docker.swarm.task.id")
            node_name = task_node_by_id.get(swarm_task_id) if swarm_task_id else None
            if not node_name:
                node_name = "local"

            items.append({
                "id": f"ctn:{cont['Id'][:12]}",
                "raw_id": cont["Id"],
                "name": name,
                "type": "orphan" if is_swarm_task else "container",
                "image": image_ref,
                "tag": _tag_of(image_ref),
                "current_digest": current_digest,
                "stack": compose_project,
                "stack_id": stack["Id"] if stack else None,
                "replicas": {"desired": 1, "running": 1 if state == "running" else 0},
                "placements": [{"node": node_name, "state": state}],
                "node": node_name,
                "health": health,
                "state": state,
                "removable": health == "offline",
                "hub_link": _hub_link(image_ref),
                "ignored": is_ignored(image_ref, compose_project),
                "created": cont.get("Created"),
            })

        # --- Enrich with remote digests ---
        sem = asyncio.Semaphore(CONCURRENCY)

        async def enrich(it):
            async with sem:
                remote = await _get_remote_digest(client, it["image"])
            it["remote_digest"] = remote
            if it["ignored"]:
                it["status"] = "ignored"
            elif not it["current_digest"]:
                it["status"] = "unknown"
            elif not remote:
                it["status"] = "error"
            elif it["current_digest"] == remote:
                it["status"] = "up-to-date"
            else:
                it["status"] = "update"
            return it

        items = list(await asyncio.gather(*(enrich(i) for i in items)))

        # Build stack-grouped view
        groups: dict[str, dict] = {}
        for it in items:
            key = it["stack"] or "__standalone__"
            groups.setdefault(key, {
                "name": it["stack"] or "Standalone",
                "stack_id": it["stack_id"],
                "items": [],
                "is_standalone": not it["stack"],
            })["items"].append(it)

        for g in groups.values():
            its = g["items"]
            its.sort(key=lambda i: (i.get("name") or "").lower())
            g["total"] = len(its)
            g["updates"] = sum(1 for i in its if i["status"] == "update")
            g["errors"] = sum(1 for i in its if i["status"] == "error")
            g["unknowns"] = sum(1 for i in its if i["status"] == "unknown")
            g["uptodate"] = sum(1 for i in its if i["status"] == "up-to-date")
            g["offline"] = sum(1 for i in its if i.get("health") == "offline")
            g["degraded"] = sum(1 for i in its if i.get("health") == "degraded")

        items.sort(key=lambda i: (i.get("name") or "").lower())
        _cache["items"] = items
        _cache["nodes"] = node_map
        _cache["task_node_by_id"] = task_node_by_id
        _cache["stacks"] = sorted(
            groups.values(),
            key=lambda s: (s["name"] or "").lower(),
        )
        _cache["ts"] = time.time()


# ============================================================================
# Operations
# ============================================================================
class Operation:
    __slots__ = ("id", "op_type", "target_id", "target_name", "target_stack",
                 "started", "ended", "status", "events", "error", "actor")

    def __init__(self, op_type: str, target_id: str, target_name: str,
                 target_stack: Optional[str] = None, actor: str = "ui"):
        self.id = uuid.uuid4().hex[:12]
        self.op_type = op_type
        self.target_id = target_id
        self.target_name = target_name
        self.target_stack = target_stack
        self.started = time.time()
        self.ended: Optional[float] = None
        self.status = "running"
        self.events: list[dict] = []
        self.error: Optional[str] = None
        self.actor = actor

    def log(self, msg: str, level: str = "info"):
        self.events.append({"ts": time.time(), "level": level, "msg": msg})
        print(f"[op {self.id}] {level}: {msg}")

    def done(self, status: str, error: Optional[str] = None):
        self.status = status
        self.ended = time.time()
        self.error = error

    def to_dict(self):
        return {
            "id": self.id, "op_type": self.op_type, "target_id": self.target_id,
            "target_name": self.target_name, "target_stack": self.target_stack,
            "started": self.started, "ended": self.ended,
            "status": self.status, "events": self.events, "error": self.error,
            "duration": (self.ended or time.time()) - self.started,
            "actor": self.actor,
        }


ops: dict[str, Operation] = {}
ops_order: list[str] = []


def new_op(op_type: str, target_id: str, target_name: str,
           target_stack: Optional[str] = None, actor: str = "ui") -> Operation:
    op = Operation(op_type, target_id, target_name,
                   target_stack=target_stack, actor=actor)
    ops[op.id] = op
    ops_order.insert(0, op.id)
    while len(ops_order) > 50:
        dead = ops_order.pop()
        if ops.get(dead) and ops[dead].status != "running":
            ops.pop(dead, None)
    return op


def persist_history(op: Operation):
    with db_conn() as c:
        c.execute(
            "INSERT INTO history "
            "(ts,op_type,target_name,target_id,target_stack,status,duration,events,error,actor) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (op.started, op.op_type, op.target_name, op.target_id, op.target_stack,
             op.status, (op.ended or time.time()) - op.started,
             json.dumps(op.events), op.error, op.actor),
        )
    # Mirror the outcome into Prometheus. Done here (not in every _do_*
    # handler) because every handler funnels through persist_history in its
    # finally-block — a single instrumentation point covers all op types.
    try:
        metrics.OPS_TOTAL.labels(op_type=op.op_type, status=op.status).inc()
    except Exception as e:
        print(f"[metrics] OPS_TOTAL inc failed: {e}")


# ============================================================================
# Apprise notifications
# ============================================================================
async def notify(title: str, body: str, status: str = "info"):
    url = get_setting("apprise_url", "")
    if not url:
        print("[notify] skipped — no apprise_url configured")
        return
    tag = get_setting("apprise_tag", "")
    # Apprise requires a non-empty body. If our ops didn't produce one, echo
    # the title so the notification isn't rejected as malformed.
    body = body or title
    try:
        async with httpx.AsyncClient(verify=VERIFY_TLS, timeout=15.0) as client:
            payload = {
                "title": title,
                "body": body,
                "type": "success" if status == "success" else "failure" if status == "error" else "info",
            }
            if tag:
                # Apprise-API accepts `tag` (splits on comma/space internally).
                payload["tag"] = tag
            r = await client.post(url, json=payload)
            if r.status_code >= 400:
                print(f"[notify] FAILED {r.status_code} → {url} body={r.text[:200]}")
            else:
                print(f"[notify] ok {r.status_code} → {url} tag={tag!r}")
    except Exception as e:
        print(f"[notify] ERROR → {url}: {e}")


# ============================================================================
# Update tasks
# ============================================================================
async def _do_update_stack(op: Operation, stack_id: int):
    try:
        op.log(f"Starting stack update (id={stack_id})")
        async with httpx.AsyncClient(verify=VERIFY_TLS, timeout=600.0) as client:
            stack = await _pg(client, f"/api/stacks/{stack_id}")
            op.log(f"Resolved stack: {stack['Name']}")
            try:
                file_data = await _pg(client, f"/api/stacks/{stack_id}/file")
            except httpx.HTTPError as e:
                raise RuntimeError(f"Can't fetch compose file (external stack?): {e}")
            op.log("Fetched compose file from Portainer")
            body = {
                "StackFileContent": file_data["StackFileContent"],
                "Env": stack.get("Env") or [],
                "Prune": True,
                "PullImage": True,
            }
            op.log("Calling Portainer: Prune=true, PullImage=true")
            r = await client.put(
                f"{PORTAINER_URL}/api/stacks/{stack_id}?endpointId={PORTAINER_ENDPOINT_ID}",
                json=body, headers=_headers(),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log(f"Portainer accepted update (HTTP {r.status_code})", "success")
        op.done("success")
        await notify(
            f"✅ Stack updated: {op.target_name}",
            f"Duration: {op.to_dict()['duration']:.1f}s", "success",
        )
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Stack update failed: {op.target_name}", str(e)[:500], "error")
    finally:
        persist_history(op)
        _cache["ts"] = 0


async def _do_update_container(op: Operation, container_id: str):
    try:
        node = _node_for_container(container_id)
        op.log(f"Recreating container with PullImage=true"
               + (f" on node '{node}'" if node else ""))
        async with httpx.AsyncClient(verify=VERIFY_TLS, timeout=600.0) as client:
            r = await client.post(
                f"{PORTAINER_URL}/api/docker/{PORTAINER_ENDPOINT_ID}/containers/"
                f"{container_id}/recreate?PullImage=true",
                headers=_headers(agent_target=node),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log("Container recreated", "success")
        op.done("success")
        await notify(f"✅ Container updated: {op.target_name}", "", "success")
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Container update failed: {op.target_name}", str(e)[:500], "error")
    finally:
        persist_history(op)
        _cache["ts"] = 0


async def _do_restart_container(op: Operation, container_id: str):
    try:
        node = _node_for_container(container_id)
        op.log("Restarting container" + (f" on node '{node}'" if node else ""))
        async with httpx.AsyncClient(verify=VERIFY_TLS, timeout=120.0) as client:
            r = await client.post(
                f"{PORTAINER_URL}/api/endpoints/{PORTAINER_ENDPOINT_ID}/docker/"
                f"containers/{container_id}/restart",
                headers=_headers(agent_target=node),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log("Container restarted", "success")
        op.done("success")
        await notify(f"🔄 Container restarted: {op.target_name}", "", "success")
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Container restart failed: {op.target_name}", str(e)[:500], "error")
    finally:
        persist_history(op)
        _cache["ts"] = 0


async def _do_remove_container(op: Operation, container_id: str):
    try:
        node = _node_for_container(container_id)
        if node:
            op.log(f"Removing container on node '{node}' (force=true, v=true)")
        else:
            op.log("Removing container (force=true, v=true)")
        async with httpx.AsyncClient(verify=VERIFY_TLS, timeout=120.0) as client:
            r = await client.delete(
                f"{PORTAINER_URL}/api/endpoints/{PORTAINER_ENDPOINT_ID}/docker/"
                f"containers/{container_id}?force=true&v=true",
                headers=_headers(agent_target=node),
            )
            # Idempotent removal: if the container is already gone (Swarm
            # cleanup, another operator, a previous click that succeeded
            # after a cache snapshot), 404 is the SAME end-state as a fresh
            # delete. Treat it as success so the operator doesn't see a
            # scary red toast for a no-op. The cache is invalidated in the
            # finally-block regardless, so the row will disappear on the
            # next refresh.
            if r.status_code == 404:
                op.log("Container already gone — treating as success", "success")
            elif r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            else:
                op.log("Container removed", "success")
        op.done("success")
        await notify(f"🗑 Container removed: {op.target_name}", "", "success")
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Container remove failed: {op.target_name}", str(e)[:500], "error")
    finally:
        persist_history(op)
        _cache["ts"] = 0


async def _do_restart_service(op: Operation, service_id: str):
    try:
        op.log("Fetching current service spec")
        async with httpx.AsyncClient(verify=VERIFY_TLS, timeout=300.0) as client:
            svc = await _pg(client, f"/api/endpoints/{PORTAINER_ENDPOINT_ID}/docker/services/{service_id}")
            version = svc["Version"]["Index"]
            spec = svc["Spec"]
            tt = spec.setdefault("TaskTemplate", {})
            tt["ForceUpdate"] = int(tt.get("ForceUpdate", 0)) + 1
            op.log(f"Bumping ForceUpdate to {tt['ForceUpdate']}")
            r = await client.post(
                f"{PORTAINER_URL}/api/endpoints/{PORTAINER_ENDPOINT_ID}/docker/services/"
                f"{service_id}/update?version={version}",
                json=spec, headers=_headers(),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log("Service restart triggered", "success")
        op.done("success")
        await notify(f"🔄 Service restarted: {op.target_name}", "", "success")
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Service restart failed: {op.target_name}", str(e)[:500], "error")
    finally:
        persist_history(op)
        _cache["ts"] = 0


# ============================================================================
# Live container stats (CPU / memory / disk) — cached separately from items
# so the expensive registry digest pass doesn't throttle stats polling.
# ============================================================================
_stats_cache: dict = {"stats": {}, "ts": 0.0}


# ---- Time-series sampler for sparklines ----------------------------------
# Persists a snapshot of `_stats_cache` into `stats_samples` every
# STATS_SAMPLE_INTERVAL seconds. Drives the per-stack sparklines in the UI.
# Runs as a lifespan-managed task (see _lifespan) — NOT at import time — so
# it stays at one-per-process and respects the single-replica invariant.


def _snapshot_stats_to_db() -> int:
    """Write the current _stats_cache into stats_samples. Returns row count."""
    snap = _stats_cache.get("stats") or {}
    if not snap:
        return 0
    ts = time.time()
    rows = [
        (ts, item_id, s.get("cpu_percent") or 0.0,
         s.get("mem_usage") or 0, s.get("mem_limit") or 0)
        for item_id, s in snap.items()
        if s.get("has_stats")
    ]
    if not rows:
        return 0
    with db_conn() as c:
        c.executemany(
            "INSERT INTO stats_samples (ts, item_id, cpu, mem_used, mem_limit) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def _prune_old_samples() -> int:
    """Delete rows older than STATS_HISTORY_DAYS. Returns rows removed."""
    cutoff = time.time() - STATS_HISTORY_DAYS * 86400
    with db_conn() as c:
        cur = c.execute("DELETE FROM stats_samples WHERE ts < ?", (cutoff,))
        return cur.rowcount or 0


async def _stats_sampler_loop():
    # Wait a beat so the first _gather_stats() has a chance to populate
    # _stats_cache before we write a row of zeros.
    await asyncio.sleep(min(60, STATS_SAMPLE_INTERVAL))
    tick = 0
    while True:
        try:
            n = _snapshot_stats_to_db()
            # Prune hourly rather than every tick — single cheap DELETE,
            # but no need to churn on every 5-minute cycle.
            if tick % max(1, 3600 // STATS_SAMPLE_INTERVAL) == 0:
                pruned = _prune_old_samples()
                if pruned:
                    print(f"[sampler] pruned {pruned} rows older than {STATS_HISTORY_DAYS}d")
            if n:
                print(f"[sampler] wrote {n} samples")
        except Exception as e:
            print(f"[sampler] error: {e}")
        tick += 1
        try:
            await asyncio.sleep(STATS_SAMPLE_INTERVAL)
        except asyncio.CancelledError:
            raise


def _stats_history(item_ids: list[str], since: float) -> dict[str, list[dict]]:
    """Return {item_id: [{ts, cpu, mem_used, mem_limit}, ...]} for the given ids
    back to `since` (epoch seconds), oldest-first. Empty list per missing id."""
    if not item_ids:
        return {}
    placeholders = ",".join("?" * len(item_ids))
    out: dict[str, list[dict]] = {i: [] for i in item_ids}
    with db_conn() as c:
        rows = c.execute(
            f"SELECT item_id, ts, cpu, mem_used, mem_limit FROM stats_samples "
            f"WHERE ts >= ? AND item_id IN ({placeholders}) "
            f"ORDER BY ts ASC",
            (since, *item_ids),
        ).fetchall()
    for r in rows:
        out[r["item_id"]].append({
            "ts": r["ts"],
            "cpu": r["cpu"],
            "mem_used": r["mem_used"],
            "mem_limit": r["mem_limit"],
        })
    return out


def _parse_stats_payload(s: dict) -> dict:
    cpu_now = ((s.get("cpu_stats") or {}).get("cpu_usage") or {}).get("total_usage", 0)
    cpu_prev = ((s.get("precpu_stats") or {}).get("cpu_usage") or {}).get("total_usage", 0)
    sys_now = (s.get("cpu_stats") or {}).get("system_cpu_usage", 0)
    sys_prev = (s.get("precpu_stats") or {}).get("system_cpu_usage", 0)
    online = (
        (s.get("cpu_stats") or {}).get("online_cpus")
        or len(((s.get("cpu_stats") or {}).get("cpu_usage") or {}).get("percpu_usage") or [])
        or 1
    )
    cpu_delta = cpu_now - cpu_prev
    sys_delta = sys_now - sys_prev
    cpu_percent = 0.0
    if sys_delta > 0 and cpu_delta > 0:
        cpu_percent = (cpu_delta / sys_delta) * online * 100.0

    mem = s.get("memory_stats") or {}
    mem_usage = mem.get("usage", 0) or 0
    mem_limit = mem.get("limit", 0) or 0
    # Docker's `usage` includes page cache; subtract inactive_file to match `docker stats`.
    mstat = mem.get("stats") or {}
    cache = mstat.get("inactive_file", 0) or mstat.get("cache", 0) or 0
    mem_usage = max(0, mem_usage - cache)
    return {
        "cpu_percent": round(cpu_percent, 1),
        "mem_usage": int(mem_usage),
        "mem_limit": int(mem_limit),
    }


async def _one_container_stats(client: httpx.AsyncClient, ep: str, cid: str, node: Optional[str] = None) -> Optional[dict]:
    """One-shot Docker stats for a running container. Returns None on failure.

    If `node` is known (Swarm task containers), first try with
    `X-PortainerAgent-Target: <node>` on a short timeout. Needed for
    single-replica services on worker nodes where Portainer's default
    aggregation doesn't resolve the container. On any failure, fall back
    to the untargeted call so we don't regress containers that today work
    fine without a target header.
    """
    url = f"{PORTAINER_URL}{ep}/containers/{cid}/stats?stream=false"
    if node:
        try:
            r = await client.get(url, headers=_headers(agent_target=node), timeout=4.0)
            if r.status_code == 200:
                return _parse_stats_payload(r.json())
        except Exception:
            pass
    try:
        r = await client.get(url, headers=_headers(), timeout=10.0)
        if r.status_code != 200:
            return None
        return _parse_stats_payload(r.json())
    except Exception as e:
        print(f"[stats] {cid[:12]}: {e}")
        return None


async def _gather_stats():
    """Compute per-item CPU/memory/disk using existing _cache["items"].

    Services aggregate stats across all their running task containers.
    Standalone containers map directly by ID.
    """
    if not _cache["items"]:
        return
    async with httpx.AsyncClient(verify=VERIFY_TLS, timeout=30.0) as client:
        ep = f"/api/endpoints/{PORTAINER_ENDPOINT_ID}/docker"
        try:
            containers = await _pg(client, f"{ep}/containers/json?all=1&size=1")
        except Exception:
            containers = []

        # Track two sizes per container:
        #   size_root = full image size on disk (SizeRootFs). Always non-zero and
        #               the number a user thinks of when they say "disk size".
        #   size_rw   = writable-layer delta. Useful to spot containers that are
        #               leaking data into their filesystem, but usually ~0.
        size_root_by_cid: dict[str, int] = {}
        size_rw_by_cid: dict[str, int] = {}
        svc_by_cid: dict[str, Optional[str]] = {}
        # cid → hostname for Swarm task containers. Resolved via the task-ID
        # label against task_node_by_id (populated by _gather). Used only as
        # a hint to _one_container_stats; it falls back to no-target on any
        # failure, so plain standalone containers (None here) stay unchanged.
        task_node_by_id = _cache.get("task_node_by_id") or {}
        node_by_cid: dict[str, Optional[str]] = {}
        running_cids: list[str] = []
        for c in containers:
            cid = c["Id"]
            size_root_by_cid[cid] = c.get("SizeRootFs", 0) or 0
            size_rw_by_cid[cid] = c.get("SizeRw", 0) or 0
            labels = c.get("Labels") or {}
            svc_by_cid[cid] = labels.get("com.docker.swarm.service.id")
            task_id = labels.get("com.docker.swarm.task.id")
            node_by_cid[cid] = task_node_by_id.get(task_id) if task_id else None
            if (c.get("State") or "").lower() == "running":
                running_cids.append(cid)

        sem = asyncio.Semaphore(STATS_CONCURRENCY)

        async def fetch(cid: str):
            async with sem:
                return cid, await _one_container_stats(client, ep, cid, node_by_cid.get(cid))

        results = await asyncio.gather(*(fetch(cid) for cid in running_cids))
        stats_by_cid = {cid: s for cid, s in results if s}

        out: dict[str, dict] = {}
        for item in _cache["items"]:
            cpu = 0.0
            mem_usage = 0
            mem_limit = 0
            # Image size is per-image, not per-container. For services with
            # multiple replicas, all replicas share the same image on disk, so
            # we keep ONE representative value instead of summing.
            size_root = 0
            size_rw = 0
            has_stats = False
            has_size = False
            if item.get("type") == "service":
                sid = item["raw_id"]
                for cid, owner in svc_by_cid.items():
                    if owner != sid:
                        continue
                    if cid in size_root_by_cid:
                        # Representative image size — same for every replica.
                        size_root = max(size_root, size_root_by_cid[cid])
                        size_rw += size_rw_by_cid.get(cid, 0)
                        has_size = True
                    st = stats_by_cid.get(cid)
                    if st:
                        cpu += st["cpu_percent"]
                        mem_usage += st["mem_usage"]
                        # Sum limits across replicas — 3 replicas at 1 GB each
                        # mean the service's effective limit is 3 GB. Without
                        # this, a perfectly-utilised service could exceed 100%.
                        mem_limit += st["mem_limit"]
                        has_stats = True
            else:
                cid = item["raw_id"]
                if cid in size_root_by_cid:
                    size_root = size_root_by_cid[cid]
                    size_rw = size_rw_by_cid.get(cid, 0)
                    has_size = True
                st = stats_by_cid.get(cid)
                if st:
                    cpu = st["cpu_percent"]
                    mem_usage = st["mem_usage"]
                    mem_limit = st["mem_limit"]
                    has_stats = True
            out[item["id"]] = {
                "cpu_percent": round(cpu, 1),
                "mem_usage": int(mem_usage),
                "mem_limit": int(mem_limit),
                "size_root": int(size_root),
                "size_rw": int(size_rw),
                "has_stats": has_stats,
                "has_size": has_size,
            }
        _stats_cache["stats"] = out
        _stats_cache["ts"] = time.time()


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
    # Authentik Forward Auth settings (admin-editable).
    # `npm_auth_secret`: empty string or None is interpreted as "don't
    # change" — the browser never receives the current value, so we need
    # a sentinel for "keep what's there". Pass a non-empty string to
    # overwrite.
    authentik_enabled: Optional[bool] = None
    authentik_admin_group: Optional[str] = None
    npm_auth_secret: Optional[str] = None


@app.get("/api/settings")
async def api_get_settings():
    with db_conn() as c:
        a = auth.get_auth_settings(c)
    return {
        "apprise_url": get_setting("apprise_url", ""),
        "apprise_tag": get_setting("apprise_tag", ""),
        "portainer_public_url": get_setting("portainer_public_url", PORTAINER_URL),
        "endpoint_id": PORTAINER_ENDPOINT_ID,
        # Authentik: state is editable from UI. NPM_AUTH_SECRET is never
        # returned in the clear — we only report whether it's set so the
        # UI can render "configured" vs "not set" without ever exposing
        # the value to a browser.
        "authentik": {
            "enabled": bool(a.get("authentik_enabled")),
            "admin_group": a.get("authentik_admin_group") or "",
            "npm_auth_secret_set": bool(a.get("npm_auth_secret")),
        },
    }


@app.post("/api/settings")
async def api_set_settings(
    s: SettingsIn,
    _admin: auth.User = Depends(auth.require_admin),
):
    if s.apprise_url is not None: set_setting("apprise_url", s.apprise_url)
    if s.apprise_tag is not None: set_setting("apprise_tag", s.apprise_tag)
    if s.portainer_public_url is not None: set_setting("portainer_public_url", s.portainer_public_url)
    auth_changed = False
    with db_conn() as c:
        if s.authentik_enabled is not None:
            auth.set_auth_setting(c, "authentik_enabled",
                                  "true" if s.authentik_enabled else "false")
            auth_changed = True
        if s.authentik_admin_group is not None:
            auth.set_auth_setting(c, "authentik_admin_group",
                                  s.authentik_admin_group.strip())
            auth_changed = True
        # Empty/None = "keep current" so the UI can send the form without
        # knowing the existing value. A whitespace-only string also treated
        # as no-op; to truly clear the secret, admins delete via a future
        # "clear" button (not implemented — zero current use case).
        if s.npm_auth_secret is not None and s.npm_auth_secret.strip() != "":
            auth.set_auth_setting(c, "npm_auth_secret", s.npm_auth_secret)
            auth_changed = True
    if auth_changed:
        auth.invalidate_auth_settings_cache()
    return {"status": "ok"}


@app.post("/api/notify-test")
async def api_notify_test(_admin: auth.User = Depends(auth.require_admin)):
    await notify("🔔 PortaUpdate test", "Notifications are wired up correctly!", "success")
    return {"status": "sent"}


@app.get("/api/healthz")
async def healthz():
    return {
        "ok": True,
        "version": APP_VERSION,
        "cache_age": int(time.time() - _cache["ts"]) if _cache["ts"] else None,
    }


@app.get("/api/version")
async def api_version():
    return {"version": APP_VERSION}


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
    to redirect to /login.
    """
    user = getattr(request.state, "user", None)
    if not user:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "username": user.username,
        "role": user.role,
        "source": user.auth_source,
    }


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


# Login HTML page. Served as a discrete route (not via StaticFiles) because
# /login has no trailing slash and we want it to map to static/login.html
# directly without relying on html=True directory-index behaviour. Also
# listed in auth.FULLY_PUBLIC_PREFIXES so the middleware never gates it.
@app.get("/login")
async def login_page():
    return FileResponse("static/login.html")


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


# Keep this line LAST — StaticFiles at "/" is a catch-all.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
