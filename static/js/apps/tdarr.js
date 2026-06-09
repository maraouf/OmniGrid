// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
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

// Active-worker detail list ([{node, file, pct, type}]) — what each worker is
// processing right now. [] when idle / missing.
function tdarrWorkersList(d) {
  return (d && Array.isArray(d.workers)) ? d.workers : [];
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
  tdarrWorkersList: tdarrWorkersList,
};
