## What changed in 1.6.0

Sixth MINOR cut on top of `1.5.0` — rolls up **326 closed issues** under the 1.6.0 milestone (211 enhancements, 115 bug fixes). Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Highlights

- Major app-integration expansion — new per-app integrations wired end-to-end for Plex, Jellyfin, Emby, Tautulli, Tdarr, Tracearr, Kavita, Seerr (Overseerr / Jellyseerr), AdGuard Home Sync, ddns-updater, Apprise and Forgejo, each following the per-app encapsulation pattern with editor + extras partials, catalog templates, icons and AI skills — bringing the per-app integration roster to 23 modules.
- *arr stack & transcoding — Sonarr / Radarr / Lidarr / Readarr / Prowlarr / Bazarr / qBittorrent extras, Prowlarr add-indexer from AI / Telegram, and a full Tdarr workflow (check-bloated, requeue-bloated, requeue-failed) with background scans, auto-poll, measured ETAs and rich per-file result UIs.
- Apps page & custom dashboard — "By app" cards redesigned (logo left, content right), multiple named custom-dashboard views with per-view private/public visibility + edit permissions, per-app data-cache TTL configurable in the app, and per-app action (skill) buttons gated on the per-instance "show extras" toggle.
- Credential-safe media art — a new per-app authenticated image proxy (`/api/services/{host}/{idx}/image-proxy`) fetches posters and avatars through each app's own credential so it never reaches the browser DOM, with an SSRF guard and a 10 MB / 1-day cache (Plex / Tautulli / Bazarr / Seerr / Forgejo art).
- Telegram bot — a new `/skills` roster plus dynamic per-app `/<skill_id>` commands (e.g. `/run_speedtest`, `/adguard_status`), expanded command help + arg-skill handling, AI free-text grounded in real fleet state, per-pending-skill polling so long background jobs (e.g. Tdarr requeue) report completion automatically, and destructive-action confirm gating that honours each surface's policy.
- Stats dashboard — slow-query investigation plus event-loop-blocking fixes, chart memoization, Samples KPIs + i18n, and a performance pass that chunked / offloaded the retention prunes.
- Prayer Times & Weather — prayer reminders (a notification N minutes before each prayer, per-user opt-in with per-user medium selection across in-app / Telegram / Apprise), Prayer-Times admin fixes, and weather-sampler additions.
- AI assistant — answers per-host telemetry questions directly and auto-resolves a single matching host; host-status accuracy fix so it no longer mislabels reachable "problem" hosts as Down.
- HTTP / Service probe correctness — UDP + legacy-TLS endpoint handling, SNI / TLS unrecognized-name fixes, the Accepted-status-codes (CSV) field no longer clearing on keystroke, and open-as-URL port links no longer appending health-check probe paths.
- Stability & performance — the tracemalloc default-ON crash-loop root-caused and fixed; SPA perf work (xterm.js lazy-load, `x-show` → `x-if` unmounting of costly subtrees, sprite-preload + requestAnimationFrame violation cleanups); per-app first-load paint fixes.
- Hosts & discovery — port-scan default coverage extended to common TCP + UDP service ports, an Asset-Inventory collapsible JSON-tree viewer, Discovery-wizard improvements, scan-only port surfacing, and Apps-instance grouping (by host / service / none).
- Security — CodeQL SSRF + path-injection findings resolved, plus UTF-8 encoding hardening on file reads.
- Provider chips, icons & status pills + cross-app polish — unified Test-connection component, reload-button consistency, app-extras presentation unified across all apps, Public-IP widget country-flag display (new `flag-icons` dep), and a top-bar recent-tabs privacy fix (a user only ever sees their own tabs).

### Telegram bot

