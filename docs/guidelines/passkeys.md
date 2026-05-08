# Passkeys (WebAuthn / FIDO2) runbook

Passkeys are OmniGrid's second-factor option alongside TOTP (`docs/guidelines/auth.md`).
Either method satisfies the 2FA gate — operators with both enrolled pick at the login screen.

## Supported authenticators

- **Hardware security keys** — YubiKey, Titan, Solo, Nitrokey (USB / NFC / BLE).
- **Platform authenticators** — Touch ID (macOS / iOS), Windows Hello, Android fingerprint /
  face / pattern unlock.
- **Password managers as authenticators** — 1Password, iCloud Keychain, Bitwarden, KeePassXC
  (passes the `cross-platform` transport).

Browser support: Chrome 67+ / Firefox 60+ / Safari 13+ / Edge 18+. Browsers without WebAuthn
fall through to the typed-code path automatically.

## Server prerequisites

- `webauthn` Python package installed (pinned in `requirements.txt`).
- HTTPS in production (browsers refuse WebAuthn over plain HTTP, except `localhost` for dev).
  OmniGrid behind an NPM-fronted TLS termination satisfies this.
- The RP ID is derived from the request's hostname on every challenge — no settings entry
  needed; works automatically across `localhost:8088` (dev) and the production hostname.

## Enrolling a passkey

1. Sign in normally (password + TOTP if you've already set that up).
2. Open the user menu → Profile → Security.
3. Find the "Passkeys (security keys)" card. Click **Add a passkey**.
4. Type a friendly name (e.g. "YubiKey 5C", "iPhone Touch ID", "1Password vault").
5. Confirm the prompt on your device — touch the key, allow the browser dialog, or unlock
   with biometrics.
6. The new passkey appears in the list. You can repeat for as many devices as you like —
   each one is independent and independently revocable.

## Signing in with a passkey

1. Type your username + password.
2. At the second-factor screen, click **Use a passkey**.
3. Confirm on your device. You're in.

If you have both TOTP and passkeys enrolled, the screen offers both options — pick whichever
is convenient. The 6-digit code field stays available as a fallback.

## Revoking a lost device

- Sign in from a still-trusted browser → Profile → Security → Passkeys → click **Revoke**
  next to the row for the lost device. The revocation is immediate; that key can no longer
  log in even if found.
- An admin can blanket-clear someone else's 2FA (TOTP + every passkey) via Admin → Users →
  click "Reset password" for the user. The user re-enrols on next login.

## Recovery codes

The 10 backup codes generated at TOTP enrolment work as a third fallback. They're
single-use, can be downloaded as a .txt, and remain visible (encrypted at rest) under
Profile → Security. If you've never enrolled in TOTP and lose every passkey, ask an admin
to clear your 2FA via the Users tab.

## RP-ID mismatch detection

If you migrate OmniGrid between hostnames (e.g. `omnigrid.old.example.com` → `omnigrid.new.example.com`)
or move from a `localhost:8088` dev URL to a production NPM-fronted domain, the browser refuses
to offer your previously-enrolled passkeys at the new origin — they were registered against the
old RP ID and the new domain is a separate WebAuthn relying party. Earlier this manifested as
"the Passkey button defaults to QR with no explanation" and the operator had no in-UI signal of
why their fingerprint / Touch ID prompts weren't appearing.

Today:

- The `user_credentials` schema has an `rp_id` column stamped at registration time with the
  effective hostname the credential was created under.
- `POST /api/local-auth/webauthn-start` partitions stored credentials into matching + orphaned
  sets per the request's current RP ID. When EVERY credential for the user is orphaned, the
  response carries `rp_id_mismatch: true` and the SPA renders an explanation:
  *"Your passkeys were registered under a different domain. Re-enrol them from Profile →
  Security on the new domain ({current})."*
- **Profile → Security** shows a per-credential red "registered for `<old-rp-id>`" badge so the
  operator can revoke + re-enrol orphaned credentials WITHOUT needing to authenticate first.
- Server logs include a greppable `[webauthn] <user> login-start RP-ID mismatch current=<x>
  orphaned=[...] matching=N` line per affected login attempt.

Empty `rp_id` (legacy credentials enrolled before this feature landed) is treated as unknown /
assume matching — existing enrolments don't fire spurious banners.

## Troubleshooting

- **"This browser doesn't support passkeys"** — upgrade your browser or use a different one.
  Mobile Safari requires iOS 16+ for full passkey UX.
- **"Passkey support is not available on this server"** — the `webauthn` Python package
  isn't installed. The operator runs `pip install -r requirements.txt` and restarts the
  service.
- **"Your passkeys were registered under a different domain"** — see the RP-ID mismatch
  section above. Revoke the orphaned credentials from Profile → Security and re-enrol on
  the current domain.
- **"Could not verify passkey"** — origin / RP ID mismatch on a SINGLE credential rather than
  the whole user. The browser sees the public hostname (via NPM) while the server might see
  something else. Check that NPM passes the original `Host` header (`proxy_set_header Host $host;`)
  and the original `X-Forwarded-Host` is preserved.
- **Counter regression / clone-detection rejection** — extremely rare. The authenticator
  reported a sign count lower than what we previously stored. Revoke the credential from
  Profile → Security, re-enrol from scratch.
- **macOS Safari/Chrome defaults to QR despite local Touch ID being available** — fixed in
  the WebAuthn assertion-options hardening pass: the assertion-options now carry
  `hints=["client-device", "hybrid", "security-key"]`, force `userVerification: required` so
  QR isn't offered without UV, list
  `internal` first in transports, and union `{internal, hybrid}` into stored transports so empty
  / hybrid-only credentials still get the local-device picker. If you see this on a current
  build, grep server logs for `[webauthn] <user> login-start (rp_id=...)` to verify the payload.

## Where the code lives

- **Backend** — `logic/webauthn_helper.py` (registration / authentication wrappers around
  the `webauthn` package), `logic/auth.py` (CRUD on `user_credentials`), `main.py` (routes
  under `/api/me/webauthn/*` and `/api/local-auth/webauthn-*`).
- **Frontend** — `static/index.html` (Profile → Security → Passkeys card + Admin → Users
  passkey count column), `static/js/app.js` (`addPasskey` / `revokePasskey` / `loadPasskeys`
  Alpine state), `static/js/login.js` ("Use a passkey" button on the second-factor screen).
- **Schema** — `user_credentials(user_id, credential_id BLOB UNIQUE, public_key BLOB,
  sign_count, transports, friendly_name, created_at, last_used_at, rp_id)`. The `rp_id`
  column was added later for RP-ID mismatch detection — empty on legacy rows, stamped
  per-registration thereafter. All additions are ALTER-only in `logic/auth.py:init_auth_schema`.
- **Lifecycle** — passkeys are wiped automatically by `delete_user`,
  `admin_reset_password`, and the user-delete cascade.
