# Env var reference — OmniGrid

The real `.env` lives at the repo root and IS tracked in git. The repo is private (self-hosted
Git), so operational secrets live alongside code rather than in a server-only file. CI ships
`.env` via rsync to `/opt/omnigrid/app/.env` on the Swarm manager. Under the image-build deploy
(see `docs/guidelines/deploy.md`) the file is delivered to the running container via a per-file
bind mount declared in `docker-compose.yml` (`/opt/omnigrid/app/.env:/app/.env:ro`), so secrets
stay on the host filesystem (NOT baked into the image — `.dockerignore` excludes `.env` from the
build context). Inside the container `main.py`'s first lines load `/app/.env` via `python-dotenv`
before any `os.getenv()` runs. `docker-compose.yml` deliberately does NOT use Compose's
`env_file:` key — the app reads its own config file so Portainer's web-editor stacks don't have
to resolve a host-side path.

This file is a curated reference for every key OmniGrid reads, with docs inline. When adding a
new env var to `main.py` or the `logic/` modules, add it here too so admins and future-you
can see the full surface in one place.

A fresh deploy can boot with NO env vars at all — bootstrap admin via
`POST /api/local-auth/bootstrap`, then configure Portainer and (optionally) OIDC / host-stats
providers from the Settings UI. Every value below is either a transitional bootstrap aid or a
process-level tunable.

## Portainer connection (OPTIONAL bootstrap — UI is authoritative)

These four keys are consulted ONCE on first boot with an empty `settings` row and seeded into
the DB. After that, Admin → Portainer wins and env changes are ignored. Marked transitional
— will be removed in a future release once every deploy is UI-managed.

```ini
PORTAINER_URL=https://portainer.example.com:9443
# Create in Portainer → My account → Access tokens.
PORTAINER_API_KEY=
# Usually 1 for the local Swarm endpoint; adjust for multi-endpoint setups.
PORTAINER_ENDPOINT_ID=1
# Set false when Portainer uses a self-signed certificate.
VERIFY_TLS=false
```

## Storage

```ini
# Database backend. Supported values: sqlite. Default is sqlite when unset
# (back-compat with deployments that pre-date this knob). Future adapters
# (postgres, mysql, ...) extend the supported set in
# logic/db.py:_SUPPORTED_DB_TYPES — startup fails loudly via the
# config-error middleware when the value isn't recognised.
DB_TYPE=sqlite

# SQLite path inside the container. Maps to /opt/omnigrid/data/omnigrid.db
# on the host via the bind mount in docker-compose.yml. Change only if you
# relocate the bind mount.
DB_PATH=/app/data/omnigrid.db

# SQLite busy_timeout in milliseconds (per-connection). How long a contended
# op WAITS for the lock before raising SQLITE_BUSY. Kept MODEST (2000) on
# purpose: sqlite3 is synchronous, so the wait BLOCKS the single event loop —
# a 5000 ms wait froze /api/healthz long enough for Swarm to mark the
# container unhealthy and SIGKILL it (the 502 flap). NOT a DB-backed tunable
# (resolving one would re-open the DB). Unset = 2000 ms; 0 = immediate fail.
DB_BUSY_TIMEOUT_MS=2000

# SQLite WAL journal mode — default ON. WAL lets readers proceed while a
# writer commits, REDUCING the reader/writer contention that otherwise stalls
# the event loop (the samplers + gather hit SQLite synchronously). The
# journal-mode switch is done ONCE per process on the first connection (NOT
# per connection — re-issuing it stormed the lock during a deploy rollover and
# caused a 502); on an already-WAL DB it's a no-op. Set 0/false/no/off to
# disable — the app then reverts the DB to the rollback journal (DELETE) once,
# self-healing a DB left in WAL on a filesystem that can't host the -wal/-shm
# files (rare; ext4/xfs/btrfs are fine).
DB_WAL_ENABLED=1
```

## Runtime tuning (process-level)

> **Live override available.** Every tunable below has a matching DB
> setting (`tuning_<lowercase_env_var>`). When set from Admin → Config
> the DB value wins; blank/unset falls back to the env var shown here,
> which falls back to the code default. UI changes take effect on the
> next consumer read (per-request for TTLs, per-tick for samplers — one
> tick lag). The authoritative list of tunables lives in
> `logic/tuning.py:TUNABLES`. **Strict rule:** every admin-tunable
> value goes through TUNABLES — no hardcoded magic numbers in Python /
> JS / HTML. Add new knobs there, not as code constants.

