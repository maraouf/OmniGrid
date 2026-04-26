# Changelog

All notable changes to OmniGrid land here. Format adheres to
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Cadence (see `notes/RELEASE_PROCESS.md` for the full operator runbook):

- **`PATCH`** — CI bumps automatically on every successful deploy
  (one per shipped TODO item). The accumulating count between releases
  is the operator's "is it time to cut a release" signal.
- **`MINOR`** — operator-controlled. When a batch of PATCH-shipped items
  feels release-worthy, the operator hand-edits `MINOR` on the server
  (which resets `PATCH` to `0`) and writes a new `[X.Y.0]` section here
  listing the items that landed since the last MINOR.
- **`MAJOR`** — breaking changes only (DB migrations that aren't
  forward-compatible, env-var renames, `/api` contract breakage).
  Migration notes ship alongside the release in `notes/MIGRATIONS.md`.

Categories per release follow Keep a Changelog:

- **Added** — new features.
- **Changed** — changes in existing functionality.
- **Deprecated** — features marked for removal in a future release.
- **Removed** — features that were dropped this release.
- **Fixed** — bug fixes.
- **Security** — fixes for vulnerabilities.
- **Internal** — refactors, doc work, build / CI changes that don't
  touch user-facing behaviour. (Non-standard but useful for a homelab
  tool where most work is internal.)

Each entry references its TODO ID (`#NNN`) so the full implementation
detail in `notes/note_todo.txt` is one click away.

## [Unreleased]

Items that have shipped to the live deploy as a PATCH bump but haven't
yet been rolled into a numbered `MINOR` release. When the operator cuts
the next release, this whole block becomes the `[X.Y.0]` entry below.

### Added

- _(none yet — the first PATCH bumps after this baseline land here)_

### Changed

- _(none yet)_

### Fixed

- _(none yet)_

### Internal

- _(none yet)_

## [1.0.0] — 2026-04-26

Baseline release — first MAJOR.MINOR.PATCH version under the new
SemVer + `CHANGELOG.md` cadence (see `notes/RELEASE_PROCESS.md`).
Existing deployments running an earlier `2.0.X` (legacy 3-part) or
`2.X` (interim 2-part) version are migrated forward by the bump logic
in `.forgejo/workflows/deploy.yml`, but the changelog story restarts
here so future readers have a clean starting point. Implementation
detail for everything that shipped before this baseline lives in
`notes/note_todo.txt` under the `## Done` block — keyed by stable
`#NNN` TODO IDs that survive across the format transition.

[Unreleased]: https://git.www.home.lan/m.a.raouf/OmniGrid/compare/v1.0.0...HEAD
[1.0.0]: https://git.www.home.lan/m.a.raouf/OmniGrid/releases/tag/v1.0.0
