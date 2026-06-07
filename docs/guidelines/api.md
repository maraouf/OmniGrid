# OmniGrid API guide

How to integrate with OmniGrid programmatically — script your cluster updates,
pipe metrics into other dashboards, or build CI gates that wait for digest
parity before promoting an image.

The README's [API section](../../README.md#api-if-you-want-to-script-it) is the
quick reference; this file covers auth, error handling, common workflows, and
endpoint contracts in depth.

## Auth

Every `/api/*` route requires authentication. Three exceptions, all unauthenticated:

| Path           | Purpose                                                                                 |
| -------------- | --------------------------------------------------------------------------------------- |
| `/api/healthz` | Liveness probe — always `200 {"status":"ok"}` if the process is alive.                  |
| `/api/version` | Returns `{version}` (the live `MAJOR.MINOR.PATCH`) for the running deploy.              |
| `/metrics`     | Prometheus exposition. (Treat as sensitive — fleet stats; gate at the proxy if needed.) |

For everything else, two auth modes:

### 1. Bearer API token (recommended for scripts)

1. Sign in to the UI as an admin → **Admin → API tokens** → **Generate**.
2. Copy the raw token (shown ONCE — OmniGrid stores only its SHA-256 at rest).
3. Send on every request:

   ```http
   Authorization: Bearer og_<your-token>
   ```

Tokens carry their own role (`admin` / `readonly`) — the role is set when the
token is issued and is independent of any user's role. Bearer-authed requests
**bypass CSRF** since they don't use cookies.

```bash
TOKEN='og_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/items | jq '.[:3]'
```

### 2. Cookie session (browser flows)

`POST /api/local-auth/login` with `{username, password}` returns the
`og_session` HMAC-signed cookie. Subsequent requests must:

- Send the cookie back (most HTTP clients do this automatically with a cookie jar).
- Include `X-CSRF-Token: <og_csrf cookie value>` on every `POST/PUT/PATCH/DELETE` to `/api/*` (double-submit pattern).

The SPA does this transparently via its global `fetch` wrapper. For curl:

```bash
COOKIE_JAR=/tmp/omnigrid-cookies.txt
# Log in
curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"...","remember":true}' \
  https://omnigrid.example.com/api/local-auth/login

# Subsequent write request must echo og_csrf
CSRF=$(grep og_csrf "$COOKIE_JAR" | awk '{print $7}')
curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
  -H "X-CSRF-Token: $CSRF" \
  -X POST https://omnigrid.example.com/api/update/stack/abc123
```

For SSO users: log in via `/api/oidc/login` and the same cookie+CSRF rules apply.

For local users with TOTP / 2FA enabled, the cookie path is two-step:

```bash
# Step 1 — username + password. If TOTP is required, returns a challenge token
# instead of a session cookie:
RESP=$(curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"..."}' \
  https://omnigrid.example.com/api/local-auth/login)

CHALLENGE=$(echo "$RESP" | jq -r '.challenge_token // empty')
if [ -n "$CHALLENGE" ]; then
  # Step 2 — submit the 6-digit code (or 8-char backup code) within 5 min
  curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
    -H 'Content-Type: application/json' \
    -d "{\"challenge_token\":\"$CHALLENGE\",\"code\":\"123456\"}" \
    https://omnigrid.example.com/api/local-auth/totp
fi
```

Bearer-token callers bypass TOTP entirely — the token's role check is the only auth step.

WebAuthn / passkey is an interchangeable second factor — when the user has either or both
enrolled, `/api/local-auth/login` returns `methods: [...]` so the client can pick. The passkey
challenge flow lives at `POST /api/local-auth/webauthn-start` + `POST /api/local-auth/webauthn-finish`;
see [`passkeys.md`](passkeys.md) for the full enrolment + login walkthrough. Headless / scripted
callers should stay on bearer tokens — passkeys are a browser flow.

### Roles

| Role       | Reads        | Writes                                     |
| ---------- | ------------ | ------------------------------------------ |
| `admin`    | ✓ everything | ✓ everything                               |
| `readonly` | ✓ everything | ✗ all `POST/PUT/PATCH/DELETE` return `403` |

The role is enforced server-side via FastAPI `Depends(auth.require_admin)` on
every write route. UI-side role gating is a UX nicety only.

### Client config (`GET /api/me`)

`/api/me` is auth-optional: unauthed callers get `{authenticated: false}`, authed callers get
their identity plus a `client_config` object that surfaces the live values of every
admin-tunable knob the SPA / dashboards need. Re-read it on a slow cadence (every page load,
or as part of any auth handshake) to pick up Admin → Config edits without a SPA reload.

```jsonc
{
  "authenticated": true,
  "username": "admin",
  "role": "admin",
  "source": "local",
  "client_config": {
    "ops_poll_ms": 2000,             // tuning_ops_poll_interval_seconds × 1000
    "hosts_parallel_fetch": 6,        // tuning_hosts_parallel_fetch
    "scheduler_tz": { "configured": "Africa/Cairo", "resolved": "Africa/Cairo", "fallback": false }
    // ...
  }
}
```

The full canonical list of tunables is `logic/tuning.py:TUNABLES`; the ones surfaced into
`client_config` are the ones the SPA actually reads. Add a knob there + the GET-side handler
when wiring a new frontend-controlled tunable.

## Response shape conventions

Most endpoints return JSON. Common shapes:

**List endpoints**

```json
[{"id": "svc:abc123", "name": "...", "status": "update"}]
```

**Single-resource**

```json
{"id": "svc:abc123"}
```

**Operation kicked off (async)**

```json
{"op_id": "<uuid>", "status": "running"}
```

**Test / probe endpoints**

```json
{"ok": true, "detail": "OK — Portainer 2.27.4, endpoint primary reachable"}
```

```json
{"ok": false, "detail": "endpoint 99 not found on this Portainer", "status": 404}
```

**Error** (FastAPI default; HTTP code in the response status)

```json
{"detail": "<message>"}
```

## Real-time events

OmniGrid streams live deltas over an SSE channel at `GET /api/events`. The
SPA connects automatically and gates its polling loops on the connection's
health — the toolbar pill flips to "Live" once the stream is up. Headless /
machine clients can subscribe too, but most should stay on polling (see
"Auth" below).

### Connection contract

- **Method**: `GET /api/events`
- **Content-Type**: `text/event-stream`
- **Headers set on response**: `Cache-Control: no-cache, no-transform`,
  `X-Accel-Buffering: no`, `Connection: keep-alive`. The latter two prevent
  upstream proxies (nginx / NPM / Traefik) from buffering frames.
- **Auth**: cookie session OR bearer token — same as every other `/api/*`
  route. Cookie-authed callers DO NOT need a CSRF token (SSE is GET-only;
  CSRF is only enforced on state-changing methods).
- **Heartbeat**: server emits a `: keepalive` comment line every 25 seconds
  with no traffic. Clients should treat ~30s without ANY frame as "stream
  is dead" and reconnect.
- **Reconnect**: browsers' `EventSource` reconnects automatically with its
  own backoff. Headless clients implementing SSE should follow the same
  pattern.

### Event types

Each frame is `event: <type>\ndata: <json>\n\n` where the JSON body is
`{"type": "<type>", "ts": <epoch>, "payload": {...}}`. Payload shape per
type:

| Type                         | Fired when                                                                                                                                                                                                                | Payload (selected fields)                                                                                                                                                                                                                                                                        |                             |
| ---                          | ---                                                                                                                                                                                                                       | ---                                                                                                                                                                                                                                                                                              |                             |
| `hello`                      | First frame after upgrade                                                                                                                                                                                                 | `{subscriber_count, heartbeat_seconds}` — confirms the upgrade succeeded.                                                                                                                                                                                                                        |                             |
| `op:created`                 | A new background op (update / restart / remove / prune) starts.                                                                                                                                                           | `{id, op_type, status, target_name, target_stack, actor, started}`                                                                                                                                                                                                                               |                             |
| `op:updated`                 | Op progresses (logs an event, transitions a substep).                                                                                                                                                                     | `{id, op_type, status, target_name, last_event:{ts, level, msg}}`                                                                                                                                                                                                                                |                             |
| `op:completed`               | Op terminates (success / error).                                                                                                                                                                                          | `{id, op_type, status, target_name, error, duration}`                                                                                                                                                                                                                                            |                             |
| `cache:invalidated`          | Items cache has been marked stale (post-op refresh, settings save).                                                                                                                                                       | `{reason}`                                                                                                                                                                                                                                                                                       |                             |
| `stats:refreshed`            | `gather_stats()` finished a cycle.                                                                                                                                                                                        | `{items, with_stats, with_size, ts}` — hint only; consumers refetch via `/api/stats`.                                                                                                                                                                                                            |                             |
| `host:failure_state_changed` | Host sampler paused / cleared a host OR per-(provider, host) auto-pause flipped.                                                                                                                                          | `{host_id, paused, consecutive_failures?, last_error?, cleared?, provider?}` — `provider` present for per-provider transitions (`snmp` / `webmin` / etc.). `host_id` is ALWAYS the bare id (the SPA's `/api/hosts/one/{id}` lookup needs the bare value, not the prefixed key the table stores). |                             |
| `host:history_appended`      | A new row was inserted into `host_metrics_samples` for a curated host.                                                                                                                                                    | `{host_id, ts}` — hint only; consumers refetch the full window via `/api/hosts/history`.                                                                                                                                                                                                         |                             |
| `host:provider_probing`      | A per-host probe slice (SNMP / Webmin / node-exporter / HTTP probe) just entered the wire — fires only on cache MISS so dict-lookup providers (Beszel / Pulse) and sampler-driven ones (Ping / service_probe) don't emit. | `{host_id, provider, started_at}` — SPA tracks `_polling[provider]` per row so the matching chip pulses while ITS specific probe is in flight.                                                                                                                                                   |                             |
| `host:provider_done`         | The matching `host:provider_probing` slice has completed (success OR failure).                                                                                                                                            | `{host_id, provider, finished_at, duration_ms, outcome?}` — SPA clears `_polling[provider]`; chip settles into its post-probe state.                                                                                                                                                             |                             |
| `host:ping_sampled`          | New ping sample landed in `ping_samples` for a curated host.                                                                                                                                                              | `{host_id, alive, rtt_ms, loss_pct, ts}` — hint only; consumers refetch via `/api/hosts/{id}/ping/history`.                                                                                                                                                                                      |                             |
| `schedule:fired`             | A schedule started or finished (two events per fire).                                                                                                                                                                     | `{schedule_id, name, kind, op_id, phase: "start"\                                                                                                                                                                                                                                                | "end", duration?, status?}` |
| `history:appended`           | A new row was written to the `history` table.                                                                                                                                                                             | `{id, ts, op_type, target_name, target_id, target_stack, status, duration, error, actor}`                                                                                                                                                                                                        |                             |
| `session:renewed`            | A cookie session was slid forward (sliding-window refresh near expiry).                                                                                                                                                   | `{user_id, expires_at, ts}`                                                                                                                                                                                                                                                                      |                             |
| `settings:updated`           | An admin Save through `POST /api/settings` committed.                                                                                                                                                                     | `{version, client_id?}` — version is the new `_settings_version` int; `client_id` is the originating tab's UUID (when present, the originating tab self-filters via `_isSelfEvent`).                                                                                                             |                             |
| `notification:created`       | A new in-app notification row was inserted by the `app` notification medium.                                                                                                                                              | `{id, ts, event, severity, title, body, actor, target_kind, target_id, unread_count}` — payload carries a self-contained snapshot so the SPA can prepend without an extra round-trip; `unread_count` is the canonical count post-insert.                                                         |                             |
| `notification:read`          | One notification row (or all unread, when `bulk=true`) was marked read.                                                                                                                                                   | `{id?, read_at, unread_count, bulk?}` — `id=null + bulk=true` means a `read-all` fired. Originating tab self-filters via `client_id`.                                                                                                                                                            |                             |
| `notification:deleted`       | One notification row was deleted (admin scrub or schedule prune).                                                                                                                                                         | `{id, unread_count}` — originating tab self-filters via `client_id`.                                                                                                                                                                                                                             |                             |
| `port_scan:completed`        | A per-host port scan (on-demand `POST /api/hosts/{id}/port-scan` OR a `port_scan_refresh` schedule fire) finished.                                                                                                        | `{host_id, scan_id, target, ports_count, new_ports?}` — hint only; consumers refetch via `GET /api/history/port-scan/{scan_id}/ports` for the full per-port detail. The on-demand POST returns `{scan_id, status: "queued"}` immediately and the SPA waits on this event.                        |                             |
| `public_ip:changed`          | The public-IP change-sampler (or any incidental lookup) detected a WAN IP change.                                                                                                                                          | `{ip, isp, asn, country, country_code, city, prev_ip, ts}` — self-contained snapshot so the topbar widget updates in place the instant the change is detected, without waiting out the SPA's 10-min refresh cache. Only a real change publishes (first-ever record does not).                      |                             |
| `telegram:linked`            | The Telegram listener's `/link` command bound a sender's Telegram user_id to an OmniGrid user.                                                                                                                            | `{username, telegram_user_id, linked_at_ms}` — SPA scopes by `username === me.username` and re-fetches `/api/me` so the Profile → Telegram card flips to its linked-state banner.                                                                                                                |                             |
| `telegram:unlinked`          | The Telegram listener's `/unlink` command (or admin-side `DELETE /api/telegram/links/{tg_id}`) dropped a mapping.                                                                                                         | `{username, telegram_user_id}` — SPA scopes by `username === me.username` and re-fetches `/api/me` so the Profile → Telegram card flips back to "Generate code".                                                                                                                                 |                             |
| `host:bulk_action_applied`   | A bulk host action (`/api/hosts/bulk/{pause,resume,snmp_vendors,snmp_tunables}`) committed across N hosts.                                                                                                                | `{action, host_ids:[...], actor, ...action-specific}` — single frame for the whole batch, not N per-host frames. Tabs reconcile via in-place row updates keyed on `host_ids`; originating tab self-filters via `client_id`.                                                                      |                             |
| `apps:bulk_pinned`           | A discovery-wizard bulk-apply (`POST /api/services/discover/{host_id}/apply`) committed N pins on one host.                                                                                                               | `{host_id, applied:[...], skipped:[...]}` — single frame for the whole batch. SPA's handler iterates the lists and patches `appsInstances` in place. Originating tab self-filters via `client_id`.                                                                                               |                             |
| `tab:activity`               | A tab heartbeated (current view, last interaction) into the `_tab_activity_registry`.                                                                                                                                     | `{client_id, username, view, ts, ...}` — drives the Admin → Sessions "active tabs" panel so admins can see who's looking at what. Originating tab self-filters.                                                                                                                                  |                             |
| `tab:closed`                 | A tab fired its `pagehide` cleanup.                                                                                                                                                                                       | `{client_id}` — peer tabs drop the entry from their local view of the registry without waiting for the 90s TTL. Originating tab self-filters.                                                                                                                                                    |                             |
| `:overflow`                  | Synthetic — the per-subscriber queue dropped events.                                                                                                                                                                      | `{}` — react with a one-shot REST refresh.                                                                                                                                                                                                                                                       |                             |
| `reconnect`                  | Synthetic — server hit the SSE max-lifetime cap (default 6 h, tunable via `tuning_sse_max_lifetime_seconds`) and is asking the client to re-upgrade so the auth middleware fires again.                                   | `{}` — `EventSource` reconnects automatically; bespoke clients should drop the connection and reopen.                                                                                                                                                                                            |                             |

Event names use a `<noun>:<verb>` convention; new event types follow the
same shape. Payloads are intentionally narrow — consumers that need the
full server-side resource shape should refetch via the REST endpoint
keyed on the payload's id field.

### Client guidance

- **Browser SPA**: connects automatically — no opt-in. The toolbar status
  pill ("Live" / "Polling") is the at-a-glance cue. Polling resumes if the
  stream drops for more than ~30s.
- **Bearer-token machine clients**: stay on polling. Browser `EventSource`
  can't easily attach an `Authorization` header — the server accepts a
  bearer over SSE just fine, but very few SSE client libraries support it
  cleanly. Use `/api/items` / `/api/ops` / `/api/history` on whatever
  cadence your scripts need; the polling endpoints are not deprecated and
  serve the same payloads they always have.
- **Slow consumers**: each subscriber gets a bounded queue (256 events).
  When the queue fills, oldest events are dropped and the next frame the
  consumer reads is `:overflow`. React by reconciling state via REST —
  events are ephemeral; there's no replay.
- **Multiple connections**: each tab opens its own subscriber. Server cost
  is one async task + one queue per connection. The single-replica deploy
  invariant means horizontal scale-out would replace this design rather
  than extend it.

### Curl example

```bash
COOKIE_JAR=/tmp/omnigrid-cookies.txt
# Cookie auth (after a successful /api/local-auth/login)
curl -sN -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
  -H 'Accept: text/event-stream' \
  https://omnigrid.example.com/api/events
```

You'll see `event: hello\ndata: {...}\n\n`, then live events as they
fire, with `: keepalive` comments every 25s during quiet periods.

## Common workflows

### Watch the cluster

```bash
# Snapshot of every service + container with update status
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/items \
  | jq '[.[] | {name, status, image, remote_digest}]'
```

`status` is one of: `update | up-to-date | unknown | error | ignored`.
`up-to-date` means the running digest matches the registry's; `update`
means a newer manifest exists; `unknown` means OmniGrid couldn't resolve
either side; `error` means the registry probe failed; `ignored` means
the image / stack matched an entry in the ignore list.

### Bulk-update every "update" stack

```bash
# Find every stack-level item with status=update, fire one update each.
ITEMS=$(curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/items)
echo "$ITEMS" | jq -r '.[] | select(.type=="service" and .status=="update") | .stack' \
  | sort -u | while read STACK; do
    echo "Updating stack $STACK..."
    curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
      "https://omnigrid.example.com/api/update/stack/$STACK" \
      | jq '.op_id'
  done
```

### Watch an operation to completion

Operations run as `BackgroundTasks` — the POST returns immediately with an
`op_id`. Poll `/api/ops/{op_id}` for the event log:

```bash
OP=$(curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  https://omnigrid.example.com/api/update/stack/abc123 | jq -r .op_id)

# Poll every 1.5s until done.
while true; do
  S=$(curl -sS -H "Authorization: Bearer $TOKEN" \
    https://omnigrid.example.com/api/ops/$OP)
  STATE=$(echo "$S" | jq -r .status)
  echo "[$(date +%T)] $STATE"
  if [ "$STATE" = "succeeded" ] || [ "$STATE" = "failed" ]; then break; fi
  sleep 1.5
done
echo "$S" | jq '.events'
```

### Pull host telemetry

`/api/hosts/list` is the cheap shape — host inventory + status, no per-host
probes. Use this for dashboards that just want "is this host up + what
provider data is available":

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/hosts/list \
  | jq '.hosts[] | {id, label, status, providers}'
```

`/api/hosts/list` does **snapshot-first render**: every row is pre-populated
from the persisted `host_snapshots` table with `_stale_fields` /
`_stale_ts` markers stamped. The SPA's existing stale-rendering pipeline
(dimmed values + "X minutes ago" tooltip) kicks in automatically so a
repeat visit paints instantly with cached values, then upgrades silently
as `/api/hosts/one/{id}` fan-out lands. First-time visits with an empty
snapshot table fall through to the legacy skeleton shape. Bearer-token
clients see the same stale markers and can decide whether to trust them
or force-fetch via `?force=true`.

**Background-refresh contract.** Both `/api/items` and `/api/hosts/list`
serve cached / snapshot data IMMEDIATELY when warm and kick the live
refresh into a background `asyncio.create_task` (Fix A from the
cold-load instant-paint work). Two response keys signal the in-flight
state to scripted callers:

- `cache_refreshing: bool` (on `/api/items`) — `true` when the in-memory
  `_cache` was served immediately because data was present, AND a
  background `_gather()` is in flight to refresh it. The next poll
  picks up the fresh state. `false` when the cache is warm OR when a
  cold-cache caller just awaited a fresh gather (in which case the
  response body IS the fresh state, no refresh in flight).
- `hub_probing: bool` (on `/api/hosts/list`) — `true` when the
  Beszel + Pulse hub probe was kicked in the background and the
  response carries snapshot rows + per-row `_stale_fields` markers
  rather than a freshly-probed result. The per-host fan-out via
  `/api/hosts/one/{id}` shares the in-flight hub probe via the
  single-flight lock, so each row's eventual upgrade gets the fresh
  data without re-paying the probe cost.

Scripts that need authoritative data should poll until both flags read
`false` (or use `?force=true` to await a fresh gather synchronously —
at the cost of the 10-30s cold-cache wall-clock).

For full telemetry on one host (cached 10s server-side; per-host probe budget is 30s — beyond that the endpoint returns a 504 with `detail: "per-host probe budget exceeded (30s) for <id>"` so OmniGrid's explicit 504 always fires before NPM's generic 60s `proxy_read_timeout`):

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://omnigrid.example.com/api/hosts/one/host01" \
  | jq '.host | {host_cpu_percent, host_mem_used, host_mem_total, host_disk_used, mounts, network_ifaces}'

# Force-bypass the 10s provider-state cache (mirrors the same flag on /api/hosts/list and the
# legacy /api/hosts endpoint):
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://omnigrid.example.com/api/hosts/one/host01?force=true"
```

Concurrent fan-out from a SPA / dashboard tile is single-flight on the server: the first cold-cache caller pays the Beszel + Pulse hub probe, every parallel caller within the same window awaits and reuses the populated cache. The SPA caps its own fan-out via `client_config.hosts_parallel_fetch` (`/api/me`) — see "Client config" below. Lazy fetch: the SPA only calls `/api/hosts/one/{id}` for rows that enter the viewport (IntersectionObserver with a 200 px above/below `rootMargin`) instead of fanning out at page load — bearer-token scrapers polling on a fixed cadence don't get this optimisation and should still consider `?force=true` carefully.

`/api/hosts/one/{id}` also writes to `host_snapshots` after a successful merge: when at least one snapshot-eligible field came from a LIVE provider (i.e. is meaningful AND not in `_stale_fields`), the merged dict is persisted so the next `/api/hosts/list` render falls back to it cleanly. Entirely-fallback merges (every snapshot-eligible field came from the snapshot itself) skip the write — the snapshot's `ts` only advances on real live data, so the freshness banner ("Last live data Xm ago") matches the SNMP / time-series chart's own freshness label.

The legacy `/api/hosts` endpoint composes `/list` + `/one` per-row and shares the same single-flight + Webmin success/fail caches. It's the lowest-friction shape for one-call dashboard scrapers (Homarr widget, Grafana JSON-API panel) that want one round-trip for the whole fleet — but at the cost of waiting for every per-host probe before returning. Prefer the split endpoints for SPA-style UIs.

For time-series charts (1 / 6 / 24 / 168 hour windows):

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://omnigrid.example.com/api/hosts/history?host_id=host01&hours=24" \
  | jq '.series | length'
```

For the per-unit Beszel systemd-services snapshot of one host
(state pill / sub_state / last_seen / last_change for every unit
the agent tracks; failed units sorted first):

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/hosts/host01/beszel/services \
  | jq '.services[] | select(.state == 3)'
```

Hosts whose Beszel agent isn't tracking systemd units return an
empty `services` array. The data is sourced from the local
`host_beszel_services` table (UPSERTed by the lifespan
`host_beszel_sampler` on every tick), not a live hub fetch.

### Schedules — cron-like jobs from the API

```bash
# List every scheduled job
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/schedules

# Add: prune all worker nodes daily at 03:00
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Nightly prune",
    "kind": "prune_all_nodes",
    "cadence": "daily",
    "time_of_day": "03:00",
    "enabled": true,
    "params": {}
  }' \
  https://omnigrid.example.com/api/schedules
```

### Backups

```bash
# Trigger a fresh DB+avatars snapshot
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  https://omnigrid.example.com/api/backups

# List available snapshots
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/backups | jq

# Download one
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/backups/2026-04-26-19-00.zip \
  -o snapshot.zip
```

### SSH command runner (admin-only, audited)

Every run goes through a destructive-pattern gate (typed-hostname confirm in
the UI) and writes a `history` row with `op_type='ssh_run'`.

```bash
# Dry-run first (recommended)
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"command":"systemctl status node_exporter","dry_run":true}' \
  https://omnigrid.example.com/api/hosts/host01/ssh/run | jq

# Real run
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"command":"systemctl status node_exporter","dry_run":false}' \
  https://omnigrid.example.com/api/hosts/host01/ssh/run | jq
```

The interactive xterm terminal (`/api/hosts/{id}/ssh/terminal`) is a WebSocket
endpoint, not a plain `/api` route — same auth (cookie session for browser
clients only; bearer doesn't work over WebSockets in stock browser APIs).

### Asset inventory

OmniGrid joins host rows against an external asset API (model / serial / location). The
cached payload is served from a JSON file on disk; refresh is admin-triggered (no
background sampler). The integration carries a master toggle (`asset_inventory_enabled`,
default true for back-compat) — when off, `GET` and `refresh` short-circuit with
`{ok:false, error:'asset_inventory_disabled'}` and the `asset_inventory_refresh` schedule
kind no-ops.

```bash
# Serve the cached asset list
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/asset-inventory | jq '.assets | length'

# Probe the upstream OAuth client_credentials flow (bool roundtrip)
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' -d '{}' \
  https://omnigrid.example.com/api/asset-inventory/test | jq

# Force a full reload from the upstream API
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  https://omnigrid.example.com/api/asset-inventory/refresh | jq
```

The upstream contract is documented in [`api_services.md`](api_services.md). Configure the
token in **Admin → Asset inventory** before calling `refresh`.

### In-app notifications

Every event that fires through Apprise also lands in a SQLite-backed in-app store. The
SPA renders the rows newest-first in a popup behind the user-avatar dropdown; bearer-token
clients can pull the same data over REST and subscribe to deltas via SSE
(`notification:created` / `notification:read` / `notification:deleted`).

```bash
# Paginated list (newest first). `unread=1` filters to unread only;
# `severity=error,warning` and `event=stack_update_failure` further narrow.
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://omnigrid.example.com/api/notifications?limit=50&offset=0" | jq

# Mark one row read
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  https://omnigrid.example.com/api/notifications/123/read | jq

# Mark every unread row read in one call
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  https://omnigrid.example.com/api/notifications/read-all | jq

# Delete one row (admin-only — admins can scrub anyone's row;
# the schedule kind `prune_notifications` does retention cleanup)
curl -sS -H "Authorization: Bearer $TOKEN" -X DELETE \
  https://omnigrid.example.com/api/notifications/123 | jq
```

Per-medium toggles (`notify_medium_app` / `notify_medium_apprise`) gate each delivery
channel independently of the per-event toggles; both default ON. Retention is controlled
by `tuning_notification_retention_days` (default 90) and the admin-creatable
`prune_notifications` schedule kind.

### Notification templates (admin-only)

Each event ships with a hard-coded default title + body; admins can override either
through DB-backed settings (`notify_template_<event>_title` / `_body`). Three routes
back the Admin → Notifications template editor and the Profile read-only viewer:

```bash
# List every event with current + default state
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/admin/notify-templates | jq

# Save a template (empty string = reset to default)
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"title": "✅ {name} updated by {actor}", "body": "Status: {status}"}' \
  https://omnigrid.example.com/api/admin/notify-templates/stack_update_success | jq

# Live preview against sample placeholder values (no state change)
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"title": "{name}", "body": "by {actor}"}' \
  https://omnigrid.example.com/api/admin/notify-templates/stack_update_success/preview | jq

# Fire one real notification through every enabled medium for verification
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{}' \
  https://omnigrid.example.com/api/admin/notify-templates/stack_update_success/test | jq
```

Curated placeholder whitelist: `{name}` / `{type}` / `{actor}` / `{host}` / `{time}`
/ `{error}` / `{status}`. Unknown placeholders render verbatim (`{foo}`) so a typo
never crashes the dispatch — the editor's preview pane highlights them in a warning
chip. Templates round-trip UTF-8 (emoji friendly).

### Swarm-agent restart

When the SPA detects a Portainer Swarm agent has been failing per-node container-stats
calls past `tuning_swarm_agent_unhealthy_threshold` consecutive cycles, the unhealthy
banner offers a one-click "Restart agent service" button (admin-only). Scripted callers
can fire the same op:

```bash
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  https://omnigrid.example.com/api/swarm/restart-agent | jq
```

Returns `{op_id}`. The op's `op_type` is `swarm_agent_restart`; success / failure fires
the `swarm_agent_restart_success` / `swarm_agent_restart_failure` notification events.

### Per-node prune

`POST /api/prune/node/{hostname}` runs `docker system prune` on the named Swarm node
(target resolved via Portainer's task / node listing). Returns `{op_id}`; the
`prune_node` op writes a `history` row on completion. The schedule kinds `prune_node`
(per-node) and `prune_all_nodes` (fan-out across every visible hostname) wrap this
operation for cron-like usage — see [`scheduler.md`](scheduler.md).

### Port scan (admin-only)

OmniGrid scans a curated host's TCP / UDP service surface from the host drawer's "Port
scan" button OR on a schedule (`port_scan_refresh` kind — see
[`scheduler.md`](scheduler.md)). The on-demand endpoint resolves the target via the
canonical chain `address → ping.host → ssh.fqdn → ssh.host → host_id`, validates the
master toggle + per-host opt-in, spawns a fire-and-forget asyncio task, and returns
`{scan_id, status: "queued"}` immediately so reverse-proxy `proxy_read_timeout` settings
(NPM defaults to 60 s) don't trip on long scans up to `tuning_port_scan_max_seconds`
(default 120 s). Completion fires `port_scan:completed` over SSE; the persisted detail
is read via `GET /api/history/port-scan/{scan_id}/ports`.

```bash
# Trigger an on-demand TCP scan of host01 (defaults to the curated `address` field +
# the configured TCP port CSV from Admin → Providers → Port Scan).
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' -d '{}' \
  https://omnigrid.example.com/api/hosts/host01/port-scan | jq
# → {"scan_id":"<uuid>","status":"queued","target":"192.X.X.X","ports_count":<n>}

# Override the default ports + concurrency + add UDP companion (Stage 2)
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"ports":"22,80,443,8080","timeout_s":3,"concurrency":16,"udp":true,"udp_ports":"53,123,161"}' \
  https://omnigrid.example.com/api/hosts/host01/port-scan | jq

# After SSE port_scan:completed lands, fetch the per-port detail
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/history/port-scan/<scan_id>/ports | jq
# → {"ports":[{"port":22,"protocol":"tcp","service_hint":"ssh","banner_excerpt":"SSH-2.0-OpenSSH_..."}, …]}
```

### Per-(provider, host) resume

`POST /api/hosts/{id}/resume-sampling` clears WHOLE-host auto-pause markers (used by
`host_metrics_sampler`'s permanent-fail window). Auto-pause from `record_provider_outcome`
is keyed `<provider>:<host_id>`; clear ONE provider at a time:

```bash
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  https://omnigrid.example.com/api/hosts/host01/provider/snmp/resume | jq
# Provider must be one of: snmp / webmin / beszel / pulse / node_exporter /
# ping / http_probe / service_probe (the eight per-(provider, host)
# auto-pause keys recorded by `record_provider_outcome`).
```

### Bulk host actions (admin-only)

Four endpoints accept `{host_ids: [...]}` plus action-specific params and apply atomically
across the matched hosts. The endpoint emits ONE `host:bulk_action_applied` SSE frame
carrying the full `host_ids` list (not N per-host `host:failure_state_changed` frames) so
other tabs reconcile in a single round-trip — the SPA handler iterates `host_ids` and
triggers `refreshHostRow` per id locally. The originating tab self-filters via `client_id`.
Per-host audit rows are still written to the `history` table (`op_type=hosts_bulk_pause`
/ `hosts_bulk_resume` / `hosts_bulk_snmp_vendors` / `hosts_bulk_snmp_tunables`) so the
Timeline tab shows the action as one row per affected host.

```bash
# Pause sampling for a list of hosts
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"host_ids":["host01","host02"]}' \
  https://omnigrid.example.com/api/hosts/bulk/pause | jq

# Resume sampling
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"host_ids":["host01","host02"]}' \
  https://omnigrid.example.com/api/hosts/bulk/resume | jq

# Apply / replace SNMP vendor whitelist on multiple hosts
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"host_ids":["host01","host02"],"vendors":["dell","ucd"]}' \
  https://omnigrid.example.com/api/hosts/bulk/snmp_vendors | jq

# Apply per-host SNMP tunables (walk concurrency, wall-clock budget)
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"host_ids":["host01"],"walk_concurrency":8,"wall_clock_budget_seconds":120}' \
  https://omnigrid.example.com/api/hosts/bulk/snmp_tunables | jq
```

Each bulk action writes one history row per affected host (`op_type=hosts_bulk_pause` /
`hosts_bulk_resume` / `hosts_bulk_snmp_vendors` / `hosts_bulk_snmp_tunables`), tagged with
the actor + the resolved host id. The Timeline tab joins on host id so the per-host view
surfaces the bulk action under that host's timeline alongside its other audit events.

### AI palette + memory (admin-only)

The AI integration ships an Admin → AI tab with master toggle, per-provider configs
(Claude / Gemini / ChatGPT / DeepSeek), usage dashboard, and a per-deployment memory
store. Calls are gated on the master toggle + the active-provider toggle; every successful
or errored call records to `ai_jobs` with token + cost + latency aggregates.

```bash
# Send a natural-language prompt to the AI palette. Body shape: {query, context?}.
# Returns {ok, provider, model, response_time_ms, tokens, answer, actions?, dsl?, …}.
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"query":"which hosts have low disk?"}' \
  https://omnigrid.example.com/api/ai/palette | jq

# Bulk-translate a verb-leading phrase into a Phase 1 DSL string the SPA can review
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"query":"pause every host with low disk"}' \
  https://omnigrid.example.com/api/ai/host-filter | jq
# → {"ok":true,"dsl":"pause: host01 host05 host09","explanation":"…"}

# Per-call feedback (user clicks 👍 / 👎 in the AI sidebar)
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"job_id":12345,"score":1,"comment":""}' \
  https://omnigrid.example.com/api/ai/feedback | jq

# AI memory CRUD — durable lessons the AI has learned about THIS deployment
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/ai/memory | jq
# → {"ok":true,"memories":[{"id":1,"ts":…,"text":"…","source":"ai"|"operator","actor":"…"}, …]}

# Add a memory manually (source defaults to 'operator')
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"text":"This deployment runs OPNsense at 192.X.X.1 — never SSH to it"}' \
  https://omnigrid.example.com/api/ai/memory | jq

# Delete one memory by id
curl -sS -H "Authorization: Bearer $TOKEN" -X DELETE \
  https://omnigrid.example.com/api/ai/memory/123 | jq

# Forget by exact text match — used when the AI emits MEMORY-FORGET: <text>
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"text":"<exact memory body to delete>"}' \
  https://omnigrid.example.com/api/ai/memory/forget | jq
```

The AI emits two directives the SPA parses out of every reply: `MEMORY: <one-line lesson>`
appends to `ai_memory` (after user confirm); `MEMORY-FORGET: <exact text>` deletes the
matching row. Memory injects into every subsequent palette call's system prompt so
knowledge accumulates across sessions.

Admin-only dashboard endpoints for the Admin → AI tab:

```bash
# Aggregate dashboard (windowed call counts, token / cost totals, per-provider breakdown)
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://omnigrid.example.com/api/admin/ai/dashboard?window=24h" | jq

# Per-call log (paginated, filterable by provider / kind / status)
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://omnigrid.example.com/api/admin/ai/jobs?limit=50&provider=claude" | jq

# Probe one provider's credentials + chosen model (uses persisted settings when body empty)
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' -d '{}' \
  https://omnigrid.example.com/api/admin/ai/claude/test | jq
# Provider must be one of: claude / gemini / chatgpt / deepseek
```

### Per-host telemetry detail endpoints

Beyond `/api/hosts/history` (the canonical per-host time-series), several admin-only
endpoints surface deeper provider-specific detail used by the host drawer:

| Method | Route                                          | Purpose                                                                                                                                                                   |
| ------ | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`  | `/api/hosts/{id}/beszel/services`              | Per-(host, systemd unit) snapshot from `host_beszel_services` (failed units first).                                                                                       |
| `GET`  | `/api/hosts/{id}/snmp/history?hours=N`         | SNMP-derived host samples (CPU / memory / disk / uptime).                                                                                                                 |
| `GET`  | `/api/hosts/{id}/snmp/iface_history?hours=N`   | Per-interface throughput counters (top-5 by current rate).                                                                                                                |
| `GET`  | `/api/hosts/{id}/snmp/temp_history?hours=N`    | Per-temperature-probe sensor readings (ENTITY-SENSOR-MIB).                                                                                                                |
| `GET`  | `/api/hosts/{id}/http-probe/history`           | Per-host HTTP probe samples (status code + TLS expiry + DNS resolution + latency per URL).                                                                                |
| `POST` | `/api/hosts/{id}/http-probe/test`              | Run one HTTP / TLS / DNS probe synchronously against the host's configured URLs.                                                                                          |
| `GET`  | `/api/hosts/{id}/ping/history?hours=N`         | Per-host Ping reachability + RTT samples.                                                                                                                                 |
| `GET`  | `/api/hosts/{id}/disk-projection?days_ahead=N` | Linear regression on `host_disk_used` across the configured window — projects "days until full" with a confidence band.                                                   |
| `GET`  | `/api/hosts/{id}/triage`                       | Inline similar-incident grouping for host failures (read from `host_failure_events`).                                                                                     |
| `GET`  | `/api/hosts/{id}/timeline?hours=N`             | Unified per-host event timeline (state changes + sampler errors + bulk-action audit rows).                                                                                |
| `GET`  | `/api/hosts/debug?id=<host>&since_hours=N`     | Raw provider payloads + counters block (samples-in-window, failure_state, provider_pause_state, full live tunables map) — the "why is this host's chart cut?" diagnostic. |

### Stats dashboards (admin-only)

Six aggregated endpoints back the **Stats** top-nav (cluster-wide insight on top of the
per-host telemetry detail above). Each is GET-only, accepts an optional `window`/`range`
query param, and returns a single fast JSON payload tuned for one dashboard sub-page.

| Method   | Route                                                                  | Purpose                                                                                                                                                                                                                                                                         |
| -------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`    | `/api/admin/stats/overview`                                            | Quick-insight counts — user / session / curated-host / asset / node / stack / container totals + per-provider enabled split. One-shot fetch for the dashboard landing card.                                                                                                     |
| `GET`    | `/api/admin/stats/database?range=24h`                                  | DB-level KPIs — table row counts, daily-INSERT bar-chart series, total bytes on disk, with thousands-separator-friendly shapes.                                                                                                                                                 |
| `GET`    | `/api/admin/stats/network?range=24h`                                   | Fleet-wide network throughput KPIs — per-host top-N + burst-rate table for the selected range.                                                                                                                                                                                  |
| `GET`    | `/api/admin/stats/incidents?range=24h`                                 | Incident-centric view of `host_failure_events` — top hosts by incident count, per-provider failure breakdown.                                                                                                                                                                   |
| `GET`    | `/api/admin/stats/ai-cost?range=24h`                                   | Finance-style view of `ai_jobs` — per-provider token / cost / latency / response-time-trend chart.                                                                                                                                                                              |
| `GET`    | `/api/admin/stats/samples?range=24h`                                   | Per-sample-table KPIs (Beszel / Pulse / NE / SNMP / Webmin / HTTP probe / service probe / ping) — row counts, daily-INSERT bar charts.                                                                                                                                          |
| `GET`    | `/api/admin/stats/samples/by-host?table=<name>&host_id=<id>&range=24h` | Per-host drill-down popup for one sample table. Returns the matching row count + recent rows for the chosen host.                                                                                                                                                               |
| `DELETE` | `/api/admin/stats/samples/by-host?table=<name>&host_id=<id>`           | Admin scrub for one host's rows in one sample table. Used by the per-host drill-down popup's "Delete rows" button.                                                                                                                                                              |
| `GET`    | `/api/admin/stats/samplers`                                            | Per-sampler live state — running flag, last-tick timestamp, last-tick row count, last-prune row count, effective interval. Drives the Samplers sub-page (sibling of Database / Network) so operators can verify each sampler is actually writing rows AND pruning to retention. |

```bash
# Example: fetch the AI-cost dashboard for the last 24 hours
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://omnigrid.example.com/api/admin/stats/ai-cost?range=24h" | jq
```

`range` values: `1h` / `24h` / `7d` / `30d` (defaults to 24h when omitted). The frontend
range-picker writes back to the user's date / time prefs so the choice persists per-tab.

### Apps + service catalog (admin-only)

The Apps surface tracks operator-pinned services on each curated host, plus a service catalog
of built-in templates (AdGuard Home, Plex, Sonarr, Authentik, etc.) that the discovery wizard
matches against open ports. Two view planes — the aggregate `/api/apps` (one row per distinct
app grouped by `catalog_id` or `name`) and the flat `/api/apps/instances` (one entry per
chip across every host).

| Method   | Route                                                   | Purpose                                                                                                                                                                                                                              |
| -------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `GET`    | `/api/apps`                                             | Cross-host aggregate — one row per distinct app, with every host that runs an instance + per-instance status.                                                                                                                        |
| `GET`    | `/api/apps/instances`                                   | Flat per-instance iterator — every chip across every host.                                                                                                                                                                           |
| `GET`    | `/api/services/catalog`                                 | List every catalog template (built-in + operator-added).                                                                                                                                                                             |
| `POST`   | `/api/services/catalog`                                 | Create a new operator-defined catalog template.                                                                                                                                                                                      |
| `PATCH`  | `/api/services/catalog/{cid}`                           | Update a template (name / icon / default ports / probe shape / etc.).                                                                                                                                                                |
| `DELETE` | `/api/services/catalog/{cid}`                           | Remove a template. Built-ins are protected unless the operator explicitly deletes them; a seeded-slug ledger prevents the same built-in from being re-seeded after deletion.                                                         |
| `POST`   | `/api/services/catalog/seed`                            | Re-seed built-in templates. Idempotent — skips slugs that already exist OR have been deleted-on-purpose.                                                                                                                             |
| `GET`    | `/api/services/catalog/export`                          | Export the full catalog as a portable JSON pack (for backup / sharing).                                                                                                                                                              |
| `POST`   | `/api/services/catalog/import`                          | Import a catalog pack — upserts by slug.                                                                                                                                                                                             |
| `POST`   | `/api/apps/catalog/{slug}/show-extras`                  | Toggle a catalog template's `show_extras` flag by slug. Body `{show_extras: bool}`. Controls whether the template's rich expanded card renders without unpinning the chip. (Per-instance overrides live on `hosts_config[].services[].show_extras`, tri-state: inherit / always / never.) |
| `POST`   | `/api/services/catalog/{cid}/pin`                       | Pin a catalog template to a host. Body: `{host_id, port?, url?, name_override?, icon_override?}`. Creates a new entry under `hosts_config[].services[]`.                                                                             |
| `POST`   | `/api/services/discover/{host_id}`                      | Run the discovery wizard for one host — matches the host's open-port set against catalog templates and returns a proposal list.                                                                                                      |
| `POST`   | `/api/services/discover/{host_id}/apply`                | Bulk-apply a discovery proposal. Body: `{picks: [{catalog_id, port, ...}]}`.                                                                                                                                                         |
| `PATCH`  | `/api/services/{host_id}/{service_idx}`                 | Edit a pinned instance (name / URL / icon / ports / probe).                                                                                                                                                                          |
| `DELETE` | `/api/services/{host_id}/{service_idx}`                 | Remove a pinned instance from a host.                                                                                                                                                                                                |
| `POST`   | `/api/services/{host_id}/{service_idx}/probe`           | Admin-only synchronous probe of one chip. Routes through the same TCP / HTTP probe helpers as the lifespan sampler; persists to `service_samples` so the SPA picks it up.                                                            |
| `POST`   | `/api/services/{host_id}/{service_idx}/test-credential` | Per-app credential test (e.g. Speedtest Tracker API key, future per-app auth probes). Slug-keyed dispatcher resolves the chip's catalog template and routes to the matching `logic/apps/<slug>.py` module. Returns `{ok, detail}`.   |
| `POST`   | `/api/services/{host_id}/{service_idx}/skill/{skill_id}` | Run one per-app SKILL — the same action surface the AI palette / Telegram bot invoke and the app-drawer skill buttons fire. Slug-keyed dispatcher resolves the chip's catalog template, validates `skill_id` against the module's `SKILLS` tuple, and delegates to `run_skill`. Body may carry `{arg}` for arg-taking skills (e.g. Radarr "add a movie"). Returns the skill's `{ok, detail, status?}`. Fleet skills (AdGuard / Pi-hole) act across every instance regardless of the chosen chip. Writes a `services_skill` history row. |
| `GET`    | `/api/services/{host_id}/{service_idx}/app-data`        | Per-app expanded-card data (e.g. Speedtest Tracker latest / average / sparkline points; APC UPS battery / load / runtime). Slug-keyed dispatcher; returns the shape the matching `static/js/apps/<slug>.js` extras renderer expects. |
| `GET`    | `/api/services/{host_id}/{service_idx}/debug`           | Per-chip diagnostics — resolved probe target, per-port outcomes, plain-language reason when probing is suppressed.                                                                                                                   |
| `GET`    | `/api/services/{host_id}/{service_idx}/history`         | Per-chip probe-result time series.                                                                                                                                                                                                   |
| `GET`    | `/api/container/{raw_id}/logs?lines=N`                  | Stream the last N log lines from a container linked to an app instance via Portainer. Routes through the Portainer agent so worker-node containers are reachable.                                                                    |
| `GET`    | `/api/service/{raw_id}/logs?lines=N`                    | Same shape for a Swarm service.                                                                                                                                                                                                      |

#### Per-app modules + skills

Apps with custom logic (an auth-gated data fetch, a rich expanded card, or
AI / Telegram skills) live in their own module under `logic/apps/<slug>.py`,
paired with a frontend module (`static/js/apps/<slug>.js`) and HTML partials
(`static/_partials/_components/apps/<slug>_editor.html` + `_extras.html`). The
generic route layer stays slug-agnostic: the three slug-keyed dispatchers
(`/test-credential`, `/app-data`, `/skill/{skill_id}`) resolve the chip's
catalog template, look up the per-app module via `logic/apps/registry.py`, and
delegate. Adding a new app is one module per layer plus one registration entry
— no edits to the generic files.

Each module declares a `SLUGS` tuple (catalog slugs it handles), an optional
`requires_api_key()`, an optional `test_credential(...)`, an optional
`fetch_data(...)` (the expanded-card payload), an optional `peek_latest(...)`
(cache-only peek for the AI context), and an optional `SKILLS` tuple +
`run_skill(...)` coroutine. Every skill is BOTH an app-drawer button AND an
AI / Telegram action — but only surfaces when the app's extras are enabled and
(if `requires_api_key()`) its api_key is set. Fleet modules
(`FLEET_SKILLS = True`) run a skill across every pinned instance regardless of
the chosen chip.

Current per-app roster:

| App | Slug(s) | Auth | Skills |
| --- | --- | --- | --- |
| Speedtest Tracker | `speedtest-tracker` | Bearer api_key | latest / run-test |
| APC UPS | `apcupsd` (SNMP-backed) | none | battery / load / runtime (frontend-only extras) |
| Radarr | `radarr` | api_key | status / upcoming / queue / look-up / add / remove / search-missing / refresh |
| Sonarr | `sonarr` | api_key | status / upcoming / queue / look-up / add / remove / search-missing / refresh |
| Lidarr | `lidarr` | api_key | status / upcoming / queue / artist look-up / add-artist / remove-artist / search-missing / refresh |
| Readarr | `readarr` | api_key | status / upcoming / queue / author look-up / add-author / remove-author / search-missing / refresh |
| Bazarr | `bazarr` | api_key | status / search-missing / list-missing |
| Seerr (Overseerr / Jellyseerr) | `seerr` (+ `overseerr` / `jellyseerr`) | api_key (+ optional TMDB key) | status / request-movie / suggest-movie |
| AdGuard Home | `adguardhome` (+ aliases) | HTTP Basic | fleet: status / enable / disable (+ timed) / refresh / re-enable |
| Pi-hole | `pihole` (v6) | password-only (SID auth) | fleet: status / enable / disable (+ timed) / refresh / re-enable |
| AdGuard Home Sync | `adguardhome-sync` (+ aliases) | HTTP Basic (optional) | status / sync-now / logs / clear-logs |
| ddns-updater | `ddns-updater` | none (HTML-table scrape) | status / update-now |

Two of these read from upstreams that expose **no JSON API**: ddns-updater
parses its web-UI HTML table (each cell carries a `data-label` attribute), and
AdGuard Home Sync best-effort scrapes its root page for the version string. The
HTML-scrape pattern is a reusable convention for any future app whose only
machine-readable surface is its rendered UI.

### HTTP probe one-shot (admin-only)

| Method | Route                                | Purpose                                                                                                       |
| ------ | ------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| `POST` | `/api/http-probe/test`               | Probe one HTTP / TLS-cert / DNS target with the form-provided URL + options (no save).                        |
| `POST` | `/api/hosts/{id}/http-probe/refresh` | Re-run the HTTP probe across all configured URLs for the given host and persist to `host_http_probe_samples`. |

### Stack + container retag-to-latest (admin-only)

When OmniGrid detects a stack / container running a pinned tag (`:v1.2.3`) that an update would
break (because the registry's `:latest` has moved past the pin), the drawer surfaces a
"Switch to `:latest`" affordance backed by these endpoints. Confirmed via SweetAlert in the SPA
because of the recreate risk.

| Method | Route                                               | Body                                                                               | Purpose                                                                                                                                                                                             |
| ------ | --------------------------------------------------- | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST` | `/api/update/stack/{stack_id}/retag-latest`         | `{tag: "latest"}` (other tags accepted; the validator clamps to a small allowlist) | Switch a Portainer-managed stack's image refs to a different tag. Routes through Portainer's stack-edit flow.                                                                                       |
| `POST` | `/api/update/container/{container_id}/retag-latest` | `{tag?: str}` (optional — defaults to `latest`)                                    | Switch a non-Portainer-managed container's image tag. Captures Config + HostConfig + Networks via inspect, pulls the new image, stop + remove + recreate with the same name. Named volumes survive. |

Returns `{op_id, new_tag}`. Drives the same Operation lifecycle as the other write ops — poll
`/api/ops/{op_id}` for progress.

### Registry release notes (admin-only)

`GET /api/registry/release-notes?image=<ref>&from_digest=<sha256>&to_digest=<sha256>` resolves
the OCI image labels on a target manifest and returns any embedded release-notes URL +
short summary. Used by the stack-update popup's blast-radius preview to show the upstream
changelog snippet before committing the update.

### Config backup (admin-only)

Settings-as-Code snapshots — a complementary surface to the SQLite zip created by the
`backup` schedule kind. Snapshot covers the `settings` KV table, schedules, ai_memory; secrets
are redacted to `__OMITTED__`. Created on demand from Admin → Config backup or by the
`config_backup` scheduler kind; the `tuning_config_backup_retention_count` knob bounds the
saved-snapshots set.

| Method   | Route                                           | Purpose                                                                                      |
| -------- | ----------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `GET`    | `/api/admin/config-backup/export`               | Stream the current config as a JSON download (no on-disk save).                              |
| `GET`    | `/api/admin/config-backup/preview`              | Same payload as `/export` but returned inline for the SPA to diff against the current state. |
| `POST`   | `/api/admin/config-backup/import`               | Body: a JSON snapshot. Applies to the running config (admin step-up required).               |
| `GET`    | `/api/admin/config-backup/list`                 | List on-disk saved snapshots under `/app/data/config_backups/`.                              |
| `POST`   | `/api/admin/config-backup/save`                 | Persist the current config as a saved snapshot (auto-named with a timestamp).                |
| `GET`    | `/api/admin/config-backup/saved/{name}`         | Fetch one saved snapshot's body.                                                             |
| `POST`   | `/api/admin/config-backup/saved/{name}/restore` | Restore from one saved snapshot. Admin step-up required.                                     |
| `DELETE` | `/api/admin/config-backup/saved/{name}`         | Delete one saved snapshot.                                                                   |

### Notification fan-out test surface (admin-only)

Three endpoints fan a test notification through different mediums for debugging the per-event /
per-medium routing matrix.

| Method | Route                | Purpose                                                                            |           |                                                                                                                                                                                  |
| ------ | -----                | -------                                                                            |           |                                                                                                                                                                                  |
| `POST` | `/api/notify-test`   | Fire a fixed test payload through every enabled medium (app + apprise + telegram). |           |                                                                                                                                                                                  |
| `POST` | `/api/apprise/test`  | Fire ONLY through Apprise.                                                         |           |                                                                                                                                                                                  |
| `POST` | `/api/telegram/test` | Fire ONLY through Telegram.                                                        |           |                                                                                                                                                                                  |
| `POST` | `/api/notify/send`   | Send a user-typed notification through ONE chosen medium. Body: `{medium: "app"    | "apprise" | "telegram", title, body, severity?, target_kind?, target_id?}`. Routes through `logic.ops.notify` with the same template + per-event toggle pipeline as system-generated events. |

### Telegram link management (admin-only)

The Telegram bot's `/link` and `/unlink` commands populate `telegram_links(telegram_user_id,
username, ...)` to map a Telegram sender to an OmniGrid user. Two admin-side endpoints surface
the table for the Admin → Notifications → Telegram tab:

| Method   | Route                                    | Purpose                                                                                                                                  |
| -------- | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`    | `/api/telegram/links`                    | List every mapping (`telegram_user_id`, `username`, `linked_at_ms`).                                                                     |
| `DELETE` | `/api/telegram/links/{telegram_user_id}` | Drop one mapping. Emits `telegram:unlinked` SSE — the linked user's Profile → Telegram card flips back to its unlinked state on receipt. |

User-side self-service for the Profile → Telegram card:

| Method   | Route                        | Purpose                                                                                       |
| -------- | ---------------------------- | --------------------------------------------------------------------------------------------- |
| `POST`   | `/api/me/telegram-link-code` | Mint a fresh one-shot code (TTL 5 min) the user pastes into the bot's `/link <code>` command. |
| `DELETE` | `/api/me/telegram-link`      | Self-service unlink for the current user.                                                     |

### Profile / WebAuthn self-service

| Method   | Route                                  | Purpose                                                                                                                              |
| -------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `GET`    | `/api/me/webauthn`                     | List the current user's enrolled passkeys (id, friendly_name, transports, last_used, rp_id, registered_at).                          |
| `POST`   | `/api/me/webauthn/register-start`      | Start enrolment — returns the WebAuthn challenge options.                                                                            |
| `POST`   | `/api/me/webauthn/register-finish`     | Complete enrolment — body: the browser's attestation response.                                                                       |
| `DELETE` | `/api/me/webauthn/{credential_row_id}` | Revoke one enrolled passkey.                                                                                                         |
| `POST`   | `/api/me/webauthn/client-error`        | Best-effort log of a client-side WebAuthn error so server-side logs reflect why the browser refused (e.g. RP-ID mismatch detection). |
| `PATCH`  | `/api/me/ui-prefs`                     | Partial update on the current user's UI prefs blob (sidebar width, datetime format, ai_conversation, theme overrides).               |
| `POST`   | `/api/me/ui-prefs/beacon`              | Same as PATCH but accepts `navigator.sendBeacon`'s `application/x-www-form-urlencoded`-style body for tab-unload writes.             |
| `PATCH`  | `/api/me/notify-prefs`                 | Partial update on the per-event notify opt-in / opt-out map.                                                                         |
| `PATCH`  | `/api/me/profile`                      | Update display name / email / bio / avatar metadata.                                                                                 |
| `POST`   | `/api/me/avatar`                       | Multipart upload of a new avatar (PNG / JPG / WebP, capped server-side).                                                             |
| `DELETE` | `/api/me/avatar`                       | Remove the user's avatar (reverts to the deterministic-hue initial).                                                                 |
| `GET`    | `/api/avatars/{fname}`                 | Fetch one avatar file. Public — no auth — same shape as `/img/icons/*`.                                                              |

### Multi-tab activity sync

The Admin → Sessions "active tabs" panel reads a shared in-memory registry of every connected
SPA tab. The SPA writes through these endpoints on every navigation / drawer-open / filter
change so admins can see "who is looking at what" in real time.

| Method   | Route                | Purpose                                                                                                       |
| -------- | -------------------- | ------------------------------------------------------------------------------------------------------------- |
| `POST`   | `/api/tabs/activity` | Heartbeat — body: `{client_id, view, drawer_host?, ...}`. Stamps into the registry. Emits `tab:activity` SSE. |
| `DELETE` | `/api/tabs/activity` | Best-effort cleanup on tab close (`pagehide`). Emits `tab:closed` SSE.                                        |
| `GET`    | `/api/tabs/activity` | Read the registry. Admin-only.                                                                                |

### Public IP / weather widget

Both endpoints support topbar widgets + the AI palette context block. Public IP is admin-only
+ default-OFF (opt-in via the master toggle in Admin → Public IP); weather is anonymous and
opt-in via the topbar widget setting. Weather has two providers (Open-Meteo, free no-key —
default; WeatherAPI.com, free with a key for full moon-phase astronomy) selected via Admin →
Weather; the active provider drives both the live `/api/weather` proxy and the historical
sampler that writes `weather_samples`.

| Method | Route                                          | Purpose                                                                                                                                                                                          |
| ------ | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `GET`  | `/api/public-ip`                               | Admin-only. Returns `{enabled, ip?, isp?, asn?, country?, city?}` from `logic.public_ip.fetch()`. `{enabled: false}` when the master toggle is off.                                              |
| `GET`  | `/api/public-ip/history?limit=N`               | Admin-only. Returns the last N rows (default 100; 1..1000) from `public_ip_history`, most-recent-first.                                                                                          |
| `GET`  | `/api/weather?lat=<f>&lon=<f>&label=<s>`       | Weather proxy returning the compact widget shape `{temp_c, humidity, wind_kmh, code, condition, icon, forecast: [...]}`. Routes through whichever provider is active (Open-Meteo or WeatherAPI). |
| `GET`  | `/api/weather/history?limit=N&lat=<f>&lon=<f>` | Cached historical samples for AI / Telegram retrospective questions (e.g. "what was the weather yesterday?"). Source is the lifespan `weather_sampler` writing into `weather_samples`.           |
| `POST` | `/api/weather/test`                            | Admin-only. Probe the chosen provider with the form-provided credentials. Body shape `{provider, lat?, lon?, api_key?}` — Open-Meteo ignores `api_key`; WeatherAPI requires it.                  |
| `GET`  | `/api/image-proxy?url=<encoded>`               | Server-side image proxy for external poster art (TMDB) so the browser loads it from the app's own domain when clients have no direct egress to `image.tmdb.org`. Host-ALLOWLISTED to TMDB image hosts (NOT an open proxy — SSRF guard), absolute-http(s) only, non-image / oversized bodies rejected, 1-day cache. Used by the AI skill-panel + app-drawer Seerr-suggestion posters. |

### Prayer Times (dashboard widget + AI / Telegram context)

Prayer Times is opt-in (default OFF — enabling authorises outbound calls to
`api.aladhan.com`). All settings (master toggle, calculation method, Asr school,
fallback location, lead time for reminders) live in the DB and are edited from
Admin → Prayer Times.

| Method | Route                                                       | Purpose                                                                                                                                                                                                                                |
| ------ | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `GET`  | `/api/prayer-times?lat=&lon=&label=&method=&school=&force=` | Today's five prayers + Sunrise + the Hijri date for one lat/lon. `method` / `school` default to the Admin → Prayer Times settings. No coordinates → falls back to the configured default location. `{configured: false}` when the feature is off; `{configured: true, no_location: true}` when enabled but no location is available. |
| `GET`  | `/api/prayer-times/history?limit=N`                         | Cached daily prayer-time + Hijri-date samples (written once per day per location by the lifespan sampler). Powers the Admin → Prayer Times "Recent samples" panel and AI / Telegram retrospective questions.                          |
| `POST` | `/api/prayer-times/test`                                    | Admin-only. Probe the AlAdhan API against the form-provided (or persisted-default) location + method/school. Stamps `last_test_success` for the shared og-test-connection component. Body `{lat?, lon?, label?, method?, school?}`.   |

### Login providers advertisement

| Method | Route                 | Purpose                                                                                                                                                                                                      |
| ------ | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `GET`  | `/api/auth/providers` | Public. Tells the login page which paths are live — `{local: bool, oidc: bool, ...}`. OIDC is hidden when the request's hostname doesn't match the configured `oidc_redirect_uri` host (multi-FQDN deploys). |

### Logs (admin-only)

| Method   | Route                                   | Purpose                                                                                                 |
| -------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `GET`    | `/api/logs?limit=N`                     | Tail of the in-memory ring buffer. Filterable by severity / source-tag.                                 |
| `DELETE` | `/api/logs`                             | Clear the in-memory ring buffer. Persistent on-disk daily files under `/app/data/logs/` are unaffected. |
| `GET`    | `/api/admin/logs/files`                 | List on-disk daily log files (`omnigrid-YYYY-MM-DD.log`) with size + modified-time.                     |
| `GET`    | `/api/admin/logs/files/{name}`          | Stream the contents of one file.                                                                        |
| `GET`    | `/api/admin/logs/files/{name}/download` | Download one file as an attachment.                                                                     |

### TOTP self-service + admin (covered above in the SPA flow, listed here for the API map)

| Method | Route                           | Purpose                                                             |
| ------ | ------------------------------- | ------------------------------------------------------------------- |
| `GET`  | `/api/me/totp`                  | Current user's TOTP state (enabled / required / backup-code count). |
| `POST` | `/api/me/totp/enroll-start`     | Returns secret + QR `otpauth://` URI.                               |
| `POST` | `/api/me/totp/enroll-confirm`   | Verifies the first code; mints backup codes.                        |
| `POST` | `/api/me/totp/regenerate-codes` | Rotates backup codes.                                               |
| `POST` | `/api/me/totp/disable`          | User self-disable (requires password).                              |
| `POST` | `/api/users/{id}/disable-totp`  | Admin-side disable of one user's TOTP enrolment.                    |
| `POST` | `/api/users/{id}/totp-force`    | Admin-side per-user `totp_force_required` flag.                     |

### Ignore list

| Method   | Route                    | Purpose                                                                                  |            |
| ------   | -----                    | -------                                                                                  |            |
| `GET`    | `/api/ignores`           | Return the operator-curated ignore patterns (kind = `image` substring OR `stack` exact). |            |
| `POST`   | `/api/ignores`           | Admin-only. Body: `{pattern: str, kind: "image"                                          | "stack"}`. |
| `DELETE` | `/api/ignores/{pattern}` | Admin-only. Delete the matching pattern row.                                             |            |

### Settings version probe

| Method | Route                   | Purpose                                                                                                                                                                                                                  |
| ------ | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `GET`  | `/api/settings/version` | Admin-only cheap probe for cross-tab settings-change detection. Returns the monotonic `_settings_version` int that's bumped on every successful `POST /api/settings`. Use as a polling fallback when SSE is unavailable. |

### Admin tuning panel

| Method | Route               | Purpose                                                                                                                                                                                                                                                                                                                                                                            |
| ------ | ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`  | `/api/admin/tuning` | Admin-only. Returns per-tunable effective state: each `tuning_<key>` carries `db` (operator override or null), `env` (env-var value), `default` (code default), `lo` / `hi` (bounds), `resolved` (effective value), plus a `consumed` boolean that flags whether any backend site actually reads the key (catches decorative knobs that drift in the registry without a consumer). |

### Debug subject (admin-only)

`GET /api/debug/subject?kind=<stack|service|container>&id=<id>&since_hours=N` returns a raw
diagnostic payload for the Stacks / Services / Nodes drawer's "Show debug data" toggle —
fanned-out Portainer responses, gather-cache entries, recent ops, history rows. Same shape
the host-drawer `GET /api/hosts/debug?id=<host>` returns for hosts.

### Cleanup overlay network (admin-only)

`POST /api/cleanup-overlay-network` is the Portainer-API-only path for stale VXLAN
overlays — used by the drawer task-error auto-fix descriptor `cleanup-overlay-network`.
Body shape `{network_id?: str, service_id?: str, cidr?: str}`; the handler parses the
failing CIDR from `task_error` when `cidr` is supplied, walks
`/docker/networks?filters={"driver":["overlay"]}` for a subnet match, verifies no live
containers reference the network, then deletes it + force-updates the named service so
Docker recreates the overlay (and a fresh VXLAN interface) on the new task. SSH-free;
aborts cleanly when the network is shared. Returns `{op_id}`; success / failure fires
`overlay_cleanup_success` / `overlay_cleanup_failure` notification events.

### Re-authentication (admin-only step-up)

`POST /api/admin/reauth` accepts `{password}` for local users and re-validates the
caller's credentials against the live password hash. Used as a step-up gate for
high-stakes admin operations (TOTP disable / force-set, bulk host pause, etc.) that
shouldn't trust the existing session alone. SSO users (no local password) bypass via
the `_user_has_local_password` check; the SPA's typed-hostname confirm is the fallback
gate for them. Body returns `{ok: bool, detail?}`.

### Version

```bash
# Current MAJOR.MINOR.PATCH (public — no auth required)
curl -sS https://omnigrid.example.com/api/version | jq
```

The `/api/admin/version` endpoint and Admin → Version UI page were removed in 2026-04-30
alongside the deploy migration to image-build. Versions are now baked into the image
at build time via the Dockerfile's `ARG VERSION`; durable MAJOR/MINOR seeds are done by
editing the repo-root `VERSION.txt`, committing, and pushing — `deploy.yml`'s version
resolver picks it up as the floor for the next PATCH bump. See `docs/RELEASE_PROCESS.md`
for the full release workflow.

### Probe-style "does my settings work?" endpoints

Each integration has a `/test` endpoint that fires one synchronous probe with
the given (or saved) credentials. Useful for CI-style health-checking your
OmniGrid deploy. Eight integration probes today (Portainer + OIDC + six of
the eight host-stats providers — Beszel / Pulse / Webmin / Ping / SNMP /
HTTP probe; node-exporter and the per-service reachability probe are
sampler-only with no synchronous `/test` endpoint):

```bash
for E in portainer beszel pulse webmin ping snmp http-probe oidc; do
  R=$(curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
    -H 'Content-Type: application/json' \
    -d '{}' \
    https://omnigrid.example.com/api/$E/test)
  OK=$(echo "$R" | jq -r .ok)
  echo "$E: $OK"
done
```

Sending an empty `{}` body falls back to the persisted settings — handy for
nightly health checks. Send `{url, api_key, ...}` to test unsaved values.

Additional probe endpoints follow the same shape:

- `POST /api/asset-inventory/test` — probe the upstream asset API token.
- `POST /api/apprise/test` — fire a fixed test payload through the Apprise
  medium ONLY (the broader `POST /api/notify-test` fans out to every enabled
  medium).
- `POST /api/telegram/test` — fire a fixed test payload through the Telegram
  medium ONLY.
- `POST /api/admin/ai/{provider}/test` — probe one AI provider's credentials
  + chosen model. `provider` ∈ `claude` / `gemini` / `chatgpt` / `deepseek`.

## Error handling

| Status            | Meaning                                                                                                                   |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `200`             | Success.                                                                                                                  |
| `400`             | Validation error (e.g. `endpoint_id must be an integer`). Body has `{detail}`.                                            |
| `401`             | No auth — bearer token / cookie missing or malformed.                                                                     |
| `403`             | Auth OK but role insufficient (readonly trying to write) OR CSRF token mismatch on cookie auth.                           |
| `404`             | Resource gone (e.g. container removed mid-flight). Most write ops treat this as success-shaped (idempotent).              |
| `429`             | Rate-limited (`/api/local-auth/login` after 5 fails / 15 min / IP).                                                       |
| `500`             | Server bug — the response body usually has the traceback when running with debug; otherwise `{detail: "internal error"}`. |
| `502 / 503 / 504` | Upstream provider failure (Portainer down, registry timeout). Body's `detail` usually identifies the upstream.            |

## Stable-vs-volatile API surface

Routes that are **safe to script against long-term**:

- `/api/healthz`, `/api/version`, `/metrics` — never break.
- `/api/items`, `/api/stats`, `/api/stats/history`, `/api/ops`, `/api/history` — additions only; existing fields are not removed.
- `/api/update/stack/{id}`, `/api/update/container/{id}`, `/api/restart/*`, `/api/remove/*`, `/api/prune/node/{hostname}`, `/api/swarm/restart-agent` — contract is `{op_id}` always.
- `/api/hosts/list`, `/api/hosts/one/{id}`, `/api/hosts/history`, `/api/hosts/config` — additive.
- `/api/schedules*`, `/api/backups*`, `/api/notifications*` — additive.

Routes that are **likely to grow / change shape** as the project evolves:

- `/api/hosts/discover` — provider field shapes track the active providers.
- `/api/*/test` — `detail` strings are localized free-text.
- `/api/admin/*` — admin-only, not part of the public contract.
- WebSocket endpoints (`/api/hosts/{id}/ssh/terminal`) — protocol may evolve.

If you're building production tooling, pin `/api/version` on startup and
log it; we'll publish a `MAJOR` bump with `notes/MIGRATIONS.md` whenever
any of the "stable" routes break their contract.

## Rate limiting

Only `/api/local-auth/login` is rate-limited today (5 failed attempts per
IP per 15 minutes → 15-minute lockout). Bearer-authed routes have no
per-IP / per-token limit; OmniGrid runs as a single-replica service with
in-process state, so abusive clients will hit the resource ceiling
(memory / cache thrashing) before any explicit limit. Treat `/api/items`
and `/api/stats` as "1 request every few seconds" — the SPA itself
polls these on a 15s cadence.

## Going further

- The full schema for every endpoint lives in `main.py` — every route is
  decorated with FastAPI type hints + a docstring. `https://omnigrid.example.com/docs`
  serves the auto-generated OpenAPI / Swagger UI when running locally
  (admin-only on production deploys via the `/api` middleware).
- `/api/openapi.json` returns the OpenAPI 3.1 spec — drop into
  `openapi-generator-cli` or similar for a typed client.
- Audit log of every write operation: `GET /api/history?actor=<username>&op_type=<type>`.
  Filterable by date, target, status, op kind. CSV / JSON export from the UI.
- Prometheus `/metrics` includes one counter per `op_type` and one gauge
  for cache age + per-host provider health — wire to your existing
  Grafana for long-term alerting.