```ini
# Items cache TTL — how long _gather() results stay valid before the next
# caller triggers a refresh.
CACHE_TTL_SECONDS=900

# Stats cache TTL — fresh stats polling without refetching all digests.
STATS_CACHE_TTL_SECONDS=30

# Parallel remote-digest fetches.
REGISTRY_CONCURRENCY=8

# How long a resolved remote manifest digest is reused before OmniGrid
# re-HEADs the registry for it. A full gather fires one HEAD per image; digests
# only change on an upstream push, so caching the result skips most of those
# requests (especially when auto-refresh runs more often than this TTL). Set 0
# to disable. Default 600 (10 min). Only successful digests are cached.
REGISTRY_DIGEST_CACHE_TTL_SECONDS=600

# Parallel /stats calls.
STATS_CONCURRENCY=16

# Per-container stats fetch timeouts. `_one_container_stats` makes up
# to two HTTP calls per running container per gather: first with
# `X-PortainerAgent-Target=<host>` (default 12s — the bumped figure
# lets Portainer's agent forwarding reach busy worker nodes) and a
# fallback without the header (default 10s — manager-local containers
# only). Operator-tunable so a flaky / slow Portainer setup can be
# loosened without a redeploy.
STATS_TARGETED_TIMEOUT_SECONDS=12
STATS_UNTARGETED_TIMEOUT_SECONDS=10

# Swarm-agent unhealthy-banner threshold. After N consecutive gather
# cycles where a Swarm node had ≥1 running task cid but ZERO
# successful stats calls, the SPA flags the agent as unhealthy via
# the banner above the Stacks / Services / Nodes views. Default 3 —
# covers transient hub blips without spamming the banner. Range 1..20.
SWARM_AGENT_UNHEALTHY_THRESHOLD=3

# Retention window for every time-series table (stats_samples,
# host_net_samples, host_metrics_samples). Pruned hourly by each
# lifespan sampler against the same window.
STATS_HISTORY_DAYS=7

# Cadence for the lifespan samplers — _stats_cache snapshot into
# stats_samples, node-exporter scrape into host_net_samples and
# host_metrics_samples.
STATS_SAMPLE_INTERVAL_SECONDS=300

# Permanent-fail window for the host_metrics_sampler. After this many
# seconds of consecutive probe failures the sampler auto-pauses the host;
# an admin clears it via POST /api/hosts/{id}/resume-sampling.
HOST_PERMANENT_FAIL_WINDOW_SECONDS=900

# Frontend /api/ops poll cadence in SECONDS (renamed from
# OPS_POLL_INTERVAL_MS to a seconds-based name for admin-friendly
# UI). Backend multiplies × 1000 before delivering to the SPA's
# setTimeout via /api/me's `client_config.ops_poll_ms`, so the
# consumer contract is unchanged.
OPS_POLL_INTERVAL_SECONDS=2

# Persistent-log retention in days. Daily files under /app/data/logs/
# older than this are deleted by an hourly sweep.
LOG_RETENTION_DAYS=7

# In-app notifications retention (days). The `prune_notifications`
# schedule kind sweeps rows from the `notifications` table older than
# this. Default 90 — most deploys want a longer trail than logs
# (7d) so quarterly review of "what happened" is possible without
# exporting to an external store.
NOTIFICATION_RETENTION_DAYS=90

# Per-host incident audit retention (days). Drives the
# host_metrics_sampler's hourly DELETE pass on `host_failure_events`
# rows older than this window. Decoupled from `STATS_HISTORY_DAYS`
# (which still drives the raw time-series sample tables) so the
# incident audit window can outlast raw samples. 0 disables pruning
# entirely (keep every incident forever). Range 0..3650.
INCIDENTS_RETENTION_DAYS=90

# Host-snapshots read-side cache TTL (seconds). The SPA fans out N
# parallel /api/hosts/one/{id} per refresh; caching the snapshot-table
# read collapses N reads into 1. Set 0 to disable.
HOST_SNAPSHOTS_CACHE_TTL_SECONDS=5

# Per-field stale grace cap (HOURS) for the snapshot fallback. When a
# host_* field has been restored-from-snapshot (no live provider value)
# for longer than this window, drop it from the merged dict + next
# saved snapshot so phantom orphans (fields a host's active providers
# can't actually produce — e.g. host_cpu_percent on an APC UPS) decay
# instead of cycling forever. Default 24h. Range 1..720h.
HOST_SNAPSHOT_STALE_FIELD_MAX_AGE_HOURS=24

# Concurrency cap on the SPA's /api/hosts/one/{id} fan-out.
# Lower if NPM's upstream pool is small or slow Webmin / NE probes
# saturate the loop (manifests as 504s on unrelated static-asset
# requests); raise on a beefy NPM with many hosts.
HOSTS_PARALLEL_FETCH=6

# AI Assistant sidebar drawer width in pixels. Range 320..720, default
# 480. Lower on a smaller laptop where the drawer covers too much
# working area; raise on a 4K monitor where the drawer feels narrow.
# Mobile viewports (< 480 px) ignore this and always render full-width.
AI_SIDEBAR_WIDTH_PX=480

# AI conversation export master toggle. 0 hides the "Export TXT" /
# "Export JSON" buttons in the AI sidebar header; 1 (default) shows
# them. The conversation persists in users.ui_prefs.ai_conversation
# regardless — this only governs the SPA-side download UI.
AI_CONVERSATION_EXPORT_ENABLED=1

# AI sidebar conversation-persist cadence (milliseconds). The sidebar
# checks for conversation changes every N ms and writes through to
# `/api/me/ui-prefs` when the signature changes. Default 2000 ms; raise
# to 5000-10000 ms on slow networks / low-power devices. Takes effect
# on the NEXT page load (interval doesn't re-arm mid-session). Range
# 500..30000.
AI_CONVERSATION_PERSIST_INTERVAL_MS=2000

# In-app notifications popup poll-fallback cadence (seconds). The popup
# normally live-updates via the `notification:*` SSE events; this knob
# only controls the polling fallback when SSE is disconnected AND the
# popup is open. Surfaced via `/api/me`'s
# `client_config.notifications_poll_seconds` (× 1000 → ms in the SPA).
# Range 5..300. Default 30 — raise on slow connections / power-saving
# devices.
NOTIFICATIONS_POLL_INTERVAL_SECONDS=30

# Port-scan provider tunables. Per-port TCP-connect timeout +
# parallel probe count + outer wall-clock budget + banner-grab read
# timeout. The CSV `port_scan_default_ports` is a plain settings
# row (not a TUNABLE — TUNABLES is integer-only) and is documented
# in the in-app Admin → Port Scan form rather than here.
PORT_SCAN_DEFAULT_TIMEOUT_SECONDS=2
PORT_SCAN_DEFAULT_CONCURRENCY=32
PORT_SCAN_MAX_SECONDS=120
PORT_SCAN_BANNER_READ_SECONDS=2

# Port-scan UDP companion (Stage 2). UDP probe timeout (3s default —
# longer than TCP's because there's no handshake to short-circuit) and
# concurrency cap (8 default — friendlier than TCP's 32 because UDP
# traffic is more conspicuous on the network). The master toggle is a
# plain settings row (`port_scan_udp_enabled`); these env vars only
# control the per-port budget knobs.
PORT_SCAN_UDP_DEFAULT_TIMEOUT_SECONDS=3
PORT_SCAN_UDP_DEFAULT_CONCURRENCY=8

# Ping host-stats provider knobs. The first three control the
# lifespan-managed sampler that writes ping_samples; the cool-down
# throttles probes against an unreachable host.
PING_INTERVAL_SECONDS=60
PING_CONCURRENCY=16
PING_PROBE_TIMEOUT_SECONDS=2
PING_COOLDOWN_SECONDS=300

# HTTP / TLS-cert / DNS health probe — seventh host-stats provider.
# Active per-URL TCP / TLS / DNS check. Targets the operator's
# curated `hosts_config[].url` + `services[].url` (or an explicit
# per-host `http_probe.urls` override). Per-host opt-in via the
# `hosts_config[].http_probe.enabled` flag in Admin → Hosts. Master
# toggle lives in the DB-backed `http_probe_enabled` setting.
HTTP_PROBE_TIMEOUT_SECONDS=8
HTTP_PROBE_CONCURRENCY=8
# Sampler cadence — 0 = inherit STATS_SAMPLE_INTERVAL_SECONDS
# (default 300s); >0 overrides per-HTTP-probe. Operators monitoring
# TLS cert expiry don't need sub-minute cadence.
HTTP_PROBE_SAMPLE_INTERVAL_SECONDS=0
# Per-(http_probe, host) auto-pause threshold — N consecutive
# rounds where EVERY URL on a host failed → mark the pair paused;
# subsequent probes are SKIPPED until operator clicks Resume.
# Mixed-success across URLs keeps the host out of the failed bucket.
# 0 = disabled.
HTTP_PROBE_FAILURE_PAUSE_ROUNDS=5
# DNS sub-probe wall-clock. Caps `socket.getaddrinfo` in a thread
# executor so a slow resolver can't stall the sampler.
HTTP_PROBE_DNS_TIMEOUT_SECONDS=5
# TLS cert expiry warning threshold (days). Drawer pill paints
# amber when remaining-days < this; red when ≤ 0 (expired).
HTTP_PROBE_CERT_WARNING_DAYS=14
# Per-host probe-result caches — success / failure. Burst refreshes
# inside the success window reuse the last good probe; failure
# cache shorter so recovery surfaces quickly. 0 = disable cache.
HTTP_PROBE_HOST_CACHE_TTL_SECONDS=30
HTTP_PROBE_HOST_FAIL_CACHE_TTL_SECONDS=5
# Default-accepted-status-codes range (inclusive). When a per-host
# `accepted_status_codes` CSV is set, this range is ignored and the
# CSV wins exactly. Default 200..399 covers 2xx + 3xx (redirect-
# fronted homelab norm). Tighten to 200..299 for strict 2xx-only;
# widen toward 100..599 for diagnostic "any response = alive".
HTTP_PROBE_DEFAULT_ACCEPTED_LO_CODE=200
HTTP_PROBE_DEFAULT_ACCEPTED_HI_CODE=399

# Per-service reachability probe — one chip per curated services[]
# entry with `probe.enabled=true`. Distinct from the host-level
# HTTP probe (which covers the whole row). Master toggle lives in
# the DB-backed `service_probe_enabled` setting; these knobs only
# affect runtime once that toggle is on.
SERVICE_PROBE_SAMPLE_INTERVAL_SECONDS=0
SERVICE_PROBE_CONCURRENCY=16
SERVICE_PROBE_TIMEOUT_SECONDS=5
SERVICE_PROBE_FAILURE_PAUSE_ROUNDS=5

# SNMP host-stats provider knobs. `SNMP_PROBE_TIMEOUT_SECONDS` is the
# per-OID UDP timeout (fast-fail on truly dead hosts);
# `SNMP_WALL_CLOCK_BUDGET_SECONDS` is the total budget for ONE probe
# against ONE host (the probe fans out ~60 OID operations across sys /
# HR / IF / ENTITY + vendor-private MIBs, and slow embedded snmpd needs
# more than the per-OID timeout × N round-trips). Default 60s wall-clock
# is plenty for even slow embedded devices while still bounding gather
# fan-out. Range 5..600s.
SNMP_PROBE_TIMEOUT_SECONDS=5
SNMP_WALL_CLOCK_BUDGET_SECONDS=60
SNMP_CONCURRENCY=16
# Per-host walk concurrency — caps how many `_snmp_get` / `_snmp_walk`
# operations fan out against ONE host inside `probe_snmp`. Default 1
# (fully serialised, CLI-equivalent) chosen for safety: slow BMC-class
# agents (iDRAC9, IPMI, low-power embedded snmpd) drop packets when 60+
# concurrent bulk requests arrive simultaneously. Operators with fast
# snmpd's (cisco / synology / linux net-snmp) can raise to 8-16. Range 1..16.
SNMP_PER_HOST_WALK_CONCURRENCY=1
# SNMP-specific sample interval. 0 (default) = use the global
# STATS_SAMPLE_INTERVAL_SECONDS for SNMP probes too. >0 = SNMP probes
# run on their own cadence (range 30..3600). Useful for keeping
# expensive switch / printer probes on a slower cadence than
# Beszel / NE hosts. Sampler reads per-tick — changes take effect
# without restart.
SNMP_SAMPLE_INTERVAL_SECONDS=0

# Per-host SNMP probe caches — distinct from the Webmin TTLs above so a
# Webmin TTL change can't silently re-tune SNMP behaviour. Success cache
# defaults 30s; failure cache defaults 5s (short, so a recovering host
# is felt within one Hosts-tab refresh).
SNMP_HOST_CACHE_TTL_SECONDS=30
SNMP_HOST_FAIL_CACHE_TTL_SECONDS=5

# SNMP-specific unreachable cool-down (seconds). Distinct from the
# AUTH_FAILURE_COOLDOWN_SECONDS knob below — SNMP has no auth challenge
# to lock out against; this dial purely throttles probes against an
# unreachable host. Default 300s (parity with the legacy auth-cooldown
# default).
SNMP_UNREACHABLE_COOLDOWN_SECONDS=300

# Per-(provider, host) auto-pause threshold. After this many
# consecutive failed sampler / probe rounds against a host, the
# (provider, host) pair gets MARKED auto-paused — subsequent probes
# are SKIPPED entirely until an admin clicks Resume on the
# provider chip in the host drawer. Distinct from any in-memory cool-
# down (which throttles INDIVIDUAL failures); this is the higher-level
# "this device is broken, stop probing it" signal. Default 5 ≈ 25 min
# at the default 5-min cadence. 0 = disabled (cool-down still applies
# where present). Hub-based providers (Beszel/Pulse) gate on hub-fetch-OK
# so a global hub outage doesn't cascade-pause every host. Ping defaults
# to 0 because alive=False is the data, not a fault condition.
SNMP_FAILURE_PAUSE_ROUNDS=5
WEBMIN_FAILURE_PAUSE_ROUNDS=5
BESZEL_FAILURE_PAUSE_ROUNDS=5
PULSE_FAILURE_PAUSE_ROUNDS=5
# Beszel hub probe timeout. Caps `probe_hub` (systems + system_stats +
# systemd_services collections) wall-clock. Lower for fast-fail on a
# stuck hub; raise for slow remote hubs. Range 1..120. Default 15.
BESZEL_PROBE_TIMEOUT_SECONDS=15
# Beszel sampler tick cadence (seconds). 0 = inherit the global
# `STATS_SAMPLE_INTERVAL_SECONDS` (same fallback Pulse / Webmin
# samplers use). Distinct knob exists so a fleet with a noisy / large
# Beszel hub can throttle Beszel sampling independently. Range
# 0..3600. Default 0 (inherit).
BESZEL_SAMPLE_INTERVAL_SECONDS=0
# Pulse sampler tick cadence (seconds). 0 = inherit
# `STATS_SAMPLE_INTERVAL_SECONDS`. Distinct knob exists so a fleet
# with a noisy / large Pulse deployment can throttle Pulse sampling
# independently from Beszel / NE / Webmin. Range 0..3600.
PULSE_SAMPLE_INTERVAL_SECONDS=0
# node-exporter sampler tick cadence (seconds). 0 = inherit
# `STATS_SAMPLE_INTERVAL_SECONDS`. Per-host scrape — every curated
# host with `ne_url` set is hit once per tick. Range 0..3600.
NODE_EXPORTER_SAMPLE_INTERVAL_SECONDS=0
# Per-fetch timeout for the Pulse `/api/state` hub call. Bounds the
# sampler tick wall-clock and the synchronous probe path. Default 15s.
PULSE_PROBE_TIMEOUT_SECONDS=15
# Per-host probe timeout for the Webmin Miniserv sampler. Default 8s
# matches the previous hardcoded fallback in `host_webmin_sampler.py`.
WEBMIN_PROBE_TIMEOUT_SECONDS=8
NODE_EXPORTER_FAILURE_PAUSE_ROUNDS=5
PING_FAILURE_PAUSE_ROUNDS=0

# SSE pipeline tunables. Heartbeat keeps a quiet stream alive past
# upstream proxy idle timers; max-lifetime forces a periodic reconnect
# so the cookie's sliding-window refresh lands; idle-threshold +
# pollops-keepalive drive the freshness watchdog and pollOps fallback.
SSE_HEARTBEAT_SECONDS=25
SSE_MAX_LIFETIME_SECONDS=21600
SSE_IDLE_THRESHOLD_SECONDS=30
POLLOPS_SSE_KEEPALIVE_SECONDS=30

# Webmin probe outer budget + per-host caches.
WEBMIN_PROBE_BUDGET_SECONDS=20
WEBMIN_HOST_CACHE_TTL_SECONDS=30
WEBMIN_HOST_FAIL_CACHE_TTL_SECONDS=5

# node-exporter per-host probe timeout, shared across the request
# path AND host_metrics_sampler.
NODE_EXPORTER_PROBE_TIMEOUT_SECONDS=10

# Outer host-provider cache TTL + sampler concurrency +
# auth-failure cool-down shared by Webmin + SSH.
HOST_PROVIDER_CACHE_TTL_SECONDS=10
# Diagnostic-log interval (in cache-access calls) for the
# `_get_host_provider_state` hit/miss summary line. Default 100;
# operators chasing cache regressions can lower to 25 for verbose
# debugging, or raise to 1000 to cut noise on busy multi-tab sessions.
HOST_PROVIDER_CACHE_DIAG_INTERVAL=100
# gather_stats per-node fail-cache TTL — skip known-unreachable
# Swarm workers for this window after a /containers/json
# ConnectTimeout. Latches off on the next successful probe.
# Eliminates the per-poll executor-saturation cliff caused by
# repeatedly re-paying the full Portainer client timeout for a
# dead worker. Default 60s; range 5-600.
STATS_PER_NODE_UNREACHABLE_TTL_SECONDS=60
# Shared DNS-failure skip cache TTL — every sampler that resolves
# hostnames consults `logic/dns_skip.should_skip_dns` and skips
# probes for unresolvable hosts within this window. Eliminates
# the per-tick executor-thrash on a fleet with N unresolvable
# hostnames. Latches off on next successful resolution. Ping
# is exempt (its purpose IS to test reachability). Default 300
# (5 min); range 60-3600.
DNS_FAILED_SKIP_SECONDS=300
# Short probe timeout when Beszel / Pulse hub latched as
# unreachable on the previous probe — cold-cache callers
# fail fast instead of paying the full 15s budget. Latches
# back to the normal probe timeout on next successful probe.
# Default 3s; range 1-30 for both.
BESZEL_PROBE_TIMEOUT_UNREACHABLE_SECONDS=3
PULSE_PROBE_TIMEOUT_UNREACHABLE_SECONDS=3
# Cache TTL for the per-host configured-providers map consulted by
# `record_provider_outcome`'s defensive guard (refuses + cleans orphan
# rows when a probe fires for an unconfigured provider). Canonical
# hosts_config saves invalidate immediately; this is the backstop.
HOST_PROVIDER_CONFIG_CACHE_TTL_SECONDS=60
HOST_METRICS_PROBE_CONCURRENCY=8
AUTH_FAILURE_COOLDOWN_SECONDS=300

# Login rate-limit policy. Three knobs — max failures, sliding window,
# lockout duration. Defaults: 5 failures / 15 min / 15 min.
RATE_LIMIT_MAX_FAILURES=5
RATE_LIMIT_WINDOW_SECONDS=900
RATE_LIMIT_LOCKOUT_SECONDS=900

# Stat-bar threshold cutovers (Hosts view CPU / Memory / Disk bars).
# Bars flip green → amber at the warn threshold and amber → red at the
# crit threshold. Adjust if your fleet runs hot-by-design (raise warn) or
# you want to surface every spike (lower warn). `warn` must be ≤ `crit`
# and `crit` < 100 — Admin → Config validates the relationship at save.
STAT_BAR_WARN_PCT=60
STAT_BAR_CRIT_PCT=85

# Stack-update convergence-poll window. Portainer's `PUT /api/stacks/{id}`
# accepts the request in ~5s and returns 200, but the actual pull + recreate
# runs ASYNCHRONOUSLY on the docker daemon (often 30-60s+ for real image
# changes). Pre-fix `do_update_stack` called `op.done('success')` as soon as
# the PUT returned — the SPA's Update button reverted to "Update" while the
# daemon was still rolling. Post-fix the op stays in `running` state and
# polls Portainer's service list every `_POLL_SECONDS` until every service
# in this stack's namespace settles (UpdateStatus.State != "updating" for
# two consecutive polls), or `_TIMEOUT_SECONDS` fires. Then `op.done()`.
STACK_UPDATE_OBSERVE_TIMEOUT_SECONDS=300
STACK_UPDATE_OBSERVE_POLL_SECONDS=15

# In-app notifications page size for the Notifications popup. Default 25;
# range 5..200. Surfaced via /api/me's `client_config.notifications_page_size`
# so a Save in Admin → Config takes effect on the next round-trip.
NOTIFICATION_PAGE_SIZE=25

# Idle-time progressive fill for the Hosts view. When the user stays at
# the top of the list without scrolling, a background ticker enqueues one
# not-yet-loaded row every N seconds into the same fan-out worker pool the
# IntersectionObserver uses. 0 disables; 1-2 for fast pre-warm on small
# fleets; 3 (default) for an invisible trickle. Range 0..30.
HOSTS_IDLE_FILL_INTERVAL_SECONDS=3

# Swarm-agent autoheal cool-down (minutes). When the `swarm_agent_health`
# schedule kind fires the `restart` action, this many minutes must elapse
# before a follow-up restart can fire — protects against a thrashing agent
# pinning the manager in a restart loop. Persisted across container restarts
# via the `swarm_autoheal_last_restart_ts` setting. Notify-mode bypasses.
SWARM_AUTOHEAL_COOLDOWN_MINUTES=30

# Port-scan refresh schedule knobs (consumed by the `port_scan_refresh`
# schedule kind — see docs/guidelines/scheduler.md). max_hosts_per_tick
# caps how many hosts one fire scans (oldest-scanned-first); min_age_seconds
# is the minimum elapsed time since a host's previous scan before it's
# eligible again; per_host_concurrency caps how many hosts the runner
# scans IN PARALLEL within ONE tick (the per-host scan itself still uses
# `PORT_SCAN_DEFAULT_CONCURRENCY` for its internal port-probe parallelism).
PORT_SCAN_SCHEDULE_MAX_HOSTS_PER_TICK=5
PORT_SCAN_SCHEDULE_MIN_AGE_SECONDS=1800
PORT_SCAN_SCHEDULE_PER_HOST_CONCURRENCY=1

# How long a schedule may sit marked "running" (previous fire recorded but
# never completed — waiter died / op hung / container killed mid-run) before
# the next tick treats it as a wedged ghost and re-fires it. Self-heals a
# stuck schedule without a restart. Raise if you run a legitimately long
# schedule that can exceed this. Range 600..86400.
SCHEDULE_STUCK_RUN_THRESHOLD_SECONDS=3600

# Per-vendor SNMP walk-concurrency overrides. When `active_vendors`
# auto-detect resolves to EXACTLY ONE vendor AND no per-host override is set
# AND the vendor's tunable is non-zero, the resolver picks the vendor-specific
# default instead of the generic SNMP_PER_HOST_WALK_CONCURRENCY. 0 = disabled
# (fall through to the generic). Range 0..16. APC excluded — single-GET probe.
SNMP_WALK_CONCURRENCY_DELL=0
SNMP_WALK_CONCURRENCY_CISCO=0
SNMP_WALK_CONCURRENCY_SYNOLOGY=0
SNMP_WALK_CONCURRENCY_UCD=0
SNMP_WALK_CONCURRENCY_PRINTER=0

# Webmin sampler outer wall-clock budget (seconds). Caps one full
# `host_webmin_sampler` tick when fanning out across all curated hosts with
# `webmin_name` set. 0 (default) = auto-derive from per-host probe timeout
# × N hosts capped at 5 min. Range 0..600.
WEBMIN_SAMPLER_BUDGET_SECONDS=0

# AI integration — provider call envelope + log-context window + retry +
# fallback chain depth. Output-token cap (16..16384) is what we ASK FOR;
# providers cap actual usage. Log-context hours / lines bound how much of
# the persistent log files the AI palette injects per call. Retry knobs
# control transient-overload recovery (HTTP 429 / 502 / 503 / 504).
# Fallback depth tries up to N backup providers when the active one is
# overloaded. All are integers; AI_RETRY_ENABLED + AI_CONVERSATION_EXPORT_ENABLED
# are 0/1 booleans (TUNABLES is integer-only).
AI_MAX_TOKENS=1024
AI_LOG_CONTEXT_HOURS=168
AI_LOG_CONTEXT_LINES=200
AI_RETRY_ENABLED=1
AI_RETRY_BACKOFF_MS=2000
AI_RETRY_FIRST_ATTEMPT_MAX_MS=5000
AI_FALLBACK_MAX_DEPTH=1
# AI provider HTTP timeouts. Two tiers — standard (palette query,
# host-filter translation, dashboard fetches) and extended (long-form
# multi-tool conversations where the model thinks for longer between
# tool dispatches). Bumped from a hardcoded constant so a slow
# provider can be loosened without a redeploy. Range 2..120 (standard)
# / 5..300 (extended).
AI_HTTP_TIMEOUT_SECONDS=15
AI_EXTENDED_HTTP_TIMEOUT_SECONDS=30

# Telegram listener long-poll + outer HTTP timeout. Telegram holds the
# `getUpdates` connection open this many seconds waiting for an update
# (server cap is 50; default 25 balances "fast wake-up on inactivity"
# with "amortise round-trip cost"). The outer HTTP timeout sits slightly
# above so Telegram has time to flush a long-poll response. Range
# 1..50 (long-poll) and 5..120 (HTTP); raise both in lock-step if a
# tight reverse-proxy `proxy_read_timeout` aborts the read.
TELEGRAM_LONG_POLL_TIMEOUT_SECONDS=25
TELEGRAM_HTTP_TIMEOUT_SECONDS=35

# Telegram destructive-command cooldown (seconds). After a destructive
# verb (`/restart`, `/cleanup`, `/update` confirm flow) lands, the same
# (sender_id, command) pair is rate-limited for this many seconds so a
# duplicate / fat-fingered re-send doesn't double-execute. 30s fits solo
# admin; multi-admin chats may want 60-120s. Range 1..600.
TELEGRAM_DESTRUCTIVE_COOLDOWN_SECONDS=30

# Per-Telegram-user AI rate limit (calls per minute). Every non-`/command`
# Telegram message routes through the AI palette, which costs real money
# on every paid provider. A rolling 60s bucket counts calls per Telegram
# user_id; over-quota senders receive a `⏳ Slow down` reply instead of
# an AI call. Default 6 (one every 10s) — generous for human typing,
# tight enough to catch a runaway script. Range 1..120; set to 120 to
# effectively disable the limit on a private bot you trust.
TELEGRAM_AI_CALLS_PER_MINUTE=6

# `/update all` fan-out concurrency cap. When the operator types
# `/update all confirm`, the dispatcher spawns a Portainer write-op
# per item; pre-bound this was unbounded — N=50 pending updates
# fanned out 50 parallel PUTs and overwhelmed the daemon. Each
# spawned op now goes through a semaphore that bounds concurrent
# rollouts. Default 4 balances rollout speed against Portainer load
# on a single-manager Swarm. Range 1..16 — set to 1 for strictly
# sequential rollouts (safe for small Pi deployments) or raise for
# fast multi-node Swarms.
TELEGRAM_BULK_UPDATE_CONCURRENCY=4

# Host baseline sampler — controls the per-host drift detector. The
# sampler recomputes a 30-day rolling baseline (median ± IQR) for CPU%
# / Memory% / Disk% / Ping RTT once per `recompute_interval`. Baselines
# move slowly so hourly is the default; high-churn fleets may want
# 30 min, monotonic fleets 6h. `first_tick_delay` lets schema migrations
# land before the first read of `host_baselines`. `min_samples` is the
# IQR floor below which a metric stays unbaselined (drift chip hidden);
# default 20 = practical Tukey IQR floor, ~1.5 h at 5-min sample cadence.
# `window_days` is the rolling-window lookback (lower for high-churn,
# raise to smooth seasonal patterns).
HOST_BASELINE_RECOMPUTE_INTERVAL_SECONDS=3600
HOST_BASELINE_FIRST_TICK_DELAY_SECONDS=60
HOST_BASELINE_MIN_SAMPLES=20
HOST_BASELINE_WINDOW_DAYS=30

# Public-IP lookup module — admin-opt-in fetch from ifconfig.co for the
# AI palette + Telegram /ip context block. Default OFF for privacy:
# enabling authorises an outbound call to a third-party JSON service
# (reveals deployment IP / ISP / ASN / geolocation). Cache TTL is the
# in-process memo window (default 600 s caps upstream at ~144/day even
# under heavy use). Fetch timeout caps the outbound HTTP call.
PUBLIC_IP_ENABLED=0
PUBLIC_IP_CACHE_TTL_SECONDS=600
PUBLIC_IP_FETCH_TIMEOUT_SECONDS=8
# Public-IP lookup endpoint URL — operator override. Leave blank
# to use the well-known ifconfig.co default. Uncomment + paste an
# alternative if you prefer a different no-key endpoint:
# PUBLIC_IP_LOOKUP_URL=https://ifconfig.co/json
PUBLIC_IP_LOOKUP_URL=

# WeatherAPI.com — supersedes the legacy Open-Meteo client. Provides
# CURRENT conditions + 7-day forecast + ASTRONOMY (sunrise / sunset /
# moonrise / moonset / moon phase / moon illumination) in one endpoint.
# Free tier: 1M calls/month. API key is set via the Admin → Weather UI
# (write-only / `_set` flag contract); no env var carries the key.
# Cache TTL bounds upstream load (default 600s = max ~144 calls/day
# per coordinate). Sampler interval drives the historical-data writer
# the AI palette + Telegram /weather command consume. Retention
# bounds DB growth (0 = keep every sample forever).
# Provider endpoints — operator-set (NOT baked into Python). Paste
# the public endpoint your deployment uses, or override via the
# Admin → Weather UI on a per-deployment basis. Empty = the weather
# feature can't probe (the dispatcher returns a "configure URL"
# error). Public reference values that work for most deployments
# (uncomment + paste to use):
# WEATHER_OPEN_METEO_ENDPOINT=https://api.open-meteo.com/v1/forecast
# WEATHER_WEATHERAPI_ENDPOINT=https://api.weatherapi.com/v1
WEATHER_OPEN_METEO_ENDPOINT=
WEATHER_WEATHERAPI_ENDPOINT=
WEATHER_CACHE_TTL_SECONDS=600
WEATHER_FETCH_TIMEOUT_SECONDS=8
WEATHER_HISTORY_RETENTION_DAYS=90
WEATHER_SAMPLER_INTERVAL_SECONDS=3600

# Asset-inventory outbound HTTP wall-clocks. Two tiers — token probe
# (OAuth2 client_credentials handshake, default 10 s) and asset fetch
# (paginated `/assets` pull, default 15 s). Bump on slow corporate
# networks / proxied tunnels; lower for tight-watchdog deploys. Range
# 2..120 (token) / 2..300 (fetch).
ASSET_INVENTORY_TOKEN_TIMEOUT_SECONDS=10
ASSET_INVENTORY_FETCH_TIMEOUT_SECONDS=15

# Cold-cache `_gather()` kick wall-clock. Bounds the synchronous wait
# on drill-down endpoints when the items cache is empty. Default 30 s
# covers a typical Portainer fan-out; raise to 60-120 s on large fleets
# / slow registries. Range 5..300.
KICK_GATHER_TIMEOUT_SECONDS=30

# Gather HTTP-client wall-clock — bounds every Portainer call made by
# one `_gather()` cycle (services / containers / tasks / nodes / stacks
# fan-out). Distinct from the kick timeout above (which bounds the
# caller's wait); this one bounds the underlying httpx client. Raise
# for slow Portainer hosts; lower to fail-fast on a stuck endpoint.
# Range 5..600.
GATHER_CLIENT_TIMEOUT_SECONDS=60

# Per-orphan-task containers probe timeout. `_gather()` issues one
# extra inspect per orphan to recover its image ref; default 3 s is
# short by design (orphan probes happen serially after the main fan-out
# and shouldn't blow the gather budget). Range 1..30.
GATHER_ORPHAN_PROBE_TIMEOUT_SECONDS=3

# OIDC discovery / token-exchange HTTP timeout. Bounds calls to the
# IdP's `/.well-known/openid-configuration` (discovery cache load) and
# `/token` (callback code-exchange). Range 2..120.
OIDC_HTTP_TIMEOUT_SECONDS=15

# `/api/items` cold-load busy-state cap (seconds). The SPA disables
# the Update button on rows currently mid-op; this knob bounds the
# longest stretch the SPA will wait for an op to settle before
# unlocking the row. Defence-in-depth against a stuck op leaving the
# row un-clickable. Range 5..600.
LOAD_BUSY_MAX_SECONDS=30

# Portainer write-op wall-clocks. Three tiers — short (container
# restart / remove, default 120 s), medium (service-level + prune,
# default 300 s), long (stack updates + image-pull-heavy paths,
# default 600 s). Raise the matching tier when the specific op class
# is consistently timing out on slow networks / large stacks / slow
# registries. Tier ceilings are tighter than the next tier's floors
# so admins can't accidentally invert the ordering.
PORTAINER_OP_TIMEOUT_SHORT_SECONDS=120
PORTAINER_OP_TIMEOUT_MEDIUM_SECONDS=300
PORTAINER_OP_TIMEOUT_LONG_SECONDS=600

# Backup retention count — how many backup zips under /app/data/backups/
# the `backup` schedule kind keeps after a successful create. 0 (default)
# = keep ALL backups (back-compat). Typical setting is 7-30 to bound
# disk growth on a daily schedule. Range 0..1000.
BACKUP_RETENTION_COUNT=0

# SPA top-of-page "Backend unreachable" banner threshold (seconds). The
# offline banner flips visible once the SPA hasn't received a successful
# backend signal (any SSE event OR any /api/* 2xx) for this many seconds —
# guards against transient blips (server restart, network glitch). Auto-
# hides on the next recovered signal. Set 0 to disable the banner entirely.
# Range 0..600 (10 min). Default 30.
BACKEND_UNREACHABLE_THRESHOLD_SECONDS=30

# Config-backup retention count — analogous knob for the new
# `config_backup` schedule kind (Settings-as-Code snapshots under
# /app/data/config_backups/). Default 30 = ~one month at daily cadence.
# 0 = unlimited.
CONFIG_BACKUP_RETENTION_COUNT=30

# SSH terminal WebSocket heartbeat (seconds). Server-side ping interval
# that keeps the WSS connection alive past idle-timeouts in NPM /
# openresty. Default 25 (under typical 30s `proxy_read_timeout`); raise
# on long-lived sessions to cut traffic. Range 5..120.
SSH_WS_HEARTBEAT_SECONDS=25

# SSH terminal session timeouts. Both apply to the interactive xterm
# modal AND the one-shot `/ssh/run` runner. CONNECT bounds the
# `asyncssh.connect` wall-clock (TCP open + key exchange); LOGIN
# bounds the auth + channel-open wall-clock; CLOSE bounds the
# server-side `conn.close()` after the terminal disconnects so a
# slow disconnect can't pin a worker forever. Range 5..120 (connect /
# login) / 1..60 (close).
SSH_TERMINAL_CONNECT_TIMEOUT_SECONDS=20
SSH_TERMINAL_LOGIN_TIMEOUT_SECONDS=20
SSH_CLOSE_TIMEOUT_SECONDS=5

# Default destination ports for per-host SSH / SNMP / Ping when the
# operator hasn't overridden them at the per-host or global-default
# level. Range 1..65535. Match the standard well-known ports unless
# your fleet runs on alternates.
SSH_DEFAULT_PORT=22
SNMP_DEFAULT_PORT=161
PING_DEFAULT_PORT=443

# EMERGENCY KILL SWITCH — set to `1` / `true` / `yes` to completely
# short-circuit the SNMP sampler tick (no probes fire, no provider
# state changes). Use this when SNMP is causing event-loop starvation
# / healthcheck flap and you need to bring the container back up
# before fixing the root cause in Admin → Providers / Admin → Hosts.
# Read via `os.environ` directly (NOT through the DB) so it works
# even when the DB is unreachable / corrupted.
OMNIGRID_DISABLE_SNMP=0

# Per-host ping packet interval (ms) between consecutive ICMP / TCP
# probes within ONE round-trip-time measurement. Range 100..2000.
# Lower for finer-grained RTT (more traffic to the target); raise
# for spotty links where back-to-back probes confuse the upstream.
PING_PACKET_INTERVAL_MS=200

# Docker Hub auth — optional, avoids anonymous rate limits.
# DOCKERHUB_USER=
# DOCKERHUB_TOKEN=

# Where python-dotenv looks for env values at startup. Override only for
# non-standard deployments.
# ENV_FILE_PATH=/app/.env
```

