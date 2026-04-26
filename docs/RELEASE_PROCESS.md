# Release process — OmniGrid

OmniGrid uses [Semantic Versioning](https://semver.org) (`MAJOR.MINOR.PATCH`)
with a two-track cadence:

- **PATCH bumps are continuous and automatic** — every successful CI
  deploy increments PATCH by 1.
- **MINOR releases are periodic and manual** — the operator decides when
  enough PATCH-shipped work has accumulated to warrant a "release" and
  cuts one by hand.

Most days you only think about PATCH. MINOR is a once-every-few-weeks
ritual that takes ~5 minutes.

## What each digit means

| Field | Bumped by | When | Reset on bump |
|---|---|---|---|
| **MAJOR** | Operator (manual) | Breaking change — DB migration that isn't forward-compatible, env var rename, `/api` contract breakage | `MINOR` → 0, `PATCH` → 0 |
| **MINOR** | Operator (manual) | Cutting a release — a batch of PATCH-shipped items is ready to be tagged as `vX.Y.0` | `PATCH` → 0 |
| **PATCH** | CI (automatic) | Every successful `git push origin main` that triggers a deploy | n/a |

The accumulating PATCH counter between MINOR releases is intentional —
it's the "tasks shipped since the last release" signal the operator
glances at to decide *when* a MINOR cut is warranted.

## Daily flow (PATCH — continuous)

For every TODO item:

1. Add a `[ ]` row to `notes/note_todo.txt` Pending block (per the lifecycle
   rule documented in `CLAUDE.md`).
2. Implement.
3. Move the row to `## Pending Validation` (`[?]`) once the code is on
   disk and smoke-checks pass locally.
4. Push to `main`. CI rsyncs to the server, bumps PATCH on the server's
   `/app/VERSION.txt` (e.g. `1.0.7` → `1.0.8`), restarts the Swarm task
   when needed, and verifies `/api/version` matches.
5. Operator validates on the live deploy. On confirmation, cut the row
   from `## Pending Validation` to `## Done` (`[x]`).
6. Add a one-line entry under `CHANGELOG.md`'s `## [Unreleased]` block
   in the appropriate category (Added / Changed / Fixed / Internal /
   etc.). Reference the TODO ID:

   ```markdown
   ### Fixed
   - Host groups admin tab now mirrors the Hosts editor's pagination + sticky action bar (#348).
   ```

That's it for daily work. No tags, no release notes, no manual version
edits — every PATCH ships silently to one user (the operator) and is
documented incrementally in `[Unreleased]`.

## Periodic flow (MINOR — release cut, every few weeks)

When `[Unreleased]` has accumulated enough items that you want to draw
a line:

1. **Pick the new MINOR.** Bumping `1.0.X` → `1.1.0` is the default.
   Bumping MAJOR (e.g. `1.X.X` → next-MAJOR `0.0`) is reserved for breaking changes (see MAJOR below).
2. **Edit the server's VERSION.txt.** SSH in and set
   `/opt/omnigrid/app/VERSION.txt` to `1.1.0` (replacing whatever
   `1.0.NN` was there). The new value takes effect on the next
   request — `main.py` re-reads VERSION.txt per call.
3. **Cut the CHANGELOG.** In `CHANGELOG.md`:
   - Rename the `## [Unreleased]` heading to `## [1.1.0] — YYYY-MM-DD`.
   - Add a fresh empty `## [Unreleased]` block at the top with the
     standard category placeholders (`_(none yet)_` lines under each).
   - Update the link references at the bottom of the file
     (`[Unreleased]: ...compare/v1.1.0...HEAD` and
     `[1.1.0]: ...releases/tag/v1.1.0`).
4. **Tag the commit.** From the local checkout:
   ```bash
   git pull
   git tag -a v1.1.0 -m "Release v1.1.0"
   git push origin v1.1.0
   ```
   The next CI run will bump PATCH from `0` → `1` (so the live deploy
   shows `1.1.1` after one task lands post-release), but the tag pins
   the exact `1.1.0` baseline for diffing later.
5. **Announce.** Optional but recommended:
   ```bash
   apprise -t "OmniGrid v1.1.0" -b "$(awk '/^## \[1.1.0\]/,/^## \[/' CHANGELOG.md | head -n -1)" \
     "$APPRISE_URL"
   ```

## Rare flow (MAJOR — breaking change)

Reserve MAJOR for changes that require operator intervention to upgrade:

- Database schema migration that isn't applied automatically by `init_db()`.
- Env var rename (e.g. `BESZEL_HUB_URL` → `MONITORING_HUB_URL`).
- `/api/...` contract breakage (response shape changes that break the SPA
  and any external integrations like Homarr widgets).
- Bind-mount path changes (e.g. `/app/data/` → `/app/state/`).

Process:

1. Same as MINOR cut, but the new version increments MAJOR (resetting both
   MINOR and PATCH).
2. Add a `notes/MIGRATIONS.md` entry for the upgrade path. Include:
   - **What changed** (env var name, DB column rename, etc).
   - **Operator action required** (rename in `.env`, run a one-shot
     migration script, etc).
   - **Roll-back plan** (which previous MAJOR is forward-compatible
     and how to pin to it).
3. Bake the migration into `init_db()` / lifespan startup where
   possible so the upgrade is hands-off; otherwise document the manual
   command clearly.
4. Tag the release as usual + Apprise the announcement so the operator
   doesn't miss the manual step.

## File reference

- **`VERSION.txt`** at repo root — dev reference; the server's copy is
  authoritative (rsync excludes this file).
- **`/opt/omnigrid/app/VERSION.txt`** on the server — single source of
  truth at runtime. `main.py:read_version()` reads it per request.
- **`.forgejo/workflows/deploy.yml`** — the "Bump VERSION.txt on server"
  step contains the PATCH-bump bash. Migrations from earlier 2-part
  (e.g. `1.49`) and legacy 3-part (e.g. `1.0.25`) formats are handled inline.
- **`CHANGELOG.md`** at repo root — the public-facing release log.
- **`notes/note_todo.txt`** — per-task implementation detail. Each
  CHANGELOG entry references a `#NNN` TODO ID for the full story.
- **`notes/MIGRATIONS.md`** — only created when a MAJOR release ships.

## Why this works for OmniGrid

OmniGrid has one operator and one user (often the same person). Heavy
release ceremony (RC builds, beta channels, signed tarballs, GPG-signed
tags) is overkill. But a *visible* changelog and a *predictable* version
bump are still load-bearing because:

- The version string is shown in the UI footer and `/api/version`.
  If two browser tabs are running different versions you want to spot
  it instantly.
- The PATCH counter is the simplest "did the deploy actually ship?"
  signal — `/api/version` returning `2.1.7` proves the latest push made
  it through rsync + restart.
- Future contributors / fork maintainers reading `CHANGELOG.md` get the
  same story you'd give them in chat without a back-and-forth.
