// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- RustDesk Server (Pro).
//
// Encapsulates every RustDesk-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   RustDesk Server Pro authenticates the console username + password (login →
//   Bearer token, re-auth per fetch); `logic/apps/rustdesk.py:fetch_data`
//   aggregates peers / online / users / version, read through the cache-backed
//   `appsAppData(inst)`. The OSS server has no API — the card degrades to a
//   clear "needs RustDesk Server Pro" error.
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the RustDesk catalog template (matched via slug; falls
// back to a substring check on `app.name`).
function isRustdeskApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug.indexOf('rustdesk') !== -1) {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('rustdesk') !== -1);
}

// Per-instance RustDesk data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/rustdesk.py:fetch_data`). Returns null
// while idle / pending / errored OR when the payload isn't available.
function rustdeskData(inst) {
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
function rustdeskCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// "online / total" device fraction string for the hero stat.
function rustdeskDeviceFraction(d) {
  if (!d) {
    return '—';
  }
  return rustdeskCount(d.devices_online) + ' / ' + rustdeskCount(d.devices);
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. requiresApiKey true
// (username + password editor) and a wide card (multi-stat grid).
export const extender = {
  slugs: ['rustdesk', 'rustdesk-server', 'rustdesk-server-pro'],
  requiresApiKey: true,
  cardSpan(app) {
    return isRustdeskApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `rustdesk*`.
export const helpers = {
  rustdeskIsApp: isRustdeskApp,
  rustdeskData: rustdeskData,
  rustdeskCount: rustdeskCount,
  rustdeskDeviceFraction: rustdeskDeviceFraction,
};
