# Security policy

Thanks for taking the time to look at OmniGrid's security posture. This
file is the public reporting policy.

OmniGrid is a homelab dashboard maintained by a single operator. I take
security reports seriously and aim to acknowledge and triage them
quickly even though this isn't a full-time project.

## Supported versions

OmniGrid follows [Semantic Versioning](https://semver.org). PATCH bumps
land automatically on every successful CI deploy; MINOR and MAJOR are
operator-controlled (see [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md)).

Because the deploy story is single-replica home-lab, only the latest
MINOR receives security fixes:

| Version          | Supported          |
|------------------|--------------------|
| latest MINOR     | :white_check_mark: |
| previous MINORs  | :x:                |

Fixes ship as a normal PATCH bump on top of the current MINOR. There is
no long-term-support branch.

## Reporting a vulnerability

**Please don't open a public issue or PR for security findings.**
Instead, use one of the private channels:

1. **Email the maintainer** at the address listed on the maintainer's
   profile on the project's git host, or
2. **Open a private security advisory** on the project's git host
   (most hosts support this — visible only to maintainers until
   published).

Either channel works; the security advisory route is preferred because
it gives us a private space to coordinate the fix and credit before
disclosure.

A useful report includes:

- The affected version (`/api/version` or the SPA footer).
- Reproduction steps — minimum click sequence or `curl` invocation
  that demonstrates the issue.
- The impact you've identified (data exposure, auth bypass, RCE,
  etc.) and any constraints on exploitation.
- Mitigations or workarounds you've already verified (operator-side
  config changes, reverse-proxy rules, etc.).
- Logs, debug payloads, or screenshots if relevant — please **redact
  secrets** (session tokens, API keys, OIDC client secrets, SSH
  passphrases) before sharing.

## What to expect

- **Acknowledgement within 72 hours** of receipt, including a first
  read on whether the report looks confirmed, needs more information,
  or falls out of scope.
- **A fix or published advisory within two weeks** for confirmed
  issues. Larger fixes may take longer; if so, you'll get a status
  update with a revised timeline.
- **Credit** in the resulting advisory and `CHANGELOG.md` entry under
  the `Security` category, unless you'd rather stay anonymous — let me
  know your preference in the report.

Coordinated disclosure is appreciated. Please don't publicise the
finding until a fix has shipped or 90 days have elapsed without
progress; if you need to disclose sooner, reach out so we can align.

## Scope

In scope (please report):

- Authentication and authorisation: session/cookie handling,
  WebAuthn / passkeys, TOTP, OIDC flow, API tokens, role enforcement,
  CSRF, the bootstrap-admin path.
- Remote code execution, command injection, SSRF, path traversal in
  any `/api/*` route or admin-only feature (SSH runner, schedules,
  asset inventory refresh, etc.).
- Privilege escalation between roles (`readonly` → `admin`) or
  cross-tenant data exposure if the deploy serves multiple operators.
- Credential leakage in logs, error responses, backups, the persistent
  log file, or the SPA — including operator-private hostnames or
  secrets surfacing in the public mirror.
- Cross-site scripting in the SPA (HTML / JS / template literals
  bypassing the existing escape helpers).
- Insecure default configuration that can't be remediated by the
  operator without a code change.

Out of scope:

- Vulnerabilities in third-party dependencies that are already
  tracked upstream (please report those to the upstream project; I'll
  pick up the fix when it lands).
- Denial-of-service from operator-controlled tunables set far outside
  their documented bounds — `TUNABLES` clamps these on read, but a
  determined operator can still misconfigure their own deploy.
- Findings that require already-compromised host access (root on the
  Swarm manager, write access to `/app/.env`, control of the
  Portainer instance OmniGrid talks to).
- Self-hosted deploys with security defences explicitly disabled by
  the operator (`VERIFY_TLS=false` on a public network, missing
  `SESSION_SECRET`, public exposure without a reverse proxy doing
  TLS, etc.).
- Attacks that require physical access to the host or the operator's
  browser session.

If you're not sure whether something is in scope, send the report
anyway — I'd rather triage a borderline finding than miss a real one.

## Hardening references

The operator-facing security runbooks live under `docs/guidelines/`:

- [`auth.md`](docs/guidelines/auth.md) — session cookies, API tokens,
  CSRF, rate limiting, bootstrap admin.
- [`passkeys.md`](docs/guidelines/passkeys.md) — WebAuthn / FIDO2
  enrolment and login flow.
- [`authentik.md`](docs/guidelines/authentik.md) — OIDC SSO setup.
- [`deploy.md`](docs/guidelines/deploy.md) — production deploy,
  reverse-proxy timeouts, registry access.

For non-security contributions, see [`CONTRIBUTING.md`](CONTRIBUTING.md).
