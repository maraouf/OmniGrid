"""Three-tier tunable resolver — DB setting > env var > code default.

Each tunable currently shipped as an env-only constant gets a parallel
DB setting (``tuning_<lowercase_env_var>``); the resolver picks the
non-blank DB value first, falls back to the env var, then to the
hardcoded default. The DB value can be edited live from Admin → Config
without a restart — every consumer calls ``tuning_int(...)`` at the
point of use rather than caching it at module import.
"""
import os
import re
import sqlite3
from enum import Enum
from pathlib import Path
from typing import Optional

from logic.db import get_setting


def _read_raw_tunable(db_key: str, env_var: str) -> tuple[str, str]:
    """Resolve the raw DB + env strings for one tunable.

    Returns ``(raw_db, env_raw)`` — both as bare strings (no parsing,
    no clamping). DB unreachable (config-error boot path) returns an
    empty ``raw_db`` rather than raising. Shared by :func:`tuning_int`
    and :func:`effective_state` so the DB-vs-env precedence and the
    narrow exception class stay in one place.
    """
    try:
        raw_db = (get_setting(db_key) or "").strip()
    except (sqlite3.Error, RuntimeError, OSError):
        # DB unreachable. Specific paths covered:
        #   sqlite3.Error    — corrupt DB / lock / I/O error mid-query.
        #   RuntimeError     — `db_conn()` raises this when DB_PATH is
        #                      unset (config-error boot path before the
        #                      .env has been provisioned).
        #   OSError          — DB file inaccessible at the filesystem
        #                      level (path doesn't exist / permission).
        # Any of these → skip straight to env / default rather than
        # propagating the boot-path failure into every tunable read.
        raw_db = ""
    return raw_db, os.getenv(env_var, "")


class Tunable(str, Enum):
    """Typed enum of every operator-tunable knob declared in
    :data:`TUNABLES`. Inherits ``str`` so existing dict lookups,
    ``tuning_int(key)`` calls, and SQLite parameter binding all
    continue to work — ``Tunable.AI_MAX_TOKENS == "tuning_ai_max_tokens"``
    is true, ``hash()`` agrees, and ``TUNABLES[Tunable.AI_MAX_TOKENS]``
    finds the same entry as ``TUNABLES["tuning_ai_max_tokens"]``.

    Naming: drop the ``tuning_`` prefix, uppercase the rest, keep the
    underscores. So ``tuning_ai_max_tokens`` -> ``Tunable.AI_MAX_TOKENS``.

    Use this enum at every static call site (``tuning_int(Tunable.X)``,
    ``TUNABLES[Tunable.X]``). Dynamic keys built from f-strings — e.g.
    ``f"tuning_{provider}_failure_pause_rounds"`` — stay as plain strings;
    the enum is for compile-time-known keys only.

    Members are sorted alphabetically by string value for stable diffs.
    Every addition to ``TUNABLES`` must add a matching member here; the
    smoke check in ``__main__`` catches missing / extra members.
    """
    ADGUARD_HISTORY_DAYS = "tuning_adguard_history_days"
    ADGUARD_SAMPLE_INTERVAL_SECONDS = "tuning_adguard_sample_interval_seconds"
    ADGUARDSYNC_HISTORY_DAYS = "tuning_adguardsync_history_days"
    ADGUARDSYNC_SAMPLE_INTERVAL_SECONDS = "tuning_adguardsync_sample_interval_seconds"
    AI_CONVERSATION_EXPORT_ENABLED = "tuning_ai_conversation_export_enabled"
    AI_CONVERSATION_PERSIST_INTERVAL_MS = "tuning_ai_conversation_persist_interval_ms"
    AI_EXTENDED_HTTP_TIMEOUT_SECONDS = "tuning_ai_extended_http_timeout_seconds"
    AI_FALLBACK_MAX_DEPTH = "tuning_ai_fallback_max_depth"
    AI_HTTP_TIMEOUT_SECONDS = "tuning_ai_http_timeout_seconds"
    AI_LOG_CONTEXT_HOURS = "tuning_ai_log_context_hours"
    AI_LOG_CONTEXT_LINES = "tuning_ai_log_context_lines"
    AI_MAX_TOKENS = "tuning_ai_max_tokens"
    AI_RETRY_BACKOFF_MS = "tuning_ai_retry_backoff_ms"
    AI_RETRY_ENABLED = "tuning_ai_retry_enabled"
    AI_RETRY_FIRST_ATTEMPT_MAX_MS = "tuning_ai_retry_first_attempt_max_ms"
    AI_SIDEBAR_WIDTH_PX = "tuning_ai_sidebar_width_px"
    APC_HISTORY_DAYS = "tuning_apc_history_days"
    APPS_EXTRAS_TTL_SECONDS = "tuning_apps_extras_ttl_seconds"
    APPS_ROUTE_BUDGET_SECONDS = "tuning_apps_route_budget_seconds"
    APPS_TILE_RENDER_BATCH = "tuning_apps_tile_render_batch"
    ASSET_INVENTORY_FETCH_TIMEOUT_SECONDS = "tuning_asset_inventory_fetch_timeout_seconds"
    ASSET_INVENTORY_TOKEN_TIMEOUT_SECONDS = "tuning_asset_inventory_token_timeout_seconds"
    AUTH_FAILURE_COOLDOWN_SECONDS = "tuning_auth_failure_cooldown_seconds"
    BACKEND_UNREACHABLE_THRESHOLD_SECONDS = "tuning_backend_unreachable_threshold_seconds"
    BACKUP_RETENTION_COUNT = "tuning_backup_retention_count"
    BESZEL_FAILURE_PAUSE_ROUNDS = "tuning_beszel_failure_pause_rounds"
    BESZEL_PROBE_TIMEOUT_SECONDS = "tuning_beszel_probe_timeout_seconds"
    BESZEL_SAMPLE_INTERVAL_SECONDS = "tuning_beszel_sample_interval_seconds"
    CACHE_TTL_SECONDS = "tuning_cache_ttl_seconds"
    CONFIG_BACKUP_RETENTION_COUNT = "tuning_config_backup_retention_count"
    DDNS_HISTORY_DAYS = "tuning_ddns_history_days"
    DDNS_SAMPLE_INTERVAL_SECONDS = "tuning_ddns_sample_interval_seconds"
    DDNS_STALE_RECORD_HOURS = "tuning_ddns_stale_record_hours"
    FAVICON_CACHE_DAYS = "tuning_favicon_cache_days"
    FAVICON_FETCH_TIMEOUT_SECONDS = "tuning_favicon_fetch_timeout_seconds"
    FING_HISTORY_DAYS = "tuning_fing_history_days"
    FING_NEW_DEVICE_HOURS = "tuning_fing_new_device_hours"
    FING_SAMPLE_INTERVAL_SECONDS = "tuning_fing_sample_interval_seconds"
    FLARESOLVERR_HISTORY_DAYS = "tuning_flaresolverr_history_days"
    FLARESOLVERR_SAMPLE_INTERVAL_SECONDS = "tuning_flaresolverr_sample_interval_seconds"
    GATHER_CLIENT_TIMEOUT_SECONDS = "tuning_gather_client_timeout_seconds"
    GATHER_ORPHAN_PROBE_TIMEOUT_SECONDS = "tuning_gather_orphan_probe_timeout_seconds"
    HOST_BASELINE_FIRST_TICK_DELAY_SECONDS = "tuning_host_baseline_first_tick_delay_seconds"
    HOST_BASELINE_MIN_SAMPLES = "tuning_host_baseline_min_samples"
    HOST_BASELINE_RECOMPUTE_INTERVAL_SECONDS = "tuning_host_baseline_recompute_interval_seconds"
    HOST_BASELINE_WINDOW_DAYS = "tuning_host_baseline_window_days"
    HOST_METRICS_PROBE_CONCURRENCY = "tuning_host_metrics_probe_concurrency"
    HOST_PERMANENT_FAIL_WINDOW_SECONDS = "tuning_host_permanent_fail_window_seconds"
    HOST_PROVIDER_CACHE_TTL_SECONDS = "tuning_host_provider_cache_ttl_seconds"
    HOST_PROVIDER_CACHE_DIAG_INTERVAL = "tuning_host_provider_cache_diag_interval"
    STATS_PER_NODE_UNREACHABLE_TTL_SECONDS = "tuning_stats_per_node_unreachable_ttl_seconds"
    DNS_FAILED_SKIP_SECONDS = "tuning_dns_failed_skip_seconds"
    BESZEL_PROBE_TIMEOUT_UNREACHABLE_SECONDS = "tuning_beszel_probe_timeout_unreachable_seconds"
    PULSE_PROBE_TIMEOUT_UNREACHABLE_SECONDS = "tuning_pulse_probe_timeout_unreachable_seconds"
    QBITTORRENT_HISTORY_DAYS = "tuning_qbittorrent_history_days"
    QBITTORRENT_SAMPLE_INTERVAL_SECONDS = "tuning_qbittorrent_sample_interval_seconds"
    UNIFI_HISTORY_DAYS = "tuning_unifi_history_days"
    UNIFI_SAMPLE_INTERVAL_SECONDS = "tuning_unifi_sample_interval_seconds"
    TDARR_HISTORY_DAYS = "tuning_tdarr_history_days"
    TDARR_SAMPLE_INTERVAL_SECONDS = "tuning_tdarr_sample_interval_seconds"
    KAVITA_HISTORY_DAYS = "tuning_kavita_history_days"
    KAVITA_SAMPLE_INTERVAL_SECONDS = "tuning_kavita_sample_interval_seconds"
    PROWLARR_HISTORY_DAYS = "tuning_prowlarr_history_days"
    PROWLARR_SAMPLE_INTERVAL_SECONDS = "tuning_prowlarr_sample_interval_seconds"
    SLOW_QUERY_THRESHOLD_MS = "tuning_slow_query_threshold_ms"
    HOST_PROVIDER_CONFIG_CACHE_TTL_SECONDS = "tuning_host_provider_config_cache_ttl_seconds"
    HOST_SNAPSHOT_STALE_FIELD_MAX_AGE_HOURS = "tuning_host_snapshot_stale_field_max_age_hours"
    HOST_SNAPSHOTS_CACHE_TTL_SECONDS = "tuning_host_snapshots_cache_ttl_seconds"
    HOSTS_IDLE_FILL_INTERVAL_SECONDS = "tuning_hosts_idle_fill_interval_seconds"
    HOSTS_PARALLEL_FETCH = "tuning_hosts_parallel_fetch"
    HTTP_PROBE_CERT_WARNING_DAYS = "tuning_http_probe_cert_warning_days"
    HTTP_PROBE_CONCURRENCY = "tuning_http_probe_concurrency"
    HTTP_PROBE_DEFAULT_ACCEPTED_HI_CODE = "tuning_http_probe_default_accepted_hi_code"
    HTTP_PROBE_DEFAULT_ACCEPTED_LO_CODE = "tuning_http_probe_default_accepted_lo_code"
    HTTP_PROBE_DNS_TIMEOUT_SECONDS = "tuning_http_probe_dns_timeout_seconds"
    HTTP_PROBE_FAILURE_PAUSE_ROUNDS = "tuning_http_probe_failure_pause_rounds"
    HTTP_PROBE_HOST_CACHE_TTL_SECONDS = "tuning_http_probe_host_cache_ttl_seconds"
    HTTP_PROBE_HOST_FAIL_CACHE_TTL_SECONDS = "tuning_http_probe_host_fail_cache_ttl_seconds"
    HTTP_PROBE_SAMPLE_INTERVAL_SECONDS = "tuning_http_probe_sample_interval_seconds"
    HTTP_PROBE_TIMEOUT_SECONDS = "tuning_http_probe_timeout_seconds"
    IMAGE_PROXY_CACHE_MAX_ENTRIES = "tuning_image_proxy_cache_max_entries"
    IMAGE_PROXY_CACHE_TTL_SECONDS = "tuning_image_proxy_cache_ttl_seconds"
    INCIDENTS_RETENTION_DAYS = "tuning_incidents_retention_days"
    KICK_GATHER_TIMEOUT_SECONDS = "tuning_kick_gather_timeout_seconds"
    LOAD_BUSY_MAX_SECONDS = "tuning_load_busy_max_seconds"
    LOG_RETENTION_DAYS = "tuning_log_retention_days"
    NODE_EXPORTER_FAILURE_PAUSE_ROUNDS = "tuning_node_exporter_failure_pause_rounds"
    NODE_EXPORTER_PROBE_TIMEOUT_SECONDS = "tuning_node_exporter_probe_timeout_seconds"
    NODE_EXPORTER_SAMPLE_INTERVAL_SECONDS = "tuning_node_exporter_sample_interval_seconds"
    NOTIFICATION_PAGE_SIZE = "tuning_notification_page_size"
    NOTIFICATION_RETENTION_DAYS = "tuning_notification_retention_days"
    NOTIFICATIONS_POLL_INTERVAL_SECONDS = "tuning_notifications_poll_interval_seconds"
    OIDC_HTTP_TIMEOUT_SECONDS = "tuning_oidc_http_timeout_seconds"
    OPS_POLL_INTERVAL_SECONDS = "tuning_ops_poll_interval_seconds"
    PIHOLE_HISTORY_DAYS = "tuning_pihole_history_days"
    PIHOLE_SAMPLE_INTERVAL_SECONDS = "tuning_pihole_sample_interval_seconds"
    PING_CONCURRENCY = "tuning_ping_concurrency"
    PING_COOLDOWN_SECONDS = "tuning_ping_cooldown_seconds"
    PING_DEFAULT_PORT = "tuning_ping_default_port"
    PING_FAILURE_PAUSE_ROUNDS = "tuning_ping_failure_pause_rounds"
    PING_INTERVAL_SECONDS = "tuning_ping_interval_seconds"
    PING_PACKET_INTERVAL_MS = "tuning_ping_packet_interval_ms"
    PING_PROBE_TIMEOUT_SECONDS = "tuning_ping_probe_timeout_seconds"
    POLLOPS_SSE_KEEPALIVE_SECONDS = "tuning_pollops_sse_keepalive_seconds"
    PORT_SCAN_BANNER_READ_SECONDS = "tuning_port_scan_banner_read_seconds"
    PORT_SCAN_DEFAULT_CONCURRENCY = "tuning_port_scan_default_concurrency"
    PORT_SCAN_DEFAULT_TIMEOUT_SECONDS = "tuning_port_scan_default_timeout_seconds"
    PORT_SCAN_MAX_SECONDS = "tuning_port_scan_max_seconds"
    PORT_SCAN_RETENTION_DAYS = "tuning_port_scan_retention_days"
    PORT_SCAN_SCHEDULE_MAX_HOSTS_PER_TICK = "tuning_port_scan_schedule_max_hosts_per_tick"
    PORT_SCAN_SCHEDULE_MIN_AGE_SECONDS = "tuning_port_scan_schedule_min_age_seconds"
    PORT_SCAN_SCHEDULE_PER_HOST_CONCURRENCY = "tuning_port_scan_schedule_per_host_concurrency"
    SCHEDULE_STUCK_RUN_THRESHOLD_SECONDS = "tuning_schedule_stuck_run_threshold_seconds"
    PORT_SCAN_UDP_DEFAULT_CONCURRENCY = "tuning_port_scan_udp_default_concurrency"
    PORT_SCAN_UDP_DEFAULT_TIMEOUT_SECONDS = "tuning_port_scan_udp_default_timeout_seconds"
    PORTAINER_OP_TIMEOUT_LONG_SECONDS = "tuning_portainer_op_timeout_long_seconds"
    PORTAINER_OP_TIMEOUT_MEDIUM_SECONDS = "tuning_portainer_op_timeout_medium_seconds"
    PORTAINER_OP_TIMEOUT_SHORT_SECONDS = "tuning_portainer_op_timeout_short_seconds"
    PRAYER_TIMES_CACHE_TTL_SECONDS = "tuning_prayer_times_cache_ttl_seconds"
    PRAYER_TIMES_FETCH_TIMEOUT_SECONDS = "tuning_prayer_times_fetch_timeout_seconds"
    PRAYER_TIMES_HISTORY_RETENTION_DAYS = "tuning_prayer_times_history_retention_days"
    PRAYER_TIMES_REMINDER_CHECK_INTERVAL_SECONDS = "tuning_prayer_times_reminder_check_interval_seconds"
    PRAYER_TIMES_REMINDER_LEAD_MINUTES = "tuning_prayer_times_reminder_lead_minutes"
    PRAYER_TIMES_SAMPLER_INTERVAL_SECONDS = "tuning_prayer_times_sampler_interval_seconds"
    PUBLIC_IP_CACHE_TTL_SECONDS = "tuning_public_ip_cache_ttl_seconds"
    PUBLIC_IP_FETCH_TIMEOUT_SECONDS = "tuning_public_ip_fetch_timeout_seconds"
    PUBLIC_IP_SAMPLE_INTERVAL_SECONDS = "tuning_public_ip_sample_interval_seconds"
    PULSE_FAILURE_PAUSE_ROUNDS = "tuning_pulse_failure_pause_rounds"
    PULSE_PROBE_TIMEOUT_SECONDS = "tuning_pulse_probe_timeout_seconds"
    PULSE_SAMPLE_INTERVAL_SECONDS = "tuning_pulse_sample_interval_seconds"
    RATE_LIMIT_LOCKOUT_SECONDS = "tuning_rate_limit_lockout_seconds"
    RATE_LIMIT_MAX_FAILURES = "tuning_rate_limit_max_failures"
    RATE_LIMIT_WINDOW_SECONDS = "tuning_rate_limit_window_seconds"
    REGISTRY_CONCURRENCY = "tuning_registry_concurrency"
    REGISTRY_DIGEST_CACHE_TTL_SECONDS = "tuning_registry_digest_cache_ttl_seconds"
    SNMP_CONCURRENCY = "tuning_snmp_concurrency"
    SNMP_DEFAULT_PORT = "tuning_snmp_default_port"
    SNMP_FAILURE_PAUSE_ROUNDS = "tuning_snmp_failure_pause_rounds"
    SNMP_HOST_CACHE_TTL_SECONDS = "tuning_snmp_host_cache_ttl_seconds"
    SNMP_HOST_FAIL_CACHE_TTL_SECONDS = "tuning_snmp_host_fail_cache_ttl_seconds"
    SNMP_PER_HOST_WALK_CONCURRENCY = "tuning_snmp_per_host_walk_concurrency"
    SNMP_PROBE_TIMEOUT_SECONDS = "tuning_snmp_probe_timeout_seconds"
    SNMP_SAMPLE_INTERVAL_SECONDS = "tuning_snmp_sample_interval_seconds"
    SNMP_UNREACHABLE_COOLDOWN_SECONDS = "tuning_snmp_unreachable_cooldown_seconds"
    SNMP_WALK_CONCURRENCY_CISCO = "tuning_snmp_walk_concurrency_cisco"
    SNMP_WALK_CONCURRENCY_DELL = "tuning_snmp_walk_concurrency_dell"
    SNMP_WALK_CONCURRENCY_PRINTER = "tuning_snmp_walk_concurrency_printer"
    SNMP_WALK_CONCURRENCY_SYNOLOGY = "tuning_snmp_walk_concurrency_synology"
    SNMP_WALK_CONCURRENCY_UCD = "tuning_snmp_walk_concurrency_ucd"
    SNMP_WALL_CLOCK_BUDGET_SECONDS = "tuning_snmp_wall_clock_budget_seconds"
    SEERR_HISTORY_DAYS = "tuning_seerr_history_days"
    SEERR_SAMPLE_INTERVAL_SECONDS = "tuning_seerr_sample_interval_seconds"
    SEERR_SUGGEST_COOLDOWN_HOURS = "tuning_seerr_suggest_cooldown_hours"
    SERVARR_HISTORY_DAYS = "tuning_servarr_history_days"
    SERVARR_SAMPLE_INTERVAL_SECONDS = "tuning_servarr_sample_interval_seconds"
    SERVICE_PROBE_CONCURRENCY = "tuning_service_probe_concurrency"
    SERVICE_PROBE_FAILURE_PAUSE_ROUNDS = "tuning_service_probe_failure_pause_rounds"
    SERVICE_PROBE_SAMPLE_INTERVAL_SECONDS = "tuning_service_probe_sample_interval_seconds"
    SERVICE_PROBE_TIMEOUT_SECONDS = "tuning_service_probe_timeout_seconds"
    SPEEDTEST_HISTORY_DAYS = "tuning_speedtest_history_days"
    SPEEDTEST_SAMPLE_INTERVAL_SECONDS = "tuning_speedtest_sample_interval_seconds"
    SSE_HEARTBEAT_SECONDS = "tuning_sse_heartbeat_seconds"
    SSE_IDLE_THRESHOLD_SECONDS = "tuning_sse_idle_threshold_seconds"
    SSE_MAX_LIFETIME_SECONDS = "tuning_sse_max_lifetime_seconds"
    SSH_CLOSE_TIMEOUT_SECONDS = "tuning_ssh_close_timeout_seconds"
    SSH_DEFAULT_PORT = "tuning_ssh_default_port"
    SSH_TERMINAL_CONNECT_TIMEOUT_SECONDS = "tuning_ssh_terminal_connect_timeout_seconds"
    SSH_TERMINAL_LOGIN_TIMEOUT_SECONDS = "tuning_ssh_terminal_login_timeout_seconds"
    SSH_WS_HEARTBEAT_SECONDS = "tuning_ssh_ws_heartbeat_seconds"
    STACK_UPDATE_OBSERVE_POLL_SECONDS = "tuning_stack_update_observe_poll_seconds"
    STACK_UPDATE_OBSERVE_TIMEOUT_SECONDS = "tuning_stack_update_observe_timeout_seconds"
    STAT_BAR_CRIT_PCT = "tuning_stat_bar_crit_pct"
    STAT_BAR_WARN_PCT = "tuning_stat_bar_warn_pct"
    STATS_CACHE_TTL_SECONDS = "tuning_stats_cache_ttl_seconds"
    STATS_CONCURRENCY = "tuning_stats_concurrency"
    STATS_HISTORY_DAYS = "tuning_stats_history_days"
    STATS_SAMPLE_INTERVAL_SECONDS = "tuning_stats_sample_interval_seconds"
    DB_SIZE_SAMPLE_INTERVAL_SECONDS = "tuning_db_size_sample_interval_seconds"
    DB_SIZE_HISTORY_DAYS = "tuning_db_size_history_days"
    STATS_TARGETED_TIMEOUT_SECONDS = "tuning_stats_targeted_timeout_seconds"
    STATS_UNTARGETED_TIMEOUT_SECONDS = "tuning_stats_untargeted_timeout_seconds"
    SWARM_AGENT_UNHEALTHY_THRESHOLD = "tuning_swarm_agent_unhealthy_threshold"
    SWARM_AUTOHEAL_COOLDOWN_MINUTES = "tuning_swarm_autoheal_cooldown_minutes"
    TELEGRAM_AI_CALLS_PER_MINUTE = "tuning_telegram_ai_calls_per_minute"
    TELEGRAM_BULK_UPDATE_CONCURRENCY = "tuning_telegram_bulk_update_concurrency"
    TELEGRAM_DESTRUCTIVE_COOLDOWN_SECONDS = "tuning_telegram_destructive_cooldown_seconds"
    TELEGRAM_HTTP_TIMEOUT_SECONDS = "tuning_telegram_http_timeout_seconds"
    TELEGRAM_LONG_POLL_TIMEOUT_SECONDS = "tuning_telegram_long_poll_timeout_seconds"
    VERSION_POLL_INTERVAL_SECONDS = "tuning_version_poll_interval_seconds"
    WEATHER_CACHE_TTL_SECONDS = "tuning_weather_cache_ttl_seconds"
    WEATHER_FETCH_TIMEOUT_SECONDS = "tuning_weather_fetch_timeout_seconds"
    WEATHER_HISTORY_RETENTION_DAYS = "tuning_weather_history_retention_days"
    WEATHER_SAMPLER_INTERVAL_SECONDS = "tuning_weather_sampler_interval_seconds"
    WEBMIN_FAILURE_PAUSE_ROUNDS = "tuning_webmin_failure_pause_rounds"
    WEBMIN_HOST_CACHE_TTL_SECONDS = "tuning_webmin_host_cache_ttl_seconds"
    WEBMIN_HOST_FAIL_CACHE_TTL_SECONDS = "tuning_webmin_host_fail_cache_ttl_seconds"
    WEBMIN_PROBE_BUDGET_SECONDS = "tuning_webmin_probe_budget_seconds"
    WEBMIN_PROBE_TIMEOUT_SECONDS = "tuning_webmin_probe_timeout_seconds"
    WEBMIN_SAMPLER_BUDGET_SECONDS = "tuning_webmin_sampler_budget_seconds"


