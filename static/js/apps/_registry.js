// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module registry.
//
// Bundles every per-app module (`static/js/apps/<slug>.js`) into
// the merged helpers + extenders the generic Alpine component
// consumes. Adding a new per-app module is two edits to this
// file: one import + one entry in each list.
//
// Why a registry (vs. auto-discovery)
//   ESM imports must be statically resolvable at parse time --
//   no `for (const f of fs.readdirSync(...))` shortcut. The
//   explicit map below keeps drift visible: a typo'd module
//   filename surfaces as a missing import error at boot, not a
//   silent "extender never fires".
//
// Lookup
//   `appsHelpers` -- flat object merged into the Alpine
//     component via `Object.assign`. Per-app methods (prefixed
//     by their app's slug, e.g. `speedtestMbpsLabel`) are
//     callable from the template / other helpers.
//   `appsExtenders` -- array consumed via
//     `window.OG_APPS_EXTENDERS`. Generic helpers
//     (`appsCardSpan`, `appsTemplateRequiresApiKey`, ...) walk
//     it to decide per-app variations.
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header
// for the full rationale -- same per-file JSHint + PyCharm
// noinspection conventions apply to every per-app module here.

import * as adguardHome from './adguardhome.js?v=__APP_VERSION__';
import * as adguardHomeSync from './adguardhome_sync.js?v=__APP_VERSION__';
import * as apc from './apc.js?v=__APP_VERSION__';
import * as bazarr from './bazarr.js?v=__APP_VERSION__';
import * as lidarr from './lidarr.js?v=__APP_VERSION__';
import * as pihole from './pihole.js?v=__APP_VERSION__';
import * as radarr from './radarr.js?v=__APP_VERSION__';
import * as seerr from './seerr.js?v=__APP_VERSION__';
import * as sonarr from './sonarr.js?v=__APP_VERSION__';
import * as speedtestTracker from './speedtest_tracker.js?v=__APP_VERSION__';

// Add new per-app modules above (one import) and below (one
// entry in `_modules`) -- the rest is fully generic.

const _modules = [adguardHome, adguardHomeSync, apc, bazarr, lidarr, pihole, radarr, seerr, sonarr, speedtestTracker];

// Named extractors -- keep `.map(extract).filter(predicate)`
// from firing PyCharm's anonymous-function / chained-call
// style lint. Both run once at module load (zero hot-path cost).
function _moduleHelpers(m) {
  return m.helpers || {};
}

function _moduleExtender(m) {
  return m.extender;
}

function _isTruthy(value) {
  return Boolean(value);
}

const _allHelpers = _modules.map(_moduleHelpers);
export const appsHelpers = Object.assign({}, ..._allHelpers);

const _allExtenders = _modules.map(_moduleExtender);
export const appsExtenders = _allExtenders.filter(_isTruthy);

// Stamp on `window` so the generic `static/js/app-apps.js`
// helpers can iterate without an import-cycle through
// `app.js`. Idempotent -- re-running this module (HMR /
// duplicate import) replaces the array.
window.OG_APPS_EXTENDERS = appsExtenders;
