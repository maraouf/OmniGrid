## What changed in 1.4.0

Fourth MINOR cut on top of `1.3.0` — rolls up **264 closed issues** under the 1.4.0 milestone (196 enhancements, 68 bug fixes). Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Highlights

- AI Assistant — full conversational sidebar replacing the modal Cmd-K target, multi-provider (OpenAI / Anthropic / Gemini) with generic `ai_max_tokens` knob, log-context window with secret-pattern redaction, MEMORY directives, host-identity enrichment, conversation export (TXT + JSON), Approval / Autonomous mode toggle, jump-to-latest pill, fenced-code-block code panels with copy buttons.
- Unified Cmd-K / Ctrl-K command palette — multi-word query support, action commands + AI assistant + Phase 1 bulk operations, multi-action queries, chart-kind heuristics, long-press shortcut overlay.
- Curated `address` field — single dedicated probe target across port-scan / ping / SNMP / SSH; resolution chain unified end-to-end (`address → ping.host → ssh.fqdn → host_id`), drawer port-scan gate with address-required banner.
- On-demand port-scan provider end-to-end — TCP + UDP, scheduled refresh, default-ports fleet expansion (50+ new ports), section-owned tunables, standalone admin sub-tab placed after SSH.
- SNMP infrastructure — vendor-aware walk pruning + signature narrowing, per-host walk serialisation for slow BMC-class agents, per-host `walk_concurrency` override, storage extractor pseudo-FS filter + per-host exclusion list, SNMP-only host main-row visualisation, Test-connection cool-down bypass.
- Beszel local sample table + Pulse / Webmin time-series storage end-to-end (sister-sampler pair so Pulse-only hosts get history), node_exporter ZFS multi-dataset pool dedup + camelCase MemTotal fix, TrueNAS disk-aggregate fix, Pulse v4 `hosts`-array extractor.
- Drawer chart polish — per-host Health Score (0-100) chip + breakdown popover, "What changed" Timeline tab, inline 1h-trend sparklines OVERLAID on Hosts-row CPU / Memory / Disk stat-bars, gap-detection no-bridge across multi-hour samples, disk-projection chart with confidence-band fork, AI-Assisted Incident Triage Drawer.
- Hosts-view bulk actions — multi-host sticky bottom bar, per-host audit rows on bulk pause/resume, partial-failure breakdown toast, `host:bulk_action_applied` SSE event for cross-tab fan-out, step-up reauth gate on destructive admin actions, idle-time progressive fill complementing scroll-driven lazy load.
- Notifications + Apprise — in-app notifications feature with cross-tab refresh, per-medium preferences in Profile → Notifications, page UX overhaul, bulk-pattern picker, swarm-agent unhealthy banner + one-click restart, scheduled autoheal with cooldown anchors that persist across container restarts, toast pause-on-hover/focus + explicit copy button.
- CodeQL security sweep — `py/full-ssrf` (defence-in-depth shared helper), `js/insecure-randomness`, `py/url-redirection`, `py/clear-text-logging-sensitive-data`, `py/path-injection` (`_safe_avatar_path` + persistent-log endpoints + node_modules path-traversal hardening).
- i18n + A11Y — `NOTIFY_TEMPLATE_DEFAULTS` migrated to i18n via new backend `logic/i18n.py` loader, WAI-ARIA radiogroup keyboard nav across the three on-page radiogroups, chart `?` info-bubble fleet-wide a11y refactor, A11Y batches.
- Admin tab refactor — `static/index.html` admin sub-tabs extracted to per-tab partials, Settings sub-sections to per-section partials, Test-before-Save gating across Portainer / OIDC / Asset Inventory tabs, Settings → Profile → Formats user-configurable datetime format that propagates across every date / datetime render.
- User UI preferences migrated from localStorage to DB so theme / sidebar width / drawer pinning travel cross-browser + cross-machine. Hardware card section gate now accepts snapshot-fallback hits so cached fields stay rendered when every live provider is offline.

### AI Assistant, Cmd-K & Conversations

