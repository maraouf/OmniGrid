// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- ddns-updater (qdm12/ddns-updater).
//
// Encapsulates every ddns-updater-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`.
//
// Data source
//   ddns-updater has NO JSON API; the backend parses its web-UI HTML table
//   (via `logic/apps/ddns_updater.py:fetch_data`) into records + a public
//   IP. The card reads that per-app data via the cache-backed
//   `appsAppData(inst)` helper. No auth (requiresApiKey: false).
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the ddns-updater catalog template (slug match; falls
// back to a substring check on `app.name`).
function isDdnsApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'ddns-updater') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('ddns') !== -1);
}

// Per-instance ddns-updater data lookup -- reads the parsed records summary.
// Returns null while idle / pending / errored OR when the payload isn't
// available, so the panel gate hides cleanly.
function ddnsData(inst) {
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

// Format an integer count; '—' for missing.
function ddnsCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// The list of failing-record domains ([] when all up to date / payload absent).
function ddnsFailing(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  const d = (this.ddnsData ? this.ddnsData(inst) : null);
  if (!d || !Array.isArray(d.failing_domains)) {
    return [];
  }
  return d.failing_domains;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. No auth, so
// requiresApiKey is false; a 2-column span + vertical telemetry layout like
// the other stat-grid apps.
export const extender = {
  slugs: ['ddns-updater'],
  requiresApiKey: false,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isDdnsApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `ddns*`.
export const helpers = {
  ddnsIsApp: isDdnsApp,
  ddnsData: ddnsData,
  ddnsCount: ddnsCount,
  ddnsFailing: ddnsFailing,
};
