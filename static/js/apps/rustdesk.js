// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- RustDesk Server (Pro).
//
// Encapsulates every RustDesk-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   RustDesk Server Pro authenticates the console username + password (login →
//   Bearer token, re-auth per fetch); `logic/apps/rustdesk.py:fetch_data`
//   aggregates peers / online / users / version, read through the cache-backed
//   `appsAppData(inst)`. The OSS server has no API — the card degrades to a
//   clear "needs RustDesk Server Pro" error.
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the RustDesk catalog template (matched via slug; falls
// back to a substring check on `app.name`).
function isRustdeskApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug.indexOf('rustdesk') !== -1) {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('rustdesk') !== -1);
}

// Per-instance RustDesk data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/rustdesk.py:fetch_data`). Returns null
// while idle / pending / errored OR when the payload isn't available.
function rustdeskData(inst) {
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
function rustdeskCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// "online / total" device fraction string for the hero stat.
function rustdeskDeviceFraction(d) {
  if (!d) {
    return '—';
  }
  return rustdeskCount(d.devices_online) + ' / ' + rustdeskCount(d.devices);
}

// OS-family display labels (keyword key -> human label). Mirrors the backend
// `_OS_LABELS`; i18n-first via `apps.rustdesk.os_<key>` with an English
// fallback (the keys are stable family slugs, never raw upstream strings).
const _RD_OS_LABELS = {
  windows: 'Windows', macos: 'macOS', android: 'Android',
  ios: 'iOS', linux: 'Linux', other: 'Other',
};

// OS-breakdown rows for the fleet-composition cell: [{key, label, count}]
// sorted by count desc. [] when the payload carries no breakdown.
function rustdeskOsRows(inst) {
  /* jshint validthis: true */
  const d = rustdeskData.call(this, inst);
  const bd = (d && d.os_breakdown && typeof d.os_breakdown === 'object') ? d.os_breakdown : null;
  if (!bd) {
    return [];
  }
  const rows = [];
  for (const k of Object.keys(bd)) {
    const n = Number(bd[k]) || 0;
    if (n > 0) {
      const lbl = (this && this.t) ? this.t('apps.rustdesk.os_' + k, _RD_OS_LABELS[k] || k) : (_RD_OS_LABELS[k] || k);
      rows.push({key: k, label: lbl, count: n});
    }
  }
  rows.sort((a, b) => b.count - a.count);
  return rows;
}

// Compact "Windows 4 · macOS 2 · Linux 1" OS-breakdown summary string ('' when
// nothing to show).
function rustdeskOsSummary(inst) {
  /* jshint validthis: true */
  const rows = rustdeskOsRows.call(this, inst);
  if (!rows.length) {
    return '';
  }
  return rows.map(r => r.label + ' ' + r.count).join(' · ');
}

// Online-peers usage trend (from the lifespan rustdesk_sampler — the Pro API
// exposes only current state). Returns the usage block
// `{series, peak, avg, active_days, samples, current, days}` or null.
function rustdeskUsage(inst) {
  /* jshint validthis: true */
  const d = rustdeskData.call(this, inst);
  return (d && d.usage && typeof d.usage === 'object') ? d.usage : null;
}

// Memo: stable `:points` string per series array reference (avoids re-render
// flicker on every Alpine flush -- the canonical SVG-builder memo pattern).
const _rdSparkMemo = new WeakMap();

// SVG polyline points for the online-peers sparkline over a 0..100 × 0..24
// viewBox. '' when there's < 2 points (nothing to draw yet).
function rustdeskSparkPoints(inst) {
  /* jshint validthis: true */
  const u = rustdeskUsage.call(this, inst);
  const series = (u && Array.isArray(u.series)) ? u.series : null;
  if (!series || series.length < 2) {
    return '';
  }
  if (_rdSparkMemo.has(series)) {
    return _rdSparkMemo.get(series);
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
  _rdSparkMemo.set(series, pts);
  return pts;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. requiresApiKey true
// (username + password editor) and a wide card (multi-stat grid).
export const extender = {
  slugs: ['rustdesk', 'rustdesk-server', 'rustdesk-server-pro'],
  requiresApiKey: true,
  cardSpan(app) {
    return isRustdeskApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `rustdesk*`.
export const helpers = {
  rustdeskIsApp: isRustdeskApp,
  rustdeskData: rustdeskData,
  rustdeskCount: rustdeskCount,
  rustdeskDeviceFraction: rustdeskDeviceFraction,
  rustdeskOsRows: rustdeskOsRows,
  rustdeskOsSummary: rustdeskOsSummary,
  rustdeskUsage: rustdeskUsage,
  rustdeskSparkPoints: rustdeskSparkPoints,
};