- Telegram `/help` curation -- hide /link for already-linked users, gate /ip on `tuning_public_ip_enabled`, s... (#1079) [Enhancement]
- Telegram AI free-text non-response + missing "Thinking" indicator (#1087) [Bug]
- Notification test messages -- include the provider name so Apprise vs Telegram test fires are distinguishable (#1119) [Enhancement]
- Telegram AI fixes (the web / system AI path was already correct -- it runs the two-round tool loop; this en... (#1141) [Bug]
- AI blind to app skills -- ROOT CAUSE behind "show me the latest speed test -> integration not configured" i... (#1147) [Enhancement]
- AdGuard Home per-app integration (backend module + APP-LEVEL aggregated extras + fleet skills + AI/Telegram) (#1159) [Enhancement]
- Telegram /help "App skills" + per-app SKILL slash commands (#1161) [Enhancement]
- Prayer Times card + AI/Telegram prayer-times & Hijri-calendar answers (#1174) [Enhancement]
- Prayer reminders — send a notification N minutes before each prayer, per-user opt-in with per-user medium s... (#1182) [Enhancement]
- HTTP/service probe-failure notifications ignored a user's per-channel Profile routing and still fired to Te... (#1186) [Bug]
- Telegram "Test connection" was broadcasting the probe message to EVERY chat in the telegram_chat_id CSV (#1189) [Bug]
- Per-app skill commands (and /whoami-style commands) in Telegram AI replies were copyable-only, not clickabl... (#1190) [Enhancement]
- Telegram AI Seerr suggestion-filter fixes (two related fixes, merged): (A) "exclude movies from Spain and D... (#1191) [Bug]
- Telegram /help — collapse the App-skills section to ONE tappable entry per app (#1198) [Enhancement]
- Telegram /hijri — the Gregorian date now follows the user's profile date format (was ISO, e (#1207) [Enhancement]
- Telegram /prayer — prayer clock times now follow the user's 12h/24h profile format (were always 24h) (#1208) [Enhancement]
- Telegram /moon — moonrise/moonset times + the 'Next 2 days' outlook dates now follow the user's profile format (#1209) [Bug]
- Telegram /cleanup — the reply now NAMES the containers being removed (was just a count) (#1211) [Enhancement]
- Prowlarr add-indexer from AI/Telegram (new write capability on the existing Prowlarr app).(not just read):... (#1237) [Enhancement]
- New Telegram /skills command -- the per-app skill roster on its own (the '🧠 App skills (tap an app to see i... (#1239) [Enhancement]
- Telegram-AI skill replies showed literal special characters (e.g (#1240) [Enhancement]
- Telegram command help -- arg-skill commands no longer paste a placeholder (#1248) [Enhancement]
- Wire qBittorrent as a per-app integration -- SINGLE-instance per chip (NOT fleet) so 2+ instances each rend... (#1250) [Enhancement]

### Stats dashboard

- service_probe / http_probe now render as ACTIVE provider chips (with stats), not muted (#1047) [Bug]
- Stats -> Samplers sub-tab icon -- was `icon-activity` (same as Network sub-tab), now `icon-loader` so the t... (#1075) [Enhancement]
- Slow-query log line -- caller-site identification (#1078) [Bug]
- Stats Incidents header -- range-picker + reload clipped off the right edge (should stay fully visible like... (#1110) [Bug]
- Stats section findings, worked one-by-one (#1111) [Enhancement]
- Unified the Admin -> AI Usage dashboard time-range filter with the Stats -> AI Cost picker (#1114) [Enhancement]
- Stats -> Database growth projection now grounded in real measured history (was a synthetic +0.5%/day stub o... (#1133) [Enhancement]
- Sampler vs Stats Samples/Samplers drift audit (#1136) [Enhancement]
- Bug fix: Stats -> AI Cost showed no data while Admin AI did (#1137) [Bug]
- Stats slow_query log warnings -- investigated + one event-loop-blocking fix (#1140) [Bug]
- Slow-query / service_sampler perf batch (#1152) [Enhancement]
- Apps edit-instance editor polish: (a) 'Show extras (UPS stats etc.)' label (#1160) [Bug]
- Apps page — keep AdGuard / Pi-hole fleet ACTIONS in the app drawer only; the Apps-grid card shows STATS only (#1199) [Enhancement]
- Stats — added an 'Apps with extras' card after the Apps card (#1214) [Enhancement]
- A11y + i18n pass across the Apps / Stats / shared surfaces (#1235) [Enhancement]
- Stats dashboard -- add 6 quick-summary cards surfacing the headline number from the deeper Stats sub-pages... (#1243) [Bug]
- Seerr app -- expanded request stats on the card/extras (#1247) [Enhancement]
- Added the 6 missing stats.samples.kind_* i18n keys (kind_stats='Container stats', kind_host_beszel_services... (#1263) [Enhancement]
- Stats -> Database growth projection now tracks the recent pace instead of over-reading (#1266) [Bug]
- Performance-review pass — chunked/offloaded every retention prune, memoized the Stats charts, closed the au... (#1284) [Enhancement]

### Prayer Times & Weather

- Weather provider overhaul + moon-phase widget + per-widget refresh + bookmark icon-URL field (#1070) [Enhancement]
- Weather fetch silently masked WeatherAPI in-body errors -> null history rows + empty widgets (#1097) [Bug]
- Weather admin UI polish + weather/moon stale-fallback display (#1099) [Bug]
- Weather widget 4x1 -- show the full user-configured forecast-day count instead of capping at 4 (#1118) [Bug]
- Prayer Times admin fixes. (A) Migrated the Service-enabled master toggle from a TUNABLE (tuning_prayer_time... (#1175) [Bug]
- Prayer-times custom-dashboard widget (#1177) [Enhancement]
- Prayer Times admin tab now has a "Recent samples" DB history panel mirroring Weather/Public IP (#1180) [Enhancement]
- Hard rename: the per-user topbar "Weather location" is now the shared "Your location" (userLat/userLon/user... (#1183) [Enhancement]
- Prayer-times topbar widget (#1193) [Enhancement]
- Prayer reminders — Fajr reminder silently skipped for users east of UTC (#1202) [Bug]

### Speedtest

- Speedtest Tracker app -- full encapsulation under per-app file structure (#1077) [Enhancement]
- App-skill framework + Speedtest run_speedtest skill -- the first of a per-app AI-skill pattern (#1144) [Enhancement]
- AI speedtest skill — live "show latest" + the web-dispatch bug that blocked it (#1154) [Bug]
- App-drawer speedtest skill result -- download / upload icons now render in distinct colours (#1162) [Enhancement]
- Speedtest averages window made a per-instance setting + label clarified (#1164) [Enhancement]
- App "Show extras" unified into ONE bidirectional control across the gear-flip card settings + the Admin ->... (#1165) [Enhancement]
- Bazarr app integration with extras (per-app encapsulation pattern, like Speedtest/APC) (#1185) [Enhancement]
- APC app card — show a loading/error/empty placeholder on the Apps tile like Speedtest (#1201) [Bug]
- Speedtest result image in the app drawer is wider (200px -> 350px) -- it's a detailed chart, not a small po... (#1262) [Bug]
- Speedtest result image clipped off the right edge in the app drawer on mobile (operator screenshot: the SPE... (#1276) [Bug]

### Media requests (Seerr)

- Seerr (Overseerr / Jellyseerr) app integration + AI movie request/suggest + TMDB wiring (#1187) [Enhancement]
- App cards: show the running app version (like Seerr/Bazarr) on AdGuard Home + Pi-hole (#1188) [Enhancement]
- App drawer Seerr "suggest a movie" result was unusable: the prose lines (movie overview / "Say request …")... (#1192) [Bug]
- Seerr 'suggest a movie' — widen the candidate pool for heavily-filtered users + make it operator-tunable (#1197) [Enhancement]
- Seerr suggest-a-movie — added a top-billed cast line (#1215) [Enhancement]
- TMDB poster images in the AI skill panel (Seerr suggestions) rendered as DIRECT image.tmdb.org links, which... (#1232) [Enhancement]
- Seerr -- new "List requests" skill (seerr_requests) lists the actual request TITLES (with year) in the queu... (#1242) [Enhancement]
- Bug fixes in seerr / prowlarr / qbittorrent / snmp / port_scanner_udp / webauthn (#1273) [Bug]

### Media servers (Plex, Jellyfin, Emby, Tautulli)

- Wire Plex as a per-app integration (logic/apps/plex.py + registry + static/js/apps/plex.js + _registry + pl... (#1245) [Enhancement]
- Wire Tautulli (Plex monitoring + statistics) as a per-host app, full encapsulation like Kavita (bespoke aut... (#1253) [Enhancement]
- Plex/Tautulli media-display fixes (3 operator-reported): (1) Plex AND Tautulli 'Recently added' grouped onl... (#1260) [Enhancement]
- Wired Tracearr (Plex / Jellyfin / Emby fleet monitoring + account-sharing detection -- github.com/connorgal... (#1267) [Enhancement]
- Plex 'What's playing on Plex' skill -- enhance the UI from a single text line per stream to the rich-item c... (#1274) [Enhancement]
- Tautulli 'Who's watching now' skill -- same rich-item enhancement as the Plex now-playing one (operator: 'd... (#1275) [Enhancement]
- Wired Jellyfin (open-source media server -- github.com/jellyfin/jellyfin) as a per-app integration followin... (#1278) [Enhancement]
- Added Emby as a new app template + wired the Emby application end-to-end (operator: 'add emby as a new app... (#1279) [Enhancement]

### *arr stack, downloads & transcoding

- qBittorrent catalog template port changes (#1040) [Enhancement]
- Radarr app integration (movie library manager) (#1203) [Enhancement]
- Bazarr — wired meaningful skills beyond status (#1210) [Enhancement]
- Sonarr app integration (TV-series manager) (#1213) [Enhancement]
- Wired Lidarr (music *arr) as a per-host app, full encapsulation like Sonarr/Radarr (#1222) [Enhancement]
- Radarr/Sonarr/Lidarr extras (#1224) [Enhancement]
- Wired Readarr (book/audiobook *arr) as a per-host app, full encapsulation like Lidarr/Sonarr/Radarr (#1226) [Enhancement]
- Release dates in *arr upcoming/calendar skills shown as raw ISO YYYY-MM-DD (#1231) [Bug]
- Wire Prowlarr (indexer manager, *arr stack) as a per-host app, full encapsulation like Lidarr/Readarr (it's... (#1233) [Enhancement]
- Wire Kavita (self-hosted digital library / reader) as a per-host app, full encapsulation like the *arr fami... (#1234) [Enhancement]
- Prowlarr app card -- "Apps synced" line restyled from raw text to brand-icon chips (#1249) [Enhancement]
- Radarr 'Upcoming movies' skill result now shows movie POSTERS next to each title (richer drawer UX) (#1255) [Enhancement]
- qBittorrent VueTorrent WebUI check + auto-update AI skills (#1261) [Bug]
- Lidarr + Readarr download-queue posters now resolve reliably (operator: Radarr/Sonarr OK after the remote-f... (#1268) [Enhancement]
- Wired Tdarr (distributed media-transcode automation -- github.com/HaveAGitGat/Tdarr) as a per-app integrati... (#1271) [Enhancement]
- Enhanced the STORAGE display in the *arr app drawers (Radarr / Sonarr / Lidarr / Readarr) -- operator: 'mak... (#1277) [Enhancement]

### DNS, ad-blocking & notification apps

- AdGuard Home catalog template (#968) [Enhancement]
- Pi-hole per-app integration (#1167) [Enhancement]
- App-skill audit-trail gaps closed (surfaced by the review pass; directly affects the Pi-hole + AdGuard flee... (#1168) [Enhancement]
- AI-context ts_display never stamped for AdGuard / Pi-hole `last`: registry.available_app_skills_context rea... (#1169) [Bug]
- Public IP — OmniGrid lagged well behind a dedicated DDNS updater on WAN IP changes (#1205) [Enhancement]
- CodeQL py/weak-sensitive-data-hashing on logic/apps/pihole.py:_sid_key (#1217) [Bug]
- Wired AdGuard Home Sync (bakito/adguardhome-sync) as a per-host app, full encapsulation pattern (#1219) [Enhancement]
- Wired ddns-updater (qdm12/ddns-updater) as a per-host app (#1227) [Enhancement]
- AdGuard Home Sync 'Recent sync logs' skill result is now pretty-formatted instead of a raw JSON dump (#1257) [Enhancement]
- Wired Apprise (caronc/apprise-api -- the notification gateway OmniGrid itself uses) as a per-app integratio... (#1258) [Enhancement]

### Forgejo, Beszel, Pulse, Webmin, Node Exporter & Portainer

- Discovery wizard. Scan a curated host's port table (port-scan results + node-exporter listen ports if avail... (#962) [Enhancement]
- App catalog template batch (Dozzle / Forgejo / MariaDB / PostgreSQL / MongoDB / InfluxDB) + port-scan coverage (#1004) [Enhancement]
- Forgejo + Portainer app-icon render height (too tall vs sibling brand icons) (#1016) [Bug]
- Pulse changelog not displaying -- release-notes resolver `get_release_notes` in `logic/registry.py` require... (#1082) [Enhancement]
- Host-drawer Beszel services -- add a live Refresh that re-probes the hub for fresh per-unit status (#1123) [Enhancement]
- Per-app instance Test-connection now shows a '✓ Last tested Xm ago' chip (parity with Portainer/OIDC) (#1221) [Bug]
- Wired Forgejo (self-hosted Git service -- a Gitea fork) as a per-app integration end-to-end following the p... (#1280) [Enhancement]

### AI Assistant, Cmd-K & Conversations

- Apps discovery exposed to the AI command palette (code-review Audit O) (#983) [Enhancement]
- Public-IP widget in Apps shows no data + IP history persisted to DB for AI questions (#1066) [Enhancement]
- AI answers per-host telemetry questions directly + auto-resolves a single matching host (#1092) [Enhancement]
- Performance -- memoize the AI-sidebar markdown render (#1093) [Bug]
- AI assistant renders inline image previews (#1156) [Bug]
- AI host status — the assistant labelled all 'problem' hosts as 'Down' (7) while the web showed only 1 down (#1212) [Bug]
- AI cleanup count discrepancy (#1216) [Bug]
- Web AI — selecting a skill action (e (#1229) [Bug]
- AI palette can answer 'what was I looking at on the other tab / desktop / phone?' -- the cross-device-hando... (#1238) [Enhancement]

### HTTP probe & Service probe

- Apps HTTP probe now falls back to GET when a service rejects HEAD (#980) [Bug]
- HTTP probe failed with "server rejected the SNI (unrecognized name)" even with verify SSL unchecked (#1037) [Bug]

### Public IP widget

- Public-IP card -- ISP brand-icon graceful-fallback when the matched brand's SVG file doesn't exist on disk... (#1081) [Bug]
- Public-IP change-detection sampler + Admin loading-state (#1096) [Enhancement]
- Public-IP widget no-data + geo-flag fixes (#1108) [Bug]
- Public-IP widget -- 3x1 / 4x1 IP hero too big, hiding country + AS (#1128) [Bug]

### Authentication, passkeys, OIDC & 2FA

- Authentik Test ✗ surfaces actual detail + Test button stays clickable after a failure (#1071) [Bug]
- Authentik admin -- Clear / Copy buttons have different styles; unify them (#1116) [Enhancement]

### Security (CodeQL & token discipline)

- CodeQL SSRF + path-injection findings (#960) [Enhancement]
- CodeQL py/stack-trace-exposure fix at the release-notes handler (main.py api_registry_release_notes) (#994) [Bug]
- CodeQL security alerts #491 + #492 (two fixes) (#1195) [Enhancement]
- Remediated the CodeQL alert 'Use of a broken or weak cryptographic hashing algorithm on sensitive data' (py... (#1283) [Bug]

### Real-time / SSE event stream

- Cross-tab Cleanup sync. **Re-opened for validation (#1023) [Bug]

### SNMP

- SNMP iDRAC (Dell-marked host) timing out (#998) [Enhancement]

### Ping

- Ping Test double-checkmark fix (#1073) [Bug]

### UPS / battery

- Added APC built-in catalog template to `logic/service_catalog.py:_BUILTIN` (#1069) [Enhancement]
- APC app -- per-instance + per-template `Show extras` checkbox (#1076) [Bug]
- APC app card adjustments (5 parts) (#1132) [Enhancement]
- Renamed the .apps-card-ups-value CSS class to the generic .apps-card-stat-value across all 13 app-extras pa... (#1244) [Enhancement]

### Provider chips, icons & status pills

- Host-drawer Apps sub-tab + per-host chip strip (#963) [Bug]
- Dedupe service-chip probe-target resolution into one shared helper (#978) [Enhancement]
- Hosts page: app chips below the provider chips (#995) [Enhancement]
- i18n Audit P — hardcoded chart/chip text (#999) [Bug]
- App-chip icon resolution now prefers the catalog template's icon field over its slug (#1002) [Bug]
- Duplicate app chips on a host (#1026) [Bug]
- Port-chip status dot moved BEFORE the number for parity with Apps (#1039) [Enhancement]
- Unified the faulty/paused provider-chip colour to RED across every surface (#1043) [Bug]
- Host-drawer per-provider chip subtitle restored to STACKED lines (#1059) [Bug]
- App port-chip URL links de-blued (#1060) [Enhancement]
- Egyptian carrier brand icons + canonical-slug consolidation (#1083) [Enhancement]
- Release-notes (stack update "What's new") code chips shattered character-by-character at line wraps -- e.g (#1166) [Bug]
- Host-drawer Timeline range picker (24h/7d/30d) hand-rolls a btn-ghost chip strip with a :class active toggle (#1171) [Enhancement]
- Add app brand icons for GitHub + Google Cloud Source Repositories (GCSR) (#1196) [Enhancement]

### App cards, Apps page & custom dashboard

- Top-level "Apps" view + reusable service templates + multi-port probes (#961) [Enhancement]
- Apps view did not auto-load on page load / refresh (#974) [Enhancement]
- Apps section i18n — route tooltip / latency-unit concatenations through t() format strings (#975) [Bug]
- Apps view not reflecting instance edits (#985) [Enhancement]
- Apps view (main page) group-by-host mode (#987) [Enhancement]
- Apps-view + UI-review-finding batch (#1003) [Bug]
- Per-row apps-count badge on the Hosts view (#1009) [Enhancement]
- Apps-view app-detail drawer 'Show debug' now wired to the Admin -> Debug debug_panel_enabled tunable (+ adm... (#1019) [Enhancement]
- Apps view ("By app" cards) redesigned: 3x app logo on the LEFT, all content to its right (#1020) [Enhancement]
- Apps Custom dashboard — Homarr/Homepage-parity board (Phase 1, fully shipped; folds in the edit/lock + hete... (#1053) [Enhancement]
- Apps Custom Phase-1 polish (#1057) [Enhancement]
- Apps Custom layout not persisted across restart (#1065) [Bug]
- Apps Custom widget + card UX overhaul (#1067) [Enhancement]
- Apps Custom dashboard -- per-card SIZE presets + flip-to-settings UX (#1086) [Enhancement]
- Apps widget + card UI fit (chosen-size responsiveness, round 2) (#1095) [Bug]
- Apps first-load perf -- a batch of first-paint, render-cadence, and reactivity fixes (#1102) [Bug]
- Apps custom dashboard -- MULTIPLE NAMED VIEWS (#1104) [Enhancement]
- Bug: Apps custom-dashboard card/widget HEIGHT preset (short/tall) not persisted on reload -- only the width... (#1120) [Bug]
- Bug fix: Apps custom-dashboard dropdown showed the wrong view vs the one displayed (#1126) [Bug]
- Apps custom dashboard -- duplicate app cards across sections (shadow copies) (#1129) [Enhancement]
- Unsectioned staging tiles double width in Apps edit mode (#1158) [Enhancement]
- apps-card-meta (the grey app description/meta line) sat with a large gap below the app title, worst in the... (#1178) [Bug]
- Apps-with-extras 'Loading …' placeholders now show a subtle spinner before the text (#1218) [Enhancement]
- Apps view — added an 'Extras only' capability filter (#1220) [Enhancement]
- Apps custom-dashboard named "views" -- per-view visibility (private/public) + edit permission (#1246) [Enhancement]
- Apps custom-dashboard tiles now tile flush -- fixed the wasted right-edge gap (#1252) [Enhancement]
- Apps custom-dashboard bookmark tile at 1x1 (half width) now shows its URL (#1256) [Enhancement]
- Topbar/Apps widget 'Updated X ago' now ages from the real data fetch time, not the SPA receive time (#1264) [Bug]

### App drawer, skills & actions

- Docker-link inline actions (#964) [Enhancement]
- App instance editor Link-to-Docker is now a searchable combobox + the Instances table shows a Docker-linked... (#1044) [Enhancement]
- App drawer now renders the per-app extras box (#1143) [Bug]
- App drawer "Probe now" button gave no feedback on click -- a successful probe showed nothing (only an error... (#1146) [Bug]
- App drawer: per-app SKILL buttons moved into their OWN boxed card AFTER the extras box (was crammed into th... (#1149) [Enhancement]
- ai_phrases is shipped-but-DEAD: every per-app SKILL declares ai_phrases but available_app_skills_context em... (#1170) [Bug]
- Unify app skill-button styling across every app (#1194) [Enhancement]
- App drawer skill-result boxes (#1200) [Enhancement]
- App drawer — added a top refresh button (mirrors the host drawer's header refresh) (#1206) [Enhancement]
- Unify app-extras presentation across ALL apps (#1230) [Bug]
- Show-extras toggle -- per-instance for non-aggregate apps + a scope label for aggregate ones (#1251) [Bug]
- Admin -> Apps instance list: added a tiny 'has extra features' indicator (icon-zap, --info accent) next to... (#1254) [Enhancement]
- App drawer 'Open in Stacks' button for Docker-linked apps (#1259) [Enhancement]
- App drawer per-app ACTION (skill) buttons must be gated on the per-instance 'show extras' checkbox, same as... (#1272) [Enhancement]
- Unified the drawer-header close-button height across all drawers (operator screenshot: in the app drawer th... (#1281) [Enhancement]

### App catalog, templates & instances

- TTL'd module-level catalog cache for `_shape_host_apps` (#966) [Enhancement]
- Fix NameError "name '_ops_mod' is not defined" when saving or re-seeding an Apps service-catalog template (#969) [Bug]
- Surface why Apps instances show degraded (#970) [Bug]
- Catalog-pinned apps were never probed (#976) [Bug]
- Apps instance editor + template inheritance (#982) [Enhancement]
- Admin -> Apps Instances grouping (group by host / service / none) (#984) [Enhancement]
- Monitoring / backup agent catalog templates (#988) [Enhancement]
- Discovery wizard no longer proposes a second app for a port another app on the host already owns (#1001) [Enhancement]
- Discovery wizard now shows ALL applicable templates for an unclaimed open port (user-decided design) (#1005) [Enhancement]
- Add Splunk catalog template (8080) (#1012) [Enhancement]
- Port-scanner coverage batch + pinned-app catalog-port union (#1022) [Enhancement]
- Bulk delete for Apps instances (#1031) [Enhancement]
- Built-in catalog template changes now PROPAGATE to existing DB rows (#1032) [Bug]
- App Templates search / filter box (#1033) [Enhancement]
- Apps instance editor: couldn't delete a port + no title icon (#1036) [Enhancement]
- App Instances search box (#1041) [Enhancement]
- Unified the mail icon — the Email (SMTP) catalog template now uses the same icon as host mail (#1045) [Enhancement]
- Added a MySQL catalog template (#1049) [Enhancement]
- Added a Proxmox VE catalog template (#1051) [Enhancement]
- Add an OPNsense firewall catalog template: 80 http + 443 https (Web UI, open_url) (#1055) [Enhancement]
- Catalog-template probe semantics validated + fixed so a healthy app no longer reads 'down' (#1058) [Bug]
- Admin -> Config tunables save/dirty audit + registry drift audit (#1134) [Bug]
- Per-app data-cache TTL operator-configurable IN THE APP (not global Config TUNABLES) (#1173) [Enhancement]

### Drawer, charts & sparklines

- Apps detail + debug drawer (mirrors the host drawer) to diagnose why an app on a host isn't working (#977) [Enhancement]
- Host drawer port-scan -> mapped-app annotation (#989) [Enhancement]
- Copy button in the Apps debug drawer (#1010) [Enhancement]
- Host-drawer Apps "Probe all" button (4th report of "probe disabled" in the host drawer) (#1011) [Enhancement]
- Host-drawer HTTP-probe box 'Latency' value now thousand-separated (#1017) [Enhancement]
- Host-drawer Services/ports: documented asset ports NOT found by the latest port scan now show an alert marker (#1021) [Enhancement]
- Host-drawer FULL-refresh button (#1038) [Bug]
- Node-view sparklines sometimes blank -- two-cause fix (#1089) [Bug]
- Host-drawer Apps -- 'Probe failed' button to re-probe ONLY the down services (#1124) [Bug]
- Container / stack item drawer now shows the RUNNING app version after the name (#1223) [Enhancement]
- Item drawer Placement list showed phantom '? (#1225) [Bug]

### Hosts editor, Host groups & Hosts page

- Apps hosts_config writes routed through a single validated persist choke point (a code-review finding) (#981) [Enhancement]
- Fixed runtime NameError: name 'PortScanIn' is not defined on the port-scan route (#996) [Bug]
- Port scan now always includes the host's configured app/service ports (#997) [Enhancement]
- Pin-to-host picker converted to a searchable/filtering combobox (#1006) [Enhancement]
- Asset port section now surfaces scan-only ports (reverse of the existing scan->asset mismatch marker) (#1008) [Enhancement]
- app-asset.js IDE-warning cleanup (#1015) [Bug]
- Admin -> Hosts HTTP-probe: typed URLs were cleared on Save (#1018) [Bug]
- Port-scan: code DEFAULT_PORTS is now a FLOOR, custom CSV ADDS (no longer shadows) (#1025) [Bug]
- Remove-app-instance confirm popup now names the app + host (was generic) (#1027) [Bug]
- Pin-to-host modal: header pin icon + assign to MULTIPLE hosts at once (#1030) [Bug]
- Discover-on-host wizard polish (#1034) [Enhancement]
- Discover "pin selected" applies ONLY the host's matched ports (#1042) [Enhancement]
- Added port 7680 (WUDO — Windows Update Delivery Optimization peer-to-peer cache) to the port scanner's DEFA... (#1052) [Enhancement]
- Quieted the http_probe no-SNI-retry red-ERROR log noise for reachable-but-SNI-strict hosts (#1061) [Bug]
- Duplicate 'Host sampling paused' notifications -- one host re-notified every redeploy / hosts_config save i... (#1115) [Bug]
- Hosts top-nav red down-host count badge (parity with the Services offline badge) (#1121) [Enhancement]
- Distinct 'resuming' indicator for a just-resumed host (was indistinguishable from paused-in-error -- both red) (#1122) [Enhancement]
- Bug fix: Hosts provider / problem filter pills stopped filtering (#1125) [Bug]
- Bug fix: port_scan_refresh schedule starved hosts (most 5d old, some 22d old, despite a 1d schedule reporti... (#1139) [Bug]
- Apps card multi-host layout -- hosts side-by-side for 2+ host apps (#1163) [Enhancement]
- Port-scan default coverage extended to common TCP + UDP service ports so the app always probes the ports a... (#1181) [Enhancement]
- Hosts page — 'problem' filter showed a count that disagreed with the rendered rows (#1204) [Enhancement]
- Consolidated repeated external-URL/host string literals into a shared typed-constant module (operator asked... (#1265) [Enhancement]
- Admin -> Asset Inventory 'Cached snapshot' preview is now a collapsible JSON tree viewer instead of a flat... (#1285) [Enhancement]

### Admin & Settings pages

- Enforce typed-enum discipline for tunable / setting / env-var key references (#967) [Enhancement]
- Admin -> Apps tab strip restyle for cross-admin consistency (#986) [Enhancement]
- Admin -> Logs file viewer capped at 500 lines (#992) [Bug]
- Relocated three operator tunables out of Admin->Config into the domain sections that govern them (user-requ... (#1098) [Enhancement]
- settings_update audit now shows per-key OLD -> NEW values (#1100) [Enhancement]
- Admin tunables-UI consistency sweep (#1101) [Enhancement]
- Admin-section performance pass -- reactive-flush + event-loop optimisations across the admin surface, landi... (#1103) [Bug]
- Admin Providers -- per-provider collapsible Tunables menus (#1105) [Enhancement]
- Admin panel dirty/undirty corrections -- controls that didn't mark their panel's Save dirty (#1107) [Enhancement]
- Admin master-toggle loading indicator -- consistent spinner + "Loading…" pill on every master-toggle admin... (#1148) [Enhancement]
- Custom Apps edit-mode delete button -> pill (match settings/refresh) (#1157) [Enhancement]
- Admin-page gating consistency batch (#1176) [Enhancement]
- Admin -> Schedules "Create schedule" Kind dropdown now sorted alphabetically by its displayed (translated)... (#1179) [Enhancement]
- Image-proxy disk cache (operator request: cache images so the same picture isn't re-downloaded each view, e... (#1269) [Enhancement]

### Logs view & retention

- logs.py error-classification regex handles Python 3.11+ ExceptionGroup tracebacks (#972) [Bug]
- Thousands separators on ms log values (#1153) [Enhancement]

### Schedules & automation

- Scheduler wedged-run self-heal (#1050) [Bug]

### Notifications & toasts

- Update-stack 'What's new' release notes -- fixed raw HTML tags, GitHub alerts, and double bullets (#1138) [Enhancement]

### Topbar, login & branding

- app-topbar.js -- three IDE inspection findings cleared with genuine code fixes (no suppression) (#1088) [Bug]

### Filters, badges & pagination

- Apps: every app now shows its port pill (was multi-port only) + ports can be CLICKABLE links (#1028) [Enhancement]
- Stacks / Services -- hide the empty header-only table + show a reset-able empty-state when a filter matches... (#1130) [Bug]
- Bug fix: Nodes view kept showing the previous filter's containers after switching filters (#1131) [Bug]
- Apps-page tile refresh pill now spins + disables while the app loads its INITIAL extra/card data (operator:... (#1282) [Enhancement]

### Internationalisation & accessibility

- UI/UX review batch — frontend a11y / i18n / visual fixes, all shipped together (#1054) [Enhancement]
- UI/UX accessibility + token + i18n pass on the Apps surface (the ux-review action pass) (#1094) [Enhancement]
- Extended scripts/audit_html_drift.py with 3 commit-time a11y-structural checks: every aria-modal="true" dia... (#1236) [Enhancement]

### Database / migrations / data

- fmtResponseError migration (#1072) [Bug]
- Extract init_db() out of main.py into its own module (#1109) [Enhancement]
- Keyboard shortcuts — added 3 page jumps + rebalanced the cheat-sheet columns (#1241) [Enhancement]

### Internal cleanup, refactor & bug sweeps

- Consolidated per-sampler numeric-coercion helpers into a shared logic/coerce.py leaf module (a code-review... (#979) [Enhancement]
- app-minor-tools.js IDE-warning cleanup (#991) [Bug]
- PERF: frontend + backend performance optimization pass -- consolidates the perf-review implementation that... (#1013) [Enhancement]
- Code-review fix batch (all LOW findings from the review pass; shipped together) (#1056) [Bug]
- Code-review fix batch from the latest review report (#1064) [Bug]
- UX-review fix-all pass on the latest UX-review report (#1068) [Enhancement]
- Local linter (scripts/lint.py) -- IDE-inspection-mimicking + scripts/install_precommit.py hook installer (#1084) [Bug]
- Lint pre-commit shield -- checked-in, cross-machine, blocks on warnings (#1090) [Bug]
- Timestamp-format drift audit + consolidation (#1150) [Enhancement]

### Other improvements & fixes

- Per-port historical detail (#965) [Enhancement]
- UTF-8 encoding on Path.read_text() / open() calls (#971) [Enhancement]
- Post-split cross-module underscore-symbol wiring (#973) [Bug]
- http_probe TLS unrecognized-name (SNI) error (#990) [Bug]
- npm dev-dep bump: stylelint 16.26.1 -> 17.12.0 and stylelint-config-standard 36.0.1 -> 40.0.0 (package.json... (#993) [Bug]
- Convention-violations review pass (#1000) [Bug]
- Apps "probe not enabled / disabled" diagnosed + debug panel enhanced (#1007) [Bug]
- PERF: replace x-show with x-if on costly subtrees so they unmount when hidden (#1014) [Enhancement]
- Top-bar recent-tabs privacy: a user now only sees THEIR OWN tabs, never other users' (#1024) [Bug]
- Probe correctness for UDP + legacy-TLS endpoints (two user-reported "always down / handshake error" cases) (#1029) [Bug]
- HTTP-probe "Accepted status codes (CSV)" field cleared on every keystroke (#1035) [Bug]
- Open-as-URL port link no longer appends health-check probe paths (#1046) [Enhancement]
- App loading spinners now show a "Loading…" label (#1048) [Enhancement]
- Backend-unreachable top banner (#1062) [Enhancement]
- Service degraded/down counter not displayed (#1063) [Enhancement]
- MAJOR stability fix — tracemalloc default-ON was the crash-loop root cause (#1074) [Bug]
- SPA -- xterm.js lazy-load eliminates steady-state Chromium `[Violation] Added non-passive event listener to... (#1080) [Bug]
- SPA -- requestAnimationFrame violation probe (opt-in diagnostic) (#1085) [Enhancement]
- Sprite-preload "preloaded but not used" console warning fixed (#1091) [Bug]
- Update-stack release-notes popup -- render markdown/HTML annotations + Copy button (#1106) [Enhancement]
- Item/stack icon improvements (#1112) [Enhancement]
- Reload-button consistency (#1113) [Bug]
- Shared Test-connection component (#1117) [Enhancement]
- Moon widget -- phase name cropped at 1x2 / 2x2 (#1127) [Enhancement]
- Clock (digital) widget -- enlarge clock + date/location in 3x2 / 4x2 (#1135) [Enhancement]
- deps(pip): bump python-multipart from >=0.0.29 to >=0.0.30 in requirements.txt (Dependabot dependency bump) (#1142) [Enhancement]
- Remove-containers confirm dialog UI -- long Swarm task-container names (one unbreakable token) sat far righ... (#1145) [Enhancement]
- Run-speed-test skill observability (#1151) [Bug]
- Release-notes renderer now handles git-cliff / conventional-commit format (#1155) [Enhancement]
- Finish the [06] fleet-module dedup follow-up: after the _common.py fleet helpers, the run_skill dispatch la... (#1172) [Enhancement]
- Stacks/Services tables — long resource name (e (#1184) [Bug]
- Four raw issues fixed in one batch (#1228) [Enhancement]
- History page: a long op target (an ssh_run target is the FULL command -- hundreds of chars) overflowed the... (#1270) [Enhancement]
