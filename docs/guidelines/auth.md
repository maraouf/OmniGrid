# Auth runbook — OmniGrid

## What exists today

Steps 1–3 (observe → admin gate → global enforcement + login page), step 5 (user-management UI)
and step 6 (CSRF + readonly UI gating) are all live. OIDC SSO is configured from the Settings UI
(DB-backed, no env vars); see `docs/guidelines/authentik.md` for the Authentik-side
walkthrough.

### Live today

- **Tables**: `users`, `sessions`, `api_tokens` (idempotent in `init_db()`).
- **Auth routes**:

  | Method | Route                                | Purpose                                                              |
  | ------ | ------------------------------------ | -------------------------------------------------------------------- |
  | `POST` | `/api/local-auth/login`              | Step 1: username + password. Returns either `og_session` or `{totp_required, challenge_token}`. |
  | `POST` | `/api/local-auth/totp`               | Step 2: submit 6-digit code or 8-char backup code against the challenge. |
  | `POST` | `/api/local-auth/totp-setup-confirm` | First-login forced-enrol path (combined enrol + verify).             |
  | `POST` | `/api/local-auth/logout`             | Clear cookie + revoke session.                                       |
  | `POST` | `/api/local-auth/bootstrap`          | One-shot; works only when `users` is empty.                          |
  | `POST` | `/api/local-auth/change-password`    | Authed; rotates local password, keeps caller session.                |
  | `GET`  | `/api/me`                            | `{authenticated:false}` or `{username,role,source,...}`.             |
  | `GET`  | `/api/auth/providers`                | Public; login page reads this to decide whether to render SSO button. |
  | `GET`  | `/api/oidc/login`                    | Begin OIDC Authorization-Code + PKCE flow.                           |
  | `GET`  | `/api/oidc/callback`                 | IdP callback — validates id_token, mints `og_session`.               |
  | `POST` | `/api/oidc/test`                     | Admin-only; probes `{issuer}/.well-known/openid-configuration`.      |
  | `GET`  | `/login`                             | Login HTML page (`static/login.html`).                               |

- **Admin routes** (all admin-only — step 5):

  | Method  | Route                                   | Purpose                                                                                  |
  | ------- | --------------------------------------- | ---------------------------------------------------------------------------------------- |
  | `GET`   | `/api/users`                            | List every user + last-login timestamps.                                                 |
  | `POST`  | `/api/users`                            | Create local or authentik-mapped user.                                                   |
  | `PATCH` | `/api/users/{id}`                       | Change role or enable/disable.                                                           |
  | `DELETE`| `/api/users/{id}`                       | Guarded: not self, not last active admin.                                                |
  | `POST`  | `/api/users/{id}/reset-password`        | Admin-reset (local users only); invalidates their sessions.                              |
  | `POST`  | `/api/users/{id}/disable-totp`          | Admin-side disable of a user's TOTP enrolment.                                           |
  | `POST`  | `/api/users/{id}/totp-force`            | Admin-side per-user `totp_force_required` flag.                                          |
  | `GET`   | `/api/sessions`                         | List active sessions across every user.                                                  |
  | `DELETE`| `/api/sessions/{token_id}`              | Revoke one session.                                                                      |
  | `GET`   | `/api/tokens`                           | List API tokens (never returns the raw value).                                           |
  | `POST`  | `/api/tokens`                           | Create; returns the raw token ONCE.                                                      |
  | `DELETE`| `/api/tokens/{id}`                      | Revoke an API token.                                                                     |

- **Middleware** (`auth.make_auth_middleware`): resolves identity from:
  1. Bearer API token — `Authorization: Bearer og_<token>`.
  2. Local session cookie — `og_session`, HMAC-signed, server-side revocable.

  OIDC SSO users arrive via (2) — the `/api/oidc/callback` route validates the id_token, calls
  `auto_provision_authentik()` to upsert the user and map admin-group membership to role, then
  mints a normal `og_session` cookie. There is no dedicated Authentik middleware branch anymore.

- **Write routes are admin-only**: `/api/update/*`, `/api/restart/*`, `/api/remove/*`,
  `DELETE /api/history`, `POST`/`DELETE` `/api/ignores`, `POST /api/settings`,
  `POST /api/notify-test`, plus every `/api/users/*`, `/api/sessions/*`, `/api/tokens/*`, and
  `/api/schedules/*` write endpoint.
- `Operation.actor` is stamped with the real username (was hardcoded `"ui"`). `history` rows
  carry that value through the persisted event log.
- **Rate limit**: 5 failed local logins per IP in 15 min → 15 min lockout. Cleared on any
  successful login.

### Path classification the middleware applies

**Fully public** (short-circuit, no identity lookup, no CSRF):

