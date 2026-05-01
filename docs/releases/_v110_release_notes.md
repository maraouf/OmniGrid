## What changed in 1.1.0

First MINOR cut after the `1.0.0` baseline — rolls up **97 closed issues** under the 1.1.0 milestone. Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Hosts editor & Host groups

- Host rows joined against an external asset API for model / serial / location, with autofill button + dirty-... (#161)
- Toggle for host-drawer Debug data panel (#162)
- The first character into a host row's ID collapses the panel (#169)
- Range pre-fill on +Add host group (#173)
- "Collapse all" button visual fix (#195)
- "+ Add sub-group" quick button on parent host groups (#196)
- `+ Add sub-group` parent dropdown didn't reflect the chosen parent (#205)
- Group range error message wasn't showing (#218)
- `Show children` parent dropdown adjustment + Expand all / Collapse all bulk buttons (#225)
- Host drawer polish — explicit 12-col grid with `col-span-6` cards, slide animation switched from Alpine x-t... (#248)
- Hosts count badge on the "Hide hosts without agents" filter (#272)
- Groups + sub-groups now HIDDEN when "Hide hosts without agents" filter is on (preference reversed from #279) (#279)
- Service summary in HOST DRAWER (#302)
- Range filter on host drawer charts now triggers refetch (#304)
- Usage chart in host drawer (Beszel) (#307)
- For the Admin → Hosts editor (122 hosts → 200+ projected) (#311)
- Hosts editor page across reloads / tab nav (#320)
- Only host drawer: Disk I/O + Network charts now distinguish "no activity in window" from "node-exporter doe... (#327)
- Pagination + sticky action bar mirroring the Hosts editor (#328)
- Debug + SSH-run toggles in the host drawer now scroll the just-expanded body to the top of the drawer viewport (#339)
- Hosts / Host groups action bar now matches the editor section's width AND pins correctly to the viewport bo... (#340)

### Drawer, charts & Node Exporter

- View: CPU sparkline invisible on idle nodes (#209)
- Row expansion converted to slide-out drawer (#217)
- Sparks self-diagnostic + `app().statsDebug()` console helper (#251)
- Host historical graphs from node-exporter (Prometheus/Grafana-lite path for NE-only hosts) (#273)
- Mem/Disk chart Alpine errors fixed (SVG <template> doesn't work) (#283)
- Subtitle reflects actual stats picker + polling cadence honors it (#289)
- In + Net Out combined into one chart shipped (#299)
- Disk I/O chart shipped (#300)
- Average chart shipped (1m / 5m / 15m) (#301)
- Bandwidth chart shipped (#303)
- Line chart legend values no longer all-red (#305)
- Theme + hotkeys pushed down by stats picker (#313)
- I/O chart hidden for NE-only hosts (#318)
- Only host Disk I/O chart now populates from `node_disk_{read,written}_bytes_total` counters (#319)
- Drawer "No NIC activity" hint now branches on whether node-exporter is in play, not on whether Beszel is ma... (#321)
- Only host Disk I/O chart was stuck on perpetual `0 B/s` for NAS / RAID boxes (Synology, TrueNAS, OPNsense).... (#324)
- Disk I/O support for NE-only hosts (#331). `parse_disk_counters` now falls back to `node_devstat_bytes_tota... (#331)
- Drawer charts now show a subtle `Updated Xs/m/h ago` freshness hint beside the time-range picker (#338)

### Admin pages: Apprise / Open-Meteo / Portainer / SSH / Debug / Sessions

- Admin-only xterm.js viewport over WSS to a backend asyncssh PTY (#160)
- Debug-panel toggle removed from Admin → Hosts (#172)
- Service "enabled" master switches for Apprise, Open-Meteo, Portainer, SSH (#194)
- Admin → all tabs — master-toggle treatment unified: child controls disable when the master is off; Apprise... (#201)
- Admin tabs use Save button + show "Unsaved" indicator (Apprise / Open-Meteo / Portainer / SSH) (#206)
- Api/items` 500 scope bug (#266)
- Inventory dirty pill unified with other admin tabs (#285)
- 4 admin-tab dirty flags unified to smart-getter pattern (#286)
- Meteo Save button moved below the URL input (#288)
- Admin → Config tab — UI override for the 6 process-level tunables (#317)
- Admin → Debug tab: smart-getter dirty pattern + Save button (#322)
- _format_provider_test_summary()` in `main.py` keeps the Pulse + Beszel test-connection response shape ident... (#334)

### Schedules

- Daily / weekly / monthly schedules now actually fire (grace window added (#198)
- weekly npm audit + node_modules served via allowlist (was wildcard mount) (#292)

### Topbar, login & branding

- Topbar split into two rows (Option A) (#165)
- Clock + weather repositioned LEFT of the user avatar (#170)
- Brand icons batch — 14 new icons + keyword wiring (#243)
- Humax brand icon added (#275)
- Clean wordmark for `samsung`, corporate mark to `samsung-electronics` (#276)
- Kaonmedia brand icon added (#277)
- Header "Update stack" button hides when stack is expanded (#281)
- Mobile topbar phase 1 — no more horizontal page scroll on iPhone (#293)
- Toolbar + Nodes header wrap cleanly on mobile (#294)
- Topbar widgets prefs follow the dirty-pattern (no auto-save on toggle) (#297)
- Avatar lifts up to row 1, clock+weather take their own row (#298)
- Utility belt merged into header flow + language above SYNC (#309)
- Page logo no longer shows a white halo at the rounded corners (#336). `static/login.html` swapped from the... (#336)

### Vendor icons

- ~30 new vendor icons added across multiple batches (Aqara, ASUS, Alienware, Amazon Fire TV, Bose, Chromecas... (#164)
- HDHomeRun + J-Tech Digital + Nixplay (#310)

### Documentation

- `CHANGELOG.md` (this file) at the repo root, in Keep-a-Changelog format, with `[Unreleased]` + `[1.0.0]` ba... (#330)
- `README.md` ref updated from `notes/note_authentik.txt` to `notes/guidelines/authentik.md` (#335)
- Operator-private hostnames in shipped docs and code comments with example.com placeholders (#337)

### Filters, badges & status pills

- Paused"` status now correctly maps to `"down"` (#269)
- Colour cleanly + always show "0 failed" (#314)
- Filter bar (Stacks / Services / Nodes views): the divider between the health and status filter groups no lo... (#326)

### Internationalisation & translations

- `actions.close` i18n key (#236)

### Database / migrations / data

- Type ShortName field name confirmed + backend exposes `type_short` (#223)
- schema-migration infrastructure (logic/migrations.py) (#291)
- User UI prefs sync (cross-device) (#296)
- Scaffolding for multi-database support (#315)

### Internal / refactor / code review

- Host Groups editor — collapsible children, NUMBER input moved to the natural Tab-order column, group headin... (#163)
- Signature-based dedupe (#182)
- Short detection widened + diagnostic added (#199)
- Reverts/cleanup follow-ups from this session (#221)
- Fix turn from the code-review report (#240)
- **Code-review compliance batches** (closed all of (#245)
- surface SESSION_SECRET-auto-generated warning to admins (#290)
- Fresh full-code-review pass (#325)
- Model switched back to SemVer `MAJOR.MINOR.PATCH` after a brief stint with the `MAJOR.MINOR`-only model (#329)

### Other improvements & fixes

- `hostStatsSourceEnabled()` field name typo (#157)
- Provider outages no longer blank the page (#222)
- The `_deriveTypeShort` JS acronym fallback (#232)
- Block agent-memory paths under `static/` (#238)
- Text-compaction fix (img_3.png) (#258)
- Filter Docker / k8s / Proxmox internal interfaces behind a toggle (#271)
- Password field is not contained in a form" warning silenced (#284)
- Paginate + add per-system match diagnostic (#308)
- _load_curated_hosts` between the two NE samplers (#332)

