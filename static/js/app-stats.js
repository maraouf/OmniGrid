// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ContinueStatementJS,BreakStatementJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML,JSAsyncFunctionMissingAwait
// noinspection JSMissingAwait,JSUnfilteredForInLoop,IfStatementWithoutBlockJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSPotentiallyInvalidUsageOfThis,HtmlUnknownTag,OverlyComplexArithmeticExpressionJS,PointlessArithmeticExpressionJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Stats view (top-nav `stats` view) — aggregate metrics across the
// fleet (Overview / Database / Network / Incidents / Samples sub-tabs).
//
// Also includes the Nodes view's per-node spark + stats helpers
// (`nodeGroups`, `nodeStats`, `nodeSparkPoints`, `nodeSparkClass`).

// Per-flush memo for nodeStats(host), keyed by host name. nodeStats is pure
// within a synchronous reactive flush (depends only on this.items /
// this.stats / this.nodesInfo / settings, none of which change mid-flush),
// and the Nodes view reads it ~15x per node card (directly + via
// nodeCpuPercent / nodeMemPercent / nodeDiskPercent / nodeProviderChip /
// nodeProviderList). Caching it for the flush turns that into ONE compute
// per host. Module-scope (non-reactive) singleton — exactly one app()
// instance — cleared on the next microtask so the following flush recomputes
// against fresh state (zero staleness, same contract as filteredHosts).
let _nodeStatsFlushCache = null;
let _nodeStatsFlushScheduled = false;

function _clearNodeStatsFlushCache() {
  _nodeStatsFlushCache = null;
  _nodeStatsFlushScheduled = false;
}

// per-flush memo for nodeSparkPoints(host, key) — same contract as
// _nodeStatsFlushCache. The Nodes view binds nodeSparkPoints for cpu/mem/disk
// per node card (disk twice + via x-show AND :points), ~8 calls/card/flush,
// each O(items x history) (it bins EVERY item's spark series). One build per
// (host,key) per flush; cleared on the next microtask. Nested Map keyed on the
// host (same key space as _nodeStatsFlushCache — object/string safe) then the
// metric. Safe (no freeze) because the Nodes cards live in an x-for that
// re-renders on the stats/sparks poll, re-evaluating these bindings fresh.
let _nodeSparkFlushCache = null;
let _nodeSparkFlushScheduled = false;

function _clearNodeSparkFlushCache() {
  _nodeSparkFlushCache = null;
  _nodeSparkFlushScheduled = false;
}

// Reactive-subscription helper. Passing a reactive prop as an argument
// here reads it, which registers Alpine's dependency tracking for that
// prop — used where a method must SUBSCRIBE to props it doesn't otherwise
// reference because a downstream per-flush memo (e.g. filteredItems) can
// cache-hit and skip reading them, leaving the effect un-subscribed (see
// nodeGroups). A no-op call statement: avoids the `void` / always-false-
// guard / unused-expression lint flags while still doing the read.
function _touchReactiveDeps(...deps) {
  return deps.length;
}