- `/metrics`
- `/api/healthz`
- `/api/version`
- Every non-`/api` path (SPA shell, `/login`, `/css/*`, `/img/*`, `/icon-*`, `/favicon`,
  `/node_modules/*`, `/i18n/*`, etc.) — the SPA handles its own login redirect via `/api/me`.

**Auth-optional** (identity resolved but 401 is NOT raised, CSRF NOT enforced):

- `/api/local-auth/*` (login, logout, bootstrap, change-password, totp, totp-setup-confirm).
- `/api/me` (must be answerable by unauthed callers).
- `/api/oidc/*` (login, callback).
- `/api/auth/providers` (public provider discovery).

**Auth-required** (401 on missing identity, CSRF enforced — see below):

- Everything else under `/api/`.

Write routes additionally gate on `role=admin` via a `require_admin` dep. A readonly user
hitting a write route gets 403 instead of 401.

## CSRF model

Double-submit cookie. Server issues an `og_csrf` cookie alongside `og_session` at login /
bootstrap (local users) and at the OIDC callback (SSO users). The middleware also has an "issue
if missing" branch so any authed request without a CSRF cookie gets one on its way out. Cookie
is NOT `HttpOnly` so JS can read it — the SPA's global `fetch` wrapper copies it onto every
`POST`/`PUT`/`PATCH`/`DELETE` as `X-CSRF-Token`. Server compares cookie vs header with
`hmac.compare_digest`; mismatch → 403.

Exemptions:

- **Bearer-token callers** (`Authorization: Bearer og_...`) — they don't use cookies, so
  cross-origin attackers can't forge them.
- **Auth-optional endpoints**: `/api/local-auth/{login,logout,bootstrap,change-password}`,
  `/api/me`, `/api/oidc/*`, `/api/auth/providers`. Login / bootstrap run BEFORE the cookie
  exists; logout via CSRF has negligible blast radius (just logs you out); change-password
  requires the current password so is intrinsically harder to forge. This keeps the login
  page's simple form working without cookie plumbing.

## Readonly UI gating

Role-based UI, not just API enforcement. A readonly user sees:

- No Update / Restart / Remove buttons on rows, in the drawer, or in the bulk-action bar.
- No Cleanup-stopped-containers button in the topbar.
- In Settings: the "Read-only" banner at the top, input fields disabled for display-only
  inspection, Save / Add / Remove buttons hidden.
- No Admin tab in the top-nav (admin-only).

API-level enforcement (`require_admin` deps) is still the source of truth. UI gating is just
"don't make them click a button that 403s".

## OIDC + Portainer settings — DB-backed (UI-managed, no env)

Every value in the Admin → Authentik OIDC and Admin → Portainer panels lives in the
`settings` table. There are NO env vars for OIDC (no `OIDC_*` keys anywhere). Portainer
connection env vars (`PORTAINER_URL` etc.) are consulted ONCE on first boot as a transitional
aid for existing deploys, then ignored — the DB is authoritative after seeding.

- `bootstrap_auth_settings(conn)` seeds OIDC + admin-group keys with blank / disabled defaults
  on first boot.
- `bootstrap_portainer_settings(conn)` seeds Portainer keys, pulling env values when present so
  legacy deploys keep working after the UI-managed refactor.
- `get_auth_settings(conn)` / `get_portainer_settings()` read through their own in-memory
  caches so hot-path callers (middleware, `_gather`) don't re-hit SQLite on every request.
- `POST /api/settings` invalidates both caches on write; the next request picks up the new
  values with no restart.

### UI contract for secrets (`client_secret`, `portainer_api_key`)

- `GET /api/settings` returns ONLY whether it's set (`oidc.client_secret_set: true/false`,
  `portainer.api_key_set: …`), never the value itself. Avoids any browser-side leak.
- `POST /api/settings` accepts the raw secret. Empty / whitespace / omitted = "keep current";
  non-empty = overwrite. There's no "clear" button today — zero-length is treated as no-op
  (intentional; makes the form safe to save repeatedly).
- "Test connection" buttons next to each panel probe the target endpoint (OIDC discovery doc,
  Portainer `/api/status`) without saving. Handy for catching typos before flipping Enable on.

## User-facing UI that already exists

- **Top-nav "Settings"** item (view-based, not a modal) — personal settings only.
  Sidebar sections (sub-tabs):
  - **Profile** — identity (display name / email / bio / avatar / topbar widgets / appearance).
  - **Notifications** — per-user opt-in/out for the 14 op events (stack/container/service/swarm-agent
    update + restart + remove + prune, success + failure pairs) plus the security events
    (`user_login`, `host_paused`). Admin-disabled events grey out.
  - **Ignore list** — image / stack ignore patterns.
  - **Language** — UI language picker (EN / AR with RTL; more by dropping a JSON in
    `static/i18n/`).
  - **Security** — change-password card + TOTP enrolment card + Authentik passive-note for
    SSO users. (Service-wide knobs like Apprise / Portainer / OIDC are admin-only and live
    under the Admin sidebar.)
  - **Keyboard shortcuts** — hotkey reference.
