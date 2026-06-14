// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- GitSync Connector (Forgejo -> GitHub repo mirror / sync).
//
// Encapsulates every GitSync-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   GitSync chips render a stat panel (pairs / enabled / paused / issue +
//   commit + release mappings / synced refs / alerts + version + last sync)
//   sourced from the GitSync Connector REST API (`GET /api/v1/metrics`) via
//   `logic/apps/gitsync.py:fetch_data`. The card reads the per-app data the
//   generic dispatcher fetched, so it never triggers a per-card round-trip on
//   the hot path. `gitsyncData(inst)` reads it via the cache-backed
//   `appsAppData(inst)` helper (same path Grafana / Forgejo use).
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the GitSync catalog template (matched via slug; falls back
// to a substring check on `app.name` so an operator-edited chip that dropped the
// catalog link but kept the brand still resolves).
function isGitsyncApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'gitsync') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('gitsync') !== -1);
}

// Per-instance GitSync data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/gitsync.py:fetch_data`). Returns null
// while idle / pending / errored OR when the payload isn't available, so the
// panel gate hides cleanly.
function gitsyncData(inst) {
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
function gitsyncCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Total unacknowledged alerts (error + warn + info) for the alerts footnote
// badge; 0 when none / missing.
function gitsyncAlertTotal(d) {
  if (!d) {
    return 0;
  }
  return (Number(d.alerts_error) || 0) + (Number(d.alerts_warn) || 0) +
    (Number(d.alerts_info) || 0);
}

// Humanise a duration in seconds -> 'Ns' / 'Nm' / 'Nh' / 'Nd' (the per-pair
// last-sync age + longest-run labels). '' for null / non-finite.
function gitsyncAge(s) {
  if (s == null) {
    return '';
  }
  const n = Math.max(0, Math.round(Number(s) || 0));
  if (!isFinite(n)) {
    return '';
  }
  if (n < 60) {
    return n + 's';
  }
  if (n < 3600) {
    return Math.floor(n / 60) + 'm';
  }
  if (n < 86400) {
    return Math.floor(n / 3600) + 'h';
  }
  return Math.floor(n / 86400) + 'd';
}

// Mirror-trend block `{days, samples, latest_mappings, week_throughput,
// peak_alerts, series_mappings, series_alerts}` from the shared lifespan
// gitsync_sampler, or null while idle / no samples yet. Drives the card's
// mappings-growth + alert-trend sparklines.
function gitsyncTrend(inst) {
  /* jshint validthis: true */
  const d = (this.gitsyncData ? this.gitsyncData(inst) : null);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:points` per numeric series array (avoids re-render flicker on
// every Alpine flush — the canonical SVG-builder memo).
const _gitsyncTrendMemo = new WeakMap();

// SVG polyline points for a trend series over a 0..100 x 0..24 viewBox,
// auto-scaled to the series' own max (min pinned at 0). '' when < 2 points.
// Memoised on the array ref.
function gitsyncTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_gitsyncTrendMemo.has(arr)) {
    return _gitsyncTrendMemo.get(arr);
  }
  const W = 100, H = 24, n = arr.length;
  let max = 1;
  for (let i = 0; i < n; i++) {
    const v = Number(arr[i]) || 0;
    if (v > max) {
      max = v;
    }
  }
  const parts = [];
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * W;
    const y = H - ((Number(arr[i]) || 0) / max) * H;
    parts.push((Math.round(x * 100) / 100) + ',' + (Math.round(y * 100) / 100));
  }
  const pts = parts.join(' ');
  _gitsyncTrendMemo.set(arr, pts);
  return pts;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. GitSync gets a
// 2-column span so the stat panel doesn't squeeze the per-instance host list,
// and a vertical telemetry-card layout like Grafana / Forgejo.
export const extender = {
  slugs: ['gitsync'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isGitsyncApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `gitsync*` so they don't
// collide with other per-app modules' helpers.
export const helpers = {
  gitsyncIsApp: isGitsyncApp,
  gitsyncData: gitsyncData,
  gitsyncCount: gitsyncCount,
  gitsyncAlertTotal: gitsyncAlertTotal,
  gitsyncAge: gitsyncAge,
  gitsyncTrend: gitsyncTrend,
  gitsyncTrendPath: gitsyncTrendPath,
};
