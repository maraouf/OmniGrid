// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,AnonymousFunctionJS,ConstantOnRightSideOfComparisonJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
// "Unused function" warnings fire on every Alpine-template-consumed
// method — the SPA spread-imports this module's export into the
// Alpine root, then templates call `sortedSchedules()` /
// `createSchedule()` / etc. via `@click` + `x-for` bindings the
// static analyser can't see. Same lineage for `Element is not
// exported` WEAK warnings. Tail-recursion at the page-clamp retry
// (`loadScheduleQueue` re-enters itself once when the server reports
// fewer pages than expected) is bounded by the clamp converging
// toward `scheduleQueueTotalPages` — depth = at most 1. Empty catch
// + unused `_` are the ignore-and-move-on shape on localStorage
// persistence + fetch failures (the surrounding toast / error pill
// owns the user-visible error). Nested `t()` calls + `String()` /
// `Math.min()` / `parseInt()` / `encodeURIComponent()` pipelines are
// idiomatic. `JSIgnoredPromiseFromCall` fires on the
// `loadScheduleQueue()` fire-and-forget calls in the queue-page
// navigation handlers — by design, the caller wants the page change
// to feel instant, not block on the round-trip.
// noinspection JSUnusedGlobalSymbols,JSUnusedLocalSymbols,UnusedFunctionJS,JSUnusedFunction,ElementNotExported,JSElementNotExported
// noinspection ContinueStatementJS,JSContinueStatement,BreakStatementJS,JSBreakStatement
// noinspection UnusedCatchParameterJS,JSUnusedCatchParameter,EmptyCatchBlockJS,JSEmptyCatchBlock
// noinspection OverlyComplexBooleanExpressionJS,OverlyComplexBooleanExpression,JSOverlyComplexBooleanExpression
// noinspection NestedFunctionCallJS,JSNestedFunctionCall
// noinspection TailRecursionJS,JSTailRecursion,JSRecursive
// noinspection JSIgnoredPromiseFromCall,JSUnhandledPromiseFromCall
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Schedules surface (Admin → Schedules).

