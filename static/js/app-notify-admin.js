// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,AnonymousFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ExceptionCaughtLocallyJS,JSReusedLocalVariable,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,RedundantLocalVariableJS,JSIgnoredPromiseFromCall,JSAsyncFunctionMissingAwait,JSMissingAwait
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection RegExpRedundantEscape,JSValidateTypes,HtmlUnknownTag,HtmlEmptyContent,HtmlEmptyTagsRecommendation,HtmlSelfClosedTag,JSReusedLocal,LocalVariableReusedJS,VoidExpressionJS,JSVoidExpression,RedundantLocalVariableJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Admin → Notifications surface — per-event opt-in matrix, per-medium
// master toggles, Apprise + Telegram + template editor.
//
// Distinct from the in-app notifications popup (`app-notifications-popup.js`).
// This module is the admin-side configuration: which events fire on which
// medium for which user, per-event template overrides, and the save flow.


export default {
  // in-flight flags for Admin tab Save buttons. Each toggles
  // around the corresponding save function so the button shows
  // "Saving…" + disabled state during the POST. Standardised
  // pattern matching hostStatsSaving — operator request
  // for visual + behavioural consistency across every Save action.
  settingsSaving: false,       // Admin → Notifications (saveSettings)
  // Per-channel Apprise test result chip — pre-fix there was only
  // the shared `notifyTestResult` driving both channels; each channel
  // now has its own inline chip via the dedicated /api/apprise/test
  // endpoint.
  appriseTestResult: null,
  // Notifications page-level Save-row test result. `testNotify`
  // populates this from POST /api/notify-test response. Drives the
  // result chip below the Save row + Test-before-Save gate via
  // `canSaveNotifications()`.
  notifyTestResult: null,
  // Last-passed-test snapshots (parallel to portainer / oidc /
  // asset patterns). Captured at the moment a /api/notify-test
  // returns ok. Compared to the current form snapshot at Save time
  // — any edit between test + save re-locks Save.
  _appriseLastPassedTest: '',
  // Dirty trackers — same pattern as `sshSettingsDirty` /
  // `hostsConfigDirty` / `hostStatsDirty()`. Each tab's Save button
  // shows the amber unsaved-changes ring + dot when its flag is
  // true. Marked by @input / @change on every relevant input;
  // Per-tab baselines for the unified dirty-tracking pattern. Each
  // baseline is a JSON snapshot string captured after loadSettings()
  // (and after a successful save). The matching `<X>Dirty()` getter
  // compares the current snapshot against the baseline so reverting
  // a typed-and-deleted edit clears the indicator — same UX as the
  // existing Profile / Asset Inventory / Providers tabs. Replaces
  // the older "set true on input, reset on save" boolean toggle
  // pattern that couldn't detect a revert.
  // Admin → Notifications tab strip (In-app / Apprise / Telegram).
  // Mirrors the host-stats provider-tab pattern. Persists to
  // localStorage so the operator returns to the same tab.
  notificationsTab: (() => {
    try {
      const v = localStorage.getItem('notificationsTab');
      if (v && ['app', 'apprise', 'telegram'].includes(v)) {
        return v;
      }
    } catch {
    }
    return 'app';
  })(),
  setNotificationsTab(name) {
    if (!this.NOTIFICATIONS_TAB_ORDER.includes(name)) {
      return;
    }
    this.notificationsTab = name;
    try {
      localStorage.setItem('notificationsTab', name);
    } catch {
    }
  },
  // Single source of truth for the Notifications tab strip order — same
  // pattern as `HOST_STATS_TAB_ORDER`. Adding a fourth medium (e.g.
  // future Discord webhook) means one entry here + one tuple entry in
  // the tab-strip x-for in `static/_partials/admin/notifications.html`.
  NOTIFICATIONS_TAB_ORDER: ['app', 'apprise', 'telegram'],
  // Arrow-key cycler for the Notifications tab strip. Mirrors
  // `cycleHostStatsTab`: ±1 step with wrap-around, focuses the
  // newly-active tab so the focus ring tracks selection per the
  // WAI-ARIA horizontal tablist pattern.
  cycleNotificationsTab(direction) {
    const order = this.NOTIFICATIONS_TAB_ORDER;
    const cur = order.indexOf(this.notificationsTab);
    const i = cur < 0 ? 0 : cur;
    const next = (i + (direction > 0 ? 1 : order.length - 1)) % order.length;
    this.setNotificationsTab(order[next]);
    this.$nextTick(() => {
      const el = document.getElementById('notify-tab-' + order[next]);
      if (el && typeof el.focus === 'function') {
        el.focus();
      }
    });
  },
  // Per-user notification toggle disable gate. Returns true
  // when the admin has globally disabled this event — UI greys out
  // the user-side checkbox and shows a "disabled by admin" tooltip.
  // The data model only narrows DOWN from the admin layer, so the
  // backend also rejects an opt-IN attempt for a globally-disabled
  // event with a 400.
  userNotifyEventDisabledByAdmin(eventKey) {
    if (!this.me || !this.me.notify_events_admin) {
      return false;
    }
    const bare = (eventKey || '').replace(/^notify_event_/, '');
    return this.me.notify_events_admin[bare] === false;
  },

  // Wrapper — fires `/api/notify-test` so an admin can verify
  // Apprise / in-app delivery from the AI palette. Same uniform
  // shape as `testAssetInventoryConnection`. Backend POSTs a
  // dummy notification through every enabled medium and returns
  // per-medium ok / detail.
  async testApprise() {
    // Per-channel Apprise probe — fires ONLY the Apprise
    // medium via the dedicated /api/apprise/test endpoint. Inline
    // result chip via `appriseTestResult` mirrors the canonical
    // Test pattern (Portainer / Beszel / Pulse / Webmin / Telegram).
    // Snapshot captured BEFORE the request so any edit between Test
    // and Save still re-locks Save through `canSaveApprise`.
    this.appriseTestResult = {pending: true};
    const appriseSnap = this._appriseTestSnapshot();
    try {
      const r = await fetch('/api/apprise/test', {method: 'POST'});
      const j = await r.json().catch(() => ({}));
      const ok = r.ok && !!j.ok;
      this.appriseTestResult = {
        pending: false,
        ok,
        detail: j.detail || this.t(ok ? 'toasts_extra.test_result_ok' : 'toasts_extra.test_result_failed'),
        status: j.status || 0,
        _ts: Date.now(),
      };
      if (ok) {
        this._appriseLastPassedTest = appriseSnap;
        this.recordTestSuccess('apprise');
      }
    } catch (_) {
      this.appriseTestResult = {pending: false, ok: false, detail: this.t('toasts.network_error'), _ts: Date.now()};
    }
  },
  async saveSettings() {
    if (this.settingsSaving) {
      return;
    }  // guard against double-click
    this.settingsSaving = true;
    try {
      // Per-event notification toggles are stored on
      // `settings` as JS booleans (resolved server-side by
      // get_setting_bool) but the SettingsIn validator expects
      // "true"/"false"/""(=clear) strings. Normalise on the way out
      // so the round-trip stays clean and a single Save POST covers
      // BOTH the Apprise URL/tag AND the event grid in this tab.
      const payload = {...this.settings};
      for (const k of (this.notifyEventKeys || [])) {
        if (k in payload) {
          payload[k] = payload[k] ? 'true' : 'false';
        }
      }
      // Per-medium master switches share the same string-on-the-wire
      // contract (true/false/clear) as the per-event toggles above.
      for (const k of (this.notifyMediumKeys || [])) {
        if (k in payload) {
          payload[k] = payload[k] ? 'true' : 'false';
        }
      }
      // Boolean fields whose SettingsIn declarations are `Optional[str]`
      // (the canonical string-on-the-wire contract every other boolean
      // setting uses). Without this normalisation the SPA POSTs the
      // raw JS bool and Pydantic rejects with `string_type`. Each
      // entry is the in-form field name on `this.settings`.
      const boolToStringFields = [
        'telegram_verify_tls',
        'telegram_listener_enabled',
        'telegram_allow_destructive',
        'swarm_autoheal_bootstrap_enabled',
      ];
      for (const k of boolToStringFields) {
        if (k in payload && typeof payload[k] === 'boolean') {
          payload[k] = payload[k] ? 'true' : 'false';
        }
      }
      // `ai_fallback_order` is an Array on the form but SettingsIn
      // declares `Optional[str]` (CSV-on-the-wire). The AI section's
      // own Save path normalises this via `.join(',')`; the generic
      // saveSettings path also ships it on `{...this.settings}`, so
      // we must coerce here too.
      if (Array.isArray(payload.ai_fallback_order)) {
        payload.ai_fallback_order = payload.ai_fallback_order
          .filter(Boolean).join(',');
      }
      const r = await fetch('/api/settings', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      // Re-capture per-tab baselines under the unified dirty-tracking
      // pattern — the Notifications tab (Apprise + Open-Meteo) is the
      // most common caller of saveSettings and lands the whole
      // settings object. Capturing both here covers the multi-section
      // save path; single-section saves (savePortainerSettings,
      // saveOidcSettings) call loadSettings which re-captures via the
      // central baseline-update block.
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
        const rm = await fetch('/api/me', {cache: 'no-store'});
        if (rm.ok) {
          const fresh = await rm.json();
          if (fresh && fresh.authenticated) {
            for (const k of Object.keys(fresh)) {
              this.me[k] = fresh[k];
            }
          }
        }
      } catch (_) { /* non-fatal — the next page load will catch up */
      }
      this.showToast(this.t('toasts.settings_saved'));
    } catch (e) {
      this.showToast(this.t('toasts.load_failed', {error: e.message}), 'error');
    } finally {
      this.settingsSaving = false;
    }
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
    'notify_event_swarm_agent_recovered',
    'notify_event_prune_success',
    'notify_event_prune_failure',
    'notify_event_user_login',
    'notify_event_host_paused',
    'notify_event_port_scan_new_port',
    'notify_event_http_probe_failure',
    'notify_event_service_probe_failure',
    'notify_event_totp_audit_log_failed',
    'notify_event_overlay_cleanup_success',
    'notify_event_overlay_cleanup_failure',
    'notify_event_prayer_reminder',
  ],
  // Per-medium master switches. Mirrors `NOTIFY_MEDIUM_NAMES` in
  // logic/ops.py. Adding a third medium adds one entry here +
  // NOTIFY_MEDIUM_NAMES + SettingsIn + api_get_settings hydration
  // (the project conventions "Settings hydration drift class" four-place audit).
  notifyMediumKeys: [
    'notify_medium_app',
    'notify_medium_apprise',
    'notify_medium_telegram',
  ],
  // Group rows for the events grid — label key + (success_key,
  // failure_key) pair. Drives the markup render so the table stays
  // declarative.
  notifyEventGroups: [
    {label: 'stack_update', success: 'notify_event_stack_update_success', failure: 'notify_event_stack_update_failure'},
    {label: 'container_update', success: 'notify_event_container_update_success', failure: 'notify_event_container_update_failure'},
    {label: 'container_restart', success: 'notify_event_container_restart_success', failure: 'notify_event_container_restart_failure'},
    {label: 'container_remove', success: 'notify_event_container_remove_success', failure: 'notify_event_container_remove_failure'},
    {label: 'service_restart', success: 'notify_event_service_restart_success', failure: 'notify_event_service_restart_failure'},
    {label: 'swarm_agent_restart', success: 'notify_event_swarm_agent_restart_success', failure: 'notify_event_swarm_agent_restart_failure'},
    {label: 'prune', success: 'notify_event_prune_success', failure: 'notify_event_prune_failure'},
  ],
  // Sampler-style events that don't have a paired success/failure
  // shape but DO surface an unhealthy state. The
  // `swarm_agent_health` schedule kind fires `swarm_agent_unhealthy`
  // when its detection threshold trips and the action is "notify".
  notifyHealthEvents: [
    {label: 'swarm_agent_unhealthy', key: 'notify_event_swarm_agent_unhealthy'},
    {label: 'swarm_agent_recovered', key: 'notify_event_swarm_agent_recovered'},
  ],
  // Security events — single-toggle per event (no success/failure
  // pair like ops events). Rendered as a separate row beneath the
  // ops-events grid. Sampler events get their own group below
  // because "host sampling auto-paused" is not a security signal.
  notifySecurityEvents: [
    {label: 'user_login', key: 'notify_event_user_login'},
  ],
  notifySamplerEvents: [
    {label: 'host_paused', key: 'notify_event_host_paused'},
    {label: 'http_probe_failure', key: 'notify_event_http_probe_failure'},
    {label: 'service_probe_failure', key: 'notify_event_service_probe_failure'},
  ],
  // Informational / scheduled events — not a problem signal (Health) nor a
  // security event, so they get their own category. Single-toggle per event
  // like the sampler / security events.
  notifyInfoEvents: [
    {label: 'prayer_reminder', key: 'notify_event_prayer_reminder'},
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
      rows.push({key: g.success, label: g.label, kind: 'success'});
      rows.push({key: g.failure, label: g.label, kind: 'failure'});
    }
    return rows;
  },
  // ---- Categorised notification list (Profile → Notifications) ----
  // Groups every event into one of three buckets so the per-event
  // matrix scales as more events are added without overwhelming the
  // user with one flat 30-row grid. Each category is rendered as a
  // collapsible card; the category header carries:
  // - icon + label
  // - active-count badge (`X / N enabled`)
  // - one chip per medium showing that category's column state
  //   (all / partial / none) — clicking flips every event in the
  //   category for that medium
  // - chevron toggling the row list visible
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
    if (this._notifyCategoriesCache) {
      return this._notifyCategoriesCache;
    }
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
      ops.push({key: g.success, label: g.label, kind: 'success'});
      ops.push({key: g.failure, label: g.label, kind: 'failure'});
      opsGroups.push({
        label: g.label,
        success_key: g.success,
        failure_key: g.failure,
      });
    }
    const health = [];
    for (const e of (this.notifySamplerEvents || [])) {
      health.push({key: e.key, label: e.label, kind: null});
    }
    for (const e of (this.notifyHealthEvents || [])) {
      health.push({key: e.key, label: e.label, kind: null});
    }
    const security = [];
    for (const e of (this.notifySecurityEvents || [])) {
      security.push({key: e.key, label: e.label, kind: null});
    }
    const info = [];
    for (const e of (this.notifyInfoEvents || [])) {
      info.push({key: e.key, label: e.label, kind: null});
    }
    this._notifyCategoriesCache = [
      {
        id: 'operations',
        icon: 'icon-activity',
        label_key: 'profile.notifications.cat_operations',
        desc_key: 'profile.notifications.cat_operations_desc',
        rows: ops,
        groups: opsGroups,
      },
      {
        id: 'health',
        icon: 'icon-alert-triangle',
        label_key: 'profile.notifications.cat_health',
        desc_key: 'profile.notifications.cat_health_desc',
        rows: health,
        groups: null,
      },
      {
        id: 'security',
        icon: 'icon-shield',
        label_key: 'profile.notifications.cat_security',
        desc_key: 'profile.notifications.cat_security_desc',
        rows: security,
        groups: null,
      },
      {
        id: 'info',
        icon: 'icon-info',
        label_key: 'profile.notifications.cat_info',
        desc_key: 'profile.notifications.cat_info_desc',
        rows: info,
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
    if (!q) {
      return rows;
    }
    const out = [];
    for (const r of rows) {
      const label = (this.t('admin.notifications.events.' + r.label) || '').toLowerCase();
      const kind = r.kind ? (this.t('admin.notifications.events.' + r.kind) || '').toLowerCase() : '';
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
    if (!q) {
      return groups;
    }
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
      if (this.userNotifyEventDisabledByAdmin(r.key)) {
        continue;
      }
      if (this.userNotifyEventValue(r.key, medium)) {
        on += 1;
      } else {
        off += 1;
      }
    }
    if (on === 0 && off === 0) {
      return 'none';
    }
    if (off === 0) {
      return 'all';
    }
    if (on === 0) {
      return 'none';
    }
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
    if (rows.length === 0) {
      return false;
    }
    let baseEvents;
    try {
      baseEvents = (JSON.parse(this._profileBaseline || '{}').notify_events) || {};
    } catch {
      return false;
    }
    for (const r of rows) {
      if (this.userNotifyEventDisabledByAdmin(r.key)) {
        continue;
      }
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
      if (cur !== base) {
        return true;
      }
    }
    return false;
  },
  notifyCategoryEnabledCount(cat) {
    const rows = (cat && cat.rows) || [];
    let total = 0, on = 0;
    const mediums = this.notifyMediumNames();
    for (const r of rows) {
      if (this.userNotifyEventDisabledByAdmin(r.key)) {
        continue;
      }
      total += 1;
      // "Enabled" for the count = at least one medium routes the event.
      for (const m of mediums) {
        if (this.userNotifyEventValue(r.key, m)) {
          on += 1;
          break;
        }
      }
    }
    return {on, total};
  },
  // Toggle every event in a category for ONE medium. Skips
  // admin-disabled events. Click on the medium chip in the
  // category header.
  toggleNotifyCategoryMedium(catId, medium, nextValue) {
    const cat = (this.notifyCategories() || []).find(c => c.id === catId);
    if (!cat) {
      return;
    }
    // Short-circuit if the medium is admin-disabled globally.
    // The chip's CSS already shows strikethrough + cursor:
    // not-allowed, but the click handler still fires unless we
    // gate here — so an operator click on a disabled chip would
    // silently mutate user prefs that don't take effect until
    // the admin re-enables the medium globally, leaving stale
    // routing landing without the operator noticing. Both
    // helpers (Medium toggle + the All toggle) gate on the
    // same predicate.
    if (!this.notifyMediumIsGloballyEnabled(medium)) {
      return;
    }
    // Resolve the next value: if not supplied, flip based on
    // current state (all → none, partial/none → all).
    let v = nextValue;
    if (v === undefined) {
      const state = this.notifyCategoryStateForMedium(cat, medium);
      v = (state !== 'all');
    }
    const f = this.profileForm || {};
    if (!f.notify_events) {
      f.notify_events = {};
    }
    const mediums = this.notifyMediumNames();
    for (const r of cat.rows) {
      if (this.userNotifyEventDisabledByAdmin(r.key)) {
        continue;
      }
      const bare = this._bareEventName(r.key);
      let slot = f.notify_events[bare];
      if (!slot || typeof slot !== 'object') {
        const prev = !!slot;
        slot = {};
        for (const mm of mediums) {
          slot[mm] = prev;
        }
        f.notify_events[bare] = slot;
      }
      slot[medium] = !!v;
    }
  },
  // Toggle EVERY medium for every event in the category — the
  // "All" master chip per category.
  toggleNotifyCategoryAll(catId, nextValue) {
    const cat = (this.notifyCategories() || []).find(c => c.id === catId);
    if (!cat) {
      return;
    }
    let v = nextValue;
    if (v === undefined) {
      // Flip based on whether ANY medium is "all" — defaults to
      // turning everything ON when the category is mixed/off.
      const mediums = this.notifyMediumNames();
      const allOn = mediums.every(m => this.notifyCategoryStateForMedium(cat, m) === 'all');
      v = !allOn;
    }
    const f = this.profileForm || {};
    if (!f.notify_events) {
      f.notify_events = {};
    }
    const mediums = this.notifyMediumNames();
    // Filter the medium list to only globally-enabled channels —
    // skip flipping prefs for an admin-disabled medium since that
    // setting can't take effect until the admin re-enables and
    // we don't want stale routing silently sitting on the user's
    // profile. Same gate as toggleNotifyCategoryMedium.
    const liveMediums = mediums.filter(m => this.notifyMediumIsGloballyEnabled(m));
    for (const r of cat.rows) {
      if (this.userNotifyEventDisabledByAdmin(r.key)) {
        continue;
      }
      const bare = this._bareEventName(r.key);
      // Preserve any pre-existing per-medium values for
      // admin-disabled channels — only mutate the live ones. This
      // keeps the operator's prior choice on a disabled medium
      // intact for when the admin re-enables it.
      const existing = (f.notify_events[bare] && typeof f.notify_events[bare] === 'object')
        ? f.notify_events[bare] : {};
      const slot = {...existing};
      for (const m of liveMediums) {
        slot[m] = !!v;
      }
      f.notify_events[bare] = slot;
    }
  },
  notifyCategoryAllState(cat) {
    const mediums = this.notifyMediumNames();
    const states = mediums.map(m => this.notifyCategoryStateForMedium(cat, m));
    if (states.every(s => s === 'all')) {
      return 'all';
    }
    if (states.every(s => s === 'none')) {
      return 'none';
    }
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
    for (const c of this.notifyCategories()) {
      next[c.id] = v;
    }
    this.notifyCategoryExpanded = next;
  },
  // Convenience-button handlers for the per-event grid. They mutate
  // settings in-place; the smart-getter dirty pattern picks the
  // change up automatically. Save still goes through the existing
  // Apprise Save button (saveSettings).
  setAllNotifyEvents(value) {
    const v = !!value;
    for (const k of this.notifyEventKeys) {
      this.settings[k] = v;
    }
  },
  setNotifyEventsErrorsOnly() {
    // Errors-only = failure true, success false for every group.
    for (const g of this.notifyEventGroups) {
      this.settings[g.success] = false;
      this.settings[g.failure] = true;
    }
  },
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
    if (!list) {
      return true;
    }  // optimistic — wait for /api/me
    const row = list.find(m => m.name === name);
    return row ? !!row.enabled : true;
  },
  // Bulk-pattern picker state — operator selects one medium for
  // "all success events" and another for "all failure events" via
  // the picker rendered under the Errors-only button. Empty string
  // = "leave the corresponding side untouched". Persisted in-memory
  // only (the row's persistence happens via the PATCH that
  // saveProfile fires on the next save).
  // Profile → Notifications redesign state. Search filters
  // event rows live; per-category expand/collapse map keyed by
  // category id (default-expanded for 'operations' so first-time
  // users see rows immediately).
  notifySearchQuery: '',
  notifyCategoryExpanded: {},
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
    preview: {rendered_title: '', rendered_body: '', used_placeholders: [], unknown_placeholders: []},
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
      if (g.success === fullKey) {
        return {label: g.label, kind: 'success', key: fullKey};
      }
      if (g.failure === fullKey) {
        return {label: g.label, kind: 'failure', key: fullKey};
      }
    }
    for (const e of (this.notifyHealthEvents || [])) {
      if (e.key === fullKey) {
        return {label: e.label, kind: null, key: fullKey};
      }
    }
    for (const e of (this.notifySecurityEvents || [])) {
      if (e.key === fullKey) {
        return {label: e.label, kind: null, key: fullKey};
      }
    }
    for (const e of (this.notifySamplerEvents || [])) {
      if (e.key === fullKey) {
        return {label: e.label, kind: null, key: fullKey};
      }
    }
    // Fallback: render the bare event name verbatim if our static
    // arrays don't know about it (audit gate already logs a WARN
    // line in that case).
    return {label: bareEventName, kind: null, key: fullKey};
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
        if (window.Swal) {
          Swal.fire({icon: 'error', text: this.t('admin.notify_templates.unknown_event_error', {event: bareEventName})});
        }
        return;
      }
      this.notifyTemplateEvent = this._resolveNotifyTemplateEvent(bareEventName);
      const title = ev.title_is_default ? '' : (ev.title || '');
      const body = ev.body_is_default ? '' : (ev.body || '');
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
        preview: {rendered_title: '', rendered_body: '', used_placeholders: [], unknown_placeholders: []},
        saving: false,
        testing: false,
      };
      // First preview render — server side so we get the same
      // placeholder analysis the operator will see during edits.
      // Awaited so the modal opens with the preview already populated
      // instead of a momentary empty body. Caller is async so the
      // awaiting is cheap.
      await this.refreshNotifyTemplatePreview();
    } catch (e) {
      if (window.Swal) {
        Swal.fire({icon: 'error', text: this.t('admin.notify_templates.load_failed', {error: String(e.message || e)})});
      }
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
      preview: {rendered_title: '', rendered_body: '', used_placeholders: [], unknown_placeholders: []},
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
      || (e.body || '') !== (e.body_baseline || '');
  },
  resetNotifyTemplateEditor() {
    // Resets the editor's title + body to empty (which the backend
    // treats as "fall back to default"). Operator still has to click
    // Save to commit; before that, the live preview shows what the
    // default looks like (matches the baseline-hydrated `_default`
    // strings).
    this.notifyTemplateEditor.title = '';
    this.notifyTemplateEditor.body = '';
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
      const end = ref.selectionEnd != null ? ref.selectionEnd : cur.length;
      this.notifyTemplateEditor[field] = cur.slice(0, start) + insert + cur.slice(end);
      // Restore the caret position to AFTER the insert so subsequent
      // chip clicks chain naturally.
      this.$nextTick(() => {
        if (ref) {
          ref.focus();
          const newPos = start + insert.length;
          try {
            ref.setSelectionRange(newPos, newPos);
          } catch (_e) { /* ignore */
          }
        }
      });
    }
    this.refreshNotifyTemplatePreview();
  },
  async refreshNotifyTemplatePreview() {
    const e = this.notifyTemplateEditor;
    if (!e || !e.event) {
      return;
    }
    // Send the IN-PROGRESS strings (or empty → server falls back to
    // default at render time). Server is the single source of truth
    // for placeholder validation (curated whitelist lives in
    // logic/ops.py); avoid duplicating the regex on the client.
    const titleStr = e.title || e.title_default || '';
    const bodyStr = e.body || e.body_default || '';
    try {
      const r = await fetch(
        '/api/admin/notify-templates/' + encodeURIComponent(e.event) + '/preview',
        {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({title: titleStr, body: bodyStr}),
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
            rendered_body: safe(bodyStr),
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
        rendered_body: d.rendered_body || '',
        used_placeholders: d.used_placeholders || [],
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
    if (!e || !e.event || e.readOnly) {
      return;
    }
    e.saving = true;
    try {
      const r = await fetch(
        '/api/admin/notify-templates/' + encodeURIComponent(e.event),
        {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({title: e.title || '', body: e.body || ''}),
        },
      );
      if (!r.ok) {
        throw new Error('HTTP ' + r.status);
      }
      const d = await r.json();
      // Refresh the editor's baseline + defaults from the server
      // response so the dirty flag clears AND the placeholder hints
      // reflect what's now in the DB.
      this.notifyTemplateEditor.title = d.title_is_default ? '' : (d.title || '');
      this.notifyTemplateEditor.body = d.body_is_default ? '' : (d.body || '');
      this.notifyTemplateEditor.title_default = d.title_default || '';
      this.notifyTemplateEditor.body_default = d.body_default || '';
      this.notifyTemplateEditor.title_baseline = this.notifyTemplateEditor.title;
      this.notifyTemplateEditor.body_baseline = this.notifyTemplateEditor.body;
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
      if (window.Swal) {
        Swal.fire({icon: 'error', text: this.t('admin.notify_templates.save_failed', {error: String(err.message || err)})});
      }
    } finally {
      this.notifyTemplateEditor.saving = false;
    }
  },
  async testNotifyTemplate() {
    const e = this.notifyTemplateEditor;
    if (!e || !e.event || e.readOnly) {
      return;
    }
    e.testing = true;
    try {
      const r = await fetch(
        '/api/admin/notify-templates/' + encodeURIComponent(e.event) + '/test',
        {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({title: e.title || '', body: e.body || ''}),
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
      this.notifyTemplateEditor.body_baseline = this.notifyTemplateEditor.body || '';
    } catch (err) {
      if (window.Swal) {
        Swal.fire({icon: 'error', text: this.t('admin.notify_templates.test_failed', {error: String(err.message || err)})});
      }
    } finally {
      this.notifyTemplateEditor.testing = false;
    }
  },
  // No-op stubs kept so any existing @input / @change bindings that
  // call mark<X>Dirty() don't throw. The smart getters re-evaluate
  // automatically on form changes via Alpine reactivity, so these
  // calls are now unnecessary but harmless. Removing the markup
  // bindings is a separate cleanup.
  markAppriseDirty() {
  },
  // Per-medium "test-relevant fields" snapshots. The shape is a
  // subset of the dirty snapshot — only the inputs that affect
  // whether the test would PASS (URL / credentials / chat id /
  // master toggle). Tunables / per-event toggles / aliases are
  // EXCLUDED — they don't affect connectivity.
  _appriseTestSnapshot() {
    const s = this.settings || {};
    return JSON.stringify({
      enabled: !!s.apprise_enabled,
      url: (s.apprise_url || '').trim(),
      tag: (s.apprise_tag || '').trim(),
    });
  },
  canSaveApprise() {
    // Save is gated only when Apprise is enabled. Disabled apprise
    // saves freely (the form values aren't going anywhere).
    if (!(this.settings || {}).apprise_enabled) {
      return true;
    }
    return this._appriseLastPassedTest === this._appriseTestSnapshot()
      && !!this._appriseLastPassedTest;
  },
  canSaveNotifications() {
    // Composite gate used by the PROVIDERS Save button only. Save
    // is unblocked when every enabled medium has a fresh passing
    // test. Per-event Save (sibling section) does NOT consult this
    // gate — per-event toggles are local to OmniGrid and have no
    // round-trip to test.
    return this.canSaveApprise() && this.canSaveTelegram();
  },
};
