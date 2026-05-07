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
new env var to `main.py` or the `logic/` modules, add it here too so operators and future-you
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
```

## Runtime tuning (process-level)

> **Live override available.** Every tunable below has a matching DB
> setting (`tuning_<lowercase_env_var>`). When set from Admin → Config
> the DB value wins; blank/unset falls back to the env var shown here,
> which falls back to the code default. UI changes take effect on the
> next consumer read (per-request for TTLs, per-tick for samplers — one
> tick lag). The authoritative list of tunables lives in
> `logic/tuning.py:TUNABLES`. **Strict rule:** every operator-tunable
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
# the operator resumes via POST /api/hosts/{id}/resume-sampling.
HOST_PERMANENT_FAIL_WINDOW_SECONDS=900

# Frontend /api/ops poll cadence in SECONDS (renamed from
# OPS_POLL_INTERVAL_MS in #514 for operator-friendly UI). Backend
# multiplies × 1000 before delivering to the SPA's setTimeout via
# /api/me's `client_config.ops_poll_ms`, so the consumer contract is
# unchanged.
OPS_POLL_INTERVAL_SECONDS=2

# Persistent-log retention in days. Daily files under /app/data/logs/
# older than this are deleted by an hourly sweep.
LOG_RETENTION_DAYS=7

# In-app notifications retention (days). The `prune_notifications`
# schedule kind sweeps rows from the `notifications` table older than
# this. Default 90 — operators usually want a longer trail than logs
# (7d) so quarterly review of "what happened" is possible without
# exporting to an external store.
NOTIFICATION_RETENTION_DAYS=90

# Host-snapshots read-side cache TTL (seconds). The SPA fans out N
# parallel /api/hosts/one/{id} per refresh; caching the snapshot-table
# read collapses N reads into 1. Set 0 to disable.
HOST_SNAPSHOTS_CACHE_TTL_SECONDS=5

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

# Ping host-stats provider knobs. The first three control the
# lifespan-managed sampler that writes ping_samples; the cool-down
# throttles probes against an unreachable host.
PING_INTERVAL_SECONDS=60
PING_CONCURRENCY=16
PING_PROBE_TIMEOUT_SECONDS=2
PING_COOLDOWN_SECONDS=300

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
# are SKIPPED entirely until the operator clicks Resume on the
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
```

## Auth — OIDC SSO (UI-managed, NO env vars)

OIDC provider config is stored in the `settings` table and edited from Admin → Authentik
OIDC. There are intentionally no `OIDC_*` env vars — the UI is the only source of truth. See
`docs/guidelines/authentik.md` for the Authentik-side setup walkthrough.

## Host-stats providers (UI-managed, NO env vars)

All six providers' credentials live in the `settings` table and are edited from
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

Per-provider chip-colour customisation lives in
`provider_color_{beszel, pulse, node_exporter, webmin, ping, snmp}` settings (`#RRGGBB` or blank
for default).

## Other UI-managed settings (NO env vars)

- **Weather proxy** (`open_meteo_url`) — edited from Admin → General. Blank = weather
  widget reports `configured: false`; there is NO fallback to `api.open-meteo.com` anymore.
- **Apprise** (`apprise_url`, `apprise_tag`, `portainer_public_url`). Edited from
  Admin → Notifications.
