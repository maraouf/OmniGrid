# Authentik OIDC integration — OmniGrid

## What this does

Adds single-sign-on via Authentik alongside local username/password — both auth methods work
simultaneously, no lockout of the local path. Admin vs readonly role for SSO users is driven by
Authentik group membership; local users keep whatever role their admin assigned in the Users UI.

**Mechanism**: OmniGrid implements Authorization-Code + PKCE (S256) against the issuer's OIDC
endpoints. The browser visits `/api/oidc/login`, gets redirected to Authentik, approves, lands
back at `/api/oidc/callback` which validates the id_token (signature vs JWKS, issuer, audience,
nonce, exp) and mints a normal OmniGrid session cookie. From there the user is
indistinguishable from a local login.

No reverse-proxy gymnastics. No Forward Auth outpost. No NPM custom Nginx config. The old
"Forward Auth" path was removed — this doc is OIDC-only from here on.

**Coexistence model**:

- Local users keep logging in at `/login` with username + password.
- OIDC users click the "Sign in with Authentik" button that appears below the local form once
  OIDC is configured.
- A local break-glass admin is still recommended for the case where Authentik is down or
  misconfigured.

**Configuration storage**: every OIDC setting (issuer URL, client ID, client secret, redirect
URI, scopes, admin group, enable toggle) lives in the `settings` table and is edited from
Admin → Authentik OIDC. There are no `OIDC_*` env vars. Restart-free updates — the settings
cache invalidates on `POST /api/settings`.

## What you need in place before you start

- [ ] Authentik running somewhere you can admin. This guide was tested against Authentik
      2024.10; UI paths may shift on newer versions, but the concepts are stable.
- [ ] OmniGrid deployed with local admin login working (first-admin bootstrap done — see
      `docs/guidelines/auth.md`).
- [ ] Admin access to both UIs.

Values you need in hand:

- OmniGrid public URL — e.g. `https://omnigrid.example.com/`.
- Authentik public URL — e.g. `https://authentik.example.com/`.
- OmniGrid admin login you're using for the pre-SSO phase.

## Step 1 — Create the admin group in Authentik

Directory → Groups → Create.

The "New Group" dialog fields (Authentik 2024.10+):