# Authoritative table of (db_key, env_var, default, min, max). The UI
# editor + the validator + the resolver all reference this single
# source of truth — adding a new knob means one edit here + extending
# the SettingsIn model + one row in the Admin → Config form.
TUNABLES: dict[str, tuple[str, int, int, int]] = {
    "tuning_cache_ttl_seconds": ("CACHE_TTL_SECONDS", 900, 30, 86400),
    "tuning_stats_cache_ttl_seconds": ("STATS_CACHE_TTL_SECONDS", 30, 5, 3600),
    "tuning_registry_concurrency": ("REGISTRY_CONCURRENCY", 8, 1, 64),
    "tuning_registry_digest_cache_ttl_seconds": ("REGISTRY_DIGEST_CACHE_TTL_SECONDS", 600, 0, 86400),
    "tuning_stats_concurrency": ("STATS_CONCURRENCY", 16, 1, 128),
    # Per-container stats fetch timeouts. `_one_container_stats` makes
    # up to two HTTP calls per running container per gather:
    # 1. Targeted (with `X-PortainerAgent-Target=<host>`) — fast-fail
    #    so we don't hang the gather on a dead worker. Bumped from
    #    4s to 12s after operator-reported worker-node stats coming
    #    back empty: Portainer's agent forwarding can take longer
    #    than 4s on busy nodes, the call would time out, and the
    #    untargeted fallback would 404 (manager doesn't have the
    #    worker's cid).
    # 2. Untargeted (no agent-target header) — fallback when the
    #    targeted call fails. Manager-local containers respond
    #    directly; worker-node cids return 404 here. 10s default.
    # Both knobs operator-tunable so a flaky / slow Portainer setup
    # can be loosened without a redeploy. Range 1..60s — anything
    # under 1s is too short for the round-trip; anything over 60s
    # would block the whole gather behind one slow container.
    "tuning_stats_targeted_timeout_seconds": ("STATS_TARGETED_TIMEOUT_SECONDS", 12, 1, 60),
    "tuning_stats_untargeted_timeout_seconds": ("STATS_UNTARGETED_TIMEOUT_SECONDS", 10, 1, 60),
    # Swarm agent unhealthy-banner threshold. After N consecutive
    # gather cycles where a Swarm node had ≥1 running task cid but
    # ZERO successful `/containers/{cid}/stats` calls (across the
    # resolved-node agent_target retry, untargeted fallback, AND
    # the brute-force every-other-host fallback), the SPA flags the
    # node's Portainer agent as unhealthy via the banner in the
    # Stacks / Hosts views. Default 3 — covers transient hub blips
    # without spamming the banner; lower for faster operator
    # feedback at the cost of more false-positive flickers; raise
    # for noisy fleets where one bad gather isn't worth surfacing.
    # Range 1..20.
    "tuning_swarm_agent_unhealthy_threshold": ("SWARM_AGENT_UNHEALTHY_THRESHOLD", 3, 1, 20),
    # Swarm autoheal cooldown — how many minutes must pass between
    # consecutive auto-restart actions on the same agent service so a
    # thrashing agent doesn't pin the manager in a restart loop.
    # `swarm_agent_health` schedule kind reads the in-memory module-
    # level last-action timestamp on every fire and short-circuits
    # when the elapsed time is under this threshold. Default 30 min;
    # range 1..1440 (24h max). Operators with very fragile fleets
    # may want to lower it; chronic-flapper situations want it
    # raised to give manual investigation time before another
    # restart cycle. Notify-mode bypasses the cooldown — only the
    # restart action consumes it.
    "tuning_swarm_autoheal_cooldown_minutes": ("SWARM_AUTOHEAL_COOLDOWN_MINUTES", 30, 1, 1440),
    # Telegram listener long-poll timeout (seconds). Telegram holds the
    # `getUpdates` connection open this many seconds waiting for an
    # update before responding. Server-side cap is 50; default 25
    # balances "fast wake-up on inactivity" with "amortise round-trip
    # cost on a busy chat". Range 1..50.
    "tuning_telegram_long_poll_timeout_seconds": ("TELEGRAM_LONG_POLL_TIMEOUT_SECONDS", 25, 1, 50),
    # Telegram listener outer HTTP wall-clock (seconds). Slightly larger
    # than the long-poll timeout so Telegram has time to flush the
    # response after a long-poll wake-up. Default 35 (= 25 + 10). Range
    # 5..120. Operators behind a tight reverse-proxy `proxy_read_timeout`
    # may want to lower both this and the long-poll timeout in lock-step.
    "tuning_telegram_http_timeout_seconds": ("TELEGRAM_HTTP_TIMEOUT_SECONDS", 35, 5, 120),
    # Telegram destructive-command cooldown (seconds). After a destructive
    # verb (`/restart`, `/cleanup`, `/update` confirm flow) lands, the
    # same (sender_id, command) pair is rate-limited for this many seconds
    # so a duplicate / fat-fingered re-send doesn't double-execute. The
    # 30s default fits a single-admin chat; multi-admin chats may want
    # higher (60-120s) to prevent admin-vs-admin race. Lower for solo
    # operators who intentionally re-fire after a real wait. Range 1..600.
    "tuning_telegram_destructive_cooldown_seconds": ("TELEGRAM_DESTRUCTIVE_COOLDOWN_SECONDS", 30, 1, 600),
    # Per-Telegram-user AI rate limit (calls per minute). An authorised
    # user typing rapid-fire questions can rack up AI calls faster than
    # the user intends — every non-`/command` Telegram message routes
    # through the AI palette, which costs real money on every paid
    # provider. The listener's `_ai_reply` consults this counter via a
    # tiny in-memory bucket keyed by Telegram user_id; over-quota
    # senders get a short "slow down" reply instead of an AI call.
    # Default 6 (one every 10s) — generous for human typing, tight
    # enough to catch a runaway bot/loop. Range 1..120; set to 120 to
    # effectively disable the limit on a private bot you trust.
    "tuning_telegram_ai_calls_per_minute": ("TELEGRAM_AI_CALLS_PER_MINUTE", 6, 1, 120),
    # `/update all` fan-out concurrency cap. When the operator types
    # `/update all confirm`, the dispatcher iterates every item with
    # an available update + spawns a Portainer write-op per item.
    # Pre-fix this was unbounded — N=50 pending updates fanned out 50
    # parallel Portainer PUTs, overwhelming the daemon and starving
    # legitimate operator-triggered updates. Bound the fan-out so the
    # dispatcher walks the queue with a semaphore — at most K stack /
    # container updates concurrent. Default 4 balances rollout speed
    # against Portainer load on a single-manager Swarm. Range 1..16;
    # set to 1 for strictly sequential rollouts (safe for small Pi
    # deployments) or raise for fast multi-node Swarms. Same bound
    # applies to bulk `/restart`-style dispatches.
    "tuning_telegram_bulk_update_concurrency": ("TELEGRAM_BULK_UPDATE_CONCURRENCY", 4, 1, 16),
    # New-version watcher poll cadence (seconds). The SPA's
    # `startVersionWatcher` polls `/api/version` on this interval so a
    # deploy that lands while the operator has the tab open surfaces the
    # "New version — reload" banner. Default 60s; lower it (e.g. 15-30s)
    # to shorten the detection lag on a fast-iterating deploy, raise it
    # to cut idle traffic. `/api/version` is a cheap public endpoint so
    # the floor is 10s. Delivered to the SPA via `client_config.version_poll_ms`
    # (× 1000). Range 10..3600.
    "tuning_version_poll_interval_seconds": ("VERSION_POLL_INTERVAL_SECONDS", 60, 10, 3600),
    # Seerr movie-suggestion dedupe cooldown (hours). When the AI suggests
    # a movie (Telegram or SPA palette), its TMDB id is recorded per user;
    # for this many hours that id is skipped so the same film doesn't cycle
    # back as a "fresh" suggestion in the same session. Default 12h covers a
    # typical browsing session without permanently exhausting the catalogue.
    # Range 0..168 (a week); 0 disables dedupe entirely (every suggestion
    # draws from the full pool again).
    "tuning_seerr_suggest_cooldown_hours": ("SEERR_SUGGEST_COOLDOWN_HOURS", 12, 0, 168),
    "tuning_stats_history_days": ("STATS_HISTORY_DAYS", 7, 1, 365),
    "tuning_stats_sample_interval_seconds": ("STATS_SAMPLE_INTERVAL_SECONDS", 300, 30, 3600),
    # Stats -> Database growth projection: how often a DB file-size sample
    # is recorded (default daily — growth is a multi-day timescale) and how
    # long the size history is retained. Retention floor is 30 days because
    # the 90-day forward projection is fit over the trailing 30-day window;
    # a longer retention just gives the regression more points + a longer
    # actual-history tail on the chart.
    "tuning_db_size_sample_interval_seconds": ("DB_SIZE_SAMPLE_INTERVAL_SECONDS", 86400, 3600, 604800),
    "tuning_db_size_history_days": ("DB_SIZE_HISTORY_DAYS", 120, 30, 730),
    # host_baseline_sampler cadence — controls how often the lifespan
    # task recomputes per-host rolling baselines for drift detection.
    # Baselines move slowly (30-day rolling window) so hourly is the
    # default; high-churn fleets (workload changes weekly) may want
    # 30min, stable fleets (workload monotonic) may dial up to 6h to
    # free DB cycles. Floor 60s prevents accidentally busy-looping;
    # ceiling 86400 (24h) prevents accidentally disabling.
    "tuning_host_baseline_recompute_interval_seconds": ("HOST_BASELINE_RECOMPUTE_INTERVAL_SECONDS", 3600, 60, 86400),
    # First-tick delay — gives schema migrations time to land before
    # the sampler reads `host_baselines`. Operators rebooting under
    # heavy load can raise; default is comfortably fast.
    "tuning_host_baseline_first_tick_delay_seconds": ("HOST_BASELINE_FIRST_TICK_DELAY_SECONDS", 60, 5, 600),
    # Minimum sample count for the IQR baseline to be statistically
    # meaningful. Below this, the metric stays unbaselined (drift chip
    # is hidden). Default 20 = the practical floor for Tukey IQR; ~1.5h
    # to surface at the default 5-min sampler cadence. Operators wanting
    # tighter statistical confidence can raise (50 was the original).
    # Range 5..500.
    "tuning_host_baseline_min_samples": ("HOST_BASELINE_MIN_SAMPLES", 20, 5, 500),
    # Rolling-window lookback (days) for the baseline computer. Default
    # 30 days. Lower for high-churn workloads where "normal" shifts
    # weekly; raise to smooth seasonal patterns. Range 1..365.
    "tuning_host_baseline_window_days": ("HOST_BASELINE_WINDOW_DAYS", 30, 1, 365),
    # Wall-clock cap for the cold-cache `_gather()` kick on cache-
    # missing drill-down endpoints. Default 30s — enough headroom for
    # a typical Portainer fan-out. Operators with large fleets / slow
    # registries hit this on incident-mode lookups; bump to 60-120s
    # in those environments. Floor 5s prevents accidentally disabling.
    "tuning_kick_gather_timeout_seconds": ("KICK_GATHER_TIMEOUT_SECONDS", 30, 5, 300),
    # Portainer write-op wall-clocks. Three tiers — short for quick
    # container restart / remove (default 120s), medium for service-
    # level ops + prune (default 300s), long for stack updates +
    # image-pull-heavy paths (default 600s). Operators on slow
    # networks / large stacks / slow registries hit these timeouts
    # under incident load; raise the appropriate tier when the
    # specific op-class is consistently timing out. Lower tiers'
    # ceilings are intentionally tighter than the upper tiers'
    # floors so admins can't accidentally invert the ordering.
    "tuning_portainer_op_timeout_short_seconds": ("PORTAINER_OP_TIMEOUT_SHORT_SECONDS", 120, 10, 600),
    "tuning_portainer_op_timeout_medium_seconds": ("PORTAINER_OP_TIMEOUT_MEDIUM_SECONDS", 300, 30, 1800),
    "tuning_portainer_op_timeout_long_seconds": ("PORTAINER_OP_TIMEOUT_LONG_SECONDS", 600, 60, 3600),
    # Prayer Times module. Standalone subsystem — the custom-dashboard
    # widget tile + AI palette + Telegram /prayer + /hijri commands all
    # consume it. The master enable toggle is the plain `prayer_times_enabled`
    # SETTING (like `weather_enabled`), NOT a tunable — so the admin toggle
    # loads with the rest of the settings; only the cache-TTL + fetch-timeout
    # below are tunables.
    # Prayer-times in-process cache TTL. Daily prayer times are static
    # for a given day, so a 1-hour default is plenty (re-fetches hourly,
    # picks up the new day across midnight). Lower for fresher data after
    # a method/location change; raise to spare api.aladhan.com on large
    # multi-user deploys. Range 300..21600.
    "tuning_prayer_times_cache_ttl_seconds": ("PRAYER_TIMES_CACHE_TTL_SECONDS", 3600, 300, 21600),
    # Prayer-times outbound HTTP wall-clock. api.aladhan.com normally
    # answers well under 1s; raise on slow links / proxy paths.
    # Range 2..60.
    "tuning_prayer_times_fetch_timeout_seconds": ("PRAYER_TIMES_FETCH_TIMEOUT_SECONDS", 8, 2, 60),
    # Cadence of the lifespan-managed prayer-times sampler that writes
    # one row per day per location into prayer_times_samples (drives the
    # Admin → Prayer Times history table). Prayer timings are daily-static,
    # so the default is 6h (21600s) rather than weather's hourly — the new
    # day's timings still appear within 6h of midnight. 0 disables the
    # sampler entirely (the on-demand cache for the widget / AI / Telegram
    # still works). Range 0..86400.
    "tuning_prayer_times_sampler_interval_seconds": ("PRAYER_TIMES_SAMPLER_INTERVAL_SECONDS", 21600, 0, 86400),
    # Retention window for prayer_times_samples. Pruned hourly by the
    # sampler. 0 keeps every day's row forever (the table is tiny — one
    # row per location per day). Range 0..3650.
    "tuning_prayer_times_history_retention_days": ("PRAYER_TIMES_HISTORY_RETENTION_DAYS", 90, 0, 3650),
    # How many minutes BEFORE each prayer the reminder notification fires
    # (logic.prayer_reminders). Per-user opt-in + medium selection lives in
    # Profile -> Notifications; this is the global lead time. 0 disables
    # prayer reminders entirely (the widget / history are unaffected).
    # Range 0..240.
    "tuning_prayer_times_reminder_lead_minutes": ("PRAYER_TIMES_REMINDER_LEAD_MINUTES", 10, 0, 240),
    # Cadence of the prayer-reminder check loop. Lower = tighter firing
    # accuracy (the reminder lands within one tick of the lead mark);
    # higher = less work. 30s gives ~sub-minute accuracy. Range 15..600.
    "tuning_prayer_times_reminder_check_interval_seconds": ("PRAYER_TIMES_REMINDER_CHECK_INTERVAL_SECONDS", 30, 15, 600),
    # Public-IP lookup module. Standalone subsystem (NOT AI-related —
    # the AI palette + Telegram /ip command both consume it but the
    # feature is independent and toggled from its own Admin → Public IP
    # section). The master enable toggle is the plain `public_ip_enabled`
    # SETTING (like `weather_enabled`), NOT a tunable — so the admin toggle
    # loads with the rest of the settings; only the cache TTL + fetch
    # timeout + sample interval below are tunables.
    # Public-IP in-process cache TTL. Single deploy hits the upstream
    # at most ~144 times/day at the 600s default; lower for fresher
    # geolocation after a WAN failover, raise on rate-limit pressure.
    # Range 60..3600.
    "tuning_public_ip_cache_ttl_seconds": ("PUBLIC_IP_CACHE_TTL_SECONDS", 600, 60, 3600),
    # Public-IP outbound HTTP wall-clock. ifconfig.co normally answers
    # well under 1s; raise on slow links / proxy paths. Range 2..60.
    "tuning_public_ip_fetch_timeout_seconds": ("PUBLIC_IP_FETCH_TIMEOUT_SECONDS", 8, 2, 60),
    # Public-IP background sampler cadence. A lifespan loop force-probes
    # ifconfig.co every N seconds (when the master toggle is on) so a
    # public-IP CHANGE is recorded in public_ip_history even when no one
    # is looking at the widget — without it, change-detection only fires
    # on incidental fetch() calls (SPA widget load / Telegram /ip / AI
    # palette) which are gated by the cache TTL, so a short-lived flap
    # shorter than the cache window is missed entirely. Default 120s
    # (2 min) detects a WAN-IP change close to a dedicated DDNS updater's
    # cadence while staying light on the upstream (~720 calls/day); a
    # detected change is pushed to the SPA live via the `public_ip:changed`
    # SSE event, so the widget updates the instant the sampler observes it
    # (no waiting out the SPA's 10-min refresh cache). Dial DOWN to 30–60s
    # for near-instant detection, or UP to reduce upstream calls. 0 disables
    # the sampler (change-detection falls back to incidental fetches only).
    # Range 0..86400.
    "tuning_public_ip_sample_interval_seconds": ("PUBLIC_IP_SAMPLE_INTERVAL_SECONDS", 120, 0, 86400),
    # Asset-inventory outbound HTTP wall-clocks. Two tiers — token
    # probe (OAuth2 client_credentials handshake, default 10s) and
    # asset fetch (paginated /assets pull, default 15s). Operators on
    # slow corporate networks or with the asset API behind a tunnel
    # can bump these; tight-watchdog deploys can lower. Range floors
    # prevent accidentally disabling.
    "tuning_asset_inventory_token_timeout_seconds": ("ASSET_INVENTORY_TOKEN_TIMEOUT_SECONDS", 10, 2, 120),
    "tuning_asset_inventory_fetch_timeout_seconds": ("ASSET_INVENTORY_FETCH_TIMEOUT_SECONDS", 15, 2, 300),
    # Per-app expanded-card extras (Speedtest / APC / ...) freshness TTL.
    # The SPA caches each `/app-data` response per (host, service_idx); past
    # this many seconds it's treated as STALE and a background refresh fires
    # while the stale value still renders (stale-while-revalidate), so an
    # expanded card updates on its own instead of going stale until a manual
    # Refresh. 0 disables auto-refresh (fetch-once-until-forced). Surfaced to
    # the SPA via /api/me client_config.apps_extras_ttl_seconds.
    "tuning_apps_extras_ttl_seconds": ("APPS_EXTRAS_TTL_SECONDS", 90, 0, 3600),
    # Window (days) for the APC UPS card's battery / output-load / runtime
    # trend sparkline. APC has NO dedicated sampler — it reads the already-
    # persisted host_snmp_samples table (whose own retention is
    # tuning_stats_history_days), so this is purely the sparkline's DISPLAY
    # window. Default 7. Raise for a longer trend (capped by the SNMP sample
    # retention); lower for a tighter recent view. Range 1..90.
    "tuning_apc_history_days": ("APC_HISTORY_DAYS", 7, 1, 90),
    # Per-app route wall-clock budget. The expanded-card data fetch
    # (GET /api/services/{host}/{idx}/app-data) and the per-app skill
    # dispatch (POST .../skill/{id}) each wrap their work in
    # asyncio.wait_for(this). When a slow upstream (e.g. Tdarr churning a
    # bloated scan, an *arr behind a stalled reverse proxy) blows past it,
    # OmniGrid raises its OWN logged HTTP 504 instead of letting the
    # request hang until the front reverse proxy emits an UNLOGGED gateway
    # 504 (which left no server-side trace of WHICH app/host timed out).
    # Keep this UNDER the reverse-proxy proxy_read_timeout (commonly 60s)
    # so OmniGrid's identifiable "<app> ... budget exceeded" fires first.
    # Default 50s. Raise toward the proxy timeout if a legitimately slow
    # app card keeps tripping it; lower for a faster fail + log. Range
    # 5..300.
    "tuning_apps_route_budget_seconds": ("APPS_ROUTE_BUDGET_SECONDS", 50, 5, 300),
    # Apps tile-render batch size. The Apps view stages each card's heavy
    # body in via a setTimeout(0)-paced queue to avoid building every body
    # in one Alpine flush (a page-hang). This is how many cards are readied
    # PER tick — higher = the visible grid finishes filling faster, lower =
    # more yielding to the browser between batches. Default 4. Raise on a
    # fast client with many apps; lower toward 1 if a heavy custom-widget
    # fleet stutters during the fill. Surfaced via /api/me
    # client_config.apps_tile_render_batch. Range 1..20.
    "tuning_apps_tile_render_batch": ("APPS_TILE_RENDER_BATCH", 4, 1, 20),
    # host_metrics_sampler permanent-fail window. After this many
    # seconds of consecutive probe failures the sampler auto-pauses the
    # host (no more probe attempts) until the operator resumes via
    # POST /api/hosts/{id}/resume-sampling. Same DB-key naming
    # convention as the other tunables so the Admin → Config form
    # auto-renders it.
    "tuning_host_permanent_fail_window_seconds": ("HOST_PERMANENT_FAIL_WINDOW_SECONDS", 900, 60, 86400),
    # Frontend /api/ops poll cadence in seconds. Stored as integer
    # seconds for operator-friendly UI (Admin → Process tunables shows
    # "min 1, max 60, default 2" instead of millisecond figures); the
    # consumer in `client_config` multiplies by 1000 to feed the SPA's
    # `setTimeout`. The SPA polls /api/ops to detect when background
    # ops complete (no event bus — ops run as FastAPI BackgroundTasks).
    # Lowering it makes UI feel snappier at the cost of more requests;
    # raising it cuts idle traffic. Read on /api/me so the frontend
    # picks the latest value without a page reload (takes effect on the
    # next pollOps iteration after a Save). Renamed from
    # tuning_ops_poll_interval_ms (and OPS_POLL_INTERVAL_MS) — operators
    # who had the old env var set need to re-enter the value in seconds.
    "tuning_ops_poll_interval_seconds": ("OPS_POLL_INTERVAL_SECONDS", 2, 1, 60),
    # Persistent-log retention window in days. Daily log files under
    # /app/data/logs/ older than this get deleted by the pruner loop
    # in main.py. Default 7d matches the stats-history retention
    # convention. Min 1d (a sweep that's run every hour wouldn't have
    # time to produce older files anyway); max 365d.
    "tuning_log_retention_days": ("LOG_RETENTION_DAYS", 7, 1, 365),
    # ``host_failure_events`` retention window in days. Drives the
    # Stats → Incidents view + Timeline tab + the inline
    # similar-incident grouping in the host drawer. Rows older than
    # this get deleted by the host_metrics_sampler prune loop on its
    # hourly tick. Default 90 days — a quarter's worth of incident
    # history for post-mortem learning without the table growing
    # unbounded over years. Set to 0 to disable pruning entirely
    # (legacy "keep every incident forever" behaviour); set to a
    # smaller value (e.g. 30) on tiny deployments where the SQLite
    # file itself is the bottleneck. Min 0 (forever), max 3650 (~10
    # years).
    "tuning_incidents_retention_days": ("INCIDENTS_RETENTION_DAYS", 90, 0, 3650),
    # Image-proxy disk cache (logic/image_cache.py) — how long a server-side-
    # fetched image (TMDB poster / per-app avatar) is served from disk before
    # re-fetching upstream. 0 disables the cache. Default 7 days (604800s) —
    # posters / avatars are effectively immutable, so a long TTL maximises the
    # cross-provider dedup win with no real staleness risk. Max 30 days.
    "tuning_image_proxy_cache_ttl_seconds": ("IMAGE_PROXY_CACHE_TTL_SECONDS", 604800, 0, 2592000),
    # Hard cap on the number of cached images — the oldest are pruned past it so
    # the cache directory can't grow unbounded. 0 = no cap. Default 5000 (a
    # large fleet's poster + avatar set fits comfortably; ~0.5-1 GB at typical
    # poster sizes).
    "tuning_image_proxy_cache_max_entries": ("IMAGE_PROXY_CACHE_MAX_ENTRIES", 5000, 0, 100000),
    # host-snapshots read-side cache TTL in seconds. The SPA
    # fans out N parallel /api/hosts/one/{id} calls per refresh, each
    # of which triggers a full SELECT against host_snapshots. Caching
    # the read for a few seconds collapses N reads into 1 without
    # serving stale data (the snapshot table is written once per
    # gather tick; the cache is also busted on every save). Default
    # 5s — N parallel callers in the same tick share one read, the
    # next refresh after TTL pays the ~1ms read once. Min 0 lets
    # operators disable the cache entirely (every call hits the DB);
    # max 300s caps a misconfigured override at 5 min.
    "tuning_host_snapshots_cache_ttl_seconds": ("HOST_SNAPSHOTS_CACHE_TTL_SECONDS", 5, 0, 300),
    # Per-field "stale grace" cap for the snapshot fallback
    # (`logic/gather.py:apply_host_snapshot_fallback`). When a provider
    # stops emitting a host_* field, the snapshot fallback restores
    # the last-known value flagged stale; without this cap, fields a
    # host CAN'T produce (e.g. host_cpu_percent on an APC UPS, or
    # host_ping_loss_pct on a non-pinged host) accumulate as phantom
    # stale rows forever — every gather restores them, every save
    # re-persists them, the cycle never breaks. This tunable bounds
    # how long a stale field can survive without a fresh value: when
    # `now - first_stale_ts > grace_window`, the field is dropped from
    # both the merged dict AND the next snapshot save, so the orphan
    # decays naturally. Default 24h covers transient outages
    # (provider down for hours, operator wants to see last value)
    # without keeping orphans forever. Range 1..720h (1h..30d).
    "tuning_host_snapshot_stale_field_max_age_hours": ("HOST_SNAPSHOT_STALE_FIELD_MAX_AGE_HOURS", 24, 1, 720),
    # concurrency cap on the SPA's per-host /api/hosts/one/<id>
    # fan-out in `loadHosts()`. Read on /api/me into
    # `me.client_config.hosts_parallel_fetch`; loadHosts resolves it per
    # call so a Save in Admin → Config takes effect on the next refresh
    # without a page reload. Default 6 matched the prior hardcoded
    # const; min 1 (serialised — guaranteed safe but slow); max 32 (NPM
    # default upstream pool exhausts well before this on most setups).
    "tuning_hosts_parallel_fetch": ("HOSTS_PARALLEL_FETCH", 6, 1, 32),
    # Idle-time progressive fill — when the operator stays at the top of
    # the Hosts view without scrolling, a background ticker enqueues
    # one not-yet-loaded host every N seconds into the SAME shared
    # `_hostRefreshQueue` the IntersectionObserver feeds, so by the
    # time they scroll, rows further down already have data. Goes
    # through the existing `tuning_hosts_parallel_fetch` worker cap, so
    # backend pressure stays bounded regardless of how aggressive this
    # is. Set 0 to disable entirely (fall back to scroll-only lazy
    # loading); set 1-2 for fast pre-warm on small fleets; default 3
    # for a slow trickle that's invisible on backend dashboards. Range
    # 0..30s. Surfaced via /api/me's `client_config.hosts_idle_fill_seconds`
    # so a Save in Admin → Config takes effect immediately without
    # restart.
    "tuning_hosts_idle_fill_interval_seconds": ("HOSTS_IDLE_FILL_INTERVAL_SECONDS", 3, 0, 30),
    # AI Assistant sidebar drawer width (pixels). Operator-tunable so
    # the same drawer adapts cleanly across a 1366 px laptop (480 px =
    # ~35% of horizontal space, often too wide) and a 4K monitor (480
    # px = ~13%, often too narrow). Surfaced via /api/me's
    # `client_config.ai_sidebar_width_px` and read by the SPA at
    # render time so a Save in Admin → Config takes effect on the
    # next page load. Mobile layout (≤ 720 px viewport) ignores this
    # value and goes full-width per the existing media query.
    # Range 320..720 — narrower than 320 hides the slash-picker chip
    # strip, wider than 720 covers most secondary content on a
    # standard laptop.
    "tuning_ai_sidebar_width_px": ("AI_SIDEBAR_WIDTH_PX", 480, 320, 720),
    # AI sidebar conversation-persist cadence (milliseconds). The
    # sidebar checks for changes every N ms and writes through to
    # `/api/me/ui-prefs` when the conversation signature changes.
    # Default 2000ms; operators on slow networks / low-power devices
    # can raise to 5000-10000ms. Range 500..30000ms. Takes effect on
    # the NEXT page load (interval doesn't re-arm mid-session).
    "tuning_ai_conversation_persist_interval_ms":
        ("AI_CONVERSATION_PERSIST_INTERVAL_MS", 2000, 500, 30000),
    # AI conversation export — toggles the export-to-txt /
    # export-to-json buttons in the AI sidebar header. Default ON
    # (1). Disable to hide the export affordance entirely (e.g. in
    # shared-deployment scenarios where conversation export should
    # require a specific operator role; the SPA-side hide is a UX
    # nudge, not a security boundary — bearer-token clients can
    # still read /api/me/ui-prefs.ai_conversation directly).
    # Range 0..1 (boolean shape).
    "tuning_ai_conversation_export_enabled": ("AI_CONVERSATION_EXPORT_ENABLED", 1, 0, 1),
    # Port-scan provider tunables. The four operator-tunable values
    # (per-port timeout, probe concurrency, outer scan-budget,
    # banner-read timeout) all flow through TUNABLES per the
    # No-static-config rule. The CSV `port_scan_default_ports`
    # stays as a plain `settings` row because TUNABLES is int-only;
    # documented separately in env_example.md.
    #
    # Per-port TCP-connect timeout (seconds). Operator-tunable so
    # noisy networks can raise it (5-10s on flaky links) and quiet
    # LANs can lower it for snappier scans (1s). Range 1..30.
    "tuning_port_scan_default_timeout_seconds": ("PORT_SCAN_DEFAULT_TIMEOUT_SECONDS", 2, 1, 30),
    # Probe concurrency cap (parallel TCP-connect probes per scan).
    # Higher = faster scan but louder log footprint on the target.
    # Range 1..256.
    "tuning_port_scan_default_concurrency": ("PORT_SCAN_DEFAULT_CONCURRENCY", 32, 1, 256),
    # Outer wall-clock budget for one scan (seconds). Caps the
    # `asyncio.wait_for` so a hung target can't pin the worker
    # forever. Default 120s covers a worst-case 1024-port scan with
    # timeout=2 + concurrency=32; raise for larger ranges (the new
    # 11000-port range cap means an aggressive operator could need
    # 10-15 minutes on a slow link). Range 30..1800.
    "tuning_port_scan_max_seconds": ("PORT_SCAN_MAX_SECONDS", 120, 30, 1800),
    # Banner-grab read timeout (seconds). When `banner_grab=true`,
    # the scanner reads up to 256 bytes per open port — services
    # that don't speak first (HTTP without a request) hit this
    # timeout and get skipped with no banner. Range 1..30.
    "tuning_port_scan_banner_read_seconds": ("PORT_SCAN_BANNER_READ_SECONDS", 2, 1, 30),
    # host_port_scans retention window (days). One row per OPEN port per scan is
    # written by the on-demand /port-scan endpoint AND the recurring
    # port_scan_refresh schedule kind; without a prune the table grows forever
    # (a daily refresh across a fleet adds hundreds of rows/day). The hourly
    # host_metrics_sampler prune sweep deletes rows older than this. 0 disables
    # pruning (keep every scan forever), matching the incidents-retention knob.
    # Default 90 days. Range 0..3650.
    "tuning_port_scan_retention_days": ("PORT_SCAN_RETENTION_DAYS", 90, 0, 3650),
    # Port-scan UDP companion (Stage 2). UDP is connectionless, so
    # the timeout regime + concurrency are different from TCP — UDP
    # probes wait the FULL window with no early "handshake completed"
    # signal, and probe traffic is more conspicuous on the wire.
    # Per-port timeout (seconds): 1..30, default 3 — longer than
    # TCP's 2s because UDP can't short-circuit on a quick refusal.
    "tuning_port_scan_udp_default_timeout_seconds": ("PORT_SCAN_UDP_DEFAULT_TIMEOUT_SECONDS", 3, 1, 30),
    # Probe concurrency: 1..64, default 8 — friendlier cap than TCP's
    # 32 because UDP probes are more visible to the target's IDS /
    # firewall logging.
    "tuning_port_scan_udp_default_concurrency": ("PORT_SCAN_UDP_DEFAULT_CONCURRENCY", 8, 1, 64),
    # Scheduled port-scan refresh — knobs consumed by the
    # `port_scan_refresh` schedule kind (`logic.schedules._run_port_scan_refresh`).
    # All three drive the SAME runner; the schedule row's `interval_seconds`
    # determines HOW OFTEN it fires, these knobs determine HOW MUCH
    # work each fire does.
    #   * max_hosts_per_tick — hard ceiling on hosts touched per fire.
    #     The runner picks oldest-scanned-first up to this count; the
    #     remainder defer to the next fire. 1..50, default 5.
    #     Rationale: keeps each tick bounded so a fleet of 100 hosts
    #     doesn't fan out 100 simultaneous scans the moment a *every-15-
    #     minutes* schedule fires for the first time.
    #   * min_age_seconds — minimum age of a host's last scan before
    #     it's eligible for re-scan. Prevents a frequent tick (e.g.
    #     every-2-min schedule on a small fleet) from re-scanning
    #     hosts that completed seconds ago. 60..86400, default 1800.
    #   * per_host_concurrency — how many hosts the runner scans IN
    #     PARALLEL within one tick. 1..4, default 1 (strictly sequential
    #     to keep load low). Raise on quiet networks where the operator
    #     wants the tick to complete faster; the per-host scan itself
    #     still uses `tuning_port_scan_default_concurrency` for its
    #     internal port-probe parallelism.
    "tuning_port_scan_schedule_max_hosts_per_tick": ("PORT_SCAN_SCHEDULE_MAX_HOSTS_PER_TICK", 5, 1, 50),
    "tuning_port_scan_schedule_min_age_seconds": ("PORT_SCAN_SCHEDULE_MIN_AGE_SECONDS", 1800, 60, 86400),
    "tuning_port_scan_schedule_per_host_concurrency": ("PORT_SCAN_SCHEDULE_PER_HOST_CONCURRENCY", 1, 1, 4),
    "tuning_schedule_stuck_run_threshold_seconds": ("SCHEDULE_STUCK_RUN_THRESHOLD_SECONDS", 3600, 600, 86400),
    # SSE heartbeat cadence (seconds). The /api/events stream
    # emits a `: keepalive\n\n` comment every N seconds so an idle NPM
    # / cloudflare proxy doesn't drop the connection on its own
    # idle-keepalive timer. Lower if your proxy has a tight idle
    # timeout (some defaults are 30s); raise to cut the comment-traffic
    # on long-lived tabs. Default 25s.
    "tuning_sse_heartbeat_seconds": ("SSE_HEARTBEAT_SECONDS", 25, 5, 300),
    # SSE connection wall-clock cap (seconds). Forces a
    # periodic close + reconnect so the cookie-authed tab re-enters the
    # auth middleware, letting the session-cookie's sliding-window
    # refresh land before the 8h hard cap. Default 6h leaves a 1h
    # margin for clock skew + heartbeat round-trip; do NOT raise past
    # the session hard cap minus that margin.
    "tuning_sse_max_lifetime_seconds": ("SSE_MAX_LIFETIME_SECONDS", 21600, 3600, 25200),
    # Webmin probe outer budget (seconds). Used by both
    # `_merge_one_host` (the per-host `asyncio.wait_for`) AND legacy
    # `api_hosts`'s `_WEBMIN_PROBE_BUDGET`. Pre-fix these duplicated
    # the same 20s constant in two places. Default 20s — enough for a
    # slow Miniserv to respond on its three-tier fallback (XML → JSON
    # → HTML scrape) but well under the 30s outer `/api/hosts/one/<id>`
    # budget so a hung Webmin doesn't blow the whole probe.
    "tuning_webmin_probe_budget_seconds": ("WEBMIN_PROBE_BUDGET_SECONDS", 20, 5, 120),
    # node-exporter per-host probe timeout (seconds). Used by
    # `_merge_one_host`'s NE block (was 10s), legacy `api_hosts`'s NE
    # probe (was 10s), AND `host_metrics_sampler` (was 15s — the
    # sampler's slightly higher value was a slow-startup compensation
    # that's no longer needed with the per-host failure-pause window).
    # Pick 10s as canonical default; operators with a deliberately
    # slow exporter raise it. Strict-rule category (e) "tuned during a
    # 504 incident".
    "tuning_node_exporter_probe_timeout_seconds": ("NODE_EXPORTER_PROBE_TIMEOUT_SECONDS", 10, 2, 60),
    # SSE freshness-watchdog idle threshold (seconds). Stored
    # as integer seconds for operator-friendly UI; SPA-side
    # `_sseIdleThresholdMs` consumer multiplies × 1000. Default 30s —
    # matches the heartbeat cadence so a stalled stream that's missing
    # both heartbeats AND organic events will trigger the polling
    # fallback within ~2 heartbeat windows. Strict-rule category (b)
    # "freshness threshold".
    "tuning_sse_idle_threshold_seconds": ("SSE_IDLE_THRESHOLD_SECONDS", 30, 5, 300),
    # pollOps SSE-up keep-alive cadence (seconds). When SSE is
    # connected, `pollOps` slows from `tuning_ops_poll_interval_seconds`
    # to this value as a defence-in-depth safety net (catches a
    # silently-stalled stream that the freshness watchdog hasn't yet
    # flipped). Stored as integer seconds; SPA × 1000 in setTimeout.
    # Default 30s — lines up with the freshness threshold so the
    # keepalive fires at-or-before the watchdog flips _sseConnected
    # to false.
    "tuning_pollops_sse_keepalive_seconds": ("POLLOPS_SSE_KEEPALIVE_SECONDS", 30, 5, 600),
    # SPA-side load-busy watchdog. `_runWithBusy` and the topbar
    # `refresh()` / `loadHosts()` flow + the SSE-pill refreshing
    # flags (`cacheRefreshing` / `hubProbing` / `statsRefreshing`)
    # cap any individual "busy" indicator at this many seconds. A
    # hung fetch (server unreachable, network blip) can't leave a
    # spinner or disabled-reload button stuck across the session.
    # Lower bound 5s — anything shorter would clear the gate before
    # a normal fetch completes and operators would see flickers.
    # Upper bound 600s — anything longer is effectively "no cap"
    # and defeats the watchdog's purpose. Default 30s matches the
    # documented per-host probe budget for `/api/hosts/one/{id}`.
    "tuning_load_busy_max_seconds": ("LOAD_BUSY_MAX_SECONDS", 30, 5, 600),
    # login rate-limit policy. Three knobs grouped (max
    # failures, sliding window, lockout duration). Default mirrors
    # the prior hardcoded policy: 5 failures in 15 min → 15 min
    # lockout. High-security operators want longer lockouts; dev
    # operators want looser limits.
    "tuning_rate_limit_max_failures": ("RATE_LIMIT_MAX_FAILURES", 5, 1, 100),
    "tuning_rate_limit_window_seconds": ("RATE_LIMIT_WINDOW_SECONDS", 900, 60, 86400),
    "tuning_rate_limit_lockout_seconds": ("RATE_LIMIT_LOCKOUT_SECONDS", 900, 60, 86400),
    # outer host-provider cache TTL (seconds). The Beszel +
    # Pulse hub batch maps + Webmin creds + active-sources tuple are
    # cached together for this window; settings saves explicitly
    # invalidate, so this only matters for the rate at which "no save
    # happened, but something changed upstream" can re-flow through.
    # Default 10s. Strict-rule category (d) "trades freshness for cost".
    "tuning_host_provider_cache_ttl_seconds": ("HOST_PROVIDER_CACHE_TTL_SECONDS", 10, 1, 300),
    # Diagnostic interval for the host-provider cache hit/miss log.
    # Every Nth call, the periodic helper logs a `[provider_state]
    # cache window: H hits / M misses (X% hit rate)` line so operators
    # can verify the cache is doing its job from Admin → Logs. Higher
    # values reduce log noise on busy multi-tab sessions; lower values
    # surface a finer-grain hit ratio for debugging. Strict-rule
    # category — diagnostic verbosity knob.
    "tuning_host_provider_cache_diag_interval": ("HOST_PROVIDER_CACHE_DIAG_INTERVAL", 100, 1, 10000),
    # gather_stats per-node fail-cache TTL. Skips known-unreachable
    # Swarm workers for this window after the first ConnectTimeout
    # / OSError so /api/stats stops paying the full client timeout
    # per dead worker on every other poll. Latches off on success.
    # Default 60s — short enough for the operator to see a recovered
    # worker within a minute, long enough to fully suppress the
    # repeat-log noise across multiple SPA poll ticks.
    "tuning_stats_per_node_unreachable_ttl_seconds": (
        "STATS_PER_NODE_UNREACHABLE_TTL_SECONDS", 60, 5, 600,
    ),
    # DNS-failure skip cache TTL — when `logic/dns_skip.should_skip_dns`
    # logs a host as unresolvable, subsequent sampler probes skip the
    # host entirely for this window instead of paying the per-host
    # probe timeout × N samplers per tick. Latches off on the next
    # successful resolution. Default 300 (5 min) — short enough that a
    # recovered DNS server brings hosts back within a chart's refresh
    # window, long enough to fully suppress the executor-thrash. Range
    # 60-3600.
    "tuning_dns_failed_skip_seconds": (
        "DNS_FAILED_SKIP_SECONDS", 300, 60, 3600,
    ),
    # Short probe timeout for Beszel when the previous probe latched
    # as unreachable. Eliminates the cold-cache 15s wait that every
    # /api/hosts/list / /api/hosts/one caller pays during a hub
    # outage. Latches back to the long timeout
    # (`tuning_beszel_probe_timeout_seconds`) on next successful
    # probe so a recovered hub immediately gets its full budget back.
    # Default 3s; range 1-30.
    "tuning_beszel_probe_timeout_unreachable_seconds": (
        "BESZEL_PROBE_TIMEOUT_UNREACHABLE_SECONDS", 3, 1, 30,
    ),
    # Same shape for Pulse — short timeout when the previous probe
    # latched as unreachable. Default 3s; range 1-30.
    "tuning_pulse_probe_timeout_unreachable_seconds": (
        "PULSE_PROBE_TIMEOUT_UNREACHABLE_SECONDS", 3, 1, 30,
    ),
    # Slow-query log threshold (ms). Every db_conn() wraps its
    # connection's execute / executemany with a perf_counter pair;
    # queries exceeding this threshold land in Admin → Logs with
    # the `[slow_query] warning:` prefix family (WARN bucket).
    # Default 100ms — matches the sync-loop-block boundary the project conventions
    # uses for the to_thread offload rule. Lower (e.g. 25) for
    # verbose debugging when hunting a regression; raise (e.g. 500)
    # to focus only on catastrophic queries on a noisy fleet. 0
    # disables the wrapper entirely (no perf_counter overhead).
    # Range 0-10000; per-use resolution so an Admin → Config edit
    # applies to the NEXT connection opened.
    "tuning_slow_query_threshold_ms": (
        "SLOW_QUERY_THRESHOLD_MS", 100, 0, 10000,
    ),
    # In-process cache TTL (seconds) for `_host_provider_config()`
    # in logic/host_metrics_sampler.py — the per-(host_id, providers)
    # map that `record_provider_outcome`'s defensive guard consults to
    # decide whether to record a failure or refuse + clean orphans. The
    # canonical save endpoint invalidates immediately on a hosts_config
    # write; this TTL is the belt-and-braces backstop for any future
    # code path that writes hosts_config without going through that
    # endpoint. Lower for snappier recovery after a write that bypassed
    # the invalidator; raise on a stable fleet to lighten DB reads
    # during a probe-burst tick. Default 60s. Strict-rule category (d)
    # "trades freshness for cost".
    "tuning_host_provider_config_cache_ttl_seconds": ("HOST_PROVIDER_CONFIG_CACHE_TTL_SECONDS", 60, 1, 3600),
    # per-host Webmin success-cache TTL (seconds). Successful
    # Webmin probes are cached for this window so burst refreshes
    # (e.g. SPA fan-out) skip the repeat probe. Default 30s. Lower
    # for live-feeling drawer reopens; raise to cut Miniserv load.
    "tuning_webmin_host_cache_ttl_seconds": ("WEBMIN_HOST_CACHE_TTL_SECONDS", 30, 1, 3600),
    # per-host Webmin failure-cache TTL (seconds). Failed
    # Webmin probes are cached for this short window so a hung host
    # doesn't burn 20s × N parallel calls. Default 5s — tight enough
    # for fast recovery detection (one Hosts-tab refresh cycle), long
    # enough to dedupe a fan-out burst. Operators with constantly-
    # flapping Webmin instances may want 30s+ to suppress the spam.
    "tuning_webmin_host_fail_cache_ttl_seconds": ("WEBMIN_HOST_FAIL_CACHE_TTL_SECONDS", 5, 1, 3600),
    # host_metrics_sampler per-tick NE probe concurrency.
    # Sampler fan-out cap on parallel node-exporter scrapes inside
    # one sampling tick. Default 8 — fits a 60-host fleet through 8
    # workers in ~3 batches without saturating the manager. Lower on
    # a Pi-class manager; raise on a beefy host or a fleet of many
    # hosts where serialised batches push past the 5-min interval.
    "tuning_host_metrics_probe_concurrency": ("HOST_METRICS_PROBE_CONCURRENCY", 8, 1, 64),
    # shared per-(host, user) cool-down (seconds) on auth
    # failures (Webmin + SSH). Same value across both modules so a
    # single Save covers both. Default 300 (5 min) — long enough to
    # avoid lockout cascades on bad creds, short enough that operators
    # don't have to wait an hour after fixing a typo.
    "tuning_auth_failure_cooldown_seconds": ("AUTH_FAILURE_COOLDOWN_SECONDS", 300, 5, 3600),
    # Ping host-stats provider knobs. All four resolved via the
    # same DB > env > default tier so operators can tune the sampler's
    # cadence + per-probe timeout + per-(host, port) cool-down without
    # editing TUNABLES. Cooldown reuses the Cooldown timer pattern but
    # has its OWN tunable rather than sharing with the auth cooldown
    # — Ping has no notion of "credential lockout"; the cool-down here
    # purely throttles probes against an unreachable host.
    # Ping sampler tick cadence (seconds). Same inherit semantics as
    # the other per-provider sample-interval knobs (Beszel / Pulse /
    # NE / SNMP) — 0 = use the global `tuning_stats_sample_interval_seconds`;
    # > 0 overrides per-Ping. Distinct knob exists because operators
    # often want Ping to tick FASTER than the data-bearing samplers
    # (sub-minute reachability checks against routers / 5G modems)
    # without bumping the global cadence. Range 0..3600.
    "tuning_ping_interval_seconds": ("PING_INTERVAL_SECONDS", 0, 0, 3600),
    "tuning_ping_concurrency": ("PING_CONCURRENCY", 16, 1, 128),
    "tuning_ping_probe_timeout_seconds": ("PING_PROBE_TIMEOUT_SECONDS", 2, 1, 30),
    "tuning_ping_cooldown_seconds": ("PING_COOLDOWN_SECONDS", 300, 30, 3600),
    # SNMP host-stats provider knobs. Two operator-tunable
    # values: the per-probe wall-clock timeout (UDP retransmits live
    # under this budget) and the fan-out concurrency cap that bounds
    # how many parallel SNMP probes the gather + per-host-merge paths
    # run in one tick. Cool-down on consecutive timeouts shares the
    # auth-failure cool-down knob (no separate "credential lockout"
    # surface for SNMP — the cool-down purely throttles probes against
    # an unreachable host, same purpose as the auth one).
    "tuning_snmp_probe_timeout_seconds": ("SNMP_PROBE_TIMEOUT_SECONDS", 5, 1, 60),
    # Cross-host SNMP fan-out cap — how many DIFFERENT hosts probe in
    # parallel per sampler tick. Lowered from 16 → 4 default because
    # each probe holds the event loop for up to
    # `tuning_snmp_wall_clock_budget_seconds` (60s default) when a
    # host is unreachable; 16 simultaneous unreachable hosts starves
    # the event loop for a full minute, breaking the Docker swarm
    # healthcheck + the SPA's /api/* requests. 4 is conservative
    # enough that a partial-outage fleet still gets responsive
    # healthchecks; operators on small (<10) reliable fleets can
    # safely raise it back to 8-16.
    "tuning_snmp_concurrency": ("SNMP_CONCURRENCY", 4, 1, 128),
    # Wall-clock budget for ONE probe against ONE host. The probe fans
    # out ~60 SNMP GET / WALK operations (sys / HR / IF / ENTITY +
    # vendor-private MIBs for Dell / Cisco / APC / UCD / Synology /
    # HP printer / Dell-RAC). pysnmp serialises the wire-level UDP
    # packets through one engine + transport target, so the total
    # wall-clock is roughly (number_of_round_trips × RTT). On a slow
    # embedded snmpd (WD MyCloud, low-power NAS, network printers)
    # an RTT of 500ms × 60 round-trips ≈ 30s — and that's BEFORE any
    # retries kick in. Pre-this-knob the budget was hardcoded as
    # `max(5.0, (timeout + 2.0) * 2)` which with the default timeout
    # of 5s came out to 14s — far too short for slow devices,
    # operators reported `snmp: timeout against <host>:161` on every
    # cycle for these hosts, hitting the 5-failures auto-pause
    # threshold and only succeeding after a manual resume (which gave
    # the probe a quiet cache-empty moment). Default 60s is plenty
    # for even the slowest device while still bounding the gather's
    # parallel-host fan-out. Range 5..600s.
    "tuning_snmp_wall_clock_budget_seconds": ("SNMP_WALL_CLOCK_BUDGET_SECONDS", 60, 5, 600),
    # Per-host walk concurrency — caps how many `_snmp_get` /
    # `_snmp_walk` operations fan out against ONE host inside
    # `probe_snmp`. Default 1 (fully serialised, CLI-equivalent
    # behaviour) chosen for safety: slow BMC-class agents (iDRAC9,
    # IPMI, low-power embedded snmpd) have limited UDP receive
    # queues and lose packets when 60+ concurrent bulk requests
    # arrive simultaneously — observed pattern is `[snmp] WALK
    # errorIndication: No SNMP response received before timeout`
    # for ~15 of ~60 OID branches per probe. Operators with fast
    # snmpd's (cisco / synology / linux net-snmp) can raise this
    # to 8-16 to get back the parallelism win; the gather wall-
    # clock drops from ~6s @ concurrency=1 to ~0.5s @ concurrency=16
    # on a 60-OID probe. Range 1..16.
    "tuning_snmp_per_host_walk_concurrency": ("SNMP_PER_HOST_WALK_CONCURRENCY", 1, 1, 16),
    # Per-vendor walk_concurrency global defaults. When `active_vendors`
    # auto-detect resolves to EXACTLY ONE vendor AND no per-host
    # override is set AND the vendor's tunable is non-zero, the
    # resolver picks the vendor-specific default instead of the generic
    # `tuning_snmp_per_host_walk_concurrency`. Lets a fleet's global
    # default match the vendor mix without forcing a compromise: a Dell
    # iDRAC fleet runs at 4 by default while a printer fleet stays at 1,
    # without the operator setting per-host overrides on every row.
    # These ship with sensible NON-ZERO defaults so simply marking a
    # host's vendor (Admin → Hosts) is enough to fit pysnmp v7's per-walk
    # overhead inside the probe budget — Dell iDRAC / Cisco BMC at 4,
    # Synology / linux net-snmp at 8 (they handle parallel SNMP well).
    # Printer stays 0 (= disabled → falls through to the generic per-host
    # tunable's safety floor of 1) because many embedded printer agents
    # choke on parallel walks. Set 0 to disable a vendor default; APC is
    # excluded — single-GET probe, concurrency has no effect.
    "tuning_snmp_walk_concurrency_dell": ("SNMP_WALK_CONCURRENCY_DELL", 4, 0, 16),
    "tuning_snmp_walk_concurrency_cisco": ("SNMP_WALK_CONCURRENCY_CISCO", 4, 0, 16),
    "tuning_snmp_walk_concurrency_synology": ("SNMP_WALK_CONCURRENCY_SYNOLOGY", 8, 0, 16),
    "tuning_snmp_walk_concurrency_ucd": ("SNMP_WALK_CONCURRENCY_UCD", 8, 0, 16),
    "tuning_snmp_walk_concurrency_printer": ("SNMP_WALK_CONCURRENCY_PRINTER", 0, 0, 16),
    # SNMP per-host caches, distinct from the Webmin TTL knobs.
    # Pre-fix the SNMP per-host caches reused tuning_webmin_host_cache_ttl_seconds /
    # tuning_webmin_host_fail_cache_ttl_seconds — operator changing the
    # Webmin TTL silently changed SNMP cache behaviour. Each provider's
    # per-host probe cache (success and fail) gets its OWN dial.
    "tuning_snmp_host_cache_ttl_seconds": ("SNMP_HOST_CACHE_TTL_SECONDS", 30, 5, 300),
    "tuning_snmp_host_fail_cache_ttl_seconds": ("SNMP_HOST_FAIL_CACHE_TTL_SECONDS", 5, 1, 60),
    # dedicated SNMP unreachable-cool-down dial. Pre-fix
    # SNMP shared `tuning_auth_failure_cooldown_seconds` with Webmin
    # / SSH (which makes sense for credential lockout but is the wrong
    # semantic for SNMP — there's no auth challenge to lock out
    # against). Operators debugging "SNMP timing out" reach for the
    # AUTH knob and get confused. Default = 300s (parity with the
    # legacy auth-cooldown default), so behaviour stays unchanged on
    # first deploy; existing deployments that bumped the auth knob
    # for SNMP keep their behaviour until they explicitly tune this
    # one. Range 30..3600.
    "tuning_snmp_unreachable_cooldown_seconds": ("SNMP_UNREACHABLE_COOLDOWN_SECONDS", 300, 30, 3600),
    # SNMP-specific sample interval (seconds). 0 = use the global
    # `tuning_stats_sample_interval_seconds` (legacy behaviour); any
    # non-zero value within the range overrides the global for SNMP
    # probes only. Operator-flagged that SNMP devices often need a
    # different cadence than Beszel/NE hosts — printers can poll
    # hourly, switches every minute. Range 0 (use global) OR 30..3600.
    "tuning_snmp_sample_interval_seconds": ("SNMP_SAMPLE_INTERVAL_SECONDS", 0, 0, 3600),
    # SNMP per-host auto-pause threshold (consecutive failed probe
    # rounds). When `_probe_one_snmp` fails this many ticks in a row,
    # the (snmp, host) pair is marked auto-paused in
    # `host_failure_state` (keyed `snmp:<host_id>` — see the project conventions
    # SNMP-aware sampler). Subsequent probes are SKIPPED entirely
    # until the operator clears the marker via
    # POST /api/hosts/{id}/provider/snmp/resume. Distinct from the
    # in-memory cool-down (`tuning_snmp_unreachable_cooldown_seconds`)
    # which is a short throttle on transient blips — auto-pause is the
    # operator-visible "this host is broken, fix it manually" state.
    # 0 = disabled (never auto-pause; cool-down still applies); 5 =
    # default (~25 min of failures at the default 5-min cadence before
    # the chip goes red). Range 0..50.
    "tuning_snmp_failure_pause_rounds": ("SNMP_FAILURE_PAUSE_ROUNDS", 5, 0, 50),
    # Webmin per-host auto-pause threshold (consecutive failed probe
    # rounds). Same semantic as `tuning_snmp_failure_pause_rounds`.
    # When per-request Webmin probes in `_merge_one_host` fail this
    # many times in a row, the (webmin, host) pair is marked auto-paused
    # in `host_failure_state` (keyed `webmin:<host_id>`); subsequent
    # probes are SKIPPED entirely until operator clears the marker via
    # POST /api/hosts/{id}/provider/webmin/resume. Distinct from the
    # 5-min auth-failure cool-down which throttles credential lockout —
    # auto-pause is the operator-visible "this Miniserv is broken, fix
    # it manually" state. 0 = disabled. Range 0..50.
    # WeatherAPI.com provider knobs — supersedes Open-Meteo. Cache TTL
    # is per-coord; fetch timeout is the outer wall-clock; sampler
    # interval drives the lifespan-managed `weather_sampler` which
    # writes per-tick rows into `weather_samples` for AI history;
    # retention bounds how far back the samples table grows.
    "tuning_weather_cache_ttl_seconds": ("WEATHER_CACHE_TTL_SECONDS", 600, 60, 86400),
    "tuning_weather_fetch_timeout_seconds": ("WEATHER_FETCH_TIMEOUT_SECONDS", 8, 1, 60),
    "tuning_weather_history_retention_days": ("WEATHER_HISTORY_RETENTION_DAYS", 90, 0, 3650),
    "tuning_weather_sampler_interval_seconds": ("WEATHER_SAMPLER_INTERVAL_SECONDS", 3600, 0, 86400),
    "tuning_webmin_failure_pause_rounds": ("WEBMIN_FAILURE_PAUSE_ROUNDS", 5, 0, 50),
    # Beszel per-host auto-pause threshold. Beszel is hub-based — the
    # hub fetch runs once per gather and produces a per-host map. A
    # "round" here is "hub fetch SUCCEEDED but this specific host was
    # missing from the map OR reported status=down/paused". Hub-level
    # outages (entire `state["beszel_map"]` empty) do NOT count toward
    # the threshold, so a brief hub blip can't cascade-pause every host.
    # 0 = disabled. Range 0..50.
    "tuning_beszel_failure_pause_rounds": ("BESZEL_FAILURE_PAUSE_ROUNDS", 5, 0, 50),
    # Beszel hub probe timeout (seconds). Caps `probe_hub` (full hub
    # crawl: systems + system_stats + systemd_services collections).
    # Lower for fast-fail on a stuck hub; raise for slow remote hubs.
    # Range 1..120. Default 15.
    "tuning_beszel_probe_timeout_seconds": ("BESZEL_PROBE_TIMEOUT_SECONDS", 15, 1, 120),
    # Beszel sampler tick cadence (seconds). 0 = use the global
    # `tuning_stats_sample_interval_seconds` (legacy / inherited
    # cadence — same fallback Pulse / Webmin samplers use).
    # Distinct knob exists so a fleet with a noisy / large Beszel hub
    # can throttle Beszel sampling independently of NE / Pulse /
    # Webmin. Range 0..3600.
    "tuning_beszel_sample_interval_seconds": ("BESZEL_SAMPLE_INTERVAL_SECONDS", 0, 0, 3600),
    # Pulse sampler tick cadence (seconds). Same inherit-semantics as
    # the Beszel knob above — 0 = use the global
    # `tuning_stats_sample_interval_seconds`; > 0 overrides per-Pulse.
    # Distinct knob so a fleet with a noisy / large Pulse hub
    # (Proxmox cluster with hundreds of guests) can throttle Pulse
    # sampling independently of NE / Beszel / Webmin. Range 0..3600.
    "tuning_pulse_sample_interval_seconds": ("PULSE_SAMPLE_INTERVAL_SECONDS", 0, 0, 3600),
    # node-exporter sampler tick cadence (seconds). Same inherit-
    # semantics — 0 = use `tuning_stats_sample_interval_seconds`;
    # > 0 overrides per-NE. Distinct knob so operators can sample
    # node-exporter at a different cadence than the hub-based
    # providers (e.g. NE every 60s for fine-grained CPU / mem /
    # disk traces, while keeping Beszel / Pulse on the slower
    # 300s default to avoid hub load). Range 0..3600.
    "tuning_node_exporter_sample_interval_seconds": ("NODE_EXPORTER_SAMPLE_INTERVAL_SECONDS", 0, 0, 3600),
    # NOTE: per-host Beszel cache TTLs were briefly declared here for parity
    # with the Webmin pair but never wired to a consumer — Beszel is a BATCH
    # probe (one hub fetch → map of all hosts) so per-host TTLs are
    # architecturally wrong; the 10s batch cache in `_get_host_provider_state`
    # already collapses N parallel callers to one hub fetch. Deleted to avoid
    # the "knob does nothing" drift class.
    # Pulse per-host auto-pause threshold. Same hub-based contract as
    # Beszel — only counts when hub fetch succeeded but the host wasn't
    # found OR Pulse reported the host status as down. 0 = disabled.
    "tuning_pulse_failure_pause_rounds": ("PULSE_FAILURE_PAUSE_ROUNDS", 5, 0, 50),
    # Pulse hub probe timeout (seconds). Single hub fetch covers every
    # host — `host_pulse_sampler` and `_get_host_provider_state` both
    # consume this. Default 15s — Pulse's `/api/state` is typically a
    # sub-second response on a healthy hub but the operator may run
    # behind a slow proxy or against an under-provisioned PVE cluster
    # where the per-node enumeration takes longer.
    "tuning_pulse_probe_timeout_seconds": ("PULSE_PROBE_TIMEOUT_SECONDS", 15, 1, 120),
    # Webmin sampler per-host probe wall-clock budget. Same shape as
    # the Pulse / NE probe timeouts. Pre-fix the sampler read this key
    # via `tuning_int(...)` but the key was never declared in TUNABLES,
    # so the resolver raised `unknown tunable` on every tick (default
    # 300s cadence) and the outer except-Exception silently caught the
    # raise — the Webmin sampler effectively never ran. Default 8s
    # matches the previous `or 8.0` fallback in the sampler code.
    "tuning_webmin_probe_timeout_seconds": ("WEBMIN_PROBE_TIMEOUT_SECONDS", 8, 1, 120),
    # Outer wall-clock budget for one full Webmin sampler tick. The
    # sampler fans out N parallel `_probe_one_host` calls (N = curated
    # hosts with `webmin_name` set); without an outer cap a single
    # wedged Webmin host can starve the whole tick. Default 0 means
    # "auto-derive from probe_timeout × hosts capped at 5 min" which
    # is the safe default for fleets <60 hosts; operators with very
    # large fleets can pin a higher value if their per-host probe is
    # genuinely fast and they want a bigger fan-out window. Range
    # 0..600s (0 = auto). Resolved per-tick.
    "tuning_webmin_sampler_budget_seconds": ("WEBMIN_SAMPLER_BUDGET_SECONDS", 0, 0, 600),
    # node-exporter per-host auto-pause threshold. Per-host scrape, so
    # the failure semantic is the same as Webmin: probe attempt that
    # raised OR returned exporter_error. 0 = disabled. Range 0..50.
    "tuning_node_exporter_failure_pause_rounds": ("NODE_EXPORTER_FAILURE_PAUSE_ROUNDS", 5, 0, 50),
    # HTTP probe — seventh host-stats provider. Active per-URL TCP /
    # TLS / DNS health check. Targets the operator's existing
    # ``hosts_config[].url`` + ``services[].url`` (or an explicit
    # per-host ``http_probe.urls`` override). All knobs operator-tunable
    # so a fleet with slow upstreams / large URL fan-out can dial
    # without redeploy.
    # Per-URL HTTP wall-clock — caps the GET request AND (separately)
    # the TLS handshake. Range 1..60. Default 8s — comfortably above
    # typical reverse-proxy latency, tight enough to keep a wedged
    # backend from starving the sampler tick.
    "tuning_http_probe_timeout_seconds": ("HTTP_PROBE_TIMEOUT_SECONDS", 8, 1, 60),
    # Per-URL probe concurrency cap inside one sampler tick. Each URL
    # consumes a socket; large fleets with many URLs per host want
    # this higher (parallelise faster) but trading off against the
    # outbound socket budget. Range 1..32. Default 8.
    "tuning_http_probe_concurrency": ("HTTP_PROBE_CONCURRENCY", 8, 1, 32),
    # Sampler tick cadence. 0 = inherit ``tuning_stats_sample_interval_seconds``
    # (same inherit semantics as the other per-provider knobs); >0
    # overrides per-HTTP-probe. Operators monitoring TLS cert expiry
    # don't need sub-minute cadence; the default falls through to
    # the global 5-min cadence. Range 0..3600.
    "tuning_http_probe_sample_interval_seconds": ("HTTP_PROBE_SAMPLE_INTERVAL_SECONDS", 0, 0, 3600),
    # Per-(http_probe, host) auto-pause threshold. Same semantic as
    # the SNMP / Webmin / NE auto-pause knobs: N consecutive failed
    # sampler rounds → mark the (http_probe, host) row as paused;
    # subsequent probes are SKIPPED entirely until the operator
    # clears via POST /api/hosts/{id}/provider/http_probe/resume. A
    # "round" = "every URL for this host failed". Mixed success
    # across URLs keeps the host out of the failed bucket. 0 =
    # disabled. Default 5. Range 0..100.
    "tuning_http_probe_failure_pause_rounds": ("HTTP_PROBE_FAILURE_PAUSE_ROUNDS", 5, 0, 100),
    # DNS sub-probe wall-clock (seconds). Caps ``socket.getaddrinfo``
    # in a thread executor so a slow resolver can't stall the
    # sampler. Range 1..30. Default 5.
    "tuning_http_probe_dns_timeout_seconds": ("HTTP_PROBE_DNS_TIMEOUT_SECONDS", 5, 1, 30),
    # TLS cert expiry warning threshold (days). The drawer card
    # paints the expiry pill amber when ``tls_expires_in_days``
    # falls below this number; red when ≤ 0 (expired). Range 1..365.
    # Default 14 — gives operators ~2 weeks lead time for renewal.
    "tuning_http_probe_cert_warning_days": ("HTTP_PROBE_CERT_WARNING_DAYS", 14, 1, 365),
    # Per-host per-result caches. Mirrors the Webmin / SNMP pair
    # so a burst of SPA fan-out (`/api/hosts/one/{id}` × N) doesn't
    # re-probe every URL on every call. Success TTL covers "fresh
    # data"; fail TTL is much shorter so recovery surfaces quickly.
    # Range 0..600 (0 = disable the cache entirely).
    "tuning_http_probe_host_cache_ttl_seconds": ("HTTP_PROBE_HOST_CACHE_TTL_SECONDS", 30, 0, 600),
    "tuning_http_probe_host_fail_cache_ttl_seconds": ("HTTP_PROBE_HOST_FAIL_CACHE_TTL_SECONDS", 5, 0, 600),
    # Default-accepted-status-codes range. Operators on diagnostic-
    # focused deploys may want to accept 4xx as "alive" (the host is
    # responding to TCP + serving HTTP, even if the request is
    # unauthorised / not-found at that path); operators on health-
    # check-focused deploys may want to tighten back to 2xx-only.
    # Default 200..399 covers redirect-fronted endpoints (Nextcloud /
    # GitLab / Forgejo / WWW redirects — the homelab norm). LO bound
    # 100..599 (the full HTTP status code space). When a per-host
    # `accepted_status_codes` CSV is set, this range is ignored and
    # the CSV wins exactly.
    "tuning_http_probe_default_accepted_lo_code": ("HTTP_PROBE_DEFAULT_ACCEPTED_LO_CODE", 200, 100, 599),
    "tuning_http_probe_default_accepted_hi_code": ("HTTP_PROBE_DEFAULT_ACCEPTED_HI_CODE", 399, 100, 599),
    # ----- Per-service reachability probes (per-chip on the curated
    # services[] list — distinct from the host-level HTTP probe).
    # Master toggle lives at the plain `service_probe_enabled` setting;
    # these knobs ONLY affect sampler runtime once that toggle is on
    # AND at least one host has `services[].probe.enabled === true`.
    "tuning_service_probe_sample_interval_seconds": ("SERVICE_PROBE_SAMPLE_INTERVAL_SECONDS", 300, 0, 3600),
    "tuning_service_probe_concurrency": ("SERVICE_PROBE_CONCURRENCY", 16, 1, 64),
    "tuning_service_probe_timeout_seconds": ("SERVICE_PROBE_TIMEOUT_SECONDS", 5, 1, 30),
    "tuning_service_probe_failure_pause_rounds": ("SERVICE_PROBE_FAILURE_PAUSE_ROUNDS", 5, 0, 50),
    # Ping per-host auto-pause threshold. CAREFUL: ping is the
    # alive/down detection signal — `alive=False` is the actual DATA
    # the operator wants surfaced, not a fault condition. So this
    # threshold counts ONLY sampler-level errors (network unreachable
    # from runner, ICMP permission denied, transport setup failure),
    # NOT host-down samples. Default disabled (0) so existing deploys
    # don't see ping chips spuriously paused on a normally-down host.
    # Operators concerned about runtime sampler errors can opt in by
    # raising the value. Range 0..50.
    "tuning_ping_failure_pause_rounds": ("PING_FAILURE_PAUSE_ROUNDS", 0, 0, 50),
    # stat-bar threshold cutovers. Pre-fix the SPA's `barLevel`
    # / `barColor` helpers hardcoded 60 (warn) and 85 (crit). Operators
    # running CPU-saturated workloads where 80% steady is normal want
    # to push warn higher; operators running provisioned workloads
    # where every spike is a signal want lower. Range bounds are
    # asymmetric on purpose — warn must be ≤ crit so the colour
    # progression stays monotonic, and crit < 100 so a crit-equal
    # reading is still a crit.
    "tuning_stat_bar_warn_pct": ("STAT_BAR_WARN_PCT", 60, 30, 90),
    "tuning_stat_bar_crit_pct": ("STAT_BAR_CRIT_PCT", 85, 50, 99),
    # Stack-update convergence-poll window. Portainer's
    # `PUT /api/stacks/{id}` accepts the request in ~5s and returns
    # success, but the actual `Prune + PullImage` runs ASYNCHRONOUSLY on
    # the docker daemon (often 30-60s+ for real changes). Pre-fix
    # `do_update_stack` called `op.done('success')` as soon as the PUT
    # returned — the SPA's button reverted to "Update" while the daemon
    # was still pulling. Post-fix: after the PUT returns, the op stays
    # in the `running` state and polls Portainer's service list every
    # `_observe_poll_seconds` (default 15s). For every service whose
    # `com.docker.stack.namespace` label matches this stack, check
    # `UpdateStatus.State`: while ANY shows `"updating"`, keep waiting.
    # Two consecutive clean polls debounce against the brief gap between
    # services rolling out one at a time. Cap at
    # `_observe_timeout_seconds` (default 5 min) so a stuck rollback
    # doesn't pin the op forever. After convergence (or timeout), THEN
    # `op.done('success')`. Result: op lifetime tracks actual update
    # duration, the SPA's existing busy-state machinery stays correct
    # without further frontend changes.
    "tuning_stack_update_observe_timeout_seconds":
        ("STACK_UPDATE_OBSERVE_TIMEOUT_SECONDS", 300, 30, 1800),
    "tuning_stack_update_observe_poll_seconds":
        ("STACK_UPDATE_OBSERVE_POLL_SECONDS", 15, 5, 120),
    # In-app notifications retention window (days). The
    # `prune_notifications` schedule kind reads this to delete rows from
    # the `notifications` table older than `now - days * 86400`. Default
    # 90d — operators want a longer trail than persistent logs (7d) so
    # they can see "what happened last quarter" in the Notifications page
    # without exporting to an external log store. Min 1d (a daily run
    # would otherwise have nothing to prune); max ~10y as the same
    # runaway-disk safety net other retention knobs carry.
    "tuning_notification_retention_days": ("NOTIFICATION_RETENTION_DAYS", 90, 1, 3650),
    # Notifications panel page size. Default 25 — the popup felt
    # overwhelming at the previous 50 on busy fleets where every
    # gather emits new entries. Range 5..200 — < 5 forces too many
    # paginated fetches; > 200 risks slow render on the in-app
    # store. Surfaced to the SPA via /api/me's `client_config.notifications_page_size`
    # so the user-side popup picks up the resolved value without a
    # restart.
    "tuning_notification_page_size": ("NOTIFICATION_PAGE_SIZE", 25, 5, 200),
    # Notifications popup poll cadence (seconds) — fallback for when the
    # SSE stream is disconnected AND the operator has the popup open. The
    # popup live-updates via SSE under normal conditions; this knob only
    # controls the polling fall-back. Default 30s; operators on slow
    # connections / power-saving devices can raise. Read by the SPA via
    # `me.client_config.notifications_poll_seconds` (× 1000 → ms).
    "tuning_notifications_poll_interval_seconds":
        ("NOTIFICATIONS_POLL_INTERVAL_SECONDS", 30, 5, 300),
    # AI provider auto-retry on transient upstream overload (HTTP 429
    # / 502 / 503 / 504). Enabled by default — when an AI palette /
    # host-filter call hits one of those statuses on the FIRST attempt
    # AND the first attempt was fast (< first_attempt_max_ms), the
    # call retries ONCE after `backoff_ms` and propagates the second
    # outcome to the route handler. Encoded as 0/1 because TUNABLES
    # only carries ints. Operator can disable from Admin → AI
    # Integration when the second-attempt latency is more annoying
    # than the modal pop-up.
    "tuning_ai_retry_enabled": ("AI_RETRY_ENABLED", 1, 0, 1),
    # Backoff in milliseconds before the retry attempt. Default 2000ms
    # = 2s — short enough that the operator's typing rhythm isn't
    # broken, long enough that a transient overload usually clears.
    # Range 0..30000 (0 = retry immediately, useful for tests; 30s =
    # generous for slow upstreams under heavy load).
    "tuning_ai_retry_backoff_ms": ("AI_RETRY_BACKOFF_MS", 2000, 0, 30000),
    # First-attempt-max-duration gate. The retry only fires when the
    # FIRST attempt resolved in < this many ms — if the first attempt
    # was already slow, the upstream is genuinely struggling and a
    # retry won't help (just doubles the user's wait). Default 5000ms
    # = 5s. Range 100..60000.
    "tuning_ai_retry_first_attempt_max_ms": ("AI_RETRY_FIRST_ATTEMPT_MAX_MS", 5000, 100, 60000),

    # ----- AI provider call envelope ----------------------------------------

    # Max output tokens per AI request. The provider caps actual usage;
    # this is the budget we ASK FOR. Lower for cost-sensitive deployments
    # (Claude Opus runs ~$0.075 per 1k output tokens at default rate-card);
    # raise for long-form palette responses that include disk-projection
    # narratives or multi-step diagnostic guidance. Default 1024 fits
    # ~3-5 paragraphs of prose. Range 16..16384.
    "tuning_ai_max_tokens": ("AI_MAX_TOKENS", 1024, 16, 16384),

    # AI log-context window — how many hours of persistent log files
    # the AI palette injects as context per call. Default 168 (7 days).
    # Pre-fix the AI saw only the in-memory ring buffer's most-recent
    # 30 error+warn lines, which on a busy fleet covers minutes
    # rather than days — operators got "I don't have access to the
    # full 24-hour history" replies. Reads from the persistent log
    # files via `logic.logs.recent_lines_window(hours=N)`. Range
    # 1..720 (30 days max). Higher windows pull more lines into the
    # prompt; the per-call cap (`tuning_ai_log_context_lines`) bounds
    # the actual token cost.
    "tuning_ai_log_context_hours": ("AI_LOG_CONTEXT_HOURS", 168, 1, 720),

    # AI log-context cap — maximum number of error+warn lines the
    # AI palette injects per call. Default 200 — comfortable for a
    # week's worth of signal on a moderately-busy fleet without
    # ballooning the prompt. Newest-last so the AI sees the most
    # recent issues even when the cap trims older lines. Range
    # 10..2000.
    "tuning_ai_log_context_lines": ("AI_LOG_CONTEXT_LINES", 200, 10, 2000),

    # AI provider fallback chain depth — when the active provider returns
    # a transient-overload status, try this many backup providers before
    # surfacing the failure. Default 1 (try ONE backup); 2 tolerates a
    # multi-provider outage at the cost of doubling user-perceived
    # latency on a true outage. Range 0..3 (0 effectively disables the
    # fallback chain even when the master toggle is on).
    "tuning_ai_fallback_max_depth": ("AI_FALLBACK_MAX_DEPTH", 1, 0, 3),

    # AI provider — outbound HTTP wall-clock for the lightweight
    # one-token "test connection" probe. Default 15s — enough for a
    # slow network plus a cold model warm-up; bump to 30-60s on flaky
    # links. Range 2..120. Per-use read inside `logic.ai.test_provider`
    # so a Save in Admin → AI Integration takes effect on the next Test
    # click without restart.
    "tuning_ai_http_timeout_seconds": ("AI_HTTP_TIMEOUT_SECONDS", 15, 2, 120),

    # AI provider — outbound HTTP wall-clock for the real chat-completion
    # call. Default 30s — covers most short prompts at reasonable model
    # latency; raise to 60-120s for long-context prompts or slower
    # reasoning models. Range 5..300. Per-use read inside
    # `logic.ai.ask_provider` so a Save in Admin → AI Integration takes
    # effect on the next call without restart.
    "tuning_ai_extended_http_timeout_seconds": ("AI_EXTENDED_HTTP_TIMEOUT_SECONDS", 30, 5, 300),

    # ----- Backups ----------------------------------------------------------

    # Number of recent backup zips to keep on disk (under
    # /app/data/backups/). The `backup` schedule kind purges older files
    # once this count is exceeded. Default 0 = keep ALL backups (back-
    # compat with pre-existing deploys). Operator typically wants 7-30
    # to bound disk growth on a daily schedule. Range 0..1000.
    # Default 75s (3 × the 25s SSE heartbeat cadence) — was 30s
    # which left only a 5s margin past one heartbeat, so any single
    # tick miss (network blip, GC pause, browser-tab throttle) would
    # false-fire the banner. 75s tolerates 2 missed heartbeats while
    # still surfacing a real outage within ~1 min.
    "tuning_backend_unreachable_threshold_seconds": ("BACKEND_UNREACHABLE_THRESHOLD_SECONDS", 75, 0, 600),
    "tuning_backup_retention_count": ("BACKUP_RETENTION_COUNT", 0, 0, 1000),
    # Settings-as-Code (config_backup schedule kind) retention. Same
    # 0 = unlimited semantics as the backup-zip retention. Operators
    # commit snapshots to git for change tracking; the on-disk
    # rotation here keeps the data dir from filling with daily
    # snapshots over years. Default 30 = roughly one month at daily
    # cadence.
    "tuning_config_backup_retention_count": ("CONFIG_BACKUP_RETENTION_COUNT", 30, 0, 1000),

    # ----- Favicon proxy (bookmark / app tile icon fallback) ----------------
    # Disk-cache TTL (days) for a fetched favicon under /app/data/favicons/.
    # 30 — site favicons change rarely; a stale one re-fetches after the window.
    "tuning_favicon_cache_days": ("FAVICON_CACHE_DAYS", 30, 1, 365),
    # Per-fetch wall-clock (s) for the favicon proxy's upstream GET(s)
    # (favicon.ico + the page-head parse). 8s — favicons are tiny; keep it tight
    # so a slow site can't hang the tile.
    "tuning_favicon_fetch_timeout_seconds": ("FAVICON_FETCH_TIMEOUT_SECONDS", 8, 2, 60),

    # ----- FlareSolverr usage sampler ---------------------------------------
    # How often the lifespan flaresolverr_sampler records each configured
    # FlareSolverr chip's live open-session count (0 = inherit the global
    # stats sample interval). Cheap call (sessions.list), so 10 min is plenty —
    # sessions change slowly.
    "tuning_flaresolverr_sample_interval_seconds": ("FLARESOLVERR_SAMPLE_INTERVAL_SECONDS", 600, 0, 86400),
    # Retention window (days) for flaresolverr_session_samples — drives the
    # card's usage trend. Default 30 (the "past 30 days" the operator asked for).
    "tuning_flaresolverr_history_days": ("FLARESOLVERR_HISTORY_DAYS", 30, 1, 365),

    # ----- ddns-updater -----------------------------------------------------

    # How often the lifespan ddns-updater sampler records each configured
    # ddns-updater chip's public IP + record totals + failing count (0 =
    # inherit the global stats sample interval). Default 600 (10 min) — IP
    # changes are infrequent and the web-UI scrape is cheap.
    "tuning_ddns_sample_interval_seconds": ("DDNS_SAMPLE_INTERVAL_SECONDS", 600, 0, 86400),
    # Retention window (days) for ddns_samples — drives the public-IP-change
    # timeline + the failing-count sparkline. Default 90 (longer than the
    # usage trends since IP changes are rare + worth keeping a quarter of).
    "tuning_ddns_history_days": ("DDNS_HISTORY_DAYS", 90, 1, 730),
    # A ddns-updater record counts as "stale" when its last successful update
    # is older than this many hours (the card surfaces a stale-record count so a
    # record that silently stopped re-pushing is visible). Tune to your update
    # cadence: a 5-minute push cadence wants a low threshold, a daily one higher.
    "tuning_ddns_stale_record_hours": ("DDNS_STALE_RECORD_HOURS", 24, 1, 8760),

    # ----- Fing (network device inventory) ----------------------------------

    # How often the lifespan Fing sampler records each configured Fing chip's
    # online / total device counts into fing_samples (0 = inherit the global
    # stats sample interval). Default 600 (10 min) — presence on a home network
    # moves slowly and the Local-API device scrape is cheap.
    "tuning_fing_sample_interval_seconds": ("FING_SAMPLE_INTERVAL_SECONDS", 600, 0, 86400),
    # Retention window (days) for fing_samples — drives the online-device
    # occupancy trend sparkline. Default 90 (a quarter of network-presence
    # history is plenty for spotting weekday / weekend occupancy patterns).
    "tuning_fing_history_days": ("FING_HISTORY_DAYS", 90, 1, 730),
    # A Fing device first-seen within this many hours counts as NEW (the
    # actionable "an unknown device just joined your network" signal surfaced on
    # the card + status skill). Default 24 — lower it for a tighter alert window,
    # raise it to keep a recently-added device flagged for longer.
    "tuning_fing_new_device_hours": ("FING_NEW_DEVICE_HOURS", 24, 1, 8760),

    # ----- AdGuard Home -----------------------------------------------------

    # How often the lifespan AdGuard sampler snapshots each configured AdGuard
    # host's queries/blocked/clients counters into the history table that drives
    # the fleet blocked-% trend (0 = inherit the global stats sample interval).
    # Default 900 (15 min) — the today-counters move slowly + the probe is cheap.
    "tuning_adguard_sample_interval_seconds": ("ADGUARD_SAMPLE_INTERVAL_SECONDS", 900, 0, 86400),
    # Retention window (days) for adguard_samples — the blocked-% trend that
    # outlives AdGuard's own short rolling stats window + survives a restart.
    # Default 90 (a quarter of daily points).
    "tuning_adguard_history_days": ("ADGUARD_HISTORY_DAYS", 90, 1, 730),
    # How often the AdGuard Home Sync sampler records each configured AGS chip's
    # replica in-sync count into adguardhome_sync_samples for the sync-reliability
    # trend (0 = inherit the global stats sample interval). Default 900 (15 min) —
    # syncs run on their own schedule + the status probe is cheap.
    "tuning_adguardsync_sample_interval_seconds": ("ADGUARDSYNC_SAMPLE_INTERVAL_SECONDS", 900, 0, 86400),
    # Retention window (days) for adguardhome_sync_samples — the sync-reliability
    # trend that survives the sync tool keeping no history of its own. Default 90.
    "tuning_adguardsync_history_days": ("ADGUARDSYNC_HISTORY_DAYS", 90, 1, 730),

    # ----- Pi-hole ----------------------------------------------------------

    # How often the lifespan Pi-hole sampler snapshots each configured Pi-hole
    # host's queries/blocked/clients counters into the history table that drives
    # the fleet blocked-% trend (0 = inherit the global stats sample interval).
    # Default 900 (15 min).
    "tuning_pihole_sample_interval_seconds": ("PIHOLE_SAMPLE_INTERVAL_SECONDS", 900, 0, 86400),
    # Retention window (days) for pihole_samples — the cross-restart blocked-%
    # trend (FTL's today-counters reset on restart). Default 90.
    "tuning_pihole_history_days": ("PIHOLE_HISTORY_DAYS", 90, 1, 730),

    # ----- Seerr (Overseerr / Jellyseerr) -----------------------------------

    # How often the lifespan Seerr sampler snapshots each configured Seerr
    # chip's request-queue gauges (pending / processing / available / issues)
    # into the backlog history table (0 = inherit the global stats interval).
    # Default 900 (15 min) — the queue moves slowly.
    "tuning_seerr_sample_interval_seconds": ("SEERR_SAMPLE_INTERVAL_SECONDS", 900, 0, 86400),
    # Retention window (days) for seerr_samples — drives the request-backlog
    # trend (pending-stuck detection). Default 90.
    "tuning_seerr_history_days": ("SEERR_HISTORY_DAYS", 90, 1, 730),

    # ----- Servarr family (Radarr / Sonarr / Lidarr / Readarr) --------------

    # How often the SHARED lifespan *arr sampler snapshots each Radarr / Sonarr
    # / Lidarr / Readarr instance's library total / missing backlog / queue /
    # free-disk gauges into servarr_samples (0 = inherit the global stats
    # interval). Default 900 (15 min) — the library + disk move slowly, so a
    # coarse cadence keeps the table small while still resolving the trend.
    "tuning_servarr_sample_interval_seconds": ("SERVARR_SAMPLE_INTERVAL_SECONDS", 900, 0, 86400),
    # Retention window (days) for servarr_samples — drives the per-*arr
    # library-growth + missing-backlog sparkline AND the disk-free-runway
    # projection (a longer window = a more confident runway fit). Default 365.
    "tuning_servarr_history_days": ("SERVARR_HISTORY_DAYS", 365, 1, 1095),

    # ----- qBittorrent ------------------------------------------------------

    # How often the lifespan qBittorrent sampler snapshots each instance's
    # dl/up transfer speed + free-disk + torrent count into qbittorrent_samples
    # (0 = inherit the global stats interval). Default 300 (5 min) — transfer
    # speeds move fast, but a 5-min cadence resolves the trend without bloating
    # the table; lower it (e.g. 60) for a finer speed sparkline.
    "tuning_qbittorrent_sample_interval_seconds": ("QBITTORRENT_SAMPLE_INTERVAL_SECONDS", 300, 0, 86400),
    # Retention window (days) for qbittorrent_samples — drives the transfer-speed
    # sparkline AND the free-disk-runway projection (a longer window = a more
    # confident runway fit). Default 30. Lower to save disk; raise for a longer
    # trend.
    "tuning_qbittorrent_history_days": ("QBITTORRENT_HISTORY_DAYS", 30, 1, 1095),
    # How often the UniFi sampler snapshots each chip's connected-client count
    # (+ wireless split + devices-online) into unifi_samples — the trend source
    # for the card's "clients over time" sparkline + "peak N clients" (UniFi's
    # Integration API keeps no client-count history of its own). 0 = inherit the
    # global stats interval. Default 900 (15 min) — client counts drift slowly.
    "tuning_unifi_sample_interval_seconds": ("UNIFI_SAMPLE_INTERVAL_SECONDS", 900, 0, 86400),
    # Retention window (days) for unifi_samples — drives the client-occupancy
    # trend. Default 30. Lower to save disk; raise for a longer trend.
    "tuning_unifi_history_days": ("UNIFI_HISTORY_DAYS", 30, 1, 1095),
    # How often the Tdarr sampler snapshots each chip's cumulative space-saved +
    # transcode count + queue into tdarr_samples. 0 = inherit the global stats
    # interval. Default 900 (15 min) — the cumulative totals move slowly, so a
    # coarse cadence captures the trend without bloating the table.
    "tuning_tdarr_sample_interval_seconds": ("TDARR_SAMPLE_INTERVAL_SECONDS", 900, 0, 86400),
    # Retention window (days) for tdarr_samples — drives the cumulative space-
    # saved line, the transcode-queue burn-down, and the per-day throughput.
    # Default 365 (the space-saved story is most satisfying over a long window);
    # lower to save disk.
    "tuning_tdarr_history_days": ("TDARR_HISTORY_DAYS", 365, 1, 1095),
    # How often the Kavita sampler snapshots each chip's library totals (series /
    # volume / chapter counts + total size) into kavita_samples. 0 = inherit the
    # global stats interval. Default 900 (15 min) — a library grows slowly.
    "tuning_kavita_sample_interval_seconds": ("KAVITA_SAMPLE_INTERVAL_SECONDS", 900, 0, 86400),
    # Retention window (days) for kavita_samples — drives the library-growth line
    # (series count + total size over time). Default 365 (the growth story is
    # most satisfying over a long window); lower to save disk.
    "tuning_kavita_history_days": ("KAVITA_HISTORY_DAYS", 365, 1, 1095),
    # How often the Prowlarr sampler snapshots each chip's cumulative query /
    # grab / failed counters into prowlarr_samples (diffed into per-day rates).
    # 0 = inherit the global stats interval. Default 900 (15 min).
    "tuning_prowlarr_sample_interval_seconds": ("PROWLARR_SAMPLE_INTERVAL_SECONDS", 900, 0, 86400),
    # Retention window (days) for prowlarr_samples — drives the per-day query /
    # grab throughput + daily failure-rate trend. Default 90 (a rate trend
    # doesn't need a full year); lower to save disk.
    "tuning_prowlarr_history_days": ("PROWLARR_HISTORY_DAYS", 90, 1, 1095),

    # ----- Speedtest Tracker ------------------------------------------------

    # How often the lifespan Speedtest sampler ingests each configured chip's
    # results into the long-horizon history table (0 = inherit the global stats
    # sample interval). Default 900 (15 min) — the sampler ingests the WHOLE
    # results series each tick (INSERT OR IGNORE dedups), so this only needs to
    # be shorter than the upstream's own retention window to never miss a result.
    "tuning_speedtest_sample_interval_seconds": ("SPEEDTEST_SAMPLE_INTERVAL_SECONDS", 900, 0, 86400),
    # Retention window (days) for speedtest_samples — the INDEPENDENT long-term
    # trend that survives the upstream ageing out its own results. Default 365
    # (a year of medians); raise for multi-year, lower to save disk.
    "tuning_speedtest_history_days": ("SPEEDTEST_HISTORY_DAYS", 365, 1, 1095),

    # ----- SSH WebSocket ----------------------------------------------------

    # Heartbeat cadence (seconds) for the SSH terminal WebSocket — server-
    # side ping interval that keeps the connection alive past idle-
    # timeouts in NPM / openresty. Default 25 seconds (under the typical
    # 30 s `proxy_read_timeout`). Lower if your proxy has a tight idle
    # timeout (some defaults are 15 s); raise on long-lived sessions to
    # cut traffic. Range 5..120.
    "tuning_ssh_ws_heartbeat_seconds": ("SSH_WS_HEARTBEAT_SECONDS", 25, 5, 120),

    # ----- Per-provider default ports --------------------------------------
    # Each port setting promoted out of the plain `settings` table per the
    # the project conventions "Plain-`settings`-row escape hatch is a drift class" rule.
    # Operator-tunable defaults consumed by per-host probes when the row
    # doesn't carry its own per-host port override. Bounds 1..65535 (TCP /
    # UDP port range). Defaults match standard service ports.

    # SSH terminal + run-command port. Consumed by `logic/ssh.py:resolve_
    # ssh_params` and the SSH admin form's default placeholder. Per-host
    # `hosts_config[].ssh.port` always wins when set; this is the global
    # fallback default. Default 22 (standard SSH).
    "tuning_ssh_default_port": ("SSH_DEFAULT_PORT", 22, 1, 65535),

    # SNMP query port. Consumed by `logic/snmp.py:probe_snmp` and the SNMP
    # admin form's default placeholder. Per-host `hosts_config[].snmp.port`
    # always wins when set. Default 161 (standard SNMP UDP).
    "tuning_snmp_default_port": ("SNMP_DEFAULT_PORT", 161, 1, 65535),

    # Ping (TCP-connect) probe port. Consumed by `logic/ping_sampler.py`
    # and the Ping admin form's default placeholder. Per-host
    # `hosts_config[].ping.port` always wins when set. Default 443
    # (universally-reachable through firewalls; HTTPS).
    "tuning_ping_default_port": ("PING_DEFAULT_PORT", 443, 1, 65535),

    # ICMP inter-packet spacing (milliseconds) for the `icmplib.ping`
    # path. Sub-quarter-second packets can trip IDS / rate-limit
    # anti-flood on commercial firewalls (UBNT EdgeRouter / Sophos /
    # Palo Alto) — operators ping-monitoring through such gear may
    # need to raise this. Default 200ms (icmplib's own default).
    "tuning_ping_packet_interval_ms": ("PING_PACKET_INTERVAL_MS", 200, 100, 2000),

    # SSH terminal connect timeout (seconds) for the interactive WS
    # session entrypoint. Operators with WAN-routed targets may need
    # to raise; strict-watchdog setups may lower. Default 20s.
    "tuning_ssh_terminal_connect_timeout_seconds": ("SSH_TERMINAL_CONNECT_TIMEOUT_SECONDS", 20, 5, 120),

    # SSH terminal login timeout (seconds) — wall-clock cap on the
    # auth handshake (post-connect, pre-shell). Same operator
    # trade-off as the connect timeout above. Default 20s.
    "tuning_ssh_terminal_login_timeout_seconds": ("SSH_TERMINAL_LOGIN_TIMEOUT_SECONDS", 20, 5, 120),

    # SSH terminal connection-close wait timeout (seconds) — wall-clock
    # cap on `conn.wait_closed()` after a terminal session ends. Default
    # 5s. Lower on flaky networks where the FIN never arrives (close
    # blocks forever, audit row stays open); raise if you genuinely care
    # about clean teardown and tolerate longer audit-close delays.
    # Range 1..60. Per-use read inside `ws_ssh_terminal` so a Save in
    # Admin → SSH takes effect on the next session-close without restart.
    "tuning_ssh_close_timeout_seconds": ("SSH_CLOSE_TIMEOUT_SECONDS", 5, 1, 60),

    # OIDC HTTP wall-clock (seconds) — covers every outbound call in
    # `logic/oidc.py`: discovery-doc GET, JWKS fetch, token exchange,
    # and the admin "Test connection" probe. Default 15s. Lower on
    # fast-fail watchdog deploys (8-10s); raise to 30-60s when the
    # IdP is behind a slow corporate proxy / VPN. Range 2..120. Per-use
    # read inside each oidc.py call site so a Save in Admin → Authentik
    # OIDC takes effect on the next round-trip without restart.
    "tuning_oidc_http_timeout_seconds": ("OIDC_HTTP_TIMEOUT_SECONDS", 15, 2, 120),

    # Gather fan-out HTTP wall-clock (seconds) — outer AsyncClient
    # timeout for the main `_gather_impl` Portainer fan-out (containers
    # + services + stacks + tasks + nodes walks). Default 60s. Lower on
    # fast-fail deploys with a healthy Portainer; raise for large fleets
    # (200+ containers) where the registry-digest probe inside each
    # walk adds wall-clock. Range 5..600. Per-use read inside
    # `_gather_impl` so a Save in Admin → Portainer takes effect on the
    # next gather without restart.
    "tuning_gather_client_timeout_seconds": ("GATHER_CLIENT_TIMEOUT_SECONDS", 60, 5, 600),

    # Gather orphaned-container probe timeout (seconds) — per-call
    # wall-clock for the cross-host container-inspect probe that
    # resolves which Swarm node owns a stale container (the inner loop
    # tries each hostname in turn; a 404 should come back fast, a 200
    # wins immediately). Default 3s. Lower (1-2s) for tight fan-outs on
    # responsive networks; raise (5-10s) for sluggish workers.
    # Range 1..30. Per-use read in `_probe_one` so a Save in Admin →
    # Portainer takes effect on the next orphan-probe pass without
    # restart.
    "tuning_gather_orphan_probe_timeout_seconds": ("GATHER_ORPHAN_PROBE_TIMEOUT_SECONDS", 3, 1, 30),
}


