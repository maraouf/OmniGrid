/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// noinspection ElementNotExported,JSUnusedGlobalSymbols,CheckTagEmptyBody,HtmlUnknownTag,HtmlExtraClosingTag
// Probe-derived fields that ``refreshHostRow`` writes EXPLICITLY from
// ``/api/hosts/one/{id}``'s payload — when the backend omits a key the
// row collapses it to ``null`` instead of letting the previous value
// stick. Curated config
// fields (``label`` / ``icon`` / ``ssh_enabled`` / ``ne_url`` /
// ``beszel_name`` / ``pulse_name`` / ``webmin_name`` / ``url`` /
// ``custom_number`` / ``asset``) are NOT in this list — ``loadHosts``
// owns them via ``CURATED_FIELDS`` and ``api/hosts/one`` does not edit
// them. When a provider extracts a new probe-derived ``host_*`` field,
// add the key here OR ship the matching backend snapshot whitelist
// entry — same hand-maintained pattern as ``_HOST_SNAPSHOT_KEYS`` in
// ``logic/gather.py``.

export const CURATED_REFRESH_FIELDS = new Set([
  // Status / failure-state surface.
  'status', 'providers', 'provider_errors',
  'sampling_paused', 'failure_window_started_at',
  'consecutive_failures', 'last_error', 'paused_at',
  'last_failure_ts',
  // Per-provider auto-pause state. `{snmp: {paused, ...},
  // webmin: {paused, ...}}` populated only when the provider has
  // a row in `host_failure_state`. Empty object for healthy hosts.
  'provider_pause_state',
  // Per-(provider, host) row counts from the local sample tables —
  // stamped by `_merge_one_host` on /api/hosts/one/{id}; surfaces under
  // the "Updated Xs ago" chip subtitle in the host drawer.
  'provider_sample_counts',
  // Per-provider effective sampler interval (seconds) — third chip
  // subtitle line. Same stamping path as provider_sample_counts.
  'provider_sample_intervals',
  // CPU / memory / disk / swap rollups.
  'cpu_percent', 'mem_percent', 'disk_percent',
  'host_cpu_percent', 'host_mem_total', 'host_mem_used',
  'host_disk_total', 'host_disk_used',
  'host_swap_used', 'host_swap_percent',
  'host_temperatures',
  // Network / disk-IO rates.
  'host_net_rx', 'host_net_tx',
  'host_disk_read_bps', 'host_disk_write_bps',
  // Identity / runtime.
  'host_platform', 'host_os', 'host_kernel', 'host_arch',
  // Kernel-reported hostname (uname -n) — distinct from curated id.
  // Surfaced by node-exporter / SNMP / Webmin so the AI palette
  // can match user-pasted `df -h` / `hostname` output back to a
  // curated host. Without this in the whitelist the value would be
  // collapsed to null on every poll's in-place reconcile.
  'host_hostname',
  // Hardware identity (DMI vendor / product / serial). Same purpose
  // as `host_hostname` — surface the operator-recognisable name so
  // the AI grounding matches across user descriptions ("the R730xd",
  // "the Pi 4", etc).
  'host_vendor', 'host_model', 'host_serial',
  'host_cpu_cores', 'host_cpu_model',
  'host_uptime_s', 'host_boot_ts',
  // Per-mount + per-NIC detail.
  'mounts', 'interfaces', 'network_ifaces',
  // Package updates.
  'package_updates_count', 'package_updates',
  // Load average.
  'host_load_1m', 'host_load_5m', 'host_load_15m',
  // Stale-marker bookkeeping.
  '_stale_fields', '_stale_ts',
  // probe wall-clock for the status-dot hover-title.
  '_probe_elapsed_ms',
  // Service-summary surface (Beszel systemd_services rollup).
  'host_services',
  // Ping (TCP/ICMP). ping_enabled is curated (per-host opt-in
  // flag from hosts_config[].ping.enabled) but it shapes the SPA's
  // reactive gates — openHostDrawer reads it to decide whether to
  // call loadHostPingHistory; the chart card's x-show gates on it
  // too. Pre-add it slipped through the CURATED_FIELDS / CURATED_
  // REFRESH_FIELDS audit so drawerHost.ping_enabled stayed undefined,
  // loadHostPingHistory never fired, and the chart was empty even
  // when ping_samples had 100+ rows. ping_alive / ping_rtt_ms /
  // ping_loss_pct are the per-tick probe state that drives the
  // header chips (red Unreachable / amber X% loss).
  'ping_enabled', 'ping_alive', 'ping_rtt_ms', 'ping_loss_pct',
  // SNMP. snmp_name is curated (per-host alias to the SNMP-
  // reachable target). Fetched on each /api/hosts response so the
  // chip in providerStates(h) tracks the operator's mapping. Probe
  // outputs (CPU/mem/disk/uptime) flow through the existing host_*
// schema fields above and don't need their own row here.
  // `snmp_enabled` (per-host opt-in flag) MUST also be in this
  // overlay set. Without it the in-place reconcile preserves the
  // stale `true` value on rows where the operator just unticked the
  // enable box, leaving the SNMP chip rendered indefinitely until
  // a hard refresh. Mirrors `ssh_enabled` and `ping_enabled` further
  // up in this list.
  'snmp_name', 'snmp_enabled',
  // APC UPS via PowerNet-MIB. Pre-fix these fell through to the
  // generic-assign loop which only writes keys present in `host`,
  // so a probe that didn't extract them (SNMP timeout, basic UPS
  // model, etc.) couldn't CLEAR a stale value — but the bigger
  // problem was the card gate `x-show="!!(h.host_ups_status)"` saw
  // empty string and hid the entire UPS info card. Explicit overlay
  // collapses missing keys to null so the gate behaves predictably
  // AND a recovered probe overwrites cleanly. Operator-reported
  // : UPS card hidden on a host where the SNMP probe was
  // working; root cause was the row being initialised pre-card-gate
  // before the probe had finished, with no subsequent overlay
  // because the field wasn't in this set.
  'host_ups_status', 'host_battery_status',
  'host_battery_percent', 'host_battery_runtime_s',
  'host_battery_temp_c', 'host_load_percent',
  // Hardware identity rows (model / serial / firmware / vendor) —
  // populated by the SNMP entityPhysical walk and a few vendor-
  // specific OIDs. Same reason as the UPS fields: explicit overlay
  // so they collapse to null when a probe goes missing instead of
  // sticking a stale value indefinitely.
  'host_model', 'host_serial', 'host_firmware', 'host_vendor',
  // Printer-MIB rollups. Supplies array + lifetime page
  // counter + console message — same overlay-explicit contract so
  // the printer card's row gates evaluate cleanly. Stale snapshot
  // fallback paints these dim with the .stale class via the
  // `isStaleField(h, '<key>')` gate; without these in the refresh
  // overlay the card body row gates couldn't tell "stale snapshot"
  // from "never had data".
  'printer_supplies', 'printer_page_count', 'printer_console_msg',
  // Dell iDRAC server-health surface (DELL-RAC-MIB tables —
  // coolingDevice / temperatureProbe / powerSupply / voltageProbe /
  // amperage / physical+virtual disk / systemBIOS). Per-row arrays so
  // the drawer can render fan / temp / PSU grids; chassis-power +
  // BIOS scalars feed the Hardware card. Same overlay contract as the
  // UPS fields above — explicit so a missed probe doesn't leave stale
  // grids on screen and snapshot fallback can repopulate via the
  // `host_*` predicate without a second whitelist edit.
  'host_dell_fans', 'host_dell_temps', 'host_dell_psus',
  'host_dell_voltages', 'host_dell_amperages',
  'host_dell_phys_disks', 'host_dell_virt_disks',
  'host_dell_power_watts',
  'host_bios_version', 'host_bios_date',
  // SNMP auto-detect diagnostic. Surfaces the most-recent
  // successful probe's vendor result so the Admin → Hosts editor
  // can render "Auto-detect last result: <vendors>" below the Vendor
  // MIBs checkbox group. Empty list when the probe never succeeded.
  'host_snmp_active_vendors', 'host_snmp_active_vendors_source',
  // Port-scan provider — on-demand scanner.
  // `detected_ports` is the latest scan's open-port array; null
  // collapse on missing keeps the drawer card gate cleanly hidden
  // when the master toggle is off OR no scan has run. `last_port_scan_ts`
  // drives the "Last scanned X ago" label.
  'detected_ports', 'last_port_scan_ts',
  // HTTP / TLS / DNS probe — seventh host-stats provider. Same
  // overlay-explicit contract as the UPS / printer / dell-* fields:
  // missing keys collapse to null so card gates behave predictably,
  // and a recovered probe overwrites cleanly. `host_http_urls` is
  // the per-URL detail array consumed by the drawer's HTTP card.
  // `host_http_url_count_total` / `host_http_url_count_ok` drive
  // the "N/M URLs healthy" rollup label.
  'host_http_status_ok', 'host_http_status_code',
  'host_http_content_match_ok', 'host_http_tls_expires_in_days',
  'host_http_tls_subject', 'host_http_tls_issuer',
  'host_http_dns_resolved', 'host_http_dns_error',
  'host_http_latency_ms', 'host_http_error',
  'host_http_url_count_total', 'host_http_url_count_ok',
  'host_http_urls', 'host_http_ts',
  // Per-host opt-in flag — same overlay contract as ping_enabled /
  // snmp_enabled / ssh_enabled. Without this in the whitelist a fresh
  // save that flips the box OFF leaves the in-place reconcile sticking
  // on the stale `true` value, so the http_probe chip in
  // providerStates(h) keeps rendering until a hard refresh.
  'http_probe_enabled', 'http_probe_urls', 'http_probe_has_targets',
  // Drift-from-baseline classification — per-metric
  // {indicator, value, median, iqr, ...} dict keyed by cpu_pct /
  // mem_pct / disk_pct / ping_rtt_ms. Empty {} when the host has no
  // baseline yet (<50 samples in window OR sampler hasn't run yet).
  // Explicit overlay so a baseline that disappears (host deleted
  // from hosts_config and re-added, or sample tables pruned beneath
  // the threshold) collapses the chip cleanly instead of sticking
  // a stale indicator.
  'drift',
]);
