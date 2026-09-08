// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS,JSUnresolvedVariable,JSUnresolvedReference
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Speedtest Tracker.
//
// Encapsulates every Speedtest-Tracker-specific helper so the
// generic `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app
// module's `helpers` into the Alpine component AND exposes the
// per-app extender record (slugs / requiresApiKey / cardSpan)
// to the generic helpers (`appsCardSpan`,
// `appsTemplateRequiresApiKey`, ...) via
// `window.OG_APPS_EXTENDERS`.
//
// Module shape
//   export const extender = { slugs, requiresApiKey, cardSpan }
//   export const helpers  = { speedtestX, speedtestY, ... }
//
// File-scope IDE directives above (each evaluated, not blanket
// suppressions):
//   - `/* jshint esversion: 11, module: true */` — PyCharm's
//     JSHint integration doesn't walk the directory tree to
//     find the root `.jshintrc`, so ES6+ syntax (let / const /
//     spread / import / export) needs the explicit per-file
//     directive. Same directive at the top of every per-app
//     module under `static/js/apps/`.
//   - `ConstantOnRHSOfComparisonJS` + `ConstantOnLHSOfComparisonJS`:
//     PyCharm flags `v == null` (RHS) AND `null == v` (LHS) — both
//     directions of every literal comparison. Suppress both at file
//     scope so we keep idiomatic `v == null` (the canonical
//     project-wide null-or-undefined check used in ~260 sites
//     elsewhere; eslint.config.js permits it via `eqeqeq: "smart"`).
//   - `FunctionWithMultipleReturnPointsJS`: guard-clause early-
//     return is the documented project style; restructuring to a
//     single-return accumulator made the file harder to read AND
//     traded one warning class for two new ones (the
//     `ConstantOnLHSOfComparisonJS` Yoda variant).
//   - `FunctionNamingConventionJS`: project convention uses `_`
//     prefix for module-private internals; PyCharm's regex
//     `[a-z][A-Za-z]*` rejects it.
//   - `AnonymousFunctionJS`, `ChainedFunctionCallJS`,
//     `ConditionalExpressionJS`, `NestedFunctionCallJS`: idiomatic
//     modern JS (`.map(...).filter(...)`, ternaries in path
//     builders, `Number(p && p[key])`). The non-idiomatic
//     alternatives are noticeably less readable for no functional
//     gain.
//   - `JSUnusedGlobalSymbols`: `extender` + `helpers` are imported
//     via `import * as <slug>` in `_registry.js`; PyCharm can't
//     trace the dynamic registry pattern.
//   - `DuplicatedCode`: APC's per-app module has a similar shape
//     by design (per-app convention); duplication is the price of
//     full encapsulation.

// Module-scope callbacks consumed by sparkPath's map / filter
// chain -- top-level so PyCharm's nested-function inspection
// stays quiet AND the closure isn't re-allocated per call.
// `_makeCoercer` closes over the metric key (one chart per
// download / upload / ping kind needs its own coercer).
function _makeCoercer(key) {
  return function _coerce(p) {
    return Number(p && p[key]) || 0;
  };
}

function _isFiniteNumber(value) {
  return isFinite(value);
}

// Format download / upload. The backend normalises every Speedtest Tracker
// schema to Mbps (Ookla bytes/s × 8 ÷ 1e6 — both the nested `data.*.bandwidth`
// and the flat download/upload fields are bytes/s), so the value here is
// ALREADY Mbps. Always render Mbps with thousand separators (never Gbps) per
// the operator's request, up to 2 decimals.
function fmtBits(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n) || n < 0) {
    return '—';
  }
  return n.toLocaleString(undefined, {maximumFractionDigits: 2}) + ' Mbps';
}

// Format ping ms -- operator-readable, 1 decimal under 100ms,
// integer above.
function fmtPing(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n) || n < 0) {
    return '—';
  }
  if (n < 100) {
    return n.toFixed(1) + ' ms';
  }
  return Math.round(n) + ' ms';
}

