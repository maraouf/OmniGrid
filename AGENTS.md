# OmniGrid agent notes

## Read before editing

- `CLAUDE.md` contains the verified architecture and invariant catalogue; open the matching file under `.claude/conventions/` before changing backend data, operations, providers, apps, AI/Telegram, frontend Alpine, CSS, accessibility, or deploy/docs code.
- Prefer executable config and scripts over prose when they disagree. Public operator runbooks are under `docs/guidelines/`; `notes/` is private scratch space, not an implementation contract.

## Structure that affects changes

- `main.py` owns the FastAPI app, lifespan, shared orchestration, and the final `main_pkg/*` route-import chain. Route chunks depend on that shared namespace and import order; preserve the chain and run the boot audit after moving route helpers.
- `logic/` contains business logic; `logic/apps/registry.py` is the registry for per-app integrations and skills. `static/` is an Alpine.js/Tailwind SPA served as-is: there is no frontend build or bundler, and `static/_partials/` are server-side includes (do not create include cycles).
- The service is intentionally single-replica: in-memory caches, operations, and the SSE bus are process-local. Start every long-running worker from `_lifespan`; use the existing background-task wrapper rather than a bare import-time or untracked task.
- Docker management has two explicit backends: Portainer REST (`"portainer"`, default) and direct Docker nodes over SSH/TLS (`"docker:<id>"`). Resolve backend-specific operations through `logic/docker_backend.py`; do not add an exposed Docker socket or a third ad-hoc dispatch path.
- SQLite is the only wired database. Keep additive/idempotent schema setup in the existing init path; put non-additive changes in a new numbered `logic/migrations.py` migration and never rewrite a shipped migration.

## Conventions enforced by the repo

- Static settings keys, tunables, and environment keys use `logic.settings_keys.Settings`, `logic.tuning.Tunable`, and `logic.env_keys.EnvKey`. Add process knobs to `logic/tuning.py:TUNABLES` (DB > env > default), not as hardcoded constants.
- User-visible frontend text must use the i18n helpers and have an English key; CSS colors, spacing, and radii use `:root` tokens and RTL-sensitive layout uses logical properties.
- Counter-rate samplers skip invalid/out-of-bounds deltas; they must not store synthetic zeroes. Provider `extract_*`/`parse_*` functions should remain pure over raw payloads.
- The repository expects CRLF for normal text files. `.githooks/*` and shell/Docker special cases stay LF; the linter can normalize ordinary files with `--fix-crlf`.

## Setup and verification

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt   # pytest + pyright for the full gate
npm install                            # node_modules is committed and served; no build step
uvicorn main:app --host 0.0.0.0 --port 8088 --reload
```

- For local boot, set at least `DB_PATH`, `SESSION_SECRET`, `BOOTSTRAP_ADMIN_USER`, and `BOOTSTRAP_ADMIN_PASSWORD`; clear bootstrap variables after first login. Portainer variables are optional first-boot seeds and later UI-managed.
- In this checkout, run `python` and `npm` directly on Windows; do not route package-manager commands through WSL.
- Focused checks: `python scripts/lint.py --files <paths> --severity warn`, `python -m pytest tests/test_file.py -q`, or `python -m pytest tests/test_file.py::test_name -q`. Frontend checks are `npm run lint` and `npm run stylelint`.
- Match the commit gate in order: `python scripts/lint.py --staged --fix-crlf --severity warn`, `python scripts/lint.py --audits --severity warn`, then `python -m pytest tests -q`. Install the tracked hooks once per clone with `python scripts/install_precommit.py`.
- Production deploys are image-build deploys driven by `.forgejo/workflows/deploy.yml`; pushes to `main` trigger the Swarm deployment and auto-increment PATCH versions. `vMAJOR.MINOR.0` tags use `.github/workflows/publish-ghcr.yml` for GHCR publication.
