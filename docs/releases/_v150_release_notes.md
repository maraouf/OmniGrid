## What changed in 1.5.0

Fifth MINOR cut on top of `1.4.0` — rolls up **162 closed issues** under the 1.5.0 milestone (115 enhancements, 47 bug fixes). Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Highlights

- Telegram bot integration end-to-end — `/link` / `/whoami` / `/weather` / `/host` / `/help` / `/cleanup` / `/version` / `/update` / `/hosts` commands, free-text AI grounded in real fleet state with Thinking-indicator, AI-fallback security gate, multi-chat-id CSV, history-table audit per dispatch, `/update all` correctness pass, AI palette can route notifications to one chat.
- Stats dashboard family expanded — five new sub-pages (Database, Samples, Network, AI Cost, Incidents) with per-table KPIs, per-provider drill-down popovers, daily-INSERT bar charts, fleet-wide throughput KPIs, finance-style ai_jobs view, incident-centric host_failure_events view, deep-link routes, range-pickers honouring user Formats preference.
- AI palette diagnostic capabilities — multi-round tool-use orchestrator (DB queries + SSH-gated diagnostics), AUTONOMOUSLY diagnoses container-bloat / disk-growth questions via tool-use instead of dispensing shell commands, schedule CRUD actions, `send_notification` action, fuzzy name-matching, refresh / reload aliases, action coverage + dispatch-gap closures.
- HTTP probe + Service probe — 7th + 8th host-stats providers with native HTTP / TLS-cert / DNS health checks, per-host editor, drawer card with status pill strip + TLS-expiry pill + DNS pill, master toggle + alias CSV, one-shot Test endpoint, Test-before-Save gate.
- Provider-chip UX overhaul — per-provider chip click → popover with last-probe result, chip-strip vocabulary legend popover, distinct chip-state colour mapping (paused = blue not grey), chip-rendering consolidation via canonical `_PROVIDER_DEFS` registry, Hosts-view chip strip moved to its own line under the row subline.
- Drift-from-baseline + Drift-chip — schema `host_baselines(host_id, metric, median, iqr)`, in-place reconcile, drift indicator on Hosts rows with sparkline + drift-chip UI enhancement.
- Multi-tab activity sync + Reproduce-here handoff — desktop tab's filter / drawer / sub-tab state can be mirrored into a phone in one click; richer per-tab state surfaces under the user-avatar menu.
- Settings-as-Code + Config Backup — new admin tab + schedule kind to back up + restore the operator-tunable admin configuration; saved-snapshots table matches Admin → Backups styling.
- Telegram + AI cross-cutting plumbing — every authorised Telegram command writes ONE history row, AI free-text calls log into `ai_jobs` + `history`, AI palette context carries time + weather, free-text replies honour `tuning_ai_max_tokens`.
- Sortable tables fleet retrofit + per-tab tunable search affordance + Admin → Logs pattern-filter axis — operator-typed search filters tunables across Admin / Providers tabs; logs filter strip gained pattern chips (auth_cooldown / probe_timeout / sql_drift / provider_paused / ws_disconnect / sampler_skip / cors_csrf).
- Internal modularisation — split `static/js/app.js` (~40k lines) into native ES modules (no build step); EnvKey + Tunable enum families replace bare-string keys for IDE jump-to-definition + autocomplete + typo detection; defer chart rendering until host drawer opens.
- Stack-update + Single-update popups now carry blast-radius preview + release-notes integration (fetched from registry image labels), Switch-to-tag affordance generalised across container + Portainer-deployed Swarm services, Recreate-silent-success detection for external containers.

### Telegram bot

