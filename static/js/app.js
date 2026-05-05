// Registry of every icon file that actually exists under static/img/icons/.
// `iconUrlFor()` consults this before returning a /img/icons/<slug>.svg URL
// so unknown stack / host names don't trigger 404 noise in the browser
// console — operators flagged "Failed to load resource: website-monitoring.svg"
// for a stack that simply doesn't have a brand mark. With the registry, the
// resolver returns '' for unrecognized slugs and the SPA's existing
// `x-show="iconUrl"` gates hide the <img> entirely.
//
// Auto-built from `ls static/img/icons/*.svg | sed 's/\.svg$//'`. Re-run that
// pipeline (or `scripts/sync_icon_registry.sh` if/when added) after adding or
// removing icons.
// Slugs that ship a `<slug>-dark.svg` variant alongside the default
// `<slug>.svg`. The icon resolver consults `_themeIcon(url)` at every
// emit point and auto-swaps to the `-dark.svg` URL when the document
// is in dark theme. Slugs NOT in this set get the same URL on both
// themes — most brand icons render fine on both backgrounds and don't
// need the second file. Adding a new dark variant: drop the
// `<slug>-dark.svg` under `static/img/icons/` AND add the slug here.
// Operators who set `h.icon = '<slug>-dark'` explicitly bypass the
// auto-swap (the `-dark` suffix is detected and short-circuits).
const KNOWN_DARK_ICONS = new Set([
  // Pre-#451 manual variants — also listed in KNOWN_ICONS as separate
  // slugs (`glinet-dark`, `portainer-dark`) for explicit-override
  // compatibility, but operators using the bare slug get the auto-swap
  // here.
  'glinet',
  'portainer',
  // Apple's bare logo is jet-black on the default file (homarr-labs
  // upstream `apple.svg`). The dark-theme variant `apple-dark.svg`
  // carries the white logo (sourced from upstream `apple-light.svg`,
  // re-saved under our standardised `-dark.svg` filename so the
  // resolver convention stays uniform: `<slug>-dark.svg` is always
  // "use this on dark theme" regardless of which side the upstream
  // calls "light" or "dark").
  'apple',
  // Apple TV+ — upstream naming matches our convention: their
  // `apple-tv-plus.svg` is the light-theme variant, their
  // `apple-tv-plus-light.svg` is the dark-theme variant (lighter
  // colours visible on dark bg). Saved locally as `apple-tv-plus.svg`
  // and `apple-tv-plus-dark.svg` respectively.
  'apple-tv-plus',
  // Synology — homarr-labs' upstream `synology.svg` is the dark-bg
  // variant (light-coloured logo); their `synology-light.svg` is the
  // light-bg variant. Saved locally as `synology.svg` (light-theme
  // default) and `synology-dark.svg` (dark-theme variant) so our
  // standard `<slug>-dark.svg` convention holds.
  'synology',
  // Dell — same upstream-`-light`-means-dark-colour-variant pattern as
  // Apple / Apple TV+. Local `dell.svg` is upstream `dell.svg`,
  // local `dell-dark.svg` is upstream `dell-light.svg`.
  'dell',
  // Amazon — same `-light`-means-dark-colour pattern. Local
  // `amazon.svg` from upstream `amazon.svg`; local `amazon-dark.svg`
  // from upstream `amazon-light.svg`.
  'amazon',
]);

const KNOWN_ICONS = new Set([
  '5g', 'adguard-home', 'alexa', 'alienware', 'amazon', 'amazon-dark', 'ansible',
  'apache', 'apc', 'apc-ups', 'apple', 'apple-dark', 'apple-light', 'apple-tv-plus',
  'apple-tv-plus-dark', 'apple-tv-plus-light', 'apprise', 'aqara', 'asus', 'authentik', 'bazarr',
  'beszel', 'bose', 'caddy', 'chromecast', 'cisco', 'cloudflare', 'cloudflared', 'database',
  'ddns-updater', 'debian', 'dell', 'dell-dark', 'deluge', 'docker', 'dovecot',
  'dozzle', 'esxi', 'fing', 'firetv', 'flaresolverr', 'forgejo',
  'freenas', 'ftth', 'gigabyte', 'gitsync', 'glinet', 'glinet-dark', 'google',
  'google-home', 'grafana', 'hisense', 'homarr', 'home-assistant', 'homebridge',
  'hdhomerun', 'homepage', 'hp', 'huawei', 'humax', 'idrac', 'ikea', 'ilo',
  'influxdb', 'jellyfin', 'jellyseerr', 'jtech', 'kali', 'kaonmedia', 'kavita', 'keycloak',
  'komodo', 'kubernetes', 'lenovo', 'linuxmint', 'lubelogger', 'mail',
  'mailcow', 'meta', 'microsoft', 'mikrotik', 'mongodb', 'motorola',
  'myspeed', 'n8n', 'nest', 'netboot-xyz', 'netdata', 'nginx', 'nixplay',
  'nginx-proxy-manager', 'nintendo-switch', 'nzbget', 'oculus', 'openvpn', 'opnsense',
  'pfsense', 'pi-hole', 'pihole', 'pivpn', 'playstation', 'plex',
  'portainer', 'portainer-dark', 'postgresql', 'poweredge', 'proliant', 'prometheus',
  'prowlarr', 'proxmox', 'pulse', 'qbittorrent', 'rachio', 'radarr',
  'reolink', 'roku', 'roundcube', 'rundeck', 'rustdesk', 'sabnzbd',
  'samsung', 'samsung-electronics', 'sandisk', 'seeedstudio', 'sensibo', 'smtp', 'somfy', 'sonarr',
  'speedtest-tracker', 'squid', 'stalwart', 'synology', 'synology-dark', 'tailscale', 'tautulli',
  'tracearr', 'traefik', 'transmission', 'truenas', 'truenas-core', 'truenas-scale',
  'ubiquiti', 'ubuntu', 'ui', 'unifi', 'ups', 'uptime-kuma',
  'vcenter', 'vdsl', 'veeam', 'vmware', 'vsphere', 'wd',
  'webmin', 'windows', 'windows-10', 'windows-server', 'wireguard', 'xiaomi',
  'zabbix',
]);

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
const CURATED_REFRESH_FIELDS = new Set([
  // Status / failure-state surface.
  'status', 'providers', 'provider_errors',
  'sampling_paused', 'failure_window_started_at',
  'consecutive_failures', 'last_error', 'paused_at',
  'last_failure_ts',
  // Per-provider auto-pause state. `{snmp: {paused, ...},
  // webmin: {paused, ...}}` populated only when the provider has
  // a row in `host_failure_state`. Empty object for healthy hosts.
  'provider_pause_state',
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
]);

function app() {
  return {
    // i18n reactive state. `lang` is watched to trigger Alpine re-renders
    // when the user swaps languages. `t()` is forwarded to the global
    // helper so every template can do `x-text="t('nav.stacks')"`.
    lang: (window.I18N && window.I18N.code) || 'en',
    dir: (window.I18N && window.I18N.dir) || 'ltr',
    availableLanguages: (window.I18N && window.I18N.languages) || [{ code: 'en', name: 'English', dir: 'ltr' }],
    t(key, vars) {
      // Touch `this.lang` so Alpine tracks this binding as a dependency
      // of the reactive `lang` state — when setLang() updates `lang`,
      // every x-text="t(...)" re-evaluates automatically.
      void this.lang;
      return (window.I18N ? window.I18N.t(key, vars) : key);
    },
    async setLang(code) {
      if (!code || code === this.lang) return;
      try {
        await window.I18N.load(code);
        this.lang = code;
        this.dir  = window.I18N.dir;
        this.showToast(this.t('toasts.language_changed') || 'Language changed');
      } catch (e) {
        // Language fetch failed — surface a fallback message that doesn't
        // require the new dict to have loaded.
        this.showToast(this.t('toasts_extra.language_load_failed'), 'error');
      }
    },
    items: [], stacks: [], nodes: {}, nodesInfo: {},
    history: [], ignores: [],
    // Server-side paging for the audit log (history). Backend's
    // /api/history accepts ?offset=&limit= and returns {history, total,
    // offset, limit}. Per-page + page persist to localStorage so the
    // operator returns to the same view on refresh; filter changes
    // reset to page 1 (large jumps within the same dataset stay where
    // the operator was).
    historyTotal: 0,
    historyPage: (typeof localStorage !== 'undefined'
                    ? Math.max(1, parseInt(localStorage.getItem('historyPage') || '1', 10) || 1)
                    : 1),
    historyPerPage: (typeof localStorage !== 'undefined'
                    ? Math.max(10, Math.min(500, parseInt(localStorage.getItem('historyPerPage') || '50', 10) || 50))
                    : 50),
    stats: {}, _statsTimer: null, _maxSize: 1,
    // Flips to true on the first successful `/api/stats` response so
    // the Stacks / Services rows swap their loading spinner for the
    // resolved status dot. Stays true once flipped — the spinner is
    // an initial-paint affordance, not a per-poll signal.
    statsLoaded: false,
    // Swarm agent unhealthy banner — populated by `loadStats` from
    // `/api/stats`'s `unhealthy_agents` field. Each entry is
    // `{host, fails, since_ts, task_cids}`. Banner renders at the
    // top of Stacks + Hosts views when the array is non-empty.
    unhealthyAgents: [],
    // In-flight flag for the unhealthy-agent banner's "Restart agent
    // service" button. Disables the button + flips the icon to a
    // spinner while the op runs. Cleared on success / failure toast.
    swarmAgentRestartBusy: false,
    sparks: {}, _sparksTimer: null,
    version: '',
    // Topbar clock + weather widgets. Per-browser preferences kept in
    // localStorage so each operator's city survives refresh. Clock is
    // purely client-side; weather goes through the backend proxy at
    // /api/weather (Open-Meteo, no API key, 10-min server cache).
    headerClockEnabled: (typeof localStorage !== 'undefined' && localStorage.getItem('headerClockEnabled') !== 'false'),
    headerWeatherEnabled: (typeof localStorage !== 'undefined' && localStorage.getItem('headerWeatherEnabled') === 'true'),
    headerWeatherLat:   (typeof localStorage !== 'undefined' ? (parseFloat(localStorage.getItem('headerWeatherLat') || '') || null) : null),
    headerWeatherLon:   (typeof localStorage !== 'undefined' ? (parseFloat(localStorage.getItem('headerWeatherLon') || '') || null) : null),
    headerWeatherLabel: (typeof localStorage !== 'undefined' ? (localStorage.getItem('headerWeatherLabel') || '') : ''),
    currentClock: '',
    weather: null,
    _clockTimer: null,
    _weatherTimer: null,
    // Version snapshot captured at first load. `watchVersion()` polls
    // /api/version every 60s and compares against this; mismatch means
    // CI just shipped a new build so we flip `newVersionAvailable` and
    // the topbar banner prompts the user to hard-reload. Prevents
    // operators from staring at stale UI after a deploy.
    bootVersion: '',
    newVersionAvailable: false,
    newVersionString: '',
    _versionTimer: null,
    historyFilters: { q: '', stack: '', op_type: '', status: '', actor: '', fromDate: '', toDate: '' },
    statsInterval: (() => {
      const v = parseInt(localStorage.getItem('statsInterval'), 10);
      return [0, 5, 15, 30, 60].includes(v) ? v : 15;
    })(),
    activeOps: [],
    // Real-time event stream. EventSource connection to /api/events;
    // when healthy, every polling loop in this component idles. When the
    // stream drops AND we haven't seen a heartbeat for ``_sseIdleThresholdMs``
    // ms, polling resumes as the fallback. Re-connect retries are handled
    // by EventSource itself (browser native); we only track the connection
    // state for the toolbar indicator and the polling-fallback gate.
    _sse: null,
    _sseConnected: false,
    _sseLastEventTs: 0,
    _sseIdleThresholdMs: 30000,
    // running tally of `:overflow` events received this session.
    // Renders a small amber chip alongside the SSE pill when > 0 so
    // operators can see "events have been dropped" without watching
    // the console. Reset to 0 only on full page reload (per-tab state).
    _sseDropped: 0,
    // Pill-flash signal (#486 enhancement). Reactive boolean that
    // toggles true at the START of each interval-mode poll and back
    // to false ~600ms later, giving the topbar pill a visible green
    // pulse on every tick. Lets operators see "the system is polling
    // right now" without watching the network tab. Independent of
    // SSE state — only fires under interval modes (Live uses push,
    // Off doesn't poll).
    _pollFlashing: false,
    _pollFlashTimer: null,
    _sseReconnects: 0,
    // Heartbeat window (server emits ``: keepalive`` every 25s). The
    // freshness watch ticks once per second to flip _sseConnected back
    // to false if no traffic arrives for the threshold above. Acts as
    // a belt-and-braces safety check on top of EventSource's onerror
    // (which doesn't always fire on silent half-open sockets).
    _sseFreshnessTimer: null,
    view: (['stacks','services','nodes','hosts','history','settings','admin'].includes(localStorage.getItem('view')) ? localStorage.getItem('view') : 'stacks'),
    // In-app notifications. Surfaces as a POPUP overlay (NOT a
    // top-level view) — operators wanted a quick check + dismiss without
    // navigating away from whatever they were doing. Same modal pattern
    // as the hotkeys help dialog. Loaded via /api/notifications when
    // the operator opens the popup OR via SSE pushes
    // (notification:created / :read / :deleted). The avatar-bar
    // unread chip pulls from `notificationsUnread` independently of the
    // list — that count is global, the popup's filters can scope.
    showNotificationsPopup: false,
    notifications: [],
    notificationsUnread: 0,
    notificationsTotal: 0,
    notificationsLoading: false,
    // Notifications page size. Initial value is 25 (UX-tightened from
    // the previous 50 — the popup felt overwhelming on busy fleets).
    // Operator can override via Admin → Notifications → "Notifications
    // page size" which writes to `tuning_notification_page_size` and
    // is delivered to the SPA via /api/me's `client_config`. Falls
    // back to 25 here when the API hasn't surfaced the override yet.
    notificationsLimit: 25,
    notificationsOffset: 0,
    // Filter state — persisted in-memory only; a reload starts with
    // every severity visible and unread-only off so operators land on
    // a complete view rather than an accidentally-empty page.
    notificationsFilterSeverity: 'all',  // all | info | warning | error | success
    notificationsFilterEvent:    'all',
    notificationsFilterUnread:   false,
    // Polling fallback for bearer-token clients (SSE skips them per
    // CLAUDE.md). Always running but no-ops when the view isn't open
    // and SSE is healthy.
    _notificationsPollHandle: null,
    search: '', statusFilter: '', healthFilter: '',
    sortField: 'name', sortDir: 'asc',
    selected: [],
    expanded: (() => { try { return JSON.parse(localStorage.getItem('expanded') || '[]'); } catch (e) { return []; } })(),
    loading: false,
    // Background-refresh indicators. The /api/items + /api/hosts/list
    // endpoints serve cached / snapshot data instantly when warm and
    // kick a background gather → set `cache_refreshing: true` /
    // `hub_probing: true` on the response. The topbar refresh button
    // pulses + reads "Refreshing…" while these are true so operators
    // see the system is working even when the foreground call is done.
    cacheRefreshing: false,
    hubProbing: false,
    statsRefreshing: false,
    drawerItem: null,
    // Node drawer — separate from drawerItem. Opens from the Nodes view
    // when the operator clicks a node row. Shape: {name, aliasInput}.
    drawerNode: null,
    drawerNodeSaving: false,
    // Hosts view state (Beszel-backed). Refreshed via /api/hosts on a
    // separate cadence from the item cache — hub calls are cheap and
    // the view wants faster feedback than the 15-30s item refresh.
    hosts: [],
    hostsError: '',
    hostsProviderErrors: {},  // {beszel: "hub 500", pulse: "...", ...}
    hostsActiveSources: [],   // list of "beszel" / "pulse" / "node_exporter"
    hostsConfigured: true,
    hostsLoading: false,
    // True only AFTER the first loadHosts() response has landed. The
    // "no host data" empty-state ladder gates on this so a fresh page
    // load (where hosts=[] / hostsCuratedCount=0 / hostsLoading=false
    // are all true initial values) doesn't flash "No host data to show"
    // before the first /api/hosts/list call resolves. Skeleton renders
    // until this flips, then the real empty states take over.
    hostsInitialLoaded: false,
    // IntersectionObserver-driven lazy fetch for /api/hosts/one/{id}.
    // For fleets of 100+ hosts the historical "fan out every row at
    // page load" pattern burned backend probes + sockets for rows
    // the operator never scrolls to. The observer (lazy-created in
    // `_observeHostRow`) watches every `data-host-id="..."` row and
    // adds the id to `_hostSeenIds` on first intersection — also
    // triggering an immediate `refreshHostRow` for that one id.
    // Subsequent 15s polls (`loadHosts`) re-fetch every host whose id
    // is in `_hostSeenIds`, so once an operator has scrolled past a
    // row it stays fresh. Off-screen rows that have NEVER been seen
    // pay zero probe cost. Set is non-reactive (vanilla Set, not
    // Alpine-tracked) — Alpine doesn't need to render anything from
    // it, and avoiding reactivity prevents the in-loop `add()` from
    // triggering a fan-out re-render.
    _hostSeenIds: new Set(),
    _hostRowObserver: null,
    // Persisted across browser refresh — mirrors the 'expanded' state
    // that the Stacks view already stores. Parsing tolerates stale /
    // invalid JSON so a corrupt entry doesn't break the whole view.
    hostsExpanded: (() => {
      try {
        const raw = typeof localStorage !== 'undefined' && localStorage.getItem('hostsExpanded');
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.filter(x => typeof x === 'string') : [];
      } catch {
        return [];
      }
    })(),
    // Slide-out drawer mode — clicking a host row opens this
    // drawer instead of expanding the row inline. `drawerHost` is the
    // live host object reference (kept up-to-date by the existing
    // `loadHosts` reconcile loop, since rows mutate fields in place
    // rather than reassigning the array). Null = drawer closed.
    drawerHost: null,
    // Open/closed state for the per-host health-score breakdown
    // popover inside the drawer. Default closed; the chip in the
    // drawer header toggles it. Resets to false on closeHostDrawer
    // so reopening a drawer always lands collapsed.
    healthPopoverOpen: false,
    // ---- Hosts bulk-selection state ---------------------------------
    // Reactive Set of host ids the operator has selected via the row
    // checkbox. Drives the sticky bottom action bar visibility +
    // count badge. Set so membership checks are O(1) and the reactive
    // get/set proxy still sees mutation. Cleared on view change AND
    // when the operator clicks "Clear" on the bar.
    selectedHosts: new Set(),
    // Bulk-action modals — pure UI state. Each opens a SweetAlert-style
    // dialog so the operator can review the change before sending.
    bulkSnmpVendorsModal: { open: false, vendors: [], mode: 'set' },
    bulkSnmpTunablesModal: { open: false, walk_concurrency: '', wall_clock_budget: '', clear: false },
    // Progressive UI feedback after a bulk action lands. Set to
    // {applied, total, action, ts} for ~5 s after the response, then
    // cleared by `_bulkAppliedTimer`. Each affected host row also
    // gets a transient `_bulkApplied: true` flag drained at the
    // same time. Drives the small "✓ N/M applied" badge above the
    // Hosts toolbar AND the per-row check glyph next to the
    // hostname so the operator gets a per-host confirmation as
    // each id settles.
    bulkAppliedSummary: null,
    _bulkAppliedTimer: null,
    // ---- Host timeline state ----------------------------------------
    // Per-host timeline cache. Shape: hostTimeline[host_id] = {events,
    // counts, loading, error, loadedAt, hours}. The drawer's Timeline
    // card consumes this directly; reactive so the chevron rotates +
    // empty/loading/error states render off the same map.
    hostTimeline: {},
    // Per-host expand state — Timeline card is collapsed by default
    // so the drawer's first paint stays light. Persisted only in
    // memory (not localStorage) — fresh open should always start
    // collapsed.
    timelineExpanded: {},
    // Per-host range picker state (24h / 7d / 30d). Default 7d.
    hostTimelineRange: {},
    hostsSearch: '',
    // clickable provider filter on the Hosts toolbar. Set of
    // active filters (provider names + 'none' for "no provider mapped")
    // — empty Set means show everything. Multi-select with OR semantics:
    // a host matches when it carries ANY of the selected providers (or
    // matches the 'none' synthetic when no provider is set on the row).
    // Persists to sessionStorage so a tab reload preserves the filter
    // but a fresh tab starts clean.
    hostsProviderFilter: new Set(
      (typeof sessionStorage !== 'undefined'
        && (sessionStorage.getItem('hostsProviderFilter') || '').split(','))
        .filter(Boolean)
    ),
    // Sort key for the Hosts view. Persisted to localStorage so the
    // operator's preferred order sticks across reloads. Supported keys:
    //   'status' (default — alive first, then paused, down, unknown)
    //   'seq'    (curated-list addition order; 'insertion' alias)
    //   'name'   (alphabetical on label or host)
    //   'type'   (platform / OS group — same-kind hosts cluster)
    //   'cpu' / 'mem' / 'disk' (descending — hottest first)
    //   'uptime' (descending — longest running first)
    hostsSort: (typeof localStorage !== 'undefined' && localStorage.getItem('hostsSort')) || 'status',
    // Hide hosts that have NO agent fields configured (no beszel_name,
    // no pulse_name, no ne_url, no webmin_name). Persisted across reloads.
    // Useful when the curated list contains inventory-only entries (FTTH /
    // 5G routers, switches without an exporter) and the operator wants
    // to focus on rows that actually probe.
    hostsHideUnconfigured: (typeof localStorage !== 'undefined' && localStorage.getItem('hostsHideUnconfigured') === '1') || false,
    // Per-host debug payloads (admin-only). Keyed by host.id. Lazily
    // populated when the operator opens the drawer's Debug panel so
    // the normal hosts-view cadence stays cheap.
    hostsDebug: {},
    hostsDebugLoading: {},   // {host_id: true} while the fetch is in flight
    hostsDebugOpen: {},      // {host_id: true} = panel is expanded
    // Per-host expand/collapse state for high-count Server health
    // sub-sections (Physical disks / Voltages). Default-collapsed when
    // the section's row count exceeds the dense-layout threshold (12);
    // operator clicks "Show all (N)" to expand.
    serverHealthExpanded: {},
    hostsCuratedCount: 0,
    hostsEnabledCount: 0,
    _hostsTimer: null,
    // Admin → Hosts editor state. ``hostsConfig`` is the curated list
    // pulled from /api/hosts/config (array of host records with
    // per-provider name mappings). ``hostsConfigSaving`` gates the
    // Save button's spinner.
    hostsConfig: [],
    // Stable display-order snapshot for the hostsConfig editor.
    // Rebuilt on load / add / remove / blur of custom_number — NOT on
    // every keystroke. Keeps rows from re-sorting mid-typing. See
    // filteredHostsConfig() + rebuildHostsConfigOrder() for the rule.
    hostsConfigSortedOrder: [],
    // Per-field validation errors for inline rendering. Keys
    // follow the pattern "<scope>_<idx>_<field>" (e.g.
    // "host_3_webmin_url", "group_0_range"). `setFieldError` /
    // `clearFieldError` / `hasFieldError` / `fieldError` wrap the map
    // so callers don't have to know the exact storage shape.
    fieldErrors: {},
    hostsConfigLoading: false,
    hostsConfigSaving: false,
    hostsConfigDirty: false,
    hostsConfigFilter: '',
    // Client-side pagination for the Admin → Hosts editor. At
    // ~200 hosts the rendered DOM (each row is a multi-input form
    // card) becomes heavy; slicing the rendered list to one page at
    // a time keeps tab switches + filter typing snappy. Full array
    // still lives in `hostsConfig`, so dirty tracking + duplicate-id
    // validator + save-path are untouched. Page index persists across
    // reloads so the operator returns to the same page after
    // tab navigation, full reload, or browser restart. A `$watch` in
    // `init()` writes the value back; a clamp in `loadHostsConfig`
    // catches the case where the stored page is now beyond the data.
    hostsConfigPage: (() => {
      try {
        const raw = localStorage.getItem('hostsConfigPage');
        const n = parseInt(raw, 10);
        if (Number.isFinite(n) && n >= 1) return n;
      } catch {}
      return 1;
    })(),
    hostsConfigPerPage: (() => {
      try {
        const raw = localStorage.getItem('hostsConfigPerPage');
        const n = parseInt(raw, 10);
        if (Number.isFinite(n) && [10, 25, 50, 100, 200].includes(n)) return n;
      } catch {}
      return 50;
    })(),
    // Datalist-backed autocomplete source for the Hosts editor.
    // Filled by discoverHosts() on demand; stays empty until the
    // operator asks.
    hostsDiscovery: { beszel: [], pulse: [], webmin: [], snmp: [] },
    hostsDiscovering: false,
    // Per-row test results keyed by row index. Each entry has
    // ``pending: bool`` and the provider payloads {beszel, pulse,
    // node_exporter} each with {ok, skipped, detail}. Cleared when
    // a row's fields change so stale results aren't shown.
    hostsTestResults: {},
    hostsTestingAll: false,
    // Host grouping (ticket #93). Operator-defined ranges over the
    // custom_number field. Loaded in loadSettings; edited inline in
    // Admin → Hosts (below the row editor); persisted via POST
    // /api/settings {host_groups: [...]}. Collapse state is keyed
    // by group name, persisted to localStorage.
    hostGroups: [],
    hostGroupsDirty: false,
    hostGroupsSaving: false,
    hostGroupsCollapsed: (() => {
      try {
        const raw = typeof localStorage !== 'undefined'
          ? localStorage.getItem('hostGroupsCollapsed') : null;
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.filter(x => typeof x === 'string') : [];
      } catch { return []; }
    })(),
    // Editor-side collapse for parent rows in Admin → Host Groups —
    // distinct from `hostGroupsCollapsed` (which is the Hosts-view
    // bucket collapse). When a parent name is in this set, the editor
    // hides its child rows so the page doesn't grow to 50+ cards on
    // deep nesting. Persisted by parent_name so it survives reloads.
    hostGroupsEditorChildrenCollapsed: (() => {
      try {
        const raw = typeof localStorage !== 'undefined'
          ? localStorage.getItem('hostGroupsEditorChildrenCollapsed') : null;
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.filter(x => typeof x === 'string') : [];
      } catch { return []; }
    })(),
    // Pagination state for the Admin → Host groups editor. Mirrors the
    // hostsConfigPage / hostsConfigPerPage pattern from #331 so the
    // admin tab can scale past ~50 groups without a 100-card scroll.
    // Persisted to localStorage so a refresh / tab nav lands the
    // operator on the same page they left.
    hostGroupsPage: (() => {
      try {
        const raw = localStorage.getItem('hostGroupsPage');
        const n = parseInt(raw, 10);
        if (Number.isFinite(n) && n >= 1) return n;
      } catch {}
      return 1;
    })(),
    hostGroupsPerPage: (() => {
      try {
        const raw = localStorage.getItem('hostGroupsPerPage');
        const n = parseInt(raw, 10);
        if (Number.isFinite(n) && [10, 25, 50, 100, 200].includes(n)) return n;
      } catch {}
      return 50;
    })(),
    // Asset inventory (ticket #78). `assetForm` is the editable form
    // state; `assetStatus` mirrors the server snapshot (secret `_set`
    // flag etc.). `assetCache` is the loaded /api/asset-inventory
    // payload — drives the preview block + drawer lookups.
    assetForm: {
      auth_mode: 'oauth2',
      base_url: '', token_url: '', client_id: '',
      client_secret: '', scope: '',
      lifetime_token: '',
      service: '', action: '',
      min_value: '', max_value: '',
      edit_url_template: '',
      verify_tls: true,
    },
    assetStatus: null,
    assetTestResult: null,
    assetCache: null,
    assetRefreshing: false,
    assetSaving: false,
    // Per-system time-series cache keyed by Beszel record id.
    // Shape: { [system_id]: { loading, error, series: [{t,cpu,mp,dp,b,...}] } }
    hostHistory: {},
    // Operator's selected drawer-chart range (1 / 6 / 24 / 168 hours).
    // Persisted to `localStorage.hostHistoryRange` so a page refresh
    // doesn't snap them back to the 1h default — operators leaving the
    // drawer on 7d while glancing away expected the picker to remember.
    // Validated against the four canonical values; anything else falls
    // back to 1.
    hostHistoryRange: (() => {
      try {
        const v = +localStorage.getItem('hostHistoryRange');
        return [1, 6, 24, 168].includes(v) ? v : 1;
      } catch (_) { return 1; }
    })(),
    // Wall-clock millis ticked every 30s by init() — drives the
    // "Updated Xm ago" freshness hint in the host-drawer chart-grid
    // header. Reactive so Alpine re-evaluates the helper.
    hostHistoryNow: 0,
    showHotkeys: false,
    // Command palette state (Cmd-K / Ctrl-K). Drops the operator into
    // any drawer / view / setting from anywhere. Toggle from the
    // hotkey handler; markup at the bottom of static/index.html.
    commandPaletteOpen: false,
    commandPaletteQuery: '',
    commandPaletteSelectedIdx: 0,
    opsExpanded: true,
    toast: '', toastType: 'success', _tt: null,
    // Auto-refresh cadence (seconds; 0 = off). Persisted to
    // localStorage so the operator's chosen cadence survives a
    // browser refresh — previously it reset to 0 on every reload.
    //
    // `refreshInterval` is now the canonical operator-set
    // cadence; legacy `autoRefresh` (items poll) and `statsInterval`
    // (stats poll) are mirrored to it on every change so the existing
    // pollers don't need to be rewired. Migration: prefer existing
    // `refreshInterval`, fall back to `statsInterval` (more granular),
    // then `autoRefresh`. New state of the world is "Live (SSE) OR
    // one cadence drives every poll uniformly".
    refreshInterval: (() => {
      // -1 = Live (SSE-driven); 0 = Off; 30/60/300 = polling cadence.
      // Default to Live for fresh installs — the SPA's whole reason
      // for SSE is that operators don't have to think about cadence.
      try {
        const k = localStorage.getItem('refreshInterval');
        if (k != null) {
          const n = parseInt(k, 10);
          if ([-1, 0, 30, 60, 300].includes(n)) return n;
        }
        const s = parseInt(localStorage.getItem('statsInterval') || '', 10);
        if ([0, 30, 60, 300].includes(s)) return s;
        const a = parseInt(localStorage.getItem('autoRefresh') || '', 10);
        if (Number.isFinite(a) && a >= 0) return [0, 30, 60, 300].includes(a) ? a : 60;
        return -1;
      } catch { return -1; }
    })(),
    autoRefresh: (() => {
      try {
        const n = parseInt(localStorage.getItem('autoRefresh') || '0', 10);
        return Number.isFinite(n) && n >= 0 ? n : 0;
      } catch { return 0; }
    })(),
    _autoTimer: null, _opsTimer: null,
    cacheLabel: '',
    settings: { apprise_url: '', apprise_tag: '', swarm_autoheal_action: 'notify', swarm_autoheal_bootstrap_enabled: true, portainer_public_url: '', debug_panel_enabled: true,
                // TOTP / 2FA policy defaults so the Admin -> Config inputs
                // bind cleanly before the first /api/settings response.
                totp_allowed: true, totp_required_for_admins: false, totp_required_for_users: false,
                totp_lockout_max_failures: 5, totp_lockout_minutes: 15,
                // Passkey master toggle default — same `true` as the backend's
                // `_TOTP_POLICY_DEFAULTS["passkeys_allowed"]` so the form
                // renders the box checked before the first /api/settings hit.
                passkeys_allowed: true },
    schedulerSaving: false,
    openMeteoSaving: false,
    scheduleSaving: false,   // schedule modal Save button
    retentionSaving: false,  // Backups → retention save
    // in-flight flags for Admin tab Save buttons. Each toggles
    // around the corresponding save function so the button shows
    // "Saving…" + disabled state during the POST. Standardised
    // pattern matching #555's hostStatsSaving — operator request
    // for visual + behavioural consistency across every Save action.
    settingsSaving: false,       // Admin → Notifications (saveSettings)
    portainerSaving: false,      // Admin → Portainer
    oidcSaving: false,           // Admin → OIDC
    // sshSettingsSaving — already declared elsewhere as sshSettingsBusy.
    // totpPolicySaving — already declared at line ~2580 with its save fn.
    // assetSaving — already declared at line ~431.
    // Admin → Hosts per-row collapse state. Keyed by host id so
    // expand/collapse survives reorders. Fresh rows auto-expand on
    // addHostRow so the operator sees the fields. Saved into
    // localStorage so a page reload doesn't re-collapse everything.
    hostsConfigExpanded: (() => {
      try {
        const raw = typeof localStorage !== 'undefined'
          ? localStorage.getItem('hostsConfigExpanded') : null;
        const parsed = raw ? JSON.parse(raw) : {};
        return (parsed && typeof parsed === 'object') ? parsed : {};
      } catch { return {}; }
    })(),
    // Per-host DISKS card collapse state for the "Show N empty" toggle.
    // Keyed by host.host. In-memory only — zero-usage mounts rarely
    // flip across browser sessions so persistence isn't worth the
    // localStorage overhead.
    hostDisksShowEmpty: {},
    // Baseline snapshot of the host-stats settings at the last
    // successful load or save. Compared against the live form to
    // derive a "dirty" boolean for the Save button's visual
    // treatment. Captured in loadSettings + after successful
    // saveHostStats so the indicator clears correctly.
    _hostStatsBaseline: '',
    // User-menu dropdown (top-right avatar button)
    userMenuOpen: false,
    // Password change form (lives in the Profile settings section now, not a modal)
    passwordForm: { current: '', next: '', confirm: '' },
    passwordBusy: false,
    // OIDC settings — `oidcStatus` is the server snapshot; `oidcForm` is
    // the editable form state before Save. Client secret is write-only —
    // we never populate it back from the server, and blank-on-save means
    // "keep existing". Same pattern as the Portainer API-key field below.
    oidcStatus: null,
    oidcForm: {
      enabled: false, issuer_url: '', client_id: '', client_secret: '',
      redirect_uri: '', scopes: 'openid email profile groups',
      admin_group: 'omnigrid-admins',
    },
    oidcTestResult: null,

    // Portainer connection — same DB-backed / UI-managed pattern as OIDC.
    // API key is write-only; blank on save means "keep current".
    portainerStatus: null,
    // UX-003: lightweight "is Portainer configured?" flag refreshed on
    // every /api/items poll. Distinct from `portainerStatus` which is
    // populated from the heavier /api/settings response only when the
    // settings page loads. null = unknown (initial paint), true/false =
    // explicit. Drives the empty-state copy on stacks/services/nodes.
    portainerConfigured: null,
    portainerForm: {
      url: '', endpoint_id: 1, verify_tls: true, api_key: '',
    },
    portainerTestResult: null,
    beszelTestResult: null,
    pulseTestResult: null,
    webminTestResult: null,
    // Ping test widget state. `pingTestHostId` is the curated
    // host_id picked from the dropdown of opted-in hosts; `pingTestResult`
    // mirrors the shape of the others (pending / ok / detail).
    pingTestHostId: '',
    pingTestResult: null,
    // #344 / SNMP test widget state. UX unified with the Ping
    // test — the picker shows curated hosts that have an
    // `snmp_name` mapped, mirroring `pingTestHostId` instead of a
    // free-text host input. `testSnmpConnection` resolves the row's
    // SNMP target + overrides client-side and submits them to the
    // existing `/api/snmp/test` endpoint, so no backend change was
    // needed for the unification.
    snmpTestHostId: '',
    snmpTestResult: null,
    // The URL typed into the "Test one Webmin URL" scratch field.
    // Persisted to localStorage so operators don't have to retype it
    // every time they reload Host Stats to re-test after a config
    // change. Per-browser (each device keeps its own last-tested URL).
    webminTestUrl: (typeof localStorage !== 'undefined' && localStorage.getItem('webminTestUrl')) || '',
    // ---- SSH console state ----
    // sshStatus[host.id]       = { configured, enabled, resolved }
    // sshOpen[host.id]         = drawer card expanded?
    // sshResult[host.id]       = { ok, exit_code, stdout, stderr, duration_ms, dry_run, resolved, destructive }
    // sshCommand[host.id]      = textarea contents (in-memory only)
    // sshDryRun[host.id]       = "preview only" checkbox (defaults to false —
    //                             operator explicitly opts INTO dry-run; destructive
    //                             commands still gate on typed-hostname confirm)
    // sshBusy[host.id]         = bool — a run is in flight
    // sshTestBusy[host.id]     = bool — Test connection in flight
    // sshLastTested[host.id]   = epoch ms of last successful status probe
    // sshSettings / sshSettingsDirty / sshSettingsBusy — Admin → SSH form state.
    // sshTestOnHost            = { host_id, result, pending } for the Admin widget.
    sshStatus: {},
    sshOpen: {},
    sshResult: {},
    sshCommand: {},
    sshDryRun: {},
    sshBusy: {},
    sshTestBusy: {},
    sshLastTested: {},
    // ---- Interactive SSH terminal modal state (TODO #170) ----
    // terminalModalOpen     bool     visibility flag (drives the modal x-show).
    // terminalHost          object   the host row currently being driven.
    // terminalState         string   'connecting' | 'connected' | 'disconnected' | 'error'
    // terminalCloseReason   string   short message shown alongside the status pill.
    // terminalResolved      object   { user, host, port, ... } from the ws "ready" frame.
    // terminal              xterm.js Terminal instance — assigned after $nextTick.
    // terminalFit           FitAddon instance for window-resize handling.
    // terminalSocket        WebSocket — null when no session active.
    // terminalResizeBound   bound resize listener — kept so close() can detach it.
    // terminalResizeObs     ResizeObserver on the .terminal-host element — refits
    //                       whenever the container's box changes (modal animation
    //                       commit, parent reflow). The window-resize listener
    //                       alone misses the initial open because the modal has
    //                       no resize event of its own.
    terminalModalOpen: false,
    terminalHost: null,
    terminalState: 'connecting',
    terminalCloseReason: '',
    terminalResolved: null,
    terminal: null,
    terminalFit: null,
    terminalSocket: null,
    terminalResizeBound: null,
    terminalResizeObs: null,
    terminalFitTimers: null,
    sshSettings: {
      user: '', port: 22, private_key: '', passphrase: '',
      password: '', fqdn_suffix: '',
      known_hosts: '', destructive_patterns: '',
      private_key_set: false, passphrase_set: false, password_set: false,
      // custom_actions: [{id, title, command}] — rendered as preset
      // buttons in the host drawer SSH card. Seeded from the legacy
      // hardcoded 5 when the DB row is empty (see seedDefaultSshActions).
      custom_actions: [],
    },
    sshSettingsDirty: false,
    sshSettingsBusy: false,
    // Dirty trackers — same pattern as `sshSettingsDirty` /
    // `hostsConfigDirty` / `hostStatsDirty()`. Each tab's Save button
    // shows the amber unsaved-changes ring + dot when its flag is
    // true. Marked by @input / @change on every relevant input;
    // Per-tab baselines for the unified dirty-tracking pattern. Each
    // baseline is a JSON snapshot string captured after loadSettings()
    // (and after a successful save). The matching `<X>Dirty()` getter
    // compares the current snapshot against the baseline so reverting
    // a typed-and-deleted edit clears the indicator — same UX as the
    // existing Profile / Asset Inventory / Host stats tabs. Replaces
    // the older "set true on input, reset on save" boolean toggle
    // pattern that couldn't detect a revert.
    _appriseBaseline: '',
    _openMeteoBaseline: '',
    _portainerBaseline: '',
    // Snapshot of the form values that successfully passed a Test
    // probe. Set to the current `_portainerSnapshot()` value on
    // every successful `testPortainerConnection()`; cleared when the
    // form is loaded fresh from the server (so the operator must
    // re-test after edits). Used by `canSavePortainer()` to gate the
    // Save button — admins can't Save an enabled Portainer config
    // without first proving the URL / API key / endpoint round-trip
    // works, so a typo'd config can't ship and break /api/items.
    _portainerLastPassedTest: '',
    // Same Test-before-Save gating as Portainer above — when OIDC is
    // enabled, the operator must run a successful Test connection
    // before Save unlocks. Cleared on form load; mutated on form
    // edit (via dirty-tracker mismatch).
    _oidcLastPassedTest: '',
    _oidcBaseline: '',
    // Test-before-Save gate for Asset Inventory — same pattern as
    // Portainer / OIDC. When asset_inventory_enabled is ON, Save is
    // locked until a successful Test against the CURRENT form values.
    // Any edit after a passing Test mutates `_assetSnapshot()` away
    // from `_assetLastPassedTest`, re-locking Save.
    _assetLastPassedTest: '',
    _debugBaseline: '',
    _totpPolicyBaseline: '',
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
      'tuning_stats_sample_interval_seconds',
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
      // SPA loadHosts() concurrency cap on per-host
      // /api/hosts/one/<id> fan-out. Read on /api/me into
      // `me.client_config.hosts_parallel_fetch`.
      'tuning_hosts_parallel_fetch',
      // #537 / SSE heartbeat cadence + connection lifetime.
      'tuning_sse_heartbeat_seconds',
      'tuning_sse_max_lifetime_seconds',
      // Webmin probe outer budget. Rendered in
      // Settings → Host stats (Webmin section) instead of the generic
      // Process tunables form so operators editing Webmin creds have
      // the budget knob ready to hand. Same `tuningForm` /
      // `tuningEffective` / `saveTuning` Alpine state — just rendered
      // in two places only when configured.
      // node-exporter per-host probe timeout. Rendered in
      // Settings → Host stats → Node-exporter section instead of
      // the generic Process tunables form so operators editing NE
      // config have the timeout knob ready to hand. Same
      // `tuningForm` / `tuningEffective` / `saveTuning` Alpine
      // state — just rendered in the domain-specific home.
      // #541 / frontend SSE knobs delivered via /api/me.
      'tuning_sse_idle_threshold_seconds',
      'tuning_pollops_sse_keepalive_seconds',
      // login rate-limit policy.
      'tuning_rate_limit_max_failures',
      'tuning_rate_limit_window_seconds',
      'tuning_rate_limit_lockout_seconds',
      // outer host-provider cache.
      'tuning_host_provider_cache_ttl_seconds',
      // per-host Webmin caches MOVED to Settings → Host stats
      // → Webmin section per operator request. See
      // `relocatedTuningKeys` below — they keep the same Alpine
      // state via the union helper, just don't render in the
      // generic Process tunables form.
      // host_metrics_sampler per-tick NE probe concurrency.
      'tuning_host_metrics_probe_concurrency',
      // shared auth-failure cool-down.
      'tuning_auth_failure_cooldown_seconds',
      // tuning_notification_retention_days relocated → Admin → Notifications
      //   (lives next to the per-medium / per-event toggles where operators
      //    expect to find it; `relocatedTuningKeys` carries it through Save).
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
    // form-seed + dirty-track + POST — caught by operator after
    // shipping #550 (Log retention card was reading empty + Save was
    // a no-op).
    // Per-provider knob lists (UX-ENH-002 partial DRY). Single source
    // of truth for which tunables render in each provider's admin panel
    // — adding a new knob is one entry here instead of editing each
    // panel's inline x-for array. Each panel references via
    // `_perProviderTuneKeys.<provider>`. Mirrored into `relocatedTuningKeys`
    // below so the form-seed + dirty-track + POST flows pick them up.
    _perProviderTuneKeys: {
      node_exporter: [
        'tuning_node_exporter_probe_timeout_seconds',
        'tuning_node_exporter_failure_pause_rounds',
      ],
      beszel: [
        'tuning_beszel_failure_pause_rounds',
      ],
      pulse: [
        'tuning_pulse_failure_pause_rounds',
        'tuning_pulse_probe_timeout_seconds',
      ],
      webmin: [
        'tuning_webmin_probe_budget_seconds',
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
        'tuning_ping_failure_pause_rounds',
      ],
    },
    relocatedTuningKeys: [
      'tuning_log_retention_days', // → Admin → Logs 
      'tuning_webmin_probe_budget_seconds', // → Settings → Host stats → Webmin 
      'tuning_node_exporter_probe_timeout_seconds', // → Settings → Host stats → NE 
      'tuning_webmin_host_cache_ttl_seconds', // → Settings → Host stats → Webmin 
      'tuning_webmin_host_fail_cache_ttl_seconds',// → Settings → Host stats → Webmin 
      // Ping provider tunables (rendered in Host stats → Ping).
      'tuning_ping_interval_seconds',
      'tuning_ping_concurrency',
      'tuning_ping_probe_timeout_seconds',
      'tuning_ping_cooldown_seconds',
      // SNMP provider tunables (rendered in Host stats → SNMP).
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
      'tuning_pulse_failure_pause_rounds',
      'tuning_pulse_probe_timeout_seconds',
      'tuning_node_exporter_failure_pause_rounds',
      'tuning_ping_failure_pause_rounds',
      // stat-bar warn / crit thresholds (frontend-consumed).
      'tuning_stat_bar_warn_pct',
      'tuning_stat_bar_crit_pct',
      // In-app notifications retention — rendered inline in Admin →
      // Notifications next to the per-medium / per-event toggles
      // (was in the generic Process tunables form previously, but
      // operators editing notification config wanted the retention
      // dial in the same place).
      'tuning_notification_retention_days',
      'tuning_notification_page_size',
    ],
    tuningForm: {},
    tuningEffective: {},
    tuningLoaded: false,
    tuningSaving: false,
    _tuningBaseline: '',
    sshTestOnHost: { host_id: '', result: null, pending: false },
    // Settings / Admin sidebar layout. Arrays drive the nav — adding a
    // section is one entry here + one <section> in the markup.
    // Section `label` is kept as a fallback (in case the translation key
    // is missing); the sidebar button actually renders via t('settings.sections.<id>').
    // Personal settings — profile, ignore list, language, hotkeys.
    // Admin-only concerns (Portainer / OIDC / Notifications / Host
    // stats) moved under the Admin section since they're global and
    // only admins can change them.
    settingsSections: [
      { id: 'profile',       label: 'Profile',            icon: 'user' },
      { id: 'notifications', label: 'Notifications',      icon: 'bell' },
      { id: 'ignores',       label: 'Ignore list',        icon: 'trash' },
      { id: 'language',      label: 'Language',           icon: 'info' },
      { id: 'security',      label: 'Security',           icon: 'shield' },
      { id: 'shortcuts',     label: 'Keyboard shortcuts', icon: 'help-circle' },
    ],
    settingsSection: (function () {
      // Gracefully migrate users whose localStorage still points at the
      // removed "authentik" (Forward Auth) section — land them on the
      // new OIDC panel instead of staring at an empty page.
      const v = localStorage.getItem('settingsSection') || 'profile';
      return v === 'authentik' ? 'oidc' : v;
    })(),
    // Profile form state — mirrors the `me` snapshot but held separately
    // so the user can edit without losing unsaved changes across refetches.
    profileForm: { display_name: '', bio: '', email: '', notify_events: {} },
    // Baseline snapshot string of the profile form, captured by
    // syncProfileForm() and refreshed by saveProfile(). Drives
    // profileDirty() so reverting an edit clears the amber ring.
    _profileBaseline: '',
    profileBusy: false,
    avatarBusy: false,
    // TOTP / 2FA enrolment state. Mirrors the /api/me/totp shape
    // plus a few transient enrolment fields used during the QR -> verify
    // step. backup_codes is `[{code, used_at}]` plain after the GET; the
    // hide/unhide eye is purely client-side (`totpCodesRevealed`).
    totp: {
      loaded: false, allowed: true, enabled: false, required: false,
      auth_source: 'local', backup_codes: [], policy: {},
    },
    totpCodesRevealed: false,
    totpEnrol: { secret: '', uri: '', code: '' },
    totpEnrolStage: 'idle',  // idle | qr | reveal
    totpEnrolBusy: false,
    totpRevealCodes: [],     // one-time plaintext list right after enrol/regen
    totpDisableForm: { password: '' },
    totpDisableBusy: false,
    // WebAuthn / passkeys. Mirrors the /api/me/webauthn shape.
    // `list` is the array of enrolled credentials; `busy` covers either
    // the register or revoke ceremony so the operator can't double-tap.
    // `supported` is the SERVER capability flag (false on builds where
    // the webauthn library isn't installed); `passkeyBrowserSupported()`
    // covers the CLIENT side separately.
    passkeys: {
      loaded: false, supported: false, list: [], busy: false,
      // server-derived effective rp_id (request.url.hostname or
      // X-Forwarded-Host). Profile → Security compares each
      // credential's stored rp_id against this so cross-domain
      // passkeys (orphaned by a domain migration) get the red
      // "different domain" badge inline.
      current_rp_id: '',
    },
    adminSections: [
      { id: 'general',        label: 'General',         icon: 'sliders' },
      { id: 'users',          label: 'Users',           icon: 'users' },
      { id: 'authentication', label: 'Authentication',  icon: 'shield' },
      { id: 'sessions',       label: 'Sessions',        icon: 'monitor' },
      { id: 'tokens',         label: 'API tokens',      icon: 'key' },
      { id: 'notifications',  label: 'Notifications',   icon: 'bell' },
      { id: 'portainer',      label: 'Portainer',       icon: 'portainer' },
      { id: 'oidc',           label: 'Authentik OIDC',  icon: 'authentik' },
      { id: 'host_stats',     label: 'Host stats',      icon: 'activity' },
      { id: 'host_groups',    label: 'Host Groups',     icon: 'layers' },
      { id: 'hosts',          label: 'Hosts',           icon: 'server' },
      { id: 'ssh',            label: 'SSH',             icon: 'terminal' },
      { id: 'assets',         label: 'Asset inventory', icon: 'package' },
      { id: 'schedules',      label: 'Schedules',       icon: 'calendar' },
      { id: 'backups',        label: 'Backups',         icon: 'archive' },
      { id: 'logs',           label: 'Logs',            icon: 'file-text' },
      { id: 'config',         label: 'Config',          icon: 'settings' },
      { id: 'debug',          label: 'Debug',           icon: 'bug' },
    ],
    // App-logs viewer state. Polled when the Logs tab is visible.
    // `logLines` is append-only during a session; clear() wipes both
    // the UI list and the server-side ring.
    logLines: [],
    logSinceTs: 0,
    logAuto: true,
    logFilter: '',
    // Severity multi-select filter. Defaults: all four levels
    // visible. Persists to localStorage so reload preserves the view.
    // Severity values match the strings `logSeverity()` returns.
    logSeverityLevels: ['error', 'warn', 'ok', 'info'],
    logSeverityFilter: { error: true, warn: true, ok: true, info: true },
    logPollHandle: null,
    // Sub-tab state for the Logs admin view. 'live' shows the
    // existing in-memory ring viewer; 'files' shows the persistent
    // daily log files with a download button + live-tail of a
    // selected file.
    logsSubTab: 'live',
    logFiles: [],
    logFilesDir: '',
    logSelectedFile: '',
    logFileBody: '',
    logFileAutoTail: true,
    logFileTailLines: 500,
    _logFileTimer: null,
    backups: [],
    backupBusy: false,
    // Scheduler state. `schedules` is the list of rows from /api/schedules,
    // `scheduleQueue` is recent scheduler-driven ops from /api/schedules/queue.
    // `scheduleKinds` is populated from the same /api/schedules response so
    // the <select> for new schedules stays in sync with the backend registry.
    schedules: [],
    scheduleQueue: [],
    // Admin → Schedules Queue pagination — SERVER-side.
    // `scheduleQueue` holds ONLY the current page's rows (not the
    // whole queue); `scheduleQueueTotal` / `scheduleQueuePages` come
    // from the response. Page size persisted to localStorage.
    scheduleQueuePageSize: (() => {
      try {
        const v = parseInt(localStorage.getItem('scheduleQueuePageSize'), 10);
        return [10, 25, 50].includes(v) ? v : 25;
      } catch { return 25; }
    })(),
    scheduleQueuePage: 1,
    scheduleQueueTotal: 0,
    scheduleQueueSearch: '',
    _scheduleQueueSearchTimer: null,
    scheduleQueueTotalPages: 1,
    scheduleKinds: ['prune_node', 'prune_all_nodes', 'gather_refresh', 'backup', 'asset_inventory_refresh', 'prune_logs', 'prune_notifications', 'swarm_agent_health'],
    scheduleMinInterval: 60,
    scheduleBusy: false,
    // Create form. `params_text` is a raw JSON textarea — we parse on submit
    // so the operator can express any kind-specific shape without us having
    // to build a dynamic form per kind. Same approach for the edit dialog.
    newSchedule: {
      name: '', kind: 'gather_refresh', params_text: '{}',
      interval_seconds: 3600, enabled: true,
      // Cadence bundle — cadence_mode drives which of the other fields
      // the backend honours. Default to 'interval' for back-compat with
      // the simple "every N seconds" flow.
      cadence_mode: 'interval', run_at_hhmm: '',
      days_of_week: [], day_of_month: 1,
    },
    // Display order for the weekday picker. Mon=0..Sun=6 matches the
    // backend's Python tm_wday convention; labels are i18n keys.
    weekdayOrder: [0, 1, 2, 3, 4, 5, 6],
    // When non-null, the edit dialog is open and bound to this copy of the
    // row being edited. `params_text` on the copy is kept as a string so
    // Alpine's two-way binding stays simple.
    editingSchedule: null,
    // Admin view state
    adminTab: 'users',
    // Settings → Host stats provider tabs. Persists in
    // localStorage so the page returns to the operator's view on
    // refresh. Default falls through to the first ENABLED provider on
    // first load (handled by `setHostStatsTab` / `loadSettings`'s
    // post-hydrate setter); 'beszel' is the safe initial since it's
    // the most-common.
    hostStatsTab: (() => {
      try {
        const v = localStorage.getItem('hostStatsTab');
        // `'snmp'` was missing from the init-time whitelist when
        // the SNMP provider landed, so any operator who picked
        // the SNMP tab got reset to `'beszel'` on every refresh
        // (the setter wrote `'snmp'` correctly, but the IIFE filtered
        // it out on the next page load and fell through to the
        // default). Whitelist now matches `setHostStatsTab`'s own
        // valid set so the persisted tab actually persists.
        if (v && ['node_exporter', 'beszel', 'pulse', 'webmin', 'ping', 'snmp'].includes(v)) return v;
      } catch {}
      return 'beszel';
    })(),
    users: [],
    sessions: [], sessionsLoaded: false,
    usersLoaded: false, tokensLoaded: false,
    tokens: [],
    newUser: { username: '', role: 'readonly', auth_source: 'local', password: '', email: '' },
    newToken: { name: '', role: 'readonly' },
    // Raw new-token payload shown exactly once in a modal after creation.
    lastCreatedToken: null,
    newIgnore: { kind: 'image', pattern: '' },
    endpointId: 1,
    busy: {},
    themePref: localStorage.getItem('theme') || 'auto',
    // Current user, set from /api/me on init. Null until that call
    // completes; the SPA defers rendering everything that depends on it.
    me: null,
    // ARCH-004 — per-session dismiss flag for the SESSION_SECRET-auto
    // warning banner. Stored in sessionStorage (NOT localStorage) so
    // it resets every browser-session — that's the whole point: each
    // restart of the OmniGrid container kills sessions, and operators
    // should be re-warned each restart so they remember to set
    // SESSION_SECRET in the env. Once they fix it, the backend stops
    // setting `me.session_secret_auto` so the banner disappears.
    sessionSecretWarningDismissed: (typeof sessionStorage !== 'undefined' &&
                                    sessionStorage.getItem('sessionSecretWarningDismissed') === '1') || false,
    // Same dismissal pattern as the SESSION_SECRET banner — per-session,
    // re-appears after a restart so the operator sees the reminder until
    // they actually clear the env vars (#370 / UX-004).
    bootstrapEnvWarningDismissed: (typeof sessionStorage !== 'undefined' &&
                                    sessionStorage.getItem('bootstrapEnvWarningDismissed') === '1') || false,

    async init() {
      // Expose the live Alpine component instance globally for the
      // browser-console diagnostic helpers (e.g.
      // `omnigrid.statsDebug()`). The factory function `app()` would
      // return a FRESH default-state object on each call — what we
      // need here is the same `this` that Alpine has been mutating
      // since boot. Single-replica + single-component-per-page so
      // there's no ambiguity about which instance to expose.
      try { window.omnigrid = this; } catch (_) {}
      // Restore persisted UI prefs that need to land before the first
      // render of their dependent views (#422 logs severity filter).
      this._restoreLogSeverity();
      // i18n is already loaded (Alpine is gated on __i18nReady), but pull
      // the authoritative language list + current code/dir into the
      // reactive Alpine state so pickers and v-bindings track it.
      try { await window.__i18nReady; } catch (_) {}
      if (window.I18N) {
        this.lang = window.I18N.code;
        this.dir  = window.I18N.dir;
        this.availableLanguages = window.I18N.languages || this.availableLanguages;
      }
      // Resolve identity FIRST. Unauthenticated users get redirected to
      // /login; everyone else falls through to the normal data loads.
      // (The global fetch wrapper also handles mid-session 401s, but doing
      // an explicit check up front avoids flashing an empty UI to someone
      // who isn't logged in at all.)
      try {
        const r = await fetch('/api/me');
        if (r.ok) {
          const m = await r.json();
          if (!m.authenticated) {
            const path = location.pathname + location.search;
            location.href = '/login?next=' + encodeURIComponent(path);
            return;
          }
          this.me = m;
          this.syncProfileForm();
          // Adopt operator-tunable notifications page size from
          // /api/me's client_config. Falls back to the in-data()
          // default (25) when missing. Bounds-clamped by the backend
          // resolver so a corrupt setting can't flood the popup.
          const npsRaw = m && m.client_config && m.client_config.notifications_page_size;
          const nps = parseInt(npsRaw, 10);
          if (Number.isFinite(nps) && nps > 0) {
            this.notificationsLimit = nps;
          }
          // apply per-user UI prefs from the server so the
          // weather/clock toggles (etc.) sync across devices for the
          // same login. Runs AFTER `me` lands so applyServerUiPrefs
          // can read this.me.ui_prefs.
          if (typeof this.applyServerUiPrefs === 'function') {
            this.applyServerUiPrefs();
          }
          // Capture the header-prefs dirty baseline AFTER hydration
          //. The baseline initialiser is `''` (the empty
          // string sentinel set on the data() block) and the
          // snapshot helper returns a populated JSON string, so
          // without this re-baseline the form always reads dirty
          // until the first Save click.
          if (typeof this._headerPrefsSnapshot === 'function') {
            this._headerPrefsBaseline = this._headerPrefsSnapshot();
          }
          // fetch TOTP status alongside /api/me so the Profile
          // section can render its 2FA card without a click-induced
          // round-trip. Authentik users (no local password) skip the
          // call; the server short-circuits its response either way.
          if (typeof this.loadTotpStatus === 'function') {
            this.loadTotpStatus();
          }
          // same pattern for the passkeys list.
          if (typeof this.loadPasskeys === 'function') {
            this.loadPasskeys();
          }
        }
      } catch (_) { /* network hiccup — next fetch will trip the wrapper */ }

      if (window.matchMedia) {
        const mq = window.matchMedia('(prefers-color-scheme: light)');
        const onSys = () => { if (this.themePref === 'auto') this.applyTheme(); };
        mq.addEventListener ? mq.addEventListener('change', onSys) : mq.addListener(onSys);
      }
      this.applyTheme();
      // URL routing — reflect current view + section in the path so a
      // refresh keeps the operator where they were (/admin/host_stats,
      // /settings/profile, etc). Run the one-time parse BEFORE wiring
      // the watchers so the initial assign doesn't spam pushState.
      this._applyRouteFromPath();
      // Persist the drawer-chart range so a refresh doesn't snap the
      // picker back to 1h. Same pattern as the `view` watcher below.
      this.$watch('hostHistoryRange', v => {
        try { localStorage.setItem('hostHistoryRange', String(v)); } catch (_) {}
      });
      this.$watch('view', v => {
        localStorage.setItem('view', v);
        this._pushRoute();
        // Load admin data lazily — only when the user actually navigates there.
        if (v === 'admin') this.openAdminTab(this.adminTab);
        // Lazy-load hosts on first entry and (re)start its refresh timer.
        // Leaving the tab clears the timer so we're not hammering the hub
        // when the view isn't visible.
        if (v === 'hosts') {
          this.loadHosts();
          if (this._hostsTimer) clearInterval(this._hostsTimer);
          // Honour the "stats off" master switch — when the
          // operator picked Off, the host poll's 15s rebuild cycle
          // (with its brief loading-spinner flash on every row)
          // is part of the "live updates" they wanted to silence.
          if (this.statsInterval > 0) {
            // when SSE is healthy, host:row_updated /
            // host:failure_state_changed events drive refreshHostRow
            // directly. The interval still fires (so the freshness
            // tooltip ticks + we resume polling within one cadence
            // window if SSE drops), but the work is conditional.
            this._hostsTimer = setInterval(() => {
              if (this._sseConnected) return;
              this.loadHosts();
            }, this.statsInterval * 1000);
          }
        } else if (this._hostsTimer) {
          clearInterval(this._hostsTimer);
          this._hostsTimer = null;
        }
      });

      // Notifications popup. Lazy-load on open; the 30s polling
      // fallback only fires when SSE is disconnected (push-driven
      // updates land via _handleNotificationCreated). Driven by
      // `showNotificationsPopup` since the popup is no longer a
      // top-level view (operators wanted a quick check + return to
      // current work without losing their place).
      this.$watch('showNotificationsPopup', open => {
        if (open) {
          if (this._notificationsPollHandle) clearInterval(this._notificationsPollHandle);
          this._notificationsPollHandle = setInterval(() => {
            if (this._sseConnected) return;
            if (this.showNotificationsPopup) this.loadNotifications();
          }, 30000);
        } else if (this._notificationsPollHandle) {
          clearInterval(this._notificationsPollHandle);
          this._notificationsPollHandle = null;
        }
      });
      this.$watch('settingsSection', v => {
        localStorage.setItem('settingsSection', v);
        this._pushRoute();
      });
      this.$watch('adminTab', () => this._pushRoute());
      // Back/forward buttons — re-read the path into app state.
      window.addEventListener('popstate', () => this._applyRouteFromPath());
      this.$watch('expanded', v => localStorage.setItem('expanded', JSON.stringify(v)));
      this.$watch('hostsSort', v => { try { localStorage.setItem('hostsSort', v); } catch {} });
      this.$watch('hostsHideUnconfigured', v => { try { localStorage.setItem('hostsHideUnconfigured', v ? '1' : '0'); } catch {} });
      // Webmin scratch-test URL persists so operators don't retype
      // the same host every time they reload Host Stats.
      this.$watch('webminTestUrl', v => { try { localStorage.setItem('webminTestUrl', v || ''); } catch {} });
      this.$watch('hostsConfigExpanded', v => {
        try { localStorage.setItem('hostsConfigExpanded', JSON.stringify(v || {})); } catch {}
      }, { deep: true });
      // Filter typing collapses the result set — jumping back to page 1
      // is the only sensible default (otherwise the operator types a
      // filter and sees an empty page because they were on page 4 of
      // the unfiltered list).
      this.$watch('hostsConfigFilter', () => { this.hostsConfigPage = 1; });
      // Persist page index so reload / tab navigation lands on the same
      // page. Pairs with the localStorage initialiser above.
      this.$watch('hostsConfigPage', v => {
        try { localStorage.setItem('hostsConfigPage', String(v)); } catch {}
      });
      // `pagedHostsConfig` clamps lazily inside a getter (can't
      // mutate state there without breaking Alpine reactivity), which
      // means `hostsConfigPage` can sit at 5 while the rendered page
      // is actually 3 of 3 (last filtered row trimmed). Watch the
      // pagination inputs and normalise `hostsConfigPage` on the next
      // tick so the visible state matches what the operator sees.
      this.$watch('hostsConfigSortedOrder', () => this._clampHostsConfigPage());
      this.$watch('hostsConfigFilter',      () => this._clampHostsConfigPage());
      this.$watch('hostsConfigPerPage',     () => this._clampHostsConfigPage());
      // Same persistence for the host-groups editor.
      this.$watch('hostGroupsPage', v => {
        try { localStorage.setItem('hostGroupsPage', String(v)); } catch {}
      });
      this.$watch('hostsExpanded', v => {
        try { localStorage.setItem('hostsExpanded', JSON.stringify(v || [])); } catch {}
      });
      // Re-surface the translated labels in Settings/Admin sidebars when
      // the user swaps language. Alpine re-renders bindings automatically
      // because `lang` is part of the component state; this watcher just
      // lets us react to the change (e.g. refresh the document title).
      this.$watch('lang', () => {
        document.title = this.t('app.name');
      });
      window.addEventListener('keydown', (e) => this.handleHotkey(e));
      // Capture-phase pre-empt for Cmd-K / Ctrl-K / Ctrl-/ — runs
      // BEFORE browser UI shortcuts (Chrome's omnibox-search Ctrl+K)
      // AND before the bubble-phase handleHotkey above. Without this,
      // some browsers consume the keystroke or focus the address bar
      // before the page-level handler ever sees the event. Capture
      // phase is the only reliable way to claim a hotkey that
      // collides with a browser default. Stops at handleHotkey if
      // matched so the bubble-phase handler doesn't try to fire it
      // a second time. All other keys fall through untouched.
      window.addEventListener('keydown', (e) => {
        const cmdMod = (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey;
        const isPaletteCombo = cmdMod && (
          e.key === 'k' || e.key === 'K' || e.code === 'KeyK' ||
          e.key === '/' || e.code === 'Slash'
        );
        if (!isPaletteCombo) return;
        // Single-fire sentinel — operator-reported diagnostic
        // showed `was=true` on first Ctrl+K press, meaning the
        // toggle had run TWICE per press (once flipping false→true
        // then immediately true→false, leaving the modal hidden).
        // Likely cause: the bubble-phase `handleHotkey` also
        // matched the same combo and re-toggled. Stamp the event
        // so any subsequent listener bails out cleanly.
        if (e._cmdpal_handled) return;
        e._cmdpal_handled = true;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        if (this.commandPaletteOpen) this.closeCommandPalette();
        else this.openCommandPalette();
      }, { capture: true });
      // Debug escape hatch: expose openCommandPalette + state on
      // window so the operator can verify the modal renders by
      // typing `__omnigridOpenPalette()` in the DevTools console.
      // If that opens the panel, the issue is hotkey detection
      // (browser pre-empting); if it doesn't, the issue is modal
      // rendering (Alpine x-show / CSS / scoping). Splits the
      // diagnostic so the operator can tell which layer is broken.
      try {
        window.__omnigridOpenPalette = () => { this.openCommandPalette(); };
      } catch (_) { /* defensive */ }
      // Click-outside listener for the chart `?` tap-driven tooltip
      //. The trigger spans + tooltip body each call
      // `@click.stop` so they're EXCLUDED from this handler — taps
      // anywhere else dismiss whatever's open.
      document.addEventListener('click', () => {
        if (this.metricTooltipOpen) this.metricTooltipOpen = null;
      });
      // Warn when closing / reloading the tab with unsaved Hosts
      // edits. Browsers ignore a custom string (Chrome shows their
      // own generic dialog), but the presence of returnValue still
      // triggers the prompt.
      window.addEventListener('beforeunload', (e) => {
        if (this.hostsConfigDirty) {
          e.preventDefault();
          e.returnValue = '';
        }
      });
      await this.loadSettings();
      await this.loadIgnores();
      await this.refresh();
      await this.loadHistory();
      this.loadVersion();
      // Prime the avatar's unread badge before the operator opens
      // the Notifications view. Cheap probe (1-row LIMIT) — see
      // loadNotificationsUnread.
      this.loadNotificationsUnread();
      // Asset inventory — load the cached asset list once on boot so
      // the drawer can surface matched rows (vendor / model / serial /
      // location) without an extra round-trip per row-expand. Silent
      // failure is fine (asset inventory is optional).
      this.loadAssetCache();
      this.startVersionWatcher();
      this.startHeaderClock();
      this.startHeaderWeather();
      // Persisted unified refresh cadence. Mirrors into legacy
      // `autoRefresh` + `statsInterval` so existing pollers see the
      // operator's choice through the keys they already read. When
      // `refreshInterval` is 0 (Off) every poller stays asleep until
      // SSE handles updates via push events.
      // setRefreshInterval already manages SSE based on the chosen
      // mode — Live opens the stream, Off / interval close it. Don't
      // double-init here. `?? -1` (not `|| 0`) preserves the explicit
      // 0 ("Off") choice across reloads instead of mapping it to -1.
      this.setRefreshInterval(this.refreshInterval ?? -1);
      this.pollOps();
      this.pollStats();
      this.pollSparks();
      // If the SPA restored to the Hosts view (saved in localStorage or
      // arrived via /hosts deep-link), trigger the same load+poll the
      // view-watcher does on manual switch.
      if (this.view === 'hosts') {
        this.loadHosts();
        // Same off-honoring rule as the view-watcher above.
        if (this.statsInterval > 0) {
          this._hostsTimer = setInterval(() => {
            this._pollWrap(this.loadHosts());
          }, this.statsInterval * 1000);
        }
      }
      // `updateCacheLabel` was retired in #486 (the "fresh / cached
      // Xs ago" topbar text was removed as confusing alongside the
      // unified picker). The 1s timer that drove it is gone too — no
      // sense burning a tick per second on a no-op.
      // Tick `hostHistoryNow` every second so the host-drawer charts'
      // "Updated Xs/Xm/Xh ago" label counts in real time.
      // Operator needs the seconds digit to tick visibly — a 30s
      // cadence made the label feel frozen. One int assignment per
      // second is a negligible cost; Alpine only re-evaluates the
      // freshness helper on bound elements (the small span near the
      // time-range picker), so most renders are skipped.
      this.hostHistoryNow = Date.now();
      setInterval(() => { this.hostHistoryNow = Date.now(); }, 1000);
    },

    async logout() {
      try {
        await fetch('/api/local-auth/logout', { method: 'POST' });
      } catch (_) { /* ignore — clearing the cookie is the important bit */ }
      location.href = '/login';
    },
    // ARCH-004 — dismiss the SESSION_SECRET-auto banner for this
    // browser session. Persists in sessionStorage so a hard-refresh
    // doesn't unhide it again, but a fresh browser-session (close +
    // reopen, or container restart on the operator's side) brings it
    // back. That's intentional: every restart kills user sessions,
    // operators need the recurring nudge to set SESSION_SECRET.
    dismissSessionSecretWarning() {
      this.sessionSecretWarningDismissed = true;
      try { sessionStorage.setItem('sessionSecretWarningDismissed', '1'); } catch (_) {}
    },
    dismissBootstrapEnvWarning() {
      this.bootstrapEnvWarningDismissed = true;
      try { sessionStorage.setItem('bootstrapEnvWarningDismissed', '1'); } catch (_) {}
    },

    // --- Profile: password strength meter -------------------------------
    // Pure UX affordance; the backend still enforces the 8-char minimum.
    // Scoring weights length more than character-class diversity — a
    // 12-char lowercase passphrase is stronger than `Aa1!` and the
    // meter should reflect that. Under 8 chars is always clamped to Weak
    // regardless of other criteria because the backend will reject it.
    get passwordStrength() {
      const pw = (this.passwordForm && this.passwordForm.next) || '';
      const criteria = {
        length8:  pw.length >= 8,
        length12: pw.length >= 12,
        lower:    /[a-z]/.test(pw),
        upper:    /[A-Z]/.test(pw),
        digit:    /\d/.test(pw),
        symbol:   /[^A-Za-z0-9]/.test(pw),
      };
      if (!pw) return { score: 0, label: '', color: 'faint', criteria };
      let score = 0;
      if (criteria.length8)  score += 1;
      if (criteria.length12) score += 1;
      if (criteria.lower && criteria.upper) score += 1;
      if (criteria.digit)  score += 1;
      if (criteria.symbol) score += 1;
      if (score > 4) score = 4;
      if (!criteria.length8) score = Math.min(score, 1);
      const labelKeys = [
        'password.strength.too_short',
        'password.strength.weak',
        'password.strength.fair',
        'password.strength.good',
        'password.strength.strong',
      ];
      const colors = ['danger',    'danger', 'warning', 'primary', 'success'];
      return {
        score,
        label: this.t(labelKeys[score]),
        color: colors[score],
        criteria,
      };
    },

    // --- Profile: password change ---------------------------------------
    async changePassword() {
      if (this.passwordBusy) return;
      const f = this.passwordForm;
      if (f.next !== f.confirm) {
        this.showToast(this.t('toasts.password_mismatch'), 'error');
        return;
      }
      if ((f.next || '').length < 8) {
        this.showToast(this.t('toasts.password_too_short'), 'error');
        return;
      }
      this.passwordBusy = true;
      try {
        const body = new URLSearchParams({
          current_password: f.current,
          new_password: f.next,
          confirm_password: f.confirm,
        });
        const r = await fetch('/api/local-auth/change-password', { method: 'POST', body });
        if (r.ok) {
          this.showToast(this.t('toasts.password_changed'), 'success');
          this.passwordForm = { current: '', next: '', confirm: '' };
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.change_failed', { status: r.status }), 'error');
        }
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      } finally {
        this.passwordBusy = false;
      }
    },

    // --- Profile: display-name / bio / email edit ------------------------
    syncProfileForm() {
      // Called after /api/me loads to populate the editable form. Kept
      // separate from `me` so unsaved edits aren't clobbered on refresh.
      // Hydrates per-user notification prefs from the resolved
      // `notify_events` map; rebuilds the baseline snapshot so the
      // dirty-getter compares against the freshly-loaded values.
      //
      // Per-medium granularity: every event normalises to a per-medium
      // dict `{medium: bool}` for editing simplicity. The wire shape
      // is mixed (legacy bare-bool OR per-medium dict) — this helper
      // expands legacy bools to a per-medium dict where every globally-
      // enabled medium inherits the bool, so the SPA's checkbox grid
      // always reads from a uniform shape. On save, events whose every
      // medium reads identically collapse back to a bare bool to keep
      // the stored payload small.
      const events = {};
      const src = (this.me && this.me.notify_events) || {};
      const mediums = this.notifyMediumNames();
      for (const k of (this.notifyEventKeys || [])) {
        const bare = k.replace(/^notify_event_/, '');
        const v = src[bare];
        const slot = {};
        if (v && typeof v === 'object') {
          // Per-medium dict: copy known mediums, fall back to true for
          // anything missing (matches the dispatcher's default-on-
          // missing-medium contract).
          for (const m of mediums) {
            slot[m] = (v[m] !== false);
          }
        } else {
          // Legacy bare-bool (or absent): apply uniformly across every
          // medium. `!!v` collapses undefined → false, false → false,
          // true → true.
          const b = !!v;
          for (const m of mediums) slot[m] = b;
        }
        events[bare] = slot;
      }
      this.profileForm = {
        display_name: (this.me && this.me.display_name) || '',
        bio:          (this.me && this.me.bio)          || '',
        email:        (this.me && this.me.email)        || '',
        notify_events: events,
      };
      this._profileBaseline = this._profileSnapshot();
    },
    // Snapshot helper — JSON-serialises the editable shape of the
    // profile form. Used by both saveProfile() and profileDirty() so
    // the comparison stays in lock-step.
    _profileSnapshot() {
      const f = this.profileForm || {};
      return JSON.stringify({
        display_name: f.display_name || '',
        bio:          f.bio || '',
        email:        f.email || '',
        notify_events: f.notify_events || {},
      });
    },
    // Dirty-tracker for the Profile form. Compares the live form
    // against the baseline captured on load + each save. Any string
    // divergence in display_name / bio / email / notify_events flips
    // the Save button to its "unsaved changes" treatment.
    profileDirty() {
      if (!this.me) return false;
      return this._profileBaseline !== this._profileSnapshot();
    },
    // Per-user notification toggle disable gate. Returns true
    // when the admin has globally disabled this event — UI greys out
    // the user-side checkbox and shows a "disabled by admin" tooltip.
    // The data model only narrows DOWN from the admin layer, so the
    // backend also rejects an opt-IN attempt for a globally-disabled
    // event with a 400.
    userNotifyEventDisabledByAdmin(eventKey) {
      if (!this.me || !this.me.notify_events_admin) return false;
      const bare = (eventKey || '').replace(/^notify_event_/, '');
      return this.me.notify_events_admin[bare] === false;
    },

    // UX-005: Asset Inventory dirty tracker — same shape as
    // `profileDirty()` and the host-stats / OIDC / Portainer /
    // Apprise / SSH dirty flags. Compares the editable `assetForm`
    // against the server-supplied `assetStatus` snapshot. Secret
    // fields (client_secret / lifetime_token) follow the standard
    // "_set" pattern: blank = keep, any non-empty value = dirty.
    assetDirty() {
      const f = this.assetForm || {};
      const s = this.assetStatus || {};
      const norm = v => (v == null ? '' : String(v));
      // Master switch — toggle change is dirty even when no
      // form field changed. Compared against the server-supplied
      // baseline captured into `assetStatus.enabled` by loadSettings.
      const enabledBaseline = (s.enabled !== false);
      if ((this.settings && (this.settings.asset_inventory_enabled !== false)) !== enabledBaseline) return true;
      // Status's auth_mode comes through as 'lifetime_token' or anything-else;
      // form normalises to 'oauth2' as the default fallback.
      const baseAuth = (s.auth_mode === 'lifetime_token') ? 'lifetime_token' : 'oauth2';
      if ((f.auth_mode || 'oauth2') !== baseAuth) return true;
      const fields = [
        'base_url', 'token_url', 'client_id', 'scope',
        'service', 'action', 'edit_url_template',
      ];
      for (const k of fields) {
        if (norm(f[k]) !== norm(s[k])) return true;
      }
      // min_value / max_value — form holds strings, status numbers.
      const sMin = (s.min_value != null) ? String(s.min_value) : '';
      const sMax = (s.max_value != null) ? String(s.max_value) : '';
      if (norm(f.min_value) !== sMin) return true;
      if (norm(f.max_value) !== sMax) return true;
      // Write-only secrets: any non-empty value in the form is a pending
      // change. Blank = keep current; the operator hasn't typed anything.
      if ((f.client_secret  || '').length > 0) return true;
      if ((f.lifetime_token || '').length > 0) return true;
      return false;
    },

    async saveProfile() {
      if (this.profileBusy) return;
      this.profileBusy = true;
      try {
        // Profile (display_name / bio / email) — same payload as before;
        // /api/me/profile ignores extra fields so we strip the notify
        // map for clarity rather than relying on the route to drop it.
        const profilePayload = {
          display_name: this.profileForm.display_name,
          bio:          this.profileForm.bio,
          email:        this.profileForm.email,
        };
        const r = await fetch('/api/me/profile', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(profilePayload),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
          return;
        }
        // Per-user notification opt-in. Sent as a separate PATCH so the
        // admin-gate refusal (400 with detail "Event 'X' is disabled by
        // admin") surfaces a useful message without rolling back the
        // profile edit. Backend payload keys are the bare event names
        // (matching the storage shape inside ui_prefs).
        //
        // Per-medium granularity: profileForm carries every event as
        // `{medium: bool}`. On save we collapse uniform-bool dicts
        // (every medium reads the same) back to a bare bool so storage
        // stays compact AND legacy parsers (operator scripts grepping
        // ui_prefs.notify_events.<event>) keep working when the user
        // hasn't routed events per-medium. Mixed dicts pass through
        // untouched.
        const formEvents = this.profileForm.notify_events || {};
        const mediums = this.notifyMediumNames();
        const events = {};
        for (const bare in formEvents) {
          const slot = formEvents[bare];
          if (slot && typeof slot === 'object') {
            const vals = mediums.map(m => slot[m] !== false);
            const allSame = vals.every(v => v === vals[0]);
            events[bare] = allSame ? !!vals[0] : { ...slot };
          } else {
            events[bare] = !!slot;
          }
        }
        const r2 = await fetch('/api/me/notify-prefs', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(events),
        });
        if (!r2.ok) {
          const j = await r2.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
          return;
        }
        this.showToast(this.t('toasts.profile_saved'));
        // Refresh `me` so the avatar badge / dropdown header / resolved
        // notify_events all reflect the persisted state. syncProfileForm
        // re-baselines so the amber ring + Unsaved indicator clear.
        const rm = await fetch('/api/me');
        if (rm.ok) { this.me = await rm.json(); this.syncProfileForm(); }
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      } finally {
        this.profileBusy = false;
      }
    },

    // --- Profile: avatar upload ------------------------------------------
    async uploadAvatar(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      // Client-side sanity — server re-validates.
      if (!/^image\//.test(file.type)) {
        this.showToast(this.t('toasts.pick_image'), 'error'); return;
      }
      if (file.size > 1_000_000) {
        this.showToast(this.t('toasts.image_too_large'), 'error'); return;
      }
      this.avatarBusy = true;
      try {
        const fd = new FormData();
        fd.append('file', file);
        const r = await fetch('/api/me/avatar', { method: 'POST', body: fd });
        if (r.ok) {
          this.showToast(this.t('toasts.avatar_updated'));
          const rm = await fetch('/api/me');
          if (rm.ok) this.me = await rm.json();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.upload_failed'), 'error');
        }
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      } finally {
        this.avatarBusy = false;
        ev.target.value = '';
      }
    },

    async clearAvatar() {
      if (!confirm(this.t('settings.profile.avatar_prompt_remove'))) return;
      try {
        const r = await fetch('/api/me/avatar', { method: 'DELETE' });
        if (r.ok) {
          this.showToast(this.t('toasts.avatar_removed'));
          const rm = await fetch('/api/me');
          if (rm.ok) this.me = await rm.json();
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    // -----------------------------------------------------------------
    // Admin view — nav + data loaders + actions.
    // -----------------------------------------------------------------
    navItems() {
      // Settings + Admin live in the avatar dropdown, not the top nav —
      // the top nav stays focused on the fleet views.
      return [
        ['stacks',   this.t('nav.stacks')],
        ['services', this.t('nav.services')],
        ['nodes',    this.t('nav.nodes')],
        ['hosts',    this.t('nav.hosts')],
        ['history',  this.t('nav.history')],
      ];
    },
    // Inner-SVG markup for each top-nav icon. Returned as an HTML
    // string rendered via `x-html` on a shared <svg> wrapper so the
    // stroke / viewBox / size stay consistent. Lucide-derived shapes —
    // layered-squares for Stacks, cube for Services, server-rack for
    // Nodes, monitor for Hosts, clock-with-arrow for History.
    navIcon(key) {
      const icons = {
        stacks:   '<path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
        services: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
        nodes:    '<rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
        hosts:    '<rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
        history:  '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
      };
      return icons[key] || '';
    },

    // -----------------------------------------------------------------
    // Nodes view — groups the fleet by which Swarm node each task /
    // container lives on. Services appear under EVERY node their tasks
    // run on (so a 3-replica global service shows under all 3 nodes);
    // plain containers / orphans appear under their single node.
    // -----------------------------------------------------------------
    nodeGroups() {
      // Seed with every known node so a node with zero items still
      // renders (helps spot "this worker is empty" at a glance).
      const byNode = new Map();
      for (const id in (this.nodes || {})) {
        const host = this.nodes[id];
        if (host) byNode.set(host, { name: host, items: [], stacks: {} });
      }

      // Pick which items we're filtering over. Reuse the same filter
      // pipeline as the stacks/services views (search + status + health).
      const items = this.filteredItems;

      for (const it of items) {
        // Services carry `placements: [{node, state}, ...]`; standalones
        // carry a single `node` field. Derive the set of nodes either way.
        const nodes = new Set();
        if (Array.isArray(it.placements) && it.placements.length) {
          for (const p of it.placements) {
            if (p && p.node && p.node !== '?' && p.node !== 'local') nodes.add(p.node);
          }
        }
        if (nodes.size === 0 && it.node && it.node !== '?' && it.node !== 'local') {
          nodes.add(it.node);
        }
        // No identifiable node → park under a synthetic "Unpinned" group.
        if (nodes.size === 0) nodes.add('__unpinned__');

        for (const n of nodes) {
          if (!byNode.has(n)) {
            byNode.set(n, {
              name: n === '__unpinned__' ? 'Unpinned / local' : n,
              items: [], stacks: {},
              is_unpinned: n === '__unpinned__',
            });
          }
          const g = byNode.get(n);
          g.items.push(it);
          const stackKey = it.stack || '__standalone__';
          if (!g.stacks[stackKey]) {
            g.stacks[stackKey] = {
              name: it.stack || 'Standalone',
              items: [],
              is_standalone: !it.stack,
            };
          }
          g.stacks[stackKey].items.push(it);
        }
      }

      // Finalise each group: counts + sorted stack list + sorted items.
      const out = [];
      for (const [key, g] of byNode) {
        const its = g.items;
        const stackList = Object.values(g.stacks)
          .map(s => ({
            ...s,
            items: s.items.slice().sort((a, b) => (a.name || '').localeCompare(b.name || '')),
          }))
          .sort((a, b) => (a.name || '').localeCompare(b.name || ''));
        out.push({
          key,
          name: g.name,
          is_unpinned: !!g.is_unpinned,
          total:    its.length,
          services: its.filter(i => i.type === 'service').length,
          containers: its.filter(i => i.type === 'container' || i.type === 'orphan').length,
          stacks:   stackList.filter(s => !s.is_standalone).length,
          updates:  its.filter(i => i.status === 'update').length,
          // Up-to-date count so the Nodes header can show a green
          // pill matching the Stacks view's `uptodate / total ok`
          // convention. Without this, Nodes shows only the bad
          // counts (updates / offline / degraded) and operators saw
          // the absence of a green affordance as inconsistent UX.
          uptodate: its.filter(i => i.status === 'up-to-date').length,
          offline:  its.filter(i => i.health === 'offline').length,
          degraded: its.filter(i => i.health === 'degraded').length,
          errors:   its.filter(i => i.status === 'error').length,
          stackList,
        });
      }
      // Sort: real nodes alphabetically, "Unpinned" last.
      return out.sort((a, b) => {
        if (a.is_unpinned !== b.is_unpinned) return a.is_unpinned ? 1 : -1;
        return (a.name || '').localeCompare(b.name || '');
      });
    },

    toggleNode(name) {
      const i = this.expanded.indexOf('node:' + name);
      if (i >= 0) this.expanded.splice(i, 1);
      else this.expanded.push('node:' + name);
    },
    isNodeExpanded(name) {
      return this.expanded.includes('node:' + name);
    },
    expandAllNodes() {
      const keys = this.nodeGroups().map(n => 'node:' + n.name);
      // Preserve non-node entries already in expanded (e.g. stacks).
      const others = this.expanded.filter(k => !k.startsWith('node:'));
      this.expanded = [...others, ...keys];
    },
    collapseAllNodes() {
      this.expanded = this.expanded.filter(k => !k.startsWith('node:'));
    },
    expandAllHosts() {
      // Skip dead / no-data rows — same rule as manual toggle.
      const alive = (this.hosts || []).filter(h => this.isHostExpandable(h));
      this.hostsExpanded = alive.map(h => h.host);
      // Warm history for every expanded host on bulk-open.
      for (const h of alive) {
        const key = this.hostHistoryKey(h);
        if (key && (h.beszel_id || h.ne_url || h.pulse_name || h.webmin_name) && !this.hostHistory[key]) {
          this.loadHostHistory(h.beszel_id || '', h.id);
        }
      }
    },
    collapseAllHosts() { this.hostsExpanded = []; },

    // "Is there an in-flight prune_node op targeting this host?". Drives
    // the button's spinner + disabled state so rapid double-clicks don't
    // queue a second prune — activeOps is the same list the ops panel reads.
    isPruneBusy(host) {
      return (this.activeOps || []).some(o =>
        o.op_type === 'prune_node' && o.target_id === host
      );
    },

    async pruneNode(host) {
      // Confirm first — `docker system prune --volumes` is destructive:
      // stopped containers go away, dangling images, unused networks, AND
      // unused volumes (which can carry data users forgot was orphaned).
      const res = await Swal.fire({
        title: this.t('admin.nodes.prune_prompt_title', { host }),
        html: this.t('admin.nodes.prune_prompt_html', { host }),
        icon: 'warning',
        showCancelButton: true,
        confirmButtonText: this.t('actions.prune_now'),
        cancelButtonText: this.t('actions.cancel'),
        confirmButtonColor: this._cssVar('--danger'),
      });
      if (!res.isConfirmed) return;
      try {
        const r = await fetch('/api/prune/node/' + encodeURIComponent(host), { method: 'POST' });
        if (r.ok) {
          // Auto-expand the floating ops panel (bottom-right) so a new
          // user actually SEES where the live progress lives. Without
          // this, the toast refers to a panel that only appears briefly
          // and stays collapsed if they've dismissed it before.
          this.opsExpanded = true;
          this.showToast(this.t('toasts_extra.prune_started', { host }));
          // Kick an immediate ops poll so the button flips to "Pruning…".
          this.pollOnce && this.pollOnce();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts_extra.save_failed_generic'), 'error');
        }
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      }
    },

    // Single predicate for "can this user do writes?". Every write button
    // is gated on this so readonly users see a clean read-only UI instead
    // of a button that just returns 403.
    isAdmin() {
      return !!(this.me && this.me.role === 'admin');
    },
    isReadonly() {
      return !!(this.me && this.me.role === 'readonly');
    },


    // Avatar helpers — deterministic colour per username so "alice" always
    // gets the same hue across refreshes. Uses HSL in CSS so the same
    // hue value produces a pleasant colour in both light and dark themes
    // (the token `--avatar-hue` feeds into a hsl() in style.css).
    initial() {
      if (!this.me || !this.me.username) return '?';
      const c = this.me.username.trim().charAt(0);
      return c ? c.toUpperCase() : '?';
    },
    avatarHue() {
      if (!this.me || !this.me.username) return 210;
      let h = 0;
      for (const ch of this.me.username) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
      return h % 360;
    },

    // URL routing helpers — keep the path in sync with view + section
    // so a browser refresh or shared link lands on the same screen.
    //
    // Path shape:
    //   /                            → stacks (default)
    //   /{view}                      → top-level view
    //   /settings/{section}          → Settings sidebar section
    //   /admin/{tab}                 → Admin sidebar tab
    // Unknown paths are left alone (the static file server already
    // handles login / assets; this only intervenes for known views).
    _routeViews() {
      return new Set(['stacks', 'services', 'nodes', 'hosts', 'history', 'settings', 'admin']);
    },
    _applyRouteFromPath() {
      const parts = (location.pathname || '/').split('/').filter(Boolean);
      if (!parts.length) return;
      const head = parts[0];
      if (!this._routeViews().has(head)) return;
      // Only assign if the current state differs, so this doesn't
      // thrash re-renders when the pushState we just wrote fires
      // popstate-like flows (it doesn't — noted for future-proofing).
      if (this.view !== head) this.view = head;
      const sub = parts[1];
      if (head === 'settings' && sub) {
        if ((this.settingsSections || []).some(s => s.id === sub)) {
          this.settingsSection = sub;
        }
      } else if (head === 'admin' && sub) {
        if ((this.adminSections || []).some(s => s.id === sub)) {
          // Use openAdminTab so the tab-specific load fires (users,
          // sessions, schedules, etc).
          this.openAdminTab(sub);
        }
      }
    },
    _pushRoute() {
      let path = '/' + (this.view || 'stacks');
      if (this.view === 'settings' && this.settingsSection) {
        path += '/' + this.settingsSection;
      } else if (this.view === 'admin' && this.adminTab) {
        path += '/' + this.adminTab;
      }
      // replaceState rather than pushState so refresh lands on the
      // same page without adding history entries per tab switch.
      // Back/forward via actual nav (hash changes, manual link) still
      // work because popstate runs _applyRouteFromPath.
      if (location.pathname !== path) {
        try { history.replaceState(null, '', path); } catch (_) {}
      }
    },

    async openAdminTab(tab) {
      // Stop the logs poller when leaving the Logs tab; it restarts
      // when the tab is opened again. Keeps network traffic silent
      // while the operator is elsewhere.
      if (this.adminTab === 'logs' && tab !== 'logs') this._stopLogPoll();
      this.adminTab = tab;
      if (tab === 'users') await this.loadUsers();
      else if (tab === 'sessions') await this.loadSessions();
      else if (tab === 'tokens') await this.loadTokens();
      else if (tab === 'backups') await this.loadBackups();
      else if (tab === 'schedules') {
        // Fire both loads in parallel — the scheduled table and the queue
        // table aren't related state-wise, no reason to wait on each other.
        await Promise.all([this.loadSchedules(), this.loadScheduleQueue()]);
      }
      else if (tab === 'logs') {
        await this.loadLogs(true);
        // Logs tab also renders the `tuning_log_retention_days`
        // settings card (moved from Process tunables) so it needs
        // the tuningForm/tuningEffective state too. Cheap call,
        // dedupes against `tuningLoaded` so a re-open doesn't double
        // fetch.
        if (!this.tuningLoaded) await this.loadTuning();
        this._startLogPoll();
      }
      // The four ex-Settings sections all read from the same /api/settings
      // payload, so a single load covers all of them. Load on every
      // open so edits from another tab don't go stale.
      else if (['notifications', 'general', 'portainer', 'oidc', 'host_stats'].includes(tab)) {
        await this.loadSettings();
        // Webmin section in host_stats also renders a tunable card
        // (tuning_webmin_probe_budget_seconds); ensure tuning state
        // is available the first time the operator visits.
        if (tab === 'host_stats' && !this.tuningLoaded) await this.loadTuning();
        // Notifications tab now hosts the relocated
        // tuning_notification_retention_days card; same lazy-load
        // pattern as host_stats so the bounds-chips + effective-value
        // chip render on first visit instead of waiting for the
        // operator to bounce through Admin → Config first.
        if (tab === 'notifications' && !this.tuningLoaded) await this.loadTuning();
        // The Ping test-target picker reads from `hostsConfig` (loaded
        // by the Hosts admin tab). When the operator opens the host_stats
        // tab without ever visiting Admin → Hosts in this session, the
        // picker is empty and the dropdown shows "No ping-enabled hosts"
        // even though there are some. Lazy-load on first visit.
        if (tab === 'host_stats' && !(Array.isArray(this.hostsConfig) && this.hostsConfig.length)) {
          this.loadHostsConfig().catch(() => {});
        }
      }
      else if (tab === 'hosts') {
        await this.loadHostsConfig();
        // Host groups live in /api/settings; load it alongside so the
        // groups editor at the bottom of this tab has current data.
        await this.loadSettings();
      }
      else if (tab === 'assets') {
        await this.loadSettings();
        await this.loadAssetCache();
      }
      else if (tab === 'config') {
        await this.loadTuning();
      }
    },

    // ----- Scheduler ----------------------------------------------------
    // Backend at /api/schedules (CRUD) and /api/schedules/queue (recent
    // scheduler-driven ops from the history table). Every write method
    // surfaces an error toast; every destructive action confirms first.

    async loadSchedules() {
      try {
        const r = await fetch('/api/schedules');
        if (!r.ok) return;
        const d = await r.json();
        // in-place reconcile (keyed on id) instead of wholesale
        // reassignment so any auto-refresh pass doesn't tear down the
        // row's expanded-detail / inline-edit state.
        this._reconcileById(this.schedules, d.schedules || []);
        if (Array.isArray(d.kinds) && d.kinds.length) this.scheduleKinds = d.kinds;
        if (typeof d.min_interval_seconds === 'number') {
          this.scheduleMinInterval = d.min_interval_seconds;
        }
      } catch (_) {}
    },

    async loadScheduleQueue() {
      // Server-side pagination: /api/schedules/queue accepts
      // page + page_size and returns one page plus total/pages.
      // Keeping the request narrow to one page's worth also saves
      // bandwidth on fleets with thousands of historic runs.
      try {
        const search = (this.scheduleQueueSearch || '').trim();
        const url = '/api/schedules/queue'
          + '?page=' + encodeURIComponent(this.scheduleQueuePage)
          + '&page_size=' + encodeURIComponent(this.scheduleQueuePageSize)
          + (search ? '&search=' + encodeURIComponent(search) : '');
        const r = await fetch(url);
        if (!r.ok) return;
        const d = await r.json();
        // in-place reconcile keyed on op_id when present;
        // synthetic ops (op_id is null on legacy rows / direct
        // gather-refresh writes) get a stable composite key from
        // (name + ts) so the reconciler can still match them across
        // ticks without trashing the row identity.
        const queue = (d.queue || []).map(row => {
          if (row && row.op_id != null && row.op_id !== '') return row;
          // Stamp a synthetic _key so _reconcileById can match it.
          return { ...row, _key: `${row && row.name || ''}@${row && row.ts || 0}` };
        });
        const key = queue.some(r => r && r._key) ? '_key' : 'op_id';
        // When the page is mixed (some rows have op_id, some _key),
        // pre-fill _key on every row so the reconciler's keyOf()
        // stays consistent across the whole array.
        if (key === '_key') {
          for (const row of queue) {
            if (row._key == null) {
              row._key = `op:${row.op_id}`;
            }
          }
        }
        this._reconcileById(this.scheduleQueue, queue, key);
        this.scheduleQueueTotal = Number.isFinite(d.total) ? d.total : this.scheduleQueue.length;
        this.scheduleQueueTotalPages = Number.isFinite(d.pages) && d.pages > 0 ? d.pages : 1;
        // Clamp current page if the backend reports fewer pages
        // than we thought (rows trimmed between requests).
        if (this.scheduleQueuePage > this.scheduleQueueTotalPages) {
          this.scheduleQueuePage = this.scheduleQueueTotalPages;
          // Re-fetch with the corrected page so the UI stays coherent.
          return this.loadScheduleQueue();
        }
      } catch (_) {}
    },
    scheduleQueuePages() { return this.scheduleQueueTotalPages; },
    // Wire name kept for template compatibility — now a thin pass-
    // through since the backend already slices to the current page.
    scheduleQueuePageItems() { return this.scheduleQueue; },
    setScheduleQueuePageSize(n) {
      const v = parseInt(n, 10);
      if (![10, 25, 50].includes(v)) return;
      this.scheduleQueuePageSize = v;
      this.scheduleQueuePage = 1;
      try { localStorage.setItem('scheduleQueuePageSize', String(v)); } catch {}
      // Refetch with the new page size.
      this.loadScheduleQueue();
    },
    scheduleQueueGoto(page) {
      const total = this.scheduleQueueTotalPages;
      const p = Math.max(1, Math.min(total, parseInt(page, 10) || 1));
      if (p === this.scheduleQueuePage) return;
      this.scheduleQueuePage = p;
      this.loadScheduleQueue();
    },
    // Debounced search — refetch 250ms after the operator stops
    // typing. Reset to page 1 so the new filtered result starts at
    // the top instead of an out-of-range page.
    onScheduleQueueSearchInput() {
      if (this._scheduleQueueSearchTimer) clearTimeout(this._scheduleQueueSearchTimer);
      this._scheduleQueueSearchTimer = setTimeout(() => {
        this.scheduleQueuePage = 1;
        this.loadScheduleQueue();
      }, 250);
    },
    clearScheduleQueueSearch() {
      this.scheduleQueueSearch = '';
      this.scheduleQueuePage = 1;
      this.loadScheduleQueue();
    },

    // Local JSON-validate so the operator gets feedback without a round-trip.
    // Empty string is normalised to {} so the common case (gather_refresh
    // has no params) doesn't require typing braces.
    _parseParamsText(raw) {
      const trimmed = (raw || '').trim();
      if (!trimmed) return {};
      let parsed;
      try { parsed = JSON.parse(trimmed); }
      catch (e) { throw new Error(this.t('admin.schedules.params_invalid_json')); }
      if (parsed == null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error(this.t('admin.schedules.params_must_be_object'));
      }
      return parsed;
    },

    async createSchedule() {
      if (this.scheduleBusy) return;
      const s = this.newSchedule;
      if (!s.name || !s.name.trim()) {
        this.showToast(this.t('admin.schedules.name_required'), 'error');
        return;
      }
      if (!this.scheduleKinds.includes(s.kind)) {
        this.showToast(this.t('admin.schedules.kind_unknown'), 'error');
        return;
      }
      if (s.interval_seconds < this.scheduleMinInterval) {
        this.showToast(this.t('admin.schedules.interval_too_small', {
          min: this.scheduleMinInterval,
        }), 'error');
        return;
      }
      // Cadence bundle — HH:MM is required for non-interval modes;
      // weekly needs at least one day; monthly needs a 1..31 day.
      const cadencePayload = this._buildCadencePayload(s);
      if (cadencePayload === null) return;  // helper already toasted
      let params;
      try { params = this._parseParamsText(s.params_text); }
      catch (e) { this.showToast(e.message, 'error'); return; }
      this.scheduleBusy = true;
      try {
        const r = await fetch('/api/schedules', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: s.name.trim(),
            kind: s.kind,
            params,
            interval_seconds: parseInt(s.interval_seconds, 10),
            enabled: !!s.enabled,
            ...cadencePayload,
          }),
        });
        if (r.ok) {
          this.showToast(this.t('admin.schedules.toasts.created'));
          this.newSchedule = {
            name: '', kind: this.scheduleKinds[0] || 'gather_refresh',
            params_text: '{}', interval_seconds: 3600, enabled: true,
            cadence_mode: 'interval', run_at_hhmm: '',
            days_of_week: [], day_of_month: 1,
          };
          await this.loadSchedules();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('admin.schedules.toasts.create_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
      finally { this.scheduleBusy = false; }
    },

    editSchedule(s) {
      // Clone so edits don't mutate the list until Save. `params_text` is
      // a pretty-printed JSON blob for the textarea; we re-parse on save.
      // The `kind` + `cadence_mode` selects need the documented empty →
      // $nextTick → reassign dance from CLAUDE.md : the modal's
      // <select> elements mount alongside their <template x-for>
      // <option> children, but Alpine commits x-model BEFORE the
      // options exist — so the matching <option value="X"> is missing
      // and the select silently falls back to the first option. Empty
      // first, double-$nextTick, then reassign once the inner x-for
      // has rendered.
      const targetKind = s.kind || '';
      const targetCadence = s.cadence_mode || (s.run_at_hhmm ? 'daily' : 'interval');
      this.editingSchedule = {
        ...s,
        kind: '',
        params_text: JSON.stringify(s.params || {}, null, 2),
        cadence_mode: '',
        run_at_hhmm: s.run_at_hhmm || '',
        days_of_week: Array.isArray(s.days_of_week) ? [...s.days_of_week] : [],
        day_of_month: s.day_of_month || 1,
      };
      this.$nextTick(() => {
        this.$nextTick(() => {
          if (!this.editingSchedule) return;
          this.editingSchedule.kind = targetKind;
          this.editingSchedule.cadence_mode = targetCadence;
        });
      });
    },

    // Build + validate the payload fields that correspond to the cadence
    // bundle. Returns null after toasting an error, so the caller can
    // early-return without wrapping in try/catch.
    _buildCadencePayload(s) {
      const mode = s.cadence_mode || 'interval';
      if (!['interval', 'daily', 'weekly', 'monthly'].includes(mode)) {
        this.showToast(this.t('admin.schedules.cadence_invalid'), 'error');
        return null;
      }
      if (mode === 'interval') {
        // Clear anchors when flipping back to interval mode so the
        // backend stops consulting stale values.
        return {
          cadence_mode: 'interval',
          run_at_hhmm: null,
          days_of_week: null,
          day_of_month: null,
        };
      }
      const hhmm = (s.run_at_hhmm || '').trim();
      if (!hhmm || !/^([01]\d|2[0-3]):[0-5]\d$/.test(hhmm)) {
        this.showToast(this.t('admin.schedules.hhmm_invalid'), 'error');
        return null;
      }
      if (mode === 'daily') {
        return {
          cadence_mode: 'daily',
          run_at_hhmm: hhmm,
          days_of_week: null,
          day_of_month: null,
        };
      }
      if (mode === 'weekly') {
        const dow = (s.days_of_week || []).map(d => parseInt(d, 10))
          .filter(n => Number.isInteger(n) && n >= 0 && n <= 6);
        if (!dow.length) {
          this.showToast(this.t('admin.schedules.weekly_needs_days'), 'error');
          return null;
        }
        return {
          cadence_mode: 'weekly',
          run_at_hhmm: hhmm,
          days_of_week: dow,
          day_of_month: null,
        };
      }
      // monthly
      const dom = parseInt(s.day_of_month, 10);
      if (!Number.isInteger(dom) || dom < 1 || dom > 31) {
        this.showToast(this.t('admin.schedules.monthly_dom_invalid'), 'error');
        return null;
      }
      return {
        cadence_mode: 'monthly',
        run_at_hhmm: hhmm,
        days_of_week: null,
        day_of_month: dom,
      };
    },

    toggleWeekday(s, day) {
      // Stable-order toggle so the stored array matches what the user
      // clicked; backend re-sorts anyway but this keeps re-open state
      // predictable.
      const arr = Array.isArray(s.days_of_week) ? [...s.days_of_week] : [];
      const i = arr.indexOf(day);
      if (i >= 0) arr.splice(i, 1);
      else arr.push(day);
      s.days_of_week = arr;
    },

    cancelEditSchedule() {
      this.editingSchedule = null;
    },

    async saveSchedule() {
      if (!this.editingSchedule) return;
      const e = this.editingSchedule;
      if (!e.name || !e.name.trim()) {
        this.showToast(this.t('admin.schedules.name_required'), 'error');
        return;
      }
      if (!this.scheduleKinds.includes(e.kind)) {
        this.showToast(this.t('admin.schedules.kind_unknown'), 'error');
        return;
      }
      if (e.interval_seconds < this.scheduleMinInterval) {
        this.showToast(this.t('admin.schedules.interval_too_small', {
          min: this.scheduleMinInterval,
        }), 'error');
        return;
      }
      const cadencePayload = this._buildCadencePayload(e);
      if (cadencePayload === null) return;
      let params;
      try { params = this._parseParamsText(e.params_text); }
      catch (err) { this.showToast(err.message, 'error'); return; }
      // UX-BUG-003 / prune_logs `days` validator. Backend
      // clamps to TUNABLES bounds [1, 365] silently; surfacing the
      // validation here lets the operator see "must be 1..365"
      // before they hit Save and see a generic "saved" toast on a
      // value that secretly snapped to the lower bound.
      if (e.kind === 'prune_logs' && params && 'days' in params) {
        const d = Number(params.days);
        if (!Number.isFinite(d) || !Number.isInteger(d) || d < 1 || d > 365) {
          this.showToast(this.t('admin.schedules.errors.prune_logs_days_range'), 'error');
          return;
        }
      }
      this.scheduleSaving = true;
      try {
        const r = await fetch('/api/schedules/' + e.id, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: e.name.trim(),
            kind: e.kind,
            params,
            interval_seconds: parseInt(e.interval_seconds, 10),
            enabled: !!e.enabled,
            ...cadencePayload,
          }),
        });
        if (r.ok) {
          this.showToast(this.t('admin.schedules.toasts.saved'));
          this.editingSchedule = null;
          await this.loadSchedules();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('admin.schedules.toasts.save_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
      finally { this.scheduleSaving = false; }
    },

    async toggleScheduleEnabled(s) {
      // Fire a PATCH with just the flipped flag — no confirm dialog; the
      // enable/disable toggle is reversible and doesn't kick off anything.
      try {
        const r = await fetch('/api/schedules/' + s.id, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: !s.enabled }),
        });
        if (r.ok) {
          await this.loadSchedules();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('admin.schedules.toasts.save_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    async deleteSchedule(s) {
      const res = await Swal.fire({
        title: this.t('admin.schedules.delete_prompt_title'),
        text: this.t('admin.schedules.delete_prompt_text', { name: s.name }),
        icon: 'warning', showCancelButton: true,
        confirmButtonText: this.t('actions.delete'),
        cancelButtonText: this.t('actions.cancel'),
        confirmButtonColor: this._cssVar('--danger'),
      });
      if (!res.isConfirmed) return;
      try {
        const r = await fetch('/api/schedules/' + s.id, { method: 'DELETE' });
        if (r.ok) {
          this.showToast(this.t('admin.schedules.toasts.deleted'));
          await this.loadSchedules();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.delete_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    async runSchedule(s) {
      // "Run now" bypasses the interval. Destructive kinds (prune_node)
      // still get a confirm so a stray click doesn't delete volumes.
      const destructiveKinds = new Set(['prune_node', 'prune_all_nodes']);
      if (destructiveKinds.has(s.kind)) {
        const ok = await this.confirmDialog({
          title: this.t('admin.schedules.run_prompt_title', { name: s.name }),
          html: this.t('admin.schedules.run_prompt_destructive_html', { kind: s.kind }),
          icon: 'warning',
          confirmText: this.t('admin.schedules.run_now'),
          confirmColor: this._cssVar('--danger'),
        });
        if (!ok) return;
      }
      try {
        const r = await fetch('/api/schedules/' + s.id + '/run', { method: 'POST' });
        if (r.ok) {
          this.showToast(this.t('admin.schedules.toasts.run_started', { name: s.name }));
          // Immediate ops-panel refresh so the operator sees progress.
          this.pollOpsNow();
          // Reload schedule rows so last_run_at flips into the visible past.
          // Small delay lets the backend finish its record_run() write.
          setTimeout(() => this.loadSchedules(), 400);
          setTimeout(() => this.loadScheduleQueue(), 1500);
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('admin.schedules.toasts.run_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    // --- Scheduler display helpers --------------------------------------
    // Reuse fmtDuration for last-duration column (same d/h/m/s bucketing).
    // humanInterval is similar but operates on the schedule's configured
    // interval — keep them separate so fmtDuration stays generic.
    humanInterval(sec) {
      if (!sec || sec <= 0) return '—';
      const d = Math.floor(sec / 86400);
      const h = Math.floor((sec % 86400) / 3600);
      const m = Math.floor((sec % 3600) / 60);
      const s = sec % 60;
      const parts = [];
      if (d) parts.push(d + 'd');
      if (h) parts.push(h + 'h');
      if (m) parts.push(m + 'm');
      if (!parts.length) parts.push(s + 's');
      // Keep it tight — two units max for readability ("1d 6h", not "1d 6h 15m 30s").
      return parts.slice(0, 2).join(' ');
    },

    // "5 minutes ago" / "in 2 hours" — used for Last execution / Next
    // execution columns. Pure JS, no dependency. Returns '—' for unset
    // timestamps so the column renders a visible placeholder.
    humanRelTime(epoch) {
      if (!epoch) return '—';
      const delta = Math.round(epoch - (Date.now() / 1000));
      const abs = Math.abs(delta);
      let value, unit;
      if (abs < 60)          { value = abs; unit = 'second'; }
      else if (abs < 3600)   { value = Math.round(abs / 60); unit = 'minute'; }
      else if (abs < 86400)  { value = Math.round(abs / 3600); unit = 'hour'; }
      else                   { value = Math.round(abs / 86400); unit = 'day'; }
      const suffix = value === 1 ? '' : 's';
      return delta >= 0
        ? this.t('admin.schedules.rel_in', { value, unit: unit + suffix })
        : this.t('admin.schedules.rel_ago', { value, unit: unit + suffix });
    },

    // Used only for the "Next execution" column — a past epoch there is
    // noise (the backend now always returns a future timestamp for
    // clock-anchored cadences, so this only fires for schedules the
    // tick loop is about to fire on its next pass).
    humanNextRun(epoch) {
      if (!epoch) return '—';
      const delta = Math.round(epoch - (Date.now() / 1000));
      if (delta <= 60) return this.t('admin.schedules.due_soon');
      return this.humanRelTime(epoch);
    },

    // One-line summary of how the schedule fires, for the table row.
    // Falls back to interval display if cadence_mode is missing (legacy rows).
    cadenceLabel(s) {
      const mode = s.cadence_mode || (s.run_at_hhmm ? 'daily' : 'interval');
      if (mode === 'daily' && s.run_at_hhmm) {
        return this.t('admin.schedules.daily_at', { hhmm: s.run_at_hhmm });
      }
      if (mode === 'weekly' && s.run_at_hhmm) {
        const days = (s.days_of_week || [])
          .map(d => this.t('admin.schedules.weekdays_short.' + d))
          .filter(Boolean)
          .join(', ');
        return this.t('admin.schedules.weekly_at', { days, hhmm: s.run_at_hhmm });
      }
      if (mode === 'monthly' && s.run_at_hhmm && s.day_of_month) {
        return this.t('admin.schedules.monthly_at', {
          dom: s.day_of_month, hhmm: s.run_at_hhmm,
        });
      }
      return this.humanInterval(s.interval_seconds);
    },

    scheduleStatusClass(status) {
      // Consistent pill colour across tables. Matches the existing pill
      // token families (pill-ok / pill-error / pill-unknown) so new UI
      // doesn't invent its own palette.
      if (status === 'success') return 'pill pill-ok';
      if (status === 'error')   return 'pill pill-error';
      return 'pill pill-unknown';
    },

    // ----- Backups ------------------------------------------------------
    async loadBackups() {
      try {
        const r = await fetch('/api/backups');
        if (r.ok) { const d = await r.json(); this.backups = d.backups || []; }
      } catch (_) {}
    },
    async createBackup() {
      if (this.backupBusy) return;
      this.backupBusy = true;
      try {
        const r = await fetch('/api/backups', { method: 'POST' });
        if (r.ok) {
          const d = await r.json().catch(() => ({}));
          if (Array.isArray(d.pruned) && d.pruned.length) {
            // Distinct toast when retention actually trimmed something;
            // operators care about being told what disappeared.
            this.showToast(this.t('toasts.backup_created_pruned', { count: d.pruned.length }));
          } else {
            this.showToast(this.t('toasts.backup_created'));
          }
          await this.loadBackups();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.backup_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
      finally { this.backupBusy = false; }
    },

    // Multi-source selector helpers. ``host_stats_source`` is now a
    // CSV ("beszel,node_exporter") — these helpers let the Settings
    // checkboxes treat it as a set.
    hasHostStatsSource(name) {
      const raw = this.settings.host_stats_source || '';
      return raw.split(',').map(s => s.trim()).includes(name);
    },
    // UX-ENH-002 — single source of truth for the disabled gate
    // shared by every per-provider admin panel's tuning-knob inputs.
    // Pre-fix each panel inlined `!isAdmin() || tuningSaving ||
    // !hasHostStatsSource('<provider>')` — same predicate, six times,
    // with the provider name as the only delta. Centralising means a
    // future change to the gate (e.g. add a master tuning-locked
    // setting) lands in one place. Each panel's inputs bind via
    // `:disabled="tuneKnobDisabled('<provider>')"`.
    tuneKnobDisabled(provider) {
      return !this.isAdmin() || this.tuningSaving || !this.hasHostStatsSource(provider);
    },
    toggleHostStatsSource(name, on) {
      const current = new Set(
        (this.settings.host_stats_source || '')
          .split(',').map(s => s.trim()).filter(s => s && s !== 'none'),
      );
      if (on) current.add(name);
      else current.delete(name);
      this.settings.host_stats_source = current.size
        ? Array.from(current).sort().join(',')
        : 'none';
    },

    // Settings → Host stats provider tab switcher. Persists the
    // chosen tab in localStorage so the page returns to the operator's
    // view on refresh. Mirrors the existing `setRefreshInterval` /
    // localStorage shape.
    setHostStatsTab(name) {
      if (!['node_exporter', 'beszel', 'pulse', 'webmin', 'ping', 'snmp'].includes(name)) return;
      this.hostStatsTab = name;
      try { localStorage.setItem('hostStatsTab', name); } catch {}
    },
    // Single source of truth for the strip's tab order. setHostStatsTab
    // already validates against this list; cycleHostStatsTab uses it to
    // implement ←/→ keyboard nav per the WAI-ARIA tablist authoring
    // pattern. New tabs added here automatically participate in
    // keyboard navigation.
    HOST_STATS_TAB_ORDER: ['node_exporter', 'beszel', 'pulse', 'webmin', 'ping', 'snmp'],
    // Cycle tabs by ±1, wrapping at both ends. Called from each tab
    // button's @keydown.left / @keydown.right handler. After the tab
    // switches we focus the newly-active button so the focus ring
    // tracks selection (per ARIA tablist guidance).
    cycleHostStatsTab(direction) {
      const order = this.HOST_STATS_TAB_ORDER;
      const cur = order.indexOf(this.hostStatsTab);
      const i = cur < 0 ? 0 : cur;
      const next = (i + (direction > 0 ? 1 : order.length - 1)) % order.length;
      this.setHostStatsTab(order[next]);
      // Re-focus the newly-active tab button so keyboard users see the
      // focus ring track the selected tab. The button carries an id of
      // the form `provider-tab-<key>` so the lookup is deterministic.
      this.$nextTick(() => {
        const el = document.getElementById('provider-tab-' + order[next]);
        if (el && typeof el.focus === 'function') el.focus();
      });
    },

    // Serialise just the host-stats-related subset of settings to a
    // stable string. Used by _hostStatsBaseline and hostStatsDirty()
    // to detect unsaved edits. Password / token fields are always
    // "" on the live form (write-only on the wire), so any typed
    // value naturally marks the form dirty.
    _hostStatsSnapshot() {
      const s = this.settings || {};
      const pick = [
        'host_stats_source',
        'node_exporter_enabled', 'node_exporter_url_template',
        'node_exporter_overrides_json',
        'beszel_hub_url', 'beszel_identity', 'beszel_password',
        'beszel_verify_tls',
        'pulse_url', 'pulse_token', 'pulse_verify_tls',
        'webmin_url', 'webmin_user', 'webmin_password',
        'webmin_verify_tls',
        // ping provider settings flow through the same dirty
        // tracker so saveHostStats picks them up alongside the other
        // providers' fields.
        'ping_enabled', 'ping_default_port', 'ping_use_icmp',
        // SNMP. v3 secret keys behave like beszel_password /
        // webmin_password — `_set` flag indicates persisted state, the
        // `*_key` strings are blanked on the form so any typed value
        // marks dirty. The aliases JSON also rides this dirty list.
        'snmp_default_community', 'snmp_default_version',
        'snmp_default_port', 'snmp_v3_user',
        'snmp_v3_auth_key', 'snmp_v3_priv_key',
        'snmp_aliases_json',
        // per-provider chip colour overrides.
        'provider_color_beszel', 'provider_color_pulse',
        'provider_color_node_exporter', 'provider_color_webmin',
        'provider_color_ping', 'provider_color_snmp',
      ];
      const subset = {};
      for (const k of pick) subset[k] = s[k];
      try { return JSON.stringify(subset); } catch { return ''; }
    },
    // Cheap dirty check — called from the template every render. String
    // comparison is O(length) which is trivial for the ~15-key subset.
    // dirty also when any of the 3 tunables that live in this
    // panel (Webmin probe budget + 2 cache TTLs + NE timeout) has an
    // unsaved change. tuningDirty() walks _allTuningKeys() so it
    // catches the relocated keys correctly.
    hostStatsDirty() {
      return this._hostStatsBaseline !== this._hostStatsSnapshot()
          || this.tuningDirty();
    },
    // In-flight flag for the unified host_stats Save button so
    // the spinner / "Saving…" label fires the same way as the
    // per-section Save buttons did pre-#555.
    hostStatsSaving: false,

    async saveHostStats() {
      this.hostStatsSaving = true;
      try {
        await this._saveHostStatsImpl();
      } finally {
        this.hostStatsSaving = false;
      }
    },

    async _saveHostStatsImpl() {
      const raw = this.settings.host_stats_source || 'none';
      const active = new Set(
        raw.split(',').map(s => s.trim()).filter(s => s && s !== 'none'),
      );
      const valid = new Set(['beszel', 'node_exporter', 'pulse', 'webmin', 'ping', 'snmp']);
      for (const s of active) {
        if (!valid.has(s)) {
          this.showToast(this.t('settings.host_stats.source_invalid'), 'error');
          return;
        }
      }
      // Source-specific validation — only the active sources' fields
      // are required to be well-formed; the others are persisted as-is
      // so toggling sources doesn't forget prior config.
      const normalized = active.size
        ? Array.from(active).sort().join(',')
        : 'none';
      const payload = {
        host_stats_source: normalized,
        // Keep node_exporter_enabled flag in sync with the selected
        // source for back-compat with anything reading the legacy flag.
        node_exporter_enabled: active.has('node_exporter'),
      };
      if (active.has('node_exporter')) {
        const tpl = (this.settings.node_exporter_url_template || '').trim();
        // Either placeholder is valid — {host} substitutes the Docker
        // hostname, {ip} substitutes the Swarm-advertised IP.
        if (tpl && !tpl.includes('{host}') && !tpl.includes('{ip}')) {
          this.showToast(this.t('settings.host_stats.placeholder_required'), 'error');
          return;
        }
        let overrides = {};
        const raw = (this.settings.node_exporter_overrides_json || '').trim() || '{}';
        try {
          const parsed = JSON.parse(raw);
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            overrides = parsed;
          } else { throw new Error('object expected'); }
        } catch (_) {
          this.showToast(this.t('settings.host_stats.overrides_invalid'), 'error');
          return;
        }
        payload.node_exporter_url_template = tpl;
        payload.node_exporter_overrides = overrides;
      }
      if (active.has('beszel')) {
        const hub = (this.settings.beszel_hub_url || '').trim();
        const ident = (this.settings.beszel_identity || '').trim();
        if (!hub || !ident) {
          this.showToast(this.t('settings.host_stats.beszel_required'), 'error');
          return;
        }
        if (!this.settings.beszel_password_set && !this.settings.beszel_password) {
          this.showToast(this.t('settings.host_stats.beszel_password_required'), 'error');
          return;
        }
        payload.beszel_hub_url = hub;
        payload.beszel_identity = ident;
        payload.beszel_verify_tls = !!this.settings.beszel_verify_tls;
        if (this.settings.beszel_password) {
          payload.beszel_password = this.settings.beszel_password;
        }
      }
      if (active.has('pulse')) {
        const url = (this.settings.pulse_url || '').trim();
        if (!url) {
          this.showToast(this.t('toasts_extra.pulse_url_required'), 'error');
          return;
        }
        if (!this.settings.pulse_token_set && !this.settings.pulse_token) {
          this.showToast(this.t('toasts_extra.pulse_token_required'), 'error');
          return;
        }
        payload.pulse_url = url;
        payload.pulse_verify_tls = !!this.settings.pulse_verify_tls;
        if (this.settings.pulse_token) {
          payload.pulse_token = this.settings.pulse_token;
        }
      }
      if (active.has('webmin')) {
        const user = (this.settings.webmin_user || '').trim();
        if (!user) {
          this.showToast(this.t('toasts_extra.webmin_user_required'), 'error');
          return;
        }
        if (!this.settings.webmin_password_set && !this.settings.webmin_password) {
          this.showToast(this.t('toasts_extra.webmin_password_required'), 'error');
          return;
        }
        // Strip trailing slash(es) — operators paste URLs with or
        // without a trailing slash; normalise here so the backend
        // and the per-host aliases map store one canonical form.
        payload.webmin_url = (this.settings.webmin_url || '').trim().replace(/\/+$/, '');
        payload.webmin_user = user;
        payload.webmin_verify_tls = !!this.settings.webmin_verify_tls;
        if (this.settings.webmin_password) {
          payload.webmin_password = this.settings.webmin_password;
        }
        payload.webmin_aliases = this.settings.webmin_aliases || {};
      }
      // ping provider. No secrets, so we always include the
      // fields if the master toggle is on; backend bounds-checks the
      // port. When the source isn't active, the master toggle still
      // round-trips so the operator's saved port + transport are
      // preserved across enable/disable cycles.
      if (this.settings.ping_enabled !== undefined) {
        payload.ping_enabled = !!this.settings.ping_enabled;
      }
      if (this.settings.ping_default_port) {
        const p = parseInt(this.settings.ping_default_port, 10);
        if (Number.isFinite(p) && p >= 1 && p <= 65535) {
          payload.ping_default_port = p;
        }
      }
      if (this.settings.ping_use_icmp !== undefined) {
        payload.ping_use_icmp = !!this.settings.ping_use_icmp;
      }
      // SNMP provider. Defaults always round-trip (so the
      // operator's saved community / version / port survive an
      // enable/disable cycle). v3 keys follow the keep-current-if-blank
      // contract — only POSTed when the user actually types a value.
      // Aliases ride the JSON-textarea pattern from node_exporter
      // overrides; `snmp_aliases_json` holds the textarea string and
      // we parse-and-reject here.
      if (this.settings.snmp_default_community !== undefined) {
        payload.snmp_default_community = (this.settings.snmp_default_community || '').trim();
      }
      if (this.settings.snmp_default_version !== undefined) {
        const v = (this.settings.snmp_default_version || '').trim().toLowerCase();
        if (v && v !== 'v2c' && v !== 'v3') {
          this.showToast(this.t('settings.host_stats.snmp.version_invalid'), 'error');
          return;
        }
        payload.snmp_default_version = v;
      }
      if (this.settings.snmp_default_port !== undefined && this.settings.snmp_default_port !== '') {
        const p = parseInt(this.settings.snmp_default_port, 10);
        if (Number.isFinite(p) && p >= 1 && p <= 65535) {
          payload.snmp_default_port = p;
        }
      }
      if (this.settings.snmp_v3_user !== undefined) {
        payload.snmp_v3_user = (this.settings.snmp_v3_user || '').trim();
      }
      if (this.settings.snmp_v3_auth_key) {
        payload.snmp_v3_auth_key = this.settings.snmp_v3_auth_key;
      }
      if (this.settings.snmp_v3_priv_key) {
        payload.snmp_v3_priv_key = this.settings.snmp_v3_priv_key;
      }
      if (this.settings.snmp_aliases_json !== undefined) {
        const raw = (this.settings.snmp_aliases_json || '').trim() || '{}';
        let aliases = {};
        try {
          const parsed = JSON.parse(raw);
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            aliases = parsed;
          } else { throw new Error('object expected'); }
        } catch (_) {
          this.showToast(this.t('settings.host_stats.snmp.aliases_invalid'), 'error');
          return;
        }
        payload.snmp_aliases = aliases;
      }
      // per-provider chip colour overrides. Always packed into
      // the payload (even when blank → backend treats blank as "clear
      // the override"). Hex validation is done server-side; the colour
      // input element naturally produces #RRGGBB so a malformed value
      // would only arrive via direct API tampering.
      for (const k of [
        'provider_color_beszel', 'provider_color_pulse',
        'provider_color_node_exporter', 'provider_color_webmin',
        'provider_color_ping', 'provider_color_snmp',
      ]) {
        if (this.settings[k] !== undefined) {
          payload[k] = (this.settings[k] || '').trim();
        }
      }
      // fold in any dirty tunables that live on this panel
      // (Webmin probe budget + cache TTLs, NE probe timeout). The
      // backend's /api/settings POST is the same endpoint the
      // dedicated saveTuning() uses, so packaging them in the same
      // payload removes the need for per-section Save buttons.
      // Validate via the same int + bounds check saveTuning does.
      for (const k of this._allTuningKeys()) {
        const raw = (this.tuningForm || {})[k];
        if (raw === '' || raw == null) {
          // Blank means "clear the override" — include it in the
          // payload as an empty string so the backend deletes the
          // setting row.
          payload[k] = '';
          continue;
        }
        const n = Number(raw);
        if (!Number.isFinite(n) || !Number.isInteger(n)) {
          this.showToast(this.t('admin.config.errors.must_be_int', {
            field: this.t('admin.config.fields.' + k + '.label'),
          }), 'error');
          return;
        }
        const eff = (this.tuningEffective || {})[k] || {};
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
        payload[k] = String(raw).trim();
      }
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (r.ok) {
          this.showToast(this.t('settings.host_stats.saved'));
          // Flip the "password is stored" indicator once we've persisted
          // a new password so the UI stops nagging to re-enter it.
          if (payload.beszel_password) {
            this.settings.beszel_password_set = true;
            this.settings.beszel_password = '';
          }
          if (payload.webmin_password) {
            this.settings.webmin_password_set = true;
            this.settings.webmin_password = '';
          }
          // SNMP v3 keys. Same flip-flag-and-clear pattern as
          // every other write-only secret in this form.
          if (payload.snmp_v3_auth_key) {
            this.settings.snmp_v3_auth_key_set = true;
            this.settings.snmp_v3_auth_key = '';
          }
          if (payload.snmp_v3_priv_key) {
            this.settings.snmp_v3_priv_key_set = true;
            this.settings.snmp_v3_priv_key = '';
          }
          // Re-capture baseline so the dirty indicator clears now that
          // the server has the same values we just sent.
          this._hostStatsBaseline = this._hostStatsSnapshot();
          // also re-baseline the tuning form (we just POSTed
          // the same values) so `tuningDirty()` flips back to false
          // and the unified Save button loses its amber ring.
          // loadTuning() does both: re-fetch /api/admin/tuning and
          // reset _tuningBaseline.
          await this.loadTuning();
          // Refresh items so the new nodes_info fields land immediately.
          this.refresh(true);
          // ALSO re-fetch /api/hosts/list with force=true so the next
          // host-data render bypasses the 10s `_host_provider_cache`
          // memo (#367 / UX-001). Without this, host rows could show
          // "Refreshing host data…" or stale provider state for up to
          // 10s after the save toast.
          this.loadHosts(true);
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    async saveRetention() {
      if (this.retentionSaving) return;
      this.retentionSaving = true;
      // Separate save endpoint from the general settings form so the
      // Backups tab doesn't require pushing the full SettingsIn bundle.
      const n = Math.max(0, parseInt(this.settings.backup_retention_count, 10) || 0);
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ backup_retention_count: n }),
        });
        if (r.ok) {
          this.settings.backup_retention_count = n;
          this.showToast(this.t('toasts.retention_saved'));
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
      finally { this.retentionSaving = false; }
    },

    // Scheduler settings — currently just the IANA timezone. Blank
    // value clears the override and the scheduler falls back to
    // container-local time. Invalid IANA names return 400 from the
    // backend (zoneinfo.ZoneInfo validates), which we surface as a
    // toast so the operator knows to fix the typo.
    // Persist the Open-Meteo upstream URL (Admin → Notifications).
    // Blank = clear override, fall back to the baked-in default.
    // Admin → Hosts toggle: show / hide the host-drawer debug panel
    // Save the Debug-tab settings via the smart-getter dirty pattern
    // — POSTs the toggle, re-baselines on success so the amber
    // "Unsaved" indicator clears, and reports failure via toast.
    debugSaving: false,
    async saveDebugSettings() {
      if (this.debugSaving) return;
      this.debugSaving = true;
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            debug_panel_enabled: !!this.settings.debug_panel_enabled,
          }),
        });
        if (r.ok) {
          this._debugBaseline = this._debugSnapshot();
          this.showToast(this.t('admin_hosts.debug_panel_toggle_saved'), 'success');
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts_extra.save_failed_generic'), 'error');
        }
      } catch (_) {
        this.showToast(this.t('toasts_extra.network_error_generic'), 'error');
      } finally {
        this.debugSaving = false;
      }
    },

    totpPolicySaving: false,
    async saveTotpPolicy() {
      if (this.totpPolicySaving) return;
      this.totpPolicySaving = true;
      try {
        const s = this.settings || {};
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            totp_allowed:              !!s.totp_allowed,
            totp_required_for_admins:  !!s.totp_required_for_admins,
            totp_required_for_users:   !!s.totp_required_for_users,
            totp_lockout_max_failures: +s.totp_lockout_max_failures || 5,
            totp_lockout_minutes:      +s.totp_lockout_minutes || 15,
            passkeys_allowed:          !!s.passkeys_allowed,
          }),
        });
        if (r.ok) {
          this._totpPolicyBaseline = this._totpPolicySnapshot();
          this.showToast(this.t('toasts.settings_saved'), 'success');
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts_extra.save_failed_generic'), 'error');
        }
      } catch (_) {
        this.showToast(this.t('toasts_extra.network_error_generic'), 'error');
      } finally {
        this.totpPolicySaving = false;
      }
    },

    async saveOpenMeteoUrl() {
      if (this.openMeteoSaving) return;
      this.openMeteoSaving = true;
      try {
        const url = (this.settings.open_meteo_url || '').trim().replace(/\/+$/, '');
        // Save the enabled flag together with the URL so the operator's
        // toggle-change persists on Save (no per-checkbox auto-save).
        const enabled = !!this.settings.open_meteo_enabled;
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            open_meteo_url: url,
            open_meteo_enabled: enabled,
          }),
        });
        if (r.ok) {
          this.settings.open_meteo_url = url;
          this._openMeteoBaseline = this._openMeteoSnapshot();
          this.showToast(this.t('admin_integrations.open_meteo_saved'), 'success');
          // Re-fetch weather now so the topbar reflects the new upstream.
          if (this.loadHeaderWeather) this.loadHeaderWeather();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts_extra.save_failed_generic'), 'error');
        }
      } catch (_) {
        this.showToast(this.t('toasts_extra.network_error_generic'), 'error');
      } finally {
        this.openMeteoSaving = false;
      }
    },

    async saveSchedulerSettings() {
      if (this.schedulerSaving) return;
      this.schedulerSaving = true;
      try {
        const tz = (this.settings.scheduler_timezone || '').trim();
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scheduler_timezone: tz }),
        });
        if (r.ok) {
          this.settings.scheduler_timezone = tz;
          this.showToast(tz
            ? this.t('scheduler_settings.saved_set', { tz })
            : this.t('scheduler_settings.saved_cleared'),
            'success');
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts_extra.save_failed_generic'), 'error');
        }
      } catch (_) {
        this.showToast(this.t('toasts_extra.network_error_generic'), 'error');
      } finally {
        this.schedulerSaving = false;
      }
    },

    // ----- App logs -----------------------------------------------------
    async loadLogs(replace = false) {
      try {
        // `since` makes repeated polls cheap — backend only returns
        // lines newer than the last one we've already rendered. On
        // `replace`, we pull the full tail and reset client state.
        const qs = replace ? '?limit=500' : ('?since=' + this.logSinceTs);
        const r = await fetch('/api/logs' + qs);
        if (!r.ok) return;
        const d = await r.json();
        const lines = d.logs || [];
        if (replace) this.logLines = lines;
        else if (lines.length) this.logLines = [...this.logLines, ...lines];
        // Cap the client-side buffer at 2× server MAX so the UI doesn't
        // grow forever even if the session stays on the tab for hours.
        const cap = (d.max || 2000) * 2;
        if (this.logLines.length > cap) {
          this.logLines = this.logLines.slice(-cap);
        }
        if (this.logLines.length) {
          this.logSinceTs = this.logLines[this.logLines.length - 1].ts;
        }
        // Autoscroll to bottom when the viewer is open and the user
        // hasn't scrolled up. $nextTick so the DOM has the new rows.
        this.$nextTick(() => {
          const box = document.getElementById('log-viewer');
          if (!box) return;
          const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
          if (replace || atBottom) box.scrollTop = box.scrollHeight;
        });
      } catch (_) {}
    },

    _startLogPoll() {
      this._stopLogPoll();
      // 2s poll — fast enough for "watch a deploy" UX, slow enough to
      // not hammer the admin API when nothing's happening.
      this.logPollHandle = setInterval(() => {
        if (this.logAuto) this.loadLogs(false);
      }, 2000);
    },

    _stopLogPoll() {
      if (this.logPollHandle) {
        clearInterval(this.logPollHandle);
        this.logPollHandle = null;
      }
    },

    async clearLogs() {
      const res = await Swal.fire({
        title: this.t('admin.logs.clear_prompt_title'),
        text: this.t('admin.logs.clear_prompt_text'),
        icon: 'warning', showCancelButton: true,
        confirmButtonText: this.t('actions.clear'),
        cancelButtonText: this.t('actions.cancel'),
        confirmButtonColor: this._cssVar('--danger'),
      });
      if (!res.isConfirmed) return;
      try {
        const r = await fetch('/api/logs', { method: 'DELETE' });
        if (r.ok) {
          this.logLines = [];
          this.logSinceTs = 0;
          this.showToast(this.t('admin.logs.cleared'));
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    filteredLogLines() {
      const q = (this.logFilter || '').toLowerCase();
      const sev = this.logSeverityFilter || {};
      const allSevOn = this.logSeverityLevels.every(k => sev[k]);
      if (!q && allSevOn) return this.logLines;
      return this.logLines.filter(l => {
        if (!allSevOn && !sev[this.logSeverity(l)]) return false;
        if (q && !l.text.toLowerCase().includes(q)) return false;
        return true;
      });
    },
    // Multi-select severity controls. Persist to localStorage so
    // the view survives a reload. setAll/errorsOnly mirror the same
    // shape as the Notifications event grid's bulk buttons.
    toggleLogSeverity(level) {
      this.logSeverityFilter[level] = !this.logSeverityFilter[level];
      this._persistLogSeverity();
    },
    // Count of log lines that resolve to a given severity level (used
    // in the per-pill counter chips). Walks logLines once per call —
    // the list is small (capped ring buffer) so this is cheap.
    logSeverityCount(level) {
      let n = 0;
      for (const l of (this.logLines || [])) {
        if (this.logSeverity(l) === level) n++;
      }
      return n;
    },
    // True when every severity level is on — used by the ALL pill's
    // is-active state. False if any level is off.
    logAllSeverityOn() {
      for (const k of this.logSeverityLevels) {
        if (!this.logSeverityFilter[k]) return false;
      }
      return true;
    },
    setAllLogSeverity(on) {
      for (const k of this.logSeverityLevels) this.logSeverityFilter[k] = !!on;
      this._persistLogSeverity();
    },
    setLogSeverityErrorsOnly() {
      for (const k of this.logSeverityLevels) {
        this.logSeverityFilter[k] = (k === 'error');
      }
      this._persistLogSeverity();
    },
    _persistLogSeverity() {
      try {
        localStorage.setItem('logSeverityFilter', JSON.stringify(this.logSeverityFilter));
      } catch (_) {}
    },
    _restoreLogSeverity() {
      try {
        const raw = localStorage.getItem('logSeverityFilter');
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object') {
          for (const k of this.logSeverityLevels) {
            if (typeof parsed[k] === 'boolean') {
              this.logSeverityFilter[k] = parsed[k];
            }
          }
        }
      } catch (_) {}
    },
    // Persistent log files. Lists / views / live-tails the
    // daily files under /app/data/logs/. Download URL is the same
    // route, no streaming — the file is small (one day's logs).
    async loadLogFiles() {
      try {
        const r = await fetch('/api/admin/logs/files');
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = await r.json();
        this.logFiles = Array.isArray(d.files) ? d.files : [];
        this.logFilesDir = d.log_dir || '';
      } catch (e) {
        this.showToast(this.t('toasts.network_error'), 'error');
      }
    },
    async viewLogFile(name) {
      this.logSelectedFile = name;
      await this._fetchLogFileBody();
      // Restart the auto-tail poll for the newly-selected file.
      if (this._logFileTimer) clearInterval(this._logFileTimer);
      this._logFileTimer = setInterval(() => {
        if (this.logsSubTab !== 'files' || !this.logSelectedFile || !this.logFileAutoTail) {
          return;
        }
        this._fetchLogFileBody();
      }, 5000);
    },
    async _fetchLogFileBody() {
      if (!this.logSelectedFile) { this.logFileBody = ''; return; }
      try {
        const r = await fetch(
          '/api/admin/logs/files/' + encodeURIComponent(this.logSelectedFile)
          + '?tail=' + this.logFileTailLines,
        );
        if (!r.ok) {
          this.logFileBody = `(unable to read: HTTP ${r.status})`;
          return;
        }
        this.logFileBody = await r.text();
      } catch (e) {
        this.logFileBody = `(network error: ${e.message})`;
      }
    },
    // Parse the file body into the same {ts, stream, text} shape the
    // Live tab uses, so the renderer can reuse `logSeverity` +
    // `colorizeLogText` + the `log-line--<sev>` class scheme.
    // File format from `logic/logs.py:_persist_line`:
    //   `2026-04-27T12:34:56Z LEVEL  message body`
    // Where LEVEL is one of ERROR / WARN / SUCCESS / INFO. Any line
    // that doesn't match the regex falls through as raw INFO so a
    // pre-existing file in some other format still renders, just
    // without the timestamp tint.
    parsedLogFileLines() {
      const body = this.logFileBody || '';
      if (!body) return [];
      const lines = body.split('\n');
      // ISO ts + 1 or more spaces + LEVEL token + space + rest.
      const RX = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(ERROR|WARN|SUCCESS|INFO)\s+(.*)$/;
      const out = [];
      for (const raw of lines) {
        if (!raw) continue;
        const m = RX.exec(raw);
        if (m) {
          const epoch = Date.parse(m[1]) / 1000;
          out.push({
            ts: Number.isFinite(epoch) ? epoch : 0,
            stream: 'file',
            level: m[2].toLowerCase(),  // matches `logSeverity()` output
            text: m[3],
          });
        } else {
          // Non-conforming line — render as INFO with the raw text.
          out.push({ ts: 0, stream: 'file', level: 'info', text: raw });
        }
      }
      return out;
    },
    // The Live tab's template hands each row to `logSeverity(l)` to
    // pick the `log-line--<sev>` class. Parsed file rows already
    // carry an explicit `level` (extracted from the file line) — use
    // it directly when present so we don't run the regex scan a
    // second time. Falls back to logSeverity for raw INFO rows that
    // didn't match the regex.
    logSeverityFor(l) {
      return (l && l.level) ? l.level : this.logSeverity(l);
    },
    // Same severity filter applied to the Files-tab parsed lines. Uses
    // logSeverityFor (which prefers the parsed `level` field, falls
    // back to `logSeverity` regex) so an INFO row whose body just has
    // "loading..." doesn't get reclassified. Shares `logSeverityFilter`
    // state with the Live tab so toggling a level in either tab carries.
    filteredLogFileLines() {
      const sev = this.logSeverityFilter || {};
      const allOn = this.logSeverityLevels.every(k => sev[k]);
      const lines = this.parsedLogFileLines();
      // Text filter — same shape as the live (stdout) tab. Reuses
      // `logFilter` so a query typed in either tab carries across,
      // matching the existing `logSeverityFilter` cross-tab pattern.
      // Case-insensitive substring match against the parsed line's
      // `text` field (parsedLogFileLines emits {ts, stream, level,
      // text} — earlier draft of this filter checked `body`/`msg`
      // which the file path doesn't populate, so every query
      // returned zero rows).
      const q = (this.logFilter || '').trim().toLowerCase();
      const filterByText = (l) => {
        if (!q) return true;
        const text = (l && (l.text || l.body || l.msg || '')) + '';
        return text.toLowerCase().includes(q);
      };
      if (allOn) return q ? lines.filter(filterByText) : lines;
      return lines.filter(l => !!sev[this.logSeverityFor(l)] && filterByText(l));
    },
    // Per-level count for the Files-tab pill chips.
    logFileSeverityCount(level) {
      let n = 0;
      for (const l of this.parsedLogFileLines()) {
        if (this.logSeverityFor(l) === level) n++;
      }
      return n;
    },
    // Copy the currently-filtered log view to the clipboard as plain
    // text. Format: "YYYY-MM-DD HH:MM:SS [stream] body" per line, so
    // the paste lands cleanly in issue trackers / chat apps with the
    // same visual shape as the viewer. Falls back to a selection-based
    // copy when navigator.clipboard isn't available (older browsers /
    // insecure contexts).
    async copyFilteredLogs() {
      const lines = this.filteredLogLines();
      if (!lines.length) return;
      const body = lines.map(l => {
        const ts = this.fmtDate ? this.fmtDate(l.ts) : String(l.ts);
        const stream = (l.stream || '').toUpperCase();
        return `${ts} [${stream}] ${l.text}`;
      }).join('\n');
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(body);
        } else {
          // Fallback — dispatch a textarea + execCommand('copy').
          // `position: fixed` + `opacity: 0` + `pointer-events: none`
          // is RTL-clean (no inset / left to flip) AND off-screen for
          // sighted users. Pre-fix used `left: -9999px` which placed
          // the textarea off the wrong edge under RTL — invisible
          // both ways but the pattern is non-RTL-safe and could be
          // copied as a template.
          const ta = document.createElement('textarea');
          ta.value = body;
          ta.setAttribute('readonly', '');
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          ta.style.pointerEvents = 'none';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
        }
        this.showToast(this.t('admin.logs.copied', { n: lines.length }), 'success');
      } catch (e) {
        this.showToast(this.t('admin.logs.copy_failed'), 'error');
      }
    },
    // Severity derived from the line body — stderr alone is coarse
    // (backend error prints go to stdout too via print()). We look
    // for textual markers anywhere in the line: ERROR / FAIL[ED|URE]
    // → 'error'; WARN / WARNING → 'warn'; otherwise 'info'.
    // Lowercase compare so "Error:", "error:", "ERROR:" all match.
    logSeverity(l) {
      if (!l) return 'info';
      const text = (l.text || '').toLowerCase();
      // stderr AND a tell-tale tag beats "happy-looking" body — but
      // a stderr line with no negative keywords stays at 'info' (our
      // own noisy prints go to stderr all the time).
      if (/\berror\b|\bfail(?:ed|ure)?\b|\btraceback\b|\bcritical\b|\bfatal\b/.test(text)) {
        return 'error';
      }
      if (/\bwarn(?:ing)?\b|deprecat/.test(text)) return 'warn';
      // Explicit success/OK lines get their own class so
      // "[xxx] probe SUCCESS" / "OK —" read as green.
      if (/\bsuccess\b|\bok —|→ ok\b/i.test(l.text || '')) return 'ok';
      return 'info';
    },
    // Escape HTML-unsafe characters before wrapping known prefixes in
    // coloured spans. Without this, a log line that happened to
    // contain `<img onerror=...>` would execute on render (Alpine's
    // x-html is unsandboxed — we have to be strict here).
    _logEscape(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    },
    // Wrap recognised tags like [webmin] / [beszel] in coloured
    // spans. Returns safe HTML (already escaped) for x-html. Tag
    // map below keeps the list explicit — new backend prefixes need
    // to be added here to get a distinct colour (otherwise they
    // fall through to the default tag colour).
    colorizeLogText(l) {
      const raw = (l && l.text) || '';
      const esc = this._logEscape(raw);
      // Tags known to carry a distinct colour class. Falls through
      // to `log-tag` (neutral accent) for unknown tag names so ALL
      // bracketed prefixes get highlighted even if uncategorised.
      const tagColors = new Set([
        'webmin', 'beszel', 'pulse', 'hosts', 'host_net_sampler',
        'ssh', 'portainer', 'i18n', 'ops', 'schedules', 'gather',
        'node_exporter', 'ne', 'oidc', 'auth', 'backup', 'stats',
        'deploy', 'version',
        // TOTP / 2FA state changes (enrol / verify / lockout
        // / admin-disable). Shares the OIDC accent token via CSS.
        'totp',
        // WebAuthn / passkey state changes. Same OIDC accent
        // family — both share the security domain.
        'webauthn',
        // SNMP host-stats provider diagnostics.
        'snmp',
        // Bulk-action operations on the Hosts view (pause /
        // resume / vendors / tunables). Distinct sub-tag so
        // operators can grep all bulk activity in one shot.
        'hosts:bulk',
      ]);
      // Replace [xxx] at the start of (or inside) the line. Allow
      // underscores / hyphens / colons for tag names like
      // [host_net_sampler] and the [hosts:bulk] sub-tag family.
      const withTags = esc.replace(/\[([a-z][a-z0-9_.:\-]*?)\]/gi, (_m, tag) => {
        const key = tag.toLowerCase();
        // Colons in tag names map to hyphens in the CSS class so
        // selectors stay simple (no escape required for `:`).
        const cssKey = key.replace(/:/g, '-');
        const cls = tagColors.has(key) ? ('log-tag log-tag--' + cssKey) : 'log-tag';
        return '<span class="' + cls + '">[' + tag + ']</span>';
      });
      return withTags;
    },

    async deleteBackup(b) {
      const res = await Swal.fire({
        title: this.t('admin.backups.delete_prompt_title'), text: b.name, icon: 'warning',
        showCancelButton: true, confirmButtonText: this.t('actions.delete'),
        cancelButtonText: this.t('actions.cancel'),
      });
      if (!res.isConfirmed) return;
      try {
        const r = await fetch('/api/backups/' + encodeURIComponent(b.name), { method: 'DELETE' });
        if (r.ok) { this.showToast(this.t('toasts.backup_deleted')); await this.loadBackups(); }
        else this.showToast(this.t('toasts.delete_failed'), 'error');
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },
    async restoreBackup(b) {
      const res = await Swal.fire({
        title: this.t('admin.backups.restore_prompt_title', { name: b.name }),
        html: this.t('admin.backups.restore_prompt_html'),
        icon: 'warning', showCancelButton: true,
        confirmButtonText: this.t('admin.backups.restore_button'),
        cancelButtonText: this.t('actions.cancel'),
        confirmButtonColor: this._cssVar('--danger'),
      });
      if (!res.isConfirmed) return;
      try {
        const r = await fetch('/api/backups/' + encodeURIComponent(b.name) + '/restore', { method: 'POST' });
        if (r.ok) {
          const d = await r.json();
          const safety = d.safety_snapshot
            ? this.t('admin.backups.restore_complete_safety', { name: d.safety_snapshot })
            : '';
          await Swal.fire({
            title: this.t('admin.backups.restore_complete_title'),
            html: this.t('admin.backups.restore_complete_html', {
              from: d.restored_from, count: d.avatar_count, safety,
            }) + this.t('admin.backups.restore_complete_signout'),
            icon: 'success',
          });
          await this.loadBackups();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.restore_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },
    async restoreBackupFromFile(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const res = await Swal.fire({
        title: this.t('admin.backups.restore_upload_title'),
        html: this.t('admin.backups.restore_upload_html', {
          name: file.name, size: (file.size / 1_000_000).toFixed(1),
        }),
        icon: 'warning', showCancelButton: true,
        confirmButtonText: this.t('admin.backups.restore_button'),
        cancelButtonText: this.t('actions.cancel'),
        confirmButtonColor: this._cssVar('--danger'),
      });
      if (!res.isConfirmed) { ev.target.value = ''; return; }
      try {
        const fd = new FormData();
        fd.append('file', file);
        const r = await fetch('/api/backups/restore', { method: 'POST', body: fd });
        if (r.ok) {
          const d = await r.json();
          const safety = d.safety_snapshot
            ? this.t('admin.backups.restore_complete_safety', { name: d.safety_snapshot })
            : '';
          await Swal.fire({
            title: this.t('admin.backups.restore_complete_title'),
            html: this.t('admin.backups.restore_complete_html', {
              from: d.restored_from, count: d.avatar_count, safety,
            }) + this.t('admin.backups.restore_complete_signout'),
            icon: 'success',
          });
          await this.loadBackups();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.restore_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
      finally { ev.target.value = ''; }
    },

    async loadUsers() {
      try {
        const r = await fetch('/api/users');
        if (!r.ok) return;
        const d = await r.json();
        this.users = d.users || [];
      } catch (_) {} finally {
        this.usersLoaded = true;
      }
    },

    async createUser() {
      const u = this.newUser;
      if (!u.username || !u.username.trim()) {
        this.showToast(this.t('toasts.username_required'), 'error');
        return;
      }
      if (u.auth_source === 'local' && (u.password || '').length < 8) {
        this.showToast(this.t('toasts.password_too_short'), 'error');
        return;
      }
      try {
        const r = await fetch('/api/users', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: u.username.trim(),
            role: u.role,
            auth_source: u.auth_source,
            password: u.auth_source === 'local' ? u.password : null,
            email: u.email || null,
          }),
        });
        if (r.ok) {
          this.showToast(this.t('toasts.user_created'));
          this.newUser = { username: '', role: 'readonly', auth_source: 'local', password: '', email: '' };
          await this.loadUsers();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.create_failed'), 'error');
        }
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      }
    },

    async patchUser(u, patch) {
      try {
        const r = await fetch('/api/users/' + u.id, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        });
        if (r.ok) { await this.loadUsers(); }
        else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.update_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    async toggleUserRole(u) {
      return this.patchUser(u, { role: u.role === 'admin' ? 'readonly' : 'admin' });
    },
    async toggleUserDisabled(u) {
      return this.patchUser(u, { disabled: !u.disabled });
    },

    async deleteUser(u) {
      const res = await Swal.fire({
        title: this.t('admin.backups.delete_user_title'),
        text: this.t('admin.backups.delete_user_confirm', { name: u.username }),
        icon: 'warning', showCancelButton: true,
        confirmButtonText: this.t('actions.delete'),
        cancelButtonText: this.t('actions.cancel'),
        confirmButtonColor: this._cssVar('--danger'),
      });
      if (!res.isConfirmed) return;
      try {
        const r = await fetch('/api/users/' + u.id, { method: 'DELETE' });
        if (r.ok) { this.showToast(this.t('toasts.user_deleted')); await this.loadUsers(); }
        else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.delete_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    async resetUserPassword(u) {
      if (u.auth_source !== 'local') {
        this.showToast(this.t('toasts.authentik_change_pw_here'), 'error');
        return;
      }
      const res = await Swal.fire({
        title: this.t('dialogs.reset_password_title', { name: u.username }),
        input: 'password', inputLabel: this.t('dialogs.reset_password_label'),
        inputAttributes: { minlength: 8, autocapitalize: 'off', autocorrect: 'off' },
        showCancelButton: true,
        confirmButtonText: this.t('dialogs.reset_button'),
        cancelButtonText: this.t('actions.cancel'),
      });
      if (!res.isConfirmed || !res.value) return;
      try {
        const r = await fetch('/api/users/' + u.id + '/reset-password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ new_password: res.value }),
        });
        if (r.ok) {
          this.showToast(this.t('toasts.password_reset'));
          // Server also clears any TOTP enrolment for the target. Refresh
          // the user list so the 2FA column reflects the new state.
          await this.loadUsers();
        }
        else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.reset_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    // ----------------------------------------------------------------
    // TOTP / 2FA management. Three call sites:
    //   - Profile (self): loadTotpStatus / startTotpEnrol / confirmTotpEnrol
    //                     / disableTotpSelf / regenerateTotpCodes
    //   - Admin -> Users: adminDisableTotp(u)
    //   - Login page does its own thing in /js/login.js
    // The /api/me/totp call returns plaintext backup codes; the
    // hide/unhide eye is purely client-side.
    // ----------------------------------------------------------------
    // ----------------------------------------------------------------
    // WebAuthn / passkeys. Mirror the TOTP UX shape: load on
    // page open, expose register/revoke from the Profile -> Security
    // card, surface count + browser-support hint in the SPA's state.
    // ----------------------------------------------------------------
    passkeyBrowserSupported() {
      return !!(window.PublicKeyCredential && navigator.credentials);
    },

    _b64uEncode(buf) {
      const b = new Uint8Array(buf);
      let s = '';
      for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
      return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    },

    _b64uDecode(s) {
      s = (s || '').replace(/-/g, '+').replace(/_/g, '/');
      while (s.length % 4) s += '=';
      const bin = atob(s);
      const out = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
      return out.buffer;
    },

    _passkeyOptionsForCreate(opts) {
      const out = Object.assign({}, opts);
      out.challenge = this._b64uDecode(opts.challenge);
      if (opts.user && opts.user.id) {
        out.user = Object.assign({}, opts.user);
        out.user.id = this._b64uDecode(opts.user.id);
      }
      if (Array.isArray(opts.excludeCredentials)) {
        out.excludeCredentials = opts.excludeCredentials.map(c => ({
          type: c.type,
          id: this._b64uDecode(c.id),
          transports: c.transports,
        }));
      }
      return out;
    },

    _passkeyAttestationResponse(cred) {
      return {
        id: cred.id,
        rawId: this._b64uEncode(cred.rawId),
        type: cred.type,
        authenticatorAttachment: cred.authenticatorAttachment || null,
        clientExtensionResults: cred.getClientExtensionResults
          ? cred.getClientExtensionResults() : {},
        response: {
          attestationObject: this._b64uEncode(cred.response.attestationObject),
          clientDataJSON:    this._b64uEncode(cred.response.clientDataJSON),
          transports: cred.response.getTransports
            ? cred.response.getTransports() : [],
        },
      };
    },

    async loadPasskeys() {
      try {
        const r = await fetch('/api/me/webauthn');
        if (!r.ok) {
          this.passkeys = { loaded: true, supported: false, list: [], busy: false, current_rp_id: '' };
          return;
        }
        const j = await r.json();
        this.passkeys = {
          loaded: true,
          supported: !!j.supported,
          list: Array.isArray(j.credentials) ? j.credentials : [],
          busy: false,
          // server's effective rp_id; Profile → Security uses
          // it as the comparison anchor for the orphaned-credential
          // badge. Lower-cased so the SPA's `pk.rp_id !== current`
          // check is stable when storage trimmed-and-lowered the
          // credential rp_id at registration but the request resolves
          // to a mixed-case Host header.
          current_rp_id: ((j.current_rp_id || '') + '').toLowerCase(),
        };
      } catch (_) {
        this.passkeys = { loaded: true, supported: false, list: [], busy: false, current_rp_id: '' };
      }
    },

    async addPasskey() {
      if (!this.passkeyBrowserSupported()) {
        this.showToast(this.t('toasts.passkey_browser_unsupported'), 'error');
        return;
      }
      if (!this.passkeys.supported) {
        this.showToast(this.t('toasts.passkey_server_unsupported'), 'error');
        return;
      }
      // Friendly-name first via SweetAlert so the user types it BEFORE
      // we touch the WebAuthn API. The server-side challenge mint +
      // navigator.credentials.create() then run back-to-back without
      // any further user interaction in between, which keeps the
      // user-gesture chain intact for password-manager extensions
      // (1Password / Bitwarden / iCloud Keychain) so they get a chance
      // to offer the "save passkey" sheet alongside the OS-native
      // picker.
      const nameRes = await Swal.fire({
        title: this.t('settings.profile.passkeys.name_prompt_title'),
        text: this.t('settings.profile.passkeys.name_prompt_body'),
        input: 'text',
        inputPlaceholder: this.t('settings.profile.passkeys.name_placeholder'),
        showCancelButton: true,
        confirmButtonText: this.t('settings.profile.passkeys.add_button'),
        cancelButtonText: this.t('actions.cancel'),
        inputValidator: (val) => {
          if (val && val.length > 64) return this.t('settings.profile.passkeys.name_prompt_body');
          return null;
        },
      });
      if (!nameRes.isConfirmed) return;
      const friendlyName = (nameRes.value || '').trim();
      this.passkeys.busy = true;
      try {
        const startResp = await fetch('/api/me/webauthn/register-start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        });
        if (!startResp.ok) {
          const j = await startResp.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.passkey_register_failed'), 'error');
          return;
        }
        const startJ = await startResp.json();
        const publicKey = this._passkeyOptionsForCreate(startJ.options);
        let cred;
        try {
          cred = await navigator.credentials.create({ publicKey });
        } catch (e) {
          // Surface the real error reason — silent toast made it
          // impossible to tell apart "user dismissed the picker",
          // "device declined", "extension blocked", "RP ID mismatch"
          //. DOMException.name is the canonical key
          // (NotAllowedError / SecurityError / InvalidStateError /
          // AbortError); fall through to the generic message when the
          // browser threw a non-DOMException.
          const detail = (e && (e.name || e.message)) || '';
          if (detail) {
            this.showToast(
              this.t('toasts.passkey_register_failed') + ' (' + detail + ')',
              'error',
            );
          } else {
            this.showToast(this.t('toasts.passkey_register_failed'), 'error');
          }
          // POST the failure to the server so it lands in Admin → Logs
          // alongside the matching register-start line. Fire-
          // and-forget — a logging endpoint shouldn't be able to
          // cascade into the user-visible flow.
          try {
            fetch('/api/me/webauthn/client-error', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                phase: 'register',
                error_name: (e && e.name) || '',
                error_message: (e && e.message) || '',
                rp_id: (publicKey && publicKey.rp && publicKey.rp.id) || '',
                origin: window.location.origin,
              }),
            }).catch(() => {});
          } catch (_) {}
          return;
        }
        if (!cred) {
          this.showToast(this.t('toasts.passkey_register_failed'), 'error');
          return;
        }
        const finishResp = await fetch('/api/me/webauthn/register-finish', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            credential: this._passkeyAttestationResponse(cred),
            friendly_name: friendlyName,
          }),
        });
        if (!finishResp.ok) {
          const j = await finishResp.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.passkey_register_failed'), 'error');
          return;
        }
        await this.loadPasskeys();
        if (this.me) this.me.passkeys = { ...(this.me.passkeys || {}),
          count: (this.passkeys.list || []).length,
          supported: true,
        };
        this.showToast(this.t('toasts.passkey_added'));
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      } finally {
        this.passkeys.busy = false;
      }
    },

    async revokePasskey(pk) {
      const res = await Swal.fire({
        title: this.t('settings.profile.passkeys.revoke_confirm_title'),
        text: this.t('settings.profile.passkeys.revoke_confirm_body', {
          name: pk.friendly_name || this.t('settings.profile.passkeys.name_default'),
        }),
        icon: 'warning', showCancelButton: true,
        confirmButtonText: this.t('settings.profile.passkeys.revoke_confirm_button'),
        cancelButtonText: this.t('actions.cancel'),
        confirmButtonColor: this._cssVar('--danger'),
      });
      if (!res.isConfirmed) return;
      this.passkeys.busy = true;
      try {
        const r = await fetch('/api/me/webauthn/' + encodeURIComponent(pk.id), {
          method: 'DELETE',
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.passkey_revoke_failed'), 'error');
          return;
        }
        await this.loadPasskeys();
        if (this.me) this.me.passkeys = { ...(this.me.passkeys || {}),
          count: (this.passkeys.list || []).length,
        };
        this.showToast(this.t('toasts.passkey_revoked'));
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      } finally {
        this.passkeys.busy = false;
      }
    },

    relativeWhen(epochSeconds) {
      if (!epochSeconds) return '';
      const now = Date.now() / 1000;
      const diff = Math.max(0, now - Number(epochSeconds));
      if (diff < 60) {
        const n = Math.round(diff);
        return this.t('hosts_extra.metrics.last_updated_seconds', { count: n }) || `${n}s ago`;
      }
      if (diff < 3600) {
        const n = Math.round(diff / 60);
        return this.t('hosts_extra.metrics.last_updated_minutes', { count: n }) || `${n}m ago`;
      }
      if (diff < 86400) {
        const n = Math.round(diff / 3600);
        return this.t('hosts_extra.metrics.last_updated_hours', { count: n }) || `${n}h ago`;
      }
      const d = new Date(Number(epochSeconds) * 1000);
      try { return d.toLocaleDateString(); } catch (_) { return d.toISOString().slice(0, 10); }
    },

    async loadTotpStatus() {
      try {
        const r = await fetch('/api/me/totp');
        if (!r.ok) {
          this.totp.loaded = true;
          return;
        }
        const j = await r.json();
        this.totp = {
          loaded: true,
          allowed: !!j.allowed,
          enabled: !!j.enabled,
          required: !!j.required,
          auth_source: j.auth_source || 'local',
          backup_codes: Array.isArray(j.backup_codes) ? j.backup_codes : [],
          policy: j.policy || {},
        };
      } catch (_) {
        this.totp.loaded = true;
      }
    },

    totpDisplayCode(c) {
      if (!c || !c.code) return '';
      if (this.totpCodesRevealed) return c.code;
      // Same character count + the space, masked.
      const len = String(c.code).replace(/\s/g, '').length;
      const half = Math.floor(len / 2);
      return ('•'.repeat(half)) + ' ' + ('•'.repeat(len - half));
    },

    async startTotpEnrol() {
      this.totpEnrolBusy = true;
      try {
        const r = await fetch('/api/me/totp/enroll-start', { method: 'POST' });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.totp_enroll_failed'), 'error');
          return;
        }
        const j = await r.json();
        this.totpEnrol = { secret: j.secret, uri: j.provisioning_uri, code: '' };
        this.totpEnrolStage = 'qr';
        // Defer to the next tick so the <div id="totp-enrol-qr"> exists.
        this.$nextTick(() => this._renderTotpQr());
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      } finally {
        this.totpEnrolBusy = false;
      }
    },

    _renderTotpQr() {
      const el = document.getElementById('totp-enrol-qr');
      if (!el) return;
      el.textContent = '';
      const uri = this.totpEnrol.uri;
      if (!uri) return;
      if (!window.qrcode) {
        const code = document.createElement('code');
        code.className = 'mono totp-qr-fallback';
        code.textContent = uri;
        el.appendChild(code);
        return;
      }
      try {
        const qr = window.qrcode(0, 'M');
        qr.addData(uri);
        qr.make();
        // parse the SVG via DOMParser + adopt its <svg> root
        // instead of `el.innerHTML = ...`. qrcode-generator's output
        // is trusted local lib data, but `innerHTML` is the
        // documented red-flag pattern for content-from-data flows;
        // matches how the rest of the SPA injects DOM.
        const svgText = qr.createSvgTag({ cellSize: 6, margin: 4, scalable: true });
        const parsed = new DOMParser().parseFromString(svgText, 'image/svg+xml');
        const root = parsed.documentElement;
        // DOMParser surfaces parse errors via a <parsererror> root —
        // fall back to the textContent-fallback below in that case
        // so the operator still sees the otpauth URI to type by hand.
        if (!root || root.tagName.toLowerCase() === 'parsererror') {
          throw new Error('SVG parse error');
        }
        el.replaceChildren(document.adoptNode(root));
      } catch (_) {
        const code = document.createElement('code');
        code.className = 'mono totp-qr-fallback';
        code.textContent = uri;
        el.appendChild(code);
      }
    },

    cancelTotpEnrol() {
      this.totpEnrol = { secret: '', uri: '', code: '' };
      this.totpEnrolStage = 'idle';
    },

    async confirmTotpEnrol() {
      const code = (this.totpEnrol.code || '').replace(/\s/g, '').trim();
      if (!/^\d{6}$/.test(code)) {
        this.showToast(this.t('toasts.totp_invalid_code'), 'error');
        return;
      }
      this.totpEnrolBusy = true;
      try {
        const r = await fetch('/api/me/totp/enroll-confirm', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ secret: this.totpEnrol.secret, code }),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.totp_enroll_failed'), 'error');
          return;
        }
        const j = await r.json();
        this.totpRevealCodes = j.backup_codes || [];
        this.totpEnrolStage = 'reveal';
        this.totpEnrol = { secret: '', uri: '', code: '' };
        await this.loadTotpStatus();
        if (this.me) this.me.totp = { ...(this.me.totp || {}), enabled: true };
        this.showToast(this.t('toasts.totp_enabled'));
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      } finally {
        this.totpEnrolBusy = false;
      }
    },

    closeTotpReveal() {
      this.totpRevealCodes = [];
      this.totpEnrolStage = 'idle';
    },

    downloadBackupCodes(codes) {
      const list = (codes && codes.length) ? codes : this.totpRevealCodes;
      if (!list || !list.length) return;
      const blob = new Blob([list.join('\n') + '\n'], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'omnigrid-backup-codes.txt';
      a.click();
      URL.revokeObjectURL(url);
    },

    async regenerateTotpCodes() {
      const res = await Swal.fire({
        title: this.t('settings.profile.totp.regen_confirm_title'),
        text: this.t('settings.profile.totp.regen_confirm_body'),
        icon: 'warning', showCancelButton: true,
        confirmButtonText: this.t('settings.profile.totp.regen_confirm_button'),
        cancelButtonText: this.t('actions.cancel'),
      });
      if (!res.isConfirmed) return;
      try {
        const r = await fetch('/api/me/totp/regenerate-codes', { method: 'POST' });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.totp_regen_failed'), 'error');
          return;
        }
        const j = await r.json();
        this.totpRevealCodes = j.backup_codes || [];
        this.totpEnrolStage = 'reveal';
        await this.loadTotpStatus();
        this.showToast(this.t('toasts.totp_regen_ok'));
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      }
    },

    async disableTotpSelf() {
      if (this.totp.required) {
        this.showToast(this.t('toasts.totp_required_no_disable'), 'error');
        return;
      }
      const res = await Swal.fire({
        title: this.t('settings.profile.totp.disable_confirm_title'),
        text: this.t('settings.profile.totp.disable_confirm_body'),
        icon: 'warning',
        input: 'password',
        inputLabel: this.t('settings.profile.totp.disable_password_label'),
        inputAttributes: { autocapitalize: 'off', autocorrect: 'off' },
        showCancelButton: true,
        confirmButtonText: this.t('settings.profile.totp.disable_confirm_button'),
        cancelButtonText: this.t('actions.cancel'),
      });
      if (!res.isConfirmed || !res.value) return;
      this.totpDisableBusy = true;
      try {
        const r = await fetch('/api/me/totp/disable', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: res.value }),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.totp_disable_failed'), 'error');
          return;
        }
        await this.loadTotpStatus();
        if (this.me) this.me.totp = { ...(this.me.totp || {}), enabled: false };
        this.showToast(this.t('toasts.totp_disabled'));
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      } finally {
        this.totpDisableBusy = false;
      }
    },

    async adminDisableTotp(u) {
      if (u.auth_source !== 'local') {
        this.showToast(this.t('toasts.authentik_change_pw_here'), 'error');
        return;
      }
      const res = await Swal.fire({
        title: this.t('admin.users.totp_disable_confirm_title', { name: u.username }),
        text: this.t('admin.users.totp_disable_confirm_body'),
        icon: 'warning', showCancelButton: true,
        confirmButtonText: this.t('admin.users.totp_disable_confirm_button'),
        cancelButtonText: this.t('actions.cancel'),
        confirmButtonColor: this._cssVar('--danger'),
      });
      if (!res.isConfirmed) return;
      try {
        const r = await fetch('/api/users/' + u.id + '/disable-totp', {
          method: 'POST',
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.totp_disable_failed'), 'error');
          return;
        }
        this.showToast(this.t('toasts.totp_admin_disabled'));
        await this.loadUsers();
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      }
    },

    // Per-user force-2FA toggle. Admin flips this flag to make
    // a specific user MUST have 2FA on regardless of the global
    // role-policy. Forcing on a user who hasn't enrolled yet causes
    // their next login to land in the forced-enrolment QR flow.
    async adminForceTotp(u, force) {
      if (u.auth_source !== 'local') {
        this.showToast(this.t('toasts.authentik_change_pw_here'), 'error');
        return;
      }
      const titleKey = force
        ? 'admin.users.totp_force_confirm_title'
        : 'admin.users.totp_unforce_confirm_title';
      const bodyKey = force
        ? 'admin.users.totp_force_confirm_body'
        : 'admin.users.totp_unforce_confirm_body';
      const res = await Swal.fire({
        title: this.t(titleKey, { name: u.username }),
        text: this.t(bodyKey),
        icon: 'question', showCancelButton: true,
        confirmButtonText: this.t('actions.confirm'),
        cancelButtonText: this.t('actions.cancel'),
        confirmButtonColor: this._cssVar('--primary'),
      });
      if (!res.isConfirmed) return;
      try {
        const r = await fetch('/api/users/' + u.id + '/totp-force', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ force: !!force }),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.totp_force_failed'), 'error');
          return;
        }
        this.showToast(this.t(force ? 'toasts.totp_forced_on' : 'toasts.totp_force_cleared'));
        await this.loadUsers();
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      }
    },

    async loadSessions() {
      try {
        const r = await fetch('/api/sessions');
        if (!r.ok) return;
        const d = await r.json();
        this.sessions = d.sessions || [];
      } catch (_) {} finally {
        this.sessionsLoaded = true;
      }
    },

    async revokeSession(s) {
      const res = await Swal.fire({
        title: this.t('admin.backups.revoke_session_title'),
        text: this.t('admin.backups.revoke_session_text', { name: s.username || this.t('admin.backups.revoke_session_default') }),
        icon: 'warning', showCancelButton: true,
        confirmButtonText: this.t('admin.sessions.revoke'),
        cancelButtonText: this.t('actions.cancel'),
      });
      if (!res.isConfirmed) return;
      try {
        const r = await fetch('/api/sessions/' + encodeURIComponent(s.token_id), { method: 'DELETE' });
        if (r.ok) { this.showToast(this.t('toasts.session_revoked')); await this.loadSessions(); }
        else this.showToast(this.t('toasts.revoke_failed'), 'error');
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    async loadTokens() {
      try {
        const r = await fetch('/api/tokens');
        if (!r.ok) return;
        const d = await r.json();
        this.tokens = d.tokens || [];
      } catch (_) {} finally {
        this.tokensLoaded = true;
      }
    },

    async createToken() {
      const t = this.newToken;
      if (!t.name || !t.name.trim()) { this.showToast(this.t('toasts.name_required'), 'error'); return; }
      try {
        const r = await fetch('/api/tokens', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: t.name.trim(), role: t.role }),
        });
        if (r.ok) {
          const d = await r.json();
          // Raw token is shown ONCE — surface it in a one-time modal.
          this.lastCreatedToken = d;
          this.newToken = { name: '', role: 'readonly' };
          await this.loadTokens();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.create_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    async deleteToken(tk) {
      const res = await Swal.fire({
        title: this.t('admin.backups.revoke_token_title'),
        text: this.t('admin.backups.revoke_token_text', { name: tk.name }),
        icon: 'warning', showCancelButton: true,
        confirmButtonText: this.t('admin.tokens.revoke'),
        cancelButtonText: this.t('actions.cancel'),
      });
      if (!res.isConfirmed) return;
      try {
        const r = await fetch('/api/tokens/' + tk.id, { method: 'DELETE' });
        if (r.ok) { this.showToast(this.t('toasts.token_revoked')); await this.loadTokens(); }
        else this.showToast(this.t('toasts.revoke_failed'), 'error');
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    copyToClipboard(text) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
          () => this.showToast(this.t('toasts.copied')),
          () => this.showToast(this.t('toasts.copy_failed_manual'), 'error'),
        );
      }
    },

    // --- Topbar clock + weather ---
    tickHeaderClock() {
      const now = new Date();
      // Browser locale owns the formatting — gives us 12h/24h + AM/PM
      // automatically based on the user's OS setting.
      this.currentClock = now.toLocaleTimeString([], {
        hour: 'numeric', minute: '2-digit',
      });
    },
    startHeaderClock() {
      if (this._clockTimer) return;
      this.tickHeaderClock();
      // 10s cadence — granular enough to keep minutes synced without
      // hammering the render loop.
      this._clockTimer = setInterval(() => this.tickHeaderClock(), 10000);
    },
    async loadHeaderWeather() {
      if (!this.headerWeatherEnabled
          || this.headerWeatherLat == null
          || this.headerWeatherLon == null) {
        this.weather = null;
        return;
      }
      try {
        const p = new URLSearchParams({
          lat:   String(this.headerWeatherLat),
          lon:   String(this.headerWeatherLon),
          label: this.headerWeatherLabel || '',
        });
        const r = await fetch('/api/weather?' + p.toString());
        if (!r.ok) { this.weather = null; return; }
        this.weather = await r.json();
      } catch (_) {
        this.weather = null;
      }
    },
    startHeaderWeather() {
      if (this._weatherTimer) return;
      this.loadHeaderWeather();
      // 10 min cadence — backend already caches 10 min per coord, so
      // this matches the server-side TTL. Even if the operator has ten
      // tabs open they hit the cache after the first.
      this._weatherTimer = setInterval(() => this.loadHeaderWeather(), 600000);
    },
    // Snapshot of the topbar-widget prefs — same baseline+snapshot
    // pattern as #305's admin-tab dirty flags. Toggling the show-clock
    // / show-weather checkboxes or editing the lat/lon/label fields
    // only marks the form dirty; nothing persists until Save is
    // clicked. `headerPrefsDirty()` re-evaluates each render via
    // Alpine reactivity. Re-baselined after every successful save.
    _headerPrefsBaseline: '',
    _headerPrefsSnapshot() {
      return JSON.stringify({
        clk:   !!this.headerClockEnabled,
        wth:   !!this.headerWeatherEnabled,
        lat:   this.headerWeatherLat == null ? '' : String(this.headerWeatherLat),
        lon:   this.headerWeatherLon == null ? '' : String(this.headerWeatherLon),
        label: this.headerWeatherLabel || '',
      });
    },
    headerPrefsDirty() {
      return this._headerPrefsBaseline !== this._headerPrefsSnapshot();
    },
    saveHeaderPrefs() {
      try {
        localStorage.setItem('headerClockEnabled',   String(!!this.headerClockEnabled));
        localStorage.setItem('headerWeatherEnabled', String(!!this.headerWeatherEnabled));
        localStorage.setItem('headerWeatherLat',     this.headerWeatherLat == null ? '' : String(this.headerWeatherLat));
        localStorage.setItem('headerWeatherLon',     this.headerWeatherLon == null ? '' : String(this.headerWeatherLon));
        localStorage.setItem('headerWeatherLabel',   this.headerWeatherLabel || '');
      } catch (_) {}
      // Re-baseline after the localStorage write so headerPrefsDirty()
      // returns false on the next render. The server PATCH below is
      // fire-and-forget — its failure shouldn't keep the form marked
      // dirty (operator still saved locally; the cross-device sync
      // can retry on the next save).
      this._headerPrefsBaseline = this._headerPrefsSnapshot();
      // also push to server-side per-user prefs so the same
      // toggles persist cross-device for the same login. Fire-and-
      // forget; localStorage stays the fast path on subsequent loads,
      // but /api/me's ui_prefs is the cross-device source of truth
      // and overrides localStorage on next page load (see init()).
      try {
        fetch('/api/me/ui-prefs', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prefs: {
            headerClockEnabled:   !!this.headerClockEnabled,
            headerWeatherEnabled: !!this.headerWeatherEnabled,
            headerWeatherLat:     this.headerWeatherLat == null ? null : Number(this.headerWeatherLat),
            headerWeatherLon:     this.headerWeatherLon == null ? null : Number(this.headerWeatherLon),
            headerWeatherLabel:   this.headerWeatherLabel || null,
          }}),
        }).catch(() => {/* silent — localStorage still has it */});
      } catch (_) {}
      // Re-fetch with the new settings immediately rather than waiting
      // for the 10-min tick. Also flushes weather to null when disabled.
      this.loadHeaderWeather();
      // Toast confirmation — per-browser preferences auto-save on
      // change, but operators coming from the per-user Profile section
      // expect a visual "saved" signal.
      if (this.showToast) this.showToast(this.t('toasts_extra.topbar_saved'), 'success');
    },
    // apply server-side ui_prefs onto local state. Called from
    // init() right after /api/me lands. Server is the cross-device
    // source of truth; localStorage is the fast-path cache that gets
    // overwritten when the server has a non-empty pref.
    applyServerUiPrefs() {
      const p = (this.me && this.me.ui_prefs) || {};
      // Only override when the server has an explicit value. An
      // empty {} means "user never saved anything" → leave the
      // localStorage default in place.
      if (typeof p.headerClockEnabled === 'boolean') {
        this.headerClockEnabled = p.headerClockEnabled;
        try { localStorage.setItem('headerClockEnabled', String(p.headerClockEnabled)); } catch (_) {}
      }
      if (typeof p.headerWeatherEnabled === 'boolean') {
        this.headerWeatherEnabled = p.headerWeatherEnabled;
        try { localStorage.setItem('headerWeatherEnabled', String(p.headerWeatherEnabled)); } catch (_) {}
      }
      if (p.headerWeatherLat != null) {
        this.headerWeatherLat = Number(p.headerWeatherLat);
        try { localStorage.setItem('headerWeatherLat', String(p.headerWeatherLat)); } catch (_) {}
      }
      if (p.headerWeatherLon != null) {
        this.headerWeatherLon = Number(p.headerWeatherLon);
        try { localStorage.setItem('headerWeatherLon', String(p.headerWeatherLon)); } catch (_) {}
      }
      if (typeof p.headerWeatherLabel === 'string') {
        this.headerWeatherLabel = p.headerWeatherLabel;
        try { localStorage.setItem('headerWeatherLabel', p.headerWeatherLabel); } catch (_) {}
      }
    },
    // Inline SVG path(s) per WMO-icon slug. Kept tiny — the topbar chip
    // is 16px so detail is wasted. Backend maps WMO codes to slugs in
    // main.py:_WMO_CODES so the mapping has ONE source of truth.
    weatherIconPath(slug) {
      const icons = {
        'sun':       '<circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
        'cloud-sun': '<circle cx="7" cy="8" r="3"/><path d="M7 2v2M2 8h2M12 8h-.5M4 4l1 1M10 4L9 5"/><path d="M20 17h-10.5a3.5 3.5 0 1 1 .8-6.9"/>',
        'cloud':     '<path d="M17 18H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 18z"/>',
        'fog':       '<path d="M3 9h18M3 13h18M3 17h12M7 5h14"/>',
        'drizzle':   '<path d="M17 14H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 14z"/><path d="M9 18v2M13 18v2M17 18v2"/>',
        'rain':      '<path d="M17 14H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 14z"/><path d="M8 16v4M12 18v4M16 16v4"/>',
        'snow':      '<path d="M17 14H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 14z"/><path d="M8 17v1M12 19v1M16 17v1M8 20v1M12 22v.01M16 20v1"/>',
        'sleet':     '<path d="M17 14H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 14z"/><path d="M9 17v2M13 19v2M17 17v2M10 20h.01M14 18h.01"/>',
        'thunder':   '<path d="M19 16a5 5 0 0 0-1-9h-1.3a7 7 0 0 0-13.4 2"/><polyline points="13 11 9 17 14 17 10 22"/>',
      };
      return icons[slug] || icons['cloud'];
    },
    async loadVersion() {
      try {
        const r = await fetch('/api/version');
        if (!r.ok) return;
        const d = await r.json();
        this.version = d.version || '';
        // First successful fetch — lock in the boot version so the
        // watcher has something to compare against. Later fetches
        // don't overwrite bootVersion; we want the original forever.
        if (!this.bootVersion && this.version) {
          this.bootVersion = this.version;
        } else if (this.bootVersion && this.version && this.version !== this.bootVersion) {
          // New build is live. Flag once; further ticks are no-ops.
          if (!this.newVersionAvailable) {
            this.newVersionAvailable = true;
            this.newVersionString = this.version;
          }
        }
      } catch (e) {}
    },
    // Start a 60s poll of /api/version so a deploy that lands while
    // the operator has the tab open triggers a hard-refresh banner.
    // Idempotent — safe to call from init() even on hot-reload.
    startVersionWatcher() {
      if (this._versionTimer) return;
      this._versionTimer = setInterval(() => this.loadVersion(), 60000);
    },
    // Force a cache-busting reload when the operator clicks the
    // "New version — reload" banner. `location.reload()` alone
    // sometimes serves the cached HTML; adding a query param forces
    // the server to re-send (and the JS/CSS assets use
    // ?v=<version> bust-tokens so they'll re-fetch too).
    // URLSearchParams.set replaces any existing `_v` so consecutive
    // reloads don't append `&_v=...&_v=...&_v=...`.
    reloadForNewVersion() {
      const params = new URLSearchParams(location.search);
      params.set('_v', this.newVersionString || String(Date.now()));
      const qs = params.toString();
      location.href = location.pathname + (qs ? '?' + qs : '') + location.hash;
    },

    // Console-pasteable snapshot of stats state — operators paste
    //   app().statsDebug()
    // to dump current items / stats / sparks counts in one go,
    // without hunting through the Console for warnings or repeating
    // grep-style queries against the SQLite. Returns the snapshot
    // object (also console.tabled for readability).
    statsDebug() {
      const items = this.items || [];
      const stats = this.stats || {};
      const sparks = this.sparks || {};
      const itemIds = items.map(i => i.id);
      const withStats = itemIds.filter(id => stats[id] && stats[id].has_stats).length;
      const sparkIds = Object.keys(sparks);
      const withSparks = sparkIds.filter(id => Array.isArray(sparks[id]) && sparks[id].length > 0).length;
      const sample = items.slice(0, 3).map(i => ({
        id: i.id, name: i.name, type: i.type, status: i.status,
        has_stats: !!(stats[i.id] && stats[i.id].has_stats),
        cpu: stats[i.id] && stats[i.id].cpu_percent,
        mem_used: stats[i.id] && stats[i.id].mem_usage,
        spark_points: (sparks[i.id] || []).length,
      }));
      const snap = {
        items_total: items.length,
        stats_keys: Object.keys(stats).length,
        items_with_stats_true: withStats,
        sparks_keys: sparkIds.length,
        items_with_sparks: withSparks,
        first_three_items: sample,
      };
      console.table(snap);
      console.log('[statsDebug]', snap);
      return snap;
    },
    // Operator-callable: walks every visible item and reports WHY each
    // one is rendering blank/empty bars. Prints a per-item table to the
    // console with the diagnosis in plain English. Run from DevTools as
    // `omnigrid.whyNoGraphs()` after the page settles.
    whyNoGraphs() {
      const items = this.items || [];
      const rows = [];
      let okCount = 0, missingFromStats = 0, statsButNoFlag = 0,
          fallbackOnly = 0, sparkOnly = 0;
      for (const i of items) {
        const id = i.id;
        const s = this.stats[id];
        const sp = this.sparks[id];
        const sparkCount = Array.isArray(sp) ? sp.length : 0;
        let diagnosis = '';
        if (!s) {
          diagnosis = 'no entry in this.stats — /api/stats did not include this id';
          missingFromStats++;
        } else if (s._stale && (!s.ts || s.ts === 0)) {
          diagnosis = 'fallback-only (cache seed from stats_samples; live gather has not run yet)';
          fallbackOnly++;
        } else if (!s.has_stats && !s.has_size) {
          diagnosis = 'has_stats=false AND has_size=false (Portainer per-container /stats fetch failed for this container)';
          statsButNoFlag++;
        } else if (!s.has_stats && s.has_size) {
          diagnosis = 'has_stats=false but has_size=true (container offline; size came from inspect)';
          statsButNoFlag++;
        } else {
          diagnosis = 'OK — has_stats=true (bar should render)';
          okCount++;
        }
        if (sparkCount === 0 && diagnosis.startsWith('OK')) diagnosis += ' but spark line empty';
        else if (sparkCount > 0 && !s) { diagnosis += ' (sparks alone — bar fallback to 0)'; sparkOnly++; }
        rows.push({
          id, name: i.name, type: i.type, status: i.status,
          stats_entry: !!s,
          has_stats: !!(s && s.has_stats),
          has_size:  !!(s && s.has_size),
          stale:     !!(s && s._stale),
          cpu:       s && s.cpu_percent,
          mem_used:  s && s.mem_usage,
          mem_limit: s && s.mem_limit,
          spark_pts: sparkCount,
          diagnosis,
        });
      }
      const summary = {
        items_total: items.length,
        ok: okCount,
        missing_from_stats: missingFromStats,
        stats_entry_but_no_data: statsButNoFlag,
        fallback_only: fallbackOnly,
        spark_only: sparkOnly,
      };
      console.log('[whyNoGraphs] SUMMARY', summary);
      console.table(rows);
      // Actionable next-step hints based on the dominant failure mode.
      if (missingFromStats === items.length && items.length > 0) {
        console.warn('[whyNoGraphs] EVERY item is missing from this.stats. Either /api/stats returned an empty {stats:{}} OR loadStats() never fired. Check Network tab for /api/stats response body.');
      } else if (statsButNoFlag > okCount) {
        console.warn('[whyNoGraphs] Most entries have has_stats=false. Portainer per-container /stats is failing — check API key scope / agent reachability on the deploy host.');
      } else if (fallbackOnly > okCount) {
        console.warn('[whyNoGraphs] Most entries are fallback-only (seeded from stats_samples, no live gather). _gather_stats() has not yet run since boot. Wait ~30s and retry.');
      } else if (okCount === items.length && items.length > 0) {
        console.log('[whyNoGraphs] All items have valid live stats. If bars still look blank, the issue is template/CSS — inspect the rendered DOM for .stat-bar elements and their --w / --c CSS variables.');
      }
      return summary;
    },
    async loadStats(force=false) {
      try {
        const r = await fetch('/api/stats' + (force ? '?force=true' : ''));
        if (!r.ok) {
          // Surface the failure in the console once per load so an
          // operator can spot a 401 / 502 / 504 without hand-checking
          // the Network tab. Stats bars stay at `—` silently otherwise.
          console.warn('[stats] /api/stats returned', r.status);
          return;
        }
        const d = await r.json();
        this.stats = d.stats || {};
        // Background-refresh indicator. /api/stats now serves the
        // seeded cache instantly + kicks `_gather_stats` in the
        // background; ``stats_refreshing`` is true while the
        // background gather is in flight. Composes with the existing
        // `cacheRefreshing` / `hubProbing` flags so the topbar
        // refresh button pulses the spinner whenever ANY background
        // refresh is running. Auto-clears on the next poll once the
        // background gather lands.
        this.statsRefreshing = !!d.stats_refreshing;
        // Swarm agent unhealthy detection — populated by gather_stats
        // when a Swarm node has consecutive bad gather cycles (every
        // task-derived cid on the node returned None). Empty array on
        // healthy fleet (most common case). The SPA banner in Stacks
        // / Hosts views renders when the array is non-empty.
        this.unhealthyAgents = Array.isArray(d.unhealthy_agents) ? d.unhealthy_agents : [];
        // Self-diagnostic — fires when /api/stats came back with
        // ZERO has_stats=true rows AND we have items loaded. That's
        // the signature of "Portainer's per-container /stats endpoint
        // failed for every running container" (most common root
        // cause: API key with restricted scope, Portainer node-agent
        // unreachable, or a network policy blocking the docker-stats
        // RPC). Logged once per noticeably-empty response, NOT every
        // poll, so the console doesn't drown in repetition.
        const ids = Object.keys(this.stats);
        const withStats = ids.filter(id => this.stats[id] && this.stats[id].has_stats).length;
        // Flips true on the FIRST successful `/api/stats` response so
        // the Stacks / Services rows can swap their loading spinner
        // for the resolved status dot. Stays true for the rest of the
        // session — a transient `/api/stats` failure later doesn't
        // re-show the spinner because the existing data is still
        // authoritative.
        this.statsLoaded = true;
        if ((this.items || []).length > 0 && ids.length > 0 && withStats === 0) {
          if (!this._warnedNoStats) {
            this._warnedNoStats = true;
            console.warn(
              '[stats] /api/stats returned ' + ids.length + ' items but ' +
              'has_stats=true on 0 of them. Per-container Docker stats are ' +
              'missing — check Portainer API-key scope / agent reachability. ' +
              'On the deploy host: tail -F the omnigrid container logs and ' +
              'look for `[stats] <cid>: ...` error lines, or hit ' +
              '/api/endpoints/<eid>/docker/containers/<cid>/stats?stream=false ' +
              'directly via curl with the same API key.'
            );
          }
        } else if (withStats > 0) {
          // Live data flowing — clear the once-per-session guard so a
          // future regression re-warns instead of staying silent.
          this._warnedNoStats = false;
        }
        // Compute max image size across all items so the disk bar is
        // normalised against the largest thing on the cluster.
        let m = 1;
        for (const id in this.stats) {
          if (this.stats[id].size_root > m) m = this.stats[id].size_root;
        }
        this._maxSize = m;
      } catch (e) {
        console.warn('[stats] /api/stats fetch failed:', e && e.message);
      }
    },
    pollStats() {
      if (this._statsTimer) {
        clearTimeout(this._statsTimer);
        this._statsTimer = null;
      }
      // Interval of 0 → operator explicitly turned stats polling off.
      // Skip the initial tick entirely (used to fire one extra
      // /api/stats after "Off" was selected) AND don't schedule
      // further ticks. Other call sites like refresh() can still
      // fetch stats on demand; we just stop the periodic timer.
      if (!(this.statsInterval > 0)) {
        // Diagnostic — without this, "no graphs anywhere" looks like
        // a backend bug when the actual cause is that the operator's
        // last session left the stats-interval picker on "Off". One
        // shot still fires below so the operator sees CURRENT data;
        // future ticks are intentionally suppressed.
        console.warn('[stats] pollStats: stats polling is OFF (statsInterval=0). Re-enable it from the topbar interval picker. Firing one diagnostic loadStats() anyway so /api/stats response is logged once.');
        try { this.loadStats(); } catch (_) { /* never crash init */ }
        return;
      }
      const tick = async () => {
        // Bracket the request so the pill flashes green for the
        // exact duration of the /api/stats round-trip (#486 flash).
        this._pollStart();
        try { await this.loadStats(); }
        finally { this._pollEnd(); }
        if (this.statsInterval > 0) {
          // when SSE is healthy the backend pushes a
          // ``stats:refreshed`` hint after every gather_stats() write;
          // the listener kicks loadStats() then. The fallback timer
          // fires only every 5 minutes as a safety net for the case
          // where the live stream is silently broken AND the
          // freshness watchdog hasn't yet flipped _sseConnected
          // back to false.
          const intervalMs = this._sseConnected
            ? Math.max(this.statsInterval * 1000, 5 * 60 * 1000)
            : this.statsInterval * 1000;
          if (this._sseConnected && !this._statsLiveLogged) {
            console.log('[live] pollStats cadence: SSE up → ' + Math.round(intervalMs / 1000) + 's safety net (push-driven via stats:refreshed)');
            this._statsLiveLogged = true;
          } else if (!this._sseConnected && this._statsLiveLogged) {
            console.log('[live] pollStats cadence: SSE down → ' + this.statsInterval + 's polling');
            this._statsLiveLogged = false;
          }
          this._statsTimer = setTimeout(tick, intervalMs);
        } else {
          this._statsTimer = null;
        }
      };
      tick();
    },
    setStatsInterval(seconds) {
      this.statsInterval = seconds;
      localStorage.setItem('statsInterval', String(seconds));
      // Clear any scheduled tick explicitly when going to 0 —
      // pollStats() returns early but we need to kill an in-flight
      // setTimeout that was scheduled before the switch.
      if (this._statsTimer) {
        clearTimeout(this._statsTimer);
        this._statsTimer = null;
      }
      // Statss interval is the master "live updates" switch — when
      // the operator picks Off (0), kill the hosts-poll timer too
      // (its 15s rebuild causes the row's loading-spinner flash).
      // When they re-enable, restart it for the hosts view.
      if (seconds > 0) {
        if (this.view === 'hosts' && !this._hostsTimer) {
          this._hostsTimer = setInterval(() => {
            this._pollWrap(this.loadHosts());
          }, this.statsInterval * 1000);
        }
      } else if (this._hostsTimer) {
        clearInterval(this._hostsTimer);
        this._hostsTimer = null;
      }
      this.pollStats();
    },

    // --- Sparklines ----------------------------------------------------
    // Fetched from /api/stats/history in one batched request for every
    // currently-known item id. The backend samples every 5 minutes (see
    // STATS_SAMPLE_INTERVAL in main.py), so polling more often than that
    // is wasted work — we refresh on a 5-minute cadence.
    async loadSparks() {
      const ids = (this.items || []).map(i => i.id).filter(Boolean);
      if (!ids.length) return;
      try {
        const params = new URLSearchParams({ item_id: ids.join(','), hours: '24' });
        const r = await fetch('/api/stats/history?' + params.toString());
        if (!r.ok) {
          console.warn('[sparks] /api/stats/history returned', r.status);
          return;
        }
        const d = await r.json();
        this.sparks = d.series || {};
        // Self-diagnostic — fires once when sparks come back empty
        // AND we have items. The most common cause is item-id drift
        // (stats_samples table has rows under historical container/
        // service IDs that don't match current cache item_ids — e.g.
        // after a Portainer migration or a stack redeploy that
        // recreated container IDs). Operator can confirm by running
        // sqlite3 on the DB: items with samples → SELECT DISTINCT
        // item_id FROM stats_samples vs current item_ids.
        const sparkIds = Object.keys(this.sparks);
        const withData = sparkIds.filter(id => Array.isArray(this.sparks[id]) && this.sparks[id].length > 0).length;
        if (ids.length > 0 && withData === 0) {
          if (!this._warnedNoSparks) {
            this._warnedNoSparks = true;
            console.warn(
              '[sparks] /api/stats/history returned ' + sparkIds.length +
              ' keys, ' + withData + ' with data, for ' + ids.length + ' current items. ' +
              'If 0/N, the stats_samples table holds data for OLD item_ids that ' +
              'no longer match current items. Sample current id: ' + ids[0] +
              '. Check: sqlite3 .../omnigrid.db "SELECT DISTINCT item_id FROM stats_samples LIMIT 5".'
            );
          }
        } else if (withData > 0) {
          this._warnedNoSparks = false;
        }
      } catch (e) {
        console.warn('[sparks] /api/stats/history fetch failed:', e && e.message);
      }
    },
    pollSparks() {
      if (this._sparksTimer) clearInterval(this._sparksTimer);
      // Off mode kills the sparks timer too. The picker's
      // "static snapshot" promise must hold for sparklines as well as
      // every other chart. Live and interval modes stay at the 5min
      // baseline because sparklines are coarse 24h aggregates that
      // barely change tick-to-tick — even with picker=30s, polling
      // sparks every 30s would be wasted bandwidth without visible
      // benefit. The first one-shot loadSparks() also gates on Off
      // so an Off-on-boot doesn't fetch once and freeze.
      if (this.refreshInterval === 0) {
        return;
      }
      this.loadSparks();
      this._sparksTimer = setInterval(() => this.loadSparks(), 5 * 60 * 1000);
    },
    // Build an SVG polyline `points` attribute for one metric of one item.
    // Returns '' when we don't have enough data yet — the caller hides the
    // element with x-show so new installs don't render empty rectangles.
    sparkPoints(item, key) {
      const rows = this.sparks[item && item.id];
      if (!rows || rows.length < 2) return '';
      const W = 60, H = 10;
      const vals = rows.map(r => {
        if (key === 'cpu') return r.cpu || 0;
        if (key === 'mem') return r.mem_limit ? (r.mem_used / r.mem_limit) * 100 : 0;
        return 0;
      });
      let lo = Infinity, hi = -Infinity;
      for (const v of vals) { if (v < lo) lo = v; if (v > hi) hi = v; }
      // Keep the sparkline visually centred when the signal is flat.
      if (hi - lo < 0.5) { lo = Math.max(0, lo - 0.5); hi = lo + 1; }
      const step = W / (vals.length - 1);
      return vals.map((v, i) => {
        const x = (i * step).toFixed(1);
        const y = (H - ((v - lo) / (hi - lo)) * H).toFixed(1);
        return `${x},${y}`;
      }).join(' ');
    },
    sparkClass(item, key) {
      // Colour follows the CURRENT reading (not the sparkline max) so the
      // line visually agrees with the stat-bar it sits beside.
      const s = this.statsFor(item);
      if (!s || !s.has_stats) return 'muted';
      const v = key === 'cpu' ? s.cpu_percent : this.memPercent(item);
      return this.barLevel(v);
    },

    // Generic in-place reconcile helper. Updates `target` to
    // match `incoming` row-by-row, keyed on the named field
    // (default `id`; stacks pass `'name'` since they don't carry an
    // id). Operations:
    //   - existing rows: copy every field from `incoming[i]` onto the
    //     proxied entry (Alpine tracks each assignment individually so
    //     the row's DOM stays mounted),
    //   - new rows: push the full incoming dict at the end,
    //   - gone rows: splice from the tail.
    // The order of `target` is finally rewritten to match `incoming`'s
    // order via swap-in-place so the row sequence the operator sees
    // tracks server-side ordering without tearing the array down.
    // Used by `refresh()` for `this.items` (key=id) and `this.stacks`
    // (key=name); matches the reconcile pattern `loadHosts` uses for
    // `this.hosts`.
    _reconcileById(target, incoming, keyField = 'id') {
      const keyOf = (r) => (r && r[keyField] != null) ? r[keyField] : null;
      const incomingKeys = new Set(incoming.map(keyOf).filter(k => k != null));
      // Drop rows whose key disappeared. Iterate from the tail so
      // splice indices stay valid.
      for (let i = target.length - 1; i >= 0; i--) {
        if (!incomingKeys.has(keyOf(target[i]))) {
          target.splice(i, 1);
        }
      }
      // Update / insert. After this loop `target` has the right rows
      // but possibly in the wrong order.
      const byKey = new Map();
      for (const row of target) {
        const k = keyOf(row);
        if (k != null) byKey.set(k, row);
      }
      for (const inc of incoming) {
        const k = keyOf(inc);
        if (k == null) continue;
        const existing = byKey.get(k);
        if (existing) {
          for (const f of Object.keys(inc)) existing[f] = inc[f];
        } else {
          target.push({ ...inc });
          byKey.set(k, target[target.length - 1]);
        }
      }
      // Reorder `target` to match `incoming`'s sequence. Build the
      // final order array and copy elements back into `target` in
      // place — avoids a full reassignment that would re-proxy the
      // array and tear down Alpine's row mounts.
      const ordered = incoming.map(inc => byKey.get(keyOf(inc))).filter(Boolean);
      for (let i = 0; i < ordered.length; i++) {
        if (target[i] !== ordered[i]) {
          target[i] = ordered[i];
        }
      }
      // Trim any trailing slots if the new array is shorter.
      while (target.length > ordered.length) target.pop();
    },

    async refresh(force=false) {
      this.loading = true;
      try {
        const r = await fetch('/api/items' + (force ? '?force=true' : ''));
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        // in-place reconcile for items + stacks instead of
        // wholesale array reassignment. Keeps Alpine from tearing
        // down each row's checkbox state, <details> open/closed
        // state, and inline-style nodes on every poll. Items are
        // keyed by `id` (the default); stacks have no id field so
        // we key on `name` (the operator-facing stack name is
        // unique within a Swarm).
        this._reconcileById(this.items, d.items || []);
        this._reconcileById(this.stacks, d.stacks || [], 'name');
        this.nodes = d.nodes || {};
        // Per-node capacity + uptime proxy — see logic/gather.py's nodes_info.
        // Drives the Nodes view's normalized CPU/mem bars.
        this.nodesInfo = d.nodes_info || {};
        // UX-003: drives the "Portainer not configured" empty-state hint.
        // null → not yet known (skeleton state); true/false → explicit.
        if (typeof d.portainer_configured === 'boolean') {
          this.portainerConfigured = d.portainer_configured;
        }
        // Cache state is implementation detail — the unified refresh
        // picker is the operator's mental model for "how live
        // is this dashboard". Cleared rather than deleted so any
        // remaining bindings render empty instead of crashing.
        this.cacheLabel = '';
        // Background-refresh indicator. /api/items returns
        // `cache_refreshing: true` when the in-memory cache was
        // served instantly + a fresh gather kicked off in
        // background. Drives the topbar refresh button's "Refreshing…"
        // pulse so the operator sees the system is still working
        // even after the foreground call completed. Auto-clears
        // on the next poll once the background gather lands.
        this.cacheRefreshing = !!d.cache_refreshing;
        // Only fire stats alongside a forced refresh when stats
        // polling is actually enabled. With statsInterval=0 the
        // operator explicitly chose "off", so auto-refresh
        // shouldn't sneak a /api/stats call in via the back door.
        if (force && this.statsInterval > 0) this.loadStats(true);
      } catch (e) { this.showToast(this.t('toasts.load_failed', { error: e.message }), 'error'); }
      this.loading = false;
    },
    async loadSettings() {
      try {
        const r = await fetch('/api/settings');
        const d = await r.json();
        this.settings = {
          apprise_url: d.apprise_url || '',
          apprise_tag: d.apprise_tag || '',
          swarm_autoheal_action: (d.swarm_autoheal_action === 'restart') ? 'restart' : 'notify',
          // First-boot auto-bootstrap of a default swarm_agent_health
          // schedule. Backend defaults to true; defensive default
          // here matches so a missing GET key doesn't drop the
          // checkbox to unchecked.
          swarm_autoheal_bootstrap_enabled: (d.swarm_autoheal_bootstrap_enabled !== false),
          portainer_public_url: d.portainer_public_url || '',
          backup_retention_count: Number.isFinite(d.backup_retention_count) ? d.backup_retention_count : 0,
          // Host-stats source + per-provider config. Mutually exclusive —
          // the radio in the Settings panel drives which block's fields
          // actually get persisted when Save is clicked.
          host_stats_source: d.host_stats_source || 'none',
          node_exporter_enabled: !!(d.node_exporter && d.node_exporter.enabled),
          node_exporter_url_template: (d.node_exporter && d.node_exporter.url_template)
            || 'http://{host}:9100/metrics',
          node_exporter_overrides_json: JSON.stringify(
            (d.node_exporter && d.node_exporter.overrides) || {}, null, 2),
          beszel_hub_url: (d.beszel && d.beszel.hub_url) || '',
          beszel_identity: (d.beszel && d.beszel.identity) || '',
          beszel_password: '',  // write-only — never shown
          beszel_password_set: !!(d.beszel && d.beszel.password_set),
          beszel_verify_tls: d.beszel ? d.beszel.verify_tls !== false : true,
          // Per-node aliases: Docker hostname → Beszel system name. Edited
          // from the Nodes view (click a node → drawer). We keep the full
          // object on this.settings so a save for one node preserves the
          // rest of the map.
          beszel_aliases: (d.beszel && d.beszel.aliases) || {},
          // Pulse provider settings — token is write-only on the wire.
          pulse_url: (d.pulse && d.pulse.url) || '',
          pulse_token: '',
          pulse_token_set: !!(d.pulse && d.pulse.token_set),
          pulse_verify_tls: d.pulse ? d.pulse.verify_tls !== false : true,
          pulse_aliases: (d.pulse && d.pulse.aliases) || {},
          // Webmin provider settings — password is write-only. Aliases
          // is Docker hostname → Miniserv base URL per host.
          webmin_url: (d.webmin && d.webmin.url) || '',
          webmin_user: (d.webmin && d.webmin.user) || '',
          webmin_password: '',
          webmin_password_set: !!(d.webmin && d.webmin.password_set),
          webmin_verify_tls: d.webmin ? !!d.webmin.verify_tls : false,
          webmin_aliases: (d.webmin && d.webmin.aliases) || {},
          // Ping provider. No secrets — every field round-trips
          // in the clear. `has_icmp_support` reflects whether icmplib
          // is importable on the server; SPA uses it to disable the
          // ICMP toggle with a hint when the package is missing.
          ping_enabled:          !!(d.ping && d.ping.enabled),
          ping_default_port:     (d.ping && Number.isFinite(d.ping.default_port)) ? d.ping.default_port : 443,
          ping_use_icmp:         !!(d.ping && d.ping.use_icmp),
          ping_has_icmp_support: !!(d.ping && d.ping.has_icmp_support),
          // SNMP provider. v3 secret keys flow as `_set` flags
          // (write-only contract); community / version / port / aliases
          // round-trip in the clear. `has_snmp_support` reflects whether
          // pysnmp is importable on the server; SPA uses it to disable
          // the ICMP-style "missing dep" hint when the package isn't
          // installed.
          snmp_default_community: (d.snmp && d.snmp.default_community) || 'public',
          snmp_default_version:   (d.snmp && d.snmp.default_version)   || 'v2c',
          snmp_default_port:      (d.snmp && Number.isFinite(d.snmp.default_port)) ? d.snmp.default_port : 161,
          snmp_v3_user:           (d.snmp && d.snmp.v3_user) || '',
          snmp_v3_auth_key:       '',
          snmp_v3_auth_key_set:   !!(d.snmp && d.snmp.v3_auth_key_set),
          snmp_v3_priv_key:       '',
          snmp_v3_priv_key_set:   !!(d.snmp && d.snmp.v3_priv_key_set),
          // Aliases textarea — same JSON-string pattern as
          // node_exporter_overrides_json so the existing dirty-tracker
          // + JSON-parse-on-save path applies.
          snmp_aliases:           (d.snmp && d.snmp.aliases) || {},
          snmp_aliases_json:      JSON.stringify((d.snmp && d.snmp.aliases) || {}, null, 2),
          snmp_has_snmp_support:  !!(d.snmp && d.snmp.has_snmp_support),
          // actual ImportError text from logic/snmp.py's module-
          // level pysnmp import block. Empty when pysnmp imported
          // cleanly. Surfaced inline in the SPA's "package missing"
          // hint so operators see the ROOT CAUSE without grepping
          // server logs.
          snmp_import_error:      (d.snmp && d.snmp.import_error) || '',
          // per-provider chip colour overrides. Empty string
          // means "use the SPA default" (see providerColor() helper).
          provider_color_beszel:        d.provider_color_beszel        || '',
          provider_color_pulse:         d.provider_color_pulse         || '',
          provider_color_node_exporter: d.provider_color_node_exporter || '',
          provider_color_webmin:        d.provider_color_webmin        || '',
          provider_color_ping:          d.provider_color_ping          || '',
          provider_color_snmp:          d.provider_color_snmp          || '',
          // Scheduler — IANA zone. Blank = container-local (legacy).
          scheduler_timezone: d.scheduler_timezone || '',
          // Open-Meteo upstream (weather widget). Blank = default.
          open_meteo_url: d.open_meteo_url || '',
          // Per-service master switches. Default true so legacy
          // deploys keep working before the operator interacts with
          // the toggles.
          apprise_enabled:    d.apprise_enabled    !== false,
          open_meteo_enabled: d.open_meteo_enabled !== false,
          portainer_enabled:  d.portainer_enabled  !== false,
          ssh_enabled:        d.ssh_enabled        !== false,
          asset_inventory_enabled: d.asset_inventory_enabled !== false,
          // Admin → Hosts toggle that controls visibility of the
          // host-drawer debug-data panel. Default true keeps the
          // legacy admin behaviour for fresh installs / pre-toggle
          // databases. Persisted via /api/settings on every flip.
          debug_panel_enabled: d.debug_panel_enabled !== false,
        };
        // Hydrate per-event notification toggles from the GET response.
        // The api_get_settings handler resolves each through
        // get_setting_bool (default true) so we get clean booleans
        // here. Without this hydration the events grid loads unchecked
        // and every Save blindly POSTs all 12 keys as 'false' (since
        // saveSettings normalises `payload[k] ? 'true' : 'false'`),
        // wiping the operator's intended state on every save.
        for (const k of (this.notifyEventKeys || [])) {
          this.settings[k] = !!d[k];
        }
        // Per-medium master switches. Default true when the
        // backend hasn't shipped the field yet (older builds) so the
        // SPA's checkbox doesn't silently default to OFF on a fresh
        // upgrade; matches `NOTIFY_MEDIUM_DEFAULTS` server-side.
        for (const k of (this.notifyMediumKeys || [])) {
          this.settings[k] = (d[k] !== false);
        }
        // TOTP / 2FA policy. Hydrate the five fields so the
        // Admin -> Config tab can render the inputs + the existing
        // saveSettings flow can ship the values back.
        this.settings.totp_allowed              = (d.totp_allowed !== false);
        this.settings.totp_required_for_admins  = !!d.totp_required_for_admins;
        this.settings.totp_required_for_users   = !!d.totp_required_for_users;
        this.settings.totp_lockout_max_failures =
          Number.isFinite(d.totp_lockout_max_failures) ? d.totp_lockout_max_failures : 5;
        this.settings.totp_lockout_minutes      =
          Number.isFinite(d.totp_lockout_minutes) ? d.totp_lockout_minutes : 15;
        // Passkey master toggle. Hydrated alongside the TOTP
        // group because both Save through the same totpPolicySnapshot
        // dirty tracker. Pre-fix the checkbox bound to a never-set
        // `settings.passkeys_allowed`, so on every page load it
        // appeared unchecked even when the DB value was true. Default
        // when the backend omits the key matches the backend's own
        // default (`_TOTP_POLICY_DEFAULTS` → True).
        this.settings.passkeys_allowed          = (d.passkeys_allowed !== false);
        // Capture baseline for the host-stats dirty indicator.
        // Passwords/tokens are always blank in the live form (write-
        // only on the wire) so any typed value flips dirty.
        this._hostStatsBaseline = this._hostStatsSnapshot();
        this.endpointId = d.endpoint_id || 1;

        // --- OIDC panel state ---
        this.oidcStatus = d.oidc || null;
        if (this.oidcStatus) {
          this.oidcForm = {
            enabled:       !!this.oidcStatus.enabled,
            issuer_url:    this.oidcStatus.issuer_url || '',
            client_id:     this.oidcStatus.client_id || '',
            client_secret: '',  // write-only — never prefill
            redirect_uri:  this.oidcStatus.redirect_uri || this.oidcStatus.redirect_uri_default || '',
            scopes:        this.oidcStatus.scopes || 'openid email profile groups',
            admin_group:   this.oidcStatus.admin_group || 'omnigrid-admins',
            // Default ON when the backend hasn't surfaced it yet (first load
            // after the migration); otherwise reflect whatever's persisted.
            verify_tls:    this.oidcStatus.verify_tls !== false,
            // ENH-002 / case-insensitive admin-group claim match.
            // Default true (legacy exact-match contract) so existing
            // deploys are no-ops; flip false in the form when the IdP
            // returns mixed-case group names that don't match the
            // operator-typed value verbatim.
            group_case_sensitive: this.oidcStatus.group_case_sensitive !== false,
          };
        }

        // --- Portainer connection panel state ---
        this.portainerStatus = d.portainer || null;
        if (this.portainerStatus) {
          this.portainerForm = {
            url:          this.portainerStatus.url || '',
            endpoint_id:  this.portainerStatus.endpoint_id || 1,
            verify_tls:   !!this.portainerStatus.verify_tls,
            api_key:      '',  // write-only — never prefill
          };
        }
        // Capture portainer-public-url baseline for the dirty getter
        // (separate from portainerForm because the public URL lives on
        // the broader `settings` object, not the form).
        this._portainerPublicBaseline = (this.settings || {}).portainer_public_url || '';
        // Capture all 5 unified-pattern baselines AFTER the form/settings
        // are fully populated. Subsequent edits compare against these.
        this._appriseBaseline    = this._appriseSnapshot();
        this._openMeteoBaseline  = this._openMeteoSnapshot();
        this._portainerBaseline  = this._portainerSnapshot();
        this._oidcBaseline       = this._oidcSnapshot();
        this._debugBaseline      = this._debugSnapshot();
        this._totpPolicyBaseline = this._totpPolicySnapshot();

        // --- Admin → SSH panel state ---
        this.hydrateSshSettings(d);

        // --- Host groups (ticket #93) ---
        // Server returns a clean list of {name, range_start, range_end,
        // order}. We keep the order-field for round-trip but also sort
        // here so the editor renders in the same order as the Hosts
        // view will. Fresh load resets dirty flag.
        this.hostGroups = Array.isArray(d.host_groups) ? d.host_groups.map(g => ({
          // Stable id minted server-side on first save (BUG-003 fix).
          // Round-trips unchanged so renames preserve the persisted
          // SSH password while a new same-named group can't inherit
          // it. Blank for any row that hasn't been saved yet.
          id: String(g.id || ''),
          name: String(g.name || ''),
          range_start: Number.isFinite(+g.range_start) ? +g.range_start : 0,
          range_end:   Number.isFinite(+g.range_end) ? +g.range_end : 0,
          // Optional display-prefix number. Empty string in the form
          // when unset; a positive integer otherwise. Sent through to
          // the server unchanged on save.
          number:      (g.number != null && +g.number > 0) ? +g.number : '',
          parent_name: String(g.parent_name || ''),
          ip_range:    String(g.ip_range || ''),
          // Per-group SSH overrides — same shape as `hosts_config[].ssh`.
          // Password is write-only (server returns `password_set`
          // flag instead of the value); UI surfaces "set" badge so
          // operators can see whether one's configured without
          // exposing it.
          ssh: {
            user:         String((g.ssh && g.ssh.user) || ''),
            port:         (g.ssh && g.ssh.port) || '',
            password:     '',
            password_set: !!(g.ssh && g.ssh.password_set),
            clear_password: false,
          },
          order:       Number.isFinite(+g.order) ? +g.order : 0,
        })) : [];
        this.hostGroupsDirty = false;
        // Bust groupedHosts() cache on every load (#423 / ENH-008).
        this.hostGroupsRevision = (this.hostGroupsRevision || 0) + 1;

        // --- Asset inventory (ticket #78) ---
        this.assetStatus = d.asset_inventory || null;
        if (this.assetStatus) {
          this.assetForm = {
            auth_mode:      (this.assetStatus.auth_mode === 'lifetime_token')
                              ? 'lifetime_token' : 'oauth2',
            base_url:       this.assetStatus.base_url || '',
            token_url:      this.assetStatus.token_url || '',
            client_id:      this.assetStatus.client_id || '',
            client_secret:  '',  // write-only — never prefill
            scope:          this.assetStatus.scope || '',
            lifetime_token: '',  // write-only — never prefill
            service:        this.assetStatus.service || '',
            action:         this.assetStatus.action  || '',
            min_value:      (this.assetStatus.min_value != null) ? String(this.assetStatus.min_value) : '',
            max_value:      (this.assetStatus.max_value != null) ? String(this.assetStatus.max_value) : '',
            edit_url_template: this.assetStatus.edit_url_template || '',
            // #417 / ENH-001 — default true if backend omits the key
            // (legacy deploy seeing first read).
            verify_tls:     (this.assetStatus.verify_tls !== false),
          };
        }
      } catch (e) { console.error(e); }
    },

    async saveOidcSettings() {
      if (this.oidcSaving) return;
      this.oidcSaving = true;
      try {
        await this._saveOidcSettingsImpl();
      } finally {
        this.oidcSaving = false;
      }
    },
    async _saveOidcSettingsImpl() {
      const body = {
        oidc_enabled:      !!this.oidcForm.enabled,
        oidc_issuer_url:   (this.oidcForm.issuer_url || '').trim(),
        oidc_client_id:    (this.oidcForm.client_id || '').trim(),
        oidc_redirect_uri: (this.oidcForm.redirect_uri || '').trim(),
        oidc_scopes:       (this.oidcForm.scopes || '').trim(),
        oidc_admin_group:  (this.oidcForm.admin_group || '').trim(),
        oidc_verify_tls:   !!this.oidcForm.verify_tls,
        oidc_group_case_sensitive: !!this.oidcForm.group_case_sensitive,
      };
      // Client secret: only send when the admin actually typed one.
      // Empty / whitespace-only = "keep current" per the backend contract.
      if (this.oidcForm.client_secret && this.oidcForm.client_secret.trim()) {
        body.oidc_client_secret = this.oidcForm.client_secret;
      }
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (r.ok) {
          // loadSettings() below re-captures the baseline; nothing to
          // clear here under the unified pattern.
          this.showToast(this.t('toasts.oidc_saved'));
          await this.loadSettings();
          this.oidcTestResult = null;
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    async testOidcConnection() {
      this.oidcTestResult = { pending: true };
      // Snapshot the form NOW so we can stamp it on success — same
      // pattern as `testPortainerConnection`.
      const probedSnapshot = this._oidcSnapshot();
      try {
        // Send the in-flight verify_tls so an admin can flip the
        // checkbox OFF and Test a self-signed issuer before saving.
        // Backend falls back to the saved DB value when the key is
        // missing.
        const r = await fetch('/api/oidc/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            issuer_url: (this.oidcForm.issuer_url || '').trim(),
            verify_tls: !!this.oidcForm.verify_tls,
          }),
        });
        const j = await r.json().catch(() => ({}));
        this.oidcTestResult = { ok: !!j.ok, status: j.status || 0, detail: j.detail || '' };
        if (j && j.ok) {
          this._oidcLastPassedTest = probedSnapshot;
        }
      } catch (e) {
        this.oidcTestResult = { ok: false, status: 0, detail: this.t('toasts.network_error') };
      }
    },
    // Save-button gate for OIDC — same shape as `canSavePortainer()`.
    // When the OIDC master toggle is OFF, Save is unconstrained
    // (no IdP probe will run anyway). When ON, the operator must
    // run a successful Test against the CURRENT form values before
    // Save unlocks; any edit after a passing Test re-locks Save by
    // mutating `_oidcSnapshot()` away from `_oidcLastPassedTest`.
    canSaveOidc() {
      const enabled = !!(this.oidcForm && this.oidcForm.enabled);
      if (!enabled) return true;
      return this._oidcLastPassedTest === this._oidcSnapshot()
             && !!this._oidcLastPassedTest;
    },

    async copyRedirectUri() {
      const uri = (this.oidcStatus && this.oidcStatus.redirect_uri_default)
        || (this.oidcForm && this.oidcForm.redirect_uri) || '';
      if (!uri) { this.showToast(this.t('toasts.no_redirect_uri'), 'error'); return; }
      try {
        await navigator.clipboard.writeText(uri);
        this.showToast(this.t('toasts.redirect_uri_copied'));
      } catch (_) { this.showToast(this.t('toasts.copy_failed'), 'error'); }
    },

    async savePortainerSettings() {
      if (this.portainerSaving) return;
      this.portainerSaving = true;
      try {
        await this._savePortainerSettingsImpl();
      } finally {
        this.portainerSaving = false;
      }
    },
    async _savePortainerSettingsImpl() {
      const body = {
        // Master switch saved alongside the URL / API key /
        // endpoint / verify_tls so the operator's toggle persists on
        // Save (no per-checkbox auto-save).
        portainer_enabled:     !!this.settings.portainer_enabled,
        portainer_url:         (this.portainerForm.url || '').trim(),
        portainer_endpoint_id: parseInt(this.portainerForm.endpoint_id) || 1,
        portainer_verify_tls:  !!this.portainerForm.verify_tls,
        // Public URL — folded into this Save button so the operator
        // doesn't have to hit two Save buttons after editing the
        // Portainer admin tab. Backend treats it as keep-current-if-
        // missing, so sending an empty string is a deliberate clear.
        portainer_public_url:  (this.settings.portainer_public_url || '').trim(),
        // Swarm autoheal action — moved to the Portainer admin panel
        // (the action targets Portainer's agent service). Saved
        // through this same POST so the operator's choice flips on
        // the same Save click as the rest of the Portainer config.
        swarm_autoheal_action: (this.settings.swarm_autoheal_action === 'restart') ? 'restart' : 'notify',
        // First-boot auto-bootstrap toggle for the default
        // swarm_agent_health schedule. Same Save click as the action
        // selector so the operator's intent applies in one round-trip.
        // Stored as a "true"/"false" string per the existing settings
        // shape (Pydantic Optional[str]).
        swarm_autoheal_bootstrap_enabled:
          this.settings.swarm_autoheal_bootstrap_enabled ? 'true' : 'false',
      };
      if (this.portainerForm.api_key && this.portainerForm.api_key.trim()) {
        body.portainer_api_key = this.portainerForm.api_key;
      }
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (r.ok) {
          // loadSettings() below re-captures the baseline.
          this.showToast(this.t('toasts.portainer_saved'));
          await this.loadSettings();
          this.portainerTestResult = null;
          // Trigger a forced refresh so the dashboard populates with data
          // from the newly-configured endpoint.
          this.refresh(true);
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    async testPortainerConnection() {
      this.portainerTestResult = { pending: true };
      // Snapshot the form NOW so we can stamp it on success. Compared
      // to the live snapshot at Save-time so any edit between Test
      // and Save invalidates the test result.
      const probedSnapshot = this._portainerSnapshot();
      try {
        const r = await fetch('/api/portainer/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url:         (this.portainerForm.url || '').trim(),
            endpoint_id: parseInt(this.portainerForm.endpoint_id) || 1,
            verify_tls:  !!this.portainerForm.verify_tls,
            api_key:     this.portainerForm.api_key || '',
          }),
        });
        const j = await r.json().catch(() => ({}));
        this.portainerTestResult = { ok: !!j.ok, status: j.status || 0, detail: j.detail || '' };
        if (j && j.ok) {
          this._portainerLastPassedTest = probedSnapshot;
        }
      } catch (e) {
        this.portainerTestResult = { ok: false, status: 0, detail: this.t('toasts.network_error') };
      }
    },
    // Save-button gate. When portainer_enabled is OFF, no test required
    // (the service won't be probed by the backend either). When ON,
    // the operator must run a successful Test against the CURRENT
    // form values before Save unlocks — typed values can't ship without
    // proving the round-trip works. Any edit after a successful Test
    // mutates `_portainerSnapshot()` away from `_portainerLastPassedTest`,
    // re-locking Save and prompting the operator to re-test.
    canSavePortainer() {
      if (!(this.settings || {}).portainer_enabled) return true;
      return this._portainerLastPassedTest === this._portainerSnapshot()
             && !!this._portainerLastPassedTest;
    },

    async testBeszelConnection() {
      // Mirrors testPortainerConnection — probes the hub with the
      // current form values (or saved password if the field is blank)
      // without mutating any settings. Result shown inline below the
      // Test button so the admin sees what went wrong without a toast.
      this.beszelTestResult = { pending: true };
      try {
        const r = await fetch('/api/beszel/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            hub_url:    (this.settings.beszel_hub_url || '').trim(),
            identity:   (this.settings.beszel_identity || '').trim(),
            password:   this.settings.beszel_password || '',
            verify_tls: !!this.settings.beszel_verify_tls,
          }),
        });
        const j = await r.json().catch(() => ({}));
        this.beszelTestResult = {
          pending: false,
          ok: !!j.ok,
          detail: j.detail || this.t(j.ok ? 'toasts_extra.test_result_ok' : 'toasts_extra.test_result_failed'),
          systems: j.systems || [],
        };
      } catch (e) {
        this.beszelTestResult = { pending: false, ok: false, detail: this.t('toasts.network_error') };
      }
    },
    async testPulseConnection() {
      // Mirrors testBeszelConnection — probes Pulse with the current
      // form values (or saved token when the field is blank).
      this.pulseTestResult = { pending: true };
      try {
        const r = await fetch('/api/pulse/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url:        (this.settings.pulse_url || '').trim(),
            token:      this.settings.pulse_token || '',
            verify_tls: !!this.settings.pulse_verify_tls,
          }),
        });
        const j = await r.json().catch(() => ({}));
        this.pulseTestResult = {
          pending: false,
          ok: !!j.ok,
          detail: j.detail || this.t(j.ok ? 'toasts_extra.test_result_ok' : 'toasts_extra.test_result_failed'),
          nodes: j.nodes || [],
        };
      } catch (e) {
        this.pulseTestResult = { pending: false, ok: false, detail: this.t('toasts.network_error') };
      }
    },
    async testWebminConnection() {
      // Probes ONE Webmin URL — user types the URL into webminTestUrl
      // since every Miniserv instance is per-host. Credentials come
      // from the settings form (or persisted values when blank).
      const url = (this.webminTestUrl || this.settings.webmin_url || '').trim();
      if (!url) {
        this.showToast(this.t('admin_hosts.webmin_test_url_required'), 'error');
        return;
      }
      this.webminTestResult = { pending: true };
      try {
        const r = await fetch('/api/webmin/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url,
            user:       (this.settings.webmin_user || '').trim(),
            password:   this.settings.webmin_password || '',
            verify_tls: !!this.settings.webmin_verify_tls,
          }),
        });
        const j = await r.json().catch(() => ({}));
        this.webminTestResult = {
          pending: false,
          ok: !!j.ok,
          detail: j.detail || this.t(j.ok ? 'toasts_extra.test_result_ok' : 'toasts_extra.test_result_failed'),
        };
      } catch (e) {
        this.webminTestResult = { pending: false, ok: false, detail: this.t('toasts.network_error') };
      }
    },
    // list of curated hosts that have ping enabled. Pulled from
    // the in-memory `hostsConfig` (loaded by the Hosts admin tab) so
    // the picker stays in sync with the row-level toggles without an
    // extra round-trip.
    pingEnabledHosts() {
      const rows = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
      return rows
        .filter(h => h && h.ping && h.ping.enabled && h.enabled !== false && h.id)
        .map(h => ({ id: h.id, label: this.hostDisplayName(h) || h.id }));
    },
    async testPingConnection() {
      // One-shot live probe against a curated host. Server-side
      // honours unsaved overrides (port + transport are optional in
      // the body), so the operator can test before committing.
      const hid = (this.pingTestHostId || '').trim();
      if (!hid) {
        this.showToast(this.t('settings.host_stats.ping.test_no_hosts'), 'error');
        return;
      }
      this.pingTestResult = { pending: true };
      try {
        const r = await fetch('/api/ping/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ host_id: hid }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.pingTestResult = {
            pending: false, ok: false,
            detail: j.detail || this.t('toasts.save_failed'),
          };
          return;
        }
        const rtt = (j.rtt_ms != null) ? j.rtt_ms.toFixed(1) : '—';
        const loss = (j.loss_pct != null) ? j.loss_pct.toFixed(0) : '—';
        const detail = j.ok
          ? this.t('settings.host_stats.ping.test_result_alive', {
              host: j.host || hid, port: j.port || '?', rtt_ms: rtt, loss_pct: loss,
            })
          : this.t('settings.host_stats.ping.test_result_down', {
              host: j.host || hid, port: j.port || '?', error: j.error || '—',
            });
        this.pingTestResult = { pending: false, ok: !!j.ok, detail };
      } catch (e) {
        this.pingTestResult = { pending: false, ok: false, detail: this.t('toasts.network_error') };
      }
    },
    // #344 / list of curated hosts that have SNMP mapped (a
    // non-empty `snmp_name` row field). Pulled from the in-memory
    // `hostsConfig` (loaded by the Hosts admin tab) so the picker
    // stays in sync with the row-level config without an extra
    // round-trip. Mirrors `pingEnabledHosts()` exactly so the two
    // providers' test UX is unified.
    snmpEnabledHosts() {
      const rows = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
      return rows
        .filter(h => h && h.enabled !== false && h.id
                     && (h.snmp_name || '').trim()
                     // explicit opt-in: SNMP probes only run
                     // when the operator checks the per-host enable
                     // box. Default-OFF mirrors ping.enabled.
                     && !!(h.snmp && h.snmp.enabled === true))
        .map(h => ({ id: h.id, label: this.hostDisplayName(h) || h.id }));
    },
    // #344 / SNMP test widget. UX-unified with the Ping test
    // : operator picks a curated SNMP-mapped host from the
    // dropdown, the helper looks up the row's `snmp_name` (target IP /
    // hostname) + per-row overrides (community / version / port /
    // v3 USM), and posts them to `/api/snmp/test`. Falls back to
    // persisted defaults server-side via `_resolve_field` for any
    // field the row doesn't override, so the operator doesn't need
    // to retype the community / v3 keys to validate one box.
    async testSnmpConnection() {
      const hid = (this.snmpTestHostId || '').trim();
      if (!hid) {
        this.showToast(this.t('settings.host_stats.snmp.test_no_hosts'), 'error');
        return;
      }
      const row = (Array.isArray(this.hostsConfig) ? this.hostsConfig : [])
        .find(h => h && h.id === hid);
      const target = ((row && row.snmp_name) || hid).trim();
      // Per-row overrides — only forwarded when the operator actually
      // set them on this row; blanks fall through to the global
      // defaults server-side.
      const ovr = (row && row.snmp) || {};
      const body = { host: target };
      if (ovr.community) body.community = ovr.community;
      if (ovr.version)   body.version   = ovr.version;
      if (ovr.port)      body.port      = ovr.port;
      if (ovr.v3_user)   body.v3_user   = ovr.v3_user;
      if (ovr.v3_auth_key) body.v3_auth_key = ovr.v3_auth_key;
      if (ovr.v3_priv_key) body.v3_priv_key = ovr.v3_priv_key;
      this.snmpTestResult = { pending: true };
      try {
        const r = await fetch('/api/snmp/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.snmpTestResult = {
            pending: false, ok: false,
            detail: j.detail || this.t('toasts.save_failed'),
          };
          return;
        }
        this.snmpTestResult = {
          pending: false, ok: !!j.ok,
          detail: j.detail || (j.ok ? 'OK' : this.t('toasts.save_failed')),
        };
      } catch (e) {
        this.snmpTestResult = { pending: false, ok: false, detail: this.t('toasts.network_error') };
      }
    },
    // -------- SSH console ----------------------------------------------
    // Hydrate the Admin → SSH form from /api/settings. Called from
    // loadSettings() alongside the other provider-specific pulls so the
    // form has values ready when the admin opens the section.
    hydrateSshSettings(apiSettings) {
      const s = (apiSettings && apiSettings.ssh) || {};
      const actions = Array.isArray(s.custom_actions) ? s.custom_actions : [];
      this.sshSettings = {
        user:            s.user || '',
        port:            s.port || 22,
        private_key:     '',               // write-only — never hydrated
        passphrase:      '',               // write-only — never hydrated
        password:        '',               // write-only — never hydrated
        fqdn_suffix:     s.fqdn_suffix || '',
        known_hosts:     s.known_hosts || '',
        destructive_patterns: s.destructive_patterns || '',
        private_key_set: !!s.private_key_set,
        passphrase_set:  !!s.passphrase_set,
        password_set:    !!s.password_set,
        // Seed with the historical 5 presets when the DB row is empty
        // so fresh installs don't start with a bare SSH card. Operators
        // can edit / remove any of them from Admin → SSH.
        custom_actions:  actions.length ? actions : this.defaultSshCustomActions(),
      };
      this.sshSettingsDirty = false;
    },
    markSshSettingsDirty() { this.sshSettingsDirty = true; },
    // The 5 baked-in commands OmniGrid used before custom actions
    // became editable. Used only as a first-boot seed — once the
    // operator saves anything, the DB row wins.
    defaultSshCustomActions() {
      // Titles are resolved through t() at SEED TIME (first boot with
      // an empty ssh_custom_actions row). Once the operator saves,
      // titles become plain DB strings — switching UI language later
      // does NOT retranslate persisted actions (they're editable
      // per-install strings now, not static UI chrome). IDs stay
      // stable so a "reset to defaults" flow could still match them.
      const tr = (k, fallback) => {
        const v = this.t('admin_ssh.default_actions.' + k);
        return (v && v !== ('admin_ssh.default_actions.' + k)) ? v : fallback;
      };
      return [
        { id: 'restart-beszel',
          title: tr('restart_beszel', 'Restart Beszel agent'),
          command: 'systemctl restart beszel-agent || docker restart beszel-agent' },
        { id: 'show-beszel-env',
          title: tr('show_beszel_env', 'Show Beszel agent env'),
          command: "systemctl show beszel-agent -p Environment || docker inspect beszel-agent --format '{{range .Config.Env}}{{println .}}{{end}}'" },
        { id: 'set-beszel-nics',
          title: tr('set_beszel_nics', 'Set Beszel NICS (edit eth0 first)'),
          command:
            "mkdir -p /etc/systemd/system/beszel-agent.service.d && " +
            "printf '[Service]\\nEnvironment=NICS=eth0\\n' > /etc/systemd/system/beszel-agent.service.d/nics.conf && " +
            "systemctl daemon-reload && systemctl restart beszel-agent" },
        { id: 'verify-beszel-nics',
          title: tr('verify_beszel_nics', 'Verify Beszel NICS setup'),
          command:
            "echo '=== override.conf (systemd) ===' && " +
            "ls -la /etc/systemd/system/beszel-agent.service.d/ 2>&1 || true; " +
            "cat /etc/systemd/system/beszel-agent.service.d/*.conf 2>&1 || true; " +
            "echo '=== beszel-agent unit Environment ===' && " +
            "systemctl show beszel-agent -p Environment 2>&1 || true; " +
            "echo '=== beszel-agent process env (if running) ===' && " +
            "(ps -eo pid,comm,args 2>/dev/null | grep -E 'beszel.?agent' | grep -v grep || echo 'no beszel process found'); " +
            "echo '=== docker fallback ===' && " +
            "(docker inspect beszel-agent --format '{{range .Config.Env}}{{println .}}{{end}}' 2>&1 || echo 'no docker beszel-agent container'); " +
            "echo '=== sudo sanity ===' && sudo -n whoami 2>&1" },
        { id: 'journal-beszel',
          title: tr('journal_beszel', 'Journal: Beszel agent (last 40)'),
          command: 'journalctl -u beszel-agent -n 40 --no-pager' },
        { id: 'ip-link',
          title: tr('ip_link', 'List NICs (ip link)'),
          command: 'ip -o link show' },
        { id: 'uptime',
          title: tr('uptime', 'Uptime + load'),
          command: 'uptime' },
      ];
    },
    addSshCustomAction() {
      this.sshSettings.custom_actions = [
        ...(this.sshSettings.custom_actions || []),
        { id: '', title: '', command: '' },
      ];
      this.markSshSettingsDirty();
    },
    removeSshCustomAction(idx) {
      const arr = (this.sshSettings.custom_actions || []).slice();
      arr.splice(idx, 1);
      this.sshSettings.custom_actions = arr;
      this.markSshSettingsDirty();
    },
    moveSshCustomAction(idx, delta) {
      const arr = (this.sshSettings.custom_actions || []).slice();
      const target = idx + delta;
      if (target < 0 || target >= arr.length) return;
      const [row] = arr.splice(idx, 1);
      arr.splice(target, 0, row);
      this.sshSettings.custom_actions = arr;
      this.markSshSettingsDirty();
    },
    // Erase a stored SSH secret (key / passphrase / password). The
    // normal save path preserves secrets on blank input (so ops don't
    // accidentally wipe a stored key by saving unrelated changes),
    // which means there's no other way to clear them. Confirm first
    // so a misclick doesn't lock the operator out of every host.
    async clearSshSecret(kind) {
      const label = kind === 'private_key' ? 'SSH private key'
                  : kind === 'passphrase' ? 'passphrase'
                  : 'default password';
      const promptTitle = this.t('toasts_extra.ssh_clear_secret_prompt_title', { label });
      const ok = typeof Swal !== 'undefined'
        ? (await Swal.fire({
            title: promptTitle,
            text: kind === 'private_key'
              ? 'This also clears the passphrase. You will need to paste a new key before any SSH action will work.'
              : kind === 'passphrase'
              ? 'The private key stays, but will be tried unprotected. If it needs a passphrase, SSH auth will fail.'
              : 'Hosts with a per-host password override will still work; everything else will need a new default.',
            icon: 'warning',
            showCancelButton: true,
            confirmButtonText: this.t('actions.clear'),
            cancelButtonText: this.t('actions.cancel'),
          })).isConfirmed
        : window.confirm(promptTitle);
      if (!ok) return;
      try {
        const body = {};
        if (kind === 'private_key') body.clear_ssh_private_key = true;
        if (kind === 'passphrase')  body.clear_ssh_passphrase  = true;
        if (kind === 'password')    body.clear_ssh_password    = true;
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts_extra.clear_failed_generic'), 'error');
          return;
        }
        await this.loadSettings();
        this.showToast(label[0].toUpperCase() + label.slice(1) + ' cleared', 'success');
      } catch (e) {
        this.showToast(this.t('toasts_extra.clear_failed_with_error', { error: e.message }), 'error');
      }
    },
    async saveSshSettings() {
      this.sshSettingsBusy = true;
      try {
        const body = {
          // Master switch saved alongside the rest of the form
          // — flipping the toggle marks the form dirty; clicking Save
          // commits both the toggle and any field edits in one go.
          ssh_enabled:                   !!this.settings.ssh_enabled,
          ssh_default_user:              this.sshSettings.user || '',
          ssh_default_port:              parseInt(this.sshSettings.port, 10) || 22,
          ssh_fqdn_suffix:               this.sshSettings.fqdn_suffix || '',
          ssh_default_known_hosts:       this.sshSettings.known_hosts || '',
          ssh_destructive_patterns:      this.sshSettings.destructive_patterns || '',
          // Backend drops rows with empty title or command, so clean
          // slots the operator left blank simply vanish on save.
          ssh_custom_actions: (this.sshSettings.custom_actions || [])
            .map(a => ({
              id:      (a.id || '').trim(),
              title:   (a.title || '').trim(),
              command: (a.command || '').trim(),
            })),
        };
        // Write-only: only send when the operator typed a new value.
        if ((this.sshSettings.private_key || '').trim() !== '') {
          body.ssh_default_private_key = this.sshSettings.private_key;
        }
        if ((this.sshSettings.passphrase || '').trim() !== '') {
          body.ssh_default_private_key_passphrase = this.sshSettings.passphrase;
        }
        if ((this.sshSettings.password || '').trim() !== '') {
          body.ssh_default_password = this.sshSettings.password;
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        // Re-pull settings so the _set flags update without a full reload.
        await this.loadSettings();
        this.showToast(this.t('toasts_extra.ssh.settings_saved'), 'success');
      } catch (e) {
        this.showToast(
          this.t('toasts_extra.save_failed_generic') + ': ' + e.message,
          'error',
        );
      } finally {
        this.sshSettingsBusy = false;
      }
    },
    // Per-host drawer card — lazy fetch ssh/status when the card opens
    // so the drawer still paints instantly for hosts where the admin
    // never touches SSH. `refresh: true` bypasses the short-circuit so
    // "Test connection" can force a fresh status read.
    async loadSshStatus(hostId, { refresh = false } = {}) {
      if (!hostId) return;
      if (!refresh && this.sshStatus[hostId]) return this.sshStatus[hostId];
      try {
        const r = await fetch('/api/hosts/' + encodeURIComponent(hostId) + '/ssh/status');
        if (!r.ok) {
          this.sshStatus = { ...this.sshStatus, [hostId]: { error: `HTTP ${r.status}` } };
          return;
        }
        const d = await r.json();
        this.sshStatus = { ...this.sshStatus, [hostId]: d };
        return d;
      } catch (e) {
        this.sshStatus = { ...this.sshStatus, [hostId]: { error: e.message } };
      }
    },
    async toggleSshCard(hostId) {
      if (!hostId) return;
      const open = !this.sshOpen[hostId];
      this.sshOpen = { ...this.sshOpen, [hostId]: open };
      if (open) {
        // Default to dry-run OFF — operator explicitly opts in. The
        // destructive-command gate (typed-hostname confirm) still
        // fires for `rm`/`dd`/`reboot`/etc. regardless of this
        // checkbox, so accidental nukes remain blocked.
        if (!(hostId in this.sshDryRun)) {
          this.sshDryRun = { ...this.sshDryRun, [hostId]: false };
        }
        // Scroll the just-expanded SSH-run body to the top of the
        // drawer viewport so the operator doesn't have to scroll
        // down on a long drawer. $nextTick lets x-show flip
        // before we measure; data-host-section keeps the selector
        // stable across host changes.
        this.$nextTick(() => {
          const el = document.querySelector(
            `[data-host-section="ssh-${hostId}"]`,
          );
          if (el) {
            try {
              el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            } catch (_) {}
          }
        });
        await this.loadSshStatus(hostId);
      }
    },
    async testSshConnection(hostId) {
      if (!hostId) return;
      this.sshTestBusy = { ...this.sshTestBusy, [hostId]: true };
      try {
        const r = await fetch('/api/hosts/' + encodeURIComponent(hostId) + '/ssh/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        });
        const j = await r.json().catch(() => ({}));
        this.sshResult = { ...this.sshResult, [hostId]: j };
        this.sshLastTested = { ...this.sshLastTested, [hostId]: Date.now() };
        if (j.ok) {
          const resolved = j.resolved || {};
          this.showToast(
            this.t('toasts_extra.ssh.test_ok', {
              user: resolved.user || '?',
              host: resolved.host || hostId,
            }),
            'success',
          );
        } else {
          this.showToast(
            this.t('toasts_extra.ssh.test_failed', { detail: j.error || 'unknown' }),
            'error',
          );
        }
        // Re-hydrate the status card so the fingerprint / last-tested
        // fields reflect whatever this probe learned.
        await this.loadSshStatus(hostId, { refresh: true });
      } catch (e) {
        this.showToast(this.t('toasts_extra.ssh.test_failed', { detail: e.message }), 'error');
      } finally {
        this.sshTestBusy = { ...this.sshTestBusy, [hostId]: false };
      }
    },
    // DB-backed custom action → drop the rendered command into the
    // textarea so the operator can review before hitting Run. `{host}`
    // is substituted with the resolved target hostname (falls back to
    // the row id when the status probe hasn't populated yet). We
    // DELIBERATELY don't auto-execute — destructive commands go
    // through the Run button's confirmation gate.
    // True when SSH actions are safe to run against this host. Gated
    // on live provider status: if the host isn't `up` (or we haven't
    // heard from a provider yet — `loading` / `unconfigured` /
    // `unknown` / `down` / `paused`), trying to SSH in will just
    // produce an OSError 113 ("No route to host") after a long
    // timeout. The drawer's UI binds Run + custom-action buttons +
    // the command textarea to this so operators can't fire dead
    // commands. Backend still enforces via classify_exception →
    // HOST_UNREACHABLE for defense in depth.
    isSshAllowed(h) {
      return !!(h && h.status === 'up');
    },
    sshDisabledReason(h) {
      if (!h) return '';
      if (h.status === 'up') return '';
      // Human-readable hint for the disabled tooltip — reason varies
      // by status so the operator knows whether to wait (loading),
      // configure a provider (unconfigured), or investigate (down /
      // unknown / paused).
      return this.t('hosts_extra_ssh.disabled_not_up', {
        status: String(h.status || '—'),
      });
    },
    runSshCustomAction(hostId, action) {
      if (!hostId || !action || !action.command) return;
      const resolved = (this.sshStatus[hostId] && this.sshStatus[hostId].resolved) || {};
      const host = resolved.host || hostId;
      const cmd = String(action.command).replace(/\{host\}/g, host);
      this.sshCommand = { ...this.sshCommand, [hostId]: cmd };
    },
    async runSshCommand(hostId) {
      const h = (this.hosts || []).find(x => x && x.id === hostId);
      if (!this.isSshAllowed(h)) {
        this.showToast(this.sshDisabledReason(h), 'error');
        return;
      }
      const command = (this.sshCommand[hostId] || '').trim();
      if (!command) {
        this.showToast(this.t('toasts_extra.ssh.command_required'), 'error');
        return;
      }
      const dryRun = this.sshDryRun[hostId] === true;
      // Destructive-command gate. The backend flags + returns
      // `destructive: [patterns]` but we check up-front too so we can
      // raise the bar BEFORE sending the payload.
      const destructiveRegex = [
        /\brm\s/i, /\bmkfs\b/i, /\bdd\s/i, />\s*\//, /\bsystemctl\s+stop\b/i,
        /\breboot\b/i, /\bpoweroff\b/i, /\bshutdown\b/i,
      ];
      const looksDestructive = !dryRun && destructiveRegex.some(r => r.test(command));
      if (looksDestructive) {
        const host = (this.sshStatus[hostId] && this.sshStatus[hostId].resolved && this.sshStatus[hostId].resolved.host) || hostId;
        const typed = window.prompt(
          this.t('hosts_extra_ssh.confirm_prompt', { host }),
          '',
        );
        if ((typed || '').trim() !== host) {
          this.showToast(this.t('toasts_extra.ssh.confirm_wrong_host'), 'error');
          return;
        }
      }
      this.sshBusy = { ...this.sshBusy, [hostId]: true };
      try {
        const r = await fetch('/api/hosts/' + encodeURIComponent(hostId) + '/ssh/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command, dry_run: dryRun }),
        });
        const j = await r.json().catch(() => ({}));
        this.sshResult = { ...this.sshResult, [hostId]: j };
        if (j.ok) {
          this.showToast(
            dryRun
              ? this.t('toasts_extra.ssh.dry_run_ok')
              : this.t('toasts_extra.ssh.run_ok', { code: j.exit_code }),
            'success',
          );
        } else {
          this.showToast(
            (dryRun
              ? this.t('toasts_extra.ssh.dry_run_failed', { detail: j.error || 'unknown' })
              : this.t('toasts_extra.ssh.run_failed', { detail: j.error || 'unknown' })),
            'error',
          );
        }
      } catch (e) {
        this.showToast(this.t('toasts_extra.ssh.run_failed', { detail: e.message }), 'error');
      } finally {
        this.sshBusy = { ...this.sshBusy, [hostId]: false };
      }
    },
    async copySshOutput(hostId) {
      const r = this.sshResult[hostId];
      if (!r) return;
      const blob = [
        '$ ' + (this.sshCommand[hostId] || ''),
        '--- exit ---',
        String(r.exit_code),
        '--- stdout ---',
        r.stdout || '',
        '--- stderr ---',
        r.stderr || '',
      ].join('\n');
      try {
        await navigator.clipboard.writeText(blob);
        this.showToast(this.t('toasts_extra.ssh.copied_output'), 'success');
      } catch (_) {
        window.prompt('Copy output', blob);
      }
    },

    // ---- Interactive SSH terminal modal (TODO #170) ----
    // Browser <-WSS-> backend <-asyncssh shell-> target host. Uses
    // xterm.js for the viewport (UMD bundles preloaded in <head>).
    // The modal lives at top-level so its WS survives drawer-state
    // transitions; closing drops the socket cleanly.
    openHostTerminal(host) {
      if (!host || !host.id) return;
      // Admin-only is also enforced server-side; this UI gate just
      // avoids "click button → 4403" friction.
      if (!this.me || this.me.role !== 'admin') {
        this.showToast(this.t('hosts_extra_ssh.terminal.not_admin'), 'error');
        return;
      }
      if (typeof window.Terminal !== 'function') {
        // xterm.js failed to load (older cached HTML, blocked by CSP, etc.)
        this.showToast(this.t('hosts_extra_ssh.terminal.xterm_missing'), 'error');
        return;
      }
      // Tear down any existing session before opening a new one — prevents
      // resource leaks if the operator switches hosts mid-session.
      this._teardownTerminalSession();
      this.terminalHost = host;
      this.terminalResolved = null;
      this.terminalCloseReason = '';
      this.terminalState = 'connecting';
      this.terminalModalOpen = true;
      // $nextTick so x-ref="terminalHost" exists in the DOM.
      this.$nextTick(() => {
        try {
          this._spawnTerminal(host);
        } catch (e) {
          this.terminalState = 'error';
          this.terminalCloseReason = (e && e.message) || String(e);
        }
      });
    },
    _spawnTerminal(host) {
      const container = this.$refs.terminalHost;
      if (!container) return;
      // ---- 1) xterm.js setup ----
      const term = new window.Terminal({
        fontFamily: 'Menlo, Consolas, "DejaVu Sans Mono", monospace',
        fontSize: 13,
        cursorBlink: true,
        scrollback: 5000,
        // Background pulled from a CSS token so it tracks the theme.
        // xterm.js renders directly to canvas / DOM so we have to read
        // the resolved colour at instantiation time.
        theme: this._terminalTheme(),
        // Convert paste to bracketed paste so the shell knows what's
        // happening; remote programs that don't grok it still work.
        macOptionIsMeta: true,
      });
      const fit = (typeof window.FitAddon === 'function')
        ? new window.FitAddon()
        : null;
      const wlAddon = (typeof window.WebLinksAddon === 'function')
        ? new window.WebLinksAddon()
        : null;
      if (fit) term.loadAddon(fit);
      if (wlAddon) term.loadAddon(wlAddon);
      term.open(container);
      // ---- Resize-to-container helper. ----
      // The earlier "staircase of fit()" approach kept failing because
      // xterm's FitAddon.proposeDimensions() returns `undefined` when
      // the parent's computed style isn't a clean px value (which can
      // happen briefly with `flex: 1 1 auto; min-height: 0`), and
      // fit.fit() silently no-ops on undefined. Result: cols stuck at
      // the default 80 and the shell wraps mid-line forever. The fix:
      // try FitAddon first (it's the most accurate when it works),
      // then if `term.cols` is still the default 80 after a short
      // wait, MANUALLY measure the container and call term.resize()
      // directly. xterm's onResize handler pipes the new size down
      // the WS so the backend PTY follows.
      //
      // Manual cell metrics for the configured Menlo / Consolas /
      // DejaVu Mono 13px font: ~7.85px wide × ~17.5px tall (Chromium
      // / WebKit / Firefox all within ±0.2px). Slight over-estimation
      // is fine — xterm tolerates one-cell rounding error and the
      // first WS resize callback will correct it.
      const _MANUAL_CELL_W = 7.85;
      const _MANUAL_CELL_H = 17.5;
      // Reserve room for xterm's vertical scrollbar so the rightmost
      // glyph cell never tucks behind the scrollbar gutter when the
      // pty has filled past the visible rows. 18px is generous enough
      // to cover all default-styled scrollbars (Chromium ~15px,
      // Firefox ~12px, Safari ~14px) without making the gap visible.
      const _MANUAL_SCROLLBAR_RESERVE = 18;
      const measureAndResize = () => {
        if (!term || !container || !container.isConnected) return false;
        // Manual measurement path (canonical). FitAddon was the
        // original approach but its `proposeDimensions()` silently
        // returns `undefined` on flex children with `min-height: 0`,
        // AND when it does work it doesn't reserve room for xterm's
        // own vertical scrollbar — so the rightmost glyph tucks
        // behind the scrollbar gutter. Manual measurement reads the
        // container's bounding rect, subtracts CSS padding + the
        // scrollbar reserve, and divides by known cell metrics for
        // the configured 13px monospace font. Idempotent: only calls
        // term.resize() when the computed cols/rows actually differ
        // from the current value, so ResizeObserver re-fires don't
        // ratchet the size down.
        const rect = container.getBoundingClientRect();
        if (rect.width < 100 || rect.height < 50) return false;
        const cs = window.getComputedStyle(container);
        const padX = (parseFloat(cs.paddingLeft) || 0)
                   + (parseFloat(cs.paddingRight) || 0);
        const padY = (parseFloat(cs.paddingTop) || 0)
                   + (parseFloat(cs.paddingBottom) || 0);
        const usableW = rect.width  - padX - _MANUAL_SCROLLBAR_RESERVE;
        const usableH = rect.height - padY;
        const cols = Math.max(20, Math.floor(usableW / _MANUAL_CELL_W));
        const rows = Math.max(5,  Math.floor(usableH / _MANUAL_CELL_H));
        if (cols !== term.cols || rows !== term.rows) {
          try { term.resize(cols, rows); } catch (_) {}
        }
        return true;
      };
      // Run the helper on a staircase + ResizeObserver. First successful
      // measurement halts the polling. The 50/250/600/1200ms retries
      // cover Alpine micro-batch, .fade-in animation completion, and a
      // long-tail safety net for slow first-paint.
      requestAnimationFrame(() => requestAnimationFrame(measureAndResize));
      const fitTimers = [
        setTimeout(measureAndResize, 50),
        setTimeout(measureAndResize, 250),
        setTimeout(measureAndResize, 600),
        setTimeout(measureAndResize, 1200),
      ];
      this.terminalFitTimers = fitTimers;
      this.terminal = term;
      this.terminalFit = fit;
      this.terminalMeasureAndResize = measureAndResize;

      // ---- 2) WebSocket to /api/hosts/{id}/ssh/terminal ----
      const proto = (location.protocol === 'https:') ? 'wss://' : 'ws://';
      const cols = term.cols || 80;
      const rows = term.rows || 24;
      const url = proto + location.host
        + '/api/hosts/' + encodeURIComponent(host.id) + '/ssh/terminal'
        + '?cols=' + cols + '&rows=' + rows;
      let ws;
      try {
        ws = new WebSocket(url);
      } catch (e) {
        this.terminalState = 'error';
        this.terminalCloseReason = (e && e.message) || String(e);
        return;
      }
      ws.binaryType = 'arraybuffer';
      this.terminalSocket = ws;

      ws.addEventListener('open', () => {
        // Send an explicit resize so the backend's PTY matches the
        // viewport (the query-string size is just a first-frame hint).
        try {
          ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
        } catch (_) {}
      });
      ws.addEventListener('message', (ev) => {
        if (typeof ev.data === 'string') {
          // Control frame — JSON.
          let ctl = null;
          try { ctl = JSON.parse(ev.data); } catch (_) { return; }
          if (!ctl || typeof ctl !== 'object') return;
          if (ctl.type === 'ready') {
            this.terminalState = 'connected';
            this.terminalResolved = ctl.resolved || null;
            // Resize on the 'ready' control frame — the modal has been
            // visible for at least one round-trip by now, so the
            // .terminal-host's box is definitely committed. The helper
            // tries FitAddon first and falls back to a manual
            // getBoundingClientRect() measurement if fit silently
            // no-ops; either way the new cols/rows pipe down the WS
            // via xterm's onResize handler.
            try { measureAndResize(); } catch (_) {}
            term.focus();
          } else if (ctl.type === 'error') {
            this.terminalState = 'error';
            this.terminalCloseReason = ctl.message || ctl.code || 'error';
          } else if (ctl.type === 'exit') {
            this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.exit_code', {
              code: (ctl.code == null ? '?' : ctl.code),
            });
          }
          // 'keepalive' frames — ignored; their job is done at the proxy layer.
        } else if (ev.data instanceof ArrayBuffer) {
          // Binary frame — raw shell output.
          const bytes = new Uint8Array(ev.data);
          term.write(bytes);
        }
      });
      ws.addEventListener('close', (ev) => {
        if (this.terminalState === 'connecting') {
          this.terminalState = 'error';
        } else if (this.terminalState !== 'error') {
          this.terminalState = 'disconnected';
        }
        // UX-BUG-004 / map close codes to user-friendly i18n
        // reasons. 4400-4403 each have distinct backend-side meanings;
        // surfacing the close-reason string via specific keys lets
        // operators behind a misconfigured NPM see "X-Forwarded-Host
        // mismatch" instead of just "connection closed".
        const reasonText = (ev.reason || '').toLowerCase();
        if (ev.code === 4400) {
          this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.bad_request');
        } else if (ev.code === 4401) {
          this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.cookie_expired');
        } else if (ev.code === 4403) {
          // 4403 covers both "origin mismatch" and "not admin".
          // The reason string distinguishes them so the operator gets
          // the right diagnostic.
          if (reasonText.includes('origin')) {
            this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.origin_mismatch');
          } else {
            this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.not_admin');
          }
        } else if (ev.code === 4402) {
          this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.csrf_failed');
        } else if (!this.terminalCloseReason && ev.reason) {
          this.terminalCloseReason = String(ev.reason);
        }
      });
      ws.addEventListener('error', () => {
        if (this.terminalState !== 'error' && this.terminalState !== 'disconnected') {
          this.terminalState = 'error';
        }
      });

      // ---- 3) Keystrokes -> WS (binary frames). xterm emits utf-8
      //         strings via onData; encode + send. ----
      const enc = new TextEncoder();
      term.onData((data) => {
        if (!ws || ws.readyState !== 1) return;
        try { ws.send(enc.encode(data)); } catch (_) {}
      });
      term.onResize(({ cols, rows }) => {
        if (!ws || ws.readyState !== 1) return;
        try { ws.send(JSON.stringify({ type: 'resize', cols, rows })); } catch (_) {}
      });

      // ---- 4) Window-resize -> measure-and-resize -> WS resize ----
      const onWinResize = () => {
        try { measureAndResize(); } catch (_) {}
      };
      window.addEventListener('resize', onWinResize);
      this.terminalResizeBound = onWinResize;
      // ---- 5) ResizeObserver on the host element ----
      // Catches dimension changes the window-resize listener misses:
      // the initial modal open (when the deferred rAF retries might
      // still race against in-flight CSS transitions), font-size
      // changes, sidebar / drawer toggles, etc.
      if (typeof window.ResizeObserver === 'function') {
        const obs = new window.ResizeObserver(() => {
          try { measureAndResize(); } catch (_) {}
        });
        try { obs.observe(container); } catch (_) {}
        this.terminalResizeObs = obs;
      }
    },
    _terminalTheme() {
      // Read tokens from the live theme so light vs dark match. No
      // hex fallback — both --terminal-bg and --terminal-fg are
      // declared in BOTH :root blocks; an empty value here would be
      // a token-definition bug we want to see immediately rather
      // than silently mask with a literal that diverges from theme.
      const cs = getComputedStyle(document.documentElement);
      const bg = cs.getPropertyValue('--terminal-bg').trim();
      const fg = cs.getPropertyValue('--terminal-fg').trim();
      return { background: bg, foreground: fg };
    },
    _teardownTerminalSession() {
      // Idempotent — safe to call from multiple paths (close button,
      // Esc key, modal-backdrop click, openHostTerminal switching hosts,
      // beforeunload).
      if (this.terminalResizeBound) {
        try { window.removeEventListener('resize', this.terminalResizeBound); } catch (_) {}
        this.terminalResizeBound = null;
      }
      if (this.terminalResizeObs) {
        try { this.terminalResizeObs.disconnect(); } catch (_) {}
        this.terminalResizeObs = null;
      }
      if (this.terminalFitTimers) {
        for (const id of this.terminalFitTimers) {
          try { clearTimeout(id); } catch (_) {}
        }
        this.terminalFitTimers = null;
      }
      if (this.terminalSocket) {
        try { this.terminalSocket.close(1000, 'client closed'); } catch (_) {}
        this.terminalSocket = null;
      }
      if (this.terminal) {
        try { this.terminal.dispose(); } catch (_) {}
        this.terminal = null;
      }
      this.terminalFit = null;
    },
    closeHostTerminal() {
      this._teardownTerminalSession();
      this.terminalModalOpen = false;
      this.terminalHost = null;
      this.terminalResolved = null;
      this.terminalState = 'connecting';
      this.terminalCloseReason = '';
    },
    reconnectHostTerminal() {
      const host = this.terminalHost;
      if (!host) return;
      // Re-spawn against the same host. _teardownTerminalSession in
      // openHostTerminal handles the cleanup of the previous session.
      this.openHostTerminal(host);
    },
    // Admin → SSH "Test on a host" widget — picks a curated host,
    // runs /ssh/test (whoami), surfaces the result inline.
    async runSshAdminTest() {
      const hostId = (this.sshTestOnHost.host_id || '').trim();
      if (!hostId) {
        this.showToast(this.t('toasts_extra.ssh.command_required'), 'error');
        return;
      }
      this.sshTestOnHost = { ...this.sshTestOnHost, pending: true, result: null };
      try {
        const r = await fetch('/api/hosts/' + encodeURIComponent(hostId) + '/ssh/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        });
        const j = await r.json().catch(() => ({}));
        this.sshTestOnHost = { ...this.sshTestOnHost, pending: false, result: j };
      } catch (e) {
        this.sshTestOnHost = {
          ...this.sshTestOnHost, pending: false,
          result: { ok: false, error: e.message },
        };
      }
    },
    async saveSettings() {
      if (this.settingsSaving) return;  // guard against double-click
      this.settingsSaving = true;
      try {
        // Per-event notification toggles are stored on
        // `settings` as JS booleans (resolved server-side by
        // get_setting_bool) but the SettingsIn validator expects
        // "true"/"false"/""(=clear) strings. Normalise on the way out
        // so the round-trip stays clean and a single Save POST covers
        // BOTH the Apprise URL/tag AND the event grid in this tab.
        const payload = { ...this.settings };
        for (const k of (this.notifyEventKeys || [])) {
          if (k in payload) payload[k] = payload[k] ? 'true' : 'false';
        }
        // Per-medium master switches share the same string-on-the-wire
        // contract (true/false/clear) as the per-event toggles above.
        for (const k of (this.notifyMediumKeys || [])) {
          if (k in payload) payload[k] = payload[k] ? 'true' : 'false';
        }
        const r = await fetch('/api/settings', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify(payload),
        });
        if (!r.ok) throw new Error(await r.text());
        // Re-capture per-tab baselines under the unified dirty-tracking
        // pattern — the Notifications tab (Apprise + Open-Meteo) is the
        // most common caller of saveSettings and lands the whole
        // settings object. Capturing both here covers the multi-section
        // save path; single-section saves (savePortainerSettings,
        // saveOidcSettings) call loadSettings which re-captures via the
        // central baseline-update block.
        this._appriseBaseline   = this._appriseSnapshot();
        this._openMeteoBaseline = this._openMeteoSnapshot();
        // Refresh `/api/me` so the per-user notification panel
        // immediately reflects any admin-side `notify_event_<name>`
        // toggle changes that just landed. Pre-fix the
        // `me.notify_events_admin` map was a stale snapshot from page
        // init, so an admin who disabled an event then switched to
        // their Profile tab still saw the per-user checkbox active —
        // backend rejected an opt-IN attempt with 400 but the UI
        // didn't grey out the control until the next full reload.
        // Now `userNotifyEventDisabledByAdmin` reads the freshest
        // admin gate the moment the admin saves.
        try {
          const rm = await fetch('/api/me', { cache: 'no-store' });
          if (rm.ok) {
            const fresh = await rm.json();
            if (fresh && fresh.authenticated) {
              for (const k of Object.keys(fresh)) this.me[k] = fresh[k];
            }
          }
        } catch (_) { /* non-fatal — the next page load will catch up */ }
        this.showToast(this.t('toasts.settings_saved'));
      } catch (e) { this.showToast(this.t('toasts.load_failed', { error: e.message }), 'error'); }
      finally { this.settingsSaving = false; }
    },
    // Unified dirty-tracking — per-tab snapshot+baseline pattern. The
    // `<X>Dirty()` getter compares the current state against the
    // baseline captured by loadSettings / saveX, so reverting a typed
    // edit clears the indicator. Mirror of profileDirty / assetDirty /
    // hostStatsDirty.
    // The 12 per-event notification keys. Single source of
    // truth for the snapshot, the markup, and the convenience-button
    // helpers — keep in lock-step with logic/ops.py:notify(event=...)
    // and the SettingsIn _NOTIFY_EVENT_KEYS tuple in main.py.
    notifyEventKeys: [
      'notify_event_stack_update_success',
      'notify_event_stack_update_failure',
      'notify_event_container_update_success',
      'notify_event_container_update_failure',
      'notify_event_container_restart_success',
      'notify_event_container_restart_failure',
      'notify_event_container_remove_success',
      'notify_event_container_remove_failure',
      'notify_event_service_restart_success',
      'notify_event_service_restart_failure',
      'notify_event_swarm_agent_restart_success',
      'notify_event_swarm_agent_restart_failure',
      'notify_event_swarm_agent_unhealthy',
      'notify_event_prune_success',
      'notify_event_prune_failure',
      'notify_event_user_login',
      'notify_event_host_paused',
    ],
    // Per-medium master switches. Mirrors `NOTIFY_MEDIUM_NAMES` in
    // logic/ops.py. Adding a third medium adds one entry here +
    // NOTIFY_MEDIUM_NAMES + SettingsIn + api_get_settings hydration
    // (CLAUDE.md "Settings hydration drift class" four-place audit).
    notifyMediumKeys: [
      'notify_medium_app',
      'notify_medium_apprise',
    ],
    // Group rows for the events grid — label key + (success_key,
    // failure_key) pair. Drives the markup render so the table stays
    // declarative.
    notifyEventGroups: [
      { label: 'stack_update',       success: 'notify_event_stack_update_success',       failure: 'notify_event_stack_update_failure' },
      { label: 'container_update',   success: 'notify_event_container_update_success',   failure: 'notify_event_container_update_failure' },
      { label: 'container_restart',  success: 'notify_event_container_restart_success',  failure: 'notify_event_container_restart_failure' },
      { label: 'container_remove',   success: 'notify_event_container_remove_success',   failure: 'notify_event_container_remove_failure' },
      { label: 'service_restart',    success: 'notify_event_service_restart_success',    failure: 'notify_event_service_restart_failure' },
      { label: 'swarm_agent_restart', success: 'notify_event_swarm_agent_restart_success', failure: 'notify_event_swarm_agent_restart_failure' },
      { label: 'prune',              success: 'notify_event_prune_success',              failure: 'notify_event_prune_failure' },
    ],
    // Sampler-style events that don't have a paired success/failure
    // shape but DO surface an unhealthy state. The
    // `swarm_agent_health` schedule kind fires `swarm_agent_unhealthy`
    // when its detection threshold trips and the action is "notify".
    notifyHealthEvents: [
      { label: 'swarm_agent_unhealthy', key: 'notify_event_swarm_agent_unhealthy' },
    ],
    // Security events — single-toggle per event (no success/failure
    // pair like ops events). Rendered as a separate row beneath the
    // ops-events grid. Sampler events get their own group below
    // because "host sampling auto-paused" is not a security signal.
    notifySecurityEvents: [
      { label: 'user_login',  key: 'notify_event_user_login' },
    ],
    notifySamplerEvents: [
      { label: 'host_paused', key: 'notify_event_host_paused' },
    ],
    // Flattened ops-event row list — every (group, kind) becomes one
    // row in the Profile→Notifications grid. Computed once-per-render
    // (no reactive deps; the underlying notifyEventGroups is static)
    // so Alpine's `<template x-for>` can iterate without nested-template
    // ambiguity. Each row carries the event-key, the group label, and
    // the kind (success / failure) so the markup can render the
    // success/failure pill in its own column.
    notifyAllOpsRows() {
      const rows = [];
      for (const g of this.notifyEventGroups) {
        rows.push({ key: g.success, label: g.label, kind: 'success' });
        rows.push({ key: g.failure, label: g.label, kind: 'failure' });
      }
      return rows;
    },
    // ---- Categorised notification list (Profile → Notifications) ----
    // Groups every event into one of three buckets so the per-event
    // matrix scales as more events are added without overwhelming the
    // user with one flat 30-row grid. Each category is rendered as a
    // collapsible card; the category header carries:
    //   - icon + label
    //   - active-count badge (`X / N enabled`)
    //   - one chip per medium showing that category's column state
    //     (all / partial / none) — clicking flips every event in the
    //     category for that medium
    //   - chevron toggling the row list visible
    // The flat per-row matrix moves INSIDE each card. Search, presets,
    // and dirty-tracking continue to operate against the same
    // profileForm.notify_events store.
    notifyCategories() {
      // Memoised — the input arrays (notifyEventGroups,
      // notifySamplerEvents, notifyHealthEvents, notifySecurityEvents)
      // are STATIC at runtime (declared once on the app() data
      // block). Pre-fix this rebuilt the full categories array on
      // every render — called from the x-for in the markup +
      // notifyCategoryStateForMedium + notifyCategoryFilteredRows +
      // notifyCategoryEnabledCount + toggle handlers — at ~5+
      // rebuilds per reactivity tick × 17+ events. Alpine treated
      // each rebuild as a fresh array and tore down / re-created
      // the per-row template (which the project's reactive-array
      // rule explicitly prohibits). Cache once; the static input
      // never changes so the cache never needs invalidation.
      if (this._notifyCategoriesCache) return this._notifyCategoriesCache;
      // Operations rows come from the existing notifyEventGroups
      // (paired success/failure); Health from notifyHealthEvents +
      // notifySamplerEvents (single-toggle health-style events);
      // Security from notifySecurityEvents.
      //
      // Operations also exposes a `groups` array — one entry per
      // operation type (Stack updates / Container updates / Service
      // restarts / etc.) with `{label, success_key, failure_key}` so
      // the markup can render each pair under a small group heading
      // instead of flattening 14 rows into a wall of text. `rows`
      // stays alongside as the legacy flat shape for any consumer
      // that wants the unbucketed list (search filter, count helpers).
      const ops = [];
      const opsGroups = [];
      for (const g of this.notifyEventGroups) {
        ops.push({ key: g.success, label: g.label, kind: 'success' });
        ops.push({ key: g.failure, label: g.label, kind: 'failure' });
        opsGroups.push({
          label: g.label,
          success_key: g.success,
          failure_key: g.failure,
        });
      }
      const health = [];
      for (const e of (this.notifySamplerEvents || [])) {
        health.push({ key: e.key, label: e.label, kind: null });
      }
      for (const e of (this.notifyHealthEvents || [])) {
        health.push({ key: e.key, label: e.label, kind: null });
      }
      const security = [];
      for (const e of (this.notifySecurityEvents || [])) {
        security.push({ key: e.key, label: e.label, kind: null });
      }
      this._notifyCategoriesCache = [
        {
          id: 'operations',
          icon: 'icon-activity',
          label_key: 'profile.notifications.cat_operations',
          desc_key:  'profile.notifications.cat_operations_desc',
          rows: ops,
          groups: opsGroups,
        },
        {
          id: 'health',
          icon: 'icon-alert-triangle',
          label_key: 'profile.notifications.cat_health',
          desc_key:  'profile.notifications.cat_health_desc',
          rows: health,
          groups: null,
        },
        {
          id: 'security',
          icon: 'icon-shield',
          label_key: 'profile.notifications.cat_security',
          desc_key:  'profile.notifications.cat_security_desc',
          rows: security,
          groups: null,
        },
      ];
      return this._notifyCategoriesCache;
    },
    _notifyCategoriesCache: null,
    // Rows that match the current search query (case-insensitive
    // substring against the translated event label OR the underlying
    // event key). Returns the input array unchanged when no search
    // is active so the common case is allocation-free.
    notifyCategoryFilteredRows(cat) {
      const rows = (cat && cat.rows) || [];
      const q = (this.notifySearchQuery || '').trim().toLowerCase();
      if (!q) return rows;
      const out = [];
      for (const r of rows) {
        const label = (this.t('admin.notifications.events.' + r.label) || '').toLowerCase();
        const kind  = r.kind ? (this.t('admin.notifications.events.' + r.kind) || '').toLowerCase() : '';
        if (label.includes(q) || kind.includes(q) || (r.key || '').toLowerCase().includes(q)) {
          out.push(r);
        }
      }
      return out;
    },
    // Operations-category groups filtered by the active search. Each
    // group represents ONE operation type (Stack updates / Container
    // restarts / etc.) with paired `success_key` + `failure_key`.
    // The group is included if its translated label matches OR if
    // either underlying event key/kind matches — so searching
    // "failure" still surfaces every group's failure side instead of
    // collapsing the operation header. Empty list when the category
    // has no `groups` (Health / Security stay flat).
    notifyCategoryFilteredGroups(cat) {
      const groups = (cat && cat.groups) || [];
      const q = (this.notifySearchQuery || '').trim().toLowerCase();
      if (!q) return groups;
      const out = [];
      const successKind = (this.t('admin.notifications.events.success') || '').toLowerCase();
      const failureKind = (this.t('admin.notifications.events.failure') || '').toLowerCase();
      for (const g of groups) {
        const label = (this.t('admin.notifications.events.' + g.label) || '').toLowerCase();
        const sk = (g.success_key || '').toLowerCase();
        const fk = (g.failure_key || '').toLowerCase();
        if (label.includes(q)
            || successKind.includes(q) || failureKind.includes(q)
            || sk.includes(q) || fk.includes(q)) {
          out.push(g);
        }
      }
      return out;
    },
    // Per-(category, medium) state — 'all', 'none', or 'partial'.
    // Drives the chip styling in the category header so the user
    // can see at a glance which mediums the category is fully
    // routing to. Skips admin-disabled events from the count so a
    // greyed row doesn't drag a category to "partial" forever.
    notifyCategoryStateForMedium(cat, medium) {
      const rows = (cat && cat.rows) || [];
      let on = 0, off = 0;
      for (const r of rows) {
        if (this.userNotifyEventDisabledByAdmin(r.key)) continue;
        if (this.userNotifyEventValue(r.key, medium)) on += 1;
        else off += 1;
      }
      if (on === 0 && off === 0) return 'none';
      if (off === 0) return 'all';
      if (on === 0) return 'none';
      return 'partial';
    },
    // Per-(category, medium) dirty marker — true when AT LEAST ONE
    // event in this category has a different routing for this medium
    // than the last-saved baseline (`_profileBaseline`, captured on
    // /api/me load + each successful saveProfile commit). Used by
    // the chip markup to render a small amber dot when the operator's
    // click hasn't yet been Saved, paired with the page-Save's amber
    // dirty ring. Without this, the chip flips to "all" / "none" /
    // "partial" the moment the operator clicks even though the change
    // hasn't been persisted — if Save fails (network blip), the chip
    // would lie about the persisted state.
    notifyCategoryDirtyForMedium(cat, medium) {
      const rows = (cat && cat.rows) || [];
      if (rows.length === 0) return false;
      let baseEvents = null;
      try {
        baseEvents = (JSON.parse(this._profileBaseline || '{}').notify_events) || {};
      } catch { return false; }
      const currentEvents = (this.profileForm && this.profileForm.notify_events) || {};
      for (const r of rows) {
        if (this.userNotifyEventDisabledByAdmin(r.key)) continue;
        // Resolve current value via the same helper the chip's state
        // uses so admin-default fallbacks compose identically.
        const cur = !!this.userNotifyEventValue(r.key, medium);
        // Resolve baseline value: same shape the helper expects, but
        // against the last-saved snapshot.
        const basePref = baseEvents[r.key];
        let base;
        if (basePref === undefined || basePref === null) {
          // Fall back to the admin default (which the chip would also
          // surface via userNotifyEventValue when the user has no
          // explicit pref).
          base = !!this.userNotifyEventValue(r.key, medium);
        } else if (typeof basePref === 'boolean') {
          base = !!basePref;
        } else if (typeof basePref === 'object') {
          // Per-medium dict shape. Missing key defaults to true (matches
          // the live helper's contract for unrecognised medium keys).
          base = basePref[medium] === undefined ? true : !!basePref[medium];
        } else {
          base = false;
        }
        if (cur !== base) return true;
      }
      return false;
    },
    notifyCategoryEnabledCount(cat) {
      const rows = (cat && cat.rows) || [];
      let total = 0, on = 0;
      const mediums = this.notifyMediumNames();
      for (const r of rows) {
        if (this.userNotifyEventDisabledByAdmin(r.key)) continue;
        total += 1;
        // "Enabled" for the count = at least one medium routes the event.
        for (const m of mediums) {
          if (this.userNotifyEventValue(r.key, m)) { on += 1; break; }
        }
      }
      return { on, total };
    },
    // Toggle every event in a category for ONE medium. Skips
    // admin-disabled events. Click on the medium chip in the
    // category header.
    toggleNotifyCategoryMedium(catId, medium, nextValue) {
      const cat = (this.notifyCategories() || []).find(c => c.id === catId);
      if (!cat) return;
      // Short-circuit if the medium is admin-disabled globally.
      // The chip's CSS already shows strikethrough + cursor:
      // not-allowed, but the click handler still fires unless we
      // gate here — so an operator click on a disabled chip would
      // silently mutate user prefs that don't take effect until
      // the admin re-enables the medium globally, leaving stale
      // routing landing without the operator noticing. Both
      // helpers (Medium toggle + the All toggle) gate on the
      // same predicate.
      if (!this.notifyMediumIsGloballyEnabled(medium)) return;
      // Resolve the next value: if not supplied, flip based on
      // current state (all → none, partial/none → all).
      let v = nextValue;
      if (v === undefined) {
        const state = this.notifyCategoryStateForMedium(cat, medium);
        v = (state !== 'all');
      }
      const f = this.profileForm || {};
      if (!f.notify_events) f.notify_events = {};
      const mediums = this.notifyMediumNames();
      for (const r of cat.rows) {
        if (this.userNotifyEventDisabledByAdmin(r.key)) continue;
        const bare = this._bareEventName(r.key);
        let slot = f.notify_events[bare];
        if (!slot || typeof slot !== 'object') {
          const prev = !!slot;
          slot = {};
          for (const mm of mediums) slot[mm] = prev;
          f.notify_events[bare] = slot;
        }
        slot[medium] = !!v;
      }
    },
    // Toggle EVERY medium for every event in the category — the
    // "All" master chip per category.
    toggleNotifyCategoryAll(catId, nextValue) {
      const cat = (this.notifyCategories() || []).find(c => c.id === catId);
      if (!cat) return;
      let v = nextValue;
      if (v === undefined) {
        // Flip based on whether ANY medium is "all" — defaults to
        // turning everything ON when the category is mixed/off.
        const mediums = this.notifyMediumNames();
        const allOn = mediums.every(m => this.notifyCategoryStateForMedium(cat, m) === 'all');
        v = !allOn;
      }
      const f = this.profileForm || {};
      if (!f.notify_events) f.notify_events = {};
      const mediums = this.notifyMediumNames();
      // Filter the medium list to only globally-enabled channels —
      // skip flipping prefs for an admin-disabled medium since that
      // setting can't take effect until the admin re-enables and
      // we don't want stale routing silently sitting on the user's
      // profile. Same gate as toggleNotifyCategoryMedium.
      const liveMediums = mediums.filter(m => this.notifyMediumIsGloballyEnabled(m));
      for (const r of cat.rows) {
        if (this.userNotifyEventDisabledByAdmin(r.key)) continue;
        const bare = this._bareEventName(r.key);
        // Preserve any pre-existing per-medium values for
        // admin-disabled channels — only mutate the live ones. This
        // keeps the operator's prior choice on a disabled medium
        // intact for when the admin re-enables it.
        const existing = (f.notify_events[bare] && typeof f.notify_events[bare] === 'object')
          ? f.notify_events[bare] : {};
        const slot = { ...existing };
        for (const m of liveMediums) slot[m] = !!v;
        f.notify_events[bare] = slot;
      }
    },
    notifyCategoryAllState(cat) {
      const mediums = this.notifyMediumNames();
      const states = mediums.map(m => this.notifyCategoryStateForMedium(cat, m));
      if (states.every(s => s === 'all'))  return 'all';
      if (states.every(s => s === 'none')) return 'none';
      return 'partial';
    },
    isNotifyCategoryExpanded(catId) {
      // Default-expanded for the FIRST category (operations) so
      // first-time users see the rows immediately; the rest collapse
      // to keep the panel short.
      if (this.notifyCategoryExpanded[catId] === undefined) {
        return catId === 'operations';
      }
      return !!this.notifyCategoryExpanded[catId];
    },
    toggleNotifyCategoryExpanded(catId) {
      const cur = this.isNotifyCategoryExpanded(catId);
      this.notifyCategoryExpanded = {
        ...this.notifyCategoryExpanded,
        [catId]: !cur,
      };
    },
    expandAllNotifyCategories(value) {
      const v = (value !== false);
      const next = {};
      for (const c of this.notifyCategories()) next[c.id] = v;
      this.notifyCategoryExpanded = next;
    },
    _appriseSnapshot() {
      const s = this.settings || {};
      const events = {};
      for (const k of this.notifyEventKeys) events[k] = !!s[k];
      const mediums = {};
      for (const k of (this.notifyMediumKeys || [])) mediums[k] = !!s[k];
      // Notifications-panel-scoped tunables. Pre-fix these were edited
      // through their own auto-rendered Admin → Config form which had
      // its own Save flow; the operator wants them flush against the
      // Notifications panel's Save button so toggling
      // `tuning_notification_retention_days` /
      // `tuning_notification_page_size` flips the same amber ring as
      // the per-event toggles. Reading from `tuningForm` keeps the
      // shape consistent with the auto-rendered Config form;
      // saveSettings POSTs both the per-event keys AND the tunables
      // through SettingsIn so the round-trip stays clean.
      const tf = this.tuningForm || {};
      const notifTunables = {
        retention: (tf.tuning_notification_retention_days ?? '').toString(),
        page_size: (tf.tuning_notification_page_size ?? '').toString(),
      };
      return JSON.stringify({
        enabled: !!s.apprise_enabled,
        url:     s.apprise_url || '',
        tag:     s.apprise_tag || '',
        events,
        mediums,
        notifTunables,
      });
    },
    appriseDirty()   { return this._appriseBaseline   !== this._appriseSnapshot(); },
    // Convenience-button handlers for the per-event grid. They mutate
    // settings in-place; the smart-getter dirty pattern picks the
    // change up automatically. Save still goes through the existing
    // Apprise Save button (saveSettings).
    setAllNotifyEvents(value) {
      const v = !!value;
      for (const k of this.notifyEventKeys) this.settings[k] = v;
    },
    setNotifyEventsErrorsOnly() {
      // Errors-only = failure true, success false for every group.
      for (const g of this.notifyEventGroups) {
        this.settings[g.success] = false;
        this.settings[g.failure] = true;
      }
    },
    // User-side convenience handlers — operate on profileForm.notify_events
    // (per-user opt-in map keyed by BARE event name — no
    // notify_event_ prefix). Per-medium granularity: each event's value
    // is `{medium: bool}` after syncProfileForm normalises it, so each
    // helper writes uniformly across every medium that's globally
    // enabled. Admin-disabled events skip — the backend rejects an
    // opt-in attempt for them with 400.
    _bareEventName(k) { return String(k || '').replace(/^notify_event_/, ''); },
    // Resolved medium list — backend hands the SPA a roster on every
    // /api/me load. Falls back to the canonical `[app, apprise]` pair
    // for older deploys / first-paint before /api/me lands.
    notifyMediumNames() {
      // Memoised — input is `this.me.notify_mediums` which is set
      // ONCE per /api/me round-trip. Pre-fix the helper rebuilt the
      // names array on every render call (Profile-panel x-for + 5
      // helper sites). Cache key is the underlying me-object identity
      // so a fresh /api/me response (which Object.assign-mutates
      // this.me) busts automatically when the array changes.
      const list = (this.me && Array.isArray(this.me.notify_mediums))
        ? this.me.notify_mediums : null;
      if (this._notifyMediumNamesCacheList === list && this._notifyMediumNamesCache) {
        return this._notifyMediumNamesCache;
      }
      this._notifyMediumNamesCacheList = list;
      this._notifyMediumNamesCache = (list && list.length)
        ? list.map(m => m.name)
        : ['app', 'apprise'];
      return this._notifyMediumNamesCache;
    },
    _notifyMediumNamesCache: null,
    _notifyMediumNamesCacheList: null,
    notifyMediumIsGloballyEnabled(name) {
      const list = (this.me && Array.isArray(this.me.notify_mediums))
        ? this.me.notify_mediums : null;
      if (!list) return true;  // optimistic — wait for /api/me
      const row = list.find(m => m.name === name);
      return row ? !!row.enabled : true;
    },
    // Read / write a single (event, medium) checkbox.
    userNotifyEventValue(eventKey, medium) {
      const bare = this._bareEventName(eventKey);
      const slot = (this.profileForm && this.profileForm.notify_events)
        ? this.profileForm.notify_events[bare] : null;
      if (slot && typeof slot === 'object') {
        // Missing medium key defaults to true (matches dispatcher's
        // default-on-missing-medium contract).
        return (slot[medium] !== false);
      }
      // Defensive: profileForm not yet hydrated.
      return false;
    },
    setUserNotifyEventValue(eventKey, medium, value) {
      const bare = this._bareEventName(eventKey);
      const f = this.profileForm || {};
      if (!f.notify_events) f.notify_events = {};
      if (!f.notify_events[bare] || typeof f.notify_events[bare] !== 'object') {
        // Coerce legacy bare-bool slot into the per-medium dict shape so
        // the rest of the form sees a uniform structure.
        const prev = !!f.notify_events[bare];
        const slot = {};
        for (const m of this.notifyMediumNames()) slot[m] = prev;
        f.notify_events[bare] = slot;
      }
      f.notify_events[bare][medium] = !!value;
    },
    // Toggle every medium for a given event in one click — clicking
    // the event label itself acts as a row-level master switch.
    toggleUserNotifyEventRow(eventKey, value) {
      if (this.userNotifyEventDisabledByAdmin(eventKey)) return;
      const bare = this._bareEventName(eventKey);
      const f = this.profileForm || {};
      if (!f.notify_events) f.notify_events = {};
      const slot = {};
      for (const m of this.notifyMediumNames()) slot[m] = !!value;
      f.notify_events[bare] = slot;
    },
    // Toggle every event for a given medium in one click — clicking
    // the medium column header acts as a column-level master switch.
    // Skips admin-disabled events.
    toggleUserNotifyMediumColumn(medium, value) {
      const f = this.profileForm || {};
      if (!f.notify_events) f.notify_events = {};
      for (const k of this.notifyEventKeys) {
        if (this.userNotifyEventDisabledByAdmin(k)) continue;
        const bare = this._bareEventName(k);
        if (!f.notify_events[bare] || typeof f.notify_events[bare] !== 'object') {
          const prev = !!f.notify_events[bare];
          const slot = {};
          for (const m of this.notifyMediumNames()) slot[m] = prev;
          f.notify_events[bare] = slot;
        }
        f.notify_events[bare][medium] = !!value;
      }
    },
    // Returns true when the event is enabled across EVERY medium —
    // used to render the row's master-toggle in its checked state.
    userNotifyEventRowAll(eventKey) {
      const bare = this._bareEventName(eventKey);
      const slot = (this.profileForm && this.profileForm.notify_events)
        ? this.profileForm.notify_events[bare] : null;
      if (!slot || typeof slot !== 'object') return false;
      const mediums = this.notifyMediumNames();
      for (const m of mediums) {
        if (slot[m] === false) return false;
      }
      return true;
    },
    setAllUserNotifyEvents(value) {
      const v = !!value;
      const f = this.profileForm || {};
      if (!f.notify_events) f.notify_events = {};
      const mediums = this.notifyMediumNames();
      for (const k of this.notifyEventKeys) {
        if (this.userNotifyEventDisabledByAdmin(k)) continue;
        const bare = this._bareEventName(k);
        const slot = {};
        for (const m of mediums) slot[m] = v;
        f.notify_events[bare] = slot;
      }
    },
    setUserNotifyEventsErrorsOnly() {
      const f = this.profileForm || {};
      if (!f.notify_events) f.notify_events = {};
      const mediums = this.notifyMediumNames();
      for (const g of this.notifyEventGroups) {
        if (!this.userNotifyEventDisabledByAdmin(g.success)) {
          const bareS = this._bareEventName(g.success);
          const slot = {};
          for (const m of mediums) slot[m] = false;
          f.notify_events[bareS] = slot;
        }
        if (!this.userNotifyEventDisabledByAdmin(g.failure)) {
          const bareF = this._bareEventName(g.failure);
          const slot = {};
          for (const m of mediums) slot[m] = true;
          f.notify_events[bareF] = slot;
        }
      }
    },
    // Bulk-pattern picker state — operator selects one medium for
    // "all success events" and another for "all failure events" via
    // the picker rendered under the Errors-only button. Empty string
    // = "leave the corresponding side untouched". Persisted in-memory
    // only (the row's persistence happens via the PATCH that
    // saveProfile fires on the next save).
    // Profile → Notifications redesign state (#993). Search filters
    // event rows live; per-category expand/collapse map keyed by
    // category id (default-expanded for 'operations' so first-time
    // users see rows immediately).
    notifySearchQuery: '',
    notifyCategoryExpanded: {},
    userNotifyBulkSuccessMedium: '',
    userNotifyBulkFailureMedium: '',
    // Notification template editor state. ONE shared model drives BOTH
    // admin-edit mode (writable) AND profile-side viewer mode (read-only).
    // `event` is the bare event name (no `notify_event_` prefix). The
    // editor is opened via openNotifyTemplateEditor / openNotifyTemplateViewer
    // and closed via closeNotifyTemplateEditor (Esc / backdrop / Close).
    // `preview` is refreshed by a 200ms-debounced call to
    // refreshNotifyTemplatePreview() — server-rendered so the typo-
    // detection logic stays in one place.
    notifyTemplateEditor: {
      open: false,
      readOnly: false,
      event: '',           // bare event name
      title: '',           // current edit value (empty = use default)
      body: '',
      title_default: '',   // hard-coded default (placeholder hint + reset target)
      body_default: '',
      title_baseline: '',  // baseline for dirty tracking; captured on open
      body_baseline: '',
      available_placeholders: [],
      samples: {},
      preview: { rendered_title: '', rendered_body: '', used_placeholders: [], unknown_placeholders: [] },
      saving: false,
      testing: false,
    },
    notifyTemplateEvent: null,  // {label, key, kind?} resolved at open time

    // ---- Notification template editor handlers ----
    // Locate the {label, kind} metadata for ONE bare event name so the
    // modal's header chip + i18n can render the human-readable bits.
    // Walks the same notifyEventGroups / notifyHealthEvents / etc.
    // arrays the rest of the SPA uses, so a new event added in any of
    // those four arrays automatically resolves a label here too.
    _resolveNotifyTemplateEvent(bareEventName) {
      const fullKey = 'notify_event_' + bareEventName;
      for (const g of (this.notifyEventGroups || [])) {
        if (g.success === fullKey) return { label: g.label, kind: 'success', key: fullKey };
        if (g.failure === fullKey) return { label: g.label, kind: 'failure', key: fullKey };
      }
      for (const e of (this.notifyHealthEvents || [])) {
        if (e.key === fullKey) return { label: e.label, kind: null, key: fullKey };
      }
      for (const e of (this.notifySecurityEvents || [])) {
        if (e.key === fullKey) return { label: e.label, kind: null, key: fullKey };
      }
      for (const e of (this.notifySamplerEvents || [])) {
        if (e.key === fullKey) return { label: e.label, kind: null, key: fullKey };
      }
      // Fallback: render the bare event name verbatim if our static
      // arrays don't know about it (audit gate already logs a WARN
      // line in that case).
      return { label: bareEventName, kind: null, key: fullKey };
    },
    // Open the editor in READ-WRITE mode (admin only — a non-admin
    // who somehow lands here gets the read-only render via isAdmin
    // gating in the modal markup, but we guard at the entry point too
    // for defence in depth).
    async openNotifyTemplateEditor(bareEventName) {
      if (!this.isAdmin()) {
        return this.openNotifyTemplateViewer(bareEventName);
      }
      await this._loadAndShowNotifyTemplate(bareEventName, false);
    },
    // Open the editor in READ-ONLY mode for users to inspect what
    // template fires for a given event. Same modal, different mode.
    async openNotifyTemplateViewer(bareEventName) {
      await this._loadAndShowNotifyTemplate(bareEventName, true);
    },
    async _loadAndShowNotifyTemplate(bareEventName, readOnly) {
      try {
        const r = await fetch('/api/admin/notify-templates');
        if (!r.ok) {
          // Read-only mode: a non-admin profile-popup user can't hit
          // the admin endpoint. The endpoint is admin-only by design;
          // for read-only we fall back to a minimal client-side
          // shape (just renders the bare event with no template).
          if (readOnly && r.status === 403) {
            this._showMinimalNotifyTemplateViewer(bareEventName);
            return;
          }
          throw new Error(`HTTP ${r.status}`);
        }
        const d = await r.json();
        const events = (d && d.events) || [];
        const ev = events.find(e => e.event === bareEventName);
        if (!ev) {
          if (window.Swal) Swal.fire({ icon: 'error', text: this.t('admin.notify_templates.unknown_event_error', { event: bareEventName }) });
          return;
        }
        this.notifyTemplateEvent = this._resolveNotifyTemplateEvent(bareEventName);
        const title = ev.title_is_default ? '' : (ev.title || '');
        const body  = ev.body_is_default  ? '' : (ev.body  || '');
        this.notifyTemplateEditor = {
          open: true,
          readOnly: !!readOnly,
          event: bareEventName,
          title: title,
          body: body,
          title_default: ev.title_default || '',
          body_default: ev.body_default || '',
          title_baseline: title,
          body_baseline: body,
          available_placeholders: d.available_placeholders || [],
          samples: d.samples || {},
          preview: { rendered_title: '', rendered_body: '', used_placeholders: [], unknown_placeholders: [] },
          saving: false,
          testing: false,
        };
        // First preview render — server side so we get the same
        // placeholder analysis the operator will see during edits.
        this.refreshNotifyTemplatePreview();
      } catch (e) {
        if (window.Swal) Swal.fire({ icon: 'error', text: this.t('admin.notify_templates.load_failed', { error: String(e.message || e) }) });
      }
    },
    // Defence-in-depth fallback when the admin-only list endpoint
    // 403s (a non-admin clicking the profile-popup info-icon while
    // tab-permissions are flapping). Renders the modal with no
    // template content so the user sees "No template configured"
    // instead of a stuck loading state.
    _showMinimalNotifyTemplateViewer(bareEventName) {
      this.notifyTemplateEvent = this._resolveNotifyTemplateEvent(bareEventName);
      this.notifyTemplateEditor = {
        open: true,
        readOnly: true,
        event: bareEventName,
        title: '',
        body: '',
        title_default: '',
        body_default: '',
        title_baseline: '',
        body_baseline: '',
        available_placeholders: [],
        samples: {},
        preview: { rendered_title: '', rendered_body: '', used_placeholders: [], unknown_placeholders: [] },
        saving: false,
        testing: false,
      };
    },
    closeNotifyTemplateEditor() {
      this.notifyTemplateEditor.open = false;
      // Reset the saving flags so the next open doesn't carry stale
      // state if a save was in flight at close-time.
      this.notifyTemplateEditor.saving = false;
      this.notifyTemplateEditor.testing = false;
    },
    notifyTemplateEditorIsDirty() {
      const e = this.notifyTemplateEditor || {};
      return (e.title || '') !== (e.title_baseline || '')
          || (e.body || '')  !== (e.body_baseline || '');
    },
    resetNotifyTemplateEditor() {
      // Resets the editor's title + body to empty (which the backend
      // treats as "fall back to default"). Operator still has to click
      // Save to commit; before that, the live preview shows what the
      // default looks like (matches the baseline-hydrated `_default`
      // strings).
      this.notifyTemplateEditor.title = '';
      this.notifyTemplateEditor.body  = '';
      this.refreshNotifyTemplatePreview();
    },
    // Insert a `{placeholder}` literal at the caret position of the
    // appropriate textarea. Falls back to appending if the caret can't
    // be read (textarea hasn't been focused yet). Refreshes preview
    // immediately so the chip click feels responsive.
    insertNotifyTemplatePlaceholder(field, placeholder) {
      const ref = field === 'title'
        ? this.$refs.notifyTemplateTitleInput
        : this.$refs.notifyTemplateBodyInput;
      const insert = '{' + placeholder + '}';
      const cur = (this.notifyTemplateEditor[field] || '');
      if (!ref) {
        this.notifyTemplateEditor[field] = cur + insert;
      } else {
        const start = ref.selectionStart != null ? ref.selectionStart : cur.length;
        const end   = ref.selectionEnd   != null ? ref.selectionEnd   : cur.length;
        const next = cur.slice(0, start) + insert + cur.slice(end);
        this.notifyTemplateEditor[field] = next;
        // Restore the caret position to AFTER the insert so subsequent
        // chip clicks chain naturally.
        this.$nextTick(() => {
          if (ref) {
            ref.focus();
            const newPos = start + insert.length;
            try { ref.setSelectionRange(newPos, newPos); } catch (_e) { /* ignore */ }
          }
        });
      }
      this.refreshNotifyTemplatePreview();
    },
    async refreshNotifyTemplatePreview() {
      const e = this.notifyTemplateEditor;
      if (!e || !e.event) return;
      // Send the IN-PROGRESS strings (or empty → server falls back to
      // default at render time). Server is the single source of truth
      // for placeholder validation (curated whitelist lives in
      // logic/ops.py); avoid duplicating the regex on the client.
      const titleStr = e.title || e.title_default || '';
      const bodyStr  = e.body  || e.body_default  || '';
      try {
        const r = await fetch(
          '/api/admin/notify-templates/' + encodeURIComponent(e.event) + '/preview',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: titleStr, body: bodyStr }),
          },
        );
        if (!r.ok) {
          // Read-only mode 403s on a non-admin caller. Render a
          // best-effort client-side preview so the modal still shows
          // something; the unknown-placeholder warning won't fire,
          // which is fine for the read-only case.
          if (r.status === 403) {
            const safe = (s) => (s || '').replace(/\{[^}]*\}/g, (tok) => tok);
            this.notifyTemplateEditor.preview = {
              rendered_title: safe(titleStr),
              rendered_body:  safe(bodyStr),
              used_placeholders: [],
              unknown_placeholders: [],
            };
            return;
          }
          throw new Error('HTTP ' + r.status);
        }
        const d = await r.json();
        // Mutate-in-place so Alpine effect dependents (the live preview
        // pane) re-render without reassigning the whole `notifyTemplateEditor`.
        this.notifyTemplateEditor.preview = {
          rendered_title: d.rendered_title || '',
          rendered_body:  d.rendered_body  || '',
          used_placeholders:    d.used_placeholders    || [],
          unknown_placeholders: d.unknown_placeholders || [],
        };
      } catch (err) {
        // Silent on transient probe errors — preview stays at last good
        // state. Console line for diagnostics.
        console.warn('[notify-templates] preview fetch failed:', err);
      }
    },
    async saveNotifyTemplate() {
      const e = this.notifyTemplateEditor;
      if (!e || !e.event || e.readOnly) return;
      e.saving = true;
      try {
        const r = await fetch(
          '/api/admin/notify-templates/' + encodeURIComponent(e.event),
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: e.title || '', body: e.body || '' }),
          },
        );
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const d = await r.json();
        // Refresh the editor's baseline + defaults from the server
        // response so the dirty flag clears AND the placeholder hints
        // reflect what's now in the DB.
        this.notifyTemplateEditor.title          = d.title_is_default ? '' : (d.title || '');
        this.notifyTemplateEditor.body           = d.body_is_default  ? '' : (d.body  || '');
        this.notifyTemplateEditor.title_default  = d.title_default || '';
        this.notifyTemplateEditor.body_default   = d.body_default || '';
        this.notifyTemplateEditor.title_baseline = this.notifyTemplateEditor.title;
        this.notifyTemplateEditor.body_baseline  = this.notifyTemplateEditor.body;
        if (window.Swal) {
          Swal.fire({
            icon: 'success',
            title: this.t('admin.notify_templates.saved_toast'),
            timer: 1400,
            showConfirmButton: false,
            toast: true,
            position: 'bottom-end',
          });
        }
      } catch (err) {
        if (window.Swal) Swal.fire({ icon: 'error', text: this.t('admin.notify_templates.save_failed', { error: String(err.message || err) }) });
      } finally {
        this.notifyTemplateEditor.saving = false;
      }
    },
    async testNotifyTemplate() {
      const e = this.notifyTemplateEditor;
      if (!e || !e.event || e.readOnly) return;
      e.testing = true;
      try {
        const r = await fetch(
          '/api/admin/notify-templates/' + encodeURIComponent(e.event) + '/test',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: e.title || '', body: e.body || '' }),
          },
        );
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.ok === false) {
          throw new Error(d.error || ('HTTP ' + r.status));
        }
        if (window.Swal) {
          Swal.fire({
            icon: 'success',
            title: this.t('admin.notify_templates.test_sent'),
            text: d.rendered_title || '',
            timer: 2200,
            showConfirmButton: false,
            toast: true,
            position: 'bottom-end',
          });
        }
        // After test: mutate baseline so any saved-during-test value
        // is now the new baseline (the test endpoint persists what
        // the operator typed before firing).
        this.notifyTemplateEditor.title_baseline = this.notifyTemplateEditor.title || '';
        this.notifyTemplateEditor.body_baseline  = this.notifyTemplateEditor.body  || '';
      } catch (err) {
        if (window.Swal) Swal.fire({ icon: 'error', text: this.t('admin.notify_templates.test_failed', { error: String(err.message || err) }) });
      } finally {
        this.notifyTemplateEditor.testing = false;
      }
    },
    // One-click bulk-set: route every success event to
    // ``successMedium`` AND every failure event to ``failureMedium``.
    // Either side can be empty — that side is left untouched. The
    // chosen medium is the ONLY one enabled for those events; every
    // other medium for the same event is set to false (matching the
    // Errors-only pattern's all-or-nothing-per-medium contract).
    // Skips events the admin has globally disabled (the backend would
    // 400 such an opt-in anyway; see api_me_notify_prefs).
    setUserNotifyEventsByMediumPattern(successMedium, failureMedium) {
      const f = this.profileForm || {};
      if (!f.notify_events) f.notify_events = {};
      const mediums = this.notifyMediumNames();
      const apply = (eventKey, chosen) => {
        if (this.userNotifyEventDisabledByAdmin(eventKey)) return;
        const bare = this._bareEventName(eventKey);
        const slot = {};
        for (const m of mediums) slot[m] = (m === chosen);
        f.notify_events[bare] = slot;
      };
      for (const g of this.notifyEventGroups) {
        if (successMedium) apply(g.success, successMedium);
        if (failureMedium) apply(g.failure, failureMedium);
      }
    },
    _debugSnapshot() {
      const s = this.settings || {};
      return JSON.stringify({
        enabled: !!s.debug_panel_enabled,
      });
    },
    debugDirty()     { return this._debugBaseline     !== this._debugSnapshot(); },
    _totpPolicySnapshot() {
      const s = this.settings || {};
      return JSON.stringify({
        allowed:                 !!s.totp_allowed,
        required_for_admins:     !!s.totp_required_for_admins,
        required_for_users:      !!s.totp_required_for_users,
        lockout_max_failures:    +s.totp_lockout_max_failures || 5,
        lockout_minutes:         +s.totp_lockout_minutes || 15,
        // Passkey master toggle joins the same dirty/baseline group
        // as the TOTP policy fields so a single Save covers both
        // sub-systems.
        passkeys_allowed:        !!s.passkeys_allowed,
      });
    },
    totpPolicyDirty() { return this._totpPolicyBaseline !== this._totpPolicySnapshot(); },
    _openMeteoSnapshot() {
      const s = this.settings || {};
      return JSON.stringify({
        enabled: !!s.open_meteo_enabled,
        url:     (s.open_meteo_url || '').trim().replace(/\/+$/, ''),
      });
    },
    openMeteoDirty() { return this._openMeteoBaseline !== this._openMeteoSnapshot(); },
    _portainerSnapshot() {
      const f = this.portainerForm || {};
      const s = this.portainerStatus || {};
      // Form vs status. api_key is write-only — any typed value (any
      // non-empty string) is dirty; baseline always has empty api_key.
      return JSON.stringify({
        enabled:     !!(this.settings || {}).portainer_enabled,
        url:         (f.url || '').trim(),
        endpoint_id: f.endpoint_id || 1,
        verify_tls:  !!f.verify_tls,
        api_key:     f.api_key ? '<set>' : '',
        baseEnabled: !!s.enabled,
        baseUrl:     s.url || '',
        baseEpId:    s.endpoint_id || 1,
        baseVerify:  !!s.verify_tls,
        publicUrl:   (this.settings || {}).portainer_public_url || '',
        basePublic:  this._portainerPublicBaseline || '',
        // Swarm autoheal action lives in the Portainer panel
        // (the action targets Portainer's agent service, not a
        // notifications routing concern). Folded into this snapshot
        // so toggling the dropdown flips the Portainer Save button's
        // amber ring.
        autoheal:    (this.settings || {}).swarm_autoheal_action || 'notify',
        // First-boot auto-bootstrap toggle for the default
        // swarm_agent_health schedule. Folded in alongside the action
        // selector so toggling either dirties the Portainer panel.
        autohealBootstrap: !!(this.settings || {}).swarm_autoheal_bootstrap_enabled,
      });
    },
    portainerDirty() { return this._portainerBaseline !== this._portainerSnapshot(); },
    _oidcSnapshot() {
      const f = this.oidcForm || {};
      const s = this.oidcStatus || {};
      return JSON.stringify({
        enabled:     !!f.enabled,
        issuer:      (f.issuer_url || '').trim(),
        client_id:   (f.client_id || '').trim(),
        secret:      f.client_secret ? '<set>' : '',
        redirect:    (f.redirect_uri || '').trim(),
        scopes:      (f.scopes || '').trim(),
        admin_group: (f.admin_group || '').trim(),
        verify_tls:  f.verify_tls !== false,
        group_case_sensitive: f.group_case_sensitive !== false,
        baseEnabled: !!s.enabled,
        baseIssuer:  s.issuer_url || '',
        baseCid:     s.client_id || '',
        baseRedir:   s.redirect_uri || '',
        baseScopes:  s.scopes || '',
        baseGrp:     s.admin_group || '',
        baseVerify:  s.verify_tls !== false,
        baseGroupCS: s.group_case_sensitive !== false,
      });
    },
    oidcDirty()      { return this._oidcBaseline      !== this._oidcSnapshot(); },
    // Test-before-Save snapshot for Asset Inventory. Mirrors the
    // Portainer / OIDC `_xSnapshot()` pattern but covers BOTH auth
    // modes (oauth2 and lifetime_token) so an admin can Test in either
    // shape and the snapshot matches at Save time. Secrets follow the
    // write-only contract — any non-empty form value is treated as a
    // pending write (encoded as the marker `<set>`); blank means
    // "keep current".
    _assetSnapshot() {
      const f = this.assetForm || {};
      const s = this.assetStatus || {};
      const mode = (f.auth_mode === 'lifetime_token') ? 'lifetime_token' : 'oauth2';
      return JSON.stringify({
        enabled:    !!(this.settings || {}).asset_inventory_enabled,
        auth_mode:  mode,
        base_url:   (f.base_url || '').trim(),
        token_url:  (f.token_url || '').trim(),
        client_id:  (f.client_id || '').trim(),
        scope:      (f.scope || '').trim(),
        client_secret:  f.client_secret  ? '<set>' : '',
        lifetime_token: f.lifetime_token ? '<set>' : '',
        service:    (f.service || '').trim(),
        action:     (f.action  || '').trim(),
        min_value:  String(f.min_value ?? '').trim(),
        max_value:  String(f.max_value ?? '').trim(),
        edit_url:   (f.edit_url_template || '').trim(),
        verify_tls: !!f.verify_tls,
        baseEnabled:   (s.enabled !== false),
        baseAuth:      (s.auth_mode === 'lifetime_token') ? 'lifetime_token' : 'oauth2',
        baseBaseUrl:   s.base_url  || '',
        baseTokenUrl:  s.token_url || '',
        baseClientId:  s.client_id || '',
        baseScope:     s.scope     || '',
        baseService:   s.service   || '',
        baseAction:    s.action    || '',
        baseMin:       (s.min_value != null) ? String(s.min_value) : '',
        baseMax:       (s.max_value != null) ? String(s.max_value) : '',
        baseEditUrl:   s.edit_url_template || '',
        baseVerify:    (s.verify_tls !== false),
      });
    },
    // Save-button gate for Asset Inventory — same shape as
    // `canSavePortainer()` / `canSaveOidc()`. When the master toggle
    // is OFF, Save is unconstrained (no upstream probe will run). When
    // ON, the operator must run a successful Test against the CURRENT
    // form values before Save unlocks; any edit after a passing Test
    // re-locks Save by mutating `_assetSnapshot()` away from
    // `_assetLastPassedTest`.
    canSaveAsset() {
      if (!(this.settings || {}).asset_inventory_enabled) return true;
      return this._assetLastPassedTest === this._assetSnapshot()
             && !!this._assetLastPassedTest;
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
    async loadTuning() {
      try {
        const r = await fetch('/api/admin/tuning');
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        this.tuningEffective = d || {};
        const form = {};
        for (const k of this._allTuningKeys()) {
          const row = (d || {})[k] || {};
          form[k] = (row.db == null || row.db === '') ? '' : String(row.db);
        }
        this.tuningForm = form;
        this._tuningBaseline = this._tuningSnapshot();
        this.tuningLoaded = true;
      } catch (e) {
        this.showToast(this.t('admin.config.load_failed', { error: e.message }), 'error');
      }
    },
    _tuningSnapshot() {
      const f = this.tuningForm || {};
      const out = {};
      for (const k of this._allTuningKeys()) out[k] = (f[k] == null ? '' : String(f[k]).trim());
      return JSON.stringify(out);
    },
    tuningDirty() { return this._tuningBaseline !== this._tuningSnapshot(); },
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
      const labelOf = (k) => {
        const lbl = this.t('admin.config.fields.' + k + '.label');
        // Missing-key fallback returns the path itself (per the i18n
        // helper's contract); detect that and use the bare key so
        // the sort doesn't bunch every untranslated row at the top.
        return (lbl && lbl !== 'admin.config.fields.' + k + '.label') ? lbl : k;
      };
      return keys.sort((a, b) => labelOf(a).localeCompare(labelOf(b)));
    },
    tuningPlaceholder(key) {
      const row = (this.tuningEffective || {})[key] || {};
      const env = row.env;
      const def = (row.default == null ? '' : String(row.default));
      if (env != null && String(env).trim() !== '') {
        return this.t('admin.config.placeholder_env', { value: env, default: def });
      }
      return this.t('admin.config.placeholder_default', { default: def });
    },
    async saveTuning() {
      if (this.tuningSaving) return;
      // UX-BUG-005 / client-side integer + bounds validation
      // before posting. Pre-fix the input was `type="number"` (rejects
      // letters) BUT the form still accepted decimals like "1.5" which
      // the backend silently truncated through the int cast. Now an
      // explicit Number.isInteger guard surfaces a clean toast naming
      // the field and the bound; the operator's value is preserved
      // until they fix it (no silent clamp).
      for (const k of this._allTuningKeys()) {
        const raw = (this.tuningForm || {})[k];
        if (raw === '' || raw == null) continue;  // blank = clear override
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
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(await r.text());
        await this.loadTuning();
        this.showToast(this.t('admin.config.saved_toast'));
      } catch (e) {
        this.showToast(this.t('admin.config.save_failed', { error: e.message }), 'error');
      } finally {
        this.tuningSaving = false;
      }
    },
    // No-op stubs kept so any existing @input / @change bindings that
    // call mark<X>Dirty() don't throw. The smart getters re-evaluate
    // automatically on form changes via Alpine reactivity, so these
    // calls are now unnecessary but harmless. Removing the markup
    // bindings is a separate cleanup (#305 follow-up).
    markAppriseDirty()    {},
    markOpenMeteoDirty()  {},
    markPortainerFormDirty() {},
    markOidcFormDirty()   {},
    // Auto-save a single per-service "enabled" master switch.
    // Wired to the @change of the toggle checkbox for Apprise /
    // Open-Meteo / Portainer / SSH so the operator doesn't have to
    // hunt for a Save button just to flip the master switch. Sends
    // ONLY the one field so it doesn't drag along whatever else is
    // dirty in the form. Toast confirms with the resulting state.
    async saveServiceEnabled(name) {
      const allowed = ['apprise', 'open_meteo', 'portainer', 'ssh'];
      if (!allowed.includes(name)) return;
      const key = name + '_enabled';
      const value = !!this.settings[key];
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ [key]: value }),
        });
        if (!r.ok) throw new Error(await r.text());
        const stateKey = value
          ? 'admin_integrations.toggle_enabled_toast'
          : 'admin_integrations.toggle_disabled_toast';
        this.showToast(this.t(stateKey, { name }), 'success');
      } catch (e) {
        // Roll the in-memory toggle back so UI matches server state.
        this.settings[key] = !value;
        this.showToast(this.t('toasts_extra.save_failed_generic'), 'error');
      }
    },
    async testNotify() {
      try {
        const r = await fetch('/api/notify-test', { method: 'POST' });
        if (!r.ok) throw new Error();
        this.showToast(this.t('toasts.test_notification_sent'));
      } catch (e) { this.showToast(this.t('toasts.test_notification_failed'), 'error'); }
    },
    async loadIgnores() {
      try {
        const r = await fetch('/api/ignores');
        this.ignores = (await r.json()).ignores || [];
      } catch (e) { console.error(e); }
    },
    async addIgnore() {
      if (!this.newIgnore.pattern.trim()) return;
      await fetch('/api/ignores', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(this.newIgnore),
      });
      this.newIgnore.pattern = '';
      await this.loadIgnores();
      await this.refresh(true);
    },
    async delIgnore(pattern) {
      await fetch('/api/ignores/' + encodeURIComponent(pattern), { method: 'DELETE' });
      await this.loadIgnores();
      await this.refresh(true);
    },
    async toggleIgnore(item) {
      if (item.ignored) {
        const match = this.ignores.find(ig =>
          (ig.kind === 'image' && (item.image || '').includes(ig.pattern)) ||
          (ig.kind === 'stack' && ig.pattern === item.stack)
        );
        if (match) await this.delIgnore(match.pattern);
      } else {
        this.newIgnore = { kind: 'image', pattern: item.image };
        await this.addIgnore();
      }
      this.drawerItem = null;
    },
    _historyQueryParams(opts = {}) {
      // Build the shared ?stack=&op_type=...&since=... query string used by
      // loadHistory() and the CSV/JSON export links, so filters stay in sync.
      // Pass `{ paging: true }` to include offset+limit derived from the
      // current page state — used by the live view. Exports omit paging
      // and request a high cap (5000) so the operator gets the full
      // filtered result, not just one page.
      const f = this.historyFilters;
      const p = new URLSearchParams();
      if (f.q)        p.set('q', f.q);
      if (f.stack)    p.set('stack', f.stack);
      if (f.op_type)  p.set('op_type', f.op_type);
      if (f.status)   p.set('status', f.status);
      if (f.actor)    p.set('actor', f.actor);
      if (f.fromDate) p.set('since', String(new Date(f.fromDate).getTime() / 1000));
      if (f.toDate) {
        // Date input is midnight-of-day; treat as inclusive END-of-day.
        const end = new Date(f.toDate); end.setHours(23, 59, 59, 999);
        p.set('until', String(end.getTime() / 1000));
      }
      if (opts.paging) {
        const per = Math.max(10, Math.min(500, this.historyPerPage || 50));
        const page = Math.max(1, this.historyPage || 1);
        p.set('limit', String(per));
        p.set('offset', String((page - 1) * per));
      } else {
        p.set('limit', '5000');
      }
      return p;
    },
    hasHistoryFilter() {
      const f = this.historyFilters;
      return !!(f.q || f.stack || f.op_type || f.status || f.actor || f.fromDate || f.toDate);
    },
    resetHistoryFilters() {
      this.historyFilters = { q: '', stack: '', op_type: '', status: '', actor: '', fromDate: '', toDate: '' };
    },
    historyExportUrl(fmt) {
      // Exports never page — the operator wants the full filtered
      // dataset in the file, not whichever page is on screen.
      return `/api/history.${fmt}?` + this._historyQueryParams().toString();
    },
    // --- History server-side paging ---
    historyTotalPages() {
      const per = Math.max(1, this.historyPerPage || 50);
      return Math.max(1, Math.ceil((this.historyTotal || 0) / per));
    },
    _persistHistoryPaging() {
      try {
        localStorage.setItem('historyPage',    String(this.historyPage));
        localStorage.setItem('historyPerPage', String(this.historyPerPage));
      } catch (_) {}
    },
    historyGoToPage(n) {
      const page = Math.max(1, Math.min(parseInt(n, 10) || 1, this.historyTotalPages()));
      if (page === this.historyPage) return;
      this.historyPage = page;
      this._persistHistoryPaging();
      this.loadHistory();
    },
    historyPrevPage() { this.historyGoToPage(this.historyPage - 1); },
    historyNextPage() { this.historyGoToPage(this.historyPage + 1); },
    historySetPerPage(n) {
      const per = Math.max(10, Math.min(500, parseInt(n, 10) || 50));
      if (per === this.historyPerPage) return;
      this.historyPerPage = per;
      this.historyPage = 1;
      this._persistHistoryPaging();
      this.loadHistory();
    },
    // Filter changes reset to page 1 — staying on page 7 of an old
    // result set when the new filtered set has 2 pages would land the
    // operator on a blank page.
    historyApplyFilter(fn) {
      if (typeof fn === 'function') fn();
      this.historyPage = 1;
      this._persistHistoryPaging();
      this.loadHistory();
    },
    get historyStackOptions() {
      // Populate the stack dropdown from whatever stacks we currently see
      // in the live cache (avoids a dedicated /api endpoint). Alphabetical.
      return [...new Set((this.stacks || []).map(s => s.name).filter(Boolean))].sort();
    },
    async loadHistory() {
      try {
        const r = await fetch('/api/history?' + this._historyQueryParams({ paging: true }).toString());
        const d = await r.json();
        // in-place reconcile keyed on history row `id` (auto-
        // increment PK from the `history` table) so each page change
        // doesn't tear down every row's expanded `<details>` state +
        // inline-style nodes for the entire table. Same helper as
        // #418/#436/#439.
        this._reconcileById(this.history, Array.isArray(d.history) ? d.history : []);
        this.historyTotal = Number.isFinite(+d.total) ? +d.total : this.history.length;
        // If the operator's persisted page is past the new filtered
        // total (e.g. they had a wide filter on page 7, narrowed it,
        // and the result is 1 page), clamp + reload.
        const max = this.historyTotalPages();
        if (this.historyPage > max) {
          this.historyPage = max;
          this._persistHistoryPaging();
          // Don't recurse forever — only one re-fetch on clamp.
          if (this.historyTotal > 0) this.loadHistory();
        }
      } catch (e) { console.error(e); }
    },
    openHistoryDetail(h) {
      const events = this.parseEvents(h.events) || [];
      const esc = (s) => String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
      const rows = events.map(ev => {
        const cls = ev.level === 'error' ? 'swal-ev-err'
                  : ev.level === 'success' ? 'swal-ev-ok'
                  : 'swal-ev-info';
        return `<div class="swal-ev ${cls}"><span class="swal-ev-ts">${esc(this.formatTimeShort(ev.ts))}</span><span class="swal-ev-msg">${esc(ev.msg)}</span></div>`;
      }).join('') || `<div class="swal-ev swal-ev-info">${esc(this.t('empty.no_events'))}</div>`;
      const meta = `
        <div class="swal-meta mono">
          <div><b>${esc(this.t('history.detail.when'))}</b> ${esc(this.formatTime(h.ts))}</div>
          <div><b>${esc(this.t('history.detail.op'))}</b> ${esc(h.op_type)}</div>
          <div><b>${esc(this.t('history.detail.target'))}</b> ${esc(h.target_name || '—')}</div>
          <div><b>${esc(this.t('history.detail.stack'))}</b> ${esc(h.target_stack || '—')}</div>
          <div><b>${esc(this.t('history.detail.actor'))}</b> ${esc(h.actor || 'ui')}</div>
          <div><b>${esc(this.t('history.detail.duration'))}</b> ${(h.duration || 0).toFixed(2)}s</div>
          <div><b>${esc(this.t('history.detail.status'))}</b> ${esc(h.status)}</div>
          ${h.error ? `<div class="swal-err"><b>${esc(this.t('history.detail.error'))}</b> ${esc(h.error)}</div>` : ''}
        </div>`;
      Swal.fire({
        title: h.target_name || h.op_type,
        html: `${meta}<div class="swal-events">${rows}</div>`,
        width: 720,
        showConfirmButton: false,
        showCloseButton: true,
        background: this._cssVar('--surface'),
        color: this._cssVar('--text'),
      });
    },
    async clearHistory() {
      const ok = await this.confirmDialog({
        title: this.t('history.clear_confirm_title'),
        html: this.t('history.clear_confirm_html'),
        icon: 'warning',
        confirmText: this.t('history.clear_confirm_button'),
        confirmColor: this._cssVar('--danger'),
      });
      if (!ok) return;
      await fetch('/api/history', { method: 'DELETE' });
      await this.loadHistory();
      this.showToast(this.t('toasts.history_cleared'));
    },
    // ---------------- in-app notifications ----------------
    notificationsQuery() {
      const params = new URLSearchParams();
      params.set('limit', String(this.notificationsLimit || 25));
      params.set('offset', String(this.notificationsOffset || 0));
      if (this.notificationsFilterUnread) params.set('unread_only', 'true');
      if (this.notificationsFilterSeverity && this.notificationsFilterSeverity !== 'all') {
        params.set('severity', this.notificationsFilterSeverity);
      }
      if (this.notificationsFilterEvent && this.notificationsFilterEvent !== 'all') {
        params.set('event', this.notificationsFilterEvent);
      }
      return params.toString();
    },
    // Open the notifications popup (#855 follow-up). Loads the latest
    // page on every open so the operator's quick-check doesn't show
    // stale data — the SSE-driven badge keeps the count current while
    // the popup is closed, but the row list itself only refreshes on
    // open / explicit Reload click. Mirrors the hotkeys-modal pattern:
    // backdrop click and Esc close the dialog.
    openNotificationsPopup() {
      this.showNotificationsPopup = true;
      // Reset offset so a re-open after closing always starts at the
      // newest page; the previously-loaded extra rows from a prior
      // "Load more" cycle would otherwise pile up indefinitely.
      this.notificationsOffset = 0;
      this.loadNotifications();
    },
    async loadNotifications() {
      // Operator-initiated reload (open / Reload button / filter
      // change) — always start from the first page. The "Load more"
      // path uses `loadMoreNotifications` which preserves the
      // existing list and only appends.
      this.notificationsOffset = 0;
      this.notificationsLoading = true;
      try {
        const r = await fetch('/api/notifications?' + this.notificationsQuery());
        if (!r.ok) {
          this.notificationsLoading = false;
          return;
        }
        const d = await r.json();
        // Replace the list — operator-initiated reload is the only call
        // path here; SSE pushes use _handleNotificationCreated for
        // in-place prepend without reassigning the array.
        this.notifications = Array.isArray(d.items) ? d.items : [];
        this.notificationsTotal = Number.isFinite(d.total) ? d.total : 0;
        this.notificationsUnread = Number.isFinite(d.unread_count) ? d.unread_count : 0;
      } catch (e) {
        console.warn('[notifications] loadNotifications failed', e);
      }
      this.notificationsLoading = false;
    },
    // Server-side pagination — Prev / Next swap the visible list to a
    // different page (limit / offset are server-driven). Caps each page
    // at `notificationsLimit` rows so a fleet with 1000+ notifications
    // never loads more than one page-worth at a time; the browser's
    // memory and DOM cost stays constant regardless of total count.
    // Page navigation REPLACES the in-memory list (no client-side
    // accumulation) — each click is a fresh server fetch with the
    // appropriate offset, the response replaces `this.notifications`,
    // and the previous page's rows are released for GC.
    notificationsPage()      { return Math.floor((this.notificationsOffset || 0) / (this.notificationsLimit || 25)) + 1; },
    notificationsPageCount() {
      const limit = this.notificationsLimit || 25;
      const total = this.notificationsTotal || 0;
      return Math.max(1, Math.ceil(total / limit));
    },
    notificationsHasNext()   { return this.notificationsPage() < this.notificationsPageCount(); },
    notificationsHasPrev()   { return (this.notificationsOffset || 0) > 0; },
    async notificationsNextPage() {
      if (this.notificationsLoading || !this.notificationsHasNext()) return;
      this.notificationsOffset = (this.notificationsOffset || 0) + (this.notificationsLimit || 25);
      await this._reloadNotificationsPage();
    },
    async notificationsPrevPage() {
      if (this.notificationsLoading || !this.notificationsHasPrev()) return;
      const prev = (this.notificationsOffset || 0) - (this.notificationsLimit || 25);
      this.notificationsOffset = Math.max(0, prev);
      await this._reloadNotificationsPage();
    },
    async _reloadNotificationsPage() {
      this.notificationsLoading = true;
      try {
        const r = await fetch('/api/notifications?' + this.notificationsQuery());
        if (!r.ok) { this.notificationsLoading = false; return; }
        const d = await r.json();
        // Page-replace, not append — keeps DOM cost constant.
        this.notifications = Array.isArray(d.items) ? d.items : [];
        if (Number.isFinite(d.total)) this.notificationsTotal = d.total;
        if (Number.isFinite(d.unread_count)) this.notificationsUnread = d.unread_count;
      } catch (e) {
        console.warn('[notifications] page change failed', e);
      }
      this.notificationsLoading = false;
    },
    // Lightweight unread-count probe — doesn't fetch the list, just the
    // count. Used by init() so the avatar badge has a count BEFORE the
    // operator opens the view.
    async loadNotificationsUnread() {
      try {
        const r = await fetch('/api/notifications?limit=1&unread_only=true');
        if (!r.ok) return;
        const d = await r.json();
        this.notificationsUnread = Number.isFinite(d.unread_count) ? d.unread_count : 0;
      } catch (_) {}
    },
    // SSE handler — published from logic/ops.py:_notify_medium_app on
    // every successful INSERT. Prepends in place; the list array isn't
    // reassigned so Alpine's row template doesn't tear DOM down.
    _handleNotificationCreated(payload) {
      if (!payload || !payload.id) return;
      // Bump global unread count (server stamps the canonical count
      // into the payload; trust it over local counter math).
      if (Number.isFinite(payload.unread_count)) {
        this.notificationsUnread = payload.unread_count;
      } else {
        this.notificationsUnread = (this.notificationsUnread || 0) + 1;
      }
      // Prepend to the visible list ONLY when it'd pass the active
      // filters — so an "errors only" view doesn't flicker an info row
      // in for one cycle.
      if (this._notificationPassesFilters(payload)
          && this.showNotificationsPopup) {
        // Avoid double-insert if operator pulled the same row via
        // loadNotifications between dispatch + SSE arrival.
        const exists = (this.notifications || []).some(n => n.id === payload.id);
        if (!exists) {
          this.notifications.unshift({
            id:          payload.id,
            ts:          payload.ts,
            event:       payload.event || '',
            severity:    payload.severity || 'info',
            title:       payload.title || '',
            body:        payload.body || '',
            actor:       payload.actor || null,
            target_kind: payload.target_kind || null,
            target_id:   payload.target_id || null,
            metadata:    null,
            read_at:     null,
          });
          this.notificationsTotal = (this.notificationsTotal || 0) + 1;
          // Trim to prevent unbounded growth when the view stays open
          // for hours (SSE bursts during a deploy can deliver dozens).
          const cap = (this.notificationsLimit || 25) * 4;
          if (this.notifications.length > cap) {
            this.notifications.length = cap;
          }
        }
      }
    },
    _handleNotificationRead(payload) {
      if (!payload) return;
      if (Number.isFinite(payload.unread_count)) {
        this.notificationsUnread = payload.unread_count;
      }
      const ts = Number.isFinite(payload.read_at) ? payload.read_at : Math.floor(Date.now() / 1000);
      if (payload.bulk) {
        for (const n of (this.notifications || [])) {
          if (n.read_at == null) n.read_at = ts;
        }
      } else if (payload.id) {
        const row = (this.notifications || []).find(n => n.id === payload.id);
        if (row && row.read_at == null) row.read_at = ts;
      }
    },
    _handleNotificationDeleted(payload) {
      if (!payload || !payload.id) return;
      const i = (this.notifications || []).findIndex(n => n.id === payload.id);
      if (i >= 0) this.notifications.splice(i, 1);
      if (Number.isFinite(payload.unread_count)) {
        this.notificationsUnread = payload.unread_count;
      }
    },
    _notificationPassesFilters(n) {
      if (!n) return false;
      if (this.notificationsFilterUnread && n.read_at != null) return false;
      if (this.notificationsFilterSeverity && this.notificationsFilterSeverity !== 'all'
          && n.severity !== this.notificationsFilterSeverity) {
        return false;
      }
      if (this.notificationsFilterEvent && this.notificationsFilterEvent !== 'all'
          && n.event !== this.notificationsFilterEvent) {
        return false;
      }
      return true;
    },
    notificationEventOptions() {
      const seen = new Set();
      const out = [];
      for (const n of (this.notifications || [])) {
        if (n.event && !seen.has(n.event)) {
          seen.add(n.event);
          out.push(n.event);
        }
      }
      out.sort();
      return out;
    },
    async markNotificationRead(id) {
      // Optimistic flip — find row + stamp read_at locally so the
      // chevron / badge dim immediately. Roll back on a non-2xx.
      const row = (this.notifications || []).find(n => n.id === id);
      const prev = row ? row.read_at : null;
      if (row && row.read_at == null) {
        row.read_at = Math.floor(Date.now() / 1000);
        if (this.notificationsUnread > 0) this.notificationsUnread -= 1;
      }
      try {
        const r = await fetch('/api/notifications/' + encodeURIComponent(id) + '/read', {
          method: 'POST',
        });
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        if (row && Number.isFinite(d.read_at)) row.read_at = d.read_at;
        if (Number.isFinite(d.unread_count)) this.notificationsUnread = d.unread_count;
      } catch (e) {
        // Roll back on failure so the operator sees the actual state.
        if (row) row.read_at = prev;
        if (prev == null) this.notificationsUnread = (this.notificationsUnread || 0) + 1;
        this.showToast(this.t('toasts.load_failed', { error: e.message }), 'error');
      }
    },
    async markAllNotificationsRead() {
      try {
        const r = await fetch('/api/notifications/read-all', { method: 'POST' });
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        const ts = Math.floor(Date.now() / 1000);
        for (const n of (this.notifications || [])) {
          if (n.read_at == null) n.read_at = ts;
        }
        this.notificationsUnread = 0;
        this.showToast(this.t('notifications.marked_all_read', { count: d.count || 0 })
          || ('Marked ' + (d.count || 0) + ' as read'));
      } catch (e) {
        this.showToast(this.t('toasts.load_failed', { error: e.message }), 'error');
      }
    },
    notificationDotClass(severity) {
      // Severity → CSS class name. The class itself is defined in
      // style.css with the matching token-backed colour.
      const sev = (severity || 'info').toLowerCase();
      if (['info', 'warning', 'error', 'success'].includes(sev)) {
        return 'notification-dot notification-dot--' + sev;
      }
      return 'notification-dot notification-dot--info';
    },
    pollOps() {
      if (this._opsTimer) clearTimeout(this._opsTimer);
      if (!this._opLingerUntil) this._opLingerUntil = {};
      if (!this._opsSeen) this._opsSeen = null;  // null sentinel = first poll
      // Linger window — keep finished ops visible in the floating panel
      // for this many seconds after they complete. Two qualifying paths:
      //   1. op was running in the previous poll and is now done;
      //   2. op is brand-new to us (never seen before) AND is already
      //      done (completed between polls — e.g. bulk cleanup where
      //      individual removes finish in <1.5s).
      // The "first poll" case is special: we prime _opsSeen with the
      // ring buffer's existing state WITHOUT lingering, so a page load
      // doesn't flood the panel with up-to-50 historical completed ops.
      const LINGER_MS = 8000;
      const tick = async () => {
        try {
          const r = await fetch('/api/ops');
          const all = (await r.json()).ops || [];
          const prevRunning = this.activeOps
            .filter(o => o.status === 'running')
            .map(o => o.id);
          const nowTs = Date.now();
          const firstPoll = this._opsSeen === null;
          if (firstPoll) this._opsSeen = new Set();
          // Two paths qualify as "just done" for both the linger
          // panel AND the downstream refresh/toast trigger:
          //   (1) observed running → done
          //   (2) brand-new to us AND already terminal (completed
          //       between polls — e.g. force-remove finishes in
          //       <1.5s so the op is never seen as `running`)
          // Merging both into the same justDone set fixes #143 where
          // fast ops bypassed the post-op items refresh and the
          // Cleanup button kept showing the removed container.
          const justDone = [];
          for (const o of all) {
            const wasUnknown = !this._opsSeen.has(o.id);
            this._opsSeen.add(o.id);
            if (o.status === 'running') continue;
            if (this._opLingerUntil[o.id]) continue;
            // Path 1: observed running → done.
            if (prevRunning.includes(o.id)) {
              this._opLingerUntil[o.id] = nowTs + LINGER_MS;
              justDone.push(o);
              continue;
            }
            // Path 2: brand-new op, already done (skip on first poll
            // so we don't surface historical ring-buffer entries).
            if (!firstPoll && wasUnknown) {
              this._opLingerUntil[o.id] = nowTs + LINGER_MS;
              justDone.push(o);
            }
          }
          // Sweep expired / evicted linger entries.
          const aliveIds = new Set(all.map(o => o.id));
          for (const id of Object.keys(this._opLingerUntil)) {
            if (!aliveIds.has(id) || this._opLingerUntil[id] <= nowTs) {
              delete this._opLingerUntil[id];
            }
          }
          this.activeOps = all.filter(
            o => o.status === 'running' || this._opLingerUntil[o.id]
          );
          if (justDone.length > 0) {
            const holdKeys = [...new Set(justDone.map(o => this._opBusyKey(o)).filter(Boolean))];
            holdKeys.forEach(k => this._holdBusy(k));
            justDone.forEach(o => this.showToast(
              this.t('toasts.op_result', {
                icon: o.status === 'success' ? '✓' : '✗',
                op: this.t('op_types.' + o.op_type) || o.op_type.replace('_', ' '),
                name: o.target_name,
              }),
              o.status === 'success' ? 'success' : 'error'
            ));
            Promise.all([this.refresh(true), this.loadHistory()])
              .finally(() => holdKeys.forEach(k => this._clearBusy(k)));
          }
        } catch (e) {}
        // Cadence is operator-tunable via Admin → Config →
        // tuning_ops_poll_interval_seconds. Backend
        // multiplies × 1000 before delivery as
        // `me.client_config.ops_poll_ms`, so the setTimeout call below
        // still consumes ms. Resolved per-tick so a Save in
        // Admin → Config takes effect on the very next cycle (after
        // /api/me re-flows). Defaults to 2 seconds (= 2000 ms) if absent.
        // SSE-fallback gate. When the live event stream is
        // healthy, op deltas arrive via /api/events and re-running the
        // poll is wasted work. Stretch the cadence to a slow keepalive
        // (every 30s) so a stalled stream we haven't yet detected
        // can't permanently freeze the live panel; the freshness
        // watchdog flips _sseConnected back to false within 30s of a
        // real disconnect and the next tick at the slow cadence
        // resumes regular polling.
        const fastMs = (this.me && this.me.client_config && this.me.client_config.ops_poll_ms) || 1500;
        // honour the unified picker's "Off" mode here too. Pre-fix
        // pollOps kept firing at `fastMs` even when the operator chose
        // Off, breaking the picker's promise of "no updates at all".
        // Now: Off → don't reschedule; Live → 30s keep-alive; interval
        // modes → fastMs (ops need faster feedback than charts so the
        // unified picker's interval value doesn't override this).
        if (this.refreshInterval === 0) {
          this._opsTimer = null;
          return;
        }
        // SSE-up keep-alive cadence is operator-tunable via
        // `tuning_pollops_sse_keepalive_seconds`. Backend × 1000 in
        // `client_config.pollops_sse_keepalive_ms`. Defensive `|| 30000`
        // covers the brief window before /api/me hydrates.
        const keepAliveMs = (this.me && this.me.client_config
                              && this.me.client_config.pollops_sse_keepalive_ms) || 30000;
        const opsPollMs = this._sseConnected ? keepAliveMs : fastMs;
        if (this._sseConnected && !this._opsLiveLogged) {
          this._opsLiveLogged = true;
        } else if (!this._sseConnected && this._opsLiveLogged) {
          this._opsLiveLogged = false;
        }
        this._opsTimer = setTimeout(tick, opsPollMs);
      };
      tick();
    },
    pollOpsNow() { this.pollOps(); },

    // ===================================================================
    // Real-time event stream 
    // ===================================================================
    // EventSource connects to /api/events on cookie-authed browsers and
    // dispatches one handler per server-side event type. Every existing
    // poll loop (pollOps / pollStats / refresh / loadHosts / loadHistory)
    // checks `_sseConnected` and skips its self-scheduled work while the
    // stream is healthy. EventSource handles reconnect natively; we only
    // track the connection state for the toolbar indicator + poll-gate.
    //
    // Reactive updates use the existing in-place reconcile contract —
    // never reassign reactive arrays from an event handler (would tear
    // every chart SVG / <details> / inline-style node down on each
    // event, defeating the entire purpose of moving from poll → push).
    // explicit disconnect so the cadence picker can fully turn
    // SSE off when the operator chooses "Off" or an interval. Without
    // this, picking Off left the SSE pipe alive and the Live pill
    // stayed green even though the operator's mental model is "no
    // updates at all".
    _disconnectSSE() {
      if (this._sse) {
        console.log('[live] SSE disconnect: closing stream (operator picked Off/interval)');
        try { this._sse.close(); } catch (_) {}
        this._sse = null;
      }
      this._sseConnected = false;
      this._sseLastEventTs = 0;
    },

    _initSSE() {
      // Defence-in-depth: never start two streams. ``init()`` runs once
      // per Alpine component instance but a future hot-reload path
      // could call it twice.
      if (this._sse) {
        try { this._sse.close(); } catch (_) {}
        this._sse = null;
      }
      console.log('[live] SSE init: opening EventSource at /api/events');
      let es;
      try {
        es = new EventSource('/api/events', { withCredentials: true });
      } catch (e) {
        console.warn('[events] EventSource not supported in this browser — staying on polling', e);
        return;
      }
      this._sse = es;
      const onAny = () => {
        this._sseLastEventTs = Date.now();
        this._sseConnected = true;
      };
      es.addEventListener('open', () => {
        onAny();
        this._sseReconnects += 1;
        // First connect carries _sseReconnects === 1 (baseline). Every
        // bump above 1 represents a recover from a drop — kick a one-
        // shot REST refresh so the SPA catches up on what it missed
        // while disconnected.
        if (this._sseReconnects > 1) {
          console.log('[live] SSE reconnect: kicking one-shot REST refresh to catch up missed deltas');
          try { this.refresh(true); } catch (_) {}
          try { if (this.view === 'hosts') this.loadHosts(true); } catch (_) {}
          try { this.loadHistory && this.loadHistory(); } catch (_) {}
        }
      });
      // ``hello`` is the bus's first frame after upgrade — treat as a
      // confirmation of healthy stream rather than a real event.
      es.addEventListener('hello', () => {
        onAny();
      });
      // `keepalive` heartbeat. Server emits one every
      // tuning_sse_heartbeat_seconds during quiet windows so the
      // freshness watchdog clock keeps advancing and we don't false-
      // flip to polling-fallback. `onAny()` updates `_sseLastEventTs`;
      // no other side-effect (it's a synthetic ping with empty
      // payload).
      es.addEventListener('keepalive', () => {
        onAny();
      });
      // ``:overflow`` signals the per-subscriber queue dropped events
      // (slow consumer / throttled tab). Backend emits this BEFORE
      // resuming the live stream so we know to reconcile via REST.
      es.addEventListener(':overflow', () => {
        onAny();
        this._sseDropped += 1;
        console.warn('[live] event=:overflow — subscriber queue dropped events; reconciling via REST (total dropped this session: ' + this._sseDropped + ')');
        // operator-visible signal. Pre-fix this lived in
        // DevTools console only; an overflow means "your tab missed
        // events; we just reconciled via REST" — worth a non-intrusive
        // amber toast so the operator sees the flap and can correlate
        // with whatever caused the burst.
        try {
          this.showToast(
            this.t('toasts_extra.sse_overflow') || 'Live event stream backlogged — refreshed automatically',
            'warning'
          );
        } catch (_) {}
        try { this.refresh(true); } catch (_) {}
        try { this.loadHistory && this.loadHistory(); } catch (_) {}
        if (this.view === 'hosts') {
          try { this.loadHosts(true); } catch (_) {}
        }
      });
      // Self-filter check FIRST so the console.log only fires for events
      // we'll actually act on. Pre-fix the log fired even on self-
      // originated events that _handleOpEvent then filtered out — log
      // spam on every op the originating tab triggered.
      es.addEventListener('op:created',   (e) => { onAny(); if (this._isSelfEvent(e)) return; this._handleOpEvent(e, 'created'); });
      es.addEventListener('op:updated',   (e) => { onAny(); if (this._isSelfEvent(e)) return; this._handleOpEvent(e, 'updated'); });
      es.addEventListener('op:completed', (e) => { onAny(); if (this._isSelfEvent(e)) return; this._handleOpEvent(e, 'completed'); });
      es.addEventListener('cache:invalidated', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        // Items dataset is large enough that delta-broadcasting it
        // isn't worth it for V1 — kick a forced refresh instead, and
        // let the existing in-place reconcile in `refresh()` do its
        // work without tearing rows down.
        try { this.refresh(true); } catch (_) {}
      });
      es.addEventListener('stats:refreshed', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        const fired = this.statsInterval > 0;
        // Hint event — the stats payload itself isn't broadcast (cheap
        // to fetch via /api/stats and the existing TTL gate prevents
        // back-to-back pulls). Skip when statsInterval=0 (operator
        // explicitly turned stats off) so the master switch still wins.
        if (fired) {
          try { this.loadStats && this.loadStats(); } catch (_) {}
        }
      });
      es.addEventListener('host:row_updated', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        try {
          const data = JSON.parse(e.data || '{}');
          const id = (data.payload && data.payload.id) || '';
          console.log('[live] event=host:row_updated id=' + id);
          if (!id) return;
          // Route through the SHARED queue + worker pool so a burst
          // of N events (sampler tick affecting many hosts) coalesces
          // through the existing 200ms debounce + shares the cap.
          // Pre-fix `refreshHostRow` was called directly here,
          // bypassing both worker pools — bursts could exceed the
          // operator-set parallel cap.
          this._hostObserverPending = this._hostObserverPending || new Set();
          this._hostObserverPending.add(id);
          if (typeof this._scheduleHostObserverFlush === 'function') {
            this._scheduleHostObserverFlush();
          } else if (typeof this._runHostRefreshQueue === 'function') {
            // Fallback path if the IO observer hasn't initialised yet
            // (e.g. browsers without IntersectionObserver). Direct
            // enqueue still goes through the shared worker pool.
            this._runHostRefreshQueue([id]).catch(() => {});
          }
        } catch (_) {}
      });
      es.addEventListener('host:failure_state_changed', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        try {
          const data = JSON.parse(e.data || '{}');
          const id = (data.payload && data.payload.host_id) || '';
          console.log('[live] event=host:failure_state_changed id=' + id);
          if (!id) return;
          this._hostObserverPending = this._hostObserverPending || new Set();
          this._hostObserverPending.add(id);
          if (typeof this._scheduleHostObserverFlush === 'function') {
            this._scheduleHostObserverFlush();
          } else if (typeof this._runHostRefreshQueue === 'function') {
            this._runHostRefreshQueue([id]).catch(() => {});
          }
        } catch (_) {}
      });
      // Bulk-action event — backend publishes ONE frame per bulk
      // endpoint (pause / resume / snmp_vendors / snmp_tunables)
      // carrying every applied host_id in the payload. SPA reconciles
      // each id in the same way the per-host handler above does
      // (single observer-pending Set + flush). For curated-config
      // edits (snmp_vendors / snmp_tunables) we ALSO trigger a
      // background `loadHosts(true)` so the curated overlay (snmp
      // sub-block, vendors list) re-syncs across tabs.
      es.addEventListener('host:bulk_action_applied', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        try {
          const data = JSON.parse(e.data || '{}');
          const payload = data.payload || {};
          const action = payload.action || '';
          const ids = Array.isArray(payload.host_ids) ? payload.host_ids : [];
          console.log('[live] event=host:bulk_action_applied action=' + action + ' ids=' + ids.length);
          if (ids.length === 0) return;
          // Per-row refresh — same path as the per-host handler.
          this._hostObserverPending = this._hostObserverPending || new Set();
          for (const id of ids) this._hostObserverPending.add(id);
          if (typeof this._scheduleHostObserverFlush === 'function') {
            this._scheduleHostObserverFlush();
          } else if (typeof this._runHostRefreshQueue === 'function') {
            this._runHostRefreshQueue(ids.slice()).catch(() => {});
          }
          // Curated-config actions also need a `hosts_config`-level
          // reload so the SPA's snmp_name / snmp.vendors / snmp.walk_*
          // overlays pick up the new server-side state.
          if ((action === 'snmp_vendors' || action === 'snmp_tunables')
              && typeof this.loadHosts === 'function') {
            this.loadHosts(true);
          }
        } catch (_) {}
      });
      // Per-(provider, host) probe-status events. Backend fires these
      // around each in-flight per-host probe slice (SNMP / Webmin / NE)
      // so a chip pulses ONLY while ITS specific probe is running, not
      // the whole row-wide `_loading` window. Cache hits skip the
      // events (no real probe ran) so chips stay at rest. Beszel /
      // Pulse / Ping skip too — they're either dict lookups (Beszel /
      // Pulse) or sampler-driven reads (Ping) inside `_merge_one_host`.
      // The row-level `_loading` pulse from #1003 still covers the
      // initial-paint case for those.
      const _setProvPolling = (host_id, provider, polling) => {
        if (!host_id || !provider) return;
        const row = (this.hosts || []).find(r => r && r.id === host_id);
        if (!row) return;
        if (!row._polling || typeof row._polling !== 'object') row._polling = {};
        if (polling) row._polling[provider] = true;
        else         delete row._polling[provider];
      };
      es.addEventListener('host:provider_probing', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        try {
          const data = JSON.parse(e.data || '{}');
          const p = data.payload || {};
          _setProvPolling(p.host_id, p.provider, true);
        } catch (_) {}
      });
      es.addEventListener('host:provider_done', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        try {
          const data = JSON.parse(e.data || '{}');
          const p = data.payload || {};
          _setProvPolling(p.host_id, p.provider, false);
        } catch (_) {}
      });
      // host_metrics_sampler publishes this on every NE
      // sample INSERT. Refresh the drawer chart only when (a) it's
      // currently open AND (b) the open host matches the event's
      // host_id. Per-host filter is critical: 50 sampled hosts firing
      // 50 events each tick would otherwise spam the SPA. Beszel
      // hosts don't go through host_metrics_sampler so they keep
      // the polling baseline — the drawer timer gracefully handles
      // both cases.
      es.addEventListener('host:history_appended', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        if (!this.drawerHost) return;
        try {
          const data = JSON.parse(e.data || '{}');
          const id = (data.payload && data.payload.host_id) || '';
          if (!id || id !== this.drawerHost.id) return;
          // Only NE-source drawers get the push refresh; Beszel
          // drawers' history isn't sample-keyed off our DB so this
          // event is irrelevant to them.
          if (!this.drawerHost.ne_url) return;
          // Debounce 200 ms — backend may fire bursts of
          // history_appended events for the open host within a single
          // sampler tick (e.g. SNMP + NE both writing). Collapse them
          // into one loadHostHistory call so we don't make N redundant
          // /api/hosts/history fetches in <1s.
          if (this._historyAppendedDebounceTimer) {
            clearTimeout(this._historyAppendedDebounceTimer);
          }
          this._historyAppendedDebounceTimer = setTimeout(() => {
            this._historyAppendedDebounceTimer = null;
            if (!this.drawerHost || this.drawerHost.id !== id) return;
            console.log('[live] event=host:history_appended id=' + id + ' → loadHostHistory (debounced)');
            this._pollWrap(this.loadHostHistory(
              this.drawerHost.beszel_id || '',
              this.drawerHost.id,
            )).catch(() => {});
          }, 200);
        } catch (_) {}
      });
      // ping_sampler publishes this on every INSERT. Drives the
      // RTT chip + (V2) the drawer Ping chart. Per-host filter same as
      // history_appended. Routes through the SHARED _hostObserverPending
      // queue so a fleet-wide ping tick (N hosts firing in the same
      // second) coalesces through the 200ms debounce + shares the
      // _hostRefreshQueue cap with poll + IO observer + other SSE
      // events. Direct refreshHostRow would bypass that cap.
      es.addEventListener('host:ping_sampled', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        try {
          const data = JSON.parse(e.data || '{}');
          const id = (data.payload && data.payload.host_id) || '';
          if (!id) return;
          this._hostObserverPending = this._hostObserverPending || new Set();
          this._hostObserverPending.add(id);
          if (typeof this._scheduleHostObserverFlush === 'function') {
            this._scheduleHostObserverFlush();
          } else if (typeof this._runHostRefreshQueue === 'function') {
            this._runHostRefreshQueue([id]).catch(() => {});
          }
          // push the new sample into the open drawer's ping
          // chart so Live mode is genuinely push-driven (no fallback
          // timer needed when SSE is healthy). Same shape as the
          // host:history_appended handler for #496.
          if (this.drawerHost && this.drawerHost.id === id && this.drawerHost.ping_enabled
              && typeof this.loadHostPingHistory === 'function') {
            this._pollWrap(this.loadHostPingHistory(id)).catch(() => {});
          }
        } catch (_) {}
      });
      es.addEventListener('schedule:fired', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        // Schedule rows + queue rebuild via the same helpers the
        // Schedules tab uses. They reconcile in place via #439's
        // _reconcileById path so re-firing them mid-tab doesn't
        // tear DOM down.
        try { this.loadSchedules && this.loadSchedules(); } catch (_) {}
        try { this.loadScheduleQueue && this.loadScheduleQueue(); } catch (_) {}
      });
      es.addEventListener('history:appended', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        // Reload via the same paginated endpoint — the in-place
        // reconcile keeps each row's <details> open/closed
        // state intact.
        try { this.loadHistory && this.loadHistory(); } catch (_) {}
      });
      // In-app notification dispatched by the `app` medium in
      // logic/ops.py:notify(). Self-filter is intentionally OFF so
      // EVERY tab gets the badge bump — operators want the badge to
      // tick even on the tab that triggered the op (the dispatcher
      // doesn't carry an X-OmniGrid-Client-Id, samplers + scheduler
      // are the most common publishers).
      es.addEventListener('notification:created', (e) => {
        onAny();
        try {
          const data = JSON.parse(e.data || '{}');
          const p = data.payload || {};
          console.log('[live] event=notification:created id=' + (p.id || ''));
          if (typeof this._handleNotificationCreated === 'function') {
            this._handleNotificationCreated(p);
          }
        } catch (_) {}
      });
      // Mark-read / mark-all-read echoes. Self-filter via the standard
      // _isSelfEvent path so the tab that issued the click doesn't
      // re-paint over its own optimistic update.
      es.addEventListener('notification:read', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        try {
          const data = JSON.parse(e.data || '{}');
          const p = data.payload || {};
          if (typeof this._handleNotificationRead === 'function') {
            this._handleNotificationRead(p);
          }
        } catch (_) {}
      });
      es.addEventListener('notification:deleted', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        try {
          const data = JSON.parse(e.data || '{}');
          const p = data.payload || {};
          if (typeof this._handleNotificationDeleted === 'function') {
            this._handleNotificationDeleted(p);
          }
        } catch (_) {}
      });
      // Settings changed — published by api_set_settings with the
      // originating tab's client_id. Self-filter via _isSelfEvent
      // skips this for the tab that did the save (it already has the
      // latest values from its own POST response). Other tabs reload
      // /api/settings so a setting flipped in one tab takes effect
      // everywhere within one SSE round-trip.
      es.addEventListener('settings:updated', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        console.log('[live] event=settings:updated → loadSettings (cross-tab)', e.data ? e.data.slice(0, 200) : '');
        try { this.loadSettings && this.loadSettings(); } catch (_) {}
        // Also pull /api/me so any client_config-delivered tunable
        // (poll cadences, fan-out caps) reflects the new value
        // without requiring a page reload. Field-by-field merge
        // (matching saveSettings's own /api/me refresh path) so the
        // cross-tab case doesn't tear down DOM bindings the way a
        // wholesale ``this.me = d`` would (Alpine Proxy identity
        // contract — see CLAUDE.md "Frontend reconciles ... in place"
        // rule). `me.notify_mediums` lives on this dict so the
        // per-medium grid reflects an admin's toggle flip on every
        // open tab within one SSE round-trip.
        try {
          fetch('/api/me', { cache: 'no-store' })
            .then(r => r.ok ? r.json() : null)
            .then(d => {
              if (d && d.authenticated && this.me) {
                for (const k of Object.keys(d)) this.me[k] = d[k];
              }
            })
            .catch(() => {});
        } catch (_) {}
      });
      // session-cookie sliding window. Backend's
      // `slide_session_if_needed` publishes this when it bumps the
      // cookie's expiry past the renewal threshold. SPA refreshes
      // `me.session_expires_at` (if exposed by /api/me) so any UI hint
      // ("session expires in X minutes") stays current. The event was
      // documented in api.md and CLAUDE.md but had no consumer pre-fix
      // — caught by the SSE-publisher-vs-consumer audit recipe.
      es.addEventListener('session:renewed', (e) => {
        onAny();
        if (this._isSelfEvent(e)) return;
        try {
          const data = JSON.parse(e.data || '{}');
          const exp = (data.payload && data.payload.expires_at) || null;
          console.log('[live] event=session:renewed expires_at=' + exp);
          // Belt-and-braces refresh of /api/me so any session-related
          // hint (expiry pill / countdown) re-hydrates without a full
          // page reload. Cheap call (cached server-side); skipped if
          // /api/me hasn't loaded yet (rare race).
          if (this.me && typeof fetch === 'function') {
            fetch('/api/me', { cache: 'no-store' })
              .then(r => r.ok ? r.json() : null)
              .then(d => { if (d && d.authenticated) this.me = d; })
              .catch(() => {});
          }
        } catch (_) {}
      });
      es.onerror = () => {
        if (this._sseConnected) {
          console.warn('[live] SSE error: connection dropped — falling back to polling until next open');
        }
        // EventSource auto-reconnects with its own backoff. We just
        // surface the visible "polling" badge until ``open`` fires
        // again. Belt-and-braces — the freshness watcher below also
        // flips the flag if no traffic arrives within the threshold.
        this._sseConnected = false;
      };
      // Freshness watchdog: flip _sseConnected to false if no event
      // (organic OR heartbeat) arrives within the idle threshold. EOL
      // catches the case where the TCP connection silently dies and
      // EventSource doesn't fire `error` immediately.
      if (this._sseFreshnessTimer) clearInterval(this._sseFreshnessTimer);
      this._sseFreshnessTimer = setInterval(() => {
        if (!this._sseLastEventTs) return;
        const idle = Date.now() - this._sseLastEventTs;
        // operator-tunable via `tuning_sse_idle_threshold_seconds`,
        // delivered as `client_config.sse_idle_threshold_ms`. Defensive
        // fallback to the historical 30000 covers the brief window
        // before /api/me hydrates AND any consumer of the legacy
        // `_sseIdleThresholdMs` property.
        const threshold = (this.me && this.me.client_config
                            && this.me.client_config.sse_idle_threshold_ms)
                          || this._sseIdleThresholdMs || 30000;
        if (idle > threshold) {
          if (this._sseConnected) {
            console.warn('[live] SSE freshness watchdog: ' + Math.round(idle / 1000) + 's since last event — flipping _sseConnected=false (polling fallback resumes)');
          }
          this._sseConnected = false;
        }
      }, 1000);
    },

    _handleOpEvent(e, phase) {
      try {
        if (this._isSelfEvent(e)) return;
        const data = JSON.parse(e.data || '{}');
        const p = data.payload || {};
        // Defer to pollOps's existing logic — it handles the linger
        // window, toast notifications, and the post-op refresh kicks.
        // Calling it as a one-off here gives the same effect as a
        // 1.5s tick landing on the operator's screen ~immediately.
        this.pollOpsNow();
        // Special-case: ``op:completed`` for an op we DID see running
        // also triggers the items refresh. pollOpsNow's own justDone
        // path picks this up so we don't double-fire here.
        void p; void phase;
      } catch (_) {}
    },

    // SSE self-filter check. Returns true when the incoming
    // event's payload.client_id matches the local tab's id (set by
    // auth-fetch.js into window.__ogClientId on first fetch). Used at
    // the top of every data-bearing handler so an SSE event published
    // off a request originating from THIS tab doesn't loop back as a
    // redundant refresh / flicker. Sampler / background-task publishers
    // don't pass client_id, so events from those paths never match
    // and the filter is a transparent no-op there.
    _isSelfEvent(e) {
      if (!e || !e.data) return false;
      const myId = window.__ogClientId;
      if (!myId) return false;
      try {
        const data = JSON.parse(e.data);
        const cid = data && data.payload && data.payload.client_id;
        if (cid && cid === myId) return true;
      } catch (_) {}
      return false;
    },
    // Single-parse SSE event unwrap. Returns the parsed event object
    // ({type, ts, payload}) when the event should be processed,
    // OR null when self-filter wins (caller should early-return).
    // Eliminates the per-handler "JSON.parse(e.data) twice" pattern —
    // _isSelfEvent does its own parse, then handlers parse again.
    // 13 handlers × 2 parses per event = unnecessary overhead on
    // fleet-wide ticks. Use:
    //   const evt = this._unwrapEventOrNull(e); if (!evt) return;
    //   const id = (evt.payload || {}).id;  // already parsed
    _unwrapEventOrNull(e) {
      if (!e || !e.data) return null;
      try {
        const data = JSON.parse(e.data);
        const myId = window.__ogClientId;
        const cid = data && data.payload && data.payload.client_id;
        if (myId && cid && cid === myId) return null;  // self-event, skip
        return data;
      } catch (_) {
        return null;  // malformed event, treat as skip
      }
    },

    // Helper for the toolbar indicator — exposes a concise status string
    // for the i18n-bound title attribute.
    sseStatusKey() {
      if (this._sseConnected) return 'events.connected_title';
      // Distinguish "never connected" from "dropped + retrying".
      if (this._sse && this._sseLastEventTs) return 'events.reconnecting_title';
      return 'events.disconnected_title';
    },

    // #486 enhancement — bracket every interval-poll fetch so the
    // topbar pill flashes green for the EXACT duration of the network
    // request (start of fetch → response landed). Counter-based so
    // concurrent polls (e.g. /api/items + /api/stats firing in the
    // same tick) don't end the flash prematurely on the first one
    // that returns. Off / Live modes short-circuit — Off shouldn't
    // poll, Live's green-on-event UX is already implicit in the SSE
    // pill colour.
    _pollStart() {
      if (this.refreshInterval === -1 || this.refreshInterval === 0) return;
      this._pollFlashCount = (this._pollFlashCount || 0) + 1;
      this._pollFlashing = true;
    },
    _pollEnd() {
      if (!this._pollFlashCount) return;
      this._pollFlashCount = Math.max(0, this._pollFlashCount - 1);
      if (this._pollFlashCount === 0) this._pollFlashing = false;
    },
    // Convenience wrapper — `await this._pollWrap(this.refresh(true))`
    // sets the flash on, awaits the promise, clears the flash in
    // finally. Returns the promise's resolved value so callers can
    // chain naturally.
    _pollWrap(promise) {
      this._pollStart();
      return Promise.resolve(promise).finally(() => this._pollEnd());
    },

    setAutoRefresh(seconds) {
      this.autoRefresh = seconds;
      try { localStorage.setItem('autoRefresh', String(seconds)); } catch {}
      if (this._autoTimer) clearInterval(this._autoTimer);
      if (seconds > 0) this._autoTimer = setInterval(() => {
        // Wrap in poll-flash brackets so the topbar pill stays green
        // for the duration of the actual /api/items round-trip.
        this._pollWrap(this.refresh(true));
      }, seconds * 1000);
    },

    // single canonical cadence-setter. Three modes mapped to
    // the picker's five buttons:
    //
    //   -1   "Live"   — SSE connection ON, every chart updates via
    //                   push events. Polling timers sleep.
    //    0   "Off"    — SSE connection CLOSED, polling sleeps. The
    //                   dashboard becomes a static snapshot of the
    //                   current state. Operator sees no more updates
    //                   until they pick another mode (or refresh).
    //   30/60/300     — SSE connection CLOSED, polling at the chosen
    //                   cadence drives every chart uniformly.
    //
    // Closing SSE for Off + interval modes is the load-bearing UX
    // fix — pre-fix the picker selected Off but the Live pill stayed
    // green because SSE was still pushing events; operators reported
    // "the picker doesn't do what it says". Now the SSE pill colour
    // is a direct read of the picker's choice.
    //
    // Mirrors the chosen polling cadence into legacy state vars
    // (`autoRefresh` for items poll + `statsInterval` for stats /
    // hosts polls) so the existing pollers don't need to be rewired.
    // Live and Off both map to legacy=0; only intervals drive the
    // pollers.
    setRefreshInterval(seconds) {
      const modeLabel = seconds === -1 ? 'Live (SSE)' : seconds === 0 ? 'Off' : seconds + 's interval';
      console.log('[live] setRefreshInterval: mode=' + modeLabel + ' (raw=' + seconds + ')');
      this.refreshInterval = seconds;
      try { localStorage.setItem('refreshInterval', String(seconds)); } catch {}
      const legacy = seconds === -1 ? 0 : seconds;
      this.setStatsInterval(legacy);
      this.setAutoRefresh(legacy);
      // SSE management — Live opens (or keeps open) the stream;
      // Off / interval modes close it so the picker is the single
      // source of truth for "is this dashboard receiving updates?".
      if (seconds === -1) {
        if (!this._sse) this._initSSE();
      } else {
        this._disconnectSSE();
      }
      // pollOps gates on `refreshInterval === 0` (see pollOps tick
      // body) — re-kick it whenever we transition AWAY from Off so
      // the panel comes back without waiting for a manual interaction.
      if (seconds !== 0 && !this._opsTimer) {
        try { this.pollOps(); } catch (_) {}
      }
      // Host-drawer history chart timer also follows the picker now
      //. Re-arm it under the new cadence whenever the operator
      // switches modes while the drawer is open. Off → clear; Live →
      // 30s baseline; interval → operator's chosen cadence.
      if (this._drawerHistoryTimer) {
        clearInterval(this._drawerHistoryTimer);
        this._drawerHistoryTimer = null;
      }
      // NE-only Live mode → push-driven → no timer.
      // Beszel / NE+Beszel Live → 30s timer (push event covers NE
      // half but Beszel data still needs polling). Interval modes
      // use the picker cadence regardless. Off → no timer.
      const dh = this.drawerHost;
      if (dh && (dh.beszel_id || dh.ne_url) && seconds !== 0) {
        const liveMode = seconds === -1;
        const pushOnly = liveMode && dh.ne_url && !dh.beszel_id;
        if (!pushOnly) {
          const ms = (liveMode ? 30 : seconds) * 1000;
          this._drawerHistoryTimer = setInterval(() => {
            if (!this.drawerHost) return;
            this._pollWrap(this.loadHostHistory(
              this.drawerHost.beszel_id || '',
              this.drawerHost.id,
            ));
          }, ms);
        }
      }
      // ping history timer follows the same picker cadence.
      // Live mode is push-driven by the `host:ping_sampled` SSE
      // handler so no timer needed; Off → no timer; interval → poll
      // at the operator's chosen cadence.
      if (this._drawerPingTimer) {
        clearInterval(this._drawerPingTimer);
        this._drawerPingTimer = null;
      }
      if (dh && dh.ping_enabled && seconds !== 0 && seconds !== -1) {
        const pingMs = seconds * 1000;
        this._drawerPingTimer = setInterval(() => {
          if (!this.drawerHost || !this.drawerHost.ping_enabled) return;
          this._pollWrap(this.loadHostPingHistory(this.drawerHost.id));
        }, pingMs);
      }
      // Sparklines — Off kills the timer; Live / interval
      // modes restart at the 5min baseline (sparklines are coarse
      // 24h aggregates, the picker doesn't change their cadence).
      if (this._sparksTimer) {
        clearInterval(this._sparksTimer);
        this._sparksTimer = null;
      }
      if (seconds !== 0) {
        try { this.pollSparks(); } catch (_) {}
      }
    },

    get counts() {
      const c = { update:0, uptodate:0, unknown:0, error:0, ignored:0, healthy:0, degraded:0, offline:0 };
      for (const i of this.items) {
        if (i.status==='update') c.update++;
        else if (i.status==='up-to-date') c.uptodate++;
        else if (i.status==='unknown') c.unknown++;
        else if (i.status==='error') c.error++;
        else if (i.status==='ignored') c.ignored++;
        if (i.health==='healthy') c.healthy++;
        else if (i.health==='degraded') c.degraded++;
        else if (i.health==='offline') c.offline++;
      }
      return c;
    },
    get filteredStacks() {
      const q = this.search.toLowerCase();
      return this.stacks
        .map(s => ({ ...s, items: s.items.filter(i => this.matches(i, q)) }))
        .filter(s => s.items.length > 0);
    },
    get filteredItems() {
      const q = this.search.toLowerCase();
      return this.items.filter(i => this.matches(i, q));
    },
    get sortedFiltered() {
      const arr = [...this.filteredItems];
      const f = this.sortField, dir = this.sortDir === 'asc' ? 1 : -1;
      const statusRank = { update:0, error:1, unknown:2, 'up-to-date':3, ignored:4 };
      arr.sort((a,b) => {
        let va, vb;
        if (f === 'status') {
          va = statusRank[a.status] ?? 99;
          vb = statusRank[b.status] ?? 99;
        } else if (f === 'uptime') {
          // uptimeFor returns ms since epoch (start time). Newer starts = larger
          // number, and we want "youngest first" when ascending. Missing values
          // sort last regardless of direction.
          const ua = this.uptimeFor(a);
          const ub = this.uptimeFor(b);
          if (ua == null && ub == null) return 0;
          if (ua == null) return 1;
          if (ub == null) return -1;
          va = ua; vb = ub;
        } else {
          va = (a[f]||'').toString().toLowerCase();
          vb = (b[f]||'').toString().toLowerCase();
        }
        if (va < vb) return -1 * dir;
        if (va > vb) return 1 * dir;
        return 0;
      });
      return arr;
    },
    matches(item, q) {
      if (q) {
        const hay = [item.name, item.image, item.stack, item.tag].filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (this.statusFilter && item.status !== this.statusFilter) return false;
      if (this.healthFilter && item.health !== this.healthFilter) return false;
      return true;
    },

    applyTheme() {
      const sysLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
      const resolved = this.themePref === 'auto' ? (sysLight ? 'light' : 'dark') : this.themePref;
      document.documentElement.setAttribute('data-theme', resolved);
    },
    cycleTheme() {
      const order = ['auto', 'light', 'dark'];
      this.themePref = order[(order.indexOf(this.themePref) + 1) % order.length];
      localStorage.setItem('theme', this.themePref);
      this.applyTheme();
    },
    _cssVar(name) {
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    },

    _busyKey(kind, id) { return `${kind}:${id}`; },
    isStackBusy(stack) {
      if (!stack || !stack.stack_id) return false;
      if (this.busy[this._busyKey('stack', stack.stack_id)]) return true;
      return this.activeOps.some(o => o.op_type === 'update_stack' && String(o.target_id) === String(stack.stack_id));
    },
    isItemBusy(item) {
      if (!item) return false;
      if (this.busy[this._busyKey('ctn', item.raw_id)]) return true;
      if (item.type === 'orphan') {
        return this.activeOps.some(o => o.op_type === 'remove_container' && o.target_id === item.raw_id);
      }
      if (item.stack_id) return this.isStackBusy({ stack_id: item.stack_id });
      if (item.type === 'container') {
        return this.activeOps.some(o => ['update_container','remove_container','restart_container'].includes(o.op_type) && o.target_id === item.raw_id);
      }
      return false;
    },
    isServiceBusy(item) {
      if (!item) return false;
      if (this.busy[this._busyKey('svc', item.raw_id)]) return true;
      return this.activeOps.some(o => o.op_type === 'restart_service' && o.target_id === item.raw_id);
    },
    isRestartBusy(item) {
      if (!item) return false;
      if (item.type === 'service') return this.isServiceBusy(item);
      return this.isItemBusy(item);
    },
    _busyTimers: {},
    _markBusy(key) {
      this.busy = { ...this.busy, [key]: true };
      if (this._busyTimers[key]) clearTimeout(this._busyTimers[key]);
      this._busyTimers[key] = setTimeout(() => {
        delete this._busyTimers[key];
        if (this.busy[key]) { const n = {...this.busy}; delete n[key]; this.busy = n; }
      }, 3000);
    },
    _holdBusy(key) {
      if (this._busyTimers[key]) { clearTimeout(this._busyTimers[key]); delete this._busyTimers[key]; }
      if (!this.busy[key]) this.busy = { ...this.busy, [key]: true };
    },
    _clearBusy(key) {
      if (this._busyTimers[key]) { clearTimeout(this._busyTimers[key]); delete this._busyTimers[key]; }
      if (this.busy[key]) { const n = {...this.busy}; delete n[key]; this.busy = n; }
    },
    _opBusyKey(op) {
      if (!op) return null;
      if (op.op_type === 'update_stack') return this._busyKey('stack', op.target_id);
      if (['update_container','remove_container','restart_container'].includes(op.op_type)) return this._busyKey('ctn', op.target_id);
      if (op.op_type === 'restart_service') return this._busyKey('svc', op.target_id);
      return null;
    },

    statusKey(s) { return (s || 'unknown').replace('up-to-date','ok'); },
    statsFor(item) {
      return (item && this.stats[item.id]) || { cpu_percent: 0, mem_usage: 0, mem_limit: 0, size_root: 0, size_rw: 0, has_stats: false, has_size: false };
    },
    // Split a host's network_ifaces into "real" (public-facing physical /
    // virtual NICs the operator cares about) and "internal" (docker veth
    // pairs, br-<id> bridges, docker0, docker_gwbridge, lo). On a Docker
    // host with N running containers there are N+ veths plus the bridge
    // pool — this turned the drawer's Network card into a 50-row wall.
    // The "real" list renders by default; the "internal" group is hidden
    // behind a toggle keyed by host.id in `networkIfacesShowDocker` so
    // each host can be expanded independently.
    networkIfacesPartition(h) {
      const ifaces = (h && h.network_ifaces) || [];
      const ifaceName = x => (typeof x === 'string' ? x : (x && x.name) || '');
      const isInternal = name => {
        const n = (name || '').toLowerCase();
        return n === 'lo'
            || n === 'docker0'
            || n === 'docker_gwbridge'
            || n.startsWith('veth')
            || n.startsWith('br-')      // docker bridge networks (br-<12hex>)
            || n.startsWith('cni')      // k8s pod network
            || n.startsWith('flannel')
            || n.startsWith('cali')     // calico
            || n.startsWith('kube-')
            || n.startsWith('vxlan')
            || n.startsWith('vmbr');    // proxmox bridge (when surfaced)
      };
      const real = [], internal = [];
      for (const iface of ifaces) {
        (isInternal(ifaceName(iface)) ? internal : real).push(iface);
      }
      return { real, internal };
    },
    networkIfacesShowDocker: {},  // {host_id: bool} — toggle map per host
    toggleNetworkIfacesDocker(h) {
      if (!h || !h.id) return;
      this.networkIfacesShowDocker[h.id] = !this.networkIfacesShowDocker[h.id];
    },
    // busy / idle split for switches that expose 30+ ports via
    // SNMP. The default "real" list mixes meaningful interfaces
    // (eth0 / vlan2 / tun1 — has traffic OR an IP) with idle ports
    // (Port-Channel21..32, unused TwoPointFiveGigabitEthernet ports —
    // no traffic, no addrs). Without this split a Cisco SG300's drawer
    // is a 50-row wall of mostly-empty rows. `busy` = at least one of
    // (rx_bytes, tx_bytes, addrs.length) is non-zero; everything else
    // falls into `idle` and is hidden behind a per-host toggle.
    networkIfacesActivityPartition(h) {
      const partition = this.networkIfacesPartition(h);
      const busy = [], idle = [];
      for (const iface of partition.real) {
        const hasTraffic = this.hostIfaceHasTraffic(iface);
        const hasAddrs = (typeof iface === 'object' && (iface.addrs || []).length > 0);
        // Treat the loopback as "busy" so it always renders; operators
        // expect to see it and it's a useful sanity-check signal.
        const name = (typeof iface === 'object' ? (iface.name || '') : iface).toLowerCase();
        const isLoopback = name === 'lo' || name === 'loopback';
        (hasTraffic || hasAddrs || isLoopback ? busy : idle).push(iface);
      }
      // Sort busy by total traffic descending so the dominant NIC
      // floats to the top — operator's eye reads the meaningful rows
      // first instead of having to scan a long alphabetical list.
      busy.sort((a, b) => {
        const ta = (+(a && a.rx_bytes) || 0) + (+(a && a.tx_bytes) || 0);
        const tb = (+(b && b.rx_bytes) || 0) + (+(b && b.tx_bytes) || 0);
        return tb - ta;
      });
      return { busy, idle, internal: partition.internal };
    },
    networkIfacesShowIdle: {},  // per-host toggle for the idle group
    toggleNetworkIfacesIdle(h) {
      if (!h || !h.id) return;
      this.networkIfacesShowIdle[h.id] = !this.networkIfacesShowIdle[h.id];
    },
    // Cap the busy-iface list to the top 10 by traffic, with a
    // per-host "Show all (N)" toggle. Switches with 52+ ports overflow
    // the drawer even after the show-idle filter; the operator wants
    // the loudest 10 by default and can opt into the rest.
    networkIfacesBusyCap: 10,
    networkIfacesShowAllBusy: {},  // per-host toggle for the busy-cap group
    toggleNetworkIfacesBusyAll(h) {
      if (!h || !h.id) return;
      this.networkIfacesShowAllBusy[h.id] = !this.networkIfacesShowAllBusy[h.id];
    },
    networkIfacesBusyVisible(h) {
      const busy = this.networkIfacesActivityPartition(h).busy;
      if (this.networkIfacesShowAllBusy[h.id]) return busy;
      return busy.slice(0, this.networkIfacesBusyCap);
    },
    networkIfacesBusyHiddenCount(h) {
      const busy = this.networkIfacesActivityPartition(h).busy;
      return Math.max(0, busy.length - this.networkIfacesBusyCap);
    },
    // per-interface SNMP traffic helpers. SNMP-derived
    // `network_ifaces[]` rows carry `rx_bytes` / `tx_bytes` /
    // `oper_status`; node-exporter / Beszel / Pulse rows have
    // `name` + `mac` + `addrs` but no traffic counters. Helpers
    // gracefully no-op when the rows lack the SNMP fields so
    // non-SNMP hosts don't see empty traffic rows.
    hostIfaceHasTraffic(iface) {
      if (!iface || typeof iface !== 'object') return false;
      const rx = +iface.rx_bytes || 0;
      const tx = +iface.tx_bytes || 0;
      return rx > 0 || tx > 0;
    },
    // Largest rx+tx total across the host's REAL interfaces — used to
    // normalise per-interface bar widths so the busiest NIC fills 100%
    // and the rest scale relative. Internal (docker / veth / etc.)
    // interfaces are excluded from the max so a noisy docker0 doesn't
    // drown the legitimate eth0/wlan0 traffic visually.
    hostIfaceMaxTotal(h) {
      const ifaces = this.networkIfacesPartition(h).real || [];
      let max = 0;
      for (const i of ifaces) {
        if (!this.hostIfaceHasTraffic(i)) continue;
        const t = (+i.rx_bytes || 0) + (+i.tx_bytes || 0);
        if (t > max) max = t;
      }
      return max;
    },
    // Inline `:style` for an interface's stacked rx/tx bar. `--rx-pct`
    // and `--tx-pct` are rendered as widths inside the bar. Returns
    // empty string when no traffic data is available so the template
    // can short-circuit without rendering an empty bar.
    hostIfaceBarStyle(iface, maxTotal) {
      if (!this.hostIfaceHasTraffic(iface) || !(maxTotal > 0)) return '';
      const total = (+iface.rx_bytes || 0) + (+iface.tx_bytes || 0);
      const totalPct = (total / maxTotal) * 100;
      const rxShare = total > 0 ? (+iface.rx_bytes || 0) / total : 0;
      const rxPct = totalPct * rxShare;
      const txPct = totalPct - rxPct;
      return `--rx-pct: ${rxPct.toFixed(2)}%; --tx-pct: ${txPct.toFixed(2)}%;`;
    },
    // True iff at least one REAL interface on this host has SNMP
    // traffic counters. Drives the optional traffic block's x-show
    // gate so non-SNMP hosts don't see an empty card.
    hostHasIfaceTraffic(h) {
      const ifaces = this.networkIfacesPartition(h).real || [];
      return ifaces.some(i => this.hostIfaceHasTraffic(i));
    },
    // UPS card helpers (APC PowerNet-MIB). Pill class for the
    // status badge, level class for the battery gauge (matching the
    // .stat-bar warn/crit convention), and human-readable runtime
    // formatter. All gracefully handle missing data; the card itself
    // is gated on `h.host_ups_status || h.host_battery_percent` in
    // the template.
    upsStatusPillClass(status) {
      const s = String(status || '').toLowerCase();
      if (s === 'online') return 'pill-ok';
      if (s === 'on-battery' || s === 'on-smart-boost' || s === 'on-smart-trim') return 'pill-update';
      if (s === 'off' || s === 'rebooting' || s.includes('bypass')
          || s === 'hardware-failure-bypass' || s === 'sleeping-until') return 'pill-error';
      return 'pill-unknown';
    },
    upsStatusLabel(status) {
      // Pretty-print the snake-case enum from PowerNet-MIB. i18n keys
      // exist for the canonical set; unknown values fall through to
      // the raw enum string.
      const s = String(status || '').toLowerCase();
      const key = `host_drawer.ups.status_${s.replace(/-/g, '_')}`;
      const translated = this.t(key);
      return (translated && translated !== key) ? translated : (status || '');
    },
    upsBatteryLevel(pct) {
      // Inverse of the .stat-bar warn/crit semantics — for batteries,
      // LOW is bad. <20% = crit (red), <50% = warn (amber), else ok.
      const n = +pct;
      if (!Number.isFinite(n)) return '';
      if (n < 20) return 'crit';
      if (n < 50) return 'warn';
      return '';
    },
    // Battery status enum (from PowerNet-MIB upsBasicBatteryStatus).
    // Operator-requested: render as a coloured pill instead of the
    // raw "battery-normal" string. battery-normal → green (pill-ok),
    // battery-low → amber (pill-update), battery-in-fault → red
    // (pill-error), unknown → grey (pill-unknown). Mirror of
    // `upsStatusPillClass` for the output-status badge.
    upsBatteryStatusPillClass(status) {
      const s = String(status || '').toLowerCase();
      if (s === 'battery-normal') return 'pill-ok';
      if (s === 'battery-low') return 'pill-update';
      if (s === 'battery-in-fault') return 'pill-error';
      return 'pill-unknown';
    },
    upsBatteryStatusLabel(status) {
      // Pretty-print the snake-case enum from PowerNet-MIB. i18n keys
      // exist for the canonical set; unknown values fall through to
      // the raw enum string. Same shape as `upsStatusLabel` so the
      // label / pill pair stays consistent across translations.
      const s = String(status || '').toLowerCase();
      const key = `host_drawer.ups.battery_${s.replace(/-/g, '_')}`;
      const translated = this.t(key);
      return (translated && translated !== key) ? translated : (status || '');
    },
    // Dell server-health pill helpers (#848 phase 2). All four lean
    // on the standard Dell Systems Management Server Health enum
    // (ok / non-critical / critical / non-recoverable / unknown /
    // other). Two flavours: server-health row status (fans / temps /
    // PSUs / voltages — string label like "ok" / "critical") and
    // physical/virtual disk state (string label like "online" /
    // "rebuild" / "failed"). The pill-* token family is reused so
    // the colour family matches every other status pill in the SPA.
    dellHealthPillClass(status) {
      const s = String(status || '').toLowerCase();
      if (s === 'ok') return 'pill-ok';
      if (s === 'non-critical') return 'pill-update';
      if (s === 'critical' || s === 'non-recoverable') return 'pill-error';
      return 'pill-unknown';
    },
    dellHealthLabel(status) {
      // i18n key family mirrors upsBatteryStatusLabel — unknown values
      // capitalise the raw string instead of crashing OR rendering the
      // raw lowercase wire form ("ok" / "critical").
      const s = String(status || '').toLowerCase();
      if (!s) return '';
      const key = `host_drawer.server_health.status_${s.replace(/-/g, '_')}`;
      const translated = this.t(key);
      if (translated && translated !== key) return translated;
      return s.charAt(0).toUpperCase() + s.slice(1);
    },
    // Physical-disk state pill — Dell OMSA arrayDiskState labels.
    // Visual encoding:
    //   green  (pill-ok)     — `online` (active in a RAID array, healthy)
    //   blue   (pill-info)   — `ready` (present, idle, available — standby)
    //   red    (pill-error)  — `failed` / `offline` / `degraded` / `removed` / `fault`
    //   amber  (pill-update) — every transient / advisory state.
    // Distinguishing online (active member) from ready (idle spare) lets
    // operators tell at a glance which disks are CARRYING the array vs
    // which are sitting idle for hot-swap / hot-spare duty.
    dellPdStatePillClass(state) {
      const s = String(state || '').toLowerCase();
      if (s === 'online') return 'pill-ok';
      if (s === 'ready') return 'pill-info';
      if (s === 'failed' || s === 'offline' || s === 'degraded'
          || s === 'removed' || s === 'fault') return 'pill-error';
      if (s === 'rebuild' || s === 'rebuilding' || s === 'recovering'
          || s === 'replacing' || s === 'replaced'
          || s === 'foreign' || s === 'blocked' || s === 'clear'
          || s === 'non-raid' || s === 'ready-foreign'
          || s === 'read-only' || s === 'uncertified'
          || s === 'smart-alert' || s === 'predictive-failure') return 'pill-update';
      return 'pill-unknown';
    },
    // Virtual-disk state pill — same online/ready split as physical
    // disks. `online` = array carrying I/O; `ready` = array initialised
    // but idle / standby.
    dellVdStatePillClass(state) {
      const s = String(state || '').toLowerCase();
      if (s === 'online') return 'pill-ok';
      if (s === 'ready') return 'pill-info';
      if (s === 'failed' || s === 'offline'
          || s === 'failed-redundancy'
          || s === 'permanently-degraded') return 'pill-error';
      if (s === 'degraded' || s === 'verifying' || s === 'resynching'
          || s === 'regenerating' || s === 'rebuilding'
          || s === 'formatting' || s === 'reconstructing'
          || s === 'initializing' || s === 'background-init'
          || s === 'degraded-redundancy') return 'pill-update';
      return 'pill-unknown';
    },
    // Disk-state pill label resolver — try i18n first, fall back to a
    // capitalised version of the raw enum string. Mirrors the
    // dellHealthLabel pattern: `host_drawer.server_health.status_<name>`
    // is the canonical key family (hyphens become underscores so
    // `non-raid` → `status_non_raid`). Lower-case at the source-of-truth
    // (Dell OMSA enum constants in logic/snmp.py) stays the wire format;
    // this helper applies translation per locale + capitalisation
    // fallback for novel enum values that don't have a key yet.
    dellStateLabel(state) {
      const s = String(state || '').toLowerCase();
      if (!s) return '';
      const key = 'host_drawer.server_health.status_' + s.replace(/-/g, '_');
      const tr = this.t(key);
      if (tr && tr !== key) return tr;
      return s.charAt(0).toUpperCase() + s.slice(1);
    },
    // Threshold at which Server health sub-sections (Physical disks /
    // Voltages) collapse by default. Above this count the dense
    // multi-column layout kicks in AND a "Show all (N) / Show fewer"
    // toggle gates the visible row count to keep the panel compact.
    SERVER_HEALTH_COLLAPSE_THRESHOLD: 12,
    // First-N rows shown when the section is collapsed. Tuned so the
    // collapsed view spans roughly two-three rows of the dense
    // multi-column grid (auto-fit minmax(190px, 1fr) → 2-4 columns
    // depending on drawer width × ~2 rows ≈ 6 visible items).
    SERVER_HEALTH_COLLAPSED_LIMIT: 6,
    // Narrow-viewport row cap. Below 480px the dense multi-column grid
    // collapses to a single column AND the outer card collapses to a
    // single column too — leaving the collapsed view at 6 rows still
    // feels long on a phone. Drop to 3 below 480px so the collapsed
    // section fits one screen on a phone. Pair this with the existing
    // `SERVER_HEALTH_COLLAPSED_LIMIT` desktop default in
    // `effectiveCollapsedLimit()`.
    SERVER_HEALTH_COLLAPSED_LIMIT_NARROW: 3,
    SERVER_HEALTH_NARROW_BREAKPOINT_PX: 480,
    // Resolve the collapsed-row limit per current viewport width. Reads
    // `window.innerWidth` so it auto-adjusts on rotate / resize without
    // requiring a manual refresh. Caller (serverHealthVisibleRows) reads
    // this on every render so the slice tracks the live viewport.
    effectiveCollapsedLimit() {
      const w = (typeof window !== 'undefined') ? (window.innerWidth || 0) : 0;
      if (w > 0 && w < this.SERVER_HEALTH_NARROW_BREAKPOINT_PX) {
        return this.SERVER_HEALTH_COLLAPSED_LIMIT_NARROW;
      }
      return this.SERVER_HEALTH_COLLAPSED_LIMIT;
    },
    // Returns the slice of rows to render given the host id + section
    // key + the underlying full-list array. When count > threshold AND
    // the section isn't expanded, returns the first N; otherwise
    // returns the full array. Section key namespaces the expand state
    // (`pd` / `volt`) so toggling Physical disks doesn't also expand
    // Voltages on the same host.
    serverHealthVisibleRows(hostId, section, rows) {
      if (!Array.isArray(rows)) return [];
      if (rows.length <= this.SERVER_HEALTH_COLLAPSE_THRESHOLD) return rows;
      const key = `${hostId}:${section}`;
      if (this.serverHealthExpanded[key]) return rows;
      return rows.slice(0, this.effectiveCollapsedLimit());
    },
    // True when the section has more rows than fit in the collapsed
    // view (so the toggle should render). False when count is at or
    // below the threshold (no collapse needed).
    serverHealthCollapsible(rows) {
      return Array.isArray(rows) && rows.length > this.SERVER_HEALTH_COLLAPSE_THRESHOLD;
    },
    serverHealthIsExpanded(hostId, section) {
      return !!this.serverHealthExpanded[`${hostId}:${section}`];
    },
    toggleServerHealthExpanded(hostId, section) {
      const key = `${hostId}:${section}`;
      this.serverHealthExpanded[key] = !this.serverHealthExpanded[key];
    },
    fmtUpsRuntime(seconds) {
      const s = +seconds;
      if (!Number.isFinite(s) || s <= 0) return '—';
      if (s < 60) return s.toFixed(0) + 's';
      const mins = Math.floor(s / 60);
      if (mins < 60) {
        const rem = Math.floor(s % 60);
        return rem ? `${mins}m ${rem}s` : `${mins}m`;
      }
      const hrs = Math.floor(mins / 60);
      const remMins = mins % 60;
      return remMins ? `${hrs}h ${remMins}m` : `${hrs}h`;
    },
    // Printer-MIB supply card helpers. Per-supply colour is
    // hand-mapped from common toner names (cyan / magenta / yellow /
    // black / waste); falls through to a neutral colour for unmapped
    // supplies. Level class follows the .stat-bar warn/crit
    // convention with INVERSE semantics (low fill = bad).
    printerSupplyColor(supply) {
      const name = String(supply && supply.name || '').toLowerCase();
      // Each colour token comes from :root so light + dark themes stay
      // consistent. CMYK-style names get their named colours; "waste"
      // / "drum" / "fuser" / etc. fall to text-dim.
      if (name.includes('cyan'))    return 'var(--info)';
      if (name.includes('magenta')) return '#ec4899';
      if (name.includes('yellow')) return 'var(--warning)';
      if (name.includes('black'))   return 'var(--text)';
      if (name.includes('waste'))   return 'var(--text-faint)';
      return 'var(--text-dim)';
    },
    printerSupplyLevel(supply) {
      // Inverse semantics — low fill = warn / crit. Operator wants a
      // "running out" signal, not a "running high" one.
      const pct = supply && supply.percent;
      const n = +pct;
      if (!Number.isFinite(n)) return '';
      if (n < 10) return 'crit';
      if (n < 25) return 'warn';
      return '';
    },
    // Set of providers that returned data for AT LEAST ONE host on
    // the most recent /api/hosts response. Used by `providerStates()`
    // to suppress chips for globally-broken providers — if pulse
    // failed cluster-wide (operator typo'd the URL, hub container
    // down), we DON'T blame every individual host with `pulse_name`
    // set; those are global-config issues, not per-host problems.
    // Recomputed cheaply each time providerStates() runs since the
    // hosts list isn't huge.
    providersWorkingGlobally() {
      const seen = new Set();
      for (const h of (this.hosts || [])) {
        for (const p of (h.providers || [])) seen.add(p);
      }
      return seen;
    },
    // Per-host provider chip states. Returns an array of
    //   { name: <provider>, state: 'ok' | 'failing' }
    // for chips that should render. Rules:
    //   1. Provider must be mapped on this host (the relevant
    //      `<provider>_name` / `ne_url` field is set).
    //   2. Provider must be globally enabled.
    //   3. Provider must be GLOBALLY HEALTHY (returned data for at
    //      least one host on this fleet). If pulse fails cluster-
    //      wide, the chip disappears from every host — that's a
    //      global-config issue, not a per-host one. The operator
    //      fixes the hub URL once, not on N hosts.
    //   4. State derivation:
    //      - 'ok'      → provider hit on THIS host AND its self-
    //                    reported status (when applicable) is not
    //                    paused/down/unreachable.
    //      - 'failing' → mapped on this host but provider didn't hit
    //                    here OR returned data with a paused/down
    //                    self-status. Chip turns red.
    // Per-provider chip colour resolver. Resolution order:
    //   1. `this.settings.provider_color_<name>` — live admin form
    //      state, hydrated by `loadSettings()` at init AND on every
    //      Save. This is what makes the chip-style update REACTIVELY
    //      as the operator drags the colour input around (Alpine
    //      tracks `settings` as a dependency of every binding that
    //      calls into here).
    //   2. `me.client_config.provider_colors[<name>]` — fallback for
    //      readonly viewers who don't fetch /api/settings. Reads from
    //      the snapshot the server stamped at the most recent
    //      /api/me round-trip, so a colour change here only takes
    //      effect on the next /api/me — fine for "view-only" users.
    //   3. Built-in distinct default so an unconfigured deploy still
    //      shows five different chip colours instead of two-or-three
    //      shared hues (pre-fix, ping shared node-exporter's amber
    //      and operators couldn't tell them apart in the row chips).
    providerColor(name) {
      const defaults = {
        beszel:        '#22c55e',  // green  (matches pill-ok hue)
        pulse:         '#3b82f6',  // blue   (matches pill-info hue)
        node_exporter: '#f59e0b',  // amber  (matches pill-update hue)
        webmin:        '#a78bfa',  // purple (distinct slot for the 4th provider)
        ping:          '#06b6d4',  // cyan   (distinct from amber + green; was conflating with exporter)
        snmp:          '#ec4899',  // pink   (sixth provider; distinct from the existing five)
      };
      // Live admin-form value first (reactive on every keystroke / save).
      const live = ((this.settings || {})['provider_color_' + name] || '').trim();
      if (live) return live;
      // Server-stamped snapshot for non-admin viewers.
      const map = (this.me && this.me.client_config && this.me.client_config.provider_colors) || {};
      const v = (map[name] || '').trim();
      return v || defaults[name] || 'currentColor';
    },
    // Inline style triplet for .chip.pill-custom — three CSS variables
    // (--chip-bg, --chip-br, --chip-fg) derived from the provider
    // colour via color-mix so the chip stays a soft tinted token rather
    // than a saturated background. The same pattern works for any
    // future dynamic-colour chip (asset categories, group badges, etc.).
    providerChipStyle(name) {
      const c = this.providerColor(name);
      return (
        '--chip-bg: color-mix(in srgb, ' + c + ' 18%, transparent); ' +
        '--chip-br: color-mix(in srgb, ' + c + ' 40%, transparent); ' +
        '--chip-fg: ' + c + ';'
      );
    },
    // Hex-colour normaliser — used by the per-provider chip
    // colour text input alongside the native colour picker. Operators
    // can paste any of these forms and have them coerced into the
    // canonical `#rrggbb` shape the backend's `^#[0-9a-fA-F]{6}$`
    // validator accepts:
    //   - blank / whitespace → "" (means "use the SPA's default")
    //   - "1e63d4" / "1E63D4" → "#1e63d4"  (auto-prepend #, lowercase)
    //   - "#1E63D4" / "#1e63d4" → "#1e63d4" (lowercase only)
    //   - anything else → returned verbatim so the input's `pattern`
    //     attribute can flag it as invalid (red ring) without us
    //     silently swallowing operator-typed garbage.
    // The native `<input type="color">` writes its own `#rrggbb` when
    // the operator drags the picker; this helper only fires when the
    // operator types directly into the text input. Both inputs
    // x-model the same `settings.provider_color_<name>` field so they
    // stay synced regardless of which one was edited last.
    normalizeHexColor(raw) {
      const v = (raw || '').trim();
      if (!v) return '';
      const bareHex = /^[0-9a-fA-F]{6}$/;
      const fullHex = /^#[0-9a-fA-F]{6}$/;
      if (bareHex.test(v)) return '#' + v.toLowerCase();
      if (fullHex.test(v)) return v.toLowerCase();
      return v;  // invalid — let the input's `pattern` flag it
    },
    // Provider name → /img/icons/<slug>.svg filename. Mostly
    // identity except for `node_exporter` → `node-exporter` (the
    // resolver convention prefers hyphens over underscores in icon
    // filenames). Returns the bare slug; the consumer wraps it in
    // `url(/img/icons/<slug>.svg)` for the mask-image binding.
    providerIconSlug(name) {
      if (name === 'node_exporter') return 'node-exporter';
      return name;
    },
    // Inline style for `.provider-icon` — paints a mono SVG
    // mask in the per-provider chip colour. Use this on a `<span>`
    // when you want the provider's icon recoloured by the operator's
    // chip-colour customisation. Pairs naturally with
    // `providerChipStyle()` when the icon sits inside a `pill-custom`
    // chip (the parent's `--chip-fg` already provides the colour, so
    // the icon picks it up via `currentColor` automatically). Use this
    // helper when the icon stands ALONE (e.g. tab strip) where there's
    // no surrounding chip to inherit from.
    providerIconStyle(name) {
      return (
        '--provider-icon-url: url(/img/icons/' + this.providerIconSlug(name) + '.svg); '
        + 'color: ' + this.providerColor(name) + ';'
      );
    },
    providerStates(h) {
      if (!h) return [];
      const active = this.hostsActiveSources || [];
      const globalOk = this.providersWorkingGlobally();
      const got = new Set(h.providers || []);
      const pause = (h && h.provider_pause_state) || {};
      const out = [];
      const badStatus = v => {
        const s = String(v || '').toLowerCase();
        return s === 'paused' || s === 'down' || s === 'unreachable';
      };
      // Row-level loading flag — true while `/api/hosts/one/{id}` is
      // in flight for this row OR before the first such call has
      // landed. We use it to flag "probe hasn't replied yet" as a
      // distinct state from "probe replied with no data" so the chip
      // doesn't render red (= broken) when the truth is "still
      // fetching". Replaced by the real per-provider state once data
      // lands.
      const rowLoading = h._loading === true;
      // Per-(provider, host) polling map populated by the
      // `host:provider_probing` / `host:provider_done` SSE events.
      // Even after the row's overall `_loading` flips false (because
      // the response landed for the providers that finished first),
      // a slow per-provider probe still in flight keeps `_polling[p]`
      // truthy. Chip pulses while EITHER row-loading OR its own
      // per-provider polling flag is set.
      const polling = (h._polling && typeof h._polling === 'object') ? h._polling : {};
      const add = (name, mapped, selfStatus) => {
        if (!mapped) return;
        if (!active.includes(name)) return;
        // Per-(provider, host) auto-pause wins over every
        // other state — operator has explicitly marked this provider
        // off for this host until they manually resume it. Render the
        // 'paused' chip even when the provider isn't globally-OK so
        // the Resume button stays reachable.
        const pauseRow = pause[name];
        if (pauseRow && pauseRow.paused) {
          out.push({
            name,
            state: 'paused',
            consecutive_failures: Number(pauseRow.consecutive_failures || 0),
            last_error: String(pauseRow.last_error || ''),
            paused_at: Number(pauseRow.paused_at || 0),
          });
          return;
        }
        // Globally-broken provider — suppress the chip entirely so
        // operators see the failure once in Settings (not N times in
        // the Hosts grid). Exception: if THIS host got data from it,
        // the provider IS working — render the ok chip.
        if (!globalOk.has(name) && !got.has(name)) return;
        let state;
        if (!got.has(name)) {
          // Probe hasn't returned a hit for this provider yet. If the
          // row itself is still in flight OR this specific provider's
          // probe is currently in flight (per the SSE polling map),
          // it's not "failing" — it's pending. Render with a subtle
          // pulse animation in the provider's actual color via
          // `.chip-loading`.
          state = (rowLoading || polling[name] === true) ? 'loading' : 'failing';
        } else if (polling[name] === true) {
          // Provider previously hit BUT a fresh probe is currently
          // in flight (e.g. force-refresh, drawer reopen). Pulse the
          // chip to communicate "re-fetching" while keeping the chip
          // in its known-good colour rather than dropping back to
          // loading-grey.
          state = 'loading';
        } else if (badStatus(selfStatus)) {
          state = 'failing';
        } else {
          state = 'ok';
        }
        out.push({ name, state });
      };
      add('beszel',        !!(h.beszel_name && String(h.beszel_name).trim()), h.beszel_status);
      add('pulse',         !!(h.pulse_name  && String(h.pulse_name).trim()),  h.pulse_status);
      add('node_exporter', !!(h.ne_url      && String(h.ne_url).trim()),      null);
      add('webmin',        !!(h.webmin_name && String(h.webmin_name).trim()), null);
      // Ping is per-host opt-in (no name/URL field — just a boolean
      // toggle). The chip turns red when the latest sample says
      // alive=false; that's the closest analog to beszel_status='down'
      // for a transport that IS the up/down signal. `ping_alive` is
      // null until the sampler fires for the first time — we don't
      // want that "no data yet" case to render a misleading red chip,
      // so only flip to 'down' when the value is explicitly false.
      add('ping',          !!h.ping_enabled, h.ping_alive === false ? 'down' : null);
      // SNMP — chip renders when the row has a snmp_name
      // alias AND `snmp.enabled === true` (#654 opt-in / #714 fix).
      // Same rules as the other providers: globally enabled, globally
      // healthy, hit on this host = ok, mapped-but-no-hit = failing.
      // SNMP doesn't carry its own self-status field (unlike
      // beszel_status) so the badStatus check no-ops by passing null.
      add('snmp',          !!(h.snmp_name && String(h.snmp_name).trim()
                              && h.snmp_enabled === true), null);
      return out;
    },
    // Stale-marker helpers for the UI.
    //
    // Backend stamps two markers on cache-seeded entries:
    //   1. `_stats_cache[id]._stale: true`              ← per-item stats
    //   2. `nodes_info[host]._stale_fields: [..]`       ← per-host telemetry
    //   3. `_stale_ts: <epoch_seconds>`                 ← persistence write
    //
    // The SPA dims any element bound to a stale value AND surfaces an
    // "X minutes ago" tooltip via `staleAge()`. This makes the
    // "provider went down" case visually explicit instead of letting
    // last-known-good values silently masquerade as live.
    isStale(obj) {
      if (!obj) return false;
      if (obj._stale === true) return true;
      const sf = obj._stale_fields;
      return Array.isArray(sf) && sf.length > 0;
    },
    isStaleField(obj, field) {
      if (!obj || !field) return false;
      const sf = obj._stale_fields;
      return Array.isArray(sf) && sf.indexOf(field) !== -1;
    },
    // True when the bulk of this host's snapshot-eligible fields are
    // stale (i.e. an entire provider went down vs a transient one-key
    // blip). The drawer's banner widens to a more explicit message
    // and the per-field warning triangles are SUPPRESSED so the
    // operator gets one clear signal instead of every <dl> row
    // carrying a triangle.
    //
    // Threshold: ≥ 6 stale fields (matches the typical count covered
    // by a single-provider outage — host_cpu_percent, host_mem_total,
    // host_mem_used, host_disk_total, host_disk_used, host_uptime_s
    // is six). Fewer than that = partial / transient — keep the
    // per-field triangles for actionable detail.
    isAllStale(obj) {
      if (!obj) return false;
      const sf = obj._stale_fields;
      if (!Array.isArray(sf)) return false;
      return sf.length >= 6;
    },
    staleAge(obj) {
      // UX-BUG-002 / return a clean fallback when `_stale_ts`
      // is 0 / missing / non-numeric. Pre-fix `fmtAgo` would either
      // render "Updated NaN ago" or empty string, depending on which
      // branch hit first; either way it's noise on a tooltip.
      if (!obj) return '';
      const tsRaw = obj._stale_ts;
      const ts = Number(tsRaw);
      if (!Number.isFinite(ts) || ts <= 0) {
        try { return (window.t && window.t('stale_marker.never')) || ''; }
        catch (_) { return ''; }
      }
      const ms = ts * 1000;
      const ago = this.fmtAgo(ms);
      // i18n: tooltip surface, not visible label. Translators handle
      // the "stale_marker.tooltip" key with the {age} placeholder.
      try { return (window.t && window.t('stale_marker.tooltip', { age: ago })) || ('Last live data ' + ago + ' ago'); }
      catch (_) { return 'Last live data ' + ago + ' ago'; }
    },
    // Theme-aware icon swap. Wraps every icon-URL emit point so
    // brands that ship a `<slug>-dark.svg` variant (KNOWN_DARK_ICONS)
    // get the dark URL when the document is in dark theme. Reads
    // `this.themePref` reactively so cycling theme via the toolbar
    // re-evaluates every Alpine `:src` binding without a page reload.
    // Idempotent — already-`-dark` URLs short-circuit, external / non-
    // /img/icons/ URLs pass through untouched.
    _themeIcon(url) {
      if (!url) return url;
      // Read themePref so Alpine tracks this as a dependency. The
      // resolution mirrors `applyTheme()` exactly (auto → matchMedia,
      // explicit → that value).
      const pref = this.themePref;
      let dark;
      if (pref === 'auto') {
        const sysLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
        dark = !sysLight;
      } else {
        dark = pref !== 'light';
      }
      let final = url;
      if (dark) {
        const m = /^\/img\/icons\/([a-z0-9_-]+)\.svg$/i.exec(url);
        if (m) {
          const slug = m[1].toLowerCase();
          // Already a -dark / -light explicit variant — operator picked
          // this file deliberately, leave it alone.
          if (!slug.endsWith('-dark') && !slug.endsWith('-light') && KNOWN_DARK_ICONS.has(slug)) {
            final = `/img/icons/${slug}-dark.svg`;
          }
        }
      }
      // Cache-bust local icon URLs with `?v=APP_VERSION` so a deploy
      // that ships a corrected SVG (e.g. the cloudflare.svg "alwa"
      // corruption recovery, #590) is guaranteed to be re-fetched —
      // unlike the bare `/img/icons/<slug>.svg` URL which the browser
      // can keep serving from disk cache for hours via heuristic
      // freshness on a Last-Modified header, even when the file on the
      // server has been updated. The version marker (`window.OG_VERSION`)
      // is set inline in `static/index.html` and substituted server-side
      // at HTML serve time, so it bumps with every PATCH deploy. The
      // global is named OG_VERSION (not __APP_VERSION__) because the
      // server-side substitution replaces every occurrence of the
      // placeholder string — including the LHS identifier — which would
      // otherwise produce `window.1.3.66 = "1.3.66"` ("Unexpected
      // number" at the dot) when the version is numeric. External /
      // non-/img/icons/ URLs pass through unchanged.
      if (/^\/img\/icons\//.test(final)) {
        const v = (typeof window !== 'undefined' && window.OG_VERSION) || '';
        if (v && v !== '__APP_VERSION__' && !final.includes('?')) {
          final = `${final}?v=${encodeURIComponent(v)}`;
        }
      }
      return final;
    },
    iconUrlFor(name) {
      // Resolve an app name to an icon URL. Every icon is local (in
      // static/img/icons/) so the dashboard works offline. Override values
      // can either be:
      //   - a bare canonical slug (resolved to /img/icons/<slug>.svg), or
      //   - a full URL or absolute path ending in .svg/.png/.webp (used verbatim).
      //
      // URLs MUST be absolute (leading "/") — the SPA runs under deep-link
      // routes like /nodes, /settings/oidc, /admin/users, and a relative
      // "img/icons/..." would resolve against those paths (→ 404). Any
      // override that looks like "img/..." is auto-prefixed with "/".
      //
      // Theme-aware swap: every return point routes through
      // `_themeIcon(url)` so brands with a `-dark.svg` variant
      // auto-resolve to the dark URL when in dark theme.
      if (!name) return '';
      // Exact / whole-name overrides (checked first).
      const overrides = {
        'seerr': 'jellyseerr',
        'docker-prune': 'docker',
        'standalone': 'docker',
        'omnigrid': 'docker',
        'nebula-sync': 'pi-hole',
        'adguardhome-sync': 'adguard-home',
        'adguard-exporter': 'adguard-home',
        'blackbox-exporter': 'prometheus',
        'fing-agent': '/img/icons/fing.svg',
        'fing': '/img/icons/fing.svg',
        'lubelogger': '/img/icons/lubelogger.svg',
        'myspeed': '/img/icons/myspeed.svg',
        'squid-proxy': '/img/icons/squid.svg',
        'squid': '/img/icons/squid.svg',
        'tracearr': '/img/icons/tracearr.svg',
        'portainer': '/img/icons/portainer.svg',
        'portainer-agent': '/img/icons/portainer.svg',
        // Somfy typos / product-line synonyms — keep these in sync
        // with the `hostIconUrl` alias map so item / stack contexts
        // (not just curated host rows) accept the same misspellings.
        'smofy':     'somfy',
        'somphy':    'somfy',
        'tahoma':    'somfy',
        'connexoon': 'somfy',
        // Cloudflared (the tunnel daemon) has its OWN file
        // (cloudflared.svg) — same orange Cloudflare cloud bytes as
        // cloudflare.svg, but a distinct URL so the operator's edge
        // cache (Cloudflare's own CDN, fitting given they ARE running
        // cloudflared) doesn't keep serving a stale broken response on
        // the cloudflare.svg URL. The `cloudflared` slug resolves
        // naturally via KNOWN_ICONS, no alias needed; keeping the
        // other Cloudflare-family aliases pointed at the parent
        // cloudflare.svg brand mark.
        'cloudflared-tunnel':    'cloudflared',
        'cloudflare-tunnel':     'cloudflare',
        'cloudflare-warp':       'cloudflare',
        'cloudflare-zero-trust': 'cloudflare',
        // Operator's custom GitSync Connector container (stack name
        // `gitsync-connector`, service name `gitsync-connector_connector`).
        // Both the stack-namespaced and bare-name forms map to the
        // gitsync brand mark. Deliberately NOT aliasing bare `connector`
        // (too generic — would collide with Kafka Connect, MQTT bridges,
        // etc.); operators wanting a different `*-connector` icon stay
        // unaffected.
        'gitsync-connector':           'gitsync',
        'gitsync-connector_connector': 'gitsync',
        'gitsync_connector':           'gitsync',
        // Linux Mint short forms — bare slug AND hyphenated alias both
        // resolve to the canonical linuxmint.svg. Mirrors the
        // hostIconUrl alias map per CLAUDE.md's "BOTH alias maps" rule
        // so item / stack contexts get the same forgiveness.
        'mint':                        'linuxmint',
        'linux-mint':                  'linuxmint',
      };
      // Prefix patterns — one entry covers all siblings of a product
      // (authentik outposts: ak-outpost-authentik-ldap-outpost, etc.).
      const prefixes = [
        ['ak-outpost-', 'authentik'],
        ['komodo-',     'komodo'],
      ];
      const raw = String(name).toLowerCase().trim();
      const natural = raw.replace(/[\s_]+/g, '-').replace(/[^a-z0-9-]/g, '');
      const mapped = overrides[raw] || overrides[natural];
      // If the override looks like a URL or path, return it (guaranteeing
      // a leading "/" so it stays absolute under deep-link routes).
      if (mapped && /[/.]/.test(mapped)) {
        const url = mapped.startsWith('/') || /^https?:/i.test(mapped) ? mapped : '/' + mapped;
        return this._themeIcon(url);
      }
      if (mapped) return this._themeIcon(`/img/icons/${mapped}.svg`);
      for (const [prefix, slug] of prefixes) {
        if (natural.startsWith(prefix)) return this._themeIcon(`/img/icons/${slug}.svg`);
      }
      if (!natural) return '';
      // Only return a URL when the slug actually exists on disk —
      // otherwise the browser fires a 404 for every stack/host name
      // that doesn't happen to match a brand. Operator complaint:
      // "this is a stack without an image, why system looking for
      // image" → fixed by gating on KNOWN_ICONS.
      if (KNOWN_ICONS.has(natural)) return this._themeIcon(`/img/icons/${natural}.svg`);
      return '';
    },
    stackIconUrl(stack) {
      return stack ? this.iconUrlFor(stack.name) : '';
    },
    itemIconUrl(item) {
      // Use the parent stack's name for items inside a stack; otherwise the
      // item's own name (for standalone containers / services without stack).
      if (!item) return '';
      return this.iconUrlFor(item.stack || item.name);
    },
    // -----------------------------------------------------------------
    // Per-node aggregates for the Nodes view. Everything is computed
    // client-side off the same `stats` / `sparks` state the other views
    // use — no new backend endpoints. Items count toward a node iff
    // any of their `placements` match (services with multiple replicas
    // contribute to every node they run on).
    // -----------------------------------------------------------------
    itemsForNode(host) {
      return this.items.filter(it => {
        if (Array.isArray(it.placements) && it.placements.length) {
          return it.placements.some(p => p && p.node === host);
        }
        return it.node === host;
      });
    },

    nodeInfoFor(host) {
      return (this.nodesInfo && this.nodesInfo[host]) || {};
    },

    nodeStats(host) {
      // Mixed-source stats:
      //   - CPU + container-memory: summed from per-item Docker stats.
      //   - Host disk / host memory / host uptime: node-exporter when
      //     enabled, else falls back to Docker-only or task-based signal.
      //   - Docker disk: /system/df totals via Portainer (always available).
      let cpuRaw = 0, memUsage = 0;
      let hasStats = false;
      for (const it of this.itemsForNode(host)) {
        const s = this.statsFor(it);
        if (s.has_stats) {
          cpuRaw += s.cpu_percent;
          memUsage += s.mem_usage;
          hasStats = true;
        }
      }
      const info = this.nodeInfoFor(host);
      const cores = info.cpu_cores || 0;
      const memBytes = info.mem_bytes || 0;
      const dockerDisk = Number.isFinite(info.docker_disk_bytes) ? info.docker_disk_bytes : 0;
      const hostDiskTotal = Number.isFinite(info.host_disk_total) ? info.host_disk_total : 0;
      const hostDiskUsed = Number.isFinite(info.host_disk_used) ? info.host_disk_used : 0;
      const hostMemTotal = Number.isFinite(info.host_mem_total) ? info.host_mem_total : 0;
      const hostMemUsed = Number.isFinite(info.host_mem_used) ? info.host_mem_used : 0;
      // Host-level CPU% straight from the provider (Beszel/Pulse/NE).
      // Lets the CPU bar render even when Portainer's per-container
      // stats haven't been gathered yet (or when the node hosts no
      // containers at all). Falls through to container-derived cpuRaw
      // when the provider didn't supply one.
      const hostCpuRaw = Number.isFinite(info.host_cpu_percent) ? info.host_cpu_percent : 0;
      // Host-stats status — three values:
      //   - 'ok'       scrape succeeded (any host_* fields populated)
      //   - 'error'    probe attempted but failed (exporter_error set,
      //                OR host-stats is enabled globally but this node
      //                returned nothing)
      //   - 'disabled' host_stats_source is 'none' / unset
      // Drives the green/red pill on the node header. The "exporter"
      // word in the variable name is historical — the same signal
      // covers both node-exporter and Beszel now.
      const source = (this.settings && this.settings.host_stats_source)
        || (this.settings && this.settings.node_exporter_enabled ? 'node_exporter' : 'none');
      // CSV of active providers — accepts single legacy values too.
      const sourceSet = new Set(
        (source || '').split(',').map(s => s.trim()).filter(s => s && s !== 'none'),
      );
      const hostStatsEnabled = sourceSet.size > 0;
      let exporterStatus = 'disabled';
      if (info.exporter_error) exporterStatus = 'error';
      else if (hostStatsEnabled && (hostMemTotal > 0 || Number.isFinite(info.host_boot_ts) || (info.mounts && info.mounts.length))) exporterStatus = 'ok';
      else if (hostStatsEnabled) exporterStatus = 'error';  // enabled but no data came back
      // Per-node provider hits — backend records which providers
      // actually contributed data for THIS node into ``_providers`` per
      // gather. Falls back to the global active set on hosts that
      // haven't been re-gathered since this field was added (e.g.
      // first boot before a fresh gather lands).
      const providersHit = Array.isArray(info._providers) ? info._providers : [];
      return {
        cpuRaw,                        // 0..cores*100 (can exceed 100)
        hostCpuRaw,                    // 0..100 — host-provider CPU%
        hasHostCpu: hostCpuRaw > 0,
        memUsage,                      // bytes — sum of container usages
        memLimit: memBytes,            // NODE RAM capacity
        cores,
        dockerDisk,
        hostDiskTotal, hostDiskUsed,
        hostMemTotal, hostMemUsed,
        hasStats,
        hasSize: dockerDisk > 0,
        hasHostStats: hostDiskTotal > 0 || hostMemTotal > 0,
        exporterStatus,
        exporterError: info.exporter_error || null,
        hostStatsSource: source,             // CSV string, legacy callers
        hostStatsSources: [...sourceSet],     // array form for new callers (GLOBAL)
        nodeProvidersHit: providersHit, // per-node list 
      };
    },
    // Label for the green/red chip on a node row — reflects the
    // providers that actually probed THIS node. Falls back to
    // the global active set on rows missing per-node tracking (e.g.
    // before the first post-upgrade gather).
    nodeProviderChip(host) {
      const st = this.nodeStats(host);
      const arr = (st.nodeProvidersHit && st.nodeProvidersHit.length)
        ? st.nodeProvidersHit
        : (st.hostStatsSources || []);
      if (arr.length === 0) return 'host';
      if (arr.length === 1) return arr[0] === 'node_exporter' ? 'exporter' : arr[0];
      return `${arr.length} sources`;
    },
    // Hover tooltip for the chip — lists the providers that contributed
    // data for this specific node.
    nodeProviderList(host) {
      const st = this.nodeStats(host);
      const src = (st.nodeProvidersHit && st.nodeProvidersHit.length)
        ? st.nodeProvidersHit
        : (st.hostStatsSources || []);
      const arr = src.map(s => s === 'node_exporter' ? 'node-exporter' : s);
      return arr.length ? arr.join(', ') : 'none';
    },

    // Host disk percent — real number when the exporter is available.
    // Returns 0 if not yet scraped so the bar collapses instead of
    // showing a misleading proportional-to-busiest-node number.
    hostDiskPercent(host) {
      const { hostDiskTotal, hostDiskUsed } = this.nodeStats(host);
      if (!hostDiskTotal) return 0;
      return Math.min(100, (hostDiskUsed / hostDiskTotal) * 100);
    },
    hostMemPercent(host) {
      const { hostMemTotal, hostMemUsed } = this.nodeStats(host);
      if (!hostMemTotal) return 0;
      return Math.min(100, (hostMemUsed / hostMemTotal) * 100);
    },

    nodeCpuPercent(host) {
      // Prefer the host-provider's CPU% (Beszel/Pulse/NE) when present —
      // it's already a 0..100 number derived from /proc/stat (or
      // equivalent) and reflects total host load including processes
      // outside Docker. Falls back to the container-aggregate cpuRaw
      // (sum of per-container Docker stats) divided by core count.
      // Clamp at 100 — brief spikes over a single tick can exceed
      // cores*100 due to sub-second bursts, and a bar that pokes past
      // 100% looks broken.
      const { cpuRaw, hostCpuRaw, hasHostCpu, cores } = this.nodeStats(host);
      if (hasHostCpu) return Math.min(100, hostCpuRaw);
      if (!cores) return 0;
      return Math.min(100, cpuRaw / cores);
    },

    nodeMemPercent(host) {
      const { memUsage, memLimit } = this.nodeStats(host);
      if (!memLimit) return 0;
      return Math.min(100, (memUsage / memLimit) * 100);
    },

    nodeDiskPercent(host) {
      // No "of N" denominator available without a host-agent — render
      // a proportional bar against the fleet's busiest Docker daemon
      // so operators see which node is carrying the most Docker disk.
      const { dockerDisk } = this.nodeStats(host);
      if (!dockerDisk) return 0;
      let max = 0;
      const infos = this.nodesInfo || {};
      for (const k in infos) {
        const v = Number(infos[k] && infos[k].docker_disk_bytes) || 0;
        if (v > max) max = v;
      }
      if (!max) return 0;
      return Math.min(100, (dockerDisk / max) * 100);
    },

    nodeUptime(host) {
      // Prefer real host boot time (node-exporter). Fall back to the
      // "oldest still-running task" proxy when the exporter isn't
      // available — still per-node, still meaningful, just measuring
      // workload uptime instead of host uptime.
      const info = this.nodeInfoFor(host);
      const bootTs = info.host_boot_ts;
      const ts = (Number.isFinite(bootTs) && bootTs > 0) ? bootTs : info.oldest_running_ts;
      if (!ts) return null;
      return Math.max(0, Math.floor(Date.now() / 1000) - Math.floor(ts));
    },
    nodeUptimeKind(host) {
      // 'host' when sourced from node-exporter, 'docker' otherwise —
      // drives the caption on the Uptime tile.
      const info = this.nodeInfoFor(host);
      return (Number.isFinite(info.host_boot_ts) && info.host_boot_ts > 0) ? 'host' : 'docker';
    },

    // Time-series aggregation for node-level sparklines. Bins samples by
    // rounded timestamp (matching the sampler's cadence) so items with
    // near-identical-but-not-exact timestamps still stack correctly.
    nodeSparkPoints(host, key) {
      const items = this.itemsForNode(host);
      if (!items.length) return '';
      const BIN = 300; // seconds — matches STATS_SAMPLE_INTERVAL default
      const byBin = new Map();
      for (const it of items) {
        const rows = this.sparks[it.id];
        if (!rows) continue;
        for (const r of rows) {
          const bin = Math.round((r.ts || 0) / BIN) * BIN;
          const agg = byBin.get(bin) || { ts: bin, cpu: 0, mem_used: 0, mem_limit: 0 };
          agg.cpu += r.cpu || 0;
          agg.mem_used += r.mem_used || 0;
          agg.mem_limit += r.mem_limit || 0;
          byBin.set(bin, agg);
        }
      }
      const sorted = Array.from(byBin.values()).sort((a, b) => a.ts - b.ts);
      if (sorted.length < 2) return '';
      const W = 60, H = 10;
      const vals = sorted.map(r => {
        if (key === 'cpu') return r.cpu;
        if (key === 'mem') return r.mem_limit ? (r.mem_used / r.mem_limit) * 100 : 0;
        return 0;
      });
      let lo = Infinity, hi = -Infinity;
      for (const v of vals) { if (v < lo) lo = v; if (v > hi) hi = v; }
      // Flat / idle series — center the line in the box rather than
      // pinning it to the bottom edge. Earlier code did
      // `lo = max(0, lo-0.5); hi = lo+1` which mapped `v=0` to `y=H`
      // (the very bottom). With stroke-width=1 the line then drew
      // from y=H-0.5 to y=H+0.5; the bottom half landed OUTSIDE the
      // viewBox and got clipped — visible result was an invisible
      // sparkline (operator's "CPU graph not showing" report on idle
      // nodes). Re-center on the midpoint so flat data lands at y=H/2.
      if (hi - lo < 0.5) {
        const mid = (lo + hi) / 2;
        lo = mid - 1;
        hi = mid + 1;
      }
      // Vertical padding — keep the polyline at least 1 unit clear of
      // the top and bottom edges so the stroke isn't clipped on
      // boundary values (a CPU sample at 0% or 100% would otherwise
      // render half-cropped). Effective drawable height = H - 2*PAD.
      const PAD = 1;
      const drawH = H - 2 * PAD;
      const step = W / (vals.length - 1);
      return vals.map((v, i) => {
        const x = (i * step).toFixed(1);
        const y = (PAD + (1 - (v - lo) / (hi - lo)) * drawH).toFixed(1);
        return `${x},${y}`;
      }).join(' ');
    },

    nodeSparkClass(host, key) {
      const st = this.nodeStats(host);
      if (!st.hasStats) return 'muted';
      const v = key === 'cpu' ? this.nodeCpuPercent(host) : this.nodeMemPercent(host);
      return this.barLevel(v);
    },

    fmtDuration(seconds) {
      if (!seconds || seconds <= 0) return '—';
      const d = Math.floor(seconds / 86400);
      const h = Math.floor((seconds % 86400) / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      if (d > 0) return d + 'd ' + h + 'h';
      if (h > 0) return h + 'h ' + m + 'm';
      return m + 'm';
    },

    stackStats(stack) {
      // Aggregate CPU / memory / image-size across every item in the stack so
      // collapsed stacks still display meaningful numbers on the group row.
      let cpu = 0, memUsage = 0, sizeRoot = 0;
      let hasStats = false, hasSize = false;
      for (const item of (stack.items || [])) {
        const s = this.statsFor(item);
        if (s.has_stats) {
          cpu += s.cpu_percent;
          memUsage += s.mem_usage;
          hasStats = true;
        }
        if (s.has_size) {
          sizeRoot += s.size_root;
          hasSize = true;
        }
      }
      return { cpu, memUsage, sizeRoot, hasStats, hasSize };
    },
    // Title Case a free-text string. Handles ALL-CAPS replies from
    // SNMP printer agents (HP / Brother often return "BLACK
    // CARTRIDGE") and mixed case alike. Each word's first letter
    // upper, rest lower; whitespace and punctuation preserved.
    // Two exceptions: known short brand acronyms (HP, IBM, ...) and
    // alphanumeric SKU codes (e.g. "3JA27A", "Q3960A") render ALL CAPS
    // — operator request 2026-05-01 for printer supply labels like
    // "Cyan Ink Hp 3ja27a" → "Cyan Ink HP 3JA27A".
    titleCase(s) {
      if (!s || typeof s !== 'string') return s || '';
      const brands = new Set(['hp','hpe','ibm','amd','arm','lg','rgb','rfid','usb','pci','io','smb','ftp','http','https','tls','ssl','nfc','vpn','dns','dhcp','ip','tcp','udp','rj45','poe','sfp','sas','sata','nvme','ssd','hdd','iot','ai','ml','gpu','cpu','ram','rom','vrm','bmc','ipmi','sff']);
      return s.replace(/\w\S*/g, w => {
        const lo = w.toLowerCase();
        if (brands.has(lo)) return w.toUpperCase();
        if (/[a-z]/i.test(w) && /[0-9]/.test(w)) return w.toUpperCase();
        return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
      });
    },
    fmtBytes(n) {
      if (n == null) return '—';
      if (n <= 0) return '0 B';
      const u = ['B','KB','MB','GB','TB'];
      let i = 0;
      while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
      return (n >= 10 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i];
    },
    // Like fmtBytes but the unit is FIXED based on `refMax` (the upper
    // bound of the chart / legend group). Use this for any chart where
    // you want every value to read in the same unit family — without
    // it, fmtBytes picks per-value (e.g. "1012 MB" next to "1.9 GB"
    // looks misaligned because the per-value picks land on different
    // tiers). Operator-flagged for the SNMP Memory chart at 2026-05-01.
    // Return the unit symbol (B / KB / MB / GB / TB) that `fmtBytes` /
    // `fmtBytesAt` would pick for a value of magnitude `n`. Used by
    // chart title chips so the chip always matches what the legend +
    // Y-axis actually render — operator-flagged that a static `B/s` /
    // `B` chip looked wrong next to a `1.2 MB/s` legend value.
    unitForBytes(n) {
      const u = ['B', 'KB', 'MB', 'GB', 'TB'];
      let v = Math.max(0, +n || 0), i = 0;
      while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
      return u[i];
    },
    fmtBytesAt(n, refMax) {
      if (n == null) return '—';
      const u = ['B','KB','MB','GB','TB'];
      let i = 0;
      let m = Math.max(0, +refMax || 0);
      while (m >= 1024 && i < u.length - 1) { m /= 1024; i++; }
      let v = (+n || 0) / Math.pow(1024, i);
      if (v <= 0 && (+n || 0) === 0) return '0 ' + u[i];
      return (v >= 10 ? v.toFixed(0) : v.toFixed(1)) + ' ' + u[i];
    },
    memPercent(item) {
      const s = this.statsFor(item);
      if (!s.mem_limit) return 0;
      return Math.min(100, (s.mem_usage / s.mem_limit) * 100);
    },
    diskPercent(item) {
      const s = this.statsFor(item);
      if (!this._maxSize) return 0;
      return Math.min(100, (s.size_root / this._maxSize) * 100);
    },
    // LOW-VISUAL — stat-bar thresholds are operator-tunable.
    // Pre-fix the 60 / 85 thresholds were hardcoded; CLAUDE.md's
    // no-static-config rule says operator-tunable visual thresholds
    // belong in TUNABLES. Now sourced from `client_config` (per-call
    // read so an Admin → Config save lands on the next render).
    _statBarWarnPct() {
      const v = this.me && this.me.client_config && this.me.client_config.stat_bar_warn_pct;
      const n = parseInt(v, 10);
      return Number.isFinite(n) && n >= 30 && n <= 90 ? n : 60;
    },
    // Resolved global SNMP per-host walk concurrency — drives the
    // Admin → Hosts editor's per-row "Walk concurrency" input
    // placeholder. Shows the actual effective default value prefixed
    // with "Inherited: " so an empty input + placeholder "Inherited: 1"
    // visually distinguishes itself from a real value of 1 (pre-fix
    // operator couldn't tell whether they'd left the field blank or
    // typed 1). Sourced from `me.client_config.snmp_per_host_walk_concurrency`
    // so an Admin → Config save takes effect on the next /api/me round-trip.
    snmpWalkConcurrencyPlaceholder() {
      const v = this.me && this.me.client_config
        && this.me.client_config.snmp_per_host_walk_concurrency;
      const n = parseInt(v, 10);
      const num = Number.isFinite(n) && n >= 1 ? n : 1;
      return this.t('admin_hosts.snmp_walk_concurrency_placeholder', { value: num });
    },
    // Per-host wall-clock-budget input placeholder — same "Inherited: <N>"
    // pattern as walk_concurrency. Sourced from
    // me.client_config.snmp_wall_clock_budget_seconds (surfaced from the
    // tunable on /api/me).
    snmpWallClockBudgetPlaceholder() {
      const v = this.me && this.me.client_config
        && this.me.client_config.snmp_wall_clock_budget_seconds;
      const n = parseInt(v, 10);
      const num = Number.isFinite(n) && n >= 5 ? n : 60;
      return this.t('admin_hosts.snmp_walk_concurrency_placeholder', { value: num });
    },
    // Per-host SNMP vendor MIB selector. Empty list = auto-detect from
    // sysDescr (the common case; covers 95% of agents). Operator can
    // declare explicit vendors to bypass auto-detect — useful for
    // agents with stripped sysDescr or to force a vendor's walks. The
    // backend's _clean_host_snmp validates against the same vendor key
    // set: dell / cisco / apc / ucd / synology / printer.
    snmpVendorChecked(row, vendor) {
      const list = (row && row.snmp && Array.isArray(row.snmp.vendors))
        ? row.snmp.vendors : [];
      return list.includes(vendor);
    },
    // Bulk vendor applicator state — selected vendor for the
    // "Apply vendor MIB to all visible rows" picker. Empty = "no
    // vendor chosen, both Apply / Clear disabled".
    hostsConfigBulkVendor: '',
    // Apply or clear ``vendor`` on EVERY row that passes the current
    // hostsConfig filter. ``add=true`` = check the vendor on each row's
    // snmp.vendors array; ``add=false`` = remove it. Skips rows where
    // `snmp.enabled !== true` (the per-row checkbox group is gated on
    // that flag and wouldn't accept the click). Marks each touched row
    // dirty so the existing Save flow picks them up.
    bulkApplySnmpVendor(vendor, add) {
      if (!vendor || this.isReadonly()) return;
      const validSet = new Set(this.snmpVendorKeys());
      if (!validSet.has(vendor)) return;
      let touched = 0;
      const rows = this.filteredHostsConfig();
      for (const entry of rows) {
        const idx = (entry && typeof entry.idx === 'number') ? entry.idx : -1;
        if (idx < 0) continue;
        const cur = this.hostsConfig[idx];
        if (!cur) continue;
        const snmpIn = cur.snmp || {};
        if (snmpIn.enabled !== true) continue;
        const list = Array.isArray(snmpIn.vendors) ? snmpIn.vendors.slice() : [];
        const set = new Set(list);
        const had = set.has(vendor);
        if (add) {
          if (had) continue;
          set.add(vendor);
        } else {
          if (!had) continue;
          set.delete(vendor);
        }
        const next = Object.assign({}, snmpIn, { vendors: Array.from(set).sort() });
        this.hostsConfig[idx].snmp = next;
        this.markHostRowDirty(idx);
        touched += 1;
      }
      if (touched > 0) {
        this.showToast(this.t(
          add ? 'admin_hosts.snmp_vendors_bulk_applied'
              : 'admin_hosts.snmp_vendors_bulk_cleared',
          { vendor: this.snmpVendorLabel(vendor), count: touched }
        ));
      }
    },
    // Last auto-detected vendor set for THIS curated row, sourced
    // from the matching live host's `host_snmp_active_vendors` field
    // (populated by `_merge_one_host` from the most recent successful
    // probe's diagnostic). Returns an empty list when (a) the host has
    // never been probed successfully, OR (b) the host doesn't appear
    // in the loaded `this.hosts` array (Admin → Hosts editor is open
    // but the Hosts view hasn't loaded yet — the helper just returns
    // empty until the row's host data lands). Helps operators new to
    // SNMP see what auto-detect picked before deciding whether to set
    // an explicit override.
    snmpAutoDetectedVendors(row) {
      if (!row || !row.id) return [];
      const list = Array.isArray(this.hosts) ? this.hosts : [];
      const live = list.find(h => h && h.id === row.id);
      const av = live && live.host_snmp_active_vendors;
      return Array.isArray(av) ? av.slice() : [];
    },
    // Canonical SNMP vendor key set sourced from /api/me's
    // client_config.snmp_vendor_keys (single source of truth — backed by
    // logic/snmp.py:_VALID_VENDOR_KEYS server-side). Adding a vendor in
    // _VENDOR_SIGNATURES surfaces a checkbox here on the next /api/me
    // round-trip without any frontend edit. Defence-in-depth fallback to
    // the historical six keys when /api/me hasn't hydrated yet OR the
    // server is older than this SPA build.
    snmpVendorKeys() {
      const cc = (this.me && this.me.client_config) || {};
      if (Array.isArray(cc.snmp_vendor_keys) && cc.snmp_vendor_keys.length) {
        return cc.snmp_vendor_keys;
      }
      return ['apc', 'cisco', 'dell', 'printer', 'synology', 'ucd'];
    },
    // Operator-friendly label for an SNMP vendor key. The persisted
    // values stay lowercase to match `_VALID_VENDOR_KEYS` server-side,
    // but the rendered checkbox text uses the brand-canonical case
    // (Dell / Cisco / APC / Synology / Printer / UCD/net-snmp).
    // Unknown keys fall through to a Title-Case fallback so a future
    // vendor added on the backend still renders sensibly without a
    // SPA edit.
    snmpVendorLabel(key) {
      const k = String(key || '').trim().toLowerCase();
      const map = {
        'apc':      'APC',
        'cisco':    'Cisco',
        'dell':     'Dell',
        'synology': 'Synology',
        'printer':  'Printer',
        'ucd':      'UCD/net-snmp',
      };
      if (k in map) return map[k];
      return k ? k[0].toUpperCase() + k.slice(1) : '';
    },
    toggleSnmpVendor(idx, vendor, checked) {
      const cur = this.hostsConfig[idx];
      const snmp = Object.assign({}, cur.snmp || {});
      const list = Array.isArray(snmp.vendors) ? snmp.vendors.slice() : [];
      const set = new Set(list);
      if (checked) set.add(vendor);
      else set.delete(vendor);
      snmp.vendors = Array.from(set).sort();
      this.hostsConfig[idx].snmp = snmp;
      this.markHostRowDirty(idx);
    },
    _statBarCritPct() {
      const v = this.me && this.me.client_config && this.me.client_config.stat_bar_crit_pct;
      const n = parseInt(v, 10);
      return Number.isFinite(n) && n >= 50 && n <= 99 ? n : 85;
    },
    barColor(pct) {
      // Kept for backward compat; prefer barLevel() which returns a CSS class.
      if (pct > this._statBarCritPct()) return 'var(--danger)';
      if (pct > this._statBarWarnPct()) return 'var(--warning)';
      return 'var(--success)';
    },
    barLevel(pct) {
      // Maps a percentage to the `.warn` / `.crit` class on `.stat-bar`, which
      // drives the fill colour from the stylesheet. Empty string = default green.
      if (pct > this._statBarCritPct()) return 'crit';
      if (pct > this._statBarWarnPct()) return 'warn';
      return '';
    },
    // Inline trend sparkline overlaid on each Hosts-row stat-bar.
    // Pulls from whichever history source the host has — Beszel / NE
    // (`hostHistory[key].series` with cpu / mp / dp percent fields) OR
    // SNMP (`hostSnmpHistory[host_id].points` with cpu_used_pct + raw
    // mem_used / mem_total + raw net counters; we derive percentages
    // here so the path stays in 0..100% Y space). Returns an empty
    // string when no source has at least 2 points — caller's `x-show`
    // gate hides the SVG cleanly.
    //
    // The output path uses a fixed 100×16 viewBox and `preserveAspectRatio="none"`
    // so it stretches to fill whatever width its parent element has —
    // overlaying the .stat-bar (~70-120px) without per-host width math.
    hostInlineSparkline(h, metric) {
      if (!h) return '';
      const W = 100, H = 16;
      const PAD_T = 1, PAD_B = 1;
      const usableH = H - PAD_T - PAD_B;

      // Try Beszel / NE history first (richest dataset).
      const FIELD_BNE = { cpu: 'cpu', memory: 'mp', disk: 'dp' };
      const beszelKey = this.hostHistoryKey ? this.hostHistoryKey(h) : (h.beszel_id || h.id || '');
      let series = null;
      let pickValue = null;
      if (beszelKey) {
        const e = this.hostHistory && this.hostHistory[beszelKey];
        const f = FIELD_BNE[metric];
        if (e && Array.isArray(e.series) && e.series.length >= 2 && f) {
          series = e.series;
          pickValue = (r) => Number(r[f]);
        }
      }

      // Fallback to SNMP history — the SNMP sampler writes a separate
      // `host_snmp_samples` table consumed by `hostSnmpHistory[host.id]`.
      // Field shape differs from Beszel/NE: cpu_used_pct already a
      // percent; memory comes as raw mem_used / mem_total; disk isn't
      // recorded in the SNMP series so disk sparklines for SNMP-only
      // hosts will be empty (correct — the data isn't there).
      if (!series) {
        const snmpEntry = this.hostSnmpHistory && this.hostSnmpHistory[h.id];
        const points = snmpEntry && Array.isArray(snmpEntry.points) ? snmpEntry.points : null;
        if (points && points.length >= 2) {
          if (metric === 'cpu') {
            series = points;
            pickValue = (p) => Number(p.cpu_used_pct);
          } else if (metric === 'memory') {
            series = points;
            pickValue = (p) => {
              const tot = Number(p.mem_total) || 0;
              const used = Number(p.mem_used) || 0;
              return tot > 0 ? (used / tot) * 100 : NaN;
            };
          } else if (metric === 'disk') {
            // Disk added to host_snmp_samples — sampler writes
            // disk_total / disk_used (bytes); we derive percent the
            // same way the memory branch does. NULL or zero total
            // → NaN so the path-builder treats it as a gap rather
            // than a flat-zero hairline.
            series = points;
            pickValue = (p) => {
              const tot = Number(p.disk_total) || 0;
              const used = Number(p.disk_used) || 0;
              return tot > 0 ? (used / tot) * 100 : NaN;
            };
          }
        }
      }

      if (!series || !pickValue) return '';
      const n = series.length;
      const out = [];
      let lastNull = true;
      let sawNonZero = false;
      for (let i = 0; i < n; i++) {
        const v = pickValue(series[i]);
        if (!Number.isFinite(v)) { lastNull = true; continue; }
        if (v > 0) sawNonZero = true;
        const clamped = Math.max(0, Math.min(100, v));
        const x = (i / (n - 1)) * W;
        const y = PAD_T + usableH - (clamped / 100) * usableH;
        out.push(`${lastNull ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`);
        lastNull = false;
      }
      // Skip rendering a flat-zero line — it draws as an invisible
      // hairline at the bottom edge of the .spark element and looks
      // like "no sparkline at all" to the operator. Common cause:
      // Beszel agents that populate `info.dp` (live disk %) but
      // don't emit `stats.dp` in their history blob, so the live
      // bar reads 53% while the historical series is flat-0%. By
      // returning `''` here we let the gate (`hostHasInlineSpark`)
      // hide the SVG cleanly so the operator sees "no spark"
      // unambiguously instead of a misleading hairline. Same rule
      // for any all-zero series across providers.
      if (!sawNonZero) return '';
      return out.join(' ');
    },
    // True when the host has at least 2 data points for `metric` so the
    // sparkline has something to draw. Drives the SVG's x-show gate so
    // dead / unloaded rows don't render an empty placeholder.
    hostHasInlineSpark(h, metric) {
      return !!this.hostInlineSparkline(h, metric);
    },
    // Threshold-tier class for a host-row sparkline — mirrors
    // `sparkClass(item, key)` used by stacks/services rows so a host
    // sparkline shares the green/amber/red colour with the stat-bar
    // sitting above it. Falls back to `muted` when telemetry is
    // missing so the line still renders (visible but neutral) instead
    // of inheriting the previous row's colour by mistake.
    hostSparkClass(h, metric) {
      if (!h) return 'muted';
      let v;
      if (metric === 'cpu') v = h.cpu_percent;
      else if (metric === 'memory') v = h.mem_percent || this.memPercentOf(h);
      else if (metric === 'disk') v = h.disk_percent || this.diskPercentOf(h);
      else v = 0;
      return this.barLevel(v);
    },
    // Single source of truth for stat-bar a11y attrs. Returns a plain
    // object spread via x-bind so every `.stat-bar` consumer announces
    // as a real progressbar instead of an empty div. The label key
    // resolves through `t()` so screen readers get a localised hint
    // (e.g. "CPU 73%"). Pass null/undefined `pct` for "unknown" — the
    // resulting valuenow is omitted but valuetext still names the
    // metric. Replaces 20+ inline rewrites with one helper.
    statBarBind(pct, labelKey) {
      const numeric = (typeof pct === 'number' && isFinite(pct))
        ? Math.max(0, Math.min(100, Math.round(pct)))
        : null;
      const label = (labelKey && typeof this.t === 'function') ? this.t(labelKey) : '';
      const text = numeric == null
        ? (label || '—')
        : (label ? `${label} ${numeric}%` : `${numeric}%`);
      return {
        role: 'progressbar',
        'aria-valuemin': '0',
        'aria-valuemax': '100',
        'aria-valuenow': numeric == null ? null : String(numeric),
        'aria-valuetext': text,
      };
    },
    cpuLabel(pct) {
      return pct >= 10 ? pct.toFixed(0) + '%' : pct.toFixed(1) + '%';
    },
    imageRepo(item) {
      if (!item || !item.image) return '';
      const img = item.image;
      const tag = item.tag || '';
      if (tag && img.endsWith(':' + tag)) return img.slice(0, -(tag.length + 1));
      return img;
    },
    nodeSummary(item) {
      const ns = (item.placements || []).map(p => p.node).filter(n => n && n !== '?');
      return [...new Set(ns)].join(', ');
    },
    uptimeFor(item) {
      // Services: Swarm reports ISO-8601 `updated` — last spec change, a good
      // proxy for "running since". Containers: Unix seconds `created`.
      if (!item) return null;
      const raw = item.type === 'service' ? item.updated : item.created;
      if (raw == null || raw === '') return null;
      const ms = typeof raw === 'number' ? raw * 1000 : Date.parse(raw);
      return isNaN(ms) ? null : ms;
    },
    fmtAgo(ms) {
      if (ms == null) return '—';
      const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
      if (sec < 60) return sec + 's';
      if (sec < 3600) return Math.floor(sec / 60) + 'm';
      if (sec < 86400) {
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        return m > 0 ? `${h}h ${m}m` : `${h}h`;
      }
      const d = Math.floor(sec / 86400);
      const h = Math.floor((sec % 86400) / 3600);
      return h > 0 ? `${d}d ${h}h` : `${d}d`;
    },
    // Short-form interval label — matches the topbar stats picker
    // values exactly (5s / 15s / 30s / 1m / 5m). Used by the Hosts
    // subtitle's "polled every X" so it reflects the operator's
    // actual setting instead of a hardcoded "15s".
    fmtIntervalShort(seconds) {
      const s = Number(seconds) || 0;
      if (s <= 0) return '';
      if (s < 60) return s + 's';
      if (s % 60 === 0) return (s / 60) + 'm';
      return s + 's';
    },
    itemSubline(item) {
      // Node hostname is rendered by the topology chip strip below,
      // not here — avoids duplicating the information in two places.
      const bits = [];
      if (item.type) bits.push(item.type);
      if (item.stack) bits.push(item.stack);
      if (item.state && item.state !== 'running') bits.push(item.state);
      return bits.join(' · ');
    },
    canUpdate(item) {
      if (!item) return false;
      if (item.type === 'orphan') return false;
      if (item.stack_id) return true;
      if (item.type === 'container') return true;
      return false;
    },
    actionLabel(item) {
      if (item.status !== 'update') return '—';
      if (item.stack_id) return this.t('actions.update_stack');
      if (item.type === 'container') return this.t('actions.recreate');
      return this.t('actions.no_stack');
    },
    isSelectable(item) {
      // Selectable if updatable, restartable (service/container), or removable.
      if (item.status === 'update' && this.canUpdate(item)) return true;
      if (item.removable) return true;
      if (item.type === 'service' || item.type === 'container') return true;
      return false;
    },
    isRestartable(item) {
      return item && (item.type === 'service' || item.type === 'container');
    },
    portainerDeepLink(x) {
      const base = (this.settings.portainer_public_url || '').replace(/\/$/,'');
      if (!base) return '#';
      if (x.stack_id) {
        const stackName = x.stack || x.name;
        return `${base}/#!/${this.endpointId}/docker/stacks/${stackName}?id=${x.stack_id}&type=1&external=false`;
      }
      return `${base}/#!/${this.endpointId}/docker/dashboard`;
    },
    sortBy(field) {
      if (this.sortField === field) this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
      else { this.sortField = field; this.sortDir = 'asc'; }
    },
    sortIndicator(field) {
      if (this.sortField !== field) return '';
      return this.sortDir === 'asc' ? '▲' : '▼';
    },
    toggleStack(name) {
      if (this.expanded.includes(name)) this.expanded = this.expanded.filter(n => n !== name);
      else this.expanded = [...this.expanded, name];
    },
    expandAllStacks() {
      this.expanded = this.filteredStacks.map(s => s.name);
    },
    collapseAllStacks() {
      this.expanded = [];
    },
    toggleSelectAll() {
      const selectable = this.filteredItems.filter(i => this.isSelectable(i));
      if (this.selected.length === selectable.length) this.selected = [];
      else this.selected = selectable.map(i => i.id);
    },
    selectAllVisible() {
      this.selected = this.filteredItems.filter(i => this.isSelectable(i)).map(i => i.id);
    },
    selectUpdatesOnly() {
      this.selected = this.filteredItems
        .filter(i => this.isSelectable(i) && i.status === 'update' && this.canUpdate(i))
        .map(i => i.id);
    },
    clearSelection() { this.selected = []; },
    clearFilters() { this.search = ''; this.statusFilter = ''; this.healthFilter = ''; },
    topologyGroups(item) {
      // Returns [{node, chips: [{state, err}, …]}, …] for rendering the
      // node + coloured-dot strip. Placements with a synthetic fallback
      // node ("local" / "?") are dropped — the strip would just show
      // noise for single-node setups where no real hostname was
      // resolved. Empty result => caller hides the strip.
      if (!item || !Array.isArray(item.placements) || !item.placements.length) return [];
      const by = new Map();
      for (const p of item.placements) {
        const node = p.node || '?';
        if (node === 'local' || node === '?') continue;
        if (!by.has(node)) by.set(node, []);
        by.get(node).push(p);
      }
      return Array.from(by.entries()).map(([node, chips]) => ({ node, chips }));
    },
    // i18n-aware topology pill tooltips (#843 follow-up). Pre-fix
    // both the Stacks and Services views inlined `:title="group.node
    // + ' — ' + group.chips.length + ' replica' + (count===1 ? '' :
    // 's')"` — the JS template-literal i18n leak that travels with
    // helper-reuse (the Services view's #814 copy duplicated the
    // pre-existing Stacks tooltip). Plural concatenation never
    // translates cleanly. Singular / plural pick at call-time so
    // languages with non-binary plural rules can extend via locale-
    // override.
    topologyNodeTooltip(group) {
      const count = (group && group.chips && group.chips.length) || 0;
      const node = (group && group.node) || '';
      // Reuses existing topology.node_title / node_title_many keys
      // (added pre-#814 for a different consumer; the i18n bundle
      // already covers the singular/plural split). Pluralization
      // picks at call-time so non-binary plural locales can extend
      // their bundle without touching JS.
      const key = count === 1 ? 'topology.node_title' : 'topology.node_title_many';
      return this.t(key, { node, count });
    },
    topologyChipTooltip(chip) {
      const state = (chip && chip.state) || 'unknown';
      const err = chip && chip.err ? String(chip.err) : '';
      // i18n-translated state label (chip.state is one of the Swarm
      // task-state strings — translate via a known-state key family;
      // unknown values fall through to the raw enum). When an error
      // string is present, append it inline.
      const stateKey = `topology.state_${state.replace(/-/g, '_')}`;
      let stateLabel = this.t(stateKey);
      if (stateLabel === stateKey) stateLabel = state;
      return err
        ? this.t('topology.chip_tooltip_with_error', { state: stateLabel, err })
        : stateLabel;
    },
    // --- Keyboard shortcuts ---------------------------------------------
    // Single source of truth — the help modal renders straight from this.
    // Each entry: { keys: ['r'], label: 'Refresh', run: () => {...} }.
    hotkeyGroups() {
      // Titles + labels resolve through t() so the entire help modal
      // translates in place. Keys + run handlers stay the same across
      // languages — shortcut bindings are locale-independent.
      const _t = (k) => this.t(k);
      return [
        {
          title: _t('hotkeys.groups.navigate'),
          items: [
            { keys: ['/'],       label: _t('hotkeys.items.focus_search'),   run: () => this.$refs.searchBox?.focus() },
            { keys: ['1'],       label: _t('hotkeys.items.view_stacks'),    run: () => this.view = 'stacks' },
            { keys: ['2'],       label: _t('hotkeys.items.view_services'),  run: () => this.view = 'services' },
            { keys: ['3'],       label: _t('hotkeys.items.view_nodes'),     run: () => this.view = 'nodes' },
            { keys: ['4'],       label: _t('hotkeys.items.view_hosts'),     run: () => this.view = 'hosts' },
            { keys: ['5'],       label: _t('hotkeys.items.view_history'),   run: () => this.view = 'history' },
            { keys: ['?'],       label: _t('hotkeys.items.show_help'),       run: () => this.showHotkeys = true },
            { keys: ['n'],       label: _t('hotkeys.items.notifications'),   run: () => this.openNotificationsPopup() },
            { keys: ['Cmd/Ctrl', 'K'], label: _t('hotkeys.items.command_palette'), run: () => this.openCommandPalette() },
            { keys: ['Esc'],     label: _t('hotkeys.items.close_clear'),     run: null, note: _t('hotkeys.items.close_clear_note') },
          ],
        },
        {
          title: _t('hotkeys.groups.refresh_theme'),
          items: [
            { keys: ['r'],        label: _t('hotkeys.items.refresh_cached'), run: () => this.refresh(false) },
            { keys: ['R'],        label: _t('hotkeys.items.refresh_force'),  run: () => this.refresh(true) },
            { keys: ['t'],        label: _t('hotkeys.items.cycle_theme'),    run: () => this.cycleTheme() },
          ],
        },
        {
          title: _t('hotkeys.groups.selection'),
          items: [
            { keys: ['a'], label: _t('hotkeys.items.select_all_visible'), run: () => this.selectAllVisible() },
            { keys: ['u'], label: _t('hotkeys.items.select_updates'),     run: () => this.selectUpdatesOnly() },
            { keys: ['x'], label: _t('hotkeys.items.clear_selection'),    run: () => this.clearSelection() },
          ],
        },
        {
          title: _t('hotkeys.groups.bulk'),
          items: [
            { keys: ['Shift', 'U'], label: _t('hotkeys.items.bulk_update'),  run: () => this.selectionUpdatable().length && this.bulkUpdate() },
            { keys: ['Shift', 'T'], label: _t('hotkeys.items.bulk_restart'), run: () => this.selectionRestartable().length && this.bulkRestart() },
            { keys: ['Shift', 'D'], label: _t('hotkeys.items.bulk_remove'),  run: () => this.selectionRemovable().length && this.bulkRemove() },
          ],
        },
        {
          title: _t('hotkeys.groups.stacks_view'),
          items: [
            { keys: ['e'], label: _t('hotkeys.items.expand_all'),    run: () => this.expandAllStacks() },
            { keys: ['c'], label: _t('hotkeys.items.collapse_all'),  run: () => this.collapseAllStacks() },
          ],
        },
      ];
    },
    handleHotkey(e) {
      const target = e.target || null;
      const active = document.activeElement || null;
      const el = active;
      // Escape works everywhere, including from inside an input — it's the
      // universal "get me out of here" key. Handle it BEFORE any guards.
      if (e.key === 'Escape') {
        if (this.commandPaletteOpen) { this.closeCommandPalette(); e.preventDefault(); return; }
        if (this.userMenuOpen) { this.userMenuOpen = false; e.preventDefault(); return; }
        if (this.showHotkeys) { this.showHotkeys = false; e.preventDefault(); return; }
        if (this.showNotificationsPopup) { this.showNotificationsPopup = false; e.preventDefault(); return; }
        // Terminal modal owns ALL keystrokes when active EXCEPT Esc.
        // Closing on Esc is the universal "get me out" affordance even
        // though it would otherwise be a legitimate keystroke for the
        // shell — operators can always reopen, and the alternative is
        // a Trap with no fallback.
        if (this.terminalModalOpen) { this.closeHostTerminal(); e.preventDefault(); return; }
        if (this.drawerHost) { this.closeHostDrawer(); e.preventDefault(); return; }
        if (this.drawerItem) { this.drawerItem = null; e.preventDefault(); return; }
        if (this.selected.length) { this.clearSelection(); e.preventDefault(); return; }
        if (this.search || this.statusFilter || this.healthFilter) {
          this.clearFilters(); e.preventDefault(); return;
        }
        if (el && typeof el.blur === 'function') el.blur();
        return;
      }

      // Cmd+K / Ctrl+K palette toggle is owned by the capture-phase
      // listener registered in `init()` (look for `_cmdpal_handled`).
      // Originally handled here in bubble phase too, which caused a
      // double-fire when both listeners ran on the same keydown
      // (operator-reported `was=true` on first press). The sentinel
      // is the canonical authority now; this branch was REMOVED to
      // make the keystroke ownership unambiguous regardless of where
      // focus lives. Capture-phase fires first regardless of focused
      // element, so the toggle works from inputs / drawers / any
      // interactive area.
      // Browser / OS combos — never intercept.
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      // Key repeat (holding a key) or IME composition — user is
      // typing, never a hotkey.
      if (e.repeat || e.isComposing) return;

      // Hotkeys fire ONLY when focus is on <body> / <html> (i.e.
      // nothing interactive has focus). This is stricter than the
      // old "not in an INPUT" check and catches: focused buttons
      // with number labels, focused custom role=textbox / combobox,
      // Alpine-managed wrappers that re-target events, and DevTools
      // focus edge cases. The result: typing digits in any form
      // field — including the newly-added asset-inventory number
      // inputs and the Admin → Hosts custom_number editor — never
      // switches views.
      const bodyFocused = !active
        || active === document.body
        || active === document.documentElement;
      // Also skip if the EVENT TARGET is a known interactive element,
      // as a second line of defense against re-targeted events.
      const interactiveTags = ['INPUT','TEXTAREA','SELECT','BUTTON'];
      const targetTag = target && target.tagName;
      const targetIsInteractive = !!target && (
        interactiveTags.includes(targetTag) ||
        target.isContentEditable ||
        target.getAttribute && (
          target.getAttribute('contenteditable') === 'true' ||
          target.getAttribute('role') === 'textbox' ||
          target.getAttribute('role') === 'combobox'
        ) ||
        (target.closest && target.closest(
          'input, textarea, select, [contenteditable="true"], [contenteditable=""], [role="textbox"], [role="combobox"]'
        ))
      );
      if (!bodyFocused || targetIsInteractive) return;

      // Walk the catalog once, match on key (case-sensitive to distinguish
      // lowercase vs Shift+letter).
      for (const group of this.hotkeyGroups()) {
        for (const entry of group.items) {
          if (!entry.run) continue;
          const char = entry.keys[entry.keys.length - 1];
          if (char === 'Esc') continue;
          if (e.key === char) {
            e.preventDefault();
            entry.run();
            return;
          }
        }
      }
    },

    // ---- Command Palette (Cmd-K / Ctrl-K) ---------------------------
    // Universal "go anywhere" search dropping the operator into any
    // drawer / view / setting. Pure-client, fuzzy-matched against the
    // existing data shapes (this.hosts / this.items / this.stacks)
    // plus a static admin-route map and the hotkeys list. Activation
    // dispatches by the result's `kind`: host → openHostDrawer, item
    // → openItemDrawer, admin → setAdminTab + view='admin', view →
    // setView, hotkey → no-op (info only). Results are scored:
    // exact-id match wins, prefix beats substring, and the displayed
    // label gets a small bonus over secondary fields.
    openCommandPalette() {
      // Defensive: each step wrapped so a failure in $nextTick /
      // input.focus() can't prevent the state flip from landing.
      // The capture-phase keydown handler relied on this method;
      // an unhandled throw inside `this.$nextTick` (e.g. when the
      // method is invoked from a non-Alpine context where $nextTick
      // isn't defined) used to swallow the entire palette open.
      try { this.commandPaletteOpen = true; } catch (e) { console.error('[cmdpal] open: state flip failed', e); }
      try { this.commandPaletteQuery = ''; this.commandPaletteSelectedIdx = 0; } catch (_) {}
      // Focus the input on the next tick (after Alpine renders the
      // x-show / :style branch). Without rAF the input isn't in the
      // DOM yet and the focus call no-ops.
      try {
        const tick = (this && typeof this.$nextTick === 'function')
          ? this.$nextTick.bind(this)
          : (fn) => requestAnimationFrame(fn);
        tick(() => {
          try {
            const input = document.getElementById('cmdpal-input');
            if (input) input.focus();
          } catch (_) {}
        });
      } catch (_) {}
    },
    closeCommandPalette() {
      this.commandPaletteOpen = false;
      this.commandPaletteQuery = '';
      this.commandPaletteSelectedIdx = 0;
    },
    // Static admin-route map. Each entry navigates to the matching
    // Admin → <tab> view via setAdminTab. Adding a new admin tab here
    // surfaces it in the palette without touching anywhere else.
    _commandAdminRoutes() {
      // Labels routed through t() so other locales translate cleanly.
      // Each i18n key lives under `command_palette.admin.<tab>` —
      // adding a new admin tab needs ONE new key + one entry here.
      const tabs = [
        'users', 'sessions', 'tokens', 'schedules', 'hosts',
        'host_groups', 'ssh', 'logs', 'tuning', 'backups',
        'notifications', 'asset', 'auth', 'oidc', 'portainer',
      ];
      return tabs.map(tab => ({
        tab,
        label: this.t('command_palette.admin.' + tab),
      }));
    },
    _commandTopViews() {
      const views = ['stacks', 'services', 'nodes', 'hosts', 'history'];
      return views.map(view => ({
        view,
        label: this.t('command_palette.view.' + view),
      }));
    },
    // Score a candidate label against the lowercase query. Returns
    // 0 for no match, 1..100 for varying match quality. Exact = 100,
    // prefix = 80, word-prefix = 60, substring = 40. Empty query
    // matches everything at score 1 (so all groups render in default
    // alphabetical order until the operator types).
    _commandScoreLabel(label, q) {
      if (!q) return 1;
      const lc = String(label || '').toLowerCase();
      if (!lc) return 0;
      // Multi-word query: every token must match the label
      // somewhere; the result is the MIN of per-token scores so a
      // strong match on one token doesn't drown a weak / missing
      // match on another. Operator-reported on the Cisco SG300
      // switch where the asset name is "Cisco SG300-52MP 52-Port
      // Gigabit PoE Managed Switch" — typing "cisco switch"
      // previously failed because the literal substring "cisco
      // switch" doesn't appear (the words are at opposite ends of
      // the label). Tokenizing splits the query, scores each
      // token's best match against the label independently, and
      // returns the worst of those — so any token that's
      // genuinely absent from the label drops the score to 0.
      const qTokens = q.split(/\s+/).filter(Boolean);
      if (qTokens.length > 1) {
        let minScore = Infinity;
        for (const t of qTokens) {
          const s = this._scoreSingleToken(lc, t);
          if (s === 0) return 0;
          if (s < minScore) minScore = s;
        }
        return minScore === Infinity ? 0 : minScore;
      }
      return this._scoreSingleToken(lc, q);
    },
    // Per-token scoring — same rules the original
    // `_commandScoreLabel` applied to the whole query before the
    // multi-word tokenization. Exact match 100, prefix 80, word-
    // prefix 60, substring 40, miss 0.
    _scoreSingleToken(lc, q) {
      if (lc === q) return 100;
      if (lc.startsWith(q)) return 80;
      const tokens = lc.split(/[\s_\-./:]+/);
      for (const t of tokens) {
        if (t === q) return 90;
        if (t.startsWith(q)) return 60;
      }
      if (lc.includes(q)) return 40;
      return 0;
    },
    // Multi-field scorer — picks the BEST score across N candidate
    // strings. Lets a host's id match more strongly than its asset
    // type without each contributing separately.
    _commandScoreFields(q, ...fields) {
      let best = 0;
      for (const f of fields) {
        const s = this._commandScoreLabel(f, q);
        if (s > best) best = s;
      }
      return best;
    },
    commandPaletteResults() {
      const q = (this.commandPaletteQuery || '').trim().toLowerCase();
      const results = [];
      const MAX_PER_GROUP = 8;
      // Hosts — search id / label / asset name / vendor / model.
      const hostsList = (this.hosts || []).slice();
      const scored = hostsList.map(h => {
        const asset = (typeof this.assetForHost === 'function') ? this.assetForHost(h) : null;
        const score = this._commandScoreFields(q,
          h.id, h.label, h.host,
          asset && asset.name, asset && asset.vendor, asset && asset.model,
        );
        return { score, host: h, asset };
      }).filter(x => x.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, MAX_PER_GROUP);
      for (const x of scored) {
        const a = x.asset;
        const sub = a && a.name ? a.name : (x.host.label || '');
        results.push({
          kind: 'host',
          label: this.hostDisplayName(x.host) || x.host.id,
          sub: (sub && sub !== x.host.id) ? sub : '',
          payload: x.host,
          group: 'hosts',
        });
      }
      // Items — stacks, services, containers.
      const itemsList = (this.items || []).slice();
      const scoredItems = itemsList.map(i => ({
        score: this._commandScoreFields(q, i.name, i.target, i.stack),
        item: i,
      })).filter(x => x.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, MAX_PER_GROUP);
      for (const x of scoredItems) {
        results.push({
          kind: 'item',
          label: x.item.name || x.item.target,
          sub: x.item.stack ? ('stack: ' + x.item.stack) : (x.item.type || ''),
          payload: x.item,
          group: 'items',
        });
      }
      // Admin routes.
      const adminScored = this._commandAdminRoutes().map(r => ({
        score: this._commandScoreLabel(r.label, q),
        route: r,
      })).filter(x => x.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, MAX_PER_GROUP);
      for (const x of adminScored) {
        results.push({
          kind: 'admin',
          label: x.route.label,
          sub: '',
          payload: x.route.tab,
          group: 'admin',
        });
      }
      // Top-level views.
      const viewScored = this._commandTopViews().map(v => ({
        score: this._commandScoreLabel(v.label, q),
        view: v,
      })).filter(x => x.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, MAX_PER_GROUP);
      for (const x of viewScored) {
        results.push({
          kind: 'view',
          label: x.view.label,
          sub: '',
          payload: x.view.view,
          group: 'views',
        });
      }
      // Hotkeys (info only — selecting one doesn't fire it; just
      // tells the operator the binding exists).
      const hotkeys = [
        { key: '/',              desc: this.t('command_palette.hotkey.focus_search') },
        { key: 'r',              desc: this.t('command_palette.hotkey.refresh_cached') },
        { key: 'R',              desc: this.t('command_palette.hotkey.refresh_force') },
        { key: 'n',              desc: this.t('command_palette.hotkey.open_notifications') },
        { key: '?',              desc: this.t('command_palette.hotkey.show_hotkeys') },
        { key: 'Esc',            desc: this.t('command_palette.hotkey.escape') },
        { key: 'Cmd-K / Ctrl-K', desc: this.t('command_palette.hotkey.open_palette') },
      ];
      const hkScored = hotkeys.map(hk => ({
        score: this._commandScoreFields(q, hk.key, hk.desc),
        hk,
      })).filter(x => x.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, MAX_PER_GROUP);
      for (const x of hkScored) {
        results.push({
          kind: 'hotkey',
          label: x.hk.desc,
          sub: x.hk.key,
          payload: null,
          group: 'hotkeys',
        });
      }
      // Clamp the selected index so it doesn't point past the end of
      // a fresh result set after the query changed.
      if (this.commandPaletteSelectedIdx >= results.length) {
        this.commandPaletteSelectedIdx = Math.max(0, results.length - 1);
      }
      return results;
    },
    commandPaletteMove(delta) {
      const r = this.commandPaletteResults();
      if (!r.length) return;
      let i = this.commandPaletteSelectedIdx + delta;
      if (i < 0) i = r.length - 1;
      if (i >= r.length) i = 0;
      this.commandPaletteSelectedIdx = i;
      // Scroll the selected row into view inside the result list.
      this.$nextTick(() => {
        const el = document.querySelector(`[data-cmdpal-idx="${i}"]`);
        if (el && typeof el.scrollIntoView === 'function') {
          el.scrollIntoView({ block: 'nearest' });
        }
      });
    },
    commandPaletteActivate() {
      const r = this.commandPaletteResults();
      const sel = r[this.commandPaletteSelectedIdx];
      if (!sel) return;
      this.closeCommandPalette();
      switch (sel.kind) {
        case 'host':
          if (typeof this.openHostDrawer === 'function') {
            this.openHostDrawer(sel.payload);
          }
          break;
        case 'item':
          if (typeof this.openItemDrawer === 'function') {
            this.openItemDrawer(sel.payload);
          } else {
            this.drawerItem = sel.payload;
          }
          break;
        case 'admin':
          this.view = 'admin';
          if (typeof this.setAdminTab === 'function') {
            this.setAdminTab(sel.payload);
          } else {
            this.adminTab = sel.payload;
          }
          break;
        case 'view':
          if (typeof this.setView === 'function') this.setView(sel.payload);
          else this.view = sel.payload;
          break;
        case 'hotkey':
          // Info-only — no activation. Could open the hotkeys cheat
          // sheet but operators usually want to actually invoke
          // something here, not just see the binding again.
          this.showHotkeys = true;
          break;
      }
    },

    selectionUpdatable() {
      return this.items.filter(i => this.selected.includes(i.id) && i.status === 'update' && this.canUpdate(i));
    },
    selectionRemovable() {
      return this.items.filter(i => this.selected.includes(i.id) && i.removable);
    },
    removableAll() {
      // Everything currently removable, regardless of selection. Drives the
      // topbar "Cleanup N" fast-action button.
      return this.items.filter(i => i.removable);
    },
    selectionRestartable() {
      return this.items.filter(i => this.selected.includes(i.id) && this.isRestartable(i));
    },
    selectionSummary() {
      const upd = this.selectionUpdatable().length;
      const rst = this.selectionRestartable().length;
      const rem = this.selectionRemovable().length;
      const parts = [];
      if (upd) parts.push(this.t('bulk.summary_updatable', { count: upd }));
      if (rst) parts.push(this.t('bulk.summary_restartable', { count: rst }));
      if (rem) parts.push(this.t('bulk.summary_removable', { count: rem }));
      return parts.length ? parts.join(' · ') : '';
    },
    openDrawer(item) { this.drawerItem = item; },
    // Body-scroll lock helper. Called from the Alpine root's
    // `x-effect` whenever any drawer-state changes — sets / clears a
    // `.drawer-scroll-lock` class on BOTH html and body so the scroll
    // viewport (which varies by browser — Chrome on html, Safari on
    // body) doesn't accept wheel input while a drawer is open. The
    // CSS rule pairs `overflow: hidden !important` on the class so it
    // wins against the existing `overflow-x: clip` rule. Three reactive
    // arguments (`drawerHost` / `drawerItem` / `drawerNode`) are
    // passed in so Alpine's effect tracker registers reads of all
    // three; without that an IIFE wrapping the same logic might
    // miss the dependency tracking.
    _applyDrawerScrollLock(host, item, node) {
      const lock = !!(host || item || node);
      const html = document.documentElement;
      const body = document.body;
      if (lock) {
        html.classList.add('drawer-scroll-lock');
        body.classList.add('drawer-scroll-lock');
      } else {
        html.classList.remove('drawer-scroll-lock');
        body.classList.remove('drawer-scroll-lock');
      }
    },

    // WAI-ARIA radiogroup keyboard pattern. Bind on the wrapper
    // element via `@keydown="_radiogroupArrowKey($event)"`. Implements
    // the canonical contract: ArrowLeft/Up → previous radio (with
    // wrap), ArrowRight/Down → next radio, Home → first, End → last.
    // RTL flips Left/Right semantics so the LOGICAL direction stays
    // intact (visual-left in RTL is "next"). Skips disabled radios.
    // Each move both focuses the next radio and triggers its click
    // handler so selection follows focus per the radiogroup pattern.
    // Roving tabindex (only the checked radio is tab-reachable) is
    // wired per-radio at the markup level via `:tabindex="..."`.
    _radiogroupArrowKey(ev) {
      const key = ev.key;
      if (key !== 'ArrowLeft' && key !== 'ArrowRight'
          && key !== 'ArrowUp' && key !== 'ArrowDown'
          && key !== 'Home' && key !== 'End') return;
      const group = ev.currentTarget;
      const radios = Array.from(group.querySelectorAll('[role="radio"]')).filter(r => !r.disabled);
      if (!radios.length) return;
      ev.preventDefault();
      let isRtl = false;
      try { isRtl = group.matches(':dir(rtl)'); }
      catch (_e) {
        isRtl = (document.documentElement.dir === 'rtl' || document.body.dir === 'rtl');
      }
      let idx = radios.indexOf(document.activeElement);
      if (idx < 0) idx = radios.findIndex(r => r.getAttribute('aria-checked') === 'true');
      if (idx < 0) idx = 0;
      let next = idx;
      if (key === 'Home') next = 0;
      else if (key === 'End') next = radios.length - 1;
      else {
        let dir = (key === 'ArrowRight' || key === 'ArrowDown') ? 1 : -1;
        if (isRtl && (key === 'ArrowLeft' || key === 'ArrowRight')) dir = -dir;
        next = (idx + dir + radios.length) % radios.length;
      }
      const target = radios[next];
      target.focus();
      target.click();
    },

    // --- Admin → Hosts: curated host list editor ---
    async loadHostsConfig() {
      // Guard against clobbering unsaved edits. The operator can hit
      // Reload deliberately (confirms via SweetAlert) or bypass when
      // clean. Native confirm() was the original implementation but
      // it doesn't translate / RTL-flip and can't theme to the dark
      // surface tokens.
      if (this.hostsConfigDirty) {
        const ok = await this.confirmDialog({
          title: this.t('admin_hosts.unsaved_confirm_title'),
          html:  this.t('admin_hosts.unsaved_confirm_html'),
          icon:  'warning',
          confirmText: this.t('admin_hosts.unsaved_confirm_button'),
          confirmColor: this._cssVar('--danger'),
        });
        if (!ok) return;
      }
      this.hostsConfigLoading = true;
      try {
        const r = await fetch('/api/hosts/config');
        if (!r.ok) {
          this.showToast(this.t('admin_hosts.load_failed_status', { status: r.status }), 'error');
          return;
        }
        const d = await r.json();
        this.hostsConfig = Array.isArray(d.hosts) ? d.hosts : [];
        // Invalidate filtered-list cache — see saveHostsConfig
        // for the full rationale. Cache key doesn't notice an array
        // identity swap, so without this `pagedHostsConfig()` would
        // keep returning pre-load row references on the next render.
        this._filteredHostsConfigCache.key = '';
        this._filteredHostsConfigCache.value = null;
        // Hydrate each row's webmin_url from settings.webmin_aliases —
        // the hosts_config endpoint doesn't carry the URL, the settings
        // table does. Keeps the editor's per-row field in sync with
        // what the probe pipeline actually reads.
        const aliases = (this.settings && this.settings.webmin_aliases) || {};
        for (const row of this.hostsConfig) {
          if (row && row.id && !row.webmin_url) {
            row.webmin_url = aliases[row.id] || '';
          }
          // Ensure every row has an ssh sub-object so Alpine's x-model
          // doesn't reactively create it piecemeal (which would break
          // the dirty tracker).
          if (!row.ssh || typeof row.ssh !== 'object') row.ssh = {};
          // same defensive default for the per-host ping
          // sub-object so Alpine bindings can read row.ping.enabled
          // without an undefined-chain on first render.
          if (!row.ping || typeof row.ping !== 'object') row.ping = {};
          // same defensive default for the per-host SNMP
          // override sub-object. Bare `snmp_name` (string) is separate
          // and lives on the row directly; the `snmp` dict is only used
          // when the operator wants to override the global community /
          // version / port / v3 keys for THIS host.
          if (!row.snmp || typeof row.snmp !== 'object') row.snmp = {};
          if (typeof row.snmp_name !== 'string') row.snmp_name = '';
          // Hydrate the per-host mount-exclusion textarea from the
          // persisted `snmp.exclude_mounts` array. The editor binds
          // a virtual `exclude_mounts_text` field (one path per
          // line, easier to type than maintaining a list); save-
          // side splits + dedupes back into the array shape the
          // backend stores. Keeps the underlying array around so
          // a load-without-edit save round-trips cleanly.
          if (Array.isArray(row.snmp.exclude_mounts) && !row.snmp.exclude_mounts_text) {
            row.snmp.exclude_mounts_text = row.snmp.exclude_mounts.join('\n');
          }
          if (typeof row.snmp.exclude_mounts_text !== 'string') {
            row.snmp.exclude_mounts_text = '';
          }
          // Stamp a stable per-row uid the first time we see this
          // row. Used as the x-for :key so DOM elements never tear
          // down + re-mount mid-typing (which loses input focus and
          // triggers the "still typing in ID is causing refresh"
          // symptom). Persisted into hostsConfig so subsequent
          // reconciliation passes preserve identity even when the
          // sort order changes. Uses `crypto.randomUUID()` (universally
          // supported in modern browsers) — the value is a UI-only
          // identifier, not a security secret, but the
          // crypto-strength source silences the CodeQL
          // `js/insecure-randomness` flag and removes any chance of
          // collision in fleets with thousands of rows.
          if (!row._uid) row._uid = this._mintRowUid();
        }
        this.hostsConfigDirty = false;
        this.rebuildHostsConfigOrder();
        // Clamp paging to the loaded data — preserves the persisted
        // page when valid, and falls back to the new last page
        // when the data has shrunk. Don't unconditionally reset to 1:
        // the operator expects to return to the same page after reload.
        this.hostsConfigPage = Math.min(
          Math.max(1, this.hostsConfigPage),
          this.hostsConfigTotalPages(),
        );
      } catch (e) {
        this.showToast(this.t('admin_hosts.load_failed', { error: e.message }), 'error');
      } finally {
        this.hostsConfigLoading = false;
      }
    },
    async discoverHosts() {
      // Pull every name each enabled provider knows about so the
      // editor's datalist inputs can offer native autocomplete. We
      // don't auto-add rows — the operator still decides which names
      // to curate. Reports per-provider errors inline.
      this.hostsDiscovering = true;
      try {
        const r = await fetch('/api/hosts/discover');
        if (!r.ok) {
          this.showToast(this.t('admin_hosts.discover.failed_status', { status: r.status }), 'error');
          return;
        }
        const d = await r.json();
        this.hostsDiscovery = {
          beszel: Array.isArray(d.beszel) ? d.beszel : [],
          pulse:  Array.isArray(d.pulse)  ? d.pulse  : [],
          webmin: Array.isArray(d.webmin) ? d.webmin : [],
          // SNMP discovery surfaces the configured aliases'
          // values (TARGETS, not curated row ids). Empty by default.
          snmp:   Array.isArray(d.snmp)   ? d.snmp   : [],
        };
        const errs = d.errors || {};
        const errKeys = Object.keys(errs);
        const bTotal = this.hostsDiscovery.beszel.length;
        const pTotal = this.hostsDiscovery.pulse.length;
        const wTotal = this.hostsDiscovery.webmin.length;
        if (errKeys.length && (bTotal + pTotal + wTotal) === 0) {
          this.showToast(
            this.t('admin_hosts.discover.no_response', { detail: errKeys.map(k => k + '=' + errs[k]).join(' · ') }),
            'error',
          );
        } else {
          const parts = [];
          if (bTotal)  parts.push(`${bTotal} Beszel`);
          if (pTotal)  parts.push(`${pTotal} Pulse`);
          if (wTotal)  parts.push(`${wTotal} Webmin`);
          this.showToast(
            parts.length
              ? this.t('admin_hosts.discover.found', { detail: parts.join(', ') })
              : this.t('admin_hosts.discover.no_results'),
            parts.length ? 'success' : 'error',
          );
        }
      } catch (e) {
        this.showToast(this.t('admin_hosts.discover.failed', { error: e.message }), 'error');
      } finally {
        this.hostsDiscovering = false;
      }
    },
    // Admin-editor view filter. We return a list of ``{row, idx}``
    // tuples so the template can render only matching rows while
    // still having the original index for move/remove/test actions.
    // Memoised filter result. ENH-006 — `pagedHostsConfig` and
    // `hostsConfigTotalPages` both call this getter on every Alpine
    // re-evaluation. With 500 hosts + a typing-driven filter that's
    // 1000+ walks per keystroke. Cache the result keyed on
    // `(filter, hostsConfig.length, hostsConfigSortedOrder.length)`
    // so repeated access in one tick is O(1). The cache busts naturally
    // when ANY of those inputs changes (add / remove / sort-rebuild /
    // typed filter character) so it's always fresh.
    _filteredHostsConfigCache: { key: '', value: null },
    filteredHostsConfig() {
      const q = (this.hostsConfigFilter || '').trim().toLowerCase();
      const order = (this.hostsConfigSortedOrder || []);
      const cfg = this.hostsConfig || [];
      const cacheKey = q + '|' + cfg.length + '|' + order.length;
      const cached = this._filteredHostsConfigCache;
      if (cached.key === cacheKey && cached.value) return cached.value;
      // Display order is a SNAPSHOT rebuilt only by
      // `rebuildHostsConfigOrder()` (called on load / add / remove /
      // blur of custom_number). Sorting reactively on every keystroke
      // was breaking input focus: typing "2" into a custom_number
      // field before finishing "24" would move the row mid-typing,
      // tearing down the DOM node and killing focus. Using a stable
      // snapshot means the sort applies on commit (blur), not on
      // keystroke — the operator can type "24" uninterrupted and the
      // row re-sorts when they tab away.
      const all = [];
      if (order.length === cfg.length && order.every(i => i < cfg.length)) {
        for (const idx of order) all.push({ row: cfg[idx], idx });
      } else {
        // Fallback: snapshot is stale (hostsConfig grew/shrank since
        // last rebuild). Show in original order so nothing is lost —
        // next rebuild will re-sort.
        for (let idx = 0; idx < cfg.length; idx++) {
          all.push({ row: cfg[idx], idx });
        }
      }
      let value;
      if (!q) {
        value = all;
      } else {
        value = all.filter(({ row }) => {
          const hay = [
            row.id, row.label, row.ne_url,
            row.beszel_name, row.pulse_name,
            row.webmin_name, row.webmin_url,
            row.url, row.icon, row.ip,
          ].filter(Boolean).join(' ').toLowerCase();
          return hay.includes(q);
        });
      }
      cached.key = cacheKey;
      cached.value = value;
      return value;
    },

    // Page-of-N slice of `filteredHostsConfig()` for rendering.
    // Returns a *windowed* `[{row, idx}, ...]` array — the same shape
    // as `filteredHostsConfig` so the template iterator is unchanged
    // (still has the original `idx` for move/remove/test actions).
    // Page is clamped to the valid range so removing the last row
    // on page N doesn't leave the user staring at an empty list —
    // they auto-fall back to the new last page.
    pagedHostsConfig() {
      const all = this.filteredHostsConfig();
      const per = this.hostsConfigPerPage || 50;
      const total = all.length;
      const totalPages = Math.max(1, Math.ceil(total / per));
      // Clamp lazily — mutating state inside a getter would break
      // Alpine reactivity, so we just compute the safe page. The
      // visible state is normalised separately via `_clampHostsConfigPage`
      // on a $watch tick so `hostsConfigPage` itself eventually
      // catches up to the truth.
      const page = Math.min(Math.max(1, this.hostsConfigPage), totalPages);
      const start = (page - 1) * per;
      return all.slice(start, start + per);
    },
    // normaliser invoked from $watch handlers in `init()`.
    // Reads the same total-pages math `pagedHostsConfig` uses and
    // resets `hostsConfigPage` if it's out of bounds, so the visible
    // state (Page X / Y indicator) matches the rendered slice.
    _clampHostsConfigPage() {
      const per = this.hostsConfigPerPage || 50;
      const total = this.filteredHostsConfig().length;
      const totalPages = Math.max(1, Math.ceil(total / per));
      const safe = Math.min(Math.max(1, this.hostsConfigPage), totalPages);
      if (safe !== this.hostsConfigPage) this.hostsConfigPage = safe;
    },

    // Total page count given the current filter + per-page.
    // Returns at least 1 so the "Page 1 of 1" indicator renders even
    // with an empty list (matches the empty-state cards' tone).
    hostsConfigTotalPages() {
      const total = this.filteredHostsConfig().length;
      const per = this.hostsConfigPerPage || 50;
      return Math.max(1, Math.ceil(total / per));
    },

    // Pagination actions. Goto/clamps internally; a no-op call (e.g.
    // Next on the last page) leaves the page unchanged so the button
    // can be safely visible-but-disabled rather than hidden.
    hostsConfigGoToPage(n) {
      const tp = this.hostsConfigTotalPages();
      this.hostsConfigPage = Math.min(Math.max(1, parseInt(n, 10) || 1), tp);
    },
    hostsConfigPrevPage() { this.hostsConfigGoToPage(this.hostsConfigPage - 1); },
    hostsConfigNextPage() { this.hostsConfigGoToPage(this.hostsConfigPage + 1); },
    // Jump to and expand a specific row by id in the
    // Admin → Hosts editor. Used by deep-link affordances like the
    // host drawer's "+ Add URL" link so the operator lands directly
    // on the row they wanted to edit (page-skipping + chevron-
    // expanding handled in one call). No-op if the id isn't found.
    focusHostsConfigRow(id) {
      if (!id) return;
      const cfg = this.hostsConfig || [];
      const cfgIdx = cfg.findIndex(r => r && r.id === id);
      if (cfgIdx < 0) return;
      const row = cfg[cfgIdx];
      // Expand the row so the URL input is visible.
      if (row && row._uid) {
        this.hostsConfigExpanded = { ...this.hostsConfigExpanded, [row._uid]: true };
      }
      // Find the row's position in the filtered list and page-jump.
      const filtered = this.filteredHostsConfig();
      const filteredIdx = filtered.findIndex(({ idx }) => idx === cfgIdx);
      if (filteredIdx >= 0) {
        const per = this.hostsConfigPerPage || 50;
        this.hostsConfigPage = Math.floor(filteredIdx / per) + 1;
      }
      // Scroll the row's DOM node into view after Alpine renders the
      // page change. Falls back gracefully if the selector misses.
      this.$nextTick(() => {
        const sel = `[data-host-row-id="${row && row.id}"]`;
        const el = document.querySelector(sel);
        if (el && typeof el.scrollIntoView === 'function') {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      });
    },
    hostsConfigSetPerPage(n) {
      const v = parseInt(n, 10);
      if (!Number.isFinite(v) || v < 1) return;
      this.hostsConfigPerPage = v;
      try { localStorage.setItem('hostsConfigPerPage', String(v)); } catch {}
      // Clamp page to the new layout so a 100→25 switch from page 2
      // doesn't leave us on a stale page index.
      this.hostsConfigPage = Math.min(this.hostsConfigPage, this.hostsConfigTotalPages());
    },

    // Recompute the hostsConfig display-order snapshot. Called on
    // load / add / remove and on blur of the custom_number input
    // (see `@change` handler in the editor). Orders by custom_number
    // ascending; rows without a number sink to the bottom; id is
    // tiebreaker for determinism.
    rebuildHostsConfigOrder() {
      const cfg = this.hostsConfig || [];
      const idxs = cfg.map((_, idx) => idx);
      idxs.sort((a, b) => {
        const ca = parseInt(cfg[a].custom_number, 10);
        const cb = parseInt(cfg[b].custom_number, 10);
        const sa = Number.isFinite(ca) ? ca : Number.MAX_SAFE_INTEGER;
        const sb = Number.isFinite(cb) ? cb : Number.MAX_SAFE_INTEGER;
        if (sa !== sb) return sa - sb;
        return String(cfg[a].id || '').localeCompare(String(cfg[b].id || ''));
      });
      this.hostsConfigSortedOrder = idxs;
    },

    // fired on `@focusout` of the host-card wrapper. Defers
    // the cn-driven sort/page-jump until the operator has fully left
    // the row card (not just blurred the cn input mid-edit).
    //
    // Without this, the previous behaviour was: type a new cn, tab to
    // the next field → the row immediately re-sorts into its numeric
    // position → on a paged editor the row may have moved to a
    // DIFFERENT page → operator has to find it again to keep editing
    // label / ne_url / beszel_name. Now: cn `@input` sets
    // `row._cnDirty = true`; the rebuild + page-jump happens here
    // ONLY when focus genuinely leaves the entire card.
    onHostCardFocusOut(idx, event, cardEl) {
      const newFocus = event && event.relatedTarget;
      // Focus moved within the same card → still editing → no-op.
      if (newFocus && cardEl && cardEl.contains(newFocus)) return;
      const row = (this.hostsConfig || [])[idx];
      if (!row || !row._cnDirty) return;
      row._cnDirty = false;
      const uid = row._uid;
      this.rebuildHostsConfigOrder();
      // Keep the row visible after the sort lands. Without this, a
      // cn that pushed the row to another page would silently move it
      // off-screen — exactly the bug we're fixing for the inline-cn
      // case, but for the post-edit case too.
      this.$nextTick(() => {
        const all = this.filteredHostsConfig();
        const pos = all.findIndex(({ row: r }) => r._uid === uid);
        if (pos >= 0) {
          const per = this.hostsConfigPerPage || 50;
          const targetPage = Math.floor(pos / per) + 1;
          if (targetPage !== this.hostsConfigPage) {
            this.hostsConfigGoToPage(targetPage);
          }
        }
      });
    },

    // Count of discovered names not already present in hostsConfig —
    // drives the "Import N discovered" button label / visibility so
    // the operator doesn't import duplicates by accident.
    discoveredMissingCount() {
      const seen = new Set((this.hostsConfig || []).map(r =>
        (r.beszel_name || r.pulse_name || r.webmin_name || r.id || '').toLowerCase()
      ));
      let n = 0;
      for (const name of (this.hostsDiscovery.beszel || [])) {
        if (!seen.has(name.toLowerCase())) n++;
      }
      for (const name of (this.hostsDiscovery.pulse || [])) {
        if (!seen.has(name.toLowerCase())) n++;
      }
      for (const name of (this.hostsDiscovery.webmin || [])) {
        if (!seen.has(name.toLowerCase())) n++;
      }
      return n;
    },
    // Bulk-create host rows from every discovered name that isn't
    // already curated. Each new row uses the discovered name as both
    // the id/label and the matching provider's name field; the
    // operator tweaks from there. A name in BOTH providers creates
    // a single row with both fields filled.
    // Helper used by importDiscoveredHosts + anywhere else that
    // programmatically mutates hostsConfig.
    _markHostsDirty() { this.hostsConfigDirty = true; },
    importDiscoveredHosts() {
      const existing = new Set((this.hostsConfig || []).map(r =>
        (r.id || '').toLowerCase()
      ));
      const added = {};
      const addOrMerge = (name, field) => {
        const key = name.toLowerCase();
        if (existing.has(key)) return;
        if (!added[key]) {
          added[key] = {
            id:          name,
            label:       name,
            ne_url:      '',
            beszel_name: '',
            pulse_name:  '',
            webmin_name: '',
            webmin_url:  '',
            snmp_name:   '',
            enabled:     true,
          };
        }
        added[key][field] = name;
      };
      for (const n of (this.hostsDiscovery.beszel || [])) addOrMerge(n, 'beszel_name');
      for (const n of (this.hostsDiscovery.pulse  || [])) addOrMerge(n, 'pulse_name');
      for (const n of (this.hostsDiscovery.webmin || [])) addOrMerge(n, 'webmin_name');
      for (const n of (this.hostsDiscovery.snmp   || [])) addOrMerge(n, 'snmp_name');
      const rows = Object.values(added);
      if (!rows.length) {
        this.showToast(this.t('admin_hosts.import.nothing_new'), 'success');
        return;
      }
      this.hostsConfig.push(...rows);
      this.hostsConfigDirty = true;
      this.showToast(this.t('admin_hosts.added_n', { count: rows.length }), 'success');
    },
    // Bulk "test every host" — fires testHostRow for each enabled
    // row in parallel. Skips rows without any provider mapping
    // (nothing to probe). Progress state (`hostsTestingAll`) drives
    // the toolbar button's spinner.
    // Export the curated Hosts list as a JSON file download. The
    // shape matches exactly what the importer consumes, so
    // round-tripping keeps every field (id / label / provider
    // mappings / url / icon / enabled) intact. Secrets aren't part
    // of this payload — Beszel/Pulse tokens are in Settings, not
    // per-host.
    exportHostsConfig() {
      const body = {
        version:     1,
        exported_at: new Date().toISOString(),
        hosts:       this.hostsConfig || [],
      };
      const blob = new Blob([JSON.stringify(body, null, 2)],
                            { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const stamp = new Date().toISOString().replace(/[:.]/g, '-');
      a.href = url;
      a.download = `omnigrid-hosts-${stamp}.json`;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => {
        URL.revokeObjectURL(url);
        a.remove();
      }, 500);
      this.showToast(
        this.t('admin_hosts.exported_n', { count: (this.hostsConfig || []).length }),
        'success'
      );
    },

    // Import a hosts JSON file. Two strategies — ``merge`` keeps
    // existing rows and adds/updates by id; ``replace`` wipes the
    // current list. The operator picks via confirm() so neither
    // flow is a surprise. Files saved by exportHostsConfig round-
    // trip cleanly; hand-edited JSON that matches the same schema
    // also works.
    async importHostsConfig(evt) {
      const file = evt && evt.target && evt.target.files && evt.target.files[0];
      if (!file) return;
      evt.target.value = '';  // reset so the same file can re-trigger
      let payload;
      try {
        const text = await file.text();
        payload = JSON.parse(text);
      } catch (e) {
        this.showToast(this.t('admin_hosts.import_invalid_json', { error: e.message }), 'error');
        return;
      }
      const incoming = Array.isArray(payload.hosts) ? payload.hosts
                     : (Array.isArray(payload) ? payload : []);
      if (!incoming.length) {
        this.showToast(this.t('admin_hosts.import.no_hosts_in_file'), 'error');
        return;
      }
      const existing = this.hostsConfig || [];
      let mode = 'merge';
      if (existing.length) {
        // SweetAlert with two buttons — OK = replace, Cancel = merge.
        // Replaces the original native confirm() so the dialog
        // theme-matches the dark surface tokens and i18n's
        //.
        const replace = await this.confirmDialog({
          title: this.t('admin_hosts.import_replace_confirm_title') || this.t('actions.confirm'),
          html:  this.t('admin_hosts.import_replace_confirm_html', { existing: existing.length, incoming: incoming.length }),
          icon:  'warning',
          confirmText: this.t('admin_hosts.import_replace_confirm_replace'),
          confirmColor: this._cssVar('--danger'),
        });
        mode = replace ? 'replace' : 'merge';
      }
      const norm = (h) => ({
        id:          String(h.id || h.name || '').trim(),
        label:       String(h.label || '').trim() || String(h.id || h.name || ''),
        ne_url:      String(h.ne_url || '').trim(),
        beszel_name: String(h.beszel_name || '').trim(),
        pulse_name:  String(h.pulse_name || '').trim(),
        url:         String(h.url || '').trim(),
        icon:        String(h.icon || '').trim(),
        enabled:     h.enabled !== false,
      });
      const cleanIncoming = incoming.map(norm).filter(h => h.id);
      if (mode === 'replace') {
        this.hostsConfig = cleanIncoming;
      } else {
        const byId = {};
        for (const row of existing) byId[row.id] = row;
        for (const row of cleanIncoming) byId[row.id] = row;  // overwrite on id collision
        this.hostsConfig = Object.values(byId);
      }
      // Invalidate filtered-list cache — array identity swap
      // doesn't move the cache key.
      this._filteredHostsConfigCache.key = '';
      this._filteredHostsConfigCache.value = null;
      this.hostsConfigDirty = true;
      this.showToast(
        this.t('admin_hosts.imported_n', { count: cleanIncoming.length }),
        'success'
      );
    },

    async testAllHostRows() {
      // Eligibility uses the canonical `rowHasProviderMapping(row)`
      // helper so the bulk-test set matches the per-row "Test
      // providers" button's enable rule. Pre-fix the filter only
      // accepted Beszel / Pulse / NE rows — SNMP-only switches /
      // Webmin-only Linux boxes / Ping-only routers were silently
      // excluded from "Test all" even though their per-row Test
      // button worked. Now any of the six provider mappings (Beszel
      // / Pulse / NE / Webmin / Ping / SNMP) qualifies.
      const rows = (this.hostsConfig || [])
        .map((row, idx) => ({ row, idx }))
        .filter(({ row }) => row.enabled !== false && this.rowHasProviderMapping(row));
      if (!rows.length) {
        this.showToast(this.t('admin_hosts.test.no_eligible'), 'error');
        return;
      }
      this.hostsTestingAll = true;
      // Auto-expand every row about to be tested so the per-row
      // result strip (✓/✗ icons + reason text) is visible without
      // the operator having to manually expand each row after the
      // bulk test fires. Mirrors the per-host Test workflow where
      // the operator is already on the row's expanded body when
      // they click Test. Pre-fix: results landed in
      // `hostsTestResults[idx]` correctly but rows that were
      // collapsed never showed the strip — operator saw only the
      // aggregate "Tested N rows" toast with no per-row detail.
      const next = { ...(this.hostsConfigExpanded || {}) };
      for (const { row } of rows) {
        if (row && row._uid) next[row._uid] = true;
      }
      this.hostsConfigExpanded = next;
      try {
        await Promise.all(rows.map(({ idx }) => this.testHostRow(idx)));
        this.showToast(this.t('admin_hosts.test.tested_n', { count: rows.length }), 'success');
      } finally {
        this.hostsTestingAll = false;
      }
    },
    // True when at least one provider (Beszel / Pulse / node-exporter
    // / Webmin / Ping / SNMP) is mapped on this row. The "Test providers"
    // button disables when none are set — there's nothing to probe and
    // the backend would return all-skipped anyway. SNMP + Ping added
    // : pre-fix an SNMP-only or Ping-only row showed the button
    // greyed out even with the row's snmp_name set or ping.enabled=true,
    // so the operator had no way to test those providers from the
    // Admin → Hosts editor.
    rowHasProviderMapping(row) {
      if (!row) return false;
      // SNMP gating mirrors ping's explicit opt-in: probe
      // only when `snmp.enabled === true`. Default-OFF (no fallback
      // to "snmp_name set means enabled") so a fresh row with the
      // checkbox unchecked doesn't claim a provider mapping.
      const snmpActive = !!(row.snmp_name || '').trim()
        && !!(row.snmp && row.snmp.enabled === true);
      return !!(
        (row.beszel_name || '').trim() ||
        (row.pulse_name  || '').trim() ||
        (row.ne_url      || '').trim() ||
        (row.webmin_name || '').trim() ||
        (row.webmin_url  || '').trim() ||
        snmpActive ||
        (row.ping && row.ping.enabled)
      );
    },
    async testHostRow(idx) {
      const row = this.hostsConfig[idx];
      if (!row) return;
      this.hostsTestResults = {
        ...this.hostsTestResults,
        [idx]: { pending: true },
      };
      try {
        // Forward the row's per-host SNMP overrides so the test
        // probe runs under the SAME config the live probe / sampler
        // would use. Without this an iDRAC row with an explicit
        // walk_concurrency=4 + vendors=["dell"] override still tested
        // at the safety-floor concurrency=1 + walk-all default,
        // surfacing as "Test failed: HTTP 504" because the full
        // 67-OID walk exceeded NPM's proxy_read_timeout. Backend
        // honours these or falls through to globals when blank.
        const snmp = (row.snmp || {});
        const r = await fetch('/api/hosts/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            beszel_name: (row.beszel_name || '').trim(),
            pulse_name:  (row.pulse_name  || '').trim(),
            ne_url:      (row.ne_url      || '').trim(),
            webmin_url:  (row.webmin_url  || '').trim(),
            // SNMP + Ping forwarded to /api/hosts/test so the
            // per-row test reflects the SAME providers the live probe
            // chain runs. Without this, SNMP-only rows reported "all
            // skipped" + ping-only rows skipped their reachability check.
            snmp_name:   (row.snmp_name   || '').trim(),
            // Per-host SNMP overrides (community / version / port /
            // v3 / walk_concurrency / vendors / wall_clock_budget).
            // Each is forwarded only when set so blanks fall through
            // to the global defaults server-side.
            snmp_community:        (snmp.community        || '').trim(),
            snmp_version:          (snmp.version          || '').trim(),
            snmp_port:             snmp.port              || 0,
            snmp_v3_user:          (snmp.v3_user          || '').trim(),
            snmp_v3_auth_key:      (snmp.v3_auth_key      || '').trim(),
            snmp_v3_priv_key:      (snmp.v3_priv_key      || '').trim(),
            snmp_walk_concurrency: snmp.walk_concurrency  || 0,
            snmp_wall_clock_budget: snmp.wall_clock_budget || 0,
            snmp_vendors:          Array.isArray(snmp.vendors) ? snmp.vendors : [],
            ping_enabled: !!(row.ping && row.ping.enabled),
            host_id:     (row.id          || '').trim(),
          }),
        });
        if (!r.ok) {
          this.hostsTestResults[idx] = { pending: false, error: `HTTP ${r.status}` };
          return;
        }
        const d = await r.json();
        this.hostsTestResults[idx] = { pending: false, ...d };
      } catch (e) {
        this.hostsTestResults[idx] = { pending: false, error: e.message };
      }
    },
    // Collapse / expand helpers for the Admin → Hosts editor. With SSH
    // + provider + icon + URL fields on every row, a 20-host list is
    // a 2000-line scroll. Collapsed rows show only the summary; the
    // field grid renders behind x-show when expanded.
    //
    // Keyed on the row's stable `_uid` (assigned on load / add) — NOT
    // on `row.id`. Earlier the lookup used `row.id` as the key, so
    // typing into the ID field changed the key mid-keystroke and the
    // row collapsed (`hostsConfigExpanded[oldId]` was true but
    // `hostsConfigExpanded[newId]` was undefined). Operators reported
    // "typing in ID closes the host" — this is that bug. _uid never
    // changes for the lifetime of the row, so the expansion state
    // sticks regardless of edits to any field.
    isHostConfigExpanded(row) {
      // Backwards-compatible: callers may pass either a row OR a
      // bare id (legacy paths). When given a string, fall back to
      // looking it up via id then resolve to _uid.
      if (typeof row === 'string') {
        if (!row) return true;
        const found = (this.hostsConfig || []).find(r => r && r.id === row);
        return found ? !!this.hostsConfigExpanded[found._uid] : false;
      }
      if (!row) return false;
      // Empty-id rows (fresh adds) always expand so the operator
      // sees the form fields without hunting for a chevron.
      if (!row.id) return true;
      return !!this.hostsConfigExpanded[row._uid];
    },
    toggleHostConfigRow(row) {
      if (typeof row === 'string') {
        const found = (this.hostsConfig || []).find(r => r && r.id === row);
        row = found;
      }
      if (!row || !row._uid) return;
      const next = { ...this.hostsConfigExpanded };
      if (next[row._uid]) delete next[row._uid]; else next[row._uid] = true;
      this.hostsConfigExpanded = next;
    },
    expandAllHostConfigRows() {
      const next = {};
      for (const h of (this.hostsConfig || [])) {
        if (h && h._uid) next[h._uid] = true;
      }
      this.hostsConfigExpanded = next;
    },
    collapseAllHostConfigRows() {
      // Wipe the whole map — every row that had an id drops back to
      // its summary line. Empty-id rows (fresh adds) stay visible
      // because isHostConfigExpanded(id) returns true for them by
      // design.
      this.hostsConfigExpanded = {};
    },
    collapseAllHostConfigRows() {
      this.hostsConfigExpanded = {};
    },
    // DISKS card — toggle the "show zero-usage mounts" state. Keyed
    // by host.host so each host's toggle is independent.
    toggleHostShowEmptyDisks(hostKey) {
      this.hostDisksShowEmpty = {
        ...this.hostDisksShowEmpty,
        [hostKey]: !this.hostDisksShowEmpty[hostKey],
      };
    },
    // Helper returning the mounts that SHOULD render for this host.
    // Thresholds: a mount is "active" when its usage % is >= 0.5. The
    // UI shows active mounts by default; zero-usage mounts collapse
    // behind a toggle so hosts with 20 ZFS datasetsdon't
    // blow out the drawer. When the operator has flipped the toggle
    // on, every mount is returned.
    visibleMounts(h) {
      const all = (h && h.mounts) || [];
      if (!all.length) return [];
      if (this.hostDisksShowEmpty[h.host]) return all;
      const active = all.filter(m => (+m.dp || 0) >= 0.5);
      // Always keep AT LEAST the root ("/" or "C:\\") so operators
      // see something even when every partition is near-empty.
      if (active.length === 0 && all[0]) return [all[0]];
      return active;
    },
    emptyMountCount(h) {
      const all = (h && h.mounts) || [];
      const active = all.filter(m => (+m.dp || 0) >= 0.5);
      return Math.max(0, all.length - active.length);
    },
    // True when the named provider is enabled in
    // settings.host_stats_source (singular CSV string — "beszel,pulse,
    // node_exporter,webmin"). The host editor uses this to hide
    // per-row Beszel / Pulse / Webmin / NE fields whose global
    // provider is disabled — operators don't waste time configuring
    // mappings that won't be probed. Falls back to TRUE (show) when
    // settings haven't loaded yet so we don't strip fields prematurely.
    hostStatsSourceEnabled(name) {
      const raw = (this.settings && this.settings.host_stats_source) || '';
      if (!raw) return true;  // settings not loaded yet → show everything
      if (raw === 'none') return false;
      const parts = String(raw).split(',').map(s => s.trim()).filter(Boolean);
      return parts.includes(name);
    },
    // Asset-inventory autofill — called from the host-row editor when
    // the operator clicks "Load from asset inventory". Looks up the
    // row's `custom_number` against the loaded asset cache (via the
    // shared `assetForHost` helper so backend-injected + client-cache
    // paths both work) and populates EMPTY fields on the row:
    //   id (Docker hostname) ← first entry in asset.hostnames (or asset.name)
    //   label              ← asset.name / vendor+model fallback
    //   url                ← first port.service_name starting with http(s)
    // Never overwrites a value the operator already typed — blank
    // fields only. Toast reports what was filled (or why nothing was).
    // Returns an array of field names that were filled, for the UI.
    autofillHostRowFromAsset(idx) {
      const row = (this.hostsConfig || [])[idx];
      if (!row) return [];
      const asset = this.assetForHost({ custom_number: row.custom_number });
      if (!asset) {
        this.showToast(this.t('admin_hosts.autofill.no_match', { n: row.custom_number }), 'warning');
        return [];
      }
      const filled = [];
      // Strip the FQDN's domain suffix when populating the host id.
      // The global `ssh_fqdn_suffix` setting (e.g. ".example.com") is
      // appended at SSH-resolve time, so storing the SHORT hostname
      // here keeps the global suffix authoritative — different deploys
      // can swap suffixes without re-typing every host. IPs and
      // hostnames-without-dots are returned unchanged.
      const _stripDomain = (raw) => {
        const v = String(raw || '').trim();
        if (!v) return '';
        // Bare hostname (no dot) — nothing to strip.
        if (v.indexOf('.') === -1) return v;
        // IPv4 — leave intact.
        if (/^\d+\.\d+\.\d+\.\d+$/.test(v)) return v;
        // IPv6 — leave intact (contains `:`, no other shape collides).
        if (v.indexOf(':') !== -1) return v;
        // FQDN — keep the leading label only.
        return v.split('.')[0];
      };
      // id / hostname — prefer the first FQDN in the asset's Hostname
      // CSV; fall back to asset.name (device label, often lowercase).
      // Strip the domain suffix so the global `ssh_fqdn_suffix`
      // setting remains the single source of truth for what gets
      // appended at resolution time.
      if (!(row.id || '').trim()) {
        const primary = (Array.isArray(asset.hostnames) && asset.hostnames[0])
          || asset.name || '';
        if (primary) {
          row.id = _stripDomain(primary);
          filled.push('id');
        }
      }
      // NOTE: `row.label` deliberately NOT auto-populated from the
      // asset record. Operator-flagged: pre-fix the label was filled
      // from `asset.name` / `vendor model` / `vendor` on import, but
      // operators want to fill it themselves so it captures their
      // own naming convention rather than whatever shape the upstream
      // asset DB happens to carry. Empty label falls through cleanly
      // — `hostDisplayName(h)` already prefers `id` when label is
      // blank, and `iconUrlFor()` walks id + label + provider names
      // for icon resolution. If you ever want bulk-prefill behaviour
      // back, add a separate "Auto-fill labels from asset" button
      // rather than re-enabling on import.
      // URL — first port whose service_name looks like an http(s) link.
      // Ports without a URL-shaped service_name are skipped.
      if (!(row.url || '').trim() && Array.isArray(asset.ports)) {
        for (const p of asset.ports) {
          const sn = (p && p.service_name) || '';
          if (/^https?:\/\//i.test(sn)) {
            row.url = sn;
            filled.push('url');
            break;
          }
        }
      }
      // IP — primary IP from the asset's interfaces. Helpful for
      // hosts where node-exporter scrape templates reference {ip}.
      if (!(row.ip || '').trim() && asset.primary_ip) {
        row.ip = asset.primary_ip;
        filled.push('ip');
      }
      if (!filled.length) {
        this.showToast(this.t('admin_hosts.autofill.nothing_to_fill'), 'info');
        return [];
      }
      this.markHostRowDirty(idx);
      // Keep the row visibly expanded after the fill — same sticky
      // rule used for typed input (see onHostRowEdit).
      if (row._uid) {
        this.hostsConfigExpanded = {
          ...this.hostsConfigExpanded,
          [row._uid]: true,
        };
      }
      this.showToast(this.t('admin_hosts.autofill.filled', {
        fields: filled.join(', '),
      }), 'success');
      return filled;
    },
    // Quick predicate for the editor UI: "is there an asset-inventory
    // match for this row's custom_number?" — drives whether the
    // autofill button is shown vs hidden. Cheap lookup via the shared
    // `assetForHost` helper.
    hostRowHasAssetMatch(row) {
      if (!row || row.custom_number == null || row.custom_number === '') return false;
      return !!this.assetForHost({ custom_number: row.custom_number });
    },

    addHostRow() {
      // Pre-fill custom_number with the next unused integer so a fresh
      // row slots into the catalogue sequence without manual thought.
      // Operator can still overwrite / clear it.
      const existing = (this.hostsConfig || [])
        .map(r => parseInt(r.custom_number, 10))
        .filter(n => Number.isFinite(n) && n > 0);
      const nextNum = existing.length ? Math.max(...existing) + 1 : 1;
      this.hostsConfig.push({
        id: '',
        label: '',
        custom_number: nextNum,
        ne_url: '',
        beszel_name: '',
        pulse_name: '',
        webmin_name: '',
        webmin_url: '',
        // SNMP target alias. Blank = no SNMP for this host.
        snmp_name: '',
        snmp: {},
        url: '',
        icon: '',
        // Free-text IP field — operator-maintained, not derived.
        ip: '',
        // Per-host SSH overrides — empty object = use global defaults.
        ssh: {},
        // Per-host ping opt-in. Default OFF; operator flips to
        // probe this host. Empty object = use global defaults
        // (ping_default_port + ping_use_icmp).
        ping: {},
        enabled: true,
        // Stable identity for x-for keying (matches the loadHostsConfig
        // hydration path).
        _uid: this._mintRowUid(),
      });
      this.hostsConfigDirty = true;
      this.rebuildHostsConfigOrder();
      // Jump to whichever page the new row landed on. After the
      // sort, the new row's display position depends on its
      // custom_number; without this jump, an "+ Add" on page 1
      // could push focus to a row only visible on page 4.
      this.$nextTick(() => {
        const newUid = this.hostsConfig[this.hostsConfig.length - 1]._uid;
        const all = this.filteredHostsConfig();
        const pos = all.findIndex(({ row }) => row._uid === newUid);
        if (pos >= 0) {
          const per = this.hostsConfigPerPage || 50;
          this.hostsConfigGoToPage(Math.floor(pos / per) + 1);
        }
      });
      // Scroll to the new (last) host card so the operator sees it
      // immediately — useful when the editor list is long enough to
      // require scrolling. A short delay lets Alpine finish rendering
      // the new row before we try to find + scroll to it.
      this.$nextTick(() => {
        setTimeout(() => {
          const cards = document.querySelectorAll('[data-host-card]');
          const last = cards[cards.length - 1];
          if (last && typeof last.scrollIntoView === 'function') {
            last.scrollIntoView({ behavior: 'smooth', block: 'center' });
            // Focus the ID input for immediate typing.
            const idInput = last.querySelector('input[placeholder*="host01"], input[placeholder*="example"]');
            if (idInput && typeof idInput.focus === 'function') idInput.focus();
          }
        }, 50);
      });
    },
    // Dirty-tracking: any input change flips the unsaved-changes
    // flag (so the Save button can flash the warning + beforeunload
    // guards kick in) AND clears any stale per-row Test result so
    // green ticks don't linger next to fields the operator is
    // currently fixing.
    // Mint a stable, collision-free per-row UID for the Admin → Hosts
    // editor. Used as the `<template x-for :key>` so Alpine never tears
    // down + re-mounts a row mid-edit (which would lose input focus +
    // collapse expanded sections). Prefer `crypto.randomUUID()` —
    // universally supported in modern browsers + cryptographically
    // strong, which silences the `js/insecure-randomness` CodeQL flag
    // even though the value is UI-only and not a security secret.
    // Fallback path covers the (vanishingly rare) case of a browser
    // without `crypto.randomUUID` — uses `crypto.getRandomValues` (a
    // cryptographically strong PRNG, the primitive that backs randomUUID
    // itself), available in every browser that ships modern JS. No
    // `Math.random` anywhere so CodeQL js/insecure-randomness has no
    // surface left to flag.
    _mintRowUid() {
      try {
        if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
          return 'r' + crypto.randomUUID();
        }
        if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
          const buf = new Uint8Array(8);
          crypto.getRandomValues(buf);
          let hex = '';
          for (let i = 0; i < buf.length; i++) hex += buf[i].toString(16).padStart(2, '0');
          return 'r' + hex;
        }
      } catch (_) { /* unreachable on any spec-compliant browser */ }
      // Last-resort: a monotonic counter scoped to the page session.
      // Not random, but unique within one tab — good enough for an Alpine
      // x-for :key that only needs intra-render uniqueness.
      this._uidCounter = (this._uidCounter | 0) + 1;
      return 'r' + Date.now().toString(36) + '_' + this._uidCounter.toString(36);
    },
    markHostRowDirty(idx) {
      this.hostsConfigDirty = true;
      if (this.hostsTestResults && this.hostsTestResults[idx]) {
        delete this.hostsTestResults[idx];
      }
    },
    // Convenience auto-fill: when the operator first types the ID
    // and the Label is still blank, mirror the ID into Label so they
    // don't have to type it twice. Respects any Label they later
    // type — once it's populated, it stays.
    onHostRowEdit(idx, field, value) {
      this.markHostRowDirty(idx);
      const row = this.hostsConfig[idx];
      if (!row) return;
      if (field === 'id') {
        // Operator-flagged: do NOT auto-fill `row.label` from the
        // typed id. Pre-fix the editor mirrored the id into the label
        // on the first keystroke so a blank label always defaulted to
        // the id value; operator wants to leave the label alone so it
        // reflects their own naming convention rather than the host
        // identifier. Empty label falls through cleanly — every
        // consumer (`hostDisplayName(h)` / `iconUrlFor()` / the
        // keyword-scan icon resolver) prefers `id` when label is
        // blank.
        // Promote the implicit "empty-id rows always render expanded"
        // rule to an EXPLICIT entry in hostsConfigExpanded the moment
        // any character lands in the ID field. Without this, typing
        // the very first character flips `!row.id` from true to false
        // and `isHostConfigExpanded(row)` falls through to the map —
        // which has no key for a fresh row — returning false and
        // collapsing the panel mid-keystroke. Sticky-expand on first
        // edit survives typing AND copy-paste.
        if (row._uid && !this.hostsConfigExpanded[row._uid]) {
          this.hostsConfigExpanded = {
            ...this.hostsConfigExpanded,
            [row._uid]: true,
          };
        }
      }
    },
    // Resolve a curated host to an icon URL. Priority:
    //   1. explicit ``h.icon`` override (admin-supplied).
    //   2. ``iconUrlFor()`` on the raw id / label / provider names
    //      — finds a hit when one of those matches an icon slug
    //      verbatim (e.g. id "opnsense" → opnsense.svg).
    //   3. KEYWORD scan of the label + id for known brand tokens
    //      (e.g. "(Apache)" → apache.svg, "(NGINX)" → nginx.svg).
    //      Lets labels like "[VM] Debian OS 13 (WebServer 01) (Apache)"
    //      auto-match without the operator setting an icon manually.
    hostIconUrl(h) {
      if (!h) return '';
      // Sentinel "no icon" values — operator sets one of these when the
      // auto keyword-scan picks the WRONG brand icon and they want to
      // render no icon at all rather than the wrong one (e.g. a host
      // whose label happens to contain "syno" matched the Synology
      // icon by accident). Returning empty hides the `<img>` via every
      // consumer's `x-show="hostIconUrl(h)"` gate. Anything else falls
      // through to the existing override → exact-slug → keyword-scan
      // resolver chain. Case-insensitive, whitespace-tolerant.
      const _icon = (h.icon || '').trim().toLowerCase();
      if (['none', '-', 'off', 'false', 'no', 'disabled', 'hidden'].includes(_icon)) {
        return '';
      }
      if (h.icon) {
        // Normalise: bare slug → absolute /img/icons/<slug>.svg.
        // Slug aliases cover the common "wrong name" cases where the
        // icon file is stored under a different slug than the brand's
        // common name (e.g. "adguard" → adguard-home.svg).
        if (/^https?:/i.test(h.icon) || h.icon.startsWith('/')) return h.icon;
        const aliases = {
          'adguard':         'adguard-home',
          'ad-guard':        'adguard-home',
          'npm':             'nginx-proxy-manager',
          'nginxproxymanager': 'nginx-proxy-manager',
          'homeassistant':   'home-assistant',
          'pihole':          'pi-hole',
          'k8s':             'kubernetes',
          'pve':             'proxmox',
          'pi-vpn':          'pivpn',
          'ts':              'tailscale',
          'ovpn':            'openvpn',
          'wg':              'wireguard',
          'wireguard-vpn':   'wireguard',
          'qbit':            'qbittorrent',
          'qb':              'qbittorrent',
          'freenas-mini':    'freenas',
          'nas':             'truenas',
          'win':             'windows',
          'win11':           'windows',
          'win10':           'windows-10',
          'win-server':      'windows-server',
          'windowsserver':   'windows-server',
          'mailserver':      'mail',
          'smtprelay':       'smtp',
          'smtp-relay':      'smtp',
          'postfix':         'mail',
          'mailu':           'mail',
          'maddy':           'mail',
          'asus-router':     'asus',
          'asus-vpn':        'asus',
          'asuswrt':         'asus',
          'rt-ax':           'asus',
          'rt-ac':           'asus',
          'western-digital': 'wd',
          'seeed':           'seeedstudio',
          'seeed-studio':    'seeedstudio',
          'western digital': 'wd',
          'wdc':             'wd',
          'mycloud':         'wd',
          'my-cloud':        'wd',
          'mybooklive':      'wd',
          'my-book-live':    'wd',
          'syno':            'synology',
          'dsm':             'synology',
          'mint':            'linuxmint',
          'linux-mint':      'linuxmint',
          'synology-dsm':    'synology',
          'meraki':          'cisco',
          'cisco-asa':       'cisco',
          'asa':             'cisco',
          'ios-xe':          'cisco',
          'iosxe':           'cisco',
          'catalyst':        'cisco',
          'nexus':           'cisco',
          // Ubiquiti family — `ubiquiti.svg` is the parent brand mark,
          // `ui.svg` is the short-form UI badge, `unifi.svg` (unchanged)
          // stays for the UniFi product line specifically. An operator
          // tagging a host `ubnt` / `edgerouter` / `airmax` / `airfiber`
          // / `unifi-os` lands on the parent Ubiquiti mark.
          'ubnt':            'ubiquiti',
          'ui.com':          'ui',
          'unifi-os':        'ubiquiti',
          'edgerouter':      'ubiquiti',
          'edge-router':     'ubiquiti',
          'edgeswitch':      'ubiquiti',
          'edge-switch':     'ubiquiti',
          'airmax':          'ubiquiti',
          'airfiber':        'ubiquiti',
          'uisp':            'ubiquiti',
          'amplifi':         'ubiquiti',
          // Reolink — IP cameras / NVRs. Aliases cover the product
          // lines operators commonly tag hosts with.
          'reolink-nvr':     'reolink',
          'reolink-cam':     'reolink',
          'reolink-camera':  'reolink',
          'rlc':             'reolink',
          'rln':             'reolink',
          // Xiaomi family — phones, routers (Mi Router), smart-home
          // hubs, vacuums. Aliases cover the product names operators
          // commonly tag hosts with.
          'mi':              'xiaomi',
          'mi-router':       'xiaomi',
          'mi-home':         'xiaomi',
          'redmi':           'xiaomi',
          'poco':            'xiaomi',
          'mihome':          'xiaomi',
          // Hisense — TVs, smart-home hubs, white goods.
          'hisense-tv':      'hisense',
          'vidaa':           'hisense',
          // Sensibo — smart-AC controllers (sky / air / pure / pod).
          'sensibo-sky':     'sensibo',
          'sensibo-air':     'sensibo',
          'sensibo-pod':     'sensibo',
          'sensibo-pure':    'sensibo',
          // HP family — short / common synonyms route to the canonical
          // hp.svg brand mark. ProLiant / iLO keep their existing
          // dedicated icons (proliant.svg / ilo.svg) since those are
          // distinct product-line marks rather than the parent HP logo.
          'hpe':              'hp',
          'hewlett-packard':  'hp',
          'hewlettpackard':   'hp',
          // Samsung — `samsung` is the consumer wordmark; the corporate
          // "Samsung Electronics" mark lives at `samsung-electronics`.
          'samsung_electronics': 'samsung-electronics',
          'samsungelectronics':  'samsung-electronics',
          // Common typo + product-line synonyms for Somfy (motorised
          // blinds / smart-home hubs). Operators have typed "smofy" /
          // "somphy" repeatedly — alias them all to the canonical
          // somfy.svg so the icon picker is forgiving.
          'smofy':            'somfy',
          'somphy':           'somfy',
          'tahoma':           'somfy',
          'connexoon':        'somfy',
          // Amazon Fire TV product line.
          'fire-tv':          'firetv',
          'fire_tv':          'firetv',
          'firestick':        'firetv',
          // Amazon Echo / Alexa — Echo product variants all use
          // alexa.svg (the canonical Alexa-blue swirl mark).
          'echo':             'alexa',
          'echo-dot':         'alexa',
          'echo-show':        'alexa',
          'echo-studio':      'alexa',
          'amazon-echo':      'alexa',
          // Generic monitoring labels — operator-typed slugs that
          // don't match a concrete brand fall through to uptime-kuma
          // (the canonical free-software uptime monitor mark) so the
          // resolver returns a real file instead of a 404.
          'website-monitoring': 'uptime-kuma',
          'website_monitoring': 'uptime-kuma',
          'uptime-monitor':     'uptime-kuma',
          'monitoring':         'uptime-kuma',
          // Cloudflared has its own file (cloudflared.svg) — see
          // iconUrlFor for the rationale. Other Cloudflare-family
          // products use the parent cloudflare.svg brand mark.
          'cloudflared-tunnel':    'cloudflared',
          'cloudflare-tunnel':     'cloudflare',
          'cloudflare-warp':       'cloudflare',
          'cloudflare-zero-trust': 'cloudflare',
          // GitSync Connector — operator's custom service.
          'gitsync-connector':           'gitsync',
          'gitsync-connector_connector': 'gitsync',
          'gitsync_connector':           'gitsync',
        };
        const slug = aliases[h.icon.toLowerCase()] || h.icon;
        return this._themeIcon('/img/icons/' + slug + '.svg');
      }
      // when the operator has cleared the display label
      // (which falls back to assetForHost(h).name per #621), the icon
      // resolver loses its primary "this is a Synology / Dell / ..."
      // signal because h.label is empty. Fold the asset's name +
      // type_short + vendor + model into the candidate pool AND the
      // keyword-scan hay so cleared-label hosts inherit the asset's
      // brand hint. Cheap lookup — assetForHost is a Map.get().
      const _asset = (typeof this.assetForHost === 'function')
        ? (this.assetForHost(h) || null)
        : null;
      const _assetName  = _asset ? String(_asset.name || '').trim() : '';
      const _assetTypeS = _asset ? String(_asset.type_short || '').trim() : '';
      const _assetVendor = _asset ? String(_asset.vendor || '').trim() : '';
      const _assetModel = _asset ? String(_asset.model || '').trim() : '';
      // Step 2 — exact-slug match on any field.
      const candidates = [
        h.id, h.label, h.host, h.beszel_name, h.pulse_name,
        _assetName, _assetTypeS, _assetVendor, _assetModel,
      ].filter(Boolean);
      for (const c of candidates) {
        const url = this.iconUrlFor(c);
        if (url) return url;
      }
      // Step 3 — keyword scan. Lowercase hay from label + id, then
      // test each known token. Order matters: longer / more specific
      // tokens win first so "nginx-proxy-manager" beats "nginx".
      const hay = [
        h.label, h.id, h.host,
        _assetName, _assetVendor, _assetModel, _assetTypeS,
      ].filter(Boolean).join(' ').toLowerCase();
      // Longest / most specific phrases first so "nginx proxy
      // manager" wins over "nginx" and "home assistant" wins over
      // "home". Every target slug must correspond to a file that
      // actually exists in static/img/icons/ — otherwise the @error
      // handler on the <img> hides the broken image.
      const tokens = [
        // reverse-proxy family
        ['nginx proxy manager',   'nginx-proxy-manager'],
        ['nginxproxymanager',     'nginx-proxy-manager'],
        ['proxy manager',         'nginx-proxy-manager'],
        [' npm',                  'nginx-proxy-manager'],
        ['(npm)',                 'nginx-proxy-manager'],
        ['traefik',               'traefik'],
        ['caddy',                 'caddy'],
        // webservers
        ['nginx',                 'nginx'],
        ['apache',                'apache'],
        // server hardware / lights-out management — checked BEFORE
        // hypervisors so "Dell PowerEdge … (iDRAC)" hits idrac and
        // "VMware vCenter Server" hits vcenter rather than the
        // generic vmware fallback.
        ['idrac',                 'idrac'],
        ['ilo',                   'ilo'],
        ['poweredge',             'poweredge'],
        ['power edge',            'poweredge'],
        ['dell server',           'poweredge'],
        ['proliant',              'proliant'],
        ['dell',                  'dell'],
        // virtualisation suite — most-specific labels first.
        ['vcenter',               'vcenter'],
        ['v-center',              'vcenter'],
        ['vsphere',               'vsphere'],
        ['esxi',                  'esxi'],
        ['esx',                   'esxi'],
        ['vmware',                'vmware'],
        // power / UPS
        ['apc ups',               'apc-ups'],
        ['apc-ups',               'apc-ups'],
        ['apc',                   'apc'],
        [' ups',                  'ups'],
        // firewalls / routers / gateways
        ['opnsense',              'opnsense'],
        ['pfsense',               'pfsense'],
        ['mikrotik',              'mikrotik'],
        // UniFi-product-line phrases win over the bare "ubiquiti"
        // match — more-specific wins when the operator labelled a
        // host with its UniFi flavour.
        ['unifi',                 'unifi'],
        // Reolink — IP cameras / NVRs. Common host-name shapes:
        // "reolink-nvr-01", "cam-reolink-front", "rlc-810a". Placed
        // before the generic "camera" / "nvr" fallbacks below so
        // brand wins over category.
        ['reolink nvr',           'reolink'],
        ['reolink cam',           'reolink'],
        ['reolink camera',        'reolink'],
        ['reolink',               'reolink'],
        ['rlc-',                  'reolink'],
        ['rln-',                  'reolink'],
        // Xiaomi family — Mi Router / Redmi / POCO / Mi Home hubs.
        // Most-specific phrases first so "mi router" wins over
        // bare "mi" (which would also match "mint" etc.).
        ['mi router',             'xiaomi'],
        ['mi-router',             'xiaomi'],
        ['mi home',               'xiaomi'],
        ['mi-home',               'xiaomi'],
        ['mihome',                'xiaomi'],
        ['xiaomi',                'xiaomi'],
        ['redmi',                 'xiaomi'],
        ['poco',                  'xiaomi'],
        // Hisense — TVs (VIDAA OS) + appliances.
        ['hisense',               'hisense'],
        ['vidaa',                 'hisense'],
        // Sensibo — AC controller pucks. Models: Sky, Air, Pod, Pure.
        ['sensibo',               'sensibo'],
        // Ubiquiti family — parent brand mark. Specific product
        // phrases first so "edgerouter" / "airmax" etc. hit even
        // when "ubiquiti" also appears in the label.
        ['edgerouter',            'ubiquiti'],
        ['edge-router',           'ubiquiti'],
        ['edgeswitch',            'ubiquiti'],
        ['edge-switch',           'ubiquiti'],
        ['airfiber',              'ubiquiti'],
        ['airmax',                'ubiquiti'],
        ['amplifi',               'ubiquiti'],
        ['uisp',                  'ubiquiti'],
        ['unifi-os',              'ubiquiti'],
        ['ubnt',                  'ubiquiti'],
        ['ubiquiti',              'ubiquiti'],
        // ASUS routers — typical model strings: "RT-AX88U",
        // "RT-AC68U", "GT-AX11000", "ZenWiFi". "asuswrt" / "merlin"
        // are the firmware names operators sometimes label hosts
        // with. Phrases ordered before the generic "router" fallback
        // above (firewalls/routers/gateways block) so the brand wins.
        ['asus router',           'asus'],
        ['asus vpn',              'asus'],
        ['asuswrt',               'asus'],
        ['merlin',                'asus'],
        ['zenwifi',               'asus'],
        ['rt-ax',                 'asus'],
        ['rt-ac',                 'asus'],
        ['gt-ax',                 'asus'],
        ['asus',                  'asus'],
        // Cisco — enterprise switching / routing / firewall / wireless.
        // Covers the big product families: Meraki (cloud-managed
        // dashboards), ASA (firewalls), Catalyst + Nexus (switches),
        // IOS-XE / IOS-XR (operating systems operators often tag
        // hosts with). Placed before the generic firewall / router
        // fallbacks below so brand wins over category.
        ['cisco meraki',          'cisco'],
        ['meraki',                'cisco'],
        ['cisco asa',             'cisco'],
        ['catalyst',              'cisco'],
        ['nexus',                 'cisco'],
        ['ios-xe',                'cisco'],
        ['ios-xr',                'cisco'],
        ['iosxe',                 'cisco'],
        ['cisco',                 'cisco'],
        // NAS / storage — Synology DSM + Western Digital (DS / RS
        // models for Synology, MyCloud / MyBook / WD Red / WD Blue
        // for Western Digital). Longer phrases first so
        // "western digital" wins over "wd".
        ['synology',              'synology'],
        ['dsm ',                  'synology'],
        ['ds ',                   'synology'],
        ['rs ',                   'synology'],
        ['syno',                  'synology'],
        ['western digital',       'wd'],
        ['western-digital',       'wd'],
        ['mycloud',               'wd'],
        ['my cloud',              'wd'],
        ['mybooklive',            'wd'],
        ['my book',               'wd'],
        ['wdc',                   'wd'],
        [' wd ',                  'wd'],
        // ISP / access-technology routers — longer phrases first so
        // "ftth router" hits ftth (not the bare "router" fallback).
        ['ftth',                  'ftth'],
        ['fiber',                 'ftth'],
        ['fibre',                 'ftth'],
        ['gpon',                  'ftth'],
        ['vdsl',                  'vdsl'],
        ['adsl',                  'vdsl'],
        ['dsl modem',             'vdsl'],
        [' dsl',                  'vdsl'],
        ['5g router',             '5g'],
        ['5g modem',              '5g'],
        ['5g cpe',                '5g'],
        ['cellular',              '5g'],
        [' lte',                  '5g'],
        [' 5g',                   '5g'],
        ['gateway',               'opnsense'],
        ['firewall',              'opnsense'],
        ['router',                'opnsense'],
        // media / entertainment
        ['plex',                  'plex'],
        ['jellyfin',              'jellyfin'],
        ['jellyseerr',            'jellyseerr'],
        ['overseerr',             'jellyseerr'],
        ['tautulli',              'tautulli'],
        ['bazarr',                'bazarr'],
        ['sonarr',                'sonarr'],
        ['radarr',                'radarr'],
        ['prowlarr',              'prowlarr'],
        // smart home
        ['home assistant',        'home-assistant'],
        ['homeassistant',         'home-assistant'],
        ['homebridge',            'homebridge'],
        // ad-blocking / DNS
        ['pi-hole',               'pi-hole'],
        ['pihole',                'pi-hole'],
        ['adguard home',          'adguard-home'],
        ['adguardhome',           'adguard-home'],
        ['adguard',               'adguard-home'],
        ['nebula',                'pi-hole'],
        // identity
        ['authentik',             'authentik'],
        ['keycloak',              'authentik'],
        // orchestration / container tooling
        ['portainer',             'portainer'],
        ['komodo',                'komodo'],
        ['dozzle',                'dozzle'],
        ['homarr',                'homarr'],
        ['homepage',              'homepage'],
        // operating systems — checked BEFORE brand names so
        // "windows server" beats bare "windows".
        ['windows server',        'windows-server'],
        ['windows-server',        'windows-server'],
        ['win server',            'windows-server'],
        ['winsrv',                'windows-server'],
        ['windows 11',            'windows'],
        ['windows 10',            'windows-10'],
        ['windows',               'windows'],
        ['win11',                 'windows'],
        ['win10',                 'windows-10'],
        ['win2019',               'windows-server'],
        ['win2022',               'windows-server'],
        ['win2025',               'windows-server'],
        // hypervisors / storage / platforms
        ['proxmox',               'proxmox'],
        ['pve',                   'proxmox'],
        ['truenas scale',         'truenas-scale'],
        ['truenas-scale',         'truenas-scale'],
        ['truenas core',          'truenas-core'],
        ['truenas-core',          'truenas-core'],
        ['truenas',               'truenas'],
        ['freenas',               'freenas'],
        ['docker',                'docker'],
        ['kubernetes',            'kubernetes'],
        ['k8s',                   'kubernetes'],
        // observability
        ['grafana',               'grafana'],
        ['prometheus',            'prometheus'],
        ['uptime kuma',           'uptime-kuma'],
        ['uptimekuma',            'uptime-kuma'],
        ['netdata',               'netdata'],
        ['beszel',                'beszel'],
        ['pulse',                 'pulse'],
        // job runners / automation
        ['rundeck',               'rundeck'],
        ['n8n',                   'n8n'],
        ['ansible',               'ansible'],
        // git forges
        ['forgejo',               'forgejo'],
        ['gitea',                 'forgejo'],
        // databases — brand-specific first, generic last.
        ['mongodb',               'mongodb'],
        ['mongo',                 'mongodb'],
        ['postgresql',            'postgresql'],
        ['postgres',              'postgresql'],
        ['influxdb',              'influxdb'],
        ['influx',                'influxdb'],
        ['mariadb',               'database'],
        ['mysql',                 'database'],
        ['redis',                 'database'],
        ['sqlite',                'database'],
        ['database',              'database'],
        [' db ',                  'database'],
        // systems management / monitoring
        ['webmin',                'webmin'],
        ['zabbix',                'zabbix'],
        // remote access / desktop
        ['rustdesk',              'rustdesk'],
        // mail — brand-specific first, generic last.
        ['mailcow',               'mailcow'],
        ['stalwart',              'stalwart'],
        ['roundcube',             'roundcube'],
        ['dovecot',               'dovecot'],
        ['smtp relay',            'smtp'],
        ['smtp gateway',          'smtp'],
        ['smtp',                  'smtp'],
        ['mail server',           'mail'],
        ['mailserver',            'mail'],
        ['mail relay',            'mail'],
        ['webmail',               'roundcube'],
        ['imap',                  'mail'],
        [' mail',                 'mail'],
        ['postfix',               'mail'],
        ['mailu',                 'mail'],
        ['maddy',                 'mail'],
        // VPN / tunnelling — checked BEFORE "openvpn" alone so
        // "pivpn" isn't shadowed by the openvpn token.
        ['pivpn',                 'pivpn'],
        ['pi-vpn',                'pivpn'],
        ['tailscale',             'tailscale'],
        ['headscale',             'tailscale'],
        ['openvpn',               'openvpn'],
        ['wireguard',             'wireguard'],
        ['wg-easy',               'wireguard'],
        // Cloudflare family — `cloudflared` (the tunnel daemon) has
        // its own file (cloudflared.svg, same artwork as
        // cloudflare.svg but a distinct URL so the operator's edge
        // cache can't get stuck on a broken response — fitting
        // given they ARE running cloudflared tunnel). Other
        // Cloudflare-family products share the parent
        // cloudflare.svg brand mark. Long-form phrases first so
        // "cloudflare zero trust" wins over bare "cloudflare".
        ['cloudflare zero trust', 'cloudflare'],
        ['cloudflare-zero-trust', 'cloudflare'],
        ['cloudflare tunnel',     'cloudflare'],
        ['cloudflare-tunnel',     'cloudflare'],
        ['cloudflared',           'cloudflared'],
        ['cloudflare warp',       'cloudflare'],
        ['cloudflare-warp',       'cloudflare'],
        ['cloudflare',            'cloudflare'],
        // GitSync Connector — operator's custom container. Long-form
        // phrases first; bare `gitsync` is also a meaningful brand
        // match in case a future stack drops the `-connector` suffix.
        ['gitsync-connector',     'gitsync'],
        ['gitsync_connector',     'gitsync'],
        ['gitsync connector',     'gitsync'],
        ['gitsync',               'gitsync'],
        // download clients
        ['qbittorrent',           'qbittorrent'],
        ['qbit',                  'qbittorrent'],
        ['transmission',          'transmission'],
        ['deluge',                'deluge'],
        ['sabnzbd',               'sabnzbd'],
        ['nzbget',                'nzbget'],
        // notifications / networking
        ['apprise',               'apprise'],
        ['fing',                  'fing'],
        ['myspeed',               'myspeed'],
        ['speedtest',             'speedtest-tracker'],
        ['kavita',                'kavita'],
        ['squid',                 'squid'],
        ['lubelogger',            'lubelogger'],
        // Rachio — smart sprinkler controllers.
        ['rachio',                'rachio'],
        // GL.iNet — travel routers / mini-routers (GL-MT, GL-AR,
        // GL-AXT, Slate, Brume, Beryl model lines).
        ['gl.inet',               'glinet'],
        ['gl-inet',               'glinet'],
        ['glinet',                'glinet'],
        ['gl-mt',                 'glinet'],
        ['gl-ar',                 'glinet'],
        ['gl-axt',                'glinet'],
        ['gl-b',                  'glinet'],
        ['slate-ax',              'glinet'],
        ['brume',                 'glinet'],
        ['beryl-ax',              'glinet'],
        // Somfy — smart-home / motorised-blind hubs (TaHoma, Connexoon).
        ['somfy',                 'somfy'],
        ['tahoma',                'somfy'],
        ['connexoon',             'somfy'],
        // HP / HPE family. Longest phrases first so "HPE ProLiant"
        // hits the existing `proliant` icon rather than falling
        // through to the generic HP wordmark, and "HP printer" /
        // "HP laptop" routes to the HP brand mark. The bare ` hp `
        // (with surrounding whitespace) avoids matching unrelated
        // "https" / "wp" / "shop" substrings inside hostnames.
        ['hewlett-packard',       'hp'],
        ['hewlett packard',       'hp'],
        [' hpe ',                 'hp'],
        ['hpe-',                  'hp'],
        [' hp ',                  'hp'],
        ['hp-',                   'hp'],
        // SanDisk — flash storage / SSDs / SD cards. Common host
        // labels: "SanDisk Extreme", "SD-Pro" model strings.
        ['sandisk',               'sandisk'],
        [' sd-pro',               'sandisk'],
        // Amazon Fire TV — streaming sticks / cubes / TVs running Fire OS.
        // Common host labels: "Fire TV Stick", "Fire TV Cube", "Fire TV 4K".
        ['fire tv',               'firetv'],
        ['fire-tv',               'firetv'],
        ['firetv',                'firetv'],
        ['firestick',             'firetv'],
        ['fire stick',            'firetv'],
        // Amazon Echo / Alexa — smart speakers, Echo Dot / Show / Studio.
        // The dashboard-icons repo's `alexa.svg` is the canonical
        // Alexa-blue swirl mark that Echo devices ship with, so all
        // Echo product variants resolve to it.
        ['amazon echo',           'alexa'],
        ['echo dot',              'alexa'],
        ['echo show',             'alexa'],
        ['echo studio',           'alexa'],
        [' echo ',                'alexa'],
        ['alexa',                 'alexa'],
        // Amazon parent brand — distinct from Alexa/Echo. Order
        // matters: matches AFTER the more-specific Alexa/Echo /
        // Fire-TV phrases so a host labelled "Amazon Echo Dot"
        // resolves to alexa.svg, not amazon.svg.
        ['amazon',                'amazon'],
        // Apple-family devices and OS marks. Apple TV / Apple TV 4K /
        // Apple TV HD all resolve to the apple-tv-plus mark (the
        // canonical curved-edge "tv" Apple uses across hardware +
        // streaming service). Generic Apple phrases fall through to
        // the apple wordmark.
        ['apple tv',              'apple-tv-plus'],
        ['apple-tv',              'apple-tv-plus'],
        ['appletv',               'apple-tv-plus'],
        ['apple homepod',         'apple'],
        ['homepod mini',          'apple'],
        ['homepod',               'apple'],
        ['apple watch',           'apple'],
        ['apple ',                'apple'],
        ['imac',                  'apple'],
        ['macbook',               'apple'],
        ['ipad',                  'apple'],
        ['iphone',                'apple'],
        // Google smart-home line — Nest Hub / Hub Max use the
        // dedicated `nest.svg` mark (Wikimedia Commons "Google Nest
        // logo"), Chromecast uses `chromecast.svg` (Wikimedia
        // Commons "Google Chromecast wordmark"), and the broader
        // Google Home / Home Hub lineup falls back to `google-home`
        // (homarr-labs dashboard-icons). Most-specific phrases first
        // so "Google Nest Hub Max" hits nest, not google-home.
        ['nest hub max',          'nest'],
        ['google nest hub',       'nest'],
        ['nest hub',              'nest'],
        ['google nest',           'nest'],
        ['google chromecast',     'chromecast'],
        ['chromecast',            'chromecast'],
        ['google home hub',       'google-home'],
        ['google home',           'google-home'],
        ['google pixel',          'google'],
        ['pixel ',                'google'],
        // Console gaming
        ['playstation',           'playstation'],
        ['ps4',                   'playstation'],
        ['ps5',                   'playstation'],
        ['nintendo switch',       'nintendo-switch'],
        ['switch 2',              'nintendo-switch'],
        ['nintendo',              'nintendo-switch'],
        // Microsoft + family. Surface lands on microsoft.svg (no
        // dedicated Surface mark in dashboard-icons).
        ['microsoft surface',     'microsoft'],
        ['surface pro',           'microsoft'],
        ['microsoft',             'microsoft'],
        // Hardware brands
        ['lenovo',                'lenovo'],
        ['veeam',                 'veeam'],
        // Linux distros. Longer / more-specific phrases first per the
        // load-bearing keyword-ordering rule, else `mint` would match
        // before `linux mint`. The whitespace-padded ` mint ` short
        // form (added below) protects against substring false-matches
        // inside hostnames like `webmint`, `intermint`, etc.
        ['debian',                'debian'],
        ['ubuntu',                'ubuntu'],
        ['linux mint',            'linuxmint'],
        ['linux-mint',            'linuxmint'],
        ['linuxmint',             'linuxmint'],
        [' mint ',                'linuxmint'],
        ['mint os',               'linuxmint'],
        ['kali linux',            'kali'],
        ['kali',                  'kali'],
        // Meta / Oculus VR
        ['oculus',                'oculus'],
        ['meta quest',            'meta'],
        // Huawei phones / tablets
        ['huawei',                'huawei'],
        // Humax — UK / EU set-top box manufacturer (Freesat, Aura, etc).
        ['humax',                 'humax'],
        // Kaonmedia — Korean set-top box / cable modem maker.
        ['kaonmedia',             'kaonmedia'],
        ['kaon media',            'kaonmedia'],
        ['kaon',                  'kaonmedia'],
        // HDHomeRun — SiliconDust network TV tuner.
        ['hdhomerun',             'hdhomerun'],
        ['hd homerun',            'hdhomerun'],
        ['hd home run',           'hdhomerun'],
        ['silicondust',           'hdhomerun'],
        // J-Tech Digital — HDMI matrix / video distribution gear.
        ['jtech digital',         'jtech'],
        ['jtech',                 'jtech'],
        ['j-tech',                'jtech'],
        ['j tech',                'jtech'],
        // Nixplay — digital photo frames.
        ['nixplay',               'nixplay'],
        // Seeed Studio — open-source hardware (Raspberry Pi accessories,
        // ReSpeaker, ReComputer, ReTerminal, XIAO boards). Long-form
        // first so "seeedstudio" matches before the bare "seeed" pad.
        ['seeed studio',          'seeedstudio'],
        ['seeedstudio',           'seeedstudio'],
        [' seeed ',               'seeedstudio'],
        // Samsung — separate slugs for the parent brand (`samsung`,
        // clean wordmark) vs. the corporate / B2B entity (`samsung-
        // electronics`, the older "Samsung Electronics" mark with the
        // ellipse). Most-specific phrase wins so "samsung electronics"
        // matches the corporate slug while "samsung galaxy" / "samsung tv"
        // land on the consumer wordmark. Order matters here.
        ['samsung electronics',   'samsung-electronics'],
        ['samsungelectronics',    'samsung-electronics'],
        ['samsung galaxy',        'samsung'],
        ['galaxy s',              'samsung'],
        ['galaxy a',              'samsung'],
        ['galaxy m',              'samsung'],
        ['galaxy tab',            'samsung'],
        ['samsung',               'samsung'],
        // Bose audio — SoundTouch / Home Speaker / Wave / QC family.
        ['bose soundtouch',       'bose'],
        ['bose home speaker',     'bose'],
        ['bose ',                 'bose'],
        ['soundtouch',            'bose'],
        // Gigabyte motherboards / desktops / Aorus brand
        ['gigabyte',              'gigabyte'],
        ['aorus',                 'gigabyte'],
        ['b550 aorus',            'gigabyte'],
        // Roku — streaming sticks. simple-icons.org source.
        ['roku',                  'roku'],
        // Alienware (Dell sub-brand) — gaming laptops / desktops.
        ['alienware',             'alienware'],
        // Amazon Kindle (e-reader) — no dedicated icon in either
        // dashboard-icons or simple-icons; falls back to amazon.svg
        // via the parent-brand keyword above.
        ['kindle',                'amazon'],
        // WD TV Live Hub — Western Digital's media-streamer line;
        // reuses the existing wd.svg parent brand mark.
        ['wd tv',                 'wd'],
        ['wd-tv',                 'wd'],
      ];
      for (const [needle, slug] of tokens) {
        if (hay.includes(needle)) return this._themeIcon('/img/icons/' + slug + '.svg');
      }
      return '';
    },
    removeHostRow(idx) {
      this.hostsConfig.splice(idx, 1);
      this.rebuildHostsConfigOrder();
      this.hostsConfigDirty = true;
      // Clamp page after delete — removing the last row on page N
      // would otherwise leave the operator on an empty page.
      this.hostsConfigPage = Math.min(this.hostsConfigPage, this.hostsConfigTotalPages());
    },
    // Manual reorder — simpler than drag-and-drop and works on
    // touch. Wraps around at the ends so the buttons stay useful
    // when a row is already top/bottom (no-op there, so we guard
    // instead of wrapping to avoid surprise). Clears any per-index
    // test result since the row's idx just moved.
    moveHostRow(idx, delta) {
      const dest = idx + delta;
      if (dest < 0 || dest >= this.hostsConfig.length) return;
      const [row] = this.hostsConfig.splice(idx, 1);
      this.hostsConfig.splice(dest, 0, row);
      this.hostsTestResults = {};
      this.hostsConfigDirty = true;
    },
    // Clone an existing host row — preserves every field except the
    // ID (prefixed with "copy-of-" to satisfy the "unique id"
    // constraint server-side) and the enabled flag (off by default
    // so the clone doesn't silently start pulling data before the
    // operator renames it). Inserted right below the source row so
    // the visual relationship is obvious.
    duplicateHostRow(idx) {
      const src = this.hostsConfig[idx];
      if (!src) return;
      const copy = {
        ...src,
        id:    (src.id ? 'copy-of-' + src.id : ''),
        label: (src.label ? src.label + ' (copy)' : ''),
        enabled: false,
      };
      this.hostsConfig.splice(idx + 1, 0, copy);
      this.hostsTestResults = {};
      this.hostsConfigDirty = true;
    },
    async saveHostsConfig() {
      // Clear any inline errors from a prior save attempt before
      // re-validating. #136 makes these per-field (red border +
      // error text beneath the bad input) instead of a toast.
      this.clearFieldErrorsByPrefix('host_');

      let hadError = false;
      const ensureExpanded = (id) => {
        // Expand the row carrying this id so the error-decorated
        // field is visible. Looks up the row by id to find its
        // stable `_uid` (the actual key in hostsConfigExpanded
        // since the typing-collapse fix).
        if (!id) return;
        const row = (this.hostsConfig || []).find(r => r && r.id === id);
        const uid = row && row._uid;
        if (uid && !this.hostsConfigExpanded[uid]) {
          this.hostsConfigExpanded = { ...this.hostsConfigExpanded, [uid]: true };
        }
      };

      // Pre-save validation: if a Webmin URL is set, the webmin_name
      // must also be set. Otherwise the probe has a target but no key
      // to look the returned host up against — would silently produce
      // empty drawer cards.
      for (let i = 0; i < (this.hostsConfig || []).length; i++) {
        const h = this.hostsConfig[i] || {};
        const wurl = (h.webmin_url || '').trim();
        const wname = (h.webmin_name || '').trim();
        if (wurl && !wname) {
          this.setFieldError('host_' + i + '_webmin_name',
            this.t('toasts_extra.webmin_url_without_name_inline'));
          ensureExpanded(h.id);
          hadError = true;
        }
      }
      // Empty id + other data — surface on the id input itself.
      for (let i = 0; i < (this.hostsConfig || []).length; i++) {
        const h = this.hostsConfig[i] || {};
        if ((h.id || '').trim() !== '') continue;
        const hasOtherData = (
          (h.label || '').trim() ||
          (h.ne_url || '').trim() ||
          (h.beszel_name || '').trim() ||
          (h.pulse_name || '').trim() ||
          (h.webmin_name || '').trim() ||
          (h.webmin_url || '').trim() ||
          (h.url || '').trim() ||
          (h.icon || '').trim()
        );
        if (hasOtherData) {
          this.setFieldError('host_' + i + '_id',
            this.t('toasts_extra.id_required_inline'));
          hadError = true;
        }
      }
      // Duplicate custom_number — tag every offending row, not just
      // one. Operator can see the whole collision group at a glance.
      const byCn = new Map();
      (this.hostsConfig || []).forEach((h, i) => {
        const cn = parseInt(h.custom_number, 10);
        if (!Number.isFinite(cn)) return;
        if (!byCn.has(cn)) byCn.set(cn, []);
        byCn.get(cn).push(i);
      });
      for (const [cn, idxs] of byCn.entries()) {
        if (idxs.length < 2) continue;
        for (const i of idxs) {
          this.setFieldError('host_' + i + '_cn',
            this.t('toasts_extra.custom_number_duplicate_inline', { cn }));
          ensureExpanded((this.hostsConfig[i] || {}).id);
        }
        hadError = true;
      }

      if (hadError) {
        this.focusFirstFieldError();
        return;
      }
      // Strip empty rows (no ID) so saving doesn't persist placeholder
      // blanks. The server dedupes by ID in case the same one was
      // typed twice.
      const clean = (this.hostsConfig || []).filter(
        h => (h.id || '').trim() !== '',
      ).map(h => {
        // custom_number: accept integer, numeric string, or blank.
        // Blank / non-numeric → null so the backend stores no value
        // (rows without a number sort last in the "Custom #" view).
        const rawNum = h.custom_number;
        let num = null;
        if (rawNum !== '' && rawNum !== null && rawNum !== undefined) {
          const parsed = parseInt(rawNum, 10);
          if (Number.isFinite(parsed)) num = parsed;
        }
        // Per-host SSH — strip falsy / blank keys so the DB doesn't
        // persist empty strings that would shadow the global default.
        // `fqdn` is the new preferred key; `host` stays for back-compat
        // (the backend's _clean_host_ssh accepts both and the resolver
        // reads `fqdn` first, then `host`).
        const sshIn = h.ssh || {};
        const sshOut = {};
        if ((sshIn.user || '').trim()) sshOut.user = sshIn.user.trim();
        if ((sshIn.fqdn || '').trim()) sshOut.fqdn = sshIn.fqdn.trim();
        if ((sshIn.host || '').trim()) sshOut.host = sshIn.host.trim();
        if (sshIn.port) {
          const p = parseInt(sshIn.port, 10);
          if (Number.isFinite(p) && p >= 1 && p <= 65535) sshOut.port = p;
        }
        // Passwords are write-only — any non-empty string overwrites.
        // Empty = "no override" (fall back to global default password).
        if (typeof sshIn.password === 'string' && sshIn.password !== '') {
          sshOut.password = sshIn.password;
        }
        // Per-host SSH is OPT-IN as of only the explicit
        // `enabled: true` flag survives the round-trip. Absence (or
        // `enabled: false`) means SSH is OFF for the host.
        //
        // **DO NOT add a legacy `disabled=true` fallback here** :
        // this `norm()` runs on EVERY save, not just at import time.
        // A defensive `else if (sshIn.disabled !== true) sshOut.enabled
        // = true` would auto-enable every row that lacks an explicit
        // flag — which is the exact symptom of "enabling host A
        // re-enables host C that was previously disabled". The schema
        // migration in `logic/migrations.py:_migration_001` handles
        // legacy `disabled` → `enabled` ONCE on first boot post-#622;
        // re-applying that conversion per-save corrupts subsequent
        // operator edits.
        if (sshIn.enabled === true) sshOut.enabled = true;
        // Per-host ping. Same shape contract as ssh — strip
        // falsy / blank keys so empty strings don't poison the merge.
        const pingIn = h.ping || {};
        const pingOut = {};
        if (pingIn.enabled) pingOut.enabled = true;
        if (pingIn.port) {
          const pp = parseInt(pingIn.port, 10);
          if (Number.isFinite(pp) && pp >= 1 && pp <= 65535) pingOut.port = pp;
        }
        const pt = String(pingIn.transport || '').trim().toLowerCase();
        if (pt === 'tcp' || pt === 'icmp') pingOut.transport = pt;
        // Per-host SNMP override. Same strip-blanks pattern as
        // ssh / ping — every key falls back to the global default when
        // empty, so we only persist explicit overrides.
        const snmpIn = h.snmp || {};
        const snmpOut = {};
        // explicit opt-IN. Persist `enabled: true` only when
        // the operator checked the box; drop the field otherwise so
        // the persisted JSON stays tight. Backend's _clean_host_snmp
        // mirrors this contract (only persists when raw value is
        // explicitly truthy) and _merge_one_host gates the probe on
        // `enabled is True` (no default-true fallback).
        if (snmpIn.enabled === true) snmpOut.enabled = true;
        const sc = String(snmpIn.community || '').trim();
        if (sc) snmpOut.community = sc;
        const sv = String(snmpIn.version || '').trim().toLowerCase();
        if (sv === 'v2c' || sv === 'v3') snmpOut.version = sv;
        if (snmpIn.port) {
          const sp = parseInt(snmpIn.port, 10);
          if (Number.isFinite(sp) && sp >= 1 && sp <= 65535) snmpOut.port = sp;
        }
        for (const k of ['v3_user', 'v3_auth_key', 'v3_priv_key']) {
          const sval = String(snmpIn[k] || '').trim();
          if (sval) snmpOut[k] = sval;
        }
        // Per-host SNMP walk_concurrency override. Server-class BMCs
        // (Dell iDRAC, Cisco IMC, Supermicro IPMI) handle parallel
        // queries fine and need > 1 to fit pysnmp's per-walk overhead
        // inside the probe budget. Blank / missing → fall through to
        // the global tunable default. Range 1..16 mirrors the
        // backend's _clean_host_snmp validator AND the global
        // tuning_snmp_per_host_walk_concurrency bounds.
        if (snmpIn.walk_concurrency) {
          const wc = parseInt(snmpIn.walk_concurrency, 10);
          if (Number.isFinite(wc) && wc >= 1 && wc <= 16) {
            snmpOut.walk_concurrency = wc;
          }
        }
        // Per-host wall_clock_budget override. Same shape as
        // walk_concurrency — overrides
        // tuning_snmp_wall_clock_budget_seconds when supplied.
        // Range 5..600 mirrors the global tunable's bounds.
        if (snmpIn.wall_clock_budget) {
          const wcb = parseInt(snmpIn.wall_clock_budget, 10);
          if (Number.isFinite(wcb) && wcb >= 5 && wcb <= 600) {
            snmpOut.wall_clock_budget = wcb;
          }
        }
        // Per-host vendor MIB selector. Operator-declared list of
        // vendor MIBs to walk for THIS host (subset of the canonical
        // vendor key set sourced from /api/me's
        // client_config.snmp_vendor_keys — single source of truth at
        // logic/snmp.py:_VALID_VENDOR_KEYS). Empty / missing = auto-
        // detect from sysDescr (the common case). Useful for agents
        // with stripped sysDescr or to force a vendor's walks even
        // when auto-detect would skip them.
        if (Array.isArray(snmpIn.vendors)) {
          const validSet = new Set(this.snmpVendorKeys());
          const cleanVendors = Array.from(new Set(
            snmpIn.vendors
              .map(v => String(v || '').trim().toLowerCase())
              .filter(v => validSet.has(v))
          )).sort();
          if (cleanVendors.length) snmpOut.vendors = cleanVendors;
        }
        // Per-host mount-exclusion list. Operator-supplied paths to
        // drop from the SNMP storage extractor output — covers
        // device-specific phantoms (dd-wrt's `/opt` reporting
        // 232 GB on a 16 MB router) on top of the universal
        // pseudo-fs prefixes that the backend filters by default.
        // Editor renders one entry per line in a textarea; we
        // split + trim + dedupe + cap at 32 entries here.
        if (typeof snmpIn.exclude_mounts_text === 'string') {
          const lines = snmpIn.exclude_mounts_text
            .split(/\r?\n|,/)
            .map(s => s.trim())
            .filter(s => s.length > 0);
          const cleanExcl = Array.from(new Set(lines)).slice(0, 32);
          if (cleanExcl.length) snmpOut.exclude_mounts = cleanExcl;
        } else if (Array.isArray(snmpIn.exclude_mounts)) {
          const cleanExcl = Array.from(new Set(
            snmpIn.exclude_mounts
              .map(s => String(s || '').trim())
              .filter(s => s.length > 0)
          )).slice(0, 32);
          if (cleanExcl.length) snmpOut.exclude_mounts = cleanExcl;
        }
        // host-level enable gates every per-provider enable.
        // A disabled host cannot have any provider enabled. Strip
        // each per-provider `enabled` flag here so reload comes back
        // consistent (no stale `ssh.enabled: true` on a row whose main
        // checkbox is off). Backend `_clean_host_*` validators mirror
        // this defence-in-depth contract — see _save_hosts_config.
        if (h.enabled === false) {
          delete sshOut.enabled;
          delete pingOut.enabled;
          delete snmpOut.enabled;
        }
        return {
          id:            (h.id || '').trim(),
          // Empty label is INTENTIONAL — `hostDisplayName(h)` falls
          // back to `assetForHost(h).name`. Don't auto-fill
          // with id here, because that would PIN the id forever and
          // the operator's intent to "use the asset's name" would be
          // silently dropped on every save.
          label:         (h.label || '').trim(),
          custom_number: num,
          ne_url:        (h.ne_url || '').trim(),
          beszel_name:   (h.beszel_name || '').trim(),
          pulse_name:    (h.pulse_name || '').trim(),
          webmin_name:   (h.webmin_name || '').trim(),
          // SNMP target alias. Empty means "no SNMP for this
          // host". Backend's _clean_host_snmp validates the override
          // dict; bare snmp_name flows through as a string.
          snmp_name:     (h.snmp_name || '').trim(),
          snmp:          snmpOut,
          url:           (h.url || '').trim(),
          icon:          (h.icon || '').trim(),
          ssh:           sshOut,
          ping:          pingOut,
          enabled:       h.enabled !== false,
        };
      });
      // Derive webmin_aliases from each row's webmin_url. Single
      // source of truth in the editor — operator types the URL on
      // the row, we sync it into settings.webmin_aliases on save.
      const webminAliases = {};
      for (const h of (this.hostsConfig || [])) {
        const id = (h.id || '').trim();
        const url = (h.webmin_url || '').trim().replace(/\/$/, '');
        if (id && url) webminAliases[id] = url;
      }
      this.hostsConfigSaving = true;
      try {
        const r = await fetch('/api/hosts/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hosts: clean }),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        const d = await r.json();
        // Capture the previous id→_uid map BEFORE replacing the
        // array. Server-side rows don't carry `_uid` (it's a
        // client-only key), so a naive replace would strip every
        // row's stable identity → `hostsConfigExpanded` keys go
        // stale → chevron clicks early-exit at `!row._uid` →
        // operator can't expand any row. Preserving the existing
        // uids by id keeps the expansion state alive across saves.
        // The same fix protects fresh `+Add` rows (their _uid was
        // minted in `addHostRow`) from being clobbered the moment
        // the operator hits Save.
        const oldUidById = {};
        for (const r of (this.hostsConfig || [])) {
          if (r && r.id && r._uid) oldUidById[r.id] = r._uid;
        }
        this.hostsConfig = d.hosts || [];
        // Invalidate the filtered-list cache (#636 root cause): the
        // cache key is `filter + length + order.length`, which DOESN'T
        // change when the array elements are replaced via `=`. Without
        // the explicit reset, `pagedHostsConfig()` keeps returning the
        // pre-save `{row, idx}` objects whose `row` references point
        // at the OLD array's elements — bindings like the SSH icon's
        // `row.ssh && row.ssh.enabled` read STALE state until a hard
        // refresh rebuilds the cache. Mutate the cache field in place
        // (don't reassign — Alpine's reactivity tracks the existing
        // proxy slot) so the next `filteredHostsConfig()` call sees
        // the empty key and rebuilds against the fresh array.
        this._filteredHostsConfigCache.key = '';
        this._filteredHostsConfigCache.value = null;
        // Re-stamp each row with its webmin_url (the hosts_config
        // endpoint doesn't know about aliases, so the field is
        // load-bearing for the editor UI only) AND its _uid (re-use
        // the previous one when the id matches; mint fresh otherwise).
        for (const row of this.hostsConfig) {
          row.webmin_url = webminAliases[row.id] || '';
          if (!row.ssh || typeof row.ssh !== 'object') row.ssh = {};
          row._uid = oldUidById[row.id] || this._mintRowUid();
        }
        this.rebuildHostsConfigOrder();
        // Persist the derived webmin_aliases in settings so the probe
        // pipeline can read it next gather.
        await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ webmin_aliases: webminAliases }),
        }).catch(() => {});
        if (this.settings) this.settings.webmin_aliases = webminAliases;
        this.hostsConfigDirty = false;
        this.showToast(this.t('admin_hosts.saved_n', { count: d.count }), 'success');
        // Refresh the Hosts tab data unconditionally — was previously
        // gated on ``this.view === 'hosts'`` so an Admin → Hosts save
        // followed by switching to the Hosts page rendered with a
        // STALE `this.hosts` (e.g. ping_enabled flipped to true in the
        // DB but `hostHasAgent(h)` still saw the pre-save snapshot, so
        // "Hide hosts without agents" hid the row until the next 15s
        // polling tick reconciled). The fetch is cheap; running it
        // regardless of current view keeps the per-host provider /
        // ping_enabled state consistent across the whole SPA. ``force=true``
        // busts the backend's 10s provider-state cache so the new
        // aliases / SSH config produce a fresh probe immediately.
        this.loadHosts(true);
      } catch (e) {
        this.showToast(this.t('admin_hosts.save_failed', { error: e.message }), 'error');
      } finally {
        this.hostsConfigSaving = false;
      }
    },

    // --- Host groups ---
    // Operator-defined custom_number ranges that bucket hosts into
    // collapsible sections in the Hosts view. Supports 2-level
    // nesting via `parent_name` + a free-text `ip_range`.
    // Append a SUB-GROUP under a specific top-level parent. Same
    // shape as addHostGroup() but pre-fills `parent_name` so the
    // operator doesn't have to navigate the parent dropdown — the
    // Hosts-groups admin tab grew a `+` chip on each top-level
    // group's header that fires this. Range pre-fill walks every
    // EXISTING sub-group of that parent to land at
    // `max(child range_end) + 1`, falling back to the parent's
    // `range_start` so a fresh-no-children parent gets a sensible
    // first sub-group window.
    addHostSubGroup(parentName) {
      const name = (parentName || '').trim();
      if (!name) return;
      const groups = this.hostGroups || [];
      const parent = groups.find(g => !(g && g.parent_name) && (g.name || '').trim() === name);
      if (!parent) return;
      const childEnds = groups
        .filter(g => g && (g.parent_name || '').trim() === name)
        .map(g => parseInt(g.range_end, 10))
        .filter(n => Number.isFinite(n) && n >= 0);
      const startAt = childEnds.length
        ? Math.max(...childEnds) + 1
        : (parseInt(parent.range_start, 10) || 1);
      const SPAN = 4;
      // Cap end at the parent's range_end so the sub-group's range
      // CAN'T spill outside its parent (the save-side validator
      // already enforces this; pre-filling within bounds avoids
      // an immediate inline error after click).
      const parentEnd = parseInt(parent.range_end, 10);
      let endAt = startAt + SPAN - 1;
      if (Number.isFinite(parentEnd)) endAt = Math.min(endAt, parentEnd);
      const next = {
        name: '',
        range_start: startAt,
        range_end:   endAt,
        order: groups.length,
        // parent_name is intentionally empty here — set inside
        // $nextTick below so the <select>'s <option> list (populated
        // by an x-for over `topLevelGroupNames`) finishes rendering
        // BEFORE x-model tries to find a match. Without the defer,
        // browsers showed the dropdown falling back to "— top-level —"
        // even though `g.parent_name` was set in state, because the
        // matching <option value="Switches"> didn't exist yet at the
        // moment the select committed its initial value.
        parent_name: '',
        ip_range: '',
        ssh: { user: '', port: '', password: '', password_set: false, clear_password: false },
      };
      // Capture the index BEFORE the array reassignment so we can
      // reach the new row through `this.hostGroups[newIdx]` later.
      // Reference-based lookup (`find(g => g === next)`) doesn't
      // work: Alpine wraps `this.hostGroups` in a reactive Proxy on
      // assignment, so iterating the array yields proxied entries
      // that compare unequal to the raw `next` literal — find()
      // returns undefined and the deferred parent_name assignment
      // silently no-ops, leaving the dropdown stuck on "— top-level —".
      const newIdx = groups.length;
      this.hostGroups = [...groups, next];
      this.hostGroupsDirty = true;
      // Make sure the parent's children block is EXPANDED so the new
      // sub-group is immediately visible — operators just clicked on
      // that parent's "+", they expect to see the result.
      const collapsed = new Set(this.hostGroupsEditorChildrenCollapsed || []);
      if (collapsed.has(name)) {
        collapsed.delete(name);
        this.hostGroupsEditorChildrenCollapsed = [...collapsed];
        try {
          if (typeof localStorage !== 'undefined') {
            localStorage.setItem(
              'hostGroupsEditorChildrenCollapsed',
              JSON.stringify(this.hostGroupsEditorChildrenCollapsed),
            );
          }
        } catch (_) {}
      }
      // Now that the new row is in the DOM and its <option> elements
      // exist, set parent_name. The select's x-model picks up the
      // new value, the matching <option> is in place, and the
      // dropdown displays "<parentName>" correctly. Mutating through
      // `this.hostGroups[newIdx]` goes via Alpine's Proxy so the
      // reactivity flush triggers the select-binding effect.
      this.$nextTick(() => {
        const list = this.hostGroups || [];
        if (list[newIdx]) list[newIdx].parent_name = name;
        // Then page-jump to the new sub-group's row so it scrolls
        // into view alongside its parent. Same calculation as
        // addHostGroup — done after the parent_name set so the
        // sortedGroupsForEditor() output reflects the updated hierarchy.
        const sorted = this.sortedGroupsForEditor();
        const pos = sorted.findIndex(e => e.origIdx === newIdx);
        if (pos >= 0) {
          const per = this.hostGroupsPerPage || 50;
          this.hostGroupsGoToPage(Math.floor(pos / per) + 1);
        }
        // Scroll the new sub-group card into view + focus its name
        // input. setTimeout(0) waits for Alpine's $nextTick after the
        // page-jump to mount the row's DOM element on the new page;
        // querying immediately would miss the freshly-rendered card.
        setTimeout(() => {
          const card = document.querySelector(`[data-host-group-card="${newIdx}"]`);
          if (card && typeof card.scrollIntoView === 'function') {
            card.scrollIntoView({ behavior: 'smooth', block: 'center' });
            const nameInput = card.querySelector('input[type="text"]');
            if (nameInput && typeof nameInput.focus === 'function') nameInput.focus();
          }
        }, 50);
      });
    },

    addHostGroup() {
      // Smart range pre-fill — look at every existing top-level group's
      // range_end and start the new group at `max(range_end) + 1`.
      // Spans 10 by default (same as before), so a fresh deploy with
      // zero groups still gets 1–10; a deploy with "ISP Routers 1–4",
      // "Gateways 5–8", "Switches 9–16" lands the new group at 17–26
      // rather than colliding at 1–10. Sub-groups don't influence the
      // top-level walk — they live inside a parent's range. Operator
      // is always free to overwrite; this just removes the busywork.
      const tops = (this.hostGroups || []).filter(g => !(g && g.parent_name));
      const ends = tops.map(g => {
        const n = parseInt(g && g.range_end, 10);
        return Number.isFinite(n) && n >= 0 ? n : null;
      }).filter(n => n != null);
      const startAt = ends.length ? Math.max(...ends) + 1 : 1;
      const SPAN = 10;
      const next = {
        name: '',
        range_start: startAt,
        range_end:   startAt + SPAN - 1,
        order: (this.hostGroups || []).length,
        parent_name: '',
        ip_range: '',
        ssh: { user: '', port: '', password: '', password_set: false, clear_password: false },
      };
      this.hostGroups = [...(this.hostGroups || []), next];
      this.hostGroupsDirty = true;
      // Jump to whichever page the new top-level row landed on so
      // the operator's focus follows the click. Mirrors the
      // addHostRow → page-jump pattern in the Hosts editor.
      this.$nextTick(() => {
        const sorted = this.sortedGroupsForEditor();
        const newOrigIdx = (this.hostGroups || []).length - 1;
        const pos = sorted.findIndex(e => e.origIdx === newOrigIdx);
        if (pos >= 0) {
          const per = this.hostGroupsPerPage || 50;
          this.hostGroupsGoToPage(Math.floor(pos / per) + 1);
        }
      });
    },
    // Top-level group names (for the parent <select> options). The
    // current row is excluded — a group cannot be its own parent. A
    // group that's already a sub-group is also excluded since
    // nesting is capped at 2 levels.
    // Render a host-group heading for the Hosts view. Prepends the
    // operator's optional `number` prefix when set so groups read as
    // "32 Smart & IOT Routers" rather than just "Smart & IOT Routers".
    hostGroupHeading(g) {
      if (!g) return '';
      const name = String(g.name || '');
      const num = (g.number != null && +g.number > 0) ? +g.number : null;
      // Format: "<number>. <name>" — dot separator for visual
      // clarity in the Hosts view headings ("32. ISP Routers" reads
      // cleaner than "32 ISP Routers" when the name itself contains
      // numbers).
      return num != null ? `${num}. ${name}` : name;
    },

    topLevelGroupNames(excludeIdx) {
      return (this.hostGroups || [])
        .map((g, i) => ({ g, i }))
        .filter(({ g, i }) => i !== excludeIdx
          && !g.parent_name
          && (g.name || '').trim())
        .map(({ g }) => g.name);
    },

    // Total hosts in a top-level bucket = its own direct hosts +
    // every host in its sub-groups. Used for the heading count so
    // operators see the parent group's "true" reach (otherwise a
    // parent that has only sub-groups looks like 0 hosts).
    bucketTotalHosts(bucket) {
      const own = (bucket && bucket.hosts && bucket.hosts.length) || 0;
      const sub = ((bucket && bucket.children) || [])
        .reduce((acc, c) => acc + ((c && c.hosts && c.hosts.length) || 0), 0);
      return own + sub;
    },
    // Flat render-list for the Hosts view's host-row template.
    // Parent-direct hosts first, then each sub-group's hosts. The
    // host object is SPREAD into the entry (so `h.label`, `h.host`,
    // `h.providers` etc. all pass through unchanged) and two extra
    // markers are attached:
    //   _sub_group   — the sub-group this row belongs to (null for
    //                  parent-direct rows).
    //   _sub_heading — true on the FIRST row of a sub-group; the
    //                  template emits the heading once before that
    //                  row, then false on subsequent rows.
    // Result: the SAME full host row markup (custom_number chip,
    // provider chips, asset location subline, drawer-expandable
    // content) renders for both parent and sub-group hosts. No
    // duplicated template — adding a feature in one place picks it
    // up everywhere.
    bucketRenderList(bucket) {
      const out = [];
      for (const h of (bucket.hosts || [])) {
        out.push(Object.assign({}, h, { _sub_group: null, _sub_heading: false }));
      }
      for (const sub of (bucket.children || [])) {
        const subHosts = sub.hosts || [];
        if (subHosts.length === 0) {
          // Empty sub-group. With "Hide hosts without agents" ON the
          // operator wants the noise reduced — skip the heading entirely
          // (the group has nothing to show). With the filter OFF, we
          // STILL skip the heading because the previous heading-only
          // marker created confusion (operator's preference iterated
          // multiple times — final landing: hide empties always; the
          // group definition is still visible in Admin → Host Groups).
          continue;
        }
        for (let i = 0; i < subHosts.length; i++) {
          out.push(Object.assign({}, subHosts[i], {
            _sub_group:   sub.group,
            _sub_heading: i === 0,
          }));
        }
      }
      return out;
    },

    // Visual order for the groups editor — each top-level group
    // immediately followed by its sub-groups. Operator's `order`
    // field still drives top-level ordering; sub-group order within
    // a parent cluster is preserved by raw-array insertion order.
    // Keeps `origIdx` (the position in the raw `hostGroups` array)
    // so save-validation + per-row button handlers can reach back
    // to the storage without rebuilding the list.
    sortedGroupsForEditor() {
      const arr = (this.hostGroups || []).map((g, i) => ({ g, origIdx: i }));
      // Pass 1: top-level rows in original order (preserving the
      // operator's move-up/move-down choices).
      const tops = arr.filter(e => !e.g.parent_name);
      // Pass 2: group sub-rows by their parent_name.
      const subs = new Map();
      for (const e of arr) {
        if (!e.g.parent_name) continue;
        const key = e.g.parent_name;
        if (!subs.has(key)) subs.set(key, []);
        subs.get(key).push(e);
      }
      // Weave: each top-level row followed by its children — but
      // skip the children when their parent is collapsed via the
      // editor-side toggle. Operator can still expand to edit them.
      //
      // `seen` MUST include kids whose parent exists even when we
      // skip rendering them (collapsed). Otherwise the orphan-catch
      // below re-adds every hidden child at the bottom of the list,
      // which is what produced the "hides first parent's kids only"
      // bug — collapsing parent B simply relocated B's kids to the
      // trailing orphan bucket instead of hiding them.
      const collapsed = new Set(this.hostGroupsEditorChildrenCollapsed || []);
      const out = [];
      const seen = new Set();
      for (const t of tops) {
        out.push(t);
        seen.add(t.origIdx);
        const kids = subs.get(t.g.name);
        if (!kids) continue;
        for (const k of kids) seen.add(k.origIdx);
        if (!collapsed.has(t.g.name)) out.push(...kids);
      }
      // True orphaned sub-groups (parent_name set but no matching
      // top-level group) still sink to the bottom so they stay
      // visible for repair rather than silently disappearing.
      // `saveHostGroups` rejects them with inline errors, but the
      // operator has to SEE them first.
      for (const e of arr) {
        if (!seen.has(e.origIdx)) out.push(e);
      }
      return out;
    },

    // Page slice of `sortedGroupsForEditor()` — keeps the same
    // `{g, origIdx, listIdx}` shape so the per-row buttons (move /
    // delete / sub-group add) reach back to the unsliced list with
    // their original indices intact. Page is clamped lazily so a
    // delete that drops the total below the current page falls back
    // to the new last page rather than rendering empty.
    pagedGroupsForEditor() {
      const all = this.sortedGroupsForEditor();
      const per = this.hostGroupsPerPage || 50;
      const totalPages = Math.max(1, Math.ceil(all.length / per));
      const page = Math.min(Math.max(1, this.hostGroupsPage), totalPages);
      const start = (page - 1) * per;
      // Re-emit as {g, origIdx, listIdx} so the template's existing
      // destructure pattern continues to work — listIdx points at the
      // ABSOLUTE position in the unpaged list, not the slice, so
      // move-up / move-down arithmetic stays correct.
      return all
        .slice(start, start + per)
        .map((entry, sliceIdx) => ({
          g: entry.g,
          origIdx: entry.origIdx,
          listIdx: start + sliceIdx,
        }));
    },
    hostGroupsTotalPages() {
      const total = this.sortedGroupsForEditor().length;
      const per = this.hostGroupsPerPage || 50;
      return Math.max(1, Math.ceil(total / per));
    },
    hostGroupsGoToPage(n) {
      const tp = this.hostGroupsTotalPages();
      this.hostGroupsPage = Math.min(Math.max(1, parseInt(n, 10) || 1), tp);
    },
    hostGroupsPrevPage() { this.hostGroupsGoToPage(this.hostGroupsPage - 1); },
    hostGroupsNextPage() { this.hostGroupsGoToPage(this.hostGroupsPage + 1); },
    hostGroupsSetPerPage(n) {
      const v = parseInt(n, 10);
      if (!Number.isFinite(v) || v < 1) return;
      this.hostGroupsPerPage = v;
      try { localStorage.setItem('hostGroupsPerPage', String(v)); } catch {}
      this.hostGroupsPage = Math.min(this.hostGroupsPage, this.hostGroupsTotalPages());
    },
    // Bulk-collapse + scroll-to-top for the sticky action bar — the
    // groups editor has its own collapse state (per parent name)
    // separate from the Hosts editor; reuse the existing
    // collapseAllHostGroupChildren handler for the action bar's
    // "Collapse all" button.
    scrollToHostGroupsTop() {
      try {
        window.scrollTo({ top: 0, behavior: 'smooth' });
      } catch {
        window.scrollTo(0, 0);
      }
    },
    // Number of sub-group rows under a given top-level group name.
    // Used by the editor's collapse toggle so the operator sees
    // "(N children)" before deciding whether to expand.
    hostGroupChildCount(parentName) {
      const name = (parentName || '').trim();
      if (!name) return 0;
      return (this.hostGroups || [])
        .filter(g => (g.parent_name || '').trim() === name)
        .length;
    },
    // Editor-side collapse predicate. Top-level groups whose name is
    // in `hostGroupsEditorChildrenCollapsed` hide their sub-rows.
    isHostGroupChildrenCollapsed(parentName) {
      return (this.hostGroupsEditorChildrenCollapsed || [])
        .includes(parentName || '');
    },
    // Toggle the editor-side collapse for one top-level group +
    // persist to localStorage so it survives reloads. The Hosts
    // VIEW collapse (hostGroupsCollapsed) is intentionally separate
    // — operators may want to keep the sidebar compact without
    // affecting the editor, or vice versa.
    // Bulk toggle: hide / show every top-level group's sub-rows in
    // one click. Useful when the editor has 10+ parents and the
    // operator wants to scan only the top-level rows. Persists to
    // the same localStorage key as the per-parent toggles so the
    // state survives reloads.
    collapseAllHostGroupChildren() {
      const names = (this.hostGroups || [])
        .filter(g => g && !g.parent_name && (g.name || '').trim()
                     && this.hostGroupChildCount(g.name) > 0)
        .map(g => g.name);
      this.hostGroupsEditorChildrenCollapsed = [...new Set(names)];
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(
            'hostGroupsEditorChildrenCollapsed',
            JSON.stringify(this.hostGroupsEditorChildrenCollapsed),
          );
        }
      } catch (_) {}
    },
    expandAllHostGroupChildren() {
      // Capture which parents had their children hidden BEFORE
      // wiping the set — those rows are about to re-enter the DOM
      // and need the same select-mount-race fix as
      // toggleHostGroupChildrenCollapsed (see #230).
      const wereCollapsed = new Set(this.hostGroupsEditorChildrenCollapsed || []);
      this.hostGroupsEditorChildrenCollapsed = [];
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(
            'hostGroupsEditorChildrenCollapsed',
            JSON.stringify(this.hostGroupsEditorChildrenCollapsed),
          );
        }
      } catch (_) {}
      // Re-touch every now-visible sub-group's parent_name with the
      // empty→set dance (same as toggleHostGroupChildrenCollapsed).
      // Self-assignment is elided by Alpine; we must clear the value
      // first, let Alpine render with the empty placeholder, then in
      // a double-nextTick reassign the real value so x-model rebinds
      // after the <option> x-for has finished inserting its children.
      if (wereCollapsed.size === 0) return;
      const groups = this.hostGroups || [];
      const restore = [];
      for (let i = 0; i < groups.length; i++) {
        const g = groups[i];
        const parent = (g && g.parent_name || '').trim();
        if (parent && wereCollapsed.has(parent)) {
          restore.push({ idx: i, value: g.parent_name });
          g.parent_name = '';
        }
      }
      if (restore.length === 0) return;
      this.$nextTick(() => {
        this.$nextTick(() => {
          const list = this.hostGroups || [];
          for (const { idx, value } of restore) {
            if (list[idx]) list[idx].parent_name = value;
          }
        });
      });
    },
    toggleHostGroupChildrenCollapsed(parentName) {
      const name = (parentName || '').trim();
      if (!name) return;
      const set = new Set(this.hostGroupsEditorChildrenCollapsed || []);
      const wasCollapsed = set.has(name);
      if (wasCollapsed) set.delete(name); else set.add(name);
      this.hostGroupsEditorChildrenCollapsed = [...set];
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(
            'hostGroupsEditorChildrenCollapsed',
            JSON.stringify(this.hostGroupsEditorChildrenCollapsed),
          );
        }
      } catch (_) {}
      // Same Alpine select-mount race as when sub-group rows
      // re-enter the DOM after un-collapsing, each row's <select>
      // mounts BEFORE the inner x-for finishes rendering its
      // <option> elements (populated from `topLevelGroupNames`).
      // Without intervention the select silently falls back to the
      // empty "— top-level —" placeholder even though `g.parent_name`
      // is set in state. Fix: clear parent_name to '' synchronously
      // (forces Alpine to render the empty placeholder), then in a
      // double-nextTick (after Alpine flushes the new DOM AND the
      // <option> x-for finishes inserting its children) reassign
      // the original value. The empty→set dance is what makes the
      // x-model effect fire on a TRUE value change and rebind the
      // select. Self-assignment doesn't work because Alpine elides
      // identical-value writes.
      if (!wasCollapsed) return;  // we just collapsed — no children visible to fix
      const groups = this.hostGroups || [];
      const restore = [];
      for (let i = 0; i < groups.length; i++) {
        const g = groups[i];
        const parent = (g && g.parent_name || '').trim();
        if (parent && parent === name) {
          restore.push({ idx: i, value: g.parent_name });
          g.parent_name = '';
        }
      }
      if (restore.length === 0) return;
      // First $nextTick: Alpine has rendered each sub-group's row
      // with parent_name='' so the <select> mounts uncontroversially
      // on the empty option. Second $nextTick: the inner <option>
      // x-for has now finished, so reassigning the real parent_name
      // lets x-model find the matching <option value="...">.
      this.$nextTick(() => {
        this.$nextTick(() => {
          const list = this.hostGroups || [];
          for (const { idx, value } of restore) {
            if (list[idx]) list[idx].parent_name = value;
          }
        });
      });
    },
    // Move a row up/down in the VISIBLE order. Translates to a raw-
    // array swap so sub-groups stick next to their parent after the
    // sort re-runs. Clamped to same bucket: we don't allow moving a
    // sub-group above its parent or past a sibling's range boundary
    // via move buttons alone — that'd break the containment rule
    // silently. Move across buckets via re-parenting (the dropdown).
    moveHostGroupByListIdx(listIdx, dir) {
      const sorted = this.sortedGroupsForEditor();
      const j = listIdx + dir;
      if (j < 0 || j >= sorted.length) return;
      const src = sorted[listIdx];
      const dst = sorted[j];
      // Refuse cross-bucket moves: a top-level row can't swap with a
      // sub-group and vice-versa, because the sort would immediately
      // undo it. Silent no-op keeps the button harmless.
      const srcParent = src.g.parent_name || src.g.name;
      const dstParent = dst.g.parent_name || dst.g.name;
      if (srcParent !== dstParent) return;
      const arr = [...this.hostGroups];
      [arr[src.origIdx], arr[dst.origIdx]] = [arr[dst.origIdx], arr[src.origIdx]];
      arr.forEach((g, i) => { g.order = i; });
      this.hostGroups = arr;
      this.hostGroupsDirty = true;
    },
    removeHostGroup(idx) {
      this.hostGroups = (this.hostGroups || []).filter((_, i) => i !== idx);
      this.hostGroupsDirty = true;
    },
    moveHostGroup(idx, dir) {
      const arr = [...(this.hostGroups || [])];
      const j = idx + dir;
      if (j < 0 || j >= arr.length) return;
      [arr[idx], arr[j]] = [arr[j], arr[idx]];
      // Renumber `order` to match new array positions so the server
      // round-trips the change on next load.
      arr.forEach((g, i) => { g.order = i; });
      this.hostGroups = arr;
      this.hostGroupsDirty = true;
    },
    markHostGroupDirty() { this.hostGroupsDirty = true; },

    // ---- Inline-field-error helpers ----
    // Keyed storage lives on `fieldErrors`. Callers set a specific
    // error text for a specific input (keyed by a stable "scope_idx_field"
    // id) and the templates render red-bordered inputs + an error
    // hint beneath. Clearing on @input means the red cue goes away
    // as soon as the operator starts fixing it — classic
    // jQuery-validate feel without the dependency.
    setFieldError(key, msg) {
      this.fieldErrors = { ...this.fieldErrors, [key]: msg };
    },
    clearFieldError(key) {
      if (key in this.fieldErrors) {
        const next = { ...this.fieldErrors };
        delete next[key];
        this.fieldErrors = next;
      }
    },
    clearFieldErrorsByPrefix(prefix) {
      const next = {};
      for (const k of Object.keys(this.fieldErrors || {})) {
        if (!k.startsWith(prefix)) next[k] = this.fieldErrors[k];
      }
      this.fieldErrors = next;
    },
    hasFieldError(key) { return !!(this.fieldErrors && this.fieldErrors[key]); },
    fieldError(key)    { return (this.fieldErrors || {})[key] || ''; },
    // Focus the DOM input whose x-model ends in the given field name
    // on the given row. Used after validation sets an error so the
    // operator's cursor lands on the first failing field.
    focusFirstFieldError() {
      const first = Object.keys(this.fieldErrors || {})[0];
      if (!first) return;
      // If the first error is keyed against a hostsConfig row that
      // lives on a different page (#331 paginates the editor),
      // navigate to that page BEFORE the DOM query — otherwise the
      // .field-invalid element doesn't exist and focus silently
      // no-ops, leaving the operator confused about why save failed.
      const m = first.match(/^host_(\d+)_/);
      if (m) {
        const rowIdx = parseInt(m[1], 10);
        const all = this.filteredHostsConfig();
        const pos = all.findIndex(({ idx }) => idx === rowIdx);
        if (pos >= 0) {
          const per = this.hostsConfigPerPage || 50;
          this.hostsConfigGoToPage(Math.floor(pos / per) + 1);
        } else if ((this.hostsConfigFilter || '').trim()) {
          // Field error lives on a row that's been filtered out — the
          // page-jump above silently fails and the operator sees a
          // generic "Save failed" toast with no actionable target. Show
          // a SweetAlert with a one-click "Clear filter" action so they
          // can reach the offending row (#368 / UX-002).
          if (typeof Swal !== 'undefined') {
            Swal.fire({
              icon: 'warning',
              title: this.t('admin_hosts.errors.filtered_title'),
              text: this.t('admin_hosts.errors.filtered_body'),
              confirmButtonText: this.t('admin_hosts.errors.filtered_clear'),
              showCancelButton: true,
              cancelButtonText: this.t('actions.cancel'),
            }).then((result) => {
              if (result.isConfirmed) {
                this.hostsConfigFilter = '';
                this.$nextTick(() => this.focusFirstFieldError());
              }
            });
            return;
          }
          this.showToast(
            this.t('admin_hosts.errors.filtered_body'),
            'error',
          );
          return;
        }
      }
      // Best-effort DOM lookup — errors are keyed by a stable id and
      // the templates bind `:class="hasFieldError('...')"` on the
      // input. A short delay lets Alpine finish rendering the error
      // state before we try to scroll into view. Bumped from 30 → 80
      // ms because we may have just changed the page above and Alpine
      // needs an extra tick to mount the new slice.
      setTimeout(() => {
        const el = document.querySelector('.field-invalid');
        if (el && typeof el.focus === 'function') {
          el.focus();
          if (typeof el.scrollIntoView === 'function') {
            el.scrollIntoView({ block: 'center', behavior: 'smooth' });
          }
        }
      }, 80);
    },

    async saveHostGroups() {
      // Clear any inline errors from a previous attempt before
      // re-validating.
      this.clearFieldErrorsByPrefix('group_');

      const clean = [];
      const indexMap = []; // clean[j] came from hostGroups[indexMap[j]]
      let hadError = false;
      (this.hostGroups || []).forEach((g, gi) => {
        const name = String(g.name || '').trim();
        if (!name) {
          // Silently skip empty-name rows — matches legacy behaviour.
          return;
        }
        const rs = parseInt(g.range_start, 10);
        const re_ = parseInt(g.range_end, 10);
        if (!Number.isFinite(rs) || !Number.isFinite(re_)) {
          this.setFieldError('group_' + gi + '_range',
            this.t('admin_hosts.groups.range_required'));
          hadError = true;
          return;
        }
        if (rs < 0 || re_ < rs) {
          this.setFieldError('group_' + gi + '_range',
            this.t('admin_hosts.groups.invalid_range', { name }));
          hadError = true;
          return;
        }
        const parent_name = String(g.parent_name || '').trim();
        const ip_range = String(g.ip_range || '').trim();
        // SSH block — only send fields the operator actually touched.
        // `password` is keep-current-if-blank (matches the global
        // secret store contract); `clear_password: true` is the
        // explicit-clear escape hatch.
        const sshIn = (g && g.ssh && typeof g.ssh === 'object') ? g.ssh : {};
        const ssh = {};
        const sUser = String(sshIn.user || '').trim();
        if (sUser) ssh.user = sUser;
        const sPort = sshIn.port;
        if (sPort != null && sPort !== '') {
          const pi = parseInt(sPort, 10);
          if (Number.isFinite(pi) && pi >= 1 && pi <= 65535) ssh.port = pi;
        }
        const sPw = String(sshIn.password || '').trim();
        if (sPw) ssh.password = sPw;
        if (sshIn.clear_password) ssh.clear_password = true;
        // Optional display-prefix number. Validated as a positive
        // integer when set; uniqueness check fires later (after every
        // row is parsed so we can name the conflicting group).
        let numberVal = null;
        const numberRaw = g.number;
        if (numberRaw !== '' && numberRaw != null) {
          const n = parseInt(numberRaw, 10);
          if (!Number.isFinite(n) || n <= 0) {
            this.setFieldError('group_' + gi + '_number',
              this.t('admin_hosts.groups.invalid_number') || 'Number must be a positive integer.');
            hadError = true;
            return;
          }
          numberVal = n;
        }
        clean.push({
          // Round-trip the stable id (server mints it on first save;
          // null/blank for fresh rows so the backend assigns one).
          // Without this, a rename would lose the password keep-
          // current carryover. See BUG-003 fix.
          id: String(g.id || ''),
          name, range_start: rs, range_end: re_,
          order: Number.isFinite(+g.order) ? +g.order : clean.length,
          parent_name: parent_name || null,
          ip_range,
          number: numberVal,
          ssh,
        });
        indexMap.push(gi);
      });
      if (hadError) {
        this.focusFirstFieldError();
        return;
      }

      // Number uniqueness — when set, no two groups may share the
      // same prefix. Reports both names so the operator knows where
      // the conflict is without scrolling.
      const seenNumbers = new Map();
      for (let j = 0; j < clean.length; j++) {
        const g = clean[j];
        if (g.number == null) continue;
        const prior = seenNumbers.get(g.number);
        if (prior !== undefined) {
          const gi = indexMap[j];
          this.setFieldError('group_' + gi + '_number',
            this.t('admin_hosts.groups.err_number_dupe', {
              other: prior.name,
            }) || `Number ${g.number} already used by "${prior.name}".`);
          hadError = true;
          continue;
        }
        seenNumbers.set(g.number, { name: g.name, idx: indexMap[j] });
      }
      if (hadError) { this.focusFirstFieldError(); return; }

      // Parent existence + self-parent + depth-1 checks.
      const byName = new Map();
      clean.forEach(g => byName.set(g.name, g));
      for (let j = 0; j < clean.length; j++) {
        const g = clean[j];
        const gi = indexMap[j];
        if (!g.parent_name) continue;
        if (g.parent_name === g.name) {
          this.setFieldError('group_' + gi + '_parent',
            this.t('admin_hosts.groups.err_self_parent'));
          hadError = true;
          continue;
        }
        const p = byName.get(g.parent_name);
        if (!p) {
          this.setFieldError('group_' + gi + '_parent',
            this.t('admin_hosts.groups.err_parent_missing', { name: g.parent_name }));
          hadError = true;
          continue;
        }
        if (p.parent_name) {
          this.setFieldError('group_' + gi + '_parent',
            this.t('admin_hosts.groups.err_parent_is_sub', { name: g.parent_name }));
          hadError = true;
          continue;
        }
        // Containment: sub-group range must be inside parent range.
        if (!(p.range_start <= g.range_start && g.range_end <= p.range_end)) {
          this.setFieldError('group_' + gi + '_range',
            this.t('admin_hosts.groups.err_not_contained', {
              parent: p.name,
              ps: p.range_start, pe: p.range_end,
            }));
          hadError = true;
        }
      }
      if (hadError) { this.focusFirstFieldError(); return; }

      // Overlap: every pair that is NOT parent-child must be disjoint.
      for (let i = 0; i < clean.length; i++) {
        for (let j = i + 1; j < clean.length; j++) {
          const a = clean[i], b = clean[j];
          const pc = (a.parent_name === b.name) || (b.parent_name === a.name);
          if (pc) continue;
          if (a.range_start <= b.range_end && b.range_start <= a.range_end) {
            const firstIdx = indexMap[i];
            this.setFieldError('group_' + firstIdx + '_range',
              this.t('admin_hosts.groups.err_overlap', {
                other: b.name, os: b.range_start, oe: b.range_end,
              }));
            hadError = true;
          }
        }
      }
      if (hadError) { this.focusFirstFieldError(); return; }

      this.hostGroupsSaving = true;
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ host_groups: clean }),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        // Reload so the server's cleaned / sorted view replaces ours.
        await this.loadSettings();
        // Bust the groupedHosts() memo (#423 / ENH-008). loadSettings
        // changes hostGroups in place; bumping the revision counter
        // forces the next access to recompute even if the array
        // identity / length didn't change.
        this.hostGroupsRevision = (this.hostGroupsRevision || 0) + 1;
        this.showToast(this.t('admin_hosts.groups.saved'), 'success');
      } catch (e) {
        this.showToast(this.t('admin_hosts.groups.save_failed') + ': ' + e.message, 'error');
      } finally {
        this.hostGroupsSaving = false;
      }
    },

    // Hosts view — group-related helpers.
    isGroupCollapsed(name) {
      return (this.hostGroupsCollapsed || []).includes(name);
    },
    toggleGroup(name) {
      const set = new Set(this.hostGroupsCollapsed || []);
      if (set.has(name)) set.delete(name); else set.add(name);
      this.hostGroupsCollapsed = Array.from(set);
      try {
        localStorage.setItem(
          'hostGroupsCollapsed',
          JSON.stringify(this.hostGroupsCollapsed),
        );
      } catch {}
    },
    expandAllGroups() {
      this.hostGroupsCollapsed = [];
      try { localStorage.setItem('hostGroupsCollapsed', '[]'); } catch {}
    },
    collapseAllGroups() {
      const names = (this.hostGroups || []).map(g => g.name).filter(Boolean);
      // Also collapse the "ungrouped" bucket so the operator can fully
      // minimise the whole view. "" is the stable key for that bucket.
      names.push('');
      this.hostGroupsCollapsed = names;
      try {
        localStorage.setItem(
          'hostGroupsCollapsed',
          JSON.stringify(this.hostGroupsCollapsed),
        );
      } catch {}
    },
    // Build the grouped view: iterate the (already filtered+sorted)
    // host list, bucket each into the first group whose range covers
    // its custom_number. Anything unmatched lands in the trailing
    // ungrouped bucket. Groups are rendered in `order` order; the
    // ungrouped bucket always comes last.
    // Build the nested {parent → children → hosts} structure the
    // Hosts view renders. 2-level nesting only: a group with
    // `parent_name` set is a SUB-GROUP of that named top-level
    // group. Most-specific-match-wins: a host whose custom_number
    // falls inside a sub-group's range lands under that sub-group,
    // NOT under the parent (even though the parent's range also
    // contains it). Hosts that match a top-level group directly
    // but no sub-group appear in the parent's own host list.
    //
    // Shape:
    //   [
    //     { group: {...top-level...}, hosts: [h, h],
    //       children: [
    //         { group: {...sub-group...}, hosts: [h, h] },
    //       ],
    //     },
    //     { group: null, hosts: [h, h] }   // Ungrouped, trailing
    //   ]
    // Memoised result. ENH-008 — `groupedHosts()` is called on
    // every Alpine re-render. With 500 hosts × 30 groups the inner
    // O(N×M) walk is 15k comparisons per tick. Cache keyed on the
    // identities of the source arrays + the host list's length and the
    // groups list's length + a counter that increments on group save
    // (`hostGroupsRevision`) so a save explicitly busts even when the
    // length is unchanged.
    _groupedHostsCache: { key: '', value: null },
    hostGroupsRevision: 0,
    groupedHosts() {
      const hosts = this.filteredHosts();
      const groups = this.hostGroups || [];
      const cacheKey = hosts.length + '|' + groups.length + '|' + (this.hostGroupsRevision || 0)
        + '|' + (this.hostsFilter || '') + '|' + (this.hideUnconfiguredHosts ? '1' : '0');
      const cached = this._groupedHostsCache;
      if (cached.key === cacheKey && cached.value) return cached.value;

      const all = groups.slice().sort(
        (a, b) => (a.order || 0) - (b.order || 0) || a.name.localeCompare(b.name),
      );
      const topLevel = all.filter(g => !g.parent_name);
      const subByParent = new Map();
      for (const g of all) {
        if (!g.parent_name) continue;
        if (!subByParent.has(g.parent_name)) subByParent.set(g.parent_name, []);
        subByParent.get(g.parent_name).push(g);
      }
      const buckets = topLevel.map(g => ({
        group: g,
        hosts: [],
        children: (subByParent.get(g.name) || []).map(sg => ({
          group: sg, hosts: [],
        })),
      }));
      const ungrouped = { group: null, hosts: [], children: [] };

      // Sorted-by-range-start index for binary search. Each entry is
      // `[range_start, range_end, bucketIdx]`. With 30 groups the
      // bisection over a sorted array is O(log N) per host vs the
      // naive O(N) linear scan; saves ~14k comparisons per render
      // with 500 hosts × 30 groups (#423 / ENH-008).
      const ranges = buckets
        .map((b, idx) => [b.group.range_start | 0, b.group.range_end | 0, idx])
        .sort((a, b) => a[0] - b[0]);
      const findBucket = (ci) => {
        // Binary search for the largest range whose start ≤ ci, then
        // walk back through any equal-start entries to find one whose
        // end ≥ ci. Falls back to a linear scan for overlapping ranges
        // (operator-defined ranges CAN overlap; first-match-wins
        // matches the prior implementation's iteration order). With
        // typical non-overlapping range sets the early-exit is hit on
        // the bisected entry without any walk.
        let lo = 0, hi = ranges.length;
        while (lo < hi) {
          const mid = (lo + hi) >>> 1;
          if (ranges[mid][0] <= ci) lo = mid + 1; else hi = mid;
        }
        // After the loop `lo` is the count of ranges with start ≤ ci.
        // Walk back through them looking for one whose end ≥ ci.
        for (let i = lo - 1; i >= 0; i--) {
          if (ranges[i][1] >= ci) return ranges[i][2];
        }
        return -1;
      };

      for (const h of hosts) {
        const cn = h.custom_number;
        const ci = (cn === null || cn === undefined || cn === '') ? null
          : (Number.isFinite(+cn) ? +cn : null);
        let placed = false;
        if (ci !== null) {
          const bIdx = findBucket(ci);
          if (bIdx >= 0) {
            const b = buckets[bIdx];
            // Parent matched — now try the sub-groups first
            // (most-specific wins). Break on the first sub-group
            // hit and DON'T also push to the parent's list. Children
            // tend to be a handful per parent so a linear scan here
            // is cheap (no need for a second bisection layer).
            let placedInChild = false;
            for (const c of b.children) {
              const cg = c.group;
              if (ci >= cg.range_start && ci <= cg.range_end) {
                c.hosts.push(h);
                placedInChild = true;
                break;
              }
            }
            if (!placedInChild) b.hosts.push(h);
            placed = true;
          }
        }
        if (!placed) ungrouped.hosts.push(h);
      }
      // Bucket filter — keep parents that have direct hosts OR a sub-group
      // with hosts. Empty parents (zero direct + zero contributing children)
      // are dropped. This matches the operator's preference: with "Hide
      // hosts without agents" ON, empty groups should DISAPPEAR (not show
      // headings of nothing). The previous `|| topLevel.length` clause was
      // a no-op (always truthy) — that's the bug that was keeping empty
      // top-level groups visible regardless of content.
      const out = buckets.filter(b =>
        b.hosts.length > 0
        || b.children.some(c => c.hosts.length > 0),
      );
      if (ungrouped.hosts.length > 0) out.push(ungrouped);
      cached.key = cacheKey;
      cached.value = out;
      return out;
    },

    // --- Asset inventory (ticket #78) ---
    async saveAssetSettings() {
      const body = {
        asset_inventory_auth_mode: (this.assetForm.auth_mode === 'lifetime_token')
                                     ? 'lifetime_token' : 'oauth2',
        asset_inventory_base_url:  (this.assetForm.base_url || '').trim(),
        asset_inventory_token_url: (this.assetForm.token_url || '').trim(),
        asset_inventory_client_id: (this.assetForm.client_id || '').trim(),
        asset_inventory_scope:     (this.assetForm.scope || '').trim(),
        asset_inventory_service:   (this.assetForm.service || '').trim(),
        asset_inventory_action:    (this.assetForm.action || '').trim(),
        asset_inventory_min_value: String(this.assetForm.min_value ?? '').trim(),
        asset_inventory_max_value: String(this.assetForm.max_value ?? '').trim(),
        asset_inventory_edit_url_template: String(this.assetForm.edit_url_template ?? '').trim(),
        asset_inventory_verify_tls: !!this.assetForm.verify_tls,
      };
      if (this.assetForm.client_secret && this.assetForm.client_secret.trim()) {
        body.asset_inventory_client_secret = this.assetForm.client_secret;
      }
      if (this.assetForm.lifetime_token && this.assetForm.lifetime_token.trim()) {
        body.asset_inventory_lifetime_token = this.assetForm.lifetime_token.trim();
      }
      this.assetSaving = true;
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        this.showToast(this.t('admin_assets.saved'), 'success');
        await this.loadSettings();
        this.assetTestResult = null;
      } catch (e) {
        this.showToast(this.t('admin_assets.save_failed') + ': ' + e.message, 'error');
      } finally {
        this.assetSaving = false;
      }
    },
    async clearAssetClientSecret() {
      try {
        const ok = await (window.Swal ? Swal.fire({
          icon: 'warning',
          title: this.t('admin_assets.clear_secret_title'),
          text: this.t('admin_assets.clear_secret_text'),
          showCancelButton: true,
          confirmButtonText: this.t('actions.confirm'),
          cancelButtonText:  this.t('actions.cancel'),
        }).then(r => !!r.isConfirmed) : confirm(this.t('toasts_extra.asset_clear_secret_prompt')));
        if (!ok) return;
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ clear_asset_inventory_client_secret: true }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        await this.loadSettings();
        this.showToast(this.t('admin_assets.secret_cleared'), 'success');
      } catch (e) {
        this.showToast(this.t('admin_assets.save_failed') + ': ' + e.message, 'error');
      }
    },
    async clearAssetLifetimeToken() {
      try {
        const ok = await (window.Swal ? Swal.fire({
          icon: 'warning',
          title: this.t('admin_assets.clear_lifetime_token_title'),
          text:  this.t('admin_assets.clear_lifetime_token_text'),
          showCancelButton: true,
          confirmButtonText: this.t('actions.confirm'),
          cancelButtonText:  this.t('actions.cancel'),
        }).then(r => !!r.isConfirmed) : confirm(this.t('toasts_extra.asset_clear_token_prompt')));
        if (!ok) return;
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ clear_asset_inventory_lifetime_token: true }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        await this.loadSettings();
        this.showToast(this.t('admin_assets.lifetime_token_cleared'), 'success');
      } catch (e) {
        this.showToast(this.t('admin_assets.save_failed') + ': ' + e.message, 'error');
      }
    },
    async testAssetConnection() {
      this.assetTestResult = { pending: true };
      // Snapshot the form NOW so we can stamp it on success — same
      // pattern as `testPortainerConnection` / `testOidcConnection`.
      const probedSnapshot = this._assetSnapshot();
      const mode = (this.assetForm.auth_mode === 'lifetime_token')
                     ? 'lifetime_token' : 'oauth2';
      // send the in-flight verify_tls so admins can flip the
      // form's checkbox OFF and Test a self-signed asset API before
      // saving (mirrors testOidcConnection's shape).
      const body = { auth_mode: mode, verify_tls: !!this.assetForm.verify_tls };
      if (mode === 'lifetime_token') {
        body.base_url       = (this.assetForm.base_url || '').trim();
        body.lifetime_token = this.assetForm.lifetime_token || '';
        body.service        = (this.assetForm.service || '').trim();
        body.action         = (this.assetForm.action  || '').trim();
        body.min_value      = String(this.assetForm.min_value ?? '').trim();
        body.max_value      = String(this.assetForm.max_value ?? '').trim();
      } else {
        body.token_url     = (this.assetForm.token_url || '').trim();
        body.client_id     = (this.assetForm.client_id || '').trim();
        body.scope         = (this.assetForm.scope || '').trim();
        body.client_secret = this.assetForm.client_secret || '';
      }
      try {
        const r = await fetch('/api/asset-inventory/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const j = await r.json().catch(() => ({}));
        // Prefer the localized catalog message when the backend sent
        // a code; the raw `detail` still wins when it's a specific
        // upstream message (e.g. the upstream's own `details` field).
        const detail = j && j.error_code
          ? this.formatError({ error: j.detail, error_code: j.error_code, error_params: j.error_params }, j.detail)
          : (j.detail || '');
        this.assetTestResult = { ok: !!j.ok, detail };
        if (j && j.ok) {
          this._assetLastPassedTest = probedSnapshot;
        }
      } catch (e) {
        this.assetTestResult = { ok: false, detail: this.t('toasts.network_error') };
      }
    },
    async loadAssetCache() {
      try {
        const r = await fetch('/api/asset-inventory');
        if (r.ok) {
          this.assetCache = await r.json();
        } else {
          this.assetCache = { ok: false, error: `HTTP ${r.status}`, assets: [] };
        }
      } catch (e) {
        this.assetCache = { ok: false, error: String(e), assets: [] };
      }
    },
    // Look up an asset by the host's custom_number. Walks the cached
    // asset list each call — N is small (tens of hosts) so a linear
    // scan avoids the staleness risk of a memoised index when the
    // cache reloads. Returns `null` when no match or no cache.
    //
    // Field-name strategy: <asset-api-host>'s MDI ships CamelCase + nested
    // (`Brand: {Name}`, `Location: {CalculatedName}`, `Type: {Name}`,
    // `SerialNumber`, `Model`, `CustomNumber`, `Hostname` (CSV string),
    // `Interfaces[].IP`). We also accept the snake_case aliases
    // (`vendor`/`manufacturer`/`model`/`serial`/`location`/`custom_number`)
    // so a generic non-MDI upstream still works without a schema map.
    assetForHost(h) {
      if (!h) return null;
      // Backend-provided shape (`/api/hosts*` injects `asset` keyed
      // by custom_number, see `_resolve_asset_for_host` in main.py).
      // When present we use it directly — works even before the
      // client-side asset cache has loaded, AND for hosts with no
      // agents configured (the live providers never populated, but
      // the asset shape is still attached). Spread into a fresh
      // object so the legacy `_raw` field exists for the debug
      // panel even on the no-cache path.
      if (h.asset && typeof h.asset === 'object') {
        return Object.assign({ _raw: null }, h.asset);
      }
      if (h.custom_number == null || h.custom_number === '') return null;
      const assets = (this.assetCache && Array.isArray(this.assetCache.assets))
        ? this.assetCache.assets : null;
      if (!assets || !assets.length) return null;
      const n = parseInt(h.custom_number, 10);
      if (!Number.isFinite(n)) return null;
      // Walk-helper: accepts a string OR a {Name}/{CalculatedName}
      // dict and returns the best display string. Catches both flat
      // and nested upstream shapes in one call.
      const pick = (...candidates) => {
        for (const v of candidates) {
          if (v == null) continue;
          if (typeof v === 'string' && v.trim()) return v.trim();
          if (typeof v === 'object') {
            const s = v.CalculatedName || v.Name || v.name || '';
            if (typeof s === 'string' && s.trim()) return s.trim();
          }
        }
        return '';
      };
      for (const a of assets) {
        if (!a) continue;
        const candidate = a.CustomNumber ?? a.custom_number ?? a.number ?? a.id;
        if (parseInt(candidate, 10) !== n) continue;
        // Hostname CSV → array of FQDNs.
        const hostnameStr = String(a.Hostname || a.hostname || '').trim();
        const hostnames = hostnameStr ? hostnameStr.split(',').map(s => s.trim()).filter(Boolean) : [];
        // Interfaces — ordered by `Number` (then `Name`) so the
        // drawer renders them in the operator's intended order
        // rather than insertion order.
        const ifacesRaw = Array.isArray(a.Interfaces) ? a.Interfaces : (a.interfaces || []);
        const ifaces = ifacesRaw.slice().sort((x, y) => {
          const xn = (x && x.Number != null) ? x.Number : Infinity;
          const yn = (y && y.Number != null) ? y.Number : Infinity;
          if (xn !== yn) return xn - yn;
          return String((x && x.Name) || '').localeCompare(String((y && y.Name) || ''));
        }).map(i => ({
          name:    String((i && (i.Name || i.name)) || '').trim(),
          ip:      String((i && (i.IP   || i.ip))   || '').trim(),
          mac:     String((i && (i.MacAddress || i.mac_address)) || '').trim(),
          number:  (i && i.Number != null) ? i.Number : null,
          comment: String((i && i.Comment) || '').trim(),
          enabled: !i || i.IsEnabled !== false,
          ip_version: String((i && (i.IPVersion || i.ip_version)) || '').trim(),
        }));
        // Primary IP — first enabled iface, then any iface, then a
        // flat `ip` alias on the asset row.
        let primaryIp = '';
        if (ifaces.length) {
          const enabled = ifaces.find(i => i.enabled && i.ip);
          const any = enabled || ifaces.find(i => i.ip);
          if (any) primaryIp = any.ip;
        }
        if (!primaryIp) primaryIp = String(a.ip || '').trim();
        // Ports — flatten the {Port: {...}} nesting MDI uses so the
        // template can read port.name / port.number / port.service_name
        // directly. ServiceName in MDI doubles as a clickable URL
        // when it starts with http(s); we pass it through unchanged.
        const portsRaw = Array.isArray(a.Ports) ? a.Ports : (a.ports || []);
        const ports = portsRaw.map(p => {
          const inner = (p && (p.Port || p.port)) || {};
          return {
            id:           p && (p.ID || p.id),
            name:         String(inner.Name || inner.name || '').trim(),
            number:       (inner.Port != null) ? inner.Port : (inner.port != null ? inner.port : null),
            service_name: String(inner.ServiceName || inner.service_name || '').trim(),
            protocol:     String(inner.Protocol || inner.protocol || '').trim(),
          };
        }).filter(p => p.name || p.number != null);
        // Optional sub-fields from the guide's nested objects:
        //   - Brand.Link     — vendor URL, clickable from the drawer
        //   - Status.Color   — #RRGGBB used to tint the status pill
        //   - Location.Details — extra free-text address / detail
        const brandObj = (a.Brand && typeof a.Brand === 'object') ? a.Brand : null;
        const statusObj = (a.Status && typeof a.Status === 'object') ? a.Status : null;
        const locObj = (a.Location && typeof a.Location === 'object') ? a.Location : null;
        return {
          id:        a.ID ?? a.id ?? null,
          vendor:    pick(a.Brand, a.brand, a.vendor, a.manufacturer),
          brand_link: brandObj ? String(brandObj.Link || brandObj.link || '').trim() : '',
          model:     pick(a.Model, a.model, a.product, a.product_name),
          // Serial — placeholder values like "NONE224" / "NONE100" /
          // "NONEXXX" mean "no real serial recorded upstream"
          // (typically a VM that doesn't have a hardware serial).
          // Return empty so the existing `x-if="assetForHost(h).serial"`
          // gate hides the row entirely instead of surfacing a
          // misleading placeholder string.
          serial:    (() => {
            const s = pick(a.SerialNumber, a.serial, a.serial_number);
            return /^NONE\d*$/i.test(s) ? '' : s;
          })(),
          location:  pick(a.Location, a.location, a.site, a.room),
          location_details: locObj ? String(locObj.Details || locObj.details || '').trim() : '',
          type:      pick(a.Type, a.type),
          // Type SHORT-form — render a compact `[VM]` / `[PHY]` / etc.
          // badge when the upstream Type object carries any short-form
          // alias. <asset-api-host>'s payloads have surfaced multiple casings
          // for this field across asset rows ("Virtual Machine" with
          // shortname "VM", "Physical Server" with code "PHY"), so we
          // walk every plausible naming the team has used. Returns ''
          // when only the long Name is present, which lets
          // hostTypePrefix fall back to that long form via `type`.
          type_short: (() => {
            const obj = (a.Type && typeof a.Type === 'object') ? a.Type
                      : (a.type && typeof a.type === 'object') ? a.type
                      : null;
            if (!obj) return '';
            // Cast a wide net — match every casing variant + every
            // synonym for "short form" we've seen on this kind of
            // payload. First non-blank wins.
            const candidates = [
              obj.shortname, obj.ShortName, obj.SHORTNAME,
              obj.short_name, obj.Shortname, obj.shortName,
              obj.short, obj.Short, obj.SHORT,
              obj.code, obj.Code, obj.CODE,
              obj.abbr, obj.Abbr, obj.ABBR,
              obj.abbreviation, obj.Abbreviation,
              obj.acronym, obj.Acronym, obj.ACRONYM,
              obj.symbol, obj.Symbol,
              obj.tag, obj.Tag, obj.TAG,
              obj.slug, obj.Slug, obj.SLUG,
              obj.alias, obj.Alias,
            ];
            for (const v of candidates) {
              if (v == null) continue;
              const s = String(v).trim();
              if (s) return s;
            }
            return '';
          })(),
          name:      pick(a.Name, a.name),
          hostnames,
          primary_ip: primaryIp,
          ram:       pick(a.RAM, a.ram, a.memory),
          sku:       pick(a.SKU, a.sku),
          firmware:  pick(a.Firmware, a.firmware),
          hardware_version: pick(a.HardwareVersion, a.hardware_version),
          barcode:   pick(a.Barcode, a.barcode),
          comment:   pick(a.Comment, a.comment),
          status_name: pick(a.Status, a.status),
          status_color: statusObj ? String(statusObj.Color || statusObj.color || '').trim() : '',
          // Server emits "Y-m-d H:i:s" strings in its local timezone.
          // The drawer renders them with `fmtAssetDateString` (separate
          // helper since the value is a string, not an epoch).
          last_modified: String(a.LastModifiedOn || a.last_modified_on || '').trim(),
          created_on:    String(a.CreatedOn || a.created_on || '').trim(),
          interfaces: ifaces,
          ports,
          _raw:      a,
        };
      }
      return null;
    },
    // Bracketed type prefix for the Hosts-view host title — renders
    // as "[VM]" / "[PHY]" / "[CT]" before the display label so
    // operators can scan a long list and tell physical / virtual /
    // container hosts apart at a glance. Resolution: prefer the
    // asset's `Type.shortname` (compact 2-3 char code), fall back to
    // the long `Type.Name`, return '' when no asset / no type. Empty
    // result skips the prefix entirely so non-asset hosts stay clean.
    //
    // One-time debug: if we have a Type object with a long `type` but
    // Resolve the human-visible display name for a host row, used by
    // the Hosts grid + drawer header + every "open this host" toast.
    // Resolution order :
    //   1. Operator-set `h.label` from Admin → Hosts (highest priority)
    //   2. Asset inventory's `name` (asset.Name / asset.CalculatedName
    //      via `assetForHost(h).name`) — lets operators leave the
    //      display label blank and inherit a meaningful name from the
    //      asset record without typing it twice
    //   3. The Docker hostname `h.host`
    //   4. The curated row id `h.id` (last-resort)
    // Anywhere the UI used `h.label || h.host` it should call this
    // helper instead so the asset-fallback applies consistently.
    hostDisplayName(h) {
      if (!h) return '';
      const op = (h.label || '').toString().trim();
      if (op) return op;
      const asset = (typeof this.assetForHost === 'function') ? this.assetForHost(h) : null;
      const an = (asset && asset.name) ? String(asset.name).trim() : '';
      if (an) return an;
      return String(h.host || h.id || '').trim();
    },
    // no `type_short`, log the available keys ONCE so the operator can
    // tell us the correct upstream field name. The set is process-wide
    // so we don't flood the console — first asset that misses logs.
    hostTypePrefix(h) {
      const a = this.assetForHost(h);
      if (!a) return '';
      // Diagnostic — fires once per asset id when type_short is empty
      // but type IS present, suggesting the upstream Type object uses
      // a field name we don't recognise yet.
      if (!a.type_short && a.type && a._raw && a._raw.Type
          && typeof a._raw.Type === 'object') {
        if (!this._loggedMissingTypeShort) this._loggedMissingTypeShort = new Set();
        const aid = String(a.id || a.type || '');
        if (!this._loggedMissingTypeShort.has(aid)) {
          this._loggedMissingTypeShort.add(aid);
          // eslint-disable-next-line no-console
          console.info(
            '[asset] type has no recognised short-name field; available keys:',
            Object.keys(a._raw.Type || {}),
            '— type:', a.type,
            '— full Type object:', a._raw.Type,
            '— falling back to derived acronym.',
          );
        }
      }
      // Source of truth is the asset's `ShortName` (<asset-api-host> MDI
      // §5.2.4, exposed by `shape_asset` as `type_short`). When the
      // operator hasn't set a ShortName upstream, fall back to the
      // long Type.Name verbatim — never invent an abbreviation,
      // since the operator's asset record is authoritative.
      const label = (a.type_short || a.type || '').trim();
      return label ? '[' + label + '] ' : '';
    },
    // Raw asset row from the cached snapshot — for the debug panel.
    // The shaped resolver (`assetForHost`) drops fields the drawer
    // doesn't render; this returns the unfiltered upstream object.
    rawAssetForHost(h) {
      if (!h || h.custom_number == null || h.custom_number === '') return null;
      const assets = (this.assetCache && Array.isArray(this.assetCache.assets))
        ? this.assetCache.assets : null;
      if (!assets || !assets.length) return null;
      const n = parseInt(h.custom_number, 10);
      if (!Number.isFinite(n)) return null;
      for (const a of assets) {
        if (!a) continue;
        const cn = a.CustomNumber ?? a.custom_number ?? a.number ?? a.id;
        if (parseInt(cn, 10) === n) return a;
      }
      return null;
    },
    // Edit-on-upstream URL for the asset. Resolution order:
    //   1. `asset_inventory.edit_url_template` from settings — the
    //      operator-configured prefix or template. If it contains
    //      a `{id}` placeholder we substitute; otherwise we append
    //      the asset id to the end (so a bare prefix like
    //      "https://<asset-api-host>/admin/pages/assets/asset_management.php?s=edit&si="
    //      produces "...&si=42" with no extra plumbing).
    //   2. Fallback: derive from `assetCache.upstream` by stripping
    //      `/api` and appending `?asset=<id>` — a reasonable guess
    //      that may not work for every deployment.
    //   3. Empty string when no upstream / template is known.
    assetEditUrl(asset) {
      if (!asset || asset.id == null) return '';
      const tpl = ((this.assetStatus && this.assetStatus.edit_url_template) || '').trim();
      if (tpl) {
        if (tpl.includes('{id}') || tpl.includes('{custom_number}') || tpl.includes('{base}')) {
          const upstream = (this.assetCache && this.assetCache.upstream) || '';
          return tpl
            .replace('{id}', String(asset.id))
            .replace('{custom_number}', String(asset._raw && asset._raw.CustomNumber || ''))
            .replace('{base}', upstream);
        }
        // Bare prefix — append the id at the end.
        return tpl + String(asset.id);
      }
      const upstream = (this.assetCache && this.assetCache.upstream) || '';
      if (!upstream) return '';
      const adminBase = upstream.replace(/\/api\/?$/, '');
      return `${adminBase}?asset=${asset.id}`;
    },
    // Resolve a clickable URL for a port chip. Three cases:
    //   1. The port's `service_name` is already an http(s):// URL —
    //      use it as-is (operator-curated full URL).
    //   2. The port's `name` or `protocol` indicates HTTP / HTTPS —
    //      synthesize a URL from the host's FQDN. Picks the first
    //      hostname from the asset row, falling back to `h.host`,
    //      then `h.label`. Skips the explicit `:port` when it's the
    //      protocol's default (80 / 443) so the URL stays clean.
    //   3. Anything else — empty string (renders as a plain chip).
    assetPortServiceUrl(port, h) {
      const s = String((port && port.service_name) || '').trim();
      if (/^https?:\/\//i.test(s)) return s;
      // Substring match on name + protocol — catches obvious HTTP
      // ports ("HTTP", "HTTPS") AND looser labels like "HTTP Admin"
      // / "NetData" (Protocol="HTTP") / "NGINX Admin" without a
      // service_name URL. HTTPS check runs first so a port labelled
      // "HTTPS" doesn't fall into the http bucket.
      const name = String((port && port.name) || '').toUpperCase();
      const proto = String((port && port.protocol) || '').toUpperCase();
      const haystack = name + ' ' + proto;
      const isHttps = haystack.includes('HTTPS');
      const isHttp  = !isHttps && haystack.includes('HTTP');
      // Protocol "IP" — use the host's raw IP (not its FQDN) for
      // the URL host part. Common for node-exporter / netdata
      // metric scrapers where DNS isn't reliable. Defaults to http
      // scheme since that's the typical raw-IP use case.
      const isIpProto = !isHttp && !isHttps && proto === 'IP';
      if (!isHttp && !isHttps && !isIpProto) return '';

      const asset = (h && this.assetForHost) ? this.assetForHost(h) : null;
      // Pick host (FQDN) and ip from the asset row + curated host.
      // The asset's `Hostname` CSV is ordered LEAST-specific → MOST-
      // specific by upstream convention (e.g. raw IP first, friendly
      // name last), so we pick the LAST entry — that's the canonical
      // FQDN the operator wants links to land on.
      let fqdn = '';
      if (asset && Array.isArray(asset.hostnames) && asset.hostnames.length) {
        fqdn = asset.hostnames[asset.hostnames.length - 1];
      }
      if (!fqdn && h) fqdn = String(h.host || h.id || h.label || '').trim();
      const ip = (asset && asset.primary_ip) || (h && h.ip) || '';

      // IP-protocol path uses raw IP and falls back to FQDN if no
      // IP is known (better something clickable than nothing).
      if (isIpProto) {
        const target = ip || fqdn;
        if (!target) return '';
        const num = (port && port.number != null) ? port.number : null;
        const portSuffix = (num != null) ? (':' + num) : '';
        return `http://${target}${portSuffix}`;
      }

      if (!fqdn) return '';
      const scheme = isHttps ? 'https' : 'http';
      const defaultPort = isHttps ? 443 : 80;
      const num = (port && port.number != null) ? port.number : null;
      const portSuffix = (num != null && num !== defaultPort) ? (':' + num) : '';
      return `${scheme}://${fqdn}${portSuffix}`;
    },
    async refreshAssetCache() {
      this.assetRefreshing = true;
      try {
        const r = await fetch('/api/asset-inventory/refresh', { method: 'POST' });
        const j = await r.json().catch(() => ({}));
        if (j && j.ok) {
          this.showToast(this.t('admin_assets.refresh_ok', { count: j.count || 0 }), 'success');
        } else {
          this.showToast(this.t('admin_assets.refresh_failed') + ': ' + this.formatError(j, 'unknown'), 'error');
        }
        await this.loadAssetCache();
      } catch (e) {
        this.showToast(this.t('admin_assets.refresh_failed') + ': ' + e.message, 'error');
      } finally {
        this.assetRefreshing = false;
      }
    },

    // --- Hosts view (Beszel-backed) ---
    // Two-phase loader (backend endpoints: /api/hosts/list + /api/hosts/one/{id}):
    //   1. /api/hosts/list — curated list + global state, no probes.
    //      Paints the table instantly with grey "…" status dots.
    //   2. Fan out /api/hosts/one/{id} per row (capped concurrency
    //      so a 30-host fleet doesn't flood the server with 30
    //      simultaneous Webmin+NE probes). Each response splices its
    //      row back into `this.hosts`, flipping _loading false and
    //      filling in stats. Alpine's proxy picks up the mutation.
    // IntersectionObserver-driven lazy fetch for host rows.
    // Called by each row's `x-init` (mobile + desktop templates) so
    // every mounted `[data-host-id]` element registers with a single
    // shared observer. On first intersection, the row's id lands in
    // `_hostSeenIds` and an immediate `refreshHostRow` fires to fill
    // the row's metrics. Re-observing the same element is a no-op
    // (IntersectionObserver dedupes internally). The observer's
    // `rootMargin` is generous (200px above + below) so rows about to
    // scroll into view start fetching slightly early — operators
    // don't see a "loading" flash mid-scroll.
    _ensureHostRowObserver() {
      if (this._hostRowObserver) return this._hostRowObserver;
      if (typeof IntersectionObserver === 'undefined') return null;
      // observer hits collect into a pending Set + debounce
      // by 200 ms before flushing. Rapid scroll past a long list
      // (e.g. 200-host fleet) coalesces into one queue load instead
      // of firing 50+ concurrent fetches in <500 ms (the prior
      // behaviour bypassed `tuning_hosts_parallel_fetch` entirely
      // because each observer entry called refreshHostRow directly).
      // The flush hands every queued id to `_runHostRefreshQueue`
      // which honours the SAME PARALLEL cap as loadHosts'
      // poll-driven fan-out.
      this._hostObserverPending = this._hostObserverPending || new Set();
      // Named method on `this` so SSE event handlers can also call
      // it directly to coalesce bursts through the SAME debounce +
      // shared worker pool. Pre-fix the flush was a closure-local
      // helper that the SSE path couldn't reach.
      this._scheduleHostObserverFlush = () => {
        if (this._hostObserverFlushTimer) clearTimeout(this._hostObserverFlushTimer);
        this._hostObserverFlushTimer = setTimeout(() => {
          this._hostObserverFlushTimer = null;
          const ids = [...(this._hostObserverPending || new Set())];
          this._hostObserverPending = new Set();
          if (!ids.length) return;
          for (const id of ids) this._hostSeenIds.add(id);
          this._runHostRefreshQueue(ids).catch(() => {});
          // Warm host history for visible rows so the inline 1h-trend
          // sparkline overlaid on each CPU / Mem / Disk stat-bar has
          // data to render without waiting for the operator to open
          // the drawer. One-shot per host — the loaders are no-ops
          // when history is already present and fresh. Off-screen
          // hosts never enter the observer's queue, so a 200-host
          // fleet doesn't pay the prefetch cost up-front.
          //
          // Two sources covered: (1) Beszel / NE history via
          // `loadHostHistory(beszel_id, host_id)` — feeds the
          // cpu / mp / dp series; (2) SNMP history via
          // `loadHostSnmpHistory(host_id)` for SNMP-monitored hosts
          // (switches / routers / printers) so those rows also get
          // CPU + Memory sparklines even when no Beszel agent / NE
          // exporter is configured.
          for (const id of ids) {
            const h = (this.hosts || []).find(x => x && x.id === id);
            if (!h) continue;
            // Beszel / NE / Pulse / Webmin prefetch. Gate accepts
            // either the post-probe `beszel_id` OR the curated
            // `beszel_name` (operator alias) — pre-fix the gate
            // required `beszel_id` which is only populated AFTER a
            // successful per-host /api/hosts/one/{id} probe lands.
            // For Beszel-only hosts that meant sparklines stayed
            // empty until the per-host probe completed, even though
            // the history time-series in `host_metrics_samples` was
            // already queryable. `loadHostHistory` itself accepts
            // an empty beszel_id and falls back to host_id-keyed
            // history (NE / Pulse / Webmin), so the broader gate is
            // safe.
            if (h.beszel_id || h.beszel_name || h.ne_url || h.pulse_name || h.webmin_name) {
              const key = this.hostHistoryKey(h);
              const cached = key && this.hostHistory && this.hostHistory[key];
              if (!cached
                  || !Array.isArray(cached.series)
                  || cached.series.length < 2) {
                try { this.loadHostHistory(h.beszel_id || '', h.id); } catch {}
              }
            }
            // SNMP prefetch — independent of Beszel / NE so a host with
            // ALL three providers gets both series loaded in parallel
            // and the helper picks whichever lands first.
            if (h.snmp_enabled && typeof this.loadHostSnmpHistory === 'function') {
              const snmpCached = this.hostSnmpHistory && this.hostSnmpHistory[h.id];
              if (!snmpCached
                  || !Array.isArray(snmpCached.points)
                  || snmpCached.points.length < 2) {
                try { this.loadHostSnmpHistory(h.id, 1); } catch {}
              }
            }
          }
        }, 200);
      };
      const handle = (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const id = entry.target && entry.target.getAttribute('data-host-id');
          if (!id) continue;
          if (this._hostSeenIds.has(id)) continue;
          this._hostObserverPending.add(id);
        }
        this._scheduleHostObserverFlush();
      };
      this._hostRowObserver = new IntersectionObserver(handle, {
        rootMargin: '200px 0px 200px 0px',
        threshold: 0,
      });
      return this._hostRowObserver;
    },
    observeHostRow(el, id) {
      if (!el || !id) return;
      const obs = this._ensureHostRowObserver();
      if (!obs) {
        // Browser without IntersectionObserver — fall back to eager
        // fetch so functionality is preserved (some old WebViews lack
        // IO). This path is unreachable on every modern browser.
        if (!this._hostSeenIds.has(id)) {
          this._hostSeenIds.add(id);
          this.refreshHostRow(id).catch(() => {});
        }
        return;
      }
      obs.observe(el);
    },
    // concurrency-capped queue runner shared between the IO
    // observer's debounced flush and loadHosts' poll-driven fan-out.
    // Resolves PARALLEL the same way loadHosts does (per-call read of
    // `me.client_config.hosts_parallel_fetch`, fallback 6) so an
    // operator's Admin → Config Save takes effect on the next call.
    async _runHostRefreshQueue(ids) {
      // Push every requested id onto the SHARED queue and spawn
      // workers only up to the cap. Pre-fix this had its own private
      // queue + pool independent of the polling-path pool, so a
      // burst of `_runHostRefreshQueue(...)` during an active
      // `loadHosts(...)` doubled the in-flight count beyond the cap.
      // Same anti-pattern applies to direct `refreshHostRow` calls
      // from SSE event handlers — those should also push here via
      // `_hostObserverPending.add(id) + scheduleFlush()` so every
      // path shares one cap.
      for (const id of (ids || [])) if (id) this._enqueueHostRefresh(id);
      await this._ensureHostRefreshWorkers();
    },
    // Shared queue + worker count used by ALL host-refresh call sites
    // (lazy IO observer, SSE event handlers, polling fan-out). Capped
    // by `me.client_config.hosts_parallel_fetch` regardless of caller.
    _hostRefreshQueue: null,
    _hostRefreshWorkerCount: 0,
    _enqueueHostRefresh(id) {
      this._hostRefreshQueue = this._hostRefreshQueue || [];
      // Cheap dedupe: skip if already queued. Uses indexOf since the
      // queue is bounded by host count; for fleets > 200 hosts a
      // Set-backed mirror would be a future optimisation.
      if (this._hostRefreshQueue.indexOf(id) === -1) {
        this._hostRefreshQueue.push(id);
      }
    },
    async _ensureHostRefreshWorkers() {
      const PARALLEL = (this.me && this.me.client_config
                        && this.me.client_config.hosts_parallel_fetch) || 6;
      const need = Math.max(0, PARALLEL - this._hostRefreshWorkerCount);
      if (!need) return;
      const queue = this._hostRefreshQueue || [];
      const slots = Math.min(need, queue.length);
      if (!slots) return;
      const worker = async () => {
        this._hostRefreshWorkerCount += 1;
        try {
          while (this._hostRefreshQueue && this._hostRefreshQueue.length) {
            const id = this._hostRefreshQueue.shift();
            if (!id) break;
            try { await this.refreshHostRow(id); }
            catch (_) { /* per-row failure stays isolated */ }
          }
        } finally {
          this._hostRefreshWorkerCount -= 1;
        }
      };
      const workers = [];
      for (let i = 0; i < slots; i++) workers.push(worker());
      await Promise.all(workers);
    },

    async loadHosts(force = false) {
      this.hostsLoading = true;
      try {
        // `force=true` bypasses the backend's 10s `_host_provider_cache`
        // memo so a settings save → next loadHosts immediately reflects
        // the new provider state (#367 / UX-001). Default polling path
        // stays cached.
        const url = force ? '/api/hosts/list?force=true' : '/api/hosts/list';
        const r = await fetch(url);
        if (!r.ok) {
          // Set the error flag but DON'T wipe the array — wholesale
          // replacement violates the in-place reconcile rule and
          // tears down every row's chart SVG on a transient HTTP
          // failure.
          // The next successful poll reconciles the existing rows in
          // place; until then, operators see the previous data with
          // a banner instead of a flicker-then-empty page.
          this.hostsError = `HTTP ${r.status}`;
          return;
        }
        const d = await r.json();
        this.hostsConfigured = !!d.configured;
        this.hostsError = d.error || '';
        this.hostsProviderErrors = d.provider_errors || {};
        this.hostsActiveSources = Array.isArray(d.active) ? d.active : [];
        // Background-refresh indicator. /api/hosts/list returns
        // `hub_probing: true` when it served snapshot rows instantly
        // and a fresh Beszel + Pulse hub probe is running in the
        // background. Drives the topbar refresh button's "Refreshing…"
        // pulse so the operator sees the system is working even
        // after the foreground call completed.
        this.hubProbing = !!d.hub_probing;
        // Merge with EXISTING rows to prevent the flicker that
        // happens when the 15s poll re-runs and resets every row to
        // the grey skeleton (hiding graphs / provider chips for a
        // second while refreshHostRow re-fetches). For each server
        // row, find the existing client row by id: if it had real
        // data, keep that data and just mark _loading=true (row stays
        // visually stable); if new, start from the skeleton.
        // Mutate in place to avoid wholesale array replacement —
        // replacing this.hosts causes Alpine to re-evaluate every
        // row's template, which re-computes chart SVGs and makes
        // graphs flicker every 15s poll. Instead, we:
        //   1. Find each existing row by id, UPDATE its fields in
        //      place (Alpine's proxy picks up each assignment so the
        //      DOM stays mounted).
        //   2. Append any new rows that don't exist yet.
        //   3. Remove any rows whose id disappeared from the response.
        const CURATED_FIELDS = [
          'label', 'icon', 'custom_number', 'url',
          'beszel_name', 'pulse_name', 'ne_url', 'webmin_name',
          'ssh_enabled', 'asset',
          // ping_enabled needs to flow through the skeleton
          // path so drawerHost.ping_enabled is truthy when the
          // operator first clicks a ping-enabled row. Otherwise the
          // openHostDrawer gate fails and loadHostPingHistory never
          // fires (chart stays "Collecting data…" forever).
          'ping_enabled',
          // SNMP target alias. Curated overlay so the per-host
          // chip in providerStates(h) renders correctly off the
          // skeleton row (before the per-host probe lands).
          // `snmp_enabled` (per-host opt-in flag) added so an
          // un-ticked save flips the chip off on the next refresh
          // instead of staying stuck on the previous `true` state.
          'snmp_name', 'snmp_enabled',
        ];
        const incoming = Array.isArray(d.hosts) ? d.hosts : [];
        const incomingIds = new Set(incoming.map(h => h.id));
        // A host with NO providers mapped (or running in a deploy with
        // every host-stats source disabled globally) has nothing to
        // probe — the backend already stamps such rows with
        // `status: 'unconfigured'` from `_shape_host_api_row`. Skipping
        // the per-host fetch here means: (a) the dot lands directly
        // on grey with no transient "loading" flash, (b) we don't
        // burn a /api/hosts/one/{id} round-trip for every dead row.
        const isUnconfigured = (h) => h && h.status === 'unconfigured';
        // 1+2: reconcile — update existing, append new.
        for (let i = 0; i < incoming.length; i++) {
          const h = incoming[i];
          const existing = (this.hosts || []).find(r => r.id === h.id);
          const skipProbe = isUnconfigured(h);
          if (existing) {
            // Existing row — overlay curated fields only. Deliberately
            // DO NOT flip `_loading` back to true on re-polls: the
            // previous data stays visible while refreshHostRow patches
            // stats in place, which is the entire point of the in-
            // place reconcile. Toggling `_loading = true` here caused
            // the status-dot template to flash from dot → spinner →
            // dot on every 15s poll cycle (most visible on paused /
            // down hosts whose red dot was the visual anchor).
            for (const k of CURATED_FIELDS) {
              if (k in h) existing[k] = h[k];
            }
            existing._seq = i;
            if (skipProbe) {
              existing.status = 'unconfigured';
              // Unconfigured rows skip the per-host probe entirely, so
              // make sure they aren't stuck on a stale spinner from a
              // previous configured-then-unmapped state.
              if (existing._loading) existing._loading = false;
            }
          } else {
            // Brand-new row — push the full skeleton so it renders.
            // Respect the backend's status when it's already populated —
            // /api/hosts/list now promotes status='up' on cold-load
            // when the snapshot fallback restored host_* runtime
            // fields (the _stale_fields branch in
            // _shape_host_api_row). Pre-fix this branch unconditionally
            // forced status='loading' for every new row, stomping the
            // backend's 'up' with the loading sentinel and hiding the
            // CPU / Mem / Disk bars (their gates require
            // h.status === 'up'). Now we keep whatever the backend
            // sent unless the row is unconfigured (no providers mapped
            // OR every provider disabled globally) or the backend left
            // status blank (legacy responses + first-ever boot with no
            // snapshot, where 'loading' is still the right initial
            // sentinel).
            this.hosts.push({
              ...h,
              _seq: i,
              _loading: !skipProbe,
              status: skipProbe ? 'unconfigured' : (h.status || 'loading'),
            });
          }
        }
        // 3: drop rows whose id is no longer present.
        for (let i = this.hosts.length - 1; i >= 0; i--) {
          if (!incomingIds.has(this.hosts[i].id)) {
            this.hosts.splice(i, 1);
          }
        }
        this.hostsCuratedCount = Number.isFinite(d.curated_count) ? d.curated_count : 0;
        this.hostsEnabledCount = Number.isFinite(d.enabled_count) ? d.enabled_count : 0;
        // Trim persisted expansion state to hosts that actually exist.
        const valid = new Set(this.hosts.map(h => h.host));
        const cleaned = (this.hostsExpanded || []).filter(n => valid.has(n));
        if (cleaned.length !== (this.hostsExpanded || []).length) {
          this.hostsExpanded = cleaned;
        }
        // Trim the bulk-selection set to hosts that still exist —
        // operator-deleted rows would otherwise stay counted in
        // selectedHostCount() and make the bulk bar render a stale
        // count badge until the operator clicks Clear. Bulk POSTs
        // would silently skip the dead id but the count miscount is
        // still confusing UX.
        if (this.selectedHosts && this.selectedHosts.size > 0) {
          let trimmed = 0;
          const next = new Set();
          for (const id of this.selectedHosts) {
            if (incomingIds.has(id)) next.add(id);
            else trimmed += 1;
          }
          if (trimmed > 0) {
            this.selectedHosts = next;
          }
        }
      } catch (e) {
        // Set the error flag but DON'T wipe the array — same rationale
        // as the HTTP-error branch above (BUG-008). A transient
        // network blip during the 15s poll shouldn't tear down every
        // row's mounted DOM; the next successful poll reconciles
        // in place. Operators see a banner with the existing rows
        // dimmed (visually) by the error chip instead of a flicker.
        this.hostsError = `Network: ${e.message}`;
        return;
      } finally {
        this.hostsLoading = false;
        // First /api/hosts/list response landed (success OR error path).
        // Empty-state ladder is now allowed to render — see hostsInitialLoaded
        // gate in static/index.html.
        this.hostsInitialLoaded = true;
      }

      // Fan out per-host fetches. Concurrency cap prevents a 30-host
      // fleet from opening 30 sockets at once (Webmin probes in
      // particular hold connections for several seconds).
      // Unconfigured hosts (no providers mapped or all providers
      // disabled globally) are skipped — refreshHostRow would just
      // re-run the same backend logic that already stamped them
      // unconfigured in the LIST response. Saves a round trip per
      // dead row and prevents the dot from flashing yellow→grey.
      // for fleets larger than ~50 hosts the auto fan-out was
      // a perf cliff: 200 rows × per-probe time burned background
      // CPU + sockets even for off-screen rows the operator never
      // looks at. Filter the fan-out to hosts that the
      // IntersectionObserver-driven lazy fetcher has already
      // marked as "seen in viewport" (via `_hostSeenIds`). On first
      // load the set is empty, so this loop is a no-op; the observer
      // fires for in-viewport rows during initial paint and triggers
      // their first fetch directly. Subsequent 15s polls re-fetch
      // every previously-seen row to keep them fresh while the user
      // is on the page; off-screen rows that have never been viewed
      // never pay the probe cost. Saves dramatic bandwidth +
      // backend load on 200-host fleets.
      // Cleanup: drop seen-ids whose hosts have disappeared.
      const _validIds = new Set(this.hosts.map(h => h.id));
      this._hostSeenIds = new Set(
        [...(this._hostSeenIds || [])].filter(id => _validIds.has(id))
      );
      // Hand the queue to the SHARED worker pool — single source of
      // concurrency truth across polling, IO observer, and SSE event
      // handlers. Pre-fix this had its own worker pool independent
      // of `_runHostRefreshQueue`'s, so combined fan-out could
      // exceed the operator-set cap when both paths fired in the
      // same window.
      const queueIds = this.hosts
        .filter(h => h.status !== 'unconfigured' && this._hostSeenIds.has(h.id))
        .map(h => h.id);
      for (const id of queueIds) this._enqueueHostRefresh(id);
      // Fire-and-forget — page paints as workers complete; history
      // pre-fetch (below) runs in parallel with the per-host stat
      // fetches.
      this._ensureHostRefreshWorkers();
      // Don't await workers — page paints as they complete. But fire
      // the history pre-fetch for pre-expanded hosts right away so
      // the drawer chart populates in parallel with the per-host
      // stat fetches.
      for (const name of this.hostsExpanded || []) {
        const host = this.hosts.find(h => h.host === name);
        if (!host) continue;
        const key = this.hostHistoryKey(host);
        // Fire on Beszel-mapped hosts OR NE-only hosts (ne_url set).
        // Skipping NE-only hosts here was the bug that left the new
        // historical-charts path unreachable from the bulk-expand
        // entry point.
        if (key && (host.beszel_id || host.ne_url || host.pulse_name || host.webmin_name) && !this.hostHistory[key]) {
          this.loadHostHistory(host.beszel_id || '', host.id);
        }
      }
      // Inline-sparkline backfill — every visible host that has been
      // probed at least once (in `_hostSeenIds`) and has Beszel or NE
      // telemetry but no usable history yet gets a one-shot history
      // fetch on every poll cycle. The IntersectionObserver-driven
      // initial prefetch fires only on first-intersect; if that first
      // call returned an empty series (e.g. Beszel hub momentarily
      // unreachable at page load) the row's sparkline would never
      // populate without this retry. Skip when the cache already has
      // ≥2 points so we don't re-fetch every 15s for hosts whose
      // history is fresh. Same SNMP retry layered alongside.
      for (const id of (this._hostSeenIds || [])) {
        const host = this.hosts.find(h => h.id === id);
        if (!host) continue;
        if (host.beszel_id || host.ne_url || host.pulse_name || host.webmin_name) {
          const key = this.hostHistoryKey(host);
          const cached = key && this.hostHistory && this.hostHistory[key];
          if (!cached
              || !Array.isArray(cached.series)
              || cached.series.length < 2) {
            try { this.loadHostHistory(host.beszel_id || '', host.id); } catch {}
          }
        }
        if (host.snmp_enabled && typeof this.loadHostSnmpHistory === 'function') {
          const snmpCached = this.hostSnmpHistory && this.hostSnmpHistory[host.id];
          if (!snmpCached
              || !Array.isArray(snmpCached.points)
              || snmpCached.points.length < 2) {
            try { this.loadHostSnmpHistory(host.id, 1); } catch {}
          }
        }
      }
      // Refresh the open drawer's chart history on every host-poll
      // tick (#363 followup). The `Updated Xs/m/h ago` freshness label
      // tracks the last successful chart fetch — without this hook
      // the label could read older than the user's host-poll cadence
      // because `loadHostHistory` was only invoked on drawer open
      // and on time-range button clicks. Skipped for the
      // `hostsExpanded` path above because that gate has a
      // `!this.hostHistory[key]` check (only fires the first time);
      // the drawer needs an UNCONDITIONAL re-fetch each tick so the
      // chart values + the freshness stamp stay in sync with the
      // host-list refresh interval.
      if (this.drawerHost
          && (this.drawerHost.beszel_id || this.drawerHost.ne_url || this.drawerHost.pulse_name || this.drawerHost.webmin_name)) {
        this.loadHostHistory(
          this.drawerHost.beszel_id || '',
          this.drawerHost.id,
        );
      }
    },

    // Fetch one host's merged stats and splice it back into the
    // hosts array. Preserves _seq and _loading handling so the UI
    // can distinguish "not yet loaded" from "probed but empty".
    async refreshHostRow(id, opts = {}) {
      // ``opts.force`` propagates to ``/api/hosts/one/{id}?force=true``.
      // Used after a Save in Admin → Hosts / Host stats so a
      // re-opened drawer sees fresh provider data without waiting out
      // the 10s provider-state cache. Default false keeps the polling
      // path cheap.
      //
      // 504 back-off — if /api/hosts/one returned 504 in the last
      // ``_hostRow504BackoffMs`` window for this host, skip the call
      // entirely. Stops the console-error spam loop where the SNMP
      // probe budget is exceeded for a slow host (iDRAC / large
      // switch) and the SPA's IntersectionObserver / drawer poll
      // hammers the same endpoint every 30s. Operator-initiated
      // calls (force=true) bypass the back-off so a refresh button
      // always tries.
      this._hostRow504BackoffMs = this._hostRow504BackoffMs || 60_000;
      this._hostRow504Until = this._hostRow504Until || {};
      const now = Date.now();
      if (!opts.force && this._hostRow504Until[id] && this._hostRow504Until[id] > now) {
        return;
      }
      try {
        const url = '/api/hosts/one/' + encodeURIComponent(id)
                  + (opts && opts.force ? '?force=true' : '');
        const r = await fetch(url);
        if (!r.ok) {
          // Per-host back-off on 504 specifically — the upstream probe
          // budget was exceeded (slow SNMP, big OID set). Mark the
          // host with `_probe_timeout: true` so the row UI can render
          // a "probe slow" badge instead of the generic unknown
          // status. 5xx is treated the same way; 4xx clears the
          // back-off (genuine config error, not a transient slowness).
          if (r.status === 504 || r.status === 502 || r.status === 503) {
            this._hostRow504Until[id] = now + this._hostRow504BackoffMs;
          }
          const row = this.hosts.find(h => h.id === id);
          if (row) {
            row._loading = false;
            row._probe_timeout = (r.status === 504);
            row._probe_error = `HTTP ${r.status}`;
            row.status = 'unknown';
            // Clear any lingering per-provider polling flags — the
            // response failed so no `provider_done` events are coming.
            row._polling = {};
          }
          return;
        }
        // Successful probe — clear any back-off marker so the next
        // tick polls normally.
        delete this._hostRow504Until[id];
        const { host } = await r.json();
        if (!host) return;
        const row = this.hosts.find(h => h.id === id);
        if (!row) return;
        // In-place update: copy every field from the new host dict
        // into the existing row. Alpine's proxy picks up each
        // assignment individually, so the host's :key hasn't
        // changed — the template DOESN'T re-render from scratch,
        // which means embedded chart SVGs and provider pill rows
        // stay mounted. No flicker.
        //
        // the original
        // ``for (k of Object.keys(host)) row[k] = host[k]`` loop only
        // ASSIGNED keys present in the incoming dict, never deleted
        // keys absent from it. So any backend-side omission (provider
        // returns empty `host_temperatures` mid-session, transient DB
        // error trims a key from the response, etc.) left the previous
        // value sticky on the row. Fix: a CURATED_REFRESH_FIELDS
        // whitelist of probe-derived keys gets explicitly written —
        // missing keys in `host` collapse to ``null`` so the row
        // clears cleanly. CURATED_FIELDS-style flow (config / asset)
        // still uses the simple assign loop for whatever else the
        // backend chose to include.
        for (const k of CURATED_REFRESH_FIELDS) {
          row[k] = (host[k] === undefined) ? null : host[k];
        }
        for (const k of Object.keys(host)) {
          if (!CURATED_REFRESH_FIELDS.has(k)) {
            row[k] = host[k];
          }
        }
        row._loading = false;
        row._probe_timeout = false;
        row._probe_error = null;
        // Per-provider polling map is now stale — the response landed
        // with the authoritative state. Drop any lingering flags so a
        // SSE `provider_done` that was lost in transit (rare —
        // replica restart mid-probe) doesn't keep the chip pulsing
        // forever. Future probes will re-populate via fresh
        // `host:provider_probing` events.
        row._polling = {};
        // History backfill after first probe lands. The
        // IntersectionObserver-driven prefetch fires when the row
        // FIRST scrolls into view — but at that moment the row is
        // still a skeleton (curated-fields-only from /api/hosts/list)
        // and its `beszel_id` / `ne_url` / `pulse_name` /
        // `webmin_name` aren't populated yet, so the observer's
        // history-loader gate fails and skips. Operator-reported:
        // sparklines were absent on initial page load and only
        // appeared after opening + closing the drawer (which has
        // its own loadHostHistory call). Now that the per-host
        // probe has landed and the provider fields are present,
        // kick off the history fetch immediately if the cache is
        // still empty. Same gate the observer + 15s poll uses.
        try {
          if (row.beszel_id || row.ne_url || row.pulse_name || row.webmin_name) {
            const histKey = this.hostHistoryKey(row);
            const cached = histKey && this.hostHistory && this.hostHistory[histKey];
            if (!cached
                || !Array.isArray(cached.series)
                || cached.series.length < 2) {
              this.loadHostHistory(row.beszel_id || '', row.id);
            }
          }
          // Same backfill for SNMP — operator reported on a Cisco
          // SG300-28P switch that the row CPU bar + sparkline only
          // appeared after drawer-open-then-close. Same root cause
          // as the unix-style providers above: IntersectionObserver
          // fires loadHostSnmpHistory on first scroll-into-view but
          // the row was still a skeleton (snmp_enabled flag from
          // /api/hosts/list arrives before the per-host probe lands
          // — visibility gates depend on history existing AND
          // having a non-zero point, which the observer's first
          // fire might not have populated yet). Drawer-open had its
          // own loadHostSnmpHistory call (line ~15394) so opening
          // the drawer bridged the gap. Now that the per-host
          // probe just landed and `snmp_enabled` is reliably set,
          // kick off the SNMP history fetch immediately if the
          // cache is still empty / sparse.
          if (row.snmp_enabled === true && typeof this.loadHostSnmpHistory === 'function') {
            const snmpCached = this.hostSnmpHistory && this.hostSnmpHistory[row.id];
            if (!snmpCached
                || !Array.isArray(snmpCached.points)
                || snmpCached.points.length < 2) {
              this.loadHostSnmpHistory(row.id, 1);
            }
          }
        } catch (_) { /* defensive — never block the probe path */ }
      } catch (_) {
        // Network failure — leave the row in skeleton state so the
        // next loadHosts cycle retries. Silent (no toast): on a big
        // fleet the spam would be worse than the missing data.
      }
    },
    // toggle a provider in the Hosts-toolbar filter set.
    // Multi-select OR semantics. The synthetic 'none' name filters
    // to hosts without ANY provider mapped (curated rows that exist
    // for inventory but have no live data source).
    toggleHostsProviderFilter(name) {
      if (!name) return;
      const set = new Set(this.hostsProviderFilter || []);
      if (set.has(name)) set.delete(name); else set.add(name);
      this.hostsProviderFilter = set;
      try {
        if (typeof sessionStorage !== 'undefined') {
          if (set.size) sessionStorage.setItem('hostsProviderFilter', [...set].join(','));
          else sessionStorage.removeItem('hostsProviderFilter');
        }
      } catch (_) { /* private mode / quota — ignore */ }
    },
    // Convenience: clear the provider filter ("All" pill click).
    clearHostsProviderFilter() {
      this.hostsProviderFilter = new Set();
      try {
        if (typeof sessionStorage !== 'undefined') {
          sessionStorage.removeItem('hostsProviderFilter');
        }
      } catch (_) { /* ignore */ }
    },
    // Whether `name` is currently in the active filter set.
    isHostsProviderFilterActive(name) {
      return !!(this.hostsProviderFilter && this.hostsProviderFilter.has(name));
    },
    // Count of curated rows that have NO provider field mapped.
    // Used by the synthetic 'none' chip to surface "how many
    // inventory-only hosts are sitting on the page right now?".
    hostsWithNoProviderCount() {
      return (this.hosts || []).filter(h => !this.hostHasAgent(h)).length;
    },
    // Status chip for a provider in the Hosts toolbar. Combines
    // "enabled in settings" with "actually returned data for at least
    // one host" so operators spot misconfigs fast.
    hostsProviderState(name) {
      const active = (this.hostsActiveSources || []).includes(name);
      const err = (this.hostsProviderErrors || {})[name];
      const matchCount = (this.hosts || [])
        .filter(h => (h.providers || []).includes(name)).length;
      if (!active && !err) {
        return { visible: false, cls: '', icon: '', title: '', styled: false };
      }
      // tooltip titles routed through i18n.
      if (err) {
        return {
          visible: true, cls: 'pill-error', icon: '✗',
          title: this.t('hosts_extra.provider_filter.title_error', { name, error: err }),
          styled: false,
        };
      }
      if (matchCount === 0) {
        return {
          visible: true, cls: 'pill-unknown', icon: '·',
          title: this.t('hosts_extra.provider_filter.title_unmatched', { name }),
          styled: false,
        };
      }
      // Healthy state — use the operator-customised provider colour
      // via `pill-custom` + `providerChipStyle()` (#621 follow-up).
      // The fixed `pill-ok` green ignored Settings → Providers colour
      // overrides; flip to pill-custom so the toolbar chip matches the
      // per-row chip's colouring.
      const titleKey = matchCount === 1
        ? 'hosts_extra.provider_filter.title_match_one'
        : 'hosts_extra.provider_filter.title_match_many';
      return {
        visible: true, cls: 'pill-custom', icon: '✓',
        title: this.t(titleKey, { name, count: matchCount }),
        styled: true,
      };
    },
    // Drawer mode : row is "expanded" when its host matches
    // the currently-open drawer. Used for chevron rotation +
    // hover-tint suppression on the source row, exactly as the
    // legacy inline-expansion behaved.
    isHostExpanded(name) {
      return !!(this.drawerHost && this.drawerHost.host === name);
    },
    // A host is "expandable" only when it's actually alive AND has
    // enough merged data to justify opening the detail cards. The
    // green dot (``status === 'up'``) is the primary signal; we
    // also require at least one provider to have matched so hosts
    // defined in Admin → Hosts but never hit don't appear
    // interactive (same visual feedback as a dead row).
    isHostExpandable(h) {
      if (!h) return false;
      // Admins can always expand — the drawer's debug panel is the
      // canonical tool for diagnosing "host not reporting any data"
      // and must be reachable even when the host is down or unmatched.
      if (this.me && this.me.role === 'admin') return true;
      // Asset-inventory match → expandable even without live data.
      // The drawer surfaces vendor / model / serial / interfaces /
      // ports from the cached <asset-api-host> row, so a host with NO live
      // providers (FTTH routers / 5G modems / etc. that nothing
      // scrapes) still has something worth opening.
      if (this.assetForHost(h)) return true;
      // Otherwise: any "up" host is expandable. The previous gate
      // ALSO required `h.providers.length > 0`, which rejected
      // node-exporter-only hosts where the providers field arrived
      // a tick later than the status. Status alone is the right
      // signal — a host whose provider replied with `ok` already
      // means there's data behind it.
      return h.status === 'up';
    },
    // --- Debug panel for a single host (admin-only) ---
    // Lazily fetches /api/hosts/debug and caches per host.id. Toggling
    // the panel open triggers the fetch on first use; later opens reuse
    // the cached snapshot (explicit Refresh clears it).
    async toggleHostDebug(hostId) {
      if (!hostId) return;
      const open = !this.hostsDebugOpen[hostId];
      this.hostsDebugOpen = { ...this.hostsDebugOpen, [hostId]: open };
      if (open) {
        this._scrollHostSectionIntoView(`debug-${hostId}`);
        if (!this.hostsDebug[hostId] && !this.hostsDebugLoading[hostId]) {
          await this.loadHostDebug(hostId);
        }
      }
    },
    // Jump to the host-drawer debug panel from another surface
    // (#843 follow-up — paused-providers banner has a "View counters"
    // link). Forces the panel OPEN (no toggle, since the operator's
    // intent here is "show me", not "flip"), triggers the lazy load
    // if cold, then scrolls. Idempotent — calling twice on an already-
    // open panel is a no-op except for the re-scroll, which is what
    // you want when the operator clicks the link a second time.
    async jumpToHostDebug(hostId) {
      if (!hostId) return;
      if (!this.hostsDebugOpen[hostId]) {
        this.hostsDebugOpen = { ...this.hostsDebugOpen, [hostId]: true };
        if (!this.hostsDebug[hostId] && !this.hostsDebugLoading[hostId]) {
          // Don't await — let the scroll happen now, the panel will
          // populate when the fetch lands.
          this.loadHostDebug(hostId).catch(() => {});
        }
      }
      this._scrollHostSectionIntoView(`debug-${hostId}`);
    },
    // Smooth-scrolls the host-drawer's inner scroller so the named
    // section (`data-host-section="<kind>-<host_id>"`) lands near the
    // top. Plain `scrollIntoView({block:'start'})` worked in
    // Chrome but Safari was scrolling the page instead of the drawer
    // — so this helper finds the drawer's scrollable ancestor
    // explicitly and sets `scrollTop` directly. Two rAFs after the
    // x-show flip ensure layout has been painted before we measure.
    _scrollHostSectionIntoView(sectionKey) {
      const sel = `[data-host-section="${sectionKey}"]`;
      this.$nextTick(() => {
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const el = document.querySelector(sel);
          if (!el) return;
          // Walk up to the nearest scrollable ancestor — the
          // host-drawer panel itself in practice.
          let scroller = el.parentElement;
          while (scroller) {
            const cs = window.getComputedStyle(scroller);
            if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll')
                && scroller.scrollHeight > scroller.clientHeight) {
              break;
            }
            scroller = scroller.parentElement;
          }
          if (scroller) {
            const rect = el.getBoundingClientRect();
            const srect = scroller.getBoundingClientRect();
            const target = scroller.scrollTop + (rect.top - srect.top) - 12;
            try {
              scroller.scrollTo({ top: target, behavior: 'smooth' });
            } catch (_) {
              scroller.scrollTop = target;
            }
          } else {
            try { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (_) {}
          }
        }));
      });
    },
    async loadHostDebug(hostId) {
      if (!hostId) return;
      this.hostsDebugLoading = { ...this.hostsDebugLoading, [hostId]: true };
      try {
        const r = await fetch('/api/hosts/debug?id=' + encodeURIComponent(hostId));
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.hostsDebug = {
            ...this.hostsDebug,
            [hostId]: { _error: j.detail || `HTTP ${r.status}` },
          };
          return;
        }
        const d = await r.json();
        this.hostsDebug = { ...this.hostsDebug, [hostId]: d };
      } catch (e) {
        this.hostsDebug = {
          ...this.hostsDebug,
          [hostId]: { _error: `Network: ${e.message}` },
        };
      } finally {
        this.hostsDebugLoading = { ...this.hostsDebugLoading, [hostId]: false };
      }
    },
    // Pretty-print JSON for the Debug panel's <pre> blocks. Empty
    // payloads return "" — the panel wrapper x-show's on truthy data
    // so the block doesn't render at all instead of showing a stub
    // "(not collected)" string that clutters the grid.
    fmtDebugJson(v) {
      if (v === null || v === undefined) return '';
      try { return JSON.stringify(v, null, 2); }
      catch { return String(v); }
    },
    // "Is this debug payload worth rendering?" — wrapper x-show for
    // each box in the grid. null / undefined → false (hide); empty
    // object / empty array → false; anything else → true.
    hasDebugData(v) {
      if (v === null || v === undefined) return false;
      if (typeof v === 'object') {
        return Array.isArray(v) ? v.length > 0 : Object.keys(v).length > 0;
      }
      return true;
    },
    // Clipboard copy button — pretty-prints the payload and shows a
    // quick toast. Falls back to a prompt() if the Clipboard API is
    // unavailable (old Safari, file:// protocol).
    // aria-label helper for the host-debug-copy buttons — resolves
    // the inner label key (e.g. `debug_panel.labels.counters`) and
    // wraps in the `debug_panel.copy_aria` template ("Copy {label}
    // to clipboard"). Pre-fix the copy buttons were icon-only with
    // a `:title` (hover-only) — screen readers announced "button
    // button button" because the SVG is `aria-hidden="true"` and no
    // accessible name was bound. Per CLAUDE.md "icon-only buttons
    // need aria-label". This helper lets every copy button bind a
    // single one-line aria-label without duplicating the t()
    // composition at every site.
    copyAriaLabel(labelKey) {
      const inner = labelKey ? this.t(labelKey) : this.t('debug_panel.copy_default_label');
      return this.t('debug_panel.copy_aria', { label: inner });
    },
    async copyDebugJson(v, label) {
      const text = this.fmtDebugJson(v);
      if (!text) {
        this.showToast(this.t('toasts_extra.nothing_to_copy'), 'warning');
        return;
      }
      // Resolve the label through i18n (#843 follow-up). Pre-fix call
      // sites passed English literals like 'Counters' / 'Raw · Pulse'
      // — both the toast AND the prompt() fallback embedded that raw
      // English regardless of locale. Now: call sites pass an i18n
      // KEY (e.g. 'debug_panel.labels.counters') and the helper
      // resolves it. Backwards-compat: if the resolved value matches
      // the raw key (i.e. no translation found), fall back to a
      // generic "debug data" string — also i18n'd. Empty / falsy
      // labels also fall through to that default.
      let resolvedLabel = '';
      if (label) {
        const candidate = this.t(label);
        // `t()` returns the key itself when no translation is found
        // — treat that as "label was a raw string, not an i18n key"
        // and use it verbatim so old callers keep working.
        resolvedLabel = (candidate === label && !label.includes('.'))
          ? label
          : candidate;
      }
      if (!resolvedLabel) {
        resolvedLabel = this.t('debug_panel.copy_default_label');
      }
      try {
        await navigator.clipboard.writeText(text);
        this.showToast(this.t('toasts_extra.copied', { label: resolvedLabel }), 'success');
      } catch (_) {
        // Fallback — let the user copy manually. Wrap the entire
        // English "Copy <label> (Cmd/Ctrl+C):" string in i18n too so
        // non-en operators get a translated prompt header.
        const promptHead = this.t('debug_panel.copy_prompt_fallback', { label: resolvedLabel });
        window.prompt(promptHead, text);
      }
    },
    // Filtered view for the Hosts table — search matches host id,
    // label, platform, OS, kernel, and provider names so an operator
    // can find a host by whatever field they remember. Sort order:
    // alive hosts first (green dot), then paused, then down/unknown,
    // each group alphabetical by label/id. Dead rows cluster at the
    // bottom so the top of the view is always the "interesting"
    // stuff.
    // True when the curated row has at least one provider name mapped —
    // i.e. SOMETHING will probe it. Inventory rows (no beszel_name /
    // pulse_name / ne_url / webmin_name) return false. Used both by
    // `filteredHosts()` for the hide-unconfigured filter and by the
    // toolbar count badge.
    hostHasAgent(h) {
      if (!h) return false;
      // SNMP gates on `snmp_enabled === true` per #654's
      // opt-in contract; the bare `snmp_name` is no longer enough.
      return !!(h.beszel_name || h.pulse_name || h.ne_url
                || h.webmin_name || h.ping_enabled
                || (h.snmp_name && h.snmp_enabled === true));
    },
    // Telemetry = any provider that contributes CPU / Memory / Disk
    // gauges. Ping is reachability + latency only; a ping-only host
    // shouldn't render CPU/Mem/Disk bars (every value would be zero,
    // which looks like "loading" or a broken row). Use this to gate
    // the bars on the host rows (desktop + mobile) and the per-card
    // CPU/Mem/Disk/Net/DiskIO charts in the drawer.
    //
    // Per-metric telemetry gates. Each axis (CPU / Memory / Disk)
    // is gated INDEPENDENTLY so a host that only reports one axis
    // (Cisco SG300 switch via SNMP exposes CPU but no mem/disk;
    // chassis BMCs like Dell iDRAC + APC UPS report ZERO of these
    // axes — only chassis sensors / UPS battery state) shows just
    // the bars it can fill instead of three together (or none).
    // Beszel / Pulse / NE / Webmin always count for all three
    // because they're host-OS agents that emit CPU + Mem + Disk
    // uniformly.
    _hostUnixAgent(h) {
      return !!(h && (h.beszel_name || h.pulse_name || h.ne_url || h.webmin_name));
    },
    // Has the SNMP history series ever recorded a NON-ZERO value
    // for this metric in the loaded window? Used to gate the bar
    // visibility so chassis BMCs (iDRAC / APC UPS / managed
    // switches without hrProcessorLoad) — whose probe writes flat
    // null / zero cpu_used_pct rows on every tick — don't surface
    // a CPU bar that looks "loading" but never moves. The previous
    // gate `hostHasInlineSpark(h, metric)` only required ≥2 finite
    // values; 0 is finite, so flat-zero history was passing as
    // "telemetry available". Stricter check requires at least one
    // strictly-positive sample so a host genuinely at idle (cpu=0
    // sustained) is correctly hidden alongside hosts that never
    // reported the metric at all.
    // Gate semantic: "this host's SNMP agent CAN report CPU/memory"
    // (capability), NOT "this host has had non-zero CPU recently"
    // (value). The DB-level distinction already exists:
    // host_metrics_sampler writes `cpu_used_pct = float(v) if v is
    // not None else None` — so an agent that returns 0% stores 0.0
    // and an agent that doesn't expose CPU stores NULL. Mirror that
    // here: any finite (non-null) value flips the gate, including 0.
    // Hides bars only for genuine no-data hosts (APC UPS, iDRAC
    // chassis BMC, basic switches that don't expose hrProcessorLoad
    // / cpmCPUTotal*); shows bars for idle-at-0 hosts (Cisco SG300
    // switches at 0% CPU) which is the truthful representation.
    _hostHasFiniteSnmpHistory(h, metric) {
      if (!h) return false;
      const entry = this.hostSnmpHistory && this.hostSnmpHistory[h.id];
      const points = entry && Array.isArray(entry.points) ? entry.points : null;
      if (!points || points.length < 2) return false;
      if (metric === 'cpu') {
        for (const p of points) {
          const v = p && p.cpu_used_pct;
          if (v !== null && v !== undefined && Number.isFinite(Number(v))) return true;
        }
        return false;
      }
      if (metric === 'memory') {
        for (const p of points) {
          const tot = Number(p && p.mem_total);
          if (Number.isFinite(tot) && tot > 0) return true;
        }
        return false;
      }
      // SNMP history doesn't currently carry disk_percent — disk
      // axis falls through to live h.disk_total / h.disk_percent.
      return false;
    },
    hostHasCpuMetric(h) {
      if (!h) return false;
      if (this._hostUnixAgent(h)) return true;
      if (h.snmp_name && h.snmp_enabled === true) {
        // Live cpu > 0 is an unambiguous signal — show bar.
        // Live cpu === 0 is ambiguous: the API row's
        // `cpu_percent` defaults to 0 when the SNMP probe didn't
        // report CPU at all (APC UPS, iDRAC chassis BMC), so we
        // can't tell "agent doesn't expose CPU" from "agent at
        // 0%" off the live value alone. Defer to SNMP history,
        // which writes NULL when the agent didn't expose
        // host_cpu_percent and a real 0.0 when the agent reported 0.
        if (Number(h.cpu_percent) > 0) return true;
        return this._hostHasFiniteSnmpHistory(h, 'cpu');
      }
      return false;
    },
    hostHasMemMetric(h) {
      if (!h) return false;
      if (this._hostUnixAgent(h)) return true;
      if (h.snmp_name && h.snmp_enabled === true) {
        // Mem-total > 0 (live) is the capability signal — UPS / chassis
        // BMC agents that don't expose hrStorage RAM don't surface a
        // total. History fallback uses the same rule.
        const memTot = +h.mem_total || 0;
        if (memTot > 0) return true;
        return this._hostHasFiniteSnmpHistory(h, 'memory');
      }
      return false;
    },
    hostHasDiskMetric(h) {
      if (!h) return false;
      if (this._hostUnixAgent(h)) return true;
      if (h.snmp_name && h.snmp_enabled === true) {
        const diskTot = +h.disk_total || 0;
        const diskPct = +h.disk_percent || 0;
        return diskTot > 0 || diskPct > 0;
      }
      return false;
    },
    // Outer container gate: ANY of the three axes has data. Used
    // by the bars-grid wrapper to mount/unmount the whole block;
    // each individual bar inside ALSO gates on its own axis so
    // a CPU-only host doesn't render empty Mem + Disk bars.
    hostHasTelemetry(h) {
      if (!h) return false;
      return this.hostHasCpuMetric(h) || this.hostHasMemMetric(h) || this.hostHasDiskMetric(h);
    },
    // Display list of agents enabled on a host — used by the drawer's
    // dedicated "Enabled agents" card. Returns rich objects so the
    // template can render colored pills:
    //   { name: 'beszel', label: 'Beszel', pill: 'pill-ok' }
    // Each pill class is hand-mapped for visual distinctness across
    // the five providers (the four ok/info/update colors that
    // `providerStates` uses don't have enough variance for five
    // distinct chips, so pill-primary is added for one slot). Ping
    // shows alongside the four telemetry providers because from the
    // operator's POV it's a distinct opt-in agent — even though it
    // doesn't contribute CPU / Mem / Disk gauges (#571 follow-up:
    // operator wanted color-coded pills, not a comma-joined list).
    hostEnabledAgents(h) {
      if (!h) return [];
      // Each chip is `pill-custom` so it picks up the configured
      // per-provider colour via providerChipStyle. The
      // hand-mapped pill class names left over from the original
      // implementation are deliberately dropped — the colour now
      // flows from the operator-settable provider_color_* settings,
      // not from a fixed visual mapping that conflated providers.
      //
      // #816: every per-host enable check is paired with a fleet-level
      // `hasHostStatsSource(<provider>)` gate. A provider that's
      // disabled at the fleet level (operator un-ticked it in
      // Settings → Host stats) MUST NOT render its chip even when
      // the per-host alias / enable flag are still populated —
      // pre-#816 a stale SNMP chip with a Paused state still appeared
      // on hosts where SNMP was globally disabled, while the per-chip
      // Resume was disabled (busy-flag stuck) and the rollup-Resume
      // was enabled. Filtering at the chip-render gate makes the
      // contradictory state impossible by construction.
      if (!h) return [];
      const out = [];
      if (h.beszel_name && this.hasHostStatsSource('beszel'))               out.push({ name: 'beszel',        label: 'Beszel' });
      if (h.pulse_name && this.hasHostStatsSource('pulse'))                 out.push({ name: 'pulse',         label: 'Pulse' });
      if (h.ne_url && this.hasHostStatsSource('node_exporter'))             out.push({ name: 'node_exporter', label: 'node-exporter' });
      if (h.webmin_name && this.hasHostStatsSource('webmin'))               out.push({ name: 'webmin',        label: 'Webmin' });
      if (h.ping_enabled && this.hasHostStatsSource('ping'))                out.push({ name: 'ping',          label: 'Ping' });
      // per #654's opt-in contract, render the SNMP chip ONLY
      // when both the alias is set AND the operator has explicitly
      // ticked "Enable SNMP for this host". Pre-fix the chip rendered
      // on every row whose snmp_name had ever been typed, even when
      // the operator un-ticked the enable box and saved.
      if (h.snmp_name && h.snmp_enabled === true && this.hasHostStatsSource('snmp')) {
        out.push({ name: 'snmp', label: 'SNMP' });
      }
      return out;
    },
    // List of paused provider names for one host (#797 / UX-ENH-003).
    // Used by the drawer's "Resume all (N)" rollup button to enumerate
    // every chip currently in Paused state. Returns an empty array
    // when no provider is paused — the rollup hides cleanly.
    //
    // FILTERS against `hostEnabledAgents(h)` so providers that are
    // NOT currently enabled on this host don't surface as paused —
    // the orphan-row sweep on host-config save (BUG-006) handles
    // host-level orphans, but this guard handles the per-host
    // provider-toggle case (operator disabled SNMP on this row but
    // the failure-state row from the previous probes still exists).
    // Without this filter, "Resume all (2)" can render on a host
    // that only has Ping enabled because old SNMP / Webmin failure
    // rows still match the host_id suffix.
    pausedProvidersFor(h) {
      if (!h) return [];
      const map = h.provider_pause_state;
      if (!map || typeof map !== 'object') return [];
      const enabledNames = new Set(
        (this.hostEnabledAgents(h) || []).map((a) => a && a.name).filter(Boolean),
      );
      const out = [];
      for (const name of Object.keys(map)) {
        if (!enabledNames.has(name)) continue;
        const row = map[name];
        if (row && row.paused) out.push(name);
      }
      return out;
    },
    // #816 — Single-source-of-truth predicates for every Resume affordance
    // in the host drawer. Pre-#816 the per-chip Resume gated on the busy
    // flag (`providerResumeBusy[host:provider]`), the rollup gated on
    // pausedProvidersFor.length, and the whole-host Resume sampling
    // gated on `h._resumeBusy`. Three independent predicates produced
    // the operator-visible bug where the per-chip button was disabled
    // while the rollup remained enabled, even though both targeted the
    // same provider on the same host.
    //
    // Contract:
    //   canResume(h, name)  — per-chip / per-provider Resume button.
    //                          Allowed when admin + the chip is paused
    //                          + neither the per-provider busy flag NOR
    //                          the whole-host busy flag is set.
    //   canResumeAny(h)     — "Resume all" rollup buttons (top banner +
    //                          bottom of card). Allowed when admin + at
    //                          least one provider is paused + NO per-
    //                          provider busy is set across the paused
    //                          set + whole-host isn't busy. So clicking
    //                          one per-chip Resume disables the rollup
    //                          for the duration, and vice versa — the
    //                          two affordances can never disagree.
    //   canResumeHost(h)    — whole-host Resume sampling button.
    //                          Allowed when admin + the host is paused +
    //                          neither host-busy NOR any per-provider
    //                          busy on this host is set.
    canResume(h, name) {
      if (!h || !name || !this.isAdmin()) return false;
      if (!this.agentPauseInfo(h, name)) return false;
      if (this.providerResumeBusy[h.id + ':' + name]) return false;
      if (h._resumeBusy) return false;
      return true;
    },
    canResumeAny(h) {
      if (!h || !this.isAdmin()) return false;
      const paused = this.pausedProvidersFor(h);
      if (!paused.length) return false;
      if (h._resumeBusy) return false;
      const hostId = h.id;
      for (const name of paused) {
        if (this.providerResumeBusy[hostId + ':' + name]) return false;
      }
      return true;
    },
    canResumeHost(h) {
      if (!h || !this.isAdmin()) return false;
      if (!h.sampling_paused) return false;
      if (h._resumeBusy) return false;
      const hostId = h.id;
      const paused = this.pausedProvidersFor(h);
      for (const name of paused) {
        if (this.providerResumeBusy[hostId + ':' + name]) return false;
      }
      return true;
    },
    // Resume-all action for the drawer rollup. Fans out
    // `resumeProvider(h, name)` calls in parallel for every currently-
    // paused provider on this host. Optimistic UI clears each row's
    // pause state immediately so the chips flip back without waiting
    // for the per-call SSE round-trip; per-call errors land in the
    // toast layer individually so a partial-failure case is visible
    // ("Resumed 4 of 6 providers; 2 failed"). Admin-only — the button
    // hides when the operator isn't admin.
    async resumeAllProviders(host) {
      if (!host || !host.id) return;
      const paused = this.pausedProvidersFor(host);
      if (!paused.length) return;
      const total = paused.length;
      const results = await Promise.allSettled(
        paused.map((name) => this.resumeProvider(host, name)),
      );
      const failed = results.filter((r) => r.status === 'rejected').length;
      const ok = total - failed;
      if (failed === 0) {
        this.showToast(this.t('hosts_extra.provider_resume_all_done', {
          count: ok, host: this.hostDisplayName(host) || host.id,
        }), 'success');
      } else {
        this.showToast(this.t('hosts_extra.provider_resume_all_partial', {
          ok, total, failed, host: this.hostDisplayName(host) || host.id,
        }), 'warning');
      }
    },
    // Per-(provider, host) auto-pause lookup. Returns the
    // pause-state row for `name` on `h`, or null when the provider
    // isn't paused for this host. Backend populates
    // `provider_pause_state: {snmp: {paused, consecutive_failures,
    // last_error, paused_at, last_ok_ts, ...}}` on every host API
    // row via `_provider_pause_state_for_host(host_id)`. Used by the
    // host drawer's Enabled-agents card to render Paused styling +
    // the Resume button (admin-only).
    // Drawer-chip state-class resolver — mirrors the outer Hosts-row
    // provider chip (`providerStates(h)` → 'failing'/'paused'/'ok')
    // so the drawer's "ENABLED AGENTS" pills reflect the SAME state
    // colour the operator sees outside the drawer. Failing → pill-error
    // (red), paused → pill-warning (orange), otherwise pill-custom
    // (operator-customised brand colour via providerChipStyle).
    _agentStateFor(h, name) {
      if (!h || !name) return 'ok';
      // Try the providerStates list first — same data the outer chip
      // strip consumes. Falls back to agentPauseInfo for older code
      // paths that don't populate providerStates.
      try {
        const states = (typeof this.providerStates === 'function')
          ? this.providerStates(h) : [];
        if (Array.isArray(states)) {
          const match = states.find(p => p && p.name === name);
          if (match && (match.state === 'failing' || match.state === 'paused')) {
            return match.state;
          }
        }
      } catch (_) { /* fall through to pause-info fallback */ }
      if (this.agentPauseInfo(h, name)) return 'paused';
      return 'ok';
    },
    agentStateClass(h, name) {
      const s = this._agentStateFor(h, name);
      if (s === 'failing') return 'pill-error';
      if (s === 'paused')  return 'pill-warning';
      return 'pill-custom';
    },
    agentStateStyle(h, name) {
      const s = this._agentStateFor(h, name);
      if (s === 'failing' || s === 'paused') return '';
      return this.providerChipStyle(name);
    },
    agentStateTitle(h, name) {
      const s = this._agentStateFor(h, name);
      if (s === 'paused') {
        const info = this.agentPauseInfo(h, name) || {};
        return this.t('hosts_extra.provider_paused', {
          provider: name,
          count: info.consecutive_failures || 0,
          error: info.last_error || '—',
        });
      }
      if (s === 'failing') {
        return this.t('hosts_extra.provider_failing', { provider: name });
      }
      return '';
    },
    agentPauseInfo(h, name) {
      if (!h || !name) return null;
      const map = h.provider_pause_state;
      if (!map || typeof map !== 'object') return null;
      const row = map[name];
      if (!row || !row.paused) return null;
      // Match `pausedProvidersFor` — only surface paused state for
      // providers that are CURRENTLY enabled on this host. Stale
      // failure-state rows from a previously-enabled provider that
      // the operator has since disabled would otherwise render an
      // amber chip + Resume button on a chip that wouldn't render
      // at all. Cheap enabledNames lookup; called per-chip so this
      // is hot path.
      const enabledNames = new Set(
        (this.hostEnabledAgents(h) || []).map((a) => a && a.name).filter(Boolean),
      );
      if (!enabledNames.has(name)) return null;
      return row;
    },
    // Last-OK timestamp for a (host, provider) pair. Returns 0 when
    // the provider has never had a successful probe recorded for this
    // host on the current schema (host hasn't been seen since #785
    // shipped, or this is the first probe ever). The chip subtitle
    // hides on 0.
    providerLastOkSeconds(h, name) {
      if (!h || !name) return 0;
      const map = h.provider_pause_state;
      if (!map || typeof map !== 'object') return 0;
      const row = map[name];
      if (!row) return 0;
      return Number(row.last_ok_ts || 0);
    },
    // Human-friendly "Xm ago" / "Xh ago" age string for the chip
    // subtitle. Returns empty when there's nothing to render (the
    // x-show gate hides the span anyway, but the helper stays
    // defensive).
    providerLastOkAge(h, name) {
      const ts = this.providerLastOkSeconds(h, name);
      if (!ts) return '';
      return this.fmtAgo(ts * 1000);
    },
    // Resume-button busy-state map. Keyed `<host_id>:<provider>` so
    // simultaneous resumes on different providers don't collide.
    providerResumeBusy: {},
    // Manual resume action for the per-provider auto-pause.
    // POSTs /api/hosts/{id}/provider/{name}/resume which clears the
    // failure-state row + the in-memory cool-down for that provider.
    // Optimistic UI: clear the local pause row immediately so the
    // chip flips back without waiting for the next poll, then refresh
    // the row via the shared queue to confirm.
    async resumeProvider(host, name) {
      if (!host || !host.id || !name) return;
      const key = host.id + ':' + name;
      if (this.providerResumeBusy[key]) return;
      this.providerResumeBusy[key] = true;
      // Safety timer — even if `await fetch` hangs forever (browser
      // network freeze, broken proxy holding the connection, etc.),
      // the button gets re-enabled after 30s. Prevents the
      // "click-once-stuck-forever" footgun that operator hit.
      const safetyTimer = setTimeout(() => {
        this.providerResumeBusy[key] = false;
      }, 30000);
      try {
        const r = await fetch(
          '/api/hosts/' + encodeURIComponent(host.id)
          + '/provider/' + encodeURIComponent(name) + '/resume',
          { method: 'POST' },
        );
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.showToast(this.t('hosts_extra.provider_resume_failed', {
            provider: name,
            error: (j && j.detail) || ('HTTP ' + r.status),
          }), 'error');
          return;
        }
        // Optimistic clear so the chip flips immediately. The next
        // refresh from the shared host-refresh queue confirms via the
        // backend's authoritative state.
        if (host.provider_pause_state && host.provider_pause_state[name]) {
          host.provider_pause_state[name].paused = false;
        }
        this.showToast(this.t('hosts_extra.provider_resumed', {
          provider: name,
          host: this.hostDisplayName(host) || host.id,
        }), 'success');
        // Force-refresh the row so backend's authoritative state lands.
        if (typeof this.refreshHostRow === 'function') {
          this.refreshHostRow(host.id, { force: true }).catch(() => {});
        }
      } catch (e) {
        this.showToast(this.t('hosts_extra.provider_resume_failed', {
          provider: name,
          error: String(e),
        }), 'error');
      } finally {
        clearTimeout(safetyTimer);
        this.providerResumeBusy[key] = false;
      }
    },
    filteredHosts() {
      const q = (this.hostsSearch || '').trim().toLowerCase();
      const statusWeight = (s) => {
        switch ((s || '').toLowerCase()) {
          case 'up':     return 0;
          case 'paused': return 1;
          case 'down':   return 2;
          default:       return 3;
        }
      };
      const nameOf = (h) => this.hostDisplayName(h).toLowerCase();
      // Group key for 'type' sort — prefer platform over os so
      // Proxmox/LXC rows cluster distinctly from plain Debian, etc.
      // Empty values sort LAST ('~' > any printable letter).
      const typeOf = (h) => {
        const t = ((h.platform || h.os || '') + '').toLowerCase().trim();
        return t || '~';
      };
      const num = (v) => (Number.isFinite(+v) ? +v : 0);

      let list = (this.hosts || []).slice();
      if (this.hostsHideUnconfigured) {
        list = list.filter(h => this.hostHasAgent(h));
      }
      // provider filter (toolbar chips). Empty set = show all
      // (status quo). Otherwise OR-match across the selected provider
      // names; the synthetic 'none' name matches hosts that have NO
      // provider field configured, so operators can isolate
      // inventory-only rows.
      if (this.hostsProviderFilter && this.hostsProviderFilter.size) {
        const filt = this.hostsProviderFilter;
        const wantNone = filt.has('none');
        list = list.filter(h => {
          if (wantNone && !this.hostHasAgent(h)) return true;
          // hostEnabledAgents() returns ALL configured provider fields
          // on the row — bare alias presence (beszel_name / pulse_name /
          // ...) counts even when the live probe hasn't returned yet,
          // so the filter survives transient probe failures.
          const agents = (this.hostEnabledAgents(h) || []).map(a => a.name);
          for (const a of agents) if (filt.has(a)) return true;
          return false;
        });
      }
      if (q) {
        list = list.filter(h => {
          // Asset-record fields layered into the haystack so a host
          // whose display label is auto-derived from the asset
          // inventory (operator hasn't set an explicit label) still
          // matches when the operator types the asset's stored
          // name / vendor / model / serial / location / type. Walk
          // EVERY enumerable string-ish key on the asset so any
          // field — including ones added later (asset_tag, ip,
          // notes, location_path, …) — is searchable without
          // re-listing them here.
          const asset = this.assetForHost ? (this.assetForHost(h) || null) : null;
          const assetFields = [];
          if (asset && typeof asset === 'object') {
            for (const k in asset) {
              const v = asset[k];
              if (v == null) continue;
              if (typeof v === 'string' || typeof v === 'number') {
                assetFields.push(String(v));
              }
            }
          }
          const hay = [
            h.host, h.label, h.id,
            h.platform, h.os, h.kernel,
            h.custom_number,
            h.beszel_name, h.pulse_name, h.snmp_name, h.webmin_name,
            h.url, h.icon,
            h.cpu_model, h.model, h.vendor, h.serial,
            ...(h.providers || []),
            ...assetFields,
          ].filter(v => v !== null && v !== undefined && v !== '')
           .join(' ')
           .toLowerCase();
          // Multi-word AND search — every whitespace-separated token
          // must appear somewhere in the haystack, in any order. Pre-
          // fix the query was treated as one substring, so typing
          // "cisco switch" missed a host with asset.name="Cisco SG300
          // 52-Port Gigabit PoE Managed Switch" because the literal
          // "cisco switch" doesn't appear continuously. Splitting on
          // whitespace and requiring every token to hit lets the
          // operator type any combination of words from the host's
          // various fields and find it.
          const tokens = q.split(/\s+/).filter(Boolean);
          return tokens.every(t => hay.includes(t));
        });
      }

      const sortKey = (this.hostsSort || 'status');
      // Every branch breaks ties via (status → name) so the result is
      // deterministic regardless of the array's incoming order.
      const tieBreak = (a, b) => {
        const sw = statusWeight(a.status) - statusWeight(b.status);
        if (sw !== 0) return sw;
        return nameOf(a).localeCompare(nameOf(b));
      };

      let cmp;
      switch (sortKey) {
        case 'seq':
        case 'insertion':
          // Addition order — falls back to name when _seq is missing
          // (older server response without the stamp).
          cmp = (a, b) => {
            const d = num(a._seq) - num(b._seq);
            if (d !== 0) return d;
            return nameOf(a).localeCompare(nameOf(b));
          };
          break;
        case 'custom_number':
          // Operator-assigned catalogue number. Hosts without a number
          // (null / blank) sort LAST so unnumbered machines cluster at
          // the bottom and don't compete with ordered entries.
          cmp = (a, b) => {
            const ax = (a.custom_number == null || a.custom_number === '') ? Number.POSITIVE_INFINITY : num(a.custom_number);
            const bx = (b.custom_number == null || b.custom_number === '') ? Number.POSITIVE_INFINITY : num(b.custom_number);
            if (ax !== bx) return ax - bx;
            return nameOf(a).localeCompare(nameOf(b));
          };
          break;
        case 'name':
          cmp = (a, b) => nameOf(a).localeCompare(nameOf(b));
          break;
        case 'type':
          cmp = (a, b) => {
            const d = typeOf(a).localeCompare(typeOf(b));
            if (d !== 0) return d;
            return tieBreak(a, b);
          };
          break;
        case 'cpu':
          cmp = (a, b) => num(b.cpu_percent) - num(a.cpu_percent) || tieBreak(a, b);
          break;
        case 'mem':
          cmp = (a, b) => num(b.mem_percent) - num(a.mem_percent) || tieBreak(a, b);
          break;
        case 'disk':
          cmp = (a, b) => num(b.disk_percent) - num(a.disk_percent) || tieBreak(a, b);
          break;
        case 'uptime':
          cmp = (a, b) => num(b.uptime_s) - num(a.uptime_s) || tieBreak(a, b);
          break;
        case 'status':
        default:
          cmp = tieBreak;
          break;
      }
      list.sort(cmp);
      return list;
    },
    toggleHost(name) {
      const host = (this.hosts || []).find(h => h.host === name);
      // Already-open drawer can always close (e.g. by clicking the
      // same row again) even if the host flipped from "up" to "down"
      // since the last open — otherwise the operator would be stuck
      // looking at stale detail cards.
      const already = this.drawerHost && this.drawerHost.host === name;
      if (already) {
        this.closeHostDrawer();
        return;
      }
      if (!this.isHostExpandable(host)) {
        return;  // dead / unmatched host — header click is a no-op
      }
      this.openHostDrawer(host);
    },
    openHostDrawer(host) {
      if (!host) return;
      this.drawerHost = host;
      // Defensive clear of any `providerResumeBusy` flags for this
      // host — covers the edge case where a previous click on the
      // Resume button left the flag stuck true (e.g. fetch hung,
      // browser killed the request mid-await, page navigation
      // interrupted the await). Without this, the per-chip Resume
      // button stays disabled forever even though no resume is
      // actually in flight. Also runs the same prefix-clear in
      // _runHostDrawerOpen for tabs reopening drawers after a long
      // idle.
      try {
        const prefix = host.id + ':';
        for (const k of Object.keys(this.providerResumeBusy || {})) {
          if (k.startsWith(prefix)) this.providerResumeBusy[k] = false;
        }
      } catch (_) { /* ignore */ }
      // #816 — defensive clear of the whole-host Resume sampling busy
      // flag. Mirror of the providerResumeBusy clear above. Without
      // this, a previous click whose await never resolved (network
      // freeze, page hidden during fetch, browser killed the request)
      // left `h._resumeBusy` stuck `true` so the Resume sampling
      // button on the whole-host pause banner rendered disabled
      // forever. The 30s safety timer in resumeHostSampling closes
      // the same window from the other end.
      if (host._resumeBusy) host._resumeBusy = false;
      // Load history once per (host, range). Subsequent re-opens of
      // the same host reuse the cached series until the range picker
      // forces a refetch. Same logic the legacy inline-expansion used.
      // Stale-cache guard. Pre-fix the gate was just `!hostHistory[key]`
      // — a previously-cached series fetched at an earlier "now" stayed
      // on the chart even after the operator had been away for an hour,
      // so the unified time-domain (which anchors on Date.now() at
      // render time) clamped every cached point to the left edge and
      // operators saw the chart cropped from the right. The dedicated
      // 30s drawer-history poll eventually corrects it, but the first
      // ~30s after reopening showed stale-positioned data. Re-fetch
      // when the cache is older than 30s OR missing entirely; under
      // that threshold the cached data is fresh enough to keep.
      const HISTORY_STALE_MS = 30_000;
      const _cacheStale = (entry) => !entry || !entry.loadedAt
        || (Date.now() - entry.loadedAt) > HISTORY_STALE_MS;
      const drawerKey = this.hostHistoryKey(host);
      if (drawerKey && (host.beszel_id || host.ne_url || host.pulse_name || host.webmin_name)
          && _cacheStale(this.hostHistory[drawerKey])) {
        this.loadHostHistory(host.beszel_id || '', host.id);
      }
      // ping history is a separate fetch (different endpoint,
      // different key namespace). Only loads when the host has opted
      // in via per-host `ping_enabled`; the chart card itself is
      // hidden via `x-show="h.ping_enabled"` for non-opted hosts.
      const pingKey = this.hostPingHistoryKey(host);
      if (pingKey && host.ping_enabled && _cacheStale(this.hostHistory[pingKey])) {
        this.loadHostPingHistory(host.id);
      }
      // #713 / SNMP history (separate endpoint, separate state
      // map). Load EAGERLY when the host has SNMP enabled, BEFORE the
      // live probe completes — the host_snmp_samples table already
      // carries previous samples written by the sampler, so charts
      // can render the last-known series within ~100ms HTTP RTT
      // instead of waiting 10-20s for the fresh SNMP probe to land.
      // Gated only on `host.snmp_enabled` (curated config) so non-
      // SNMP hosts still skip the fetch.
      if (host.snmp_enabled && _cacheStale(this.hostSnmpHistory[host.id])) {
        this.loadHostSnmpHistory(host.id, this.hostHistoryRange || 1);
      }
      // per-interface SNMP history powers the per-port
      // throughput chart on switches / routers. Same gate + cadence
      // as the per-host SNMP history call above.
      if (host.snmp_enabled && _cacheStale(this.hostSnmpIfaceHistory[host.id])) {
        this.loadHostSnmpIfaceHistory(host.id, this.hostHistoryRange || 1);
      }
      // Per-temperature-probe history powers the multi-line
      // temperature chart card on Dell server hosts (#848 phase 3).
      // Same gate + cadence; non-Dell hosts get an empty `probes`
      // object back so the chart card stays hidden cleanly.
      if (host.snmp_enabled && _cacheStale(this.hostSnmpTempHistory[host.id])) {
        this.loadHostSnmpTempHistory(host.id, this.hostHistoryRange || 1);
      }
      // Dedicated drawer-history poll — keeps the chart series +
      // the `Updated Xs ago` freshness label in sync regardless of
      // whether the operator has the host-list poll enabled (when
      // `statsInterval=0` the loadHosts setInterval never fires, so a
      // hook inside loadHosts doesn't reach the drawer). 30s is a
      // sensible default for a drawer the operator is actively
      // watching; clears on closeHostDrawer. Cadence rules under
      // #486 + #496:
      //   - Off → no timer (static-snapshot promise).
      //   - Live + NE-only host → no timer; the new
      //     `host:history_appended` push event refreshes the chart on
      // every sampler write.
      //   - Live + Beszel host (or NE+Beszel hybrid) → 30s timer
      //     because Beszel data isn't push-driven from our side
      //     (PocketBase owns the writes; we'd need a PB → bus
      //     bridge to push, out of scope).
      //   - Interval mode → poll at the picker cadence regardless of
      //     source (operator explicitly opted out of push).
      if (this._drawerHistoryTimer) {
        clearInterval(this._drawerHistoryTimer);
        this._drawerHistoryTimer = null;
      }
      const hasHistory = host.beszel_id || host.ne_url;
      const live = this.refreshInterval === -1;
      const off = this.refreshInterval === 0;
      // NE-only hosts in Live mode rely entirely on push.
      const pushOnly = live && host.ne_url && !host.beszel_id;
      if (hasHistory && !off && !pushOnly) {
        const ms = (live ? 30 : this.refreshInterval) * 1000;
        this._drawerHistoryTimer = setInterval(() => {
          if (!this.drawerHost) return;
          this._pollWrap(this.loadHostHistory(
            this.drawerHost.beszel_id || '',
            this.drawerHost.id,
          ));
        }, ms);
      }
      // ping history timer. Live mode is push-driven by the
      // host:ping_sampled SSE handler so no fallback timer needed
      // when SSE is healthy; for Off / interval modes the same
      // pickup-cadence rules apply as for the main history timer.
      if (this._drawerPingTimer) {
        clearInterval(this._drawerPingTimer);
        this._drawerPingTimer = null;
      }
      if (host.ping_enabled && !off && !live) {
        const pingMs = this.refreshInterval * 1000;
        this._drawerPingTimer = setInterval(() => {
          if (!this.drawerHost || !this.drawerHost.ping_enabled) return;
          this._pollWrap(this.loadHostPingHistory(this.drawerHost.id));
        }, pingMs);
      }
      // Preload SSH status — admin only, and only when the host
      // explicitly opted IN to SSH (#622, post-flip). Without this the
      // SSH card header shows "Not configured" until the operator
      // clicks to expand it — a false-negative for opted-in fleets.
      if (this.isAdmin && this.isAdmin() && host.ssh_enabled) {
        this.loadSshStatus(host.id);
      }
    },
    closeHostDrawer() {
      this.drawerHost = null;
      this.healthPopoverOpen = false;
      if (this._drawerHistoryTimer) {
        clearInterval(this._drawerHistoryTimer);
        this._drawerHistoryTimer = null;
      }
      if (this._drawerPingTimer) {
        clearInterval(this._drawerPingTimer);
        this._drawerPingTimer = null;
      }
    },
    // SNMP time-series state. Keyed per-host (no Beszel-id
    // fallback because SNMP probes are always per-host). Reads from
    // the new `/api/hosts/{id}/snmp/history` endpoint that wraps the
    // `host_snmp_samples` table written by the sampler. Same loading-
    // flag pattern as `hostHistory` so chart cards don't flicker on
    // range-picker clicks.
    hostSnmpHistory: {},
    async loadHostSnmpHistory(hostId, hours) {
      if (!hostId) return;
      const h = +hours || 1;
      const prev = this.hostSnmpHistory[hostId] || {};
      this.hostSnmpHistory[hostId] = {
        loading: true,
        error: prev.error || '',
        points: Array.isArray(prev.points) ? prev.points : [],
        loadedAt: prev.loadedAt || 0,
      };
      try {
        const r = await fetch(
          `/api/hosts/${encodeURIComponent(hostId)}/snmp/history?hours=${h}`
        );
        if (!r.ok) {
          this.hostSnmpHistory[hostId] = {
            loading: false, error: `HTTP ${r.status}`,
            points: prev.points || [], loadedAt: prev.loadedAt || 0,
          };
          return;
        }
        const d = await r.json();
        this.hostSnmpHistory[hostId] = {
          loading: false,
          error: d.error || '',
          points: Array.isArray(d.points) ? d.points : [],
          loadedAt: Date.now(),
        };
      } catch (e) {
        this.hostSnmpHistory[hostId] = {
          loading: false, error: String(e),
          points: prev.points || [], loadedAt: prev.loadedAt || 0,
        };
      }
    },
    // Per-temperature-probe history for Dell server hosts (#848
    // phase 3). Same shape as hostSnmpIfaceHistory but keyed by
    // probe_idx. `probes: { idx: { name, points: [{ts, c}, …] } }`.
    hostSnmpTempHistory: {},
    async loadHostSnmpTempHistory(hostId, hours) {
      if (!hostId) return;
      const h = +hours || 1;
      const prev = this.hostSnmpTempHistory[hostId] || {};
      this.hostSnmpTempHistory[hostId] = {
        loading: true,
        error: prev.error || '',
        probes: prev.probes && typeof prev.probes === 'object' ? prev.probes : {},
        loadedAt: prev.loadedAt || 0,
      };
      try {
        const r = await fetch(
          `/api/hosts/${encodeURIComponent(hostId)}/snmp/temp_history?hours=${h}`
        );
        if (!r.ok) {
          this.hostSnmpTempHistory[hostId] = {
            loading: false, error: `HTTP ${r.status}`,
            probes: prev.probes || {}, loadedAt: prev.loadedAt || 0,
          };
          return;
        }
        const d = await r.json();
        this.hostSnmpTempHistory[hostId] = {
          loading: false,
          error: d.error || '',
          probes: (d.probes && typeof d.probes === 'object') ? d.probes : {},
          loadedAt: Date.now(),
        };
      } catch (e) {
        this.hostSnmpTempHistory[hostId] = {
          loading: false, error: String(e),
          probes: prev.probes || {}, loadedAt: prev.loadedAt || 0,
        };
      }
    },
    // Per-interface SNMP counter history. One entry per host,
    // each storing { ifaces: { ifname: [points] }, loading, error,
    // loadedAt }. Powers the per-port throughput chart on switches /
    // routers. Same loading-flag + back-compat pattern as
    // `hostSnmpHistory` so chart cards don't flicker.
    hostSnmpIfaceHistory: {},
    async loadHostSnmpIfaceHistory(hostId, hours) {
      if (!hostId) return;
      const h = +hours || 1;
      const prev = this.hostSnmpIfaceHistory[hostId] || {};
      this.hostSnmpIfaceHistory[hostId] = {
        loading: true,
        error: prev.error || '',
        ifaces: prev.ifaces && typeof prev.ifaces === 'object' ? prev.ifaces : {},
        loadedAt: prev.loadedAt || 0,
      };
      try {
        const r = await fetch(
          `/api/hosts/${encodeURIComponent(hostId)}/snmp/iface_history?hours=${h}`
        );
        if (!r.ok) {
          this.hostSnmpIfaceHistory[hostId] = {
            loading: false, error: `HTTP ${r.status}`,
            ifaces: prev.ifaces || {}, loadedAt: prev.loadedAt || 0,
          };
          return;
        }
        const d = await r.json();
        this.hostSnmpIfaceHistory[hostId] = {
          loading: false,
          error: d.error || '',
          ifaces: (d.ifaces && typeof d.ifaces === 'object') ? d.ifaces : {},
          loadedAt: Date.now(),
        };
      } catch (e) {
        this.hostSnmpIfaceHistory[hostId] = {
          loading: false, error: String(e),
          ifaces: prev.ifaces || {}, loadedAt: prev.loadedAt || 0,
        };
      }
    },
    // Compute per-interface bps series from the cumulative counters.
    // Returns { in: [bps...], out: [bps...], times: [ts...] } aligned
    // to the points length. Skip-don't-synthesize on out-of-bounds
    // deltas: same bounds as `snmpThroughputBpsSeries`.
    snmpIfaceBpsSeries(hostId, ifname) {
      const ifaces = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
      const series = ifaces[ifname] || [];
      if (series.length < 2) return { in: [], out: [], times: [] };
      // skip-don't-synthesize. Same null-slot pattern as
      // `snmpThroughputBpsSeries`. First slot null (no predecessor);
      // any out-of-bounds delta (wrap / reboot / gap > 1h / null
      // counter / > 10 GB) leaves the slot null instead of plotting
      // a synthesized 0 that visually merges with a real idle iface.
      const inBps = new Array(series.length).fill(null);
      const outBps = new Array(series.length).fill(null);
      const times = series.map(p => p.ts);
      for (let i = 1; i < series.length; i++) {
        const a = series[i - 1], b = series[i];
        const dt = (b.ts || 0) - (a.ts || 0);
        if (dt < 1 || dt > 3600) continue;
        const ai = a.in_bytes, bi = b.in_bytes;
        if (ai != null && bi != null) {
          const di = bi - ai;
          if (di >= 0 && di <= 10 * 1024 * 1024 * 1024) inBps[i] = di / dt;
        }
        const ao = a.out_bytes, bo = b.out_bytes;
        if (ao != null && bo != null) {
          const dout = bo - ao;
          if (dout >= 0 && dout <= 10 * 1024 * 1024 * 1024) outBps[i] = dout / dt;
        }
      }
      return { in: inBps, out: outBps, times };
    },
    // Top N interfaces by latest combined throughput. Returns array of
    // { name, lastIn, lastOut, total } sorted desc. Used to pick which
    // ports to plot on the per-port chart so 48-port switches don't
    // produce 96 noisy lines.
    snmpTopIfacesByThroughput(hostId, n) {
      const ifaces = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
      const out = [];
      for (const name of Object.keys(ifaces)) {
        const s = this.snmpIfaceBpsSeries(hostId, name);
        let lastIn = 0, lastOut = 0;
        for (let i = s.in.length - 1; i >= 0; i--) {
          if (s.in[i] > 0) { lastIn = s.in[i]; break; }
        }
        for (let i = s.out.length - 1; i >= 0; i--) {
          if (s.out[i] > 0) { lastOut = s.out[i]; break; }
        }
        const total = lastIn + lastOut;
        if (total > 0) out.push({ name, lastIn, lastOut, total });
      }
      out.sort((a, b) => b.total - a.total);
      return out.slice(0, n || 5);
    },
    snmpHasIfaceHistory(hostId) {
      const ifaces = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
      for (const name of Object.keys(ifaces)) {
        if ((ifaces[name] || []).length >= 2) return true;
      }
      return false;
    },
    // true when this host's SNMP history has accumulated enough
    // points to draw a polyline (≥ 2 ticks). Used to gate the
    // "Collecting data..." spinner block that every SNMP chart card
    // shows during warm-up — operator-flagged that pre-fix every
    // SNMP chart rendered an empty grid + axis labels with no
    // indication that data was being collected.
    snmpHasEnoughHistory(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      return series.length >= 2;
    },
    // true when at least one interface has computable
    // utilization % (history sample with link_speed_mbps known). The
    // heatmap card uses this to decide between rendering chips with
    // util% colours vs the "Collecting data..." spinner. Pre-fix the
    // chips rendered from live ifaces immediately but always grey
    // (link speed null) — looked broken. Now waits until at least
    // one chip can show a meaningful colour.
    snmpHasIfaceUtilization(hostId, h) {
      // True when at least one iface has ≥ 2 ticks of bps history.
      // Link speed used for the divisor is either the agent-reported
      // ifHighSpeed (preferred) or a 100 Mbps fallback assumption
      // — printers / embedded gear that don't expose
      // ifHighSpeed previously had this card stuck at "Collecting…"
      // forever. The fallback divisor is announced in the legend
      // tooltip so operators know the percentages are an
      // approximation on those hosts.
      const names = this.snmpAllIfacesSorted(hostId, h);
      for (const n of names) {
        const s = this.snmpIfaceBpsSeries(hostId, n);
        if (s.in.length >= 2 || s.out.length >= 2) return true;
      }
      return false;
    },
    // per-iface utilization series (% of link capacity) over
    // time. Walks `snmpIfaceBpsSeries` and divides each point by the
    // iface's link capacity (Mbps × 1e6 / 8 = bytes/sec). Returns []
    // when link speed unknown — caller guards on length before
    // rendering. Used by the per-port utilization LINE chart that
    // replaced the chip-strip heatmap (operator-flagged that the
    // chip layout was misread as a broken chart).
    snmpIfaceUtilizationSeries(hostId, ifname, h) {
      // #827 — fall back to a 100 Mbps assumption when ifHighSpeed
      // isn't exposed (printers / embedded gear). The percentages
      // are then approximate but the polyline RENDERS instead of
      // staying empty forever. snmpIfaceLinkSpeedAssumed() flags
      // the assumption for legend display.
      const link = this.snmpIfaceLinkSpeedMbps(hostId, ifname, h)
                  || this._DEFAULT_IFACE_LINK_MBPS;
      const linkBps = link * 1_000_000 / 8;
      if (linkBps <= 0) return [];
      const s = this.snmpIfaceBpsSeries(hostId, ifname);
      // null-aware. When the underlying bps series has a
      // counter-wrap / gap slot (null), propagate the null so
      // `_snmpPolyPoints` skips it instead of plotting 0 %.
      const out = new Array(s.in.length).fill(null);
      for (let i = 0; i < s.in.length; i++) {
        const inV = s.in[i], outV = s.out[i];
        if (inV == null && outV == null) continue;
        const peak = Math.max(inV || 0, outV || 0);
        out[i] = Math.min(100, (peak / linkBps) * 100);
      }
      return out;
    },
    snmpIfaceUtilizationLine(hostId, ifname, h) {
      const vals = this.snmpIfaceUtilizationSeries(hostId, ifname, h);
      if (!vals.length) return '';
      // #815 — pull timestamps from the underlying iface series so the
      // utilization polyline renders against the drawer-shared time
      // domain. snmpIfaceUtilizationSeries derives from snmpIfaceBpsSeries
      // which exposes parallel `times`, identical length to the values
      // array.
      const s = this.snmpIfaceBpsSeries(hostId, ifname);
      return this._snmpPathGapped(vals, 100, { times: s.times });
    },
    // Gap-aware path string for one iface's bps series scaled to refMax.
    // Consumer renders via SVG `<path :d>` not `<polyline :points>` so
    // counter-wrap / reboot / gap nulls show as visual breaks.
    snmpIfaceLine(hostId, ifname, dir, refMax) {
      const s = this.snmpIfaceBpsSeries(hostId, ifname);
      const vals = (dir === 'in' ? s.in : s.out);
      if (!vals.length) return '';
      return this._snmpPathGapped(vals, refMax || 1, { times: s.times });
    },
    snmpIfaceMaxBps(hostId) {
      const ifaces = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
      let m = 0;
      for (const name of Object.keys(ifaces)) {
        const s = this.snmpIfaceBpsSeries(hostId, name);
        for (const v of s.in)  if (v > m) m = v;
        for (const v of s.out) if (v > m) m = v;
      }
      return m;
    },
    // #725 slice 4 — link speed (Mbps) for one iface. Tries history
    // first (newest non-null), then falls back to the live
    // `host.network_ifaces[].link_speed_mbps` from the latest probe.
    // Returns null when ifHighSpeed isn't exposed on this device.
    snmpIfaceLinkSpeedMbps(hostId, ifname, h) {
      const series = ((this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {})[ifname] || [];
      for (let i = series.length - 1; i >= 0; i--) {
        const s = series[i].link_speed_mbps;
        if (s != null && s > 0) return s;
      }
      const host = h || (this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null);
      if (host && Array.isArray(host.network_ifaces)) {
        const live = host.network_ifaces.find(i => i && i.name === ifname);
        if (live && live.link_speed_mbps && live.link_speed_mbps > 0) return live.link_speed_mbps;
      }
      return null;
    },
    // 100 Mbps fallback when ifHighSpeed isn't exposed —
    // printers / embedded gear with no managed-NIC reporting still
    // produce a percentage on the per-port utilization chart instead
    // of leaving the card stuck at "Collecting data…". The chart's
    // legend tooltip surfaces "(assumed 100 Mbps)" via
    // `snmpIfaceLinkSpeedAssumed` so operators know the divisor is
    // approximate on those hosts.
    _DEFAULT_IFACE_LINK_MBPS: 100,
    snmpIfaceLinkSpeedAssumed(hostId, ifname, h) {
      return !this.snmpIfaceLinkSpeedMbps(hostId, ifname, h);
    },
    // Utilization % for one iface = max(in, out) bps × 8 ÷ link_bps × 100.
    // Falls back to a 100 Mbps assumption when link speed unknown
    // — pre-fix this returned null so the percent legend stayed
    // blank on printers and the line chart stayed empty forever.
    snmpIfaceUtilizationPct(hostId, ifname, h) {
      const link = this.snmpIfaceLinkSpeedMbps(hostId, ifname, h)
                  || this._DEFAULT_IFACE_LINK_MBPS;
      if (!link) return null;
      const s = this.snmpIfaceBpsSeries(hostId, ifname);
      let lastIn = 0, lastOut = 0;
      for (let i = s.in.length - 1; i >= 0; i--) {
        if (s.in[i] > 0) { lastIn = s.in[i]; break; }
      }
      for (let i = s.out.length - 1; i >= 0; i--) {
        if (s.out[i] > 0) { lastOut = s.out[i]; break; }
      }
      const peakBps = Math.max(lastIn, lastOut);
      const linkBps = link * 1_000_000 / 8;       // Mbps → bytes/sec capacity
      if (linkBps <= 0) return null;
      return Math.min(100, (peakBps / linkBps) * 100);
    },
    // Full iface list for the heatmap. Tries history first, falls
    // back to the LIVE `host.network_ifaces[]` so chips render
    // immediately when the history table is still empty (fresh
    // SNMP enrolment, or before the first sampler tick lands).
    // Excludes loopback / docker / veth / bridge / cni / flannel /
    // cali / vmnet / tap / tun / ovs prefixes — same exclusion set
    // the sampler uses, so chip count matches what the throughput
    // chart graphs.
    snmpAllIfacesSorted(hostId, h) {
      const exclude = ['lo', 'docker', 'veth', 'br-', 'cni',
                       'flannel', 'cali', 'vmnet', 'tap', 'tun', 'ovs'];
      const isExcluded = (name) => {
        const n = (name || '').toLowerCase();
        return exclude.some(p => n.startsWith(p));
      };
      const ifacesHist = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
      let names = Object.keys(ifacesHist).filter(n => !isExcluded(n));
      if (!names.length) {
        const host = h || (this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null);
        if (host && Array.isArray(host.network_ifaces)) {
          names = host.network_ifaces
            .map(i => i && i.name)
            .filter(n => n && !isExcluded(n));
        }
      }
      return names.sort((a, b) =>
        a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })
      );
    },
    // Color mapping for the heatmap cell: green < 50 < amber < 85 < red.
    // Same thresholds as the .stat-bar fill (CLAUDE.md: 60/85 split was
    // for CPU/mem; ports tend to alarm earlier so 50/85). Returns a
    // CSS color literal — heatmap uses inline style because there's
    // no pre-existing token for the per-cell shade.
    snmpIfaceHeatmapColor(pct) {
      if (pct == null) return 'var(--surface-3)';      // unknown speed
      if (pct >= 85) return 'var(--danger)';
      if (pct >= 50) return 'var(--warning)';
      if (pct > 0)  return 'var(--success)';
      return 'var(--surface-3)';                       // 0% / idle
    },
    // True when this host has SNMP data worth charting (per-core CPU
    // OR load avg OR buffers/cached). Drives the gate on the new
    // chart cards so non-SNMP hosts don't see them.
    hostHasSnmpCharts(h) {
      if (!h) return false;
      // SNMP CPU / Load / Memory charts are SUPPRESSED when
      // the host also has Beszel or node-exporter enabled. Both
      // providers carry the same data with a smoother time-series
      // surface AND their own existing chart cards (CPU % / Memory %
      // / Disk % / Load avg) below. Showing the SNMP cards too
      // produced redundant CPU + Memory bars that disagreed with
      // the Beszel/NE values (SNMP's hrProcessorLoad is a 5s
      // average; ssCpuIdle uses snmpd's accounting interval; both
      // are coarser than Beszel/NE's per-tick deltas). Keep the
      // SNMP cards EXCLUSIVE to SNMP-only hosts (managed switches,
      // UPSes, NVRs, embedded routers without an OmniGrid agent).
      if (h.beszel_id || h.ne_url || h.pulse_name || h.webmin_name) return false;
      // also return true when historical SNMP samples exist
      // for this host even before the LIVE probe has populated
      // `host_*` fields. Lets the chart cards render the
      // last-known series during the 10-20s probe window instead
      // of staying blank, with a freshness label so the operator
      // can see how stale the displayed data is.
      const hist = this.hostSnmpHistory[h.id];
      if (hist && Array.isArray(hist.points) && hist.points.length > 0) return true;
      // #138 / also return true when the host reports printer
      // page-count OR IF-MIB net counters (printers / switches /
      // routers without CPU / memory MIBs). Page-count gate requires
      // a real printer signature (supplies / console msg / non-zero
      // count) — APC UPSes and routers can answer prtMarkerLifeCount
      // with 0, which previously triggered the printer chart on
      // non-printer gear.
      const supplies = h.printer_supplies || [];
      const looksLikePrinter = (Array.isArray(supplies) && supplies.length > 0)
        || !!(h.printer_console_msg && String(h.printer_console_msg).trim())
        || ((+h.printer_page_count || 0) > 0);
      if (looksLikePrinter && h.printer_page_count != null) return true;
      if (h.host_net_rx_total_bytes != null || h.host_net_tx_total_bytes != null) return true;
      // #820 — APC UPS hosts (Smart-UPS, Back-UPS, etc.) often expose
      // PowerNet-MIB load / battery / temperature OIDs but neither
      // hrStorage NOR IF-MIB. Without this branch the SNMP chart grid
      // wrapper hid for basic UPS models, taking the new UPS chart
      // cards down with it. Live values arriving on the API row is
      // enough to open the grid; the per-card x-show then decides
      // whether each individual card renders.
      if (typeof h.host_load_percent === 'number'
          || typeof h.host_battery_percent === 'number'
          || typeof h.host_battery_temp_c === 'number'
          || (h.host_ups_status && String(h.host_ups_status).trim())) return true;
      // #848 phase 3 — Dell server hosts whose only SNMP surface is
      // the temperatureProbeTable (no CPU / mem / IF-MIB; the iDRAC's
      // standard MIB-II is locked down). Live host_dell_temps OR
      // historical temp samples is enough to mount the grid wrapper.
      if (Array.isArray(h.host_dell_temps) && h.host_dell_temps.length) return true;
      if (this.dellHasTempHistory && this.dellHasTempHistory(h.id)) return true;
      if ((h.host_cpu_per_core || []).length > 0
          || h.host_load_1m || h.host_load_5m || h.host_load_15m
          || h.host_mem_buffers || h.host_mem_cached) return true;
      // Final fallback: any SNMP-enabled host opens the grid so the
      // chart cards mount immediately on drawer-open. Each card's own
      // x-show still gates on data presence — empty cards just render
      // their "Collecting data..." placeholder until samples accumulate.
      // Pre-fix the grid stayed hidden until the first SNMP probe
      // populated host_* fields (10-20s window after drawer-open),
      // making the host look chart-less when it would soon have data.
      if (h.snmp_enabled === true) return true;
      return false;
    },
    // Detect a reboot in the SNMP uptime history. Walks the
    // points pairwise from latest to oldest looking for the LAST
    // backwards-jump in `uptime_s` (each adjacent pair where N's value
    // is less than N-1's = a reboot in that window). Returns
    // `{ts, prev_uptime_s, age_s}` of the most recent detected reboot,
    // or null when no reboot is in the history window. The drawer
    // surfaces a compact "Rebooted Xh ago" badge when this is non-null
    // AND the age is under 24h (older reboots aren't surfaced to keep
    // the badge actionable for fresh anomalies; full uptime is always
    // shown via the live `host_uptime_s` field).
    snmpRebootInfo(h) {
      if (!h) return null;
      const hist = this.hostSnmpHistory[h.id];
      const points = (hist && hist.points) || [];
      if (points.length < 2) return null;
      let detected = null;
      for (let i = 1; i < points.length; i++) {
        const prev = points[i - 1];
        const curr = points[i];
        if (prev.uptime_s == null || curr.uptime_s == null) continue;
        // Reboot fingerprint: uptime went BACKWARDS between samples.
        // Allow a small slack (60s) to absorb counter-precision noise
        // when sampler ticks are tightly spaced.
        if (curr.uptime_s + 60 < prev.uptime_s) {
          detected = {
            ts: curr.ts,
            prev_uptime_s: prev.uptime_s,
            age_s: Math.max(0, Math.round(Date.now() / 1000 - curr.ts)),
          };
        }
      }
      // Only surface reboots within the last 24h — older reboots are
      // archaeology, not actionable.
      if (detected && detected.age_s > 86400) return null;
      return detected;
    },
    // Memory chart Y-axis upper bound. Prefer the LIVE
    // `host_mem_total` (Beszel/NE-style absolute), fall back to the
    // max `mem_total` in history points so the axis renders sensibly
    // before the live probe completes. Final fallback is the highest
    // observed mem_used + mem_buffers + mem_cached + mem_free across
    // history (if mem_total field was never populated).
    snmpMemMax(h) {
      if (!h) return 0;
      const live = +h.host_mem_total || 0;
      if (live > 0) return live;
      const hist = this.hostSnmpHistory[h.id];
      const points = (hist && hist.points) || [];
      let max = 0;
      for (const p of points) {
        if (p.mem_total && +p.mem_total > max) max = +p.mem_total;
      }
      if (max > 0) return max;
      // Synthesise from the layer sum as a last resort.
      for (const p of points) {
        const sum = (+p.mem_used || 0) + (+p.mem_buffers || 0)
                  + (+p.mem_cached || 0) + (+p.mem_free || 0);
        if (sum > max) max = sum;
      }
      return max;
    },
    // Freshness label for the SNMP chart section. Returns
    // `{age_s, label, stale}` or null when there's no data yet.
    // `stale` is true once age exceeds 2× the host_snmp sampler
    // cadence (~5min), used to amber-tint the label so the
    // operator knows the data hasn't refreshed in a while.
    //
    // #856 — Combined freshness across the two writers feeding the
    // host drawer: the LIFESPAN sampler (writes `host_snmp_samples`,
    // surfaces here as `hist.points[last].ts`) AND the per-request
    // gather path (writes the snapshot `_stale_ts`). Pre-fix this
    // helper read ONLY the sampler's most-recent row, so when the
    // gather path successfully merged live data 7m ago but the
    // sampler hadn't written a row in 9h (sampler paused / gated /
    // its INSERT condition not met for this host), the label said
    // "Last sample 9h ago" while the snapshot banner — sourced from
    // `_stale_ts` — said "Last live data 7m ago". Two surfaces
    // disagreed about the SAME host. Post-fix: take the
    // most-recent of (sampler ts, snapshot ts) so both surfaces
    // report the same value. The downstream root cause (sampler
    // lagging the gather path) is a separate concern; this is the
    // honest-UI fix that always reflects the operator's freshest
    // signal.
    snmpHistoryFreshness(h) {
      if (!h) return null;
      const hist = this.hostSnmpHistory[h.id];
      const samplerTs = (hist && Array.isArray(hist.points) && hist.points.length)
        ? Number((hist.points[hist.points.length - 1] || {}).ts
                 || (hist.points[hist.points.length - 1] || {}).t || 0)
        : 0;
      const snapshotTs = Number(h._stale_ts || 0);
      const ts = Math.max(samplerTs, snapshotTs);
      if (!ts || !Number.isFinite(ts) || ts <= 0) return null;
      const ageS = Math.max(0, Math.round(Date.now() / 1000 - ts));
      let label;
      if (ageS < 60) label = ageS + 's';
      else if (ageS < 3600) label = Math.round(ageS / 60) + 'm';
      else label = Math.round(ageS / 3600) + 'h';
      // `source` lets the template render a tooltip explaining which
      // writer the timestamp came from — operators tracing
      // freshness disagreements can see at a glance whether the
      // value came from the sampler or the snapshot.
      const source = (samplerTs >= snapshotTs) ? 'sampler' : 'snapshot';
      return { age_s: ageS, label, stale: ageS > 600, source };
    },
    // Build a polyline `points` attribute from a series of values.
    // Normalises against `max` (default = max value in series) so the
    // chart spans the full SVG viewBox. ViewBox 420×120 matches the
    // existing Beszel / NE chart cards so the SNMP charts
    // render at the same scale + gridline density as their cousins.
    // #815 — Unified drawer time-domain for every host-drawer chart.
    // Returns the [tMinSec, tMaxSec] window the picker has selected
    // (1h / 6h / 24h / 7d) anchored to "now" so every chart renders
    // against the SAME visual x-axis. Pre-#815 each helper computed
    // x by sample-INDEX (`x = i / (n-1) * w`), which made each
    // chart's leftmost pixel mean "the oldest sample I have" — varied
    // across providers (NE 4 samples / 38 min, Beszel 12 / 55 min,
    // SNMP 60 / 60 min) so spikes never aligned vertically across
    // cards. Time-based x makes leftmost = "now - rangeMs" universally
    // and a sparse provider's polyline simply starts mid-axis where
    // its earliest sample landed. Width / height kept at the existing
    // `_snmpPolyPoints` constants (w=420 hh=120) so card paddings &
    // axis labels stay calibrated.
    _drawerTimeDomain() {
      const rangeHours = Number(this.hostHistoryRange) || 1;
      const tMaxSec = Math.floor(Date.now() / 1000);
      const tMinSec = tMaxSec - (rangeHours * 3600);
      return { tMinSec, tMaxSec, w: 420, hh: 120 };
    },
    // Auto-derive a "this is a gap" threshold (seconds) from the actual
    // sample cadence. Median Δt × 2.5 covers natural sampler jitter
    // (tick alignment / skew, occasional doubled ticks) but flags
    // genuine outage-class gaps. 60s floor so a fast sampler doesn't
    // false-positive on a one-tick hiccup. Used by every chart helper
    // to break the rendered line at long gaps so a multi-hour host
    // outage (power failure, network drop, manual shutdown) renders as
    // a visual discontinuity instead of one fake-smooth line bridging
    // the dead period. Provider-agnostic — works whether the series
    // came from Beszel (variable tier cadence), NE (5min), Ping
    // (configurable), or SNMP (5min default), because the threshold is
    // derived from the data itself rather than hard-coded per source.
    _detectGapThresholdSec(times) {
      if (!times || times.length < 3) return null;
      const deltas = [];
      let prev = 0;
      for (const t of times) {
        const ts = Number(t) || 0;
        if (!ts) continue;
        if (prev > 0) {
          const dt = ts - prev;
          if (dt > 0) deltas.push(dt);
        }
        prev = ts;
      }
      if (deltas.length < 2) return null;
      deltas.sort((a, b) => a - b);
      const median = deltas[Math.floor(deltas.length / 2)];
      return Math.max(60, median * 2.5);
    },
    _snmpPolyPoints(values, max, opts) {
      // null-aware. Skip-don't-synthesize: when a counter-rate
      // helper passes a null at a wrap / reboot / gap point, OMIT it
      // from the polyline points string instead of plotting it as 0.
      // CPU per-core / load polylines that fill empty slots with 0
      // still work because 0 IS a meaningful "load=0" value for those
      // series. Pre-fix the polyline string contained only the valid
      // points so the rendered line bridged across nulls — visually
      // identical to a steady ramp, hiding the gap. Most chart cards
      // now use `_snmpPathGapped` for the SVG `<path d>` attribute
      // (M commands at every gap so genuine outages render as breaks
      // instead of bridges). This helper stays for the legacy
      // `<polyline points>` consumers (CPU per-core / load) which
      // never emit nulls.
      //
      // #815 unified time-domain — when `opts.times` (parallel array
      // of epoch SECONDS) is supplied, x is computed against the
      // drawer-shared [tMin, tMax] window so this chart's pixel
      // coordinates match every other chart in the open drawer. When
      // `times` is absent, falls back to the legacy index-based
      // scaling for un-migrated callers.
      if (!values || !values.length) return '';
      const m = max !== undefined ? max : Math.max(0.0001, ...values.filter(v => v != null));
      const n = values.length;
      const times = opts && opts.times;
      const dom = (times && times.length === n) ? this._drawerTimeDomain() : null;
      const w = dom ? dom.w : 420;
      const hh = dom ? dom.hh : 120;
      const span = dom ? Math.max(1, dom.tMaxSec - dom.tMinSec) : 1;
      const out = [];
      for (let i = 0; i < n; i++) {
        const v = values[i];
        if (v == null) continue;
        let x;
        if (dom) {
          const ts = Number(times[i]) || 0;
          if (!ts) continue;
          x = ((ts - dom.tMinSec) / span) * w;
          if (x < 0 || x > w) continue;
        } else {
          x = (i / Math.max(1, n - 1)) * w;
        }
        const y = hh - ((+v || 0) / m) * hh;
        out.push(`${x.toFixed(1)},${y.toFixed(1)}`);
      }
      return out.join(' ');
    },
    // Gap-aware SVG path builder. Same scaling as `_snmpPolyPoints`
    // but emits an SVG path `d` string with `M` (moveto) at every gap
    // so a single `<path>` element renders as multiple disconnected
    // segments — genuine null gaps appear as visual breaks instead of
    // straight-line bridges. Cheaper than rendering N `<polyline>`
    // elements when the series has many gaps. Consumers swap their
    // `<polyline points="...">` for `<path d="...">` and bind the
    // result here.
    //
    // #815 unified time-domain — same `opts.times` contract as
    // `_snmpPolyPoints`.
    _snmpPathGapped(values, max, opts) {
      if (!values || !values.length) return '';
      const m = max !== undefined ? max : Math.max(0.0001, ...values.filter(v => v != null));
      const n = values.length;
      const times = opts && opts.times;
      const dom = (times && times.length === n) ? this._drawerTimeDomain() : null;
      const gapThr = times ? this._detectGapThresholdSec(times) : null;
      const w = dom ? dom.w : 420;
      const hh = dom ? dom.hh : 120;
      const span = dom ? Math.max(1, dom.tMaxSec - dom.tMinSec) : 1;
      const out = [];
      let needMove = true;
      let prevTs = 0;
      for (let i = 0; i < n; i++) {
        const v = values[i];
        if (v == null) {
          // Null = gap. Next valid point starts a fresh sub-path.
          needMove = true;
          prevTs = 0;
          continue;
        }
        let x;
        let curTs = 0;
        if (dom) {
          curTs = Number(times[i]) || 0;
          if (!curTs) { needMove = true; prevTs = 0; continue; }
          x = ((curTs - dom.tMinSec) / span) * w;
          if (x < 0 || x > w) { needMove = true; prevTs = 0; continue; }
        } else {
          x = (i / Math.max(1, n - 1)) * w;
        }
        // Time-gap break — when consecutive valid samples are separated
        // by > gapThreshold seconds, emit M (moveto) instead of L
        // (lineto) so the rendered line breaks. Catches multi-hour host
        // outages where the underlying sampler simply stopped writing
        // rows for a stretch — pre-fix the line bridged the dead period
        // as a single fake-smooth segment, painting "down for hours" as
        // "fading from X to Y".
        if (!needMove && gapThr && prevTs > 0 && curTs > 0 && (curTs - prevTs) > gapThr) {
          needMove = true;
        }
        const y = hh - ((+v || 0) / m) * hh;
        out.push(`${needMove ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`);
        needMove = false;
        prevTs = curTs;
      }
      return out.join(' ');
    },
    // Min/Max/Last over a series field. Returns null when the
    // series is empty so the legend's `x-show` short-circuits to
    // hidden. Mirrors the shape of `hostMetricStats(...)` so the
    // template binding reads the same.
    snmpStats(hostId, key, idx) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (!series.length) return null;
      let pick;
      if (key === 'cpu_per_core' && typeof idx === 'number') {
        pick = (p) => (p.cpu_per_core || [])[idx];
      } else {
        pick = (p) => p[key];
      }
      const vals = series.map(pick).filter(v => v !== null && v !== undefined);
      if (!vals.length) return null;
      let min = Infinity, max = -Infinity;
      for (const v of vals) {
        if (v < min) min = v;
        if (v > max) max = v;
      }
      // Operator-reported on a Ubiquiti USW Enterprise switch:
      // drawer "Used X%" legend showed 100% while the row CPU bar
      // read 0%. Root cause: this helper used to return the last
      // NON-NULL value from `vals` (the filtered list), which on
      // hosts whose probes intermittently return CPU and then go
      // null can be a stale 100% from hours ago — disconnected
      // from the latest actual probe. The chart line correctly
      // plots fresh nulls as 0; the legend should match. Now scan
      // the FULL series back-to-front for the most recent
      // non-null point — this is "the most recent KNOWN value at
      // or before now" rather than "the last value we ever
      // observed". For all-non-null series the result is
      // identical to the previous behaviour. `lastIdx` exposed
      // alongside so callers can detect "last sample is stale"
      // (when `lastIdx < series.length - 1`).
      let last = null, lastIdx = -1;
      for (let i = series.length - 1; i >= 0; i--) {
        const v = pick(series[i]);
        if (v !== null && v !== undefined) {
          last = v;
          lastIdx = i;
          break;
        }
      }
      const stale = lastIdx >= 0 && lastIdx < series.length - 1;
      return { min, max, last, lastIdx, stale };
    },
    // Five evenly-spaced X-axis timestamp labels for the
    // bottom of the chart. Matches the existing `xAxisFromSeries`
    // call shape on Beszel / NE cards.
    //
    // #815 — Switched to drawer-unified [tMin, tMax] window so SNMP
    // chart axis labels match Beszel / NE / Ping cards on the same
    // pixel positions. Pre-#815 SNMP labels reflected actual sample
    // timestamps; post-fix they reflect the picker's selected range
    // (1h / 6h / 24h / 7d) anchored to "now".
    snmpXAxis(hostId, n) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      n = n || 5;
      if (series.length < 2) return Array(n).fill('');
      const dom = this._drawerTimeDomain();
      const span = Math.max(1, dom.tMaxSec - dom.tMinSec);
      const out = [];
      for (let i = 0; i < n; i++) {
        const ts = dom.tMinSec + Math.round((i / (n - 1)) * span);
        out.push(this._snmpFmtAxisTime(ts));
      }
      return out;
    },
    _snmpFmtAxisTime(ts) {
      const d = new Date((+ts) * 1000);
      const hh = String(d.getHours()).padStart(2, '0');
      const mm = String(d.getMinutes()).padStart(2, '0');
      return `${hh}:${mm}`;
    },
    // CPU per-core lines — one polyline string per core index.
    snmpCpuPerCoreLines(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (!series.length) return [];
      // Determine core count from the FIRST point that has a non-empty
      // cpu_per_core. Older samples may have fewer cores (e.g. host
      // reboot changed core count); render only the consistent prefix.
      const numCores = (series.find(p => (p.cpu_per_core || []).length) || {}).cpu_per_core?.length || 0;
      const out = [];
      const times = series.map(p => p.ts);
      for (let i = 0; i < numCores; i++) {
        const vals = series.map(p => (p.cpu_per_core || [])[i] ?? 0);
        out.push(this._snmpPathGapped(vals, 100, { times }));
      }
      return out;
    },
    // Returns a SINGLE SVG path-d string with one subpath per core.
    // Each subpath starts with `M` (the gapped-path builder already
    // emits `M ... L ...`), so concatenating them produces a valid
    // path with N disconnected polylines. Avoids the `<template x-for>`
    // inside SVG where Alpine 3.x's x-for scope doesn't always
    // establish the iteration variable cleanly (browser HTML parsers
    // don't treat `<template>` as a real template element when it's
    // inside the SVG namespace, which can leave the inner directive
    // evaluated against the parent scope where the iteration var is
    // undefined).
    snmpCpuPerCoreCombinedLine(hostId) {
      const lines = this.snmpCpuPerCoreLines(hostId);
      return lines && lines.length ? lines.join(' ') : '';
    },
    snmpCpuUsedPctLine(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      const vals = series.map(p => p.cpu_used_pct ?? 0);
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, 100, { times });
    },
    // Operator-flagged: SNMP Load chart should render as % of cores
    // rather than raw `load_1m=0.18` numbers. Converts each load
    // value to a percentage via cores resolved from the host (live
    // `host_cpu_per_core` length OR cores) — falls back to 1 (treat
    // as single-core) when cores is unknown so behaviour matches
    // the pre-conversion chart for hosts that don't expose cores.
    // 100 % cap so a busy 4-core box (load=8 → 200%) still fits the
    // chart without auto-rescaling the Y-axis.
    snmpCoresFor(hostId) {
      const h = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
      if (h) {
        const c = (h.host_cpu_per_core || []).length || h.cpu_cores || h.cores;
        if (c && c > 0) return c;
      }
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      for (const p of series) {
        const c = (p.cpu_per_core || []).length;
        if (c > 0) return c;
      }
      return 1;
    },
    snmpLoadLine(hostId, key) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      const cores = this.snmpCoresFor(hostId);
      const vals = series.map(p => Math.min(100, ((p[key] ?? 0) / cores) * 100));
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, 100, { times });
    },
    snmpLoadMax(hostId) {
      // Always 100 % now — chart Y-axis stays fixed so a busy machine
      // doesn't auto-rescale every tick.
      return 100;
    },
    // Legend / live value as percent (0..100, capped). Operator wants
    // "12 %" not "0.18".
    snmpLoadPctLive(hostId, liveLoad) {
      const cores = this.snmpCoresFor(hostId);
      const pct = Math.max(0, Math.min(100, ((+liveLoad || 0) / cores) * 100));
      return pct;
    },
    snmpMemArea(hostId, key) {
      // For the memory chart — render each layer as a polyline, scaled
      // against mem_total. `key` ∈ {used, buffers, cached, free}.
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (!series.length) return '';
      // Normalise against the largest mem_total seen (handles probes
      // pre/post a memory hot-add cleanly).
      let maxTotal = 0;
      for (const p of series) maxTotal = Math.max(maxTotal, p.mem_total || 0);
      if (!maxTotal) return '';
      const fieldKey = 'mem_' + key;
      const vals = series.map(p => p[fieldKey] || 0);
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, maxTotal, { times });
    },
    // derive per-tick throughput series in bytes/sec from the
    // cumulative IF-MIB ifHCInOctets / ifHCOutOctets samples. Skip-
    // don't-synthesize: out-of-bounds deltas (negative = counter
    // reset / reboot, near-zero timespan, hour-plus gap, absurd byte
    // delta) become 0 in the rendered series so a flat segment is
    // visibly distinct from a real "host idle" zero. First point is
    // always 0 because there's no predecessor to diff against. dir ∈
    // {'rx', 'tx'}.
    snmpThroughputBpsSeries(hostId, dir) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (series.length < 2) return [];
      const fieldKey = 'net_' + dir + '_total_bytes';
      // skip-don't-synthesize: out-of-bounds deltas (counter
      // wrap, reboot, gap, null) emit `null` so `_snmpPolyPoints`
      // omits the point from the polyline. Pre-fix this filled with
      // 0 — visually identical to a real "0 bps idle" segment,
      // burying the wrap signal. First-sample slot stays null too
      // (no predecessor to diff against).
      const out = new Array(series.length).fill(null);
      for (let i = 1; i < series.length; i++) {
        const a = series[i - 1], b = series[i];
        const dt = (b.ts || 0) - (a.ts || 0);
        const av = a[fieldKey], bv = b[fieldKey];
        if (av == null || bv == null) continue;
        if (dt < 1 || dt > 3600) continue;       // gap or doubled tick
        const db = bv - av;
        if (db < 0 || db > 10 * 1024 * 1024 * 1024) continue;   // wrap / reboot / 10 GB cap
        out[i] = db / dt;
      }
      return out;
    },
    // APC UPS Output Load % over the picker window.
    // Renders the percentage of UPS capacity in use — e.g. 13% on a
    // 10 kVA Smart-UPS RT means the connected gear is drawing ~1.3 kVA.
    // Reads `load_percent` from `host_snmp_samples` rows; NULL slots
    // (host wasn't a UPS yet, or sample didn't include the OID) are
    // omitted via `_snmpPolyPoints`. Y-axis pinned to 100% so a busy
    // UPS doesn't auto-rescale.
    snmpUpsLoadLine(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (!series.length) return '';
      const vals = series.map(p => (p.load_percent != null ? +p.load_percent : null));
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, 100, { times });
    },
    // True when at least 2 samples have a non-null load_percent —
    // used internally by the chart-body template to switch between
    // the polyline and the "Collecting data" placeholder.
    snmpHasUpsLoadHistory(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      let n = 0;
      for (const p of series) {
        if (p.load_percent != null) {
          n++;
          if (n >= 2) return true;
        }
      }
      return false;
    },
    // True when the UPS Load card should RENDER. Returns true on
    // live host_load_percent OR ≥1 historical sample. Card body uses
    // `snmpHasUpsLoadHistory` to decide between polyline and the
    // "Collecting…" hint. Pre-#820 follow-up the card hid until the
    // sampler accumulated 2 ticks (~10 min on default cadence) — long
    // enough for operators to assume nothing was being recorded.
    snmpHasUpsLoad(hostId) {
      const drawer = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
      if (drawer && typeof drawer.host_load_percent === 'number') return true;
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      for (const p of series) {
        if (p.load_percent != null) return true;
      }
      return false;
    },
    // APC UPS Battery % over the picker window. Same shape as the
    // load helper; pinned to 100%. Renders the discharge curve when
    // the UPS is on battery + the recharge curve afterwards.
    snmpUpsBatteryLine(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (!series.length) return '';
      const vals = series.map(p => (p.battery_percent != null ? +p.battery_percent : null));
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, 100, { times });
    },
    snmpHasUpsBatteryHistory(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      let n = 0;
      for (const p of series) {
        if (p.battery_percent != null) {
          n++;
          if (n >= 2) return true;
        }
      }
      return false;
    },
    snmpHasUpsBattery(hostId) {
      const drawer = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
      if (drawer && typeof drawer.host_battery_percent === 'number') return true;
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      for (const p of series) {
        if (p.battery_percent != null) return true;
      }
      return false;
    },
    // APC UPS Battery temperature (°C) over the picker window. Auto-
    // ranges so a flat-ish 36°C line still has vertical movement
    // — caller passes Math.max(50, observed_max) so a normal-range
    // host renders ~36 / 40 / 50 ticks instead of all-0..100.
    snmpUpsBatteryTempLine(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (!series.length) return '';
      const vals = series.map(p => (p.battery_temp_c != null ? +p.battery_temp_c : null));
      const times = series.map(p => p.ts);
      let m = 0;
      for (const v of vals) if (v != null && v > m) m = v;
      return this._snmpPathGapped(vals, Math.max(50, m), { times });
    },
    snmpUpsBatteryTempMax(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      let m = 0;
      for (const p of series) {
        const v = p.battery_temp_c;
        if (v != null && v > m) m = v;
      }
      return Math.max(50, m);
    },
    snmpHasUpsBatteryTempHistory(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      let n = 0;
      for (const p of series) {
        if (p.battery_temp_c != null) {
          n++;
          if (n >= 2) return true;
        }
      }
      return false;
    },
    snmpHasUpsBatteryTemp(hostId) {
      const drawer = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
      if (drawer && typeof drawer.host_battery_temp_c === 'number') return true;
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      for (const p of series) {
        if (p.battery_temp_c != null) return true;
      }
      return false;
    },
    // Dell server temperature-probe chart helpers (#848 phase 3).
    // Multi-line — one polyline per probe, sharing a single y-axis
    // (max across all probes) so spikes on Inlet vs Exhaust are
    // visually comparable. Each probe gets a distinct hue from a small
    // palette; the legend below the chart pairs name + last reading.
    dellTempProbes(hostId) {
      // Sorted probe metadata for the chart + legend. Returns an array
      // of `{idx, name, points, last_c, color}`. Two sources merge:
      //   1) hostSnmpTempHistory — persisted samples from the sampler
      //      (provides the time-series points for the chart)
      //   2) drawerHost.host_dell_temps — live probe payload from the
      //      most recent /api/hosts/one fetch (provides immediate
      //      readings while history accumulates)
      // The merge lets the chart slot render as soon as the drawer is
      // open — pre-fix the chart card sat at "Collecting data..." until
      // 2 sampler ticks (~10 min) had landed two persisted samples.
      const entry = this.hostSnmpTempHistory[hostId] || {};
      const probes = entry.probes || {};
      const drawer = (this.drawerHost && this.drawerHost.id === hostId)
                     ? this.drawerHost : null;
      const liveTemps = drawer && Array.isArray(drawer.host_dell_temps)
                        ? drawer.host_dell_temps : [];
      const palette = [
        'var(--info)', 'var(--warning)', 'var(--success)',
        'var(--danger)', 'var(--primary)', '#a78bfa',
        '#ec4899', '#06b6d4',
      ];
      // Build a unified probe map keyed by idx — history rows define
      // the probe set when present; live rows top up any probe that
      // history doesn't know about yet (first-tick scenario where the
      // sampler hasn't run but the drawer probe just landed).
      const merged = {};
      for (const idx of Object.keys(probes)) {
        merged[idx] = { idx, name: (probes[idx] || {}).name || `temp-${idx}`,
                        points: Array.isArray((probes[idx] || {}).points)
                                ? (probes[idx] || {}).points : [] };
      }
      const nowTs = Math.floor(Date.now() / 1000);
      for (const t of liveTemps) {
        const idx = String(t.idx || '');
        if (!idx) continue;
        if (!merged[idx]) {
          merged[idx] = { idx, name: t.name || `temp-${idx}`, points: [] };
        }
        // Append the live reading as a synthetic "now" sample only
        // when history is empty for this probe — otherwise the
        // sampler-persisted points are authoritative for time series.
        if (!merged[idx].points.length && t.celsius != null) {
          merged[idx].points = [{ ts: nowTs, c: +t.celsius }];
        }
      }
      const idxs = Object.keys(merged).sort((a, b) => {
        const na = a.split('.').map(n => +n || 0);
        const nb = b.split('.').map(n => +n || 0);
        for (let k = 0; k < Math.max(na.length, nb.length); k++) {
          const da = na[k] || 0, db = nb[k] || 0;
          if (da !== db) return da - db;
        }
        return 0;
      });
      const out = [];
      let i = 0;
      for (const idx of idxs) {
        const p = merged[idx];
        const pts = p.points;
        let lastC = null;
        for (let j = pts.length - 1; j >= 0; j--) {
          if (pts[j].c != null) { lastC = pts[j].c; break; }
        }
        out.push({
          idx,
          name: p.name,
          points: pts,
          last_c: lastC,
          color: palette[i % palette.length],
        });
        i++;
      }
      return out;
    },
    dellTempMaxC(hostId) {
      let m = 0;
      const entry = this.hostSnmpTempHistory[hostId] || {};
      const probes = entry.probes || {};
      for (const idx of Object.keys(probes)) {
        const pts = (probes[idx] || {}).points || [];
        for (const pt of pts) {
          if (pt.c != null && pt.c > m) m = pt.c;
        }
      }
      // Also consider live drawer readings so the y-axis max stays
      // correct on first-tick scenarios where history is empty but
      // dellTempProbes synthesised single "now" points from the live
      // host_dell_temps payload.
      const drawer = (this.drawerHost && this.drawerHost.id === hostId)
                     ? this.drawerHost : null;
      if (drawer && Array.isArray(drawer.host_dell_temps)) {
        for (const t of drawer.host_dell_temps) {
          if (t && t.celsius != null && +t.celsius > m) m = +t.celsius;
        }
      }
      // Domain floor — 60°C is a sensible upper bound for an
      // idle / lightly-loaded server so a flat 30-40°C line still has
      // visible vertical movement against the same axis as a loaded
      // host hitting 65-70°C.
      return Math.max(60, m);
    },
    dellTempLine(hostId, points) {
      // SVG path `d` for one probe's series, normalised against the
      // chart's shared y-max. The viewBox is 0 0 420 120 with
      // preserveAspectRatio="none", so x ∈ [0, 420] spans the full
      // chart width and y ∈ [0, 120] spans the full chart height.
      //
      // Single-point fallback: when only one valid sample exists,
      // _snmpPathGapped maps the synthetic "now" timestamp to the right
      // edge of the time domain — producing `M ~420,y` with no `L`
      // follow-up, which draws nothing AND a 4-pixel nub extension
      // would clip past the right edge. Instead, when the series has
      // exactly one valid point, render a full-width horizontal line
      // at the current temperature so the user sees an actual reading.
      // The full polyline replaces this once a second sample lands.
      if (!Array.isArray(points) || !points.length) return '';
      const validPts = points.filter(p => p && p.c != null);
      const max = this.dellTempMaxC(hostId);
      if (validPts.length === 1) {
        const c = +validPts[0].c;
        const y = Math.max(0, Math.min(120, 120 - (c / max) * 120));
        return `M 0,${y.toFixed(1)} L 420,${y.toFixed(1)}`;
      }
      const vals = points.map(p => (p && p.c != null ? +p.c : null));
      const times = points.map(p => p && p.ts);
      return this._snmpPathGapped(vals, max, { times });
    },
    dellHasTempHistory(hostId) {
      // Mount the chart slot whenever a probe has at least one sample
      // OR the live drawer payload has a `host_dell_temps` reading we
      // can synthesize a "now" point from (`dellTempProbes` does the
      // merge). dellTempLine handles single-point series by appending a
      // 4-pixel horizontal nub so the line is visible before history
      // accumulates the second point. Pre-fix this required ≥2 persisted
      // points per probe, leaving the "Collecting data..." placeholder
      // up for 5-10 minutes after deploy — the legend kept showing live
      // values while the chart slot stayed empty, which read as broken.
      const entry = this.hostSnmpTempHistory[hostId] || {};
      const probes = entry.probes || {};
      for (const idx of Object.keys(probes)) {
        if (((probes[idx] || {}).points || []).length >= 1) return true;
      }
      const drawer = (this.drawerHost && this.drawerHost.id === hostId)
                     ? this.drawerHost : null;
      if (drawer && Array.isArray(drawer.host_dell_temps)) {
        for (const t of drawer.host_dell_temps) {
          if (t && t.celsius != null) return true;
        }
      }
      return false;
    },
    dellHasTemps(hostId) {
      // Card-render gate. True when the live drawer payload has
      // host_dell_temps OR the temp history has any rows. Mirrors the
      // UPS gate's "live OR history" predicate so the card stays
      // mounted when the live probe is briefly empty.
      const drawer = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
      if (drawer && Array.isArray(drawer.host_dell_temps) && drawer.host_dell_temps.length) return true;
      const entry = this.hostSnmpTempHistory[hostId] || {};
      const probes = entry.probes || {};
      return Object.keys(probes).length > 0;
    },
    snmpThroughputLine(hostId, dir) {
      const vals = this.snmpThroughputBpsSeries(hostId, dir);
      if (!vals.length) return '';
      const m = this.snmpThroughputMaxBps(hostId);
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      const times = series.map(p => p.ts);
      // Gap-aware path so wrap / reboot / gap nulls render as visual
      // breaks instead of straight-line bridges. Consumer must use
      // SVG <path :d> not <polyline :points>.
      return this._snmpPathGapped(vals, m || 1, { times });
    },
    snmpThroughputMaxBps(hostId) {
      const rx = this.snmpThroughputBpsSeries(hostId, 'rx');
      const tx = this.snmpThroughputBpsSeries(hostId, 'tx');
      let m = 0;
      for (const v of rx) if (v > m) m = v;
      for (const v of tx) if (v > m) m = v;
      return m;
    },
    snmpThroughputLast(hostId, dir) {
      const vals = this.snmpThroughputBpsSeries(hostId, dir);
      for (let i = vals.length - 1; i >= 0; i--) {
        if (vals[i] > 0) return vals[i];
      }
      return 0;
    },
    snmpHasThroughput(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (series.length < 2) return false;
      for (const p of series) {
        if (p.net_rx_total_bytes != null || p.net_tx_total_bytes != null) return true;
      }
      return false;
    },
    // printer pages-printed sparkline. Series of pages-per-day
    // rates derived from adjacent samples of `printer_page_count`
    // (Printer-MIB prtMarkerLifeCount, monotonic). Skip-don't-
    // synthesize on out-of-bounds deltas (negative = printer reset
    // / counter rollover, near-zero timespan, hour-plus gap, > 10 000
    // pages = absurd-rate guard against agent glitches).
    snmpPagesPerDaySeries(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (series.length < 2) return [];
      const out = new Array(series.length).fill(0);
      for (let i = 1; i < series.length; i++) {
        const a = series[i - 1], b = series[i];
        const dt = (b.ts || 0) - (a.ts || 0);
        const av = a.printer_page_count, bv = b.printer_page_count;
        if (av == null || bv == null) continue;
        if (dt < 1 || dt > 3600) continue;
        const dp = bv - av;
        if (dp < 0 || dp > 10000) continue;
        out[i] = (dp / dt) * 86400;     // pages per day
      }
      return out;
    },
    snmpPagesPerDayLine(hostId) {
      const vals = this.snmpPagesPerDaySeries(hostId);
      if (!vals.length) return '';
      const m = Math.max(0.0001, ...vals);
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, m || 1, { times });
    },
    snmpPagesPerDayMax(hostId) {
      const vals = this.snmpPagesPerDaySeries(hostId);
      let m = 0;
      for (const v of vals) if (v > m) m = v;
      return m;
    },
    snmpPagesPerDayLast(hostId) {
      const vals = this.snmpPagesPerDaySeries(hostId);
      for (let i = vals.length - 1; i >= 0; i--) {
        if (vals[i] > 0) return vals[i];
      }
      return 0;
    },
    // Banner copy — derives the per-tick interval from the SAME tunable
    // the SNMP sampler uses. Resolution order :
    //   1. tuning_snmp_sample_interval_seconds when > 0 (SNMP runs at
    //      its own cadence, distinct from the global Beszel/NE one).
    //   2. tuning_stats_sample_interval_seconds (legacy / inherited
    //      cadence when the SNMP-specific knob is 0).
    //   3. 300s fallback when client_config hasn't hydrated.
    // Both knobs are surfaced via /api/me's client_config; the literal
    // `{minutes}` placeholder never reaches the rendered DOM.
    snmpWarmingUpText() {
      const cc = (this.me && this.me.client_config) || {};
      const snmpSec  = +cc.snmp_sample_interval_seconds || 0;
      const statsSec = +cc.stats_sample_interval_seconds || 0;
      const sec = snmpSec > 0 ? snmpSec : (statsSec || 300);
      const minutes = Math.max(1, Math.round(sec / 60));
      let s = this.t('host_drawer.snmp_charts.warming_up', { minutes });
      // Defensive: if i18n's interpolation didn't substitute (older
      // browser-cached bundle, helper called pre-load, etc.) — replace
      // manually so the literal `{minutes}` placeholder never reaches
      // the operator's screen.
      if (typeof s === 'string' && s.indexOf('{minutes}') >= 0) {
        s = s.split('{minutes}').join(String(minutes));
      }
      return s;
    },
    // Legend value for SNMP load lines. Operator-flagged: reading
    // `last` showed 0.00 while the chart line clearly had a non-zero
    // peak (the most recent tick happened to be 0). Show MAX over the
    // window so the legend matches what the eye sees on the chart.
    snmpLoadLegendValue(hostId, key, liveValue) {
      const live = +liveValue;
      if (Number.isFinite(live) && live > 0) return live.toFixed(2);
      const stats = this.snmpStats(hostId, key);
      if (stats && stats.max > 0) return stats.max.toFixed(2);
      return (live || 0).toFixed(2);
    },
    // List of providers actually wired ON THIS HOST — drives the
    // "no data" banner so it only names providers the operator has
    // configured a target for on the specific row. Pre-fix the
    // banner read from the global `host_stats_source` CSV which
    // claimed Webmin/SNMP were checked even on hosts that had no
    // Webmin/SNMP target name set — confusing.
    enabledProvidersList(h) {
      if (!h) return this.t('hosts_extra.no_data.no_providers') || 'any provider';
      const out = [];
      if ((h.beszel_id || h.beszel_name || '').trim()) out.push('Beszel');
      if ((h.pulse_name || '').trim()) out.push('Pulse');
      if ((h.ne_url || '').trim()) out.push('node-exporter');
      if ((h.webmin_name || h.webmin_url || '').trim()) out.push('Webmin');
      if (h.snmp_enabled === true && (h.snmp_name || '').trim()) out.push('SNMP');
      if (h.ping_enabled === true) out.push('Ping');
      if (!out.length) return this.t('hosts_extra.no_data.no_providers') || 'any provider';
      if (out.length === 1) return out[0];
      if (out.length === 2) return out[0] + ' or ' + out[1];
      return out.slice(0, -1).join(', ') + ', or ' + out[out.length - 1];
    },
    snmpHasPageCount(hostId, h) {
      // Show the printer pages chart ONLY when the host looks like a
      // real printer — APC UPSes, switches and other non-printer SNMP
      // gear can occasionally answer OID 1.3.6.1.2.1.43.10.2.1.4.1.1
      // (prtMarkerLifeCount) with 0, which previously triggered the
      // chart card on a UPS. Gate on a printer signature: at least one
      // supply row OR a console message OR a NON-ZERO page count from
      // EITHER the live row OR history (DB-backed fast-path so the
      // card appears immediately on drawer open instead of waiting
      // for the 10-30s live SNMP probe to land).
      if (!h) return false;
      const supplies = h.printer_supplies || [];
      const hasSupplies = Array.isArray(supplies) && supplies.length > 0;
      const hasConsole = !!(h.printer_console_msg && String(h.printer_console_msg).trim());
      const hasNonZeroLive = (+h.printer_page_count || 0) > 0;
      const hasNonZeroHist = this.snmpLatestPageCount(hostId) > 0;
      if (!hasSupplies && !hasConsole && !hasNonZeroLive && !hasNonZeroHist) return false;
      if (h.printer_page_count != null) return true;
      return hasNonZeroHist;
    },
    // read the most-recent non-null `printer_page_count` from
    // the persisted SNMP history. Lets the Printer card surface a
    // lifetime page count immediately on drawer open from DB-backed
    // history instead of waiting for the live SNMP probe (10-30s
    // round trip). Falls back to 0 when no history exists.
    snmpLatestPageCount(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      for (let i = series.length - 1; i >= 0; i--) {
        const v = series[i].printer_page_count;
        if (v != null && v > 0) return v;
      }
      return 0;
    },
    async loadHostHistory(systemId, hostId) {
      // Preserve whatever series we already have so the chart doesn't
      // flicker back to "Collecting data…" between range-picker
      // clicks. Only the ``loading`` flag flips; the visible line
      // stays put until fresh data lands, then swaps in place.
      // Cache key: prefer beszel_id when present (Beszel path), else
      // fall back to the curated host_id (NE-only path). Every chart
      // helper looks up by this same key, so the templates pass
      // `hostHistoryKey(h)` instead of bare `h.beszel_id`.
      if (!hostId) {
        const host = (this.hosts || []).find(h => h.beszel_id === systemId);
        hostId = host ? host.id : '';
      }
      const cacheKey = systemId || hostId;
      if (!cacheKey) return;
      const prev = this.hostHistory[cacheKey] || {};
      this.hostHistory[cacheKey] = {
        loading: true,
        error: prev.error || '',
        series: Array.isArray(prev.series) ? prev.series : [],
        collectors: prev.collectors || null,
        loadedAt: prev.loadedAt || 0,
      };
      try {
        const qs = {
          system_id: systemId || '',
          hours: String(this.hostHistoryRange),
        };
        if (hostId) qs.host_id = hostId;
        const params = new URLSearchParams(qs);
        const r = await fetch('/api/hosts/history?' + params.toString());
        if (!r.ok) {
          this.hostHistory[cacheKey] = {
            loading: false,
            error: `HTTP ${r.status}`,
            series: prev.series || [],  // keep previous on HTTP error
            collectors: prev.collectors || null,
            loadedAt: prev.loadedAt || 0,
          };
          return;
        }
        const d = await r.json();
        const next = Array.isArray(d.series) ? d.series : [];
        // Stamp loadedAt on every successful HTTP 2xx, regardless of
        // whether the series came back populated. Operator expectation
        // is "when did we last poll the backend" (matching their
        // statsInterval cadence) — an occasional empty-series reply
        // (hub briefly returning [] during a restart, or a host with
        // no samples in the selected window) shouldn't make the
        // freshness label drift past one poll cycle. The chart
        // VALUES still preserve `prev.series` on empty so the visible
        // line doesn't blank, but the timestamp follows fetches.
        // (#365 followup — was previously gated on `next.length`,
        // which made the label appear stuck whenever a tick happened
        // to land an empty reply.)
        const stamp = Date.now();
        this.hostHistory[cacheKey] = {
          loading: false,
          error: d.error || '',
          // Only overwrite on a non-empty response. A transient empty
          // reply (hub rebooting, rate-limit) shouldn't blank a chart
          // that was already populated.
          series: next.length ? next : (prev.series || []),
          // NE-only path returns a `collectors` dict per #347 telling
          // us whether each metric ever produced a non-null sample in
          // the window. Beszel path doesn't include it; null = unknown.
          collectors: d.collectors || null,
          loadedAt: stamp,
        };
      } catch (e) {
        this.hostHistory[cacheKey] = {
          loading: false,
          error: e.message,
          series: prev.series || [],
          collectors: prev.collectors || null,
          loadedAt: prev.loadedAt || 0,
        };
      }
    },
    // Subtle freshness label for the host-drawer chart-grid header
    //. Returns a short translated string like "Updated 2m ago"
    // or empty when the cache hasn't seen a successful fetch yet
    // (caller hides the line). Reads `hostHistoryNow` (ticked every
    // 30s) so the label stays current without re-fetching the data.
    hostHistoryFreshness(h) {
      if (!h) return '';
      const key = this.hostHistoryKey(h);
      const entry = this.hostHistory[key];
      if (!entry || !entry.loadedAt) return '';
      // Don't render "Updated Xs ago" on a permanently-flat series.
      // A SNMP-only host with no Beszel/NE wired keeps `loadedAt`
      // updated by the polling loop even though the series is
      // empty / all-zero — operator reads "Updated 2s ago" as
      // "fresh data" but there's nothing to look at. Suppress the
      // freshness label when fewer than 2 history points exist OR
      // when every point is zero across the canonical metric keys.
      const series = entry.series || [];
      if (series.length < 2) return '';
      const sentinel = ['cpu','mp','dp','net','dr','dw','la1_pct','temp_max','gpu_pwr','gpu_usage','gpu_vram_pct'];
      let hasData = false;
      for (const r of series) {
        for (const k of sentinel) {
          if ((+r[k] || 0) > 0) { hasData = true; break; }
        }
        if (hasData) break;
      }
      if (!hasData) return '';
      // `hostHistoryNow` is bumped on a 30s timer; touching it inside
      // the getter means Alpine re-evaluates whenever it ticks.
      const now = this.hostHistoryNow || Date.now();
      const ageMs = Math.max(0, now - entry.loadedAt);
      const ageS = Math.floor(ageMs / 1000);
      if (ageS < 60) {
        return this.t('hosts_extra.metrics.last_updated_seconds', { count: ageS });
      }
      if (ageS < 3600) {
        return this.t('hosts_extra.metrics.last_updated_minutes', {
          count: Math.floor(ageS / 60),
        });
      }
      return this.t('hosts_extra.metrics.last_updated_hours', {
        count: Math.floor(ageS / 3600),
      });
    },
    // UX-ENH-002 / absolute-time tooltip companion for the
    // relative "Updated Xs ago" label. Operators correlating a chart
    // anomaly with Grafana / Prometheus dashboards need a stable
    // anchor — the relative label drifts every second, the ISO string
    // doesn't.
    hostHistoryFreshnessAbsolute(h) {
      if (!h) return '';
      const key = this.hostHistoryKey(h);
      const entry = this.hostHistory[key];
      if (!entry || !entry.loadedAt) return '';
      try {
        return new Date(entry.loadedAt).toISOString().replace(/\.\d{3}Z$/, 'Z');
      } catch (_) {
        return '';
      }
    },
    // True only when we KNOW the named NE collector is missing for this
    // host (sampler walked the window and never saw a non-null value).
    // Returns false for Beszel hosts (no `ne_url`), Beszel+NE hybrids
    // (history fetched via Beszel path → no collectors dict), and
    // freshly-loaded hosts whose first /api/hosts/history reply hasn't
    // landed yet. Drives the Disk I/O / Network "enable the collector"
    // empty-state branches in the host drawer.
    hostCollectorMissing(h, name) {
      if (!h || !h.ne_url) return false;
      const key = this.hostHistoryKey(h);
      const c = this.hostHistory[key] && this.hostHistory[key].collectors;
      if (!c) return false;
      return c[name] === false;
    },
    // Resolve the right hostHistory[] key for one host. Beszel-mapped
    // hosts use the Beszel system id (legacy behaviour); NE-only hosts
    // fall back to the curated hosts_config id (the same id the
    // host_metrics_sampler keys its rows on). Returns '' when neither
    // path is available — chart helpers short-circuit on falsy keys.
    hostHistoryKey(h) {
      if (!h) return '';
      return h.beszel_id || h.id || '';
    },

    // separate key namespace for the ping-latency drawer chart.
    // Stored as a sibling slot in `hostHistory` so the existing chart
    // helpers (`hostChart` / `hostChartMax` / `hostMetricStats`) work
    // unmodified — they read `entry.series[<idx>][key]`, where key is
    // `'rtt'` for the ping series. Using `hostHistoryKey` directly
    // would pollute the host's main history slot (which carries
    // cpu/mp/dp/nr/ns from Beszel/NE on different timestamps), so the
    // ping series gets its own `ping:<id>` namespace.
    hostPingHistoryKey(h) {
      if (!h || !h.id) return '';
      return 'ping:' + h.id;
    },

    // fetch /api/hosts/{id}/ping/history for the host whose
    // drawer is open and store as `entry.series` on a separate
    // namespace so the existing chart helpers work without changes.
    // Mirrors `loadHostHistory`'s shape: stamp `loadedAt` for the
    // freshness label, leave the previous series in place on a
    // network blip (no wholesale array reassignment).
    async loadHostPingHistory(hostId) {
      if (!hostId) return;
      const key = 'ping:' + hostId;
      // Honour the shared host-history range picker (#343 follow-up).
      // Was hardcoded to ?hours=24; now reads `hostHistoryRange` so
      // the ping series re-fetches with the same window as CPU /
      // Memory / Disk / Net when the operator clicks 1h / 6h / 24h / 7d.
      const hours = Math.max(1, Math.min(168, Number(this.hostHistoryRange) || 24));
      try {
        const r = await fetch('/api/hosts/' + encodeURIComponent(hostId) + '/ping/history?hours=' + hours);
        if (!r.ok) return;
        const d = await r.json();
        // Field name `t` matches the convention `loadHostHistory` uses
        // (and what `xAxisFromSeries` reads at s[idx].t). Also keep
        // `ts` for back-compat with any consumer expecting the raw
        // pocketbase column name. Pre-fix the x-axis was blank because
        // xAxisFromSeries pulled from `.t` which was undefined.
        const points = (d.points || []).map(p => ({
          t:        Number(p.ts) || 0,
          ts:       Number(p.ts) || 0,
          rtt:      Number(p.rtt_ms) || 0,
          alive:    !!p.alive,
          loss_pct: Number(p.loss_pct) || 0,
        }));
        if (!this.hostHistory[key]) this.hostHistory[key] = {};
        this.hostHistory[key].series = points;
        this.hostHistory[key].loadedAt = Date.now();
      } catch (e) {
        console.warn('[ping] loadHostPingHistory failed:', e);
      }
    },

    // Permanent-fail tracking helpers. Backend sets
    // `h.sampling_paused: true` on the host record once consecutive
    // probe failures exceed the configured window
    // (`tuning_host_permanent_fail_window_seconds`). Frontend renders an
    // icon in the table + a banner in the drawer with a Resume button.
    hostFailureMinutes(h) {
      if (!h || !h.failure_window_started_at) return 0;
      const elapsed = (Date.now() / 1000) - h.failure_window_started_at;
      return Math.max(0, Math.floor(elapsed / 60));
    },
    // ENH-018 — render "last probe N seconds/minutes/hours ago" so the
    // operator can decide whether to wait or hit Resume on a paused
    // host whose actual outage may have already cleared. Reads
    // ``last_failure_ts`` populated by the sampler on every
    // _record_failure tick. Returns null when the host has no
    // failure-state row (host has never failed) so the banner copy
    // is omitted entirely.
    hostLastFailureAge(h) {
      if (!h || !h.last_failure_ts) return null;
      const elapsed = Math.max(0, Math.floor((Date.now() / 1000) - h.last_failure_ts));
      if (elapsed < 60) {
        return this.t('hosts_extra.permanent_fail.last_error_age_seconds', { seconds: elapsed });
      }
      if (elapsed < 3600) {
        return this.t('hosts_extra.permanent_fail.last_error_age_minutes', { minutes: Math.floor(elapsed / 60) });
      }
      return this.t('hosts_extra.permanent_fail.last_error_age_hours', { hours: Math.floor(elapsed / 3600) });
    },
    async resumeHostSampling(h) {
      if (!h || !h.id || h._resumeBusy) return;
      h._resumeBusy = true;
      // Safety timer — mirrors #810's per-provider safety. Even if
      // `await fetch` hangs forever (browser network freeze, broken
      // proxy holding the connection), the button gets re-enabled
      // after 30s. Prevents the stuck-disabled state operators hit
      // when the page came back from a network blip with the busy
      // flag still set.
      const safetyTimer = setTimeout(() => { h._resumeBusy = false; }, 30000);
      try {
        const r = await fetch('/api/hosts/' + encodeURIComponent(h.id) + '/resume-sampling', {
          method: 'POST',
        });
        if (r.ok) {
          // Optimistic: clear the marker locally so the banner /
          // table icon disappear before the next host-list poll.
          h.sampling_paused = false;
          h.failure_window_started_at = 0;
          h.consecutive_failures = 0;
          h.last_error = '';
          this.showToast(this.t('hosts_extra.permanent_fail.resumed_toast', { host: this.hostDisplayName(h) || h.id }), 'success');
          // #816 — whole-host pause supersedes per-provider pause. When
          // the operator clicks Resume on the whole-host banner, also
          // walk the paused-providers set on this host and clear each
          // (parallel via resumeAllProviders). One click clears every
          // pause layer for the host — pre-fix the operator had to click
          // both Resume sampling AND Resume all to fully recover.
          const stillPaused = this.pausedProvidersFor(h);
          if (stillPaused.length > 0) {
            this.resumeAllProviders(h).catch(() => {});
          }
          // Refresh the host record to pick up backend's view + any
          // new probe results that landed during the API roundtrip.
          // ``force: true`` busts the 10s provider-state cache so the
          // operator sees the post-resume probe immediately instead
          // of waiting out the TTL (ENH-003).
          if (typeof this.refreshHostRow === 'function') {
            this.refreshHostRow(h.id, { force: true }).catch(() => {});
          }
        } else {
          const j = await r.json().catch(() => ({}));
          const detail = j.detail || ('HTTP ' + r.status);
          this.showToast(this.t('hosts_extra.permanent_fail.resume_failed_toast', { host: this.hostDisplayName(h) || h.id, error: detail }), 'error');
        }
      } catch (err) {
        this.showToast(this.t('hosts_extra.permanent_fail.resume_failed_toast', { host: this.hostDisplayName(h) || h.id, error: String(err) }), 'error');
      } finally {
        clearTimeout(safetyTimer);
        h._resumeBusy = false;
      }
    },

    // Tap-driven tooltip state for the chart `?` icons. Holds
    // a `<host_id>:<metric_key>` string when a tooltip is open, null
    // when nothing is showing. Mobile lacks hover so the native :title
    // never fires; this Alpine state powers a click-to-toggle tooltip
    // body that ALSO works on desktop. ESC + click-outside close.
    metricTooltipOpen: null,
    toggleMetricTooltip(h, key) {
      const slot = (h && h.id ? h.id : '') + ':' + key;
      this.metricTooltipOpen = (this.metricTooltipOpen === slot) ? null : slot;
      // After Alpine renders the visible tooltip, smart-place it so a
      // left-column chart card doesn't crop the body off the drawer's
      // start edge. The helper measures and applies an --align-start
      // or --align-center modifier when the default end-anchor would
      // overflow.
      if (this.metricTooltipOpen) {
        this.$nextTick(() => this._adjustMetricTooltipPlacement());
      }
    },
    metricTooltipKey(h, key) {
      return (h && h.id ? h.id : '') + ':' + key;
    },
    _adjustMetricTooltipPlacement() {
      // Find the just-opened tooltip body. Alpine renders x-show via
      // display:none, so the visible one is whichever has computed
      // display !== 'none'.
      const all = document.querySelectorAll('.metric-source-tooltip');
      for (const el of all) {
        // Reset any previous modifier so the measurement reflects the
        // default end-anchored placement, then re-apply if needed.
        el.classList.remove('metric-source-tooltip--align-start');
        el.classList.remove('metric-source-tooltip--align-center');
        if (getComputedStyle(el).display === 'none') continue;
        // Use the host drawer as the clipping reference when present;
        // fall back to the viewport. 8px breathing room on each side.
        const drawer = el.closest('.host-drawer');
        const bounds = drawer ? drawer.getBoundingClientRect() : { left: 0, right: window.innerWidth };
        const PAD = 8;
        let rect = el.getBoundingClientRect();
        if (rect.left < bounds.left + PAD) {
          // Overflowing the start edge — flip to start-anchored.
          el.classList.add('metric-source-tooltip--align-start');
          rect = el.getBoundingClientRect();
          if (rect.right > bounds.right - PAD) {
            // Flipped overflow on the opposite side too — centre it as
            // a final fallback (rare; only on very narrow drawers).
            el.classList.remove('metric-source-tooltip--align-start');
            el.classList.add('metric-source-tooltip--align-center');
          }
        }
      }
    },

    // Per-host definitive source label for the chart-help tooltips
    //. Resolves the actual provider that populates a given
    // metric for THIS host, considering what's mapped on the host
    // record + each metric's provider precedence. Falls back to the
    // generic i18n string when nothing is configured. Network has
    // a special path: when both Beszel and NE are mapped, NE rates
    // back-fill the chart whenever Beszel returns zero (host_net
    // sampler) — surface that explicitly.
    metricSource(h, key) {
      const fallback = this.t('hosts_extra.metrics.source_' + key);
      if (!h) return fallback;
      const beszel = (h.beszel_name || '').trim();
      const beszelId = (h.beszel_id || '').trim();
      const pulse = (h.pulse_name || '').trim();
      const ne = (h.ne_url || '').trim();
      const webmin = (h.webmin_name || h.webmin_url || '').trim();
      const snmp = (h.snmp_name || '').trim();
      const beszelLabel = beszel || beszelId;
      const snmpEnabled = h.snmp_enabled === true && snmp;
      const pingEnabled = h.ping_enabled === true;

      // Provider precedence per metric. Order matters: first match wins
      // for "what populates this for this host". NE-only metrics that
      // Beszel doesn't track are flagged when Beszel is the only source.
      const precedence = {
        // CPU is now sampled by NE too — derived from
        // node_cpu_seconds_total deltas — so NE qualifies as a
        // CPU provider alongside Beszel. SNMP fallback for managed
        // network gear / printers / UPSes.
        cpu:        ['beszel', 'ne', 'snmp'],
        memory:     ['pulse', 'beszel', 'ne', 'snmp'],
        disk:       ['beszel', 'ne'],
        disk_io:    ['beszel', 'ne'],
        load_avg:   ['beszel', 'ne', 'snmp'],
        swap:       ['beszel'],
        // Temperature comes from Beszel only today — node-
        // exporter exposes thermal via `node_hwmon_temp_celsius` but
        // OmniGrid's NE sampler doesn't extract those yet. Add 'ne'
        // to the precedence list when that work lands.
        temperature: ['beszel'],
        // GPU comes from Beszel only — agent reads NVIDIA / AMD GPU
        // stats and emits per-GPU power / usage / VRAM in `stats.g`.
        gpu:         ['beszel'],
        // Network + Bandwidth share the same upstream + the NE-fallback
        // when both are present. SNMP throughput chart (#725b) reads
        // ifHCInOctets / ifHCOutOctets from the device itself.
        network:    ['beszel', 'ne', 'snmp'],
        bandwidth:  ['beszel', 'ne', 'snmp'],
        // SNMP-only metrics — switch / router / printer kit.
        snmp_throughput: ['snmp'],
        snmp_cpu:    ['snmp'],
        snmp_memory: ['snmp'],
        snmp_load:   ['snmp'],
        snmp_pages:  ['snmp'],
        // Dell server temperatures come from the iDRAC's
        // temperatureProbeTable walk (1.3.6.1.4.1.674.10892.5.4.700.20).
        // SNMP-only — no Beszel / NE crossover.
        dell_temps:  ['snmp'],
        // Ping is its own thing — TCP / ICMP probe per host.
        ping:        ['ping'],
      };
      // Ping label resolves the per-host transport + port the user
      // actually configured (or the global defaults when no per-host
      // override is set), AND prefixes the resolved host so the
      // tooltip names WHICH target is being probed. Pre-fix the
      // tooltip read literally "Ping probe (this host)" or "(ICMP)"
      // with no indication of which host the probe targeted.
      // Format: "Ping probe (<host> · ICMP)" or "Ping probe (<host> · TCP :<port>)".
      // Per-host values come from the API row (`ping_transport` /
      // `ping_port`); global defaults come from `me.client_config.ping`;
      // host name comes from `h.label || h.id`.
      let pingLabel = '';
      if (pingEnabled) {
        const cfgGlobal = (this.me && this.me.client_config && this.me.client_config.ping) || {};
        const hostTransport = String((h && h.ping_transport) || '').toLowerCase();
        const useIcmp = (hostTransport === 'icmp')
                     || (hostTransport === '' && !!cfgGlobal.use_icmp);
        // Use the BACKEND-RESOLVED ping target so the tooltip names
        // the actual DNS / IP being probed — not just the curated
        // host_id (which is often a label like "ftth" that doesn't
        // resolve via DNS). `h.ping_target` is computed in
        // `_shape_host_api_row` per the same chain
        // `logic.db.curated_ping_hosts` uses: ssh.fqdn → ssh.host →
        // host.id. Falls back to label/id if the API row predates the
        // ping_target field (defence-in-depth on a stale SPA cache).
        const targetName = (
          (h && h.ping_target)
          || (h && (h.label || h.id || ''))
          || ''
        ).toString().trim();
        let probeStr;
        if (useIcmp) {
          probeStr = 'ICMP';
        } else {
          const port = (h && Number(h.ping_port))
                       || (cfgGlobal.default_port ? Number(cfgGlobal.default_port) : 443);
          probeStr = `TCP :${port}`;
        }
        pingLabel = targetName
          ? `Ping probe (${targetName} · ${probeStr})`
          : `Ping probe (${probeStr})`;
      }
      const providers = {
        beszel: beszelLabel ? `Beszel agent (${beszelLabel})` : '',
        pulse:  pulse       ? `Pulse (${pulse})`              : '',
        ne:     ne          ? `node-exporter (${ne})`         : '',
        webmin: webmin      ? `Webmin (${webmin})`            : '',
        snmp:   snmpEnabled ? `SNMP (${snmp})`                : '',
        ping:   pingLabel,
      };

      const order = precedence[key] || ['beszel', 'pulse', 'ne', 'webmin'];
      const active = order.filter(p => providers[p]);

      // No provider in this metric's precedence is mapped on the host.
      // Two sub-cases:
      //   (a) Host has NO providers at all → use the generic i18n hint
      //       so the operator sees what could populate this metric.
      //   (b) Host has SOME providers but none that supply THIS metric
      //       (e.g. NE-only host viewing the CPU chart, since the NE
      //       sampler doesn't track CPU yet) → name the providers it
      //       DOES have and explain why this chart will be empty.
      if (active.length === 0) {
        const mapped = Object.values(providers).filter(p => p);
        if (mapped.length === 0) return fallback;
        const summary = mapped.join(', ');
        return `This host is mapped to ${summary}, but ${key} is not surfaced by any of those providers — chart will stay empty until you add a provider that tracks it.`;
      }

      const primary = providers[active[0]];

      // Operator-flagged: just the active source — no fallback chain
      // suffix, no dual-source phrasing. The chart is rendered from
      // ONE provider's data; calling out fallbacks confused operators
      // into thinking the chart was somehow merged. Even the Network
      // chart's NE-back-fill nuance (when Beszel returns zero we
      // overlay NE rates from host_net_samples) is suppressed in the
      // tooltip — too much detail for a one-line chip hint.
      return primary;
    },

    // Busy flag. True while the picker's underlying loaders
    // are in flight — bound to each picker button's `:disabled` so
    // operators can't queue rapid 1h → 6h → 24h clicks while the
    // first fetch is still resolving (the SPA used to lag the chart
    // updates and present inconsistent data on slow networks). Cleared
    // in a `finally` even on fetch errors so a flapped picker recovers.
    hostHistoryRangeBusy: false,
    async setHostHistoryRange(hours) {
      // Ignore re-clicks while a previous picker click is still
      // resolving — the buttons are also disabled in the markup but
      // belt-and-braces against keyboard / programmatic invocations.
      if (this.hostHistoryRangeBusy) return;
      this.hostHistoryRange = hours;
      const hrs = Math.max(1, Math.min(168, Number(hours) || 1));
      this.hostHistoryRangeBusy = true;
      // Safety timer — if any single loader hangs forever (network
      // freeze, broken proxy holding the connection), the busy flag
      // would otherwise stick true. 30s mirrors the per-host probe
      // budget ; the operator regains control even when a fetch
      // never resolves.
      const safetyTimer = setTimeout(() => {
        this.hostHistoryRangeBusy = false;
      }, 30000);
      try {
        const tasks = [];
        // Reload the open drawer host's history.
        if (this.drawerHost && (this.drawerHost.beszel_id || this.drawerHost.ne_url || this.drawerHost.pulse_name || this.drawerHost.webmin_name)) {
          tasks.push(this.loadHostHistory(this.drawerHost.beszel_id || '', this.drawerHost.id));
        }
        // Ping chart shares the same range picker. When the operator
        // switches between 1h / 6h / 24h / 7d, the ping series re-fetches
        // alongside CPU / Mem / Disk / Net / Disk-IO.
        if (this.drawerHost && this.drawerHost.ping_enabled) {
          tasks.push(this.loadHostPingHistory(this.drawerHost.id));
        }
        // SNMP charts (CPU per-core, load, memory stacked-area, total
        // throughput, per-port throughput, per-port utilization, printer
        // pages) ALL render off `hostSnmpHistory[hostId].points` +
        // `hostSnmpIfaceHistory[hostId].ifaces`. Pre-fix the range picker
        // was wired ONLY to Beszel/NE/Ping — clicking 6h/24h/7d on an
        // SNMP-only host left the SNMP chart cards stuck at the initial
        // 1h window. Re-fetch both SNMP series here so the picker drives
        // every chart card on the host uniformly.
        if (this.drawerHost && this.drawerHost.snmp_enabled) {
          if (typeof this.loadHostSnmpHistory === 'function') {
            tasks.push(this.loadHostSnmpHistory(this.drawerHost.id, hrs));
          }
          if (typeof this.loadHostSnmpIfaceHistory === 'function') {
            tasks.push(this.loadHostSnmpIfaceHistory(this.drawerHost.id, hrs));
          }
          if (typeof this.loadHostSnmpTempHistory === 'function') {
            tasks.push(this.loadHostSnmpTempHistory(this.drawerHost.id, hrs));
          }
        }
        // Also handle any legacy expanded rows (kept for back-compat —
        // the inline-expansion code path is mostly dead but not yet
        // removed; covering both means the range works wherever the
        // user clicks it from).
        for (const name of (this.hostsExpanded || [])) {
          const host = (this.hosts || []).find(h => h.host === name);
          if (host && (host.beszel_id || host.ne_url || host.pulse_name || host.webmin_name)) {
            tasks.push(this.loadHostHistory(host.beszel_id || '', host.id));
          }
        }
        // `allSettled` so one failing loader doesn't leave the picker
        // stuck disabled — a 5xx on Pulse history shouldn't prevent
        // the operator from swapping back to 1h.
        if (tasks.length) await Promise.allSettled(tasks);
      } finally {
        clearTimeout(safetyTimer);
        this.hostHistoryRangeBusy = false;
      }
    },
    // --- Axis-label helpers used by the metric-card template ---
    _fmtAxisPct(v) { return Math.round(v) + '%'; },
    _fmtAxisBytes(v) {
      if (v <= 0) return '0 B/s';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let u = 0; let n = v;
      while (n >= 1024 && u < units.length - 1) { n /= 1024; u++; }
      const digits = n >= 100 ? 0 : n >= 10 ? 1 : 2;
      return n.toFixed(digits) + ' ' + units[u] + '/s';
    },
    // return a Y-axis bytes formatter pinned to the unit
    // family of `refMax`. Use as `yAxisAuto(max, _fmtAxisBytesAt(max))`
    // so every tick (top + interpolated middles + 0) renders in the
    // same unit, instead of `_fmtAxisBytes`'s per-value auto-scale
    // (operator-flagged: `MB/s` chip with ticks `4.0 MB/s / 2.0 MB/s
    // / 0` is fine, but `KB/s` near-zero ticks mixed with MB/s top
    // tick is broken).
    _fmtAxisBytesAt(refMax) {
      const fmtAt = this.fmtBytesAt.bind(this);
      return (v) => (v <= 0 ? '0 B/s' : fmtAt(v, refMax) + '/s');
    },
    _fmtAxisTime(ts) {
      if (!ts) return '';
      const d = new Date(ts * 1000);
      const hh = String(d.getHours()).padStart(2, '0');
      const mm = String(d.getMinutes()).padStart(2, '0');
      return `${hh}:${mm}`;
    },
    // Produce 3 Y-axis labels (top/middle/bottom) for a chart with a
    // fixed max — percent charts use 100/50/0. Range is [0..100] here.
    yAxisPercent() { return ['100%', '50%', '0%']; },
    // Produce 4 Y-axis labels for an auto-ranged chart (max at top,
    // zero at bottom, two interpolated ticks between).
    yAxisAuto(max, formatter) {
      const fmt = formatter || this._fmtAxisBytes;
      if (!max || max <= 0) return [fmt(0), '', '', fmt(0)];
      return [fmt(max), fmt(max * 0.66), fmt(max * 0.33), fmt(0)];
    },
    // Pick up to 5 evenly-spaced timestamps from the series and format
    // them as HH:MM strings for the X-axis below the chart.
    //
    // #815 — Switched from "evenly-spaced points across the actual
    // sample series" to "evenly-spaced ticks across the drawer's
    // unified [tMin, tMax] window". Pre-#815 a chart with 4 sparse
    // samples got 4 axis labels equal to those sample times; a chart
    // with 60 dense samples got 5 labels evenly spaced through them.
    // Two cards next to each other showed different label times for
    // the same horizontal pixel — making "where was my spike" hard to
    // read across providers. Post-fix every chart's axis labels are
    // [tMin, …, tMax] so the same pixel position means the same
    // wall-clock time across every drawer chart.
    xAxisFromSeries(systemId, slots = 5) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series || entry.series.length < 2) return [];
      const dom = this._drawerTimeDomain();
      const span = Math.max(1, dom.tMaxSec - dom.tMinSec);
      const out = [];
      for (let i = 0; i < slots; i++) {
        const ts = dom.tMinSec + Math.round((i / (slots - 1)) * span);
        out.push(this._fmtAxisTime(ts));
      }
      return out;
    },

    // Build an SVG path for one metric across the host's history. Native
    // line chart, no Chart.js dependency — the series is small and the
    // shape is simple enough that a polyline does the job. The result
    // carries extra fields (area path, gridlines, axis labels) so the
    // template can render a richer chart than just a single polyline.
    hostChart(systemId, key, opts = {}) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series || entry.series.length < 2) return null;
      const W = opts.width || 420;
      const H = opts.height || 100;
      const PAD_X = 4;
      const PAD_T = 6;   // top pad so the peak doesn't clip against the border
      const PAD_B = 4;
      const pts = entry.series.map(r => r[key]);
      let lo = Infinity, hi = -Infinity;
      for (const v of pts) {
        const n = Number(v) || 0;
        if (n < lo) lo = n;
        if (n > hi) hi = n;
      }
      if (!isFinite(lo)) { lo = 0; hi = 1; }
      if (hi - lo < 0.5) { lo = Math.max(0, lo - 0.5); hi = lo + 1; }
      // Optional forced range — e.g. CPU/Mem/Disk charts clamp to 0..100.
      if (opts.min !== undefined) lo = opts.min;
      if (opts.max !== undefined) hi = opts.max;
      // #815 — Unified drawer time-domain. When every point has a `t`
      // timestamp (epoch seconds — set by the loader on every Beszel/NE/
      // Ping fetch), the leftmost pixel of every host-drawer chart now
      // means "the start of the picker window" (now - rangeMs) and the
      // rightmost means "now". Sparse-sample providers (e.g. NE with 4
      // samples/hour) start mid-axis at their earliest sample instead
      // of stretching to fill the full width — letting operators visually
      // compare spikes across cards on the same time-grid. Falls back
      // to the legacy index-based stepping when no timestamps are
      // available (defence-in-depth: the loader has emitted `t` since
      // Beszel landed; index fallback only fires if a future change to
      // hostHistory loaders forgets to stamp it).
      const usableW = W - PAD_X * 2;
      const usableH = H - PAD_T - PAD_B;
      const dom = this._drawerTimeDomain();
      const tSpan = Math.max(1, dom.tMaxSec - dom.tMinSec);
      const haveTimes = entry.series.every(r => Number(r && r.t) > 0);
      const xy = entry.series.map((r, i) => {
        const n = Number(r[key]) || 0;
        let x;
        if (haveTimes) {
          const ts = Number(r.t) || 0;
          // Out-of-range samples render at the clamped edge so the
          // line meets the axis cleanly; the polyline doesn't extend
          // beyond [PAD_X, W - PAD_X].
          x = PAD_X + Math.max(0, Math.min(1, (ts - dom.tMinSec) / tSpan)) * usableW;
        } else {
          const step = usableW / Math.max(1, entry.series.length - 1);
          x = PAD_X + i * step;
        }
        return {
          x,
          y: PAD_T + usableH - ((n - lo) / (hi - lo || 1)) * usableH,
        };
      });
      const points = xy.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
      // Gap-aware path — same coordinates as `points` but emits M
      // (moveto) instead of L (lineto) at every long sampling gap so
      // the rendered line breaks. Catches multi-hour host outages
      // where Beszel / NE / Ping samplers simply stopped writing rows
      // — pre-fix the line bridged the dead period as one fake-smooth
      // segment, painting "down for hours" as "fading from X to Y".
      // Threshold auto-derived from the median sample interval × 2.5
      // so the same logic works whatever provider produced the series.
      // Consumers swap `<polyline :points="hostChart(...).points">`
      // for `<path class="metric-line" :d="hostChart(...).pathGapped">`.
      const seriesTs = haveTimes ? entry.series.map(r => Number(r.t) || 0) : null;
      const gapThr = seriesTs ? this._detectGapThresholdSec(seriesTs) : null;
      const gapSegs = [];
      let prevSegT = 0;
      for (let i = 0; i < xy.length; i++) {
        const curT = seriesTs ? (seriesTs[i] || 0) : 0;
        const isGap = (gapThr && i > 0 && prevSegT > 0 && curT > 0 && (curT - prevSegT) > gapThr);
        gapSegs.push((i === 0 || isGap ? 'M' : 'L') + xy[i].x.toFixed(1) + ',' + xy[i].y.toFixed(1));
        prevSegT = curT;
      }
      const pathGapped = gapSegs.join(' ');
      // Area path — polyline + closure back to baseline so we can fill
      // under the curve. Baseline is the chart's bottom. Gap-aware:
      // each contiguous run is filled separately (closes back to
      // baseline before the next run starts) so a multi-hour gap
      // doesn't produce a single fake-smooth filled trapezoid bridging
      // the dead period.
      const baseY = (H - PAD_B).toFixed(1);
      const areaSegs = [];
      let runStartIdx = -1;
      const closeRun = (endIdx) => {
        if (runStartIdx < 0 || endIdx < runStartIdx) return;
        let seg = `M${xy[runStartIdx].x.toFixed(1)},${baseY}`;
        for (let j = runStartIdx; j <= endIdx; j++) {
          seg += ` L${xy[j].x.toFixed(1)},${xy[j].y.toFixed(1)}`;
        }
        seg += ` L${xy[endIdx].x.toFixed(1)},${baseY} Z`;
        areaSegs.push(seg);
      };
      let prevAreaT = 0;
      for (let i = 0; i < xy.length; i++) {
        const curT = seriesTs ? (seriesTs[i] || 0) : 0;
        const isGap = (gapThr && i > 0 && prevAreaT > 0 && curT > 0 && (curT - prevAreaT) > gapThr);
        if (isGap) {
          closeRun(i - 1);
          runStartIdx = i;
        } else if (runStartIdx < 0) {
          runStartIdx = i;
        }
        prevAreaT = curT;
      }
      closeRun(xy.length - 1);
      const area = areaSegs.join(' ');
      // Horizontal reference ticks — three evenly-spaced gridlines so
      // the eye has something to anchor the peaks against.
      const ticks = [0.25, 0.5, 0.75].map(frac => ({
        y: (PAD_T + usableH * frac).toFixed(1),
        value: (hi - (hi - lo) * frac),
      }));
      // Pre-rendered gridline path — a single SVG path string with
      // `M0,y H W` for each tick. Used instead of an Alpine
      // `<template x-for>` inside `<svg>` (Alpine's `<template>`
      // doesn't work in the SVG namespace — the browser parses
      // SVG `<template>` as an unknown element, child nodes never
      // attach to its `.content` document fragment, so Alpine
      // throws "Cannot read properties of undefined (reading
      // 'children')" + the inner `tk` reference goes undefined).
      // Single path keeps the SVG renderable + avoids the runtime
      // error; visual outcome is identical (3 horizontal lines).
      // The y coordinate uses the same `* 1.2` multiplier the old
      // template did so the gridline positions match the layout
      // the operator was already seeing.
      const gridPath = ticks
        .map(t => `M0,${(parseFloat(t.y) * 1.2).toFixed(1)} H${W}`)
        .join(' ');
      const cur = Number(pts[pts.length - 1]) || 0;
      return {
        points,
        pathGapped,
        area,
        ticks,
        gridPath,
        width: W,
        height: H,
        min: lo,
        max: hi,
        current: cur,
      };
    },
    // "Is this net-series flat at zero?" — used by the Net In / Net
    // Out cards to swap the chart for an actionable hint when Beszel's
    // agent isn't tracking any NIC (the agent needs NICS=<iface> env
    // set before it emits nr/ns numbers). Distinct from "no data yet"
    // because hostHistory[].series IS populated — every point is 0.
    isNetSeriesFlat(systemId, key) {
      const stats = this.hostMetricStats(systemId, key, false);
      return !!(stats && stats.maxRaw === 0);
    },
    // Min/Max label helper — returns both pre-formatted strings
    // (used directly in the chart header) and raw numeric values
    // (used by templates to decide flat-signal collapsing).
    // Shared peak across multiple keys — used by the combined Net I/O
    // and Disk I/O charts so the two polylines render
    // against the same y-axis and are visually comparable.
    // Returns 0 when no data so the caller can short-circuit the chart.
    hostChartMax(systemId, keys) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series) return 0;
      let m = 0;
      for (const k of keys) {
        for (const r of entry.series) {
          const n = Number(r[k]) || 0;
          if (n > m) m = n;
        }
      }
      return m;
    },
    // "permanently flat" detector. Returns true when the chart
    // has accumulated enough history (default ≥ 12 points = 1 hour at
    // a 5-min cadence) AND every point across the listed fields is 0.
    // Caller uses this to HIDE chart cards whose data source is
    // genuinely never going to populate (e.g. SNMP-only TrueNAS host
    // for Disk I/O — SNMP doesn't track per-mount IOPS, so dr/dw stay
    // at 0 forever). Pre-fix the card stayed visible permanently with
    // a "Disk idle" hint, which read like "data is loading" instead of
    // "this provider doesn't surface this metric". Hosts in warmup
    // (< minPoints) get the benefit of the doubt — chart still shows.
    hostChartIsPermanentlyFlat(systemId, keys, minPoints) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series) return false;
      const points = entry.series.length;
      const need = +minPoints || 12;
      if (points < need) return false;     // still warming up
      // hostChartMax === 0 means every point in every key is 0/missing.
      return this.hostChartMax(systemId, keys) === 0;
    },
    // Per-sensor temperature readout for the chart card stats line
    //. Returns [[sensor_name, celsius], ...] sorted hottest-
    // first, capped at 3 — modern hosts can expose 8+ sensors
    // (coretemp_package + nvme_composite + acpitz + per-core +
    // hwmon …) and shoving them all into the inline header
    // overflowed the row, decoupling sensor name from value. The
    // chart line carries `temp_max` (the global peak across ALL
    // sensors at each tick) so nothing hot gets hidden — only the
    // verbose readout is trimmed. The full per-sensor dict is
    // available via `h.host_temperatures` if a future drawer wants
    // to expose it.
    hostTemperatureRows(h) {
      const t = (h && h.host_temperatures) || {};
      const rows = Object.entries(t).filter(([, c]) => Number.isFinite(Number(c)));
      rows.sort((a, b) => Number(b[1]) - Number(a[1]));
      return rows.slice(0, 3);
    },
    // True when the host emits more sensors than we show inline.
    // Drives the "+N more" chip the operator sees when there's a long
    // tail beyond the top 3.
    hostTemperatureExtraCount(h) {
      const t = (h && h.host_temperatures) || {};
      const n = Object.keys(t).length;
      return n > 3 ? (n - 3) : 0;
    },
    // Deterministic per-sensor colour token. Cycles through the
    // five existing pill / accent tokens so we don't introduce new
    // colour literals (CLAUDE.md token discipline). Index comes from
    // a sorted-name lookup so each sensor always gets the same colour
    // across renders, regardless of `Object.entries` iteration order.
    hostTempLineColor(name, sortedNames) {
      const palette = [
        'var(--primary)',
        'var(--warning)',
        'var(--danger)',
        'var(--success)',
        'var(--info)',
      ];
      const idx = sortedNames.indexOf(name);
      return palette[(idx >= 0 ? idx : 0) % palette.length];
    },
    // Multi-line chart helper for the Temperature card. Produces
    // one polyline per sensor with auto-scaled Y axis, padded ±5°C so
    // tight ranges still render with clear vertical movement. Sensors
    // are discovered by walking every point's `temps` dict (the union,
    // since some sensors only appear partway through a session). Each
    // sensor's missing samples are skipped, not zero-padded — that
    // matches CLAUDE.md's "skip-don't-synthesize" rule for time series.
    // Returns null when there's not enough data to draw two points.
    hostTempChart(systemId, opts = {}) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series || entry.series.length < 2) return null;
      const W = opts.width || 420;
      const H = opts.height || 120;
      const PAD_X = 4;
      const PAD_T = 6;
      const PAD_B = 4;
      // Discover the union of sensor names across the whole window
      // and capture both lo and hi so the Y axis auto-fits.
      const sensorNames = new Set();
      let lo = Infinity, hi = -Infinity;
      for (const r of entry.series) {
        const t = r && r.temps;
        if (!t || typeof t !== 'object') continue;
        for (const [name, c] of Object.entries(t)) {
          const n = Number(c);
          if (!Number.isFinite(n)) continue;
          sensorNames.add(name);
          if (n < lo) lo = n;
          if (n > hi) hi = n;
        }
      }
      if (!isFinite(lo) || sensorNames.size === 0) return null;
      // ±5°C breathing room so flat-ish series don't render as a
      // single horizontal pixel. Min anchored at >=0 so a freezer
      // host with subzero readings doesn't leave a Y-axis label
      // showing negative-zero.
      lo = Math.max(0, Math.floor(lo - 5));
      hi = Math.ceil(hi + 5);
      if (hi - lo < 10) hi = lo + 10;
      const sortedNames = Array.from(sensorNames).sort();
      const usableW = W - PAD_X * 2;
      const usableH = H - PAD_T - PAD_B;
      // #815 — Unified drawer time-domain. Same contract as `hostChart`:
      // when every series row has a `t` epoch-seconds timestamp, x is
      // computed against the picker window so this chart's pixels align
      // with every other drawer chart. Index-based fallback for any
      // future loader regression that forgets to stamp `t`.
      const dom = this._drawerTimeDomain();
      const tSpan = Math.max(1, dom.tMaxSec - dom.tMinSec);
      const haveTimes = entry.series.every(r => Number(r && r.t) > 0);
      const stepFallback = usableW / Math.max(1, entry.series.length - 1);
      // Five-token palette, matched to the slug list returned in
      // dByColor below. Index = sensor's position in sortedNames; %5
      // wraps when a host has more sensors than colours.
      const slugs = ['primary', 'warning', 'danger', 'success', 'info'];
      const dByColor = { primary: '', warning: '', danger: '', success: '', info: '' };
      const lines = [];
      // Time-gap break threshold — derived from the series cadence so
      // a multi-hour outage breaks the line for every sensor, not just
      // those whose individual sample happens to be missing at the
      // gap boundary.
      const seriesTs = haveTimes ? entry.series.map(r => Number(r && r.t) || 0) : null;
      const gapThr = seriesTs ? this._detectGapThresholdSec(seriesTs) : null;
      sortedNames.forEach((name, idx) => {
        const segs = [];   // SVG path data — handles missing samples
        let cur = '';
        let prevTs = 0;
        for (let i = 0; i < entry.series.length; i++) {
          const t = entry.series[i] && entry.series[i].temps;
          const v = t && Number(t[name]);
          if (!Number.isFinite(v)) {
            // Sample missing → break the line; next valid point
            // starts a new sub-path so we don't synthesise a slope
            // through a gap.
            if (cur) { segs.push(cur); cur = ''; }
            prevTs = 0;
            continue;
          }
          let x;
          let curTs = 0;
          if (haveTimes) {
            curTs = Number(entry.series[i].t) || 0;
            x = PAD_X + Math.max(0, Math.min(1, (curTs - dom.tMinSec) / tSpan)) * usableW;
          } else {
            x = PAD_X + i * stepFallback;
          }
          // Time-gap break — if the previous valid point was more than
          // `gapThr` seconds ago, start a fresh sub-path so the
          // rendered line doesn't bridge the dead period.
          if (cur && gapThr && prevTs > 0 && curTs > 0 && (curTs - prevTs) > gapThr) {
            segs.push(cur);
            cur = '';
          }
          const y = PAD_T + usableH - ((v - lo) / (hi - lo || 1)) * usableH;
          cur += (cur ? ' L' : 'M') + x.toFixed(1) + ',' + y.toFixed(1);
          prevTs = curTs;
        }
        if (cur) segs.push(cur);
        const d = segs.join(' ');
        if (!d) return;
        const slug = slugs[idx % slugs.length];
        dByColor[slug] = dByColor[slug] ? dByColor[slug] + ' ' + d : d;
        lines.push({ name, d, color: 'var(--' + slug + ')' });
      });
      if (lines.length === 0) return null;
      // Y-axis ticks: 3 labels (top / mid / bottom) so the .metric-y-axis
      // flex `justify-content: space-between` lands them at the same
      // visual rhythm as `yAxisPercent()` (`100% / 50% / 0%`). Earlier
      // ship used 4 labels which made the inner two land at 33%/66%
      // of the y-axis div — visually offset from anything meaningful
      // on the chart and read as "labels out of bounds".
      const mid = lo + (hi - lo) / 2;
      const yAxis = [hi, mid, lo].map(v => Math.round(v) + '°');
      return { lines, dByColor, yAxis, min: lo, max: hi, sortedNames };
    },
    // Window-aggregated packet-loss for the Ping chart's loss chip.
    // Pre-fix the chip read `h.ping_loss_pct` directly — that field
    // reflects the LATEST single probe's loss only, which is
    // meaningless when the operator is looking at a 24h / 7d window
    // (the chip claimed the whole-window loss but described one tick).
    // Window-correct definition: of the samples we have IN the window,
    // how many were `alive=false`? `loss% = down_count / received_count
    // × 100`. Missing samples (sampler not running, OmniGrid down,
    // host outage where the sampler couldn't write rows) count as
    // "no data" — NOT 100% loss — so a multi-hour OmniGrid outage
    // followed by recovery shows 0% over a window where every received
    // sample is alive=true. Returns null when there are no samples
    // (chip hides via the `> 0` gate).
    hostPingWindowLoss(systemId) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series || !entry.series.length) return null;
      let total = 0, down = 0;
      for (const r of entry.series) {
        total++;
        if (r && r.alive === false) down++;
      }
      if (!total) return null;
      return Math.round(100 * down / total);
    },
    hostMetricStats(systemId, key, asPct = true) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series || entry.series.length === 0) return null;
      let lo = Infinity, hi = -Infinity;
      for (const r of entry.series) {
        const n = Number(r[key]) || 0;
        if (n < lo) lo = n;
        if (n > hi) hi = n;
      }
      if (!isFinite(lo)) return null;
      if (asPct) {
        return {
          min: lo.toFixed(1) + '%',
          max: hi.toFixed(1) + '%',
          minRaw: lo, maxRaw: hi,
        };
      }
      // Operator-flagged: when `lo = 0` and `hi` is a non-zero MB/s
      // value, `fmtBytes` rendered `0 B/s` next to `19 MB/s` —
      // different units in the same chart's legend. Lock both
      // formatted values to the unit family of `hi` (the chart's
      // max) via `fmtBytesAt`.
      return {
        min: this.fmtBytesAt(lo, hi) + '/s',
        max: this.fmtBytesAt(hi, hi) + '/s',
        minRaw: lo, maxRaw: hi,
      };
    },
    // Seconds → "6d 3h" / "5h 12m" / "34m 12s" — matches img_10's format.
    fmtUptimeShort(s) {
      if (!s || s <= 0) return '—';
      const d = Math.floor(s / 86400);
      const h = Math.floor((s % 86400) / 3600);
      const m = Math.floor((s % 3600) / 60);
      if (d > 0) return `${d}d ${h}h`;
      if (h > 0) return `${h}h ${m}m`;
      return `${m}m`;
    },
    // ISO string → "Updated 2s ago" / "2d ago"
    fmtUpdatedAgo(iso) {
      if (!iso) return '';
      const t = Date.parse(iso);
      if (isNaN(t)) return '';
      const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
      if (s < 60) return `Updated ${s}s ago`;
      if (s < 3600) return `Updated ${Math.floor(s / 60)}m ago`;
      if (s < 86400) return `Updated ${Math.floor(s / 3600)}h ago`;
      return `Updated ${Math.floor(s / 86400)}d ago`;
    },
    memPercentOf(h) {
      if (!h || !h.mem_total) return 0;
      return Math.round((h.mem_used / h.mem_total) * 100);
    },
    diskPercentOf(h) {
      if (!h || !h.disk_total) return 0;
      return Math.round((h.disk_used / h.disk_total) * 100);
    },
    // Percent label that distinguishes "genuinely zero" from "small
    // but non-zero" by rendering "<1%" when 0 < v < 1 (e.g. 39 MB
    // used on a 232 GB disk = 0.016% which rounds to 0%, hiding the
    // signal that there IS data). Operator-reported on a dd-wrt
    // host whose /opt mount had a few MB used out of 232 GB and the
    // bar label read "0%" alongside a non-empty fill — confused
    // "is this loading?" vs "is this near-empty?". Uses 1-decimal
    // precision for fractional values < 10 so 1.7% reads as "1.7%"
    // instead of "2%". Integers above 10. Negative / NaN / falsy
    // → "0%" (defensive — shouldn't happen but keeps the label
    // shape consistent).
    fmtPercentLabel(v) {
      const n = +v;
      if (!Number.isFinite(n) || n <= 0) return '0%';
      if (n < 1) return '<1%';
      if (n < 10) return n.toFixed(1) + '%';
      return Math.round(n) + '%';
    },
    // -------------------- Per-host Health Score --------------------
    // Synthesises CPU / Memory / Disk / Provider / Pending-Updates
    // signals into a single 0-100 score per host, with a per-axis
    // breakdown for the drawer popover. Operator quote: "anything
    // <80 gets attention this morning" — so the overall score is
    // worst-axis-wins (min of valid sub-scores), matching that
    // mental model exactly. A weighted-average alternative would
    // dilute a single bad axis and bury the actionable signal.
    //
    // Each axis returns { key, label, score: 0..100 | null, detail }.
    // null score means the axis is N/A for this host (no telemetry,
    // no providers configured, package count missing) and is skipped
    // from both the overall score and the breakdown list.
    //
    // Down / paused hosts short-circuit to a single Status axis
    // with score=0 — synthesising CPU% on a dead host is meaningless
    // and "100 / 100 / 0" averages would lie about reachability.
    healthAxes(h) {
      if (!h) return [];
      // Unconfigured / loading rows have nothing to grade — return []
      // so the chip hides cleanly via `healthScore() == null`.
      if (h.status === 'unconfigured' || h.status === 'loading') return [];
      // Down / paused → status axis dominates. Operator's reaction
      // to a 0 with reason "Sampling paused" is "click in", which
      // matches what they should be doing for a paused host anyway.
      if (h.sampling_paused || h.status === 'down') {
        return [{
          key: 'status',
          label: this.t('hosts_extra.health.axis_status'),
          score: 0,
          detail: h.sampling_paused
            ? this.t('hosts_extra.health.status_paused')
            : this.t('hosts_extra.health.status_down'),
        }];
      }
      const warn = this._statBarWarnPct();
      const crit = this._statBarCritPct();
      // Linear ramp: 100 at <=warn, 0 at >=crit, linear between.
      // Mirrors the existing barLevel() colour cue thresholds so
      // the chip's amber turn-over lines up with the stat-bar's.
      const pctScore = (v) => {
        if (v == null || !Number.isFinite(v)) return null;
        if (v >= crit) return 0;
        if (v <= warn) return 100;
        return Math.round(100 - ((v - warn) / (crit - warn)) * 100);
      };
      const axes = [];
      // CPU
      if (this.hostHasTelemetry(h) && Number.isFinite(h.cpu_percent)) {
        const s = pctScore(h.cpu_percent);
        if (s != null) {
          axes.push({
            key: 'cpu',
            label: this.t('hosts_extra.health.axis_cpu'),
            score: s,
            detail: (h.cpu_percent || 0).toFixed(1) + '%',
          });
        }
      }
      // Memory
      if (this.hostHasTelemetry(h)) {
        const mp = Number.isFinite(h.mem_percent) && h.mem_percent > 0
          ? h.mem_percent : this.memPercentOf(h);
        if (Number.isFinite(mp) && mp > 0) {
          const s = pctScore(mp);
          if (s != null) {
            axes.push({
              key: 'memory',
              label: this.t('hosts_extra.health.axis_memory'),
              score: s,
              detail: Math.round(mp) + '%',
            });
          }
        }
      }
      // Disk — worst-mount-wins. A 95% / 50% pair scores like 95%
      // because that one filling mount will start failing writes
      // while the other looks healthy. Single-mount fallback uses
      // h.disk_percent directly. Picks the mount with the highest
      // fill percent and reports its mountpoint in the detail
      // string so the operator knows which one is hot.
      if (Array.isArray(h.mounts) && h.mounts.length) {
        let worstPct = -1;
        let worstName = '';
        for (const m of h.mounts) {
          const fp = this.mountFillPercent(m);
          if (fp > worstPct) {
            worstPct = fp;
            worstName = m.mp || m.path || m.name || '/';
          }
        }
        if (worstPct >= 0) {
          const s = pctScore(worstPct);
          if (s != null) {
            axes.push({
              key: 'disk',
              label: this.t('hosts_extra.health.axis_disk'),
              score: s,
              detail: Math.round(worstPct) + '% · ' + worstName,
            });
          }
        }
      } else if (this.hostHasTelemetry(h) && Number.isFinite(h.disk_percent) && h.disk_percent > 0) {
        const s = pctScore(h.disk_percent);
        if (s != null) {
          axes.push({
            key: 'disk',
            label: this.t('hosts_extra.health.axis_disk'),
            score: s,
            detail: Math.round(h.disk_percent) + '%',
          });
        }
      }
      // Providers — deduct 25 per failing/paused provider, floored at 0.
      // Empty list (no providers enabled) returns null so the axis
      // doesn't drag a Ping-only host's score down to 100/100/0/N/A.
      const enabledAgents = this.hostEnabledAgents(h);
      if (enabledAgents.length) {
        const states = this.providerStates(h) || [];
        const failing = states.filter(p => p.state === 'failing' || p.state === 'paused');
        const score = Math.max(0, 100 - failing.length * 25);
        let detail;
        if (!failing.length) {
          detail = this.t('hosts_extra.health.providers_ok', { count: enabledAgents.length });
        } else {
          detail = this.t('hosts_extra.health.providers_failing', {
            count: failing.length,
            names: failing.map(p => p.name).join(', '),
          });
        }
        axes.push({
          key: 'providers',
          label: this.t('hosts_extra.health.axis_providers'),
          score, detail,
        });
      }
      // Pending updates — bucketed (0/75/45/15) so a host with 100+
      // pending updates dominates the score even though "pending"
      // doesn't mean "broken". 0 pending → 100 (axis effectively
      // n/a). 1-10 → 75 (mild nudge). 11-50 → 45 (something's been
      // ignored). >50 → 15 (someone hasn't run apt update in months).
      if (Number.isFinite(h.package_updates_count)) {
        const n = h.package_updates_count;
        let score, detail;
        if (n === 0) {
          score = 100;
          detail = this.t('hosts_extra.health.updates_zero');
        } else if (n <= 10) {
          score = 75;
          detail = this.t('hosts_extra.health.updates_pending', { count: n });
        } else if (n <= 50) {
          score = 45;
          detail = this.t('hosts_extra.health.updates_pending', { count: n });
        } else {
          score = 15;
          detail = this.t('hosts_extra.health.updates_pending', { count: n });
        }
        axes.push({
          key: 'updates',
          label: this.t('hosts_extra.health.axis_updates'),
          score, detail,
        });
      }
      return axes;
    },
    // Worst-axis-wins: returns the lowest sub-score across all valid
    // axes, or null if no axis is computable for this host.
    healthScore(h) {
      const axes = this.healthAxes(h);
      if (!axes.length) return null;
      let worst = 100;
      for (const a of axes) {
        if (a.score != null && a.score < worst) worst = a.score;
      }
      return worst;
    },
    // Returns the single lowest-scoring axis — populates the chip
    // tooltip + the breakdown popover's "Worst axis" callout.
    healthWorstAxis(h) {
      const axes = this.healthAxes(h);
      if (!axes.length) return null;
      let worst = null;
      for (const a of axes) {
        if (a.score == null) continue;
        if (worst == null || a.score < worst.score) worst = a;
      }
      return worst;
    },
    // Threshold tier for the chip background colour. 80+ green,
    // 50-79 amber, <50 red. Aligns with operator's "anything <80
    // gets attention" mental model — anything green is healthy,
    // amber is "look later", red is "look now".
    healthChipClass(score) {
      if (score == null) return '';
      if (score < 50) return 'health-chip-bad';
      if (score < 80) return 'health-chip-warn';
      return 'health-chip-ok';
    },
    // Toggle the breakdown popover open at host-drawer scope. Click
    // the chip in the drawer header → flips the panel; click again
    // (or close drawer) closes. State stays on the app() instance
    // because the popover lives inside the drawer template tree.
    toggleHealthPopover() {
      this.healthPopoverOpen = !this.healthPopoverOpen;
    },
    // ===== HOST TIMELINE =============================================
    // Triage view inside the drawer aggregating ops, notifications,
    // provider auto-pause + recovery markers per host. Backed by
    // GET /api/hosts/{id}/timeline?hours=N. Cached per-host with
    // a 30s TTL (faster invalidation when the operator clicks
    // refresh or changes the range). Reads + writes
    // `this.hostTimeline[hid]` and `this.hostTimelineRange[hid]`.
    toggleHostTimeline(hostId) {
      const id = (hostId || '').toString();
      if (!id) return;
      const wasOpen = !!this.timelineExpanded[id];
      this.timelineExpanded[id] = !wasOpen;
      // First open → kick off the fetch. Subsequent opens use the
      // cache unless it's stale.
      if (!wasOpen) {
        const cache = this.hostTimeline[id];
        const now = Date.now();
        const stale = !cache
          || !cache.loadedAt
          || (now - cache.loadedAt) > 30000;
        if (stale) {
          this.loadHostTimeline(id, false);
        }
      }
    },
    setHostTimelineRange(hostId, hours) {
      const id = (hostId || '').toString();
      const h = Math.max(1, Math.min(720, parseInt(hours, 10) || 168));
      if (!id) return;
      this.hostTimelineRange[id] = h;
      // Clear cache so the new range is honoured immediately.
      delete this.hostTimeline[id];
      this.loadHostTimeline(id, true);
    },
    async loadHostTimeline(hostId, force) {
      const id = (hostId || '').toString();
      if (!id) return;
      const hours = this.hostTimelineRange[id] || 168;
      // Mark loading flag without clearing the existing event list so
      // the operator sees stale-then-fresh rather than a flash of
      // empty during refetch.
      const existing = this.hostTimeline[id] || {};
      this.hostTimeline[id] = {
        ...existing,
        loading: true,
        error: null,
        hours,
      };
      try {
        const r = await fetch(
          '/api/hosts/' + encodeURIComponent(id) + '/timeline?hours=' + hours,
          { credentials: 'same-origin' },
        );
        if (!r.ok) {
          const detail = await r.text().catch(() => '');
          throw new Error('HTTP ' + r.status + (detail ? ': ' + detail.slice(0, 200) : ''));
        }
        const data = await r.json();
        this.hostTimeline[id] = {
          events:   Array.isArray(data.events) ? data.events : [],
          counts:   data.counts || { ops: 0, notifications: 0, failures: 0, recoveries: 0 },
          loading:  false,
          error:    null,
          loadedAt: Date.now(),
          hours,
        };
      } catch (e) {
        this.hostTimeline[id] = {
          ...(this.hostTimeline[id] || {}),
          loading: false,
          error: (e && e.message) ? e.message : 'timeline fetch failed',
          hours,
        };
      }
    },
    hostTimelineKindLabel(kind) {
      const k = (kind || '').toString();
      const key = 'host_drawer.timeline.kind_' + k;
      const tr = this.t(key);
      if (tr && tr !== key) return tr;
      // Fallback when i18n key is missing — humanise the enum.
      return k.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
    },
    hostTimelineKindChipClass(kind) {
      switch ((kind || '').toString()) {
        case 'op':                 return 'pill-info';
        case 'notification':       return 'pill-warning';
        case 'provider_paused':    return 'pill-error';
        case 'provider_recovered': return 'pill-ok';
        default:                   return 'pill-muted';
      }
    },
    hostTimelineIconRef(kind, severity) {
      // Per-kind icons distinct enough to read at a glance in a busy
      // timeline — operator can spot a paused-provider entry without
      // hovering for the title.
      const sev = (severity || 'info').toString();
      const k = (kind || '').toString();
      if (k === 'op')                  return 'icon-history';
      if (k === 'notification')        return 'icon-bell';
      if (k === 'provider_paused')     return 'icon-pause';
      if (k === 'provider_recovered')  return 'icon-check';
      // Unknown kind — fall back on severity for forward-compat.
      if (sev === 'success')           return 'icon-activity';
      if (sev === 'error')             return 'icon-bug';
      return 'icon-info';
    },
    hostTimelineTimeLabel(ts) {
      const n = Number(ts);
      if (!Number.isFinite(n) || n <= 0) return '';
      try {
        const d = new Date(n * 1000);
        return d.toLocaleString();
      } catch {
        return '';
      }
    },
    // ===== HOSTS BULK SELECTION ======================================
    // Selection helpers. The Hosts main view's row checkbox stops
    // propagation so click on the row body still opens the drawer
    // but click on the checkbox only toggles selection.
    isHostSelected(hostId) {
      return this.selectedHosts.has((hostId || '').toString());
    },
    toggleHostSelection(hostId) {
      const id = (hostId || '').toString();
      if (!id) return;
      if (this.selectedHosts.has(id)) {
        this.selectedHosts.delete(id);
      } else {
        this.selectedHosts.add(id);
      }
      // Re-assign so Alpine sees the mutation (Set mutations don't
      // trigger reactivity by themselves).
      this.selectedHosts = new Set(this.selectedHosts);
    },
    clearHostSelection() {
      this.selectedHosts = new Set();
    },
    selectedHostCount() {
      return this.selectedHosts.size;
    },
    selectedHostsArray() {
      return Array.from(this.selectedHosts);
    },
    // Select-all-visible (filtered by current Hosts toolbar state).
    selectAllVisibleHosts() {
      const ids = (this.filteredHosts() || [])
        .map(h => h && h.id)
        .filter(Boolean)
        .map(String);
      this.selectedHosts = new Set(ids);
    },
    // ===== HOSTS BULK ACTIONS ========================================
    // Each action POSTs to a `/api/hosts/bulk/<action>` endpoint and
    // surfaces the partial-success response via the existing toast
    // helpers. Selection is preserved on success so the operator can
    // chain actions; cleared on operator click of "Clear".
    async _hostsBulkPost(path, payload, successMsgKey) {
      const ids = this.selectedHostsArray();
      if (ids.length === 0) return;
      const body = { host_ids: ids, ...(payload || {}) };
      try {
        const r = await fetch('/api/hosts/bulk/' + path, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify(body),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          const detail = data && (data.detail || data.error)
            || ('HTTP ' + r.status);
          this.showToast(this.t('hosts_extra.bulk.error', { error: detail }) || detail, 'error');
          return data;
        }
        const appliedIds = Array.isArray(data.applied) ? data.applied : [];
        const applied = appliedIds.length;
        const errors = Object.keys(data.errors || {}).length;
        const skipped = (data.skipped || []).length;
        if (errors > 0) {
          this.showToast(
            this.t('hosts_extra.bulk.partial', { applied, errors }) || (applied + ' applied, ' + errors + ' errors'),
            'warning',
          );
        } else {
          const msg = this.t(successMsgKey || 'hosts_extra.bulk.success', { applied })
            || (applied + ' hosts updated');
          this.showToast(msg, 'success');
        }
        // Progressive UI feedback — mark each applied row so the
        // per-row check glyph + the summary badge both render
        // immediately. Clears after 5 s via a single shared timer.
        // In-place mutation only (Alpine reactive-array rule).
        const appliedSet = new Set(appliedIds.map(String));
        if (Array.isArray(this.hosts)) {
          for (const row of this.hosts) {
            if (row && appliedSet.has(String(row.id))) {
              row._bulkApplied = true;
            }
          }
        }
        this.bulkAppliedSummary = {
          applied,
          total: ids.length,
          action: path,
          ts: Date.now(),
        };
        if (this._bulkAppliedTimer) {
          clearTimeout(this._bulkAppliedTimer);
        }
        this._bulkAppliedTimer = setTimeout(() => {
          if (Array.isArray(this.hosts)) {
            for (const row of this.hosts) {
              if (row && row._bulkApplied) row._bulkApplied = false;
            }
          }
          this.bulkAppliedSummary = null;
          this._bulkAppliedTimer = null;
        }, 5000);
        // Force a refresh so the row state reflects the change.
        if (typeof this.loadHosts === 'function') {
          this.loadHosts(true);
        }
        return data;
      } catch (e) {
        this.showToast(
          this.t('hosts_extra.bulk.error', { error: (e && e.message) || 'request failed' })
            || 'Bulk action failed',
          'error',
        );
      }
    },
    async bulkPauseHosts() {
      if (this.selectedHostCount() === 0) return;
      // SweetAlert confirm — destructive (sampler will skip these
      // hosts until manually resumed). Body shows the actual host
      // names so the operator can verify the selection before
      // committing — for >10 hosts the list is truncated to the first
      // 10 + "...and N more" so a 200-host pause confirm doesn't fill
      // the entire screen with hostnames.
      const ids = this.selectedHostsArray();
      const sample = ids.slice(0, 10);
      const more = ids.length - sample.length;
      const sampleHtml = sample.map(id => '<code>' + this._logEscape(id) + '</code>').join(', ');
      const moreHtml = more > 0
        ? ' ' + (this.t('hosts_extra.bulk.pause_confirm_more', { more }) || ('… and ' + more + ' more'))
        : '';
      try {
        const result = await Swal.fire({
          title: this.t('hosts_extra.bulk.pause_confirm_title') || 'Pause sampling?',
          html:  (this.t('hosts_extra.bulk.pause_confirm_body', { count: this.selectedHostCount() })
                  || ('Pause sampling on ' + this.selectedHostCount() + ' host(s)?'))
                 + '<br><br><div class="text-[11.5px] text-[var(--text-dim)] mono break-words">' + sampleHtml + moreHtml + '</div>',
          icon:  'warning',
          showCancelButton: true,
          confirmButtonText: this.t('hosts_extra.bulk.pause_confirm_ok') || 'Pause',
          cancelButtonText:  this.t('actions.cancel') || 'Cancel',
        });
        if (!result.isConfirmed) return;
      } catch { return; }
      await this._hostsBulkPost('pause', null, 'hosts_extra.bulk.pause_success');
    },
    async bulkResumeHosts() {
      if (this.selectedHostCount() === 0) return;
      await this._hostsBulkPost('resume', null, 'hosts_extra.bulk.resume_success');
    },
    openBulkSnmpVendorsModal() {
      if (this.selectedHostCount() === 0) return;
      this.bulkSnmpVendorsModal = { open: true, vendors: [], mode: 'set' };
    },
    closeBulkSnmpVendorsModal() {
      this.bulkSnmpVendorsModal = { open: false, vendors: [], mode: 'set' };
    },
    toggleBulkVendor(v) {
      const cur = this.bulkSnmpVendorsModal.vendors || [];
      const idx = cur.indexOf(v);
      if (idx >= 0) {
        cur.splice(idx, 1);
      } else {
        cur.push(v);
      }
      this.bulkSnmpVendorsModal = { ...this.bulkSnmpVendorsModal, vendors: [...cur] };
    },
    async submitBulkSnmpVendors() {
      const m = this.bulkSnmpVendorsModal || {};
      await this._hostsBulkPost(
        'snmp_vendors',
        { vendors: m.vendors || [], mode: m.mode || 'set' },
        'hosts_extra.bulk.snmp_vendors_success',
      );
      this.closeBulkSnmpVendorsModal();
    },
    openBulkSnmpTunablesModal() {
      if (this.selectedHostCount() === 0) return;
      this.bulkSnmpTunablesModal = {
        open: true,
        walk_concurrency: '',
        wall_clock_budget: '',
        clear: false,
      };
    },
    closeBulkSnmpTunablesModal() {
      this.bulkSnmpTunablesModal = {
        open: false,
        walk_concurrency: '',
        wall_clock_budget: '',
        clear: false,
      };
    },
    async submitBulkSnmpTunables() {
      const m = this.bulkSnmpTunablesModal || {};
      const payload = { clear: !!m.clear };
      if (!m.clear) {
        const wc = parseInt(m.walk_concurrency, 10);
        if (Number.isFinite(wc)) payload.walk_concurrency = wc;
        const wcb = parseInt(m.wall_clock_budget, 10);
        if (Number.isFinite(wcb)) payload.wall_clock_budget = wcb;
        if (payload.walk_concurrency == null && payload.wall_clock_budget == null) {
          this.showToast(
            this.t('hosts_extra.bulk.snmp_tunables_empty')
              || 'Set at least one tunable or enable Clear',
            'warning',
          );
          return;
        }
      }
      await this._hostsBulkPost(
        'snmp_tunables',
        payload,
        'hosts_extra.bulk.snmp_tunables_success',
      );
      this.closeBulkSnmpTunablesModal();
    },
    // Segmented-bar helper — percent-full of a single mount, used as
    // the INNER fill width inside an equal-width slot. Each mount
    // gets its own flex-1 slot in the bar; within that slot, the
    // fill's width reflects that mount's own percent full. This
    // makes small-capacity mounts (e.g. /boot at 252 MB on a 939 GB
    // pool) visible — their slot takes the same share of the bar as
    // a multi-TB /. Previously the bar used "this mount's share of
    // the whole pool" for width, which hid any small partition at
    // sub-pixel widths.
    //
    // Prefers the provider-supplied `dp` (percent full) when present;
    // falls back to computing from GiB floats (.du / .d) so mounts
    // missing a pre-computed percent still render correctly.
    // Tooltip for the disk stat-bar on the Hosts row. Shows the
    // aggregate disk%, the absolute used / total bytes, AND a per-
    // mount breakdown when there's more than one — operators
    // hovering see exactly which mount(s) are full without
    // opening the drawer. Worst-mount callout fires when any
    // mount is over the warn threshold so a critical mount
    // surfaces in the tooltip even when the aggregate is low
    // (dd-wrt's 100%-full squashfs `/` is invisible in the
    // aggregate but shows up here as "WORST: / at 100%").
    hostDiskBarTitle(h) {
      if (!h) return '';
      const aggPct = h.disk_percent || this.diskPercentOf(h);
      const parts = [
        this.t('columns.disk') + ': ' + this.fmtPercentLabel(aggPct),
      ];
      if (h.disk_total) {
        parts.push('(' + this.fmtBytes(h.disk_used) + ' / ' + this.fmtBytes(h.disk_total) + ')');
      }
      const mounts = Array.isArray(h.mounts) ? h.mounts : [];
      if (mounts.length > 1) {
        let worst = null;
        for (const m of mounts) {
          const fp = this.mountFillPercent(m);
          if (worst == null || fp > this.mountFillPercent(worst)) worst = m;
        }
        if (worst && this.mountFillPercent(worst) > this._statBarWarnPct()) {
          parts.push('— Worst: ' + (worst.n || worst.mountpoint || '?')
            + ' ' + Math.round(this.mountFillPercent(worst)) + '%');
        }
        const lines = mounts.map(m => '  ' + (m.n || m.mountpoint || '?')
          + ' · ' + Math.round(this.mountFillPercent(m)) + '%');
        parts.push('\n' + lines.join('\n'));
      }
      if (this.isStaleField(h, 'host_disk_total') || this.isStaleField(h, 'host_disk_used')) {
        parts.push('— ' + this.staleAge(h));
      }
      return parts.join(' ').replace(' \n', '\n');
    },
    // Threshold tier for a single mount's fill — drives the segmented
    // disk bar's per-segment colour (kept for callers; row no longer
    // uses the segmented variant). Mirrors `barLevel(pct)` so a
    // full mount visually screams the same way a single-mount bar
    // would. Empty / unknown returns 'ok' (green) so the bar reads
    // as healthy at rest.
    mountFillLevel(m) {
      const pct = this.mountFillPercent(m);
      if (pct > this._statBarCritPct()) return 'crit';
      if (pct > this._statBarWarnPct()) return 'warn';
      return 'ok';
    },
    mountFillPercent(m) {
      if (!m) return 0;
      const dp = Number(m.dp);
      if (Number.isFinite(dp) && dp > 0) return Math.min(100, Math.max(0, dp));
      const size = Number(m.d) || 0;
      const used = Number(m.du) || 0;
      if (size <= 0) return 0;
      return Math.min(100, Math.max(0, (used / size) * 100));
    },
    // Green → amber → red by threshold, matching how nodeStats renders.
    pctColor(pct) {
      if (pct >= 85) return 'var(--danger)';
      if (pct >= 60) return 'var(--warning)';
      return 'var(--success)';
    },
    statusDotColor(status) {
      if (status === 'up') return 'var(--success)';
      if (status === 'down' || status === 'unreachable') return 'var(--danger)';
      if (status === 'paused') return 'var(--warning)';
      // Grey dots — no signal (yet) worth alerting on:
      //   'loading'      — skeleton state, probe hasn't returned
      //   'unconfigured' — curated row has NO provider fields set,
      //                    so there's literally nothing to probe
      if (status === 'loading' || status === 'unconfigured') {
        return 'var(--text-faint)';
      }
      // 'unknown' — providers ARE mapped but none returned data.
      // Red because this IS a real failure to reach the host.
      if (status === 'unknown') return 'var(--danger)';
      return 'var(--text-faint)';
    },

    // Stacks/Services row status indicator — drives the small dot
    // before the row icon. Returns one of `is-up` (green, all healthy),
    // `is-degraded` (amber, has updates pending OR partial degraded),
    // `is-down` (red, any item offline / errored), or `is-unknown`
    // (grey, nothing actionable). The corresponding spinner state is
    // gated on `statsLoaded` — until the first /api/stats response
    // lands, the row renders a spinner instead of a dot so operators
    // see the "still fetching data in background" affordance the user
    // requested. Once stats are in, the dot reflects the rolled-up
    // state of the stack's items.
    stackStatusDotClass(stack) {
      if (!stack) return 'is-unknown';
      if ((stack.offline || 0) > 0) return 'is-down';
      if ((stack.errors  || 0) > 0) return 'is-down';
      if ((stack.degraded || 0) > 0 || (stack.updates || 0) > 0) return 'is-degraded';
      if ((stack.unknowns || 0) > 0) return 'is-unknown';
      if ((stack.uptodate || 0) > 0 || (stack.total || 0) > 0) return 'is-up';
      return 'is-unknown';
    },
    // Per-item dot — used by the Services view's leading column.
    // Mirrors the stack helper's tiers off item.status / item.health
    // so the same colour family lights up across both views.
    itemStatusDotClass(item) {
      if (!item) return 'is-unknown';
      const status = String(item.status || '').toLowerCase();
      const health = String(item.health || '').toLowerCase();
      if (status === 'error' || health === 'offline')   return 'is-down';
      if (status === 'update' || health === 'degraded') return 'is-degraded';
      if (status === 'up-to-date' && health === 'healthy') return 'is-up';
      if (status === 'unknown' || !status) return 'is-unknown';
      return 'is-unknown';
    },

    // hover-title for the host status dot. Surfaces the probe
    // wall-clock so operators can tell whether an `unknown` status
    // came from a fast 5xx or a slow 30s hang. Backend stamps
    // `_probe_elapsed_ms` on every `/api/hosts/one/{id}` response;
    // missing on the legacy `/api/hosts` path (returns empty string).
    hostProbeTitle(h) {
      if (!h || typeof h._probe_elapsed_ms !== 'number') return '';
      const ms = h._probe_elapsed_ms;
      const status = h.status || '';
      const human = ms < 1000
        ? `${ms} ms`
        : `${(ms / 1000).toFixed(1)} s`;
      return this.t('hosts_extra.probe_title', { elapsed: human, status }) || `Probe took ${human}`;
    },

    // --- Node drawer (Nodes view → click a node) ---
    openNodeDrawer(node) {
      // Seed the drawer with the currently stored alias (if any) so the
      // input is pre-populated with whatever's in the DB. The identity
      // default (node.name) is shown as a placeholder, not a value, so
      // saving an empty string clears the mapping.
      const current = (this.settings.beszel_aliases || {})[node.name] || '';
      this.drawerNode = { name: node.name, aliasInput: current };
    },
    async saveNodeBeszelMapping() {
      if (!this.drawerNode) return;
      const name = this.drawerNode.name;
      const val = (this.drawerNode.aliasInput || '').trim();
      // Merge into the existing map: blank = delete entry, otherwise set.
      const map = { ...(this.settings.beszel_aliases || {}) };
      if (val) map[name] = val;
      else delete map[name];
      this.drawerNodeSaving = true;
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ beszel_aliases: map }),
        });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          throw new Error(d.detail || `HTTP ${r.status}`);
        }
        this.settings.beszel_aliases = map;
        this.showToast(
          val
            ? `Beszel mapping saved: ${name} → ${val}`
            : `Beszel mapping cleared for ${name}`,
          'success',
        );
        this.drawerNode = null;
        // Force the next gather to re-read the alias map and re-match
        // Beszel systems. Without force=true the cached items stay
        // attached to the old alias resolution until CACHE_TTL lapses.
        await this.refresh(true);
      } catch (e) {
        this.showToast(`Save failed: ${e.message}`, 'error');
      } finally {
        this.drawerNodeSaving = false;
      }
    },
    parseEvents(j) { try { return JSON.parse(j || '[]'); } catch (e) { return []; } },
    // formatTime + formatTimeShort are kept for backwards compatibility
    // with any template bindings — both now delegate to the unified
    // fmt* helpers so every date in the UI renders in dd/mm/yyyy format.
    formatTime(ts) { return this.fmtDate(ts); },
    formatTimeShort(ts) {
      if (!ts) return '—';
      const d = new Date(ts * 1000);
      if (isNaN(d.getTime())) return '—';
      const pad = n => String(n).padStart(2, '0');
      return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    },

    // Date + time in dd/mm/yyyy, HH:MM:SS (24-hour). Used everywhere
    // a full timestamp is shown (history, sessions, asset cache,
    // etc). Uniform across browsers so two operators in different
    // locales see identical strings in the admin tables.
    fmtDate(ts) {
      if (!ts) return '—';
      const d = new Date(ts * 1000);
      if (isNaN(d.getTime())) return '—';
      const pad = n => String(n).padStart(2, '0');
      return pad(d.getDate()) + '/' + pad(d.getMonth() + 1) + '/' + d.getFullYear()
           + ', ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    },
    // Date only — dd/mm/yyyy. Use when the time-of-day would be noise
    // (e.g. "created 25/04/2026" on a config row).
    fmtDateOnly(ts) {
      if (!ts) return '—';
      const d = new Date(ts * 1000);
      if (isNaN(d.getTime())) return '—';
      const pad = n => String(n).padStart(2, '0');
      return pad(d.getDate()) + '/' + pad(d.getMonth() + 1) + '/' + d.getFullYear();
    },
    // Date + short time — dd/mm/yyyy, HH:MM (no seconds). Use when
    // second-precision is noise (scheduled-next-run, audit-log list).
    fmtDateTimeShort(ts) {
      if (!ts) return '—';
      const d = new Date(ts * 1000);
      if (isNaN(d.getTime())) return '—';
      const pad = n => String(n).padStart(2, '0');
      return pad(d.getDate()) + '/' + pad(d.getMonth() + 1) + '/' + d.getFullYear()
           + ', ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    },
    copy(text) {
      navigator.clipboard?.writeText(text);
      this.showToast(this.t('toasts.copied'));
    },
    async confirmDialog({ title, html, icon = 'warning', confirmText, confirmColor }) {
      // No hex fallbacks — every token below is declared in BOTH :root
      // blocks. If `_cssVar` returns "" something is genuinely broken
      // at the token level and we want it to surface visibly rather
      // than be silently papered over by a literal that diverges from
      // the rest of the theme. Per CLAUDE.md's "no fallback literals"
      // rule (extended to JS-side reads).
      const r = await Swal.fire({
        title, html, icon,
        showCancelButton: true,
        confirmButtonText: confirmText || this.t('actions.confirm'),
        cancelButtonText: this.t('actions.cancel'),
        reverseButtons: true,
        focusCancel: true,
        background: this._cssVar('--surface'),
        color: this._cssVar('--text'),
        confirmButtonColor: confirmColor || this._cssVar('--warning'),
        cancelButtonColor: this._cssVar('--btn-cancel-bg'),
      });
      return r.isConfirmed;
    },
    showToast(msg, type='success') {
      this.toast = msg;
      this.toastType = type;
      clearTimeout(this._tt);
      this._tt = setTimeout(() => this.toast = '', 4000);
    },

    // Error formatter — prefers the structured `error_code` + i18n
    // lookup (`errors.OG####`) over the backend's raw `error` string
    // when the backend returned a code. Falls back to the English
    // `error` text when no code is present or translation is missing.
    // See logic/errors.py for the catalogue.
    formatError(resp, fallback) {
      if (resp && resp.error_code) {
        const key = 'errors.' + String(resp.error_code);
        const localized = this.t(key, resp.error_params || {});
        // window.I18N.load falls back to English text when the key is
        // missing; the English text IS the DEFAULT_MESSAGES entry. If
        // the backend sent a more specific override_message (e.g.
        // upstream's `details` field), prefer that so operators see
        // the concrete failure, not the generic catalog text.
        if (resp.error && resp.error !== localized && !key.startsWith('errors.undefined')) {
          return resp.error;
        }
        if (localized && localized !== key) return localized;
      }
      return (resp && resp.error) || fallback || '';
    },

    async itemAction(item) {
      if (this.isItemBusy(item)) return;
      const ok = item.stack_id
        ? await this.confirmDialog({
            title: this.t('dialogs.update_stack_title'),
            html: this.t('dialogs.update_stack_html', { name: item.stack }),
            icon: 'warning', confirmText: this.t('actions.update_stack'),
          })
        : await this.confirmDialog({
            title: this.t('dialogs.recreate_container_title'),
            html: this.t('dialogs.recreate_container_html', { name: item.name }),
            icon: 'warning', confirmText: this.t('actions.recreate'),
          });
      if (!ok) return;
      if (this.isItemBusy(item)) return;
      const key = item.stack_id
        ? this._busyKey('stack', item.stack_id)
        : this._busyKey('ctn', item.raw_id);
      const url = item.stack_id
        ? `/api/update/stack/${item.stack_id}`
        : `/api/update/container/${item.raw_id}`;
      this._markBusy(key);
      try {
        const r = await fetch(url, { method: 'POST' });
        if (!r.ok) throw new Error(await r.text());
        this.showToast(this.t('toasts.queued', { name: item.stack || item.name }));
        this.drawerItem = null;
        this.pollOpsNow();
      } catch (e) {
        this._clearBusy(key);
        this.showToast(this.t('toasts.failed_with_error', { error: e.message }), 'error');
      }
    },
    async updateStack(stack) {
      if (this.isStackBusy(stack)) return;
      const ok = await this.confirmDialog({
        title: this.t('dialogs.update_stack_title'),
        html: this.t('dialogs.update_stack_html', { name: stack.name }),
        icon: 'warning', confirmText: this.t('actions.update_stack'),
      });
      if (!ok) return;
      if (this.isStackBusy(stack)) return;
      const key = this._busyKey('stack', stack.stack_id);
      this._markBusy(key);
      try {
        const r = await fetch(`/api/update/stack/${stack.stack_id}`, { method: 'POST' });
        if (!r.ok) throw new Error(await r.text());
        this.showToast(this.t('toasts.queued', { name: stack.name }));
        this.pollOpsNow();
      } catch (e) {
        this._clearBusy(key);
        this.showToast(this.t('toasts.failed_with_error', { error: e.message }), 'error');
      }
    },
    async restartService(item) { return this.restartItem(item); },
    async restartItem(item) {
      if (!this.isRestartable(item)) return;
      if (this.isRestartBusy(item)) return;
      const isService = item.type === 'service';
      const body = isService
        ? this.t('dialogs.restart_service_html', { name: item.name })
        : this.t('dialogs.restart_container_html', { name: item.name });
      const ok = await this.confirmDialog({
        title: isService ? this.t('dialogs.restart_service_title') : this.t('dialogs.restart_container_title'),
        html: body,
        icon: 'question', confirmText: this.t('actions.restart'), confirmColor: this._cssVar('--primary'),
      });
      if (!ok) return;
      if (this.isRestartBusy(item)) return;
      const key = isService ? this._busyKey('svc', item.raw_id) : this._busyKey('ctn', item.raw_id);
      const url = isService
        ? `/api/restart/service/${item.raw_id}`
        : `/api/restart/container/${item.raw_id}`;
      this._markBusy(key);
      try {
        const r = await fetch(url, { method: 'POST' });
        if (!r.ok) throw new Error(await r.text());
        this.showToast(this.t('toasts.restart_queued', { name: item.name }));
        this.drawerItem = null;
        this.pollOpsNow();
      } catch (e) {
        this._clearBusy(key);
        this.showToast(this.t('toasts.failed_with_error', { error: e.message }), 'error');
      }
    },
    // Unhealthy-agent banner's one-click force-restart of the
    // Portainer agent service. Backend auto-discovers the service
    // by image-prefix + name pattern; on ambiguous discovery the op
    // surfaces the candidate list and refuses to auto-pick.
    async restartSwarmAgent() {
      const ok = await this.confirmDialog({
        title: this.t('swarm_agent_banner.confirm_title'),
        text:  this.t('swarm_agent_banner.confirm_text'),
        icon: 'warning',
        confirmText: this.t('swarm_agent_banner.restart_button'),
        confirmColor: this._cssVar('--warning'),
      });
      if (!ok) return;
      if (this.swarmAgentRestartBusy) return;
      this.swarmAgentRestartBusy = true;
      try {
        const r = await fetch('/api/swarm/restart-agent', { method: 'POST' });
        if (!r.ok) throw new Error(await r.text());
        this.showToast(this.t('swarm_agent_banner.restart_queued'));
        this.pollOpsNow();
      } catch (e) {
        this.showToast(this.t('toasts.failed_with_error', { error: e.message }), 'error');
      } finally {
        // Cleared after a short delay so the spinner stays visible
        // long enough for the operator to see it fired (the actual
        // op runs in the background and surfaces in the ops queue).
        setTimeout(() => { this.swarmAgentRestartBusy = false; }, 1200);
      }
    },
    async bulkRestart() {
      const picked = this.selectionRestartable().filter(i => !this.isRestartBusy(i));
      if (picked.length === 0) { this.showToast(this.t('toasts.nothing_restartable'), 'error'); return; }
      const items = picked.slice(0, 8)
        .map(i => `<li><code>${i.name}</code> <span class="hint-sub">· ${i.type}</span></li>`)
        .join('');
      const more = picked.length > 8 ? this.t('dialogs.bulk_update_more', { count: picked.length - 8 }) : '';
      const titleKey = picked.length === 1 ? 'dialogs.bulk_restart_title' : 'dialogs.bulk_restart_title_plural';
      const ok = await this.confirmDialog({
        title: this.t(titleKey, { count: picked.length }),
        html: this.t('dialogs.bulk_restart_html', { items, more }),
        icon: 'question', confirmText: this.t('actions.restart'), confirmColor: this._cssVar('--primary'),
      });
      if (!ok) return;
      let okCount = 0, fail = 0;
      for (const i of picked) {
        const isService = i.type === 'service';
        const key = isService ? this._busyKey('svc', i.raw_id) : this._busyKey('ctn', i.raw_id);
        if (this.busy[key]) continue;
        this._markBusy(key);
        try {
          const url = isService ? `/api/restart/service/${i.raw_id}` : `/api/restart/container/${i.raw_id}`;
          const r = await fetch(url, { method: 'POST' });
          if (r.ok) okCount++;
          else { fail++; this._clearBusy(key); }
        } catch (e) { fail++; this._clearBusy(key); }
      }
      this.selected = [];
      this.pollOpsNow();
      this.showToast(this.t('toasts.restart_result', { ok: okCount, fail }), fail ? 'error' : 'success');
    },
    async removeContainer(item) {
      if (this.isItemBusy(item)) return;
      const ok = await this.confirmDialog({
        title: this.t('dialogs.remove_container_title'),
        html: this.t('dialogs.remove_container_html', { name: item.name }),
        icon: 'warning', confirmText: this.t('actions.remove'), confirmColor: this._cssVar('--danger'),
      });
      if (!ok) return;
      if (this.isItemBusy(item)) return;
      const key = this._busyKey('ctn', item.raw_id);
      this._markBusy(key);
      try {
        const r = await fetch(`/api/remove/container/${item.raw_id}`, { method: 'POST' });
        if (!r.ok) throw new Error(await r.text());
        this.showToast(this.t('toasts.remove_queued', { name: item.name }));
        this.drawerItem = null;
        this.pollOpsNow();
      } catch (e) {
        this._clearBusy(key);
        this.showToast(this.t('toasts.failed_with_error', { error: e.message }), 'error');
      }
    },
    async bulkUpdate() {
      const picked = this.selectionUpdatable();
      const stackIds = new Set();
      const queue = [];
      for (const i of picked) {
        if (i.stack_id) {
          if (!stackIds.has(i.stack_id)) { stackIds.add(i.stack_id); queue.push(i); }
        } else {
          queue.push(i);
        }
      }
      const runnable = queue.filter(i => !this.isItemBusy(i));
      const skipped = queue.length - runnable.length;
      if (runnable.length === 0) {
        this.showToast(skipped ? this.t('toasts.already_running', { count: skipped }) : this.t('toasts.nothing_to_update'), 'error');
        return;
      }
      const items = runnable.slice(0, 8).map(i => `<li><code>${i.stack || i.name}</code></li>`).join('');
      const more = runnable.length > 8 ? this.t('dialogs.bulk_update_more', { count: runnable.length - 8 }) : '';
      const skippedNote = skipped ? this.t('dialogs.bulk_update_skipped', { count: skipped }) : '';
      const ok = await this.confirmDialog({
        title: this.t('dialogs.bulk_update_title'),
        html: this.t('dialogs.bulk_update_html', {
          runnable: runnable.length, stacks: stackIds.size,
          skipped_note: skippedNote, items, more,
        }),
        icon: 'warning', confirmText: this.t('actions.update'),
      });
      if (!ok) return;
      let okCount = 0, fail = 0;
      for (const i of runnable) {
        const key = i.stack_id ? this._busyKey('stack', i.stack_id) : this._busyKey('ctn', i.raw_id);
        if (this.busy[key]) continue;
        this._markBusy(key);
        try {
          const url = i.stack_id ? `/api/update/stack/${i.stack_id}` : `/api/update/container/${i.raw_id}`;
          const r = await fetch(url, { method: 'POST' });
          if (r.ok) okCount++;
          else { fail++; this._clearBusy(key); }
        } catch (e) { fail++; this._clearBusy(key); }
      }
      this.selected = [];
      this.pollOpsNow();
      this.showToast(this.t('toasts.bulk_result', { ok: okCount, fail }), fail ? 'error' : 'success');
    },
    async bulkRemove() {
      return this._bulkRemoveItems(this.selectionRemovable(), { clearSelection: true });
    },
    async bulkRemoveAll() {
      // Fast-action topbar button — clean up every stopped/failed container
      // on the cluster without having to select them one by one.
      return this._bulkRemoveItems(this.removableAll(), { clearSelection: false });
    },
    async _bulkRemoveItems(source, { clearSelection }) {
      const picked = source.filter(i => !this.isItemBusy(i));
      if (picked.length === 0) {
        this.showToast(this.t('toasts.nothing_removable'), 'error');
        return;
      }
      const items = picked.slice(0, 8).map(i => `<li><code>${i.name}</code></li>`).join('');
      const more = picked.length > 8 ? this.t('dialogs.bulk_update_more', { count: picked.length - 8 }) : '';
      const titleKey = picked.length === 1 ? 'dialogs.bulk_remove_title' : 'dialogs.bulk_remove_title_plural';
      const ok = await this.confirmDialog({
        title: this.t(titleKey, { count: picked.length }),
        html: this.t('dialogs.bulk_remove_html', { items, more }),
        icon: 'warning', confirmText: this.t('actions.remove'), confirmColor: this._cssVar('--danger'),
      });
      if (!ok) return;
      let okCount = 0, fail = 0;
      for (const i of picked) {
        const key = this._busyKey('ctn', i.raw_id);
        if (this.busy[key]) continue;
        this._markBusy(key);
        try {
          const r = await fetch(`/api/remove/container/${i.raw_id}`, { method: 'POST' });
          if (r.ok) okCount++;
          else { fail++; this._clearBusy(key); }
        } catch (e) { fail++; this._clearBusy(key); }
      }
      if (clearSelection) this.selected = [];
      this.pollOpsNow();
      this.showToast(this.t('toasts.remove_result', { ok: okCount, fail }), fail ? 'error' : 'success');
    },
  };
}