## Auth — local sessions

```ini
# HMAC key for session cookies. Generate once, keep stable:
#   python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
# Leaving this empty auto-generates an ephemeral one at startup; sessions
# will then die on every restart. Set it explicitly in prod.
SESSION_SECRET=

# How often (seconds) to persist a session's `last_seen_at` (the Admin →
# Sessions "last seen" column). The auth middleware touches this on every
# authenticated request; throttling it to ~once/minute/session removes a
# per-request DB write that otherwise serializes the SPA's polls + per-host
# fan-out against the samplers. 0 = write on every request (legacy). Unset
# = 60. Env-only (a DB-backed tunable would re-read the DB on this same hot
# path, defeating the point).
SESSION_LAST_SEEN_THROTTLE_SECONDS=60
```

## Auth — OIDC SSO (UI-managed, NO env vars)

OIDC provider config is stored in the `settings` table and edited from Admin → Authentik
OIDC. There are intentionally no `OIDC_*` env vars — the UI is the only source of truth. See
`docs/guidelines/authentik.md` for the Authentik-side setup walkthrough.

## Host-stats providers (UI-managed, NO env vars)

All eight providers' credentials live in the `settings` table and are edited from
Admin → Providers (renamed from "Host stats" earlier in 1.2.x). No env vars for any of them.
The curated host list (`hosts_config`) is also DB-backed, managed from Admin → Hosts.

