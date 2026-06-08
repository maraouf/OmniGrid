// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Apprise (caronc/apprise-api), the notification
// fan-out API OmniGrid itself uses for deploy / op notifications.
//
// Encapsulates every Apprise-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges this module's `helpers`
// into the Alpine component AND exposes the extender record (slugs /
// requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   The chip renders a small panel (how many notification endpoints, which
//   services, which tags) sourced from `GET /json/urls/<key>?privacy=1`,
//   via `logic/apps/apprise.py:fetch_data`. The card reads the per-app
//   data the generic dispatcher fetched, so it never triggers a per-card
//   round-trip on the hot path. `appriseData(inst)` reads it via the
//   cache-backed `appsAppData(inst)` helper (same path the other apps use).
//
// No auth (requiresApiKey: false) -- the apprise-api server has no
// built-in authentication; the editor only needs the cache TTL + the
// shared Test-connection. The instance URL is set in the generic chip URL
// field (point it at the apprise-api root, or at .../notify/<key> for a
// non-default config key).
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Apprise catalog template (matched via slug; falls
// back to a name check so an operator-edited chip that dropped the catalog
// link but kept the brand still resolves).
function isAppriseApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'apprise') {
    return true;
  }
  return String(app.name || '').toLowerCase().indexOf('apprise') !== -1;
}

// Per-instance Apprise summary -- reads the per-app data the generic
// dispatcher fetched. Returns null while idle / pending / errored OR when
// the payload isn't available, so the panel gate hides cleanly.
function appriseData(inst) {
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
function appriseCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// The configured services list ([{scheme, name, count}], [] when absent).
function appriseServices(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  const d = (this.appriseData ? this.appriseData(inst) : null);
  if (!d || !Array.isArray(d.services)) {
    return [];
  }
  return d.services;
}

// The configured tags list -- drops the implicit "all" tag so the chip
// strip shows only the operator-meaningful routing tags ([] when absent).
function appriseTags(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  const d = (this.appriseData ? this.appriseData(inst) : null);
  if (!d || !Array.isArray(d.tags)) {
    return [];
  }
  return d.tags.filter((t) => String(t || '').toLowerCase() !== 'all');
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. requiresApiKey
// is false: the apprise-api server has no built-in auth.
export const extender = {
  slugs: ['apprise'],
  requiresApiKey: false,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isAppriseApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `apprise*` so they
// don't collide with other per-app modules' helpers.
export const helpers = {
  appriseIsApp: isAppriseApp,
  appriseData: appriseData,
  appriseCount: appriseCount,
  appriseServices: appriseServices,
  appriseTags: appriseTags,
};
