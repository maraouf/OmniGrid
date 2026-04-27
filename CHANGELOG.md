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

### Changed

- Deploy success notification title now includes the new version number — `✅ OmniGrid deployed v1.X.Y — service back up`. The bump step's `steps.version.outputs.version` is already published earlier in the same job; the "Notify on successful restart" step reads it and builds the title around it. Body shape unchanged (service / commit / branch / reason / run URL). Defensive blank-guard falls back to the previous title shape if the version output is ever empty (#414).

### Added

- WebAuthn / FIDO2 passkeys as a 2FA factor alongside TOTP (#345). Local-auth users enrol passkeys (1Password, iCloud Keychain, Bitwarden, hardware keys like YubiKey / Titan / Solo, platform authenticators like Touch ID / Windows Hello / Android fingerprint) from Profile → Security and pick "Use a passkey" at the second-factor login screen. Either method satisfies the 2FA gate; users with both enrolled choose at sign-in. New table `user_credentials(user_id, credential_id BLOB UNIQUE, public_key BLOB, sign_count, transports, friendly_name, created_at, last_used_at)` (additive, no migration). Backend wraps Duo's `webauthn>=2.0` package via `logic/webauthn_helper.py`; routes `GET /api/me/webauthn`, `POST /api/me/webauthn/register-{start,finish}`, `DELETE /api/me/webauthn/{id}`, `POST /api/local-auth/webauthn-{start,finish}`. RP ID derived per-request from `request.url.hostname` so dev (`localhost:8088`) and prod (NPM-fronted domain) work without settings. Authentik users skip every passkey path; bearer-token requests bypass entirely. Origin verification + sign-counter monotonic check on every assertion. Operator-facing runbook at `docs/guidelines/passkeys.md`. Admin → Users gains a "Passkeys" count column. Recovery codes from #345 still work as a third fallback (#381).
- Two-tab Admin → Logs viewer + new `prune_logs` scheduler kind. The existing in-memory ring buffer view becomes the "Live" sub-tab; the new "Files" sub-tab lists every persistent daily log file under `/app/data/logs/` with size + modified time, plus per-file Download and View buttons. The viewer live-tails the selected file every 5s (toggleable). Three new admin-only routes: `GET /api/admin/logs/files`, `GET /api/admin/logs/files/{name}?tail=N`, `GET /api/admin/logs/files/{name}/download`. New scheduler kind `prune_logs` runs the same sweep the lifespan pruner does, on whatever cadence the operator schedules — useful for ad-hoc one-shot cleanups (#425).

- Persistent logs on disk + configurable retention. Every stdout/stderr line that lands in the in-memory ring buffer is now also appended to a daily file at `/app/data/logs/omnigrid-YYYY-MM-DD.log` (host-side: `/opt/omnigrid/data/logs/`). Format follows industry-standard log shape — ISO 8601 UTC timestamp + uppercase fixed-width level (ERROR / WARN / SUCCESS / INFO, classified by content scan matching the SPA's `logSeverity()`) + message. Files are parseable by Promtail / Vector / Fluent Bit / `tail -F` / grep without configuration. New tunable `tuning_log_retention_days` (Admin → Config; default 7 days, range 1–365); lifespan-managed pruner sweeps the directory hourly and deletes any `omnigrid-YYYY-MM-DD.log` whose filename date is older than N days. Best-effort writes — disk-full / permission failures don't break the in-memory tee (#424).

- `/api/ops` poll cadence is now a tunable (Admin → Config → "Ops poll cadence (ms)"). Backed by `tuning_ops_poll_interval_ms` (default 1500 ms, range 250–60000); surfaced to the SPA via `/api/me`'s `client_config.ops_poll_ms`. `pollOps()` resolves the value per-tick so a Save in Admin → Config takes effect on the next `/api/me` round-trip without a restart. Lower for snappier UI feedback after a button click, higher to cut idle traffic on a long-running SPA tab (#417).

### Changed

- Admin tab primary action buttons unified — every Save / Create button across Admin tabs now reads "Save" / "Create" instead of the verbose per-tab labels (Save OIDC settings / Save tunables / Save version / Save groups / Save SSH settings / Create schedule / Create token / Save Portainer settings). Cross-tab consistency requested by the operator. Markup keeps the per-tab i18n keys intact; only the English values changed, so translators will update their bundles on the next pass (#429).

- Admin → Logs → Files tab now renders log files with the same colourisation as the Live tab: tinted timestamps, severity-coloured rows (red ERROR / amber WARN / green SUCCESS / default INFO), and `[beszel]` / `[pulse]` / `[hosts]` etc. tag accent chips. Lines are parsed via the canonical `<ISO ts> <LEVEL> <body>` regex matching the file format from `logic/logs.py:_persist_line` (#427).

### Added

- Temperature chart in the host drawer for hosts whose Beszel agent emits thermal sensors (e.g. Raspberry Pi `cpu_thermal`, Intel/AMD `package_id_0`, NVMe `nvme_composite`). Backend: `extract_stats` exposes a new `host_temperatures: {sensor: celsius}` dict; the per-point history shape gains `temps` (full sensor dict) and `temp_max` (peak across all sensors — the chart line). Frontend: new chart card after Swap, gated on the dict being non-empty; live header lists every sensor sorted hottest-first. Hosts without thermal data hide the card entirely. node-exporter `node_hwmon_temp_celsius` integration deferred (#437).

- Admin → Sessions table now shows the 2FA method each session was authenticated with (Password / Password + TOTP / Passkey / OIDC (Authentik) / Bootstrap admin) plus the login time. Schema ALTER adds `sessions.auth_method TEXT NOT NULL DEFAULT 'password'`; `issued_at` was already on the row. Every cookie-issuing call site now passes the right method tag (#436).

- Admin master toggle for passkey enrolment + login (`passkeys_allowed`, default true) — mirrors the existing `totp_allowed` toggle. Surfaced in Admin → Authentication as "Allow users to enrol passkeys". When OFF: Profile hides the "Add a passkey" button, login flow drops `webauthn` from the second-factor `methods` list (already-enrolled passkeys are NOT offered for login), and `register-start` / `webauthn-start` routes return 403 as defence-in-depth. Existing rows stay in the DB so flipping the toggle back on restores login. Same gate semantics applied to TOTP — disabling `totp_allowed` now also blocks login-with-TOTP for already-enrolled users (the previous "existing enrolments stay active" behaviour was misleading and is no longer accurate). i18n `admin.config.totp.passkeys_allowed_label/_hint` + `settings.profile.passkeys.disabled_by_admin` (#432).

### Fixed

- Stacks page rendered empty after #418's reconcile helper landed — `_reconcileById` assumed every entry carries an `id` field but stacks are keyed by `name`. Helper now takes a `keyField` arg (default `'id'`); the stacks call site passes `'name'`. Items unchanged (#436).
- Pulse `extract_node_stats` now infers `host_arch = "x86_64"` (normalised) when the kernel string ends with `-pve` so PVE-only hosts (no Beszel / NE agent on the hypervisor itself) no longer display blank arch (#430).
- `_get_failure_state` (`logic/host_metrics_sampler.py`) docstring cleaned up — internal helper retains column parity with the API-facing `_failure_state_for_host`, including the post-#461 `last_failure_ts` field with `first_failure_ts` fallback for pre-schema-add rows (#431).
- `_normalize_code` (`logic/asset_inventory.py`) no longer accepts pseudo-negative codes like `Ex-1686`. Dropped the `.lstrip("-")` so `.isdigit()` rejects negatives correctly; eliminates a false-match against `_ERR_NO_RECORDS = "1686"` (#432).
- Webmin `extract_package_updates` element-style fallback scopes its tag walk to the first `<updates>` / `<packages>` / `<pkglist>` parent's direct children when one exists; falls back to the legacy `root.iter()` walk only when no scoped parent is present. Custom themes with nested `<update>` / `<package>` / `<pkg>` tags in unrelated page sections no longer inflate the count (#433).
- `metrics.populate_from_cache`'s pre-seeded `(status, type)` cartesian product now drives off `ITEM_STATUSES` + `ITEM_TYPES` tuples in `logic/gather.py`. Adding a new status / type is one edit in gather.py; Grafana queries against the new combination match a zero-seeded series automatically (#434).
- Hosts editor search input now debounces at 150ms (`x-model.debounce.150ms`). `filteredHostsConfig()` walks → 1 per keystroke-burst-end instead of 1 per char on a 500-host fleet — smoother for fast typists, no felt-responsiveness change (#435).
- `logic/webmin.py:_scrape_net` now walks every NIC-table in the HTML UI and de-dups by name (first-seen wins). Webmin 2.x split-tables under separate `<h3>` sections (physical / virtual / VLAN NICs) now contribute to the union instead of being silently dropped after the first table (#424).
- ENH-010: `_normalize_code` in `logic/asset_inventory.py` now strips `Ex` / `ERR_` / `Error_` prefixes (case-insensitive) so downstream `[Ex{code}]` rendering doesn't double-prefix when the upstream API emits `Error_3537` instead of the canonical `Ex3537` (#425).
- ENH-011: Backup-code consumption (`logic/totp.py:consume_backup_code`) is now O(1) via a SHA-256 hash index. `encrypt_backup_codes` writes a `code_hash` alongside each encrypted entry; consume looks up by hash, skipping decrypt entirely. Pre-#426 entries fall through to the legacy decrypt-and-compare loop on first touch and get their `code_hash` backfilled lazily (#426).
- ENH-012: `POST /api/hosts/{id}/resume-sampling` now also clears the SSH + Webmin auth cooldown timers keyed on the host. A single resume click now recovers from the all-three-providers-paused-on-same-host case instead of leaving the cooldowns ticking. Endpoint response gains `cooldowns_cleared: <int>` (#427).
- ENH-013: Pulse bytes-vs-GiB unit detection now version-aware. New `_fetch_version(client, base_url, token)` probes `/api/version` at probe-time; `_pulse_uses_bytes(base_url)` consults a known-version-prefix map (`("4.", "5.")` → bytes); the magnitude heuristic stays as a silent fallback when the endpoint is 404'd by older Pulse builds. Eliminates the misclassification of <10MB volumes on embedded LXC guests (#428).
- ENH-014: `seed_default_schedules` now wraps its seed sequence in `BEGIN IMMEDIATE` so SQLite serialises concurrent first-boot calls (`_lifespan` + first `gather()`). After lock acquisition the seeded-flag gate re-runs inside the transaction so the second caller short-circuits cleanly. `BEGIN IMMEDIATE` failure (autocommit-mode test fixtures) falls through to the per-row IntegrityError catch (#429).
- ENH-004: `omnigrid_registry_errors_total` / `omnigrid_registry_latency_seconds` `registry` label is now bucketed through `_classify_registry()` in `logic/registry.py`. Known public registries (Docker Hub, GHCR, GCR, Quay, LSCR, MCR, ECR) map to themselves; everything else collapses to `private`. Prometheus cardinality is bounded regardless of how many private mirrors an operator pulls from (#419).
- ENH-005: `b64uDecode` in the login page's WebAuthn helper now validates input shape (length, base64url charset) before `atob` and throws operator-readable errors that distinguish "server payload regression" from "hardware key rejected" (#420).
- ENH-006: `filteredHostsConfig()` is now memoised on `(filter, length, sortedOrder.length)` so repeated access in the same Alpine tick is O(1) instead of re-walking the whole array. Saves ~1000 walks/keystroke on a 500-host fleet (#421).
- ENH-007: Beszel `_warned_no_mounts` set replaced with a 1024-entry FIFO-evicting `OrderedDict`. Warn-once semantics survive; memory stays bounded on fleets with rotating hostnames (#422).
- ENH-008: `groupedHosts()` now memoises on `(hosts.length, groups.length, hostGroupsRevision, filter, hideUnconfiguredHosts)` and uses a sorted-by-`range_start` array for binary-search lookup of each host's parent bucket (O(log N) per host instead of O(N)). With 500 hosts × 30 groups: ~15k comparisons per render → ~2.5k on the first call + O(1) on subsequent reads in the same tick (#423).
- ENH-001: Asset inventory `verify_tls` is now an operator-controlled setting (default True) instead of being hardcoded in three places. Settings round-trip through `asset_inventory_verify_tls` (DB) → `asset_inventory.verify_tls` (API) → `assetForm.verify_tls` (UI). New checkbox in Admin → Asset Inventory; both `refresh_cache` call sites + `_run_asset_inventory_refresh` consult the live setting on every refresh — homelab operators with self-signed asset API endpoints can opt out without monkey-patching (#417).
- ENH-003: `refresh()` (the items + stacks auto-refresh poll) now uses an in-place `_reconcileById` helper instead of wholesale-reassigning `this.items` / `this.stacks`. Drops gone rows, updates/inserts by id, reorders in place to match server-side sequence — keeps Alpine from tearing down each row's checkbox state, `<details>` open/closed state, and inline-style nodes on every poll (#418).
- BUG-009: SSH password resolver desync — when the recorded `password_source` was `per_host` / `sub_group` / `main_group` but that record's password was empty, the resolver fell through to the global password while the audit row still recorded the stale source classification. The resolver now downgrades `password_source` to `"global"` (in both `resolved` + `base_result`) when the fallback fires; logged in the `[ssh]` audit so future incident review sees the actual auth source (#415).
- BUG-010: `_get_host_provider_state` cache key now includes a SHA-256 hash of the credential blob (Beszel / Pulse / Webmin / node-exporter URLs + identities + passwords + verify_tls flags). Changing only a credential field via raw SQL (or any path that bypasses `api_set_settings`'s whitelist) auto-busts the cache instead of serving stale data for up to 10s. The whitelist's explicit `invalidate_host_provider_cache` call remains as the primary path; the hash is defence-in-depth (#416).

- Temperature chart polylines were invisible AND y-axis labels rendered out of bounds. Two distinct bugs in the same surface: (1) Alpine `<template x-for>` does NOT work inside `<svg>` — the SVG-namespace template element isn't a real `HTMLTemplateElement` so Alpine threw when iterating per-sensor path elements (same gotcha already documented for the percentage charts which precompute path data outside the loop). Refactor: `hostTempChart()` now returns a `dByColor` dict keyed by palette token; the chart renders five fixed `<path class="metric-line" style="color: var(--token)">` elements and the helper concatenates each sensor's path data into the slot for its assigned token. `.metric-line { stroke: currentColor }` resolves the colour through CSS inheritance. (2) Y-axis returned 4 labels distributed via `justify-content: space-between` at 0%/33%/66%/100% of the axis div — the inner two didn't align with anything meaningful on the chart. Trimmed to 3 labels (top / mid / bottom) so the rhythm matches `yAxisPercent()` on the percentage charts (#443).

### Changed

- Scheduler-timezone fallback now surfaces in `/api/me`'s `client_config.scheduler_tz` (`{configured, resolved, fallback}` shape) and the admin Schedules tab renders a danger-tinted warning banner below the TZ input when `fallback: true` (configured value isn't a valid IANA name and the scheduler is using container-local time despite the operator's intent). Backed by a new `logic.schedules.scheduler_tz_state()` helper that validates via `ZoneInfo`. New i18n key `scheduler_settings.tz_fallback_warning`. ENH-017 from `notes/code_review_2026-04-27.txt` (#460).

- Host drawer's "Sampling paused" banner now shows a sub-line "Last probe attempt N seconds/minutes/hours ago — not yet retried since pause." so the operator can spot a paused host whose failure signal may have already cleared without the noise of an immediate Resume click. Schema gained `host_failure_state.last_failure_ts REAL` (additive ALTER); `_record_failure` populates it on every failure tick; `_failure_state_for_host` exposes it; SPA's `CURATED_REFRESH_FIELDS` whitelist propagates it; new helper `hostLastFailureAge(h)` picks seconds / minutes / hours i18n keys based on age. New i18n keys `hosts_extra.permanent_fail.last_error_age_{seconds,minutes,hours}`. ENH-018 (#461).

- `_run_prune_logs` schedule history rows now record the resolved `days` value in `target_name` (`"42 log file(s) (days=14)"`) so audits show whether the param-override or the tuning fallback fired, and what the value was after clamping. Suffix drops cleanly when `days` resolution failed before clamp. ENH-015 (#458).

- `_get_host_provider_state` cache key in `main.py` now includes the active-sources tuple. Previously a settings change that flipped `host_stats_source` would serve stale provider state for up to the 10s TTL via paths that didn't run the explicit `invalidate_host_provider_cache()`; now the cache hit gates on key equality alongside the TTL so a sources change auto-busts. Defence-in-depth — explicit invalidation still runs for instant feedback. ENH-009 (#456).

- `/api/hosts/one/{host_id}` now accepts `?force=true` to bypass the 10s provider-state cache, mirroring the parallel param already on `/api/hosts/list` (#347). The SPA's `refreshHostRow(id, opts)` takes a `{ force: true }` option and the two highest-value Save / Resume call sites use it: (a) `saveHostsConfig` triggers `loadHosts(true)` so a fresh probe runs after editing curated aliases / SSH config, and (b) `resumeHostSampling` force-refreshes immediately after the operator un-pauses a host so the first post-resume probe lands without waiting out the TTL. The 15s polling path stays cache-friendly. ENH-003 from `notes/code_review_2026-04-27.txt` (#455).

- i18n violations sweep from the 2026-04-27 code review — every hardcoded English string flagged on the SPA + login page now flows through `t('key.path')` (#445). Sites covered: Nodes view provider chip / prune button tooltips, Hosts mobile-card metric labels, Hosts desktop-table headers (Host / Platform / CPU / Memory / Disk / Uptime / Cores / Arch / Kernel), the host external-link `:title`, four Hosts empty-state blocks (all_disabled / no_provider_match / no_provider_enabled / no_data) rewritten to `x-html` with placeholders, Pulse + Webmin Host-stats settings panels (~25 strings: URL labels, placeholders, hint paragraphs, API-token / Verify-TLS / Test-connection labels, alias hints), Admin → Hosts toolbar (heading + Discover / Import / Test-all buttons + tooltips, empty-state, filter no-match + Clear), and ~12 Hosts-editor JS toast call sites. Two native `confirm()` dialogs (unsaved-changes guard in `loadHostsConfig`, replace-vs-merge in `importHostsConfig`) replaced with SweetAlert so they translate / RTL-flip / theme-match the dark surface tokens. Login page tab title (`<title>OmniGrid — Sign in</title>`) gains a `data-i18n-title` attribute; `login.js:applyI18nDom` now walks `[data-i18n-title]` and updates BOTH the title element AND `document.title` so the tab label refreshes immediately on language change.

- Audit log (Admin → History) now uses server-side paging instead of fetching the whole filtered set up to a 500-row cap and rendering the lot client-side (#446). Backend's `/api/history` accepts `?offset=&limit=` and returns `{history, total, offset, limit}` (`_history_query` opt-in `with_total=True` emits a paired `SELECT COUNT(*)` for the same WHERE clause). Frontend renders one page at a time with a « First / ‹ Prev / page-jump input / Next › / Last » control set + per-page selector (25 / 50 / 100 / 200 / 500). Page + per-page persist to localStorage so refresh returns to the same view; filter changes route through a helper that resets to page 1 before re-fetching. Export endpoints (`/api/history.json`, `/api/history.csv`) deliberately stay un-paged with `limit=5000` — the operator wants the full filtered dataset in the file, not just one page. New i18n keys under `pagination.*`: `page_n_of_m`, `total_count`, `prev`, `next`, `first_glyph`, `last_glyph`, `per_page_option`.

### Internal

- `_HOST_SNAPSHOT_KEYS` is no longer a hand-maintained tuple drift class. Replaced with an `_is_snapshot_key(key)` predicate (`host_*` prefix OR `key in _BARE_SNAPSHOT_KEYS = {"mounts", "interfaces", "package_updates_count", "package_updates"}`). `apply_host_snapshot_fallback` and `seed_nodes_info_from_snapshots` iterate `snap_data.keys()` through the predicate — any `host_*` field a provider sprouts gets snapshotted AND restored automatically without a parallel whitelist edit. Legacy tuple kept as a compat alias. Eliminates the BUG-001 drift class. ENH-020 (#462).

- `_validate_id_token` in `logic/oidc.py` now logs `[oidc] kid=... not in cached jwks — bypassing cache to refresh` whenever a JWKS cache-bypass refresh fires (key rotation mid-flight). Without the line the recovery path was invisible in Admin → Logs. ENH-016 (#459).

- `_flatten_temperatures` was being called THREE times per point in `logic/beszel.py:fetch_system_history` (once for `temps`, twice for `temp_max`). On a 168-hour history at 1-min granularity that's 30k+ wasted parses per drawer load. Now computed once per point and reused. ENH-014 (#457).

- `.gitignore`: `.claude/worktrees/agent-a65201162a3ca6ffe` was committed earlier as a gitlink/submodule entry, so the existing `.claude/worktrees/*` pattern (which only affects untracked files) didn't suppress it from `git status`. Tightened to directory-level `.claude/worktrees/` and ran `git rm --cached` to untrack the entry without touching files on disk. Future agent worktrees write into the same directory and stay invisible to git (#449).

- Dead-code cleanup from the 2026-04-27 code review (#444). (a) BUG-006 / `logic/host_metrics_sampler.py` — the `if not window:` legacy fallback (and its `host_permanent_fail_window_seconds` `get_setting` read) was unreachable: `tuning.tuning_int(...)` always returns at least the code default. Removed the dead branch and the now-unused `get_setting` import. (b) ENH-019 / `main.py` — `host_permanent_fail_window_seconds` was kept as a SettingsIn field, GET-side resolver row, and POST-side validator branch even after #410 rolled it into `tuning_host_permanent_fail_window_seconds`. UI no longer wrote it; the read-side tuning fallback ignored it. Removed all three sites + the orphaned read in `static/js/app.js`. Operators with a stored legacy row become inert (no readers); no migration needed since #410's tuning system already wins.

- `_HOST_SNAPSHOT_KEYS` whitelist in `logic/gather.py` was dropping real provider-emitted fields, so when a provider went down mid-gather the affected host drawer cards (Load average / Swap / Temperature / package updates) blanked instead of showing the persisted values flagged stale. Three issues fixed together: bare `load_1m/5m/15m` keys were never written by any provider — the providers prefix as `host_load_*`; top-level `network` is similarly never emitted; Beszel-emitted `host_swap_used`, `host_swap_percent`, `host_temperatures` were missing from the whitelist entirely. Snapshot WRITE was unaffected; the fix is purely on the READ-side `apply_host_snapshot_fallback`. BUG-001 from `notes/code_review_2026-04-27.txt` (#440).

- `notify(event=...)` was hardcoding the per-event admin-gate default to `True`, but `_NOTIFY_EVENT_DEFAULTS` explicitly defaults `user_login` to `False` (login traffic is noisy by design). On a fresh deploy where the admin hadn't clicked Save in Notifications, the backend fired user_login notifications while the UI advertised the toggle as OFF. Moved `NOTIFY_EVENT_NAMES` + `NOTIFY_EVENT_DEFAULTS` to `logic/ops.py` as the single source of truth and re-aliased into `main.py` so call sites don't churn; `notify()` now reads the event-specific default from the same map (`NOTIFY_EVENT_DEFAULTS.get(event, True)`). Future events with a `False` default will be honoured automatically. BUG-002 from `notes/code_review_2026-04-27.txt` (#441).

- `_record_failure` in `logic/host_metrics_sampler.py` was sync but reached for `asyncio.get_event_loop()` to schedule the auto-pause notification via `asyncio.ensure_future(..., loop=loop)` — Python 3.12+ deprecates this API outside a running coroutine and 3.14 removes it. Made `_record_failure` async (its callers in `_probe_one` are already inside an async context) so the notification dispatch can use `asyncio.create_task(...)`, which is the supported API since 3.7 and gets the running loop for free inside an async function. Both `_probe_one` call sites switched to `await _record_failure(...)`. No behaviour change to the auto-pause window or notification semantics. BUG-010 from `notes/code_review_2026-04-27.txt` (#454).

- `_run_prune_logs` schedule kind in `logic/schedules.py` accepted an admin-supplied `params.days` without a range check — a negative value silently ran as a no-op (`prune_old_logs(<=0)` internal guard returns 0 with no signal to the operator), a huge value effectively disabled the prune, and a non-int string fell through to the tuning default. Now clamps the resolved value (param or tuning fallback) to `TUNABLES["tuning_log_retention_days"]`'s `[1, 365]` range before calling `prune_old_logs`, matching the Admin → Config validator. BUG-009 from `notes/code_review_2026-04-27.txt` (#453).

- `_validate_id_token` in `logic/oidc.py` was feeding the unverified id_token header's `alg` straight into PyJWT's `algorithms=[alg]` kwarg — relying on PyJWT's own ≥2.0 refusal to honour `alg=none` even when listed. Hardened: new `_ALLOWED_ID_TOKEN_ALGORITHMS` frozenset whitelists asymmetric algorithms from the OIDC core spec (`RS256`/`RS384`/`RS512`, `ES256`/`ES384`/`ES512`, `PS256`/`PS384`/`PS512`, `EdDSA`); any other `alg` (including `none`) is rejected before the `jwt.decode` call. Symmetric algorithms (HS*) deliberately excluded — id_tokens must be signed with the IdP's JWKS-published asymmetric key, never the operator-side client_secret. BUG-008 from `notes/code_review_2026-04-27.txt` (#452).

- `refreshHostRow` in the SPA leaked stale fields when `/api/hosts/one/{id}` omitted a key (BUG-007 / #451). The original `for (k of Object.keys(host)) row[k] = host[k]` loop only ASSIGNED keys present in the new payload; absent keys retained their previous value, so a provider's `host_temperatures` flapping between empty and populated would leave the chart locked on the last non-empty dict. Fix: new module-scope `CURATED_REFRESH_FIELDS` Set listing every probe-derived field (status / failure-state / CPU / mem / disk / swap / temperatures / network / disk-IO / identity / mounts / interfaces / package_updates / load avg / stale markers / host_services). `refreshHostRow` writes each whitelist field explicitly with `(host[k] === undefined) ? null : host[k]` so a missing key collapses to null instead of leaving stale data; a second loop covers any other key the backend chose to include for forward-compat. Curated config fields (`label` / `icon` / `ssh_disabled` / `ne_url` / etc.) stay owned by `loadHosts`'s `CURATED_FIELDS` skeleton path and are deliberately NOT in the new whitelist.

- Temperature card legend chips overflowed the chart's right edge on hosts with many thermal sensors (8 cores). Cause: `.metric-card-head` had no `flex-wrap`, and `.metric-card-stats` was `inline-flex` with no wrapping either, so the chip row grew past the card width and the "+N more" chip got clipped. Fix: added `flex-wrap: wrap` to both, switched `.metric-card-stats` to `display: flex` + `justify-content: flex-end` + `min-width: 0` so chips wrap onto a new header line (right-aligned) instead of bleeding past the card boundary. Two-chip charts (CPU / Memory `Min` / `Max` etc.) keep their existing layout since wrapping only kicks in when the row actually overflows (#450).

- `/api/oidc/test` now respects the in-flight `verify_tls` checkbox from the OIDC settings form instead of always using the saved DB value (#448). Symptom: admin pasted a self-signed-issuer URL, unticked Verify TLS, clicked Test → backend used the SAVED True value and the test failed with a TLS handshake error before the operator could save the new value. `oidc.test_discovery(issuer_url, verify_tls=None)` now accepts an explicit override; `api_oidc_test` passes `body.get("verify_tls")` through; the SPA's `testOidcConnection()` sends `verify_tls: !!this.oidcForm.verify_tls`. Mirrors the `/api/portainer/test` pattern. BUG-005 from `notes/code_review_2026-04-27.txt`.

- Hosts view: parent group labels now render in `--text-dim` (slightly faded) so sub-group labels stand out as the more specific row in 2-level group hierarchies. Sub-group labels keep full `--text` colour. Applied to both mobile-card and desktop-table render paths. No token discipline drift — uses the existing `--text-dim` token. Operator request via `tmp/img_5.png` (#471).

- `host_arch` label now harmonised across every provider extractor. Pre-fix `node_exporter.py` mapped `amd64 → x86_64` inline, but `logic/beszel.py:_derive_arch`, `logic/webmin.py`, and `logic/pulse.py` passed raw values through — two providers reporting the same physical host disagreed (NE saw `x86_64`, Beszel saw `amd64`). New `logic.merge.normalize_arch(s)` helper centralises the alias map (`amd64 → x86_64`, `i386/i686 → x86`, others pass through verbatim); every provider extractor now routes its arch value through it. Future arches harmonise automatically by adding one entry to the helper. BUG-007 from the 2026-04-27 second-pass review (#477).

- `loadHosts` in the SPA reset `this.hosts = []` on BOTH error paths (HTTP non-2xx and network exception), tearing down every row's chart SVG on a single transient failure during the 15s poll. Now only sets `this.hostsError` and leaves the rows alone — the next successful poll reconciles in place per the documented "every polled reactive array uses in-place reconcile, including the error path" rule. Operators see a banner with previous data dimmed instead of a flicker-then-empty page. BUG-008 from the 2026-04-27 second-pass review (#478).

- `/api/hosts/{host_id}/resume-sampling` now validates the path id against the curated `hosts_config` list before clearing the auto-pause row, returning 404 for unknown host ids instead of silently DELETE-ing zero rows. Matches the validation shape of `/api/hosts/one/{host_id}` for endpoint consistency. BUG-005 from the 2026-04-27 second-pass review (#476).

- `logic/tuning.py:tuning_int` now clamps the resolved value to `(_lo, _hi)` from `TUNABLES` on every read (DB / env / default paths all routed through the same `max(lo, min(hi, parsed))` clamp). Pre-fix a raw SQL `INSERT INTO settings` or an env-var typo would flow an out-of-bounds value straight through to the consumer — corrupt DB state could disable a sampler or panic the OPS poll cadence. Per-consumer clamps (e.g. `_run_prune_logs`) become redundant defence-in-depth. New tunables enforce their bounds automatically by adding the `(env, default, lo, hi)` tuple to `TUNABLES`. BUG-004 from the 2026-04-27 second-pass review (#475).

- WebSocket SSH terminal at `/api/hosts/{host_id}/ssh/terminal` now enforces an explicit Origin gate before `accept()` as defence-in-depth against CSWSH. Pre-fix the route validated session cookie + admin role only; the cookie's `SameSite=lax` blocked most cross-site WS upgrades on Chromium / Firefox but subdomain attacks and custom proxy setups could still leak the cookie. Compares `websocket.headers.get("origin")` against `_request_origin(websocket)` (the same helper WebAuthn uses for HTTP routes); mismatch closes with code 4403 + reason "origin mismatch" + a `[ssh] terminal Origin mismatch` log line. Empty Origin (non-browser callers) intentionally allowed since the admin cookie + role gate already rejected unauthenticated callers. `_request_origin` type annotation loosened to accept either `Request` or `WebSocket` (both expose the same `.headers` / `.url` shape). BUG-003 from the 2026-04-27 second-pass review (#474).

- `is_meaningful(False)` returned False because Python's `bool ⊂ int` made `isinstance(False, int)` true and `False == 0` flunk the meaningful-value test. Latent today (no `host_*` field carries an intentional boolean) but would silently break the merge order the moment a provider emits one. Now short-circuits `bool` before the int branch — both `True` AND `False` return True, matching the docstring's stated semantics. BUG-006 from the 2026-04-27 second-pass review (#467).

- `_get_failure_state` in `logic/host_metrics_sampler.py` lagged the schema after #461 added `host_failure_state.last_failure_ts` — the SELECT only read 5 columns, so any internal consumer reaching for the new field saw `None`. Brought to column-parity with the sister API helper `_failure_state_for_host`. Latent today (no internal caller uses the field yet) but ships ready for a future auto-resume feature. BUG-007 from the 2026-04-27 second-pass review (#468).

- Webmin host cards reported memory as 1024× the real value on Webmin module variants whose `mem_total` / `memory_total` is already emitted in bytes (Webmin's `real_mem` is reliably KiB, but the alternate keys some builds emit are sometimes bytes). `extract_system_status` in `logic/webmin.py` now resolves both the value AND the matched key via a new `pick_with_key()` helper; a new `_bytes_or_kib(value, key)` applies KiB scaling for `real_mem` and a magnitude heuristic for the alternate keys (raw > 2^31 ≈ 2.15 GiB → already bytes, otherwise KiB). `mem_used` derivation runs through the same helper so used + total stay consistent. Threshold catches every realistic byte-report ≥ 2 GiB without false-positiving plausible KiB reports. BUG-001 from the 2026-04-27 second-pass review (#466).

- Beszel `fetch_system_history` in `logic/beszel.py` was building the PocketBase filter via f-string interpolation: `filt = f"(system='{system_id}'&&type='{stat_type}')"`. An apostrophe in either field broke the query and silently returned an empty series (drawer chart stuck on "Collecting data…"). PocketBase doesn't support parameterised binds for arbitrary filter expressions; escaped via doubling (`'` → `''`, the SQL-style escape PB's parser accepts). BUG-002 from the 2026-04-27 second-pass review (#465).

- Schedule rows could get stuck "running" forever after a lifespan cancel mid-run. `fire_schedule()` records `(last_op_id, last_duration=NULL)` synchronously, then spawns a fire-and-forget waiter that's supposed to rewrite the row with the real duration + status when the op completes. If the lifespan was cancelled mid-run (container restart, hot reload), the waiter died before its second `record_run` call and the NULL-duration sentinel stuck forever — `_is_previous_run_active` kept reading NULL as "still running" and the tick loop skipped the schedule on every subsequent pass. Fix: ghost-clear sweep at `scheduler_loop` startup walks every row where `last_op_id IS NOT NULL AND last_duration IS NULL`, and if the op_id isn't in `_ops.ops` (the live in-memory dict only carries currently-running ops; restart wipes it), stamps the row `(0, "error")` so the next tick can fire normally. Diagnostic `[scheduler] cleared ghost run for '<name>' (op <id> not live post-restart)` log line per recovered schedule. BUG-004 from `notes/code_review_2026-04-27.txt` (#447).

- `stats_samples` was gaining duplicate rows for the most-recent sample of each item after every container restart. Cause: `seed_stats_cache_from_db` populates `_stats_cache` from the latest persisted row with `has_stats: True` AND `_stale: True`; the lifespan-managed sampler's first tick was iterating `_stats_cache` and re-INSERTing every row whose `has_stats` was true — including the seed rows — at `ts=now`. If the operator opened the SPA in the gap (≤60s), `gather_stats()` overwrote the seed with fresh values and the bug dodged; without UI activity, phantom points accumulated one per restart. Fix: `_snapshot_stats_to_db` now skips entries flagged `_stale: True`. BUG-003 from `notes/code_review_2026-04-27.txt` (#442).

- Temperature chart upgraded to multi-line + Y-axis scale (#439). Each sensor now gets its own coloured polyline (palette: `--primary` / `--warning` / `--danger` / `--success` / `--info`, deterministic by sorted sensor name). Y axis shows three temperature labels auto-scaled to the min/max across all sensors (±5°C padding). Header chips carry a colour swatch matching their line so the operator can map name → line at a glance. Missing samples break the line at the gap (skip-don't-synthesize, no zero-padding). Replaces the previous single-line `temp_max` rendering which hid per-sensor differentiation and had no axis labels.

- Temperature chart card header overflowed on hosts with 5+ thermal sensors (e.g. an Intel/AMD desktop with `coretemp_package`, `nvme_composite`, `acpitz`, per-core temps). Sensor names ran into the title row while values wrapped to a second row, decoupling the pairing. Capped the inline readout to the top 3 hottest sensors with a "+N more" chip (with full list in its tooltip), and made each chip `whitespace-nowrap` so name+value stay visually paired even when the row wraps. The chart line still uses `temp_max` so nothing hot is hidden (#437 follow-up).

- Profile → Passkeys card: "Add a passkey" button sat flush against the bottom of the enrolled-keys list with no breathing room. Added a top margin to the action row so the button has visible separation (#435).

- Passkey assertion verifier rejected with "Unexpected client data origin" when NPM rewrites the `Host` header to its internal upstream while keeping the public hostname in `X-Forwarded-Host`. `_request_origin` was reading only the `Host` header — now uses the same `X-Forwarded-Host` → `Host` → `request.url.netloc/.hostname` chain as `_request_rp_id` so origin and RP ID stay in lock-step (#434).

- Passkey enrolment was failing with `SecurityError` behind NPM. `_request_rp_id` was reading `request.url.hostname` (Starlette's parsed URL — the internal upstream hostname, not the public domain the browser sees). Switched to `X-Forwarded-Host` → `Host` header → URL-hostname fallback chain. `:port` stripped. Also surfaced client-side WebAuthn ceremony failures into Admin → Logs via a new `POST /api/me/webauthn/client-error` endpoint that the SPA fires on every DOMException — log line carries both client- and server-side `rp_id` + `origin` so future RP-mismatches are one grep away. Added five new error codes to `logic/errors.py` (OG0103 / OG0104 / OG0105 / OG0106 / OG0900) and a `message_for(code)` helper; refactored recent HTTPException raises in `main.py` to source canonical messages from the catalog (#433).

- Default schedules ("Prune debian13docker", "Refresh fleet cache") were re-seeding on every container boot even after the operator deleted them. `seed_default_schedules` in `logic/schedules.py` now uses a one-shot `default_schedules_seeded` setting flag instead of per-name existence checks — once seeded, no future call recreates the rows regardless of whether the operator deleted them. Operators wanting to re-seed clear the flag from the SQLite shell or `/api/settings` (#430).

- Passkey enrolment didn't reliably surface the password-manager save sheet (1Password / Bitwarden / iCloud Keychain). Added the WebAuthn Level 3 `hints: ["client-device", "hybrid", "security-key"]` field to registration options to nudge browsers toward the cross-platform picker. `addPasskey` in `static/js/app.js` now surfaces the actual `DOMException` name in the failure toast (was silently swallowing — impossible to debug user-cancel vs RP ID mismatch vs extension-blocked) (#431).

- Admin → Users table status pills (Active / Disabled / admin / readonly / 2FA On / Off / Required) were rendering colourless. Markup had been referencing `pill-success`, `pill-warning`, `pill-muted`, `pill-primary` classes that were never defined in CSS, so they all fell back to the base `.pill` rule (border + padding only). Added the four missing variants in `static/css/style.css`, each aliasing an existing token family — no new colour literals (#428).

- Schedule edit modal: `kind` + `cadence_mode` dropdowns weren't preselecting the saved value (the documented Alpine select-mount race — `x-model` commits before the `<option>` x-for inserts children, so the matching option doesn't exist yet and the select falls back to the first one). `editSchedule()` now sets the bound fields to `''` synchronously, then reassigns the real values in a double-`$nextTick` so the inner x-for has finished rendering first (#426).

- `host_net_sampler` was ignoring the permanent-fail auto-pause. The metrics sampler already skipped paused hosts; the net sampler kept hitting their NE endpoints and emitting `[host_net_sampler] '<host>' exporter_error: All connection attempts failed` log lines. Net sampler now reads `host_failure_state.paused` before each probe and short-circuits when set. Best-effort DB read — transient errors don't accidentally silence ALL polling (#423).

- "New version — reload" banner was appending `_v=` to the URL on every click instead of replacing it (URL grew as `?_v=1.1.31&_v=1.1.32&_v=1.1.33`). `reloadForNewVersion()` now uses `URLSearchParams.set` so consecutive reloads keep exactly one `_v=<latest>` in the search string. Hash is preserved (#418).

- Application logs view gained a severity multi-select filter (Error / Warning / Success / Info). State persists to `localStorage.logSeverityFilter` so reload preserves the view. All / None / Errors-only convenience buttons mirror the Notifications event grid's bulk shape. Backend untouched — fully client-side over the existing log ring buffer (#422).
- Brand-style mono icons for Admin → Portainer + Admin → OIDC (Authentik). New `icon-portainer` (solid bold "P" mark + signature accent square) and `icon-authentik` (faithful mono port of the "key entering a castle" mark — almond eye + shaft on the left, rounded body with three battlement merlons hanging into a window cutout and a doorway notch at the bottom) `<symbol>` entries in the sprite block, both rendered in `currentColor` to inherit the sidebar's existing line-icon styling. Sidebar entries + section headings on each tab updated to use them; every other icon left untouched (#421).

### Changed

- Notifications event grid: "Host sampling auto-paused" split out of the Security events group into its own "Sampler events" section. New i18n key `admin.notifications.events.sampler_section`; backend setting key unchanged (`notify_event_host_paused`) so no migration. Visible on both Admin → Notifications and Profile → Notifications (#420).

- Admin → Users / Sessions / Tokens intro paragraph moved from above the section boxes to below them, so the start of every Admin tab renders consistently (heading area first). Other tabs that carry their own per-section subtitle are unchanged (#419).

### Fixed

- Status-dot flicker on the Hosts view's 15s poll cycle — most visible as a red dot disappearing and reappearing on every poll for down / paused hosts. `loadHosts()` was setting `existing._loading = !skipProbe` on every poll, which toggled the dot template from dot → spinner → dot in a tight loop. Now only brand-new rows get the spinner; existing rows keep their previous data visible while `refreshHostRow` patches stats in place. Original `#405` fix targeted a different cause (DB-exception path returning falsy defaults); this addresses the actual flicker source (#416).

- Chart `?` tooltip cropped at the host-drawer start edge on left-column metric cards (regression visible after #413). Smart-placement helper `_adjustMetricTooltipPlacement()` measures the just-opened tooltip after `$nextTick` and applies `.metric-source-tooltip--align-start` when the default end-anchored body would overflow the drawer's start edge, falling back to `.metric-source-tooltip--align-center` when both edges would clip. Default end-anchored behaviour is preserved when the tooltip already fits (#415).

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
- Tap-driven tooltips for chart `?` icons (mobile-friendly, replaces the hover-only native `title`). New `metricTooltipOpen` Alpine state slot + `toggleMetricTooltip(h, key)` + click-outside / ESC dismiss. Native `:title` retained as desktop hover fallback. Placement fixed: `.metric-card-title` is now an inline-flex container so the `?` icon stays inline with the title in narrow chart cards instead of wrapping to row 2 (#413).
- New "host_paused" notification event fires when `host_metrics_sampler` auto-pauses a host after the configured failure window. Default ON; admin-side gate in Admin → Notifications, per-user opt-in toggle in Profile → Notifications. Backend `_record_failure` fire-and-forgets an Apprise `notify(event="host_paused")` on the pause transition with paused-minutes + last_error in the body (#411).
- Sampling-pause window setting moved into the unified Tuning Config tunables list (`tuning_host_permanent_fail_window_seconds`) with the standard DB > env > default fallback. Replaces the dedicated card with its own Save button — the operator now edits it alongside the other tunables. Backend keeps a backwards-compat read on the legacy `host_permanent_fail_window_seconds` setting for pre-#410 deploys (#410).
- Per-host metric-source tooltip now correctly resolves `cpu` and `load_avg` to node-exporter for NE-only hosts (after #402 shipped CPU% derivation). Previously stuck on the "NE doesn't track CPU yet" fallback (#409).
- Backups, Schedules (Scheduled + Queue), and Create-User / Create-Token card headers now carry matching icons consistent with the section-icon unification waves (#406, #407, #408).
- Permanent-fail icon flicker on poll — fixed. Backend's `_failure_state_for_host` was returning falsy defaults on transient SQLite BUSY (during the concurrent sampler writes for OTHER failing hosts), causing the alert-triangle to vanish + reappear on every 15s poll cycle. Helper now returns an empty dict on exception so the response omits the failure-state keys and the frontend's in-place reconcile preserves the previously-known values (#405).
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

<!--
  Version link references — Forgejo milestone URLs, written as relative
  paths so the host stays out of the repo and a fork / mirror picks
  the right links automatically. Path resolves from the CHANGELOG.md
  URL `<host>/<owner>/<repo>/src/branch/<branch>/CHANGELOG.md` —
  three `..` segments take us to `<host>/<owner>/<repo>/`, then
  `milestone/<id>` lands on the right page. We don't have a v1.0.0
  release tag (no `[1.0.0]` link target on purpose); the heading
  above renders literally as `## [1.0.0]` text, which is fine.
-->
[Unreleased]: ../../../milestone/2
[1.1.0]: ../../../milestone/1
