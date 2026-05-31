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
/* global Alpine, Swal, I18N, t, AbortController, setTimeout, clearTimeout */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// App drawer surfaces: openAppDrawer + closeAppDrawer + resync,
// per-host drawer (openAppHostDrawer + …), per-instance debug pane,
// in-drawer probe/test/restart/update/logs actions, Docker-link
// Logs modal (closeAppLogModal/reloadAppLog/_fetchAppLog), and
// the per-host bulk probe-all fan-out (probeAllHostApps +
// _fanOutBounded). Split from app-apps.js when it crossed the
// 3000-line split-candidate threshold.

export default {
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

  // ----------------------------------------------------------------
  // Apps view group-by mode — 'app' (default: one card per app, with
  // its per-host instances inside) or 'host' (one card per host, with
  // its app icons beneath). Persisted to localStorage.
  // ----------------------------------------------------------------
  appsViewGroupBy: (() => {
    try {
      const v = localStorage.getItem('appsViewGroupBy');
      return ['app', 'host', 'custom'].includes(v) ? v : 'app';
    } catch (_) {
      return 'app';
    }
  })(),

  openAppHostDrawer(group) {
    if (!group) {
      return;
    }
    this.drawerAppHost = group;
  },

  closeAppHostDrawer() {
    this.drawerAppHost = null;
  },

  isAppHostDrawerOpen() {
    return !!this.drawerAppHost;
  },

  // Re-point an open host-app drawer to the matching refreshed host
  // group after an apps reload (by host_id). No-op when closed or the
  // host no longer has any apps.
  _resyncDrawerAppHost() {
    if (!this.drawerAppHost) {
      return;
    }
    const hid = this.drawerAppHost.host_id;
    const match = this.appsHostGroups().find((g) => g && g.host_id === hid);
    if (match) {
      this.drawerAppHost = match;
    }
  },

  // From the host-app drawer, open the per-app drawer for one of the
  // host's apps. Closes the host drawer first so only one drawer is
  // open at a time, then resolves the full app group by group_id.
  openAppFromHostDrawer(appEntry) {
    if (!appEntry) {
      return;
    }
    const gid = appEntry.group_id;
    const app = (this.appsList || []).find((g) => g && g.group_id === gid);
    this.closeAppHostDrawer();
    if (app) {
      this.openAppDrawer(app);
    }
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
        throw new Error(this.fmtApiError(j, r.status));
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
      await this.loadAppInstanceDebug(inst);
    }
    if (typeof this.loadAppsList === 'function') {
      await this.loadAppsList(true);
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

  // Resolve a Docker-linked chip to its item in the current /api/items
  // snapshot. A container link matches by name AND (when stored) host —
  // so a name shared across hosts resolves to the precise target; falls
  // back to name-only (host may have changed since the link was saved)
  // + raw_id/id. A service link matches by name / stack namespace.
  // Returns null when nothing matches.
  _appDrawerResolveItem(inst) {
    if (!inst) {
      return null;
    }
    const items = Array.isArray(this.items) ? this.items : [];
    if (inst.docker_container) {
      const c = items.find((it) => it && (it.type === 'container' || it.type === 'orphan')
        && (it.name === inst.docker_container || it.raw_id === inst.docker_container || it.id === inst.docker_container)
        && (!inst.docker_host || (it.node || '') === inst.docker_host));
      if (c) {
        return c;
      }
      if (inst.docker_host) {
        const c2 = items.find((it) => it && (it.type === 'container' || it.type === 'orphan')
          && (it.name === inst.docker_container || it.raw_id === inst.docker_container || it.id === inst.docker_container));
        if (c2) {
          return c2;
        }
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

  // True when the chip resolves to ANY Docker target (container OR
  // service) in the current items list — gates the App-drawer Logs
  // button. Container links tail one container (agent-target routed);
  // service links tail the Swarm service's aggregated logs.
  appDrawerCanLogs(inst) {
    const target = this._appDrawerResolveItem(inst);
    return !!(target && (target.raw_id || target.id));
  },

  // Open the logs modal for a Docker-linked chip + fetch the tail.
  // Resolves the chip in /api/items: a CONTAINER hits the
  // /api/container/{raw_id}/logs proxy (carrying node for agent-target
  // routing); a SERVICE hits /api/service/{raw_id}/logs (manager-level
  // aggregate, no node). `kind` drives which endpoint _fetchAppLog uses.
  appDrawerLogs(inst) {
    const target = this._appDrawerResolveItem(inst);
    if (!target || !(target.raw_id || target.id)) {
      if (typeof this.showToast === 'function') {
        this.showToast(this.t('apps.drawer.logs_no_container') || 'No linked Docker target resolved for logs', 'error');
      }
      return;
    }
    const isService = target.type === 'service';
    this.appLogModal = {
      open: true,
      loading: false,
      error: '',
      text: '',
      title: (inst && inst.name) || target.name || '',
      raw_id: target.raw_id || target.id || '',
      node: isService ? '' : (target.node || ''),
      kind: isService ? 'service' : 'container',
      tail: 200,
    };
    this._fetchAppLog();
  },

  closeAppLogModal() {
    this.appLogModal.open = false;
    this.appLogModal.text = '';
    this.appLogModal.error = '';
  },

  reloadAppLog() {
    if (this.appLogModal.open) {
      this._fetchAppLog();
    }
  },

  async _fetchAppLog() {
    const m = this.appLogModal;
    if (!m.raw_id) {
      m.error = this.t('apps.drawer.logs_no_container') || 'No container resolved';
      return;
    }
    m.loading = true;
    m.error = '';
    try {
      const qs = new URLSearchParams({tail: String(m.tail || 200)});
      if (m.node) {
        qs.set('node', m.node);
      }
      // Service links tail the Swarm-service aggregate; container links
      // tail the single container (node-scoped).
      const base = (m.kind === 'service') ? '/api/service/' : '/api/container/';
      const r = await fetch(base + encodeURIComponent(m.raw_id) + '/logs?' + qs.toString());
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      const j = await r.json();
      m.text = j.logs || '';
    } catch (err) {
      m.error = err && err.message ? err.message : String(err);
    } finally {
      m.loading = false;
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
  async probeAppNow(host, serviceIdx, opts = {}) {
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
    // Defence-in-depth: abort the probe after 30s so a hung request can
    // NEVER leave the button stuck-disabled forever — the finally always
    // re-enables it. (The server probe has its own per-port timeouts; this
    // is just a client-side safety net for a pathological hang.)
    const _ctrl = new AbortController();
    const _abortTimer = setTimeout(() => _ctrl.abort(), 30000);
    try {
      const r = await fetch(`/api/services/${encodeURIComponent(host.id)}/${serviceIdx}/probe`, {
        method: 'POST',
        signal: _ctrl.signal,
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
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
      if (!opts.silent && typeof this.toast === 'function') {
        const msg = j.alive
          ? (this.t('host_drawer.apps.probe_success') || 'Probe OK') + (j.rtt_ms != null ? ' (' + j.rtt_ms + 'ms)' : '')
          : (this.t('host_drawer.apps.probe_failed') || 'Probe failed') + (j.error ? ': ' + j.error : '');
        this.toast(msg, j.alive ? 'success' : 'error');
      }
      // The in-place patch above updates the chip's rollup status, but the
      // host-drawer tiles now show status via the per-port pills (driven by
      // app.port_results, which the probe just refreshed server-side). Re-
      // fetch the host row so those pills reflect the new probe result —
      // refreshHostRow updates the same object drawerHost references, so the
      // open drawer's tiles update without a full re-open.
      if (!opts.skipRefresh && typeof this.refreshHostRow === 'function') {
        this.refreshHostRow(host.id, {force: true}).catch(() => {
          // best-effort refresh; ignore failures
        });
      }
    } catch (err) {
      if (!opts.silent && typeof this.toast === 'function') {
        this.toast(
          (this.t('host_drawer.apps.probe_error') || 'Probe error: ') + (err && err.message ? err.message : err),
          'error'
        );
      }
    } finally {
      clearTimeout(_abortTimer);
      this.probeNowInFlight[key] = false;
    }
  },

  // Probe EVERY app on a host in one click — the host-drawer Apps card's
  // header "Probe all" button. Always-visible + clearly labelled, so it's
  // unmistakably an active control (unlike the compact per-tile refresh
  // icon). Reuses probeAppNow per app with {silent, skipRefresh} so it
  // doesn't fire N toasts / N host-refreshes, then does ONE refresh + ONE
  // summary toast at the end.
  async probeAllHostApps(h) {
    if (!h || !Array.isArray(h.apps) || !h.apps.length) {
      return;
    }
    if (!this._hostAppsProbingAll) {
      this._hostAppsProbingAll = {};
    }
    if (this._hostAppsProbingAll[h.id]) {
      return;
    }
    this._hostAppsProbingAll[h.id] = true;
    try {
      // Snapshot the service_idx list up front — refreshHostRow mutates
      // h.apps in place, so iterating it live could skip/repeat.
      const idxs = h.apps.map((a) => a && a.service_idx).filter((x) => x != null);
      for (const idx of idxs) {
        await this.probeAppNow(h, idx, {silent: true, skipRefresh: true});
      }
      if (typeof this.refreshHostRow === 'function') {
        await this.refreshHostRow(h.id, {force: true}).catch(() => undefined);
      }
      if (typeof this.toast === 'function') {
        const apps = Array.isArray(h.apps) ? h.apps : [];
        const up = apps.filter((a) => a && a.status === 'up').length;
        const msg = this.t('host_drawer.apps.probe_all_done', {up: up, total: apps.length}) || ('Probed ' + apps.length + ' apps — ' + up + ' up');
        this.toast(msg, 'success');
      }
    } finally {
      this._hostAppsProbingAll[h.id] = false;
    }
  },

  // Probe ONLY the failed / down apps on a host — the common case is
  // "I just fixed the down ones, are they back up?" without re-probing
  // the whole (mostly-up) list. Filters to apps whose status is set and
  // not 'up' (down / unknown / degraded); otherwise identical to
  // probeAllHostApps (silent per-app + one refresh + one summary toast).
  // Shares the `_hostAppsProbingAll[h.id]` busy flag so it can't overlap
  // a probe-all run.
  async probeFailedHostApps(h) {
    if (!h || !Array.isArray(h.apps) || !h.apps.length) {
      return;
    }
    if (!this._hostAppsProbingAll) {
      this._hostAppsProbingAll = {};
    }
    if (this._hostAppsProbingAll[h.id]) {
      return;
    }
    const failedIdxs = h.apps
      .filter((a) => a && a.status && a.status !== 'up' && a.service_idx != null)
      .map((a) => a.service_idx);
    if (!failedIdxs.length) {
      if (typeof this.toast === 'function') {
        this.toast(this.t('host_drawer.apps.probe_failed_none') || 'No down services to re-probe', 'success');
      }
      return;
    }
    this._hostAppsProbingAll[h.id] = true;
    try {
      for (const idx of failedIdxs) {
        await this.probeAppNow(h, idx, {silent: true, skipRefresh: true});
      }
      if (typeof this.refreshHostRow === 'function') {
        await this.refreshHostRow(h.id, {force: true}).catch(() => undefined);
      }
      if (typeof this.toast === 'function') {
        const apps = Array.isArray(h.apps) ? h.apps : [];
        const probed = failedIdxs.length;
        const up = apps.filter((a) => a && failedIdxs.includes(a.service_idx) && a.status === 'up').length;
        const msg = this.t('host_drawer.apps.probe_failed_done', {up: up, total: probed})
          || ('Re-probed ' + probed + ' down — ' + up + ' now up');
        this.toast(msg, 'success');
      }
    } finally {
      this._hostAppsProbingAll[h.id] = false;
    }
  },

  // ----------------------------------------------------------------
  // Apps-VIEW per-app bulk actions — "Probe all (N)" + "Open all".
  // These live on the top-level Apps card (one card = one catalog
  // template, with N instances spread across hosts), distinct from the
  // host-drawer "Probe all" above (which is one host, N apps).
  // ----------------------------------------------------------------

  // Shared bounded fan-out: run `worker(item, i)` across `items` with at
  // most `concurrency` in flight, collecting each result (or the thrown
  // error) into a positional results array. A reusable primitive so other
  // bulk-with-inline-results surfaces (e.g. a future "Test all integrations"
  // sweep) don't each re-roll a Promise pool. Never rejects — a worker
  // throw is captured as {ok:false, error} so one bad item can't abort the
  // batch.
  async _fanOutBounded(items, worker, concurrency = 4) {
    const arr = Array.isArray(items) ? items : [];
    const results = new Array(arr.length);
    let next = 0;
    const runOne = async () => {
      while (true) {
        const i = next;
        next += 1;
        if (i >= arr.length) {
          return;
        }
        try {
          results[i] = await worker(arr[i], i);
        } catch (err) {
          results[i] = {ok: false, error: (err && err.message) ? err.message : String(err)};
        }
      }
    };
    const lanes = Math.max(1, Math.min(concurrency, arr.length));
    const pool = [];
    for (let k = 0; k < lanes; k += 1) {
      pool.push(runOne());
    }
    await Promise.all(pool);
    return results;
  },

  // True while a "Probe all" batch is running for this host — drives the
  // header button's spinner + disabled state.
  hostAppsProbingAll(h) {
    return !!(h && this._hostAppsProbingAll && this._hostAppsProbingAll[h.id]);
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
};
