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

### Internal

- Consolidated `_load_curated_hosts` between the two NE samplers — both now import the canonical `curated_ne_hosts()` from `logic/db.py` (#357 / CONS-001). Drops ~30 duplicated lines and means a future NE-aware sampler (e.g. ping / SNMP) only adds to the canonical helper.
- New `_format_provider_test_summary()` in `main.py` keeps the Pulse + Beszel test-connection response shape identical (#359 / CONS-003). Webmin and Portainer keep their bespoke summaries; future `{hosts: {...}}`-shaped providers should reuse the helper.
- `README.md` ref updated from `notes/note_authentik.txt` to `notes/guidelines/authentik.md` (#360 / DEAD-001).

### Changed

- `Settings → Portainer → Test` now validates the configured endpoint id, not just `/api/status` (#360 / DEAD-002). Test now probes `/api/endpoints/{endpoint_id}` after the status check; success message reads `OK — Portainer X.Y.Z, endpoint <Name> reachable`, and a misconfigured endpoint id surfaces as `endpoint X not found on this Portainer` instead of failing silently until the next gather. Falls back to the saved `endpoint_id` when the form's value is blank.

### Fixed

- SSH terminal modal: xterm cols/rows now match the modal's actual dimensions on first open (#353). The initial `fit()` was running before the flex-1 `.terminal-host` had its layout committed, so xterm fell back to the default 80×24 and the shell wrapped mid-line. Fit now fires through a belt-and-braces staircase — double `requestAnimationFrame` + `setTimeout` retries at 50 / 250 / 600 ms + a `ResizeObserver` on the host element + a final refit when the WS `ready` control frame lands. `fit.fit()` is idempotent so the overlap is a no-op when an earlier pass already produced the right size.

### Changed

- Host drawer "No NIC activity" hint now branches on whether node-exporter is in play, not on whether Beszel is mapped (#341). Hybrids that run BOTH a Beszel agent (reporting zero NICs) AND an NE exporter (with real net data) — opnsense was the canonical example — now see the NE-flavoured wording (loopback / Docker bridges / veth pairs excluded from totals) instead of the Beszel "set NICS=eth0" hint, which was misleading because NE was the source the operator expected to fix. Pure Beszel hosts still see the NICS hint.

- NE-only host drawer: Disk I/O + Network charts now distinguish "no activity in window" from "node-exporter doesn't expose this collector" (#347). Backend `/api/hosts/history` (NE path) now returns a `collectors` dict — `{disk_io, net, fs, mem, cpu}` booleans recording whether any sample in the window held a non-null value for each metric. Frontend gates a new "Enable the diskstats / netstats collector" empty-state ahead of the existing idle copy so hosts whose exporter is permanently missing the collector get remediation guidance instead of a wait-and-see message. New `hosts_extra.collectors_missing.*` i18n family.

### Added

- FreeBSD Disk I/O support for NE-only hosts (#352). `parse_disk_counters` now falls back to `node_devstat_bytes_total{device,type}` when the Linux `node_disk_*` family produces no eligible devices, so opnsense / pfSense / TrueNAS / FreeBSD hosts populate the Disk I/O chart from the same sampler pipeline. FreeBSD-specific exclusions: `pass*` (SCSI passthrough), `md*` (memdisk), `cd*`. Linux pass takes precedence when both families are present; smoke test covers parse + rate + precedence.

- Admin → Host groups tab: pagination + sticky action bar mirroring the Hosts editor (#348). Page size persists to localStorage; Add / Collapse all / Save / scroll-to-Top stay pinned to the viewport on long lists. Action bar repositioned `position: fixed; bottom` so it's visible from page entry instead of only after scrolling past its natural position; new `.hosts-config-page-bottom-pad` class gives both editors a 80px bottom gutter so the fixed bar can never obscure the last row.
- NE-only host Disk I/O chart now populates from `node_disk_{read,written}_bytes_total` counters (#339). New `parse_disk_counters` in `logic/node_exporter.py`, `host_metrics_samples` table gained `disk_read_bps` / `disk_write_bps` columns, sampler tracks rates independently from net (a disk subsystem reboot doesn't drop net rates and vice-versa). Hosts whose exporter has the diskstats collector enabled show real I/O after ~10 minutes (two sampler ticks).
- `CHANGELOG.md` (this file) at the repo root, in Keep-a-Changelog format, with `[Unreleased]` + `[1.0.0]` baseline blocks. Operator-facing release notes now live here instead of being scattered across `notes/note_todo.txt` (#350).

- `notes/RELEASE_PROCESS.md` — operator runbook covering per-digit SemVer semantics, daily PATCH cadence, periodic MINOR cuts, rare MAJOR breaking-change ritual (#350).

- `DB_TYPE` env var — scaffolding for multi-database support (#335).

- Services row: colour cleanly + always show "0 failed" (#334).

- Pagination for the Admin → Hosts editor (122 hosts → 200+ projected) (#331).

- Service summary in HOST DRAWER (#321 shipped) (#321).

- Beszel `systemd_services`: paginate + add per-system match diagnostic (#328 follow-up) (#332).

- Bandwidth chart shipped (#322).

- Load-average chart shipped (1m / 5m / 15m) (#320).

- Disk I/O chart shipped (#319).

- Net In + Net Out combined into one chart shipped (#318).

- ARCH-004: surface SESSION_SECRET-auto-generated warning to admins (#309).

- Kaonmedia brand icon added (#294).

- Samsung re-org: clean wordmark for `samsung`, corporate mark to `samsung-electronics` (#292).

- Humax brand icon added (#291).

- Brand icons batch — 14 new icons + keyword wiring (#259).

- `Show children` parent dropdown adjustment + Expand all / Collapse all bulk buttons (#240).

- Asset type ShortName field name confirmed + backend exposes `type_short` (#237).

- Host-group range error message wasn't showing (#232).

- All admin tabs use Save button + show "Unsaved" indicator (Apprise / Open-Meteo / Portainer / SSH) (#220).

- `+ Add sub-group` parent dropdown didn't reflect the chosen parent (#219).

- Type-short detection widened + diagnostic added (#212).

- "+ Add sub-group" quick button on parent host groups (#209).

- Per-service "enabled" master switches for Apprise, Open-Meteo, Portainer, SSH (#204) (#206).

- Smarter range pre-fill on +Add host group (#183).

- `hostStatsSourceEnabled()` field name typo (#167).


### Changed

- Version model switched back to SemVer `MAJOR.MINOR.PATCH` after a brief stint with the `MAJOR.MINOR`-only model. CI auto-bumps PATCH on every deploy; MINOR/MAJOR remain operator-controlled (#349).

- Fresh full-code-review pass — `notes/code_review_2026-04-26.txt` written (#345).

- Persist Hosts editor page across reloads / tab nav (#340).

- Disk I/O chart hidden for NE-only hosts (#338).

- Admin → Config tab — UI override for the 6 process-level tunables (#337).

- Per-host historical graphs from node-exporter (Prometheus/Grafana-lite path for NE-only hosts) (#289).

- Mobile topbar — utility belt merged into header flow + language above SYNC (#329).

- Backend: pull `systemd_services` data from Beszel's PocketBase collection (#321 follow-up) (#328).

- Swap-usage chart in host drawer (Beszel) (#327).

- Brand icons: HDHomeRun + J-Tech Digital + Nixplay (#330).

- Multi-line chart legend values no longer all-red (#325).

- Date-range filter on host drawer charts now triggers refetch (#323).

- Mobile topbar — avatar lifts up to row 1, clock+weather take their own row (#317).

- Mobile topbar phase 1 — no more horizontal page scroll on iPhone (#312).

- Hosts toolbar + Nodes header wrap cleanly on mobile (#313).

- Profile → Topbar widgets prefs follow the dirty-pattern (no auto-save on toggle) (#316).

- Per-user UI prefs sync (cross-device) (#315).

- ARCH-003: weekly npm audit + node_modules served via allowlist (was wildcard mount) (#311).

- Hosts subtitle reflects actual stats picker + polling cadence honors it (#308).

- All 4 admin-tab dirty flags unified to smart-getter pattern (#305).

- DOM "Password field is not contained in a form" warning silenced (#303).

- Stack header "Update stack" button hides when stack is expanded (#300).

- Network drawer: filter Docker / k8s / Proxmox internal interfaces behind a toggle (#287).

- Hidden-hosts count badge on the "Hide hosts without agents" filter (#288).

- Empty groups + sub-groups now HIDDEN when "Hide hosts without agents" filter is on (preference reversed from #298) (#301).

- Asset Inventory dirty pill unified with other admin tabs (#304).

- UX-001: stale-data markers visible in UI (#275).

- Open-Meteo Save button moved below the URL input — matches Apprise layout (#307).

- UX-002: skeleton placeholders during initial fetch (#276).

- UX-003: empty-state hint when Portainer not configured (#277).

- UX-008: Admin sidebar collapses to <select> below 768px (#280).

- Empty sub-group headings (REVERSED — see #301) (#298).

- Net In / Net Out chart heights match CPU/Memory/Disk (#286).

- Beszel "paused" status now maps to "down" (#285).

- Host drawer: explicit 12-col grid with col-span-6 on each card (#283).

- Stats-graphs diagnostic resolved the issue — confirmed working by operator (#273).

- Stacks/Services view stats graphs — RESOLVED via #273 diagnostics (#250).

- CSS palette tokenization complete — every CSS-001 to CSS-032 violation closed (#270).

- `gather_stats()` diagnostic logging — pinpoints why stats are empty in operator's #250 (#269).

- Host drawer slide animation finally working — switched from Alpine x-transition to plain CSS keyframe (#268).

- Icon-resolver registry — kills the `<unknown-slug>.svg` 404 noise (#266).

- Closed every remaining I18N-* violation from `notes/code_review_2026-04-25.md` (#265).

- Three runtime errors from operator's deployed-page console (#263).

- Brand icons batch 3 — Roku + Alienware (simple-icons), Kindle + WD-TV fallbacks (#256 closed) (#262).

- Brand icons batch 2 — 5 more from Wikimedia Commons (Samsung / Bose / Gigabyte / Nest / Chromecast) (#260).

- Page-load Alpine errors after the new-version banner reload — `app.js` was racing Alpine (#258).

- Host Groups editor: Tab skips Move ↑ / Move ↓ / Delete ✕ buttons (#257).

- Re-applied disable-when-master-off across all admin tabs (operator's final answer on #238) (#249).

- Amazon Fire TV brand icon (#248).

- Removed the `_deriveTypeShort` JS acronym fallback — asset ShortName is the only source of truth (#247).

- Host group heading format: `<number>. <name>` (dot separator) (#246).

- Hosts page: row expansion converted to slide-out drawer (#239 finished) (#245).

- Brand icons: SanDisk + HP-family completion (#244).

- Somfy typo aliases — `smofy` resolves to somfy.svg (#243).

- HP brand-icon resolver wiring (#242).

- Host Groups editor: NUMBER input moved to first column for natural Tab order (#241).

- Reverted #228 + #229's "disable form when master switch is off" treatment (#238).

- Cross-provider snapshot fallback so providers going down don't blank the page (#236).

- Brand icons: Rachio + GL.iNet + Somfy (#234).

- CPU bar empty in Nodes view when only host-stats providers report (#231).

- Admin → SSH child controls now disable when master switch is off (#229).

- Admin → OIDC: same dirty + disabled treatment as the other admin tabs (#228).

- Squid PNG → SVG wrapper (#226).

- Lubelogger PNG → SVG wrapper (#225).

- Sensibo brand icon (#224).

- Hisense brand icon (#222).

- Hosts view: skip the loading state for unconfigured hosts (#221).

- SSH state dot moved to right side of host-row header (#218).

- Motorola brand icon (#216).

- Portainer Service-disabled toggle now also greys endpoint_id, verify_tls checkbox, Save + Test buttons (#215).

- Host Stats per-provider blocks: hide → disable (#213).

- Aqara + IKEA + Xiaomi brand icons (#210).

- SSH state dot on Admin → Hosts collapsed-row headers (#203).

- Brand icon on Admin → Hosts collapsed-row headers (#201).

- After save, no host rows can expand (#200).

- Synology icon — replaced with the Wikimedia Commons SVG (#198).

- Reolink icon — replaced with logowik.com WebP (#197).

- Reolink icon — operator-supplied PNG embedded in SVG wrapper (#196).

- WD icon — replaced with the canonical Wikimedia Commons SVG (#195).

- Reolink brand icon (#194).

- Copy filtered logs to clipboard (#193).

- Beszel log spam — one-shot "no EFS" warnings (#191).

- Ubiquiti icon replaced with the canonical svgrepo "U-in-a-circle" mark (#190).

- Autofill chip hidden when host row is collapsed (#189).

- Cisco icon replaced with the actual Wikimedia Commons SVG (#188).

- Ubiquiti icon redo + Cisco icon restored with wordmark (#187).

- Asset-autofill button promoted to row header (#186).

- Brand icons for Ubiquiti + UI (#185).

- Cisco icon replaced with iconic bridge-only variant (#184).

- Topbar clock + weather repositioned to the LEFT of the user avatar (#180).

- Asset-inventory autofill in Admin → Hosts row (#178).

- Cisco brand icon (#177).

- "Open Terminal" button styling (#176).

- Interactive SSH terminal modal (#170).

- Brand icon for ASUS routers (#174).

- Collapsible children in Host Groups editor (#173).

- Asset card hidden when no inventory match (#171).

- Real root cause finally found (#169).

- Root cause: typing in host editor's ID field collapses the row (#166).


### Fixed

- Deploy bash now collapses any legacy `MAJOR=2` VERSION.txt value to `1.0.<counter>` in a one-shot migration instead of double-bumping. Recovers a previous mis-migration that produced `2.255.1` and ensures the next deploy normalizes to `1.0.255` (#349).

- Mobile filter bar (Stacks / Services / Nodes views): the divider between the health and status filter groups no longer strands on its own row mid-wrap — `hidden sm:inline-block` drops it on phones. `.filter-chip` padding tightens to `4px / 11px` font under `max-width: 640px`, recouping one to two wrapped rows on iPhone-width screens (#346).
- NE-only host Disk I/O chart was stuck on perpetual `0 B/s` for NAS / RAID boxes (Synology, TrueNAS, OPNsense). `dm-*` and `md*` devices are no longer excluded from `parse_disk_counters` totals — they ARE the user-facing volumes on those hosts. Empty-device-list now returns `None` totals (instead of `0`), so the sampler stores NULL rates and the chart shows "no data" instead of a misleading flat-zero line (#344).

- Host drawer debug panel — consistent panel widths (#343).

- Admin → Debug tab: smart-getter dirty pattern + Save button (#342).

- Mobile topbar regression: theme + hotkeys pushed down by stats picker (#333).

- Hosts mobile-card layout fixed — provider chips no longer crush the host name (#314).

- Per-host provider chips: suppress globally-broken providers (#299).

- CPU/Mem/Disk chart Alpine errors fixed (SVG <template> doesn't work) (#302).

- Host drawer: text-compaction fix (img_3.png) (#274).

- Drawer CSS regression fix (img_4.png) (#278).

- UX-005: Asset Inventory dirty indicator + Profile i18n leak fix (#279).

- UX-001 follow-up: stale markers extended to Hosts main grid (#281).

- ARCH-001 / BUG-003 close-out: duplicate-id check on host_groups (#284).

- Host status precedence + per-provider chip coloring fixes — VALIDATED ("yes in red, the beszel chip should be red") (#297).

- Per-host provider chips turn red when an enabled+mapped provider fails — VALIDATED via #297 (#290).

- Hotfix: `/api/items` 500 from UX-003 scope bug (#282).

- Stats / sparks self-diagnostic + `app().statsDebug()` console helper (#267).

- Code-review bugs swept — BUG-002 / 003 / 004 / 005 / 007 / 008 / 009 / 011 (#261).

- Multi-fix turn from the code-review report (#255).

- Optional `number` field on host groups for display-prefix labelling (#233).

- Real fix for #219 — sub-group parent dropdown still didn't work (#230).

- Nodes view: CPU sparkline invisible on idle nodes — clip-at-bottom-edge fix (#223).

- Asset autofill — strip FQDN domain suffix from id (#217).

- Daily / weekly / monthly schedules never fired — grace window added (#211).

- "Collapse all" button visual fix (#208).

- Asset type prefix on Hosts-view host titles (#202).

- Host Groups editor typing lag — root cause + fix (#199).

- Duplicate debug-panel toggle removed from Admin → Hosts (#182).

- Clock + weather positioning fix (#181).

- Typing the first character into a host row's ID collapses the panel — FIXED (again) (#179).

- Admin toggle for host-drawer Debug data panel (#172).

- Root-cause fix (#165).


### Internal

- ARCH-002: schema-migration infrastructure (logic/migrations.py) (#310).

- Consolidations CONS-003 / CONS-004 / CONS-007 from the code review (#271).

- Host drawer width + value-wrap polish + stats diagnostic + slide cleanup (#264).

- i18n violation cleanup pass — addressed every entry in code_review_2026-04-25.md's I18N-* section (#254).

- `.gitignore` — block agent-memory paths under `static/` (#253).

- Cleanup polish — `actions.close` i18n key (#251).

- Frontend reverts/cleanup follow-ups from this session (#235).

- `.claude/settings.local.json` simplified to wildcard auto-allow (#227).

- SSH resolve + status log spam — signature-based dedupe (#192).

- Topbar split into two rows (Option A) (#175).

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
