// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,OverlyComplexBooleanExpressionJS,AnonymousFunctionJS,NestedFunctionCallJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,ExceptionCaughtLocallyJS,JSReusedLocalVariable,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,RedundantLocalVariableJS,JSIgnoredPromiseFromCall
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,HtmlUnknownTag,HtmlEmptyContent,HtmlEmptyTagsRecommendation,HtmlSelfClosedTag,JSReusedLocal,LocalVariableReusedJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Admin → Config — the tunables editor.

// Per-flush memo for _tuningSnapshot() — the 172-key JSON serialization that
// tuningDirty() runs. tuningDirty() is bound in :class / x-show on the Config
// tab (x2) AND the AI tab (x5), so Alpine re-evaluated it ~7x per flush, each
// rebuilding the full snapshot. The snapshot only changes when the operator
// edits a tunable input (which itself triggers a flush), so memoize it for the
// flush + clear on the next microtask — the 7 reads collapse to one build,
// and a real edit rebuilds on the flush it triggers. loadTuning() busts the
// memo before stamping the baseline so the baseline never reads a stale build.
let _tuningSnapshotMemo = null;
let _tuningSnapshotScheduled = false;

// Cross-flush memo for sortedTuningKeys() — the Config x-for source re-sorted
// 41 keys via localeCompare with ~2 t() lookups per comparison on EVERY flush,
// even though the sort order only changes on a language switch. Cache keyed on
// (active lang + key count); keying on this.lang auto-invalidates on setLang
// (no manual bust needed). (ADMIN-PERF-05.)
let _sortedTuningKeysMemo = {lang: null, len: -1, value: []};

