"""Typed enum of every plain ``settings`` table key the codebase reads
or writes via :func:`logic.db.get_setting` / :func:`logic.db.set_setting`
/ :func:`logic.db.get_setting_bool`.

Parallel to :class:`logic.tuning.Tunable` (which covers the
``tuning_<key>`` family resolved via :func:`logic.tuning.tuning_int`).
Where ``Tunable`` is the single source of truth for runtime-tunable
numeric knobs, :class:`Settings` is the single source of truth for the
plain string / boolean / JSON-blob settings the operator edits from
the various Admin tabs and Settings panels.

Why this exists
---------------
Pre-enum, every call site wrote the literal key:

    get_setting("ai_active_provider", "claude")
    set_setting("oidc_client_secret", value)
    get_setting_bool("notify_event_user_login", default=False)

A typo (``"ai_actve_provder"``) silently reads an empty default at
runtime — no exception, no warning — and the same typo on the write
side silently lands as a ghost row that nothing else ever reads. The
``Tunable`` enum closed this gap for the TUNABLES family; this module
closes it for the plain-settings family.

After the refactor every call site goes through the enum:

    get_setting(Settings.AI_ACTIVE_PROVIDER, "claude")
    set_setting(Settings.OIDC_CLIENT_SECRET, value)
    get_setting_bool(Settings.NOTIFY_EVENT_USER_LOGIN, default=False)

The enum inherits ``str`` so existing function signatures (which take
``key: str``) accept the enum value transparently —
``Settings.OIDC_ENABLED == "oidc_enabled"`` is true, ``hash()`` agrees,
and SQLite parameter binding works without any adapter.

Naming
------
Member name = DB key uppercased with underscores preserved. So
``oidc_client_secret`` -> ``Settings.OIDC_CLIENT_SECRET``,
``apprise_url`` -> ``Settings.APPRISE_URL``, etc. Members are sorted
alphabetically by string value so diffs stay tidy.

Dynamic-family keys
-------------------
Keys whose names are computed at runtime — ``f"notify_event_{event}"``,
``f"notify_template_{event}_title"``, ``f"ai_provider_{name}_api_key"``,
``f"last_test_success_{provider}"`` — cannot be enumerated at
compile time. They get helper functions instead (one per family) that
return the assembled string key. The helper is the single point at
which the prefix shape is declared; call sites pass the dynamic suffix
and never write the prefix themselves. For dynamic families whose
suffix space is also enumerable (e.g. the ``notify_event_*`` family
where every event name lives in :data:`logic.ops.NOTIFY_EVENT_NAMES`),
the static known members get explicit enum entries too — both shapes
coexist so static call sites get the typed-enum guarantee while the
dispatcher loops can keep using the helper.

Secrets contract
----------------
Keys ending in ``_token`` / ``_password`` / ``_secret`` / ``_api_key`` /
``_private_key`` / ``_passphrase`` are enumerated here verbatim. The
"keep-current-if-blank" write contract and the ``_set`` flag exposed
through ``/api/settings`` (so the browser sees ``oidc_client_secret_set:
True`` rather than the value) are unchanged — this module only renames
how the key string is referenced at the call site, not how it's
stored or how the API surfaces it.
"""
from enum import Enum


