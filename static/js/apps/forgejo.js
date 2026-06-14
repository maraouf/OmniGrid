// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Forgejo (self-hosted Git service).
//
// Encapsulates every Forgejo-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   Forgejo chips render a 4-stat panel (repos / open PRs / open issues /
//   notifications) sourced from the Gitea-compatible REST API (user/repos +
//   issues/search + notifications) via `logic/apps/forgejo.py:fetch_data`. The
//   card reads the per-app data the generic dispatcher fetched, so it never
//   triggers a per-card round-trip on the hot path. `forgejoData(inst)` reads
//   it via the cache-backed `appsAppData(inst)` helper (same path Plex uses).
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Forgejo catalog template (matched via slug; falls
// back to a substring check on `app.name` so an operator-edited chip that
// dropped the catalog link but kept the brand still resolves).
function isForgejoApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'forgejo') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('forgejo') !== -1);
}

// Per-instance Forgejo data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/forgejo.py:fetch_data`). Returns null
// while idle / pending / errored OR when the payload isn't available, so the
// panel gate hides cleanly.
function forgejoData(inst) {
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
function forgejoCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Open-backlog trend block `{days, samples, latest_backlog, peak_backlog,
// week_change, series_backlog}` from the shared lifespan forgejo_sampler, or
// null while idle / no samples yet. Drives the card's review-queue burn-down
// sparkline + the week-change stat.
function forgejoTrend(inst) {
  /* jshint validthis: true */
  const d = (this.forgejoData ? this.forgejoData(inst) : null);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:points` per numeric series array (avoids re-render flicker on
// every Alpine flush — the canonical SVG-builder memo).
const _forgejoTrendMemo = new WeakMap();

// SVG polyline points for the open-backlog sparkline over a 0..100 × 0..24
// viewBox, auto-scaled to the series' own max (min pinned at 0). '' when < 2
// points. Memoised on the array ref.
function forgejoTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_forgejoTrendMemo.has(arr)) {
    return _forgejoTrendMemo.get(arr);
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
  _forgejoTrendMemo.set(arr, pts);
  return pts;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Forgejo gets a
// 2-column span so the 4-stat panel doesn't squeeze the per-instance host
// list, and a vertical telemetry-card layout like Plex / Jellyfin / Bazarr.
export const extender = {
  slugs: ['forgejo'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isForgejoApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `forgejo*` so they don't
// collide with other per-app modules' helpers.
export const helpers = {
  forgejoIsApp: isForgejoApp,
  forgejoData: forgejoData,
  forgejoCount: forgejoCount,
  forgejoTrend: forgejoTrend,
  forgejoTrendPath: forgejoTrendPath,
};