export default {
  // Admin → Config. DB-overridable process tunables. `tuningForm`
  // holds string values (blank = clear / fall back to env). `tuningEffective`
  // mirrors the GET /api/admin/tuning response so the form can render
  // env-fallback / default placeholders + the resolved current value.
  tuningKeys: [
    'tuning_cache_ttl_seconds',
    'tuning_stats_cache_ttl_seconds',
    'tuning_registry_concurrency',
    'tuning_stats_concurrency',
    'tuning_stats_targeted_timeout_seconds',
    'tuning_stats_untargeted_timeout_seconds',
    'tuning_swarm_agent_unhealthy_threshold',
    'tuning_stats_history_days',
    // host_failure_events (incidents) retention — independent
    // window from `tuning_stats_history_days` because operators
    // usually want a longer post-mortem audit trail than the raw
    // sample tables. 0 disables pruning entirely.
    'tuning_incidents_retention_days',
    'tuning_stats_sample_interval_seconds',
    'tuning_host_baseline_recompute_interval_seconds',
    'tuning_host_baseline_first_tick_delay_seconds',
    'tuning_kick_gather_timeout_seconds',
    // permanent-fail window (was a separate card with its own
    // Save button until the operator asked for it to be a regular
    // tunable). Backend's `_record_failure` reads it via
    // `tuning_int("tuning_host_permanent_fail_window_seconds")`.
    'tuning_host_permanent_fail_window_seconds',
    // frontend /api/ops poll cadence in SECONDS (was
    // `tuning_ops_poll_interval_ms` until the operator pointed out
    // that ms forced a manual conversion when tuning). Backend
    // multiplies by 1000 in `client_config.ops_poll_ms` so pollOps()
    // still consumes ms in its setTimeout. Resolved per-tick so a
    // Save here takes effect on the next cycle after /api/me re-flows.
    'tuning_ops_poll_interval_seconds',
    // persistent-log retention in days. Rendered in
    // Admin → Logs (Files sub-tab) instead of the generic Process
    // tunables form so operators looking at the daily log files
    // have the retention knob ready to hand. The tunable is still
    // wired through TUNABLES + SettingsIn + i18n; just not shown
    // here. Same `tuningForm` / `tuningEffective` / `saveTuning`
    // Alpine state, so no separate plumbing needed.
    // host_snapshots read-side cache TTL in seconds. Was
    // missing from this list so the Admin → Process tunables
    // form silently omitted the row.
    'tuning_host_snapshots_cache_ttl_seconds',
    // Per-field stale grace cap (hours). When a host_* field has
    // been stale-restored from snapshot for longer than this
    // window, drop it from the merged dict + snapshot so phantom
    // orphans (fields the host's active providers can't actually
    // produce) decay instead of cycling forever.
    'tuning_host_snapshot_stale_field_max_age_hours',
    // SPA loadHosts() concurrency cap on per-host
    // /api/hosts/one/<id> fan-out. Read on /api/me into
    // `me.client_config.hosts_parallel_fetch`.
    'tuning_hosts_parallel_fetch',
    // Idle-time progressive-fill cadence on the Hosts view —
    // background ticker trickles not-yet-loaded rows through
    // the shared refresh queue so when the operator scrolls
    // down the data is already there. Surfaced via
    // `me.client_config.hosts_idle_fill_seconds`. 0 = disabled.
    'tuning_hosts_idle_fill_interval_seconds',
    // / SSE heartbeat cadence + connection lifetime.
    'tuning_sse_heartbeat_seconds',
    'tuning_sse_max_lifetime_seconds',
    // Webmin probe outer budget. Rendered in
    // Admin → Providers (Webmin section) instead of the generic
    // Process tunables form so operators editing Webmin creds have
    // the budget knob ready to hand. Same `tuningForm` /
    // `tuningEffective` / `saveTuning` Alpine state — just rendered
    // in two places only when configured.
    // node-exporter per-host probe timeout. Rendered in
    // Admin → Providers → Node-exporter section instead of
    // the generic Process tunables form so operators editing NE
    // config have the timeout knob ready to hand. Same
    // `tuningForm` / `tuningEffective` / `saveTuning` Alpine
    // state — just rendered in the domain-specific home.
    // / frontend SSE knobs delivered via /api/me.
    'tuning_sse_idle_threshold_seconds',
    'tuning_pollops_sse_keepalive_seconds',
    'tuning_load_busy_max_seconds',
    // login rate-limit policy.
    'tuning_rate_limit_max_failures',
    'tuning_rate_limit_window_seconds',
    'tuning_rate_limit_lockout_seconds',
    // outer host-provider cache.
    'tuning_host_provider_cache_ttl_seconds',
    // Cadence (in cache-access calls) for the host-provider
    // cache hit/miss diagnostic log line.
    'tuning_host_provider_cache_diag_interval',
    // gather_stats per-node fail-cache TTL (skip known-unreachable
    // Swarm workers for this window after first ConnectTimeout).
    'tuning_stats_per_node_unreachable_ttl_seconds',
    // DNS-failure skip cache TTL — every sampler that resolves
    // hostnames consults logic.dns_skip to skip probes for
    // unresolvable hosts within this window.
    'tuning_dns_failed_skip_seconds',
    // Slow-query log threshold (ms) — every db_conn() wraps its
    // execute / executemany with timing; queries exceeding land in
    // Admin → Logs with the [slow_query] WARN prefix. 0 disables.
    'tuning_slow_query_threshold_ms',
    // host_metrics_sampler.py _host_provider_config() cache TTL.
    'tuning_host_provider_config_cache_ttl_seconds',
    // per-host Webmin caches MOVED to Admin → Providers
    // → Webmin section per operator request. See
    // `relocatedTuningKeys` below — they keep the same Alpine
    // state via the union helper, just don't render in the
    // generic Process tunables form.
    // host_metrics_sampler per-tick NE probe concurrency.
    'tuning_host_metrics_probe_concurrency',
    // shared auth-failure cool-down.
    'tuning_auth_failure_cooldown_seconds',
    // tuning_notification_retention_days relocated → Admin → Notifications
    // (lives next to the per-medium / per-event toggles where operators
    //  expect to find it; `relocatedTuningKeys` carries it through Save).
  ],
  // Tunables rendered OUTSIDE the generic Process tunables form
  //. Same `tuningForm` / `tuningEffective` /
  // `saveTuning` state as the Process tunables form — just rendered
  // in domain-specific sections (Logs / Webmin / NE) so operators
  // editing related config have the knob ready to hand. The
  // `loadTuning` / `_tuningSnapshot` / `saveTuning` iteration sites
  // walk `_allTuningKeys()` (the union) so save round-trips ALL
  // tunable keys, not just the ones rendered in the generic form.
  // Without this list those relocated keys would be invisible to
  // form-seed + dirty-track + POST — caught after shipping the
  // Log-retention card relocation (the card was reading empty and
  // Save was a no-op until the relocated key was added here).
  // Per-provider knob lists (partial DRY). Single source
  // of truth for which tunables render in each provider's admin panel
  // — adding a new knob is one entry here instead of editing each
  // panel's inline x-for array. Each panel references via
  // `_perProviderTuneKeys.<provider>`. Mirrored into `relocatedTuningKeys`
  // below so the form-seed + dirty-track + POST flows pick them up.
  _perProviderTuneKeys: {
    node_exporter: [
      'tuning_node_exporter_probe_timeout_seconds',
      'tuning_node_exporter_sample_interval_seconds',
      'tuning_node_exporter_failure_pause_rounds',
    ],
    beszel: [
      'tuning_beszel_probe_timeout_seconds',
      'tuning_beszel_probe_timeout_unreachable_seconds',
      'tuning_beszel_sample_interval_seconds',
      'tuning_beszel_failure_pause_rounds',
    ],
    pulse: [
      'tuning_pulse_failure_pause_rounds',
      'tuning_pulse_probe_timeout_seconds',
      'tuning_pulse_probe_timeout_unreachable_seconds',
      'tuning_pulse_sample_interval_seconds',
    ],
    webmin: [
      'tuning_webmin_probe_timeout_seconds',
      'tuning_webmin_probe_budget_seconds',
      'tuning_webmin_sampler_budget_seconds',
      'tuning_webmin_host_cache_ttl_seconds',
      'tuning_webmin_host_fail_cache_ttl_seconds',
      'tuning_webmin_failure_pause_rounds',
    ],
    snmp: [
      'tuning_snmp_probe_timeout_seconds',
      'tuning_snmp_wall_clock_budget_seconds',
      'tuning_snmp_per_host_walk_concurrency',
      'tuning_snmp_walk_concurrency_dell',
      'tuning_snmp_walk_concurrency_cisco',
      'tuning_snmp_walk_concurrency_synology',
      'tuning_snmp_walk_concurrency_ucd',
      'tuning_snmp_walk_concurrency_printer',
      'tuning_snmp_concurrency',
      'tuning_snmp_sample_interval_seconds',
      'tuning_snmp_unreachable_cooldown_seconds',
      'tuning_snmp_host_cache_ttl_seconds',
      'tuning_snmp_host_fail_cache_ttl_seconds',
      'tuning_snmp_failure_pause_rounds',
    ],
    ping: [
      'tuning_ping_interval_seconds',
      'tuning_ping_concurrency',
      'tuning_ping_probe_timeout_seconds',
      'tuning_ping_cooldown_seconds',
      'tuning_ping_packet_interval_ms',
      'tuning_ping_failure_pause_rounds',
    ],
    http_probe: [
      'tuning_http_probe_timeout_seconds',
      'tuning_http_probe_concurrency',
      'tuning_http_probe_sample_interval_seconds',
      'tuning_http_probe_failure_pause_rounds',
      'tuning_http_probe_dns_timeout_seconds',
      'tuning_http_probe_cert_warning_days',
      'tuning_http_probe_host_cache_ttl_seconds',
      'tuning_http_probe_host_fail_cache_ttl_seconds',
      'tuning_http_probe_default_accepted_lo_code',
      'tuning_http_probe_default_accepted_hi_code',
    ],
    service_probe: [
      'tuning_service_probe_sample_interval_seconds',
      'tuning_service_probe_concurrency',
      'tuning_service_probe_timeout_seconds',
      'tuning_service_probe_failure_pause_rounds',
    ],
  },
  relocatedTuningKeys: [
    'tuning_log_retention_days', // → Admin → Logs
    'tuning_webmin_probe_budget_seconds', // → Admin → Providers → Webmin
    'tuning_webmin_sampler_budget_seconds', // → Admin → Providers → Webmin (sampler tick budget)
    'tuning_node_exporter_probe_timeout_seconds', // → Admin → Providers → NE
    'tuning_beszel_probe_timeout_unreachable_seconds', // → Admin → Providers → Beszel
    'tuning_pulse_probe_timeout_unreachable_seconds', // → Admin → Providers → Pulse
    // Asset Inventory outbound HTTP timeouts — rendered in
    // Admin → Asset Inventory next to URL / client ID / etc.
    // Section-owned save via assetDirty() / saveAssetSettings().
    'tuning_asset_inventory_token_timeout_seconds',
    'tuning_asset_inventory_fetch_timeout_seconds',
    // Portainer write-op timeout tiers — rendered in Admin →
    // Portainer alongside URL / API key / endpoint ID. Section-
    // owned save via portainerDirty() / savePortainerSettings().
    'tuning_portainer_op_timeout_short_seconds',
    'tuning_portainer_op_timeout_medium_seconds',
    'tuning_portainer_op_timeout_long_seconds',
    // Public IP — standalone subsystem with its own Admin → Public
    // IP section. Master toggle (1/0) + cache TTL + fetch timeout.
    // Section-owned save via publicIpSectionDirty() / savePublicIpSection().
    'tuning_public_ip_enabled',
    'tuning_public_ip_cache_ttl_seconds',
    'tuning_public_ip_fetch_timeout_seconds',
    'tuning_public_ip_sample_interval_seconds',
    // Apps per-card extras (Speedtest / APC) freshness TTL — rendered in
    // Admin → Apps (not the generic Config form). Section-owned save via
    // appsSettingsSectionDirty() / saveAppsSettingsSection(). SPA consumer
    // reads via me.client_config.apps_extras_ttl_seconds (stale-while-
    // revalidate background refresh of the per-instance /app-data cache).
    'tuning_apps_extras_ttl_seconds',
    // Apps first-paint tile-render batch size — also rendered in Admin → Apps
    // (relocated out of the generic Config form), saved via the same
    // appsSettingsSectionDirty() / saveAppsSettingsSection() path. SPA consumer
    // reads via me.client_config.apps_tile_render_batch (how many tiles the
    // lazy-render queue readies per setTimeout(0) tick on first paint).
    'tuning_apps_tile_render_batch',
    // WeatherAPI.com — standalone subsystem with its own Admin →
    // Weather section. Cache TTL (default 600s), outbound HTTP
    // wall-clock (default 8s), persisted-sample retention (default
    // 90d, 0 disables pruning), and lifespan-managed sampler cadence
    // (default 3600s, 0 disables the historical sampler).
    // Section-owned save via weatherSectionDirty() / saveWeatherSection().
    'tuning_weather_cache_ttl_seconds',
    'tuning_weather_fetch_timeout_seconds',
    'tuning_weather_history_retention_days',
    'tuning_weather_sampler_interval_seconds',
    // Telegram listener long-poll + outer-HTTP timeouts — rendered
    // inside Admin → Notifications → Telegram tab next to the bot-
    // token / chat-id / api-base inputs. Section save piggy-backs on
    // the existing notifications-save chain (see _appriseSnapshot et
    // al.) so an edit there commits through the same Save click as
    // the other Telegram fields.
    'tuning_telegram_long_poll_timeout_seconds',
    'tuning_telegram_http_timeout_seconds',
    'tuning_telegram_destructive_cooldown_seconds',
    'tuning_telegram_ai_calls_per_minute',
    'tuning_telegram_bulk_update_concurrency',
    // Gather fan-out client timeout + orphan-probe per-call timeout —
    // also rendered in Admin → Portainer (gather talks to Portainer).
    'tuning_gather_client_timeout_seconds',
    'tuning_gather_orphan_probe_timeout_seconds',
    'tuning_webmin_host_cache_ttl_seconds', // → Admin → Providers → Webmin
    'tuning_webmin_host_fail_cache_ttl_seconds',// → Admin → Providers → Webmin
    // Swarm autoheal cooldown — fires from the `swarm_agent_health`
    // schedule kind, not visible in any host-stats section. Listed
    // here so the save round-trip + dirty tracking still pick it up
    // even though it lives in the generic Admin → Config form.
    'tuning_swarm_autoheal_cooldown_minutes',
    // Beszel section tunables — rendered in Admin → Providers
    // → Beszel via `_perProviderTuneKeys.beszel`. Listed here so
    // saveHostStats picks them up alongside the other provider
    // tunables instead of leaking them to the generic Admin →
    // Config save path.
    'tuning_beszel_probe_timeout_seconds',
    'tuning_beszel_sample_interval_seconds',
    // Ping provider tunables (rendered in Providers → Ping).
    'tuning_ping_interval_seconds',
    'tuning_ping_concurrency',
    'tuning_ping_probe_timeout_seconds',
    'tuning_ping_cooldown_seconds',
    'tuning_ping_packet_interval_ms',
    // HTTP / TLS / DNS probe — seventh host-stats provider.
    // Section-owned save via httpProbeSectionDirty() /
    // saveHttpProbeSection(). Rendered in Admin → Providers →
    // HTTP probe; the consumer reads the values via tuning_int(...)
    // per-call inside `logic/host_http_sampler.py`.
    'tuning_http_probe_timeout_seconds',
    'tuning_http_probe_concurrency',
    'tuning_http_probe_sample_interval_seconds',
    'tuning_http_probe_failure_pause_rounds',
    'tuning_http_probe_dns_timeout_seconds',
    'tuning_http_probe_cert_warning_days',
    'tuning_http_probe_host_cache_ttl_seconds',
    'tuning_http_probe_host_fail_cache_ttl_seconds',
    'tuning_http_probe_default_accepted_lo_code',
    'tuning_http_probe_default_accepted_hi_code',
    // Service probe — per-service-chip reachability sampler.
    // Section-owned save via serviceProbeSectionDirty() /
    // saveServiceProbeSection(). Rendered in Admin → Providers →
    // Service probe; the sampler reads the values via tuning_int(...)
    // per-call inside `logic/service_sampler.py`.
    'tuning_service_probe_sample_interval_seconds',
    'tuning_service_probe_concurrency',
    'tuning_service_probe_timeout_seconds',
    'tuning_service_probe_failure_pause_rounds',
    // SNMP provider tunables (rendered in Providers → SNMP).
    'tuning_snmp_probe_timeout_seconds',
    'tuning_snmp_wall_clock_budget_seconds',
    'tuning_snmp_per_host_walk_concurrency',
    'tuning_snmp_concurrency',
    // SNMP per-host cache TTLs, distinct from Webmin's pair.
    'tuning_snmp_host_cache_ttl_seconds',
    'tuning_snmp_host_fail_cache_ttl_seconds',
    // dedicated SNMP unreachable cool-down (was sharing the
    // auth-failure cool-down with Webmin / SSH).
    'tuning_snmp_unreachable_cooldown_seconds',
    // SNMP-specific sample interval (0 = use global cadence).
    'tuning_snmp_sample_interval_seconds',
    // SNMP per-(provider, host) auto-pause threshold. N
    // consecutive failed sampler rounds → mark host as Paused on
    // the SNMP chip; operator clears via Resume button.
    'tuning_snmp_failure_pause_rounds',
    // Webmin per-(provider, host) auto-pause threshold.
    // Same semantic as the SNMP one; counts failed _merge_one_host
    // probes (cool-down responses don't count).
    'tuning_webmin_failure_pause_rounds',
    // Beszel / Pulse / node-exporter / Ping per-(provider, host)
    // auto-pause thresholds. Generalised the SNMP+Webmin
    // pattern to every provider so the chip + Resume button work
    // uniformly. Hub-based providers (Beszel/Pulse) only count
    // hub-OK + missing-host as failures, so a global hub blip
    // doesn't cascade-pause every host. Ping default 0 because
    // alive=False is the data, not a fault.
    'tuning_beszel_failure_pause_rounds',
    'tuning_beszel_probe_timeout_seconds',
    'tuning_beszel_sample_interval_seconds',
    'tuning_pulse_failure_pause_rounds',
    'tuning_pulse_probe_timeout_seconds',
    // Pulse + NE per-sampler interval overrides — mirror the
    // Beszel knob's shape (0 = inherit `tuning_stats_sample_interval_seconds`,
    // > 0 = override). Rendered in Admin → Providers →
    // Pulse / NE respectively via `_perProviderTuneKeys`.
    'tuning_pulse_sample_interval_seconds',
    'tuning_node_exporter_sample_interval_seconds',
    'tuning_webmin_probe_timeout_seconds',
    'tuning_node_exporter_failure_pause_rounds',
    'tuning_ping_failure_pause_rounds',
    // stat-bar warn / crit thresholds (frontend-consumed).
    'tuning_stat_bar_warn_pct',
    'tuning_stat_bar_crit_pct',
    // Stack-update convergence-poll window — keeps the busy-state
    // honest by waiting for Swarm-service UpdateStatus to settle
    // after Portainer accepts the PUT.
    'tuning_stack_update_observe_timeout_seconds',
    'tuning_stack_update_observe_poll_seconds',
    // In-app notifications retention — rendered inline in Admin →
    // Notifications next to the per-medium / per-event toggles
    // (was in the generic Process tunables form previously, but
    // operators editing notification config wanted the retention
    // dial in the same place).
    'tuning_notification_retention_days',
    'tuning_notification_page_size',
    'tuning_notifications_poll_interval_seconds',
    // AI provider auto-retry on transient upstream overload —
    // rendered in Admin → AI Integration alongside the existing
    // master toggle / active-provider / max-tokens fields. Per
    // user request: "add the config items to tunables in the AI
    // section please in admin", NOT the generic Process tunables
    // form. Backend reads via `tuning_int(...)` per-call inside
    // `logic/ai.py:_with_retry`.
    'tuning_ai_retry_enabled',
    'tuning_ai_retry_backoff_ms',
    'tuning_ai_retry_first_attempt_max_ms',
    // AI output-token cap + fallback-chain depth — rendered under
    // Admin → AI Integration alongside the existing "Max response
    // tokens" / "Fallback max depth" form fields. Backend reads
    // via `tuning_int(...)` per-call.
    'tuning_ai_max_tokens',
    'tuning_ai_fallback_max_depth',
    // AI sidebar drawer width — also rendered under Admin → AI
    // Integration (NOT the generic Process tunables form) per user
    // preference: it's an AI-feature UI control, not a generic
    // tunable. Reads via `me.client_config.ai_sidebar_width_px`.
    'tuning_ai_sidebar_width_px',
    // AI sidebar conversation-persist cadence (ms) — same admin
    // section. Reads via `me.client_config.ai_conversation_persist_ms`.
    'tuning_ai_conversation_persist_interval_ms',
    // AI conversation export master toggle — surfaced as a
    // checkbox under Admin → AI Integration. Reads via
    // `me.client_config.ai_conversation_export_enabled`; SPA hides
    // the export buttons in the AI sidebar header when false.
    'tuning_ai_conversation_export_enabled',
    // AI log-context window + cap — render in Admin → AI Integration
    // alongside the rest of the AI form, NOT the generic Process
    // tunables.
    'tuning_ai_log_context_hours',
    'tuning_ai_log_context_lines',
    // AI provider HTTP timeouts — rendered in Admin → AI Integration
    // alongside the rest of the AI form. `tuning_ai_http_timeout_seconds`
    // caps the Test-connection probe; `tuning_ai_extended_http_timeout_seconds`
    // caps the real chat-completion call. Per-use reads inside
    // `logic.ai.test_provider` / `ask_provider`.
    'tuning_ai_http_timeout_seconds',
    'tuning_ai_extended_http_timeout_seconds',
    // Port-scan tunables — rendered in Admin → Port Scan, NOT
    // the generic Config form. Each routes through TUNABLES so
    // the operator can adjust without redeploy.
    'tuning_port_scan_default_timeout_seconds',
    'tuning_port_scan_default_concurrency',
    'tuning_port_scan_max_seconds',
    'tuning_port_scan_banner_read_seconds',
    // Stage 2 — UDP tunables. Surfaced from Admin → Port Scan
    // alongside their TCP counterparts; both flow through the
    // generic tuning form so the bounds-chips UI lights up.
    'tuning_port_scan_udp_default_timeout_seconds',
    'tuning_port_scan_udp_default_concurrency',
    // Scheduled port-scan refresh — knobs for the
    // `port_scan_refresh` schedule kind. Consumed by the runner
    // in logic/schedules.py at fire time. Surfaced from Admin →
    // Port Scan via the generic tuning form so the bounds-chips
    // UI lights up.
    'tuning_port_scan_schedule_max_hosts_per_tick',
    'tuning_port_scan_schedule_min_age_seconds',
    'tuning_port_scan_schedule_per_host_concurrency',
    // Scheduler wedged-run self-heal — how long a schedule may sit with
    // last_duration=NULL (waiter died / op hung) before the next tick
    // treats it as a ghost and re-fires. Consumed by
    // logic/schedules.py:_is_previous_run_active.
    'tuning_schedule_stuck_run_threshold_seconds',
    // Backup retention count — rendered under Admin → Backups
    // (form field "Keep N most recent backups"). 0 = keep all.
    'tuning_backup_retention_count',
    // Settings-as-Code (config_backup schedule kind) on-disk
    // snapshot retention — sibling of tuning_backup_retention_count
    // for the new JSON-snapshot path.
    'tuning_config_backup_retention_count',
    // SSH WebSocket heartbeat cadence — rendered under Admin →
    // SSH alongside the other SSH knobs.
    'tuning_ssh_ws_heartbeat_seconds',
    // SSH terminal connect / login wall-clocks.
    'tuning_ssh_terminal_connect_timeout_seconds',
    'tuning_ssh_terminal_login_timeout_seconds',
    // SSH terminal connection-close wait timeout — caps how long
    // `conn.wait_closed()` blocks after a terminal session ends.
    'tuning_ssh_close_timeout_seconds',
    // OIDC outbound HTTP wall-clock — covers discovery / JWKS /
    // token exchange / Test-connection probe. Rendered in Admin →
    // Authentik OIDC via section-owned save.
    'tuning_oidc_http_timeout_seconds',
    // Per-provider default ports — promoted out of plain settings.
    'tuning_ssh_default_port',
    'tuning_snmp_default_port',
    'tuning_ping_default_port',
  ],
  tuningForm: {},
  tuningEffective: {},
  tuningLoaded: false,
  tuningSaving: false,
  _tuningBaseline: '',
  // Per-tab tunable search — workflow-compressor. The 8 provider
  // sub-tabs + the Process tunables tab each render N tunables;
  // typing here filters the visible rows by key / localised label /
  // localised help text (case-insensitive substring). Empty string
  // = no filter (every row visible). Shared across all tabs since
  // the user is in ONE tab at a time; persists across tab switches
  // for the rare cross-tab search workflow.
  tunableSearch: '',
  // Helper consumed by every row's `x-show` gate. Returns true when
  // the search is empty OR the key contains the query OR the
  // localised label / help text contains the query.
  tunableMatchesSearch(key) {
    const q = (this.tunableSearch || '').trim().toLowerCase();
    if (!q) {
      return true;
    }
    if (!key) {
      return false;
    }
    const k = String(key).toLowerCase();
    if (k.includes(q)) {
      return true;
    }
    try {
      const label = (this.t && this.t('admin.config.fields.' + key + '.label')) || '';
      if (String(label).toLowerCase().includes(q)) {
        return true;
      }
      const help = (this.t && this.t('admin.config.fields.' + key + '.help')) || '';
      if (String(help).toLowerCase().includes(q)) {
        return true;
      }
    } catch (_) {
    }
    return false;
  },
  // Admin → Config. Load DB / env / default state from the
  // dedicated endpoint so the form can render placeholders for the
  // env-fallback behind each input. `tuningForm[k]` is always a
  // string — blank means "clear the override", non-blank means
  // "store this number".
  // Union of in-form `tuningKeys` + relocated-elsewhere
  // `relocatedTuningKeys`. Every iteration site that touches the
  // tuning system (form-seed, snapshot, POST builder, validator)
  // walks THIS list so a relocated tunable still round-trips.
  _allTuningKeys() {
    return (this.tuningKeys || []).concat(this.relocatedTuningKeys || []);
  },
  // Per-tunable inherit-source map. Each entry: tunable that has
  // "0 = inherit" semantics → the source tunable it falls back to
  // when its effective is 0. Drives `tuningEffectiveLabel(key)` so
  // the Admin → Config form reads "Inherited: 300s" instead of the
  // misleading "Effective: 0". Keep this in lockstep with the
  // sampler-side resolver fallbacks (e.g. `host_pulse_sampler` and
  // `host_metrics_sampler._resolve_outer_interval`).
  _tuningInheritSource: {
    tuning_beszel_sample_interval_seconds: 'tuning_stats_sample_interval_seconds',
    tuning_pulse_sample_interval_seconds: 'tuning_stats_sample_interval_seconds',
    tuning_node_exporter_sample_interval_seconds: 'tuning_stats_sample_interval_seconds',
    tuning_snmp_sample_interval_seconds: 'tuning_stats_sample_interval_seconds',
    tuning_ping_interval_seconds: 'tuning_stats_sample_interval_seconds',
    tuning_http_probe_sample_interval_seconds: 'tuning_stats_sample_interval_seconds',
    tuning_service_probe_sample_interval_seconds: 'tuning_stats_sample_interval_seconds',
  },
  // Compose the "Effective: <X>" / "Inherited: <X>" label for one
  // tunable. Consults the LIVE form value first (so emptying / zeroing
  // the input immediately flips the chip to the inherit form, even
  // before Save) and falls back to the server-side effective from
  // the GET response when the form value is undefined.
  //
  // Inherit semantics: when the tunable has a known inherit-source
  // AND its resolved value is 0 / empty (the "0 = inherit" sentinel),
  // the chip reads "Inherited: <source-effective> (from <source-key>)"
  // — resolved through the source tunable's CURRENT label so a chain
  // of edits in one Save renders correctly without a reload.
  tuningEffectiveLabel(key) {
    const formMap = this.tuningForm || {};
    const effMap = this.tuningEffective || {};
    const formRaw = formMap[key];
    const serverRow = effMap[key] || {};
    const isBlankForm = (formRaw === '' || formRaw === null || formRaw === '0' || formRaw === 0);
    // Resolved value the chip should display when NOT inheriting:
    // form takes precedence (operator's pending edit), else server-
    // effective. Coerced to Number for the bool-bound check below.
    let resolved;
    if (formRaw !== undefined && formRaw !== '' && formRaw !== null) {
      const n = Number(formRaw);
      resolved = Number.isFinite(n) ? n : formRaw;
    } else {
      resolved = serverRow.effective;
    }
    const source = this._tuningInheritSource[key];
    // Trigger inherit-label when the key has a known source AND the
    // resolved value is empty / 0 / undefined. `isBlankForm` covers
    // the operator-just-emptied case; the `resolved === 0` case
    // covers the freshly-loaded "0 = inherit" sentinel.
    const inheriting = !!source && (
      isBlankForm
      || resolved === 0 || resolved === '0'
      || resolved === null || resolved === undefined
    );
    if (inheriting) {
      // Source's resolved value — same form-first precedence so a
      // pending edit on the SOURCE tunable propagates to the
      // inheritor's chip immediately.
      const srcFormRaw = formMap[source];
      let srcResolved;
      if (srcFormRaw !== undefined && srcFormRaw !== '' && srcFormRaw !== null) {
        const sn = Number(srcFormRaw);
        srcResolved = Number.isFinite(sn) ? sn : srcFormRaw;
      } else {
        srcResolved = ((effMap[source] || {}).effective);
      }
      if (srcResolved !== undefined && srcResolved !== null && srcResolved !== '') {
        return this.t('admin.config.inherited_label', {value: srcResolved, source: source})
          || ('Inherited: ' + srcResolved + ' (from ' + source + ')');
      }
    }
    return this.t('admin.config.effective_label', {value: resolved})
      || ('Effective: ' + (resolved === undefined ? '' : resolved));
  },
  async loadTuning() {
    try {
      const r = await fetch('/api/admin/tuning');
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      const d = await r.json();
      this.tuningEffective = d || {};
      const form = {};
      // Tunables whose range is 0..1 accept common boolean strings
      // ("true"/"false"/"yes"/"no") at write-time on the backend, but
      // the SPA's checkbox `true-value="1" false-value="0"` won't
      // light a checkmark unless the model is the literal "1". Self-
      // heal corrupt DB values by normalising at read time too — a
      // previous form-state binding that leaked `String(true)` →
      // "true" into the DB would otherwise show as unchecked here.
      const TRUTHY = new Set(['true', 'yes', 'on', 'y', 't']);
      const FALSY = new Set(['false', 'no', 'off', 'n', 'f']);
      for (const k of this._allTuningKeys()) {
        const row = (d || {})[k] || {};
        let v = (row.db == null || row.db === '') ? '' : String(row.db);
        const isBoolBound = (Number(row.min) === 0 && Number(row.max) === 1);
        if (isBoolBound && v !== '' && !/^[01]$/.test(v)) {
          const lo = v.toLowerCase();
          if (TRUTHY.has(lo)) {
            v = '1';
          } else {
            if (FALSY.has(lo)) {
              v = '0';
            }
          }
        }
        form[k] = v;
      }
      this.tuningForm = form;
      _tuningSnapshotMemo = null;  // bust the per-flush memo so the baseline reflects the just-loaded form
      this._tuningBaseline = this._tuningSnapshot();
      this.tuningLoaded = true;
    } catch (e) {
      this.showToast(this.t('admin.config.load_failed', {error: e.message}), 'error');
    }
  },
  _tuningSnapshot() {
    // Per-flush memo (see _tuningSnapshotMemo decl): the ~7 tuningDirty() reads
    // per flush share ONE 172-key serialization; cleared on the next microtask.
    if (_tuningSnapshotMemo !== null) {
      return _tuningSnapshotMemo;
    }
    const f = this.tuningForm || {};
    const out = {};
    for (const k of this._allTuningKeys()) {
      out[k] = (f[k] == null ? '' : String(f[k]).trim());
    }
    _tuningSnapshotMemo = JSON.stringify(out);
    if (!_tuningSnapshotScheduled) {
      _tuningSnapshotScheduled = true;
      queueMicrotask(() => {
        _tuningSnapshotMemo = null;
        _tuningSnapshotScheduled = false;
      });
    }
    return _tuningSnapshotMemo;
  },
  tuningDirty() {
    return this._tuningBaseline !== this._tuningSnapshot();
  },
  // Operator-readable order: sort the tunable rows alphabetically
  // by their resolved (translated) label so the form scans like a
  // glossary instead of a code-defined sequence. Returns a fresh
  // array per call — Alpine's reactive iteration doesn't memoise
  // x-for results, but the array is small (~20 entries) so the
  // sort cost is sub-millisecond. Falls back to the raw key when a
  // label translation is missing so a partially-translated bundle
  // still renders deterministically.
  sortedTuningKeys() {
    const keys = (this.tuningKeys || []).slice();
    // Cross-flush memo (see _sortedTuningKeysMemo decl): the sorted order only
    // changes on a language switch, so reuse the cached array until this.lang
    // (or the key count) changes instead of re-sorting + re-t()-ing per flush.
    if (_sortedTuningKeysMemo.lang === this.lang && _sortedTuningKeysMemo.len === keys.length) {
      return _sortedTuningKeysMemo.value;
    }
    const labelOf = (k) => {
      const lbl = this.t('admin.config.fields.' + k + '.label');
      // Missing-key fallback returns the path itself (per the i18n
      // helper's contract); detect that and use the bare key so
      // the sort doesn't bunch every untranslated row at the top.
      return (lbl && lbl !== 'admin.config.fields.' + k + '.label') ? lbl : k;
    };
    keys.sort((a, b) => labelOf(a).localeCompare(labelOf(b)));
    _sortedTuningKeysMemo = {lang: this.lang, len: keys.length, value: keys};
    return keys;
  },
  tuningPlaceholder(key) {
    const row = (this.tuningEffective || {})[key] || {};
    const env = row.env;
    const def = (row.default == null ? '' : String(row.default));
    if (env != null && String(env).trim() !== '') {
      return this.t('admin.config.placeholder_env', {value: env, default: def});
    }
    return this.t('admin.config.placeholder_default', {default: def});
  },
  async saveTuning() {
    if (this.tuningSaving) {
      return;
    }
    // saveTuning is the Admin → Config "Save all" path — commits
    // every tunable across every section. Per-section saves
    // (saveAiSettings / saveSshSettings / saveRetention / ...)
    // own their own subset of tunables and write them directly in
    // their own POST body — they do NOT call saveTuning.
    // client-side integer + bounds validation
    // before posting. Pre-fix the input was `type="number"` (rejects
    // letters) BUT the form still accepted decimals like "1.5" which
    // the backend silently truncated through the int cast. Now an
    // explicit Number.isInteger guard surfaces a clean toast naming
    // the field and the bound; the operator's value is preserved
    // until they fix it (no silent clamp).
    for (const k of this._allTuningKeys()) {
      const raw = (this.tuningForm || {})[k];
      if (raw === '' || raw == null) {
        continue;
      }  // blank = clear override
      const n = Number(raw);
      if (!Number.isFinite(n) || !Number.isInteger(n)) {
        this.showToast(this.t('admin.config.errors.must_be_int', {
          field: this.t('admin.config.fields.' + k + '.label'),
        }), 'error');
        return;
      }
      const eff = this.tuningEffective[k] || {};
      if (Number.isFinite(eff.min) && n < eff.min) {
        this.showToast(this.t('admin.config.errors.below_min', {
          field: this.t('admin.config.fields.' + k + '.label'),
          min: eff.min,
        }), 'error');
        return;
      }
      if (Number.isFinite(eff.max) && n > eff.max) {
        this.showToast(this.t('admin.config.errors.above_max', {
          field: this.t('admin.config.fields.' + k + '.label'),
          max: eff.max,
        }), 'error');
        return;
      }
    }
    this.tuningSaving = true;
    try {
      const body = {};
      for (const k of this._allTuningKeys()) {
        const v = (this.tuningForm || {})[k];
        body[k] = (v == null ? '' : String(v).trim());
      }
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      await this.loadTuning();
      // Refresh `me.client_config` from /api/me so any SPA bindings
      // that read tuning values via the client_config channel
      // (`ai_sidebar_width_px`, `hosts_parallel_fetch`, `ops_poll_ms`,
      // etc.) update immediately instead of waiting for the operator
      // to reload the page. The PATCH succeeded against the DB; the
      // next /api/me round-trip carries the new value into the
      // reactive me state, which Alpine then propagates to every
      // binding that references `me.client_config.<key>`.
      try {
        const rm = await fetch('/api/me');
        if (rm.ok) {
          const me = await rm.json();
          if (me && me.client_config) {
            this.me = me;
          }
        }
      } catch (_) { /* live-apply best-effort; reload still works */
      }
      this.showToast(this.t('admin.config.saved_toast'));
    } catch (e) {
      this.showToast(this.t('admin.config.save_failed', {error: e.message}), 'error');
    } finally {
      this.tuningSaving = false;
    }
  },
  // Helper used by section-scoped dirty trackers — parses the tuning
  // baseline JSON into an object so each section can compare its own
  // keys against the baseline without re-snapshotting the world.
  _tuningBaselineMap() {
    try {
      return JSON.parse(this._tuningBaseline || '{}') || {};
    } catch (_) {
      return {};
    }
  },
};
