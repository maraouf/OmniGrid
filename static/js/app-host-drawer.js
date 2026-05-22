/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// noinspection ElementNotExported,JSUnusedGlobalSymbols,CheckTagEmptyBody,HtmlUnknownTag,HtmlExtraClosingTag
// SPA Host detail drawer — slide-out panel triggered by clicking a
// Hosts-view row.
//
// Surface:
//   - State: `drawerHost`, `hostHistory`, `hostHistoryRange`,
//     `hostHistoryNow`, `hostHistoryRangeBusy`
//   - Lifecycle: `openHostDrawer`, `openHostDrawerById`,
//     `closeHostDrawer`
//   - Range picker: `setHostHistoryRange`
//   - History fetches per metric kind:
//     `loadHostHistory`, `loadHostHttpProbeHistory`,
//     `loadHostPingHistory`, `loadHostSnmpHistory`,
//     `loadHostSnmpIfaceHistory`, `loadHostSnmpTempHistory`
//   - Helpers: `_kickPerHostChartFetches`, `hostHistoryKey`,
//     `hostPingHistoryKey`, `hostHistoryFreshness*`
//
// Phase 2, Batch 22 of the static/js/app.js modularisation.
// noinspection AnonymousFunctionJS,ConditionalExpressionJS

export default {
  // Slide-out drawer mode — clicking a host row opens this
  // drawer instead of expanding the row inline. `drawerHost` is the
  // live host object reference (kept up-to-date by the existing
  // `loadHosts` reconcile loop, since rows mutate fields in place
  // rather than reassigning the array). Null = drawer closed.
  drawerHost: null,
  // Per-system time-series cache keyed by Beszel record id.
  // Shape: { [system_id]: { loading, error, series: [{t,cpu,mp,dp,b,...}] } }
  hostHistory: {},
  // Operator's selected drawer-chart range (1 / 6 / 24 / 168 hours).
  // Persisted to `localStorage.hostHistoryRange` so a page refresh
  // doesn't snap them back to the 1h default — operators leaving the
  // drawer on 7d while glancing away expected the picker to remember.
  // Validated against the four canonical values; anything else falls
  // back to 1.
  hostHistoryRange: (() => {
    try {
      const v = +localStorage.getItem('hostHistoryRange');
      return [1, 6, 24, 168].includes(v) ? v : 1;
    } catch (_) {
      return 1;
    }
  })(),
  // Wall-clock millis ticked every 30s by init() — drives the
  // "Updated Xm ago" freshness hint in the host-drawer chart-grid
  // header. Reactive so Alpine re-evaluates the helper.
  hostHistoryNow: 0,
  // Fire every per-host chart fetch the drawer needs for a single
  // host, with empty-cache + stale-cache guards. Shared between
  // `openHostDrawer` (initial drawer mount) and the arrow-key host
  // nav handler (in-place drawerHost swap that deliberately skips
  // openHostDrawer to avoid replaying the slide-in animation).
  // Pre-fix the arrow-nav handler only kicked `loadHostHistory`,
  // leaving ping / SNMP host / SNMP iface / SNMP temp caches stale-
  // or-empty for the new host so charts sat on "Collecting data"
  // until the next 30s drawer-poll tick or 60s SSE sample event.
  // Both call sites now go through this single helper so adding a
  // new per-host load helper (e.g. for a future provider) means
  // editing ONE place instead of two divergent blocks.
  //
  // Stale-cache contract: `_cacheStale` returns true when
  // loadedAt > 30s old OR cache is undefined. Empty-cache bypass
  // (`series.length < 2`) ALSO triggers refetch — covers the case
  // where a previous-session HTTP error stamped loadedAt without
  // populating the cache, leaving an "operationally empty but
  // timestamp-fresh" entry the stale check skipped.
  _kickPerHostChartFetches(host) {
    if (!host) {
      return;
    }
    try {
      const _stale = (entry) => !entry || !entry.loadedAt
        || (Date.now() - entry.loadedAt) > 30000;
      const _seriesLen = (entry, field) => {
        if (!entry) {
          return 0;
        }
        const arr = entry[field];
        return Array.isArray(arr) ? arr.length : 0;
      };
      // Beszel / NE / Pulse / Webmin main history.
      const drawerKey = this.hostHistoryKey(host);
      if (drawerKey
        && (host.beszel_id || host.ne_url
          || host.pulse_name || host.webmin_name)) {
        const cached = this.hostHistory[drawerKey];
        if (_seriesLen(cached, 'series') < 2 || _stale(cached)) {
          this.loadHostHistory(host.beszel_id || '', host.id);
        }
      }
      // Ping latency.
      const pingKey = this.hostPingHistoryKey(host);
      if (pingKey && host.ping_enabled) {
        const pcache = this.hostHistory[pingKey];
        if (_seriesLen(pcache, 'series') < 2 || _stale(pcache)) {
          this.loadHostPingHistory(host.id);
        }
      }
      // SNMP per-host (CPU / Mem / Disk / load) + per-iface
      // throughput + per-temperature-probe history. All three
      // gated on the same `_snmpHasProbeTarget` predicate.
      if (this._snmpHasProbeTarget(host)) {
        const sh = this.hostSnmpHistory[host.id];
        if (_seriesLen(sh, 'points') < 2 || _stale(sh)) {
          if (typeof this.loadHostSnmpHistory === 'function') {
            this.loadHostSnmpHistory(host.id, this.hostHistoryRange || 1);
          }
        }
        const ih = this.hostSnmpIfaceHistory[host.id];
        const ifaces = (ih && ih.ifaces && typeof ih.ifaces === 'object') ? ih.ifaces : {};
        let ihMax = 0;
        for (const k of Object.keys(ifaces)) {
          const a = Array.isArray(ifaces[k]) ? ifaces[k] : [];
          if (a.length > ihMax) {
            ihMax = a.length;
          }
        }
        if (ihMax < 2 || _stale(ih)) {
          if (typeof this.loadHostSnmpIfaceHistory === 'function') {
            this.loadHostSnmpIfaceHistory(host.id, this.hostHistoryRange || 1);
          }
        }
        const th = this.hostSnmpTempHistory[host.id];
        const probes = (th && th.probes && typeof th.probes === 'object') ? th.probes : {};
        let thMax = 0;
        for (const k of Object.keys(probes)) {
          const a = Array.isArray(probes[k]) ? probes[k] : [];
          if (a.length > thMax) {
            thMax = a.length;
          }
        }
        if (thMax < 2 || _stale(th)) {
          if (typeof this.loadHostSnmpTempHistory === 'function') {
            this.loadHostSnmpTempHistory(host.id, this.hostHistoryRange || 1);
          }
        }
      }
      // HTTP probe latency — gated on http_probe_enabled OR a pre-
      // existing cache entry (so a recently-disabled probe still
      // surfaces the last collected window in the chart).
      if (host.http_probe_enabled || (this.hostHttpProbeHistory && this.hostHttpProbeHistory[host.id])) {
        const hh = this.hostHttpProbeHistory && this.hostHttpProbeHistory[host.id];
        if (_seriesLen(hh, 'series') < 2 || _stale(hh)) {
          if (typeof this.loadHostHttpProbeHistory === 'function') {
            this.loadHostHttpProbeHistory(host.id, this.hostHistoryRange || 1);
          }
        }
      }
    } catch (_) { /* best-effort; charts catch up via drawer-poll timer */
    }
  },
  // Open the host drawer by id — looks up the host object from
  // `this.hosts` and navigates to the Hosts view first if the
  // operator clicked from somewhere else (e.g. Stats → Samples
  // drill-down popup). Toast when the id isn't in the curated
  // list so orphaned sample rows don't navigate to nowhere.
  openHostDrawerById(hostId) {
    const id = (hostId || '').toString().trim();
    if (!id) {
      return;
    }
    const list = Array.isArray(this.hosts) ? this.hosts : [];
    const host = list.find(h => h && (h.id === id || h.host === id));
    if (!host) {
      this.showToast(
        this.t('hosts_extra.drawer.not_found', {id})
        || ('Host not found: ' + id),
        'error',
      );
      return;
    }
    if (this.view !== 'hosts') {
      this.view = 'hosts';
    }
    this.openHostDrawer(host);
  },
  openHostDrawer(host) {
    if (!host) {
      return;
    }
    this.drawerHost = host;
    // Defensive clear of any `providerResumeBusy` flags for this
    // host — covers the edge case where a previous click on the
    // Resume button left the flag stuck true (e.g. fetch hung,
    // browser killed the request mid-await, page navigation
    // interrupted the await). Without this, the per-chip Resume
    // button stays disabled forever even though no resume is
    // actually in flight. Also runs the same prefix-clear in
    // _runHostDrawerOpen for tabs reopening drawers after a long
    // idle.
    try {
      const prefix = host.id + ':';
      for (const k of Object.keys(this.providerResumeBusy || {})) {
        if (k.startsWith(prefix)) {
          this.providerResumeBusy[k] = false;
        }
      }
    } catch (_) { /* ignore */
    }
    // — defensive clear of the whole-host Resume sampling busy
    // flag. Mirror of the providerResumeBusy clear above. Without
    // this, a previous click whose await never resolved (network
    // freeze, page hidden during fetch, browser killed the request)
    // left `h._resumeBusy` stuck `true` so the Resume sampling
    // button on the whole-host pause banner rendered disabled
    // forever. The 30s safety timer in resumeHostSampling closes
    // the same window from the other end.
    if (host._resumeBusy) {
      host._resumeBusy = false;
    }
    // Per-host chart fetches — main hostHistory + ping + SNMP host
    // / iface / temp. Single shared helper used by both the initial
    // drawer mount (here) AND the arrow-key host nav handler at
    // `handleHotkey` so adding a new per-host load helper means
    // editing ONE place instead of two divergent blocks. Each fetch
    // inside the helper is guarded by stale-cache + empty-cache
    // checks so re-opening the same host within 30s reuses the
    // cached series; an "operationally empty but timestamp-fresh"
    // entry (previous-session HTTP error stamped loadedAt without
    // populating data) still triggers a fresh fetch.
    this._kickPerHostChartFetches(host);
    // Dedicated drawer-history poll — keeps the chart series +
    // the `Updated Xs ago` freshness label in sync regardless of
    // whether the operator has the host-list poll enabled (when
    // `statsInterval=0` the loadHosts setInterval never fires, so a
    // hook inside loadHosts doesn't reach the drawer). 30s is a
    // sensible default for a drawer the operator is actively
    // watching; clears on closeHostDrawer. Cadence rules under
    // +:
    // - Off → no timer (static-snapshot promise).
    // - Live + NE-only host → no timer; the new
    //   `host:history_appended` push event refreshes the chart on
    // every sampler write.
    // - Live + Beszel host (or NE+Beszel hybrid) → 30s timer
    //   because Beszel data isn't push-driven from our side
    //   (PocketBase owns the writes; we'd need a PB → bus
    //   bridge to push, out of scope).
    // - Interval mode → poll at the picker cadence regardless of
    //   source (operator explicitly opted out of push).
    if (this._drawerHistoryTimer) {
      clearInterval(this._drawerHistoryTimer);
      this._drawerHistoryTimer = null;
    }
    const hasHistory = host.beszel_id || host.ne_url;
    const live = this.refreshInterval === -1;
    const off = this.refreshInterval === 0;
    // NE-only hosts in Live mode rely entirely on push.
    const pushOnly = live && host.ne_url && !host.beszel_id;
    if (hasHistory && !off && !pushOnly) {
      const ms = (live ? 30 : this.refreshInterval) * 1000;
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
    // SNMP iface / temp history on the same drawer-poll cadence —
    // self-heal for the case where the initial drawer-open fetch
    // landed empty (sampler hadn't ticked yet, transient backend
    // 5xx, etc.). Pre-fix the iface cache only refreshed on
    // drawer-open + range-picker-click; if neither happened, charts
    // stayed stuck at "Collecting data" indefinitely. Same gate as
    // the drawer-open fetches (`_snmpHasProbeTarget`); only fires
    // when the operator hasn't opted out via `refreshInterval=0`.
    if (this._drawerSnmpHistoryTimer) {
      clearInterval(this._drawerSnmpHistoryTimer);
      this._drawerSnmpHistoryTimer = null;
    }
    if (!off && this._snmpHasProbeTarget(host)) {
      const snmpMs = (live ? 30 : this.refreshInterval) * 1000;
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
    // ping history timer. Live mode is push-driven by the
    // host:ping_sampled SSE handler so no fallback timer needed
    // when SSE is healthy; for Off / interval modes the same
    // pickup-cadence rules apply as for the main history timer.
    if (this._drawerPingTimer) {
      clearInterval(this._drawerPingTimer);
      this._drawerPingTimer = null;
    }
    if (host.ping_enabled && !off && !live) {
      const pingMs = this.refreshInterval * 1000;
      this._drawerPingTimer = setInterval(() => {
        if (!this.drawerHost || !this.drawerHost.ping_enabled) {
          return;
        }
        this._pollWrap(this.loadHostPingHistory(this.drawerHost.id));
      }, pingMs);
    }
    // Preload SSH status — admin only, and only when the host
    // explicitly opted IN to SSH. Without this the
    // SSH card header shows "Not configured" until the operator
    // clicks to expand it — a false-negative for opted-in fleets.
    if (this.isAdmin && this.isAdmin() && host.ssh_enabled) {
      this.loadSshStatus(host.id);
    }
  },
  closeHostDrawer() {
    this.drawerHost = null;
    this.healthPopoverOpen = false;
    if (this._drawerHistoryTimer) {
      clearInterval(this._drawerHistoryTimer);
      this._drawerHistoryTimer = null;
    }
    if (this._drawerPingTimer) {
      clearInterval(this._drawerPingTimer);
      this._drawerPingTimer = null;
    }
    if (this._drawerSnmpHistoryTimer) {
      clearInterval(this._drawerSnmpHistoryTimer);
      this._drawerSnmpHistoryTimer = null;
    }
  },
  async loadHostHttpProbeHistory(hostId, hours) {
    if (!hostId) {
      return;
    }
    const hrs = Math.max(1, Math.min(168, Number(hours) || Number(this.hostHistoryRange) || 1));
    try {
      const resp = await fetch('/api/hosts/' + encodeURIComponent(hostId) + '/http-probe/history?hours=' + hrs);
      if (!resp.ok) {
        this.hostHttpProbeHistory[hostId] = {
          series: [],
          collectors: {},
          loadedAt: Date.now(),
          hours: hrs,
          error: 'HTTP ' + resp.status,
        };
        return;
      }
      const j = await resp.json();
      const rawSeries = Array.isArray(j && j.series) ? j.series : [];
      this.hostHttpProbeHistory[hostId] = {
        series: rawSeries,
        collectors: (j && j.collectors) || {},
        loadedAt: Date.now(),
        hours: hrs,
        error: (j && j.error) || null,
      };
      // Aggregate per-URL points by timestamp → one point per ts with
      // avg(latency_ms) across URLs — feeds the drawer's full-width
      // Latency metric card via the standard `hostHistory[key]` shape
      // so `hostChart(key, 'latency_ms', ...)` + `xAxisFromSeries(key)`
      // + `yAxisAuto(max, fmt)` all work unchanged. Failing probes
      // (null latency) are skipped from the average — empty buckets
      // surface as the polyline's natural gaps rather than synthesised
      // zeros (skip-don't-synthesize discipline).
      const buckets = new Map();
      for (const pt of rawSeries) {
        if (!pt || !pt.t) continue;
        const lat = pt.latency_ms;
        if (lat == null || !Number.isFinite(+lat)) continue;
        let entry = buckets.get(pt.t);
        if (!entry) {
          entry = {sum: 0, n: 0};
          buckets.set(pt.t, entry);
        }
        entry.sum += +lat;
        entry.n += 1;
      }
      const aggSeries = [];
      for (const [t, {sum, n}] of buckets) {
        if (n > 0) aggSeries.push({t, latency_ms: sum / n});
      }
      aggSeries.sort((a, b) => a.t - b.t);
      if (!this.hostHistory) this.hostHistory = {};
      const lkey = this.httpLatencyKey({id: hostId});
      this.hostHistory[lkey] = {
        series: aggSeries,
        loadedAt: Date.now(),
      };
    } catch (e) {
      this.hostHttpProbeHistory[hostId] = {
        series: [],
        collectors: {},
        loadedAt: Date.now(),
        hours: hrs,
        error: String(e && e.message || e),
      };
    }
  },
  async loadHostSnmpHistory(hostId, hours) {
    if (!hostId) {
      return;
    }
    const h = +hours || 1;
    const prev = this.hostSnmpHistory[hostId] || {};
    this.hostSnmpHistory[hostId] = {
      loading: true,
      error: prev.error || '',
      points: Array.isArray(prev.points) ? prev.points : [],
      loadedAt: prev.loadedAt || 0,
    };
    try {
      const r = await fetch(
        `/api/hosts/${encodeURIComponent(hostId)}/snmp/history?hours=${h}`
      );
      if (!r.ok) {
        this.hostSnmpHistory[hostId] = {
          loading: false, error: `HTTP ${r.status}`,
          points: prev.points || [], loadedAt: prev.loadedAt || 0,
        };
        return;
      }
      const d = await r.json();
      this.hostSnmpHistory[hostId] = {
        loading: false,
        error: d.error || '',
        points: Array.isArray(d.points) ? d.points : [],
        // Server-side bucket cadence for the returned series. Lets
        // rate-computation helpers (`snmpThroughputBpsSeries`) scale
        // their gap-detection threshold to the actual cadence rather
        // than the hardcoded 3600s cap that rejected every delta on
        // 7d windows (5040s buckets) → empty throughput chart.
        // 0 when the response wasn't bucketed (≤2h windows).
        bucket_seconds: Number(d.bucket_seconds) || 0,
        loadedAt: Date.now(),
      };
    } catch (e) {
      this.hostSnmpHistory[hostId] = {
        loading: false, error: String(e),
        points: prev.points || [], loadedAt: prev.loadedAt || 0,
      };
    }
  },
  async loadHostSnmpTempHistory(hostId, hours) {
    if (!hostId) {
      return;
    }
    const h = +hours || 1;
    const prev = this.hostSnmpTempHistory[hostId] || {};
    this.hostSnmpTempHistory[hostId] = {
      loading: true,
      error: prev.error || '',
      probes: prev.probes && typeof prev.probes === 'object' ? prev.probes : {},
      loadedAt: prev.loadedAt || 0,
    };
    try {
      const r = await fetch(
        `/api/hosts/${encodeURIComponent(hostId)}/snmp/temp_history?hours=${h}`
      );
      if (!r.ok) {
        this.hostSnmpTempHistory[hostId] = {
          loading: false, error: `HTTP ${r.status}`,
          probes: prev.probes || {}, loadedAt: prev.loadedAt || 0,
        };
        return;
      }
      const d = await r.json();
      this.hostSnmpTempHistory[hostId] = {
        loading: false,
        error: d.error || '',
        probes: (d.probes && typeof d.probes === 'object') ? d.probes : {},
        loadedAt: Date.now(),
      };
    } catch (e) {
      this.hostSnmpTempHistory[hostId] = {
        loading: false, error: String(e),
        probes: prev.probes || {}, loadedAt: prev.loadedAt || 0,
      };
    }
  },
  async loadHostSnmpIfaceHistory(hostId, hours) {
    if (!hostId) {
      return;
    }
    const h = +hours || 1;
    const prev = this.hostSnmpIfaceHistory[hostId] || {};
    this.hostSnmpIfaceHistory[hostId] = {
      loading: true,
      error: prev.error || '',
      ifaces: prev.ifaces && typeof prev.ifaces === 'object' ? prev.ifaces : {},
      loadedAt: prev.loadedAt || 0,
    };
    try {
      const r = await fetch(
        `/api/hosts/${encodeURIComponent(hostId)}/snmp/iface_history?hours=${h}`
      );
      if (!r.ok) {
        // Stamp loadedAt even on failure so the staleness check
        // doesn't fire a thundering retry on every poll cycle —
        // pre-fix the cache stayed at loadedAt:0 forever, every
        // _cacheStale check returned true, the polling loop would
        // re-fetch every cycle even though every fetch was 5xx.
        // Visible in console so debugging is possible.
        console.warn('[snmp] loadHostSnmpIfaceHistory ' + hostId + ' HTTP ' + r.status);
        this.hostSnmpIfaceHistory[hostId] = {
          loading: false, error: `HTTP ${r.status}`,
          ifaces: prev.ifaces || {}, loadedAt: Date.now(),
        };
        return;
      }
      const d = await r.json();
      const ifaces = (d.ifaces && typeof d.ifaces === 'object') ? d.ifaces : {};
      const ifaceCount = Object.keys(ifaces).length;
      if (ifaceCount === 0) {
        console.warn('[snmp] loadHostSnmpIfaceHistory ' + hostId + ' returned empty ifaces (sampler may not have written yet, or backend response shape mismatch)');
      }
      this.hostSnmpIfaceHistory[hostId] = {
        loading: false,
        error: d.error || '',
        ifaces,
        // Server-side bucket cadence — lets snmpIfaceBpsSeries scale
        // its dt cap so the per-port chart doesn't blank on 7d+
        // windows where the bucket size exceeds the static 3600s cap.
        // 0 when the response wasn't bucketed (≤2h windows).
        bucket_seconds: Number(d.bucket_seconds) || 0,
        loadedAt: Date.now(),
      };
    } catch (e) {
      console.warn('[snmp] loadHostSnmpIfaceHistory ' + hostId + ' fetch threw:', e);
      this.hostSnmpIfaceHistory[hostId] = {
        loading: false, error: String(e),
        ifaces: prev.ifaces || {}, loadedAt: Date.now(),
      };
    }
  },
  async loadHostHistory(systemId, hostId) {
    // Preserve whatever series we already have so the chart doesn't
    // flicker back to "Collecting data…" between range-picker
    // clicks. Only the ``loading`` flag flips; the visible line
    // stays put until fresh data lands, then swaps in place.
    // Cache key: prefer beszel_id when present (Beszel path), else
    // fall back to the curated host_id (NE-only path). Every chart
    // helper looks up by this same key, so the templates pass
    // `hostHistoryKey(h)` instead of bare `h.beszel_id`.
    if (!hostId) {
      const host = (this.hosts || []).find(h => h.beszel_id === systemId);
      hostId = host ? host.id : '';
    }
    const cacheKey = systemId || hostId;
    if (!cacheKey) {
      return;
    }
    const prev = this.hostHistory[cacheKey] || {};
    this.hostHistory[cacheKey] = {
      loading: true,
      error: prev.error || '',
      series: Array.isArray(prev.series) ? prev.series : [],
      collectors: prev.collectors || null,
      loadedAt: prev.loadedAt || 0,
    };
    try {
      const qs = {
        system_id: systemId || '',
        hours: String(this.hostHistoryRange),
      };
      if (hostId) {
        qs.host_id = hostId;
      }
      const params = new URLSearchParams(qs);
      const r = await fetch('/api/hosts/history?' + params.toString());
      if (!r.ok) {
        this.hostHistory[cacheKey] = {
          loading: false,
          error: `HTTP ${r.status}`,
          series: prev.series || [],  // keep previous on HTTP error
          collectors: prev.collectors || null,
          loadedAt: prev.loadedAt || 0,
        };
        return;
      }
      const d = await r.json();
      const next = Array.isArray(d.series) ? d.series : [];
      // Enrich the latest series point with live merged stats for
      // `temps` / `gpus` / `temp_max` / `gpu_pwr` / `gpu_usage` /
      // `gpu_vram_pct` when the host has live data but the persisted
      // samples haven't caught up yet (sampler warm-up gap, or a
      // newly-enabled GPU agent). Otherwise the chart shows
      // "Collecting data" indefinitely even though the operator can
      // see the values in the host card / drawer header.
      try {
        const host = (this.hosts || []).find(h => h && h.id === hostId);
        if (host && next.length) {
          const last = next[next.length - 1];
          const liveTemps = host.host_temperatures || null;
          const liveGpus = Array.isArray(host.host_gpus) ? host.host_gpus : [];
          const seriesHasTemps = next.some(r => r && r.temps && Object.keys(r.temps).length > 0);
          const seriesHasGpu = next.some(r => r && Number(r.gpu_pwr) > 0);
          if (!seriesHasTemps && liveTemps && Object.keys(liveTemps).length > 0) {
            // Backfill the LIVE per-sensor reading across EVERY series
            // row, not just the last one — Beszel's hub doesn't always
            // persist `stats.t` in the aggregated `system_stats`
            // collection (the live `systems` collection has them but
            // the history doesn't), and stamping only the LAST row
            // produces a single-point SVG path (`M x,y` with no `L`)
            // which renders as nothing. Backfilling gives the chart a
            // flat horizontal line at the current temp — visible
            // signal that the chart card is alive even when historical
            // temp data is missing. When historical data lands later
            // (`seriesHasTemps === true`) this branch is skipped and
            // the real per-tick variation renders.
            const vals = Object.values(liveTemps).map(v => Number(v)).filter(Number.isFinite);
            const maxTemp = vals.length ? Math.max(...vals) : 0;
            for (const row of next) {
              if (!row) {
                continue;
              }
              row.temps = liveTemps;
              row.temp_max = maxTemp;
            }
          }
          if (!seriesHasGpu && liveGpus.length) {
            let pwrSum = 0, usageSum = 0, vUsedSum = 0, vTotSum = 0, n = 0;
            for (const g of liveGpus) {
              if (!g || typeof g !== 'object') {
                continue;
              }
              const w = Number(g.power_watts);
              if (Number.isFinite(w)) {
                pwrSum += w;
              }
              const u = Number(g.usage_percent);
              if (Number.isFinite(u)) {
                usageSum += u;
              }
              const vu = Number(g.vram_used_bytes);
              if (Number.isFinite(vu)) {
                vUsedSum += vu;
              }
              const vt = Number(g.vram_total_bytes);
              if (Number.isFinite(vt)) {
                vTotSum += vt;
              }
              n += 1;
            }
            if (n) {
              last.gpus = liveGpus;
              last.gpu_pwr = pwrSum / n;
              last.gpu_usage = usageSum / n;
              last.gpu_vram_pct = vTotSum > 0 ? (100 * vUsedSum / vTotSum) : 0;
            }
          }
        }
      } catch (_) { /* enrichment is best-effort */
      }
      // Stamp loadedAt on every successful HTTP 2xx, regardless of
      // whether the series came back populated. Operator expectation
      // is "when did we last poll the backend" (matching their
      // statsInterval cadence) — an occasional empty-series reply
      // (hub briefly returning [] during a restart, or a host with
      // no samples in the selected window) shouldn't make the
      // freshness label drift past one poll cycle. The chart
      // VALUES still preserve `prev.series` on empty so the visible
      // line doesn't blank, but the timestamp follows fetches.
      //
      const stamp = Date.now();
      this.hostHistory[cacheKey] = {
        loading: false,
        error: d.error || '',
        // Only overwrite on a non-empty response. A transient empty
        // reply (hub rebooting, rate-limit) shouldn't blank a chart
        // that was already populated.
        series: next.length ? next : (prev.series || []),
        // NE-only path returns a `collectors` dict per telling
        // us whether each metric ever produced a non-null sample in
        // the window. Beszel path doesn't include it; null = unknown.
        collectors: d.collectors || null,
        loadedAt: stamp,
      };
    } catch (e) {
      this.hostHistory[cacheKey] = {
        loading: false,
        error: e.message,
        series: prev.series || [],
        collectors: prev.collectors || null,
        loadedAt: prev.loadedAt || 0,
      };
    }
  },
  // Subtle freshness label for the host-drawer chart-grid header
  //. Returns a short translated string like "Updated 2m ago"
  // or empty when the cache hasn't seen a successful fetch yet
  // (caller hides the line). Reads `hostHistoryNow` (ticked every
  // 30s) so the label stays current without re-fetching the data.
  hostHistoryFreshness(h) {
    if (!h) {
      return '';
    }
    const key = this.hostHistoryKey(h);
    const entry = this.hostHistory[key];
    if (!entry || !entry.loadedAt) {
      return '';
    }
    // Don't render "Updated Xs ago" on a permanently-flat series.
    // A SNMP-only host with no Beszel/NE wired keeps `loadedAt`
    // updated by the polling loop even though the series is
    // empty / all-zero — operator reads "Updated 2s ago" as
    // "fresh data" but there's nothing to look at. Suppress the
    // freshness label when fewer than 2 history points exist OR
    // when every point is zero across the canonical metric keys.
    const series = entry.series || [];
    if (series.length < 2) {
      return '';
    }
    const sentinel = ['cpu', 'mp', 'dp', 'net', 'dr', 'dw', 'la1_pct', 'temp_max', 'gpu_pwr', 'gpu_usage', 'gpu_vram_pct'];
    let hasData = false;
    for (const r of series) {
      for (const k of sentinel) {
        if ((+r[k] || 0) > 0) {
          hasData = true;
          break;
        }
      }
      if (hasData) {
        break;
      }
    }
    if (!hasData) {
      return '';
    }
    // `hostHistoryNow` is bumped on a 30s timer; touching it inside
    // the getter means Alpine re-evaluates whenever it ticks.
    const now = this.hostHistoryNow || Date.now();
    const ageMs = Math.max(0, now - entry.loadedAt);
    const ageS = Math.floor(ageMs / 1000);
    if (ageS < 60) {
      return this.t('hosts_extra.metrics.last_updated_seconds', {count: ageS});
    }
    if (ageS < 3600) {
      return this.t('hosts_extra.metrics.last_updated_minutes', {
        count: Math.floor(ageS / 60),
      });
    }
    return this.t('hosts_extra.metrics.last_updated_hours', {
      count: Math.floor(ageS / 3600),
    });
  },
  // absolute-time tooltip companion for the
  // relative "Updated Xs ago" label. Operators correlating a chart
  // anomaly with Grafana / Prometheus dashboards need a stable
  // anchor — the relative label drifts every second, the ISO string
  // doesn't.
  hostHistoryFreshnessAbsolute(h) {
    if (!h) {
      return '';
    }
    const key = this.hostHistoryKey(h);
    const entry = this.hostHistory[key];
    if (!entry || !entry.loadedAt) {
      return '';
    }
    try {
      return new Date(entry.loadedAt).toISOString().replace(/\.\d{3}Z$/, 'Z');
    } catch (_) {
      return '';
    }
  },
  // Resolve the right hostHistory[] key for one host. Beszel-mapped
  // hosts use the Beszel system id (legacy behaviour); NE-only hosts
  // fall back to the curated hosts_config id (the same id the
  // host_metrics_sampler keys its rows on). Returns '' when neither
  // path is available — chart helpers short-circuit on falsy keys.
  hostHistoryKey(h) {
    if (!h) {
      return '';
    }
    return h.beszel_id || h.id || '';
  },

  // separate key namespace for the ping-latency drawer chart.
  // Stored as a sibling slot in `hostHistory` so the existing chart
  // helpers (`hostChart` / `hostChartMax` / `hostMetricStats`) work
  // unmodified — they read `entry.series[<idx>][key]`, where key is
  // `'rtt'` for the ping series. Using `hostHistoryKey` directly
  // would pollute the host's main history slot (which carries
  // cpu/mp/dp/nr/ns from Beszel/NE on different timestamps), so the
  // ping series gets its own `ping:<id>` namespace.
  hostPingHistoryKey(h) {
    if (!h || !h.id) {
      return '';
    }
    return 'ping:' + h.id;
  },

  // fetch /api/hosts/{id}/ping/history for the host whose
  // drawer is open and store as `entry.series` on a separate
  // namespace so the existing chart helpers work without changes.
  // Mirrors `loadHostHistory`'s shape: stamp `loadedAt` for the
  // freshness label, leave the previous series in place on a
  // network blip (no wholesale array reassignment).
  async loadHostPingHistory(hostId) {
    if (!hostId) {
      return;
    }
    const key = 'ping:' + hostId;
    // Honour the shared host-history range picker.
    // Was hardcoded to ?hours=24; now reads `hostHistoryRange` so
    // the ping series re-fetches with the same window as CPU /
    // Memory / Disk / Net when the operator clicks 1h / 6h / 24h / 7d.
    const hours = Math.max(1, Math.min(168, Number(this.hostHistoryRange) || 24));
    try {
      const r = await fetch('/api/hosts/' + encodeURIComponent(hostId) + '/ping/history?hours=' + hours);
      if (!r.ok) {
        return;
      }
      const d = await r.json();
      // Field name `t` matches the convention `loadHostHistory` uses
      // (and what `xAxisFromSeries` reads at s[idx].t). Also keep
      // `ts` for back-compat with any consumer expecting the raw
      // pocketbase column name. Pre-fix the x-axis was blank because
      // xAxisFromSeries pulled from `.t` which was undefined.
      // Server now buckets when hours > 2 (see api_hosts_ping_history)
      // so `rtt_ms` may legitimately be null for a bucket that was
      // entirely down (no alive samples to average). Preserve null
      // through to the chart helper so the polyline's
      // skip-don't-synthesize path renders the period as a gap
      // rather than plotting a fake 0ms latency point.
      const points = (d.points || []).map(p => ({
        t: Number(p.ts) || 0,
        ts: Number(p.ts) || 0,
        rtt: (p.rtt_ms === null || p.rtt_ms === undefined)
          ? null : (Number(p.rtt_ms) || 0),
        alive: !!p.alive,
        loss_pct: Number(p.loss_pct) || 0,
      }));
      if (!this.hostHistory[key]) {
        this.hostHistory[key] = {};
      }
      this.hostHistory[key].series = points;
      this.hostHistory[key].loadedAt = Date.now();
    } catch (e) {
      console.warn('[ping] loadHostPingHistory failed:', e);
    }
  },

  // Busy flag. True while the picker's underlying loaders
  // are in flight — bound to each picker button's `:disabled` so
  // operators can't queue rapid 1h → 6h → 24h clicks while the
  // first fetch is still resolving (the SPA used to lag the chart
  // updates and present inconsistent data on slow networks). Cleared
  // in a `finally` even on fetch errors so a flapped picker recovers.
  hostHistoryRangeBusy: false,
  async setHostHistoryRange(hours) {
    // Ignore re-clicks while a previous picker click is still
    // resolving — the buttons are also disabled in the markup but
    // belt-and-braces against keyboard / programmatic invocations.
    if (this.hostHistoryRangeBusy) {
      return;
    }
    this.hostHistoryRange = hours;
    const hrs = Math.max(1, Math.min(168, Number(hours) || 1));
    this.hostHistoryRangeBusy = true;
    // Safety timer — if any single loader hangs forever (network
    // freeze, broken proxy holding the connection), the busy flag
    // would otherwise stick true. 30s mirrors the per-host probe
    // budget ; the operator regains control even when a fetch
    // never resolves.
    const safetyTimer = setTimeout(() => {
      this.hostHistoryRangeBusy = false;
    }, 30000);
    try {
      const tasks = [];
      // Reload the open drawer host's history.
      if (this.drawerHost && (this.drawerHost.beszel_id || this.drawerHost.ne_url || this.drawerHost.pulse_name || this.drawerHost.webmin_name)) {
        tasks.push(this.loadHostHistory(this.drawerHost.beszel_id || '', this.drawerHost.id));
      }
      // Ping chart shares the same range picker. When the operator
      // switches between 1h / 6h / 24h / 7d, the ping series re-fetches
      // alongside CPU / Mem / Disk / Net / Disk-IO.
      if (this.drawerHost && this.drawerHost.ping_enabled) {
        tasks.push(this.loadHostPingHistory(this.drawerHost.id));
      }
      // SNMP charts (CPU per-core, load, memory stacked-area, total
      // throughput, per-port throughput, per-port utilization, printer
      // pages) ALL render off `hostSnmpHistory[hostId].points` +
      // `hostSnmpIfaceHistory[hostId].ifaces`. Pre-fix the range picker
      // was wired ONLY to Beszel/NE/Ping — clicking 6h/24h/7d on an
      // SNMP-only host left the SNMP chart cards stuck at the initial
      // 1h window. Re-fetch both SNMP series here so the picker drives
      // every chart card on the host uniformly.
      // Loose gate via `_snmpHasProbeTarget` so hosts with the
      // sampler running via `snmp_name` but no curated UI checkbox
      // ticked (snmp_enabled=false) still re-fetch on range change.
      // Pre-fix the gate was strict (`snmp_enabled` only), so a host
      // whose drawer-open path populated the iface cache via the
      // looser `_snmpHasProbeTarget` then clicked 6h/24h/7d would
      // get the cache STAY at the initial 1h window. Same uniformity
      // rule documented for the FIVE chart-fetch sites in the
      // SNMP-history reload sweep — this is the SIXTH site that was
      // missed in the original sweep.
      if (this.drawerHost && this._snmpHasProbeTarget(this.drawerHost)) {
        if (typeof this.loadHostSnmpHistory === 'function') {
          tasks.push(this.loadHostSnmpHistory(this.drawerHost.id, hrs));
        }
        if (typeof this.loadHostSnmpIfaceHistory === 'function') {
          tasks.push(this.loadHostSnmpIfaceHistory(this.drawerHost.id, hrs));
        }
        if (typeof this.loadHostSnmpTempHistory === 'function') {
          tasks.push(this.loadHostSnmpTempHistory(this.drawerHost.id, hrs));
        }
      }
      // HTTP probe latency history — fires alongside the other
      // per-host charts so the range picker drives every card
      // uniformly. Gate on http_probe_enabled OR existing samples
      // (a recently-disabled probe should still re-fetch one final
      // window so the chart shows what was collected pre-disable).
      if (this.drawerHost && (this.drawerHost.http_probe_enabled || (this.hostHttpProbeHistory && this.hostHttpProbeHistory[this.drawerHost.id]))) {
        if (typeof this.loadHostHttpProbeHistory === 'function') {
          tasks.push(this.loadHostHttpProbeHistory(this.drawerHost.id, hrs));
        }
      }
      // Also handle any legacy expanded rows (kept for back-compat —
      // the inline-expansion code path is mostly dead but not yet
      // removed; covering both means the range works wherever the
      // user clicks it from).
      for (const name of (this.hostsExpanded || [])) {
        const host = (this.hosts || []).find(h => h.host === name);
        if (host && (host.beszel_id || host.ne_url || host.pulse_name || host.webmin_name)) {
          tasks.push(this.loadHostHistory(host.beszel_id || '', host.id));
        }
      }
      // `allSettled` so one failing loader doesn't leave the picker
      // stuck disabled — a 5xx on Pulse history shouldn't prevent
      // the operator from swapping back to 1h.
      if (tasks.length) {
        await Promise.allSettled(tasks);
      }
    } finally {
      clearTimeout(safetyTimer);
      this.hostHistoryRangeBusy = false;
    }
  },
};