- Telegram notification medium (#866) [Enhancement]
- Telegram notification medium (#867) [Enhancement]
- Telegram Phase 2.2 — user mapping + /weather + /whoami + /link + AI fallback + security gate (#868) [Enhancement]
- Notifications admin — Save-button split + Telegram link admin datatable + /hosts grouped lists + weather re... (#869) [Enhancement]
- Profile → Telegram card now auto-refreshes when `/link` or `/unlink` runs in Telegram (#871) [Bug]
- Telegram free-text AI replies now honour `tuning_ai_max_tokens` instead of a hardcoded 512 cap (#872) [Bug]
- Telegram `/cleanup` command (#874) [Enhancement]
- Telegram AI free-text is now grounded in real fleet state (#875) [Enhancement]
- Telegram AI now shows a "Thinking…" placeholder + native typing indicator while the model is running, then... (#876) [Bug]
- Telegram free-text AI calls now log into the `ai_jobs` table (Admin → AI Usage dashboard) and the `history`... (#877) [Enhancement]
- Every authorised Telegram command now writes ONE row into the `history` table at the dispatcher level (#878) [Enhancement]
- Telegram `/host <target>` command (#879) [Enhancement]
- Telegram `/help` now lists every command + groups aliases inline on one line (#880) [Enhancement]
- Telegram `/link` now detects an already-linked sender and refuses with a clear message before falling throu... (#882) [Bug]
- Telegram AI palette context now carries current time + (when configured) weather, closing the gap that made... (#883) [Enhancement]
- Telegram bot improvements (#888) [Enhancement]
- Lint + audit-trail + Telegram-command-correctness sweep (#891) [Bug]
- Telegram chat_id CSV — accept multiple authorised chats (option 2) (#892) [Enhancement]
- Telegram admin tab — merge Test Connection + Save toasts into one feedback box (#893) [Enhancement]
- Telegram /update preview wrongly includes orphan-type containers (#895) [Bug]
- Telegram AI gave inaccurate host status (#898) [Bug]
- Notifications admin Save UX split + Telegram AI host-status reconciliation + Test-pass optimistic stamp (#900) [Enhancement]
- Telegram `/update all` (and SPA bulk update) (#913) [Bug]
- Telegram `/version` build-time line now honours the sender's date/time format pref + deployment TZ (#915) [Bug]

### Stats dashboard

- Stats → AI Cost — added a bar-chart visualisation of the existing `avg_response_time_trend` data (backend a... (#812) [Enhancement]
- Stats section in the user-avatar menu (#816) [Enhancement]
- Stats Dashboard — new Database sub-page (#821) [Enhancement]
- Stats — new Samples sub-page with per-table KPIs (#823) [Enhancement]
- Stats → Database polish — Rows column populated + thousands separator + chart gridlines (#824) [Enhancement]
- Stats — new Incidents sub-page (incident-centric view of host_failure_events) (#826) [Enhancement]
- Stats — new AI Cost sub-page (finance-style view of ai_jobs) (#827) [Enhancement]
- Stats — new Network sub-page (fleet-wide throughput KPIs) (#828) [Enhancement]
- Stats data wired into AI palette context (#829) [Enhancement]
- Stats deep-link routes — `/stats` + `/stats/<sub>` page-refresh returns 404 (#830) [Enhancement]
- Stats → Network — burst-rate table now follows the page's selected range (#833) [Enhancement]
- Stats charts loading effect (#834) [Enhancement]
- Stats → Samples — per-provider drill-down popup landed (#844) [Enhancement]
- Stats → Samples — added `<hr>` separators around the "Per table breakdown" section AND a new bar-chart sect... (#855) [Enhancement]
- Stats page rendering blank -> root-caused as a TWO-bug interaction (#884) [Bug]
- Stats sub-tab section-range pickers (#909) [Enhancement]
- Stats charts — every x-axis date label now honours the user's Formats preference (#910) [Enhancement]
- Native HTTP / TLS-cert / DNS health probe provider (7th host-stats provider) (#925) [Enhancement]
- Stale "Host stats" terminology cleanup + Pulse/Webmin tab-label i18n fix (#941) [Bug]
- i18n concat-shape sites migrated to canonical template-with-placeholders shape across the Stats dashboard t... (#947) [Enhancement]
- Stats dashboard grid-template extracted from inline `style=` to `.stats-card-grid` class (#948) [Enhancement]
- Bug batch — three user-flagged regressions caught after the per-chip popover + chip-strip legend + Stats →... (#956) [Enhancement]

### AI Assistant, Cmd-K & Conversations

- AI palette: schedule CRUD actions (operator-requested follow-up to the retag_image AI dispatch) (#810) [Enhancement]
- AI conversation history not preserved across different computers (#814) [Enhancement]
- Admin → AI Integration dashboard popups (#815) [Enhancement]
- AI sidebar mode select aria-describedby (#832) [Enhancement]
- AI palette dispatch gaps closed (#835) [Enhancement]
- i18n missing-key batch + Cmd-K verb-overlap fix (a) added 24 `command_palette.action.*` keys for the newly-... (#837) [Enhancement]
- AI palette refresh / reload aliases (#840) [Enhancement]
- AI palette name-matching — fuzzy search + forward stack field + raise item cap (#849) [Bug]
- History → AI-Diagnose button on error rows (#856) [Enhancement]
- AI palette system prompt — taught the AI how to diagnose "Collecting data" sparkline placeholders (#863) [Bug]
- AI palette action coverage + Portainer/asset-inventory/NE TUNABLE wiring discipline (#865) [Enhancement]
- AI reports wrong host count -> answers with the prompt-sample cap (30) instead of the actual fleet size (#886) [Bug]
- AI palette — new `send_notification` action lets the operator route a custom (operator-typed) message to ON... (#899) [Enhancement]
- AI palette deeper diagnostic capabilities (#902) [Enhancement]
- AI palette diagnostic tools (#906) [Enhancement]
- AI palette now AUTONOMOUSLY diagnoses container-bloat / disk-growth questions via tool-use instead of dispe... (#916) [Enhancement]
- SPA hint when AI provider times out without fallback engaging (#924) [Enhancement]
- app-ai.js noinspection-strategy revised + 5 fire-and-forget call sites made explicit (#939) [Bug]

### HTTP probe & Service probe

- Hosts view — Ping (HTTP probe) provider icon missing from the per-host provider-chip strip in the row header (#928) [Bug]

### Authentication, passkeys, OIDC & 2FA

- Portainer + OIDC test buttons (#820) [Enhancement]
- Lint-discipline batch — `_safe_int` / `_safe_float` / `_int_or_none` / `_float_or_none` helper pattern adop... (#890) [Bug]

### Security (CodeQL & token discipline)

- CodeQL `py/path-injection` alert on `api_serve_avatar` (#808) [Bug]
- CodeQL `py/path-injection` on `/api/admin/config-backup/saved/{name}` family (#817) [Enhancement]
- fixes — visual + CSS token discipline (#901) [Enhancement]

### Real-time / SSE event stream

- SSE cross-tab self-filter gap closed for `port_scan:completed` (#838) [Enhancement]
- Admin → Config Backup — Saved snapshots table now matches Admin → Backups table styling for cross-tab consi... (#853) [Enhancement]
- Multi-tab activity sync panel shipped (#857) [Enhancement]
- Tab-activity richer state + Reproduce-here handoff (#905) [Enhancement]

### SNMP

- SNMP gate consistency — chip + chart-mount + bar-capability all consume the SAME strict `_snmpHasProbeTarge... (#805) [Bug]
- Host status taxonomy false-positives — SNMP-mapped + _stale_fields fixes (#907) [Bug]

### UPS / battery

- Hosts page CPU bar locked at full red on APC UPS (and similar UPS / printer / dumb-network-gear hosts that... (#860) [Bug]

### Beszel, Pulse, Webmin, Node Exporter & Portainer

- Hosts-page header — add Portainer "Open" button alongside the existing Beszel + Pulse buttons (#803) [Enhancement]
- Container recreate fix — `_do_update_container` POSTed to `/containers/{id}/recreate?PullImage=true` with N... (#806) [Bug]
- Pulse + NE sample-interval tunables with inherit semantics shipped (#843) [Enhancement]
- Switch-to-tag affordance now available for Portainer-deployed Swarm services (was previously container-only) (#922) [Bug]

### Provider chips, icons & status pills

- 5G + FTTH router brand icon refresh (#798) [Enhancement]
- Host drawer enabled-agents chip (#842) [Enhancement]
- Host-row sparkline + drift-chip UI enhancement (#854) [Enhancement]
- Toolbar provider chip visibility now gates on CONFIGURED hosts, not probe-succeeded hosts (#926) [Bug]
- Admin → Hosts per-row provider chip strip (#934) [Enhancement]
- SPA chip-rendering consolidation + Hosts view chip-line layout (#936) [Enhancement]
- Per-provider chip click → small popover with most-recent probe result (workflow-compressor; no new backend... (#951) [Enhancement]
- Provider chip-state vocabulary popover (#952) [Enhancement]
- Provider chip-state colour distinctness (#957) [Bug]
- Profile → Notifications per-category provider chips now always render on their own line below the category... (#959) [Enhancement]

### Drawer, charts & sparklines

- Host drawer charts — X-axis flips to MMM-d (e (#813) [Enhancement]
- Host drawer Temperature + GPU Power Draw charts stuck on "Collecting data" when local samples lack temps/gpus (#825) [Bug]
- Host drawer charts — range-aware x-axis tick density (#845) [Enhancement]
- Drawer-chart bucketing — unified 120-point density across every host-drawer time-series endpoint (#846) [Bug]
- Two operator-flagged drawer-region cleanups (#847) [Bug]
- Debug panel for Stacks / Services / Nodes drawers (mirrors the host-drawer panel) (#861) [Enhancement]
- Idle-item "Collecting data" false-positive on 0% CPU services (#862) [Bug]
- Shared `og-range-picker` Alpine component template + multi-pass include expander (#903) [Enhancement]
- Service / container drawer now surfaces every exposed port with a clickable external link (#917) [Enhancement]
- Host-drawer chart `Last sample Xm ago` label now reflects newest raw-sample age regardless of window (#927) [Bug]
- Defer chart rendering until host drawer opens (#932) [Enhancement]
- Drawer-heading font-size token family extended for the 16-18 px range (#942) [Enhancement]
- SPA modules noinspection blocks expanded to match the categorical pattern across `app-charts.js`, `app-noti... (#943) [Bug]
- RTL flip for drawerNode + drawerItem + `.host-drawer` (#944) [Enhancement]
- Shared og-range-picker ARIA contract corrected (#946) [Enhancement]
- Asset-source SVG indicator in the host drawer header gained a screen-reader-readable accessible name (#949) [Enhancement]

### Hosts editor, Host groups & Hosts page

- Standalone containers on worker nodes (#804) [Bug]
- Retag affordance generalised — "Switch to :latest" becomes "Switch to tag…" (#807) [Enhancement]
- Single-update popup release-notes integration (#848) [Enhancement]
- Stack-update confirm dialog now carries a blast-radius preview (#851) [Enhancement]
- Drift-from-baseline indicator on Hosts rows (#859) [Bug]
- Consolidate the `hosts_config` JSON parse skeleton across 10+ sampler / consumer sites (#896) [Enhancement]
- Problem-hosts triage filter (#904) [Enhancement]
- Host status taxonomy false-positive (#908) [Bug]
- Recreate-silent-success on external containers (#914) [Enhancement]
- HTTP-probe per-host editor (#929) [Enhancement]
- Port-scan schedule fired every cadence-tick but EVERY host fell into `skipped["disabled"]` because the sche... (#933) [Bug]
- Removed redundant "N providers" / "2 providers" subline text from the host card on the Hosts view (#940) [Enhancement]
- Hosts view desktop grid — Disk column width parity with CPU + Memory; freed space reallocated to the Host (... (#945) [Bug]

### Admin & Settings pages

- Settings-as-Code — new admin tab + schedule kind to back up + restore the operator-tunable admin configurat... (#809) [Enhancement]
- Admin polish batch — three operator-flagged UI consistency passes shipped together (#811) [Enhancement]
- Audit log date filters honour the user profile date format (#818) [Enhancement]
- Test Connection buttons across admin (#819) [Enhancement]
- Stuck-disabled admin reload buttons (#822) [Bug]
- Admin write-actions audit-trail (#831) [Enhancement]
- Plain-settings escape hatch migration (#836) [Enhancement]
- TUNABLES coverage gap audit batch (#839) [Enhancement]
- Admin audit-trail coverage batch (#841) [Enhancement]
- Two new TUNABLES promoted from hardcoded constants (#852) [Enhancement]
- Tunable enum replaces bare-string tunable keys for IDE jump-to-definition + autocomplete + typo detection (#873) [Enhancement]
- Admin checkbox + table loading UI canon (#889) [Enhancement]
- Switch host_failure_events (incidents) from "keep forever" to a tunable 90-day default retention (#897) [Enhancement]
- Shared `<og-tab-strip>` partial (#950) [Enhancement]
- Admin → Logs filter strip gained a third axis (pattern) alongside the existing source-tag + severity filters (#953) [Enhancement]
- Sortable tables fleet retrofit (#954) [Enhancement]
- Per-tab tunable search affordance (#958) [Enhancement]

### Schedules & automation

- Incidents (host_failure_events) is no longer pruned (#885) [Enhancement]
- Schedule "Last execution" column displayed the WRONG fire-time after a container restart (#937) [Bug]

### Notifications & toasts

- Notifications popup — cluster-pivot toggle (#858) [Enhancement]
- Notification template `{time}` placeholder now renders against the recipient's `ui_prefs.datetime_format` i... (#870) [Enhancement]

### Topbar, login & branding

- Profile → Topbar widgets weather section °C / °F unit preference (#799) [Enhancement]

### Internationalisation & accessibility

- Missing i18n key `admin_hosts.ssh_password_override_placeholder` added to `static/i18n/en.json` (#920) [Enhancement]

### API endpoints & backend helpers

- EnvKey enum + env_get helper (#921) [Enhancement]

### Documentation

- Document GHCR pre-built images (#801) [Enhancement]

### Internal cleanup, refactor & bug sweeps

- Phantom stale-banner cleanup (#800) [Bug]
- batch — three visual-consistency fixes from the an earlier pass (#850) [Bug]
- Fix against the full bug list (#864) [Bug]
- Fixed HTML lint warnings on `static/index.html`: 6× "Namespace 'x-transition' is not bound" + "Element main... (#881) [Bug]
- Fix lint issues in main.py (#894) [Bug]
- IDE-inspector cleanup sweep across `logic/` (#911) [Bug]
- Alpine ReferenceError: rpLeadingLabel is not defined (#918) [Bug]
- main.py lint sweep — real-bug subset (#919) [Enhancement]
- Cleanup (N) button — multi-click bug + stale count (#923) [Bug]
- Code-review batch — bugs + enhancements landed together (#930) [Bug]
- Split `static/js/app.js` (~40k lines) into native ES modules (#931) [Enhancement]
- text-[9.5px] arbitrary Tailwind value re-introduced across 10 sites (#935) [Enhancement]
- Three operator-flagged CSS / loading-state fixes batched together (#938) [Enhancement]
- Stale-terminology lint — CI script greps user-visible surfaces for renamed-section leaks (#955) [Enhancement]

### Other improvements & fixes

- Port-scan known-ports + detected-ports UX polish (#802) [Enhancement]
- Standalone Public-IP / ISP / ASN lookup subsystem (#887) [Enhancement]
- UI busy-state never extended past 3 s (#912) [Enhancement]
