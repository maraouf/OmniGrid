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
import asyncio  # noqa: F401  used in main body + re-exported for children via `from main import *`
import hashlib  # noqa: F401  re-exported via `from main import *`
import json  # noqa: F401  re-exported via `from main import *`
import math  # noqa: F401  re-exported via `from main import *`
import os  # noqa: F401  used in main body + re-exported for children
import re  # noqa: F401  re-exported via `from main import *`
import secrets  # noqa: F401  re-exported via `from main import *`
import sqlite3  # noqa: F401  used in main body + re-exported for children
import tempfile  # noqa: F401  re-exported via `from main import *`
import time  # noqa: F401  used in main body + re-exported for children
import uuid  # noqa: F401  re-exported via `from main import *`
from contextlib import asynccontextmanager
from typing import Annotated, Any, Iterable, Optional, Set, cast  # noqa: F401  Any/Iterable/Set/cast re-exported via `from main import *` for children

# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).
from dotenv import load_dotenv

from logic.env_keys import EnvKey, env_get  # noqa: E402

load_dotenv(env_get(EnvKey.ENV_FILE_PATH, "/app/.env"))

# Install the stdout/stderr tee as early as possible so uvicorn's own
# startup lines land in the in-memory buffer that powers Admin → Logs.
# Tee is idempotent + passthrough — Docker logs still see everything.
from logic import logs as _logs  # noqa: E402

_logs.install()

