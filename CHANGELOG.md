# Changelog

All notable changes to OmniGrid land here. Format adheres to
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Cadence (see `docs/RELEASE_PROCESS.md` for the full operator runbook):

- **`PATCH`** — CI bumps automatically on every successful deploy (one per shipped TODO item). The accumulating count between releases is the operator's "is it time to cut a release" signal.
- **`MINOR`** — operator-controlled. When a batch of PATCH-shipped items feels release-worthy, the operator hand-edits `MINOR` on the server (which resets `PATCH` to `0`) and writes a new `[X.Y.0]` section here listing the items that landed since the last MINOR.
- **`MAJOR`** — breaking changes only (DB migrations that aren't forward-compatible, env-var renames, `/api` contract breakage). Migration notes ship alongside the release in `notes/MIGRATIONS.md`.

Categories per release follow Keep a Changelog:

- **Added** — new features.
- **Changed** — changes in existing functionality.
- **Deprecated** — features marked for removal in a future release.
- **Removed** — features that were dropped this release.
- **Fixed** — bug fixes.
- **Security** — fixes for vulnerabilities.
- **Internal** — refactors, doc work, build / CI changes that don't touch user-facing behaviour. (Non-standard but useful for a homelab tool where most work is internal.)

## [Unreleased]

Items that have shipped to the live deploy as a PATCH bump but haven't
yet been rolled into a numbered `MINOR` release. When the operator cuts
the next release, this whole block becomes the `[X.Y.0]` entry below.

### Added

- Hosts bulk-bar action grouping — visual separator between sampler-state actions (Resume / Pause) and SNMP-config actions (Vendors / Tunables). The two groups touch different state planes (failure state vs curated `hosts_config`); the divider signals the semantic boundary so the bar reads as two groups rather than a flat list. Self-hides on viewports < 600px so a wrap line doesn't carry an awkward visual stub.
- New `host:bulk_action_applied` SSE event for cross-tab fan-out. Bulk Hosts-view actions (pause / resume / SNMP vendors / SNMP tunables) used to publish one event per affected id (pause + resume) or zero events (the SNMP variants). Now each bulk endpoint publishes ONE event carrying every applied id in the payload — O(1) SSE frames per bulk action regardless of fleet size. SPA handler reconciles each id through the same per-row refresh path the per-host handler uses; SNMP variants also trigger a background `loadHosts(true)` so the curated `snmp_name` / `snmp.vendors` / `snmp.walk_*` overlay re-syncs across tabs.
- Bulk SNMP modals (vendors + tunables) now show a "this will apply to N selected host(s)" reminder above the Apply button. Operator focused on the form fields can no longer submit without re-checking the selection count.
- Bulk-pause SweetAlert confirm shows the actual host list — first 10 hostnames inline as monospace chips, with "… and N more" when count exceeds 10. Reduces the "I clicked Confirm but forgot which hosts I'd selected" mistake class.
- Probe-timeout badge glyph diverges from the stale-snapshot badge — now uses `icon-zap` (lightning) for probe-timeout and keeps `icon-clock` for stale. Pre-fix both states rendered the same amber clock, requiring operators to hover for the title attribute to disambiguate.
- Notify-category chips show an explicit unsaved-change marker — small amber dot in the chip's corner appears the moment the operator clicks until Save commits. Pre-fix the chip flipped state immediately on click but if Save failed (network blip) the UI was lying about the persisted state.
- Admin tab width unified via `scrollbar-gutter: stable` on `html, body`. Pre-fix the page width visibly jumped when switching tabs because the body scrollbar appeared / disappeared with content height — Notifications + Providers (host_stats) tabs are tall enough to need vertical scroll, narrowing the content by ~15px versus shorter tabs. The gutter is now reserved permanently so the page width is constant across every admin tab.
- Hosts bars + sparklines now paint from snapshot on cold-load / page refresh. Pre-fix the SPA's bar gates (`hostHasCpuMetric` / `hostHasMemMetric` / `hostHasDiskMetric`) require `h.status === 'up'` for any of the bars to render. The `/api/hosts/list` cold-load path returns rows with `providers_hit=[]` (the endpoint is the fast skeleton — per-host probes via `/api/hosts/one` come AFTER it returns) and the snapshot fallback only restores `host_*` prefixed fields, NOT `beszel_status` / `pulse_status`. The status precedence chain in `_shape_host_api_row` then fell through to `unconfigured` / `unknown`, hiding bars even though `host_cpu_percent` etc. were populated from the persisted snapshot. Fix: new precedence branch in `_shape_host_api_row` promotes status='up' provisionally when `_stale_fields` is non-empty AND at least one of `host_cpu_percent` / `host_mem_total` / `host_disk_total` / `host_uptime_s` is populated. The existing `_stale_fields` marker stays set so the SPA's stale-rendering pipeline (dimmed values + amber-clock badge + "X minutes ago" tooltip) kicks in automatically. Live status overwrites this on the next `/api/hosts/one` response.

### Fixed

- Topbar refresh spinner no longer spins forever after a forced refresh. Pre-fix `/api/items` / `/api/hosts/list` / `/api/stats` all set their `cache_refreshing` / `hub_probing` / `stats_refreshing` response flags to True whenever `force=true` was passed — and the SPA's auto-refresh path always sends `force=true` to bypass its own cadence cache. Result: every poll reset the flag back to True, the topbar spinner gate `(loading || cacheRefreshing || hubProbing || statsRefreshing)` stayed True forever, and the spinner never settled. Fix: the flags now only fire on genuinely-stale-cache paths. Force-bypass with a fresh cache still kicks a background refresh for next-time but doesn't flag the spinner — the response IS fresh, so spinning would lie.
- In-app notification body no longer duplicates the title when the event template's body is intentionally empty. Pre-fix the in-app medium had a `body = body or title` fallback line that overwrote empty bodies with the title — events like `user_login` (template body=`""`) rendered TWICE in the notifications panel: once as the title, once as a duplicate body line. The Apprise medium keeps its `body = body or title` fallback because Apprise's HTTP API rejects empty bodies; the in-app store has no such constraint. Empty bodies now stay empty for in-app rendering.
- i18n keys for `swarm_autoheal_*` (action selector + bootstrap toggle labels + dropdown options) moved from `settings.notifications.*` to `settings.portainer.*` namespace so the relocated panel renders translated labels. Pre-fix the panel rendered raw key strings (`settings.portainer.swarm_autoheal_label` etc.) because the keys were left under the old namespace when the selector moved to the Portainer admin tab. Six keys relocated; six `[i18n] Missing key` browser-console warnings silenced.
- Missing i18n key `admin.notify_templates.placeholder_help.message` added. The earlier `swarm_agent_unhealthy` correctness pass introduced a new `{message}` placeholder always populated regardless of severity (so warning / informational templates don't render empty bodies via the legacy `{error}`-only-on-failure binding) but didn't add the corresponding entry to the placeholder-help block in `static/i18n/en.json`. Browser console showed `[i18n] Missing key` on every Admin → Notifications template-editor open. New key documents the placeholder semantics for non-failure events.

### Added

- Three remaining non-button click handlers fleet-wide converted to `<button type="button">`. Topbar search clear (✕) was a `<span @click>`, the Nodes view per-node toggle area was a `<div @click>`, and the host-drawer no-source-hint admin link was an `<a href="#" @click.prevent>`. All three lacked native keyboard activation, `:focus-visible` ring, and (for the link) cleanly avoided the `href="#"` scroll-to-top side-effect. The Nodes toggle gains `:aria-expanded` so screen readers announce its state. Modal backdrops correctly stay as `<div>` because they're full-screen overlays, not button-shaped affordances. New i18n keys `actions.clear_search` / `actions.expand` / `actions.collapse`.
- First-boot auto-bootstrap of a default `swarm_agent_health` schedule. The autoheal feature used to require an admin-created schedule row to fire — operators who never set up the schedule got the underlying detection but no notification or restart action. New helper `bootstrap_swarm_agent_health_schedule` in `logic/schedules.py` runs at boot and creates a 5-minute default the first time Portainer is configured AND no equivalent row exists. Idempotent + latched via a `swarm_autoheal_bootstrap_done` flag so a deleted-on-purpose row stays deleted across restarts. New Admin → Portainer toggle "Auto-create default swarm_agent_health schedule" lets operators opt out before the first boot; flip to off, save, restart, and the helper skips the auto-create. The action defaults to `notify` (the existing default), so the only visible side-effect is one extra schedule row in Admin → Schedules.
- Schedule-kind audit gate at boot. New `logic/schedules.py:audit_schedule_kinds()` walks `SCHEDULE_KINDS` and verifies (a) every entry maps to a coroutine function so a non-async typo can't slip into the dispatch table, (b) every entry's name follows the `_run_<kind>` convention so a kind named `foo` mistakenly wired to `_run_bar` is caught at boot rather than at fire time, and (c) every runner has a docstring so the operator-facing scheduler runbook can stay synchronised with the in-code surface. Static-only — does NOT fire any runner with sample params (would spawn real ops). Boot site logs WARN lines for each drift category alongside the existing notify-template audit. Same shape as `audit_template_coverage` in `logic/ops.py`.
- Bulk-action progressive UI feedback on the Hosts main view. Pre-fix every bulk action (pause / resume / SNMP vendors / SNMP tunables) ended with `loadHosts(true)` and a single toast — operators got no per-host confirmation as each id settled. After this change, the bulk-action response now drives a small green "✓ N/M applied" status badge above the Hosts toolbar AND a per-row check glyph next to the hostname for every applied id; both clear together after 5 s via a shared timer. Drains naturally rather than disappearing instantly so the operator sees confirmation as the rows reconcile. The per-row markers survive the post-action `loadHosts(true)` reconcile because the existing in-place field-by-field assignment only touches keys present in the incoming API dict. New `icon-check` symbol added to the UI sprite. Reduced-motion variant disables the scale-in keyframe.
- Timeline `target_name` matching tightened to candidates this host has actually used. The host-drawer Timeline endpoint's `name_candidates` set used to unconditionally include the curated row's free-form label + every provider alias, so two hosts sharing a label (operator types "Web server" twice) leaked history rows across each other's timelines via the SQL `OR target_name IN (...)` clause. The candidate set is now pre-filtered against `SELECT DISTINCT target_name FROM history WHERE target_id=hid` — a name only counts as a fuzz-match candidate if THIS host has actually been associated with it via target_id at some point. Bare host_id is always preserved; on transient DB error the helper falls back to including every extra (legacy behaviour) so a blip doesn't lose timeline coverage. No schema change.
- Persistent-log sub-tag `[hosts:bulk]` for bulk Hosts-view operations. The four bulk endpoints (`bulk-pause` / `bulk-resume` / `bulk-snmp-vendors` / `bulk-snmp-tunables`) used to print under the generic `[hosts]` tag, indistinguishable from per-host probe diagnostics in Admin → Logs. They now print under the dedicated `[hosts:bulk]` sub-tag with its own indigo accent colour (`#6366f1` dark / `#4338ca` light) so operators can grep all bulk activity in one shot. The `colorizeLogText` regex now allows `:` in tag names; colons in tag names map to hyphens for the CSS class so selectors stay simple. Audit recipe for any future colon-bearing sub-tag: extend the regex character class only if needed; the CSS class transforms colons to hyphens automatically.
- Notifications panel page size is now operator-tunable. New `tuning_notification_page_size` knob (default 25, range 5..200, env `NOTIFICATION_PAGE_SIZE`) replaces the hardcoded 50. Reduced default from 50 → 25 because the popup felt overwhelming on busy fleets where every gather emits new entries. Operator overrides via Admin → Config; SPA reads the resolved value from `/api/me`'s `client_config.notifications_page_size` so a save takes effect on the next round-trip without a page reload. `notificationsLimit` initialises from the in-data() default (25) until /api/me lands.
- Per-provider polling indicators on Hosts row chips — SSE-driven per-provider granularity. Two new event types `host:provider_probing` and `host:provider_done` emitted around each in-flight per-host probe slice in `_merge_one_host` (SNMP / Webmin / node-exporter). Cache hits skip the events; Beszel / Pulse / Ping also skip (dict-lookup or sampler-driven). SPA tracks `_polling[provider]` per row; the matching chip pulses subtly while ITS probe is in flight even after the row's overall `_loading` flag has flipped false. Lets operators eyeball which specific provider is the bottleneck on a slow host without opening the drawer or debug panel. Failed responses (502 / 503 / 504) and successful refreshes both clear `row._polling = {}` so a lost `provider_done` event can't keep a chip pulsing forever. New event types documented in `docs/guidelines/api.md`. Builds on the row-level `chip-loading` pulse from the previous Loading-state visual feedback entry below.
- Loading-state visual feedback across Stacks / Services / Hosts views. Three coordinated changes so the SPA never misrepresents an in-flight probe as a hard failure during initial paint. Stacks rows gain a small leading indicator inside the name cell: a 10×10 spinner while `/api/stats` hasn't returned for the first time, swapping to a coloured roll-up dot (green / amber / red / grey via the new `stackStatusDotClass` helper) once stats land. Services rows get the same pattern in their leading column; the previous dot — which mirrored the update-status pill colour and conflated "needs update" with "loading" — is replaced by the new spinner→dot. Host provider chips no longer render as red `pill-error` while the row's per-host probe is in flight: a new `'loading'` state on `providerStates(h)` drives a `pill-custom chip-loading` class that keeps the provider's brand colour but pulses opacity subtly via `@keyframes provider-loading-pulse`, then settles into the real colour once the probe lands. Row-level host spinner (the small `host-loading-spinner` icon on the left of each host row) is unchanged. New `statsLoaded` flag flips true on the first successful `/api/stats` response and stays true; new i18n keys under `chips.loading_stats` / `chips.stack_status_*` / `chips.item_status_*` / `hosts_extra.provider_loading`. Reduced-motion variant disables every animation.
- Cross-restart items snapshot persistence. Closes the last blocking path left after the cold-load instant-paint work below: a fresh container restart had no in-memory `_cache` to fall back to, so the FIRST `/api/items` request still blocked on the full Portainer fan-out + image-digest probe (10-30s) until the in-memory cache filled. New `items_snapshot` SQLite table (single-row, `id=1`, JSON blob with items + stacks + nodes + nodes_info + ts) populated at the end of every successful `_gather` and seeded back into `_cache` at lifespan startup. With it, the first `/api/items` after restart finds populated cache, serves it instantly + kicks the live gather in background. Boot seed runs AFTER `seed_nodes_info_from_snapshots` and merges into `_cache["nodes_info"]` rather than clobbering, preserving the per-field `_stale_fields` markers that the host_snapshots pipeline stamps. Items / stacks / nodes_info entries seeded from the snapshot get `_stale: true` markers and `_cache["_stale"]` is set at the cache level until the next live gather replaces it. Single-row design — every gather replaces the snapshot wholesale, so removed / ignored items auto-clean on the next successful gather. New helpers in `logic/gather.py`: `save_items_snapshot()` (called at end of `_gather_impl`) and `seed_items_cache_from_snapshot()` (called from the boot-time `_seed_caches_bg`).
- Cold-load instant paint across Hosts + Items / Stacks / Services / Nodes pages. Pre-fix `/api/hosts/list` synchronously awaited the Beszel + Pulse hub probe (15-30s blocking) before returning snapshot rows, and `/api/items` awaited the full Portainer fan-out + image-digest probe whenever the in-memory cache was stale or `force=true` was set (which the SPA's auto-refresh path always sends). Both endpoints now serve cached / snapshot data IMMEDIATELY and kick the refresh into a background `asyncio.create_task` so subsequent calls hit a warming cache via the existing single-flight locks. New `_kick_background_gather()` single-flight helper guards `_gather` from a thundering-herd of parallel kicks; new `_peek_cached_host_provider_state()` returns the host-provider cache iff warm or `None` if cold/stale (extracted cache-key computation to module-level `_compute_host_provider_cache_key()` so both helpers share one definition — drift would mean the peek says "warm" while the get re-fires the probe). Response shape adds `hub_probing: true` (Hosts) and `cache_refreshing: true` (Items) when a background refresh is in flight. Cold-empty cache (first `/api/items` after a fresh container restart with no in-memory state and no items_snapshot table yet — the cross-restart persistence layer is tracked separately) is the only path that still blocks. SPA stays as-is functionally — the per-host fan-out via `/api/hosts/one/{id}` already shares the in-flight hub probe through the single-flight lock, so each row's eventual upgrade gets the fresh data without re-paying the probe cost. Net: pages paint in milliseconds with the prior snapshot; refresh runs invisibly.
- Admin-editable notification templates with placeholders. Each event in `NOTIFY_EVENT_NAMES` now resolves its title and body through DB-backed `notify_template_<event>_title` / `_body` settings; defaults baked into `NOTIFY_TEMPLATE_DEFAULTS` mirror the legacy hard-coded literals so a deploy with no template overrides behaves identically to before. Curated placeholder whitelist — `{name}` / `{type}` / `{actor}` / `{host}` / `{time}` / `{error}` / `{status}` — rendered via `str.format_map` + a `SafeDict` subclass that returns `{token}` verbatim for unknown keys so a typo never crashes the dispatch. Three new admin-only routes power the editor UI: `GET /api/admin/notify-templates` (list every event with current + default state and an `available_placeholders` roster), `POST /api/admin/notify-templates/{event}` (save title and/or body — empty string resets to default), `POST /api/admin/notify-templates/{event}/preview` (live render with sample placeholder values + flags unknown placeholders for the preview pane), `POST /api/admin/notify-templates/{event}/test` (fire one real notification through the live dispatcher so the admin can see the rendered output land in their inbox). Admin → Notifications event rows are now clickable — clicking the label opens a modal editor with two textareas, an inline placeholder chip strip (click a chip to insert at the caret), a debounced live-preview pane, and a Send-test button. Profile → Notifications gains a small info-icon next to each event that opens the same modal in read-only mode so users can see what fires without admin access. Boot-time audit gate (`audit_template_coverage`) logs a WARN line whenever an event ships without a default template; the same data surfaces on `/api/admin/notify-templates`'s `missing_defaults` / `unknown_defaults` arrays so the editor can render a warning chip if the codebase drifts. Templates are emoji-safe (UTF-8 round-trip through SQLite) and i18n strings live under `admin.notify_templates.*` and `profile.notify_templates.*`.
- Test-before-Save gating extended to Asset Inventory. Mirrors the same pattern already in place for Portainer and Authentik OIDC. When `asset_inventory_enabled` is ON, the Save button locks until a successful Test connection has been run against the CURRENT form values; any edit after a passing Test re-locks Save. When the master toggle is OFF the gate releases (no upstream probe will fire either way). New `_assetSnapshot()` helper covers BOTH auth modes (oauth2 + lifetime_token) with secrets following the write-only `<set>` marker contract; `canSaveAsset()` compares the stamped snapshot from the last successful Test against the live snapshot. New i18n key `admin_assets.save_requires_test`. Reminder span shows next to the Save button when enabled-and-not-yet-tested. **Follow-up patch:** dirty cues stay PURELY tied to `xDirty()` for all three pages (Asset / OIDC / Portainer). An earlier iteration tried to gate the amber ring + Unsaved pulse on `&& canSaveX()`, but that suppressed the cues whenever a post-test edit (e.g. toggling verify_tls) re-locked the gate, leaving the operator with no signal that their edit took effect. The dirty cue now ALWAYS reflects "you changed something"; the disabled Save button + dimmed reminder span carry the gate state separately. Three honest signals beat one mixed signal.
- Test-before-Save gating extended to Authentik OIDC. Same shape as the Portainer entry: `_oidcLastPassedTest` state + `_oidcSnapshot()` + `canSaveOidc()` gate. When the OIDC master toggle is ON, the operator must run a successful Test against the CURRENT form values before Save unlocks; any edit after a passing Test re-locks Save by mutating the snapshot. When OFF the gate releases. New i18n key `settings.oidc.save_requires_test`.
- Test-before-Save gating on Admin → Portainer. When `portainer_enabled` is ON, Save is locked until a successful Test connection has proven the URL / API key / endpoint round-trip works against the CURRENT form values. Any edit after a passing Test re-locks Save until the operator re-tests. When the master toggle is OFF the gate releases (the backend won't probe either way). New `_portainerLastPassedTest` state + `canSavePortainer()` helper. New i18n key `settings.portainer.save_requires_test`.
- Notifications page UX overhaul — Profile and Admin merged into one categorised, grouped, page-Save flow. Combines several rounds of polish that share the same surfaces and helpers. (1) Profile → Notifications redesigned around three collapsible category cards (Operations / Health / Security) with a search input + Expand all / Collapse all controls, replacing the flat per-event grid that was getting unwieldy as new events ship (~20+ rows). Each category header shows label + description + active-count badge + one chip per medium showing column state (all / partial / none) + chevron. Clicking a medium chip bulk-toggles every event in the category for that medium. New helpers in `static/js/app.js`: `notifyCategories()` (canonical category structure with both flat `rows` and paired `groups`), `notifyCategoryFilteredRows`, `notifyCategoryFilteredGroups`, `notifyCategoryStateForMedium`, `notifyCategoryEnabledCount`, `toggleNotifyCategoryMedium`, `toggleNotifyCategoryAll`, `isNotifyCategoryExpanded`, `toggleNotifyCategoryExpanded`, `expandAllNotifyCategories`. Data model unchanged — `profileForm.notify_events` still keys events by bare name with per-medium dict, so existing helpers + dirty tracking work. New CSS for `.notify-category` / `.notify-category-header` / `.notify-cat-chip`; new i18n family under `profile.notifications.cat_{operations,health,security}` + `cat_*_desc` + `expand_all` / `collapse_all` / `search_placeholder`. (2) Admin → Notifications events section moved to the same `notifyCategories()` helper, rendering the four event arrays (`notifyEventGroups` / `notifySecurityEvents` / `notifySamplerEvents` / `notifyHealthEvents`) as collapsible category cards — same source of truth as Profile, so a new event added in any of those four arrays appears on BOTH pages. Admin keeps single-toggle-per-event semantics (one Enabled checkbox per event); per-medium routing stays exclusive to Profile. Categorised expand state namespaced as `admin:<cat.id>` so Admin and Profile track expand/collapse independently. Profile's search-bar input padding bumped (`ps-7` → `ps-9`, plus `pe-8` for the trailing clear button) and the search icon repositioned (`start-2` → `start-3`) so the icon no longer overlaps placeholder / typed text. Category headers no longer render a leading icon — inconsistent with the SPA's icon-light style. (3) Operations category renders as PAIRED GROUPS — one operation header (Stack updates / Container updates / Container restarts / etc.) + indented success / failure sub-rows, instead of a flat 14-row list of "Stack updates · Success" / "Stack updates · Failure". Health + Security stay flat (single toggle per event). Same shape on BOTH Admin and Profile. New `notifyCategoryFilteredGroups(cat)` filters operation groups by search against operation label OR translated success/failure kind labels OR underlying event keys. Group header rows span every grid column on Profile (CSS `grid-column: 1 / -1`); sub-rows render with a small ↳ glyph + green / red kind label. (4) The standalone "Save" button under the notification-retention card has been retired; the page-level Save now flushes BOTH the apprise / events form AND the retention tunable in one click. Page-Save's amber dirty-ring fires when EITHER `appriseDirty()` OR `tuningDirty()` is true; the Saving label flips when EITHER `settingsSaving` OR `tuningSaving` is in flight.
- Scheduled Swarm-agent autoheal — new `swarm_agent_health` schedule kind detects unhealthy Portainer agent nodes (any node whose consecutive bad-gather count meets `tuning_swarm_agent_unhealthy_threshold`) and either fires a notification or auto-restarts the agent service. Action chosen via the new Admin → Notifications dropdown `swarm_autoheal_action` (default `notify`). Restart path consumes a cooldown anchor keyed against the new `tuning_swarm_autoheal_cooldown_minutes` knob (default 30, range 1..1440 minutes) so a thrashing agent service can't pin the manager in a restart loop. New Apprise event `swarm_agent_unhealthy` joins the existing `swarm_agent_restart_success/failure` pair; wired through the four-place audit (SettingsIn / api_get_settings / SPA notifyEventKeys / dirty-tracker snapshot). Admin → Notifications grid gains a Health events section + Swarm agent restarts pair. Skip-if-no-unhealthy keeps the schedule's run history honest about how often detection fires vs how often it actually acts.
- Multi-host bulk-action sticky bottom bar on the Hosts main view. Selection checkbox on every row (desktop + mobile, admin-only) toggles each host into a reactive `selectedHosts` Set. The bottom-fixed action bar appears as soon as one host is selected and offers four actions: Pause sampling (inserts a `paused=1` row in `host_failure_state` so the lifespan-managed sampler short-circuits on the next tick), Resume sampling (clears the bare-id and every per-provider `<provider>:<host_id>` prefixed row), Apply SNMP vendor MIBs (set / add / remove modes against each host's `snmp.vendors` override), and Apply SNMP tunables (per-host walk_concurrency / wall_clock_budget overrides with the same `(1..16, 5..600)` bounds as `_clean_host_snmp` enforces; `clear=true` removes overrides). Backend exposes four endpoints `POST /api/hosts/bulk/{pause,resume,snmp_vendors,snmp_tunables}`; each validates host_ids against the curated list and returns `{applied, skipped, errors}` so the SPA can surface partial-success states. SweetAlert confirms the destructive Pause action. Vendor / tunable inputs get their own modals.
- "What changed" Timeline tab on the host drawer for incident triage. New `GET /api/hosts/{host_id}/timeline?hours=N` endpoint aggregates four signal sources keyed to the host into one chronological stream: ops history rows where `target_id` or `target_name` matches the host id (or any of its provider-name aliases), in-app notifications targeted at the host (`target_kind='host'`), `host_failure_state` snapshots for the bare-id AND every per-provider `<provider>:<host_id>` prefixed row (surface as `provider_paused` events), and `host_provider_last_ok` rows (surface as `provider_recovered` events). Frontend renders the merged stream as a vertical timeline below the drawer header (collapsed by default to keep first paint light) with severity-coloured icons, kind chips, ISO timestamps, and actor sub-line. Range picker (24h / 7d / 30d) plus refresh button. Per-host 30s TTL cache so range-switching is snappy.
- Unified Cmd-K / Ctrl-K command palette — universal "go anywhere" search modal that drops the operator into any drawer / view / admin tab from anywhere in the SPA. Pure-client implementation, no backend changes. Hotkey wiring intercepts `Cmd+K` / `Ctrl+K` BEFORE the modifier-combo bail-out (the only modifier combo we claim — operators expect Cmd-K to work mid-typing in a search box, matching Linear / Notion / Raycast convention). Result-builder fuzzy-matches the query against five sources: Hosts (id / label / asset.name / asset.vendor / asset.model), Items (stacks / services / containers), Admin routes (15 tabs), top-level views (Stacks / Services / Nodes / Hosts / History), and the Hotkeys list. Scoring: exact match 100, prefix 80, word-prefix 60, substring 40. Each group capped at 8 results. Keyboard nav: ↑/↓ wraps, Enter activates, Esc closes; mouse hover updates the selected index too. Adding a new admin tab needs ONE new i18n key + one entry in `_commandAdminRoutes`. **Follow-up patch:** Operator reported the palette didn't open on Windows (Ctrl+K event reached the page, but the modal didn't render). Tailwind v4's in-browser JIT was inconsistent at generating the `max-h-[80vh]` arbitrary-value class on the inner card — when the JIT skipped it the card rendered with no max-height AND `overflow-hidden` clipped the result list to its own collapsed height, leaving the operator with what looked like "panel didn't open" even though `commandPaletteOpen=true`. Replaced the arbitrary class with an inline `style="max-height: 80vh"` (which the browser always honours regardless of JIT state) and centred the modal vertically (`items-center` instead of `items-start` + `mt-[8vh]`) to mirror the working Hotkeys help modal. Same drift class as the earlier `pt-[8vh]` collapsing — Tailwind v4 arbitrary-vh values are unreliable on the in-browser runtime; reach for inline style or stock classes when the value is load-bearing. **Follow-up patch 2:** STILL not opening after the Tailwind fix. Added a SECOND keydown listener on `window` with `{ capture: true }` that runs in CAPTURE phase — strictly before the bubble-phase `handleHotkey` AND before the browser's UI-shortcut layer (Chrome / Edge consume Ctrl+K for omnibox-search even when `preventDefault()` runs in bubble phase). The capture-phase handler matches the same predicate, calls `preventDefault() + stopPropagation()`, and toggles the palette directly. Standard pattern for hotkeys that collide with browser defaults — `mousetrap` and similar libraries do this internally. **Follow-up patch 3:** STILL not opening after the capture-phase fix. Replaced the modal's outer `x-show="commandPaletteOpen"` gate with an explicit inline `:style="commandPaletteOpen ? 'display:flex' : 'display:none'"` so Tailwind v4's JIT and any cascade quirks can't mask the toggle. `openCommandPalette()` now wraps every step in defensive try/catch so a failure in `$nextTick` / `input.focus()` can't prevent the state flip from landing. Diagnostic logs land in DevTools console (`[cmdpal] capture-phase hotkey matched...` and `[cmdpal] open called, commandPaletteOpen now=true`) plus a `window.__omnigridOpenPalette()` global escape hatch lets operators verify the modal renders independent of the keystroke path.
- Per-host Health Score (0-100) chip + breakdown popover. New chip in the Hosts row header (desktop + mobile) and the host-drawer header that synthesises CPU / Memory / Disk / Provider-failures / Pending-Updates into one 0-100 number using worst-axis-wins. Operator's intended mental model: "anything <80 gets attention this morning" — synthesises ~6 visual cues into one. CPU / Memory / Disk axes share the existing stat-bar warn/crit thresholds (60 / 85, operator-tunable) so the chip's amber turn-over aligns with the bars'. Three colour tiers — green ≥80, amber 50-79, red <50. Clicking the drawer-header chip toggles a per-axis breakdown popover with mini progress bars showing exactly which axis is dragging the score down + a close button. Down/paused hosts short-circuit to a single Status axis with score=0. New i18n family under `hosts_extra.health.*`.
- Disk-percent label distinguishes "genuinely zero" from "small but non-zero". On a dd-wrt SNMP host with `/opt` reporting ~39 MB used / 232 GB total = 0.016% — `Math.round` floored to 0%, hiding the signal that data IS arriving. New `fmtPercentLabel(v)` helper renders `<1%` for `0 < v < 1`, one-decimal for `1 ≤ v < 10`, integer above 10. Bar-fill width also gets a 1% floor when the underlying value > 0 so the bar shows a visible sliver instead of empty when the label reads `<1%`. Wired into the disk row in desktop table + mobile card.
- SNMP-only host stat-bars + sparklines on the main Hosts list. Pre-fix `hostHasTelemetry()` only matched the four unix-style providers (Beszel / Pulse / NE / Webmin), so SNMP-only hosts (managed routers, switches, DD-WRT boxes) had their CPU / Memory / Disk stat-bars AND inline sparklines hidden even though the per-host probe had populated `cpu_percent` / `mem_percent` via UCD-net-snmp + hrProcessorLoad. The gate now includes SNMP only when the agent ACTUALLY reports host-resource data — non-zero CPU, mem-total, or mem-percent. Chassis BMCs like Dell iDRAC / Cisco IMC / Supermicro IPMI (SNMP for hardware sensors only — fans / temps / PSU / RAID / BIOS, NOT host CPU/Mem) stay correctly hidden so 0% bars don't mislead the operator into thinking the chassis is idle when really the agent doesn't expose those metrics. Disk reads 0% (or `<1%` post-fix) honestly when the SNMP agent doesn't expose dskTable.
- Probe-timeout badge on the Hosts row. When the per-host /api/hosts/one/{id} probe returns HTTP 504 (per-host budget exceeded), the row now renders an amber clock glyph next to the hostname so the operator sees a distinct "actively timing out" signal instead of the row sitting silently on its previously-known data. Priority order across the three Hosts-row badges is now paused (red triangle) > probe-timeout (amber clock, transient 60s back-off) > stale (amber clock, snapshot fallback) — each gate excludes the higher-priority states so only one badge ever renders at a time. Tooltip explains the 60s back-off window. Wired on both the desktop table and mobile card. Flag clears automatically on the next successful probe.
- Host-row warning/error icon tooltips made specific to the underlying cause. Pre-fix the badges (sampling paused / probe timeout / stale snapshot) had brief generic titles; operator wanted hover answers "what's wrong + what to do" without opening the drawer. New `*_full` i18n variants spell out: paused → adds last error + the Resume hint; probe-timeout → explains 30s budget exceeded + common causes (slow SNMP walk, hub mid-restart) + auto-retry; stale → lists the actual `_stale_fields` so the operator sees which keys came from snapshot. Each badge is now wrapped in a `<span role="img" :title :aria-label>` so the tooltip fires reliably on browsers / extensions that skip `title` on bare SVG, AND screen readers announce it. Cursor changes to `cursor-help` over the badges.
- Pulse time-series storage so Pulse-only hosts (Proxmox VMs without a Beszel agent or node-exporter) get the same inline Hosts-row sparklines AND host-drawer chart cards every other provider host already had. New `host_pulse_samples` SQLite table + lifespan-managed `logic/host_pulse_sampler.py` (one central `probe_pulse()` per tick covers every host — Pulse hub returns whole-fleet state in a single call). Counter-rate computation for net rx / tx follows the same skip-don't-synthesize discipline as the other samplers (60 ≤ Δs ≤ 900, 0 ≤ Δbytes ≤ 10 GB; out-of-bounds deltas SKIP rather than synthesize 0). `recent_samples` / `history_series` read helpers emit the same Beszel-compatible envelope so SPA chart helpers + inline sparkline data-source ladder treat Pulse-only hosts identically to NE-only hosts. `/api/hosts/history` falls through to the Pulse path when NE has no rows AND the curated row carries `pulse_name`. SPA chart-grid gate extended from `(h.beszel_id || h.ne_url)` to `(h.beszel_id || h.ne_url || h.pulse_name || h.webmin_name)` so the drawer chart cards mount for Pulse-only and Webmin-only hosts.
- Webmin time-series storage parity. Same shape as the Pulse work above — `host_webmin_samples` table + `logic/host_webmin_sampler.py` lifespan task, but per-host fan-out (Miniserv is per-host like NE, not a central hub). Resolves the last "Webmin-only hosts have no sparkline" gap. `/api/hosts/history` host_id fallback chain now reads NE → Pulse → Webmin (Pulse first because Pulse-only hosts are more common). Most operators run Webmin alongside NE so the NE sampler already covers them; this work only matters for the small set of hosts where Webmin is the SOLE configured surface.
- Per-vendor SNMP `walk_concurrency` global defaults — five new tunables (`tuning_snmp_walk_concurrency_dell` / `_cisco` / `_synology` / `_ucd` / `_printer`, range 0..16, default 0=disabled). When sysDescr auto-detect resolves to EXACTLY ONE vendor AND no per-host override is set AND the vendor's tunable is non-zero, the resolver picks the vendor-specific default instead of the generic `tuning_snmp_per_host_walk_concurrency`. Lets a Dell iDRAC fleet pin a global default of 4 while a printer fleet stays at 1, without setting per-host overrides on every row. APC excluded — single-GET probe, concurrency has no effect.
- Bulk-pattern picker on Profile → Notifications. New `<select>` row under the existing bulk buttons lets a user pick one medium for "all success events" and another for "all failure events" in one click instead of clicking 12+ checkboxes. Classification follows the existing success/failure event-group pairing; admin-disabled events are skipped (matching the Errors-only pattern's contract).
- `_VENDOR_SIGNATURES` weighting + `_detect_primary_vendor` helper. Each signature now carries a relevance weight (vendor-specific tokens like `idrac` / `smart-ups` / `ciscosystems` score 80-100; generic OS markers like `openwrt` / `alpine linux` score 30). The legacy `_detect_vendors_from_sysdescr` set-return contract is preserved (auto-detect still picks every matching vendor). The new `_detect_primary_vendor` returns the single highest-scoring vendor for tighter-pruning futures — a Linux-running Cisco IOS XE device tie-breaks cleanly to `cisco` instead of double-walking both families.
- Per-host SNMP `wall_clock_budget` override on `hosts_config[].snmp.wall_clock_budget` (range 5..600s, matching the global `tuning_snmp_wall_clock_budget_seconds` bounds). Same shape as the existing `walk_concurrency` override — overrides the global tunable when supplied, falls through when blank. Lets a slow iDRAC chassis pin a 90s budget while the rest of the fleet stays at the 60s default. Admin → Hosts editor SNMP row gains a "Wall-clock budget (s)" input alongside "Walk concurrency"; both placeholders now read "Inherited: <N>" so an empty input visually distinguishes itself from a typed value matching the global. New `client_config.snmp_wall_clock_budget_seconds` on `/api/me` so the SPA renders the right placeholder.
- `probe_snmp` SUCCESS responses now carry the same diagnostic dict (`active_vendors` / `active_vendors_source` / `sys_descr` / `skip_entity_mib` / `walk_concurrency_resolved` / `wall_clock_budget_resolved`) the TimeoutError branch already surfaced. Operators verifying "is the per-host vendors override actually taking effect?" can now see the answer on a healthy probe without waiting for a timeout. `/api/snmp/test` passes the diagnostics through on both success and failure responses.

- Vendor-aware SNMP walk pruning. `probe_snmp` now runs a Phase 0 sysDescr GET solo before any other walk, then auto-detects the agent's vendor from sysDescr against a substring-signature map (Dell / Cisco / APC / Synology / UCD-net-snmp / Printer). Phase 1 only runs base + matching-vendor walks — skipping the ~17-30 walks that don't apply for the detected vendor. A Dell iDRAC's 67-walk probe drops to ~50 walks (saving ~13s at concurrency=1); a non-vendor host like a Linux box or printer drops to ~30 walks. ENTITY-MIB walks are also pruned when only Dell is detected because Dell-RAC-MIB has the chassis identity already. New per-host `hosts_config[].snmp.vendors` override (subset of dell / cisco / apc / ucd / synology / printer) bypasses auto-detect for agents with stripped sysDescr or to force a vendor's walks even when auto-detect would skip them. When sysDescr is empty / unrecognised AND no per-host override is set, falls back to walk-all so unknown agents stay covered. Admin → Hosts SNMP row gains a "Vendor MIBs" checkbox group; the panel reads "Auto-detect from sysDescr" when nothing is checked and "Override: walking only base + <list>" when at least one is. Probe response (on timeout) carries structured `active_vendors` / `active_vendors_source` / `sys_descr` / `skip_entity_mib` fields so the diagnostic shows exactly which walks were live and why.
- Per-host SNMP walk_concurrency override on `hosts_config[].snmp.walk_concurrency` (range 1..16, matching the global `tuning_snmp_per_host_walk_concurrency` bounds). Server-class BMCs like Dell iDRAC, Cisco IMC, and Supermicro IPMI handle parallel queries fine and need > 1 to fit pysnmp v7's per-walk overhead inside the probe budget; the safety-floor concurrency=1 default stays for low-power embedded snmpd's that drop UDP packets at higher concurrency. Admin → Hosts editor SNMP row gains a "Walk concurrency" input; placeholder shows the resolved global default sourced from `tuning_snmp_per_host_walk_concurrency` (via `/api/me`'s `client_config.snmp_per_host_walk_concurrency`) so the resolved value is visible at a glance when the field is blank.
- Per-medium notification preferences in Profile → Notifications. Replaces the previous single-checkbox-per-event list with a grid (one column per delivery medium — Apprise / In-app — plus an event-label column and an "All" master-toggle column). Users can now route different events to different channels — success events to the Apprise inbox and failures to the In-app drawer, success-of-everything to In-app and failure-of-everything to Apprise, or any combination across the full event list. Column headers double as bulk-toggle (left-click enables a channel across every event; right-click disables). The "All" column toggles every channel for that event in one click. Globally-disabled mediums (admin flipped off the whole channel) still appear in the grid with a warning glyph + tooltip explaining the column won't deliver until re-enabled in Admin → Notifications. Backend storage stays back-compatible: the `ui_prefs.notify_events` map now accepts a mixed shape — each event's value is either a bare `bool` (legacy "enabled across every channel") OR a per-medium dict `{medium: bool}`. The dispatcher in `logic/ops.py:notify` checks the per-(event, medium) gate inside the medium fan-out loop so a single notification can fire on Apprise but skip In-app (or vice versa) without affecting other events. Uniform-bool dicts collapse back to a bare bool on save to keep storage compact, so deploys whose users don't route per-medium see no shape drift in their persisted state.
- `/api/me` now returns `notify_mediums: [{name, enabled}, ...]` listing every registered notification medium with its global-enable state. The SPA's Profile → Notifications grid renders one column per entry without a separate round-trip, so adding a new medium via the documented `NOTIFY_MEDIUMS` extension contract automatically extends the grid.
- Swarm agent unhealthy banner + one-click restart. Detection runs at the end of every `gather_stats` from per-node task-derived-cid stats-success ratios; the banner appears above the Stacks / Services / Nodes views (deliberately NOT on Hosts — host-stats providers don't go through Portainer's agent) when consecutive failures exceed `tuning_swarm_agent_unhealthy_threshold` (default 3, range 1..20). The banner carries a "Restart agent service" button gated on admin role: `discover_swarm_agent_service(client)` matches by image-prefix (`portainer/agent`, `portainer/agent-ce`, `portainer-ee/agent`, `portainer-ce/agent`) or name-fallback, the new `do_restart_swarm_agent` op handler bumps `Spec.TaskTemplate.ForceUpdate += 1`. Audit trail via the existing operations system; Apprise events `swarm_agent_restart_success` / `swarm_agent_restart_failure`. Counts only task-derived cids — manager-aggregated cids respond fine even when a worker's agent is dead, so counting them would suppress the banner.
- Log files viewer (Admin → Logs → Files) gains a text filter input matching the Live tab. Same `logFilter` state — query typed in either tab carries across (mirrors the existing `logSeverityFilter` cross-tab pattern). Substring match against the parsed log line text, case-insensitive. Clear button next to the input wipes the query.
- Asset Inventory master toggle (`asset_inventory_enabled`) following the per-service master-switch pattern (Apprise / Open-Meteo / Portainer / SSH). Pill chip in the Admin → Asset Inventory header (pill-ok / pill-muted), checkbox under the intro paragraph, every dependent input wrapped in a `<fieldset :disabled>` so the HTML spec cascades the disabled state to every nested control. Test button stays active when off so creds can be verified before re-enabling; Save commits the toggle; Refresh respects the gate. `/api/asset-inventory` GET + `/api/asset-inventory/refresh` short-circuit to `{ok:false, error:'asset_inventory_disabled'}` when off; the `asset_inventory_refresh` schedule kind no-ops with a `skipped (asset_inventory disabled)` history row. Admin → Hosts "Load from asset inventory" autofill button hides when the master switch is off. Defaults true for back-compat — existing deploys see no behavioural change until the toggle is flipped.
- Operator-tunable per-container stats timeouts. Two new knobs in Admin → Config: `tuning_stats_targeted_timeout_seconds` (default 12s, was hardcoded 4s) for `/containers/{id}/stats` calls carrying `X-PortainerAgent-Target` and `tuning_stats_untargeted_timeout_seconds` (default 10s) for the no-header fallback. The 4 → 12s default bump fixes the most common worker-node-stats-empty failure mode: Portainer's agent forwarding to busy worker nodes routinely exceeds 4s, the targeted call would time out, the untargeted fallback would 404 (manager's daemon doesn't have the worker's cid), and the UI rendered as `—`.

- In-app notifications. Every event that already fires through Apprise (stack / container / service / prune ops, login, host sampling auto-pause) now also writes a row into a new `notifications` SQLite table. A Notifications popup (linked from the user-avatar dropdown — opens as an overlay, not a separate view, so operators can quick-check and return to the same page) renders the rows newest-first with severity dots, event icons, mark-read / mark-all-read controls, and severity / event / unread filter chips. The avatar shows an unread-count badge that ticks live over SSE (`notification:created` / `:read` / `:deleted` event types), with a 30s polling fallback for bearer-token clients.
- Per-medium notification toggles. Admin → Notifications gains two checkboxes — "In-app" and "Apprise" — that gate each delivery channel independently of the per-event toggles. Both default ON for back-compat. The dispatcher in `logic/ops.py:notify` fans out to every enabled medium in parallel via `asyncio.gather`, so a failure in one channel doesn't drop delivery on the others. Project conventions gain a "Canonical extension pattern: add a notification medium" entry codifying the six-step contract for adding a third medium.
- New `prune_notifications` schedule kind that sweeps notifications older than `tuning_notification_retention_days` (default 90 days). Operator-tunable from Admin → Config; admin-creatable from Admin → Schedules with the existing cron picker.

### Internal

- Deploy pipeline rolls each release with a single Swarm operation instead of two. The redundant `docker service update --force --image <REG_PATH>:<new>@<digest>` step was retired from `.forgejo/workflows/deploy.yml`; `OMNIGRID_IMAGE` is now exported with the registry-resolved `@sha256:...` digest pinned alongside the version tag BEFORE `stack deploy`, so `docker stack deploy --resolve-image=always` rolls the task in one go. Pre-fix every deploy ran TWO image-replacing operations in sequence (each with `start-first` rolling), leaving 2 stopped containers per deploy where 1 sufficed. Net: half the stopped-container churn + ~15-30s faster on the Pi. Project conventions deploy section updated to reflect the single-roll model. Step numbers in deploy.yml compacted: 6→removed, 7→6, 8→7, 9→8, 10→9, 11→10.
- Container base image bumped from `python:3.12-slim` to `python:3.14-slim`. Touches `Dockerfile`, `docker-compose.yml` (two comment references), `.forgejo/workflows/deploy.yml` (build-cache rationale block), project conventions files-and-layout entry,`.github/dependabot.yml` (Docker section comment), and `docs/guidelines/auth.md` (bcrypt-hash recipe note). No `requirements.txt` pins changed — every dep should resolve to the same version with cp314 wheels in place of cp312. If a dep's cp314 wheel isn't yet on PyPI, the `pip install -r requirements.txt` layer will compile from sdist (a few minutes longer); the deploy verification gates catch any hard incompatibility before traffic shifts.
- Front-end alpinejs pinned to `^3.15.12` in `package.json` (was `*`) and `node_modules/alpinejs/dist/cdn.min.js` refreshed via `npm install`. Lockfile + on-disk bundle now match the tracked pin. Caret semver lets future 3.15.x patch bumps drift in via `npm install` without a manual edit; minor jumps require touching the pin first.
- CI workflow moved to Node.js 24 ahead of the September 2026 Node 20 removal. `.github/workflows/publish-ghcr.yml` sets `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24="true"` at workflow scope so every JS-based step (`actions/checkout` / `docker/*`) runs on the supported runtime end-to-end. Dependabot's internal graph-build jobs need the same switch via a repo-level Actions variable; documented in `.github/dependabot.yml`'s comment block since the workflow `env:` doesn't reach Dependabot's runner.
- `logic.ops.notify(...)` now accepts optional `target_kind` / `target_id` / `metadata` kwargs that flow into the in-app store's metadata column. Existing call sites are back-compat — the new kwargs default to None and the legacy three-positional signature still works. Five `_do_*` op handlers and four user-login paths thread the new kwargs so the in-app rows carry actionable target hints (host id, stack id, container id, login method).
- New `notifications` SQLite table with indexes on `ts DESC` and a partial index on `read_at IS NULL` for the unread-count probe. Auth + retention plumbed through the standard four-place hydration audit (SettingsIn / api_get_settings / api_set_settings / loadSettings) so the per-medium toggles round-trip cleanly across browser tabs.

### Fixed

- Notifications + Portainer admin polish + Hosts row-flash unification. Swarm autoheal action selector relocated from Admin → Notifications to Admin → Portainer (the action targets Portainer's agent service, not notifications routing). Notifications Save button reordered to AFTER the tunable cards so the natural flow is per-medium → per-event → tunables → Save. `_appriseSnapshot` now folds in the retention + page-size tunables so editing them flips the page-Save's amber dirty-ring. Hosts row-stale-flash gate unified with Stacks/Services — was `(h._loading || isStale(h))` (pulsed during the brief per-host probe), now `isStale(h)` to match the item-rows' "pulse only when displaying cached data" semantic. The existing host-loading-spinner icon continues to indicate in-flight probe separately.
- Timeline endpoint emitted `provider_paused` / `provider_recovered` events from the CURRENT snapshot of `host_failure_state` + `host_provider_last_ok` — meaning a host that paused → resumed → paused → resumed over the requested window only showed the LATEST state, not the sequence. New `host_failure_events` SQLite table captures every transition with `(ts, host_id, provider, kind, error, actor)`. Wired into `_record_failure` (paused-edge) and `_clear_failure` (recovered-edge). Timeline endpoint queries the new table for the true history; falls back to the legacy current-state snapshot logic when the new table is empty so the timeline doesn't break during the migration window. Pruner reuses `tuning_stats_history_days`.
- Persisted swarm-agent autoheal cooldown anchors across container restarts. Pre-fix the anchors were module-level `float = 0.0` that reset on every restart — meaning a buggy schedule cron OR a malicious actor could bypass the cooldown by repeatedly restarting OmniGrid. Now persisted via three settings keys (`swarm_autoheal_last_restart_ts` / `_notify_ts` / `_notify_set`); lazy-loaded on first runner invocation after process start.
- Bulk-pause / bulk-resume opened a fresh `db_conn()` per host. For 200 selected hosts that was 200 SQLite write transactions. Refactored to a single outer transaction + `executemany`; per-row failure falls back to the per-host loop for partial-success error attribution.
- Admin-disabled medium chips in the categorised Profile → Notifications panel still mutated user prefs on click. CSS showed strikethrough + cursor: not-allowed but the click handler still flipped `profileForm.notify_events` for the disabled medium — leaving stale routing landing the moment the admin re-enabled. `toggleNotifyCategoryMedium` and `toggleNotifyCategoryAll` now short-circuit on `!notifyMediumIsGloballyEnabled(medium)`.
- Timeline endpoint f-string SQL builder triggered static-analyser taint-exit warnings (CodeQL, semgrep python-sql-injection). Safe at runtime (placeholders are `?` literals, values pass through bind parameters) but the shape itself was the trigger. Switched to string concatenation + a `# nosec B608` marker with rationale at the call site.
- `.notify-cat-chip` had a hardcoded `padding: 2px var(--s-2)` literal violating the spacing-token rule. Replaced with `padding: var(--s-1) var(--s-2)`.
- `notifyMediumNames()` rebuilt the medium-names array on every render call (Profile-panel x-for + 5 helper sites). Memoised on `_notifyMediumNamesCache` keyed against the underlying me-object identity.
- New `_full_host_cache_bust()` helper calls BOTH `invalidate_host_provider_cache()` and `_invalidate_provider_state_cache()` so the four bulk endpoints + future writers can't drift from the cache-pair contract.
- `items_snapshot` blob size cap (5 MiB). Pre-fix a runaway gather on a misconfigured fleet could produce a multi-MB JSON blob that strains SQLite's blob-handling on the read-back path. Cap-tripped writes log a WARN line + skip the write so the prior canonical snapshot stays intact.
- `/api/stats` cold-load instant paint — pre-fix the endpoint blocked on a synchronous `_gather_stats()` whenever `(now - _stats_cache["ts"]) > tuning_stats_cache_ttl_seconds`. `seed_stats_cache_from_db` deliberately stamps `_stats_cache["ts"] = 0.0` so seeded entries are refreshed on first call; result: every page load waited through the full Portainer container fan-out (10-30s) before bars / sparklines could paint on Stacks / Services. Fix mirrors the `/api/items` instant-paint pattern: new `_kick_background_stats_gather()` single-flight helper; the endpoint now serves the warm cache instantly when populated and kicks `_gather_stats` in the background. Cold-empty cache (first call after a fresh container) still blocks. New `stats_refreshing` boolean on the response wires into the topbar refresh button's "Refreshing…" indicator alongside the existing `cache_refreshing` / `hub_probing` flags.
- Hosts main-view sparkline observer gate accepted `beszel_name`. The IntersectionObserver-driven `loadHostHistory` prefetch checked `(h.beszel_id || h.ne_url || h.pulse_name || h.webmin_name)` — but `beszel_id` is the hub-resolved record ID (populated only AFTER a successful per-host /api/hosts/one/{id} probe lands), whereas the curated alias is `beszel_name`. For Beszel-only hosts the gate failed on initial paint and sparklines stayed empty until the per-host probe completed, even though the time-series in `host_metrics_samples` was already queryable. Gate extended to also accept `h.beszel_name`.
- Bulk-host action correctness pass — three coordinated fixes around the `/api/hosts/bulk/*` endpoints. (a) `first_failure_ts` now writes a `0.0` sentinel on the bulk-pause INSERT path so the host_metrics_sampler's failure-window math doesn't treat a manual pause as a fresh streak; ON CONFLICT path leaves an existing genuine streak's start-time untouched. (b) Bulk-pause / bulk-resume now publish `host:failure_state_changed` SSE events per applied host_id so cross-tab observers refresh in place rather than waiting up to 15s for the next loadHosts poll. (c) `selectedHosts` Set is intersected with the incoming host id set on every reconcile, so operators who deleted a selected host via Admin → Hosts no longer see a stale count badge until the next Clear click.
- Stacks / Services cold-load visual treatment to match Hosts. (a) Stat-bar `.stale` class binding extended to `(!statsLoaded || isStale(statsFor(item)))` so all six bar consumers (CPU / Memory / Disk × Stacks expanded + Services) dim during cold-load instead of painting as "0% bright bar". (b) Stack header spinner gated on `!expanded.includes(stack.name)` so it hides when expanded — same expand/collapse logic as the status dot, no more spinner duplication when stats hasn't returned and a stack is open. (c) Stack-level `.row-stale-flash` driven by `(stack._stale || (stack.items || []).some(i => i._stale))` so the entire group row pulses subtly until live data lands.
- Swarm-agent restart cooldown anchor moved to AFTER `asyncio.create_task` returns, with explicit failure handling. Pre-fix the anchor was stamped before the spawn — if `do_restart_swarm_agent(op)` raised synchronously the cooldown was locked in for the cooldown window even though no real restart happened. Now the anchor only stamps on a successful spawn; failure leaves the anchor at its previous value so the next tick retries immediately.
- `notifyCategories()` rebuilt the full categories array on every call — invoked from the Profile-panel x-for + 5 helpers. Each reactivity tick rebuilt 5+ times × 17+ events; Alpine tore down / re-created the per-row template (which the reactive-array rule prohibits). Memoised on `_notifyCategoriesCache`. The input arrays are static at runtime so the cache never needs invalidation.
- Background-refresh topbar indicator. The previously-orphan `cache_refreshing` / `hub_probing` API response flags from `/api/items` + `/api/hosts/list` are now consumed: new `cacheRefreshing` + `hubProbing` state vars; topbar refresh button spinner spins while EITHER the foreground refresh OR a background gather is in flight; label flips to "Refreshing…" when only the background one is. Auto-clears on the next poll.
- SQLite LIKE-pattern wildcard leak in three new host-id sites (timeline endpoint + bulk-host-resume). The patterns `(hid, f"%:{hid}")` did not escape SQLite LIKE meta-chars `%` and `_`; a curated host id `web_01` would match every `snmp:webX01` / `webmin:webY01` row via the underscore wildcard — bulk-resume on `web_01` would silently DELETE failure-state rows for unrelated hosts. The fleet-wide replacement at `main.py:6091` had retired this anti-pattern; the timeline + bulk-resume sites reintroduced it. New `_sqlite_like_escape(s)` helper escapes `\` `%` `_` for safe inclusion in LIKE patterns; all three sites now use `LIKE ? ESCAPE '\\'` paired with the helper. Any future code adding a `LIKE '%:host_id'` pattern MUST go through the helper.
- Bulk-host endpoints (`api_hosts_bulk_pause/resume/snmp_vendors/snmp_tunables`) called `invalidate_host_provider_cache()` but skipped `_invalidate_provider_state_cache()`. The per-host equivalents at `api_hosts_provider_resume` call BOTH. Result: bulk_resume deleted prefixed `<provider>:<hid>` rows from `host_failure_state`, but the next `/api/hosts/list` within the 5s `_PROVIDER_STATE_CACHE_TTL` window read the stale cached map and rendered the chip as still-paused. SPA fires `loadHosts(true)` immediately after the bulk action, almost guaranteeing the collision. Every bulk endpoint now calls both invalidators side-by-side. Rule: any code path that mutates `host_failure_state` OR `host_provider_last_ok` rows MUST call BOTH invalidators.
- `swarm_agent_unhealthy` notify title rendered with double prefix. Caller passed `"⚠️ Portainer agent(s) unhealthy"` (no colon); template render path extracts `{name}` by splitting on `":"` — no colon → whole title used as legacy_target_name → template `"⚠️ Swarm agent unhealthy: {name}"` rendered as `"⚠️ Swarm agent unhealthy: ⚠️ Portainer agent(s) unhealthy"`. Caller now passes a colon-formed title `"⚠️ Portainer agent(s) unhealthy: <host list>"` so the legacy parser extracts the host list as `{name}`.
- Body template rendered empty under severity=warning. `{error}` placeholder was only populated when `severity == "error"`, leaving warning / success templates that bind `{error}` with an empty body. Saved today by the `if rendered_body:` falsy fallback, but would break the moment an admin edits the template via Admin → Notification templates and removes the fallback path. Introduced a NEW `{message}` placeholder always populated with `legacy_body` regardless of severity; updated `swarm_agent_unhealthy` template to bind `{message}` instead of `{error}`. `{error}` semantics unchanged for back-compat. New rule: warning / informational templates MUST bind `{message}` not `{error}`. Added to `NOTIFY_PLACEHOLDERS` list + sample-values map for the live-preview pane.
- `<span @click>` chip nested inside `<button>` in the notify-category header. The new categorised Profile → Notifications panel had per-medium bulk-toggle chips as `<span @click.stop>` INSIDE the parent `<button class="notify-category-header">`. Two violations: HTML5 forbids interactive content nested in `<button>` (browsers handle focus + Enter activation inconsistently), and the project's "Clickable elements MUST be `<button type='button'>`" rule — span had no `role="button"` / `tabindex` / Enter-Space activation / focus ring, so keyboard-only operators couldn't toggle the medium routing. Refactored the header into a flex ROW (not a button); expand/collapse trigger is now `<button class="notify-category-toggle">` and medium chips are sibling `<button class="notify-cat-chip">`s in their own `<div role="group" class="notify-cat-chip-row">`. Each chip carries `aria-pressed` (true / false / mixed) so screen readers announce per-medium state. Admin-disabled chips use native `disabled` attribute.
- `swarm_agent_unhealthy` notify-only path fired unconditionally on every schedule tick. A genuinely down agent on a 5-minute schedule paged the operator 12×/hour. The restart action has cooldown via `tuning_swarm_autoheal_cooldown_minutes`; the notify-only path bypassed it entirely. New module-level state vars `_swarm_autoheal_last_notify_ts` + `_swarm_autoheal_last_notify_set` (frozenset of unhealthy hosts at last fire) gate firing on EITHER (a) the unhealthy host set changed since the last fire (new node failed / a previous one recovered) OR (b) the cooldown elapsed (chronically-down host gets reminded periodically). Reuses the existing tunable so operators have ONE knob across both action modes.
- `host_pulse_sampler` tick error `unknown tunable: tuning_pulse_probe_timeout_seconds`. The sampler reads `tuning.tuning_int("tuning_pulse_probe_timeout_seconds")` to bound the Pulse `/api/state` probe wall-clock, but the key was never declared in `logic/tuning.py:TUNABLES` — every tick raised in the resolver and the outer try/except logged + returned an empty hub map, silently disabling the Pulse fallback for the whole tick window. Added the tunable through the canonical six-step contract: TUNABLES entry `("PULSE_PROBE_TIMEOUT_SECONDS", 15, 1, 120)`, `SettingsIn` Optional field, `tuning_keys` list in `api_me`'s `client_config`, both `tuningKeys` arrays in `static/js/app.js`, i18n label + help, and `docs/guidelines/env_example.md` snippet + reference-table row. Default 15s preserves the previous `or 15.0` fallback so existing deploys see no behavioural change.
- SNMP per-core CPU chart `<template x-for>` inside `<svg>` Alpine error. DevTools console showed `Uncaught ReferenceError: line is not defined` from an Alpine effect. Browser HTML parsers don't treat `<template>` as a real template element when it's inside the SVG namespace, so the iteration variable `line` never entered scope and `:d="line"` evaluated against the parent component where `line` is undefined. Replaced the `<template x-for>` block with a single `<path :d="snmpCpuPerCoreCombinedLine(h.id)">`; the new helper joins every per-core path-d string with a space (SVG's `M` command implicitly starts a new subpath, so N concatenated paths render as N disconnected polylines). Matches the existing Dell-temp chart workaround for the same anti-pattern.
- APC UPS / iDRAC chassis BMC row showed CPU bar even though SNMP doesn't expose CPU. Regression introduced by the previous gate relaxation. Backend's API row shaper does `"cpu_percent": s.get("host_cpu_percent") or 0`, so when SNMP doesn't return CPU, the API row surfaces a literal 0 — and the relaxed gate accepted any finite value including 0. Fix: `hostHasCpuMetric` now requires `Number(h.cpu_percent) > 0` to short-circuit on the live value; when live cpu is 0 (ambiguous), defer to SNMP history which correctly stores NULL when the agent didn't expose CPU vs 0.0 when the agent reported a real zero. Same pattern for `hostHasMemMetric`. After fix: APC UPS / iDRAC stay correctly hidden; idle Cisco SG300 (live CPU 0, history CPU 0) still correctly shows.
- SNMP CPU/memory gate semantic — "agent CAN report" not "value > 0". Reported on 7 Cisco SG300 switches where the row's CPU bar + sparkline stayed hidden even though the drawer's CPU per-core chart had real data. Root cause: the row's gate required at least one strictly-positive sample (`v > 0`) in SNMP history to flip true. The row's backfill loads only 1 hour of SNMP history; if the host has been idle at 0% throughout that hour, every sample reads 0 and the gate hides the bar. Drawer-open extends the window so historical activity enters the cache → gate flips → bar appears (the "open + close drawer fixes it" workaround). The DB-level distinction already existed correctly: the SNMP sampler writes `cpu_used_pct = float(v) if v is not None else None`, so an agent that returns 0 stores 0.0 and an agent that doesn't expose CPU stores NULL. Renamed `_hostHasNonZeroSnmpHistory` → `_hostHasFiniteSnmpHistory` and changed the predicate to accept any finite (non-null) value, including 0. Memory branch relaxed similarly — `tot > 0` alone is the capability signal (a freshly-booted host with 0% used is still truthful data). Idle SNMP hosts now render their CPU/memory bars at 0% on initial page load instead of staying blank until drawer-open-close. Hosts whose SNMP agent doesn't expose CPU at all (APC UPS, iDRAC chassis BMC, basic switches) still correctly hide their CPU bars because every sample's `cpu_used_pct` is NULL.
- SNMP storage extractor filters pseudo-filesystem mounts + accepts a per-host exclusion list. DD-WRT was surfacing `/opt` as a 232 GB hrStorageFixedDisk on a 16 MB router, polluting host_disk_total. Two-layer fix: (a) universal pseudo-fs prefixes (`/dev`, `/proc`, `/sys`, `/run`, `/var/run`, `/var/lock`, `/dev/shm`) filter on every probe regardless of how the agent classifies them; (b) new per-host `hosts_config[].snmp.exclude_mounts` list (max 32 entries, exact or prefix-with-slash match) for device-specific phantoms — Admin → Hosts SNMP editor gets a textarea, one path per line.
- Threshold-coloured segmented disk bar on multi-mount hosts. Each mount segment now picks its colour from the same warn / crit thresholds the rest of the app uses (green / amber / red) instead of an index-based rainbow palette. Operators see a near-full mount visually scream regardless of which slot it occupies, while flex-1 segment widths still keep every mount visible. Reported on a TrueNAS VM where the bar appeared "60% green-filled" but the aggregate label read 1.6% — rainbow colours were obscuring which mount was actually full.
- Per-metric (CPU / Memory / Disk) telemetry gates on the Hosts row replace the previous all-or-nothing `hostHasTelemetry`. SNMP hosts that legitimately expose only some axes (Cisco SG300 switch — CPU only; wireless APs — CPU + Memory only; Dell iDRAC chassis BMC — neither) now show only the bars they have data for instead of three bars together (or none). Three new helpers — `hostHasCpuMetric` / `hostHasMemMetric` / `hostHasDiskMetric` — combine "unix-style agent configured (always all three)" with "for SNMP-only hosts, does the live or historical series actually report this axis". Each bar block on the row + mobile card now gates on its own per-axis helper; the outer container's union gate keeps the existing mount/unmount semantics. **Follow-up patch:** iDRAC + APC UPS hosts STILL showed a CPU bar after the initial fix because the original gate's "≥2 finite history points" check passed `Number(null) === 0` as finite, so flat-NULL `cpu_used_pct` rows the SNMP sampler writes every tick faked telemetry. Tightened to require at least one strictly-positive sample via a new `_hostHasNonZeroSnmpHistory` helper; flat-null and flat-zero series no longer count.
- Cmd-K / Ctrl-K palette now opens reliably regardless of focus (fifth follow-up patch on the original Cmd-K palette work). Operator-reported Ctrl+K only worked when focus was on the footer/header — from inside the page (input fields, drawer content) the keystroke was a no-op. Earlier diagnostic data revealed Ctrl+K was double-firing per keystroke; the sentinel-on-event fix from the fourth follow-up wasn't enough to gate the bubble-phase handler in all focus contexts. Final fix: removed the palette-combo branch from `handleHotkey` (bubble-phase) entirely so the capture-phase listener registered in `init()` is now the SOLE owner of Cmd+K / Ctrl+K. Window-level capture phase fires before any input-element handler regardless of focus, so the toggle works uniformly across the page.
- Command palette (Cmd-K) now matches multi-word queries. Typing `cisco switch` previously failed against `[Switch] Cisco SG300-52MP 52-Port Gigabit PoE Managed Switch` because the scorer treated the entire query as one substring. Now tokenizes the query, scores each token independently against the label, and returns the worst-token's score — labels with all the words (in any order, anywhere in the label) match.
- SNMP-only host CPU bar + sparkline appear immediately after the first per-host probe lands, not after drawer-open-then-close. `refreshHostRow`'s success branch now triggers `loadHostSnmpHistory` for `snmp_enabled` hosts when the cache is sparse — same backfill pattern that previously covered Beszel/NE/Pulse/Webmin only. Operator-reported on a Cisco SG300-28P switch.
- node-exporter parser now accepts both `node_memory_MemTotal_bytes` (canonical camelCase) and `node_memtotal_bytes` (lowercase fork form). Operator-reported on a Linux VM running standard node-exporter where `host_mem_total` was 0 while `host_mem_avail` populated correctly — same-shape inconsistency as the long-standing `node_memory_MemAvailable_bytes` camelCase handling.
- SNMP-only hosts now get an inline disk sparkline. Two new columns (`disk_total`, `disk_used`) added to `host_snmp_samples`; SNMP sampler writes them; `/api/hosts/{id}/snmp/history` envelope and `hostInlineSparkline`'s SNMP fallback both consume them. Operator-reported on a DD-WRT router + WDMyCloud NAS where the row's disk bar correctly showed live percent but the sparkline was blank (the previous SNMP fallback had no disk path at all).
- SNMP-only hosts no longer have their `host_cpu_percent` silently stripped from the live probe path when richer providers (Beszel / NE / Pulse) are GLOBALLY enabled for OTHER hosts. Operator-reported on a Ubiquiti USW Enterprise switch where the row CPU bar read 0% while the drawer's per-core CPU chart showed actual values. `extract_stats` previously gated on the global `active_sources` set rather than per-host config, so a fleet running Beszel for some hosts + SNMP for others saw SNMP-only hosts get cpu stripped. Removed the preemptive pop; per-host merge order via `_merge_best` already handles "richer provider overrides SNMP" correctly for mixed-provider hosts.
- Sparkline data loads immediately after the per-host probe lands, not on the next 15s `loadHosts` cycle. Pre-fix the IntersectionObserver-driven prefetch fired before the row's probe-derived provider fields (`beszel_id` / `ne_url` / `pulse_name` / `webmin_name`) had landed, so the gate skipped and sparklines stayed absent until the operator opened-and-closed the drawer (which calls `loadHostHistory` directly). `refreshHostRow`'s success branch now triggers the history fetch when the probe lands the provider fields.
- Disk sparkline hides cleanly when the historical series is flat-zero (Beszel agents that populate live `info.dp` but omit `stats.dp` produce all-zero history that previously drew as an invisible hairline at the bottom edge of the `.spark` element). New `sawNonZero` flag in `hostInlineSparkline` returns `''` for flat-zero series so the SVG x-show hides the spark unambiguously.
- ZFS multi-dataset pool dedup in the node-exporter parser. On a 14-dataset OPNsense `zroot` pool, `host_disk_total` was reading 8.7 TB instead of the true 817 GB because every ZFS dataset reports `size = used + pool_avail` and `avail = pool_avail` — naive sum inflates the total by (N-1) × pool_avail. Fix groups datasets by pool name and counts each pool's avail ONCE; subsequent siblings contribute only their own used. Per-mount display still emits every dataset individually. Non-ZFS filesystems unaffected.
- Hosts-row sparklines now use the same `.spark` style as Stacks / Services rows — sit BELOW the bar at 80px max-width / 10px tall, threshold-coloured (green / amber / red matching the bar reading). Pre-fix used `.host-row-spark` with full-width overlay and high-contrast white-on-dark stroke; the new treatment unifies the three views' visual rhythm. New `hostSparkClass(h, metric)` helper mirrors `sparkClass(item, key)` so the line colour follows `barLevel(value)`. Six markup sites updated (3 desktop, 3 mobile cards). The disk segmented-bar variant (multi-mount hosts) ALSO gets aligned to 16px height + 1px border so it matches `.stat-bar`'s visual weight — pre-fix it rendered at 10px / no border, breaking the horizontal rhythm with adjacent CPU / Memory bars.
- Ping `?` info-bubble tooltip now names the BACKEND-RESOLVED ping target via the full sampler chain (`ping.host` per-host override → `ssh.fqdn` → `ssh.host` → parsed hostname from curated `url` → `h.id` last-resort fallback) instead of `h.label || h.id`. The earlier round only checked `ssh.fqdn` / `ssh.host` / `id`, missing the highest-priority `ping.host` override AND the URL-hostname fallback step from `logic.ping_sampler._curated_ping_hosts`'s chain. New `_resolve_ping_target` helper in `_shape_host_api_row` mirrors the sampler's chain exactly. Tooltip now reads e.g. "Ping probe (router1.example.com · ICMP)" instead of "Ping probe (router1 · ICMP)" on rows whose probe target differs from the display label.
- `logic/snmp.py:_bounded` semaphore handling switched from `async with walk_sem:` to explicit `await walk_sem.acquire()` + `try/finally walk_sem.release()`. Behaviour-equivalent — the placeholder-bypass + cancellation-cleanup paths now read uniformly without nested context managers. An `acquired` flag in the finally block guarantees release even if `await coro` raises after a successful acquire.
- `_snmp_host_cache` and `_snmp_host_fail_cache` keys now include `frozenset(vendors)` alongside `h["id"]`. Pre-fix the bare-id key kept serving the cached previous-vendors result for `tuning_snmp_host_cache_ttl_seconds` (default 30s) after an operator edited `row.snmp.vendors`; now the new vendor set lands on the next probe immediately.
- Walk-concurrency input placeholder in Admin → Hosts SNMP row now reads "Inherited: <N>" instead of bare "<N>" via new `admin_hosts.snmp_walk_concurrency_placeholder` i18n key. Empty input + the placeholder visually distinguishes itself from a typed value matching the global. The HTML `max` attribute also corrected from `32` to `16` matching the global tunable bounds, and the SPA's `saveHostsConfig` walk_concurrency clamp updated to `wc <= 16` for consistency.
- SPA's `saveHostsConfig` SNMP vendors literal migrated from a hardcoded six-element set to `this.snmpVendorKeys()`. Adding a vendor in `_VENDOR_SIGNATURES` now flows through `/api/me`'s `client_config.snmp_vendor_keys` to the SPA save path automatically — no second SPA edit required.
- `_merge_one_host` SNMP probe path now records per-host probe outcomes via `record_provider_outcome` on both success and failure branches, mirroring the Webmin sister block. Pre-fix the SNMP per-host probe path bypassed the helper entirely, so `host_provider_last_ok` only got stamped at the slow lifespan-sampler cadence — the SNMP chip's "Updated Xm ago" subtitle drifted upward instead of refreshing on drawer open. Cool-down skips remain exempted (no `record_provider_outcome` call) so deliberate throttle skips don't count toward the per-host auto-pause counter. Real failures route through the auto-pause counter (`tuning_snmp_failure_pause_rounds`) so a hung agent eventually self-pauses.
- Per-host SNMP `walk_concurrency` clamp narrowed from `1..32` to `1..16`, matching the global `tuning_snmp_per_host_walk_concurrency` bounds. Pre-fix an operator setting per-host = 24 silently passed validation while setting global = 24 silently clamped — internally inconsistent.
- Debug panel "Active providers" SNMP gate now matches the actual probe path. `api_hosts_debug` previously listed "snmp" as active when an alias OR `snmp_name` was set; tightened to require `snmp_name` set AND `record["snmp"]["enabled"] is True` — same per-host opt-in flag the probe checks. Operators who explicitly disabled SNMP for a row no longer see it listed as active in the debug panel.
- `_resolved_*` placeholder coroutines bypass the per-host `walk_sem` semaphore in `logic/snmp.py:probe_snmp`. At Dell-only fleet pruning (~32 placeholder slots) the acquire/release overhead is no longer paid; only real walks contend for the semaphore.
- Single source of truth for the SNMP vendor key set. `_clean_host_snmp` previously hardcoded a six-element vendor literal; the SPA hardcoded the same list at `static/index.html:6168`; `logic/snmp.py:_VALID_VENDOR_KEYS` already existed. Backend now imports `_VALID_VENDOR_KEYS` via the new `_clean_vendors_input` helper. `/api/me` carries `client_config.snmp_vendor_keys` (sorted list); SPA's new `snmpVendorKeys()` helper iterates from there with a six-key fallback for old-server compat. Adding a vendor in `_VENDOR_SIGNATURES` now surfaces a checkbox automatically.
- "Vendor MIBs" label routed through i18n via new `admin_hosts.snmp_vendors_label` key; the hardcoded English literal in `static/index.html:6166` replaced with `x-text="t(...)"`.
- Vendor-checkbox dim/disable now also gates on `row.snmp.enabled`. Checkbox `:disabled` and label `:class="opacity-50"` add `!(row.snmp && row.snmp.enabled)` so the row's vendor selectors visually grey out when SNMP itself is disabled for that row, matching the existing `!row.enabled` gate.
- `logic/ops.py:notify` short-circuits when the user's per-event pref is a non-empty dict where every medium value is False (semantic equivalent of legacy bare-bool False opt-out). Pre-fix `{event: {app: false, apprise: false}}` would enter the per-medium fan-out, log N "skipped" lines plus a "no mediums enabled" trailer; post-fix one "opted out across every medium" line and return. Empty dicts still fall through to the per-medium fan-out (treated as "no explicit choice", every medium defaults to True there).
- `vendors` payload now filtered uniformly via the new `_clean_vendors_input` helper at three call sites — `/api/snmp/test`, `_merge_one_host`, `api_hosts_debug` SNMP kickoff. Non-string entries and unknown vendor keys are filtered at the boundary; downstream consumers no longer need defensive `isinstance` checks.
- `settings:updated` SSE handler in `static/js/app.js` now refreshes `/api/me` field-by-field (matching the saveSettings flow's pattern) instead of replacing `this.me` wholesale. Cross-tab admin saves that flip a master-toggle (e.g. `notify_medium_app=false`) propagate to every open tab within one SSE round-trip without tearing down DOM bindings reactive to `me.*` (Alpine Proxy identity contract).
- `_resolved_value` / `_resolved_dict` / `_resolved_list` placeholder coros now share a uniform `(v=None)` signature. Pre-fix `_resolved_value(v)` took a positional arg while the sibling helpers didn't — minor API divergence the ninth-pass review flagged.
- `api_me_notify_prefs` now logs `[notify] empty per-medium dict for '<user>'.'<event>' — treated as 'no explicit choice' (event pref unchanged)` when a PATCH sends `{event: {}}`. Pre-fix the empty dict silently dropped before merge so operators investigating "why didn't my notify toggle save?" had no breadcrumb. Persistence behaviour unchanged — empty dicts still drop (matches the "no explicit choice" semantics).
- CHANGELOG.md walk_concurrency range corrected from `1..32` to `1..16` in the per-host override entry, matching the global tunable bounds AND the existing per-host walk-serialisation entry. Eliminates the in-document contradiction the ninth-pass review flagged.
- `_VENDOR_SIGNATURES["ucd"]` no longer over-matches every Linux box. Bare `"linux "`, `"freebsd "`, `"debian "`, `"ubuntu "` tokens dropped — every Cisco IOS XE box, Dell iDRAC, and vendor BMC running embedded Linux pre-fix auto-detected as `ucd` and accumulated 6 wasted UCD-SNMP-MIB walks per probe. Anchored to genuine UCD / net-snmp markers (`"ucd-snmp"`, `"net-snmp"`, `"openwrt"`, `"raspbian "`, `"alpine linux"`) which only appear in sysDescr when net-snmp IS the active agent — the case where UCD-SNMP-MIB walks are productive.
- `_VENDOR_SIGNATURES["printer"]` "samsung " token replaced with seven Samsung-printer-specific product-line prefixes (`"samsung clp"` / `"samsung clx"` / `"samsung ml-"` / `"samsung scx"` / `"samsung xpress"` / `"samsung proxpress"` / `"samsung multixpress"`). Pre-fix the bare `"samsung "` matched Samsung NAS appliances, Smart TVs, and phones — the printer auto-detect then fired ~4 wasted Printer-MIB walks per probe against devices that always returned noSuchObject. Real Samsung printers still match.
- `/api/hosts/debug` SNMP block no longer fires a 20s probe against a host's bare id when the operator hasn't enrolled SNMP for that host. The SNMP block was the ONLY provider block in the handler missing per-host gating — every other block (Beszel / Pulse / node-exporter / Webmin / Ping) gated on a per-host config field, but SNMP only checked the global `"snmp" in active` flag then fell through to `record["id"]` as the probe target. On a fleet-enabled SNMP deploy, opening the debug panel for a ping-only / Beszel-only host fired a 20s SNMP probe against the host's bare hostname and timed out — surfaced as a red SNMP error chip on hosts that were never enrolled. Fixed by mirroring the canonical gate from `_merge_one_host`: HARD-GATE on `snmp_target` resolving from `snmp_aliases[record.id]` OR `record.snmp_name` (no bare-id fallthrough), AND on `record.snmp.enabled === true`. Both gates required.
- `/api/hosts/debug` endpoint no longer returns HTTP 504 for hosts whose probes cumulatively exceed the upstream `proxy_read_timeout` (default 60s on Nginx Proxy Manager). Two root causes were addressed. First, the "rendered" section was calling `api_hosts()` — which re-probes EVERY curated host — just to extract the one matching row; a 200-host fleet then re-fired 200 extra probe rounds on every debug request for no benefit. The rendered row is now derived synchronously by calling `_shape_host_api_row` off the merged dict the handler already built (zero extra network). Second, the SNMP probe's wall-clock budget was the same 60s used by sampler / gather paths even though every other probe in the handler had already consumed budget; the debug path now passes `wall_clock_budget=20.0` to `probe_snmp` so SNMP can't dominate. Beszel / Pulse `httpx.AsyncClient` timeouts also dropped from 15s → 8s, Webmin probe timeout from 10s → 8s. Worst-case sequential wall-clock fell from ~110s+ to ~55s, comfortably under the typical 60s reverse-proxy window.
- `logic/snmp.py:probe_snmp` no longer emits "coroutine '...' was never awaited" runtime warnings on cancellation. With per-host walk serialisation default concurrency=1, every `wait_for(gather(...))` cancellation left ~66 of ~67 `_bounded` wrappers cancelled before they ever entered their `async with walk_sem:` body — the captured raw coroutines never got awaited and Python's GC surfaced the leak on the next tick. The wrapper now wraps its body in `try/except BaseException` and calls `coro.close()` on every cancellation path, running the captured coroutine to synthetic completion. `close()` is a no-op on already-started coroutines so the healthy-path is unaffected.
- Dell iDRAC SNMP probe `host_dell_*` table fields (cooling devices / temperatures / PSUs / voltages / amperages / physical disks / virtual disks / BIOS) populated empty even when the manager's `snmpbulkwalk -Cc` returned data for the same OID. pysnmp v7's `bulk_walk_cmd` with `lexicographicMode=False` was over-strict on the iDRAC's reply OID format and short-circuited the walk at the first sub-tree-boundary check; switched to `lexicographicMode=True` with the existing prefix-filter as the boundary check + a `crossed_boundary` early-break so the walk stops at the FIRST out-of-tree OID instead of continuing past it. Cost: zero extra round-trips for walks that worked before; one extra discard round-trip on slow vendor walks that were broken before but now recover. Behaviour mirrors the working CLI semantics — operators can now trust the Server health card to render real data on Dell servers.
- Slow BMC-class SNMP agents (iDRAC9 / IPMI / low-power embedded snmpd) dropped packets when `probe_snmp` fanned out 60+ concurrent `bulk_walk_cmd` calls — the agent's UDP receive queue overflowed and ~15 of ~60 OID branches timed out per probe (PSU / voltage / amperage / phys disks / virt disks / BIOS). CLI `snmpbulkwalk` worked against the same OIDs because it was sequential. Fix: per-host `asyncio.Semaphore(N)` wrapping every coroutine in `probe_snmp`'s gather; `N = tuning_snmp_per_host_walk_concurrency` (default 1 — fully serialised, CLI-equivalent wire pattern; range 1..16). Fast snmpd's (Cisco / Synology / linux net-snmp) can recover full parallelism by raising the knob in Admin → Config — a 60-OID probe at 100ms RTT serialises to ~6s wall-clock at concurrency=1 vs ~0.5s at concurrency=16.
- iDRAC firmware field showed the chassis URL (`https://<ip>:443`) instead of a firmware version on modern iDRAC9 / iDRAC10. The old Dell-RAC-MIB documentation mapped `1.3.6.1.4.1.674.10892.5.1.1.6.0` to `host_firmware`, but on current firmware that OID is `iDRACURL` — the chassis web management URL, not a version string. `extract_vendor_info` now detects URL-shaped values from that OID (lowercase `http://` / `https://` prefix) and routes them to a new `host_idrac_url` field — preserves the operator-useful click-through to the iDRAC web UI rather than discarding it. Non-URL values (older firmware that DOES emit a plain version string here) keep the original `host_firmware` mapping for back-compat. Real iDRAC firmware version comes from the systemBIOS walk (`host_dell_bios_version`), surfaced on the Hardware card.
- Drawer "ENABLED AGENTS" chip strip now (a) top-aligns its chips so per-chip column wrappers sit flush at the top instead of vertically centring around the tallest chip, and (b) mirrors the OUTSIDE provider state — failing chips render red, paused chips render orange, healthy chips keep the per-provider brand colour. New `_agentStateFor(h, name)` helper consults `providerStates(h)` (same source the outer chip strip uses) so the inside-drawer and outside-row chip rows can never disagree on a host's provider health.
- Display label in Admin → Hosts is no longer auto-populated from the asset-import name or from the ID field. Pre-fix: importing from the asset inventory clobbered the operator's preferred label with the asset record's `name` / `vendor model` / `vendor`, AND typing the first character into the ID field auto-mirrored the value into a blank label. Both behaviours surprised operators who wanted the label to reflect their own naming convention. Post-fix: only the ID is auto-derived (from the FQDN / asset.name pair via the existing `_stripDomain` helper); the label stays whatever the operator types or leaves blank. Empty label falls through cleanly across the SPA — `hostDisplayName(h)` prefers `id` when label is blank, icon resolution still walks id + label + provider names.
- Synology dark-theme icon now uses the simple-icons single-colour white-on-transparent variant (`viewBox="0 8 24 7"`, `fill="#FFFFFF"`). The previous dark variant was a copy of the dark-coloured logo so was hard to see on dark theme. The icon resolver auto-swaps `synology.svg` → `synology-dark.svg` on dark theme via the existing `KNOWN_DARK_ICONS` mechanism — no wiring change needed.
- SNMP `Test connection` button (Admin → Hosts) no longer always reports `snmp: in cool-down (Ns remaining)` once the host has failed any automatic probe. Pre-fix the cool-down throttle (intended to suppress 60 sampler ticks/min from burning UDP timeouts against an unreachable host) gated the operator-clicked Test path too — operators fixing iDRAC / NAS / printer SNMP creds couldn't validate the fix until the 5-min cool-down expired, typically concluded the fix was wrong, and gave up. New `bypass_cooldown=True` on `/api/snmp/test`'s call to `probe_snmp` so operator-initiated tests always run; sampler / gather paths keep the throttle. Successful manual tests also clear any pending cool-down so the next automatic tick picks up immediately.
- Notifications retention dial moved from Admin → Process Tunables to Admin → Notifications, next to the per-medium / per-event toggles where operators editing notifications actually look for it. Same Save flow + effective-value resolution as before; only the visual location moved.
- SNMP probe wall-clock budget against slow embedded devices is now an operator-tunable separate from the per-OID timeout. Pre-fix the budget was hardcoded as `max(5.0, (timeout + 2.0) * 2)` — with the default 5s per-OID timeout that gave only 14s, far too short for the ~60 OID operations the probe fans out (sysName / hrStorage / hrProcessorLoad / ifTable / ENTITY-MIB + vendor-private MIBs). Slow embedded snmpd (low-power NAS, network printers, ~500 ms RTT) blew past 14s on every cycle, hit the 5-failures auto-pause threshold, and only succeeded when manually resumed. New `tuning_snmp_wall_clock_budget_seconds` (default 60s, range 5..600) sets the total wall-clock window for ONE probe; `tuning_snmp_probe_timeout_seconds` keeps its semantic as the per-OID UDP timeout (fast-fail on truly dead hosts). Both labels reworked in Admin → Config so the distinction between "per-OID timeout" and "wall-clock budget" is obvious.
- Debug panel "Active providers:" row now uses the same per-provider chip styling (icon + per-provider colour via `pill-custom` + `providerChipStyle`) as the rest of the SPA, applied uniformly across whatever providers are mapped on the host (ping / snmp / beszel / pulse / node_exporter / webmin). Pre-fix the chips rendered as plain neutral tags, breaking the visual association with the same provider chips operators see in the Settings → Providers tab strip and host rows.
- Linux Mint icon now resolves for the bare `mint` and hyphenated `linux-mint` slugs across both host and item/stack contexts. Pre-fix only `linuxmint` (no separator) and the full `linux mint` keyword phrase hit; an operator setting a host's icon override to `mint` saw a broken-image placeholder (resolver tried `/img/icons/mint.svg` and 404'd). Aliases added to both the `hostIconUrl` alias map and the `iconUrlFor` overrides map; keyword scan extended with `linux-mint`, the whitespace-padded ` mint ` short form (same defensive convention as ` wd ` / ` hp `), and `mint os`.
- Worker-node service stats — multi-round fix. Per-node `/containers/json?all=1&size=1` sweep with `X-PortainerAgent-Target=<host>` merges every daemon's containers; tasks-endpoint backfill via `/tasks` adds cids that aren't in the per-node listings; size dicts left unpopulated for task-only cids so disk renders as `—` not `0 B`; `[stats] <cid> no stats — agent_target=<node> status=<X>, untargeted status=<Y>` diagnostic lines on every failure. Final round: retry-on-500 loop in `_one_container_stats` for the agent-targeted call. Operator-observed pattern: a small subset of cids return `agent_target status=500, untargeted status=404` — agent is deployed and responding (not a connection failure), but transient overload from 16 concurrent stats calls through one Swarm-agent forwarder produces 5xx on a few. Three attempts with linear backoff (0.3s + 0.7s) catch the transient case; 4xx and 200 short-circuit immediately so no extra wall-clock on the healthy path.
- Notifications popup capped via server-side pagination — Prev / Next swap one page at a time so a fleet with 1000+ notifications never piles them all into the DOM. "Showing A–B of N (page X of Y)" footer + Prev / Next buttons; the response replaces the in-memory list (no client-side accumulation).
- `.chip-active` style now visibly distinct on top of severity-tinted chip backgrounds. Pre-fix the 1px inset ring + 8% brightness bump was lost against `.pill-error` / `.pill-ok` / `.pill-update` / `.pill-info` — operators clicked a severity filter in the notifications popup and couldn't tell which was selected. Post-fix: 2px outline halo + 2px inset shadow + bold label.
- Off-by-one `</div>` in `static/index.html` (introduced in the drawer-extraction refactor) closed; div counts balance at 1228/1228. The "Element div is not closed" IDE warning resolves.
- Notification popup severity filter chips now carry per-severity brand colours (info=blue, success=green, warning=amber, error=red) so the selected level is visually unmistakable; the active chip also gets an inset ring + brightness bump via the existing `chip-active` class.
- New keyboard shortcut `n` opens the notifications popup. Esc closes it (alongside the existing modal/drawer/selection/filter cascade).
- Avatar unread-notifications badge now renders fully visible above the avatar circle. Pre-fix the badge was clipped to a tiny dot because it was a child of `.user-avatar` (whose `overflow: hidden` keeps the uploaded image inside the circular boundary). Wrapping the avatar + badge in a sibling `.relative` anchor keeps the image clip behaviour while letting the badge render outside it.
- Drawer-chart range picker (1h / 6h / 24h / 7d) now persists across refresh via `localStorage.hostHistoryRange`. Pre-fix the operator's selection snapped back to the 1h default on every page reload.
- Profile → Notifications now greys out per-user toggles in real time when the admin globally disables an event in Admin → Notifications. Pre-fix the same-session admin saw their per-user checkbox stay actionable until the next full page reload — the SPA's `me.notify_events_admin` map was a stale page-init snapshot and didn't update when settings landed. Backend was already rejecting opt-IN attempts for globally-disabled events with 400; the UI now matches. Cursor switches to `not-allowed` on disabled labels for an extra visual cue alongside the opacity dim and the "Disabled by admin" tooltip.
- Printer-card freshness banner and SNMP chart-section freshness banner no longer disagree on the same host. Pre-fix the chart read the lifespan-managed sampler's last-row timestamp while the printer banner read the snapshot persistence timestamp — when the two writers fired at different cadences, operators saw "Last live data 7m ago" alongside "Last sample 9h ago" on the same drawer. Post-fix `snmpHistoryFreshness` returns the most-recent of (sampler ts, snapshot ts) and exposes a `source` field that drives a tooltip explaining which writer the timestamp came from.
- Apprise "Provider paused" / "Host sampling paused" titles now use the colour emoji warning glyph (⚠️) instead of the mono B&W variant — yellow triangle is visible at a glance in Apprise inboxes / Slack / Telegram alongside the rest of the operator's notifications instead of blending into the line.

### Security

- Event-bus publish-trace log line at `logic/events.py:178` now sanitises the identity hint through a regex allow-list (`^[A-Za-z0-9._:\-/]{1,128}$` → returns `m.group(0)`; falls back to `<id>` placeholder otherwise). CodeQL `py/clear-text-logging-sensitive-data` traced `webmin_password = ""` (initialised as empty literal in `main.py`) through `state` dict → cache → `_merge_one_host` → eventually the print site, despite the actual `_ident` value always being a host_id / op_id / schedule_id. Match-derived substring breaks the taint chain CodeQL conservatively walks; pathological values (now AND in any future dict ever taint-bridged into the payload) can't reach stdout.
- OIDC post-login redirect target no longer round-trips through the flow cookie. CodeQL's `py/url-redirection` taint tracker walked the value from `flow["next"]` straight through to `RedirectResponse(url=...)` because it doesn't model HMAC-cookie verification as a sanitiser; previous rounds of fixes (regex allow-list + `m.group(0)`) still got flagged. Refactor: server-side `_flow_paths: dict[str, (path, ts)]` keyed by the server-generated `state` token. At login start the `?next=` value is validated through `_safe_next` and stashed; at callback (after the existing state vs cookie HMAC check) the path is popped from the dict. Cookie carries only state/nonce/verifier — the redirect target genuinely never leaves server memory. Defence-in-depth re-validation through `_safe_next` on consume; opportunistic prune of expired entries when the dict grows past 256 to bound burst-driven half-finished flows.
- OIDC callback's post-login redirect target validated through `re.fullmatch` against an RFC-3986 path-chars allow-list, returning the Match's `group(0)` rather than the raw user value. Pre-fix the `_safe_next` helper used prefix checks and returned the user value verbatim — CodeQL's `py/url-redirection` taint tracker doesn't recognise prefix-only checks as a sanitiser, and even an intermediate `urlparse + reconstruct` round still propagated taint through `parsed.path`. Match-derived substring + a literal `_SAFE_NEXT_FALLBACK = "/"` for every reject path means the return value is never the raw input. Defensive prefix gates for `//`, `/\`, and any backslash also retained (some user-agents normalise backslashes to forward-slashes mid-flight, which can re-introduce a `//attacker.example` past a pure prefix check). Both call sites in `logic/oidc.py` (callback + login-start) benefit; matches the JS-side `login.js:nextPath()` pattern from #871.
- Cool-down message parser in `main.py:_humanise_probe_error` no longer uses regex. Pre-fix it called `re.search(r"(\d+)s remaining", low)` against probe response text that flows from `/api/snmp/test`'s body through provider-specific error formatters — CodeQL's `py/polynomial-redos` flagged the unbounded `\d+` on user-influenced data. Even bounding to `\d{1,10}` left the rule alerting; the durable fix replaces the `re.search` call with `low.find("s remaining")` + a bounded backwards walk that gathers up to 10 digit characters. No `re` call on the line, so the analyser has nothing to flag. Behaviour-equivalent for legitimate inputs; pathological strings with thousands of repeated digits now process in fixed time.
- Login-flow `?next=` redirect target now validated through `URL` resolution against `location.origin` rather than a naive `startsWith('/')` check. Pre-fix bypasses included `/\\evil.com` (browsers normalise the backslash, turning the path into `//evil.com` after the prefix check) and zero-width-character injections. Closes CodeQL `js/client-side-unvalidated-url-redirection`. Open-redirect / phishing vector eliminated.
- Per-row UID generation in Admin → Hosts now uses `crypto.randomUUID()` (cryptographically strong) via the `_mintRowUid()` helper. Follow-up patch: the previous `Math.random()` fallback inside the same helper carried a deprecated lgtm.com-style suppression annotation that the git host's static-analysis pass still flagged; the fallback now uses `crypto.getRandomValues(new Uint8Array(8))` (the same primitive that backs `randomUUID` itself) so no `Math.random` source remains anywhere in the SPA. Last-resort path on truly ancient browsers without either crypto API is a monotonic page-session counter — not random at all but unique within the tab, which is all an Alpine `<template x-for :key>` requires.
- Dockerhub auth credentials no longer leak to attacker-controlled token realms. `logic/registry.py`'s `"docker.io" in realm` substring check (CodeQL py/incomplete-url-substring-sanitization) replaced with a proper `urlparse(realm).hostname` exact-suffix match — only `docker.io` and `*.docker.io` qualify for the auto-attached `DOCKERHUB_USER` / `DOCKERHUB_TOKEN`.
- Shared URL-safety check (`logic/url_safety.py`) extracted from the Webmin module and applied at every probe entry point — Pulse, Beszel, Asset inventory, Webmin. Each `base_url` is admin-only DB input (require_admin gate + CSRF), not public form data, so the CodeQL py/full-ssrf flags on these call sites are false positives in the threat model — but the new validator gives every probe one place to (a) reject scheme typos like `file://` / `javascript:` / `data:` and missing hostnames, and (b) point CodeQL suppression annotations at a documented rationale. Validator stays intentionally permissive (accepts LAN IPs, custom ports, self-signed HTTPS) so the home-lab deploy story isn't broken.

### Fixed

- iDRAC drawer's Server health card now actually renders. Backend probe + snapshot fallback + frontend whitelist were all wired correctly, but the 10 `host_dell_*` fields were missing from `_shape_host_api_row` so they were silently dropped at the API boundary — the SPA always received `undefined` and the render gate evaluated false. Adding the fields to the row builder lets the card mount on any host whose SNMP probe walked back DELL-RAC-MIB rows. Same drift class as the prior `host_temperatures` / `host_gpus` regressions.
- Drawer charts no longer bridge across multi-hour sampling gaps with a single fake-smooth line. Operator-reported case: power-failure outage left the Ping chart drawing a continuous line from the last pre-outage sample to the first recovery sample, painting "down for hours" as "fading from X to Y". New auto-detected gap threshold (median sample interval × 2.5, 60s floor) breaks the rendered line at every long gap so the discontinuity is visible. Applied to the Ping / CPU / Memory / Disk / Network / Net I/O / Disk I/O / Load / GPU / Service-status charts, every SNMP chart (per-core CPU / CPU% / load / memory / per-port throughput / utilization / UPS load / battery / battery temp), the Dell server-temperature chart, and the host temperatures chart. Provider-agnostic — works for Beszel, node-exporter, Ping, and SNMP series alike. Area-fill paths also break at gaps so the fill doesn't bridge either. Underlying samplers were already correctly skipping out-of-bounds counter deltas per the skip-don't-synthesize rule; this fix is purely in the rendering layer.
- Ping packet-loss chip now reflects window-aggregated loss instead of the latest single tick. New `hostPingWindowLoss(systemId)` helper walks the loaded ping history series for the chart's selected range and returns `Math.round(100 × down_count / received_count)`. Missing samples (sampler not running, OmniGrid down) count as "no data" — NOT 100% loss — so the operator's multi-hour-OmniGrid-outage scenario shows 0% over a window where every received sample is alive. Pairs with the gap-aware chart fix above so the visual + the badge agree on what "no data" means.

### Added

- `SECURITY.md` at the repo root — concise security policy that the public git host auto-detects (renders the "Report a vulnerability" workflow on the Security tab). Covers supported versions (only the latest MINOR receives security fixes — the single-replica home-lab deploy story means operators run one cut at a time), private reporting channels (maintainer email or a private security advisory on the git host — never a public issue), what to include in a report (version, repro, impact, mitigations, redacted logs), response targets (72h acknowledgement, two-week fix-or-published-advisory window for confirmed issues, credit by default), in-scope categories (auth bypass, RCE / SSRF / path traversal, role escalation, credential leakage including operator-private hostnames in the public mirror, XSS), out-of-scope items (third-party CVEs tracked upstream, DoS from out-of-bounds tunables, attacks requiring already-compromised host access, deploys with defences explicitly disabled), and a hardening-runbook pointer block into `docs/guidelines/` (auth / passkeys / authentik / deploy). `CONTRIBUTING.md`'s "Security disclosure" section continues to summarise the process for contributors arriving via the on-ramp.
- `CONTRIBUTING.md` at the repo root — concise contributor on-ramp for outside collaboration on the public mirror. Covers project scope (single-replica + no build step + no formal test suite are deliberate constraints), bug-report template, feature-proposal flow, local dev setup, the load-bearing conventions outsiders need to know up front (i18n strict via `t()`, CSS strict via tokens, RTL via logical properties, long-running tasks in `_lifespan`, counter-rate samplers skip rather than synthesize, brand-icon onboarding from official sources), PR process (small PRs, exercise UI before submitting, update `[Unreleased]` but don't touch operator-private files), commit-message guidance, SemVer cadence pointer, security-disclosure channel, and a short code-of-conduct closer.
- Dell iDRAC server-health SNMP coverage. The SNMP probe now walks DELL-RAC-MIB's coolingDevice / temperatureProbe / powerSupply / voltageProbe / amperage / physicalDisk / virtualDisk / systemBIOS tables alongside the existing chassis-identity GET; the host drawer surfaces the data through a new "Server health" card with six subsections (fans, temperatures, power supplies, voltages, physical disks, virtual disks), each rendering name + value + status-pill rows with the standard Dell health enum colour mapping. Chassis-total power consumption surfaces as a pill in the section header. BIOS version + release date land on the existing Hardware card. Stale-fallback wiring across every subsection so cached values stay visible during a brief SNMP outage with the standard dimmed / "X minutes ago" treatment.
- Per-temperature-probe time-series chart on the host drawer for Dell servers. New `host_snmp_temp_samples` table backs the sampler; the chart card renders a polyline per probe (Inlet / Exhaust / CPU1 / CPU2 / etc.) sharing a single y-axis with a compact below-chart legend pairing probe name and last reading. Auto-ranged to max(60°C, observed max) so a normal-range server still has visible vertical movement. Picks up the existing 1h / 6h / 24h / 7d range picker so the time domain stays unified with every other drawer chart. New endpoint `GET /api/hosts/{id}/snmp/temp_history?hours=N` returns the per-probe series; admin-only.

## [1.3.0] — 2026-05-02

Third MINOR cut on top of `1.2.0` — rolls up **316 closed issues** under the 1.3.0 milestone (232 enhancements, 84 bug fixes). Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Highlights

- SNMP infrastructure (per-port throughput chart, utilization heatmap, total-throughput chart, opt-in per-host enable, tunables, uptime + reboot detection, Memory chart unit alignment).
- Ping host-stats provider end-to-end (per-host TCP/ICMP probes, drawer chart, hosts-table cells, cap_add NET_RAW for ICMP, cool-down skip semantics).
- APC UPS over-time charts (Output Load %, Battery %, Battery Temperature) in the host drawer.
- Drawer chart system polish (time-range picker disables-while-loading + spinner; `Updated Xs ago` freshness hint; first-position counters & state debug panel; full unified-cadence #232 timer + pushOnly gate).
- Provider chips + per-provider styling — chip class refactor, reactive colour application, mono SVG icons in provider tabs, paused-banner with debug-panel jump-link.
- Real-time / SSE polish — third "reconnecting" pill state with amber pulse, freshness-watchdog flips connection state on silent half-open sockets.
- Authentication tightening — passkeys WebAuthn QR-only on macOS root cause + fix (RP-ID), digest-mismatch follow-up, three-front fix shipped, OIDC cookie cleanup on every callback path.
- Body-scroll lock when any drawer is open — eliminates accidental background-page scroll while the operator interacts with the host / item / node drawer.
- Snapshot persistence timestamps now reflect the last LIVE probe (not the last save), so the host card's freshness banner agrees with the chart's "Last sample N ago" instead of refreshing on every drawer poll.
- Settings → Host stats refactor (tab strip with horizontal scrolling preserved + vertical scroll locked).
- Hardware card section gate now accepts snapshot-fallback hits so cached host_cpu_model / host_mem_total / host_disk_total / host_serial / host_model / host_firmware / host_vendor / host_swap_used stay rendered when every live provider is offline.

### Authentication, passkeys & 2FA

- `passkeys_allowed` now returned by `api_get_settings` next to the TOTP-policy fields (#218) [Bug]
- OIDC flow cookie now deleted on every callback path, not just the success branch (#222) [Bug]
- Spinner pattern brought to all Save buttons that were missing it (#293) [Bug]
- requirements.txt — bumped three floor-pinned deps to current PyPI latest (#295) [Enhancement]
- WebAuthn passkey QR-only on macOS — multi-pass investigation, root cause was RP-ID change (#330) [Enhancement]
- Authentication tab now has Enabled/Disabled pill + remaining width outliers across admin tabs unified (#342) [Enhancement]
- WebAuthn RP-ID mismatch detection (#359) [Enhancement]
- WebAuthn `verify_authentication` 0/0 sign-counter check comment rewritten to match actual code behaviour —... (#373) [Enhancement]
- Defensive `.get(key, default)` swap across every `_TOTP_POLICY_DEFAULTS[...]` and... (#492) [Enhancement]

### Real-time / SSE event stream

- Real-time event stream via SSE — replaces the SPA's polling-only "live feel" with a single push channel from... (#228) [Enhancement]
- UX batch — five UX-bugs and five UX-enhancements shipped together (#232) [Enhancement]
- SSE-push host history chart — `host_metrics_sampler.py:_probe_one` publishes `host:history_appended` event... (#234) [Enhancement]
- Live-mode tracing console.logs in `static/js/app.js` (#243) [Bug]
- docs-maintainer agent sweep — five files updated by the agent (api.md got a new "Client config" subsection +... (#249) [Enhancement]
- (CRITICAL) — removed `host:row_updated` SSE publish from `/api/hosts/one/{id}` (#251) [Bug]
- Wired `session:renewed` SSE listener in `static/js/app.js:_initSSE` (#257) [Enhancement]
- Fix — operator-visible amber toast on `:overflow` SSE event (#260) [Enhancement]
- Events-dropped counter chip alongside the SSE pill (#261) [Enhancement]
- Pass `force: true` to `refreshHostRow` from BOTH SSE handlers (`host:row_updated` listener kept for future... (#267) [Enhancement]
- Bounded the SSE per-subscriber `local: asyncio.Queue` (`asyncio.Queue(maxsize=256)`) so a paused/throttled... (#269) [Enhancement]
- SSE heartbeat cadence now operator-tunable via `tuning_sse_heartbeat_seconds` (default 25, range 5-300) (#272) [Enhancement]
- SSE connection lifetime cap now operator-tunable via `tuning_sse_max_lifetime_seconds` (default 21600 = 6h,... (#273) [Enhancement]
- SSE freshness-watchdog idle threshold now operator-tunable via `tuning_sse_idle_threshold_seconds` (default... (#276) [Enhancement]
- pollOps SSE-up keep-alive cadence now operator-tunable via `tuning_pollops_sse_keepalive_seconds` (default... (#277) [Enhancement]
- SSE freshness watchdog false-flip fixed (#294) [Bug]
- Debounced `host:history_appended` SSE handler (#494) [Enhancement]
- `X-OmniGrid-Client-Id` request-correlation header for SSE self-filter (#498) [Enhancement]

### SNMP

- SNMP host-stats provider (sixth in the family) (#361) [Enhancement]
- SNMP per-host enable checkbox persistence fixed (#363) [Enhancement]
- SNMP raw + normalized panels added to host-drawer "Show debug data" (#364) [Enhancement]
- Per-host "Enable SNMP for this host" checkbox flipped from default-on to OPT-IN (#365) [Enhancement]
- SNMP `tuning_snmp_probe_timeout_seconds` + `tuning_snmp_concurrency` are now actually consumed — operator... (#366) [Bug]
- SNMP tunables added to `SettingsIn` Pydantic model (#367) [Bug]
- SNMP per-host probe targeting hard-gated on alias OR `snmp_name` (#368) [Enhancement]
- SNMP `probe_snmp` `try/except asyncio.TimeoutError` now reachable (#369) [Enhancement]
- SNMP per-host cache TTL knobs separated from Webmin's (#370) [Enhancement]
- `_snmp_get` / `_snmp_walk` log exception type at WARNING + carve out cancellation (#376) [Bug]
- SNMP debug-panel raw payload expanded (#386) [Enhancement]
- Dedicated `tuning_snmp_unreachable_cooldown_seconds` knob (#389) [Enhancement]
- SNMP-aware `host_metrics_sampler` (#392) [Enhancement]
- UCD-SNMP-MIB OIDs (1.3.6.1.4.1.2021.x) for embedded Linux (#395) [Enhancement]
- SNMP walks no longer crash on pysnmp 7.x (#398) [Bug]
- APC UPS card in host drawer (#412) [Enhancement]
- Per-interface SNMP traffic chart in host drawer — oper-status dot, ↓rx · ↑tx mono span, stacked bar... (#415) [Enhancement]
- Hosts-page SNMP chip respects per-host opt-in flag (#422) [Enhancement]
- SNMP CPU/Load/Memory cards hidden when host also has Beszel or node-exporter (avoids redundant disagreeing... (#424) [Enhancement]
- SNMP chart cards upgraded to match Beszel/NE chart styling (420×120 viewBox, gridlines, legend strip +... (#425) [Enhancement]
- SNMP interface list capped at top 10 by traffic + per-host "Show {count} more" toggle (busy-by-traffic-desc... (#426) [Enhancement]
- SNMP Memory chart Y-axis no longer reads "0 B / 0 B / 0" while waiting on live probe — derives max from... (#429) [Bug]
- "No data from any enabled provider" banner lists SNMP + Ping (#430) [Enhancement]
- SNMP charts on freshly-enabled hosts show "Collecting first samples" hint (#432) [Enhancement]
- SNMP Memory chart unit alignment via `fmtBytesAt(value, refMax)` (#433) [Enhancement]
- SNMP uptime trend + reboot detection (#434) [Enhancement]
- SNMP total-throughput chart — cumulative ifHCInOctets / ifHCOutOctets sums persisted, in/out... (#438) [Enhancement]
- SNMP-only nodes no longer see the misleading "Time-series sourced from Beszel/NE" banner (#441) [Bug]
- Help-circle metric-source tooltip on every chart (Ping + SNMP CPU/Load/Memory/Throughput/Pages + per-port).... (#442) [Enhancement]
- Per-port SNMP throughput chart — new `host_snmp_iface_samples` table, sampler write per active... (#444) [Enhancement]
- SNMP Load chart legend zero-when-chart-non-zero — `snmpLoadLegendValue` falls back to `snmpStats(...).max` (#445) [Enhancement]
- SNMP Load chart renders as % of cores instead of raw load values — `snmpCoresFor` + `snmpLoadPctLive` helpers (#447) [Enhancement]
- Printer pages chart hidden on non-printer SNMP hosts (UPS / router false positives suppressed via... (#450) [Enhancement]
- SNMP freshness banner always renders in `--warning` orange (#452) [Enhancement]
- "Collecting data..." spinner pattern landed on EVERY chart card during warm-up — Beszel/NE side... (#468) [Enhancement]
- Dedicated SNMP sample interval — `tuning_snmp_sample_interval_seconds` (default 0 = inherit global) (#473) [Enhancement]
- SNMP throughput delta helpers emit `null` on out-of-bounds (counter wrap / reboot / gap) instead of... (#474) [Bug]
- Capped `/api/hosts/{id}/snmp/iface_history` SELECT with `LIMIT h * 60 * 64` (#484) [Enhancement]
- SNMP throughput / per-port throughput / per-port utilization charts render genuine null gaps as visual breaks... (#490) [Enhancement]
- Module-load INFO line in `logic/snmp.py` reports which pysnmp walk function the resolver picked... (#493) [Enhancement]
- Per-(provider, host) auto-pause + manual resume across EVERY provider (Beszel, Pulse, node-exporter, Webmin,... (#501) [Enhancement]
- SNMP charts now follow the drawer's 1h / 6h / 24h / 7d range picker (#504) [Bug]
- Time-range picker (1h / 6h / 24h / 7d) now renders on the host drawer for SNMP-only hosts (managed switches,... (#511) [Enhancement]
- Hardware card SNMP rows (model / serial / firmware) now render `—` placeholder when the snapshot saw the... (#512) [Bug]
- UPS info card now renders when ANY UPS field is present (live OR stale), not just `host_ups_status` (#518) [Bug]
- Per-port utilization chart now renders on hosts whose SNMP agent doesn't expose `ifHighSpeed` (printers /... (#520) [Enhancement]
- SNMP "Collecting first samples — chart will populate after the next sampler tick (~N min)" hint now reflects... (#526) [Enhancement]
- Printer info card now stays mounted with cached values when the SNMP provider is offline (#527) [Bug]

### Ping

- Added `icmplib==3.0.4` to `requirements.txt` so the Ping provider's "use ICMP" toggle becomes wired out of... (#296) [Enhancement]
- Ping-only hosts now register as "configured", get a provider chip, and surface accurate up/down status (#299) [Enhancement]
- Settings → Host stats TABS refactor shipped (#300) [Enhancement]
- Ping host-stats provider end-to-end (#301) [Enhancement]
- Settings → Host stats → Ping → Test target picker — fixed empty dropdown when the operator opens the Settings... (#302) [Bug]
- Hosts table — CPU / Memory / Disk bars no longer render on host ROWS for ping-only hosts (#303) [Bug]
- docker-compose.yml — added `cap_add: [NET_RAW]` to the `omnigrid` service so the Ping provider's optional... (#306) [Enhancement]
- Host-drawer Ping latency chart shipped (#308) [Enhancement]
- Drawer chart-grid wrapper now opens for `h.ping_enabled` too — pre-fix the gate was `(h.beszel_id ||... (#309) [Bug]
- Ping-only host CPU/Memory/Disk surfaces tightened across hosts table + drawer (#310) [Bug]
- /api/hosts/debug — `active_providers` now per-host filtered (#312) [Enhancement]
- Ping sampler hardening — robustness pass (#313) [Enhancement]
- Host drawer — dedicated Ping debug box (raw + normalized) added to the existing per-provider debug panel... (#314) [Enhancement]
- Settings → Host stats renamed to "Providers" — operator request that the section name reflect what it... (#315) [Enhancement]
- Ping chart range picker (1h / 6h / 24h / 7d) + cadence wiring complete (#317) [Enhancement]
- CURATED_FIELDS + CURATED_REFRESH_FIELDS extended for ping (#318) [Enhancement]
- Drawer second chart-grid wrapper now also opens for `h.ping_enabled` (#319) [Enhancement]
- Ping legend ms-formatting fix in host-drawer chart card (#320) [Bug]
- Ping chart x-axis labels were blank — fixed (#321) [Bug]
- Per-provider chip colour customisation in Settings → Providers (#326) [Enhancement]
- Hosts header provider-chip strip now includes ping (#328) [Bug]
- Host drawer Ping latency chart promoted to its own full-width row above CPU/Memory/Disk.. (#329) [Enhancement]
- Per-row provider chips on the Admin → Hosts EDITOR (the small `beszel`/`pulse`/`exporter`/`webmin`/`ping`... (#346) [Enhancement]
- Provider chips on the Hosts page header toolbar (top strip showing beszel/pulse/node_exporter/webmin/ping)... (#352) [Enhancement]
- SSH "Enable for this host" checkbox moved from RIGHT to LEFT of the SSH section, matching the Ping section's... (#355) [Enhancement]
- Ping port + transport per-host inputs now also disable when the host's main "enabled" is OFF — operator... (#379) [Enhancement]

### UPS / battery

- APC PowerNet-MIB OIDs (1.3.6.1.4.1.318.x) for Smart-UPS family (#394) [Enhancement]
- APC UPS card refinements in the host drawer (#515) [Enhancement]
- APC UPS over-time charts (Output Load %, Battery %, Battery Temperature) in the host drawer (#516) [Enhancement]

### Printer

- Printer-MIB walks added (#409) [Enhancement]
- Printer card supply bars now render in their mapped brand colour (cyan/magenta/yellow/black/waste-grey)... (#423) [Enhancement]
- Printer supply names render brand acronyms + SKU codes ALL CAPS — `titleCase()` rule extension (#436) [Enhancement]
- Printer pages-printed sparkline + lifetime headline (#439) [Enhancement]
- Lifetime page count repositioned inside Printer card body at 18px semibold mono (#449) [Enhancement]
- Printer card freshness banner — orange "Last sample Xm ago" via `snmpHistoryFreshness(h)` + snapshot-stale... (#467) [Bug]
- Printer card uses DB-backed history fast-path — `snmpLatestPageCount` walks history backwards (#469) [Enhancement]
- Pages printed chart REMOVED entirely per operator request (#470) [Enhancement]

### Beszel / Pulse / Webmin / Portainer

- Outer 45s timeout on `/api/hosts/one/{host_id}` to prevent NPM 504s (#241) [Enhancement]
- Mitigation — Beszel + Pulse hub probes inside `_do_host_provider_probe` now run in parallel via... (#259) [Enhancement]
- `_get_host_provider_state(force=True)` now also drops the per-host Webmin caches (#266) [Enhancement]
- Webmin probe outer budget unified across legacy `api_hosts` AND `_merge_one_host` via... (#274) [Enhancement]
- `_AUTH_COOLDOWN_SECONDS` duplicated across `logic/webmin.py:74` AND `logic/ssh.py:111` unified under one... (#280) [Enhancement]
- Move Webmin cache TTLs to Settings → Host stats → Webmin section (#285) [Enhancement]
- Settings → Host stats — unified Save (#289) [Enhancement]
- Admin Save button standardisation — in-flight + disabled state across Notifications / Portainer / OIDC + audit of ~10 other Save buttons + saveSchedule / saveRetention modal Saves + saveSshSettings label normalisation (#290) 
- Settings → Host stats tab labels simplified per operator request — three keys in `static/i18n/en.json`... (#298) [Enhancement]
- UI consistency — Apprise (Notifications) + SSH admin tabs now have an "Enabled" / "Disabled" pill next to the... (#338) [Enhancement]
- UX review batch — i18n hardcoded-string sweep, drawer/modal A11Y dialog roles, global focus-visible ring, prefers-reduced-motion expansion, skip-link utility, and /admin/hosts hard-href fix (#410) 
- Beszel + Pulse Test buttons pinned right via grid layout (`grid-cols-[1fr_auto]` + `justify-self-end`) (#414) [Enhancement]
- Hosts-toolbar Open Beszel / Open Pulse buttons floating to the trailing edge — three-pass fix landing on... (#440) [Bug]
- Beszel Load avg chart shows `load` unit chip in title (#455) [Enhancement]
- GPU chart cards (Power Draw / Usage / VRAM) for hosts with discrete GPUs via Beszel `stats.g` (#460) [Bug]
- Beszel Load avg chart renders as % of cores via `la*_pct` per-tick fields (#462) [Enhancement]
- README.md updated against current state — host telemetry charts list extended (Temperature, GPU Power / Usage... (#482) [Enhancement]
- Beszel history fetch now picks the right aggregation tier for the requested window (#513) [Bug]
- Pulse + Beszel probe failures now log to stdout (and therefore land in Admin → Logs) (#523) [Bug]
- Pulse and Beszel probes now hard-gate on explicit `pulse_name` / `beszel_name` aliases (#525) [Bug]

### Provider chips & icons

- Per-provider chip colours apply reactively in Hosts page + drawer (#327) [Enhancement]
- Provider icons (mono SVG) in Settings → Providers tab strip + Admin → Hosts collapsed-card chip strip —... (#362) [Enhancement]
- Hosts-page header provider chips became clickable filters (#391) [Enhancement]
- Provider tab strip dot now uses `.dot-on` / `.dot-off` utility classes (#407) [Enhancement]
- Per-port utilization heatmap. ifHighSpeed walk + `link_speed_mbps` persistence +... (#451) [Enhancement]
- `network_ifaces` added to `_BARE_SNAPSHOT_KEYS` so per-iface chip strip + per-port heatmap fall back to... (#476) [Enhancement]
- Per-iface 32-bit counter degraded badge on the host drawer's network-iface chip strip (#491) [Enhancement]
- "Last successful probe" timestamp on every provider chip (#497) [Enhancement]

### Drawer, charts & Node Exporter

- Admin → Process tunables — bounds rendered as three small icon chips (↓ min · ↑ max · ◎ default) instead of... (#248) [Enhancement]
- node-exporter per-host probe timeout unified across THREE consumers via... (#275) [Bug]
- Move "node-exporter probe timeout (seconds)" out of Process tunables to Settings → Host stats → Node-exporter... (#286) [Bug]
- Host drawer — dedicated "Enabled agents" card with colored pills, sitting just above the System card (#307) [Enhancement]
- Host drawer — dedicated "Enabled agents" card with colored pills + repositioned (#311) [Enhancement]
- History view's OP cell chip wraps `gather refresh` (and any multi-word op_type) onto two lines, looking... (#322) [Bug]
- Cloudflare brand icon shipped — `static/img/icons/cloudflare.svg` from homarr-labs/dashboard-icons (orange... (#324) [Enhancement]
- Tiny 9px package icon next to display name when sourced from asset inventory (operator-typed labels show no... (#357) [Enhancement]
- Stat-bar warn / crit thresholds operator-tunable (#406) [Enhancement]
- IDEA — Drawer focus-trap helper (`_focusTrap(el)`) (#417) [Enhancement]
- "+ Add URL" link in host drawer System card lands on the specific host's row in Admin → Hosts (#428) [Enhancement]
- Hardware inventory rows (host_model / host_serial / host_firmware) added to drawer Hardware card (#437) [Enhancement]
- Chart-source tooltip simplified — `metricSource()` returns only the active primary provider, no fallback... (#453) [Enhancement]
- Faded amber `⚠` triangle prefix on every stale text element via `.stale:not(.stat-bar)::before` (#454) [Bug]
- Permanently-flat chart cards hide after 1h soak via `hostChartIsPermanentlyFlat` (#456) [Enhancement]
- Chart title order unified `name → [unit] → tooltip`; dynamic unit chips via `unitForBytes()` (#458) [Enhancement]
- Network + Bandwidth chart-source tooltips simplified — `metricSource()` returns one active source (#459) [Enhancement]
- Temperature chart shows "Collecting data..." spinner during warm-up (#461) [Enhancement]
- Total throughput / per-port throughput legend + Y-axis share ONE unit family via `fmtBytesAt(v, max)` (#463) [Enhancement]
- "Edit" button added to host drawer header (admin-only) — close-drawer + openAdminTab('hosts') +... (#464) [Enhancement]
- Per-port throughput polylines now draw — rewrote as 10 fixed polylines indexed against... (#465) [Bug]
- Total Throughput chart static-rate headline (`↓ rx ↑ tx`) above chart line; per-port utilization heatmap... (#466) [Enhancement]
- Pages chart no longer stays in spinner forever for idle printers — gate dropped `snmpPagesPerDayMax > 0`... (#471) [Bug]
- Per-port utilization chart converted from chip-strip heatmap to a true LINE CHART (top-5 ifaces, Y-axis... (#472) [Enhancement]
- 32-bit ifInOctets wrap detection — `extract_interfaces` tags each iface row with `counter_width: 32 | 64` (#475) [Enhancement]
- "Updated Xs ago" freshness label suppressed on permanently-flat charts (#479) [Enhancement]
- Per-port throughput legend defensiveness — verified no fix needed (#480) [Bug]
- Compact stale display when ALL host_* fields are stale (#496) [Bug]
- Host-drawer charts on a unified time x-axis (#505) [Enhancement]
- Host-drawer pause-banner + Resume-button consistency pass (#506) [Enhancement]
- Top-of-drawer "{N} providers auto-paused" affordance (#509) [Enhancement]
- Disabled-host banner copy (#510) [Enhancement]
- Host-drawer debug panel now exposes per-host counters & state (#521) [Enhancement]
- Charts cropped from the right on initial drawer open (#524) [Enhancement]
- Body-scroll lock when any drawer is open (#530) [Bug]
- Time-range picker (1h / 6h / 24h / 7d) now disables its buttons while the underlying loaders are in flight,... (#531) [Enhancement]

### Hosts editor, Host groups & Hosts page

- Perf — short-TTL cache on `load_host_snapshots()` (default 5s, admin-tunable via... (#230) [Bug]
- Debounce on the Hosts-view filter input (#242) [Enhancement]
- Admin → Hosts collapsed-card layout fixes (#316) [Bug]
- Hosts page lazy-loaded probe fetch via IntersectionObserver (#331) [Enhancement]
- Hosts + Host_groups + Providers admin tabs aligned to the standardised pattern (#341) [Enhancement]
- SSH icon repositioned to RIGHT of the Admin → Hosts editor row header (was on the LEFT) (#345) [Enhancement]
- Per-host SSH flipped from opt-out (`ssh.disabled=true`) to opt-in (`ssh.enabled=true`) (#347) [Enhancement]
- Host display label now falls back to the asset-inventory's stored name when the operator has left the Admin →... (#350) [Enhancement]
- Admin → Hosts editor's collapsed row header — the small green/grey SSH-state dot replaced with an SSH... (#351) [Enhancement]
- Admin → Hosts editor's collapsed row header — when the operator clears the display label, the header now... (#358) [Bug]
- Host-level "enabled" checkbox now hard-gates every per-provider checkbox in Admin → Hosts editor — operator... (#371) [Enhancement]
- "Page X of Y" pagination labels in Admin → Hosts editor + Admin → Host Groups now use the existing... (#387) [Enhancement]
- Friendlier hosts_config save-side error messages (duplicate id / custom_number) (#431) [Bug]
- Orphan sweep on lifespan startup + per-provider orphan detection (#528) [Bug]

### Admin & Settings pages

- Ops poll cadence tunable — switched from milliseconds to seconds in the admin UI (#263) [Enhancement]
- Auth rate-limit policy now operator-tunable (#278) [Enhancement]
- `_WEBMIN_HOST_CACHE_TTL` (30s success) + `_WEBMIN_HOST_FAIL_CACHE_TTL` (5s failure) in... (#282) [Enhancement]
- `_HOST_PROVIDER_CACHE_TTL = 10.0` in `main.py:_get_host_provider_state` now operator-tunable via... (#283) [Enhancement]
- `_PROBE_CONCURRENCY = 8` in `logic/host_metrics_sampler.py` now operator-tunable via... (#284) [Enhancement]
- Settings → Host stats tab strip — horizontal scrolling preserved, vertical scrolling suppressed (#297) [Enhancement]

### Logs view & retention

- UI reorganization — moved two tunables out of the generic Process tunables form to their domain-specific... (#287) [Enhancement]

### Schedules & automation

- SnmpEngine module-level singleton (#382) [Bug]
- Warming-up banner reads configured sampler interval — three-pass fix landing on `snmpWarmingUpText()` helper... (#443) [Bug]

### Mobile / responsive UX

- `extract_storage` unit-normalisation heuristic for hrStorageType=RAM (#375) [Enhancement]
- Host mobile-card `.host-mobile-card-metric .name` font bumped 9.5px → 10.5px and letter-spacing 0.5px → 0.3px... (#405) [Bug]

### Topbar, login & branding

- Investigate "new version" blue topbar button not appearing (#227) [Bug]
- Single context-aware refresh button — replaced the topbar's icon-only refresh + the Hosts-toolbar "Refresh"... (#236) [Enhancement]
- Topbar refresh button restyled to match the previous Hosts-toolbar shape (#237) [Enhancement]
- Alpine `t` shadowing in the topbar nav — `<template x-for="t in navItems()">` declared the loop variable as... (#240) [Bug]
- Login error fix — disabled-user case now returns specific 403 "Account is disabled (#288) [Bug]
- Login UI — 403 detail now surfaced (#291) [Enhancement]
- Login UI — password field cleared on every failed login attempt (#292) [Bug]
- `get_credential_by_credential_id` SELECT in `logic/auth.py` now includes `rp_id` (#374) [Bug]
- Deploy workflow redirects `docker login` stderr to `/dev/null` (#385) [Enhancement]
- A11Y review LOW + NIT findings (#413) [Enhancement]

### Filters, badges & status pills

- Clickable `button.chip` chips meet `--touch-target-min` on phones (≤768px viewport) (#399) [Enhancement]
- IDEA — Provider filter chip "Solo" via Shift-click (#418) [Enhancement]
- IDEA — CHANGELOG "What's New" badge after deploy (#420) [Enhancement]

### Internationalisation & accessibility

- "Error: " prefix in host-debug error display now uses i18n via new `debug_panel.error_prefix` key — operator... (#388) [Bug]
- A11Y / IA broader retrofit (tablist roles, progressbar attrs, profile-modal avatar role) (#416) [Enhancement]
- i18n bundle JSON syntax fix (#500) [Bug]

### Database / migrations / data

- SHA-256 git migration — local working tree, push remote, runner-side checkout all converted from SHA-1 to... (#304) [Enhancement]
- Deploy migration to Dockerfile-based image build (Plan A — full image with static/ + node_modules/ baked) (#333) [Enhancement]
- Snapshot-first render in `/api/hosts/list` (#517) [Enhancement]
- Per-host probe path now writes to `host_snapshots` (#522) [Enhancement]

### API endpoints & backend helpers

- `api_hosts` docstring gained a deprecation note directing bearer-token scrapers to `/api/hosts/list` +... (#256) [Enhancement]

### Documentation

- Fix `CHANGELOG.md` release-page links on the public git host (#245) [Bug]
- Three stale references to `tuning_ops_poll_interval_ms` / `OPS_POLL_INTERVAL_MS` cleaned up in `README.md`,... (#253) [Bug]
- deploy.yml — replaced `actions/checkout@v4` with a manual SHA-256-compatible clone step (#305) [Bug]
- Hardened deploy.yml version-source resolution — code-complete (#334) [Enhancement]
- Extend deploy.yml to also push the built image to the container registry (#335) [Enhancement]
- Dockerfile OCI `image.source` label now carries a multi-line LABEL comment cross-referencing... (#383) [Enhancement]
- `_clean_host_snmp` now carries an explicit comment documenting that omission == disabled (#384) [Enhancement]

### Internal cleanup, refactor & bug sweeps

- Enhancements sweep — 17 enhancements shipped in one batch (3 of the original 20 — were already covered) (#231) [Enhancement]
- `_do_host_provider_probe(active:..., cache_key: tuple)` annotation corrected from `list` to `set[str]` (#254) [Enhancement]
- `loss_pct` format spec in `logic/ping_sampler.py` is now defensive — `(result.get('loss_pct') or 0):.0f` (#378) [Bug]
- `_ALLOWED_TRANSPORTS` (frozenset) and `_TRANSPORT_ORDER` (tuple) hoisted from per-credential loops to module... (#381) [Enhancement]
- `paused_at` SQL drift fixed — extended `_failure_state_for_host` SELECT + return dict to surface the column... (#477) [Enhancement]
- Defensive `.get("passkeys_allowed", True)` in `main.py` (two call sites) replaces the `[]` subscript on... (#486) [Enhancement]
- Cleaned unused `_default` destructure in `logic/schedules.py:_run_prune_logs` — switched to `_, _, _lo, _hi =... (#488) [Bug]
- Resume button defensive clear + visual prominence (#508) [Bug]

### Other improvements & fixes

- `prune_old_logs` cutoff math + filename-date parse now route through a new shared `_resolved_tz()` helper... (#219) [Enhancement]
- Legacy `/api/hosts` now calls `_shape_host_api_row(h, s, providers, any_provider_enabled=True)` per row... (#220) [Enhancement]
- `_validate_id_token._find_key` now rejects `kid is None` instead of silently picking `keys[0]` (#221) [Bug]
- `verify_authentication` now actually performs the sign-counter regression check the comment promised (#223) [Enhancement]
- `verify_registration` now whitelists client-supplied `transports` against the documented... (#224) [Bug]
- `/api/events` now caps each connection's wall-clock lifetime at `_SSE_MAX_LIFETIME_SECONDS = 6 * 3600` (6h,... (#225) [Enhancement]
- `auto_provision_authentik` username collision is now O(1) in expectation (#226) [Enhancement]
- Cursor:pointer fix — global `button { cursor: pointer; }` rule + `button:disabled { cursor: not-allowed }`... (#229) [Enhancement]
- Stale-data badges in the Hosts UI — three gaps closed end-to-end (#233) [Bug]
- Polling pill UX enhancement — pill mirrors the picker's chosen mode (Live / Off / Polling) with appropriate... (#235) [Enhancement]
- Loaders for Admin → Users / Sessions / Tokens (#238) [Enhancement]
- CSRF mismatch self-recovery in the global fetch wrapper (#239) [Bug]
- Fix fan-out 504s from `/api/hosts/one/<id>` saturating NPM's upstream pool (#244) [Enhancement]
- No-static-config rule + first knob converted (`PARALLEL` → `tuning_hosts_parallel_fetch`) (#246) [Enhancement]
- Admin → Process tunables — fixed hardcoded "six" subtitle + rewrote every help string with detailed use-case... (#247) [Bug]
- switched from relative paths to absolute git-host URLs (#250) [Bug]
- `_get_host_provider_state` re-computes `active` + `cred_blob` + `cache_key` INSIDE `_host_provider_lock` via... (#252) [Enhancement]
- `_webmin_host_cache.pop(h["id"], None)` also fires on failure-write branch (#255) [Bug]
- Fix — added `tuning_host_snapshots_cache_ttl_seconds` to the SPA's `tuningKeys` array (#258) [Enhancement]
- Version link references switched to ROOT-RELATIVE paths (#262) [Enhancement]
- Per-host probe wall-clock as hover-title on the host status dot (#264) [Enhancement]
- Removed env-var-name hint line from Admin → Process tunables rows (#265) [Enhancement]
- Prometheus histogram `omnigrid_host_provider_lock_wait_seconds` on `_host_provider_lock` acquire time (#268) [Enhancement]
- Request-correlation log line at every `events.publish` site (#270) [Enhancement]
- Hosts header label fixed — "polling off in Live" UX bug (#271) [Bug]
- Convention-violations housekeeping notes — closing for record (no work to ship) (#279) [Bug]
- Sorted Process tunables form alphabetically by translated label (#281) [Enhancement]
- Nodes-section source-count chip overcount — fixed both sides (#323) [Enhancement]
- Split `cloudflared` from `cloudflare` "solved now" (#325) [Bug]
- Three-front fix shipped (#332) [Bug]
- Flip Swarm to PULL from the container registry instead of using local-only tags (#336) [Enhancement]
- Removed Admin → Version page + GET/POST `/api/admin/version` endpoints (#337) [Bug]
- Title-row spacing unified across ALL admin tabs to the dominant `mb-2` pattern (#339) [Bug]
- Admin → Sessions tab spacing unified — `space-y-3` → `space-y-4` (matches Users / Tokens / Notifications... (#340) [Enhancement]
- Automated dep-bump PR config added for the public mirror (#343) [Enhancement]
- Fixed the digest-mismatch ✕ status on OmniGrid's own row (#344) [Bug]
- CRITICAL: cross-host SSH toggle bug — ticking row A's checkbox auto-enabled OTHER rows that didn't have an... (#348) [Enhancement]
- Digest-mismatch root cause + real fix (#344 follow-up; #117 investigation result) (#349) [Bug]
- Provider chips on the Hosts header toolbar now use `class="chip"` instead of `class="pill"` so the... (#353) [Bug]
- Long display labels now ellipsis-truncate with `min-w-0 max-w-[280px] truncate` instead of pushing the SSH... (#354) [Enhancement]
- SSH icon (and any other binding that reads from row data) was returning STALE state until a hard refresh... (#356) [Bug]
- Legacy `/api/hosts` refactored to compose `_get_host_provider_state` + `_merge_one_host` — operator... (#360) [Enhancement]
- Lazy IO observer fan-out now honours `tuning_hosts_parallel_fetch` concurrency cap (#372) [Enhancement]
- Renamed `for c in creds:` → `for cred in creds:` in `api_local_login_webauthn_start` (#377) [Enhancement]
- Host icon resolution now reads `assetForHost(h).name` / `type_short` / `vendor` / `model` as additional... (#380) [Bug]
- `probe_snmp` reads ENTITY-MIB physical-entry walks + sysContact / sysLocation (#390) [Enhancement]
- `probe_snmp` extended with Dell DELL-RAC-MIB (iDRAC) + Cisco CISCO-MEMORY-POOL-MIB / CISCO-PROCESS-MIB /... (#393) [Enhancement]
- SYNOLOGY-MIB OIDs (1.3.6.1.4.1.6574.x) for DSM-based NAS (#396) [Enhancement]
- Ubiquiti UniFi switch / AP sysDescr "MODEL, FIRMWARE" parser (#397) [Enhancement]
- `var(--provider-icon-size, 14px)` fallback literal removed from `.provider-icon` (#400) [Enhancement]
- `rgba(0, 0, 0, 0.18)` literal on `.log-sev-pill.is-active .log-sev-count` replaced with new... (#401) [Enhancement]
- `--r-pill: 999px` token added; all 7 `border-radius: 999px` literals migrated to `var(--r-pill)` — operator... (#402) [Enhancement]
- Typography token family declared on `:root` — `--fs-xs` (11px) / `--fs-sm` (12px) / `--fs-md` (13px) /... (#403) [Enhancement]
- Profile-modal avatar moved from inline `:style="'background: hsl(...)'"` to sanctioned `--avatar-hue`... (#404) [Enhancement]
- SweetAlert2 overrides token-ised — `13px` → `var(--fs-md)`, `12px` → `var(--fs-sm)`, `8px 18px` → `var(--s-3)... (#408) [Enhancement]
- Network card "idle interfaces" toggle for switches (#411) [Enhancement]
- IDEA — Density toggle (compact/comfortable/spacious) (#419) [Enhancement]
- Hosts-page CPU/Mem/Disk percentages now render as integers (`Math.round`) instead of `73.84579584587%` (#421) [Enhancement]
- Single-interface unhide — host with exactly 1 docker/internal iface (and no busy / idle ifaces) now renders... (#427) [Enhancement]
- Desktop Hosts-page CPU / Memory / Disk bars self-identify on hover via `:title` tooltips (#435) [Enhancement]
- No-data banner lists per-host enabled providers (was global `host_stats_source` CSV) —... (#446) [Enhancement]
- Per-field stale styling sharpened (opacity 0.55→0.45, saturate(0.6), dashed underline) (#448) [Bug]
- Per-port heatmap renders chips from live `network_ifaces[]` before iface_history accumulates (#457) [Enhancement]
- Dead `SettingsIn.show_header_clock` / `show_header_weather` fields removed (declared but never... (#478) [Enhancement]
- Provider icons + text labels in chips visually centered (#481) [Enhancement]
- Added `network_ifaces` to SPA `CURATED_REFRESH_FIELDS` (#483) [Bug]
- Unified host-refresh worker pool (#485) [Bug]
- Removed the legacy `_HOST_SNAPSHOT_KEYS` tuple in `logic/gather.py` (#487) [Enhancement]
- `_snmp_walk` connection-level errors now return whatever varBinds were already collected instead of... (#489) [Enhancement]
- Settings GET version int for cheap cross-tab change detection (#495) [Enhancement]
- Node column removed from the Stacks view (#499) [Enhancement]
- Resume-all button counted disabled providers (#502) [Enhancement]
- Per-provider admin-panel tuning-knob blocks share a centralised key list + disabled-gate helper (#503) [Enhancement]
- Services view's Node column now renders topology-style pills (host name + state-coloured dot, green for... (#507) [Enhancement]
- Stale banner now lists the actual stale field names so operators can identify what's counted as cached (#514) [Bug]
- host_swap_used now renders inline in the Hardware card (#519) [Bug]
- "Counters & state" debug-panel section moved to the FIRST position in the host-debug grid (#529) [Bug]
- Snapshot persistence timestamp (`_stale_ts`) now reflects "last LIVE probe" instead of "last save" (#532) [Enhancement]
- "No data from any enabled provider — OmniGrid could not match this host to <providers>" banner now suppresses... (#533) [Enhancement]

## [1.2.0] — 2026-04-28

Second MINOR cut on top of `1.1.0` — rolls up **118 closed issues** under the 1.2.0 milestone. Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Highlights

- **FIDO2 passkeys as a 2FA factor** alongside TOTP — full enrolment flow, recovery codes, force-2FA toggle from Admin → Users, passkey transports rendered as inline chips.
- **OIDC / SSO end-to-end** — Google + Authentik + generic providers; secure cookie cleanup on every callback path; digest-mismatch + RP-ID hardening on macOS WebAuthn.
- **Real-time event stream** replacing the SPA's polling loops — new `/api/events` SSE endpoint backed by an in-process pub/sub bus; toolbar "Live" pill flips state on connection health; `op:created` / `op:updated` / `cache:invalidated` / `stats:refreshed` / `host:row_updated` and ~10 more events wired through.
- **Logs view + daily-rotated retention** — multi-level filter chips, copy-to-clipboard, configurable retention via Admin → Config, on-disk rotation honors level config at runtime.
- **Beszel / Pulse / Webmin / Portainer provider system** — per-provider chips, mono SVG icons, paused-banner state, drawer overlay surface, master enable toggles per provider.
- **Mobile / responsive overhaul** — no more horizontal page scroll on iPhone, mobile-first toolbars, Toolbar + Nodes header wrap cleanly, mobile topbar phase 1.
- **Notifications system** — 12+ event types wired through Apprise, per-event enable toggles in Admin, dedupe window, force-immediate test button.
- **Schedules & automation** maturity — schedule history view, master schedule enable, per-schedule run history.

### Authentication, passkeys & 2FA

- User force-2FA toggle from Admin → Users table (#114) [Bug]
- Enrolment QR was rendering at full container width (~600-800px on desktop). qrcode-generator SVG has no int... (#115) [Enhancement]
- FIDO2 passkeys as a 2FA factor alongside TOTP (#116) [Enhancement]
- QR rendering bug — TOTP enrolment QR was showing the raw `otpauth://...` URI instead of an actual QR code (#117) [Enhancement]
- The TOTP / 2FA policy section (master toggle + per-role required + lockout window) moves out of Admin → Con... (#119) [Enhancement]
- Button + dirty indicator on Admin → Authentication tab TOTP/2FA section (#121) [Enhancement]
- Profile section icons — About card heading gets `#icon-id-card`; Two-factor card heading swapped its inline... (#128) [Enhancement]
- Six text buttons (`→ readonly` / `Disable` / `Reset pw` / `Disable 2FA` / `Force 2FA` / `Delete`) replaced... (#133) [Enhancement]
- Users table status pills (Active / Disabled / admin / readonly / 2FA On / Off / Required) were rendering co... (#156) [Enhancement]
- Master toggle for passkey enrolment + login (`passkeys_allowed`, default true) (#158) [Enhancement]
- Wraps Duo's `webauthn>=2.0` package via `logic/webauthn_helper.py`; routes `GET /api/me/webauthn`, `POST /a... (#160) [Enhancement]
- Enrolment didn't reliably surface the password-manager save sheet (1Password / Bitwarden / iCloud Keychain) (#161) [Bug]
- Enrolment was failing with `SecurityError` behind NPM. `_request_rp_id` was reading `request.url.hostname`... (#162) [Enhancement]
- Profile → Passkeys card: "Add a passkey" button sat flush against the bottom of the enrolled-keys list with... (#164) [Enhancement]
- Sessions table now shows the 2FA method each session was authenticated with (Password / Password + TOTP / P... (#165) [Enhancement]
- `passkeys_allowed` now returned by `api_get_settings` next to the TOTP-policy fields (#196) [Enhancement]
- 17-enhancement sweep across OIDC / events / metrics / TOTP / Webmin / WebAuthn (#199) [Enhancement]
- Passkey transports rendered as inline chips (#213) [Enhancement]
### OIDC / SSO

- Style mono icons for Admin → Portainer + Admin → OIDC (Authentik) (#150) [Enhancement]
- `/api/oidc/test` now respects the in-flight `verify_tls` checkbox from the OIDC settings form instead of al... (#176) [Bug]
- `_validate_id_token` in `logic/oidc.py` was feeding the unverified id_token header's `alg` straight into Py... (#178) [Bug]
- `_validate_id_token` in `logic/oidc.py` now logs `[oidc] kid=... not in cached jwks (#185) [Enhancement]
- `_validate_id_token._find_key` now rejects `kid is None` instead of silently picking `keys[0]` (#200) [Bug]
- OIDC flow cookie now deleted on every callback failure path via `HTTPException(headers=...)` (#201) [Bug]
- `verify_authentication` now actually performs the sign-counter regression guard the docstring promised (#202) [Bug]
### Real-time / event stream

- SSE pill gains a third "reconnecting" state with amber pulse (#211) [Enhancement]
- Time event stream replacing the SPA's polling loops (#214) [Enhancement]
### Logs view & retention

- Logs view gained a severity multi-select filter (Error / Warning / Success / Info) (#146) [Enhancement]
- Logs on disk + configurable retention (#152) [Enhancement]
- Tab Admin → Logs viewer + new `prune_logs` scheduler kind (#153) [Enhancement]
- Logs → Files tab now renders log files with the same colourisation as the Live tab (#154) [Enhancement]
- `_run_prune_logs` schedule kind in `logic/schedules.py` accepted an admin-supplied `params.days` without a... (#179) [Enhancement]
- `_run_prune_logs` schedule history rows now record the resolved `days` value in `target_name` (`"42 log fil... (#188) [Enhancement]
- `prune_old_logs` cutoff math + filename-date parse now route through a shared `_resolved_tz()` helper s (#195) [Bug]
### Schedules & automation

- `/api/ops` poll cadence is now a tunable (Admin → Config → "Ops poll cadence (ms)"). Backed by `tuning_ops_... (#145) [Enhancement]
- Schedules ("Prune <docker-host>", "Refresh fleet cache") were re-seeding on every container boot even afte... (#159) [Enhancement]
- Rows could get stuck "running" forever after a lifespan cancel mid-run. `fire_schedule()` records `(last_op... (#175) [Bug]
- **Unified topbar refresh cadence** (#206). Replaced the separate SYNC + STATS pickers with ONE control offe... (#206) [Enhancement]
### Notifications

- Notifications admin tab into Notifications + General; per-event notification toggles. `logic/ops.py:notify(... (#107) [Enhancement]
- Notification (#108) [Enhancement]
- When a notification fires for a specific user (per-event opt-in path with `actor_username`), the configured... (#138) [Enhancement]
- Host_paused" notification event fires when `host_metrics_sampler` auto-pauses a host after the configured f... (#142) [Enhancement]
- Success notification title now includes the new version number (#143) [Enhancement]
- `notify(event=...)` was hardcoding the per-event admin-gate default to `True`, but `_NOTIFY_EVENT_DEFAULTS`... (#169) [Enhancement]
### Hosts editor & Host groups

- Subgroup in Admin → Host Groups now scrolls the new row into view + focuses the name input (#113) [Enhancement]
- View: parent group labels now render in `--text-dim` (slightly faded) so sub-group labels stand out as the... (#197) [Enhancement]
- Stale-data badges in the Hosts UI (#216) [Enhancement]
### Drawer, charts & Node Exporter

- `loadHostHistory` now stamps `loadedAt = Date.now()` on every successful HTTP 2xx, regardless of whethe (#100) [Enhancement]
- Chart "?" data-source icons in host drawer (#129) [Enhancement]
- Chart `?` icons now resolve a definitive per-host label instead of a generic "Beszel OR node-exporter" string (#130) [Enhancement]
- Host metric-source tooltip now correctly resolves `cpu` and `load_avg` to node-exporter for NE-only hosts (... (#137) [Bug]
- Tooltip cropped at the host-drawer start edge on left-column metric cards (#141) [Bug]
- Chart in the host drawer for hosts whose Beszel agent emits thermal sensors (e.g (#166) [Enhancement]
- Chart upgraded to multi-line + Y-axis scale (#167) [Enhancement]
- Chart polylines were invisible AND y-axis labels rendered out of bounds (#172) [Enhancement]
- `refreshHostRow` in the SPA leaked stale fields when `/api/hosts/one/{id}` omitted a key (#177). The origin... (#177) [Bug]
- Card legend chips overflowed the chart's right edge on hosts with many thermal sensors (8 cores) (#182) [Enhancement]
- Host cards reported memory as 1024× the real value on Webmin module variants whose `mem_total` / `memory_to... (#190) [Bug]
- Host drawer "Updated Xs ago" label gains absolute-ISO tooltip for Grafana correlation (#212) [Enhancement]
### Stats sampler & metrics infra

- `host_net_sampler` was ignoring the permanent-fail auto-pause. The metrics sampler already skipped paused h... (#151) [Bug]
- `stats_samples` was gaining duplicate rows for the most-recent sample of each item after every container re... (#168) [Enhancement]
- `_HOST_SNAPSHOT_KEYS` whitelist in `logic/gather.py` was dropping real provider-emitted fields, so when a p... (#170) [Bug]
- `_record_failure` in `logic/host_metrics_sampler.py` was sync but reached for `asyncio.get_event_loop()` to... (#180) [Enhancement]
- `resumeHostSampling` force-refreshes immediately after the operator un-pauses a host so the first post- (#181) [Enhancement]
- `_get_host_provider_state` cache key in `main.py` now includes the active-sources tuple. Previously a setti... (#183) [Enhancement]
- `_get_failure_state` (`logic/host_metrics_sampler.py`) docstring cleaned up (#189) [Enhancement]
- Host-snapshots read-side cache (#192) [Bug]
- `_get_failure_state` in `logic/host_metrics_sampler.py` lagged the schema after #189 added `host_failure_st... (#193) [Enhancement]
- _warned_no_mounts` set replaced with a 1024-entry FIFO-evicting `OrderedDict` (#205) [Enhancement]
- StaleAge guard for missing `_stale_ts` (#207) [Enhancement]
### Beszel / Pulse / Webmin / Portainer

- `_flatten_temperatures` was being called THREE times per point in `logic/beszel.py:fetch_system_history` (o... (#184) [Enhancement]
- Fetch_system_history` in `logic/beszel.py` was building the PocketBase filter via f-string interpolation (#191) [Bug]
### Admin & Settings pages

- Admin env-vars-still-set warning banner (#104) [Enhancement]
- Admin → Version copy + the deploy.yml bump-step note. `patch_label` drops the "(CI-managed)" suffix; `patch... (#105) [Enhancement]
- Two-layer scoping (admin global + per-user) (#110) [Enhancement]
- Becomes a settings-sidebar peer of Profile / Ignore list / Language. `settingsSections` gets `{id:'notifica... (#118) [Enhancement]
- Settings-sidebar peer of Profile / Notifications / Ignore list / Language (#120) [Enhancement]
- Save-button copy across admin tabs (#123) [Enhancement]
- Header icons on Admin + Settings views (#124) [Enhancement]
- Intro paragraph ("User accounts, active sessions, and API tokens (#127) [Enhancement]
- Every admin tab's primary heading now renders its matching `adminSections[i].icon` next to the title using... (#131) [Enhancement]
- Users / Sessions / Tokens intro paragraph moved from above the section boxes to below them, so the start of... (#147) [Enhancement]
- Log (Admin → History) now uses server-side paging instead of fetching the whole filtered set up to a 500-ro... (#173) [Enhancement]
- Admin → Config tuning fields client-side integer + bounds validation (#210) [Enhancement]
### Topbar, login & branding

- Topbar widgets card always showed "Unsaved" indicator on page open. `_headerPrefsBaseline` was initialised... (#112) [Bug]
- Logo inside the source chip on the Profile page (#125) [Enhancement]
- Schedules (Scheduled + Queue), and Create-User / Create-Token card headers now carry matching icons consist... (#136) [Enhancement]
- Reload" banner was appending `_v=` to the URL on every click instead of replacing it (URL grew as `?_v=1.1.... (#149) [Enhancement]
- Assertion verifier rejected with "Unexpected client data origin" when NPM rewrites the `Host` header to its... (#163) [Enhancement]
- Every hardcoded English string flagged on the SPA + login page now flows through `t('key.path')` (#174) [Enhancement]
### Vendor icons

- Three returns in `iconUrlFor` plus `hostIconUrl`'s explicit-override path AND keyword-scan path (stack/item... (#215) [Bug]
### Filters, badges & status pills

- Symbol>` dedup on `static/index.html`. 15 unique icons (copy / chevron-right / chevron-down / chevron-up /... (#111) [Enhancement]
- "Unsaved" pill text now flashes subtly on a 2s opacity cycle (1 → 0.55 → 1, ease-in-out) (#122) [Enhancement]
- Both flipped from `pill-success` (bright green) to `pill-ok` (subtle muted-green) (#134) [Enhancement]
- Fail marker for chronically-down hosts (#135) [Enhancement]
- `is_meaningful(False)` returned False because Python's `bool ⊂ int` made `isinstance(False, int)` true and... (#194) [Bug]
- Updates badge on the Stacks nav button (#217) [Enhancement]
### Mobile / responsive UX

- Pinch-zoom is now actually disabled on iOS Safari, not just on Android. iOS Safari deliberately ignores the... (#132) [Enhancement]
### API endpoints & backend helpers

- `/api/hosts/one/{host_id}` now accepts `?force=true` to bypass the 10s provider-state cache, mirroring the... (#101) [Enhancement]
- Test endpoints surface human-readable failure summaries instead of raw upstream stack traces (#103) [Enhancement]
- Version page now edits every component (MAJOR / MINOR / PATCH) and writes the values straight to `VERSION.txt` (#106) [Enhancement]
- Timezone fallback now surfaces in `/api/me`'s `client_config.scheduler_tz` (`{configured, resolved, fallbac... (#186) [Enhancement]
- Passkeys_allowed in api_get_settings (#198) [Enhancement]
### Documentation

- Documentation refresh pass — 5 docs files modified to match the recently-shipped feature waves: PII leak in... (#126) [Bug]
### Internal cleanup, refactor & bug sweeps

- Field error on a filtered-out row no longer silently no-ops. `focusFirstFieldError` in `static/js/app.js` e... (#102) [Bug]
- Startup robustness pass. (a): `seed_stats_cache_from_db` and `seed_nodes_info_from_snapshots` moved into a... (#140) [Bug]
- Tab primary action buttons unified (#157) [Enhancement]
- Dead-code cleanup from (#171) [Bug]
- `_HOST_SNAPSHOT_KEYS` is no longer a hand-maintained tuple drift class. Replaced with an `_is_snapshot_key(... (#187) [Enhancement]
- 10-bug sweep shipped in one batch (#203) [Enhancement]
- Five UX-bugs and five UX-enhancements shipped together (#207–#215). was already fixed via #198 (passkeys_al... (#208) [Enhancement]
### Other improvements & fixes

- Editor: typing `custom_number` into a row + tabbing out no longer reorders the row mid-edit. cn `@input` no... (#109) [Enhancement]
- `host_permanent_fail_window_seconds` was kept as a SettingsIn field, GET-side resolver row, and POST-side v... (#139) [Enhancement]
- Dot flicker on the Hosts view's 15s poll cycle (#144) [Enhancement]
- Notifications event grid: "Host sampling auto-paused" split out of the Security events group into its own "... (#148) [Enhancement]
- Edit modal: `kind` + `cadence_mode` dropdowns weren't preselecting the saved value (the documented Alpine s... (#155) [Bug]
- Pointer on every clickable button (#204) [Enhancement]
- SSH terminal close-code toasts (4400/4401/4402/4403) with origin-mismatch path showing NPM-debug guidance (#209) [Enhancement]

## [1.1.0] — 2026-04-26

First MINOR cut after the `1.0.0` baseline — rolls up **97 closed issues** under the 1.1.0 milestone. Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Highlights

- **Drawer-based host UX**: row-expansion converted to a slide-out drawer with explicit 12-col grid + slide animation; host details, debug panel and SSH-run toggles all live in the new drawer surface.
- **Host historical charts from node-exporter** — Prometheus/Grafana-lite path for NE-only hosts. New chart card grid: CPU/Memory/Disk + Bandwidth + Disk I/O + Load Average (1m/5m/15m).
- **Live xterm.js SSH terminal** in Admin → Hosts (admin-only WSS to a backend asyncssh PTY).
- **Asset API integration** on host rows — model/serial/location autofill button + dirty-state tracking.
- **Schedules infrastructure** — daily / weekly / monthly schedules now actually fire (grace window added).
- **Vendor icons batch** — ~30 new vendor icons (Aqara, ASUS, Alienware, Amazon Fire TV, Bose, Chromecast, HDHomeRun, Humax, J-Tech Digital, Kaonmedia, Nixplay, Samsung family rationalisation, +14-icon brand batch).
- **Admin master toggles** for Apprise / Open-Meteo / Portainer / SSH; child controls disable when the master is off; unified Save + dirty-pill pattern across every Admin tab.
- **Multi-database scaffolding** (laying the groundwork for non-SQLite backends).
- **i18n infrastructure** — `actions.close` and friends, every shipped string now flows through `t()`.

### Hosts editor & Host groups

- Host rows joined against an external asset API for model / serial / location, with autofill button + dirty-... (#3)
- Toggle for host-drawer Debug data panel (#4)
- The first character into a host row's ID collapses the panel (#8)
- Range pre-fill on +Add host group (#11)
- "Collapse all" button visual fix (#14)
- "+ Add sub-group" quick button on parent host groups (#15)
- `+ Add sub-group` parent dropdown didn't reflect the chosen parent (#19)
- Group range error message wasn't showing (#23)
- `Show children` parent dropdown adjustment + Expand all / Collapse all bulk buttons (#27)
- Host drawer polish — explicit 12-col grid with `col-span-6` cards, slide animation switched from Alpine x-t... (#34)
- Hosts count badge on the "Hide hosts without agents" filter (#40)
- Groups + sub-groups now HIDDEN when "Hide hosts without agents" filter is on (preference reversed from #45) (#45)
- Service summary in HOST DRAWER (#64)
- Range filter on host drawer charts now triggers refetch (#66)
- Usage chart in host drawer (Beszel) (#68)
- For the Admin → Hosts editor (122 hosts → 200+ projected) (#72)
- Hosts editor page across reloads / tab nav (#79)
- Only host drawer: Disk I/O + Network charts now distinguish "no activity in window" from "node-exporter doe... (#85)
- Pagination + sticky action bar mirroring the Hosts editor (#86)
- Debug + SSH-run toggles in the host drawer now scroll the just-expanded body to the top of the drawer viewport (#96)
- Hosts / Host groups action bar now matches the editor section's width AND pins correctly to the viewport bo... (#97)

### Drawer, charts & Node Exporter

- View: CPU sparkline invisible on idle nodes (#21)
- Row expansion converted to slide-out drawer (#22)
- Sparks self-diagnostic + `app().statsDebug()` console helper (#35)
- Host historical graphs from node-exporter (Prometheus/Grafana-lite path for NE-only hosts) (#41)
- Mem/Disk chart Alpine errors fixed (SVG <template> doesn't work) (#47)
- Subtitle reflects actual stats picker + polling cadence honors it (#52)
- In + Net Out combined into one chart shipped (#61)
- Disk I/O chart shipped (#62)
- Average chart shipped (1m / 5m / 15m) (#63)
- Bandwidth chart shipped (#65)
- Line chart legend values no longer all-red (#67)
- Theme + hotkeys pushed down by stats picker (#73)
- I/O chart hidden for NE-only hosts (#77)
- Only host Disk I/O chart now populates from `node_disk_{read,written}_bytes_total` counters (#78)
- Drawer "No NIC activity" hint now branches on whether node-exporter is in play, not on whether Beszel is ma... (#80)
- Only host Disk I/O chart was stuck on perpetual `0 B/s` for NAS / RAID boxes (Synology, TrueNAS, OPNsense).... (#82)
- Disk I/O support for NE-only hosts (#89). `parse_disk_counters` now falls back to `node_devstat_bytes_tota... (#89)
- Drawer charts now show a subtle `Updated Xs/m/h ago` freshness hint beside the time-range picker (#95)

### Admin pages: Apprise / Open-Meteo / Portainer / SSH / Debug / Sessions

- Admin-only xterm.js viewport over WSS to a backend asyncssh PTY (#2)
- Debug-panel toggle removed from Admin → Hosts (#10)
- Service "enabled" master switches for Apprise, Open-Meteo, Portainer, SSH (#13)
- Admin → all tabs — master-toggle treatment unified: child controls disable when the master is off; Apprise... (#18)
- Admin tabs use Save button + show "Unsaved" indicator (Apprise / Open-Meteo / Portainer / SSH) (#20)
- Api/items` 500 scope bug (#37)
- Inventory dirty pill unified with other admin tabs (#49)
- 4 admin-tab dirty flags unified to smart-getter pattern (#50)
- Meteo Save button moved below the URL input (#51)
- Admin → Config tab — UI override for the 6 process-level tunables (#76)
- Admin → Debug tab: smart-getter dirty pattern + Save button (#81)
- _format_provider_test_summary()` in `main.py` keeps the Pulse + Beszel test-connection response shape ident... (#91)

### Schedules

- Daily / weekly / monthly schedules now actually fire (grace window added (#16)
- weekly npm audit + node_modules served via allowlist (was wildcard mount) (#55)

### Topbar, login & branding

- Topbar split into two rows (Option A) (#7)
- Clock + weather repositioned LEFT of the user avatar (#9)
- Brand icons batch — 14 new icons + keyword wiring (#32)
- Humax brand icon added (#42)
- Clean wordmark for `samsung`, corporate mark to `samsung-electronics` (#43)
- Kaonmedia brand icon added (#44)
- Header "Update stack" button hides when stack is expanded (#46)
- Mobile topbar phase 1 — no more horizontal page scroll on iPhone (#56)
- Toolbar + Nodes header wrap cleanly on mobile (#57)
- Topbar widgets prefs follow the dirty-pattern (no auto-save on toggle) (#59)
- Avatar lifts up to row 1, clock+weather take their own row (#60)
- Utility belt merged into header flow + language above SYNC (#70)
- Page logo no longer shows a white halo at the rounded corners (#93). `static/login.html` swapped from the... (#93)

### Vendor icons

- ~30 new vendor icons added across multiple batches (Aqara, ASUS, Alienware, Amazon Fire TV, Bose, Chromecas... (#6)
- HDHomeRun + J-Tech Digital + Nixplay (#71)

### Documentation

- `CHANGELOG.md` (this file) at the repo root, in Keep-a-Changelog format, with `[Unreleased]` + `[1.0.0]` ba... (#88)
- `README.md` ref updated from `notes/note_authentik.txt` to `notes/guidelines/authentik.md` (#92)
- Operator-private hostnames in shipped docs and code comments with example.com placeholders (#94)

### Filters, badges & status pills

- Paused"` status now correctly maps to `"down"` (#38)
- Colour cleanly + always show "0 failed" (#74)
- Filter bar (Stacks / Services / Nodes views): the divider between the health and status filter groups no lo... (#84)

### Internationalisation & translations

- `actions.close` i18n key (#29)

### Database / migrations / data

- Type ShortName field name confirmed + backend exposes `type_short` (#26)
- schema-migration infrastructure (logic/migrations.py) (#54)
- User UI prefs sync (cross-device) (#58)
- Scaffolding for multi-database support (#75)

### Internal / refactor / code review

- Host Groups editor — collapsible children, NUMBER input moved to the natural Tab-order column, group headin... (#5)
- Signature-based dedupe (#12)
- Short detection widened + diagnostic added (#17)
- Reverts/cleanup follow-ups from this session (#24)
- Fix turn from the code-review report (#31)
- **Code-review compliance batches** (closed all of (#33)
- surface SESSION_SECRET-auto-generated warning to admins (#53)
- Fresh full-code-review pass (#83)
- Model switched back to SemVer `MAJOR.MINOR.PATCH` after a brief stint with the `MAJOR.MINOR`-only model (#87)

### Other improvements & fixes

- `hostStatsSourceEnabled()` field name typo (#1)
- Provider outages no longer blank the page (#25)
- The `_deriveTypeShort` JS acronym fallback (#28)
- Block agent-memory paths under `static/` (#30)
- Text-compaction fix (img_3.png) (#36)
- Filter Docker / k8s / Proxmox internal interfaces behind a toggle (#39)
- Password field is not contained in a form" warning silenced (#48)
- Paginate + add per-system match diagnostic (#69)
- _load_curated_hosts` between the two NE samplers (#90)

## [1.0.0] — 2026-03-21

Baseline release — first version under the SemVer + `CHANGELOG.md`
cadence (see `docs/RELEASE_PROCESS.md`). The changelog story starts
here.

<!-- Version link references — root-relative paths (start with `/`).

 Both git-host markdown renderers rewrite links starting with `/` as paths relative to the REPO root, not the host root. So `/releases/tag/v1.2.0` resolves to `https://<host>/<owner>/<repo>/releases/tag/v1.2.0` on either platform — same source line works on both hosts. No operator-specific domain or username baked in (privacy rule satisfied), no `..`-count to tune per renderer (the previous fix attempts in #507/#512/#513 chased this for several rounds).

 Why not relative paths: one git host uses `<host>/<owner>/<repo>/blob/<branch>/` (4 segments before the file) so 2 `..` resolves correctly; another uses `.../src/branch/<branch>/` (5 segments) AND its renderer can drop `..` traversal that would escape the file's directory. No `..`-count satisfies both. Root-relative sidesteps this entirely.

 We don't have a v1.0.0 release tag (no `[1.0.0]` link target on purpose); the heading above renders as `## [1.0.0]` text, which is fine. The `[Unreleased]` link points at the milestones view since no release page exists yet.
-->
[Unreleased]: /milestones
[1.1.0]:../../releases/tag/v1.1.0
[1.2.0]:../../releases/tag/v1.2.0
[1.3.0]:../../releases/tag/v1.3.0
