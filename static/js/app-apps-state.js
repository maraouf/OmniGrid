// noinspection JSUnusedLocalSymbols,JSUnusedGlobalSymbols,JSValidateTypes,FunctionNamingConventionJS,JSVariableNamingConventionJS,BadName,BadVariableName
// noinspection ConstantOnRightSideOfComparisonJS,FunctionTooLongJS,OverlyLongMethodJS,OverlyComplexFunctionJS,RedundantConditionalExpressionJS
// noinspection ElementNotExported,UnusedCatchParameterJS,JSUnresolvedReference
/* global setTimeout */
/* jshint esversion: 11, browser: true, strict: implied, curly: false, eqeqeq: false, -W069 */
// Shared module-scope state for the app-apps* family of SPA modules
// (app-apps.js + app-apps-card.js + app-apps-drawer.js + app-apps-data.js
// + app-apps-instances.js). Split out so the per-flush memo caches +
// lazy-render queue + per-tile diagnostic dict have ONE owner across
// every sibling consumer — without this, each split file would have to
// redeclare its own copy, and Alpine reactivity / WeakMap identity
// would diverge between files (the orchestration file's `filteredApps`
// memo would be different from the card file's `appsVisibleInstances`
// memo even though they read the same source).
//
// Every export here is a plain JS value (object / WeakMap / fn). Module
// state, not Alpine state — Alpine reactivity NEVER wraps these
// (modules import them by reference, not via the Alpine proxy).
//
// Underscore-prefixed names match the existing project convention for
// module-internal helpers (PyCharm's default name regex is suppressed
// at the top of every app-apps* file via the noinspection block).

// ---- Per-instance uptime-sparkline memo ----------------------------
// Keyed on the instance's `status_history` array REFERENCE (stable
// across reactive flushes until /api/apps reloads), so the SVG points
// string + uptime % are built ONCE per series, not on every Alpine
// flush.
export const _appsSparkCache = new WeakMap();

// ---- Per-flush memo for hostAppsHealth -----------------------------
// The host row binds it ~3x per apps pill (count badge text + colour
// class + title). Cleared on the next microtask; hosts reconcile IN
// PLACE (stable h ref), so the per-flush clear is what keeps it
// correct when h.apps updates on the next poll.
export let _hostAppsHealthFlushCache = null;
export let _hostAppsHealthFlushScheduled = false;

export function _clearHostAppsHealthFlushCache() {
  _hostAppsHealthFlushCache = null;
  _hostAppsHealthFlushScheduled = false;
}

// Mutators for the `let`-bindings above. ESM `export let` re-exports
// a LIVE binding (importers see the current value), but importers
// CAN'T assign through the import (read-only). Sibling files therefore
// call these setters to mutate the binding.
export function _setHostAppsHealthFlushCache(v) {
  _hostAppsHealthFlushCache = v;
}

export function _setHostAppsHealthFlushScheduled(v) {
  _hostAppsHealthFlushScheduled = v;
}

// ---- Per-flush memo for filteredApps -------------------------------
// The Apps view's `<template x-for>` binds it AND `appsCounts` reads
// it; the helper re-allocates a NEW array + inner Object.assign on
// every read. Without the memo, Alpine re-evaluates it on EVERY
// reactive flush (host polls, ops polls, SSE events, mouse
// interactions), allocating GC pressure + re-running the search /
// filter every time.
export let _filteredAppsCache = null;
export let _filteredAppsCacheKey = null;
export let _filteredAppsFlushScheduled = false;

export function _clearFilteredAppsFlushCache() {
  _filteredAppsCache = null;
  _filteredAppsCacheKey = null;
  _filteredAppsFlushScheduled = false;
}

export function _setFilteredAppsCache(v) {
  _filteredAppsCache = v;
}

export function _setFilteredAppsCacheKey(v) {
  _filteredAppsCacheKey = v;
}

export function _setFilteredAppsFlushScheduled(v) {
  _filteredAppsFlushScheduled = v;
}

// ---- Per-flush memo for appsVisibleInstances -----------------------
// Called TWICE per app card per flush (once for the standard chip
// list, once for the per-app extras x-for added with the encapsulation
// architecture). Without memoization, the helper allocates a NEW slice
// on each read, doubling the per-card work. WeakMap keyed on the app
// reference so a card poll-reconcile invalidates cleanly.
export const _appsVisibleInstancesCache = new WeakMap();
export let _appsVisibleInstancesFlushScheduled = false;

export function _clearAppsVisibleInstancesFlushCache() {
  // WeakMap has no clear() in older browsers; rely on natural GC when
  // app objects are replaced by poll-reconcile. Reset the scheduler
  // flag so the next call re-queues a microtask.
  const hasClear = (typeof _appsVisibleInstancesCache.clear === 'function');
  if (hasClear) {
    _appsVisibleInstancesCache.clear();
  }
  _appsVisibleInstancesFlushScheduled = false;
}

export function _setAppsVisibleInstancesFlushScheduled(v) {
  _appsVisibleInstancesFlushScheduled = v;
}

// ---- Per-flush memo for the per-app-extras gate --------------------
// Wrapping the per-app extras `<template x-for>` in an outer
// `<template x-if="anyAppExtrasMatch(app)">` lets every non-matching
// app card SKIP the iteration entirely instead of rendering N empty
// wrapper divs. The match-set is small (~2 apps today: APC + Speedtest)
// so the per-flush cost is trivial; the memo just amortises the
// registry walk to once per app per flush.
export const _anyAppExtrasMatchCache = new WeakMap();

// ---- Per-tile lazy-render diagnostic + RAF-staggered queue ---------
// `_appsTileRenderLog` tracks per-tile render durations so the user
// can inspect in devtools which tile took how long to mount. Cleared on
// every fresh `loadAppsList(force=true)`. Keys are app.group_id, values
// are {first_seen_ms, mount_ms}. Read the object directly off the Alpine
// component in devtools to see per-tile timing.
export const _appsTileRenderLog = {};

// FIFO queue of group_ids whose body subtree should mount, one per
// setTimeout(0) tick (NOT requestAnimationFrame — rAF defers when
// Alpine's reactive flush blocks paint, which would stall the queue
// after the first tile). `_appsTileQueueProcessing` is the kick guard
// — `true` while the processor chain is in flight, `false` when the
// queue drains.
export const _appsTileQueue = [];
export let _appsTileQueueProcessing = false;

export function _setAppsTileQueueProcessing(v) {
  _appsTileQueueProcessing = v;
}
