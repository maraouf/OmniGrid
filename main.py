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
import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional, Set

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
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from logic import auth, backups, errors as _err, events as _events, metrics, oidc, schedules, totp
from logic import webauthn_helper as webauthn_h
from pydantic import BaseModel, field_validator

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
# logic.portainer.get_portainer_settings(). Process-level tunables
# (cache TTLs, concurrency caps, sample interval, history days) resolve
# via logic.tuning (DB > env > default) — see #337.
from logic import db as _db  # noqa: E402
from logic.db import DB_PATH, db_conn, get_setting, get_setting_bool, set_setting, active_host_stats_providers  # noqa: E402,F401
from logic import tuning  # noqa: E402
DOCKERHUB_USER = os.getenv("DOCKERHUB_USER", "")
DOCKERHUB_TOKEN = os.getenv("DOCKERHUB_TOKEN", "")

# Bootstrap-only env vars for seeding the first admin. Only consulted when
# the users table is empty at startup — safe to leave set or unset afterward.
BOOTSTRAP_ADMIN_USER = os.getenv("BOOTSTRAP_ADMIN_USER", "")
BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")

# Notification event names + per-event default state — single source of
# truth lives in ``logic.ops`` so ``notify()`` and ``api_get_settings``
# read the same map.
# previously these were defined here AND duplicated as a hardcoded
# ``default=True`` inside ``notify()`` — fresh deploys fired user_login
# notifications even though the admin form claimed the toggle was off.
from logic.ops import (  # noqa: E402
    NOTIFY_EVENT_NAMES as _NOTIFY_EVENT_NAMES,
    NOTIFY_EVENT_DEFAULTS as _NOTIFY_EVENT_DEFAULTS,
)


# TOTP / 2FA policy defaults (#345). DB > default. Same shape as the
# notify_event_* defaults map above so api_get_settings reads through
# get_setting / get_setting_bool with these as the fallbacks.
_TOTP_POLICY_DEFAULTS = {
    "totp_allowed":               True,
    "totp_required_for_admins":   False,
    "totp_required_for_users":    False,
    "totp_lockout_max_failures":  5,
    "totp_lockout_minutes":       15,
    # Passkey master toggle (#432). Mirrors `totp_allowed`. When OFF,
    # `register-start` returns 403; existing enrolments stay valid for
    # login until each user revokes (or admin clears via reset).
    "passkeys_allowed":           True,
}


# TOTP-policy resolution cache (#470 / ENH-003). The login flow calls
# `_resolve_totp_policy()` 6+ times per typical sign-in, each previously
# hitting 6 DB rows. A 2-second TTL collapses the burst into one read
# without making settings changes feel laggy (the Admin -> Config Save
# explicitly invalidates via `_invalidate_totp_policy_cache()`). The
# value is process-local and small (a 6-key dict).
_totp_policy_cache: dict = {"value": None, "ts": 0.0}
_TOTP_POLICY_CACHE_TTL_SECONDS = 2.0


def _invalidate_totp_policy_cache() -> None:
    """Force `_resolve_totp_policy()` to re-read from DB on the next call.
    Call from every code path that mutates a `totp_*` or `passkeys_allowed`
    setting so a Save in the Admin UI takes effect within one tick."""
    _totp_policy_cache["value"] = None
    _totp_policy_cache["ts"] = 0.0


def _resolve_totp_policy() -> dict:
    """Return the resolved TOTP policy as a dict with concrete types.

    Caller (login flow + admin override + Profile guards) only needs to
    read scalar booleans / ints. No env vars are consulted -- this is
    purely DB-backed (Admin -> Config edits the values).

    Cached for `_TOTP_POLICY_CACHE_TTL_SECONDS` (#470 / ENH-003) — every
    login flow makes 6+ calls in quick succession, each previously
    hitting the DB. The cache is invalidated on every settings write
    via `_invalidate_totp_policy_cache()` so admin edits take effect
    immediately.
    """
    cached_value = _totp_policy_cache.get("value")
    cached_ts = _totp_policy_cache.get("ts") or 0.0
    if cached_value is not None and (time.time() - cached_ts) < _TOTP_POLICY_CACHE_TTL_SECONDS:
        return cached_value
    resolved = {
        "totp_allowed":              get_setting_bool(
            "totp_allowed", _TOTP_POLICY_DEFAULTS["totp_allowed"],
        ),
        "totp_required_for_admins":  get_setting_bool(
            "totp_required_for_admins",
            _TOTP_POLICY_DEFAULTS["totp_required_for_admins"],
        ),
        "totp_required_for_users":   get_setting_bool(
            "totp_required_for_users",
            _TOTP_POLICY_DEFAULTS["totp_required_for_users"],
        ),
        "totp_lockout_max_failures": int(
            get_setting(
                "totp_lockout_max_failures",
                str(_TOTP_POLICY_DEFAULTS["totp_lockout_max_failures"]),
            ) or _TOTP_POLICY_DEFAULTS["totp_lockout_max_failures"]
        ),
        "totp_lockout_minutes":      int(
            get_setting(
                "totp_lockout_minutes",
                str(_TOTP_POLICY_DEFAULTS["totp_lockout_minutes"]),
            ) or _TOTP_POLICY_DEFAULTS["totp_lockout_minutes"]
        ),
        "passkeys_allowed":          get_setting_bool(
            "passkeys_allowed", _TOTP_POLICY_DEFAULTS["passkeys_allowed"],
        ),
    }
    _totp_policy_cache["value"] = resolved
    _totp_policy_cache["ts"] = time.time()
    return resolved


def _totp_required_for(role: str, policy: Optional[dict] = None) -> bool:
    """Is TOTP required for the given role under current policy?"""
    p = policy or _resolve_totp_policy()
    if not p["totp_allowed"]:
        return False
    if role == "admin":
        return bool(p["totp_required_for_admins"])
    return bool(p["totp_required_for_users"])

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
    # ARCH-002 — wrap init_db so a failure short-circuits to the same
    # config-error code path as DB_PATH_ERROR. Background tasks DON'T
    # spawn on a partial schema; operator sees a readable diagnostic
    # in the browser instead of the samplers crash-looping against a
    # half-initialised DB.
    try:
        init_db()
    except Exception as e:
        _db_mod.DB_PATH_ERROR = f"init_db failed: {e}"
        print(f"[boot] CONFIG ERROR: init_db failed: {e}")
        print("[boot] Skipping every background worker until init_db can complete. "
              "The app is serving the config-error page.")
        yield
        return
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

    # ARCH-001 — pre-seed caches from the persisted snapshot tables in
    # the BACKGROUND so a large stats_samples table can't delay the
    # FastAPI app from accepting connections. The CTE in
    # seed_stats_cache_from_db (`ROW_NUMBER OVER PARTITION BY item_id`)
    # can take several seconds with cold cache + months of history;
    # before this change that delay sat synchronously inside lifespan
    # startup, eating into Swarm's 60s start_period budget. Caches are
    # consulted lazily by `/api/items` + `/api/stats` so a brief gap
    # before they're populated just means the first request after boot
    # gets fresh data instead of cached — fine.
    async def _seed_caches_bg():
        try:
            from logic import stats as _stats_mod
            n_stats = _stats_mod.seed_stats_cache_from_db()
            if n_stats:
                print(f"[boot] seeded {n_stats} stats entries from stats_samples")
        except Exception as e:
            print(f"[boot] seed_stats_cache_from_db failed: {e}")
        try:
            from logic import gather as _gather_mod
            n_hosts = _gather_mod.seed_nodes_info_from_snapshots()
            if n_hosts:
                print(f"[boot] seeded {n_hosts} host snapshots from host_snapshots")
        except Exception as e:
            print(f"[boot] seed_nodes_info_from_snapshots failed: {e}")

    seed_task = asyncio.create_task(_seed_caches_bg(), name="boot-seed-caches")
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
    # Per-host historical metrics sampler — feeds the host drawer charts
    # for nodes that don't have a Beszel agent. Same lifespan-only +
    # skip-don't-synthesize discipline as host_net_sampler.
    from logic import host_metrics_sampler as _host_metrics_sampler
    host_metrics_sampler = asyncio.create_task(
        _host_metrics_sampler.host_metrics_sampler_loop(),
        name="host-metrics-sampler",
    )
    # Ping reachability sampler (#343) — TCP-connect (or optional ICMP)
    # probes for hosts that opt in via hosts_config[].ping.enabled.
    # Same lifespan-only contract; dormant when "ping" isn't in
    # host_stats_source. Writes to ping_samples; pubs `host:ping_sampled`.
    from logic import ping_sampler as _ping_sampler
    ping_sampler = asyncio.create_task(
        _ping_sampler.ping_sampler_loop(), name="ping-sampler",
    )
    # Persistent-log pruner (#424) — sweeps /app/data/logs/ once per
    # hour, deletes any omnigrid-YYYY-MM-DD.log older than the
    # operator-tunable retention window.
    log_pruner = asyncio.create_task(_log_pruner_loop(), name="log-pruner")
    try:
        yield
    finally:
        # Cancel in reverse-start order. Each cancel + await is wrapped so
        # one failing shutdown step can't starve the next one.
        for task in (log_pruner, ping_sampler, host_metrics_sampler, host_net_sampler, scheduler, sampler, seed_task):
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
# SSE bus health collectors (#472 / ENH-005). Wires
# `omnigrid_events_subscribers` + `omnigrid_events_dropped` on the
# Prometheus registry so /metrics surfaces queue health alongside the
# cache-age collector.
metrics.register_events_collectors(
    subscriber_count=_events.bus.subscriber_count,
    dropped_count=_events.bus.dropped_count,
)


# ============================================================================
# SQLite persistence — db_conn / get_setting / set_setting live in logic/db.py.
# init_db() stays here as the boot orchestrator: it creates the core tables
# (history / ignores / settings / stats_samples) and delegates to module
# schema hooks (auth.init_auth_schema) for module-owned tables.
# ============================================================================
def init_db():
    with db_conn() as c:
        # Wrap the whole schema-create script in an explicit transaction
        # so a power loss / hard kill mid-init can't leave a half-applied
        # schema. Every statement in here is idempotent (CREATE IF NOT
        # EXISTS / ALTER ... except OperationalError) so the worst case
        # was always recoverable, but rolling-back an interrupted boot
        # is cleaner than racing with `IF NOT EXISTS` on the next start.
        # BUG-011 in the code review.
        c.executescript("""
        BEGIN;
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

        -- Per-host historical CPU/memory/disk/network samples for
        -- node-exporter-only hosts (no Beszel agent). Populated by
        -- logic/host_metrics_sampler.py at STATS_SAMPLE_INTERVAL_SECONDS
        -- cadence; pruned to STATS_HISTORY_DAYS. Sibling table to
        -- host_net_samples — same skip-don't-synthesize discipline for
        -- the net rate columns. CPU/mem/disk are point-in-time gauges
        -- and stored verbatim (NULL when the probe didn't return a
        -- meaningful value).
        CREATE TABLE IF NOT EXISTS host_metrics_samples (
            ts             INTEGER NOT NULL,
            host_id        TEXT    NOT NULL,
            cpu_percent    REAL,
            mem_used       INTEGER,
            mem_total      INTEGER,
            disk_used      INTEGER,
            disk_total     INTEGER,
            net_rx_bps     REAL,
            net_tx_bps     REAL,
            disk_read_bps  REAL,
            disk_write_bps REAL,
            PRIMARY KEY (ts, host_id)
        );
        CREATE INDEX IF NOT EXISTS idx_host_metrics_samples_host_ts
            ON host_metrics_samples(host_id, ts DESC);

        -- Last-known per-host nodes_info blob (Beszel / Pulse /
        -- node-exporter / Webmin merged). Written at the end of every
        -- successful gather, read at startup AND on every gather to
        -- fill in missing host_* fields when a provider is down.
        -- Operators see "stale" data instead of empty bars when a
        -- provider goes offline. One row per host (PK = host); the
        -- ``data`` column carries the JSON blob.
        CREATE TABLE IF NOT EXISTS host_snapshots (
            host TEXT PRIMARY KEY,
            ts REAL NOT NULL,
            data TEXT NOT NULL
        );
        -- Permanent-fail tracking (#383). One row per host whose
        -- host_metrics_sampler has hit consecutive probe failures. When
        -- ``paused`` flips to 1, the sampler short-circuits subsequent
        -- ticks (no probe attempt, no log spam) until the operator
        -- explicitly resumes via POST /api/hosts/{id}/resume-sampling.
        -- ``first_failure_ts`` is the wall-clock of the FIRST failure
        -- in the current streak; the auto-pause fires when
        -- ``now - first_failure_ts`` exceeds the configured window.
        CREATE TABLE IF NOT EXISTS host_failure_state (
            host_id TEXT PRIMARY KEY,
            first_failure_ts REAL NOT NULL,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            paused INTEGER NOT NULL DEFAULT 0,
            paused_at REAL,
            last_error TEXT
        );

        -- Ping reachability time-series (#343). Populated by
        -- logic/ping_sampler.py at tuning_ping_interval_seconds
        -- cadence; pruned to tuning_stats_history_days (reuses the
        -- existing retention knob — no separate ping retention).
        -- ``alive`` is INTEGER 0/1 (SQLite has no native bool). RTT
        -- columns NULL when the probe got no responses.
        CREATE TABLE IF NOT EXISTS ping_samples (
            ts         INTEGER NOT NULL,
            host_id    TEXT    NOT NULL,
            alive      INTEGER NOT NULL,
            rtt_ms     REAL,
            rtt_min_ms REAL,
            rtt_max_ms REAL,
            loss_pct   REAL,
            PRIMARY KEY (ts, host_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ping_samples_host_ts
            ON ping_samples(host_id, ts DESC);
        COMMIT;
        """)
        # Idempotent column additions for existing deployments. SQLite pre-3.35
        # has no "ADD COLUMN IF NOT EXISTS", so we catch the OperationalError
        # that gets raised when the column already exists. Safe to re-run on
        # every boot.
        for ddl in (
            "ALTER TABLE history ADD COLUMN actor TEXT DEFAULT 'ui'",
            "ALTER TABLE history ADD COLUMN target_stack TEXT",
            # #339 — disk I/O rates, derived per-tick by
            # host_metrics_sampler from node_disk_{read,written}_bytes_total.
            # Same skip-don't-synthesize discipline as the net rate columns;
            # NULL when the delta is out of bounds.
            "ALTER TABLE host_metrics_samples ADD COLUMN disk_read_bps REAL",
            "ALTER TABLE host_metrics_samples ADD COLUMN disk_write_bps REAL",
            # ENH-018 — wall-clock of the MOST RECENT probe failure.
            # ``first_failure_ts`` already records the start of the
            # streak; this is the timestamp of the latest failed
            # probe so the drawer can render "last error N seconds
            # ago" instead of leaving the operator wondering whether
            # the issue may have already cleared.
            "ALTER TABLE host_failure_state ADD COLUMN last_failure_ts REAL",
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
        # Schema migrations infrastructure (ARCH-002). Adds the
        # `schema_migrations` table and applies any pending migrations
        # registered in `logic/migrations.py:MIGRATIONS`. Empty registry
        # today — additive changes still go in the CREATE TABLE block
        # above. Non-additive changes (renames, type changes, data
        # migrations) get a numbered migration function. Boot halts on
        # migration failure so a half-applied schema can't slip through.
        from logic import migrations as _migrations
        _migrations.init_migrations_schema(c)
        _migrations.apply_pending(c)


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
notify_with_retry = _ops_mod.notify_with_retry
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
# Persistent-log pruner (#424). Once per hour, walks /app/data/logs/ and
# drops any daily file older than `tuning_log_retention_days`. The first
# tick is delayed 60s after boot so the lifespan startup has finished
# emitting its banner lines before the sweep runs (cosmetic).
# ============================================================================
async def _log_pruner_loop() -> None:
    import asyncio
    from logic import logs as _logs_mod
    await asyncio.sleep(60)
    while True:
        try:
            days = tuning.tuning_int("tuning_log_retention_days")
            removed = _logs_mod.prune_old_logs(days)
            if removed:
                print(f"[logs] pruned {removed} log file(s) older than {days}d")
        except Exception as e:
            print(f"[logs] pruner tick error: {e}")
        await asyncio.sleep(3600)


# ============================================================================
# API endpoints
# ============================================================================
@app.get("/api/stats")
async def api_stats(force: bool = False):
    now = time.time()
    if force or not _stats_cache["stats"] or (now - _stats_cache["ts"] > tuning.tuning_int("tuning_stats_cache_ttl_seconds")):
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
    hours = max(1, min(hours, tuning.tuning_int("tuning_stats_history_days") * 24))
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
    if force or not _cache["items"] or (now - _cache["ts"] > tuning.tuning_int("tuning_cache_ttl_seconds")):
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
        # UX-003: lets the SPA distinguish "no items + Portainer connected"
        # (legitimate empty cluster) from "no items because Portainer was
        # never configured" (point operator at Settings → Portainer).
        # Reading this avoids loading the full /api/settings payload just
        # to render an empty-state hint. Module-level import is
        # ``portainer`` (line 384); ``_portainer`` only exists inside
        # function bodies that re-import it locally — using the bare
        # name here is what matches scope.
        "portainer_configured": portainer.is_configured(),
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


# ============================================================================
# Real-time event stream (SSE)
# ----------------------------------------------------------------------------
# Replaces the SPA's polling cadence on cookie-authed callers. Bearer-token
# machine clients can't easily set custom request headers via EventSource so
# they keep polling — that's documented in `docs/guidelines/api.md`.
#
# Auth: middleware enforces 401 on missing identity for every /api/* path
# except the documented public/auth-optional set, so this route inherits the
# standard cookie-OR-bearer check just like /api/ops or /api/items.
#
# CSRF: SSE is GET-only; no CSRF cookie check applies (the global middleware
# only runs CSRF on state-changing methods).
#
# Heartbeat: emit a real ``event: keepalive`` line every 25s. NOT a
# comment line — `EventSource.onmessage` doesn't fire for SSE comments,
# so a comment-only heartbeat keeps the TCP socket alive but never
# reaches the SPA's freshness watchdog (which advances
# `_sseLastEventTs` only on real events). Pre-#561 the comment-form
# caused a 30s-quiet-window false-flip into polling-fallback mode even
# though the connection was healthy. The real-event form lets the
# generic onmessage listener bump the timestamp on every heartbeat,
# AND keeps the socket-warm property the comment had.
# ============================================================================
# #537 / #538 — both moved to TUNABLES (tuning_sse_heartbeat_seconds,
# tuning_sse_max_lifetime_seconds). Resolve at the consumer site via
# `tuning.tuning_int(...)` so a Save in Admin → Config takes effect on
# the next /api/events reconnect — no module-level constants here so a
# stale import-time read can't pin the old value. The historical
# defaults (25s heartbeat, 6h lifetime — 1h margin before session 8h
# hard cap) are preserved as the TUNABLES defaults; bounds on the
# lifetime knob (3600-25200s = 1h-7h) prevent an operator from racing
# past the session hard cap.


def _format_sse(evt: dict) -> str:
    """One SSE record per event. ``event:`` carries the type, ``data:``
    is JSON.

    Handles the special ``:overflow`` synthetic emitted by
    ``logic.events`` when a subscriber's queue dropped events — the
    SPA reacts by doing a one-shot REST refresh to catch up.
    """
    ev_type = evt.get("type") or "message"
    payload = {
        "type": ev_type,
        "ts": evt.get("ts"),
        "payload": evt.get("payload") or {},
    }
    return f"event: {ev_type}\ndata: {json.dumps(payload, default=str)}\n\n"


@app.get("/api/events")
async def api_events(request: Request):
    """Server-sent events stream — one connection per SPA tab.

    The SPA's polling loops idle while this connection is healthy; if
    the connection drops, polling resumes within ~30s as the fallback
    safety net (see static/js/app.js:_sseConnected).
    """

    async def event_stream():
        # ``hello`` lands as the first frame so the client can confirm
        # the upgrade succeeded BEFORE waiting for the first organic
        # event. Carries process-level diagnostics that the connection-
        # state indicator surfaces in its tooltip.
        # #537 — heartbeat cadence is operator-tunable; resolve per
        # connection-open so a Save takes effect on the next reconnect.
        heartbeat_seconds = tuning.tuning_int("tuning_sse_heartbeat_seconds")
        max_lifetime_seconds = tuning.tuning_int("tuning_sse_max_lifetime_seconds")
        yield _format_sse({
            "type": "hello",
            "ts": time.time(),
            "payload": {
                "subscriber_count": _events.subscriber_count(),
                "heartbeat_seconds": heartbeat_seconds,
            },
        })

        async def producer(queue: asyncio.Queue):
            """Consume the event-bus iterator and forward into a local
            queue. Runs as a task so we can race it against the
            heartbeat timer + the disconnect check.

            #535 — `queue.put_nowait` with overflow synthesis: pre-fix
            this awaited an unbounded `queue.put`, so a slow client
            could let the local queue grow without bound while the
            bus's drop-oldest cap (256) stayed satisfied (because we
            moved events off the bus queue immediately). Now we mirror
            the bus's bound; on `QueueFull`, drop the new event and
            emit a synthetic `:local-overflow` hint so the SPA can
            reconcile via REST (same recovery path the existing
            `:overflow` triggers).
            """
            try:
                async for evt in _events.bus.subscribe():
                    try:
                        queue.put_nowait(evt)
                    except asyncio.QueueFull:
                        try:
                            queue.put_nowait({
                                "type": ":local-overflow",
                                "ts": time.time(),
                                "payload": {"dropped_type": evt.get("type")},
                            })
                        except asyncio.QueueFull:
                            # Even the overflow signal didn't fit —
                            # the consumer is stuck. Drop silently;
                            # the outer disconnect-check will reap
                            # the connection on the next iteration.
                            pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Defensive — log and signal end-of-stream so the
                # outer loop exits cleanly on bus malfunction.
                print(f"[events] subscribe iterator failed: {e}")
                await queue.put(None)

        local: asyncio.Queue = asyncio.Queue(maxsize=256)
        task = asyncio.create_task(producer(local))
        started_at = time.time()
        try:
            while True:
                if await request.is_disconnected():
                    break
                # Cap the connection's wall-clock lifetime so the auth
                # middleware re-fires on the EventSource reconnect and
                # the session cookie's sliding-window refresh has a
                # chance to land before the 8h hard cap (#464 /
                # BUG-009). Emit a synthetic `reconnect` hint so the
                # SPA logs the cycle in dev-tools network tab; the
                # `EventSource` API itself reconnects automatically on
                # any normal end-of-stream.
                if (time.time() - started_at) > max_lifetime_seconds:
                    yield _format_sse({
                        "type": "reconnect",
                        "ts": time.time(),
                        "payload": {"reason": "lifetime_cap"},
                    })
                    break
                try:
                    evt = await asyncio.wait_for(
                        local.get(), timeout=heartbeat_seconds,
                    )
                except asyncio.TimeoutError:
                    # No traffic for the heartbeat window — keep the
                    # socket warm AND give the SPA's freshness watchdog
                    # something to consume so it doesn't false-flip
                    # to polling-fallback during quiet periods (#561).
                    # Emitted as a real `event: keepalive` line (NOT a
                    # `: comment` line) because EventSource fires
                    # `onmessage` only for real events; comment lines
                    # arrive at the socket but never reach the SPA's
                    # event handler that advances `_sseLastEventTs`.
                    # Empty JSON payload — the event's existence is
                    # the signal, no fields to carry.
                    yield "event: keepalive\ndata: {}\n\n"
                    continue
                if evt is None:
                    # Bus signalled end-of-stream; propagate cleanly.
                    break
                yield _format_sse(evt)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # Disable upstream buffering — nginx + NPM both proxy SSE
            # by default but the X-Accel-Buffering hint guarantees the
            # bytes flush per event instead of being chunked.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _history_query(
    stack: Optional[str], op_type: Optional[str], status: Optional[str],
    actor: Optional[str], q: Optional[str],
    since: Optional[float], until: Optional[float],
    limit: int,
    offset: int = 0,
    *,
    with_total: bool = False,
):
    """Shared builder for filterable history queries. All filters are
    optional; missing ones degrade gracefully to an unfiltered scan.

    When ``with_total=True`` the return value is ``(rows, total)`` —
    ``total`` is the unpaginated COUNT(*) for the same WHERE clause,
    used by the SPA's server-side pager. Default ``with_total=False``
    preserves the legacy list-only return shape so the export endpoints
    don't pay the extra query.
    """
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
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    eff_limit = max(1, min(limit, 5000))
    eff_offset = max(0, int(offset or 0))
    data_sql = f"SELECT * FROM history{where_sql} ORDER BY ts DESC LIMIT ? OFFSET ?"
    data_params = list(params) + [eff_limit, eff_offset]
    with db_conn() as c:
        rows = c.execute(data_sql, data_params).fetchall()
        if with_total:
            count_sql = f"SELECT COUNT(*) AS n FROM history{where_sql}"
            total_row = c.execute(count_sql, params).fetchone()
            total = int(total_row["n"] if total_row else 0)
    rows_out = [dict(r) for r in rows]
    if with_total:
        return rows_out, total
    return rows_out


