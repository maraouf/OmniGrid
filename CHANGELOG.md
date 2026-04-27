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

### Added

- Local-admin 2FA (TOTP) for accounts without Authentik SSO. End-to-end TOTP via pyotp + cryptography (Fernet, HKDF-from-SESSION_SECRET); 5 additive `users.*` columns; multi-step `api_local_login` with in-memory `_totp_challenges` (5-min TTL); Profile → Two-factor card (idle/qr/reveal sub-states); Admin → Users 2FA column + per-row Disable; Admin → Config policy section (master + per-role required + lockout); Login page rewritten for multi-step (TOTP code OR backup code OR forced-enrol QR + reveal). Authentik users skip every path; bearer-token requests bypass; `[totp]` audit lines for every state change (#345).
- Per-user force-2FA toggle from Admin → Users table. New `users.totp_force_required INTEGER NOT NULL DEFAULT 0` column. User dataclass + `_row_to_user` + `list_users` extended; `set_user_totp_force_required` helper. `_totp_required_for_user(user)` ORs the per-user flag with global role-policy and short-circuits for Authentik. New `POST /api/users/{id}/totp-force` admin endpoint with audit row `op_type='totp_force_set'`. Frontend: 2FA column has a 3rd "Required" state pill (warning); Users-table row gains "Force 2FA" / "Unforce" buttons. i18n keys `admin.users.totp_{required, force, unforce, ...}` + `toasts.totp_{forced_on, force_cleared, force_failed}` (#376).
- New Admin → Authentication tab — the TOTP / 2FA policy section (master toggle + per-role required + lockout window) moves out of Admin → Config into its own sidebar entry directly after Users. Future home for any other auth-related admin settings (forced-OIDC-only mode, session-cookie tunables, bearer-token rotation policy). i18n key `admin.sections.authentication` (#374).
- Save button + dirty indicator on Admin → Authentication tab TOTP/2FA section. Mirrors the canonical master-toggle pattern (`_totpPolicyBaseline` slot + `_totpPolicySnapshot()` + `totpPolicyDirty()` + `saveTotpPolicy()` POST to `/api/settings`, baseline captured after `loadSettings()` and after each successful save). Frontend Save row inside the Authentication tab section: amber-ringed `btn-primary` Save button + animated amber-dot "Unsaved" pill, both bound to `totpPolicyDirty()` (#382).
- Notifications becomes a settings-sidebar peer of Profile / Ignore list / Language. `settingsSections` gets `{id:'notifications'}` between Profile and Ignore list; Notifications card moved out of the Profile-grid into a top-level section. Convenience buttons (Enable all / Disable all / Errors only) at the bottom of the Notifications card mirror the admin-side row but skip admin-disabled events. i18n key reused: `settings.sections.notifications` (#378).
- Per-user notification opt-in/out — two-layer scoping (admin global + per-user). Reuses the existing `users.ui_prefs` JSON column (sub-key `notify_events`) — no schema change. Backend: `logic/auth.py` gains `get_user_notify_prefs` / `set_user_notify_prefs` helpers; `logic/ops.py` `notify()` extends with `actor_username: Optional[str]` (kw-only after `event=`); 12 ops + 2 user_login call sites pass it. Notify gate: admin-event check first, then per-user `notify_events` map check, short-circuit with `[notify] skipped — user 'X' opted out of 'Y'`. New endpoint `PATCH /api/me/notify-prefs` (current_user, NOT admin-only); refuses `True` for admin-disabled events with 400. `/api/me` GET adds `notify_events` (resolved per-user map) + `notify_events_admin` (admin gate snapshot). Frontend: Profile modal Notifications card mirroring the admin grid; admin-disabled toggles grey out (opacity-50) with tooltip. i18n keys under `profile.notifications.{title, subtitle, disabled_by_admin}` (#357).
- Notification: user-login event. New `notify_event_user_login` setting (default OFF). `main.py` adds the field to `SettingsIn` + GET response + `_NOTIFY_EVENT_KEYS` validator tuple; `await notify(f"🔓 {u.username} signed in", f"via local from {ip}", "info", event="user_login")` after `set_csrf_cookie` in `api_local_login` (try/except wraps the call so notification failure never breaks the login response). `logic/oidc.py` mirrors the pattern after `delete_cookie(FLOW_COOKIE)` in the OIDC callback (body reads "via authentik from {ip}"). Frontend extends `notifyEventKeys` (now 13) + a new `notifySecurityEvents` registry; new "Security events" subsection with single-toggle row. i18n: `admin.notifications.events.{security_section, user_login, enabled}` (#355).
- Split Notifications admin tab into Notifications + General; per-event notification toggles. `logic/ops.py:notify()` now accepts keyword-only `event=` and gates on `notify_event_<event>` via `get_setting_bool`; 12 call sites updated to pass the matching event key. `main.py` adds 12 `notify_event_*` Optional[str] fields on `SettingsIn` (default-true) + validator block + resolved-bool round-trip in `api_get_settings`. Frontend: new `general` section in `adminSections` directly after `notifications`; `_appriseSnapshot` folds in the 12 event keys so a single Save covers Apprise URL/tag + every event toggle; new `notifyEventKeys` / `notifyEventGroups` registries + `setAllNotifyEvents` / `setNotifyEventsErrorsOnly` helpers. Notifications tab keeps Apprise + new 6-row × 2-col events grid with Enable all / Disable all / Errors only buttons; new General tab hosts the moved Open-Meteo block verbatim (#354).
- Pending-updates badge on the Stacks nav button — small pill showing the count of items with `status === 'update'`. Only renders on Stacks (Services / Nodes are alternate views over the same items, so a duplicate badge would just repeat the same number). Wired off the existing `counts.update` getter; the existing op-polling loop already calls `refresh(true)` on every op completion, so the badge falls back to 0 once an update lands without any extra wiring (#372).
- Per-chart "?" data-source icons in host drawer. Each of the 8 chart cards (CPU / Memory / Disk / Network / Disk I/O / Load Avg / Bandwidth / Swap) gains a small `<svg><use href="#icon-help-circle"/></svg>` next to the title, bound to `:title="t('hosts_extra.metrics.source_<key>')"` for a native browser hover tooltip explaining the data source per chart (Beszel vs node-exporter vs fallback chain). New i18n keys under `hosts_extra.metrics.source_*` (8 keys); new `.metric-source-help` CSS rule (cursor:help, `var(--text-faint)`) (#373).
- Admin intro paragraph ("User accounts, active sessions, and API tokens. Destructive actions prompt for confirmation.") was rendering on every admin tab even though the copy only describes Users / Sessions / Tokens. Wrapped in `x-show="['users','sessions','tokens'].includes(adminTab)"` so it only shows on the three tabs the text actually describes; other tabs already carry their own section heading + subtitle (#395).
- Per-host metric-source tooltips — chart `?` icons now resolve a definitive per-host label instead of a generic "Beszel OR node-exporter" string. New `metricSource(h, key)` helper in `static/js/app.js` reads the host's mapped providers (`h.beszel_name` / `h.pulse_name` / `h.ne_url` / `h.webmin_name`) and a per-metric precedence map; returns labels like `Beszel agent (web01)` / `node-exporter (http://10.0.0.10:9100/metrics)`. Network / Bandwidth get an explicit callout for the NE-rate fallback when Beszel returns zero. CPU / Load Avg / Swap on NE-only hosts surface "the NE sampler doesn't track this yet, so this chart will be empty for this host". Falls back to the generic i18n string when nothing is configured (#390).
- Profile section icons — About card heading gets `#icon-id-card`; Two-factor card heading swapped its inline shield-path SVG for the consolidated `<use href="#icon-shield"/>` symbol. Both follow the unified flex-wrapper pattern (#398).
- Portainer + OIDC admin badges unified — both flipped from `pill-success` (bright green) to `pill-ok` (subtle muted-green); Portainer wording flipped from "Configured" / "Not configured" to "Enabled" / "Disabled" so both reads match the master-toggle pattern (#401).
- Admin section-header icons unified — every admin tab's primary heading now renders its matching `adminSections[i].icon` next to the title using the same `<svg><use href="#icon-X"/></svg>` + flex-wrapper pattern that #396 applied to settings sections. 19 sites: Users, Sessions, Tokens, Notifications, General, Portainer, OIDC, Host Stats, Hosts, Host Groups, SSH, Asset Inventory, Schedules, Backups, Logs, Config, Authentication, Version, Debug (#399).
- Settings section-header icons unified — every settings section now renders its sidebar-matching icon next to the heading: Profile → Identity (`#icon-user`), Notifications (`#icon-bell`, already shipped with #378), Ignores (`#icon-trash`), Language (`#icon-info`), Shortcuts (`#icon-help-circle`). Security wraps two cards that already carry their own icons (lock, shield) (#396).
- Permanent-fail marker for chronically-down hosts. New `host_failure_state` table tracks consecutive sampler failures per host; once they exceed `host_permanent_fail_window_seconds` (default 900 = 15 min, editable in Admin → Config), the sampler auto-pauses the host (no probe attempts, no log spam) until the operator clicks "Resume sampling" in the host drawer's banner. Hosts table renders a danger-tinted alert-triangle icon next to the name when paused. New `POST /api/hosts/{id}/resume-sampling` admin endpoint clears the marker. Backend exposes `sampling_paused` / `failure_window_started_at` / `consecutive_failures` / `last_error` on every host row in `/api/hosts` (#383).
- Per-recipient Apprise routing — when a notification fires for a specific user (per-event opt-in path with `actor_username`), the configured Apprise URL's recipient is overridden via the POST body's `to=` field with the user's email. Apprise's mailto handler honours `to=` as a query-time recipient override; non-mailto schemes (Discord webhook, Slack incoming, Telegram bot) silently ignore it (#356).
- NE CPU% derivation. `parse_exporter_text` now sums `node_cpu_seconds_total` across all CPUs all modes for `host_cpu_seconds_total`, only mode=idle for `host_cpu_seconds_idle`. Sampler delta-maths these against the previous tick to compute `%CPU = 100 * (1 - (delta_idle / delta_total))` clamped to [0, 100]; skips on counter reset / clock skew. NE-only hosts now have a populated CPU chart after two sampler ticks (#402).
- Admin → Users actions column iconified — six text buttons (`→ readonly` / `Disable` / `Reset pw` / `Disable 2FA` / `Force 2FA` / `Delete`) replaced with icon-only squares carrying the original `:title` for hover hint + `:aria-label` for screen readers. Cluster separators split identity / 2FA / destructive groups visually. Net win: actions row collapses from 3 stacked rows to 1 inline row (#400).
- Lifespan startup robustness pass. (a) ARCH-001: `seed_stats_cache_from_db` and `seed_nodes_info_from_snapshots` moved into a background `asyncio.create_task` so a slow CTE on a months-old `stats_samples` table can't delay FastAPI accepting connections — Swarm's 60s `start_period` budget stays intact even with a cold cache. (b) ARCH-002: `init_db()` is now wrapped in try/except — a DDL failure short-circuits to the same config-error code path as `DB_PATH_ERROR` (sets `DB_PATH_ERROR` to the failure string + yields without spawning any background tasks). Previously a partial init_db could spawn the four samplers against a half-initialised schema (#404).
- Authentik logo inside the source chip on the Profile page. When `me.source === 'authentik'`, the chip renders a small `<img src="/img/icons/authentik.svg">` before the text. Local + bearer-token sources stay text-only (#394).
- Sidebar-header icons on Admin + Settings views — the "Admin" / "Settings" headers at the top of each sidebar now render their matching avatar-dropdown icon (shield / settings) next to the label. New `.page-sidebar-header-icon` CSS rule + `display: flex` on `.page-sidebar-header` to host the icon inline. Subtle `var(--text-dim)` colour, gap matches the sidebar buttons' spacing (#392).
- New Settings → Security section — settings-sidebar peer of Profile / Notifications / Ignore list / Language. The change-password card AND the TOTP enrolment card AND the Authentik passive-note card move out of the Profile grid into this new wrapper. Profile stays focused on identity (display name / email / bio / topbar widgets / appearance). New i18n key `settings.sections.security`. Sidebar entry `{id:'security', label:'Security', icon:'shield'}` was already registered in `settingsSections` (#389).
- Bootstrap-admin env-vars-still-set warning banner. Backend extends `/api/me` with `bootstrap_env_still_set: bool` — true ONLY when both `BOOTSTRAP_ADMIN_USER` and `BOOTSTRAP_ADMIN_PASSWORD` are set in env AND `count_users() > 0` (the seed path is now a no-op but env vars are still in `.env` waiting to surprise-re-seed on a wiped DB). Frontend banner mirrors the SESSION_SECRET banner: yellow warning chrome, dismissable per browser session, re-appears on restart until env vars are cleared. New `bootstrapEnvWarningDismissed` state slot + `dismissBootstrapEnvWarning()` method. New i18n family `warnings.bootstrap_env_still_set.{title, body, dismiss_title}` (#350).

### Changed

- Admin → Version page now edits every component (MAJOR / MINOR / PATCH) and writes the values straight to `VERSION.txt`. Replaces the original DB-override model from earlier in the cycle. Compose now layers a writable per-file bind for `VERSION.txt` on top of the read-only `/app` mount; the deployment pipeline keeps bumping the same file on every deploy. Use case: reset PATCH to 0 from the UI when cutting a MINOR release. Operators must redeploy the stack once for the new compose bind to take effect (#353).
- Simplified Admin → Version copy + the deploy.yml bump-step note. `patch_label` drops the "(CI-managed)" suffix; `patch_hint` is now "Managed by the deployment pipeline."; `cadence_note` rewritten to reflect the new editability; `.forgejo/workflows/deploy.yml`'s 32-line bump-step doc-comment block collapsed to a single `# Managed by the deployment pipeline.` line (script logic untouched) (#352).
- Provider test endpoints surface human-readable failure summaries instead of raw upstream stack traces. New `_humanise_probe_error(raw, target_label)` helper in `main.py` pattern-matches common upstream-failure shapes (HTTP 401/403/404/500/503, DNS gaierror, connection refused, connection reset, timeout, TLS handshake, EOF, multi-line PocketBase JSON dumps, Webmin auth-cool-down + "all modules failed") into operator-readable one-liners. Wired into `_format_provider_test_summary` (Pulse + Beszel) AND extended to Webmin + Portainer test endpoints (#349).
- Hosts editor: typing `custom_number` into a row + tabbing out no longer reorders the row mid-edit. cn `@input` now sets `row._cnDirty = true`; rebuild + page-jump deferred to a new `onHostCardFocusOut(idx, $event, $el)` handler on the host-card `@focusout`. Ignores focusouts that land back inside the same card (still editing) and only fires `rebuildHostsConfigOrder()` + `$nextTick` page-jump when focus has truly left the card. Admin → Host Groups was unaffected — `sortedGroupsForEditor()` orders by ORIGINAL array order, not by `g.number` (#359).
- SVG `<symbol>` dedup on `static/index.html`. 15 unique icons (copy / chevron-right / chevron-down / chevron-up / refresh-ccw / rotate-cw / trash / info / clock / help-circle / alert-triangle / search / lock / arrow-up-right / loader) extracted into a hidden `<defs>` block; 76 inline SVG instances now consume the symbols via `<svg ...><use href="#icon-..."/></svg>`. Each symbol bakes the canonical Lucide attrs (fill="none", stroke="currentColor", stroke-width="2", round caps); per-site stroke-width overrides on the wrapper inherit through `<use>`. RTL-flippable icons keep `flip-rtl` on the wrapper (#360).
- Add subgroup in Admin → Host Groups now scrolls the new row into view + focuses the name input. Added `:data-host-group-card="origIdx"` to the row card wrapper; extended `addHostSubGroup` `$nextTick` block with a 50ms `setTimeout` that queries the new card via the data-attr after the page-jump and calls `scrollIntoView({behavior:'smooth', block:'center'})` + focuses the name input (#361).
- Unify Save-button copy across admin tabs — every Save button now reads "Save" instead of mixing bare and bespoke labels ("Save OIDC Settings" / "Save Host Groups" / "Save Asset Inventory" / "Save Tuning Config" / "Save Version" / "Save Hosts editor"). Six bespoke i18n key references in `static/index.html` flipped to `t('actions.save')`. The bespoke i18n keys stay in the dictionary — they're still referenced by section headings + saved-toasts (which keep their context-specific copy for confirmation) (#388).
- Post-save host-data staleness window — `saveHostStats` now bypasses the 10s `_host_provider_cache` memo on the next host-list poll. Backend `api_hosts_list` accepts `force: bool = False` query param that propagates to `_get_host_provider_state(force=force)`. Frontend `loadHosts(force = false)` accepts a flag and appends `?force=true` to the URL when set; `saveHostStats` now calls `this.loadHosts(true)` immediately after the success toast so the next render reflects the new provider state without waiting up to 10s for the natural cache miss (#347).

- Per-chart "?" tooltip in host drawer was not rendering — the `:title` was bound directly on the `<svg>` element, but browsers ignore the HTML `title` attribute on SVG (SVG needs a `<title>` child instead). Each of the 8 chart-help icons now wraps its `<svg>` in a `<span class="metric-source-help" :title="...">` so the tooltip lands on the span (#373 fix-pass).
- Mobile pinch-zoom is now actually disabled on iOS Safari, not just on Android. iOS Safari deliberately ignores the viewport `user-scalable=no` hint since iOS 10 (accessibility), so the meta tag alone wasn't enough. Added `touch-action: pan-x pan-y` on body + JS `gesturestart` / `gesturechange` / `gestureend` preventDefault listeners + a 300ms double-tap detector at the document level. Three layers stack to cover every browser path (#385 fix-pass).
- "Unsaved" pill text now flashes subtly on a 2s opacity cycle (1 → 0.55 → 1, ease-in-out) — slower than Tailwind's `animate-pulse` opacity range but matched in cadence so the eye lands on the dot first then notices the gently-breathing word. New `@keyframes subtle-flash` + `.unsaved-flash` utility class in `static/css/style.css`; applied to every unsaved-pill text span across 16 admin/settings sites (Profile, Notifications, Authentication, Apprise, OpenMeteo, Portainer, OIDC, Debug, Hosts editor, Host groups editor, Config, Version). The animated dot keeps its Tailwind `animate-pulse` (#386).

### Internal

- Documentation refresh pass — 5 docs files modified to match the recently-shipped feature waves: PII leak in `static/i18n/README.md` stripped (operator-private Forgejo URL); 5 audit-grep cleanup sites in `docs/guidelines/deploy.md` (`git.www.example.com` → `git.example.com`); two API table corrections in `README.md` (`POST /api/hosts/discover` → `GET`, `/api/hosts/history` clarified to accept both `system_id` and `host_id`); `docs/guidelines/env_example.md` extended with the bootstrap_env_still_set warning paragraph + recently-shipped surface coverage (per-event notifications, TOTP/2FA policy, asset-inventory auth modes, host_groups optional fields) + corrected `open_meteo_url` location after the General-tab split (#393).

### Fixed

- QR rendering bug — TOTP enrolment QR was showing the raw `otpauth://...` URI instead of an actual QR code. Two compounding issues: script tag in index.html pointed at `qrcode.js` while the package's main is `dist/qrcode.js`; AND `qrcode-generator/dist/qrcode.js` wasn't on the `_NPM_ALLOWED` allowlist per CLAUDE.md ARCH-003. Both fixed (#375).
- TOTP enrolment QR was rendering at full container width (~600-800px on desktop). qrcode-generator SVG has no intrinsic CSS size; `.totp-qr svg { max-width: 100% }` let it stretch. Fix: clamp to `width: 220px; max-width: 100%; height: auto` (#380).
- Profile → Topbar widgets card always showed "Unsaved" indicator on page open. `_headerPrefsBaseline` was initialised to `''` and only re-captured inside `saveHeaderPrefs()` — `headerPrefsDirty()` compared `'' !== '{...}'` → always true until first Save. Fix: `init()` now captures the baseline via `_headerPrefsSnapshot()` immediately after `applyServerUiPrefs()` hydrates the form (#379).
- Notification settings save bug — `loadSettings()` did NOT hydrate the `notify_event_*` keys from the GET response into `this.settings`, so every Save POSTed all events as `'false'` (saveSettings normaliser mapped undefined to false), wiping the persisted state on every save. Fix: hydration loop in `loadSettings` after the explicit settings assignment (shipped alongside #355).
- Pagination — field error on a filtered-out row no longer silently no-ops. `focusFirstFieldError` in `static/js/app.js` extended: when the host-row error key (`host_<idx>_<field>`) maps to a row that's NOT in the current `filteredHostsConfig()` slice AND a filter is active, fires a SweetAlert "Validation errors on filtered-out rows / Clear filter" with a one-click Clear-filter action. Confirming clears `hostsConfigFilter` and re-runs `focusFirstFieldError` (now reaches the row) (#348).
- Host drawer freshness label out-of-sync with host-poll cadence. Two iterations: (a) DEDICATED drawer poll — `openHostDrawer` now starts a `setInterval(loadHostHistory, 30s)` that tracks `drawerHost`; cleared on `closeHostDrawer`. Independent of `statsInterval` so the chart refreshes even when the host-list auto-refresh is off. (b) `loadHostHistory` now stamps `loadedAt = Date.now()` on every successful HTTP 2xx, regardless of whether the series came back populated — operator expectation is "when did we last poll", and an occasional empty reply shouldn't make the label drift past one poll cycle. Visible series still preserves `prev.series` on empty so the chart line doesn't blank (#346).

## [1.1.0] — 2026-04-26

First MINOR cut after the `1.0.0` baseline. Every entry below shipped to
the live deploy as a PATCH bump (the daily CI auto-bump cadence) and is
now rolled into this MINOR release. Highlights: NE-only host charts now
populate Disk I/O on Linux (`node_disk_*`) AND FreeBSD (`node_devstat_*`),
distinguish "host is idle" from "exporter doesn't expose this collector"
(#327), pull cross-provider snapshots so existing data survives a provider
outage (#222 series), and fold a refreshed pile of UX polish (host-drawer
freshness label, scroll-on-expand, action-bar sticky behaviour, login-page
logo, SSH terminal cols/rows). Backend / docs / refactor work was largely
internal cleanup — see the Internal section.

### Internal

- Documentation moved from `notes/guidelines/` and `notes/RELEASE_PROCESS.md` to a new `docs/` directory; new `docs/screenshots/` for README images; new `docs/README.md` index. Operator-private files (`note_todo.txt`, `notes.txt`, `forgejo_runner_config.yml`, the live Grafana dashboard, `.claude/agent-memory/**`) stay in `notes/`. `CHANGELOG.md` and the root `README.md` keep their root-level positions per convention. CLAUDE.md / code docstrings / cross-references updated to the new paths.

- Consolidated `_load_curated_hosts` between the two NE samplers — both now import the canonical `curated_ne_hosts()` from `logic/db.py` (#332 / CONS-001). Drops ~30 duplicated lines and means a future NE-aware sampler (e.g. ping / SNMP) only adds to the canonical helper.
- New `_format_provider_test_summary()` in `main.py` keeps the Pulse + Beszel test-connection response shape identical (#334 / CONS-003). Webmin and Portainer keep their bespoke summaries; future `{hosts: {...}}`-shaped providers should reuse the helper.
- `README.md` ref updated from `notes/note_authentik.txt` to `notes/guidelines/authentik.md` (#335 / DEAD-001).
- Replaced operator-private hostnames in shipped docs and code comments with example.com placeholders (#337).

### Changed

- `Settings → Portainer → Test` now validates the configured endpoint id, not just `/api/status` (#335 / DEAD-002). Test now probes `/api/endpoints/{endpoint_id}` after the status check; success message reads `OK — Portainer X.Y.Z, endpoint <Name> reachable`, and a misconfigured endpoint id surfaces as `endpoint X not found on this Portainer` instead of failing silently until the next gather. Falls back to the saved `endpoint_id` when the form's value is blank.

### Added

- Host-drawer charts now show a subtle `Updated Xs/m/h ago` freshness hint beside the time-range picker (#338). Stamped on every successful chart fetch and ticks every second so the seconds digit counts visibly. Hidden until the first response lands.

### Changed

- Show Debug + SSH-run toggles in the host drawer now scroll the just-expanded body to the top of the drawer viewport (#339). Walks up to the drawer's scrollable ancestor explicitly and sets `scrollTop` so Safari (which sometimes scrolled the page instead of the drawer with bare `scrollIntoView`) tracks correctly.

### Fixed

- Admin → Hosts / Host groups action bar now matches the editor section's width AND pins correctly to the viewport bottom while scrolling (#340). Three-iteration fix: introduced `--app-footer-clearance: 48px` token to clear the sticky footer, then switched `.hosts-config-actionbar` from `position: fixed` (which overshot the section because the admin layout nests it inside a 1100px-max-width `.page-layout` with a 220px sidebar) to `position: sticky; bottom: var(--app-footer-clearance)` so the bar inherits the section's natural in-flow width. Sticky was silently broken by `html, body { overflow-x: hidden }` — that property promotes html/body to a scroll container, which sticky descendants pin to instead of the viewport. Swapped to `overflow-x: clip` (clips overflow without establishing a scroll container) so sticky operates against the viewport as expected.

- SSH terminal modal: xterm cols/rows now match the modal's actual dimensions on first open even when xterm's `FitAddon.proposeDimensions()` silently returns `undefined`. New `measureAndResize` helper tries FitAddon first; if `term.cols` stays at the default 80, falls back to a manual `getBoundingClientRect()` measurement using known cell metrics (~7.85px × ~17.5px per cell at 13px Menlo / Consolas / DejaVu Mono) and calls `term.resize()` directly. Helper runs on rAF + 50/250/600/1200ms `setTimeout`s + a `ResizeObserver` + the WS `ready` control frame.

- Login-page logo no longer shows a white halo at the rounded corners (#336). `static/login.html` swapped from the rasterised `icon-512.png` to `omnigrid.svg`, and `.login-logo` lost its redundant `background: var(--surface)` fill. The SVG renders with crisp anti-aliasing at any zoom level; favicon keeps the PNG for universal browser compat.

- SSH terminal modal: xterm cols/rows now match the modal's actual dimensions on first open. The initial `fit()` was running before the flex-1 `.terminal-host` had its layout committed, so xterm fell back to the default 80×24 and the shell wrapped mid-line. Fit now fires through a belt-and-braces staircase — double `requestAnimationFrame` + `setTimeout` retries at 50 / 250 / 600 ms + a `ResizeObserver` on the host element + a final refit when the WS `ready` control frame lands. `fit.fit()` is idempotent so the overlap is a no-op when an earlier pass already produced the right size.

### Changed

- Host drawer "No NIC activity" hint now branches on whether node-exporter is in play, not on whether Beszel is mapped (#321). Hybrids that run BOTH a Beszel agent (reporting zero NICs) AND an NE exporter (with real net data) — opnsense was the canonical example — now see the NE-flavoured wording (loopback / Docker bridges / veth pairs excluded from totals) instead of the Beszel "set NICS=eth0" hint, which was misleading because NE was the source the operator expected to fix. Pure Beszel hosts still see the NICS hint.

- NE-only host drawer: Disk I/O + Network charts now distinguish "no activity in window" from "node-exporter doesn't expose this collector" (#327). Backend `/api/hosts/history` (NE path) now returns a `collectors` dict — `{disk_io, net, fs, mem, cpu}` booleans recording whether any sample in the window held a non-null value for each metric. Frontend gates a new "Enable the diskstats / netstats collector" empty-state ahead of the existing idle copy so hosts whose exporter is permanently missing the collector get remediation guidance instead of a wait-and-see message. New `hosts_extra.collectors_missing.*` i18n family.

### Added

- FreeBSD Disk I/O support for NE-only hosts (#331). `parse_disk_counters` now falls back to `node_devstat_bytes_total{device,type}` when the Linux `node_disk_*` family produces no eligible devices, so opnsense / pfSense / TrueNAS / FreeBSD hosts populate the Disk I/O chart from the same sampler pipeline. FreeBSD-specific exclusions: `pass*` (SCSI passthrough), `md*` (memdisk), `cd*`. Linux pass takes precedence when both families are present; smoke test covers parse + rate + precedence.

- Admin → Host groups tab: pagination + sticky action bar mirroring the Hosts editor (#328). Page size persists to localStorage; Add / Collapse all / Save / scroll-to-Top stay pinned to the viewport on long lists. Action bar repositioned `position: fixed; bottom` so it's visible from page entry instead of only after scrolling past its natural position; new `.hosts-config-page-bottom-pad` class gives both editors a 80px bottom gutter so the fixed bar can never obscure the last row.
- NE-only host Disk I/O chart now populates from `node_disk_{read,written}_bytes_total` counters (#319). New `parse_disk_counters` in `logic/node_exporter.py`, `host_metrics_samples` table gained `disk_read_bps` / `disk_write_bps` columns, sampler tracks rates independently from net (a disk subsystem reboot doesn't drop net rates and vice-versa). Hosts whose exporter has the diskstats collector enabled show real I/O after ~10 minutes (two sampler ticks).
- `CHANGELOG.md` (this file) at the repo root, in Keep-a-Changelog format, with `[Unreleased]` + `[1.0.0]` baseline blocks. Operator-facing release notes now live here instead of being scattered across `notes/note_todo.txt` (#330).

- `notes/RELEASE_PROCESS.md` — operator runbook covering per-digit SemVer semantics, daily PATCH cadence, periodic MINOR cuts, rare MAJOR breaking-change ritual (#330).

- `DB_TYPE` env var — scaffolding for multi-database support (#315).

- Services row: colour cleanly + always show "0 failed" (#314).

- Pagination for the Admin → Hosts editor (122 hosts → 200+ projected) (#311).

- Service summary in HOST DRAWER (#302 shipped) (#302).

- Beszel `systemd_services`: paginate + add per-system match diagnostic (#308 follow-up) (#312).

- Bandwidth chart shipped (#303).

- Load-average chart shipped (1m / 5m / 15m) (#301).

- Disk I/O chart shipped (#300).

- Net In + Net Out combined into one chart shipped (#299).

- ARCH-004: surface SESSION_SECRET-auto-generated warning to admins (#290).

- Kaonmedia brand icon added (#277).

- Samsung re-org: clean wordmark for `samsung`, corporate mark to `samsung-electronics` (#276).

- Humax brand icon added (#275).

- Brand icons batch — 14 new icons + keyword wiring (#243).

- `Show children` parent dropdown adjustment + Expand all / Collapse all bulk buttons (#225).

- Asset type ShortName field name confirmed + backend exposes `type_short` (#223).

- Host-group range error message wasn't showing (#218).

- All admin tabs use Save button + show "Unsaved" indicator (Apprise / Open-Meteo / Portainer / SSH) (#206).

- `+ Add sub-group` parent dropdown didn't reflect the chosen parent (#205).

- Type-short detection widened + diagnostic added (#199).

- "+ Add sub-group" quick button on parent host groups (#196).

- Per-service "enabled" master switches for Apprise, Open-Meteo, Portainer, SSH (#194).

- Smarter range pre-fill on +Add host group (#173).

- `hostStatsSourceEnabled()` field name typo (#157).


### Changed

- Version model switched back to SemVer `MAJOR.MINOR.PATCH` after a brief stint with the `MAJOR.MINOR`-only model. CI auto-bumps PATCH on every deploy; MINOR/MAJOR remain operator-controlled (#329).

- Fresh full-code-review pass — `notes/code_review_2026-04-26.txt` written (#325).

- Persist Hosts editor page across reloads / tab nav (#320).

- Disk I/O chart hidden for NE-only hosts (#318).

- Admin → Config tab — UI override for the 6 process-level tunables (#317).

- Per-host historical graphs from node-exporter (Prometheus/Grafana-lite path for NE-only hosts) (#273).

- Mobile topbar — utility belt merged into header flow + language above SYNC (#309).

- Backend: pull `systemd_services` data from Beszel's PocketBase collection (#302 follow-up) (#308).

- Swap-usage chart in host drawer (Beszel) (#307).

- Brand icons: HDHomeRun + J-Tech Digital + Nixplay (#310).

- Multi-line chart legend values no longer all-red (#305).

- Date-range filter on host drawer charts now triggers refetch (#304).

- Mobile topbar — avatar lifts up to row 1, clock+weather take their own row (#298).

- Mobile topbar phase 1 — no more horizontal page scroll on iPhone (#293).

- Hosts toolbar + Nodes header wrap cleanly on mobile (#294).

- Profile → Topbar widgets prefs follow the dirty-pattern (no auto-save on toggle) (#297).

- Per-user UI prefs sync (cross-device) (#296).

- ARCH-003: weekly npm audit + node_modules served via allowlist (was wildcard mount) (#292).

- Hosts subtitle reflects actual stats picker + polling cadence honors it (#289).

- All 4 admin-tab dirty flags unified to smart-getter pattern (#286).

- DOM "Password field is not contained in a form" warning silenced (#284).

- Stack header "Update stack" button hides when stack is expanded (#281).

- Network drawer: filter Docker / k8s / Proxmox internal interfaces behind a toggle (#271).

- Hidden-hosts count badge on the "Hide hosts without agents" filter (#272).

- Empty groups + sub-groups now HIDDEN when "Hide hosts without agents" filter is on (preference reversed from #279) (#282).

- Asset Inventory dirty pill unified with other admin tabs (#285).

- UX-001: stale-data markers visible in UI (#259).

- Open-Meteo Save button moved below the URL input — matches Apprise layout (#288).

- **Code-review compliance batches** (closed all of: UX-001 stale-data markers + UX-002 skeleton placeholders + UX-003 empty-state hints + UX-005 / UX-008, every CSS-001 to CSS-032 palette tokenization violation, every remaining I18N-* violation from the 2026-04-25 review, plus a sweep of bugs BUG-002 / 003 / 004 / 005 / 007 / 008 / 009 / 011) (#245, #249, #254, #255, #259–#265).
- Interactive SSH terminal modal — admin-only xterm.js viewport over WSS to a backend asyncssh PTY (#160).
- Asset-inventory integration — host rows joined against an external asset API for model / serial / location, with autofill button + dirty-pill UX (#161, #168, #176, #192, #203).
- Cross-provider snapshot fallback — provider outages no longer blank the page; cached `nodes_info` survives, stamped with `_stale_fields` so the SPA can age values out (#222).
- Hosts page — row expansion converted to slide-out drawer; collapsed-row headers gain SSH state dot + brand icon; provider chips suppress globally-broken providers and turn red when an enabled+mapped provider fails (#217, #230, #274, #278, #280, #295).
- Host Groups editor — collapsible children, NUMBER input moved to the natural Tab-order column, group heading rendered as `<number>. <name>`, optional `number` field for display-prefix labelling, Tab skips Move ↑/↓ + Delete fixed, duplicate-id check (#163, #189, #219, #226, #231, #241, #268).
- Admin → all tabs — master-toggle treatment unified: child controls disable when the master is off; Apprise / OIDC / Portainer / SSH / Open-Meteo / Asset Inventory all share the same dirty pill + disabled-form behaviour (#201, #214, #215, #224, #234, #285, #288).
- Topbar — clock + weather repositioned LEFT of the user avatar; copy-filtered-logs button on Admin → Logs; one-shot "no EFS" warnings to cut Beszel log spam (#170, #171, #181, #183).
- Brand-icon library expansion — ~30 new vendor icons added across multiple batches (Aqara, ASUS, Alienware, Amazon Fire TV, Bose, Chromecast, Cisco, Gigabyte, GL.iNet, Hisense, HP family, IKEA, Kindle, Lubelogger, Motorola, Nest, Rachio, Reolink, Roku, Samsung, SanDisk, Sensibo, Somfy + typo aliases, Squid, Synology, Ubiquiti family, WD, WD-TV, Xiaomi). Plus an icon-resolver registry that kills the noisy `<unknown-slug>.svg` 404 spam from missing icons (#164, #167, #174, #175, #177, #178, #180, #184–#188, #191, #197, #202, #208, #210, #211, #212, #220, #227, #228, #229, #233, #244, #246, #250).
- Host drawer polish — explicit 12-col grid with `col-span-6` cards, slide animation switched from Alpine x-transition to a CSS keyframe, Net In / Net Out chart heights match CPU / Memory / Disk, debug panel widths normalized (#248, #252, #267, #270, #323).
- Schedules — daily / weekly / monthly schedules now actually fire (grace window added — they were silently no-ops near the day-boundary) (#198).
- Sub-group filter behaviour — empty groups + sub-groups hide when "Hide hosts without agents" is on (#279 / #282 — preference reversed once mid-cycle, final state matches operator intent).
- Hidden-hosts count badge on the "Hide hosts without agents" filter (#272).
- Beszel `"paused"` status now correctly maps to `"down"` (#269).
- Asset Inventory autofill strips FQDN domain suffix from id so `ssh_fqdn_suffix` still applies cleanly at SSH-resolve time (#203).
- Removed the `_deriveTypeShort` JS acronym fallback — asset ShortName is the only source of truth (#232).


### Fixed

- Deploy bash now collapses any legacy `MAJOR=2` VERSION.txt value to `1.0.<counter>` in a one-shot migration instead of double-bumping. Recovers a previous mis-migration that produced `2.255.1` and ensures the next deploy normalizes to `1.0.255` (#329).

- Mobile filter bar (Stacks / Services / Nodes views): the divider between the health and status filter groups no longer strands on its own row mid-wrap — `hidden sm:inline-block` drops it on phones. `.filter-chip` padding tightens to `4px / 11px` font under `max-width: 640px`, recouping one to two wrapped rows on iPhone-width screens (#326).
- NE-only host Disk I/O chart was stuck on perpetual `0 B/s` for NAS / RAID boxes (Synology, TrueNAS, OPNsense). `dm-*` and `md*` devices are no longer excluded from `parse_disk_counters` totals — they ARE the user-facing volumes on those hosts. Empty-device-list now returns `None` totals (instead of `0`), so the sampler stores NULL rates and the chart shows "no data" instead of a misleading flat-zero line (#324).

- Host drawer debug panel — consistent panel widths (#323).

- Admin → Debug tab: smart-getter dirty pattern + Save button (#322).

- Mobile topbar regression: theme + hotkeys pushed down by stats picker (#313).

- Hosts mobile-card layout fixed — provider chips no longer crush the host name (#295).

- Per-host provider chips: suppress globally-broken providers (#280).

- CPU/Mem/Disk chart Alpine errors fixed (SVG <template> doesn't work) (#283).

- Host drawer: text-compaction fix (img_3.png) (#258).

- Drawer CSS regression fix (img_4.png) (#262).

- UX-005: Asset Inventory dirty indicator + Profile i18n leak fix (#263).

- UX-001 follow-up: stale markers extended to Hosts main grid (#265).

- ARCH-001 / BUG-003 close-out: duplicate-id check on host_groups (#268).

- Host status precedence + per-provider chip coloring fixes — VALIDATED ("yes in red, the beszel chip should be red") (#278).

- Per-host provider chips turn red when an enabled+mapped provider fails — VALIDATED via #278 (#274).

- Hotfix: `/api/items` 500 from UX-003 scope bug (#266).

- Stats / sparks self-diagnostic + `app().statsDebug()` console helper (#251).

- Code-review bugs swept — BUG-002 / 003 / 004 / 005 / 007 / 008 / 009 / 011 (#245).

- Multi-fix turn from the code-review report (#240).

- Optional `number` field on host groups for display-prefix labelling (#219).

- Real fix for #205 — sub-group parent dropdown still didn't work (#216).

- Nodes view: CPU sparkline invisible on idle nodes — clip-at-bottom-edge fix (#209).

- Asset autofill — strip FQDN domain suffix from id (#203).

- Daily / weekly / monthly schedules never fired — grace window added (#198).

- "Collapse all" button visual fix (#195).

- Asset type prefix on Hosts-view host titles (#192).

- Host Groups editor typing lag — root cause + fix (#189).

- Duplicate debug-panel toggle removed from Admin → Hosts (#172).

- Clock + weather positioning fix (#171).

- Typing the first character into a host row's ID collapses the panel — FIXED (again) (#169).

- Admin toggle for host-drawer Debug data panel (#162).

- Root-cause fix (#155).


### Internal

- ARCH-002: schema-migration infrastructure (logic/migrations.py) (#291).

- Consolidations CONS-003 / CONS-004 / CONS-007 from the code review (#255).

- Host drawer width + value-wrap polish + stats diagnostic + slide cleanup (#248).

- i18n violation cleanup pass — addressed every entry in code_review_2026-04-25.md's I18N-* section.

- `.gitignore` — block agent-memory paths under `static/` (#238).

- Cleanup polish — `actions.close` i18n key (#236).

- Frontend reverts/cleanup follow-ups from this session (#221).

- `.claude/settings.local.json` simplified to wildcard auto-allow (#213).

- SSH resolve + status log spam — signature-based dedupe (#182).

- Topbar split into two rows (Option A) (#165).

## [1.0.0] — 2026-03-21

Baseline release — first version under the SemVer + `CHANGELOG.md`
cadence (see `docs/RELEASE_PROCESS.md`). The changelog story starts
here; implementation detail for everything that shipped before this
baseline lives in `notes/note_todo.txt` under the `## Done` block,
keyed by stable `#NNN` TODO IDs.

[Unreleased]: https://git.example.com/OmniGrid/compare/v1.1.0...HEAD
[1.1.0]: https://git.example.com/OmniGrid/compare/v1.0.0...v1.1.0
[1.0.0]: https://git.example.com/OmniGrid/releases/tag/v1.0.0
