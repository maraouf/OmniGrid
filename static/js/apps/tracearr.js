// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Tracearr (Plex / Jellyfin / Emby fleet monitoring).
//
// Encapsulates every Tracearr-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`. Mirrors the Tautulli card shape (a 4-stat
// panel) but Tracearr watches a MULTI-server media fleet, so the stats are
// Active streams / Servers (online/total) / Users / Violations, sourced from
// `logic/apps/tracearr.py:fetch_data` via the cache-backed `appsAppData(inst)`.
//
// File-scope IDE directives: see `bazarr.js`'s header for the full rationale --
// same per-file JSHint + PyCharm conventions.

// True when `app` is the Tracearr catalog template (slug match; falls back to a
// substring check on `app.name`).
function isTracearrApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'tracearr') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('tracearr') !== -1);
}

// Per-instance Tracearr data lookup -- reads the per-app data the generic
// dispatcher fetched from `/api/v1/public/stats` (+ /health) via
// `logic/apps/tracearr.py:fetch_data`. Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides cleanly.
function tracearrData(inst) {
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
function tracearrCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// "online/total" servers label, e.g. "2/3". '—' for missing.
function tracearrServers(d) {
  if (!d) {
    return '—';
  }
  const on = Number(d.servers_online);
  const tot = Number(d.servers_total);
  if (!isFinite(on) || !isFinite(tot)) {
    return '—';
  }
  return Math.round(on).toLocaleString() + '/' + Math.round(tot).toLocaleString();
}

// Total stream bandwidth — already a human string from Tracearr's
// formatBitrate (e.g. "12.3 Mbps"). '—' when nothing is streaming.
function tracearrBandwidth(d) {
  const s = d && d.bandwidth ? String(d.bandwidth).trim() : '';
  return s || '—';
}

// SVG `d` path for the plays-over-time sparkline (last 30 daily buckets), over
// a 200x32 viewBox — mirrors the speedtest sparkPath. '' when < 2 points so the
// chart hides cleanly.
function tracearrPlaysPath(d) {
  const series = (d && Array.isArray(d.plays_series)) ? d.plays_series.map(Number).filter(isFinite) : [];
  if (series.length < 2) {
    return '';
  }
  const W = 200, H = 32;
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = (max - min) || 1;
  const stepX = W / Math.max(1, series.length - 1);
  let path = '';
  for (let i = 0; i < series.length; i++) {
    const x = (i * stepX).toFixed(1);
    const y = (H - ((series[i] - min) / range) * H).toFixed(1);
    path += (i === 0 ? 'M' : 'L') + x + ',' + y + ' ';
  }
  return path.trim();
}

// True when there's a plays series worth charting (>= 2 points).
function tracearrHasPlays(d) {
  return !!(d && Array.isArray(d.plays_series) && d.plays_series.length >= 2);
}

// Percentage (0-100) of one playback tier in the quality breakdown. `tier` is
// 'direct_play' | 'direct_stream' | 'transcode'. 0 when no data.
function tracearrQualityPct(d, tier) {
  const q = (d && d.quality) ? d.quality : null;
  if (!q) {
    return 0;
  }
  const total = Number(q.total) || 0;
  if (total <= 0) {
    return 0;
  }
  return Math.round(((Number(q[tier]) || 0) / total) * 100);
}

// True when the quality breakdown has any plays to show.
function tracearrHasQuality(d) {
  return !!(d && d.quality && (Number(d.quality.total) || 0) > 0);
}

// Top platforms list ([{name, count}]) for the breakdown chips. [] when none.
function tracearrPlatforms(d) {
  return (d && Array.isArray(d.platforms)) ? d.platforms : [];
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Tracearr gets a
// 2-column span + a vertical telemetry-card layout like the rest of the family.
export const extender = {
  slugs: ['tracearr'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isTracearrApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `tracearr*`.
export const helpers = {
  tracearrIsApp: isTracearrApp,
  tracearrData: tracearrData,
  tracearrCount: tracearrCount,
  tracearrServers: tracearrServers,
  tracearrBandwidth: tracearrBandwidth,
  tracearrPlaysPath: tracearrPlaysPath,
  tracearrHasPlays: tracearrHasPlays,
  tracearrQualityPct: tracearrQualityPct,
  tracearrHasQuality: tracearrHasQuality,
  tracearrPlatforms: tracearrPlatforms,
};
