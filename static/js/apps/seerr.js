// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Seerr (Overseerr / Jellyseerr media-request manager).
//
// Encapsulates every Seerr-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   Seerr chips render a request-queue stat panel (pending / approved /
//   processing / available + open issues) sourced from Seerr's
//   `GET /api/v1/request/count`. The card reads the per-app data the
//   generic dispatcher fetched via `logic/apps/seerr.py:fetch_data`, so
//   it never triggers a per-card round-trip on the hot path.
//   `seerrData(inst)` reads it via the cache-backed `appsAppData(inst)`
//   helper (same path Bazarr / Speedtest / APC use).
//
// The headline feature (request a movie by title, suggest a random movie)
// lives entirely in the AI / Telegram skill path -- this SPA module only
// renders the expanded card; the skills are dispatched server-side.
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Seerr catalog template (matched via slug; falls
// back to a substring check on `app.name` so an operator-edited chip that
// dropped the catalog link but kept the brand still resolves).
function isSeerrApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'seerr') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('seerr') !== -1);
}

// Per-instance Seerr data lookup -- reads the per-app data the generic
// dispatcher fetched from `GET /api/v1/request/count` (via
// `logic/apps/seerr.py:fetch_data`). Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides
// cleanly. No module-scope memo needed: `this.appsAppData(inst)` is itself
// cache-backed and only READS during render.
function seerrData(inst) {
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
function seerrCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Request-backlog trend block {days, samples, peak_pending, latest_pending,
// series} from the lifespan seerr_sampler, or null while idle / no samples yet.
function seerrTrend(inst) {
  /* jshint validthis: true */
  const d = (this.seerrData ? this.seerrData(inst) : null);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:points` per numeric series array (the canonical SVG-builder
// memo — avoids re-render flicker on every Alpine flush).
const _seerrTrendMemo = new WeakMap();

// SVG polyline points for the pending-backlog sparkline over a 0..200 × 0..32
// viewBox. '' when < 2 points.
function seerrTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_seerrTrendMemo.has(arr)) {
    return _seerrTrendMemo.get(arr);
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
  _seerrTrendMemo.set(arr, d);
  return d;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Seerr gets a
// 2-column span so the request-queue panel doesn't squeeze the
// per-instance host list, and a vertical telemetry-card layout like
// Bazarr / APC.
export const extender = {
  slugs: ['seerr'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isSeerrApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `seerr*` so they don't
// collide with other per-app modules' helpers.
export const helpers = {
  seerrIsApp: isSeerrApp,
  seerrData: seerrData,
  seerrCount: seerrCount,
  seerrTrend: seerrTrend,
  seerrTrendPath: seerrTrendPath,
};