- **Top-nav "Admin"** item (admin role only). Sidebar sections:
  - **General** — Open-Meteo proxy URL.
  - **Users** — create user (local or authentik), toggle role, enable/disable, admin-reset
    password, force-2FA toggle, disable a user's TOTP, delete. Destructive actions confirm
    via SweetAlert. Self-delete and last-active-admin demotion blocked.
  - **Authentication** — TOTP / 2FA policy section (master toggle + per-role required +
    lockout window). Future home for any other auth-related admin settings.
  - **Sessions** — active session list across every user with IP / UA / last-seen / expiry;
    one-click revoke.
  - **API tokens** — create named tokens with role; raw value shown ONCE in a reveal modal
    (we store SHA-256 only); revoke any token.
  - **Notifications** — global Apprise URL + tag + per-medium master toggles (`app` /
    `apprise` / `telegram`) + per-event toggles (24 events; canonical set in
    `logic.ops:NOTIFY_EVENT_NAMES`) plus per-event title + body template overrides.
    Master toggle gates dependent inputs.
  - **Portainer** — connection settings + public URL. Master toggle.
  - **Authentik OIDC** — SSO provider config. Master toggle. See `docs/guidelines/authentik.md`.
  - **Providers** (Beszel / Pulse / node-exporter / Webmin / Ping / SNMP / HTTP probe /
    per-service reachability probe — renamed from "Host stats"), **Hosts**, **Host Groups**,
    **Apps** (service catalog + pinned per-host instances), **SSH**, **Port Scan**,
    **Public IP**, **Asset inventory**, **AI integration**, **Schedules**, **Backups**,
    **Config** (process-level tunables — see `docs/guidelines/env_example.md`),
    **Config Backup**, **Logs**, **Debug**.
- **Profile modal** opened by clicking the username pill in the top-right is now retired —
  identity / password / TOTP have all moved into the Settings → Profile and Settings → Security
  sub-sections. The username pill in the topbar opens a small dropdown (avatar, role chip,
  jump links to Settings + Admin, Logout).
- Password change (Settings → Security → Change password card):
  - Rate-limited via the same per-IP limiter as `/api/local-auth/login`.
  - Invalidates every session for the user EXCEPT the caller's own `token_id`, so the user
    doesn't need to re-login immediately.
  - Rejects matching-password, <8 char passwords, and mismatched confirm.

## TOTP (2FA) for local accounts

Local-admin TOTP (Authenticator-app codes) is implemented end-to-end (`logic/totp.py`,
`pyotp` + `cryptography` Fernet for at-rest encryption keyed off `SESSION_SECRET` via HKDF,
5 additive `users.totp_*` columns).

**User enrolment** (Settings → Security → "Two-factor authentication" card):

1. Click **Enable**. The card shows a QR code + the secret in `otpauth://` form for manual
   entry into 1Password / Authenticator / etc.
2. Enter the 6-digit code from the app to confirm. On success, the card flips to "reveal"
   mode, showing 10 single-use backup codes — **shown ONCE**, store them somewhere safe.
