// noinspection ALL
// noinspection NestedFunctionCallJS,MagicNumberJS,ConditionalExpressionJS,NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,AnonymousFunctionJS,ConstantOnRightSideOfComparisonJS,FunctionWithMoreThanThreeNegationsJS,RegExpAnonymousGroup
// noinspection OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS
// noinspection ChainedMethodCallJS,NestedConditionalExpressionJS,RedundantConditionalExpressionJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection JSForIIterationOverNonNumericKeyJS,NestedTemplateLiteralJS,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithInconsistentReturnsJS
// noinspection OverlyNestedFunctionJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,IfStatementWithoutBlockJS,NegatedIfStatementJS
// noinspection NegatedConditionalExpressionJS,JSNegatedConditionalExpression,OverlyComplexBooleanExpressionJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML
// noinspection JSAsyncFunctionMissingAwait,JSMissingAwait,JSUnfilteredForInLoop,OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS
// noinspection OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures,JSValidateTypes,JSPotentiallyInvalidUsageOfThis
// noinspection JSIgnoredPromiseFromCall,AnonymousCapturingGroupJS,AssignmentToFunctionParameterJS,JSIfStatementsCanBeSimplified,IfStatementSimplifyable,RedundantIfStatementJS
// noinspection RegExpRedundantEscape,JSDeprecatedSymbols,VoidExpressionJS,JSVoidExpression,RedundantLocalVariableJS,JSPossiblyAssignedToNullVariable
// noinspection JSObjectNullOrUndefined,JSReusedLocalVariable,RegExpRedundantNestingJS,RegExpUnnecessaryNonCapturingGroupJS,HtmlEmptyContent,HtmlEmptyTagsRecommendation,HtmlUnknownTag,OverlyComplexArithmeticExpressionJS,PointlessArithmeticExpressionJS
// noinspection JSPossibleNullableReference,JSNullableReference,JSPossibleNullOrUndefinedAccess,LocalVariableReusedJS,JSDuplicatedDeclaration,JSReusedLocal,UnnecessaryReturnStatementJS,UnnecessarySemicolonJS

/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode, BroadcastChannel, crypto */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, maxstatements: false, -W069, -W018, -W030, -W083, -E016, -W071 */


// Note: the JSHint-style `/* global Alpine, Swal, I18N, t */` directive
// that used to live here was removed when this file became an ES module
// — ESLint v9 under sourceType:"module" parses that directive and treats
// it as a redeclaration of the globals already configured in
// `eslint.config.js`, which is a hard error. The runtime globals
// themselves (`window.Alpine`, `window.Swal`, `window.I18N`, `window.t`)
// are still attached at the same load order; removing the directive
// doesn't change a single bound name at runtime.
// SPA top-level Alpine component factory.
//
// This file is now an ES module (loaded via `<script type="module">` in
// `static/index.html`). It imports the SPA's module-private constants
// and side-effect globals from sibling `app-*.js` files, then defines
// the giant `app()` factory at the bottom and exposes it on `window`
// so the markup-side `x-data="app()"` expression resolves.
//
// Cache-busting: the import URLs carry `?v=__APP_VERSION__` query
// strings; `main.py:serve_app_js_module` substitutes the marker at
// serve time with the current PATCH version so a deploy invalidates
// every browser-cached module URL in one shot.
//
// Constants and the AI-code copy global live in dedicated files for
// readability; method extraction from the `app()` body into per-feature
// modules is a follow-up — the current shape preserves every existing
// `this.X` cross-reference because every method still merges into one
// Alpine component at instantiation time.


import appUtils from './app-utils.js?v=__APP_VERSION__';
import appI18n from './app-i18n.js?v=__APP_VERSION__';
import appIconResolvers from './app-icon-resolvers.js?v=__APP_VERSION__';
import appKeyboard from './app-keyboard.js?v=__APP_VERSION__';
import appSse from './app-sse.js?v=__APP_VERSION__';
import appAsset from './app-asset.js?v=__APP_VERSION__';
import appTelegram from './app-telegram.js?v=__APP_VERSION__';
import appAuth from './app-auth.js?v=__APP_VERSION__';
import appNotificationsPopup from './app-notifications-popup.js?v=__APP_VERSION__';
import appSsh from './app-ssh.js?v=__APP_VERSION__';
import appBackups from './app-backups.js?v=__APP_VERSION__';
import appSchedules from './app-schedules.js?v=__APP_VERSION__';
import appUsersAdmin from './app-users-admin.js?v=__APP_VERSION__';
import appOidc from './app-oidc.js?v=__APP_VERSION__';
import appPortainer from './app-portainer.js?v=__APP_VERSION__';
import appHostGroups from './app-host-groups.js?v=__APP_VERSION__';
import appLogs from './app-logs.js?v=__APP_VERSION__';
import appAi from './app-ai.js?v=__APP_VERSION__';
import appAiAdmin from './app-ai-admin.js?v=__APP_VERSION__';
import appAiDispatch from './app-ai-dispatch.js?v=__APP_VERSION__';
import appAdmin from './app-admin.js?v=__APP_VERSION__';
// Multi-view helpers (Stacks selection + topology, command palette,
// hosts editor / filters / dirty tracking, drawer scroll-lock,
// keyboard a11y helpers). Originally overflowed out of app-sse-stream.js
// as `-b`; renamed for clarity since the contents are NOT SSE-specific.
import appViewsHelpers from './app-views-helpers.js?v=__APP_VERSION__';
// Host-drawer chart builders — SNMP load / memory / throughput
// + Dell hardware (temperature probes) + APC UPS load / battery
// + battery-temperature SVG line + max + has-history helpers.
// Originally overflowed out of app-drawer-bulk.js as `-b`; renamed
// because every method in here builds a chart series.
import appDrawerCharts from './app-drawer-charts.js?v=__APP_VERSION__';
import appHostsEditor from './app-hosts-editor.js?v=__APP_VERSION__';
import appCharts from './app-charts.js?v=__APP_VERSION__';
import appCommandPalette from './app-command-palette.js?v=__APP_VERSION__';
import appHostDrawer from './app-host-drawer.js?v=__APP_VERSION__';
import appTopbar from './app-topbar.js?v=__APP_VERSION__';
import appNotifyAdmin from './app-notify-admin.js?v=__APP_VERSION__';
import appHostsGrid from './app-hosts-grid.js?v=__APP_VERSION__';
import appProviders from './app-providers.js?v=__APP_VERSION__';
import appOps from './app-ops.js?v=__APP_VERSION__';
import appStats from './app-stats.js?v=__APP_VERSION__';
import appTuning from './app-tuning.js?v=__APP_VERSION__';
import appMinorTools from './app-minor-tools.js?v=__APP_VERSION__';
import appApps from './app-apps.js?v=__APP_VERSION__';
// Split out from app-apps.js when it crossed the 3000-line split-
// candidate threshold. Each sibling owns a cohesive domain:
//   app-apps-card.js      — per-card helpers (status pills + port
//                           pills + sparklines + card-span + per-card
//                           probe-all / open-all pills)
//   app-apps-drawer.js    — app + per-host drawer surfaces, in-drawer
//                           probe/test/restart/update/logs, per-host
//                           bulk probe-all fan-out
//   app-apps-data.js      — per-app per-instance data cache for the
//                           expanded-card extras + credential test
//                           dispatcher
//   app-apps-instances.js — Admin → Apps Templates + Instances tabs:
//                           catalog CRUD, instance editor, bulk
//                           select+delete, pin-to-host modal,
//                           discover wizard
// Shared module-scope state (per-flush memos + lazy-render queue +
// per-tile diagnostic dict) lives in `app-apps-state.js` so every
// sibling observes the same cache identity — declared once, imported
// by each consumer.
import appAppsCard from './app-apps-card.js?v=__APP_VERSION__';
import appAppsDrawer from './app-apps-drawer.js?v=__APP_VERSION__';
import appAppsData from './app-apps-data.js?v=__APP_VERSION__';
import appAppsInstances from './app-apps-instances.js?v=__APP_VERSION__';
// Per-app modules — each one lives under `static/js/apps/<slug>.js`
// and bundles its own helpers + extender record. The registry
// merges every per-app module's `helpers` into a flat object that
// merges into the Alpine component below; the registry's side-
// effect ALSO stamps `window.OG_APPS_EXTENDERS` so the generic
// helpers in `app-apps*.js` can iterate per-app extender records
// (slugs / requiresApiKey / cardSpan) without an import cycle.
import {appsHelpers as appsPerApp} from './apps/_registry.js?v=__APP_VERSION__';
import appSseStream from './app-sse-stream.js?v=__APP_VERSION__';
import appDrawerBulk from './app-drawer-bulk.js?v=__APP_VERSION__';
// Side-effect import — installs `window.__ogCopyAiCode` at module-load
// time so the AI markdown renderer's fenced-code-block copy buttons
// (rendered via `x-html` string concat, can't bind Alpine `@click`)
// keep working. No exports.
import './app-globals.js?v=__APP_VERSION__';

// Merge module exports into one target while PRESERVING property
// descriptors. `Object.assign` calls every getter at copy-time and
// stores the RESULT VALUE — which breaks Alpine reactivity on any
// `get filteredStacks() / get counts() / get filteredItems() / get
// sortedFiltered() / get passwordStrength() / get historyStackOptions() /
// get _LOAD_BUSY_MAX_MS()` declared in the modules or the inline
// state literal. Pre-fix the Stacks / Services / Nodes views showed
// the topbar count correctly (`stacks.length` is a direct read) but
// rendered ZERO rows because `filteredStacks` had been frozen to its
// empty-initial-state value at factory call time. `defineProperties`
// over `getOwnPropertyDescriptors` copies the getter descriptor as-is
// so Alpine's proxy sees a live computed property.
function _mergeKeepDescriptors(target, ...sources) {
  for (const source of sources) {
    if (!source) {
      continue;
    }
    Object.defineProperties(target, Object.getOwnPropertyDescriptors(source));
  }
  return target;
}