def tuning_int(db_key: str) -> int:
    """Return the effective value for one tunable. Three-tier: DB > env > default.

    Always clamps the resolved value to ``(_lo, _hi)`` from ``TUNABLES``.
    The Admin → Config form already validates on save, but a raw SQL
    ``INSERT INTO settings (...)`` (or an env-var typo) would otherwise
    flow straight through to the consumer — corrupt DB state could
    disable a sampler (e.g. a 0 sample interval) or panic the OPS poll
    cadence (e.g. negative ms). Clamping at READ time means every
    consumer sees a value within bounds without each having to re-clamp
    """
    if db_key not in TUNABLES:
        raise KeyError(f"unknown tunable: {db_key}")
    env_var, default, lo, hi = TUNABLES[db_key]

    def _clamp(v: int) -> int:
        return max(lo, min(hi, v))

    raw_db, env_raw = _read_raw_tunable(db_key, env_var)
    if raw_db:
        try:
            return _clamp(int(raw_db))
        except ValueError:
            pass
    if env_raw:
        try:
            return _clamp(int(env_raw))
        except ValueError:
            pass
    return _clamp(default)


# ---------------------------------------------------------------------------
# Stats-charts unified bucketing rule (operator-flagged):
#   1h or 24h  → per-hour buckets   (3600s)
#   7d or 30d  → per-day  buckets   (86400s)
#   90d        → per-week buckets   (604800s)
# Consumed by every `/api/admin/stats/*` endpoint that accepts a `?range=`
# param. Single source of truth so the per-chart implementations stay in
# sync as new ranges are added. Wider windows that can't fit a bar-per-bucket
# (e.g. 1h with hourly buckets = 1 bar) are accepted as-is — a single bar is
# the operator-stated convention for that case.
STATS_RANGE_SECONDS: dict[str, int] = {
    "1h": 3600,
    "24h": 86400,
    "7d": 7 * 86400,
    "30d": 30 * 86400,
    "90d": 90 * 86400,
}

