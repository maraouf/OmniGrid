// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- OPNsense (firewall / router).
//
// Encapsulates every OPNsense-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`. Mirrors the Proxmox / UniFi card shape (a
// key+secret-auth stat panel) -- the stats are Gateways / Services / Memory /
// Load / Firewall-states / DHCP, sourced from `logic/apps/opnsense.py:fetch_data`
// (a fan-out of tolerated diagnostics reads) via the cache-backed
// `appsAppData(inst)`.
//
// File-scope IDE directives: see `proxmox.js`'s header for the full rationale.

// True when `app` is the OPNsense catalog template (slug match; falls back to a
// substring check on `app.name`).
function isOpnsenseApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'opnsense') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('opnsense') !== -1);
}

// Per-instance OPNsense data lookup -- reads the per-app data the generic
// dispatcher fetched. Returns null while idle / pending / errored.
function opnsenseData(inst) {
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

// Format an integer count with thousands separators; '—' for missing.
function opnsenseCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// "online/total" (or "running/total") fraction string for a kind
// ('gateways' | 'services').
function opnsenseFraction(inst, kind) {
  /* jshint validthis: true */
  const d = opnsenseData.call(this, inst);
  if (!d) {
    return '—';
  }
  const map = {
    gateways: ['gateways_online', 'gateways_total'],
    services: ['services_running', 'services_total']
  };
  const keys = map[kind] || map.gateways;
  const a = Number(d[keys[0]]) || 0;
  const b = Number(d[keys[1]]) || 0;
  return a.toLocaleString() + '/' + b.toLocaleString();
}

// The per-gateway detail rows [{name, status, delay, loss, address}] — [] when
// none.
function opnsenseGateways(inst) {
  /* jshint validthis: true */
  const d = opnsenseData.call(this, inst);
  return (d && Array.isArray(d.gateways)) ? d.gateways : [];
}

// Humanise a bytes-per-second rate -> "1.5 MB/s" (decimal/1000). '0 B/s' for
// non-positive / non-finite.
function opnsenseBps(v) {
  let b = Number(v);
  if (!isFinite(b) || b <= 0) {
    return '0 B/s';
  }
  const units = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
  let i = 0;
  while (b >= 1000 && i < units.length - 1) {
    b /= 1000;
    i++;
  }
  return (i === 0 ? Math.round(b) : b.toFixed(1)) + ' ' + units[i];
}

// The per-interface throughput rows [{name, rx_bps, tx_bps}] — [] when none.
function opnsenseInterfaces(inst) {
  /* jshint validthis: true */
  const d = opnsenseData.call(this, inst);
  return (d && Array.isArray(d.interfaces)) ? d.interfaces : [];
}

// Load average (1m) as a percentage of CPU cores — the understandable form
// (100% = every core fully busy). null when core count is unknown (caller
// then falls back to the raw load number).
function opnsenseLoadPct(inst) {
  /* jshint validthis: true */
  const d = opnsenseData.call(this, inst);
  if (!d) {
    return null;
  }
  const cores = Number(d.cpu_cores) || 0;
  if (cores <= 0) {
    return null;
  }
  return Math.round((Number(d.load_1m) || 0) / cores * 100);
}

// Colour for a load-% value: green / amber / red at the same thresholds the
// stat bars use (so Load reads consistently with CPU / Memory).
function opnsenseLoadColor(pct) {
  const p = Number(pct) || 0;
  if (p >= 90) {
    return 'var(--danger)';
  }
  if (p >= 70) {
    return 'var(--warning)';
  }
  return 'var(--success)';
}

// Busiest interface's total (rx+tx) bytes/sec — the denominator for the
// per-interface relative throughput bars. Floored at 1 to avoid /0.
function opnsenseIfaceMax(inst) {
  /* jshint validthis: true */
  const list = opnsenseInterfaces.call(this, inst);
  let m = 1;
  for (let i = 0; i < list.length; i++) {
    const t = (Number(list[i].rx_bps) || 0) + (Number(list[i].tx_bps) || 0);
    if (t > m) {
      m = t;
    }
  }
  return m;
}

// Width % (0..100) of an interface's rx OR tx bar, relative to the busiest
// interface's total throughput.
function opnsenseIfaceBarPct(inst, bps) {
  /* jshint validthis: true */
  return Math.min(100, (Number(bps) || 0) / opnsenseIfaceMax.call(this, inst) * 100);
}

// Humanise uptime seconds -> "Xd Yh" / "Yh Zm" / "Zm"; '' when zero/unknown.
function opnsenseUptime(inst) {
  /* jshint validthis: true */
  const d = opnsenseData.call(this, inst);
  const s = d ? Math.max(0, Number(d.uptime_s) || 0) : 0;
  if (!s) {
    return '';
  }
  const day = Math.floor(s / 86400);
  const hr = Math.floor((s % 86400) / 3600);
  const min = Math.floor((s % 3600) / 60);
  if (day) {
    return day + 'd ' + hr + 'h';
  }
  if (hr) {
    return hr + 'h ' + min + 'm';
  }
  return min + 'm';
}

// Humanise a byte volume -> "1.5 GB" / "920 MB" (decimal/1000). '0 B' for
// non-positive / non-finite. Used for the period data-volume totals.
function opnsenseBytes(v) {
  let b = Number(v);
  if (!isFinite(b) || b <= 0) {
    return '0 B';
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let i = 0;
  while (b >= 1000 && i < units.length - 1) {
    b /= 1000;
    i++;
  }
  return (i === 0 ? Math.round(b) : b.toFixed(1)) + ' ' + units[i];
}

// The interface-throughput usage trend `{days, samples, total_rx_bytes,
// total_tx_bytes, peak_rx_bps, peak_tx_bps, avg_rx_bps, avg_tx_bps, active_days,
// series_rx, series_tx}` from the shared lifespan opnsense_sampler, or null while
// idle / no samples yet. Drives the card's throughput-trend sparkline.
function opnsenseUsage(inst) {
  /* jshint validthis: true */
  const d = opnsenseData.call(this, inst);
  return (d && d.usage && typeof d.usage === 'object') ? d.usage : null;
}

// Memo: stable `:points` per numeric series array (avoids re-render flicker on
// every Alpine flush — the canonical SVG-builder memo).
const _opnsenseTrendMemo = new WeakMap();

// SVG polyline points for a trend series over a 0..100 x 0..24 viewBox,
// auto-scaled to the series' own max (min pinned at 0). '' when < 2 points.
// Memoised on the array ref.
function opnsenseUsagePath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_opnsenseTrendMemo.has(arr)) {
    return _opnsenseTrendMemo.get(arr);
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
  _opnsenseTrendMemo.set(arr, pts);
  return pts;
}

// Endpoint diagnostics (HTTP status + body snippet + self-diagnosed hint) are
// stamped by this app's fetch_data into the standard out['_debug'] block and
// rendered by the GENERIC drawer debug panel (_components/apps/_debug_panel.html
// + appsDebug / appsDebugHint helpers) — no app-specific helper needed here.

// Extender record -- consumed by the generic helpers in `static/js/app-apps.js`
// via `window.OG_APPS_EXTENDERS`. Key+secret auth + a 2-column card span.
export const extender = {
  slugs: ['opnsense'],
  requiresApiKey: true,
  cardSpan(app) {
    return isOpnsenseApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `opnsense*`.
export const helpers = {
  opnsenseIsApp: isOpnsenseApp,
  opnsenseData: opnsenseData,
  opnsenseCount: opnsenseCount,
  opnsenseFraction: opnsenseFraction,
  opnsenseGateways: opnsenseGateways,
  opnsenseUptime: opnsenseUptime,
  opnsenseBps: opnsenseBps,
  opnsenseBytes: opnsenseBytes,
  opnsenseInterfaces: opnsenseInterfaces,
  opnsenseLoadPct: opnsenseLoadPct,
  opnsenseLoadColor: opnsenseLoadColor,
  opnsenseIfaceMax: opnsenseIfaceMax,
  opnsenseIfaceBarPct: opnsenseIfaceBarPct,
  opnsenseUsage: opnsenseUsage,
  opnsenseUsagePath: opnsenseUsagePath,
};