function app() {
  return _mergeKeepDescriptors({}, appMinorTools, appTuning, appStats, appOps, appProviders, appHostsGrid, appNotifyAdmin, appTopbar, appHostDrawer, appCommandPalette, appCharts, appHostsEditor, appAi, appAiAdmin, appAiDispatch, appAdmin, appLogs, appHostGroups, appPortainer, appOidc, appUsersAdmin, appSchedules, appBackups, appSsh, appNotificationsPopup, appAuth, appTelegram, appAsset, appSse, appKeyboard, appIconResolvers, appI18n, appUtils, appApps, appAppsCard, appAppsDrawer, appAppsData, appAppsInstances, appsPerApp, appSseStream, appViewsHelpers, appDrawerBulk, appDrawerCharts, {
    items: [], stacks: [], nodes: {}, nodesInfo: {},
    // Swarm agent unhealthy banner — populated by `loadStats` from
    // `/api/stats`'s `unhealthy_agents` field. Each entry is
    // `{host, fails, since_ts, task_cids}`. Banner renders at the
    // top of Stacks + Hosts views when the array is non-empty.
    unhealthyAgents: [],
    // In-flight flag for the unhealthy-agent banner's "Restart agent
    // service" button. Disables the button + flips the icon to a
    // spinner while the op runs. Cleared on success / failure toast.
    swarmAgentRestartBusy: false,
    version: '',
    // Version snapshot captured at first load. `watchVersion()` polls
    // /api/version every 60s and compares against this; mismatch means
    // CI just shipped a new build so we flip `newVersionAvailable` and
    // the topbar banner prompts the user to hard-reload. Prevents
    // operators from staring at stale UI after a deploy.
    bootVersion: '',
    newVersionAvailable: false,
    newVersionString: '',
    _versionTimer: null,
    // Real-time event stream. EventSource connection to /api/events;
    // when healthy, every polling loop in this component idles. When the
    // stream drops AND we haven't seen a heartbeat for ``_sseIdleThresholdMs``
    // ms, polling resumes as the fallback. Re-connect retries are handled
    // by EventSource itself (browser native); we only track the connection
    // state for the toolbar indicator and the polling-fallback gate.
    _sse: null,
    _sseConnected: false,
    _sseLastEventTs: 0,
    _sseIdleThresholdMs: 30000,
    // running tally of `:overflow` events received this session.
    // Renders a small amber chip alongside the SSE pill when > 0 so
    // operators can see "events have been dropped" without watching
    // the console. Reset to 0 only on full page reload (per-tab state).
    _sseDropped: 0,
    // Pill-flash signal. Reactive boolean that
    // toggles true at the START of each interval-mode poll and back
    // to false ~600ms later, giving the topbar pill a visible green
    // pulse on every tick. Lets operators see "the system is polling
    // right now" without watching the network tab. Independent of
    // SSE state — only fires under interval modes (Live uses push,
    // Off doesn't poll).
    _pollFlashing: false,
    _pollFlashTimer: null,
    _sseReconnects: 0,
    // Heartbeat window (server emits ``: keepalive`` every 25s). The
    // freshness watch ticks once per second to flip _sseConnected back
    // to false if no traffic arrives for the threshold above. Acts as
    // a belt-and-braces safety check on top of EventSource's onerror
    // (which doesn't always fire on silent half-open sockets).
    _sseFreshnessTimer: null,
    view: (['stacks', 'services', 'nodes', 'hosts', 'apps', 'history', 'settings', 'admin', 'stats'].includes(localStorage.getItem('view')) ? localStorage.getItem('view') : 'stacks'),
    search: '', statusFilter: '', healthFilter: '',
    sortField: 'name', sortDir: 'asc',
    selected: [],
    expanded: (() => {
      try {
        return JSON.parse(localStorage.getItem('expanded') || '[]');
      } catch (_) {
        return [];
      }
    })(),
    loading: false,
    // Background-refresh indicators. The /api/items + /api/hosts/list
    // endpoints serve cached / snapshot data instantly when warm and
    // kick a background gather → set `cache_refreshing: true` /
    // `hub_probing: true` on the response. The topbar refresh button
    // pulses + reads "Refreshing…" while these are true so operators
    // see the system is working even when the foreground call is done.
    cacheRefreshing: false,
    hubProbing: false,
    drawerItem: null,
    // Node drawer — separate from drawerItem. Opens from the Nodes view
    // when the operator clicks a node row. Shape: {name, aliasInput}.
    drawerNode: null,
    drawerNodeSaving: false,
    // Per-provider chip popover — workflow-compressor. Clicking a
    // provider chip on a host row opens a small panel anchored to the
    // chip with the most-recent probe result (timestamp + state +
    // error + latency + raw value). Data is composed from the row's
    // existing `provider_pause_state` map — no new API call. Shape:
    // `{host_id, provider_name, state}` (`state` is the chip's state
    // string at click time so the popover header chip matches what
    // the operator just clicked even if the row re-polls mid-view).
    // Null = popover closed; outside-click / Esc / second-click on
    // the same chip close it. See provider-chip-popover.html for the
    // rendered surface.
    chipPopoverOpen: null,
    // Chip-strip vocabulary legend — density-as-product feature. A
    // small `?` button at the end of each chip strip toggles this
    // state to open / close a 5-row legend popover documenting
    // what each chip colour means (ok / failing / paused /
    // configured_inactive / loading). Tracks the host id whose `?`
    // is open (null = no legend open) so the popover scopes to
    // that one row — earlier shape used a global boolean which
    // caused every chip strip in the view to render its legend
    // simultaneously when any `?` was clicked. Esc + outside-click
    // dismiss. See chip-strip-legend.html for the rendered surface.
    chipLegendOpen: null,
    // Apps feature state — top-level Apps view + Admin → Apps tab.
    // `appsList` is the cross-host aggregate (one row per app, with
    // every host instance nested inside). `appsCatalog` is the catalog
    // template library. `appsInstances` is the flat per-instance view
    // used by Admin → Apps "Instances" sub-tab. Loaded flags drive the
    // skeleton-vs-empty-state ladder.
    appsList: [],
    appsListLoaded: false,
    appsListLoading: false,
    appsListError: '',
    // Per-app (group_id) "show all hosts" toggle for the Apps-view card —
    // an app on many hosts caps its instance list (see appsVisibleInstances)
    // so the card doesn't grow tall and disrupt the grid.
    appsInstancesExpanded: {},
    // Per-tile lazy-render visibility tracker. Drives the per-card
    // IntersectionObserver gate -- each `<article class="apps-card">`
    // renders ONLY its lightweight header (icon + name + status pill +
    // action pills) immediately; the heavy body (instance list, port
    // pills, sparklines, per-app extras like Speedtest data fetches)
    // is gated on `appsCardVisible(app.group_id)` returning true.
    // Observer (lazy-created in `_observeAppCard`) flips the entry to
    // true on first intersection so the heavy render happens only for
    // tiles scrolled into view. Plain object (not Set) so Alpine's
    // per-key reactivity triggers exactly one re-render when a tile
    // becomes visible. Cleared on `loadAppsList(force=true)` so a
    // catalog edit re-paints the visible set.
    _appsVisibleTiles: {},
    // Second-stage gate: a tile flips `_appsReadyTiles[gid] = true`
    // when the RAF-paced render queue actually picks it. Between
    // `_appsVisibleTiles[gid]` and `_appsReadyTiles[gid]` flipping,
    // the card renders the shimmer-skeleton placeholder (see
    // `_components/apps-card.html` skeleton block). Plain object so
    // Alpine's per-key reactivity fires exactly one re-render per
    // tile transition. Reset by `loadAppsList(force=true)` alongside
    // `_appsVisibleTiles`.
    _appsReadyTiles: {},
    _appsCardObserver: null,
    appsCatalog: [],
    appsCatalogLoaded: false,
    appsCatalogStatus: '',
    appsCatalogReseeding: false,
    appsCatalogImporting: false,
    // App-drawer Docker-link Logs modal — tails a linked container's
    // logs via the /api/container/{raw_id}/logs proxy.
    appLogModal: {open: false, loading: false, error: '', text: '', title: '', raw_id: '', node: '', tail: 200},
    appsInstances: [],
    appsInstancesLoaded: false,
    appsInstancesStatus: '',
    // Bulk-delete selection for the Admin → Apps → Instances table —
    // keyed by `host_id:service_idx`. Plain object (not a Set) so Alpine
    // tracks per-key reactivity for the row checkboxes + bulk bar.
    appsInstancesSelected: {},
    appsInstancesBulkDeleting: false,
    // Bulk-delete selection for the Admin → Apps → Templates table —
    // keyed by catalog id (template delete is by id, no index-shift).
    appsCatalogSelected: {},
    appsCatalogBulkDeleting: false,
    // Free-text filter for the Admin → Apps → Templates table (name /
    // slug / description / port). Client-side over appsCatalog.
    appsCatalogSearch: '',
    // Free-text filter for the Admin → Apps → Instances table (name /
    // catalog / host / port). Client-side over appsInstances.
    appsInstancesSearch: '',
    // Searchable Link-to-Docker combobox (instance editor): filter text +
    // dropdown-open flag.
    appsDockerLinkSearch: '',
    appsDockerLinkDropdownOpen: false,
    appsDockerLinkActiveIdx: -1,
    // Custom Apps-layout edit/lock toggle. Transient (default LOCKED each
    // visit) — the layout itself persists in ui_prefs; this flag just
    // gates the condensed-draggable editor vs the locked big-card view.
    appsCustomEditMode: false,
    // True WHILE an edit-mode resize gesture is in progress (the edge
    // handle / corner size button is being dragged). Declared here so
    // the cell's `:draggable` + `@dragstart` bindings can read it without
    // a ReferenceError — appsSizeControl sets it on pointerdown and the
    // cell @dragstart preventDefaults the reorder-move while it's true,
    // so dragging the resize handle resizes instead of starting a move.
    _appsResizing: false,
    // Inline add-bookmark form (edit mode) — set to a section id to open
    // its name/url form; cleared on submit/cancel. Transient.
    appsBookmarkOpenFor: '',
    appsBookmarkName: '',
    appsBookmarkUrl: '',
    // Optional icon hint — accepts EITHER a full URL (rendered
    // verbatim) OR a slug from the existing iconUrlFor / KNOWN_ICONS
    // resolver chain (e.g. "github" / "plex" / "adguard"). When blank,
    // the bookmark tile falls back to the default initial-letter
    // render. Tile decides which path to take via
    // `appsBookmarkIconResolved(icon)`.
    appsBookmarkIcon: '',
    appsAdminTab: 'templates',   // 'templates' | 'instances'
    appsCatalogEditOpen: false,
    appsCatalogEdit: {},
    appsCatalogSaving: false,
    appsCatalogEditError: '',
    // Open-time snapshot signature for template-editor dirty tracking —
    // see `appsCatalogDirty()` / `_appsCatalogFormSig()`.
    _appsCatalogEditSnapshot: '',
    // Per-bookmark-uid icon-load-failure flags. A bookmark's logo <img>
    // sets its uid true on @error (shows the fallback link glyph) and
    // false on @load (so an icon edit that now resolves re-shows the
    // brand mark). Keyed by item.uid.
    _bookmarkIconBroken: {},
    // Pin-to-host modal — operator selects a curated host + optional
    // overrides, then POSTs to /api/services/catalog/{cid}/pin which
    // appends a new chip to the host's services[] array pre-filled
    // from the template's default_ports + icon + name.
    appsPinModalOpen: false,
    appsPinForm: {template: null, host_id: '', url: '', probe_enabled: true},
    appsPinSaving: false,
    appsPinError: '',
    // Searchable host picker for the Pin-to-host modal — mirrors the
    // discovery wizard's picker: `appsPinHostSearch` is the live filter
    // text, `appsPinHostDropdownOpen` gates the match list, and
    // `appsPinHostActiveIdx` is the keyboard-highlight index into
    // appsPinFilteredHosts() (−1 = none). The selected host id lives on
    // appsPinForm.host_id — these three are display-only state.
    appsPinHostSearch: '',
    appsPinHostDropdownOpen: false,
    appsPinHostActiveIdx: -1,
    // Discovery wizard — host picker + proposal list. `appsDiscoverResult`
    // is the most-recent /api/services/discover/{host_id} response;
    // `appsDiscoverSelected` is a Set of catalog_ids the operator checked
    // for bulk-apply via /api/services/discover/{host_id}/apply.
    appsDiscoverOpen: false,
    appsDiscoverForm: {host_id: ''},
    appsDiscoverLoading: false,
    appsDiscoverError: '',
    appsDiscoverResult: {detected_ports: [], proposals: [], scanned_at: 0},
    appsDiscoverSelected: new Set(),
    appsDiscoverApplying: false,
    appsDiscoverApplyError: '',
    // Searchable host picker for the discovery wizard. `appsDiscoverHostSearch`
    // is the live filter text; `appsDiscoverHostDropdownOpen` gates the match
    // list; `appsDiscoverHostActiveIdx` is the keyboard-highlight index into
    // appsDiscoverFilteredHosts() (−1 = none). The selected host id still
    // lives on appsDiscoverForm.host_id — these three are display-only state.
    appsDiscoverHostSearch: '',
    appsDiscoverHostDropdownOpen: false,
    appsDiscoverHostActiveIdx: -1,
    appsSearchQuery: '',
    appsStatusFilter: '',  // '' | 'up' | 'down' | 'degraded' | 'unknown'
    // Per-chip "Probe now" in-flight tracker. Keyed by
    // `'probe:' + host_id + ':' + service_idx` so simultaneous clicks
    // on different chips don't share state; the matching button binds
    // :disabled + spinner class to this map.
    probeNowInFlight: {},
    // Per-host "Probe all" batch in-flight flag (host_drawer Apps card header).
    _hostAppsProbingAll: {},
    // Apps-VIEW per-app "Probe all" batch in-flight flag, keyed by the app's
    // group_id (one app card = one catalog template, N instances across hosts).
    _appsProbingAll: {},
    // Transient per-app probe-all summary line ("4/5 up") rendered in the
    // Apps-view card header right after a fan-out completes; keyed by group_id.
    _appsProbeAllSummary: {},
    // Per-(host, service_idx) probe history cache, populated lazily by
    // `loadAppHistory` when the App Drawer opens. Keyed by
    // `host_id + ':' + service_idx`; value is `{samples, hours, loadedAt}`.
    appsHistory: {},

    // Hosts view state (Beszel-backed). Refreshed via /api/hosts on a
    // separate cadence from the item cache — hub calls are cheap and
    // the view wants faster feedback than the 15-30s item refresh.
    hosts: [],
    hostsError: '',
    hostsProviderErrors: {},  // {beszel: "hub 500", pulse: "...", ...}
    hostsActiveSources: [],   // list of "beszel" / "pulse" / "node_exporter"
    hostsConfigured: true,
    hostsLoading: false,
    // True only AFTER the first loadHosts() response has landed. The
    // "no host data" empty-state ladder gates on this so a fresh page
    // load (where hosts=[] / hostsCuratedCount=0 / hostsLoading=false
    // are all true initial values) doesn't flash "No host data to show"
    // before the first /api/hosts/list call resolves. Skeleton renders
    // until this flips, then the real empty states take over.
    hostsInitialLoaded: false,
    // IntersectionObserver-driven lazy fetch for /api/hosts/one/{id}.
    // For fleets of 100+ hosts the historical "fan out every row at
    // page load" pattern burned backend probes + sockets for rows
    // the operator never scrolls to. The observer (lazy-created in
    // `_observeHostRow`) watches every `data-host-id="..."` row and
    // adds the id to `_hostSeenIds` on first intersection — also
    // triggering an immediate `refreshHostRow` for that one id.
    // Subsequent 15s polls (`loadHosts`) re-fetch every host whose id
    // is in `_hostSeenIds`, so once an operator has scrolled past a
    // row it stays fresh. Off-screen rows that have NEVER been seen
    // pay zero probe cost. Set is non-reactive (vanilla Set, not
    // Alpine-tracked) — Alpine doesn't need to render anything from
    // it, and avoiding reactivity prevents the in-loop `add()` from
    // triggering a fan-out re-render.
    _hostSeenIds: new Set(),
    _hostRowObserver: null,
    // Persisted across browser refresh — mirrors the 'expanded' state
    // that the Stacks view already stores. Parsing tolerates stale /
    // invalid JSON so a corrupt entry doesn't break the whole view.
    hostsExpanded: (() => {
      try {
        const raw = typeof localStorage !== 'undefined' && localStorage.getItem('hostsExpanded');
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.filter(x => typeof x === 'string') : [];
      } catch {
        return [];
      }
    })(),
    // Open/closed state for the per-host health-score breakdown
    // popover inside the drawer. Default closed; the chip in the
    // drawer header toggles it. Resets to false on closeHostDrawer
    // so reopening a drawer always lands collapsed.
    healthPopoverOpen: false,
    // ---- Hosts bulk-selection state ---------------------------------
    // Reactive Set of host ids the operator has selected via the row
    // checkbox. Drives the sticky bottom action bar visibility +
    // count badge. Set so membership checks are O(1) and the reactive
    // get/set proxy still sees mutation. Cleared on view change AND
    // when the operator clicks "Clear" on the bar.
    selectedHosts: new Set(),
    // Bulk-action modals — pure UI state. Each opens a SweetAlert-style
    // dialog so the operator can review the change before sending.
    bulkSnmpVendorsModal: {open: false, vendors: [], mode: 'set'},
    bulkSnmpTunablesModal: {open: false, walk_concurrency: '', wall_clock_budget: '', clear: false},
    // Progressive UI feedback after a bulk action lands. Set to
    // {applied, total, action, ts} for ~5 s after the response, then
    // cleared by `_bulkAppliedTimer`. Each affected host row also
    // gets a transient `_bulkApplied: true` flag drained at the
    // same time. Drives the small "✓ N/M applied" badge above the
    // Hosts toolbar AND the per-row check glyph next to the
    // hostname so the operator gets a per-host confirmation as
    // each id settles.
    bulkAppliedSummary: null,
    _bulkAppliedTimer: null,
    // ---- Host timeline state ----------------------------------------
    // Per-host timeline cache. Shape: hostTimeline[host_id] = {events,
    // counts, loading, error, loadedAt, hours}. The drawer's Timeline
    // card consumes this directly; reactive so the chevron rotates +
    // empty/loading/error states render off the same map.
    hostTimeline: {},
    // Per-host triage state — populated by loadHostTriage(). Same
    // shape and lifetime as hostTimeline. Drives the "Similar
    // Incidents" panel below the Timeline card.
    hostTriage: {},
    // Per-host (group_index) expand state for the Similar Incidents
    // panel. Two-level key: { [hostId]: { [groupIdx]: bool } }.
    triageExpanded: {},
    // Per-host expand state — Timeline card is collapsed by default
    // so the drawer's first paint stays light. Persisted only in
    // memory (not localStorage) — fresh open should always start
    // collapsed.
    timelineExpanded: {},
    // Per-host range picker state (24h / 7d / 30d). Default 7d.
    hostTimelineRange: {},
    hostsSearch: '',
    // clickable provider filter on the Hosts toolbar. Set of
    // active filters (provider names + 'none' for "no provider mapped")
    // — empty Set means show everything. Multi-select with OR semantics:
    // a host matches when it carries ANY of the selected providers (or
    // matches the 'none' synthetic when no provider is set on the row).
    // Persists to sessionStorage so a tab reload preserves the filter
    // but a fresh tab starts clean.
    hostsProviderFilter: new Set(
      (typeof sessionStorage !== 'undefined'
        && (sessionStorage.getItem('hostsProviderFilter') || '').split(','))
        .filter(Boolean)
    ),
    // Problem-hosts filter. When TRUE, the Hosts list shows ONLY rows
    // whose status ∈ {down, paused, unknown} so operators triaging
    // incidents see actionable rows without scrolling through 100+ up
    // hosts. Same status taxonomy as the Telegram AI context's
    // `problem_hosts` block (`logic/telegram_listener.py:_classify`)
    // for symmetry. Persists to sessionStorage so a tab reload keeps
    // the filter; a fresh tab starts clean. The toolbar chip + Stats
    // dashboard "Problem hosts" tile both flip this flag.
    hostsProblemFilter: (typeof sessionStorage !== 'undefined')
      && sessionStorage.getItem('hostsProblemFilter') === '1',
    // Sort key for the Hosts view. Persisted to localStorage so the
    // operator's preferred order sticks across reloads. Supported keys:
    // 'status' (default — alive first, then paused, down, unknown)
    // 'seq'    (curated-list addition order; 'insertion' alias)
    // 'name'   (alphabetical on label or host)
    // 'type'   (platform / OS group — same-kind hosts cluster)
    // 'cpu' / 'mem' / 'disk' (descending — hottest first)
    // 'uptime' (descending — longest running first)
    hostsSort: (typeof localStorage !== 'undefined' && localStorage.getItem('hostsSort')) || 'status',
    // Hide hosts that have NO agent fields configured (no beszel_name,
    // no pulse_name, no ne_url, no webmin_name). Persisted across reloads.
    // Useful when the curated list contains inventory-only entries (FTTH /
    // 5G routers, switches without an exporter) and the operator wants
    // to focus on rows that actually probe.
    hostsHideUnconfigured: (typeof localStorage !== 'undefined' && localStorage.getItem('hostsHideUnconfigured') === '1') || false,
    // Per-host debug payloads (admin-only). Keyed by host.id. Lazily
    // populated when the operator opens the drawer's Debug panel so
    // the normal hosts-view cadence stays cheap.
    hostsDebug: {},
    hostsDebugLoading: {},   // {host_id: true} while the fetch is in flight
    // Per-host Beszel service detail cache. Populated by
    // `loadHostBeszelServices(host_id)` which hits
    // `/api/hosts/{id}/beszel/services` and stores the per-unit list
    // ({name, state, sub_state, last_seen_ts, last_change_ts}). Used
    // by the AI palette context (fmtHost surfaces `services_detail`
    // so the AI can answer "what's the state of nginx on web01?")
    // AND by the host-drawer per-service detail pane.
    hostsBeszelServices: {},
    hostsBeszelServicesLoading: {},
    // Per-host open / closed state for the drawer's per-unit Beszel
    // services detail pane. Toggled by `toggleHostBeszelServices`;
    // first open lazy-loads the data.
    hostBeszelServicesOpen: {},
    hostsDebugOpen: {},      // {host_id: true} = panel is expanded
    // Per-subject debug payloads for the Stacks / Services / Nodes
    // drawers — admin-only, lazily fetched when the operator opens the
    // drawer's Debug panel. Keyed by `kind:id` (e.g. "item:abc12345"
    // or "node:web01.example.com") so item-id-vs-node-id collisions
    // can't happen.
    subjectsDebug: {},        // {key: payload}
    subjectsDebugLoading: {}, // {key: true} while fetch is in flight
    subjectsDebugOpen: {},    // {key: true} = panel is expanded
    // Per-host expand/collapse state for high-count Server health
    // sub-sections (Physical disks / Voltages). Default-collapsed when
    // the section's row count exceeds the dense-layout threshold (12);
    // operator clicks "Show all (N)" to expand.
    serverHealthExpanded: {},
    hostsCuratedCount: 0,
    hostsEnabledCount: 0,
    _hostsTimer: null,
    // Per-field validation errors for inline rendering. Keys
    // follow the pattern "<scope>_<idx>_<field>" (e.g.
    // "host_3_webmin_url", "group_0_range"). `setFieldError` /
    // `clearFieldError` / `hasFieldError` / `fieldError` wrap the map
    // so callers don't have to know the exact storage shape.
    fieldErrors: {},
    hostsDiscovering: false,
    // Settings → Profile → Formats. Single text input — the user
    // types one datetime format string (e.g. `dd/MM/yyyy HH:mm:ss`,
    // `yyyy-MM-dd HH:mm`, `MMM d, yyyy h:mm a`). The SPA's
    // `_applyDateTimeFormat` token parser turns it into rendered
    // strings; `fmtDateOnly` and `fmtDateTimeShort` derive their own
    // shorter formats by stripping time / seconds from the user's
    // value. Persisted to `ui_prefs.datetime_format`. Empty / blank
    // value = inherit the SPA default `DEFAULT_DATETIME_FORMAT`. The
    // draft is what the input binds to via x-model; Save commits.
    datetimeFormatDraft: '',
    toast: '', toastType: 'success', _tt: null,
    // Auto-refresh cadence (seconds; 0 = off). Persisted to
    // localStorage so the operator's chosen cadence survives a
    // browser refresh — previously it reset to 0 on every reload.
    //
    // `refreshInterval` is now the canonical operator-set
    // cadence; legacy `autoRefresh` (items poll) and `statsInterval`
    // (stats poll) are mirrored to it on every change so the existing
    // pollers don't need to be rewired. Migration: prefer existing
    // `refreshInterval`, fall back to `statsInterval` (more granular),
    // then `autoRefresh`. New state of the world is "Live (SSE) OR
    // one cadence drives every poll uniformly".
    refreshInterval: (() => {
      // -1 = Live (SSE-driven); 0 = Off; 30/60/300 = polling cadence.
      // Default to Live for fresh installs — the SPA's whole reason
      // for SSE is that operators don't have to think about cadence.
      try {
        const k = localStorage.getItem('refreshInterval');
        if (k != null) {
          const n = parseInt(k, 10);
          if ([-1, 0, 30, 60, 300].includes(n)) {
            return n;
          }
        }
        const s = parseInt(localStorage.getItem('statsInterval') || '', 10);
        if ([0, 30, 60, 300].includes(s)) {
          return s;
        }
        const a = parseInt(localStorage.getItem('autoRefresh') || '', 10);
        if (Number.isFinite(a) && a >= 0) {
          return [0, 30, 60, 300].includes(a) ? a : 60;
        }
        return -1;
      } catch {
        return -1;
      }
    })(),
    autoRefresh: (() => {
      try {
        const n = parseInt(localStorage.getItem('autoRefresh') || '0', 10);
        return Number.isFinite(n) && n >= 0 ? n : 0;
      } catch {
        return 0;
      }
    })(),
    _autoTimer: null, _opsTimer: null,
    cacheLabel: '',
    settings: {
      apprise_url: '', apprise_tag: '', swarm_autoheal_action: 'notify', swarm_autoheal_bootstrap_enabled: true, portainer_public_url: '', debug_panel_enabled: true,
      // TOTP / 2FA policy defaults so the Admin -> Config inputs
      // bind cleanly before the first /api/settings response.
      totp_allowed: true, totp_required_for_admins: false, totp_required_for_users: false,
      totp_lockout_max_failures: 5, totp_lockout_minutes: 15,
      // Passkey master toggle default — same `true` as the backend's
      // `_TOTP_POLICY_DEFAULTS["passkeys_allowed"]` so the form
      // renders the box checked before the first /api/settings hit.
      passkeys_allowed: true,
      // AI integration defaults so the AI tab's form renders
      // sensibly before the first /api/settings response.
      ai_enabled: false, ai_active_provider: 'claude', ai_max_tokens: 1024
    },
    openMeteoSaving: false,
    // Per-host DISKS card collapse state for the "Show N empty" toggle.
    // Keyed by host.host. In-memory only — zero-usage mounts rarely
    // flip across browser sessions so persistence isn't worth the
    // localStorage overhead.
    hostDisksShowEmpty: {},
    // Baseline snapshot of the host-stats settings at the last
    // successful load or save. Compared against the live form to
    // derive a "dirty" boolean for the Save button's visual
    // treatment. Captured in loadSettings + after successful
    // saveHostStats so the indicator clears correctly.
    _hostStatsBaseline: '',
    // User-menu dropdown (top-right avatar button)
    userMenuOpen: false,
    // Split-Save baselines + per-button result chips. The page has
    // TWO independent Save buttons: providers (parent section) and
    // per-event (sibling section). Each has its own dirty snapshot
    // + own save handler + own success chip so functionality stays
    // separated — saving providers won't show a per-event success
    // message and vice versa.
    _providersBaseline: '',
    _perEventBaseline: '',
    providersSaveResult: null,
    perEventSaveResult: null,
    // Ping test widget state. `pingTestHostId` is the curated
    // host_id picked from the dropdown of opted-in hosts; `pingTestResult`
    // mirrors the shape of the others (pending / ok / detail).
    pingTestHostId: '',
    // / SNMP test widget state. UX unified with the Ping
    // test — the picker shows curated hosts that have an
    // `snmp_name` mapped, mirroring `pingTestHostId` instead of a
    // free-text host input. `testSnmpConnection` resolves the row's
    // SNMP target + overrides client-side and submits them to the
    // existing `/api/snmp/test` endpoint, so no backend change was
    // needed for the unification.
    snmpTestHostId: '',
    // Last successful Test-connection timestamp per provider, keyed
    // by short name (`portainer` / `oidc` / `beszel` / `pulse` /
    // `webmin` / `snmp` / `ping` / `asset_inventory`). Hydrated from
    // the DB-backed `settings.last_test_success` block on every
    // /api/settings load, written by the backend at the END of every
    // successful test endpoint. epoch seconds; nullable. Server-side
    // authoritative so every operator + browser sees the same value.
    _lastTestSuccess: {},
    // Tick once a minute so the relative-time labels next to every
    // Test button refresh without reloading. Bound at init() time.
    _lastTestSuccessNow: Math.floor(Date.now() / 1000),
    // The URL typed into the "Test one Webmin URL" scratch field.
    // Persisted to localStorage so operators don't have to retype it
    // every time they reload the Providers tab to re-test after a config
    // change. Per-browser (each device keeps its own last-tested URL).
    webminTestUrl: (typeof localStorage !== 'undefined' && localStorage.getItem('webminTestUrl')) || '',
    _openMeteoBaseline: '',
    _debugBaseline: '',
    // Settings / Admin sidebar layout. Arrays drive the nav — adding a
    // section is one entry here + one <section> in the markup.
    // Section `label` is kept as a fallback (in case the translation key
    // is missing); the sidebar button actually renders via t('settings.sections.<id>').
    // Personal settings — profile, ignore list, language, hotkeys.
    // Admin-only concerns (Portainer / OIDC / Notifications / Host
    // stats) moved under the Admin section since they're global and
    // only admins can change them.
    settingsSections: [
      {id: 'profile', label: 'Profile', icon: 'user'},
      {id: 'notifications', label: 'Notifications', icon: 'bell'},
      {id: 'ignores', label: 'Ignore list', icon: 'trash'},
      {id: 'language', label: 'Language', icon: 'info'},
      {id: 'security', label: 'Security', icon: 'shield'},
      {id: 'shortcuts', label: 'Keyboard shortcuts', icon: 'help-circle'},
    ],
    settingsSection: (function () {
      // Gracefully migrate users whose localStorage still points at the
      // removed "authentik" (Forward Auth) section — land them on the
      // new OIDC panel instead of staring at an empty page.
      const v = localStorage.getItem('settingsSection') || 'profile';
      return v === 'authentik' ? 'oidc' : v;
    })(),
    // Profile form state — mirrors the `me` snapshot but held separately
    // so the user can edit without losing unsaved changes across refetches.
    profileForm: {display_name: '', bio: '', email: '', notify_events: {}},
    // Baseline snapshot string of the profile form, captured by
    // syncProfileForm() and refreshed by saveProfile(). Drives
    // profileDirty() so reverting an edit clears the amber ring.
    _profileBaseline: '',
    profileBusy: false,
    avatarBusy: false,
    // Admin sidebar sections — order is operator-visible, group with
    // separators between major scopes (identity & user-management →
    // integrations & data sources → operations & diagnostics). Items
    // with `separator: true` render as a `<hr>` divider in the sidebar
    // and a disabled `<option>` in the mobile <select>. Each
    // separator carries a stable `id` (`_sep_<N>`) for the x-for `:key`
    // — never collides with a real section id because real sections
    // never start with `_`.
    adminSections: [
      {id: 'users', label: 'Users', icon: 'users'},
      {id: 'authentication', label: 'Authentication', icon: 'shield'},
      {id: 'oidc', label: 'Authentik OIDC', icon: 'authentik'},
      {id: 'sessions', label: 'Sessions', icon: 'monitor'},
      {id: 'tokens', label: 'API tokens', icon: 'key'},
      {id: 'notifications', label: 'Notifications', icon: 'bell'},
      {id: '_sep_1', separator: true},
      {id: 'portainer', label: 'Portainer', icon: 'portainer'},
      {id: 'providers', label: 'Providers', icon: 'activity'},
      {id: 'ssh', label: 'SSH', icon: 'terminal'},
      {id: 'port_scan', label: 'Port Scan', icon: 'search'},
      {id: 'public_ip', label: 'Public IP', icon: 'globe'},
      {id: 'weather', label: 'Weather', icon: 'cloud'},
      {id: 'host_groups', label: 'Host Groups', icon: 'layers'},
      {id: 'hosts', label: 'Hosts', icon: 'server'},
      {id: 'apps', label: 'Apps', icon: 'grid'},
      {id: 'assets', label: 'Asset inventory', icon: 'package'},
      {id: 'ai', label: 'AI integration', icon: 'zap'},
      {id: '_sep_2', separator: true},
      {id: 'schedules', label: 'Schedules', icon: 'calendar'},
      {id: 'backups', label: 'Backup', icon: 'archive'},
      {id: 'config', label: 'Config', icon: 'settings'},
      {id: 'config_backup', label: 'Config Backup', icon: 'save'},
      {id: 'logs', label: 'Logs', icon: 'file-text'},
      {id: 'debug', label: 'Debug', icon: 'bug'},
    ],
    // Multi-tab activity registry — { client_id: { actor, view, drawer_host,
    // admin_tab, settings_section, stats_tab, title, ts } }. Hydrated at SPA
    // boot from `GET /api/tabs/activity`; updated live via SSE
    // `tab:activity` / `tab:closed` events. Excludes the calling tab via
    // backend self-filter so the topbar widget doesn't list us. Drives the
    // topbar pill + popover for multi-tab activity tracking.
    tabActivity: {},
    // Heartbeat in-flight gate so a slow network doesn't stack heartbeats.
    _tabHeartbeatBusy: false,
    // Last-published heartbeat snapshot — heartbeat short-circuits when
    // nothing changed since last post + < 30s elapsed.
    _tabHeartbeatLast: {ts: 0, signature: ''},
    // BroadcastChannel for cross-tab focus. `null` when the browser
    // doesn't support it (Safari < 15.4 / older Firefox).
    _tabFocusChannel: null,
    logSinceTs: 0,
    logAuto: true,
    logFilter: '',
    // Severity multi-select filter. Defaults: all four levels
    // visible. Persists to localStorage so reload preserves the view.
    // Severity values match the strings `logSeverity()` returns.
    logSeverityLevels: ['error', 'warn', 'ok', 'info'],
    logSeverityFilter: {error: true, warn: true, ok: true, info: true},
    // Log-pattern chip filter — third axis alongside source-tag +
    // severity. Auto-derived chips that match cross-cutting patterns
    // operators routinely triage on: auth-cool-down skips, probe
    // timeouts, SQL drift warnings, provider auto-pause events,
    // SSE / WS reconnects, sampler skips, CSRF / CORS rejections.
    // Each chip is a `{id, label_key, desc_key, regex_str}` shape;
    // the chip's RegExp is compiled per-call (cheap; small chip set).
    // Multi-select semantics: ANY-of-N selected → show only lines
    // matching at least one selected pattern. ALL OFF → no pattern
    // filter applied (the source-tag + severity filters still gate).
    // Persisted to localStorage so the view survives a reload.
    logPatternDefs: [
      // Auth-cool-down — `logic/cooldown.py` consumers (Webmin / SSH)
      // emit "skipped (cool-down)" lines when a per-host 401 cool-down
      // is active. Operators triaging "why isn't this host probing?"
      // pin this chip to see every cool-down skip across all sources.
      {id: 'auth_cooldown', regex_str: '(skipped \\(cool-?down\\)|cool-?down active|401.*back\\s*off)'},
      // Probe timeout — every per-host provider probe times out under
      // its outer wall-clock (15-30s typically). Pinning this chip
      // shows every provider hitting its outer cap so operators can
      // spot a network-segment outage at a glance.
      {id: 'probe_timeout', regex_str: '(timed?\\s*out|timeout\\s*(?:exceeded|reached)|probe\\s*budget|wall-?clock\\s*cap)'},
      // SQL drift — additive-ALTER warnings + schema-migrations
      // mentions. Useful during a release upgrade where a new column
      // landed and a stale SELECT might still be in flight.
      {id: 'sql_drift', regex_str: '(SQL\\s*drift|additive\\s*ALTER|schema_migrations|no\\s*such\\s*column)'},
      // Provider auto-pause — `_record_failure` / `_clear_failure`
      // events. Pinning this chip shows the full timeline of a host's
      // pause / resume cycle without scrolling raw log.
      {id: 'provider_paused', regex_str: '(auto-?paused|provider\\s*(?:paused|resumed)|consecutive\\s*failures)'},
      // SSE / WS disconnect — operator-visible "stream dropped"
      // events. Useful when the SPA Live pill flickers and the
      // operator wants to confirm it's a server-side disconnect vs
      // a local-network blip.
      {id: 'ws_disconnect', regex_str: '(SSE.*(?:reconnect|drop|close)|WebSocket.*(?:close|disconnect)|stream\\s*(?:closed|stalled))'},
      // Sampler skip — any sampler tick that deliberately deferred
      // (cool-down, paused host, missing config, etc.). Companion
      // chip to the per-pattern ones above for the "why isn't N
      // happening?" triage workflow.
      {id: 'sampler_skip', regex_str: '(sampler.*(?:skip|defer|noop)|skipping.*sampler|deferred\\s*to\\s*next\\s*tick)'},
      // CSRF / CORS / auth — security-class events. Pinning this
      // chip during an auth-debug session surfaces 403 / origin /
      // CSRF mismatch lines across all sources.
      {id: 'cors_csrf', regex_str: '(CSRF|CORS|forbidden\\s*\\(403\\)|origin\\s*mismatch|invalid\\s*token)'},
    ],
    logPatternFilter: {auth_cooldown: false, probe_timeout: false, sql_drift: false, provider_paused: false, ws_disconnect: false, sampler_skip: false, cors_csrf: false},
    // Cached compiled regexes per-pattern. Built lazily on first
    // filter call + invalidated on chip-set change (no UI today
    // alters the set, but a future "operator-custom pattern" feature
    // would invalidate this map). Per-call compilation is cheap but
    // cache hits are cheaper across the thousands-of-lines ring.
    _logPatternRegexCache: null,
    logPollHandle: null,
    // Display order for the weekday picker. Mon=0..Sun=6 matches the
    // backend's Python tm_wday convention; labels are i18n keys.
    weekdayOrder: [0, 1, 2, 3, 4, 5, 6],
    // Admin view state
    adminTab: 'users',
    // Admin → Providers provider tabs. Persists in
    // localStorage so the page returns to the operator's view on
    // refresh. Default falls through to the first ENABLED provider on
    // first load (handled by `setHostStatsTab` / `loadSettings`'s
    // post-hydrate setter); 'beszel' is the safe initial since it's
    // the most-common.
    hostStatsTab: (() => {
      try {
        const v = localStorage.getItem('hostStatsTab');
        // `'snmp'` was missing from the init-time whitelist when
        // the SNMP provider landed, so any operator who picked
        // the SNMP tab got reset to `'beszel'` on every refresh
        // (the setter wrote `'snmp'` correctly, but the IIFE filtered
        // it out on the next page load and fell through to the
        // default). Whitelist now matches `setHostStatsTab`'s own
        // valid set so the persisted tab actually persists.
        if (v && ['node_exporter', 'beszel', 'pulse', 'webmin', 'ping', 'snmp'].includes(v)) {
          return v;
        }
      } catch {
      }
      return 'beszel';
    })(),
    snapshotsLoaded: false,
    newIgnore: {kind: 'image', pattern: ''},
    endpointId: 1,
    busy: {},
    themePref: localStorage.getItem('theme') || 'auto',
    // Current user, set from /api/me on init. Null until that call
    // completes; the SPA defers rendering everything that depends on it.
    me: null,
    // Per-session dismiss flag for the SESSION_SECRET-auto
    // warning banner. Stored in sessionStorage (NOT localStorage) so
    // it resets every browser-session — that's the whole point: each
    // restart of the OmniGrid container kills sessions, and operators
    // should be re-warned each restart so they remember to set
    // SESSION_SECRET in the env. Once they fix it, the backend stops
    // setting `me.session_secret_auto` so the banner disappears.
    sessionSecretWarningDismissed: (typeof sessionStorage !== 'undefined' &&
      sessionStorage.getItem('sessionSecretWarningDismissed') === '1') || false,
    // Same dismissal pattern as the SESSION_SECRET banner — per-session,
    // re-appears after a restart so the operator sees the reminder until
    // they actually clear the env vars.
    bootstrapEnvWarningDismissed: (typeof sessionStorage !== 'undefined' &&
      sessionStorage.getItem('bootstrapEnvWarningDismissed') === '1') || false,

    // Mobile-viewport flag (<= 768px — matches the .hosts-mobile-cards CSS
    // breakpoint). Drives the Hosts-view gate that lets each form factor's
    // per-host x-for on this so only the ACTIVE tree's rows render + bind
    // (the inactive one iterates [] -> zero per-row bindings, instead of both
    // trees evaluating every per-host binding each flush under x-show).
    // Initialized from matchMedia at construction so the first render is
    // correct; kept live by a 'change' listener wired in init().
    isMobileViewport: (typeof window !== 'undefined' && window.matchMedia)
      ? window.matchMedia('(max-width: 768px)').matches : false,

    async init() {
      // Expose the live Alpine component instance globally for the
      // browser-console diagnostic helpers (e.g.
      // `omnigrid.statsDebug()`). The factory function `app()` would
      // return a FRESH default-state object on each call — what we
      // need here is the same `this` that Alpine has been mutating
      // since boot. Single-replica + single-component-per-page so
      // there's no ambiguity about which instance to expose.
      try {
        window.omnigrid = this;
      } catch (_) {
      }
      // Start the backend-unreachable banner watcher. Cheap 5s ticker that
      // reads `window.__ogLastBackendOkTs` (stamped by the fetch wrapper +
      // SSE onAny) and flips `backendUnreachable` once the gap exceeds the
      // operator-tunable threshold. See _startBackendReachabilityWatcher
      // in app-sse.js.
      try {
        this._startBackendReachabilityWatcher();
      } catch (_) {
      }
      // Hydrate the per-tab SESSION_SECRET banner-dismissal flag from
      // sessionStorage so a single tab session keeps the dismissal but a
      // fresh tab re-shows the warning (the underlying SESSION_SECRET
      // threat persists across deploys until the operator sets the env
      // var explicitly). Defence-in-depth — try/catch for environments
      // where sessionStorage is unavailable / blocked.
      try {
        if (sessionStorage.getItem('og_session_secret_banner_dismissed') === '1') {
          this.sessionSecretBannerDismissed = true;
        }
      } catch (_) {
      }
      // Wrap any orphan `<input type="password">` in a hidden
      // `<form>` so Chrome / Edge stop logging "Password field is
      // not contained in a form" warnings (~17 instances across
      // admin tabs — Beszel / Pulse / Webmin / Portainer / OIDC /
      // SSH / Asset / SNMP / host-groups secrets). The form uses
      // `display: contents` so layout is unaffected; `submit.prevent`
      // catches Enter-key submissions so Alpine's existing button
      // handlers stay the source of truth. Runs on init AND on every
      // settings-section / admin-tab open via a MutationObserver so
      // dynamically-rendered partials get the same treatment.
      try {
        this._wrapOrphanPasswordFields();
      } catch (_) {
      }
      try {
        const obs = new MutationObserver(() => {
          // Debounce — multiple Alpine x-if toggles fire in bursts;
          // wrapping in microtask keeps the work tight.
          if (this._wrapPwdScheduled) {
            return;
          }
          this._wrapPwdScheduled = true;
          queueMicrotask(() => {
            this._wrapPwdScheduled = false;
            try {
              this._wrapOrphanPasswordFields();
            } catch (_) {
            }
          });
        });
        obs.observe(document.body, {childList: true, subtree: true});
      } catch (_) {
      }
      // Tick the last-test-success "now" reference once a minute so
      // the relative-time labels next to every Test connection button
      // refresh without reload. Cheap (one int assignment + reactive
      // re-evaluation of the small set of `lastTestSuccessLabel(...)`
      // bindings).
      try {
        setInterval(() => {
          this._lastTestSuccessNow = Math.floor(Date.now() / 1000);
        }, 60 * 1000);
      } catch (_) {
      }
      // Idle-time progressive fill for the Hosts view. While the
      // operator stays at the top of the page (no scroll), the
      // IntersectionObserver only fires for the few rows actually in
      // the viewport — every off-screen row stays unfetched
      // indefinitely. This ticker quietly enqueues ONE not-yet-seen
      // host every N seconds (tunable, default 3 s) into the SAME
      // shared `_hostRefreshQueue` the IO observer feeds, so by the
      // time the operator scrolls, rows further down already have
      // data. Backend pressure stays bounded because every enqueued
      // id goes through the existing `tuning_hosts_parallel_fetch`
      // worker cap. Set `tuning_hosts_idle_fill_interval_seconds=0`
      // to disable (scroll-only lazy load — pre-fix behaviour).
      // Skip conditions on every tick:
      //   - not on the Hosts view (paying the trickle cost on a view
      //     the user isn't looking at is wasteful)
      //   - tab is hidden (browser visibility API; same logic as
      //     "don't poll while user is on another tab")
      //   - drawerHost is open (operator is investigating one host;
      //     trickle would compete with their /api/hosts/one/<id> +
      //     history fetches)
      //   - a fast scroll is in progress (the IO observer is firing
      //     bursts; let it own the queue for a beat)
      //   - every host already seen (nothing left to fill)
      try {
        let lastScrollTs = 0;
        // Window-level scroll only. Earlier iteration tried capture-
        // phase document listener (`{ capture: true }`) to catch
        // scrolls inside ANY nested scroll container, but that picked
        // up too much noise — drawer bodies / dropdowns / Alpine
        // x-effect-driven minor scrolls all updated `lastScrollTs`,
        // keeping the idle-fill gate blocked even when the operator
        // wasn't actually scrolling the page. Window scroll covers
        // the actual page-content scroll path which is what the gate
        // is trying to debounce against. The Hosts page scrolls the
        // body/window, not a nested container.
        window.addEventListener('scroll', () => {
          lastScrollTs = Date.now();
        }, {passive: true});
        // First ~5 ticker fires log their gate evaluation to console
        // so operators chasing "why isn't idle-fill working?" can see
        // which gate is blocking without inspecting source. Bounded so
        // the log doesn't spam over hours of usage.
        let _idleDebugBudget = 5;
        const _idleDebug = (_reason, _extra) => {
          if (_idleDebugBudget <= 0) {
            return;
          }
          _idleDebugBudget -= 1;
        };
        const idleFillTick = () => {
          try {
            const intervalSeconds = (this.me && this.me.client_config
              && Number(this.me.client_config.hosts_idle_fill_seconds)) || 0;
            if (intervalSeconds <= 0) {
              _idleDebug('skip: intervalSeconds<=0', {intervalSeconds});
              return;
            }
            if (this.view !== 'hosts') {
              _idleDebug('skip: wrong view', {view: this.view});
              return;
            }
            if (typeof document !== 'undefined'
              && document.visibilityState === 'hidden') {
              _idleDebug('skip: tab hidden');
              return;
            }
            if (this.drawerHost) {
              _idleDebug('skip: drawer open', {id: this.drawerHost && this.drawerHost.id});
              return;
            }
            // Skip if the user is actively scrolling — let the IO
            // observer's burst-coalescer own the queue. 500 ms of
            // post-scroll idle passes this gate. Lowered from 1500 ms
            // because operators sitting still on the page reported
            // idle-fill never firing; ANY transient scroll (browser-
            // back, Alpine x-effect-driven layout shift, anchor jump)
            // would block it for a full 1.5 s, frequently long enough
            // that the next ticker tick re-skips on the same residual.
            const sinceScroll = Date.now() - lastScrollTs;
            if (sinceScroll < 500) {
              _idleDebug('skip: recent scroll', {sinceScrollMs: sinceScroll});
              return;
            }
            const hosts = this.hosts || [];
            if (!hosts.length) {
              _idleDebug('skip: no hosts loaded yet');
              return;
            }
            const seen = this._hostSeenIds || new Set();
            // Find the first host not yet seen + not currently
            // queued. `_hostRefreshQueue` is shared with the IO
            // observer so we don't double-enqueue an id already
            // pending a refresh. `_hostObserverPending` covers the
            // 200 ms debounced batch the observer is about to flush.
            const queued = new Set(this._hostRefreshQueue || []);
            const obsPending = this._hostObserverPending || new Set();
            let nextId = null;
            for (const h of hosts) {
              const id = h && h.id;
              if (!id) {
                continue;
              }
              if (seen.has(id)) {
                continue;
              }
              if (queued.has(id)) {
                continue;
              }
              if (obsPending.has(id)) {
                continue;
              }
              nextId = id;
              break;
            }
            if (!nextId) {
              _idleDebug('skip: every host already seen / queued / pending', {
                total: hosts.length,
                seen: seen.size,
                queued: queued.size,
                obsPending: obsPending.size,
              });
              return;
            }
            // (Was a `_idleDebug('enqueueing host', ...)` console call —
            // dropped per operator request; happy-path enqueue is too
            // noisy in the console once the gate is verified working.
            // The skip-reason calls above still fire to surface gate
            // misconfigs.)
            // Route through the shared queue so the worker cap
            // applies. The function name reads "lazy" but it IS
            // the canonical entry point used by every refresh
            // path including scroll-triggered ones.
            //
            // ORDER MATTERS: enqueue FIRST, mark seen AFTER. Pre-fix
            // the flow was reversed (mark seen → enqueue), which meant
            // a failure between the two — `_enqueueHostRefresh` errors,
            // `refreshHostRow` 504-backoff, or network failure inside
            // the worker — left the host permanently in `_hostSeenIds`
            // with no data and no retry path until page reload. Now
            // we only mark seen once the enqueue succeeds; failed
            // enqueues fall through to the next idle-fill tick AND
            // the IO observer's scroll-driven fetch path picks up
            // the host whenever the operator scrolls past it.
            let enqueued = false;
            try {
              this._enqueueHostRefresh(nextId);
              enqueued = true;
            } catch (_) { /* enqueue failed — leave host unseen for retry */
            }
            if (enqueued) {
              seen.add(nextId);
              this._hostSeenIds = seen;
              try {
                this._ensureHostRefreshWorkers();
              } catch (_) {
              }
            }
          } catch (_) { /* never let the ticker throw */
          }
        };
        // Run the ticker every 1 s; the gate inside compares
        // `intervalSeconds` against an internal counter so the
        // effective cadence matches the operator-tuned value
        // without restarting the interval when the tunable changes.
        let tickCount = 0;
        setInterval(() => {
          // idle-fill only does work on the Hosts view — bail
          // immediately off it so the 1Hz tick is a single comparison (no
          // client_config read, no modulo, no enqueue work) on every other
          // view rather than running the full gate each second.
          if (this.view !== 'hosts') {
            return;
          }
          tickCount += 1;
          const intervalSeconds = (this.me && this.me.client_config
            && Number(this.me.client_config.hosts_idle_fill_seconds)) || 0;
          if (intervalSeconds <= 0) {
            return;
          }
          if (tickCount % Math.max(1, Math.round(intervalSeconds)) !== 0) {
            return;
          }
          idleFillTick();
        }, 1000);
      } catch (_) {
      }
      // Restore persisted UI prefs that need to land before the first
      // render of their dependent views.
      this._restoreLogSeverity();
      this._restoreLogPattern();
      // i18n is already loaded (Alpine is gated on __i18nReady), but pull
      // the authoritative language list + current code/dir into the
      // reactive Alpine state so pickers and v-bindings track it.
      try {
        await window.__i18nReady;
      } catch (_) {
      }
      if (window.I18N) {
        this.lang = window.I18N.code;
        this.dir = window.I18N.dir;
        this.availableLanguages = window.I18N.languages || this.availableLanguages;
      }
      // Resolve identity FIRST. Unauthenticated users get redirected to
      // /login; everyone else falls through to the normal data loads.
      // (The global fetch wrapper also handles mid-session 401s, but doing
      // an explicit check up front avoids flashing an empty UI to someone
      // who isn't logged in at all.)
      try {
        const r = await fetch('/api/me');
        if (r.ok) {
          const m = await r.json();
          if (!m.authenticated) {
            const path = location.pathname + location.search;
            location.href = '/login?next=' + encodeURIComponent(path);
            return;
          }
          this.me = m;
          this.syncProfileForm();
          // Hydrate `aiProviderNames` from the canonical backend list
          // (logic.ai.SUPPORTED_PROVIDERS, surfaced via
          // /api/me's client_config.ai.provider_names). The data field
          // ships with a defensive fallback literal for the brief
          // window before /api/me resolves; this overwrite makes the
          // SPA the single source of truth for the provider order
          // even when the backend tuple grows.
          try {
            const names = m && m.client_config && m.client_config.ai && m.client_config.ai.provider_names;
            if (Array.isArray(names) && names.length) {
              this.aiProviderNames = names.slice();
            }
          } catch (_) { /* fall through to the literal fallback */
          }
          // Hydrate theme preference from the user's DB-backed
          // `ui_prefs.theme` (cross-browser / cross-machine source of
          // truth). localStorage stays as a per-browser fast-path
          // cache — used for the initial paint before /api/me round-
          // trips, but the DB value wins on every load. When the DB
          // value differs from the localStorage cache, sync the cache
          // so the next page load matches.
          try {
            const dbTheme = (m && m.ui_prefs && m.ui_prefs.theme) || '';
            if (dbTheme && ['auto', 'light', 'dark'].includes(dbTheme)
              && dbTheme !== this.themePref) {
              this.themePref = dbTheme;
              try {
                localStorage.setItem('theme', dbTheme);
              } catch (_) {
              }
              this.applyTheme();
            }
          } catch (_) {
          }
          // Hydrate UI language from DB — same write-through pattern
          // as theme. localStorage caches the operator's last choice
          // for instant first-paint; DB value wins on every load so
          // the operator's locale follows them across browsers /
          // machines AND so backend notification template resolution
          // (logic/ops.py:resolve_actor_locale) picks up the right
          // language for notifications fired by this operator.
          try {
            const dbLang = (m && m.ui_prefs && m.ui_prefs.lang) || '';
            if (dbLang && dbLang !== this.lang) {
              try {
                localStorage.setItem('lang', dbLang);
              } catch (_) {
              }
              if (typeof this.setLang === 'function') {
                this.setLang(dbLang);
              }
            }
          } catch (_) {
          }
          // Hydrate host-drawer time-range picker from DB — same shape
          // as theme above. Override the localStorage cache when the DB
          // has a value so the operator's preferred range follows them
          // across browsers.
          try {
            const dbRange = m && m.ui_prefs && Number(m.ui_prefs.host_history_range);
            if (Number.isFinite(dbRange) && dbRange > 0
              && dbRange !== this.hostHistoryRange) {
              // Setting the bound model triggers the existing $watch
              // which writes the localStorage cache + re-PATCHes the
              // backend. The PATCH is idempotent (same value) so the
              // round-trip costs ~30ms once on /api/me load.
              this.hostHistoryRange = dbRange;
            }
          } catch (_) {
          }
          // AI sidebar slash-picker recents — last 5 invoked
          // ACTION-kind slash entries, FIFO + dedupe, persisted to
          // ui_prefs.ai_recent_slash_actions so the "Recents" group
          // at the top of the slash picker survives reloads + follows
          // the operator across browsers.
          try {
            const recents = m && m.ui_prefs && m.ui_prefs.ai_recent_slash_actions;
            if (Array.isArray(recents)) {
              this.aiRecentSlashActions = recents.slice(0, 5).filter(x => typeof x === 'string' && x);
            }
          } catch (_) {
          }
          // AI sidebar mode (approval / autonomous). Persisted to
          // ui_prefs.ai_sidebar_mode so the operator's pick follows
          // them across browsers. Default 'approval' — destructive
          // actions surface the inline-confirm chip. 'autonomous'
          // bypasses the confirm path entirely; only choose when
          // the operator wants the AI to act without intervention
          // (e.g. agentic background workflows).
          try {
            const mode = m && m.ui_prefs && m.ui_prefs.ai_sidebar_mode;
            if (mode === 'approval' || mode === 'autonomous') {
              this.aiSidebarMode = mode;
            }
          } catch (_) {
          }
          // AI sidebar launcher visibility — operator-controlled
          // preference to hide the floating launcher button so
          // keyboard-only operators don't pay the Tab cost on every
          // page load. Cmd-K still opens the sidebar regardless.
          // Persisted to ui_prefs.ai_sidebar_launcher_hidden so the
          // choice follows the operator across browsers.
          try {
            const hidden = m && m.ui_prefs && m.ui_prefs.ai_sidebar_launcher_hidden;
            this.aiSidebarLauncherHidden = !!hidden;
            // Sync the draft so the Settings → Profile checkbox starts
            // un-dirty after hydration. Without this, the checkbox
            // would render with the saved value but `headerPrefsDirty()`
            // would return true on the very first render because the
            // draft default differs from the just-loaded value.
            this.aiSidebarLauncherHiddenDraft = !!hidden;
          } catch (_) {
          }
          // ui_prefs.notifications_group_similar — Notifications popup
          // cluster-pivot toggle. Operator's choice
          // persists cross-device so a busy fleet's "group similar"
          // view survives every reload.
          try {
            const gs = m && m.ui_prefs && m.ui_prefs.notifications_group_similar;
            this.notificationsGroupSimilar = !!gs;
          } catch (_) {
          }
          // ui_prefs.ai_sidebar_pinned — pin-to-dock mode. When true,
          // the sidebar opens automatically AND stays docked as a
          // left-edge split (body padding shrinks main view).
          // Persisted across browsers / machines so the operator's
          // workspace layout survives a fresh login.
          try {
            const pinned = m && m.ui_prefs && m.ui_prefs.ai_sidebar_pinned;
            this.aiSidebarPinned = !!pinned;
            // When pinned at hydration, force the sidebar open. The
            // CSS `.ai-sidebar-drawer.pinned` rule makes it always
            // visible regardless of the `.open` class, but we also
            // flip the state so reads of `aiSidebarOpen` elsewhere
            // (focus trap, ESC handler) stay consistent.
            if (this.aiSidebarPinned) {
              this.aiSidebarOpen = true;
            }
          } catch (_) {
          }
          // AI assistant conversation history — restore from
          // ui_prefs.ai_conversation so a hard reload (or moving to a
          // different browser / machine) doesn't drop the chat. The
          // Clear button stamps `ai_conversation_cleared_at` in
          // ui_prefs WITHOUT touching the conversation array; turns
          // older than the cutoff are filtered out of the hydration
          // (visible chat resets) while every turn stays preserved in
          // the DB for learning / analytics.
          try {
            const dbConv = m && m.ui_prefs && m.ui_prefs.ai_conversation;
            const cutoff = (m && m.ui_prefs && Number(m.ui_prefs.ai_conversation_cleared_at)) || 0;
            // Pick whichever source has the most turns. DB is canonical
            // for cross-browser; localStorage is the per-browser
            // write-through cache (`persistAiConversation` writes both).
            // The cache wins when (a) the DB row is missing because
            // a redeploy ran before the last PATCH landed, OR (b) a
            // concurrent /api/me wholesale-replace clobbered it
            // mid-flight. Either source's older turns get filtered by
            // the cleared_at cutoff so screen-clear semantics hold
            // regardless of which one wins.
            let lsConv = null;
            try {
              if (typeof localStorage !== 'undefined' && m && m.id) {
                const raw = localStorage.getItem('aiConversation:' + m.id);
                if (raw) {
                  const parsed = JSON.parse(raw);
                  if (Array.isArray(parsed)) {
                    lsConv = parsed;
                  }
                }
              }
            } catch (_) { /* private-mode / corrupt entry — skip */
            }
            const dbLen = Array.isArray(dbConv) ? dbConv.length : 0;
            const lsLen = Array.isArray(lsConv) ? lsConv.length : 0;
            const conv = (lsLen > dbLen) ? lsConv : dbConv;
            if (Array.isArray(conv) && conv.length > 0) {
              // Filter rule: preserve every turn whose ts post-dates
              // the cleared cutoff. Pre-fix this passed `Number(t.ts)
              // > cutoff` which silently NUKED every turn whose
              // persisted record didn't carry `ts` (the async-persist
              // map historically omitted the field — fixed in
              // `persistAiConversation`). Belt-and-braces: when a
              // turn carries no `ts` at all, treat it as a legacy
              // pre-fix record and PRESERVE it (the user shouldn't
              // lose cross-device history because the persist map
              // had a bug). Once a new turn fires the persist round-
              // trip, every turn rewrites with `ts` and the filter
              // tightens back to exact cutoff comparison.
              const filtered = cutoff > 0
                ? conv.filter(t => {
                  if (!t) {
                    return false;
                  }
                  const ts = Number(t.ts);
                  if (!Number.isFinite(ts) || ts <= 0) {
                    return true;
                  } // legacy / missing ts → preserve
                  return ts > cutoff;
                })
                : conv;
              this.aiConversation = filtered;
              // Precompute rendered markdown for each restored assistant
              // turn so the x-html binding reads the stamped turn._html
              // field instead of re-parsing on every flush (same
              // precompute-once win the live reply path applies). Error /
              // user turns render via x-text and are skipped; any turn
              // left unstamped falls back to the memoized renderer.
              for (const _t of filtered) {
                if (_t && _t.role === 'assistant' && !_t.error && _t.text
                  && _t._html === undefined) {
                  try {
                    _t._html = this._renderAiAnswerMd(_t.text);
                  } catch (_e) { /* fall back to the binding's renderer */
                  }
                }
              }
              this._aiConversationClearedAt = cutoff;
              // If localStorage held more turns than the DB (a
              // mid-flight regression mid-PATCH or a redeploy that
              // outran the last write), repair the DB by re-persisting
              // from the merged in-memory array. Fire-and-forget — a
              // failure here just means the next persist round-trip
              // (next turn / clear / feedback) will handle it.
              if (lsLen > dbLen) {
                try {
                  this.persistAiConversation();
                } catch (_) {
                }
              }
              // Re-fetch chart data for any restored turn that had
              // `host_ids` — the shells render via x-html but stay in
              // "Loading…" state until the populator runs.
              this.$nextTick(() => {
                for (const t of filtered) {
                  if (t && Array.isArray(t.host_ids) && t.host_ids.length > 0 && t.ts) {
                    for (const hid of t.host_ids) {
                      // Pass the saved `chart_kind` through so re-
                      // hydrated turns render the same chart type
                      // they originally requested. Absent / legacy
                      // turns get '' which defaults to disk_projection.
                      this._populateAiSidebarHostChart(hid, t.ts, t.chart_kind || '');
                    }
                  }
                }
              });
            }
          } catch (_) {
          }
          // Hydrate the last-Test-success cache from the DB-backed
          // `client_config.last_test_success` block. Drives the
          // "Last connected: <relative time>" label next to every Test
          // connection button. Cross-browser / cross-machine consistent
          // because the source is the `settings` KV row, not localStorage.
          const lts = m && m.client_config && m.client_config.last_test_success;
          if (lts && typeof lts === 'object') {
            this._lastTestSuccess = {...lts};
          }
          // Adopt operator-tunable notifications page size from
          // /api/me's client_config. Falls back to the in-data()
          // default (25) when missing. Bounds-clamped by the backend
          // resolver so a corrupt setting can't flood the popup.
          const npsRaw = m && m.client_config && m.client_config.notifications_page_size;
          const nps = parseInt(npsRaw, 10);
          if (Number.isFinite(nps) && nps > 0) {
            this.notificationsLimit = nps;
          }
          // apply per-user UI prefs from the server so the
          // weather/clock toggles (etc.) sync across devices for the
          // same login. Runs AFTER `me` lands so applyServerUiPrefs
          // can read this.me.ui_prefs.
          if (typeof this.applyServerUiPrefs === 'function') {
            this.applyServerUiPrefs();
          }
          // Capture the header-prefs dirty baseline AFTER hydration
          //. The baseline initialiser is `''` (the empty
          // string sentinel set on the data() block) and the
          // snapshot helper returns a populated JSON string, so
          // without this re-baseline the form always reads dirty
          // until the first Save click.
          if (typeof this._headerPrefsSnapshot === 'function') {
            this._headerPrefsBaseline = this._headerPrefsSnapshot();
          }
          // fetch TOTP status alongside /api/me so the Profile
          // section can render its 2FA card without a click-induced
          // round-trip. Authentik users (no local password) skip the
          // call; the server short-circuits its response either way.
          if (typeof this.loadTotpStatus === 'function') {
            this.loadTotpStatus();
          }
          // same pattern for the passkeys list.
          if (typeof this.loadPasskeys === 'function') {
            this.loadPasskeys();
          }
        }
      } catch (_) { /* network hiccup — next fetch will trip the wrapper */
      }

      if (window.matchMedia) {
        const mq = window.matchMedia('(prefers-color-scheme: light)');
        const onSys = () => {
          if (this.themePref === 'auto') {
            this.applyTheme();
          }
        };
        mq.addEventListener('change', onSys);
      }
      // keep isMobileViewport live so the Hosts view swaps which
      // form factor's rows render when the viewport crosses 768px. matchMedia
      // 'change' fires only on crossing the breakpoint (not per resize px),
      // so no debounce is needed.
      if (window.matchMedia) {
        const mqHosts = window.matchMedia('(max-width: 768px)');
        mqHosts.addEventListener('change', (e) => {
          this.isMobileViewport = e.matches;
        });
      }
      this.applyTheme();
      // URL routing — reflect current view + section in the path so a
      // refresh keeps the operator where they were (/admin/providers,
      // /settings/profile, etc). Run the one-time parse BEFORE wiring
      // the watchers so the initial assign doesn't spam pushState.
      this._applyRouteFromPath();
      // Persist the drawer-chart range so a refresh doesn't snap the
      // picker back to 1h. localStorage stays as a fast-path cache for
      // first-paint before /api/me round-trips; the DB-backed
      // `users.ui_prefs.host_history_range` is the cross-browser /
      // cross-machine source of truth. Same shape as the theme
      // migration.
      this.$watch('hostHistoryRange', v => {
        try {
          localStorage.setItem('hostHistoryRange', String(v));
        } catch (_) {
        }
        this.persistHostHistoryRange(v);
        // Re-fetch host-debug for any host whose debug panel is
        // currently open — the `samples_in_window` block is keyed
        // off the chart range, so flipping 1h → 24h needs a fresh
        // fetch to surface the right counts. Cheap fan-out: typically
        // only ONE drawer is open, so this is at most a single call.
        try {
          for (const hid of Object.keys(this.hostsDebugOpen || {})) {
            if (this.hostsDebugOpen[hid] && !this.hostsDebugLoading[hid]) {
              this.loadHostDebug(hid).catch(() => {
              });
            }
          }
        } catch (_) {
        }
      });
      // AI-sidebar textarea — vanilla input listener that throttles
      // `aiSidebarQuery` updates to once every ~300 ms. Pre-fix the
      // textarea used `x-model.debounce.150ms` which still routed
      // every keystroke through Alpine's reactivity (debouncing only
      // delays the SETTER; Alpine still wires up reactive
      // dependencies on every keystroke), AND a `@input="aiSidebarSlashIdx = 0"`
      // handler that wrote to a second reactive field per keystroke.
      // On this complex page (1900+ binding sites) even cheap state
      // writes registered as visible typing lag. The vanilla path
      // skips Alpine entirely until the throttle fires; typing feels
      // native because the browser owns the keystroke handling
      // exclusively. The `$nextTick` wrap defers attachment until
      // the textarea is in the DOM (the sidebar markup is gated by
      // `aiSidebarOpen` x-show, which is initially false — but the
      // textarea is always present even when hidden).
      this.$nextTick(() => {
        const el = document.getElementById('og-ai-sidebar-input');
        if (!el) {
          return;
        }
        // Iteration 4 — also moved the placeholder + disabled
        // bindings + keydown handler off the Alpine attribute path
        // so the textarea is FULLY outside Alpine's reactivity until
        // the 300 ms throttle fires. Pre-fix even with `x-model`
        // gone, the `:placeholder` / `:disabled` / `@keydown`
        // bindings caused Alpine to do work per keystroke on the
        // 1900-binding-site page — visible as typing lag for some
        // operators. The vanilla equivalents below sidestep that.
        try {
          const ph = (typeof this.t === 'function')
            ? this.t('ai_sidebar.input_placeholder')
            : 'Ask the AI...';
          if (ph) {
            el.placeholder = ph;
            el.setAttribute('aria-label', ph);
          }
        } catch (_) { /* ignore */
        }
        // Reflect aiSidebarBusy onto the disabled attribute via a
        // $watch — fires only when the boolean transitions, NOT on
        // every keystroke. Initial sync covers the page-load case.
        el.disabled = !!this.aiSidebarBusy;
        this.$watch('aiSidebarBusy', (v) => {
          try {
            el.disabled = !!v;
          } catch (_) {
          }
        });
        // Vanilla keydown listener — replaces the Alpine `@keydown`
        // binding. Bound so `this` inside the handler refers to the
        // Alpine component. We dispatch into the same
        // `aiSidebarHandleKeydown` so the slash-picker / Enter /
        // Esc / arrow-up flows are unchanged.
        const onKey = (ev) => {
          try {
            this.aiSidebarHandleKeydown(ev);
          } catch (_) { /* ignore */
          }
        };
        el.addEventListener('keydown', onKey);
        let pendingTimer = null;
        const flushSoon = () => {
          if (pendingTimer) {
            return;
          }  // already scheduled
          pendingTimer = setTimeout(() => {
            pendingTimer = null;
            const value = el.value || '';
            if (value !== this.aiSidebarQuery) {
              this.aiSidebarQuery = value;
              // Reset slash-picker selection when the query changes
              // (slash-results may produce a different ordered list).
              if (this.aiSidebarSlashIdx !== 0) {
                this.aiSidebarSlashIdx = 0;
              }
            }
          }, 300);
        };
        el.addEventListener('input', flushSoon);
        // Sync immediately on blur — operators expect "click Send"
        // after typing to send the FULL text without the 300 ms
        // settle window. The Enter-press handler also reads
        // `e.target.value` directly via `aiSidebarHandleKeydown`,
        // so this is belt-and-braces for the click-Send path.
        el.addEventListener('blur', () => {
          if (pendingTimer) {
            clearTimeout(pendingTimer);
            pendingTimer = null;
          }
          if (el.value !== this.aiSidebarQuery) {
            this.aiSidebarQuery = el.value || '';
          }
        });
        // Track the listener / timer so destroy paths can clean up
        // (today the SPA doesn't tear down components, but if a
        // future refactor introduces hot-reload semantics this
        // hook makes the cleanup obvious).
        this._aiSidebarInputCleanup = () => {
          if (pendingTimer) {
            clearTimeout(pendingTimer);
          }
          el.removeEventListener('input', flushSoon);
        };
      });

      // Persistence safety net — `aiConversation` mutations should
      // ideally call `persistAiConversation()` explicitly (every push
      // site does), but Alpine's $watch on an array doesn't reliably
      // fire on `.push()` mutations (only on full reassignment). Three
      // belt-and-braces layers replace the unreliable single $watch:
      //
      //   1. `_aiConvSignature` tracks the JSON-serialised length+last-ts
      //      tuple of `aiConversation`; an interval ticks every 2s
      //      and fires `persistAiConversation` when the signature
      //      changed since the last tick. Catches every push regardless
      //      of how Alpine's reactivity reports it.
      //
      //   2. `beforeunload` fires `persistAiConversationSync()` (a
      //      blocking version using `navigator.sendBeacon` — fire-and-
      //      forget but DELIVERY-GUARANTEED via the browser's beacon
      //      queue). Covers the "user typed and refreshed within the
      //      next 2s" race the interval can't catch.
      //
      //   3. The original $watch stays for the ASSIGNMENT path
      //      (init hydration / clearAiConversation). It fires there
      //      reliably even when it doesn't fire for pushes.
      //
      // The actual persist function is idempotent (always serialises
      // the full capped array) so duplicate calls are harmless.
      this._aiConvSignature = '';
      const _computeAiConvSig = () => {
        const arr = this.aiConversation || [];
        const last = arr[arr.length - 1] || {};
        return arr.length + ':' + (last.ts || 0) + ':' + (last.role || '') + ':' + (last.text || '').length;
      };
      // Operator-tunable cadence — defaults to 2s. Slow networks /
      // low-power devices can raise it via Admin → Config →
      // `tuning_ai_conversation_persist_interval_ms` to e.g. 5000ms.
      // Read from `me.client_config.ai_conversation_persist_ms` once
      // when the interval is armed; a Save in Admin → Config takes
      // effect on the next page load (the interval doesn't reload
      // its cadence mid-flight).
      const _aiPersistMs = (this.me && this.me.client_config
        && Number(this.me.client_config.ai_conversation_persist_ms)) || 2000;
      this._aiPersistInterval = setInterval(() => {
        try {
          const sig = _computeAiConvSig();
          if (sig !== this._aiConvSignature) {
            this._aiConvSignature = sig;
            this.persistAiConversation();
          }
        } catch (_) { /* never let a failed tick kill the interval */
        }
      }, _aiPersistMs);
      // beforeunload — write through synchronously via navigator.sendBeacon
      // so the request lands even though the page is unloading. Falls
      // back to localStorage-only when sendBeacon is unavailable
      // (very rare; localStorage is sync so it ALWAYS lands).
      window.addEventListener('beforeunload', () => {
        try {
          this.persistAiConversationSync();
        } catch (_) {
        }
      });
      // pagehide is fired on iOS Safari and on bfcache freezes; mirrors
      // beforeunload to cover those paths. Both can fire so the persist
      // is idempotent (writes the same payload twice = no harm).
      window.addEventListener('pagehide', () => {
        try {
          this.persistAiConversationSync();
        } catch (_) {
        }
      });
      // visibilitychange to 'hidden' covers tab-switch + window blur —
      // a save here means the chat survives even if the user closes
      // the browser without firing beforeunload (some mobile cases).
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') {
          try {
            this.persistAiConversationSync();
          } catch (_) {
          }
        }
      });
      // The original $watch stays for ASSIGNMENT paths.
      let _aiPersistDebounce = null;
      this.$watch('aiConversation', () => {
        if (_aiPersistDebounce) {
          clearTimeout(_aiPersistDebounce);
        }
        _aiPersistDebounce = setTimeout(() => {
          _aiPersistDebounce = null;
          try {
            this.persistAiConversation();
          } catch (_) {
          }
        }, 500);
      });
      this.$watch('view', v => {
        localStorage.setItem('view', v);
        this._pushRoute();
        // Load admin data lazily — only when the user actually navigates there.
        if (v === 'admin') {
          this.openAdminTab(this.adminTab);
        }
        // Same lazy-load contract for Stats — without this, a
        // localStorage-restored or route-applied view='stats' lands
        // with statsOverview={} and loadStatsOverview() never fires,
        // so the dashboard renders a perpetual loading spinner.
        if (v === 'stats') {
          this.openStatsTab(this.statsTab || 'dashboard');
        }
        // Lazy-load hosts on first entry and (re)start its refresh timer.
        // Leaving the tab clears the timer so we're not hammering the hub
        // when the view isn't visible.
        if (v === 'hosts') {
          this.loadHosts();
          if (this._hostsTimer) {
            clearInterval(this._hostsTimer);
          }
          // Honour the "stats off" master switch — when the
          // operator picked Off, the host poll's 15s rebuild cycle
          // (with its brief loading-spinner flash on every row)
          // is part of the "live updates" they wanted to silence.
          if (this.statsInterval > 0) {
            // when SSE is healthy, host:row_updated /
            // host:failure_state_changed events drive refreshHostRow
            // directly. The interval still fires (so the freshness
            // tooltip ticks + we resume polling within one cadence
            // window if SSE drops), but the work is conditional.
            this._hostsTimer = setInterval(() => {
              if (this._sseConnected) {
                return;
              }
              this.loadHosts();
            }, this.statsInterval * 1000);
          }
        } else if (this._hostsTimer) {
          clearInterval(this._hostsTimer);
          this._hostsTimer = null;
        }
        // Apps view — lazy-load aggregate + restart its refresh timer.
        // Same SSE-aware fallback shape as hosts (interval polls only
        // when SSE is disconnected; SSE-driven row updates take over
        // when the stream is healthy).
        if (v === 'apps') {
          if (typeof this.loadAppsList === 'function') {
            this.loadAppsList();
          }
          if (this._appsTimer) {
            clearInterval(this._appsTimer);
          }
          if (this.statsInterval > 0) {
            this._appsTimer = setInterval(() => {
              if (this._sseConnected) return;
              if (typeof this.loadAppsList === 'function') this.loadAppsList();
            }, Math.max(30, this.statsInterval) * 1000);
          }
        } else if (this._appsTimer) {
          clearInterval(this._appsTimer);
          this._appsTimer = null;
        }
      });

      // Notifications popup. Lazy-load on open; the 30s polling
      // fallback only fires when SSE is disconnected (push-driven
      // updates land via _handleNotificationCreated). Driven by
      // `showNotificationsPopup` since the popup is no longer a
      // top-level view (operators wanted a quick check + return to
      // current work without losing their place).
      this.$watch('showNotificationsPopup', open => {
        if (open) {
          if (this._notificationsPollHandle) {
            clearInterval(this._notificationsPollHandle);
          }
          // Operator-tunable poll cadence — when SSE is disconnected
          // AND the notifications popup is open, fall back to polling
          // at this interval. Defaults to 30s; operators on slow
          // connections / power-saving devices can raise it without
          // touching code. Routed through `me.client_config` so a
          // Save in Admin → Config takes effect on the next popup
          // open without a page reload.
          const pollSec = (this.me && this.me.client_config
            && Number(this.me.client_config.notifications_poll_seconds)) || 30;
          this._notificationsPollHandle = setInterval(() => {
            if (this._sseConnected) {
              return;
            }
            if (this.showNotificationsPopup) {
              this.loadNotifications();
            }
          }, pollSec * 1000);
        } else if (this._notificationsPollHandle) {
          clearInterval(this._notificationsPollHandle);
          this._notificationsPollHandle = null;
        }
      });
      this.$watch('settingsSection', v => {
        localStorage.setItem('settingsSection', v);
        this._pushRoute();
      });
      this.$watch('adminTab', () => this._pushRoute());
      // Back/forward buttons — re-read the path into app state.
      window.addEventListener('popstate', () => this._applyRouteFromPath());
      this.$watch('expanded', v => localStorage.setItem('expanded', JSON.stringify(v)));
      this.$watch('hostsSort', v => {
        try {
          localStorage.setItem('hostsSort', v);
        } catch {
        }
      });
      this.$watch('hostsHideUnconfigured', v => {
        try {
          localStorage.setItem('hostsHideUnconfigured', v ? '1' : '0');
        } catch {
        }
      });
      // Webmin scratch-test URL persists so operators don't retype
      // the same host every time they reload the Providers tab.
      this.$watch('webminTestUrl', v => {
        try {
          localStorage.setItem('webminTestUrl', v || '');
        } catch {
        }
      });
      this.$watch('hostsConfigExpanded', v => {
        try {
          localStorage.setItem('hostsConfigExpanded', JSON.stringify(v || {}));
        } catch {
        }
      }, {deep: true});
      // Filter typing collapses the result set — jumping back to page 1
      // is the only sensible default (otherwise the operator types a
      // filter and sees an empty page because they were on page 4 of
      // the unfiltered list).
      this.$watch('hostsConfigFilter', () => {
        this.hostsConfigPage = 1;
      });
      // Persist page index so reload / tab navigation lands on the same
      // page. Pairs with the localStorage initialiser above.
      this.$watch('hostsConfigPage', v => {
        try {
          localStorage.setItem('hostsConfigPage', String(v));
        } catch {
        }
      });
      // `pagedHostsConfig` clamps lazily inside a getter (can't
      // mutate state there without breaking Alpine reactivity), which
      // means `hostsConfigPage` can sit at 5 while the rendered page
      // is actually 3 of 3 (last filtered row trimmed). Watch the
      // pagination inputs and normalise `hostsConfigPage` on the next
      // tick so the visible state matches what the operator sees.
      this.$watch('hostsConfigSortedOrder', () => this._clampHostsConfigPage());
      this.$watch('hostsConfigFilter', () => this._clampHostsConfigPage());
      this.$watch('hostsConfigPerPage', () => this._clampHostsConfigPage());
      // Same persistence for the host-groups editor.
      this.$watch('hostGroupsPage', v => {
        try {
          localStorage.setItem('hostGroupsPage', String(v));
        } catch {
        }
      });
      this.$watch('hostsExpanded', v => {
        try {
          localStorage.setItem('hostsExpanded', JSON.stringify(v || []));
        } catch {
        }
      });
      // Re-surface the translated labels in Settings/Admin sidebars when
      // the user swaps language. Alpine re-renders bindings automatically
      // because `lang` is part of the component state; this watcher just
      // lets us react to the change (e.g. refresh the document title).
      this.$watch('lang', () => {
        document.title = this.t('app.name');
      });
      window.addEventListener('keydown', (e) => this.handleHotkey(e));
      // Capture-phase pre-empt for Cmd-K / Ctrl-K — runs BEFORE
      // browser UI shortcuts (Chrome's omnibox-search Ctrl+K) AND
      // before the bubble-phase handleHotkey above. Without this,
      // some browsers consume the keystroke or focus the address bar
      // before the page-level handler ever sees the event. Capture
      // phase is the only reliable way to claim a hotkey that
      // collides with a browser default. Stops at handleHotkey if
      // matched so the bubble-phase handler doesn't try to fire it
      // a second time. All other keys fall through untouched.
      //
      // Operator-validated rule (2026-05-09): we do NOT use
      // capture-phase intercept to claim browser-default
      // shortcuts (Cmd+R reload / Cmd+Shift+R hard-reload / Cmd+T
      // new-tab / etc.) — instead we pick alternate keys that
      // don't collide. Cmd+K is the one allowed exception because
      // it has no browser-default action on Mac and the SPA's AI
      // sidebar is the canonical "Cmd+K opens it" affordance
      // matching every other modern app (Linear, Notion, etc.).
      window.addEventListener('keydown', (e) => {
        const cmdMod = (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey;
        const isPaletteCombo = cmdMod && (
          e.key === 'k' || e.key === 'K' || e.code === 'KeyK'
        );
        if (!isPaletteCombo) {
          return;
        }
        if (e._cmdpal_handled) {
          return;
        }
        e._cmdpal_handled = true;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        // Long-press discoverability: hold Cmd-K (or Ctrl-K) for
        // >500ms to surface the keyboard-shortcuts overlay (reuses
        // the existing `showHotkeys` modal that the dedicated
        // Cmd+Shift+/ binding opens). Quick tap = AI sidebar
        // toggle (existing behaviour).
        //
        // Implementation: on keydown, schedule a 500ms timer that
        // flips `showHotkeys=true`. On keyup, cancel the timer; if
        // it hadn't fired yet, the press was short → toggle AI
        // sidebar. If it DID fire, the overlay is already up so we
        // do nothing on keyup. Skip the entire dance on key-repeat
        // (operator holding Cmd while tapping K repeatedly).
        if (e.repeat) {
          return;
        }
        if (this._cmdkLongPressTimer) {
          try {
            clearTimeout(this._cmdkLongPressTimer);
          } catch (_) {
          }
          this._cmdkLongPressTimer = null;
        }
        let firedLong = false;
        this._cmdkLongPressTimer = setTimeout(() => {
          firedLong = true;
          this._cmdkLongPressTimer = null;
          try {
            this.showHotkeys = true;
          } catch (_) {
          }
        }, 500);
        const onUp = (up) => {
          // Match the same combo on release. Some keyboards report
          // `e.key` as 'k'/'K' depending on Shift state at release;
          // we already gated Shift out on keydown so this is a
          // belt-and-braces check.
          if (up.code !== 'KeyK' && up.key !== 'k' && up.key !== 'K') {
            return;
          }
          window.removeEventListener('keyup', onUp, {capture: true});
          if (this._cmdkLongPressTimer) {
            try {
              clearTimeout(this._cmdkLongPressTimer);
            } catch (_) {
            }
            this._cmdkLongPressTimer = null;
          }
          if (!firedLong) {
            // Short press — original AI sidebar toggle.
            if (this.aiSidebarOpen) {
              this.closeAiSidebar();
            } else {
              this.openAiSidebar();
            }
          }
          // firedLong=true → overlay already open, leave it alone.
        };
        window.addEventListener('keyup', onUp, {capture: true});
      }, {capture: true});
      // (Diagnostic `window.__omnigridOpenPalette` escape hatch
      //  removed — the palette is verified working in production
      //  and the global handle was just polluting the SPA's window
      //  surface for operators who Tab-completed in DevTools.)

      // Click-outside listener for the chart `?` tap-driven tooltip
      //. The trigger spans + tooltip body each call
      // `@click.stop` so they're EXCLUDED from this handler — taps
      // anywhere else dismiss whatever's open.
      document.addEventListener('click', () => {
        if (this.metricTooltipOpen) {
          this.metricTooltipOpen = null;
        }
      });
      // Warn when closing / reloading the tab with unsaved Hosts
      // edits. Browsers ignore a custom string (Chrome shows their
      // own generic dialog), but the presence of returnValue still
      // triggers the prompt.
      window.addEventListener('beforeunload', (e) => {
        if (this.hostsConfigDirty) {
          e.preventDefault();
          e.returnValue = '';
        }
      });
      await this.loadSettings();
      await this.loadIgnores();
      await this.refresh();
      await this.loadHistory();
      this.loadVersion();
      // Prime the avatar's unread badge before the operator opens
      // the Notifications view. Cheap probe (1-row LIMIT) — see
      // loadNotificationsUnread.
      this.loadNotificationsUnread();
      // Asset inventory — load the cached asset list once on boot so
      // the drawer can surface matched rows (vendor / model / serial /
      // location) without an extra round-trip per row-expand. Silent
      // failure is fine (asset inventory is optional).
      this.loadAssetCache();
      this.startVersionWatcher();
      this.startHeaderClock();
      this.startHeaderWeather();
      // Persisted unified refresh cadence. Mirrors into legacy
      // `autoRefresh` + `statsInterval` so existing pollers see the
      // operator's choice through the keys they already read. When
      // `refreshInterval` is 0 (Off) every poller stays asleep until
      // SSE handles updates via push events.
      // setRefreshInterval already manages SSE based on the chosen
      // mode — Live opens the stream, Off / interval close it. Don't
      // double-init here. `?? -1` (not `|| 0`) preserves the explicit
      // 0 ("Off") choice across reloads instead of mapping it to -1.
      this.setRefreshInterval(this.refreshInterval ?? -1);
      this.pollOps();
      this.pollStats();
      this.pollSparks();
      // If the SPA restored to the Hosts view (saved in localStorage or
      // arrived via /hosts deep-link), trigger the same load+poll the
      // view-watcher does on manual switch.
      if (this.view === 'hosts') {
        this.loadHosts();
        // Same off-honoring rule as the view-watcher above.
        if (this.statsInterval > 0) {
          this._hostsTimer = setInterval(() => {
            this._pollWrap(this.loadHosts());
          }, this.statsInterval * 1000);
        }
      } else {
        // Pre-fetch hosts ONCE on init even when the operator restored
        // to a non-Hosts view, so the AI palette / sidebar context
        // (`_buildAiPaletteContext`) always has host records to ground
        // answers against. Without this, the AI sees an empty hosts
        // array on Stacks / Services / Nodes / Settings views and
        // refuses with "host monitoring data is currently unavailable"
        // — frustrating because the data exists, the SPA just hasn't
        // loaded it yet. Single fire-and-forget call (no polling
        // timer, no SSE wiring) — keeps the data fresh enough for
        // grounded answers without competing with the view-specific
        // 15s timer that takes over once the operator switches views.
        // Perf finding 6: when the restored view is 'apps', fire the VISIBLE
        // view's data FIRST so /api/apps isn't queued behind this
        // fire-and-forget AI-context loadHosts on the cold event loop. The
        // apps branch below only wires the refresh timer — its initial
        // loadAppsList is hoisted here.
        if (this.view === 'apps' && typeof this.loadAppsList === 'function') {
          this.loadAppsList();
        }
        this.loadHosts();
      }
      // If the SPA restored to the Apps view (saved in localStorage or a
      // deep-link), trigger the same load+poll the view-watcher does on a
      // manual switch. The `$watch('view')` only fires on CHANGE, so a
      // page load that's already on 'apps' would otherwise stay blank
      // until the operator clicked Reload — mirror the watcher's apps
      // branch here so initial render populates automatically.
      if (this.view === 'apps') {
        // Initial loadAppsList() is hoisted into the else-branch above (fired
        // BEFORE the AI-context loadHosts per perf finding 6); here we only
        // wire the refresh timer.
        if (this.statsInterval > 0) {
          this._appsTimer = setInterval(() => {
            if (this._sseConnected) {
              return;
            }
            if (typeof this.loadAppsList === 'function') {
              this.loadAppsList();
            }
          }, Math.max(30, this.statsInterval) * 1000);
        }
      }
      // `updateCacheLabel` was retired (the "fresh / cached
      // Xs ago" topbar text was removed as confusing alongside the
      // unified picker). The 1s timer that drove it is gone too — no
      // sense burning a tick per second on a no-op.
      // Tick `hostHistoryNow` every second so the host-drawer charts'
      // "Updated Xs/Xm/Xh ago" label counts in real time. Gated on
      // a drawer being OPEN — when no drawer is shown, the ticker
      // stops entirely so an idle SPA doesn't burn a flush every
      // second of every minute. Arms on drawer open, clears on
      // close; pattern matches the `_drawerHistoryTimer` shape in
      // app-host-drawer.js.
      this.hostHistoryNow = Date.now();
      this._hostHistoryTicker = null;
      const _startHistoryTicker = () => {
        if (this._hostHistoryTicker) {
          return;
        }
        this._hostHistoryTicker = setInterval(() => {
          this.hostHistoryNow = Date.now();
        }, 1000);
      };
      const _stopHistoryTicker = () => {
        if (this._hostHistoryTicker) {
          clearInterval(this._hostHistoryTicker);
          this._hostHistoryTicker = null;
        }
      };
      // Re-evaluate on every reactive flush whether ANY drawer is
      // open; toggle the ticker accordingly. Alpine's `$watch` doesn't
      // accept multi-prop dependencies in a single call, so use the
      // composite reactive expression via `Alpine.effect`.
      try {
        if (typeof Alpine !== 'undefined' && typeof Alpine.effect === 'function') {
          Alpine.effect(() => {
            const anyOpen = !!(this.drawerHost || this.drawerApp
              || this.drawerAppHost || this.drawerItem
              || this.drawerNode);
            if (anyOpen) {
              _startHistoryTicker();
            } else {
              _stopHistoryTicker();
            }
          });
        }
      } catch (_) {
        // Fallback — if Alpine.effect isn't available, just start
        // the ticker unconditionally (original behaviour).
        _startHistoryTicker();
      }
      // Multi-tab activity wiring. Boot-hydrate the
      // sibling-tab map, fire the first heartbeat so OTHER tabs see us,
      // then arm a 30s tick + cleanup hooks. Cross-tab focus channel
      // wires up here too (lazy-created in `focusTabByClientId` if not
      // yet present).
      try {
        this._tabActivityHydrate();
        this._tabActivityHeartbeat();
        // Re-publish on every navigation event so sibling tabs see the
        // location change without waiting for the 30s tick.
        this.$watch('view', () => this._tabActivityHeartbeat());
        this.$watch('drawerHost', () => this._tabActivityHeartbeat());
        this.$watch('adminTab', () => this._tabActivityHeartbeat());
        this.$watch('settingsSection', () => this._tabActivityHeartbeat());
        this.$watch('statsTab', () => this._tabActivityHeartbeat());
        // Idle heartbeat — keeps the entry alive past the backend's
        // 90s TTL when the operator hasn't navigated.
        setInterval(() => {
          try {
            this._tabActivityHeartbeat();
          } catch (_) {
          }
        }, 30000);
        // Cleanup — DELETE the registry entry when the tab unloads so
        // sibling tabs see the count drop immediately instead of
        // waiting for TTL expiry. `pagehide` fires more reliably than
        // `unload` (mobile + bfcache safe). `keepalive: true` ensures
        // the request reaches the backend even mid-tab-close.
        const cleanup = () => {
          try {
            fetch('/api/tabs/activity', {method: 'DELETE', keepalive: true});
          } catch (_) {
          }
        };
        window.addEventListener('pagehide', cleanup);
        // BroadcastChannel listener — sibling tabs ask THIS tab to
        // focus itself. Best-effort; cross-window focus is browser-
        // discretionary, but reliable in same-process tabs.
        try {
          if (typeof BroadcastChannel === 'function') {
            this._tabFocusChannel = new BroadcastChannel('omnigrid-tab-focus');
            this._tabFocusChannel.addEventListener('message', (ev) => {
              const cid = ev && ev.data && ev.data.client_id;
              if (cid && cid === window.__ogClientId) {
                try {
                  window.focus();
                } catch (_) {
                }
              }
            });
          }
        } catch (_) { /* BroadcastChannel sandboxed */
        }
      } catch (_) { /* defensive — tab-activity wiring is non-critical */
      }
    },

    async logout() {
      try {
        await fetch('/api/local-auth/logout', {method: 'POST'});
      } catch (_) { /* ignore — clearing the cookie is the important bit */
      }
      location.href = '/login';
    },
    // Dismiss the SESSION_SECRET-auto banner for this
    // browser session. Persists in sessionStorage so a hard-refresh
    // doesn't unhide it again, but a fresh browser-session (close +
    // reopen, or container restart on the operator's side) brings it
    // back. That's intentional: every restart kills user sessions,
    // operators need the recurring nudge to set SESSION_SECRET.
    dismissSessionSecretWarning() {
      this.sessionSecretWarningDismissed = true;
      try {
        sessionStorage.setItem('sessionSecretWarningDismissed', '1');
      } catch (_) {
      }
    },
    dismissBootstrapEnvWarning() {
      this.bootstrapEnvWarningDismissed = true;
      try {
        sessionStorage.setItem('bootstrapEnvWarningDismissed', '1');
      } catch (_) {
      }
    },

    // --- Profile: display-name / bio / email edit ------------------------
    syncProfileForm() {
      // Called after /api/me loads to populate the editable form. Kept
      // separate from `me` so unsaved edits aren't clobbered on refresh.
      // Hydrates per-user notification prefs from the resolved
      // `notify_events` map; rebuilds the baseline snapshot so the
      // dirty-getter compares against the freshly-loaded values.
      //
      // Per-medium granularity: every event normalises to a per-medium
      // dict `{medium: bool}` for editing simplicity. The wire shape
      // is mixed (legacy bare-bool OR per-medium dict) — this helper
      // expands legacy bools to a per-medium dict where every globally-
      // enabled medium inherits the bool, so the SPA's checkbox grid
      // always reads from a uniform shape. On save, events whose every
      // medium reads identically collapse back to a bare bool to keep
      // the stored payload small.
      const events = {};
      const src = (this.me && this.me.notify_events) || {};
      const mediums = this.notifyMediumNames();
      for (const k of (this.notifyEventKeys || [])) {
        const bare = k.replace(/^notify_event_/, '');
        const v = src[bare];
        const slot = {};
        if (v && typeof v === 'object') {
          // Per-medium dict: copy known mediums, fall back to true for
          // anything missing (matches the dispatcher's default-on-
          // missing-medium contract).
          for (const m of mediums) {
            slot[m] = (v[m] !== false);
          }
        } else {
          // Legacy bare-bool (or absent): apply uniformly across every
          // medium. `!!v` collapses undefined → false, false → false,
          // true → true.
          const b = !!v;
          for (const m of mediums) {
            slot[m] = b;
          }
        }
        events[bare] = slot;
      }
      this.profileForm = {
        display_name: (this.me && this.me.display_name) || '',
        bio: (this.me && this.me.bio) || '',
        email: (this.me && this.me.email) || '',
        notify_events: events,
      };
      this._profileBaseline = this._profileSnapshot();
    },
    // Snapshot helper — JSON-serialises the editable shape of the
    // profile form. Used by both saveProfile() and profileDirty() so
    // the comparison stays in lock-step.
    _profileSnapshot() {
      const f = this.profileForm || {};
      return JSON.stringify({
        display_name: f.display_name || '',
        bio: f.bio || '',
        email: f.email || '',
        notify_events: f.notify_events || {},
      });
    },
    // Dirty-tracker for the Profile form. Compares the live form
    // against the baseline captured on load + each save. Any string
    // divergence in display_name / bio / email / notify_events flips
    // the Save button to its "unsaved changes" treatment.
    profileDirty() {
      if (!this.me) {
        return false;
      }
      return this._profileBaseline !== this._profileSnapshot();
    },

    async saveProfile() {
      if (this.profileBusy) {
        return;
      }
      this.profileBusy = true;
      try {
        // Profile (display_name / bio / email) — same payload as before;
        // /api/me/profile ignores extra fields so we strip the notify
        // map for clarity rather than relying on the route to drop it.
        const profilePayload = {
          display_name: this.profileForm.display_name,
          bio: this.profileForm.bio,
          email: this.profileForm.email,
        };
        const r = await fetch('/api/me/profile', {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(profilePayload),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
          return;
        }
        // Per-user notification opt-in. Sent as a separate PATCH so the
        // admin-gate refusal (400 with detail "Event 'X' is disabled by
        // admin") surfaces a useful message without rolling back the
        // profile edit. Backend payload keys are the bare event names
        // (matching the storage shape inside ui_prefs).
        //
        // Per-medium granularity: profileForm carries every event as
        // `{medium: bool}`. On save we collapse uniform-bool dicts
        // (every medium reads the same) back to a bare bool so storage
        // stays compact AND legacy parsers (operator scripts grepping
        // ui_prefs.notify_events.<event>) keep working when the user
        // hasn't routed events per-medium. Mixed dicts pass through
        // untouched.
        const formEvents = this.profileForm.notify_events || {};
        const mediums = this.notifyMediumNames();
        const events = {};
        for (const bare in formEvents) {
          const slot = formEvents[bare];
          if (slot && typeof slot === 'object') {
            const vals = mediums.map(m => slot[m] !== false);
            const allSame = vals.every(v => v === vals[0]);
            events[bare] = allSame ? !!vals[0] : {...slot};
          } else {
            events[bare] = !!slot;
          }
        }
        const r2 = await fetch('/api/me/notify-prefs', {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(events),
        });
        if (!r2.ok) {
          const j = await r2.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
          return;
        }
        this.showToast(this.t('toasts.profile_saved'));
        // Refresh `me` so the avatar badge / dropdown header / resolved
        // notify_events all reflect the persisted state. syncProfileForm
        // re-baselines so the amber ring + Unsaved indicator clear.
        const rm = await fetch('/api/me');
        if (rm.ok) {
          this.me = await rm.json();
          this.syncProfileForm();
        }
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      } finally {
        this.profileBusy = false;
      }
    },

    // --- Profile: avatar upload ------------------------------------------
    async uploadAvatar(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) {
        return;
      }
      // Client-side sanity — server re-validates.
      if (!/^image\//.test(file.type)) {
        this.showToast(this.t('toasts.pick_image'), 'error');
        return;
      }
      if (file.size > 1000000) {
        this.showToast(this.t('toasts.image_too_large'), 'error');
        return;
      }
      this.avatarBusy = true;
      try {
        const fd = new FormData();
        fd.append('file', file);
        const r = await fetch('/api/me/avatar', {method: 'POST', body: fd});
        if (r.ok) {
          this.showToast(this.t('toasts.avatar_updated'));
          const rm = await fetch('/api/me');
          if (rm.ok) {
            this.me = await rm.json();
          }
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.upload_failed'), 'error');
        }
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      } finally {
        this.avatarBusy = false;
        ev.target.value = '';
      }
    },

    async clearAvatar() {
      if (!confirm(this.t('settings.profile.avatar_prompt_remove'))) {
        return;
      }
      try {
        const r = await fetch('/api/me/avatar', {method: 'DELETE'});
        if (r.ok) {
          this.showToast(this.t('toasts.avatar_removed'));
          const rm = await fetch('/api/me');
          if (rm.ok) {
            this.me = await rm.json();
          }
        }
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      }
    },
  });
}

// Expose the factory on `window` so the markup-side
// `x-data="app()"` expression resolves. Pre-module the factory was
// declared as a top-level `function app()` in a classic script which
// implicitly attached it to `window`; ES modules have their own
// scope so the assignment is explicit now.
window.app = app;
