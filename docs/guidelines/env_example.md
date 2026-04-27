# Env var reference — OmniGrid

The real `.env` lives at the repo root and IS tracked in git. The repo is private (self-hosted
Forgejo), so operational secrets live alongside code rather than in a server-only file. CI ships
`.env` via rsync to `/opt/omnigrid/app/.env`; the bind mount `/opt/omnigrid/app:/app:ro` makes
it visible inside the container at `/app/.env`, where `main.py`'s first lines load it via
`python-dotenv` before any `os.getenv()` runs. `docker-compose.yml` deliberately does NOT use
Compose's `env_file:` key — the app reads its own config file so Portainer's web-editor stacks
don't have to resolve a host-side path.

This file is a curated reference for every key OmniGrid reads, with docs inline. When adding a
new env var to `main.py` or the `logic/` modules, add it here too so operators and future-you
can see the full surface in one place.

A fresh deploy can boot with NO env vars at all — bootstrap admin via
`POST /api/local-auth/bootstrap`, then configure Portainer and (optionally) OIDC / host-stats
providers from the Settings UI. Every value below is either a transitional bootstrap aid or a
process-level tunable.

## Portainer connection (OPTIONAL bootstrap — UI is authoritative)

These four keys are consulted ONCE on first boot with an empty `settings` row and seeded into
the DB. After that, Settings → Portainer wins and env changes are ignored. Marked transitional
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

> **Live override available (#337).** Each of the six tunables below also has a
> matching DB setting (`tuning_<lowercase_env_var>`). When set from
> Admin → Config the DB value wins; blank/unset falls back to the env var
> shown here, which falls back to the code default. UI changes take
> effect on the next consumer read (per-request for TTLs, per-tick for
> samplers — one tick lag).

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

# Retention window for every time-series table (stats_samples,
# host_net_samples, host_metrics_samples). Pruned hourly by each
# lifespan sampler against the same window.
STATS_HISTORY_DAYS=7

# Cadence for the lifespan samplers — _stats_cache snapshot into
# stats_samples, node-exporter scrape into host_net_samples and
# host_metrics_samples.
STATS_SAMPLE_INTERVAL_SECONDS=300

# Docker Hub auth — optional, avoids anonymous rate limits.
# DOCKERHUB_USER=
# DOCKERHUB_TOKEN=

# Where python-dotenv looks for env values at startup. Override only for
# non-standard deployments (e.g. alternative bind mount layouts).
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

OIDC provider config is stored in the `settings` table and edited from Settings → Authentik
OIDC. There are intentionally no `OIDC_*` env vars — the UI is the only source of truth. See
`docs/guidelines/authentik.md` for the Authentik-side setup walkthrough.

## Host-stats providers (UI-managed, NO env vars)

Beszel Hub, Proxmox Pulse, Prometheus node-exporter, and Webmin credentials all live in the
`settings` table (`beszel_hub_url`, `beszel_identity`, `beszel_password`, `pulse_url`,
`pulse_token`, `node_exporter_enabled`, `node_exporter_url_template`, `webmin_url`, `webmin_user`,
`webmin_password`, `webmin_aliases`, etc.) and are edited from Settings → Host stats. No env vars.
The curated host list (`hosts_config`) is also DB-backed, managed from Admin → Hosts.

The `node_exporter_url_template` validator accepts either `{host}` OR `{ip}` as a placeholder
(substitution logic in `logic/gather.py` resolves both against the curated host row).

## Other UI-managed settings (NO env vars)

- **Weather proxy** (`open_meteo_url`) — edited from Admin → General. Blank = weather
  widget reports `configured: false`; there is NO fallback to `api.open-meteo.com` anymore.
- **Apprise** (`apprise_url`, `apprise_tag`, `portainer_public_url`). Edited from
  Admin → Notifications.
- **Per-event notification toggles** — 12 op events
  (`notify_event_{stack_update, container_update, container_restart, container_remove,
  service_restart, prune}_{success, failure}`) plus one security event
  (`notify_event_user_login`, default OFF). Admin → Notifications hosts the global gates;
  Settings → Notifications hosts the per-user opt-in/out (stored in
  `users.ui_prefs.notify_events`). Two-layer scoping: admin gate first, then per-user.
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
| `PORTAINER_URL`                   | Bootstrap   | `""`                 | UI-managed. Seeded into DB on first boot; Settings → Portainer wins thereafter. |
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
| `DOCKERHUB_USER`                  | Optional    | unset                | Docker Hub auth (avoid anonymous rate limits).                                  |
| `DOCKERHUB_TOKEN`                 | Optional    | unset                | Paired with `DOCKERHUB_USER`.                                                   |
| `SESSION_SECRET`                  | Auth        | auto-generated       | HMAC key for session cookies. Set explicitly in prod.                           |
| `BOOTSTRAP_ADMIN_USER`            | First-boot  | unset                | First-boot-only admin seed.                                                     |
| `BOOTSTRAP_ADMIN_PASSWORD`        | First-boot  | unset                | First-boot-only admin seed.                                                     |

No env vars exist for OIDC, Beszel, Pulse, node-exporter, Apprise, schedules, or the curated
hosts list. All of those are UI-managed and stored in the `settings` table.
