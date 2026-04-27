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
const KNOWN_ICONS = new Set([
  '5g', 'adguard-home', 'alexa', 'alienware', 'amazon', 'ansible',
  'apache', 'apc', 'apc-ups', 'apple', 'apple-light', 'apple-tv-plus',
  'apple-tv-plus-light', 'apprise', 'aqara', 'asus', 'authentik', 'bazarr',
  'beszel', 'bose', 'caddy', 'chromecast', 'cisco', 'database',
  'ddns-updater', 'debian', 'dell', 'deluge', 'docker', 'dovecot',
  'dozzle', 'esxi', 'fing', 'firetv', 'flaresolverr', 'forgejo',
  'freenas', 'ftth', 'gigabyte', 'glinet', 'glinet-dark', 'google',
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
  'speedtest-tracker', 'squid', 'stalwart', 'synology', 'tailscale', 'tautulli',
  'tracearr', 'traefik', 'transmission', 'truenas', 'truenas-core', 'truenas-scale',
  'ubiquiti', 'ubuntu', 'ui', 'unifi', 'ups', 'uptime-kuma',
  'vcenter', 'vdsl', 'veeam', 'vmware', 'vsphere', 'wd',
  'webmin', 'windows', 'windows-10', 'windows-server', 'wireguard', 'xiaomi',
  'zabbix',
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
    stats: {}, _statsTimer: null, _maxSize: 1,
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
    view: (['stacks','services','nodes','hosts','history','settings','admin'].includes(localStorage.getItem('view')) ? localStorage.getItem('view') : 'stacks'),
    search: '', statusFilter: '', healthFilter: '',
    sortField: 'name', sortDir: 'asc',
    selected: [],
    expanded: (() => { try { return JSON.parse(localStorage.getItem('expanded') || '[]'); } catch (e) { return []; } })(),
    loading: false,
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
    // Slide-out drawer mode (#239) — clicking a host row opens this
    // drawer instead of expanding the row inline. `drawerHost` is the
    // live host object reference (kept up-to-date by the existing
    // `loadHosts` reconcile loop, since rows mutate fields in place
    // rather than reassigning the array). Null = drawer closed.
    drawerHost: null,
    hostsSearch: '',
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
    // Per-field validation errors for inline rendering (#136). Keys
    // follow the pattern "<scope>_<idx>_<field>" (e.g.
    // "host_3_webmin_url", "group_0_range"). `setFieldError` /
    // `clearFieldError` / `hasFieldError` / `fieldError` wrap the map
    // so callers don't have to know the exact storage shape.
    fieldErrors: {},
    hostsConfigLoading: false,
    hostsConfigSaving: false,
    hostsConfigDirty: false,
    hostsConfigFilter: '',
    // Client-side pagination for the Admin → Hosts editor (#331). At
    // ~200 hosts the rendered DOM (each row is a multi-input form
    // card) becomes heavy; slicing the rendered list to one page at
    // a time keeps tab switches + filter typing snappy. Full array
    // still lives in `hostsConfig`, so dirty tracking + duplicate-id
    // validator + save-path are untouched. Page index persists across
    // reloads (#340) so the operator returns to the same page after
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
    hostsDiscovery: { beszel: [], pulse: [], webmin: [] },
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
    },
    assetStatus: null,
    assetTestResult: null,
    assetCache: null,
    assetRefreshing: false,
    assetSaving: false,
    // Per-system time-series cache keyed by Beszel record id.
    // Shape: { [system_id]: { loading, error, series: [{t,cpu,mp,dp,b,...}] } }
    hostHistory: {},
    hostHistoryRange: 1,  // hours — matches img_8's "Last 1 hour" default
    // Wall-clock millis ticked every 30s by init() — drives the
    // "Updated Xm ago" freshness hint in the host-drawer chart-grid
    // header (#363). Reactive so Alpine re-evaluates the helper.
    hostHistoryNow: 0,
    showHotkeys: false,
    opsExpanded: true,
    toast: '', toastType: 'success', _tt: null,
    // Auto-refresh cadence (seconds; 0 = off). Persisted to
    // localStorage so the operator's chosen cadence survives a
    // browser refresh — previously it reset to 0 on every reload.
    autoRefresh: (() => {
      try {
        const n = parseInt(localStorage.getItem('autoRefresh') || '0', 10);
        return Number.isFinite(n) && n >= 0 ? n : 0;
      } catch { return 0; }
    })(),
    _autoTimer: null, _opsTimer: null,
    cacheLabel: '',
    settings: { apprise_url: '', apprise_tag: '', portainer_public_url: '', debug_panel_enabled: true,
                // TOTP / 2FA policy defaults so the Admin -> Config inputs
                // bind cleanly before the first /api/settings response.
                totp_allowed: true, totp_required_for_admins: false, totp_required_for_users: false,
                totp_lockout_max_failures: 5, totp_lockout_minutes: 15 },
    schedulerSaving: false,
    openMeteoSaving: false,
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
    // Dirty trackers (#220) — same pattern as `sshSettingsDirty` /
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
    _oidcBaseline: '',
    _debugBaseline: '',
    _totpPolicyBaseline: '',
    // Admin → Config (#337). DB-overridable process tunables. `tuningForm`
    // holds string values (blank = clear / fall back to env). `tuningEffective`
    // mirrors the GET /api/admin/tuning response so the form can render
    // env-fallback / default placeholders + the resolved current value.
    tuningKeys: [
      'tuning_cache_ttl_seconds',
      'tuning_stats_cache_ttl_seconds',
      'tuning_registry_concurrency',
      'tuning_stats_concurrency',
      'tuning_stats_history_days',
      'tuning_stats_sample_interval_seconds',
    ],
    tuningForm: {},
    tuningEffective: {},
    tuningLoaded: false,
    tuningSaving: false,
    _tuningBaseline: '',
    // Admin → Version — direct VERSION.txt editor; Save writes the
    // file. Pre-populates from /api/admin/version on load.
    versionForm: { major: 0, minor: 0, patch: 0 },
    versionState: { current: '', major: 0, minor: 0, patch: 0 },
    versionLoaded: false,
    versionSaving: false,
    _versionBaseline: '',
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
    // TOTP / 2FA enrolment state (#345). Mirrors the /api/me/totp shape
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
    adminSections: [
      { id: 'general',        label: 'General',         icon: 'sliders' },
      { id: 'users',          label: 'Users',           icon: 'users' },
      { id: 'authentication', label: 'Authentication',  icon: 'shield' },
      { id: 'sessions',       label: 'Sessions',        icon: 'monitor' },
      { id: 'tokens',         label: 'API tokens',      icon: 'key' },
      { id: 'notifications',  label: 'Notifications',   icon: 'bell' },
      { id: 'portainer',      label: 'Portainer',       icon: 'box' },
      { id: 'oidc',           label: 'Authentik OIDC',  icon: 'id-card' },
      { id: 'host_stats',     label: 'Host stats',      icon: 'activity' },
      { id: 'hosts',          label: 'Hosts',           icon: 'server' },
      { id: 'host_groups',    label: 'Host Groups',     icon: 'layers' },
      { id: 'ssh',            label: 'SSH',             icon: 'terminal' },
      { id: 'assets',         label: 'Asset inventory', icon: 'package' },
      { id: 'schedules',      label: 'Schedules',       icon: 'calendar' },
      { id: 'backups',        label: 'Backups',         icon: 'archive' },
      { id: 'logs',           label: 'Logs',            icon: 'file-text' },
      { id: 'config',         label: 'Config',          icon: 'settings' },
      { id: 'version',        label: 'Version',         icon: 'tag' },
      { id: 'debug',          label: 'Debug',           icon: 'bug' },
    ],
    // App-logs viewer state. Polled when the Logs tab is visible.
    // `logLines` is append-only during a session; clear() wipes both
    // the UI list and the server-side ring.
    logLines: [],
    logSinceTs: 0,
    logAuto: true,
    logFilter: '',
    logPollHandle: null,
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
    scheduleKinds: ['prune_node', 'prune_all_nodes', 'gather_refresh', 'backup', 'asset_inventory_refresh'],
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
    users: [],
    sessions: [],
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
          // #313 — apply per-user UI prefs from the server so the
          // weather/clock toggles (etc.) sync across devices for the
          // same login. Runs AFTER `me` lands so applyServerUiPrefs
          // can read this.me.ui_prefs.
          if (typeof this.applyServerUiPrefs === 'function') {
            this.applyServerUiPrefs();
          }
          // Capture the header-prefs dirty baseline AFTER hydration
          // (#379). The baseline initialiser is `''` (the empty
          // string sentinel set on the data() block) and the
          // snapshot helper returns a populated JSON string, so
          // without this re-baseline the form always reads dirty
          // until the first Save click.
          if (typeof this._headerPrefsSnapshot === 'function') {
            this._headerPrefsBaseline = this._headerPrefsSnapshot();
          }
          // #345 — fetch TOTP status alongside /api/me so the Profile
          // section can render its 2FA card without a click-induced
          // round-trip. Authentik users (no local password) skip the
          // call; the server short-circuits its response either way.
          if (typeof this.loadTotpStatus === 'function') {
            this.loadTotpStatus();
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
            this._hostsTimer = setInterval(() => this.loadHosts(), this.statsInterval * 1000);
          }
        } else if (this._hostsTimer) {
          clearInterval(this._hostsTimer);
          this._hostsTimer = null;
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
      // page (#340). Pairs with the localStorage initialiser above.
      this.$watch('hostsConfigPage', v => {
        try { localStorage.setItem('hostsConfigPage', String(v)); } catch {}
      });
      // Same persistence for the host-groups editor (#348).
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
      // Asset inventory — load the cached asset list once on boot so
      // the drawer can surface matched rows (vendor / model / serial /
      // location) without an extra round-trip per row-expand. Silent
      // failure is fine (asset inventory is optional).
      this.loadAssetCache();
      this.startVersionWatcher();
      this.startHeaderClock();
      this.startHeaderWeather();
      // Restart the persisted auto-refresh timer (if any). The initial
      // value was read from localStorage at component-construction
      // time; we only need to kick off the interval here.
      if (this.autoRefresh > 0) this.setAutoRefresh(this.autoRefresh);
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
          this._hostsTimer = setInterval(() => this.loadHosts(), this.statsInterval * 1000);
        }
      }
      setInterval(() => this.updateCacheLabel(), 1000);
      // Tick `hostHistoryNow` every second so the host-drawer charts'
      // "Updated Xs/Xm/Xh ago" label counts in real time (#363).
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
      // Hydrates per-user notification opt-in state (#357) from the
      // resolved `notify_events` map; rebuilds the baseline snapshot so
      // the dirty-getter compares against the freshly-loaded values.
      const events = {};
      const src = (this.me && this.me.notify_events) || {};
      for (const k of (this.notifyEventKeys || [])) {
        // notifyEventKeys carries `notify_event_<name>`; the per-user
        // map keys are the bare names (matching the data model).
        const bare = k.replace(/^notify_event_/, '');
        events[bare] = !!src[bare];
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
    // Per-user notification toggle disable gate (#357). Returns true
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
        // Per-user notification opt-in (#357). Sent as a separate PATCH
        // so the admin-gate refusal (400 with detail "Event 'X' is
        // disabled by admin") surfaces a useful message without rolling
        // back the profile edit. Backend payload keys are the bare
        // event names (matching the storage shape inside ui_prefs).
        const events = this.profileForm.notify_events || {};
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
        if (key && (h.beszel_id || h.ne_url) && !this.hostHistory[key]) {
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
        this._startLogPoll();
      }
      // The four ex-Settings sections all read from the same /api/settings
      // payload, so a single load covers all of them. Load on every
      // open so edits from another tab don't go stale.
      else if (['notifications', 'general', 'portainer', 'oidc', 'host_stats'].includes(tab)) {
        await this.loadSettings();
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
      else if (tab === 'version') {
        await this.loadVersionAdmin();
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
        this.schedules = d.schedules || [];
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
        this.scheduleQueue = d.queue || [];
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
      this.editingSchedule = {
        ...s,
        params_text: JSON.stringify(s.params || {}, null, 2),
        cadence_mode: s.cadence_mode || (s.run_at_hhmm ? 'daily' : 'interval'),
        run_at_hhmm: s.run_at_hhmm || '',
        days_of_week: Array.isArray(s.days_of_week) ? [...s.days_of_week] : [],
        day_of_month: s.day_of_month || 1,
      };
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
      ];
      const subset = {};
      for (const k of pick) subset[k] = s[k];
      try { return JSON.stringify(subset); } catch { return ''; }
    },
    // Cheap dirty check — called from the template every render. String
    // comparison is O(length) which is trivial for the ~15-key subset.
    hostStatsDirty() {
      return this._hostStatsBaseline !== this._hostStatsSnapshot();
    },

    async saveHostStats() {
      const raw = this.settings.host_stats_source || 'none';
      const active = new Set(
        raw.split(',').map(s => s.trim()).filter(s => s && s !== 'none'),
      );
      const valid = new Set(['beszel', 'node_exporter', 'pulse', 'webmin']);
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
          // Re-capture baseline so the dirty indicator clears now that
          // the server has the same values we just sent.
          this._hostStatsBaseline = this._hostStatsSnapshot();
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
    // (#342) — POSTs the toggle, re-baselines on success so the amber
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
      if (!q) return this.logLines;
      return this.logLines.filter(l => l.text.toLowerCase().includes(q));
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
          const ta = document.createElement('textarea');
          ta.value = body;
          ta.setAttribute('readonly', '');
          ta.style.position = 'absolute';
          ta.style.left = '-9999px';
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
        // #345 — TOTP / 2FA state changes (enrol / verify / lockout
        // / admin-disable). Shares the OIDC accent token via CSS.
        'totp',
      ]);
      // Replace [xxx] at the start of (or inside) the line. Allow
      // underscores / hyphens for tag names like [host_net_sampler].
      const withTags = esc.replace(/\[([a-z][a-z0-9_.\-]*?)\]/gi, (_m, tag) => {
        const key = tag.toLowerCase();
        const cls = tagColors.has(key) ? ('log-tag log-tag--' + key) : 'log-tag';
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
      } catch (_) {}
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
    // TOTP / 2FA management (#345). Three call sites:
    //   - Profile (self): loadTotpStatus / startTotpEnrol / confirmTotpEnrol
    //                     / disableTotpSelf / regenerateTotpCodes
    //   - Admin -> Users: adminDisableTotp(u)
    //   - Login page does its own thing in /js/login.js
    // The /api/me/totp call returns plaintext backup codes; the
    // hide/unhide eye is purely client-side.
    // ----------------------------------------------------------------
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
        el.innerHTML = qr.createSvgTag({ cellSize: 6, margin: 4, scalable: true });
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

    // Per-user force-2FA toggle (#376). Admin flips this flag to make
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
      } catch (_) {}
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
      } catch (_) {}
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
      // #313 — also push to server-side per-user prefs so the same
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
    // #313 — apply server-side ui_prefs onto local state. Called from
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
    reloadForNewVersion() {
      const sep = location.search ? '&' : '?';
      location.href = location.pathname + location.search + sep + '_v=' + encodeURIComponent(this.newVersionString || Date.now());
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
        // Unconditional "what arrived" line so the operator can spot
        // the disconnect between server-side `/api/stats` (which may
        // return rich data) and what the SPA actually has in
        // `this.stats` (which the templates read). If this line shows
        // 0 keys but `curl /api/stats` shows non-zero rows, the SPA
        // has a parse / state issue, not a backend issue.
        try {
          const ids = Object.keys(this.stats);
          const withStats = ids.filter(id => this.stats[id] && this.stats[id].has_stats).length;
          const withSize = ids.filter(id => this.stats[id] && this.stats[id].has_size).length;
          const withStale = ids.filter(id => this.stats[id] && this.stats[id]._stale).length;
          const sample = ids.slice(0, 2).map(id => ({
            id,
            has_stats: !!(this.stats[id] && this.stats[id].has_stats),
            has_size: !!(this.stats[id] && this.stats[id].has_size),
            cpu: this.stats[id] && this.stats[id].cpu_percent,
            mem_used: this.stats[id] && this.stats[id].mem_usage,
            mem_limit: this.stats[id] && this.stats[id].mem_limit,
            size_root: this.stats[id] && this.stats[id].size_root,
            stale: !!(this.stats[id] && this.stats[id]._stale),
          }));
          console.log('[stats] loadStats: keys=' + ids.length + ' with_stats=' + withStats + ' with_size=' + withSize + ' stale=' + withStale + ' items=' + (this.items || []).length, sample);
        } catch (_) { /* never let logging crash the path */ }
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
        await this.loadStats();
        if (this.statsInterval > 0) {
          this._statsTimer = setTimeout(tick, this.statsInterval * 1000);
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
          this._hostsTimer = setInterval(() => this.loadHosts(), this.statsInterval * 1000);
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
        try {
          const sparkIds = Object.keys(this.sparks);
          const withData = sparkIds.filter(id => Array.isArray(this.sparks[id]) && this.sparks[id].length > 0).length;
          const sample = sparkIds.slice(0, 2).map(id => ({
            id, points: (this.sparks[id] || []).length,
            first: (this.sparks[id] || [])[0] || null,
            last: (this.sparks[id] || []).slice(-1)[0] || null,
          }));
          console.log('[sparks] loadSparks: keys=' + sparkIds.length + ' with_points=' + withData + ' requested_for=' + ids.length, sample);
        } catch (_) { /* never let logging crash the path */ }
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

    async refresh(force=false) {
      this.loading = true;
      try {
        const r = await fetch('/api/items' + (force ? '?force=true' : ''));
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        this.items = d.items || [];
        this.stacks = d.stacks || [];
        this.nodes = d.nodes || {};
        // Per-node capacity + uptime proxy — see logic/gather.py's nodes_info.
        // Drives the Nodes view's normalized CPU/mem bars.
        this.nodesInfo = d.nodes_info || {};
        // UX-003: drives the "Portainer not configured" empty-state hint.
        // null → not yet known (skeleton state); true/false → explicit.
        if (typeof d.portainer_configured === 'boolean') {
          this.portainerConfigured = d.portainer_configured;
        }
        // Non-UI label; stays English since it's diagnostic-adjacent.
        this.cacheLabel = d.cached ? `cached ${d.age}s ago` : 'fresh';
        // Only fire stats alongside a forced refresh when stats
        // polling is actually enabled. With statsInterval=0 the
        // operator explicitly chose "off", so auto-refresh
        // shouldn't sneak a /api/stats call in via the back door.
        if (force && this.statsInterval > 0) this.loadStats(true);
      } catch (e) { this.showToast(this.t('toasts.load_failed', { error: e.message }), 'error'); }
      this.loading = false;
    },
    updateCacheLabel() {},
    async loadSettings() {
      try {
        const r = await fetch('/api/settings');
        const d = await r.json();
        this.settings = {
          apprise_url: d.apprise_url || '',
          apprise_tag: d.apprise_tag || '',
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
          // Scheduler — IANA zone. Blank = container-local (legacy).
          scheduler_timezone: d.scheduler_timezone || '',
          // Open-Meteo upstream (weather widget). Blank = default.
          open_meteo_url: d.open_meteo_url || '',
          // Per-service master switches (#204). Default true so legacy
          // deploys keep working before the operator interacts with
          // the toggles.
          apprise_enabled:    d.apprise_enabled    !== false,
          open_meteo_enabled: d.open_meteo_enabled !== false,
          portainer_enabled:  d.portainer_enabled  !== false,
          ssh_enabled:        d.ssh_enabled        !== false,
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
        // TOTP / 2FA policy (#345). Hydrate the five fields so the
        // Admin -> Config tab can render the inputs + the existing
        // saveSettings flow can ship the values back.
        this.settings.totp_allowed              = (d.totp_allowed !== false);
        this.settings.totp_required_for_admins  = !!d.totp_required_for_admins;
        this.settings.totp_required_for_users   = !!d.totp_required_for_users;
        this.settings.totp_lockout_max_failures =
          Number.isFinite(d.totp_lockout_max_failures) ? d.totp_lockout_max_failures : 5;
        this.settings.totp_lockout_minutes      =
          Number.isFinite(d.totp_lockout_minutes) ? d.totp_lockout_minutes : 15;
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
          };
        }
      } catch (e) { console.error(e); }
    },

    async saveOidcSettings() {
      const body = {
        oidc_enabled:      !!this.oidcForm.enabled,
        oidc_issuer_url:   (this.oidcForm.issuer_url || '').trim(),
        oidc_client_id:    (this.oidcForm.client_id || '').trim(),
        oidc_redirect_uri: (this.oidcForm.redirect_uri || '').trim(),
        oidc_scopes:       (this.oidcForm.scopes || '').trim(),
        oidc_admin_group:  (this.oidcForm.admin_group || '').trim(),
        oidc_verify_tls:   !!this.oidcForm.verify_tls,
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
      try {
        const r = await fetch('/api/oidc/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ issuer_url: (this.oidcForm.issuer_url || '').trim() }),
        });
        const j = await r.json().catch(() => ({}));
        this.oidcTestResult = { ok: !!j.ok, status: j.status || 0, detail: j.detail || '' };
      } catch (e) {
        this.oidcTestResult = { ok: false, status: 0, detail: this.t('toasts.network_error') };
      }
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
      const body = {
        // Master switch (#204) saved alongside the URL / API key /
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
      } catch (e) {
        this.portainerTestResult = { ok: false, status: 0, detail: this.t('toasts.network_error') };
      }
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
          // Master switch (#204) saved alongside the rest of the form
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
        // down on a long drawer (#364). $nextTick lets x-show flip
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
        // Map close codes to user-friendly reasons.
        if (ev.code === 4401) {
          this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.cookie_expired');
        } else if (ev.code === 4403) {
          this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.not_admin');
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
        this.showToast(this.t('toasts.settings_saved'));
      } catch (e) { this.showToast(this.t('toasts.load_failed', { error: e.message }), 'error'); }
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
      'notify_event_prune_success',
      'notify_event_prune_failure',
      'notify_event_user_login',
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
      { label: 'prune',              success: 'notify_event_prune_success',              failure: 'notify_event_prune_failure' },
    ],
    // Security events — single-toggle per event (no success/failure
    // pair like ops events). Rendered as a separate row beneath the
    // ops-events grid.
    notifySecurityEvents: [
      { label: 'user_login', key: 'notify_event_user_login' },
    ],
    _appriseSnapshot() {
      const s = this.settings || {};
      const events = {};
      for (const k of this.notifyEventKeys) events[k] = !!s[k];
      return JSON.stringify({
        enabled: !!s.apprise_enabled,
        url:     s.apprise_url || '',
        tag:     s.apprise_tag || '',
        events,
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
    // User-side convenience handlers (#378). Mutate profileForm.notify_events
    // (the per-user opt-in map keyed by BARE event name — no
    // notify_event_ prefix) and respect the admin-disabled gate so
    // the user can't bulk-enable an event the admin has globally
    // turned off (the backend would 400 such an attempt anyway).
    _bareEventName(k) { return String(k || '').replace(/^notify_event_/, ''); },
    setAllUserNotifyEvents(value) {
      const v = !!value;
      const f = this.profileForm || {};
      if (!f.notify_events) f.notify_events = {};
      for (const k of this.notifyEventKeys) {
        if (this.userNotifyEventDisabledByAdmin(k)) continue;
        f.notify_events[this._bareEventName(k)] = v;
      }
    },
    setUserNotifyEventsErrorsOnly() {
      const f = this.profileForm || {};
      if (!f.notify_events) f.notify_events = {};
      for (const g of this.notifyEventGroups) {
        if (!this.userNotifyEventDisabledByAdmin(g.success)) {
          f.notify_events[this._bareEventName(g.success)] = false;
        }
        if (!this.userNotifyEventDisabledByAdmin(g.failure)) {
          f.notify_events[this._bareEventName(g.failure)] = true;
        }
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
        baseEnabled: !!s.enabled,
        baseIssuer:  s.issuer_url || '',
        baseCid:     s.client_id || '',
        baseRedir:   s.redirect_uri || '',
        baseScopes:  s.scopes || '',
        baseGrp:     s.admin_group || '',
        baseVerify:  s.verify_tls !== false,
      });
    },
    oidcDirty()      { return this._oidcBaseline      !== this._oidcSnapshot(); },
    // Admin → Config (#337). Load DB / env / default state from the
    // dedicated endpoint so the form can render placeholders for the
    // env-fallback behind each input. `tuningForm[k]` is always a
    // string — blank means "clear the override", non-blank means
    // "store this number".
    async loadTuning() {
      try {
        const r = await fetch('/api/admin/tuning');
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        this.tuningEffective = d || {};
        const form = {};
        for (const k of this.tuningKeys) {
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
      for (const k of this.tuningKeys) out[k] = (f[k] == null ? '' : String(f[k]).trim());
      return JSON.stringify(out);
    },
    tuningDirty() { return this._tuningBaseline !== this._tuningSnapshot(); },
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
      this.tuningSaving = true;
      try {
        const body = {};
        for (const k of this.tuningKeys) {
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
    // Admin → Version — direct VERSION.txt editor. All three
    // components (MAJOR / MINOR / PATCH) are operator-editable; Save
    // writes the file. The deployment pipeline keeps bumping the same
    // file's PATCH on every successful deploy.
    async loadVersionAdmin() {
      try {
        const r = await fetch('/api/admin/version');
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        this.versionState = d || {};
        this.versionForm = { major: d.major, minor: d.minor, patch: d.patch };
        this._versionBaseline = this._versionSnapshot();
        this.versionLoaded = true;
      } catch (e) {
        this.showToast(this.t('admin.version.load_failed', { error: e.message }), 'error');
      }
    },
    _versionSnapshot() {
      const f = this.versionForm || {};
      return JSON.stringify({
        major: Number(f.major) || 0,
        minor: Number(f.minor) || 0,
        patch: Number(f.patch) || 0,
      });
    },
    versionDirty() { return this._versionBaseline !== this._versionSnapshot(); },
    async saveVersionAdmin() {
      if (this.versionSaving) return;
      this.versionSaving = true;
      try {
        const f = this.versionForm || {};
        const body = {
          major: Number(f.major) || 0,
          minor: Number(f.minor) || 0,
          patch: Number(f.patch) || 0,
        };
        const r = await fetch('/api/admin/version', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        const d = await r.json();
        this.versionState = d || {};
        this.versionForm = { major: d.major, minor: d.minor, patch: d.patch };
        this._versionBaseline = this._versionSnapshot();
        this.showToast(this.t('admin.version.saved_toast', { version: d.current }));
        // Bump the SPA's footer / cache-bust knob — the running build
        // didn't change, but the rendered version string did.
        this.appVersion = d.current;
      } catch (e) {
        this.showToast(this.t('admin.version.save_failed', { error: e.message }), 'error');
      } finally {
        this.versionSaving = false;
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
    // Auto-save a single per-service "enabled" master switch (#204).
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
    _historyQueryParams() {
      // Build the shared ?stack=&op_type=...&since=... query string used by
      // loadHistory() and the CSV/JSON export links, so filters stay in sync.
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
      p.set('limit', '500');
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
      return `/api/history.${fmt}?` + this._historyQueryParams().toString();
    },
    get historyStackOptions() {
      // Populate the stack dropdown from whatever stacks we currently see
      // in the live cache (avoids a dedicated /api endpoint). Alphabetical.
      return [...new Set((this.stacks || []).map(s => s.name).filter(Boolean))].sort();
    },
    async loadHistory() {
      try {
        const r = await fetch('/api/history?' + this._historyQueryParams().toString());
        this.history = (await r.json()).history || [];
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
        this._opsTimer = setTimeout(tick, 1500);
      };
      tick();
    },
    pollOpsNow() { this.pollOps(); },
    setAutoRefresh(seconds) {
      this.autoRefresh = seconds;
      try { localStorage.setItem('autoRefresh', String(seconds)); } catch {}
      if (this._autoTimer) clearInterval(this._autoTimer);
      if (seconds > 0) this._autoTimer = setInterval(() => this.refresh(true), seconds * 1000);
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
    providerStates(h) {
      if (!h) return [];
      const active = this.hostsActiveSources || [];
      const globalOk = this.providersWorkingGlobally();
      const got = new Set(h.providers || []);
      const out = [];
      const badStatus = v => {
        const s = String(v || '').toLowerCase();
        return s === 'paused' || s === 'down' || s === 'unreachable';
      };
      const add = (name, mapped, selfStatus) => {
        if (!mapped) return;
        if (!active.includes(name)) return;
        // Globally-broken provider — suppress the chip entirely so
        // operators see the failure once in Settings (not N times in
        // the Hosts grid). Exception: if THIS host got data from it,
        // the provider IS working — render the ok chip.
        if (!globalOk.has(name) && !got.has(name)) return;
        let state;
        if (!got.has(name))      state = 'failing';
        else if (badStatus(selfStatus)) state = 'failing';
        else                     state = 'ok';
        out.push({ name, state });
      };
      add('beszel',        !!(h.beszel_name && String(h.beszel_name).trim()), h.beszel_status);
      add('pulse',         !!(h.pulse_name  && String(h.pulse_name).trim()),  h.pulse_status);
      add('node_exporter', !!(h.ne_url      && String(h.ne_url).trim()),      null);
      add('webmin',        !!(h.webmin_name && String(h.webmin_name).trim()), null);
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
    staleAge(obj) {
      if (!obj) return '';
      const ts = obj._stale_ts;
      if (!ts || ts <= 0) return '';
      const ms = ts * 1000;
      const ago = this.fmtAgo(ms);
      // i18n: tooltip surface, not visible label. Translators handle
      // the "stale_marker.tooltip" key with the {age} placeholder.
      try { return (window.t && window.t('stale_marker.tooltip', { age: ago })) || ('Last live data ' + ago + ' ago'); }
      catch (_) { return 'Last live data ' + ago + ' ago'; }
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
        return mapped.startsWith('/') || /^https?:/i.test(mapped) ? mapped : '/' + mapped;
      }
      if (mapped) return `/img/icons/${mapped}.svg`;
      for (const [prefix, slug] of prefixes) {
        if (natural.startsWith(prefix)) return `/img/icons/${slug}.svg`;
      }
      if (!natural) return '';
      // Only return a URL when the slug actually exists on disk —
      // otherwise the browser fires a 404 for every stack/host name
      // that doesn't happen to match a brand. Operator complaint:
      // "this is a stack without an image, why system looking for
      // image" → fixed by gating on KNOWN_ICONS.
      if (KNOWN_ICONS.has(natural)) return `/img/icons/${natural}.svg`;
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
        hostStatsSources: [...sourceSet],     // array form for new callers
      };
    },
    // Label for the green/red chip on a node row — "3 sources" when
    // multiple are active, else the single provider name. Keeps the
    // header compact when the operator enabled everything.
    nodeProviderChip(host) {
      const st = this.nodeStats(host);
      const arr = st.hostStatsSources || [];
      if (arr.length === 0) return 'host';
      if (arr.length === 1) return arr[0] === 'node_exporter' ? 'exporter' : arr[0];
      return `${arr.length} sources`;
    },
    // Hover tooltip for the chip — lists the active provider names.
    nodeProviderList(host) {
      const st = this.nodeStats(host);
      const arr = (st.hostStatsSources || []).map(s =>
        s === 'node_exporter' ? 'node-exporter' : s
      );
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
    fmtBytes(n) {
      if (n == null) return '—';
      if (n <= 0) return '0 B';
      const u = ['B','KB','MB','GB','TB'];
      let i = 0;
      while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
      return (n >= 10 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i];
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
    barColor(pct) {
      // Kept for backward compat; prefer barLevel() which returns a CSS class.
      if (pct > 85) return 'var(--danger)';
      if (pct > 60) return 'var(--warning)';
      return 'var(--success)';
    },
    barLevel(pct) {
      // Maps a percentage to the `.warn` / `.crit` class on `.stat-bar`, which
      // drives the fill colour from the stylesheet. Empty string = default green.
      if (pct > 85) return 'crit';
      if (pct > 60) return 'warn';
      return '';
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
            { keys: ['3'],       label: _t('hotkeys.items.view_history'),   run: () => this.view = 'history' },
            { keys: ['?'],       label: _t('hotkeys.items.show_help'),      run: () => this.showHotkeys = true },
            { keys: ['Esc'],     label: _t('hotkeys.items.close_clear'),    run: null, note: _t('hotkeys.items.close_clear_note') },
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
        if (this.userMenuOpen) { this.userMenuOpen = false; e.preventDefault(); return; }
        if (this.showHotkeys) { this.showHotkeys = false; e.preventDefault(); return; }
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

    // --- Admin → Hosts: curated host list editor ---
    async loadHostsConfig() {
      // Guard against clobbering unsaved edits. The operator can hit
      // Reload deliberately (confirms) or bypass when clean.
      if (this.hostsConfigDirty && !confirm(
        'You have unsaved changes in the Hosts list. Discard them and reload from the server?'
      )) return;
      this.hostsConfigLoading = true;
      try {
        const r = await fetch('/api/hosts/config');
        if (!r.ok) {
          this.showToast(`Load hosts failed: HTTP ${r.status}`, 'error');
          return;
        }
        const d = await r.json();
        this.hostsConfig = Array.isArray(d.hosts) ? d.hosts : [];
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
          // Stamp a stable per-row uid the first time we see this
          // row. Used as the x-for :key so DOM elements never tear
          // down + re-mount mid-typing (which loses input focus and
          // triggers the "still typing in ID is causing refresh"
          // symptom). Persisted into hostsConfig so subsequent
          // reconciliation passes preserve identity even when the
          // sort order changes.
          if (!row._uid) {
            row._uid = 'r' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
          }
        }
        this.hostsConfigDirty = false;
        this.rebuildHostsConfigOrder();
        // Clamp paging to the loaded data — preserves the persisted
        // page (#340) when valid, and falls back to the new last page
        // when the data has shrunk. Don't unconditionally reset to 1:
        // the operator expects to return to the same page after reload.
        this.hostsConfigPage = Math.min(
          Math.max(1, this.hostsConfigPage),
          this.hostsConfigTotalPages(),
        );
      } catch (e) {
        this.showToast(`Load hosts failed: ${e.message}`, 'error');
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
          this.showToast(`Discover failed: HTTP ${r.status}`, 'error');
          return;
        }
        const d = await r.json();
        this.hostsDiscovery = {
          beszel: Array.isArray(d.beszel) ? d.beszel : [],
          pulse:  Array.isArray(d.pulse)  ? d.pulse  : [],
          webmin: Array.isArray(d.webmin) ? d.webmin : [],
        };
        const errs = d.errors || {};
        const errKeys = Object.keys(errs);
        const bTotal = this.hostsDiscovery.beszel.length;
        const pTotal = this.hostsDiscovery.pulse.length;
        const wTotal = this.hostsDiscovery.webmin.length;
        if (errKeys.length && (bTotal + pTotal + wTotal) === 0) {
          this.showToast(`No provider responded: ${errKeys.map(k => k + '=' + errs[k]).join(' · ')}`, 'error');
        } else {
          const parts = [];
          if (bTotal)  parts.push(`${bTotal} Beszel`);
          if (pTotal)  parts.push(`${pTotal} Pulse`);
          if (wTotal)  parts.push(`${wTotal} Webmin`);
          this.showToast(
            parts.length
              ? `Discovered ${parts.join(', ')} name(s) — autocomplete is live`
              : 'No enabled provider returned any hosts — check connection settings',
            parts.length ? 'success' : 'error',
          );
        }
      } catch (e) {
        this.showToast(`Discover failed: ${e.message}`, 'error');
      } finally {
        this.hostsDiscovering = false;
      }
    },
    // Admin-editor view filter. We return a list of ``{row, idx}``
    // tuples so the template can render only matching rows while
    // still having the original index for move/remove/test actions.
    filteredHostsConfig() {
      const q = (this.hostsConfigFilter || '').trim().toLowerCase();
      // Display order is a SNAPSHOT rebuilt only by
      // `rebuildHostsConfigOrder()` (called on load / add / remove /
      // blur of custom_number). Sorting reactively on every keystroke
      // was breaking input focus: typing "2" into a custom_number
      // field before finishing "24" would move the row mid-typing,
      // tearing down the DOM node and killing focus. Using a stable
      // snapshot means the sort applies on commit (blur), not on
      // keystroke — the operator can type "24" uninterrupted and the
      // row re-sorts when they tab away.
      const order = (this.hostsConfigSortedOrder || []);
      const cfg = this.hostsConfig || [];
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
      if (!q) return all;
      return all.filter(({ row }) => {
        const hay = [
          row.id, row.label, row.ne_url,
          row.beszel_name, row.pulse_name,
          row.webmin_name, row.webmin_url,
          row.url, row.icon, row.ip,
        ].filter(Boolean).join(' ').toLowerCase();
        return hay.includes(q);
      });
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
      // Alpine reactivity, so we just compute the safe page.
      const page = Math.min(Math.max(1, this.hostsConfigPage), totalPages);
      const start = (page - 1) * per;
      return all.slice(start, start + per);
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

    // #359 — fired on `@focusout` of the host-card wrapper. Defers
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
            enabled:     true,
          };
        }
        added[key][field] = name;
      };
      for (const n of (this.hostsDiscovery.beszel || [])) addOrMerge(n, 'beszel_name');
      for (const n of (this.hostsDiscovery.pulse  || [])) addOrMerge(n, 'pulse_name');
      for (const n of (this.hostsDiscovery.webmin || [])) addOrMerge(n, 'webmin_name');
      const rows = Object.values(added);
      if (!rows.length) {
        this.showToast(this.t('admin_hosts.import.nothing_new'), 'success');
        return;
      }
      this.hostsConfig.push(...rows);
      this.hostsConfigDirty = true;
      this.showToast(`Added ${rows.length} host(s) — review and Save to persist.`, 'success');
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
        `Exported ${(this.hostsConfig || []).length} host(s) to file.`,
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
        this.showToast(`Invalid JSON: ${e.message}`, 'error');
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
        // Let the user pick — ``confirm()`` only offers OK / Cancel,
        // so we use it as merge-vs-replace via "OK to replace?".
        mode = confirm(
          `Replace all ${existing.length} current hosts with ${incoming.length} from the file?\n\n` +
          `OK   → replace\n` +
          `Cancel → merge (update existing IDs, add new ones)`
        ) ? 'replace' : 'merge';
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
      this.hostsConfigDirty = true;
      this.showToast(
        `Imported ${cleanIncoming.length} host(s) — review and Save to persist.`,
        'success'
      );
    },

    async testAllHostRows() {
      const rows = (this.hostsConfig || [])
        .map((row, idx) => ({ row, idx }))
        .filter(({ row }) =>
          row.enabled !== false &&
          ((row.beszel_name || '').trim() ||
           (row.pulse_name || '').trim() ||
           (row.ne_url || '').trim())
        );
      if (!rows.length) {
        this.showToast(this.t('admin_hosts.test.no_eligible'), 'error');
        return;
      }
      this.hostsTestingAll = true;
      try {
        await Promise.all(rows.map(({ idx }) => this.testHostRow(idx)));
        this.showToast(`Tested ${rows.length} host(s) — see per-row results.`, 'success');
      } finally {
        this.hostsTestingAll = false;
      }
    },
    // True when at least one provider (Beszel / Pulse / node-exporter
    // / Webmin) is mapped on this row. The "Test providers" button
    // disables when none are set — there's nothing to probe and the
    // backend would return all-skipped anyway.
    rowHasProviderMapping(row) {
      if (!row) return false;
      return !!(
        (row.beszel_name || '').trim() ||
        (row.pulse_name  || '').trim() ||
        (row.ne_url      || '').trim() ||
        (row.webmin_name || '').trim() ||
        (row.webmin_url  || '').trim()
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
        const r = await fetch('/api/hosts/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            beszel_name: (row.beszel_name || '').trim(),
            pulse_name:  (row.pulse_name  || '').trim(),
            ne_url:      (row.ne_url      || '').trim(),
            webmin_url:  (row.webmin_url  || '').trim(),
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
      // The global `ssh_fqdn_suffix` setting (e.g. ".home.lan") is
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
          // Mirror the label convenience from onHostRowEdit so first
          // fill doesn't leave a blank label behind.
          if (!(row.label || '').trim()) {
            // asset.name is a better label than the FQDN when both exist.
            row.label = String(asset.name || primary).trim();
            filled.push('label');
          }
        }
      }
      if (!(row.label || '').trim()) {
        const label = asset.name
          || ((asset.vendor && asset.model) ? `${asset.vendor} ${asset.model}` : '')
          || asset.vendor || asset.model || '';
        if (label) {
          row.label = String(label).trim();
          filled.push('label');
        }
      }
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
        url: '',
        icon: '',
        // Free-text IP field — operator-maintained, not derived.
        ip: '',
        // Per-host SSH overrides — empty object = use global defaults.
        ssh: {},
        enabled: true,
        // Stable identity for x-for keying (matches the loadHostsConfig
        // hydration path).
        _uid: 'r' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36),
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
        if (!row.label) row.label = value;
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
        };
        const slug = aliases[h.icon.toLowerCase()] || h.icon;
        return '/img/icons/' + slug + '.svg';
      }
      // Step 2 — exact-slug match on any field.
      const candidates = [
        h.id, h.label, h.host, h.beszel_name, h.pulse_name,
      ].filter(Boolean);
      for (const c of candidates) {
        const url = this.iconUrlFor(c);
        if (url) return url;
      }
      // Step 3 — keyword scan. Lowercase hay from label + id, then
      // test each known token. Order matters: longer / more specific
      // tokens win first so "nginx-proxy-manager" beats "nginx".
      const hay = [h.label, h.id, h.host]
        .filter(Boolean).join(' ').toLowerCase();
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
        // Linux distros
        ['debian',                'debian'],
        ['ubuntu',                'ubuntu'],
        ['linux mint',            'linuxmint'],
        ['linuxmint',             'linuxmint'],
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
        if (hay.includes(needle)) return '/img/icons/' + slug + '.svg';
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
        if (sshIn.disabled) sshOut.disabled = true;
        return {
          id:            (h.id || '').trim(),
          label:         (h.label || h.id || '').trim(),
          custom_number: num,
          ne_url:        (h.ne_url || '').trim(),
          beszel_name:   (h.beszel_name || '').trim(),
          pulse_name:    (h.pulse_name || '').trim(),
          webmin_name:   (h.webmin_name || '').trim(),
          url:           (h.url || '').trim(),
          icon:          (h.icon || '').trim(),
          ssh:           sshOut,
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
        // Re-stamp each row with its webmin_url (the hosts_config
        // endpoint doesn't know about aliases, so the field is
        // load-bearing for the editor UI only) AND its _uid (re-use
        // the previous one when the id matches; mint fresh otherwise).
        for (const row of this.hostsConfig) {
          row.webmin_url = webminAliases[row.id] || '';
          if (!row.ssh || typeof row.ssh !== 'object') row.ssh = {};
          row._uid = oldUidById[row.id]
            || ('r' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36));
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
        this.showToast(`Saved ${d.count} host(s)`, 'success');
        // The Hosts tab consumes this list — refresh it so the new
        // mapping takes effect without a full page reload.
        if (this.view === 'hosts') this.loadHosts();
      } catch (e) {
        this.showToast(`Save failed: ${e.message}`, 'error');
      } finally {
        this.hostsConfigSaving = false;
      }
    },

    // --- Host groups (#93 + #134) ---
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
      // Format: "<number>. <name>" — dot separator (#244) for visual
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
      // Same Alpine select-mount race as #230 — when sub-group rows
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

    // ---- Inline-field-error helpers (#136) ----
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
    groupedHosts() {
      const hosts = this.filteredHosts();
      const all = (this.hostGroups || []).slice().sort(
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

      for (const h of hosts) {
        const cn = h.custom_number;
        const ci = (cn === null || cn === undefined || cn === '') ? null
          : (Number.isFinite(+cn) ? +cn : null);
        let placed = false;
        if (ci !== null) {
          for (const b of buckets) {
            const g = b.group;
            if (ci < g.range_start || ci > g.range_end) continue;
            // Parent matched — now try the sub-groups first
            // (most-specific wins). Break on the first sub-group
            // hit and DON'T also push to the parent's list.
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
            break;
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
      const mode = (this.assetForm.auth_mode === 'lifetime_token')
                     ? 'lifetime_token' : 'oauth2';
      const body = { auth_mode: mode };
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
          this.hostsError = `HTTP ${r.status}`;
          this.hosts = [];
          return;
        }
        const d = await r.json();
        this.hostsConfigured = !!d.configured;
        this.hostsError = d.error || '';
        this.hostsProviderErrors = d.provider_errors || {};
        this.hostsActiveSources = Array.isArray(d.active) ? d.active : [];
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
          'ssh_disabled', 'asset',
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
            // Existing row — overlay curated fields only and flag
            // loading (refreshHostRow will patch stats in place).
            for (const k of CURATED_FIELDS) {
              if (k in h) existing[k] = h[k];
            }
            existing._seq = i;
            // Unconfigured hosts skip the loading flag → grey dot
            // stays grey across reloads instead of flashing back to
            // "loading" before the (skipped) probe would have run.
            existing._loading = !skipProbe;
            if (skipProbe) existing.status = 'unconfigured';
          } else {
            // Brand-new row — push the full skeleton so it renders.
            this.hosts.push({
              ...h,
              _seq: i,
              _loading: !skipProbe,
              status: skipProbe ? 'unconfigured' : 'loading',
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
      } catch (e) {
        this.hostsError = `Network: ${e.message}`;
        this.hosts = [];
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
      const queue = this.hosts
        .filter(h => h.status !== 'unconfigured')
        .map(h => h.id);
      const PARALLEL = 6;
      const worker = async () => {
        while (queue.length) {
          const id = queue.shift();
          if (!id) break;
          await this.refreshHostRow(id);
        }
      };
      const workers = [];
      for (let i = 0; i < Math.min(PARALLEL, this.hosts.length); i++) {
        workers.push(worker());
      }
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
        if (key && (host.beszel_id || host.ne_url) && !this.hostHistory[key]) {
          this.loadHostHistory(host.beszel_id || '', host.id);
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
          && (this.drawerHost.beszel_id || this.drawerHost.ne_url)) {
        this.loadHostHistory(
          this.drawerHost.beszel_id || '',
          this.drawerHost.id,
        );
      }
    },

    // Fetch one host's merged stats and splice it back into the
    // hosts array. Preserves _seq and _loading handling so the UI
    // can distinguish "not yet loaded" from "probed but empty".
    async refreshHostRow(id) {
      try {
        const r = await fetch('/api/hosts/one/' + encodeURIComponent(id));
        if (!r.ok) {
          // Mark the row as probed but errored so the operator can
          // spot it. MUTATE in place (not splice) so Alpine doesn't
          // tear down + re-mount the row — that's what causes the
          // chart-flicker across 15s polling cycles.
          const row = this.hosts.find(h => h.id === id);
          if (row) {
            row._loading = false;
            row.status = 'unknown';
          }
          return;
        }
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
        for (const k of Object.keys(host)) {
          row[k] = host[k];
        }
        row._loading = false;
      } catch (_) {
        // Network failure — leave the row in skeleton state so the
        // next loadHosts cycle retries. Silent (no toast): on a big
        // fleet the spam would be worse than the missing data.
      }
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
        return { visible: false, cls: '', icon: '', title: '' };
      }
      if (err) {
        return {
          visible: true, cls: 'pill-error', icon: '✗',
          title: `${name} error: ${err}`,
        };
      }
      if (matchCount === 0) {
        return {
          visible: true, cls: 'pill-unknown', icon: '·',
          title: `${name} is enabled but matched no host`,
        };
      }
      return {
        visible: true, cls: 'pill-ok', icon: '✓',
        title: `${name} — ${matchCount} host${matchCount === 1 ? '' : 's'}`,
      };
    },
    // Drawer mode (#239): row is "expanded" when its host matches
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
    // Smooth-scrolls the host-drawer's inner scroller so the named
    // section (`data-host-section="<kind>-<host_id>"`) lands near the
    // top (#364). Plain `scrollIntoView({block:'start'})` worked in
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
    async copyDebugJson(v, label) {
      const text = this.fmtDebugJson(v);
      if (!text) {
        this.showToast(this.t('toasts_extra.nothing_to_copy'), 'warning');
        return;
      }
      try {
        await navigator.clipboard.writeText(text);
        this.showToast(this.t('toasts_extra.copied', { label: label || 'debug data' }), 'success');
      } catch (_) {
        // Fallback — let the user copy manually.
        window.prompt('Copy ' + (label || 'debug data') + ' (Cmd/Ctrl+C):', text);
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
      return !!(h.beszel_name || h.pulse_name || h.ne_url || h.webmin_name);
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
      const nameOf = (h) => (h.label || h.host || '').toLowerCase();
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
      if (q) {
        list = list.filter(h => {
          const hay = [
            h.host, h.label, h.id, h.platform, h.os, h.kernel,
            h.beszel_name, h.pulse_name, ...(h.providers || []),
          ].filter(Boolean).join(' ').toLowerCase();
          return hay.includes(q);
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
      // Load history once per (host, range). Subsequent re-opens of
      // the same host reuse the cached series until the range picker
      // forces a refetch. Same logic the legacy inline-expansion used.
      const drawerKey = this.hostHistoryKey(host);
      if (drawerKey && (host.beszel_id || host.ne_url) && !this.hostHistory[drawerKey]) {
        this.loadHostHistory(host.beszel_id || '', host.id);
      }
      // Dedicated drawer-history poll (#365) — keeps the chart series +
      // the `Updated Xs ago` freshness label in sync regardless of
      // whether the operator has the host-list poll enabled (when
      // `statsInterval=0` the loadHosts setInterval never fires, so a
      // hook inside loadHosts doesn't reach the drawer). 30s is a
      // sensible default for a drawer the operator is actively
      // watching; clears on closeHostDrawer.
      if (this._drawerHistoryTimer) {
        clearInterval(this._drawerHistoryTimer);
      }
      if (host.beszel_id || host.ne_url) {
        this._drawerHistoryTimer = setInterval(() => {
          if (!this.drawerHost) return;
          this.loadHostHistory(
            this.drawerHost.beszel_id || '',
            this.drawerHost.id,
          );
        }, 30 * 1000);
      }
      // Preload SSH status — admin only, and only when the host
      // didn't opt out. Without this the SSH card header shows
      // "Not configured" until the operator clicks to expand it —
      // a false-negative for fully-configured fleets.
      if (this.isAdmin && this.isAdmin() && !host.ssh_disabled) {
        this.loadSshStatus(host.id);
      }
    },
    closeHostDrawer() {
      this.drawerHost = null;
      if (this._drawerHistoryTimer) {
        clearInterval(this._drawerHistoryTimer);
        this._drawerHistoryTimer = null;
      }
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
    // (#363). Returns a short translated string like "Updated 2m ago"
    // or empty when the cache hasn't seen a successful fetch yet
    // (caller hides the line). Reads `hostHistoryNow` (ticked every
    // 30s) so the label stays current without re-fetching the data.
    hostHistoryFreshness(h) {
      if (!h) return '';
      const key = this.hostHistoryKey(h);
      const entry = this.hostHistory[key];
      if (!entry || !entry.loadedAt) return '';
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
    // True only when we KNOW the named NE collector is missing for this
    // host (sampler walked the window and never saw a non-null value).
    // Returns false for Beszel hosts (no `ne_url`), Beszel+NE hybrids
    // (history fetched via Beszel path → no collectors dict), and
    // freshly-loaded hosts whose first /api/hosts/history reply hasn't
    // landed yet. Drives the Disk I/O / Network "enable the collector"
    // empty-state branches in the host drawer (#347).
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
    setHostHistoryRange(hours) {
      this.hostHistoryRange = hours;
      // Reload the open drawer host's history (#323 — the range
      // buttons did nothing because this only iterated `hostsExpanded`,
      // which is the legacy inline-expansion state. The actual viewer
      // is the slide-out drawer keyed on `drawerHost`).
      if (this.drawerHost && (this.drawerHost.beszel_id || this.drawerHost.ne_url)) {
        this.loadHostHistory(this.drawerHost.beszel_id || '', this.drawerHost.id);
      }
      // Also handle any legacy expanded rows (kept for back-compat —
      // the inline-expansion code path is mostly dead but not yet
      // removed; covering both means the range works wherever the
      // user clicks it from).
      for (const name of (this.hostsExpanded || [])) {
        const host = (this.hosts || []).find(h => h.host === name);
        if (host && (host.beszel_id || host.ne_url)) {
          this.loadHostHistory(host.beszel_id || '', host.id);
        }
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
    xAxisFromSeries(systemId, slots = 5) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series || entry.series.length < 2) return [];
      const s = entry.series;
      const out = [];
      for (let i = 0; i < slots; i++) {
        const idx = Math.round(i * (s.length - 1) / (slots - 1));
        out.push(this._fmtAxisTime(s[idx]?.t));
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
      const step = (W - PAD_X * 2) / (pts.length - 1);
      const usableH = H - PAD_T - PAD_B;
      const xy = pts.map((v, i) => {
        const n = Number(v) || 0;
        return {
          x: PAD_X + i * step,
          y: PAD_T + usableH - ((n - lo) / (hi - lo || 1)) * usableH,
        };
      });
      const points = xy.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
      // Area path — polyline + closure back to baseline so we can fill
      // under the curve. Baseline is the chart's bottom.
      const baseY = (H - PAD_B).toFixed(1);
      const area = 'M'
        + `${xy[0].x.toFixed(1)},${baseY} `
        + xy.map(p => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
        + ` L${xy[xy.length - 1].x.toFixed(1)},${baseY} Z`;
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
    // and Disk I/O charts (#318 / #319) so the two polylines render
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
      return {
        min: this.fmtBytes(lo) + '/s',
        max: this.fmtBytes(hi) + '/s',
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