@app.get("/api/history")
async def api_history(
    limit: int = 100,
    offset: int = 0,
    stack: Optional[str] = None,
    op_type: Optional[str] = None,
    status: Optional[str] = None,
    actor: Optional[str] = None,
    q: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
):
    rows, total = _history_query(
        stack, op_type, status, actor, q, since, until,
        limit, offset=offset, with_total=True,
    )
    return {
        "history": rows,
        "total":   total,
        "offset":  max(0, int(offset or 0)),
        "limit":   max(1, min(int(limit or 100), 5000)),
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

    @field_validator("kind")
    @classmethod
    def _kind_must_be_known(cls, v: str) -> str:
        # ``logic.gather.is_ignored`` only honours these two values; a
        # typo silently inserted a no-op row before this validator.
        # Reject early with a clear 422 from FastAPI so the operator
        # learns the typo at edit time rather than wondering why their
        # ignore rule isn't taking effect.
        normalised = (v or "").strip().lower()
        if normalised not in ("image", "stack"):
            raise ValueError("kind must be 'image' or 'stack'")
        return normalised


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
    # Per-service "enabled" master switches. Default true (legacy
    # behaviour preserved on first boot). When false, the service's
    # consumer code short-circuits — values stay in the settings
    # table so the operator can flip back on without re-typing. The
    # admin UI also disables the inputs visually so the operator
    # sees the saved config grayed out, not erased.
    apprise_enabled: Optional[bool] = None
    open_meteo_enabled: Optional[bool] = None
    portainer_enabled: Optional[bool] = None
    ssh_enabled: Optional[bool] = None
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
    # ENH-002 / #469 — case-insensitive admin-group claim match. Default
    # True preserves the legacy exact-match contract.
    oidc_group_case_sensitive: Optional[bool] = None
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
    #   {"docker01": "docker.example.com"}
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
    # (e.g. ``{"docker01": "https://docker.example.com:10000"}``).
    # ``webmin_url`` is retained as an optional default/template for
    # future use. Password is write-only like every other secret.
    webmin_url: Optional[str] = None
    webmin_user: Optional[str] = None
    webmin_password: Optional[str] = None
    webmin_verify_tls: Optional[bool] = None
    webmin_aliases: Optional[dict] = None
    # Ping (#343) — fifth host-stats provider. Reachability + RTT only,
    # opt-in per host (hosts_config[].ping.enabled). No credentials, no
    # aliases — the provider runs against the host's own id (or the
    # per-host SSH FQDN override). ``ping_default_port`` is the TCP port
    # used when a per-host row doesn't override; ``ping_use_icmp`` flips
    # the global default transport when the icmplib package is present
    # AND the container has CAP_NET_RAW (per-host ``transport``
    # overrides individually). Three matching tunables resolve via
    # logic/tuning.py.
    ping_enabled: Optional[bool] = None
    ping_default_port: Optional[int] = None
    ping_use_icmp: Optional[bool] = None
    # SNMP (#344) — sixth host-stats provider. Per-host probe (no
    # central hub). Defaults are global; per-host overrides live on
    # ``hosts_config[].snmp = {community, version, port, v3_*}``.
    # ``snmp_default_community`` defaults to "public" (the common read-
    # only community on home-lab gear); ``snmp_default_version``
    # accepts "v2c" or "v3"; ``snmp_default_port`` defaults to 161.
    # The three v3 keys (user / auth-key / priv-key) follow the same
    # write-only ``_set`` flag contract as every other secret — empty
    # input keeps the current value, non-empty replaces it.
    # ``snmp_aliases`` maps Docker hostname → SNMP target IP/host so
    # the probe can hit a different address than the curated row's id.
    snmp_default_community: Optional[str] = None
    snmp_default_version: Optional[str] = None
    snmp_default_port: Optional[int] = None
    snmp_v3_user: Optional[str] = None
    snmp_v3_auth_key: Optional[str] = None
    snmp_v3_priv_key: Optional[str] = None
    snmp_aliases: Optional[dict] = None
    # Per-provider chip color (#596) — operator-customisable hex colour
    # for the per-host provider chip rendered in the Hosts view + the
    # drawer's "Enabled agents" card. Each value is a 7-char `#RRGGBB`
    # string OR blank to fall back to the SPA's built-in default. The
    # `failing` red chip is unaffected (it intentionally stays a
    # uniform error colour regardless of the provider's normal hue).
    provider_color_beszel: Optional[str] = None
    provider_color_pulse: Optional[str] = None
    provider_color_node_exporter: Optional[str] = None
    provider_color_webmin: Optional[str] = None
    provider_color_ping: Optional[str] = None
    provider_color_snmp: Optional[str] = None
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
    # Asset inventory V1 — OAuth2 client_credentials against <asset-api-host>.
    # Secret is write-only (see api_set_settings keep-if-blank rule);
    # admin clears via clear_asset_inventory_client_secret flag.
    asset_inventory_base_url: Optional[str] = None
    asset_inventory_token_url: Optional[str] = None
    asset_inventory_client_id: Optional[str] = None
    asset_inventory_client_secret: Optional[str] = None
    asset_inventory_scope: Optional[str] = None
    clear_asset_inventory_client_secret: Optional[bool] = None
    # #417 / ENH-001 — TLS verification toggle for the asset API.
    # Default True; flip to False for self-signed homelab endpoints.
    asset_inventory_verify_tls: Optional[bool] = None
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
    # lifetime-token flavour. <asset-api-host>'s services.php routes by these
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
    # Edit-on-upstream URL template used by the host drawer's
    # "Edit on <asset-api-host>" link. Placeholders: {id} (asset DB id),
    # {custom_number} (asset CustomNumber), {base} (the configured
    # base_url). Blank → no link rendered. Operator-configured
    # because <asset-api-host>'s URL scheme isn't part of the API guide.
    asset_inventory_edit_url_template: Optional[str] = None
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
    # ssh_fqdn_suffix=".example.com" → "webserver.example.com". Host IDs that
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
    # Show the host-drawer admin debug panel (raw provider JSON +
    # merged shape). Default ``true`` preserves the legacy behaviour.
    # When false, the panel is hidden for everyone (including admins);
    # other admin tools on the drawer remain visible.
    debug_panel_enabled: Optional[bool] = None
    # -----------------------------------------------------------------
    # Process-level tunables (#337). DB > env > default — see
    # logic/tuning.py:TUNABLES. Every field is Optional[str] so blank
    # ("") clears the override and falls back to the env var; missing
    # = "leave alone". Bounds-checked at write time against TUNABLES.
    # -----------------------------------------------------------------
    tuning_cache_ttl_seconds: Optional[str] = None
    tuning_stats_cache_ttl_seconds: Optional[str] = None
    tuning_registry_concurrency: Optional[str] = None
    tuning_stats_concurrency: Optional[str] = None
    tuning_stats_history_days: Optional[str] = None
    tuning_stats_sample_interval_seconds: Optional[str] = None
    # #410 — host_metrics_sampler permanent-fail window. Same DB-key
    # naming + bounds-check via TUNABLES as the others.
    tuning_host_permanent_fail_window_seconds: Optional[str] = None
    # #417 — frontend /api/ops poll cadence in SECONDS (#514 — was
    # `tuning_ops_poll_interval_ms`; renamed for operator-friendly UI).
    # The SPA reads the effective value (× 1000) via /api/me's
    # `client_config.ops_poll_ms` and uses it as the setTimeout delay
    # between consecutive ops polls.
    tuning_ops_poll_interval_seconds: Optional[str] = None
    # #424 — persistent-log retention in days. Daily files under
    # /app/data/logs/ older than this get deleted by the lifespan
    # _log_pruner_loop().
    tuning_log_retention_days: Optional[str] = None
    # #467 — host-snapshots read-side cache TTL (seconds). The SPA fans
    # out N parallel /api/hosts/one/{id} per refresh; caching the
    # snapshot-table read for a few seconds collapses N reads into 1.
    tuning_host_snapshots_cache_ttl_seconds: Optional[str] = None
    # #506 — concurrency cap on the SPA's per-host /api/hosts/one/<id>
    # fan-out in `loadHosts()`. Read on /api/me into
    # `me.client_config.hosts_parallel_fetch`.
    tuning_hosts_parallel_fetch: Optional[str] = None
    # #537 / #538 — SSE heartbeat cadence + connection lifetime cap.
    tuning_sse_heartbeat_seconds: Optional[str] = None
    tuning_sse_max_lifetime_seconds: Optional[str] = None
    # #539 — Webmin probe outer budget (shared by /api/hosts and
    # /api/hosts/one).
    tuning_webmin_probe_budget_seconds: Optional[str] = None
    # #540 — node-exporter per-host probe timeout (shared by /api/hosts,
    # /api/hosts/one, the debug endpoint, and host_metrics_sampler).
    tuning_node_exporter_probe_timeout_seconds: Optional[str] = None
    # #541 / #542 — frontend SSE knobs delivered via /api/me's
    # client_config (× 1000 ms conversion in main.py).
    tuning_sse_idle_threshold_seconds: Optional[str] = None
    tuning_pollops_sse_keepalive_seconds: Optional[str] = None
    # #543 — login rate-limit policy (3 knobs).
    tuning_rate_limit_max_failures: Optional[str] = None
    tuning_rate_limit_window_seconds: Optional[str] = None
    tuning_rate_limit_lockout_seconds: Optional[str] = None
    # #547 / #546 — outer host-provider cache + per-host Webmin caches.
    tuning_host_provider_cache_ttl_seconds: Optional[str] = None
    tuning_webmin_host_cache_ttl_seconds: Optional[str] = None
    tuning_webmin_host_fail_cache_ttl_seconds: Optional[str] = None
    # #548 — host_metrics_sampler per-tick NE probe concurrency.
    tuning_host_metrics_probe_concurrency: Optional[str] = None
    # #549 — shared (Webmin + SSH) per-(host, user) auth-failure cool-down.
    tuning_auth_failure_cooldown_seconds: Optional[str] = None
    # #343 — Ping host-stats provider knobs.
    tuning_ping_interval_seconds: Optional[str] = None
    tuning_ping_concurrency: Optional[str] = None
    tuning_ping_probe_timeout_seconds: Optional[str] = None
    tuning_ping_cooldown_seconds: Optional[str] = None
    # -----------------------------------------------------------------
    # Per-event notification toggles. Each maps to one of the
    # 12 (event group × success/failure) notify() call sites in
    # logic/ops.py; gated inside notify() via the event= kwarg. Default
    # behaviour is "send" so existing deploys keep all notifications on.
    # Stored as "true"/"false" strings; "" clears (read-side falls back
    # to the default-true). The /api/notify-test endpoint always sends
    # regardless of these toggles.
    # -----------------------------------------------------------------
    notify_event_stack_update_success: Optional[str] = None
    notify_event_stack_update_failure: Optional[str] = None
    notify_event_container_update_success: Optional[str] = None
    notify_event_container_update_failure: Optional[str] = None
    notify_event_container_restart_success: Optional[str] = None
    notify_event_container_restart_failure: Optional[str] = None
    notify_event_container_remove_success: Optional[str] = None
    notify_event_container_remove_failure: Optional[str] = None
    notify_event_service_restart_success: Optional[str] = None
    notify_event_service_restart_failure: Optional[str] = None
    notify_event_prune_success: Optional[str] = None
    notify_event_prune_failure: Optional[str] = None
    # Security event — defaults to OFF (login traffic is noisy).
    notify_event_user_login: Optional[str] = None
    # System event (#411) — fires when host_metrics_sampler auto-pauses
    # a host after the configured failure window. Default ON.
    notify_event_host_paused: Optional[str] = None
    # -----------------------------------------------------------------
    # TOTP / 2FA policies (#345). Master toggle plus role-scoped
    # required-flags plus lockout knobs. Authentik users are excluded
    # from every TOTP path -- their IdP handles MFA.
    # -----------------------------------------------------------------
    totp_allowed: Optional[bool] = None
    totp_required_for_admins: Optional[bool] = None
    totp_required_for_users: Optional[bool] = None
    totp_lockout_max_failures: Optional[int] = None
    totp_lockout_minutes: Optional[int] = None
    # Passkey master toggle (#432). Mirrors totp_allowed.
    passkeys_allowed: Optional[bool] = None


@app.get("/api/settings")
async def api_get_settings(request: Request):
    from logic import portainer as _portainer
    with db_conn() as c:
        a = auth.get_auth_settings(c)
    p = _portainer.get_portainer_settings()
    return {
        # Per-service master switches (#204). Default true so existing
        # deploys don't change behaviour — flip false to short-circuit
        # the service in code AND grey out the inputs in the UI.
        "apprise_enabled":    (get_setting("apprise_enabled",    "true") or "true").lower() == "true",
        "open_meteo_enabled": (get_setting("open_meteo_enabled", "true") or "true").lower() == "true",
        "portainer_enabled":  (get_setting("portainer_enabled",  "true") or "true").lower() == "true",
        "ssh_enabled":        (get_setting("ssh_enabled",        "true") or "true").lower() == "true",
        "apprise_url": get_setting("apprise_url", ""),
        "apprise_tag": get_setting("apprise_tag", ""),
        # Per-event notification toggles. Resolved through
        # get_setting_bool so the frontend gets clean booleans (no
        # client-side string parsing). Default true preserves the
        # legacy "send everything" behaviour for existing deploys.
        "notify_event_stack_update_success":      get_setting_bool("notify_event_stack_update_success", True),
        "notify_event_stack_update_failure":      get_setting_bool("notify_event_stack_update_failure", True),
        "notify_event_container_update_success":  get_setting_bool("notify_event_container_update_success", True),
        "notify_event_container_update_failure":  get_setting_bool("notify_event_container_update_failure", True),
        "notify_event_container_restart_success": get_setting_bool("notify_event_container_restart_success", True),
        "notify_event_container_restart_failure": get_setting_bool("notify_event_container_restart_failure", True),
        "notify_event_container_remove_success":  get_setting_bool("notify_event_container_remove_success", True),
        "notify_event_container_remove_failure":  get_setting_bool("notify_event_container_remove_failure", True),
        "notify_event_service_restart_success":   get_setting_bool("notify_event_service_restart_success", True),
        "notify_event_service_restart_failure":   get_setting_bool("notify_event_service_restart_failure", True),
        "notify_event_prune_success":             get_setting_bool("notify_event_prune_success", True),
        "notify_event_prune_failure":             get_setting_bool("notify_event_prune_failure", True),
        # Security event — default OFF (login spam is noisy; opt-in).
        "notify_event_user_login":                get_setting_bool("notify_event_user_login", False),
        # System event (#411) — fires when host_metrics_sampler auto-
        # pauses a host after the failure window. Default ON.
        "notify_event_host_paused":               get_setting_bool("notify_event_host_paused", True),
        # TOTP / 2FA policy (#345). Five fields driving the multi-step
        # login flow + Profile enrolment guards + Admin -> Users action
        # enablement. Defaults preserve "no 2FA required" semantics so
        # an upgrade is a no-op until the operator opts in.
        "totp_allowed":              get_setting_bool(
            "totp_allowed", _TOTP_POLICY_DEFAULTS["totp_allowed"],
        ),
        "totp_required_for_admins":  get_setting_bool(
            "totp_required_for_admins",
            _TOTP_POLICY_DEFAULTS["totp_required_for_admins"],
        ),
        "totp_required_for_users":   get_setting_bool(
            "totp_required_for_users",
            _TOTP_POLICY_DEFAULTS["totp_required_for_users"],
        ),
        "totp_lockout_max_failures": int(get_setting(
            "totp_lockout_max_failures",
            str(_TOTP_POLICY_DEFAULTS["totp_lockout_max_failures"]),
        ) or _TOTP_POLICY_DEFAULTS["totp_lockout_max_failures"]),
        "totp_lockout_minutes":      int(get_setting(
            "totp_lockout_minutes",
            str(_TOTP_POLICY_DEFAULTS["totp_lockout_minutes"]),
        ) or _TOTP_POLICY_DEFAULTS["totp_lockout_minutes"]),
        "passkeys_allowed":          get_setting_bool(
            "passkeys_allowed", _TOTP_POLICY_DEFAULTS["passkeys_allowed"],
        ),
        # Open-Meteo upstream (Admin → General). Returned in the
        # clear so the input round-trips and reloads persisted. Blank
        # disables the topbar weather widget (see _open_meteo_url).
        "open_meteo_url": get_setting("open_meteo_url", "") or "",
        # Host groups — returned as a parsed list of dicts. Per-group
        # SSH password is masked at the boundary: we replace it with
        # a `password_set: bool` flag so the browser learns whether a
        # password is configured but never receives the value. Same
        # contract as every other secret in the settings table.
        "host_groups": (lambda raw: (
            (lambda groups: [
                {**g, "ssh": (lambda s: (
                    {k: v for k, v in s.items() if k != "password"}
                    | ({"password_set": True} if (s.get("password") or "") else {"password_set": False})
                ))(g.get("ssh") if isinstance(g.get("ssh"), dict) else {})}
                for g in groups if isinstance(g, dict)
            ])(json.loads(raw) if (raw or "").strip() else [])
        ))(get_setting("host_groups", "") or ""),
        # Asset inventory (<asset-api-host>). Secret is write-only — UI sees
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
            "edit_url_template":  get_setting("asset_inventory_edit_url_template", "") or "",
            # #417 / ENH-001 — TLS verification toggle. Default True.
            "verify_tls":         (get_setting("asset_inventory_verify_tls", "true") or "true").strip().lower() != "false",
        },
        "portainer_public_url": get_setting("portainer_public_url", str(p.get("portainer_url") or "")),
        "backup_retention_count": int(get_setting("backup_retention_count", "0") or "0"),
        "scheduler_timezone": get_setting("scheduler_timezone", "") or "",
        # Host-drawer admin debug panel visibility (Admin → Hosts toggle).
        # Default true — preserves the legacy behaviour for existing
        # deploys that haven't touched the setting. Operators who don't
        # use the raw-JSON dump can flip to false to declutter the
        # drawer without losing other admin-only affordances.
        "debug_panel_enabled": (
            (get_setting("debug_panel_enabled", "true") or "true").lower() == "true"
        ),
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
        # Both fields derive from the single helper (CONS-004). The
        # legacy string form is kept for back-compat with older SPA
        # bundles that read ``host_stats_source`` instead of the list.
        "host_stats_source": (",".join(sorted(active_host_stats_providers()))
                              or "none"),
        "host_stats_sources": sorted(active_host_stats_providers()),
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
        # Ping (#343) — no secrets, so fields round-trip in the clear.
        # ``has_icmp_support`` reflects whether ``icmplib`` is importable
        # (the container's Python may not have it); the SPA uses this
        # to disable the ICMP toggle with a hint when the package is
        # missing.
        "ping": {
            "enabled":          get_setting_bool("ping_enabled", False),
            "default_port":     int(get_setting("ping_default_port", "443") or "443"),
            "use_icmp":         get_setting_bool("ping_use_icmp", False),
            "has_icmp_support": (lambda: __import__("logic.ping", fromlist=["has_icmp_support"]).has_icmp_support())(),
        },
        # SNMP (#344). v3 secret keys follow the write-only ``_set``
        # flag contract; community, version, port, aliases round-trip
        # in the clear (community is technically a credential but it's
        # not a SECRET in the same sense — many operators want to see
        # the configured value to confirm). ``has_snmp_support`` mirrors
        # the Ping pattern so the SPA's master toggle disables with a
        # "package missing" hint when pysnmp isn't installed.
        "snmp": {
            "default_community":   get_setting("snmp_default_community", "public") or "public",
            "default_version":     (get_setting("snmp_default_version", "v2c") or "v2c").strip().lower(),
            "default_port":        int(get_setting("snmp_default_port", "161") or "161"),
            "v3_user":             get_setting("snmp_v3_user", "") or "",
            "v3_auth_key_set":     bool(get_setting("snmp_v3_auth_key", "")),
            "v3_priv_key_set":     bool(get_setting("snmp_v3_priv_key", "")),
            "aliases":             json.loads(get_setting("snmp_aliases", "{}") or "{}"),
            "has_snmp_support":    (lambda: __import__("logic.snmp", fromlist=["has_snmp_support"]).has_snmp_support())(),
            # #644 — surface the actual ImportError text from logic.snmp's
            # module-level import block so the SPA's hint can show the
            # ROOT CAUSE instead of just "package not installed". Empty
            # string when pysnmp imported cleanly. Operators don't have
            # to grep the server log to figure out which symbol/path
            # is missing — the hint banner shows it inline.
            "import_error":        (lambda: getattr(
                __import__("logic.snmp", fromlist=["_SNMP_IMPORT_ERROR"]),
                "_SNMP_IMPORT_ERROR", "",
            ))(),
        },
        # Per-provider chip colour overrides (#596). Empty string means
        # "use the SPA's built-in default" — the SPA's `providerColor()`
        # helper falls back to the same default constant. Round-tripped
        # in the clear (not a secret).
        "provider_color_beszel":        get_setting("provider_color_beszel", "")        or "",
        "provider_color_pulse":         get_setting("provider_color_pulse", "")         or "",
        "provider_color_node_exporter": get_setting("provider_color_node_exporter", "") or "",
        "provider_color_webmin":        get_setting("provider_color_webmin", "")        or "",
        "provider_color_ping":          get_setting("provider_color_ping", "")          or "",
        "provider_color_snmp":          get_setting("provider_color_snmp", "")          or "",
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
            "group_case_sensitive": bool(a.get("oidc_group_case_sensitive", True)),
        },
    }


