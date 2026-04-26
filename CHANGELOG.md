# Changelog

All notable changes to OmniGrid land here. Format adheres to
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Cadence (see `docs/RELEASE_PROCESS.md` for the full operator runbook):

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

_(no items yet — `1.1.0` was just cut. Next PATCH-shipped item lands its
entry here.)_

## [1.1.0] — 2026-04-26

First MINOR cut after the `1.0.0` baseline. Every entry below shipped to
the live deploy as a PATCH bump (the daily CI auto-bump cadence) and is
now rolled into this MINOR release. Highlights: NE-only host charts now
populate Disk I/O on Linux (`node_disk_*`) AND FreeBSD (`node_devstat_*`),
distinguish "host is idle" from "exporter doesn't expose this collector"
([#327](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/327)), pull cross-provider snapshots so existing data survives a provider
outage ([#222](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/222) series), and fold a refreshed pile of UX polish (host-drawer
freshness label, scroll-on-expand, action-bar sticky behaviour, login-page
logo, SSH terminal cols/rows). Backend / docs / refactor work was largely
internal cleanup — see the Internal section.

### Internal

- Documentation moved from `notes/guidelines/` and `notes/RELEASE_PROCESS.md` to a new `docs/` directory; new `docs/screenshots/` for README images; new `docs/README.md` index. Operator-private files (`note_todo.txt`, `notes.txt`, `forgejo_runner_config.yml`, the live Grafana dashboard, `.claude/agent-memory/**`) stay in `notes/`. `CHANGELOG.md` and the root `README.md` keep their root-level positions per convention. CLAUDE.md / code docstrings / cross-references updated to the new paths.

- Consolidated `_load_curated_hosts` between the two NE samplers — both now import the canonical `curated_ne_hosts()` from `logic/db.py` ([#332](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/332) / CONS-001). Drops ~30 duplicated lines and means a future NE-aware sampler (e.g. ping / SNMP) only adds to the canonical helper.
- New `_format_provider_test_summary()` in `main.py` keeps the Pulse + Beszel test-connection response shape identical ([#334](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/334) / CONS-003). Webmin and Portainer keep their bespoke summaries; future `{hosts: {...}}`-shaped providers should reuse the helper.
- `README.md` ref updated from `notes/note_authentik.txt` to `notes/guidelines/authentik.md` ([#335](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/335) / DEAD-001).
- Replaced operator-private hostnames in shipped docs and code comments with example.com placeholders ([#337](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/337)).

### Changed

- `Settings → Portainer → Test` now validates the configured endpoint id, not just `/api/status` ([#335](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/335) / DEAD-002). Test now probes `/api/endpoints/{endpoint_id}` after the status check; success message reads `OK — Portainer X.Y.Z, endpoint <Name> reachable`, and a misconfigured endpoint id surfaces as `endpoint X not found on this Portainer` instead of failing silently until the next gather. Falls back to the saved `endpoint_id` when the form's value is blank.

### Added

- Host-drawer charts now show a subtle `Updated Xs/m/h ago` freshness hint beside the time-range picker ([#338](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/338)). Stamped on every successful chart fetch and ticks every second so the seconds digit counts visibly. Hidden until the first response lands.

### Changed

- Show Debug + SSH-run toggles in the host drawer now scroll the just-expanded body to the top of the drawer viewport ([#339](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/339)). Walks up to the drawer's scrollable ancestor explicitly and sets `scrollTop` so Safari (which sometimes scrolled the page instead of the drawer with bare `scrollIntoView`) tracks correctly.

### Fixed

- Admin → Hosts / Host groups action bar now matches the editor section's width AND pins correctly to the viewport bottom while scrolling ([#340](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/340)). Three-iteration fix: introduced `--app-footer-clearance: 48px` token to clear the sticky footer, then switched `.hosts-config-actionbar` from `position: fixed` (which overshot the section because the admin layout nests it inside a 1100px-max-width `.page-layout` with a 220px sidebar) to `position: sticky; bottom: var(--app-footer-clearance)` so the bar inherits the section's natural in-flow width. Sticky was silently broken by `html, body { overflow-x: hidden }` — that property promotes html/body to a scroll container, which sticky descendants pin to instead of the viewport. Swapped to `overflow-x: clip` (clips overflow without establishing a scroll container) so sticky operates against the viewport as expected.

- SSH terminal modal: xterm cols/rows now match the modal's actual dimensions on first open even when xterm's `FitAddon.proposeDimensions()` silently returns `undefined`. New `measureAndResize` helper tries FitAddon first; if `term.cols` stays at the default 80, falls back to a manual `getBoundingClientRect()` measurement using known cell metrics (~7.85px × ~17.5px per cell at 13px Menlo / Consolas / DejaVu Mono) and calls `term.resize()` directly. Helper runs on rAF + 50/250/600/1200ms `setTimeout`s + a `ResizeObserver` + the WS `ready` control frame.

- Login-page logo no longer shows a white halo at the rounded corners ([#336](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/336)). `static/login.html` swapped from the rasterised `icon-512.png` to `omnigrid.svg`, and `.login-logo` lost its redundant `background: var(--surface)` fill. The SVG renders with crisp anti-aliasing at any zoom level; favicon keeps the PNG for universal browser compat.

- SSH terminal modal: xterm cols/rows now match the modal's actual dimensions on first open. The initial `fit()` was running before the flex-1 `.terminal-host` had its layout committed, so xterm fell back to the default 80×24 and the shell wrapped mid-line. Fit now fires through a belt-and-braces staircase — double `requestAnimationFrame` + `setTimeout` retries at 50 / 250 / 600 ms + a `ResizeObserver` on the host element + a final refit when the WS `ready` control frame lands. `fit.fit()` is idempotent so the overlap is a no-op when an earlier pass already produced the right size.

### Changed

- Host drawer "No NIC activity" hint now branches on whether node-exporter is in play, not on whether Beszel is mapped ([#321](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/321)). Hybrids that run BOTH a Beszel agent (reporting zero NICs) AND an NE exporter (with real net data) — opnsense was the canonical example — now see the NE-flavoured wording (loopback / Docker bridges / veth pairs excluded from totals) instead of the Beszel "set NICS=eth0" hint, which was misleading because NE was the source the operator expected to fix. Pure Beszel hosts still see the NICS hint.

- NE-only host drawer: Disk I/O + Network charts now distinguish "no activity in window" from "node-exporter doesn't expose this collector" ([#327](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/327)). Backend `/api/hosts/history` (NE path) now returns a `collectors` dict — `{disk_io, net, fs, mem, cpu}` booleans recording whether any sample in the window held a non-null value for each metric. Frontend gates a new "Enable the diskstats / netstats collector" empty-state ahead of the existing idle copy so hosts whose exporter is permanently missing the collector get remediation guidance instead of a wait-and-see message. New `hosts_extra.collectors_missing.*` i18n family.

### Added

- FreeBSD Disk I/O support for NE-only hosts ([#331](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/331)). `parse_disk_counters` now falls back to `node_devstat_bytes_total{device,type}` when the Linux `node_disk_*` family produces no eligible devices, so opnsense / pfSense / TrueNAS / FreeBSD hosts populate the Disk I/O chart from the same sampler pipeline. FreeBSD-specific exclusions: `pass*` (SCSI passthrough), `md*` (memdisk), `cd*`. Linux pass takes precedence when both families are present; smoke test covers parse + rate + precedence.

- Admin → Host groups tab: pagination + sticky action bar mirroring the Hosts editor ([#328](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/328)). Page size persists to localStorage; Add / Collapse all / Save / scroll-to-Top stay pinned to the viewport on long lists. Action bar repositioned `position: fixed; bottom` so it's visible from page entry instead of only after scrolling past its natural position; new `.hosts-config-page-bottom-pad` class gives both editors a 80px bottom gutter so the fixed bar can never obscure the last row.
- NE-only host Disk I/O chart now populates from `node_disk_{read,written}_bytes_total` counters ([#319](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/319)). New `parse_disk_counters` in `logic/node_exporter.py`, `host_metrics_samples` table gained `disk_read_bps` / `disk_write_bps` columns, sampler tracks rates independently from net (a disk subsystem reboot doesn't drop net rates and vice-versa). Hosts whose exporter has the diskstats collector enabled show real I/O after ~10 minutes (two sampler ticks).
- `CHANGELOG.md` (this file) at the repo root, in Keep-a-Changelog format, with `[Unreleased]` + `[1.0.0]` baseline blocks. Operator-facing release notes now live here instead of being scattered across `notes/note_todo.txt` ([#330](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/330)).

- `notes/RELEASE_PROCESS.md` — operator runbook covering per-digit SemVer semantics, daily PATCH cadence, periodic MINOR cuts, rare MAJOR breaking-change ritual ([#330](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/330)).

- `DB_TYPE` env var — scaffolding for multi-database support ([#315](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/315)).

- Services row: colour cleanly + always show "0 failed" ([#314](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/314)).

- Pagination for the Admin → Hosts editor (122 hosts → 200+ projected) ([#311](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/311)).

- Service summary in HOST DRAWER ([#302](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/302) shipped) ([#302](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/302)).

- Beszel `systemd_services`: paginate + add per-system match diagnostic ([#308](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/308) follow-up) ([#312](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/312)).

- Bandwidth chart shipped ([#303](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/303)).

- Load-average chart shipped (1m / 5m / 15m) ([#301](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/301)).

- Disk I/O chart shipped ([#300](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/300)).

- Net In + Net Out combined into one chart shipped ([#299](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/299)).

- ARCH-004: surface SESSION_SECRET-auto-generated warning to admins ([#290](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/290)).

- Kaonmedia brand icon added ([#277](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/277)).

- Samsung re-org: clean wordmark for `samsung`, corporate mark to `samsung-electronics` ([#276](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/276)).

- Humax brand icon added ([#275](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/275)).

- Brand icons batch — 14 new icons + keyword wiring ([#243](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/243)).

- `Show children` parent dropdown adjustment + Expand all / Collapse all bulk buttons ([#225](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/225)).

- Asset type ShortName field name confirmed + backend exposes `type_short` ([#223](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/223)).

- Host-group range error message wasn't showing ([#218](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/218)).

- All admin tabs use Save button + show "Unsaved" indicator (Apprise / Open-Meteo / Portainer / SSH) ([#206](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/206)).

- `+ Add sub-group` parent dropdown didn't reflect the chosen parent ([#205](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/205)).

- Type-short detection widened + diagnostic added ([#199](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/199)).

- "+ Add sub-group" quick button on parent host groups ([#196](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/196)).

- Per-service "enabled" master switches for Apprise, Open-Meteo, Portainer, SSH ([#194](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/194)).

- Smarter range pre-fill on +Add host group ([#173](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/173)).

- `hostStatsSourceEnabled()` field name typo ([#157](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/157)).


### Changed

- Version model switched back to SemVer `MAJOR.MINOR.PATCH` after a brief stint with the `MAJOR.MINOR`-only model. CI auto-bumps PATCH on every deploy; MINOR/MAJOR remain operator-controlled ([#329](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/329)).

- Fresh full-code-review pass — `notes/code_review_2026-04-26.txt` written ([#325](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/325)).

- Persist Hosts editor page across reloads / tab nav ([#320](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/320)).

- Disk I/O chart hidden for NE-only hosts ([#318](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/318)).

- Admin → Config tab — UI override for the 6 process-level tunables ([#317](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/317)).

- Per-host historical graphs from node-exporter (Prometheus/Grafana-lite path for NE-only hosts) ([#273](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/273)).

- Mobile topbar — utility belt merged into header flow + language above SYNC ([#309](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/309)).

- Backend: pull `systemd_services` data from Beszel's PocketBase collection ([#302](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/302) follow-up) ([#308](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/308)).

- Swap-usage chart in host drawer (Beszel) ([#307](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/307)).

- Brand icons: HDHomeRun + J-Tech Digital + Nixplay ([#310](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/310)).

- Multi-line chart legend values no longer all-red ([#305](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/305)).

- Date-range filter on host drawer charts now triggers refetch ([#304](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/304)).

- Mobile topbar — avatar lifts up to row 1, clock+weather take their own row ([#298](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/298)).

- Mobile topbar phase 1 — no more horizontal page scroll on iPhone ([#293](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/293)).

- Hosts toolbar + Nodes header wrap cleanly on mobile ([#294](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/294)).

- Profile → Topbar widgets prefs follow the dirty-pattern (no auto-save on toggle) ([#297](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/297)).

- Per-user UI prefs sync (cross-device) ([#296](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/296)).

- ARCH-003: weekly npm audit + node_modules served via allowlist (was wildcard mount) ([#292](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/292)).

- Hosts subtitle reflects actual stats picker + polling cadence honors it ([#289](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/289)).

- All 4 admin-tab dirty flags unified to smart-getter pattern ([#286](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/286)).

- DOM "Password field is not contained in a form" warning silenced ([#284](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/284)).

- Stack header "Update stack" button hides when stack is expanded ([#281](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/281)).

- Network drawer: filter Docker / k8s / Proxmox internal interfaces behind a toggle ([#271](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/271)).

- Hidden-hosts count badge on the "Hide hosts without agents" filter ([#272](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/272)).

- Empty groups + sub-groups now HIDDEN when "Hide hosts without agents" filter is on (preference reversed from [#279](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/279)) ([#282](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/282)).

- Asset Inventory dirty pill unified with other admin tabs ([#285](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/285)).

- UX-001: stale-data markers visible in UI ([#259](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/259)).

- Open-Meteo Save button moved below the URL input — matches Apprise layout ([#288](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/288)).

- **Code-review compliance batches** (closed all of: UX-001 stale-data markers + UX-002 skeleton placeholders + UX-003 empty-state hints + UX-005 / UX-008, every CSS-001 to CSS-032 palette tokenization violation, every remaining I18N-* violation from the 2026-04-25 review, plus a sweep of bugs BUG-002 / 003 / 004 / 005 / 007 / 008 / 009 / 011) ([#245](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/245), [#249](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/249), [#254](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/254), [#255](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/255), [#259](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/259)–[#265](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/265)).
- Interactive SSH terminal modal — admin-only xterm.js viewport over WSS to a backend asyncssh PTY ([#160](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/160)).
- Asset-inventory integration — host rows joined against an external asset API for model / serial / location, with autofill button + dirty-pill UX ([#161](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/161), [#168](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/168), [#176](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/176), [#192](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/192), [#203](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/203)).
- Cross-provider snapshot fallback — provider outages no longer blank the page; cached `nodes_info` survives, stamped with `_stale_fields` so the SPA can age values out ([#222](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/222)).
- Hosts page — row expansion converted to slide-out drawer; collapsed-row headers gain SSH state dot + brand icon; provider chips suppress globally-broken providers and turn red when an enabled+mapped provider fails ([#217](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/217), [#230](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/230), [#274](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/274), [#278](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/278), [#280](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/280), [#295](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/295)).
- Host Groups editor — collapsible children, NUMBER input moved to the natural Tab-order column, group heading rendered as `<number>. <name>`, optional `number` field for display-prefix labelling, Tab skips Move ↑/↓ + Delete fixed, duplicate-id check ([#163](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/163), [#189](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/189), [#219](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/219), [#226](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/226), [#231](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/231), [#241](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/241), [#268](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/268)).
- Admin → all tabs — master-toggle treatment unified: child controls disable when the master is off; Apprise / OIDC / Portainer / SSH / Open-Meteo / Asset Inventory all share the same dirty pill + disabled-form behaviour ([#201](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/201), [#214](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/214), [#215](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/215), [#224](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/224), [#234](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/234), [#285](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/285), [#288](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/288)).
- Topbar — clock + weather repositioned LEFT of the user avatar; copy-filtered-logs button on Admin → Logs; one-shot "no EFS" warnings to cut Beszel log spam ([#170](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/170), [#171](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/171), [#181](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/181), [#183](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/183)).
- Brand-icon library expansion — ~30 new vendor icons added across multiple batches (Aqara, ASUS, Alienware, Amazon Fire TV, Bose, Chromecast, Cisco, Gigabyte, GL.iNet, Hisense, HP family, IKEA, Kindle, Lubelogger, Motorola, Nest, Rachio, Reolink, Roku, Samsung, SanDisk, Sensibo, Somfy + typo aliases, Squid, Synology, Ubiquiti family, WD, WD-TV, Xiaomi). Plus an icon-resolver registry that kills the noisy `<unknown-slug>.svg` 404 spam from missing icons ([#164](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/164), [#167](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/167), [#174](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/174), [#175](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/175), [#177](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/177), [#178](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/178), [#180](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/180), [#184](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/184)–[#188](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/188), [#191](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/191), [#197](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/197), [#202](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/202), [#208](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/208), [#210](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/210), [#211](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/211), [#212](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/212), [#220](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/220), [#227](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/227), [#228](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/228), [#229](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/229), [#233](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/233), [#244](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/244), [#246](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/246), [#250](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/250)).
- Host drawer polish — explicit 12-col grid with `col-span-6` cards, slide animation switched from Alpine x-transition to a CSS keyframe, Net In / Net Out chart heights match CPU / Memory / Disk, debug panel widths normalized ([#248](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/248), [#252](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/252), [#267](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/267), [#270](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/270), [#323](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/323)).
- Schedules — daily / weekly / monthly schedules now actually fire (grace window added — they were silently no-ops near the day-boundary) ([#198](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/198)).
- Sub-group filter behaviour — empty groups + sub-groups hide when "Hide hosts without agents" is on ([#279](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/279) / [#282](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/282) — preference reversed once mid-cycle, final state matches operator intent).
- Hidden-hosts count badge on the "Hide hosts without agents" filter ([#272](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/272)).
- Beszel `"paused"` status now correctly maps to `"down"` ([#269](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/269)).
- Asset Inventory autofill strips FQDN domain suffix from id so `ssh_fqdn_suffix` still applies cleanly at SSH-resolve time ([#203](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/203)).
- Removed the `_deriveTypeShort` JS acronym fallback — asset ShortName is the only source of truth ([#232](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/232)).


### Fixed

- Deploy bash now collapses any legacy `MAJOR=2` VERSION.txt value to `1.0.<counter>` in a one-shot migration instead of double-bumping. Recovers a previous mis-migration that produced `2.255.1` and ensures the next deploy normalizes to `1.0.255` ([#329](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/329)).

- Mobile filter bar (Stacks / Services / Nodes views): the divider between the health and status filter groups no longer strands on its own row mid-wrap — `hidden sm:inline-block` drops it on phones. `.filter-chip` padding tightens to `4px / 11px` font under `max-width: 640px`, recouping one to two wrapped rows on iPhone-width screens ([#326](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/326)).
- NE-only host Disk I/O chart was stuck on perpetual `0 B/s` for NAS / RAID boxes (Synology, TrueNAS, OPNsense). `dm-*` and `md*` devices are no longer excluded from `parse_disk_counters` totals — they ARE the user-facing volumes on those hosts. Empty-device-list now returns `None` totals (instead of `0`), so the sampler stores NULL rates and the chart shows "no data" instead of a misleading flat-zero line ([#324](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/324)).

- Host drawer debug panel — consistent panel widths ([#323](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/323)).

- Admin → Debug tab: smart-getter dirty pattern + Save button ([#322](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/322)).

- Mobile topbar regression: theme + hotkeys pushed down by stats picker ([#313](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/313)).

- Hosts mobile-card layout fixed — provider chips no longer crush the host name ([#295](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/295)).

- Per-host provider chips: suppress globally-broken providers ([#280](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/280)).

- CPU/Mem/Disk chart Alpine errors fixed (SVG <template> doesn't work) ([#283](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/283)).

- Host drawer: text-compaction fix (img_3.png) ([#258](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/258)).

- Drawer CSS regression fix (img_4.png) ([#262](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/262)).

- UX-005: Asset Inventory dirty indicator + Profile i18n leak fix ([#263](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/263)).

- UX-001 follow-up: stale markers extended to Hosts main grid ([#265](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/265)).

- ARCH-001 / BUG-003 close-out: duplicate-id check on host_groups ([#268](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/268)).

- Host status precedence + per-provider chip coloring fixes — VALIDATED ("yes in red, the beszel chip should be red") ([#278](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/278)).

- Per-host provider chips turn red when an enabled+mapped provider fails — VALIDATED via [#278](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/278) ([#274](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/274)).

- Hotfix: `/api/items` 500 from UX-003 scope bug ([#266](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/266)).

- Stats / sparks self-diagnostic + `app().statsDebug()` console helper ([#251](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/251)).

- Code-review bugs swept — BUG-002 / 003 / 004 / 005 / 007 / 008 / 009 / 011 ([#245](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/245)).

- Multi-fix turn from the code-review report ([#240](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/240)).

- Optional `number` field on host groups for display-prefix labelling ([#219](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/219)).

- Real fix for [#205](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/205) — sub-group parent dropdown still didn't work ([#216](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/216)).

- Nodes view: CPU sparkline invisible on idle nodes — clip-at-bottom-edge fix ([#209](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/209)).

- Asset autofill — strip FQDN domain suffix from id ([#203](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/203)).

- Daily / weekly / monthly schedules never fired — grace window added ([#198](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/198)).

- "Collapse all" button visual fix ([#195](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/195)).

- Asset type prefix on Hosts-view host titles ([#192](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/192)).

- Host Groups editor typing lag — root cause + fix ([#189](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/189)).

- Duplicate debug-panel toggle removed from Admin → Hosts ([#172](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/172)).

- Clock + weather positioning fix ([#171](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/171)).

- Typing the first character into a host row's ID collapses the panel — FIXED (again) ([#169](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/169)).

- Admin toggle for host-drawer Debug data panel ([#162](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/162)).

- Root-cause fix ([#155](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/155)).


### Internal

- ARCH-002: schema-migration infrastructure (logic/migrations.py) ([#291](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/291)).

- Consolidations CONS-003 / CONS-004 / CONS-007 from the code review ([#255](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/255)).

- Host drawer width + value-wrap polish + stats diagnostic + slide cleanup ([#248](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/248)).

- i18n violation cleanup pass — addressed every entry in code_review_2026-04-25.md's I18N-* section.

- `.gitignore` — block agent-memory paths under `static/` ([#238](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/238)).

- Cleanup polish — `actions.close` i18n key ([#236](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/236)).

- Frontend reverts/cleanup follow-ups from this session ([#221](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/221)).

- `.claude/settings.local.json` simplified to wildcard auto-allow ([#213](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/213)).

- SSH resolve + status log spam — signature-based dedupe ([#182](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/182)).

- Topbar split into two rows (Option A) ([#165](https://git.w.oufa.co/m.a.raouf/OmniGrid/issues/165)).

## [1.0.0] — 2026-03-21

Baseline release — first version under the SemVer + `CHANGELOG.md`
cadence (see `docs/RELEASE_PROCESS.md`). The changelog story starts
here; implementation detail for everything that shipped before this
baseline lives in `notes/note_todo.txt` under the `## Done` block,
keyed by stable `#NNN` TODO IDs.

[Unreleased]: https://git.w.oufa.co/m.a.raouf/OmniGrid/compare/v1.1.0...HEAD
[1.1.0]: https://git.w.oufa.co/m.a.raouf/OmniGrid/compare/v1.0.0...v1.1.0
[1.0.0]: https://git.w.oufa.co/m.a.raouf/OmniGrid/releases/tag/v1.0.0
