// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- FlareSolverr (Cloudflare-challenge solver proxy).
//
// Encapsulates every FlareSolverr-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   FlareSolverr has no auth; `logic/apps/flaresolverr.py:fetch_data` GETs / for
//   ready/version/user-agent + POSTs /v1 {cmd: sessions.list} for the active
//   session count, read through the cache-backed `appsAppData(inst)`.
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the FlareSolverr catalog template (matched via slug; falls
// back to a substring check on `app.name`).
function isFlaresolverrApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'flaresolverr') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('flaresolverr') !== -1);
}

// Per-instance FlareSolverr data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/flaresolverr.py:fetch_data`). Returns null
// while idle / pending / errored OR when the payload isn't available.
function flaresolverrData(inst) {
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
function flaresolverrCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// 30-day open-session usage trend (from the lifespan flaresolverr_sampler --
// FlareSolverr has no historical/request-volume API). Returns the usage block
// `{series, peak, avg, active_days, samples, current, days}` or null.
function flaresolverrUsage(inst) {
  /* jshint validthis: true */
  const d = flaresolverrData.call(this, inst);
  return (d && d.usage && typeof d.usage === 'object') ? d.usage : null;
}

// Memo: stable `:points` string per series array reference (avoids re-render
// flicker on every Alpine flush -- the canonical SVG-builder memo pattern).
const _fsSparkMemo = new WeakMap();

// SVG polyline points for the usage sparkline over a 0..100 × 0..24 viewBox.
// '' when there's < 2 points (nothing to draw yet).
function flaresolverrSparkPoints(inst) {
  /* jshint validthis: true */
  const u = flaresolverrUsage.call(this, inst);
  const series = (u && Array.isArray(u.series)) ? u.series : null;
  if (!series || series.length < 2) {
    return '';
  }
  if (_fsSparkMemo.has(series)) {
    return _fsSparkMemo.get(series);
  }
  const W = 100, H = 24, n = series.length;
  let max = 1;
  for (let i = 0; i < n; i++) {
    const v = Number(series[i]) || 0;
    if (v > max) {
      max = v;
    }
  }
  const parts = [];
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * W;
    const y = H - ((Number(series[i]) || 0) / max) * H;
    parts.push((Math.round(x * 100) / 100) + ',' + (Math.round(y * 100) / 100));
  }
  const pts = parts.join(' ');
  _fsSparkMemo.set(series, pts);
  return pts;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. No api_key (the proxy
// has no auth) and the default single-column layout (a compact status tile).
export const extender = {
  slugs: ['flaresolverr'],
  requiresApiKey: false,
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `flaresolverr*`.
export const helpers = {
  flaresolverrIsApp: isFlaresolverrApp,
  flaresolverrData: flaresolverrData,
  flaresolverrCount: flaresolverrCount,
  flaresolverrUsage: flaresolverrUsage,
  flaresolverrSparkPoints: flaresolverrSparkPoints,
};
