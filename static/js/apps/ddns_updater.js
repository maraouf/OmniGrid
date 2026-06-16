// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection JSUnresolvedVariable,JSUnresolvedReference
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

// The per-record detail list ([] when payload absent). Each entry carries
// {domain, provider, ip_version, status, status_raw, last_updated, current_ip,
// previous_ips} — surfaced from the parsed web-UI table.
function ddnsRecords(inst) {
  /* jshint validthis: true */
  const d = ddnsData.call(this, inst);
  return (d && Array.isArray(d.records)) ? d.records : [];
}

// The history block {days, samples, current_ip, current_ip_since,
// ip_change_count, ip_changes, fail_series, fail_peak} from the lifespan
// ddns sampler, or null while idle / no samples yet.
function ddnsHistory(inst) {
  /* jshint validthis: true */
  const d = ddnsData.call(this, inst);
  return (d && d.history && typeof d.history === 'object') ? d.history : null;
}

// Public-IP-change timeline, NEWEST-first ({ts, ip}) for the card list. []
// when no history / no changes recorded yet.
function ddnsIpChanges(inst) {
  /* jshint validthis: true */
  const h = ddnsHistory.call(this, inst);
  const arr = (h && Array.isArray(h.ip_changes)) ? h.ip_changes : [];
  return arr.slice().reverse();
}

// The provider breakdown ([{provider, count}], busiest-first) — [] when absent.
function ddnsProviders(inst) {
  /* jshint validthis: true */
  const d = ddnsData.call(this, inst);
  return (d && Array.isArray(d.provider_breakdown)) ? d.provider_breakdown : [];
}

// "IP stable for N days" — whole days since the current public IP was first
// observed (history.current_ip_since, epoch seconds). null when unknown / no
// sampler history yet (so the card line hides).
function ddnsIpStableDays(inst) {
  /* jshint validthis: true */
  const h = ddnsHistory.call(this, inst);
  const since = h ? Number(h.current_ip_since) : 0;
  if (!since || !isFinite(since) || since <= 0) {
    return null;
  }
  const days = Math.floor((Date.now() / 1000 - since) / 86400);
  return days >= 0 ? days : null;
}

// Memo: stable `:points` string per series array reference (the canonical
// SVG-builder memo — avoids re-render flicker on every flush). Shared by the
// fail + up sparklines (keyed on each array ref, so no collision).
const _ddnsSparkMemo = new WeakMap();

// SVG polyline points for a daily-count series over a 0..100 × 0..24 viewBox,
// auto-scaled to its own max (min pinned at 0). '' when < 2 points.
function _ddnsSpark(series) {
  if (!Array.isArray(series) || series.length < 2) {
    return '';
  }
  if (_ddnsSparkMemo.has(series)) {
    return _ddnsSparkMemo.get(series);
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
  _ddnsSparkMemo.set(series, pts);
  return pts;
}

// Failing-count sparkline points. '' when < 2 points.
function ddnsFailSparkPoints(inst) {
  /* jshint validthis: true */
  const h = ddnsHistory.call(this, inst);
  return _ddnsSpark((h && Array.isArray(h.fail_series)) ? h.fail_series : null);
}

// Up-to-date-count sparkline points (the up-vs-fail trend companion line).
function ddnsUpSparkPoints(inst) {
  /* jshint validthis: true */
  const h = ddnsHistory.call(this, inst);
  return _ddnsSpark((h && Array.isArray(h.up_series)) ? h.up_series : null);
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
  ddnsRecords: ddnsRecords,
  ddnsHistory: ddnsHistory,
  ddnsIpChanges: ddnsIpChanges,
  ddnsProviders: ddnsProviders,
  ddnsIpStableDays: ddnsIpStableDays,
  ddnsFailSparkPoints: ddnsFailSparkPoints,
  ddnsUpSparkPoints: ddnsUpSparkPoints,
};
