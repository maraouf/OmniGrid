// noinspection ElementNotExported,JSUnusedGlobalSymbols,JSUnusedLocalSymbols,JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,CheckTagEmptyBody,HtmlUnknownTag,HtmlExtraClosingTag,MagicNumberJS,UnusedCatchParameterJS,OverlyComplexBooleanExpressionJS,FunctionWithMultipleReturnPointsJS,FunctionWithMoreThanThreeNegationsJS,OverlyNestedFunctionJS,OverlyLongFunctionJS,OverlyComplexFunctionJS,FunctionWithInconsistentReturnsJS,ChainedFunctionCallJS,NestedFunctionCallJS,NestedAssignmentJS,JSVariableNamingConventionJS,FunctionNamingConventionJS,JSStringConcatenationToES6Template,JSPotentiallyInvalidUsageOfThis,ContinueStatementJS,BreakStatementJS,AssignmentToFunctionParameterJS,IfStatementWithoutBlockJS,IfStatementWithIdenticalBranchesJS,AnonymousFunctionJS,AnonymousCapturingGroupJS,AnonymousFunctionRegExpJS,NamedFunctionExpressionJS,ConditionalExpressionJS,NestedConditionalExpressionJS,ConstantOnRightSideOfComparisonJS,ConstantOnLeftSideOfComparisonJS,EmptyCatchBlockJS,StatementWithEmptyBodyJS,RedundantConditionalExpressionJS,RedundantLocalVariableJS,JSValidateTypes,JSCheckFunctionSignatures,JSPrimitiveTypeWrapperUsage,JSDuplicatedDeclaration,TooManyFunctionParametersJS,NestedTemplateLiteralJS,AssignmentToForLoopParameterJS,AssignmentResultUsedJS,ConditionalCanBeReplacedWithEarlyExitJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Notifications popup — the bell icon top-right + dropdown panel.
//
// Backed by the `notifications` table (in-app medium); rows are written
// by `logic/ops.py:notify` whenever the `app` medium fires. The SPA's
// SSE handlers `_handleNotificationCreated` / `_handleNotificationRead` /
// `_handleNotificationDeleted` keep the popup state synchronized across
// tabs in real time.
//
// Filter chips (severity / event / unread-only) and similar-clustering
// (groupBySimilar toggle) live here too. The Admin → Notifications tab
// (event/medium toggles, template editor) is a different surface and
// stays elsewhere.
//
// Phase 2, Batch 9 of the static/js/app.js modularisation.

