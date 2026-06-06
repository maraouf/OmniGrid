// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Sonarr (TV-series collection manager).
//
// Encapsulates every Sonarr-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`. Mirrors `radarr.js` (Sonarr is the TV
// analog of Radarr): a 4-stat panel (series / missing episodes /
// downloading / disk) + a multi-mount Storage list, sourced from
// `logic/apps/sonarr.py:fetch_data` via the cache-backed `appsAppData(inst)`.
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Sonarr catalog template (slug match; falls back to a
// substring check on `app.name`).
function isSonarrApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'sonarr') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('sonarr') !== -1);
}

// Per-instance Sonarr data lookup -- reads the per-app data the generic
// dispatcher fetched from `GET /api/v3/series` (via
// `logic/apps/sonarr.py:fetch_data`). Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides
// cleanly.
function sonarrData(inst) {
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
function sonarrCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// The per-mount disk list (every volume Sonarr reports). Returns [] when none.
function sonarrDisks(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  const d = (this.sonarrData ? this.sonarrData(inst) : null);
  if (!d || !Array.isArray(d.disks)) {
    return [];
  }
  return d.disks;
}

// Format a GiB float as a human size, promoting to TiB at >= 1024 GiB
// (matches Sonarr's own GiB / TiB display). '—' for missing / zero.
function sonarrSize(gib) {
  const n = Number(gib);
  if (gib == null || !isFinite(n) || n <= 0) {
    return '—';
  }
  if (n >= 1024) {
    return (n / 1024).toLocaleString(undefined, { maximumFractionDigits: 1 }) + ' TiB';
  }
  return n.toLocaleString(undefined, { maximumFractionDigits: 1 }) + ' GiB';
}

// Used-percent for a mount's usage bar (0..100); 0 when total is unknown.
function sonarrDiskUsedPct(m) {
  if (!m) {
    return 0;
  }
  const total = Number(m.total_gb);
  const free = Number(m.free_gb);
  if (!isFinite(total) || total <= 0 || !isFinite(free)) {
    return 0;
  }
  const pct = ((total - free) / total) * 100;
  return Math.max(0, Math.min(100, Math.round(pct)));
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Sonarr gets a
// 2-column span + a vertical telemetry-card layout like Radarr / Bazarr.
export const extender = {
  slugs: ['sonarr'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isSonarrApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `sonarr*`.
export const helpers = {
  sonarrIsApp: isSonarrApp,
  sonarrData: sonarrData,
  sonarrCount: sonarrCount,
  sonarrDisks: sonarrDisks,
  sonarrSize: sonarrSize,
  sonarrDiskUsedPct: sonarrDiskUsedPct,
};