STATS_BUCKET_SECONDS: dict[str, int] = {
    "1h": 3600,
    "24h": 3600,
    "7d": 86400,
    "30d": 86400,
    "90d": 7 * 86400,
}


def stats_range_seconds(range_key: str) -> int | None:
    """Return total seconds for the operator-selected window, or None
    when the key is unknown — caller falls back to its own default.
    """
    return STATS_RANGE_SECONDS.get((range_key or "").strip().lower())


def resolve_provider_interval(provider_tunable_key: "Tunable",
                              *,
                              min_floor: int = 30,
                              global_default: int = 300) -> int:
    """Canonical "sampler tick cadence" resolver shared across per-provider
    samplers (http_probe / service_probe / future siblings).

    Contract: read `provider_tunable_key` via `tuning_int`. If > 0, use
    that value (operator opted in to a per-provider override). If 0,
    fall through to `STATS_SAMPLE_INTERVAL_SECONDS` (the global tick
    cadence every other sampler defers to). In both branches floor at
    `min_floor` seconds (default 30s) to prevent operator-corrupt DB
    state from busy-looping the sampler.

    Argument is typed as :class:`Tunable` (NOT bare ``str``) so a typo
    is caught at the call site — sampler authors discover the
    available knobs through the enum's autocomplete instead of
    grepping. Dynamic-key paths don't use this helper.

    Replaces the twin `_resolve_<provider>_probe_interval()` helpers
    in `logic/host_http_sampler.py` and `logic/service_sampler.py`
    (per the project conventions priority L duplicate-code rule). Future per-
    provider samplers add their `tuning_<provider>_sample_interval_seconds`
    knob to `TUNABLES` + consume this helper.
    """
    iv = tuning_int(provider_tunable_key)
    if iv > 0:
        return max(min_floor, iv)
    global_iv = tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS)
    return max(min_floor, global_iv or global_default)


