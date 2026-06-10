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
// SPA Apps tab + Admin → Apps tab — top-level Apps card grid (cross-host
// aggregate via /api/apps) + catalog template CRUD (Admin → Apps tab,
// reads /api/services/catalog). Per-host chip instances continue to be
// edited via Admin → Hosts (unchanged); this module handles the
// catalog-template lifecycle + the cross-host viewing experience.

// Per-instance uptime-sparkline memo — keyed on the instance's
// `status_history` array REFERENCE (stable across reactive flushes until
// /api/apps reloads), so the SVG points string + uptime % are built ONCE
// per series, not on every Alpine flush (see the project conventions "Template-read
// getters … MUST be flush-memoized"). Module-scope so Alpine reactivity
// never wraps it; WeakMap so a cache entry is GC'd with its history array
// when the app list reloads.
//
// Module-scope state moved to `app-apps-state.js` so every sibling
// app-apps* file observes the SAME cache identity. Pre-split each file
// declared its own copy and the per-flush memos diverged silently
// (orchestration's filteredApps memo ≠ card-file's appsVisibleInstances
// memo even though both read the same source). The shared module owns
// the WeakMaps + `let`-bindings + setter helpers; this file imports
// what it needs.
import {
  _filteredAppsCache,
  _setFilteredAppsCache,
  _filteredAppsCacheKey,
  _setFilteredAppsCacheKey,
  _filteredAppsFlushScheduled,
  _setFilteredAppsFlushScheduled,
  _clearFilteredAppsFlushCache,
  _appsTileRenderLog,
  _ogPerfCount,
  _appsTileQueue,
  _appsTileQueueProcessing,
  _setAppsTileQueueProcessing,
  _appsSparkCache,
  _appsVisibleInstancesCache,
  _appsVisibleInstancesFlushScheduled,
  _setAppsVisibleInstancesFlushScheduled,
  _clearAppsVisibleInstancesFlushCache,
  _anyAppExtrasMatchCache,
  _hostAppsHealthFlushCache,
  _setHostAppsHealthFlushCache,
  _hostAppsHealthFlushScheduled,
  _setHostAppsHealthFlushScheduled,
  _clearHostAppsHealthFlushCache,
} from './app-apps-state.js?v=__APP_VERSION__';

// Per-flush memo for `_buildAppsCustom()`. appsCustomSections() +
// appsCustomUnsectioned() call it ~5x per reactive flush (the section
// x-for + length guards + the unsectioned x-for / count span), and each
// pass re-allocates the entire {sections, unsectioned} model. Build it
// ONCE per flush, serve the same object to all 5 readers, then clear on
// the next microtask so a layout edit / poll reconcile rebuilds. App-
// apps.js-only state, so it lives here (not in the shared app-apps-state
// module). Freeze-safe per caveat-6: both consumers feed x-for sources
// that re-subscribe to filteredApps() every flush.
let _appsCustomBuildCache = null;
let _appsCustomBuildScheduled = false;

// Per-flush memo for `appsHostGroups()` — the by-app grid's x-for source.
// It walks filteredApps(), builds a per-host grouping + a fresh per-app
// descriptor for every instance, and per-group sorts; un-memoized it
// re-ran fully every flush (+ a 2nd call from appResolvePin). Build once
// per flush; queueMicrotask clear rebuilds next flush. Freeze-safe
// (caveat-6: the grid x-for re-subscribes to filteredApps every flush).
let _appsHostGroupsCache = null;
let _appsHostGroupsScheduled = false;