| Field                | Value / note                                                                                                                                                   |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Name                 | `omnigrid-admins`                                                                                                                                              |
| Superuser Privileges | OFF (leave the toggle dark — this is Authentik's own admin flag and is NOT what OmniGrid checks. OmniGrid cares only that the user is a member of this group.) |
| Parents              | empty (leave both lists alone — we want a top-level group. Parents are for nested inheritance, not needed here.)                                               |
| Roles                | empty (Authentik roles are its internal RBAC for the Authentik UI itself; OmniGrid doesn't read them.)                                                         |

Click Create Group.

Then add members: open the new group → Users tab → "Add existing user" → pick yourself (and
anyone else who should be admin in OmniGrid).

The group name is what OmniGrid compares against. Default is `omnigrid-admins` but you can
change it later. Anyone NOT in this group who logs in via OIDC becomes a readonly user
automatically — still useful, just can't update/restart/remove anything.

## Step 2 — Create the OAuth2 / OpenID Connect provider in Authentik

Applications → Providers → Create → OAuth2 / OpenID Provider.

Note the provider type: this is NOT a proxy provider. We do a pure OIDC authorization-code
flow — the browser talks straight to OmniGrid, Authentik is just the IdP.

Fields:

| Field                              | Value                                                                                                                                                                                                                                      |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Name                               | `OmniGrid — OIDC Provider`                                                                                                                                                                                                                 |
| Authorization flow                 | `default-provider-authorization-implicit-consent` (or `…-explicit-consent` if you want users to see an approval screen on first login)                                                                                                     |
| Authentication flow                | `default-authentication-flow`                                                                                                                                                                                                              |
| Client type                        | Confidential                                                                                                                                                                                                                               |
| Client ID                          | (auto-generated by Authentik — copy this)                                                                                                                                                                                                  |
| Client Secret                      | (auto-generated by Authentik — copy this)                                                                                                                                                                                                  |
| Redirect URIs / Origins (RegEx)    | `https://omnigrid.example.com/api/oidc/callback` ← paste the **exact** URL OmniGrid shows in Admin → Authentik OIDC → Redirect URI (there's a Copy button right there). One per line; no trailing slash; case-sensitive.                   |
| Signing Key                        | `authentik Self-signed Certificate` (the default)                                                                                                                                                                                          |
| Subject mode                       | hashed user ID (default)                                                                                                                                                                                                                   |
| Include claims in id_token         | ON                                                                                                                                                                                                                                         |

**Scopes**. Add at least:

- `openid`
- `email`
- `profile`

Plus a scope that emits the `groups` claim. In Authentik 2024.10 this is usually shown as:

- `goauthentik.io/providers/oauth2/scope-groups` (or simply `groups` on newer builds).

If unsure: save the provider, log in once, decode the id_token at [jwt.io](https://jwt.io/), and
confirm the `groups` claim is present. If not, add the groups scope and try again.

Save.

## Step 3 — Create the application bound to the provider

Applications → Applications → Create.

| Field             | Value                                                                                                                                   |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Name              | `OmniGrid`                                                                                                                              |
| Slug              | `omnigrid`                                                                                                                              |
| Provider          | The OIDC provider you just made (Step 2)                                                                                                |
| Launch URL        | `https://omnigrid.example.com/`                                                                                                         |
| Icon              | Optional — grab `static/img/logo/omnigrid.png` from the OmniGrid repo if you want the tile to look right on the Authentik launchpad.    |
| Open in new tab   | Usually off; your call.                                                                                                                 |

Save.

Now restrict access (so only your homelab users can see it):

1. Open the application → Policy / Group / User bindings tab → Create binding → Group.
2. Add at least:
   - `omnigrid-admins` (the one from Step 1).
   - Any other group(s) whose members should get readonly access.

If you leave the application with no bindings Authentik treats it as "everyone with an Authentik
account". That's fine for a small homelab but explicit bindings are safer.

## Step 4 — Wire the provider into OmniGrid

Log into OmniGrid as a local admin and open:

Top-nav → **Admin** → **Authentik OIDC**.

Fields:

| Field                      | Value / note                                                                                                                                                                                                                                                                           |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Enable OIDC login**      | Check this on last, after the other fields validate.                                                                                                                                                                                                                                   |
| Issuer URL                 | `https://authentik.example.com/application/o/omnigrid/` — from the Authentik provider's Metadata tab — copy the "OpenID Configuration Issuer" value. Trailing slash is fine either way. OmniGrid appends `/.well-known/openid-configuration` internally.                               |
| Client ID                  | Paste Authentik's generated value.                                                                                                                                                                                                                                                     |
| Client secret              | Paste Authentik's generated value. Write-only — once saved we never show it again, only `(configured)`.                                                                                                                                                                                |
| Redirect URI               | Read-only, with a Copy button. Computed from the request origin. Paste into Authentik's Redirect URIs allowlist (Step 2) exactly.                                                                                                                                                      |
| Scopes                     | `openid email profile groups`. Space-separated. The `groups` scope is required for role mapping — see the scope notes in Step 2 for Authentik-version variance.                                                                                                                        |
| Admin group                | `omnigrid-admins` (or whatever you called it in Step 1 — must match exactly).                                                                                                                                                                                                          |

Click "Test connection" — this GETs `{issuer}/.well-known/openid-configuration` and shows ✓ / ✗.
If ✗, fix the issuer URL before flipping Enable on.

Now check "Enable OIDC login" and click Save. The "Sign in with Authentik" button appears on
`/login` within a second or two (the login page queries `/api/auth/providers` on load).

Because OIDC settings are DB-backed and the auth-settings cache is invalidated on
`POST /api/settings`, no restart is required — the next inbound request picks up the new
provider config.

## Step 5 — Smoke test (both auth paths)

### Test A — OIDC path

Open an incognito window — otherwise a lingering local-login cookie will hide what's actually
happening.

1. Visit `https://omnigrid.example.com/`.
2. SPA redirects you to `/login`.
3. Click "Sign in with Authentik". You bounce to Authentik, log in (or pass through if your
   Authentik session is already live), hit the consent screen (if using explicit-consent), click
   Approve.
4. You land back in OmniGrid already logged in. Top-right pill shows your username; role chip
   says `admin` (because you're in `omnigrid-admins`).
5. curl confirmation (from any authed session cookie):

```bash
curl -sS -b /tmp/c https://omnigrid.example.com/api/me | jq
# {
#   "authenticated": true,
#   "username":      "you@example.com",
#   "role":          "admin",
#   "source":        "authentik"
# }
```

`source=authentik` confirms the OIDC path is live. If it says `local` you hit the
username/password form instead — check the button appeared on `/login` and you clicked it.

### Test B — Local login path

1. Open ANOTHER incognito window.
2. Visit `https://omnigrid.example.com/`.
3. Enter a local admin's username + password. You land in OmniGrid with `source=local`.

```bash
curl -sS -b /tmp/c https://omnigrid.example.com/api/me | jq
# { "username":"admin", "role":"admin", "source":"local" }
```

### Test C — Readonly via OIDC

1. Log into Authentik as a user NOT in `omnigrid-admins` but IS bound to the application.
2. Sign in via OmniGrid's OIDC button — lands with `role=readonly`.
3. No Update / Restart / Remove buttons; no Admin tab; Settings shows the read-only banner with
   disabled inputs.
4. In Authentik, add that user to `omnigrid-admins`.
5. Log out of OmniGrid and sign back in via OIDC — role chip flips to `admin`. Unlike the old
   Forward Auth flow, OIDC re-evaluates group membership on each login, not each request — a
   user already signed in keeps their old role until their session expires or they log out and
   back in.

## Step 6 — Recommended: keep at least one local admin

Even though local login coexists with OIDC as a first-class path, it's still wise to keep one
local admin around:

- If Authentik is down, local users can still log in at `/login`.
- If OIDC is misconfigured after a change (wrong secret, bad redirect URI), disable the toggle
  from the Settings panel and everything keeps working via local passwords.
- The "ensure at least one active admin exists" guards in the Users UI refuse to demote /
  disable / delete the last admin, so you can't accidentally orphan the system.

Pattern that works well for small teams:

- One local admin (break-glass).
- Human day-to-day users provisioned via Authentik.
- One or two API tokens (Admin → API tokens) for scripts / exporters that shouldn't depend on
  either login UI.

## Troubleshooting

### "Test connection" returns ✗ with HTTP 404

The issuer URL is wrong. In Authentik → Providers → your OIDC provider → Metadata tab → copy
"OpenID Configuration Issuer" verbatim into the Settings form.

### Clicking "Sign in with Authentik" works, consent granted, but the callback shows "OIDC token exchange failed: HTTP 401"

- Wrong `client_secret`. Authentik regenerates it if you click the "Rotate Client Secret" button
  — copy the new value into Admin → Authentik OIDC → Client secret and Save.
- Could also be a `client_id` mismatch — double-check both.

### Callback 400s with "redirect_uri mismatch" (or Authentik shows its own error page before ever hitting OmniGrid)

- The Redirect URI field in Authentik's provider MUST contain the **exact** URL shown in
  OmniGrid's Admin → Authentik OIDC → Redirect URI field, character-for-character. Hit the Copy button in
  OmniGrid, paste into Authentik, Save the provider.
- Common cause: trailing slash on one side only.
- Second common cause: scheme mismatch (http vs https) — check the proxy is passing
  `X-Forwarded-Proto` so OmniGrid computes `https` in the Settings display.

### "id_token validation failed: Signature verification failed"

- Clock skew between OmniGrid and Authentik. We tolerate 30 s of skew via `leeway=30`; beyond
  that, sync NTP on both hosts.
- Also possible: you pointed the Issuer URL at one Authentik instance but the token was signed
  by a different one. Check both Authentik URLs are the same host.

### Users land with role=readonly even when they're in the admin group

- The `groups` scope isn't emitting anything the id_token carries. Step 2's scope notes have
  the Authentik-version-variance; the easiest confirmation is to log in once, grab the id_token
  from the session's debug output (or watch the network tab), paste into [jwt.io](https://jwt.io/),
  and check whether the `groups` claim is present. If it isn't, add/tick the groups scope on
  the provider.
- Second cause: the admin group name doesn't match. OmniGrid's Admin → Authentik OIDC → Admin group and
  Authentik's group Name must be byte-for-byte identical.

### `/api/oidc/login` returns 503 "OIDC is not configured"

Enable toggle is off, OR one of the three required fields (issuer URL, client ID, client
secret) is empty. Fill them and flip the toggle.

### 5 failed OIDC attempts in a row → 429 Too many requests

The OIDC callback shares the per-IP rate limiter with `/api/local-auth/login`. Wait 15 minutes
or restart to clear. A flurry of callback failures almost always means a config mismatch worth
fixing (see redirect_uri / client_secret above), not a valid user hitting a transient glitch.

## Rolling back

If anything breaks, OIDC can be disabled without touching Authentik:

1. OmniGrid → Admin → Authentik OIDC → uncheck "Enable OIDC login" → Save.
2. `/api/auth/providers` now returns `{oidc: false}`; the SSO button disappears from `/login`
   on the next load. Local logins keep working throughout.
3. Fix the OIDC config at your leisure, re-enable the toggle, done.

## Related files in this repo

| Path                               | Purpose                                                                                                                                   |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `logic/oidc.py`                    | Discovery, PKCE, token exchange, id_token validation, callback.                                                                           |
| `logic/auth.py`                    | `auto_provision_authentik` (shared with OIDC callback), `get_auth_settings` cache.                                                        |
| `main.py:/api/oidc/*`              | Route bindings + test-connection endpoint.                                                                                                |
| `main.py:/api/auth/providers`      | Public endpoint the login page reads.                                                                                                     |
| `static/login.html`                | SSO button, appears once OIDC is live.                                                                                                    |
| `static/_partials/admin/oidc.html` | Authentik OIDC admin panel (extracted partial; inlined into `static/index.html` at request time via `<!-- INCLUDE: admin/oidc.html -->`). |
| `docs/guidelines/auth.md`          | Full auth runbook incl. local login, bootstrap, CSRF, API tokens.                                                                         |
