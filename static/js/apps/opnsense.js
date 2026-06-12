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
  opnsenseInterfaces: opnsenseInterfaces,
};
