# Changelog

All notable changes to OmniGrid land here. Format adheres to
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Cadence (see `docs/RELEASE_PROCESS.md` for the full release runbook):

- **`PATCH`** — CI bumps automatically on every successful deploy (one per shipped TODO item). The accumulating count between releases is the "is it time to cut a release" signal.
- **`MINOR`** — manually cut. When a batch of PATCH-shipped items feels release-worthy, `MINOR` is hand-edited on the server (which resets `PATCH` to `0`) and a new `[X.Y.0]` section is written here listing the items that landed since the last MINOR.
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

Items shipped to the live deploy via the daily PATCH cadence that are
not yet rolled into a tagged `MINOR` release. The next MINOR cut renames
this whole block to `[X.Y.0]` and adds a fresh empty `[Unreleased]` above.

### Added

- Stack-update confirm dialog now shows a blast-radius preview. The "Update stack" SweetAlert body inlines a new `.blast-radius-block` listing every service / container / orphan in the stack — with a per-row type chip + `update available` indicator + a one-line summary counting `services × replicas, containers, orphans, total`. Composed from the already-cached `stack.items` array (no extra fetch). Operators see "this will touch 12 items: 5 services × 8 replicas, 4 containers, 0 orphans" BEFORE confirming, instead of clicking Confirm and finding out the stack was bigger than they thought. The full interactive stack dependency-graph view (cross-stack edges via network / volume / service-affinity) remains a deferred follow-up; this MVP delivers the highest-value half — the blast-radius preview — at near-zero implementation cost.
- History row detail — new "Diagnose with AI" button on error rows (status=error OR non-empty error field) AND when AI is enabled per `aiSidebarSurfaceEnabled()`. Click opens the AI sidebar pre-loaded with the row's structured context (op_type / target / stack / status / when / duration / actor / error text + last 8 parsed events) so the AI can suggest root-cause + remediation without the operator typing context. Uses the existing AI palette + sidebar pipeline (turns persist to `ui_prefs.ai_conversation`); zero new endpoints, zero schema changes. First third of the "Settings audit + diff workflows" trifecta — the remaining Settings-audit + inline-Settings-diff parts are filed as a follow-up because they require a new `settings_audit(ts, actor, field, old, new)` schema + dedicated Admin sub-tab.
- Notifications popup — new "Group similar" toggle in the filter bar pivots the visible page's rows by `(event, target_kind, target_id)` so a busy fleet's wall of identical "service restarted" / "host paused" entries collapses to one cluster row per (event, target) with a `×N` count badge + "Spanning Xm ago → Ym ago" range. Each cluster row carries a chevron to expand the children inline (smaller font + indented border), a "Mark cluster read" button that bulk-marks every unread child via the existing `/api/notifications/{id}/read` endpoint (SSE `notification:read` still fires per row so other tabs reconcile cleanly), and severity = highest-severity-in-cluster so one error in a sea of info surfaces visually. Toggle persists to `ui_prefs.notifications_group_similar` (via `PATCH /api/me/ui-prefs`'s standard `{prefs: {...}}` envelope) so the operator's choice follows them across browsers / machines. Grouping is page-scoped (current `/api/notifications` response only); full cross-page clustering with a server-side `?cluster=true` mode is the deferred follow-up. Zero new endpoints, zero schema changes.
- Drift-from-baseline indicator on every Hosts row. Each curated host's CPU%, Memory%, Disk% and Ping RTT now compare against a 30-day rolling baseline (median ± interquartile range) computed once per hour by a new lifespan-managed sampler. A small ▲ / ▼ / ━ chip lands next to the percent label — red ▲ when the live value exceeds median + IQR (potentially over-utilised), indigo ▼ when below median − IQR (under-utilised vs the host's own normal range), slate ━ when within. Hover-title carries the localised baseline detail (current value, 30-day median, ±IQR, sample count). No chip rendered for hosts with fewer than 50 samples in the window OR a degenerate IQR (all samples identical) — operators see a chip only when the baseline is statistically meaningful. New schema `host_baselines(host_id, metric, median, iqr, sample_count, computed_ts)` (additive `CREATE TABLE IF NOT EXISTS`), new module `logic/host_baseline.py` with `compute_baselines` / `load_baselines` / `drift_indicator` / `host_drift_for_api`, new sampler `logic/host_baseline_sampler.py` (hourly cadence + 60s startup delay), wired through `_merge_one_host` + `_shape_host_api_row` + the `CURATED_REFRESH_FIELDS` overlay. New `--pill-info-bg/br/fg` token family declared on both dark + light :root for the ▼ tone. Per-metric historical drift modal (click the chip) is the deferred follow-up; MVP carries only the chip strip + hover-title.
- Stats → Samples now includes a daily-totals bar chart over the last 90 days. Sums every sample-bearing table's row count per day; chart renders after the per-table breakdown with `<hr>` separators around the breakdown for visual rhythm. New backend field `daily_totals: [{date, total}, ...]` on `/api/admin/stats/samples` (computed once per request via `strftime('%Y-%m-%d', ts, 'unixepoch')` per table, summed across tables). Pure-CSS bar chart (new `.samples-daily-chart` rules) — hover reveals date + count via `:title`.
- Stats → AI Cost — Response time trend section now shows a bar chart above the existing table. Reuses the `.samples-daily-chart` CSS family; hover shows `<date>: <avg_ms> · <jobs>`. The accompanying table is now sorted day-DESC (newest at top).
- Dashboard tile format — Users tile now renders as `active/total` (e.g. `2/3`); Schedules tile renders as `enabled/total` (e.g. `7/7`). Sub-line carries the descriptive tail.

- Stats section in the user-avatar dropdown — admin-only quick-insight Dashboard page. New top-level view (`/stats`) parallel to Admin, sharing the same `.page-layout` shape (mobile select + desktop sidebar + content area) for visual consistency. First sub-page is **Dashboard** with fourteen quick-insight cards: Users (total / active / admin count), Active sessions, Host-stats providers (enabled / disabled split with chip pills per provider — beszel / pulse / node_exporter / webmin / snmp / ping), Curated hosts (total / enabled in inventory), Host groups, Asset inventory, Swarm nodes, Stacks, Services, Containers (service replicas + standalone / orphan), Schedules (total / enabled), Backups (SQLite + avatars snapshots), Config backups (settings-as-code JSON snapshots), Tunables (total + overridden-from-default count). Each card click-jumps to the matching admin sub-tab or top-level view (Stacks / Services / Nodes click direct to those views). Backend endpoint `GET /api/admin/stats/overview` returns everything in one fetch; counts are read from existing in-memory `_cache` (no Portainer round-trip), the `host_groups` JSON setting, `logic.backups.list_backups`, `logic.config_export.list_snapshots`, `logic.schedules.list_schedules`, and `logic.tuning.TUNABLES`. CSV-controlled host-stats providers consume `active_host_stats_providers()`, per-host opt-in providers (snmp / ping) are reported as enabled iff at least one curated row turns them on. New `stats.*` i18n block. New `icon-cpu` + `icon-database` sprite symbols. The menu entry is gated on `isAdmin()` — non-admins never see the launcher; backend endpoint also gates on `require_admin`.