def stats_bucket_seconds_for_range(range_key: str) -> int:
    """Bucket size in seconds for the chart serving `range_key`. Falls
    back to day buckets (86400s) for unknown ranges so the consumer
    never crashes on a typo'd query param.
    """
    return STATS_BUCKET_SECONDS.get((range_key or "").strip().lower(), 86400)


_UNUSED_KEY_CACHE: Optional[frozenset[str]] = None

# Named regex groups so the "anonymous capturing group" inspection
# stays clean — both regexes are consumed via ``m['key']`` /
# ``m['name']`` rather than the positional ``m.group(1)`` form. Kept
# at module scope so ``scripts/audit_tunables.py`` can import the
# compiled objects directly and the catalogue stays single-sourced.
_BARE_STR_RE = re.compile(r"\b_?tuning_int\s*\(\s*[\"'](?P<key>tuning_[a-z0-9_]+)[\"']")
_ENUM_REF_RE = re.compile(r"\b_?tuning_int\s*\(\s*(?:_Tunable|Tunable)\.(?P<name>[A-Z][A-Z0-9_]+)\b")

# Indirect-consumer helpers — functions that accept a ``Tunable`` member
# (or its bare-string value) and forward it to ``tuning_int`` internally.
# Without this list, the keys consumed via these helpers (e.g.
# ``resolve_provider_interval(Tunable.HTTP_PROBE_SAMPLE_INTERVAL_SECONDS)``
# at logic/host_http_sampler.py) would falsely report as "no live consumer"
# in the Admin → Config audit. New helpers that take a ``Tunable``
# parameter and forward to ``tuning_int`` MUST be appended here so the
# audit stays honest. The regex matches the helper-name + a ``Tunable.X``
# member in ANY argument position of the call (positional or keyword) — so a
# helper that takes the Tunable as a 2nd-positional / kwarg (e.g.
# ``fleet_blocker_trend_summary(table, Tunable.X)`` /
# ``run_sampler_tick(..., history_days_tunable=Tunable.X)``) is still detected.
_INDIRECT_CONSUMER_HELPERS: tuple[str, ...] = (
    "resolve_provider_interval",
    # Shared per-app sampler cadence resolver (logic/apps/_common.py) — every
    # <slug>_sampler.py forwards its <APP>_SAMPLE_INTERVAL_SECONDS Tunable here.
    "resolve_sample_interval",
    # Shared fleet-blocker trend reader (logic/apps/_common.py) — AdGuard +
    # Pi-hole samplers forward their <APP>_HISTORY_DAYS Tunable as the 2nd arg.
    "fleet_blocker_trend_summary",
    # Shared generic sampler tick (logic/apps/_common.py) — every <slug>_sampler
    # forwards its <APP>_HISTORY_DAYS Tunable via the history_days_tunable kwarg.
    "run_sampler_tick",
)
# Build the helper-name alternation fragment. When the list has ONE
# entry the fragment is the bare name (no group); when it has 2+ entries
# the fragment is `(?:a|b|c)`. Conditional construction avoids emitting
# `(?:single_name)` which the IDE rightly flags as an unnecessary non-
# capturing group — and removes the inspection trigger entirely rather
# than relying on a per-line `# noinspection` comment that the regex
# inspector wasn't honouring for these multi-line compile statements.
if len(_INDIRECT_CONSUMER_HELPERS) == 1:
    _HELPER_ALT = _INDIRECT_CONSUMER_HELPERS[0]
