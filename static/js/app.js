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
    items: [], stacks: [], nodes: {},
    history: [], ignores: [],
    stats: {}, _statsTimer: null, _maxSize: 1,
    sparks: {}, _sparksTimer: null,
    version: '',
    historyFilters: { q: '', stack: '', op_type: '', status: '', actor: '', fromDate: '', toDate: '' },
    statsInterval: (() => {
      const v = parseInt(localStorage.getItem('statsInterval'), 10);
      return [0, 5, 15, 30, 60].includes(v) ? v : 15;
    })(),
    activeOps: [],
    view: (['stacks','services','nodes','history','settings','admin'].includes(localStorage.getItem('view')) ? localStorage.getItem('view') : 'stacks'),
    search: '', statusFilter: '', healthFilter: '',
    sortField: 'name', sortDir: 'asc',
    selected: [],
    expanded: (() => { try { return JSON.parse(localStorage.getItem('expanded') || '[]'); } catch (e) { return []; } })(),
    loading: false,
    drawerItem: null,
    showHotkeys: false,
    opsExpanded: true,
    toast: '', toastType: 'success', _tt: null,
    autoRefresh: 0, _autoTimer: null, _opsTimer: null,
    cacheLabel: '',
    settings: { apprise_url: '', apprise_tag: '', portainer_public_url: '' },
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
      admin_group: 'portaupdate-admins',
    },
    oidcTestResult: null,

    // Portainer connection — same DB-backed / UI-managed pattern as OIDC.
    // API key is write-only; blank on save means "keep current".
    portainerStatus: null,
    portainerForm: {
      url: '', endpoint_id: 1, verify_tls: true, api_key: '',
    },
    portainerTestResult: null,
    // Settings / Admin sidebar layout. Arrays drive the nav — adding a
    // section is one entry here + one <section> in the markup.
    // Section `label` is kept as a fallback (in case the translation key
    // is missing); the sidebar button actually renders via t('settings.sections.<id>').
    settingsSections: [
      { id: 'profile',       label: 'Profile' },
      { id: 'notifications', label: 'Notifications' },
      { id: 'portainer',     label: 'Portainer' },
      { id: 'ignores',       label: 'Ignore list' },
      { id: 'oidc',          label: 'Authentik OIDC' },
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
      { id: 'users',    label: 'Users' },
      { id: 'sessions', label: 'Sessions' },
      { id: 'tokens',   label: 'API tokens' },
      { id: 'backups',  label: 'Backups' },
    ],
    backups: [],
    backupBusy: false,
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
      this.$watch('view', v => {
        localStorage.setItem('view', v);
        // Load admin data lazily — only when the user actually navigates there.
        if (v === 'admin') this.openAdminTab(this.adminTab);
      });
      this.$watch('settingsSection', v => localStorage.setItem('settingsSection', v));
      this.$watch('expanded', v => localStorage.setItem('expanded', JSON.stringify(v)));
      // Re-surface the translated labels in Settings/Admin sidebars when
      // the user swaps language. Alpine re-renders bindings automatically
      // because `lang` is part of the component state; this watcher just
      // lets us react to the change (e.g. refresh the document title).
      this.$watch('lang', () => {
        document.title = this.t('app.name');
      });
      window.addEventListener('keydown', (e) => this.handleHotkey(e));
      await this.loadSettings();
      await this.loadIgnores();
      await this.refresh();
      await this.loadHistory();
      this.loadVersion();
      this.pollOps();
      this.pollStats();
      this.pollSparks();
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

    async openAdminTab(tab) {
      this.adminTab = tab;
      if (tab === 'users') await this.loadUsers();
      else if (tab === 'sessions') await this.loadSessions();
      else if (tab === 'tokens') await this.loadTokens();
      else if (tab === 'backups') await this.loadBackups();
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
          this.showToast(this.t('toasts.backup_created'));
          await this.loadBackups();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.backup_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
      finally { this.backupBusy = false; }
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

    async loadVersion() {
      try {
        const r = await fetch('/api/version');
        if (!r.ok) return;
        const d = await r.json();
        this.version = d.version || '';
      } catch (e) {}
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
        };
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
            admin_group:   this.oidcStatus.admin_group || 'portaupdate-admins',
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
      const tick = async () => {
        try {
          const r = await fetch('/api/ops');
          const all = (await r.json()).ops || [];
          const prevRunning = this.activeOps.map(o => o.id);
          this.activeOps = all.filter(o => o.status === 'running');
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
      //   - a bare canonical slug (resolved to img/icons/<slug>.svg), or
      //   - a full URL or relative path ending in .svg/.png/.webp (used verbatim).
      if (!name) return '';
      // Exact / whole-name overrides (checked first).
      const overrides = {
        'seerr': 'jellyseerr',
        'docker-prune': 'docker',
        'standalone': 'docker',
        'portaupdate': 'docker',
        'nebula-sync': 'pi-hole',
        'adguardhome-sync': 'adguard-home',
        'adguard-exporter': 'adguard-home',
        'blackbox-exporter': 'prometheus',
        'fing-agent': 'img/icons/fing.svg',
        'fing': 'img/icons/fing.svg',
        'lubelogger': 'img/icons/lubelogger.png',
        'myspeed': 'img/icons/myspeed.svg',
        'squid-proxy': 'img/icons/squid.png',
        'squid': 'img/icons/squid.png',
        'tracearr': 'img/icons/tracearr.png',
        'portainer': 'img/icons/portainer.png',
        'portainer-agent': 'img/icons/portainer.png',
      };
      // Prefix patterns — one entry covers all siblings of a product
      // (authentik outposts: ak-outpost-authentik-ldap-outpost,
      //  ak-outpost-authentik-radius-outpost, ak-outpost-authentik-proxy-outpost, ...).
      // Checked after exact overrides, before the selfhst slug fallback.
      const prefixes = [
        ['ak-outpost-', 'authentik'],
        ['komodo-',     'komodo'],
      ];
      const raw = String(name).toLowerCase().trim();
      const natural = raw.replace(/[\s_]+/g, '-').replace(/[^a-z0-9-]/g, '');
      const mapped = overrides[raw] || overrides[natural];
      // If the override looks like a URL or path (contains a separator or
      // extension), return it verbatim. Otherwise treat it as a slug.
      if (mapped && /[/.]/.test(mapped)) return mapped;
      if (mapped) return `img/icons/${mapped}.svg`;
      for (const [prefix, slug] of prefixes) {
        if (natural.startsWith(prefix)) return `img/icons/${slug}.svg`;
      }
      if (!natural) return '';
      return `img/icons/${natural}.svg`;
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