class Settings(str, Enum):
    """Typed enum of every static plain-settings key. See module
    docstring for naming + usage conventions."""

    AI_ACTIVE_PROVIDER = "ai_active_provider"
    AI_ENABLED = "ai_enabled"
    AI_FALLBACK_ENABLED = "ai_fallback_enabled"
    AI_FALLBACK_ORDER = "ai_fallback_order"
    AI_MAX_TOKENS = "ai_max_tokens"
    APPRISE_ENABLED = "apprise_enabled"
    APPRISE_TAG = "apprise_tag"
    APPRISE_URL = "apprise_url"
    ASSET_INVENTORY_ACTION = "asset_inventory_action"
    ASSET_INVENTORY_AUTH_MODE = "asset_inventory_auth_mode"
    ASSET_INVENTORY_BASE_URL = "asset_inventory_base_url"
    ASSET_INVENTORY_CLIENT_ID = "asset_inventory_client_id"
    ASSET_INVENTORY_CLIENT_SECRET = "asset_inventory_client_secret"
    ASSET_INVENTORY_EDIT_URL_TEMPLATE = "asset_inventory_edit_url_template"
    ASSET_INVENTORY_ENABLED = "asset_inventory_enabled"
    ASSET_INVENTORY_LIFETIME_TOKEN = "asset_inventory_lifetime_token"
    ASSET_INVENTORY_MAX_VALUE = "asset_inventory_max_value"
    ASSET_INVENTORY_MIN_VALUE = "asset_inventory_min_value"
    ASSET_INVENTORY_SCOPE = "asset_inventory_scope"
    ASSET_INVENTORY_SERVICE = "asset_inventory_service"
    ASSET_INVENTORY_TOKEN_URL = "asset_inventory_token_url"
    ASSET_INVENTORY_VERIFY_TLS = "asset_inventory_verify_tls"
    BACKUP_RETENTION_COUNT = "backup_retention_count"
    BESZEL_ALIASES = "beszel_aliases"
    BESZEL_HUB_URL = "beszel_hub_url"
    BESZEL_IDENTITY = "beszel_identity"
    BESZEL_PASSWORD = "beszel_password"
    BESZEL_VERIFY_TLS = "beszel_verify_tls"
    DEBUG_PANEL_ENABLED = "debug_panel_enabled"
    DEFAULT_SCHEDULES_SEEDED = "default_schedules_seeded"
    HOSTS_CONFIG = "hosts_config"
    HOST_GROUPS = "host_groups"
    HOST_STATS_SOURCE = "host_stats_source"
    HTTP_PROBE_ALIASES = "http_probe_aliases"
    HTTP_PROBE_ENABLED = "http_probe_enabled"
    NODE_EXPORTER_ENABLED = "node_exporter_enabled"
    NODE_EXPORTER_OVERRIDES = "node_exporter_overrides"
    NODE_EXPORTER_URL_TEMPLATE = "node_exporter_url_template"
    # Notification per-event toggles. Static members for every name in
    # :data:`logic.ops.NOTIFY_EVENT_NAMES`. The dispatcher in
    # ``ops.notify`` iterates the names dynamically and uses
    # :func:`notify_event_key` to assemble the key.
    NOTIFY_EVENT_CONTAINER_REMOVE_FAILURE = "notify_event_container_remove_failure"
    NOTIFY_EVENT_CONTAINER_REMOVE_SUCCESS = "notify_event_container_remove_success"
    NOTIFY_EVENT_CONTAINER_RESTART_FAILURE = "notify_event_container_restart_failure"
    NOTIFY_EVENT_CONTAINER_RESTART_SUCCESS = "notify_event_container_restart_success"
    NOTIFY_EVENT_CONTAINER_UPDATE_FAILURE = "notify_event_container_update_failure"
    NOTIFY_EVENT_CONTAINER_UPDATE_SUCCESS = "notify_event_container_update_success"
    NOTIFY_EVENT_HOST_PAUSED = "notify_event_host_paused"
    NOTIFY_EVENT_HTTP_PROBE_FAILURE = "notify_event_http_probe_failure"
    NOTIFY_EVENT_OVERLAY_CLEANUP_FAILURE = "notify_event_overlay_cleanup_failure"
    NOTIFY_EVENT_OVERLAY_CLEANUP_SUCCESS = "notify_event_overlay_cleanup_success"
    NOTIFY_EVENT_PORT_SCAN_NEW_PORT = "notify_event_port_scan_new_port"
    NOTIFY_EVENT_PRAYER_REMINDER = "notify_event_prayer_reminder"
    NOTIFY_EVENT_SERVICE_PROBE_FAILURE = "notify_event_service_probe_failure"
    NOTIFY_EVENT_PRUNE_FAILURE = "notify_event_prune_failure"
    NOTIFY_EVENT_PRUNE_SUCCESS = "notify_event_prune_success"
    NOTIFY_EVENT_SERVICE_RESTART_FAILURE = "notify_event_service_restart_failure"
    NOTIFY_EVENT_SERVICE_RESTART_SUCCESS = "notify_event_service_restart_success"
    NOTIFY_EVENT_STACK_UPDATE_FAILURE = "notify_event_stack_update_failure"
    NOTIFY_EVENT_STACK_UPDATE_SUCCESS = "notify_event_stack_update_success"
    NOTIFY_EVENT_SWARM_AGENT_RECOVERED = "notify_event_swarm_agent_recovered"
    NOTIFY_EVENT_SWARM_AGENT_RESTART_FAILURE = "notify_event_swarm_agent_restart_failure"
    NOTIFY_EVENT_SWARM_AGENT_RESTART_SUCCESS = "notify_event_swarm_agent_restart_success"
    NOTIFY_EVENT_SWARM_AGENT_UNHEALTHY = "notify_event_swarm_agent_unhealthy"
    NOTIFY_EVENT_TOTP_AUDIT_LOG_FAILED = "notify_event_totp_audit_log_failed"
    NOTIFY_EVENT_USER_LOGIN = "notify_event_user_login"
    # Notification per-medium master switches. Reads from the
    # dispatcher in ``ops.notify`` are dynamic
    # (``f"notify_medium_{medium}"``); the SettingsIn + api_get_settings
    # plumbing in main.py uses literals so each shipped medium gets a
    # static member here too. Use :func:`notify_medium_key` for
    # dynamic-medium loops.
    NOTIFY_MEDIUM_APP = "notify_medium_app"
    NOTIFY_MEDIUM_APPRISE = "notify_medium_apprise"
    NOTIFY_MEDIUM_TELEGRAM = "notify_medium_telegram"
    OIDC_CLIENT_SECRET = "oidc_client_secret"
    OIDC_REDIRECT_URI = "oidc_redirect_uri"
    OPEN_METEO_ENABLED = "open_meteo_enabled"
    OPEN_METEO_URL = "open_meteo_url"
    PASSKEYS_ALLOWED = "passkeys_allowed"
    # WeatherAPI.com replacement provider (supersedes Open-Meteo —
    # natively exposes moon phases via the astronomy block which
    # Open-Meteo doesn't ship). Operator opt-in via the master switch;
    # API key is a write-only secret with the `_set` flag pattern.
    # Default location keeps the topbar weather widget + AI palette
    # context fed without per-call lat/lon params.
    PING_DEFAULT_PORT = "ping_default_port"
    PING_ENABLED = "ping_enabled"
    PING_USE_ICMP = "ping_use_icmp"
    PLEX_CLIENT_IDENTIFIER = "plex_client_identifier"
    PORTAINER_API_KEY = "portainer_api_key"
    PORTAINER_ENABLED = "portainer_enabled"
    PORTAINER_ENDPOINT_ID = "portainer_endpoint_id"
    PORTAINER_PUBLIC_URL = "portainer_public_url"
    PORTAINER_URL = "portainer_url"
    PORTAINER_VERIFY_TLS = "portainer_verify_tls"
    PORT_SCAN_DEFAULT_PORTS = "port_scan_default_ports"
    PORT_SCAN_ENABLED = "port_scan_enabled"
    PORT_SCAN_UDP_DEFAULT_PORTS = "port_scan_udp_default_ports"
    PRAYER_TIMES_API_BASE_URL = "prayer_times_api_base_url"
    PRAYER_TIMES_ENABLED = "prayer_times_enabled"
    PRAYER_TIMES_DEFAULT_LABEL = "prayer_times_default_label"
    PRAYER_TIMES_DEFAULT_LAT = "prayer_times_default_lat"
    PRAYER_TIMES_DEFAULT_LON = "prayer_times_default_lon"
    PRAYER_TIMES_METHOD = "prayer_times_method"
    PRAYER_TIMES_SCHOOL = "prayer_times_school"
    PROVIDER_COLOR_BESZEL = "provider_color_beszel"
    PROVIDER_COLOR_HTTP_PROBE = "provider_color_http_probe"
    PROVIDER_COLOR_NODE_EXPORTER = "provider_color_node_exporter"
    PROVIDER_COLOR_PING = "provider_color_ping"
    PROVIDER_COLOR_PULSE = "provider_color_pulse"
    PROVIDER_COLOR_SERVICE_PROBE = "provider_color_service_probe"
    PROVIDER_COLOR_SNMP = "provider_color_snmp"
    PROVIDER_COLOR_WEBMIN = "provider_color_webmin"
    PUBLIC_IP_ENABLED = "public_ip_enabled"
    PULSE_ALIASES = "pulse_aliases"
    PULSE_TOKEN = "pulse_token"
    PULSE_URL = "pulse_url"
    PULSE_VERIFY_TLS = "pulse_verify_tls"
    SCHEDULER_TIMEZONE = "scheduler_timezone"
    SERVICE_CATALOG_SEEDED_SLUGS = "service_catalog_seeded_slugs"
    SERVICE_PROBE_ENABLED = "service_probe_enabled"
    SNMP_ALIASES = "snmp_aliases"
    SNMP_DEFAULT_COMMUNITY = "snmp_default_community"
    SNMP_DEFAULT_PORT = "snmp_default_port"
    SNMP_DEFAULT_VERSION = "snmp_default_version"
    SNMP_V3_AUTH_KEY = "snmp_v3_auth_key"
    SNMP_V3_PRIV_KEY = "snmp_v3_priv_key"
    SNMP_V3_USER = "snmp_v3_user"
    SSH_CUSTOM_ACTIONS = "ssh_custom_actions"
    SSH_DEFAULT_KNOWN_HOSTS = "ssh_default_known_hosts"
    SSH_DEFAULT_PASSWORD = "ssh_default_password"
    SSH_DEFAULT_PORT = "ssh_default_port"
    SSH_DEFAULT_PRIVATE_KEY = "ssh_default_private_key"
    SSH_DEFAULT_PRIVATE_KEY_PASSPHRASE = "ssh_default_private_key_passphrase"
    SSH_DEFAULT_USER = "ssh_default_user"
    SSH_DESTRUCTIVE_PATTERNS = "ssh_destructive_patterns"
    SSH_ENABLED = "ssh_enabled"
    SSH_FQDN_SUFFIX = "ssh_fqdn_suffix"
    SWARM_AUTOHEAL_ACTION = "swarm_autoheal_action"
    SWARM_AUTOHEAL_BOOTSTRAP_DONE = "swarm_autoheal_bootstrap_done"
    SWARM_AUTOHEAL_BOOTSTRAP_ENABLED = "swarm_autoheal_bootstrap_enabled"
    SWARM_AUTOHEAL_LAST_NOTIFY_SET = "swarm_autoheal_last_notify_set"
    SWARM_AUTOHEAL_LAST_NOTIFY_TS = "swarm_autoheal_last_notify_ts"
    SWARM_AUTOHEAL_LAST_RESTART_TS = "swarm_autoheal_last_restart_ts"
    TELEGRAM_ALLOW_DESTRUCTIVE = "telegram_allow_destructive"
    TELEGRAM_API_BASE = "telegram_api_base"
    TELEGRAM_AUTHORIZED_USER_IDS = "telegram_authorized_user_ids"
    TELEGRAM_BOT_TOKEN = "telegram_bot_token"
    TELEGRAM_CHAT_ID = "telegram_chat_id"
    TELEGRAM_LAST_UPDATE_ID = "telegram_last_update_id"
    TELEGRAM_LISTENER_ENABLED = "telegram_listener_enabled"
    TELEGRAM_THREAD_ID = "telegram_thread_id"
    TELEGRAM_USER_MAPPINGS = "telegram_user_mappings"
    TELEGRAM_VERIFY_TLS = "telegram_verify_tls"
    TOTP_ALLOWED = "totp_allowed"
    TOTP_LOCKOUT_MAX_FAILURES = "totp_lockout_max_failures"
    TOTP_LOCKOUT_MINUTES = "totp_lockout_minutes"
    TOTP_REQUIRED_FOR_ADMINS = "totp_required_for_admins"
    TOTP_REQUIRED_FOR_USERS = "totp_required_for_users"
    # Weather provider selector — values: "open-meteo" (default, no
    # API key required, NO moon data) or "weatherapi" (free key from
    # weatherapi.com, full moon-phase / illumination / moonrise /
    # moonset astronomy). Moon widget + moon AI questions
    # auto-disable when the selector is "open-meteo" (the provider
    # simply doesn't return that data).
    WEATHER_API_BASE_URL = "weather_api_base_url"
    WEATHER_API_KEY = "weather_api_key"
    WEATHER_DEFAULT_LABEL = "weather_default_label"
    WEATHER_DEFAULT_LAT = "weather_default_lat"
    WEATHER_DEFAULT_LON = "weather_default_lon"
    WEATHER_ENABLED = "weather_enabled"
    WEATHER_PROVIDER = "weather_provider"
    WEBMIN_ALIASES = "webmin_aliases"
    WEBMIN_PASSWORD = "webmin_password"
    WEBMIN_URL = "webmin_url"
    WEBMIN_USER = "webmin_user"
    WEBMIN_VERIFY_TLS = "webmin_verify_tls"