- **Beszel Hub** — `beszel_hub_url`, `beszel_identity`, `beszel_password`, `beszel_verify_tls`,
  `beszel_aliases`.
- **Proxmox Pulse** — `pulse_url`, `pulse_token`, `pulse_verify_tls`, `pulse_aliases`.
- **Prometheus node-exporter** — `node_exporter_enabled`, `node_exporter_url_template`,
  `node_exporter_overrides`. Template accepts either `{host}` OR `{ip}` as a placeholder
  (resolved against the curated host row in `logic/gather.py`).
- **Webmin / Miniserv** — `webmin_url`, `webmin_user`, `webmin_password`, `webmin_verify_tls`,
  `webmin_aliases` (per-host Miniserv URL map — every Webmin target runs its own Miniserv,
  unlike Beszel/Pulse which are central hubs).
- **Ping** — `ping_enabled`, `ping_default_port`, `ping_use_icmp`. Per-host opt-in via
  `hosts_config[].ping = {enabled, host, port, transport}`. Reachability + RTT only.
- **SNMP** — `snmp_default_community`, `snmp_default_version`, `snmp_default_port`,
  `snmp_v3_user`, `snmp_v3_auth_key`, `snmp_v3_priv_key`, `snmp_aliases`. Per-host overrides via
  `hosts_config[].snmp = {enabled, community, version, port, v3_user, v3_auth_key, v3_priv_key}`.
  Optional `pysnmp` dep (in `requirements.txt`) — without it the master toggle disables itself.
