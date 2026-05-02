## What changed in 1.2.0

Second MINOR cut on top of `1.1.0` — rolls up **118 closed issues** under the 1.2.0 milestone. Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Authentication, passkeys & 2FA

- User force-2FA toggle from Admin → Users table (#114)
- Enrolment QR was rendering at full container width (~600-800px on desktop). qrcode-generator SVG has no int... (#115)
- FIDO2 passkeys as a 2FA factor alongside TOTP (#116)
- QR rendering bug — TOTP enrolment QR was showing the raw `otpauth://...` URI instead of an actual QR code (#117)
- The TOTP / 2FA policy section (master toggle + per-role required + lockout window) moves out of Admin → Con... (#119)
- Button + dirty indicator on Admin → Authentication tab TOTP/2FA section (#121)
- Profile section icons — About card heading gets `#icon-id-card`; Two-factor card heading swapped its inline... (#128)
- Six text buttons (`→ readonly` / `Disable` / `Reset pw` / `Disable 2FA` / `Force 2FA` / `Delete`) replaced... (#133)
- Users table status pills (Active / Disabled / admin / readonly / 2FA On / Off / Required) were rendering co... (#156)
- Master toggle for passkey enrolment + login (`passkeys_allowed`, default true) (#158)
- Wraps Duo's `webauthn>=2.0` package via `logic/webauthn_helper.py`; routes `GET /api/me/webauthn`, `POST /a... (#160)
- Enrolment didn't reliably surface the password-manager save sheet (1Password / Bitwarden / iCloud Keychain) (#161)
- Enrolment was failing with `SecurityError` behind NPM. `_request_rp_id` was reading `request.url.hostname`... (#162)
- Profile → Passkeys card: "Add a passkey" button sat flush against the bottom of the enrolled-keys list with... (#164)
- Sessions table now shows the 2FA method each session was authenticated with (Password / Password + TOTP / P... (#165)
- `passkeys_allowed` now returned by `api_get_settings` next to the TOTP-policy fields (#196)
- 17-enhancement sweep across OIDC / events / metrics / TOTP / Webmin / WebAuthn (#199)
- Passkey transports rendered as inline chips (#213)

### OIDC / SSO

- Style mono icons for Admin → Portainer + Admin → OIDC (Authentik) (#150)
- `/api/oidc/test` now respects the in-flight `verify_tls` checkbox from the OIDC settings form instead of al... (#176)
- `_validate_id_token` in `logic/oidc.py` was feeding the unverified id_token header's `alg` straight into Py... (#178)
- `_validate_id_token` in `logic/oidc.py` now logs `[oidc] kid=... not in cached jwks (#185)
- `_validate_id_token._find_key` now rejects `kid is None` instead of silently picking `keys[0]` (#200)
- OIDC flow cookie now deleted on every callback failure path via `HTTPException(headers=...)` (#201)
- `verify_authentication` now actually performs the sign-counter regression guard the docstring promised (#202)

### Real-time / event stream

- SSE pill gains a third "reconnecting" state with amber pulse (#211)
- Time event stream replacing the SPA's polling loops (#214)

### Logs view & retention

- Logs view gained a severity multi-select filter (Error / Warning / Success / Info) (#146)
- Logs on disk + configurable retention (#152)
- Tab Admin → Logs viewer + new `prune_logs` scheduler kind (#153)
- Logs → Files tab now renders log files with the same colourisation as the Live tab (#154)
- `_run_prune_logs` schedule kind in `logic/schedules.py` accepted an admin-supplied `params.days` without a... (#179)
- `_run_prune_logs` schedule history rows now record the resolved `days` value in `target_name` (`"42 log fil... (#188)
- `prune_old_logs` cutoff math + filename-date parse now route through a shared `_resolved_tz()` helper s (#195)

### Schedules & automation

- `/api/ops` poll cadence is now a tunable (Admin → Config → "Ops poll cadence (ms)"). Backed by `tuning_ops_... (#145)
- Schedules ("Prune debian13docker", "Refresh fleet cache") were re-seeding on every container boot even afte... (#159)
- Rows could get stuck "running" forever after a lifespan cancel mid-run. `fire_schedule()` records `(last_op... (#175)
- **Unified topbar refresh cadence** (#206). Replaced the separate SYNC + STATS pickers with ONE control offe... (#206)

### Notifications

- Notifications admin tab into Notifications + General; per-event notification toggles. `logic/ops.py:notify(... (#107)
- Notification (#108)
- When a notification fires for a specific user (per-event opt-in path with `actor_username`), the configured... (#138)
- Host_paused" notification event fires when `host_metrics_sampler` auto-pauses a host after the configured f... (#142)
- Success notification title now includes the new version number (#143)
- `notify(event=...)` was hardcoding the per-event admin-gate default to `True`, but `_NOTIFY_EVENT_DEFAULTS`... (#169)

### Hosts editor & Host groups

- Subgroup in Admin → Host Groups now scrolls the new row into view + focuses the name input (#113)
- View: parent group labels now render in `--text-dim` (slightly faded) so sub-group labels stand out as the... (#197)
- Stale-data badges in the Hosts UI (#216)

### Drawer, charts & Node Exporter

- `loadHostHistory` now stamps `loadedAt = Date.now()` on every successful HTTP 2xx, regardless of whethe (#100)
- Chart "?" data-source icons in host drawer (#129)
- Chart `?` icons now resolve a definitive per-host label instead of a generic "Beszel OR node-exporter" string (#130)
- Host metric-source tooltip now correctly resolves `cpu` and `load_avg` to node-exporter for NE-only hosts (... (#137)
- Tooltip cropped at the host-drawer start edge on left-column metric cards (#141)
- Chart in the host drawer for hosts whose Beszel agent emits thermal sensors (e.g (#166)
- Chart upgraded to multi-line + Y-axis scale (#167)
- Chart polylines were invisible AND y-axis labels rendered out of bounds (#172)
- `refreshHostRow` in the SPA leaked stale fields when `/api/hosts/one/{id}` omitted a key (#177). The origin... (#177)
- Card legend chips overflowed the chart's right edge on hosts with many thermal sensors (8 cores) (#182)
- Host cards reported memory as 1024× the real value on Webmin module variants whose `mem_total` / `memory_to... (#190)
- Host drawer "Updated Xs ago" label gains absolute-ISO tooltip for Grafana correlation (#212)

### Stats sampler & metrics infra

- `host_net_sampler` was ignoring the permanent-fail auto-pause. The metrics sampler already skipped paused h... (#151)
- `stats_samples` was gaining duplicate rows for the most-recent sample of each item after every container re... (#168)
- `_HOST_SNAPSHOT_KEYS` whitelist in `logic/gather.py` was dropping real provider-emitted fields, so when a p... (#170)
- `_record_failure` in `logic/host_metrics_sampler.py` was sync but reached for `asyncio.get_event_loop()` to... (#180)
- `resumeHostSampling` force-refreshes immediately after the operator un-pauses a host so the first post- (#181)
- `_get_host_provider_state` cache key in `main.py` now includes the active-sources tuple. Previously a setti... (#183)
- `_get_failure_state` (`logic/host_metrics_sampler.py`) docstring cleaned up (#189)
- Host-snapshots read-side cache (#192)
- `_get_failure_state` in `logic/host_metrics_sampler.py` lagged the schema after #189 added `host_failure_st... (#193)
- _warned_no_mounts` set replaced with a 1024-entry FIFO-evicting `OrderedDict` (#205)
- StaleAge guard for missing `_stale_ts` (#207)

### Beszel / Pulse / Webmin / Portainer

- `_flatten_temperatures` was being called THREE times per point in `logic/beszel.py:fetch_system_history` (o... (#184)
- Fetch_system_history` in `logic/beszel.py` was building the PocketBase filter via f-string interpolation (#191)

### Admin & Settings pages

- Admin env-vars-still-set warning banner (#104)
- Admin → Version copy + the deploy.yml bump-step note. `patch_label` drops the "(CI-managed)" suffix; `patch... (#105)
- Two-layer scoping (admin global + per-user) (#110)
- Becomes a settings-sidebar peer of Profile / Ignore list / Language. `settingsSections` gets `{id:'notifica... (#118)
- Settings-sidebar peer of Profile / Notifications / Ignore list / Language (#120)
- Save-button copy across admin tabs (#123)
- Header icons on Admin + Settings views (#124)
- Intro paragraph ("User accounts, active sessions, and API tokens (#127)
- Every admin tab's primary heading now renders its matching `adminSections[i].icon` next to the title using... (#131)
- Users / Sessions / Tokens intro paragraph moved from above the section boxes to below them, so the start of... (#147)
- Log (Admin → History) now uses server-side paging instead of fetching the whole filtered set up to a 500-ro... (#173)
- Admin → Config tuning fields client-side integer + bounds validation (#210)

### Topbar, login & branding

- Topbar widgets card always showed "Unsaved" indicator on page open. `_headerPrefsBaseline` was initialised... (#112)
- Logo inside the source chip on the Profile page (#125)
- Schedules (Scheduled + Queue), and Create-User / Create-Token card headers now carry matching icons consist... (#136)
- Reload" banner was appending `_v=` to the URL on every click instead of replacing it (URL grew as `?_v=1.1.... (#149)
- Assertion verifier rejected with "Unexpected client data origin" when NPM rewrites the `Host` header to its... (#163)
- Every hardcoded English string flagged on the SPA + login page now flows through `t('key.path')` (#174)

### Vendor icons

- Three returns in `iconUrlFor` plus `hostIconUrl`'s explicit-override path AND keyword-scan path (stack/item... (#215)

### Filters, badges & status pills

- Symbol>` dedup on `static/index.html`. 15 unique icons (copy / chevron-right / chevron-down / chevron-up /... (#111)
- "Unsaved" pill text now flashes subtly on a 2s opacity cycle (1 → 0.55 → 1, ease-in-out) (#122)
- Both flipped from `pill-success` (bright green) to `pill-ok` (subtle muted-green) (#134)
- Fail marker for chronically-down hosts (#135)
- `is_meaningful(False)` returned False because Python's `bool ⊂ int` made `isinstance(False, int)` true and... (#194)
- Updates badge on the Stacks nav button (#217)

### Mobile / responsive UX

- Pinch-zoom is now actually disabled on iOS Safari, not just on Android. iOS Safari deliberately ignores the... (#132)

### API endpoints & backend helpers

- `/api/hosts/one/{host_id}` now accepts `?force=true` to bypass the 10s provider-state cache, mirroring the... (#101)
- Test endpoints surface human-readable failure summaries instead of raw upstream stack traces (#103)
- Version page now edits every component (MAJOR / MINOR / PATCH) and writes the values straight to `VERSION.txt` (#106)
- Timezone fallback now surfaces in `/api/me`'s `client_config.scheduler_tz` (`{configured, resolved, fallbac... (#186)
- Passkeys_allowed in api_get_settings (#198)

### Documentation

- Documentation refresh pass — 5 docs files modified to match the recently-shipped feature waves: PII leak in... (#126)

### Internal cleanup, refactor & bug sweeps

- Field error on a filtered-out row no longer silently no-ops. `focusFirstFieldError` in `static/js/app.js` e... (#102)
- Startup robustness pass. (a): `seed_stats_cache_from_db` and `seed_nodes_info_from_snapshots` moved into a... (#140)
- Tab primary action buttons unified (#157)
- Dead-code cleanup from (#171)
- `_HOST_SNAPSHOT_KEYS` is no longer a hand-maintained tuple drift class. Replaced with an `_is_snapshot_key(... (#187)
- 10-bug sweep shipped in one batch (#203)
- Five UX-bugs and five UX-enhancements shipped together (#207–#215). was already fixed via #198 (passkeys_al... (#208)

### Other improvements & fixes

- Editor: typing `custom_number` into a row + tabbing out no longer reorders the row mid-edit. cn `@input` no... (#109)
- `host_permanent_fail_window_seconds` was kept as a SettingsIn field, GET-side resolver row, and POST-side v... (#139)
- Dot flicker on the Hosts view's 15s poll cycle (#144)
- Notifications event grid: "Host sampling auto-paused" split out of the Security events group into its own "... (#148)
- Edit modal: `kind` + `cadence_mode` dropdowns weren't preselecting the saved value (the documented Alpine s... (#155)
- Pointer on every clickable button (#204)
- SSH terminal close-code toasts (4400/4401/4402/4403) with origin-mismatch path showing NPM-debug guidance (#209)