else:
    _HELPER_ALT = "(?:" + "|".join(_INDIRECT_CONSUMER_HELPERS) + ")"

# The inner `(?:_Tunable|Tunable)` group IS a genuine multi-alternation
# (typed-enum reference may be aliased to `_Tunable` at the import
# site) — kept non-capturing because we don't need the match value.
# ``[^)]*?`` (non-greedy, stops at the first close-paren) lets the Tunable
# sit in ANY argument position of the call — first-positional (the original
# ``resolve_*`` helpers), 2nd-positional, or a kwarg — while still scoping the
# match to within the one call's parens. The registered helpers never take a
# Tunable after a nested-paren arg, so the first ``)`` always closes the call.
_INDIRECT_REF_RE = re.compile(
    r"\b" + _HELPER_ALT
    + r"\s*\([^)]*?(?:_Tunable|Tunable)\.(?P<name>[A-Z][A-Z0-9_]+)\b"
)
_INDIRECT_BARE_STR_RE = re.compile(
    r"\b" + _HELPER_ALT
    + r"\s*\(\s*[\"'](?P<key>tuning_[a-z0-9_]+)[\"']"
)

# Dynamic-key f-string patterns — keys consumed via f-string
# interpolation that static grep can't see. Treated as consumed so
# the audit doesn't surface false-positive dead-drift reports on them.
# Single source of truth for both ``_unused_keys_scan`` (in-process
# admin badge) and ``scripts/audit_tunables.py`` (offline CI tool).
_DYNAMIC_KEY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tuning_portainer_op_timeout_", ("short_seconds", "medium_seconds", "long_seconds")),
    ("tuning_snmp_walk_concurrency_", ("cisco", "dell", "printer", "synology", "ucd")),
)


