// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,JSUnusedGlobalSymbols,JSUnusedLocalSymbols,ElementNotExported
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName
// noinspection OverlyComplexBooleanExpressionJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression,NegatedIfStatementJS
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSIfStatementsCanBeSimplified,IfStatementSimplifyable,RedundantIfStatementJS,RedundantLocalVariableJS,JSReusedLocalVariable
// noinspection HtmlUnknownTag,HtmlEmptyContent,HtmlEmptyTagsRecommendation,InnerHTMLJS,VoidExpressionJS,JSVoidExpression
// noinspection CssConvertColorToRgb,CssReplaceWithShorthandSafely,RegExpRedundantEscape,AnonymousCapturingGroupJS,RegExpAnonymousGroup
// noinspection JSDeprecatedSymbols,DOMNotInherited,JSPotentiallyInvalidUsageOfThis,JSPossiblyAssignedToNullVariable,JSReferencingArgumentsOutsideOfFunction
// noinspection JSForIIterationOverNonNumericKeyJS,JSHint
// Comprehensive per-inspection suppressions mirror app-ai-admin.js.
// SPA-wide idioms covered: constants on the right of comparisons;
// anonymous arrow callbacks; chained map+filter; ternaries; magic
// numbers for unit-conversion (60/3600/86400 seconds, percentage
// thresholds 25/30/50/85/90/99); short uppercase locals; Alpine-
// called methods PyCharm can't trace; `throw new Error(...)` for
// unified error handling; `for...in` over the persisted SSE event-
// type map (operator-controlled key set, safe without hasOwnProperty
// because the map is constructed fresh per session).
/* global Alpine, Swal, I18N, t, OG_VERSION */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA real-time event stream + node items + bulk-palette helpers
//
// SPLIT FROM `app.js`: that file crossed 14k lines and was carrying
// every Alpine component method in one inline `{ ... }` literal.
// Each extracted chunk becomes an `export default { ... }` module
// merged back into the component via `_mergeKeepDescriptors`. Cross-
// chunk method references (`this.X`) keep working without any
// binding gymnastics because they all merge onto the same target
// object before Alpine instantiation.

// Per-flush node->items index for the Nodes view. itemsForNode(host) used to
// this.items.filter() on EVERY call, and the Nodes view calls it once per
// node card (via nodeStats) PLUS once per spark (nodeSparkPoints) — O(nodes *
// items) per flush. This builds the bucket map ONCE per flush by iterating
// this.items a single time, making itemsForNode an O(1) lookup. Cleared on
// the next microtask (same zero-staleness contract as the filteredHosts
// memo). Module-scope singleton — exactly one app() instance.
let _nodeItemsIndexCache = null;
let _nodeItemsIndexScheduled = false;

function _clearNodeItemsIndexCache() {
  _nodeItemsIndexCache = null;
  _nodeItemsIndexScheduled = false;
}

// flush-memoize the EXPENSIVE Stacks/Services getters. Each is pure
// within a synchronous reactive flush (depends only on this.items /
// this.stacks / search / statusFilter / healthFilter / sortField / sortDir,
// none of which change mid-flush) but was recomputed AND re-allocated on every
// access — filteredStacks spreads every stack + filters its items;
// filteredItems filters; sortedFiltered spreads + sorts — and they're read
// multiple times per flush as x-for sources. One module-scope per-flush cache,
// cleared on the next microtask (zero staleness, same contract as the
// filteredHosts memo). `undefined` is the not-cached sentinel.
//
// IMPORTANT — these three are safe to memo BECAUSE their dominant consumer is a
// heavy x-for (the Stacks / Services table) that reads the getter granularly
// every flush, so Alpine's fine-grained reactivity re-subscribes the render
// effect to this.items on every flush. `counts` is DELIBERATELY excluded: all
// its consumers are lightweight (nav badge x-show, filter-chip x-text, title)
// and never iterate this.items, so a cache-hit early-return would return before
// touching this.items and Alpine would never register the dependency for those
// effects — the badge would FREEZE at its last value after an in-place items
// reconcile. `counts` therefore always-computes (see its getter).
const _stacksFlushCache = {
  filteredStacks: undefined,
  filteredItems: undefined,
  sortedFiltered: undefined,
};
let _stacksFlushScheduled = false;

function _scheduleStacksFlushClear() {
  if (_stacksFlushScheduled) {
    return;
  }
  _stacksFlushScheduled = true;
  queueMicrotask(() => {
    _stacksFlushCache.filteredStacks = undefined;
    _stacksFlushCache.filteredItems = undefined;
    _stacksFlushCache.sortedFiltered = undefined;
    _stacksFlushScheduled = false;
  });
}

