// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- UniFi (UniFi Network / UniFi OS console).
//
// Encapsulates every UniFi-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   UniFi chips render a stat panel (sites / devices online+total / APs /
//   switches / gateways / clients wired+wireless + version) sourced from the
//   official UniFi Network Integration API (X-API-KEY) via
//   `logic/apps/unifi.py:fetch_data`. The card reads the per-app data the
//   generic dispatcher fetched, so it never triggers a per-card round-trip on
//   the hot path. `unifiData(inst)` reads it via the cache-backed
//   `appsAppData(inst)` helper (same path GitSync / Grafana use).
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the UniFi catalog template (matched via slug; falls back
// to a substring check on `app.name` so an operator-edited chip that dropped the
// catalog link but kept the brand still resolves -- 'unifi' / 'UniFi OS' /
// 'Ubiquiti UniFi' all hit).
function isUnifiApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  // Substring match so every UniFi variant resolves — the canonical
  // `unifi-os-server` template, the bare `unifi` / `unifi-network` / `unifi-os`
  // aliases, AND the editor gate's lowercased catalog NAME ("unifi os server").
  if (slug.indexOf('unifi') !== -1) {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('unifi') !== -1);
}

// Per-instance UniFi data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/unifi.py:fetch_data`). Returns null while
// idle / pending / errored OR when the payload isn't available, so the panel
// gate hides cleanly.
function unifiData(inst) {
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
function unifiCount(v) {
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
function unifiDeviceFraction(d) {
  if (!d) {
    return '—';
  }
  return unifiCount(d.devices_online) + ' / ' + unifiCount(d.devices);
}

// Per-AP client-load rows for the drawer distribution bars — each {name,
// clients, pct} where pct is the bar width relative to the busiest AP. Capped
// at the 6 busiest (the backend sorts busiest-first). [] when no per-AP data
// (older Network versions without uplinkDeviceId → the block hides).
function unifiApLoad(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  const d = (this.unifiData ? this.unifiData(inst) : null);
  if (!d || !Array.isArray(d.ap_load) || !d.ap_load.length) {
    return [];
  }
  const rows = d.ap_load.filter((r) => r && (Number(r.clients) || 0) > 0).slice(0, 6);
  if (!rows.length) {
    return [];
  }
  let max = 1;
  for (const r of rows) {
    const c = Number(r.clients) || 0;
    if (c > max) {
      max = c;
    }
  }
  return rows.map((r) => ({
    name: String(r.name || '?'),
    clients: Number(r.clients) || 0,
    pct: Math.max(4, Math.round((Number(r.clients) || 0) / max * 100)),
    // Per-AP radio channel + channel-utilisation (from the legacy stat/device
    // join) — channel 0 / util null when the AP wasn't in the radio map. The
    // drawer bar shows "ch 36 (5G) · 42%" beside the client count.
    channel: Number(r.channel) || 0,
    util: (r.util == null) ? null : (Number(r.util) || 0),
    band: String(r.band || ''),
  }));
}

// WAN / ISP-uplink block from the legacy stat/health join, or null. Carries the
// live throughput (rx_bps / tx_bps, bytes/sec), the plan speeds (down_mbps /
// up_mbps), the utilisation % (down_util / up_util), latency + IP.
function unifiWan(inst) {
  /* jshint validthis: true */
  const d = (this.unifiData ? this.unifiData(inst) : null);
  const w = (d && d.wan && typeof d.wan === 'object') ? d.wan : null;
  if (!w) {
    return null;
  }
  // Treat an all-zero WAN block (no gateway / never sampled) as absent so the
  // card doesn't show a meaningless "0 / 0 Mbps".
  const keys = ['rx_bps', 'tx_bps', 'down_mbps', 'up_mbps'];
  return keys.some((k) => (Number(w[k]) || 0) > 0) ? w : null;
}

// Format a byte/sec rate as Mbps ('—' for non-finite). 1 decimal under 100, 0
// at/above (a 935 Mbps line shouldn't show ".0").
function unifiMbps(bps) {
  const n = Number(bps) || 0;
  const mbps = n * 8 / 1000000;
  if (!isFinite(mbps)) {
    return '—';
  }
  return (mbps >= 100 ? Math.round(mbps) : Math.round(mbps * 10) / 10).toLocaleString();
}

// "↓ X / ↑ Y Mbps" live WAN throughput string for the card stat.
function unifiWanThroughput(inst) {
  /* jshint validthis: true */
  const w = unifiWan.call(this, inst);
  if (!w) {
    return '—';
  }
  return '↓ ' + unifiMbps(w.rx_bps) + ' / ↑ ' + unifiMbps(w.tx_bps) + ' Mbps';
}

// Client-occupancy retention trend from the lifespan unifi_sampler (per-day
// average client count + wireless split), or null while idle / no samples.
function unifiTrend(inst) {
  /* jshint validthis: true */
  const d = (this.unifiData ? this.unifiData(inst) : null);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:d` per numeric series array (avoids re-render flicker on every
// Alpine flush).
const _unifiTrendMemo = new WeakMap();

// SVG polyline points for a sparkline over a 0..200 × 0..32 viewBox, auto-scaled
// to the series' own min/max. '' when < 2 points. Memoised on the array ref.
function unifiTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_unifiTrendMemo.has(arr)) {
    return _unifiTrendMemo.get(arr);
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
  _unifiTrendMemo.set(arr, d);
  return d;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. UniFi gets a 2-column
// span so the stat panel doesn't squeeze the per-instance host list, and a
// vertical telemetry-card layout like GitSync / Grafana.
export const extender = {
  slugs: ['unifi-os-server', 'unifi', 'unifi-network', 'unifi-os'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isUnifiApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `unifi*` so they don't
// collide with other per-app modules' helpers.
export const helpers = {
  unifiIsApp: isUnifiApp,
  unifiData: unifiData,
  unifiCount: unifiCount,
  unifiDeviceFraction: unifiDeviceFraction,
  unifiApLoad: unifiApLoad,
  unifiWan: unifiWan,
  unifiMbps: unifiMbps,
  unifiWanThroughput: unifiWanThroughput,
  unifiTrend: unifiTrend,
  unifiTrendPath: unifiTrendPath,
};