export default {
  stats: {}, _statsTimer: null, _maxSize: 1,
  // Flips to true on the first successful `/api/stats` response so
  // the Stacks / Services rows swap their loading spinner for the
  // resolved status dot. Stays true once flipped — the spinner is
  // an initial-paint affordance, not a per-poll signal.
  statsLoaded: false,
  sparks: {}, _sparksTimer: null,
  statsInterval: (() => {
    const v = parseInt(localStorage.getItem('statsInterval'), 10);
    return [0, 5, 15, 30, 60].includes(v) ? v : 15;
  })(),
  statsRefreshing: false,
  // Stats view — admin-only quick-insight pages. Sub-tabs grow with
  // each release; canonical roster lives here and the matching i18n
  // keys hang off `stats.sections.<id>`.
  statsSections: [
    {id: 'dashboard', label: 'Dashboard', icon: 'layout-dashboard'},
    {id: 'database', label: 'Database', icon: 'database'},
    {id: 'samples', label: 'Samples', icon: 'chart-scatter'},
    {id: 'samplers', label: 'Samplers', icon: 'loader'},
    {id: 'incidents', label: 'Incidents', icon: 'alert-triangle'},
    {id: 'network', label: 'Network', icon: 'activity'},
    {id: 'ai_cost', label: 'AI Cost', icon: 'zap'},
  ],
  // Operator-selected sub-tab (Dashboard / Database / Samples /
  // Samplers / Incidents / Network / AI Cost). Persisted to
  // `localStorage.statsTab` so the operator's last tab is restored
  // on next visit. Validated against `statsSections[].id` so a
  // stale localStorage value can't crash the binding.
  statsTab: (() => {
    try {
      const v = (typeof localStorage !== 'undefined' && localStorage.getItem('statsTab')) || '';
      if (['dashboard', 'database', 'samples', 'samplers', 'incidents', 'network', 'ai_cost'].includes(v)) {
        return v;
      }
    } catch (_) { /* private mode — fall through */
    }
    return 'dashboard';
  })(),
  statsOverview: {},
  // Stats → Dashboard summary cards (db size / total samples / incidents /
  // network / AI cost / AI jobs over 30d). Loaded alongside the overview but
  // from a SEPARATE endpoint (`/api/admin/stats/summary`) because the
  // total-samples figure is a COUNT(*) scan across ~18 tables — kept off the
  // fast overview fetch so the main grid paints immediately and the summary
  // cards fill in when their (cached, 60s) result lands.
  statsSummary: {},
  statsSummaryLoaded: false,
  // Stats → Samplers (per-sampler tick + prune health). Lazy-loaded
  // on first tab activation; reloadable via the panel's Reload
  // button.
  statsSamplers: [],
  statsSamplersLoaded: false,
  statsOverviewLoaded: false,
  statsDatabase: {},
  statsDatabaseLoaded: false,
  statsSamples: {},
  statsSamplesLoaded: false,
  // Per-provider drill-down modal — opens on chip click in the
  // Samples breakdown table. `open` toggles visibility; the other
  // fields carry the in-flight fetch state + the resolved data.
  statsSamplesDrillDown: {
    open: false,
    loading: false,
    table: '',     // canonical sample-bearing table name
    provider: '',    // lowercased provider tag — drives context-aware orphan marker (item_id vs host_id)
    host_col: '',    // backend-reported host-id column name (`host_id` for hosts, `item_id` for Portainer stats_samples)
    label: '',     // operator-friendly heading (provider + kind)
    rows: [],     // [{host_id, rows, label, address, *_name, curated}, ...] sorted DESC server-side
    total: 0,      // SUM(rows) — cross-checks against outer count
    outer: 0,      // outer per-table row count (set fresh by backend's outer_count)
    error: '',
    // Per-row prune busy-state map keyed by host_id. MUST be in the
    // initial state declaration so Alpine's first reactive read of
    // `pruning[row.host_id]` from the modal markup doesn't throw a
    // "cannot read property of undefined" — the error-trap fallback
    // would otherwise render the Delete button as disabled.
    pruning: {},
  },
  statsIncidents: {},
  statsIncidentsLoaded: false,
  // Operator-selected range for the Incidents sub-tab (hours).
  // Persisted to `localStorage.statsIncidentsHours` so the chosen
  // window survives a reload. Default 168h (7d). Validated against
  // the canonical {24, 168, 720} so a stale value can't display
  // an unsupported window.
  statsIncidentsHours: (() => {
    try {
      const v = +(typeof localStorage !== 'undefined' && localStorage.getItem('statsIncidentsHours'));
      if ([24, 168, 720].includes(v)) {
        return v;
      }
    } catch (_) { /* private mode */
    }
    return 168;
  })(),
  statsNetwork: {},
  statsNetworkLoaded: false,
  statsNetworkHours: (() => {
    try {
      const v = +(typeof localStorage !== 'undefined' && localStorage.getItem('statsNetworkHours'));
      if ([24, 168, 720].includes(v)) {
        return v;
      }
    } catch (_) { /* private mode */
    }
    return 168;
  })(),

  // -----------------------------------------------------------------
  // Nodes view — groups the fleet by which Swarm node each task /
  // container lives on. Services appear under EVERY node their tasks
  // run on (so a 3-replica global service shows under all 3 nodes);
  // plain containers / orphans appear under their single node.
  // -----------------------------------------------------------------
  nodeGroups() {
    // Touch the filter inputs DIRECTLY so the Nodes x-for effect
    // subscribes to them. `this.filteredItems` (read below) is a
    // per-flush memo: if another visible-via-x-show view (Services'
    // sortedFiltered / counts) populated its cache earlier in the same
    // flush, the call here returns a cache HIT that never reads the
    // filter props — so the Nodes effect wouldn't subscribe and a filter
    // change left the expanded nodes showing the PREVIOUS filter's
    // containers (the caveat-6 memo-subscription trap, same class as the
    // groupedHosts filter bug). Reading them as args to the no-op
    // `_touchReactiveDeps` registers the dependency without a `void` /
    // always-false-guard / unused-expression lint flag.
    _touchReactiveDeps(this.search, this.statusFilter, this.healthFilter);
    // Seed with every known node so a node with zero items still
    // renders (helps spot "this worker is empty" at a glance).
    const byNode = new Map();
    for (const id in (this.nodes || {})) {
      const host = this.nodes[id];
      if (host) {
        byNode.set(host, {name: host, items: [], stacks: {}});
      }
    }

    // Pick which items we're filtering over. Reuse the same filter
    // pipeline as the stacks/services views (search + status + health).
    const items = this.filteredItems;

    for (const it of items) {
      // Services carry `placements: [{node, state}, ...]`; standalones
      // carry a single `node` field. Derive the set of nodes either way.
      const nodes = new Set();
      if (Array.isArray(it.placements) && it.placements.length) {
        for (const p of it.placements) {
          if (p && p.node && p.node !== '?' && p.node !== 'local') {
            nodes.add(p.node);
          }
        }
      }
      if (nodes.size === 0 && it.node && it.node !== '?' && it.node !== 'local') {
        nodes.add(it.node);
      }
      // No identifiable node → park under a synthetic "Unpinned" group.
      if (nodes.size === 0) {
        nodes.add('__unpinned__');
      }

      for (const n of nodes) {
        if (!byNode.has(n)) {
          byNode.set(n, {
            name: n === '__unpinned__' ? 'Unpinned / local' : n,
            items: [], stacks: {},
            is_unpinned: n === '__unpinned__',
          });
        }
        const g = byNode.get(n);
        g.items.push(it);
        const stackKey = it.stack || '__standalone__';
        if (!g.stacks[stackKey]) {
          g.stacks[stackKey] = {
            name: it.stack || 'Standalone',
            items: [],
            is_standalone: !it.stack,
          };
        }
        g.stacks[stackKey].items.push(it);
      }
    }

    // Finalise each group: counts + sorted stack list + sorted items.
    const out = [];
    for (const [key, g] of byNode) {
      const its = g.items;
      const stackList = Object.values(g.stacks)
        .map(s => ({
          ...s,
          items: s.items.slice().sort((a, b) => (a.name || '').localeCompare(b.name || '')),
        }))
        .sort((a, b) => (a.name || '').localeCompare(b.name || ''));
      out.push({
        key,
        name: g.name,
        is_unpinned: !!g.is_unpinned,
        total: its.length,
        services: its.filter(i => i.type === 'service').length,
        containers: its.filter(i => i.type === 'container' || i.type === 'orphan').length,
        stacks: stackList.filter(s => !s.is_standalone).length,
        // `updates` counts ONLY actionable live items — running
        // items with a newer remote digest. Offline / orphan
        // containers with stale digests are tracked separately
        // under `updates_offline` so the per-node header chip
        // doesn't inflate when the operator just has a few exited
        // task containers whose pinned image happens to have
        // drifted. Mirrors the per-stack rollup in
        // `logic/gather.py` (the server-side split for the
        // Stacks view) so both views read consistently.
        updates: its.filter(i => i.status === 'update' && i.health !== 'offline').length,
        updates_offline: its.filter(i => i.status === 'update' && i.health === 'offline').length,
        // Up-to-date count so the Nodes header can show a green
        // pill matching the Stacks view's `uptodate / total ok`
        // convention. Without this, Nodes shows only the bad
        // counts (updates / offline / degraded) and operators saw
        // the absence of a green affordance as inconsistent UX.
        uptodate: its.filter(i => i.status === 'up-to-date').length,
        offline: its.filter(i => i.health === 'offline').length,
        degraded: its.filter(i => i.health === 'degraded').length,
        errors: its.filter(i => i.status === 'error').length,
        stackList,
      });
    }
    // Sort: real nodes alphabetically, "Unpinned" last.
    return out.sort((a, b) => {
      if (a.is_unpinned !== b.is_unpinned) {
        return a.is_unpinned ? 1 : -1;
      }
      return (a.name || '').localeCompare(b.name || '');
    });
  },
  async loadStatsOverview() {
    // Re-skeleton on every (re)load: flip the loaded flag OFF up front so a
    // Reload click re-shows the in-card skeletons until the fresh data lands,
    // instead of leaving the stale values on screen. The `finally` below flips
    // it back true regardless of success/failure so the skeleton can't stick.
    this.statsOverviewLoaded = false;
    // Fire the (slower, COUNT-scan-backed) summary fetch in PARALLEL — the
    // trailing `.catch` marks the non-await as INTENTIONAL (matches the repo's
    // fire-and-forget convention, e.g. loadHostsConfig().catch(...) in
    // app-admin.js). The main overview grid paints immediately and the six
    // summary cards fill in when their result lands; loadStatsSummary already
    // swallows its own errors + sets its loaded flag, so this catch never fires.
    this.loadStatsSummary().catch(() => { /* fire-and-forget */
    });
    try {
      const r = await fetch('/api/admin/stats/overview');
      if (!r.ok) {
        return;
      }
      this.statsOverview = await r.json();
    } catch (_) {
    } finally {
      this.statsOverviewLoaded = true;
    }
  },
  // Dashboard summary cards (db size / total samples / incidents / network /
  // AI cost / AI jobs over 30d) from `/api/admin/stats/summary` (cached 60s
  // server-side). Separate from loadStatsOverview so the heavy total-samples
  // COUNT scan never delays the main grid's first paint.
  async loadStatsSummary() {
    // Re-skeleton the six summary cards on every (re)load — see the matching
    // note in loadStatsOverview. The `finally` restores the flag so it can't
    // stick on a failed fetch.
    this.statsSummaryLoaded = false;
    try {
      const r = await fetch('/api/admin/stats/summary');
      if (!r.ok) {
        return;
      }
      this.statsSummary = await r.json();
    } catch (_) {
    } finally {
      this.statsSummaryLoaded = true;
    }
  },
  async loadStatsDatabase() {
    try {
      const r = await fetch('/api/admin/stats/database');
      if (!r.ok) {
        return;
      }
      this.statsDatabase = await r.json();
    } catch (_) {
    } finally {
      this.statsDatabaseLoaded = true;
    }
  },
  // Stats → Samplers loader. Reads `/api/admin/stats/samplers`
  // (single module-level dict read, sub-millisecond) so the panel
  // can paint fresh on Reload click. Defensive: swallows network /
  // shape errors so a transient blip during a sampler restart
  // doesn't leave the panel stuck in a loading state.
  async loadStatsSamplers() {
    try {
      const r = await fetch('/api/admin/stats/samplers');
      if (!r.ok) {
        return;
      }
      const data = await r.json();
      this.statsSamplers = Array.isArray(data && data.samplers) ? data.samplers : [];
    } catch (_) {
    } finally {
      this.statsSamplersLoaded = true;
    }
  },
  // Format ms-duration for the samplers table. Sub-1s renders as
  // `Nms` (operator-friendly); >=1s renders as `N.Ns` so the table
  // doesn't show "4823ms" — `4.8s` reads faster + matches the
  // host-drawer chart-card format. Defensive on non-numeric input.
  statsSamplersFormatMs(ms) {
    const n = Number(ms);
    if (!Number.isFinite(n) || n < 0) {
      return '—';
    }
    if (n < 1000) {
      return Math.round(n) + 'ms';
    }
    return (n / 1000).toFixed(1) + 's';
  },
  // Colour the duration cell when it crosses the slow-loop boundary
  // (100ms — the same boundary the project conventions uses for the `to_thread`
  // offload rule). Amber at 100ms-1s; red at >=1s. Operators spot
  // a sampler that started taking 5 seconds when it used to take 50
  // ms at a glance via the colour.
  statsSamplersDurationClass(ms) {
    const n = Number(ms);
    if (!Number.isFinite(n) || n < 0) {
      return 'text-[var(--text-faint)]';
    }
    if (n >= 1000) {
      return 'text-[var(--danger)] font-semibold';
    }
    if (n >= 100) {
      return 'text-[var(--warning)] font-semibold';
    }
    return 'text-[var(--text-dim)]';
  },
  // Operator-friendly "N ago" age formatter for the last-tick /
  // last-prune columns. Sub-60s as seconds, sub-60min as minutes,
  // beyond as hours. Hours is the right ceiling — prune runs
  // hourly so a 25h gap on a sampler tick is the strongest signal
  // the operator wants ("did this sampler stop?").
  statsSamplersAge(seconds) {
    const n = Number(seconds);
    if (!Number.isFinite(n) || n < 0) {
      return '—';
    }
    if (n < 60) {
      return n + 's ago';
    }
    if (n < 3600) {
      return Math.floor(n / 60) + 'm ago';
    }
    return Math.floor(n / 3600) + 'h ago';
  },
  // Range for the "Samples written per day" chart. Distinct from
  // the global stats range (rest of the Samples tab is all-time);
  // operator-flagged 2026-05-11 to add per-section range picker
  // matching the host-chart picker shape (1h / 24h / 7d / 30d).
  // Persisted to `localStorage.statsSamplesRange` so the chart
  // remembers the operator's range across reloads. Validated
  // against the canonical {'1h','24h','7d','30d','90d','all'} set.
  statsSamplesRange: (() => {
    try {
      const v = (typeof localStorage !== 'undefined' && localStorage.getItem('statsSamplesRange')) || '';
      if (['1h', '24h', '7d', '30d', '90d', 'all'].includes(v)) {
        return v;
      }
    } catch (_) { /* private mode */
    }
    return '90d';
  })(),
  // Per-table breakdown sort state. Operator-flagged: every column
  // sortable, default by `rows` desc. Clicking the same column flips
  // direction; clicking a different column switches to it (default
  // desc for numeric / oldest_ts / newest_ts, asc for string columns).
  statsSamplesSortBy: 'rows',
  statsSamplesSortDir: 'desc',
  _statsSamplesSorted() {
    const rows = (this.statsSamples && this.statsSamples.tables) || [];
    const col = this.statsSamplesSortBy || 'rows';
    const dir = this.statsSamplesSortDir === 'asc' ? 1 : -1;
    const isNum = (col === 'rows' || col === 'unique_hosts'
      || col === 'oldest_ts' || col === 'newest_ts');
    return rows.slice().sort((a, b) => {
      let av = a ? a[col] : null;
      let bv = b ? b[col] : null;
      if (isNum) {
        av = Number(av) || 0;
        bv = Number(bv) || 0;
        return (av - bv) * dir;
      }
      av = (av == null ? '' : String(av)).toLowerCase();
      bv = (bv == null ? '' : String(bv)).toLowerCase();
      if (av < bv) {
        return -1 * dir;
      }
      if (av > bv) {
        return 1 * dir;
      }
      return 0;
    });
  },
  _statsSamplesSortIndicator(col) {
    if (this.statsSamplesSortBy !== col) {
      return '';
    }
    return this.statsSamplesSortDir === 'asc' ? ' ▲' : ' ▼';
  },
  async loadStatsSamples(range) {
    const r_arg = (range || this.statsSamplesRange || '90d').toString();
    this.statsSamplesRange = r_arg;
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('statsSamplesRange', r_arg);
      }
    } catch (_) { /* private mode — best-effort persist */
    }
    try {
      const r = await fetch('/api/admin/stats/samples?range=' + encodeURIComponent(r_arg));
      if (!r.ok) {
        return;
      }
      this.statsSamples = await r.json();
    } catch (_) {
    } finally {
      this.statsSamplesLoaded = true;
    }
  },
  async loadStatsIncidents(hours) {
    const h = Number(hours) || this.statsIncidentsHours || 168;
    this.statsIncidentsHours = h;
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('statsIncidentsHours', String(h));
      }
    } catch (_) { /* private mode */
    }
    try {
      const r = await fetch('/api/admin/stats/incidents?hours=' + encodeURIComponent(h));
      if (!r.ok) {
        return;
      }
      this.statsIncidents = await r.json();
    } catch (_) {
    } finally {
      this.statsIncidentsLoaded = true;
    }
  },
  async loadStatsNetwork(hours) {
    const h = Number(hours) || this.statsNetworkHours || 168;
    this.statsNetworkHours = h;
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('statsNetworkHours', String(h));
      }
    } catch (_) { /* private mode */
    }
    try {
      const r = await fetch('/api/admin/stats/network?hours=' + encodeURIComponent(h));
      if (!r.ok) {
        return;
      }
      this.statsNetwork = await r.json();
    } catch (_) {
    } finally {
      this.statsNetworkLoaded = true;
    }
  },
  // Map the Stats → Network range-chip's numeric hours value to the
  // human-readable label the chips display. Used by the burst-rate
  // table heading so "Top hosts by burst rate — last 7d" tracks the
  // operator's range pick.
  statsNetworkRangeLabel() {
    const h = Number(this.statsNetworkHours) || 168;
    if (h === 1) {
      return '1h';
    }
    if (h === 24) {
      return '24h';
    }
    if (h === 168) {
      return '7d';
    }
    if (h === 720) {
      return '30d';
    }
    if (h === 2160) {
      return '90d';
    }
    return h + 'h';
  },

  // Console-pasteable snapshot of stats state — operators paste
  // app().statsDebug()
  // to dump current items / stats / sparks counts in one go,
  // without hunting through the Console for warnings or repeating
  // grep-style queries against the SQLite. Returns the snapshot
  // object (also console.tabled for readability).
  statsDebug() {
    const items = this.items || [];
    const stats = this.stats || {};
    const sparks = this.sparks || {};
    const itemIds = items.map(i => i.id);
    const withStats = itemIds.filter(id => stats[id] && stats[id].has_stats).length;
    const sparkIds = Object.keys(sparks);
    const withSparks = sparkIds.filter(id => Array.isArray(sparks[id]) && sparks[id].length > 0).length;
    const sample = items.slice(0, 3).map(i => ({
      id: i.id, name: i.name, type: i.type, status: i.status,
      has_stats: !!(stats[i.id] && stats[i.id].has_stats),
      cpu: stats[i.id] && stats[i.id].cpu_percent,
      mem_used: stats[i.id] && stats[i.id].mem_usage,
      spark_points: (sparks[i.id] || []).length,
    }));
    const snap = {
      items_total: items.length,
      stats_keys: Object.keys(stats).length,
      items_with_stats_true: withStats,
      sparks_keys: sparkIds.length,
      items_with_sparks: withSparks,
      first_three_items: sample,
    };
    console.table(snap);
    console.log('[statsDebug]', snap);
    return snap;
  },
  // Operator-callable: walks every visible item and reports WHY each
  // one is rendering blank/empty bars. Prints a per-item table to the
  // console with the diagnosis in plain English. Run from DevTools as
  // `omnigrid.whyNoGraphs()` after the page settles.
  whyNoGraphs() {
    const items = this.items || [];
    const rows = [];
    let okCount = 0, missingFromStats = 0, statsButNoFlag = 0,
      fallbackOnly = 0, sparkOnly = 0;
    for (const i of items) {
      const id = i.id;
      const s = this.stats[id];
      const sp = this.sparks[id];
      const sparkCount = Array.isArray(sp) ? sp.length : 0;
      let diagnosis;
      if (!s) {
        diagnosis = 'no entry in this.stats — /api/stats did not include this id';
        missingFromStats++;
      } else if (s._stale && (!s.ts || s.ts === 0)) {
        diagnosis = 'fallback-only (cache seed from stats_samples; live gather has not run yet)';
        fallbackOnly++;
      } else if (!s.has_stats && !s.has_size) {
        diagnosis = 'has_stats=false AND has_size=false (Portainer per-container /stats fetch failed for this container)';
        statsButNoFlag++;
      } else if (!s.has_stats && s.has_size) {
        diagnosis = 'has_stats=false but has_size=true (container offline; size came from inspect)';
        statsButNoFlag++;
      } else {
        diagnosis = 'OK — has_stats=true (bar should render)';
        okCount++;
      }
      if (sparkCount === 0 && diagnosis.startsWith('OK')) {
        diagnosis += ' but spark line empty';
      } else {
        if (sparkCount > 0 && !s) {
          diagnosis += ' (sparks alone — bar fallback to 0)';
          sparkOnly++;
        }
      }
      rows.push({
        id, name: i.name, type: i.type, status: i.status,
        stats_entry: !!s,
        has_stats: !!(s && s.has_stats),
        has_size: !!(s && s.has_size),
        stale: !!(s && s._stale),
        cpu: s && s.cpu_percent,
        mem_used: s && s.mem_usage,
        mem_limit: s && s.mem_limit,
        spark_pts: sparkCount,
        diagnosis,
      });
    }
    const summary = {
      items_total: items.length,
      ok: okCount,
      missing_from_stats: missingFromStats,
      stats_entry_but_no_data: statsButNoFlag,
      fallback_only: fallbackOnly,
      spark_only: sparkOnly,
    };
    console.log('[whyNoGraphs] SUMMARY', summary);
    console.table(rows);
    // Actionable next-step hints based on the dominant failure mode.
    if (missingFromStats === items.length && items.length > 0) {
      console.warn('[whyNoGraphs] EVERY item is missing from this.stats. Either /api/stats returned an empty {stats:{}} OR loadStats() never fired. Check Network tab for /api/stats response body.');
    } else if (statsButNoFlag > okCount) {
      console.warn('[whyNoGraphs] Most entries have has_stats=false. Portainer per-container /stats is failing — check API key scope / agent reachability on the deploy host.');
    } else if (fallbackOnly > okCount) {
      console.warn('[whyNoGraphs] Most entries are fallback-only (seeded from stats_samples, no live gather). _gather_stats() has not yet run since boot. Wait ~30s and retry.');
    } else if (okCount === items.length && items.length > 0) {
      console.log('[whyNoGraphs] All items have valid live stats. If bars still look blank, the issue is template/CSS — inspect the rendered DOM for .stat-bar elements and their --w / --c CSS variables.');
    }
    return summary;
  },
  async loadStats(force = false) {
    try {
      const r = await fetch('/api/stats' + (force ? '?force=true' : ''));
      if (!r.ok) {
        // Surface the failure in the console once per load so an
        // operator can spot a 401 / 502 / 504 without hand-checking
        // the Network tab. Stats bars stay at `—` silently otherwise.
        console.warn('[stats] /api/stats returned', r.status);
        return;
      }
      const d = await r.json();
      this.stats = d.stats || {};
      // Background-refresh indicator. /api/stats now serves the
      // seeded cache instantly + kicks `_gather_stats` in the
      // background; ``stats_refreshing`` is true while the
      // background gather is in flight. Composes with the existing
      // `cacheRefreshing` / `hubProbing` flags so the topbar
      // refresh button pulses the spinner whenever ANY background
      // refresh is running. Auto-clears on the next poll once the
      // background gather lands.
      this._setRefreshingFlag('statsRefreshing', d.stats_refreshing);
      // Swarm agent unhealthy detection — populated by gather_stats
      // when a Swarm node has consecutive bad gather cycles (every
      // task-derived cid on the node returned None). Empty array on
      // healthy fleet (most common case). The SPA banner in Stacks
      // / Hosts views renders when the array is non-empty.
      this.unhealthyAgents = Array.isArray(d.unhealthy_agents) ? d.unhealthy_agents : [];
      // Self-diagnostic — fires when /api/stats came back with
      // ZERO has_stats=true rows AND we have items loaded. That's
      // the signature of "Portainer's per-container /stats endpoint
      // failed for every running container" (most common root
      // cause: API key with restricted scope, Portainer node-agent
      // unreachable, or a network policy blocking the docker-stats
      // RPC). Logged once per noticeably-empty response, NOT every
      // poll, so the console doesn't drown in repetition.
      const ids = Object.keys(this.stats);
      const withStats = ids.filter(id => this.stats[id] && this.stats[id].has_stats).length;
      // Flips true on the FIRST successful `/api/stats` response so
      // the Stacks / Services rows can swap their loading spinner
      // for the resolved status dot. Stays true for the rest of the
      // session — a transient `/api/stats` failure later doesn't
      // re-show the spinner because the existing data is still
      // authoritative.
      this.statsLoaded = true;
      if ((this.items || []).length > 0 && ids.length > 0 && withStats === 0) {
        if (!this._warnedNoStats) {
          this._warnedNoStats = true;
          console.warn(
            '[stats] /api/stats returned ' + ids.length + ' items but ' +
            'has_stats=true on 0 of them. Per-container Docker stats are ' +
            'missing — check Portainer API-key scope / agent reachability. ' +
            'On the deploy host: tail -F the omnigrid container logs and ' +
            'look for `[stats] <cid>: ...` error lines, or hit ' +
            '/api/endpoints/<eid>/docker/containers/<cid>/stats?stream=false ' +
            'directly via curl with the same API key.'
          );
        }
      } else if (withStats > 0) {
        // Live data flowing — clear the once-per-session guard so a
        // future regression re-warns instead of staying silent.
        this._warnedNoStats = false;
      }
      // Compute max image size across all items so the disk bar is
      // normalised against the largest thing on the cluster.
      let m = 1;
      for (const id in this.stats) {
        if (this.stats[id].size_root > m) {
          m = this.stats[id].size_root;
        }
      }
      this._maxSize = m;
    } catch (e) {
      console.warn('[stats] /api/stats fetch failed:', e && e.message);
    }
  },

  // --- Sparklines ----------------------------------------------------
  // Fetched from /api/stats/history in one batched request for every
  // currently-known item id. The backend samples every 5 minutes (see
  // STATS_SAMPLE_INTERVAL in main.py), so polling more often than that
  // is wasted work — we refresh on a 5-minute cadence.
  async loadSparks() {
    const ids = (this.items || []).map(i => i.id).filter(Boolean);
    if (!ids.length) {
      return;
    }
    try {
      const params = new URLSearchParams({item_id: ids.join(','), hours: '24'});
      const r = await fetch('/api/stats/history?' + params.toString());
      if (!r.ok) {
        console.warn('[sparks] /api/stats/history returned', r.status);
        return;
      }
      const d = await r.json();
      this.sparks = d.series || {};
      // Self-diagnostic — fires once when sparks come back empty
      // AND we have items. The most common cause is item-id drift
      // (stats_samples table has rows under historical container/
      // service IDs that don't match current cache item_ids — e.g.
      // after a Portainer migration or a stack redeploy that
      // recreated container IDs). Operator can confirm by running
      // sqlite3 on the DB: items with samples → SELECT DISTINCT
      // item_id FROM stats_samples vs current item_ids.
      const sparkIds = Object.keys(this.sparks);
      const withData = sparkIds.filter(id => Array.isArray(this.sparks[id]) && this.sparks[id].length > 0).length;
      if (ids.length > 0 && withData === 0) {
        if (!this._warnedNoSparks) {
          this._warnedNoSparks = true;
          console.warn(
            '[sparks] /api/stats/history returned ' + sparkIds.length +
            ' keys, ' + withData + ' with data, for ' + ids.length + ' current items. ' +
            'If 0/N, the stats_samples table holds data for OLD item_ids that ' +
            'no longer match current items. Sample current id: ' + ids[0] +
            '. Check: sqlite3 .../omnigrid.db "SELECT DISTINCT item_id FROM stats_samples LIMIT 5".'
          );
        }
      } else if (withData > 0) {
        this._warnedNoSparks = false;
      }
    } catch (e) {
      console.warn('[sparks] /api/stats/history fetch failed:', e && e.message);
    }
  },
  statsFor(item) {
    return (item && this.stats[item.id]) || {cpu_percent: 0, mem_usage: 0, mem_limit: 0, size_root: 0, size_rw: 0, has_stats: false, has_size: false};
  },

  nodeStats(host) {
    // Per-flush memo — see _nodeStatsFlushCache at module scope. The Nodes
    // view reads nodeStats ~15x per node card; this collapses it to one
    // compute per host per flush.
    if (_nodeStatsFlushCache === null) {
      _nodeStatsFlushCache = new Map();
      if (!_nodeStatsFlushScheduled) {
        _nodeStatsFlushScheduled = true;
        queueMicrotask(_clearNodeStatsFlushCache);
      }
    }
    const _cached = _nodeStatsFlushCache.get(host);
    if (_cached !== undefined) {
      return _cached;
    }
    // Mixed-source stats:
    // - CPU + container-memory: summed from per-item Docker stats.
    // - Host disk / host memory / host uptime: node-exporter when
    //   enabled, else falls back to Docker-only or task-based signal.
    // - Docker disk: /system/df totals via Portainer (always available).
    let cpuRaw = 0, memUsage = 0;
    let hasStats = false;
    for (const it of this.itemsForNode(host)) {
      const s = this.statsFor(it);
      if (s.has_stats) {
        cpuRaw += s.cpu_percent;
        memUsage += s.mem_usage;
        hasStats = true;
      }
    }
    const info = this.nodeInfoFor(host);
    const cores = info.cpu_cores || 0;
    const memBytes = info.mem_bytes || 0;
    const dockerDisk = Number.isFinite(info.docker_disk_bytes) ? info.docker_disk_bytes : 0;
    const hostDiskTotal = Number.isFinite(info.host_disk_total) ? info.host_disk_total : 0;
    const hostDiskUsed = Number.isFinite(info.host_disk_used) ? info.host_disk_used : 0;
    const hostMemTotal = Number.isFinite(info.host_mem_total) ? info.host_mem_total : 0;
    const hostMemUsed = Number.isFinite(info.host_mem_used) ? info.host_mem_used : 0;
    // Host-level CPU% straight from the provider (Beszel/Pulse/NE).
    // Lets the CPU bar render even when Portainer's per-container
    // stats haven't been gathered yet (or when the node hosts no
    // containers at all). Falls through to container-derived cpuRaw
    // when the provider didn't supply one.
    const hostCpuRaw = Number.isFinite(info.host_cpu_percent) ? info.host_cpu_percent : 0;
    // Host-stats status — three values:
    // - 'ok'       scrape succeeded (any host_* fields populated)
    // - 'error'    probe attempted but failed (exporter_error set,
    //              OR host-stats is enabled globally but this node
    //              returned nothing)
    // - 'disabled' host_stats_source is 'none' / unset
    // Drives the green/red pill on the node header. The "exporter"
    // word in the variable name is historical — the same signal
    // covers both node-exporter and Beszel now.
    const source = (this.settings && this.settings.host_stats_source)
      || (this.settings && this.settings.node_exporter_enabled ? 'node_exporter' : 'none');
    // CSV of active providers — accepts single legacy values too.
    const sourceSet = new Set(
      (source || '').split(',').map(s => s.trim()).filter(s => s && s !== 'none'),
    );
    const hostStatsEnabled = sourceSet.size > 0;
    let exporterStatus = 'disabled';
    if (info.exporter_error) {
      exporterStatus = 'error';
    } else {
      if (hostStatsEnabled && (hostMemTotal > 0 || Number.isFinite(info.host_boot_ts) || (info.mounts && info.mounts.length))) {
        exporterStatus = 'ok';
      } else {
        if (hostStatsEnabled) {
          exporterStatus = 'error';
        }
      }
    }  // enabled but no data came back
    // Per-node provider hits — backend records which providers
    // actually contributed data for THIS node into ``_providers`` per
    // gather. Falls back to the global active set on hosts that
    // haven't been re-gathered since this field was added (e.g.
    // first boot before a fresh gather lands).
    const providersHit = Array.isArray(info._providers) ? info._providers : [];
    const result = {
      cpuRaw,                        // 0..cores*100 (can exceed 100)
      hostCpuRaw,                    // 0..100 — host-provider CPU%
      hasHostCpu: hostCpuRaw > 0,
      memUsage,                      // bytes — sum of container usages
      memLimit: memBytes,            // NODE RAM capacity
      cores,
      dockerDisk,
      hostDiskTotal, hostDiskUsed,
      hostMemTotal, hostMemUsed,
      hasStats,
      hasSize: dockerDisk > 0,
      hasHostStats: hostDiskTotal > 0 || hostMemTotal > 0,
      exporterStatus,
      exporterError: info.exporter_error || null,
      hostStatsSource: source,             // CSV string, legacy callers
      hostStatsSources: [...sourceSet],     // array form for new callers (GLOBAL)
      nodeProvidersHit: providersHit, // per-node list
    };
    _nodeStatsFlushCache.set(host, result);
    return result;
  },

  // Time-series aggregation for node-level sparklines. Bins samples by
  // rounded timestamp (matching the sampler's cadence) so items with
  // near-identical-but-not-exact timestamps still stack correctly.
  nodeSparkPoints(host, key) {
    // per-flush memo — see _nodeSparkFlushCache. `undefined` = not
    // cached (the builder returns '' or a points string, never undefined).
    if (_nodeSparkFlushCache === null) {
      _nodeSparkFlushCache = new Map();
      if (!_nodeSparkFlushScheduled) {
        _nodeSparkFlushScheduled = true;
        queueMicrotask(_clearNodeSparkFlushCache);
      }
    }
    let _perHost = _nodeSparkFlushCache.get(host);
    if (!_perHost) {
      _perHost = new Map();
      _nodeSparkFlushCache.set(host, _perHost);
    }
    const _hit = _perHost.get(key);
    if (_hit !== undefined) {
      return _hit;
    }
    const _memo = (v) => {
      _perHost.set(key, v);
      return v;
    };
    const items = this.itemsForNode(host);
    if (!items.length) {
      return _memo('');
    }
    const BIN = 300; // seconds — matches STATS_SAMPLE_INTERVAL default
    const byBin = new Map();
    for (const it of items) {
      const rows = this.sparks[it.id];
      if (!rows) {
        continue;
      }
      for (const r of rows) {
        const bin = Math.round((r.ts || 0) / BIN) * BIN;
        const agg = byBin.get(bin) || {ts: bin, cpu: 0, mem_used: 0, mem_limit: 0, size_root: 0};
        agg.cpu += r.cpu || 0;
        agg.mem_used += r.mem_used || 0;
        agg.mem_limit += r.mem_limit || 0;
        agg.size_root += r.size_root || 0;
        byBin.set(bin, agg);
      }
    }
    const sorted = Array.from(byBin.values()).sort((a, b) => a.ts - b.ts);
    if (sorted.length < 2) {
      return _memo('');
    }
    const W = 60, H = 10;
    const vals = sorted.map(r => {
      if (key === 'cpu') {
        return r.cpu;
      }
      if (key === 'mem') {
        return r.mem_limit ? (r.mem_used / r.mem_limit) * 100 : 0;
      }
      // 'disk' → summed image-disk footprint across every item on this
      // node. Auto-rescaled to the window's lo/hi via the shared min-
      // max normalisation below — captures fleet-wide image-bytes
      // drift even when no single item grew much. Backend's stats_samples
      // table gained a `size_root` column; pre-migration deploys return
      // 0 for every row so the auto-rescale flat-zero gate hides the
      // sparkline cleanly.
      if (key === 'disk') {
        return r.size_root || 0;
      }
      return 0;
    });
    // 'disk'-specific early-bail: when no row carries a real
    // size_root value (pre-migration deploys or hosts whose
    // sampler hasn't run since the schema update), every value is
    // 0. Without this gate, the flat-line treatment below would
    // render a misleading flat line at H/2. Returning '' hides
    // the SVG via x-show, keeping the cell clean.
    if (key === 'disk' && vals.every(v => !v)) {
      return _memo('');
    }
    let lo = Infinity, hi = -Infinity;
    for (const v of vals) {
      if (v < lo) {
        lo = v;
      }
      if (v > hi) {
        hi = v;
      }
    }
    // Flat / idle series — center the line in the box rather than
    // pinning it to the bottom edge. Earlier code did
    // `lo = max(0, lo-0.5); hi = lo+1` which mapped `v=0` to `y=H`
    // (the very bottom). With stroke-width=1 the line then drew
    // from y=H-0.5 to y=H+0.5; the bottom half landed OUTSIDE the
    // viewBox and got clipped — visible result was an invisible
    // sparkline (operator's "CPU graph not showing" report on idle
    // nodes). Re-center on the midpoint so flat data lands at y=H/2.
    if (hi - lo < 0.5) {
      const mid = (lo + hi) / 2;
      lo = mid - 1;
      hi = mid + 1;
    }
    // Vertical padding — keep the polyline at least 1 unit clear of
    // the top and bottom edges so the stroke isn't clipped on
    // boundary values (a CPU sample at 0% or 100% would otherwise
    // render half-cropped). Effective drawable height = H - 2*PAD.
    const PAD = 1;
    const drawH = H - 2 * PAD;
    const step = W / (vals.length - 1);
    return _memo(vals.map((v, i) => {
      const x = (i * step).toFixed(1);
      const y = (PAD + (1 - (v - lo) / (hi - lo)) * drawH).toFixed(1);
      return `${x},${y}`;
    }).join(' '));
  },

  nodeSparkClass(host, key) {
    const st = this.nodeStats(host);
    if (!st.hasStats) {
      return 'muted';
    }
    let v;
    if (key === 'cpu') {
      v = this.nodeCpuPercent(host);
    } else {
      if (key === 'disk') {
        // Prefer host disk percent (node-exporter) when available;
        // fall back to the docker-daemon disk percent otherwise.
        v = (typeof this.hostDiskPercent === 'function' && st.hasHostStats)
          ? this.hostDiskPercent(host)
          : (typeof this.nodeDiskPercent === 'function' ? this.nodeDiskPercent(host) : 0);
      } else {
        v = this.nodeMemPercent(host);
      }
    }
    return this.barLevel(v);
  },
  // Pretty-shape the `counters.samples_in_window` block for the
  // dedicated debug panel. Backend returns:
  //   { hours, since_ts, host_snmp_samples: {count, newest_ts,
  //     oldest_ts, median_gap_s, newest_age_s}, host_metrics_samples,
  //     host_snmp_iface_samples, ping_samples, host_net_samples }
  // We project to a list of rows so the template can `x-for` it.
  // Rows whose host doesn't even have ANY sample for that table
  // are still emitted (count=0) so the operator can SEE the
  // missing-data signal — this is the diagnostic.
  samplesWindowRows(win) {
    if (!win || typeof win !== 'object') {
      return [];
    }
    // Friendly labels — the raw table names (`host_snmp_samples`
    // etc.) are correct but not great. The label resolves through
    // i18n so non-en locales translate; falls back to a short form.
    const tables = [
      // Order: NE first (most common signal), then per-provider
      // local tables, then SNMP, then ping. Every entry is a local
      // table now — Beszel was the read-through-only outlier; it
      // gained `host_beszel_samples` so it slots into the same
      // shape the others use.
      ['host_metrics_samples', 'samples_table.label_node_exporter'],
      ['host_beszel_samples', 'samples_table.label_beszel'],
      ['host_pulse_samples', 'samples_table.label_pulse'],
      ['host_webmin_samples', 'samples_table.label_webmin'],
      ['host_snmp_samples', 'samples_table.label_snmp'],
      ['host_snmp_iface_samples', 'samples_table.label_snmp_iface'],
      ['host_net_samples', 'samples_table.label_host_net'],
      ['ping_samples', 'samples_table.label_ping'],
    ];
    // Routes through fmtDate so the operator's Formats preference
    // applies here too — keeps the per-sample timestamp consistent
    // with the rest of the SPA's renders.
    const fmtTs = (ts) => this.fmtDate(ts);
    const fmtAge = (s) => {
      if (s == null) {
        return '—';
      }
      const n = Math.max(0, Math.round(+s || 0));
      if (n < 60) {
        return n + 's';
      }
      if (n < 3600) {
        return Math.round(n / 60) + 'm';
      }
      if (n < 86400) {
        return (n / 3600).toFixed(1) + 'h';
      }
      return (n / 86400).toFixed(1) + 'd';
    };
    const fmtGap = (s) => {
      if (s == null) {
        return '—';
      }
      const n = Math.max(0, Math.round(+s || 0));
      if (n < 60) {
        return n + 's';
      }
      return Math.round(n / 60) + 'm ' + (n % 60) + 's';
    };
    const winHours = +(win.hours || 1);
    // Newest-age warning threshold — flag when the newest sample
    // is OLDER than the window itself (sampler hasn't ticked AT
    // ALL during the selected window — the canonical "chart cut"
    // signal). The threshold is `winHours * 3600`s.
    const ageWarnThreshold = winHours * 3600;
    const out = [];
    for (const [key, labelKey] of tables) {
      const blob = win[key];
      if (!blob || typeof blob !== 'object') {
        continue;
      }
      if (blob._error) {
        out.push({
          table: key,
          label: this.t(labelKey) || key,
          count: 0,
          oldest_str: '—',
          newest_str: '—',
          newest_age_str: '—',
          newest_age_warn: false,
          median_gap_str: this.t('debug_panel.samples_table.error') || ('error: ' + blob._error),
        });
        continue;
      }
      const count = +blob.count || 0;
      const ageS = blob.newest_age_s;
      out.push({
        table: key,
        label: this.t(labelKey) || key,
        count,
        oldest_str: count > 0 ? fmtTs(blob.oldest_ts) : '—',
        newest_str: count > 0 ? fmtTs(blob.newest_ts) : '—',
        newest_age_str: count > 0 ? fmtAge(ageS) : '—',
        newest_age_warn: count > 0 && ageS != null && +ageS > ageWarnThreshold,
        median_gap_str: count >= 2 ? fmtGap(blob.median_gap_s) : '—',
      });
    }
    return out;
  },
};