def audit_dynamic_keys() -> set[str]:
    """Expand the dynamic-key f-string catalogue to a flat key set.

    Returns the union of every ``prefix + suffix`` combination in
    ``_DYNAMIC_KEY_PATTERNS``. The audit-script + in-process scan
    both consume this so the catalogue stays single-sourced.
    """
    out: set[str] = set()
    for prefix, suffixes in _DYNAMIC_KEY_PATTERNS:
        for suffix in suffixes:
            out.add(prefix + suffix)
    return out


def audit_consumed_keys() -> set[str]:
    """Scan ``main.py`` + ``logic/*.py`` for ``tuning_int(...)`` keys.

    Returns every key the codebase actually reads — bare-string
    literal arguments AND typed ``Tunable.X`` member references AND
    the documented dynamic-key catalogue. Used by:

    * ``_unused_keys_scan()`` (in-process — admin badge gate);
    * ``scripts/audit_tunables.py`` (CI tool — exits non-zero on
      bidirectional drift).

    Reads each source file once via ``Path.read_text``; OSError is
    swallowed per file so a stray unreadable file can't crash the
    scan.
    """
    enum_members = {m.name: m.value for m in Tunable}
    consumed: set[str] = audit_dynamic_keys()
    root = Path(__file__).resolve().parent.parent
    # `main.py` is split across twelve files via a star-import chain
    # under `main_pkg/`. Each child file is named for the route family
    # it owns (`hosts_routes`, `auth_routes`, `apps_routes`, ...).
    # Glob `main*.py` at root for the entry file AND every
    # `main_pkg/*.py` to cover the continuation chain.
    targets: list[Path] = sorted(root.glob("main*.py"))
    targets.extend(sorted((root / "main_pkg").glob("*.py")))
    # rglob (NOT glob) so per-app modules under `logic/apps/` and any other
    # subpackage are scanned — a consumer like `logic/apps/seerr.py` reading
    # `tuning_int(Tunable.SEERR_SUGGEST_COOLDOWN_HOURS)` would otherwise be
    # invisible and its knob falsely flagged decorative.
    targets.extend((root / "logic").rglob("*.py"))
    for path in targets:
        try:
            # encoding="utf-8" required so Windows dev sessions don't trip
            # the cp1252 default codec on em-dashes / smart quotes in
            # docstrings. Linux deploys default to UTF-8 already.
            txt = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in _BARE_STR_RE.finditer(txt):
            consumed.add(m["key"])
        for m in _ENUM_REF_RE.finditer(txt):
            name = m["name"]
            if name in enum_members:
                consumed.add(enum_members[name])
        # Indirect-consumer helpers (see `_INDIRECT_CONSUMER_HELPERS`).
        # Mirrors the direct `tuning_int` regexes — typed-enum AND
        # bare-string forms both count as "consumed" so the audit
        # doesn't flag the keys as decorative.
        for m in _INDIRECT_REF_RE.finditer(txt):
            name = m["name"]
            if name in enum_members:
                consumed.add(enum_members[name])
        for m in _INDIRECT_BARE_STR_RE.finditer(txt):
            consumed.add(m["key"])
    return consumed


