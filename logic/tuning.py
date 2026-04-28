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
    # #383 — host_metrics_sampler permanent-fail window. After this many
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
    # #467 — host-snapshots read-side cache TTL in seconds. The SPA
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
    # #506 — concurrency cap on the SPA's per-host /api/hosts/one/<id>
    # fan-out in `loadHosts()`. Read on /api/me into
    # `me.client_config.hosts_parallel_fetch`; loadHosts resolves it per
    # call so a Save in Admin → Config takes effect on the next refresh
    # without a page reload. Default 6 matched the prior hardcoded
    # const; min 1 (serialised — guaranteed safe but slow); max 32 (NPM
    # default upstream pool exhausts well before this on most setups).
    "tuning_hosts_parallel_fetch": ("HOSTS_PARALLEL_FETCH", 6, 1, 32),
    # #537 — SSE heartbeat cadence (seconds). The /api/events stream
    # emits a `: keepalive\n\n` comment every N seconds so an idle NPM
    # / cloudflare proxy doesn't drop the connection on its own
    # idle-keepalive timer. Lower if your proxy has a tight idle
    # timeout (some defaults are 30s); raise to cut the comment-traffic
    # on long-lived tabs. Default 25s.
    "tuning_sse_heartbeat_seconds": ("SSE_HEARTBEAT_SECONDS", 25, 5, 300),
    # #538 — SSE connection wall-clock cap (seconds). Forces a
    # periodic close + reconnect so the cookie-authed tab re-enters the
    # auth middleware, letting the session-cookie's sliding-window
    # refresh land before the 8h hard cap. Default 6h leaves a 1h
    # margin for clock skew + heartbeat round-trip; do NOT raise past
    # the session hard cap minus that margin.
    "tuning_sse_max_lifetime_seconds": ("SSE_MAX_LIFETIME_SECONDS", 21600, 3600, 25200),
    # #539 — Webmin probe outer budget (seconds). Used by both
    # `_merge_one_host` (the per-host `asyncio.wait_for`) AND legacy
    # `api_hosts`'s `_WEBMIN_PROBE_BUDGET`. Pre-#539 these duplicated
    # the same 20s constant in two places. Default 20s — enough for a
    # slow Miniserv to respond on its three-tier fallback (XML → JSON
    # → HTML scrape) but well under the 30s outer `/api/hosts/one/<id>`
    # budget so a hung Webmin doesn't blow the whole probe.
    "tuning_webmin_probe_budget_seconds": ("WEBMIN_PROBE_BUDGET_SECONDS", 20, 5, 120),
    # #540 — node-exporter per-host probe timeout (seconds). Used by
    # `_merge_one_host`'s NE block (was 10s), legacy `api_hosts`'s NE
    # probe (was 10s), AND `host_metrics_sampler` (was 15s — the
    # sampler's slightly higher value was a slow-startup compensation
    # that's no longer needed with the per-host failure-pause window).
    # Pick 10s as canonical default; operators with a deliberately
    # slow exporter raise it. Strict-rule category (e) "tuned during a
    # 504 incident".
    "tuning_node_exporter_probe_timeout_seconds": ("NODE_EXPORTER_PROBE_TIMEOUT_SECONDS", 10, 2, 60),
    # #541 — SSE freshness-watchdog idle threshold (seconds). Stored
    # as integer seconds for operator-friendly UI; SPA-side
    # `_sseIdleThresholdMs` consumer multiplies × 1000. Default 30s —
    # matches the heartbeat cadence so a stalled stream that's missing
    # both heartbeats AND organic events will trigger the polling
    # fallback within ~2 heartbeat windows. Strict-rule category (b)
    # "freshness threshold".
    "tuning_sse_idle_threshold_seconds": ("SSE_IDLE_THRESHOLD_SECONDS", 30, 5, 300),
    # #542 — pollOps SSE-up keep-alive cadence (seconds). When SSE is
    # connected, `pollOps` slows from `tuning_ops_poll_interval_seconds`
    # to this value as a defence-in-depth safety net (catches a
    # silently-stalled stream that the freshness watchdog hasn't yet
    # flipped). Stored as integer seconds; SPA × 1000 in setTimeout.
    # Default 30s — lines up with the freshness threshold so the
    # keepalive fires at-or-before the watchdog flips _sseConnected
    # to false.
    "tuning_pollops_sse_keepalive_seconds": ("POLLOPS_SSE_KEEPALIVE_SECONDS", 30, 5, 600),
    # #543 — login rate-limit policy. Three knobs grouped (max
    # failures, sliding window, lockout duration). Default mirrors
    # the prior hardcoded policy: 5 failures in 15 min → 15 min
    # lockout. High-security operators want longer lockouts; dev
    # operators want looser limits.
    "tuning_rate_limit_max_failures": ("RATE_LIMIT_MAX_FAILURES", 5, 1, 100),
    "tuning_rate_limit_window_seconds": ("RATE_LIMIT_WINDOW_SECONDS", 900, 60, 86400),
    "tuning_rate_limit_lockout_seconds": ("RATE_LIMIT_LOCKOUT_SECONDS", 900, 60, 86400),
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