export default {
  schedulerSaving: false,
  scheduleSaving: false,   // schedule modal Save button
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
    } catch {
      return 25;
    }
  })(),
  scheduleQueuePage: 1,
  scheduleQueueTotal: 0,
  scheduleQueueSearch: '',
  _scheduleQueueSearchTimer: null,
  scheduleQueueTotalPages: 1,
  scheduleKinds: ['prune_node', 'prune_all_nodes', 'gather_refresh', 'backup', 'config_backup', 'asset_inventory_refresh', 'prune_logs', 'prune_notifications', 'prune_config_backups', 'swarm_agent_health', 'port_scan_refresh'],
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
  // When non-null, the edit dialog is open and bound to this copy of the
  // row being edited. `params_text` on the copy is kept as a string so
  // Alpine's two-way binding stays simple.
  editingSchedule: null,
  // Loading flags for the admin tables that fetch their rows
  // asynchronously on first paint. Mirror the `usersLoaded` shape so
  // the partials render a centered `.loading-state` block while the
  // initial fetch is in flight, then flip to the populated table.
  schedulesLoaded: false,
  // Sort state for the Schedules admin table. Default `col: ''` = no
  // sort (server-supplied order). Routes through the shared
  // `_sortToggle` + `_sortRows` helpers.
  schedulesSort: {col: '', dir: 'desc'},
  sortedSchedules() {
    return this._sortRows(this.schedules || [], this.schedulesSort);
  },

  // ----- Scheduler ----------------------------------------------------
  // Backend at /api/schedules (CRUD) and /api/schedules/queue (recent
  // scheduler-driven ops from the history table). Every write method
  // surfaces an error toast; every destructive action confirms first.

  async loadSchedules() {
    try {
      const r = await fetch('/api/schedules');
      if (!r.ok) {
        return;
      }
      const d = await r.json();
      // in-place reconcile (keyed on id) instead of wholesale
      // reassignment so any auto-refresh pass doesn't tear down the
      // row's expanded-detail / inline-edit state.
      this._reconcileById(this.schedules, d.schedules || []);
      if (Array.isArray(d.kinds) && d.kinds.length) {
        this.scheduleKinds = d.kinds;
      }
      if (typeof d.min_interval_seconds === 'number') {
        this.scheduleMinInterval = d.min_interval_seconds;
      }
    } catch (_) {
    } finally {
      this.schedulesLoaded = true;
    }
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
      if (!r.ok) {
        return;
      }
      const d = await r.json();
      // in-place reconcile keyed on op_id when present;
      // synthetic ops (op_id is null on legacy rows / direct
      // gather-refresh writes) get a stable composite key from
      // (name + ts) so the reconciler can still match them across
      // ticks without trashing the row identity.
      const queue = (d.queue || []).map(row => {
        if (row && row.op_id != null && row.op_id !== '') {
          return row;
        }
        // Stamp a synthetic _key so _reconcileById can match it.
        return {...row, _key: `${row && row.name || ''}@${row && row.ts || 0}`};
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
    } catch (_) {
    }
  },
  scheduleQueuePages() {
    return this.scheduleQueueTotalPages;
  },
  // Wire name kept for template compatibility — now a thin pass-
  // through since the backend already slices to the current page.
  scheduleQueuePageItems() {
    return this.scheduleQueue;
  },
  setScheduleQueuePageSize(n) {
    const v = parseInt(n, 10);
    if (![10, 25, 50].includes(v)) {
      return;
    }
    this.scheduleQueuePageSize = v;
    this.scheduleQueuePage = 1;
    try {
      localStorage.setItem('scheduleQueuePageSize', String(v));
    } catch {
    }
    // Refetch with the new page size.
    this.loadScheduleQueue();
  },
  scheduleQueueGoto(page) {
    const total = this.scheduleQueueTotalPages;
    const p = Math.max(1, Math.min(total, parseInt(page, 10) || 1));
    if (p === this.scheduleQueuePage) {
      return;
    }
    this.scheduleQueuePage = p;
    this.loadScheduleQueue();
  },
  // Debounced search — refetch 250ms after the operator stops
  // typing. Reset to page 1 so the new filtered result starts at
  // the top instead of an out-of-range page.
  onScheduleQueueSearchInput() {
    if (this._scheduleQueueSearchTimer) {
      clearTimeout(this._scheduleQueueSearchTimer);
    }
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

  async createSchedule() {
    if (this.scheduleBusy) {
      return;
    }
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
    if (cadencePayload === null) {
      return;
    }  // helper already toasted
    let params;
    try {
      params = this._parseParamsText(s.params_text);
    } catch (e) {
      this.showToast(e.message, 'error');
      return;
    }
    this.scheduleBusy = true;
    try {
      const r = await fetch('/api/schedules', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          name: s.name.trim(),
          kind: s.kind,
          params,
          interval_seconds: Number(s.interval_seconds) || 0,
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
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.scheduleBusy = false;
    }
  },

  editSchedule(s) {
    // Clone so edits don't mutate the list until Save. `params_text` is
    // a pretty-printed JSON blob for the textarea; we re-parse on save.
    // The `kind` + `cadence_mode` selects need the documented empty →
    // $nextTick → reassign dance from the project conventions : the modal's
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
        if (!this.editingSchedule) {
          return;
        }
        this.editingSchedule.kind = targetKind;
        this.editingSchedule.cadence_mode = targetCadence;
      });
    });
  },

  cancelEditSchedule() {
    this.editingSchedule = null;
  },

  async saveSchedule() {
    if (!this.editingSchedule) {
      return;
    }
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
    if (cadencePayload === null) {
      return;
    }
    let params;
    try {
      params = this._parseParamsText(e.params_text);
    } catch (err) {
      this.showToast(err.message, 'error');
      return;
    }
    // prune_logs `days` validator. Backend
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
        headers: {'Content-Type': 'application/json'},
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
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.scheduleSaving = false;
    }
  },

  async toggleScheduleEnabled(s) {
    // Fire a PATCH with just the flipped flag — no confirm dialog; the
    // enable/disable toggle is reversible and doesn't kick off anything.
    try {
      const r = await fetch('/api/schedules/' + s.id, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({enabled: !s.enabled}),
      });
      if (r.ok) {
        await this.loadSchedules();
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('admin.schedules.toasts.save_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  async deleteSchedule(s) {
    const res = await Swal.fire({
      title: this.t('admin.schedules.delete_prompt_title'),
      text: this.t('admin.schedules.delete_prompt_text', {name: s.name}),
      icon: 'warning', showCancelButton: true,
      confirmButtonText: this.t('actions.delete'),
      cancelButtonText: this.t('actions.cancel'),
      confirmButtonColor: this._cssVar('--danger'),
    });
    if (!res.isConfirmed) {
      return;
    }
    try {
      const r = await fetch('/api/schedules/' + s.id, {method: 'DELETE'});
      if (r.ok) {
        this.showToast(this.t('admin.schedules.toasts.deleted'));
        await this.loadSchedules();
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.delete_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  async runSchedule(s) {
    // "Run now" bypasses the interval. Destructive kinds (prune_node)
    // still get a confirm so a stray click doesn't delete volumes.
    const destructiveKinds = new Set(['prune_node', 'prune_all_nodes']);
    if (destructiveKinds.has(s.kind)) {
      const ok = await this.confirmDialog({
        title: this.t('admin.schedules.run_prompt_title', {name: s.name}),
        html: this.t('admin.schedules.run_prompt_destructive_html', {kind: s.kind}),
        icon: 'warning',
        confirmText: this.t('admin.schedules.run_now'),
        confirmColor: this._cssVar('--danger'),
        focusConfirm: true,
      });
      if (!ok) {
        return;
      }
    }
    try {
      const r = await fetch('/api/schedules/' + s.id + '/run', {method: 'POST'});
      if (r.ok) {
        this.showToast(this.t('admin.schedules.toasts.run_started', {name: s.name}));
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
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  scheduleStatusClass(status) {
    // Consistent pill colour across tables. Matches the existing pill
    // token families (pill-ok / pill-error / pill-unknown) so new UI
    // doesn't invent its own palette.
    if (status === 'success') {
      return 'pill pill-ok';
    }
    if (status === 'error') {
      return 'pill pill-error';
    }
    return 'pill pill-unknown';
  },

  async saveSchedulerSettings() {
    if (this.schedulerSaving) {
      return;
    }
    this.schedulerSaving = true;
    try {
      const tz = (this.settings.scheduler_timezone || '').trim();
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scheduler_timezone: tz}),
      });
      if (r.ok) {
        this.settings.scheduler_timezone = tz;
        this.showToast(tz
            ? this.t('scheduler_settings.saved_set', {tz})
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
};
