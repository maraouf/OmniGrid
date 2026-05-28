// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection OverlyComplexBooleanExpressionJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall
// noinspection RedundantLocalVariableJS,JSReusedLocalVariable,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,ExceptionCaughtLocallyJS
// noinspection HtmlUnknownTag,HtmlEmptyContent,HtmlEmptyTagsRecommendation,InnerHTMLJS,VoidExpressionJS,JSVoidExpression
// SVG self-closing tags inside JS string literals (`<path/>`, `<line/>`,
// `<rect/>`) are valid SVG syntax and render correctly in every modern
// browser — PyCharm's "Empty tag doesn't work in some browsers" is a
// legacy XHTML-era warning. The "Unknown html tag pending" / `hostname`
// / `x` / `txt` etc. flags fire on literal angle-bracket placeholders
// inside JS strings (template tokens like `<pending>`, AI-prompt
// markers like `<hostname>`) that PyCharm's HTML parser treats as
// markup. These are string content, not actual HTML.
// Per-inspection suppressions above (NOT blanket "ALL") mirror the
// shape app-ai-admin.js settled on. The SPA-wide conventions these
// cover are explicit operator choices: constants on the right of
// comparisons (modern ESLint default — opposite of Yoda); anonymous
// arrow callbacks for Alpine bindings; chained map+filter; ternaries
// for short conditional rendering; magic numbers for unit-conversion
// constants (60 / 3600 / 86400 seconds); Alpine-called methods that
// PyCharm can't trace through x-on:click attributes; idiomatic
// `catch {}` no-op blocks for non-critical path branches. Genuine
// bugs in other inspection IDs (unresolved imports, real type
// mismatches) still surface.
/* global Alpine, Swal, I18N, t */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA — Admin view nav + data loaders + actions.
//
// SPLIT FROM `app.js`. Cross-method `this.X` references keep working
// through the `_mergeKeepDescriptors` chain in app.js.

// memo the Stacks/Services per-item sparkline `points` string per
// sparks-row ARRAY REFERENCE. this.sparks is replaced wholesale on every
// pollSparks (app-stats.js) so each item's rows array is a fresh reference per
// poll -> the WeakMap entry busts; between polls it's stable -> hits. Sub-keyed
// by metric + rows.length. An expanded Stacks view binds sparkPoints for
// cpu/mem/disk per visible item, so this collapses N*3 fresh string builds per
// flush to one-per-(item,metric) until the next poll. Same WeakMap-on-series
// contract as the shipped host-row _hostSparkData; safe because these bindings
// live inside the Stacks/Services x-for, which re-renders on the sparks poll.
const _sparkPointsMemo = new WeakMap();

