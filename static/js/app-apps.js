// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ContinueStatementJS,BreakStatementJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML,JSAsyncFunctionMissingAwait
// noinspection JSMissingAwait,JSUnfilteredForInLoop,IfStatementWithoutBlockJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSPotentiallyInvalidUsageOfThis,JSIgnoredPromiseFromCall
/* global Alpine, Swal, I18N, t */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Apps tab + Admin → Apps tab — top-level Apps card grid (cross-host
// aggregate via /api/apps) + catalog template CRUD (Admin → Apps tab,
// reads /api/services/catalog). Per-host chip instances continue to be
// edited via Admin → Hosts (unchanged); this module handles the
// catalog-template lifecycle + the cross-host viewing experience.

export default {
  // ----------------------------------------------------------------
  // Top-level Apps view — cross-host aggregate.
  // ----------------------------------------------------------------
  async loadAppsList(force = false) {
    // When `force=true` the caller wants a fresh fetch even if a poll
    // is already in-flight (e.g. operator clicks Refresh after editing
    // the catalog). Otherwise the in-flight check coalesces overlapping
    // calls so a fast SSE-driven re-poll doesn't stack dozens of GETs.
    if (this.appsListLoading && !force) {
      return;
    }
    this.appsListLoading = true;
    this.appsListError = '';
    try {
      const r = await fetch('/api/apps');
      if (!r.ok) {
        if (r.status === 401) {
          return;
        }
        throw new Error('HTTP ' + r.status);
      }
      const j = await r.json();
      const incoming = Array.isArray(j.apps) ? j.apps : [];
      // In-place reconcile (per the every-polled-reactive-array rule):
      // mutate existing rows when group_id matches, splice gone ones,
      // push new ones. Apps view binds to chart sparklines + status
      // pulse animations that we don't want to tear down on every poll.
      const byGid = {};
      for (const row of this.appsList) {
        byGid[row.group_id] = row;
      }
      const seenGids = new Set();
      for (const incomingApp of incoming) {
        const gid = incomingApp.group_id;
        if (!gid) {
          continue;
        }
        seenGids.add(gid);
        const existing = byGid[gid];
        if (existing) {
          // Field-by-field overwrite.
          for (const k of Object.keys(incomingApp)) {
            existing[k] = incomingApp[k];
          }
        } else {
          this.appsList.push(incomingApp);
        }
      }
      // Splice gone apps.
      for (let i = this.appsList.length - 1; i >= 0; i--) {
        if (!seenGids.has(this.appsList[i].group_id)) {
          this.appsList.splice(i, 1);
        }
      }
      // Re-sort by name (incoming is already sorted, but in-place
      // reconcile doesn't preserve order — sort after).
      this.appsList.sort((a, b) => (a.name || '').toLowerCase()
        .localeCompare((b.name || '').toLowerCase()));
      this.appsListLoaded = true;
    } catch (err) {
      this.appsListError = err && err.message ? err.message : String(err);
      // Don't wholesale-replace on error — keep existing rows.
    } finally {
      this.appsListLoading = false;
    }
  },

  // Filtered apps list — search + status filter applied client-side.
  filteredApps() {
    const q = (this.appsSearchQuery || '').trim().toLowerCase();
    const sf = this.appsStatusFilter || '';
    let out = this.appsList || [];
    if (sf) {
      out = out.filter(a => a.status === sf);
    }
    if (q) {
      out = out.filter(a => {
        if ((a.name || '').toLowerCase().includes(q)) {
          return true;
        }
        const cat = a.catalog || {};
        if ((cat.slug || '').toLowerCase().includes(q)) {
          return true;
        }
        if ((cat.description || '').toLowerCase().includes(q)) {
          return true;
        }
        for (const inst of (a.instances || [])) {
          if ((inst.host_label || '').toLowerCase().includes(q)) {
            return true;
          }
          if ((inst.url || '').toLowerCase().includes(q)) {
            return true;
          }
        }
        return false;
      });
    }
    return out;
  },

  // Aggregate counters for the view header.
  appsCounts() {
    const list = this.appsList || [];
    let up = 0, down = 0, degraded = 0, unknown = 0;
    for (const a of list) {
      if (a.status === 'up') {
        up++;
      } else if (a.status === 'down') {
        down++;
      } else if (a.status === 'degraded') {
        degraded++;
      } else {
        unknown++;
      }
    }
    return {total: list.length, up, down, degraded, unknown};
  },

  // Per-card chip class for the status pill.
  appStatusPillClass(status) {
    if (status === 'up') {
      return 'pill-ok';
    }
    if (status === 'down') {
      return 'pill-error';
    }
    if (status === 'degraded') {
      return 'pill-warning';
    }
    return 'pill-muted';
  },

  // Diagnosis reason for a non-up Apps instance — answers "why is this
  // degraded / down?" at a glance. Prefers a specific failing port's
  // error (multi-port chips), then the chip-level rollup error, then a
  // generic fallback. Probe error strings come straight from the
  // sampler (timeout / ConnectionRefusedError / unexpected status 404 /
  // …) and stay un-translated — they're diagnostic, not UI chrome.
  // Returns '' for up instances so the template can gate on it.
  appsInstanceReason(inst) {
    if (!inst || inst.status === 'up') {
      return '';
    }
    if (inst.status === 'unknown') {
      return this.t('apps.reason_no_probe') || 'No probe result yet';
    }
    const pr = (inst.port_results || []).find((p) => p && !p.alive && p.error);
    if (pr) {
      return this.t('apps.reason_port', {port: pr.port, error: pr.error})
        || ('Port ' + pr.port + ': ' + pr.error);
    }
    const lp = inst.last_probe;
    if (lp && lp.error) {
      return lp.error;
    }
    return this.t('apps.reason_unreachable') || 'Probe failed (no detail)';
  },

  // Latency unit — single i18n point for the "Nms" suffix used across every
  // Apps surface (bare form). Parenthesised form via appsLatencyParen().
  // Keeps the unit out of template-literal concatenation so RTL / non-Latin
  // locales can place / translate it (ms vs мс vs ミリ秒).
  appsLatencyMs(n) {
    // Group thousands per the browser locale ("1,234" / "1.234") and let
    // the i18n format own the space + unit ("{n} ms").
    const num = (typeof n === 'number') ? n.toLocaleString() : n;
    return this.t('common.latency_ms', {n: num}) || (num + ' ms');
  },
  appsLatencyParen(n) {
    const num = (typeof n === 'number') ? n.toLocaleString() : n;
    return this.t('common.latency_paren', {n: num}) || ('(' + num + ' ms)');
  },

  // Host-drawer chip-strip tooltip: "<name> — <status> (<rtt>ms)". The
  // name+status base (incl. its separator) lives in the i18n format string
  // so locales control the separator; rtt is appended via the latency key.
  appsChipTitle(app) {
    const name = (app && (app.name || (app.catalog && app.catalog.name))) || '';
    const status = this.t('apps.status_' + (app && app.status)) || (app && app.status) || '';
    let s = this.t('apps.chip_tooltip', {name, status}) || (name + ' — ' + status);
    const rtt = app && app.last_probe && app.last_probe.rtt_ms;
    if (rtt != null) {
      s += ' ' + this.appsLatencyParen(rtt);
    }
    return s;
  },

  // Chip-strip accessible name — name + status only (the latency is noise in
  // a screen-reader announcement).
  appsChipAriaLabel(app) {
    const name = (app && (app.name || (app.catalog && app.catalog.name))) || '';
    const status = this.t('apps.status_' + (app && app.status)) || (app && app.status) || '';
    return this.t('apps.chip_tooltip', {name, status}) || (name + ' — ' + status);
  },

  // Apps instance host-row tooltip: canonical address, plus the operator
  // label when it differs. Separator lives in the i18n format string.
  appsInstanceHostTitle(inst) {
    const base = (inst && (inst.host_address || inst.host_id)) || '';
    const label = (inst && inst.host_label) || '';
    if (label && label !== base) {
      return this.t('apps.host_tooltip', {host: base, label}) || (base + ' — ' + label);
    }
    return base;
  },

  // Per-port pill tooltip: "<status> (<rtt>ms) — <error>" (rtt + error both
  // optional). status + the error separator route through i18n; the error
  // text itself is the un-translated probe diagnostic from the sampler.
  appsPortTitle(pr) {
    if (!pr) {
      return '';
    }
    let s = pr.alive ? (this.t('apps.status_up') || 'up') : (this.t('apps.status_down') || 'down');
    if (pr.rtt_ms != null) {
      s += ' ' + this.appsLatencyParen(pr.rtt_ms);
    }
    if (pr.error) {
      s += this.t('apps.error_suffix', {error: pr.error}) || (' — ' + pr.error);
    }
    return s;
  },

  // ----------------------------------------------------------------
  // Apps detail / debug drawer — mirrors the host drawer. Clicking an
  // app card opens drawerApp; each instance has a "Show debug" panel
  // that lazy-fetches /api/services/{host}/{idx}/debug (the resolved
  // probe target, chip + catalog config, latest per-port outcomes) so
  // the operator can see WHY an instance on a host isn't reporting.
  // ----------------------------------------------------------------
  drawerApp: null,
  appDebug: {},      // { "<host_id>:<idx>": { loading, error, data } }
  appDebugOpen: {},  // { "<host_id>:<idx>": bool }

  appInstanceKey(inst) {
    return (inst && (inst.host_id + ':' + inst.service_idx)) || '';
  },

  openAppDrawer(app) {
    if (!app) {
      return;
    }
    this.drawerApp = app;
    this.appDebugOpen = {};
  },

  closeAppDrawer() {
    this.drawerApp = null;
    this.appDebugOpen = {};
  },

  // Re-point an open App drawer to the matching refreshed group (by
  // group_id) after an apps reload, so edits made elsewhere (the
  // instance editor) reflect in the open drawer. No-op when the drawer
  // is closed or the group is gone (e.g. the chip was deleted).
  _resyncDrawerApp() {
    if (!this.drawerApp) {
      return;
    }
    const gid = this.drawerApp.group_id;
    const match = (this.appsList || []).find((g) => g && g.group_id === gid);
    if (match) {
      this.drawerApp = match;
    }
  },

  // True when the app drawer should treat itself as open (used by the
  // scroll-lock effect + ESC handler).
  isAppDrawerOpen() {
    return !!this.drawerApp;
  },

  // Toggle the per-instance debug panel; lazy-fetch on first open.
  toggleAppInstanceDebug(inst) {
    const key = this.appInstanceKey(inst);
    if (!key) {
      return;
    }
    const open = !this.appDebugOpen[key];
    this.appDebugOpen = Object.assign({}, this.appDebugOpen, {[key]: open});
    if (open && !this.appDebug[key]) {
      this.loadAppInstanceDebug(inst);
    }
  },

  async loadAppInstanceDebug(inst) {
    const key = this.appInstanceKey(inst);
    if (!key || !inst) {
      return;
    }
    this.appDebug = Object.assign({}, this.appDebug, {[key]: {loading: true, error: '', data: null}});
    try {
      const r = await fetch('/api/services/' + encodeURIComponent(inst.host_id)
        + '/' + encodeURIComponent(inst.service_idx) + '/debug');
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      const data = await r.json();
      this.appDebug = Object.assign({}, this.appDebug, {[key]: {loading: false, error: '', data}});
    } catch (err) {
      this.appDebug = Object.assign({}, this.appDebug, {
        [key]: {loading: false, error: (err && err.message) ? err.message : String(err), data: null},
      });
    }
  },

  // Re-probe one instance from the drawer (reuses the per-chip
  // probe-now endpoint), then refresh its debug data + the apps list.
  async appDrawerProbeNow(inst) {
    const key = this.appInstanceKey(inst);
    if (!key || !inst) {
      return;
    }
    try {
      const r = await fetch('/api/services/' + encodeURIComponent(inst.host_id)
        + '/' + encodeURIComponent(inst.service_idx) + '/probe', {method: 'POST'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        this.showToast((this.t('apps.drawer.probe_failed') || 'Probe failed: ') + (j.detail || ('HTTP ' + r.status)), 'error');
      }
    } catch (err) {
      this.showToast((this.t('apps.drawer.probe_failed') || 'Probe failed: ') + ((err && err.message) || err), 'error');
    }
    if (this.appDebugOpen[key]) {
      this.loadAppInstanceDebug(inst);
    }
    if (typeof this.loadAppsList === 'function') {
      this.loadAppsList(true);
    }
  },

  // Admin Edit affordance from the App detail drawer — redirect to the
  // Apps instance editor (Admin → Apps → Instances) and open the edit
  // modal for THIS chip. The drawer's per-instance object (from
  // list_apps) lacks the per-chip name/icon/catalog fields the editor
  // needs, so we navigate, (re)load the richer appsInstances list, find
  // the matching row by (host_id, service_idx), and open its editor.
  async editAppInstanceFromDrawer(inst) {
    if (!inst || !inst.host_id) {
      return;
    }
    const hostId = inst.host_id;
    const idx = inst.service_idx;
    this.closeAppDrawer();
    this.view = 'admin';
    this.adminTab = 'apps';
    if (typeof this.setAppsAdminTab === 'function') {
      this.setAppsAdminTab('instances');
    }
    if (typeof this.loadAppsInstances === 'function') {
      await this.loadAppsInstances();
    }
    const match = (this.appsInstances || []).find(
      (x) => x && x.host_id === hostId && x.service_idx === idx);
    if (match) {
      this.$nextTick(() => this.openInstanceEdit(match));
    }
  },

  // Open a specific instance row in the host drawer / Admin → Hosts editor.
  goToAdminHostsForInstance(inst) {
    if (!inst || !inst.host_id) {
      return;
    }
    this.view = 'admin';
    this.adminTab = 'hosts';
    this.hostsFilter = inst.host_id;
    if (this.$nextTick) {
      this.$nextTick(() => {
        // Best-effort: scroll to the host row matching the id.
        const row = document.querySelector(`[data-host-row-id="${inst.host_id}"]`);
        if (row && row.scrollIntoView) {
          row.scrollIntoView({behavior: 'smooth', block: 'center'});
        }
      });
    }
  },

  // ----------------------------------------------------------------
  // Apps → Instances grouping. With many pinned chips the flat table
  // gets noisy, so the operator can group by host or by service
  // (catalog / name). Rendered as a flattened row list (group-header
  // rows + item rows) so the <table> stays one tbody; headers are
  // collapsible. Mode persists to localStorage.
  // ----------------------------------------------------------------
  appsInstancesGroupBy: (() => {
    try {
      const v = localStorage.getItem('appsInstancesGroupBy');
      return ['none', 'host', 'service'].includes(v) ? v : 'host';
    } catch (_) {
      return 'host';
    }
  })(),
  appsInstancesCollapsed: {},

  setAppsInstancesGroupBy(mode) {
    if (!['none', 'host', 'service'].includes(mode)) {
      return;
    }
    this.appsInstancesGroupBy = mode;
    try {
      localStorage.setItem('appsInstancesGroupBy', mode);
    } catch (_) { /* private mode — in-memory only */ }
  },

  toggleAppsInstanceGroup(key) {
    this.appsInstancesCollapsed = Object.assign({}, this.appsInstancesCollapsed,
      {[key]: !this.appsInstancesCollapsed[key]});
  },

  // Grouped render structure for the instances table — one entry per
  // group: {key, label, count, items}. Rendered as one <tbody> per
  // group (valid HTML, keeps column alignment with the shared thead)
  // with a collapsible header row. 'none' returns a single group with
  // an empty key/label so the markup suppresses its header row.
  appsInstancesGroups() {
    const list = Array.isArray(this.appsInstances) ? this.appsInstances : [];
    const mode = this.appsInstancesGroupBy || 'host';
    if (mode === 'none') {
      return [{key: '__all__', label: '', count: list.length, items: list}];
    }
    const groups = {};
    const order = [];
    for (const inst of list) {
      let key;
      let label;
      if (mode === 'service') {
        label = inst.catalog_name || inst.name || (this.t('admin_apps.unlinked') || '— unlinked —');
        key = 'svc:' + label.toLowerCase();
      } else {
        key = 'host:' + (inst.host_id || '');
        label = (typeof this.appsInstanceHostTitle === 'function' ? this.appsInstanceHostTitle(inst) : '')
          || inst.host_address || inst.host_id;
      }
      if (!groups[key]) {
        groups[key] = {key, label, items: []};
        order.push(key);
      }
      groups[key].items.push(inst);
    }
    order.sort((a, b) => (groups[a].label || '').toLowerCase().localeCompare((groups[b].label || '').toLowerCase()));
    return order.map((key) => ({
      key, label: groups[key].label, count: groups[key].items.length, items: groups[key].items,
    }));
  },

  // ----------------------------------------------------------------
  // Admin → Apps — catalog template CRUD.
  // ----------------------------------------------------------------
  async loadAppsCatalog() {
    try {
      const r = await fetch('/api/services/catalog');
      if (!r.ok) {
        if (r.status === 401 || r.status === 403) {
          return;
        }
        throw new Error('HTTP ' + r.status);
      }
      const j = await r.json();
      this.appsCatalog = Array.isArray(j.entries) ? j.entries : [];
      this.appsCatalogLoaded = true;
    } catch (err) {
      this.appsCatalogStatus = err && err.message ? err.message : String(err);
    }
  },

  async loadAppsInstances() {
    try {
      const r = await fetch('/api/apps/instances');
      if (!r.ok) {
        if (r.status === 401 || r.status === 403) {
          return;
        }
        throw new Error('HTTP ' + r.status);
      }
      const j = await r.json();
      this.appsInstances = Array.isArray(j.instances) ? j.instances : [];
      this.appsInstancesLoaded = true;
    } catch {
      this.appsInstances = [];
      this.appsInstancesLoaded = true;
    }
  },

  openAppCatalogNew() {
    this.appsCatalogEdit = {
      id: null,
      name: '',
      slug: '',
      icon: '',
      description: '',
      default_ports: [],
    };
    this.appsCatalogEditError = '';
    this.appsCatalogEditOpen = true;
    // Templates tab is the only place the editor lives. Force the
    // tab strip back to Templates when opening so the editor is
    // visible even if the operator is currently on Instances.
    this.appsAdminTab = 'templates';
    this._scrollAppsEditorIntoView();
  },

  openAppCatalogEdit(entry) {
    if (!entry) {
      return;
    }
    this.appsCatalogEdit = {
      id: entry.id,
      name: entry.name || '',
      slug: entry.slug || '',
      icon: entry.icon || '',
      description: entry.description || '',
      // Deep-clone the port list so editor edits don't mutate the
      // table's reactive row before Save.
      default_ports: JSON.parse(JSON.stringify(entry.default_ports || [])),
    };
    this.appsCatalogEditError = '';
    this.appsCatalogEditOpen = true;
    this.appsAdminTab = 'templates';
    this._scrollAppsEditorIntoView();
  },

  // Smooth-scroll the editor anchor into view after Alpine renders the
  // x-show'd block. Double `$nextTick` covers the case where the
  // editor's parent (the Templates tab) was hidden when the click
  // landed; first tick reveals the tab, second tick measures the
  // newly-laid-out editor. Best-effort — silent no-op if the anchor
  // isn't in the DOM (template not loaded, ancestor display:none, etc.).
  _scrollAppsEditorIntoView() {
    if (!this.$nextTick) {
      return;
    }
    this.$nextTick(() => {
      this.$nextTick(() => {
        const anchor = document.querySelector('[data-apps-editor-anchor]');
        if (anchor && anchor.scrollIntoView) {
          anchor.scrollIntoView({behavior: 'smooth', block: 'start'});
        }
        // Move keyboard focus to the first text input inside the
        // editor so the operator can start typing immediately.
        const firstInput = anchor && anchor.querySelector('input[type="text"]');
        if (firstInput && firstInput.focus) {
          firstInput.focus({preventScroll: true});
        }
      });
    });
  },

  // Tab strip click handler — close the editor when leaving Templates
  // so the form doesn't linger on the Instances tab (where it would
  // sit awkwardly below the instance table with no visible header).
  setAppsAdminTab(tab) {
    if (this.appsAdminTab === tab) {
      return;
    }
    if (tab !== 'templates' && this.appsCatalogEditOpen) {
      this.closeAppCatalogEditor();
    }
    this.appsAdminTab = tab;
  },

  closeAppCatalogEditor() {
    this.appsCatalogEditOpen = false;
    this.appsCatalogEdit = {};
    this.appsCatalogEditError = '';
  },

  addAppCatalogPort() {
    if (!this.appsCatalogEdit.default_ports) {
      this.appsCatalogEdit.default_ports = [];
    }
    this.appsCatalogEdit.default_ports.push({
      port: 80,
      protocol: 'tcp',
      label: '',
      probe_path: '/',
      probe_status: 0,
    });
  },

  removeAppCatalogPort(idx) {
    if (!this.appsCatalogEdit.default_ports) {
      return;
    }
    if (idx < 0 || idx >= this.appsCatalogEdit.default_ports.length) {
      return;
    }
    this.appsCatalogEdit.default_ports.splice(idx, 1);
  },

  async saveAppCatalogEntry() {
    if (this.appsCatalogSaving) {
      return;
    }
    const entry = this.appsCatalogEdit || {};
    if (!entry.name || !entry.name.trim()) {
      this.appsCatalogEditError = this.t('admin_apps.error_name_required') || 'Name is required';
      return;
    }
    this.appsCatalogSaving = true;
    this.appsCatalogEditError = '';
    const body = {
      name: entry.name.trim(),
      slug: (entry.slug || '').trim(),
      icon: (entry.icon || '').trim(),
      description: (entry.description || '').trim(),
      default_ports: entry.default_ports || [],
    };
    try {
      const url = entry.id
        ? `/api/services/catalog/${entry.id}`
        : '/api/services/catalog';
      const method = entry.id ? 'PATCH' : 'POST';
      const r = await fetch(url, {
        method,
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      await this.loadAppsCatalog();
      this.closeAppCatalogEditor();
      if (typeof this.toast === 'function') {
        this.toast(this.t('admin_apps.saved') || 'Template saved', 'success');
      }
    } catch (err) {
      this.appsCatalogEditError = err && err.message ? err.message : String(err);
    } finally {
      this.appsCatalogSaving = false;
    }
  },

  async deleteAppCatalogEntry(entry) {
    if (!entry || !entry.id) {
      return;
    }
    const confirmed = typeof this.confirmDialog === 'function'
      ? await this.confirmDialog({
        title: this.t('admin_apps.delete_confirm_title') || 'Delete template?',
        text: (this.t('admin_apps.delete_confirm_text')
            || 'Per-host chips linked to this template will keep their own name + icon, just lose the catalog binding.')
          + ' (' + entry.name + ')',
        icon: 'warning',
        confirmButtonText: this.t('actions.delete') || 'Delete',
      })
      : window.confirm('Delete "' + entry.name + '"?');
    if (!confirmed) {
      return;
    }
    try {
      const r = await fetch(`/api/services/catalog/${entry.id}`, {method: 'DELETE'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      await this.loadAppsCatalog();
      if (typeof this.toast === 'function') {
        this.toast(this.t('admin_apps.deleted') || 'Template deleted', 'success');
      }
    } catch (err) {
      if (typeof this.toast === 'function') {
        this.toast((this.t('admin_apps.delete_failed') || 'Delete failed: ') + err.message, 'error');
      }
    }
  },

  // ----------------------------------------------------------------
  // Apps → Instances tab — edit / delete a pinned chip in place. Chips
  // are otherwise create-only (pin / discovery); this is the per-app
  // editor the Hosts tab deferred to Apps. Routes through the per-chip
  // PATCH / DELETE endpoints (validated persist + audit). For a
  // catalog-linked chip, clearing name / icon re-inherits from the
  // template.
  // ----------------------------------------------------------------
  appsInstanceEditOpen: false,
  appsInstanceEditSaving: false,
  appsInstanceEditError: '',
  appsInstanceEditForm: {
    host_id: '', service_idx: -1, host_label: '', catalog_name: '',
    name: '', url: '', icon: '', probe_enabled: true, probe_type: 'tcp',
    ports: [], docker_stack: '', docker_container: '',
  },

  openInstanceEdit(inst) {
    if (!inst) {
      return;
    }
    // Seed the per-port rows from the chip's probe.ports[] (inherited
    // from the catalog template at pin time, e.g. AdGuard's 3 ports),
    // normalising each entry so the inputs bind cleanly.
    const ports = (Array.isArray(inst.ports) ? inst.ports : []).map((p) => ({
      port: (p && p.port != null) ? p.port : '',
      protocol: (p && p.protocol) || 'tcp',
      label: (p && p.label) || '',
      probe_path: (p && p.probe_path) || '',
      probe_status: (p && p.probe_status != null) ? p.probe_status : 0,
    }));
    this.appsInstanceEditForm = {
      host_id: inst.host_id,
      service_idx: inst.service_idx,
      host_label: inst.host_address || inst.host_id,
      catalog_name: inst.catalog_name || '',
      name: inst.name || '',
      url: inst.url || '',
      icon: inst.icon || '',
      probe_enabled: inst.probe_enabled !== false,
      probe_type: inst.probe_type || 'tcp',
      ports: ports,
      docker_stack: inst.docker_stack || '',
      docker_container: inst.docker_container || '',
    };
    this.appsInstanceEditError = '';
    this.appsInstanceEditOpen = true;
  },

  // Datalist candidates for the "Link to Docker" picker, sourced from
  // the already-loaded /api/items snapshot (this.items). Stacks =
  // service names + stack namespaces; containers = standalone /orphan
  // container names. Free-text inputs back these so the operator can
  // also type an id the snapshot doesn't list.
  appsDockerStackOptions() {
    const out = new Set();
    for (const it of (this.items || [])) {
      if (!it) {
        continue;
      }
      if (it.type === 'service' && it.name) {
        out.add(it.name);
      }
      if (it.stack) {
        out.add(it.stack);
      }
    }
    return Array.from(out).sort();
  },
  appsDockerContainerOptions() {
    const out = new Set();
    for (const it of (this.items || [])) {
      if (it && (it.type === 'container' || it.type === 'orphan') && it.name) {
        out.add(it.name);
      }
    }
    return Array.from(out).sort();
  },

  // Resolve a Docker-linked chip to its item in the current /api/items
  // snapshot. docker_container wins (standalone container, matched by
  // name / raw_id / id); else docker_stack matches a service by name /
  // stack namespace. Returns null when nothing matches.
  _appDrawerResolveItem(inst) {
    if (!inst) {
      return null;
    }
    const items = Array.isArray(this.items) ? this.items : [];
    if (inst.docker_container) {
      const c = items.find((it) => it && (it.type === 'container' || it.type === 'orphan')
        && (it.name === inst.docker_container || it.raw_id === inst.docker_container || it.id === inst.docker_container));
      if (c) {
        return c;
      }
    }
    if (inst.docker_stack) {
      return items.find((it) => it && it.type === 'service'
        && (it.name === inst.docker_stack || it.stack === inst.docker_stack)) || null;
    }
    return null;
  },

  // App-drawer inline Restart for a Docker-linked chip — hands off to
  // the existing restartItem op (its own confirm + audit + agent-target
  // routing).
  appDrawerRestart(inst) {
    const target = this._appDrawerResolveItem(inst);
    if (!target) {
      if (typeof this.showToast === 'function') {
        this.showToast(this.t('apps.drawer.restart_no_match') || 'Linked Docker target not found in the current items list', 'error');
      }
      return;
    }
    if (typeof this.restartItem === 'function') {
      this.restartItem(target);
    }
  },

  // App-drawer inline Update for a Docker-linked chip — hands off to the
  // existing itemAction op (recreate-with-pull for containers / stack
  // update + pull for services). Its own confirm gate applies.
  appDrawerUpdate(inst) {
    const target = this._appDrawerResolveItem(inst);
    if (!target) {
      if (typeof this.showToast === 'function') {
        this.showToast(this.t('apps.drawer.restart_no_match') || 'Linked Docker target not found in the current items list', 'error');
      }
      return;
    }
    if (typeof this.itemAction === 'function') {
      this.itemAction(target);
    }
  },

  // True when a Docker-linked chip's target currently has an update —
  // gates the App drawer's Update button so it only shows when there's
  // something to update.
  appDrawerHasUpdate(inst) {
    const target = this._appDrawerResolveItem(inst);
    return !!(target && target.status === 'update');
  },

  addInstancePort() {
    if (!Array.isArray(this.appsInstanceEditForm.ports)) {
      this.appsInstanceEditForm.ports = [];
    }
    this.appsInstanceEditForm.ports.push({port: '', protocol: 'tcp', label: '', probe_path: '', probe_status: 0});
  },

  removeInstancePort(i) {
    if (Array.isArray(this.appsInstanceEditForm.ports)) {
      this.appsInstanceEditForm.ports.splice(i, 1);
    }
  },

  closeInstanceEdit() {
    this.appsInstanceEditOpen = false;
  },

  async saveInstanceEdit() {
    const f = this.appsInstanceEditForm;
    if (!f.host_id || f.service_idx < 0) {
      return;
    }
    this.appsInstanceEditSaving = true;
    this.appsInstanceEditError = '';
    try {
      const r = await fetch('/api/services/' + encodeURIComponent(f.host_id)
        + '/' + encodeURIComponent(f.service_idx), {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          name: f.name, url: f.url, icon: f.icon,
          probe_enabled: f.probe_enabled, probe_type: f.probe_type,
          ports: Array.isArray(f.ports) ? f.ports : [],
          docker_stack: f.docker_stack || '', docker_container: f.docker_container || '',
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      this.appsInstanceEditOpen = false;
      await this.loadAppsInstances();
      // Await the Apps-view refresh + re-sync an open drawer so the edit
      // (URL / ports / icon / link) reflects in the top-level Apps view
      // and the App drawer, not just the admin instances table.
      if (typeof this.loadAppsList === 'function') {
        await this.loadAppsList(true);
        this._resyncDrawerApp();
      }
    } catch (err) {
      this.appsInstanceEditError = (err && err.message) ? err.message : String(err);
    } finally {
      this.appsInstanceEditSaving = false;
    }
  },

  async deleteInstance(inst) {
    if (!inst || !inst.host_id) {
      return;
    }
    const label = inst.name || inst.catalog_name || ('service ' + inst.service_idx);
    const confirmed = typeof this.confirmDialog === 'function'
      ? await this.confirmDialog({
        title: this.t('admin_apps.instance_delete_confirm_title') || 'Remove app instance?',
        text: (this.t('admin_apps.instance_delete_confirm_text')
            || 'This unpins the chip from the host. The catalog template is unaffected.')
          + ' (' + label + ')',
        icon: 'warning',
        confirmButtonText: this.t('actions.delete') || 'Delete',
      })
      : window.confirm('Remove "' + label + '"?');
    if (!confirmed) {
      return;
    }
    try {
      const r = await fetch('/api/services/' + encodeURIComponent(inst.host_id)
        + '/' + encodeURIComponent(inst.service_idx), {method: 'DELETE'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      await this.loadAppsInstances();
      if (typeof this.loadAppsList === 'function') {
        await this.loadAppsList(true);
        this._resyncDrawerApp();
      }
      if (typeof this.toast === 'function') {
        this.toast(this.t('admin_apps.instance_deleted') || 'App instance removed', 'success');
      }
    } catch (err) {
      if (typeof this.toast === 'function') {
        this.toast((this.t('admin_apps.instance_delete_failed') || 'Remove failed: ') + err.message, 'error');
      }
    }
  },

  // ----------------------------------------------------------------
  // Host-drawer Apps surface — manual probe-now + per-port history.
  // The chip rendering itself lives in static/index.html; this section
  // owns the network calls + per-button in-flight state.
  // ----------------------------------------------------------------

  // Manual one-shot probe against a specific chip on a host. Backend
  // route: POST /api/services/{host_id}/{service_idx}/probe — runs the
  // canonical TCP / HTTP probe + persists to service_samples + returns
  // the outcome inline so the SPA can patch the row without waiting for
  // the next host poll.
  async probeAppNow(host, serviceIdx) {
    if (!host || serviceIdx == null) {
      return;
    }
    if (!this.probeNowInFlight) {
      this.probeNowInFlight = {};
    }
    const key = 'probe:' + host.id + ':' + serviceIdx;
    if (this.probeNowInFlight[key]) {
      return;
    }
    this.probeNowInFlight[key] = true;
    try {
      const r = await fetch(`/api/services/${encodeURIComponent(host.id)}/${serviceIdx}/probe`, {
        method: 'POST',
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      const j = await r.json();
      // Patch the matching app's last_probe + status in place so the
      // chip + status pill update immediately. In-place mutation
      // matches the every-polled-reactive-array discipline.
      const apps = Array.isArray(host.apps) ? host.apps : [];
      const app = apps.find(a => a.service_idx === serviceIdx);
      if (app) {
        app.last_probe = {
          alive: !!j.alive,
          rtt_ms: j.rtt_ms,
          ts: j.ts,
          error: j.error,
        };
        app.status = j.alive ? 'up' : 'down';
      }
      if (typeof this.toast === 'function') {
        const msg = j.alive
          ? (this.t('host_drawer.apps.probe_success') || 'Probe OK') + (j.rtt_ms != null ? ' (' + j.rtt_ms + 'ms)' : '')
          : (this.t('host_drawer.apps.probe_failed') || 'Probe failed') + (j.error ? ': ' + j.error : '');
        this.toast(msg, j.alive ? 'success' : 'error');
      }
    } catch (err) {
      if (typeof this.toast === 'function') {
        this.toast(
          (this.t('host_drawer.apps.probe_error') || 'Probe error: ') + (err && err.message ? err.message : err),
          'error'
        );
      }
    } finally {
      this.probeNowInFlight[key] = false;
    }
  },

  // Per-(host, service_idx) probe history for the host drawer's
  // sparkline / mini-chart. Stored in `appsHistory[host_id:service_idx]`
  // = {samples: [...], loadedAt}. Cap default 24h; range picker is a
  // future extension.
  async loadAppHistory(hostId, serviceIdx, hours) {
    if (!hostId || serviceIdx == null) {
      return;
    }
    const window = Math.max(1, Math.min(parseInt(hours, 10) || 24, 24 * 30));
    const key = hostId + ':' + serviceIdx;
    if (!this.appsHistory) {
      this.appsHistory = {};
    }
    try {
      const r = await fetch(`/api/services/${encodeURIComponent(hostId)}/${serviceIdx}/history?hours=${window}`);
      if (!r.ok) {
        return;
      }
      const j = await r.json();
      this.appsHistory[key] = {
        samples: Array.isArray(j.samples) ? j.samples : [],
        hours: window,
        loadedAt: Date.now(),
      };
    } catch {
      // Silent — sparkline shows empty state.
    }
  },

  // ----------------------------------------------------------------
  // Pin-to-host modal — operator picks a curated host from the existing
  // hostsConfig list + optional URL override + probe-enable flag; the
  // POST to /api/services/catalog/{cid}/pin creates the chip server-
  // side via the same _clean_host_services validator that Admin →
  // Hosts uses. After save the Instances tab refresh picks up the new
  // chip automatically.
  // ----------------------------------------------------------------
  openAppCatalogPin(entry) {
    if (!entry || !entry.id) {
      return;
    }
    this.appsPinForm = {
      template: entry,
      host_id: '',
      url: '',
      probe_enabled: true,
    };
    this.appsPinError = '';
    this.appsPinModalOpen = true;
    // The host picker reads from `hostsConfig`. The Admin → Apps tab
    // loader doesn't fetch it (only the Hosts tab does), so the picker
    // is empty if the operator hasn't visited Hosts in this session.
    // Lazy-load on first open.
    if (!Array.isArray(this.hostsConfig) || !this.hostsConfig.length) {
      if (typeof this.loadHostsConfig === 'function') {
        this.loadHostsConfig().catch(() => undefined);
      }
    }
  },

  closeAppCatalogPin() {
    this.appsPinModalOpen = false;
    this.appsPinForm = {template: null, host_id: '', url: '', probe_enabled: true};
    this.appsPinError = '';
  },

  async submitAppCatalogPin() {
    if (this.appsPinSaving) {
      return;
    }
    const form = this.appsPinForm || {};
    const tpl = form.template;
    if (!tpl || !tpl.id) {
      this.appsPinError = this.t('admin_apps.pin_no_template') || 'No template selected';
      return;
    }
    if (!form.host_id) {
      this.appsPinError = this.t('admin_apps.pin_pick_host') || 'Pick a host';
      return;
    }
    this.appsPinSaving = true;
    this.appsPinError = '';
    try {
      const r = await fetch(`/api/services/catalog/${tpl.id}/pin`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          host_id: form.host_id,
          url: (form.url || '').trim(),
          probe_enabled: !!form.probe_enabled,
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      const j = await r.json();
      // Refresh the instance list so the new chip appears.
      await this.loadAppsInstances();
      // Toast confirmation with a quick path back to the host editor.
      if (typeof this.toast === 'function') {
        this.toast(
          (this.t('admin_apps.pin_success') || 'Pinned to host: ') + (j.host_id || ''),
          'success'
        );
      }
      this.closeAppCatalogPin();
    } catch (err) {
      this.appsPinError = err && err.message ? err.message : String(err);
    } finally {
      this.appsPinSaving = false;
    }
  },

  // ----------------------------------------------------------------
  // Discovery wizard — port-scan + catalog match → bulk-bind.
  // ----------------------------------------------------------------
  // Jump from the top-level Apps view's empty-state CTA into the
  // discovery wizard. The wizard markup lives in the Admin → Apps
  // partial (hidden via x-show on the admin page-content), so we have
  // to navigate to that view + tab first, then open the modal on the
  // next tick once the partial is visible.
  openDiscoveryFromAppsView() {
    this.view = 'admin';
    this.adminTab = 'apps';
    if (typeof this.setAppsAdminTab === 'function') {
      this.setAppsAdminTab('templates');
    }
    this.$nextTick(() => this.openAppsDiscoverWizard());
  },

  openAppsDiscoverWizard() {
    this.appsDiscoverOpen = true;
    this.appsDiscoverForm = {host_id: ''};
    this.appsDiscoverResult = {detected_ports: [], proposals: [], scanned_at: 0};
    this.appsDiscoverError = '';
    this.appsDiscoverApplyError = '';
    this.appsDiscoverSelected = new Set();
    this.appsDiscoverHostSearch = '';
    this.appsDiscoverHostDropdownOpen = false;
    this.appsDiscoverHostActiveIdx = -1;
    // Host picker reads from hostsConfig — lazy-load if the operator
    // hasn't visited Admin → Hosts in this session.
    if (!Array.isArray(this.hostsConfig) || !this.hostsConfig.length) {
      if (typeof this.loadHostsConfig === 'function') {
        this.loadHostsConfig().catch(() => undefined);
      }
    }
  },

  closeAppsDiscoverWizard() {
    this.appsDiscoverOpen = false;
    this.appsDiscoverForm = {host_id: ''};
    this.appsDiscoverResult = {detected_ports: [], proposals: [], scanned_at: 0};
    this.appsDiscoverError = '';
    this.appsDiscoverApplyError = '';
    this.appsDiscoverSelected = new Set();
    this.appsDiscoverHostSearch = '';
    this.appsDiscoverHostDropdownOpen = false;
    this.appsDiscoverHostActiveIdx = -1;
  },

  // Display label for a curated host row in the discovery host picker —
  // canonical hostname/IP first, then the operator label when it differs.
  // Mirrors the old <option> text so the searchable input reads identically.
  appsHostLabel(h) {
    if (!h) {
      return '';
    }
    const base = (h.address || h.id || '').trim();
    const label = (h.label || '').trim();
    return base + (label && label !== base ? ' — ' + label : '');
  },

  // Filtered + capped host list for the searchable picker. Matches the
  // query (case-insensitive substring) against label + id + address so
  // the operator can type any identifier they remember. Empty query
  // returns the whole list (capped) so focusing the field shows options.
  appsDiscoverFilteredHosts() {
    const all = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
    const q = (this.appsDiscoverHostSearch || '').trim().toLowerCase();
    let out = all;
    if (q) {
      out = all.filter((h) => {
        if (!h) {
          return false;
        }
        const hay = ((h.label || '') + ' ' + (h.id || '') + ' ' + (h.address || '')).toLowerCase();
        return hay.includes(q);
      });
    }
    // Cap the rendered list so a huge fleet doesn't paint hundreds of
    // <li> nodes; the operator narrows with the query rather than scroll.
    return out.slice(0, 50);
  },

  // Commit a host selection from the dropdown (click or keyboard Enter).
  selectAppsDiscoverHost(h) {
    if (!h || !h.id) {
      return;
    }
    this.appsDiscoverForm.host_id = h.id;
    this.appsDiscoverHostSearch = this.appsHostLabel(h);
    this.appsDiscoverHostDropdownOpen = false;
    this.appsDiscoverHostActiveIdx = -1;
    this.runAppsDiscovery();
  },

  // Arrow-key navigation through the filtered match list. Opens the
  // dropdown on first keypress and clamps the highlight index in range.
  appsDiscoverHostMove(delta) {
    this.appsDiscoverHostDropdownOpen = true;
    const n = this.appsDiscoverFilteredHosts().length;
    if (!n) {
      this.appsDiscoverHostActiveIdx = -1;
      return;
    }
    let idx = this.appsDiscoverHostActiveIdx + delta;
    if (idx < 0) {
      idx = n - 1;
    }
    if (idx >= n) {
      idx = 0;
    }
    this.appsDiscoverHostActiveIdx = idx;
  },

  // Enter key: select the highlighted match, or — when nothing is
  // highlighted but the filter narrows to exactly one host — that host.
  appsDiscoverHostEnter() {
    const list = this.appsDiscoverFilteredHosts();
    if (this.appsDiscoverHostActiveIdx >= 0 && this.appsDiscoverHostActiveIdx < list.length) {
      this.selectAppsDiscoverHost(list[this.appsDiscoverHostActiveIdx]);
    } else if (list.length === 1) {
      this.selectAppsDiscoverHost(list[0]);
    }
  },

  async runAppsDiscovery() {
    const hostId = (this.appsDiscoverForm && this.appsDiscoverForm.host_id) || '';
    if (!hostId) {
      this.appsDiscoverResult = {detected_ports: [], proposals: [], scanned_at: 0};
      return;
    }
    this.appsDiscoverLoading = true;
    this.appsDiscoverError = '';
    this.appsDiscoverResult = {detected_ports: [], proposals: [], scanned_at: 0};
    this.appsDiscoverSelected = new Set();
    try {
      const r = await fetch(`/api/services/discover/${encodeURIComponent(hostId)}`, {
        method: 'POST',
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      const j = await r.json();
      this.appsDiscoverResult = j;
      // Pre-select proposals with confidence >= 0.9 — high-confidence
      // matches (all template ports detected + name match OR multi-port
      // exact match) are the safe bulk-bind candidates.
      const preSelected = new Set();
      for (const prop of (j.proposals || [])) {
        if (prop && prop.confidence >= 0.9 && prop.catalog && prop.catalog.id) {
          preSelected.add(prop.catalog.id);
        }
      }
      this.appsDiscoverSelected = preSelected;
    } catch (err) {
      this.appsDiscoverError = err && err.message ? err.message : String(err);
    } finally {
      this.appsDiscoverLoading = false;
    }
  },

  toggleAppsDiscoverSelection(catalogId) {
    if (!catalogId) {
      return;
    }
    // Alpine 3 reacts to Set assignment, not mutation. Clone, mutate,
    // reassign so :checked bindings re-evaluate on toggle.
    const next = new Set(this.appsDiscoverSelected || []);
    if (next.has(catalogId)) {
      next.delete(catalogId);
    } else {
      next.add(catalogId);
    }
    this.appsDiscoverSelected = next;
  },

  toggleAllAppsDiscoverSelections(checked) {
    if (!this.appsDiscoverResult) {
      return;
    }
    if (checked) {
      const next = new Set();
      for (const prop of (this.appsDiscoverResult.proposals || [])) {
        if (prop && prop.catalog && prop.catalog.id) {
          next.add(prop.catalog.id);
        }
      }
      this.appsDiscoverSelected = next;
    } else {
      this.appsDiscoverSelected = new Set();
    }
  },

  async submitAppsDiscoverApply() {
    if (this.appsDiscoverApplying) {
      return;
    }
    const hostId = (this.appsDiscoverForm && this.appsDiscoverForm.host_id) || '';
    if (!hostId) {
      this.appsDiscoverApplyError = this.t('admin_apps.pin_pick_host') || 'Pick a host';
      return;
    }
    const ids = Array.from(this.appsDiscoverSelected || []);
    if (!ids.length) {
      this.appsDiscoverApplyError = this.t('admin_apps.discover_select_at_least_one') || 'Select at least one proposal';
      return;
    }
    this.appsDiscoverApplying = true;
    this.appsDiscoverApplyError = '';
    try {
      const r = await fetch(`/api/services/discover/${encodeURIComponent(hostId)}/apply`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({catalog_ids: ids, probe_enabled: true}),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      const j = await r.json();
      await this.loadAppsInstances();
      if (typeof this.toast === 'function') {
        const nApplied = (j.applied || []).length;
        const nSkipped = (j.skipped || []).length;
        let msg = (this.t('admin_apps.discover_applied') || 'Pinned ') + nApplied;
        if (nSkipped) {
          msg += ' · ' + nSkipped + ' ' + (this.t('admin_apps.discover_skipped') || 'skipped');
        }
        this.toast(msg, 'success');
      }
      this.closeAppsDiscoverWizard();
    } catch (err) {
      this.appsDiscoverApplyError = err && err.message ? err.message : String(err);
    } finally {
      this.appsDiscoverApplying = false;
    }
  },

  async reseedAppCatalog() {
    if (this.appsCatalogReseeding) {
      return;
    }
    this.appsCatalogReseeding = true;
    this.appsCatalogStatus = '';
    try {
      const r = await fetch('/api/services/catalog/seed', {method: 'POST'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      const j = await r.json();
      await this.loadAppsCatalog();
      this.appsCatalogStatus = (this.t('admin_apps.reseed_done') || 'Re-seeded') + ': +' + (j.added || 0);
      if (typeof this.toast === 'function') {
        this.toast(this.appsCatalogStatus, 'success');
      }
    } catch (err) {
      this.appsCatalogStatus = err && err.message ? err.message : String(err);
    } finally {
      this.appsCatalogReseeding = false;
    }
  },
};