- **HTTP probe** — `http_probe_enabled`, `http_probe_aliases`. Per-host opt-in via
  `hosts_config[].http_probe = {enabled, urls?, content_match?, accepted_status_codes?, verify_tls?}`.
  Sub-probes per URL: HTTP GET + TLS handshake (cert expiry) + DNS resolution. Surfaces in
  the drawer + per-row provider chip. Master toggle gated; one-shot `/api/http-probe/test`.
- **Per-service reachability probe** — `service_probe_enabled`. Per-(curated `services[]`)
  opt-in via `services[*].probe.enabled`. Distinct from the host-level HTTP probe — one
  reachability chip per service entry on each curated host. Sampler-only (no synchronous
  `/test` endpoint).

Per-provider chip-colour customisation lives in
`provider_color_{beszel, pulse, node_exporter, webmin, ping, snmp, http_probe, service_probe}`
settings (`#RRGGBB` or blank for default).

## Other UI-managed settings (NO env vars)

- **Weather proxy** (`open_meteo_url`) — edited from Admin → General. Blank = weather
  widget reports `configured: false`; there is NO fallback to `api.open-meteo.com` anymore.
- **Apprise** (`apprise_url`, `apprise_tag`, `portainer_public_url`). Edited from
  Admin → Notifications.
- **Per-event notification toggles** — 24 events today (the canonical set lives in
  `logic.ops:NOTIFY_EVENT_NAMES`). Covers the seven write-op success/failure pairs
  (`stack_update` / `container_update` / `container_restart` / `container_remove` /
  `service_restart` / `prune` / `swarm_agent_restart`), the two swarm-agent transition
  events (`swarm_agent_unhealthy` / `swarm_agent_recovered`), two overlay-cleanup events
  (`overlay_cleanup_success` / `overlay_cleanup_failure`), and security / observability
  events (`user_login` — default OFF; `host_paused` — default ON; `http_probe_failure` /
  `service_probe_failure` / `port_scan_new_port` — default OFF;
  `totp_audit_log_failed` — default ON). Admin → Notifications hosts the global gates
  AND the per-medium master toggles (`notify_medium_app` / `notify_medium_apprise` /
  `notify_medium_telegram`, all default ON); Settings → Notifications hosts the per-user
  opt-in/out (stored in `users.ui_prefs.notify_events`). Two-layer scoping: admin gate +
  medium gate first, then per-user.
- **TOTP / 2FA policy** (`totp_allowed`, `totp_required_for_admins`,
  `totp_required_for_users`, `totp_lockout_max_failures`, `totp_lockout_minutes`). Edited
  from Admin → Authentication. Per-user `totp_force_required` flag lives on the `users`
  table and is toggled from Admin → Users.
- **SSH** — global defaults (`ssh_default_user`, `ssh_default_port`, `ssh_default_private_key`,
  `ssh_default_private_key_passphrase`, `ssh_default_password`, `ssh_default_known_hosts`,
  `ssh_fqdn_suffix`, `ssh_destructive_patterns`, `ssh_custom_actions`). Key material and
  passwords are write-only (`_set` flag pattern); the API exposes only
  `ssh_default_private_key_set` / `ssh_default_password_set` etc. The `SettingsIn` model accepts
  `clear_ssh_private_key` / `clear_ssh_passphrase` / `clear_ssh_password` flags to explicitly
  unset a secret. `ssh_fqdn_suffix` (e.g. `.example.com`) is auto-appended — leading-dot normalised
  — to bare host IDs that don't contain a dot.
- **Scheduler** (`scheduler_timezone` — IANA name).
- **Asset inventory** — OAuth2 OR static lifetime-token modes against an external asset API
  (see `docs/guidelines/api_services.md`). `asset_inventory_auth_mode` selects between
  `oauth2` and `lifetime_token`; secrets follow the same write-only `_set`-flag convention
  with explicit `clear_*` flags.
- **Host groups** (`host_groups`) and curated host list (`hosts_config`) — JSON arrays.
  Optional per-group `number` field for display-prefix labelling, optional per-group SSH
  credentials, optional `parent_name` for nested sub-groups.

## Bootstrap first admin (first-boot only — remove once admin exists)

```ini
# When the users table is empty at startup AND both vars are set, OmniGrid
# creates this admin once and logs a notice. The seed self-disables forever
# after any user exists. Leave blank to claim the first admin interactively
# via POST /api/local-auth/bootstrap instead (see docs/guidelines/auth.md).
BOOTSTRAP_ADMIN_USER=
BOOTSTRAP_ADMIN_PASSWORD=
```

After the first admin lands, blank both values in a follow-up commit. The
SPA surfaces a yellow warning banner (`bootstrap_env_still_set` field on
`/api/me`) when both env vars remain populated AND the users table is non-
empty — the seed code is a no-op at that point but a wiped DB would re-seed
unexpectedly. The banner is dismissable per browser session and re-appears
on every restart until the env vars are cleared.

## Full key reference

Quick index of every env var OmniGrid reads, grouped by scope:

