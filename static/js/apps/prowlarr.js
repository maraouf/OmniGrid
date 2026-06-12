// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Prowlarr (indexer manager, *arr stack).
//
// Encapsulates every Prowlarr-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`. Mirrors `lidarr.js` (same *arr design) but
// Prowlarr manages INDEXERS not a media library, so the card is a 4-stat panel
// (indexers enabled/total / apps synced / queries / grabs) with NO Storage
// section, sourced from `logic/apps/prowlarr.py:fetch_data` via the
// cache-backed `appsAppData(inst)`.
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Prowlarr catalog template (slug match; falls back to a
// substring check on `app.name`).
function isProwlarrApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'prowlarr') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('prowlarr') !== -1);
}

// Per-instance Prowlarr data lookup -- reads the per-app data the generic
// dispatcher fetched from `GET /api/v1/indexer` (+ apps / stats / health) via
// `logic/apps/prowlarr.py:fetch_data`. Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides cleanly.
function prowlarrData(inst) {
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
function prowlarrCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Comma-joined list of the REAL connected *arr app names (Sonarr / Radarr /
// Whisparr / …) so the drawer + tooltip show what's actually synced rather
// than a bare count. '' when none / no data.
function prowlarrAppsNames(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  const d = (this.prowlarrData ? this.prowlarrData(inst) : null);
  if (!d || !Array.isArray(d.apps_names) || !d.apps_names.length) {
    return '';
  }
  return d.apps_names.filter((n) => typeof n === 'string' && n).join(', ');
}

// The synced *arr apps as styled-chip descriptors ({name, icon}) — one per
// connected application, brand icon resolved via the shared iconUrlFor()
// resolver. Drives the chip strip in the card (replaces the old raw
// comma-joined text). [] when nothing is synced / no data.
function prowlarrAppsList(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  const d = (this.prowlarrData ? this.prowlarrData(inst) : null);
  if (!d || !Array.isArray(d.apps_names)) {
    return [];
  }
  const resolve = (typeof this.iconUrlFor === 'function') ? (n) => this.iconUrlFor(n) : () => '';
  return d.apps_names
    .filter((n) => typeof n === 'string' && n.trim())
    .map((n) => {
      const name = n.trim();
      return {name: name, icon: resolve(name) || ''};
    });
}

// "enabled / total" label for the Indexers stat cell. '—' when no data.
function prowlarrIndexersLabel(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  const d = (this.prowlarrData ? this.prowlarrData(inst) : null);
  if (!d) {
    return '—';
  }
  return prowlarrCount(d.indexers_enabled) + ' / ' + prowlarrCount(d.indexers_total);
}

// Counter-rate retention trend from the lifespan prowlarr_sampler (per-day
// query/grab throughput + daily failure-rate), or null while idle / no samples.
function prowlarrTrend(inst) {
  /* jshint validthis: true */
  const d = (this.prowlarrData ? this.prowlarrData(inst) : null);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:d` per numeric series array (avoids re-render flicker on every
// Alpine flush).
const _prowlarrTrendMemo = new WeakMap();

// SVG polyline points for a sparkline over a 0..200 × 0..32 viewBox, auto-scaled
// to the series' own min/max. '' when < 2 points. Memoised on the array ref.
function prowlarrTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_prowlarrTrendMemo.has(arr)) {
    return _prowlarrTrendMemo.get(arr);
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
  _prowlarrTrendMemo.set(arr, d);
  return d;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Prowlarr gets a
// 2-column span + a vertical telemetry-card layout like the rest of the *arr
// family.
export const extender = {
  slugs: ['prowlarr'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isProwlarrApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `prowlarr*`.
export const helpers = {
  prowlarrIsApp: isProwlarrApp,
  prowlarrData: prowlarrData,
  prowlarrCount: prowlarrCount,
  prowlarrAppsNames: prowlarrAppsNames,
  prowlarrAppsList: prowlarrAppsList,
  prowlarrIndexersLabel: prowlarrIndexersLabel,
  prowlarrTrend: prowlarrTrend,
  prowlarrTrendPath: prowlarrTrendPath,
};