@app.post("/api/settings")
async def api_set_settings(
    s: SettingsIn,
    _admin: auth.User = Depends(auth.require_admin),
):
    from logic import portainer as _portainer
    # Per-service master switches (#204). Persisted as "true" / "false"
    # strings to match every other boolean toggle in the settings table.
    if s.apprise_enabled is not None:
        set_setting("apprise_enabled", "true" if s.apprise_enabled else "false")
    if s.open_meteo_enabled is not None:
        set_setting("open_meteo_enabled", "true" if s.open_meteo_enabled else "false")
    if s.portainer_enabled is not None:
        set_setting("portainer_enabled", "true" if s.portainer_enabled else "false")
    if s.ssh_enabled is not None:
        set_setting("ssh_enabled", "true" if s.ssh_enabled else "false")
    if s.apprise_url is not None: set_setting("apprise_url", s.apprise_url)
    if s.apprise_tag is not None: set_setting("apprise_tag", s.apprise_tag)
    # Per-event notification toggles. Each value MUST be
    # "true" / "false" / "" (empty clears → read-side falls back to
    # the default-true via get_setting_bool). Anything else is a
    # 400 so a typo can't silently disable a category. The notify()
    # gate in logic/ops.py honours these per-event keys.
    # Derived from the module-level _NOTIFY_EVENT_NAMES tuple (#357 —
    # single source of truth for both admin gates and per-user opt-in).
    _NOTIFY_EVENT_KEYS = tuple(f"notify_event_{n}" for n in _NOTIFY_EVENT_NAMES)
    for _ek in _NOTIFY_EVENT_KEYS:
        _v = getattr(s, _ek, None)
        if _v is None:
            continue
        _norm = (_v or "").strip().lower()
        if _norm not in ("", "true", "false"):
            raise HTTPException(
                status_code=400,
                detail=f"{_ek} must be 'true', 'false', or '' (clear).",
            )
        set_setting(_ek, _norm)
    # TOTP / 2FA policy (#345). Booleans persisted as "true" / "false";
    # ints bounds-checked then stored as decimal strings (matches the
    # tuning_* shape from #337). Same dirty + Save UI pattern as the
    # other admin-tab toggles.
    if s.totp_allowed is not None:
        set_setting("totp_allowed", "true" if s.totp_allowed else "false")
    if s.totp_required_for_admins is not None:
        set_setting(
            "totp_required_for_admins",
            "true" if s.totp_required_for_admins else "false",
        )
    if s.totp_required_for_users is not None:
        set_setting(
            "totp_required_for_users",
            "true" if s.totp_required_for_users else "false",
        )
    if s.totp_lockout_max_failures is not None:
        n = int(s.totp_lockout_max_failures)
        if n < 3 or n > 20:
            raise HTTPException(
                status_code=400,
                detail="totp_lockout_max_failures must be in the range 3..20.",
            )
        set_setting("totp_lockout_max_failures", str(n))
    if s.totp_lockout_minutes is not None:
        n = int(s.totp_lockout_minutes)
        if n < 1 or n > 1440:
            raise HTTPException(
                status_code=400,
                detail="totp_lockout_minutes must be in the range 1..1440.",
            )
        set_setting("totp_lockout_minutes", str(n))
    if s.passkeys_allowed is not None:
        set_setting("passkeys_allowed", "true" if s.passkeys_allowed else "false")
    # Invalidate the policy cache (#470 / ENH-003) so a Save in
    # Admin -> Config takes effect on the next call instead of waiting
    # out the TTL window. Cheap — just resets the dict.
    if (s.totp_allowed is not None or s.totp_required_for_admins is not None
            or s.totp_required_for_users is not None
            or s.totp_lockout_max_failures is not None
            or s.totp_lockout_minutes is not None
            or s.passkeys_allowed is not None):
        _invalidate_totp_policy_cache()
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
        valid = {"beszel", "node_exporter", "pulse", "webmin", "ping", "snmp"}
        unknown = parts - valid
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    "host_stats_source must be a CSV of 'beszel' / "
                    "'node_exporter' / 'pulse' / 'webmin' / 'ping' / "
                    "'snmp' "
                    f"(or 'none'). Unknown: {sorted(unknown)}"
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
    # Ping (#343). No secrets — every field round-trips in the clear.
    # Validation: `ping_default_port` clamped to 1..65535. `ping_enabled`
    # is the master toggle but this acts as documentation only — the
    # provider also has to be in `host_stats_source` to actually probe
    # (handled by `active_host_stats_providers()` upstream).
    if s.ping_enabled is not None:
        set_setting("ping_enabled", "true" if s.ping_enabled else "false")
    if s.ping_default_port is not None:
        try:
            p = int(s.ping_default_port)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="ping_default_port must be an integer",
            )
        if not (1 <= p <= 65535):
            raise HTTPException(
                status_code=400,
                detail="ping_default_port must be 1-65535",
            )
        set_setting("ping_default_port", str(p))
    if s.ping_use_icmp is not None:
        set_setting("ping_use_icmp", "true" if s.ping_use_icmp else "false")
    # SNMP (#344). Mirror the webmin / beszel / pulse persistence
    # contract: community / version / port / aliases round-trip in the
    # clear; v3 user is also clear text; the two v3 keys are write-only
    # (keep current if blank). Validation: port clamped to 1..65535;
    # version restricted to {"v2c", "v3"}; community trimmed.
    if s.snmp_default_community is not None:
        set_setting("snmp_default_community", (s.snmp_default_community or "").strip())
    if s.snmp_default_version is not None:
        v = (s.snmp_default_version or "").strip().lower()
        if v and v not in ("v2c", "v3"):
            raise HTTPException(
                status_code=400,
                detail="snmp_default_version must be 'v2c' or 'v3' (or blank)",
            )
        set_setting("snmp_default_version", v or "v2c")
    if s.snmp_default_port is not None:
        try:
            p = int(s.snmp_default_port)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="snmp_default_port must be an integer",
            )
        if not (1 <= p <= 65535):
            raise HTTPException(
                status_code=400,
                detail="snmp_default_port must be 1-65535",
            )
        set_setting("snmp_default_port", str(p))
    if s.snmp_v3_user is not None:
        set_setting("snmp_v3_user", (s.snmp_v3_user or "").strip())
    if s.snmp_v3_auth_key is not None and s.snmp_v3_auth_key.strip() != "":
        set_setting("snmp_v3_auth_key", s.snmp_v3_auth_key)
    if s.snmp_v3_priv_key is not None and s.snmp_v3_priv_key.strip() != "":
        set_setting("snmp_v3_priv_key", s.snmp_v3_priv_key)
    if s.snmp_aliases is not None:
        clean = {
            str(k).strip(): str(v).strip()
            for k, v in (s.snmp_aliases or {}).items()
            if str(k).strip() and str(v).strip()
        }
        set_setting("snmp_aliases", json.dumps(clean))
    # Per-provider chip colours (#596). Hex string `#RRGGBB` (7 chars,
    # case-insensitive) OR empty/blank to clear the override and fall
    # back to the SPA's built-in default. Any other shape rejected at
    # save time rather than letting an invalid value reach inline
    # CSS where it'd silently break the chip render.
    import re as _re
    _hex_re = _re.compile(r"^#[0-9a-fA-F]{6}$")
    for _field in (
        "provider_color_beszel", "provider_color_pulse",
        "provider_color_node_exporter", "provider_color_webmin",
        "provider_color_ping", "provider_color_snmp",
    ):
        _val = getattr(s, _field, None)
        if _val is None:
            continue
        _trim = _val.strip()
        if _trim == "":
            set_setting(_field, "")
            continue
        if not _hex_re.match(_trim):
            raise HTTPException(
                status_code=400,
                detail=f"{_field} must be a 7-char hex colour (e.g. #22c55e) or blank",
            )
        set_setting(_field, _trim.lower())
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
    # Admin → Hosts: show / hide the per-host drawer debug panel.
    # Persisted as the string "true" / "false" (matches every other
    # boolean toggle in this table — see node_exporter_enabled etc.).
    if s.debug_panel_enabled is not None:
        set_setting("debug_panel_enabled", "true" if s.debug_panel_enabled else "false")
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

    # --- Host groups (#93 + #134) -----------------------------------------
    # Each entry: {name, range_start, range_end, order?, parent_name?,
    # ip_range?}. `parent_name` (optional, string) references another
    # group's name to nest under; nesting is fixed at 2 levels so a
    # parent cannot itself have a parent_name. `ip_range` is free-text
    # metadata captured alongside — no filter impact yet, surfaced to
    # the UI for display only. Name capped at 60 chars.
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
            parent_name = (g.get("parent_name") or "").strip()[:60] or None
            ip_range = (g.get("ip_range") or "").strip()[:120]
            # Optional `number` — operator-supplied display prefix
            # (e.g. "32 Smart & IOT Routers"). Stored separately from
            # the range so the operator can pick a label number that
            # doesn't have to match a host's custom_number. Blank /
            # missing → None; uniqueness is enforced below alongside
            # parent / containment / overlap checks.
            number_raw = g.get("number")
            number_val: int | None
            if number_raw in (None, "", 0, "0"):
                number_val = None
            else:
                try:
                    number_val = int(number_raw)
                    if number_val <= 0:
                        number_val = None
                except (TypeError, ValueError):
                    number_val = None
            # Optional per-group SSH credentials. Same shape as
            # `hosts_config[].ssh` so the resolver in `logic/ssh.py`
            # can iterate them uniformly. Keep-current-if-blank for
            # the password — same convention as the global secret
            # store.
            clean_ssh: dict = {}
            ssh_in = g.get("ssh") if isinstance(g.get("ssh"), dict) else {}
            user = str((ssh_in or {}).get("user") or "").strip()
            if user:
                clean_ssh["user"] = user
            port = (ssh_in or {}).get("port")
            if port not in (None, "", 0):
                try:
                    pi = int(port)
                    if 1 <= pi <= 65535:
                        clean_ssh["port"] = pi
                except (TypeError, ValueError):
                    pass
            # Stable group id — UUID minted on first save, persists
            # across renames. Used as the key for password keep-current
            # lookup so a rename + new-group-with-old-name pair can't
            # leak the old password into a freshly-created row. New
            # groups arrive without an id and get one minted here;
            # existing groups round-trip whatever the API previously
            # emitted.
            row_id = str(g.get("id") or "").strip()
            if not row_id:
                import uuid
                row_id = uuid.uuid4().hex

            # Password resolution:
            #   1. New non-empty password → store it (clear flag is
            #      ignored — operator typed a new value, that wins).
            #   2. Empty + clear_password=true → erase (don't carry
            #      forward).
            #   3. Empty + no clear flag → carry forward the prior
            #      persisted value (keep-current-if-blank — same
            #      contract as every other secret in the settings
            #      table). Lookup is by stable `id`, not by `name`,
            #      so renames preserve the password but a new group
            #      that happens to reuse an old name does NOT inherit.
            new_pw = str((ssh_in or {}).get("password") or "").strip()
            clear = bool((ssh_in or {}).get("clear_password"))
            if new_pw:
                clean_ssh["password"] = new_pw
            elif not clear:
                try:
                    prior_raw = get_setting("host_groups", "") or ""
                    prior_groups = json.loads(prior_raw) if prior_raw.strip() else []
                except (TypeError, ValueError):
                    prior_groups = []
                if isinstance(prior_groups, list):
                    for pg in prior_groups:
                        if not isinstance(pg, dict):
                            continue
                        prior_id = str(pg.get("id") or "").strip()
                        # First-pass match: by stable id.
                        if prior_id and prior_id == row_id:
                            prior_pw = (pg.get("ssh") or {}).get("password") or ""
                            if prior_pw:
                                clean_ssh["password"] = prior_pw
                            break
                    else:
                        # No id-match. Only fall back to name-match
                        # for legacy rows that lack an id at all
                        # (first save after the upgrade). Any prior
                        # row that already has an id is treated as
                        # a different group, even if names collide.
                        for pg in prior_groups:
                            if not isinstance(pg, dict):
                                continue
                            prior_id = str(pg.get("id") or "").strip()
                            if prior_id:
                                continue  # has an id; can't be us
                            if pg.get("name") == name:
                                prior_pw = (pg.get("ssh") or {}).get("password") or ""
                                if prior_pw:
                                    clean_ssh["password"] = prior_pw
                                break
            clean_groups.append({
                "id":          row_id,
                "name":        name,
                "range_start": rs,
                "range_end":   re_,
                "order":       order,
                "parent_name": parent_name,
                "ip_range":    ip_range,
                "number":      number_val,
                "ssh":         clean_ssh,
            })

        # Parent validation — 2-level nesting means the referenced
        # parent must (a) exist in the same payload, (b) be named
        # differently from the child (no self-parent), (c) be a
        # TOP-LEVEL group (no parent_name of its own) — this is how
        # we keep the depth at 2 without adding a cycle detector.
        by_name = {g["name"]: g for g in clean_groups}
        for g in clean_groups:
            pn = g["parent_name"]
            if not pn:
                continue
            if pn == g["name"]:
                raise HTTPException(
                    400,
                    f"host_groups: '{g['name']}' cannot be its own parent.",
                )
            parent = by_name.get(pn)
            if parent is None:
                raise HTTPException(
                    400,
                    f"host_groups: '{g['name']}' references unknown parent '{pn}'.",
                )
            if parent.get("parent_name"):
                raise HTTPException(
                    400,
                    f"host_groups: '{g['name']}' parent '{pn}' is itself a "
                    f"sub-group. Nesting is limited to two levels.",
                )

        # Containment: every sub-group's range must fit inside its
        # parent's range. A sub-group 5-10 under a parent 1-4 would
        # never match any host and is always a config mistake.
        for g in clean_groups:
            pn = g["parent_name"]
            if not pn:
                continue
            parent = by_name[pn]  # existence already validated above
            if not (parent["range_start"] <= g["range_start"]
                    and g["range_end"] <= parent["range_end"]):
                raise HTTPException(
                    400,
                    f"host_groups: sub-group '{g['name']}' "
                    f"({g['range_start']}–{g['range_end']}) must be contained "
                    f"in parent '{pn}' range "
                    f"({parent['range_start']}–{parent['range_end']}).",
                )

        # Overlap: every pair of groups that is NOT parent-child must
        # be disjoint. Covers three cases in one rule:
        #   - Two top-level groups overlapping (bad).
        #   - A sub-group overlapping a top-level group that is NOT
        #     its parent (bad — would double-assign hosts).
        #   - Two sub-groups overlapping (bad — whether they share a
        #     parent or not; cross-parent overlap is structurally
        #     impossible when parents are disjoint, but we check
        #     anyway as a belt-and-braces).
        # Parent-child pairs are expected to overlap (sub is contained
        # in parent by construction) and are skipped.
        def _is_parent_child(a: dict, b: dict) -> bool:
            return (a["parent_name"] == b["name"]
                    or b["parent_name"] == a["name"])
        n = len(clean_groups)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = clean_groups[i], clean_groups[j]
                if _is_parent_child(a, b):
                    continue
                # Standard interval-overlap test.
                if a["range_start"] <= b["range_end"] \
                        and b["range_start"] <= a["range_end"]:
                    raise HTTPException(
                        400,
                        f"host_groups: '{a['name']}' "
                        f"({a['range_start']}–{a['range_end']}) overlaps "
                        f"'{b['name']}' ({b['range_start']}–{b['range_end']}). "
                        f"Ranges must be disjoint except for parent↔sub-group pairs.",
                    )

        # Number uniqueness — when set, no two groups may share the
        # same display number. Operators using the prefix to mirror an
        # asset-tag scheme would silently get duplicates without this.
        seen_numbers: dict[int, str] = {}
        for g in clean_groups:
            num = g.get("number")
            if num is None:
                continue
            prior = seen_numbers.get(num)
            if prior is not None:
                raise HTTPException(
                    400,
                    f"host_groups: number {num} is used by both "
                    f"'{prior}' and '{g['name']}'. Group numbers must be unique.",
                )
            seen_numbers[num] = g["name"]

        # Duplicate-id check — analogous to BUG-009's fix on hosts_config.
        # The password-merge logic at the top of this loop matches by
        # stable `id`; two incoming rows sharing the same id is an
        # ambiguous-state condition (operator hand-crafted JSON, UI race,
        # or a restored backup that overlapped a fresh row). Reject early
        # with both names so the operator can spot the offender.
        gid_seen: dict[str, str] = {}
        for g in clean_groups:
            gid = g.get("id")
            if not gid:
                continue
            prior_name = gid_seen.get(gid)
            if prior_name is not None:
                raise HTTPException(
                    400,
                    f"host_groups: id {gid} is used by both "
                    f"'{prior_name}' and '{g['name']}'. Each group must have a unique id.",
                )
            gid_seen[gid] = g["name"]

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
    if s.asset_inventory_verify_tls is not None:
        set_setting("asset_inventory_verify_tls",
                    "true" if s.asset_inventory_verify_tls else "false")
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
    if s.asset_inventory_edit_url_template is not None:
        set_setting("asset_inventory_edit_url_template",
                    (s.asset_inventory_edit_url_template or "").strip())

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
            # Without flipping auth_changed here, the cached
            # admin-group claim survives until restart even though
            # the DB has the new value. `auto_provision_authentik`
            # would keep matching incoming OIDC logins against the
            # OLD group and route the wrong users to admin/readonly.
            # See BUG-001 in notes/code_review_2026-04-25.md.
            auth_changed = True
        if s.oidc_verify_tls is not None:
            auth.set_auth_setting(c, "oidc_verify_tls",
                                  "true" if s.oidc_verify_tls else "false")
            auth_changed = True
        if s.oidc_group_case_sensitive is not None:
            auth.set_auth_setting(c, "oidc_group_case_sensitive",
                                  "true" if s.oidc_group_case_sensitive else "false")
            auth_changed = True
        # Client secret: keep-current-if-blank.
        if s.oidc_client_secret is not None and s.oidc_client_secret.strip() != "":
            auth.set_auth_setting(c, "oidc_client_secret", s.oidc_client_secret)
            auth_changed = True

    # Tuning knobs (#337). Each field is keep-if-None / clear-if-blank /
    # bounds-check-and-store-if-provided. Bounds come from
    # logic.tuning.TUNABLES so the resolver, the editor, and the
    # validator share one source of truth. Stored as plain strings —
    # the resolver int-casts on read.
    for _k, (_env, _default, _lo, _hi) in tuning.TUNABLES.items():
        _val = getattr(s, _k, None)
        if _val is None:
            continue
        _raw = _val.strip()
        if _raw == "":
            set_setting(_k, "")
            continue
        try:
            _n = int(_raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail=f"{_k} must be an integer (got {_val!r})",
            )
        if _n < _lo or _n > _hi:
            raise HTTPException(
                status_code=400,
                detail=f"{_k} must be between {_lo} and {_hi} (got {_n})",
            )
        set_setting(_k, str(_n))

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
    # Provider settings touched? Drop the host-provider state cache so
    # the next /api/hosts/one/{id} re-reads with the new credentials /
    # source / aliases instead of serving up to 10s of stale auth_failed
    # rows. Mirrors the auth / portainer invalidation pattern above.
    _host_provider_fields = {
        "host_stats_source",
        "beszel_hub_url", "beszel_identity", "beszel_password",
        "beszel_verify_tls", "beszel_aliases",
        "pulse_url", "pulse_token", "pulse_verify_tls", "pulse_aliases",
        "webmin_url", "webmin_user", "webmin_password",
        "webmin_verify_tls", "webmin_aliases",
        "node_exporter_enabled", "node_exporter_url_template",
        "node_exporter_overrides",
        # #343 — Ping. No credential to bust the cred-blob hash with;
        # cache TTL alone catches `ping_enabled` flips after the
        # 10s window, but we still bust on save for instant feedback.
        "ping_enabled", "ping_default_port", "ping_use_icmp",
        # #344 — SNMP. Defaults + aliases + v3 keys all live under the
        # same per-provider state; any change here invalidates the
        # cred-blob hash so subsequent /api/hosts/one/<id> calls re-
        # probe with the new credentials.
        "snmp_default_community", "snmp_default_version", "snmp_default_port",
        "snmp_v3_user", "snmp_v3_auth_key", "snmp_v3_priv_key",
        "snmp_aliases",
    }
    if _host_provider_fields & set(s.model_dump(exclude_unset=True).keys()):
        invalidate_host_provider_cache()
    return {"status": "ok"}


# ----------------------------------------------------------------------------
# Process-level tunables (#337). Admin-only read endpoint that surfaces
# the DB / env / default tier per knob plus the resolved effective value.
# Writes go through the existing POST /api/settings (additive pattern —
# no new POST per provider). The UI reads this once on tab open to
# render placeholders for the env-fallback / default behind each input.
# ----------------------------------------------------------------------------
@app.get("/api/admin/tuning")
async def api_admin_tuning(_admin: auth.User = Depends(auth.require_admin)):
    return tuning.effective_state()


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

    Honours an in-flight ``verify_tls`` from the form when supplied so
    an admin can flip the checkbox OFF and Test a self-signed issuer
    before saving (BUG-005). Missing key falls back to the saved DB
    value via ``oidc._verify_tls()``.
    """
    body = await request.json()
    issuer = (body.get("issuer_url") or "").strip()
    verify_tls = body.get("verify_tls")
    if verify_tls is not None:
        verify_tls = bool(verify_tls)
    return await oidc.test_discovery(issuer, verify_tls=verify_tls)


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
    # Endpoint id (#360 / DEAD-002): probe `/api/endpoints/{id}` after
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
                # (#369 / UX-003 follow-up) so the operator sees
                # "Portainer rejected the credentials (HTTP 401 — ...)"
                # instead of a bare body dump.
                raw = f"HTTP {r.status_code}: {r.text[:200]}"
                return {"ok": False, "status": r.status_code,
                        "detail": _humanise_probe_error(raw, "Portainer")}
            version = ""
            try:
                data = r.json()
                version = data.get("Version") or data.get("version") or ""
            except Exception:
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
            except Exception:
                name = f"#{endpoint_id}"
            return {"ok": True, "status": 200,
                    "detail": f"{prefix}, endpoint {name} reachable",
                    "endpoint_id": endpoint_id}
        if ep.status_code == 404:
            # Specific Portainer-shaped message — keep the bespoke copy
            # rather than humanising. Operators recognise this exact
            # phrasing from the related #360 / DEAD-002 fix.
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
    _admin: auth.User = Depends(auth.require_admin),
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
    return _format_provider_test_summary(
        result,
        target_label="Pulse",
        item_singular="node",
        item_plural="node(s)",
        count_key="node_count",
        items_key="nodes",
    )


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
        # #369 / UX-003 follow-up: route Webmin's verbatim probe error
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
    return _format_provider_test_summary(
        adapted,
        target_label="hub",
        item_singular="system",
        item_plural="system(s)",
        count_key="system_count",
        items_key="systems",
    )


@app.post("/api/snmp/test")
async def api_snmp_test(
    request: Request,
    _admin: auth.User = Depends(auth.require_admin),
):
    """Admin-only: probe one SNMP host (#344).

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
    try:
        port = int(_resolve_field(body, "port", "snmp_default_port", "161") or "161")
    except (TypeError, ValueError):
        port = 161
    v3_user = _resolve_field(body, "v3_user", "snmp_v3_user", "")
    v3_auth = _resolve_field(body, "v3_auth_key", "snmp_v3_auth_key", "")
    v3_priv = _resolve_field(body, "v3_priv_key", "snmp_v3_priv_key", "")

    result = await _snmp.probe_snmp(
        host,
        community=community,
        version=version,
        port=port,
        v3_user=v3_user,
        v3_auth_key=v3_auth,
        v3_priv_key=v3_priv,
        timeout=10.0,
    )
    if result.get("error") and not result.get("hosts"):
        return {"ok": False,
                "detail": _humanise_probe_error(result["error"], "SNMP")}
    hosts = result.get("hosts") or {}
    if not hosts:
        return {"ok": False,
                "detail": "no parseable response — check community / version / port"}
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
        detail_bits.append(f"mem={mem // (1024**3)} GB")
    if disk:
        detail_bits.append(f"disk={disk // (1024**3)} GB")
    if nics:
        detail_bits.append(f"nics={nics}")
    return {"ok": True, "detail": " · ".join(detail_bits),
            "host_key": host_key}


# ----------------------------------------------------------------------------
# Asset inventory (ticket #78) — <asset-api-host> OAuth2 client_credentials. Manual
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
    # #445 — honour the `asset_inventory_verify_tls` toggle here too.
    # Body wins (so admins can flip the form's checkbox OFF and Test
    # a self-signed asset API before saving); otherwise the persisted
    # setting (default True) applies. Mirrors the OIDC-test shape.
    body_verify_tls = body.get("verify_tls")
    if body_verify_tls is None:
        verify_tls = _asset_inventory_verify_tls()
    else:
        verify_tls = bool(body_verify_tls)
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
            verify_tls=verify_tls,
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
        token_url, client_id, client_secret, scope=scope, verify_tls=verify_tls,
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
            verify_tls=_asset_inventory_verify_tls(),
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
        verify_tls=_asset_inventory_verify_tls(),
    )


def _asset_inventory_verify_tls() -> bool:
    """Read the operator-controlled `asset_inventory_verify_tls` setting
    on every refresh (#417 / ENH-001). Default True so first-boot deploys
    keep validating TLS — homelab operators with self-signed asset APIs
    flip the toggle in Admin → Asset Inventory."""
    raw = (get_setting("asset_inventory_verify_tls", "true") or "true").strip().lower()
    return raw != "false"


# Local aliases for the canonical merge helpers in `logic/merge.py`.
# Was a duplicated private implementation here AND in logic/gather.py;
# centralised in #271 (CONS-003) so the merge semantics stay byte-
# identical across the two call sites without a "don't import private
# helpers across modules" caveat.
from logic.merge import is_meaningful as _meaningful, merge_best as _merge_best


def _resolve_field(body: dict, body_key: str,
                   setting_key: str, default: str = "") -> str:
    """Pick a field value from ``body`` first, falling back to the
    persisted ``settings`` table when ``body[body_key]`` is blank.

    Standard contract for the test-connection endpoints (CONS-007 in
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
    one-liners (#369 / UX-003).

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
        # Try to pull the seconds-remaining; fall through to a generic
        # message when the probe didn't include it.
        m = re.search(r"(\d+)s remaining", low)
        if m:
            return (f"{target_label} auth is in cool-down ({m.group(1)}s remaining) — "
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
    ``hosts`` map (CONS-003). One helper keeps the wording, truncation
    threshold, and key ordering identical so a future copy-paste isn't
    needed; Webmin and Portainer keep their bespoke shapes because
    their probe contracts are different (Webmin returns a single
    host_key; Portainer inspects ``Version``).

    Returns the exact dict the route should return.
    """
    err = probe_result.get("error")
    if err:
        return {"ok": False, "detail": _humanise_probe_error(err, target_label)}
    hosts = probe_result.get("hosts") or {}
    names = sorted(hosts.keys())
    label = item_singular if len(hosts) == 1 else item_plural
    detail = (f"OK — reached {target_label}, {len(hosts)} {label} visible: "
              + (", ".join(names[:5]) or "none"))
    if len(names) > 5:
        detail += f" (+{len(names) - 5} more)"
    return {"ok": True, "detail": detail,
            count_key: len(hosts), items_key: names}