- **Per-event notification toggles** — 14 op events
  (`notify_event_{stack_update, container_update, container_restart, container_remove,
  service_restart, swarm_agent_restart, prune}_{success, failure}`) plus two security events
  (`notify_event_user_login`, default OFF; `notify_event_host_paused`, default ON). Admin →
  Notifications hosts the global gates AND the per-medium master toggles
  (`notify_medium_app` / `notify_medium_apprise`, both default ON); Settings → Notifications
  hosts the per-user opt-in/out (stored in `users.ui_prefs.notify_events`). Two-layer
  scoping: admin gate + medium gate first, then per-user.
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
| `CACHE_TTL_SECONDS`               | Runtime     | `900`                | Items cache TTL.                                                                |
| `STATS_CACHE_TTL_SECONDS`         | Runtime     | `30`                 | Stats cache TTL.                                                                |
| `REGISTRY_CONCURRENCY`            | Runtime     | `8`                  | Parallel remote-digest fetches.                                                 |
| `STATS_CONCURRENCY`               | Runtime     | `16`                 | Parallel `/stats` calls.                                                        |
| `STATS_HISTORY_DAYS`              | Runtime     | `7`                  | Retention window for `stats_samples`.                                           |
| `STATS_SAMPLE_INTERVAL_SECONDS`   | Runtime     | `300`                | Sampler cadence.                                                                |
| `STATS_TARGETED_TIMEOUT_SECONDS`  | Runtime     | `12`                 | Per-container `/stats` timeout WITH `X-PortainerAgent-Target`. Range 1..60.     |
| `STATS_UNTARGETED_TIMEOUT_SECONDS`| Runtime     | `10`                 | Per-container `/stats` timeout for the manager-local fallback path. Range 1..60. |
| `SWARM_AGENT_UNHEALTHY_THRESHOLD` | Runtime     | `3`                  | Consecutive failed gather cycles before the unhealthy banner fires. Range 1..20. |
| `HOST_PERMANENT_FAIL_WINDOW_SECONDS` | Runtime  | `900`                | host_metrics_sampler auto-pause window.                                          |
| `OPS_POLL_INTERVAL_SECONDS`       | Runtime     | `2`                  | SPA's /api/ops poll cadence in seconds; multiplied × 1000 before delivery via `client_config.ops_poll_ms`. Renamed from the legacy `OPS_POLL_INTERVAL_MS` for operator-friendly admin UI. |
| `LOG_RETENTION_DAYS`              | Runtime     | `7`                  | Persistent-log retention.                                                        |
| `NOTIFICATION_RETENTION_DAYS`     | Runtime     | `90`                 | In-app notifications retention. Drives the `prune_notifications` schedule kind. |
| `HOST_SNAPSHOTS_CACHE_TTL_SECONDS` | Runtime    | `5`                  | host_snapshots read-cache TTL.                                                   |
| `HOSTS_PARALLEL_FETCH`            | Runtime     | `6`                  | SPA fan-out concurrency cap on `/api/hosts/one/{id}`.                            |
| `AI_SIDEBAR_WIDTH_PX`             | Runtime     | `480`                | AI Assistant sidebar drawer width in pixels (320..720). Mobile ignores.          |
| `PING_INTERVAL_SECONDS`           | Runtime     | `60`                 | Ping sampler tick cadence.                                                       |
| `PING_CONCURRENCY`                | Runtime     | `16`                 | Ping sampler fan-out.                                                            |
| `PING_PROBE_TIMEOUT_SECONDS`      | Runtime     | `2`                  | Per-probe timeout.                                                               |
| `PING_COOLDOWN_SECONDS`           | Runtime     | `300`                | Per-(host, port) cool-down on consecutive ping failures.                         |
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
| `HOST_METRICS_PROBE_CONCURRENCY`  | Runtime     | `8`                  | host_metrics_sampler per-tick NE probe fan-out.                                  |
| `AUTH_FAILURE_COOLDOWN_SECONDS`   | Runtime     | `300`                | Shared Webmin + SSH auth-failure cool-down.                                      |
| `RATE_LIMIT_MAX_FAILURES`         | Runtime     | `5`                  | Login rate-limit max fails.                                                      |
| `RATE_LIMIT_WINDOW_SECONDS`       | Runtime     | `900`                | Login rate-limit sliding window.                                                 |
| `RATE_LIMIT_LOCKOUT_SECONDS`      | Runtime     | `900`                | Login rate-limit lockout duration.                                               |
| `STAT_BAR_WARN_PCT`               | Runtime     | `60`                 | Stat-bar amber-threshold percentage (Hosts view CPU/Mem/Disk bars). Range 30..90; must be ≤ `STAT_BAR_CRIT_PCT`. |
| `STAT_BAR_CRIT_PCT`               | Runtime     | `85`                 | Stat-bar red-threshold percentage. Range 50..99.                                 |
| `DOCKERHUB_USER`                  | Optional    | unset                | Docker Hub auth (avoid anonymous rate limits).                                  |
| `DOCKERHUB_TOKEN`                 | Optional    | unset                | Paired with `DOCKERHUB_USER`.                                                   |
| `SESSION_SECRET`                  | Auth        | auto-generated       | HMAC key for session cookies. Set explicitly in prod.                           |
| `BOOTSTRAP_ADMIN_USER`            | First-boot  | unset                | First-boot-only admin seed.                                                     |
| `BOOTSTRAP_ADMIN_PASSWORD`        | First-boot  | unset                | First-boot-only admin seed.                                                     |

No env vars exist for OIDC, Beszel, Pulse, node-exporter, Apprise, schedules, or the curated
hosts list. All of those are UI-managed and stored in the `settings` table.
