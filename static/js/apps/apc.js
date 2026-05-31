// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- APC (UPS / NMC / PDU).
//
// Encapsulates every APC-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app
// module's `helpers` into the Alpine component AND exposes the
// extender record (slugs / cardSpan) to the generic helpers
// via `window.OG_APPS_EXTENDERS`.
//
// Data source
//   APC chips render a 5-stat panel (battery / output load /
//   runtime / battery temperature / battery state) sourced from
//   the `host_snmp_samples` DB table -- NOT the live host row.
//   The card reads the per-app data the generic dispatcher
//   fetched via `logic/apps/apc.py:fetch_data` (latest sample
//   row for the pinned host), so it never triggers a per-card
//   SNMP round-trip on the hot path. The fetch_data payload:
//     available          (bool -- false on non-UPS / no row yet)
//     battery_percent    (0..100)
//     load_percent       (0..100)
//     battery_runtime_s  (seconds)
//     battery_temp_c     (Celsius)
//     ups_status         ('On Line' / 'On Battery' / ...)
//     battery_status     ('Normal' / 'Low' / ...)
//   `hostUpsData(inst)` reads it via the generic, cache-backed
//   `this.appsAppData(inst)` helper (same path Speedtest uses).
//
// Extender shape
//   {slugs, cardSpan(app)} -- consumed by generic helpers
//   in `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`.

// True when `app` is the APC catalog template (matched via slug;
// falls back to a substring check on `app.name` so an
// operator-edited chip that dropped the catalog link but kept
// the brand still resolves -- generous match so 'APC Smart-UPS'
// / 'APC UPS' / 'APC RT 3000XL' all hit).
function isApcApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'apc') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('apc') !== -1);
}

// Returns true when ANY UPS field is populated. Extracted from
// the multi-clause `if (batt == null && load == null && ...)`
// guard inside `hostUpsData` so PyCharm's
// OverlyComplexBooleanExpressionJS inspection stays quiet AND
// the intent reads more clearly ("does this host have UPS
// data?"). Caller treats falsy === "render nothing".
function _hasAnyUpsData(batt, load, rt, temp, status) {
  if (batt != null) {
    return true;
  }
  if (load != null) {
    return true;
  }
  if (rt != null) {
    return true;
  }
  if (temp != null) {
    return true;
  }
  return Boolean(status);
}

// Per-instance UPS data lookup -- reads the per-app data the
// generic dispatcher fetched from the `host_snmp_samples` table
// (via `logic/apps/apc.py:fetch_data`), NOT the live host row.
// The card therefore renders from the DB sample and never
// triggers a per-card SNMP round-trip on the hot path (the
// operator's explicit requirement). Returns null when the
// fetch is idle / pending / errored OR when the sample carries
// no UPS field (a plain SNMP host pinned to the APC template by
// mistake) so the panel gate hides cleanly.
//
// No module-scope memo is needed here: `this.appsAppData(inst)`
// is itself cache-backed (per-(host_id, service_idx) in
// `_appsDataCache`) and only READS from it during render — the
// async `loadAppData` fetch it kicks on a cache-miss resolves
// LATER (off the render path), so there's no mid-render reactive
// write to loop on. Same contract Speedtest's extras partial
// already relies on.
function hostUpsData(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  // JSHint can't infer the bind so opt into `validthis`.
  /* jshint validthis: true */
  if (!inst || !this.appsAppData) {
    return null;
  }
  // appsAppData returns null while idle / pending / errored
  // (sentinels filtered), else the fetch_data payload.
  const d = this.appsAppData(inst);
  if (!d || !d.available) {
    return null;
  }
  const batt = d.battery_percent;
  const load = d.load_percent;
  const rt = d.battery_runtime_s;
  const temp = d.battery_temp_c;
  // Prefer output-status (On Line / On Battery) since it answers
  // "is the UPS doing its job right now" better than the binary
  // battery-status (Normal / Low); fall back to battery_status.
  const status = String(d.ups_status || d.battery_status || '').trim();
  if (!_hasAnyUpsData(batt, load, rt, temp, status)) {
    return null;
  }
  return {
    battery_percent: (batt == null) ? null : Number(batt),
    load_percent: (load == null) ? null : Number(load),
    runtime_s: (rt == null) ? null : Number(rt),
    battery_temp_c: (temp == null) ? null : Number(temp),
    status: status,
  };
}

// Format a runtime in seconds as "Nh Mm" / "Mm Ss" / "Ss" --
// operator-readable mins/sec. APC's `upsAdvBatteryRunTimeRemaining`
// is TimeTicks (centiseconds), already converted to seconds at
// the extractor level. Negative / null -> em-dash so the panel
// cell shows a sensible placeholder.
function upsRuntimeLabel(s) {
  if (s == null) {
    return '—';
  }
  const total = Math.max(0, Math.round(Number(s) || 0));
  if (total >= 3600) {
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
  if (total >= 60) {
    const m = Math.floor(total / 60);
    const sec = total % 60;
    return sec > 0 ? `${m}m ${sec}s` : `${m}m`;
  }
  return `${total}s`;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. APC
// gets a 2-column span so the 5-stat panel doesn't squeeze the
// per-instance host list.
export const extender = {
  slugs: ['apc'],
  requiresApiKey: false,
  cardSpan(app) {
    return isApcApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the
// merge in `static/js/apps/_registry.js`. Names are prefixed
// `apc*` so they don't collide with other per-app modules'
// helpers.
export const helpers = {
  apcIsApp: isApcApp,
  apcHostUpsData: hostUpsData,
  apcUpsRuntimeLabel: upsRuntimeLabel,
};
