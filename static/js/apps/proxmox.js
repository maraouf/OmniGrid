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
};
