// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ContinueStatementJS,BreakStatementJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML,JSAsyncFunctionMissingAwait
// noinspection JSMissingAwait,JSUnfilteredForInLoop,IfStatementWithoutBlockJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSPotentiallyInvalidUsageOfThis,JSIgnoredPromiseFromCall,VoidExpressionJS,JSVoidExpression,ControlFlowStatementWithoutBracesJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode, EventSource */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Server-Sent Events (SSE) live-update plumbing.

export default {

  // Reactive flag — flips true after `backend_unreachable_threshold_seconds`
  // of silence from both SSE + REST, drives the top-of-page offline banner.
  // Seeded false so the banner never flashes on first paint before init()
  // has had a chance to stamp `window.__ogLastBackendOkTs`.
  backendUnreachable: false,

  // Per-tab dismissal flag for the Admin → Authentication
  // "SESSION_SECRET auto-generated" banner. Hydrated from sessionStorage
  // in init() so a single tab session keeps the dismissal but a fresh
  // browser session (or different tab) re-shows the warning since the
  // underlying threat is still active. The banner is gated on BOTH
  // `me.client_config.session_secret_auto_generated` AND
  // `!sessionSecretBannerDismissed` so dismissing it in the SPA hides
  // it without touching server state.
  sessionSecretBannerDismissed: false,

  _initSSE() {
    // Defence-in-depth: never start two streams. ``init()`` runs once
    // per Alpine component instance but a future hot-reload path
    // could call it twice.
    if (this._sse) {
      try {
        this._sse.close();
      } catch (_) {
      }
      this._sse = null;
    }
    console.log('[live] SSE init: opening EventSource at /api/events');
    let es;
    try {
      es = new EventSource('/api/events', {withCredentials: true});
    } catch (e) {
      console.warn('[events] EventSource not supported in this browser — staying on polling', e);
      return;
    }
    this._sse = es;
    const onAny = () => {
      this._sseLastEventTs = Date.now();
      this._sseConnected = true;
      // Any SSE frame (incl. keepalive) is a fresh proof-of-life from the
      // backend — feed the backend-unreachable banner watcher's clock so the
      // banner hides as soon as the stream resumes. Window-global mirrors
      // the same channel the fetch wrapper writes to (auth-fetch.js).
      window.__ogLastBackendOkTs = Date.now();
    };
    es.addEventListener('open', () => {
      onAny();
      this._sseReconnects += 1;
      // First connect carries _sseReconnects === 1 (baseline). Every
      // bump above 1 represents a recover from a drop — kick a one-
      // shot REST refresh so the SPA catches up on what it missed
      // while disconnected.
      if (this._sseReconnects > 1) {
        console.log('[live] SSE reconnect: kicking one-shot REST refresh to catch up missed deltas');
        try {
          this.refresh(true);
        } catch (_) {
        }
        try {
          if (this.view === 'hosts') {
            this.loadHosts(true);
          }
        } catch (_) {
        }
        try {
          if (this.loadHistory) {
            this.loadHistory();
          }
        } catch (_) {
        }
      }
    });
    // ``hello`` is the bus's first frame after upgrade — treat as a
    // confirmation of healthy stream rather than a real event.
    es.addEventListener('hello', () => {
      onAny();
    });
    // `keepalive` heartbeat. Server emits one every
    // tuning_sse_heartbeat_seconds during quiet windows so the
    // freshness watchdog clock keeps advancing and we don't false-
    // flip to polling-fallback. `onAny()` updates `_sseLastEventTs`;
    // no other side-effect (it's a synthetic ping with empty
    // payload).
    es.addEventListener('keepalive', () => {
      onAny();
    });
    // ``:overflow`` signals the per-subscriber queue dropped events
    // (slow consumer / throttled tab). Backend emits this BEFORE
    // resuming the live stream so we know to reconcile via REST.
    es.addEventListener(':overflow', () => {
      onAny();
      this._sseDropped += 1;
      console.warn('[live] event=:overflow — subscriber queue dropped events; reconciling via REST (total dropped this session: ' + this._sseDropped + ')');
      // operator-visible signal. Pre-fix this lived in
      // DevTools console only; an overflow means "your tab missed
      // events; we just reconciled via REST" — worth a non-intrusive
      // amber toast so the operator sees the flap and can correlate
      // with whatever caused the burst.
      try {
        this.showToast(
          this.t('toasts_extra.sse_overflow') || 'Live event stream backlogged — refreshed automatically',
          'warning'
        );
      } catch (_) {
      }
      try {
        this.refresh(true);
      } catch (_) {
      }
      try {
        if (this.loadHistory) {
          this.loadHistory();
        }
      } catch (_) {
      }
      if (this.view === 'hosts') {
        try {
          this.loadHosts(true);
        } catch (_) {
        }
      }
    });
    // Self-filter check FIRST so the console.log only fires for events
    // we'll actually act on. Pre-fix the log fired even on self-
    // originated events that _handleOpEvent then filtered out — log
    // spam on every op the originating tab triggered.
    es.addEventListener('op:created', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      this._handleOpEvent(e, 'created');
    });
    es.addEventListener('op:updated', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      this._handleOpEvent(e, 'updated');
    });
    es.addEventListener('op:completed', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      this._handleOpEvent(e, 'completed');
    });
    es.addEventListener('cache:invalidated', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      // Items dataset is large enough that delta-broadcasting it
      // isn't worth it for V1 — kick a forced refresh instead, and
      // let the existing in-place reconcile in `refresh()` do its
      // work without tearing rows down.
      //
      // DEBOUNCED (trailing): a Cleanup / bulk-remove in ANOTHER tab
      // fires ONE `cache:invalidated` PER removed container — a burst
      // of N events. Firing refresh(true) on each would launch N
      // concurrent forced gathers; worse, the inter-event timing could
      // land a refresh mid-burst (before the last container is gone)
      // and leave this tab's "Cleanup (N)" button stale. Coalescing to
      // a single trailing refresh ~400ms after the burst settles
      // guarantees ONE refresh that reads the final post-cleanup state,
      // so the other tabs drop the removed items + hide the Cleanup
      // button without the operator refreshing each tab by hand.
      this._scheduleCacheInvalidatedRefresh();
    });
    es.addEventListener('stats:refreshed', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      // Hint event — the stats payload itself isn't broadcast
      // (cheap to fetch via /api/stats and the existing TTL gate
      // prevents back-to-back pulls). Trigger loadStats UNLESS the
      // operator has set the cadence picker to Off
      // (`refreshInterval === 0`); in Live mode (-1) and any
      // positive cadence, fresh stats data is exactly what the
      // operator opted in to see. Pre-fix this gated on
      // `statsInterval > 0`, but `setRefreshInterval` remaps Live
      // (-1) to legacy `statsInterval = 0` (since polling sleeps in
      // Live and SSE drives updates), so the handler skipped Live
      // entirely — the dashboard's stats data never refreshed in
      // Live mode after a backend gather completed, leaving bars
      // stuck on seeded-stale snapshot data and the topbar refresh
      // spinner spinning on the never-cleared `stats_refreshing`
      // flag from the initial response.
      if (this.refreshInterval === 0) {
        return;
      }
      try {
        if (this.loadStats) {
          this.loadStats();
        }
      } catch (_) {
      }
    });
    es.addEventListener('host:row_updated', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      try {
        const data = JSON.parse(e.data || '{}');
        const id = (data.payload && data.payload.id) || '';
        console.log('[live] event=host:row_updated id=' + id);
        if (!id) {
          return;
        }
        // Route through the SHARED queue + worker pool so a burst
        // of N events (sampler tick affecting many hosts) coalesces
        // through the existing 200ms debounce + shares the cap.
        // Pre-fix `refreshHostRow` was called directly here,
        // bypassing both worker pools — bursts could exceed the
        // operator-set parallel cap.
        this._hostObserverPending = this._hostObserverPending || new Set();
        this._hostObserverPending.add(id);
        if (typeof this._scheduleHostObserverFlush === 'function') {
          this._scheduleHostObserverFlush();
        } else if (typeof this._runHostRefreshQueue === 'function') {
          // Fallback path if the IO observer hasn't initialised yet
          // (e.g. browsers without IntersectionObserver). Direct
          // enqueue still goes through the shared worker pool.
          this._runHostRefreshQueue([id]).catch(() => {
          });
        }
      } catch (_) {
      }
    });
    es.addEventListener('port_scan:completed', (e) => {
      onAny();
      // NOTE: do NOT short-circuit on `_isSelfEvent` — the SPA
      // tab that kicked the scan off NEEDS to see this event so
      // it can clear the spinner + show the completion toast.
      // The "self-event filter" pattern is for cross-tab dedupe
      // of state changes the originating tab already mirrored
      // optimistically; here, the originating tab is sitting on
      // a 202-Accepted with the spinner still running and is
      // EXACTLY the audience that needs the event.
      try {
        const data = JSON.parse(e.data || '{}');
        const payload = data.payload || {};
        const hostId = payload.host_id || '';
        if (!hostId) {
          return;
        }
        console.log('[live] event=port_scan:completed id=' + hostId
          + ' ports_open=' + (payload.ports_open || 0)
          + ' udp_open=' + (payload.udp_open || 0)
          + ' ok=' + payload.ok);
        // Clear the in-flight marker + the row's spinner on EVERY
        // tab that has the host loaded — not just the originating
        // tab. Two tabs both watching opnsense both see the
        // button re-enable when the scan finishes.
        const row = (this.hosts || []).find(h => h && h.id === hostId);
        if (row) {
          row._port_scan_running = false;
        }
        if (this.drawerHost && this.drawerHost.id === hostId) {
          this.drawerHost._port_scan_running = false;
        }
        // Refresh the host row so `detected_ports` + `last_port_scan_ts`
        // repaint without a manual reload.
        this.refreshHostRow(hostId, {force: true});
        // Surface a completion toast IF this tab kicked off the
        // scan (recorded by `runPortScan` in `_inFlightPortScans`).
        // Other tabs see the row update but skip the toast — only
        // one tab gets the "scan complete" feedback.
        if (this._inFlightPortScans && this._inFlightPortScans[hostId]) {
          delete this._inFlightPortScans[hostId];
          if (payload.ok) {
            const openCount = (payload.ports_open || 0) + (payload.udp_open || 0);
            // First-scan toast hides the "(N new since last scan)"
            // suffix because there is no last scan to diff against —
            // the parenthetical is misleading on the host's first
            // scan ever. Backend signals via `is_first_scan` in the
            // SSE payload (only true when no prior scan_id row
            // exists in `host_port_scans` for this host).
            // Surface the configured scan target — what the user
            // actually typed in Admin → Hosts (Hostname or IP, or
            // a provider override). The wire-level `resolved_ip`
            // from getaddrinfo is no longer included in the toast
            // parenthetical: user-flagged that the resolver-derived
            // IP is often surprising / wrong (Docker bridge
            // gateway, search-domain mis-resolution, /etc/hosts
            // override) and showing it next to the configured
            // address conflated "what I asked for" with "what the
            // OS resolved it to". The configured value is what the
            // user expects. The DNS-failure path below is the
            // exception — when getaddrinfo failed AND zero ports
            // came back, that's a real "your alias doesn't
            // resolve" signal worth surfacing.
            const _resolvedIp = payload.resolved_ip || '';
            const _target = payload.target || hostId;
            const _dnsFailed = !_resolvedIp;
            if (_dnsFailed && openCount === 0 && !payload.udp_open) {
              this.showToast(this.t('host_drawer.port_scan.scan_dns_failed', {
                host: _target,
              }) || ('DNS lookup failed for ' + _target
                + ' — set the Hostname or IP in Admin → Hosts to a reachable address.'),
                'error');
            } else {
              const i18nKey = payload.is_first_scan
                ? 'host_drawer.port_scan.scan_complete_body_first'
                : 'host_drawer.port_scan.scan_complete_body';
              // Backend computes the new-since-last-scan diff as
              // `payload.new_count` (raw count — port+proto tuples
              // in this scan but not the previous one). Pre-fix
              // this was hardcoded to 0 with the comment "diff
              // happens server-side via notify path", but the
              // notify path is for individual port-emerged
              // notifications — the toast still wants the
              // aggregate diff count. Falls back to 0 when the
              // backend doesn't ship the field (very old payload).
              this.showToast(this.t(i18nKey, {
                host: _target,
                open_count: openCount,
                new_count: Number(payload.new_count) || 0,
              }), 'success');
            }
          } else {
            const _targetErr = payload.target || hostId;
            this.showToast(this.t('host_drawer.port_scan.scan_failed_body', {
              host: _targetErr,
              error: payload.error || 'unknown',
            }), 'error');
          }
        }
      } catch (_) {
      }
    });
    es.addEventListener('host:failure_state_changed', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      try {
        const data = JSON.parse(e.data || '{}');
        const payload = data.payload || {};
        const id = payload.host_id || '';
        console.log('[live] event=host:failure_state_changed id=' + id);
        if (!id) {
          return;
        }
        this._hostObserverPending = this._hostObserverPending || new Set();
        this._hostObserverPending.add(id);
        if (typeof this._scheduleHostObserverFlush === 'function') {
          this._scheduleHostObserverFlush();
        } else if (typeof this._runHostRefreshQueue === 'function') {
          this._runHostRefreshQueue([id]).catch(() => {
          });
        }
        // Proactive incident chip — only surface when the operator
        // has the AI sidebar open AND the new state is actionable
        // (paused / failing). Recovered states ("up") are good news
        // and don't warrant a "investigate?" prompt.
        const newState = payload.state || payload.new_state || '';
        if (this.aiSidebarOpen && (newState === 'paused' || newState === 'failing')) {
          this._setAiIncidentChip({
            kind: 'host_failure',
            host_id: id,
            title: id + ' entered ' + newState + ' state — investigate?',
            query: 'Why is host ' + id + ' in ' + newState + ' state? Walk me through the likely cause and what to check.',
          });
        }
      } catch (_) {
      }
    });
    // Bulk-action event — backend publishes ONE frame per bulk
    // endpoint (pause / resume / snmp_vendors / snmp_tunables)
    // carrying every applied host_id in the payload. SPA reconciles
    // each id in the same way the per-host handler above does
    // (single observer-pending Set + flush). For curated-config
    // edits (snmp_vendors / snmp_tunables) we ALSO trigger a
    // background `loadHosts(true)` so the curated overlay (snmp
    // sub-block, vendors list) re-syncs across tabs.
    es.addEventListener('host:bulk_action_applied', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      try {
        const data = JSON.parse(e.data || '{}');
        const payload = data.payload || {};
        const action = payload.action || '';
        const ids = Array.isArray(payload.host_ids) ? payload.host_ids : [];
        console.log('[live] event=host:bulk_action_applied action=' + action + ' ids=' + ids.length);
        if (ids.length === 0) {
          return;
        }
        // Per-row refresh — same path as the per-host handler.
        this._hostObserverPending = this._hostObserverPending || new Set();
        for (const id of ids) {
          this._hostObserverPending.add(id);
        }
        if (typeof this._scheduleHostObserverFlush === 'function') {
          this._scheduleHostObserverFlush();
        } else if (typeof this._runHostRefreshQueue === 'function') {
          this._runHostRefreshQueue(ids.slice()).catch(() => {
          });
        }
        // Curated-config actions also need a `hosts_config`-level
        // reload so the SPA's snmp_name / snmp.vendors / snmp.walk_*
        // overlays pick up the new server-side state.
        if ((action === 'snmp_vendors' || action === 'snmp_tunables')
          && typeof this.loadHosts === 'function') {
          this.loadHosts(true);
        }
      } catch (_) {
      }
    });
    // Per-(provider, host) probe-status events. Backend fires these
    // around each in-flight per-host probe slice (SNMP / Webmin / NE)
    // so a chip pulses ONLY while ITS specific probe is running, not
    // the whole row-wide `_loading` window. Cache hits skip the
    // events (no real probe ran) so chips stay at rest. Beszel /
    // Pulse / Ping skip too — they're either dict lookups (Beszel /
    // Pulse) or sampler-driven reads (Ping) inside `_merge_one_host`.
    // The row-level `_loading` pulse still covers the
    // initial-paint case for those.
    // Minimum visible-pulse duration. Without this, fast probes
    // (cache miss but warm hub, ~50-300ms) flash the chip once
    // and settle so quickly the operator can't perceive the
    // animation. Clamping the off-flip to 500ms after the on-flip
    // gives the pulse one full cycle of the
    // `provider-loading-pulse` keyframes (1.4s ease-in-out) so
    // the flash registers visually. Slow probes (>500ms) don't
    // see any added latency — the off-flip fires when the SSE
    // `done` event lands, identical to before.
    const _PROV_POLL_MIN_VISIBLE_MS = 500;
    const _setProvPolling = (host_id, provider, polling) => {
      if (!host_id || !provider) {
        return;
      }
      const row = (this.hosts || []).find(r => r && r.id === host_id);
      if (!row) {
        return;
      }
      if (!row._polling || typeof row._polling !== 'object') {
        row._polling = {};
      }
      if (!row._pollingStart || typeof row._pollingStart !== 'object') {
        row._pollingStart = {};
      }
      if (polling) {
        row._polling[provider] = true;
        row._pollingStart[provider] = Date.now();
        return;
      }
      // Off-flip — clamp to minimum visible duration.
      const startedAt = row._pollingStart[provider] || 0;
      const elapsed = Date.now() - startedAt;
      const remaining = _PROV_POLL_MIN_VISIBLE_MS - elapsed;
      if (remaining > 0) {
        // Defer the clear so the chip flashes through one full
        // pulse cycle. setTimeout with the SAME row reference
        // mutates in place; if the row is removed in the meantime
        // the timer no-ops harmlessly.
        setTimeout(() => {
          try {
            delete row._polling[provider];
          } catch (_) {
          }
        }, remaining);
      } else {
        delete row._polling[provider];
      }
    };
    es.addEventListener('host:provider_probing', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      try {
        const data = JSON.parse(e.data || '{}');
        const p = data.payload || {};
        _setProvPolling(p.host_id, p.provider, true);
      } catch (_) {
      }
    });
    es.addEventListener('host:provider_done', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      try {
        const data = JSON.parse(e.data || '{}');
        const p = data.payload || {};
        _setProvPolling(p.host_id, p.provider, false);
      } catch (_) {
      }
    });
    // host_metrics_sampler publishes this on every NE
    // sample INSERT. Refresh the drawer chart only when (a) it's
    // currently open AND (b) the open host matches the event's
    // host_id. Per-host filter is critical: 50 sampled hosts firing
    // 50 events each tick would otherwise spam the SPA. Beszel
    // hosts don't go through host_metrics_sampler so they keep
    // the polling baseline — the drawer timer gracefully handles
    // both cases.
    es.addEventListener('host:history_appended', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      if (!this.drawerHost) {
        return;
      }
      try {
        const data = JSON.parse(e.data || '{}');
        const id = (data.payload && data.payload.host_id) || '';
        if (!id || id !== this.drawerHost.id) {
          return;
        }
        // Only NE-source drawers get the push refresh; Beszel
        // drawers' history isn't sample-keyed off our DB so this
        // event is irrelevant to them.
        if (!this.drawerHost.ne_url) {
          return;
        }
        // Debounce 200 ms — backend may fire bursts of
        // history_appended events for the open host within a single
        // sampler tick (e.g. SNMP + NE both writing). Collapse them
        // into one loadHostHistory call so we don't make N redundant
        // /api/hosts/history fetches in <1s.
        if (this._historyAppendedDebounceTimer) {
          clearTimeout(this._historyAppendedDebounceTimer);
        }
        this._historyAppendedDebounceTimer = setTimeout(() => {
          this._historyAppendedDebounceTimer = null;
          if (!this.drawerHost || this.drawerHost.id !== id) {
            return;
          }
          console.log('[live] event=host:history_appended id=' + id + ' → loadHostHistory (debounced)');
          this._pollWrap(this.loadHostHistory(
            this.drawerHost.beszel_id || '',
            this.drawerHost.id,
          )).catch(() => {
          });
        }, 200);
      } catch (_) {
      }
    });
    // ping_sampler publishes this on every INSERT. Drives the
    // RTT chip + (V2) the drawer Ping chart. Per-host filter same as
    // history_appended. Routes through the SHARED _hostObserverPending
    // queue so a fleet-wide ping tick (N hosts firing in the same
    // second) coalesces through the 200ms debounce + shares the
    // _hostRefreshQueue cap with poll + IO observer + other SSE
    // events. Direct refreshHostRow would bypass that cap.
    es.addEventListener('host:ping_sampled', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      try {
        const data = JSON.parse(e.data || '{}');
        const id = (data.payload && data.payload.host_id) || '';
        if (!id) {
          return;
        }
        this._hostObserverPending = this._hostObserverPending || new Set();
        this._hostObserverPending.add(id);
        if (typeof this._scheduleHostObserverFlush === 'function') {
          this._scheduleHostObserverFlush();
        } else if (typeof this._runHostRefreshQueue === 'function') {
          this._runHostRefreshQueue([id]).catch(() => {
          });
        }
        // push the new sample into the open drawer's ping
        // chart so Live mode is genuinely push-driven (no fallback
        // timer needed when SSE is healthy). Same shape as the
        // host:history_appended handler.
        if (this.drawerHost && this.drawerHost.id === id && this.drawerHost.ping_enabled
          && typeof this.loadHostPingHistory === 'function') {
          this._pollWrap(this.loadHostPingHistory(id)).catch(() => {
          });
        }
      } catch (_) {
      }
    });
    es.addEventListener('schedule:fired', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      // Schedule rows + queue rebuild via the same helpers the
      // Schedules tab uses. They reconcile in place's
      // _reconcileById path so re-firing them mid-tab doesn't
      // tear DOM down.
      try {
        if (this.loadSchedules) {
          this.loadSchedules();
        }
      } catch (_) {
      }
      try {
        if (this.loadScheduleQueue) {
          this.loadScheduleQueue();
        }
      } catch (_) {
      }
    });
    es.addEventListener('history:appended', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      // Reload via the same paginated endpoint — the in-place
      // reconcile keeps each row's <details> open/closed
      // state intact.
      try {
        if (this.loadHistory) {
          this.loadHistory();
        }
      } catch (_) {
      }
    });
    // In-app notification dispatched by the `app` medium in
    // logic/ops.py:notify(). Self-filter is intentionally OFF so
    // EVERY tab gets the badge bump — operators want the badge to
    // tick even on the tab that triggered the op (the dispatcher
    // doesn't carry an X-OmniGrid-Client-Id, samplers + scheduler
    // are the most common publishers).
    es.addEventListener('notification:created', (e) => {
      onAny();
      try {
        const data = JSON.parse(e.data || '{}');
        const p = data.payload || {};
        console.log('[live] event=notification:created id=' + (p.id || ''));
        if (typeof this._handleNotificationCreated === 'function') {
          this._handleNotificationCreated(p);
        }
        // Proactive incident chip — fire on warning / error / critical
        // severity notifications when the AI sidebar is open. Info
        // notifications (e.g. successful schedule fires) don't
        // warrant an "investigate?" chip — too much noise.
        const sev = String(p.severity || '').toLowerCase();
        if (this.aiSidebarOpen && (sev === 'warning' || sev === 'error' || sev === 'critical')) {
          const hostId = (p.target_kind === 'host' && p.target_id) ? String(p.target_id) : '';
          const title = p.title || p.event || 'Notification';
          const titleLine = hostId
            ? (title + ' (' + hostId + ') — investigate?')
            : (title + ' — investigate?');
          const query = hostId
            ? ('A "' + title + '" notification just fired for host ' + hostId + '. What happened, what should I check, and what action would help?')
            : ('A "' + title + '" notification just fired. What happened and what should I check?');
          this._setAiIncidentChip({
            kind: 'notification',
            host_id: hostId,
            title: titleLine,
            query: query,
            severity: sev,
          });
        }
      } catch (_) {
      }
    });
    // Mark-read / mark-all-read echoes. Self-filter via the standard
    // _isSelfEvent path so the tab that issued the click doesn't
    // re-paint over its own optimistic update.
    es.addEventListener('notification:read', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      try {
        const data = JSON.parse(e.data || '{}');
        const p = data.payload || {};
        if (typeof this._handleNotificationRead === 'function') {
          this._handleNotificationRead(p);
        }
      } catch (_) {
      }
    });
    es.addEventListener('notification:deleted', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      try {
        const data = JSON.parse(e.data || '{}');
        const p = data.payload || {};
        if (typeof this._handleNotificationDeleted === 'function') {
          this._handleNotificationDeleted(p);
        }
      } catch (_) {
      }
    });
    // Settings changed — published by api_set_settings with the
    // originating tab's client_id. Self-filter via _isSelfEvent
    // skips this for the tab that did the save (it already has the
    // latest values from its own POST response). Other tabs reload
    // /api/settings so a setting flipped in one tab takes effect
    // everywhere within one SSE round-trip.
    es.addEventListener('settings:updated', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      console.log('[live] event=settings:updated → loadSettings (cross-tab)', e.data ? e.data.slice(0, 200) : '');
      try {
        if (this.loadSettings) {
          this.loadSettings();
        }
      } catch (_) {
      }
      // Also pull /api/me so any client_config-delivered tunable
      // (poll cadences, fan-out caps) reflects the new value
      // without requiring a page reload. Field-by-field merge
      // (matching saveSettings's own /api/me refresh path) so the
      // cross-tab case doesn't tear down DOM bindings the way a
      // wholesale ``this.me = d`` would (Alpine Proxy identity
      // contract — see CLAUDE.md "Frontend reconciles ... in place"
      // rule). `me.notify_mediums` lives on this dict so the
      // per-medium grid reflects an admin's toggle flip on every
      // open tab within one SSE round-trip.
      try {
        fetch('/api/me', {cache: 'no-store'})
          .then(r => r.ok ? r.json() : null)
          .then(d => {
            if (d && d.authenticated && this.me) {
              for (const k of Object.keys(d)) {
                this.me[k] = d[k];
              }
            }
          })
          .catch(() => {
          });
      } catch (_) {
      }
    });
    // session-cookie sliding window. Backend's
    // `slide_session_if_needed` publishes this when it bumps the
    // cookie's expiry past the renewal threshold. SPA refreshes
    // `me.session_expires_at` (if exposed by /api/me) so any UI hint
    // ("session expires in X minutes") stays current. The event was
    // documented in api.md and CLAUDE.md but had no consumer pre-fix
    // — caught by the SSE-publisher-vs-consumer audit recipe.
    // Multi-tab activity. `tab:activity` updates
    // the local map in place so the topbar widget re-renders without
    // a wholesale-replace (the array-mutation rule applies — Alpine
    // tears down rebound DOM on full-replace). `tab:closed` deletes
    // the entry. Both are self-filtered by `_isSelfEvent` so this tab
    // doesn't echo its own heartbeat back into its own list.
    es.addEventListener('tab:activity', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      try {
        const data = JSON.parse(e.data || '{}');
        const p = data.payload || {};
        const cid = p.client_id;
        if (!cid) {
          return;
        }
        // Privacy: only track THIS user's own other tabs — never another
        // user's. The events bus is a global fan-out (no per-user routing),
        // so a cross-user `tab:activity` reaches every client; drop it here
        // before it enters the local map. Mirrors the backend GET's
        // actor-scope. (If we can't identify the current user yet, drop —
        // privacy-first, matching the backend.)
        const myUser = this.me && this.me.username;
        if (!myUser || (p.actor && p.actor !== myUser)) {
          return;
        }
        // Field-by-field assign so Alpine sees a granular update
        // instead of a wholesale-replace (preserves popover focus
        // state when one sibling navigates while popover is open).
        const cur = this.tabActivity[cid] || {};
        this.tabActivity[cid] = Object.assign({}, cur, p);
      } catch (_) {
      }
    });
    es.addEventListener('tab:closed', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      try {
        const data = JSON.parse(e.data || '{}');
        const cid = (data.payload || {}).client_id;
        if (!cid) {
          return;
        }
        // Defensive — fall back to deleting via reactive proxy. Alpine
        // catches delete on a reactive object.
        if (this.tabActivity[cid]) {
          delete this.tabActivity[cid];
        }
      } catch (_) {
      }
    });
    // telegram:linked / telegram:unlinked — emitted by the listener
    // when /link or /unlink succeeds. Re-fetch /api/me so the
    // Profile → Telegram card flips between "Generate code" and
    // the linked-state banner without a manual page reload. Payload
    // carries `username` so we only refresh when the event targets
    // the operator viewing this tab.
    es.addEventListener('telegram:linked', (e) => {
      onAny();
      try {
        const data = JSON.parse(e.data || '{}');
        const payloadUser = (data.payload && data.payload.username) || '';
        const myUser = (this.me && this.me.username) || '';
        if (!payloadUser || payloadUser !== myUser) {
          return;
        }
        console.log('[live] event=telegram:linked username=' + payloadUser);
        if (typeof fetch === 'function') {
          fetch('/api/me', {cache: 'no-store'})
            .then(r => r.ok ? r.json() : null)
            .then(d => {
              if (d && d.authenticated) {
                this.me = d;
              }
            })
            .catch(() => {
            });
        }
      } catch (_) {
      }
    });
    es.addEventListener('telegram:unlinked', (e) => {
      onAny();
      try {
        const data = JSON.parse(e.data || '{}');
        const payloadUser = (data.payload && data.payload.username) || '';
        const myUser = (this.me && this.me.username) || '';
        if (!payloadUser || payloadUser !== myUser) {
          return;
        }
        console.log('[live] event=telegram:unlinked username=' + payloadUser);
        if (typeof fetch === 'function') {
          fetch('/api/me', {cache: 'no-store'})
            .then(r => r.ok ? r.json() : null)
            .then(d => {
              if (d && d.authenticated) {
                this.me = d;
              }
            })
            .catch(() => {
            });
        }
      } catch (_) {
      }
    });
    es.addEventListener('session:renewed', (e) => {
      onAny();
      if (this._isSelfEvent(e)) {
        return;
      }
      try {
        const data = JSON.parse(e.data || '{}');
        const exp = (data.payload && data.payload.expires_at) || null;
        console.log('[live] event=session:renewed expires_at=' + exp);
        // Belt-and-braces refresh of /api/me so any session-related
        // hint (expiry pill / countdown) re-hydrates without a full
        // page reload. Cheap call (cached server-side); skipped if
        // /api/me hasn't loaded yet (rare race).
        if (this.me && typeof fetch === 'function') {
          fetch('/api/me', {cache: 'no-store'})
            .then(r => r.ok ? r.json() : null)
            .then(d => {
              if (d && d.authenticated) {
                this.me = d;
              }
            })
            .catch(() => {
            });
        }
      } catch (_) {
      }
    });
    es.onerror = () => {
      if (this._sseConnected) {
        console.warn('[live] SSE error: connection dropped — falling back to polling until next open');
      }
      // EventSource auto-reconnects with its own backoff. We just
      // surface the visible "polling" badge until ``open`` fires
      // again. Belt-and-braces — the freshness watcher below also
      // flips the flag if no traffic arrives within the threshold.
      this._sseConnected = false;
    };
    // Freshness watchdog: flip _sseConnected to false if no event
    // (organic OR heartbeat) arrives within the idle threshold. EOL
    // catches the case where the TCP connection silently dies and
    // EventSource doesn't fire `error` immediately.
    if (this._sseFreshnessTimer) {
      clearInterval(this._sseFreshnessTimer);
    }
    this._sseFreshnessTimer = setInterval(() => {
      if (!this._sseLastEventTs) {
        return;
      }
      const idle = Date.now() - this._sseLastEventTs;
      // operator-tunable via `tuning_sse_idle_threshold_seconds`,
      // delivered as `client_config.sse_idle_threshold_ms`. Defensive
      // fallback to the historical 30000 covers the brief window
      // before /api/me hydrates AND any consumer of the legacy
      // `_sseIdleThresholdMs` property.
      const threshold = (this.me && this.me.client_config
          && this.me.client_config.sse_idle_threshold_ms)
        || this._sseIdleThresholdMs || 30000;
      if (idle > threshold) {
        if (this._sseConnected) {
          console.warn('[live] SSE freshness watchdog: ' + Math.round(idle / 1000) + 's since last event — flipping _sseConnected=false (polling fallback resumes)');
        }
        this._sseConnected = false;
      }
    }, 1000);
  },

  _handleOpEvent(e, phase) {
    try {
      if (this._isSelfEvent(e)) {
        return;
      }
      const data = JSON.parse(e.data || '{}');
      const p = data.payload || {};
      // Defer to pollOps's existing logic — it handles the linger
      // window, toast notifications, and the post-op refresh kicks.
      // Calling it as a one-off here gives the same effect as a
      // 1.5s tick landing on the operator's screen ~immediately.
      this.pollOpsNow();
      // Special-case: ``op:completed`` for an op we DID see running
      // also triggers the items refresh. pollOpsNow's own justDone
      // path picks this up so we don't double-fire here.
      void p;
      void phase;
    } catch (_) {
    }
  },

  // SSE self-filter check. Returns true when the incoming
  // event's payload.client_id matches the local tab's id (set by
  // auth-fetch.js into window.__ogClientId on first fetch). Used at
  // the top of every data-bearing handler so an SSE event published
  // off a request originating from THIS tab doesn't loop back as a
  // redundant refresh / flicker. Sampler / background-task publishers
  // don't pass client_id, so events from those paths never match
  // and the filter is a transparent no-op there.
  _isSelfEvent(e) {
    if (!e || !e.data) {
      return false;
    }
    const myId = window.__ogClientId;
    if (!myId) {
      return false;
    }
    try {
      const data = JSON.parse(e.data);
      const cid = data && data.payload && data.payload.client_id;
      if (cid && cid === myId) {
        return true;
      }
    } catch (_) {
    }
    return false;
  },

  // Trailing-debounced forced items refresh for `cache:invalidated`. A
  // cross-tab Cleanup / bulk-remove emits one event per container, so we
  // coalesce the burst into a single refresh(true) that runs ~400ms after
  // the LAST event — guaranteeing the post-cleanup state (removed items
  // gone → "Cleanup (N)" button hidden) lands in every tab without the
  // operator refreshing each one. The timer handle is process-local to the
  // component; a fresh event resets it so only the trailing call survives.
  //
  // Browser background-tab throttling caveat: Chrome / Edge / Firefox
  // throttle setTimeout in hidden tabs to ~1 firing per minute (sometimes
  // longer under battery-saver / intensive throttling). A 400ms timer set
  // while the tab is in the background may not fire until the tab is
  // re-foregrounded, leaving the operator with a stale Cleanup button +
  // stale items list when they switch to the other tab. The
  // `visibilitychange` companion listener below pre-empts the throttled
  // timer: when the tab becomes visible AND a refresh is pending, fire
  // it immediately so the catch-up is instant on tab-focus.
  _scheduleCacheInvalidatedRefresh() {
    if (this._cacheInvalidatedRefreshTimer) {
      clearTimeout(this._cacheInvalidatedRefreshTimer);
    }
    this._cacheInvalidatedRefreshTimer = setTimeout(() => {
      this._cacheInvalidatedRefreshTimer = null;
      try {
        this.refresh(true);
      } catch (_) {
      }
    }, 400);
    // Idempotent: attach the visibility catch-up listener exactly once
    // per page load. A duplicate listener would cause N concurrent
    // refreshes on every tab-focus after a tab-blur, which is the
    // exact regression this fix is supposed to remove.
    if (!this._cacheInvalidatedVisibilityHookAttached) {
      this._cacheInvalidatedVisibilityHookAttached = true;
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') {
          return;
        }
        if (!this._cacheInvalidatedRefreshTimer) {
          return;
        }
        // A refresh is queued but throttled — fire it now.
        clearTimeout(this._cacheInvalidatedRefreshTimer);
        this._cacheInvalidatedRefreshTimer = null;
        try {
          this.refresh(true);
        } catch (_) {
        }
      });
    }
  },

  // Backend-unreachable banner watcher. Polls every 5s (cheap — one
  // subtraction + a comparison) and flips `backendUnreachable` based on how
  // long ago the SPA last saw a successful backend signal. The signal sources
  // are the SSE `onAny()` stamp (every event including keepalive) AND the
  // auth-fetch.js wrapper's 2xx stamp on /api/* responses, both writing to
  // `window.__ogLastBackendOkTs`. Threshold is the
  // tuning_backend_unreachable_threshold_seconds TUNABLE delivered via
  // /api/me.client_config.backend_unreachable_threshold_seconds; 0 disables
  // the banner entirely. Init() seeds the timestamp + starts the ticker so a
  // dead-on-arrival page doesn't false-positive before the first signal.
  _startBackendReachabilityWatcher() {
    if (this._backendReachabilityTimer) {
      return;
    }
    if (!window.__ogLastBackendOkTs) {
      window.__ogLastBackendOkTs = Date.now();
    }
    const evaluate = () => {
      const cfg = (this.me && this.me.client_config) || {};
      const thresholdS = +cfg.backend_unreachable_threshold_seconds;
      // 0 / NaN / negative -> banner disabled. Honors the operator-facing
      // "set 0 to silence" contract documented on the TUNABLE.
      if (!thresholdS || thresholdS < 0) {
        if (this.backendUnreachable) {
          this.backendUnreachable = false;
        }
        return;
      }
      const lastOk = window.__ogLastBackendOkTs || Date.now();
      const stale = (Date.now() - lastOk) > thresholdS * 1000;
      if (stale !== this.backendUnreachable) {
        this.backendUnreachable = stale;
      }
    };
    evaluate();
    this._backendReachabilityTimer = setInterval(evaluate, 5000);
  },

  // Helper for the toolbar indicator — exposes a concise status string
  // for the i18n-bound title attribute.
  sseStatusKey() {
    if (this._sseConnected) {
      return 'events.connected_title';
    }
    // Distinguish "never connected" from "dropped + retrying".
    if (this._sse && this._sseLastEventTs) {
      return 'events.reconnecting_title';
    }
    return 'events.disconnected_title';
  },
};