export default {
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
    // Cluster pivot. When on, the popup
    // groups consecutive notifications sharing (event, target_kind,
    // target_id) into one row with a "xN" badge — operators on a
    // busy fleet can pivot a wall of identical "service restarted"
    // entries into one row each. Off by default so first-time users
    // see the canonical flat list. Persisted to `ui_prefs.notifications_group_similar`
    // so the toggle survives reloads / cross-device login.
    // **Scope**: grouping is page-scoped (current /api/notifications
    // response only) — full cross-page clustering would need a
    // server-side ``?cluster=true`` mode, deferred to follow-up.
    notificationsGroupSimilar: false,
    // Per-cluster expansion state — set of cluster keys currently
    // showing their individual items. Cleared on filter / page change
    // so an expanded cluster on page 1 doesn't leak its open state
    // onto a cluster with the same key on page 2.
    _notificationsClusterExpanded: {},
    // Polling fallback for bearer-token clients (SSE skips them per
    // CLAUDE.md). Always running but no-ops when the view isn't open
    // and SSE is healthy.
    _notificationsPollHandle: null,
    // ---------------- in-app notifications ----------------
    notificationsQuery() {
      const params = new URLSearchParams();
      params.set('limit', String(this.notificationsLimit || 25));
      params.set('offset', String(this.notificationsOffset || 0));
      if (this.notificationsFilterUnread) {
        params.set('unread_only', 'true');
      }
      if (this.notificationsFilterSeverity && this.notificationsFilterSeverity !== 'all') {
        params.set('severity', this.notificationsFilterSeverity);
      }
      if (this.notificationsFilterEvent && this.notificationsFilterEvent !== 'all') {
        params.set('event', this.notificationsFilterEvent);
      }
      return params.toString();
    },
    // Open the notifications popup. Loads the latest
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
      if (this.notificationsLoading || !this.notificationsHasNext()) {
        return;
      }
      this.notificationsOffset = (this.notificationsOffset || 0) + (this.notificationsLimit || 25);
      await this._reloadNotificationsPage();
    },
    async notificationsPrevPage() {
      if (this.notificationsLoading || !this.notificationsHasPrev()) {
        return;
      }
      const prev = (this.notificationsOffset || 0) - (this.notificationsLimit || 25);
      this.notificationsOffset = Math.max(0, prev);
      await this._reloadNotificationsPage();
    },
    async _reloadNotificationsPage() {
      this.notificationsLoading = true;
      // Cluster expansion state is page-scoped — clear so a cluster
      // expanded on page 1 doesn't leak into page 2.
      this._notificationsClusterExpanded = {};
      try {
        const r = await fetch('/api/notifications?' + this.notificationsQuery());
        if (!r.ok) { this.notificationsLoading = false; return; }
        const d = await r.json();
        // Page-replace, not append — keeps DOM cost constant.
        this.notifications = Array.isArray(d.items) ? d.items : [];
        if (Number.isFinite(d.total)) {
          this.notificationsTotal = d.total;
        }
        if (Number.isFinite(d.unread_count)) {
          this.notificationsUnread = d.unread_count;
        }
      } catch (e) {
        console.warn('[notifications] page change failed', e);
      }
      this.notificationsLoading = false;
    },
    // ---- Notifications cluster pivot (MVP) ----
    // Pivots the current page's notifications into clusters keyed on
    // (event, target_kind, target_id). Each cluster carries:
    //   - key: stable identity string for expansion tracking
    //   - latest: the newest notification (ts max) — drives the row's
    //     title / body / severity / actor / target chips
    //   - items: the full set of notifications in this cluster, newest
    //     first
    //   - count: items.length
    //   - earliest_ts / latest_ts: ts range so the row can render
    //     "12 events between 14:02 and 14:11"
    //   - unread: count of items with read_at == null (drives the
    //     unread chip + the cluster mark-all-read button)
    //   - severity: highest-severity in the cluster (error > warning >
    //     info > success > unknown) so an error in a sea of info
    //     surfaces visually
    // Singleton clusters (one item) get the same shape as flat rows
    // — the template branches on count to render either path cleanly.
    notificationClusters() {
      const list = Array.isArray(this.notifications) ? this.notifications : [];
      if (!list.length) {
        return [];
      }
      const rank = { error: 4, warning: 3, info: 2, success: 1 };
      const out = [];
      const byKey = new Map();
      for (const n of list) {
        const ev = (n && n.event) || '';
        const tk = (n && n.target_kind) || '';
        const tid = (n && n.target_id) || '';
        const key = ev + '||' + tk + '||' + tid;
        let cluster = byKey.get(key);
        if (!cluster) {
          cluster = {
            key, items: [], count: 0,
            earliest_ts: n.ts, latest_ts: n.ts,
            latest: n, severity: n.severity || 'info',
            unread: 0,
          };
          byKey.set(key, cluster);
          out.push(cluster);
        }
        cluster.items.push(n);
        cluster.count += 1;
        if (n.ts < cluster.earliest_ts) {
          cluster.earliest_ts = n.ts;
        }
        if (n.ts > cluster.latest_ts) {
          cluster.latest_ts = n.ts;
          cluster.latest = n;
        }
        if (n.read_at == null) {
          cluster.unread += 1;
        }
        const lr = rank[n.severity] || 0;
        const cr = rank[cluster.severity] || 0;
        if (lr > cr) {
          cluster.severity = n.severity;
        }
      }
      return out;
    },
    isNotificationClusterExpanded(key) {
      return !!(this._notificationsClusterExpanded || {})[key];
    },
    toggleNotificationCluster(key) {
      if (!key) {
        return;
      }
      const set = this._notificationsClusterExpanded || {};
      if (set[key]) {
        delete set[key];
      }
      else {
        set[key] = true;
      }
      this._notificationsClusterExpanded = { ...set };
    },
    // Persist the cluster-toggle to ui_prefs so it survives a reload
    // / cross-device login. Fire-and-forget — best-effort persistence.
    async _persistNotificationsGroupSimilar() {
      try {
        await fetch('/api/me/ui-prefs', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prefs: {
              notifications_group_similar: !!this.notificationsGroupSimilar,
            },
          }),
        });
        if (this.me && this.me.ui_prefs) {
          this.me.ui_prefs.notifications_group_similar =
              !!this.notificationsGroupSimilar;
        }
      } catch (_) {}
    },
    // Lightweight unread-count probe — doesn't fetch the list, just the
    // count. Used by init() so the avatar badge has a count BEFORE the
    // operator opens the view.
    async loadNotificationsUnread() {
      try {
        const r = await fetch('/api/notifications?limit=1&unread_only=true');
        if (!r.ok) {
          return;
        }
        const d = await r.json();
        this.notificationsUnread = Number.isFinite(d.unread_count) ? d.unread_count : 0;
      } catch (_) {}
    },
    // SSE handler — published from logic/ops.py:_notify_medium_app on
    // every successful INSERT. Prepends in place; the list array isn't
    // reassigned so Alpine's row template doesn't tear DOM down.
    _handleNotificationCreated(payload) {
      if (!payload || !payload.id) {
        return;
      }
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
      if (!payload) {
        return;
      }
      if (Number.isFinite(payload.unread_count)) {
        this.notificationsUnread = payload.unread_count;
      }
      const ts = Number.isFinite(payload.read_at) ? payload.read_at : Math.floor(Date.now() / 1000);
      if (payload.bulk) {
        for (const n of (this.notifications || [])) {
          if (n.read_at == null) {
            n.read_at = ts;
          }
        }
      } else if (payload.id) {
        const row = (this.notifications || []).find(n => n.id === payload.id);
        if (row && row.read_at == null) {
          row.read_at = ts;
        }
      }
    },
    _handleNotificationDeleted(payload) {
      if (!payload || !payload.id) {
        return;
      }
      const i = (this.notifications || []).findIndex(n => n.id === payload.id);
      if (i >= 0) {
        this.notifications.splice(i, 1);
      }
      if (Number.isFinite(payload.unread_count)) {
        this.notificationsUnread = payload.unread_count;
      }
    },
    _notificationPassesFilters(n) {
      if (!n) {
        return false;
      }
      if (this.notificationsFilterUnread && n.read_at != null) {
        return false;
      }
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
        if (this.notificationsUnread > 0) {
          this.notificationsUnread -= 1;
        }
      }
      try {
        const r = await fetch('/api/notifications/' + encodeURIComponent(id) + '/read', {
          method: 'POST',
        });
        if (!r.ok) {
          throw new Error(await r.text());
        }
        const d = await r.json();
        if (row && Number.isFinite(d.read_at)) {
          row.read_at = d.read_at;
        }
        if (Number.isFinite(d.unread_count)) {
          this.notificationsUnread = d.unread_count;
        }
      } catch (e) {
        // Roll back on failure so the operator sees the actual state.
        if (row) {
          row.read_at = prev;
        }
        if (prev == null) {
          this.notificationsUnread = (this.notificationsUnread || 0) + 1;
        }
        this.showToast(this.t('toasts.load_failed', { error: e.message }), 'error');
      }
    },
    async markAllNotificationsRead() {
      try {
        const r = await fetch('/api/notifications/read-all', { method: 'POST' });
        if (!r.ok) {
          throw new Error(await r.text());
        }
        const d = await r.json();
        const ts = Math.floor(Date.now() / 1000);
        for (const n of (this.notifications || [])) {
          if (n.read_at == null) {
            n.read_at = ts;
          }
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
};
