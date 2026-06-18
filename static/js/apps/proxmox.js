// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Proxmox VE (hypervisor management).
//
// Encapsulates every Proxmox-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`. Mirrors the Rundeck / Grafana card shape (a
// token-auth stat panel) -- the stats are Nodes / VMs / Containers / CPU / Mem,
// sourced from `logic/apps/proxmox.py:fetch_data` (one /cluster/resources call)
// via the cache-backed `appsAppData(inst)`.
//
// File-scope IDE directives: see `rundeck.js`'s header for the full rationale.

// True when `app` is the Proxmox catalog template (slug match; falls back to a
// substring check on `app.name`).
function isProxmoxApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'proxmox' || slug === 'proxmox-ve' || slug === 'pve') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('proxmox') !== -1);
}

// Per-instance Proxmox data lookup -- reads the per-app data the generic
// dispatcher fetched. Returns null while idle / pending / errored.
function proxmoxData(inst) {
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
function proxmoxCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// "running/total" fraction string for a guest kind ('vm' | 'ct' | 'node').
function proxmoxFraction(inst, kind) {
  /* jshint validthis: true */
  const d = proxmoxData.call(this, inst);
  if (!d) {
    return '—';
  }
  const map = {
    vm: ['vms_running', 'vms_total'], ct: ['cts_running', 'cts_total'],
    node: ['nodes_online', 'nodes_total']
  };
  const keys = map[kind] || map.vm;
  const run = Number(d[keys[0]]) || 0;
  const tot = Number(d[keys[1]]) || 0;
  return run.toLocaleString() + '/' + tot.toLocaleString();
}

// "used / total" storage string (humanised bytes) -- '' when no storage data.
function proxmoxStorage(inst) {
  /* jshint validthis: true */
  const d = proxmoxData.call(this, inst);
  const tot = d ? Number(d.storage_total) : 0;
  if (!isFinite(tot) || tot <= 0) {
    return '';
  }
  const fmt = (n) => {
    let v = Math.max(0, Number(n) || 0);
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let i = 0;
    while (v >= 1024 && i < units.length - 1) {
      v /= 1024;
      i++;
    }
    return (i === 0 ? Math.round(v) : v.toFixed(1)) + ' ' + units[i];
  };
  return fmt(d.storage_used) + ' / ' + fmt(tot);
}

// Cluster quorum / membership block ({clustered, quorate, name, nodes_online,
// nodes_total}) — null when absent OR for a STANDALONE node (clustered=false),
// since quorum only matters for a real cluster.
function proxmoxQuorum(inst) {
  /* jshint validthis: true */
  const d = proxmoxData.call(this, inst);
  const q = (d && d.quorum && typeof d.quorum === 'object') ? d.quorum : null;
  return (q && q.clustered) ? q : null;
}

// Last-backup block ({last_age_s, last_ok, failed_recent, total_recent}) or null.
function proxmoxBackup(inst) {
  /* jshint validthis: true */
  const d = proxmoxData.call(this, inst);
  const b = (d && d.backup && typeof d.backup === 'object') ? d.backup : null;
  return (b && Number.isFinite(Number(b.last_age_s))) ? b : null;
}

// Humanise a backup age (seconds) → "Nd" / "Nh" / "Nm" / "just now".
function proxmoxBackupAge(inst) {
  /* jshint validthis: true */
  const b = proxmoxBackup.call(this, inst);
  if (!b) {
    return '';
  }
  const s = Math.max(0, Math.floor(Number(b.last_age_s) || 0));
  const days = Math.floor(s / 86400);
  const hrs = Math.floor((s % 86400) / 3600);
  const mins = Math.floor((s % 3600) / 60);
  if (days) {
    return days + 'd';
  }
  if (hrs) {
    return hrs + 'h';
  }
  return mins ? (mins + 'm') : 'just now';
}

// Cluster-resource trend (from the lifespan proxmox_sampler) — per-day average
// CPU% / memory% / storage%, or null while idle / no samples.
function proxmoxTrend(inst) {
  /* jshint validthis: true */
  const d = proxmoxData.call(this, inst);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:d` per numeric series array (avoids re-render flicker).
const _pveTrendMemo = new WeakMap();

// SVG polyline `:d` over a 0..200 × 0..32 viewBox on a FIXED 0..100 scale (the
// series are percentages, so CPU/mem/storage share one comparable axis). '' when
// < 2 points. Memoised on the array ref.
function proxmoxTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_pveTrendMemo.has(arr)) {
    return _pveTrendMemo.get(arr);
  }
  const W = 200, H = 32, n = arr.length;
  const stepX = W / Math.max(1, n - 1);
  let d = '';
  for (let i = 0; i < n; i++) {
    const x = (i * stepX).toFixed(1);
    const v = Math.max(0, Math.min(100, Number(arr[i]) || 0));
    const y = (H - v / 100 * H).toFixed(1);
    d += (i === 0 ? 'M' : 'L') + x + ',' + y + ' ';
  }
  d = d.trim();
  _pveTrendMemo.set(arr, d);
  return d;
}

// Extender record -- consumed by the generic helpers in `static/js/app-apps.js`
// via `window.OG_APPS_EXTENDERS`. Token auth + a 2-column card span.
export const extender = {
  slugs: ['proxmox', 'proxmox-ve', 'pve'],
  requiresApiKey: true,
  cardSpan(app) {
    return isProxmoxApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `proxmox*`.
export const helpers = {
  proxmoxIsApp: isProxmoxApp,
  proxmoxData: proxmoxData,
  proxmoxCount: proxmoxCount,
  proxmoxFraction: proxmoxFraction,
  proxmoxStorage: proxmoxStorage,
  proxmoxQuorum: proxmoxQuorum,
  proxmoxBackup: proxmoxBackup,
  proxmoxBackupAge: proxmoxBackupAge,
  proxmoxTrend: proxmoxTrend,
  proxmoxTrendPath: proxmoxTrendPath,
};