// Format a packet-loss percentage -- 1 decimal, '—' for missing / negative.
function fmtPct(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n) || n < 0) {
    return '—';
  }
  return n.toFixed(1) + '%';
}

// Build the "ISP · Server (Location)" provenance string for the latest
// result. '' when neither ISP nor server is known.
function speedtestProvenance(latest) {
  if (!latest) {
    return '';
  }
  const isp = String(latest.isp || '').trim();
  const srv = String(latest.server || '').trim();
  const loc = String(latest.server_location || '').trim();
  const srvFull = srv + ((srv && loc) ? (' (' + loc + ')') : '');
  return [isp, srvFull].filter(Boolean).join(' · ');
}

// SVG path builder for one metric (download / upload / ping)
// over the cached series array. Empty / missing series returns
// "" so the path renders nothing. `width` / `height` are the
// SVG viewBox dimensions consumed by the matching template's
// `<svg viewBox="0 0 200 32">` in the extras partial -- change
// either side together if the chart card resizes.
// `peerKey` is a SECOND field sharing this frame and this unit. Download
// and upload are both Mbps, and the interesting thing about showing them
// together is which is higher — but each was normalised to its own range,
// so an upload a tenth the size of the download still filled the plot and
// the two tracked each other. Passing the sibling key puts both on one
// scale. Ping is deliberately NOT given a peer: it is milliseconds, a
// different dimension, and forcing it onto a Mbps scale would be a worse
// lie than leaving it on its own.
function sparkPath(series, key, peerKey) {
  if (!Array.isArray(series) || series.length < 2) {
    return '';
  }
  const coercer = _makeCoercer(key);
  const values = series.map(coercer).filter(_isFiniteNumber);
  if (values.length < 2) {
    return '';
  }
  let scale = values;
  if (peerKey) {
    const peerVals = series.map(_makeCoercer(peerKey)).filter(_isFiniteNumber);
    if (peerVals.length) {
      scale = values.concat(peerVals);
    }
  }
  const width = 200;
  const height = 32;
  const min = Math.min(...scale);
  const max = Math.max(...scale);
  const range = (max - min) || 1;
  const stepX = width / Math.max(1, values.length - 1);
  let d = '';
  for (let i = 0; i < values.length; i++) {
    const x = (i * stepX).toFixed(1);
    const y = (height - ((values[i] - min) / range) * height).toFixed(1);
    const cmd = (i === 0) ? 'M' : 'L';
    d += cmd + x + ',' + y + ' ';
  }
  return d.trim();
}

// Round a positive max UP to a friendly 1 / 2 / 5 x 10^n step so axis tick
// labels read cleanly (187 -> 200, 53 -> 60, 4200 -> 5000). Returns 1 for
// non-positive / non-finite input so the axis never divides by zero.
function _niceMax(v) {
  if (!isFinite(v) || v <= 0) {
    return 1;
  }
  const exp = Math.floor(Math.log10(v));
  const base = Math.pow(10, exp);
  const f = v / base;
  let nice;
  if (f <= 1) {
    nice = 1;
  } else if (f <= 2) {
    nice = 2;
  } else if (f <= 5) {
    nice = 5;
  } else {
    nice = 10;
  }
  return nice * base;
}

// Short local time label for an x-axis tick from an ISO `created_at` string.
// Browser locale owns the HH:MM format; '' when unparseable.
function _fmtChartTs(ts) {
  if (!ts) {
    return '';
  }
  const d = new Date(ts);
  if (isNaN(d.getTime())) {
    return '';
  }
  return d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}

