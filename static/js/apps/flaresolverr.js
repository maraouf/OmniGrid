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
};
