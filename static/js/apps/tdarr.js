// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Tdarr (distributed media-transcode automation).
//
// Encapsulates every Tdarr-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`. Card stats are Files / Transcode queue /
// Health queue / Space saved / Workers, sourced from
// `logic/apps/tdarr.py:fetch_data` via the cache-backed `appsAppData(inst)`.
// Tdarr is no-auth by default, so the api_key is OPTIONAL (sent as x-api-key
// only when set).
//
// File-scope IDE directives: see `bazarr.js`'s header for the full rationale.

// True when `app` is the Tdarr catalog template (slug match; falls back to a
// substring check on `app.name`).
function isTdarrApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'tdarr') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('tdarr') !== -1);
}

// Per-instance Tdarr data lookup -- reads the per-app data the generic
// dispatcher fetched from the cruddb stats + get-nodes via
// `logic/apps/tdarr.py:fetch_data`. Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides cleanly.
function tdarrData(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  if (!inst || !this.appsAppData) {
    return null;
  }
  const d = this.appsAppData(inst);
  if (!d || !d.available) {
    return null;
  }
  return d;
}

// Format an integer count with thousand separators; '—' for missing.
function tdarrCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Format the net space saved (a GB number) as a human size (GB → TB). '—' for
// missing; "0 GB" when nothing saved yet.
function tdarrSpace(d) {
  if (!d || d.space_saved_gb == null) {
    return '—';
  }
  const g = Number(d.space_saved_gb);
  if (!isFinite(g)) {
    return '—';
  }
  if (g >= 1024) {
    return (g / 1024).toLocaleString(undefined, {maximumFractionDigits: 1}) + ' TB';
  }
  return g.toLocaleString(undefined, {maximumFractionDigits: 1}) + ' GB';
}

// "active/nodes" workers label, e.g. "2/3". '—' for missing.
function tdarrWorkers(d) {
  if (!d) {
    return '—';
  }
  const a = Number(d.workers_active);
  const n = Number(d.nodes);
  if (!isFinite(a) || !isFinite(n)) {
    return '—';
  }
  return Math.round(a).toLocaleString() + '/' + Math.round(n).toLocaleString();
}

// Top breakdown list for a kind ('resolutions' | 'codecs' | 'containers') —
// [{name, count}] aggregated across libraries. [] when none.
function tdarrBreakdown(d, kind) {
  return (d && Array.isArray(d[kind])) ? d[kind] : [];
}

// Format a fps figure as a live transcode-speed label ('123 fps'); '—' for
// missing / zero (the card hides the chip then).
function tdarrFps(v) {
  const n = Number(v);
  if (v == null || !isFinite(n) || n <= 0) {
    return '—';
  }
  return n.toLocaleString(undefined, {maximumFractionDigits: 0}) + ' fps';
}

// Per-node rollup [{name, workers_active, capacity, fps, paused, idle}] from
// the get-nodes payload — every registered node, busiest-first. [] when none.
function tdarrNodeSummary(d) {
  return (d && Array.isArray(d.node_summary)) ? d.node_summary : [];
}

// Count of IDLE nodes (registered + has capacity + not paused but processing
// nothing) — the "a node joined but isn't working" warning. 0 when none.
function tdarrIdleNodes(d) {
  return d ? (Number(d.idle_nodes) || 0) : 0;
}

// Retention trend block from the lifespan tdarr_sampler (cumulative space-saved
// + queue burn-down + per-day throughput), or null while idle / no samples yet.
function tdarrTrend(inst) {
  /* jshint validthis: true */
  const d = (this.tdarrData ? this.tdarrData(inst) : null);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:d` per numeric series array (avoids re-render flicker on every
// Alpine flush).
const _tdarrTrendMemo = new WeakMap();

// SVG polyline points for a sparkline over a 0..200 × 0..32 viewBox, auto-scaled
// to the series' own min/max. '' when < 2 points. Memoised on the array ref.
function tdarrTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_tdarrTrendMemo.has(arr)) {
    return _tdarrTrendMemo.get(arr);
  }
  const W = 200, H = 32, n = arr.length;
  let min = Infinity, max = -Infinity;
  for (let i = 0; i < n; i++) {
    const v = Number(arr[i]) || 0;
    if (v < min) {
      min = v;
    }
    if (v > max) {
      max = v;
    }
  }
  const range = (max - min) || 1;
  const stepX = W / Math.max(1, n - 1);
  let d = '';
  for (let i = 0; i < n; i++) {
    const x = (i * stepX).toFixed(1);
    const y = (H - ((Number(arr[i]) || 0) - min) / range * H).toFixed(1);
    d += (i === 0 ? 'M' : 'L') + x + ',' + y + ' ';
  }
  d = d.trim();
  _tdarrTrendMemo.set(arr, d);
  return d;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Tdarr gets a
// 2-column span + a vertical telemetry-card layout like the rest of the family.
// requiresApiKey is FALSE — Tdarr is open by default; the editor still offers
// an OPTIONAL key field for auth-enabled setups.
export const extender = {
  slugs: ['tdarr'],
  requiresApiKey: false,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isTdarrApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `tdarr*`.
export const helpers = {
  tdarrIsApp: isTdarrApp,
  tdarrData: tdarrData,
  tdarrCount: tdarrCount,
  tdarrSpace: tdarrSpace,
  tdarrWorkers: tdarrWorkers,
  tdarrBreakdown: tdarrBreakdown,
  tdarrFps: tdarrFps,
  tdarrNodeSummary: tdarrNodeSummary,
  tdarrIdleNodes: tdarrIdleNodes,
  tdarrTrend: tdarrTrend,
  tdarrTrendPath: tdarrTrendPath,
};