3. Card shows a **Regenerate codes** action (re-display + invalidate the old set) and a
   **Disable** action (requires the user's password).

**Admin tools** (Admin → Users):

- 2FA column shows per-row status: `none` / `enabled` / `required` (required = global policy
  or per-user force flag is on but the user hasn't enrolled yet — the next login WILL be
  blocked at the TOTP challenge step until they enrol).
- Per-row **Disable 2FA** (admin-side disable; user retains nothing — the next time they hit
  the TOTP screen they're back to enrolment). Used when a user loses both their authenticator
  and their backup codes.
- Per-row **Force 2FA** / **Unforce** toggles the per-user `totp_force_required` flag. ORs
  with the global per-role policy.

**Admin policy** (Admin → Authentication):

- **Allow TOTP for local users** — master toggle. Off disables the entire TOTP code path
  globally; existing enrollments stay in the DB but are bypassed at login.
- **Require TOTP for admins** / **Require TOTP for users** — per-role policy. Forces enrolment
  on next login.
- **Lockout after N failures** + **Lockout window in minutes** — protects against brute-force
  on the 6-digit space. Defaults: 5 failures / 15 minutes.

**Login flow with TOTP**:

```
POST /api/local-auth/login {username, password}
  → 200 {totp_required: true, challenge_token: "..."}
POST /api/local-auth/totp {challenge_token, code}   // 6-digit OR 8-char backup code
  → 200 + sets og_session
```

The `challenge_token` is held in the in-memory `_totp_challenges` cache with a 5-minute TTL.
A second password-correct call before challenge resolution invalidates the previous challenge.
Authentik users skip every TOTP path (Authentik handles MFA). Bearer-token requests bypass.
Every state change (`enable_start` / `enable_confirm` / `disable` / `verify_pass` /
`verify_fail` / `lockout` / `force_set` / `admin_disable`) writes a `[totp]` log line and a
`history` row.

**Endpoints** (auth runbook scope; full schema in `main.py`):

| Method  | Route                                  | Purpose                                                          |
| ------- | -------------------------------------- | ---------------------------------------------------------------- |
| `POST`  | `/api/local-auth/totp`                 | Submit a TOTP code for the active challenge_token.               |
| `POST`  | `/api/local-auth/totp-setup-confirm`   | First-login forced-enrol path (enrol + verify in one call).      |
| `GET`   | `/api/me/totp`                         | Current user's TOTP state (enabled / required / backup-code count). |
| `POST`  | `/api/me/totp/enroll-start`            | Returns secret + QR `otpauth://` URI.                            |
| `POST`  | `/api/me/totp/enroll-confirm`          | Verifies the first code; mints backup codes.                     |
| `POST`  | `/api/me/totp/regenerate-codes`        | Rotates backup codes.                                            |
| `POST`  | `/api/me/totp/disable`                 | User self-disable (requires password).                           |
| `POST`  | `/api/users/{id}/disable-totp`         | Admin-side disable.                                              |
| `POST`  | `/api/users/{id}/totp-force`           | Admin-side per-user force flag.                                  |

## Password flows

| Flow                | Route                                          | Notes                                                                                                     |
| ------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Self-service change | `POST /api/local-auth/change-password`         | Validates the current password first; rate-limited. Keeps caller session, invalidates every other one.    |
| Admin reset         | `POST /api/users/{id}/reset-password`          | No current-password check (admin authority). Local users only — Authentik users are rejected 400. Invalidates every session for the target user. |

## Session mechanics

- **Cookie**: `og_session`, `HttpOnly`, `Secure` (when `X-Forwarded-Proto: https` is seen from
  NPM), `SameSite=Lax`, `Path=/`.
- **HMAC-signed payload**: `<token_id>.<expires_at>.<hmac_sha256>`.
- **Server-side row** in the `sessions` table holds `user_id` + IP + UA + expiry. Logout
  deletes the row. Admin revoke deletes by `user_id`.
- **Lifetime**: 8h hard cap, sliding — reissued on any authed request when remaining life
  drops below 1h.
- Rotating `SESSION_SECRET` invalidates every session on next request.

## How the SPA handles auth

`static/index.html` does four things:

1. A global `fetch()` wrapper is installed BEFORE Alpine init. Any 401 from any API call
   redirects the browser to `/login?next=<current-path>`.
2. `init()` calls `/api/me` first. If `{authenticated:false}`, redirect; else store the user
   + role on the Alpine state as `me`. The same call also surfaces `bootstrap_env_still_set`
   (banner-warning when `BOOTSTRAP_ADMIN_USER` / `BOOTSTRAP_ADMIN_PASSWORD` are still in env
   AND users exist) and `notify_events` / `notify_events_admin` (per-user opt-in/out + admin
   gate snapshot).
3. The header's top-right is a username pill (`username · role`) that opens a small
   dropdown — links to Settings + Admin (admin only) and Logout. Logout posts to
   `/api/local-auth/logout` then navigates to `/login`. The old "profile modal" is gone;
   identity / password / TOTP all live as Settings → Profile / Settings → Security cards.
4. "Settings" and "Admin" are top-nav views (paths `/settings/<section>` and
   `/admin/<tab>`, persisted via Alpine state). Both render a sidebar (sub-tab list) and a
   content area that opens with a page-title bar (the active sub-tab's icon + label) so the
   icon is consistent end-to-end: avatar dropdown → sidebar entry → page-title bar all share
   the same symbol. The filter-chip bar hides on these views (same treatment as History).

No middleware change is needed for OIDC login — the callback route mints the same `og_session`
cookie local logins use, so every downstream handler behaves identically for local and OIDC
users.

## How to verify after deploy

```bash
# Unauthenticated read → 401
curl -sS -o /dev/null -w "%{http_code}\n" https://omnigrid.<host>/api/items
# → 401

# Public endpoints still open
curl -sS -o /dev/null -w "%{http_code}\n" https://omnigrid.<host>/api/healthz
# → 200
curl -sS -o /dev/null -w "%{http_code}\n" https://omnigrid.<host>/api/version
# → 200

# Login → 200 with cookies
curl -sS -c /tmp/c -X POST https://omnigrid.<host>/api/local-auth/login \
  -d 'username=admin' -d 'password=<your-pw>'

# Authed read → 200
curl -sS -b /tmp/c https://omnigrid.<host>/api/items | jq '.items | length'

# OIDC status appears in /api/settings (client secret is NEVER returned)
curl -sS -b /tmp/c https://omnigrid.<host>/api/settings | jq '.oidc'
# → {"enabled":false,"issuer_url":"","client_id":"",
#    "client_secret_set":false,"redirect_uri":"",
#    "redirect_uri_default":"https://.../api/oidc/callback",
#    "scopes":"openid email profile groups",
#    "admin_group":"omnigrid-admins"}

# Portainer connection status (api_key is never returned)
curl -sS -b /tmp/c https://omnigrid.<host>/api/settings | jq '.portainer'
# → {"url":"https://portainer.example.com:8443",
#    "endpoint_id":19,"verify_tls":false,
#    "api_key_set":true,"configured":true}

# OIDC login flow — before config: 503; after config: 302 to Authentik
curl -sS -o /dev/null -w "%{http_code}\n" https://omnigrid.<host>/api/oidc/login
# → 503 (before config) or 302 (after config)

# Self-service password change (browser: top-right username → profile modal)
curl -sS -b /tmp/c -X POST \
  https://omnigrid.<host>/api/local-auth/change-password \
  -d 'current_password=<old>' \
  -d 'new_password=<new-strong>' \
  -d 'confirm_password=<new-strong>'
# → {"status":"ok"}
# All OTHER sessions for this user are now invalidated; /tmp/c still works.
```

Browser: visiting the site while unauthenticated redirects to `/login`. After logging in, the
user badge appears in the top-right of the header. Clicking it opens the profile modal; clicking
"Settings" in the nav opens the settings view (no overlay). "Admin" tab appears for admins.

## Step-5 verification (user / session / token management)

```bash
# List users
curl -sS -b /tmp/c https://omnigrid.<host>/api/users | jq '.users'

# Create a readonly user
curl -sS -b /tmp/c -X POST https://omnigrid.<host>/api/users \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","role":"readonly","auth_source":"local","password":"changeme123"}'
# → {"ok":true,"id":2,"username":"alice","role":"readonly"}

# Promote to admin
curl -sS -b /tmp/c -X PATCH https://omnigrid.<host>/api/users/2 \
  -H 'Content-Type: application/json' -d '{"role":"admin"}'

# Last-admin guard: trying to demote the only admin → 400
curl -sS -b /tmp/c -X PATCH https://omnigrid.<host>/api/users/1 \
  -H 'Content-Type: application/json' -d '{"role":"readonly"}'
# → {"detail":"Cannot demote or disable the last active admin."}  (if only 1 admin)

# Admin-reset another user's password
curl -sS -b /tmp/c -X POST https://omnigrid.<host>/api/users/2/reset-password \
  -H 'Content-Type: application/json' -d '{"new_password":"newstrongpw1"}'

# Active sessions
curl -sS -b /tmp/c https://omnigrid.<host>/api/sessions | jq '.sessions | length'

# Create API token (raw returned ONCE)
curl -sS -b /tmp/c -X POST https://omnigrid.<host>/api/tokens \
  -H 'Content-Type: application/json' -d '{"name":"homarr","role":"readonly"}'
# → {"ok":true,"name":"homarr","role":"readonly","token":"og_xxxx..."}

# Use the token
curl -sS -H 'Authorization: Bearer og_xxxx...' https://omnigrid.<host>/api/items | jq '.items | length'
```

## OIDC + Portainer settings verification (UI-managed, DB-backed)

```bash
# Initial state on a fresh deploy: Portainer seeded from env if set,
# OIDC blank/disabled.
curl -sS -b /tmp/c https://omnigrid.<host>/api/settings | jq '.oidc,.portainer'

# Configure OIDC (UI: Admin → Authentik OIDC). The client secret
# follows the "blank = keep current" contract so the form is safe to
# save repeatedly.
CSRF=$(grep og_csrf /tmp/c | awk '{print $NF}')
curl -sS -b /tmp/c -H "X-CSRF-Token: $CSRF" -X POST \
  https://omnigrid.<host>/api/settings \
  -H 'Content-Type: application/json' \
  -d '{
    "oidc_enabled": true,
    "oidc_issuer_url": "https://authentik.example.com/application/o/omnigrid/",
    "oidc_client_id": "REPLACE-ME",
    "oidc_client_secret": "REPLACE-ME",
    "oidc_admin_group": "omnigrid-admins"
  }'
# → {"status":"ok"}

# Public providers endpoint — login page reads this
curl -sS https://omnigrid.<host>/api/auth/providers
# → {"local":true,"oidc":true}

# Test OIDC discovery (admin-only, no state changes)
curl -sS -b /tmp/c -H "X-CSRF-Token: $CSRF" -X POST \
  https://omnigrid.<host>/api/oidc/test \
  -H 'Content-Type: application/json' \
  -d '{"issuer_url":"https://authentik.example.com/application/o/omnigrid/"}'
# → {"ok":true,"status":200,"detail":"OK — issuer: https://..."}

# Configure Portainer from the UI (Admin → Portainer connection).
# Same "blank api_key = keep current" contract.
curl -sS -b /tmp/c -H "X-CSRF-Token: $CSRF" -X POST \
  https://omnigrid.<host>/api/settings \
  -H 'Content-Type: application/json' \
  -d '{
    "portainer_url": "https://portainer.example.com:8443",
    "portainer_api_key": "ptr_REPLACE-ME",
    "portainer_endpoint_id": 19,
    "portainer_verify_tls": false
  }'
# Dashboard populates on next /api/items (cache invalidated by the POST).
```

## Step-6 verification (CSRF + readonly UI)

```bash
# Cookie-authed write WITHOUT an X-CSRF-Token header → 403
curl -sS -b /tmp/c -X POST https://omnigrid.<host>/api/notify-test
# → 403 {"detail":"CSRF token mismatch"}

# Same call WITH the header → 200
CSRF=$(grep og_csrf /tmp/c | awk '{print $NF}')
curl -sS -b /tmp/c -H "X-CSRF-Token: $CSRF" -X POST https://omnigrid.<host>/api/notify-test
# → {"status":"sent"}

# Bearer-token write is exempt — no CSRF header needed
curl -sS -H 'Authorization: Bearer og_xxx...' -X POST \
  https://omnigrid.<host>/api/notify-test
# → {"status":"sent"}  (no X-CSRF-Token sent; bearer is exempt)
```

Browser smoke test for readonly gating:

1. As admin, create a readonly user (Admin → Users → Create).
2. Open an incognito window, log in as that user.
3. Confirm: no Admin tab in nav, no action buttons on rows, no bulk bar when selecting items,
   Settings shows the read-only banner with disabled inputs and no Save buttons.

## How to seed the first admin — three ways

You cannot get locked out by step 1 (nothing is gated). But you'll want an admin in place BEFORE
step 2/3 lands. Pick one:

### 1. Env-var bootstrap (recommended for CI-driven deploys)

Set `BOOTSTRAP_ADMIN_USER` and `BOOTSTRAP_ADMIN_PASSWORD` in `.env` at the repo root, commit,
and push. On first boot with an empty `users` table, OmniGrid creates that admin and logs:

```
[auth] Seeded bootstrap admin '<name>'. Change password after first login.
```

The seed code is a no-op on every subsequent boot (gated on empty `users` table). After first
login, change the password from the UI (Admin → Users), then blank both values in `.env` and
push a follow-up commit so the credential doesn't linger in the running container's env.

### 2. One-shot endpoint (good for ad-hoc / emergency)

Ship with no bootstrap env vars, then from any network that can reach OmniGrid:

```bash
curl -sS -X POST https://omnigrid.<host>/api/local-auth/bootstrap \
  -d "username=admin" -d "password=<strong-pw>"
```

Returns 201 + sets session cookie. Subsequent calls return 403 because the `users` table is no
longer empty.

### 3. Direct SQL (true break-glass)

Use only if `SESSION_SECRET` was lost and sessions can't be minted, or the API is unreachable.
The deploy target is a Debian 13 VM (hostname `docker.example.com`, user `pi` — just a unix
username, not a Raspberry Pi).

```bash
ssh pi@docker.example.com
# Hash inside the container — bcrypt ships with the app.
CID=$(docker ps -qf name=omnigrid_omnigrid)
HASH=$(docker exec -it $CID python3 -c \
  "import bcrypt; print(bcrypt.hashpw(b'YOUR-PW', bcrypt.gensalt(12)).decode())")
# Write on the host against the bind-mounted DB — the container is
# python:3.14-slim and has no sqlite3 CLI.
sudo sqlite3 /opt/omnigrid/data/omnigrid.db \
  "INSERT INTO users(username,role,auth_source,password_hash,created_at) \
   VALUES('admin','admin','local','$HASH',strftime('%s','now'));"
```

## Race-condition gotcha

`/api/local-auth/bootstrap` is public by design — you can't auth-gate the first-admin creation
without a chicken-and-egg. If OmniGrid is internet-exposed at the moment of first deploy, a
racer could claim the bootstrap endpoint before you do.

Mitigations (pick one):

- **PREFERRED**: seed via env vars (method #1). The endpoint self-disables immediately on first
  boot, before any network traffic reaches it.
- Deploy behind LAN/VPN first, claim bootstrap, then expose via NPM.
- Temporarily block `/api/local-auth/bootstrap` at NPM for the first few minutes of exposure.

## Secret storage: `.env` tracked in git, app loads it at startup

This repo is private (self-hosted Git), so `.env` is committed at the repo root alongside
`main.py` / `logic/auth.py` and ships via the normal CI rsync pipeline to
`/opt/omnigrid/app/.env` on the manager. After the image-build deploy migration the
`.env` is no longer baked into the image — it rides a per-file bind mount declared in
`docker-compose.yml` (`/opt/omnigrid/app/.env:/app/.env:ro`), so secrets stay host-controlled
while the application code lives inside the image. `main.py`'s first lines load
`/app/.env` via `python-dotenv` before any `os.getenv()` runs. Compose doesn't use `env_file:`
— the app reads its own config file, same pattern as adguardhome-sync / many other stacks.

We avoid Compose's `env_file:` because Portainer's web-editor stacks can't resolve relative
paths against the host filesystem (they'd look inside Portainer's own container at
`/data/compose/<stack-id>/…`). Letting the app read its own file sidesteps Portainer's parser
entirely.

**Override precedence**: values set in `docker-compose.yml`'s `environment:` block would win over
values in `/app/.env` (`load_dotenv` is called with `override=False`), but in practice the
compose has NO `environment:` block — every tunable, including `DB_PATH`, flows from `.env`.

Why not the "gitignored, hand-managed on the server" pattern?

- Keeps secrets in the same version control as the code that reads them, so schema changes +
  env changes land atomically.
- CI ships a new `.env` on push — no out-of-band manual step.
- Only viable because the repo is private. **Do NOT enable public mirroring.**

What lives in `.env` (reference with inline docs: `docs/guidelines/env_example.md`):

- `PORTAINER_URL`, `PORTAINER_API_KEY`, `PORTAINER_ENDPOINT_ID`, `VERIFY_TLS` — OPTIONAL
  bootstrap only. Seeded into the DB on first boot; after that Admin → Portainer wins and
  env is ignored. Fresh deploys can leave these blank.
- `CACHE_TTL_SECONDS`, `STATS_CACHE_TTL_SECONDS`, `REGISTRY_CONCURRENCY`, `STATS_CONCURRENCY`,
  `STATS_HISTORY_DAYS`, `STATS_SAMPLE_INTERVAL_SECONDS`.
- `DB_TYPE` (default `sqlite`; supported set lives in `logic/db.py:_SUPPORTED_DB_TYPES`).
- `DB_PATH` (inside the container; the host bind mount maps to `/opt/omnigrid/data`).
- Optional `DOCKERHUB_USER` / `DOCKERHUB_TOKEN`.
- `SESSION_SECRET` (required for prod; auto-generated if unset).
- `BOOTSTRAP_ADMIN_USER` / `BOOTSTRAP_ADMIN_PASSWORD` (first boot only).
- `ENV_FILE_PATH` — override if `.env` isn't at `/app/.env`.

NOT in `.env` (UI-only): every OIDC setting, every host-stats provider credential
(Beszel / Pulse / node-exporter / Webmin / Ping / SNMP / HTTP probe /
per-service reachability probe — the eight providers shipped today), the
weather proxy URL (`open_meteo_url`), all SSH runner settings (including `ssh_fqdn_suffix`,
`ssh_custom_actions`, and the write-only key / password fields with their `clear_*` unset
flags), every Telegram setting (bot token, chat id, listener toggle), and the
scheduler timezone. There are no `OIDC_*`, `BESZEL_*`, `PULSE_*`, `WEBMIN_*`,
`PING_*` (provider credentials — distinct from `PING_INTERVAL_SECONDS` etc. tunables), `SNMP_*`
(provider credentials — distinct from `SNMP_PROBE_TIMEOUT_SECONDS` etc.), `HTTP_PROBE_*`
(provider credentials — distinct from `HTTP_PROBE_TIMEOUT_SECONDS` etc.),
`SERVICE_PROBE_*` (same distinction), `TELEGRAM_*` (provider credentials —
distinct from `TELEGRAM_LONG_POLL_TIMEOUT_SECONDS` etc.), or `SSH_*` env vars
beyond the tunables. Configure from Admin → Authentik OIDC / Admin → Portainer /
Admin → Providers / Admin → SSH / Admin → Notifications after first admin login.

## `SESSION_SECRET` rotation

Regenerating `SESSION_SECRET` and pushing invalidates every active session — useful as a nuclear
kick-everyone-out if you suspect compromise. Generate:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Paste the output into `.env`, commit, push. Users re-authenticate.

## Initial server setup (do once)

The deploy target is a Debian 13 VM (amd64, 16 GB / 100 GB) reachable at `pi@docker.example.com`.
Image-build deploy — `/opt/omnigrid/app/` is the rsynced build context, NOT a runtime
bind mount. The directory tree is created once on initial setup; every subsequent change
ships via CI which rsyncs the build context, runs `docker build` on the manager, and
rolls the new tag in.

```bash
ssh pi@docker.example.com
sudo mkdir -p /opt/omnigrid/{app,data}
sudo chown -R pi:pi /opt/omnigrid
# Pre-create the .env BEFORE the first deploy — the image deliberately does NOT bake it
# (it's in .dockerignore); secrets ride a per-file bind mount in docker-compose.yml.
scp .env pi@docker.example.com:/opt/omnigrid/app/.env
ssh pi@docker.example.com 'chmod 600 /opt/omnigrid/app/.env'
# Trigger the first CI deploy by pushing to main, OR build locally on the manager:
cd /opt/omnigrid/app
docker build --build-arg VERSION=1.0.0 -t omnigrid:1.0.0 -t omnigrid:latest .
docker stack deploy --resolve-image=always --compose-file docker-compose.yml omnigrid
```

Subsequent pushes to main rebuild the image, push it to the configured registry, and force
the running service onto the new tag. See `docs/guidelines/deploy.md` for the full pipeline
runbook.

The SQLite database lives at `/opt/omnigrid/data/omnigrid.db` on the host (mounted into the
container at `/app/data/omnigrid.db` via the `/opt/omnigrid/data:/app/data` bind in
`docker-compose.yml`).

## Env var reference

| Var                           | Purpose                                                                                                                                                  |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SESSION_SECRET`              | (required for prod; auto-generated if unset). HMAC key for session cookies. Rotate to kick every logged-in user. Keep stable otherwise.                  |
| `PORTAINER_URL`               | (optional bootstrap — UI is authoritative). Seeded into the DB on first boot; after that Admin → Portainer wins. Fresh deploys can leave this blank. |
| `PORTAINER_API_KEY`           | Same bootstrap rules as above.                                                                                                                           |
| `PORTAINER_ENDPOINT_ID`       | Same bootstrap rules as above.                                                                                                                           |
| `VERIFY_TLS`                  | Same bootstrap rules as above.                                                                                                                           |
| `DB_TYPE`                     | (optional). Database backend selector. Default `sqlite`; an unrecognised value surfaces a config-error page rather than crash-looping.                   |
| `BOOTSTRAP_ADMIN_USER`        | (optional, first-boot only). Only consulted when the `users` table is empty. Creates one admin, then self-disables.                                      |
| `BOOTSTRAP_ADMIN_PASSWORD`    | Same first-boot rules as above.                                                                                                                           |

No `OIDC_*` env vars. OIDC is UI-managed only.

## How to verify after ship

```bash
# Claim first admin via one-shot endpoint (no env bootstrap set):
curl -sS -X POST https://omnigrid.<host>/api/local-auth/bootstrap \
  -d "username=admin" -d "password=<strong-pw>"

# Login (follows redirects, keeps cookies):
curl -sS -c cookies.txt -X POST https://omnigrid.<host>/api/local-auth/login \
  -d "username=admin" -d "password=<your-pw>"

# Confirm identity resolved by middleware:
curl -sS -b cookies.txt https://omnigrid.<host>/api/me
# → {"authenticated":true,"username":"admin","role":"admin","source":"local"}

# Fail a few to watch the rate limiter kick in:
for i in 1 2 3 4 5 6; do
  curl -sS -o /dev/null -w "%{http_code}\n" -X POST \
    https://omnigrid.<host>/api/local-auth/login \
    -d "username=admin" -d "password=wrong"
done
# → 401 401 401 401 401 429  (Retry-After: 900)
```

## Schema reference

```sql
users(
  id, username UNIQUE, email, password_hash, role,
  auth_source IN ('local','authentik'), disabled, created_at, last_login_at
)
sessions(
  token_id PK, user_id, issued_at, last_seen_at, expires_at, ip, user_agent
)
api_tokens(
  id, name UNIQUE, token_hash, role, created_at, last_used_at, created_by
)
```

Lifetimes: 8h session hard cap, sliding window (re-issues cookie when the remaining life drops
under 1h on any authed request). Logout deletes the row; password change deletes all rows for
that user.

## Operator checklist (per deploy)

- [ ] `.env` at repo root is up to date (not blank `SESSION_SECRET`).
- [ ] Repo visibility is still PRIVATE (never flip this on without rotating every secret in
      `.env` first — the git history would leak them).
- [ ] First admin exists (via `BOOTSTRAP_ADMIN_*` or one-shot bootstrap).
- [ ] Portainer connection configured (either via env bootstrap on first boot or via Settings →
      Portainer after login).
- [ ] When enabling OIDC:
    - [ ] Authentik OIDC provider + application configured and bound to the `omnigrid-admins`
          group (see `docs/guidelines/authentik.md`).
    - [ ] Admin → Authentik OIDC filled in (issuer URL, client ID, client secret, redirect
          URI pasted into Authentik's allowlist).
    - [ ] "Test connection" returns ✓.
    - [ ] Enable toggle on, Save.
    - [ ] SSO button appears on `/login`.