// Build the AXED chart model for the wide-tall tiles (3x2 / 4x2). The simple
// `sparkPath` chart has no axes + normalises each line independently; here we
// put download + upload on a SHARED Mbps scale (left y-axis) so their relative
// magnitudes read true, ping on its own ms scale (right y-axis), over a time
// x-axis, with tick labels. Coordinates live in a fixed viewBox; the partial
// draws the axis lines / gridlines / tick text / polylines from this model.
// Returns null when < 2 points (caller hides the chart).
// Short local DATE label for an x-axis tick from an epoch-seconds value.
// Used by the long-horizon trend chart, whose points are DAILY buckets — a
// clock time there would claim a precision the bucket does not have.
function _fmtChartDate(epochSeconds) {
  const n = Number(epochSeconds) || 0;
  if (n <= 0) {
    return '';
  }
  const d = new Date(n * 1000);
  if (isNaN(d.getTime())) {
    return '';
  }
  return d.toLocaleDateString([], {month: 'short', day: 'numeric'});
}

// Shared coordinate + tick builder behind BOTH axed charts. Takes three
// parallel numeric arrays and the two x-axis end labels, and returns the model
// the partial draws: axis extents, one path per metric, and the tick arrays.
//
// download + upload share ONE Mbps scale (left y-axis) so their relative
// magnitudes read true; ping gets its OWN ms scale (right y-axis) because it
// is a different dimension and forcing it onto the Mbps scale would misstate
// it. That dual-axis treatment is the answer to "these lines are not the same
// unit" — not leaving the frame unlabelled.
//
// `ul` / `pg` may be empty (an older history with no companion series): the
// missing line's path comes back '' and it contributes nothing to the scale.
function _axedModel(dl, ul, pg, xStartLabel, xEndLabel) {
  // Wide + SHORT viewBox. The SVG is rendered with preserveAspectRatio="none"
  // + a fixed CSS height equal to H, so it ALWAYS fills the tile width and is
  // exactly H px tall (no letterbox gap, no auto-height ambiguity). W is set
  // near a typical wide-tile width so the horizontal stretch stays ~1.0; the
  // data-line strokes use vector-effect="non-scaling-stroke" to stay crisp.
  const W = 460, H = 92, padL = 42, padR = 36, padT = 8, padB = 16;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  function peak(arr) {
    let m = 0;
    for (let i = 0; i < arr.length; i++) {
      const v = Number(arr[i]) || 0;
      if (v > m) {
        m = v;
      }
    }
    return m;
  }

  const mbpsMax = _niceMax(Math.max(1, peak(dl), peak(ul)));
  const pingMax = _niceMax(Math.max(1, peak(pg)));
  const n = dl.length;
  const stepX = plotW / Math.max(1, n - 1);

  function xAt(i) {
    return padL + i * stepX;
  }

  function yMbps(v) {
    return padT + plotH - (Math.min(v, mbpsMax) / mbpsMax) * plotH;
  }

  function yPing(v) {
    return padT + plotH - (Math.min(v, pingMax) / pingMax) * plotH;
  }

  function path(vals, yf) {
    let d = '';
    for (let i = 0; i < vals.length; i++) {
      d += (i === 0 ? 'M' : 'L') + xAt(i).toFixed(1) + ',' + yf(Number(vals[i]) || 0).toFixed(1) + ' ';
    }
    return d.trim();
  }

  const yTicks = [0, mbpsMax / 2, mbpsMax].map(function (v) {
    return {y: yMbps(v).toFixed(1), label: (Math.round(v * 10) / 10).toLocaleString()};
  });
  const pTicks = [0, pingMax].map(function (v) {
    return {y: yPing(v).toFixed(1), label: String(Math.round(v))};
  });
  const xTicks = [
    {x: xAt(0).toFixed(1), label: xStartLabel, anchor: 'start'},
    {x: xAt(Math.max(0, n - 1)).toFixed(1), label: xEndLabel, anchor: 'end'},
  ];
  return {
    w: W, h: H,
    axisX0: padL, axisX1: (W - padR), axisY0: padT, axisY1: (padT + plotH),
    downPath: path(dl, yMbps),
    upPath: path(ul, yMbps),
    pingPath: path(pg, yPing),
    yTicks: yTicks, pTicks: pTicks, xTicks: xTicks,
  };
}

