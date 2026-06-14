// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- qBittorrent (BitTorrent client with Web UI).
//
// Encapsulates every qBittorrent-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`. Single-instance app -- the operator can pin
// SEVERAL qBittorrent instances and each renders its own card (the AI /
// Telegram target a specific one by host; give each chip a distinct NAME so
// they're easy to tell apart). The card shows live transfer speeds + torrent
// counts by state, sourced from `logic/apps/qbittorrent.py:fetch_data` via the
// cache-backed `appsAppData(inst)`.
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the qBittorrent catalog template (slug match; falls back
// to a substring check on `app.name`).
function isQbittorrentApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'qbittorrent') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('qbittorrent') !== -1);
}

// Per-instance qBittorrent data lookup -- reads the per-app data the generic
// dispatcher fetched from `GET /api/v2/transfer/info` (+ torrents/info) via
// `logic/apps/qbittorrent.py:fetch_data`. Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides cleanly.
function qbittorrentData(inst) {
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
function qbittorrentCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Format a bytes/second rate as a human transfer speed (B/s … TiB/s).
// '0 B/s' for zero / missing.
function qbittorrentSpeed(bytesPerS) {
  const n = Number(bytesPerS);
  if (bytesPerS == null || !isFinite(n) || n <= 0) {
    return '0 B/s';
  }
  const units = ['B/s', 'KiB/s', 'MiB/s', 'GiB/s', 'TiB/s'];
  let val = n;
  let idx = 0;
  while (val >= 1024 && idx < units.length - 1) {
    val /= 1024;
    idx += 1;
  }
  return val.toLocaleString(undefined, {maximumFractionDigits: 1}) + ' ' + units[idx];
}

// Format a byte count as a human size (B … TiB) — for the all-time transfer
// totals + free-disk. '—' for missing / zero.
function qbittorrentBytes(v) {
  const n = Number(v);
  if (v == null || !isFinite(n) || n <= 0) {
    return '—';
  }
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
  let val = n;
  let idx = 0;
  while (val >= 1024 && idx < units.length - 1) {
    val /= 1024;
    idx += 1;
  }
  return val.toLocaleString(undefined, {maximumFractionDigits: 1}) + ' ' + units[idx];
}

// Transfer-speed / free-disk trend block from the lifespan qbittorrent_sampler,
// or null while idle / no samples yet.
function qbittorrentTrend(inst) {
  /* jshint validthis: true */
  const d = (this.qbittorrentData ? this.qbittorrentData(inst) : null);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:d` per numeric series array (avoids re-render flicker on every
// Alpine flush).
const _qbittorrentTrendMemo = new WeakMap();

// SVG polyline points for a sparkline over a 0..200 × 0..32 viewBox, auto-scaled
// to the series' own min/max. '' when < 2 points. Memoised on the array ref.
function qbittorrentTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_qbittorrentTrendMemo.has(arr)) {
    return _qbittorrentTrendMemo.get(arr);
  }
  const W = 200, H = 32, n = arr.length;
  let min = Infinity, max = -Infinity;
  for (let i = 0; i < n; i++) {
    const v = Number(arr[i]) || 0;
    if (v < min) {
      min = v;
    }
    if (v > max) {
      max = v;
    }
  }
  const range = (max - min) || 1;
  const stepX = W / Math.max(1, n - 1);
  let d = '';
  for (let i = 0; i < n; i++) {
    const x = (i * stepX).toFixed(1);
    const y = (H - ((Number(arr[i]) || 0) - min) / range * H).toFixed(1);
    d += (i === 0 ? 'M' : 'L') + x + ',' + y + ' ';
  }
  d = d.trim();
  _qbittorrentTrendMemo.set(arr, d);
  return d;
}

// Human ETA from a qBittorrent `eta` (seconds; 8640000 = the qBit ∞ sentinel,
// also used for a stalled / unknown estimate). '∞' for the sentinel /
// non-positive; otherwise 'Xd Yh' / 'Xh Ym' / 'Ym'.
function qbittorrentEta(seconds) {
  const s = Number(seconds) || 0;
  if (s <= 0 || s >= 8640000) {
    return '∞';
  }
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) {
    return d + 'd ' + h + 'h';
  }
  if (h) {
    return h + 'h ' + m + 'm';
  }
  return m + 'm';
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. qBittorrent gets a
// 2-column span + a vertical telemetry-card layout like the *arr family.
export const extender = {
  slugs: ['qbittorrent'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isQbittorrentApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `qbittorrent*`.
export const helpers = {
  qbittorrentIsApp: isQbittorrentApp,
  qbittorrentData: qbittorrentData,
  qbittorrentCount: qbittorrentCount,
  qbittorrentSpeed: qbittorrentSpeed,
  qbittorrentBytes: qbittorrentBytes,
  qbittorrentTrend: qbittorrentTrend,
  qbittorrentTrendPath: qbittorrentTrendPath,
  qbittorrentEta: qbittorrentEta,
};
