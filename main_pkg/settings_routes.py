"""Settings KV admin endpoints — `/api/settings/version`,
`/api/settings` GET + POST, plus the internal
`_api_set_settings_inner` validator. Bridges the admin form
to the `set_setting(...)` write path with the typed
`Settings` enum + the four-place hydration contract.

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
from main import *  # noqa: E402,F401,F403
# IDE contract: PyCharm/Pyright can't trace `from X import *`, so
# every name resolved through the wildcard above would be flagged as
# "Unresolved reference". The explicit imports below resolve at
# runtime too (Python's import system caches; second-import is a dict
# lookup), so they're safe + they silence the IDE in every scope.
from main import (  # noqa: E402,F401 — explicit for IDE; runtime via the * above
    AdminUser,
    HTTPException,
    Request,
    Settings,
    Tunable,
    _NOTIFY_EVENT_NAMES,
    _TOTP_POLICY_DEFAULTS,
    _cache,
    _events,
    _invalidate_totp_policy_cache,
    _ops_mod,
    _request_client_id,
    active_host_stats_providers,
    ai_provider_api_key_key,
    ai_provider_base_url_key,
    ai_provider_enabled_key,
    ai_provider_model_key,
    app,
    auth,
    db_conn,
    get_setting,
    get_setting_bool,
    oidc,
    set_setting,
    tuning,
)

# Sibling names that load AFTER settings_routes in the chain — a real
# import would cycle. TYPE_CHECKING is False at runtime so the IDE
# sees them without triggering the cycle.
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from main_pkg.admin_stats_routes import (  # noqa: F401
        _ai_supported_providers,
        _settings_version_for_payload,
    )
    from main_pkg.apps_routes import (  # noqa: F401
        _sync_host_stats_source,
        invalidate_host_provider_cache,
    )
    from main_pkg.hosts_routes import _slugify_action  # noqa: F401

# `SettingsIn` is defined in main_pkg.ops_routes which loads BEFORE
# settings_routes via the chain, so a real import resolves cleanly.
from main_pkg.ops_routes import SettingsIn  # noqa: E402,F401
import json
from typing import Any, Optional, cast


# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).


@app.get("/api/settings/version")
async def api_get_settings_version(_u: AdminUser):
    """Cheap probe for cross-tab settings-change detection. Returns the
    monotonic `_settings_version` int that's bumped on every
    `set_setting` call. SPA polls this on tab focus + on a slow
    background timer; a version mismatch triggers a full /api/settings
    reload. Avoids re-fetching the full settings blob just to check
    whether anything changed.
    """
    from logic.db import get_settings_version
    return {"version": get_settings_version()}


@app.get("/api/settings")
async def api_get_settings(request: Request):
    """Return the full settings snapshot for the Admin / Settings forms."""
    from logic import portainer as _portainer
    from logic.db import get_settings_version
    # Late import — admin_stats_routes is loaded AFTER settings_routes
    # in main.py's chain; a module-top import would cycle. The
    # function-body import resolves at call time when both modules
    # are loaded. Same pattern as the other late imports above.
    from main_pkg.admin_stats_routes import _ai_supported_providers
    with db_conn() as c:
        a = auth.get_auth_settings(c)
    p = _portainer.get_portainer_settings()
    return {
        # Per-service master switches. Default true so existing
        # deploys don't change behaviour — flip false to short-circuit
        # the service in code AND grey out the inputs in the UI.
        "apprise_enabled": (get_setting(Settings.APPRISE_ENABLED, "true") or "true").lower() == "true",
        "open_meteo_enabled": (get_setting(Settings.OPEN_METEO_ENABLED, "true") or "true").lower() == "true",
        "portainer_enabled": (get_setting(Settings.PORTAINER_ENABLED, "true") or "true").lower() == "true",
        "ssh_enabled": (get_setting(Settings.SSH_ENABLED, "true") or "true").lower() == "true",
        "asset_inventory_enabled": (get_setting(Settings.ASSET_INVENTORY_ENABLED, "true") or "true").lower() == "true",
        "apprise_url": get_setting(Settings.APPRISE_URL),
        "apprise_tag": get_setting(Settings.APPRISE_TAG),
        "swarm_autoheal_action": (get_setting(Settings.SWARM_AUTOHEAL_ACTION, "notify") or "notify").lower(),
        # First-boot auto-bootstrap toggle for the default
        # swarm_agent_health schedule. Operators flip this to false
        # to opt out before the bootstrap-done latch trips.
        "swarm_autoheal_bootstrap_enabled": (
                                                get_setting(Settings.SWARM_AUTOHEAL_BOOTSTRAP_ENABLED, "true") or "true"
                                            ).lower() != "false",
        # Per-event notification toggles. Resolved through
        # get_setting_bool so the frontend gets clean booleans (no
        # client-side string parsing). Default true preserves the
        # legacy "send everything" behaviour for existing deploys.
        "notify_event_stack_update_success": get_setting_bool(Settings.NOTIFY_EVENT_STACK_UPDATE_SUCCESS, True),
        "notify_event_stack_update_failure": get_setting_bool(Settings.NOTIFY_EVENT_STACK_UPDATE_FAILURE, True),
        "notify_event_container_update_success": get_setting_bool(Settings.NOTIFY_EVENT_CONTAINER_UPDATE_SUCCESS, True),
        "notify_event_container_update_failure": get_setting_bool(Settings.NOTIFY_EVENT_CONTAINER_UPDATE_FAILURE, True),
        "notify_event_container_restart_success": get_setting_bool(Settings.NOTIFY_EVENT_CONTAINER_RESTART_SUCCESS, True),
        "notify_event_container_restart_failure": get_setting_bool(Settings.NOTIFY_EVENT_CONTAINER_RESTART_FAILURE, True),
        "notify_event_container_remove_success": get_setting_bool(Settings.NOTIFY_EVENT_CONTAINER_REMOVE_SUCCESS, True),
        "notify_event_container_remove_failure": get_setting_bool(Settings.NOTIFY_EVENT_CONTAINER_REMOVE_FAILURE, True),
        "notify_event_service_restart_success": get_setting_bool(Settings.NOTIFY_EVENT_SERVICE_RESTART_SUCCESS, True),
        "notify_event_service_restart_failure": get_setting_bool(Settings.NOTIFY_EVENT_SERVICE_RESTART_FAILURE, True),
        "notify_event_swarm_agent_restart_success": get_setting_bool(Settings.NOTIFY_EVENT_SWARM_AGENT_RESTART_SUCCESS, True),
        "notify_event_swarm_agent_restart_failure": get_setting_bool(Settings.NOTIFY_EVENT_SWARM_AGENT_RESTART_FAILURE, True),
        "notify_event_swarm_agent_unhealthy": get_setting_bool(Settings.NOTIFY_EVENT_SWARM_AGENT_UNHEALTHY, True),
        "notify_event_swarm_agent_recovered": get_setting_bool(Settings.NOTIFY_EVENT_SWARM_AGENT_RECOVERED, True),
        "notify_event_prune_success": get_setting_bool(Settings.NOTIFY_EVENT_PRUNE_SUCCESS, True),
        "notify_event_prune_failure": get_setting_bool(Settings.NOTIFY_EVENT_PRUNE_FAILURE, True),
        # Security event — default OFF (login spam is noisy; opt-in).
        "notify_event_user_login": get_setting_bool(Settings.NOTIFY_EVENT_USER_LOGIN),
        # System event — fires when host_metrics_sampler auto-
        # pauses a host after the failure window. Default ON.
        "notify_event_host_paused": get_setting_bool(Settings.NOTIFY_EVENT_HOST_PAUSED, True),
        # Port-scan provider — default OFF so first-run scanner doesn't
        # flood. Operators flip on after triaging the initial baseline.
        "notify_event_port_scan_new_port": get_setting_bool(Settings.NOTIFY_EVENT_PORT_SCAN_NEW_PORT),
        "notify_event_http_probe_failure": get_setting_bool(Settings.NOTIFY_EVENT_HTTP_PROBE_FAILURE),
        "notify_event_service_probe_failure": get_setting_bool(Settings.NOTIFY_EVENT_SERVICE_PROBE_FAILURE),
        "service_probe_enabled": get_setting_bool(Settings.SERVICE_PROBE_ENABLED),
        # TOTP audit-row INSERT failure — warn when audit trail missing.
        "notify_event_totp_audit_log_failed": get_setting_bool(Settings.NOTIFY_EVENT_TOTP_AUDIT_LOG_FAILED, True),
        # Drawer auto-fix — VXLAN overlay cleanup outcomes.
        "notify_event_overlay_cleanup_success": get_setting_bool(Settings.NOTIFY_EVENT_OVERLAY_CLEANUP_SUCCESS, True),
        "notify_event_overlay_cleanup_failure": get_setting_bool(Settings.NOTIFY_EVENT_OVERLAY_CLEANUP_FAILURE, True),
        # Per-medium master switches. Defaults from
        # NOTIFY_MEDIUM_DEFAULTS (both ON for back-compat); operators
        # flip individually from Admin → Notifications.
        "notify_medium_app": get_setting_bool(
            Settings.NOTIFY_MEDIUM_APP,
            _ops_mod.NOTIFY_MEDIUM_DEFAULTS.get("app", True),
        ),
        "notify_medium_apprise": get_setting_bool(
            Settings.NOTIFY_MEDIUM_APPRISE,
            _ops_mod.NOTIFY_MEDIUM_DEFAULTS.get("apprise", True),
        ),
        # Telegram medium (Phase 1: send-only). Defaults OFF (requires
        # bot-token + chat-id config before it can fire).
        "notify_medium_telegram": get_setting_bool(
            Settings.NOTIFY_MEDIUM_TELEGRAM,
            _ops_mod.NOTIFY_MEDIUM_DEFAULTS.get("telegram", False),
        ),
        "telegram_chat_id": get_setting(Settings.TELEGRAM_CHAT_ID),
        "telegram_thread_id": get_setting(Settings.TELEGRAM_THREAD_ID),
        "telegram_verify_tls": (get_setting(Settings.TELEGRAM_VERIFY_TLS, "true") or "true").lower() == "true",
        # Telegram Bot API base URL — blank = fall back to upstream
        # default (https://api.telegram.org). Surfaced for the admin
        # form so self-hosted Bot API gateways are operator-tunable.
        "telegram_api_base": get_setting(Settings.TELEGRAM_API_BASE),
        # Write-only: surface only a `_set` flag, never the raw token.
        "telegram_bot_token_set": bool((get_setting(Settings.TELEGRAM_BOT_TOKEN) or "").strip()),
        # Diagnostics block — surfaces "what's missing for the listener
        # to fire" in the Admin form so operators don't have to grep
        # Admin → Logs for `[telegram_listener] unauthorized update:
        # no telegram_chat_id configured`. Each flag is True iff that
        # specific setting is configured (non-empty for strings, set
        # for write-only credentials). The SPA's Telegram section
        # reads these to render an inline "Missing: <list>" hint
        # under the form.
        "telegram_diagnostics": {
            "bot_token_configured": bool((get_setting(Settings.TELEGRAM_BOT_TOKEN) or "").strip()),
            "chat_id_configured": bool((get_setting(Settings.TELEGRAM_CHAT_ID) or "").strip()),
            "listener_enabled": get_setting_bool(Settings.TELEGRAM_LISTENER_ENABLED),
            "notify_medium_enabled": get_setting_bool(
                Settings.NOTIFY_MEDIUM_TELEGRAM,
                _ops_mod.NOTIFY_MEDIUM_DEFAULTS.get("telegram", False),
            ),
        },
        # Phase 2 — inbound command listener config.
        "telegram_listener_enabled": get_setting_bool(Settings.TELEGRAM_LISTENER_ENABLED),
        "telegram_allow_destructive": get_setting_bool(Settings.TELEGRAM_ALLOW_DESTRUCTIVE),
        "telegram_authorized_user_ids": get_setting(Settings.TELEGRAM_AUTHORIZED_USER_IDS),
        # TOTP / 2FA policy. Five fields driving the multi-step
        # login flow + Profile enrolment guards + Admin -> Users action
        # enablement. Defaults preserve "no 2FA required" semantics so
        # an upgrade is a no-op until the operator opts in.
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
        "totp_lockout_max_failures": int(get_setting(
            Settings.TOTP_LOCKOUT_MAX_FAILURES,
            str(_TOTP_POLICY_DEFAULTS.get("totp_lockout_max_failures", 5)),
        ) or _TOTP_POLICY_DEFAULTS.get("totp_lockout_max_failures", 5)),
        "totp_lockout_minutes": int(get_setting(
            Settings.TOTP_LOCKOUT_MINUTES,
            str(_TOTP_POLICY_DEFAULTS.get("totp_lockout_minutes", 15)),
        ) or _TOTP_POLICY_DEFAULTS.get("totp_lockout_minutes", 15)),
        "passkeys_allowed": get_setting_bool(
            Settings.PASSKEYS_ALLOWED, bool(_TOTP_POLICY_DEFAULTS.get("passkeys_allowed", True)),
        ),
        # Open-Meteo upstream (Admin → General). DEPRECATED — kept
        # for legacy seed round-trip; the topbar widget + sampler
        # now consume the WeatherAPI.com block below.
        "open_meteo_url": get_setting(Settings.OPEN_METEO_URL) or "",
        # Weather (Admin → Weather). Dual-provider — `provider`
        # selects between "open-meteo" (default, no key, no moon)
        # and "weatherapi" (free key, full moon astronomy).
        # `supports_moon` is a synthesised boolean computed from
        # the active provider — the SPA's moon-widget gate +
        # AI palette moon-question handling read this directly.
        # API key follows the write-only `_set` flag pattern; the
        # SPA only ever sees `api_key_set: bool`.
        "weather": {
            "enabled": get_setting_bool(Settings.WEATHER_ENABLED, default=False),
            "provider": (lambda v: ("weatherapi" if v == "weatherapi" else "open-meteo"))(
                (get_setting(Settings.WEATHER_PROVIDER) or "").strip().lower()
            ),
            "supports_moon": (
                (get_setting(Settings.WEATHER_PROVIDER) or "").strip().lower()
                == "weatherapi"
            ),
            "api_base_url": get_setting(Settings.WEATHER_API_BASE_URL) or "",
            "api_key_set": bool((get_setting(Settings.WEATHER_API_KEY) or "").strip()),
            "default_label": get_setting(Settings.WEATHER_DEFAULT_LABEL) or "",
            "default_lat": get_setting(Settings.WEATHER_DEFAULT_LAT) or "",
            "default_lon": get_setting(Settings.WEATHER_DEFAULT_LON) or "",
        },
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
                ))(g["ssh"] if isinstance(g.get("ssh"), dict) else {})}
                for g in groups if isinstance(g, dict)
            ])(json.loads(raw) if (raw or "").strip() else [])
        ))(get_setting(Settings.HOST_GROUPS) or ""),
        # Asset inventory (<asset-api-host>). Secret is write-only — UI sees
        # a `_set` flag only. Other fields round-trip in the clear.
        "asset_inventory": {
            "auth_mode": (get_setting(Settings.ASSET_INVENTORY_AUTH_MODE) or "oauth2"),
            "base_url": get_setting(Settings.ASSET_INVENTORY_BASE_URL) or "",
            "token_url": get_setting(Settings.ASSET_INVENTORY_TOKEN_URL) or "",
            "client_id": get_setting(Settings.ASSET_INVENTORY_CLIENT_ID) or "",
            "client_secret_set": bool(get_setting(Settings.ASSET_INVENTORY_CLIENT_SECRET)),
            "scope": get_setting(Settings.ASSET_INVENTORY_SCOPE) or "",
            "lifetime_token_set": bool(get_setting(Settings.ASSET_INVENTORY_LIFETIME_TOKEN)),
            "service": get_setting(Settings.ASSET_INVENTORY_SERVICE) or "",
            "action": get_setting(Settings.ASSET_INVENTORY_ACTION) or "",
            "min_value": (lambda v: int(v) if (v or "").strip().lstrip("-").isdigit() else None)(
                get_setting(Settings.ASSET_INVENTORY_MIN_VALUE)),
            "max_value": (lambda v: int(v) if (v or "").strip().lstrip("-").isdigit() else None)(
                get_setting(Settings.ASSET_INVENTORY_MAX_VALUE)),
            "edit_url_template": get_setting(Settings.ASSET_INVENTORY_EDIT_URL_TEMPLATE) or "",
            # / — TLS verification toggle. Default True.
            "verify_tls": (get_setting(Settings.ASSET_INVENTORY_VERIFY_TLS, "true") or "true").strip().lower() != "false",
        },
        # AI integration — Stage 1 admin surface. Per-provider api_key
        # round-trips as a `_set` boolean only; everything else returns
        # in the clear so the form can render existing values.
        # ``active_provider`` defaults to "claude" so a fresh deploy has
        # a sensible default selected when the master toggle is flipped.
        # ``defaults`` carries the canonical model id + API host for each
        # provider so the SPA can pre-fill empty fields on first edit
        # (admins can override per-deployment instead of typing every
        # URL from scratch). The defaults block is NOT operator-tunable
        # — it ships with the canonical endpoints; admins override by
        # entering different values into the form, which then persist
        # over the default. If a provider rotates its public host, the
        # operator can keep using the old saved value OR clear the
        # field to re-pick the new default on the next form load.
        "ai": {
            "enabled": (get_setting(Settings.AI_ENABLED, "false") or "false").lower() == "true",
            "active_provider": (get_setting(Settings.AI_ACTIVE_PROVIDER) or "claude"),
            # max_tokens + fallback_max_depth are TUNABLES (DB > env >
            # default with bounds-clamp). /api/me reads them via
            # `tuning_int(...)` too, so the two endpoints agree on the
            # same value for the same effective deploy state. Pre-fix
            # this read via `get_setting(Settings.AI_MAX_TOKENS, ...)` while
            # the consumers + /api/me read via `tuning_int(...)` — same
            # field, two DB keys, /api/settings and /api/me silently
            # diverged.
            "max_tokens": tuning.tuning_int(Tunable.AI_MAX_TOKENS),
            # Provider fallback chain config — opt-in, off by default so
            # existing deploys don't suddenly start cost-shifting traffic
            # to alternate providers without operator awareness.
            "fallback_enabled": (get_setting(Settings.AI_FALLBACK_ENABLED, "false") or "false").lower() == "true",
            "fallback_order": get_setting(Settings.AI_FALLBACK_ORDER) or "",
            "fallback_max_depth": tuning.tuning_int(Tunable.AI_FALLBACK_MAX_DEPTH),
            "providers": {
                name: {
                    "enabled": (get_setting(ai_provider_enabled_key(name), "false") or "false").lower() == "true",
                    "model": get_setting(ai_provider_model_key(name)) or "",
                    "base_url": get_setting(ai_provider_base_url_key(name)) or "",
                    "api_key_set": bool(get_setting(ai_provider_api_key_key(name))),
                }
                for name in _ai_supported_providers()
            },
            "defaults": {
                "claude": {"model": "claude-opus-4-7", "base_url": "https://api.anthropic.com"},
                "gemini": {"model": "gemini-2.5-pro", "base_url": "https://generativelanguage.googleapis.com"},
                "chatgpt": {"model": "gpt-4o", "base_url": "https://api.openai.com"},
                "deepseek": {"model": "deepseek-chat", "base_url": "https://api.deepseek.com"},
            },
        },
        # Public-IP lookup block — standalone subsystem (NOT AI). Its
        # own Admin → Public IP section owns the toggle + tunables.
        # The AI palette + Telegram /ip command both consume the
        # module but the feature is independent.
        "public_ip": {
            "enabled": bool(tuning.tuning_int(Tunable.PUBLIC_IP_ENABLED)),
            "cache_ttl_seconds": tuning.tuning_int(Tunable.PUBLIC_IP_CACHE_TTL_SECONDS),
            "fetch_timeout_seconds": tuning.tuning_int(Tunable.PUBLIC_IP_FETCH_TIMEOUT_SECONDS),
        },
        "portainer_public_url": get_setting(Settings.PORTAINER_PUBLIC_URL, str(p.get("portainer_url") or "")),
        "backup_retention_count": tuning.tuning_int(Tunable.BACKUP_RETENTION_COUNT),
        "scheduler_timezone": get_setting(Settings.SCHEDULER_TIMEZONE) or "",
        # Host-drawer admin debug panel visibility (Admin → Hosts toggle).
        # Default true — preserves the legacy behaviour for existing
        # deploys that haven't touched the setting. Operators who don't
        # use the raw-JSON dump can flip to false to declutter the
        # drawer without losing other admin-only affordances.
        "debug_panel_enabled": (
            (get_setting(Settings.DEBUG_PANEL_ENABLED, "true") or "true").lower() == "true"
        ),
        "node_exporter": {
            "enabled": (get_setting(Settings.NODE_EXPORTER_ENABLED, "false") or "false").lower() == "true",
            "url_template": get_setting(Settings.NODE_EXPORTER_URL_TEMPLATE, "http://{host}:9100/metrics"),
            "overrides": json.loads(get_setting(Settings.NODE_EXPORTER_OVERRIDES, "{}") or "{}"),
        },
        # Host-stats sources — stored as CSV so multiple providers can
        # be enabled at once (Beszel for cross-platform real-time stats
        # + node-exporter for Linux-host detail). The read-back shim
        # keeps single-value legacy rows ("beszel" / "node_exporter")
        # working without a migration; upgrades with only
        # ``node_exporter_enabled`` set default to that one source.
        # Both fields derive from the single helper. The
        # legacy string form is kept for back-compat with older SPA
        # bundles that read ``host_stats_source`` instead of the list.
        "host_stats_source": (",".join(sorted(active_host_stats_providers()))
                              or "none"),
        "host_stats_sources": sorted(active_host_stats_providers()),
        # Beszel Hub — password is write-only. UI only learns "is it set".
        "beszel": {
            "hub_url": get_setting(Settings.BESZEL_HUB_URL),
            "identity": get_setting(Settings.BESZEL_IDENTITY),
            "password_set": bool(get_setting(Settings.BESZEL_PASSWORD)),
            "verify_tls": (get_setting(Settings.BESZEL_VERIFY_TLS, "true") or "true").lower() == "true",
            "aliases": json.loads(get_setting(Settings.BESZEL_ALIASES, "{}") or "{}"),
        },
        # Pulse — token is write-only like Beszel's password.
        "pulse": {
            "url": get_setting(Settings.PULSE_URL),
            "token_set": bool(get_setting(Settings.PULSE_TOKEN)),
            "verify_tls": (get_setting(Settings.PULSE_VERIFY_TLS, "true") or "true").lower() == "true",
            "aliases": json.loads(get_setting(Settings.PULSE_ALIASES, "{}") or "{}"),
        },
        # Webmin — password is write-only (same _set-flag convention as
        # beszel_password / pulse_token / portainer_api_key). ``aliases``
        # is Docker hostname → Miniserv base URL per host.
        "webmin": {
            "url": get_setting(Settings.WEBMIN_URL),
            "user": get_setting(Settings.WEBMIN_USER),
            "password_set": bool(get_setting(Settings.WEBMIN_PASSWORD)),
            "verify_tls": (get_setting(Settings.WEBMIN_VERIFY_TLS, "false") or "false").lower() == "true",
            "aliases": json.loads(get_setting(Settings.WEBMIN_ALIASES, "{}") or "{}"),
        },
        # Ping — no secrets, so fields round-trip in the clear.
        # ``has_icmp_support`` reflects whether ``icmplib`` is importable
        # (the container's Python may not have it); the SPA uses this
        # to disable the ICMP toggle with a hint when the package is
        # missing.
        "ping": {
            "enabled": get_setting_bool(Settings.PING_ENABLED),
            "default_port": tuning.tuning_int(Tunable.PING_DEFAULT_PORT),
            "use_icmp": get_setting_bool(Settings.PING_USE_ICMP),
            "has_icmp_support": (lambda: __import__("logic.ping", fromlist=["has_icmp_support"]).has_icmp_support())(),
        },
        # Port-scan provider — on-demand TCP scanner. Defaults are
        # global; per-host overrides land on `hosts_config[].port_scan`.
        # Default ports list is empty here so the SPA can show "using
        # built-in top-100" placeholder when blank; the scanner
        # falls back to `port_scanner.DEFAULT_PORTS` when given an
        # empty list.
        "port_scan": {
            "enabled": get_setting_bool(Settings.PORT_SCAN_ENABLED),
            "default_ports": get_setting(Settings.PORT_SCAN_DEFAULT_PORTS) or "",
            # Per-port timeout + concurrency now flow through TUNABLES
            # (tuning_port_scan_default_timeout_seconds /
            # tuning_port_scan_default_concurrency). The plain-settings
            # `port_scan_default_timeout_seconds` /
            # `port_scan_default_concurrency` rows from the legacy POST
            # path migrate naturally — `tuning_int` resolves DB > env >
            # default and the legacy rows continue to seed the DB
            # value. Per the No-static-config rule.
            "default_timeout": tuning.tuning_int(Tunable.PORT_SCAN_DEFAULT_TIMEOUT_SECONDS),
            "default_concurrency": tuning.tuning_int(Tunable.PORT_SCAN_DEFAULT_CONCURRENCY),
            # Stage 2 (UDP). UDP runs under the master `enabled` toggle
            # (operator-flagged 2026-05-10 to remove the separate
            # `udp_enabled` flag). Field kept on the response for
            # back-compat with older SPA builds; new SPA ignores it.
            "udp_enabled": True,
            "udp_default_ports": get_setting(Settings.PORT_SCAN_UDP_DEFAULT_PORTS) or "",
            "udp_default_timeout": tuning.tuning_int(Tunable.PORT_SCAN_UDP_DEFAULT_TIMEOUT_SECONDS),
            "udp_default_concurrency": tuning.tuning_int(Tunable.PORT_SCAN_UDP_DEFAULT_CONCURRENCY),
        },
        # SNMP. v3 secret keys follow the write-only ``_set``
        # flag contract; community, version, port, aliases round-trip
        # in the clear (community is technically a credential but it's
        # not a SECRET in the same sense — many operators want to see
        # the configured value to confirm). ``has_snmp_support`` mirrors
        # the Ping pattern so the SPA's master toggle disables with a
        # "package missing" hint when pysnmp isn't installed.
        "snmp": {
            "default_community": get_setting(Settings.SNMP_DEFAULT_COMMUNITY, "public") or "public",
            "default_version": (get_setting(Settings.SNMP_DEFAULT_VERSION, "v2c") or "v2c").strip().lower(),
            "default_port": tuning.tuning_int(Tunable.SNMP_DEFAULT_PORT),
            "v3_user": get_setting(Settings.SNMP_V3_USER) or "",
            "v3_auth_key_set": bool(get_setting(Settings.SNMP_V3_AUTH_KEY)),
            "v3_priv_key_set": bool(get_setting(Settings.SNMP_V3_PRIV_KEY)),
            "aliases": json.loads(get_setting(Settings.SNMP_ALIASES, "{}") or "{}"),
            "has_snmp_support": (lambda: __import__("logic.snmp", fromlist=["has_snmp_support"]).has_snmp_support())(),
            # surface the actual ImportError text from logic.snmp's
            # module-level import block so the SPA's hint can show the
            # ROOT CAUSE instead of just "package not installed". Empty
            # string when pysnmp imported cleanly. Operators don't have
            # to grep the server log to figure out which symbol/path
            # is missing — the hint banner shows it inline.
            "import_error": (lambda: getattr(
                __import__("logic.snmp", fromlist=["_SNMP_IMPORT_ERROR"]),
                "_SNMP_IMPORT_ERROR", "",
            ))(),
        },
        # HTTP / TLS / DNS probe — seventh host-stats provider.
        # ``aliases`` is a CSV string (not JSON) because the value is
        # simpler than the other aliases maps + the operator types it
        # directly in the UI textarea rather than an editor — keeping
        # it CSV avoids the JSON-escape round-trip.
        "http_probe": {
            "enabled": get_setting_bool(Settings.HTTP_PROBE_ENABLED),
            "aliases": get_setting(Settings.HTTP_PROBE_ALIASES) or "",
        },
        # Per-provider chip colour overrides. Empty string means
        # "use the SPA's built-in default" — the SPA's `providerColor()`
        # helper falls back to the same default constant. Round-tripped
        # in the clear (not a secret).
        "provider_color_beszel": get_setting(Settings.PROVIDER_COLOR_BESZEL) or "",
        "provider_color_pulse": get_setting(Settings.PROVIDER_COLOR_PULSE) or "",
        "provider_color_node_exporter": get_setting(Settings.PROVIDER_COLOR_NODE_EXPORTER) or "",
        "provider_color_webmin": get_setting(Settings.PROVIDER_COLOR_WEBMIN) or "",
        "provider_color_ping": get_setting(Settings.PROVIDER_COLOR_PING) or "",
        "provider_color_snmp": get_setting(Settings.PROVIDER_COLOR_SNMP) or "",
        "provider_color_http_probe": get_setting(Settings.PROVIDER_COLOR_HTTP_PROBE) or "",
        "provider_color_service_probe": get_setting(Settings.PROVIDER_COLOR_SERVICE_PROBE) or "",
        # SSH console — global defaults (Admin → SSH). Secrets
        # redacted per CLAUDE.md's ``_set`` flag contract: the browser
        # learns only whether a private key / passphrase has been set.
        # Known-hosts is non-secret (paste-and-forget public data) so
        # the full blob round-trips. Destructive patterns are operator-
        # editable regex — shown verbatim for the textarea.
        "ssh": {
            "user": get_setting(Settings.SSH_DEFAULT_USER) or "",
            "port": tuning.tuning_int(Tunable.SSH_DEFAULT_PORT),
            "private_key_set": bool(get_setting(Settings.SSH_DEFAULT_PRIVATE_KEY)),
            "passphrase_set": bool(get_setting(Settings.SSH_DEFAULT_PRIVATE_KEY_PASSPHRASE)),
            "password_set": bool(get_setting(Settings.SSH_DEFAULT_PASSWORD)),
            "fqdn_suffix": get_setting(Settings.SSH_FQDN_SUFFIX) or "",
            "known_hosts": get_setting(Settings.SSH_DEFAULT_KNOWN_HOSTS) or "",
            "custom_actions": (lambda raw:
                               (json.loads(raw) if (raw or "").strip() else [])
                               )(get_setting(Settings.SSH_CUSTOM_ACTIONS)),
            "destructive_patterns": (
                get_setting(Settings.SSH_DESTRUCTIVE_PATTERNS) or ""
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
        # Settings-version int for cheap cross-tab change detection.
        # Bumped on every set_setting call. SPA reads this once on
        # /api/settings load + polls /api/settings/version cheaply.
        "_version": get_settings_version(),
    }


@app.post("/api/settings")
async def api_set_settings(
    s: SettingsIn,
    request: Request,
    _admin: AdminUser,
):
    """Partial-update the settings KV; `None` fields keep their current value."""
    from logic import portainer as _portainer
    from logic.db import defer_settings_version_bump, batch_settings_writes
    # Multi-field Saves call set_setting N times. Without the defer
    # context, each call bumps `_settings_version` and other tabs see
    # N version mismatches → N reloads of /api/settings + /api/me per
    # Save. The defer context collapses to ONE bump at end-of-request.
    # batch_settings_writes additionally buffers the row WRITES into ONE
    # INSERT OR REPLACE transaction at context exit (excluded high-
    # frequency keys bypass the buffer so a background tick during the
    # handler's `await`s can't mis-buffer). The two contexts compose
    # cleanly: defer handles the cross-tab notification, batch handles
    # the DB transaction count.
    with defer_settings_version_bump():
        with batch_settings_writes():
            result = await _api_set_settings_inner(s, request, _portainer)
    # Audit row — record which top-level fields were touched (Pydantic
    # `model_dump(exclude_unset=True)` keys). Secret-suffix fields are
    # already filtered server-side; the audit row carries field NAMES
    # only, never values. Skip when the operator submitted an empty body.
    try:
        # Explicit `str(...)` map narrows from Pydantic's `dict[str, Any]`
        # key view (which pyright widens to `list[Any]`) to `list[str]`
        # so `', '.join(...)` doesn't trip "Unexpected type" diagnostics.
        touched: list[str] = sorted(str(k) for k in s.model_dump(exclude_unset=True).keys())
        if touched:
            with db_conn() as c:
                _ops_mod.write_admin_audit(
                    c, "settings_update",
                    target_kind="settings",
                    actor=_admin.username,
                    message=f"Updated settings: {', '.join(touched[:30])}"
                            + (f" (+{len(touched) - 30} more)" if len(touched) > 30 else ""),
                )
    except Exception as _e:
        print(f"[ops] settings_update audit-row skipped: {_e!r}")
    return result


# noinspection PyTypeChecker,PyUnresolvedReferences
def _set_bool_settings(s, keys) -> None:
    """Validate + persist a set of boolean-shaped settings from a
    SettingsIn body. Each value must be ``"true"`` / ``"false"`` /
    ``""`` (empty clears; read-side falls back to the documented
    default-true via ``get_setting_bool``). Raises ``HTTPException``
    on malformed input. Used by the per-event + per-medium toggle
    validators in ``/api/settings`` POST — both loops shared a 10-line
    body before extraction."""
    for k in keys:
        v = getattr(s, k, None)
        if v is None:
            continue
        norm = (v or "").strip().lower()
        if norm not in ("", "true", "false"):
            raise HTTPException(
                status_code=400,
                detail=f"{k} must be 'true', 'false', or '' (clear).",
            )
        set_setting(k, norm)


def _set_url_secondary_password_tls(
    s, *,
    url_field: str, url_setting,
    password_field: str, password_setting,
    verify_tls_field: str, verify_tls_setting,
    secondary_field: Optional[str] = None, secondary_setting=None,
) -> None:
    """Persist the standard URL / secondary-identity / password /
    verify_tls quartet for a provider credentials block. Used by the
    Beszel (url + identity + password + verify_tls) / Pulse (url +
    token + verify_tls — no secondary) / Webmin (url + user +
    password + verify_tls) blocks in ``_api_set_settings_inner``.

    Contract:
      * URL is trimmed + trailing-slash stripped (clean concat
        downstream).
      * ``secondary_field`` / ``secondary_setting`` are optional —
        Pulse skips this slot since it has no user/identity field.
      * Password follows the keep-current-if-blank contract (the
        operator-visible "leave blank to keep current" UX shared by
        every other secret in the settings table).
      * verify_tls persists as a "true"/"false" string to match the
        boolean convention every other tls toggle uses.
    """
    # `getattr(s, X, None)` types as `Any | None` — narrow each
    # field to a concrete str (or bool for verify_tls) at the use
    # site so PyCharm's set_setting() arg-type check passes without
    # blanket suppression.
    url_val = getattr(s, url_field, None)
    if isinstance(url_val, str):
        set_setting(url_setting, url_val.strip().rstrip("/"))
    if secondary_field is not None and secondary_setting is not None:
        sec_val = getattr(s, secondary_field, None)
        if isinstance(sec_val, str):
            set_setting(secondary_setting, sec_val.strip())
    pw_val = getattr(s, password_field, None)
    if isinstance(pw_val, str) and pw_val.strip() != "":
        set_setting(password_setting, pw_val)
    vtls_val = getattr(s, verify_tls_field, None)
    if vtls_val is not None:
        set_setting(verify_tls_setting, "true" if vtls_val else "false")


def _clean_alias_dict(d, value_transform=None) -> dict:
    """Trim + drop-empty for alias maps (Docker-hostname → provider-
    target). Optional ``value_transform`` lets the caller add extra
    normalization on the value side — webmin's URLs need a trailing-
    slash strip, but pulse / beszel / snmp aliases store bare target
    strings. Used by the four ``X_aliases`` blocks in
    `_api_set_settings_inner` that previously repeated the same
    dict-comprehension verbatim."""
    out = {}
    for k, v in (d or {}).items():
        ks, vs = str(k).strip(), str(v).strip()
        if not ks or not vs:
            continue
        out[ks] = value_transform(vs) if value_transform else vs
    return out


async def _api_set_settings_inner(s: "SettingsIn", request: Request, _portainer) -> dict:
    # Late import — `_ai_supported_providers` lives in
    # `main_pkg.admin_stats_routes` which is loaded AFTER this module
    # in the main.py chain; a module-top import would cycle. Function-
    # body late-binding resolves at call time when both modules are
    # imported. Same pattern used elsewhere in this module for
    # `_ai_supported_providers` (api_get_settings above).
    from main_pkg.admin_stats_routes import _ai_supported_providers
    # Per-service master switches. Persisted as "true" / "false"
    # strings to match every other boolean toggle in the settings table.
    if s.apprise_enabled is not None:
        set_setting(Settings.APPRISE_ENABLED, "true" if s.apprise_enabled else "false")
    if s.open_meteo_enabled is not None:
        set_setting(Settings.OPEN_METEO_ENABLED, "true" if s.open_meteo_enabled else "false")
    if s.portainer_enabled is not None:
        set_setting(Settings.PORTAINER_ENABLED, "true" if s.portainer_enabled else "false")
    if s.ssh_enabled is not None:
        set_setting(Settings.SSH_ENABLED, "true" if s.ssh_enabled else "false")
    if s.asset_inventory_enabled is not None:
        set_setting(Settings.ASSET_INVENTORY_ENABLED,
                    "true" if s.asset_inventory_enabled else "false")
    if s.apprise_url is not None:
        set_setting(Settings.APPRISE_URL, s.apprise_url)
    if s.apprise_tag is not None:
        set_setting(Settings.APPRISE_TAG, s.apprise_tag)
    # Telegram medium (Phase 1: send-only). Bot token follows the write-
    # only secret contract — blank / whitespace = keep current, any
    # non-empty value overwrites. Chat / thread IDs are plain strings.
    if s.telegram_bot_token is not None:
        token = (s.telegram_bot_token or "").strip()
        if token:
            set_setting(Settings.TELEGRAM_BOT_TOKEN, token)
    if s.telegram_chat_id is not None:
        set_setting(Settings.TELEGRAM_CHAT_ID, (s.telegram_chat_id or "").strip())
    if s.telegram_thread_id is not None:
        set_setting(Settings.TELEGRAM_THREAD_ID, (s.telegram_thread_id or "").strip())
    if s.telegram_verify_tls is not None:
        verify_str = (s.telegram_verify_tls or "true").strip().lower()
        set_setting(Settings.TELEGRAM_VERIFY_TLS, "true" if verify_str != "false" else "false")
    if s.telegram_api_base is not None:
        set_setting(Settings.TELEGRAM_API_BASE, (s.telegram_api_base or "").strip())
    if s.notify_medium_telegram is not None:
        b = (s.notify_medium_telegram or "").strip().lower()
        set_setting(Settings.NOTIFY_MEDIUM_TELEGRAM, "true" if b == "true" else "false")
    # Phase 2 — listener config.
    if s.telegram_listener_enabled is not None:
        b = (s.telegram_listener_enabled or "").strip().lower()
        set_setting(Settings.TELEGRAM_LISTENER_ENABLED, "true" if b == "true" else "false")
    if s.telegram_allow_destructive is not None:
        b = (s.telegram_allow_destructive or "").strip().lower()
        set_setting(Settings.TELEGRAM_ALLOW_DESTRUCTIVE, "true" if b == "true" else "false")
    if s.telegram_authorized_user_ids is not None:
        # CSV of Telegram user_id ints. Backend keeps the value raw —
        # the listener parses + validates on every check.
        set_setting(Settings.TELEGRAM_AUTHORIZED_USER_IDS,
                    (s.telegram_authorized_user_ids or "").strip())
    if s.swarm_autoheal_action is not None:
        action = (s.swarm_autoheal_action or "").strip().lower()
        if action not in ("", "notify", "restart"):
            raise HTTPException(
                status_code=400,
                detail="swarm_autoheal_action must be 'notify' or 'restart'.",
            )
        set_setting(Settings.SWARM_AUTOHEAL_ACTION, action or "notify")
    if s.swarm_autoheal_bootstrap_enabled is not None:
        # Accept booleans + the legacy "true"/"false" string shape
        # via Pydantic's str annotation. Empty string falls back to
        # the default ("true") on the read side.
        raw = (s.swarm_autoheal_bootstrap_enabled or "").strip().lower()
        if raw in ("", "true", "false"):
            set_setting(Settings.SWARM_AUTOHEAL_BOOTSTRAP_ENABLED, raw or "true")
        else:
            raise HTTPException(
                status_code=400,
                detail="swarm_autoheal_bootstrap_enabled must be "
                       "'true' or 'false'.",
            )
    # Per-event notification toggles. Each value MUST be
    # "true" / "false" / "" (empty clears → read-side falls back to
    # the default-true via get_setting_bool). Anything else is a
    # 400 so a typo can't silently disable a category. The notify()
    # gate in logic/ops.py honours these per-event keys.
    # Derived from the module-level _NOTIFY_EVENT_NAMES tuple (
    # single source of truth for both admin gates and per-user opt-in).
    _NOTIFY_EVENT_KEYS = tuple(f"notify_event_{n}" for n in _NOTIFY_EVENT_NAMES)
    _set_bool_settings(s, _NOTIFY_EVENT_KEYS)
    # Per-medium master switches. Same "true" / "false" / ""
    # contract as the per-event toggles above so the SPA's existing
    # boolean-cast pattern works unchanged. Bouncing through the
    # NOTIFY_MEDIUM_NAMES tuple keeps the validator additive — adding a
    # third medium adds one entry there + one SettingsIn field; this
    # block needs no edit.
    _set_bool_settings(s, [f"notify_medium_{m}" for m in _ops_mod.NOTIFY_MEDIUM_NAMES])
    # TOTP / 2FA policy. Booleans persisted as "true" / "false";
    # ints bounds-checked then stored as decimal strings (matches the
    # tuning_* shape). Same dirty + Save UI pattern as the
    # other admin-tab toggles.
    if s.totp_allowed is not None:
        set_setting(Settings.TOTP_ALLOWED, "true" if s.totp_allowed else "false")
    if s.totp_required_for_admins is not None:
        set_setting(
            Settings.TOTP_REQUIRED_FOR_ADMINS,
            "true" if s.totp_required_for_admins else "false",
        )
    if s.totp_required_for_users is not None:
        set_setting(
            Settings.TOTP_REQUIRED_FOR_USERS,
            "true" if s.totp_required_for_users else "false",
        )
    if s.totp_lockout_max_failures is not None:
        n = int(s.totp_lockout_max_failures)
        if n < 3 or n > 20:
            raise HTTPException(
                status_code=400,
                detail="totp_lockout_max_failures must be in the range 3..20.",
            )
        set_setting(Settings.TOTP_LOCKOUT_MAX_FAILURES, str(n))
    if s.totp_lockout_minutes is not None:
        n = int(s.totp_lockout_minutes)
        if n < 1 or n > 1440:
            raise HTTPException(
                status_code=400,
                detail="totp_lockout_minutes must be in the range 1..1440.",
            )
        set_setting(Settings.TOTP_LOCKOUT_MINUTES, str(n))
    if s.passkeys_allowed is not None:
        set_setting(Settings.PASSKEYS_ALLOWED, "true" if s.passkeys_allowed else "false")
    # Invalidate the policy cache so a Save in
    # Admin -> Config takes effect on the next call instead of waiting
    # out the TTL window. Cheap — just resets the dict.
    if (
        s.totp_allowed is not None
        or s.totp_required_for_admins is not None
        or s.totp_required_for_users is not None
        or s.totp_lockout_max_failures is not None
        or s.totp_lockout_minutes is not None
        or s.passkeys_allowed is not None
    ):
        _invalidate_totp_policy_cache()
    # Open-Meteo upstream — strips trailing slashes so `<base>/v1/...`
    # composition in api_weather stays stable whether the operator
    # typed a trailing slash or not. DEPRECATED — see weather_* block.
    if s.open_meteo_url is not None:
        set_setting(Settings.OPEN_METEO_URL, (s.open_meteo_url or "").strip().rstrip("/"))
    # WeatherAPI.com — every field is independently nullable on
    # SettingsIn so a partial save (e.g. just flipping the master
    # toggle) leaves the other fields untouched. API key follows the
    # keep-current-if-blank contract: non-empty string overwrites,
    # empty / whitespace / None = no-op; explicit clear via
    # `clear_weather_api_key=true`. Trailing slash on base URL is
    # stripped so the per-endpoint formatter can append cleanly.
    weather_changed = False
    if s.weather_enabled is not None:
        set_setting(Settings.WEATHER_ENABLED, "true" if s.weather_enabled else "false")
        weather_changed = True
    if s.weather_provider is not None:
        v = (s.weather_provider or "").strip().lower()
        if v not in ("open-meteo", "weatherapi", ""):
            raise HTTPException(
                status_code=400,
                detail=f"weather_provider must be 'open-meteo' or 'weatherapi' (got {v!r})",
            )
        set_setting(Settings.WEATHER_PROVIDER, v or "open-meteo")
        weather_changed = True
    if s.weather_api_base_url is not None:
        set_setting(Settings.WEATHER_API_BASE_URL,
                    (s.weather_api_base_url or "").strip().rstrip("/"))
        weather_changed = True
    if s.clear_weather_api_key:
        set_setting(Settings.WEATHER_API_KEY, "")
        weather_changed = True
    elif s.weather_api_key is not None and (s.weather_api_key or "").strip():
        set_setting(Settings.WEATHER_API_KEY, s.weather_api_key.strip())
        weather_changed = True
    if s.weather_default_label is not None:
        set_setting(Settings.WEATHER_DEFAULT_LABEL, (s.weather_default_label or "").strip())
        weather_changed = True
    if s.weather_default_lat is not None:
        # Validate as a float when non-empty so a typo'd "abc" lands
        # as a clear 400 instead of a silent NaN on every probe.
        raw_lat = (s.weather_default_lat or "").strip()
        if raw_lat:
            try:
                float(raw_lat)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"weather_default_lat {raw_lat!r} is not a number",
                )
        set_setting(Settings.WEATHER_DEFAULT_LAT, raw_lat)
        weather_changed = True
    if s.weather_default_lon is not None:
        raw_lon = (s.weather_default_lon or "").strip()
        if raw_lon:
            try:
                float(raw_lon)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"weather_default_lon {raw_lon!r} is not a number",
                )
        set_setting(Settings.WEATHER_DEFAULT_LON, raw_lon)
        weather_changed = True
    if weather_changed:
        # Drop the in-process per-coord TTL cache so a re-configured
        # provider takes effect on the very next call instead of
        # waiting up to tuning_weather_cache_ttl_seconds.
        try:
            from logic.weather import invalidate_cache as _weather_invalidate
            _weather_invalidate()
        except Exception:  # noqa: BLE001 — module is optional on the import path
            pass
    if s.portainer_public_url is not None:
        set_setting(Settings.PORTAINER_PUBLIC_URL, s.portainer_public_url)
    if s.backup_retention_count is not None:
        n = max(0, int(s.backup_retention_count))
        set_setting(Settings.BACKUP_RETENTION_COUNT, str(n))
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
        set_setting(Settings.SCHEDULER_TIMEZONE, tz_name)
    if s.node_exporter_enabled is not None:
        set_setting(Settings.NODE_EXPORTER_ENABLED, "true" if s.node_exporter_enabled else "false")
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
        set_setting(Settings.NODE_EXPORTER_URL_TEMPLATE, tpl)
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
        set_setting(Settings.NODE_EXPORTER_OVERRIDES, json.dumps(clean))
    if s.host_stats_source is not None:
        # Accept a CSV ("beszel,node_exporter") or a single legacy value.
        # Empty / "none" / unknown tokens collapse to "none" so the
        # gather skips the whole block.
        raw = (s.host_stats_source or "").strip()
        parts = {t.strip().lower() for t in raw.split(",") if t.strip()}
        parts.discard("none")
        valid = {"beszel", "node_exporter", "pulse", "webmin", "ping", "snmp", "http_probe", "service_probe"}
        unknown = parts - valid
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    "host_stats_source must be a CSV of 'beszel' / "
                    "'node_exporter' / 'pulse' / 'webmin' / 'ping' / "
                    "'snmp' / 'http_probe' "
                    f"(or 'none'). Unknown: {sorted(unknown)}"
                ),
            )
        normalized = ",".join(sorted(parts)) if parts else "none"
        set_setting(Settings.HOST_STATS_SOURCE, normalized)
    # Beszel: URL + identity + password + verify_tls — keep-current-
    # if-blank for the password, same shape as every other secret.
    _set_url_secondary_password_tls(
        s,
        url_field="beszel_hub_url", url_setting=Settings.BESZEL_HUB_URL,
        secondary_field="beszel_identity", secondary_setting=Settings.BESZEL_IDENTITY,
        password_field="beszel_password", password_setting=Settings.BESZEL_PASSWORD,
        verify_tls_field="beszel_verify_tls", verify_tls_setting=Settings.BESZEL_VERIFY_TLS,
    )
    # Pulse: URL + token (in the password slot) + verify_tls — no
    # secondary identity field.
    _set_url_secondary_password_tls(
        s,
        url_field="pulse_url", url_setting=Settings.PULSE_URL,
        password_field="pulse_token", password_setting=Settings.PULSE_TOKEN,
        verify_tls_field="pulse_verify_tls", verify_tls_setting=Settings.PULSE_VERIFY_TLS,
    )
    if s.pulse_aliases is not None:
        set_setting(Settings.PULSE_ALIASES, json.dumps(_clean_alias_dict(s.pulse_aliases)))
    if s.beszel_aliases is not None:
        # Filter to string→string, trim, drop empty entries so a blank
        # row in the UI doesn't persist as a ghost mapping.
        set_setting(Settings.BESZEL_ALIASES, json.dumps(_clean_alias_dict(s.beszel_aliases)))
    # Webmin: URL + user + password + verify_tls. Same suffix / _set
    # conventions as every other provider's secret;
    # ``webmin_aliases`` (handled separately below) is Docker
    # hostname → Miniserv base URL.
    _set_url_secondary_password_tls(
        s,
        url_field="webmin_url", url_setting=Settings.WEBMIN_URL,
        secondary_field="webmin_user", secondary_setting=Settings.WEBMIN_USER,
        password_field="webmin_password", password_setting=Settings.WEBMIN_PASSWORD,
        verify_tls_field="webmin_verify_tls", verify_tls_setting=Settings.WEBMIN_VERIFY_TLS,
    )
    if s.webmin_aliases is not None:
        # Webmin alias values are Miniserv base URLs — strip trailing
        # `/` so downstream concatenation stays clean.
        set_setting(
            Settings.WEBMIN_ALIASES,
            json.dumps(_clean_alias_dict(s.webmin_aliases, value_transform=lambda v: v.rstrip("/"))),
        )
    # Ping. No secrets — every field round-trips in the clear.
    # Validation: `ping_default_port` clamped to 1..65535. `ping_enabled`
    # is the master toggle but this acts as documentation only — the
    # provider also has to be in `host_stats_source` to actually probe
    # (handled by `active_host_stats_providers()` upstream).
    if s.ping_enabled is not None:
        set_setting(Settings.PING_ENABLED, "true" if s.ping_enabled else "false")
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
        set_setting(Settings.PING_DEFAULT_PORT, str(p))
    if s.ping_use_icmp is not None:
        set_setting(Settings.PING_USE_ICMP, "true" if s.ping_use_icmp else "false")
    # HTTP / TLS / DNS probe — seventh host-stats provider. Master
    # toggle plus alias CSV ("docker_host=probe_url,...") for hosts
    # where the curated row's ``url`` field isn't the right probe
    # target. Alias values normalised: rstrip trailing slash, dedupe
    # by key (last-write-wins), keep input order otherwise.
    if s.http_probe_enabled is not None:
        set_setting(Settings.HTTP_PROBE_ENABLED, "true" if s.http_probe_enabled else "false")
        # Keep `host_stats_source` in sync with the master toggle so the
        # merge gate (which requires BOTH the master flag AND the CSV
        # membership) doesn't silently skip the provider when the
        # operator flips only the master checkbox. Same shape applied
        # to `service_probe` below — without this sync, the host drawer
        # card sits empty even though the operator enabled the feature.
        _sync_host_stats_source("http_probe", bool(s.http_probe_enabled))
    if s.service_probe_enabled is not None:
        set_setting(Settings.SERVICE_PROBE_ENABLED, "true" if s.service_probe_enabled else "false")
        _sync_host_stats_source("service_probe", bool(s.service_probe_enabled))
    if s.http_probe_aliases is not None:
        raw = (s.http_probe_aliases or "").strip()
        if raw:
            pairs: list[str] = []
            seen_keys: set[str] = set()
            for token in raw.split(","):
                token = token.strip()
                if not token or "=" not in token:
                    continue
                k, _, v = token.partition("=")
                k = k.strip()
                v = v.strip().rstrip("/")
                if not k or not v:
                    continue
                if k in seen_keys:
                    # last-write-wins — replace the prior entry
                    pairs = [p for p in pairs if not p.startswith(k + "=")]
                seen_keys.add(k)
                pairs.append(f"{k}={v}")
            set_setting(Settings.HTTP_PROBE_ALIASES, ",".join(pairs))
        else:
            set_setting(Settings.HTTP_PROBE_ALIASES, "")
    # Port-scan provider — master toggle + global defaults. Per-host
    # overrides live on `hosts_config[].port_scan` and persist via the
    # existing `hosts_config` setting; no separate aliases store.
    # Ports list validated via `parse_port_csv` so an invalid CSV
    # token doesn't reach the scanner.
    if s.port_scan_enabled is not None:
        set_setting(Settings.PORT_SCAN_ENABLED, "true" if s.port_scan_enabled else "false")
    if s.port_scan_default_ports is not None:
        from logic.port_scanner import parse_port_csv as _pcsv
        raw = (s.port_scan_default_ports or "").strip()
        if raw and not _pcsv(raw):
            raise HTTPException(
                status_code=400,
                detail="port_scan_default_ports must be CSV/range syntax (e.g. '22,80,443,8000-8100')",
            )
        set_setting(Settings.PORT_SCAN_DEFAULT_PORTS, raw)
    # `port_scan_udp_enabled` is DEPRECATED — UDP runs under the
    # master `port_scan_enabled` toggle (operator-flagged 2026-05-10).
    # We accept the field on POST for legacy compatibility but no
    # longer persist it; existing rows in the settings table become
    # dead data and can be pruned by a future migration.
    if s.port_scan_udp_default_ports is not None:
        from logic.port_scanner import parse_port_csv as _pcsv
        raw = (s.port_scan_udp_default_ports or "").strip()
        if raw and not _pcsv(raw):
            raise HTTPException(
                status_code=400,
                detail="port_scan_udp_default_ports must be CSV/range syntax (e.g. '53,67,123,161,5353')",
            )
        set_setting(Settings.PORT_SCAN_UDP_DEFAULT_PORTS, raw)
    # NOTE: legacy `port_scan_default_timeout_seconds` /
    # `port_scan_default_concurrency` plain-key write paths were
    # removed. They were dead code — every consumer reads via
    # `tuning_int(Tunable.PORT_SCAN_DEFAULT_TIMEOUT_SECONDS)` /
    # `tuning_int(Tunable.PORT_SCAN_DEFAULT_CONCURRENCY)` (typed-enum
    # form per the STRICT key-enum rule) and the SPA's port_scan partial
    # binds to the TUNABLES form. The legacy keys remain on
    # `SettingsIn` only to gracefully ignore old POST bodies; no
    # `set_setting` writes here means the values silently land
    # nowhere, matching what the consumers were already seeing.
    # Per CLAUDE.md "Plain-settings escape hatch is a drift class".
    # SNMP. Mirror the webmin / beszel / pulse persistence
    # contract: community / version / port / aliases round-trip in the
    # clear; v3 user is also clear text; the two v3 keys are write-only
    # (keep current if blank). Validation: port clamped to 1..65535;
    # version restricted to {"v2c", "v3"}; community trimmed.
    if s.snmp_default_community is not None:
        set_setting(Settings.SNMP_DEFAULT_COMMUNITY, (s.snmp_default_community or "").strip())
    if s.snmp_default_version is not None:
        v = (s.snmp_default_version or "").strip().lower()
        if v and v not in ("v2c", "v3"):
            raise HTTPException(
                status_code=400,
                detail="snmp_default_version must be 'v2c' or 'v3' (or blank)",
            )
        set_setting(Settings.SNMP_DEFAULT_VERSION, v or "v2c")
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
        set_setting(Settings.SNMP_DEFAULT_PORT, str(p))
    if s.snmp_v3_user is not None:
        set_setting(Settings.SNMP_V3_USER, (s.snmp_v3_user or "").strip())
    if s.snmp_v3_auth_key is not None and s.snmp_v3_auth_key.strip() != "":
        set_setting(Settings.SNMP_V3_AUTH_KEY, s.snmp_v3_auth_key)
    if s.snmp_v3_priv_key is not None and s.snmp_v3_priv_key.strip() != "":
        set_setting(Settings.SNMP_V3_PRIV_KEY, s.snmp_v3_priv_key)
    if s.snmp_aliases is not None:
        set_setting(Settings.SNMP_ALIASES, json.dumps(_clean_alias_dict(s.snmp_aliases)))
    # Per-provider chip colours. Hex string `#RRGGBB` (7 chars,
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
            "provider_color_http_probe", "provider_color_service_probe",
    ):
        _val = getattr(s, _field, None)
        if _val is None:
            continue
        _trim = str(_val).strip()
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
        set_setting(Settings.SSH_DEFAULT_USER, (s.ssh_default_user or "").strip())
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
        set_setting(Settings.SSH_DEFAULT_PORT, str(p))
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
                               else (get_setting(Settings.SSH_DEFAULT_PRIVATE_KEY_PASSPHRASE) or "")
                           ) or None
            _asyncssh.import_private_key(
                s.ssh_default_private_key, passphrase=pw_candidate,
            )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"ssh_default_private_key failed to parse: {type(e).__name__}: {e}",
            )
        set_setting(Settings.SSH_DEFAULT_PRIVATE_KEY, s.ssh_default_private_key)
    if (
        s.ssh_default_private_key_passphrase is not None
        and s.ssh_default_private_key_passphrase.strip() != ""
    ):
        set_setting(
            Settings.SSH_DEFAULT_PRIVATE_KEY_PASSPHRASE,
            s.ssh_default_private_key_passphrase,
        )
    # SSH password auth. Blank = keep-current (matches the _set flag
    # convention). Non-empty replaces. No validation needed at save
    # time — asyncssh raises on connect if the password is wrong.
    if (
        s.ssh_default_password is not None
        and s.ssh_default_password.strip() != ""
    ):
        set_setting(Settings.SSH_DEFAULT_PASSWORD, s.ssh_default_password)
    if s.ssh_fqdn_suffix is not None:
        # Normalise — operator might paste with or without leading dot.
        # Store canonical form: leading dot, no trailing dot, trimmed.
        raw = (s.ssh_fqdn_suffix or "").strip().rstrip(".")
        if raw and not raw.startswith("."):
            raw = "." + raw
        set_setting(Settings.SSH_FQDN_SUFFIX, raw)
    if s.ssh_default_known_hosts is not None:
        set_setting(Settings.SSH_DEFAULT_KNOWN_HOSTS, s.ssh_default_known_hosts or "")
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
        set_setting(Settings.SSH_DESTRUCTIVE_PATTERNS, raw)
    # Clear flags — operator clicked "Clear" on an SSH secret. Delete
    # the underlying setting outright (not just set to ""); downstream
    # code treats missing / empty identically, but the flag-driven path
    # is the only way to erase a value that the keep-current-if-blank
    # contract otherwise preserves forever.
    if s.clear_ssh_private_key:
        set_setting(Settings.SSH_DEFAULT_PRIVATE_KEY, "")
        set_setting(Settings.SSH_DEFAULT_PRIVATE_KEY_PASSPHRASE, "")  # orphaned passphrase is noise
    if s.clear_ssh_passphrase:
        set_setting(Settings.SSH_DEFAULT_PRIVATE_KEY_PASSPHRASE, "")
    if s.clear_ssh_password:
        set_setting(Settings.SSH_DEFAULT_PASSWORD, "")
    # Provider-secret clear handlers. Each flag sets the corresponding
    # settings KV row to empty so the keep-current-if-blank contract
    # at every other write path treats the secret as "explicitly
    # cleared" on the next save. Pairs with the SPA's per-secret
    # `clear<Field>()` helper that fires this flag in isolation
    # (separate POST so an in-flight form edit doesn't accidentally
    # land alongside the clear).
    if s.clear_beszel_password:
        set_setting(Settings.BESZEL_PASSWORD, "")
    if s.clear_pulse_token:
        set_setting(Settings.PULSE_TOKEN, "")
    if s.clear_webmin_password:
        set_setting(Settings.WEBMIN_PASSWORD, "")
    if s.clear_portainer_api_key:
        set_setting(Settings.PORTAINER_API_KEY, "")
    if s.clear_oidc_client_secret:
        set_setting(Settings.OIDC_CLIENT_SECRET, "")
    # Custom SSH actions — JSON array replaces the whole list wholesale.
    # Full-replace semantics match how Admin → Hosts saves hosts_config.
    # Shape validation lives here so the runner can trust what it reads.
    # Admin → Hosts: show / hide the per-host drawer debug panel.
    # Persisted as the string "true" / "false" (matches every other
    # boolean toggle in this table — see node_exporter_enabled etc.).
    if s.debug_panel_enabled is not None:
        set_setting(Settings.DEBUG_PANEL_ENABLED, "true" if s.debug_panel_enabled else "false")
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
                "id": (a.get("id") or "").strip() or _slugify_action(title),
                "title": title[:80],
                "command": cmd[:2048],
            })
        set_setting(Settings.SSH_CUSTOM_ACTIONS, json.dumps(clean_actions))

    # --- Host groups -----------------------------------------
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
            _rs_raw = g.get("range_start")
            _re_raw = g.get("range_end")
            if _rs_raw is None or _re_raw is None:
                continue
            try:
                # `g.get(...)` returns Any from PyCharm's perspective;
                # the int() call sees Any | None even after the None
                # check above. cast() asserts the concrete type so the
                # call type-checks without per-line ignore markers.
                rs = int(cast(int, _rs_raw))
                re_ = int(cast(int, _re_raw))
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
                    # Parse into a temp local so PyCharm narrows the
                    # ternary `_n <= 0` check against a known int rather
                    # than the outer `int | None` annotation (which
                    # otherwise triggers "Member 'None' has no __le__").
                    _n = int(cast(Any, number_raw))
                    number_val = None if _n <= 0 else _n
                except (TypeError, ValueError):
                    number_val = None
            # Optional per-group SSH credentials. Same shape as
            # `hosts_config[].ssh` so the resolver in `logic/ssh.py`
            # can iterate them uniformly. Keep-current-if-blank for
            # the password — same convention as the global secret
            # store.
            clean_ssh: dict = {}
            _raw_ssh_in = g.get("ssh")
            ssh_in: dict = _raw_ssh_in if isinstance(_raw_ssh_in, dict) else {}
            user = str((ssh_in or {}).get("user") or "").strip()
            if user:
                clean_ssh["user"] = user
            port = (ssh_in or {}).get("port")
            if port not in (None, "", 0):
                try:
                    pi = int(cast(int, port))
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
            # 1. New non-empty password → store it (clear flag is
            #    ignored — operator typed a new value, that wins).
            # 2. Empty + clear_password=true → erase (don't carry
            #    forward).
            # 3. Empty + no clear flag → carry forward the prior
            #    persisted value (keep-current-if-blank — same
            #    contract as every other secret in the settings
            #    table). Lookup is by stable `id`, not by `name`,
            #    so renames preserve the password but a new group
            #    that happens to reuse an old name does NOT inherit.
            new_pw = str((ssh_in or {}).get("password") or "").strip()
            clear = bool((ssh_in or {}).get("clear_password"))
            if new_pw:
                clean_ssh["password"] = new_pw
            elif not clear:
                try:
                    prior_raw = get_setting(Settings.HOST_GROUPS) or ""
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
                "id": row_id,
                "name": name,
                "range_start": rs,
                "range_end": re_,
                "order": order,
                "parent_name": parent_name,
                "ip_range": ip_range,
                "number": number_val,
                "ssh": clean_ssh,
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
        # - Two top-level groups overlapping (bad).
        # - A sub-group overlapping a top-level group that is NOT
        #   its parent (bad — would double-assign hosts).
        # - Two sub-groups overlapping (bad — whether they share a
        #   parent or not; cross-parent overlap is structurally
        #   impossible when parents are disjoint, but we check
        #   anyway as a belt-and-braces).
        # Parent-child pairs are expected to overlap (sub is contained
        # in parent by construction) and are skipped.
        def _is_parent_child(grp_a: dict, grp_b: dict) -> bool:
            """Whether two host-group dicts form a parent-child pair."""
            return (grp_a["parent_name"] == grp_b["name"]
                    or grp_b["parent_name"] == grp_a["name"])

        n = len(clean_groups)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = clean_groups[i], clean_groups[j]
                if _is_parent_child(a, b):
                    continue
                # Standard interval-overlap test.
                if (
                    a["range_start"] <= b["range_end"]
                    and b["range_start"] <= a["range_end"]
                ):
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
            # `num` was narrowed away from None above but `g.get(...)`
            # is typed Any, so seen_numbers[num] = ... fails the
            # generic-key check. cast() asserts the int type.
            num_k = cast(int, num)
            prior = seen_numbers.get(num_k)
            if prior is not None:
                raise HTTPException(
                    400,
                    f"host_groups: number {num} is used by both "
                    f"'{prior}' and '{g['name']}'. Group numbers must be unique.",
                )
            seen_numbers[num_k] = g["name"]

        # Duplicate-id check — analogous to the fix on hosts_config.
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
            gid_k = cast(str, gid)  # narrowed from Any by truthy check above
            prior_name = gid_seen.get(gid_k)
            if prior_name is not None:
                raise HTTPException(
                    400,
                    f"host_groups: id {gid} is used by both "
                    f"'{prior_name}' and '{g['name']}'. Each group must have a unique id.",
                )
            gid_seen[gid_k] = g["name"]

        # Persist in order-field order so render iteration doesn't have to re-sort.
        clean_groups.sort(key=lambda grp_row: (grp_row["order"], grp_row["name"]))
        set_setting(Settings.HOST_GROUPS, json.dumps(clean_groups))

    # --- Asset inventory --------------------------------------------------
    # Secret follows the keep-current-if-blank + clear-flag contract.
    if s.asset_inventory_base_url is not None:
        set_setting(Settings.ASSET_INVENTORY_BASE_URL,
                    (s.asset_inventory_base_url or "").strip().rstrip("/"))
    if s.asset_inventory_token_url is not None:
        set_setting(Settings.ASSET_INVENTORY_TOKEN_URL,
                    (s.asset_inventory_token_url or "").strip())
    if s.asset_inventory_client_id is not None:
        set_setting(Settings.ASSET_INVENTORY_CLIENT_ID,
                    (s.asset_inventory_client_id or "").strip())
    if s.asset_inventory_scope is not None:
        set_setting(Settings.ASSET_INVENTORY_SCOPE,
                    (s.asset_inventory_scope or "").strip())
    if s.asset_inventory_verify_tls is not None:
        set_setting(Settings.ASSET_INVENTORY_VERIFY_TLS,
                    "true" if s.asset_inventory_verify_tls else "false")
    if (
        s.asset_inventory_client_secret is not None
        and s.asset_inventory_client_secret.strip() != ""
    ):
        set_setting(Settings.ASSET_INVENTORY_CLIENT_SECRET,
                    s.asset_inventory_client_secret)
    if s.clear_asset_inventory_client_secret:
        set_setting(Settings.ASSET_INVENTORY_CLIENT_SECRET, "")
    if s.asset_inventory_auth_mode is not None:
        mode = (s.asset_inventory_auth_mode or "").strip().lower()
        if mode not in ("oauth2", "lifetime_token"):
            mode = "oauth2"
        set_setting(Settings.ASSET_INVENTORY_AUTH_MODE, mode)
    if (
        s.asset_inventory_lifetime_token is not None
        and s.asset_inventory_lifetime_token.strip() != ""
    ):
        set_setting(Settings.ASSET_INVENTORY_LIFETIME_TOKEN,
                    s.asset_inventory_lifetime_token.strip())
    if s.clear_asset_inventory_lifetime_token:
        set_setting(Settings.ASSET_INVENTORY_LIFETIME_TOKEN, "")
    if s.asset_inventory_service is not None:
        set_setting(Settings.ASSET_INVENTORY_SERVICE,
                    (s.asset_inventory_service or "").strip())
    if s.asset_inventory_action is not None:
        set_setting(Settings.ASSET_INVENTORY_ACTION,
                    (s.asset_inventory_action or "").strip())
    if s.asset_inventory_min_value is not None:
        # Blank = clear; non-blank = parse as int. Anything malformed
        # falls back to clear so a typo doesn't poison the setting.
        v = (s.asset_inventory_min_value or "").strip()
        try:
            set_setting(Settings.ASSET_INVENTORY_MIN_VALUE, str(int(v)) if v else "")
        except ValueError:
            set_setting(Settings.ASSET_INVENTORY_MIN_VALUE, "")
    if s.asset_inventory_max_value is not None:
        v = (s.asset_inventory_max_value or "").strip()
        try:
            set_setting(Settings.ASSET_INVENTORY_MAX_VALUE, str(int(v)) if v else "")
        except ValueError:
            set_setting(Settings.ASSET_INVENTORY_MAX_VALUE, "")
    if s.asset_inventory_edit_url_template is not None:
        set_setting(Settings.ASSET_INVENTORY_EDIT_URL_TEMPLATE,
                    (s.asset_inventory_edit_url_template or "").strip())

    # ----- AI integration (Stage 1) ----------------------------------
    # Master toggle + active-provider validator + per-provider fields.
    # API keys ride the keep-current-if-blank contract: only a non-empty
    # string is persisted, so an empty POST keeps the existing key.
    # Provider list is the canonical `logic.ai.SUPPORTED_PROVIDERS`
    # tuple — adding a fifth provider is a one-line edit there and
    # every consumer (validator below + api_get_settings + api_me) picks
    # it up automatically.
    _AI_PROVIDER_NAMES = _ai_supported_providers()
    if s.ai_enabled is not None:
        set_setting(Settings.AI_ENABLED, "true" if s.ai_enabled else "false")
    if s.ai_active_provider is not None:
        active = (s.ai_active_provider or "").strip().lower()
        if active and active not in _AI_PROVIDER_NAMES:
            raise HTTPException(
                400,
                f"ai_active_provider must be one of {','.join(_AI_PROVIDER_NAMES)}",
            )
        set_setting(Settings.AI_ACTIVE_PROVIDER, active)
    # NOTE: legacy `ai_max_tokens` / `ai_fallback_max_depth` plain-key
    # write paths were removed. They were dead code — every consumer
    # reads via `tuning_int(Tunable.AI_MAX_TOKENS)` /
    # `tuning_int(Tunable.AI_FALLBACK_MAX_DEPTH)` (typed-enum form per
    # the STRICT key-enum rule) and the SPA's AI Integration partial
    # binds to the TUNABLES form. Per CLAUDE.md "Plain-settings escape
    # hatch is a drift class" — numeric operator-tunable values must
    # flow through TUNABLES, not via
    # `get_setting` / `set_setting`. Fields remain on `SettingsIn`
    # so old POST bodies don't 422; the values silently land nowhere
    # which matches what the consumers were already seeing.
    if s.ai_fallback_enabled is not None:
        set_setting(Settings.AI_FALLBACK_ENABLED, "true" if s.ai_fallback_enabled else "false")
    if s.ai_fallback_order is not None:
        # CSV of provider ids — sanitise to known providers, drop unknowns
        # so a typo can't bring fallback dispatch into an unsupported branch.
        valid = set(_ai_supported_providers())
        items = [p.strip().lower() for p in s.ai_fallback_order.split(",") if p.strip()]
        cleaned = []
        seen = set()
        for p in items:
            if p in valid and p not in seen:
                cleaned.append(p)
                seen.add(p)
        set_setting(Settings.AI_FALLBACK_ORDER, ",".join(cleaned))
    for _ai_name in _AI_PROVIDER_NAMES:
        # enabled
        _v = getattr(s, f"ai_provider_{_ai_name}_enabled", None)
        if _v is not None:
            set_setting(ai_provider_enabled_key(_ai_name), "true" if _v else "false")
        # model
        _v = getattr(s, f"ai_provider_{_ai_name}_model", None)
        if _v is not None:
            set_setting(ai_provider_model_key(_ai_name), (_v or "").strip())
        # base_url
        _v = getattr(s, f"ai_provider_{_ai_name}_base_url", None)
        if _v is not None:
            set_setting(ai_provider_base_url_key(_ai_name),
                        (_v or "").strip().rstrip("/"))
        # api_key — keep-current-if-blank
        _v = getattr(s, f"ai_provider_{_ai_name}_api_key", None)
        if _v is not None and str(_v).strip():
            set_setting(ai_provider_api_key_key(_ai_name), str(_v).strip())

    _cache["ts"] = 0  # force the next gather to re-read alias settings

    auth_changed = False
    portainer_changed = False
    with db_conn() as c:
        # --- Portainer connection -----------------------------------------
        if s.portainer_url is not None:
            set_setting(Settings.PORTAINER_URL, (s.portainer_url or "").rstrip("/"))
            portainer_changed = True
        if s.portainer_endpoint_id is not None:
            set_setting(Settings.PORTAINER_ENDPOINT_ID, str(int(s.portainer_endpoint_id)))
            portainer_changed = True
        if s.portainer_verify_tls is not None:
            set_setting(Settings.PORTAINER_VERIFY_TLS, "true" if s.portainer_verify_tls else "false")
            portainer_changed = True
        # Empty / whitespace-only = "keep current" (same pattern as
        # oidc_client_secret). Admins clear the value by a different
        # route if ever needed.
        if s.portainer_api_key is not None and s.portainer_api_key.strip() != "":
            set_setting(Settings.PORTAINER_API_KEY, s.portainer_api_key)
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
            # See in notes/code_review_2026-04-25.md.
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

    # Tuning knobs. Each field is keep-if-None / clear-if-blank /
    # bounds-check-and-store-if-provided. Bounds come from
    # logic.tuning.TUNABLES so the resolver, the editor, and the
    # validator share one source of truth. Stored as plain strings —
    # the resolver int-casts on read.
    for _k, (_env, _default, _lo, _hi) in tuning.TUNABLES.items():
        _val = getattr(s, _k, None)
        if _val is None:
            continue
        _raw = str(_val).strip()
        if _raw == "":
            set_setting(_k, "")
            continue
        try:
            _n = int(_raw)
        except (TypeError, ValueError):
            # Loose boolean coercion for tunables bounded to 0..1 — the
            # SPA's checkbox-bound knobs (e.g. tuning_ai_retry_enabled)
            # may receive "true" / "false" if a previous form-state
            # binding leaked a JS bool through `String(true)`. Accept
            # the common truthy / falsy strings for those bounds so a
            # corrupt DB row from the iteration period self-heals on
            # the next save without operator intervention.
            _l = _raw.lower()
            if _lo == 0 and _hi == 1:
                if _l in ("true", "yes", "on", "y", "t", "1"):
                    _n = 1
                elif _l in ("false", "no", "off", "n", "f", "0"):
                    _n = 0
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{_k} must be 0 or 1 (got {_val!r})",
                    )
            else:
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
        # Ping. No credential to bust the cred-blob hash with;
        # cache TTL alone catches `ping_enabled` flips after the
        # 10s window, but we still bust on save for instant feedback.
        "ping_enabled", "ping_default_port", "ping_use_icmp",
        # SNMP. Defaults + aliases + v3 keys all live under the
        # same per-provider state; any change here invalidates the
        # cred-blob hash so subsequent /api/hosts/one/<id> calls re-
        # probe with the new credentials.
        "snmp_default_community", "snmp_default_version", "snmp_default_port",
        "snmp_v3_user", "snmp_v3_auth_key", "snmp_v3_priv_key",
        "snmp_aliases",
        # HTTP / TLS / DNS probe. Master toggle + aliases live here;
        # per-host config (urls / content_match / accepted codes /
        # verify_tls) is on `hosts_config[].http_probe` and rides the
        # generic hosts_config cache-invalidation path.
        "http_probe_enabled", "http_probe_aliases",
        # Per-service reachability probe. Master toggle lives here;
        # per-chip config is on `hosts_config[].services[].probe`.
        "service_probe_enabled",
    }
    if _host_provider_fields & set(s.model_dump(exclude_unset=True).keys()):
        # Late import — the TYPE_CHECKING block above only exposes
        # `invalidate_host_provider_cache` to static type checkers;
        # at runtime it's NOT in this module's namespace, so a bare
        # reference here raises NameError on every settings save that
        # touches a provider field (operator-flagged: "Save failed:
        # NameError: name 'invalidate_host_provider_cache' is not
        # defined"). Per CLAUDE.md "STRICT — Cross-module underscore-
        # name LOAD_GLOBAL leaks" — late-import is the safe pattern
        # for non-underscore cross-module names too.
        from main_pkg.apps_routes import invalidate_host_provider_cache as _invalidate
        _invalidate()
    # Broadcast a settings-changed signal so other tabs can refresh
    # without polling. Self-filter via the originating tab's
    # X-OmniGrid-Client-Id header so this tab doesn't loop the event
    # back as a redundant /api/settings re-fetch.
    try:
        _events.publish(
            "settings:updated",
            {"version": _settings_version_for_payload()},
            client_id=_request_client_id(request),
        )
    except Exception as e:
        print(f"[events] settings:updated publish failed: {e}")
    return {"status": "ok"}


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
