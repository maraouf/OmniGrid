// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Tautulli (Plex monitoring + statistics).
//
// Encapsulates every Tautulli-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`. Mirrors the *arr / Kavita card shape (a
// 4-stat panel) but Tautulli monitors a Plex server, so the stats are
// Streams / Transcodes / Bandwidth / Libraries, sourced from
// `logic/apps/tautulli.py:fetch_data` via the cache-backed `appsAppData(inst)`.
// No Storage section (Tautulli reports activity, not per-mount disks).
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Tautulli catalog template (slug match; falls back to a
// substring check on `app.name`).
function isTautulliApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'tautulli') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('tautulli') !== -1);
}

// Per-instance Tautulli data lookup -- reads the per-app data the generic
// dispatcher fetched from `cmd=get_activity` (+ libraries / version) via
// `logic/apps/tautulli.py:fetch_data`. Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides cleanly.
function tautulliData(inst) {
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
function tautulliCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Format a kbps bandwidth figure as a human rate (kbps / Mbps / Gbps). '—' for
// missing / zero (nothing streaming).
function tautulliBandwidth(kbps) {
  const n = Number(kbps);
  if (kbps == null || !isFinite(n) || n <= 0) {
    return '—';
  }
  if (n < 1000) {
    return Math.round(n).toLocaleString() + ' kbps';
  }
  const mbps = n / 1000;
  if (mbps < 1000) {
    return mbps.toLocaleString(undefined, {maximumFractionDigits: 1}) + ' Mbps';
  }
  return (mbps / 1000).toLocaleString(undefined, {maximumFractionDigits: 1}) + ' Gbps';
}

// SVG `d` path for the plays-over-time sparkline (last 30 daily buckets), over
// a 200x32 viewBox — mirrors the Tracearr / Speedtest sparkline. '' when < 2
// points so the chart hides cleanly. `this` is the Alpine component.
function tautulliPlaysPath(inst) {
  /* jshint validthis: true */
  const d = tautulliData.call(this, inst);
  const raw = (d && Array.isArray(d.plays_series)) ? d.plays_series : [];
  const series = raw.map(Number).filter(isFinite);
  if (series.length < 2) {
    return '';
  }
  const W = 200, H = 32;
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = (max - min) || 1;
  const stepX = W / Math.max(1, series.length - 1);
  let path = '';
  for (let i = 0; i < series.length; i++) {
    const x = (i * stepX).toFixed(1);
    const y = (H - ((series[i] - min) / range) * H).toFixed(1);
    path += (i === 0 ? 'M' : 'L') + x + ',' + y + ' ';
  }
  return path.trim();
}

// True when there's a plays series worth charting (>= 2 points).
function tautulliHasPlays(inst) {
  /* jshint validthis: true */
  const d = tautulliData.call(this, inst);
  return !!(d && Array.isArray(d.plays_series) && d.plays_series.length >= 2);
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Tautulli gets a
// 2-column span + a vertical telemetry-card layout like the rest of the family.
export const extender = {
  slugs: ['tautulli'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isTautulliApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `tautulli*`.
export const helpers = {
  tautulliIsApp: isTautulliApp,
  tautulliData: tautulliData,
  tautulliCount: tautulliCount,
  tautulliBandwidth: tautulliBandwidth,
  tautulliPlaysPath: tautulliPlaysPath,
  tautulliHasPlays: tautulliHasPlays,
};
