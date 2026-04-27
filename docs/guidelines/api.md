# OmniGrid API guide

How to integrate with OmniGrid programmatically — script your cluster updates,
pipe metrics into other dashboards, or build CI gates that wait for digest
parity before promoting an image.

The README's [API section](../../README.md#api-if-you-want-to-script-it) is the
quick reference; this file covers auth, error handling, common workflows, and
endpoint contracts in depth.

## Auth

Every `/api/*` route requires authentication. Three exceptions, all unauthenticated:

| Path | Purpose |
| --- | --- |
| `/api/healthz` | Liveness probe — always `200 {"status":"ok"}` if the process is alive. |
| `/api/version` | Returns `{version, git_sha}` for the running deploy. |
| `/metrics` | Prometheus exposition. (Treat as sensitive — operator stats; gate at the proxy if needed.) |

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

### Roles

| Role | Reads | Writes |
| --- | --- | --- |
| `admin` | ✓ everything | ✓ everything |
| `readonly` | ✓ everything | ✗ all `POST/PUT/PATCH/DELETE` return `403` |

The role is enforced server-side via FastAPI `Depends(auth.require_admin)` on
every write route. UI-side role gating is a UX nicety only.

## Response shape conventions

Most endpoints return JSON. Common shapes:

```json
// List endpoints
[{"id":"svc:abc123","name":"...","status":"update", ... }, ... ]

// Single-resource
{"id":"svc:abc123", ...}

// Operation kicked off (async)
{"op_id":"<uuid>", "status":"running"}

// Test / probe endpoints
{"ok":true, "detail":"OK — Portainer 2.27.4, endpoint primary reachable"}
{"ok":false, "detail":"endpoint 99 not found on this Portainer", "status":404}

// Error
{"detail":"<message>"}  // FastAPI default; HTTP code in the response status
```

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

For full telemetry on one host (cached 10s server-side):

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://omnigrid.example.com/api/hosts/one/host01" \
  | jq '.host | {host_cpu_percent, host_mem_used, host_mem_total, host_disk_used, mounts, network_ifaces}'
```

For time-series charts (1 / 6 / 24 / 168 hour windows):

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://omnigrid.example.com/api/hosts/history?host_id=host01&hours=24" \
  | jq '.series | length'
```

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
background sampler).

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

### Version (admin)

```bash
# Current MAJOR / MINOR / PATCH
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://omnigrid.example.com/api/admin/version | jq

# Direct VERSION.txt write (used by the Admin → Version page)
curl -sS -H "Authorization: Bearer $TOKEN" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"major":1,"minor":2,"patch":0}' \
  https://omnigrid.example.com/api/admin/version | jq
```

The compose file layers a writable per-file bind for `VERSION.txt`; without it, POST returns
a 500 with operator-actionable detail. See `docs/RELEASE_PROCESS.md`.

### Probe-style "does my settings work?" endpoints

Each integration has a `/test` endpoint that fires one synchronous probe with
the given (or saved) credentials. Useful for CI-style health-checking your
OmniGrid deploy:

```bash
for E in portainer beszel pulse webmin oidc; do
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

## Error handling

| Status | Meaning |
| --- | --- |
| `200` | Success. |
| `400` | Validation error (e.g. `endpoint_id must be an integer`). Body has `{detail}`. |
| `401` | No auth — bearer token / cookie missing or malformed. |
| `403` | Auth OK but role insufficient (readonly trying to write) OR CSRF token mismatch on cookie auth. |
| `404` | Resource gone (e.g. container removed mid-flight). Most write ops treat this as success-shaped (idempotent). |
| `429` | Rate-limited (`/api/local-auth/login` after 5 fails / 15 min / IP). |
| `500` | Server bug — the response body usually has the traceback when running with debug; otherwise `{detail: "internal error"}`. |
| `502 / 503 / 504` | Upstream provider failure (Portainer down, registry timeout). Body's `detail` usually identifies the upstream. |

## Stable-vs-volatile API surface

Routes that are **safe to script against long-term**:

- `/api/healthz`, `/api/version`, `/metrics` — never break.
- `/api/items`, `/api/stats`, `/api/stats/history`, `/api/ops`, `/api/history` — additions only; existing fields are not removed.
- `/api/update/stack/{id}`, `/api/update/container/{id}`, `/api/restart/*`, `/api/remove/*` — contract is `{op_id}` always.
- `/api/hosts/list`, `/api/hosts/one/{id}`, `/api/hosts/history`, `/api/hosts/config` — additive.
- `/api/schedules*`, `/api/backups*` — additive.

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
per-IP / per-token limit; the operator runs OmniGrid as a single-replica
service with in-process state, so abusive clients will hit the resource
ceiling (memory / cache thrashing) before any explicit limit. Treat
`/api/items` and `/api/stats` as "1 request every few seconds" — the SPA
itself polls these on a 15s cadence.

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
