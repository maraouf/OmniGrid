// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Rundeck (runbook automation / job scheduler).
//
// Encapsulates every Rundeck-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   Rundeck authenticates with a user API token (X-Rundeck-Auth-Token header);
//   `logic/apps/rundeck.py:fetch_data` aggregates projects / jobs / running /
//   version, read through the cache-backed `appsAppData(inst)`.
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Rundeck catalog template (matched via slug; falls back
// to a substring check on `app.name`).
function isRundeckApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'rundeck') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('rundeck') !== -1);
}

// Per-instance Rundeck data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/rundeck.py:fetch_data`). Returns null
// while idle / pending / errored OR when the payload isn't available.
function rundeckData(inst) {
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

// Format an integer count; '—' for missing / non-finite.
function rundeckCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Human duration for a second count ('—' for non-positive) — "Xh Ym" / "Ym Zs"
// / "Zs". Used by the avg-run-time + next-scheduled-run stats.
function rundeckDuration(s) {
  const n = Number(s) || 0;
  if (n <= 0) {
    return '—';
  }
  if (n >= 3600) {
    return Math.floor(n / 3600) + 'h ' + Math.floor((n % 3600) / 60) + 'm';
  }
  if (n >= 60) {
    return Math.floor(n / 60) + 'm ' + Math.floor(n % 60) + 's';
  }
  return Math.round(n) + 's';
}

// Recent-execution failure rate (0..100) — '—' when no finished runs in the
// window (so the card doesn't show a misleading 0% on an idle Rundeck).
function rundeckFailureRate(inst) {
  /* jshint validthis: true */
  const d = rundeckData.call(this, inst);
  if (!d || !(Number(d.recent_completed) || 0)) {
    return '—';
  }
  return (Number(d.failure_rate) || 0) + '%';
}

// Per-project failure-rate breakdown (which project is flakiest). Returns the
// backend's `per_project_failure` array (`[{name, failure_rate, completed,
// failed}]`, flakiest first) or [] when absent. Drawer-only on the SPA.
function rundeckPerProjectFailure(inst) {
  /* jshint validthis: true */
  const d = rundeckData.call(this, inst);
  const rows = (d && Array.isArray(d.per_project_failure)) ? d.per_project_failure : null;
  return rows || [];
}

// Failure-rate trend (from the lifespan rundeck_sampler). Returns the trend
// block `{series, peak, avg, samples, current, days}` or null.
function rundeckTrend(inst) {
  /* jshint validthis: true */
  const d = rundeckData.call(this, inst);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:points` string per series array reference (avoids re-render
// flicker on every Alpine flush -- the canonical SVG-builder memo pattern).
const _rdSparkMemo = new WeakMap();

// SVG polyline points for the failure-rate sparkline over a 0..100 × 0..24
// viewBox (series is already a 0..100 percent, so the Y scale is fixed at 100
// — a 50% spike always reads half-height). '' when < 2 points.
function rundeckSparkPoints(inst) {
  /* jshint validthis: true */
  const tr = rundeckTrend.call(this, inst);
  const series = (tr && Array.isArray(tr.series)) ? tr.series : null;
  if (!series || series.length < 2) {
    return '';
  }
  if (_rdSparkMemo.has(series)) {
    return _rdSparkMemo.get(series);
  }
  const W = 100, H = 24, n = series.length;
  const parts = [];
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * W;
    const v = Math.max(0, Math.min(100, Number(series[i]) || 0));
    const y = H - (v / 100) * H;
    parts.push((Math.round(x * 100) / 100) + ',' + (Math.round(y * 100) / 100));
  }
  const pts = parts.join(' ');
  _rdSparkMemo.set(series, pts);
  return pts;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. requiresApiKey true
// (API-token editor) and a wide card (multi-stat grid).
export const extender = {
  slugs: ['rundeck'],
  requiresApiKey: true,
  cardSpan(app) {
    return isRundeckApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `rundeck*`.
export const helpers = {
  rundeckIsApp: isRundeckApp,
  rundeckData: rundeckData,
  rundeckCount: rundeckCount,
  rundeckDuration: rundeckDuration,
  rundeckFailureRate: rundeckFailureRate,
  rundeckPerProjectFailure: rundeckPerProjectFailure,
  rundeckTrend: rundeckTrend,
  rundeckSparkPoints: rundeckSparkPoints,
};
