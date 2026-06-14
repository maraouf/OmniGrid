// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Grafana (metrics + observability dashboard).
//
// Encapsulates every Grafana-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   Grafana chips render a stat panel (dashboards / folders / datasources /
//   org / users+orgs) sourced from the Grafana REST API (/api/org +
//   /api/search + /api/folders + /api/datasources + /api/admin/stats) via
//   `logic/apps/grafana.py:fetch_data`. The card reads the per-app data the
//   generic dispatcher fetched, so it never triggers a per-card round-trip on
//   the hot path. `grafanaData(inst)` reads it via the cache-backed
//   `appsAppData(inst)` helper (same path Plex / Forgejo use).
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Grafana catalog template (matched via slug; falls back
// to a substring check on `app.name` so an operator-edited chip that dropped the
// catalog link but kept the brand still resolves).
function isGrafanaApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'grafana') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('grafana') !== -1);
}

// Per-instance Grafana data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/grafana.py:fetch_data`). Returns null
// while idle / pending / errored OR when the payload isn't available, so the
// panel gate hides cleanly.
function grafanaData(inst) {
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
function grafanaCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Comma-joined names from a string array (the unhealthy-datasource / firing-
// alert name lists), truncated to the first 6 with a '…' overflow marker.
// '' when empty / not an array — drives the chip :title tooltips.
function grafanaNames(arr) {
  if (!Array.isArray(arr) || !arr.length) {
    return '';
  }
  const names = arr.filter((n) => typeof n === 'string' && n.trim()).map((n) => n.trim());
  if (!names.length) {
    return '';
  }
  return names.slice(0, 6).join(', ') + (names.length > 6 ? ', …' : '');
}

// Meta-monitor trend block `{days, samples, latest_firing, peak_firing,
// latest_dashboards, series_firing, series_dashboards}` from the shared lifespan
// grafana_sampler, or null while idle / no samples yet. Drives the card's
// firing-alert ('monitor of the monitor') sparkline.
function grafanaTrend(inst) {
  /* jshint validthis: true */
  const d = (this.grafanaData ? this.grafanaData(inst) : null);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:points` per numeric series array (avoids re-render flicker on
// every Alpine flush — the canonical SVG-builder memo).
const _grafanaTrendMemo = new WeakMap();

// SVG polyline points for a trend series over a 0..100 x 0..24 viewBox,
// auto-scaled to the series' own max (min pinned at 0). '' when < 2 points.
// Memoised on the array ref.
function grafanaTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_grafanaTrendMemo.has(arr)) {
    return _grafanaTrendMemo.get(arr);
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
  _grafanaTrendMemo.set(arr, pts);
  return pts;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Grafana gets a
// 2-column span so the stat panel doesn't squeeze the per-instance host list,
// and a vertical telemetry-card layout like Plex / Jellyfin / Forgejo.
export const extender = {
  slugs: ['grafana'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isGrafanaApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `grafana*` so they don't
// collide with other per-app modules' helpers.
export const helpers = {
  grafanaIsApp: isGrafanaApp,
  grafanaData: grafanaData,
  grafanaCount: grafanaCount,
  grafanaNames: grafanaNames,
  grafanaTrend: grafanaTrend,
  grafanaTrendPath: grafanaTrendPath,
};
