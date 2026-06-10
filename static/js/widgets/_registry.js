// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Custom-dashboard widget module registry.
//
// The widget analogue of static/js/apps/_registry.js. Bundles every
// per-widget module (static/js/widgets/<kind>.js) into the merged
// helpers the generic Alpine component consumes, AND collects each
// module's `widget` record into `window.OG_WIDGETS` so the generic
// widget helpers (appsWidgetDecorationIcon / widgetSupportsRefresh /
// widgetHasData / widgetFreshnessLabel / refreshWidget /
// appsAvailableWidgetKinds, plus the appsWidgetKinds getter) dispatch
// per-kind behaviour without a hardcoded if/else ladder.
//
// Adding a new widget kind is two edits to this file: one import + one
// entry in `_modules` (its array position sets the picker order).
//
// `_modules` order IS the canonical widget-kind order (clock first).
//   `widgetsHelpers` — flat object of every module's render helpers,
//     merged into the Alpine component in app.js.
//   `window.OG_WIDGETS` — { byKind, kinds }: per-kind extender records
//     keyed by `kind`, plus the ordered kind list.

import * as clock from './clock.js?v=__APP_VERSION__';
import * as weather from './weather.js?v=__APP_VERSION__';
import * as moon from './moon.js?v=__APP_VERSION__';
import * as publicIp from './public_ip.js?v=__APP_VERSION__';
import * as systemStats from './system_stats.js?v=__APP_VERSION__';
import * as prayerTimes from './prayer_times.js?v=__APP_VERSION__';
import * as arrCalendar from './arr_calendar.js?v=__APP_VERSION__';

// Add new per-widget modules above (one import) and below (one entry in
// `_modules`) — the rest is fully generic. Array order = picker order.
const _modules = [clock, weather, moon, publicIp, systemStats, prayerTimes, arrCalendar];

function _moduleHelpers(m) {
  return m.helpers || {};
}

function _moduleWidget(m) {
  return m.widget;
}

function _isTruthy(value) {
  return Boolean(value);
}

const _allHelpers = _modules.map(_moduleHelpers);
export const widgetsHelpers = Object.assign({}, ..._allHelpers);

const _records = _modules.map(_moduleWidget).filter(_isTruthy);
const _byKind = {};
const _kinds = [];
_records.forEach((r) => {
  _byKind[r.kind] = r;
  _kinds.push(r.kind);
});

// Stamp on `window` so the generic widget helpers in app-apps.js /
// app-topbar.js can iterate without an import cycle through app.js.
// Idempotent — re-running this module replaces the maps.
window.OG_WIDGETS = {byKind: _byKind, kinds: _kinds};