# --- Dynamic-family helpers -------------------------------------------------
# Suffix-templated keys whose suffix is computed at runtime. One helper per
# family; the prefix lives in exactly one place so a future rename is a
# one-line edit. Callers pass the dynamic suffix and never assemble the
# prefix themselves.


def ai_provider_enabled_key(name: str) -> str:
    """``ai_provider_<name>_enabled`` — per-provider master toggle."""
    return f"ai_provider_{name}_enabled"


def ai_provider_model_key(name: str) -> str:
    """``ai_provider_<name>_model`` — per-provider model identifier."""
    return f"ai_provider_{name}_model"


def ai_provider_base_url_key(name: str) -> str:
    """``ai_provider_<name>_base_url`` — per-provider HTTP base URL."""
    return f"ai_provider_{name}_base_url"


def ai_provider_api_key_key(name: str) -> str:
    """``ai_provider_<name>_api_key`` — per-provider API credential.
    Secret per the secrets contract (write-only, ``_set`` flag exposed
    through ``/api/settings``)."""
    return f"ai_provider_{name}_api_key"


def last_test_success_key(provider: str) -> str:
    """``last_test_success_<provider>`` — epoch-seconds of last
    successful Test Connection. Written by the Admin → Test buttons
    so the UI can render an "ok N minutes ago" subtitle."""
    return f"last_test_success_{provider}"


def notify_event_key(event: str) -> str:
    """``notify_event_<event>`` — per-event notification toggle.
    Static known members (every name in :data:`logic.ops.NOTIFY_EVENT_NAMES`)
    are also enumerated as ``Settings.NOTIFY_EVENT_*`` for compile-time
    safety at static call sites; this helper is for the dispatcher
    loop in ``ops.notify`` which iterates the names dynamically."""
    return f"notify_event_{event}"


def notify_medium_key(medium: str) -> str:
    """``notify_medium_<medium>`` — per-medium master switch.
    Only :attr:`Settings.NOTIFY_MEDIUM_TELEGRAM` has a static literal
    write site today; this helper covers the dynamic read in
    ``ops.notify``."""
    return f"notify_medium_{medium}"


def notify_template_title_key(event: str) -> str:
    """``notify_template_<event>_title`` — admin-edited title override
    that wins over the hard-coded default in
    :data:`logic.ops.NOTIFY_TEMPLATE_DEFAULTS`."""
    return f"notify_template_{event}_title"


def notify_template_body_key(event: str) -> str:
    """``notify_template_<event>_body`` — admin-edited body override.
    Companion to :func:`notify_template_title_key`."""
    return f"notify_template_{event}_body"
