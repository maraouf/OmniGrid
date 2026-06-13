// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,JSUnresolvedVariable,JSUnresolvedReference
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
    // Power-quality scalars (PowerNet-MIB; null/'' when not a UPS or the
    // OID didn't answer). battery_replace is a 0/1 flag (1 = replace).
    input_voltage: (d.input_voltage == null) ? null : Number(d.input_voltage),
    output_voltage: (d.output_voltage == null) ? null : Number(d.output_voltage),
    input_freq_hz: (d.input_freq_hz == null) ? null : Number(d.input_freq_hz),
    last_transfer: String(d.last_transfer || '').trim(),
    battery_replace: (d.battery_replace == null) ? null : Number(d.battery_replace),
    self_test: String(d.self_test || '').trim(),
  };
}

// True when the UPS sample carries ANY power-quality scalar — gates the
// power-quality row so it hides cleanly on UPSes / firmwares that don't
// expose the upsAdvInput*/Output* OIDs.
function apcHasPowerQuality(u) {
  if (!u) {
    return false;
  }
  if (u.input_voltage != null) {
    return true;
  }
  if (u.output_voltage != null) {
    return true;
  }
  if (u.input_freq_hz != null) {
    return true;
  }
  if (u.battery_replace != null) {
    return true;
  }
  return Boolean(u.last_transfer) || Boolean(u.self_test);
}

// Humanise a kebab-case enum token ("high-line-voltage" -> "High line
// voltage"). Shared fallback for the i18n-first label helpers below.
function _apcHumanise(s) {
  const t = String(s || '').replace(/-/g, ' ').trim();
  if (!t) {
    return '';
  }
  return t.charAt(0).toUpperCase() + t.slice(1);
}

// Last-transfer-to-battery cause label — i18n key first, humanised
// fallback (per the label-helper i18n rule). '' for empty.
function apcTransferLabel(s) {
  /* jshint validthis: true */
  const raw = String(s || '').trim();
  if (!raw) {
    return '';
  }
  const key = 'apps.apc.transfer_' + raw.replace(/-/g, '_');
  const tr = this.t ? this.t(key) : key;
  return (tr && tr !== key) ? tr : _apcHumanise(raw);
}

// Self-test result label — i18n key first, humanised fallback. '' for empty.
function apcSelfTestLabel(s) {
  /* jshint validthis: true */
  const raw = String(s || '').trim();
  if (!raw) {
    return '';
  }
  const key = 'apps.apc.selftest_' + raw.replace(/-/g, '_');
  const tr = this.t ? this.t(key) : key;
  return (tr && tr !== key) ? tr : _apcHumanise(raw);
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

// The battery/load/runtime history block `{days, samples, battery_series,
// load_series, runtime_series, battery_current, load_current,
// runtime_current_min, runtime_low_min}` from the windowed host_snmp_samples
// read, or null. Drives the card's trend sparklines.
function apcHistory(inst) {
  /* jshint validthis: true */
  if (!inst || !this.appsAppData) {
    return null;
  }
  const d = this.appsAppData(inst);
  return (d && d.history && typeof d.history === 'object') ? d.history : null;
}

// Memo: stable `:points` string per series array reference (the canonical
// SVG-builder memo — avoids re-render flicker on every Alpine flush).
const _apcSparkMemo = new WeakMap();

// SVG polyline points for one APC trend series (`'battery'` | `'load'` |
// `'runtime'`) over a 0..100 × 0..24 viewBox. '' when < 2 points. Each series
// is scaled to its OWN max so the shape reads regardless of unit (% vs minutes).
function apcSparkPoints(inst, key) {
  /* jshint validthis: true */
  const h = apcHistory.call(this, inst);
  const series = (h && Array.isArray(h[key + '_series'])) ? h[key + '_series'] : null;
  if (!series || series.length < 2) {
    return '';
  }
  if (_apcSparkMemo.has(series)) {
    return _apcSparkMemo.get(series);
  }
  const W = 100, H = 24, n = series.length;
  let max = 1;
  for (let i = 0; i < n; i++) {
    const v = Number(series[i]) || 0;
    if (v > max) {
      max = v;
    }
  }
  const parts = [];
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * W;
    const y = H - ((Number(series[i]) || 0) / max) * H;
    parts.push((Math.round(x * 100) / 100) + ',' + (Math.round(y * 100) / 100));
  }
  const pts = parts.join(' ');
  _apcSparkMemo.set(series, pts);
  return pts;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. APC
// gets a 2-column span so the 5-stat panel doesn't squeeze the
// per-instance host list.
export const extender = {
  slugs: ['apc'],
  requiresApiKey: false,
  // Render the card header as a widget-style accent eyebrow (dot +
  // uppercase + status-accent colour) instead of the plain bold app
  // name — APC is a telemetry card, so a widget-tile-style title reads
  // as cohesive with the weather / moon / pubip widget tiles it sits
  // beside. The generic `appsCardEyebrowTitle(app)` helper walks the
  // registered extenders for this flag.
  eyebrowTitle: true,
  // Vertical card layout (title row on TOP, logo below it + smaller,
  // then the per-host ports + the UPS extras filling the rest) instead
  // of the generic logo-left / content-right row. Makes far better use
  // of the tile for a telemetry card whose body is a stat panel rather
  // than a long instance list. The generic `appsCardVerticalLayout(app)`
  // helper walks the registered extenders for this flag; CSS does the
  // reflow via `.apps-card--vertical` (display:contents on the content
  // wrapper hoists header + body so the article grid can stack them).
  verticalLayout: true,
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
  apcHistory: apcHistory,
  apcSparkPoints: apcSparkPoints,
  apcHasPowerQuality: apcHasPowerQuality,
  apcTransferLabel: apcTransferLabel,
  apcSelfTestLabel: apcSelfTestLabel,
};
