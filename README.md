<!-- HTML5 deprecated the `align` attribute; the bare image renders
     fine without centering on every git host. -->
<!--suppress CheckImageSize -->
<img src="static/img/logo/omnigrid.svg" alt="OmniGrid logo" width="96" />

# OmniGrid

> Single-replica FastAPI + Alpine.js dashboard for Docker Swarm clusters **and the bare hosts that run them**. Portainer-native stack / service / container updates, multi-provider host telemetry (Beszel / Pulse / node-exporter / Webmin / Ping / SNMP / HTTP probe / per-service reachability), interactive SSH + audited one-shot runner, scheduler, OIDC + TOTP + passkey auth, Apprise + Telegram notifications.

A Portainer-native operations dashboard for Docker Swarm clusters **and the bare hosts that run them**. One screen, four core capabilities:

- **Updates** — scan every Swarm service, compare against remote registry digests (Docker Hub / GHCR / lscr.io / any v2 registry), one-click stack update, container recreate, service restart, orphan-task cleanup. All via the Portainer REST API — no direct Docker socket.
- **Host telemetry** — live CPU / Memory / Disk / Disk I/O / Network / Load / Bandwidth time-series per curated host, sourced from any combination of Beszel, Pulse, node-exporter, Webmin, Ping (TCP/ICMP reachability + RTT), SNMP (managed switches / routers / UPSes), HTTP probe (per-URL TCP / TLS-cert / DNS health checks), and per-service reachability. Cross-provider fallback + per-host snapshots so a flaky agent doesn't blank the chart.
- **Operations** — interactive xterm.js SSH terminal, admin-audited one-shot SSH runner with destructive-pattern guard, cron-like scheduled jobs (cache refresh / docker prune / SQLite + avatars backup / asset-inventory refresh), Apprise notifications, full audit log of every action.
- **Auth** — local accounts + API tokens, optional Authentik OIDC SSO, TOTP + WebAuthn passkey 2FA, two roles (admin / read-only), CSRF-hardened, rate-limited login, session revocation, self-service password change.

Built as a friendlier replacement for Diun Dash plus the tab-jumping between Portainer / Beszel / Grafana / SSH that homelab clusters tend to grow. Diun only **observes**; OmniGrid **acts**.

📋 **Releases & changelog:** see [`CHANGELOG.md`](CHANGELOG.md) for the full per-version release notes (Keep a Changelog format). Per-version links jump to the matching milestone. The release cadence (PATCH on every deploy, periodic manually-cut MINORs) is documented in [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md).

<!-- Screenshots live under `docs/screenshots/` — see the gallery below
     for the full set. The hero shot is the Nodes view (Stacks grouped
     by host node + live HOST CPU/MEM/DISK bars). The bare <img>
     renders without HTML5-deprecated `align` centering. -->
<img src="docs/screenshots/nodes-view.png" alt="OmniGrid Nodes view — stacks grouped by host with live CPU / Memory / Disk bars" width="960" />

## Features

### Cluster updates & operations