### Changed

- Host-row sparklines (Hosts view mobile cards + desktop table CPU / Mem / Disk cells) now match the width of the stat-bar above them and carry stronger visual weight. New `.spark--host` CSS modifier overrides the legacy 80px width cap (kept for stacks / services rows where the narrow form fits alongside an item name), bumps height from 10px → 18px, increases stroke-width from 1 → 1.5px, and adds rounded line-caps + line-joins for cleaner trend lines. Each sparkline now renders TWO `<path>` elements — a soft tinted area-fill under the trend line (currentColor at 16% opacity, gap-aware: NaN-interrupted series get per-run filled regions rather than one smeared polygon) PLUS the existing stroke. New SPA helper `hostInlineSparklineArea(h, metric)` mirrors `hostInlineSparkline` and emits the closed-to-baseline path. Drift-from-baseline chips (▲ / ▼ / ━) now reliably sit INLINE with the percent label on mobile cards — the prior `.host-mobile-card-metric .value { display: block }` rule forced the chip onto its own row, which surfaced as a stray red triangle below the Memory bytes label. The chip itself was redesigned: pill-shaped (border-radius 999px), font-size bumped from `--fs-3xs` to `--fs-2xs` with bold weight, min-width 18px so ▲ / ▼ / ━ stay visually consistent regardless of glyph width.
- Admin → AI Integration dashboard popups — pagination at 20 rows per page + click-to-sort headers across every modal (Jobs / Cost / Tokens / Response time / Accuracy / Pass rate). JOBS uses server-side pagination via the existing `/api/admin/ai/jobs?limit=20&offset=N` contract; trend modals (Cost / Tokens / Response time / Accuracy / Pass rate) page client-side because the bucketed dataset from `/api/admin/ai/dashboard` is bounded (≤720 rows on the 30d window). Every `<th>` is now a clickable `<button>` (keyboard accessible — Tab + Enter to sort) with a unicode `▲` / `▼` indicator on the active column; `aiSortBy(col)` toggles asc/desc on repeat clicks and resets to page 1. Paginator footer shows "Page X of Y" + Prev / Next; hidden when there's at most one page. Filter changes (provider / status) reset to page 1. Page + sort state resets on every modal open so navigating between tiles doesn't carry one modal's last-page through to the next. Stable mixed-type comparator handles numeric / string / null fields; nulls sink to the bottom regardless of direction. **Follow-up patch — Prev/Next i18n + page size:** the paginator buttons rendered the literal "actions.prev" / "actions.next" because the original entry claimed those keys existed under `actions.*` when they actually live under `pagination.*`. Switched both call sites in `static/_partials/admin/ai.html` to `t('pagination.prev')` / `t('pagination.next')` to consume the existing keys rather than duplicating them. Same change reduces page size from 25 to 20: `aiModalPageSize` default flipped in `static/js/app.js` plus the three `Number(...) || 25` defensive fallbacks in `loadAiJobs` / `aiTrendRows` / `aiJobsRows` flipped to `|| 20`.
- Host drawer charts — X-axis flips to MMM-d (e.g. `May 8`) when the 7d range is selected; HH:MM otherwise. Threshold ≥ 48h so 1h / 6h / 24h all keep HH:MM. `Intl.DateTimeFormat` honours the browser locale for month abbreviation. Single source of truth: SNMP-specific `_snmpFmtAxisTime` consolidated into the canonical `_fmtAxisTime` so every chart card in the drawer (Beszel / NE / Pulse / Webmin / Ping / SNMP host / SNMP per-iface / SNMP per-temp probe) routes its X-axis label formatting through ONE helper.
- Admin → AI Memory — branded "Memory" with capital M in every operator-visible string (title, add / delete labels, empty state, confirms). Delete button restyled to match the rest of the admin section's destructive-affordance pattern: icon-led `btn btn-ghost text-danger` with `icon-trash` to the left of the label. **Follow-up patch:** delete button restyled again to the `.user-action-btn user-action-btn--danger` family (26×26 icon-only red trash square) once Schedules / Users / Backups had standardised on the same icon-only shape, so the visual treatment lands cross-tab consistently.
- Admin → Config Backup retention count — gained a section-owned Save button with dirty/undirty pulse (amber ring + Unsaved pulse-dot) plus the long-form description from the existing `admin.config.fields.tuning_config_backup_retention_count.help` i18n key. Pre-fix the input had bounds chips and an effective-value caption but no Save button; typed values were lost on navigation. Section-owned save handlers (`_configBackupSectionTuningKeys` / `configBackupSectionDirty` / `saveConfigBackupSection`) mirror the Port Scan / SNMP / AI section pattern.
- Admin reload buttons across Users / Sessions / API tokens / Schedules / Schedule queue / Backups / Logs (live tail + log files) / Config Backup now render with a refresh icon and spin while their loader is running. New shared SPA-state registry `_loadBusy[<key>]` + `_runWithBusy(key, fn)` helper guard against double-fires (a fast click can't start a second concurrent fetch) and drive both the spinning-icon class and the button's `:disabled` attribute. Buttons keep the "Reload" text; the icon sits to its left, gap `1.5`. One source of truth for the icon (`icon-refresh-cw`) used everywhere. **Follow-up patch:** dropped the `:disabled="_loadBusy.<key>"` binding from all 9 reload buttons after a user-flagged edge case where the busy flag stayed truthy whenever the underlying Promise hung (network blip, dead probe, slow listing) — a transient hang turned into a persistently-disabled button across the session. The `_runWithBusy` helper's `if (this._loadBusy[key]) return;` early-exit already prevents concurrent fires from a double-click; the disabled binding was defensive overkill. The spinner-icon class stays as the "we're working" feedback. Config Backup's `configBackupBusy` gate is retained (it's the in-flight save lock, not the load-busy registry).
- Logs admin tab — Copy and Clear buttons gain icons (`icon-copy` and `icon-trash` respectively) to match the rest of the admin section's icon-led affordances. Both buttons keep the existing label + i18n + disabled-state contract; the destructive-tone Clear button keeps `text-danger`. **Follow-up patch:** restyled to the `.user-action-btn` family (`user-action-btn` for Copy, `user-action-btn user-action-btn--danger` for Clear) so they collapse to 26×26 icon-only squares matching the Schedules / Users / Backups action buttons. Header strip now reads Reload | search-input | severity-filter | Copy-icon | Clear-icon instead of Reload | Copy with text | Clear with text. `:title` + `:aria-label` retain the localised labels for mouse-hover + screen-reader access; Copy's `:disabled="!filteredLogLines().length"` gate preserved.
- Brand icons refreshed for `5g` and `ftth` host slugs — both `static/img/icons/5g.svg` and `static/img/icons/ftth.svg` now carry the new marks supplied from the public icon CDN, embedded as base64 inside SVG wrappers per the brand-icon onboarding rule for PNG-only assets. Filenames + slugs unchanged so the existing keyword-scan resolver routes hosts labelled `5g` / `ftth` to the new marks without HTML / JS wiring.

### Security

- Severed user-controlled string flow into the `/api/admin/config-backup/saved/{name}` family (download / restore / delete) to clear a CodeQL `py/path-injection` alert. Same drift class as the avatar fix earlier this cycle: `_safe_path` in `logic/config_export.py` already enforced regex allow + `os.path.realpath` confinement, but CodeQL's interprocedural taint tracker doesn't accept that pattern. Refactored to parse `name` via the canonical regex `^(?P<prefix>config|omnigrid-config)_(\d{4})\.(\d{2})\.(\d{2})_(\d{2})\.(\d{2})\.(\d{2})\.json$` — closed alternation for the prefix + six int captures for the timestamp — then rebuild the filename via f-string from those PRIMITIVES (`int()` conversion is the canonical taint-stripper) before joining under `CONFIG_BACKUP_DIR`. `list_snapshots` also tightened to gate on the strict regex so the UI never surfaces a file the action endpoints would reject. The previous regex + realpath confinement was semantically correct; the primitive-rebuild pattern is what CodeQL recognises.
- Severed user-controlled string flow into the `/api/avatars/{fname}` path-construction expression to clear a CodeQL `py/path-injection` alert. New sanitizer `_avatar_path_from_fname` parses the URL segment via the strict canonical regex `^u(\d+)\.(png|jpg|jpeg|gif|webp)$` into PRIMITIVES (int user_id + allowlisted ext) and rebuilds the path from those — `int()` conversion is the canonical taint-stripper for numeric operator input, and the ext is drawn from a closed regex-alternation set. The four-layer `_safe_avatar_path` helper (regex + basename + commonpath) was already semantically correct but CodeQL's interprocedural taint tracker didn't fully accept it; the primitive-rebuild path is the canonical CodeQL-recognised sanitizer pattern. `_safe_avatar_path` is kept for the DB-stored-path use case in `api_clear_avatar`.

### Fixed

- AI conversation history not preserved across different computers for the same login. The async `persistAiConversation` mapping was missing the `ts` field on each turn (the sync `persistAiConversationSync` variant had it). The Clear button stamps `ai_conversation_cleared_at` on `ui_prefs`; hydration filters by `Number(t.ts) > cutoff`, so without `ts` the filter evaluated `Number(undefined) === NaN`, `NaN > cutoff === false`, and every turn got dropped on cross-device login. Two fixes: (a) added `ts: t.ts || null` to the async-persist map so future PATCHes carry the field; (b) defensive — the hydration filter now treats turns with missing-or-zero ts as legacy pre-fix records and PRESERVES them rather than dropping them, so users with existing missing-ts data in the DB get cross-device history immediately rather than only after typing a new turn first.
- Container recreate (`_do_update_container`) was failing with `HTTP 400 {"message":"Invalid request payload","details":"EOF"}` against newer Portainer versions because the POST to `/containers/{id}/recreate?PullImage=true` sent no request body. Portainer's body-parser EOFs before reading anything when no JSON is provided. Fix: pass `json={}` to the httpx POST so a valid empty-JSON body is sent (httpx auto-sets `Content-Length` + `Content-Type: application/json`). The `?PullImage=true` query param continues to drive the actual recreate behaviour; the body is otherwise unused. Manager- and worker-resident standalone containers both benefit (worker also needed the agent-target routing fix that landed alongside the snapshot-on-worker-node fix).
- SNMP UI gates now consistent across the drawer "Enabled agents" chip, the bar-capability fallbacks, and the chart-mount predicate — all consume the same `_snmpHasProbeTarget` helper (strict opt-in `snmp_enabled === true` AND a resolvable target via `snmp_name` OR the canonical per-host `address` field). Pre-fix: drawer chip required `snmp_name && snmp_enabled === true` (too strict — hid SNMP chip on hosts opted in via `address` only); CPU / mem capability fallbacks used `snmp_name || snmp_enabled === true` (too loose — rendered bars for snmp_name-only hosts the operator never opted in to). Symptom: APC UPS with SNMP ticked + address set but no snmp_name showed only the Ping chip in the "Enabled agents" card despite SNMP actively probing.
- Standalone (non-Swarm) containers running on Swarm worker nodes — `status=unknown` regression. The container-loop image-inspect call in `_gather()` (`logic/gather.py`) ran without an `agent_target`, so for a `docker run` container on a worker (e.g. a sidecar agent on a worker node), the inspect routed to the manager's Docker daemon — which doesn't have the worker-only image, returns 404 — exception caught + silently passed, `current_digest` stayed None, and the status-derivation chain labelled the row "unknown" forever despite the remote-digest probe succeeding cleanly. Same `X-PortainerAgent-Target` rule that gates `_do_update_container` / `_do_restart_container` / `_do_remove_container`. Fix: moved the existing 4-tier node-resolution chain (`com.docker.swarm.node.id` label → Swarm task-id → per-node container sweep → `"local"`) ABOVE the image-inspect call and threaded `agent_target=node_name` into `portainer.pg(...)`. Cleans up worker-resident plain compose containers fleet-wide.
- Host-drawer "Showing cached data" banner — doubled "Last live update Last live data 4s ago — value restored from cache snapshot ago." text. The banner template `host_drawer.stale.body` substitutes `{age}` into `Last live update {age} ago. {count} field(s) restored…`, but the caller passed `staleAge(h)` — the **tooltip-surface** helper that already wraps the time in the full i18n sentence `Last live data {age} ago — value restored from cache snapshot`. The outer template's wrapping then double-wrapped the inner sentence. Fixed by adding a sibling helper `staleAgeShort(h)` that returns the bare relative-time string (e.g. `4s`) for inline substitution into larger templates; banner caller switched to it. `staleAge(h)` is unchanged — widely used as a tooltip surface where the full sentence is correct.
- Phantom stale fields on hosts whose providers can't produce them — e.g. `host_cpu_percent` and `host_ping_loss_pct` rendering "stale" forever on an APC UPS that has neither a CPU% surface nor ping enabled. Root cause: `apply_host_snapshot_fallback` restores any meaningful snapshot value when the live merged dict lacks it; `save_host_snapshots` then re-persists the restored value (the underscore strip only drops `_*` bookkeeping keys, not the data values). Result: an orphan field that was once briefly populated cycles forever — every gather restores, every save re-persists, the loop never breaks. Fix: per-field stale grace cap. `apply_host_snapshot_fallback` now persists a `_meta_stale_since` map (round-trips through the snapshot — save filter changed to keep `_meta_*` keys) tracking when each stale field FIRST went stale. When `now - first_stale_ts > grace_window`, the field is dropped from BOTH the merged dict AND the next saved snapshot, so the orphan decays naturally. Default 24h covers transient outages (provider down for hours, operator wants to see last value) without keeping orphans forever. Same grace check applied to `seed_nodes_info_from_snapshots` so the first paint after a container restart matches the gather-time semantics. New tunable `tuning_host_snapshot_stale_field_max_age_hours` (env `HOST_SNAPSHOT_STALE_FIELD_MAX_AGE_HOURS`, default 24, range 1..720h) surfaced in Admin → Config and documented in `docs/guidelines/env_example.md`. **Follow-up patch — Hosts-row bar gates honour stale flags.** Even with the snapshot grace cap shipping, the same APC UPS still showed a stale CPU% bar on the Hosts page row that never updated, because `hostHasCpuMetric` / `hostHasMemMetric` / `hostHasDiskMetric` use a value-first capability check (`if (Number(h.cpu_percent) > 0) return true`) that trips on the snapshot-restored stale value. `.stale` dim styling is correctly applied but the bar still renders forever with a value that can't recover. Gates now check `isStaleField(h, '<key>')` BEFORE the value-based shortcut: when the field is in `_stale_fields`, skip the value test and fall through to the SNMP-history capability check (`_hostHasFiniteSnmpHistory`). For an APC UPS — no `hrProcessorLoad` in the SNMP walk → SNMP history all-NULL → gate returns false → bar HIDDEN cleanly. For a Beszel-tracked Linux host with a transient provider outage, `_hostUnixAgent` short-circuits to true at the top of each gate so the bar still renders dim-stale during recovery — recoverable-outage UX preserved. Fix applies to both desktop table and mobile card render paths (both gate via the same helpers).

### Added

- Settings-as-Code admin tab + `config_backup` schedule kind. New "Admin → Config backup" surface lets operators export every operator-tunable setting (settings KV + schedules + ai_memory) as a single human-readable JSON file for git-based change tracking, then re-apply on a fresh deploy. Secrets (api keys / passwords / tokens / private keys) are redacted to the literal sentinel `"__OMITTED__"`; on import, redacted entries are skipped so live secret material is preserved. Three sections: Export (Download as JSON / Save to disk now), Import (file picker + warning + confirm), Saved snapshots (table with Download / Restore / Delete). Backend module `logic/config_export.py` provides the snapshot + apply primitives; eight new admin-only endpoints under `/api/admin/config-backup/*`. New schedule kind `config_backup` runs the same save-to-disk path on a cadence; retention via the new tunable `tuning_config_backup_retention_count` (default 30, 0 = unlimited).
- AI palette can now create / update / delete schedules. Three new actions (`schedule_create` / `schedule_update` / `schedule_delete`) added to `ALLOWED_PALETTE_ACTIONS`; each takes a structured `ACTION_DATA: {<json>}` directive carrying the schedule payload. Create + update fire immediately (non-destructive); delete is destructive and gates behind the AI sidebar's inline-confirm chip (no popup). All three reuse the existing `/api/schedules` CRUD endpoints — same backend bounds-clamping + skip-if-running gates apply. New parser `parse_palette_action_data(text)` validates JSON; main.py shaper threads `action_data` through to the SPA. Frontend gains a single dispatch helper `_aiScheduleDispatch(op, opts)` plus 12 new alias-map entries covering operator phrasings ("create schedule", "modify schedule", "delete the experimental-prune schedule", etc.).
- AI palette can now dispatch the new "Switch to tag…" flow — operator says "switch komodo-core to :2" in the AI sidebar (or modal Cmd-K palette) → AI replies with `ACTION: retag_image` + `ACTION_TAG: 2` + `ACTION_ITEM: komodo-core`; inline-confirm chip appears in the sidebar (no popup) and a click on Yes fires the same backend retag flow the drawer's inline popover uses. Backend: `retag_image` added to `ALLOWED_PALETTE_ACTIONS`; new `parse_palette_action_tag` + `parse_palette_action_item` parsers; prompt teaching block under AVAILABLE ACTIONS. Frontend: new `retag-image` descriptor + `_aiRetagDispatch` helper resolving the target via `ACTION_ITEM` then the open item drawer; new alias map entries (`retag_image` / `switch_tag` / `pin_to_tag` / `change_tag` / `track_tag`); `_runCommandPaletteAction` + `confirmInlineAction` thread the `tag` + `actionItem` params through; persist / hydrate carry them on each turn so reload preserves them.
- "Switch to :latest" generalised to "🏷 Switch to tag…" — accepts any valid Docker tag (e.g. `:latest`, `:2`, `:v2-stable`) instead of being hardcoded to `:latest`. Operator's flagship case: a container deployed as `:2.0.0-dev` (frozen snapshot) can now be retagged to `:2` to track the moving v2 line for patch updates without bumping to `:latest` (which on some images still tracks an older major). Backend (`logic/ops.py` + `main.py`): new `new_tag` parameter threaded through every retag helper + endpoint; new `ContainerRetagIn` Pydantic model; `_validate_retag_tag` sanitises against the Docker tag charset (OCI spec); empty input defaults to `latest` (back-compat). Frontend: replaces the SweetAlert-modal flow with an inline popover anchored to the button itself — small panel slides in below the action button with a pre-focused input, current image shown for reference, Esc / outside-click to dismiss, Enter to submit. `canRetagToLatest` gate loosened so the affordance accepts both "pin upward" and "pin downward" moves (e.g. `:latest → :2`). New CSS rule `.retag-popover` (+ child rules); new i18n keys under `drawer.retag_tag*` and `toasts.retag_already_target` / `retag_queued` / `retag_invalid_tag`.
- Hosts-page header — Portainer "Open" button alongside the existing Beszel + Pulse buttons. Same visual pattern (brand icon + label + arrow-up-right). Gated on BOTH `settings.portainer_enabled === true` AND `settings.portainer_public_url` being set, so the button hides cleanly when Portainer is disabled OR when no public URL is configured. Theme-aware icon swap via `_themeIcon` (`portainer.svg` / `portainer-dark.svg`). New i18n keys `hosts_extra.open_portainer` / `_title`.
- Port-scan chips now flag asset-inventory mismatches — when asset inventory has a record for a host with port definitions, detected ports that aren't in the asset's port list render with a small round-exclamation icon (the existing `#icon-info` sprite — visually a `!` shape) prepended to the chip label. Icon inherits the chip's `currentColor` so it visually matches the pill. New SPA helper `portScanShouldFlag(host, port)`; `portScanChipTitle` appends a 2nd-line "this port is not in the asset inventory's port list" tooltip when flagged. Loose match by port number (asset's protocol field is informational). Suppressed when the asset record has zero port definitions (nothing to compare against). New i18n key `host_drawer.port_scan.chip_asset_mismatch_title`.
- RTSP (554) and RTMP (1935) added to the port-scan provider's `DEFAULT_PORTS` tuple AND `_PORT_HINTS` mapping, so a fresh scan on a host running an RTSP camera or RTMP streaming server surfaces the chip with its service hint already populated.
- Public pre-built images on GHCR — OmniGrid is now published to `ghcr.io/maraouf/omnigrid` so anyone can pull a known release without rebuilding from source. Tag layout: `:latest` floats to the newest minor; `:<MAJOR>.<MINOR>` (e.g. `:1.4`) floats to the newest minor on that major line; `:<MAJOR>.<MINOR>.0` is the immutable cut-day MINOR pin (use for rollbacks). Publish trigger is the `.github/workflows/publish-ghcr.yml` workflow firing only on tags matching `v<MAJOR>.<MINOR>.0` — daily auto-PATCH churn (`.1` onwards) stays in the maintainer-private registry and would clutter the public package list. Package is public; `docker pull` works anonymously, no `docker login` needed for read access. Compose / Swarm wiring: `export OMNIGRID_IMAGE=ghcr.io/maraouf/omnigrid:latest` before `docker stack deploy --resolve-image=always`. README's Deploy section gains a new "Pull a pre-built image (no build step)" subsection; full publish-trigger contract + tag matrix lives in `docs/guidelines/deploy.md` under "Pre-built images on GHCR".

### Changed

- Host-drawer port-scan detected-ports list now sorts by port number ascending across both protocols (TCP + UDP interleave) instead of the backend's response order, so the chip strip reads as a numeric sequence (`22/tcp · 53/udp · 80/tcp · 443/tcp · …`) for at-a-glance scanning. Stable tie-break on protocol (TCP before UDP for duplicate port numbers — rare but possible, e.g. dual-stack DNS on 53). UDP chips render with the `pill-info` (blue) fill colour — distinct from TCP's green/amber so the protocol is visually obvious at a glance. UDP rolls up to ONE colour because the goal is "make UDP visually distinct from TCP"; the curated/unknown signal for UDP is carried by the tooltip + the asset-mismatch icon (when asset inventory is enabled). New SPA helper `sortedDetectedPorts(host)`; `portScanChipClass(host, port)` short-circuits on UDP to return `pill-info` regardless of curated state, falling through to the pill-ok / pill-warning branch for TCP.
- Brand icons refreshed for `5g` and `ftth` host slugs — both `static/img/icons/5g.svg` and `static/img/icons/ftth.svg` now carry the new marks supplied from the public icon CDN, embedded as base64 inside SVG wrappers per the brand-icon onboarding rule for PNG-only assets. Filenames + slugs unchanged so the existing keyword-scan resolver routes hosts labelled `5g` / `ftth` to the new marks without any HTML / JS wiring.

### Added

- Profile → Topbar widgets weather section — new °C / °F unit preference. The topbar weather chip + AI palette context all respected °C only; operators in °F locales had to do mental conversion. New `ui_prefs.weather_unit` field (`'c'` | `'f'`, default `'c'`) hydrated from `localStorage.headerWeatherUnit` for fast first paint with cross-device sync via `/api/me/ui-prefs`. Radio buttons render under the existing lat/lon/label grid (full-row spanning under the three-column block on desktop; stacks naturally on mobile). Routes through the existing `headerPrefsDirty()` / `saveHeaderPrefs()` pair so the unit toggle shares the same Save button + dirty-amber-ring as the rest of the topbar widget. Backend `/api/weather` continues to return Celsius unconditionally; SPA helpers `formatTempPref(c, decimals)` (returns `21.3°C` / `70°F`) and `convertTempPref(c)` (bare number for AI context) convert at the render boundary. AI palette context now forwards both the converted temperature AND the unit suffix string so AI replies match the operator's preferred unit; daily forecast min/max in the context block are pre-converted too. New i18n keys `topbar_widgets.unit_label` / `unit_celsius` / `unit_fahrenheit`.
- AI sidebar Pin-to-dock mode — converts the slide-out drawer into a permanent left-edge split-pane layout. New `aiSidebarPinned` state hydrated from `ui_prefs.ai_sidebar_pinned`; pin / unpin button in the sidebar header (icon swaps between `icon-pin` / `icon-pin-off`); hidden on mobile (sidebar is `100vw` there, pinning would obscure all content). When pinned, the body becomes a true two-column flex layout — sidebar `flex: 0 0 var(--ai-sidebar-width); order: -1`, app-shell `flex: 1 1 0`. Body scroll-lock is exempted in pinned mode so the rest of the SPA stays fully scrollable + interactive (pinned is NOT a modal). Backdrop hidden when pinned. New SVG sprite symbols `icon-pin` + `icon-pin-off`; new i18n keys `ai_sidebar.pin` / `pin_title` / `unpin` / `unpin_title`. Mobile media query reverts to `display: block` so a desktop-saved pref viewed on a phone doesn't break the layout.

## [1.4.0] — 2026-05-10

Fourth MINOR cut on top of `1.3.0` — rolls up **264 closed issues** under the 1.4.0 milestone (196 enhancements, 68 bug fixes). Each item shipped continuously through the PATCH cadence; this MINOR bundles them under a single tag for rollback / changelog reference.

### Highlights

- **AI Assistant** — full conversational sidebar replacing the modal Cmd-K target, multi-provider (OpenAI / Anthropic / Gemini / DeepSeek) with generic `ai_max_tokens` knob, log-context window with secret-pattern redaction, `MEMORY:` / `MEMORY-FORGET:` directives, host-identity enrichment, conversation export (TXT + JSON), Approval / Autonomous mode toggle, jump-to-latest pill, fenced-code-block code panels with copy buttons.
- **Unified Cmd-K / Ctrl-K command palette** — multi-word query support, action commands + AI assistant + Phase 1 bulk operations, multi-action queries, chart-kind heuristics, long-press shortcut overlay.
- **Curated `address` field** — single dedicated probe target across port-scan / ping / SNMP / SSH; resolution chain unified end-to-end (`aliases[id] → <provider>_name → address → SKIP`), drawer port-scan gate with address-required banner.
- **On-demand port-scan provider** — TCP + UDP, scheduled refresh kind (`port_scan_refresh`), default-ports fleet expansion (50+ new ports), section-owned tunables, standalone admin sub-tab placed after SSH.
- **SNMP infrastructure** — vendor-aware walk pruning + signature narrowing, per-host walk serialisation for slow BMC-class agents, per-host `walk_concurrency` override, storage extractor pseudo-FS filter + per-host exclusion list, SNMP-only host main-row visualisation, Test-connection cool-down bypass.
- **Beszel / Pulse / Webmin local sample storage** — Beszel local sample table closes the read-through-only gap (charts no longer empty when the hub's `1m` aggregation tier ages out); sister Pulse + Webmin samplers so Pulse-only hosts get history. node_exporter ZFS multi-dataset pool dedup + camelCase `MemTotal` fix, TrueNAS disk-aggregate fix, Pulse v4 `hosts`-array extractor.
- **Drawer chart polish** — per-host Health Score (0-100) chip + breakdown popover, "What changed" Timeline tab, inline 1h-trend sparklines overlaid on Hosts-row CPU / Memory / Disk stat-bars, gap-detection no-bridge across multi-hour samples, disk-projection chart with confidence-band fork, AI-Assisted Incident Triage Drawer.
- **Hosts-view bulk actions** — multi-host sticky bottom bar, per-host audit rows on bulk pause/resume, partial-failure breakdown toast, `host:bulk_action_applied` SSE event for cross-tab fan-out, step-up reauth gate on destructive admin actions, idle-time progressive fill complementing scroll-driven lazy load.
- **Notifications & Apprise** — in-app notifications feature with cross-tab refresh, per-medium preferences in Profile → Notifications, page UX overhaul, bulk-pattern picker, swarm-agent unhealthy banner + one-click restart, scheduled autoheal with cooldown anchors persisted across container restarts, toast pause-on-hover/focus + explicit copy button.
- **CodeQL security sweep** — `py/full-ssrf` (defence-in-depth shared helper), `js/insecure-randomness`, `py/url-redirection`, `py/clear-text-logging-sensitive-data`, `py/path-injection` (`_safe_avatar_path` + persistent-log endpoints + node_modules path-traversal hardening).
- **i18n + A11Y** — `NOTIFY_TEMPLATE_DEFAULTS` migrated to i18n via new backend `logic/i18n.py` loader, WAI-ARIA radiogroup keyboard nav across the three on-page radiogroups, chart `?` info-bubble fleet-wide a11y refactor, additional accessibility batches.
- **Admin tab refactor** — `static/index.html` admin sub-tabs extracted to per-tab partials under `static/_partials/admin/`, Settings sub-sections to per-section partials, Test-before-Save gating across Portainer / OIDC / Asset Inventory tabs, Settings → Profile → Formats with a user-configurable datetime token grammar that propagates across every date / datetime render.
- **User UI preferences** migrated from localStorage to the DB so theme / sidebar width / drawer pinning / datetime format travel cross-browser + cross-machine. Hardware card section gate accepts snapshot-fallback hits so cached fields stay rendered when every live provider is offline.

### Added

- AI Assistant — multi-turn conversational sidebar (Cmd-K target), multi-provider abstraction (`logic/ai.py:ask_provider` + `ask_provider_with_fallback`), inline charts (`memory_history` / `cpu_history` / `disk_projection`), per-call cost / latency / token-usage dashboard, retry-once-on-overload gate (`AI_RETRY_*` tunables), conversation export to TXT / JSON (gated behind `tuning_ai_conversation_export_enabled`), persistent log-context window (default 7 days, secret-redacted, capped at `tuning_ai_log_context_lines`).
- AI memory — durable per-deployment lessons via `MEMORY:` (append) and `MEMORY-FORGET:` (delete by exact text) directives, backed by the new `ai_memory` SQLite table; admin-only routes `GET / POST / DELETE /api/ai/memory[/{id}]` plus `POST /api/ai/memory/forget`.
- AI palette context enrichment — host telemetry / weather forecast / log context flow into the prompt; `_buildAiPaletteContext` is the single source of truth shared between the modal palette and the sidebar.
- Curated `address` field on every host row — provider-independent probe target consumed by port-scan, ping, SNMP, and SSH (replaces the bare `host_id` fallback that was the source of fan-out-against-non-mapped-hosts regressions).
- On-demand port-scan provider (`logic/port_scanner.py`) — TCP-first with optional UDP companion (`logic/port_scanner_udp.py`); per-host opt-in via `hosts_config[].port_scan = {enabled, ports?, timeout_s?, concurrency?}`; banner-grab via best-effort first-256-byte read; persists to `host_port_scans` (one row per detected open port); emits `port_scan:completed` SSE.
- Scheduled `port_scan_refresh` schedule kind — periodically re-scans port-scan-enabled hosts with oldest-scanned-first selection, per-tick host cap, min-age gate, and per-host parallelism (`tuning_port_scan_schedule_*` tunables).
- In-app notifications — SQLite-backed `notifications` table with per-medium master toggles + per-event admin gates + per-user opt-in/out; Notifications popup behind the user-avatar dropdown with severity / event / unread filters; `prune_notifications` schedule kind for retention.
- Notification template editor — admin-only `/api/admin/notify-templates*` routes; per-event title + body overrides via `notify_template_<event>_title` / `_body` settings; curated `{name}` / `{type}` / `{actor}` / `{host}` / `{time}` / `{error}` / `{status}` placeholder whitelist; live preview + Send-test path.
- `swarm_agent_health` schedule kind — watches the in-process `_agent_health` map; either notify-only (`swarm_agent_unhealthy` event) or auto-restart the Portainer agent service (gated by `tuning_swarm_autoheal_cooldown_minutes`, anchor persisted via `swarm_autoheal_last_restart_ts` so cooldown survives container restarts); first-boot bootstrap creates a default 5-minute schedule when Portainer is configured.
- Settings → Profile → Formats — user-configurable datetime token grammar (default `dd/MM/yyyy, HH:mm:ss`) persisted to `ui_prefs.datetime_format`; the new `_applyDateTimeFormat(d, fmt)` + `_userDateTimeFormat()` helpers route every date / datetime / clock render (`fmtDate` / `fmtDateOnly` / `fmtDateTimeShort` / `tickHeaderClock` / `hostTimelineTimeLabel` / persistent-log freshness / samples-table `fmtTs`) through one shared parser. Time-only renders strip time tokens via `_stripTimeTokens` so a single user pref drives every variant.
- Per-host Health Score — derived from CPU / memory / disk / net / load / errors with a breakdown popover; chip rendered next to the hostname when the underlying provider data is rich enough.
- Host drawer "What changed" Timeline tab — per-host transition log from `host_failure_events` joined with `history` rows where the host was the target.
- AI-Assisted Incident Triage drawer — surfaces clustered failure events from `logic/triage.py` and offers the AI palette as a "explain this" handoff.
- Inline 1h-trend sparklines overlaid on the Hosts-row CPU / Memory / Disk stat-bars (lazy-loaded via IntersectionObserver, with snapshot fallback paint on cold load).
- Hosts-view bulk-action sticky bottom bar — multi-host pause / resume / SNMP-vendor / SNMP-tunables apply, partial-failure breakdown toast, per-host audit rows, `host:bulk_action_applied` SSE event for cross-tab fan-out.
- Step-up reauth gate (`POST /api/admin/reauth`) on destructive admin actions for local-password users; SSO users bypass via `_user_has_local_password` short-circuit.
- WAI-ARIA radiogroup keyboard navigation for the three on-page radiogroups (drawer chart range picker, Health filter chips, stat-bar threshold picker).
- Backend i18n loader (`logic/i18n.py`) — used by `NOTIFY_TEMPLATE_DEFAULTS` migration; reads from `static/i18n/<lang>.json` with English fallback so notification titles / bodies localise per recipient.
- New SSE event types: `host:provider_probing` / `host:provider_done` (per-host probe slice progress), `host:bulk_action_applied` (cross-tab fan-out for bulk Hosts-view actions), `port_scan:completed`.
- New tunables: `tuning_ai_max_tokens`, `tuning_ai_log_context_hours`, `tuning_ai_log_context_lines`, `tuning_ai_retry_*`, `tuning_ai_fallback_max_depth`, `tuning_ai_sidebar_width_px`, `tuning_ai_conversation_export_enabled`, `tuning_port_scan_default_*`, `tuning_port_scan_schedule_*`, `tuning_port_scan_udp_default_*`, `tuning_swarm_autoheal_cooldown_minutes`. Full table in [`docs/guidelines/env_example.md`](docs/guidelines/env_example.md).
- AI sidebar launcher hide — Settings → Profile → Topbar widgets toggle (Cmd-K still opens the sidebar regardless).
- Beszel local sample tables (`host_beszel_samples`, `host_beszel_services`) + lifespan-managed `host_beszel_sampler` so chart queries no longer depend on the hub's transient aggregation tiers.
- Pulse + Webmin lifespan-managed samplers (`host_pulse_sampler`, `host_webmin_sampler`) writing to `host_pulse_samples` / `host_webmin_samples`.
- Pulse v4 `hosts`-array extractor (`extract_pulse_host_stats`) — Pulse-agent-tracked Linux hosts that don't share the PVE-guest schema.

### Changed

- AI sidebar dirty-cue contract — amber ring + Unsaved pulse-dot tied PURELY to `<name>Dirty()`, never gated on `canSave<Name>()`. Earlier iteration coupled the two and suppressed the cue when a post-test edit re-locked the gate; three honest signals beat one mixed signal.
- Admin sub-tab markup extracted from `static/index.html` (≈14k lines) into per-tab partials under `static/_partials/admin/<tab>.html`, inlined at request time by `_render_shell()`'s `<!-- INCLUDE: ... -->` marker expansion. Settings sub-sections similarly extracted to `static/_partials/settings/<section>.html`. Master template down to ≈8k lines (~44% smaller).
- Provider chip state taxonomy normalised — `failing` → `pill-error` (red), `paused` → `pill-muted` (grey), healthy → brand colour. `pill-warning` (amber) is no longer used for either state; visual conflation between "currently producing a recoverable warning" and "auto-paused after repeated failures" was confusing the at-a-glance triage.
- Backend post-merge percent recompute — `_merge_one_host` re-derives `host_mem_percent` and `host_disk_percent` from the final merged `host_*_used` / `host_*_total` after every provider has contributed, so SNMP's naive `total - free` no longer leaks past NE's per-OS-aware bytes accounting. SPA helpers `memPercentOf(h)` / `diskPercentOf(h)` consume the recomputed value first; `fmtPercentLabel(v)` renders 1 decimal across the full range.
- `/api/me` `client_config` now includes `hosts_idle_fill_seconds`, `notifications_page_size`, `stat_bar_warn_pct`, and the AI sidebar / port-scan tunables surface so the SPA reads through one canonical channel.
- Snapshot persistence — `_merge_one_host` writes to `host_snapshots` only when at least one snapshot-eligible field came from a LIVE provider, so the freshness banner ("Last live data Xm ago") matches the chart's "Last sample N ago" instead of resetting on every drawer poll.
- Beszel hub-tier picker — `_pick_stat_type(hours)` selects retention-aware aggregation tiers (`≤1h → 1m`, `≤12h → 10m`, `≤48h → 20m`, otherwise `120m`) so 24h windows no longer return only the last hour.
- node_exporter parser handles `node_memory_MemTotal_bytes` (camelCase) and ZFS multi-dataset pool dedup so disk totals don't multiply by N datasets.
- Webmin extractor suppresses `host_cpu_percent` when any other provider is active (its single-shot `/proc/stat` snapshot is coarser than Beszel / NE longer-window samples).
- Apprise notify dispatch + the new `app` medium fan out via `asyncio.gather(return_exceptions=True)` so a failure in one medium doesn't drop the others; retry-once-on-transient-overload (HTTP 429 / 502 / 503 / 504) gated by the `tuning_ai_retry_*` knobs for AI provider calls.
- Synology dark-theme icon updated to a single-colour white-on-transparent variant; Linux Mint, Apprise, Seerr brand icons added or refreshed.
- Python container base bumped from `python:3.12-slim` to `python:3.14-slim`. Existing call sites updated for `datetime.fromtimestamp(ts, tz=timezone.utc)` / `datetime.now(timezone.utc)` (replacing deprecated `utcfromtimestamp` / `utcnow`).
- CI workflow moved to Node.js 24 ahead of the September 2026 Node 20 removal.
- Drawer-chart range picker (1h / 6h / 24h / 7d) persists across refresh via `localStorage.hostHistoryRange`.
- Topbar refresh spinner now stops when the underlying gather completes (no longer spins forever after a transient error).
- `swarm_agent_unhealthy` notifications fire on TRANSITIONS only — single incident emits one alert + one matching `swarm_agent_recovered`, instead of every cycle.
- TOTP audit rows go through `assert_op_type` so the canonical `op_type` registry catches typos at insert time.
- Notifications retention dial moved from Admin → Process Tunables to Admin → Notifications where users editing notification policy will find it.
- Per-medium notification preferences moved into Profile → Notifications.
- `notify` template defaults now resolve via `NOTIFY_TEMPLATE_DEFAULTS[event][kind]` with DB overrides, and missing placeholders render verbatim (`{key}`) via `SafeDict` rather than raising `KeyError` mid-dispatch.
- UI sprite caching switched from `no-cache` to `public, max-age=31536000, immutable` with content-hash query string.

### Fixed

- Cool-down log lines no longer flag as ERROR — `_severity_for` regex was matching the literal `failed` in cool-down skip messages and turning Admin → Logs red on benign skips. Skip / cool-down / deferred messages now use verbs that don't match the ERROR regex; resolved probe target is included in every log line so back-off tracing doesn't require host_id → alias cross-reference.
- Drawer charts no longer bridge across multi-hour sampling gaps with one fake-smooth line — gap detection inserts a break when consecutive samples are >2× the expected interval apart.
- Stack-header `upd` count no longer inflated by offline / orphan containers carrying stale image digests.
- `_kick_background_gather` returns the in-flight task ref instead of bool, so cold-cache callers `await` the same task the bg-refresh path just spawned (single-flight invariant).
- `_BACKGROUND_TASKS` defensive cap on the strong-ref set fires a WARN log line at the cap so a future spawn-site leak is visible instead of silently growing the set.
- SSE `host:provider_*` events thread `client_id` for self-filter so the originating tab doesn't echo-paint on its own action.
- SSE `host:provider_done` events carry an `ok: bool` outcome hint so the SPA chip can settle into the right post-probe state without a second round-trip.
- Hosts row in-place reconcile — never wholesale-replace `this.hosts` array. Backend transient errors (single tick) preserve the previously-known marker through the blip via `Object.keys(host)`-only assignment in `refreshHostRow`.
- Sparkline post-probe history backfill + flat-zero hide on cold load.
- Port-scan "Scan ports" drawer button stays disabled + spinning until the scan COMPLETES on the backend (was prematurely re-enabling on the queued response).
- Port-scan previous-cycle results are surfaced even when the master toggle is off (read from the persistent table; running NEW scans is still gated).
- Webmin master-toggle disabled-state respected by the section's dirty-tracking helper.
- TrueNAS disk-aggregate over-count from multiple `node_filesystem_*` series sharing one underlying pool.
- AI request-timeout retry honours the fallback chain instead of returning the first 30s timeout to the user.
- AI sidebar typing lag (textarea is now an uncontrolled DOM input with a throttled vanilla JS listener; per-keystroke Alpine reactivity removed).
- Beszel hub-batch single-flight — concurrent /api/hosts/one/{id} callers reuse one hub probe instead of fanning out N.
- AI palette markdown rendering — fenced code blocks render as proper code panels with copy buttons.
- AI weather context — `/api/weather` response now uses compact field names (`temp_c` / `humidity` / `wind_kmh` / `condition` / `forecast`) the SPA actually reads, replacing the verbose Open-Meteo schema that the SPA's `_buildAiPaletteContext` was looking through but not finding.
- AI palette chart-kind dispatch — heuristic correctly picks `memory_history` / `cpu_history` / `disk_projection` from the prompt content; irrelevant disk-projection charts no longer render on non-disk queries.
- Drawer keyboard navigation (←/→ to step through the visible filtered list) no longer re-fires `openHostDrawer` on every press.
- Apprise brand icon — switched to homarr-labs `dashboard-icons/webp/apprise.webp` source with the `<image href="data:...">` SVG-wrap pattern so the resolver's `.svg` extension contract holds.
- Toast notifications — pause-on-hover/focus + explicit copy button (replaces the auto-dismiss-only contract that was eating in-flight reads).
- SweetAlert page-jump on confirm popups — when validation fails on a row that's not on the current admin-editor page, the page-jump runs BEFORE `focusFirstFieldError` so the focus actually lands.
- `<asset-api-host>/admin/api` cache clear on `Type.ShortName` casing variations — `shape_asset` walks every plausible casing so the host drawer's `[<TYPE>]` prefix renders consistently.
- SQLite LIKE-pattern wildcard leak in three host-id sites (timeline + bulk-resume) — host_ids carrying `%` / `_` no longer match unrelated rows.
- Path-traversal guard on persistent-log endpoints + node_modules path-traversal hardening.
- `py/clear-text-logging-sensitive-data` at `logic/events.py:178` — event payloads no longer log secret-bearing fields verbatim.
- Stack-row `_stale` indicator + `cache_refreshing` / `hub_probing` SPA hints surface during the cold-load instant-paint window.
- `host_pulse_sampler` no longer raises `unknown tunable` on every tick (same drift class as the earlier Webmin sampler fix).
- `audit_template_data` placeholder validation rejects unknown placeholders at save time; deprecated placeholders flagged via the `NOTIFY_DEPRECATED_PLACEHOLDERS` map.
- `_clean_host_snmp.walk_concurrency` per-host override accepts the same `(lo, hi)` range as the global `tuning_snmp_per_host_walk_concurrency` (was 1..32 vs 1..16 — admin-set per-host=24 validated fine but global=24 silently clamped to 16).

### Security

- CodeQL `py/full-ssrf` triage across the probe modules — defence-in-depth refactored into a shared `url_safety` helper that classifies hostnames before outbound HTTP fan-out.
- CodeQL `js/insecure-randomness` cleanup at `static/js/app.js` — `Math.random()` fallback replaced with `crypto.getRandomValues` for the per-tab `client_id` and the WebAuthn challenge nonce.
- CodeQL `py/url-redirection` at `logic/oidc.py` — `next` parameter validated against a same-origin allowlist before redirect.
- CodeQL `py/path-injection` at `_safe_avatar_path` — avatar-write path traversal hardened via Path.resolve() + parent-directory containment check.
- Test-before-Save gate on Portainer / OIDC / Asset Inventory admin tabs — Save unlocks only when the form snapshot stamped at last-successful-Test matches the live form snapshot.
- Step-up reauth gate (`POST /api/admin/reauth`) on bulk-destructive admin actions for local-password users.
- `omnigrid.*` container label namespace lockdown — only the curated set (`omnigrid.url` / `omnigrid.name` / `omnigrid.icon` / `omnigrid.hide`) is consumed; arbitrary labels do not flow into the SPA.

### Internal

- Provider-name set canonicalised — `logic/host_metrics_sampler.py:_PROVIDER_PREFIXES` is the single source of truth; `main.py` imports as `_PROVIDER_AUTO_PAUSE_NAMES`. SNMP vendor MIB key set similarly canonicalised at `logic/snmp.py:_VALID_VENDOR_KEYS`. Notification mediums at `logic/ops.py:NOTIFY_MEDIUMS`.
- `op_type` canonical-name registry — `logic/ops.py:OP_TYPES` is the single source of truth; `assert_op_type(op_type)` gates every raw `INSERT INTO history` site so typos can't silently land bad rows.
- `record_provider_outcome(host_id, provider, ok, ...)` is now the canonical helper at every per-(provider, host) probe boundary — both success AND failure branches stamp `host_provider_last_ok` so the chip's "Updated Xm ago" subtitle populates.
- Section-owned save pattern — every admin tab declares `_<name>SectionTuningKeys()` + `_<name>SectionPlainKeys()` + `<name>SectionDirty()` + `save<Name>Section()` so a Pulse Save no longer re-POSTs every Webmin / Beszel / NE field.
- Auto-pause sweep on lifespan startup — orphan `<provider>:<host_id>` rows in `host_failure_state` + `host_provider_last_ok` are deleted when the host has been removed from `hosts_config` OR no longer has the provider configured.
- Snapshot-first render in `/api/hosts/list` — pre-populates each row with last-known `host_*` fields from `host_snapshots` with `_stale_fields` / `_stale_ts` markers so repeat visits paint instantly.
- `_populate_detected_ports` shared helper — `api_hosts_list` AND `_merge_one_host` read from one helper to surface `host.detected_ports[]` from `host_port_scans`, with no toggle / provider-state gates.
- AI palette context build moved to a single `_buildAiPaletteContext()` method shared by the modal palette and the sidebar; rich records (host metrics, weather forecast, log context) replace bare IDs.
- `static/index.html` sub-8k lines via the per-partial split.
- `tmp/img_*` references stripped from every committed surface — local screenshots are ephemeral; descriptions in prose travel.
- `notes/note_todo.txt` LATER block ordering hardened to ascending `[#NNN]`; sub-section headings now show `(none)` placeholder when empty so "actually empty" is distinct from "section truncated by a bad edit".

### Removed

- `host:row_updated` SSE event retired — `/api/hosts/one/{id}` was the only publisher, and the SPA-side handler caused an SSE infinite loop (read endpoint published an event, handler called the read endpoint, which published another event). Per-host UI updates now flow exclusively through `host:failure_state_changed` (sampler-driven) and the existing 30s polling fallback.
- Admin → Version page removed — pre-fix the route at `GET/POST /api/admin/version` wrote to `/app/VERSION.txt` via a per-file bind mount that no longer exists under image-build deploys. The durable seed path is now: edit repo-root `VERSION.txt`, commit, push — `deploy.yml`'s source-B resolver reads it as the floor.

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
[1.4.0]:../../releases/tag/v1.4.0
