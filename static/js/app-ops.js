// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ExceptionCaughtLocallyJS,JSReusedLocalVariable,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,RedundantLocalVariableJS,JSMissingAwait
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSCheckNamingConventionsInspection,UnnecessaryLocalVariableJS,JSIfStatementsCanBeSimplified,IfStatementSimplifyable,IncrementDecrementResultUsedJS
// Sibling-file canonical noinspection block — same shape as
// app-admin.js / app-charts.js / app-ai.js / app-stats.js so the
// suppressed warning classes stay consistent across the SPA. Real
// bugs (typos / dead assignments / wrong types) are fixed inline,
// NOT suppressed.
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Ops — active operations polling + History tab + bulk-action helpers.

export default {
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
  historyFilters: {q: '', stack: '', op_type: '', status: '', actor: '', fromDate: '', toDate: ''},
  activeOps: [],
  opsExpanded: true,
  _historyQueryParams(opts = {}) {
    // Build the shared ?stack=&op_type=...&since=... query string used by
    // loadHistory() and the CSV/JSON export links, so filters stay in sync.
    // Pass `{ paging: true }` to include offset+limit derived from the
    // current page state — used by the live view. Exports omit paging
    // and request a high cap (5000) so the operator gets the full
    // filtered result, not just one page.
    const f = this.historyFilters;
    const p = new URLSearchParams();
    if (f.q) {
      p.set('q', f.q);
    }
    if (f.stack) {
      p.set('stack', f.stack);
    }
    if (f.op_type) {
      p.set('op_type', f.op_type);
    }
    if (f.status) {
      p.set('status', f.status);
    }
    if (f.actor) {
      p.set('actor', f.actor);
    }
    if (f.fromDate) {
      p.set('since', String(new Date(f.fromDate).getTime() / 1000));
    }
    if (f.toDate) {
      // Date input is midnight-of-day; treat as inclusive END-of-day.
      const end = new Date(f.toDate);
      end.setHours(23, 59, 59, 999);
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
  historyGoToPage(n) {
    const page = Math.max(1, Math.min(parseInt(n, 10) || 1, this.historyTotalPages()));
    if (page === this.historyPage) {
      return;
    }
    this.historyPage = page;
    this._persistHistoryPaging();
    this.loadHistory();
  },
  historyPrevPage() {
    this.historyGoToPage(this.historyPage - 1);
  },
  historyNextPage() {
    this.historyGoToPage(this.historyPage + 1);
  },
  historySetPerPage(n) {
    const per = Math.max(10, Math.min(500, parseInt(n, 10) || 50));
    if (per === this.historyPerPage) {
      return;
    }
    this.historyPerPage = per;
    this.historyPage = 1;
    this._persistHistoryPaging();
    this.loadHistory();
  },
  // Filter changes reset to page 1 — staying on page 7 of an old
  // result set when the new filtered set has 2 pages would land the
  // operator on a blank page.
  historyApplyFilter(fn) {
    if (typeof fn === 'function') {
      fn();
    }
    this.historyPage = 1;
    this._persistHistoryPaging();
    this.loadHistory();
  },
  get historyStackOptions() {
    // Populate the stack dropdown from whatever stacks we currently see
    // in the live cache (avoids a dedicated /api endpoint). Alphabetical.
    return [...new Set((this.stacks || []).map(s => s.name).filter(Boolean))].sort();
  },
  // Request-version counter for `loadHistory` — every fetch
  // increments this; the response handler drops stale responses
  // (those whose version is older than the latest fired request).
  // Typing in a filter input that fires a debounced refetch + an
  // SSE-driven `history:appended` reload + an operator-initiated
  // paging change can stack 3-N concurrent /api/history requests.
  // The
  // newest response should win, but TCP / proxy ordering is not
  // guaranteed — a stale response landing AFTER a newer one
  // would clobber the fresh state via the in-place reconcile.
  // The version-counter pattern is simpler than AbortController
  // (no browser-API requirement) and handles the same case:
  // mark the request, check on completion, drop if stale.
  _historyFetchSeq: 0,
  async loadHistory() {
    const seq = ++this._historyFetchSeq;
    try {
      const r = await fetch('/api/history?' + this._historyQueryParams({paging: true}).toString());
      const d = await r.json();
      // Drop stale responses — if a newer fetch fired while this
      // one was in flight, the newer one will (or already did)
      // populate `this.history`. Skip the reconcile to avoid
      // clobbering fresh state with stale data.
      if (seq !== this._historyFetchSeq) {
        return;
      }
      // in-place reconcile keyed on history row `id` (auto-
      // increment PK from the `history` table) so each page change
      // doesn't tear down every row's expanded `<details>` state +
      // inline-style nodes for the entire table. Reuses the same
      // `_reconcileById` helper that the hosts/items/schedules/etc.
      // pollers use to keep Alpine reactivity stable across reloads.
      this._reconcileById(this.history, Array.isArray(d.history) ? d.history : []);
      this.historyTotal = Number.isFinite(+d.total) ? +d.total : this.history.length;
      // If the operator's persisted page is past the new filtered
      // total (e.g. they had a wide filter on page 7, narrowed it,
      // and the result is 1 page), clamp + reload.
      const max = this.historyTotalPages();
      if (this.historyPage > max) {
        this.historyPage = max;
        this._persistHistoryPaging();
        // Recurse-and-await the clamped re-fetch. The inner call
        // sees the now-correct page + total, falls through the
        // clamp branch on its second pass, completes normally.
        // No infinite recursion risk because the clamp branch only
        // triggers when `historyPage > max` — after we just set
        // `historyPage = max`, the next call's `page > max` is
        // false and the recursion terminates.
        if (this.historyTotal > 0) {
          await this.loadHistory();
        }
      }
    } catch (e) {
      console.error(e);
    }
  },
  openHistoryDetail(h) {
    // ai_palette rows carry a JSON OBJECT in `events` (not the
    // array-of-{ts,level,msg} shape every other op_type uses). Route
    // them to the AI-specific detail renderer that re-uses the same
    // `.ai-resp*` CSS classes the live AI response popup uses, so
    // weeks-old AI conversations look identical to the original
    // popup. Falls through to the standard renderer for every other
    // op_type so all existing history detail views stay unchanged.
    if ((h.op_type || '') === 'ai_palette') {
      return this._openAiPaletteHistoryDetail(h);
    }
    // port_scan rows carry a JSON OBJECT in `events` (same shape
    // the endpoint emits: scan_id / target / ports_scanned /
    // ports_open / scan_duration_ms). Route to the dedicated
    // detail renderer so operators clicking through History see
    // the per-scan summary + a link to view the open-ports chip
    // strip in the host drawer.
    if ((h.op_type || '') === 'port_scan') {
      return this._openPortScanHistoryDetail(h);
    }
    const events = this.parseEvents(h.events) || [];
    const esc = (s) => String(s ?? '').replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]));
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
          <div class="swal-meta-wide"><b>${esc(this.t('history.detail.target'))}</b> <span class="swal-meta-val">${esc(h.target_name || '—')}</span></div>
          <div><b>${esc(this.t('history.detail.stack'))}</b> ${esc(h.target_stack || '—')}</div>
          <div><b>${esc(this.t('history.detail.actor'))}</b> ${esc(h.actor || 'ui')}</div>
          <div><b>${esc(this.t('history.detail.duration'))}</b> ${(h.duration || 0).toFixed(2)}s</div>
          <div><b>${esc(this.t('history.detail.status'))}</b> ${esc(h.status)}</div>
          ${h.error ? `<div class="swal-err"><b>${esc(this.t('history.detail.error'))}</b> ${esc(h.error)}</div>` : ''}
        </div>`;
    // Diagnose button MVP — only
    // surfaces for error rows AND when AI is enabled. Click opens the
    // AI sidebar pre-loaded with this history row's context (op_type,
    // target, error text, recent events) so the AI can explain root
    // cause + suggest a fix. Cheap surface — reuses the existing AI
    // palette + sidebar machinery; no new endpoint.
    const aiEnabled = !!(this.aiSidebarSurfaceEnabled && this.aiSidebarSurfaceEnabled());
    const showDiagnose = (h.status === 'error' || h.error) && aiEnabled;
    const diagnoseBtn = showDiagnose
      ? `<button type="button" id="og-history-diagnose-btn" class="btn btn-soft fs-sm">${esc(this.t('history.detail.diagnose') || 'Diagnose with AI')}</button>`
      : '';
    const self = this;
    Swal.fire({
      title: h.target_name || h.op_type,
      html: `${meta}<div class="swal-events scrollbar">${rows}</div>${diagnoseBtn ? `<div class="mt-3 flex justify-end">${diagnoseBtn}</div>` : ''}`,
      width: 720,
      showConfirmButton: false,
      showCloseButton: true,
      background: this._cssVar('--surface'),
      color: this._cssVar('--text'),
      didOpen: () => {
        if (!showDiagnose) {
          return;
        }
        const btn = document.getElementById('og-history-diagnose-btn');
        if (!btn) {
          return;
        }
        btn.addEventListener('click', () => {
          try {
            Swal.close();
          } catch (_) {
          }
          self._diagnoseHistoryRowWithAi(h);
        });
      },
    });
  },
  pollOps() {
    if (this._opsTimer) {
      clearTimeout(this._opsTimer);
    }
    if (!this._opLingerUntil) {
      this._opLingerUntil = {};
    }
    if (!this._opsSeen) {
      this._opsSeen = null;
    }  // null sentinel = first poll
    // Linger window — keep finished ops visible in the floating panel
    // for this many seconds after they complete. Two qualifying paths:
    // 1. op was running in the previous poll and is now done;
    // 2. op is brand-new to us (never seen before) AND is already
    //    done (completed between polls — e.g. bulk cleanup where
    //    individual removes finish in <1.5s).
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
        if (firstPoll) {
          this._opsSeen = new Set();
        }
        // Two paths qualify as "just done" for both the linger
        // panel AND the downstream refresh/toast trigger:
        // (1) observed running → done
        // (2) brand-new to us AND already terminal (completed
        //     between polls — e.g. force-remove finishes in
        //     <1.5s so the op is never seen as `running`)
        // Merging both into the same justDone set fixes where
        // fast ops bypassed the post-op items refresh and the
        // Cleanup button kept showing the removed container.
        const justDone = [];
        for (const o of all) {
          const wasUnknown = !this._opsSeen.has(o.id);
          this._opsSeen.add(o.id);
          if (o.status === 'running') {
            // The original `_markBusy(key)` set a 3 s auto-clear timer
            // so a missed poll couldn't strand a button. Once we
            // SEE the op as running on the wire, cancel that timer
            // — the running → terminal transition path below will
            // call `_clearBusy` via `_clearBusyFromOp` when the op
            // actually finishes, however long that takes (stack
            // pull + recreate routinely exceeds 3 s).
            const runningKey = this._opBusyKey(o);
            if (runningKey && this.busy[runningKey]) {
              this._holdBusy(runningKey);
            }
            continue;
          }
          if (this._opLingerUntil[o.id]) {
            continue;
          }
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
      } catch (_) {
      }
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
  pollOpsNow() {
    this.pollOps();
  },
  // Active-Ops chip / history label for the canonical op_type enum
  // (update_stack / update_container / restart_service / restart_container
  // / remove_container). Same i18n-first-then-fallback pattern as the
  // dell* / ups* helpers. Fallback splits underscores AND capitalises
  // each word so a brand-new op_type ("update_in_place") renders
  // "Update In Place" instead of the brittle bare-replace shape.
  opTypeLabel(op_type) {
    const s = String(op_type || '').toLowerCase();
    if (!s) {
      return '';
    }
    const key = `op_types.${s}`;
    const translated = this.t(key);
    if (translated && translated !== key) {
      return translated;
    }
    return s.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  },
  opStatusLabel(status) {
    const s = String(status || '').toLowerCase();
    if (!s) {
      return '';
    }
    const key = `op_status.${s}`;
    const translated = this.t(key);
    if (translated && translated !== key) {
      return translated;
    }
    return s.charAt(0).toUpperCase() + s.slice(1);
  },
  async itemAction(item, opts) {
    if (this.isItemBusy(item)) {
      // Silent no-op was the prior behaviour — operator-flagged after
      // pressing Recreate repeatedly on an external container with a
      // stuck "running" op in activeOps and seeing nothing happen +
      // no logs. Toast surfaces the busy state so the operator knows
      // the click registered AND knows where to look if the op-in-
      // flight is stale: the floating running-ops pill at the bottom
      // of the page OR the History tab.
      this.showToast(this.t('toasts.already_in_progress', {name: item.name || item.stack || item.raw_id}), 'warning');
      return;
    }
    const skipConfirm = !!(opts && opts.skipConfirm);
    if (!skipConfirm) {
      // Async release-notes hint — popup opens INSTANTLY with a
      // loading placeholder; fetch runs in parallel and replaces
      // the placeholder when it resolves. Bulk updates and
      // AI-dispatch (skipConfirm) bypass this entirely since the
      // popup itself is bypassed. The earlier synchronous variant
      // blocked popup-open on the registry call → 1-2 second delay
      // where the operator saw nothing.
      const baseHtml = item.stack_id
        ? this.t('dialogs.update_stack_html', {name: item.stack})
        : this.t('dialogs.recreate_container_html', {name: item.name});
      const html = baseHtml + (item.image ? this._releaseNotesPlaceholderHtml() : '');
      // Fire-and-forget — the await on `confirmDialog` below opens
      // the popup synchronously; the async filler races against the
      // operator's click + the popup's DOM lifecycle.
      if (item.image) {
        this._replaceReleaseNotesAsync(item.image);
      }
      // Brand logo for the confirm header (replaces the generic warning
      // glyph). Resolves the stack/container's icon the same way the cards
      // do; SweetAlert renders it above the title.
      const confirmImg = this.iconUrlFor(item.icon || item.stack || item.name);
      const ok = item.stack_id
        ? await this.confirmDialog({
          title: this.t('dialogs.update_stack_title'),
          html: html,
          imageUrl: confirmImg, imageWidth: 64, imageAlt: item.stack || item.name || '',
          confirmText: this.t('actions.update_stack'),
          focusConfirm: true,
        })
        : await this.confirmDialog({
          title: this.t('dialogs.recreate_container_title'),
          html: html,
          imageUrl: confirmImg, imageWidth: 64, imageAlt: item.name || '',
          confirmText: this.t('actions.recreate'),
          focusConfirm: true,
        });
      if (!ok) {
        return;
      }
    }
    if (this.isItemBusy(item)) {
      return;
    }
    const key = item.stack_id
      ? this._busyKey('stack', item.stack_id)
      : this._busyKey('ctn', item.raw_id);
    const url = item.stack_id
      ? `/api/update/stack/${item.stack_id}`
      : `/api/update/container/${item.raw_id}`;
    this._markBusy(key);
    try {
      const r = await fetch(url, {method: 'POST'});
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      this.showToast(this.t('toasts.queued', {name: item.stack || item.name}));
      this.drawerItem = null;
      this.pollOpsNow();
    } catch (e) {
      this._clearBusy(key);
      this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
    }
  },
  async bulkRestart() {
    const picked = this.selectionRestartable().filter(i => !this.isRestartBusy(i));
    if (picked.length === 0) {
      this.showToast(this.t('toasts.nothing_restartable'), 'error');
      return;
    }
    const items = picked.slice(0, 8)
      .map(i => `<li><code>${i.name}</code> <span class="hint-sub">· ${i.type}</span></li>`)
      .join('');
    const more = picked.length > 8 ? this.t('dialogs.bulk_update_more', {count: picked.length - 8}) : '';
    const titleKey = picked.length === 1 ? 'dialogs.bulk_restart_title' : 'dialogs.bulk_restart_title_plural';
    const ok = await this.confirmDialog({
      title: this.t(titleKey, {count: picked.length}),
      html: this.t('dialogs.bulk_restart_html', {items, more}),
      icon: 'question', confirmText: this.t('actions.restart'), confirmColor: this._cssVar('--primary'),
      focusConfirm: true,
    });
    if (!ok) {
      return;
    }
    let okCount = 0, fail = 0;
    for (const i of picked) {
      const isService = i.type === 'service';
      const key = isService ? this._busyKey('svc', i.raw_id) : this._busyKey('ctn', i.raw_id);
      if (this.busy[key]) {
        continue;
      }
      this._markBusy(key);
      try {
        const url = isService ? `/api/restart/service/${i.raw_id}` : `/api/restart/container/${i.raw_id}`;
        const r = await fetch(url, {method: 'POST'});
        if (r.ok) {
          okCount++;
        } else {
          fail++;
          this._clearBusy(key);
        }
      } catch (_) {
        fail++;
        this._clearBusy(key);
      }
    }
    this.selected = [];
    this.pollOpsNow();
    this.showToast(this.t('toasts.restart_result', {ok: okCount, fail}), fail ? 'error' : 'success');
  },
  async bulkUpdate() {
    return this._bulkUpdateItems(this.selectionUpdatable(), {clearSelection: true});
  },
  async bulkUpdateAll(opts) {
    // AI palette fast-action — pull updates for every item with an
    // available update, regardless of selection. Same dedupe + confirm
    // dialog + sequential POST loop as `bulkUpdate()`. Accepts
    // `{ skipConfirm }` for the AI sidebar inline-confirm path.
    return this._bulkUpdateItems(this.updatableAll(), {
      clearSelection: false,
      skipConfirm: !!(opts && opts.skipConfirm),
    });
  },
  async _bulkUpdateItems(source, {clearSelection, skipConfirm} = {}) {
    const stackIds = new Set();
    const queue = [];
    for (const i of source) {
      if (i.stack_id) {
        if (!stackIds.has(i.stack_id)) {
          stackIds.add(i.stack_id);
          queue.push(i);
        }
      } else {
        queue.push(i);
      }
    }
    const runnable = queue.filter(i => !this.isItemBusy(i));
    const skipped = queue.length - runnable.length;
    if (runnable.length === 0) {
      this.showToast(skipped ? this.t('toasts.already_running', {count: skipped}) : this.t('toasts.nothing_to_update'), 'error');
      return;
    }
    if (!skipConfirm) {
      const items = runnable.slice(0, 8).map(i => `<li><code>${i.stack || i.name}</code></li>`).join('');
      const more = runnable.length > 8 ? this.t('dialogs.bulk_update_more', {count: runnable.length - 8}) : '';
      const skippedNote = skipped ? this.t('dialogs.bulk_update_skipped', {count: skipped}) : '';
      const ok = await this.confirmDialog({
        title: this.t('dialogs.bulk_update_title'),
        html: this.t('dialogs.bulk_update_html', {
          runnable: runnable.length, stacks: stackIds.size,
          skipped_note: skippedNote, items, more,
        }),
        icon: 'warning', confirmText: this.t('actions.update'),
        focusConfirm: true,
      });
      if (!ok) {
        return;
      }
    }
    let okCount = 0, fail = 0;
    for (const i of runnable) {
      const key = i.stack_id ? this._busyKey('stack', i.stack_id) : this._busyKey('ctn', i.raw_id);
      if (this.busy[key]) {
        continue;
      }
      this._markBusy(key);
      try {
        const url = i.stack_id ? `/api/update/stack/${i.stack_id}` : `/api/update/container/${i.raw_id}`;
        const r = await fetch(url, {method: 'POST'});
        if (r.ok) {
          okCount++;
        } else {
          fail++;
          this._clearBusy(key);
        }
      } catch (_) {
        fail++;
        this._clearBusy(key);
      }
    }
    if (clearSelection) {
      this.selected = [];
    }
    this.pollOpsNow();
    this.showToast(this.t('toasts.bulk_result', {ok: okCount, fail}), fail ? 'error' : 'success');
  },
  async bulkRemove() {
    return this._bulkRemoveItems(this.selectionRemovable(), {clearSelection: true});
  },
  async bulkRemoveAll(opts) {
    // Fast-action topbar button — clean up every stopped/failed container
    // on the cluster without having to select them one by one. Accepts
    // `{ skipConfirm }` so the AI sidebar's inline-confirm path can
    // bypass the inner SweetAlert (the operator already approved
    // inline; a second popup would defeat the no-popup contract).
    return this._bulkRemoveItems(this.removableAll(), {
      clearSelection: false,
      skipConfirm: !!(opts && opts.skipConfirm),
    });
  },
  async _bulkRemoveItems(source, {clearSelection, skipConfirm} = {}) {
    const picked = source.filter(i => !this.isItemBusy(i));
    if (picked.length === 0) {
      this.showToast(this.t('toasts.nothing_removable'), 'error');
      return;
    }
    // `skipConfirm` is set when the AI sidebar already inline-confirmed
    // the action (no popup contract). Modal palette + topbar callers
    // leave it false so the rich SweetAlert with the per-container
    // list still fires.
    if (!skipConfirm) {
      const items = picked.slice(0, 8).map(i => `<li><code>${i.name}</code></li>`).join('');
      const more = picked.length > 8 ? this.t('dialogs.bulk_update_more', {count: picked.length - 8}) : '';
      const titleKey = picked.length === 1 ? 'dialogs.bulk_remove_title' : 'dialogs.bulk_remove_title_plural';
      const ok = await this.confirmDialog({
        title: this.t(titleKey, {count: picked.length}),
        html: this.t('dialogs.bulk_remove_html', {items, more}),
        icon: 'warning', confirmText: this.t('actions.remove'), confirmColor: this._cssVar('--danger'),
        focusConfirm: true,
      });
      if (!ok) {
        return;
      }
    }
    // Track which raw_ids we just successfully fired a remove for so
    // we can OPTIMISTICALLY splice them out of `this.items` once the
    // POSTs return — without that splice, the topbar "Cleanup (N)"
    // count stayed > 0 (and the button stayed visible) until the
    // next gather refresh ~30s later, which read as "the button
    // didn't work" + "I have to click refresh to see it disappear".
    const okIds = [];
    let okCount = 0, fail = 0;
    for (const i of picked) {
      const key = this._busyKey('ctn', i.raw_id);
      if (this.busy[key]) {
        continue;
      }
      this._markBusy(key);
      try {
        const r = await fetch(`/api/remove/container/${i.raw_id}`, {method: 'POST'});
        if (r.ok) {
          okCount++;
          okIds.push(i.raw_id);
        } else {
          fail++;
          this._clearBusy(key);
        }
      } catch (_) {
        fail++;
        this._clearBusy(key);
      }
    }
    if (clearSelection) {
      this.selected = [];
    }
    // Optimistically splice the successfully-removed items out of
    // the local items list IN PLACE (per the project conventions "reactive arrays
    // mutated in place" rule). This makes the topbar "Cleanup (N)"
    // count drop immediately so the button disappears without
    // waiting for the next gather refresh. The backend op completes
    // asynchronously; the natural pollOps → op-completed →
    // _clearBusyFromOp flow + the existing 30s items poll picks up
    // the eventual gather refresh after the per-op
    // `gather.invalidate_cache()` fires backend-side.
    //
    // Stash the just-removed raw_ids on a short-lived suppression
    // set so the natural items poll's in-place reconcile doesn't
    // RE-ADD them if it races ahead of the backend's cache
    // invalidation. Pre-fix calling refresh(true) immediately after
    // the POSTs returned stale items (the backend's `_cache.items`
    // still carried them until each remove op's finally-block fired
    // `gather.invalidate_cache()`) — the in-place reconcile re-
    // introduced the rows and the operator saw the topbar count
    // bump back up, then had to click Cleanup again to trigger a
    // second remove on the already-gone container (which the
    // backend's idempotent 404→success path tolerated, but felt
    // broken from the SPA).
    if (okIds.length && Array.isArray(this.items)) {
      const idSet = new Set(okIds);
      for (let idx = this.items.length - 1; idx >= 0; idx--) {
        if (idSet.has(this.items[idx].raw_id)) {
          this.items.splice(idx, 1);
        }
      }
      if (!this._recentlyRemovedIds) {
        this._recentlyRemovedIds = new Set();
      }
      for (const rid of okIds) {
        this._recentlyRemovedIds.add(rid);
      }
      // Clear the suppression after 30s — by then the backend's
      // gather cache has been invalidated by every op's finally
      // block + the next natural poll will return fresh items
      // without the deleted rows.
      setTimeout(() => {
        if (!this._recentlyRemovedIds) {
          return;
        }
        for (const rid of okIds) {
          this._recentlyRemovedIds.delete(rid);
        }
      }, 30000);
    }
    this.pollOpsNow();
    this.showToast(this.t('toasts.remove_result', {ok: okCount, fail}), fail ? 'error' : 'success');
  },
};