export default {

  // -----------------------------------------------------------------
  // Admin view — nav + data loaders + actions.
  // -----------------------------------------------------------------
  navItems() {
    // Settings + Admin live in the avatar dropdown, not the top nav —
    // the top nav stays focused on the fleet views.
    return [
      ['stacks', this.t('nav.stacks')],
      ['services', this.t('nav.services')],
      ['nodes', this.t('nav.nodes')],
      ['hosts', this.t('nav.hosts')],
      ['apps', this.t('nav.apps') || 'Apps'],
      ['history', this.t('nav.history')],
    ];
  },
  // Inner-SVG markup for each top-nav icon. Returned as an HTML
  // string rendered via `x-html` on a shared <svg> wrapper so the
  // stroke / viewBox / size stay consistent. Lucide-derived shapes —
  // layered-squares for Stacks, cube for Services, server-rack for
  // Nodes, monitor for Hosts, grid for Apps, clock-with-arrow for
  // History.
  navIcon(key) {
    const icons = {
      stacks: '<path d="M12 2 2 7l10 5 10-5-10-5z"></path><path d="m2 17 10 5 10-5"></path><path d="m2 12 10 5 10-5"></path>',
      services: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line>',
      nodes: '<rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect><rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect><line x1="6" y1="6" x2="6.01" y2="6"></line><line x1="6" y1="18" x2="6.01" y2="18"></line>',
      hosts: '<rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line>',
      apps: '<rect x="3" y="3" width="7" height="7" rx="1"></rect><rect x="14" y="3" width="7" height="7" rx="1"></rect><rect x="3" y="14" width="7" height="7" rx="1"></rect><rect x="14" y="14" width="7" height="7" rx="1"></rect>',
      history: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"></path><path d="M3 3v5h5"></path><path d="M12 7v5l4 2"></path>',
    };
    return icons[key] || '';
  },

  toggleNode(name) {
    const i = this.expanded.indexOf('node:' + name);
    if (i >= 0) {
      this.expanded.splice(i, 1);
    } else {
      this.expanded.push('node:' + name);
    }
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
      const hasProvider = !!(h.beszel_id || h.ne_url || h.pulse_name || h.webmin_name);
      const needsWarm = !!key && hasProvider && !this.hostHistory[key];
      if (needsWarm) {
        this.loadHostHistory(h.beszel_id || '', h.id);
      }
    }
  },
  collapseAllHosts() {
    this.hostsExpanded = [];
  },

  // "Is there an in-flight prune_node op targeting this host?". Drives
  // the button's spinner + disabled state so rapid double-clicks don't
  // queue a second prune — activeOps is the same list the ops panel reads.
  isPruneBusy(host) {
    return (this.activeOps || []).some(o =>
      o.op_type === 'prune_node' && o.target_id === host
    );
  },

  async pruneNode(host, opts) {
    // Confirm first — `docker system prune --volumes` is destructive:
    // stopped containers go away, dangling images, unused networks, AND
    // unused volumes (which can carry data users forgot was orphaned).
    const skipConfirm = !!(opts && opts.skipConfirm);
    if (!skipConfirm) {
      const res = await Swal.fire({
        title: this.t('admin.nodes.prune_prompt_title', {host}),
        html: this.t('admin.nodes.prune_prompt_html', {host}),
        icon: 'warning',
        showCancelButton: true,
        confirmButtonText: this.t('actions.prune_now'),
        cancelButtonText: this.t('actions.cancel'),
        confirmButtonColor: this._cssVar('--danger'),
      });
      if (!res.isConfirmed) {
        return;
      }
    }
    try {
      const r = await fetch('/api/prune/node/' + encodeURIComponent(host), {method: 'POST'});
      if (r.ok) {
        // Auto-expand the floating ops panel (bottom-right) so a new
        // user actually SEES where the live progress lives. Without
        // this, the toast refers to a panel that only appears briefly
        // and stays collapsed if they've dismissed it before.
        this.opsExpanded = true;
        this.showToast(this.t('toasts_extra.prune_started', {host}));
        // Kick an immediate ops poll so the button flips to "Pruning…".
        if (this.pollOnce) {
          this.pollOnce();
        }
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts_extra.save_failed_generic'), 'error');
      }
    } catch {
      // Network failure (fetch threw) — surface a generic toast.
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
    if (!this.me || !this.me.username) {
      return '?';
    }
    const c = this.me.username.trim().charAt(0);
    return c ? c.toUpperCase() : '?';
  },

  // URL routing helpers — keep the path in sync with view + section
  // so a browser refresh or shared link lands on the same screen.
  //
  // Path shape:
  // /                            → stacks (default)
  // /{view}                      → top-level view
  // /settings/{section}          → Settings sidebar section
  // /admin/{tab}                 → Admin sidebar tab
  // Unknown paths are left alone (the static file server already
  // handles login / assets; this only intervenes for known views).
  _routeViews() {
    return new Set(['stacks', 'services', 'nodes', 'hosts', 'apps', 'history', 'settings', 'admin', 'stats']);
  },
  _applyRouteFromPath() {
    const parts = (location.pathname || '/').split('/').filter(Boolean);
    if (!parts.length) {
      return;
    }
    const head = parts[0];
    if (!this._routeViews().has(head)) {
      return;
    }
    // Only assign if the current state differs, so this doesn't
    // thrash re-renders when the pushState we just wrote fires
    // popstate-like flows (it doesn't — noted for future-proofing).
    if (this.view !== head) {
      this.view = head;
    }
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
    } else if (head === 'stats') {
      // /stats with no sub-tab defaults to the current statsTab
      // (init 'dashboard'); /stats/<id> opens that sub-tab. Always
      // route through openStatsTab so the matching loader fires —
      // without this the dashboard's loading-spinner never resolves
      // because loadStatsOverview() was never invoked.
      const target = (sub && (this.statsSections || []).some(s => s.id === sub))
        ? sub
        : (this.statsTab || 'dashboard');
      this.openStatsTab(target);
    }
  },
  _pushRoute() {
    let path = '/' + (this.view || 'stacks');
    if (this.view === 'settings' && this.settingsSection) {
      path += '/' + this.settingsSection;
    } else if (this.view === 'admin' && this.adminTab) {
      path += '/' + this.adminTab;
    } else if (this.view === 'stats' && this.statsTab) {
      path += '/' + this.statsTab;
    }
    // replaceState rather than pushState so refresh lands on the
    // same page without adding history entries per tab switch.
    // Back/forward via actual nav (hash changes, manual link) still
    // work because popstate runs _applyRouteFromPath.
    if (location.pathname !== path) {
      try {
        history.replaceState(null, '', path);
      } catch {
        // history.replaceState can throw in restricted contexts (file://
        // origins, sandboxed iframes). Path stays as-is; non-fatal.
      }
    }
  },

  async openStatsTab(tab) {
    // Stats view (admin-only) — mirrors openAdminTab's shape: switch
    // view, set sub-tab, fire the matching loader.
    this.view = 'stats';
    this.statsTab = tab || 'dashboard';
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('statsTab', this.statsTab);
      }
    } catch {
      // Private-mode browsers throw on localStorage writes; sub-tab
      // choice doesn't persist across reloads in that case.
    }
    // Dispatch table replaces a 6-deep nested else/if chain so adding
    // a new sub-tab is one row, not another indentation level.
    const loaders = {
      dashboard: this.loadStatsOverview,
      database: this.loadStatsDatabase,
      samples: this.loadStatsSamples,
      incidents: this.loadStatsIncidents,
      network: this.loadStatsNetwork,
      ai_cost: this.loadStatsAiCost,
    };
    const loader = loaders[this.statsTab];
    if (loader) {
      await loader.call(this);
    }
    if (this._pushRoute) {
      this._pushRoute();
    }
  },
  sortStatsSamplesBy(col) {
    if (this.statsSamplesSortBy === col) {
      // Same column → flip direction.
      this.statsSamplesSortDir = (this.statsSamplesSortDir === 'asc') ? 'desc' : 'asc';
    } else {
      this.statsSamplesSortBy = col;
      // Sensible default direction per column type: numeric / time
      // columns sort desc-first (largest / newest); string columns
      // sort asc-first (alphabetical).
      const isNum = (col === 'rows' || col === 'unique_hosts'
        || col === 'oldest_ts' || col === 'newest_ts');
      this.statsSamplesSortDir = isNum ? 'desc' : 'asc';
    }
  },
  // Per-provider drill-down modal — fetches per-host row counts for
  // ONE sample-bearing table, sorted DESC. Footer total cross-checks
  // against the outer per-table count rendered on the Samples page.
  async openStatsSamplesDrillDown(row) {
    if (!row || !row.name) {
      return;
    }
    const label = (row.provider || '') + ' — ' + (row.name || '');
    this.statsSamplesDrillDown = {
      open: true,
      loading: true,
      table: row.name,
      // Provider tag exposed to row-context helpers (e.g. the
      // Portainer `stats_samples` table uses `item_id` referring
      // to containers, NOT hosts — so the "no longer curated"
      // marker text + the orphan-delete button copy adapt).
      provider: (row.provider || '').toLowerCase(),
      host_col: '',
      label: label,
      rows: [],
      total: 0,
      outer: Number(row.rows || 0),  // stale-fallback only — backend overwrites with fresh snapshot
      error: '',
      // Per-row prune busy-state map keyed by host_id. Prevents
      // rapid clicks on the same Delete button from firing twice.
      pruning: {},
    };
    try {
      const r = await fetch('/api/admin/stats/samples/by-host?table='
        + encodeURIComponent(row.name));
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        this.statsSamplesDrillDown.error = (d && d.detail) || ('HTTP ' + r.status);
        return;
      }
      this.statsSamplesDrillDown.rows = Array.isArray(d.rows) ? d.rows : [];
      this.statsSamplesDrillDown.total = Number(d.total || 0);
      this.statsSamplesDrillDown.host_col = d.host_col || '';
      // Backend's fresh outer_count is the authoritative number for
      // the cross-check (sampled in the same SELECT snapshot as the
      // per-host GROUP BY, so they MUST match unless there's a real
      // SQL bug). The stale `row.rows` from the Samples-page load
      // stays as a fallback only.
      if (d.outer_count !== undefined && d.outer_count !== null) {
        this.statsSamplesDrillDown.outer = Number(d.outer_count);
      }
      if (d.error) {
        this.statsSamplesDrillDown.error = d.error;
      }
    } catch (e) {
      this.statsSamplesDrillDown.error = (e && e.message) || String(e);
    } finally {
      this.statsSamplesDrillDown.loading = false;
    }
  },
  closeStatsSamplesDrillDown() {
    this.statsSamplesDrillDown = {
      open: false, loading: false, table: '', provider: '',
      host_col: '', label: '', rows: [], total: 0, outer: 0,
      error: '', pruning: {},
    };
  },
  // Delete all rows in <table> for one host_id (orphan or
  // intentional cleanup). Audit-logged on the backend via the
  // `samples_prune_orphan` op_type so History shows what got
  // pruned + when + by whom.
  async pruneStatsSampleRows(row) {
    if (!row || !row.host_id) {
      return;
    }
    const table = this.statsSamplesDrillDown.table;
    if (!table) {
      return;
    }
    if (this.statsSamplesDrillDown.pruning[row.host_id]) {
      return;
    }
    const hostId = row.host_id;
    const rowCount = Number(row.rows || 0).toLocaleString();
    const ok = await this.confirmDialog({
      title: this.t('stats.samples.drill_down.prune_confirm_title')
        || 'Delete sample rows?',
      html: this.t('stats.samples.drill_down.prune_confirm_html', {id: hostId, count: rowCount, table})
        || ('Delete <strong>' + rowCount + '</strong> rows from <code>' + table
          + '</code> for <code>' + hostId + '</code>? This cannot be undone.'),
      icon: 'warning',
      confirmText: this.t('stats.samples.drill_down.prune_confirm_ok') || 'Delete',
      confirmColor: this._cssVar('--danger'),
      focusConfirm: false,
    });
    if (!ok) {
      return;
    }
    this.statsSamplesDrillDown.pruning[row.host_id] = true;
    try {
      const r = await fetch('/api/admin/stats/samples/by-host', {
        method: 'DELETE',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({table, host_id: hostId}),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        this.showToast(this.t('toasts.failed_with_error', {
          error: (d && d.detail) || ('HTTP ' + r.status),
        }), 'error');
        return;
      }
      this.showToast(
        this.t('stats.samples.drill_down.prune_success', {
          count: Number(d.deleted || 0).toLocaleString(), id: hostId,
        }) || (Number(d.deleted || 0).toLocaleString() + ' rows deleted for ' + hostId),
        'success',
      );
      // Re-fetch the drill-down so the row disappears + totals update.
      const refreshed = await fetch('/api/admin/stats/samples/by-host?table='
        + encodeURIComponent(table));
      const refData = await refreshed.json().catch(() => ({}));
      if (refreshed.ok) {
        this.statsSamplesDrillDown.rows = Array.isArray(refData.rows) ? refData.rows : [];
        this.statsSamplesDrillDown.total = Number(refData.total || 0);
        if (refData.outer_count !== undefined && refData.outer_count !== null) {
          this.statsSamplesDrillDown.outer = Number(refData.outer_count);
        }
      }
    } catch (e) {
      this.showToast(this.t('toasts.failed_with_error', {
        error: (e && e.message) || String(e),
      }), 'error');
    } finally {
      this.statsSamplesDrillDown.pruning[row.host_id] = false;
    }
  },

  async openAdminTab(tab) {
    // Stop the logs poller when leaving the Logs tab; it restarts
    // when the tab is opened again. Keeps network traffic silent
    // while the operator is elsewhere.
    if (this.adminTab === 'logs' && tab !== 'logs') {
      this._stopLogPoll();
    }
    this.adminTab = tab;
    if (tab === 'users') {
      await this.loadUsers();
    } else {
      if (tab === 'sessions') {
        await this.loadSessions();
      } else {
        if (tab === 'tokens') {
          await this.loadTokens();
        } else {
          if (tab === 'backups') {
            await this.loadBackups();
          } else {
            if (tab === 'config_backup') {
              await this.loadConfigBackupSaved();
            } else {
              if (tab === 'schedules') {
                // Fire both loads in parallel — the scheduled table and the queue
                // table aren't related state-wise, no reason to wait on each other.
                await Promise.all([this.loadSchedules(), this.loadScheduleQueue()]);
              } else if (tab === 'logs') {
                await this.loadLogs(true);
                // Logs tab also renders the `tuning_log_retention_days`
                // settings card (moved from Process tunables) so it needs
                // the tuningForm/tuningEffective state too. Cheap call,
                // dedupes against `tuningLoaded` so a re-open doesn't double
                // fetch.
                if (!this.tuningLoaded) {
                  await this.loadTuning();
                }
                this._startLogPoll();
              }
                // The four ex-Settings sections all read from the same /api/settings
                // payload, so a single load covers all of them. Load on every
              // open so edits from another tab don't go stale.
              else if (['notifications', 'general', 'portainer', 'oidc', 'providers'].includes(tab)) {
                await this.loadSettings();
                // Webmin section in Providers also renders a tunable card
                // (tuning_webmin_probe_budget_seconds); ensure tuning state
                // is available the first time the operator visits.
                if (tab === 'providers' && !this.tuningLoaded) {
                  await this.loadTuning();
                }
                // Notifications tab now hosts the relocated
                // tuning_notification_retention_days card; same lazy-load
                // pattern as Providers so the bounds-chips + effective-value
                // chip render on first visit instead of waiting for the
                // operator to bounce through Admin → Config first.
                if (tab === 'notifications' && !this.tuningLoaded) {
                  await this.loadTuning();
                }
                // The Ping test-target picker reads from `hostsConfig` (loaded
                // by the Hosts admin tab). When the operator opens the Providers
                // tab without ever visiting Admin → Hosts in this session, the
                // picker is empty and the dropdown shows "No ping-enabled hosts"
                // even though there are some. Lazy-load on first visit.
                if (tab === 'providers' && !(Array.isArray(this.hostsConfig) && this.hostsConfig.length)) {
                  this.loadHostsConfig().catch(() => {
                  });
                }
              } else if (tab === 'hosts') {
                await this.loadHostsConfig();
                // Host groups live in /api/settings; load it alongside so the
                // groups editor at the bottom of this tab has current data.
                await this.loadSettings();
              } else if (tab === 'apps') {
                // Admin → Apps tab — load catalog templates + flat
                // instance list. Both are cheap admin-only fetches;
                // run in parallel since they're independent.
                await Promise.all([
                  this.loadAppsCatalog(),
                  this.loadAppsInstances(),
                ]);
              } else if (tab === 'assets') {
                await this.loadSettings();
                await this.loadAssetCache();
              } else if (tab === 'ai') {
                // Hydrates the per-provider form state + the dashboard.
                // Two parallel calls — settings primes the form, dashboard
                // primes the tile grid. Failure of either is non-fatal: the
                // partial degrades to an empty-state.
                await Promise.all([this.loadSettings(), this.loadAiDashboard(true)]);
                // AI tab also renders the relocated `tuning_ai_retry_*`
                // tunables (sub-section "Auto-retry on transient overload");
                // ensure tuning state is hydrated on first visit so the
                // section's `x-show="tuningLoaded"` gate fires and the
                // bounds-chips / effective-value / form bindings render
                // instead of staying invisible. Same lazy-load pattern as
                // providers / notifications / logs tabs.
                if (!this.tuningLoaded) {
                  await this.loadTuning();
                }
              } else if (tab === 'config') {
                await this.loadTuning();
              }
                // Weather admin tab — same lazy-load pattern as
                // Providers / Notifications / Logs / AI / Port Scan:
                // the four weather tunables (cache TTL / fetch timeout
                // / history retention / sampler interval) bind to
                // `tuningForm[...]` and `tuningEffective[...]` so
                // first-visit needs the tuning state hydrated before
                // the inputs + bounds-chips + effective-value chip
                // render. Without this, the Advanced section shows
                // "default:" / "Effective: undefined" everywhere
                // (operator-reported bug).
              else if (tab === 'weather') {
                await this.loadSettings();
                if (!this.tuningLoaded) {
                  await this.loadTuning();
                }
              }
                // Port Scan admin tab — same lazy-load pattern as Providers /
                // Notifications / Logs / AI: the four port-scan tunables
                // (timeout / concurrency / max_seconds / banner_read) bind to
                // `tuningForm[...]`, so first-visit needs the tuning state
                // hydrated before the inputs render. Also load settings so
              // `port_scan_enabled` + `port_scan_default_ports` round-trip.
              else if (tab === 'port_scan') {
                await this.loadSettings();
                if (!this.tuningLoaded) {
                  await this.loadTuning();
                }
              }
            }
          }
        }
      }
    }
  },

  // Local JSON-validate so the operator gets feedback without a round-trip.
  // Empty string is normalised to {} so the common case (gather_refresh
  // has no params) doesn't require typing braces.
  _parseParamsText(raw) {
    const trimmed = (raw || '').trim();
    if (!trimmed) {
      return {};
    }
    let parsed;
    try {
      parsed = JSON.parse(trimmed);
    } catch (_) {
      throw new Error(this.t('admin.schedules.params_invalid_json'));
    }
    if (parsed == null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error(this.t('admin.schedules.params_must_be_object'));
    }
    return parsed;
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
    if (!hhmm || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(hhmm)) {
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
    if (i >= 0) {
      arr.splice(i, 1);
    } else {
      arr.push(day);
    }
    s.days_of_week = arr;
  },

  // --- Scheduler display helpers --------------------------------------
  // Reuse fmtDuration for last-duration column (same d/h/m/s bucketing).
  // humanInterval is similar but operates on the schedule's configured
  // interval — keep them separate so fmtDuration stays generic.
  humanInterval(sec) {
    if (!sec || sec <= 0) {
      return '—';
    }
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    const parts = [];
    if (d) {
      parts.push(d + 'd');
    }
    if (h) {
      parts.push(h + 'h');
    }
    if (m) {
      parts.push(m + 'm');
    }
    if (!parts.length) {
      parts.push(s + 's');
    }
    // Keep it tight — two units max for readability ("1d 6h", not "1d 6h 15m 30s").
    return parts.slice(0, 2).join(' ');
  },

  // "5 minutes ago" / "in 2 hours" — used for Last execution / Next
  // execution columns. Pure JS, no dependency. Returns '—' for unset
  // timestamps so the column renders a visible placeholder.
  humanRelTime(epoch) {
    if (!epoch) {
      return '—';
    }
    const delta = Math.round(epoch - (Date.now() / 1000));
    const abs = Math.abs(delta);
    let value, unit;
    if (abs < 60) {
      value = abs;
      unit = 'second';
    } else if (abs < 3600) {
      value = Math.round(abs / 60);
      unit = 'minute';
    } else if (abs < 86400) {
      value = Math.round(abs / 3600);
      unit = 'hour';
    } else {
      value = Math.round(abs / 86400);
      unit = 'day';
    }
    const suffix = value === 1 ? '' : 's';
    return delta >= 0
      ? this.t('admin.schedules.rel_in', {value, unit: unit + suffix})
      : this.t('admin.schedules.rel_ago', {value, unit: unit + suffix});
  },

  // Used only for the "Next execution" column — a past epoch there is
  // noise (the backend now always returns a future timestamp for
  // clock-anchored cadences, so this only fires for schedules the
  // tick loop is about to fire on its next pass).
  humanNextRun(epoch) {
    if (!epoch) {
      return '—';
    }
    const delta = Math.round(epoch - (Date.now() / 1000));
    if (delta <= 60) {
      return this.t('admin.schedules.due_soon');
    }
    return this.humanRelTime(epoch);
  },

  // One-line summary of how the schedule fires, for the table row.
  // Falls back to interval display if cadence_mode is missing (legacy rows).
  cadenceLabel(s) {
    const mode = s.cadence_mode || (s.run_at_hhmm ? 'daily' : 'interval');
    if (mode === 'daily' && s.run_at_hhmm) {
      return this.t('admin.schedules.daily_at', {hhmm: s.run_at_hhmm});
    }
    if (mode === 'weekly' && s.run_at_hhmm) {
      const days = (s.days_of_week || [])
        .map(d => this.t('admin.schedules.weekdays_short.' + d))
        .filter(Boolean)
        .join(', ');
      return this.t('admin.schedules.weekly_at', {days, hhmm: s.run_at_hhmm});
    }
    if (mode === 'monthly' && s.run_at_hhmm && s.day_of_month) {
      return this.t('admin.schedules.monthly_at', {
        dom: s.day_of_month, hhmm: s.run_at_hhmm,
      });
    }
    return this.humanInterval(s.interval_seconds);
  },

  // Multi-source selector helpers. ``host_stats_source`` is now a
  // CSV ("beszel,node_exporter") — these helpers let the Settings
  // checkboxes treat it as a set.
  hasHostStatsSource(name) {
    // Port-scan is an on-demand provider with its own master
    // toggle (`port_scan_enabled`), not a continuous-telemetry
    // provider in the `host_stats_source` CSV. Surface its
    // active-state through the same predicate so the tab strip's
    // dot indicator reads the right gate.
    if (name === 'port_scan') {
      return !!this.settings.port_scan_enabled;
    }
    // The probe-result providers (http_probe / service_probe) collapsed
    // their dual toggle (CSV inclusion + master enable) down to a single
    // master toggle. Read the master directly so the tab-strip dot
    // updates LIVE on master flip, not only after Save commits the
    // CSV-side mirror.
    if (name === 'http_probe') {
      return !!this.settings.http_probe_enabled;
    }
    if (name === 'service_probe') {
      return !!this.settings.service_probe_enabled;
    }
    const raw = this.settings.host_stats_source || '';
    return raw.split(',').map(s => s.trim()).includes(name);
  },
  // single source of truth for the disabled gate
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
    // Port-scan flips its dedicated master toggle, not the CSV.
    if (name === 'port_scan') {
      this.settings.port_scan_enabled = !!on;
      return;
    }
    const current = new Set(
      (this.settings.host_stats_source || '')
        .split(',').map(s => s.trim()).filter(s => s && s !== 'none'),
    );
    if (on) {
      current.add(name);
    } else {
      current.delete(name);
    }
    this.settings.host_stats_source = current.size
      ? Array.from(current).sort().join(',')
      : 'none';
  },

  // Admin → Providers provider tab switcher. Persists the
  // chosen tab in localStorage so the page returns to the operator's
  // view on refresh. Mirrors the existing `setRefreshInterval` /
  // localStorage shape.
  setHostStatsTab(name) {
    // Validate against `HOST_STATS_TAB_ORDER` (single source of
    // truth — see canonical-key-set rule in CLAUDE.md). Pre-fix
    // this hardcoded a parallel literal that lagged behind every
    // new tab — `port_scan` shipped in `HOST_STATS_TAB_ORDER` but
    // got silently rejected here, so the tab couldn't be clicked.
    if (!this.HOST_STATS_TAB_ORDER.includes(name)) {
      return;
    }
    this.hostStatsTab = name;
    try {
      localStorage.setItem('hostStatsTab', name);
    } catch {
    }
  },
  // Single source of truth for the strip's tab order. setHostStatsTab
  // already validates against this list; cycleHostStatsTab uses it to
  // implement ←/→ keyboard nav per the WAI-ARIA tablist authoring
  // pattern. New tabs added here automatically participate in
  // keyboard navigation.
  HOST_STATS_TAB_ORDER: ['node_exporter', 'beszel', 'pulse', 'webmin', 'ping', 'snmp', 'http_probe', 'service_probe'],
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
      if (el && typeof el.focus === 'function') {
        el.focus();
      }
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
      // port-scan provider — on-demand TCP scanner. Master toggle
      // + global defaults; per-host overrides live on
      // `hosts_config[].port_scan` and ride `saveHostsConfig`.
      // `port_scan_default_timeout` / `port_scan_default_concurrency`
      // legacy plain keys are GONE — they were tracked here despite
      // never being bound in any partial (the UI binds to
      // `tuningForm['tuning_port_scan_default_*']`). Removing them
      // from the pick array eliminates the perpetually-`undefined`
      // baseline entries that masked real dirty-state changes.
      'port_scan_enabled', 'port_scan_default_ports',
      // Port-scan UDP companion (Stage 2). UDP runs under the
      // master `port_scan_enabled` toggle (operator-flagged
      // 2026-05-10 to remove the separate `port_scan_udp_enabled`
      // flag — TCP + UDP enable/disable together). Default UDP
      // ports + UDP tunables still ride the dirty tracker.
      'port_scan_udp_default_ports',
      // SNMP. v3 secret keys behave like beszel_password /
      // webmin_password — `_set` flag indicates persisted state, the
      // `*_key` strings are blanked on the form so any typed value
      // marks dirty. The aliases JSON also rides this dirty list.
      'snmp_default_community', 'snmp_default_version',
      'snmp_default_port', 'snmp_v3_user',
      'snmp_v3_auth_key', 'snmp_v3_priv_key',
      'snmp_aliases_json',
      // HTTP / TLS / DNS probe — master toggle + per-provider
      // chip colour. Without these keys in the snapshot pick list,
      // the baseline JSON omits them and `httpProbeSectionDirty()`
      // compares `settings.http_probe_enabled` against
      // `undefined` — the section reads as dirty forever even
      // after a successful save.
      'http_probe_enabled',
      // Per-service reachability probe — master toggle. Same drift
      // class fix as `http_probe_enabled`.
      'service_probe_enabled',
      // per-provider chip colour overrides.
      'provider_color_beszel', 'provider_color_pulse',
      'provider_color_node_exporter', 'provider_color_webmin',
      'provider_color_ping', 'provider_color_snmp',
      'provider_color_http_probe', 'provider_color_service_probe',
    ];
    const subset = {};
    for (const k of pick) {
      subset[k] = s[k];
    }
    try {
      return JSON.stringify(subset);
    } catch {
      return '';
    }
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
  // In-flight flag for the unified Providers Save button so
  // the spinner / "Saving…" label fires the same way as the
  // per-section Save buttons did pre-fix.
  hostStatsSaving: false,

  async saveHostStats() {
    this.hostStatsSaving = true;
    try {
      await this._saveHostStatsImpl();
    } finally {
      this.hostStatsSaving = false;
    }
  },
  canSaveHttpProbeSection() {
    // No Test-before-Save gate at this section: per-host URLs live
    // on the Admin → Hosts row's `http_probe.urls` field, and the
    // Test button here is a one-shot diagnostic against an operator-
    // typed URL — not a section-level config validation. Save
    // unlocks purely on dirty state; the operator can flip the
    // master toggle on/off and commit without proving a URL works.
    return true;
  },
  httpProbeTestUrl: '',

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
    if (this.debugSaving) {
      return;
    }
    this.debugSaving = true;
    try {
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
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

  _startLogPoll() {
    this._stopLogPoll();
    // 2s poll — fast enough for "watch a deploy" UX, slow enough to
    // not hammer the admin API when nothing's happening.
    this.logPollHandle = setInterval(() => {
      if (this.logAuto) {
        this.loadLogs(false);
      }
    }, 2000);
  },

  _stopLogPoll() {
    if (this.logPollHandle) {
      clearInterval(this.logPollHandle);
      this.logPollHandle = null;
    }
  },
  // True when every severity level is on — used by the ALL pill's
  // is-active state. False if any level is off.
  logAllSeverityOn() {
    for (const k of this.logSeverityLevels) {
      if (!this.logSeverityFilter[k]) {
        return false;
      }
    }
    return true;
  },
  async viewLogFile(name) {
    this.logSelectedFile = name;
    await this._fetchLogFileBody();
    // Restart the auto-tail poll for the newly-selected file.
    if (this._logFileTimer) {
      clearInterval(this._logFileTimer);
    }
    this._logFileTimer = setInterval(() => {
      if (this.logsSubTab !== 'files' || !this.logSelectedFile || !this.logFileAutoTail) {
        return;
      }
      this._fetchLogFileBody();
    }, 5000);
  },
  // Change the tail window (lines shown) + immediately re-fetch. "All"
  // (0) reads the whole file, which makes a 5s live-tail re-read of the
  // entire file pointless + heavy — so switching to All turns auto-tail
  // off. The operator can re-enable it after narrowing back to a window.
  setLogFileTail(n) {
    this.logFileTailLines = Number(n) || 0;
    if (this.logFileTailLines === 0) {
      this.logFileAutoTail = false;
    }
    this._fetchLogFileBody();
  },
  async _fetchLogFileBody() {
    if (!this.logSelectedFile) {
      this.logFileBody = '';
      return;
    }
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
  // `2026-04-27T12:34:56Z LEVEL  message body`
  // Where LEVEL is one of ERROR / WARN / SUCCESS / INFO. Any line
  // that doesn't match the regex falls through as raw INFO so a
  // pre-existing file in some other format still renders, just
  // without the timestamp tint.
  parsedLogFileLines() {
    const body = this.logFileBody || '';
    if (!body) {
      return [];
    }
    const lines = body.split('\n');
    // ISO ts + 1 or more spaces + LEVEL token + space + rest.
    // Named capture groups (`?<name>`) — JSHint's E016 rule predates
    // ES2018 and falsely flags them as invalid regex; the per-line
    // suppression below silences just that diagnostic while keeping
    // the rest of JSHint live for this declaration. The regex itself
    // is valid in every browser since Chrome 64 / Firefox 78.
    const RX = /^(?<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(?<level>ERROR|WARN|SUCCESS|INFO)\s+(?<text>.*)$/; // jshint ignore:line
    const out = [];
    for (const raw of lines) {
      if (!raw) {
        continue;
      }
      const m = RX.exec(raw);
      if (m) {
        const epoch = Date.parse(m.groups.ts) / 1000;
        out.push({
          ts: Number.isFinite(epoch) ? epoch : 0,
          stream: 'file',
          level: m.groups.level.toLowerCase(),  // matches `logSeverity()` output
          text: m.groups.text,
        });
      } else {
        // Non-conforming line — render as INFO with the raw text.
        out.push({ts: 0, stream: 'file', level: 'info', text: raw});
      }
    }
    return out;
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
    const activePats = this._activeLogPatterns();
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
      if (!q) {
        return true;
      }
      const text = (l && (l.text || l.body || l.msg || '')) + '';
      return text.toLowerCase().includes(q);
    };
    const filterByPattern = (l) => {
      if (!activePats.length) {
        return true;
      }
      return this._lineMatchesAnyPattern(l, activePats);
    };
    if (allOn && !activePats.length) {
      return q ? lines.filter(filterByText) : lines;
    }
    return lines.filter(l => (allOn || !!sev[this.logSeverityFor(l)]) && filterByText(l) && filterByPattern(l));
  },

  _b64uEncode(buf) {
    const b = new Uint8Array(buf);
    let s = '';
    for (let i = 0; i < b.length; i++) {
      s += String.fromCharCode(b[i]);
    }
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  },

  _b64uDecode(s) {
    let val = (s || '').replace(/-/g, '+').replace(/_/g, '/');
    while (val.length % 4) {
      val += '=';
    }
    const bin = atob(val);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) {
      out[i] = bin.charCodeAt(i);
    }
    return out.buffer;
  },

  relativeWhen(epochSeconds) {
    if (!epochSeconds) {
      return '';
    }
    const now = Date.now() / 1000;
    const diff = Math.max(0, now - Number(epochSeconds));
    if (diff < 60) {
      const n = Math.round(diff);
      return this.t('hosts_extra.metrics.last_updated_seconds', {count: n}) || `${n}s ago`;
    }
    if (diff < 3600) {
      const n = Math.round(diff / 60);
      return this.t('hosts_extra.metrics.last_updated_minutes', {count: n}) || `${n}m ago`;
    }
    if (diff < 86400) {
      const n = Math.round(diff / 3600);
      return this.t('hosts_extra.metrics.last_updated_hours', {count: n}) || `${n}h ago`;
    }
    // Older than 24h → fall back to a date-only render. Goes
    // through `fmtDateOnly` so the operator's chosen Formats
    // preference applies here too.
    return this.fmtDateOnly(Number(epochSeconds));
  },

  copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        () => this.showToast(this.t('toasts.copied')),
        () => this.showToast(this.t('toasts.copy_failed_manual'), 'error'),
      );
    }
  },
  // Snapshot of the topbar-widget prefs — same baseline+snapshot
  // pattern as admin-tab dirty flags. Toggling the show-clock
  // / show-weather checkboxes or editing the lat/lon/label fields
  // only marks the form dirty; nothing persists until Save is
  // clicked. `headerPrefsDirty()` re-evaluates each render via
  // Alpine reactivity. Re-baselined after every successful save.
  _headerPrefsBaseline: '',
  _headerPrefsSnapshot() {
    return JSON.stringify({
      clk: !!this.headerClockEnabled,
      wth: !!this.headerWeatherEnabled,
      lat: this.headerWeatherLat == null ? '' : String(this.headerWeatherLat),
      lon: this.headerWeatherLon == null ? '' : String(this.headerWeatherLon),
      label: this.headerWeatherLabel || '',
      unit: (this.headerWeatherUnit === 'f') ? 'f' : 'c',
      // AI launcher visibility participates in the dirty/save flow
      // so toggling the checkbox marks the form dirty (amber ring +
      // "Unsaved" pulse-dot) and reverting it back to the original
      // un-marks it. Save is what commits the change to
      // ui_prefs.ai_sidebar_launcher_hidden — auto-save was the
      // pre-fix behaviour and didn't fit the rest of the form's
      // explicit-save model.
      aiLauncherHidden: !!this.aiSidebarLauncherHiddenDraft,
      // Datetime format string. Trimmed so trailing whitespace
      // doesn't make the form look dirty when nothing meaningful
      // changed. Empty draft + empty baseline both serialise as ''
      // → match → not dirty (intentional: no value vs cleared
      // value are equivalent for this preference).
      dtFmt: (this.datetimeFormatDraft || '').trim(),
    });
  },
  headerPrefsDirty() {
    return this._headerPrefsBaseline !== this._headerPrefsSnapshot();
  },
  saveHeaderPrefs() {
    try {
      localStorage.setItem('headerClockEnabled', String(!!this.headerClockEnabled));
      localStorage.setItem('headerWeatherEnabled', String(!!this.headerWeatherEnabled));
      localStorage.setItem('headerWeatherLat', this.headerWeatherLat == null ? '' : String(this.headerWeatherLat));
      localStorage.setItem('headerWeatherLon', this.headerWeatherLon == null ? '' : String(this.headerWeatherLon));
      localStorage.setItem('headerWeatherLabel', this.headerWeatherLabel || '');
      localStorage.setItem('headerWeatherUnit', (this.headerWeatherUnit === 'f') ? 'f' : 'c');
    } catch (_) {
    }
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
      // Datetime format goes through the same PATCH so cross-device
      // sync stays the source of truth. Empty / whitespace draft is
      // serialised as null which the backend treats as "clear the
      // override; revert to the SPA default at next render".
      const dtFmtTrimmed = (this.datetimeFormatDraft || '').trim();
      fetch('/api/me/ui-prefs', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          prefs: {
            headerClockEnabled: !!this.headerClockEnabled,
            headerWeatherEnabled: !!this.headerWeatherEnabled,
            headerWeatherLat: this.headerWeatherLat == null ? null : Number(this.headerWeatherLat),
            headerWeatherLon: this.headerWeatherLon == null ? null : Number(this.headerWeatherLon),
            headerWeatherLabel: this.headerWeatherLabel || null,
            headerWeatherUnit: (this.headerWeatherUnit === 'f') ? 'f' : 'c',
            datetime_format: dtFmtTrimmed || null,
          }
        }),
      }).catch(() => {/* silent — localStorage still has it */
      });
      // Update the live `me.ui_prefs.datetime_format` immediately so
      // every `:value="fmtDate(...)"` binding re-renders with the
      // new format on this very save (the PATCH above is fire-and-
      // forget; we don't wait for it). Empty draft → unset so the
      // SPA falls back to DEFAULT_DATETIME_FORMAT.
      if (this.me) {
        if (!this.me.ui_prefs) {
          this.me.ui_prefs = {};
        }
        this.me.ui_prefs.datetime_format = dtFmtTrimmed || '';
      }
    } catch (_) {
    }
    // Re-fetch with the new settings immediately rather than waiting
    // for the 10-min tick. Also flushes weather to null when disabled.
    this.loadHeaderWeather();
    // Commit the AI-launcher draft (Settings → Profile checkbox)
    // through its existing PATCH helper. The draft is what the
    // checkbox binds to via x-model; save flips the live value
    // and persists to `ui_prefs.ai_sidebar_launcher_hidden`. Done
    // AFTER the snapshot re-baseline above so the dirty-flag
    // clears cleanly post-save.
    if (this.aiSidebarLauncherHiddenDraft !== this.aiSidebarLauncherHidden) {
      try {
        this.setAiSidebarLauncherHidden(!!this.aiSidebarLauncherHiddenDraft);
      } catch (_) {
      }
    }
    // Toast confirmation — per-browser preferences auto-save on
    // change, but operators coming from the per-user Profile section
    // expect a visual "saved" signal.
    if (this.showToast) {
      this.showToast(this.t('toasts_extra.topbar_saved'), 'success');
    }
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
      try {
        localStorage.setItem('headerClockEnabled', String(p.headerClockEnabled));
      } catch (_) {
      }
    }
    if (typeof p.headerWeatherEnabled === 'boolean') {
      this.headerWeatherEnabled = p.headerWeatherEnabled;
      try {
        localStorage.setItem('headerWeatherEnabled', String(p.headerWeatherEnabled));
      } catch (_) {
      }
    }
    if (p.headerWeatherLat != null) {
      this.headerWeatherLat = Number(p.headerWeatherLat);
      try {
        localStorage.setItem('headerWeatherLat', String(p.headerWeatherLat));
      } catch (_) {
      }
    }
    if (p.headerWeatherLon != null) {
      this.headerWeatherLon = Number(p.headerWeatherLon);
      try {
        localStorage.setItem('headerWeatherLon', String(p.headerWeatherLon));
      } catch (_) {
      }
    }
    if (typeof p.headerWeatherLabel === 'string') {
      this.headerWeatherLabel = p.headerWeatherLabel;
      try {
        localStorage.setItem('headerWeatherLabel', p.headerWeatherLabel);
      } catch (_) {
      }
    }
    if (typeof p.headerWeatherUnit === 'string') {
      const u = p.headerWeatherUnit.toLowerCase() === 'f' ? 'f' : 'c';
      this.headerWeatherUnit = u;
      try {
        localStorage.setItem('headerWeatherUnit', u);
      } catch (_) {
      }
    }
    // Datetime format. Server is the only source of truth for this
    // preference (no localStorage cache — the SPA reads via the
    // already-hydrated `this.me.ui_prefs.datetime_format`). The draft
    // mirrors the live value so the form input renders with the
    // current setting and `headerPrefsDirty()` is false on first
    // render.
    this.datetimeFormatDraft = (typeof p.datetime_format === 'string') ? p.datetime_format : '';
  },
  // °C → operator's preferred unit. Returns the formatted string with
  // the unit suffix attached (e.g. `21.3°C` or `70°F`). Backend
  // `/api/weather` ALWAYS returns Celsius; the SPA converts at the
  // render boundary. `decimals` lets the caller pick precision —
  // topbar chip uses 1 (matches the °C-only pre-fix display);
  // forecast min/max uses 0 (the pre-fix Math.round path).
  formatTempPref(c, decimals = 1) {
    if (c == null || !Number.isFinite(+c)) {
      return '';
    }
    const f = (+c) * 9 / 5 + 32;
    const v = (this.headerWeatherUnit === 'f') ? f : (+c);
    const factor = Math.pow(10, Math.max(0, Math.trunc(decimals || 0)));
    return (Math.round(v * factor) / factor) + (this.headerWeatherUnit === 'f' ? '°F' : '°C');
  },
  // Convert a Celsius value to the operator's preferred unit and
  // return the bare number (no suffix). Used by AI palette context
  // where the JSON payload carries the unit separately.
  convertTempPref(c) {
    if (c == null || !Number.isFinite(+c)) {
      return null;
    }
    if (this.headerWeatherUnit === 'f') {
      return Math.round(((+c) * 9 / 5 + 32) * 10) / 10;
    }
    return Math.round((+c) * 10) / 10;
  },
  // ---- Shared "is this loader running right now" registry --------
  // Used by admin reload buttons to drive the spinning-icon state.
  // Keyed on a free-form string ('users' / 'sessions' / 'tokens' /
  // 'schedules' / 'schedule_queue' / 'backups' / 'logs' /
  // 'log_files' / 'config_backup_saved'). The button binds to
  // `_loadBusy.<key>` for both the spinner class and the
  // `:disabled` attribute; the helper guards against double-fires
  // so a fast-clicking user can't start a second concurrent fetch.
  // The wrapped fn can be sync OR async — `await fn()` works either
  // way thanks to the implicit-promise wrap on a non-thenable value.
  // Pre-declared keys ensure Alpine's reactive Proxy registers each
  // property as a stable tracked path from mount. Adding properties
  // later via `_loadBusy[key] = true` works in most cases but has
  // edge cases (especially when Alpine evaluates the binding before
  // the first write, then races against subsequent writes) where the
  // initial DOM evaluation sticks even after the data clears. Listing
  // every key with `false` up-front side-steps the whole class.
  _loadBusy: {
    users: false, sessions: false, tokens: false,
    schedules: false, schedule_queue: false,
    backups: false, config_backup_saved: false,
    logs: false, log_files: false,
    stats_overview: false, stats_database: false, stats_samples: false,
    stats_incidents: false, stats_network: false, stats_ai_cost: false,
    history: false, hosts_config: false,
  },
  // Watchdog timer per busy-key — see `_runWithBusy` below. Cleared
  // when the inner fn resolves naturally; otherwise the timer force-
  // clears the busy flag after `_LOAD_BUSY_MAX_MS` so a hung Promise
  // can't leave the reload button visually stuck forever.
  _loadBusyWd: {},
  // Watchdog timers for the SSE-pill "refreshing" flags
  // (`cacheRefreshing` / `hubProbing` / `statsRefreshing`). The backend
  // mirrors background-task state into these via response payload
  // fields. If a backend task stalls (or SSE drops silently in Live
  // mode so no fresh response lands to clear them), the SSE pill
  // would stay in `--refreshing` state with its fast-spin animation
  // forever. Watchdog clears the flag after `_LOAD_BUSY_MAX_MS`.
  _refreshingWd: {},
  // Wrapper that mirrors any backend-derived boolean flag into a
  // SPA-side reactive property AND arms a watchdog so the flag can't
  // stay truthy past `_LOAD_BUSY_MAX_MS`. Use for any flag whose
  // truthiness drives a long-running animation (spinner, pulse).
  _setRefreshingFlag(key, value) {
    this[key] = !!value;
    try {
      clearTimeout(this._refreshingWd[key]);
    } catch (_) {
    }
    if (this[key]) {
      this._refreshingWd[key] = setTimeout(() => {
        if (this[key]) {
          this[key] = false;
        }
      }, this._LOAD_BUSY_MAX_MS || 30000);
    } else {
      delete this._refreshingWd[key];
    }
  },
  // Watchdog cap (ms). Operator-tunable via `tuning_load_busy_max_seconds`
  // (Admin → Config). Defaults to 30000 ms (matches the published
  // `/api/hosts/one/{id}` probe budget); range 5..600 seconds.
  // The 30000 literal here is a defence-in-depth fallback for the
  // brief window before `/api/me` hydrates — every real consumer
  // reads through the getter so a save in Admin → Config lands on
  // the next round-trip.
  get _LOAD_BUSY_MAX_MS() {
    const v = this.me && this.me.client_config && this.me.client_config.load_busy_max_ms;
    const n = Number(v);
    return Number.isFinite(n) && n >= 5000 ? n : 30000;
  },
  async _runWithBusy(key, fn) {
    if (!key || typeof fn !== 'function') {
      return;
    }
    if (this._loadBusy[key]) {
      return;
    }
    this._loadBusy[key] = true;
    // Watchdog — if the inner fn hangs (network blip, dead probe,
    // slow listing) clear the busy flag after _LOAD_BUSY_MAX_MS so
    // the reload button doesn't stay disabled across the session.
    // The fn keeps running in the background; when it finally
    // resolves, finally{} would re-clear (already false — no-op).
    try {
      clearTimeout(this._loadBusyWd[key]);
    } catch (_) {
    }
    this._loadBusyWd[key] = setTimeout(() => {
      if (this._loadBusy[key]) {
        this._loadBusy[key] = false;
      }
    }, this._LOAD_BUSY_MAX_MS);
    try {
      await fn();
    } finally {
      this._loadBusy[key] = false;
      try {
        clearTimeout(this._loadBusyWd[key]);
      } catch (_) {
      }
      delete this._loadBusyWd[key];
    }
  },
  async loadVersion() {
    try {
      const r = await fetch('/api/version');
      if (!r.ok) {
        return;
      }
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
    } catch (_) {
    }
  },
  // Start a 60s poll of /api/version so a deploy that lands while
  // the operator has the tab open triggers a hard-refresh banner.
  // Idempotent — safe to call from init() even on hot-reload.
  startVersionWatcher() {
    if (this._versionTimer) {
      return;
    }
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
    if (this.statsInterval <= 0) {
      // Diagnostic — without this, "no graphs anywhere" looks like
      // a backend bug when the actual cause is that the operator's
      // last session left the stats-interval picker on "Off". One
      // shot still fires below so the operator sees CURRENT data;
      // future ticks are intentionally suppressed.
      // Logged ONCE per session — pre-fix every pollStats invocation
      // (which fires on Live-mode SSE reconnect, view nav, etc.) re-
      // emitted the warning, drowning out actionable console output.
      // The `_pollStatsOffLogged` latch makes it appear once and
      // stay quiet for the rest of the session.
      if (!this._pollStatsOffLogged) {
        console.warn('[stats] pollStats: stats polling is OFF (statsInterval=0). Re-enable it from the topbar interval picker. Firing one diagnostic loadStats() anyway so /api/stats response is logged once.');
        this._pollStatsOffLogged = true;
      }
      try {
        this.loadStats();
      } catch (_) { /* never crash init */
      }
      return;
    }
    const tick = async () => {
      // Bracket the request so the pill flashes green for the
      // exact duration of the /api/stats round-trip.
      this._pollStart();
      try {
        await this.loadStats();
      } finally {
        this._pollEnd();
      }
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
  pollSparks() {
    if (this._sparksTimer) {
      clearInterval(this._sparksTimer);
    }
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
    if (!rows || rows.length < 2) {
      return '';
    }
    // memo — see _sparkPointsMemo. `undefined` = not cached (the
    // builder returns '' or a points string, never undefined).
    let _perArr = _sparkPointsMemo.get(rows);
    if (!_perArr) {
      _perArr = new Map();
      _sparkPointsMemo.set(rows, _perArr);
    }
    const _mk = key + '|' + rows.length;
    const _hit = _perArr.get(_mk);
    if (_hit !== undefined) {
      return _hit;
    }
    const _memo = (v) => {
      _perArr.set(_mk, v);
      return v;
    };
    const W = 60, H = 10;
    const vals = rows.map(r => {
      if (key === 'cpu') {
        return r.cpu || 0;
      }
      if (key === 'mem') {
        return r.mem_limit ? (r.mem_used / r.mem_limit) * 100 : 0;
      }
      // 'disk' → per-item image-disk footprint (size_root, bytes).
      // Snapshot of the image's bytes-on-disk at each sampler tick;
      // sparkline shows image-size drift over time (catches image
      // bloat from rolling-tag updates). Auto-rescales to the
      // window's lo/hi via the shared min-max normalisation below
      // so a flat-ish image renders centred rather than pinned to
      // the bottom edge.
      if (key === 'disk') {
        return r.size_root || 0;
      }
      return 0;
    });
    let lo = Infinity, hi = -Infinity;
    for (const v of vals) {
      if (v < lo) {
        lo = v;
      }
      if (v > hi) {
        hi = v;
      }
    }
    // Empty series → bail (no samples to plot). Disk-specific: when
    // EVERY sample reports size_root=0 we treat it as "no data" so
    // pre-migration deploys (where size_root wasn't persisted yet)
    // hide cleanly instead of rendering an invisible flat-zero
    // hairline. For CPU / mem an all-zero series is meaningful —
    // an idle container legitimately reports cpu_percent=0 and
    // mem_usage may stay flat under the noise floor — so we render
    // the truthful flat line at the baseline rather than dropping
    // to the "Collecting data" hint (the data IS being collected,
    // it's just all zeros).
    if (!Number.isFinite(lo)) {
      return _memo('');
    }
    if (key === 'disk' && hi <= 0) {
      return _memo('');
    }
    // Keep the sparkline visually centred when the signal is flat —
    // map the flat value to the MIDPOINT of the box (not the
    // bottom edge) so an idle 0% CPU renders a visible line
    // instead of being half-clipped by the viewBox boundary. Same
    // fix `nodeSparkPoints` carries.
    if (hi - lo < 0.5) {
      const mid = (lo + hi) / 2;
      lo = mid - 1;
      hi = mid + 1;
    }
    const step = W / (vals.length - 1);
    return _memo(vals.map((v, i) => {
      const x = (i * step).toFixed(1);
      const y = (H - ((v - lo) / (hi - lo)) * H).toFixed(1);
      return `${x},${y}`;
    }).join(' '));
  },
  sparkClass(item, key) {
    // Colour follows the CURRENT reading (not the sparkline max) so the
    // line visually agrees with the stat-bar it sits beside.
    const s = this.statsFor(item);
    if (!s || !s.has_stats) {
      return 'muted';
    }
    const v = key === 'cpu' ? s.cpu_percent : this.memPercent(item);
    return this.barLevel(v);
  },

  // Generic in-place reconcile helper. Updates `target` to
  // match `incoming` row-by-row, keyed on the named field
  // (default `id`; stacks pass `'name'` since they don't carry an
  // id). Operations:
  // - existing rows: copy every field from `incoming[i]` onto the
  //   proxied entry (Alpine tracks each assignment individually so
  //   the row's DOM stays mounted),
  // - new rows: push the full incoming dict at the end,
  // - gone rows: splice from the tail.
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
      if (k != null) {
        byKey.set(k, row);
      }
    }
    for (const inc of incoming) {
      const k = keyOf(inc);
      if (k == null) {
        continue;
      }
      const existing = byKey.get(k);
      if (existing) {
        for (const f of Object.keys(inc)) {
          existing[f] = inc[f];
        }
      } else {
        target.push({...inc});
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
    while (target.length > ordered.length) {
      target.pop();
    }
  },

  async refresh(force = false) {
    this.loading = true;
    // Watchdog cap — mirror the `_runWithBusy` pattern so a hung fetch
    // (server not responding, network blip) can't leave the topbar
    // spinner stuck across the session. Cleared on natural resolve.
    const _wd = setTimeout(() => {
        if (this.loading) {
          this.loading = false;
        }
      },
      this._LOAD_BUSY_MAX_MS || 30000);
    try {
      const r = await fetch('/api/items' + (force ? '?force=true' : ''));
      if (!r.ok) {
        // Build a concise error message — NEVER include the raw
        // response body (a 502/504 proxy page is multi-kilobyte
        // HTML that, dumped into a toast, makes a wall of `<html>
        // ...openresty...` text). Prefer JSON `detail` from
        // FastAPI; fall back to the status text; otherwise just
        // the status code. Inline this rather than helper-ing
        // because each fetch site has slightly different gates
        // (some need r.text in the OK path).
        let _msg = `HTTP ${r.status}`;
        try {
          const _ct = r.headers.get('content-type') || '';
          if (_ct.includes('application/json')) {
            const _j = await r.json();
            if (_j && (_j.detail || _j.error || _j.message)) {
              _msg += ': ' + String(_j.detail || _j.error || _j.message).slice(0, 200);
            }
          } else if (r.statusText) {
            _msg += ` ${r.statusText}`;
          }
        } catch (_) { /* leave bare HTTP nnn */
        }
        throw new Error(_msg);
      }
      const d = await r.json();
      // in-place reconcile for items + stacks instead of
      // wholesale array reassignment. Keeps Alpine from tearing
      // down each row's checkbox state, <details> open/closed
      // state, and inline-style nodes on every poll. Items are
      // keyed by `id` (the default); stacks have no id field so
      // we key on `name` (the operator-facing stack name is
      // unique within a Swarm).
      // Filter just-removed raw_ids out of the incoming items so a
      // polled refresh that races ahead of the backend's cache
      // invalidation can't re-introduce them. Suppression auto-
      // clears 30s after the bulk-remove (see `_bulkRemoveItems`).
      let incomingItems = d.items || [];
      if (this._recentlyRemovedIds && this._recentlyRemovedIds.size) {
        const supp = this._recentlyRemovedIds;
        incomingItems = incomingItems.filter(it => !supp.has(it && it.raw_id));
      }
      this._reconcileById(this.items, incomingItems);
      this._reconcileById(this.stacks, d.stacks || [], 'name');
      this.nodes = d.nodes || {};
      // Per-node capacity + uptime proxy — see logic/gather.py's nodes_info.
      // Drives the Nodes view's normalized CPU/mem bars.
      this.nodesInfo = d.nodes_info || {};
      // drives the "Portainer not configured" empty-state hint.
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
      this._setRefreshingFlag('cacheRefreshing', d.cache_refreshing);
      // Fire stats alongside a forced refresh UNLESS the cadence
      // picker is set to Off. Pre-fix this gated on
      // `statsInterval > 0`, but `setRefreshInterval` remaps Live
      // (-1) to legacy `statsInterval = 0`, so a forced refresh in
      // Live mode skipped /api/stats entirely — bars stayed on
      // seeded stale data forever. Now any non-Off mode loads
      // stats; only `refreshInterval === 0` (explicit Off) skips.
      if (force && this.refreshInterval !== 0) {
        this.loadStats(true);
      }
    } catch (e) {
      try {
        this.showToast(this.t('toasts.load_failed', {error: e.message}), 'error');
      } catch (_) {
      }
    } finally {
      this.loading = false;
      try {
        clearTimeout(_wd);
      } catch (_) {
      }
    }
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
        ping_enabled: !!(d.ping && d.ping.enabled),
        ping_default_port: (d.ping && Number.isFinite(d.ping.default_port)) ? d.ping.default_port : 443,
        ping_use_icmp: !!(d.ping && d.ping.use_icmp),
        ping_has_icmp_support: !!(d.ping && d.ping.has_icmp_support),
        // Port-scan provider — on-demand TCP scanner. `port_scan_enabled`
        // is the master toggle; defaults cascade into per-host
        // overrides on `hosts_config[].port_scan`.
        port_scan_enabled: !!(d.port_scan && d.port_scan.enabled),
        port_scan_default_ports: (d.port_scan && d.port_scan.default_ports) || '',
        // Port-scan UDP companion (Stage 2). UDP runs under the
        // master `port_scan_enabled` toggle (operator-flagged
        // 2026-05-10 to remove the separate flag). `port_scan_udp_enabled`
        // hydrate retained as `true` so any leftover Alpine binding
        // sees a truthy value during the deprecation window — value
        // is otherwise unused.
        port_scan_udp_enabled: true,
        port_scan_udp_default_ports: (d.port_scan && d.port_scan.udp_default_ports) || '',
        port_scan_default_timeout: (d.port_scan && Number.isFinite(d.port_scan.default_timeout)) ? d.port_scan.default_timeout : 2,
        port_scan_default_concurrency: (d.port_scan && Number.isFinite(d.port_scan.default_concurrency)) ? d.port_scan.default_concurrency : 32,
        // SNMP provider. v3 secret keys flow as `_set` flags
        // (write-only contract); community / version / port / aliases
        // round-trip in the clear. `has_snmp_support` reflects whether
        // pysnmp is importable on the server; SPA uses it to disable
        // the ICMP-style "missing dep" hint when the package isn't
        // installed.
        snmp_default_community: (d.snmp && d.snmp.default_community) || 'public',
        snmp_default_version: (d.snmp && d.snmp.default_version) || 'v2c',
        snmp_default_port: (d.snmp && Number.isFinite(d.snmp.default_port)) ? d.snmp.default_port : 161,
        snmp_v3_user: (d.snmp && d.snmp.v3_user) || '',
        snmp_v3_auth_key: '',
        snmp_v3_auth_key_set: !!(d.snmp && d.snmp.v3_auth_key_set),
        snmp_v3_priv_key: '',
        snmp_v3_priv_key_set: !!(d.snmp && d.snmp.v3_priv_key_set),
        // Aliases textarea — same JSON-string pattern as
        // node_exporter_overrides_json so the existing dirty-tracker
        // + JSON-parse-on-save path applies.
        snmp_aliases: (d.snmp && d.snmp.aliases) || {},
        snmp_aliases_json: JSON.stringify((d.snmp && d.snmp.aliases) || {}, null, 2),
        snmp_has_snmp_support: !!(d.snmp && d.snmp.has_snmp_support),
        // actual ImportError text from logic/snmp.py's module-
        // level pysnmp import block. Empty when pysnmp imported
        // cleanly. Surfaced inline in the SPA's "package missing"
        // hint so operators see the ROOT CAUSE without grepping
        // server logs.
        snmp_import_error: (d.snmp && d.snmp.import_error) || '',
        // HTTP / TLS / DNS probe — seventh host-stats provider.
        // Master enable + alias CSV. Per-host overrides land on
        // `hosts_config[].http_probe` and ride the curated-config
        // round-trip.
        http_probe_enabled: !!(d.http_probe && d.http_probe.enabled),
        http_probe_aliases: (d.http_probe && d.http_probe.aliases) || '',
        // Service probe — eighth host-stats provider. Master toggle
        // surfaced at the top level of `/api/settings` (not nested
        // under a `service_probe` sub-object like http_probe is).
        service_probe_enabled: !!d.service_probe_enabled,
        // per-provider chip colour overrides. Empty string
        // means "use the SPA default" (see providerColor() helper).
        provider_color_beszel: d.provider_color_beszel || '',
        provider_color_pulse: d.provider_color_pulse || '',
        provider_color_node_exporter: d.provider_color_node_exporter || '',
        provider_color_webmin: d.provider_color_webmin || '',
        provider_color_ping: d.provider_color_ping || '',
        provider_color_snmp: d.provider_color_snmp || '',
        provider_color_http_probe: d.provider_color_http_probe || '',
        provider_color_service_probe: d.provider_color_service_probe || '',
        // Scheduler — IANA zone. Blank = container-local (legacy).
        scheduler_timezone: d.scheduler_timezone || '',
        // Open-Meteo upstream (weather widget). DEPRECATED — kept
        // for legacy round-trip only; UI consumer is the WeatherAPI
        // block below.
        open_meteo_url: d.open_meteo_url || '',
        // Weather — dual-provider dispatch (Open-Meteo OR
        // WeatherAPI.com). Master toggle gates the topbar widget +
        // lifespan sampler + AI palette context. The `weather_provider`
        // selector picks between providers; moon-widget gate +
        // moon-AI handling auto-disable when "open-meteo" is active
        // (no moon data on that provider). API key is write-only:
        // SPA sees only the `api_key_set` boolean via `settings.weather`.
        weather_enabled: !!(d.weather && d.weather.enabled),
        weather_provider: ((d.weather && d.weather.provider) === 'weatherapi')
          ? 'weatherapi' : 'open-meteo',
        weather_api_base_url: (d.weather && d.weather.api_base_url) || '',
        weather_default_label: (d.weather && d.weather.default_label) || '',
        weather_default_lat: (d.weather && d.weather.default_lat) || '',
        weather_default_lon: (d.weather && d.weather.default_lon) || '',
        weather: d.weather || {
          enabled: false, provider: 'open-meteo', supports_moon: false,
          api_base_url: '', api_key_set: false,
          default_label: '', default_lat: '', default_lon: '',
        },
        // Per-service master switches. Default true so legacy
        // deploys keep working before the operator interacts with
        // the toggles.
        apprise_enabled: d.apprise_enabled !== false,
        open_meteo_enabled: d.open_meteo_enabled !== false,
        portainer_enabled: d.portainer_enabled !== false,
        ssh_enabled: d.ssh_enabled !== false,
        asset_inventory_enabled: d.asset_inventory_enabled !== false,
        // Admin → Hosts toggle that controls visibility of the
        // host-drawer debug-data panel. Default true keeps the
        // legacy admin behaviour for fresh installs / pre-toggle
        // databases. Persisted via /api/settings on every flip.
        debug_panel_enabled: d.debug_panel_enabled !== false,
        // AI integration master toggle + active provider — populated
        // here so the Admin → AI tab's master switch + active-provider
        // selector reflect the saved state on first render. Per-provider
        // detail (model / base_url / api_key_set) is hydrated into
        // `this.aiForm` separately by hydrateAiFromSettings(d).
        ai_enabled: !!(d.ai && d.ai.enabled),
        ai_active_provider: (d.ai && d.ai.active_provider) || 'claude',
        ai_max_tokens: (d.ai && Number.isFinite(+d.ai.max_tokens) && +d.ai.max_tokens > 0) ? +d.ai.max_tokens : 1024,
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
      // Telegram-specific fields. Plain settings (chat_id / thread_id
      // / verify_tls) hydrate directly; the bot token follows the
      // write-only secret contract — `telegram_bot_token` stays
      // empty in the form, the `_set` flag drives the "saved" hint
      // + placeholder copy. Without this hydration the snapshot's
      // chat_id / thread_id would read undefined on first paint and
      // any operator edit would flip dirty against a phantom
      // baseline (and the operator's actual saved values wouldn't
      // appear in the input boxes).
      this.settings.telegram_bot_token = '';
      this.settings.telegram_bot_token_set = !!d.telegram_bot_token_set;
      this.settings.telegram_chat_id = (d.telegram_chat_id || '').toString();
      this.settings.telegram_thread_id = (d.telegram_thread_id || '').toString();
      this.settings.telegram_verify_tls = (d.telegram_verify_tls !== false);
      // Operator-tunable Bot API base URL — blank = upstream default.
      this.settings.telegram_api_base = (d.telegram_api_base || '').toString();
      // Phase 2 — listener config.
      this.settings.telegram_listener_enabled = !!d.telegram_listener_enabled;
      this.settings.telegram_allow_destructive = !!d.telegram_allow_destructive;
      this.settings.telegram_authorized_user_ids = (d.telegram_authorized_user_ids || '').toString();
      // TOTP / 2FA policy. Hydrate the five fields so the
      // Admin -> Config tab can render the inputs + the existing
      // saveSettings flow can ship the values back.
      this.settings.totp_allowed = (d.totp_allowed !== false);
      this.settings.totp_required_for_admins = !!d.totp_required_for_admins;
      this.settings.totp_required_for_users = !!d.totp_required_for_users;
      this.settings.totp_lockout_max_failures =
        Number.isFinite(d.totp_lockout_max_failures) ? d.totp_lockout_max_failures : 5;
      this.settings.totp_lockout_minutes =
        Number.isFinite(d.totp_lockout_minutes) ? d.totp_lockout_minutes : 15;
      // Passkey master toggle. Hydrated alongside the TOTP
      // group because both Save through the same totpPolicySnapshot
      // dirty tracker. Pre-fix the checkbox bound to a never-set
      // `settings.passkeys_allowed`, so on every page load it
      // appeared unchecked even when the DB value was true. Default
      // when the backend omits the key matches the backend's own
      // default (`_TOTP_POLICY_DEFAULTS` → True).
      this.settings.passkeys_allowed = (d.passkeys_allowed !== false);
      // Capture baseline for the host-stats dirty indicator.
      // Passwords/tokens are always blank in the live form (write-
      // only on the wire) so any typed value flips dirty.
      this._hostStatsBaseline = this._hostStatsSnapshot();
      this.endpointId = d.endpoint_id || 1;

      // --- OIDC panel state ---
      this.oidcStatus = d.oidc || null;
      if (this.oidcStatus) {
        this.oidcForm = {
          enabled: !!this.oidcStatus.enabled,
          issuer_url: this.oidcStatus.issuer_url || '',
          client_id: this.oidcStatus.client_id || '',
          client_secret: '',  // write-only — never prefill
          redirect_uri: this.oidcStatus.redirect_uri || this.oidcStatus.redirect_uri_default || '',
          scopes: this.oidcStatus.scopes || 'openid email profile groups',
          admin_group: this.oidcStatus.admin_group || 'omnigrid-admins',
          // Default ON when the backend hasn't surfaced it yet (first load
          // after the migration); otherwise reflect whatever's persisted.
          verify_tls: this.oidcStatus.verify_tls !== false,
          // case-insensitive admin-group claim match.
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
          url: this.portainerStatus.url || '',
          endpoint_id: this.portainerStatus.endpoint_id || 1,
          verify_tls: !!this.portainerStatus.verify_tls,
          api_key: '',  // write-only — never prefill
        };
      }
      // Capture portainer-public-url baseline for the dirty getter
      // (separate from portainerForm because the public URL lives on
      // the broader `settings` object, not the form).
      this._portainerPublicBaseline = (this.settings || {}).portainer_public_url || '';
      // Capture all 5 unified-pattern baselines AFTER the form/settings
      // are fully populated. Subsequent edits compare against these.
      this._appriseBaseline = this._appriseSnapshot();
      // Split-Save baselines — providers + per-event have their
      // own dirty trackers + Save handlers so functionality stays
      // separated (see saveProviders / savePerEvent).
      try {
        this._providersBaseline = this._providersSnapshot();
        this._perEventBaseline = this._perEventSnapshot();
      } catch (_) {
      }
      // Optimistic Test-pass stamp on load. Operator-flagged: after a
      // page reload the per-channel Save was disabled forever because
      // `_<medium>LastPassedTest` resets to '' on every refresh — so
      // flipping ANY field (even non-test-relevant ones like
      // `telegram_allow_destructive`) couldn't unlock Save until the
      // operator ran the per-channel Test button again. Since the
      // settings just loaded WERE persisted through a Save (which
      // requires Test to pass), we can reasonably stamp the current
      // test-snapshot as "passed" at hydrate time. The Test gate then
      // only re-fires when a test-RELEVANT field (URL / token / chat
      // ID / verify_tls / enabled) actually changes from the loaded
      // baseline — non-test-relevant edits (allow_destructive,
      // listener_enabled, authorized_user_ids, etc.) Save freely.
      try {
        this._appriseLastPassedTest = this._appriseTestSnapshot();
        this._telegramLastPassedTest = this._telegramTestSnapshot();
      } catch (_) {
      }
      this._openMeteoBaseline = this._openMeteoSnapshot();
      // Weather — capture baseline snapshot AND seed `_weatherLastPassedTest`
      // with the same baseline so an already-saved-and-tested configuration
      // re-hydrates without forcing the operator to re-Test on every page
      // load. Section-owned save in app-topbar.js refreshes both on commit.
      try {
        if (typeof this._weatherSnapshot === 'function') {
          this._weatherBaselineSnapshot = this._weatherSnapshot();
          // Only seed last-passed-test if the master toggle is ON AND
          // an API key is set — otherwise the gate stays unlocked
          // (toggle-off path) until a fresh Test runs.
          if (this.settings && this.settings.weather_enabled
            && this.settings.weather
            && this.settings.weather.api_key_set) {
            this._weatherLastPassedTest = this._weatherBaselineSnapshot;
          }
        }
      } catch (_) {
      }
      this._portainerBaseline = this._portainerSnapshot();
      this._oidcBaseline = this._oidcSnapshot();
      this._debugBaseline = this._debugSnapshot();
      this._totpPolicyBaseline = this._totpPolicySnapshot();
      // AI integration — hydrate the per-provider form state +
      // capture its baseline. Mirrors the pattern above for the
      // other admin-tab forms.
      this.hydrateAiFromSettings(d);

      // --- Admin → SSH panel state ---
      this.hydrateSshSettings(d);

      // --- Host groups ---
      // Server returns a clean list of {name, range_start, range_end,
      // order}. We keep the order-field for round-trip but also sort
      // here so the editor renders in the same order as the Hosts
      // view will. Fresh load resets dirty flag.
      this.hostGroups = Array.isArray(d.host_groups) ? d.host_groups.map(g => ({
        // Stable id minted server-side on first save (fix).
        // Round-trips unchanged so renames preserve the persisted
        // SSH password while a new same-named group can't inherit
        // it. Blank for any row that hasn't been saved yet.
        id: String(g.id || ''),
        name: String(g.name || ''),
        range_start: Number.isFinite(+g.range_start) ? +g.range_start : 0,
        range_end: Number.isFinite(+g.range_end) ? +g.range_end : 0,
        // Optional display-prefix number. Empty string in the form
        // when unset; a positive integer otherwise. Sent through to
        // the server unchanged on save.
        number: (g.number != null && +g.number > 0) ? +g.number : '',
        parent_name: String(g.parent_name || ''),
        ip_range: String(g.ip_range || ''),
        // Per-group SSH overrides — same shape as `hosts_config[].ssh`.
        // Password is write-only (server returns `password_set`
        // flag instead of the value); UI surfaces "set" badge so
        // operators can see whether one's configured without
        // exposing it.
        ssh: {
          user: String((g.ssh && g.ssh.user) || ''),
          port: (g.ssh && g.ssh.port) || '',
          password: '',
          password_set: !!(g.ssh && g.ssh.password_set),
          clear_password: false,
        },
        order: Number.isFinite(+g.order) ? +g.order : 0,
      })) : [];
      this.hostGroupsDirty = false;
      // Bust groupedHosts() cache on every load.
      this.hostGroupsRevision = (this.hostGroupsRevision || 0) + 1;

      // --- Asset inventory ---
      this.assetStatus = d.asset_inventory || null;
      if (this.assetStatus) {
        this.assetForm = {
          auth_mode: (this.assetStatus.auth_mode === 'lifetime_token')
            ? 'lifetime_token' : 'oauth2',
          base_url: this.assetStatus.base_url || '',
          token_url: this.assetStatus.token_url || '',
          client_id: this.assetStatus.client_id || '',
          client_secret: '',  // write-only — never prefill
          scope: this.assetStatus.scope || '',
          lifetime_token: '',  // write-only — never prefill
          service: this.assetStatus.service || '',
          action: this.assetStatus.action || '',
          min_value: (this.assetStatus.min_value != null) ? String(this.assetStatus.min_value) : '',
          max_value: (this.assetStatus.max_value != null) ? String(this.assetStatus.max_value) : '',
          edit_url_template: this.assetStatus.edit_url_template || '',
          // / — default true if backend omits the key
          // (legacy deploy seeing first read).
          verify_tls: (this.assetStatus.verify_tls !== false),
        };
      }
    } catch (e) {
      console.error(e);
    }
  },

  async copyRedirectUri() {
    const uri = (this.oidcStatus && this.oidcStatus.redirect_uri_default)
      || (this.oidcForm && this.oidcForm.redirect_uri) || '';
    if (!uri) {
      this.showToast(this.t('toasts.no_redirect_uri'), 'error');
      return;
    }
    try {
      await navigator.clipboard.writeText(uri);
      this.showToast(this.t('toasts.redirect_uri_copied'));
    } catch (_) {
      this.showToast(this.t('toasts.copy_failed'), 'error');
    }
  },

  // Optimistic stamp of the last-test-success cache when a Test
  // endpoint reports ok=true. Backend ALSO writes the same timestamp
  // to the `settings` KV (`last_test_success_<provider>`) — that's
  // the cross-browser source of truth, hydrated into
  // `_lastTestSuccess` on every /api/me load via
  // `client_config.last_test_success`. The optimistic stamp here is
  // just so the label updates IMMEDIATELY on Test-success without
  // waiting for the next /api/me round-trip.
  recordTestSuccess(key) {
    if (!key) {
      return;
    }
    const ts = Math.floor(Date.now() / 1000);
    this._lastTestSuccess = {...(this._lastTestSuccess || {}), [key]: ts};
  },
  // Wrap every `<input type="password">` whose closest ancestor
  // `<form>` is missing in a hidden `<form>` so Chromium stops
  // emitting "Password field is not contained in a form" DevTools
  // warnings (~17 instances across admin tabs — Beszel / Pulse /
  // Webmin / Portainer / OIDC / SSH / Asset / SNMP / host-groups
  // secrets). The form uses `display: contents` so layout is
  // unaffected, AND `onsubmit="return false"` so an accidental
  // Enter-key submit doesn't navigate the page (the SPA POSTs every
  // secret via fetch, never form-submission). Idempotent — runs on
  // init + on every Alpine DOM mutation; already-wrapped inputs
  // skip cleanly via the `closest('form')` guard.
  _wrapOrphanPasswordFields() {
    const inputs = document.querySelectorAll('input[type="password"]');
    for (const input of inputs) {
      let form = input.closest('form');
      if (!form) {
        form = document.createElement('form');
        form.style.display = 'contents';
        form.setAttribute('onsubmit', 'return false');
        form.dataset.passwordWrap = '1';
        input.parentNode.insertBefore(form, input);
        form.appendChild(input);
      }
      // Chrome / Edge fire `[DOM] Password forms should have
      // (optionally hidden) username fields for accessibility`
      // for every password input that lives in a form WITHOUT a
      // matching username field — password managers + AT need
      // an account identifier paired with the secret. The login
      // page already has explicit username + password inputs;
      // every OTHER password field in the SPA (Beszel password,
      // Webmin password, OIDC client_secret, Portainer api_key,
      // SSH passphrase, SNMP v3 keys, etc.) is for a SERVICE
      // credential, not a personal login — so there's no real
      // username to pair. Inject ONE hidden disabled username
      // input per form to satisfy the heuristic; disabled keeps
      // it out of the form's submitted-fields set so it can't
      // accidentally leak into a future POST. Idempotent —
      // re-runs of this helper find the existing field and skip.
      if (form.dataset.usernameInjected === '1') {
        continue;
      }
      if (form.querySelector('input[autocomplete="username"]')) {
        form.dataset.usernameInjected = '1';
        continue;
      }
      const u = document.createElement('input');
      u.type = 'text';
      u.name = 'username';
      u.autocomplete = 'username';
      u.disabled = true;
      u.setAttribute('aria-hidden', 'true');
      u.style.display = 'none';
      form.insertBefore(u, input);
      form.dataset.usernameInjected = '1';
    }
  },
  // Returns the formatted "Last connected: <relative time>" label
  // for a provider key, or '' when no successful test has been
  // recorded yet (so the consumer's `x-show` collapses cleanly).
  // The relative-time math reads `_lastTestSuccessNow` so a 60s
  // tick refreshes every label without reloading.
  lastTestSuccessLabel(key) {
    const ts = (this._lastTestSuccess || {})[key];
    if (!ts) {
      return '';
    }
    const now = this._lastTestSuccessNow || Math.floor(Date.now() / 1000);
    const delta = Math.max(0, now - ts);
    let rel;
    if (delta < 60) {
      rel = this.t('common.just_now') || 'just now';
    } else {
      if (delta < 3600) {
        rel = this.t('common.minutes_ago', {count: Math.floor(delta / 60)}) || `${Math.floor(delta / 60)}m ago`;
      } else {
        if (delta < 86400) {
          rel = this.t('common.hours_ago', {count: Math.floor(delta / 3600)}) || `${Math.floor(delta / 3600)}h ago`;
        } else {
          rel = this.t('common.days_ago', {count: Math.floor(delta / 86400)}) || `${Math.floor(delta / 86400)}d ago`;
        }
      }
    }
    return this.t('admin.last_connected_label', {rel: rel}) || `Last connected ${rel}`;
  },

  // list of curated hosts that have ping enabled. Pulled from
  // the in-memory `hostsConfig` (loaded by the Hosts admin tab) so
  // the picker stays in sync with the row-level toggles without an
  // extra round-trip.
  pingEnabledHosts() {
    const rows = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
    return rows
      .filter(h => h && h.ping && h.ping.enabled && h.enabled !== false && h.id)
      .map(h => ({id: h.id, label: this.hostDisplayName(h) || h.id}));
  },
  // / list of curated hosts that have SNMP mapped (a
  // non-empty `snmp_name` row field). Pulled from the in-memory
  // `hostsConfig` (loaded by the Hosts admin tab) so the picker
  // stays in sync with the row-level config without an extra
  // round-trip. Mirrors `pingEnabledHosts()` exactly so the two
  // providers' test UX is unified.
  snmpEnabledHosts() {
    const rows = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
    return rows
      .filter(h => h && h.enabled !== false && h.id
        // explicit opt-in: SNMP probes only run
        // when the operator checks the per-host enable
        // box. Default-OFF mirrors ping.enabled.
        && !!(h.snmp && h.snmp.enabled === true)
        // Same canonical chain (snmp_name → address) the
        // live sampler uses — a host with `address` set
        // and `snmp_name` blank IS valid SNMP target.
        && ((h.snmp_name || '').trim() || (h.address || '').trim()))
      .map(h => ({id: h.id, label: this.hostDisplayName(h) || h.id}));
  },
  // User-side convenience handlers — operate on profileForm.notify_events
  // (per-user opt-in map keyed by BARE event name — no
  // notify_event_ prefix). Per-medium granularity: each event's value
  // is `{medium: bool}` after syncProfileForm normalises it, so each
  // helper writes uniformly across every medium that's globally
  // enabled. Admin-disabled events skip — the backend rejects an
  // opt-in attempt for them with 400.
  _bareEventName(k) {
    return String(k || '').replace(/^notify_event_/, '');
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
  // Returns true when the event is enabled across EVERY medium —
  // used to render the row's master-toggle in its checked state.
  userNotifyEventRowAll(eventKey) {
    const bare = this._bareEventName(eventKey);
    const slot = (this.profileForm && this.profileForm.notify_events)
      ? this.profileForm.notify_events[bare] : null;
    if (!slot || typeof slot !== 'object') {
      return false;
    }
    const mediums = this.notifyMediumNames();
    for (const m of mediums) {
      if (slot[m] === false) {
        return false;
      }
    }
    return true;
  },
  userNotifyBulkSuccessMedium: '',
  userNotifyBulkFailureMedium: '',
  _debugSnapshot() {
    const s = this.settings || {};
    return JSON.stringify({
      enabled: !!s.debug_panel_enabled,
    });
  },
  debugDirty() {
    return this._debugBaseline !== this._debugSnapshot();
  },
  _openMeteoSnapshot() {
    const s = this.settings || {};
    return JSON.stringify({
      enabled: !!s.open_meteo_enabled,
      url: (s.open_meteo_url || '').trim().replace(/\/+$/, ''),
    });
  },
  openMeteoDirty() {
    return this._openMeteoBaseline !== this._openMeteoSnapshot();
  },
  markOpenMeteoDirty() {
  },
  // Auto-save a single per-service "enabled" master switch.
  // Wired to the @change of the toggle checkbox for Apprise /
  // Open-Meteo / Portainer / SSH so the operator doesn't have to
  // hunt for a Save button just to flip the master switch. Sends
  // ONLY the one field so it doesn't drag along whatever else is
  // dirty in the form. Toast confirms with the resulting state.
  async saveServiceEnabled(name) {
    const allowed = ['apprise', 'open_meteo', 'portainer', 'ssh'];
    if (!allowed.includes(name)) {
      return;
    }
    const key = name + '_enabled';
    const value = !!this.settings[key];
    try {
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({[key]: value}),
      });
      if (!r.ok) {
        throw new Error(await r.text());
      }
      const stateKey = value
        ? 'admin_integrations.toggle_enabled_toast'
        : 'admin_integrations.toggle_disabled_toast';
      this.showToast(this.t(stateKey, {name}), 'success');
    } catch (_) {
      // Roll the in-memory toggle back so UI matches server state.
      this.settings[key] = !value;
      this.showToast(this.t('toasts_extra.save_failed_generic'), 'error');
    }
  },
  // ---- Providers vs per-event Save split --------------------------
  // The notifications page has TWO functionally separate Save
  // buttons. The first ("Save channel configuration", in the
  // providers section) commits Apprise URL/tag, Telegram credentials,
  // medium toggles, listener config, and in-app tunables. The second
  // ("Save event toggles", in the per-event sibling section)
  // commits ONLY the per-event notification opt-ins. They have:
  //   - independent dirty trackers (`providersDirty()` vs
  //     `perEventDirty()`) backed by disjoint snapshot helpers
  //     (`_providersSnapshot` vs `_perEventSnapshot`).
  //   - independent save handlers (`saveProviders` builds a payload
  //     containing only Apprise/Telegram/medium/tunable keys;
  //     `savePerEvent` builds one containing only `notify_event_*`
  //     keys). They never share a POST.
  //   - independent success/error chips (`providersSaveResult` vs
  //     `perEventSaveResult`).
  //   - independent button labels ("Save channel configuration" vs
  //     "Save event toggles") + inline scope-hint paragraphs so the
  //     operator can never mistake one for the other.
  // Result: editing a per-event toggle ONLY enables the per-event
  // Save button; editing a channel field ONLY enables the channel
  // Save button. An action on one button never visually conflates
  // with the other.
  _providersSnapshot() {
    const s = this.settings || {};
    const tf = this.tuningForm || {};
    return JSON.stringify({
      // Apprise
      apprise_enabled: !!s.apprise_enabled,
      apprise_url: (s.apprise_url || '').trim(),
      apprise_tag: (s.apprise_tag || '').trim(),
      // Telegram core
      telegram_chat_id: (s.telegram_chat_id || '').trim(),
      telegram_thread_id: (s.telegram_thread_id || '').trim(),
      telegram_verify_tls: !!s.telegram_verify_tls,
      // Write-only secret — non-empty form value = dirty.
      telegram_token_pending: (s.telegram_bot_token || '').trim() ? 'pending' : '',
      // Telegram Phase 2 listener config
      telegram_listener_enabled: !!s.telegram_listener_enabled,
      telegram_allow_destructive: !!s.telegram_allow_destructive,
      telegram_authorized_user_ids: (s.telegram_authorized_user_ids || '').trim(),
      // Per-medium fan-out toggles
      medium_app: !!s.notify_medium_app,
      medium_apprise: !!s.notify_medium_apprise,
      medium_telegram: !!s.notify_medium_telegram,
      // In-app tunables (live in the In-app tab body)
      tuning_retention: (tf.tuning_notification_retention_days ?? '').toString(),
      tuning_page: (tf.tuning_notification_page_size ?? '').toString(),
      tuning_poll: (tf.tuning_notifications_poll_interval_seconds ?? '').toString(),
    });
  },
  providersDirty() {
    return this._providersBaseline !== this._providersSnapshot();
  },
  _perEventSnapshot() {
    const events = {};
    for (const k of (this.notifyEventKeys || [])) {
      events[k] = !!(this.settings || {})[k];
    }
    return JSON.stringify(events);
  },
  perEventDirty() {
    return this._perEventBaseline !== this._perEventSnapshot();
  },
  async savePerEvent() {
    // Per-event-only POST — strictly the notify_event_* keys. No
    // Test-before-Save gate (per-event toggles don't round-trip).
    if (this.settingsSaving) {
      return;
    }
    this.settingsSaving = true;
    this.perEventSaveResult = null;
    try {
      const payload = {};
      for (const k of (this.notifyEventKeys || [])) {
        if (k in this.settings) {
          payload[k] = this.settings[k] ? 'true' : 'false';
        }
      }
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      await this.loadSettings();
      this._perEventBaseline = this._perEventSnapshot();
      this._appriseBaseline = this._appriseSnapshot();
      this.perEventSaveResult = {
        ok: true,
        detail: this.t('admin.notifications.per_event_save_success') || 'Per-event toggles saved',
      };
    } catch (e) {
      this.perEventSaveResult = {
        ok: false,
        detail: String(e && e.message ? e.message : e),
      };
    } finally {
      this.settingsSaving = false;
    }
  },
  async loadIgnores() {
    try {
      const r = await fetch('/api/ignores');
      this.ignores = (await r.json()).ignores || [];
    } catch (e) {
      console.error(e);
    }
  },
  async addIgnore() {
    if (!this.newIgnore.pattern.trim()) {
      return;
    }
    await fetch('/api/ignores', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(this.newIgnore),
    });
    this.newIgnore.pattern = '';
    await this.loadIgnores();
    await this.refresh(true);
  },
  async delIgnore(pattern) {
    await fetch('/api/ignores/' + encodeURIComponent(pattern), {method: 'DELETE'});
    await this.loadIgnores();
    await this.refresh(true);
  },
  async toggleIgnore(item) {
    if (item.ignored) {
      const match = this.ignores.find(ig =>
        (ig.kind === 'image' && (item.image || '').includes(ig.pattern)) ||
        (ig.kind === 'stack' && ig.pattern === item.stack)
      );
      if (match) {
        await this.delIgnore(match.pattern);
      }
    } else {
      this.newIgnore = {kind: 'image', pattern: item.image};
      await this.addIgnore();
    }
    this.drawerItem = null;
  },
  hasHistoryFilter() {
    const f = this.historyFilters;
    return !!(f.q || f.stack || f.op_type || f.status || f.actor || f.fromDate || f.toDate);
  },
  resetHistoryFilters() {
    this.historyFilters = {q: '', stack: '', op_type: '', status: '', actor: '', fromDate: '', toDate: ''};
  },
  _persistHistoryPaging() {
    try {
      localStorage.setItem('historyPage', String(this.historyPage));
      localStorage.setItem('historyPerPage', String(this.historyPerPage));
    } catch (_) {
    }
  },
  // Open the AI sidebar pre-loaded with a "diagnose this history row"
  // user-turn. The seeded prompt carries the row's op_type, target,
  // status, error text, and recent events so the AI can answer
  // root-cause questions without the operator typing them all out.
  // Falls back to the AI palette when the sidebar surface is not
  // available (unlikely — both gates check the same toggles).
  _diagnoseHistoryRowWithAi(h) {
    if (!h) {
      return;
    }
    const parts = [];
    parts.push(`Diagnose this history row and suggest the most likely root cause + one specific remediation step.`);
    parts.push(`Op: ${h.op_type || 'unknown'}`);
    if (h.target_name) {
      parts.push(`Target: ${h.target_name}`);
    }
    if (h.target_stack) {
      parts.push(`Stack: ${h.target_stack}`);
    }
    parts.push(`Status: ${h.status || 'unknown'}`);
    parts.push(`When: ${this.formatTime(h.ts) || '—'}`);
    if (h.duration) {
      parts.push(`Duration: ${(h.duration || 0).toFixed(2)}s`);
    }
    if (h.actor) {
      parts.push(`Actor: ${h.actor}`);
    }
    if (h.error) {
      parts.push(`Error: ${h.error}`);
    }
    const events = this.parseEvents(h.events) || [];
    if (events.length) {
      const tail = events.slice(-8).map(ev =>
        `[${this.formatTimeShort(ev.ts)} ${ev.level || 'info'}] ${ev.msg || ''}`
      ).join('\n');
      parts.push(`Recent events:\n${tail}`);
    }
    const prompt = parts.join('\n');
    try {
      if (typeof this.openAiSidebar === 'function') {
        this.openAiSidebar();
      }
      // Stash the prompt into the sidebar's query state and fire its
      // existing send pipeline. The pipeline records turns + persists
      // to ui_prefs.ai_conversation, so we route through it rather
      // than re-implementing send-on-mount logic here. Use `$nextTick`
      // so the textarea has time to bind to the new value before the
      // send fires (the sidebar's textarea reads from the state field
      // at submit time, but the DOM element may need one tick to
      // reflect the prefill visually).
      if (typeof this._setAiSidebarQuery === 'function') {
        this._setAiSidebarQuery(prompt);
      } else {
        this.aiSidebarQuery = prompt;
      }
      const fire = () => {
        if (typeof this.sendAiSidebarMessage === 'function') {
          this.sendAiSidebarMessage();
        }
      };
      if (this.$nextTick) {
        this.$nextTick(fire);
      } else {
        setTimeout(fire, 0);
      }
    } catch (e) {
      console.warn('[history] diagnose-with-ai failed', e);
    }
  },
  async clearHistory() {
    const ok = await this.confirmDialog({
      title: this.t('history.clear_confirm_title'),
      html: this.t('history.clear_confirm_html'),
      icon: 'warning',
      confirmText: this.t('history.clear_confirm_button'),
      confirmColor: this._cssVar('--danger'),
      focusConfirm: true,
    });
    if (!ok) {
      return;
    }
    await fetch('/api/history', {method: 'DELETE'});
    await this.loadHistory();
    this.showToast(this.t('toasts.history_cleared'));
  },
  // Mark every unread notification in one cluster read. Bulk version
  // of `markNotificationRead` — fires one PATCH per row through the
  // existing handler so the SSE notification:read event publishes per
  // notification (other tabs reconcile each row independently).
  async markClusterRead(cluster) {
    if (!cluster || !cluster.items) {
      return;
    }
    const unread = cluster.items.filter(n => n && n.read_at == null);
    for (const n of unread) {
      try {
        await this.markNotificationRead(n.id);
      } catch (_) {
      }
    }
  },

};