| Var                               | Scope       | Default              | Notes                                                                           |
| --------------------------------- | ----------- | -------------------- | ------------------------------------------------------------------------------- |
| `ENV_FILE_PATH`                   | Bootstrap   | `/app/.env`          | Path `python-dotenv` loads at startup.                                          |
| `PORTAINER_URL`                   | Bootstrap   | `""`                 | UI-managed. Seeded into DB on first boot; Admin → Portainer wins thereafter. |
| `PORTAINER_API_KEY`               | Bootstrap   | `""`                 | Same bootstrap rules.                                                           |
| `PORTAINER_ENDPOINT_ID`           | Bootstrap   | `1`                  | Same bootstrap rules.                                                           |
| `VERIFY_TLS`                      | Bootstrap   | `true`               | Stored as `portainer_verify_tls` after seeding.                                 |
| `DB_TYPE`                         | Runtime     | `sqlite`             | Database backend. Supported: `sqlite`. Invalid value → config-error page.       |
| `DB_PATH`                         | Runtime     | `/app/data/omnigrid.db` | SQLite path inside container.                                                   |
| `DB_BUSY_TIMEOUT_MS`              | Runtime     | `2000`               | SQLite per-connection busy_timeout (ms). Kept modest — sqlite3 is synchronous so the wait blocks the event loop (5000 froze `/api/healthz` → unhealthy kill → 502). `0` = immediate fail. Env-only (a DB tunable would re-open the DB). |
| `DB_WAL_ENABLED`                  | Runtime     | `1` (on)             | SQLite WAL (switched once per process on the first connection; reduces reader/writer contention). `0`/`false`/`no`/`off` disables it and reverts the DB to the rollback journal (DELETE) once. busy_timeout + synchronous apply regardless. |
| `CACHE_TTL_SECONDS`               | Runtime     | `900`                | Items cache TTL.                                                                |
| `STATS_CACHE_TTL_SECONDS`         | Runtime     | `30`                 | Stats cache TTL.                                                                |
| `REGISTRY_CONCURRENCY`            | Runtime     | `8`                  | Parallel remote-digest fetches.                                                 |
| `REGISTRY_DIGEST_CACHE_TTL_SECONDS` | Runtime   | `600`                | Reuse a resolved manifest digest for N s before re-HEADing (`0` disables).      |
| `STATS_CONCURRENCY`               | Runtime     | `16`                 | Parallel `/stats` calls.                                                        |
| `STATS_HISTORY_DAYS`              | Runtime     | `7`                  | Retention window for `stats_samples`.                                           |
| `STATS_SAMPLE_INTERVAL_SECONDS`   | Runtime     | `300`                | Sampler cadence.                                                                |
| `STATS_TARGETED_TIMEOUT_SECONDS`  | Runtime     | `12`                 | Per-container `/stats` timeout WITH `X-PortainerAgent-Target`. Range 1..60.     |
| `STATS_UNTARGETED_TIMEOUT_SECONDS`| Runtime     | `10`                 | Per-container `/stats` timeout for the manager-local fallback path. Range 1..60. |
| `SWARM_AGENT_UNHEALTHY_THRESHOLD` | Runtime     | `3`                  | Consecutive failed gather cycles before the unhealthy banner fires. Range 1..20. |
| `HOST_PERMANENT_FAIL_WINDOW_SECONDS` | Runtime  | `900`                | host_metrics_sampler auto-pause window.                                          |
| `OPS_POLL_INTERVAL_SECONDS`       | Runtime     | `2`                  | SPA's /api/ops poll cadence in seconds; multiplied × 1000 before delivery via `client_config.ops_poll_ms`. Renamed from the legacy `OPS_POLL_INTERVAL_MS` for admin-UI friendliness. |
| `LOG_RETENTION_DAYS`              | Runtime     | `7`                  | Persistent-log retention.                                                        |
| `NOTIFICATION_RETENTION_DAYS`     | Runtime     | `90`                 | In-app notifications retention. Drives the `prune_notifications` schedule kind. |
| `INCIDENTS_RETENTION_DAYS`        | Runtime     | `90`                 | Per-host incident audit retention. Drives the hourly DELETE on `host_failure_events`. 0 disables pruning. Range 0..3650. |
| `HOST_SNAPSHOTS_CACHE_TTL_SECONDS` | Runtime    | `5`                  | host_snapshots read-cache TTL.                                                   |
| `HOST_SNAPSHOT_STALE_FIELD_MAX_AGE_HOURS` | Runtime | `24`           | Per-field stale grace cap (hours). Drops orphan host_* fields from snapshot when restored-stale longer than the cap. |
| `HOSTS_PARALLEL_FETCH`            | Runtime     | `6`                  | SPA fan-out concurrency cap on `/api/hosts/one/{id}`.                            |
| `AI_SIDEBAR_WIDTH_PX`             | Runtime     | `480`                | AI Assistant sidebar drawer width in pixels (320..720). Mobile ignores.          |
| `AI_CONVERSATION_EXPORT_ENABLED`  | Runtime     | `1`                  | Show / hide the AI conversation export buttons (TXT + JSON) in the sidebar.     |
| `AI_CONVERSATION_PERSIST_INTERVAL_MS` | Runtime | `2000`               | AI sidebar conversation auto-persist cadence (ms). Takes effect on next page load. Range 500..30000. |
| `NOTIFICATIONS_POLL_INTERVAL_SECONDS` | Runtime | `30`                 | Notifications-popup poll fallback (seconds) when the SSE stream is down. Range 5..300. |
| `PORT_SCAN_DEFAULT_TIMEOUT_SECONDS` | Runtime   | `2`                  | Per-port TCP-connect timeout for the port-scan provider (1..30).                 |
| `PORT_SCAN_DEFAULT_CONCURRENCY`   | Runtime     | `32`                 | Parallel probe cap per scan (1..256).                                            |
| `PORT_SCAN_MAX_SECONDS`           | Runtime     | `120`                | Outer wall-clock budget for a single port-scan request (30..1800).               |
| `PORT_SCAN_BANNER_READ_SECONDS`   | Runtime     | `2`                  | Banner-grab read timeout when `banner_grab=true` on a scan (1..30).              |
| `PORT_SCAN_UDP_DEFAULT_TIMEOUT_SECONDS` | Runtime | `3`                  | Per-port UDP probe timeout (1..30). Stage 2 UDP companion.                       |
| `PORT_SCAN_UDP_DEFAULT_CONCURRENCY` | Runtime   | `8`                  | UDP probe concurrency cap (1..64). Stage 2 UDP companion.                        |
| `PING_INTERVAL_SECONDS`           | Runtime     | `60`                 | Ping sampler tick cadence.                                                       |
| `PING_CONCURRENCY`                | Runtime     | `16`                 | Ping sampler fan-out.                                                            |
| `PING_PROBE_TIMEOUT_SECONDS`      | Runtime     | `2`                  | Per-probe timeout.                                                               |
| `PING_COOLDOWN_SECONDS`           | Runtime     | `300`                | Per-(host, port) cool-down on consecutive ping failures.                         |
| `HTTP_PROBE_TIMEOUT_SECONDS`      | Runtime     | `8`                  | HTTP / TLS probe per-URL wall-clock (caps GET request AND TLS handshake). Range 1..60.   |
| `HTTP_PROBE_CONCURRENCY`          | Runtime     | `8`                  | Parallel HTTP / TLS probes per sampler tick. Range 1..32.                        |
| `HTTP_PROBE_SAMPLE_INTERVAL_SECONDS` | Runtime  | `0`                  | HTTP probe sampler cadence in seconds. 0 = inherit `STATS_SAMPLE_INTERVAL_SECONDS`. Range 0..3600. |
| `HTTP_PROBE_FAILURE_PAUSE_ROUNDS` | Runtime     | `5`                  | Per-(http_probe, host) auto-pause threshold. A "round" = "every URL for this host failed". 0 = disabled. Range 0..100. |
| `HTTP_PROBE_DNS_TIMEOUT_SECONDS`  | Runtime     | `5`                  | DNS sub-probe wall-clock (caps `socket.getaddrinfo`). Range 1..30.               |
| `HTTP_PROBE_CERT_WARNING_DAYS`    | Runtime     | `14`                 | TLS cert expiry warning threshold — drawer paints expiry pill amber under this many days, red when ≤ 0. Range 1..365. |
| `HTTP_PROBE_HOST_CACHE_TTL_SECONDS` | Runtime   | `30`                 | Per-host HTTP probe success cache TTL. Range 0..600 (0 = disable cache).         |
| `HTTP_PROBE_HOST_FAIL_CACHE_TTL_SECONDS` | Runtime | `5`               | Per-host HTTP probe failure cache TTL. Tight so recovery surfaces fast. Range 0..600. |
| `HTTP_PROBE_DEFAULT_ACCEPTED_LO_CODE` | Runtime | `200`               | Default accepted-status-code range LOW bound (inclusive). Per-host CSV override wins exactly. Range 100..599. |
| `HTTP_PROBE_DEFAULT_ACCEPTED_HI_CODE` | Runtime | `399`               | Default accepted-status-code range HIGH bound (inclusive). 399 covers 2xx + 3xx (homelab norm). Range 100..599. |
| `SERVICE_PROBE_SAMPLE_INTERVAL_SECONDS` | Runtime | `0`              | Per-service probe sampler cadence. 0 = inherit `STATS_SAMPLE_INTERVAL_SECONDS`. Range 0..3600. |
| `SERVICE_PROBE_CONCURRENCY`       | Runtime     | `16`                 | Parallel service probes per sampler tick. Range 1..64.                           |
| `SERVICE_PROBE_TIMEOUT_SECONDS`   | Runtime     | `5`                  | Per-target TCP/HTTP probe timeout. Range 1..30.                                  |
| `SERVICE_PROBE_FAILURE_PAUSE_ROUNDS` | Runtime  | `5`                  | Per-(service_probe, host) auto-pause threshold. 0 = disabled. Range 0..50.       |
| `SNMP_PROBE_TIMEOUT_SECONDS`      | Runtime     | `5`                  | Per-OID UDP timeout for SNMP queries (fast-fail on dead hosts).                  |
| `SNMP_WALL_CLOCK_BUDGET_SECONDS`  | Runtime     | `60`                 | Total wall-clock budget for ONE probe against ONE host (~60 OIDs round-trip). Range 5..600. |
| `SNMP_CONCURRENCY`                | Runtime     | `16`                 | SNMP probe fan-out cap (parallel hosts within one tick).                         |
| `SNMP_PER_HOST_WALK_CONCURRENCY`  | Runtime     | `1`                  | Per-host walk concurrency inside `probe_snmp`. 1 = serialised (CLI-equivalent). Range 1..16. |
| `SNMP_SAMPLE_INTERVAL_SECONDS`    | Runtime     | `0`                  | SNMP-specific sample interval; 0 inherits the global stats interval, 30..3600 overrides for SNMP probes only. |
| `SNMP_HOST_CACHE_TTL_SECONDS`     | Runtime     | `30`                 | Per-host SNMP success-cache TTL. Distinct from Webmin's TTL so a Webmin tweak can't re-tune SNMP. |
| `SNMP_HOST_FAIL_CACHE_TTL_SECONDS`| Runtime     | `5`                  | Per-host SNMP failure-cache TTL. Tight so recovery is felt within one refresh cycle. |
| `SNMP_UNREACHABLE_COOLDOWN_SECONDS` | Runtime   | `300`                | SNMP-specific unreachable cool-down — distinct from AUTH_FAILURE_COOLDOWN_SECONDS (no auth challenge to lock out against). |
| `SNMP_FAILURE_PAUSE_ROUNDS`       | Runtime     | `5`                  | Per-(snmp, host) auto-pause threshold. After N consecutive failed sampler rounds, the chip flips to Paused and probes stop until manual Resume. 0 = disabled. |
| `WEBMIN_FAILURE_PAUSE_ROUNDS`     | Runtime     | `5`                  | Per-(webmin, host) auto-pause threshold. Cool-down responses don't count toward the threshold; only real probe failures do. 0 = disabled. |
| `BESZEL_FAILURE_PAUSE_ROUNDS`     | Runtime     | `5`                  | Per-(beszel, host) auto-pause threshold. Hub-fetch-OK gate so a global hub outage doesn't cascade-pause every host. 0 = disabled. |
| `BESZEL_PROBE_TIMEOUT_SECONDS`    | Runtime     | `15`                 | Wall-clock timeout for `probe_hub` (systems + system_stats + systemd_services). Range 1..120. |
| `BESZEL_SAMPLE_INTERVAL_SECONDS`  | Runtime     | `0`                  | Beszel sampler tick cadence in seconds. 0 = inherit `STATS_SAMPLE_INTERVAL_SECONDS`. Range 0..3600. |
| `PULSE_SAMPLE_INTERVAL_SECONDS`   | Runtime     | `0`                  | Pulse sampler tick cadence in seconds. 0 = inherit `STATS_SAMPLE_INTERVAL_SECONDS`. Range 0..3600. |
| `NODE_EXPORTER_SAMPLE_INTERVAL_SECONDS` | Runtime | `0`                | node-exporter sampler tick cadence in seconds. 0 = inherit `STATS_SAMPLE_INTERVAL_SECONDS`. Range 0..3600. |
| `PULSE_FAILURE_PAUSE_ROUNDS`      | Runtime     | `5`                  | Per-(pulse, host) auto-pause threshold. Same hub-fetch-OK contract as Beszel. 0 = disabled. |
| `PULSE_PROBE_TIMEOUT_SECONDS`     | Runtime     | `15`                 | Per-fetch timeout for Pulse `/api/state` hub probe. Bounds sampler tick wall-clock + sync probe path. Range 1..120. |
| `WEBMIN_PROBE_TIMEOUT_SECONDS`    | Runtime     | `8`                  | Per-host probe timeout for the Webmin Miniserv sampler. Range 1..120. |
| `NODE_EXPORTER_FAILURE_PAUSE_ROUNDS` | Runtime  | `5`                  | Per-(node_exporter, host) auto-pause threshold. Per-host scrape, so any HTTP error / timeout / `exporter_error` counts. 0 = disabled. |
| `PING_FAILURE_PAUSE_ROUNDS`       | Runtime     | `0`                  | Per-(ping, host) auto-pause threshold. Counts ONLY sampler-level errors (DNS, ICMP perm-denied, transport setup), NOT alive=False which is the actual data. Default 0 (disabled) so a normally-down host doesn't get its ping chip spuriously paused. |
| `SSE_HEARTBEAT_SECONDS`           | Runtime     | `25`                 | SSE keepalive comment cadence.                                                   |
| `SSE_MAX_LIFETIME_SECONDS`        | Runtime     | `21600`              | SSE connection wall-clock cap before forced reconnect.                           |
| `SSE_IDLE_THRESHOLD_SECONDS`      | Runtime     | `30`                 | SPA freshness-watchdog idle threshold.                                           |
| `POLLOPS_SSE_KEEPALIVE_SECONDS`   | Runtime     | `30`                 | pollOps fallback cadence when SSE connected.                                     |
| `WEBMIN_PROBE_BUDGET_SECONDS`     | Runtime     | `20`                 | Outer per-host Webmin probe timeout.                                             |
| `WEBMIN_HOST_CACHE_TTL_SECONDS`   | Runtime     | `30`                 | Per-host Webmin success cache TTL.                                               |
| `WEBMIN_HOST_FAIL_CACHE_TTL_SECONDS` | Runtime  | `5`                  | Per-host Webmin failure cache TTL.                                               |
| `NODE_EXPORTER_PROBE_TIMEOUT_SECONDS` | Runtime | `10`                 | Per-host NE scrape timeout.                                                      |
| `HOST_PROVIDER_CACHE_TTL_SECONDS` | Runtime     | `10`                 | Outer host-provider memo TTL.                                                    |
| `HOST_PROVIDER_CACHE_DIAG_INTERVAL` | Runtime   | `100`                | Cache hit/miss diagnostic-log cadence (calls). Lower = more verbose.            |
| `STATS_PER_NODE_UNREACHABLE_TTL_SECONDS` | Runtime | `60`             | gather_stats skip-window for failed Swarm workers (seconds).                    |
| `DNS_FAILED_SKIP_SECONDS`            | Runtime   | `300`                | Shared sampler skip-window for hosts with failing DNS resolution.               |
| `BESZEL_PROBE_TIMEOUT_UNREACHABLE_SECONDS` | Runtime | `3`              | Short Beszel probe timeout when previous probe latched as unreachable.          |
| `PULSE_PROBE_TIMEOUT_UNREACHABLE_SECONDS`  | Runtime | `3`              | Short Pulse probe timeout when previous probe latched as unreachable.           |
| `HOST_PROVIDER_CONFIG_CACHE_TTL_SECONDS` | Runtime | `60`                 | Per-host configured-providers map cache TTL (record_provider_outcome guard).     |
| `HOST_METRICS_PROBE_CONCURRENCY`  | Runtime     | `8`                  | host_metrics_sampler per-tick NE probe fan-out.                                  |
| `AUTH_FAILURE_COOLDOWN_SECONDS`   | Runtime     | `300`                | Shared Webmin + SSH auth-failure cool-down.                                      |
| `RATE_LIMIT_MAX_FAILURES`         | Runtime     | `5`                  | Login rate-limit max fails.                                                      |
| `RATE_LIMIT_WINDOW_SECONDS`       | Runtime     | `900`                | Login rate-limit sliding window.                                                 |
| `RATE_LIMIT_LOCKOUT_SECONDS`      | Runtime     | `900`                | Login rate-limit lockout duration.                                               |
| `STAT_BAR_WARN_PCT`               | Runtime     | `60`                 | Stat-bar amber-threshold percentage (Hosts view CPU/Mem/Disk bars). Range 30..90; must be ≤ `STAT_BAR_CRIT_PCT`. |
| `STAT_BAR_CRIT_PCT`               | Runtime     | `85`                 | Stat-bar red-threshold percentage. Range 50..99.                                 |
| `STACK_UPDATE_OBSERVE_TIMEOUT_SECONDS` | Runtime | `300`                | Maximum time `do_update_stack` waits for Swarm-service rollouts to settle after Portainer accepts the PUT. Range 30..1800. |
| `STACK_UPDATE_OBSERVE_POLL_SECONDS`    | Runtime | `15`                 | Polling cadence for the post-PUT service-list check. Range 5..120.               |
| `NOTIFICATION_PAGE_SIZE`          | Runtime     | `25`                 | In-app notifications popup page size. Range 5..200. Surfaced via `client_config.notifications_page_size`. |
| `HOSTS_IDLE_FILL_INTERVAL_SECONDS` | Runtime    | `3`                  | Idle-time progressive fill cadence for the Hosts view (0 = disabled). Range 0..30. |
| `SWARM_AUTOHEAL_COOLDOWN_MINUTES` | Runtime     | `30`                 | Cool-down (minutes) between consecutive `swarm_agent_health` `restart` actions. Persisted across container restarts via `swarm_autoheal_last_restart_ts`. Range 1..1440. |
| `PORT_SCAN_SCHEDULE_MAX_HOSTS_PER_TICK` | Runtime | `5`                | `port_scan_refresh` runner cap — max hosts touched per tick (oldest-scanned-first). Range 1..50. |
| `PORT_SCAN_SCHEDULE_MIN_AGE_SECONDS` | Runtime  | `1800`               | `port_scan_refresh` runner — min age (s) of a host's previous scan before re-eligible. Range 60..86400. |
| `PORT_SCAN_SCHEDULE_PER_HOST_CONCURRENCY` | Runtime | `1`              | `port_scan_refresh` runner — host-level parallelism within ONE tick (per-host scan still uses `PORT_SCAN_DEFAULT_CONCURRENCY`). Range 1..4. |
| `SCHEDULE_STUCK_RUN_THRESHOLD_SECONDS` | Runtime | `3600`            | Seconds a schedule may sit marked "running" (fire recorded, completion never stamped — waiter died / op hung / killed mid-run) before the next tick treats it as a wedged ghost and re-fires. Self-heals without a restart. Raise above a legitimately long schedule's runtime. Range 600..86400. |
| `SNMP_WALK_CONCURRENCY_DELL`      | Runtime     | `0`                  | Per-vendor SNMP walk-concurrency override. 0 = disabled (fall through to `SNMP_PER_HOST_WALK_CONCURRENCY`). Range 0..16. |
| `SNMP_WALK_CONCURRENCY_CISCO`     | Runtime     | `0`                  | Per-vendor SNMP walk-concurrency override (Cisco). Range 0..16. |
| `SNMP_WALK_CONCURRENCY_SYNOLOGY`  | Runtime     | `0`                  | Per-vendor SNMP walk-concurrency override (Synology). Range 0..16. |
| `SNMP_WALK_CONCURRENCY_UCD`       | Runtime     | `0`                  | Per-vendor SNMP walk-concurrency override (UCD-SNMP / Linux net-snmp). Range 0..16. |
| `SNMP_WALK_CONCURRENCY_PRINTER`   | Runtime     | `0`                  | Per-vendor SNMP walk-concurrency override (Printer-MIB). Range 0..16. |
| `WEBMIN_SAMPLER_BUDGET_SECONDS`   | Runtime     | `0`                  | Outer wall-clock budget for one full `host_webmin_sampler` tick. 0 = auto-derive (probe timeout × N hosts, capped at 5 min). Range 0..600. |
| `AI_MAX_TOKENS`                   | Runtime     | `1024`               | Max output tokens per AI request (the budget we ASK FOR; provider caps actual). Range 16..16384. |
| `AI_LOG_CONTEXT_HOURS`            | Runtime     | `168`                | How many hours of persistent log files the AI palette injects as context per call. Range 1..720. |
| `AI_LOG_CONTEXT_LINES`            | Runtime     | `200`                | Maximum error+warn lines the AI palette injects per call. Range 10..2000. |
| `AI_RETRY_ENABLED`                | Runtime     | `1`                  | Auto-retry AI calls once on HTTP 429 / 502 / 503 / 504. 0/1. |
| `AI_RETRY_BACKOFF_MS`             | Runtime     | `2000`               | Backoff (ms) before the AI retry attempt. Range 0..30000. |
| `AI_RETRY_FIRST_ATTEMPT_MAX_MS`   | Runtime     | `5000`               | First-attempt-max-duration gate — retry only fires when the first attempt resolved in < this many ms. Range 100..60000. |
| `AI_FALLBACK_MAX_DEPTH`           | Runtime     | `1`                  | AI provider fallback chain depth — number of backup providers tried on transient overload. Range 0..3 (0 disables the chain). |
| `AI_HTTP_TIMEOUT_SECONDS`         | Runtime     | `15`                 | AI provider HTTP timeout — standard tier (palette / host-filter / dashboard fetches). Range 2..120. |
| `AI_EXTENDED_HTTP_TIMEOUT_SECONDS`| Runtime     | `30`                 | AI provider HTTP timeout — extended tier (long-form multi-tool conversations). Range 5..300. |
| `BACKUP_RETENTION_COUNT`          | Runtime     | `0`                  | Number of recent backup zips the `backup` schedule kind keeps after a successful create (0 = keep all). Range 0..1000. |
| `BACKEND_UNREACHABLE_THRESHOLD_SECONDS` | Runtime | `30`               | Seconds of silence before the SPA's "Backend unreachable" top banner appears (0 disables). Range 0..600. |
| `CONFIG_BACKUP_RETENTION_COUNT`   | Runtime     | `30`                 | Number of `config_backup` snapshots retained under `/app/data/config_backups/`. 0 = unlimited. Range 0..1000. |
| `TELEGRAM_LONG_POLL_TIMEOUT_SECONDS` | Runtime  | `25`                 | Telegram `getUpdates` long-poll timeout. Range 1..50 (Telegram server cap). |
| `TELEGRAM_HTTP_TIMEOUT_SECONDS`   | Runtime     | `35`                 | Outer HTTP timeout for the Telegram listener; should sit slightly above the long-poll value. Range 5..120. |
| `TELEGRAM_DESTRUCTIVE_COOLDOWN_SECONDS` | Runtime | `30`                 | Per-(sender, command) cooldown after a Telegram destructive verb fires. Range 1..600. |
| `TELEGRAM_AI_CALLS_PER_MINUTE`    | Runtime     | `6`                  | Per-Telegram-user rate limit for AI palette calls (rolling 60s bucket per user_id). Range 1..120. |
| `TELEGRAM_BULK_UPDATE_CONCURRENCY`| Runtime     | `4`                  | `/update all` fan-out concurrency cap. Default 4 — sequential is 1, fast multi-node Swarms can raise to 16. Range 1..16. |
| `HOST_BASELINE_RECOMPUTE_INTERVAL_SECONDS` | Runtime | `3600`        | Cadence for the host-baseline drift sampler. Range 60..86400. |
| `HOST_BASELINE_FIRST_TICK_DELAY_SECONDS` | Runtime | `60`             | Delay before the first baseline pass after lifespan start. Range 5..600. |
| `HOST_BASELINE_MIN_SAMPLES`       | Runtime     | `20`                 | Minimum sample count before a metric gets an IQR baseline (drift chip is hidden below this). Range 5..500. |
| `HOST_BASELINE_WINDOW_DAYS`       | Runtime     | `30`                 | Rolling-window lookback (days) for the baseline computation. Range 1..365. |
| `PUBLIC_IP_ENABLED`               | Runtime     | `0`                  | Master gate for the Public-IP lookup module (Admin → Public IP). Default OFF — enabling authorises outbound calls to ifconfig.co. |
| `PUBLIC_IP_CACHE_TTL_SECONDS`     | Runtime     | `600`                | In-process cache TTL for Public-IP lookups. Range 60..3600. |
| `PUBLIC_IP_FETCH_TIMEOUT_SECONDS` | Runtime     | `8`                  | HTTP timeout for the Public-IP fetch against ifconfig.co. Range 2..60. |
| `WEATHER_OPEN_METEO_ENDPOINT`          | Runtime | `""`   | Open-Meteo forecast endpoint URL (operator-pasted; no baked default). Empty = the feature returns a "configure URL" error. |
| `WEATHER_WEATHERAPI_ENDPOINT`          | Runtime | `""`   | WeatherAPI.com base URL (operator-pasted; no baked default). Empty = the feature returns a "configure URL" error. |
| `WEATHER_CACHE_TTL_SECONDS`            | Runtime | `600`  | Weather provider in-process per-coordinate cache TTL. Range 60..86400. |
| `WEATHER_FETCH_TIMEOUT_SECONDS`        | Runtime | `8`    | Weather provider outbound HTTP wall-clock. Range 1..60. |
| `WEATHER_HISTORY_RETENTION_DAYS`       | Runtime | `90`   | Days of weather + moon-phase samples kept in `weather_samples`. 0 disables pruning. Range 0..3650. |
| `WEATHER_SAMPLER_INTERVAL_SECONDS`     | Runtime | `3600` | Lifespan-managed weather sampler cadence. 0 disables the historical-data sampler. Range 0..86400. |
| `ASSET_INVENTORY_TOKEN_TIMEOUT_SECONDS` | Runtime | `10`              | OAuth2 token-handshake timeout for the asset-inventory client. Range 2..120. |
| `ASSET_INVENTORY_FETCH_TIMEOUT_SECONDS` | Runtime | `15`              | Asset-list fetch timeout for the asset-inventory client. Range 2..300. |
| `KICK_GATHER_TIMEOUT_SECONDS`     | Runtime     | `30`                 | Wall-clock cap for the cold-cache `_gather()` kick on drill-down endpoints. Range 5..300. |
| `GATHER_CLIENT_TIMEOUT_SECONDS`   | Runtime     | `60`                 | Inner httpx client wall-clock for every Portainer call inside one `_gather()` cycle. Range 5..600. |
| `GATHER_ORPHAN_PROBE_TIMEOUT_SECONDS` | Runtime | `3`                  | Per-orphan-task inspect timeout during gather. Short by design. Range 1..30. |
| `OIDC_HTTP_TIMEOUT_SECONDS`       | Runtime     | `15`                 | OIDC discovery + token-exchange HTTP timeout. Range 2..120. |
| `LOAD_BUSY_MAX_SECONDS`           | Runtime     | `30`                 | SPA row-level busy-state cap — bounds how long the Update button stays disabled on a stuck op. Range 5..600. |
| `PORTAINER_OP_TIMEOUT_SHORT_SECONDS` | Runtime  | `120`                | Portainer write-op wall-clock — short tier (container restart / remove). Range 10..600. |
| `PORTAINER_OP_TIMEOUT_MEDIUM_SECONDS` | Runtime | `300`                | Portainer write-op wall-clock — medium tier (service-level + prune). Range 30..1800. |
| `PORTAINER_OP_TIMEOUT_LONG_SECONDS` | Runtime   | `600`                | Portainer write-op wall-clock — long tier (stack update + image-pull-heavy paths). Range 60..3600. |
| `HOST_SNAPSHOT_STALE_FIELD_MAX_AGE_HOURS` | Runtime | `24`            | Per-field stale grace cap (hours) before snapshot-restored fields decay out of both the merged dict and the next saved snapshot. Range 1..720. |
| `SSH_WS_HEARTBEAT_SECONDS`        | Runtime     | `25`                 | SSH terminal WebSocket server-ping cadence (seconds). Keeps the WSS connection alive past upstream proxy idle timers. Range 5..120. |
| `SSH_TERMINAL_CONNECT_TIMEOUT_SECONDS` | Runtime | `20`                 | `asyncssh.connect` wall-clock — TCP open + key exchange. Range 5..120. |
| `SSH_TERMINAL_LOGIN_TIMEOUT_SECONDS`   | Runtime | `20`                 | Per-session auth + channel-open wall-clock. Range 5..120. |
| `SSH_CLOSE_TIMEOUT_SECONDS`       | Runtime     | `5`                  | Server-side SSH close wall-clock so a slow disconnect can't pin a worker. Range 1..60. |
| `SSH_DEFAULT_PORT`                | Runtime     | `22`                 | Per-host SSH destination port when no per-host or global override is set. Range 1..65535. |
| `SNMP_DEFAULT_PORT`               | Runtime     | `161`                | Per-host SNMP destination port when no per-host or global override is set. Range 1..65535. |
| `PING_DEFAULT_PORT`               | Runtime     | `443`                | Per-host Ping destination port (TCP-connect probe) when no per-host or global override is set. Range 1..65535. |
| `PING_PACKET_INTERVAL_MS`         | Runtime     | `200`                | Inter-probe gap (ms) within one ping RTT measurement. Range 100..2000. |
| `DOCKERHUB_USER`                  | Optional    | unset                | Docker Hub auth (avoid anonymous rate limits).                                  |
| `DOCKERHUB_TOKEN`                 | Optional    | unset                | Paired with `DOCKERHUB_USER`.                                                   |
| `SESSION_SECRET`                  | Auth        | auto-generated       | HMAC key for session cookies. Set explicitly in prod.                           |
| `SESSION_LAST_SEEN_THROTTLE_SECONDS` | Auth     | `60`                 | Min seconds between per-session `last_seen_at` writes (Admin → Sessions). Throttles a per-request DB write that serialized polls against samplers. `0` = every request. Env-only. |
| `BOOTSTRAP_ADMIN_USER`            | First-boot  | unset                | First-boot-only admin seed.                                                     |
| `BOOTSTRAP_ADMIN_PASSWORD`        | First-boot  | unset                | First-boot-only admin seed.                                                     |

No env vars exist for OIDC, Beszel, Pulse, node-exporter, Apprise, schedules, or the curated
hosts list. All of those are UI-managed and stored in the `settings` table.