@app.get("/api/hosts")
async def api_hosts(force: bool = False):
    """Hosts view — returns the CURATED host list merged with live
    stats from every enabled provider.

    Source of truth is ``hosts_config`` (Settings → Hosts). If it's
    empty, falls back to auto-discovering from the Beszel / Pulse
    batch maps so the view isn't blank for fresh installs.

    NOTE (#524) — refactored from the original ~975-line inline
    duplication of the Beszel / Pulse / NE / Webmin probe logic to
    compose the protected helper chain (``_get_host_provider_state`` +
    ``_merge_one_host``) per row. Bearer-token scrapers (Homarr widget,
    Grafana, custom dashboards) hitting THIS endpoint now share the
    SPA's single-flight lock on hub probes (#506) AND the per-host
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
                    "id":          k,
                    "label":       (v or {}).get("beszel_name") or k,
                    "ne_url":      "",
                    "beszel_name": k,
                    "pulse_name":  "",
                    "enabled":     True,
                }
                for k, v in sorted(beszel_map.items(), key=lambda kv: kv[0].lower())
            ]
        elif pulse_map:
            curated = [
                {
                    "id":          k,
                    "label":       (v or {}).get("pulse_name") or k,
                    "ne_url":      "",
                    "beszel_name": "",
                    "pulse_name":  k,
                    "enabled":     True,
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
        ), return_exceptions=False)
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
            "_merged":      merged,
            "_providers":   providers_hit,
        })

    # ---- Shape the response ---------------------------------------
    # Snapshot fallback (#449) — apply ONCE for every entry whose probes
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
        # Share `_shape_host_api_row` with the new endpoints (#458/#459).
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
            _shape_host_api_row(h, s, entry["_providers"], any_provider_enabled=True)
        )

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
# #547 — cache TTL is now operator-tunable via
# `tuning_host_provider_cache_ttl_seconds`. Default preserved at 10s.
# Resolved at every consumer site (NOT cached at module import) per
# the strict-rule contract.
_host_provider_cache: dict = {"ts": 0.0, "state": None}
# Single-flight guard for ``_get_host_provider_state`` (#506). Without
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
# #546 — both Webmin cache TTLs are now operator-tunable via
# `tuning_webmin_host_cache_ttl_seconds` (default 30s, success cache)
# and `tuning_webmin_host_fail_cache_ttl_seconds` (default 5s, negative
# cache from #506). Resolved per consumer-site read.
_webmin_host_cache: dict[str, tuple[float, dict]] = {}
_webmin_host_fail_cache: dict[str, tuple[float, dict]] = {}

# Per-host SNMP result caches — same pattern as the Webmin caches.
# Success cache for 30s, fail cache for 5s. SNMP probes are bounded by
# UDP timeout (default 5s × ~13 OID walks fanned in parallel ≈ 5-8s
# wall-clock on a healthy host) so caching the result for the burst
# fan-out is the same win Webmin gets. Per-host id keying matches the
# Webmin cache; SNMP is per-host, no central hub.
_snmp_host_cache: dict[str, tuple[float, dict]] = {}
_snmp_host_fail_cache: dict[str, tuple[float, dict]] = {}


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
    # Webmin (#344). Bust on every settings-save touching SNMP creds /
    # aliases so the next probe picks up the new community / version /
    # port without waiting out the 30s TTL.
    _snmp_host_cache.clear()
    _snmp_host_fail_cache.clear()


async def _get_host_provider_state(force: bool = False) -> dict:
    """Fetch + cache the provider state needed to merge any host.

    The "batch" providers (Beszel, Pulse) expose one endpoint that
    returns every host in one call, so we memoise them for
    ``_HOST_PROVIDER_CACHE_TTL`` seconds. A burst of /api/hosts/one/{id}
    calls from the SPA hits the cache; settings changes auto-clear
    after the TTL expires (no explicit invalidation needed).
    """
    def _compute_cache_key() -> tuple[set[str], tuple]:
        """Return (active_sources, cache_key) — the active providers as a
        set + the cache-bust key (sorted-active-tuple + cred-blob-hash).
        Re-callable so the post-lock path can refresh the key after a
        settings save during the lock-wait (BUG-004 / #518) without
        risking the queued caller using a pre-save snapshot.
        """
        active_set = active_host_stats_providers()
        # Cache key includes the active-sources tuple so a settings
        # change like flipping `host_stats_source` from "beszel" to
        # "beszel,pulse" auto-busts the cache. Save paths also call
        # `invalidate_host_provider_cache()` directly for instant
        # feedback; the key match is defence-in-depth.
        # BUG-010 fix (#416) — credential-blob hash folded into the key
        # so changing `beszel_password` (without flipping
        # `host_stats_source`) busts the cache too.
        cred_blob = "|".join((
            get_setting("beszel_hub_url", "") or "",
            get_setting("beszel_identity", "") or "",
            get_setting("beszel_password", "") or "",
            get_setting("beszel_verify_tls", "true") or "true",
            get_setting("pulse_url", "") or "",
            get_setting("pulse_token", "") or "",
            get_setting("pulse_verify_tls", "true") or "true",
            get_setting("webmin_url", "") or "",
            get_setting("webmin_user", "") or "",
            get_setting("webmin_password", "") or "",
            get_setting("webmin_verify_tls", "true") or "true",
            get_setting("node_exporter_url_template", "") or "",
            get_setting("node_exporter_overrides", "") or "",
            # SNMP (#344) — every credential / default that affects
            # what the probe sees. v3 keys are the security-sensitive
            # ones; the community + port + version + aliases also
            # belong here so a global default change auto-busts the
            # cache without waiting on the explicit invalidate path.
            get_setting("snmp_default_community", "") or "",
            get_setting("snmp_default_version", "") or "",
            get_setting("snmp_default_port", "") or "",
            get_setting("snmp_v3_user", "") or "",
            get_setting("snmp_v3_auth_key", "") or "",
            get_setting("snmp_v3_priv_key", "") or "",
            get_setting("snmp_aliases", "") or "",
        ))
        cred_hash = hashlib.sha256(cred_blob.encode("utf-8")).hexdigest()[:16]
        return active_set, (tuple(sorted(active_set)), cred_hash)

    now = time.time()
    active, cache_key = _compute_cache_key()
    # #547 — cache TTL is operator-tunable; resolve once at the top of
    # the function and reuse for both the pre-lock and post-lock checks
    # (within the same call, the value can't legitimately change).
    cache_ttl = tuning.tuning_int("tuning_host_provider_cache_ttl_seconds")
    cached = _host_provider_cache.get("state")
    cached_key = _host_provider_cache.get("key")
    if (not force and cached and cached_key == cache_key
            and (now - _host_provider_cache.get("ts", 0.0)) < cache_ttl):
        return cached

    # Single-flight (#506) — only ONE concurrent caller does the cold-
    # cache probe; the rest await on the lock and pick up the populated
    # cache via the post-lock re-check below. Pre-fix N parallel
    # /api/hosts/one/<id> calls fired N independent Beszel hub + Pulse
    # probes, saturating the event loop. Force=true requests still
    # serialise here so a SPA settings-save fan-out doesn't 6× the
    # upstream load either.
    # #533 — measure the wait so operators can see whether contention
    # is the cause of elevated /api/hosts/one latency vs slow upstreams.
    # First caller bucket-counts in sub-ms (zero wait); subsequent
    # callers in the same fan-out bucket-count in seconds.
    _lock_wait_start = time.monotonic()
    async with _host_provider_lock:
        metrics.HOST_PROVIDER_LOCK_WAIT.observe(time.monotonic() - _lock_wait_start)
        # BUG-004 fix (#518) — RE-COMPUTE active + cache_key inside the
        # lock. A settings save during the lock-wait could have changed
        # `host_stats_source` or any credential, so the pre-lock values
        # are stale. Without this re-compute, a queued caller would run
        # a probe under a snapshot that no longer matches the current
        # settings (e.g. probing Beszel after the operator turned it
        # off). Generalisable rule: when single-flighting via lock-then-
        # recheck, re-COMPUTE the cache key inside the lock, don't just
        # re-read the cache.
        active, cache_key = _compute_cache_key()
        # Re-check inside the lock: another caller may have populated
        # the cache while we were waiting. ``force`` requests always
        # re-probe but only the FIRST forced caller pays the cost —
        # subsequent forced callers within the same lock-acquire window
        # see a fresh cache (now < TTL) and reuse it.
        now2 = time.time()
        cached2 = _host_provider_cache.get("state")
        cached_key2 = _host_provider_cache.get("key")
        if (cached2 and cached_key2 == cache_key
                and (now2 - _host_provider_cache.get("ts", 0.0)) < cache_ttl):
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

    # Beszel + Pulse hub probes run in PARALLEL (#517). Prior sequential
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
        hub_url = get_setting("beszel_hub_url", "") or ""
        ident = get_setting("beszel_identity", "") or ""
        passw = get_setting("beszel_password", "") or ""
        verify = (get_setting("beszel_verify_tls", "true") or "true").lower() == "true"
        if not (hub_url and ident and passw):
            return {}, "missing url / identity / password"
        r = await _beszel.probe_hub(hub_url, ident, passw, verify_tls=verify)
        return r.get("systems") or {}, r.get("error")

    async def _probe_pulse() -> tuple[dict, str | None]:
        if "pulse" not in active:
            return {}, None
        pulse_url = get_setting("pulse_url", "") or ""
        pulse_token = get_setting("pulse_token", "") or ""
        verify = (get_setting("pulse_verify_tls", "true") or "true").lower() == "true"
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

    # SNMP (#344) — settings-derived defaults flow through state so
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
        snmp_default_community = get_setting("snmp_default_community", "") or "public"
        snmp_default_version = (
            get_setting("snmp_default_version", "") or "v2c"
        ).strip().lower() or "v2c"
        try:
            snmp_default_port = int(get_setting("snmp_default_port", "") or "161")
        except (TypeError, ValueError):
            snmp_default_port = 161
        snmp_v3_user = get_setting("snmp_v3_user", "") or ""
        snmp_v3_auth_key = get_setting("snmp_v3_auth_key", "") or ""
        snmp_v3_priv_key = get_setting("snmp_v3_priv_key", "") or ""
        try:
            sn_aliases_raw = json.loads(get_setting("snmp_aliases", "{}") or "{}")
            if isinstance(sn_aliases_raw, dict):
                snmp_aliases = {
                    str(k).strip(): str(v).strip()
                    for k, v in sn_aliases_raw.items()
                    if str(k).strip() and str(v).strip()
                }
        except ValueError:
            snmp_aliases = {}

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
        # SNMP (#344) — defaults + aliases. Per-host overrides land
        # later via `hosts_config[].snmp`.
        "snmp_default_community": snmp_default_community,
        "snmp_default_version":   snmp_default_version,
        "snmp_default_port":      snmp_default_port,
        "snmp_v3_user":           snmp_v3_user,
        "snmp_v3_auth_key":       snmp_v3_auth_key,
        "snmp_v3_priv_key":       snmp_v3_priv_key,
        "snmp_aliases":           snmp_aliases,
    }
    _host_provider_cache["ts"] = time.time()
    _host_provider_cache["state"] = state
    _host_provider_cache["key"] = cache_key
    return state


async def _merge_one_host(h: dict, state: dict, *, force: bool = False) -> tuple[dict, list[str]]:
    """Merge one curated host with provider data. Runs NE + Webmin
    probes inline for THIS host only; Beszel/Pulse lookups hit the
    cached batch maps. Returns (merged_dict, providers_hit).

    #531 — when ``force=True``, drop this host's per-host Webmin
    caches (success + failure) before the probe block so the next
    `probe_webmin` call hits the wire. Pre-fix `?force=true` only
    bypassed the OUTER `_host_provider_cache`; the 30s success cache
    + 5s failure cache still served the previously-cached entry.
    Operators expect "force = re-probe everything for THIS host".
    Settings-save paths already invalidate every cache via
    `invalidate_host_provider_cache()`; this is the per-host force-
    refresh path (drawer reopen with `?force=true`).
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
        # SNMP per-host caches (#344) — same force=true contract as
        # Webmin. Drop both success + fail entries so the next probe
        # block hits the wire and produces a fresh sample.
        _snmp_host_cache.pop(h["id"], None)
        _snmp_host_fail_cache.pop(h["id"], None)

    # Pulse — coarse fallback layer.
    pulse_key = h.get("pulse_name") or h.get("id") or ""
    if "pulse" in active and pulse_key:
        pstats = _pulse.lookup(state["pulse_map"], pulse_key)
        if pstats:
            _merge_best(merged, pstats)
            providers_hit.append("pulse")

    # SNMP (#344) — runs AFTER Pulse but BEFORE Beszel so the unix-
    # style providers can override SNMP's coarser data wherever they
    # have visibility. Each curated row can override community / port
    # / version / v3 keys via `hosts_config[].snmp`; falls through to
    # the global defaults from state otherwise. Per-host alias map
    # (Docker hostname → SNMP target) wins over the row's snmp_name.
    # Per-host enable gate (#651): the row's ``snmp.enabled`` opts a
    # specific host IN/OUT of SNMP probing without losing the rest of
    # the override config. Default when the flag is missing: enabled
    # (preserves the historical "snmp_name set → probe" behaviour so
    # existing rows don't silently stop reporting).
    if "snmp" in active:
        from logic import snmp as _snmp
        row_snmp = h.get("snmp") if isinstance(h.get("snmp"), dict) else {}
        snmp_enabled = row_snmp.get("enabled", True)
        snmp_target = (
            (state.get("snmp_aliases") or {}).get(h["id"])
            or (h.get("snmp_name") or "").strip()
            or h["id"]
        )
        if snmp_target and snmp_enabled:
            now = time.time()
            wm_success_ttl = tuning.tuning_int("tuning_webmin_host_cache_ttl_seconds")
            wm_fail_ttl = tuning.tuning_int("tuning_webmin_host_fail_cache_ttl_seconds")
            cached = _snmp_host_cache.get(h["id"])
            if cached and (now - cached[0]) < wm_success_ttl:
                result = cached[1]
            else:
                fail_cached = _snmp_host_fail_cache.get(h["id"])
                if fail_cached and (now - fail_cached[0]) < wm_fail_ttl:
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
                        )
                    except Exception as e:  # noqa: BLE001
                        result = {"hosts": {}, "error": f"snmp probe failed: {e}"}
                    if (result.get("hosts") or {}):
                        _snmp_host_cache[h["id"]] = (now, result)
                        _snmp_host_fail_cache.pop(h["id"], None)
                    else:
                        _snmp_host_fail_cache[h["id"]] = (now, result)
                        _snmp_host_cache.pop(h["id"], None)
                        err = result.get("error") or "empty hosts map"
                        print(f"[hosts] snmp probe failed for {h.get('id')!r}: {err}")
            hosts_map = result.get("hosts") or {}
            if hosts_map:
                stats = next(iter(hosts_map.values()))
                _merge_best(merged, stats)
                providers_hit.append("snmp")

    # Beszel.
    beszel_key = h.get("beszel_name") or h.get("id") or ""
    if "beszel" in active and beszel_key:
        bstats = state["beszel_map"].get(beszel_key)
        if bstats:
            _merge_best(merged, bstats)
            providers_hit.append("beszel")

    # Node-exporter (per-host probe).
    # #540 — operator-tunable timeout via `tuning_node_exporter_probe_timeout_seconds`.
    if "node_exporter" in active and h.get("ne_url"):
        _ne_timeout = tuning.tuning_int("tuning_node_exporter_probe_timeout_seconds")
        try:
            async with httpx.AsyncClient(verify=False, timeout=float(_ne_timeout)) as ne_client:
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
            # #546 — both cache TTLs are operator-tunable. Resolved
            # once per call (the same TTLs apply across both branches
            # of the if/else below).
            wm_success_ttl = tuning.tuning_int("tuning_webmin_host_cache_ttl_seconds")
            wm_fail_ttl = tuning.tuning_int("tuning_webmin_host_fail_cache_ttl_seconds")
            cached = _webmin_host_cache.get(h["id"])
            if cached and (now - cached[0]) < wm_success_ttl:
                result = cached[1]
            else:
                # Negative-result cache (#506) — short-circuit a recently-
                # failed probe so a SPA fan-out burst doesn't burn 20s ×
                # PARALLEL on an unreachable Webmin. Tunable TTL means
                # recovery is felt within one Hosts-tab refresh cycle
                # at the default 5s.
                fail_cached = _webmin_host_fail_cache.get(h["id"])
                if fail_cached and (now - fail_cached[0]) < wm_fail_ttl:
                    result = fail_cached[1]
                else:
                    # #539 — Webmin probe budget is operator-tunable;
                    # shared with the legacy `api_hosts` consumer.
                    _wm_budget = tuning.tuning_int("tuning_webmin_probe_budget_seconds")
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
                    # Cache the OUTCOME — successes go in the long-lived
                    # cache (30s TTL), failures go in the negative cache
                    # (5s TTL) so a hung Webmin doesn't re-burn 20s on
                    # every parallel call. Recovery is felt within 5s
                    # because the fail cache is short.
                    if (result.get("hosts") or {}):
                        _webmin_host_cache[h["id"]] = (now, result)
                        _webmin_host_fail_cache.pop(h["id"], None)
                    else:
                        _webmin_host_fail_cache[h["id"]] = (now, result)
                        # BUG-007 fix (#521) — also drop any stale
                        # success entry so the negative-cache's "fast
                        # failure detection" claim actually holds. Pre-
                        # fix a host whose success cache was populated
                        # 25s ago + has just gone down would keep
                        # serving the stale success for 5 more seconds
                        # (until the success cache's 30s TTL expired)
                        # because the success cache lookup at line 3631
                        # short-circuits before the fail cache is even
                        # consulted.
                        _webmin_host_cache.pop(h["id"], None)
                        err = result.get("error") or "empty hosts map"
                        print(f"[hosts] webmin probe failed for {h.get('id')!r}: {err}")
            hosts_map = result.get("hosts") or {}
            if hosts_map:
                stats = next(iter(hosts_map.values()))
                _merge_best(merged, stats)
                providers_hit.append("webmin")

    # Ping (#343) — fifth provider, runs LAST in the merge chain. Only
    # consults the LATEST stored sample (the sampler does the actual
    # probing on its own cadence). When this host is opted-out
    # (``hosts_config[].ping.enabled == False``), we deliberately skip —
    # no row, no chip, no banner.
    pcfg = h.get("ping") if isinstance(h.get("ping"), dict) else {}
    if "ping" in active and pcfg.get("enabled"):
        from logic import ping_sampler as _ping_sampler
        from logic import ping as _ping_mod
        recent = _ping_sampler.last_samples(h["id"], limit=1)
        if recent:
            last = recent[0]
            stats = _ping_mod.to_host_stats({
                "alive":    last.get("alive"),
                "rtt_ms":   last.get("rtt_ms"),
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

    # Snapshot fallback (#449) — when a provider went down mid-session,
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

    return merged, providers_hit


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
        except Exception:
            _asset_idx_cache["index"] = {}
        _asset_idx_cache["mtime"] = mtime
    raw = _asset_idx_cache["index"].get(cn_int)
    return _ai.shape_asset(raw) if raw else None


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
    # Status precedence (revised — see operator complaint that hosts
    # were marked "down" purely because Beszel was paused/down even
    # when Pulse + node-exporter + Webmin were happily scraping):
    #   1. ANY non-Beszel provider returning data → "up". Beszel's
    #      self-reported status is suggestive but its agent can be
    #      paused/down while the host is still reachable — pulse /
    #      NE / webmin all probe via different paths/ports/protocols
    #      and a successful scrape from any of them proves the host
    #      is alive. SSH and other "is this host reachable" gates
    #      depend on this status, so a single failing provider must
    #      not lock other features out.
    #   2. Beszel's explicit status (with paused → down normalisation)
    #      when Beszel is the ONLY signal we have. Operator pauses
    #      hosts in Beszel deliberately when they're offline; "down"
    #      here reflects reality.
    #   3. Pulse's explicit status as a secondary fallback.
    #   4. "up" if any provider hit at all (covers Beszel-only
    #      hosts where Beszel returned data with no explicit status).
    #   5. "unconfigured" when no provider is mapped/enabled — grey.
    #   6. "unknown" when providers ARE mapped + active but none
    #      answered — surfaced red as a real outage signal.
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
    snmp_mapped = bool(
        (h.get("snmp_name") or "").strip()
        or (isinstance(h.get("snmp"), dict) and h.get("snmp"))
    )
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
    elif (not any_provider_enabled) or not (
        (h.get("beszel_name") or "").strip()
        or (h.get("pulse_name")  or "").strip()
        or (h.get("webmin_name") or "").strip()
        or (h.get("ne_url")      or "").strip()
        or ping_enabled
        or snmp_mapped
    ):
        host_status = "unconfigured"
    else:
        host_status = "unknown"
    return {
        "id":              h["id"],
        "name":            h["id"],
        "host":            h["id"],
        # Empty label is INTENTIONAL post-#621 — frontend's
        # `hostDisplayName(h)` falls back to the asset inventory's
        # name when this is blank. The previous `or h["id"]` fallback
        # silently overrode the operator's "use asset name" intent on
        # every API response. Pass the literal stored value through.
        "label":           h.get("label") or "",
        "beszel_name":     h.get("beszel_name") or "",
        "pulse_name":      h.get("pulse_name") or "",
        "ne_url":          h.get("ne_url") or "",
        # SNMP target alias (#344). Surfaced on the API row so
        # `providerStates(h)` and `hostHasAgent(h)` can decide whether
        # to render the SNMP chip + count this host as having an agent.
        "snmp_name":       h.get("snmp_name") or "",
        "url":             h.get("url") or "",
        "icon":            h.get("icon") or "",
        "providers":       providers_hit or [],
        "status":          host_status,
        # Raw per-provider status surfaced so the SPA's `providerStates(h)`
        # helper can mark a chip red when Beszel/Pulse self-reports
        # paused/down even if it returned data (otherwise the chip
        # stays green because the provider was technically "hit").
        "beszel_status":   s.get("beszel_status") or "",
        "docker_node":     (h["id"] if _is_swarm_node(h.get("id")) else ""),
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
        # Asset-inventory snapshot — null when no match. Resolved
        # lazily here (vs. eagerly in the loop above) so each
        # _shape_host_api_row call is self-contained. The cache read
        # is fast (file → JSON) but the index build is O(N), so
        # repeated calls in /api/hosts/one/{id} fanouts pay it once
        # per call. If that becomes a hotspot we can stash the
        # index on the request via FastAPI Depends().
        "asset":            _resolve_asset_for_host(h.get("custom_number")),
        # Per-host SSH-enabled flag (#622 — opt-in semantics post
        # migration #001). True only when the operator explicitly ticked
        # "Enable SSH for this host" in Admin → Hosts. The drawer's SSH
        # card + common-actions panel render only when this is true.
        "ssh_enabled":      bool((h.get("ssh") or {}).get("enabled", False)),
        # Ping (#343). `ping_enabled` is the per-host opt-in flag (the
        # SPA uses it to gate the latency chip + drawer chart). The
        # alive / RTT / loss values come from the merged provider
        # dict — empty when the sampler hasn't run yet OR ping isn't
        # enabled for this host. Booleans coerced safely so a
        # null-from-snapshot doesn't crash the spread.
        "ping_enabled":     bool((h.get("ping") or {}).get("enabled", False)),
        "ping_alive":       bool(s.get("host_ping_alive")) if s.get("host_ping_alive") is not None else None,
        "ping_rtt_ms":      (float(s.get("host_ping_rtt_ms")) if s.get("host_ping_rtt_ms") is not None else None),
        "ping_loss_pct":    (float(s.get("host_ping_loss_pct")) if s.get("host_ping_loss_pct") is not None else None),
        # Load averages (node-exporter primary, Beszel agents emit
        # `la=[1m,5m,15m]` which `extract_stats` now also surfaces here
        # so the load-average chart works for Beszel-only hosts too).
        # Frontend only renders the row when any of the three is > 0.
        "load_1m":          float(s.get("host_load_1m") or 0),
        "load_5m":          float(s.get("host_load_5m") or 0),
        "load_15m":         float(s.get("host_load_15m") or 0),
        # Per-sensor temperatures (#437). `host_temperatures` is a
        # `{sensor: celsius}` dict from the Beszel agent's `stats.t`
        # (only present when the agent exposes thermal data — Pi has
        # `cpu_thermal`, Intel/AMD has `package_id_0`, NVMe has
        # `nvme_composite`). Hosts without thermal sensors get an
        # empty dict and the frontend chart card hides via the
        # length-gate. The whitelist on this row was the reason the
        # field was being silently dropped before — extract_stats
        # produced it but it never reached the SPA without this line.
        "host_temperatures": dict(s.get("host_temperatures") or {}),
        # Service summary (#321) — Beszel agents that run with the
        # systemd extension emit a list of service objects. The
        # extractor normalises into `{total, failed, failed_names}`.
        # Hosts whose agent doesn't track services get
        # `{total: 0, failed: 0, failed_names: []}` and the drawer
        # badge gates on `services.total > 0` to hide cleanly.
        "services":         (s.get("host_services") or {"total": 0, "failed": 0, "failed_names": []}),
        # DMI / hardware identity (node-exporter only — Linux /
        # FreeBSD with the DMI collector). Empty strings = no DMI.
        "dmi_vendor":       (s.get("host_dmi_vendor") or ""),
        "dmi_product":      (s.get("host_dmi_product") or ""),
        "dmi_serial":       (s.get("host_dmi_serial") or ""),
        "dmi_bios_version": (s.get("host_dmi_bios_version") or ""),
        # Stale-marker bookkeeping (#449). Populated by
        # apply_host_snapshot_fallback when a provider went down and we
        # filled missing host_* fields from the persisted snapshot.
        # SPA's isStale / isStaleField / staleAge helpers consult these
        # to dim the corresponding bars / fields and surface the
        # "Showing cached data" drawer banner. Empty list / 0 when
        # everything is live so the frontend's reconcile clears the
        # markers cleanly when a provider recovers.
        "_stale_fields":   list(s.get("_stale_fields") or []),
        "_stale_ts":       float(s.get("_stale_ts") or 0.0),
        # Permanent-fail tracking (#383). All four fields are non-zero
        # only when the host_metrics_sampler has recorded consecutive
        # failures for this host. `sampling_paused: true` triggers the
        # frontend banner + table icon; the operator clears via POST
        # /api/hosts/{id}/resume-sampling.
        **_failure_state_for_host(h["id"]),
    }


def _failure_state_for_host(host_id: str) -> dict:
    """Read the host_failure_state row for a given host (#383). Returns
    the four fields when the read succeeds AND the row exists. Returns
    only the falsy defaults when the row genuinely doesn't exist (host
    has never failed). Returns an EMPTY dict on any DB error so the
    spread in `_shape_host_api_row` becomes a no-op — letting the
    frontend's in-place reconcile preserve the previously-known values
    instead of momentarily flipping `sampling_paused` to false during
    a transient SQLite BUSY (which the frontend would render as the
    icon vanishing and reappearing on every poll cycle — #405)."""
    try:
        with db_conn() as c:
            cur = c.execute(
                "SELECT first_failure_ts, consecutive_failures, paused, "
                "last_error, last_failure_ts "
                "FROM host_failure_state WHERE host_id = ?",
                (host_id,),
            )
            row = cur.fetchone()
    except Exception:
        # Don't return falsy defaults — that would clobber a previously
        # paused row's marker on the wire. Empty dict means "no info,
        # frontend keep what you had". See #405.
        return {}
    if row is None:
        # Row genuinely absent — host has never failed.
        return {
            "sampling_paused":            False,
            "failure_window_started_at":  0,
            "consecutive_failures":       0,
            "last_error":                 "",
            "last_failure_ts":            0,
        }
    # ENH-018 — surface ``last_failure_ts`` so the drawer can render
    # "last error N seconds ago" alongside the existing
    # "first failure M minutes ago" banner copy. Falls back to
    # ``first_failure_ts`` for rows that pre-date the column add (the
    # first probe failure on the new schema overwrites the NULL).
    last_ts = row[4] if (len(row) > 4 and row[4] is not None) else row[0]
    return {
        "sampling_paused":            bool(row[2]),
        "failure_window_started_at":  int(row[0] or 0),
        "consecutive_failures":       int(row[1] or 0),
        "last_error":                 row[3] or "",
        "last_failure_ts":            int(last_ts or 0),
    }


@app.get("/api/hosts/list")
async def api_hosts_list(force: bool = False):
    """Skeleton endpoint — curated host list + global state, NO
    per-host probes. Paired with /api/hosts/one/{id} for progressive
    loading: the SPA paints rows immediately from this response, then
    fans out per-host fetches to fill in the stats.

    `force=true` bypasses the 10s `_host_provider_cache` memo. Used
    by the SPA right after a successful host-stats settings save so
    the operator sees the new provider state without waiting up to
    10s for the next natural cache miss (#367 / UX-001).
    """
    curated = _load_hosts_config()
    state = await _get_host_provider_state(force=force)
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


async def _hosts_one_inner(h: dict, *, force: bool):
    """Inner helper for `/api/hosts/one/{host_id}` — fetches the
    provider state then merges this host's row. Split out so the
    outer endpoint can wrap the whole sequence in `asyncio.wait_for`.
    """
    state = await _get_host_provider_state(force=force)
    merged_pair = await _merge_one_host(h, state, force=force)
    return state, merged_pair


@app.get("/api/hosts/one/{host_id}")
async def api_hosts_one(host_id: str, force: bool = False):
    """Merge ONE curated host with provider data.

    Called N times in parallel by the SPA after /api/hosts/list
    returns the skeleton. The shared Beszel/Pulse cache ensures the
    batch probes run at most once per TTL window.

    ``force=true`` mirrors the parallel param on ``/api/hosts/list``
    (#347) and bypasses the 10s provider-state cache so a host drawer
    re-opened immediately after Admin → Hosts Save sees fresh provider
    data instead of waiting out the TTL
    """
    curated = _load_hosts_config()
    h = next((x for x in curated if x.get("id") == host_id), None)
    if h is None or not h.get("enabled", True):
        raise HTTPException(404, f"Host not found: {host_id}")
    # Outer per-host budget (30s — #506). With #506's single-flight
    # `_get_host_provider_state` lock, the cold-cache Beszel+Pulse cost
    # is paid by the FIRST caller only; subsequent fan-out calls reuse
    # the populated cache. Worst-case for the first caller is
    # ~15s Beszel + ~15s Pulse + ~10s NE + ~20s Webmin sequentially,
    # but NE has its own 10s `httpx` timeout and Webmin its own 20s
    # `asyncio.wait_for`, so a single laggy provider can't blow past
    # this budget. 30s comfortably under any reasonable NPM
    # `proxy_read_timeout` (default 60s) so OmniGrid's explicit 504
    # always fires first, never NPM's generic gateway timeout.
    # #528 — capture probe wall-clock so the SPA can hover-title a
    # "took Xs" hint on the row. Useful when a host shows `unknown`
    # status: operators can see at a glance whether it was a fast 5xx
    # or a slow 30s hang, without grepping logs.
    _probe_start = time.monotonic()
    try:
        state, (merged, providers) = await asyncio.wait_for(
            _hosts_one_inner(h, force=force),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"per-host probe budget exceeded (30s) for {host_id}",
        )
    probe_elapsed_ms = int((time.monotonic() - _probe_start) * 1000)
    any_enabled = bool(state["active"])
    row = _shape_host_api_row(
        h, merged, providers, any_provider_enabled=any_enabled,
    )
    row["_probe_elapsed_ms"] = probe_elapsed_ms
    # NO SSE publish here (#515). Earlier this endpoint published
    # `host:row_updated` so other tabs would see the freshly-merged row
    # — but the SAME tab subscribes to the bus, so the event triggered
    # the SPA's `host:row_updated` listener, which called
    # `refreshHostRow(id)`, which hit THIS endpoint again, which
    # published another event. Self-sustaining infinite loop, amplified
    # by N hosts × M tabs. Reads aren't a state change; the events that
    # legitimately need to push host updates (`host:failure_state_changed`,
    # `host:history_appended`) are still published from
    # `host_metrics_sampler`. Other tabs catch up on the next poll cycle
    # (the SPA gracefully degrades to 30s polling when SSE is connected
    # — see CLAUDE.md's polling-fallback bullet).
    return {"host": row}


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
            # Empty label is INTENTIONAL post-#621 — frontend's
            # `hostDisplayName(h)` resolver falls back to the asset
            # inventory's name when this is blank. The previous
            # `or hid` fallback (kept for years pre-asset-inventory)
            # silently overwrote that intent on EVERY load, defeating
            # the save-side fixes in #632 / #635. Pass the literal
            # stored value through.
            "label":       (h.get("label") or "").strip(),
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
            # Free-text IP field — operator-maintained, not auto-derived
            # from `ne_url` / DNS / asset inventory. Stored as-typed so
            # the operator can put "192.168.2.1", "10.0.0.5/24", or
            # "fe80::1" and we don't second-guess. No filter impact
            # today; captured so the Hosts drawer can display it and
            # a future group-filter iteration can parse it.
            "ip":          (h.get("ip") or "").strip()[:64],
            # Per-host SSH override sub-dict. Optional user / port /
            # disabled / host override — the key material itself lives
            # in the GLOBAL ssh_default_private_key setting (V1 scope:
            # single global key). Missing or non-dict values collapse
            # to {} so downstream code can always do dict.get(...).
            "ssh":         _clean_host_ssh(h.get("ssh")),
            # Per-host ping opt-in (#343). Default OFF — operator opts
            # in per host. Optional `port` + `transport` overrides
            # cascade over the globals.
            "ping":        _clean_host_ping(h.get("ping")),
            # SNMP target alias (#344) — Docker hostname → SNMP-reachable
            # name/IP when the curated row's id isn't directly addressable
            # by the SNMP agent. Empty falls through to the global
            # snmp_aliases map and finally to the bare id.
            "snmp_name":   (h.get("snmp_name") or "").strip(),
            # Per-host SNMP override sub-dict. Optional community /
            # version / port / v3_user / v3_auth_key / v3_priv_key —
            # any unset key falls through to the global default. {} =
            # "no override" (the common case).
            "snmp":        _clean_host_snmp(h.get("snmp")),
            "enabled":     bool(h.get("enabled", True)),
        })
    return clean


