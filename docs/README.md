# OmniGrid documentation

Operator-facing runbooks, integration guides, and visual reference. Everything
in this directory ships in the public repo.

For the project overview and quick-start, see the
[root README](../README.md).
For release notes, see the [root CHANGELOG](../CHANGELOG.md) — kept at the
repository root by convention so git hosts and toolchains (npm / PyPI /
packagers) auto-detect it.

## Guidelines

Step-by-step runbooks for each integration / subsystem.

| File | Purpose |
| --- | --- |
| [`guidelines/auth.md`](guidelines/auth.md) | Local accounts + sessions + API tokens — bootstrap the first admin, manage users, rotate `SESSION_SECRET`. |
| [`guidelines/passkeys.md`](guidelines/passkeys.md) | WebAuthn / FIDO2 passkeys as a 2FA method — enrolment, login, recovery, troubleshooting. |
| [`guidelines/authentik.md`](guidelines/authentik.md) | Authentik OIDC SSO setup. Settings live in the DB; this walks the IdP-side configuration end to end. |
| [`guidelines/deploy.md`](guidelines/deploy.md) | Production deploy via the CI pipeline (image-build + registry push + force-update, #609). Runner setup, deploy-key rotation, version-bump model, manual rollback, reverse-proxy timeouts, troubleshooting. |
| [`guidelines/env_example.md`](guidelines/env_example.md) | Every supported `.env` key with defaults, scope, and migration notes. |
| [`guidelines/metrics_guide.md`](guidelines/metrics_guide.md) | Prometheus `/metrics` schema + Grafana dashboard import notes. |
| [`guidelines/scheduler.md`](guidelines/scheduler.md) | Scheduler kinds (`gather_refresh` / `prune_node` / `prune_all_nodes` / `backup` / `asset_inventory_refresh` / `prune_logs`), endpoints, safety properties. |
| [`guidelines/beszel_agent.md`](guidelines/beszel_agent.md) | Beszel agent install + the `EXTRA_FILESYSTEMS` / `NICS` env knobs OmniGrid relies on. |
| [`guidelines/api.md`](guidelines/api.md) | **OmniGrid HTTP API** — auth modes, common workflows, error shapes, stability contract. |
| [`guidelines/api_services.md`](guidelines/api_services.md) | OmniGrid's outbound integrations (Asset API, Apprise, Open-Meteo). |
| [`guidelines/npm_updates.md`](guidelines/npm_updates.md) | Front-end dependency bump workflow — npm install, what gets committed, allowlist additions. |

## Releases

| File | Purpose |
| --- | --- |
| [`RELEASE_PROCESS.md`](RELEASE_PROCESS.md) | Per-digit SemVer semantics, daily PATCH cadence (CI auto-bump), periodic MINOR cuts, rare MAJOR breaking-change ritual. |
| [`grafana_dashboard_omnigrid.example.json`](grafana_dashboard_omnigrid.example.json) | Importable Grafana dashboard template — public-facing copy with placeholder URLs. The operator's working dashboard with live URLs lives at `notes/grafana_dashboard_omnigrid.json` and is intentionally not in this directory. |

## Screenshots

`screenshots/` holds the images referenced from the root README (and any future
walkthroughs). Add new screenshots here at sensible names (`hosts-view.png`,
`stack-detail.png`, etc.) and reference them from the README via
`![alt](docs/screenshots/<name>.png)`.

## Conventions

- **Public-shippable content only.** Anything operator-private (working
  scratch notes, runner config, the `.claude/agent-memory/**` dirs, the
  deploy `.env`, the live Grafana dashboard) stays under `notes/` or is
  gitignored. The audit grep is documented in `CLAUDE.md`'s
  "Operator-private hostnames" convention bullet.
- **Hostname / IP placeholders.** `*.example.com` (RFC 2606 reserved domain)
  for hostnames; `192.X.X.X` for IPs (chosen over the technically-valid RFC 5737
  `192.0.2.x` because the operator prefers visually-obvious-as-non-real over
  strict standards conformance).
- **Cross-links.** Internal cross-references between guideline files use
  relative paths within `docs/` (e.g. `guidelines/auth.md` → `auth.md`).
  Links from outside `docs/` (root README, CLAUDE.md, code docstrings) use
  the full `docs/guidelines/<file>.md` path.
