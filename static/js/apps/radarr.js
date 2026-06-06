// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Radarr (movie collection manager).
//
// Encapsulates every Radarr-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   Radarr chips render a 4-stat panel (movies / missing / downloading /
//   disk free) sourced from Radarr's `GET /api/v3/movie` + queue / disk
//   / health endpoints. The card reads the per-app data the generic
//   dispatcher fetched via `logic/apps/radarr.py:fetch_data`, so it never
//   triggers a per-card round-trip on the hot path. `radarrData(inst)`
//   reads it via the cache-backed `appsAppData(inst)` helper (same path
//   Bazarr / Seerr / Speedtest / APC use).
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Radarr catalog template (matched via slug; falls
// back to a substring check on `app.name` so an operator-edited chip that
// dropped the catalog link but kept the brand still resolves).
function isRadarrApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'radarr') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('radarr') !== -1);
}

// Per-instance Radarr data lookup -- reads the per-app data the generic
// dispatcher fetched from `GET /api/v3/movie` (via
// `logic/apps/radarr.py:fetch_data`). Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides
// cleanly. No module-scope memo needed: `this.appsAppData(inst)` is itself
// cache-backed and only READS during render.
function radarrData(inst) {
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
function radarrCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Format a GiB value as a compact "N.N GB" label; '—' for missing / zero.
function radarrGb(v) {
  const n = Number(v);
  if (v == null || !isFinite(n) || n <= 0) {
    return '—';
  }
  return n.toLocaleString(undefined, { maximumFractionDigits: 1 }) + ' GB';
}

// The per-mount disk list (every volume Radarr reports). Returns [] when the
// payload has none. Each entry is {path, free_gb, total_gb}.
function radarrDisks(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  const d = (this.radarrData ? this.radarrData(inst) : null);
  if (!d || !Array.isArray(d.disks)) {
    return [];
  }
  return d.disks;
}

// Format a GiB float as a human size, promoting to TiB at >= 1024 GiB
// (matches Radarr's own GiB / TiB display). '—' for missing / zero.
function radarrSize(gib) {
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
function radarrDiskUsedPct(m) {
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
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Radarr gets a
// 2-column span so the 4-stat panel doesn't squeeze the per-instance host
// list, and a vertical telemetry-card layout like Bazarr / Seerr / APC.
export const extender = {
  slugs: ['radarr'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isRadarrApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `radarr*` so they
// don't collide with other per-app modules' helpers.
export const helpers = {
  radarrIsApp: isRadarrApp,
  radarrData: radarrData,
  radarrCount: radarrCount,
  radarrGb: radarrGb,
  radarrDisks: radarrDisks,
  radarrSize: radarrSize,
  radarrDiskUsedPct: radarrDiskUsedPct,
};
