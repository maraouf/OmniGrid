// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Kavita (self-hosted digital library / reader).
//
// Encapsulates every Kavita-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`. Mirrors the *arr card shape (a 4-stat panel)
// but Kavita manages a digital library, so the stats are
// Libraries / Series / Volumes / Size, sourced from
// `logic/apps/kavita.py:fetch_data` via the cache-backed `appsAppData(inst)`.
// No Storage section (Kavita reports a single total size, not per-mount disks).
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Kavita catalog template (slug match; falls back to a
// substring check on `app.name`).
function isKavitaApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'kavita') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('kavita') !== -1);
}

// Per-instance Kavita data lookup -- reads the per-app data the generic
// dispatcher fetched from `GET /api/Library` (+ server stats) via
// `logic/apps/kavita.py:fetch_data`. Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides cleanly.
function kavitaData(inst) {
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
function kavitaCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Format a byte count as a human size (MiB / GiB / TiB). '—' for missing /
// zero (e.g. when the api_key isn't an admin's, server stats come back 0).
function kavitaSize(bytes) {
  const n = Number(bytes);
  if (bytes == null || !isFinite(n) || n <= 0) {
    return '—';
  }
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
  let val = n;
  let idx = 0;
  while (val >= 1024 && idx < units.length - 1) {
    val /= 1024;
    idx += 1;
  }
  return val.toLocaleString(undefined, { maximumFractionDigits: 1 }) + ' ' + units[idx];
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Kavita gets a
// 2-column span + a vertical telemetry-card layout like the *arr family.
export const extender = {
  slugs: ['kavita'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isKavitaApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `kavita*`.
export const helpers = {
  kavitaIsApp: isKavitaApp,
  kavitaData: kavitaData,
  kavitaCount: kavitaCount,
  kavitaSize: kavitaSize,
};
