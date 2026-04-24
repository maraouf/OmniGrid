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
        this.showToast('Language load failed', 'error');
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
    hostsConfigLoading: false,
    hostsConfigSaving: false,
    hostsConfigDirty: false,
    hostsConfigFilter: '',
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
    // Per-system time-series cache keyed by Beszel record id.
    // Shape: { [system_id]: { loading, error, series: [{t,cpu,mp,dp,b,...}] } }
    hostHistory: {},
    hostHistoryRange: 1,  // hours — matches img_8's "Last 1 hour" default
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
    settings: { apprise_url: '', apprise_tag: '', portainer_public_url: '' },
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
    // sshDryRun[host.id]       = "preview only" checkbox (defaults to true)
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
    sshSettings: {
      user: '', port: 22, private_key: '', passphrase: '',
      password: '', fqdn_suffix: '',
      known_hosts: '', destructive_patterns: '',
      private_key_set: false, passphrase_set: false, password_set: false,
    },
    sshSettingsDirty: false,
    sshSettingsBusy: false,
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
      { id: 'profile',       label: 'Profile' },
      { id: 'ignores',       label: 'Ignore list' },
      { id: 'language',      label: 'Language' },
      { id: 'shortcuts',     label: 'Keyboard shortcuts' },
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
    profileForm: { display_name: '', bio: '', email: '' },
    profileBusy: false,
    avatarBusy: false,
    adminSections: [
      { id: 'users',          label: 'Users' },
      { id: 'sessions',       label: 'Sessions' },
      { id: 'tokens',         label: 'API tokens' },
      { id: 'notifications',  label: 'Notifications' },
      { id: 'portainer',      label: 'Portainer' },
      { id: 'oidc',           label: 'Authentik OIDC' },
      { id: 'host_stats',     label: 'Host stats' },
      { id: 'hosts',          label: 'Hosts' },
      { id: 'ssh',            label: 'SSH' },
      { id: 'schedules',      label: 'Schedules' },
      { id: 'backups',        label: 'Backups' },
      { id: 'logs',           label: 'Logs' },
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
    scheduleKinds: ['prune_node', 'prune_all_nodes', 'gather_refresh', 'backup'],
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

    async init() {
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
          this._hostsTimer = setInterval(() => this.loadHosts(), 15000);
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
      // Webmin scratch-test URL persists so operators don't retype
      // the same host every time they reload Host Stats.
      this.$watch('webminTestUrl', v => { try { localStorage.setItem('webminTestUrl', v || ''); } catch {} });
      this.$watch('hostsConfigExpanded', v => {
        try { localStorage.setItem('hostsConfigExpanded', JSON.stringify(v || {})); } catch {}
      }, { deep: true });
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
        this._hostsTimer = setInterval(() => this.loadHosts(), 15000);
      }
      setInterval(() => this.updateCacheLabel(), 1000);
    },

    async logout() {
      try {
        await fetch('/api/local-auth/logout', { method: 'POST' });
      } catch (_) { /* ignore — clearing the cookie is the important bit */ }
      location.href = '/login';
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
      this.profileForm = {
        display_name: (this.me && this.me.display_name) || '',
        bio:          (this.me && this.me.bio)          || '',
        email:        (this.me && this.me.email)        || '',
      };
    },
    // Dirty-tracker for the Profile form. Compares the live form
    // against the baseline pulled from `me`. Any string divergence
    // in display_name / bio / email flips the Save button to its
    // "unsaved changes" visual treatment — same as Admin → Hosts
    // and the host-stats Save button.
    profileDirty() {
      if (!this.me) return false;
      const f = this.profileForm || {};
      const base = {
        display_name: this.me.display_name || '',
        bio:          this.me.bio          || '',
        email:        this.me.email        || '',
      };
      return (f.display_name || '') !== base.display_name
          || (f.bio          || '') !== base.bio
          || (f.email        || '') !== base.email;
    },

    async saveProfile() {
      if (this.profileBusy) return;
      this.profileBusy = true;
      try {
        const r = await fetch('/api/me/profile', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.profileForm),
        });
        if (r.ok) {
          this.showToast(this.t('toasts.profile_saved'));
          // Refresh `me` so the avatar badge / dropdown header use the new name.
          const rm = await fetch('/api/me');
          if (rm.ok) { this.me = await rm.json(); this.syncProfileForm(); }
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
        }
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
        if (h.beszel_id && !this.hostHistory[h.beszel_id]) {
          this.loadHostHistory(h.beszel_id, h.id);
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
        title: 'Prune ' + host + '?',
        html: `This runs <code class="mono">docker system prune -f --volumes</code> on <b>${host}</b>:<br><br>` +
              '• Stopped containers<br>' +
              '• Dangling images (not <code>-a</code>)<br>' +
              '• Unused networks<br>' +
              '• Unused local volumes — <b>orphaned data is deleted</b><br>' +
              '• Build cache<br><br>' +
              'Running workloads are NOT affected.',
        icon: 'warning',
        showCancelButton: true,
        confirmButtonText: 'Prune now',
        confirmButtonColor: 'var(--danger)',
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
          this.showToast('Prune started on ' + host + ' — see the floating panel (bottom-right) for progress. It will also show up in History when done.');
          // Kick an immediate ops poll so the button flips to "Pruning…".
          this.pollOnce && this.pollOnce();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || 'Prune failed to start', 'error');
        }
      } catch (_) {
        this.showToast('Network error', 'error');
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
      else if (['notifications', 'portainer', 'oidc', 'host_stats'].includes(tab)) {
        await this.loadSettings();
      }
      else if (tab === 'hosts') {
        await this.loadHostsConfig();
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
      try {
        const r = await fetch('/api/schedules/queue?limit=50');
        if (!r.ok) return;
        const d = await r.json();
        this.scheduleQueue = d.queue || [];
      } catch (_) {}
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
        if (tpl && !tpl.includes('{host}')) {
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
          this.showToast('Pulse URL is required', 'error');
          return;
        }
        if (!this.settings.pulse_token_set && !this.settings.pulse_token) {
          this.showToast('Pulse API token is required', 'error');
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
          this.showToast('Webmin user is required', 'error');
          return;
        }
        if (!this.settings.webmin_password_set && !this.settings.webmin_password) {
          this.showToast('Webmin password is required', 'error');
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
    async saveOpenMeteoUrl() {
      if (this.openMeteoSaving) return;
      this.openMeteoSaving = true;
      try {
        const url = (this.settings.open_meteo_url || '').trim().replace(/\/+$/, '');
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ open_meteo_url: url }),
        });
        if (r.ok) {
          this.settings.open_meteo_url = url;
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
        confirmButtonColor: 'var(--danger)',
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
        confirmButtonColor: 'var(--danger)',
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
            }),
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
        confirmButtonColor: 'var(--danger)',
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
        if (r.ok) this.showToast(this.t('toasts.password_reset'));
        else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.reset_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
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
    saveHeaderPrefs() {
      try {
        localStorage.setItem('headerClockEnabled',   String(!!this.headerClockEnabled));
        localStorage.setItem('headerWeatherEnabled', String(!!this.headerWeatherEnabled));
        localStorage.setItem('headerWeatherLat',     this.headerWeatherLat == null ? '' : String(this.headerWeatherLat));
        localStorage.setItem('headerWeatherLon',     this.headerWeatherLon == null ? '' : String(this.headerWeatherLon));
        localStorage.setItem('headerWeatherLabel',   this.headerWeatherLabel || '');
      } catch (_) {}
      // Re-fetch with the new settings immediately rather than waiting
      // for the 10-min tick. Also flushes weather to null when disabled.
      this.loadHeaderWeather();
      // Toast confirmation — per-browser preferences auto-save on
      // change, but operators coming from the per-user Profile section
      // expect a visual "saved" signal.
      if (this.showToast) this.showToast(this.t('toasts_extra.topbar_saved'), 'success');
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

    async loadStats(force=false) {
      try {
        const r = await fetch('/api/stats' + (force ? '?force=true' : ''));
        if (!r.ok) return;
        const d = await r.json();
        this.stats = d.stats || {};
        // Compute max image size across all items so the disk bar is
        // normalised against the largest thing on the cluster.
        let m = 1;
        for (const id in this.stats) {
          if (this.stats[id].size_root > m) m = this.stats[id].size_root;
        }
        this._maxSize = m;
      } catch (e) {}
    },
    pollStats() {
      if (this._statsTimer) clearTimeout(this._statsTimer);
      const tick = async () => {
        await this.loadStats();
        if (this.statsInterval > 0) {
          this._statsTimer = setTimeout(tick, this.statsInterval * 1000);
        }
      };
      tick();
    },
    setStatsInterval(seconds) {
      this.statsInterval = seconds;
      localStorage.setItem('statsInterval', String(seconds));
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
        if (!r.ok) return;
        const d = await r.json();
        this.sparks = d.series || {};
      } catch (e) {}
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
        // Non-UI label; stays English since it's diagnostic-adjacent.
        this.cacheLabel = d.cached ? `cached ${d.age}s ago` : 'fresh';
        if (force) this.loadStats(true);  // fire-and-forget, don't block UI
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
        };
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

        // --- Admin → SSH panel state ---
        this.hydrateSshSettings(d);
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
        this.oidcTestResult = { ok: false, status: 0, detail: 'Network error' };
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
        portainer_url:         (this.portainerForm.url || '').trim(),
        portainer_endpoint_id: parseInt(this.portainerForm.endpoint_id) || 1,
        portainer_verify_tls:  !!this.portainerForm.verify_tls,
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
        this.portainerTestResult = { ok: false, status: 0, detail: 'Network error' };
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
          detail: j.detail || (j.ok ? 'OK' : 'Failed'),
          systems: j.systems || [],
        };
      } catch (e) {
        this.beszelTestResult = { pending: false, ok: false, detail: 'Network error' };
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
          detail: j.detail || (j.ok ? 'OK' : 'Failed'),
          nodes: j.nodes || [],
        };
      } catch (e) {
        this.pulseTestResult = { pending: false, ok: false, detail: 'Network error' };
      }
    },
    async testWebminConnection() {
      // Probes ONE Webmin URL — user types the URL into webminTestUrl
      // since every Miniserv instance is per-host. Credentials come
      // from the settings form (or persisted values when blank).
      const url = (this.webminTestUrl || this.settings.webmin_url || '').trim();
      if (!url) {
        this.showToast('Enter a Webmin URL to test', 'error');
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
          detail: j.detail || (j.ok ? 'OK' : 'Failed'),
        };
      } catch (e) {
        this.webminTestResult = { pending: false, ok: false, detail: 'Network error' };
      }
    },
    // -------- SSH console ----------------------------------------------
    // Hydrate the Admin → SSH form from /api/settings. Called from
    // loadSettings() alongside the other provider-specific pulls so the
    // form has values ready when the admin opens the section.
    hydrateSshSettings(apiSettings) {
      const s = (apiSettings && apiSettings.ssh) || {};
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
      };
      this.sshSettingsDirty = false;
    },
    markSshSettingsDirty() { this.sshSettingsDirty = true; },
    async saveSshSettings() {
      this.sshSettingsBusy = true;
      try {
        const body = {
          ssh_default_user:              this.sshSettings.user || '',
          ssh_default_port:              parseInt(this.sshSettings.port, 10) || 22,
          ssh_fqdn_suffix:               this.sshSettings.fqdn_suffix || '',
          ssh_default_known_hosts:       this.sshSettings.known_hosts || '',
          ssh_destructive_patterns:      this.sshSettings.destructive_patterns || '',
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
        // Default to dry-run-checked so the UI can't accidentally
        // launch the first command on open.
        if (!(hostId in this.sshDryRun)) {
          this.sshDryRun = { ...this.sshDryRun, [hostId]: true };
        }
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
    // Prebuilt action → command map. Kept as a function so a future
    // sub-step (e.g. NIC name prompt) can inline its own logic.
    async runSshPreset(hostId, preset) {
      if (!hostId || !preset) return;
      let cmd = '';
      switch (preset) {
        case 'restart_beszel':
          cmd = 'systemctl restart beszel-agent || docker restart beszel-agent';
          break;
        case 'show_beszel_env':
          cmd = "systemctl show beszel-agent -p Environment || docker inspect beszel-agent --format '{{range .Config.Env}}{{println .}}{{end}}'";
          break;
        case 'set_nics': {
          const nic = window.prompt(this.t('hosts_extra_ssh.action_set_nics_prompt'), 'eth0');
          if (!nic) return;
          const safe = nic.replace(/[^A-Za-z0-9_.:-]/g, '');
          if (!safe) {
            // Validation-only toast — piggybacks on the generic "network"
            // key rather than adding a one-off for an edge case a typed
            // prompt already enforces client-side.
            this.showToast(this.t('toasts_extra.ssh.command_required'), 'error');
            return;
          }
          // Try the systemd drop-in first (native install); fall back
          // to a docker-env rewrite + restart for containerised agents.
          cmd =
            "if command -v systemctl >/dev/null && systemctl list-unit-files 2>/dev/null | grep -q beszel-agent; then " +
            "mkdir -p /etc/systemd/system/beszel-agent.service.d && " +
            "printf '[Service]\\nEnvironment=NICS=" + safe + "\\n' > /etc/systemd/system/beszel-agent.service.d/nics.conf && " +
            "systemctl daemon-reload && systemctl restart beszel-agent; " +
            "else docker inspect beszel-agent >/dev/null 2>&1 && " +
            "docker run --rm -v /var/run/docker.sock:/var/run/docker.sock alpine/socat TCP:localhost:1 - >/dev/null 2>&1; " +
            "echo 'Configure NICS in your compose / env file and redeploy: NICS=" + safe + "'; fi";
          this.sshCommand = { ...this.sshCommand, [hostId]: cmd };
          return;  // let the operator preview + run
        }
        case 'journal':
          cmd = 'journalctl -u beszel-agent -n 40 --no-pager';
          break;
        case 'ip_link':
          cmd = 'ip -o link show';
          break;
        default:
          return;
      }
      this.sshCommand = { ...this.sshCommand, [hostId]: cmd };
    },
    async runSshCommand(hostId) {
      const command = (this.sshCommand[hostId] || '').trim();
      if (!command) {
        this.showToast(this.t('toasts_extra.ssh.command_required'), 'error');
        return;
      }
      const dryRun = this.sshDryRun[hostId] !== false;
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
        const r = await fetch('/api/settings', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify(this.settings),
        });
        if (!r.ok) throw new Error(await r.text());
        this.showToast(this.t('toasts.settings_saved'));
      } catch (e) { this.showToast(this.t('toasts.load_failed', { error: e.message }), 'error'); }
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
          for (const o of all) {
            const wasUnknown = !this._opsSeen.has(o.id);
            this._opsSeen.add(o.id);
            if (o.status === 'running') continue;
            if (this._opLingerUntil[o.id]) continue;
            // Path 1: observed running → done.
            if (prevRunning.includes(o.id)) {
              this._opLingerUntil[o.id] = nowTs + LINGER_MS;
              continue;
            }
            // Path 2: brand-new op, already done (skip on first poll
            // so we don't surface historical ring-buffer entries).
            if (!firstPoll && wasUnknown) {
              this._opLingerUntil[o.id] = nowTs + LINGER_MS;
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
          const justDone = all.filter(o => o.status !== 'running' && prevRunning.includes(o.id));
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
        'lubelogger': '/img/icons/lubelogger.png',
        'myspeed': '/img/icons/myspeed.svg',
        'squid-proxy': '/img/icons/squid.png',
        'squid': '/img/icons/squid.png',
        'tracearr': '/img/icons/tracearr.png',
        'portainer': '/img/icons/portainer.png',
        'portainer-agent': '/img/icons/portainer.png',
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
      return `/img/icons/${natural}.svg`;
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
      // Raw CPU sum is 0..cores*100. Divide by cores to get 0..100%
      // normalised against THIS node's actual capacity. Clamp at 100 —
      // brief spikes over a single tick can exceed cores*100 due to
      // sub-second bursts, and a bar that pokes past 100% looks broken.
      const { cpuRaw, cores } = this.nodeStats(host);
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
      if (hi - lo < 0.5) { lo = Math.max(0, lo - 0.5); hi = lo + 1; }
      const step = W / (vals.length - 1);
      return vals.map((v, i) => {
        const x = (i * step).toFixed(1);
        const y = (H - ((v - lo) / (hi - lo)) * H).toFixed(1);
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
      // Ignore while typing in an input / textarea / select / contenteditable.
      const el = document.activeElement;
      const inField = el && (
        ['INPUT','TEXTAREA','SELECT'].includes(el.tagName) ||
        el.isContentEditable
      );
      // Escape works everywhere, including from inside an input — it's the
      // universal "get me out of here" key.
      if (e.key === 'Escape') {
        if (this.userMenuOpen) { this.userMenuOpen = false; e.preventDefault(); return; }
        if (this.showHotkeys) { this.showHotkeys = false; e.preventDefault(); return; }
        if (this.drawerItem) { this.drawerItem = null; e.preventDefault(); return; }
        if (this.selected.length) { this.clearSelection(); e.preventDefault(); return; }
        if (this.search || this.statusFilter || this.healthFilter) {
          this.clearFilters(); e.preventDefault(); return;
        }
        // Last resort: blur the focused element so search box releases focus.
        if (el && typeof el.blur === 'function') el.blur();
        return;
      }
      if (inField) return;
      // Never intercept browser / OS combos.
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      // Walk the catalog once, match on key (case-sensitive to distinguish
      // lowercase vs Shift+letter).
      for (const group of this.hotkeyGroups()) {
        for (const entry of group.items) {
          if (!entry.run) continue;
          // Entry keys is a sequence — for single-key entries the last item
          // is the literal character. Modifiers like 'Shift' are embedded
          // only so the help modal can render them.
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
        }
        this.hostsConfigDirty = false;
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
      const all = (this.hostsConfig || []).map((row, idx) => ({ row, idx }));
      if (!q) return all;
      return all.filter(({ row }) => {
        const hay = [
          row.id, row.label, row.ne_url,
          row.beszel_name, row.pulse_name,
          row.webmin_name, row.webmin_url,
          row.url, row.icon,
        ].filter(Boolean).join(' ').toLowerCase();
        return hay.includes(q);
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
        this.showToast('Nothing new to import — every discovered name is already configured.', 'success');
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
        this.showToast('No hosts found in file.', 'error');
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
        this.showToast('No enabled hosts with provider mappings to test.', 'error');
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
    isHostConfigExpanded(id) {
      if (!id) return true;  // empty-id rows (fresh adds) always expand
      return !!this.hostsConfigExpanded[id];
    },
    toggleHostConfigRow(id) {
      if (!id) return;
      const next = { ...this.hostsConfigExpanded };
      if (next[id]) delete next[id]; else next[id] = true;
      this.hostsConfigExpanded = next;
    },
    expandAllHostConfigRows() {
      const next = {};
      for (const h of (this.hostsConfig || [])) {
        if (h && h.id) next[h.id] = true;
      }
      this.hostsConfigExpanded = next;
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
    // behind a toggle so hosts with 20 ZFS datasets (img_14.png) don't
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
        // Per-host SSH overrides — empty object = use global defaults.
        ssh: {},
        enabled: true,
      });
      this.hostsConfigDirty = true;
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
      if (field === 'id') {
        const row = this.hostsConfig[idx];
        if (row && !row.label) row.label = value;
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
        // firewalls / routers / gateways
        ['opnsense',              'opnsense'],
        ['pfsense',               'pfsense'],
        ['mikrotik',              'mikrotik'],
        ['unifi',                 'unifi'],
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
      ];
      for (const [needle, slug] of tokens) {
        if (hay.includes(needle)) return '/img/icons/' + slug + '.svg';
      }
      return '';
    },
    removeHostRow(idx) {
      this.hostsConfig.splice(idx, 1);
      this.hostsConfigDirty = true;
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
      // Pre-save validation: if a Webmin URL is set, the webmin_name
      // must also be set. Otherwise the probe has a target but no key
      // to look the returned host up against — would silently produce
      // empty drawer cards. Reverse (name without URL) is allowed so
      // an operator can stage a name while rolling out Miniserv.
      for (let i = 0; i < (this.hostsConfig || []).length; i++) {
        const h = this.hostsConfig[i] || {};
        const wurl = (h.webmin_url || '').trim();
        const wname = (h.webmin_name || '').trim();
        if (wurl && !wname) {
          const id = (h.id || '').trim() || '(row ' + (i + 1) + ')';
          this.showToast(
            this.t('toasts_extra.webmin_url_without_name', { id }),
            'error',
          );
          return;
        }
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
        const sshIn = h.ssh || {};
        const sshOut = {};
        if ((sshIn.user || '').trim()) sshOut.user = sshIn.user.trim();
        if ((sshIn.host || '').trim()) sshOut.host = sshIn.host.trim();
        if (sshIn.port) {
          const p = parseInt(sshIn.port, 10);
          if (Number.isFinite(p) && p >= 1 && p <= 65535) sshOut.port = p;
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
        this.hostsConfig = d.hosts || [];
        // Re-stamp each row with its webmin_url (the hosts_config
        // endpoint doesn't know about aliases, so the field is
        // load-bearing for the editor UI only).
        for (const row of this.hostsConfig) {
          row.webmin_url = webminAliases[row.id] || '';
        }
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

    // --- Hosts view (Beszel-backed) ---
    async loadHosts() {
      this.hostsLoading = true;
      try {
        const r = await fetch('/api/hosts');
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
        // Stamp insertion order (_seq) so the 'seq' sort can restore the
        // curated-list order regardless of how later sorts reshuffle the
        // array. Backend already returns hosts in curated order.
        this.hosts = Array.isArray(d.hosts)
          ? d.hosts.map((h, i) => ({ ...h, _seq: i }))
          : [];
        this.hostsCuratedCount = Number.isFinite(d.curated_count) ? d.curated_count : 0;
        this.hostsEnabledCount = Number.isFinite(d.enabled_count) ? d.enabled_count : 0;
        // Trim persisted expansion state to hosts that actually exist
        // in the current response — otherwise a host removed from the
        // curated list stays in hostsExpanded forever.
        const valid = new Set(this.hosts.map(h => h.host));
        const cleaned = (this.hostsExpanded || []).filter(n => valid.has(n));
        if (cleaned.length !== (this.hostsExpanded || []).length) {
          this.hostsExpanded = cleaned;
        }
        // Kick off history fetches for pre-expanded hosts so the charts
        // populate without the operator having to re-click the drawer.
        // Only runs for hosts with a beszel_id (the history source) and
        // only when no cached series exists yet.
        for (const name of this.hostsExpanded || []) {
          const host = this.hosts.find(h => h.host === name);
          if (host && host.beszel_id && !this.hostHistory[host.beszel_id]) {
            this.loadHostHistory(host.beszel_id, host.id);
          }
        }
      } catch (e) {
        this.hostsError = `Network: ${e.message}`;
        this.hosts = [];
      } finally {
        this.hostsLoading = false;
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
    isHostExpanded(name) { return this.hostsExpanded.includes(name); },
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
      // Non-admin viewers keep the old gating so dead rows stay non-
      // interactive (nothing useful to show them anyway).
      if (this.me && this.me.role === 'admin') return true;
      if (h.status && h.status !== 'up') return false;
      if (!(h.providers || []).length) return false;
      return true;
    },
    // --- Debug panel for a single host (admin-only) ---
    // Lazily fetches /api/hosts/debug and caches per host.id. Toggling
    // the panel open triggers the fetch on first use; later opens reuse
    // the cached snapshot (explicit Refresh clears it).
    async toggleHostDebug(hostId) {
      if (!hostId) return;
      const open = !this.hostsDebugOpen[hostId];
      this.hostsDebugOpen = { ...this.hostsDebugOpen, [hostId]: open };
      if (open && !this.hostsDebug[hostId] && !this.hostsDebugLoading[hostId]) {
        await this.loadHostDebug(hostId);
      }
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
      // Already-expanded rows can always be collapsed, even if they
      // flipped from "up" to "down" since the last open — otherwise
      // the operator would be stuck looking at stale detail cards.
      const already = this.hostsExpanded.includes(name);
      if (!already && !this.isHostExpandable(host)) {
        return;  // dead / unmatched host — header click is a no-op
      }
      const i = this.hostsExpanded.indexOf(name);
      if (i === -1) {
        this.hostsExpanded.push(name);
        // Load history when a row expands for the first time. Further
        // expand/collapse cycles use the cached data until the range
        // changes.
        if (host && host.beszel_id && !this.hostHistory[host.beszel_id]) {
          this.loadHostHistory(host.beszel_id, host.id);
        }
      } else {
        this.hostsExpanded.splice(i, 1);
      }
    },
    async loadHostHistory(systemId, hostId) {
      // Preserve whatever series we already have so the chart doesn't
      // flicker back to "Collecting data…" between range-picker
      // clicks. Only the ``loading`` flag flips; the visible line
      // stays put until fresh data lands, then swaps in place.
      const prev = this.hostHistory[systemId] || {};
      this.hostHistory[systemId] = {
        loading: true,
        error: prev.error || '',
        series: Array.isArray(prev.series) ? prev.series : [],
      };
      // Fall back to looking up the curated hosts_config id from the
      // live hosts list when the caller didn't pass one — keeps legacy
      // call sites working without rewriting every invocation. The
      // server uses host_id as the key to layer in NE-sampled rx/tx
      // rates (host_net_samples) when Beszel's nr/ns are all zero.
      if (!hostId) {
        const host = (this.hosts || []).find(h => h.beszel_id === systemId);
        hostId = host ? host.id : '';
      }
      try {
        const qs = {
          system_id: systemId,
          hours: String(this.hostHistoryRange),
        };
        if (hostId) qs.host_id = hostId;
        const params = new URLSearchParams(qs);
        const r = await fetch('/api/hosts/history?' + params.toString());
        if (!r.ok) {
          this.hostHistory[systemId] = {
            loading: false,
            error: `HTTP ${r.status}`,
            series: prev.series || [],  // keep previous on HTTP error
          };
          return;
        }
        const d = await r.json();
        const next = Array.isArray(d.series) ? d.series : [];
        this.hostHistory[systemId] = {
          loading: false,
          error: d.error || '',
          // Only overwrite on a non-empty response. A transient empty
          // reply (hub rebooting, rate-limit) shouldn't blank a chart
          // that was already populated.
          series: next.length ? next : (prev.series || []),
        };
      } catch (e) {
        this.hostHistory[systemId] = {
          loading: false,
          error: e.message,
          series: prev.series || [],
        };
      }
    },
    setHostHistoryRange(hours) {
      this.hostHistoryRange = hours;
      // Reload every currently-expanded host's history under the new window.
      for (const name of this.hostsExpanded) {
        const host = (this.hosts || []).find(h => h.host === name);
        if (host && host.beszel_id) this.loadHostHistory(host.beszel_id, host.id);
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
      const cur = Number(pts[pts.length - 1]) || 0;
      return {
        points,
        area,
        ticks,
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
    // Segmented-bar helpers — width and offset of a single mount's
    // USED portion, expressed as percent of the host's total pool
    // capacity (h.disk_total). A 939 GB pool with a 15 GB / 75 MB
    // split between ``/`` and ``/boot/firmware`` renders a ~1.6%
    // emerald stripe + a ~0.008% blue stripe on an otherwise-grey
    // bar — visually matching img_1 / Beszel's Total Usage.
    //
    // Mount sizes come from extract_stats' GiB floats (.du / .d),
    // so we multiply by 1024**3 to get bytes before dividing by
    // h.disk_total (bytes). Defensive defaults keep NaN out of the
    // style string when a mount lacks numbers.
    mountSegmentWidth(h, idx) {
      if (!h || !h.disk_total || !(h.mounts || [])[idx]) return 0;
      const m = h.mounts[idx];
      const used = (Number(m.du) || 0) * 1024 ** 3;
      return Math.min(100, Math.max(0, (used / h.disk_total) * 100));
    },
    mountSegmentOffset(h, idx) {
      let offset = 0;
      for (let i = 0; i < idx; i++) {
        offset += this.mountSegmentWidth(h, i);
      }
      return offset;
    },
    // Green → amber → red by threshold, matching how nodeStats renders.
    pctColor(pct) {
      if (pct >= 85) return 'var(--danger)';
      if (pct >= 60) return 'var(--warning)';
      return 'var(--success)';
    },
    statusDotColor(status) {
      if (status === 'up') return 'var(--success)';
      if (status === 'down') return 'var(--danger)';
      if (status === 'paused') return 'var(--warning)';
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
    formatTime(ts) { return new Date(ts * 1000).toLocaleString(); },
    formatTimeShort(ts) { return new Date(ts * 1000).toLocaleTimeString(undefined, { hour12: false }); },

    // Consistent dd/mm/yyyy hh:mm:ss AM/PM regardless of browser locale.
    // Used in admin tables so session/login timestamps match across users.
    fmtDate(ts) {
      if (!ts) return '—';
      const d = new Date(ts * 1000);
      if (isNaN(d.getTime())) return '—';
      const pad = n => String(n).padStart(2, '0');
      let h = d.getHours();
      const ap = h >= 12 ? 'PM' : 'AM';
      h = h % 12 || 12;
      return pad(d.getDate()) + '/' + pad(d.getMonth() + 1) + '/' + d.getFullYear()
           + ' ' + pad(h) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()) + ' ' + ap;
    },
    copy(text) {
      navigator.clipboard?.writeText(text);
      this.showToast(this.t('toasts.copied'));
    },
    async confirmDialog({ title, html, icon = 'warning', confirmText, confirmColor }) {
      const isLight = document.documentElement.getAttribute('data-theme') === 'light';
      const warn = this._cssVar('--warning') || '#f59e0b';
      const r = await Swal.fire({
        title, html, icon,
        showCancelButton: true,
        confirmButtonText: confirmText || this.t('actions.confirm'),
        cancelButtonText: this.t('actions.cancel'),
        reverseButtons: true,
        focusCancel: true,
        background: this._cssVar('--surface'),
        color: this._cssVar('--text'),
        confirmButtonColor: confirmColor || warn,
        cancelButtonColor: isLight ? '#9ca3af' : '#374151',
      });
      return r.isConfirmed;
    },
    showToast(msg, type='success') {
      this.toast = msg;
      this.toastType = type;
      clearTimeout(this._tt);
      this._tt = setTimeout(() => this.toast = '', 4000);
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
