# Contributing to OmniGrid

Thanks for taking the time to look at OmniGrid. This file is the short
on-ramp for outside contributors; admin-facing runbooks live under
[`docs/`](docs/).

OmniGrid is a small project maintained as a homelab tool first and a
public collaboration second. I welcome bug reports, focused PRs, and
proposals that align with the existing scope. I'm slower at reviewing
sprawling refactors and major architectural redirects — those usually
benefit from an issue conversation up front.

## Table of contents

- [Project scope](#project-scope)
- [Reporting bugs](#reporting-bugs)
- [Proposing features](#proposing-features)
- [Local development](#local-development)
- [Conventions you need to know](#conventions-you-need-to-know)
- [Pull request process](#pull-request-process)
- [Commit messages](#commit-messages)
- [Releases & versioning](#releases--versioning)
- [Security disclosure](#security-disclosure)
- [Code of conduct](#code-of-conduct)

## Project scope

OmniGrid is a **single-replica** FastAPI + Alpine.js dashboard pinned to
one Swarm manager node. Several design choices follow from that
constraint and are unlikely to change:

- In-memory caches for items, stats, ops history (no horizontal scale).
- SQLite by default; PostgreSQL / MariaDB are tracked for future work
  but stay strictly opt-in.
- No build step on the frontend — `node_modules/` is committed and
  served as static files. No bundler, no Vite, no Webpack.
- No formal test suite yet; correctness is verified manually plus the
  CI deploy gates (`docker info`, `service ps`, `/api/healthz` probe,
  `/api/version` round-trip). Adding pytest fixtures alongside
  timing-sensitive features is encouraged.
- Public-shippable docs live under `docs/`. Maintainer-private working
  notes live under `notes/` and are not the contract — please don't
  open PRs against `notes/`.

Out of scope (please open an issue first if you want to discuss):

- Multi-replica horizontal scale-out.
- Replacing Portainer with direct Docker socket access.
- Heavy frontend frameworks (React / Vue / Svelte).
- Build pipelines on the frontend (Tailwind v4 in-browser JIT and
  Alpine are deliberate).

## Reporting bugs

Before opening an issue, please check:

1. The [README](README.md) "Features" and "Configuration" sections.
2. The [`CHANGELOG.md`](CHANGELOG.md) — your bug may already be fixed
   in `[Unreleased]`.
3. Existing issues on the project's git host (open + closed).

A useful bug report includes:

- OmniGrid version (`/api/version` or the SPA footer).
- Browser + OS for UI bugs.
- Relevant log lines from **Admin → Logs** (the persistent log
  retains 7 days by default; the colour-tagged `[hosts]` /
  `[snmp]` / `[webmin]` / `[ssh]` etc. prefixes help triage).
- Output of `GET /api/hosts/debug?id=<host>` (admin-only) for
  host-stats / provider issues — it carries the merged provider state,
  per-provider pause counters, sample row counts, and resolved
  tunables.
- Steps to reproduce, or at minimum the screen + click sequence that
  triggered the issue.

Please **redact secrets** before pasting logs / debug payloads —
session tokens, API keys, OIDC client secrets, SSH passphrases, etc.

## Proposing features

The fast path for a feature proposal is an issue on the project's git host with:

1. The user-visible problem you want solved (one paragraph).
2. A sketch of where the change lands (which file/module, roughly
   what shape).
3. Anything you've already ruled out and why.

For larger ideas (new host-stats provider, new auth method, new
storage backend, etc.) the issue + design conversation should land
**before** the PR so we can align on shape and scope before code lands.

## Local development

OmniGrid runs without containers for development.

```bash
# Python deps (httpx, fastapi, uvicorn, prometheus-client, pydantic, bcrypt,
#   python-multipart, python-dotenv, pysnmp, asyncssh, beautifulsoup4, …)
pip install -r requirements.txt

# Frontend deps (Alpine.js, SweetAlert2, Tailwind v4 browser JIT, xterm.js).
# These live committed under node_modules/ — no build step.
npm install

# Minimum env to boot — every other setting can be configured from the UI.
export DB_PATH="./data/omnigrid.db"
export SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export BOOTSTRAP_ADMIN_USER="admin"
export BOOTSTRAP_ADMIN_PASSWORD="changeme-on-first-login"

# Optional — Portainer integration if you want to exercise update flows.
export PORTAINER_URL="https://portainer.example.com:9443"
export PORTAINER_API_KEY="ptr_..."
export PORTAINER_ENDPOINT_ID="1"
export VERIFY_TLS="false"

# Run with auto-reload.
uvicorn main:app --host 0.0.0.0 --port 8088 --reload
```

Browse to `http://localhost:8088/`, log in with the bootstrap admin,
then **clear the bootstrap variables** before any further deploy.

`docs/guidelines/env_example.md` documents every supported `.env` key
with defaults and migration notes.

## Conventions you need to know

A few load-bearing conventions are worth knowing before your first PR.
The non-obvious ones:

- **i18n is strict.** Every user-visible string in HTML / JS goes
  through `t('key.path')` and lives in `static/i18n/en.json`. Hardcoded
  English in `:title` / template literals / toast bodies is a bug —
  including for proper nouns like provider names. Translations into
  other languages are merged into the matching `static/i18n/<code>.json`
  with empty strings falling back to English.
- **CSS is strictly token-disciplined.** Every colour / spacing / radius
  goes through a `var(--*)` token defined on `:root`. No hex / rgb /
  hsl outside the `:root` blocks. No inline `style="…"` for non-runtime
  values (the only sanctioned inline-style CSS variables are `--w` /
  `--c` for stat-bars and `--avatar-hue` for the user-menu avatar).
- **No new files without need.** Prefer editing existing files. Don't
  create README / docs files unless asked or required.
- **Logical CSS properties for RTL.** Use `me-*` / `ms-*` /
  `border-s-*` / `text-start` etc. (Tailwind logical utilities) and
  `margin-inline-*` / `padding-inline-*` (CSS) instead of left/right.
- **Long-running tasks belong in `_lifespan`** — never at module
  import time. The single-replica deploy depends on this.
- **Counter-rate samplers must SKIP, not synthesize.** Out-of-bounds
  deltas are dropped, not stored as zero — synthesizing a zero papers
  over the exact signal the chart is supposed to expose.
- **Provider extractors are pure functions.** `logic/<provider>.py`'s
  `extract_*` and `parse_*` functions take raw payloads and return the
  shared `host_*` schema. Pure functions are the easiest place to add
  pytest fixtures when we start growing the suite.
- **Brand-icon onboarding** — fetch from official sources or the
  MIT-licensed `homarr-labs/dashboard-icons` repo. Adding a brand is a
  one-line entry in `iconUrlFor()`'s keyword table in
  `static/js/app.js` plus the SVG file under `static/img/icons/`.

## Pull request process

1. **Branch off `main`.** OmniGrid uses a single-trunk model.
2. **Keep PRs small.** A focused PR (one feature, one fix, one
   refactor) merges in days. A 2k-line PR mixing three changes can sit
   in review for weeks.
3. **Run the project locally before opening the PR.** For UI changes,
   exercise the affected view in a browser — type-checks and tests
   verify code correctness, not feature correctness. If you can't test
   the UI for some reason, say so explicitly in the PR description.
4. **Update CHANGELOG.md.** Add a one-line entry under
   `## [Unreleased]` in the appropriate category (Added / Changed /
   Fixed / Removed / Deprecated / Security / Internal). Don't include
   `#NNN` issue refs in CHANGELOG bullets — those belong in the PR
   description.
5. **Don't bump `VERSION.txt` or the `[Unreleased]` heading.** PATCH
   bumps happen automatically on CI deploy; MINOR / MAJOR cuts are
   maintainer-controlled per the [Release Process](docs/RELEASE_PROCESS.md).
6. **Self-review your diff.** Skim the unified diff one final time
   before pushing — extra console logs, debug prints, commented-out
   code, half-finished comments tend to land in PRs that rush the
   final review.

PR description template:

```markdown
## Summary
<one or two sentences — what changed and why>

## Test plan
- [ ] step 1
- [ ] step 2

## Screenshots (UI changes only)
<before / after>

Closes #<issue number if any>
```

The maintainer reviews PRs as time permits —
typically within a week for focused changes, longer for sprawling ones.
A "needs changes" review isn't a rejection; it's a chance to refine.

## Commit messages

- Use the imperative mood: `add X`, `fix Y`, `refactor Z`.
- Keep the subject line under ~70 characters; wrap the body at 72.
- **Don't include `#NNN` issue numbers in commit messages or in code
  comments.** Most git hosts auto-link them and they rot when the
  project moves between issue trackers — describe the WHY in prose.
- Squash before merge if your branch has fixup commits, or just let
  the maintainer squash-merge — both work.

## Releases & versioning

OmniGrid follows [Semantic Versioning](https://semver.org):

- **PATCH** is bumped automatically on every successful CI deploy.
  Don't touch it.
- **MINOR** is maintainer-controlled and is cut when accumulated PATCH
  items feel release-worthy.
- **MAJOR** is reserved for breaking changes (DB migrations that
  aren't forward-compatible, env var renames, `/api` contract
  breakage). Migration notes ship alongside MAJOR releases.

The full release runbook is in
[`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md).

## Security disclosure

If you discover a security vulnerability — credential leak, auth
bypass, RCE, SSRF, anything that affects confidentiality / integrity /
availability of OmniGrid deployments — **please don't open a public
issue.** Instead:

1. Email the maintainer directly via the address listed on the
   maintainer's profile on the project's git host, or
2. Open a private security advisory on the project's git host (most
   hosts support this — visible only to maintainers until published).

Include reproduction steps, the affected version range, and what
mitigations / workarounds you've identified. I'll acknowledge within
72 hours and aim to ship a fix or at minimum a published advisory
within two weeks for confirmed issues. Coordinated disclosure is
appreciated.

## Code of conduct

Be kind, be specific, be patient. Reviews focus on the code and the
problem, not the person. Disagreements are resolved by going back to
the user-visible behaviour the change is trying to produce. The
maintainer reserves the right to lock or remove threads that go off
the rails.

---

Thanks again for contributing. If anything in this file is unclear or
gets out of sync with the codebase, please open an issue (or a PR
against this file) — keeping the on-ramp short and accurate is
genuinely helpful.