def _unused_keys_scan() -> frozenset[str]:
    """Compute the set of declared-but-not-consumed TUNABLE keys.

    Bundled with the running app so the Admin → Config form can flag
    a knob whose value silently no-ops — the drift class where a key
    is declared in TUNABLES (and rendered in the form) but no consumer
    ever reads it via ``tuning_int``, so the operator's edit does
    nothing. Shares the scan logic with ``scripts/audit_tunables.py`` via the
    public ``audit_consumed_keys()`` helper above. Cached at
    module-scope because the source layout is static at runtime; a
    fresh deploy re-warms naturally.
    """
    global _UNUSED_KEY_CACHE
    cached = _UNUSED_KEY_CACHE
    if cached is not None:
        return cached
    consumed = audit_consumed_keys()
    declared = set(TUNABLES.keys())
    result = frozenset(declared - consumed)
    _UNUSED_KEY_CACHE = result
    return result


def effective_state() -> dict:
    """Return current effective values + which tier each came from. Used by
    the GET endpoint so the UI can render placeholders showing the env
    fallback and the code default behind the DB override.

    Each entry also carries ``unused: bool`` so the Admin → Config form
    can render a grey "no live consumer" badge next to a knob whose
    value would silently no-op. Pre-fix the operator had no way to
    know which knobs were dead at runtime — the audit script
    (``scripts/audit_tunables.py``) was the offline answer; this
    surfaces the same signal directly in the UI.
    """
    unused = _unused_keys_scan()
    out: dict = {}
    for k, (env_var, default, lo, hi) in TUNABLES.items():
        raw_db, env_raw = _read_raw_tunable(k, env_var)
        out[k] = {
            "db": raw_db,
            "env": env_raw,
            "default": default,
            "effective": tuning_int(k),
            "min": lo,
            "max": hi,
            "env_var": env_var,
            "unused": k in unused,
        }
    return out


if __name__ == "__main__":
    # Smoke: tuning_int returns env value when DB is blank, code default
    # when both are blank. DB lookup is best-effort — when DB_PATH is
    # unset in the dev shell, the resolver still falls through to env /
    # default cleanly.
    assert tuning_int(Tunable.CACHE_TTL_SECONDS) > 0
    assert tuning_int(Tunable.STATS_CONCURRENCY) > 0
    # Tunable<->TUNABLES coverage check — every enum member must point
    # at a real TUNABLES entry and vice versa. The two parallel
    # declarations are the canonical drift class; this smoke catches
    # the divergence at import time.
    _missing = [m.name for m in Tunable if m.value not in TUNABLES]
    _extra = [k for k in TUNABLES if k not in {m.value for m in Tunable}]
    assert not _missing, f"Tunable members not in TUNABLES: {_missing}"
    assert not _extra, f"TUNABLES keys not in Tunable enum: {_extra}"
    print("tuning smoke passed")