- **Seven views**: Stacks (grouped, default) · Services (flat sortable) · Nodes (per-host swarm grouping with HOST stats) · Hosts (curated inventory + telemetry) · Apps (per-host pinned services + catalog) · History (audit log) · Timeline (cross-host event timeline) — plus the **Stats** top-nav for cluster-wide dashboards (Overview / Database / Samples / Network / Incidents / AI Cost).
- **Digest-level update detection** — compares your running `image@sha256:...` against the remote manifest. Supports Docker Hub, GHCR, lscr.io, and any v2 registry. Token-cached www-authenticate dance for private registries.
- **Click-to-act** — Update Stack (prune+repull+redeploy), Recreate container, Restart service (no pull), Remove offline / orphan containers, all via the Portainer REST API.
- **Bulk operations** — checkbox multi-select; dedupes by stack so one stack = one update call.
- **Live operations panel** — streaming per-op event log floats bottom-right; auto-routes container ops to the correct Swarm worker via `X-PortainerAgent-Target`.
- **Ignore list** — pin certain images or stacks to skip (e.g. pinned `:v1.2.3` tags you don't want bumped).
- **Schedules** — cron-like recurring jobs (cache refresh / `docker system prune` per-node or fan-out / SQLite+avatars backup / asset-inventory refresh) with skip-if-running guards and audited history.

### Host telemetry & inventory

- **Curated host list** — admin-defined inventory under Admin → Hosts; each row maps to one or more provider-specific identifiers PLUS a dedicated `address` field that's the canonical provider-independent probe target (port-scan, ping, SNMP, SSH all resolve to it before falling through to provider-specific aliases).
- **Eight monitoring providers** (any combination): Beszel Hub (Pocketbase), Pulse (Proxmox), Prometheus node-exporter (Linux + FreeBSD), Webmin / Miniserv, Ping (TCP-connect or ICMP echo for reachability + RTT), SNMP (v2c / v3 USM for managed switches / routers / UPSes / printers), HTTP probe (per-URL TCP + TLS-cert-expiry + DNS resolution), and a per-service reachability probe (one chip per curated `services[]` entry with `probe.enabled=true`). Cross-provider fallback merges stats with a "most-specific wins" rule + per-host snapshots so a flaky agent doesn't blank the chart. Per-provider chip colour customisable in Settings → Providers.
- **Time-series charts** — CPU / Memory / Disk usage / Disk I/O (Linux + FreeBSD `node_devstat_*`) / Network In/Out / Bandwidth / Load 1m/5m/15m (rendered as % of cores) / Swap / Temperature (per-sensor lines from `stats.t`) / GPU Power / GPU Usage / GPU VRAM (NVIDIA / AMD via Beszel `stats.g`), with 1h / 6h / 24h / 7d range picker, dynamic unit chips that lock to one family (legend + Y-axis + chip stay aligned across magnitudes), permanently-flat charts auto-hide after a 1 h soak, and a live "Updated Xs ago" freshness label.
- **Switch / managed-gear telemetry** (SNMP) — total throughput line chart, per-port throughput multi-line chart (top 5 by current rate, solid in / dashed out), per-port utilization line chart (% of link capacity from `ifHighSpeed`), uptime trend with reboot detection, hardware inventory rows from `entPhysicalTable` (model / serial / firmware), printer toner / ink supplies + lifetime page count headline + console message via Printer-MIB.
- **Host drawer detail** — hardware (vendor / model / serial / OS / kernel / arch), network interfaces, mounted filesystems, package-update count, systemd service status, optional asset-inventory join (model / serial / location from a third-party asset API).
- **Host groups** — admin-assigned `custom_number` ranges bucket curated hosts into collapsible sections (e.g. "Gateways 1-4", "VMs 100-199").

### Operations & access

- **Interactive SSH terminal** — admin-only xterm.js modal over WSS to a backend asyncssh PTY. PTY-forced (so sudo doesn't silently no-op), full audit row per session.
- **One-shot SSH runner** — admin-audited dry-run-by-default runner with destructive-pattern guard (typed-hostname confirm for `rm` / `dd` / `reboot` / etc.) and per-(host, user) 5-min cool-down on auth failure.
- **Port scanner** (TCP + optional UDP companion) — on-demand from the host drawer OR scheduled via the `port_scan_refresh` kind. Runs as a fire-and-forget asyncio task so reverse-proxy `proxy_read_timeout` settings don't trip on long scans; emits `port_scan:completed` over SSE; per-port detail + banner-grab persists to `host_port_scans`. Per-host opt-in via `hosts_config[].port_scan = {enabled}`.
- **Apps view + service catalog** — admin-pinned services on each curated host, paired with a built-in catalog of templates (AdGuard Home, Plex, Sonarr, Authentik, …). Discovery wizard matches a host's open-port set against catalog templates and proposes pins; per-instance Probe-now, Logs (Portainer-routed for containerised apps), and Restart/Update (when linked to a Portainer container or stack). Aggregate `/api/apps` + flat `/api/apps/instances` + portable catalog JSON export / import.
- **Telegram bot** — outbound notifications as a third medium alongside in-app + Apprise, plus inbound long-poll for `/help` / `/hosts` / `/host` / `/restart` (alias `/reboot`) / `/cleanup` / `/update` / `/link` / `/whoami` / `/weather` / `/moon` / `/time` / `/version` / `/ip`, free-form AI chat in authorised chats. Destructive commands gate on a typed-confirm two-step. Account linking via `POST /api/me/telegram-link-code` + the bot's `/link <code>` command. See [`docs/guidelines/telegram.md`](docs/guidelines/telegram.md).
- **AI assistant** — multi-provider (Claude / Gemini / ChatGPT / DeepSeek) palette + multi-turn sidebar with persistent chat history, inline charts (`memory_history` / `cpu_history` / `disk_projection`), per-deployment memory store (`MEMORY:` / `MEMORY-FORGET:` directives), structured `ACTION:` directives the SPA dispatches inline, fallback chain on transient overload, retry-once-on-429/502/503/504 gate, per-call cost / latency / token-usage dashboard, log-context window (default 7 days of error+warn lines, secret-redacted before injection). Admin-only.
- **Auto-fix action buttons in drawers** — when a Swarm task error matches a known pattern (VXLAN sandbox-join, image-pull failure, etc.), the drawer surfaces one-click "Auto-fix" actions (Portainer-API-only when possible, falling back to SSH-with-pre-loaded-command). Destructive actions gate on a SweetAlert confirm + spinner overlay.
- **Bulk host actions** — pause / resume sampling, apply SNMP vendor whitelist, apply per-host SNMP tunables across N hosts in one POST. Each affected host fires its usual SSE event so other tabs catch up within one frame.
- **Audit log** — every operation (updates, restarts, ssh runs, schedule fires, backups, AI calls, port scans) persisted to SQLite with full event log. Filterable + CSV / JSON export. Timeline tab gives a unified per-host event view (state changes + sampler errors + bulk-action audit rows).
- **Backups** — DB + avatars snapshot zips via SQLite's online `.backup()` API. Browseable + restorable from the Admin → Backups page. Tunable retention via `tuning_backup_retention_count` (0 = keep all; 7-30 typical).
- **Notifications (in-app + Apprise)** — every write op + scheduled-job completion fires through TWO mediums in parallel: an SQLite-backed in-app store (Notifications popup behind the user-avatar dropdown, severity / event / unread filters, mark-read, retention via the `prune_notifications` schedule kind) and the existing Apprise webhook fan-out. Per-medium master toggles + per-event admin gates + per-user opt-in/out. Admin-only template editor for per-event title + body overrides with curated `{name}` / `{type}` / `{actor}` / `{host}` / `{time}` / `{error}` / `{status}` placeholder whitelist.
- **Prometheus `/metrics`** — gather stats, op counts, cache age, host-stats provider health.

### Auth & UX

- **Local accounts** — username / password with bcrypt hashes, sliding 8h sessions, server-side revocation, rate-limited login (5 fails / 15 min / IP).
- **TOTP (2FA)** for local accounts — `pyotp` + Fernet-encrypted secrets at rest, QR enrolment, 10 single-use backup codes, admin-side master toggle + per-role required + per-user force flag, configurable failure lockout. Authentik users skip every TOTP path (Authentik handles MFA upstream).
- **API tokens** — admin-issued opaque tokens (SHA-256 at rest, raw token surfaced once on create) for machine clients. Tokens carry their own role; bearer-auth bypasses CSRF.
- **Authentik OIDC SSO** — Authorization-Code + PKCE flow, JWKS validation, group-based admin promotion, fully DB-backed config (no env vars).
- **Two roles**: `admin` (all ops) · `readonly` (reads only). Write routes enforce server-side; UI hides write buttons for read-only users.
- **CSRF** double-submit cookie, automatic on every cookie-authed write request.
- **Self-service** — change password, manage TOTP enrolment + backup codes, revoke own sessions, manage avatar / display name / email / bio, opt in/out of individual notification events.
- **Polish**: dark + light theme, English + Arabic with RTL support (more languages: drop a JSON in `static/i18n/`), global search (`/`), keyboard shortcuts (`?` for the cheat sheet), per-user view persistence.

### Deploy story

- **No Docker socket** — every Docker call goes through Portainer's REST API.
- **Image-build deploy** — CI pipeline rsyncs the build context (Dockerfile + source + `node_modules/`) to the Swarm manager, builds an `omnigrid:<version>` image there, pushes to a container registry, and force-updates the Swarm service onto the new tag. Each version is pinned in Swarm's task spec so manual rollback has a discrete tag to point at.
- **Self-healing** — Swarm `update_config: start-first, failure_action: rollback, monitor: 30s` so a failed deploy auto-rolls back (the same template OmniGrid recommends for services it manages).

## Architecture

```
┌───────────────┐       ┌──────────────┐       ┌──────────────┐
│   Browser     │──────▶│   OmniGrid   │──────▶│  Portainer   │
│ (Alpine+Tail) │  REST │   (FastAPI)  │  REST │   (Swarm)    │
└───────────────┘       └──────┬───────┘       └──────────────┘
                               │
                               │ HEAD /v2/*/manifests/<tag>
                               ▼
                    ┌──────────────────────┐
                    │  Docker registries   │
                    │ (hub, ghcr, lscr, …) │
                    └──────────────────────┘
```

- **`main.py`** — FastAPI backend (routes + lifespan + orchestration). Aggregates data from Portainer (services, tasks, nodes, stacks, containers), resolves remote digests in parallel, runs background update + prune + restart jobs, fires the in-app notification store + Apprise webhooks.
- **`logic/`** — modular business logic: `gather`, `stats`, `ops`, `auth`, `oidc`, `registry`, `portainer`, `beszel`, `pulse`, `node_exporter`, `webmin`, `ping` / `ping_sampler`, `snmp`, `http_probe` / `host_http_sampler`, `service_sampler`, `host_metrics_sampler`, `host_net_sampler`, `host_baseline` / `host_baseline_sampler`, `schedules`, `backups`, `asset_inventory`, `events` (SSE bus), `tuning` (TUNABLES + 3-tier resolver), `settings_keys` / `env_keys` (typed key registries), `merge`, `cooldown`, `migrations`, `webauthn_helper`, `totp`, `telegram_listener`, `notify_telegram`, `public_ip`, `config_export`, `datetime_fmt`, `i18n`.
- **`static/index.html` + `static/js/app.js` + `static/css/style.css`** — single-page Alpine.js + Tailwind UI; no build step.
- **`/opt/omnigrid/data/omnigrid.db`** — SQLite. Holds history, ignores, settings, users, sessions, API tokens, WebAuthn credentials, schedules, in-app notifications, host snapshots, per-(provider, host) failure state, and the time-series tables (`stats_samples`, `host_metrics_samples`, `host_net_samples`, `host_snmp_samples`, `host_snmp_iface_samples`, `host_snmp_temp_samples`, `ping_samples`).

## Deploy

The canonical production deploy is the CI pipeline — push to `main`, the runner rsyncs the build context to the Swarm manager, builds the `omnigrid:<version>` image there, pushes to the configured registry, and force-updates the running stack. Full deploy runbook (runner setup, deploy-key rotation, registry credentials, manual rollback) lives in [`docs/guidelines/deploy.md`](docs/guidelines/deploy.md).

### Pull a pre-built image (no build step)

Pre-built multi-platform images are published to a public container registry at `ghcr.io/maraouf/omnigrid` on every MINOR release (cut-day `v<MAJOR>.<MINOR>.0` tag — daily auto-PATCH builds stay in the maintainer-private registry). The package is public — `docker pull` works anonymously, no token needed:

```bash
docker pull ghcr.io/maraouf/omnigrid:latest          # newest minor
docker pull ghcr.io/maraouf/omnigrid:1.4             # newest patch on the 1.4 line
docker pull ghcr.io/maraouf/omnigrid:1.4.0           # exact, immutable
```

Tag layout: `latest` floats to the newest minor we've shipped; `<MAJOR>.<MINOR>` floats to the newest minor on that major line; `<MAJOR>.<MINOR>.0` is the immutable cut-day MINOR tag (use this for rollbacks). Only `v<MAJOR>.<MINOR>.0` cut-day tags are published — daily auto-PATCH builds (`.1`, `.2`, …) stay in the maintainer-private registry and on the Swarm manager itself; cutting a MINOR is what publishes a new public tag.

For a Swarm `docker-compose.yml`, set `OMNIGRID_IMAGE=ghcr.io/maraouf/omnigrid:latest` (or pin a specific tag) before `docker stack deploy` and the compose substitution picks it up. See the [GHCR section in the deploy runbook](docs/guidelines/deploy.md#pre-built-images-on-ghcr) for `--with-registry-auth` details and the publish-trigger contract.

### Manual stand-up (build locally)

For a one-off / manual stand-up:

**1. Prep the host** (on the Swarm manager node):

```bash
sudo mkdir -p /opt/omnigrid/app /opt/omnigrid/data
sudo chown -R $USER:$USER /opt/omnigrid
```

**2. Copy the build context** (Dockerfile, `main.py`, `logic/`, `static/`, `node_modules/`, `requirements.txt`, `docker-compose.yml`, `.env`) to `/opt/omnigrid/app/` on the manager. CI does this via rsync; manually you can `scp -r` the working tree.

**3. Create a Portainer API key**:
Portainer UI → profile menu → *My account* → *Access tokens* → add a new token. Give it admin scope (it needs to update any stack). Drop it into `/opt/omnigrid/app/.env` as `PORTAINER_API_KEY` (or paste it into Admin → Portainer after first login).

**4. Build and deploy the stack** on the manager:

```bash
cd /opt/omnigrid/app
docker build --build-arg VERSION=1.0.0 -t omnigrid:1.0.0 -t omnigrid:latest .
docker stack deploy --resolve-image=always --compose-file docker-compose.yml omnigrid
```

The `OMNIGRID_IMAGE` env var in the compose file resolves to the registry path in CI deploys, or falls back to the local `omnigrid:latest` tag for manual builds.

**5. Point a reverse proxy at it (optional)**:
Any HTTPS-terminating proxy works — Nginx Proxy Manager, Traefik, Caddy, plain Nginx, etc. Forward `omnigrid.example.com` (or whatever hostname you publish under) → `http://<manager>:9500`. OmniGrid has its own local login + optional Authentik OIDC SSO, so the proxy doesn't need to do auth — just terminate TLS and forward. See [`docs/guidelines/authentik.md`](docs/guidelines/authentik.md) to wire up OIDC.

**6. Open it up**, hit ⚙️ Settings, configure:

- Apprise URL: e.g. `http://apprise.example.com:8005/notify/OmniGrid` (or with a tag)
- Portainer public URL: e.g. `https://portainer.example.com` (for the "Open in Portainer" deep links)

## How updates work

| Item type                                       | What happens on click                                                                                                                                                                                        |
|-------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Service in a Portainer stack                    | `PUT /api/stacks/{id}?endpointId={eid}` with `{Prune:true, PullImage:true}` — identical to Portainer UI's "Update the stack + re-pull + prune"                                                               |
| Standalone compose container                    | `POST /api/docker/{eid}/containers/{id}/recreate?PullImage=true`                                                                                                                                             |
| Swarm service without a Portainer-managed stack | Update button disabled. Use Restart (ForceUpdate bump) or redeploy via CLI.                                                                                                                                  |
| Restart action (drawer)                         | Bumps `TaskTemplate.ForceUpdate` and calls `POST /services/{id}/update` — same image, fresh tasks                                                                                                            |
| Per-node prune (Hosts / Schedules)              | `docker system prune` on the named Swarm node via Portainer's task-routing — cleans dangling images / stopped containers / unused networks                                                                   |
| Swarm-agent restart (banner action)             | When the unhealthy banner fires (≥ N consecutive cycles of zero stats responses for a node's tasks), one click bumps the Portainer-agent service's `TaskTemplate.ForceUpdate` to roll the failing agent task |

## Environment variables

| Var | Default | Notes |
|-----|---------|-------|

**A fresh deploy can boot with NO env vars set** — bootstrap the first admin via `POST /api/local-auth/bootstrap`, then configure Portainer / OIDC / monitoring providers from the Settings UI. Everything below is either a first-boot seed (one-shot, ignored after the DB row exists) or a process-level tunable you can also override from Admin → Config.

| Var                                                 | Default                 | Notes                                                                                                                                                   |
|-----------------------------------------------------|-------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
| `PORTAINER_URL`                                     | —                       | **Optional bootstrap.** Seeded into the DB on first boot; `Admin → Portainer` is authoritative after that.                                              |
| `PORTAINER_API_KEY`                                 | —                       | **Optional bootstrap** — same as above. Starts with `ptr_`.                                                                                             |
| `PORTAINER_ENDPOINT_ID`                             | `1`                     | **Optional bootstrap** — the Swarm endpoint id.                                                                                                         |
| `VERIFY_TLS`                                        | `true`                  | **Optional bootstrap** — stored as `portainer_verify_tls` in the DB after seeding.                                                                      |
| `DB_PATH`                                           | `/app/data/omnigrid.db` | SQLite location.                                                                                                                                        |
| `DB_TYPE`                                           | `sqlite`                | DB backend. Currently `sqlite` only — scaffolding for future Postgres / MariaDB / Mongo backends.                                                       |
| `SESSION_SECRET`                                    | auto-generated          | HMAC key for session cookies. **Set explicitly in prod** — auto-generated means sessions die on every restart.                                          |
| `BOOTSTRAP_ADMIN_USER` / `BOOTSTRAP_ADMIN_PASSWORD` | —                       | First-boot-only admin seed. Consulted when the users table is empty; ignored after that. Blank both values in a follow-up commit once you've logged in. |
| `ENV_FILE_PATH`                                     | `/app/.env`             | Where `python-dotenv` looks for env values at startup.                                                                                                  |
| `DOCKERHUB_USER` / `DOCKERHUB_TOKEN`                | —                       | Optional. Bypass anonymous Docker Hub rate limits.                                                                                                      |

**Process-level tunables** (DB > env > default — live UI override at `Admin → Config`):

| Var                                                                   | Default     | Notes                                                                                                                                                                                                      |
|-----------------------------------------------------------------------|-------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `CACHE_TTL_SECONDS`                                                   | `900`       | Items cache TTL (full registry-digest refresh interval).                                                                                                                                                   |
| `STATS_CACHE_TTL_SECONDS`                                             | `30`        | Per-container stats cache TTL — fresh polling without forcing a full digest re-fetch.                                                                                                                      |
| `REGISTRY_CONCURRENCY`                                                | `8`         | Parallel remote-digest fetches.                                                                                                                                                                            |
| `STATS_CONCURRENCY`                                                   | `16`        | Parallel `/containers/{id}/stats` calls.                                                                                                                                                                   |
| `STATS_HISTORY_DAYS`                                                  | `7`         | Retention window for the time-series tables (`stats_samples` / `host_metrics_samples` / `host_net_samples` / `host_snmp_samples` / `host_snmp_iface_samples` / `host_snmp_temp_samples` / `ping_samples`). |
| `STATS_SAMPLE_INTERVAL_SECONDS`                                       | `300`       | How often the lifespan samplers snapshot into the time-series tables.                                                                                                                                      |
| `STATS_TARGETED_TIMEOUT_SECONDS` / `STATS_UNTARGETED_TIMEOUT_SECONDS` | `12` / `10` | Per-container `/stats` HTTP timeouts (Portainer-agent-targeted vs untargeted fallback). Bumped from a hardcoded 4 s to fix worker-node stats coming back empty under busy hubs.                            |
| `SWARM_AGENT_UNHEALTHY_THRESHOLD`                                     | `3`         | Consecutive failed gather cycles before the unhealthy-Swarm-agent banner fires above the Stacks / Services / Nodes views. Range 1–20.                                                                      |
| `SNMP_SAMPLE_INTERVAL_SECONDS`                                        | `0`         | SNMP-specific sample interval. `0` inherits the global `STATS_SAMPLE_INTERVAL_SECONDS`; any value `30..3600` overrides for SNMP probes only (printers can poll hourly while switches poll every minute).   |
| `SNMP_WALL_CLOCK_BUDGET_SECONDS` / `SNMP_PER_HOST_WALK_CONCURRENCY`   | `60` / `1`  | SNMP probe wall-clock budget (the ~60-OID fan-out lives under this) and per-host walk concurrency (default 1 = serialised; raise to 8–16 for fast snmpd's).                                                |
| `SNMP_HOST_CACHE_TTL_SECONDS` / `SNMP_HOST_FAIL_CACHE_TTL_SECONDS`    | `30` / `5`  | Per-host SNMP success / failure probe cache TTLs. Distinct from the Webmin pair so a Webmin tweak can't silently re-tune SNMP.                                                                             |
| `SNMP_UNREACHABLE_COOLDOWN_SECONDS`                                   | `300`       | SNMP-specific unreachable cool-down. Distinct from `AUTH_FAILURE_COOLDOWN_SECONDS` (no auth challenge to lock out against).                                                                                |
| `STAT_BAR_WARN_PCT` / `STAT_BAR_CRIT_PCT`                             | `60` / `85` | Hosts-view stat-bar amber / red threshold percentages. Edit live from Admin → Config; the SPA reads via `/api/me`'s `client_config.stat_bar_warn_pct`.                                                     |
| `HOST_PERMANENT_FAIL_WINDOW_SECONDS`                                  | `900`       | `host_metrics_sampler` auto-pause window after consecutive probe failures.                                                                                                                                 |
| `OPS_POLL_INTERVAL_SECONDS`                                           | `2`         | SPA `/api/ops` poll cadence in seconds; multiplied × 1000 before delivery via `/api/me`'s `client_config.ops_poll_ms` (renamed from the legacy `OPS_POLL_INTERVAL_MS` for admin-UI friendliness).          |
| `LOG_RETENTION_DAYS`                                                  | `7`         | Persistent-log retention for `/app/data/logs/` (pruned hourly).                                                                                                                                            |
| `NOTIFICATION_RETENTION_DAYS`                                         | `90`        | In-app notifications retention in days. Drives the `prune_notifications` schedule kind.                                                                                                                    |
| `HOST_SNAPSHOTS_CACHE_TTL_SECONDS`                                    | `5`         | Read-side cache TTL on `host_snapshots` to collapse parallel `/api/hosts/one/{id}` reads (set 0 to disable).                                                                                               |
| `HOSTS_PARALLEL_FETCH`                                                | `6`         | Concurrency cap on the SPA's `/api/hosts/one/{id}` fan-out (read on `/api/me` as `client_config.hosts_parallel_fetch`).                                                                                    |

The authoritative table is [`logic/tuning.py:TUNABLES`](logic/tuning.py); the env-var names above are mirrored from there. Every tunable value lives in `TUNABLES` — no hardcoded magic numbers in Python / JS / HTML. Add new knobs there, never as code constants.

OIDC has **no env vars** — every OIDC setting (issuer URL, client ID / secret, redirect URI, scopes, admin group, enable toggle) lives in the DB `settings` table and is edited from `Admin → Authentik OIDC`. See [`docs/guidelines/env_example.md`](docs/guidelines/env_example.md) for the full reference and [`docs/guidelines/authentik.md`](docs/guidelines/authentik.md) for the Authentik-side walkthrough.

## API (if you want to script it)

Every `/api/*` route requires authentication (401 otherwise) — except `/api/healthz`, `/api/version`, and `/metrics`. Two auth modes:

- **Bearer API token** (preferred for scripts): `Authorization: Bearer og_<token>`. Issue from `Admin → API tokens`. Tokens carry their own role (`admin` / `readonly`); bearer requests bypass CSRF.
- **Cookie session** (browser): `og_session` HMAC-signed cookie + `og_csrf` double-submit on every write.

```
# Cluster overview & operations
GET    /api/items                          all services + containers + stacks with status
GET    /api/stats                          live CPU / memory / size per item
GET    /api/stats/history                  per-item time-series (sparklines)
POST   /api/update/stack/{id}              prune+repull+redeploy   → {op_id}
POST   /api/update/container/{id}          recreate w/ pull        → {op_id}
POST   /api/restart/service/{id}           ForceUpdate bump        → {op_id}
POST   /api/restart/container/{id}                                  → {op_id}
POST   /api/remove/container/{id}          delete -fv              → {op_id}
POST   /api/prune/node/{hostname}          docker system prune     → {op_id}
POST   /api/swarm/restart-agent            restart Portainer agent → {op_id}

# Operations panel & history
GET    /api/ops                            list active+recent ops (in-memory, last 50)
GET    /api/ops/{op_id}                    single op + event log
GET    /api/history?limit=100&search=...   persisted completed ops (filterable)
GET    /api/history.json                   bulk JSON export of the same filtered query
GET    /api/history.csv                    bulk CSV export of the same filtered query
DELETE /api/history                        clear history (admin — self-audited)
GET    /api/item/{raw_id}                  single item by full Docker/Swarm id (svc:/ctn:/raw)

# Real-time event stream
GET    /api/events                         SSE stream — see "Real-time events" in docs/guidelines/api.md

# Hosts (curated inventory + telemetry)
GET    /api/hosts                          legacy — composes /list + per-row /one. Accepts ?force=true
GET    /api/hosts/list                     skeleton list (fast, no per-host probes). Accepts ?force=true
GET    /api/hosts/one/{host_id}            single curated host merged with provider data. Accepts ?force=true
GET    /api/hosts/history?system_id=...&host_id=...&hours=...   per-host time-series; system_id (Beszel) OR host_id (NE-only)
GET    /api/hosts/{host_id}/ping/history?hours=...              ping reachability + RTT series
GET / POST                   /api/hosts/config                   list / replace `hosts_config`
GET                          /api/hosts/discover                 probe each provider for available host names
POST                         /api/hosts/test                     per-row validation (provider names + URLs)
POST                         /api/hosts/{host_id}/resume-sampling                clear a host's whole-host auto-pause marker
POST                         /api/hosts/{host_id}/provider/{provider}/resume     clear ONE per-(provider, host) auto-pause marker
POST                         /api/hosts/bulk/pause                bulk pause sampling across host_ids
POST                         /api/hosts/bulk/resume               bulk resume sampling across host_ids
POST                         /api/hosts/bulk/snmp_vendors         apply per-host SNMP vendor whitelist across host_ids
POST                         /api/hosts/bulk/snmp_tunables        apply per-host SNMP tunable overrides across host_ids
GET                          /api/hosts/{host_id}/timeline?hours=N              per-host event timeline (state changes + sampler errors + audit rows)
GET                          /api/hosts/{host_id}/triage                        similar-incident clustering for the failure-history pane
GET                          /api/hosts/debug?id=<host>                         raw provider-payload view for one host (admin)
GET                          /api/hosts/{host_id}/snmp/history?hours=N           per-host SNMP samples (CPU / mem / disk / uptime)
GET                          /api/hosts/{host_id}/snmp/iface_history?hours=N     per-interface throughput (top-5 by rate)
GET                          /api/hosts/{host_id}/snmp/temp_history?hours=N      per-temperature-probe sensors
GET                          /api/hosts/{host_id}/http-probe/history             per-host HTTP probe samples
POST                         /api/hosts/{host_id}/http-probe/test                run one HTTP / TLS / DNS probe synchronously
GET                          /api/hosts/{host_id}/disk-projection?days_ahead=N   linear-regression "days until full" with confidence band
GET                          /api/hosts/{host_id}/triage                         similar-incident grouping for failures
GET                          /api/hosts/{host_id}/timeline?hours=N               unified per-host event timeline
GET                          /api/hosts/{host_id}/beszel/services                per-(host, systemd unit) snapshot from the Beszel agent
POST                         /api/hosts/{host_id}/port-scan                       on-demand TCP / UDP scan → {scan_id, status:"queued"}
GET                          /api/history/port-scan/{scan_id}/ports               per-port detail for one scan

# Auth / users / sessions / tokens / TOTP
POST   /api/local-auth/login               username + password → og_session OR {totp_required, challenge_token}
POST   /api/local-auth/totp                 6-digit TOTP code OR 8-char backup code → og_session
POST   /api/local-auth/totp-setup-confirm   first-login forced-enrol path (combined enrol+verify)
POST   /api/local-auth/logout
POST   /api/local-auth/change-password
POST   /api/local-auth/bootstrap           one-shot first-admin seed
GET    /api/oidc/login                     starts the Authorization-Code+PKCE flow
GET    /api/oidc/callback
GET    /api/me                             current identity + client_config (auth-optional; tunables surfaced for SPA consumers)
GET / PATCH                  /api/me/{ui-prefs,notify-prefs,profile}    self-service profile + per-user notify opt-in/out
GET                          /api/me/totp
POST                         /api/me/totp/{enroll-start,enroll-confirm,regenerate-codes,disable}
GET / POST / PATCH / DELETE  /api/users[/{id}]
POST                         /api/users/{id}/{reset-password,disable-totp,totp-force}
GET / DELETE                  /api/sessions[/{token_id}]
GET / POST / DELETE          /api/tokens[/{id}]

# Settings & integrations (admin)
GET    /api/settings
POST   /api/settings                       additive — null = keep current
POST   /api/portainer/test                 probe Portainer + verify endpoint id
POST   /api/beszel/test
POST   /api/pulse/test
POST   /api/webmin/test
POST   /api/ping/test                      probe a single ping target (TCP or ICMP)
POST   /api/snmp/test                      probe an SNMP v2c / v3 target
POST   /api/http-probe/test                 probe one HTTP / TLS-cert / DNS target
POST   /api/oidc/test                      probe issuer's discovery endpoint
POST   /api/telegram/test                  fire a fixed test payload through Telegram ONLY
POST   /api/apprise/test                   fire a fixed test payload through Apprise ONLY
POST   /api/notify-test                    fire a test payload through every enabled medium
POST   /api/notify/send                    admin-typed notification routed to ONE medium

# Schedules / backups / SSH
GET / POST / PATCH / DELETE  /api/schedules[/{id}]
POST                          /api/schedules/{id}/run     fire immediately → {op_id}
GET                           /api/schedules/queue?limit=50
GET / POST / DELETE          /api/backups[/{name}]   create / list / remove
POST                          /api/backups/{name}/restore
GET                          /api/hosts/{id}/ssh/status
POST                          /api/hosts/{id}/ssh/test
POST                          /api/hosts/{id}/ssh/run    body: {command, dry_run}
WS                            /api/hosts/{id}/ssh/terminal   interactive xterm (WebSocket; cookie auth only — bearer not supported via stock browser WS APIs)

# Asset inventory
GET                           /api/asset-inventory                   serve cached asset list
POST                          /api/asset-inventory/test              probe asset-API token
POST                          /api/asset-inventory/refresh           force a full reload

# In-app notifications
GET                           /api/notifications                     paginated list (filterable by unread/severity/event)
POST                          /api/notifications/{id}/read           mark one row read
POST                          /api/notifications/read-all            mark every unread row read
DELETE                        /api/notifications/{id}                delete one row (admin)

# Notification templates (admin-only — title + body overrides per event)
GET                           /api/admin/notify-templates            list every event with current + default state
POST                          /api/admin/notify-templates/{event}    save title + body (empty = reset to default)
POST                          /api/admin/notify-templates/{event}/preview   render against sample placeholders
POST                          /api/admin/notify-templates/{event}/test      fire a real notification through every enabled medium

# AI integration (admin-only)
GET                           /api/admin/ai/dashboard?window=24h     aggregate token / cost / pass-rate dashboard
GET                           /api/admin/ai/jobs?limit=50            per-call log (filterable by provider / kind / status)
POST                          /api/admin/ai/{provider}/test          probe one provider's credentials + chosen model
POST                          /api/ai/palette                        natural-language palette query → answer / actions
POST                          /api/ai/host-filter                    bulk-translate a verb-leading phrase → Phase 1 DSL
POST                          /api/ai/feedback                       per-call 👍 / 👎 from the AI sidebar
GET / POST / DELETE          /api/ai/memory[/{id}]                  AI memory CRUD (durable per-deployment lessons)
POST                          /api/ai/memory/forget                  delete by exact-text match (`MEMORY-FORGET:` directive)

# Stats dashboards (admin-only)
GET                           /api/admin/stats/overview              cluster-wide quick-insight counts
GET                           /api/admin/stats/database?range=24h    DB KPIs + daily-INSERT bar charts
GET                           /api/admin/stats/network?range=24h     fleet-wide network throughput + burst-rate
GET                           /api/admin/stats/incidents?range=24h   incident view of host_failure_events
GET                           /api/admin/stats/ai-cost?range=24h     finance-style view of ai_jobs
GET                           /api/admin/stats/samples?range=24h     per-sample-table KPIs
GET / DELETE                  /api/admin/stats/samples/by-host       per-host drill-down (per sample table)
GET                           /api/admin/stats/samplers              per-sampler live state + last-tick rows + last-prune rows

# Admin tuning panel (admin-only)
GET                           /api/admin/tuning                      per-tunable effective state (DB / env / default / resolved)

# Config backup (admin-only — Settings-as-Code snapshots)
GET                           /api/admin/config-backup/export        stream current config as JSON download
GET                           /api/admin/config-backup/preview       same as export but inline JSON for diffing
POST                          /api/admin/config-backup/import        apply a snapshot to running config
GET                           /api/admin/config-backup/list          list saved snapshots under /app/data/config_backups/
POST                          /api/admin/config-backup/save          persist current config as a named snapshot
GET / POST / DELETE          /api/admin/config-backup/saved/{name}  fetch / restore / delete one snapshot

# Registry + retag
GET                           /api/registry/release-notes            resolve OCI labels for blast-radius preview
POST                          /api/update/stack/{id}/retag-latest    switch a Portainer stack's tag → {op_id}
POST                          /api/update/container/{id}/retag-latest  switch a container's image tag → {op_id}

# Telegram link management (admin + self-service)
GET                           /api/telegram/links                    list Telegram → user mappings (admin)
DELETE                        /api/telegram/links/{telegram_user_id} drop one mapping (admin)
POST                          /api/me/telegram-link-code             mint a one-shot /link code (TTL 5 min)
DELETE                        /api/me/telegram-link                  self-service unlink

# Profile / WebAuthn / UI prefs (self-service)
GET                           /api/me/webauthn                       list this user's enrolled passkeys
POST                          /api/me/webauthn/register-{start,finish}  passkey enrolment two-step
DELETE                        /api/me/webauthn/{credential_row_id}   revoke one passkey
POST                          /api/me/webauthn/client-error          best-effort browser-side error log
PATCH                         /api/me/{ui-prefs,notify-prefs,profile}  self-service prefs (covered above)
POST                          /api/me/ui-prefs/beacon                  beacon-friendly PATCH variant
POST / DELETE                 /api/me/avatar                          multipart upload / removal
GET                           /api/avatars/{fname}                    public avatar fetch

# Notification fan-out test surface (admin-only)
POST                          /api/notify-test                       fire test through every enabled medium
POST                          /api/notify/send                        user-typed notification → ONE medium

# Multi-tab activity sync (Admin → Sessions "active tabs" panel)
GET / POST / DELETE          /api/tabs/activity                       SPA heartbeat / read / cleanup

# Public IP / weather (topbar widgets + AI palette context block)
GET                           /api/public-ip                          admin-only; default OFF for privacy
GET                           /api/public-ip/history?limit=N          admin-only; ordered most-recent-first
GET                           /api/weather?lat=&lon=&label=           weather proxy (Open-Meteo or WeatherAPI per Admin → Weather)
GET                           /api/weather/history?limit=N&lat=&lon=  cached historical samples for AI / Telegram retrospective questions
POST                          /api/weather/test                       probe Open-Meteo or WeatherAPI credentials (admin)

# Login providers advertisement
GET                           /api/auth/providers                     {local, oidc, ...} for login page rendering

# Logs (admin-only)
GET / DELETE                  /api/logs                                tail / clear the in-memory ring buffer
GET                           /api/admin/logs/files                    list on-disk daily log files
GET                           /api/admin/logs/files/{name}             stream one file
GET                           /api/admin/logs/files/{name}/download    download one file

# Apps + service catalog (admin-only)
GET                           /api/apps                              one row per distinct app (grouped by catalog_id/name)
GET                           /api/apps/instances                    flat per-instance iterator (every chip across every host)
GET / POST                    /api/services/catalog                  list / create catalog templates
PATCH / DELETE                /api/services/catalog/{cid}            update / remove one template
POST                          /api/services/catalog/seed             re-seed built-in templates (idempotent)
GET                           /api/services/catalog/export           portable JSON pack export
POST                          /api/services/catalog/import           portable JSON pack import (upsert by slug)
POST                          /api/services/catalog/{cid}/pin        pin a template to one host
POST                          /api/services/discover/{host_id}       run discovery wizard for one host
POST                          /api/services/discover/{host_id}/apply bulk-apply a discovery proposal
PATCH / DELETE                /api/services/{host_id}/{service_idx}  edit / remove one pinned instance
POST                          /api/services/{host_id}/{service_idx}/probe   admin-only one-shot probe
POST                          /api/services/{host_id}/{service_idx}/test-credential  per-app credential test (e.g. Speedtest Tracker API key)
GET                           /api/services/{host_id}/{service_idx}/app-data        per-app expanded card data (per-slug dispatcher)
GET                           /api/services/{host_id}/{service_idx}/debug   per-chip diagnostics
GET                           /api/services/{host_id}/{service_idx}/history per-chip probe-result series
GET                           /api/container/{raw_id}/logs?lines=N   Portainer-routed container logs (admin)
GET                           /api/service/{raw_id}/logs?lines=N     Portainer-routed Swarm service logs (admin)

# HTTP probe (admin-only)
POST                          /api/http-probe/test                   probe one HTTP / TLS / DNS target (no save)
POST                          /api/hosts/{id}/http-probe/refresh     re-run + persist all URLs for the host

# Ignores
GET                           /api/ignores                           list ignore patterns
POST                          /api/ignores                           add pattern (admin)
DELETE                        /api/ignores/{pattern}                 remove pattern (admin)

# Settings version probe
GET                           /api/settings/version                  {version: int} — bumped on every Save (admin)

# Debug subject
GET                           /api/debug/subject?kind=&id=&since_hours=N  raw Stacks/Services/Nodes diagnostic (admin)

# Cleanup overlay network (Portainer-API-only path for stale VXLAN overlays)
POST                          /api/cleanup-overlay-network           {network_id?, service_id?, cidr?} → {op_id}

# Re-authentication (admin step-up gate)
POST                          /api/admin/reauth                      {password} → {ok}

# Health / metrics / version
GET    /api/healthz                        always 200 if alive
GET    /api/version                        {version}
GET    /metrics                            Prometheus exposition (no auth)
```

Full schema for each endpoint lives in `main.py` — every route is decorated with FastAPI type hints and most have docstrings explaining the contract.

## Limitations

- **External stacks** (deployed via `docker stack deploy` CLI and then "discovered" by Portainer) have no compose file stored in Portainer → stack update returns HTTP 400. The Update button is disabled and the detail drawer explains this. Workaround: redeploy via CLI or use the Restart (no-pull) action.
- **No live Docker events.** The ops panel polls the in-memory event log at 1.5s intervals — good enough for the "kicked off → succeeded / failed" loop, but not a real-time `docker events` stream.
- **Single-replica only.** State (live ops dict, gather cache, host-stats cache) lives in-memory inside one process. Running multiple replicas would split this state across replicas; the compose placement constraint pins the service to a single manager node by default. Lifespan-managed background tasks (samplers, scheduler, drift watcher) follow the same single-replica invariant.
- **SQLite-backed only (today).** The `DB_TYPE` env var scaffolds multi-database support but only `sqlite` is wired up. Postgres / MariaDB / MongoDB are on the roadmap for when a deployment with an existing managed DB needs them.
- **Worker-node container ops require Portainer Edge agent.** Container-level write ops (recreate / restart / remove) are routed via `X-PortainerAgent-Target: <hostname>` so Portainer talks to the right Docker daemon. Stack and service ops use Portainer's Swarm-aware endpoints and don't need this.
- **Time-series retention is bounded.** `STATS_HISTORY_DAYS` defaults to 7. Charts stop having data beyond that window. Bump the env var or push the data to a downstream Prometheus / VictoriaMetrics if you need long-term retention.
- **Asset-inventory integration is admin-supplied.** OmniGrid joins host rows against an external asset API (model / serial / location). The API contract is documented in [`docs/guidelines/api_services.md`](docs/guidelines/api_services.md); without it the asset card simply doesn't render.

## Updating OmniGrid itself

The CI pipeline handles the full update flow: `git push origin main` rsyncs the build context, runs `docker build --build-arg VERSION=<new>` on the manager, pushes the tag to the registry, and force-updates the running service onto it. CI also auto-bumps PATCH on every successful deploy. See [`docs/guidelines/deploy.md`](docs/guidelines/deploy.md) for the full runbook.

For manual updates without the pipeline, rsync the build context to the manager and rebuild + redeploy:

```bash
ssh pi@<manager> '
  cd /opt/omnigrid/app
  docker build --build-arg VERSION=$(date +%Y%m%d) -t omnigrid:latest .
  docker service update --force --image omnigrid:latest omnigrid_omnigrid
'
```

Or, of course, use OmniGrid itself to update… itself. Fun thought.

## Documentation

- [`docs/README.md`](docs/README.md) — index of admin runbooks (auth, OIDC,
  deploy, env reference, scheduler, metrics, npm updates, Beszel agent setup).
- [`CHANGELOG.md`](CHANGELOG.md) — release notes per Keep a Changelog (root
  per convention so git hosts and packagers auto-detect it).
- [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md) — SemVer cadence,
  PATCH auto-bump on deploy, periodic MINOR cuts, MAJOR breaking-change ritual.

## Contributing

Bug reports, focused pull requests, and feature proposals are welcome.
OmniGrid is maintained as a homelab tool first and a public collaboration
second, so a quick read of the on-ramp before opening a PR helps make
sure your work lands smoothly.

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — short contributor on-ramp.
  Covers project scope (single-replica, no build step, SQLite default by
  design), how to file a bug or propose a feature, local dev setup, the
  load-bearing conventions outsiders need to know up front (i18n strict
  via `t()`, CSS strict via tokens, RTL via logical properties,
  long-running tasks in `_lifespan`, brand-icon onboarding), pull
  request process, and the SemVer cadence pointer.
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — Contributor Covenant 2.1.
  Sets the expectations for participation in issues, PRs, and any other
  project space. Reports of unacceptable behaviour go to the maintainer
  email listed in the file.
- [`SECURITY.md`](SECURITY.md) — security policy. Private reporting
  channel, supported versions, response targets, and what to include in
  a vulnerability report.

## Screenshots

The Nodes view at the top of this README is the dashboard's most-used surface.
The full gallery lives under `docs/screenshots/` — a quick tour:

### Cluster overview

|                                                               |                                                                                                 |
|---------------------------------------------------------------|-------------------------------------------------------------------------------------------------|
| ![Stacks](docs/screenshots/stacks-view-light.png)             | **Stacks view** — grouped table, expand-per-stack, the default landing surface.                 |
| ![Services](docs/screenshots/services-view-light.png)         | **Services view** — flat sortable list of every Swarm service.                                  |
| ![Service detail](docs/screenshots/service-detail-drawer.png) | **Service detail drawer** — image / digest / actions (Restart / Recreate / Ignore).             |
| ![Nodes](docs/screenshots/nodes-view.png)                     | **Nodes view** — stacks grouped by Swarm node with live HOST CPU / MEM / DISK / UPTIME bars.    |
| ![History](docs/screenshots/history-audit-log.png)            | **History (audit log)** — every operation persisted with filterable when / op / target columns. |

### Hosts

|                                                                           |                                                                                          |
|---------------------------------------------------------------------------|------------------------------------------------------------------------------------------|
| ![Hosts (light)](docs/screenshots/hosts-view-light.png)                   | **Hosts view (light)** — curated host inventory grouped by `custom_number` ranges.       |
| ![Hosts (dark)](docs/screenshots/hosts-view-dark.png)                     | **Hosts view (dark)** — same data, dark theme.                                           |
| ![Hardware drawer](docs/screenshots/host-drawer-hardware.png)             | **Host drawer — hardware** — vendor / model / serial / OS / kernel / network details.    |
| ![Charts drawer](docs/screenshots/host-drawer-charts.png)                 | **Host drawer — charts** — CPU / Mem / Disk / Net In/Out / Load / Bandwidth time-series. |
| ![Charts drawer (bottom)](docs/screenshots/host-drawer-charts-bottom.png) | **Host drawer — bandwidth + swap** — scrolled view of the chart grid.                    |

### Admin / operations

|                                                                |                                                                                                                    |
|----------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| ![SSH run](docs/screenshots/host-drawer-ssh-run.png)           | **Host drawer — SSH-run** — admin one-shot command runner with dry-run, destructive-pattern guard, full audit.     |
| ![SSH terminal](docs/screenshots/host-drawer-ssh-terminal.png) | **Host drawer — SSH terminal** — interactive xterm.js session via WSS to the backend's asyncssh PTY.               |
| ![Hosts editor](docs/screenshots/admin-hosts-editor.png)       | **Admin → Hosts editor** — paginated curated-host CRUD with live discovery from each provider.                     |
| ![Schedules](docs/screenshots/admin-schedules.png)             | **Admin → Schedules** — cron-like recurring jobs (gather refresh / prune / backup / asset refresh).                |
| ![Backups](docs/screenshots/admin-backups.png)                 | **Admin → Backups** — DB + avatars snapshot zips with download / restore.                                          |
| ![Profile](docs/screenshots/settings-profile.png)              | **Settings → Profile** — account info, display name / email / avatar, password change.                             |
| ![Debug drawer](docs/screenshots/host-drawer-debug.png)        | **Host drawer — debug** — raw provider-payload view (Beszel / Pulse / NE / Webmin) for troubleshooting empty rows. |
