// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Bazarr (subtitle manager).
//
// Encapsulates every Bazarr-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app
// module's `helpers` into the Alpine component AND exposes the
// extender record (slugs / requiresApiKey / cardSpan) to the
// generic helpers via `window.OG_APPS_EXTENDERS`.
//
// Data source
//   Bazarr chips render a 4-stat panel (episodes / movies missing
//   subtitles, throttled providers, health issues) sourced from
//   Bazarr's `GET /api/badges`. The card reads the per-app data the
//   generic dispatcher fetched via `logic/apps/bazarr.py:fetch_data`,
//   so it never triggers a per-card round-trip on the hot path.
//   `bazarrData(inst)` reads it via the cache-backed `appsAppData(inst)`
//   helper (same path Speedtest / APC use).
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for
// the full rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Bazarr catalog template (matched via slug;
// falls back to a substring check on `app.name` so an operator-edited
// chip that dropped the catalog link but kept the brand still resolves).
function isBazarrApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'bazarr') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('bazarr') !== -1);
}

// Per-instance Bazarr data lookup -- reads the per-app data the generic
// dispatcher fetched from `GET /api/badges` (via
// `logic/apps/bazarr.py:fetch_data`). Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides
// cleanly. No module-scope memo needed: `this.appsAppData(inst)` is itself
// cache-backed and only READS during render (same contract APC / Speedtest
// rely on).
function bazarrData(inst) {
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
function bazarrCount(v) {
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
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Bazarr gets a
// 2-column span so the 4-stat panel doesn't squeeze the per-instance host
// list, and a vertical telemetry-card layout like APC.
export const extender = {
  slugs: ['bazarr'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isBazarrApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `bazarr*` so they
// don't collide with other per-app modules' helpers.
export const helpers = {
  bazarrIsApp: isBazarrApp,
  bazarrData: bazarrData,
  bazarrCount: bazarrCount,
};