export default {
  // ===================================================================
  // Real-time event stream
  // ===================================================================
  // EventSource connects to /api/events on cookie-authed browsers and
  // dispatches one handler per server-side event type. Every existing
  // poll loop (pollOps / pollStats / refresh / loadHosts / loadHistory)
  // checks `_sseConnected` and skips its self-scheduled work while the
  // stream is healthy. EventSource handles reconnect natively; we only
  // track the connection state for the toolbar indicator + poll-gate.
  //
  // Reactive updates use the existing in-place reconcile contract —
  // never reassign reactive arrays from an event handler (would tear
  // every chart SVG / <details> / inline-style node down on each
  // event, defeating the entire purpose of moving from poll → push).
  // explicit disconnect so the cadence picker can fully turn
  // SSE off when the operator chooses "Off" or an interval. Without
  // this, picking Off left the SSE pipe alive and the Live pill
  // stayed green even though the operator's mental model is "no
  // updates at all".
  _disconnectSSE() {
    if (this._sse) {
      console.log('[live] SSE disconnect: closing stream (operator picked Off/interval)');
      try {
        this._sse.close();
      } catch {
      }
      this._sse = null;
    }
    this._sseConnected = false;
    this._sseLastEventTs = 0;
  },
  // Multi-tab activity ------------------------------------------------
  // Snapshot of THIS tab's current location for the heartbeat payload.
  // Walks the SPA's reactive view-state and emits a compact dict that
  // the backend stamps into the in-process tab registry + broadcasts to
  // sibling tabs.
  _tabActivitySnapshot() {
    const drawerHostId = (this.drawerHost && this.drawerHost.id) || null;
    // Operator-friendly title — i18n'd so non-en operators see
    // localised text in the topbar tab-activity popover. Top-level
    // view labels come from the existing `nav.*` keys the topbar
    // sidebar already consumes; the leg-join uses a dedicated
    // `topbar.tabs.path_separator` key so a locale that wants a
    // different glyph (or a plain dash) can override it without
    // touching this code.
    const v = (this.view || '').toString();
    const _navLabel = (key) => {
      const out = this.t('nav.' + key);
      // Fallback to capitalised view id if the locale doesn't define
      // the key — keeps the popover useful even on incomplete bundles.
      return (out && out !== 'nav.' + key) ? out : (key.charAt(0).toUpperCase() + key.slice(1));
    };
    const _join = (leg, target) => {
      const tmpl = this.t('topbar.tabs.path_separator', {leg, target});
      return (tmpl && tmpl !== 'topbar.tabs.path_separator') ? tmpl : (leg + ' → ' + target);
    };
    let title = v ? _navLabel(v) : '';
    if (v === 'admin' && this.adminTab) {
      title = _join(_navLabel('admin'), this.adminTab);
    } else {
      if (v === 'settings' && this.settingsSection) {
        title = _join(_navLabel('settings'), this.settingsSection);
      } else {
        if (v === 'stats' && this.statsTab) {
          title = _join(_navLabel('stats'), this.statsTab);
        } else {
          if (v === 'hosts' && drawerHostId) {
            title = _join(_navLabel('hosts'), drawerHostId);
          }
        }
      }
    }

    // Richer state — filter chip state + selected hosts + selected
    // items. Lets the popover show "Hosts → 12 selected, paused
    // filter on" instead of just "Hosts". Powers the "Reproduce
    // here" handoff so the operator on the phone can copy the
    // desktop tab's filter/drawer state into the current tab.
    const _filters = {
      search: (this.search || '').toString() || null,
      statusFilter: (this.statusFilter || '').toString() || null,
      healthFilter: (this.healthFilter || '').toString() || null,
      hostsProblemFilter: !!this.hostsProblemFilter || null,
      hostsHideUnconfigured: !!this.hostsHideUnconfigured || null,
      hostsProviderFilter: (this.hostsProviderFilter
        && this.hostsProviderFilter.size)
        ? Array.from(this.hostsProviderFilter)
        : null,
    };
    // Strip null/false entries so the snapshot stays compact on the
    // wire (heartbeat fires every 30s — keep the payload small).
    const filters = {};
    for (const k of Object.keys(_filters)) {
      if (_filters[k]) {
        filters[k] = _filters[k];
      }
    }
    const selectionIds = Array.isArray(this.selected)
      ? this.selected.slice(0, 50)  // cap to bound the heartbeat size
      : [];
    const _hasRichState = Object.keys(filters).length > 0
      || selectionIds.length > 0;

    return {
      view: v || null,
      drawer_host: drawerHostId,
      drawer_item: (this.drawerItem && (this.drawerItem.id || this.drawerItem.name)) || null,
      admin_tab: (this.adminTab || '').toString() || null,
      settings_section: (this.settingsSection || '').toString() || null,
      stats_tab: (this.statsTab || '').toString() || null,
      title: title || null,
      // Compact richer state — only emitted when actually populated
      // so idle tabs don't waste heartbeat bytes.
      filters: Object.keys(filters).length ? filters : null,
      selection: selectionIds.length ? selectionIds : null,
      // Pre-formatted summary label the popover renders if the
      // operator wants more than the bare title. e.g. "Hosts → 12
      // selected · paused filter on".
      rich_label: _hasRichState ? this._tabActivityRichLabel(filters, selectionIds) : null,
    };
  },

  // Render a richer popover label for a snapshot — e.g.
  // "Hosts → 12 selected · paused filter on". Powers the
  // operator's "what's open on the other tab?" glance + the
  // Reproduce-here handoff. All fragments go through `t()` so
  // non-en locales see the localised count + filter names.
  _tabActivityRichLabel(filters, selection) {
    const fragments = [];
    if (selection && selection.length) {
      fragments.push(this.t('topbar.tabs.rich.selection', {count: selection.length}) || (selection.length + ' selected'));
    }
    if (filters.hostsProblemFilter) {
      fragments.push(this.t('topbar.tabs.rich.problem_filter') || 'problem filter');
    }
    if (filters.hostsHideUnconfigured) {
      fragments.push(this.t('topbar.tabs.rich.hide_unconfigured_filter') || 'hide unconfigured');
    }
    if (filters.hostsProviderFilter && filters.hostsProviderFilter.length) {
      fragments.push(this.t('topbar.tabs.rich.provider_filter', {providers: filters.hostsProviderFilter.join(', ')})
        || ('providers: ' + filters.hostsProviderFilter.join(', ')));
    }
    if (filters.statusFilter) {
      fragments.push(this.t('topbar.tabs.rich.status_filter', {status: filters.statusFilter})
        || ('status: ' + filters.statusFilter));
    }
    if (filters.healthFilter) {
      fragments.push(this.t('topbar.tabs.rich.health_filter', {health: filters.healthFilter})
        || ('health: ' + filters.healthFilter));
    }
    if (filters.search) {
      fragments.push(this.t('topbar.tabs.rich.search', {query: filters.search})
        || ('search: ' + filters.search));
    }
    return fragments.join(' · ');
  },

  // Device descriptor renderers — drive the small "📱 iPhone · Safari"
  // chip in the tab-activity popover so the operator can tell which
  // machine the OTHER tab is on. Backend stamps `device =
  // {form_factor, platform, browser, ua}` on every heartbeat entry
  // (see `_parse_tab_activity_device` in main.py); these helpers
  // map that descriptor into renderable text + an emoji prefix.
  _tabActivityDeviceEmoji(device) {
    if (!device || typeof device !== 'object') {
      return '';
    }
    const ff = String(device.form_factor || '').toLowerCase();
    if (ff === 'mobile') {
      return '📱';
    }
    if (ff === 'tablet') {
      return '💻';
    }
    // 'desktop' or anything else falls through to the desktop glyph
    // — better than rendering no emoji at all (operator can still
    // disambiguate via the platform / browser label).
    return '🖥️';
  },
  _tabActivityDeviceLabel(device) {
    if (!device || typeof device !== 'object') {
      return '';
    }
    // Platform + browser pair via the i18n bundle so non-en locales
    // get localised labels. Backend tags are stable English keys
    // (`iOS` / `Mac` / `Chrome` / etc.) — the i18n key encodes the
    // same as a lower-cased slug under `topbar.tabs.device.platform.*`
    // / `topbar.tabs.device.browser.*`. Fallback to the raw English
    // tag when the locale doesn't define the key (forward-compat
    // for novel UA detection cases).
    const platformKey = String(device.platform || 'Other').toLowerCase().replace(/[^a-z0-9]+/g, '_');
    const browserKey = String(device.browser || 'Other').toLowerCase().replace(/[^a-z0-9]+/g, '_');
    const platformLabel = this.t('topbar.tabs.device.platform.' + platformKey);
    const browserLabel = this.t('topbar.tabs.device.browser.' + browserKey);
    const p = (platformLabel && platformLabel !== 'topbar.tabs.device.platform.' + platformKey)
      ? platformLabel
      : String(device.platform || '');
    const b = (browserLabel && browserLabel !== 'topbar.tabs.device.browser.' + browserKey)
      ? browserLabel
      : String(device.browser || '');
    if (p && b) {
      return p + ' · ' + b;
    }
    return p || b || '';
  },

  // "Reproduce here" handoff — pull the other tab's filter + drawer
  // state into the CURRENT tab. Operator clicks a row in the
  // tab-activity popover when they want to mirror state from
  // laptop → phone (or vice versa). The popover passes the row's
  // snapshot dict to this helper; we mutate the current tab's
  // state in place + persist where applicable.
  reproduceTabHere(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') {
      return;
    }
    // Top-level view + sub-tab navigation.
    if (snapshot.view) {
      this.view = snapshot.view;
    }
    if (snapshot.admin_tab && typeof this.openAdminTab === 'function') {
      this.openAdminTab(snapshot.admin_tab);
    } else if (snapshot.admin_tab) {
      this.adminTab = snapshot.admin_tab;
    }
    if (snapshot.settings_section) {
      this.settingsSection = snapshot.settings_section;
    }
    if (snapshot.stats_tab) {
      this.statsTab = snapshot.stats_tab;
    }
    // Filter restore — each filter flag uses the SAME mutator
    // helpers the toolbar chips do so persistence + downstream
    // reactivity (sessionStorage / SSE chip refresh) stay coherent.
    const f = snapshot.filters || {};
    this.search = f.search || '';
    this.statusFilter = f.statusFilter || '';
    this.healthFilter = f.healthFilter || '';
    if (this.hostsProblemFilter !== Boolean(f.hostsProblemFilter)) {
      this.toggleProblemHostsFilter();
    }
    this.hostsHideUnconfigured = !!f.hostsHideUnconfigured;
    // Provider filter — replace the whole set in one mutation so we
    // don't bounce sessionStorage writes on every chip.
    this.hostsProviderFilter = new Set(Array.isArray(f.hostsProviderFilter) ? f.hostsProviderFilter : []);
    try {
      if (typeof sessionStorage !== 'undefined') {
        if (this.hostsProviderFilter.size) {
          sessionStorage.setItem('hostsProviderFilter', [...this.hostsProviderFilter].join(','));
        } else {
          sessionStorage.removeItem('hostsProviderFilter');
        }
      }
    } catch { /* ignore */
    }
    // Drawer state — open the same host / item if the source tab
    // had one open. Items / hosts must exist locally; if the source
    // tab had a drawer for a host we don't know about yet, the
    // open silently no-ops (the operator can refresh first).
    if (snapshot.drawer_host && Array.isArray(this.hosts)) {
      const target = this.hosts.find(h => h && h.id === snapshot.drawer_host);
      if (target && typeof this.openHostDrawer === 'function') {
        this.openHostDrawer(target);
      }
    }
    if (snapshot.drawer_item && Array.isArray(this.items)) {
      const target = this.items.find(it => it && (it.id === snapshot.drawer_item || it.name === snapshot.drawer_item));
      if (target) {
        this.drawerItem = target;
      }
    }
    // Toast confirmation so the operator sees the mirror landed.
    if (typeof this.showToast === 'function') {
      this.showToast(this.t('topbar.tabs.rich.reproduced') || 'Mirrored other tab\'s state', 'success');
    }
  },
  // Heartbeat publisher — POSTs the current snapshot to the backend.
  // Short-circuits when nothing changed AND the last post was < 25s ago
  // (idle-tab path; the backend's 90s TTL still keeps the entry alive).
  async _tabActivityHeartbeat() {
    if (this._tabHeartbeatBusy) {
      return;
    }
    const snap = this._tabActivitySnapshot();
    const sig = JSON.stringify(snap);
    const now = Date.now();
    const stale = (now - (this._tabHeartbeatLast.ts || 0)) > 25000;
    if (sig === this._tabHeartbeatLast.signature && !stale) {
      return;
    }
    this._tabHeartbeatBusy = true;
    try {
      await fetch('/api/tabs/activity', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(snap),
      });
      this._tabHeartbeatLast = {ts: now, signature: sig};
    } catch { /* best-effort; next tick retries */
    } finally {
      this._tabHeartbeatBusy = false;
    }
  },
  // Boot-time hydration of the local map from the backend's snapshot
  // so the topbar widget paints sibling tabs immediately, before the
  // first SSE event lands.
  async _tabActivityHydrate() {
    try {
      const r = await fetch('/api/tabs/activity', {cache: 'no-store'});
      if (!r.ok) {
        return;
      }
      const d = await r.json();
      const fresh = {};
      for (const t of (d.tabs || [])) {
        if (t && t.client_id) {
          fresh[t.client_id] = t;
        }
      }
      this.tabActivity = fresh;
    } catch { /* best-effort */
    }
  },
  // Click-to-focus a sibling tab. Uses BroadcastChannel where available
  // (every modern browser since 2022); receivers self-match on the id
  // and call `window.focus()`. Best-effort — cross-window focus is
  // browser-discretionary, but works reliably when both tabs are in
  // the same browser process group.
  focusTabByClientId(cid) {
    if (!cid || cid === window.__ogClientId) {
      return;
    }
    try {
      // BroadcastChannel is a browser-globals API (not in JSHint's
      // node-default env list). Per-line ignore on the use site is
      // narrower than adding it to a project-wide `/* global */`
      // declaration that would silence missing-import bugs elsewhere.
      if (!this._tabFocusChannel && typeof BroadcastChannel === 'function') { // jshint ignore:line
        this._tabFocusChannel = new BroadcastChannel('omnigrid-tab-focus'); // jshint ignore:line
      }
      if (this._tabFocusChannel) {
        this._tabFocusChannel.postMessage({client_id: cid});
      }
    } catch { /* BroadcastChannel disabled / sandboxed */
    }
  },
  // Sorted view of `tabActivity` for the topbar popover. Newest tab
  // first (largest `ts`).
  tabActivityList() {
    const out = [];
    const map = this.tabActivity || {};
    for (const cid of Object.keys(map)) {
      out.push(map[cid]);
    }
    out.sort((a, b) => (Number(b.ts || 0) - Number(a.ts || 0)));
    return out;
  },
  tabActivityCount() {
    return this.tabActivityList().length;
  },
  // Mobile-safe positioning for the "N tabs" popover. The desktop CSS anchors
  // it to the pill's right edge (inset-inline-end: 0), but on a narrow screen
  // the pill sits far from the right edge, so a 280px+ popover overflows off
  // the LEFT of the screen. On <=640px pin it as a fixed sheet spanning the
  // viewport (with margins) just below the button; desktop returns '' so the
  // stylesheet's absolute positioning applies. Recomputed each time `open`
  // flips (the binding passes `open` so it re-evaluates on toggle).
  tabsPopoverStyle(btn, open) {
    try {
      if (!open || !btn || window.innerWidth > 640) {
        return '';
      }
      const r = btn.getBoundingClientRect();
      const top = Math.round(r.bottom + 6);
      return 'position: fixed; inset-inline-start: var(--s-3); '
        + 'inset-inline-end: var(--s-3); top: ' + top + 'px; '
        + 'min-width: 0; max-width: none; width: auto; '
        + 'max-height: calc(100vh - ' + (top + 12) + 'px); overflow-y: auto;';
    } catch (_e) {
      return '';
    }
  },
  // Single-parse SSE event unwrap. Returns the parsed event object
  // ({type, ts, payload}) when the event should be processed,
  // OR null when self-filter wins (caller should early-return).
  // Eliminates the per-handler "JSON.parse(e.data) twice" pattern —
  // _isSelfEvent does its own parse, then handlers parse again.
  // 13 handlers × 2 parses per event = unnecessary overhead on
  // fleet-wide ticks. Use:
  // const evt = this._unwrapEventOrNull(e); if (!evt) return;
  // const id = (evt.payload || {}).id;  // already parsed
  _unwrapEventOrNull(e) {
    if (!e || !e.data) {
      return null;
    }
    try {
      const data = JSON.parse(e.data);
      const myId = window.__ogClientId;
      const cid = data && data.payload && data.payload.client_id;
      if (myId && cid && cid === myId) {
        return null;
      }  // self-event, skip
      return data;
    } catch {
      return null;  // malformed event, treat as skip
    }
  },

  // enhancement — bracket every interval-poll fetch so the
  // topbar pill flashes green for the EXACT duration of the network
  // request (start of fetch → response landed). Counter-based so
  // concurrent polls (e.g. /api/items + /api/stats firing in the
  // same tick) don't end the flash prematurely on the first one
  // that returns. Off / Live modes short-circuit — Off shouldn't
  // poll, Live's green-on-event UX is already implicit in the SSE
  // pill colour.
  _pollStart() {
    if (this.refreshInterval === -1 || this.refreshInterval === 0) {
      return;
    }
    this._pollFlashCount = (this._pollFlashCount || 0) + 1;
    this._pollFlashing = true;
  },
  _pollEnd() {
    if (!this._pollFlashCount) {
      return;
    }
    this._pollFlashCount = Math.max(0, this._pollFlashCount - 1);
    if (this._pollFlashCount === 0) {
      this._pollFlashing = false;
    }
  },
  // Convenience wrapper — `await this._pollWrap(this.refresh(true))`
  // sets the flash on, awaits the promise, clears the flash in
  // finally. Returns the promise's resolved value so callers can
  // chain naturally.
  _pollWrap(promise) {
    this._pollStart();
    return Promise.resolve(promise).finally(() => this._pollEnd());
  },

  setAutoRefresh(seconds) {
    this.autoRefresh = seconds;
    try {
      localStorage.setItem('autoRefresh', String(seconds));
    } catch {
    }
    if (this._autoTimer) {
      clearInterval(this._autoTimer);
    }
    if (seconds > 0) {
      this._autoTimer = setInterval(() => {
        // Wrap in poll-flash brackets so the topbar pill stays green
        // for the duration of the actual /api/items round-trip.
        this._pollWrap(this.refresh(true));
      }, seconds * 1000);
    }
  },

  // single canonical cadence-setter. Three modes mapped to
  // the picker's five buttons:
  //
  // -1   "Live"   — SSE connection ON, every chart updates via
  //                 push events. Polling timers sleep.
  //  0   "Off"    — SSE connection CLOSED, polling sleeps. The
  //                 dashboard becomes a static snapshot of the
  //                 current state. Operator sees no more updates
  //                 until they pick another mode (or refresh).
  // 30/60/300     — SSE connection CLOSED, polling at the chosen
  //                 cadence drives every chart uniformly.
  //
  // Closing SSE for Off + interval modes is the load-bearing UX
  // fix — pre-fix the picker selected Off but the Live pill stayed
  // green because SSE was still pushing events; operators reported
  // "the picker doesn't do what it says". Now the SSE pill colour
  // is a direct read of the picker's choice.
  //
  // Mirrors the chosen polling cadence into legacy state vars
  // (`autoRefresh` for items poll + `statsInterval` for stats /
  // hosts polls) so the existing pollers don't need to be rewired.
  // Live and Off both map to legacy=0; only intervals drive the
  // pollers.
  setRefreshInterval(seconds) {
    const modeLabel = seconds === -1 ? 'Live (SSE)' : seconds === 0 ? 'Off' : seconds + 's interval';
    console.log('[live] setRefreshInterval: mode=' + modeLabel + ' (raw=' + seconds + ')');
    this.refreshInterval = seconds;
    try {
      localStorage.setItem('refreshInterval', String(seconds));
    } catch {
    }
    const legacy = seconds === -1 ? 0 : seconds;
    this.setStatsInterval(legacy);
    this.setAutoRefresh(legacy);
    // SSE management — Live opens (or keeps open) the stream;
    // Off / interval modes close it so the picker is the single
    // source of truth for "is this dashboard receiving updates?".
    if (seconds === -1) {
      if (!this._sse) {
        this._initSSE();
      }
    } else {
      this._disconnectSSE();
    }
    // pollOps gates on `refreshInterval === 0` (see pollOps tick
    // body) — re-kick it whenever we transition AWAY from Off so
    // the panel comes back without waiting for a manual interaction.
    if (seconds !== 0 && !this._opsTimer) {
      try {
        this.pollOps();
      } catch {
      }
    }
    // Host-drawer history chart timer also follows the picker now
    //. Re-arm it under the new cadence whenever the operator
    // switches modes while the drawer is open. Off → clear; Live →
    // 30s baseline; interval → operator's chosen cadence.
    if (this._drawerHistoryTimer) {
      clearInterval(this._drawerHistoryTimer);
      this._drawerHistoryTimer = null;
    }
    // NE-only Live mode → push-driven → no timer.
    // Beszel / NE+Beszel Live → 30s timer (push event covers NE
    // half but Beszel data still needs polling). Interval modes
    // use the picker cadence regardless. Off → no timer.
    const dh = this.drawerHost;
    if (dh && (dh.beszel_id || dh.ne_url) && seconds !== 0) {
      const liveMode = seconds === -1;
      const pushOnly = liveMode && dh.ne_url && !dh.beszel_id;
      if (!pushOnly) {
        const ms = (liveMode ? 30 : seconds) * 1000;
        this._drawerHistoryTimer = setInterval(() => {
          if (!this.drawerHost) {
            return;
          }
          this._pollWrap(this.loadHostHistory(
            this.drawerHost.beszel_id || '',
            this.drawerHost.id,
          ));
        }, ms);
      }
    }
    // ping history timer follows the same picker cadence.
    // Live mode is push-driven by the `host:ping_sampled` SSE
    // handler so no timer needed; Off → no timer; interval → poll
    // at the operator's chosen cadence.
    if (this._drawerPingTimer) {
      clearInterval(this._drawerPingTimer);
      this._drawerPingTimer = null;
    }
    if (dh && dh.ping_enabled && seconds !== 0 && seconds !== -1) {
      const pingMs = seconds * 1000;
      this._drawerPingTimer = setInterval(() => {
        if (!this.drawerHost || !this.drawerHost.ping_enabled) {
          return;
        }
        this._pollWrap(this.loadHostPingHistory(this.drawerHost.id));
      }, pingMs);
    }
    // SNMP iface / temp / host history follow the same picker
    // cadence as the main history timer. Self-healing for the
    // initial-fetch-empty case (sampler hadn't ticked yet, transient
    // backend error). Off → no timer; otherwise (Live OR interval)
    // re-fetch on tick. Same `_snmpHasProbeTarget` gate as every
    // other SNMP fetch site so hosts with the sampler running via
    // `snmp_name` but no curated UI checkbox ticked still self-heal.
    if (this._drawerSnmpHistoryTimer) {
      clearInterval(this._drawerSnmpHistoryTimer);
      this._drawerSnmpHistoryTimer = null;
    }
    if (dh && this._snmpHasProbeTarget(dh) && seconds !== 0) {
      const liveMode = seconds === -1;
      const snmpMs = (liveMode ? 30 : seconds) * 1000;
      this._drawerSnmpHistoryTimer = setInterval(() => {
        if (!this.drawerHost) {
          return;
        }
        if (!this._snmpHasProbeTarget(this.drawerHost)) {
          return;
        }
        const hrs = this.hostHistoryRange || 1;
        if (typeof this.loadHostSnmpHistory === 'function') {
          this._pollWrap(this.loadHostSnmpHistory(this.drawerHost.id, hrs));
        }
        if (typeof this.loadHostSnmpIfaceHistory === 'function') {
          this._pollWrap(this.loadHostSnmpIfaceHistory(this.drawerHost.id, hrs));
        }
        if (typeof this.loadHostSnmpTempHistory === 'function') {
          this._pollWrap(this.loadHostSnmpTempHistory(this.drawerHost.id, hrs));
        }
      }, snmpMs);
    }
    // Sparklines — Off kills the timer; Live / interval
    // modes restart at the 5min baseline (sparklines are coarse
    // 24h aggregates, the picker doesn't change their cadence).
    if (this._sparksTimer) {
      clearInterval(this._sparksTimer);
      this._sparksTimer = null;
    }
    if (seconds !== 0) {
      try {
        this.pollSparks();
      } catch {
      }
    }
  },

  get counts() {
    // `update` counts ONLY actionable live updates — running items
    // with a newer remote digest. Offline / orphan containers with
    // stale digests get tracked separately under `update_offline`
    // so the topbar nav badge + filter chip + any other consumer
    // doesn't show "1 pending update" for a stack whose only stale
    // item is an exited orphan that the Update-stack button can't
    // fix anyway. Mirrors the per-stack rollup in `logic/gather.py`
    // and the per-node rollup in `nodesView` so all three count
    // sources read consistently.
    //
    // NOT flush-memoized (unlike filteredStacks / sortedFiltered below):
    // every consumer of `counts` is lightweight — the Stacks nav badge
    // (x-show), the filter-chip x-text counts, the nav title — none of
    // them iterate this.items. A cache-hit early-return would return
    // before reading this.items, so Alpine's fine-grained reactivity
    // (@vue/reactivity) would never subscribe those effects to the items
    // they depend on, and the badge would FREEZE at its last value after
    // an in-place items reconcile. The single O(items) pass per read is
    // cheap; correctness wins over the micro-optimization here.
    const c = {update: 0, update_offline: 0, uptodate: 0, unknown: 0, error: 0, ignored: 0, healthy: 0, degraded: 0, offline: 0};
    for (const i of this.items) {
      if (i.status === 'update') {
        if (i.health === 'offline') {
          c.update_offline++;
        } else {
          c.update++;
        }
      } else if (i.status === 'up-to-date') {
        c.uptodate++;
      } else {
        if (i.status === 'unknown') {
          c.unknown++;
        } else {
          if (i.status === 'error') {
            c.error++;
          } else {
            if (i.status === 'ignored') {
              c.ignored++;
            }
          }
        }
      }
      if (i.health === 'healthy') {
        c.healthy++;
      } else {
        if (i.health === 'degraded') {
          c.degraded++;
        } else {
          if (i.health === 'offline') {
            c.offline++;
          }
        }
      }
    }
    return c;
  },
  get filteredStacks() {
    if (_stacksFlushCache.filteredStacks !== undefined) {
      return _stacksFlushCache.filteredStacks;
    }
    const q = this.search.toLowerCase();
    const out = this.stacks
      .map(s => ({...s, items: s.items.filter(i => this.matches(i, q))}))
      .filter(s => s.items.length > 0);
    _stacksFlushCache.filteredStacks = out;
    _scheduleStacksFlushClear();
    return out;
  },
  get filteredItems() {
    if (_stacksFlushCache.filteredItems !== undefined) {
      return _stacksFlushCache.filteredItems;
    }
    const q = this.search.toLowerCase();
    const out = this.items.filter(i => this.matches(i, q));
    _stacksFlushCache.filteredItems = out;
    _scheduleStacksFlushClear();
    return out;
  },
  get sortedFiltered() {
    if (_stacksFlushCache.sortedFiltered !== undefined) {
      return _stacksFlushCache.sortedFiltered;
    }
    const arr = [...this.filteredItems];
    const f = this.sortField, dir = this.sortDir === 'asc' ? 1 : -1;
    const statusRank = {update: 0, error: 1, unknown: 2, 'up-to-date': 3, ignored: 4};
    arr.sort((a, b) => {
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
        if (ua == null && ub == null) {
          return 0;
        }
        if (ua == null) {
          return 1;
        }
        if (ub == null) {
          return -1;
        }
        va = ua;
        vb = ub;
      } else {
        va = (a[f] || '').toString().toLowerCase();
        vb = (b[f] || '').toString().toLowerCase();
      }
      if (va < vb) {
        return -1 * dir;
      }
      if (va > vb) {
        return 1 * dir;
      }
      return 0;
    });
    _stacksFlushCache.sortedFiltered = arr;
    _scheduleStacksFlushClear();
    return arr;
  },
  matches(item, q) {
    if (q) {
      const hay = [item.name, item.image, item.stack, item.tag].filter(Boolean).join(' ').toLowerCase();
      if (!hay.includes(q)) {
        return false;
      }
    }
    if (this.statusFilter && item.status !== this.statusFilter) {
      return false;
    }
    if (this.healthFilter && item.health !== this.healthFilter) {
      return false;
    }
    return true;
  },

  applyTheme() {
    const sysLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
    const resolved = this.themePref === 'auto' ? (sysLight ? 'light' : 'dark') : this.themePref;
    document.documentElement.setAttribute('data-theme', resolved);
    // Invalidate the icon-resolver caches so the theme-dependent
    // URL swap (`<slug>` vs `<slug>-dark`) re-resolves on the next
    // binding read. Without this, KNOWN_DARK_ICONS brands would
    // keep showing their pre-theme variant after a theme cycle.
    if (typeof this._iconCacheClear === 'function') {
      this._iconCacheClear();
    }
  },
  cycleTheme() {
    const order = ['auto', 'light', 'dark'];
    this.themePref = order[(order.indexOf(this.themePref) + 1) % order.length];
    // Cache in localStorage for fast-path first-paint on the next
    // page load (avoids a flash of wrong-theme while /api/me round-
    // trips). The DB is the cross-browser / cross-machine source
    // of truth — write through to the user's `ui_prefs.theme` so the
    // operator's preference follows them across browsers.
    try {
      localStorage.setItem('theme', this.themePref);
    } catch {
    }
    this.applyTheme();
    this.persistThemePref(this.themePref);
  },
  // Write-through to /api/me/ui-prefs so the theme follows the
  // operator across browsers. Best-effort — a network blip leaves
  // the localStorage cache as the fallback. Skipped for API-token
  // pseudo-users (negative ids) since /api/me/ui-prefs returns 400
  // for them.
  async persistThemePref(value) {
    if (!this.me || !this.me.id || this.me.id < 0) {
      return;
    }
    try {
      await fetch('/api/me/ui-prefs', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prefs: {theme: value}}),
      });
    } catch (e) {
      // Localised cache still has the new value — operator sees the
      // theme they picked on this browser; the next /api/me load will
      // re-sync if the DB write eventually succeeds.
      if (window.console && console.warn) {
        console.warn('[theme] persist to DB failed:', e);
      }
    }
  },
  async persistHostHistoryRange(value) {
    if (!this.me || !this.me.id || this.me.id < 0) {
      return;
    }
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) {
      return;
    }
    try {
      await fetch('/api/me/ui-prefs', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prefs: {host_history_range: n}}),
      });
    } catch (e) {
      if (window.console && console.warn) {
        console.warn('[host_history_range] persist to DB failed:', e);
      }
    }
  },
  _cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  },

  _busyKey(kind, id) {
    return `${kind}:${id}`;
  },
  isStackBusy(stack) {
    if (!stack) {
      return false;
    }
    // Direct-Docker compose-project stack — no Portainer int stack_id; keyed by
    // node:project to match updateStack's busy key + the op's target_id.
    if (!stack.stack_id) {
      if (!stack.compose_path) {
        return false;
      }
      const tid = (stack.compose_node_id || '') + ':' + stack.name;
      if (this.busy[this._busyKey('stack', 'docker:' + tid)]) {
        return true;
      }
      return this.activeOps.some(o => o.op_type === 'update_stack' && String(o.target_id) === tid);
    }
    if (this.busy[this._busyKey('stack', stack.stack_id)]) {
      return true;
    }
    return this.activeOps.some(o => o.op_type === 'update_stack' && String(o.target_id) === String(stack.stack_id));
  },
  isItemBusy(item) {
    if (!item) {
      return false;
    }
    if (this.busy[this._busyKey('ctn', item.raw_id)]) {
      return true;
    }
    if (item.type === 'orphan') {
      return this.activeOps.some(o => o.op_type === 'remove_container' && o.target_id === item.raw_id);
    }
    if (item.stack_id) {
      return this.isStackBusy({stack_id: item.stack_id});
    }
    if (item.type === 'container') {
      return this.activeOps.some(o => ['update_container', 'remove_container', 'restart_container'].includes(o.op_type) && o.target_id === item.raw_id);
    }
    return false;
  },
  isServiceBusy(item) {
    if (!item) {
      return false;
    }
    if (this.busy[this._busyKey('svc', item.raw_id)]) {
      return true;
    }
    return this.activeOps.some(o => o.op_type === 'restart_service' && o.target_id === item.raw_id);
  },
  isRestartBusy(item) {
    if (!item) {
      return false;
    }
    if (item.type === 'service') {
      return this.isServiceBusy(item);
    }
    return this.isItemBusy(item);
  },
  _busyTimers: {},
  _markBusy(key) {
    this.busy = {...this.busy, [key]: true};
    if (this._busyTimers[key]) {
      clearTimeout(this._busyTimers[key]);
    }
    this._busyTimers[key] = setTimeout(() => {
      delete this._busyTimers[key];
      if (this.busy[key]) {
        const n = {...this.busy};
        delete n[key];
        this.busy = n;
      }
    }, 3000);
  },
  _holdBusy(key) {
    if (this._busyTimers[key]) {
      clearTimeout(this._busyTimers[key]);
      delete this._busyTimers[key];
    }
    if (!this.busy[key]) {
      this.busy = {...this.busy, [key]: true};
    }
  },
  _clearBusy(key) {
    if (this._busyTimers[key]) {
      clearTimeout(this._busyTimers[key]);
      delete this._busyTimers[key];
    }
    if (this.busy[key]) {
      const n = {...this.busy};
      delete n[key];
      this.busy = n;
    }
  },
  _opBusyKey(op) {
    if (!op) {
      return null;
    }
    if (op.op_type === 'update_stack') {
      return this._busyKey('stack', op.target_id);
    }
    if (['update_container', 'remove_container', 'restart_container'].includes(op.op_type)) {
      return this._busyKey('ctn', op.target_id);
    }
    if (op.op_type === 'restart_service') {
      return this._busyKey('svc', op.target_id);
    }
    return null;
  },

  statusKey(s) {
    return (s || 'unknown').replace('up-to-date', 'ok');
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
    return {real, internal};
  },
  networkIfacesShowDocker: {},  // {host_id: bool} — toggle map per host
  toggleNetworkIfacesDocker(h) {
    if (!h || !h.id) {
      return;
    }
    this.networkIfacesShowDocker[h.id] = !this.networkIfacesShowDocker[h.id];
  },
  // busy / idle split for switches that expose 30+ ports via
  // SNMP. The default "real" list mixes meaningful interfaces
  // (eth0 / vlan2 / tun1 — has traffic OR an IP) with idle ports
  // (Port-Channel21..32, unused TwoPointFiveGigabitEthernet ports —
  // no traffic, no addrs). Without this split a Cisco SG300's drawer
  // is a 50-row wall of mostly-empty rows. `busy` = at least one of
  // (rx_bytes, tx_bytes, addrs.length) is non-zero; everything else
  // falls into `idle` and is hidden behind a per-host toggle.
  networkIfacesActivityPartition(h) {
    const partition = this.networkIfacesPartition(h);
    const busy = [], idle = [];
    for (const iface of partition.real) {
      const hasTraffic = this.hostIfaceHasTraffic(iface);
      const hasAddrs = (typeof iface === 'object' && (iface.addrs || []).length > 0);
      // Treat the loopback as "busy" so it always renders; operators
      // expect to see it and it's a useful sanity-check signal.
      const name = (typeof iface === 'object' ? (iface.name || '') : iface).toLowerCase();
      const isLoopback = name === 'lo' || name === 'loopback';
      (hasTraffic || hasAddrs || isLoopback ? busy : idle).push(iface);
    }
    // Sort busy by total traffic descending so the dominant NIC
    // floats to the top — operator's eye reads the meaningful rows
    // first instead of having to scan a long alphabetical list.
    busy.sort((a, b) => {
      const ta = (+(a && a.rx_bytes) || 0) + (+(a && a.tx_bytes) || 0);
      const tb = (+(b && b.rx_bytes) || 0) + (+(b && b.tx_bytes) || 0);
      return tb - ta;
    });
    return {busy, idle, internal: partition.internal};
  },
  networkIfacesShowIdle: {},  // per-host toggle for the idle group
  toggleNetworkIfacesIdle(h) {
    if (!h || !h.id) {
      return;
    }
    this.networkIfacesShowIdle[h.id] = !this.networkIfacesShowIdle[h.id];
  },
  // Cap the busy-iface list to the top 10 by traffic, with a
  // per-host "Show all (N)" toggle. Switches with 52+ ports overflow
  // the drawer even after the show-idle filter; the operator wants
  // the loudest 10 by default and can opt into the rest.
  networkIfacesBusyCap: 10,
  networkIfacesShowAllBusy: {},  // per-host toggle for the busy-cap group
  toggleNetworkIfacesBusyAll(h) {
    if (!h || !h.id) {
      return;
    }
    this.networkIfacesShowAllBusy[h.id] = !this.networkIfacesShowAllBusy[h.id];
  },
  networkIfacesBusyVisible(h) {
    const busy = this.networkIfacesActivityPartition(h).busy;
    if (this.networkIfacesShowAllBusy[h.id]) {
      return busy;
    }
    return busy.slice(0, this.networkIfacesBusyCap);
  },
  networkIfacesBusyHiddenCount(h) {
    const busy = this.networkIfacesActivityPartition(h).busy;
    return Math.max(0, busy.length - this.networkIfacesBusyCap);
  },
  // per-interface SNMP traffic helpers. SNMP-derived
  // `network_ifaces[]` rows carry `rx_bytes` / `tx_bytes` /
  // `oper_status`; node-exporter / Beszel / Pulse rows have
  // `name` + `mac` + `addrs` but no traffic counters. Helpers
  // gracefully no-op when the rows lack the SNMP fields so
  // non-SNMP hosts don't see empty traffic rows.
  hostIfaceHasTraffic(iface) {
    if (!iface || typeof iface !== 'object') {
      return false;
    }
    const rx = +iface.rx_bytes || 0;
    const tx = +iface.tx_bytes || 0;
    return rx > 0 || tx > 0;
  },
  // Largest rx+tx total across the host's REAL interfaces — used to
  // normalise per-interface bar widths so the busiest NIC fills 100%
  // and the rest scale relative. Internal (docker / veth / etc.)
  // interfaces are excluded from the max so a noisy docker0 doesn't
  // drown the legitimate eth0/wlan0 traffic visually.
  hostIfaceMaxTotal(h) {
    const ifaces = this.networkIfacesPartition(h).real || [];
    let max = 0;
    for (const i of ifaces) {
      if (!this.hostIfaceHasTraffic(i)) {
        continue;
      }
      const t = (+i.rx_bytes || 0) + (+i.tx_bytes || 0);
      if (t > max) {
        max = t;
      }
    }
    return max;
  },
  // Inline `:style` for an interface's stacked rx/tx bar. `--rx-pct`
  // and `--tx-pct` are rendered as widths inside the bar. Returns
  // empty string when no traffic data is available so the template
  // can short-circuit without rendering an empty bar.
  hostIfaceBarStyle(iface, maxTotal) {
    if (!this.hostIfaceHasTraffic(iface) || maxTotal <= 0) {
      return '';
    }
    const total = (+iface.rx_bytes || 0) + (+iface.tx_bytes || 0);
    const totalPct = (total / maxTotal) * 100;
    const rxShare = total > 0 ? (+iface.rx_bytes || 0) / total : 0;
    const rxPct = totalPct * rxShare;
    const txPct = totalPct - rxPct;
    return `--rx-pct: ${rxPct.toFixed(2)}%; --tx-pct: ${txPct.toFixed(2)}%;`;
  },
  // UPS card helpers (APC PowerNet-MIB). Pill class for the
  // status badge, level class for the battery gauge (matching the
  // .stat-bar warn/crit convention), and human-readable runtime
  // formatter. All gracefully handle missing data; the card itself
  // is gated on `h.host_ups_status || h.host_battery_percent` in
  // the template.
  upsStatusPillClass(status) {
    const s = String(status || '').toLowerCase();
    if (s === 'online') {
      return 'pill-ok';
    }
    if (s === 'on-battery' || s === 'on-smart-boost' || s === 'on-smart-trim') {
      return 'pill-update';
    }
    if (s === 'off' || s === 'rebooting' || s.includes('bypass')
      || s === 'hardware-failure-bypass' || s === 'sleeping-until') {
      return 'pill-error';
    }
    return 'pill-unknown';
  },
  upsStatusLabel(status) {
    // Pretty-print the snake-case enum from PowerNet-MIB. i18n keys
    // exist for the canonical set; unknown values fall through to
    // the raw enum string. Short-circuit when the status is empty
    // / null so the i18n loader doesn't see a missing-key probe
    // for `host_drawer.ups.status_` (no enum value).
    const s = String(status || '').toLowerCase();
    if (!s) {
      return '';
    }
    const key = `host_drawer.ups.status_${s.replace(/-/g, '_')}`;
    const translated = this.t(key);
    return (translated && translated !== key) ? translated : (status || '');
  },
  upsBatteryLevel(pct) {
    // Inverse of the .stat-bar warn/crit semantics — for batteries,
    // LOW is bad. <20% = crit (red), <50% = warn (amber), else ok.
    const n = +pct;
    if (!Number.isFinite(n)) {
      return '';
    }
    if (n < 20) {
      return 'crit';
    }
    if (n < 50) {
      return 'warn';
    }
    return '';
  },
  // Battery status enum (from PowerNet-MIB upsBasicBatteryStatus).
  // Operator-requested: render as a coloured pill instead of the
  // raw "battery-normal" string. battery-normal → green (pill-ok),
  // battery-low → amber (pill-update), battery-in-fault → red
  // (pill-error), unknown → grey (pill-unknown). Mirror of
  // `upsStatusPillClass` for the output-status badge.
  upsBatteryStatusPillClass(status) {
    const s = String(status || '').toLowerCase();
    if (s === 'battery-normal') {
      return 'pill-ok';
    }
    if (s === 'battery-low') {
      return 'pill-update';
    }
    if (s === 'battery-in-fault') {
      return 'pill-error';
    }
    return 'pill-unknown';
  },
  upsBatteryStatusLabel(status) {
    // Pretty-print the snake-case enum from PowerNet-MIB. i18n keys
    // exist for the canonical set; unknown values fall through to
    // the raw enum string. Same shape as `upsStatusLabel` so the
    // label / pill pair stays consistent across translations.
    const s = String(status || '').toLowerCase();
    const key = `host_drawer.ups.battery_${s.replace(/-/g, '_')}`;
    const translated = this.t(key);
    return (translated && translated !== key) ? translated : (status || '');
  },
  // Dell server-health pill helpers. All four lean
  // on the standard Dell Systems Management Server Health enum
  // (ok / non-critical / critical / non-recoverable / unknown /
  // other). Two flavours: server-health row status (fans / temps /
  // PSUs / voltages — string label like "ok" / "critical") and
  // physical/virtual disk state (string label like "online" /
  // "rebuild" / "failed"). The pill-* token family is reused so
  // the colour family matches every other status pill in the SPA.
  dellHealthPillClass(status) {
    const s = String(status || '').toLowerCase();
    if (s === 'ok') {
      return 'pill-ok';
    }
    if (s === 'non-critical') {
      return 'pill-update';
    }
    if (s === 'critical' || s === 'non-recoverable') {
      return 'pill-error';
    }
    return 'pill-unknown';
  },
  // HTTP probe TLS expiry pill class. Three-state: red when expired
  // (negative days remaining), amber when within the operator-tunable
  // warning window (`me.client_config.http_probe_cert_warning_days`,
  // default 30 days), green otherwise. Mirrors `upsStatusPillClass`
  // / `dellHealthPillClass` shape.
  httpProbeTlsPillClass(daysRemaining) {
    const d = Number(daysRemaining);
    if (!Number.isFinite(d)) {
      return 'pill-muted';
    }
    if (d < 0) {
      return 'pill-error';
    }
    const warn = Number((this.me && this.me.client_config && this.me.client_config.http_probe_cert_warning_days) || 30);
    if (d <= warn) {
      return 'pill-warning';
    }
    return 'pill-ok';
  },
  dellHealthLabel(status) {
    // i18n key family mirrors upsBatteryStatusLabel — unknown values
    // capitalise the raw string instead of crashing OR rendering the
    // raw lowercase wire form ("ok" / "critical").
    const s = String(status || '').toLowerCase();
    if (!s) {
      return '';
    }
    const key = `host_drawer.server_health.status_${s.replace(/-/g, '_')}`;
    const translated = this.t(key);
    if (translated && translated !== key) {
      return translated;
    }
    return s.charAt(0).toUpperCase() + s.slice(1);
  },
  // Stack-update convergence summary — pulls the latest "Convergence
  // poll: N service(s) still updating: …" event line off a running
  // op's event log and returns a tidy one-line summary string for
  // the active-ops panel. Backend emits these lines from
  // `logic/ops.py:_await_stack_convergence`; the SPA renders them
  // inline so the operator sees rolling-update progress instead of
  // staring at a 300s spinner. Returns '' when the op is NOT in a
  // convergence wait (other op_types, or stack-update before any
  // poll has logged), so the consumer can gate via `x-if`.
  stackConvergenceSummary(op) {
    if (!op || op.status !== 'running') {
      return '';
    }
    if (op.op_type !== 'update_stack') {
      return '';
    }
    const events = Array.isArray(op.events) ? op.events : [];
    let latestPoll = '';
    let latestWaiting = '';
    // Walk newest → oldest. The convergence-poll line is what we
    // most want; fall back to the "Waiting for stack convergence
    // (timeout=…, poll=…)" line if no poll has fired yet so the
    // operator at least sees "convergence wait started".
    for (let i = events.length - 1; i >= 0; i--) {
      const msg = String((events[i] || {}).msg || '');
      if (!latestPoll && msg.startsWith('Convergence poll:')) {
        latestPoll = msg;
        break;
      }
      if (!latestWaiting && msg.startsWith('Waiting for stack convergence')) {
        latestWaiting = msg;
      }
    }
    if (latestPoll) {
      // Strip the "Convergence poll: " prefix so the chip reads as
      // a clean one-liner: "2 service(s) still updating: …". Keeps
      // the operator's eye on the actionable part.
      return latestPoll.replace(/^Convergence poll:\s*/, '');
    }
    if (latestWaiting) {
      return this.t('active_ops.convergence_starting') || 'Waiting for stack convergence…';
    }
    return '';
  },
  // Helper: index of a provider in the fallback chain, or -1 if not in chain.
  fallbackPriority(name) {
    const order = Array.isArray(this.settings.ai_fallback_order)
      ? this.settings.ai_fallback_order : [];
    return order.indexOf(name);
  },
  // Toggle a provider's membership in the fallback chain. Adding
  // appends to the end (lowest priority); removing splices out and
  // shifts the rest up. Operator can re-order via the up/down chips
  // surfaced in the per-card UI below.
  toggleFallbackProvider(name) {
    const order = Array.isArray(this.settings.ai_fallback_order)
      ? this.settings.ai_fallback_order.slice() : [];
    const i = order.indexOf(name);
    if (i >= 0) {
      order.splice(i, 1);
    } else {
      order.push(name);
    }
    this.settings.ai_fallback_order = order;
    this.markAiFormDirty();
  },
  // Move a provider up (-1) or down (+1) in the fallback order.
  moveFallbackProvider(name, delta) {
    const order = Array.isArray(this.settings.ai_fallback_order)
      ? this.settings.ai_fallback_order.slice() : [];
    const i = order.indexOf(name);
    if (i < 0) {
      return;
    }
    const j = i + delta;
    if (j < 0 || j >= order.length) {
      return;
    }
    [order[i], order[j]] = [order[j], order[i]];
    this.settings.ai_fallback_order = order;
    this.markAiFormDirty();
  },
  // Physical-disk state pill — Dell OMSA arrayDiskState labels.
  // Visual encoding:
  // green  (pill-ok)     — `online` (active in a RAID array, healthy)
  // blue   (pill-info)   — `ready` (present, idle, available — standby)
  // red    (pill-error)  — `failed` / `offline` / `degraded` / `removed` / `fault`
  // amber  (pill-update) — every transient / advisory state.
  // Distinguishing online (active member) from ready (idle spare) lets
  // operators tell at a glance which disks are CARRYING the array vs
  // which are sitting idle for hot-swap / hot-spare duty.
  dellPdStatePillClass(state) {
    const s = String(state || '').toLowerCase();
    if (s === 'online') {
      return 'pill-ok';
    }
    if (s === 'ready') {
      return 'pill-info';
    }
    if (s === 'failed' || s === 'offline' || s === 'degraded'
      || s === 'removed' || s === 'fault') {
      return 'pill-error';
    }
    if (s === 'rebuild' || s === 'rebuilding' || s === 'recovering'
      || s === 'replacing' || s === 'replaced'
      || s === 'foreign' || s === 'blocked' || s === 'clear'
      || s === 'non-raid' || s === 'ready-foreign'
      || s === 'read-only' || s === 'uncertified'
      || s === 'smart-alert' || s === 'predictive-failure') {
      return 'pill-update';
    }
    return 'pill-unknown';
  },
  // Virtual-disk state pill — same online/ready split as physical
  // disks. `online` = array carrying I/O; `ready` = array initialised
  // but idle / standby.
  dellVdStatePillClass(state) {
    const s = String(state || '').toLowerCase();
    if (s === 'online') {
      return 'pill-ok';
    }
    if (s === 'ready') {
      return 'pill-info';
    }
    if (s === 'failed' || s === 'offline'
      || s === 'failed-redundancy'
      || s === 'permanently-degraded') {
      return 'pill-error';
    }
    if (s === 'degraded' || s === 'verifying' || s === 'resynching'
      || s === 'regenerating' || s === 'rebuilding'
      || s === 'formatting' || s === 'reconstructing'
      || s === 'initializing' || s === 'background-init'
      || s === 'degraded-redundancy') {
      return 'pill-update';
    }
    return 'pill-unknown';
  },
  // Disk-state pill label resolver — try i18n first, fall back to a
  // capitalised version of the raw enum string. Mirrors the
  // dellHealthLabel pattern: `host_drawer.server_health.status_<name>`
  // is the canonical key family (hyphens become underscores so
  // `non-raid` → `status_non_raid`). Lower-case at the source-of-truth
  // (Dell OMSA enum constants in logic/snmp.py) stays the wire format;
  // this helper applies translation per locale + capitalisation
  // fallback for novel enum values that don't have a key yet.
  dellStateLabel(state) {
    const s = String(state || '').toLowerCase();
    if (!s) {
      return '';
    }
    const key = 'host_drawer.server_health.status_' + s.replace(/-/g, '_');
    const tr = this.t(key);
    if (tr && tr !== key) {
      return tr;
    }
    return s.charAt(0).toUpperCase() + s.slice(1);
  },
  // Threshold at which Server health sub-sections (Physical disks /
  // Voltages) collapse by default. Above this count the dense
  // multi-column layout kicks in AND a "Show all (N) / Show fewer"
  // toggle gates the visible row count to keep the panel compact.
  SERVER_HEALTH_COLLAPSE_THRESHOLD: 12,
  // First-N rows shown when the section is collapsed. Tuned so the
  // collapsed view spans roughly two-three rows of the dense
  // multi-column grid (auto-fit minmax(190px, 1fr) → 2-4 columns
  // depending on drawer width × ~2 rows ≈ 6 visible items).
  SERVER_HEALTH_COLLAPSED_LIMIT: 6,
  // Narrow-viewport row cap. Below 480px the dense multi-column grid
  // collapses to a single column AND the outer card collapses to a
  // single column too — leaving the collapsed view at 6 rows still
  // feels long on a phone. Drop to 3 below 480px so the collapsed
  // section fits one screen on a phone. Pair this with the existing
  // `SERVER_HEALTH_COLLAPSED_LIMIT` desktop default in
  // `effectiveCollapsedLimit()`.
  SERVER_HEALTH_COLLAPSED_LIMIT_NARROW: 3,
  SERVER_HEALTH_NARROW_BREAKPOINT_PX: 480,
  // Resolve the collapsed-row limit per current viewport width. Reads
  // `window.innerWidth` so it auto-adjusts on rotate / resize without
  // requiring a manual refresh. Caller (serverHealthVisibleRows) reads
  // this on every render so the slice tracks the live viewport.
  effectiveCollapsedLimit() {
    const w = (typeof window !== 'undefined') ? (window.innerWidth || 0) : 0;
    if (w > 0 && w < this.SERVER_HEALTH_NARROW_BREAKPOINT_PX) {
      return this.SERVER_HEALTH_COLLAPSED_LIMIT_NARROW;
    }
    return this.SERVER_HEALTH_COLLAPSED_LIMIT;
  },
  // Returns the slice of rows to render given the host id + section
  // key + the underlying full-list array. When count > threshold AND
  // the section isn't expanded, returns the first N; otherwise
  // returns the full array. Section key namespaces the expand state
  // (`pd` / `volt`) so toggling Physical disks doesn't also expand
  // Voltages on the same host.
  serverHealthVisibleRows(hostId, section, rows) {
    if (!Array.isArray(rows)) {
      return [];
    }
    if (rows.length <= this.SERVER_HEALTH_COLLAPSE_THRESHOLD) {
      return rows;
    }
    const key = `${hostId}:${section}`;
    if (this.serverHealthExpanded[key]) {
      return rows;
    }
    return rows.slice(0, this.effectiveCollapsedLimit());
  },
  // True when the section has more rows than fit in the collapsed
  // view (so the toggle should render). False when count is at or
  // below the threshold (no collapse needed).
  serverHealthCollapsible(rows) {
    return Array.isArray(rows) && rows.length > this.SERVER_HEALTH_COLLAPSE_THRESHOLD;
  },
  serverHealthIsExpanded(hostId, section) {
    return !!this.serverHealthExpanded[`${hostId}:${section}`];
  },
  toggleServerHealthExpanded(hostId, section) {
    const key = `${hostId}:${section}`;
    this.serverHealthExpanded[key] = !this.serverHealthExpanded[key];
  },
  // Printer-MIB supply card helpers. Per-supply colour is
  // hand-mapped from common toner names (cyan / magenta / yellow /
  // black / waste); falls through to a neutral colour for unmapped
  // supplies. Level class follows the .stat-bar warn/crit
  // convention with INVERSE semantics (low fill = bad).
  printerSupplyColor(supply) {
    const name = String(supply && supply.name || '').toLowerCase();
    // Each colour token comes from :root so light + dark themes stay
    // consistent. CMYK-style names get their named colours; "waste"
    // / "drum" / "fuser" / etc. fall to text-dim.
    if (name.includes('cyan')) {
      return 'var(--info)';
    }
    if (name.includes('magenta')) {
      return 'var(--chart-palette-pink)';
    }
    if (name.includes('yellow')) {
      return 'var(--warning)';
    }
    if (name.includes('black')) {
      return 'var(--text)';
    }
    if (name.includes('waste')) {
      return 'var(--text-faint)';
    }
    return 'var(--text-dim)';
  },
  printerSupplyLevel(supply) {
    // Inverse semantics — low fill = warn / crit. Operator wants a
    // "running out" signal, not a "running high" one.
    const pct = supply && supply.percent;
    const n = +pct;
    if (!Number.isFinite(n)) {
      return '';
    }
    if (n < 10) {
      return 'crit';
    }
    if (n < 25) {
      return 'warn';
    }
    return '';
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
      for (const p of (h.providers || [])) {
        seen.add(p);
      }
    }
    return seen;
  },
  // Per-host provider chip states. Returns an array of
  // { name: <provider>, state: 'ok' | 'failing' }
  // for chips that should render. Rules:
  // 1. Provider must be mapped on this host (the relevant
  //    `<provider>_name` / `ne_url` field is set).
  // 2. Provider must be globally enabled.
  // 3. Provider must be GLOBALLY HEALTHY (returned data for at
  //    least one host on this fleet). If pulse fails cluster-
  //    wide, the chip disappears from every host — that's a
  //    global-config issue, not a per-host one. The operator
  //    fixes the hub URL once, not on N hosts.
  // 4. State derivation:
  //    - 'ok'      → provider hit on THIS host AND its self-
  //                  reported status (when applicable) is not
  //                  paused/down/unreachable.
  //    - 'failing' → mapped on this host but provider didn't hit
  //                  here OR returned data with a paused/down
  //                  self-status. Chip turns red.
  // Per-provider chip colour resolver. Resolution order:
  // 1. `this.settings.provider_color_<name>` — live admin form
  //    state, hydrated by `loadSettings()` at init AND on every
  //    Save. This is what makes the chip-style update REACTIVELY
  //    as the operator drags the colour input around (Alpine
  //    tracks `settings` as a dependency of every binding that
  //    calls into here).
  // 2. `me.client_config.provider_colors[<name>]` — fallback for
  //    readonly viewers who don't fetch /api/settings. Reads from
  //    the snapshot the server stamped at the most recent
  //    /api/me round-trip, so a colour change here only takes
  //    effect on the next /api/me — fine for "view-only" users.
  // 3. Built-in distinct default so an unconfigured deploy still
  //    shows five different chip colours instead of two-or-three
  //    shared hues (pre-fix, ping shared node-exporter's amber
  //    and operators couldn't tell them apart in the row chips).
  // Provider-chip color resolver.
  // Defaults flow through CSS tokens (`--provider-default-<name>`)
  // declared in :root for both themes — operator's per-provider color
  // picker overrides via the `provider_color_<name>` settings KV.
  // Zero hex literals in JS (PyCharm's "Convert color to rgb()"
  // intention has no JS-string content to fire on); theme-aware via
  // the CSS variable cascade. Picker `<input type="color" :value>`
  // still gets a hex literal because getComputedStyle resolves the
  // var() to the stored hex at read time.
  providerColor(name) {
    // Live admin-form value first (reactive on every keystroke / save).
    const live = ((this.settings || {})['provider_color_' + name] || '').trim();
    if (live) {
      return live;
    }
    // Token-derived default — resolves to the hex declared on :root
    // for the active theme. Hyphenated form per CSS naming convention
    // (matches the `--provider-default-node-exporter` etc. tokens).
    try {
      const tokenName = '--provider-default-' + String(name).replace(/_/g, '-');
      const resolved = getComputedStyle(document.documentElement)
        .getPropertyValue(tokenName).trim();
      if (resolved) {
        return resolved;
      }
    } catch {
      // SSR / pre-DOM-ready / sandboxed iframe — fall through.
    }
    // Server-stamped snapshot for non-admin viewers (fallback before
    // getComputedStyle resolves — e.g. when DOM root isn't ready yet).
    const map = (this.me && this.me.client_config && this.me.client_config.provider_colors) || {};
    const v = (map[name] || '').trim();
    return v || 'currentColor';
  },
  // Inline style triplet for .chip.pill-custom — three CSS variables
  // (--chip-bg, --chip-br, --chip-fg) derived from the provider
  // colour via color-mix so the chip stays a soft tinted token rather
  // than a saturated background. The same pattern works for any
  // future dynamic-colour chip (asset categories, group badges, etc.).
  providerChipStyle(name) {
    const c = this.providerColor(name);
    return (
      '--chip-bg: color-mix(in srgb, ' + c + ' 18%, transparent); ' +
      '--chip-br: color-mix(in srgb, ' + c + ' 40%, transparent); ' +
      '--chip-fg: ' + c + ';'
    );
  },
  // Provider name → /img/icons/<slug>.svg filename. Mostly
  // identity except for `node_exporter` → `node-exporter` (the
  // resolver convention prefers hyphens over underscores in icon
  // filenames). Returns the bare slug; the consumer wraps it in
  // `url(/img/icons/<slug>.svg)` for the mask-image binding.
  providerIconSlug(name) {
    if (name === 'node_exporter') {
      return 'node-exporter';
    }
    if (name === 'http_probe') {
      return 'http-probe';
    }
    if (name === 'service_probe') {
      return 'service-probe';
    }
    return name;
  },
  // Inline style for `.provider-icon` — paints a mono SVG
  // mask in the per-provider chip colour. Use this on a `<span>`
  // when you want the provider's icon recoloured by the operator's
  // chip-colour customisation. Pairs naturally with
  // `providerChipStyle()` when the icon sits inside a `pill-custom`
  // chip (the parent's `--chip-fg` already provides the colour, so
  // the icon picks it up via `currentColor` automatically). Use this
  // helper when the icon stands ALONE (e.g. tab strip) where there's
  // no surrounding chip to inherit from.
  providerIconStyle(name) {
    return (
      '--provider-icon-url: url(/img/icons/' + this.providerIconSlug(name) + '.svg); '
      + 'color: ' + this.providerColor(name) + ';'
    );
  },
  // Stale-marker helpers for the UI.
  //
  // Backend stamps two markers on cache-seeded entries:
  // 1. `_stats_cache[id]._stale: true`              ← per-item stats
  // 2. `nodes_info[host]._stale_fields: [..]`       ← per-host telemetry
  // 3. `_stale_ts: <epoch_seconds>`                 ← persistence write
  //
  // The SPA dims any element bound to a stale value AND surfaces an
  // "X minutes ago" tooltip via `staleAge()`. This makes the
  // "provider went down" case visually explicit instead of letting
  // last-known-good values silently masquerade as live.
  isStale(obj) {
    if (!obj) {
      return false;
    }
    if (obj._stale === true) {
      return true;
    }
    const sf = obj._stale_fields;
    return Array.isArray(sf) && sf.length > 0;
  },
  isStaleField(obj, field) {
    if (!obj || !field) {
      return false;
    }
    const sf = obj._stale_fields;
    return Array.isArray(sf) && sf.indexOf(field) !== -1;
  },
  // Stale-grace countdown — surfaces when at least ONE field is
  // within 20% of its 24h grace expiry. Backend stamps
  // `_meta_stale_grace_remaining_s` (a `{key: seconds_remaining}`
  // map) at apply-time when any field crosses the warning
  // threshold. Returns the smallest remaining seconds across all
  // fields (worst-case for the operator), or null when no field
  // is in the warning window. The drawer banner reads this so
  // operators get a "data will be discarded in X" countdown
  // BEFORE the silent drop.
  staleGraceRemainingSeconds(obj) {
    if (!obj) {
      return null;
    }
    const m = obj._meta_stale_grace_remaining_s;
    if (!m || typeof m !== 'object') {
      return null;
    }
    let smallest = null;
    for (const k of Object.keys(m)) {
      const v = +m[k];
      if (Number.isFinite(v) && v >= 0) {
        if (smallest === null || v < smallest) {
          smallest = v;
        }
      }
    }
    return smallest;
  },
  // Operator-facing human label for the smallest remaining grace
  // window. Returns "" when no field is in the warning window
  // (banner suppresses cleanly).
  staleGraceRemainingLabel(obj) {
    const s = this.staleGraceRemainingSeconds(obj);
    if (s === null || s === undefined) {
      return '';
    }
    if (s < 60) {
      return Math.round(s) + 's';
    }
    if (s < 3600) {
      return Math.round(s / 60) + 'm';
    }
    if (s < 86400) {
      return (s / 3600).toFixed(1) + 'h';
    }
    return (s / 86400).toFixed(1) + 'd';
  },
  // True when the bulk of this host's snapshot-eligible fields are
  // stale (i.e. an entire provider went down vs a transient one-key
  // blip). The drawer's banner widens to a more explicit message
  // and the per-field warning triangles are SUPPRESSED so the
  // operator gets one clear signal instead of every <dl> row
  // carrying a triangle.
  //
  // Threshold: ≥ 6 stale fields (matches the typical count covered
  // by a single-provider outage — host_cpu_percent, host_mem_total,
  // host_mem_used, host_disk_total, host_disk_used, host_uptime_s
  // is six). Fewer than that = partial / transient — keep the
  // per-field triangles for actionable detail.
  isAllStale(obj) {
    if (!obj) {
      return false;
    }
    const sf = obj._stale_fields;
    if (!Array.isArray(sf)) {
      return false;
    }
    return sf.length >= 6;
  },
  // Provider-level stale enumeration. A provider is "stale" when
  // it's MAPPED on the curated row (`*_name` / `ne_url` /
  // `snmp_enabled`) but missing from `h.providers` (the live-hits
  // list this gather cycle produced). The stale banner can surface
  // these names so operators see "Beszel + Pulse cached, NE live"
  // rather than just a generic "N field(s) restored" count. Order
  // mirrors the merge order documented in the project conventions (Pulse → SNMP →
  // Beszel → NE → Webmin → Ping).
  staleProviders(h) {
    if (!h) {
      return [];
    }
    const got = new Set(h.providers || []);
    const out = [];
    const trim = v => String(v || '').trim();
    const push = (name, mapped) => {
      if (!mapped) {
        return;
      }
      if (got.has(name)) {
        return;
      }
      out.push(name);
    };
    push('pulse', !!trim(h.pulse_name));
    // SNMP: enabled + EITHER snmp_name OR address — same canonical
    // chain (aliases → snmp_name → address → SKIP) as the live
    // sampler / `_merge_one_host` / `rowHasProviderMapping`.
    push('snmp', h.snmp_enabled === true
      && (!!trim(h.snmp_name) || !!trim(h.address)));
    push('beszel', !!trim(h.beszel_name));
    push('node_exporter', !!trim(h.ne_url));
    push('webmin', !!trim(h.webmin_name));
    push('ping', !!h.ping_enabled);
    return out;
  },
  // Display label for a provider id — "node_exporter" → "exporter"
  // matches the existing chip rendering convention.
  providerDisplayName(name) {
    if (name === 'node_exporter') {
      return 'exporter';
    }
    return name;
  },
  staleAge(obj) {
    // return a clean fallback when `_stale_ts`
    // is 0 / missing / non-numeric. Pre-fix `fmtAgo` would either
    // render "Updated NaN ago" or empty string, depending on which
    // branch hit first; either way it's noise on a tooltip.
    // **Tooltip surface only** — returns the FULL i18n sentence
    // (`Last live data 4s ago — value restored from cache snapshot`).
    // For inline substitution into a larger template (e.g. the
    // drawer banner whose copy already wraps the time in its own
    // sentence), use `staleAgeShort(obj)` instead — passing
    // staleAge(h) into a {age} placeholder double-wraps the time.
    if (!obj) {
      return '';
    }
    const tsRaw = obj._stale_ts;
    const ts = Number(tsRaw);
    if (!Number.isFinite(ts) || ts <= 0) {
      try {
        return (window.t && window.t('stale_marker.never')) || '';
      } catch {
        return '';
      }
    }
    const ms = ts * 1000;
    const ago = this.fmtAgo(ms);
    // i18n: tooltip surface, not visible label. Translators handle
    // the "stale_marker.tooltip" key with the {age} placeholder.
    try {
      return (window.t && window.t('stale_marker.tooltip', {age: ago})) || ('Last live data ' + ago + ' ago');
    } catch {
      return 'Last live data ' + ago + ' ago';
    }
  },
  // Bare relative-time (e.g. `4s`, `12m`, `3h`) for the snapshot
  // timestamp on `obj._stale_ts`. Use this when injecting into a
  // larger i18n template that supplies its own `... ago` wrapping
  // (the host-drawer "Showing cached data" banner is the canonical
  // consumer). `staleAge(obj)` is for tooltip surfaces — passing it
  // into a {age} placeholder double-wraps the time. Empty string
  // when `_stale_ts` is missing / 0 / non-numeric so the outer
  // template still renders cleanly.
  staleAgeShort(obj) {
    if (!obj) {
      return '';
    }
    const ts = Number(obj._stale_ts);
    if (!Number.isFinite(ts) || ts <= 0) {
      return '';
    }
    return this.fmtAgo(ts * 1000);
  },
  // -----------------------------------------------------------------
  // Per-node aggregates for the Nodes view. Everything is computed
  // client-side off the same `stats` / `sparks` state the other views
  // use — no new backend endpoints. Items count toward a node iff
  // any of their `placements` match (services with multiple replicas
  // contribute to every node they run on).
  // -----------------------------------------------------------------
  itemsForNode(host) {
    // Per-flush node->items index — see _nodeItemsIndexCache at module scope.
    // Built once per flush by bucketing this.items: an item with placements
    // contributes to EACH DISTINCT node it runs on (the inner Set dedupes so
    // a service with 2 replicas on the same node isn't double-counted —
    // matches the old .some() filter), an item without placements goes to its
    // own node bucket.
    if (_nodeItemsIndexCache === null) {
      const idx = new Map();
      const add = (node, it) => {
        let arr = idx.get(node);
        if (!arr) {
          arr = [];
          idx.set(node, arr);
        }
        arr.push(it);
      };
      for (const it of this.items) {
        if (Array.isArray(it.placements) && it.placements.length) {
          const seen = new Set();
          for (const p of it.placements) {
            if (p && p.node && !seen.has(p.node)) {
              seen.add(p.node);
              add(p.node, it);
            }
          }
        } else if (it.node) {
          add(it.node, it);
        }
      }
      _nodeItemsIndexCache = idx;
      if (!_nodeItemsIndexScheduled) {
        _nodeItemsIndexScheduled = true;
        queueMicrotask(_clearNodeItemsIndexCache);
      }
    }
    return _nodeItemsIndexCache.get(host) || [];
  },

  nodeInfoFor(host) {
    return (this.nodesInfo && this.nodesInfo[host]) || {};
  },
  // Label for the green/red chip on a node row — reflects the
  // providers that actually probed THIS node. Falls back to
  // the global active set on rows missing per-node tracking (e.g.
  // before the first post-upgrade gather).
  nodeProviderChip(host) {
    const st = this.nodeStats(host);
    const arr = (st.nodeProvidersHit && st.nodeProvidersHit.length)
      ? st.nodeProvidersHit
      : (st.hostStatsSources || []);
    if (arr.length === 0) {
      return 'host';
    }
    if (arr.length === 1) {
      return arr[0] === 'node_exporter' ? 'exporter' : arr[0];
    }
    return `${arr.length} sources`;
  },
  // Hover tooltip for the chip — lists the providers that contributed
  // data for this specific node.
  nodeProviderList(host) {
    const st = this.nodeStats(host);
    const src = (st.nodeProvidersHit && st.nodeProvidersHit.length)
      ? st.nodeProvidersHit
      : (st.hostStatsSources || []);
    const arr = src.map(s => s === 'node_exporter' ? 'node-exporter' : s);
    return arr.length ? arr.join(', ') : 'none';
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
    const {cpuRaw, hostCpuRaw, hasHostCpu, cores} = this.nodeStats(host);
    if (hasHostCpu) {
      return Math.min(100, hostCpuRaw);
    }
    if (!cores) {
      return 0;
    }
    return Math.min(100, cpuRaw / cores);
  },

  nodeMemPercent(host) {
    const {memUsage, memLimit} = this.nodeStats(host);
    if (!memLimit) {
      return 0;
    }
    return Math.min(100, (memUsage / memLimit) * 100);
  },

  nodeDiskPercent(host) {
    // No "of N" denominator available without a host-agent — render
    // a proportional bar against the fleet's busiest Docker daemon
    // so operators see which node is carrying the most Docker disk.
    const {dockerDisk} = this.nodeStats(host);
    if (!dockerDisk) {
      return 0;
    }
    let max = 0;
    const infos = this.nodesInfo || {};
    for (const k of Object.keys(infos)) {
      const v = Number(infos[k] && infos[k].docker_disk_bytes) || 0;
      if (v > max) {
        max = v;
      }
    }
    if (!max) {
      return 0;
    }
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
    if (!ts) {
      return null;
    }
    return Math.max(0, Math.floor(Date.now() / 1000) - Math.floor(ts));
  },
  nodeUptimeKind(host) {
    // 'host' when sourced from node-exporter, 'docker' otherwise —
    // drives the caption on the Uptime tile.
    const info = this.nodeInfoFor(host);
    return (Number.isFinite(info.host_boot_ts) && info.host_boot_ts > 0) ? 'host' : 'docker';
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
    return {cpu, memUsage, sizeRoot, hasStats, hasSize};
  },
  // Title Case a free-text string. Handles ALL-CAPS replies from
  // SNMP printer agents (HP / Brother often return "BLACK
  // CARTRIDGE") and mixed case alike. Each word's first letter
  // upper, rest lower; whitespace and punctuation preserved.
  // Two exceptions: known short brand acronyms (HP, IBM, ...) and
  // alphanumeric SKU codes (e.g. "3JA27A", "Q3960A") render ALL CAPS
  // — operator request 2026-05-01 for printer supply labels like
  // "Cyan Ink Hp 3ja27a" → "Cyan Ink HP 3JA27A".
  titleCase(s) {
    if (!s || typeof s !== 'string') {
      return s || '';
    }
    const brands = new Set(['hp', 'hpe', 'ibm', 'amd', 'arm', 'lg', 'rgb', 'rfid', 'usb', 'pci', 'io', 'smb', 'ftp', 'http', 'https', 'tls', 'ssl', 'nfc', 'vpn', 'dns', 'dhcp', 'ip', 'tcp', 'udp', 'rj45', 'poe', 'sfp', 'sas', 'sata', 'nvme', 'ssd', 'hdd', 'iot', 'ai', 'ml', 'gpu', 'cpu', 'ram', 'rom', 'vrm', 'bmc', 'ipmi', 'sff']);
    return s.replace(/\w\S*/g, w => {
      const lo = w.toLowerCase();
      if (brands.has(lo)) {
        return w.toUpperCase();
      }
      if (/[a-z]/i.test(w) && /[0-9]/.test(w)) {
        return w.toUpperCase();
      }
      return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
    });
  },
  memPercent(item) {
    const s = this.statsFor(item);
    if (!s.mem_limit) {
      return 0;
    }
    return Math.min(100, (s.mem_usage / s.mem_limit) * 100);
  },
  diskPercent(item) {
    const s = this.statsFor(item);
    if (!this._maxSize) {
      return 0;
    }
    return Math.min(100, (s.size_root / this._maxSize) * 100);
  },
  // LOW-VISUAL — stat-bar thresholds are operator-tunable.
  // Pre-fix the 60 / 85 thresholds were hardcoded; the project conventions's
  // no-static-config rule says operator-tunable visual thresholds
  // belong in TUNABLES. Now sourced from `client_config` (per-call
  // read so an Admin → Config save lands on the next render).
  _statBarWarnPct() {
    const v = this.me && this.me.client_config && this.me.client_config.stat_bar_warn_pct;
    const n = parseInt(v, 10);
    return Number.isFinite(n) && n >= 30 && n <= 90 ? n : 60;
  },
  // Resolved global SNMP per-host walk concurrency — drives the
  // Admin → Hosts editor's per-row "Walk concurrency" input
  // placeholder. Shows the actual effective default value prefixed
  // with "Inherited: " so an empty input + placeholder "Inherited: 1"
  // visually distinguishes itself from a real value of 1 (pre-fix
  // operator couldn't tell whether they'd left the field blank or
  // typed 1). Sourced from `me.client_config.snmp_per_host_walk_concurrency`
  // so an Admin → Config save takes effect on the next /api/me round-trip.
  snmpWalkConcurrencyPlaceholder() {
    const v = this.me && this.me.client_config
      && this.me.client_config.snmp_per_host_walk_concurrency;
    const n = parseInt(v, 10);
    const num = Number.isFinite(n) && n >= 1 ? n : 1;
    return this.t('admin_hosts.snmp_walk_concurrency_placeholder', {value: num});
  },
  // Per-host wall-clock-budget input placeholder — same "Inherited: <N>"
  // pattern as walk_concurrency. Sourced from
  // me.client_config.snmp_wall_clock_budget_seconds (surfaced from the
  // tunable on /api/me).
  snmpWallClockBudgetPlaceholder() {
    const v = this.me && this.me.client_config
      && this.me.client_config.snmp_wall_clock_budget_seconds;
    const n = parseInt(v, 10);
    const num = Number.isFinite(n) && n >= 5 ? n : 60;
    return this.t('admin_hosts.snmp_walk_concurrency_placeholder', {value: num});
  },
  // Per-host SNMP vendor MIB selector. Empty list = auto-detect from
  // sysDescr (the common case; covers 95% of agents). Operator can
  // declare explicit vendors to bypass auto-detect — useful for
  // agents with stripped sysDescr or to force a vendor's walks. The
  // backend's _clean_host_snmp validates against the same vendor key
  // set: dell / cisco / apc / ucd / synology / printer.
  snmpVendorChecked(row, vendor) {
    const list = (row && row.snmp && Array.isArray(row.snmp.vendors))
      ? row.snmp.vendors : [];
    return list.includes(vendor);
  },
  // Apply or clear ``vendor`` on EVERY row that passes the current
  // hostsConfig filter. ``add=true`` = check the vendor on each row's
  // snmp.vendors array; ``add=false`` = remove it. Skips rows where
  // `snmp.enabled !== true` (the per-row checkbox group is gated on
  // that flag and wouldn't accept the click). Marks each touched row
  // dirty so the existing Save flow picks them up.
  bulkApplySnmpVendor(vendor, add) {
    if (!vendor || this.isReadonly()) {
      return;
    }
    const validSet = new Set(this.snmpVendorKeys());
    if (!validSet.has(vendor)) {
      return;
    }
    let touched = 0;
    const rows = this.filteredHostsConfig();
    for (const entry of rows) {
      const idx = (entry && typeof entry.idx === 'number') ? entry.idx : -1;
      if (idx < 0) {
        continue;
      }
      const cur = this.hostsConfig[idx];
      if (!cur) {
        continue;
      }
      const snmpIn = cur.snmp || {};
      if (snmpIn.enabled !== true) {
        continue;
      }
      const list = Array.isArray(snmpIn.vendors) ? snmpIn.vendors.slice() : [];
      const set = new Set(list);
      const had = set.has(vendor);
      if (add) {
        if (had) {
          continue;
        }
        set.add(vendor);
      } else {
        if (!had) {
          continue;
        }
        set.delete(vendor);
      }
      this.hostsConfig[idx].snmp = Object.assign({}, snmpIn, {vendors: Array.from(set).sort()});
      this.markHostRowDirty(idx);
      touched += 1;
    }
    if (touched > 0) {
      this.showToast(this.t(
        add ? 'admin_hosts.snmp_vendors_bulk_applied'
          : 'admin_hosts.snmp_vendors_bulk_cleared',
        {vendor: this.snmpVendorLabel(vendor), count: touched}
      ));
    } else {
      // Always-feedback contract: silent no-ops read as "the button
      // is broken" to operators. Explain why nothing happened — the
      // most common cases are (a) every filtered row already had the
      // target state (no work to do), (b) every filtered row has
      // snmp.enabled !== true so the bulk-apply correctly skipped
      // them (the inner loop guards on `snmp.enabled` to avoid
      // toggling vendor state on hosts where SNMP isn't even on).
      // Toast lands in the WARN tier so it's visually distinct from
      // a SUCCESS bulk-apply.
      this.showToast(this.t(
        'admin_hosts.snmp_vendors_bulk_noop',
        {vendor: this.snmpVendorLabel(vendor), count: rows.length}
      ), 'warning');
    }
  },
  // Last auto-detected vendor set for THIS curated row, sourced
  // from the matching live host's `host_snmp_active_vendors` field
  // (populated by `_merge_one_host` from the most recent successful
  // probe's diagnostic). Returns an empty list when (a) the host has
  // never been probed successfully, OR (b) the host doesn't appear
  // in the loaded `this.hosts` array (Admin → Hosts editor is open
  // but the Hosts view hasn't loaded yet — the helper just returns
  // empty until the row's host data lands). Helps operators new to
  // SNMP see what auto-detect picked before deciding whether to set
  // an explicit override.
  snmpAutoDetectedVendors(row) {
    if (!row || !row.id) {
      return [];
    }
    const list = Array.isArray(this.hosts) ? this.hosts : [];
    const live = list.find(h => h && h.id === row.id);
    const av = live && live.host_snmp_active_vendors;
    return Array.isArray(av) ? av.slice() : [];
  },
  // Canonical SNMP vendor key set sourced from /api/me's
  // client_config.snmp_vendor_keys (single source of truth — backed by
  // logic/snmp.py:_VALID_VENDOR_KEYS server-side). Adding a vendor in
  // _VENDOR_SIGNATURES surfaces a checkbox here on the next /api/me
  // round-trip without any frontend edit. Defence-in-depth fallback to
  // the historical six keys when /api/me hasn't hydrated yet OR the
  // server is older than this SPA build.
  snmpVendorKeys() {
    const cc = (this.me && this.me.client_config) || {};
    if (Array.isArray(cc.snmp_vendor_keys) && cc.snmp_vendor_keys.length) {
      return cc.snmp_vendor_keys;
    }
    return ['apc', 'cisco', 'dell', 'printer', 'synology', 'ucd'];
  },
  // Operator-friendly label for an SNMP vendor key. The persisted
  // values stay lowercase to match `_VALID_VENDOR_KEYS` server-side,
  // but the rendered checkbox text uses the brand-canonical case
  // (Dell / Cisco / APC / Synology / Printer / UCD/net-snmp).
  // Unknown keys fall through to a Title-Case fallback so a future
  // vendor added on the backend still renders sensibly without a
  // SPA edit.
  snmpVendorLabel(key) {
    const k = String(key || '').trim().toLowerCase();
    const map = {
      'apc': 'APC',
      'cisco': 'Cisco',
      'dell': 'Dell',
      'synology': 'Synology',
      'printer': 'Printer',
      'ucd': 'UCD/net-snmp',
    };
    if (k in map) {
      return map[k];
    }
    return k ? k[0].toUpperCase() + k.slice(1) : '';
  },
  toggleSnmpVendor(idx, vendor, checked) {
    const cur = this.hostsConfig[idx];
    const snmp = Object.assign({}, cur.snmp || {});
    const list = Array.isArray(snmp.vendors) ? snmp.vendors.slice() : [];
    const set = new Set(list);
    if (checked) {
      set.add(vendor);
    } else {
      set.delete(vendor);
    }
    snmp.vendors = Array.from(set).sort();
    this.hostsConfig[idx].snmp = snmp;
    this.markHostRowDirty(idx);
  },
  _statBarCritPct() {
    const v = this.me && this.me.client_config && this.me.client_config.stat_bar_crit_pct;
    const n = parseInt(v, 10);
    return Number.isFinite(n) && n >= 50 && n <= 99 ? n : 85;
  },
  barColor(pct) {
    // Kept for backward compat; prefer barLevel() which returns a CSS class.
    if (pct > this._statBarCritPct()) {
      return 'var(--danger)';
    }
    if (pct > this._statBarWarnPct()) {
      return 'var(--warning)';
    }
    return 'var(--success)';
  },
  barLevel(pct) {
    // Maps a percentage to the `.warn` / `.crit` class on `.stat-bar`, which
    // drives the fill colour from the stylesheet. Empty string = default green.
    if (pct > this._statBarCritPct()) {
      return 'crit';
    }
    if (pct > this._statBarWarnPct()) {
      return 'warn';
    }
    return '';
  },
  // Single source of truth for stat-bar a11y attrs. Returns a plain
  // object spread via x-bind so every `.stat-bar` consumer announces
  // as a real progressbar instead of an empty div. The label key
  // resolves through `t()` so screen readers get a localised hint
  // (e.g. "CPU 73%"). Pass null/undefined `pct` for "unknown" — the
  // resulting valuenow is omitted but valuetext still names the
  // metric. Replaces 20+ inline rewrites with one helper.
  statBarBind(pct, labelKey) {
    const numeric = (typeof pct === 'number' && isFinite(pct))
      ? Math.max(0, Math.min(100, Math.round(pct)))
      : null;
    const label = (labelKey && typeof this.t === 'function') ? this.t(labelKey) : '';
    const text = numeric == null
      ? (label || '—')
      : (label ? `${label} ${numeric}%` : `${numeric}%`);
    return {
      role: 'progressbar',
      'aria-valuemin': '0',
      'aria-valuemax': '100',
      'aria-valuenow': numeric == null ? null : String(numeric),
      'aria-valuetext': text,
    };
  },
  cpuLabel(pct) {
    return pct >= 10 ? pct.toFixed(0) + '%' : pct.toFixed(1) + '%';
  },
  imageRepo(item) {
    if (!item || !item.image) {
      return '';
    }
    const img = item.image;
    const tag = item.tag || '';
    if (tag && img.endsWith(':' + tag)) {
      return img.slice(0, -(tag.length + 1));
    }
    return img;
  },
  nodeSummary(item) {
    const ns = (item.placements || []).map(p => p.node).filter(n => n && n !== '?');
    return [...new Set(ns)].join(', ');
  },

  // Resolve the http://<host>:<published> URL for an item's exposed
  // port. Returns '' when no host can be determined OR the port
  // protocol isn't tcp (UDP / SCTP have no in-browser navigation).
  //
  // Host resolution chain:
  //   1. Direct `item.node` (only standalone containers carry this).
  //   2. First running placement's node from `item.placements` —
  //      this is where Swarm services live. Pre-fix this step was
  //      missing so services fell through to the Portainer URL
  //      (user-flagged: a service on a worker node linked to
  //      `portainer.example.com:9618` instead of
  //      `<worker-hostname>:<published>`).
  //   3. Any placement's node from `item.placements` (running pref
  //      first, but accept stopped placements as a last hint).
  //   4. Hostname extracted from the Portainer public URL — works
  //      for ingress-mode publishes that genuinely route on every
  //      Swarm node.
  //   5. window.location.hostname so the link still navigates
  //      somewhere reasonable in a dev / single-host setup.
  //
  // For steps 1-3, the resolved node-hostname is mapped against
  // curated hosts (id / host_hostname / label) and the curated row's
  // `address` field is preferred when available. When no curated
  // match exists, the raw node hostname itself is used (Swarm node
  // hostnames typically resolve in the operator's local DNS / hosts
  // file, otherwise the operator types the IP in Admin → Hosts and
  // the curated match kicks in).
  //
  // Protocol is always http:// — Docker port publish doesn't imply
  // TLS; operators with TLS-fronted services reach them via their
  // own ingress separately.
  itemPortLink(item, port) {
    if (!item || !port || !port.published) {
      return '';
    }
    const proto = (port.protocol || 'tcp').toLowerCase();
    if (proto !== 'tcp') {
      return '';
    }
    // Build the candidate node-hostname list in priority order.
    const candidateNodes = [];
    if (item.node) {
      candidateNodes.push(item.node);
    }
    if (Array.isArray(item.placements)) {
      // Running placements first, then everything else — same node
      // may appear twice but the curated-host lookup is identity-
      // tolerant so the duplicate is harmless.
      for (const p of item.placements) {
        if (p && p.node && p.state === 'running') {
          candidateNodes.push(p.node);
        }
      }
      for (const p of item.placements) {
        if (p && p.node) {
          candidateNodes.push(p.node);
        }
      }
    }
    const _hostsArr = Array.isArray(this.hosts) ? this.hosts : [];
    const _findCurated = (nodeName) => {
      if (!nodeName) {
        return null;
      }
      const lower = String(nodeName).toLowerCase();
      return _hostsArr.find(h => {
        if (!h) {
          return false;
        }
        return [h.id, h.host_hostname, h.label].some(c => c && String(c).toLowerCase() === lower);
      }) || null;
    };
    // Walk candidates in order, resolving via curated-host match
    // when possible. The raw node hostname is acceptable too (Swarm
    // hostnames usually resolve in the operator's local DNS) — we
    // just prefer the curated `address` when it's set.
    let host = '';
    for (const nodeName of candidateNodes) {
      if (!nodeName) {
        continue;
      }
      const match = _findCurated(nodeName);
      if (match) {
        host = (match.address || match.host_hostname || match.id || nodeName || '').trim();
        if (host) {
          break;
        }
      }
      // No curated match — fall back to the raw node hostname.
      host = String(nodeName).trim();
      if (host) {
        break;
      }
    }
    // Portainer public URL fallback — ingress-mode publishes land
    // on every Swarm node, so the public URL hostname is reasonable
    // when no placement-derived candidate worked.
    if (!host) {
      const pubUrl = (this.settings && this.settings.portainer_public_url) || '';
      if (pubUrl) {
        try {
          host = new URL(pubUrl).hostname;
        } catch {
          // ignore
        }
      }
    }
    // Last resort: the SPA's own window hostname.
    if (!host) {
      host = window.location.hostname || '';
    }
    if (!host) {
      return '';
    }
    // Short-hostname → FQDN promotion. Swarm reports node names as
    // bare hostnames (e.g. `worker01`, `web01`) which don't always
    // resolve in the browser unless the user's DNS handles short
    // names. When the resolved host has NO dots, promote it to a
    // FQDN by learning the LAN domain suffix.
    //
    // Resolution order for the suffix:
    //   1. **Inspect curated hosts** — find any other curated row
    //      whose `address` OR `host_hostname` is a FQDN (multi-
    //      label, non-IP). Use the longest-common-suffix across
    //      those FQDNs as the operator's actual LAN domain. This
    //      is the authoritative source — if the operator has set
    //      `worker01.example.lan` as another host's address,
    //      we know `example.lan` is the right suffix.
    //   2. Fallback: last TWO labels of `window.location.hostname`.
    //      Naive "everything after the first dot" fails when the
    //      SPA itself sits on a sub-subdomain (e.g. SPA at
    //      `omnigrid.www.example.lan` — the LAN is still
    //      `example.lan`, not `www.example.lan`). Using the
    //      trailing two labels handles both shapes:
    //      `omnigrid.example.lan` → `example.lan`, AND
    //      `omnigrid.www.example.lan` → `example.lan`.
    //   3. When neither yields a multi-label suffix (e.g. browser
    //      at `localhost` or a bare-host setup), skip the promotion
    //      and use the bare hostname.
    // Skip the promotion entirely when host is an IPv4 literal —
    // appending a DNS suffix to an IP makes no sense.
    if (host && host.indexOf('.') < 0 && !/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) {
      let suffix = this._resolveLanSuffixFromCuratedHosts(host);
      if (!suffix) {
        // Fallback: last-2-labels of window.location.hostname.
        const winHost = window.location.hostname || '';
        const parts = winHost.split('.').filter(Boolean);
        if (parts.length >= 2 && !/^\d+$/.test(parts[parts.length - 1])) {
          suffix = parts.slice(-2).join('.');
        }
      }
      if (suffix && suffix.indexOf('.') >= 0) {
        host = host + '.' + suffix;
      }
    }
    return 'http://' + host + ':' + port.published;
  },

  // Learn the operator's LAN domain suffix from curated host
  // records. Walks `this.hosts` looking for any FQDN in `address` /
  // `host_hostname` / `label` (multi-label string, not an IPv4
  // literal, not matching the current host's bare name we're
  // promoting). Returns the longest-common-suffix shared by every
  // FQDN found — that's the operator's authoritative LAN domain.
  // Returns '' when no curated FQDN exists.
  //
  // Why longest-common-suffix: if the operator has
  // `worker01.example.lan` AND `web01.example.lan` AND
  // `mail.corp.example.lan`, the LCS is `example.lan` — the right
  // value. A single curated FQDN's suffix would falsely match
  // `mail.corp.example.lan` → `corp.example.lan` which isn't the
  // bare LAN root. With multiple FQDNs we hit `example.lan` correctly.
  _resolveLanSuffixFromCuratedHosts(promotingHost) {
    if (!Array.isArray(this.hosts)) {
      return '';
    }
    const promotingLower = String(promotingHost || '').toLowerCase();
    const fqdns = [];
    for (const h of this.hosts) {
      if (!h) {
        continue;
      }
      for (const fieldVal of [h.address, h.host_hostname, h.label]) {
        const v = String(fieldVal || '').trim().toLowerCase();
        if (!v || v.indexOf('.') < 0) {
          continue;
        }
        // Skip IPv4 literals — they're not FQDNs.
        if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(v)) {
          continue;
        }
        // Skip URL-shaped values (some `address` rows are full URLs).
        if (v.indexOf('://') >= 0 || v.indexOf('/') >= 0 || v.indexOf(':') >= 0) {
          continue;
        }
        // Skip the bare-host we're TRYING to promote (defence
        // against circular learning if the operator's `address`
        // already happens to be the bare short hostname).
        if (v === promotingLower) {
          continue;
        }
        // Only consider strings that look hostname-shaped.
        if (!/^[a-z0-9.-]+$/.test(v)) {
          continue;
        }
        fqdns.push(v);
      }
    }
    if (fqdns.length === 0) {
      return '';
    }
    // Compute longest-common-suffix label-wise across every FQDN.
    // Reverse the labels of each, find the common prefix, reverse
    // back. Truncate any leading single-label result — we need at
    // least 2 labels for a meaningful suffix.
    const splitRev = fqdns.map(f => f.split('.').reverse());
    let common = splitRev[0].slice();
    for (let i = 1; i < splitRev.length; i++) {
      const next = splitRev[i];
      const lim = Math.min(common.length, next.length);
      const out = [];
      for (let j = 0; j < lim; j++) {
        if (common[j] === next[j]) {
          out.push(common[j]);
        } else {
          break;
        }
      }
      common = out;
      if (common.length === 0) {
        break;
      }
    }
    if (common.length < 2) {
      return '';
    }
    return common.reverse().join('.');
  },

  // Resolve a Swarm node hostname to a curated host row so the SSH
  // terminal can target it. Tries (in order): exact id match, exact
  // host match, prefix match (so `host01` matches
  // `host01.example.com`). Returns null when nothing matches.
  _findHostByNodeName(nodeName) {
    if (!nodeName || !Array.isArray(this.hosts)) {
      return null;
    }
    const needle = String(nodeName).trim().toLowerCase();
    if (!needle) {
      return null;
    }
    const exactId = this.hosts.find(h => h && (h.id || '').toLowerCase() === needle);
    if (exactId) {
      return exactId;
    }
    const exactHost = this.hosts.find(h => h && (h.host || '').toLowerCase() === needle);
    if (exactHost) {
      return exactHost;
    }
    // Prefix match — the node hostname's first label often equals
    // the curated id's first label. Both sides split on '.' and
    // compared so `host01` ↔ `host01.example.com`.
    const stem = needle.split('.')[0];
    const stemMatch = this.hosts.find(h => {
      const hid = (h && h.id || '').toLowerCase().split('.')[0];
      const hh = (h && h.host || '').toLowerCase().split('.')[0];
      return stem && (hid === stem || hh === stem);
    });
    return stemMatch || null;
  },
  uptimeFor(item) {
    // Services: Swarm reports ISO-8601 `updated` — last spec change, a good
    // proxy for "running since". Containers: Unix seconds `created`.
    if (!item) {
      return null;
    }
    const raw = item.type === 'service' ? item.updated : item.created;
    if (raw == null || raw === '') {
      return null;
    }
    const ms = typeof raw === 'number' ? raw * 1000 : Date.parse(raw);
    return isNaN(ms) ? null : ms;
  },
};