- Unified Cmd-K / Ctrl-K command palette (#605) [Bug]
- Command palette multi-word query support (#614) [Enhancement]
- Bulk-host action correctness pass (#624) [Enhancement]
- Cmd-K palette cleanup pass (#678) [Enhancement]
- `static/index.html` admin sub-tabs extracted to per-tab partials (#686) [Enhancement]
- Settings sub-sections extracted to per-section partials (#687) [Enhancement]
- Ten UX review findings. HIGH: Cmd-K palette footer "navigate / select / close" hints i18n'd; Cmd-K result row... (#689) [Enhancement]
- AI integration foundation (#691) [Enhancement]
- AI tab follow-ups: (a) Header icons fixed (#692) [Enhancement]
- AI tab title casing + provider mono brand icons. (a) Capitalised "Integration" across every i18n key referrin... (#698) [Enhancement]
- Cmd-K palette extended with action commands + AI assistant + Phase 1 bulk operations (#704) [Enhancement]
- AI palette + dashboard — consolidated bundle (covers original ship #1111 + resilience/observability follow-up... (#706) [Enhancement]
- AI provider-list canonicalisation + Alpine `t`-shadow audit (#707) [Enhancement]
- CSS spacing-token migration in the AI tab + missing cleanup-action i18n keys (#708) [Enhancement]
- AI integration: generic `ai_max_tokens` setting in Admin → AI Integration, applied across every provider (#710) [Enhancement]
- Admin save-button rows separated by an HR top-border + form-field hints repositioned to sit right after their... (#711) [Enhancement]
- AI: Gemini 2.5 Pro thinking-mode compatibility. Pre-fix the palette's Gemini call set `thinkingConfig: {think... (#712) [Enhancement]
- AI palette: multi-action query support (#717) [Enhancement]
- AI Assistant sidebar — full conversational drawer replacing the modal palette as Cmd-K target (#719) [Enhancement]
- AI sidebar polish bundle — slash actions visible on bare `/`, true zero-popup destructive flow, screen-clear... (#721) [Bug]
- AI weather context + 1-decimal percent label uniformity. Two coordinated fixes. (a) **Weather questions no lo... (#722) [Bug]
- AI sidebar — inline disk-projection charts when the AI returns a HOSTS list, plus inline-confirm chip renderi... (#724) [Bug]
- 34 a11y / UX findings closed across the AI sidebar / dashboard / palette surfaces from the 2026-05-07 audit p... (#728) [Enhancement]
- AI sidebar conversation export (TXT + JSON) (#729) [Enhancement]
- AI palette + sidebar wave: 7 coordinated improvements landed in one batch. (a) **Asset-alias grounding** (#730) [Enhancement]
- AI sidebar Approval / Autonomous mode toggle. New compact `<select>` in the AI sidebar header (between the ti... (#732) [Enhancement]
- AI sidebar log-context. User-flagged: "check logs and tell me if any errors needs to be fixed" (#735) [Enhancement]
- AI sidebar typing lag fix. User-flagged: typing in the AI sidebar input was visibly laggy. **Iteration 4 (#737) [Bug]
- AI sidebar — render fenced code blocks as proper code panels with copy buttons (#740) [Bug]
- AI host-identity enrichment (#741) [Bug]
- AI request-timeout retry / fallback gap. User-flagged: "Error: Request timed out after 30s (#742) [Bug]
- AI palette test_* family — wire the four new test actions (`test_snmp` / `test_ping` / `test_asset_inventory`... (#745) [Enhancement]
- AI palette markdown formatting wave (#747) [Bug]
- AI memory feature — durable lessons surfaced via MEMORY: directives (#748) [Enhancement]
- AI Integration — fold `tuning_ai_sidebar_width_px` + `tuning_ai_conversation_export_enabled` into the section... (#752) [Enhancement]
- AI palette log-context — extend window to past 7 days AND redact secret patterns before shipping to LLM (#759) [Enhancement]
- AI provider base_url — cleartext-credentials warning when operator pastes http:// (#761) [Enhancement]
- Long-Cmd-K-press shortcut overlay (#767) [Enhancement]
- AI sidebar launcher hide — dirty/save flow on the Settings → Profile checkbox (#770) [Bug]
- AI sidebar — jump-to-latest pill for new messages while scrolled up (#771) [Enhancement]
- AI palette context — surface absolute memory in GB (#772) [Enhancement]
- AI palette chart-kind dispatch (#774) [Bug]
- AI palette context + chart-kind heuristic (#776) [Enhancement]
- AI memory / CPU history chart (#779) [Bug]

### Curated address field & port-scan provider

- On-demand port-scan provider (#535) [Enhancement]
- Port Scan moved out of Admin → Providers into its own standalone admin sub-tab placed AFTER SSH (#731) [Enhancement]
- Port-scan provider — Stage 1 polish wave: 12 coordinated patches to close gaps surfaced after the user starte... (#733) [Enhancement]
- Port-scan Stage 2 — UDP support (#734) [Enhancement]
- Port-scan "Scan ports" button now stays disabled + spinning until the actual scan COMPLETES on the backend (n... (#738) [Bug]
- Five coordinated remediations covering audit categories 1, 2, 3, 5 + an unrelated port-scan chip-strip regres... (#743) [Enhancement]
- Op_type canonical-name registry (#744) [Enhancement]
- Port Scan admin tab — section-owned tunables (#753) [Enhancement]
- Curated `address` field — single dedicated probe target across port-scan / ping / SNMP / SSH (#773) [Enhancement]
- Port-scan default ports — fleet expansion (50+ new ports across 5 user batches) (#775) [Enhancement]
- Scheduled port-scan kind — periodically refresh open ports across hosts (#780) [Enhancement]
- Admin → Port Scan — section headings for TCP / UDP / Scheduled refresh blocks (#781) [Enhancement]
- Port-scan previous-cycle results were hidden when port-scan was disabled (#794) [Bug]
- Quick batch — three corrections from user feedback (#795) [Bug]

### Authentication, passkeys, OIDC & 2FA

- CodeQL `py/url-redirection` at `logic/oidc.py:587` (#562) [Enhancement]
- Test-before-Save gating pattern across Portainer / OIDC / Asset Inventory admin tabs (#620) [Enhancement]
- Unified secret-input UX across every admin-tab secret input (#663) [Enhancement]
- Step-up reauth gate on bulk-destructive admin actions (#668) [Enhancement]
- Mono brand icons added to the sprite (Portainer + Authentik OIDC) for admin headers. New `<symbol id="icon-po... (#693) [Enhancement]
- Fleet of small UI polish landed this turn. (a) Authentik chip 1-line (#696) [Enhancement]
- Last-Test-success indicator next to every admin Test-connection button (#699) [Enhancement]
- `/api/auth/providers` hides Authentik SSO when current request hostname doesn't match the configured OIDC red... (#701) [Bug]
- Secret-input row width parity across every admin Test-connection / credential surface (#709) [Bug]
- TOTP audit-row INSERT failure (#762) [Enhancement]

### Security (CodeQL & token discipline)

- CodeQL py/full-ssrf flag on `logic/webmin.py:143` (and the matching POST below it) triaged + defence-in-depth... (#544) [Enhancement]
- CodeQL py/full-ssrf flags across the probe modules triaged + defence-in-depth refactored to a shared helper... (#546) [Enhancement]
- CodeQL security triage — three findings cleaned up (#553) [Bug]
- CodeQL `js/insecure-randomness` final cleanup at `static/js/app.js:10271`. Replaced the `Math.random()` fallb... (#555) [Enhancement]
- CodeQL Python security triage (#558) [Enhancement]
- CodeQL `py/clear-text-logging-sensitive-data` at `logic/events.py:178` (#565) [Enhancement]
- Server-health card font-sizes tokenised (#594) [Enhancement]
- Token discipline finishings (#596) [Enhancement]
- Six small correctness / hygiene fixes from the latest review pass (#636) [Bug]
- CodeQL `py/path-injection` finding on `_safe_avatar_path` (#726) [Bug]

### Real-time / SSE event stream

- In-app notifications feature (#541) [Enhancement]
- Notification-medium dict-shape recognition + cross-tab refresh (#578) [Enhancement]
- Cold-load + progressive paint comprehensive pass (#622) [Enhancement]
- `host:bulk_action_applied` SSE event for cross-tab fan-out. Pre-fix bulk-pause / bulk-resume published ONE `h... (#632) [Enhancement]
- Live-mode SSE stats / refresh handlers now trigger loadStats (#662) [Bug]
- Filter per-provider probe events from log trace (#667) [Enhancement]
- `host:provider_*` SSE events thread `client_id` for self-filter (#673) [Bug]
- `host:provider_done` SSE event carries an `ok: bool` outcome hint (#684) [Enhancement]
- `.scrollbar` utility class shows a clearly visible thumb at rest with high-contrast hover / drag state (#703) [Bug]

### SNMP

- Cool-down log lines no longer flag as ERROR + now include the resolved probe target (#534) [Bug]
- Drawer charts no longer bridge across multi-hour sampling gaps with one fake-smooth line (#538) [Enhancement]
- Printer-card freshness banner and the SNMP chart-section banner now report the SAME value across the host dra... (#542) [Enhancement]
- SNMP wall-clock probe budget against slow embedded devices. Pre-fix the budget was hardcoded `max(5.0, (timeo... (#561) [Enhancement]
- SNMP Test connection + host-drawer debug panel both bypass the unreachable cool-down (#564) [Enhancement]
- SNMP per-host walk serialisation. Slow BMC-class agents (iDRAC9, IPMI, low-power embedded snmpd) lose packets... (#571) [Enhancement]
- Per-host SNMP walk_concurrency override (#574) [Enhancement]
- Vendor-aware SNMP walk pruning. Pre-fix `probe_snmp` ran all ~67 OID branches against every SNMP-enabled host... (#575) [Enhancement]
- SNMP probe internals + per-host probe contract. (a) `_merge_one_host` SNMP block now calls `record_provider_o... (#576) [Enhancement]
- SNMP vendor key set drift fix end-to-end. Single source of truth at `logic/snmp.py:_VALID_VENDOR_KEYS`. `_cle... (#577) [Enhancement]
- SNMP vendor signature narrowing (#579) [Enhancement]
- Admin → Hosts SNMP row — per-host override flow + UI polish end-to-end (#580) [Enhancement]
- `probe_snmp` diagnostic surface uniform across success / timeout / test endpoint (#581) [Enhancement]
- Documentation + verified-correct items. (a) `logic/snmp.py` doc-comment near `_VENDOR_SIGNATURES` updated (#582) [Enhancement]
- `probe_snmp` internals — vendor-aware concurrency + signature weighting (#583) [Enhancement]
- Per-row Test connection (`/api/hosts/test`) now honours per-host SNMP overrides AND caps the wall_clock_budge... (#585) [Enhancement]
- Host-drawer SNMP chart `?` info bubbles + per-port utilization expectation gap (#588) [Enhancement]
- Disk + voltage state labels now title-cased in the SPA pills (#592) [Enhancement]
- Host-drawer chart grid opens for SNMP-only hosts immediately on first drawer-open (not after the first sample... (#603) [Enhancement]
- SNMP-only host main-row visualisation pass (#608) [Enhancement]
- Disk-percent label distinguishes "genuinely zero" from "small but non-zero". Reported on a dd-wrt SNMP host w... (#609) [Enhancement]
- SNMP storage extractor filters pseudo-filesystem mounts + accepts a per-host exclusion list (#611) [Enhancement]
- SNMP per-core CPU chart `<template x-for>` inside `<svg>` Alpine error (#616) [Enhancement]
- Bulk SNMP modals — added `this affects N hosts` reminder above the Apply button (#653) [Enhancement]
- Bulk SNMP-vendors mode dropdown labels rewritten for clarity (#682) [Enhancement]
- FreeBSD memory discrepancy (#720) [Bug]
- SNMP sub-section — section-owned tunables (#751) [Enhancement]
- Notifications headers + swarm_agent_health checkbox (#757) [Enhancement]
- SNMP sampler + Test Providers gate + Test endpoint resolver (#782) [Bug]
- `curated_snmp_hosts()` doesn't pass `address` field (#784) [Bug]
- SNMP chart "Collecting data" placeholder despite fresh `host_snmp_iface_samples` rows (#789) [Bug]
- Ping chart "Collecting first samples" SNMP banner spuriously rendered on ping-only hosts (e.g (#792) [Bug]

### Ping

- Multi-host bulk-action sticky bottom bar (Hosts main view) (#617) [Enhancement]
- host_failure_events append-only transition log (#635) [Enhancement]
- Ping sub-section — section-owned tunables (#750) [Enhancement]

### UPS / battery

- Per-host Health Score (0-100) chip + breakdown popover (#607) [Enhancement]

### Beszel, Pulse, Webmin, Node Exporter & Portainer

- Worker-node service stats regression (#554) [Enhancement]
- Asset Inventory master toggle (`asset_inventory_enabled`) following the per-service master-switch pattern use... (#560) [Enhancement]
- Swarm agent unhealthy banner + one-click restart (#567) [Enhancement]
- Pulse + Webmin time-series storage end-to-end. Sister-sampler pair so Pulse-only hosts (Proxmox VMs without a... (#604) [Enhancement]
- ZFS multi-dataset pool dedup in `logic/node_exporter.py:parse_exporter_text` (#610) [Enhancement]
- node-exporter parser missed `node_memory_MemTotal_bytes` (camelCase) (#613) [Bug]
- `host_pulse_sampler` tick error (#621) [Enhancement]
- Swarm-agent autoheal correctness pass (#625) [Enhancement]
- Notifications + Portainer admin polish pass + Hosts row-flash unification (#628) [Enhancement]
- Stack-row `_stale` indicator + `cache_refreshing` / `hub_probing` SPA hint (#642) [Bug]
- i18n keys for `swarm_autoheal_*` moved from `settings.notifications.*` to `settings.portainer.*` namespace (#646) [Enhancement]
- `host_webmin_sampler` no longer raises `unknown tunable` on every tick. Same drift class as the earlier Pulse... (#659) [Enhancement]
- `audit_template_data` placeholder validation (#672) [Enhancement]
- Stale-banner provider enumeration (#683) [Bug]
- Defensive guard against false-positive Beszel / Pulse pause notifications for hosts the user says aren't conf... (#688) [Bug]
- Switch-to-:latest for stack-managed AND non-Portainer-managed standalone containers (#695) [Enhancement]
- Stack-header `upd` count no longer inflated by offline / orphan containers with stale image digests (#714) [Bug]
- Webmin sub-section — section-owned tunables (#746) [Enhancement]
- node-exporter sub-section (#749) [Enhancement]
- TrueNAS disk-aggregate fix (#754) [Bug]
- Webmin sub-section — always-dirty when disabled (#756) [Bug]
- Drawer auto-fix — Portainer-API path for VXLAN stale-overlay cleanup (#758) [Enhancement]
- Beszel local sample table (#785) [Enhancement]
- Pulse v4 `hosts`-array extractor (#788) [Enhancement]
- Beszel local-store `history_series` (#791) [Bug]
- Beszel services x-for undefined-access (#793) [Bug]

### Provider chips, icons & status pills

- Debug panel's "Active providers:" row now uses the same per-provider chip styling (`pill-custom` + `providerC... (#559) [Enhancement]
- Drawer "ENABLED AGENTS" chip strip (#569) [Enhancement]
- Per-provider chip-flash minimum visible duration (#680) [Enhancement]

### Drawer, charts & sparklines

- Per-medium notification preferences in Profile → Notifications (#572) [Enhancement]
- Ping `?` info-bubble tooltip end-to-end (#600) [Enhancement]
- Inline 1h-trend sparklines OVERLAID on each Hosts-row CPU / Memory / Disk stat-bar (#602) [Enhancement]
- Sparkline post-probe history backfill + flat-zero hide. User reported on the Hosts list view: (a) all sparkli... (#612) [Bug]
- Post-probe `loadHostSnmpHistory` backfill. Reported on a Cisco SG300-28P switch where the row's CPU bar + spa... (#615) [Bug]
- Host drawer "What changed" Timeline tab (#618) [Enhancement]
- Cold-load and background-refresh visual treatment (#627) [Bug]
- Hosts main-view sparkline observer gate accepted `beszel_name`. Pre-fix the IntersectionObserver-driven `load... (#630) [Bug]
- Timeline event icons distinct per `kind` (#654) [Enhancement]
- User UI preferences migrated from localStorage to DB (cross-browser / cross-machine). Pre-fix several user-ch... (#700) [Enhancement]
- AI-Assisted Incident Triage Drawer (#705) [Enhancement]
- Disk-projection chart fix bundle (#715) [Enhancement]
- Paused provider chips render grey (muted), not orange (warning). User-flagged: when sampling is paused on a h... (#723) [Bug]
- Disk-projection chart confidence-band fork (#725) [Bug]
- AI chats cleared on deploy / page reload + irrelevant disk-projection charts on non-disk queries (#736) [Bug]
- Drawer keyboard navigation (#765) [Bug]
- Server-health card — thousand-separator formatting on fan RPM + PSU watts (#777) [Bug]
- Host drawer Debug panel — "Samples in window" table + expanded Tunables coverage + AI diagnostic visibility (#783) [Enhancement]
- Debug pane "Copy all" button (#790) [Enhancement]
- Host-drawer Timeline + main-page CPU/Mem bars (#796) [Enhancement]

### Hosts editor, Host groups & Hosts page

- Display label no longer auto-populated in two paths in Admin → Hosts (#551) [Bug]
- Several UI quick wins. (a) Icon override now accepts a "no icon" sentinel (#552) [Bug]
- Probe-timeout badge surfaced on the Hosts row when the per-host /api/hosts/one/{id} probe returns HTTP 504 (p... (#606) [Bug]
- Per-host `_seq` ordering hint (#633) [Enhancement]
- Hosts bulk-bar polish pass (#655) [Enhancement]
- Hosts view — idle-time progressive fill complementing scroll-driven lazy load (#768) [Bug]

### Admin & Settings pages

- Notifications retention dial moved from Admin → Process Tunables to Admin → Notifications, where users editin... (#563) [Enhancement]
- Swarm agent scheduled autoheal (#568) [Enhancement]
- Log files viewer (Admin → Logs → Files) text filter input matching the Live tab. Same `logFilter` state (#570) [Enhancement]
- swarm_agent_unhealthy notifications now fire on TRANSITIONS only (#631) [Enhancement]
- Admin tab width unified via `scrollbar-gutter: stable` on `html, body` (#649) [Enhancement]
- Audit-template log de-duplication (#669) [Bug]
- Test-connection UX unification across every admin tab + drawer-button icon parity + scrollbar drag-hover foll... (#702) [Enhancement]
- Surface Swarm task errors in the service / item drawer (#739) [Enhancement]
- Settings → Profile → Formats (#797) [Enhancement]

### Logs view & retention

- Persistent-log sub-tag `[hosts:bulk]` for bulk Hosts-view operations (#641) [Enhancement]
- Defence-in-depth path-injection guard on persistent-log endpoints (#713) [Enhancement]

### Schedules & automation

- Schedule-kind audit gate at boot (#634) [Enhancement]
- Persist `_swarm_autoheal_last_restart_ts` + notify anchors across container restarts. Pre-fix the cooldown an... (#638) [Enhancement]
- First-boot auto-bootstrap of a default `swarm_agent_health` schedule. Pre-fix the schedule kind required an a... (#643) [Enhancement]
- Red "Latest task error" panel surfacing on healthy services (#766) [Bug]

### Notifications & toasts

- WAI-ARIA radiogroup keyboard nav for the three on-page radiogroups (range picker + Health filter chips + Stat... (#537) [Enhancement]
- Apprise "Provider paused" / "Host sampling paused" notification titles now use the colour emoji `⚠️` (U+26A0... (#543) [Enhancement]
- Notifications popup polish (#548) [Bug]
- Drawer toggle buttons — "Show debug data" + "SSH commands" (#586) [Enhancement]
- Notifications page UX overhaul (#619) [Enhancement]
- Bulk-action progressive UI feedback on the Hosts main view. Pre-fix every bulk action ended with `loadHosts(t... (#640) [Enhancement]
- In-app notification body no longer duplicates the title when the event's template body is intentionally empty (#645) [Bug]
- Hosts bars + sparklines paint from snapshot on cold-load / page refresh (#648) [Bug]
- Apprise brand icon refreshed to the homarr-labs `dashboard-icons/webp/apprise.webp` source. SVG-only contract... (#671) [Enhancement]
- Bulk-action toast surfaces partial-failure breakdown. Pre-fix the SPA toast after a bulk action showed only `... (#679) [Enhancement]
- UI sprite caching switched from `no-cache, must-revalidate` to `public, max-age=31536000, immutable` with a `... (#694) [Enhancement]
- Host groups editor — parent-self collapse with compact summary header + bulk toggles + unified bulk strip (#697) [Enhancement]
- Toast notifications — pause-on-hover/focus + explicit copy button (#769) [Bug]
- SweetAlert page-jump on confirm popups (#778) [Bug]

### Mobile / responsive UX

- Probe-timeout badge glyph now `icon-zap` (lightning bolt), distinct from the stale-snapshot badge's `icon-clo... (#652) [Bug]
- `.notify-cat-chip` click target now ≥ 24×24 (WCAG 2.5.5) (#656) [Bug]

### Topbar, login & branding

- In-flight button feedback consistency (#593) [Enhancement]
- Topbar refresh spinner no longer spins forever (#657) [Bug]
- Background-gather indicator pill in the topbar (#664) [Enhancement]
- Stacks + Services views — failure / updates signals split across rows AND topbar nav buttons; no collision (#755) [Enhancement]
- Logs filter textbox — dropped the icon-slot left-pad on every search input that doesn't have an absolute-posi... (#786) [Enhancement]
- Migrate every keyboard shortcut to Cmd/Ctrl modifiers + add Cmd/Ctrl+Shift+L for the topbar Cleanup button (#787) [Enhancement]

### Vendor icons, hardware & RAID

- iDRAC Server health card + follow-up patches. Originally tracked as the headline ship plus five distinct poli... (#540) [Enhancement]
- Linux Mint icon wired into the host + item/stack icon resolvers (#557) [Enhancement]
- Synology dark-theme icon updated to the simple-icons single-colour white-on-transparent variant (5256 bytes... (#566) [Enhancement]
- iDRAC debug-panel timeout improvements (#587) [Enhancement]
- Server health "Physical disks" + "Voltages" high-count panels (#590) [Enhancement]
- Dell PD/VD state pill colour split: `online` stays green (`pill-ok`) (#591) [Enhancement]
- UI icon sprite extracted from `static/index.html` to `static/img/ui-sprite.svg` (#597) [Enhancement]
- Distinct `seerr.svg` icon for the Fallenbagel/seerr brand (#690) [Enhancement]

### Filters, badges & pagination

- Notifications popup capped via server-side pagination (#549) [Enhancement]

### Internationalisation & accessibility

- 19 findings across Visual / CSS / UX / IA / A11Y (#536) [Enhancement]
- Bulk-pattern picker on Profile → Notifications (#584) [Enhancement]
- Debug-panel "Refresh" button now carries an inline `#icon-refresh-ccw` SVG sized at 14 px (matching the `host... (#589) [Enhancement]
- i18n quick-wins batch — four small label / button consolidations across raw-English literals to `t()`-bound k... (#595) [Enhancement]
- Chart `?` info-bubble fleet-wide a11y refactor (#598) [Enhancement]
- Server-health "Show all (N) / Show fewer" toggle (#599) [Enhancement]
- A11Y batch — 4 findings closed in one pass (#601) [Enhancement]
- Categorised Profile → Notifications panel correctness pass (#626) [Enhancement]
- `admin.notify_templates.placeholder_help.message` i18n key added (#647) [Enhancement]
- Bulk-pause SweetAlert confirm now shows the actual host list. Pre-fix the body shows `count` only (#650) [Enhancement]
- Notify-category chips show an explicit dirty marker for unsaved changes (#651) [Enhancement]
- Hardcoded "Host disk" / "Docker disk" body labels in the Nodes view (#677) [Enhancement]
- Eight UX review findings I18N (4): (1) Active Ops floating panel rendered raw English `op_type.replace('_','... (#685) [Enhancement]
- `NOTIFY_TEMPLATE_DEFAULTS` migrated to i18n. **Backend i18n loader (`logic/i18n.py`):** new module exposes `t... (#727) [Enhancement]
- UX review CSS + accessibility batch (#764) [Enhancement]

### Database / migrations / data

- SQLite LIKE-pattern wildcard leak in three host-id sites (timeline + bulk-resume) (#623) [Enhancement]
- Residuals fixed THIS TURN: (a) `main.py:1660` history search query (#658) [Enhancement]
- Coordinated DB schema work across two tables (#661) [Enhancement]
- Items snapshot schema-version stamp (#670) [Enhancement]

### API endpoints & backend helpers

- Profile → Notifications panel now greys out (and functionally disables) per-user toggles for events the admin... (#545) [Bug]
- /api/hosts/debug end-to-end fix bundle (#573) [Bug]
- /api/stats cold-load instant paint (#629) [Bug]
- Timeline `target_name` matching tightened to candidates this host has actually used (#644) [Bug]
- Cold-cache `_gather()` / `_gather_stats()` paths now route through their single-flight kicks (#660) [Bug]
- Per-host audit rows for bulk Hosts-view actions (pause/resume). Pre-fix `/api/hosts/bulk/pause` + `/api/hosts... (#665) [Enhancement]
- Deprecated-placeholder support in template preview endpoint. New `NOTIFY_DEPRECATED_PLACEHOLDERS` map in `log... (#675) [Enhancement]
- Path-traversal hardening for avatar + node_modules endpoints (#718) [Enhancement]

### Documentation

- Python base image bumped from `python:3.12-slim` to `python:3.14-slim` (#556) [Enhancement]

### Internal cleanup, refactor & bug sweeps

- `notifyMediumNames()` memoisation. Same drift class as the `notifyCategories()` memoisation (#637) [Enhancement]
- Fleet-wide cleanup of `<span @click>` / `<a href="#" @click>` / `<div @click>` non-modal-backdrop sites conve... (#639) [Enhancement]
- Single source of truth for provider-name set (#666) [Enhancement]
- Plain-settings drift class + op_type-registry coverage + log-classifier transient sync (#760) [Enhancement]
- PERF-LOW defensive caps on `_BACKGROUND_TASKS` set + history fetch request-version counter. Two forward-looki... (#763) [Bug]

### Other improvements & fixes

- Ping packet-loss badge now reflects window-aggregated loss instead of the latest single tick (#539) [Enhancement]
- Drawer-chart range picker (1h / 6h / 24h / 7d) now persists across refresh via `localStorage.hostHistoryRange` (#547) [Enhancement]
- CI workflow moved to Node.js 24 ahead of the September 2026 Node 20 removal (#550) [Enhancement]
- `_kick_background_gather` returns the in-flight task. Pre-fix the helper returned bool (#674) [Enhancement]
- swarm_agent_health history rows skip-if-no-change (#676) [Bug]
- Two small backend cleanups (#681) [Enhancement]
- Tunable save validator now accepts loose boolean strings ("true"/"false"/"yes"/"no"/"on"/"off") for tunables... (#716) [Enhancement]
