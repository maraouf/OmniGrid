// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Jellyfin (open-source media server).
//
// Encapsulates every Jellyfin-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   Jellyfin chips render a 4-stat panel (movies / series / songs / now
//   playing) sourced from the Jellyfin REST API (Items/Counts + Sessions +
//   System/Info) via `logic/apps/jellyfin.py:fetch_data`. The card reads the
//   per-app data the generic dispatcher fetched, so it never triggers a
//   per-card round-trip on the hot path. `jellyfinData(inst)` reads it via the
//   cache-backed `appsAppData(inst)` helper (same path Plex / Bazarr use).
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Jellyfin catalog template (matched via slug; falls
// back to a substring check on `app.name` so an operator-edited chip that
// dropped the catalog link but kept the brand still resolves).
function isJellyfinApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'jellyfin') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('jellyfin') !== -1);
}

// Per-instance Jellyfin data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/jellyfin.py:fetch_data`). Returns null
// while idle / pending / errored OR when the payload isn't available, so the
// panel gate hides cleanly.
function jellyfinData(inst) {
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
function jellyfinCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Format a bits/second figure as a human streaming bandwidth (kbps / Mbps /
// Gbps). '—' for zero / missing.
function jellyfinBandwidth(bps) {
  const n = Number(bps);
  if (bps == null || !isFinite(n) || n <= 0) {
    return '—';
  }
  if (n >= 1000000000) {
    return (n / 1000000000).toLocaleString(undefined, {maximumFractionDigits: 1}) + ' Gbps';
  }
  if (n >= 1000000) {
    return (n / 1000000).toLocaleString(undefined, {maximumFractionDigits: 1}) + ' Mbps';
  }
  if (n >= 1000) {
    return (n / 1000).toLocaleString(undefined, {maximumFractionDigits: 0}) + ' kbps';
  }
  return n.toLocaleString() + ' bps';
}

// Streaming trend block `{days, samples, latest_streams, peak_streams,
// peak_streams_today, peak_transcodes, series_streams, series_transcodes}` from
// the shared lifespan emby_sampler (one sampler serves both brands), or null
// while idle / no samples yet. Drives the card's 'peak streams today' stat + the
// daily peak-streams sparkline.
function jellyfinTrend(inst) {
  /* jshint validthis: true */
  const d = (this.jellyfinData ? this.jellyfinData(inst) : null);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:points` per numeric series array (avoids re-render flicker on
// every Alpine flush — the canonical SVG-builder memo).
const _jellyfinTrendMemo = new WeakMap();

// SVG polyline points for a peak-streams sparkline over a 0..100 × 0..24 viewBox,
// auto-scaled to the series' own max (min pinned at 0). '' when < 2 points.
// Memoised on the array ref.
function jellyfinTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_jellyfinTrendMemo.has(arr)) {
    return _jellyfinTrendMemo.get(arr);
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
  _jellyfinTrendMemo.set(arr, pts);
  return pts;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Jellyfin gets a
// 2-column span so the 4-stat panel doesn't squeeze the per-instance host
// list, and a vertical telemetry-card layout like Plex / Bazarr / APC.
export const extender = {
  slugs: ['jellyfin'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isJellyfinApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `jellyfin*` so they don't
// collide with other per-app modules' helpers.
export const helpers = {
  jellyfinIsApp: isJellyfinApp,
  jellyfinData: jellyfinData,
  jellyfinCount: jellyfinCount,
  jellyfinBandwidth: jellyfinBandwidth,
  jellyfinTrend: jellyfinTrend,
  jellyfinTrendPath: jellyfinTrendPath,
};
