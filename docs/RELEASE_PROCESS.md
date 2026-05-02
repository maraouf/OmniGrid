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

## Where the version number lives (image-build deploy)

Version is **baked into the image at build time** by the Dockerfile's
`ARG VERSION`. The deploy pipeline overrides it via
`docker build --build-arg VERSION=<new>`, which writes it to
`/app/VERSION.txt` inside the image AND stamps the OCI
`org.opencontainers.image.version` label.
`main.py:_read_version()` reads `/app/VERSION.txt` from inside the
container at startup, so `/api/version` reflects the version baked at
build time — not the contents of the host filesystem.

The repo-root `VERSION.txt` is **not** the runtime source of truth. It
remains as:

- A dev-time hint for IDEs / `_read_version()`'s repo-fallback path.
- A **file-grounded floor** that the deploy pipeline reads as one of three
  version sources (see "Daily flow" below). Hand-editing it is how the
  operator seeds a MAJOR / MINOR bump for the next CI deploy.

A local `docker build` without `--build-arg VERSION=...` produces an
image whose `/api/version` reports `0.0.0-dev` — visible signal that
the build wasn't versioned.

## Daily flow (PATCH — continuous)

For every TODO item:

1. Add a `[ ]` row to `notes/note_todo.txt` Pending block (per the lifecycle
   rule documented in `CLAUDE.md`).
2. Implement.
3. Move the row to `## Pending Validation` (`[?]`) once the code is on
   disk and smoke-checks pass locally.
4. Push to `main`. The CI pipeline (`.forgejo/workflows/deploy.yml`):
   - rsyncs the build context to `/opt/omnigrid/app` on the Swarm manager,
   - resolves the previous version from THREE sources and picks the
     highest semver:
     1. **Live `/api/version`** on the running service — most authoritative
        when reachable.
     2. **`VERSION.txt` from the rsynced build context** (file-grounded
        floor; survives a brief outage of the live service AND lets the
        operator seed MAJOR / MINOR bumps from the repo).
     3. **Highest existing `omnigrid:<X.Y.Z>` tag** in the local image
        registry on the manager (covers post-rollback scenarios).
   - increments PATCH by 1, runs `docker build --build-arg VERSION=<new>`,
     pushes `omnigrid:<new>` + `omnigrid:latest` to the container
     registry, and `docker service update --force --image <reg>:<new>` rolls
     the running task in zero-downtime via Swarm's `start-first` +
     `failure_action: rollback` update_config.
   - asserts `/api/version` equals the freshly-built tag — catches the
     edge case where the build succeeded but Swarm rolled back.
5. Operator validates on the live deploy. On confirmation, cut the row
   from `## Pending Validation` to `## Done` (`[x]`).
6. Add a one-line entry under `CHANGELOG.md`'s `## [Unreleased]` block
   in the appropriate category (Added / Changed / Fixed / Internal /
   Removed / Deprecated / Security):

   ```markdown
   ### Fixed
   - Host groups admin tab now mirrors the Hosts editor's pagination + sticky action bar.
   ```

   CHANGELOG entries describe the change in plain prose. Do NOT include
   `#NNN` cross-references in CHANGELOG.md — the file ships in the public
   release surface, where `#NNN` markdown auto-renders as a GitHub-issue
   link, but the numbers in `note_todo.txt` are an INTERNAL TODO ID space
   that would resolve to the wrong tracker.

That's it for daily work. No tags, no release notes, no manual version
edits — every PATCH ships silently to the live deploy and is documented
incrementally in `[Unreleased]`.

## Periodic flow (MINOR — release cut, every few weeks)

When `[Unreleased]` has accumulated enough items that you want to draw
a line:

1. **Pick the new MINOR.** Bumping `1.0.X` → `1.1.0` is the default.
   Bumping MAJOR is reserved for breaking changes (see MAJOR below).
2. **Seed the floor in repo-root `VERSION.txt`.** Edit the file to
   `1.1.0` (replacing whatever `1.0.NN` was there), commit, push. The
   next CI deploy reads this as the file-grounded floor (Source B in
   the version resolver), takes `max(live, file, image-tag)`, and
   increments PATCH from there. The first post-cut deploy lands at
   `1.1.1` (CI never lets a single push fail to bump PATCH; the cut
   commit itself is what shipped at `1.1.0`-equivalent).

   Alternative — manually build + roll a `1.1.0` image on the manager
   before the next CI deploy:

   ```bash
   ssh pi@docker.example.com 'cd /opt/omnigrid/app && \
     docker build --build-arg VERSION=1.1.0 \
       -t omnigrid:1.1.0 -t omnigrid:latest .'
   docker service update --force --image omnigrid:1.1.0 omnigrid_omnigrid
   ```

   The next CI deploy will see `/api/version=1.1.0` (Source A, most
   authoritative) and increment PATCH → `1.1.1`.
3. **Cut the CHANGELOG.** In `CHANGELOG.md`:
   - Rename the `## [Unreleased]` heading to `## [1.1.0] — YYYY-MM-DD`.
   - Add a fresh empty `## [Unreleased]` block at the top with the
     standard category placeholders.
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

- **`Dockerfile`** at repo root — `ARG VERSION=0.0.0-dev` placeholder; the
  deploy pipeline overrides it via `--build-arg`. The build writes the
  resolved value to `/app/VERSION.txt` and stamps it as the OCI
  `org.opencontainers.image.version` label.
- **`/app/VERSION.txt`** inside the running container — single runtime
  source of truth. `main.py:_read_version()` reads it per
  `/api/version` request.
- **`VERSION.txt`** at repo root — dev-time hint AND file-grounded floor
  for the deploy pipeline's version resolver (Source B). Hand-edit to
  seed MAJOR / MINOR bumps.
- **`.forgejo/workflows/deploy.yml`** — the `Build image, deploy stack,
  force update, verify` step contains the three-source version resolver
  and the bump bash. Legacy migrations from 2-part (`1.49`) and earlier
  3-part (`2.x.y`) formats are handled inline.
- **`CHANGELOG.md`** at repo root — the public-facing release log.
- **`notes/note_todo.txt`** — per-task implementation detail. CHANGELOG
  entries don't reference TODO IDs (they'd auto-link to GitHub issues on
  the public release surface); operator-private references stay in
  `note_todo.txt` itself.
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
  it through rsync + build + service update.
- Future contributors / fork maintainers reading `CHANGELOG.md` get the
  same story you'd give them in chat without a back-and-forth.