import httpx  # noqa: E402,F401  re-exported via `from main import *` for children
from fastapi import (  # noqa: E402,F401  Form/FastApiPath/WebSocket/WebSocketDisconnect re-exported for children
    BackgroundTasks,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Path as FastApiPath,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse  # noqa: E402,F401  FileResponse/StreamingResponse re-exported for children
from fastapi.staticfiles import StaticFiles  # noqa: E402,F401  re-exported for child routes (main mounts it at "/" at the tail)

from logic import auth, backups, config_export, errors as _err, events as _events, metrics, oidc, schedules, totp  # noqa: E402,F401  config_export/oidc/totp/_err re-exported for children

# FastAPI dependency-injection type aliases (PEP 604 / PEP 612). Declaring
# `_admin: AdminUser` instead of the older
# `_admin: auth.User = Depends(auth.require_admin)` lets PyCharm's
# type-checker narrow correctly through fastapi's `Depends()` Generic
# (which the IDE stubs as `Optional[Any]` and would otherwise emit a
# `Member 'None' of 'Any | None'` warning chain on every `.id` /
# `.username` access in every route handler). The `Annotated[X,
# Depends(callable)]` form is canonical for FastAPI — the second
# arg is the dependency spec; PyCharm narrows on the first. The bare
# `= Depends()` sentinel keeps Python's parameter-ordering rule
# satisfied when the handler also has positional-with-default params
# before `_admin`. ~140 route handlers consume these aliases.
AdminUser = Annotated[auth.User, Depends(auth.require_admin)]
CurrentUser = Annotated[auth.User, Depends(auth.current_user)]
from logic import webauthn_helper as webauthn_h  # noqa: E402,F401  re-exported for users_routes / auth_routes
from pydantic import BaseModel, field_validator  # noqa: E402,F401  field_validator re-exported for ops_routes' StackRetagIn

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
from logic.version import read_version  # noqa: E402,F401  re-exported for users_routes / scan_routes

# ============================================================================
# Config
# ============================================================================
# Portainer connection config is DB-backed / UI-managed — see
# logic.portainer.get_portainer_settings(). Process-level tunables
# (cache TTLs, concurrency caps, sample interval, history days) resolve
# via logic.tuning (DB > env > default).
from logic import db as _db  # noqa: E402
from logic.db import DB_PATH as _DB_PATH_OPT, db_conn, get_setting, get_setting_bool, load_settings_json, set_setting, active_host_stats_providers  # noqa: E402,F401

# Narrow DB_PATH from `Optional[str]` (logic.db's typed env-or-None) to
# `str` for every consumer below. If DB_PATH is missing the boot path
# already serves the config-error page (see DB_PATH_ERROR handling in
# `_lifespan`), so by the time any route handler runs we know the value
# is a real string — fall back to "" instead of None so callers like
# `os.path.dirname(DB_PATH)` don't trip type-checker None-access errors.
DB_PATH: str = _DB_PATH_OPT or ""
from logic import tuning  # noqa: E402,F401  used in main body + re-exported for children
from logic.tuning import Tunable  # noqa: E402,F401  used in main body + re-exported for children
from logic.settings_keys import (  # noqa: E402,F401  helper-key functions re-exported for child route consumers
    Settings,
    ai_provider_api_key_key,
    ai_provider_base_url_key,
    ai_provider_enabled_key,
    ai_provider_model_key,
    last_test_success_key,
    notify_event_key,
)
from logic import host_baseline as _host_baseline  # noqa: E402,F401  re-exported for apps_routes drift queries
# `coerce_int` narrows Any|None → Optional[int] at every JSON/dict-cell
# boundary in the Apps endpoints. Hoisted to module level so the same
# helper is reused across `api_service_probe_now`, `_shape_host_apps`,
# `_clean_host_services` etc. without repeated function-local imports.
# Imported via the public alias so static analysis doesn't flag the
# underscore-prefixed `_coerce_int` original (private to the module).
from logic.service_catalog import coerce_int as _coerce_int_local  # noqa: E402,F401  re-exported for apps_routes / hosts_routes

DOCKERHUB_USER = env_get(EnvKey.DOCKERHUB_USER)
DOCKERHUB_TOKEN = env_get(EnvKey.DOCKERHUB_TOKEN)

# Bootstrap-only env vars for seeding the first admin. Only consulted when
# the users table is empty at startup — safe to leave set or unset afterward.
BOOTSTRAP_ADMIN_USER = env_get(EnvKey.BOOTSTRAP_ADMIN_USER)
BOOTSTRAP_ADMIN_PASSWORD = env_get(EnvKey.BOOTSTRAP_ADMIN_PASSWORD)

# Notification event names + per-event default state — single source of
# truth lives in ``logic.ops`` so ``notify()`` and ``api_get_settings``
# read the same map.
# previously these were defined here AND duplicated as a hardcoded
# ``default=True`` inside ``notify()`` — fresh deploys fired user_login
# notifications even though the admin form claimed the toggle was off.
from logic.ops import (  # noqa: E402,F401  both re-exported via `from main import *` for auth_routes / settings_routes / admin_ai_routes
    NOTIFY_EVENT_NAMES as _NOTIFY_EVENT_NAMES,
    NOTIFY_EVENT_DEFAULTS as _NOTIFY_EVENT_DEFAULTS,
)

# TOTP / 2FA policy defaults. DB > default. Same shape as the
# notify_event_* defaults map above so api_get_settings reads through
# get_setting / get_setting_bool with these as the fallbacks.
_TOTP_POLICY_DEFAULTS = {
    "totp_allowed": True,
    "totp_required_for_admins": False,
    "totp_required_for_users": False,
    "totp_lockout_max_failures": 5,
    "totp_lockout_minutes": 15,
    # Passkey master toggle. Mirrors `totp_allowed`. When OFF,
    # `register-start` returns 403; existing enrolments stay valid for
    # login until each user revokes (or admin clears via reset).
    "passkeys_allowed": True,
}

# TOTP-policy resolution cache. The login flow calls
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


# noinspection PyTypeChecker,PyUnresolvedReferences
def _resolve_totp_policy() -> dict:
    """Return the resolved TOTP policy as a dict with concrete types.

    Caller (login flow + admin override + Profile guards) only needs to
    read scalar booleans / ints. No env vars are consulted -- this is
    purely DB-backed (Admin -> Config edits the values).

    Cached for `_TOTP_POLICY_CACHE_TTL_SECONDS` — every
    login flow makes 6+ calls in quick succession, each previously
    hitting the DB. The cache is invalidated on every settings write
    via `_invalidate_totp_policy_cache()` so admin edits take effect
    immediately.
    """
    cached_value = _totp_policy_cache.get("value")
    cached_ts = _totp_policy_cache.get("ts") or 0.0
    if cached_value is not None and (time.time() - cached_ts) < _TOTP_POLICY_CACHE_TTL_SECONDS:
        return cached_value
    # `_TOTP_POLICY_DEFAULTS` is a dict mixing bool and int values; dict.get
    # widens to `bool | int` which the type-checker rejects when passed to
    # `get_setting_bool(default: bool)`. Cast each default to plain bool at
    # the call site so the narrowing is explicit.
    resolved = {
        "totp_allowed": get_setting_bool(
            Settings.TOTP_ALLOWED, bool(_TOTP_POLICY_DEFAULTS.get("totp_allowed", True)),
        ),
        "totp_required_for_admins": get_setting_bool(
            Settings.TOTP_REQUIRED_FOR_ADMINS,
            bool(_TOTP_POLICY_DEFAULTS.get("totp_required_for_admins", False)),
        ),
        "totp_required_for_users": get_setting_bool(
            Settings.TOTP_REQUIRED_FOR_USERS,
            bool(_TOTP_POLICY_DEFAULTS.get("totp_required_for_users", False)),
        ),
        "totp_lockout_max_failures": int(
            get_setting(
                Settings.TOTP_LOCKOUT_MAX_FAILURES,
                str(_TOTP_POLICY_DEFAULTS.get("totp_lockout_max_failures", 5)),
            ) or _TOTP_POLICY_DEFAULTS.get("totp_lockout_max_failures", 5)
        ),
        "totp_lockout_minutes": int(
            get_setting(
                Settings.TOTP_LOCKOUT_MINUTES,
                str(_TOTP_POLICY_DEFAULTS.get("totp_lockout_minutes", 15)),
            ) or _TOTP_POLICY_DEFAULTS.get("totp_lockout_minutes", 15)
        ),
        "passkeys_allowed": get_setting_bool(
            Settings.PASSKEYS_ALLOWED, bool(_TOTP_POLICY_DEFAULTS.get("passkeys_allowed", True)),
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
async def _lifespan(_app: FastAPI):
    """Lifespan-managed startup — per the single-replica rule in
    CLAUDE.md, long-running workers live here so they stay at
    one-per-process. The `_app` parameter is FastAPI's required
    handler signature; the lifespan body uses module-level `app`
    directly, so the parameter is unused-by-design."""
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
    # Wrap init_db so a failure short-circuits to the same
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

    # Pre-seed caches from the persisted snapshot tables in
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
        """Background task: seed in-memory caches from persisted snapshots
        so the first /api/items / /api/stats / /api/hosts after a
        container restart returns prior data instantly while live
        gathers run in parallel."""
        try:
            from logic import stats as _stats_local
            n_stats = _stats_local.seed_stats_cache_from_db()
            if n_stats:
                print(f"[boot] seeded {n_stats} stats entries from stats_samples")
        except (ImportError, OSError, RuntimeError) as boot_err:
            print(f"[boot] seed_stats_cache_from_db failed: {boot_err}")
        try:
            from logic import gather as _gather_local
            n_hosts = _gather_local.seed_nodes_info_from_snapshots()
            if n_hosts:
                print(f"[boot] seeded {n_hosts} host snapshots from host_snapshots")
        except (ImportError, OSError, RuntimeError) as boot_err:
            print(f"[boot] seed_nodes_info_from_snapshots failed: {boot_err}")
        # Cross-restart items snapshot seed. Populate `_cache`
        # from `items_snapshot` so the FIRST `/api/items` after a
        # container restart returns the prior snapshot instantly while
        # the live gather runs in the background. MUST run AFTER
        # seed_nodes_info_from_snapshots because both touch
        # `_cache["nodes_info"]` and the items-snapshot helper merges
        # rather than clobbers. First-ever boot (empty table) returns 0
        # and falls through to the legacy block-on-gather behaviour.
        try:
            from logic import gather as _gather_local
            n_items = _gather_local.seed_items_cache_from_snapshot()
            if n_items:
                print(f"[boot] seeded {n_items} items from items_snapshot")
        except (ImportError, OSError, RuntimeError) as boot_err:
            print(f"[boot] seed_items_cache_from_snapshot failed: {boot_err}")
        # Orphan sweep on startup. Cleans stale `<provider>:<host_id>`
        # rows from `host_failure_state` + `host_provider_last_ok` where the
        # host has been deleted OR no longer has that provider configured.
        # Pre-fix these accumulated until the operator re-saved a host config
        # — operator-reported pulse/beszel paused state on a Ping-only host
        # carried over indefinitely hardened the probe-side gate.
        # Running on every deploy ensures a fresh DB without operator action.
        try:
            # `_load_hosts_config` + `_sweep_orphan_provider_state_rows`
            # are defined in main_pkg modules — underscore-prefixed
            # names are NOT pulled in by `from X import *` so we must
            # import them explicitly here. By lifespan-startup time the
            # chain has fully loaded; the import is a sys.modules cache
            # hit (no chain side-effects).
            # noinspection PyProtectedMember
            from main_pkg.hosts_routes import (
                _load_hosts_config as _load_curated,
                _sweep_orphan_provider_state_rows as _sweep_orphans,
            )
            curated = _load_curated()
            live_ids = {h.get("id") for h in curated if h.get("id")}
            removed = _sweep_orphans(live_ids)
            if removed:
                print(f"[boot] orphan sweep removed {removed} row(s) from "
                      f"host_failure_state / host_provider_last_ok")
        except (sqlite3.Error, OSError, RuntimeError) as boot_err:
            print(f"[boot] orphan sweep failed: {boot_err}")
        # Notification template audit — verify every event registered in
        # NOTIFY_EVENT_NAMES has a matching default in
        # NOTIFY_TEMPLATE_DEFAULTS. Drift surfaces as a WARN log line +
        # a flag on /api/admin/notify-templates so the SPA's editor can
        # render a warning chip. Cheap; runs once at boot.
        try:
            # Boot-only variant — emits the per-WARN-line trace AND
            # the consolidated `[boot] notify template audit:` summary
            # below. The GET path on /api/admin/notify-templates uses
            # the data-only variant so a drift deploy doesn't re-flood
            # the log on every Admin → Notifications visit.
            audit = _ops_mod.audit_template_and_log()
            if audit.get("missing_defaults") or audit.get("unknown_defaults"):
                print(
                    f"[boot] notify template audit: "
                    f"missing_defaults={audit.get('missing_defaults') or []} "
                    f"unknown_defaults={audit.get('unknown_defaults') or []}"
                )
        except (ImportError, AttributeError, RuntimeError) as boot_err:
            print(f"[boot] notify template audit failed: {boot_err}")

        # Schedule-kind audit gate — same shape as the notify template
        # audit above. Walks `SCHEDULE_KINDS`, verifies every runner is
        # async + name-matches the `_run_<kind>` convention + has a
        # docstring. Drift logs a WARN line; new schedule kinds added
        # later this turn or in future PRs catch any plumbing error
        # that would otherwise only surface at fire time. Static-only
        # — does NOT fire any runner (would legitimately spawn ops).
        try:
            sched_audit = schedules.audit_schedule_kinds()
            if (
                sched_audit.get("missing_async")
                or sched_audit.get("name_mismatches")
                or sched_audit.get("missing_docstrings")
            ):
                print(
                    f"[boot] schedule kinds audit: "
                    f"missing_async={sched_audit.get('missing_async') or []} "
                    f"name_mismatches={sched_audit.get('name_mismatches') or []} "
                    f"missing_docstrings={sched_audit.get('missing_docstrings') or []}"
                )
        except (ImportError, AttributeError, RuntimeError) as boot_err:
            print(f"[boot] schedule kinds audit failed: {boot_err}")

        # First-boot helper — auto-create a default swarm_agent_health
        # schedule when Portainer is configured AND no equivalent row
        # exists yet. Operators who want to opt out flip
        # `swarm_autoheal_bootstrap_enabled` to false in Admin →
        # Portainer before the next restart. Idempotent + latched
        # via `swarm_autoheal_bootstrap_done` so a deleted-on-purpose
        # row stays deleted across restarts.
        try:
            with _db.db_conn() as _conn:
                bootstrap_status = schedules.bootstrap_swarm_agent_health_schedule(_conn)
            if bootstrap_status.get("status") == "created":
                print(
                    f"[boot] swarm_agent_health bootstrap: "
                    f"created default schedule "
                    f"name={bootstrap_status.get('name')!r}"
                )
            elif bootstrap_status.get("status") == "skipped_portainer_unconfigured":
                # Verbose enough that an operator wondering "why didn't
                # it auto-bootstrap" finds the answer in Admin → Logs
                # without grepping the source. Will retry next boot.
                print(
                    "[boot] swarm_agent_health bootstrap: "
                    "skipped — Portainer not configured (will retry on next boot)"
                )
        except (sqlite3.Error, OSError, ImportError, RuntimeError) as boot_err:
            print(f"[boot] swarm_agent_health bootstrap failed: {boot_err}")

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
    # Ping reachability sampler — TCP-connect (or optional ICMP)
    # probes for hosts that opt in via hosts_config[].ping.enabled.
    # Same lifespan-only contract; dormant when "ping" isn't in
    # host_stats_source. Writes to ping_samples; pubs `host:ping_sampled`.
    from logic import ping_sampler as _ping_sampler
    ping_sampler = asyncio.create_task(
        _ping_sampler.ping_sampler_loop(), name="ping-sampler",
    )
    # Pulse-only history sampler — feeds the host-drawer chart grid
    # AND the inline Hosts-row sparkline for Pulse-only hosts (no
    # Beszel agent, no node-exporter). Writes to host_pulse_samples;
    # /api/hosts/history dispatches to host_pulse_sampler.history_series
    # when the curated row has a `pulse_name` set. Lifespan-only +
    # skip-don't-synthesize discipline shared with the other samplers.
    from logic import host_pulse_sampler as _host_pulse_sampler
    host_pulse_sampler = asyncio.create_task(
        _host_pulse_sampler.host_pulse_sampler_loop(),
        name="host-pulse-sampler",
    )
    # Webmin-only history sampler — completes the history-parity
    # picture across providers. Most operators run Webmin alongside
    # NE so the NE sampler already covers them; this sampler only
    # matters for the small set of Webmin-only hosts. Same lifespan-
    # only + skip-don't-synthesize discipline as the other samplers.
    from logic import host_webmin_sampler as _host_webmin_sampler
    host_webmin_sampler = asyncio.create_task(
        _host_webmin_sampler.host_webmin_sampler_loop(),
        name="host-webmin-sampler",
    )
    # Beszel local-store sampler — closes the read-through-only gap
    # for Beszel charts. Pre-fix Beszel was the only host-stats
    # provider without its own local SQLite table; every chart query
    # hit the PocketBase hub directly, so when the hub's `1m`
    # aggregation tier aged out (~1h retention) the data was gone
    # and OmniGrid had no fallback. The sampler writes one row per
    # Beszel-tracked host per tick into `host_beszel_samples` —
    # same canonical "every provider has its own local store" shape
    # as Pulse / Webmin / NE / SNMP / Ping. Same lifespan-only +
    # skip-don't-synthesize discipline.
    from logic import host_beszel_sampler as _host_beszel_sampler
    host_beszel_sampler = asyncio.create_task(
        _host_beszel_sampler.host_beszel_sampler_loop(),
        name="host-beszel-sampler",
    )
    # Per-host baseline sampler — recomputes
    # 30-day rolling median + IQR for cpu_pct / mem_pct / disk_pct /
    # ping_rtt_ms once per hour. Drives the ▲/▼/━ drift indicator
    # on every Hosts row + the per-host enrichment in `_merge_one_host`.
    from logic import host_baseline_sampler as _host_baseline_sampler
    host_baseline_sampler = asyncio.create_task(
        _host_baseline_sampler.host_baseline_sampler_loop(),
        name="host-baseline-sampler",
    )
    # HTTP / TLS / DNS health probe — seventh host-stats provider.
    # Active per-URL TCP / TLS-cert / DNS sampler. Dormant when
    # ``"http_probe"`` isn't in ``host_stats_source`` OR the master
    # ``http_probe_enabled`` setting is false — same dormant-but-
    # ticking shape as the other lifespan samplers so a runtime toggle
    # lands without restart. Same lifespan-only + skip-don't-synthesize
    # discipline as the other samplers.
    from logic import host_http_sampler as _host_http_sampler
    host_http_sampler = asyncio.create_task(
        _host_http_sampler.host_http_sampler_loop(),
        name="host-http-sampler",
    )
    # Per-service reachability sampler — walks every curated host's
    # `services[]` for entries with `probe.enabled=true` and probes
    # each via TCP-connect or HTTP. Dormant when the master
    # `service_probe_enabled` toggle is off OR no service has opted in
    # (per-tick gate so a runtime flip takes effect without restart).
    from logic import service_sampler as _service_sampler
    service_sampler = asyncio.create_task(
        _service_sampler.service_sampler_loop(),
        name="service-sampler",
    )
    # Persistent-log pruner — sweeps /app/data/logs/ once per
    # hour, deletes any omnigrid-YYYY-MM-DD.log older than the
    # operator-tunable retention window.
    log_pruner = asyncio.create_task(_log_pruner_loop(), name="log-pruner")
    # Telegram inbound-command listener (Phase 2 — long-poll loop).
    # Per-iteration gate inside the loop checks the
    # `telegram_listener_enabled` setting + bot-token + chat-id so the
    # operator can flip the listener on/off in Admin → Notifications
    # without a restart. Loop sleeps 5s when disabled — cheap idle.
    from logic import telegram_listener as _telegram_listener
    telegram_listener = asyncio.create_task(
        _telegram_listener.listener_loop(),
        name="telegram-listener",
    )
    try:
        yield
    finally:
        # Cancel in reverse-start order. Each cancel + await is wrapped so
        # one failing shutdown step can't starve the next one.
        for task in (telegram_listener, log_pruner, service_sampler, host_http_sampler, host_baseline_sampler, host_beszel_sampler, host_webmin_sampler, host_pulse_sampler, ping_sampler, host_metrics_sampler, host_net_sampler, scheduler, sampler, seed_task):
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


# Explicit unhandled-exception logging. FastAPI's default ServerErrorMiddleware
# logs via the `uvicorn.error` Python logger — under some deployment configs
# (custom log_config, docker-log-driver filtering) those tracebacks don't
# reach the operator's stdout / Admin → Logs view. This handler tees the
# full traceback through `print()` which the `logic.logs` stdout tee
# captures into BOTH the in-memory ring (Admin → Logs UI) AND the
# persistent daily log file. Returns a generic 500 to the client; the
# detail stays internal so we don't leak stack traces over the wire.
@app.exception_handler(Exception)
async def _log_unhandled_exception(request: Request, exc: Exception):
    import traceback as _tb
    tb = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
    method = request.method
    path = request.url.path
    print(f"[http] UNHANDLED EXCEPTION {method} {path} "
          f"exc={type(exc).__name__}: {exc}\n{tb}")
    return JSONResponse(
        {"error": "internal_server_error",
         "detail": f"{type(exc).__name__}: {exc}"},
        status_code=500,
    )


# long-lived immutable caching for version-busted static assets.
# Asset refs in the shell carry ?v=<APP_VERSION> (rewritten by _render_shell);
# a real deploy bumps the PATCH version → the ?v= value changes → the URL is a
# fresh cache key, so marking the response immutable for a year is safe and
# saves a conditional-revalidation round-trip per asset per navigation. GATED
# on a non-dev version: local 0.0.0-dev builds keep revalidating so a dev edit
# (which does NOT bump the version) isn't pinned stale for a year. The shell
# itself (`/`, no ?v=) is untouched, so it keeps its revalidate-on-load
# behaviour and always picks up new asset URLs after a deploy.
_IMMUTABLE_DEV_MARKER = "0.0.0-dev"


@app.middleware("http")
async def _immutable_versioned_assets(request: Request, call_next):
    resp = await call_next(request)
    try:
        if request.method in ("GET", "HEAD") and resp.status_code == 200:
            v = request.query_params.get("v")
            if v and v != _IMMUTABLE_DEV_MARKER and not request.url.path.startswith("/api/"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    except (AttributeError, TypeError):
        # Defensive: never let header decoration break a response (e.g. an
        # exotic response type without a mutable .headers mapping).
        pass
    return resp


# gzip text responses (JSON API payloads, HTML shell, CSS / JS) above
# a size threshold — meaningful on the larger /api/items + /api/hosts payloads
# and the SPA shell. The SSE stream (/api/events, text/event-stream) is
# BYPASSED: GZipMiddleware's chunk buffering would hold events back until the
# zlib block fills, stalling real-time delivery + the keepalive heartbeat.
# Imported from fastapi (a direct dependency) rather than starlette, which is
# only a transitive dep — fastapi re-exports GZipMiddleware unchanged.
from fastapi.middleware.gzip import GZipMiddleware as _GZipMiddleware  # noqa: E402


class _SSESafeGZipMiddleware:
    """GZips responses EXCEPT the /api/events SSE stream.

    Composition (not subclassing) so the ``__init__(app, minimum_size)`` shape
    matches Starlette's middleware-factory protocol cleanly; it delegates to an
    inner GZipMiddleware for everything but the SSE path, which is passed
    straight through to the app (gzip chunk-buffering would stall live events).
    """

    def __init__(self, asgi_app, minimum_size: int = 1024):
        self.app = asgi_app
        self._gzip = _GZipMiddleware(asgi_app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").startswith("/api/events"):
            await self.app(scope, receive, send)
            return
        await self._gzip(scope, receive, send)


app.add_middleware(_SSESafeGZipMiddleware, minimum_size=1024)

# Prometheus metric definitions moved to logic/metrics.py. The cache-age
# collector is wired below (once _cache exists), and every remaining
# metric call site in this file references them via `metrics.NAME`.
metrics.register_cache_age_collector(lambda: _cache)
# SSE bus health collectors. Wires
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

# Canonical baseline AI memories. Seeded on every boot via init_db; the
# duplicate-text guard there makes the operation idempotent. Source
# 'system' (with actor 'bootstrap') distinguishes these from operator-
# added or AI-emitted memories in the Admin → AI → Memory pane.
# Each entry is a single string — the AI's palette user-prompt prepends
# the full set so every conversation starts with this baseline knowledge.
_AI_MEMORY_SEEDS: tuple[str, ...] = (
    # Swarm task-ID suffix gotcha — the AI tried to drill into a service-
    # named container (`tracearr_tracearr`) with docker_container_du and
    # got 'No such container'. The actual container name carries a
    # dynamic task-ID suffix (`tracearr_tracearr.1.glr5r6sv31fcz8e0p019m1sbm`)
    # that has to be discovered via docker_ps_with_sizes first.
    "When dealing with Docker Swarm containers, the running container name carries a "
    "DYNAMIC TASK-ID SUFFIX (e.g. `tracearr_tracearr.1.glr5r6sv31fcz8e0p019m1sbm`), "
    "NOT the bare service name (`tracearr_tracearr`). For docker_container_du and any "
    "`docker exec`-style operation, ALWAYS resolve the full container name first via "
    "`ssh_diag preset=docker_ps_with_sizes` (or a `docker ps` lookup) and use the EXACT "
    "name from that output. Single-replica compose containers use `<stack>_<service>_1` "
    "shape (no task ID); standalone containers carry whatever name was passed to "
    "`docker run --name`.",
)


def init_db():
    """Boot orchestrator — create all SQLite tables idempotently and apply pending migrations."""
    with db_conn() as c:
        # Wrap the whole schema-create script in an explicit transaction
        # so a power loss / hard kill mid-init can't leave a half-applied
        # schema. Every statement in here is idempotent (CREATE IF NOT
        # EXISTS / ALTER ... except OperationalError) so the worst case
        # was always recoverable, but rolling-back an interrupted boot
        # is cleaner than racing with `IF NOT EXISTS` on the next start.
        # in the code review.
        c.executescript("""
        BEGIN;
        -- target_kind taxonomy column added in migration 3 (separate
        -- from op_type which names the action). Values used today:
        -- 'op' (container / stack / service write op), 'schedule'
        -- (scheduler-fired runs), 'ssh' (admin SSH console), 'hosts'
        -- (curated-config bulk actions), 'auth' (password / token
        -- changes), 'system' (catch-all). Index supports the Admin →
        -- History bucket-by-kind filter.
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL, op_type TEXT NOT NULL,
            target_kind TEXT,
            target_name TEXT, target_id TEXT,
            status TEXT NOT NULL, duration REAL,
            events TEXT, error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_history_ts ON history(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_history_op_type ON history(op_type);
        CREATE INDEX IF NOT EXISTS idx_history_target_name ON history(target_name);
        CREATE INDEX IF NOT EXISTS idx_history_status ON history(status);
        -- idx_history_target_kind is created by migration — keep it
        -- there so legacy DBs (where the table exists without the
        -- column at init_db time) don't fail the executescript before
        -- migrations get a chance to ADD COLUMN.

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
            cpu REAL, mem_used REAL, mem_limit REAL,
            size_root REAL
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

        -- Pulse-only history. Mirrors host_metrics_samples shape so
        -- the SPA's chart helpers + inline sparkline data-source
        -- ladder treat Pulse-only hosts identically to NE-only hosts.
        -- Separate table so a host running BOTH Pulse and NE doesn't
        -- get double-writes from two samplers — each table has one
        -- writer, one consumer. Pulse doesn't expose disk read/write
        -- counters so those columns aren't on this table; the
        -- history_series envelope returns 0 for those keys instead.
        CREATE TABLE IF NOT EXISTS host_pulse_samples (
            ts             INTEGER NOT NULL,
            host_id        TEXT    NOT NULL,
            cpu_percent    REAL,
            mem_total      INTEGER,
            mem_used       INTEGER,
            disk_total     INTEGER,
            disk_used      INTEGER,
            net_rx_bps     REAL,
            net_tx_bps     REAL,
            PRIMARY KEY (ts, host_id)
        );
        CREATE INDEX IF NOT EXISTS idx_host_pulse_samples_host_ts
            ON host_pulse_samples(host_id, ts DESC);

        -- Beszel-only history. Same shape as host_pulse_samples; the
        -- separate table is the canonical "every provider has its own
        -- local store" pattern — pre-fix Beszel was the read-through-
        -- only outlier (every chart query hit the PocketBase hub
        -- directly), so when the hub's `1m` aggregation tier aged out
        -- (~1h retention) the data was gone and OmniGrid had no local
        -- cache to fall back on. Drove visible chart "cuts" at the
        -- head of any window > 1h. With this table + the lifespan
        -- `host_beszel_sampler` writing one row per host per tick,
        -- Beszel data lives in OmniGrid's own retention window
        -- (default 7d) regardless of hub-side retention.
        CREATE TABLE IF NOT EXISTS host_beszel_samples (
            ts             INTEGER NOT NULL,
            host_id        TEXT    NOT NULL,
            cpu_percent    REAL,
            mem_total      INTEGER,
            mem_used       INTEGER,
            disk_total     INTEGER,
            disk_used      INTEGER,
            net_rx_bps     REAL,
            net_tx_bps     REAL,
            -- Beszel chart-extras: per-tick captures of fields beyond
            -- the basic CPU/Mem/Disk/Net set the other samplers
            -- carry. Beszel agents expose load avg / swap / temps /
            -- GPUs out of the box; preserving them in the local table
            -- means the host drawer's Load / Swap / Temperature /
            -- GPU chart cards keep working when the hub ages out
            -- (same chart-cut class the basic samples columns
            -- prevent for CPU/Mem/Disk/Net). Variable-shape payloads
            -- (temperatures dict, GPUs list) ride as JSON TEXT —
            -- mirrors the SNMP sampler's `cpu_per_core` blob pattern;
            -- callers parse on read.
            load_1m            REAL,
            load_5m            REAL,
            load_15m           REAL,
            swap_percent       REAL,
            swap_used          REAL,
            bandwidth          REAL,
            containers         INTEGER,
            temperatures_json  TEXT,
            gpus_json          TEXT,
            PRIMARY KEY (ts, host_id)
        );
        CREATE INDEX IF NOT EXISTS idx_host_beszel_samples_host_ts
            ON host_beszel_samples(host_id, ts DESC);

        -- Beszel per-host systemd unit table — one row per
        -- (host_id, service_name) tuple, snapshot of the latest
        -- observed state. Sampler UPSERTs on every tick so an
        -- operator can answer "which units are currently failed
        -- on web01?" without round-tripping to the Beszel hub. The
        -- per-row `last_seen_ts` lets the SPA detect units that
        -- have aged out (Beszel agent stopped tracking the unit).
        -- `last_change_ts` lets the drawer surface "failed since
        -- 2h ago" without scanning a transition log.
        CREATE TABLE IF NOT EXISTS host_beszel_services (
            host_id         TEXT    NOT NULL,
            service_name    TEXT    NOT NULL,
            state           INTEGER,
            sub_state       INTEGER,
            last_seen_ts    INTEGER NOT NULL,
            last_change_ts  INTEGER NOT NULL,
            PRIMARY KEY (host_id, service_name)
        );
        CREATE INDEX IF NOT EXISTS idx_host_beszel_services_host_state
            ON host_beszel_services(host_id, state);

        -- Webmin-only history. Same shape as host_pulse_samples;
        -- separate table so a host with both Webmin AND NE doesn't
        -- get double-writes from two samplers. Webmin is per-host
        -- (Miniserv per target box, like NE); the sampler fans out
        -- across curated rows with a webmin_url set.
        CREATE TABLE IF NOT EXISTS host_webmin_samples (
            ts             INTEGER NOT NULL,
            host_id        TEXT    NOT NULL,
            cpu_percent    REAL,
            mem_total      INTEGER,
            mem_used       INTEGER,
            disk_total     INTEGER,
            disk_used      INTEGER,
            net_rx_bps     REAL,
            net_tx_bps     REAL,
            PRIMARY KEY (ts, host_id)
        );
        CREATE INDEX IF NOT EXISTS idx_host_webmin_samples_host_ts
            ON host_webmin_samples(host_id, ts DESC);

        -- SNMP-specific time-series. Separate from
        -- host_metrics_samples because: (a) SNMP exposes per-core CPU
        -- + buffers/cached memory that the unified `host_metrics_samples`
        -- schema doesn't carry; (b) the rate-derivation contract for
        -- net/disk doesn't apply (SNMP gives gauges, not counters
        -- here); (c) keeps SNMP enrichment additive — operators with
        -- only Beszel/NE pay zero query cost. JSON cpu_per_core blob
        -- is fine because the row count is one per host per tick
        -- and we never query INTO the JSON; bulk reads return the
        -- raw text + frontend parses. Skip-don't-synthesize discipline
        -- still applies — the sampler does NOT insert when memTotal is
        -- 0 / undefined (would mask "host disappeared" as flat zeros).
        CREATE TABLE IF NOT EXISTS host_snmp_samples (
            ts            INTEGER NOT NULL,
            host_id       TEXT    NOT NULL,
            cpu_per_core  TEXT,
            cpu_used_pct  REAL,
            load_1m       REAL,
            load_5m       REAL,
            load_15m      REAL,
            mem_total     INTEGER,
            mem_used      INTEGER,
            mem_buffers   INTEGER,
            mem_cached    INTEGER,
            mem_free      INTEGER,
            disk_total    INTEGER,
            disk_used     INTEGER,
            PRIMARY KEY (ts, host_id)
        );
        CREATE INDEX IF NOT EXISTS idx_host_snmp_samples_host_ts
            ON host_snmp_samples(host_id, ts DESC);

        -- per-interface SNMP counter samples for switch / router
        -- per-port throughput charts. One row per (ts, host_id, ifname);
        -- counters are cumulative IF-MIB ifHCInOctets / ifHCOutOctets
        -- (with 32-bit fallback inside the extractor). Chart layer
        -- computes per-pair deltas → bps and applies skip-don't-
        -- synthesize on out-of-bounds (counter wrap, reboot, gap).
        CREATE TABLE IF NOT EXISTS host_snmp_iface_samples (
            ts        INTEGER NOT NULL,
            host_id   TEXT    NOT NULL,
            ifname    TEXT    NOT NULL,
            in_bytes  INTEGER,
            out_bytes INTEGER,
            PRIMARY KEY (ts, host_id, ifname)
        );
        CREATE INDEX IF NOT EXISTS idx_host_snmp_iface_samples_host_ts
            ON host_snmp_iface_samples(host_id, ts DESC);

        -- Per-temperature-probe history for Dell server hosts. One row per (ts, host_id, probe_idx); the
        -- temperatureProbeTable typically reports 4-12 probes per
        -- server (Inlet / Exhaust / CPU1 / CPU2 / chipset / etc.) and
        -- the chart card renders one polyline per probe. probe_name
        -- denormalised onto every row so the chart can label the
        -- legend without joining back to the latest per-probe row.
        -- value_c is degrees Celsius (already converted from MIB's
        -- deci-degC at extraction time).
        CREATE TABLE IF NOT EXISTS host_snmp_temp_samples (
            ts         INTEGER NOT NULL,
            host_id    TEXT    NOT NULL,
            probe_idx  TEXT    NOT NULL,
            probe_name TEXT,
            value_c    REAL,
            PRIMARY KEY (ts, host_id, probe_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_host_snmp_temp_samples_host_probe_ts
            ON host_snmp_temp_samples(host_id, probe_idx, ts DESC);

        -- Per-host rolling baseline (median + IQR) for drift detection.
        -- One row per (host_id, metric) — UPSERT on every recompute.
        -- Metric is one of: cpu_pct / mem_pct / disk_pct / ping_rtt_ms.
        -- Computed hourly by `host_baseline_sampler`
        -- from the matching time-series table (host_metrics_samples for
        -- cpu/mem/disk; ping_samples for rtt). Drives the ▲/▼/━ drift
        -- indicator on every Hosts row.
        CREATE TABLE IF NOT EXISTS host_baselines (
            host_id     TEXT NOT NULL,
            metric      TEXT NOT NULL,
            median      REAL,
            iqr         REAL,
            sample_count INTEGER,
            computed_ts INTEGER NOT NULL,
            PRIMARY KEY (host_id, metric)
        );
        CREATE INDEX IF NOT EXISTS idx_host_baselines_computed_ts
            ON host_baselines(computed_ts DESC);

        -- Append-only transition log for the host pause/resume
        -- lifecycle. Pre-fix the timeline endpoint synthesised
        -- `provider_paused` / `provider_recovered` events from
        -- the CURRENT `host_failure_state` snapshot — meaning a
        -- host that had paused → resumed → paused → resumed showed
        -- only the LATEST state, not the history. This table
        -- captures every transition so the timeline reflects the
        -- true incident sequence. Pruned by the same retention
        -- knob the time-series tables use (`tuning_stats_history_days`).
        --
        -- Schema:
        --   host_id  — BARE host_id (not the prefixed `<provider>:<id>`
        --              form used by host_failure_state rows). Always
        --              the operator-visible identifier so the timeline
        --              filters on host_id IN (...) work.
        --   provider — '' for whole-host events; '<provider>' for
        --              per-(provider, host) events.
        --   kind     — 'paused' | 'recovered' (extensible — future
        --              kinds like 'manual_pause' / 'manual_resume'
        --              can drop in without schema migration).
        --   error    — last error string (only set on 'paused'
        --              events; truncated to 500 chars).
        --   actor    — 'sampler' | 'admin:<username>' | 'scheduler'
        --              so audit trails can distinguish auto vs manual.
        CREATE TABLE IF NOT EXISTS host_failure_events (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       REAL NOT NULL,
            host_id  TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            kind     TEXT NOT NULL,
            error    TEXT,
            actor    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_host_failure_events_ts
            ON host_failure_events(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_host_failure_events_host_ts
            ON host_failure_events(host_id, ts DESC);

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
        -- Cross-restart persistence for the items / stacks / nodes
        -- gather cache. Single-row table — `id=1` always —
        -- carrying a JSON blob with `items` / `stacks` / `nodes` /
        -- `nodes_info` / `ts`. Written at the end of every successful
        -- `_gather()`, read at lifespan startup so the FIRST
        -- `/api/items` after a container restart returns the prior
        -- snapshot instantly while the live gather runs in the
        -- background. Without this, post-restart the in-memory `_cache`
        -- is empty and the first request blocks on the full Portainer
        -- fan-out + image-digest probe (10-30s). Single-row design —
        -- the gather replaces the snapshot wholesale, so stale-ignore
        -- / removed-item cleanup is automatic on the next successful
        -- gather. Cleared by an `INSERT OR REPLACE` on each save.
        CREATE TABLE IF NOT EXISTS items_snapshot (
            id INTEGER PRIMARY KEY,
            ts REAL NOT NULL,
            data TEXT NOT NULL
        );
        -- Permanent-fail tracking. One row per (host, provider) whose
        -- sampler has hit consecutive probe failures. When ``paused`` flips
        -- to 1, the sampler short-circuits subsequent ticks (no probe
        -- attempt, no log spam) until the operator explicitly resumes via
        -- POST /api/hosts/{id}/resume-sampling.
        --
        -- Schema after migration 2 (split_provider_host_pk):
        --   host_id  — bare host identifier (operator-visible).
        --   provider — '' for whole-host pauses (legacy bare-id rows
        --              from /api/hosts/{id}/pause-sampling); '<name>' for
        --              per-(provider, host) pauses driven by
        --              record_provider_outcome.
        -- Composite PK (host_id, provider) replaces the legacy prefixed
        -- "<provider>:<host_id>" key. Reads now use direct equality lookups
        -- instead of full-table-scan WHERE host_id LIKE '%:hid'.
        CREATE TABLE IF NOT EXISTS host_failure_state (
            host_id              TEXT NOT NULL,
            provider             TEXT NOT NULL DEFAULT '',
            first_failure_ts     REAL NOT NULL,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            paused               INTEGER NOT NULL DEFAULT 0,
            paused_at            REAL,
            last_error           TEXT,
            last_failure_ts      REAL,
            PRIMARY KEY (host_id, provider)
        );
        -- idx_host_failure_state_provider is created by migration —
        -- on legacy DBs the table exists WITHOUT the `provider` column
        -- at this point in init_db (CREATE TABLE IF NOT EXISTS is a
        -- no-op when the table already exists), so the index creation
        -- has to wait until migration has rebuilt the table.

        -- Per-(provider, host) last-successful-probe timestamp.
        -- Distinct from host_failure_state which only exists during a
        -- failure streak. last_ok lives ALWAYS — every
        -- record_provider_outcome with ok=True UPSERTs here. After
        -- migration, host_id and provider are separate columns with
        -- composite PK. Drives the "Updated Xm ago" subtitle on each
        -- provider chip in the host drawer's Enabled-agents card.
        CREATE TABLE IF NOT EXISTS host_provider_last_ok (
            host_id    TEXT NOT NULL,
            provider   TEXT NOT NULL,
            last_ok_ts INTEGER NOT NULL,
            PRIMARY KEY (host_id, provider)
        );
        -- idx_host_provider_last_ok_provider — same story as above;
        -- migration owns the index creation so legacy DBs don't
        -- fail the executescript before migrations get to run.
        -- Composite (provider, last_ok_ts DESC) speeds up the
        -- "every host's freshness for ONE provider, newest first" read
        -- pattern (chip-strip render across a 200-host fleet after
        -- BUG-001 lands and the NE sampler also UPSERTs here every
        -- tick). Additive — safe to run on existing deployments;
        -- SQLite no-ops if the index already exists.
        CREATE INDEX IF NOT EXISTS idx_host_provider_last_ok_provider_ts
        ON host_provider_last_ok (provider, last_ok_ts DESC);

        -- Ping reachability time-series. Populated by
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

        -- Public-IP change history. Records every CHANGED outcome from
        -- logic.public_ip.fetch() (operator-opt-in, gated by
        -- tuning_public_ip_enabled). ONE row per change — duplicate IPs
        -- from the cache hit OR consecutive fetches returning the same
        -- value DON'T write a row. Drives the AI palette's ability to
        -- answer "when did my IP / ISP last change?" + the Admin →
        -- Public IP history table. Never pruned by the standard
        -- tuning_stats_history_days retention — IP-change events are
        -- low-volume + high-value (operators want a year+ of history).
        CREATE TABLE IF NOT EXISTS public_ip_history (
            ts      INTEGER PRIMARY KEY,
            ip      TEXT NOT NULL,
            isp     TEXT,
            asn     TEXT,
            country TEXT,
            city    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_public_ip_history_ip
            ON public_ip_history(ip);

        -- HTTP / TLS-cert / DNS health probe (seventh host-stats provider).
        -- ONE row per (host_id, url, ts). Written by
        -- logic/host_http_sampler.py at
        -- tuning_http_probe_sample_interval_seconds cadence (default
        -- 0 = inherit global stats interval). Pruned to
        -- tuning_stats_history_days. Composite primary key allows
        -- multiple URLs per host per tick (operator monitoring
        -- several services on one host).
        CREATE TABLE IF NOT EXISTS host_http_samples (
            ts                   INTEGER NOT NULL,
            host_id              TEXT    NOT NULL,
            url                  TEXT    NOT NULL,
            status_code          INTEGER,
            status_ok            INTEGER NOT NULL,
            content_match_ok     INTEGER NOT NULL,
            tls_expires_in_days  INTEGER,
            tls_subject          TEXT,
            tls_issuer           TEXT,
            tls_error            TEXT,
            dns_resolved         INTEGER NOT NULL,
            latency_ms           INTEGER,
            error                TEXT,
            PRIMARY KEY (ts, host_id, url)
        );
        CREATE INDEX IF NOT EXISTS idx_host_http_samples_host_ts
            ON host_http_samples(host_id, ts DESC);

        -- Per-service reachability probe results — one row per
        -- (host_id, service_idx, ts). `service_idx` is the position
        -- of the service in `hosts_config[host_id].services[]` at
        -- sampler-tick time; we accept that an operator reorder will
        -- mis-attribute pre-reorder rows because the alternative
        -- (sliding-window UUID assignment on every service-list edit)
        -- is much more complex. Operators don't reorder often; the
        -- chart's freshness label highlights when the data is from
        -- before the most recent edit so the operator can recompute.
        -- `alive=1` is the success signal; `rtt_ms` populated only
        -- on alive=1 ticks (skipped on failures per the skip-don't-
        -- synthesize rule).
        -- `port` column distinguishes per-port samples (port=80/443/etc)
        -- from rollup samples (port=0 sentinel — chip-level status). The
        -- sentinel value (0 — not a valid TCP/UDP port per RFC) is used
        -- instead of NULL because SQLite treats every NULL as distinct
        -- in PRIMARY KEY uniqueness checks, which breaks the
        -- INSERT OR REPLACE upsert pattern. Pre-migration installs
        -- (no `port` column) are upgraded in-place by
        -- `_migration_005_service_samples_port_column` which rebuilds
        -- the table with the new PK + backfills existing rows to
        -- port=0. Single-port chips emit ONLY the rollup row; multi-port
        -- chips emit one rollup row PLUS one row per port.
        CREATE TABLE IF NOT EXISTS service_samples (
            ts            INTEGER NOT NULL,
            host_id       TEXT    NOT NULL,
            service_idx   INTEGER NOT NULL,
            port          INTEGER NOT NULL DEFAULT 0,
            alive         INTEGER NOT NULL,
            rtt_ms        INTEGER,
            error         TEXT,
            PRIMARY KEY (ts, host_id, service_idx, port)
        );
        CREATE INDEX IF NOT EXISTS idx_service_samples_host_ts
            ON service_samples(host_id, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_service_samples_host_idx_ts
            ON service_samples(host_id, service_idx, ts DESC);

        -- Apps feature — reusable service templates ("catalog"). Each row
        -- is a recipe an operator can bind to N hosts (Radarr / Sonarr /
        -- Plex / Portainer / etc.) so they don't redefine probe path +
        -- default ports + icon slug per host. Per-host instances continue
        -- to live in `hosts_config[].services[]`; chips may now carry
        -- `catalog_id` (numeric FK to this table) to inherit the template's
        -- defaults. `default_ports_json` is a JSON array of
        -- `{port, protocol, label, probe_path, probe_status}` so a template
        -- can carry multi-port shape (Portainer 8000 + 8443). `icon` is the
        -- brand-icon slug (resolved by static/js/app.js:iconUrlFor).
        -- `source = 'builtin' | 'operator'` distinguishes shipped seed
        -- templates from operator-added ones; builtin rows are idempotently
        -- seeded on first boot when the table is empty.
        CREATE TABLE IF NOT EXISTS service_catalog (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL,
            slug            TEXT    NOT NULL UNIQUE,
            icon            TEXT,
            description     TEXT,
            default_ports_json TEXT NOT NULL DEFAULT '[]',
            source          TEXT    NOT NULL DEFAULT 'operator',
            created_ts      INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
            updated_ts      INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
        );
        CREATE INDEX IF NOT EXISTS idx_service_catalog_slug
            ON service_catalog(slug);

        -- Port-scan provider results. ONE ROW PER OPEN PORT per scan;
        -- closed-port rows would balloon the table on multi-host scans.
        -- `scan_id` groups rows from one scan so the SPA can fetch a
        -- whole scan via `scan_id` OR the latest scan per host via
        -- `(host_id, ts DESC, scan_id) LIMIT 1` to find the head + then
        -- `scan_id = ?` to pull every row in that scan. `service_hint`
        -- is a tiny lookup-table guess (port 32400 → "plex") for chip
        -- labels; NOT a fingerprint — Stage 2's banner-grab path will
        -- replace this with real version detection.
        CREATE TABLE IF NOT EXISTS host_port_scans (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              INTEGER NOT NULL,
            host_id         TEXT    NOT NULL,
            scan_id         TEXT    NOT NULL,
            port            INTEGER NOT NULL,
            service_hint    TEXT,
            banner_excerpt  TEXT,
            protocol        TEXT    DEFAULT 'tcp'
        );
        CREATE INDEX IF NOT EXISTS idx_host_port_scans_host_ts
            ON host_port_scans(host_id, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_host_port_scans_scan
            ON host_port_scans(scan_id);

        -- In-app notifications store. One row per notification dispatched
        -- through `logic.ops.notify`'s `app` medium (sibling of the
        -- existing `apprise` medium). Drives the avatar badge unread-count,
        -- the Notifications page, and SSE pushes. Pruned on schedule
        -- (`prune_notifications` kind) by `tuning_notification_retention_days`.
        -- `severity` mirrors the four levels operators see in the persistent
        -- log viewer (info / warning / error / success). `metadata` is a
        -- free-form JSON blob the renderer can read for richer formatting
        -- (icons, links, durations) without breaking the column shape.
        -- `read_at` NULL = unread; epoch seconds when the operator marked it
        -- read. Index on read_at where NULL gives the unread-count probe an
        -- O(unread) scan rather than O(total).
        CREATE TABLE IF NOT EXISTS notifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          INTEGER NOT NULL,
            event       TEXT    NOT NULL,
            severity    TEXT    NOT NULL,
            title       TEXT    NOT NULL,
            body        TEXT    NOT NULL,
            actor       TEXT,
            target_kind TEXT,
            target_id   TEXT,
            metadata    TEXT,
            read_at     INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_ts
            ON notifications(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_notifications_unread
            ON notifications(read_at) WHERE read_at IS NULL;
        -- AI integration (Stage 1 foundation). One row per call to an
        -- AI provider. Stage 1 ships the schema + admin surface; the
        -- writer lives in `logic/ai.py` (Stage 2+) — when wired up,
        -- every provider call records here so the dashboard can render
        -- token usage / cost / pass-rate / accuracy / response-time
        -- aggregates without needing a separate metrics store.
        --
        --   provider          — claude / gemini / chatgpt / deepseek
        --   model             — provider-specific model id at call time
        --                        (e.g. claude-opus-4-7, gpt-4o,
        --                        gemini-2.5-pro, deepseek-chat). Stored
        --                        per-row so the dashboard can break
        --                        token usage down by model.
        --   kind              — what the call was for (free-form;
        --                        Stage 2+ defines the canonical kinds).
        --   status            — running / success / error.
        --   prompt_tokens     — input tokens consumed.
        --   completion_tokens — output tokens generated.
        --   total_tokens      — sum (or provider-reported total when it
        --                        differs from prompt+completion).
        --   cost_usd          — operator-visible cost in USD; computed
        --                        from per-provider rate cards by the
        --                        writer at insert time so historical
        --                        rows survive a rate-card change.
        --   response_time_ms  — end-to-end latency the writer measured.
        --   accuracy_score    — 0..1 score from the optional accuracy
        --                        check; NULL when the call was not
        --                        validated.
        --   accuracy_check    — JSON metadata about the validation
        --                        (which check ran, expected vs actual,
        --                        etc).
        --   error             — short error message when status='error'.
        --   metadata          — JSON catch-all (request id, retries,
        --                        whatever the writer wants to keep).
        CREATE TABLE IF NOT EXISTS ai_jobs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                INTEGER NOT NULL,
            provider          TEXT    NOT NULL,
            model             TEXT,
            kind              TEXT,
            status            TEXT    NOT NULL,
            prompt_tokens     INTEGER,
            completion_tokens INTEGER,
            total_tokens      INTEGER,
            cost_usd          REAL,
            response_time_ms  INTEGER,
            accuracy_score    REAL,
            accuracy_check    TEXT,
            error             TEXT,
            metadata          TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ai_jobs_ts
            ON ai_jobs(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_ai_jobs_provider_ts
            ON ai_jobs(provider, ts DESC);
        -- AI memory table — durable lessons the AI has learned about
        -- this specific OmniGrid deployment. Populated when an AI
        -- reply emits a `MEMORY: ...` line; injected into every
        -- subsequent palette call's system prompt so the AI accumulates
        -- knowledge across sessions and avoids repeating mistakes.
        --   text   — the memory body (one-line directive, no newlines).
        --   source — 'ai' when emitted by a model reply, 'operator'
        --            when added manually via the admin UI.
        --   actor  — username of the operator whose conversation produced
        --            the memory (or 'system' when seeded).
        CREATE TABLE IF NOT EXISTS ai_memory (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts     INTEGER NOT NULL,
            text   TEXT    NOT NULL,
            source TEXT    NOT NULL DEFAULT 'ai',
            actor  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ai_memory_ts
            ON ai_memory(ts DESC);
        COMMIT;
        """)
        # Idempotent column additions for existing deployments. SQLite pre-3.35
        # has no "ADD COLUMN IF NOT EXISTS", so we catch the OperationalError
        # that gets raised when the column already exists. Safe to re-run on
        # every boot.
        for ddl in (
                "ALTER TABLE history ADD COLUMN actor TEXT DEFAULT 'ui'",
                "ALTER TABLE history ADD COLUMN target_stack TEXT",
                # target_kind taxonomy column — also handled by migration 3
                # which runs at the end of init_db, but adding it here too
                # means any code path that touches `target_kind` BEFORE the
                # migration applies (e.g. an executescript reference earlier
                # in init_db) doesn't fail. Idempotent via the
                # OperationalError catch below.
                "ALTER TABLE history ADD COLUMN target_kind TEXT",
                # disk I/O rates, derived per-tick by
                # host_metrics_sampler from node_disk_{read,written}_bytes_total.
                # Same skip-don't-synthesize discipline as the net rate columns;
                # NULL when the delta is out of bounds.
                "ALTER TABLE host_metrics_samples ADD COLUMN disk_read_bps REAL",
                "ALTER TABLE host_metrics_samples ADD COLUMN disk_write_bps REAL",
                # Per-item image-disk footprint (`size_root` in bytes) — added
                # so Stacks / Services / Containers can render a disk sparkline
                # mirroring the CPU / Memory ones. Pre-fix `stats_samples` only
                # stored CPU + memory, so disk had no time-series and the UI
                # could only show a CURRENT snapshot bar. Sampler writes
                # `s.get("size_root")` each tick.
                "ALTER TABLE stats_samples ADD COLUMN size_root REAL",
                # wall-clock of the MOST RECENT probe failure.
                # ``first_failure_ts`` already records the start of the
                # streak; this is the timestamp of the latest failed
                # probe so the drawer can render "last error N seconds
                # ago" instead of leaving the operator wondering whether
                # the issue may have already cleared.
                "ALTER TABLE host_failure_state ADD COLUMN last_failure_ts REAL",
                # host uptime in SECONDS per SNMP probe. Lets the
                # drawer surface a current-uptime pill AND detect reboots:
                # when sample[N].uptime_s < sample[N-1].uptime_s the host
                # rebooted in the gap (sysUpTime counter resets at boot).
                # Stored as seconds (not raw TimeTicks) so it matches the
                # `host_uptime_s` field convention every other provider
                # uses. Additive — NULL for pre-uptime-column rows.
                "ALTER TABLE host_snmp_samples ADD COLUMN uptime_s INTEGER",
                # switch total throughput. Stored as the cumulative
                # IF-MIB ifHCInOctets / ifHCOutOctets sums (excluding
                # loopback / docker-bridge / virtual ifaces — same exclusion
                # set as Beszel / NE). The chart layer computes deltas at
                # render time. Skip-don't-synthesize: sampler inserts NULL
                # when SNMP didn't return either counter, so the chart can
                # tell "host stopped responding" from "0 bps idle".
                "ALTER TABLE host_snmp_samples ADD COLUMN net_rx_total_bytes INTEGER",
                "ALTER TABLE host_snmp_samples ADD COLUMN net_tx_total_bytes INTEGER",
                # printer lifetime page count (Printer-MIB
                # prtMarkerLifeCount). Cumulative monotonic counter; the
                # SPA computes per-interval deltas → pages/day for the
                # sparkline + reads the live value as the lifetime
                # headline. NULL for non-printer hosts.
                "ALTER TABLE host_snmp_samples ADD COLUMN printer_page_count INTEGER",
                # per-iface link speed (Mbps) so the per-port
                # utilization heatmap can compute throughput ÷ link
                # capacity. NULL when the agent doesn't expose ifHighSpeed
                # (older IF-MIB-v1-only devices) — heatmap renders such
                # ifaces in grey ("unknown speed") instead of red.
                "ALTER TABLE host_snmp_iface_samples ADD COLUMN link_speed_mbps INTEGER",
                # APC UPS time-series fields. Sampler writes the live
                # values per probe so the host drawer can render Output
                # Load %, Battery %, Battery temperature charts over the
                # picker window. NULL for non-UPS hosts. Reads come from
                # `host_load_percent` / `host_battery_percent` /
                # `host_battery_temp_c` extracted in `logic/snmp.py` via
                # PowerNet-MIB OIDs (1.3.6.1.4.1.318.1.1.1.x).
                "ALTER TABLE host_snmp_samples ADD COLUMN load_percent REAL",
                "ALTER TABLE host_snmp_samples ADD COLUMN battery_percent REAL",
                "ALTER TABLE host_snmp_samples ADD COLUMN battery_temp_c REAL",
                # Aggregate disk totals — added so SNMP-only hosts can
                # render the inline disk sparkline. Pre-fix the table
                # carried CPU + memory + load + UPS + interface data
                # but no disk percent, so the SPA's hostInlineSparkline
                # SNMP fallback explicitly skipped disk ("SNMP series
                # doesn't carry it"). Operator reported on a dd-wrt +
                # WDMyCloud NAS where the row's disk bar correctly
                # showed live percent but the sparkline beneath stayed
                # absent. Sampler now writes both columns; SPA derives
                # disk % from the pair (matches the `mem_used/mem_total`
                # branch's pattern). Aggregate values respect the same
                # exclude-mounts list `extract_storage` honours, so
                # phantom rows (dd-wrt's `/opt`) don't pollute history.
                "ALTER TABLE host_snmp_samples ADD COLUMN disk_total INTEGER",
                "ALTER TABLE host_snmp_samples ADD COLUMN disk_used INTEGER",
                # HTTP probe — TLS certificate metadata + DNS / TLS
                # error strings persisted alongside the numeric outcome
                # so the drawer card can surface cert subject / issuer
                # without cross-referencing an external monitor.
                # tls_error carries the exception text when the TLS
                # handshake failed (cert chain broken, hostname mismatch,
                # expired). Idempotent additive adds.
                "ALTER TABLE host_http_samples ADD COLUMN tls_subject TEXT",
                "ALTER TABLE host_http_samples ADD COLUMN tls_issuer TEXT",
                "ALTER TABLE host_http_samples ADD COLUMN tls_error TEXT",
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
        # Schema migrations infrastructure. Adds the
        # `schema_migrations` table and applies any pending migrations
        # registered in `logic/migrations.py:MIGRATIONS`. Empty registry
        # today — additive changes still go in the CREATE TABLE block
        # above. Non-additive changes (renames, type changes, data
        # migrations) get a numbered migration function. Boot halts on
        # migration failure so a half-applied schema can't slip through.
        from logic import migrations as _migrations
        _migrations.init_migrations_schema(c)
        _migrations.apply_pending(c)
        # First-boot ai_memory seed — canonical lessons every deploy
        # benefits from regardless of conversation history. Idempotent
        # via the duplicate-text guard so re-running on existing
        # databases doesn't accumulate duplicates. Source `system` so
        # the Admin → AI → Memory pane can distinguish seeded baseline
        # lessons from `ai`-emitted or `operator`-added ones.
        for canonical_text in _AI_MEMORY_SEEDS:
            try:
                already = c.execute(
                    "SELECT 1 FROM ai_memory WHERE text = ? LIMIT 1",
                    (canonical_text,),
                ).fetchone()
                if already:
                    continue
                c.execute(
                    "INSERT INTO ai_memory (ts, text, source, actor) "
                    "VALUES (?, ?, 'system', 'bootstrap')",
                    (int(time.time()), canonical_text),
                )
            except sqlite3.Error:
                # Seed is best-effort — a one-off insert failure
                # doesn't block init_db.
                pass

        # Apps feature — service_catalog built-in templates. The boot
        # seed adds any builtin that's NEW to _BUILTIN (tracked via a
        # seeded-slug ledger) so a builtin shipped in a later release
        # appears automatically on the next deploy, while builtins the
        # operator deleted on purpose stay gone. Operator edits to a
        # builtin already in the table are never overwritten.
        try:
            from logic.service_catalog import seed_builtins as _seed_catalog
            # noinspection PyArgumentEqualDefault
            n_added = _seed_catalog(force=False)
            if n_added:
                print(f"[service_catalog] seeded {n_added} built-in templates")
        except (sqlite3.Error, ImportError, OSError) as e:
            print(f"[service_catalog] seed deferred: {e}")


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
# `_tag_of` already aliased above from `registry.tag_of`; redundant
# re-assignment here was lint-flagged "Redeclared '_tag_of' defined
# above without usage" — dropped, the upstream alias is canonical.
_node_attr = _gather_mod.node_attr
_node_matches = _gather_mod.node_matches

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
_do_restart_swarm_agent = _ops_mod.do_restart_swarm_agent

# ============================================================================
# Stats moved to logic/stats.py. Shim aliases so existing routes + lifespan
# keep working unchanged.
# ============================================================================
from logic import stats as _stats_mod  # noqa: E402

_stats_cache = _stats_mod.get_stats_cache()
_gather_stats = _stats_mod.gather_stats
_stats_history = _stats_mod.stats_history
_stats_sampler_loop = _stats_mod.stats_sampler_loop

# Background-task references — `asyncio.create_task` returns a future
# the event loop only WEAKLY references; without a strong reference
# elsewhere, GC can eat the task mid-execution and the work silently
# disappears (no exceptions, no logs — the print statements never
# get a chance to fire). Every fire-and-forget task spawned from a
# request handler MUST get added to a strong-reference container
# that outlives the handler. Tasks remove themselves on completion
# via `task.add_done_callback(set.discard)`.
_BACKGROUND_TASKS: set = set()
# Defensive cap on _BACKGROUND_TASKS — a task that NEVER COMPLETES
# (deadlock, infinite loop, hung await) sits in the set forever
# because the done_callback never fires. Without a cap a leaky
# spawn site could grow the set unbounded over a long-running
# deploy. Every spawn-from-handler today goes through
# `spawn_background_task`
# which keeps the count bounded by the actual in-flight work, so
# the cap is theoretical — but it's defence-in-depth for a future
# regression. When the set hits the cap, drop the oldest tasks
# (earliest by event-loop scheduling — set iteration in Python
# is insertion-order-stable enough for monitoring purposes) and
# log a WARN line so the issue is visible in Admin → Logs. 1000 is
# generous: even a fleet-wide port-scan + every host-stats refresh
# in flight at once shouldn't approach this.
_BACKGROUND_TASKS_CAP: int = 1000
# Single-fire-per-window throttle for the cap-eviction WARN line.
# Without this, a runaway-spawn site fires the WARN on EVERY new
# spawn while the set is full — operators see thousands of identical
# lines in Admin → Logs that drown out the actual signal. After the
# first WARN, suppress for 60s, then re-fire if the cap is still
# being hit (so a persistent leak STAYS visible without flooding).
_BACKGROUND_TASKS_CAP_WARN_WINDOW_SECONDS: float = 60.0
_background_tasks_cap_last_warn_ts: float = 0.0


def spawn_background_task(coro, *, label: str = ""):
    """Wrap `asyncio.create_task` with a strong-reference + cleanup
    callback so spawned tasks aren't GC'd mid-execution.

    Drop-in replacement for `asyncio.create_task(coro)` on the
    request-handler path. Logs an `[bg]` line on creation + on
    completion (success / exception) so a vanished task is visible
    in Admin → Logs instead of being silently lost.
    """
    # Defensive cap — when the set is at the limit, drop the oldest
    # task references (the tasks themselves keep running until they
    # complete or are explicitly cancelled; we just stop tracking
    # them). Logging the eviction so a runaway-spawn site is visible.
    # This branch should NEVER fire under normal operation; if it
    # does, that's a leak signal.
    if len(_BACKGROUND_TASKS) >= _BACKGROUND_TASKS_CAP:
        # Set iteration order is insertion-order-stable in CPython
        # 3.7+, which is good enough for "drop the oldest". We pop
        # one tracked task to make room — it'll continue executing
        # (the strong reference in _BACKGROUND_TASKS was the only
        # thing keeping the GC from reaping it though, so by
        # dropping it we accept the GC risk for the OLDEST tasks
        # specifically; new tasks still get the strong reference).
        try:
            stale = next(iter(_BACKGROUND_TASKS))
            _BACKGROUND_TASKS.discard(stale)
            # Single-fire-per-window throttle — see the
            # `_BACKGROUND_TASKS_CAP_WARN_WINDOW_SECONDS` constant
            # block above for the rationale. The eviction itself
            # still happens on every spawn while the cap is hit;
            # only the WARN line is throttled.
            global _background_tasks_cap_last_warn_ts
            now_ts = time.time()
            if (now_ts - _background_tasks_cap_last_warn_ts
                >= _BACKGROUND_TASKS_CAP_WARN_WINDOW_SECONDS):
                _background_tasks_cap_last_warn_ts = now_ts
                print(
                    f"[bg] WARNING — _BACKGROUND_TASKS at cap ({_BACKGROUND_TASKS_CAP}) "
                    f"— evicted oldest task; this signals a spawn-site leak "
                    f"or a never-completing task. Audit the [bg] log "
                    f"for tasks that spawned but never logged 'done'. "
                    f"(suppressing further WARN lines for "
                    f"{int(_BACKGROUND_TASKS_CAP_WARN_WINDOW_SECONDS)}s)"
                )
        except StopIteration:
            pass
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    print(f"[bg] task spawned label={label!r}")

    def _on_done(t):
        _BACKGROUND_TASKS.discard(t)
        if t.cancelled():
            print(f"[bg] task cancelled label={label!r}")
            return
        exc = t.exception()
        if exc is not None:
            import traceback as _tb
            tb = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
            print(f"[bg] task FAILED label={label!r} exc={type(exc).__name__}: {exc}\n{tb}")
        else:
            print(f"[bg] task done label={label!r}")

    task.add_done_callback(_on_done)
    return task


# ============================================================================
# Persistent-log pruner. Once per hour, walks /app/data/logs/ and
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
            days = tuning.tuning_int(Tunable.LOG_RETENTION_DAYS)
            removed = _logs_mod.prune_old_logs(days)
            if removed:
                print(f"[logs] pruned {removed} log file(s) older than {days}d")
        except Exception as e:
            print(f"[logs] pruner tick error: {e}")
        await asyncio.sleep(3600)


# ============================================================================
# API endpoints
# ============================================================================
# noinspection PyProtectedMember
@app.get("/api/stats")
async def api_stats(force: bool = False):
    """Serve cached / seeded stats instantly; refresh in background.

    Cold-load instant paint pattern (mirrors `/api/items` Fix A): when
    ``_stats_cache["stats"]`` already holds entries — including those
    seeded from `stats_samples` at boot — return them IMMEDIATELY
    regardless of the TTL or ``force`` flag, and kick ``_gather_stats``
    into a single-flighted background task. Pre-fix the TTL guard
    blocked on a synchronous gather whenever ``ts`` was older than
    `tuning_stats_cache_ttl_seconds` — and `seed_stats_cache_from_db`
    deliberately stamps ``ts=0.0`` so the seeded entries get refreshed
    on the first call, which made every page load wait through the
    full gather before bars / sparklines could paint. Cold-empty cache
    (no in-memory data — first `/api/stats` after a fresh container
    that has never gathered) still blocks; there's nothing to serve.

    The SPA's existing `_stale: True` markers on seeded entries drive
    the dimmed `.stale` treatment so operators see "this is from cache
    while we refresh" rather than fully-bright but stale numbers.
    """
    now = time.time()
    has_cached_stats = bool(_stats_cache.get("stats"))
    cache_stale = (now - _stats_cache["ts"]) > tuning.tuning_int(Tunable.STATS_CACHE_TTL_SECONDS)
    stats_refreshing = False

    if not has_cached_stats:
        # Truly cold: no data to serve, must block on the gather.
        # Same single-flight contract as the cold path in /api/items
        # — route through `_kick_background_stats_gather()` so a
        # concurrent `force=true` caller doesn't spawn a parallel
        # `gather_stats()` racing the cold caller on `_stats_cache`
        # mutation. `_kick_background_stats_gather` +
        # `_background_stats_task` are underscore-prefixed module-
        # level globals in main_pkg/apps_routes.py — `from X import
        # *` skips them, and the mutable global must be read via
        # attribute access on every check so we don't race the
        # spawner. (Function-level PyProtectedMember suppression on
        # the def line covers every access in this body.)
        from main_pkg import apps_routes as _apps_routes
        _apps_routes._kick_background_stats_gather()
        _stats_task = _apps_routes._background_stats_task
        if _stats_task is not None:
            try:
                await _stats_task
            except Exception as e:  # noqa: BLE001
                print(f"[stats] cold-cache gather_stats failed: {e}")
    elif cache_stale or force:
        # Stale cache OR force-bypass — kick a refresh in the
        # background. Single-flight: no-op when one is already in
        # flight.
        from main_pkg import apps_routes as _apps_routes
        _apps_routes._kick_background_stats_gather()
    # `stats_refreshing` reflects whether a BACKGROUND gather is
    # actually in flight RIGHT NOW (regardless of why it was
    # kicked). Pre-fix the flag tracked the cache-stale predicate,
    # which evaluated True on every poll when `_gather_stats` takes
    # longer than the TTL (busy fleets routinely take 30-60s for
    # the per-container `/stats` fan-out, vs the default TTL of 30s)
    # — so the spinner span stuck "on" indefinitely. Now the flag
    # only fires while there's a running task; once the task
    # completes the next poll's response carries
    # `stats_refreshing: false` and the spinner clears.
    from main_pkg import apps_routes as _apps_routes_stats
    _bg_stats_task = _apps_routes_stats._background_stats_task
    if _bg_stats_task is not None and not _bg_stats_task.done():
        stats_refreshing = True
    # Swarm agent unhealthy detection — surfaces every node where
    # the per-node `_agent_health` consecutive-failure counter has
    # crossed the operator-tunable threshold. SPA renders a banner
    # in Stacks / Hosts views with operator-fix copy. Empty list on
    # healthy fleet (most common case). See `logic/stats.py` for the
    # detection logic at the end of `gather_stats`.
    from logic.stats import get_agent_health as _get_agent_health
    threshold = tuning.tuning_int(Tunable.SWARM_AGENT_UNHEALTHY_THRESHOLD)
    health_map = _get_agent_health() or {}
    unhealthy_agents = [
        {
            "host": host,
            "fails": int(state.get("fails", 0)),
            "since_ts": float(state.get("since_ts", 0.0)),
            "task_cids": int(state.get("task_cids", 0)),
        }
        for host, state in health_map.items()
        if int(state.get("fails", 0)) >= threshold
    ]
    return {
        "stats": _stats_cache["stats"],
        "ts": _stats_cache["ts"],
        "age": int(now - _stats_cache["ts"]) if _stats_cache["ts"] else None,
        "unhealthy_agents": unhealthy_agents,
        # True when a background _gather_stats was kicked and the
        # response body is from the prior snapshot. SPA's topbar
        # refresh button reads this to surface a "Refreshing…"
        # indicator. Mirrors `/api/items` ``cache_refreshing``.
        "stats_refreshing": stats_refreshing,
    }


@app.get("/api/stats/history")
async def api_stats_history(item_id: str, hours: int = 24):
    """Return sparkline samples for one or more item IDs over the last N hours.

    `item_id` may be comma-separated to fetch multiple in one round-trip
    (the UI batches all visible stacks so it's not N requests per refresh).

    Server-side bucketing kicks in for windows > 2h via the shared
    `_bucket_drawer_series` helper — keeps each item's series at
    ~120 points regardless of window so inline sparklines don't accumulate
    multi-hundred points for the 24h+ default range.
    """
    hours = max(1, min(hours, tuning.tuning_int(Tunable.STATS_HISTORY_DAYS) * 24))
    ids = [s.strip() for s in item_id.split(",") if s.strip()]
    since = time.time() - hours * 3600
    raw_series = _stats_history(ids, since)
    # Late import — `_bucket_drawer_series` is an underscore-prefixed
    # helper in main_pkg.hosts_ssh_routes. Per Python's import-* rules
    # underscore names don't propagate through the star-import chain
    # AND module-level __getattr__ doesn't fire for bare LOAD_GLOBAL
    # inside function bodies, so the only path that lands the symbol
    # in this function's lookup chain is an explicit import. Late-bound
    # to avoid the main → main_pkg cycle. The protected-member warning
    # is intentional — the underscore prefix marks the helper as
    # internal to main_pkg, but cross-module reuse is sanctioned for
    # the late-import pattern; suppress here so the IDE chrome stays
    # clean.
    # noinspection PyProtectedMember
    from main_pkg.hosts_ssh_routes import _bucket_drawer_series
    bucketed = {iid: _bucket_drawer_series(series, hours) for iid, series in raw_series.items()}
    return {
        "since": since,
        "hours": hours,
        "series": bucketed,
    }


# noinspection PyProtectedMember
@app.get("/api/items")
async def api_items(force: bool = False):
    """Return the items / stacks / nodes cache — instant when present.

    Cold-load instant paint (Fix A): when ``_cache`` already holds
    items, this endpoint returns them IMMEDIATELY regardless of the
    TTL or ``force`` flag, and kicks ``_gather()`` into a single-
    flighted background task. The SPA's auto-refresh path always
    sends ``force=true`` per the legacy contract; pre-fix that meant
    every poll cycle blocked on a fresh Portainer fan-out + image-
    digest probe (10-30s). With Fix A the operator sees the prior
    snapshot instantly while the live gather runs invisibly; the next
    poll cycle (or the same SPA fetch if the operator hits ``r``
    again a few seconds later) picks up the fresh state. Cold cache
    (no items in ``_cache`` yet — typically the first
    ``/api/items`` after a fresh container restart, before
    ``items_snapshot`` cross-restart persistence has hydrated) is
    the only path that still awaits ``_gather()``: there's nothing
    cached to serve, so blocking is the only honest option.
    """
    now = time.time()
    cache_ttl = tuning.tuning_int(Tunable.CACHE_TTL_SECONDS)
    has_cached_data = bool(_cache.get("items"))
    cache_stale = (now - _cache["ts"]) > cache_ttl
    cache_refreshing = False

    if not has_cached_data:
        # Truly cold: no data to serve, must block on the gather. Route
        # through `_kick_background_gather()` so the single-flight
        # guard covers this path too — pre-fix the cold-cache branch
        # called `await _gather()` directly without setting
        # `_background_gather_task`, so a concurrent caller hitting
        # `force=true` could spawn a SECOND parallel `_gather()` racing
        # the first one on `_cache` mutation. Now both paths share the
        # same task reference; the cold caller awaits it before
        # responding, the force caller fire-and-forgets and returns
        # the (still-cold but populated by the first caller) cache.
        # `_kick_background_gather` now returns the task directly so
        # the cold-cache caller awaits the same task the single-flight
        # guard tracks (no race between this await and a subsequent
        # `_background_gather_task` read). `_kick_background_gather` +
        # `_background_gather_task` live in main_pkg/apps_routes.py;
        # they enter main's namespace via the tail star-import chain.
        # `_kick_background_gather` + the `_background_gather_task`
        # global both live in main_pkg/apps_routes.py. Underscore-
        # prefixed names AREN'T pulled in by `from X import *`, and
        # `_background_gather_task` is a MUTABLE global (the
        # spawner re-binds it on each kick) so we MUST read it via
        # attribute access on the source module every time — caching
        # the value here would race the spawner.
        from main_pkg import apps_routes as _apps_routes
        gather_task = _apps_routes._kick_background_gather()
        if gather_task is not None:
            try:
                await gather_task
            except Exception as e:  # noqa: BLE001
                # Don't let a transient gather error break the
                # response — the SPA will retry on the next poll, and
                # the next branch happily serves whatever ``_cache``
                # contains (possibly empty, in which case the SPA
                # renders the existing empty-state ladder).
                print(f"[items] cold-cache gather failed: {e}")
    elif cache_stale or force:
        # Stale cache OR force-bypass — kick a refresh in the
        # background. Single-flight: no-op when one is already in
        # flight.
        from main_pkg import apps_routes as _apps_routes
        _apps_routes._kick_background_gather()
    # `cache_refreshing` reflects whether a BACKGROUND gather is
    # actually in flight RIGHT NOW. Pre-fix the flag tracked the
    # cache-stale predicate, which evaluated True on every poll when
    # `_gather` takes longer than the TTL — so the spinner span
    # stuck "on" indefinitely. Now the flag only fires while there's
    # a running task; once the task completes the next poll's
    # response carries `cache_refreshing: false` and the spinner
    # clears.
    from main_pkg import apps_routes as _apps_routes
    _bg_task = _apps_routes._background_gather_task
    if _bg_task is not None and not _bg_task.done():
        cache_refreshing = True

    return {
        "items": _cache["items"],
        "stacks": _cache["stacks"],
        "nodes": _cache["nodes"],
        # Capacity + uptime proxy per node — drives the Nodes view's
        # stat tiles. Keyed by hostname, matches _cache["nodes"]'s values.
        "nodes_info": _cache.get("nodes_info") or {},
        "cached": (now - _cache["ts"] > 1),
        "age": int(now - _cache["ts"]) if _cache["ts"] else None,
        # True when a background gather was kicked and the response
        # body is from the prior snapshot. SPA may render a subtle
        # "refreshing…" hint; the next poll picks up the fresh data.
        "cache_refreshing": cache_refreshing,
        # lets the SPA distinguish "no items + Portainer connected"
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
    """Return the cached item record for `raw_id` (svc:* / ctn:* prefix accepted)."""
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
    name = getattr(user, "username", None) if user is not None else None
    if isinstance(name, str) and name:
        return name
    return (request.headers.get("x-forwarded-user") or "ui").strip() or "ui"


def _request_client_id(request: Optional[Request]) -> Optional[str]:
    """Extract the per-tab client id from the request headers.

    SPA's `auth-fetch.js` attaches `X-OmniGrid-Client-Id: <uuid>` on
    every fetch. Backend write-handlers that publish SSE events should
    pass this to `events.publish(..., client_id=...)` so the originating
    tab can self-filter the echoed event. Returns None when the header
    is absent (bearer-token clients without the wrapper, sampler /
    background tasks that have no Request, third-party callers).

    Trims to a sane length so a malicious / oversized header can't
    poison an event payload. UUIDs are 36 chars; 64 is generous
    headroom for a future format change.
    """
    if request is None:
        return None
    raw = request.headers.get("x-omnigrid-client-id")
    if not raw:
        return None
    val = raw.strip()
    if not val:
        return None
    return val[:64]


def _item_context(container_or_service_id: str) -> tuple[str, Optional[str]]:
    """Resolve (display_name, target_stack) for a cache item by raw or prefix id."""
    for it in _cache["items"]:
        rid = it.get("raw_id") or ""
        if rid.startswith(container_or_service_id) or container_or_service_id.startswith(rid):
            return it.get("name") or container_or_service_id[:12], it.get("stack")
    return container_or_service_id[:12], None


@app.get("/api/registry/release-notes")
async def api_registry_release_notes(
    image: str,
    _admin: AdminUser,
):
    """Best-effort release-notes lookup for an image.

    Walks the registry's OCI labels (`org.opencontainers.image.source` +
    `.version`), detects GitHub-hosted sources, and pulls the matching
    Release's body markdown from GitHub's public API. Falls back to a
    bare source-URL response when the source isn't a recognised host or
    the matching release doesn't exist. Used by the single-update popup
    in the SPA (stacks + standalone containers); bulk updates skip the
    fetch to keep that surface clean.
    """
    image = (image or "").strip()
    if not image:
        raise HTTPException(400, "image query param required")
    try:
        result = await registry.get_release_notes(image)
    except Exception as e:  # noqa: BLE001
        # Log the full exception server-side (operator reads it in
        # Admin → Logs), but return a GENERIC error to the client —
        # the raw exception text can carry stack-trace / internal-path
        # detail (CodeQL py/stack-trace-exposure). The SPA only gates on
        # `ok` / `body` / `source_url` and never renders `error`, so a
        # constant message is sufficient.
        print(f"[release-notes] api lookup failed for {image!r}: {e}")
        result = {"ok": False, "error": "release-notes lookup failed"}
    return result


@app.post("/api/update/stack/{stack_id}")
async def api_update_stack(
    stack_id: int, bg: BackgroundTasks, request: Request,
    _admin: AdminUser,
):
    """Trigger a stack pull + recreate via Portainer's stacks update endpoint."""
    name = f"stack-{stack_id}"
    for s in _cache["stacks"]:
        if s.get("stack_id") == stack_id:
            name = s["name"]
            break
    op = new_op("update_stack", str(stack_id), name,
                target_stack=name, actor=_actor_from(request))
    bg.add_task(_do_update_stack, op, stack_id)
    return {"op_id": op.id}


class StackRetagIn(BaseModel):
    """Optional ``image_repo`` filter — when present, only image: lines whose
    repo matches that prefix are retagged. Otherwise every image: line in
    the compose file flips. Optional ``tag`` field — defaults to
    ``"latest"`` for back-compat with the original "Switch to :latest"
    code path; operators can pass any valid Docker tag (e.g. ``"2"``,
    ``"v2-stable"``) to track a moving sub-version tag instead."""
    image_repo: Optional[str] = None
    tag: Optional[str] = None


class ContainerRetagIn(BaseModel):
    """Optional ``tag`` field — same semantics as ``StackRetagIn.tag``."""
    tag: Optional[str] = None


def _validate_retag_tag(raw: Optional[str]) -> str:
    """Sanitise the operator-supplied retag target tag. Empty / whitespace
    falls back to ``"latest"``. Otherwise: trim, validate against the
    Docker tag charset (``[A-Za-z0-9_.-]`` up to 128 chars per OCI
    spec, can't start with ``.`` or ``-``). Rejects anything else with
    a 400 — typo'd or malicious input shouldn't reach the compose
    rewriter."""
    import re as _re
    t = (raw or "").strip()
    if not t:
        return "latest"
    if len(t) > 128 or not _re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", t):
        raise HTTPException(
            400,
            f"Invalid tag {t!r} — must match Docker tag spec [A-Za-z0-9_][A-Za-z0-9_.-]{{0,127}}",
        )
    return t


@app.post("/api/update/container/{container_id}/retag-latest")
async def api_update_container_retag_latest(
    container_id: str, bg: BackgroundTasks, request: Request,
    body: Optional[ContainerRetagIn] = None,
    *,
    _admin: AdminUser,
):
    """Switch a non-Portainer-managed container's image tag to ``:latest``.

    Sibling of ``api_update_stack_retag_latest`` (case 1) for containers
    that aren't part of a Portainer-managed stack — e.g. Komodo's own
    container, or any container deployed via raw `docker run` /
    external compose. The handler captures the container's full
    Config + HostConfig + Networks via inspect, pulls the new image,
    stops + removes the old container, creates a fresh one with the
    same name + new image + identical config, reconnects extra
    networks, and starts it. Volumes survive because they're named
    refs in the captured HostConfig.

    Operator confirmed via SweetAlert because of the recreate risk —
    transient state (anonymous volumes, ephemeral env, in-memory
    sessions) is lost. Persistent state (named volumes, env vars from
    compose / -e flags, restart policy) survives.
    """
    new_tag = _validate_retag_tag(body.tag if body else None)
    name, stack = _item_context(container_id)
    op = new_op("update_container", container_id, name,
                target_stack=stack, actor=_actor_from(request))
    bg.add_task(_ops_mod.do_retag_container_to_latest, op, container_id, new_tag)
    return {"op_id": op.id, "new_tag": new_tag}


# ----------------------------------------------------------------------------
# Split continuation: ops_routes loads BEFORE settings_routes so its symbols
# join main's namespace ahead of any later decorators.
# ----------------------------------------------------------------------------
from main_pkg.ops_routes import *  # noqa: E402,F401,F403
# ----------------------------------------------------------------------------
# Split continuation: main_pkg.settings_routes loads at this point so its
# symbols join main's namespace BEFORE main_pkg.admin_ai_routes's load.
# ----------------------------------------------------------------------------
from main_pkg.settings_routes import *  # noqa: E402,F401,F403
# ----------------------------------------------------------------------------
# main_pkg.admin_stats_routes ships helper functions (`_ai_supported_providers`,
# `_resolve_ai_fallback_chain`, etc.) that admin_ai_routes + auth_routes +
# settings_routes consume via the module-level __getattr__ resolver. It HAS
# no tail-chain back to a sibling that gets loaded otherwise — both pre-split
# references were inside TYPE_CHECKING-only blocks (False at runtime). Load
# explicitly here so its symbols land in sys.modules before any consumer
# fires; the __getattr__ resolver finds them via dict lookup on this module.
# ----------------------------------------------------------------------------
from main_pkg.admin_stats_routes import *  # noqa: E402,F401,F403
# ----------------------------------------------------------------------------
# Continuation chain: main_pkg.admin_ai_routes (more helpers + routes) →
# main_pkg.hosts_routes (host + admin route handlers) → main_pkg.hosts_provider_routes
# → main_pkg.auth_routes. Each star-import re-exports every symbol.
# ----------------------------------------------------------------------------
from main_pkg.admin_ai_routes import *  # noqa: E402,F401,F403
# ----------------------------------------------------------------------------
# Continuation extracted to `main_pkg.hosts_routes` to keep this file under the
# line-count threshold. Star-import re-exports every symbol
# (including the FastAPI routes registered there).
# ----------------------------------------------------------------------------
from main_pkg.hosts_routes import *  # noqa: E402,F401,F403


# ----------------------------------------------------------------------------
# Cross-module underscore-name eager-wiring.
#
# Python's module-level __getattr__ (PEP 562) handles attribute access
# (`mod.X`) and `from X import Y` lookups, but it does NOT fall through
# for bare LOAD_GLOBAL instructions inside function bodies. The
# `main.py` → `main_pkg/*` split made every function-body reference to
# a sibling-module underscore helper a latent NameError waiting to fire
# the first time the route runs.
#
# Rather than chasing 500s one at a time, this block runs AFTER every
# `main_pkg/*` module has finished loading via the star-import chain
# above + eagerly copies the cross-module underscore-prefixed symbols
# into each consumer's __dict__ so bare LOAD_GLOBAL resolves correctly.
# Map shape: `{consumer_module: [(source_module, [names...]),...]}`.
# Adding a new cross-module bare reference: add the entry here OR add
# an explicit late-import at the call site (same effect).
#
# Audit script: `tmp/audit_underscore_leaks.py` walks every function in
# main + main_pkg/* via AST + cross-references the resulting LOAD_GLOBAL
# refs against each module's runtime __dict__ to surface every leak.
# Re-run after refactors that move underscore helpers between modules.
def _wire_cross_module_underscore_globals() -> None:
    import sys as _sys
    # Rebind map — names from sibling modules that the consumer references
    # under a DIFFERENT alias than the source-side name. Map shape:
    # `{consumer_module: [(src_module, src_name, dst_name), ...]}`. Used
    # primarily for `logic/*` underscore-aliased helpers (`merge_best` →
    # `_merge_best`, `is_meaningful` → `_meaningful`) that consumer
    # functions reference via bare LOAD_GLOBAL. Same drift class as
    # the same-name fixups below; the rename support is the only delta.
    rebinds: dict[str, list[tuple[str, str, str]]] = {
        "main_pkg.apps_routes": [
            ("logic.merge", "merge_best", "_merge_best"),
            ("logic.merge", "is_meaningful", "_meaningful"),
        ],
        "main_pkg.hosts_ssh_routes": [
            ("logic.merge", "merge_best", "_merge_best"),
            ("logic.merge", "is_meaningful", "_meaningful"),
        ],
    }
    fixups: dict[str, list[tuple[str, list[str]]]] = {
        "main_pkg.admin_ai_routes": [
            ("main", ["_cache", "_gather", "_actor_from", "_ops_mod", "_logs",
                      "_NOTIFY_EVENT_NAMES", "_coerce_int_local"]),
            ("main_pkg.admin_stats_routes", ["_SAMPLES_TABLE_HOST_COL", "_resolve_ai_fallback_chain", "_ai_supported_providers"]),
            ("main_pkg.apps_routes", ["_load_hosts_config", "_populate_detected_ports"]),
            ("main_pkg.hosts_routes", ["_clean_vendors_input"]),
        ],
        "main_pkg.admin_stats_routes": [
            ("main", ["_cache"]),
            ("main_pkg.admin_ai_routes", ["_is_asset_inventory_enabled"]),
            ("main_pkg.apps_routes", ["_load_hosts_config"]),
        ],
        "main_pkg.apps_routes": [
            ("main", ["_actor_from", "_ops_mod"]),
            ("main_pkg.hosts_routes", ["_clean_vendors_input", "_clean_host_services",
                                       "_failure_state_for_host",
                                       "_is_provider_paused", "_provider_pause_state_for_host"]),
        ],
        "main_pkg.auth_routes": [
            ("main_pkg.hosts_routes", ["_snmp_vendor_keys_sorted"]),
        ],
        "main_pkg.hosts_provider_routes": [
            ("main_pkg.hosts_routes", ["_clean_vendors_input"]),
        ],
        "main_pkg.hosts_routes": [
            ("main_pkg.apps_routes", ["_get_host_provider_state", "_http_probe_host_cache",
                                      "_merge_one_host", "_shape_host_api_row",
                                      "_snmp_host_cache", "_snmp_host_fail_cache",
                                      "_webmin_host_cache", "_webmin_host_fail_cache",
                                      "_host_provider_lock", "_peek_cached_host_provider_state",
                                      "_populate_detected_ports"]),
        ],
        "main_pkg.hosts_ssh_routes": [
            ("main", ["_cache", "_stats_cache"]),
            ("main_pkg.apps_routes", ["_shape_host_api_row"]),
            ("main_pkg.auth_routes", ["_request_origin"]),
            ("main_pkg.hosts_routes", ["_clean_vendors_input", "_failure_state_for_host",
                                       "_item_samples_in_window", "_provider_pause_state_for_host",
                                       "_sqlite_like_escape"]),
        ],
        "main_pkg.settings_routes": [
            ("main_pkg.admin_stats_routes", ["_settings_version_for_payload", "_ai_supported_providers"]),
            ("main_pkg.apps_routes", ["_sync_host_stats_source"]),
            ("main_pkg.hosts_routes", ["_slugify_action"]),
        ],
        "main_pkg.users_routes": [
            ("main_pkg.auth_routes", ["_AVATAR_DIR", "_request_origin", "_request_rp_id"]),
        ],
    }
    missing: list[str] = []
    for consumer_name, sources in fixups.items():
        cmod = _sys.modules.get(consumer_name)
        if cmod is None:
            missing.append(f"consumer module not loaded: {consumer_name}")
            continue
        for src_name, names in sources:
            smod = _sys.modules.get(src_name)
            if smod is None:
                missing.append(f"source module not loaded: {src_name}")
                continue
            sdict = smod.__dict__
            cdict = cmod.__dict__
            for n in names:
                if n not in sdict:
                    # Don't crash boot — just log to stderr.
                    missing.append(f"{src_name}.{n} not found (consumer: {consumer_name})")
                    continue
                cdict[n] = sdict[n]
    # Apply renames (logic.merge → _merge_best / _meaningful aliases).
    for consumer_name, entries in rebinds.items():
        cmod = _sys.modules.get(consumer_name)
        if cmod is None:
            missing.append(f"consumer module not loaded: {consumer_name}")
            continue
        for src_name, src_attr, dst_attr in entries:
            smod = _sys.modules.get(src_name)
            if smod is None:
                # Logic modules sometimes aren't directly imported by the
                # main chain; import-on-demand here so the rebind always
                # has a source dict to read from.
                try:
                    __import__(src_name)
                    smod = _sys.modules.get(src_name)
                except ImportError:
                    smod = None
            if smod is None:
                missing.append(f"source module not loaded: {src_name}")
                continue
            sdict = smod.__dict__
            if src_attr not in sdict:
                missing.append(f"{src_name}.{src_attr} not found (consumer: {consumer_name})")
                continue
            cmod.__dict__[dst_attr] = sdict[src_attr]
    if missing:
        import sys as _sys2
        for m in missing:
            print(f"[wire_cross_module] {m}", file=_sys2.stderr)


_wire_cross_module_underscore_globals()


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
