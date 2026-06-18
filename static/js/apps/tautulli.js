// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Tautulli (Plex monitoring + statistics).
//
// Encapsulates every Tautulli-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`. Mirrors the *arr / Kavita card shape (a
// 4-stat panel) but Tautulli monitors a Plex server, so the stats are
// Streams / Transcodes / Bandwidth / Libraries, sourced from
// `logic/apps/tautulli.py:fetch_data` via the cache-backed `appsAppData(inst)`.
// No Storage section (Tautulli reports activity, not per-mount disks).
//
// File-scope IDE directives: see `bazarr.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Tautulli catalog template (slug match; falls back to a
// substring check on `app.name`).
function isTautulliApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'tautulli') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('tautulli') !== -1);
}

// Per-instance Tautulli data lookup -- reads the per-app data the generic
// dispatcher fetched from `cmd=get_activity` (+ libraries / version) via
// `logic/apps/tautulli.py:fetch_data`. Returns null while idle / pending /
// errored OR when the payload isn't available, so the panel gate hides cleanly.
function tautulliData(inst) {
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
function tautulliCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Format a kbps bandwidth figure as a human rate (kbps / Mbps / Gbps). '—' for
// missing / zero (nothing streaming).
function tautulliBandwidth(kbps) {
  const n = Number(kbps);
  if (kbps == null || !isFinite(n) || n <= 0) {
    return '—';
  }
  if (n < 1000) {
    return Math.round(n).toLocaleString() + ' kbps';
  }
  const mbps = n / 1000;
  if (mbps < 1000) {
    return mbps.toLocaleString(undefined, {maximumFractionDigits: 1}) + ' Mbps';
  }
  return (mbps / 1000).toLocaleString(undefined, {maximumFractionDigits: 1}) + ' Gbps';
}

// SVG `d` path for the plays-over-time sparkline (last 30 daily buckets), over
// a 200x32 viewBox — mirrors the Tracearr / Speedtest sparkline. '' when < 2
// points so the chart hides cleanly. `this` is the Alpine component.
function tautulliPlaysPath(inst) {
  /* jshint validthis: true */
  const d = tautulliData.call(this, inst);
  const raw = (d && Array.isArray(d.plays_series)) ? d.plays_series : [];
  const series = raw.map(Number).filter(isFinite);
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
function tautulliHasPlays(inst) {
  /* jshint validthis: true */
  const d = tautulliData.call(this, inst);
  return !!(d && Array.isArray(d.plays_series) && d.plays_series.length >= 2);
}

// The #1 watcher (last 30d) for the card's "Top watcher" stat, or null.
function tautulliTopUser(inst) {
  /* jshint validthis: true */
  const d = tautulliData.call(this, inst);
  const u = (d && Array.isArray(d.top_users)) ? d.top_users : [];
  return u.length ? u[0] : null;
}

// Memo: stable bar-rect array per numeric `values` array (avoids re-render
// flicker on every Alpine flush — the bars are an object array `x-for` reads).
const _tautulliBarsMemo = new WeakMap();

// SVG bar-rect geometry [{x, y, w, h}] for a distribution (day-of-week /
// hour-of-day) over a 0..200 × 0..32 viewBox, auto-scaled to the series max.
// [] for an empty / missing series. Memoised on the array ref.
function tautulliBars(values) {
  if (!Array.isArray(values) || !values.length) {
    return [];
  }
  if (_tautulliBarsMemo.has(values)) {
    return _tautulliBarsMemo.get(values);
  }
  const W = 200, H = 32, n = values.length;
  const gap = n > 16 ? 0.5 : 1;
  let max = 0;
  for (let i = 0; i < n; i++) {
    const v = Number(values[i]) || 0;
    if (v > max) {
      max = v;
    }
  }
  max = max || 1;
  const slot = W / n;
  const bw = Math.max(0.5, slot - gap);
  const out = [];
  for (let i = 0; i < n; i++) {
    const h = Math.max(0, (Number(values[i]) || 0) / max * H);
    out.push({
      x: (i * slot).toFixed(2), y: (H - h).toFixed(2),
      w: bw.toFixed(2), h: h.toFixed(2)
    });
  }
  _tautulliBarsMemo.set(values, out);
  return out;
}

// Single SVG path string for the whole bar chart — rendered as ONE <path> on a
// 200×32 viewBox. A <template x-for> producing <rect> inside <svg> is broken:
// the HTML parser treats the nested <template> as a foreign SVG element with no
// .content, so Alpine can't establish the loop scope and crashes. Drawing every
// bar as one solid-filled path (the codebase SVG-chart convention) sidesteps it.
function tautulliBarsPath(values) {
  const bars = tautulliBars(values);
  if (!bars.length) {
    return '';
  }
  let d = '';
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    d += 'M' + b.x + ' ' + b.y + 'h' + b.w + 'v' + b.h + 'h-' + b.w + 'z';
  }
  return d;
}

// The busiest category {label, value} in a distribution {labels, values}, or
// null when empty / all-zero.
function tautulliPeak(dist) {
  if (!dist || !Array.isArray(dist.values) || !dist.values.length) {
    return null;
  }
  let maxI = 0, maxV = -Infinity;
  for (let i = 0; i < dist.values.length; i++) {
    const v = Number(dist.values[i]) || 0;
    if (v > maxV) {
      maxV = v;
      maxI = i;
    }
  }
  if (maxV <= 0) {
    return null;
  }
  const labels = Array.isArray(dist.labels) ? dist.labels : [];
  return {label: String(labels[maxI] || ''), value: maxV};
}

// The transcode-vs-direct stream-type series {labels, direct, transcode} from
// the payload, or empty arrays.
function tautulliStreamType(inst) {
  /* jshint validthis: true */
  const d = tautulliData.call(this, inst);
  const st = (d && d.stream_type) || {};
  return {
    labels: Array.isArray(st.labels) ? st.labels : [],
    direct: Array.isArray(st.direct) ? st.direct : [],
    transcode: Array.isArray(st.transcode) ? st.transcode : [],
  };
}

// True when there's a transcode-vs-direct series worth charting (>= 2 days AND
// at least one non-zero point across either line).
function tautulliHasStreamType(inst) {
  /* jshint validthis: true */
  const st = tautulliStreamType.call(this, inst);
  if (st.labels.length < 2) {
    return false;
  }
  for (let i = 0; i < st.direct.length; i++) {
    if ((Number(st.direct[i]) || 0) > 0) {
      return true;
    }
  }
  for (let i = 0; i < st.transcode.length; i++) {
    if ((Number(st.transcode[i]) || 0) > 0) {
      return true;
    }
  }
  return false;
}

// Shared-scale max across BOTH the direct + transcode series, so the two lines
// are comparable on one chart. 1 (not 0) so the path builder never divides by 0.
function tautulliStreamTypeMax(inst) {
  /* jshint validthis: true */
  const st = tautulliStreamType.call(this, inst);
  let max = 0;
  for (let i = 0; i < st.direct.length; i++) {
    const v = Number(st.direct[i]) || 0;
    if (v > max) {
      max = v;
    }
  }
  for (let i = 0; i < st.transcode.length; i++) {
    const v = Number(st.transcode[i]) || 0;
    if (v > max) {
      max = v;
    }
  }
  return max || 1;
}

// Memo: stable `:d` per (series array, shared max) so the dual line doesn't
// re-render-flicker on every Alpine flush.
const _tautulliStreamPathMemo = new WeakMap();

// SVG line path for one stream-type series over a 200x32 viewBox, scaled to the
// SHARED `max` (passed in so direct + transcode sit on one scale). '' when < 2
// points. Memoised on (array ref + max).
function tautulliStreamTypePath(values, max) {
  if (!Array.isArray(values) || values.length < 2) {
    return '';
  }
  const m = Number(max) || 1;
  const cached = _tautulliStreamPathMemo.get(values);
  if (cached && cached.max === m) {
    return cached.d;
  }
  const W = 200, H = 32, n = values.length;
  const stepX = W / Math.max(1, n - 1);
  let d = '';
  for (let i = 0; i < n; i++) {
    const x = (i * stepX).toFixed(1);
    const y = (H - ((Number(values[i]) || 0) / m) * H).toFixed(1);
    d += (i === 0 ? 'M' : 'L') + x + ',' + y + ' ';
  }
  d = d.trim();
  _tautulliStreamPathMemo.set(values, {max: m, d: d});
  return d;
}

// Share of plays that were TRANSCODED over the charted window (0-100 int), or
// null when there's no data — drives the "N% transcoded" caption.
function tautulliTranscodeShare(inst) {
  /* jshint validthis: true */
  const st = tautulliStreamType.call(this, inst);
  let dir = 0, tr = 0;
  for (let i = 0; i < st.direct.length; i++) {
    dir += Number(st.direct[i]) || 0;
  }
  for (let i = 0; i < st.transcode.length; i++) {
    tr += Number(st.transcode[i]) || 0;
  }
  const total = dir + tr;
  if (total <= 0) {
    return null;
  }
  return Math.round((100 * tr) / total);
}

// Concurrent-stream retention trend from the lifespan tautulli_sampler (per-day
// peak streams + per-day mean bandwidth + today_peak), or null while idle / no
// samples. Mirrors plexTrend.
function tautulliTrend(inst) {
  /* jshint validthis: true */
  const d = tautulliData.call(this, inst);
  return (d && d.trend && typeof d.trend === 'object') ? d.trend : null;
}

// Memo: stable `:d` per numeric series array (avoids re-render flicker on every
// Alpine flush).
const _tautulliTrendMemo = new WeakMap();

// SVG polyline points for a sparkline over a 0..200 × 0..32 viewBox, auto-scaled
// to the series' own min/max. '' when < 2 points. Memoised on the array ref.
function tautulliTrendPath(arr) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  if (_tautulliTrendMemo.has(arr)) {
    return _tautulliTrendMemo.get(arr);
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
  _tautulliTrendMemo.set(arr, d);
  return d;
}

// The "peak concurrent today" figure from the local sampler (or 0). The card
// shows it only when > 0.
function tautulliTodayPeak(inst) {
  /* jshint validthis: true */
  const t = tautulliTrend.call(this, inst);
  return (t && Number(t.today_peak)) || 0;
}

// The most-active library {name, plays, type} (last 30d) for the drawer stat,
// or null when the home-stats payload didn't carry the top_libraries card.
function tautulliMostActiveLib(inst) {
  /* jshint validthis: true */
  const d = tautulliData.call(this, inst);
  const lib = (d && d.most_active_library) || {};
  return lib && lib.name ? lib : null;
}

// The single most-played title {title, plays, type} (last 30d), or null.
function tautulliMostPlayed(inst) {
  /* jshint validthis: true */
  const d = tautulliData.call(this, inst);
  const m = (d && d.most_played) || {};
  return m && m.title ? m : null;
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Tautulli gets a
// 2-column span + a vertical telemetry-card layout like the rest of the family.
export const extender = {
  slugs: ['tautulli'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isTautulliApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `tautulli*`.
export const helpers = {
  tautulliIsApp: isTautulliApp,
  tautulliData: tautulliData,
  tautulliCount: tautulliCount,
  tautulliBandwidth: tautulliBandwidth,
  tautulliPlaysPath: tautulliPlaysPath,
  tautulliHasPlays: tautulliHasPlays,
  tautulliTopUser: tautulliTopUser,
  tautulliBars: tautulliBars,
  tautulliBarsPath: tautulliBarsPath,
  tautulliPeak: tautulliPeak,
  tautulliStreamType: tautulliStreamType,
  tautulliHasStreamType: tautulliHasStreamType,
  tautulliStreamTypeMax: tautulliStreamTypeMax,
  tautulliStreamTypePath: tautulliStreamTypePath,
  tautulliTranscodeShare: tautulliTranscodeShare,
  tautulliTrend: tautulliTrend,
  tautulliTrendPath: tautulliTrendPath,
  tautulliTodayPeak: tautulliTodayPeak,
  tautulliMostActiveLib: tautulliMostActiveLib,
  tautulliMostPlayed: tautulliMostPlayed,
};
