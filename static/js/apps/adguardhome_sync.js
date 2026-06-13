// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- AdGuard Home Sync (bakito/adguardhome-sync).
//
// Encapsulates every sync-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   The chip renders a small status panel (sync running? origin OK?
//   replicas in sync) sourced from the sync tool's `GET /api/v1/status`,
//   via `logic/apps/adguardhome_sync.py:fetch_data`. The card reads the
//   per-app data the generic dispatcher fetched, so it never triggers a
//   per-card round-trip on the hot path. `adguardsyncData(inst)` reads
//   it via the cache-backed `appsAppData(inst)` helper (same path
//   Radarr / Sonarr / Bazarr / Seerr use).
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the AdGuard Home Sync catalog template (matched via
// slug; falls back to a substring check on `app.name` so an
// operator-edited chip that dropped the catalog link but kept the brand
// still resolves).
function isAdguardSyncApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'adguardhome-sync' || slug === 'adguard-home-sync' || slug === 'adguardhomesync') {
    return true;
  }
  const nm = String(app.name || '').toLowerCase();
  // Match a de-linked chip by name too: any name containing BOTH 'adguard'
  // AND 'sync' (e.g. "AdGuard Home Sync") is this app, not plain AdGuard —
  // the inverse of isAdguardApp's 'sync' exclusion, so the two never both
  // match the same chip.
  return (nm.indexOf('adguard') !== -1) && (nm.indexOf('sync') !== -1);
}

// Per-instance sync-status lookup -- reads the per-app data the generic
// dispatcher fetched from `GET /api/v1/status`. Returns null while idle /
// pending / errored OR when the payload isn't available, so the panel
// gate hides cleanly.
function adguardsyncData(inst) {
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
function adguardsyncCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// The list of failing replica names ([] when all in sync / payload absent).
function adguardsyncFailed(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  const d = (this.adguardsyncData ? this.adguardsyncData(inst) : null);
  if (!d || !Array.isArray(d.failed_names)) {
    return [];
  }
  return d.failed_names;
}

// Per-replica detail rows ([] when payload absent). Each carries
// {host, status, ok, error, last_sync, protection_enabled}.
function adguardsyncReplicas(inst) {
  /* jshint validthis: true */
  const d = (this.adguardsyncData ? this.adguardsyncData(inst) : null);
  return (d && Array.isArray(d.replicas)) ? d.replicas : [];
}

// Relative "5m" age for a replica's ISO last-sync timestamp ('' when blank /
// unparseable). Uses the component's shared fmtAgo so the format matches the
// rest of the UI; the template wraps it with an "ago" i18n label.
function adguardsyncRelAge(iso) {
  /* jshint validthis: true */
  if (!iso) {
    return '';
  }
  const ms = Date.parse(String(iso));
  if (isNaN(ms)) {
    return '';
  }
  return (typeof this.fmtAgo === 'function') ? this.fmtAgo(ms) : '';
}

// True when every configured replica is in sync (and at least one exists).
function adguardsyncAllOk(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  const d = (this.adguardsyncData ? this.adguardsyncData(inst) : null);
  if (!d) {
    return false;
  }
  const total = Number(d.replicas_total) || 0;
  const ok = Number(d.replicas_ok) || 0;
  return Boolean(d.origin_ok) && total > 0 && ok === total;
}

// Per-instance sync-reliability history (the lifespan sampler's series),
// embedded on the per-app data as `history`. Null when absent / not yet sampled.
function adguardsyncHistory(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  if (!inst || !this.appsAppData) {
    return null;
  }
  const d = this.appsAppData(inst);
  return (d && d.history && typeof d.history === 'object') ? d.history : null;
}

// Memo: stable `:points` string per series array reference (the canonical
// SVG-builder memo — avoids re-render flicker on every Alpine flush).
const _adguardsyncSparkMemo = new WeakMap();

// SVG polyline points for the sync-reliability trend (daily-MIN in-sync %)
// over a 0..100 × 0..24 viewBox. '' when < 2 points. Scaled to a FIXED 0..100
// ceiling so a dip reads as "% of replicas in sync that day" against 100%.
function adguardsyncSyncSpark(inst) {
  /* jshint validthis: true */
  const h = adguardsyncHistory.call(this, inst);
  const series = (h && Array.isArray(h.sync_pct_series)) ? h.sync_pct_series : null;
  if (!series || series.length < 2) {
    return '';
  }
  if (_adguardsyncSparkMemo.has(series)) {
    return _adguardsyncSparkMemo.get(series);
  }
  const W = 100, H = 24, n = series.length;
  const parts = [];
  for (let i = 0; i < n; i++) {
    const pct = Math.max(0, Math.min(100, Number(series[i]) || 0));
    const x = (i / (n - 1)) * W;
    const y = H - (pct / 100) * H;
    parts.push((Math.round(x * 100) / 100) + ',' + (Math.round(y * 100) / 100));
  }
  const pts = parts.join(' ');
  _adguardsyncSparkMemo.set(series, pts);
  return pts;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. requiresApiKey
// is false: the sync API's HTTP Basic auth is OPTIONAL, so the card +
// skills work against an unauthenticated instance (the editor still
// offers username + password for instances that DO require it).
export const extender = {
  slugs: ['adguardhome-sync', 'adguard-home-sync', 'adguardhomesync'],
  requiresApiKey: false,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isAdguardSyncApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `adguardsync*` so
// they don't collide with other per-app modules' helpers.
export const helpers = {
  adguardsyncIsApp: isAdguardSyncApp,
  adguardsyncData: adguardsyncData,
  adguardsyncCount: adguardsyncCount,
  adguardsyncFailed: adguardsyncFailed,
  adguardsyncReplicas: adguardsyncReplicas,
  adguardsyncRelAge: adguardsyncRelAge,
  adguardsyncAllOk: adguardsyncAllOk,
  adguardsyncHistory: adguardsyncHistory,
  adguardsyncSyncSpark: adguardsyncSyncSpark,
};
