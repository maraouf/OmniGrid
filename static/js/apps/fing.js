// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Fing (network device inventory + presence).
//
// Encapsulates every Fing-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   `logic/apps/fing.py:fetch_data` GETs the Fing Local API
//   (`/1/devices?auth=<key>`) and shapes it into device totals + online/offline
//   counts + type/vendor breakdowns + a NEW-device count + a compact device
//   list, read through the cache-backed `appsAppData(inst)`. The occupancy
//   sparkline is the lifespan `fing_sampler`'s online-device daily-MAX series
//   (Fing's Local API is current-state-only).
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Fing catalog template (matched via slug; falls back to
// a substring check on `app.name`).
function isFingApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'fing') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('fing') !== -1);
}

// Per-instance Fing data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/fing.py:fetch_data`). Returns null while
// idle / pending / errored OR when the payload isn't available.
function fingData(inst) {
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

// Format an integer count with thousands separators; '—' for missing.
function fingCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// The per-device detail rows [{name, ip, mac, type, vendor, online, new,
// first_seen}] — [] when none.
function fingDevices(inst) {
  /* jshint validthis: true */
  const d = fingData.call(this, inst);
  return (d && Array.isArray(d.devices)) ? d.devices : [];
}

// The device-type breakdown [{name, count}] — [] when none.
function fingByType(inst) {
  /* jshint validthis: true */
  const d = fingData.call(this, inst);
  return (d && Array.isArray(d.by_type)) ? d.by_type : [];
}

// The online-occupancy history block `{online_series, peak_online,
// current_online, current_total, new_event_days, samples, days}` or null.
function fingHistory(inst) {
  /* jshint validthis: true */
  const d = fingData.call(this, inst);
  return (d && d.history && typeof d.history === 'object') ? d.history : null;
}

// Memo: stable `:points` string per series array reference (avoids re-render
// flicker on every Alpine flush -- the canonical SVG-builder memo pattern).
const _fingSparkMemo = new WeakMap();

// SVG polyline points for the online-device occupancy sparkline over a
// 0..100 × 0..24 viewBox. '' when there's < 2 points (nothing to draw yet).
function fingSparkPoints(inst) {
  /* jshint validthis: true */
  const h = fingHistory.call(this, inst);
  const series = (h && Array.isArray(h.online_series)) ? h.online_series : null;
  if (!series || series.length < 2) {
    return '';
  }
  if (_fingSparkMemo.has(series)) {
    return _fingSparkMemo.get(series);
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
  _fingSparkMemo.set(series, pts);
  return pts;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. api_key auth (the
// Local API key, sent as ?auth=<key>) + a 2-column card span (device list +
// breakdowns + occupancy trend).
export const extender = {
  slugs: ['fing'],
  requiresApiKey: true,
  cardSpan(app) {
    return isFingApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `fing*`.
export const helpers = {
  fingIsApp: isFingApp,
  fingData: fingData,
  fingCount: fingCount,
  fingDevices: fingDevices,
  fingByType: fingByType,
  fingHistory: fingHistory,
  fingSparkPoints: fingSparkPoints,
};
