## What changed in 1.2.0

Second MINOR cut on top of `1.1.0` — rolls up **118 closed issues** under the 1.2.0 milestone. Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Authentication, passkeys & 2FA

- User force-2FA toggle from Admin → Users table (#361)
- Enrolment QR was rendering at full container width (~600-800px on desktop). qrcode-generator SVG has no int... (#362)
- FIDO2 passkeys as a 2FA factor alongside TOTP (#363)
- QR rendering bug — TOTP enrolment QR was showing the raw `otpauth://...` URI instead of an actual QR code (#364)
- The TOTP / 2FA policy section (master toggle + per-role required + lockout window) moves out of Admin → Con... (#366)
- Button + dirty indicator on Admin → Authentication tab TOTP/2FA section (#368)
- Profile section icons — About card heading gets `#icon-id-card`; Two-factor card heading swapped its inline... (#376)
- Six text buttons (`→ readonly` / `Disable` / `Reset pw` / `Disable 2FA` / `Force 2FA` / `Delete`) replaced... (#382)
- Users table status pills (Active / Disabled / admin / readonly / 2FA On / Off / Required) were rendering co... (#411)
- Master toggle for passkey enrolment + login (`passkeys_allowed`, default true) (#413)
- Wraps Duo's `webauthn>=2.0` package via `logic/webauthn_helper.py`; routes `GET /api/me/webauthn`, `POST /a... (#415)
- Enrolment didn't reliably surface the password-manager save sheet (1Password / Bitwarden / iCloud Keychain) (#416)
- Enrolment was failing with `SecurityError` behind NPM. `_request_rp_id` was reading `request.url.hostname`... (#417)
- Profile → Passkeys card: "Add a passkey" button sat flush against the bottom of the enrolled-keys list with... (#419)
- Sessions table now shows the 2FA method each session was authenticated with (Password / Password + TOTP / P... (#420)
- `passkeys_allowed` now returned by `api_get_settings` next to the TOTP-policy fields (#453)
- 17-enhancement sweep across OIDC / events / metrics / TOTP / Webmin / WebAuthn (#458)
- Passkey transports rendered as inline chips (#493)

### OIDC / SSO

- Style mono icons for Admin → Portainer + Admin → OIDC (Authentik) (#405)
- `/api/oidc/test` now respects the in-flight `verify_tls` checkbox from the OIDC settings form instead of al... (#432)
- `_validate_id_token` in `logic/oidc.py` was feeding the unverified id_token header's `alg` straight into Py... (#434)
- `_validate_id_token` in `logic/oidc.py` now logs `[oidc] kid=... not in cached jwks (#441)
- `_validate_id_token._find_key` now rejects `kid is None` instead of silently picking `keys[0]` (#460)
- OIDC flow cookie now deleted on every callback failure path via `HTTPException(headers=...)` (#461)
- `verify_authentication` now actually performs the sign-counter regression guard the docstring promised (#462)

### Real-time / event stream

- SSE pill gains a third "reconnecting" state with amber pulse (#491)
- Time event stream replacing the SPA's polling loops (#494)

### Logs view & retention

- Logs view gained a severity multi-select filter (Error / Warning / Success / Info) (#401)
- Logs on disk + configurable retention (#407)
- Tab Admin → Logs viewer + new `prune_logs` scheduler kind (#408)
- Logs → Files tab now renders log files with the same colourisation as the Live tab (#409)
- `_run_prune_logs` schedule kind in `logic/schedules.py` accepted an admin-supplied `params.days` without a... (#435)
- `_run_prune_logs` schedule history rows now record the resolved `days` value in `target_name` (`"42 log fil... (#444)
- `prune_old_logs` cutoff math + filename-date parse now route through a shared `_resolved_tz()` helper s (#452)

### Schedules & automation

- `/api/ops` poll cadence is now a tunable (Admin → Config → "Ops poll cadence (ms)"). Backed by `tuning_ops_... (#400)
- Schedules ("Prune debian13docker", "Refresh fleet cache") were re-seeding on every container boot even afte... (#414)
- Rows could get stuck "running" forever after a lifespan cancel mid-run. `fire_schedule()` records `(last_op... (#431)
- **Unified topbar refresh cadence** (#486). Replaced the separate SYNC + STATS pickers with ONE control offe... (#486)

### Notifications

- Notifications admin tab into Notifications + General; per-event notification toggles. `logic/ops.py:notify(... (#354)
- Notification (#355)
- When a notification fires for a specific user (per-event opt-in path with `actor_username`), the configured... (#390)
- Host_paused" notification event fires when `host_metrics_sampler` auto-pauses a host after the configured f... (#395)
- Success notification title now includes the new version number (#396)
- `notify(event=...)` was hardcoding the per-event admin-gate default to `True`, but `_NOTIFY_EVENT_DEFAULTS`... (#425)

### Hosts editor & Host groups

- Subgroup in Admin → Host Groups now scrolls the new row into view + focuses the name input (#360)
- View: parent group labels now render in `--text-dim` (slightly faded) so sub-group labels stand out as the... (#455)
- Stale-data badges in the Hosts UI (#496)

### Drawer, charts & Node Exporter

- `loadHostHistory` now stamps `loadedAt = Date.now()` on every successful HTTP 2xx, regardless of whethe (#346)
- Chart "?" data-source icons in host drawer (#377)
- Chart `?` icons now resolve a definitive per-host label instead of a generic "Beszel OR node-exporter" string (#378)
- Host metric-source tooltip now correctly resolves `cpu` and `load_avg` to node-exporter for NE-only hosts (... (#388)
- Tooltip cropped at the host-drawer start edge on left-column metric cards (regression visible after #394) (#393)
- Chart in the host drawer for hosts whose Beszel agent emits thermal sensors (e.g (#422)
- Chart upgraded to multi-line + Y-axis scale (#423)
- Chart polylines were invisible AND y-axis labels rendered out of bounds (#428)
- `refreshHostRow` in the SPA leaked stale fields when `/api/hosts/one/{id}` omitted a key (#433). The origin... (#433)
- Card legend chips overflowed the chart's right edge on hosts with many thermal sensors (8 cores) (#438)
- Host cards reported memory as 1024× the real value on Webmin module variants whose `mem_total` / `memory_to... (#447)
- Host drawer "Updated Xs ago" label gains absolute-ISO tooltip for Grafana correlation (#492)

### Stats sampler & metrics infra

- `host_net_sampler` was ignoring the permanent-fail auto-pause. The metrics sampler already skipped paused h... (#406)
- `stats_samples` was gaining duplicate rows for the most-recent sample of each item after every container re... (#424)
- `_HOST_SNAPSHOT_KEYS` whitelist in `logic/gather.py` was dropping real provider-emitted fields, so when a p... (#426)
- `_record_failure` in `logic/host_metrics_sampler.py` was sync but reached for `asyncio.get_event_loop()` to... (#436)
- `resumeHostSampling` force-refreshes immediately after the operator un-pauses a host so the first post- (#437)
- `_get_host_provider_state` cache key in `main.py` now includes the active-sources tuple. Previously a setti... (#439)
- `_get_failure_state` (`logic/host_metrics_sampler.py`) docstring cleaned up (#445)
- Host-snapshots read-side cache (#449)
- `_get_failure_state` in `logic/host_metrics_sampler.py` lagged the schema after #445 added `host_failure_st... (#450)
- _warned_no_mounts` set replaced with a 1024-entry FIFO-evicting `OrderedDict` (#468)
- StaleAge guard for missing `_stale_ts` (#487)

### Beszel / Pulse / Webmin / Portainer

- `_flatten_temperatures` was being called THREE times per point in `logic/beszel.py:fetch_system_history` (o... (#440)
- Fetch_system_history` in `logic/beszel.py` was building the PocketBase filter via f-string interpolation (#448)

### Admin & Settings pages

- Admin env-vars-still-set warning banner (#350)
- Admin → Version copy + the deploy.yml bump-step note. `patch_label` drops the "(CI-managed)" suffix; `patch... (#352)
- Two-layer scoping (admin global + per-user) (#357)
- Becomes a settings-sidebar peer of Profile / Ignore list / Language. `settingsSections` gets `{id:'notifica... (#365)
- Settings-sidebar peer of Profile / Notifications / Ignore list / Language (#367)
- Save-button copy across admin tabs (#370)
- Header icons on Admin + Settings views (#371)
- Intro paragraph ("User accounts, active sessions, and API tokens (#374)
- Every admin tab's primary heading now renders its matching `adminSections[i].icon` next to the title using... (#379)
- Users / Sessions / Tokens intro paragraph moved from above the section boxes to below them, so the start of... (#402)
- Log (Admin → History) now uses server-side paging instead of fetching the whole filtered set up to a 500-ro... (#429)
- Admin → Config tuning fields client-side integer + bounds validation (#490)

### Topbar, login & branding

- Topbar widgets card always showed "Unsaved" indicator on page open. `_headerPrefsBaseline` was initialised... (#359)
- Logo inside the source chip on the Profile page (#372)
- Schedules (Scheduled + Queue), and Create-User / Create-Token card headers now carry matching icons consist... (#386)
- Reload" banner was appending `_v=` to the URL on every click instead of replacing it (URL grew as `?_v=1.1.... (#404)
- Assertion verifier rejected with "Unexpected client data origin" when NPM rewrites the `Host` header to its... (#418)
- Every hardcoded English string flagged on the SPA + login page now flows through `t('key.path')` (#430)

### Vendor icons

- Three returns in `iconUrlFor` plus `hostIconUrl`'s explicit-override path AND keyword-scan path (stack/item... (#495)

### Filters, badges & status pills

- Symbol>` dedup on `static/index.html`. 15 unique icons (copy / chevron-right / chevron-down / chevron-up /... (#358)
- "Unsaved" pill text now flashes subtly on a 2s opacity cycle (1 → 0.55 → 1, ease-in-out) (#369)
- Both flipped from `pill-success` (bright green) to `pill-ok` (subtle muted-green) (#383)
- Fail marker for chronically-down hosts (#384)
- `is_meaningful(False)` returned False because Python's `bool ⊂ int` made `isinstance(False, int)` true and... (#451)
- Updates badge on the Stacks nav button (#498)

### Mobile / responsive UX

- Pinch-zoom is now actually disabled on iOS Safari, not just on Android. iOS Safari deliberately ignores the... (#381)

### API endpoints & backend helpers

- `/api/hosts/one/{host_id}` now accepts `?force=true` to bypass the 10s provider-state cache, mirroring the... (#347)
- Test endpoints surface human-readable failure summaries instead of raw upstream stack traces (#349)
- Version page now edits every component (MAJOR / MINOR / PATCH) and writes the values straight to `VERSION.txt` (#353)
- Timezone fallback now surfaces in `/api/me`'s `client_config.scheduler_tz` (`{configured, resolved, fallbac... (#442)
- Passkeys_allowed in api_get_settings (#456)

### Documentation

- Documentation refresh pass — 5 docs files modified to match the recently-shipped feature waves: PII leak in... (#373)

### Internal cleanup, refactor & bug sweeps

- Field error on a filtered-out row no longer silently no-ops. `focusFirstFieldError` in `static/js/app.js` e... (#348)
- Startup robustness pass. (a): `seed_stats_cache_from_db` and `seed_nodes_info_from_snapshots` moved into a... (#392)
- Tab primary action buttons unified (#412)
- Dead-code cleanup from (#427)
- `_HOST_SNAPSHOT_KEYS` is no longer a hand-maintained tuple drift class. Replaced with an `_is_snapshot_key(... (#443)
- 10-bug sweep shipped in one batch (#465)
- Five UX-bugs and five UX-enhancements shipped together (#487–#495). was already fixed via #456 (passkeys_al... (#488)

### Other improvements & fixes

- Editor: typing `custom_number` into a row + tabbing out no longer reorders the row mid-edit. cn `@input` no... (#356)
- `host_permanent_fail_window_seconds` was kept as a SettingsIn field, GET-side resolver row, and POST-side v... (#391)
- Dot flicker on the Hosts view's 15s poll cycle (#397)
- Notifications event grid: "Host sampling auto-paused" split out of the Security events group into its own "... (#403)
- Edit modal: `kind` + `cadence_mode` dropdowns weren't preselecting the saved value (the documented Alpine s... (#410)
- Pointer on every clickable button (#466)
- SSH terminal close-code toasts (4400/4401/4402/4403) with origin-mismatch path showing NPM-debug guidance (#489)
