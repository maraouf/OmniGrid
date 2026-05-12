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
    "tuning_stats_targeted_timeout_seconds":   ("STATS_TARGETED_TIMEOUT_SECONDS", 12, 1, 60),
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
    "tuning_swarm_autoheal_cooldown_minutes":   ("SWARM_AUTOHEAL_COOLDOWN_MINUTES", 30, 1, 1440),
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
    "tuning_port_scan_schedule_min_age_seconds":    ("PORT_SCAN_SCHEDULE_MIN_AGE_SECONDS", 1800, 60, 86400),
    "tuning_port_scan_schedule_per_host_concurrency": ("PORT_SCAN_SCHEDULE_PER_HOST_CONCURRENCY", 1, 1, 4),
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
    "tuning_load_busy_max_seconds":         ("LOAD_BUSY_MAX_SECONDS", 30, 5, 600),
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
    # Ping sampler tick cadence (seconds). Same inherit semantics as
    # the other per-provider sample-interval knobs (Beszel / Pulse /
    # NE / SNMP) — 0 = use the global `tuning_stats_sample_interval_seconds`;
    # > 0 overrides per-Ping. Distinct knob exists because operators
    # often want Ping to tick FASTER than the data-bearing samplers
    # (sub-minute reachability checks against routers / 5G modems)
    # without bumping the global cadence. Range 0..3600.
    "tuning_ping_interval_seconds":      ("PING_INTERVAL_SECONDS", 0, 0, 3600),
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
    # Default 0 (= disabled, falls through to the generic tunable). Set
    # to 1..16 to enable. APC excluded — single-GET probe, concurrency
    # has no effect.
    "tuning_snmp_walk_concurrency_dell":     ("SNMP_WALK_CONCURRENCY_DELL", 0, 0, 16),
    "tuning_snmp_walk_concurrency_cisco":    ("SNMP_WALK_CONCURRENCY_CISCO", 0, 0, 16),
    "tuning_snmp_walk_concurrency_synology": ("SNMP_WALK_CONCURRENCY_SYNOLOGY", 0, 0, 16),
    "tuning_snmp_walk_concurrency_ucd":      ("SNMP_WALK_CONCURRENCY_UCD", 0, 0, 16),
    "tuning_snmp_walk_concurrency_printer":  ("SNMP_WALK_CONCURRENCY_PRINTER", 0, 0, 16),
    # SNMP per-host caches, distinct from the Webmin TTL knobs.
    # Pre-fix the SNMP per-host caches reused tuning_webmin_host_cache_ttl_seconds /
    # tuning_webmin_host_fail_cache_ttl_seconds — operator changing the
    # Webmin TTL silently changed SNMP cache behaviour. Each provider's
    # per-host probe cache (success and fail) gets its OWN dial.
    "tuning_snmp_host_cache_ttl_seconds":      ("SNMP_HOST_CACHE_TTL_SECONDS", 30, 5, 300),
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
    # `host_failure_state` (keyed `snmp:<host_id>` — see CLAUDE.md
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
    "tuning_notification_page_size":      ("NOTIFICATION_PAGE_SIZE", 25, 5, 200),
    # AI provider auto-retry on transient upstream overload (HTTP 429
    # / 502 / 503 / 504). Enabled by default — when an AI palette /
    # host-filter call hits one of those statuses on the FIRST attempt
    # AND the first attempt was fast (< first_attempt_max_ms), the
    # call retries ONCE after `backoff_ms` and propagates the second
    # outcome to the route handler. Encoded as 0/1 because TUNABLES
    # only carries ints. Operator can disable from Admin → AI
    # Integration when the second-attempt latency is more annoying
    # than the modal pop-up.
    "tuning_ai_retry_enabled":            ("AI_RETRY_ENABLED", 1, 0, 1),
    # Backoff in milliseconds before the retry attempt. Default 2000ms
    # = 2s — short enough that the operator's typing rhythm isn't
    # broken, long enough that a transient overload usually clears.
    # Range 0..30000 (0 = retry immediately, useful for tests; 30s =
    # generous for slow upstreams under heavy load).
    "tuning_ai_retry_backoff_ms":         ("AI_RETRY_BACKOFF_MS", 2000, 0, 30000),
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

    # ----- Backups ----------------------------------------------------------

    # Number of recent backup zips to keep on disk (under
    # /app/data/backups/). The `backup` schedule kind purges older files
    # once this count is exceeded. Default 0 = keep ALL backups (back-
    # compat with pre-existing deploys). Operator typically wants 7-30
    # to bound disk growth on a daily schedule. Range 0..1000.
    "tuning_backup_retention_count": ("BACKUP_RETENTION_COUNT", 0, 0, 1000),
    # Settings-as-Code (config_backup schedule kind) retention. Same
    # 0 = unlimited semantics as the backup-zip retention. Operators
    # commit snapshots to git for change tracking; the on-disk
    # rotation here keeps the data dir from filling with daily
    # snapshots over years. Default 30 = roughly one month at daily
    # cadence.
    "tuning_config_backup_retention_count": ("CONFIG_BACKUP_RETENTION_COUNT", 30, 0, 1000),

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
    # CLAUDE.md "Plain-`settings`-row escape hatch is a drift class" rule.
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
    "1h":  3600,
    "24h": 86400,
    "7d":  7 * 86400,
    "30d": 30 * 86400,
    "90d": 90 * 86400,
}

STATS_BUCKET_SECONDS: dict[str, int] = {
    "1h":  3600,
    "24h": 3600,
    "7d":  86400,
    "30d": 86400,
    "90d": 7 * 86400,
}


def stats_range_seconds(range_key: str) -> int | None:
    """Return total seconds for the operator-selected window, or None
    when the key is unknown — caller falls back to its own default.
    """
    return STATS_RANGE_SECONDS.get((range_key or "").strip().lower())


def stats_bucket_seconds_for_range(range_key: str) -> int:
    """Bucket size in seconds for the chart serving `range_key`. Falls
    back to day buckets (86400s) for unknown ranges so the consumer
    never crashes on a typo'd query param.
    """
    return STATS_BUCKET_SECONDS.get((range_key or "").strip().lower(), 86400)


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
