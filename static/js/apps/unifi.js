// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- UniFi (UniFi Network / UniFi OS console).
//
// Encapsulates every UniFi-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   UniFi chips render a stat panel (sites / devices online+total / APs /
//   switches / gateways / clients wired+wireless + version) sourced from the
//   official UniFi Network Integration API (X-API-KEY) via
//   `logic/apps/unifi.py:fetch_data`. The card reads the per-app data the
//   generic dispatcher fetched, so it never triggers a per-card round-trip on
//   the hot path. `unifiData(inst)` reads it via the cache-backed
//   `appsAppData(inst)` helper (same path GitSync / Grafana use).
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the UniFi catalog template (matched via slug; falls back
// to a substring check on `app.name` so an operator-edited chip that dropped the
// catalog link but kept the brand still resolves -- 'unifi' / 'UniFi OS' /
// 'Ubiquiti UniFi' all hit).
function isUnifiApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  // Substring match so every UniFi variant resolves — the canonical
  // `unifi-os-server` template, the bare `unifi` / `unifi-network` / `unifi-os`
  // aliases, AND the editor gate's lowercased catalog NAME ("unifi os server").
  if (slug.indexOf('unifi') !== -1) {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('unifi') !== -1);
}

// Per-instance UniFi data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/unifi.py:fetch_data`). Returns null while
// idle / pending / errored OR when the payload isn't available, so the panel
// gate hides cleanly.
function unifiData(inst) {
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
function unifiCount(v) {
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
function unifiDeviceFraction(d) {
  if (!d) {
    return '—';
  }
  return unifiCount(d.devices_online) + ' / ' + unifiCount(d.devices);
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. UniFi gets a 2-column
// span so the stat panel doesn't squeeze the per-instance host list, and a
// vertical telemetry-card layout like GitSync / Grafana.
export const extender = {
  slugs: ['unifi-os-server', 'unifi', 'unifi-network', 'unifi-os'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isUnifiApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `unifi*` so they don't
// collide with other per-app modules' helpers.
export const helpers = {
  unifiIsApp: isUnifiApp,
  unifiData: unifiData,
  unifiCount: unifiCount,
  unifiDeviceFraction: unifiDeviceFraction,
};