// Build the AXED chart model for the wide-tall tiles (3x2 / 4x2) and the app
// drawer, over the RECENT per-test series. The simple `sparkPath` chart has no
// axes + normalises each line independently; this one is scaled and labelled.
// Returns null when < 2 points (caller hides the chart).
function speedtestChartModel(series) {
  if (!Array.isArray(series) || series.length < 2) {
    return null;
  }
  const dl = series.map(function (p) {
    return Number(p && p.download) || 0;
  });
  const ul = series.map(function (p) {
    return Number(p && p.upload) || 0;
  });
  const pg = series.map(function (p) {
    return Number(p && p.ping) || 0;
  });
  return _axedModel(dl, ul, pg,
                    _fmtChartTs(series[0].ts),
                    _fmtChartTs(series[series.length - 1].ts));
}

// Memo: one model per trend block reference. The trend arrays are rebuilt only
// when the payload is refetched, so keying on the block itself is stable and
// keeps the model out of every Alpine flush.
const _stTrendModelMemo = new WeakMap();

// Build the AXED model for the LONG-HORIZON trend (daily medians out of
// OmniGrid's own speedtest_samples history). Same dual-axis treatment as the
// recent-series chart above.
//
// This frame previously drew its three lines through a builder that
// normalised each array to its OWN min/max. Download and upload are both
// Mbps — so an upload a fifth the size of the download still climbed to the top
// of the plot and the two read as level. Sharing the Mbps scale is what makes
// them comparable, and is what lets the frame carry an honest axis.
//
// Returns null when there are < 2 daily points (caller hides the chart).
function speedtestTrendChartModel(trend) {
  if (!trend || typeof trend !== 'object') {
    return null;
  }
  const dl = Array.isArray(trend.series) ? trend.series : [];
  if (dl.length < 2) {
    return null;
  }
  const hit = _stTrendModelMemo.get(trend);
  if (hit) {
    return hit;
  }
  // The companion series share `series`' day buckets + stride, so they line up
  // index-for-index when present. A short/absent one is simply not drawn.
  const ul = (Array.isArray(trend.series_upload) && trend.series_upload.length === dl.length)
    ? trend.series_upload : [];
  const pg = (Array.isArray(trend.series_ping) && trend.series_ping.length === dl.length)
    ? trend.series_ping : [];
  const model = _axedModel(dl, ul, pg,
                           _fmtChartDate(trend.first_ts),
                           _fmtChartDate(trend.last_ts));
  _stTrendModelMemo.set(trend, model);
  return model;
}

// True when `app` is a Speedtest Tracker catalog template
// (matched via slug; falls back to a substring check on
// `app.name` so an operator-edited chip that dropped the
// catalog link but kept the brand still resolves).
function isSpeedtestApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'speedtest-tracker' || slug === 'speedtest') {
    return true;
  }
  const name = String(app.name || '').toLowerCase();
  return (name.indexOf('speedtest') !== -1);
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`.
export const extender = {
  slugs: ['speedtest-tracker', 'speedtest'],
  requiresApiKey: true,
  // Speedtest expanded card takes 2 columns so the chart +
  // averages have room without forcing the per-instance host
  // list narrower than its siblings on the same row.
  cardSpan(app) {
    return isSpeedtestApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the
// merge in `static/js/apps/_registry.js`. Names are prefixed
// `speedtest*` so they don't collide with other per-app
// modules' helpers.
export const helpers = {
  speedtestIsApp: isSpeedtestApp,
  speedtestMbpsLabel: fmtBits,
  speedtestPingLabel: fmtPing,
  speedtestPctLabel: fmtPct,
  speedtestProvenance: speedtestProvenance,
  speedtestSparkPath: sparkPath,
  speedtestChartModel: speedtestChartModel,
  speedtestTrendChartModel: speedtestTrendChartModel,
};
