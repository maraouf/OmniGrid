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
//   the curated host row's SNMP-derived fields:
//     host.host_battery_percent       (0..100)
//     host.host_load_percent          (0..100)
//     host.host_battery_runtime_s     (seconds)
//     host.host_battery_temp_c        (Celsius)
//     host.host_ups_status            ('On Line' / 'On Battery' / ...)
//     host.host_battery_status        ('Normal' / 'Low' / ...)
//   No backend module needs to fetch anything -- the data is
//   already on the merged host row by the time the Apps card
//   renders. The companion `logic/apps/apc.py` is a SLUGS-only
//   stub for the same reason.
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

// Predicate factory consumed by `Array.find` in `hostUpsData`.
// Closes over the target host_id so the `find` callback stays
// O(1) per element. Module scope keeps PyCharm's nested-function
// inspection quiet.
function _hostMatcher(targetId) {
  return function _match(h) {
    return Boolean(h) && h.id === targetId;
  };
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

// Per-instance UPS data lookup -- finds the host the chip is
// pinned to via `inst.host_id` against the SPA's `hosts` array,
// pulls the six SNMP-populated UPS fields, returns null when
// none are present (so the panel gate hides cleanly for
// non-SNMP / non-UPS hosts that happen to be pinned to the APC
// template by mistake).
//
// Cached per-(host_id) via a flush-memo so the O(N) host-array
// walk runs once per render flush instead of N times per chip
// instance. Memo clears on the next microtask (matches the
// `filteredHosts` / `providerStates` memo pattern documented in
// the project conventions).
//
// The memo lives on the Alpine component (`this._appsUpsCache`)
// rather than module scope so a hot-reload / second page-load
// initialises cleanly. Same shape as the speedtest module's
// `_appsDataCache`.
function hostUpsData(inst) {
  // `this` is the Alpine component (the function gets merged
  // in via `appsHelpers`); `_appsUpsCache` lives on the
  // component for hot-reload safety. JSHint can't infer the
  // bind so opt into `validthis` at function scope.
  /* jshint validthis: true */
  if (!inst || !inst.host_id) {
    return null;
  }
  if (!this._appsUpsCache) {
    this._appsUpsCache = Object.create(null);
    if (!this._appsUpsCachePending) {
      this._appsUpsCachePending = true;
      queueMicrotask(() => {
        this._appsUpsCache = null;
        this._appsUpsCachePending = false;
      });
    }
  }
  if (inst.host_id in this._appsUpsCache) {
    return this._appsUpsCache[inst.host_id];
  }
  // `Array.find` replaces the for/break lookup so PyCharm's
  // BreakStatementJS inspection stays quiet AND the intent
  // reads more clearly ("find this host by id").
  const hosts = Array.isArray(this.hosts) ? this.hosts : [];
  const host = hosts.find(_hostMatcher(inst.host_id)) || null;
  if (!host) {
    this._appsUpsCache[inst.host_id] = null;
    return null;
  }
  // Gate: at least ONE UPS field must be populated. Otherwise
  // the host is just an SNMP-probed non-UPS device pinned to
  // the APC template by mistake -- render nothing rather than
  // an empty panel of dashes. The multi-clause null-check is
  // extracted to `_hasAnyUpsData` so PyCharm's
  // OverlyComplexBooleanExpressionJS inspection stays quiet.
  const batt = host.host_battery_percent;
  const load = host.host_load_percent;
  const rt = host.host_battery_runtime_s;
  const temp = host.host_battery_temp_c;
  const status = host.host_ups_status || host.host_battery_status || '';
  if (!_hasAnyUpsData(batt, load, rt, temp, status)) {
    this._appsUpsCache[inst.host_id] = null;
    return null;
  }
  const data = {
    battery_percent: (batt == null) ? null : Number(batt),
    load_percent: (load == null) ? null : Number(load),
    runtime_s: (rt == null) ? null : Number(rt),
    battery_temp_c: (temp == null) ? null : Number(temp),
    // Prefer output-status (On Line / On Battery) since it
    // answers the "is the UPS doing its job right now" question
    // better than the binary battery-status (Normal / Low).
    // Fall back to battery_status if output is empty.
    status: (host.host_ups_status || host.host_battery_status || '').trim(),
  };
  this._appsUpsCache[inst.host_id] = data;
  return data;
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
