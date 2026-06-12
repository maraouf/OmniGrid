// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Lidarr (music collection manager).
//
// Encapsulates every Lidarr-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`. Mirrors `sonarr.js` (Lidarr is the music
// *arr): a 4-stat panel (artists / missing albums / downloading / monitored)
// + a multi-mount Storage list, sourced from `logic/apps/lidarr.py:fetch_data`
// via the cache-backed `appsAppData(inst)`.
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Lidarr catalog template (slug match; falls back to a
// substring check on `app.name`).
function isLidarrApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'lidarr') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('lidarr') !== -1);
}

// Per-instance Lidarr data lookup -- reads the per-app data the generic
// dispatcher fetched from `GET /api/v1/artist` (via
// `logic/apps/lidarr.py:fetch_data`). Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides
// cleanly.
function lidarrData(inst) {
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
function lidarrCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Format a GiB value as a compact "N.N GB" label; '—' for missing / zero.
function lidarrGb(v) {
  const n = Number(v);
  if (v == null || !isFinite(n) || n <= 0) {
    return '—';
  }
  return n.toLocaleString(undefined, {maximumFractionDigits: 1}) + ' GB';
}

// Library/backlog/disk trend block from the shared lifespan servarr_sampler,
// or null while idle / no samples yet.
function lidarrTrend(inst) {
  /* jshint validthis: true */
  const d = (this.lidarrData ? this.lidarrData(inst) : null);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:d` per numeric series array (avoids re-render flicker on
// every Alpine flush).
const _lidarrTrendMemo = new WeakMap();

// SVG polyline points for a sparkline over a 0..200 × 0..32 viewBox, auto-scaled
// to the series' own min/max. '' when < 2 points. Memoised on the array ref.
function lidarrTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_lidarrTrendMemo.has(arr)) {
    return _lidarrTrendMemo.get(arr);
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
  _lidarrTrendMemo.set(arr, d);
  return d;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Lidarr gets a
// 2-column span + a vertical telemetry-card layout like Sonarr / Radarr.
export const extender = {
  slugs: ['lidarr'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isLidarrApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `lidarr*`.
export const helpers = {
  lidarrIsApp: isLidarrApp,
  lidarrData: lidarrData,
  lidarrCount: lidarrCount,
  lidarrGb: lidarrGb,
  lidarrTrend: lidarrTrend,
  lidarrTrendPath: lidarrTrendPath,
};
