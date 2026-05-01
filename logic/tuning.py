"""Three-tier tunable resolver — DB setting > env var > code default.

Each tunable currently shipped as an env-only constant gets a parallel
DB setting (``tuning_<lowercase_env_var>``); the resolver picks the
non-blank DB value first, falls back to the env var, then to the
hardcoded default. The DB value can be edited live from Admin → Config
without a restart — every consumer calls ``tuning_int(...)`` at the
point of use rather than caching it at module import.
"""
import os

from logic.db import get_setting


# Authoritative table of (db_key, env_var, default, min, max). The UI
# editor + the validator + the resolver all reference this single
# source of truth — adding a new knob means one edit here + extending
# the SettingsIn model + one row in the Admin → Config form.
TUNABLES: dict[str, tuple[str, int, int, int]] = {
    "tuning_cache_ttl_seconds":             ("CACHE_TTL_SECONDS",            900, 30,  86400),
    "tuning_stats_cache_ttl_seconds":       ("STATS_CACHE_TTL_SECONDS",       30,  5,  3600),
    "tuning_registry_concurrency":          ("REGISTRY_CONCURRENCY",           8,  1,  64),
    "tuning_stats_concurrency":             ("STATS_CONCURRENCY",             16,  1,  128),
    "tuning_stats_history_days":            ("STATS_HISTORY_DAYS",             7,  1,  365),
    "tuning_stats_sample_interval_seconds": ("STATS_SAMPLE_INTERVAL_SECONDS", 300, 30,  3600),
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
    # concurrency cap on the SPA's per-host /api/hosts/one/<id>
    # fan-out in `loadHosts()`. Read on /api/me into
    # `me.client_config.hosts_parallel_fetch`; loadHosts resolves it per
    # call so a Save in Admin → Config takes effect on the next refresh
    # without a page reload. Default 6 matched the prior hardcoded
    # const; min 1 (serialised — guaranteed safe but slow); max 32 (NPM
    # default upstream pool exhausts well before this on most setups).
    "tuning_hosts_parallel_fetch": ("HOSTS_PARALLEL_FETCH", 6, 1, 32),
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
    # `api_hosts`'s `_WEBMIN_PROBE_BUDGET`. Pre-#539 these duplicated
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
    "tuning_ping_interval_seconds":      ("PING_INTERVAL_SECONDS", 60, 10, 3600),
    "tuning_ping_concurrency":           ("PING_CONCURRENCY", 16, 1, 128),
    "tuning_ping_probe_timeout_seconds": ("PING_PROBE_TIMEOUT_SECONDS", 2, 1, 30),
    "tuning_ping_cooldown_seconds":      ("PING_COOLDOWN_SECONDS", 300, 30, 3600),
    # SNMP host-stats provider knobs. Two operator-tunable
    # values: the per-probe wall-clock timeout (UDP retransmits live
    # under this budget) and the fan-out concurrency cap that bounds
    # how many parallel SNMP probes the gather + per-host-merge paths
    # run in one tick. Cool-down on consecutive timeouts shares the
    # auth-failure cool-down knob (no separate "credential lockout"
    # surface for SNMP — the cool-down purely throttles probes against
    # an unreachable host, same purpose as the auth one).
    "tuning_snmp_probe_timeout_seconds": ("SNMP_PROBE_TIMEOUT_SECONDS", 5, 1, 60),
    "tuning_snmp_concurrency":           ("SNMP_CONCURRENCY", 16, 1, 128),
    # SNMP per-host caches, distinct from the Webmin TTL knobs.
    # Pre-#659 the SNMP per-host caches reused tuning_webmin_host_cache_ttl_seconds /
    # tuning_webmin_host_fail_cache_ttl_seconds — operator changing the
    # Webmin TTL silently changed SNMP cache behaviour. Each provider's
    # per-host probe cache (success and fail) gets its OWN dial.
    "tuning_snmp_host_cache_ttl_seconds":      ("SNMP_HOST_CACHE_TTL_SECONDS", 30, 5, 300),
    "tuning_snmp_host_fail_cache_ttl_seconds": ("SNMP_HOST_FAIL_CACHE_TTL_SECONDS", 5, 1, 60),
    # dedicated SNMP unreachable-cool-down dial. Pre-#678
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

    try:
        raw = (get_setting(db_key, "") or "").strip()
    except Exception:
        # DB unreachable (config-error boot path) — skip straight to env.
        raw = ""
    if raw:
        try:
            return _clamp(int(raw))
        except ValueError:
            pass
    env_raw = os.getenv(env_var, "")
    if env_raw:
        try:
            return _clamp(int(env_raw))
        except ValueError:
            pass
    return _clamp(default)


def effective_state() -> dict:
    """Return current effective values + which tier each came from. Used by
    the GET endpoint so the UI can render placeholders showing the env
    fallback and the code default behind the DB override.
    """
    out: dict = {}
    for k, (env_var, default, lo, hi) in TUNABLES.items():
        try:
            raw_db = (get_setting(k, "") or "").strip()
        except Exception:
            raw_db = ""
        env_raw = os.getenv(env_var, "")
        out[k] = {
            "db":        raw_db,
            "env":       env_raw,
            "default":   default,
            "effective": tuning_int(k),
            "min":       lo,
            "max":       hi,
            "env_var":   env_var,
        }
    return out


if __name__ == "__main__":
    # Smoke: tuning_int returns env value when DB is blank, code default
    # when both are blank. DB lookup is best-effort — when DB_PATH is
    # unset in the dev shell, the resolver still falls through to env /
    # default cleanly.
    assert tuning_int("tuning_cache_ttl_seconds") > 0
    assert tuning_int("tuning_stats_concurrency") > 0
    print("tuning smoke passed")
