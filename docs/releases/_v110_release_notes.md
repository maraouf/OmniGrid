## What changed in 1.1.0

First MINOR cut after the `1.0.0` baseline — rolls up **97 closed issues** under the 1.1.0 milestone. Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Hosts editor & Host groups

- Host rows joined against an external asset API for model / serial / location, with autofill button + dirty-... (#161) [Enhancement]
- Toggle for host-drawer Debug data panel (#162) [Enhancement]
- The first character into a host row's ID collapses the panel (#169) [Enhancement]
- Range pre-fill on +Add host group (#173) [Enhancement]
- "Collapse all" button visual fix (#195) [Enhancement]
- "+ Add sub-group" quick button on parent host groups (#196) [Enhancement]
- `+ Add sub-group` parent dropdown didn't reflect the chosen parent (#205) [Enhancement]
- Group range error message wasn't showing (#218) [Enhancement]
- `Show children` parent dropdown adjustment + Expand all / Collapse all bulk buttons (#225) [Enhancement]
- Host drawer polish — explicit 12-col grid with `col-span-6` cards, slide animation switched from Alpine x-t... (#248) [Enhancement]
- Hosts count badge on the "Hide hosts without agents" filter (#272) [Enhancement]
- Groups + sub-groups now HIDDEN when "Hide hosts without agents" filter is on (preference reversed from #279) (#279) [Enhancement]
- Service summary in HOST DRAWER (#302) [Enhancement]
- Range filter on host drawer charts now triggers refetch (#304) [Enhancement]
- Usage chart in host drawer (Beszel) (#307) [Enhancement]
- For the Admin → Hosts editor (122 hosts → 200+ projected) (#311) [Enhancement]
- Hosts editor page across reloads / tab nav (#320) [Enhancement]
- Only host drawer: Disk I/O + Network charts now distinguish "no activity in window" from "node-exporter doe... (#327) [Bug]
- Pagination + sticky action bar mirroring the Hosts editor (#328) [Enhancement]
- Debug + SSH-run toggles in the host drawer now scroll the just-expanded body to the top of the drawer viewport (#339) [Enhancement]
- Hosts / Host groups action bar now matches the editor section's width AND pins correctly to the viewport bo... (#340) [Enhancement]

### Drawer, charts & Node Exporter

- View: CPU sparkline invisible on idle nodes (#209) [Enhancement]
- Row expansion converted to slide-out drawer (#217) [Bug]
- Sparks self-diagnostic + `app().statsDebug()` console helper (#251) [Enhancement]
- Host historical graphs from node-exporter (Prometheus/Grafana-lite path for NE-only hosts) (#273) [Enhancement]
- Mem/Disk chart Alpine errors fixed (SVG <template> doesn't work) (#283) [Enhancement]
- Subtitle reflects actual stats picker + polling cadence honors it (#289) [Enhancement]
- In + Net Out combined into one chart shipped (#299) [Enhancement]
- Disk I/O chart shipped (#300) [Enhancement]
- Average chart shipped (1m / 5m / 15m) (#301) [Enhancement]
- Bandwidth chart shipped (#303) [Enhancement]
- Line chart legend values no longer all-red (#305) [Enhancement]
- Theme + hotkeys pushed down by stats picker (#313) [Bug]
- I/O chart hidden for NE-only hosts (#318) [Enhancement]
- Only host Disk I/O chart now populates from `node_disk_{read,written}_bytes_total` counters (#319) [Enhancement]
- Drawer "No NIC activity" hint now branches on whether node-exporter is in play, not on whether Beszel is ma... (#321) [Enhancement]
- Only host Disk I/O chart was stuck on perpetual `0 B/s` for NAS / RAID boxes (Synology, TrueNAS, OPNsense).... (#324) [Bug]
- Disk I/O support for NE-only hosts (#331) [Enhancement]. `parse_disk_counters` now falls back to `node_devstat_bytes_tota... (#331) [Enhancement]
- Drawer charts now show a subtle `Updated Xs/m/h ago` freshness hint beside the time-range picker (#338) [Enhancement]

### Admin pages: Apprise / Open-Meteo / Portainer / SSH / Debug / Sessions

- Admin-only xterm.js viewport over WSS to a backend asyncssh PTY (#160) [Enhancement]
- Debug-panel toggle removed from Admin → Hosts (#172) [Enhancement]
- Service "enabled" master switches for Apprise, Open-Meteo, Portainer, SSH (#194) [Enhancement]
- Admin → all tabs — master-toggle treatment unified: child controls disable when the master is off; Apprise... (#201) [Enhancement]
- Admin tabs use Save button + show "Unsaved" indicator (Apprise / Open-Meteo / Portainer / SSH) (#206) [Enhancement]
- Api/items` 500 scope bug (#266) [Enhancement]
- Inventory dirty pill unified with other admin tabs (#285) [Enhancement]
- 4 admin-tab dirty flags unified to smart-getter pattern (#286) [Enhancement]
- Meteo Save button moved below the URL input (#288) [Enhancement]
- Admin → Config tab — UI override for the 6 process-level tunables (#317) [Enhancement]
- Admin → Debug tab: smart-getter dirty pattern + Save button (#322) [Enhancement]
- _format_provider_test_summary()` in `main.py` keeps the Pulse + Beszel test-connection response shape ident... (#334) [Enhancement]

### Schedules

- Daily / weekly / monthly schedules now actually fire (grace window added (#198) [Enhancement]
- weekly npm audit + node_modules served via allowlist (was wildcard mount) (#292) [Enhancement]

### Topbar, login & branding

- Topbar split into two rows (Option A) (#165) [Enhancement]
- Clock + weather repositioned LEFT of the user avatar (#170) [Enhancement]
- Brand icons batch — 14 new icons + keyword wiring (#243) [Enhancement]
- Humax brand icon added (#275) [Enhancement]
- Clean wordmark for `samsung`, corporate mark to `samsung-electronics` (#276) [Enhancement]
- Kaonmedia brand icon added (#277) [Enhancement]
- Header "Update stack" button hides when stack is expanded (#281) [Enhancement]
- Mobile topbar phase 1 — no more horizontal page scroll on iPhone (#293) [Enhancement]
- Toolbar + Nodes header wrap cleanly on mobile (#294) [Enhancement]
- Topbar widgets prefs follow the dirty-pattern (no auto-save on toggle) (#297) [Enhancement]
- Avatar lifts up to row 1, clock+weather take their own row (#298) [Enhancement]
- Utility belt merged into header flow + language above SYNC (#309) [Enhancement]
- Page logo no longer shows a white halo at the rounded corners (#336) [Enhancement]. `static/login.html` swapped from the... (#336) [Enhancement]

### Vendor icons

- ~30 new vendor icons added across multiple batches (Aqara, ASUS, Alienware, Amazon Fire TV, Bose, Chromecas... (#164) [Enhancement]
- HDHomeRun + J-Tech Digital + Nixplay (#310) [Enhancement]

### Documentation

- `CHANGELOG.md` (this file) at the repo root, in Keep-a-Changelog format, with `[Unreleased]` + `[1.0.0]` ba... (#330) [Enhancement]
- `README.md` ref updated from `notes/note_authentik.txt` to `notes/guidelines/authentik.md` (#335) [Enhancement]
- Operator-private hostnames in shipped docs and code comments with example.com placeholders (#337) [Enhancement]

### Filters, badges & status pills

- Paused"` status now correctly maps to `"down"` (#269) [Bug]
- Colour cleanly + always show "0 failed" (#314) [Enhancement]
- Filter bar (Stacks / Services / Nodes views): the divider between the health and status filter groups no lo... (#326) [Enhancement]

### Internationalisation & translations

- `actions.close` i18n key (#236) [Enhancement]

### Database / migrations / data

- Type ShortName field name confirmed + backend exposes `type_short` (#223) [Enhancement]
- schema-migration infrastructure (logic/migrations.py) (#291) [Enhancement]
- User UI prefs sync (cross-device) (#296) [Enhancement]
- Scaffolding for multi-database support (#315) [Enhancement]

### Internal / refactor / code review

- Host Groups editor — collapsible children, NUMBER input moved to the natural Tab-order column, group headin... (#163) [Bug]
- Signature-based dedupe (#182) [Enhancement]
- Short detection widened + diagnostic added (#199) [Enhancement]
- Reverts/cleanup follow-ups from this session (#221) [Enhancement]
- Fix turn from the code-review report (#240) [Enhancement]
- **Code-review compliance batches** (closed all of (#245) [Enhancement]
- surface SESSION_SECRET-auto-generated warning to admins (#290) [Enhancement]
- Fresh full-code-review pass (#325) [Enhancement]
- Model switched back to SemVer `MAJOR.MINOR.PATCH` after a brief stint with the `MAJOR.MINOR`-only model (#329) [Enhancement]

### Other improvements & fixes

- `hostStatsSourceEnabled()` field name typo (#157) [Enhancement]
- Provider outages no longer blank the page (#222) [Enhancement]
- The `_deriveTypeShort` JS acronym fallback (#232) [Enhancement]
- Block agent-memory paths under `static/` (#238) [Enhancement]
- Text-compaction fix (img_3.png) (#258) [Enhancement]
- Filter Docker / k8s / Proxmox internal interfaces behind a toggle (#271) [Enhancement]
- Password field is not contained in a form" warning silenced (#284) [Enhancement]
- Paginate + add per-system match diagnostic (#308) [Enhancement]
- _load_curated_hosts` between the two NE samplers (#332) [Enhancement]

