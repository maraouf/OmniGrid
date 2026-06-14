// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- netboot.xyz (network-boot menu manager).
//
// Encapsulates every netboot.xyz-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   netboot.xyz's webapp has no auth and no plain-HTTP stats API — its data is
//   socket.io-driven. `logic/apps/netbootxyz.py:fetch_data` drives the webapp's
//   `getdash` socket.io event (over HTTP long-polling) to read boot-menu /
//   webapp versions + update status + host CPU / RAM, read through the
//   cache-backed `appsAppData(inst)`.
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the netboot.xyz catalog template (matched via slug; falls
// back to a substring check on `app.name`).
function isNetbootxyzApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'netboot-xyz' || slug === 'netbootxyz') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('netboot') !== -1);
}

// Per-instance netboot.xyz data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/netbootxyz.py:fetch_data`). Returns null
// while idle / pending / errored OR when the payload isn't available.
function netbootxyzData(inst) {
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

// Host RAM percent (used / total) for the card; null when the dash probe
// didn't return memory figures (so the RAM stat hides cleanly).
function netbootxyzMemPercent(d) {
  if (!d) {
    return null;
  }
  const total = Number(d.mem_total) || 0;
  const used = Number(d.mem_used) || 0;
  if (total <= 0) {
    return null;
  }
  return Math.round((used / total) * 100);
}

// The locally-downloaded boot assets ([{name, size_bytes}], [] when absent) —
// "what can I PXE-boot right now". Drives the drawer's downloaded-assets list.
function netbootxyzAssets(inst) {
  /* jshint validthis: true */
  const d = (this.netbootxyzData ? this.netbootxyzData(inst) : null);
  if (!d || !Array.isArray(d.assets)) {
    return [];
  }
  return d.assets.filter((a) => a && String(a.name || '').trim());
}

// The UNTRACKED (orphaned / old) downloaded boot assets ([{name, size_bytes}],
// [] when none) — downloads not referenced by the current boot-menu catalog.
// Drives the drawer's untracked-assets review list + the Clear-untracked
// action; the count also surfaces as a warning-tinted stat cell.
function netbootxyzUntracked(inst) {
  /* jshint validthis: true */
  const d = (this.netbootxyzData ? this.netbootxyzData(inst) : null);
  if (!d || !Array.isArray(d.untracked)) {
    return [];
  }
  return d.untracked.filter((a) => a && String(a.name || '').trim());
}

// Render a byte count as a human size (KB / MB / GB, decimal/1000). '' for
// non-positive / missing — the row then shows just the name.
function netbootxyzSize(n) {
  let b = Number(n) || 0;
  if (!isFinite(b) || b <= 0) {
    return '';
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  for (let i = 0; i < units.length; i++) {
    if (b < 1000) {
      return (i === 0 ? b.toFixed(0) : b.toFixed(1)) + ' ' + units[i];
    }
    b /= 1000;
  }
  return b.toFixed(1) + ' PB';
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. No api_key (the
// webapp has no auth) and the default single-column layout (it's a compact
// status + version tile, not a multi-stat telemetry panel).
export const extender = {
  slugs: ['netboot-xyz', 'netbootxyz'],
  requiresApiKey: false,
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `netbootxyz*`.
export const helpers = {
  netbootxyzIsApp: isNetbootxyzApp,
  netbootxyzData: netbootxyzData,
  netbootxyzMemPercent: netbootxyzMemPercent,
  netbootxyzAssets: netbootxyzAssets,
  netbootxyzUntracked: netbootxyzUntracked,
  netbootxyzSize: netbootxyzSize,
};
