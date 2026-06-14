// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Nginx Proxy Manager (NPM).
//
// Encapsulates every NPM-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   NPM authenticates with an email + password (the admin login) exchanged
//   for a short-lived bearer token; `logic/apps/nginx_proxy_manager.py:
//   fetch_data` re-auths per fetch and aggregates proxy-host / certificate /
//   redirection / stream / dead-host counts, read through the cache-backed
//   `appsAppData(inst)` (no per-card round-trip on the hot path).
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the NPM catalog template (matched via slug; falls back to
// a substring check on `app.name`).
function isNpmApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'nginx-proxy-manager' || slug === 'npm') {
    return true;
  }
  const name = String(app.name || '').toLowerCase();
  return (name.indexOf('nginx proxy manager') !== -1 || name.indexOf('proxy manager') !== -1);
}

// Per-instance NPM data lookup -- reads the per-app data the generic dispatcher
// fetched (via `logic/apps/nginx_proxy_manager.py:fetch_data`). Returns null
// while idle / pending / errored OR when the payload isn't available.
function npmData(inst) {
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
function npmCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// "on / total" proxy-host fraction string for the hero stat.
function npmProxyFraction(d) {
  if (!d) {
    return '—';
  }
  return npmCount(d.proxy_enabled) + ' / ' + npmCount(d.proxy_hosts);
}

// Soonest cert's days-left (number) or null when no certs / unknown — drives
// the card "Next cert" stat.
function npmCertMinDays(inst) {
  /* jshint validthis: true */
  const d = npmData.call(this, inst);
  if (!d || d.cert_min_days == null) {
    return null;
  }
  const n = Number(d.cert_min_days);
  return isFinite(n) ? Math.round(n) : null;
}

// Display label for the soonest-cert stat: "9d", "expired", or "—".
function npmCertMinLabel(inst) {
  /* jshint validthis: true */
  const days = npmCertMinDays.call(this, inst);
  if (days == null) {
    return '—';
  }
  if (days < 0) {
    return (this.t && this.t('apps.npm.expired')) || 'expired';
  }
  return days + 'd';
}

// State bucket for colouring a days-left value: 'expired' (<0), 'soon' (<=30),
// or 'ok'.
function npmCertState(days) {
  const n = Number(days);
  if (!isFinite(n)) {
    return 'ok';
  }
  if (n < 0) {
    return 'expired';
  }
  if (n <= 30) {
    return 'soon';
  }
  return 'ok';
}

// CSS colour token for a cert days-left value (danger / warning / success).
function npmCertColor(days) {
  const s = npmCertState(days);
  if (s === 'expired') {
    return 'var(--danger)';
  }
  if (s === 'soon') {
    return 'var(--warning)';
  }
  return 'var(--success)';
}

// Plain-HTTP proxy-host count (enabled hosts with no SSL cert).
function npmPlainHttp(inst) {
  /* jshint validthis: true */
  const d = npmData.call(this, inst);
  return d ? (Number(d.proxy_plain_http) || 0) : 0;
}

// The soonest-expiry cert list for the drawer (array of
// {id, label, days, provider, renewable}).
function npmCertList(inst) {
  /* jshint validthis: true */
  const d = npmData.call(this, inst);
  return (d && Array.isArray(d.certs_soonest)) ? d.certs_soonest : [];
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. requiresApiKey true
// (email + password editor) and a wide card (multi-stat grid).
export const extender = {
  slugs: ['nginx-proxy-manager', 'npm'],
  requiresApiKey: true,
  cardSpan(app) {
    return isNpmApp(app) ? 2 : 1;
  },
};

// Config-drift trend block `{days, samples, latest_hosts, hosts_week_change,
// peak_plain_http, series_hosts, series_plain_http}` from the shared lifespan
// npm_sampler, or null while idle / no samples yet. Drives the card's
// proxy-host-growth sparkline.
function npmTrend(inst) {
  /* jshint validthis: true */
  const d = (this.npmData ? this.npmData(inst) : null);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:points` per numeric series array (avoids re-render flicker on
// every Alpine flush — the canonical SVG-builder memo).
const _npmTrendMemo = new WeakMap();

// SVG polyline points for a trend series over a 0..100 x 0..24 viewBox,
// auto-scaled to the series' own max (min pinned at 0). '' when < 2 points.
// Memoised on the array ref.
function npmTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_npmTrendMemo.has(arr)) {
    return _npmTrendMemo.get(arr);
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
  _npmTrendMemo.set(arr, pts);
  return pts;
}

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `npm*`.
export const helpers = {
  npmIsApp: isNpmApp,
  npmData: npmData,
  npmCount: npmCount,
  npmProxyFraction: npmProxyFraction,
  npmCertMinDays: npmCertMinDays,
  npmCertMinLabel: npmCertMinLabel,
  npmCertState: npmCertState,
  npmCertColor: npmCertColor,
  npmPlainHttp: npmPlainHttp,
  npmCertList: npmCertList,
  npmTrend: npmTrend,
  npmTrendPath: npmTrendPath,
};
