// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
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
// schema to Mbps (flat Kbps / 1000, nested Ookla bytes/s * 8 / 1e6), so the
// value here is ALREADY Mbps. Always render Mbps with thousand separators
// (never Gbps) per the operator's request, up to 2 decimals.
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

// SVG path builder for one metric (download / upload / ping)
// over the cached series array. Empty / missing series returns
// "" so the path renders nothing. `width` / `height` are the
// SVG viewBox dimensions consumed by the matching template's
// `<svg viewBox="0 0 200 32">` in the extras partial -- change
// either side together if the chart card resizes.
function sparkPath(series, key) {
  if (!Array.isArray(series) || series.length < 2) {
    return '';
  }
  const coercer = _makeCoercer(key);
  const values = series.map(coercer).filter(_isFiniteNumber);
  if (values.length < 2) {
    return '';
  }
  const width = 200;
  const height = 32;
  const min = Math.min(...values);
  const max = Math.max(...values);
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
  speedtestSparkPath: sparkPath,
};