def _clean_host_ssh(raw: Any) -> dict:
    """Normalise the per-host ``ssh`` sub-dict.

    Accepts only the keys that make sense at V1 (``user`` / ``port``
    / ``host`` / ``fqdn`` / ``password`` / ``enabled``) and coerces
    their types. Unknown keys are dropped so a malformed import can't
    smuggle arbitrary fields into the persisted JSON. Empty → empty
    dict, which the SSH module treats as "host opted OUT of SSH"
    under the post-#622 opt-in semantics.

    Pre-#622 the gate field was ``disabled`` (off-when-set, default =
    inherit global). Post-#622 it's ``enabled`` (on-when-set, default
    = host is OFF). Inputs with the legacy ``disabled`` key are
    silently dropped here — the client-side ``norm()`` in
    `static/js/app.js` already converts old-shape backups at import
    time, and the schema migration in ``logic/migrations.py`` rewrites
    every existing DB row, so by the time data reaches this validator
    it should always be the new shape. Defensive: if a stray legacy
    POST arrives with ``disabled: false``, we treat it as the
    pre-flip "implicitly enabled" intent and write ``enabled: true``.
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
    # New `enabled` flag (#622). ONLY explicit `enabled: true` writes
    # the flag through; everything else (absent, false, legacy
    # `disabled` field) leaves the row in the new "OFF until opted in"
    # default. The schema migration in `logic/migrations.py:#001`
    # handles legacy data on first boot — DO NOT add a defensive
    # `disabled` fallback here (#628 root cause): the writer runs on
    # every save, not just at import, and any "fall back to enabled
    # when not explicitly disabled" branch would re-enable rows the
    # operator just unchecked elsewhere in the same save.
    if raw.get("enabled") is True:
        out["enabled"] = True
    return out


def _clean_host_ping(raw: Any) -> dict:
    """Normalise the per-host ``ping`` sub-dict (#343).

    Accepts ``enabled`` (bool, default False), ``port`` (int 1..65535
    or null = use global ``ping_default_port``), and ``transport``
    (``"tcp"`` / ``"icmp"`` / null = use global ``ping_use_icmp``).
    Unknown keys are dropped; malformed types collapse to default.
    Empty input → empty dict (= "no per-host config" — which itself
    means "ping NOT enabled for this host" because the gate defaults
    to OFF).
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    if bool(raw.get("enabled")):
        out["enabled"] = True
    port = raw.get("port")
    if port not in (None, "", 0):
        try:
            p = int(port)
            if 1 <= p <= 65535:
                out["port"] = p
        except (TypeError, ValueError):
            pass
    t = (str(raw.get("transport") or "")).strip().lower()
    if t in ("tcp", "icmp"):
        out["transport"] = t
    return out


def _clean_host_snmp(raw: Any) -> dict:
    """Normalise the per-host ``snmp`` override sub-dict (#344).

    Accepts every per-host SNMP override on a curated row:
      * ``community`` (str)        — overrides ``snmp_default_community``
      * ``version``   ("v2c"/"v3") — overrides ``snmp_default_version``
      * ``port``      (1..65535)   — overrides ``snmp_default_port``
      * ``v3_user``   (str)        — overrides ``snmp_v3_user``
      * ``v3_auth_key`` (str)      — overrides ``snmp_v3_auth_key``
      * ``v3_priv_key`` (str)      — overrides ``snmp_v3_priv_key``

    Empty / missing input → empty dict (the common case — most rows
    inherit every default). Unknown keys are dropped silently so a
    malformed import or a stale field name can't smuggle arbitrary
    data through. v3 keys persist VERBATIM in the curated JSON; admin
    is the only role that reads/writes ``hosts_config``, and the
    backup tooling already redacts the file via the same path it
    redacts ssh.password — see logic.backups.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    # #651 — explicit per-host enable flag, parallel to ping.enabled.
    # Default behaviour: when the field is missing, treat as enabled
    # (preserves the historical "snmp_name set → probe" gate so existing
    # rows don't silently stop working). When the field is explicitly
    # False, the row opts OUT even with snmp_name configured — useful
    # for temporarily disabling a flaky SNMP target without losing the
    # community / version / port overrides on the row.
    if "enabled" in raw:
        out["enabled"] = bool(raw.get("enabled"))
    community = (str(raw.get("community") or "")).strip()
    if community:
        out["community"] = community
    version = (str(raw.get("version") or "")).strip().lower()
    if version in ("v2c", "v3"):
        out["version"] = version
    port = raw.get("port")
    if port not in (None, "", 0):
        try:
            p = int(port)
            if 1 <= p <= 65535:
                out["port"] = p
        except (TypeError, ValueError):
            pass
    for k in ("v3_user", "v3_auth_key", "v3_priv_key"):
        v = (str(raw.get(k) or "")).strip()
        if v:
            out[k] = v
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

    # Duplicate-custom_number check — must run BEFORE id-dedup,
    # because the UI may send two entries with the same cn but
    # different ids and we want to point the operator at both of
    # them (the id-dedup loop below would collapse same-id rows
    # but leave different-id / same-cn rows in, which is what
    # this check catches).
    by_cn: dict[int, list[str]] = {}
    for h in hosts:
        if not isinstance(h, dict):
            continue
        cn = _coerce_int(h.get("custom_number"))
        if cn is None:
            continue
        hid = (h.get("id") or h.get("name") or "").strip() or "(unnamed)"
        by_cn.setdefault(cn, []).append(hid)
    dupes = {cn: ids for cn, ids in by_cn.items() if len(ids) > 1}
    if dupes:
        parts = [f"#{cn}: {', '.join(ids)}" for cn, ids in sorted(dupes.items())]
        raise HTTPException(
            400,
            "hosts_config: duplicate custom_number — "
            + "; ".join(parts)
            + ". Each host must have a unique custom_number.",
        )

    # Duplicate-id check — without this, two rows with the same id
    # would silently collapse via `seen[hid] = ...` (last wins),
    # losing the first row + its custom_number / IP / SSH overrides
    # without any error to the operator (BUG-009 in the code review).
    id_counts: dict[str, int] = {}
    for h in hosts:
        if not isinstance(h, dict):
            continue
        hid = (h.get("id") or h.get("name") or "").strip()
        if not hid:
            continue
        id_counts[hid] = id_counts.get(hid, 0) + 1
    id_dupes = sorted(hid for hid, n in id_counts.items() if n > 1)
    if id_dupes:
        raise HTTPException(
            400,
            "hosts_config: duplicate id — "
            + ", ".join(id_dupes)
            + ". Each host must have a unique id.",
        )

    seen: dict[str, dict] = {}
    for h in hosts:
        if not isinstance(h, dict):
            raise HTTPException(400, "every host entry must be an object")
        hid = (h.get("id") or h.get("name") or "").strip()
        if not hid:
            raise HTTPException(400, "host entry is missing 'id'")
        seen[hid] = {
            "id":            hid,
            # Empty label is INTENTIONAL post-#621 — the SPA's
            # `hostDisplayName(h)` resolver falls back to the asset
            # inventory's name when this is blank. DO NOT auto-fill
            # with `hid` here: that would silently overwrite an empty
            # operator intent with the host id, defeating the
            # "inherit from asset" feature on every save.
            "label":         (h.get("label") or "").strip(),
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
            # Free-text IP — see _load_hosts_config for rationale.
            "ip":            (h.get("ip") or "").strip()[:64],
            # Per-host SSH override block — see _clean_host_ssh for
            # the shape contract. {} when no override is set.
            "ssh":           _clean_host_ssh(h.get("ssh")),
            # Per-host ping opt-in (#343).
            "ping":          _clean_host_ping(h.get("ping")),
            # Per-host SNMP target alias + per-row override block (#344).
            "snmp_name":     (h.get("snmp_name") or "").strip(),
            "snmp":          _clean_host_snmp(h.get("snmp")),
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
    # Host-config rows feed provider name resolution (beszel_name /
    # pulse_name / webmin_name aliases). Drop the provider state cache
    # so /api/hosts/one/{id} doesn't serve up to 10s of stale results
    # using the old aliases. Same rationale as in api_set_settings.
    invalidate_host_provider_cache()
    return {"hosts": saved, "count": len(saved)}


@app.post("/api/hosts/{host_id}/resume-sampling")
async def api_hosts_resume_sampling(
    host_id: str,
    _u: auth.User = Depends(auth.require_admin),
):
    """Admin-only: clear the auto-pause marker for a host that the
    host_metrics_sampler has put on hold after consecutive failures.
    Next sampler tick will re-attempt the probe; if it succeeds the
    row stays cleared, if it fails the failure-window counter starts
    again from zero.

    Validates ``host_id`` against the curated ``hosts_config`` list
    so the endpoint behaves consistently with `/api/hosts/one/{host_id}`
    — admin previously
    could DELETE a stale failure-state row for a host_id that wasn't
    even in the curated list, which is harmless but inconsistent with
    the parallel endpoint's 404).
    """
    curated = _load_hosts_config()
    h = next((x for x in curated if x.get("id") == host_id), None)
    if h is None:
        raise HTTPException(404, f"Host not found: {host_id}")
    try:
        with db_conn() as c:
            cur = c.execute(
                "DELETE FROM host_failure_state WHERE host_id = ?",
                (host_id,),
            )
            cleared = cur.rowcount or 0
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"resume-sampling failed: {e}")
    # ENH-012 (#427) — also clear the SSH + Webmin auth cooldowns for
    # this host so a single resume click recovers from the
    # all-three-providers-paused-on-same-host case (sampler is paused,
    # SSH cooldown still arming, Webmin cooldown still arming). Each
    # cooldown is keyed differently — SSH on (host_id, user); Webmin
    # on (base_url, user). For Webmin we walk the per-host alias map +
    # the global URL since either could be the cooldown target. Both
    # provider modules expose `_auth_cooldown_timer` per CLAUDE.md's
    # "Add a host-stats provider" canonical checklist (CONS-004).
    cooldown_cleared: list[str] = []
    try:
        from logic import ssh as _ssh
        # SSH cooldowns are keyed on (host_id, user); we don't know the
        # user here, so wipe the entire cooldown map for this host_id
        # by iterating known users from the cooldown's internal store.
        # Cooldown.clear(*key) takes the same key tuple as arm/remaining
        # so we walk and clear known (host_id, *) pairs. Implementation
        # detail: Cooldown stores keys as a tuple in `._timers`.
        timers = getattr(_ssh._auth_cooldown_timer, "_armed", None)
        if timers:
            doomed = [k for k in list(timers.keys())
                      if isinstance(k, tuple) and k and k[0] == (host_id or "")]
            for k in doomed:
                _ssh._auth_cooldown_timer.clear(*k)
                cooldown_cleared.append(f"ssh:{k}")
    except Exception as e:
        print(f"[hosts] resume-sampling: ssh cooldown clear failed: {e}")
    try:
        from logic import webmin as _webmin
        # Webmin cooldowns key on (base_url, user). The host's base_url
        # could come from `webmin_url` field or the alias map; walk the
        # cooldown's `_timers` dict and drop any entry whose first key
        # element matches one of the candidate URLs.
        candidates: set[str] = set()
        wurl = (h.get("webmin_url") or "").strip().rstrip("/")
        if wurl:
            candidates.add(wurl)
        # Resolved URL via the alias map — Webmin module's helper
        webmin_name = (h.get("webmin_name") or "").strip()
        if webmin_name:
            try:
                aliases_raw = get_setting("webmin_aliases", "") or ""
                aliases = json.loads(aliases_raw) if aliases_raw else {}
                if isinstance(aliases, dict):
                    aliased = (aliases.get(webmin_name) or "").strip().rstrip("/")
                    if aliased:
                        candidates.add(aliased)
            except Exception:
                pass
        timers = getattr(_webmin._auth_cooldown_timer, "_armed", None)
        if timers and candidates:
            doomed = [k for k in list(timers.keys())
                      if isinstance(k, tuple) and k and k[0] in candidates]
            for k in doomed:
                _webmin._auth_cooldown_timer.clear(*k)
                cooldown_cleared.append(f"webmin:{k}")
    except Exception as e:
        print(f"[hosts] resume-sampling: webmin cooldown clear failed: {e}")
    if cooldown_cleared:
        print(f"[hosts] {host_id!r} resume-sampling cleared cooldowns: {cooldown_cleared}")
    invalidate_host_provider_cache()
    return {
        "host_id": host_id,
        "cleared": bool(cleared),
        "cooldowns_cleared": len(cooldown_cleared),
    }


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
    from logic import snmp as _snmp

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
    # SNMP test row (#344). Body fields are all optional; defaults flow
    # through from the global settings the same way other providers do.
    snmp_target = (body.get("snmp_target") or body.get("snmp_name") or "").strip()
    if not snmp_target and row_id:
        try:
            sn_aliases = json.loads(get_setting("snmp_aliases", "{}") or "{}")
            if isinstance(sn_aliases, dict):
                snmp_target = str(sn_aliases.get(row_id, "") or "").strip()
        except ValueError:
            snmp_target = ""
    snmp_community = (body.get("snmp_community") or "").strip()
    snmp_version = (body.get("snmp_version") or "").strip().lower()
    try:
        snmp_port = int(body.get("snmp_port") or 0) or 0
    except (TypeError, ValueError):
        snmp_port = 0
    out = {
        "beszel": {"ok": False, "skipped": True, "detail": "not set"},
        "pulse":  {"ok": False, "skipped": True, "detail": "not set"},
        "node_exporter": {"ok": False, "skipped": True, "detail": "not set"},
        "webmin": {"ok": False, "skipped": True, "detail": "not set"},
        "snmp":   {"ok": False, "skipped": True, "detail": "not set"},
    }

    # Respect the global host_stats_source CSV — a provider disabled
    # in Settings → Host stats MUST NOT be probed here, even if the
    # operator filled in its per-row field. The live Hosts-view code
    # path already honours this; the per-row test needs to match so
    # "passes here" = "works in production".
    active_sources = {
        s.strip().lower() for s in
        (get_setting("host_stats_source", "") or "").split(",")
        if s.strip() and s.strip().lower() != "none"
    }

    if beszel_name and "beszel" not in active_sources:
        out["beszel"] = {"ok": False, "skipped": True,
                         "detail": "disabled in host_stats_source"}
        beszel_name = ""  # skip the probe block below
    if pulse_name and "pulse" not in active_sources:
        out["pulse"] = {"ok": False, "skipped": True,
                        "detail": "disabled in host_stats_source"}
        pulse_name = ""
    if ne_url and "node_exporter" not in active_sources:
        out["node_exporter"] = {"ok": False, "skipped": True,
                                "detail": "disabled in host_stats_source"}
        ne_url = ""
    if webmin_url and "webmin" not in active_sources:
        out["webmin"] = {"ok": False, "skipped": True,
                         "detail": "disabled in host_stats_source"}
        webmin_url = ""
    if snmp_target and "snmp" not in active_sources:
        out["snmp"] = {"ok": False, "skipped": True,
                       "detail": "disabled in host_stats_source"}
        snmp_target = ""

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

    if snmp_target:
        if not _snmp.has_snmp_support():
            out["snmp"] = {
                "ok": False, "skipped": False,
                "detail": "pysnmp not installed (pip install pysnmp)",
            }
        else:
            community = snmp_community or get_setting("snmp_default_community", "public") or "public"
            version = snmp_version or (get_setting("snmp_default_version", "v2c") or "v2c").lower()
            try:
                port = snmp_port or int(get_setting("snmp_default_port", "161") or "161")
            except (TypeError, ValueError):
                port = 161
            r = await _snmp.probe_snmp(
                snmp_target,
                community=community,
                version=version,
                port=port,
                v3_user=get_setting("snmp_v3_user", "") or "",
                v3_auth_key=get_setting("snmp_v3_auth_key", "") or "",
                v3_priv_key=get_setting("snmp_v3_priv_key", "") or "",
            )
            if r.get("error") and not r.get("hosts"):
                out["snmp"] = {"ok": False, "skipped": False,
                               "detail": f"snmp error: {r['error']}"}
            elif r.get("hosts"):
                host_key, stats = next(iter(r["hosts"].items()))
                cpu = stats.get("host_cpu_percent")
                mem = stats.get("host_mem_total") or 0
                detail_bits = [f"matched · {host_key}"]
                if cpu is not None:
                    try:
                        detail_bits.append(f"cpu={int(cpu)}%")
                    except (TypeError, ValueError):
                        pass
                if mem:
                    detail_bits.append(f"mem={mem // (1024**3)} GB")
                out["snmp"] = {
                    "ok": True, "skipped": False,
                    "detail": " · ".join(detail_bits),
                }
            else:
                out["snmp"] = {"ok": False, "skipped": False,
                               "detail": "snmp responded with no parseable data"}

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

    # SNMP discovery (#344) — there's no central hub to enumerate so
    # discovery surfaces the configured ``snmp_aliases`` map's keys.
    # Each entry is the curated row's id; the autocomplete value is
    # the alias's TARGET (the SNMP-reachable host/IP). The Admin →
    # Hosts editor renders this list as the snmp_name column's
    # datalist so operators don't have to retype targets they've
    # already mapped at the global level. Empty when no aliases are
    # configured — that's the expected state on first-boot deploys.
    snmp_names: list[str] = []
    try:
        sn_aliases_raw = json.loads(get_setting("snmp_aliases", "{}") or "{}")
        if isinstance(sn_aliases_raw, dict):
            snmp_names = sorted(
                {str(v).strip() for v in sn_aliases_raw.values() if str(v).strip()},
                key=str.lower,
            )
    except ValueError:
        snmp_names = []

    return {
        "beszel": beszel_names,
        "pulse":  pulse_names,
        "webmin": webmin_names,
        "snmp":   snmp_names,
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
    from logic import host_metrics_sampler as _host_metrics_sampler

    curated = _load_hosts_config()
    record = next((h for h in curated if h["id"] == id), None)
    if record is None:
        raise HTTPException(404, f"no curated host with id={id!r}")

    # Which providers are live? Same derivation as api_hosts (CONS-004).
    active = active_host_stats_providers()

    providers_raw: dict[str, Any] = {
        "pulse": None, "beszel": None, "node_exporter": None,
        "webmin": None, "snmp": None,
    }
    providers_normalized: dict[str, Any] = {
        "pulse": None, "beszel": None, "node_exporter": None,
        "webmin": None, "snmp": None,
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
        # #540 — operator-tunable NE probe timeout.
        _ne_timeout = tuning.tuning_int("tuning_node_exporter_probe_timeout_seconds")
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
                # Last 5 host_metrics_samples rows for this host. The
                # sampler writes one row per STATS_SAMPLE_INTERVAL
                # (default 5 min) when NE returns meaningful gauges or
                # sane-bounded counter deltas; see
                # logic.host_metrics_sampler._compute_row.
                "recent_metrics_samples": _host_metrics_sampler.last_samples(record["id"], limit=5),
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

    # ---- Ping (#343) — most recent samples + the resolved sampler
    #      target so the operator can see exactly what address the
    #      probe is hitting (DNS failure debugging). Only renders
    #      when ping is in active AND this host is opted in. -------
    if "ping" in active and bool((record.get("ping") or {}).get("enabled", False)):
        try:
            from logic import ping_sampler as _ping_sampler_dbg
            from logic import ping as _ping_dbg
            samples = _ping_sampler_dbg.last_samples(record["id"], limit=5) or []
            # Replicate the sampler's target-resolution chain so the
            # debug surface shows the same `host` the probe is using.
            ping_cfg = (record.get("ping") or {}) if isinstance(record.get("ping"), dict) else {}
            ssh_cfg = (record.get("ssh") or {}) if isinstance(record.get("ssh"), dict) else {}
            url_host_dbg = ""
            url_raw = (record.get("url") or "").strip()
            if url_raw:
                try:
                    from urllib.parse import urlparse as _urlparse_dbg
                    url_host_dbg = (_urlparse_dbg(url_raw).hostname or "").strip()
                except (ValueError, TypeError):
                    url_host_dbg = ""
            target = (
                (ping_cfg.get("host") or "").strip()
                or (ssh_cfg.get("fqdn") or "").strip()
                or (ssh_cfg.get("host") or "").strip()
                or url_host_dbg
                or record["id"]
            )
            providers_raw["ping"] = {
                "target":          target,
                "port":            ping_cfg.get("port"),
                "transport":       ping_cfg.get("transport") or "(global default)",
                "icmp_supported":  _ping_dbg.has_icmp_support(),
                "samples_count":   len(samples),
                "last_samples":    samples,
            }
            if samples:
                last = samples[0]
                stats = _ping_dbg.to_host_stats({
                    "alive":    last.get("alive"),
                    "rtt_ms":   last.get("rtt_ms"),
                    "loss_pct": last.get("loss_pct"),
                })
                if stats:
                    providers_normalized["ping"] = stats
        except Exception as e:
            providers_raw["ping"] = {"_error": str(e)}

    # ---- SNMP (#344) — fresh probe against THIS host. Surfaces the
    #      raw response shape (host_key + first few stats fields) so
    #      operators can confirm community/version/port resolution and
    #      see which OIDs the agent actually answered. -------------
    if "snmp" in active:
        from logic import snmp as _snmp
        if not _snmp.has_snmp_support():
            providers_raw["snmp"] = {"_error": "pysnmp not installed"}
        else:
            row_snmp = (record.get("snmp") if isinstance(record.get("snmp"), dict)
                        else {})
            try:
                sn_aliases = json.loads(get_setting("snmp_aliases", "{}") or "{}")
                if not isinstance(sn_aliases, dict):
                    sn_aliases = {}
            except ValueError:
                sn_aliases = {}
            snmp_target = (
                sn_aliases.get(record["id"])
                or (record.get("snmp_name") or "").strip()
                or record["id"]
            )
            community = ((row_snmp.get("community") or "").strip()
                         or (get_setting("snmp_default_community", "") or "public"))
            version = (((row_snmp.get("version") or "").strip().lower())
                       or (get_setting("snmp_default_version", "") or "v2c").lower()
                       or "v2c")
            try:
                port = int(row_snmp.get("port")
                           or get_setting("snmp_default_port", "") or "161")
            except (TypeError, ValueError):
                port = 161
            v3_user = ((row_snmp.get("v3_user") or "").strip()
                       or get_setting("snmp_v3_user", "") or "")
            v3_auth = ((row_snmp.get("v3_auth_key") or "").strip()
                       or get_setting("snmp_v3_auth_key", "") or "")
            v3_priv = ((row_snmp.get("v3_priv_key") or "").strip()
                       or get_setting("snmp_v3_priv_key", "") or "")
            try:
                r = await _snmp.probe_snmp(
                    snmp_target,
                    community=community, version=version, port=port,
                    v3_user=v3_user, v3_auth_key=v3_auth,
                    v3_priv_key=v3_priv,
                    timeout=10.0, active_sources=active,
                )
                providers_raw["snmp"] = {
                    "target":     snmp_target,
                    "community":  community,
                    "version":    version,
                    "port":       port,
                    "v3_user":    v3_user,
                    "v3_auth_set": bool(v3_auth),
                    "v3_priv_set": bool(v3_priv),
                    "hosts_keys": sorted((r.get("hosts") or {}).keys()),
                    "error":      r.get("error"),
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

    # ---- Rendered — what /api/hosts would return for this host ---
    try:
        live = await api_hosts()
        rendered = next(
            (h for h in (live.get("hosts") or []) if h.get("id") == id),
            None,
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
        if (p == "beszel"        and (record.get("beszel_name") or "").strip())
        or (p == "pulse"         and (record.get("pulse_name") or "").strip())
        or (p == "node_exporter" and (record.get("ne_url") or "").strip())
        or (p == "webmin"        and (record.get("webmin_name") or "").strip())
        or (p == "ping"          and bool((record.get("ping") or {}).get("enabled", False)))
        # SNMP is "active for this host" when EITHER an alias is mapped
        # OR a per-row snmp_name is set. The provider also runs against
        # the bare host id when no alias / name is set, but that's the
        # implicit default — we only mark the row "actively snmp-probed"
        # when the operator has signalled intent.
        or (p == "snmp" and bool(
            ((record.get("snmp_name") or "").strip())
            or (isinstance(record.get("snmp"), dict) and record["snmp"])
        ))
    )
    return {
        "host_record":          record,
        "active_providers":     host_active,
        "active_providers_global": sorted(active),
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


# ----------------------------------------------------------------------------
# Interactive SSH terminal — TODO #170
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
    from logic import ssh as _ssh
    try:
        with db_conn() as c:
            cur = c.execute(
                "INSERT INTO history "
                "(ts, op_type, target_name, target_id, target_stack, "
                " status, duration, events, error, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    "ssh_terminal",
                    f"{resolved.get('user') or '?'}@{resolved.get('host') or host_id}",
                    f"{host_id}",
                    None,
                    "running",
                    0.0,
                    json.dumps([{
                        "ts":    time.time(),
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
@app.websocket("/api/hosts/{host_id}/ssh/terminal")
async def ws_ssh_terminal(websocket: WebSocket, host_id: str):
    """Bridge a browser WebSocket to a live PTY-backed SSH shell.

    Frame protocol (browser → backend):
      - **binary**     — raw stdin bytes (forwarded verbatim to the shell).
      - **text JSON**  — control message:
            ``{"type": "resize", "cols": N, "rows": M}``
            ``{"type": "ping"}``  (no-op; server pings are separate)

    Frame protocol (backend → browser):
      - **binary**     — raw stdout bytes from the shell.
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
    #         apply to WebSocket routes.
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
    #          WebSocket upgrades skip the HTTP middleware's CSRF path,
    #          so admin-only WS routes can't rely on the same
    #          double-submit cookie protection HTTP routes get. The
    #          session cookie's ``SameSite=lax`` attribute blocks most
    #          cross-site WS upgrades on Chromium / Firefox, but
    #          subdomain attacks and custom proxy setups can still
    #          leak the cookie. Reject the upgrade when the browser-
    #          supplied Origin doesn't match the resolved server
    #          origin. ``Origin`` may be empty for some non-browser
    #          callers (e.g. command-line tools that explicitly bypass
    #          it); we treat empty as "no claim made" and accept it
    #          since the admin cookie + role gate already rejected
    #          unauthenticated callers — the Origin gate is purely a
    #          browser-CSWSH defence and a missing header isn't one of
    #          those attack shapes.
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
    bytes_in = 0   # browser -> shell
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
        loop = asyncio.get_event_loop()
        stop_event = asyncio.Event()

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
                        chunk = chunk.encode("utf-8", errors="replace")
                    bytes_out += len(chunk)
                    await websocket.send_bytes(chunk)
            except (asyncio.CancelledError, asyncssh.DisconnectError,
                    asyncssh.Error, BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                print(f"[ssh] terminal upstream_to_ws error: {type(e).__name__}: {e}")
            finally:
                stop_event.set()

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
                            data_b = data_s.encode("utf-8", errors="replace")
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
            except Exception as e:
                print(f"[ssh] terminal ws_to_upstream error: {type(e).__name__}: {e}")
            finally:
                stop_event.set()

        async def heartbeat():
            """WS ping every 25s so idle proxies don't drop us."""
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(25)
                    if stop_event.is_set():
                        break
                    try:
                        # Starlette's WebSocket doesn't expose a public
                        # ping; fall back to a JSON keepalive frame the
                        # client can ignore. Keeps any L7 proxy from
                        # dropping the idle TCP socket.
                        await websocket.send_json({"type": "keepalive", "ts": time.time()})
                    except Exception:
                        break
            except asyncio.CancelledError:
                pass

        t1 = asyncio.create_task(upstream_to_ws(), name="ssh-term-up")
        t2 = asyncio.create_task(ws_to_upstream(), name="ssh-term-dn")
        t3 = asyncio.create_task(heartbeat(),       name="ssh-term-hb")
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
                except (asyncio.CancelledError, Exception):
                    pass

        # Try to harvest the shell's exit code so the close frame can
        # surface "exit 0" vs "exit 1". asyncssh exposes this on the
        # process once the channel closes.
        exit_code = None
        try:
            exit_code = proc.exit_status
        except Exception:
            exit_code = None
        if exit_code not in (None, 0):
            final_error = f"shell exited with code {exit_code}"
        try:
            await websocket.send_json({"type": "exit", "code": exit_code})
        except Exception:
            pass
        try:
            await websocket.close(code=1000, reason="shell exited")
        except Exception:
            pass
    except WebSocketDisconnect:
        # Normal browser-side close (tab closed / network blip). Not an
        # error; final_status stays "success".
        pass
    except Exception as e:
        final_status = "failed"
        final_error = f"{type(e).__name__}: {e}"
        print(f"[ssh] terminal session ERROR host={host_id!r}: {e}")
        try:
            await websocket.close(code=4500, reason="internal_error")
        except Exception:
            pass
    finally:
        # Always close the upstream SSH connection.
        if proc is not None:
            try:
                proc.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(conn.wait_closed(), timeout=5.0)
            except Exception:
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
        # NE-only path — read from host_metrics_samples and shape it
        # into the same envelope Beszel returns.
        from logic import host_metrics_sampler as _hms
        try:
            series = _hms.history_series(hid, h)
            # Per-metric "did the collector ever report?" diagnostic
            # (#347). Lets the SPA tell "host is just idle" from
            # "exporter doesn't expose node_disk_* / node_network_*"
            # — different empty-state copy + different remediation.
            collectors = _hms.series_collectors_present(hid, h)
        except Exception as e:
            return {"series": [], "error": f"host_metrics_sampler: {e}"}
        return {"series": series, "collectors": collectors, "error": None}

    if not sid:
        return {"series": [], "error": "system_id or host_id required"}

    from logic import beszel as _beszel
    hub_url = get_setting("beszel_hub_url", "") or ""
    ident = get_setting("beszel_identity", "") or ""
    passw = get_setting("beszel_password", "") or ""
    verify = (get_setting("beszel_verify_tls", "true") or "true").lower() == "true"
    if not (hub_url and ident and passw):
        return {"series": [], "error": "Beszel not configured"}
    return await _beszel.fetch_system_history(
        hub_url, ident, passw, sid, hours=h, verify_tls=verify,
        host_id=(hid or None),
    )


@app.get("/api/hosts/{host_id}/ping/history")
async def api_hosts_ping_history(
    host_id: str, hours: int = 1,
    _admin: auth.User = Depends(auth.require_admin),
):
    """Ping reachability time-series for one curated host (#343).

    Mirrors :func:`api_hosts_history` shape — returns
    ``{points: [...], error: None}`` with one point per
    ``ping_samples`` row in the window. Empty list when this host
    has never been probed (sampler hasn't run yet, or the host isn't
    opted in). Window clamped to 1..168 hours like the Beszel path.
    """
    h = max(1, min(168, int(hours or 1)))
    hid = (host_id or "").strip()
    if not hid:
        return {"points": [], "error": "host_id required"}
    # Reach into the sampler module's read helper (same pattern the
    # NE-only path uses with ``host_metrics_sampler.recent_samples``).
    from logic import ping_sampler as _ping_sampler
    since = int(time.time() - h * 3600)
    try:
        rows = _ping_sampler.recent_samples(hid, since, limit=h * 60)
    except Exception as e:
        return {"points": [], "error": f"ping_sampler: {e}"}
    return {"points": rows, "error": None}


class PingTestIn(BaseModel):
    host_id: str
    # Optional ad-hoc overrides — when blank, the test honours the
    # host's persisted ping config (or the global defaults). Used by
    # the Settings-tab "Test ping" button when the operator has typed
    # values that haven't been saved yet.
    port: Optional[int] = None
    transport: Optional[str] = None
    timeout_seconds: Optional[float] = None


@app.post("/api/ping/test")
async def api_ping_test(
    body: PingTestIn,
    _admin: auth.User = Depends(auth.require_admin),
):
    """One-shot ping probe against a curated host (#343). Used by the
    "Test ping" button in Settings → Host stats and the per-host test
    in Admin → Hosts. Always live (no cache); does NOT write to
    ``ping_samples`` so test-clicks don't pollute the chart series.
    """
    hid = (body.host_id or "").strip()
    if not hid:
        raise HTTPException(400, "host_id required")
    curated = _load_hosts_config()
    h = next((x for x in curated if x.get("id") == hid), None)
    if h is None:
        raise HTTPException(404, f"Host not found: {hid}")
    # Resolve the probe target the same way the sampler does so the
    # test result reflects what the sampler will actually probe.
    ssh_cfg = h.get("ssh") if isinstance(h.get("ssh"), dict) else {}
    target = (ssh_cfg.get("fqdn") or ssh_cfg.get("host") or hid).strip() or hid
    pcfg = h.get("ping") if isinstance(h.get("ping"), dict) else {}
    default_port = int(get_setting("ping_default_port", "443") or "443") or 443
    port = body.port if body.port is not None else (pcfg.get("port") or default_port)
    use_icmp_global = get_setting_bool("ping_use_icmp", False)
    transport = (body.transport or pcfg.get("transport") or "").strip().lower()
    if transport not in ("tcp", "icmp"):
        transport = "icmp" if use_icmp_global else "tcp"
    timeout = float(body.timeout_seconds) if body.timeout_seconds is not None \
        else float(tuning.tuning_int("tuning_ping_probe_timeout_seconds"))
    from logic import ping as _ping_mod
    if transport == "icmp" and not _ping_mod.has_icmp_support():
        transport = "tcp"
    result = await _ping_mod.probe_ping(
        target, port=int(port), transport=transport,
        timeout_seconds=timeout, count=3,
    )
    return {
        "ok":     bool(result.get("alive")),
        "host":   target,
        "port":   int(port),
        "transport": transport,
        **result,
    }


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


# Admin → Version page was removed in 2026-04-30 alongside the deploy
# migration to image-build. Pre-#606 the page wrote to /app/VERSION.txt
# via a per-file bind mount; post-#606 the file is baked into the image
# at build time and any in-container write lands in the ephemeral
# overlay layer that the next `service update --force` discards. The
# durable seed path is now: edit repo-root VERSION.txt, commit, push —
# deploy.yml's source-B resolver (head -n1 ${DEPLOY_PATH}/VERSION.txt)
# picks it up as the floor for the next PATCH bump.


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

    The per-service master switch (#204) `open_meteo_enabled` is
    consulted first — when disabled, return `""` regardless of what
    URL is stored. This way the URL stays in the settings table for
    when the operator flips back on, but the weather endpoint cleanly
    reports "not configured" while the switch is off.
    """
    from logic.db import get_setting_bool
    if not get_setting_bool("open_meteo_enabled", default=True):
        return ""
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
        # Admin → General stores `open_meteo_url` (post-#354 split out
        # of the legacy Notifications panel); blank disables the
        # widget entirely rather than forwarding to a hardcoded public
        # endpoint the operator didn't opt into.
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


# ----------------------------------------------------------------------------
# Persistent log files (#424 / #425). Daily files under /app/data/logs/.
# Admin-only. Three routes:
#   GET /api/admin/logs/files                      — directory listing
#   GET /api/admin/logs/files/{name}?tail=N        — text body, last N lines (N optional)
#   GET /api/admin/logs/files/{name}/download      — full file as attachment
# Filename is validated against the canonical regex inside `safe_log_path`
# so path-traversal attempts (../, absolute paths) bounce with 404.
# ----------------------------------------------------------------------------
@app.get("/api/admin/logs/files")
async def api_admin_logs_files(_admin: auth.User = Depends(auth.require_admin)):
    return {"files": _logs.list_persistent_logs(), "log_dir": _logs.LOG_DIR}


@app.get("/api/admin/logs/files/{name}")
async def api_admin_logs_file_view(
    name: str,
    tail: int = 0,
    _admin: auth.User = Depends(auth.require_admin),
):
    body = _logs.read_persistent_log(name, tail_lines=tail if tail > 0 else None)
    if body is None:
        return JSONResponse(status_code=404, content={"detail": "log file not found"})
    return Response(content=body, media_type="text/plain; charset=utf-8")


@app.get("/api/admin/logs/files/{name}/download")
async def api_admin_logs_file_download(
    name: str,
    _admin: auth.User = Depends(auth.require_admin),
):
    path = _logs.safe_log_path(name)
    if not path or not os.path.isfile(path):
        return JSONResponse(status_code=404, content={"detail": "log file not found"})
    return FileResponse(path, filename=name, media_type="text/plain; charset=utf-8")


# ============================================================================
# Auth routes (step 1: local login, logout, one-shot bootstrap, /api/me).
# Registered here — above the StaticFiles catch-all — per CLAUDE.md.
# ============================================================================
# ----------------------------------------------------------------------------
# TOTP / 2FA challenge store (#345). In-memory dict mapping
# challenge_id -> {user_id, kind, secret?, issued_at, expires_at}. Lifespan-
# scoped because the matching cookie isn't issued until the second step
# completes. Single-replica pinning (CLAUDE.md) makes this safe.
# ``kind`` is one of:
#   "totp_required"      — user has TOTP enrolled; verifying a code
#   "totp_setup_required" — policy forces enrolment; user must set up
#                            TOTP before the cookie is issued.
# ----------------------------------------------------------------------------
_TOTP_CHALLENGE_TTL_SECONDS = 5 * 60
_totp_challenges: dict[str, dict] = {}


def _prune_totp_challenges() -> None:
    now = time.time()
    stale = [k for k, v in _totp_challenges.items() if v.get("expires_at", 0) <= now]
    for k in stale:
        _totp_challenges.pop(k, None)


def _create_totp_challenge(payload: dict) -> tuple[str, int]:
    _prune_totp_challenges()
    cid = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + _TOTP_CHALLENGE_TTL_SECONDS
    _totp_challenges[cid] = {**payload, "expires_at": expires_at}
    return cid, expires_at


def _consume_totp_challenge(cid: str) -> Optional[dict]:
    _prune_totp_challenges()
    return _totp_challenges.pop(cid, None)


def _peek_totp_challenge(cid: str) -> Optional[dict]:
    _prune_totp_challenges()
    return _totp_challenges.get(cid)


# ----------------------------------------------------------------------------
# WebAuthn (passkey) challenge stores (#381). Two flavours, both the same
# in-memory dict shape as the TOTP store -- single-replica deploy makes it
# safe. Pruned lazily on every read/write.
#
#   _webauthn_login_challenges -- raw challenge bytes pending second-
#       factor verification. Keyed by challenge_id (opaque token the
#       SPA echoes back). Created by /api/local-auth/webauthn-start;
#       consumed by /api/local-auth/webauthn-finish. 5-min TTL.
#
#   _webauthn_register_challenges -- raw challenge bytes pending
#       enrolment. Keyed by user_id (the call sites are authed and we
#       only allow one in-flight enrolment per user). Created by
#       /api/me/webauthn/register-start; consumed by register-finish.
#       5-min TTL.
#
# RP ID + origin are derived per-request from the URL the SPA hit
# (request.url.hostname / .scheme), so dev (localhost:8088) and prod
# (NPM-fronted domain) both work without a settings entry.
# ----------------------------------------------------------------------------
_WEBAUTHN_CHALLENGE_TTL_SECONDS = 5 * 60
_webauthn_login_challenges: dict[str, dict] = {}
_webauthn_register_challenges: dict[int, dict] = {}


def _prune_webauthn_challenges() -> None:
    now = time.time()
    for k in [k for k, v in _webauthn_login_challenges.items()
              if v.get("expires_at", 0) <= now]:
        _webauthn_login_challenges.pop(k, None)
    for k in [k for k, v in _webauthn_register_challenges.items()
              if v.get("expires_at", 0) <= now]:
        _webauthn_register_challenges.pop(k, None)


def _create_webauthn_login_challenge(payload: dict) -> tuple[str, int]:
    _prune_webauthn_challenges()
    cid = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + _WEBAUTHN_CHALLENGE_TTL_SECONDS
    _webauthn_login_challenges[cid] = {**payload, "expires_at": expires_at}
    return cid, expires_at


def _consume_webauthn_login_challenge(cid: str) -> Optional[dict]:
    _prune_webauthn_challenges()
    return _webauthn_login_challenges.pop(cid, None)


def _peek_webauthn_login_challenge(cid: str) -> Optional[dict]:
    _prune_webauthn_challenges()
    return _webauthn_login_challenges.get(cid)


def _set_webauthn_register_challenge(user_id: int, payload: dict) -> int:
    _prune_webauthn_challenges()
    expires_at = int(time.time()) + _WEBAUTHN_CHALLENGE_TTL_SECONDS
    _webauthn_register_challenges[user_id] = {
        **payload, "expires_at": expires_at,
    }
    return expires_at


def _consume_webauthn_register_challenge(user_id: int) -> Optional[dict]:
    _prune_webauthn_challenges()
    return _webauthn_register_challenges.pop(user_id, None)


def _request_rp_id(request: Request) -> str:
    """Derive the WebAuthn RP ID from the incoming request.

    RP ID is the hostname (no port, no scheme) the SPA hit, AS THE
    BROWSER SEES IT — has to be a registrable suffix of the page's
    actual origin or `navigator.credentials.create()` rejects with
    SecurityError. Behind a reverse proxy (NPM in OmniGrid's deploy)
    the upstream connection's URL has the internal hostname (typically
    ``localhost`` or the Docker stack name), which would mismatch the
    public domain the browser sees and break enrolment (#433).

    Resolution order: ``X-Forwarded-Host`` header (what proxies set
    when they want the backend to know the original Host), then the
    ``Host`` header (NPM forwards this verbatim), then
    ``request.url.hostname`` as a last resort for direct (non-proxied)
    dev runs. Strip the ``:port`` suffix in every case — RP IDs are
    hostname-only.

    ENH-015 / #480 — the WebAuthn register-finish path calls this
    twice (directly + via `_request_origin`); cache the resolved value
    on `request.state.rp_id` so the second call is a dict lookup.
    """
    cached = getattr(request.state, "rp_id", None)
    if cached is not None:
        return cached
    candidates = [
        request.headers.get("x-forwarded-host", ""),
        request.headers.get("host", ""),
        request.url.hostname or "",
    ]
    for raw in candidates:
        host = (raw or "").split(",")[0].strip().lower()
        if ":" in host:
            host = host.split(":", 1)[0]
        if host:
            try:
                request.state.rp_id = host
            except Exception:
                # `WebSocket` doesn't expose `state` like Request — the
                # cache is best-effort; just skip when unavailable.
                pass
            return host
    raise HTTPException(
        status_code=400,
        detail=_err.message_for(_err.AUTH_WEBAUTHN_RP_ID_UNRESOLVABLE),
    )


def _request_origin(request) -> str:
    """Full origin used for WebAuthn assertion verification AND for the
    WebSocket admin-route Origin gate.

    Accepts either a Starlette ``Request`` or a ``WebSocket``; both
    expose ``.headers`` and ``.url`` with the shape we need so the
    helper duck-types cleanly.

    Resolution order matches ``_request_rp_id`` — ``X-Forwarded-Host``
    (what the public-facing reverse proxy sets to convey the original
    Host), then the ``Host`` header, then ``request.url.netloc /
    .hostname`` as a final fallback. Some NPM setups rewrite the Host
    header to the internal upstream hostname while preserving the
    public hostname in X-Forwarded-Host — if origin disagrees with
    rp_id, the WebAuthn verifier rejects with "Unexpected client data
    origin" because the browser-signed clientDataJSON.origin (the
    public URL) doesn't match the server-computed expected_origin
    (the internal one). Honouring X-Forwarded-Host on this side keeps
    rp_id + origin in lock-step.

    Also trusts ``X-Forwarded-Proto`` so HTTPS termination at NPM is
    visible to the verifier.
    """
    proto = (request.headers.get("x-forwarded-proto", "")
             or request.url.scheme or "http").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        # ENH-006 / #473 — reject bogus X-Forwarded-Proto values
        # (e.g. "ftp", "file") instead of silently flipping to https.
        # Falls back to the actual request scheme; logs once so a
        # mis-configured proxy is debuggable from Admin → Logs.
        bad = proto
        proto = (request.url.scheme or "http").lower()
        if proto not in ("http", "https"):
            proto = "http"
        print(
            f"[webauthn] rejecting X-Forwarded-Proto={bad!r} "
            f"(not http/https) — falling back to scheme={proto!r}"
        )
    host_candidates = [
        request.headers.get("x-forwarded-host", ""),
        request.headers.get("host", ""),
        request.url.netloc or "",
        request.url.hostname or "",
    ]
    host_header = ""
    for raw in host_candidates:
        cand = (raw or "").split(",")[0].strip()
        if cand:
            host_header = cand
            break
    return f"{proto}://{host_header}"


@app.post("/api/local-auth/login")
async def api_local_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    ip = auth._client_ip(request)
    # ENH-012 / #478 — check both the IP-only bucket AND the
    # (ip, username) bucket. The latter scopes lockout to the actual
    # user being typo'd at, so a corporate-NAT'd office isn't
    # collateral-damaged by one user's bad password.
    auth.rate_limit_check(ip, username)
    with db_conn() as c:
        u = auth.get_user_by_username(c, username)
        # #554 — split the failure cases for clearer operator-facing
        # error messages without disclosing username existence.
        # SECURITY: only specialise the message AFTER a successful
        # password verification; otherwise an attacker could enumerate
        # disabled accounts by probing for the "Account disabled"
        # response without knowing the password.
        password_ok = (
            u is not None
            and u.auth_source == "local"
            and auth.verify_password(password, _get_user_password_hash(c, u.id))
        )
        if not password_ok:
            auth.rate_limit_record_failure(ip, username)
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if u.disabled:
            # Password is verified correct; the user just can't log in.
            # Safe to disclose because we already proved the caller
            # holds the credentials. Use 403 so the SPA's login page
            # can branch on the status code if it ever wants per-case
            # styling. NOTE: we do NOT record a rate-limit failure
            # here — the credentials were CORRECT; the lockout exists
            # to slow down brute-force, not to punish a re-enable
            # attempt by a legitimate user.
            raise HTTPException(
                status_code=403,
                detail="Account is disabled. Contact your administrator.",
            )
        # ----------------------------------------------------------------
        # 2FA gate (#345 TOTP + #381 passkeys). Branches before any
        # session cookie is issued:
        #   (a) user has TOTP enabled OR passkeys enrolled -> respond
        #       200 with step="totp_required" and methods=[...] so the
        #       SPA renders one of (or both) "Authenticator code" /
        #       "Use a passkey" inputs at the second-factor screen.
        #   (b) policy requires 2FA for this role AND user has neither
        #       TOTP nor passkeys -> respond step="totp_setup_required"
        #       (forced TOTP enrolment; passkey-only enrolment-on-login
        #       isn't offered because it requires a roundtrip the
        #       legacy login form can't host).
        #   (c) no 2FA, no requirement -> issue cookie (legacy path).
        # ----------------------------------------------------------------
        policy = _resolve_totp_policy()
        state = auth.get_user_totp_state(c, u.id)
        passkey_count = auth.count_user_credentials(c, u.id)
        # Master-toggle gates (#432). When admin disables a method,
        # treat enrolled credentials of that type as if they don't
        # exist for login purposes — the method drops from `methods`
        # and is skipped in the has_2fa check. The user's enrolment
        # rows stay in the DB so flipping the toggle back on restores
        # the login path. If admin disables BOTH and the user has
        # nothing else, they fall through to single-factor (this is
        # the admin's explicit choice).
        totp_login_enabled = bool(state["enabled"]) and policy["totp_allowed"]
        passkey_login_enabled = (
            passkey_count > 0
            and policy["passkeys_allowed"]
            and webauthn_h.WEBAUTHN_AVAILABLE
        )
        has_2fa = totp_login_enabled or passkey_login_enabled
        # Lockout check happens BEFORE we mint a challenge so a locked
        # user gets a clear 423 rather than a stale challenge_id. Lockout
        # state is TOTP-only for now -- passkeys have their own per-IP
        # rate-limit on webauthn-finish failures. Skip when totp_allowed
        # is off (no point locking out a method we won't honour anyway).
        if totp_login_enabled and state["locked_until"]:
            if state["locked_until"] > int(time.time()):
                retry = state["locked_until"] - int(time.time())
                raise HTTPException(
                    status_code=423,
                    detail=(
                        "Account locked due to too many failed 2FA attempts. "
                        f"Try again in {max(1, retry // 60)} minute(s)."
                    ),
                    headers={"Retry-After": str(retry)},
                )
            # Lockout expired -- clear the state so the next failure
            # starts a fresh counter.
            auth.clear_totp_lockout(c, u.id)
        if has_2fa:
            methods: list[str] = []
            if totp_login_enabled:
                methods.append("totp")
            if passkey_login_enabled:
                methods.append("webauthn")
            cid, exp = _create_totp_challenge({
                "user_id": u.id,
                "kind": "totp_required",
                "ip": ip,
            })
            auth.rate_limit_clear(ip, username)
            return JSONResponse({
                "step": "totp_required",
                "challenge_id": cid,
                "expires_at": exp,
                "username": u.username,
                "methods": methods,
            })
        if policy["totp_allowed"] and _totp_required_for(u.role, policy):
            secret_plain = totp.generate_secret()
            uri = totp.provisioning_uri(secret_plain, u.username)
            cid, exp = _create_totp_challenge({
                "user_id": u.id,
                "kind": "totp_setup_required",
                "secret": secret_plain,
                "ip": ip,
            })
            auth.rate_limit_clear(ip, username)
            return JSONResponse({
                "step": "totp_setup_required",
                "challenge_id": cid,
                "expires_at": exp,
                "username": u.username,
                "secret": secret_plain,
                "provisioning_uri": uri,
            })
        # Legacy single-factor path.
        auth.rate_limit_clear(ip, username)
        auth.touch_last_login(c, u.id)
        cookie_value, expires_at = auth.create_session(
            c, u.id, ip, request.headers.get("user-agent"),
            auth_method="password",
        )
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({"username": u.username, "role": u.role, "source": u.auth_source})
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    # Security event — opt-in via Admin → Notifications. Fire-and-
    # forget via the shared retry helper (#475 / ENH-009) so a
    # transient Apprise blip doesn't drop the audit notification on
    # the floor.
    asyncio.create_task(notify_with_retry(
        f"🔓 {u.username} signed in",
        f"via local from {ip}",
        "info",
        event="user_login",
        actor_username=u.username,
        label=f"user_login (local) {u.username!r}",
    ))
    return resp


@app.post("/api/local-auth/totp")
async def api_local_login_totp(
    request: Request,
    challenge_id: str = Form(...),
    code: str = Form(...),
):
    """Step 2 of the multi-step login for users with TOTP enrolled.

    Verifies the 6-digit TOTP (or a backup code) against the user's
    stored secret, increments the per-user failure counter on miss,
    locks on threshold, and issues the og_session cookie on success.
    """
    ip = auth._client_ip(request)
    auth.rate_limit_check(ip)
    challenge = _peek_totp_challenge(challenge_id)
    if not challenge or challenge.get("kind") != "totp_required":
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    user_id = challenge["user_id"]
    policy = _resolve_totp_policy()
    # Master toggle (#432). When admin disables TOTP, refuse to verify
    # codes from already-enrolled users — defence in depth alongside
    # the api_local_login `methods` filter that already drops 'totp'
    # from the login response. A stale client could still POST here.
    if not policy["totp_allowed"]:
        _consume_totp_challenge(challenge_id)
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=403,
            detail=_err.message_for(_err.AUTH_TOTP_DISABLED_BY_ADMIN),
        )
    with db_conn() as c:
        u = auth.get_user(c, user_id)
        if not u or u.disabled or u.auth_source != "local":
            _consume_totp_challenge(challenge_id)
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=400, detail="User not eligible.")
        state = auth.get_user_totp_state(c, user_id)
        if state["locked_until"] and state["locked_until"] > int(time.time()):
            retry = state["locked_until"] - int(time.time())
            raise HTTPException(
                status_code=423,
                detail=(
                    "Account locked due to too many failed 2FA attempts. "
                    f"Try again in {max(1, retry // 60)} minute(s)."
                ),
                headers={"Retry-After": str(retry)},
            )
        secret_ct = auth.get_user_totp_secret(c, user_id)
        if not secret_ct:
            _consume_totp_challenge(challenge_id)
            raise HTTPException(status_code=400, detail="TOTP not enrolled.")
        try:
            secret_plain = totp.decrypt_secret(secret_ct)
        except Exception as e:
            print(f"[totp] decrypt secret FAILED for user {u.username}: {e}")
            raise HTTPException(status_code=500, detail="TOTP decrypt failed.")
        verified = False
        used_backup = False
        if totp.verify_code(secret_plain, code):
            verified = True
        else:
            matched, new_blob = totp.consume_backup_code(
                state["backup_codes_json"], code,
            )
            if matched:
                verified = True
                used_backup = True
                auth.update_user_totp_backup_codes(c, user_id, new_blob)
        if not verified:
            n, locked = auth.record_totp_failure(
                c, user_id,
                policy["totp_lockout_max_failures"],
                policy["totp_lockout_minutes"] * 60,
            )
            auth.rate_limit_record_failure(ip)
            print(f"[totp] {u.username} verify FAILED ({n}/{policy['totp_lockout_max_failures']})")
            if locked:
                print(f"[totp] {u.username} locked out for {policy['totp_lockout_minutes']}m")
                raise HTTPException(
                    status_code=423,
                    detail=(
                        "Account locked due to too many failed 2FA attempts. "
                        f"Try again in {policy['totp_lockout_minutes']} minute(s)."
                    ),
                )
            raise HTTPException(status_code=401, detail="Invalid code.")
        # Success path -- consume the challenge, clear lockout, issue cookie.
        _consume_totp_challenge(challenge_id)
        auth.clear_totp_lockout(c, user_id)
        auth.rate_limit_clear(ip)
        auth.touch_last_login(c, user_id)
        cookie_value, expires_at = auth.create_session(
            c, user_id, ip, request.headers.get("user-agent"),
            auth_method="totp",
        )
    if used_backup:
        print(f"[totp] {u.username} used backup code")
    else:
        print(f"[totp] {u.username} verified successfully")
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({
        "username": u.username, "role": u.role, "source": u.auth_source,
    })
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    try:
        await notify(
            f"🔓 {u.username} signed in",
            f"via local (2FA) from {ip}",
            "info",
            event="user_login",
            actor_username=u.username,
        )
    except Exception as _e:
        print(f"[notify] user_login (totp) failed: {_e}")
    return resp


@app.post("/api/local-auth/totp-setup-confirm")
async def api_local_login_totp_setup_confirm(
    request: Request,
    challenge_id: str = Form(...),
    code: str = Form(...),
):
    """Step 2 of the multi-step login when policy is forcing enrolment.

    Verifies the freshly-typed 6-digit code against the secret we
    issued in step 1, persists the secret + backup codes, then issues
    the cookie. Returns the 10 plaintext backup codes (one-time reveal).
    """
    ip = auth._client_ip(request)
    auth.rate_limit_check(ip)
    challenge = _peek_totp_challenge(challenge_id)
    if not challenge or challenge.get("kind") != "totp_setup_required":
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    user_id = challenge["user_id"]
    secret_plain = challenge.get("secret") or ""
    if not totp.verify_code(secret_plain, code):
        auth.rate_limit_record_failure(ip)
        raise HTTPException(status_code=401, detail="Invalid code.")
    backup_plain = totp.generate_backup_codes(10)
    encrypted_secret = totp.encrypt_secret(secret_plain)
    encrypted_codes_json = totp.encrypt_backup_codes(backup_plain)
    with db_conn() as c:
        u = auth.get_user(c, user_id)
        if not u or u.disabled or u.auth_source != "local":
            _consume_totp_challenge(challenge_id)
            raise HTTPException(status_code=400, detail="User not eligible.")
        auth.set_user_totp_secret(
            c, user_id, encrypted_secret, encrypted_codes_json,
        )
        _consume_totp_challenge(challenge_id)
        auth.rate_limit_clear(ip)
        auth.touch_last_login(c, user_id)
        cookie_value, expires_at = auth.create_session(
            c, user_id, ip, request.headers.get("user-agent"),
            auth_method="totp",
        )
    print(f"[totp] {u.username} enrolled (forced by policy)")
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({
        "username": u.username, "role": u.role, "source": u.auth_source,
        "backup_codes": backup_plain,
    })
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    try:
        await notify(
            f"🔓 {u.username} signed in",
            f"via local (2FA enrolled) from {ip}",
            "info",
            event="user_login",
            actor_username=u.username,
        )
    except Exception as _e:
        print(f"[notify] user_login (totp setup) failed: {_e}")
    return resp


# ============================================================================
# Login passkey routes (#381). Pair with the existing TOTP routes above —
# both consume the same challenge-id minted in api_local_login. The login
# flow's "second factor" pivots on which method the SPA POSTs back:
# /api/local-auth/totp for a 6-digit code, /api/local-auth/webauthn-* for
# a passkey assertion. CSRF is exempt because the caller doesn't have a
# session cookie yet (auth-optional path).
# ============================================================================
class WebauthnLoginStartIn(BaseModel):
    challenge_id: str


class WebauthnLoginFinishIn(BaseModel):
    challenge_id: str
    credential: dict  # raw PublicKeyCredential JSON from the SPA


@app.post("/api/local-auth/webauthn-start")
async def api_local_login_webauthn_start(
    body: WebauthnLoginStartIn,
    request: Request,
):
    """Step 2A of the multi-step login: hand the SPA a WebAuthn
    challenge to feed into ``navigator.credentials.get()``.

    Reads the user_id from the in-memory TOTP challenge (minted by
    api_local_login). Allows the user to switch between TOTP and
    passkey on the same screen without re-entering the password --
    the challenge_id is shared.

    Returns ``{options: <PublicKeyCredentialRequestOptions>, login_id}``.
    The SPA POSTs the assertion back via webauthn-finish with the
    same login_id.
    """
    if not webauthn_h.WEBAUTHN_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=_err.message_for(_err.AUTH_WEBAUTHN_LIBRARY_MISSING),
        )
    # Master toggle (#432). Defence-in-depth — the SPA won't offer
    # the passkey method when this is off (login response omits
    # 'webauthn' from `methods`), but a stale client could still try.
    if not _resolve_totp_policy()["passkeys_allowed"]:
        raise HTTPException(
            status_code=403,
            detail=_err.message_for(_err.AUTH_PASSKEYS_DISABLED_BY_ADMIN),
        )
    ip = auth._client_ip(request)
    auth.rate_limit_check(ip)
    challenge = _peek_totp_challenge(body.challenge_id)
    if not challenge or challenge.get("kind") != "totp_required":
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    user_id = challenge["user_id"]
    with db_conn() as c:
        u = auth.get_user(c, user_id)
        if not u or u.disabled or u.auth_source != "local":
            raise HTTPException(status_code=400, detail="User not eligible.")
        creds = auth.list_user_credentials(c, user_id)
    if not creds:
        raise HTTPException(
            status_code=400,
            detail="No passkeys enrolled for this account.",
        )
    rp_id = _request_rp_id(request)
    # #605 — detect credentials registered under a different domain.
    # WebAuthn binds credentials to their RP ID; if the operator
    # migrated OmniGrid between domains, stored credentials are still
    # in the DB but the browser correctly refuses to offer them on the
    # new domain — falling through to the QR / hybrid flow with no
    # explanation. Compute the orphaned set so the SPA can surface a
    # clear "re-enrol from Profile" hint above the Passkey button.
    # Empty `rp_id` on a credential row means "registered before this
    # column landed (#605 rollout)" — treat as unknown rather than
    # mismatched so the legacy creds don't fire spurious banners.
    orphaned = []
    matching = []
    for c in creds:
        cred_rp = (c.get("rp_id") or "").strip().lower()
        if cred_rp and cred_rp != rp_id.lower():
            orphaned.append({
                "id": c["id"],
                "friendly_name": c.get("friendly_name") or "",
                "rp_id": cred_rp,
            })
        else:
            matching.append(c)
    rp_id_mismatch = len(orphaned) > 0 and len(matching) == 0
    # Build the assertion options against ALL stored credentials. Even
    # when every credential is orphaned, we still send the options so
    # the browser tries — the spec-correct outcome is still QR-fallback,
    # but the SPA surfaces the banner explaining WHY based on the
    # `rp_id_mismatch` flag below. If at least one matching credential
    # exists, restrict allowCredentials to those so the picker doesn't
    # waste a click on a stale credential.
    _allow_set = matching if matching else creds
    options, raw_challenge = webauthn_h.make_authentication_options(
        rp_id=rp_id,
        allowed_credentials=[
            {
                "credential_id": c["credential_id"],
                "transports": c["transports"],
            }
            for c in _allow_set
        ],
    )
    login_id, expires_at = _create_webauthn_login_challenge({
        "user_id": user_id,
        "challenge_bytes": raw_challenge,
        "rp_id": rp_id,
        "origin": _request_origin(request),
        "ip": ip,
    })
    # #602 — surface the per-credential transports being sent so the
    # operator can grep server logs to verify the assertion-options
    # payload includes 'internal' (without it, macOS Safari/Chrome
    # default to the QR/hybrid flow regardless of `hints`).
    _allow = (options.get("allowCredentials") or []) if isinstance(options, dict) else []
    _transports_summary = [
        {"id_prefix": (c.get("id") or "")[:8], "transports": c.get("transports")}
        for c in _allow
    ]
    print(
        f"[webauthn] {u.username} login-start (rp_id={rp_id}) "
        f"hints={options.get('hints') if isinstance(options, dict) else None} "
        f"allow={_transports_summary}"
    )
    if orphaned:
        print(
            f"[webauthn] {u.username} login-start RP-ID mismatch "
            f"current={rp_id!r} orphaned={[(o['friendly_name'], o['rp_id']) for o in orphaned]} "
            f"matching={len(matching)} (#605)"
        )
    return JSONResponse({
        "options": options,
        "login_id": login_id,
        "expires_at": expires_at,
        "username": u.username,
        # #605 — surface the RP-ID mismatch state so the SPA's login
        # form can render a clear hint instead of letting the browser
        # silently fall through to QR. Only fires when EVERY stored
        # credential's rp_id differs from the current rp_id (any
        # matching cred → operator can still authenticate normally,
        # no banner needed). `orphaned_credentials` lists the
        # friendly names + their original rp_ids for context.
        "rp_id_mismatch": rp_id_mismatch,
        "orphaned_credentials": orphaned,
        "current_rp_id": rp_id,
    })


@app.post("/api/local-auth/webauthn-finish")
async def api_local_login_webauthn_finish(
    body: WebauthnLoginFinishIn,
    request: Request,
):
    """Step 2B: verify the passkey assertion + mint the session cookie.

    Same success path as ``/api/local-auth/totp``: ``touch_last_login``,
    ``create_session``, ``set_session_cookie`` + ``set_csrf_cookie``,
    fire the user_login notification. Failures land in the per-IP
    rate-limit counter so a stolen credential_id can't be brute-forced.
    """
    if not webauthn_h.WEBAUTHN_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Passkey support is not available on this server.",
        )
    ip = auth._client_ip(request)
    auth.rate_limit_check(ip)
    challenge = _peek_webauthn_login_challenge(body.challenge_id)
    if not challenge:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    user_id = challenge["user_id"]
    expected_challenge: bytes = challenge["challenge_bytes"]
    expected_rp_id: str = challenge["rp_id"]
    expected_origin: str = challenge["origin"]
    cred_payload = body.credential or {}
    raw_id = cred_payload.get("rawId") or cred_payload.get("id") or ""
    if not raw_id or not isinstance(raw_id, str):
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400,
            detail="Malformed assertion payload.",
        )
    try:
        credential_id_bytes = webauthn_h.b64u_decode(raw_id)
    except Exception:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Malformed credential id.",
        )
    with db_conn() as c:
        u = auth.get_user(c, user_id)
        if not u or u.disabled or u.auth_source != "local":
            _consume_webauthn_login_challenge(body.challenge_id)
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=400, detail="User not eligible.")
        stored = auth.get_credential_by_credential_id(c, credential_id_bytes)
        if not stored or stored["user_id"] != user_id:
            _consume_webauthn_login_challenge(body.challenge_id)
            auth.rate_limit_record_failure(ip)
            raise HTTPException(
                status_code=401, detail="Unknown credential.",
            )
        try:
            verified = webauthn_h.verify_authentication(
                credential_json=cred_payload,
                expected_challenge=expected_challenge,
                expected_origin=expected_origin,
                expected_rp_id=expected_rp_id,
                public_key=stored["public_key"],
                current_sign_count=stored["sign_count"],
            )
        except Exception as e:
            _consume_webauthn_login_challenge(body.challenge_id)
            auth.rate_limit_record_failure(ip)
            print(f"[webauthn] {u.username} verify FAILED: {e}")
            raise HTTPException(
                status_code=401, detail="Passkey verification failed.",
            )
        # Success path -- consume both challenges, bump sign-count, issue cookie.
        _consume_webauthn_login_challenge(body.challenge_id)
        # Also drop the paired TOTP challenge so the user can't replay
        # it via the TOTP path. We don't know the challenge_id used for
        # webauthn-start (login_id is its own), but the TOTP one was
        # never consumed in webauthn-start -- prune by user_id.
        _prune_totp_challenges()
        for k, v in list(_totp_challenges.items()):
            if v.get("user_id") == user_id and v.get("kind") == "totp_required":
                _totp_challenges.pop(k, None)
        auth.update_credential_after_use(
            c, stored["id"], verified["new_sign_count"],
        )
        auth.rate_limit_clear(ip)
        auth.touch_last_login(c, user_id)
        cookie_value, expires_at = auth.create_session(
            c, user_id, ip, request.headers.get("user-agent"),
            auth_method="passkey",
        )
    print(f"[webauthn] {u.username} verified successfully (cred {stored['id']})")
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({
        "username": u.username, "role": u.role, "source": u.auth_source,
    })
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    try:
        await notify(
            f"🔓 {u.username} signed in",
            f"via local (passkey) from {ip}",
            "info",
            event="user_login",
            actor_username=u.username,
        )
    except Exception as _e:
        print(f"[notify] user_login (webauthn) failed: {_e}")
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
            auth_method="bootstrap",
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
        # Client-side runtime knobs — read once on init, applied to the
        # next poll iteration. Resolved per-request so an Admin → Config
        # save takes effect on the next /api/me round-trip without a
        # page reload. Add new client-tunables here rather than via a
        # separate endpoint.
        "client_config": {
            # Tunable is stored as integer seconds (#514) for operator-
            # friendly UI; multiply by 1000 here so the SPA's setTimeout
            # consumer keeps its existing ms-based contract. Renaming
            # the SPA field would touch every call site for no gain.
            "ops_poll_ms": tuning.tuning_int("tuning_ops_poll_interval_seconds") * 1000,
            # #506 — SPA's loadHosts() reads this and uses it as the cap on
            # parallel /api/hosts/one/<id> calls during fan-out. Resolved
            # per /api/me round-trip so an Admin → Config save takes
            # effect on the next call.
            "hosts_parallel_fetch": tuning.tuning_int("tuning_hosts_parallel_fetch"),
            # #541 — SSE freshness-watchdog idle threshold. Stored as
            # seconds; SPA's `_sseIdleThresholdMs` consumer wants ms.
            "sse_idle_threshold_ms": tuning.tuning_int("tuning_sse_idle_threshold_seconds") * 1000,
            # #542 — pollOps SSE-up keep-alive cadence. Same ms-conversion
            # pattern as ops_poll_ms (#514) and sse_idle_threshold_ms.
            "pollops_sse_keepalive_ms": tuning.tuning_int("tuning_pollops_sse_keepalive_seconds") * 1000,
            # Scheduler-tz state so the admin Schedules tab can badge
            # "TZ: <name> → falling back to UTC" when the operator typed
            # an invalid IANA name. ``configured`` = raw setting,
            # ``resolved`` = active TZ (None on blank or invalid),
            # ``fallback`` = True only when configured was non-empty
            # but ZoneInfo rejected it.
            "scheduler_tz": schedules.scheduler_tz_state(),
            # #596 — per-provider chip colours. Hex string per provider,
            # falls back to the SPA's built-in default when the operator
            # hasn't customised. Read once on /api/me and applied to the
            # provider chip via inline `:style` (--chip-bg/-br/-fg).
            "provider_colors": {
                "beszel":        get_setting("provider_color_beszel", "")        or "",
                "pulse":         get_setting("provider_color_pulse", "")         or "",
                "node_exporter": get_setting("provider_color_node_exporter", "") or "",
                "webmin":        get_setting("provider_color_webmin", "")        or "",
                "ping":          get_setting("provider_color_ping", "")          or "",
            },
        },
    }
    # ARCH-004: surface the SESSION_SECRET-auto-generated state to admins.
    # When SESSION_SECRET isn't set in the env, logic/auth.py generates an
    # ephemeral one at boot — every container restart invalidates every
    # session. Today the only signal is a one-line print at boot, buried
    # in logs. Exposing this boolean lets the SPA render a dismissible
    # warning banner so operators know their sessions die on every redeploy.
    # Boolean only (no message string) — i18n surface lives in en.json.
    # Always included so the SPA can also clear a stale "dismissed" flag
    # once SESSION_SECRET is finally set in the env.
    out["session_secret_auto"] = (auth.auto_secret_warning() is not None)
    # UX-004 / #370 — bootstrap admin env vars still set in `.env` AFTER the
    # users table has been seeded. The bootstrap path is then a harmless
    # no-op on every restart, but two operational risks remain: (a) wiping
    # the DB and restarting would silently re-seed an admin from the env
    # values (surprise), (b) the password is sitting plaintext in `.env`.
    # Surfacing this boolean lets the SPA show a dismissible banner so
    # the operator clears the env vars before the next deploy.
    if BOOTSTRAP_ADMIN_USER and BOOTSTRAP_ADMIN_PASSWORD:
        with db_conn() as _bc:
            _user_n = auth.count_users(_bc)
        out["bootstrap_env_still_set"] = (_user_n > 0)
    else:
        out["bootstrap_env_still_set"] = False
    if profile:
        out.update({
            "id":           profile["id"],
            "email":        profile.get("email") or "",
            "display_name": profile.get("display_name") or "",
            "bio":          profile.get("bio") or "",
            "created_at":   profile.get("created_at"),
            "last_login_at": profile.get("last_login_at"),
            "avatar_url":   f"/api/avatars/{profile['avatar_path']}" if profile.get("avatar_path") else None,
            # Per-user UI prefs (#313). JSON dict — currently carries
            # `headerWeatherEnabled` / `headerClockEnabled` so toggling
            # them on desktop survives the trip to iPhone (or any other
            # browser) for the same login. Empty `{}` for users who've
            # never set anything; SPA falls back to its own per-toggle
            # defaults in that case.
            "ui_prefs":     profile.get("ui_prefs") or {},
        })
        # Per-user notification opt-in map (#357). Two-layer scoping:
        # the admin gate is shared via ``notify_events_admin`` so the
        # SPA can grey out toggles for events admin has globally
        # disabled; ``notify_events`` is the user's own resolved map
        # (defaults to admin state until the user opts out).
        admin_map = {
            name: get_setting_bool(
                f"notify_event_{name}", _NOTIFY_EVENT_DEFAULTS[name],
            )
            for name in _NOTIFY_EVENT_NAMES
        }
        with db_conn() as _c:
            user_prefs = auth.get_user_notify_prefs(_c, profile["id"])
        resolved = {
            name: bool(user_prefs[name]) if name in user_prefs else admin_map[name]
            for name in _NOTIFY_EVENT_NAMES
        }
        out["notify_events"] = resolved
        out["notify_events_admin"] = admin_map
        # TOTP / 2FA summary (#345). Surfaced on /api/me so the SPA can
        # render the Profile section + the "Required by policy" banner
        # without a follow-up round-trip on every page load. Detailed
        # backup-codes payload still ships separately via /api/me/totp.
        _totp_policy = _resolve_totp_policy()
        with db_conn() as _c2:
            _totp_state = auth.get_user_totp_state(_c2, profile["id"])
            _passkey_count = auth.count_user_credentials(_c2, profile["id"])
        out["totp"] = {
            "enabled":  bool(_totp_state["enabled"]),
            "allowed":  bool(_totp_policy["totp_allowed"]),
            "required": (
                user.auth_source == "local"
                and _totp_required_for(user.role, _totp_policy)
            ),
        }
        # Passkeys (#381). The SPA uses ``count`` as a quick hint
        # (e.g. show "+ Add passkey" when 0; show the list inline when
        # >0) without the full /api/me/webauthn round-trip. ``supported``
        # is the server-side capability flag (False when the webauthn
        # library is missing).
        out["passkeys"] = {
            "count": int(_passkey_count),
            "supported": (
                user.auth_source == "local"
                and webauthn_h.WEBAUTHN_AVAILABLE
            ),
            # Admin master toggle (#432). When false, the SPA hides /
            # disables the "Add a passkey" button. Existing enrolments
            # remain visible + login-eligible until each user revokes.
            "allowed": bool(_totp_policy["passkeys_allowed"]),
        }
    return out


class UiPrefsIn(BaseModel):
    """Partial-update payload for PATCH /api/me/ui-prefs.

    Free-form dict — keys are SPA-defined (e.g. headerWeatherEnabled).
    Send `null` for a key to delete it from the stored prefs (so the
    SPA falls back to its default).
    """
    prefs: dict


@app.patch("/api/me/ui-prefs")
async def api_me_ui_prefs(body: UiPrefsIn, request: Request):
    """Merge `body.prefs` into the calling user's `ui_prefs`.

    Auth required (cookie or token). API-token "users" (negative ids)
    can't store prefs — return 400. Returns the merged prefs so the
    SPA can confirm what's persisted.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Authentication required")
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot store UI prefs")
    with db_conn() as c:
        merged = auth.update_ui_prefs(c, user.id, body.prefs)
    return {"ui_prefs": merged}


class UserNotifyPrefsIn(BaseModel):
    """Partial-update payload for PATCH /api/me/notify-prefs (#357).

    Each of the 13 fields is Optional[bool]; ``None`` means "leave
    unchanged", ``True`` / ``False`` set the per-user opt-in state.
    The backend rejects any attempt to opt INTO an event the admin
    has globally disabled (the data model only meaningfully scopes
    DOWN from the admin layer).
    """
    stack_update_success: Optional[bool] = None
    stack_update_failure: Optional[bool] = None
    container_update_success: Optional[bool] = None
    container_update_failure: Optional[bool] = None
    container_restart_success: Optional[bool] = None
    container_restart_failure: Optional[bool] = None
    container_remove_success: Optional[bool] = None
    container_remove_failure: Optional[bool] = None
    service_restart_success: Optional[bool] = None
    service_restart_failure: Optional[bool] = None
    prune_success: Optional[bool] = None
    prune_failure: Optional[bool] = None
    user_login: Optional[bool] = None


@app.patch("/api/me/notify-prefs")
async def api_me_notify_prefs(
    body: UserNotifyPrefsIn,
    user: auth.User = Depends(auth.current_user),
):
    """Per-user opt-in/out for the 13 notification events (#357).

    Layered ON TOP of the admin-side ``notify_event_*`` gates: a
    notification fires only when (admin enabled) AND (user opted-in,
    or hasn't expressed a pref → defaults to admin state). Refuses to
    set a pref to True for an event admin has disabled — the model
    only narrows DOWN from the admin layer.

    API-token "users" (negative ids) can't store prefs.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot store notify prefs")
    payload = body.dict(exclude_unset=True)
    # Admin gate snapshot — refuse opt-IN for admin-disabled events.
    admin_map = {
        name: get_setting_bool(
            f"notify_event_{name}", _NOTIFY_EVENT_DEFAULTS[name],
        )
        for name in _NOTIFY_EVENT_NAMES
    }
    for name, value in payload.items():
        if value is True and admin_map.get(name) is False:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Event '{name}' is disabled by admin; "
                    "cannot enable per-user."
                ),
            )
    # Read-modify-write so unspecified events keep their stored value.
    with db_conn() as c:
        current = auth.get_user_notify_prefs(c, user.id)
        merged = dict(current)
        for name, value in payload.items():
            if value is None:
                continue
            merged[name] = bool(value)
        auth.set_user_notify_prefs(c, user.id, merged)
    # Resolved response shape mirrors api_get_me's ``notify_events``
    # block so the SPA can drop it straight into state.
    resolved = {
        name: (
            bool(merged[name]) if name in merged else admin_map[name]
        )
        for name in _NOTIFY_EVENT_NAMES
    }
    return {
        "notify_events": resolved,
        "notify_events_admin": admin_map,
    }


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
# Profile -> Two-factor authentication (TOTP) — #345.
# ============================================================================
class TotpEnrollConfirmIn(BaseModel):
    secret: str
    code: str


class TotpDisableIn(BaseModel):
    password: str


def _totp_authentik_guard(user: auth.User) -> None:
    if user.auth_source == "authentik":
        raise HTTPException(
            status_code=400,
            detail="Authentik users manage 2FA in their IdP.",
        )


def _totp_required_for_user(user: auth.User) -> bool:
    """Convenience wrapper around _totp_required_for() given a User.

    Honours the global role-based policy (#345) AND the per-user
    `totp_force_required` admin override (#376). Either one is enough
    to require 2FA for this user. Authentik users always return False
    here — their auth_source short-circuits TOTP at the call sites.
    """
    if getattr(user, "auth_source", "local") != "local":
        return False
    if getattr(user, "totp_force_required", False):
        return True
    return _totp_required_for(user.role)


@app.get("/api/me/totp")
async def api_me_totp_status(user: auth.User = Depends(auth.current_user)):
    """Return the caller's 2FA status + decrypted backup codes.

    Backup codes are returned in plaintext (with a ``used_at`` flag per
    code) so the Profile page can render them under a hide/unhide
    eye toggle. Authentik users get a short-circuited reply that the
    SPA renders as "managed by IdP". API tokens (negative id) get 400.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    policy = _resolve_totp_policy()
    if user.auth_source == "authentik":
        return {
            "auth_source": user.auth_source,
            "allowed": False,
            "enabled": False,
            "required": False,
            "backup_codes": [],
            "policy": policy,
        }
    with db_conn() as c:
        state = auth.get_user_totp_state(c, user.id)
    codes = totp.decrypt_backup_codes(state["backup_codes_json"])
    return {
        "auth_source": user.auth_source,
        "allowed": bool(policy["totp_allowed"]),
        "enabled": bool(state["enabled"]),
        "required": _totp_required_for_user(user),
        "backup_codes": codes,
        "policy": policy,
    }


@app.post("/api/me/totp/enroll-start")
async def api_me_totp_enroll_start(user: auth.User = Depends(auth.current_user)):
    """Generate a fresh secret + provisioning_uri for the caller.

    The secret is NOT persisted at this stage -- the SPA echoes it back
    via /api/me/totp/enroll-confirm so the user proves they captured
    it correctly before we lock it in.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    _totp_authentik_guard(user)
    policy = _resolve_totp_policy()
    if not policy["totp_allowed"]:
        raise HTTPException(
            403, "Two-factor authentication is disabled by admin policy.",
        )
    secret_plain = totp.generate_secret()
    uri = totp.provisioning_uri(secret_plain, user.username)
    print(f"[totp] {user.username} enroll-start (secret prepared, awaiting confirm)")
    return {
        "secret": secret_plain,
        "provisioning_uri": uri,
        "username": user.username,
        "issuer": "OmniGrid",
    }


@app.post("/api/me/totp/enroll-confirm")
async def api_me_totp_enroll_confirm(
    body: TotpEnrollConfirmIn,
    user: auth.User = Depends(auth.current_user),
):
    """Persist the secret + generate backup codes after a successful
    verification.

    Returns the 10 plaintext backup codes ONCE in this response. The
    Profile page also keeps them recoverable via /api/me/totp afterwards
    (encrypted at rest with the same Fernet key).
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    _totp_authentik_guard(user)
    policy = _resolve_totp_policy()
    if not policy["totp_allowed"]:
        raise HTTPException(
            403, "Two-factor authentication is disabled by admin policy.",
        )
    if not body.secret or len(body.secret) < 16:
        raise HTTPException(400, "Missing or malformed secret.")
    if not totp.verify_code(body.secret, body.code):
        raise HTTPException(401, "Invalid verification code.")
    backup_plain = totp.generate_backup_codes(10)
    encrypted_secret = totp.encrypt_secret(body.secret)
    encrypted_codes_json = totp.encrypt_backup_codes(backup_plain)
    with db_conn() as c:
        auth.set_user_totp_secret(
            c, user.id, encrypted_secret, encrypted_codes_json,
        )
    print(f"[totp] {user.username} enrolled")
    return {
        "ok": True,
        "backup_codes": backup_plain,
    }


@app.post("/api/me/totp/regenerate-codes")
async def api_me_totp_regenerate_codes(
    user: auth.User = Depends(auth.current_user),
):
    """Replace the backup codes with a fresh batch of 10. Existing
    codes are discarded (used + unused alike). One-time reveal of the
    new plaintext list."""
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    _totp_authentik_guard(user)
    with db_conn() as c:
        state = auth.get_user_totp_state(c, user.id)
        if not state["enabled"]:
            raise HTTPException(400, "Two-factor authentication is not enabled.")
        backup_plain = totp.generate_backup_codes(10)
        encrypted = totp.encrypt_backup_codes(backup_plain)
        auth.update_user_totp_backup_codes(c, user.id, encrypted)
    print(f"[totp] {user.username} regenerated backup codes")
    return {"ok": True, "backup_codes": backup_plain}


@app.post("/api/me/totp/disable")
async def api_me_totp_disable(
    body: TotpDisableIn,
    user: auth.User = Depends(auth.current_user),
):
    """Self-disable 2FA after re-confirming the password.

    Refused when the admin policy currently requires TOTP for the
    user's role -- the operator must lift the policy first OR an
    admin must override. Authentik users 400.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    _totp_authentik_guard(user)
    # 2FA is satisfied if EITHER TOTP OR a passkey is enrolled (#381). So
    # a user with a passkey can self-disable TOTP even when policy
    # requires 2FA. Block ONLY when removing TOTP would leave the user
    # with no 2FA at all under a required-2FA policy.
    if _totp_required_for_user(user):
        with db_conn() as c:
            passkeys = auth.count_user_credentials(c, user.id)
        if passkeys == 0:
            raise HTTPException(
                403,
                "Admin policy requires 2FA for your role; "
                "enrol a passkey first or ask an admin to lift the policy.",
            )
    with db_conn() as c:
        stored = _get_user_password_hash(c, user.id)
        if not auth.verify_password(body.password, stored):
            raise HTTPException(401, "Current password is incorrect.")
        auth.clear_user_totp(c, user.id)
    print(f"[totp] {user.username} disabled")
    return {"ok": True}


# ============================================================================
# Profile -> WebAuthn / passkey management (#381). Cookie-authed; CSRF
# enforced globally by the middleware. Authentik users 400 (their IdP
# manages MFA). API-token "users" (negative ids) 400.
# ============================================================================
class WebauthnRegisterStartIn(BaseModel):
    """Empty body -- the route reads username + user_id from the
    session. Kept as a model for future fields (e.g. preferred
    transports filter)."""
    pass


class WebauthnRegisterFinishIn(BaseModel):
    credential: dict
    friendly_name: Optional[str] = None


def _webauthn_self_guard(user: auth.User) -> None:
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage passkeys")
    if user.auth_source == "authentik":
        raise HTTPException(
            status_code=400,
            detail="Authentik users manage 2FA in their IdP.",
        )
    if not webauthn_h.WEBAUTHN_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=_err.message_for(_err.AUTH_WEBAUTHN_LIBRARY_MISSING),
        )


class WebauthnClientErrorIn(BaseModel):
    """Body for /api/me/webauthn/client-error — the SPA POSTs this when
    `navigator.credentials.create()` or `.get()` rejects with a
    DOMException so the failure reason lands in Admin → Logs (#433).
    Fields are all best-effort strings; capped server-side to keep a
    misbehaving client from spamming the buffer.
    """
    phase: Optional[str] = None        # "register" | "login"
    error_name: Optional[str] = None   # DOMException.name
    error_message: Optional[str] = None
    rp_id: Optional[str] = None
    origin: Optional[str] = None


@app.post("/api/me/webauthn/client-error")
async def api_me_webauthn_client_error(
    body: WebauthnClientErrorIn,
    request: Request,
    user: auth.User = Depends(auth.current_user),
):
    """Surface a client-side WebAuthn ceremony failure into the server
    log buffer. Pure logging — no DB write, no state change. Caps each
    field at 200 chars so a flooding client can't spam the ring."""
    def _trim(s: Optional[str]) -> str:
        s = (s or "").strip()
        return s[:200]
    phase    = _trim(body.phase) or "?"
    err_name = _trim(body.error_name) or "?"
    err_msg  = _trim(body.error_message)
    rp_id    = _trim(body.rp_id) or _request_rp_id(request)
    origin   = _trim(body.origin) or _request_origin(request)
    server_origin = _request_origin(request)
    server_rp_id  = _request_rp_id(request)
    msg = (
        f"[webauthn] CLIENT ERROR — user={user.username} phase={phase} "
        f"error_name={err_name}"
    )
    if err_msg:
        msg += f" error_message={err_msg!r}"
    msg += (
        f" client_rp_id={rp_id} client_origin={origin} "
        f"server_rp_id={server_rp_id} server_origin={server_origin}"
    )
    print(msg)
    return {"ok": True}


@app.get("/api/me/webauthn")
async def api_me_webauthn_list(
    request: Request,
    user: auth.User = Depends(auth.current_user),
):
    """Return every passkey enrolled for the caller.

    Each row is shaped ``{id, friendly_name, transports, created_at,
    last_used_at, sign_count, credential_id, rp_id}`` -- credential_id is
    base64url for display purposes only (stable identifier for the
    revoke button). public_key never leaves the server.

    ``rp_id`` (#605) lets the SPA flag credentials registered under a
    different domain (orphaned passkeys that the browser will refuse
    to offer at login). Profile → Security renders an inline badge
    when ``pk.rp_id !== current_rp_id``.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage passkeys")
    if user.auth_source == "authentik":
        return {"auth_source": user.auth_source, "supported": False, "credentials": []}
    if not webauthn_h.WEBAUTHN_AVAILABLE:
        return {
            "auth_source": user.auth_source,
            "supported": False,
            "credentials": [],
            "error": "webauthn library not installed on the server.",
        }
    with db_conn() as c:
        rows = auth.list_user_credentials(c, user.id)
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "credential_id": webauthn_h.b64u_encode(r["credential_id"]),
            "friendly_name": r["friendly_name"],
            "transports": r["transports"],
            "created_at": r["created_at"],
            "last_used_at": r["last_used_at"],
            "sign_count": r["sign_count"],
            "rp_id": r.get("rp_id", "") or "",
        })
    return {
        "auth_source": user.auth_source,
        "supported": True,
        "credentials": out,
        # #605 — current effective rp_id so the SPA can compare each
        # credential's rp_id against the live page's domain WITHOUT
        # the SPA having to re-derive it (the SPA's `location.hostname`
        # would skip X-Forwarded-Host edge cases that `_request_rp_id`
        # handles).
        "current_rp_id": _request_rp_id(request),
    }


@app.post("/api/me/webauthn/register-start")
async def api_me_webauthn_register_start(
    request: Request,
    user: auth.User = Depends(auth.current_user),
):
    """Hand the SPA ``PublicKeyCredentialCreationOptions``.

    The challenge is stashed in-memory keyed by user_id (5-min TTL).
    The SPA echoes back the authenticator response via register-finish
    -- if the user starts a second enrolment without finishing the
    first, the challenge is overwritten (last-wins; safe -- challenges
    are per-user and not consumable across users).
    """
    _webauthn_self_guard(user)
    # Admin master toggle (#432). Only register-start is gated — list /
    # revoke / login still work for already-enrolled keys, mirroring
    # the totp_allowed shape (admin can flip enrolment off without
    # breaking active logins).
    if not _resolve_totp_policy()["passkeys_allowed"]:
        raise HTTPException(
            status_code=403,
            detail=_err.message_for(_err.AUTH_PASSKEYS_DISABLED_BY_ADMIN),
        )
    rp_id = _request_rp_id(request)
    rp_name = "OmniGrid"
    with db_conn() as c:
        creds = auth.list_user_credentials(c, user.id)
    existing_ids = [c["credential_id"] for c in creds]
    # WebAuthn user-handle: 1..64 bytes, opaque to the RP. Use the
    # numeric user id as a left-padded 4-byte blob -- stable per user,
    # never leaks PII.
    user_handle = f"omnigrid-user-{user.id}".encode("utf-8")
    options, raw_challenge = webauthn_h.make_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=user_handle,
        username=user.username,
        display_name=user.username,
        existing_credential_ids=existing_ids,
    )
    expires_at = _set_webauthn_register_challenge(user.id, {
        "challenge_bytes": raw_challenge,
        "rp_id": rp_id,
        "origin": _request_origin(request),
    })
    print(
        f"[webauthn] {user.username} register-start "
        f"(rp_id={rp_id}, origin={_request_origin(request)})"
    )
    return {
        "options": options,
        "expires_at": expires_at,
        "rp_id": rp_id,
    }


@app.post("/api/me/webauthn/register-finish")
async def api_me_webauthn_register_finish(
    body: WebauthnRegisterFinishIn,
    request: Request,
    user: auth.User = Depends(auth.current_user),
):
    """Verify the attestation + persist the new credential row.

    Friendly name validation: 0-64 visible chars; empty -> default
    "Passkey N" where N = (existing count + 1) so the operator gets
    a sensible label even when the SPA forgot to prompt.
    """
    _webauthn_self_guard(user)
    state = _consume_webauthn_register_challenge(user.id)
    if not state:
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    cred_payload = body.credential or {}
    if not isinstance(cred_payload, dict):
        raise HTTPException(
            status_code=400, detail="Malformed credential payload.",
        )
    try:
        result = webauthn_h.verify_registration(
            credential_json=cred_payload,
            expected_challenge=state["challenge_bytes"],
            expected_origin=state["origin"],
            expected_rp_id=state["rp_id"],
        )
    except Exception as e:
        print(f"[webauthn] {user.username} register verify FAILED: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Could not verify passkey: {e}",
        )
    try:
        friendly = webauthn_h.validate_friendly_name(body.friendly_name or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with db_conn() as c:
        existing = auth.list_user_credentials(c, user.id)
        if not friendly:
            friendly = f"Passkey {len(existing) + 1}"
        # Duplicate check (UNIQUE on credential_id catches it too --
        # mapped to 409 here for the friendlier shape).
        for r in existing:
            if r["credential_id"] == result["credential_id"]:
                raise HTTPException(
                    status_code=409,
                    detail="This passkey is already enrolled.",
                )
        try:
            row_id = auth.add_user_credential(
                c,
                user_id=user.id,
                credential_id=result["credential_id"],
                public_key=result["public_key"],
                sign_count=result["sign_count"],
                transports=result["transports"],
                friendly_name=friendly,
                # #605 — stamp the rp_id this credential was registered
                # under so login can detect "credential registered under
                # a different domain" later. ``state["rp_id"]`` came
                # from `_request_rp_id(request)` at register-start
                # time, so it tracks the effective hostname the user
                # was on when they enrolled.
                rp_id=state.get("rp_id", "") or "",
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="This passkey is already enrolled.",
            )
    print(f"[webauthn] {user.username} enrolled passkey "
          f"id={row_id} name={friendly!r}")
    return {
        "ok": True,
        "id": row_id,
        "friendly_name": friendly,
    }


@app.delete("/api/me/webauthn/{credential_row_id}")
async def api_me_webauthn_delete(
    credential_row_id: int,
    user: auth.User = Depends(auth.current_user),
):
    """Revoke ONE passkey owned by the caller.

    The DB delete is gated on ``(user_id, id)`` so passing another
    user's credential id 404s instead of revoking it.
    """
    _webauthn_self_guard(user)
    with db_conn() as c:
        ok = auth.delete_user_credential(c, user.id, credential_row_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Passkey not found.")
    print(f"[webauthn] {user.username} revoked passkey id={credential_row_id}")
    return {"ok": True}


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
        # Capture the avatar path BEFORE the delete so we can unlink
        # the file on disk afterwards. Without this the file lingers
        # under /app/data/avatars/ and a recycled user-id (rare —
        # autoincrement reset / restore-from-backup) would silently
        # inherit the orphan. BUG-008 in the code review.
        profile = auth.get_user_profile(c, user_id) or {}
        avatar_path = (profile.get("avatar_path") or "").strip()
        auth.delete_user(c, user_id)
    if avatar_path:
        try:
            full = os.path.join(_AVATAR_DIR, avatar_path)
            if os.path.exists(full):
                os.remove(full)
        except OSError:
            pass  # best-effort cleanup; the orphan is cosmetic
    return {"ok": True}


@app.post("/api/users/{user_id}/reset-password")
async def api_reset_password(
    user_id: int,
    r: PasswordResetIn,
    _admin: auth.User = Depends(auth.require_admin),
):
    """Admin password-reset for a local user.

    Note: this ALSO clears any TOTP enrolment (#345). Operators reset
    passwords when a user has lost access; that usually means their
    authenticator device is gone too. The user re-enrols via Profile
    after the next login if 2FA is still required by policy.
    """
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


@app.post("/api/users/{user_id}/disable-totp")
async def api_admin_disable_totp(
    user_id: int,
    request: Request,
    admin: auth.User = Depends(auth.require_admin),
):
    """Admin override: clear a user's TOTP enrolment + lockout state.

    Useful when a user has lost their authenticator device. The user
    re-enrols via Profile on the next login if policy still requires
    2FA for their role. Audited via the history table with
    op_type='totp_admin_disabled'.
    """
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target.auth_source != "local":
            raise HTTPException(
                status_code=400,
                detail="Authentik users manage 2FA in their IdP.",
            )
        state = auth.get_user_totp_state(c, user_id)
        if not state["enabled"]:
            return {"ok": True, "already_disabled": True}
        auth.clear_user_totp(c, user_id)
        # Audit row -- mirrors the ssh_run pattern above.
        try:
            c.execute(
                "INSERT INTO history "
                "(ts, op_type, target_name, target_id, target_stack, "
                " status, duration, events, error, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(), "totp_admin_disabled",
                    target.username, str(user_id), None,
                    "success", 0.0,
                    json.dumps([{
                        "ts": time.time(), "level": "info",
                        "msg": f"2FA disabled for {target.username} by {admin.username}",
                    }]),
                    None, admin.username,
                ),
            )
        except Exception as e:
            print(f"[totp] audit-log insert failed: {e}")
    print(f"[totp] {target.username} disabled BY ADMIN ({admin.username})")
    return {"ok": True}


class TotpForceIn(BaseModel):
    force: bool


@app.post("/api/users/{user_id}/totp-force")
async def api_admin_totp_force(
    user_id: int,
    body: TotpForceIn,
    admin: auth.User = Depends(auth.require_admin),
):
    """Admin override: per-user force-2FA flag (#376).

    Layers ON TOP of the global totp_required_for_admins / _users
    policy — flipping this ON forces 2FA for THIS user even when
    the global policy doesn't require it for their role. Forcing
    OFF reverts to whatever the global policy says (if global policy
    requires 2FA for the role, the user still has to use it).

    Forcing 2FA on a user who hasn't enrolled yet causes their next
    login to land in the forced-enrolment QR flow — already handled
    by api_local_login's multi-step path.

    Audited via the history table with op_type='totp_force_set'.
    """
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target.auth_source != "local":
            raise HTTPException(
                status_code=400,
                detail="Authentik users manage 2FA in their IdP.",
            )
        if bool(target.totp_force_required) == bool(body.force):
            return {"ok": True, "force_required": bool(body.force), "no_change": True}
        auth.set_user_totp_force_required(c, user_id, bool(body.force))
        try:
            c.execute(
                "INSERT INTO history "
                "(ts, op_type, target_name, target_id, target_stack, "
                " status, duration, events, error, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(), "totp_force_set",
                    target.username, str(user_id), None,
                    "success", 0.0,
                    json.dumps([{
                        "ts": time.time(), "level": "info",
                        "msg": (
                            f"2FA force-required {'enabled' if body.force else 'cleared'} "
                            f"for {target.username} by {admin.username}"
                        ),
                    }]),
                    None, admin.username,
                ),
            )
        except Exception as e:
            print(f"[totp] audit-log insert failed: {e}")
    print(
        f"[totp] {target.username} force-2FA "
        f"{'ENABLED' if body.force else 'CLEARED'} BY ADMIN ({admin.username})"
    )
    return {"ok": True, "force_required": bool(body.force)}


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
    search: str = "",
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

    Optional ``search`` param does a case-insensitive substring
    match on ``target_name`` / ``op_type`` / ``status``. Backend
    filtering keeps the page count accurate when the operator is
    searching across thousands of rows.
    """
    # Build a reusable WHERE-clause + bind args. Backend search lives
    # entirely in SQL so the page count + slice are correct against
    # the filtered set, not the unfiltered total.
    actor = schedules.SCHEDULER_ACTOR
    where = "actor = ?"
    args: list = [actor]
    s = (search or "").strip().lower()
    if s:
        where += (" AND ("
                  "LOWER(COALESCE(target_name, '')) LIKE ? OR "
                  "LOWER(COALESCE(op_type, '')) LIKE ? OR "
                  "LOWER(COALESCE(status, '')) LIKE ?"
                  ")")
        like = f"%{s}%"
        args.extend([like, like, like])

    # Legacy single-query path — keep until every caller is migrated.
    if page_size <= 0:
        limit = max(1, min(int(limit), 500))
        with db_conn() as c:
            rows = c.execute(
                f"SELECT * FROM history WHERE {where} "
                f"ORDER BY ts DESC LIMIT ?",
                args + [limit],
            ).fetchall()
        return {"queue": [dict(r) for r in rows]}

    # Paginated path — count + slice. Cap page_size at 100 to guard
    # against accidentally-huge queries.
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    offset = (page - 1) * page_size
    with db_conn() as c:
        total_row = c.execute(
            f"SELECT COUNT(*) FROM history WHERE {where}", args,
        ).fetchone()
        total = int((total_row[0] if total_row else 0) or 0)
        rows = c.execute(
            f"SELECT * FROM history WHERE {where} "
            f"ORDER BY ts DESC LIMIT ? OFFSET ?",
            args + [page_size, offset],
        ).fetchall()
    return {
        "queue":     [dict(r) for r in rows],
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "pages":     max(1, (total + page_size - 1) // page_size),
        "search":    search or "",
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
    # Cache-Control: no-cache, must-revalidate — the SPA shell is the
    # entry point that references EVERY versioned asset (`/js/app.js?v=...`,
    # `/css/style.css`, the inline `window.__APP_VERSION__` global), so a
    # browser-cached shell would freeze the whole asset chain at a stale
    # PATCH and the `?v=` bust scheme falls apart. `no-cache` doesn't
    # disable caching — it forces revalidation on every navigation so a
    # 304 is allowed when nothing changed; only the body bytes are
    # skipped, the headers (including the freshly-substituted version)
    # are re-served. Safe for the SPA shell; do NOT copy onto static
    # assets (they SHOULD cache by the URL-versioning contract).
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


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


# Serve node_modules directly — but only the specific files that
# index.html / login.html / alpine-gate.js actually reference.
# Earlier this was a wildcard `app.mount("/node_modules", StaticFiles(...))`
# which served EVERY file in the tree (readmes, sourcemaps, TS sources,
# unused locales, package metadata) even though only ~7 files are
# actually requested. ARCH-003 in the code review flagged this as
# unnecessary surface bloat — not a security hole (the files are public
# on npm anyway) but tidy + faster to audit.
#
# Adding a new dep that needs serving = add its path to _NPM_ALLOWED.
# Anything outside the allowlist 404s; anything inside is served
# straight from the on-disk file with the correct media-type.
_NPM_ALLOWED: Set[str] = {
    "@tailwindcss/browser/dist/index.global.js",
    "alpinejs/dist/cdn.min.js",
    "sweetalert2/dist/sweetalert2.all.min.js",
    "@xterm/xterm/css/xterm.css",
    "@xterm/xterm/lib/xterm.js",
    "@xterm/addon-fit/lib/addon-fit.js",
    "@xterm/addon-web-links/lib/addon-web-links.js",
    "qrcode-generator/dist/qrcode.js",
}


@app.get("/node_modules/{path:path}")
async def api_node_modules(path: str):
    """Allowlist-gated static server for the 7 npm files the SPA actually
    uses. Everything else returns 404 — keeps the served surface tight.
    """
    # Path-traversal guard: no `..` segments, no leading slashes, must
    # match an entry in the allowlist exactly. Belt-and-braces — FastAPI's
    # path converter wouldn't let `..` through in practice, but the
    # explicit check makes the security property obvious.
    if ".." in path or path.startswith("/") or path not in _NPM_ALLOWED:
        raise HTTPException(404, "Not found")
    file_path = os.path.join("node_modules", path)
    if not os.path.isfile(file_path):
        raise HTTPException(404, "Not found")
    return FileResponse(file_path)


# Translation bundles. Mounted at /i18n/ (before the "/" catch-all, same
# ordering rule as /metrics / /node_modules) so the SPA can fetch
# /i18n/en.json, /i18n/ar.json, /i18n/index.json at boot. Anonymous-
# readable: language files are UI strings, not secrets.
if os.path.isdir("static/i18n"):
    app.mount("/i18n", StaticFiles(directory="static/i18n"), name="i18n")


# Keep this line LAST — StaticFiles at "/" is a catch-all.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