export default {
  // ----------------------------------------------------------------
  // Top-level Apps view — cross-host aggregate.
  // ----------------------------------------------------------------
  async loadAppsList(force = false) {
    // When `force=true` the caller wants a fresh fetch even if a poll
    // is already in-flight (e.g. operator clicks Refresh after editing
    // the catalog). Otherwise the in-flight check coalesces overlapping
    // calls so a fast SSE-driven re-poll doesn't stack dozens of GETs.
    if (this.appsListLoading && !force) {
      return;
    }
    // NOTE: a `force` refresh must NOT blanket-reset `_appsVisibleTiles`
    // / `_appsReadyTiles`. Doing so un-readied EVERY tile, un-mounting
    // every card body (ports / sparklines) back to a skeleton and then
    // re-staggering them — so probing ONE app (which calls
    // loadAppsList(true)) visibly stripped the ports from ALL apps for a
    // beat. Each app is autonomous: an existing tile keeps its mounted
    // body across a data refresh; the in-place reconcile below just
    // updates its fields. New / renamed apps (new group_id) still mount
    // correctly via their own `x-init="_observeAppCard(...)"` + the
    // persistent observer, and removed gids are pruned after the splice
    // below — so the "stale skeleton on rename" case the old reset
    // guarded against is handled without the global tear-down.
    this.appsListLoading = true;
    this.appsListError = '';
    try {
      // `force` (operator Refresh, post-edit reload) bypasses the
      // backend's short-TTL list_apps cache so the operator sees fresh
      // probe state on demand; idle polls hit the cache.
      const r = await fetch('/api/apps' + (force ? '?force=true' : ''));
      if (!r.ok) {
        if (r.status === 401) {
          return;
        }
        throw new Error('HTTP ' + r.status);
      }
      const j = await r.json();
      const incoming = Array.isArray(j.apps) ? j.apps : [];
      // In-place reconcile (per the every-polled-reactive-array rule):
      // mutate existing rows when group_id matches, splice gone ones,
      // push new ones. Apps view binds to chart sparklines + status
      // pulse animations that we don't want to tear down on every poll.
      const byGid = {};
      for (const row of this.appsList) {
        byGid[row.group_id] = row;
      }
      const seenGids = new Set();
      for (const incomingApp of incoming) {
        const gid = incomingApp.group_id;
        if (!gid) {
          continue;
        }
        seenGids.add(gid);
        const existing = byGid[gid];
        if (existing) {
          // Field-by-field overwrite — but reconcile the nested
          // `instances` array IN PLACE (by host_id+service_idx) so the
          // inner x-for + per-port pill loop don't tear down on every
          // poll. Wholesale `existing.instances = incomingApp.instances`
          // re-renders the entire nested grid (visible flicker), which
          // defeats the in-place reconcile this method does for the
          // top-level list.
          for (const k of Object.keys(incomingApp)) {
            if (k === 'instances' && Array.isArray(existing.instances) && Array.isArray(incomingApp.instances)) {
              this._reconcileInstancesInPlace(existing.instances, incomingApp.instances);
            } else {
              existing[k] = incomingApp[k];
            }
          }
        } else {
          this.appsList.push(incomingApp);
        }
      }
      // Splice gone apps.
      for (let i = this.appsList.length - 1; i >= 0; i--) {
        if (!seenGids.has(this.appsList[i].group_id)) {
          this.appsList.splice(i, 1);
        }
      }
      // Prune per-tile render trackers for apps that no longer exist so
      // they don't grow unbounded across refreshes (replaces the hygiene
      // the old blanket force-reset did, WITHOUT un-readying surviving
      // tiles). Surviving tiles keep their ready/visible state → bodies
      // stay mounted (no flash).
      for (const map of [this._appsVisibleTiles, this._appsReadyTiles]) {
        if (!map) {
          continue;
        }
        for (const gid of Object.keys(map)) {
          if (!seenGids.has(gid)) {
            delete map[gid];
          }
        }
      }
      // Re-sort by name (incoming is already sorted, but in-place
      // reconcile doesn't preserve order — sort after).
      this.appsList.sort((a, b) => (a.name || '').toLowerCase()
        .localeCompare((b.name || '').toLowerCase()));
      this.appsListLoaded = true;
      // If the user is already on the custom board (restored from
      // localStorage), kick the server-backed views load now. Single-flight +
      // me.ui_prefs-guarded internally; cheap on repeat calls (loaded-guard
      // returns immediately). The apps grid is already painted above
      // (appsListLoaded = true), so awaiting here only defers this method's
      // resolution on the first custom-board load.
      if (this.appsViewGroupBy === 'custom') {
        await this.ensureAppViewsLoaded();
      }
    } catch (err) {
      this.appsListError = err && err.message ? err.message : String(err);
      // Don't wholesale-replace on error — keep existing rows.
    } finally {
      this.appsListLoading = false;
    }
  },

  // Reconcile a nested per-host instances[] array IN PLACE so the inner
  // x-for + per-port pill loop survive a poll without DOM tear-down.
  // Match instances by host_id+service_idx (update field-by-field, push
  // new, splice gone); within each matched instance reconcile its
  // port_results[] by port number the same way. Same discipline as the
  // top-level appsList reconcile.
  _reconcileInstancesInPlace(existingArr, incomingArr) {
    const keyOf = (inst) => (inst.host_id || '') + ':' + inst.service_idx;
    const byKey = {};
    for (const e of existingArr) {
      byKey[keyOf(e)] = e;
    }
    const seen = new Set();
    for (const inc of incomingArr) {
      const k = keyOf(inc);
      seen.add(k);
      const ex = byKey[k];
      if (!ex) {
        existingArr.push(inc);
        continue;
      }
      for (const kk of Object.keys(inc)) {
        if (kk === 'port_results' && Array.isArray(ex.port_results) && Array.isArray(inc.port_results)) {
          this._reconcilePortResultsInPlace(ex.port_results, inc.port_results);
        } else {
          ex[kk] = inc[kk];
        }
      }
    }
    for (let i = existingArr.length - 1; i >= 0; i--) {
      if (!seen.has(keyOf(existingArr[i]))) {
        existingArr.splice(i, 1);
      }
    }
  },

  // Reconcile a port_results[] array in place by port number.
  _reconcilePortResultsInPlace(existingArr, incomingArr) {
    const byPort = {};
    for (const e of existingArr) {
      byPort[e.port] = e;
    }
    const seen = new Set();
    for (const inc of incomingArr) {
      seen.add(inc.port);
      const ex = byPort[inc.port];
      if (ex) {
        for (const kk of Object.keys(inc)) {
          ex[kk] = inc[kk];
        }
      } else {
        existingArr.push(inc);
      }
    }
    for (let i = existingArr.length - 1; i >= 0; i--) {
      if (!seen.has(existingArr[i].port)) {
        existingArr.splice(i, 1);
      }
    }
  },

  // Filtered apps list — search + status filter applied client-side.
  filteredApps() {
    const q = (this.appsSearchQuery || '').trim().toLowerCase();
    const sf = this.appsStatusFilter || '';
    const ef = !!this.appsExtrasOnly;
    const src = this.appsList || [];
    // Per-flush memo — see _filteredAppsCache comment block at top.
    // Cache key folds (search, statusFilter, source-array ref) so any
    // filter change OR appsList reconcile invalidates cleanly.
    // (The temporary `ogAppsLimit` render cap was removed once the APC
    // UI-freeze was root-caused + fixed — extras are now opt-in, so the
    // heavy panel no longer renders unless ticked, and the page loads
    // correctly with every app rendered.)
    const cacheKey = q + '|' + sf + '|' + (ef ? '1' : '0') + '|' + src.length;
    if (_filteredAppsCache && _filteredAppsCacheKey === cacheKey
      && _filteredAppsCache.__src === src) {
      return _filteredAppsCache;
    }
    let out = src;
    if (ef) {
      // "Extras only" — keep apps whose catalog template has a registered
      // per-app module (extras-capable). appsTemplateSupportsExtras walks
      // window.OG_APPS_EXTENDERS; pass the slug (fallback to the app name).
      out = out.filter(a => this.appsTemplateSupportsExtras(
        ((a.catalog && a.catalog.slug) || '') || (a.name || '')));
    }
    if (sf) {
      // Status filter: keep apps whose rollup status matches AND drill
      // into each so ONLY the matching instances show — so the by-host
      // view (and the per-app instance list) surface just the hosts +
      // tiles in that state, giving a clear "what's degraded / down"
      // view. Shallow-copy so the reactive appsList source isn't
      // mutated; group_id is preserved so keyed x-for still reuses DOM.
      out = out
        .filter(a => a.status === sf)
        .map(a => Object.assign({}, a, {
          instances: Array.isArray(a.instances)
            ? a.instances.filter(i => i.status === sf)
            : a.instances,
        }));
    }
    if (q) {
      out = out.filter(a => {
        if ((a.name || '').toLowerCase().includes(q)) {
          return true;
        }
        const cat = a.catalog || {};
        if ((cat.slug || '').toLowerCase().includes(q)) {
          return true;
        }
        if ((cat.description || '').toLowerCase().includes(q)) {
          return true;
        }
        for (const inst of (a.instances || [])) {
          if ((inst.host_label || '').toLowerCase().includes(q)) {
            return true;
          }
          if ((inst.url || '').toLowerCase().includes(q)) {
            return true;
          }
        }
        return false;
      });
    }
    // Stamp the source reference on the result so the cache key
    // check (above) can verify the result was built from the
    // current appsList — defensive when the search/filter strings
    // hash-collide across two distinct poll snapshots.
    Object.defineProperty(out, '__src', {value: src, enumerable: false});
    _setFilteredAppsCache(out);
    _setFilteredAppsCacheKey(cacheKey);
    if (!_filteredAppsFlushScheduled) {
      _setFilteredAppsFlushScheduled(true);
      queueMicrotask(_clearFilteredAppsFlushCache);
    }
    return out;
  },

  // Aggregate counters for the view header.
  appsCounts() {
    const list = this.appsList || [];
    let up = 0, down = 0, degraded = 0, unknown = 0;
    for (const a of list) {
      if (a.status === 'up') {
        up++;
      } else if (a.status === 'down') {
        down++;
      } else if (a.status === 'degraded') {
        degraded++;
      } else {
        unknown++;
      }
    }
    return {total: list.length, up, down, degraded, unknown};
  },

  // Per-card chip class for the status pill.
  appStatusPillClass(status) {
    if (status === 'up') {
      return 'pill-ok';
    }
    if (status === 'down') {
      return 'pill-error';
    }
    if (status === 'degraded') {
      return 'pill-warning';
    }
    return 'pill-muted';
  },

  // Diagnosis reason for a non-up Apps instance — answers "why is this
  // degraded / down?" at a glance. Prefers a specific failing port's
  // error (multi-port chips), then the chip-level rollup error, then a
  // generic fallback. Probe error strings come straight from the
  // sampler (timeout / ConnectionRefusedError / unexpected status 404 /
  // …) and stay un-translated — they're diagnostic, not UI chrome.
  // Returns '' for up instances so the template can gate on it.
  appsInstanceReason(inst) {
    if (!inst || inst.status === 'up') {
      return '';
    }
    if (inst.status === 'unknown') {
      return this.t('apps.reason_no_probe') || 'No probe result yet';
    }
    const pr = (inst.port_results || []).find((p) => p && !p.alive && p.error);
    if (pr) {
      return this.t('apps.reason_port', {port: pr.port, error: pr.error})
        || ('Port ' + pr.port + ': ' + pr.error);
    }
    const lp = inst.last_probe;
    if (lp && lp.error) {
      return lp.error;
    }
    return this.t('apps.reason_unreachable') || 'Probe failed (no detail)';
  },

  // Latency unit — single i18n point for the "Nms" suffix used across every
  // Apps surface (bare form). Parenthesised form via appsLatencyParen().
  // Keeps the unit out of template-literal concatenation so RTL / non-Latin
  // locales can place / translate it (ms vs мс vs ミリ秒).
  appsLatencyMs(n) {
    // Group thousands per the browser locale ("1,234" / "1.234") and let
    // the i18n format own the space + unit ("{n} ms").
    const num = (typeof n === 'number') ? n.toLocaleString() : n;
    return this.t('common.latency_ms', {n: num}) || (num + ' ms');
  },
  appsLatencyParen(n) {
    const num = (typeof n === 'number') ? n.toLocaleString() : n;
    return this.t('common.latency_paren', {n: num}) || ('(' + num + ' ms)');
  },

  // Per-instance uptime sparkline for the Apps card. Reads the backend-
  // supplied `inst.status_history` ([{ts, up}, ...], oldest->newest) and
  // returns a MEMOIZED descriptor {points, pct, n, allUp}:
  //   points = SVG polyline coords in a 0..48 x 0..12 viewBox — up sits at
  //            the top edge, a down sample drops to the baseline, so one
  //            <polyline> reads as "flat = healthy, dip = outage";
  //   pct    = uptime % over the window; n = sample count;
  //   allUp  = no down samples (drives the green-vs-amber colour class).
  // Returns null when there are < 2 points (caller hides the spark). The
  // result is cached against the status_history array ref so :points +
  // :title + the colour class all read it without rebuilding per flush.
  appsInstanceSpark(inst) {
    const hist = inst && inst.status_history;
    if (!Array.isArray(hist) || hist.length < 2) {
      return null;
    }
    const cached = _appsSparkCache.get(hist);
    if (cached) {
      return cached;
    }
    const width = 48;
    const top = 1;
    const bottom = 11;
    const n = hist.length;
    const stepX = width / (n - 1);
    const coords = [];
    let upCount = 0;
    for (let i = 0; i < n; i++) {
      const up = !!(hist[i] && hist[i].up);
      if (up) {
        upCount++;
      }
      coords.push((i * stepX).toFixed(1) + ',' + (up ? top : bottom));
    }
    const out = {
      points: coords.join(' '),
      pct: Math.round((upCount / n) * 100),
      n: n,
      allUp: upCount === n,
    };
    _appsSparkCache.set(hist, out);
    return out;
  },

  // Host-drawer chip-strip tooltip: "<name> — <status> (<rtt>ms)". The
  // name+status base (incl. its separator) lives in the i18n format string
  // so locales control the separator; rtt is appended via the latency key.
  appsChipTitle(app) {
    const name = (app && (app.name || (app.catalog && app.catalog.name))) || '';
    const status = this.t('apps.status_' + (app && app.status)) || (app && app.status) || '';
    let s = this.t('apps.chip_tooltip', {name, status}) || (name + ' — ' + status);
    const rtt = app && app.last_probe && app.last_probe.rtt_ms;
    if (rtt != null) {
      s += ' ' + this.appsLatencyParen(rtt);
    }
    return s;
  },

  // Chip-strip accessible name — name + status only (the latency is noise in
  // a screen-reader announcement).
  appsChipAriaLabel(app) {
    const name = (app && (app.name || (app.catalog && app.catalog.name))) || '';
    const status = this.t('apps.status_' + (app && app.status)) || (app && app.status) || '';
    return this.t('apps.chip_tooltip', {name, status}) || (name + ' — ' + status);
  },

  // Apps instance host-row tooltip: canonical address, plus the operator
  // label when it differs. Separator lives in the i18n format string.
  appsInstanceHostTitle(inst) {
    const base = (inst && (inst.host_address || inst.host_id)) || '';
    const label = (inst && inst.host_label) || '';
    if (label && label !== base) {
      return this.t('apps.host_tooltip', {host: base, label}) || (base + ' — ' + label);
    }
    return base;
  },

  // Tri-state for a per-port pill: 'up' (alive===true), 'down'
  // (alive===false), 'unknown' (alive null/undefined = PENDING — the
  // port is configured on the chip but the sampler hasn't probed it
  // yet). Drives the pill + status-dot colour class.
  appsPortState(pr) {
    if (!pr) {
      return 'unknown';
    }
    if (pr.alive === true) {
      return 'up';
    }
    if (pr.alive === false) {
      return 'down';
    }
    return 'unknown';
  },

  // Clickable-port URL: when a port is flagged `open_url` (per-port
  // checkbox in the catalog template / instance editor), return
  // <scheme>://<host>:<port> so the pill renders as a link. Works on ANY
  // protocol — the operator ticked the box, so they know the port serves
  // a browsable URL even if it's labelled tcp (e.g. an app on a bare-TCP
  // chip that actually speaks HTTP). Scheme follows the protocol: https
  // for `https`, otherwise http. Empty string ⇒ not clickable.
  appsPortHref(pr, ctx) {
    if (!pr || !pr.open_url || !pr.port) {
      return '';
    }
    const host = ctx && (ctx.host_address || ctx.address || ctx.host || ctx.host_id || ctx.id);
    if (!host) {
      return '';
    }
    const proto = String(pr.protocol || '').toLowerCase();
    const base = (proto === 'https' ? 'https' : 'http') + '://' + host + ':' + pr.port;
    // Append the per-port HTTP path (e.g. Pi-hole's /admin/) so the link
    // lands on the app's real entry point, not the bare host root. Uses the
    // same `probe_path` the HTTP health probe hits; normalise to a leading
    // slash. A bare '/' adds nothing, so skip it. BUT health-check endpoints
    // (/ping, /api/v1/info, /healthz, …) are NOT user-facing pages — the
    // probe keeps hitting them, but the clickable link drops them so it
    // opens the app's UI root instead of a JSON/health response.
    let path = String(pr.probe_path || '').trim();
    if (path && path !== '/' && !this._isHealthCheckPath(path)) {
      if (path[0] !== '/') {
        path = '/' + path;
      }
      return base + path;
    }
    return base;
  },

  // True when a probe_path is a health/status endpoint (not a navigable
  // page) — so appsPortHref drops it from the clickable URL while the
  // probe still hits it. Matches the common health paths + the `/api/vN/…`
  // info/health family. Trailing slashes are ignored.
  _isHealthCheckPath(path) {
    const p = String(path || '').trim().toLowerCase().replace(/\/+$/, '');
    if (!p) {
      return false;
    }
    const exact = [
      '/ping', '/health', '/healthz', '/healthcheck', '/api/health', '/api/healthz',
      '/status', '/-/healthy', '/-/ready', '/livez', '/readyz', '/health/ready',
      '/health/live', '/health/ready', '/health/started',
      '/metrics', '/-/metrics',
    ];
    if (exact.includes(p)) {
      return true;
    }
    // /api/v1/info (NetData), /api/v2/info, /api/v1/health, etc.
    if (/^\/api\/v\d+\/(?:info|health|status|ready)$/.test(p)) {
      return true;
    }
    // Prometheus / k8s style: /-/health, /-/health/live, /-/health/ready,
    // /-/live, /-/started, /healthz/live, etc.
    return /^\/-\/health(?:\/(?:live|ready|started))?$/.test(p)
      || /^\/-\/(?:live|started)$/.test(p)
      || /^\/healthz?\/(?:live|ready|started)$/.test(p);
  },

  // ---- Apps-view per-app host-list cap (Show all / Show less) --------
  // An app pinned to many hosts would make its Apps-view card grow very
  // tall and break the grid's uniform rows. Cap the rendered instance
  // list at APPS_INSTANCES_COLLAPSED_LIMIT until the operator expands it.
  _appsInstancesLimit() {
    // Cap visible per-host instances at 3 before the "Show all (N)"
    // toggle. A card pinned to many hosts (e.g. node_exporter on 50+
    // boxes) would otherwise grow tall + break the Apps grid's
    // uniform row heights. Was 6, then 4, now 3 per operator-flagged
    // "decrease the height of app cards with a lot of hosts, display
    // only 3 then show more". Operator clicks "Show all (N)" to
    // expand; per-app expanded state is persisted in
    // `appsInstancesExpanded[group_id]` so a poll-refresh doesn't
    // collapse what the operator just opened.
    return 3;
  },
  // Lazy-render observer wiring. Each app card calls
  // `_observeAppCard($el, app.group_id)` in `x-init`; the lazy
  // observer flips `_appsVisibleTiles[group_id] = true` on first
  // intersection. The heavy body (instance list, port pills,
  // per-app extras) is gated on `appsCardVisible(group_id)` so
  // off-screen tiles cost ZERO render work -- crucial on fleets
  // where the Apps grid is dozens of tiles tall and Chrome
  // previously hung trying to render every tile's chip list +
  // per-app extras + Speedtest fetches simultaneously at page-
  // load. Per-tile mount timing is recorded on the inspectable
  // `_appsTileRenderLog` object for devtools-side tracing.
  // First-stage gate: tile is on-screen so the shimmer skeleton +
  // header should mount. The heavy body still waits for the queue
  // processor to flip `_appsReadyTiles[gid]` (see `appsCardReady`).
  appsCardVisible(groupId) {
    return !!(groupId && this._appsVisibleTiles && this._appsVisibleTiles[groupId]);
  },
  // Second-stage gate: the staggered-render queue picked this tile
  // and granted it a paint slot. Template uses this to swap the
  // shimmer skeleton for the real instance list / port pills /
  // sparklines / per-app extras. See `_processAppsTileQueue` for the
  // RAF cadence (one tile per animation frame). Mount duration for
  // each tile is recorded on `_appsTileRenderLog[gid].mount_ms`.
  appsCardReady(groupId) {
    return !!(groupId && this._appsReadyTiles && this._appsReadyTiles[groupId]);
  },
  _observeAppCard(el, groupId) {
    if (!el || !groupId) {
      return;
    }
    // Already visible -- short-circuit before allocating the observer.
    if (this._appsVisibleTiles && this._appsVisibleTiles[groupId]) {
      return;
    }
    if (!this._appsCardObserver) {
      // Defer the actual observe() call ONE microtask so x-init
      // runs across every tile BEFORE the observer starts firing
      // callbacks (avoids the cold-start "everything visible at
      // once" thundering herd).
      this._appsCardObserver = new IntersectionObserver((entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) {
            continue;
          }
          const gid = e.target.getAttribute('data-apps-group-id');
          if (!gid || (this._appsVisibleTiles && this._appsVisibleTiles[gid])) {
            continue;
          }
          if (!this._appsVisibleTiles) {
            this._appsVisibleTiles = {};
          }
          const t0 = performance.now();
          // Reactive write -- triggers exactly one re-render for
          // THIS group_id's gated bindings (per-tile, not fleet-wide).
          // The flip mounts the SKELETON; the body still waits for
          // the stagger queue.
          this._appsVisibleTiles[gid] = true;
          _appsTileRenderLog[gid] = {first_seen_ms: t0, mount_ms: 0};
          // Enqueue body-mount on the staggered RAF queue so
          // N simultaneously-becoming-visible tiles don't all build
          // their chip / port / sparkline subtrees in the same
          // Alpine flush (the page-hang regression class).
          this._enqueueAppsTileMount(gid);
          // Unobserve so the same tile doesn't re-fire on every
          // intersection event (the gate is one-way: once visible,
          // always rendered until the next loadAppsList(force)).
          this._appsCardObserver.unobserve(e.target);
        }
      }, {
        // Pre-render 200px above + below the viewport so scrolling
        // doesn't reveal an unrendered tile mid-scroll. Same shape
        // as the hosts-grid `_hostRowObserver`.
        rootMargin: '200px 0px 200px 0px',
        threshold: 0,
      });
    }
    this._appsCardObserver.observe(el);
  },
  // Push a tile onto the FIFO render queue and kick off the
  // processor if it's idle. Idempotent — re-enqueueing a tile
  // already queued OR already ready is a no-op.
  _enqueueAppsTileMount(groupId) {
    if (!groupId || (this._appsReadyTiles && this._appsReadyTiles[groupId])) {
      return;
    }
    if (_appsTileQueue.indexOf(groupId) !== -1) {
      return;
    }
    _appsTileQueue.push(groupId);
    if (!_appsTileQueueProcessing) {
      _setAppsTileQueueProcessing(true);
      // setTimeout(..., 0) NOT requestAnimationFrame — the first
      // tile's reactive flush can monopolise the main thread long
      // enough that the browser DEFERS rAF callbacks (rAF only fires
      // before a paint, and Alpine's reactive flush blocks paint).
      // Symptom: first tile renders, rest stuck on skeleton because
      // the next rAF never fires. setTimeout(0) is task-queue-paced
      // and fires regardless of paint cadence, so the queue drains
      // even when the page is actively rendering.
      const self = this;
      setTimeout(() => self._processAppsTileQueue(), 0);
    }
  },
  // Process one tile per macrotask tick: pop, flip the ready flag
  // (mounts the heavy body via Alpine's per-key reactivity), and
  // record the mount duration on `_appsTileRenderLog`. The
  // setTimeout-per-tile cadence lets the browser drain its render
  // queue between mounts — the page-hang symptom (hangs after tile
  // headers paint) was caused by every visible tile's body mounting
  // in ONE Alpine flush.
  _processAppsTileQueue() {
    if (_appsTileQueue.length === 0) {
      _setAppsTileQueueProcessing(false);
      return;
    }
    // Ready a BATCH of tiles per macrotask tick (perf finding 2) instead of
    // exactly one — a screenful of cards finishes ~batch-factor faster while
    // STILL yielding to the browser between batches (the anti-hang property
    // is "never build ALL N bodies in one synchronous flush", NOT "exactly
    // one per tick"). Batch size is the operator tunable
    // `tuning_apps_tile_render_batch` (default 4), delivered via
    // /api/me's client_config; the `|| 4` fallback covers the brief window
    // before /api/me hydrates. Stale gids (tile torn down by a poll-reconcile
    // between enqueue + process) are skipped WITHOUT consuming the batch
    // budget so a stale burst still drains promptly.
    let batch = 4;
    try {
      const b = this.me && this.me.client_config && this.me.client_config.apps_tile_render_batch;
      if (Number.isFinite(b) && b >= 1) {
        batch = b;
      }
    } catch (_e) { /* keep default */
    }
    let processed = 0;
    while (_appsTileQueue.length > 0 && processed < batch) {
      const gid = _appsTileQueue.shift();
      const app = (this.appsList || []).find(a => a && a.group_id === gid);
      if (!app) {
        continue;  // stale gid — no real work, doesn't count against the batch
      }
      const t0 = performance.now();
      try {
        if (!this._appsReadyTiles) {
          this._appsReadyTiles = {};
        }
        // Reactive write — flips this tile's body gate (Alpine's per-key
        // reactivity re-renders ONLY this card's body block).
        this._appsReadyTiles[gid] = true;
      } catch (_e) {
        // Mount flip failed — skip this tile so one bad body can't stall
        // the queue; the rest of the batch continues.
      }
      const took = Math.round(performance.now() - t0);
      if (_appsTileRenderLog[gid]) {
        _appsTileRenderLog[gid].mount_ms = took;
      }
      processed++;
    }
    // Schedule the next batch on the next macrotask tick. setTimeout (not
    // rAF) so the queue advances independent of paint cadence — critical
    // when the batch's reactive flush monopolises the main thread + would
    // otherwise defer rAF.
    if (_appsTileQueue.length > 0) {
      const self = this;
      setTimeout(() => self._processAppsTileQueue(), 0);
    } else {
      _setAppsTileQueueProcessing(false);
    }
  },
  appsVisibleInstances(app) {
    if (!app) {
      return [];
    }
    // Per-flush WeakMap memo — called TWICE per card (standard chip
    // list + per-app extras x-for). See _appsVisibleInstancesCache
    // comment block at top.
    const expanded = !!(this.appsInstancesExpanded
      && this.appsInstancesExpanded[app.group_id]);
    const cached = _appsVisibleInstancesCache.get(app);
    if (cached && cached.expanded === expanded
      && cached.src === app.instances) {
      return cached.result;
    }
    const all = Array.isArray(app.instances) ? app.instances : [];
    const result = expanded ? all : all.slice(0, this._appsInstancesLimit());
    _appsVisibleInstancesCache.set(app, {expanded, src: app.instances, result});
    if (!_appsVisibleInstancesFlushScheduled) {
      _setAppsVisibleInstancesFlushScheduled(true);
      queueMicrotask(_clearAppsVisibleInstancesFlushCache);
    }
    return result;
  },
  // True when at least one per-app extras partial matches the app —
  // gates the per-app-extras `<template x-for>` so non-matching apps
  // skip the iteration entirely (saves N empty wrapper divs per
  // non-matching app card). Memoized per-flush via WeakMap so the
  // registry walk is one-shot per app per flush regardless of how
  // many bindings consult it.
  anyAppExtrasMatch(app) {
    if (!app) {
      return false;
    }
    if (_anyAppExtrasMatchCache.has(app)) {
      return _anyAppExtrasMatchCache.get(app);
    }
    const ext = (window.OG_APPS_EXTENDERS || []);
    const cat = app.catalog || {};
    const slug = String(cat.slug || '').trim().toLowerCase();
    const name = String(app.name || '').toLowerCase();
    let matched = false;
    for (const e of ext) {
      if (!e || !Array.isArray(e.slugs)) {
        continue;
      }
      for (const s of e.slugs) {
        const needle = String(s).toLowerCase();
        // Exact slug wins; loose name-substring only as a de-linked fallback
        // (no slug). Mirrors the live copy in app-apps-card.js (which wins via
        // the merge order) — kept in sync to avoid drift.
        if (slug === needle || (!slug && needle && name.indexOf(needle) !== -1)) {
          matched = true;
          break;
        }
      }
      if (matched) {
        break;
      }
    }
    _anyAppExtrasMatchCache.set(app, matched);
    return matched;
  },
  appsInstancesCollapsible(app) {
    const all = (app && Array.isArray(app.instances)) ? app.instances : [];
    return all.length > this._appsInstancesLimit();
  },
  appsInstancesHiddenCount(app) {
    const all = (app && Array.isArray(app.instances)) ? app.instances : [];
    return Math.max(0, all.length - this._appsInstancesLimit());
  },
  toggleAppsInstances(app) {
    if (!app || !app.group_id) {
      return;
    }
    if (!this.appsInstancesExpanded) {
      this.appsInstancesExpanded = {};
    }
    this.appsInstancesExpanded[app.group_id] = !this.appsInstancesExpanded[app.group_id];
  },

  // Aggregate app-health summary for a host's pinned apps — drives the
  // Hosts-view row "N apps" badge (count) AND its colour (aggregate
  // health). Returns {total, up, down, unknown, state} where state is
  // '' (no apps) / 'warn' (>=1 app down) / 'ok' (all up; pending/unknown
  // apps don't count as a failure). DELIBERATELY a separate signal from
  // the host's reachability status dot: amber on that dot means
  // 'paused', so app-degraded gets its own badge rather than overloading
  // the load-bearing 6-value status enum. Derived purely from h.apps
  // (already reconciled in place), so the badge updates with the row.
  hostAppsHealth(h) {
    const _key = (h && h.id) || null;
    if (_key !== null) {
      if (_hostAppsHealthFlushCache === null) {
        _setHostAppsHealthFlushCache(new Map());
        if (!_hostAppsHealthFlushScheduled) {
          _setHostAppsHealthFlushScheduled(true);
          queueMicrotask(_clearHostAppsHealthFlushCache);
        }
      }
      const _hit = _hostAppsHealthFlushCache.get(_key);
      if (_hit !== undefined) {
        return _hit;
      }
      const _res = this._hostAppsHealthCompute(h);
      _hostAppsHealthFlushCache.set(_key, _res);
      return _res;
    }
    return this._hostAppsHealthCompute(h);
  },
  // Uncached compute behind the per-flush memo above.
  // Tracks `degraded` as a first-class counter (was silently lumped into
  // `unknown`, which hid the count from the host-row apps pill); state is
  // 'warn' when any apps are down OR degraded so the pill flips amber on
  // partial-outage too. Operators reading the pill see the breakdown via
  // the new degraded/down chips beside the total count.
  _hostAppsHealthCompute(h) {
    const apps = (h && Array.isArray(h.apps)) ? h.apps : [];
    const total = apps.length;
    if (!total) {
      return {total: 0, up: 0, down: 0, degraded: 0, unknown: 0, state: ''};
    }
    let up = 0;
    let down = 0;
    let degraded = 0;
    let unknown = 0;
    for (const a of apps) {
      const s = a && a.status;
      if (s === 'up') {
        up += 1;
      } else if (s === 'down') {
        down += 1;
      } else if (s === 'degraded') {
        degraded += 1;
      } else {
        unknown += 1;
      }
    }
    return {
      total,
      up,
      down,
      degraded,
      unknown,
      state: (down > 0 || degraded > 0) ? 'warn' : 'ok',
    };
  },

  // Per-port pill tooltip: "<status> (<rtt>ms) — <error>" (rtt + error both
  // optional). status + the error separator route through i18n; the error
  // text itself is the un-translated probe diagnostic from the sampler.
  // A pending (configured-but-unprobed) port reads "pending".
  appsPortTitle(pr) {
    if (!pr) {
      return '';
    }
    const state = this.appsPortState(pr);
    let s;
    if (state === 'up') {
      s = this.t('apps.status_up') || 'up';
    } else if (state === 'down') {
      s = this.t('apps.status_down') || 'down';
    } else {
      s = this.t('apps.port_pending') || 'pending';
    }
    if (pr.rtt_ms != null) {
      s += ' ' + this.appsLatencyParen(pr.rtt_ms);
    }
    if (pr.error) {
      s += this.t('apps.error_suffix', {error: pr.error}) || (' — ' + pr.error);
    }
    return s;
  },

  // ----------------------------------------------------------------
  // Apps detail / debug drawer — mirrors the host drawer. Clicking an
  // app card opens drawerApp; each instance has a "Show debug" panel
  // that lazy-fetches /api/services/{host}/{idx}/debug (the resolved
  // probe target, chip + catalog config, latest per-port outcomes) so
  // the operator can see WHY an instance on a host isn't reporting.
  // ----------------------------------------------------------------
  drawerApp: null,
  appDebug: {},      // { "<host_id>:<idx>": { loading, error, data } }
  appDebugOpen: {},  // { "<host_id>:<idx>": bool }

  appInstanceKey(inst) {
    return (inst && (inst.host_id + ':' + inst.service_idx)) || '';
  },

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

  setAppsViewGroupBy(mode) {
    if (!['app', 'host', 'custom'].includes(mode)) {
      return;
    }
    this.appsViewGroupBy = mode;
    try {
      localStorage.setItem('appsViewGroupBy', mode);
    } catch (_) {
      // private mode — in-memory only
    }
    // Custom dashboards are server-backed (shareable) — they load lazily when
    // the custom board mounts: the loading pane's x-effect AND loadAppsList
    // both call ensureAppViewsLoaded (single-flight + me.ui_prefs-guarded), so
    // no explicit trigger is needed here.
  },

  // -------------------------------------------------------------------
  // Custom layout (Apps Phase 1) — Homarr-style board where the user
  // drags app cards into their own collapsible sections. The layout
  // (section list + per-section ordered group_id lists + collapse
  // state) persists PER-USER in `users.ui_prefs.apps_custom_layout` via
  // the existing /api/me/ui-prefs channel (same as datetime_format /
  // ai_conversation) — NO new backend. App-tile stable key is the
  // app's `group_id` (the catalog-template grouping that the 'By app'
  // view already keys cards on). Apps not placed in any section render
  // in a trailing "Unsectioned" bucket; newly-pinned apps land there
  // automatically until the user drags them into a section.
  // -------------------------------------------------------------------
  appsCustomLayout: null,  // hydrated lazily from me.ui_prefs — a live REFERENCE
                           // to the ACTIVE view's layout (see appsCustomViews)
  // Named custom views collection: { active_id, views: [{id, name, layout}] }.
  // Hydrated by _hydrateAppsCustomViews() (migrates the legacy single
  // apps_custom_layout into one view on first load). The user can keep several
  // named dashboards + switch between them; appsCustomLayout always points at
  // the active view's layout so every existing board mutator keeps working.
  appsCustomViews: null,
  // True while switching custom dashboards via the picker — drives a
  // loading indicator. Some dashboards are heavy (many tiles) + take a
  // couple seconds to rebuild + mount, so the switch yields to paint the
  // spinner first, then clears it after the new board's first paint.
  appsViewSwitching: false,
  // Bound to the rename input in the view-picker (null = not renaming).
  appsViewRenaming: null,
  // Unified "Dashboard settings" modal — rename + share in ONE dialog
  // (replaces the two old SweetAlert popups). `choice` is the 3-way sharing
  // selector value: 'private' | 'public_readonly' | 'public_editable'.
  appsViewModal: {
    open: false, id: '', name: '', choice: 'private',
    orig_name: '', orig_choice: 'private', can_manage: false, saving: false,
  },
  // Views now live server-side in the `app_views` table (so a view can be
  // shared public across users), loaded once via GET /api/apps/views. These
  // gate the custom board until the collection has landed.
  appsViewsLoaded: false,   // true once the server collection is adopted
  appsViewsLoadError: '',   // non-empty → load/migrate failed
  _appViewsLoading: false,  // single-flight guard for ensureAppViewsLoaded

  // Ordered widget kinds the user can drop into a section. Derived from
  // the per-widget registry (static/js/widgets/_registry.js stamps
  // `window.OG_WIDGETS.kinds` in module order) — adding a kind is a new
  // module + one `_modules` entry there, no edit here. `moon` is
  // provider-dependent (only meaningful when WeatherAPI.com is the active
  // weather provider; the tile's empty state hints "switch provider"
  // otherwise).
  get appsWidgetKinds() {
    return (window.OG_WIDGETS && window.OG_WIDGETS.kinds) || [];
  },
  // Per-kind registry record lookup (decorationIcon / supportsRefresh /
  // available / hasData / freshnessObj / refresh). Returns null for an
  // unknown kind. The widget analogue of the per-app extender lookup.
  _widgetRec(kind) {
    const reg = window.OG_WIDGETS;
    return (reg && reg.byKind && reg.byKind[kind]) || null;
  },

  // Reactive ms timestamp driving the clock widget's live time.
  // Ticked every second by an Apps-view-gated interval armed in
  // init() (see app.js). Declared here so Alpine tracks it as a
  // reactive dependency — `_clockDateForItem` reads it so the clock
  // re-renders each tick. Must NOT reuse the drawer-scoped
  // `hostHistoryNow` (that ticker stops when no drawer is open, which
  // froze the displayed time while the CSS colon kept pulsing).
  appsClockNow: 0,

  // Per-(chip, skill) drawer skill state, declared here so Alpine makes
  // them DEEPLY reactive from init. Lazy-creating them inside a method
  // (`this._appSkillResult = this._appSkillResult || {}`) didn't reliably
  // make nested mutations (e.g. a result's `followup_busy`) re-render the
  // bound `:disabled` — which left the Seerr "Request" follow-up button
  // stuck (un-clickable) after the first tap. Declaring them as data
  // fields guarantees deep reactivity for every mutation path.
  _appSkillResult: {},
  _appSkillBusy: {},

  // Prayer-times widget state. `prayer` holds the latest
  // /api/prayer-times response ({timings, prayers, next, hijri, ...});
  // `_prayerFetchedAt` gates the in-process refresh (10-min window like
  // public_ip). Location comes from the logged-in user's existing
  // user location (userLat/Lon) — no separate prayer
  // location pref. The live countdown reads the shared 1s `appsClockNow`
  // tick so it ticks down without a dedicated timer.
  prayer: null,
  _prayerFetchedAt: 0,

  // i18n label for a widget kind (picker + tile heading).
  appsWidgetLabel(kind) {
    return this.t('apps.custom.widget_' + kind) || kind;
  },
  // Sprite-id resolver for the widget tile's background-icon
  // decoration. Drives the single `<use>` inside the decoration
  // `<svg>` (we used to have one `<template x-if>` per kind but
  // that triggers Alpine cloneNode errors because <template>
  // inside <svg> lives in the SVG namespace and has no .content
  // property — driving via this helper avoids the issue).
  appsWidgetDecorationIcon(item) {
    const kind = (item && item.widget) || '';
    const rec = this._widgetRec(kind);
    if (rec && typeof rec.decorationIcon === 'function') {
      return rec.decorationIcon.call(this, this);
    }
    // Unknown kind — fall back to a neutral icon rather than an
    // empty fragment (which would produce a broken sprite ref).
    return 'icon-clock';
  },

  // ---- *arr release-calendar widget (arr_calendar) ----------------------
  // Homarr-style month grid of upcoming movie / series / album / book
  // releases from the configured Radarr / Sonarr / Lidarr / Readarr
  // instances. Data: GET /api/apps/arr-calendar (per-month, cached). The
  // widget is gated whole-cloth on me.client_config.arr_calendar_available
  // (no *arr configured → hidden from the picker + a configure empty-state),
  // and each category only appears when its service contributed rows.
  arrCalendar: null,        // latest /api/apps/arr-calendar response
  _arrCalFetchedAt: 0,      // ms — local receive stamp (freshness backstop)
  _arrCalFetching: '',      // YM currently in flight (per-month de-dup guard)
  _arrCalMonthCache: {},    // { 'YYYY-MM': { ts, data } } — 10-min per-month cache
  arrCalViewYM: '',         // displayed month 'YYYY-MM' ('' → current month)
  arrCalOpenDay: '',        // 'YYYY-MM-DD' of the pinned day popover ('' = none)
  _arrCalGridMemo: null,    // { key, val } — memoised 6×7 grid
  _arrCalWeekdaysMemo: null,

  // Widget kinds offered in the Add-widget picker — drops any kind whose
  // registry record reports `available(c) === false` (e.g. 'arr_calendar'
  // when no *arr service is configured) so it can't be added.
  appsAvailableWidgetKinds() {
    const all = this.appsWidgetKinds || [];
    return all.filter((k) => {
      const rec = this._widgetRec(k);
      if (rec && typeof rec.available === 'function') {
        return rec.available.call(this, this);
      }
      return true;
    });
  },
  arrCalHoverDay: '',       // 'YYYY-MM-DD' hovered (desktop preview, transient)

  // Weather-condition → sprite icon-id mapper. Matches the backend's
  // `weather.condition` string against common WMO-mapped phrases the
  // /api/weather endpoint emits. Falls back to the generic cloud icon
  // so an unrecognised condition still renders SOMETHING brand-
  // appropriate. Used by the apps-widget-tile.html weather branch.
  appsWeatherIconId(condition) {
    const c = String(condition || '').toLowerCase();
    if (!c) {
      return 'icon-weather-cloud';
    }
    if (c.includes('thunder')) {
      return 'icon-weather-thunder';
    }
    if (c.includes('snow') || c.includes('flurr') || c.includes('blizzard') || c.includes('ice')) {
      return 'icon-weather-snow';
    }
    if (c.includes('rain') || c.includes('drizzle') || c.includes('shower')) {
      return 'icon-weather-rain';
    }
    if (c.includes('fog') || c.includes('mist') || c.includes('haze')) {
      return 'icon-weather-fog';
    }
    if (c.includes('partly') || c.includes('partial')) {
      return 'icon-weather-partly-cloudy';
    }
    if (c.includes('clear') || c.includes('sunny') || c.includes('sun')) {
      return 'icon-theme-sun';
    }
    if (c.includes('cloud')) {
      return 'icon-weather-cloud';
    }
    return 'icon-weather-cloud';
  },

  // Fire-and-forget per-card weather fetch. Called from the widget
  // tile's `x-init` (next to the existing `_ensurePublicIp` hook) so
  // every override-mode card kicks its own fetch on first paint. 10-
  // minute cache window (same as `_ensurePublicIp`). Silent failure
  // leaves the cache empty — the card renders the empty state.
  // ----------------------------------------------------------------
  // Prayer Times widget
  // ----------------------------------------------------------------
  // One-shot fetch on first tile mount (x-init) + on the Apps-view
  // refresh path. Location = the logged-in user's existing weather
  // location (userLat/Lon) so there's no separate prayer
  // location pref; falls back to the operator's Admin default when the
  // user has no saved location (the endpoint resolves that). Gated on
  // the master toggle so a disabled feature never hits the network.
  // 10-min cache window matching the other self-fetching widgets; the
  // live countdown re-derives from `appsClockNow` so it ticks without a
  // re-fetch.
  _ensurePrayerTimes(force = false) {
    const cc = (this.me && this.me.client_config) || {};
    if (cc.prayer_times_enabled === false) {
      // Master toggle off — surface the disabled empty-state, no fetch.
      if (!this.prayer) {
        this.prayer = {configured: false};
      }
      return;
    }
    const now = Date.now();
    // Refetch when the cached next-prayer epoch has already passed so the
    // hero advances to the following prayer (the client countdown alone
    // can't roll the `next` pointer — that lives in the response).
    const nextAt = this.prayer && this.prayer.next && this.prayer.next.at_ts;
    const nextPassed = nextAt && (nextAt * 1000) < now;
    if (!force && !nextPassed && this.prayer && this.prayer.configured !== false
      && (now - this._prayerFetchedAt) < 10 * 60 * 1000) {
      return;
    }
    if (this._prayerFetching) {
      return;
    }
    this._prayerFetching = true;
    const lat = this.userLat;
    const lon = this.userLon;
    const params = new URLSearchParams();
    if (lat != null && lon != null) {
      params.set('lat', String(lat));
      params.set('lon', String(lon));
      if (this.userLabel) {
        params.set('label', this.userLabel);
      }
    }
    if (force) {
      params.set('force', '1');
    }
    const qs = params.toString();
    // Return the promise so the AI palette can `await` a completed fetch
    // before building its context (so prayer questions answer in the
    // same turn). The widget x-init path ignores the return value.
    return fetch('/api/prayer-times' + (qs ? ('?' + qs) : ''))
      .then(r => (r.ok ? r.json() : null))
      .then((fresh) => {
        if (!fresh) {
          return;
        }
        // In-place reconcile (anti-flicker — same pattern as
        // loadHeaderWeather / _ensurePublicIp): keep the bound subtree
        // mounted, just update fields.
        const haveCur = this.prayer && typeof this.prayer === 'object';
        if (haveCur && typeof fresh === 'object') {
          Object.keys(this.prayer).forEach((k) => {
            if (!(k in fresh)) {
              delete this.prayer[k];
            }
          });
          Object.assign(this.prayer, fresh);
        } else {
          this.prayer = fresh;
        }
        this._prayerFetchedAt = now;
      })
      .catch(() => { /* silent — tile shows the no-data empty state */
      })
      .finally(() => {
        this._prayerFetching = false;
      });
  },
  // Explicit refresh button on the tile.
  refreshPrayerTimes() {
    this._ensurePrayerTimes(true);
  },

  // ----- Per-app extension hooks -------------------------------
  // Generic dispatchers that walk `window.OG_APPS_EXTENDERS`
  // (registered by each per-app SPA module under
  // `static/js/apps/<slug>.js`) to decide per-app variations
  // (card span, api_key support, extras-panel visibility).
  //
  // Resolve the grid-column span for one app card. Default = 1.
  // Per-app modules can request a wider span via the registered
  // ``cardSpan`` extender (see `static/js/apps/*.js`). The first
  // matching extender that returns >1 wins; absent extenders
  // fall through to default 1.
  appsCardSpan(app) {
    const ext = (window.OG_APPS_EXTENDERS || []);
    for (const e of ext) {
      if (e && typeof e.cardSpan === 'function') {
        const span = e.cardSpan(app);
        if (typeof span === 'number' && span > 1) {
          return span;
        }
      }
    }
    return 1;
  },
  // Whether a given app TEMPLATE supports an extras panel — drives
  // the visibility of the per-template + per-instance "Show extras"
  // checkboxes in the Admin → Apps editors. Registry-driven via
  // `window.OG_APPS_EXTENDERS` — any per-app module registered in
  // `static/js/apps/*.js` is treated as extras-capable. The
  // argument is the editor's current slug (catalog `slug` field)
  // OR the chip's catalog_name / app name; substring match against
  // each extender's `slugs` array.
  appsTemplateSupportsExtras(slugOrName) {
    const s = String(slugOrName || '').toLowerCase();
    if (!s) {
      return false;
    }
    const ext = (window.OG_APPS_EXTENDERS || []);
    for (const e of ext) {
      if (e && Array.isArray(e.slugs)) {
        for (const slug of e.slugs) {
          if (s.indexOf(String(slug).toLowerCase()) !== -1) {
            return true;
          }
        }
      }
    }
    return false;
  },
  // Whether a given app TEMPLATE AGGREGATES its extras across instances —
  // fleet apps (Pi-hole / AdGuard) render ONE combined card for every
  // instance, so a single "Show extras" toggle governs them ALL. Registry-
  // driven: the per-app extender sets `appLevelExtras: true`. Drives the
  // Admin → Apps editor hint that toggling extras affects EVERY instance (vs.
  // per-instance for non-aggregate apps). Same slug-substring match as
  // appsTemplateSupportsExtras.
  appsTemplateAggregatesExtras(slugOrName) {
    const s = String(slugOrName || '').toLowerCase();
    if (!s) {
      return false;
    }
    const ext = (window.OG_APPS_EXTENDERS || []);
    for (const e of ext) {
      if (e && e.appLevelExtras === true && Array.isArray(e.slugs)) {
        for (const slug of e.slugs) {
          if (s.indexOf(String(slug).toLowerCase()) !== -1) {
            return true;
          }
        }
      }
    }
    return false;
  },
  // Whether a given app TEMPLATE requires a per-instance api_key.
  // Slug-keyed lookup against the backend's per-app registry —
  // delivered via `/api/me`'s `client_config.apps_module_slugs`
  // list. Per-app modules that DECLARE `requires_api_key()` get
  // a positive hit; others fall through to false. Centralised
  // here so the editor's API-key field + Test-credential button
  // can render uniformly without per-app SPA literals.
  appsTemplateRequiresApiKey(slugOrName) {
    const s = String(slugOrName || '').toLowerCase();
    if (!s) {
      return false;
    }
    const ext = (window.OG_APPS_EXTENDERS || []);
    for (const e of ext) {
      if (e && Array.isArray(e.slugs)) {
        for (const slug of e.slugs) {
          if (s.indexOf(String(slug).toLowerCase()) !== -1 && e.requiresApiKey) {
            return true;
          }
        }
      }
    }
    return false;
  },
  // Resolve the effective "show extras" boolean for one instance,
  // honouring the tri-state per-instance override + the template
  // default. Resolution order:
  //   1. Per-instance `inst.show_extras` (true / false) — explicit
  //      operator opt-in or opt-out for THIS instance regardless of
  //      template. null / undefined means "inherit".
  //   2. App-level `app.catalog.show_extras` (true / false) — the
  //      template's default that every uninherited instance follows.
  //   3. Default `true` — when no override + no template setting,
  //      extras render. Backward-compatible with the pre-toggle
  //      behaviour where every extras-capable instance
  //      unconditionally rendered its panel.
  appsShowExtras(app, inst, item) {
    // Explicit per-instance / per-template flag ALWAYS wins.
    if (inst && typeof inst.show_extras === 'boolean') {
      return inst.show_extras;
    }
    const cat = (app && app.catalog) || null;
    if (cat && typeof cat.show_extras === 'boolean') {
      return cat.show_extras;
    }
    // On the Custom dashboard the size PRESET decides — checked BEFORE the
    // cardSpan fallback below so a wide-span per-app module (APC / Speedtest)
    // still honours the operator's chosen footprint. Sizing a card up to
    // double / x-large WIDTH or tall HEIGHT is an implicit "give me the rich
    // view" request; a narrow-short card (half / normal width + short height)
    // HIDES extras so they don't clip the fixed footprint. `item` is only in
    // scope on the Custom dashboard (the per-app extras partial passes it when
    // defined); the by-app grid passes nothing and falls through below.
    const opts = (item && item.opts) || null;
    if (opts) {
      if (opts.size === 'double' || opts.size === 'xlarge') {
        return true;
      }
      if (opts.height === 'tall') {
        return true;
      }
      // normal + short (2x1): extras DO render, but the size-gated CSS
      // hides the sparkline and caps the per-app stat grid to the first
      // 2 cells so they fit the short footprint in the freed space.
      if (opts.size === 'normal') {
        return true;
      }
      // half + short (1x1): ports only — no room for any extras.
      return false;
    }
    // No preset in scope (by-app grid): a wide-span per-app module
    // (cardSpan >= 2, e.g. APC / Speedtest) implicitly wants the rich view.
    if (this.appsCardSpan(app) >= 2) {
      return true;
    }
    // Default OFF — extras are OPT-IN on a compact card. The card still
    // shows icon + title + per-host instance list; only the per-app
    // extras panel is gated. (Historical: defaulting true caused the
    // APC freeze before that bug was fixed; the auto-show above is safe
    // now + only fires on deliberately-enlarged cards.)
    return false;
  },
  // Per-app expanded-card data — generic dispatcher backed by
  // GET /api/services/{host_id}/{service_idx}/app-data. The
  // backend selects the per-app module via slug; the SPA caches
  // the response per (host_id, service_idx) for the render
  // flush. Per-app modules (static/js/apps/*.js) read this via
  // `cmp.appsAppData(inst)` + drive their template gates on the
  // returned shape.
  _appsDataCache: null,
  _appsDataPending: null,
  // Epoch-ms timestamp per cache key of the last SUCCESSFUL /app-data
  // fetch — drives the stale-while-revalidate refresh in appsAppData.
  _appsDataFetchedAt: null,
  // Resolved extras freshness TTL in ms (0 = disabled / fetch-once).
  // Reads the operator tunable via /api/me client_config; the 90000
  // fallback only covers the brief window before /api/me hydrates.
  _appsExtrasTtlMs() {
    const s = this.me && this.me.client_config
      && this.me.client_config.apps_extras_ttl_seconds;
    const n = Number(s);
    if (Number.isFinite(n) && n >= 0) {
      return n * 1000;
    }
    return 90000;
  },
  // Dev-only per-flush getter-call histogram (see app-apps-state.js for the
  // full contract). Any getter under investigation can call
  // `this._ogPerfCount('getterName')` at its top; counts log per ~1s window
  // when `localStorage.og_perf_histogram === '1'`, true no-op otherwise.
  _ogPerfCount(name) {
    return _ogPerfCount(name);
  },
  appsAppDataKey(inst) {
    if (!inst || !inst.host_id || inst.service_idx == null) {
      return '';
    }
    return inst.host_id + ':' + String(inst.service_idx);
  },
  appsAppData(inst) {
    const key = this.appsAppDataKey(inst);
    if (!key) {
      return null;
    }
    if (!this._appsDataCache) {
      this._appsDataCache = {};
    }
    if (key in this._appsDataCache) {
      const v = this._appsDataCache[key];
      // Sentinel filtering — internal pending / error markers are
      // hidden from the template gate (which expects truthy = render
      // the live data card). The dedicated `appsAppDataStatus(inst)`
      // helper surfaces them for the loading / error empty states.
      if (v && typeof v === 'object' && v.__pending) {
        return null;
      }
      if (v && typeof v === 'object' && v.__error) {
        return null;
      }
      // Stale-while-revalidate: a real cached value older than the TTL kicks
      // a BACKGROUND refresh (the stale value still renders now; the cache
      // swaps when the fetch lands). The _appsDataPending guard in
      // loadAppData coalesces repeated stale hits to a single fetch per TTL
      // window, and loadAppData(false) leaves the stale value in place (it
      // only stamps a __pending sentinel on a cache MISS), so the card never
      // flickers to a loading state on revalidation. 0 = disabled.
      const ttlMs = this._appsExtrasTtlMs();
      if (ttlMs > 0 && this._appsDataFetchedAt) {
        const at = this._appsDataFetchedAt[key];
        if (at && (Date.now() - at) > ttlMs) {
          this.loadAppData(inst, false);
        }
      }
      return v;
    }
    this.loadAppData(inst, false);
    return null;
  },
  // Status of the per-app data fetch for one instance. Drives the
  // template's empty-state branches: `pending` shows "Loading...",
  // `error` shows the upstream failure detail, `ok` is the live
  // card, `idle` is "no api_key set" / "not yet requested".
  appsAppDataStatus(inst) {
    const key = this.appsAppDataKey(inst);
    if (!key || !this._appsDataCache) {
      return 'idle';
    }
    const v = this._appsDataCache[key];
    if (v && typeof v === 'object' && v.__pending) {
      return 'pending';
    }
    if (v && typeof v === 'object' && v.__error) {
      return 'error';
    }
    if (v && typeof v === 'object') {
      return 'ok';
    }
    return 'idle';
  },
  // Human-readable error detail for the per-app data fetch — only
  // populated when appsAppDataStatus(inst) === 'error'. Returns ''
  // otherwise so the template's empty-state branch can render
  // unconditionally.
  appsAppDataError(inst) {
    const key = this.appsAppDataKey(inst);
    if (!key || !this._appsDataCache) {
      return '';
    }
    const v = this._appsDataCache[key];
    if (v && typeof v === 'object' && v.__error) {
      return String(v.__error || '').slice(0, 240);
    }
    return '';
  },
  async loadAppData(inst, force) {
    const key = this.appsAppDataKey(inst);
    if (!key) {
      return;
    }
    if (!this._appsDataPending) {
      this._appsDataPending = {};
    }
    if (this._appsDataPending[key] && !force) {
      return;
    }
    this._appsDataPending[key] = true;
    // Stamp a __pending sentinel into the cache so the template's
    // status helper renders the "Loading..." empty state immediately
    // (vs. cache-miss → null → re-trigger loadAppData → loop). Once
    // the fetch lands the sentinel is replaced by the real response
    // or an __error sentinel.
    if (!this._appsDataCache) {
      this._appsDataCache = {};
    }
    if (!(key in this._appsDataCache)) {
      this._appsDataCache[key] = {__pending: true};
    }
    try {
      const url = '/api/services/' + encodeURIComponent(inst.host_id)
        + '/' + encodeURIComponent(inst.service_idx) + '/app-data'
        + (force ? '?force=true' : '');
      const r = await fetch(url, {cache: 'no-store'});
      if (!r.ok) {
        // Try to surface a useful error detail from the backend's
        // JSON body; fall through to status text on parse failure.
        let detail = 'HTTP ' + r.status;
        try {
          const j = await r.json();
          if (j && j.detail) {
            detail = String(j.detail);
          }
        } catch (_e) {
          // body wasn't JSON; keep the HTTP status fallback.
        }
        this._appsDataCache[key] = {__error: detail};
      } else {
        this._appsDataCache[key] = await r.json();
        // Stamp the success time so appsAppData's stale-while-revalidate
        // check can age this entry out + trigger a background refresh.
        if (!this._appsDataFetchedAt) {
          this._appsDataFetchedAt = {};
        }
        this._appsDataFetchedAt[key] = Date.now();
      }
    } catch (err) {
      this._appsDataCache[key] = {__error: (err && err.message) ? err.message : String(err)};
    } finally {
      this._appsDataPending[key] = false;
    }
  },
  // Hostname (without scheme / path) for a bookmark's subtitle line.
  appsBookmarkHost(url) {
    if (!url) {
      return '';
    }
    try {
      return new URL(url).host || url;
    } catch (_) {
      return String(url).replace(/^[a-z]+:\/\//i, '').split('/')[0];
    }
  },
  // Icon resolver for a bookmark — accepts EITHER a slug (e.g.
  // "github" / "plex" / "adguard") OR a full URL (http / https /
  // data: / absolute path). Slugs route through the existing
  // iconUrlFor resolver (same path the stack / host icons take).
  // URLs render verbatim. Empty `item.icon` falls back to
  // iconUrlFor on the bookmark NAME so a brand-named bookmark
  // ("Plex") still gets the brand icon without explicit config.
  appsBookmarkIconUrl(item) {
    if (!item) {
      return '';
    }
    const raw = (item.icon || '').trim();
    if (raw) {
      // data-URI or absolute path — render verbatim (these ARE images).
      if (raw.startsWith('data:') || raw.startsWith('/')) {
        return raw;
      }
      // A full http(s) URL is only trustworthy as an icon when it points
      // at an actual IMAGE file (ends in an image extension). Operators
      // who saved a bookmark before the icon input was fixed from
      // type="url" to type="text" ended up with the PAGE url (e.g.
      // `https://5g/`) sitting in the icon field — loading that as an
      // <img> just 404s / renders nothing. So: an image-extension URL is
      // used verbatim; any OTHER http(s) value is ignored here and we
      // fall through to brand-resolving the NAME below (which finds the
      // ftth / 5g brand icon).
      if (/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) {
        if (/\.(?:svg|png|webp|jpe?g|gif|ico)(?:\?.*)?$/i.test(raw)) {
          return raw;
        }
        // Non-image URL in the icon field — skip it, resolve from name.
        return this.iconUrlFor(item.name || '') || '';
      }
      // Bare slug — route through the brand-icon resolver first.
      let resolved;
      try {
        resolved = this.iconUrlFor(raw);
      } catch (_) {
        resolved = '';
      }
      if (resolved) {
        return resolved;
      }
      // The operator set this icon slug EXPLICITLY, so trust it and hit
      // the file directly — bypassing iconUrlFor's KNOWN_ICONS gate
      // (that gate exists to avoid 404s for AUTO-derived stack/host
      // NAMES, not for an operator's deliberate slug). This is why a
      // host-keyword slug like `ftth` — a real /img/icons/ftth.svg that
      // resolves for hosts via the keyword scan but isn't in
      // KNOWN_ICONS — now also resolves for a bookmark. The bookmark
      // <img> has an @error handler that hides it cleanly if the slug
      // turns out to have no matching file.
      const slug = raw.toLowerCase().replace(/[\s_]+/g, '-').replace(/[^a-z0-9-]/g, '');
      if (slug) {
        return '/img/icons/' + slug + '.svg';
      }
      return raw;
    }
    // No explicit icon — fall back to the brand resolver on the
    // bookmark's display name. Returns '' when the name matches no
    // brand (the <img> @error handler then hides the broken image
    // cleanly). NEVER fall through to the bookmark URL as an icon
    // source — a URL like `https://5g/` is not an image and the
    // browser would try (and fail) to load it as one (operator-flagged).
    try {
      return this.iconUrlFor(item.name || '') || '';
    } catch (_) {
      return '';
    }
  },

  // lgtm[js/insecure-randomness]  — CodeQL: this function is the
  // canonical id-minter for Alpine `:key` UI identifiers (NOT a
  // security secret). Primary path is `crypto.randomUUID()`;
  // secondary is `crypto.getRandomValues`; tertiary is a
  // deterministic monotonic counter (NO PRNG anywhere in the
  // chain). The lgtm marker survives stale-cache re-scans where
  // CodeQL's data-flow tracker carries forward a flag from an
  // earlier revision that DID use a PRNG. Mirror suppression on
  // `_mintInstancePortUid` below.
  _newId(prefix) {
    // Primary path — crypto.randomUUID() (universally supported in
    // modern browsers). Returns a 36-char UUID string. The id is a
    // UI-only identifier (Alpine x-for :key), not a security secret;
    // we still use the crypto-strength source so CodeQL's
    // `js/insecure-randomness` audit stays clean fleet-wide.
    try {
      if (window.crypto && window.crypto.randomUUID) {
        return (prefix || '') + window.crypto.randomUUID();
      }
      // Secondary path — crypto.getRandomValues() (older browsers
      // that have Web Crypto but not the .randomUUID shortcut). Same
      // crypto-strength source; assembles 8 random bytes into a
      // 16-char hex string for the suffix. The fallback chain is
      // crypto-only by design — NO PRNG anywhere in this function
      // so CodeQL's data-flow analysis sees the consumer chain as
      // entropy-clean from source to sink.
      if (window.crypto && window.crypto.getRandomValues) {
        const buf = new Uint8Array(8);
        window.crypto.getRandomValues(buf);
        let hex = '';
        for (let i = 0; i < buf.length; i++) {
          hex += buf[i].toString(16).padStart(2, '0');
        }
        return (prefix || '') + Date.now().toString(36) + '-' + hex;
      }
    } catch (_) { /* fall through to the deterministic fallback */
    }
    // Tertiary fallback — Web Crypto truly unavailable (extremely
    // old browser / disabled by enterprise policy). Use a
    // monotonically-increasing counter combined with a timestamp so
    // the id is unique within the session without invoking any
    // PRNG. The id isn't a secret — collision-resistance +
    // deterministic-not-PRNG is what the Alpine x-for key contract
    // needs; entropy isn't relevant.
    this._idCounter = (this._idCounter || 0) + 1;
    return (prefix || '') + Date.now().toString(36) + '-c' + this._idCounter.toString(36);
  },
  _newSectionId() {
    return this._newId('sec-');
  },

  // Normalise one stored item into the canonical {uid, kind, ...} shape.
  // Back-compat: a bare string is a legacy app group_id. Returns null for
  // anything unrecognisable so a tampered blob can't crash the render.
  // Sanitiser for the per-item `opts` sub-dict (Apps Custom dashboard
  // per-card settings). Every Custom-layout item carries an optional
  // opts dict via this shape:
  //   {size: 'half' | 'normal' | 'double' | 'xlarge',
  //    follow_user: bool,            // widget-specific (clock + weather)
  //    clock_tz: string,             // IANA TZ name (clock widget only)
  //    clock_format: string,         // datetime token string (clock only)
  //    clock_style: 'digital' | 'analog',
  //    weather_lat: number,          // override location (weather only)
  //    weather_lng: number,
  //    weather_label: string,
  //    weather_units: 'metric' | 'imperial',
  //    weather_forecast_days: 0..7,  // 0 disables forecast strip
  //    weather_show_conditions: bool}
  // Unknown keys are dropped on round-trip; numeric clamps enforced to
  // keep a corrupted ui_prefs blob from breaking the layout.
  _normAppsItemOpts(raw) {
    if (!raw || typeof raw !== 'object') {
      return {};
    }
    const out = {};
    const size = String(raw.size || '').toLowerCase();
    if (size === 'half' || size === 'normal' || size === 'double' || size === 'xlarge') {
      out.size = size;
    }
    // HEIGHT preset (short / tall) — must be whitelisted here or it's
    // stripped on every normalize pass, so a card's height reverted on
    // reload while its width (size, above) persisted. This sanitizer is
    // the single choke point for opts on BOTH load and save.
    const height = String(raw.height || '').toLowerCase();
    if (height === 'short' || height === 'tall') {
      out.height = height;
    }
    if (typeof raw.follow_user === 'boolean') {
      out.follow_user = raw.follow_user;
    }
    if (typeof raw.clock_tz === 'string' && raw.clock_tz.length <= 64) {
      out.clock_tz = raw.clock_tz;
    }
    if (typeof raw.clock_format === 'string' && raw.clock_format.length <= 128) {
      out.clock_format = raw.clock_format;
    }
    const style = String(raw.clock_style || '').toLowerCase();
    if (style === 'digital' || style === 'analog') {
      out.clock_style = style;
    }
    if (typeof raw.weather_lat === 'number' && isFinite(raw.weather_lat)) {
      out.weather_lat = Math.max(-90, Math.min(90, raw.weather_lat));
    }
    if (typeof raw.weather_lng === 'number' && isFinite(raw.weather_lng)) {
      out.weather_lng = Math.max(-180, Math.min(180, raw.weather_lng));
    }
    if (typeof raw.weather_label === 'string' && raw.weather_label.length <= 80) {
      out.weather_label = raw.weather_label;
    }
    const units = String(raw.weather_units || '').toLowerCase();
    if (units === 'metric' || units === 'imperial') {
      out.weather_units = units;
    }
    if (typeof raw.weather_forecast_days === 'number'
      && isFinite(raw.weather_forecast_days)) {
      out.weather_forecast_days = Math.max(0, Math.min(7,
        Math.round(raw.weather_forecast_days)));
    }
    if (typeof raw.weather_show_conditions === 'boolean') {
      out.weather_show_conditions = raw.weather_show_conditions;
    }
    return out;
  },

  _normAppsItem(raw) {
    if (typeof raw === 'string') {
      return {uid: this._newId('it-'), kind: 'app', ref: raw, opts: {}};
    }
    if (!raw || typeof raw !== 'object') {
      return null;
    }
    const uid = raw.uid ? String(raw.uid) : this._newId('it-');
    const kind = raw.kind;
    const opts = this._normAppsItemOpts(raw.opts);
    if (kind === 'app' && raw.ref != null) {
      return {uid, kind: 'app', ref: String(raw.ref), opts};
    }
    if (kind === 'widget' && this.appsWidgetKinds.includes(raw.widget)) {
      return {uid, kind: 'widget', widget: String(raw.widget), opts};
    }
    if (kind === 'bookmark' && (raw.url || raw.name)) {
      return {
        uid, kind: 'bookmark',
        name: typeof raw.name === 'string' ? raw.name : '',
        url: typeof raw.url === 'string' ? raw.url : '',
        icon: typeof raw.icon === 'string' ? raw.icon : '',
        opts,
      };
    }
    return null;
  },

  // ── Multiple named custom views ────────────────────────────────
  // The custom dashboard is no longer a single layout: the user keeps SEVERAL
  // named views and switches between them. Persistence (free-form ui_prefs,
  // NO backend change):
  //   ui_prefs.apps_custom_views = { active_id, views: [{id, name, layout}] }
  // where `layout` is the same {sections, unsectioned_collapsed} object the
  // board mutators already operate on. `appsCustomLayout` stays a live
  // REFERENCE to the ACTIVE view's layout, so every existing section / drag /
  // collapse mutator + _persistAppsCustomLayout keep working unchanged — they
  // just act on whichever view is active. Legacy single-layout installs
  // (ui_prefs.apps_custom_layout with sections) migrate into one view on first
  // hydrate; the legacy key keeps being written (= active view) for back-compat
  // with an older client / rollback.

  // Normalise a raw saved layout blob into {sections, unsectioned_collapsed}.
  // Heterogeneous items[] is canonical; legacy `app_ids:[gid]` (pre-widget
  // schema) converts to app items so existing saved boards survive untouched.
  _normAppsLayout(saved) {
    const src = (saved && typeof saved === 'object') ? saved : {};
    const sections = (Array.isArray(src.sections) ? src.sections : [])
      .filter(s => s && typeof s === 'object' && s.id)
      .map(s => {
        let items;
        if (Array.isArray(s.items)) {
          items = s.items.map(it => this._normAppsItem(it)).filter(Boolean);
        } else if (Array.isArray(s.app_ids)) {
          items = s.app_ids.map(gid => ({uid: this._newId('it-'), kind: 'app', ref: String(gid), opts: {}}));
        } else {
          items = [];
        }
        return {
          id: String(s.id),
          name: typeof s.name === 'string' ? s.name : '',
          collapsed: s.collapsed === true,
          items,
        };
      });
    return {sections, unsectioned_collapsed: !!src.unsectioned_collapsed};
  },

  _appsViewDefaultName() {
    return (this.t && this.t('apps.custom.view_default_name')) || 'My Dashboard';
  },

  _appsActiveViewObj() {
    const c = this.appsCustomViews;
    if (!c || !Array.isArray(c.views) || !c.views.length) {
      return null;
    }
    return c.views.find(v => v && v.id === c.active_id) || c.views[0];
  },

  // Build the views collection once me.ui_prefs has landed (migrate legacy /
  // seed a default) and point appsCustomLayout at the active view's layout.
  // Re-callable: once built (≥1 view) it only re-points appsCustomLayout and
  // returns, so it never clobbers an in-flight drag.
  // Sync re-point of appsCustomLayout to the active view's layout. The views
  // collection itself is loaded ASYNCHRONOUSLY from the server (app_views) by
  // ensureAppViewsLoaded() — views moved out of ui_prefs so they can be shared
  // public across users. Until the collection lands we keep a valid empty
  // layout so consumers (the build memo) never hit null, and opportunistically
  // kick the loader when the user is on the custom board.
  _hydrateAppsCustomViews() {
    if (this.appsCustomViews && Array.isArray(this.appsCustomViews.views) && this.appsCustomViews.views.length) {
      const av = this._appsActiveViewObj();
      if (av) {
        this.appsCustomLayout = av.layout;
      }
      return;
    }
    if (!this.appsCustomLayout || !Array.isArray(this.appsCustomLayout.sections)) {
      this.appsCustomLayout = this._normAppsLayout({});
    }
    if (!this.appsViewsLoaded && !this._appViewsLoading
      && this.appsViewGroupBy === 'custom' && this.me && this.me.ui_prefs) {
      // fire-and-forget — single-flight guarded inside ensureAppViewsLoaded
      this.ensureAppViewsLoaded();
    }
  },

  // Map a server app_views row into the local view object (normalised layout
  // + the resolved permission flags the UI gates on).
  _normServerView(sv) {
    const s = sv || {};
    return {
      id: String(s.id || ''),
      name: (typeof s.name === 'string' && s.name.trim()) ? s.name : this._appsViewDefaultName(),
      layout: this._normAppsLayout(s.layout || {}),
      visibility: s.visibility === 'public' ? 'public' : 'private',
      edit_permission: s.edit_permission === 'all' ? 'all' : 'owner',
      owner_username: s.owner_username || '',
      is_owner: !!s.is_owner,
      can_edit: s.can_edit !== false,
      can_manage: !!s.can_manage,
      updated_at: s.updated_at || 0,
    };
  },

  // Adopt a server-returned views array as the live collection + pick the
  // active view (persisted per-user in ui_prefs.apps_active_view_id).
  _adoptServerViews(serverViews) {
    const views = (Array.isArray(serverViews) ? serverViews : [])
      .filter(v => v && v.id)
      .map(v => this._normServerView(v));
    let activeId = null;
    try {
      activeId = (this.me && this.me.ui_prefs && this.me.ui_prefs.apps_active_view_id) || null;
    } catch (_) { /* default null */
    }
    if (!activeId || !views.some(v => v.id === activeId)) {
      activeId = views.length ? views[0].id : null;
    }
    this.appsCustomViews = {active_id: activeId, views};
    const av = this._appsActiveViewObj();
    this.appsCustomLayout = av ? av.layout : this._normAppsLayout({});
    _appsCustomBuildCache = null;
  },

  // Load the user's app-dashboard views from the server once. On an empty
  // server (fresh user OR a deployment upgrading from the old ui_prefs-only
  // storage) it migrates the user's existing local views into owned-private
  // rows, or seeds a single default. Single-flight + idempotent.
  async ensureAppViewsLoaded(force = false) {
    if (this._appViewsLoading) {
      return;
    }
    if (this.appsViewsLoaded && !force) {
      return;
    }
    // Migration reads me.ui_prefs (the user's pre-existing local views) — wait
    // until /api/me has landed so we don't prematurely seed a default and skip
    // the import. Reading me.ui_prefs here also registers it as an x-effect
    // dependency, so the loading-pane's x-effect re-fires this once it lands.
    if (!(this.me && this.me.ui_prefs)) {
      return;
    }
    this._appViewsLoading = true;
    this.appsViewsLoadError = '';
    try {
      const r = await fetch('/api/apps/views');
      if (!r.ok) {
        if (r.status === 401) {
          return;
        }
        throw new Error(await this.fmtResponseError(r));
      }
      const j = await r.json();
      let serverViews = Array.isArray(j.views) ? j.views : [];
      if (!serverViews.length) {
        serverViews = await this._migrateOrSeedAppViews();
      }
      this._adoptServerViews(serverViews);
      this.appsViewsLoaded = true;
    } catch (err) {
      this.appsViewsLoadError = (err && err.message) ? err.message : String(err);
    } finally {
      this._appViewsLoading = false;
    }
  },

  // First-load bootstrap when the server has no views for this user: migrate
  // any pre-existing local (ui_prefs) views into owned-private rows, else seed
  // one default. Returns the server-shaped rows for _adoptServerViews.
  async _migrateOrSeedAppViews() {
    let localViews = [];
    let activeId = null;
    try {
      const up = (this.me && this.me.ui_prefs) || {};
      const migrated = !!up.apps_views_migrated;
      const saved = up.apps_custom_views;
      const legacy = up.apps_custom_layout;
      if (!migrated && saved && Array.isArray(saved.views) && saved.views.length) {
        localViews = saved.views.filter(v => v && v.id).map(v => ({
          id: String(v.id),
          name: (typeof v.name === 'string' && v.name.trim()) ? v.name.slice(0, 64) : this._appsViewDefaultName(),
          layout: this._normAppsLayout(v.layout || {}),
        }));
        activeId = saved.active_id || null;
      } else if (!migrated && legacy && Array.isArray(legacy.sections) && legacy.sections.length) {
        localViews = [{id: this._newId('view-'), name: this._appsViewDefaultName(), layout: this._normAppsLayout(legacy)}];
      }
    } catch (_) { /* fall through to seed */
    }

    const out = [];
    if (localViews.length) {
      for (const lv of localViews) {
        const row = await this._postAppView({
          id: lv.id,
          name: lv.name,
          layout: {sections: lv.layout.sections || [], unsectioned_collapsed: !!lv.layout.unsectioned_collapsed},
          visibility: 'private',
          edit_permission: 'owner',
        });
        if (row) {
          out.push(row);
        }
      }
      // Mark migrated + carry the previously-active id forward so the same
      // dashboard stays selected.
      const prefs = {apps_views_migrated: true};
      if (activeId) {
        prefs.apps_active_view_id = activeId;
      }
      this._patchUiPrefs(prefs);
    }
    if (!out.length) {
      // Fresh user (or migration produced nothing) — seed one default view.
      const row = await this._postAppView({
        name: this._appsViewDefaultName(),
        layout: {sections: [], unsectioned_collapsed: false},
        visibility: 'private',
        edit_permission: 'owner',
      });
      if (row) {
        out.push(row);
      }
    }
    return out;
  },

  // POST one view to the server; returns the server row (or null on failure).
  async _postAppView(body) {
    try {
      const r = await fetch('/api/apps/views', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      const j = await r.json();
      return j.view || null;
    } catch (err) {
      this._appViewError(err);
      return null;
    }
  },

  // Fire-and-forget ui_prefs merge (active-view id, migrated flag) — same
  // channel + shape the rest of the SPA uses for ui_prefs scalars.
  _patchUiPrefs(prefs) {
    if (this.me) {
      if (!this.me.ui_prefs) {
        this.me.ui_prefs = {};
      }
      Object.assign(this.me.ui_prefs, prefs);
    }
    try {
      fetch('/api/me/ui-prefs', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prefs}),
      }).catch(() => { /* in-memory already updated */
      });
    } catch (_) { /* ignore */
    }
  },

  _persistActiveViewId(id) {
    this._patchUiPrefs({apps_active_view_id: id});
  },

  _appViewError(err) {
    const msg = (err && err.message) ? err.message : String(err);
    if (typeof this.showToast === 'function') {
      this.showToast(msg, 'error');
    }
  },

  // Lazy entry point kept for every existing caller. Ensures the views
  // collection is built + appsCustomLayout points at the active view, and
  // ALWAYS leaves appsCustomLayout a valid {sections,...} object (even in the
  // brief pre-/api/me window) so consumers never hit null.
  _hydrateAppsCustomLayout() {
    this._hydrateAppsCustomViews();
    if (!this.appsCustomLayout || !Array.isArray(this.appsCustomLayout.sections)) {
      this.appsCustomLayout = this._normAppsLayout({});
    }
  },

  appsUnsectionedCollapsed() {
    this._hydrateAppsCustomLayout();
    return !!this.appsCustomLayout.unsectioned_collapsed;
  },
  toggleAppsUnsectionedCollapsed() {
    this._hydrateAppsCustomLayout();
    this.appsCustomLayout.unsectioned_collapsed = !this.appsCustomLayout.unsectioned_collapsed;
    this._persistAppsCustomLayout();
  },

  // Shared builder for the custom-mode render. Returns
  // `{sections: [{id, name, collapsed, items:[renderable]}], unsectioned: [appObj]}`.
  // Each renderable item is `{uid, kind, app?}` (app resolved to the live
  // filtered object) / `{uid, kind:'widget', widget}` / `{uid, kind:'bookmark',
  // name, url, icon}`. App refs filtered out by search / status are still
  // marked "assigned" so they don't leak into Unsectioned, just not rendered.
  // Unsectioned only ever holds APP cards (widgets / bookmarks live solely
  // where the user placed them).
  _buildAppsCustom() {
    // Flush-memo (see _appsCustomBuildCache decl): collapse the ~5
    // calls/flush from appsCustomSections() + appsCustomUnsectioned()
    // into ONE model build per flush.
    if (_appsCustomBuildCache !== null) {
      return _appsCustomBuildCache;
    }
    this._hydrateAppsCustomLayout();
    const apps = this.filteredApps() || [];
    const byId = {};
    for (const a of apps) {
      byId[a.group_id] = a;
    }
    // Active search query (the Apps-view search box, shared across modes).
    // App tiles already honour it via filteredApps() above; here it also
    // filters bookmark tiles by name/url and HIDES widget tiles (they're
    // info panels, not search targets) so a query turns the board into a
    // focused results view.
    const q = (this.appsSearchQuery || '').trim().toLowerCase();
    const assignedRefs = new Set();
    const sections = (this.appsCustomLayout.sections || []).map(s => {
      const items = [];
      for (const it of (s.items || [])) {
        // Pass the persisted opts dict through to the rendered item
        // unchanged — every per-card setting (size, follow-user, custom
        // TZ / format / location / units / forecast-days) lives here.
        // The render-side resolver `effectiveClockOpts` /
        // `effectiveWeatherOpts` reads the dict + falls back to global
        // user settings when a key is absent.
        const opts = it.opts || {};
        if (it.kind === 'app') {
          // Duplicate-app-card feature: the SAME app ref may now live in
          // multiple sections (or the same section more than once). The
          // FIRST occurrence of a ref in document order is the "main"
          // copy; every later one is a "shadow" (is_shadow) — the render
          // layer shows a remove pill on shadows only (the main is removed
          // by dragging it back to Unsectioned). `assignedRefs` still
          // tracks "has at least one placement" so an app drops back to
          // Unsectioned only when EVERY copy is gone.
          const isShadow = assignedRefs.has(it.ref);
          assignedRefs.add(it.ref);
          const app = byId[it.ref];
          if (!app) {
            continue;  // filtered out this pass (search / status) — stays assigned
          }
          items.push({uid: it.uid, kind: 'app', ref: it.ref, app, opts, is_shadow: isShadow});
        } else if (it.kind === 'widget') {
          if (q) {
            continue;  // hide info widgets while searching
          }
          items.push({uid: it.uid, kind: 'widget', widget: it.widget, opts});
        } else if (it.kind === 'bookmark') {
          if (q && !((it.name || '').toLowerCase().includes(q) || (it.url || '').toLowerCase().includes(q))) {
            continue;  // bookmark doesn't match the search
          }
          items.push({
            uid: it.uid, kind: 'bookmark',
            name: it.name, url: it.url, icon: it.icon, opts,
          });
        }
      }
      return {id: s.id, name: s.name, collapsed: !!s.collapsed, items};
    });
    const unsectioned = apps.filter(a => !assignedRefs.has(a.group_id));
    const result = {sections, unsectioned};
    _appsCustomBuildCache = result;
    if (!_appsCustomBuildScheduled) {
      _appsCustomBuildScheduled = true;
      queueMicrotask(() => {
        _appsCustomBuildCache = null;
        _appsCustomBuildScheduled = false;
      });
    }
    return result;
  },

  appsCustomSections() {
    return this._buildAppsCustom().sections;
  },
  appsCustomUnsectioned() {
    return this._buildAppsCustom().unsectioned;
  },
  // Renderable count of an app + the widgets/bookmarks in a section — drives
  // the locked-view "hide empty section" gate (a section with only widgets
  // still counts as non-empty).
  appsSectionItemCount(sec) {
    return (sec && Array.isArray(sec.items)) ? sec.items.length : 0;
  },

  _findAppsSection(sectionId) {
    this._hydrateAppsCustomLayout();
    return this.appsCustomLayout.sections.find(s => s.id === sectionId) || null;
  },

  addAppsSection() {
    this._hydrateAppsCustomLayout();
    this.appsCustomLayout.sections.push({
      id: this._newSectionId(),
      name: this.t('apps.custom.new_section_name') || 'New section',
      collapsed: false,
      items: [],
    });
    this._persistAppsCustomLayout();
  },

  renameAppsSection(sectionId, name) {
    const sec = this._findAppsSection(sectionId);
    if (!sec) {
      return;
    }
    sec.name = (name == null ? '' : String(name)).slice(0, 80);
    this._persistAppsCustomLayout();
  },

  deleteAppsSection(sectionId) {
    this._hydrateAppsCustomLayout();
    // App items fall back to Unsectioned (no longer referenced anywhere);
    // any widgets / bookmarks the section held are removed with it.
    const i = this.appsCustomLayout.sections.findIndex(s => s.id === sectionId);
    if (i < 0) {
      return;
    }
    this.appsCustomLayout.sections.splice(i, 1);
    this._persistAppsCustomLayout();
  },

  toggleAppsSectionCollapsed(sectionId) {
    const sec = this._findAppsSection(sectionId);
    if (!sec) {
      return;
    }
    sec.collapsed = !sec.collapsed;
    this._persistAppsCustomLayout();
  },

  isAppsSectionCollapsed(sectionId) {
    const sec = this._findAppsSection(sectionId);
    return !!(sec && sec.collapsed);
  },

  // Add a widget tile (clock / weather / …) to a section.
  addAppsWidget(sectionId, widgetKind) {
    if (!this.appsWidgetKinds.includes(widgetKind)) {
      return;
    }
    const sec = this._findAppsSection(sectionId);
    if (!sec) {
      return;
    }
    sec.items.push({uid: this._newId('it-'), kind: 'widget', widget: widgetKind});
    this._persistAppsCustomLayout();
  },

  // Add a bookmark (external link) tile to a section. `name` / `url` from
  // the edit-mode add-bookmark form; icon optional (resolver falls back to
  // a keyword/slug match on the name).
  addAppsBookmark(sectionId, name, url, icon) {
    const sec = this._findAppsSection(sectionId);
    if (!sec) {
      return;
    }
    const u = (url == null ? '' : String(url)).trim();
    const nm = (name == null ? '' : String(name)).trim();
    if (!u && !nm) {
      return;
    }
    sec.items.push({
      uid: this._newId('it-'), kind: 'bookmark',
      name: nm.slice(0, 80), url: u.slice(0, 2048), icon: (icon || '').trim().slice(0, 80),
    });
    this._persistAppsCustomLayout();
  },

  // Inline add-bookmark form (edit mode) — open / submit / cancel.
  // `appsBookmarkIcon` is the OPTIONAL icon-URL field; empty leaves
  // the tile rendering with the default initial-letter / favicon
  // fallback. When populated, the bookmark tile's `<img>` renders
  // the user-supplied URL directly (svg / png / favicon all work
  // — the browser does the format detection).
  openAppsBookmarkForm(sectionId) {
    this.appsBookmarkOpenFor = (this.appsBookmarkOpenFor === sectionId) ? '' : sectionId;
    this.appsBookmarkName = '';
    this.appsBookmarkUrl = '';
    this.appsBookmarkIcon = '';
  },
  submitAppsBookmark(sectionId) {
    const name = (this.appsBookmarkName || '').trim();
    let url = (this.appsBookmarkUrl || '').trim();
    let icon = (this.appsBookmarkIcon || '').trim();
    if (!url && !name) {
      return;
    }
    // Forgive a missing scheme — default to https:// so the link works.
    if (url && !/^[a-z][a-z0-9+.-]*:\/\//i.test(url)) {
      url = 'https://' + url;
    }
    // Same scheme-tolerance for the icon URL. Keep absolute URLs
    // and `data:` URIs intact; default to https:// otherwise so a
    // bare `cdn.simpleicons.org/github` works as expected.
    if (icon && !/^[a-z][a-z0-9+.-]*:\/\//i.test(icon)
      && !icon.startsWith('data:') && !icon.startsWith('/')) {
      icon = 'https://' + icon;
    }
    this.addAppsBookmark(sectionId, name || this.appsBookmarkHost(url), url, icon);
    this.appsBookmarkOpenFor = '';
    this.appsBookmarkName = '';
    this.appsBookmarkUrl = '';
    this.appsBookmarkIcon = '';
  },
  cancelAppsBookmarkForm() {
    this.appsBookmarkOpenFor = '';
    this.appsBookmarkName = '';
    this.appsBookmarkUrl = '';
    this.appsBookmarkIcon = '';
  },

  // Remove ONE item (widget / bookmark / app) from its section by uid.
  // (App items removed this way just re-appear in Unsectioned.)
  removeAppsItem(sectionId, uid) {
    const sec = this._findAppsSection(sectionId);
    if (!sec) {
      return;
    }
    const i = sec.items.findIndex(it => it.uid === uid);
    if (i >= 0) {
      sec.items.splice(i, 1);
      this._persistAppsCustomLayout();
    }
  },

  // --- native HTML5 drag-and-drop -----------------------------------
  // Two tile drag sources: an EXISTING section item carries its uid; an
  // UNSECTIONED app card carries its group_id (no item exists yet — the
  // drop creates one). Section-header drag carries the section id. All go
  // through a transient state field (browsers don't expose getData during
  // dragover) AND dataTransfer for completeness.
  _appsDragUid: null,        // moving an existing item
  _appsDragAppRef: null,     // dragging an unsectioned app (create on drop)
  _appsDragSectionId: null,  // reordering a section

  appsItemDragStart(ev, uid) {
    this._appsDragUid = String(uid);
    this._appsDragAppRef = null;
    this._appsDragSectionId = null;
    try {
      ev.dataTransfer.effectAllowed = 'move';
      ev.dataTransfer.setData('text/plain', 'item:' + uid);
    } catch (_) { /* restricted */
    }
  },
  appsUnsectionedDragStart(ev, groupId) {
    this._appsDragAppRef = String(groupId);
    this._appsDragUid = null;
    this._appsDragSectionId = null;
    try {
      ev.dataTransfer.effectAllowed = 'move';
      ev.dataTransfer.setData('text/plain', 'app:' + groupId);
    } catch (_) { /* restricted */
    }
  },

  // Drop the dragged tile into `sectionId` ('__unsectioned' un-assigns an
  // app item), optionally BEFORE the item identified by `beforeUid`.
  appsTileDrop(sectionId, beforeUid) {
    const dragUid = this._appsDragUid;
    const dragRef = this._appsDragAppRef;
    this._appsDragUid = null;
    this._appsDragAppRef = null;
    this._hydrateAppsCustomLayout();
    const secs = this.appsCustomLayout.sections;

    // 1) Pull the moving item out of wherever it currently lives.
    let moving = null;
    if (dragUid) {
      for (const s of secs) {
        const i = s.items.findIndex(it => it.uid === dragUid);
        if (i >= 0) {
          moving = s.items.splice(i, 1)[0];
          break;
        }
      }
    } else if (dragRef) {
      // Unsectioned app → create an app item (and defensively strip any
      // stale ref so it can't double-up).
      for (const s of secs) {
        const i = s.items.findIndex(it => it.kind === 'app' && it.ref === dragRef);
        if (i >= 0) {
          s.items.splice(i, 1);
        }
      }
      moving = {uid: this._newId('it-'), kind: 'app', ref: dragRef};
    }
    if (!moving) {
      return;
    }

    // 2) Re-insert. Dropping an APP item onto '__unsectioned' just drops it
    // (leaving it referenced nowhere → it returns to the Unsectioned bucket).
    // A widget / bookmark dropped on '__unsectioned' has nowhere to live, so
    // it's discarded (they only exist inside sections by design).
    if (!sectionId || sectionId === '__unsectioned') {
      this._persistAppsCustomLayout();
      return;
    }
    const sec = secs.find(s => s.id === sectionId);
    if (!sec) {
      this._persistAppsCustomLayout();
      return;
    }
    let pos = sec.items.length;
    if (beforeUid) {
      const bi = sec.items.findIndex(it => it.uid === beforeUid);
      if (bi >= 0) {
        pos = bi;
      }
    }
    sec.items.splice(pos, 0, moving);
    this._persistAppsCustomLayout();
  },

  // Keyboard reorder for a focused edit-mode tile (WCAG 2.1.1 — the
  // drag-and-drop reorder is otherwise mouse/touch-only). Moves the tile
  // one slot WITHIN its section: dir -1 = earlier (Arrow Up/Left), +1 =
  // later (Arrow Down/Right). No wrap at the section edges. Reuses the same
  // layout array + persist path as appsTileDrop; cross-section moves stay on
  // the drag path. Returns true when a move happened so the caller can
  // preventDefault only on an actual reorder.
  appsItemMove(uid, dir) {
    if (!uid || (dir !== -1 && dir !== 1)) {
      return false;
    }
    this._hydrateAppsCustomLayout();
    const secs = this.appsCustomLayout.sections || [];
    // Resolve the owning section WITHOUT an explicit loop so the findIndex /
    // $nextTick closures aren't declared inside a for-body (JSHint W083).
    const s = secs.find(sec => (sec.items || []).some(it => it.uid === uid));
    if (!s) {
      return false;
    }
    const i = s.items.findIndex(it => it.uid === uid);
    const j = i + dir;
    if (j < 0 || j >= s.items.length) {
      return false;
    }
    const tmp = s.items[i];
    s.items[i] = s.items[j];
    s.items[j] = tmp;
    this._persistAppsCustomLayout();
    // Restore focus to the moved tile after Alpine re-renders the list.
    this.$nextTick(() => {
      try {
        const el = document.querySelector('[data-apps-tile-uid="' + uid + '"]');
        if (el && typeof el.focus === 'function') {
          el.focus();
        }
      } catch (_e) { /* ignore */
      }
    });
    return true;
  },

  // Arrow-key handler for a focused edit-mode tile → appsItemMove. Bound on
  // the draggable cell; the `ev.target === ev.currentTarget` guard means it
  // only fires when the CELL itself is focused, never when an arrow key is
  // pressed inside a descendant input (so text-field caret nav is intact).
  _appsTileMoveKey(ev, uid) {
    if (!this.appsCustomEditMode || ev.target !== ev.currentTarget) {
      return;
    }
    const k = ev.key;
    let dir;
    if (k === 'ArrowUp' || k === 'ArrowLeft') {
      dir = -1;
    } else if (k === 'ArrowDown' || k === 'ArrowRight') {
      dir = 1;
    } else {
      return;
    }
    if (this.appsItemMove(uid, dir)) {
      ev.preventDefault();
    }
  },

  // Keyboard reorder for a focused edit-mode SECTION header (WCAG 2.1.1 —
  // the section drag-and-drop is otherwise mouse/touch-only; tiles already
  // got appsItemMove, sections were the gap). Moves the section one slot:
  // dir -1 = earlier (Arrow Up/Left), +1 = later (Arrow Down/Right). No wrap.
  // Mirrors appsSectionDrop's array-splice + persist. Returns true on a move.
  appsSectionMove(sectionId, dir) {
    const id = String(sectionId);
    if (!id || (dir !== -1 && dir !== 1)) {
      return false;
    }
    this._hydrateAppsCustomLayout();
    const secs = this.appsCustomLayout.sections || [];
    const i = secs.findIndex(s => s.id === id);
    if (i < 0) {
      return false;
    }
    const j = i + dir;
    if (j < 0 || j >= secs.length) {
      return false;
    }
    const tmp = secs[i];
    secs[i] = secs[j];
    secs[j] = tmp;
    this._persistAppsCustomLayout();
    // Restore focus to the moved section header after Alpine re-renders.
    this.$nextTick(() => {
      try {
        const el = document.querySelector('[data-apps-section-id="' + id + '"]');
        if (el && typeof el.focus === 'function') {
          el.focus();
        }
      } catch (_e) { /* ignore */
      }
    });
    return true;
  },

  // Arrow-key handler for a focused edit-mode section header → appsSectionMove.
  // The `ev.target === ev.currentTarget` guard keeps caret-nav inside the
  // rename input intact (the handler only fires when the header itself is
  // focused). Mirrors _appsTileMoveKey.
  _appsSectionMoveKey(ev, sectionId) {
    if (!this.appsCustomEditMode || ev.target !== ev.currentTarget) {
      return;
    }
    const k = ev.key;
    let dir;
    if (k === 'ArrowUp' || k === 'ArrowLeft') {
      dir = -1;
    } else if (k === 'ArrowDown' || k === 'ArrowRight') {
      dir = 1;
    } else {
      return;
    }
    if (this.appsSectionMove(sectionId, dir)) {
      ev.preventDefault();
    }
  },

  appsSectionDragStart(ev, sectionId) {
    this._appsDragSectionId = String(sectionId);
    this._appsDragUid = null;
    this._appsDragAppRef = null;
    try {
      ev.dataTransfer.effectAllowed = 'move';
      ev.dataTransfer.setData('text/plain', 'section:' + sectionId);
    } catch (_) { /* ignore */
    }
  },

  // Reorder sections — drop the dragged section BEFORE `targetSectionId`.
  appsSectionDrop(targetSectionId) {
    const src = this._appsDragSectionId;
    this._appsDragSectionId = null;
    if (!src || src === targetSectionId) {
      return;
    }
    this._hydrateAppsCustomLayout();
    const secs = this.appsCustomLayout.sections;
    const si = secs.findIndex(s => s.id === src);
    const ti = secs.findIndex(s => s.id === targetSectionId);
    if (si < 0 || ti < 0) {
      return;
    }
    const moved = secs.splice(si, 1)[0];
    const newTi = secs.findIndex(s => s.id === targetSectionId);
    secs.splice(newTi, 0, moved);
    this._persistAppsCustomLayout();
  },

  // ============================================================
  // Per-card setting helpers — size, flip-to-settings, per-widget
  // overrides. Drives the Apps Custom dashboard's three-template
  // sizing (half / normal / double), the gear-icon flip animation
  // (card rotates 180° to reveal a back-face settings pane), and the
  // per-widget configuration (clock TZ + format + analog/digital;
  // weather location + units + forecast days + show-conditions).
  // Persisted via the existing ui_prefs.apps_custom_layout channel —
  // the per-item `opts` sub-dict carries everything.
  // ============================================================

  // Resolve the card-size CSS class for ONE Custom-layout item.
  // Default 'normal' when no override is set. Used by the apps-card.html
  // + apps-widget-tile.html + apps-bookmark-tile.html `:class`
  // bindings to swap the grid-span + content-density rules.
  // Per-section status rollup for the section-header chips (up /
  // degraded / down / unknown counts across the section's APP items).
  // Widgets + bookmarks have no probe status so they're skipped. NOT
  // memoised on purpose: the only consumers are lightweight header chips
  // (x-text / x-show, not an x-for), and a flush-memo with a cache-hit
  // early-return would freeze them when an app's status changes via the
  // in-place reconcile (see the perf "freeze" caveat). The loop is tiny
  // (a handful of sections, few items each) so always-compute is cheap.
  appsSectionCounts(sec) {
    let up = 0, degraded = 0, down = 0, unknown = 0;
    const items = (sec && sec.items) || [];
    for (const it of items) {
      if (!it || it.kind !== 'app' || !it.app) {
        continue;
      }
      const s = it.app.status;
      if (s === 'up') {
        up++;
      } else if (s === 'degraded') {
        degraded++;
      } else if (s === 'down') {
        down++;
      } else {
        unknown++;
      }
    }
    return {up, degraded, down, unknown, total: up + degraded + down + unknown};
  },
  appsCardSizeClass(item) {
    const size = item && item.opts && item.opts.size;
    if (size === 'half' || size === 'double' || size === 'xlarge') {
      return 'apps-card--size-' + size;
    }
    return 'apps-card--size-normal';
  },
  // Just the size value (without the class prefix) — used by the
  // back-face radio bindings for `:checked` selection state.
  appsCardSize(item) {
    const size = item && item.opts && item.opts.size;
    return (size === 'half' || size === 'double' || size === 'xlarge') ? size : 'normal';
  },
  // True when the card should render its body skeleton-only (just
  // title + icon at a small footprint). Same gate the body-render
  // templates consult — half-size cards hide their heavy body subtree
  // so they read as a "what's pinned here" placeholder.
  appsCardIsSkeleton(item) {
    return this.appsCardSize(item) === 'half';
  },

  // Mutate the per-card size + persist. Cycles ascending half → normal
  // → double → xlarge → (wrap) half when called without an explicit
  // size. Called from the size-cycle tap on the front face (quick-toggle
  // in edit mode) AND from the back-face size radio.
  setAppsCardSize(uid, size) {
    if (!uid) {
      return;
    }
    const it = this._findAppsItemByUid(uid);
    if (!it) {
      return;
    }
    if (!it.opts) {
      it.opts = {};
    }
    if (size === 'half' || size === 'normal' || size === 'double' || size === 'xlarge') {
      it.opts.size = size;
    } else {
      // Cycle ascending through the four width presets, wrapping at the end.
      const order = ['half', 'normal', 'double', 'xlarge'];
      const idx = order.indexOf(it.opts.size || 'normal');
      it.opts.size = order[(idx + 1) % order.length];
    }
    this._persistAppsCustomLayout();
  },

  // ---- HEIGHT presets (parallel to the width/size system above) -----
  // Every card / widget / bookmark is locked to one of TWO fixed
  // heights — `short` or `tall` — so a card can never grow past its
  // preset (same containment guarantee the width presets give on the
  // horizontal axis). Default is `short`. Stored under
  // `item.opts.height`; the `.apps-card--height-<h>` class caps the
  // flip cell's height in CSS.
  appsCardHeightClass(item) {
    const h = item && item.opts && item.opts.height;
    return (h === 'tall') ? 'apps-card--height-tall' : 'apps-card--height-short';
  },
  // Just the height value (without the class prefix) — back-face radio
  // `:checked` state + the drag-handle's start index.
  appsCardHeight(item) {
    const h = item && item.opts && item.opts.height;
    return (h === 'tall') ? 'tall' : 'short';
  },
  // Mutate the per-card height + persist. Explicit value sets it; a
  // null/absent arg TOGGLES short ↔ tall (the two-preset cycle).
  setAppsCardHeight(uid, height) {
    if (!uid) {
      return;
    }
    const it = this._findAppsItemByUid(uid);
    if (!it) {
      return;
    }
    if (!it.opts) {
      it.opts = {};
    }
    if (height === 'short' || height === 'tall') {
      it.opts.height = height;
    } else {
      it.opts.height = (it.opts.height === 'tall') ? 'short' : 'tall';
    }
    this._persistAppsCustomLayout();
  },
  // Edit-mode VERTICAL resize control — mirror of appsSizeControl but on
  // the Y axis + only TWO presets. Tap toggles short ↔ tall; a vertical
  // drag snaps between the two by drag distance. Suppresses the cell's
  // reorder-drag for the gesture duration (same _appsResizing guard the
  // width handle uses).
  appsHeightControl(ev, uid) {
    if (!uid) {
      return;
    }
    const item = this._findAppsItemByUid(uid);
    if (!item) {
      return;
    }
    this._appsResizing = true;
    const heights = ['short', 'tall'];
    const startY = (ev && typeof ev.clientY === 'number') ? ev.clientY : 0;
    let startIdx = heights.indexOf(this.appsCardHeight(item));
    if (startIdx < 0) {
      startIdx = 0;
    }
    let dragged = false;
    const STEP = 56;  // px of vertical drag per preset step
    const self = this;
    const move = (e) => {
      const delta = e.clientY - startY;
      if (Math.abs(delta) > 4) {
        dragged = true;
      }
      let idx = startIdx + Math.round(delta / STEP);
      idx = Math.max(0, Math.min(heights.length - 1, idx));
      if (heights[idx] !== self.appsCardHeight(item)) {
        self.setAppsCardHeight(uid, heights[idx]);
      }
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      if (!dragged) {
        self.setAppsCardHeight(uid, null);  // tap = toggle
      }
      setTimeout(() => {
        self._appsResizing = false;
      }, 0);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  },
  // Edit-mode size control — ONE pointer handler that's both a TAP and
  // a DRAG: a tap cycles the preset (old behaviour), a horizontal drag
  // snaps between the three presets (half / normal / double) by drag
  // distance, so the operator can "drag to resize" as requested. The
  // cell's `appsCardSizeClass` updates live as `setAppsCardSize` mutates
  // the layout, so the tile visibly widens / narrows while dragging.
  appsSizeControl(ev, uid) {
    if (!uid) {
      return;
    }
    const item = this._findAppsItemByUid(uid);
    if (!item) {
      return;
    }
    // Suppress the cell's HTML5 reorder-drag for the duration of this
    // resize gesture. The cell is `draggable=true` for reorder, so a
    // pointer-drag that STARTS on this size button would otherwise fire
    // the cell's dragstart (move mode) instead of resizing — the
    // operator-reported "cards go into move mode when trying to drag the
    // edges". Set synchronously in this pointerdown handler (which fires
    // BEFORE dragstart) so the cell's `@dragstart` guard preventDefaults
    // the move. Cleared in `up`.
    this._appsResizing = true;
    const sizes = ['half', 'normal', 'double', 'xlarge'];
    const startX = (ev && typeof ev.clientX === 'number') ? ev.clientX : 0;
    let startIdx = sizes.indexOf(this.appsCardSize(item));
    if (startIdx < 0) {
      startIdx = 1;
    }
    let dragged = false;
    const STEP = 64;  // px of horizontal drag per preset step
    const self = this;
    const move = (e) => {
      const delta = e.clientX - startX;
      if (Math.abs(delta) > 4) {
        dragged = true;
      }
      let idx = startIdx + Math.round(delta / STEP);
      idx = Math.max(0, Math.min(sizes.length - 1, idx));
      if (sizes[idx] !== self.appsCardSize(item)) {
        self.setAppsCardSize(uid, sizes[idx]);
      }
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      // A clean tap (no drag) keeps the legacy click-to-cycle behaviour.
      if (!dragged) {
        self.setAppsCardSize(uid, null);
      }
      // Re-enable the cell's reorder-drag now the resize gesture is
      // done. Deferred one tick so a trailing dragstart from this same
      // gesture (some browsers fire it late) still sees the flag set.
      setTimeout(() => {
        self._appsResizing = false;
      }, 0);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  },

  // Flip-state tracker — module-scope Set so a re-render doesn't
  // reset the flipped flag (operator pops gear, flips the card, the
  // 30s poll re-evaluates the template → we want the back face to
  // stay visible across the poll). Keyed by item.uid.
  appsCardIsFlipped(uid) {
    if (!this._appsFlippedCards) {
      this._appsFlippedCards = {};
    }
    return !!this._appsFlippedCards[uid];
  },
  toggleAppsCardFlipped(uid) {
    if (!uid) {
      return;
    }
    if (!this._appsFlippedCards) {
      this._appsFlippedCards = {};
    }
    this._appsFlippedCards[uid] = !this._appsFlippedCards[uid];
  },
  closeAppsCardFlipped(uid) {
    if (uid && this._appsFlippedCards) {
      this._appsFlippedCards[uid] = false;
    }
  },

  // Find ONE persisted item across every section + unsectioned by
  // uid. Returns the raw layout item (not the rendered view-model)
  // so mutations persist via _persistAppsCustomLayout.
  _findAppsItemByUid(uid) {
    if (!this.appsCustomLayout || !Array.isArray(this.appsCustomLayout.sections)) {
      return null;
    }
    for (const sec of this.appsCustomLayout.sections) {
      if (!Array.isArray(sec.items)) {
        continue;
      }
      for (const it of sec.items) {
        if (it.uid === uid) {
          return it;
        }
      }
    }
    return null;
  },

  // Generic per-item-opts setter. Use case: back-face form bindings
  // call `setAppsItemOpt(uid, 'clock_tz', 'Europe/Cairo')` etc.
  // Numeric coercion + sanitisation lives in `_normAppsItemOpts`
  // — this setter only writes through; the next save round-trip
  // re-runs the sanitiser on hydration.
  setAppsItemOpt(uid, key, value) {
    if (!uid || !key) {
      return;
    }
    const it = this._findAppsItemByUid(uid);
    if (!it) {
      return;
    }
    if (!it.opts) {
      it.opts = {};
    }
    if (value === null || value === undefined || value === '') {
      delete it.opts[key];
    } else {
      it.opts[key] = value;
    }
    this._persistAppsCustomLayout();
  },

  // Set a TOP-LEVEL field on a custom-layout item (name / url / icon),
  // not an opts key — used by the bookmark card-settings editor so the
  // operator can rename a bookmark / change its URL or icon from the
  // gear flip. Persists immediately via the same ui_prefs layout
  // channel as setAppsItemOpt. The icon resolves through
  // appsBookmarkIconUrl (brand resolver + explicit-slug file fallback).
  setAppsItemField(uid, key, value) {
    if (!uid || !key) {
      return;
    }
    const it = this._findAppsItemByUid(uid);
    if (!it) {
      return;
    }
    it[key] = value;
    this._persistAppsCustomLayout();
  },

  // Gear-flip "Show extras" — EFFECTIVE checked state for the card. Reads
  // the resolved show_extras (per-instance override → catalog default →
  // size preset) via appsShowExtras on the card's first instance, so the
  // checkbox reflects what's actually rendered (and matches the Admin →
  // Apps instance editor once an explicit toggle has been made).
  appsCardShowExtrasChecked(item) {
    const app = item && item.app;
    const inst = (app && Array.isArray(app.instances)) ? app.instances[0] : null;
    return !!this.appsShowExtras(app, inst, item);
  },

  // Gear-flip "Show extras" — toggle handler. Writes the per-instance
  // show_extras (NOT the catalog flag) via the admin PATCH; the backend
  // syncs the value across EVERY instance of the app (same catalog_id),
  // so the gear-flip + the Admin → Apps instance editor stay identical and
  // change together. Optimistically mirrors onto the card's instances, then
  // reloads so the fleet-wide sync + the extras re-render land. Admin-gated
  // in the markup (the PATCH endpoint enforces it too).
  async appsSetCardShowExtras(item, checked) {
    const app = item && item.app;
    const inst = (app && Array.isArray(app.instances)) ? app.instances[0] : null;
    if (!inst || !inst.host_id || inst.service_idx == null) {
      return;
    }
    const value = !!checked;
    try {
      const r = await fetch('/api/services/' + encodeURIComponent(inst.host_id)
        + '/' + encodeURIComponent(inst.service_idx), {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({show_extras: value}),
      });
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      (app.instances || []).forEach((i) => {
        if (i) {
          i.show_extras = value;
        }
      });
      await this.loadAppsList(true);
    } catch (err) {
      const msg = (err && err.message) ? err.message : String(err);
      if (typeof this.showToast === 'function') {
        this.showToast(msg, 'error');
      } else {
        console.error('[apps] set card show_extras failed:', msg);
      }
    }
  },

  // Clamp a typed averages-window value to the valid range [2, 60], or ''
  // when blank (= "use the app default"). Shared by the gear-flip card
  // setting + the Admin → Apps instance editor so both enforce the SAME
  // bounds the backend does — a number input's min/max only bounds the
  // spinner, not typed values, so typing 90 must clamp to 60 on the spot.
  appsClampAvgWindow(v) {
    const s = String(v == null ? '' : v).trim();
    if (s === '') {
      return '';
    }
    const n = parseInt(s, 10);
    if (!Number.isFinite(n)) {
      return '';
    }
    return Math.max(2, Math.min(60, n));
  },

  // Per-instance data-cache TTL clamp (seconds, 5..3600) — same blank-
  // passthrough contract as appsClampAvgWindow ('' => app default).
  appsClampCacheTtl(v) {
    const s = String(v == null ? '' : v).trim();
    if (s === '') {
      return '';
    }
    const n = parseInt(s, 10);
    if (!Number.isFinite(n)) {
      return '';
    }
    return Math.max(5, Math.min(3600, n));
  },

  // Current per-instance averages window for a card's app (Speedtest),
  // read from the card's FIRST instance. '' when unset (the app default
  // 10 applies). Seeds the gear-flip "Averages window" number input.
  appChipAvgWindow(item) {
    const app = item && item.app;
    const inst = (app && Array.isArray(app.instances)) ? app.instances[0] : null;
    return (inst && inst.avg_window != null && inst.avg_window !== '')
      ? inst.avg_window : '';
  },

  // Set the per-instance averages window from the gear-flip card settings.
  // Unlike setAppsItemOpt (per-USER card OPTIONS → ui_prefs), this writes
  // CHIP data (hosts_config) via the ADMIN-only PATCH
  // /api/services/{host}/{idx} — the markup gates the control on isAdmin().
  // Resolves the instance from the card's first instance (Speedtest is
  // single-instance per card). Blank => clears the override (app default
  // 10); a value is clamped 2..60. Refreshes the per-app data so the
  // "Avg of last N" labels re-render with the new window.
  async setAppChipAvgWindow(item, value) {
    const app = item && item.app;
    const inst = (app && Array.isArray(app.instances)) ? app.instances[0] : null;
    if (!inst || !inst.host_id || inst.service_idx == null) {
      return;
    }
    // '' clears the override (backend → app default); else clamped 2..60.
    const send = this.appsClampAvgWindow(value);
    try {
      const r = await fetch('/api/services/' + encodeURIComponent(inst.host_id)
        + '/' + encodeURIComponent(inst.service_idx), {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({avg_window: send}),
      });
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      // Optimistic local reflect, then refresh per-app data so the
      // averages re-window with the new value.
      inst.avg_window = (send === '' ? null : send);
      if (typeof this.loadAppData === 'function') {
        await this.loadAppData(inst, true);
      }
    } catch (err) {
      const msg = (err && err.message) ? err.message : String(err);
      if (typeof this.showToast === 'function') {
        this.showToast(msg, 'error');
      } else {
        console.error('[apps] set avg_window failed:', msg);
      }
    }
  },

  // Persist the ACTIVE view's layout to the server (app_views) — fire-and-
  // forget PUT, called by every board mutator (drag / drop / resize / collapse
  // / section CRUD). appsCustomLayout is a live reference to the active view's
  // layout so it's already current; we sync it onto the active view object
  // then PUT just that view. NO-OP when the active view is read-only to the
  // caller (a public read-only view, or a read-only-role user on a public-
  // editable view) — the UI also blocks those edits; this is defence-in-depth.
  _persistAppsCustomLayout() {
    this._hydrateAppsCustomLayout();
    const av = this._appsActiveViewObj();
    if (!av || !av.id) {
      return;
    }
    if (av.can_edit === false) {
      return;  // view-only — nothing to persist
    }
    av.layout = this.appsCustomLayout;
    const layout = {
      sections: av.layout.sections || [],
      unsectioned_collapsed: !!av.layout.unsectioned_collapsed,
    };
    try {
      fetch('/api/apps/views/' + encodeURIComponent(av.id), {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({layout}),
      }).catch(() => { /* in-memory already updated */
      });
    } catch (_) { /* ignore */
    }
  },

  // ── View-management API (Apps custom dashboard) ────────────────
  // The picker UI binds to these. All mutate appsCustomViews + persist via
  // _persistAppsCustomLayout (which serializes the whole collection). After a
  // structural change that affects the rendered board (switch / delete) the
  // per-flush build memo is cleared so the next render rebuilds for the new
  // active layout.
  appsViews() {
    this._hydrateAppsCustomLayout();
    return (this.appsCustomViews && Array.isArray(this.appsCustomViews.views)) ? this.appsCustomViews.views : [];
  },
  appsActiveViewId() {
    this._hydrateAppsCustomLayout();
    return this.appsCustomViews ? this.appsCustomViews.active_id : null;
  },
  appsActiveViewName() {
    this._hydrateAppsCustomLayout();
    const av = this._appsActiveViewObj();
    return av ? av.name : '';
  },
  appsSwitchView(id) {
    this._hydrateAppsCustomLayout();
    if (!this.appsCustomViews || !id || id === this.appsCustomViews.active_id) {
      return;
    }
    const target = this.appsCustomViews.views.find(v => v && v.id === id);
    if (!target) {
      return;
    }
    // Show the loading indicator FIRST, then yield (setTimeout 0) so the
    // browser paints the spinner BEFORE the heavy layout swap + tile
    // re-mount runs (a synchronous rebuild would otherwise block the
    // thread and the spinner would never appear). Alpine flushes the
    // flag on a microtask + the browser paints before the macrotask
    // fires, so the spinner is on-screen when the heavy work starts.
    this.appsViewSwitching = true;
    setTimeout(() => {
      this.appsCustomViews.active_id = id;
      this.appsCustomLayout = target.layout;
      _appsCustomBuildCache = null;  // force rebuild for the new active layout
      this.appsViewRenaming = null;
      // A read-only view (public read-only you don't own, or a readonly-role
      // user on a public-editable one) can't be edited — drop out of edit mode.
      if (target.can_edit === false) {
        this.appsCustomEditMode = false;
      }
      this._persistActiveViewId(id);  // remember the selection per-user
      // Drop the spinner after the new board has had a frame to paint
      // its first tiles (the per-tile lazy-mount queue handles the rest
      // with its own skeletons).
      requestAnimationFrame(() => requestAnimationFrame(() => {
        this.appsViewSwitching = false;
      }));
    }, 0);
  },
  // ── Duplicate / remove app-card copies (custom dashboard) ──────────
  // Duplicate-app feature: an app card can be copied into the same
  // section (then dragged elsewhere) so one service shows on multiple
  // dashboards / sections. The first placement of a ref is the "main"
  // copy; duplicates are "shadows". Each copy carries its OWN opts
  // (size / height), so shadows can be sized independently.

  // Add a shadow copy of the app item `uid` right after it in the SAME
  // section. The copy gets a fresh uid + a clone of the source opts (so
  // it starts at the same size, then resizes independently). The user
  // drags it to another section afterwards. App items only.
  appsDuplicateAppItem(uid) {
    this._hydrateAppsCustomLayout();
    const secs = (this.appsCustomLayout && this.appsCustomLayout.sections) || [];
    // Find the owning section without an explicit loop so the findIndex
    // closure isn't declared inside a for-body (JSHint W083).
    const s = secs.find(sec => (sec.items || []).some(it => it && it.uid === uid));
    if (!s) {
      return;
    }
    const items = s.items || [];
    const idx = items.findIndex(it => it && it.uid === uid);
    const src = items[idx];
    if (!src || src.kind !== 'app') {
      return;  // only app cards are duplicable
    }
    const copy = {
      uid: this._newId('it-'),
      kind: 'app',
      ref: src.ref,
      opts: {...(src.opts || {})},
    };
    items.splice(idx + 1, 0, copy);
    _appsCustomBuildCache = null;
    this._persistAppsCustomLayout();
  },
  // Remove the app-card copy `uid`. Guarded: refuses to remove the MAIN
  // (first-in-document-order occurrence of the ref) — the main is removed
  // only by dragging it back to Unsectioned. Shadows are freely
  // removable; when the last copy is gone the app returns to Unsectioned
  // automatically (see _buildAppsCustom's assignedRefs).
  appsRemoveAppItem(uid) {
    this._hydrateAppsCustomLayout();
    const secs = (this.appsCustomLayout && this.appsCustomLayout.sections) || [];
    // Locate the owning section without an explicit loop so the findIndex
    // closure isn't declared inside a for-body (JSHint W083).
    const targetSec = secs.find(sec => (sec.items || []).some(it => it && it.uid === uid));
    const targetIdx = targetSec ? targetSec.items.findIndex(it => it && it.uid === uid) : -1;
    const target = targetSec ? targetSec.items[targetIdx] : null;
    if (!target || target.kind !== 'app') {
      return;
    }
    // Main-guard: the FIRST occurrence of this ref in document order is
    // the main copy and can't be deleted here.
    let firstUid = null;
    for (const s of secs) {
      for (const it of (s.items || [])) {
        if (it && it.kind === 'app' && it.ref === target.ref) {
          firstUid = it.uid;
          break;
        }
      }
      if (firstUid) {
        break;
      }
    }
    if (firstUid === uid) {
      return;  // can't remove the main copy
    }
    targetSec.items.splice(targetIdx, 1);
    _appsCustomBuildCache = null;
    this._persistAppsCustomLayout();
  },
  // The active view object (with visibility / edit_permission / ownership
  // flags) — the UI binds the Share control + badges + read-only gating to it.
  appsActiveView() {
    this._hydrateAppsCustomLayout();
    return this._appsActiveViewObj();
  },
  // Whether the caller may rearrange the ACTIVE board. False for a public
  // read-only view you don't own, or a read-only-role user on an editable
  // public view. Gates the Edit toggle + every board mutator. Defaults true
  // while the collection is still loading (no destructive edits possible yet).
  appsCanEditActiveView() {
    const av = this.appsActiveView();
    return !av || av.can_edit !== false;
  },
  async appsCreateView(name) {
    const nm = (typeof name === 'string' && name.trim()) ? name.trim().slice(0, 64) : this._appsViewDefaultName();
    const row = await this._postAppView({
      name: nm,
      layout: {sections: [], unsectioned_collapsed: false},
      visibility: 'private',
      edit_permission: 'owner',
    });
    if (!row) {
      return null;
    }
    const v = this._normServerView(row);
    if (!this.appsCustomViews) {
      this.appsCustomViews = {active_id: null, views: []};
    }
    this.appsCustomViews.views.push(v);
    this.appsCustomViews.active_id = v.id;
    this.appsCustomLayout = v.layout;
    _appsCustomBuildCache = null;
    this._persistActiveViewId(v.id);
    return v.id;
  },
  async appsDeleteView(id) {
    const v = this.appsCustomViews && this.appsCustomViews.views.find(x => x && x.id === id);
    if (!v) {
      return;
    }
    try {
      const r = await fetch('/api/apps/views/' + encodeURIComponent(id), {method: 'DELETE'});
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
    } catch (err) {
      this._appViewError(err);
      return;
    }
    const i = this.appsCustomViews.views.findIndex(x => x && x.id === id);
    if (i < 0) {
      return;
    }
    const wasActive = this.appsCustomViews.active_id === id;
    this.appsCustomViews.views.splice(i, 1);
    this.appsViewRenaming = null;
    if (!this.appsCustomViews.views.length) {
      // Deleted the last visible view — seed a fresh default so the board is
      // never empty.
      await this.appsCreateView('');
      return;
    }
    if (wasActive) {
      const next = this.appsCustomViews.views[Math.max(0, i - 1)];
      this.appsCustomViews.active_id = next.id;
      this.appsCustomLayout = next.layout;
      _appsCustomBuildCache = null;
      this._persistActiveViewId(next.id);
    }
  },
  async appsDuplicateView(id) {
    const src = this.appsCustomViews && this.appsCustomViews.views.find(v => v && v.id === id);
    if (!src) {
      return null;
    }
    // Deep-clone the layout, re-minting section + item uids so the copy is
    // fully independent (no shared :key collisions). The copy is always a NEW
    // PRIVATE view owned by the caller — duplicating a public view you don't
    // own gives you your own editable private copy.
    const cloneSrc = {
      sections: (src.layout.sections || []).map(s => ({
        id: this._newId('sec-'),
        name: s.name,
        collapsed: s.collapsed,
        items: (s.items || []).map(it => Object.assign({}, it, {uid: this._newId('it-')})),
      })),
      unsectioned_collapsed: !!src.layout.unsectioned_collapsed,
    };
    const copyName = ((this.t && this.t('apps.custom.view_copy_suffix', {name: src.name})) || (src.name + ' (copy)')).slice(0, 64);
    const row = await this._postAppView({
      name: copyName,
      layout: cloneSrc,
      visibility: 'private',
      edit_permission: 'owner',
    });
    if (!row) {
      return null;
    }
    const v = this._normServerView(row);
    this.appsCustomViews.views.push(v);
    this.appsCustomViews.active_id = v.id;
    this.appsCustomLayout = v.layout;
    _appsCustomBuildCache = null;
    this._persistActiveViewId(v.id);
    return v.id;
  },
  // ── Sharing control (owner only — the route enforces it too) ───────────
  // One 3-way choice maps to (visibility, edit_permission):
  //   private          → (private, owner)
  //   public_readonly  → (public,  owner)   everyone can view, only owner edits
  //   public_editable  → (public,  all)     anyone except read-only-role edits
  // ── Unified "Dashboard settings" modal (rename + share in one dialog) ──────
  // The 3-way sharing choice maps to (visibility, edit_permission):
  //   private          → (private, owner)
  //   public_readonly  → (public,  owner)   everyone views, only owner edits
  //   public_editable  → (public,  all)     anyone except read-only-role edits
  _viewChoiceFrom(v) {
    if (!v || v.visibility !== 'public') {
      return 'private';
    }
    return v.edit_permission === 'all' ? 'public_editable' : 'public_readonly';
  },
  // The sharing-option descriptors the modal's radiogroup renders (icon +
  // title + subtitle, all i18n'd). One place so the list + labels stay DRY.
  appsShareOptions() {
    const t = (k, fb) => (this.t && this.t(k)) || fb;
    return [
      {
        value: 'private', icon: 'icon-lock',
        title: t('apps.custom.share_opt_private_title', 'Private'),
        sub: t('apps.custom.share_opt_private_sub', 'Only you can see this dashboard.'),
      },
      {
        value: 'public_readonly', icon: 'icon-eye',
        title: t('apps.custom.share_opt_readonly_title', 'Public · Read-only'),
        sub: t('apps.custom.share_opt_readonly_sub', 'Everyone can view it; only you can change it.'),
      },
      {
        value: 'public_editable', icon: 'icon-users',
        title: t('apps.custom.share_opt_editable_title', 'Public · Editable'),
        sub: t('apps.custom.share_opt_editable_sub', 'Everyone can view it; anyone except read-only users can rearrange it.'),
      },
    ];
  },
  // Open the settings modal for a view the caller may edit (owner sees the
  // sharing section; an editor sees the name only). Seeds focus on the name.
  openAppsViewSettings(id) {
    const v = this.appsViews().find(x => x && x.id === id);
    if (!v || v.can_edit === false) {
      return;  // read-only viewers can't edit
    }
    const choice = this._viewChoiceFrom(v);
    this.appsViewModal = {
      open: true,
      id: v.id,
      name: v.name || '',
      choice,
      orig_name: v.name || '',
      orig_choice: choice,
      can_manage: !!v.can_manage,
      saving: false,
    };
    this.$nextTick(() => {
      const el = this.$refs && this.$refs.appsViewName;
      if (el) {
        el.focus();
        el.select();
      }
    });
  },
  closeAppsViewSettings() {
    this.appsViewModal.open = false;
  },
  async saveAppsViewSettings() {
    const m = this.appsViewModal;
    if (!m || !m.open || m.saving) {
      return;
    }
    const name = (m.name || '').trim().slice(0, 64);
    if (!name) {
      return;  // name is required (Save button is also disabled when blank)
    }
    // Build a single PUT with only the changed fields. name/layout edits are
    // allowed for editors; visibility/edit_permission are owner-only (and the
    // sharing section only renders for can_manage), so we only send them then.
    const body = {};
    if (name !== m.orig_name) {
      body.name = name;
    }
    if (m.can_manage && m.choice !== m.orig_choice) {
      body.visibility = m.choice === 'private' ? 'private' : 'public';
      body.edit_permission = m.choice === 'public_editable' ? 'all' : 'owner';
    }
    if (!Object.keys(body).length) {
      this.closeAppsViewSettings();  // nothing changed
      return;
    }
    m.saving = true;
    try {
      const updated = await this._putAppView(m.id, body);
      if (updated) {
        this._applyServerViewUpdate(updated);
        this.closeAppsViewSettings();
      }
    } finally {
      m.saving = false;
    }
  },
  // A short i18n label for the active view's sharing state — drives the badge
  // in the picker. Returns '' for a private owned view (no badge needed).
  appsViewShareLabel(v) {
    if (!v) {
      return '';
    }
    if (!v.is_owner) {
      // Someone else's public view — show who shared it.
      return (this.t && this.t('apps.custom.shared_by', {owner: v.owner_username || '?'}))
        || ('Shared by ' + (v.owner_username || '?'));
    }
    if (v.visibility === 'public') {
      return v.edit_permission === 'all'
        ? (this.t('apps.custom.badge_public_editable') || 'Public · Editable')
        : (this.t('apps.custom.badge_public_readonly') || 'Public · Read-only');
    }
    return '';  // private + owned — no badge
  },
  // PUT one view; returns the server row (or null on failure).
  async _putAppView(id, body) {
    try {
      const r = await fetch('/api/apps/views/' + encodeURIComponent(id), {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      const j = await r.json();
      return j.view || null;
    } catch (err) {
      this._appViewError(err);
      return null;
    }
  },
  // Merge a server-returned view row back onto the live collection in place
  // (preserves the array identity Alpine tracks). Re-points appsCustomLayout
  // when the updated view is the active one.
  _applyServerViewUpdate(serverView) {
    if (!this.appsCustomViews || !serverView || !serverView.id) {
      return;
    }
    const nv = this._normServerView(serverView);
    const existing = this.appsCustomViews.views.find(v => v && v.id === nv.id);
    if (existing) {
      existing.name = nv.name;
      existing.visibility = nv.visibility;
      existing.edit_permission = nv.edit_permission;
      existing.owner_username = nv.owner_username;
      existing.is_owner = nv.is_owner;
      existing.can_edit = nv.can_edit;
      existing.can_manage = nv.can_manage;
      existing.updated_at = nv.updated_at;
    } else {
      this.appsCustomViews.views.push(nv);
    }
    if (this.appsCustomViews.active_id === nv.id) {
      const av = this._appsActiveViewObj();
      if (av) {
        this.appsCustomLayout = av.layout;
      }
    }
  },

  // UI handlers — themed Swal prompt / confirm wrappers the picker binds to.
  async appsPromptCreateView() {
    const result = await Swal.fire({
      title: this.t('apps.custom.view_new_title') || 'New view',
      input: 'text',
      inputPlaceholder: this.t('apps.custom.view_name_placeholder') || 'View name',
      inputAttributes: {maxlength: '64', autocapitalize: 'words'},
      showCancelButton: true,
      confirmButtonText: this.t('actions.create') || 'Create',
      cancelButtonText: this.t('actions.cancel') || 'Cancel',
      background: this._cssVar('--surface'),
      color: this._cssVar('--text'),
      confirmButtonColor: this._cssVar('--primary'),
      cancelButtonColor: this._cssVar('--btn-cancel-bg'),
    });
    if (!result.isConfirmed) {
      return;
    }
    await this.appsCreateView(result.value || '');
  },
  async appsConfirmDeleteView(id) {
    const v = this.appsViews().find(x => x && x.id === id);
    if (!v) {
      return;
    }
    // Deleting the last view seeds a fresh default (see appsDeleteView), so
    // there's no "keep at least one" gate anymore. Ownership is enforced by
    // the route + the button is hidden for non-owners; this is the confirm.
    const ok = await this.confirmDialog({
      title: this.t('apps.custom.view_delete_title') || 'Delete view',
      html: (this.t('apps.custom.view_delete_confirm', {name: v.name}) || ('Delete the "' + v.name + '" view? Its layout will be lost.')),
      icon: 'warning',
      confirmText: this.t('actions.delete') || 'Delete',
      confirmColor: this._cssVar('--danger'),
    });
    if (ok) {
      await this.appsDeleteView(id);
    }
  },

  // Host-grouped view of the filtered apps. Walks every app's per-host
  // instances and buckets them by host, so each host becomes a card
  // carrying the apps that run on it. Status rolls up to the worst
  // instance status on that host (down > degraded > unknown > up).
  // Each app entry carries the parent app's identity (group_id / name
  // / icon) merged with the per-host instance fields so the card can
  // render an icon tile + status dot and the drawer can show per-port
  // detail. Sorted by host label / address.
  appsHostGroups() {
    if (_appsHostGroupsCache !== null) {
      return _appsHostGroupsCache;
    }
    const apps = this.filteredApps();
    const byHost = {};
    const order = [];
    for (const app of apps) {
      for (const inst of (app.instances || [])) {
        const hid = inst.host_id || '';
        if (!byHost[hid]) {
          byHost[hid] = {
            host_id: hid,
            host_label: inst.host_label || '',
            host_address: inst.host_address || hid,
            apps: [],
          };
          order.push(hid);
        }
        byHost[hid].apps.push({
          group_id: app.group_id,
          name: app.name,
          icon: app.icon,
          catalog_name: (app.catalog && app.catalog.name) || '',
          status: inst.status,
          url: inst.url,
          service_idx: inst.service_idx,
          host_id: hid,
          host_label: inst.host_label || '',
          host_address: inst.host_address || hid,
          last_probe: inst.last_probe,
          port_results: inst.port_results,
          ports: inst.ports || [],
        });
      }
    }
    const rank = {up: 0, unknown: 1, degraded: 2, down: 3};
    // Primary (lowest) port for an app entry — from the chip's configured
    // probe.ports, else its probed port_results. Drives the port-ascending
    // sort within each host group (apps with no resolvable port sink last).
    const appPort = (a) => {
      const nums = [];
      for (const p of (a.ports || [])) {
        const n = Number(p && p.port);
        if (n > 0) {
          nums.push(n);
        }
      }
      if (!nums.length) {
        for (const pr of (a.port_results || [])) {
          const n = Number(pr && pr.port);
          if (n > 0) {
            nums.push(n);
          }
        }
      }
      return nums.length ? Math.min(...nums) : Number.MAX_SAFE_INTEGER;
    };
    const groups = order.map((hid) => {
      const g = byHost[hid];
      let worst = 'up';
      let up = 0;
      for (const a of g.apps) {
        if ((rank[a.status] || 0) > (rank[worst] || 0)) {
          worst = a.status;
        }
        if (a.status === 'up') {
          up++;
        }
      }
      g.status = g.apps.length ? worst : 'unknown';
      g.up_count = up;
      g.app_count = g.apps.length;
      g.apps.sort((a, b) => {
        const pa = appPort(a);
        const pb = appPort(b);
        if (pa !== pb) {
          return pa - pb;
        }
        return (a.name || '').toLowerCase().localeCompare((b.name || '').toLowerCase());
      });
      return g;
    });
    groups.sort((a, b) => (a.host_label || a.host_address || '').toLowerCase()
      .localeCompare((b.host_label || b.host_address || '').toLowerCase()));
    _appsHostGroupsCache = groups;
    if (!_appsHostGroupsScheduled) {
      _appsHostGroupsScheduled = true;
      queueMicrotask(() => {
        _appsHostGroupsCache = null;
        _appsHostGroupsScheduled = false;
      });
    }
    return groups;
  },

  // ----------------------------------------------------------------
  // Apps-by-host drawer — opened from a host card in the host-grouped
  // Apps view. Shows the host header + the list of apps running on it
  // (icon / status / url / per-port detail). Clicking an app row hands
  // off to the per-app drawer (openAppFromHostDrawer).
  // ----------------------------------------------------------------
  drawerAppHost: null,

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
    // Busy state so the button shows a spinner + disables while the
    // probe POST + appsList reload are in flight. Without it a click
    // produced NO visible feedback on success (only an error toast on
    // failure + a silent in-place reconcile), reading as "the button
    // does nothing". Mirrors the appSkillBusy / _appSkillBusy pattern.
    this._appProbeBusy = this._appProbeBusy || {};
    if (this._appProbeBusy[key]) {
      return;  // already probing this chip — ignore the double-click
    }
    this._appProbeBusy[key] = true;
    let ok = false;
    try {
      try {
        const r = await fetch('/api/services/' + encodeURIComponent(inst.host_id)
          + '/' + encodeURIComponent(inst.service_idx) + '/probe', {method: 'POST'});
        if (r.ok) {
          ok = true;
        } else {
          const detail = await this.fmtResponseError(r);
          this.showToast((this.t('apps.drawer.probe_failed') || 'Probe failed: ') + detail, 'error');
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
    } finally {
      this._appProbeBusy[key] = false;
    }
    if (ok) {
      this.showToast(this.t('apps.drawer.probe_done') || 'Probe complete', 'success');
    }
  },

  // True while a "Probe now" run is in flight for a given chip — drives
  // the drawer button's spinner + disabled state.
  appProbeBusy(inst) {
    const key = this.appInstanceKey(inst);
    return !!(key && this._appProbeBusy && this._appProbeBusy[key]);
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

  setAppsInstancesGroupBy(mode) {
    if (!['none', 'host', 'service'].includes(mode)) {
      return;
    }
    this.appsInstancesGroupBy = mode;
    try {
      localStorage.setItem('appsInstancesGroupBy', mode);
    } catch (_) {
      // private mode — in-memory only
    }
  },

  toggleAppsInstanceGroup(key) {
    this.appsInstancesCollapsed = Object.assign({}, this.appsInstancesCollapsed,
      {[key]: !this.appsInstancesCollapsed[key]});
  },

  // Collapse / expand every instance group at once. Operate over the
  // currently-rendered groups (respects the active group-by mode) so
  // the buttons only ever touch real header keys; the '__all__'
  // single-group ('none' mode) has no collapsible header so it's
  // skipped. Replace the whole map (Alpine proxy reactivity) rather
  // than mutating in place.
  collapseAllAppsInstanceGroups() {
    const next = {};
    for (const grp of this.appsInstancesGroups()) {
      if (grp.key !== '__all__') {
        next[grp.key] = true;
      }
    }
    this.appsInstancesCollapsed = next;
  },

  expandAllAppsInstanceGroups() {
    this.appsInstancesCollapsed = {};
  },

  // Grouped render structure for the instances table — one entry per
  // group: {key, label, count, items}. Rendered as one <tbody> per
  // group (valid HTML, keeps column alignment with the shared thead)
  // with a collapsible header row. 'none' returns a single group with
  // an empty key/label so the markup suppresses its header row.
  // Free-text filter for the Instances table — matches name / catalog /
  // host (id / label / address) / port number / protocol, case-insensitive.
  appsInstancesMatch(inst, q) {
    if (!inst) {
      return false;
    }
    const hay = [
      inst.name || '', inst.catalog_name || '', inst.catalog_slug || '',
      inst.host_id || '', inst.host_label || '', inst.host_address || '',
      (inst.ports || []).map((p) => (p && p.port != null ? p.port : '')).join(' '),
      (inst.ports || []).map((p) => (p && p.protocol) || '').join(' '),
    ].join(' ').toLowerCase();
    return hay.includes(q);
  },
  appsInstancesMatchCount() {
    const q = (this.appsInstancesSearch || '').trim().toLowerCase();
    const list = Array.isArray(this.appsInstances) ? this.appsInstances : [];
    if (!q) {
      return list.length;
    }
    return list.filter((inst) => this.appsInstancesMatch(inst, q)).length;
  },
  appsInstancesGroups() {
    let list = Array.isArray(this.appsInstances) ? this.appsInstances : [];
    const q = (this.appsInstancesSearch || '').trim().toLowerCase();
    if (q) {
      list = list.filter((inst) => this.appsInstancesMatch(inst, q));
    }
    const mode = this.appsInstancesGroupBy || 'host';
    if (mode === 'none') {
      return [{key: '__all__', label: '', count: list.length, items: list}];
    }
    const groups = {};
    const order = [];
    for (const inst of list) {
      let key;
      let label;
      if (mode === 'service') {
        label = inst.catalog_name || inst.name || (this.t('admin_apps.unlinked') || '— unlinked —');
        key = 'svc:' + label.toLowerCase();
      } else {
        key = 'host:' + (inst.host_id || '');
        label = (typeof this.appsInstanceHostTitle === 'function' ? this.appsInstanceHostTitle(inst) : '')
          || inst.host_address || inst.host_id;
      }
      if (!groups[key]) {
        groups[key] = {key, label, items: []};
        order.push(key);
      }
      groups[key].items.push(inst);
    }
    order.sort((a, b) => (groups[a].label || '').toLowerCase().localeCompare((groups[b].label || '').toLowerCase()));
    return order.map((key) => ({
      key, label: groups[key].label, count: groups[key].items.length, items: groups[key].items,
    }));
  },

  // ----------------------------------------------------------------
  // Admin → Apps — catalog template CRUD.
  // ----------------------------------------------------------------
  async loadAppsCatalog() {
    try {
      const r = await fetch('/api/services/catalog');
      if (!r.ok) {
        if (r.status === 401 || r.status === 403) {
          return;
        }
        throw new Error('HTTP ' + r.status);
      }
      const j = await r.json();
      this.appsCatalog = Array.isArray(j.entries) ? j.entries : [];
      this.appsCatalogLoaded = true;
    } catch (err) {
      this.appsCatalogStatus = err && err.message ? err.message : String(err);
    }
  },

  async loadAppsInstances() {
    try {
      const r = await fetch('/api/apps/instances');
      if (!r.ok) {
        if (r.status === 401 || r.status === 403) {
          return;
        }
        throw new Error('HTTP ' + r.status);
      }
      const j = await r.json();
      this.appsInstances = Array.isArray(j.instances) ? j.instances : [];
      this.appsInstancesStatus = '';
      this.appsInstancesLoaded = true;
    } catch (err) {
      // Mirror loadAppsCatalog: on a transient blip, record the error
      // but LEAVE appsInstances intact so a prior good list survives
      // (don't blank the table). Mark loaded so the skeleton clears.
      this.appsInstancesStatus = err && err.message ? err.message : String(err);
      this.appsInstancesLoaded = true;
    }
  },

  openAppCatalogNew() {
    this.appsCatalogEdit = {
      id: null,
      name: '',
      slug: '',
      icon: '',
      description: '',
      default_ports: [],
    };
    this.appsCatalogEditError = '';
    this.appsCatalogEditOpen = true;
    // Templates tab is the only place the editor lives. Force the
    // tab strip back to Templates when opening so the editor is
    // visible even if the operator is currently on Instances.
    this.appsAdminTab = 'templates';
    this._scrollAppsEditorIntoView();
  },

  openAppCatalogEdit(entry) {
    if (!entry) {
      return;
    }
    this.appsCatalogEdit = {
      id: entry.id,
      name: entry.name || '',
      slug: entry.slug || '',
      icon: entry.icon || '',
      description: entry.description || '',
      // Deep-clone the port list so editor edits don't mutate the
      // table's reactive row before Save.
      default_ports: JSON.parse(JSON.stringify(entry.default_ports || [])),
    };
    this.appsCatalogEditError = '';
    this.appsCatalogEditOpen = true;
    this.appsAdminTab = 'templates';
    this._scrollAppsEditorIntoView();
  },

  // Smooth-scroll the editor anchor into view after Alpine renders the
  // x-show'd block. Double `$nextTick` covers the case where the
  // editor's parent (the Templates tab) was hidden when the click
  // landed; first tick reveals the tab, second tick measures the
  // newly-laid-out editor. Best-effort — silent no-op if the anchor
  // isn't in the DOM (template not loaded, ancestor display:none, etc.).
  _scrollAppsEditorIntoView() {
    if (!this.$nextTick) {
      return;
    }
    this.$nextTick(() => {
      this.$nextTick(() => {
        const anchor = document.querySelector('[data-apps-editor-anchor]');
        if (anchor && anchor.scrollIntoView) {
          anchor.scrollIntoView({behavior: 'smooth', block: 'start'});
        }
        // Move keyboard focus to the first text input inside the
        // editor so the operator can start typing immediately.
        const firstInput = anchor && anchor.querySelector('input[type="text"]');
        if (firstInput && firstInput.focus) {
          firstInput.focus({preventScroll: true});
        }
      });
    });
  },

  // Tab strip click handler — close the editor when leaving Templates
  // so the form doesn't linger on the Instances tab (where it would
  // sit awkwardly below the instance table with no visible header).
  setAppsAdminTab(tab) {
    if (this.appsAdminTab === tab) {
      return;
    }
    if (tab !== 'templates' && this.appsCatalogEditOpen) {
      this.closeAppCatalogEditor();
    }
    this.appsAdminTab = tab;
  },

  closeAppCatalogEditor() {
    this.appsCatalogEditOpen = false;
    this.appsCatalogEdit = {};
    this.appsCatalogEditError = '';
  },

  addAppCatalogPort() {
    if (!this.appsCatalogEdit.default_ports) {
      this.appsCatalogEdit.default_ports = [];
    }
    this.appsCatalogEdit.default_ports.push({
      port: 80,
      protocol: 'tcp',
      label: '',
      probe_path: '/',
      probe_status: 0,
      open_url: false,
    });
  },

  removeAppCatalogPort(idx) {
    if (!this.appsCatalogEdit.default_ports) {
      return;
    }
    if (idx < 0 || idx >= this.appsCatalogEdit.default_ports.length) {
      return;
    }
    this.appsCatalogEdit.default_ports.splice(idx, 1);
  },

  async saveAppCatalogEntry() {
    if (this.appsCatalogSaving) {
      return;
    }
    const entry = this.appsCatalogEdit || {};
    if (!entry.name || !entry.name.trim()) {
      this.appsCatalogEditError = this.t('admin_apps.error_name_required') || 'Name is required';
      return;
    }
    this.appsCatalogSaving = true;
    this.appsCatalogEditError = '';
    const body = {
      name: entry.name.trim(),
      slug: (entry.slug || '').trim(),
      icon: (entry.icon || '').trim(),
      description: (entry.description || '').trim(),
      default_ports: entry.default_ports || [],
    };
    try {
      const url = entry.id
        ? `/api/services/catalog/${entry.id}`
        : '/api/services/catalog';
      const method = entry.id ? 'PATCH' : 'POST';
      const r = await fetch(url, {
        method,
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      await this.loadAppsCatalog();
      this.closeAppCatalogEditor();
      if (typeof this.toast === 'function') {
        this.toast(this.t('admin_apps.saved') || 'Template saved', 'success');
      }
    } catch (err) {
      this.appsCatalogEditError = err && err.message ? err.message : String(err);
    } finally {
      this.appsCatalogSaving = false;
    }
  },

  async deleteAppCatalogEntry(entry) {
    if (!entry || !entry.id) {
      return;
    }
    const confirmed = typeof this.confirmDialog === 'function'
      ? await this.confirmDialog({
        title: this.t('admin_apps.delete_confirm_title', {name: entry.name || ''})
          || ('Delete ' + (entry.name || 'template') + '?'),
        text: this.t('admin_apps.delete_confirm_text')
          || 'Per-host chips linked to this template will keep their own name + icon, just lose the catalog binding.',
        icon: 'warning',
        confirmButtonText: this.t('actions.delete') || 'Delete',
      })
      : window.confirm('Delete "' + (entry.name || 'template') + '"?');
    if (!confirmed) {
      return;
    }
    try {
      const r = await fetch(`/api/services/catalog/${entry.id}`, {method: 'DELETE'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      await this.loadAppsCatalog();
      if (typeof this.toast === 'function') {
        this.toast(this.t('admin_apps.deleted') || 'Template deleted', 'success');
      }
    } catch (err) {
      if (typeof this.toast === 'function') {
        this.toast((this.t('admin_apps.delete_failed') || 'Delete failed: ') + err.message, 'error');
      }
    }
  },

  // Free-text filter for the Templates table — matches the search term
  // (case-insensitive) against the template name, slug, description, and
  // any of its default-port numbers. Empty search returns the full list.
  filteredAppsCatalog() {
    const q = (this.appsCatalogSearch || '').trim().toLowerCase();
    const list = Array.isArray(this.appsCatalog) ? this.appsCatalog : [];
    if (!q) {
      return list;
    }
    return list.filter((e) => {
      if (!e) {
        return false;
      }
      const hay = [
        e.name || '', e.slug || '', e.description || '',
        (e.default_ports || []).map((p) => (p && p.port != null ? p.port : '')).join(' '),
        (e.default_ports || []).map((p) => (p && p.protocol) || '').join(' '),
      ].join(' ').toLowerCase();
      return hay.includes(q);
    });
  },

  // True when a template port number is in the proposal's matched set
  // (detected open on the host). Number-coerced both sides so an int
  // template port matches a string/number matched_ports entry.
  discoverPortIsMatched(prop, portNum) {
    const m = (prop && prop.matched_ports) || [];
    const n = Number(portNum);
    return m.some((p) => Number(p) === n);
  },

  // Resolve a Discover-proposal port NUMBER to its catalog-template port
  // definition so the matched / unmatched chips can show the protocol kind
  // (/http, /https, /tcp) + the open-as-URL marker like every other port
  // view. matched_ports / unmatched_ports are bare numbers; the metadata
  // lives on prop.catalog.default_ports. Falls back to tcp / no-url.
  discoverPortMeta(prop, port) {
    const dp = (prop && prop.catalog && prop.catalog.default_ports) || [];
    const found = dp.find((p) => p && Number(p.port) === Number(port));
    return found || {protocol: 'tcp', open_url: false};
  },

  // ----------------------------------------------------------------
  // Templates bulk-delete — multi-select rows in the Admin → Apps →
  // Templates table, delete every selected catalog template in one
  // action. Keyed by catalog id; reassigning appsCatalogSelected (not
  // mutating in place) keeps Alpine's row checkboxes + bulk bar reactive.
  // Template delete is by id (no index-shift), so order doesn't matter.
  // ----------------------------------------------------------------
  catalogSelKey(entry) {
    return entry && entry.id != null ? String(entry.id) : '';
  },
  isCatalogSelected(entry) {
    return !!this.appsCatalogSelected[this.catalogSelKey(entry)];
  },
  toggleCatalogSelected(entry) {
    const k = this.catalogSelKey(entry);
    if (!k) {
      return;
    }
    const next = Object.assign({}, this.appsCatalogSelected);
    if (next[k]) {
      delete next[k];
    } else {
      next[k] = true;
    }
    this.appsCatalogSelected = next;
  },
  appsCatalogSelectedCount() {
    const s = this.appsCatalogSelected || {};
    return Object.keys(s).filter((k) => s[k]).length;
  },
  appsCatalogAllSelected() {
    // Reflects the VISIBLE (filtered) rows so select-all + the header
    // checkbox track what the operator can actually see.
    const list = this.filteredAppsCatalog();
    if (!list.length) {
      return false;
    }
    return list.every((e) => !!this.appsCatalogSelected[this.catalogSelKey(e)]);
  },
  toggleSelectAllCatalog() {
    const list = this.filteredAppsCatalog();
    if (this.appsCatalogAllSelected()) {
      // Deselect only the visible rows; selections outside the current
      // filter are left intact.
      const next = Object.assign({}, this.appsCatalogSelected);
      for (const e of list) {
        delete next[this.catalogSelKey(e)];
      }
      this.appsCatalogSelected = next;
      return;
    }
    const next = Object.assign({}, this.appsCatalogSelected);
    for (const e of list) {
      next[this.catalogSelKey(e)] = true;
    }
    this.appsCatalogSelected = next;
  },
  clearCatalogSelection() {
    this.appsCatalogSelected = {};
  },
  async bulkDeleteCatalog() {
    const sel = (this.appsCatalog || []).filter(
      (e) => e && this.appsCatalogSelected[this.catalogSelKey(e)]);
    const n = sel.length;
    if (!n) {
      return;
    }
    const confirmed = typeof this.confirmDialog === 'function'
      ? await this.confirmDialog({
        title: this.t('admin_apps.bulk_delete_templates_confirm_title', {n})
          || ('Delete ' + n + ' template' + (n === 1 ? '' : 's') + '?'),
        text: this.t('admin_apps.bulk_delete_templates_confirm_text')
          || 'Per-host chips linked to these templates keep their own name + icon, just lose the catalog binding.',
        icon: 'warning',
        confirmButtonText: this.t('actions.delete') || 'Delete',
      })
      : window.confirm('Delete ' + n + ' templates?');
    if (!confirmed) {
      return;
    }
    this.appsCatalogBulkDeleting = true;
    let ok = 0;
    let failed = 0;
    for (const entry of sel) {
      try {
        const r = await fetch(`/api/services/catalog/${entry.id}`, {method: 'DELETE'});
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(this.fmtApiError(j, r.status));
        }
        ok += 1;
      } catch (_e) {
        failed += 1;
      }
    }
    this.appsCatalogSelected = {};
    this.appsCatalogBulkDeleting = false;
    await this.loadAppsCatalog();
    if (typeof this.toast === 'function') {
      if (failed) {
        const msg = this.t('admin_apps.bulk_delete_partial', {ok, failed})
          || (ok + ' deleted, ' + failed + ' failed');
        this.toast(msg, ok ? 'warning' : 'error');
      } else {
        this.toast(this.t('admin_apps.bulk_deleted_templates', {n: ok})
          || (ok + ' templates deleted'), 'success');
      }
    }
  },

  // ----------------------------------------------------------------
  // Apps → Instances tab — edit / delete a pinned chip in place. Chips
  // are otherwise create-only (pin / discovery); this is the per-app
  // editor the Hosts tab deferred to Apps. Routes through the per-chip
  // PATCH / DELETE endpoints (validated persist + audit). For a
  // catalog-linked chip, clearing name / icon re-inherits from the
  // template.
  // ----------------------------------------------------------------
  appsInstanceEditOpen: false,
  appsInstanceEditSaving: false,
  appsInstanceEditError: '',
  appsInstanceEditForm: {
    host_id: '', service_idx: -1, host_label: '',
    catalog_name: '', catalog_slug: '',
    name: '', url: '', icon: '', probe_enabled: true, probe_type: 'tcp',
    ports: [], docker_stack: '', docker_container: '', docker_host: '',
    // Per-instance show_extras tri-state (null = inherit, true /
    // false = explicit override) — consumed by every per-app
    // extras partial under `static/_partials/_components/apps/`
    // via the shared `appsShowExtras(app, inst)` resolver.
    show_extras: null,
    // Per-instance api_key — only consumed by templates that
    // declare it (currently Speedtest Tracker). Empty string =
    // unset (clears any stored value on save). Backend stamps
    // `api_key_set` on the instance row so the SPA can render
    // the "Saved" indicator without round-tripping the secret.
    api_key: '',
    api_key_set: false,
  },

  openInstanceEdit(inst) {
    if (!inst) {
      return;
    }
    // Seed the per-port rows from the chip's probe.ports[] (inherited
    // from the catalog template at pin time, e.g. AdGuard's 3 ports),
    // normalising each entry so the inputs bind cleanly.
    const ports = (Array.isArray(inst.ports) ? inst.ports : []).map((p) => ({
      port: (p && p.port != null) ? p.port : '',
      protocol: (p && p.protocol) || 'tcp',
      label: (p && p.label) || '',
      probe_path: (p && p.probe_path) || '',
      probe_status: (p && p.probe_status != null) ? p.probe_status : 0,
      open_url: !!(p && p.open_url),
      // Stable per-row key so the x-for keys on identity, NOT the array
      // index — index keys made Alpine reuse port-row DOM nodes by
      // position, so deleting a row appeared to do nothing / removed the
      // wrong one. Stripped on save.
      _uid: this._mintInstancePortUid(),
    }));
    this.appsInstanceEditForm = {
      host_id: inst.host_id,
      service_idx: inst.service_idx,
      host_label: inst.host_address || inst.host_id,
      catalog_name: inst.catalog_name || '',
      catalog_slug: inst.catalog_slug || '',
      name: inst.name || '',
      url: inst.url || '',
      icon: inst.icon || '',
      probe_enabled: inst.probe_enabled !== false,
      probe_type: inst.probe_type || 'tcp',
      ports: ports,
      docker_stack: inst.docker_stack || '',
      docker_container: inst.docker_container || '',
      docker_host: inst.docker_host || '',
      // Per-instance opt-in to render the per-app extras panel
      // (each per-app SPA module under `static/js/apps/<slug>.js`
      // owns its own panel template + helpers). Tri-state:
      // null = inherit from template's `show_extras` (default
      // true), true = always show, false = always hide regardless
      // of template. The SPA's `appsShowExtras(app, inst)` helper
      // resolves the chain before the per-app `<template x-if>`
      // gates fire; null / true → render, false → hide.
      show_extras: (inst.show_extras !== undefined && inst.show_extras !== null)
        ? !!inst.show_extras
        : null,
      // Per-instance api_key — never returned in the clear from
      // the backend (only `api_key_set: bool` flag). Editor starts
      // blank; a non-empty value on save overwrites, empty +
      // unchanged preserves the stored secret (keep-current
      // pattern from the global-secrets convention).
      api_key: '',
      api_key_set: !!inst.api_key_set,
      // Mirrors the live copy in app-apps-instances.js (which wins via the
      // merge order) — kept in sync to avoid drift on the last-tested chip.
      last_test_ok_ts: Number(inst.last_test_ok_ts) || 0,
    };
    this.appsInstanceEditError = '';
    // Seed the Link-to-Docker combobox input with the current link's label
    // (so it shows what's linked) + start with the dropdown closed.
    this.appsDockerLinkSearch = this.appsInstanceDockerLinkLabel();
    this.appsDockerLinkDropdownOpen = false;
    this.appsInstanceEditOpen = true;
  },

  // Datalist candidates for the "Link to Docker" picker, sourced from
  // the already-loaded /api/items snapshot (this.items). Stacks =
  // service names + stack namespaces; containers = standalone /orphan
  // container names. Free-text inputs back these so the operator can
  // also type an id the snapshot doesn't list.
  // Docker-link picker options — one grouped list from the live
  // /api/items snapshot. CONTAINERS (host-specific = the precise
  // restart/update target) come first WITH their host; SERVICES (span
  // hosts) follow. The option value is the item id (svc:/ctn:) — a
  // transient picker key; selecting one derives the STABLE stored
  // fields (name + host for a container, name for a service) via
  // setAppsInstanceDockerLink, so a container recreate (new id) doesn't
  // break the link.
  appsDockerLinkOptions() {
    const containers = [];
    const services = [];
    for (const it of (this.items || [])) {
      if (!it || !it.id || !it.name) {
        continue;
      }
      if (it.type === 'container' || it.type === 'orphan') {
        // Only offer RUNNING containers as link targets. Exclude stopped /
        // exited / dead / removed (orphan task) + offline ("faulty")
        // containers — none of them are a useful restart / update target
        // and they only clutter the picker.
        if ((it.state || '').toLowerCase() !== 'running') {
          continue;
        }
        containers.push({id: it.id, label: it.name + (it.node ? ' · ' + it.node : ''), name: it.name, host: it.node || ''});
      } else if (it.type === 'service') {
        services.push({id: it.id, label: it.name + (it.stack ? ' · ' + it.stack : ''), name: it.name, host: ''});
      }
    }
    const byLabel = (a, b) => (a.label || '').toLowerCase().localeCompare((b.label || '').toLowerCase());
    containers.sort(byLabel);
    services.sort(byLabel);
    return {containers, services};
  },

  // Searchable combobox support for the Link-to-Docker picker. Filters the
  // grouped options by the typed text so a host with many containers /
  // services isn't a long unscrollable dropdown.
  appsDockerLinkFiltered() {
    const opts = this.appsDockerLinkOptions();
    const q = (this.appsDockerLinkSearch || '').trim().toLowerCase();
    if (!q) {
      return opts;
    }
    const f = (arr) => (arr || []).filter((o) => (o.label || '').toLowerCase().includes(q));
    return {containers: f(opts.containers), services: f(opts.services)};
  },
  // Display label of the CURRENTLY-linked item (seeds the combobox input
  // on open + restored after a pick) — '' when not linked.
  appsInstanceDockerLinkLabel() {
    const v = this.appsInstanceDockerLinkValue();
    if (!v) {
      return '';
    }
    const opts = this.appsDockerLinkOptions();
    const found = (opts.containers || []).concat(opts.services || []).find((o) => o.id === v);
    return found ? found.label : '';
  },
  // Apply a combobox pick: set the link + show the chosen label in the
  // input + close the dropdown.
  appsDockerLinkPick(itemId, label) {
    this.setAppsInstanceDockerLink(itemId);
    this.appsDockerLinkSearch = label || '';
    this.appsDockerLinkDropdownOpen = false;
  },
  // Close the picker + DISCARD any unmatched free text. The link is ONLY
  // ever set by clicking an option (appsDockerLinkPick) — typed text is a
  // filter, never a saved value — so on close we snap the input back to
  // the currently-linked item's label (or '' when not linked). This stops
  // the box from showing free text that looks like a selection but won't
  // persist; to actually change the link the user must pick an option (or
  // the "— Not linked —" row).
  appsDockerLinkClose() {
    this.appsDockerLinkDropdownOpen = false;
    this.appsDockerLinkSearch = this.appsInstanceDockerLinkLabel();
  },

  // Keyboard-nav support for the Link-to-Docker combobox — mirrors the
  // pin-host / discover-host comboboxes so all three share the same
  // contract (arrow move + Enter select + Escape close + aria-
  // activedescendant). The dropdown spans two groups (containers +
  // services) plus the "Not linked" row, so nav walks a FLATTENED list;
  // each entry carries a stable dom id for aria-activedescendant + the
  // per-row highlight.
  appsDockerLinkFlat() {
    const f = this.appsDockerLinkFiltered();
    const out = [{
      id: '',
      label: this.t('admin_apps.instance_edit_docker_none') || '— Not linked —',
      dom: 'apps-docker-link-opt-none',
    }];
    (f.containers || []).forEach((o, i) => out.push({id: o.id, label: o.label, dom: 'apps-docker-link-opt-c' + i}));
    (f.services || []).forEach((o, i) => out.push({id: o.id, label: o.label, dom: 'apps-docker-link-opt-s' + i}));
    return out;
  },
  appsDockerLinkActiveDom() {
    const flat = this.appsDockerLinkFlat();
    const i = this.appsDockerLinkActiveIdx;
    return (i >= 0 && i < flat.length) ? flat[i].dom : '';
  },
  appsDockerLinkFocusDom(dom) {
    const i = this.appsDockerLinkFlat().findIndex(x => x.dom === dom);
    if (i >= 0) {
      this.appsDockerLinkActiveIdx = i;
    }
  },
  appsDockerLinkMove(delta) {
    this.appsDockerLinkDropdownOpen = true;
    const n = this.appsDockerLinkFlat().length;
    if (!n) {
      this.appsDockerLinkActiveIdx = -1;
      return;
    }
    let idx = this.appsDockerLinkActiveIdx + delta;
    if (idx < 0) {
      idx = n - 1;
    }
    if (idx >= n) {
      idx = 0;
    }
    this.appsDockerLinkActiveIdx = idx;
  },
  appsDockerLinkEnter() {
    const flat = this.appsDockerLinkFlat();
    const i = this.appsDockerLinkActiveIdx;
    if (i < 0 || i >= flat.length) {
      return;
    }
    const it = flat[i];
    // The "Not linked" row clears the link (label '' so the input blanks);
    // every other row links to its item id + shows its label.
    this.appsDockerLinkPick(it.id, it.id ? it.label : '');
  },

  // Current <select> value for the edit form: the item id matching the
  // stored link (container name + host wins; falls back to name-only
  // across snapshots; else the service), or '' when unlinked / the
  // linked item isn't in the current snapshot.
  appsInstanceDockerLinkValue() {
    const f = this.appsInstanceEditForm || {};
    const items = Array.isArray(this.items) ? this.items : [];
    if (f.docker_container) {
      const c = items.find((it) => it && (it.type === 'container' || it.type === 'orphan')
        && it.name === f.docker_container && (!f.docker_host || (it.node || '') === f.docker_host));
      if (c) {
        return c.id;
      }
      const c2 = items.find((it) => it && (it.type === 'container' || it.type === 'orphan') && it.name === f.docker_container);
      if (c2) {
        return c2.id;
      }
    }
    if (f.docker_stack) {
      const s = items.find((it) => it && it.type === 'service'
        && (it.name === f.docker_stack || it.stack === f.docker_stack));
      if (s) {
        return s.id;
      }
    }
    return '';
  },

  // Apply a picker selection to the edit form's stable fields. A
  // container sets docker_container + docker_host (clears docker_stack);
  // a service sets docker_stack (clears docker_container + docker_host);
  // '' clears the link entirely.
  setAppsInstanceDockerLink(itemId) {
    const f = this.appsInstanceEditForm;
    if (!f) {
      return;
    }
    if (!itemId) {
      f.docker_container = '';
      f.docker_stack = '';
      f.docker_host = '';
      return;
    }
    const it = (this.items || []).find((x) => x && x.id === itemId);
    if (!it) {
      return;
    }
    if (it.type === 'container' || it.type === 'orphan') {
      f.docker_container = it.name || '';
      f.docker_host = it.node || '';
      f.docker_stack = '';
    } else if (it.type === 'service') {
      f.docker_stack = it.name || '';
      f.docker_container = '';
      f.docker_host = '';
    }
  },

  // One-line confirmation of what the current edit-form link resolves to
  // in the live snapshot — surfaces the live status so the operator sees
  // the mapping is real (uses the mapping "to do something"). Empty when
  // unlinked.
  appsInstanceDockerLinkSummary() {
    const f = this.appsInstanceEditForm || {};
    if (!f.docker_container && !f.docker_stack) {
      return '';
    }
    const target = this._appDrawerResolveItem(f);
    if (!target) {
      return this.t('admin_apps.instance_edit_docker_unresolved') || 'Linked target not in the current items snapshot';
    }
    const where = target.node ? (' · ' + target.node) : '';
    const status = target.status || target.health || '';
    return (this.t('admin_apps.instance_edit_docker_linked') || 'Linked')
      + ' → ' + (target.name || '') + where + (status ? ' (' + status + ')' : '');
  },

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

  // Stable id for an instance-editor port row — used as the x-for :key so
  // splicing a row out doesn't make Alpine reuse DOM nodes by index.
  // lgtm[js/insecure-randomness]  — Same suppression contract as
  // `_newId` above. This id is a UI-only Alpine `:key` for per-port
  // row stable identity (NOT a security secret). The fallback
  // chain is crypto-only by design; the lgtm marker handles
  // CodeQL stale-cache where a prior revision used a PRNG source.
  _mintInstancePortUid() {
    // Primary path — crypto.randomUUID() (universally supported in
    // modern browsers). Returns a 36-char UUID prefixed with `pp_`.
    // The id is a UI-only Alpine x-for key (per-port row stable
    // identity); not a security secret. Crypto-strength source
    // keeps the CodeQL `js/insecure-randomness` audit clean and
    // matches the same shape used by `_newId` above.
    try {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return 'pp_' + window.crypto.randomUUID();
      }
      // Secondary path — crypto.getRandomValues() for older
      // browsers without the .randomUUID shortcut.
      if (window.crypto && typeof window.crypto.getRandomValues === 'function') {
        const buf = new Uint8Array(8);
        window.crypto.getRandomValues(buf);
        let hex = '';
        for (let i = 0; i < buf.length; i++) {
          hex += buf[i].toString(16).padStart(2, '0');
        }
        return 'pp_' + Date.now().toString(36) + '-' + hex;
      }
    } catch (_e) { /* fall through to the deterministic counter */
    }
    // Tertiary fallback — Web Crypto truly unavailable. Use a
    // monotonically-increasing counter scoped to the
    // component instance, NOT a PRNG. The id needs collision-
    // resistance + uniqueness within the session, not entropy.
    this._instancePortUidCounter = (this._instancePortUidCounter || 0) + 1;
    return 'pp_' + Date.now().toString(36) + '-c' + this._instancePortUidCounter.toString(36);
  },

  addInstancePort() {
    if (!Array.isArray(this.appsInstanceEditForm.ports)) {
      this.appsInstanceEditForm.ports = [];
    }
    this.appsInstanceEditForm.ports.push({
      port: '', protocol: 'tcp', label: '', probe_path: '', probe_status: 0,
      open_url: false, _uid: this._mintInstancePortUid(),
    });
  },

  removeInstancePort(i) {
    if (Array.isArray(this.appsInstanceEditForm.ports)) {
      this.appsInstanceEditForm.ports.splice(i, 1);
    }
  },

  closeInstanceEdit() {
    this.appsInstanceEditOpen = false;
  },

  async saveInstanceEdit() {
    const f = this.appsInstanceEditForm;
    if (!f.host_id || f.service_idx < 0) {
      return;
    }
    this.appsInstanceEditSaving = true;
    this.appsInstanceEditError = '';
    try {
      const r = await fetch('/api/services/' + encodeURIComponent(f.host_id)
        + '/' + encodeURIComponent(f.service_idx), {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          name: f.name, url: f.url, icon: f.icon,
          probe_enabled: f.probe_enabled, probe_type: f.probe_type,
          // Strip the editor-only `_uid` (x-for key) from each port row.
          ports: Array.isArray(f.ports) ? f.ports.map((p) => ({
            port: p.port, protocol: p.protocol, label: p.label,
            probe_path: p.probe_path, probe_status: p.probe_status, open_url: p.open_url,
          })) : [],
          docker_stack: f.docker_stack || '', docker_container: f.docker_container || '',
          docker_host: f.docker_host || '',
          // Per-instance `show_extras` tri-state — true / false /
          // null (= inherit template default). The backend
          // `services[].show_extras` field is the source of truth;
          // the SPA editor's checkbox `:indeterminate` reflects
          // null on load + becomes true / false on click. Only
          // sent when the operator made an explicit choice (true
          // or false); null keeps the field absent so a template
          // default-flip propagates without a per-instance save.
          show_extras: (f.show_extras === true || f.show_extras === false)
            ? f.show_extras
            : null,
          // Per-instance api_key — keep-current-if-blank contract
          // shared with every other secret in OmniGrid (Beszel
          // password, Portainer token, etc.). Non-empty value
          // overwrites the stored secret; empty string + an
          // already-set flag keeps the existing value. Backend
          // returns `api_key_set: bool` so the SPA can render the
          // "Saved" indicator without round-tripping the secret.
          api_key: (typeof f.api_key === 'string') ? f.api_key : '',
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      this.appsInstanceEditOpen = false;
      await this.loadAppsInstances();
      // Await the Apps-view refresh + re-sync an open drawer so the edit
      // (URL / ports / icon / link) reflects in the top-level Apps view
      // and the App drawer, not just the admin instances table.
      if (typeof this.loadAppsList === 'function') {
        await this.loadAppsList(true);
        this._resyncDrawerApp();
        this._resyncDrawerAppHost();
      }
    } catch (err) {
      this.appsInstanceEditError = (err && err.message) ? err.message : String(err);
    } finally {
      this.appsInstanceEditSaving = false;
    }
  },

  async deleteInstance(inst) {
    if (!inst || !inst.host_id) {
      return;
    }
    const label = inst.name || inst.catalog_name
      || (inst.catalog && inst.catalog.name) || ('service ' + inst.service_idx);
    const hostName = inst.host_label || inst.host_id || '';
    const confirmed = typeof this.confirmDialog === 'function'
      ? await this.confirmDialog({
        // Name the app + host prominently in the title (was a generic
        // "Remove app instance?" with the name only tucked into the body).
        title: this.t('admin_apps.instance_delete_confirm_title', {name: label, host: hostName})
          || ('Remove ' + label + ' from ' + hostName + '?'),
        text: this.t('admin_apps.instance_delete_confirm_text')
          || 'This unpins the chip from the host. The catalog template is unaffected.',
        icon: 'warning',
        confirmButtonText: this.t('actions.delete') || 'Delete',
      })
      : window.confirm('Remove "' + label + '" from ' + hostName + '?');
    if (!confirmed) {
      return;
    }
    try {
      const r = await fetch('/api/services/' + encodeURIComponent(inst.host_id)
        + '/' + encodeURIComponent(inst.service_idx), {method: 'DELETE'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      await this.loadAppsInstances();
      if (typeof this.loadAppsList === 'function') {
        await this.loadAppsList(true);
        this._resyncDrawerApp();
        this._resyncDrawerAppHost();
      }
      if (typeof this.toast === 'function') {
        this.toast(this.t('admin_apps.instance_deleted') || 'App instance removed', 'success');
      }
    } catch (err) {
      if (typeof this.toast === 'function') {
        this.toast((this.t('admin_apps.instance_delete_failed') || 'Remove failed: ') + err.message, 'error');
      }
    }
  },

  // ----------------------------------------------------------------
  // Instances bulk-delete — multi-select rows in the Admin → Apps →
  // Instances table, delete every selected chip in one action. Selection
  // is keyed by `host_id:service_idx`; reassigning appsInstancesSelected
  // (rather than mutating in place) guarantees Alpine re-evaluates the
  // row checkboxes + bulk bar.
  // ----------------------------------------------------------------
  instanceSelKey(inst) {
    return inst ? ((inst.host_id || '') + ':' + inst.service_idx) : '';
  },
  isInstanceSelected(inst) {
    return !!this.appsInstancesSelected[this.instanceSelKey(inst)];
  },
  toggleInstanceSelected(inst) {
    const k = this.instanceSelKey(inst);
    if (!k) {
      return;
    }
    const next = Object.assign({}, this.appsInstancesSelected);
    if (next[k]) {
      delete next[k];
    } else {
      next[k] = true;
    }
    this.appsInstancesSelected = next;
  },
  appsInstancesSelectedCount() {
    const s = this.appsInstancesSelected || {};
    return Object.keys(s).filter((k) => s[k]).length;
  },
  appsInstancesAllSelected() {
    const list = this.appsInstances || [];
    if (!list.length) {
      return false;
    }
    return list.every((i) => !!this.appsInstancesSelected[this.instanceSelKey(i)]);
  },
  toggleSelectAllInstances() {
    if (this.appsInstancesAllSelected()) {
      this.appsInstancesSelected = {};
      return;
    }
    const next = {};
    for (const i of (this.appsInstances || [])) {
      next[this.instanceSelKey(i)] = true;
    }
    this.appsInstancesSelected = next;
  },
  clearInstanceSelection() {
    this.appsInstancesSelected = {};
  },
  async bulkDeleteInstances() {
    const sel = (this.appsInstances || []).filter(
      (i) => i && this.appsInstancesSelected[this.instanceSelKey(i)]);
    const n = sel.length;
    if (!n) {
      return;
    }
    const confirmed = typeof this.confirmDialog === 'function'
      ? await this.confirmDialog({
        title: this.t('admin_apps.bulk_delete_confirm_title', {n})
          || ('Remove ' + n + ' app instance' + (n === 1 ? '' : 's') + '?'),
        text: this.t('admin_apps.bulk_delete_confirm_text')
          || 'This unpins the selected chips from their hosts. Catalog templates are unaffected.',
        icon: 'warning',
        confirmButtonText: this.t('actions.delete') || 'Delete',
      })
      : window.confirm('Remove ' + n + ' app instances?');
    if (!confirmed) {
      return;
    }
    this.appsInstancesBulkDeleting = true;
    // The DELETE endpoint removes from each host's services[] BY INDEX,
    // so deleting must go DESCENDING by service_idx per host — removing a
    // higher index first keeps every lower index valid. Deleting low-first
    // would shift the higher rows down and mis-target the next delete.
    const byHost = {};
    for (const inst of sel) {
      const hid = inst.host_id || '';
      (byHost[hid] = byHost[hid] || []).push(inst);
    }
    let ok = 0;
    let failed = 0;
    for (const hid of Object.keys(byHost)) {
      const rows = byHost[hid].slice().sort((a, b) => b.service_idx - a.service_idx);
      for (const inst of rows) {
        try {
          const r = await fetch('/api/services/' + encodeURIComponent(inst.host_id)
            + '/' + encodeURIComponent(inst.service_idx), {method: 'DELETE'});
          if (!r.ok) {
            const j = await r.json().catch(() => ({}));
            throw new Error(this.fmtApiError(j, r.status));
          }
          ok += 1;
        } catch (_e) {
          failed += 1;
        }
      }
    }
    this.appsInstancesSelected = {};
    this.appsInstancesBulkDeleting = false;
    await this.loadAppsInstances();
    if (typeof this.loadAppsList === 'function') {
      await this.loadAppsList(true);
      this._resyncDrawerApp();
      this._resyncDrawerAppHost();
    }
    if (typeof this.toast === 'function') {
      if (failed) {
        const msg = this.t('admin_apps.bulk_delete_partial', {ok, failed})
          || (ok + ' removed, ' + failed + ' failed');
        this.toast(msg, ok ? 'warning' : 'error');
      } else {
        this.toast(this.t('admin_apps.bulk_deleted', {n: ok})
          || (ok + ' app instances removed'), 'success');
      }
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

  // Instances of this app that can be probed (carry a host_id + service_idx
  // — the per-chip probe endpoint targets exactly those). Used for the
  // "Probe all (N)" button count + gate.
  appsProbeTargetCount(app) {
    if (!app || !Array.isArray(app.instances)) {
      return 0;
    }
    return app.instances.filter((i) => i && i.host_id != null && i.service_idx != null).length;
  },

  // Instances of this app that carry a clickable URL — drives the
  // "Open all (N)" button count + gate.
  appsOpenableCount(app) {
    if (!app || !Array.isArray(app.instances)) {
      return 0;
    }
    return app.instances.filter((i) => i && i.url).length;
  },

  // Is this app's per-app probe-all batch currently running? Drives the
  // button spinner + :disabled. Keyed by group_id.
  appsProbingAll(app) {
    return !!(app && this._appsProbingAll && this._appsProbingAll[app.group_id]);
  },

  // Transient summary line ("4/5 up") shown in the card header right after a
  // probe-all finishes; '' when none. Keyed by group_id.
  appsProbeAllSummary(app) {
    return (app && this._appsProbeAllSummary && this._appsProbeAllSummary[app.group_id]) || '';
  },

  // Probe EVERY instance of this app in one click — bounded fan-out over the
  // per-chip probe endpoint, then ONE apps-list reload so each instance's
  // status dot + rtt updates in place (the inline per-host result). A single
  // summary toast + a transient header summary line report the rollup.
  async probeAllInstances(app) {
    if (!app || !Array.isArray(app.instances) || !app.instances.length) {
      return;
    }
    const gid = app.group_id;
    if (!this._appsProbingAll) {
      this._appsProbingAll = {};
    }
    if (this._appsProbingAll[gid]) {
      return;
    }
    // Snapshot the targets up front — loadAppsList(true) reconciles
    // app.instances in place, so iterating it live could skip/repeat.
    const targets = app.instances
      .filter((i) => i && i.host_id != null && i.service_idx != null)
      .map((i) => ({host_id: i.host_id, service_idx: i.service_idx}));
    if (!targets.length) {
      return;
    }
    this._appsProbingAll[gid] = true;
    this._appsProbeAllSummary[gid] = '';
    try {
      const results = await this._fanOutBounded(targets, async (tgt) => {
        const r = await fetch('/api/services/' + encodeURIComponent(tgt.host_id)
          + '/' + encodeURIComponent(tgt.service_idx) + '/probe', {method: 'POST'});
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          return {ok: false, error: j.detail || ('HTTP ' + r.status)};
        }
        const j = await r.json();
        return {ok: true, alive: !!j.alive};
      }, 4);
      const up = results.filter((x) => x && x.ok && x.alive).length;
      const total = targets.length;
      this._appsProbeAllSummary[gid] = this.t('apps.probe_all_summary', {up: up, total: total})
        || (up + '/' + total + ' up');
      // ONE reload so every instance's dot + rtt reflects the fresh probe
      // (the inline per-host result), matching the in-place reconcile
      // discipline (loadAppsList reconciles instances by host_id+service_idx).
      if (typeof this.loadAppsList === 'function') {
        await this.loadAppsList(true);
      }
      if (typeof this.showToast === 'function') {
        this.showToast(
          this.t('apps.probe_all_done', {name: app.name, up: up, total: total})
          || ('Probed ' + total + ' instances — ' + up + ' up'),
          up === total ? 'success' : 'info'
        );
      }
    } finally {
      this._appsProbingAll[gid] = false;
    }
  },

  // Open every instance URL of this app in a new tab. MUST stay synchronous
  // (no await before the window.open loop) so the browser treats each open
  // as user-initiated — an intervening await would break the click-gesture
  // chain and the popup blocker would swallow all but the first.
  openAllInstances(app) {
    if (!app || !Array.isArray(app.instances)) {
      return;
    }
    const urls = [];
    const seen = new Set();
    for (const inst of app.instances) {
      const u = inst && inst.url;
      if (u && !seen.has(u)) {
        seen.add(u);
        urls.push(u);
      }
    }
    if (!urls.length) {
      return;
    }
    for (const u of urls) {
      window.open(u, '_blank', 'noopener');
    }
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

  // ----------------------------------------------------------------
  // Pin-to-host modal — operator picks a curated host from the existing
  // hostsConfig list + optional URL override + probe-enable flag; the
  // POST to /api/services/catalog/{cid}/pin creates the chip server-
  // side via the same _clean_host_services validator that Admin →
  // Hosts uses. After save the Instances tab refresh picks up the new
  // chip automatically.
  // ----------------------------------------------------------------
  openAppCatalogPin(entry) {
    if (!entry || !entry.id) {
      return;
    }
    this.appsPinForm = {
      template: entry,
      host_ids: [],
      url: '',
      probe_enabled: true,
    };
    this.appsPinError = '';
    this.appsPinHostSearch = '';
    this.appsPinHostDropdownOpen = false;
    this.appsPinHostActiveIdx = -1;
    this.appsPinModalOpen = true;
    // The host picker reads from `hostsConfig`. The Admin → Apps tab
    // loader doesn't fetch it (only the Hosts tab does), so the picker
    // is empty if the operator hasn't visited Hosts in this session.
    // Lazy-load on first open.
    if (!Array.isArray(this.hostsConfig) || !this.hostsConfig.length) {
      if (typeof this.loadHostsConfig === 'function') {
        this.loadHostsConfig().catch(() => undefined);
      }
    }
  },

  closeAppCatalogPin() {
    this.appsPinModalOpen = false;
    this.appsPinForm = {template: null, host_ids: [], url: '', probe_enabled: true};
    this.appsPinError = '';
    this.appsPinHostSearch = '';
    this.appsPinHostDropdownOpen = false;
    this.appsPinHostActiveIdx = -1;
  },

  // ---- Pin-to-host searchable picker -----------------------------
  // Mirrors the discovery wizard's host picker (appsDiscoverFilteredHosts
  // / appsDiscoverHostMove / etc.) but writes the selection to
  // appsPinForm.host_id. Reuses the shared appsHostLabel(h) formatter so
  // the input reads identically to the old <option> text. Kept as a
  // separate set of helpers (not shared with the discovery picker)
  // because selecting a host here does NOT trigger a side-effect probe —
  // it only fills the form field.
  appsPinFilteredHosts() {
    const all = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
    const q = (this.appsPinHostSearch || '').trim().toLowerCase();
    let out = all;
    if (q) {
      out = all.filter((h) => {
        if (!h) {
          return false;
        }
        const hay = ((h.label || '') + ' ' + (h.id || '') + ' ' + (h.address || '')).toLowerCase();
        return hay.includes(q);
      });
    }
    return out.slice(0, 50);
  },

  // Multi-host pin: clicking a host TOGGLES it into the selected set
  // (host_ids) and keeps the picker open + clears the filter so the
  // operator can keep adding more. Selected hosts render as removable
  // chips above the input.
  toggleAppsPinHost(h) {
    if (!h || !h.id) {
      return;
    }
    if (!this.appsPinForm || !Array.isArray(this.appsPinForm.host_ids)) {
      this.appsPinForm.host_ids = [];
    }
    const i = this.appsPinForm.host_ids.indexOf(h.id);
    if (i >= 0) {
      this.appsPinForm.host_ids.splice(i, 1);
    } else {
      this.appsPinForm.host_ids.push(h.id);
    }
    this.appsPinHostSearch = '';
    this.appsPinHostActiveIdx = -1;
  },
  appsPinHostChosen(h) {
    return !!(h && this.appsPinForm
      && Array.isArray(this.appsPinForm.host_ids)
      && this.appsPinForm.host_ids.includes(h.id));
  },
  removeAppsPinHost(id) {
    if (!this.appsPinForm || !Array.isArray(this.appsPinForm.host_ids)) {
      return;
    }
    const i = this.appsPinForm.host_ids.indexOf(id);
    if (i >= 0) {
      this.appsPinForm.host_ids.splice(i, 1);
    }
  },
  // Selected host rows (objects) for the removable chip strip.
  appsPinSelectedHosts() {
    const ids = (this.appsPinForm && Array.isArray(this.appsPinForm.host_ids))
      ? this.appsPinForm.host_ids : [];
    const cfg = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
    return ids.map((id) => cfg.find((h) => h && h.id === id) || {id});
  },

  appsPinHostMove(delta) {
    this.appsPinHostDropdownOpen = true;
    const n = this.appsPinFilteredHosts().length;
    if (!n) {
      this.appsPinHostActiveIdx = -1;
      return;
    }
    let idx = this.appsPinHostActiveIdx + delta;
    if (idx < 0) {
      idx = n - 1;
    }
    if (idx >= n) {
      idx = 0;
    }
    this.appsPinHostActiveIdx = idx;
  },

  appsPinHostEnter() {
    const list = this.appsPinFilteredHosts();
    if (this.appsPinHostActiveIdx >= 0 && this.appsPinHostActiveIdx < list.length) {
      this.toggleAppsPinHost(list[this.appsPinHostActiveIdx]);
    } else if (list.length === 1) {
      this.toggleAppsPinHost(list[0]);
    }
  },

  async submitAppCatalogPin() {
    if (this.appsPinSaving) {
      return;
    }
    const form = this.appsPinForm || {};
    const tpl = form.template;
    if (!tpl || !tpl.id) {
      this.appsPinError = this.t('admin_apps.pin_no_template') || 'No template selected';
      return;
    }
    const hostIds = Array.isArray(form.host_ids) ? form.host_ids.slice() : [];
    if (!hostIds.length) {
      this.appsPinError = this.t('admin_apps.pin_pick_host') || 'Pick at least one host';
      return;
    }
    this.appsPinSaving = true;
    this.appsPinError = '';
    // Multi-host: pin to each selected host in turn. 409 = already pinned
    // (the backend's duplicate guard) — counted separately, not a failure.
    let pinned = 0;
    let already = 0;
    let failed = 0;
    const failMsgs = [];
    try {
      for (const hid of hostIds) {
        try {
          const r = await fetch(`/api/services/catalog/${tpl.id}/pin`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              host_id: hid,
              url: (form.url || '').trim(),
              probe_enabled: !!form.probe_enabled,
            }),
          });
          if (r.status === 409) {
            already += 1;
            continue;
          }
          if (!r.ok) {
            const j = await r.json().catch(() => ({}));
            failed += 1;
            failMsgs.push(hid + ': ' + (j.detail || ('HTTP ' + r.status)));
            continue;
          }
          pinned += 1;
        } catch (e) {
          failed += 1;
          failMsgs.push(hid + ': ' + ((e && e.message) ? e.message : e));
        }
      }
      // Refresh the instance list so the new chips appear.
      await this.loadAppsInstances();
      if (typeof this.toast === 'function') {
        const parts = [];
        if (pinned) {
          parts.push(this.t('admin_apps.pin_result_pinned', {n: pinned}) || (pinned + ' pinned'));
        }
        if (already) {
          parts.push(this.t('admin_apps.pin_result_already', {n: already}) || (already + ' already pinned'));
        }
        if (failed) {
          parts.push(this.t('admin_apps.pin_result_failed', {n: failed}) || (failed + ' failed'));
        }
        const pinMsg = (this.t('admin_apps.pin_success_multi', {name: tpl.name || ''})
          || ('Pinned ' + (tpl.name || '') + ': ')) + parts.join(', ');
        this.toast(pinMsg, failed ? 'warning' : 'success');
      }
      if (failed) {
        // Keep the modal open so failures stay visible.
        this.appsPinError = failMsgs.slice(0, 5).join('; ');
      } else {
        this.closeAppCatalogPin();
      }
    } finally {
      this.appsPinSaving = false;
    }
  },

  // ----------------------------------------------------------------
  // Discovery wizard — port-scan + catalog match → bulk-bind.
  // ----------------------------------------------------------------
  // Jump from the top-level Apps view's empty-state CTA into the
  // discovery wizard. The wizard markup lives in the Admin → Apps
  // partial (hidden via x-show on the admin page-content), so we have
  // to navigate to that view + tab first, then open the modal on the
  // next tick once the partial is visible.
  openDiscoveryFromAppsView() {
    this.view = 'admin';
    this.adminTab = 'apps';
    if (typeof this.setAppsAdminTab === 'function') {
      this.setAppsAdminTab('templates');
    }
    this.$nextTick(() => this.openAppsDiscoverWizard());
  },

  openAppsDiscoverWizard() {
    this.appsDiscoverOpen = true;
    this.appsDiscoverForm = {host_id: ''};
    this.appsDiscoverResult = {detected_ports: [], proposals: [], scanned_at: 0};
    this.appsDiscoverError = '';
    this.appsDiscoverApplyError = '';
    this.appsDiscoverSelected = new Set();
    this.appsDiscoverHostSearch = '';
    this.appsDiscoverHostDropdownOpen = false;
    this.appsDiscoverHostActiveIdx = -1;
    // Host picker reads from hostsConfig — lazy-load if the operator
    // hasn't visited Admin → Hosts in this session.
    if (!Array.isArray(this.hostsConfig) || !this.hostsConfig.length) {
      if (typeof this.loadHostsConfig === 'function') {
        this.loadHostsConfig().catch(() => undefined);
      }
    }
  },

  closeAppsDiscoverWizard() {
    this.appsDiscoverOpen = false;
    this.appsDiscoverForm = {host_id: ''};
    this.appsDiscoverResult = {detected_ports: [], proposals: [], scanned_at: 0};
    this.appsDiscoverError = '';
    this.appsDiscoverApplyError = '';
    this.appsDiscoverSelected = new Set();
    this.appsDiscoverHostSearch = '';
    this.appsDiscoverHostDropdownOpen = false;
    this.appsDiscoverHostActiveIdx = -1;
  },

  // Display label for a curated host row in the discovery host picker —
  // canonical hostname/IP first, then the operator label when it differs.
  // Mirrors the old <option> text so the searchable input reads identically.
  appsHostLabel(h) {
    if (!h) {
      return '';
    }
    const base = (h.address || h.id || '').trim();
    const label = (h.label || '').trim();
    return base + (label && label !== base ? ' — ' + label : '');
  },

  // Filtered + capped host list for the searchable picker. Matches the
  // query (case-insensitive substring) against label + id + address so
  // the operator can type any identifier they remember. Empty query
  // returns the whole list (capped) so focusing the field shows options.
  appsDiscoverFilteredHosts() {
    const all = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
    const q = (this.appsDiscoverHostSearch || '').trim().toLowerCase();
    let out = all;
    if (q) {
      out = all.filter((h) => {
        if (!h) {
          return false;
        }
        const hay = ((h.label || '') + ' ' + (h.id || '') + ' ' + (h.address || '')).toLowerCase();
        return hay.includes(q);
      });
    }
    // Cap the rendered list so a huge fleet doesn't paint hundreds of
    // <li> nodes; the operator narrows with the query rather than scroll.
    return out.slice(0, 50);
  },

  // Commit a host selection from the dropdown (click or keyboard Enter).
  selectAppsDiscoverHost(h) {
    if (!h || !h.id) {
      return;
    }
    this.appsDiscoverForm.host_id = h.id;
    this.appsDiscoverHostSearch = this.appsHostLabel(h);
    this.appsDiscoverHostDropdownOpen = false;
    this.appsDiscoverHostActiveIdx = -1;
    this.runAppsDiscovery();
  },

  // Arrow-key navigation through the filtered match list. Opens the
  // dropdown on first keypress and clamps the highlight index in range.
  appsDiscoverHostMove(delta) {
    this.appsDiscoverHostDropdownOpen = true;
    const n = this.appsDiscoverFilteredHosts().length;
    if (!n) {
      this.appsDiscoverHostActiveIdx = -1;
      return;
    }
    let idx = this.appsDiscoverHostActiveIdx + delta;
    if (idx < 0) {
      idx = n - 1;
    }
    if (idx >= n) {
      idx = 0;
    }
    this.appsDiscoverHostActiveIdx = idx;
  },

  // Enter key: select the highlighted match, or — when nothing is
  // highlighted but the filter narrows to exactly one host — that host.
  appsDiscoverHostEnter() {
    const list = this.appsDiscoverFilteredHosts();
    if (this.appsDiscoverHostActiveIdx >= 0 && this.appsDiscoverHostActiveIdx < list.length) {
      this.selectAppsDiscoverHost(list[this.appsDiscoverHostActiveIdx]);
    } else if (list.length === 1) {
      this.selectAppsDiscoverHost(list[0]);
    }
  },

  async runAppsDiscovery() {
    const hostId = (this.appsDiscoverForm && this.appsDiscoverForm.host_id) || '';
    if (!hostId) {
      this.appsDiscoverResult = {detected_ports: [], proposals: [], scanned_at: 0};
      return;
    }
    this.appsDiscoverLoading = true;
    this.appsDiscoverError = '';
    this.appsDiscoverResult = {detected_ports: [], proposals: [], scanned_at: 0};
    this.appsDiscoverSelected = new Set();
    try {
      const r = await fetch(`/api/services/discover/${encodeURIComponent(hostId)}`, {
        method: 'POST',
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      const j = await r.json();
      this.appsDiscoverResult = j;
      // Count how many proposals match each detected port. A port matched
      // by 2+ templates is AMBIGUOUS (e.g. several apps share 80 / 443) —
      // auto-checking them would pin multiple apps competing for the same
      // port, so leave them UNCHECKED — the shared port's owner must be
      // picked by hand rather than auto-bound to multiple apps.
      const portMatchCount = {};
      for (const prop of (j.proposals || [])) {
        for (const port of (prop.matched_ports || [])) {
          portMatchCount[port] = (portMatchCount[port] || 0) + 1;
        }
      }
      // Pre-select proposals with confidence >= 0.9 (all template ports
      // detected + name match OR multi-port exact match) — the safe
      // bulk-bind candidates — EXCEPT those whose matched ports collide
      // with another proposal.
      const preSelected = new Set();
      for (const prop of (j.proposals || [])) {
        if (!prop || prop.confidence < 0.9 || !prop.catalog || !prop.catalog.id) {
          continue;
        }
        const ambiguous = (prop.matched_ports || []).some(
          (port) => (portMatchCount[port] || 0) >= 2);
        if (!ambiguous) {
          preSelected.add(prop.catalog.id);
        }
      }
      this.appsDiscoverSelected = preSelected;
    } catch (err) {
      this.appsDiscoverError = err && err.message ? err.message : String(err);
    } finally {
      this.appsDiscoverLoading = false;
    }
  },

  toggleAppsDiscoverSelection(catalogId) {
    if (!catalogId) {
      return;
    }
    // Alpine 3 reacts to Set assignment, not mutation. Clone, mutate,
    // reassign so :checked bindings re-evaluate on toggle.
    const next = new Set(this.appsDiscoverSelected || []);
    if (next.has(catalogId)) {
      next.delete(catalogId);
    } else {
      next.add(catalogId);
    }
    this.appsDiscoverSelected = next;
  },

  toggleAllAppsDiscoverSelections(checked) {
    if (!this.appsDiscoverResult) {
      return;
    }
    if (checked) {
      const next = new Set();
      for (const prop of (this.appsDiscoverResult.proposals || [])) {
        if (prop && prop.catalog && prop.catalog.id) {
          next.add(prop.catalog.id);
        }
      }
      this.appsDiscoverSelected = next;
    } else {
      this.appsDiscoverSelected = new Set();
    }
  },

  async submitAppsDiscoverApply() {
    if (this.appsDiscoverApplying) {
      return;
    }
    const hostId = (this.appsDiscoverForm && this.appsDiscoverForm.host_id) || '';
    if (!hostId) {
      this.appsDiscoverApplyError = this.t('admin_apps.pin_pick_host') || 'Pick a host';
      return;
    }
    const ids = Array.from(this.appsDiscoverSelected || []);
    if (!ids.length) {
      this.appsDiscoverApplyError = this.t('admin_apps.discover_select_at_least_one') || 'Select at least one proposal';
      return;
    }
    this.appsDiscoverApplying = true;
    this.appsDiscoverApplyError = '';
    try {
      const r = await fetch(`/api/services/discover/${encodeURIComponent(hostId)}/apply`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({catalog_ids: ids, probe_enabled: true}),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      const j = await r.json();
      await this.loadAppsInstances();
      if (typeof this.toast === 'function') {
        const nApplied = (j.applied || []).length;
        const nSkipped = (j.skipped || []).length;
        let msg = (this.t('admin_apps.discover_applied') || 'Pinned ') + nApplied;
        if (nSkipped) {
          msg += ' · ' + nSkipped + ' ' + (this.t('admin_apps.discover_skipped') || 'skipped');
        }
        this.toast(msg, 'success');
      }
      this.closeAppsDiscoverWizard();
    } catch (err) {
      this.appsDiscoverApplyError = err && err.message ? err.message : String(err);
    } finally {
      this.appsDiscoverApplying = false;
    }
  },

  async reseedAppCatalog() {
    if (this.appsCatalogReseeding) {
      return;
    }
    this.appsCatalogReseeding = true;
    this.appsCatalogStatus = '';
    try {
      const r = await fetch('/api/services/catalog/seed', {method: 'POST'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      const j = await r.json();
      await this.loadAppsCatalog();
      this.appsCatalogStatus = (this.t('admin_apps.reseed_done') || 'Re-seeded') + ': +' + (j.added || 0);
      if (typeof this.toast === 'function') {
        this.toast(this.appsCatalogStatus, 'success');
      }
    } catch (err) {
      this.appsCatalogStatus = err && err.message ? err.message : String(err);
    } finally {
      this.appsCatalogReseeding = false;
    }
  },

  // Export the whole catalog as a portable JSON pack (download). The
  // backend strips install-specific id/timestamps + keys on slug so the
  // pack re-imports cleanly on any install.
  async exportAppCatalog() {
    this.appsCatalogStatus = '';
    try {
      const r = await fetch('/api/services/catalog/export');
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      const pack = await r.json();
      const blob = new Blob([JSON.stringify(pack, null, 2)], {type: 'application/json'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'omnigrid-catalog-pack.json';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      this.appsCatalogStatus = (this.t('admin_apps.export_done') || 'Exported') + ': ' + (pack.count || 0);
    } catch (err) {
      this.appsCatalogStatus = err && err.message ? err.message : String(err);
      if (typeof this.toast === 'function') {
        this.toast(this.appsCatalogStatus, 'error');
      }
    }
  },

  // Import a catalog pack from a chosen JSON file. Accepts a full export
  // pack ({entries:[...]}) or a bare array; the backend upserts by slug.
  async importAppCatalog(ev) {
    const input = ev && ev.target;
    const file = input && input.files && input.files[0];
    if (!file) {
      return;
    }
    if (this.appsCatalogImporting) {
      return;
    }
    this.appsCatalogImporting = true;
    this.appsCatalogStatus = '';
    try {
      const text = await file.text();
      let parsed;
      try {
        parsed = JSON.parse(text);
      } catch (pe) {
        throw new Error((this.t('admin_apps.import_bad_json') || 'Not valid JSON') + ': ' + (pe && pe.message ? pe.message : pe));
      }
      // Accept either a full pack {entries:[...]} or a bare array.
      const body = Array.isArray(parsed) ? {entries: parsed} : {entries: (parsed && parsed.entries) || []};
      const r = await fetch('/api/services/catalog/import', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      const j = await r.json();
      await this.loadAppsCatalog();
      const errs = (j.errors || []).length;
      this.appsCatalogStatus = (this.t('admin_apps.import_done') || 'Imported')
        + ': +' + (j.created || 0) + ' / ~' + (j.updated || 0) + (errs ? (' / ' + errs + ' err') : '');
      if (typeof this.toast === 'function') {
        this.toast(this.appsCatalogStatus, errs ? 'warning' : 'success');
      }
    } catch (err) {
      this.appsCatalogStatus = err && err.message ? err.message : String(err);
      if (typeof this.toast === 'function') {
        this.toast(this.appsCatalogStatus, 'error');
      }
    } finally {
      this.appsCatalogImporting = false;
      // Reset the file input so re-selecting the same file re-fires change.
      if (input) {
        input.value = '';
      }
    }
  },
};
