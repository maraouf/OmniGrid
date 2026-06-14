// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Radarr (movie collection manager).
//
// Encapsulates every Radarr-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   Radarr chips render a 4-stat panel (movies / missing / downloading /
//   disk free) sourced from Radarr's `GET /api/v3/movie` + queue / disk
//   / health endpoints. The card reads the per-app data the generic
//   dispatcher fetched via `logic/apps/radarr.py:fetch_data`, so it never
//   triggers a per-card round-trip on the hot path. `radarrData(inst)`
//   reads it via the cache-backed `appsAppData(inst)` helper (same path
//   Bazarr / Seerr / Speedtest / APC use).
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Radarr catalog template (matched via slug; falls
// back to a substring check on `app.name` so an operator-edited chip that
// dropped the catalog link but kept the brand still resolves).
function isRadarrApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'radarr') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('radarr') !== -1);
}

// Per-instance Radarr data lookup -- reads the per-app data the generic
// dispatcher fetched from `GET /api/v3/movie` (via
// `logic/apps/radarr.py:fetch_data`). Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides
// cleanly. No module-scope memo needed: `this.appsAppData(inst)` is itself
// cache-backed and only READS during render.
function radarrData(inst) {
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
function radarrCount(v) {
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
function radarrGb(v) {
  const n = Number(v);
  if (v == null || !isFinite(n) || n <= 0) {
    return '—';
  }
  return n.toLocaleString(undefined, {maximumFractionDigits: 1}) + ' GB';
}

// Library/backlog/disk trend block {days, samples, latest_total, peak_missing,
// latest_missing, latest_disk_free_gb, disk_runway_days, series_total,
// series_missing, series_disk_free} from the shared lifespan servarr_sampler,
// or null while idle / no samples yet.
function radarrTrend(inst) {
  /* jshint validthis: true */
  const d = (this.radarrData ? this.radarrData(inst) : null);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:d` per numeric series array (the canonical SVG-builder memo —
// avoids re-render flicker on every Alpine flush).
const _radarrTrendMemo = new WeakMap();

// SVG polyline points for a sparkline over a 0..200 × 0..32 viewBox, auto-scaled
// to the series' own min/max. '' when < 2 points. Memoised on the array ref.
function radarrTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_radarrTrendMemo.has(arr)) {
    return _radarrTrendMemo.get(arr);
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
  _radarrTrendMemo.set(arr, d);
  return d;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Radarr gets a
// 2-column span so the 4-stat panel doesn't squeeze the per-instance host
// list, and a vertical telemetry-card layout like Bazarr / Seerr / APC.
export const extender = {
  slugs: ['radarr'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isRadarrApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `radarr*` so they
// don't collide with other per-app modules' helpers.
export const helpers = {
  radarrIsApp: isRadarrApp,
  radarrData: radarrData,
  radarrCount: radarrCount,
  radarrGb: radarrGb,
  radarrTrend: radarrTrend,
  radarrTrendPath: radarrTrendPath,
};
