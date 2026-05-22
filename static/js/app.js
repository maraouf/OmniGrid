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
  return _mergeKeepDescriptors({}, appMinorTools, appTuning, appStats, appOps, appProviders, appHostsGrid, appNotifyAdmin, appTopbar, appHostDrawer, appCommandPalette, appCharts, appHostsEditor, appAi, appLogs, appHostGroups, appPortainer, appOidc, appUsersAdmin, appSchedules, appBackups, appSsh, appNotificationsPopup, appAuth, appTelegram, appAsset, appSse, appKeyboard, appIconResolvers, appI18n, appUtils, {
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
    view: (['stacks', 'services', 'nodes', 'hosts', 'history', 'settings', 'admin', 'stats'].includes(localStorage.getItem('view')) ? localStorage.getItem('view') : 'stacks'),
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
    // every time they reload Host Stats to re-test after a config
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
      {id: 'general', label: 'General', icon: 'sliders'},
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
      {id: 'host_groups', label: 'Host Groups', icon: 'layers'},
      {id: 'hosts', label: 'Hosts', icon: 'server'},
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
    logPollHandle: null,
    // Display order for the weekday picker. Mon=0..Sun=6 matches the
    // backend's Python tm_wday convention; labels are i18n keys.
    weekdayOrder: [0, 1, 2, 3, 4, 5, 6],
    // Admin view state
    adminTab: 'users',
    // Settings → Host stats provider tabs. Persists in
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
      // the same host every time they reload Host Stats.
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
        this.loadHosts();
      }
      // `updateCacheLabel` was retired (the "fresh / cached
      // Xs ago" topbar text was removed as confusing alongside the
      // unified picker). The 1s timer that drove it is gone too — no
      // sense burning a tick per second on a no-op.
      // Tick `hostHistoryNow` every second so the host-drawer charts'
      // "Updated Xs/Xm/Xh ago" label counts in real time.
      // Operator needs the seconds digit to tick visibly — a 30s
      // cadence made the label feel frozen. One int assignment per
      // second is a negligible cost; Alpine only re-evaluates the
      // freshness helper on bound elements (the small span near the
      // time-range picker), so most renders are skipped.
      this.hostHistoryNow = Date.now();
      setInterval(() => {
        this.hostHistoryNow = Date.now();
      }, 1000);
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

    // -----------------------------------------------------------------
    // Admin view — nav + data loaders + actions.
    // -----------------------------------------------------------------
    navItems() {
      // Settings + Admin live in the avatar dropdown, not the top nav —
      // the top nav stays focused on the fleet views.
      return [
        ['stacks', this.t('nav.stacks')],
        ['services', this.t('nav.services')],
        ['nodes', this.t('nav.nodes')],
        ['hosts', this.t('nav.hosts')],
        ['history', this.t('nav.history')],
      ];
    },
    // Inner-SVG markup for each top-nav icon. Returned as an HTML
    // string rendered via `x-html` on a shared <svg> wrapper so the
    // stroke / viewBox / size stay consistent. Lucide-derived shapes —
    // layered-squares for Stacks, cube for Services, server-rack for
    // Nodes, monitor for Hosts, clock-with-arrow for History.
    navIcon(key) {
      const icons = {
        stacks: '<path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
        services: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
        nodes: '<rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
        hosts: '<rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
        history: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
      };
      return icons[key] || '';
    },

    toggleNode(name) {
      const i = this.expanded.indexOf('node:' + name);
      if (i >= 0) {
        this.expanded.splice(i, 1);
      } else {
        this.expanded.push('node:' + name);
      }
    },
    isNodeExpanded(name) {
      return this.expanded.includes('node:' + name);
    },
    expandAllNodes() {
      const keys = this.nodeGroups().map(n => 'node:' + n.name);
      // Preserve non-node entries already in expanded (e.g. stacks).
      const others = this.expanded.filter(k => !k.startsWith('node:'));
      this.expanded = [...others, ...keys];
    },
    collapseAllNodes() {
      this.expanded = this.expanded.filter(k => !k.startsWith('node:'));
    },
    expandAllHosts() {
      // Skip dead / no-data rows — same rule as manual toggle.
      const alive = (this.hosts || []).filter(h => this.isHostExpandable(h));
      this.hostsExpanded = alive.map(h => h.host);
      // Warm history for every expanded host on bulk-open.
      for (const h of alive) {
        const key = this.hostHistoryKey(h);
        if (key && (h.beszel_id || h.ne_url || h.pulse_name || h.webmin_name) && !this.hostHistory[key]) {
          this.loadHostHistory(h.beszel_id || '', h.id);
        }
      }
    },
    collapseAllHosts() {
      this.hostsExpanded = [];
    },

    // "Is there an in-flight prune_node op targeting this host?". Drives
    // the button's spinner + disabled state so rapid double-clicks don't
    // queue a second prune — activeOps is the same list the ops panel reads.
    isPruneBusy(host) {
      return (this.activeOps || []).some(o =>
        o.op_type === 'prune_node' && o.target_id === host
      );
    },

    async pruneNode(host, opts) {
      // Confirm first — `docker system prune --volumes` is destructive:
      // stopped containers go away, dangling images, unused networks, AND
      // unused volumes (which can carry data users forgot was orphaned).
      const skipConfirm = !!(opts && opts.skipConfirm);
      if (!skipConfirm) {
        const res = await Swal.fire({
          title: this.t('admin.nodes.prune_prompt_title', {host}),
          html: this.t('admin.nodes.prune_prompt_html', {host}),
          icon: 'warning',
          showCancelButton: true,
          confirmButtonText: this.t('actions.prune_now'),
          cancelButtonText: this.t('actions.cancel'),
          confirmButtonColor: this._cssVar('--danger'),
        });
        if (!res.isConfirmed) {
          return;
        }
      }
      try {
        const r = await fetch('/api/prune/node/' + encodeURIComponent(host), {method: 'POST'});
        if (r.ok) {
          // Auto-expand the floating ops panel (bottom-right) so a new
          // user actually SEES where the live progress lives. Without
          // this, the toast refers to a panel that only appears briefly
          // and stays collapsed if they've dismissed it before.
          this.opsExpanded = true;
          this.showToast(this.t('toasts_extra.prune_started', {host}));
          // Kick an immediate ops poll so the button flips to "Pruning…".
          this.pollOnce && this.pollOnce();
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts_extra.save_failed_generic'), 'error');
        }
      } catch (_) {
        this.showToast(this.t('toasts.network_error'), 'error');
      }
    },

    // Single predicate for "can this user do writes?". Every write button
    // is gated on this so readonly users see a clean read-only UI instead
    // of a button that just returns 403.
    isAdmin() {
      return !!(this.me && this.me.role === 'admin');
    },
    isReadonly() {
      return !!(this.me && this.me.role === 'readonly');
    },


    // Avatar helpers — deterministic colour per username so "alice" always
    // gets the same hue across refreshes. Uses HSL in CSS so the same
    // hue value produces a pleasant colour in both light and dark themes
    // (the token `--avatar-hue` feeds into a hsl() in style.css).
    initial() {
      if (!this.me || !this.me.username) {
        return '?';
      }
      const c = this.me.username.trim().charAt(0);
      return c ? c.toUpperCase() : '?';
    },

    // URL routing helpers — keep the path in sync with view + section
    // so a browser refresh or shared link lands on the same screen.
    //
    // Path shape:
    // /                            → stacks (default)
    // /{view}                      → top-level view
    // /settings/{section}          → Settings sidebar section
    // /admin/{tab}                 → Admin sidebar tab
    // Unknown paths are left alone (the static file server already
    // handles login / assets; this only intervenes for known views).
    _routeViews() {
      return new Set(['stacks', 'services', 'nodes', 'hosts', 'history', 'settings', 'admin', 'stats']);
    },
    _applyRouteFromPath() {
      const parts = (location.pathname || '/').split('/').filter(Boolean);
      if (!parts.length) {
        return;
      }
      const head = parts[0];
      if (!this._routeViews().has(head)) {
        return;
      }
      // Only assign if the current state differs, so this doesn't
      // thrash re-renders when the pushState we just wrote fires
      // popstate-like flows (it doesn't — noted for future-proofing).
      if (this.view !== head) {
        this.view = head;
      }
      const sub = parts[1];
      if (head === 'settings' && sub) {
        if ((this.settingsSections || []).some(s => s.id === sub)) {
          this.settingsSection = sub;
        }
      } else if (head === 'admin' && sub) {
        if ((this.adminSections || []).some(s => s.id === sub)) {
          // Use openAdminTab so the tab-specific load fires (users,
          // sessions, schedules, etc).
          this.openAdminTab(sub);
        }
      } else if (head === 'stats') {
        // /stats with no sub-tab defaults to the current statsTab
        // (init 'dashboard'); /stats/<id> opens that sub-tab. Always
        // route through openStatsTab so the matching loader fires —
        // without this the dashboard's loading-spinner never resolves
        // because loadStatsOverview() was never invoked.
        const target = (sub && (this.statsSections || []).some(s => s.id === sub))
          ? sub
          : (this.statsTab || 'dashboard');
        this.openStatsTab(target);
      }
    },
    _pushRoute() {
      let path = '/' + (this.view || 'stacks');
      if (this.view === 'settings' && this.settingsSection) {
        path += '/' + this.settingsSection;
      } else if (this.view === 'admin' && this.adminTab) {
        path += '/' + this.adminTab;
      } else if (this.view === 'stats' && this.statsTab) {
        path += '/' + this.statsTab;
      }
      // replaceState rather than pushState so refresh lands on the
      // same page without adding history entries per tab switch.
      // Back/forward via actual nav (hash changes, manual link) still
      // work because popstate runs _applyRouteFromPath.
      if (location.pathname !== path) {
        try {
          history.replaceState(null, '', path);
        } catch (_) {
        }
      }
    },

    async openStatsTab(tab) {
      // Stats view (admin-only) — mirrors openAdminTab's shape: switch
      // view, set sub-tab, fire the matching loader.
      this.view = 'stats';
      this.statsTab = tab || 'dashboard';
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem('statsTab', this.statsTab);
        }
      } catch (_) {
        /* private mode */
      }
      if (this.statsTab === 'dashboard') {
        await this.loadStatsOverview();
      } else {
        if (this.statsTab === 'database') {
          await this.loadStatsDatabase();
        } else {
          if (this.statsTab === 'samples') {
            await this.loadStatsSamples();
          } else {
            if (this.statsTab === 'incidents') {
              await this.loadStatsIncidents();
            } else {
              if (this.statsTab === 'network') {
                await this.loadStatsNetwork();
              } else {
                if (this.statsTab === 'ai_cost') {
                  await this.loadStatsAiCost();
                }
              }
            }
          }
        }
      }
      this._pushRoute && this._pushRoute();
    },
    sortStatsSamplesBy(col) {
      if (this.statsSamplesSortBy === col) {
        // Same column → flip direction.
        this.statsSamplesSortDir = (this.statsSamplesSortDir === 'asc') ? 'desc' : 'asc';
      } else {
        this.statsSamplesSortBy = col;
        // Sensible default direction per column type: numeric / time
        // columns sort desc-first (largest / newest); string columns
        // sort asc-first (alphabetical).
        const isNum = (col === 'rows' || col === 'unique_hosts'
          || col === 'oldest_ts' || col === 'newest_ts');
        this.statsSamplesSortDir = isNum ? 'desc' : 'asc';
      }
    },
    // Per-provider drill-down modal — fetches per-host row counts for
    // ONE sample-bearing table, sorted DESC. Footer total cross-checks
    // against the outer per-table count rendered on the Samples page.
    async openStatsSamplesDrillDown(row) {
      if (!row || !row.name) {
        return;
      }
      const label = (row.provider || '') + ' — ' + (row.name || '');
      this.statsSamplesDrillDown = {
        open: true,
        loading: true,
        table: row.name,
        // Provider tag exposed to row-context helpers (e.g. the
        // Portainer `stats_samples` table uses `item_id` referring
        // to containers, NOT hosts — so the "no longer curated"
        // marker text + the orphan-delete button copy adapt).
        provider: (row.provider || '').toLowerCase(),
        host_col: '',
        label: label,
        rows: [],
        total: 0,
        outer: Number(row.rows || 0),  // stale-fallback only — backend overwrites with fresh snapshot
        error: '',
        // Per-row prune busy-state map keyed by host_id. Prevents
        // rapid clicks on the same Delete button from firing twice.
        pruning: {},
      };
      try {
        const r = await fetch('/api/admin/stats/samples/by-host?table='
          + encodeURIComponent(row.name));
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.statsSamplesDrillDown.error = (d && d.detail) || ('HTTP ' + r.status);
          return;
        }
        this.statsSamplesDrillDown.rows = Array.isArray(d.rows) ? d.rows : [];
        this.statsSamplesDrillDown.total = Number(d.total || 0);
        this.statsSamplesDrillDown.host_col = d.host_col || '';
        // Backend's fresh outer_count is the authoritative number for
        // the cross-check (sampled in the same SELECT snapshot as the
        // per-host GROUP BY, so they MUST match unless there's a real
        // SQL bug). The stale `row.rows` from the Samples-page load
        // stays as a fallback only.
        if (d.outer_count !== undefined && d.outer_count !== null) {
          this.statsSamplesDrillDown.outer = Number(d.outer_count);
        }
        if (d.error) {
          this.statsSamplesDrillDown.error = d.error;
        }
      } catch (e) {
        this.statsSamplesDrillDown.error = (e && e.message) || String(e);
      } finally {
        this.statsSamplesDrillDown.loading = false;
      }
    },
    closeStatsSamplesDrillDown() {
      this.statsSamplesDrillDown = {
        open: false, loading: false, table: '', provider: '',
        host_col: '', label: '', rows: [], total: 0, outer: 0,
        error: '', pruning: {},
      };
    },
    // Delete all rows in <table> for one host_id (orphan or
    // intentional cleanup). Audit-logged on the backend via the
    // `samples_prune_orphan` op_type so History shows what got
    // pruned + when + by whom.
    async pruneStatsSampleRows(row) {
      if (!row || !row.host_id) {
        return;
      }
      const table = this.statsSamplesDrillDown.table;
      if (!table) {
        return;
      }
      if (this.statsSamplesDrillDown.pruning[row.host_id]) {
        return;
      }
      const hostId = row.host_id;
      const rowCount = Number(row.rows || 0).toLocaleString();
      const ok = await this.confirmDialog({
        title: this.t('stats.samples.drill_down.prune_confirm_title')
          || 'Delete sample rows?',
        html: this.t('stats.samples.drill_down.prune_confirm_html', {id: hostId, count: rowCount, table})
          || ('Delete <strong>' + rowCount + '</strong> rows from <code>' + table
            + '</code> for <code>' + hostId + '</code>? This cannot be undone.'),
        icon: 'warning',
        confirmText: this.t('stats.samples.drill_down.prune_confirm_ok') || 'Delete',
        confirmColor: this._cssVar('--danger'),
        focusConfirm: false,
      });
      if (!ok) {
        return;
      }
      this.statsSamplesDrillDown.pruning[row.host_id] = true;
      try {
        const r = await fetch('/api/admin/stats/samples/by-host', {
          method: 'DELETE',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({table, host_id: hostId}),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.showToast(this.t('toasts.failed_with_error', {
            error: (d && d.detail) || ('HTTP ' + r.status),
          }), 'error');
          return;
        }
        this.showToast(
          this.t('stats.samples.drill_down.prune_success', {
            count: Number(d.deleted || 0).toLocaleString(), id: hostId,
          }) || (Number(d.deleted || 0).toLocaleString() + ' rows deleted for ' + hostId),
          'success',
        );
        // Re-fetch the drill-down so the row disappears + totals update.
        const refreshed = await fetch('/api/admin/stats/samples/by-host?table='
          + encodeURIComponent(table));
        const refData = await refreshed.json().catch(() => ({}));
        if (refreshed.ok) {
          this.statsSamplesDrillDown.rows = Array.isArray(refData.rows) ? refData.rows : [];
          this.statsSamplesDrillDown.total = Number(refData.total || 0);
          if (refData.outer_count !== undefined && refData.outer_count !== null) {
            this.statsSamplesDrillDown.outer = Number(refData.outer_count);
          }
        }
      } catch (e) {
        this.showToast(this.t('toasts.failed_with_error', {
          error: (e && e.message) || String(e),
        }), 'error');
      } finally {
        this.statsSamplesDrillDown.pruning[row.host_id] = false;
      }
    },

    async openAdminTab(tab) {
      // Stop the logs poller when leaving the Logs tab; it restarts
      // when the tab is opened again. Keeps network traffic silent
      // while the operator is elsewhere.
      if (this.adminTab === 'logs' && tab !== 'logs') {
        this._stopLogPoll();
      }
      this.adminTab = tab;
      if (tab === 'users') {
        await this.loadUsers();
      } else {
        if (tab === 'sessions') {
          await this.loadSessions();
        } else {
          if (tab === 'tokens') {
            await this.loadTokens();
          } else {
            if (tab === 'backups') {
              await this.loadBackups();
            } else {
              if (tab === 'config_backup') {
                await this.loadConfigBackupSaved();
              } else {
                if (tab === 'schedules') {
                  // Fire both loads in parallel — the scheduled table and the queue
                  // table aren't related state-wise, no reason to wait on each other.
                  await Promise.all([this.loadSchedules(), this.loadScheduleQueue()]);
                } else if (tab === 'logs') {
                  await this.loadLogs(true);
                  // Logs tab also renders the `tuning_log_retention_days`
                  // settings card (moved from Process tunables) so it needs
                  // the tuningForm/tuningEffective state too. Cheap call,
                  // dedupes against `tuningLoaded` so a re-open doesn't double
                  // fetch.
                  if (!this.tuningLoaded) {
                    await this.loadTuning();
                  }
                  this._startLogPoll();
                }
                  // The four ex-Settings sections all read from the same /api/settings
                  // payload, so a single load covers all of them. Load on every
                // open so edits from another tab don't go stale.
                else if (['notifications', 'general', 'portainer', 'oidc', 'providers'].includes(tab)) {
                  await this.loadSettings();
                  // Webmin section in Providers also renders a tunable card
                  // (tuning_webmin_probe_budget_seconds); ensure tuning state
                  // is available the first time the operator visits.
                  if (tab === 'providers' && !this.tuningLoaded) {
                    await this.loadTuning();
                  }
                  // Notifications tab now hosts the relocated
                  // tuning_notification_retention_days card; same lazy-load
                  // pattern as Providers so the bounds-chips + effective-value
                  // chip render on first visit instead of waiting for the
                  // operator to bounce through Admin → Config first.
                  if (tab === 'notifications' && !this.tuningLoaded) {
                    await this.loadTuning();
                  }
                  // The Ping test-target picker reads from `hostsConfig` (loaded
                  // by the Hosts admin tab). When the operator opens the Providers
                  // tab without ever visiting Admin → Hosts in this session, the
                  // picker is empty and the dropdown shows "No ping-enabled hosts"
                  // even though there are some. Lazy-load on first visit.
                  if (tab === 'providers' && !(Array.isArray(this.hostsConfig) && this.hostsConfig.length)) {
                    this.loadHostsConfig().catch(() => {
                    });
                  }
                } else if (tab === 'hosts') {
                  await this.loadHostsConfig();
                  // Host groups live in /api/settings; load it alongside so the
                  // groups editor at the bottom of this tab has current data.
                  await this.loadSettings();
                } else if (tab === 'assets') {
                  await this.loadSettings();
                  await this.loadAssetCache();
                } else if (tab === 'ai') {
                  // Hydrates the per-provider form state + the dashboard.
                  // Two parallel calls — settings primes the form, dashboard
                  // primes the tile grid. Failure of either is non-fatal: the
                  // partial degrades to an empty-state.
                  await Promise.all([this.loadSettings(), this.loadAiDashboard(true)]);
                  // AI tab also renders the relocated `tuning_ai_retry_*`
                  // tunables (sub-section "Auto-retry on transient overload");
                  // ensure tuning state is hydrated on first visit so the
                  // section's `x-show="tuningLoaded"` gate fires and the
                  // bounds-chips / effective-value / form bindings render
                  // instead of staying invisible. Same lazy-load pattern as
                  // providers / notifications / logs tabs.
                  if (!this.tuningLoaded) {
                    await this.loadTuning();
                  }
                } else if (tab === 'config') {
                  await this.loadTuning();
                }
                  // Port Scan admin tab — same lazy-load pattern as Host stats /
                  // Notifications / Logs / AI: the four port-scan tunables
                  // (timeout / concurrency / max_seconds / banner_read) bind to
                  // `tuningForm[...]`, so first-visit needs the tuning state
                  // hydrated before the inputs render. Also load settings so
                // `port_scan_enabled` + `port_scan_default_ports` round-trip.
                else if (tab === 'port_scan') {
                  await this.loadSettings();
                  if (!this.tuningLoaded) {
                    await this.loadTuning();
                  }
                }
              }
            }
          }
        }
      }
    },

    // Local JSON-validate so the operator gets feedback without a round-trip.
    // Empty string is normalised to {} so the common case (gather_refresh
    // has no params) doesn't require typing braces.
    _parseParamsText(raw) {
      const trimmed = (raw || '').trim();
      if (!trimmed) {
        return {};
      }
      let parsed;
      try {
        parsed = JSON.parse(trimmed);
      } catch (_) {
        throw new Error(this.t('admin.schedules.params_invalid_json'));
      }
      if (parsed == null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error(this.t('admin.schedules.params_must_be_object'));
      }
      return parsed;
    },

    // Build + validate the payload fields that correspond to the cadence
    // bundle. Returns null after toasting an error, so the caller can
    // early-return without wrapping in try/catch.
    _buildCadencePayload(s) {
      const mode = s.cadence_mode || 'interval';
      if (!['interval', 'daily', 'weekly', 'monthly'].includes(mode)) {
        this.showToast(this.t('admin.schedules.cadence_invalid'), 'error');
        return null;
      }
      if (mode === 'interval') {
        // Clear anchors when flipping back to interval mode so the
        // backend stops consulting stale values.
        return {
          cadence_mode: 'interval',
          run_at_hhmm: null,
          days_of_week: null,
          day_of_month: null,
        };
      }
      const hhmm = (s.run_at_hhmm || '').trim();
      if (!hhmm || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(hhmm)) {
        this.showToast(this.t('admin.schedules.hhmm_invalid'), 'error');
        return null;
      }
      if (mode === 'daily') {
        return {
          cadence_mode: 'daily',
          run_at_hhmm: hhmm,
          days_of_week: null,
          day_of_month: null,
        };
      }
      if (mode === 'weekly') {
        const dow = (s.days_of_week || []).map(d => parseInt(d, 10))
          .filter(n => Number.isInteger(n) && n >= 0 && n <= 6);
        if (!dow.length) {
          this.showToast(this.t('admin.schedules.weekly_needs_days'), 'error');
          return null;
        }
        return {
          cadence_mode: 'weekly',
          run_at_hhmm: hhmm,
          days_of_week: dow,
          day_of_month: null,
        };
      }
      // monthly
      const dom = parseInt(s.day_of_month, 10);
      if (!Number.isInteger(dom) || dom < 1 || dom > 31) {
        this.showToast(this.t('admin.schedules.monthly_dom_invalid'), 'error');
        return null;
      }
      return {
        cadence_mode: 'monthly',
        run_at_hhmm: hhmm,
        days_of_week: null,
        day_of_month: dom,
      };
    },

    toggleWeekday(s, day) {
      // Stable-order toggle so the stored array matches what the user
      // clicked; backend re-sorts anyway but this keeps re-open state
      // predictable.
      const arr = Array.isArray(s.days_of_week) ? [...s.days_of_week] : [];
      const i = arr.indexOf(day);
      if (i >= 0) {
        arr.splice(i, 1);
      } else {
        arr.push(day);
      }
      s.days_of_week = arr;
    },

    // --- Scheduler display helpers --------------------------------------
    // Reuse fmtDuration for last-duration column (same d/h/m/s bucketing).
    // humanInterval is similar but operates on the schedule's configured
    // interval — keep them separate so fmtDuration stays generic.
    humanInterval(sec) {
      if (!sec || sec <= 0) {
        return '—';
      }
      const d = Math.floor(sec / 86400);
      const h = Math.floor((sec % 86400) / 3600);
      const m = Math.floor((sec % 3600) / 60);
      const s = sec % 60;
      const parts = [];
      if (d) {
        parts.push(d + 'd');
      }
      if (h) {
        parts.push(h + 'h');
      }
      if (m) {
        parts.push(m + 'm');
      }
      if (!parts.length) {
        parts.push(s + 's');
      }
      // Keep it tight — two units max for readability ("1d 6h", not "1d 6h 15m 30s").
      return parts.slice(0, 2).join(' ');
    },

    // "5 minutes ago" / "in 2 hours" — used for Last execution / Next
    // execution columns. Pure JS, no dependency. Returns '—' for unset
    // timestamps so the column renders a visible placeholder.
    humanRelTime(epoch) {
      if (!epoch) {
        return '—';
      }
      const delta = Math.round(epoch - (Date.now() / 1000));
      const abs = Math.abs(delta);
      let value, unit;
      if (abs < 60) {
        value = abs;
        unit = 'second';
      } else if (abs < 3600) {
        value = Math.round(abs / 60);
        unit = 'minute';
      } else if (abs < 86400) {
        value = Math.round(abs / 3600);
        unit = 'hour';
      } else {
        value = Math.round(abs / 86400);
        unit = 'day';
      }
      const suffix = value === 1 ? '' : 's';
      return delta >= 0
        ? this.t('admin.schedules.rel_in', {value, unit: unit + suffix})
        : this.t('admin.schedules.rel_ago', {value, unit: unit + suffix});
    },

    // Used only for the "Next execution" column — a past epoch there is
    // noise (the backend now always returns a future timestamp for
    // clock-anchored cadences, so this only fires for schedules the
    // tick loop is about to fire on its next pass).
    humanNextRun(epoch) {
      if (!epoch) {
        return '—';
      }
      const delta = Math.round(epoch - (Date.now() / 1000));
      if (delta <= 60) {
        return this.t('admin.schedules.due_soon');
      }
      return this.humanRelTime(epoch);
    },

    // One-line summary of how the schedule fires, for the table row.
    // Falls back to interval display if cadence_mode is missing (legacy rows).
    cadenceLabel(s) {
      const mode = s.cadence_mode || (s.run_at_hhmm ? 'daily' : 'interval');
      if (mode === 'daily' && s.run_at_hhmm) {
        return this.t('admin.schedules.daily_at', {hhmm: s.run_at_hhmm});
      }
      if (mode === 'weekly' && s.run_at_hhmm) {
        const days = (s.days_of_week || [])
          .map(d => this.t('admin.schedules.weekdays_short.' + d))
          .filter(Boolean)
          .join(', ');
        return this.t('admin.schedules.weekly_at', {days, hhmm: s.run_at_hhmm});
      }
      if (mode === 'monthly' && s.run_at_hhmm && s.day_of_month) {
        return this.t('admin.schedules.monthly_at', {
          dom: s.day_of_month, hhmm: s.run_at_hhmm,
        });
      }
      return this.humanInterval(s.interval_seconds);
    },

    // Multi-source selector helpers. ``host_stats_source`` is now a
    // CSV ("beszel,node_exporter") — these helpers let the Settings
    // checkboxes treat it as a set.
    hasHostStatsSource(name) {
      // Port-scan is an on-demand provider with its own master
      // toggle (`port_scan_enabled`), not a continuous-telemetry
      // provider in the `host_stats_source` CSV. Surface its
      // active-state through the same predicate so the tab strip's
      // dot indicator reads the right gate.
      if (name === 'port_scan') {
        return !!this.settings.port_scan_enabled;
      }
      // The probe-result providers (http_probe / service_probe) collapsed
      // their dual toggle (CSV inclusion + master enable) down to a single
      // master toggle. Read the master directly so the tab-strip dot
      // updates LIVE on master flip, not only after Save commits the
      // CSV-side mirror.
      if (name === 'http_probe') {
        return !!this.settings.http_probe_enabled;
      }
      if (name === 'service_probe') {
        return !!this.settings.service_probe_enabled;
      }
      const raw = this.settings.host_stats_source || '';
      return raw.split(',').map(s => s.trim()).includes(name);
    },
    // single source of truth for the disabled gate
    // shared by every per-provider admin panel's tuning-knob inputs.
    // Pre-fix each panel inlined `!isAdmin() || tuningSaving ||
    // !hasHostStatsSource('<provider>')` — same predicate, six times,
    // with the provider name as the only delta. Centralising means a
    // future change to the gate (e.g. add a master tuning-locked
    // setting) lands in one place. Each panel's inputs bind via
    // `:disabled="tuneKnobDisabled('<provider>')"`.
    tuneKnobDisabled(provider) {
      return !this.isAdmin() || this.tuningSaving || !this.hasHostStatsSource(provider);
    },
    toggleHostStatsSource(name, on) {
      // Port-scan flips its dedicated master toggle, not the CSV.
      if (name === 'port_scan') {
        this.settings.port_scan_enabled = !!on;
        return;
      }
      const current = new Set(
        (this.settings.host_stats_source || '')
          .split(',').map(s => s.trim()).filter(s => s && s !== 'none'),
      );
      if (on) {
        current.add(name);
      } else {
        current.delete(name);
      }
      this.settings.host_stats_source = current.size
        ? Array.from(current).sort().join(',')
        : 'none';
    },

    // Settings → Host stats provider tab switcher. Persists the
    // chosen tab in localStorage so the page returns to the operator's
    // view on refresh. Mirrors the existing `setRefreshInterval` /
    // localStorage shape.
    setHostStatsTab(name) {
      // Validate against `HOST_STATS_TAB_ORDER` (single source of
      // truth — see canonical-key-set rule in CLAUDE.md). Pre-fix
      // this hardcoded a parallel literal that lagged behind every
      // new tab — `port_scan` shipped in `HOST_STATS_TAB_ORDER` but
      // got silently rejected here, so the tab couldn't be clicked.
      if (!this.HOST_STATS_TAB_ORDER.includes(name)) {
        return;
      }
      this.hostStatsTab = name;
      try {
        localStorage.setItem('hostStatsTab', name);
      } catch {
      }
    },
    // Single source of truth for the strip's tab order. setHostStatsTab
    // already validates against this list; cycleHostStatsTab uses it to
    // implement ←/→ keyboard nav per the WAI-ARIA tablist authoring
    // pattern. New tabs added here automatically participate in
    // keyboard navigation.
    HOST_STATS_TAB_ORDER: ['node_exporter', 'beszel', 'pulse', 'webmin', 'ping', 'snmp', 'http_probe', 'service_probe'],
    // Cycle tabs by ±1, wrapping at both ends. Called from each tab
    // button's @keydown.left / @keydown.right handler. After the tab
    // switches we focus the newly-active button so the focus ring
    // tracks selection (per ARIA tablist guidance).
    cycleHostStatsTab(direction) {
      const order = this.HOST_STATS_TAB_ORDER;
      const cur = order.indexOf(this.hostStatsTab);
      const i = cur < 0 ? 0 : cur;
      const next = (i + (direction > 0 ? 1 : order.length - 1)) % order.length;
      this.setHostStatsTab(order[next]);
      // Re-focus the newly-active tab button so keyboard users see the
      // focus ring track the selected tab. The button carries an id of
      // the form `provider-tab-<key>` so the lookup is deterministic.
      this.$nextTick(() => {
        const el = document.getElementById('provider-tab-' + order[next]);
        if (el && typeof el.focus === 'function') {
          el.focus();
        }
      });
    },

    // Serialise just the host-stats-related subset of settings to a
    // stable string. Used by _hostStatsBaseline and hostStatsDirty()
    // to detect unsaved edits. Password / token fields are always
    // "" on the live form (write-only on the wire), so any typed
    // value naturally marks the form dirty.
    _hostStatsSnapshot() {
      const s = this.settings || {};
      const pick = [
        'host_stats_source',
        'node_exporter_enabled', 'node_exporter_url_template',
        'node_exporter_overrides_json',
        'beszel_hub_url', 'beszel_identity', 'beszel_password',
        'beszel_verify_tls',
        'pulse_url', 'pulse_token', 'pulse_verify_tls',
        'webmin_url', 'webmin_user', 'webmin_password',
        'webmin_verify_tls',
        // ping provider settings flow through the same dirty
        // tracker so saveHostStats picks them up alongside the other
        // providers' fields.
        'ping_enabled', 'ping_default_port', 'ping_use_icmp',
        // port-scan provider — on-demand TCP scanner. Master toggle
        // + global defaults; per-host overrides live on
        // `hosts_config[].port_scan` and ride `saveHostsConfig`.
        // `port_scan_default_timeout` / `port_scan_default_concurrency`
        // legacy plain keys are GONE — they were tracked here despite
        // never being bound in any partial (the UI binds to
        // `tuningForm['tuning_port_scan_default_*']`). Removing them
        // from the pick array eliminates the perpetually-`undefined`
        // baseline entries that masked real dirty-state changes.
        'port_scan_enabled', 'port_scan_default_ports',
        // Port-scan UDP companion (Stage 2). UDP runs under the
        // master `port_scan_enabled` toggle (operator-flagged
        // 2026-05-10 to remove the separate `port_scan_udp_enabled`
        // flag — TCP + UDP enable/disable together). Default UDP
        // ports + UDP tunables still ride the dirty tracker.
        'port_scan_udp_default_ports',
        // SNMP. v3 secret keys behave like beszel_password /
        // webmin_password — `_set` flag indicates persisted state, the
        // `*_key` strings are blanked on the form so any typed value
        // marks dirty. The aliases JSON also rides this dirty list.
        'snmp_default_community', 'snmp_default_version',
        'snmp_default_port', 'snmp_v3_user',
        'snmp_v3_auth_key', 'snmp_v3_priv_key',
        'snmp_aliases_json',
        // HTTP / TLS / DNS probe — master toggle + per-provider
        // chip colour. Without these keys in the snapshot pick list,
        // the baseline JSON omits them and `httpProbeSectionDirty()`
        // compares `settings.http_probe_enabled` against
        // `undefined` — the section reads as dirty forever even
        // after a successful save.
        'http_probe_enabled',
        // Per-service reachability probe — master toggle. Same drift
        // class fix as `http_probe_enabled`.
        'service_probe_enabled',
        // per-provider chip colour overrides.
        'provider_color_beszel', 'provider_color_pulse',
        'provider_color_node_exporter', 'provider_color_webmin',
        'provider_color_ping', 'provider_color_snmp',
        'provider_color_http_probe', 'provider_color_service_probe',
      ];
      const subset = {};
      for (const k of pick) {
        subset[k] = s[k];
      }
      try {
        return JSON.stringify(subset);
      } catch {
        return '';
      }
    },
    // Cheap dirty check — called from the template every render. String
    // comparison is O(length) which is trivial for the ~15-key subset.
    // dirty also when any of the 3 tunables that live in this
    // panel (Webmin probe budget + 2 cache TTLs + NE timeout) has an
    // unsaved change. tuningDirty() walks _allTuningKeys() so it
    // catches the relocated keys correctly.
    hostStatsDirty() {
      return this._hostStatsBaseline !== this._hostStatsSnapshot()
        || this.tuningDirty();
    },
    // In-flight flag for the unified Providers Save button so
    // the spinner / "Saving…" label fires the same way as the
    // per-section Save buttons did pre-fix.
    hostStatsSaving: false,

    async saveHostStats() {
      this.hostStatsSaving = true;
      try {
        await this._saveHostStatsImpl();
      } finally {
        this.hostStatsSaving = false;
      }
    },
    canSaveHttpProbeSection() {
      // No Test-before-Save gate at this section: per-host URLs live
      // on the Admin → Hosts row's `http_probe.urls` field, and the
      // Test button here is a one-shot diagnostic against an operator-
      // typed URL — not a section-level config validation. Save
      // unlocks purely on dirty state; the operator can flip the
      // master toggle on/off and commit without proving a URL works.
      return true;
    },
    httpProbeTestUrl: '',

    // Scheduler settings — currently just the IANA timezone. Blank
    // value clears the override and the scheduler falls back to
    // container-local time. Invalid IANA names return 400 from the
    // backend (zoneinfo.ZoneInfo validates), which we surface as a
    // toast so the operator knows to fix the typo.
    // Persist the Open-Meteo upstream URL (Admin → Notifications).
    // Blank = clear override, fall back to the baked-in default.
    // Admin → Hosts toggle: show / hide the host-drawer debug panel
    // Save the Debug-tab settings via the smart-getter dirty pattern
    // — POSTs the toggle, re-baselines on success so the amber
    // "Unsaved" indicator clears, and reports failure via toast.
    debugSaving: false,
    async saveDebugSettings() {
      if (this.debugSaving) {
        return;
      }
      this.debugSaving = true;
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            debug_panel_enabled: !!this.settings.debug_panel_enabled,
          }),
        });
        if (r.ok) {
          this._debugBaseline = this._debugSnapshot();
          this.showToast(this.t('admin_hosts.debug_panel_toggle_saved'), 'success');
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts_extra.save_failed_generic'), 'error');
        }
      } catch (_) {
        this.showToast(this.t('toasts_extra.network_error_generic'), 'error');
      } finally {
        this.debugSaving = false;
      }
    },

    _startLogPoll() {
      this._stopLogPoll();
      // 2s poll — fast enough for "watch a deploy" UX, slow enough to
      // not hammer the admin API when nothing's happening.
      this.logPollHandle = setInterval(() => {
        if (this.logAuto) {
          this.loadLogs(false);
        }
      }, 2000);
    },

    _stopLogPoll() {
      if (this.logPollHandle) {
        clearInterval(this.logPollHandle);
        this.logPollHandle = null;
      }
    },
    // True when every severity level is on — used by the ALL pill's
    // is-active state. False if any level is off.
    logAllSeverityOn() {
      for (const k of this.logSeverityLevels) {
        if (!this.logSeverityFilter[k]) {
          return false;
        }
      }
      return true;
    },
    async viewLogFile(name) {
      this.logSelectedFile = name;
      await this._fetchLogFileBody();
      // Restart the auto-tail poll for the newly-selected file.
      if (this._logFileTimer) {
        clearInterval(this._logFileTimer);
      }
      this._logFileTimer = setInterval(() => {
        if (this.logsSubTab !== 'files' || !this.logSelectedFile || !this.logFileAutoTail) {
          return;
        }
        this._fetchLogFileBody();
      }, 5000);
    },
    async _fetchLogFileBody() {
      if (!this.logSelectedFile) {
        this.logFileBody = '';
        return;
      }
      try {
        const r = await fetch(
          '/api/admin/logs/files/' + encodeURIComponent(this.logSelectedFile)
          + '?tail=' + this.logFileTailLines,
        );
        if (!r.ok) {
          this.logFileBody = `(unable to read: HTTP ${r.status})`;
          return;
        }
        this.logFileBody = await r.text();
      } catch (e) {
        this.logFileBody = `(network error: ${e.message})`;
      }
    },
    // Parse the file body into the same {ts, stream, text} shape the
    // Live tab uses, so the renderer can reuse `logSeverity` +
    // `colorizeLogText` + the `log-line--<sev>` class scheme.
    // File format from `logic/logs.py:_persist_line`:
    // `2026-04-27T12:34:56Z LEVEL  message body`
    // Where LEVEL is one of ERROR / WARN / SUCCESS / INFO. Any line
    // that doesn't match the regex falls through as raw INFO so a
    // pre-existing file in some other format still renders, just
    // without the timestamp tint.
    parsedLogFileLines() {
      const body = this.logFileBody || '';
      if (!body) {
        return [];
      }
      const lines = body.split('\n');
      // ISO ts + 1 or more spaces + LEVEL token + space + rest.
      const RX = /^(?<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(?<level>ERROR|WARN|SUCCESS|INFO)\s+(?<text>.*)$/;
      const out = [];
      for (const raw of lines) {
        if (!raw) {
          continue;
        }
        const m = RX.exec(raw);
        if (m) {
          const epoch = Date.parse(m.groups.ts) / 1000;
          out.push({
            ts: Number.isFinite(epoch) ? epoch : 0,
            stream: 'file',
            level: m.groups.level.toLowerCase(),  // matches `logSeverity()` output
            text: m.groups.text,
          });
        } else {
          // Non-conforming line — render as INFO with the raw text.
          out.push({ts: 0, stream: 'file', level: 'info', text: raw});
        }
      }
      return out;
    },
    // Same severity filter applied to the Files-tab parsed lines. Uses
    // logSeverityFor (which prefers the parsed `level` field, falls
    // back to `logSeverity` regex) so an INFO row whose body just has
    // "loading..." doesn't get reclassified. Shares `logSeverityFilter`
    // state with the Live tab so toggling a level in either tab carries.
    filteredLogFileLines() {
      const sev = this.logSeverityFilter || {};
      const allOn = this.logSeverityLevels.every(k => sev[k]);
      const lines = this.parsedLogFileLines();
      // Text filter — same shape as the live (stdout) tab. Reuses
      // `logFilter` so a query typed in either tab carries across,
      // matching the existing `logSeverityFilter` cross-tab pattern.
      // Case-insensitive substring match against the parsed line's
      // `text` field (parsedLogFileLines emits {ts, stream, level,
      // text} — earlier draft of this filter checked `body`/`msg`
      // which the file path doesn't populate, so every query
      // returned zero rows).
      const q = (this.logFilter || '').trim().toLowerCase();
      const filterByText = (l) => {
        if (!q) {
          return true;
        }
        const text = (l && (l.text || l.body || l.msg || '')) + '';
        return text.toLowerCase().includes(q);
      };
      if (allOn) {
        return q ? lines.filter(filterByText) : lines;
      }
      return lines.filter(l => !!sev[this.logSeverityFor(l)] && filterByText(l));
    },

    _b64uEncode(buf) {
      const b = new Uint8Array(buf);
      let s = '';
      for (let i = 0; i < b.length; i++) {
        s += String.fromCharCode(b[i]);
      }
      return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    },

    _b64uDecode(s) {
      s = (s || '').replace(/-/g, '+').replace(/_/g, '/');
      while (s.length % 4) {
        s += '=';
      }
      const bin = atob(s);
      const out = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) {
        out[i] = bin.charCodeAt(i);
      }
      return out.buffer;
    },

    relativeWhen(epochSeconds) {
      if (!epochSeconds) {
        return '';
      }
      const now = Date.now() / 1000;
      const diff = Math.max(0, now - Number(epochSeconds));
      if (diff < 60) {
        const n = Math.round(diff);
        return this.t('hosts_extra.metrics.last_updated_seconds', {count: n}) || `${n}s ago`;
      }
      if (diff < 3600) {
        const n = Math.round(diff / 60);
        return this.t('hosts_extra.metrics.last_updated_minutes', {count: n}) || `${n}m ago`;
      }
      if (diff < 86400) {
        const n = Math.round(diff / 3600);
        return this.t('hosts_extra.metrics.last_updated_hours', {count: n}) || `${n}h ago`;
      }
      // Older than 24h → fall back to a date-only render. Goes
      // through `fmtDateOnly` so the operator's chosen Formats
      // preference applies here too.
      return this.fmtDateOnly(Number(epochSeconds));
    },

    copyToClipboard(text) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
          () => this.showToast(this.t('toasts.copied')),
          () => this.showToast(this.t('toasts.copy_failed_manual'), 'error'),
        );
      }
    },
    // Snapshot of the topbar-widget prefs — same baseline+snapshot
    // pattern as admin-tab dirty flags. Toggling the show-clock
    // / show-weather checkboxes or editing the lat/lon/label fields
    // only marks the form dirty; nothing persists until Save is
    // clicked. `headerPrefsDirty()` re-evaluates each render via
    // Alpine reactivity. Re-baselined after every successful save.
    _headerPrefsBaseline: '',
    _headerPrefsSnapshot() {
      return JSON.stringify({
        clk: !!this.headerClockEnabled,
        wth: !!this.headerWeatherEnabled,
        lat: this.headerWeatherLat == null ? '' : String(this.headerWeatherLat),
        lon: this.headerWeatherLon == null ? '' : String(this.headerWeatherLon),
        label: this.headerWeatherLabel || '',
        unit: (this.headerWeatherUnit === 'f') ? 'f' : 'c',
        // AI launcher visibility participates in the dirty/save flow
        // so toggling the checkbox marks the form dirty (amber ring +
        // "Unsaved" pulse-dot) and reverting it back to the original
        // un-marks it. Save is what commits the change to
        // ui_prefs.ai_sidebar_launcher_hidden — auto-save was the
        // pre-fix behaviour and didn't fit the rest of the form's
        // explicit-save model.
        aiLauncherHidden: !!this.aiSidebarLauncherHiddenDraft,
        // Datetime format string. Trimmed so trailing whitespace
        // doesn't make the form look dirty when nothing meaningful
        // changed. Empty draft + empty baseline both serialise as ''
        // → match → not dirty (intentional: no value vs cleared
        // value are equivalent for this preference).
        dtFmt: (this.datetimeFormatDraft || '').trim(),
      });
    },
    headerPrefsDirty() {
      return this._headerPrefsBaseline !== this._headerPrefsSnapshot();
    },
    saveHeaderPrefs() {
      try {
        localStorage.setItem('headerClockEnabled', String(!!this.headerClockEnabled));
        localStorage.setItem('headerWeatherEnabled', String(!!this.headerWeatherEnabled));
        localStorage.setItem('headerWeatherLat', this.headerWeatherLat == null ? '' : String(this.headerWeatherLat));
        localStorage.setItem('headerWeatherLon', this.headerWeatherLon == null ? '' : String(this.headerWeatherLon));
        localStorage.setItem('headerWeatherLabel', this.headerWeatherLabel || '');
        localStorage.setItem('headerWeatherUnit', (this.headerWeatherUnit === 'f') ? 'f' : 'c');
      } catch (_) {
      }
      // Re-baseline after the localStorage write so headerPrefsDirty()
      // returns false on the next render. The server PATCH below is
      // fire-and-forget — its failure shouldn't keep the form marked
      // dirty (operator still saved locally; the cross-device sync
      // can retry on the next save).
      this._headerPrefsBaseline = this._headerPrefsSnapshot();
      // also push to server-side per-user prefs so the same
      // toggles persist cross-device for the same login. Fire-and-
      // forget; localStorage stays the fast path on subsequent loads,
      // but /api/me's ui_prefs is the cross-device source of truth
      // and overrides localStorage on next page load (see init()).
      try {
        // Datetime format goes through the same PATCH so cross-device
        // sync stays the source of truth. Empty / whitespace draft is
        // serialised as null which the backend treats as "clear the
        // override; revert to the SPA default at next render".
        const dtFmtTrimmed = (this.datetimeFormatDraft || '').trim();
        fetch('/api/me/ui-prefs', {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            prefs: {
              headerClockEnabled: !!this.headerClockEnabled,
              headerWeatherEnabled: !!this.headerWeatherEnabled,
              headerWeatherLat: this.headerWeatherLat == null ? null : Number(this.headerWeatherLat),
              headerWeatherLon: this.headerWeatherLon == null ? null : Number(this.headerWeatherLon),
              headerWeatherLabel: this.headerWeatherLabel || null,
              headerWeatherUnit: (this.headerWeatherUnit === 'f') ? 'f' : 'c',
              datetime_format: dtFmtTrimmed || null,
            }
          }),
        }).catch(() => {/* silent — localStorage still has it */
        });
        // Update the live `me.ui_prefs.datetime_format` immediately so
        // every `:value="fmtDate(...)"` binding re-renders with the
        // new format on this very save (the PATCH above is fire-and-
        // forget; we don't wait for it). Empty draft → unset so the
        // SPA falls back to DEFAULT_DATETIME_FORMAT.
        if (this.me) {
          if (!this.me.ui_prefs) {
            this.me.ui_prefs = {};
          }
          this.me.ui_prefs.datetime_format = dtFmtTrimmed || '';
        }
      } catch (_) {
      }
      // Re-fetch with the new settings immediately rather than waiting
      // for the 10-min tick. Also flushes weather to null when disabled.
      this.loadHeaderWeather();
      // Commit the AI-launcher draft (Settings → Profile checkbox)
      // through its existing PATCH helper. The draft is what the
      // checkbox binds to via x-model; save flips the live value
      // and persists to `ui_prefs.ai_sidebar_launcher_hidden`. Done
      // AFTER the snapshot re-baseline above so the dirty-flag
      // clears cleanly post-save.
      if (this.aiSidebarLauncherHiddenDraft !== this.aiSidebarLauncherHidden) {
        try {
          this.setAiSidebarLauncherHidden(!!this.aiSidebarLauncherHiddenDraft);
        } catch (_) {
        }
      }
      // Toast confirmation — per-browser preferences auto-save on
      // change, but operators coming from the per-user Profile section
      // expect a visual "saved" signal.
      if (this.showToast) {
        this.showToast(this.t('toasts_extra.topbar_saved'), 'success');
      }
    },
    // apply server-side ui_prefs onto local state. Called from
    // init() right after /api/me lands. Server is the cross-device
    // source of truth; localStorage is the fast-path cache that gets
    // overwritten when the server has a non-empty pref.
    applyServerUiPrefs() {
      const p = (this.me && this.me.ui_prefs) || {};
      // Only override when the server has an explicit value. An
      // empty {} means "user never saved anything" → leave the
      // localStorage default in place.
      if (typeof p.headerClockEnabled === 'boolean') {
        this.headerClockEnabled = p.headerClockEnabled;
        try {
          localStorage.setItem('headerClockEnabled', String(p.headerClockEnabled));
        } catch (_) {
        }
      }
      if (typeof p.headerWeatherEnabled === 'boolean') {
        this.headerWeatherEnabled = p.headerWeatherEnabled;
        try {
          localStorage.setItem('headerWeatherEnabled', String(p.headerWeatherEnabled));
        } catch (_) {
        }
      }
      if (p.headerWeatherLat != null) {
        this.headerWeatherLat = Number(p.headerWeatherLat);
        try {
          localStorage.setItem('headerWeatherLat', String(p.headerWeatherLat));
        } catch (_) {
        }
      }
      if (p.headerWeatherLon != null) {
        this.headerWeatherLon = Number(p.headerWeatherLon);
        try {
          localStorage.setItem('headerWeatherLon', String(p.headerWeatherLon));
        } catch (_) {
        }
      }
      if (typeof p.headerWeatherLabel === 'string') {
        this.headerWeatherLabel = p.headerWeatherLabel;
        try {
          localStorage.setItem('headerWeatherLabel', p.headerWeatherLabel);
        } catch (_) {
        }
      }
      if (typeof p.headerWeatherUnit === 'string') {
        const u = p.headerWeatherUnit.toLowerCase() === 'f' ? 'f' : 'c';
        this.headerWeatherUnit = u;
        try {
          localStorage.setItem('headerWeatherUnit', u);
        } catch (_) {
        }
      }
      // Datetime format. Server is the only source of truth for this
      // preference (no localStorage cache — the SPA reads via the
      // already-hydrated `this.me.ui_prefs.datetime_format`). The draft
      // mirrors the live value so the form input renders with the
      // current setting and `headerPrefsDirty()` is false on first
      // render.
      this.datetimeFormatDraft = (typeof p.datetime_format === 'string') ? p.datetime_format : '';
    },
    // °C → operator's preferred unit. Returns the formatted string with
    // the unit suffix attached (e.g. `21.3°C` or `70°F`). Backend
    // `/api/weather` ALWAYS returns Celsius; the SPA converts at the
    // render boundary. `decimals` lets the caller pick precision —
    // topbar chip uses 1 (matches the °C-only pre-fix display);
    // forecast min/max uses 0 (the pre-fix Math.round path).
    formatTempPref(c, decimals = 1) {
      if (c == null || !Number.isFinite(+c)) {
        return '';
      }
      const f = (+c) * 9 / 5 + 32;
      const v = (this.headerWeatherUnit === 'f') ? f : (+c);
      const factor = Math.pow(10, Math.max(0, decimals | 0));
      return (Math.round(v * factor) / factor) + (this.headerWeatherUnit === 'f' ? '°F' : '°C');
    },
    // Convert a Celsius value to the operator's preferred unit and
    // return the bare number (no suffix). Used by AI palette context
    // where the JSON payload carries the unit separately.
    convertTempPref(c) {
      if (c == null || !Number.isFinite(+c)) {
        return null;
      }
      if (this.headerWeatherUnit === 'f') {
        return Math.round(((+c) * 9 / 5 + 32) * 10) / 10;
      }
      return Math.round((+c) * 10) / 10;
    },
    // ---- Shared "is this loader running right now" registry --------
    // Used by admin reload buttons to drive the spinning-icon state.
    // Keyed on a free-form string ('users' / 'sessions' / 'tokens' /
    // 'schedules' / 'schedule_queue' / 'backups' / 'logs' /
    // 'log_files' / 'config_backup_saved'). The button binds to
    // `_loadBusy.<key>` for both the spinner class and the
    // `:disabled` attribute; the helper guards against double-fires
    // so a fast-clicking user can't start a second concurrent fetch.
    // The wrapped fn can be sync OR async — `await fn()` works either
    // way thanks to the implicit-promise wrap on a non-thenable value.
    // Pre-declared keys ensure Alpine's reactive Proxy registers each
    // property as a stable tracked path from mount. Adding properties
    // later via `_loadBusy[key] = true` works in most cases but has
    // edge cases (especially when Alpine evaluates the binding before
    // the first write, then races against subsequent writes) where the
    // initial DOM evaluation sticks even after the data clears. Listing
    // every key with `false` up-front side-steps the whole class.
    _loadBusy: {
      users: false, sessions: false, tokens: false,
      schedules: false, schedule_queue: false,
      backups: false, config_backup_saved: false,
      logs: false, log_files: false,
      stats_overview: false, stats_database: false, stats_samples: false,
      stats_incidents: false, stats_network: false, stats_ai_cost: false,
      history: false, hosts_config: false,
    },
    // Watchdog timer per busy-key — see `_runWithBusy` below. Cleared
    // when the inner fn resolves naturally; otherwise the timer force-
    // clears the busy flag after `_LOAD_BUSY_MAX_MS` so a hung Promise
    // can't leave the reload button visually stuck forever.
    _loadBusyWd: {},
    // Watchdog timers for the SSE-pill "refreshing" flags
    // (`cacheRefreshing` / `hubProbing` / `statsRefreshing`). The backend
    // mirrors background-task state into these via response payload
    // fields. If a backend task stalls (or SSE drops silently in Live
    // mode so no fresh response lands to clear them), the SSE pill
    // would stay in `--refreshing` state with its fast-spin animation
    // forever. Watchdog clears the flag after `_LOAD_BUSY_MAX_MS`.
    _refreshingWd: {},
    // Wrapper that mirrors any backend-derived boolean flag into a
    // SPA-side reactive property AND arms a watchdog so the flag can't
    // stay truthy past `_LOAD_BUSY_MAX_MS`. Use for any flag whose
    // truthiness drives a long-running animation (spinner, pulse).
    _setRefreshingFlag(key, value) {
      this[key] = !!value;
      try {
        clearTimeout(this._refreshingWd[key]);
      } catch (_) {
      }
      if (this[key]) {
        this._refreshingWd[key] = setTimeout(() => {
          if (this[key]) {
            this[key] = false;
          }
        }, this._LOAD_BUSY_MAX_MS || 30000);
      } else {
        delete this._refreshingWd[key];
      }
    },
    // Watchdog cap (ms). Operator-tunable via `tuning_load_busy_max_seconds`
    // (Admin → Config). Defaults to 30000 ms (matches the published
    // `/api/hosts/one/{id}` probe budget); range 5..600 seconds.
    // The 30000 literal here is a defence-in-depth fallback for the
    // brief window before `/api/me` hydrates — every real consumer
    // reads through the getter so a save in Admin → Config lands on
    // the next round-trip.
    get _LOAD_BUSY_MAX_MS() {
      const v = this.me && this.me.client_config && this.me.client_config.load_busy_max_ms;
      const n = Number(v);
      return Number.isFinite(n) && n >= 5000 ? n : 30000;
    },
    async _runWithBusy(key, fn) {
      if (!key || typeof fn !== 'function') {
        return;
      }
      if (this._loadBusy[key]) {
        return;
      }
      this._loadBusy[key] = true;
      // Watchdog — if the inner fn hangs (network blip, dead probe,
      // slow listing) clear the busy flag after _LOAD_BUSY_MAX_MS so
      // the reload button doesn't stay disabled across the session.
      // The fn keeps running in the background; when it finally
      // resolves, finally{} would re-clear (already false — no-op).
      try {
        clearTimeout(this._loadBusyWd[key]);
      } catch (_) {
      }
      this._loadBusyWd[key] = setTimeout(() => {
        if (this._loadBusy[key]) {
          this._loadBusy[key] = false;
        }
      }, this._LOAD_BUSY_MAX_MS);
      try {
        await fn();
      } finally {
        this._loadBusy[key] = false;
        try {
          clearTimeout(this._loadBusyWd[key]);
        } catch (_) {
        }
        delete this._loadBusyWd[key];
      }
    },
    async loadVersion() {
      try {
        const r = await fetch('/api/version');
        if (!r.ok) {
          return;
        }
        const d = await r.json();
        this.version = d.version || '';
        // First successful fetch — lock in the boot version so the
        // watcher has something to compare against. Later fetches
        // don't overwrite bootVersion; we want the original forever.
        if (!this.bootVersion && this.version) {
          this.bootVersion = this.version;
        } else if (this.bootVersion && this.version && this.version !== this.bootVersion) {
          // New build is live. Flag once; further ticks are no-ops.
          if (!this.newVersionAvailable) {
            this.newVersionAvailable = true;
            this.newVersionString = this.version;
          }
        }
      } catch (_) {
      }
    },
    // Start a 60s poll of /api/version so a deploy that lands while
    // the operator has the tab open triggers a hard-refresh banner.
    // Idempotent — safe to call from init() even on hot-reload.
    startVersionWatcher() {
      if (this._versionTimer) {
        return;
      }
      this._versionTimer = setInterval(() => this.loadVersion(), 60000);
    },
    // Force a cache-busting reload when the operator clicks the
    // "New version — reload" banner. `location.reload()` alone
    // sometimes serves the cached HTML; adding a query param forces
    // the server to re-send (and the JS/CSS assets use
    // ?v=<version> bust-tokens so they'll re-fetch too).
    // URLSearchParams.set replaces any existing `_v` so consecutive
    // reloads don't append `&_v=...&_v=...&_v=...`.
    reloadForNewVersion() {
      const params = new URLSearchParams(location.search);
      params.set('_v', this.newVersionString || String(Date.now()));
      const qs = params.toString();
      location.href = location.pathname + (qs ? '?' + qs : '') + location.hash;
    },
    pollStats() {
      if (this._statsTimer) {
        clearTimeout(this._statsTimer);
        this._statsTimer = null;
      }
      // Interval of 0 → operator explicitly turned stats polling off.
      // Skip the initial tick entirely (used to fire one extra
      // /api/stats after "Off" was selected) AND don't schedule
      // further ticks. Other call sites like refresh() can still
      // fetch stats on demand; we just stop the periodic timer.
      if (!(this.statsInterval > 0)) {
        // Diagnostic — without this, "no graphs anywhere" looks like
        // a backend bug when the actual cause is that the operator's
        // last session left the stats-interval picker on "Off". One
        // shot still fires below so the operator sees CURRENT data;
        // future ticks are intentionally suppressed.
        // Logged ONCE per session — pre-fix every pollStats invocation
        // (which fires on Live-mode SSE reconnect, view nav, etc.) re-
        // emitted the warning, drowning out actionable console output.
        // The `_pollStatsOffLogged` latch makes it appear once and
        // stay quiet for the rest of the session.
        if (!this._pollStatsOffLogged) {
          console.warn('[stats] pollStats: stats polling is OFF (statsInterval=0). Re-enable it from the topbar interval picker. Firing one diagnostic loadStats() anyway so /api/stats response is logged once.');
          this._pollStatsOffLogged = true;
        }
        try {
          this.loadStats();
        } catch (_) { /* never crash init */
        }
        return;
      }
      const tick = async () => {
        // Bracket the request so the pill flashes green for the
        // exact duration of the /api/stats round-trip.
        this._pollStart();
        try {
          await this.loadStats();
        } finally {
          this._pollEnd();
        }
        if (this.statsInterval > 0) {
          // when SSE is healthy the backend pushes a
          // ``stats:refreshed`` hint after every gather_stats() write;
          // the listener kicks loadStats() then. The fallback timer
          // fires only every 5 minutes as a safety net for the case
          // where the live stream is silently broken AND the
          // freshness watchdog hasn't yet flipped _sseConnected
          // back to false.
          const intervalMs = this._sseConnected
            ? Math.max(this.statsInterval * 1000, 5 * 60 * 1000)
            : this.statsInterval * 1000;
          if (this._sseConnected && !this._statsLiveLogged) {
            console.log('[live] pollStats cadence: SSE up → ' + Math.round(intervalMs / 1000) + 's safety net (push-driven via stats:refreshed)');
            this._statsLiveLogged = true;
          } else if (!this._sseConnected && this._statsLiveLogged) {
            console.log('[live] pollStats cadence: SSE down → ' + this.statsInterval + 's polling');
            this._statsLiveLogged = false;
          }
          this._statsTimer = setTimeout(tick, intervalMs);
        } else {
          this._statsTimer = null;
        }
      };
      tick();
    },
    setStatsInterval(seconds) {
      this.statsInterval = seconds;
      localStorage.setItem('statsInterval', String(seconds));
      // Clear any scheduled tick explicitly when going to 0 —
      // pollStats() returns early but we need to kill an in-flight
      // setTimeout that was scheduled before the switch.
      if (this._statsTimer) {
        clearTimeout(this._statsTimer);
        this._statsTimer = null;
      }
      // Statss interval is the master "live updates" switch — when
      // the operator picks Off (0), kill the hosts-poll timer too
      // (its 15s rebuild causes the row's loading-spinner flash).
      // When they re-enable, restart it for the hosts view.
      if (seconds > 0) {
        if (this.view === 'hosts' && !this._hostsTimer) {
          this._hostsTimer = setInterval(() => {
            this._pollWrap(this.loadHosts());
          }, this.statsInterval * 1000);
        }
      } else if (this._hostsTimer) {
        clearInterval(this._hostsTimer);
        this._hostsTimer = null;
      }
      this.pollStats();
    },
    pollSparks() {
      if (this._sparksTimer) {
        clearInterval(this._sparksTimer);
      }
      // Off mode kills the sparks timer too. The picker's
      // "static snapshot" promise must hold for sparklines as well as
      // every other chart. Live and interval modes stay at the 5min
      // baseline because sparklines are coarse 24h aggregates that
      // barely change tick-to-tick — even with picker=30s, polling
      // sparks every 30s would be wasted bandwidth without visible
      // benefit. The first one-shot loadSparks() also gates on Off
      // so an Off-on-boot doesn't fetch once and freeze.
      if (this.refreshInterval === 0) {
        return;
      }
      this.loadSparks();
      this._sparksTimer = setInterval(() => this.loadSparks(), 5 * 60 * 1000);
    },
    // Build an SVG polyline `points` attribute for one metric of one item.
    // Returns '' when we don't have enough data yet — the caller hides the
    // element with x-show so new installs don't render empty rectangles.
    sparkPoints(item, key) {
      const rows = this.sparks[item && item.id];
      if (!rows || rows.length < 2) {
        return '';
      }
      const W = 60, H = 10;
      const vals = rows.map(r => {
        if (key === 'cpu') {
          return r.cpu || 0;
        }
        if (key === 'mem') {
          return r.mem_limit ? (r.mem_used / r.mem_limit) * 100 : 0;
        }
        // 'disk' → per-item image-disk footprint (size_root, bytes).
        // Snapshot of the image's bytes-on-disk at each sampler tick;
        // sparkline shows image-size drift over time (catches image
        // bloat from rolling-tag updates). Auto-rescales to the
        // window's lo/hi via the shared min-max normalisation below
        // so a flat-ish image renders centred rather than pinned to
        // the bottom edge.
        if (key === 'disk') {
          return r.size_root || 0;
        }
        return 0;
      });
      let lo = Infinity, hi = -Infinity;
      for (const v of vals) {
        if (v < lo) {
          lo = v;
        }
        if (v > hi) {
          hi = v;
        }
      }
      // Empty series → bail (no samples to plot). Disk-specific: when
      // EVERY sample reports size_root=0 we treat it as "no data" so
      // pre-migration deploys (where size_root wasn't persisted yet)
      // hide cleanly instead of rendering an invisible flat-zero
      // hairline. For CPU / mem an all-zero series is meaningful —
      // an idle container legitimately reports cpu_percent=0 and
      // mem_usage may stay flat under the noise floor — so we render
      // the truthful flat line at the baseline rather than dropping
      // to the "Collecting data" hint (the data IS being collected,
      // it's just all zeros).
      if (!Number.isFinite(lo)) {
        return '';
      }
      if (key === 'disk' && hi <= 0) {
        return '';
      }
      // Keep the sparkline visually centred when the signal is flat —
      // map the flat value to the MIDPOINT of the box (not the
      // bottom edge) so an idle 0% CPU renders a visible line
      // instead of being half-clipped by the viewBox boundary. Same
      // fix `nodeSparkPoints` carries.
      if (hi - lo < 0.5) {
        const mid = (lo + hi) / 2;
        lo = mid - 1;
        hi = mid + 1;
      }
      const step = W / (vals.length - 1);
      return vals.map((v, i) => {
        const x = (i * step).toFixed(1);
        const y = (H - ((v - lo) / (hi - lo)) * H).toFixed(1);
        return `${x},${y}`;
      }).join(' ');
    },
    sparkClass(item, key) {
      // Colour follows the CURRENT reading (not the sparkline max) so the
      // line visually agrees with the stat-bar it sits beside.
      const s = this.statsFor(item);
      if (!s || !s.has_stats) {
        return 'muted';
      }
      const v = key === 'cpu' ? s.cpu_percent : this.memPercent(item);
      return this.barLevel(v);
    },

    // Generic in-place reconcile helper. Updates `target` to
    // match `incoming` row-by-row, keyed on the named field
    // (default `id`; stacks pass `'name'` since they don't carry an
    // id). Operations:
    // - existing rows: copy every field from `incoming[i]` onto the
    //   proxied entry (Alpine tracks each assignment individually so
    //   the row's DOM stays mounted),
    // - new rows: push the full incoming dict at the end,
    // - gone rows: splice from the tail.
    // The order of `target` is finally rewritten to match `incoming`'s
    // order via swap-in-place so the row sequence the operator sees
    // tracks server-side ordering without tearing the array down.
    // Used by `refresh()` for `this.items` (key=id) and `this.stacks`
    // (key=name); matches the reconcile pattern `loadHosts` uses for
    // `this.hosts`.
    _reconcileById(target, incoming, keyField = 'id') {
      const keyOf = (r) => (r && r[keyField] != null) ? r[keyField] : null;
      const incomingKeys = new Set(incoming.map(keyOf).filter(k => k != null));
      // Drop rows whose key disappeared. Iterate from the tail so
      // splice indices stay valid.
      for (let i = target.length - 1; i >= 0; i--) {
        if (!incomingKeys.has(keyOf(target[i]))) {
          target.splice(i, 1);
        }
      }
      // Update / insert. After this loop `target` has the right rows
      // but possibly in the wrong order.
      const byKey = new Map();
      for (const row of target) {
        const k = keyOf(row);
        if (k != null) {
          byKey.set(k, row);
        }
      }
      for (const inc of incoming) {
        const k = keyOf(inc);
        if (k == null) {
          continue;
        }
        const existing = byKey.get(k);
        if (existing) {
          for (const f of Object.keys(inc)) {
            existing[f] = inc[f];
          }
        } else {
          target.push({...inc});
          byKey.set(k, target[target.length - 1]);
        }
      }
      // Reorder `target` to match `incoming`'s sequence. Build the
      // final order array and copy elements back into `target` in
      // place — avoids a full reassignment that would re-proxy the
      // array and tear down Alpine's row mounts.
      const ordered = incoming.map(inc => byKey.get(keyOf(inc))).filter(Boolean);
      for (let i = 0; i < ordered.length; i++) {
        if (target[i] !== ordered[i]) {
          target[i] = ordered[i];
        }
      }
      // Trim any trailing slots if the new array is shorter.
      while (target.length > ordered.length) {
        target.pop();
      }
    },

    async refresh(force = false) {
      this.loading = true;
      // Watchdog cap — mirror the `_runWithBusy` pattern so a hung fetch
      // (server not responding, network blip) can't leave the topbar
      // spinner stuck across the session. Cleared on natural resolve.
      const _wd = setTimeout(() => {
          if (this.loading) {
            this.loading = false;
          }
        },
        this._LOAD_BUSY_MAX_MS || 30000);
      try {
        const r = await fetch('/api/items' + (force ? '?force=true' : ''));
        if (!r.ok) {
          throw new Error(await r.text());
        }
        const d = await r.json();
        // in-place reconcile for items + stacks instead of
        // wholesale array reassignment. Keeps Alpine from tearing
        // down each row's checkbox state, <details> open/closed
        // state, and inline-style nodes on every poll. Items are
        // keyed by `id` (the default); stacks have no id field so
        // we key on `name` (the operator-facing stack name is
        // unique within a Swarm).
        // Filter just-removed raw_ids out of the incoming items so a
        // polled refresh that races ahead of the backend's cache
        // invalidation can't re-introduce them. Suppression auto-
        // clears 30s after the bulk-remove (see `_bulkRemoveItems`).
        let incomingItems = d.items || [];
        if (this._recentlyRemovedIds && this._recentlyRemovedIds.size) {
          const supp = this._recentlyRemovedIds;
          incomingItems = incomingItems.filter(it => !supp.has(it && it.raw_id));
        }
        this._reconcileById(this.items, incomingItems);
        this._reconcileById(this.stacks, d.stacks || [], 'name');
        this.nodes = d.nodes || {};
        // Per-node capacity + uptime proxy — see logic/gather.py's nodes_info.
        // Drives the Nodes view's normalized CPU/mem bars.
        this.nodesInfo = d.nodes_info || {};
        // drives the "Portainer not configured" empty-state hint.
        // null → not yet known (skeleton state); true/false → explicit.
        if (typeof d.portainer_configured === 'boolean') {
          this.portainerConfigured = d.portainer_configured;
        }
        // Cache state is implementation detail — the unified refresh
        // picker is the operator's mental model for "how live
        // is this dashboard". Cleared rather than deleted so any
        // remaining bindings render empty instead of crashing.
        this.cacheLabel = '';
        // Background-refresh indicator. /api/items returns
        // `cache_refreshing: true` when the in-memory cache was
        // served instantly + a fresh gather kicked off in
        // background. Drives the topbar refresh button's "Refreshing…"
        // pulse so the operator sees the system is still working
        // even after the foreground call completed. Auto-clears
        // on the next poll once the background gather lands.
        this._setRefreshingFlag('cacheRefreshing', d.cache_refreshing);
        // Fire stats alongside a forced refresh UNLESS the cadence
        // picker is set to Off. Pre-fix this gated on
        // `statsInterval > 0`, but `setRefreshInterval` remaps Live
        // (-1) to legacy `statsInterval = 0`, so a forced refresh in
        // Live mode skipped /api/stats entirely — bars stayed on
        // seeded stale data forever. Now any non-Off mode loads
        // stats; only `refreshInterval === 0` (explicit Off) skips.
        if (force && this.refreshInterval !== 0) {
          this.loadStats(true);
        }
      } catch (e) {
        try {
          this.showToast(this.t('toasts.load_failed', {error: e.message}), 'error');
        } catch (_) {
        }
      } finally {
        this.loading = false;
        try {
          clearTimeout(_wd);
        } catch (_) {
        }
      }
    },
    async loadSettings() {
      try {
        const r = await fetch('/api/settings');
        const d = await r.json();
        this.settings = {
          apprise_url: d.apprise_url || '',
          apprise_tag: d.apprise_tag || '',
          swarm_autoheal_action: (d.swarm_autoheal_action === 'restart') ? 'restart' : 'notify',
          // First-boot auto-bootstrap of a default swarm_agent_health
          // schedule. Backend defaults to true; defensive default
          // here matches so a missing GET key doesn't drop the
          // checkbox to unchecked.
          swarm_autoheal_bootstrap_enabled: (d.swarm_autoheal_bootstrap_enabled !== false),
          portainer_public_url: d.portainer_public_url || '',
          backup_retention_count: Number.isFinite(d.backup_retention_count) ? d.backup_retention_count : 0,
          // Host-stats source + per-provider config. Mutually exclusive —
          // the radio in the Settings panel drives which block's fields
          // actually get persisted when Save is clicked.
          host_stats_source: d.host_stats_source || 'none',
          node_exporter_enabled: !!(d.node_exporter && d.node_exporter.enabled),
          node_exporter_url_template: (d.node_exporter && d.node_exporter.url_template)
            || 'http://{host}:9100/metrics',
          node_exporter_overrides_json: JSON.stringify(
            (d.node_exporter && d.node_exporter.overrides) || {}, null, 2),
          beszel_hub_url: (d.beszel && d.beszel.hub_url) || '',
          beszel_identity: (d.beszel && d.beszel.identity) || '',
          beszel_password: '',  // write-only — never shown
          beszel_password_set: !!(d.beszel && d.beszel.password_set),
          beszel_verify_tls: d.beszel ? d.beszel.verify_tls !== false : true,
          // Per-node aliases: Docker hostname → Beszel system name. Edited
          // from the Nodes view (click a node → drawer). We keep the full
          // object on this.settings so a save for one node preserves the
          // rest of the map.
          beszel_aliases: (d.beszel && d.beszel.aliases) || {},
          // Pulse provider settings — token is write-only on the wire.
          pulse_url: (d.pulse && d.pulse.url) || '',
          pulse_token: '',
          pulse_token_set: !!(d.pulse && d.pulse.token_set),
          pulse_verify_tls: d.pulse ? d.pulse.verify_tls !== false : true,
          pulse_aliases: (d.pulse && d.pulse.aliases) || {},
          // Webmin provider settings — password is write-only. Aliases
          // is Docker hostname → Miniserv base URL per host.
          webmin_url: (d.webmin && d.webmin.url) || '',
          webmin_user: (d.webmin && d.webmin.user) || '',
          webmin_password: '',
          webmin_password_set: !!(d.webmin && d.webmin.password_set),
          webmin_verify_tls: d.webmin ? !!d.webmin.verify_tls : false,
          webmin_aliases: (d.webmin && d.webmin.aliases) || {},
          // Ping provider. No secrets — every field round-trips
          // in the clear. `has_icmp_support` reflects whether icmplib
          // is importable on the server; SPA uses it to disable the
          // ICMP toggle with a hint when the package is missing.
          ping_enabled: !!(d.ping && d.ping.enabled),
          ping_default_port: (d.ping && Number.isFinite(d.ping.default_port)) ? d.ping.default_port : 443,
          ping_use_icmp: !!(d.ping && d.ping.use_icmp),
          ping_has_icmp_support: !!(d.ping && d.ping.has_icmp_support),
          // Port-scan provider — on-demand TCP scanner. `port_scan_enabled`
          // is the master toggle; defaults cascade into per-host
          // overrides on `hosts_config[].port_scan`.
          port_scan_enabled: !!(d.port_scan && d.port_scan.enabled),
          port_scan_default_ports: (d.port_scan && d.port_scan.default_ports) || '',
          // Port-scan UDP companion (Stage 2). UDP runs under the
          // master `port_scan_enabled` toggle (operator-flagged
          // 2026-05-10 to remove the separate flag). `port_scan_udp_enabled`
          // hydrate retained as `true` so any leftover Alpine binding
          // sees a truthy value during the deprecation window — value
          // is otherwise unused.
          port_scan_udp_enabled: true,
          port_scan_udp_default_ports: (d.port_scan && d.port_scan.udp_default_ports) || '',
          port_scan_default_timeout: (d.port_scan && Number.isFinite(d.port_scan.default_timeout)) ? d.port_scan.default_timeout : 2,
          port_scan_default_concurrency: (d.port_scan && Number.isFinite(d.port_scan.default_concurrency)) ? d.port_scan.default_concurrency : 32,
          // SNMP provider. v3 secret keys flow as `_set` flags
          // (write-only contract); community / version / port / aliases
          // round-trip in the clear. `has_snmp_support` reflects whether
          // pysnmp is importable on the server; SPA uses it to disable
          // the ICMP-style "missing dep" hint when the package isn't
          // installed.
          snmp_default_community: (d.snmp && d.snmp.default_community) || 'public',
          snmp_default_version: (d.snmp && d.snmp.default_version) || 'v2c',
          snmp_default_port: (d.snmp && Number.isFinite(d.snmp.default_port)) ? d.snmp.default_port : 161,
          snmp_v3_user: (d.snmp && d.snmp.v3_user) || '',
          snmp_v3_auth_key: '',
          snmp_v3_auth_key_set: !!(d.snmp && d.snmp.v3_auth_key_set),
          snmp_v3_priv_key: '',
          snmp_v3_priv_key_set: !!(d.snmp && d.snmp.v3_priv_key_set),
          // Aliases textarea — same JSON-string pattern as
          // node_exporter_overrides_json so the existing dirty-tracker
          // + JSON-parse-on-save path applies.
          snmp_aliases: (d.snmp && d.snmp.aliases) || {},
          snmp_aliases_json: JSON.stringify((d.snmp && d.snmp.aliases) || {}, null, 2),
          snmp_has_snmp_support: !!(d.snmp && d.snmp.has_snmp_support),
          // actual ImportError text from logic/snmp.py's module-
          // level pysnmp import block. Empty when pysnmp imported
          // cleanly. Surfaced inline in the SPA's "package missing"
          // hint so operators see the ROOT CAUSE without grepping
          // server logs.
          snmp_import_error: (d.snmp && d.snmp.import_error) || '',
          // HTTP / TLS / DNS probe — seventh host-stats provider.
          // Master enable + alias CSV. Per-host overrides land on
          // `hosts_config[].http_probe` and ride the curated-config
          // round-trip.
          http_probe_enabled: !!(d.http_probe && d.http_probe.enabled),
          http_probe_aliases: (d.http_probe && d.http_probe.aliases) || '',
          // Service probe — eighth host-stats provider. Master toggle
          // surfaced at the top level of `/api/settings` (not nested
          // under a `service_probe` sub-object like http_probe is).
          service_probe_enabled: !!d.service_probe_enabled,
          // per-provider chip colour overrides. Empty string
          // means "use the SPA default" (see providerColor() helper).
          provider_color_beszel: d.provider_color_beszel || '',
          provider_color_pulse: d.provider_color_pulse || '',
          provider_color_node_exporter: d.provider_color_node_exporter || '',
          provider_color_webmin: d.provider_color_webmin || '',
          provider_color_ping: d.provider_color_ping || '',
          provider_color_snmp: d.provider_color_snmp || '',
          provider_color_http_probe: d.provider_color_http_probe || '',
          provider_color_service_probe: d.provider_color_service_probe || '',
          // Scheduler — IANA zone. Blank = container-local (legacy).
          scheduler_timezone: d.scheduler_timezone || '',
          // Open-Meteo upstream (weather widget). Blank = default.
          open_meteo_url: d.open_meteo_url || '',
          // Per-service master switches. Default true so legacy
          // deploys keep working before the operator interacts with
          // the toggles.
          apprise_enabled: d.apprise_enabled !== false,
          open_meteo_enabled: d.open_meteo_enabled !== false,
          portainer_enabled: d.portainer_enabled !== false,
          ssh_enabled: d.ssh_enabled !== false,
          asset_inventory_enabled: d.asset_inventory_enabled !== false,
          // Admin → Hosts toggle that controls visibility of the
          // host-drawer debug-data panel. Default true keeps the
          // legacy admin behaviour for fresh installs / pre-toggle
          // databases. Persisted via /api/settings on every flip.
          debug_panel_enabled: d.debug_panel_enabled !== false,
          // AI integration master toggle + active provider — populated
          // here so the Admin → AI tab's master switch + active-provider
          // selector reflect the saved state on first render. Per-provider
          // detail (model / base_url / api_key_set) is hydrated into
          // `this.aiForm` separately by hydrateAiFromSettings(d).
          ai_enabled: !!(d.ai && d.ai.enabled),
          ai_active_provider: (d.ai && d.ai.active_provider) || 'claude',
          ai_max_tokens: (d.ai && Number.isFinite(+d.ai.max_tokens) && +d.ai.max_tokens > 0) ? +d.ai.max_tokens : 1024,
        };
        // Hydrate per-event notification toggles from the GET response.
        // The api_get_settings handler resolves each through
        // get_setting_bool (default true) so we get clean booleans
        // here. Without this hydration the events grid loads unchecked
        // and every Save blindly POSTs all 12 keys as 'false' (since
        // saveSettings normalises `payload[k] ? 'true' : 'false'`),
        // wiping the operator's intended state on every save.
        for (const k of (this.notifyEventKeys || [])) {
          this.settings[k] = !!d[k];
        }
        // Per-medium master switches. Default true when the
        // backend hasn't shipped the field yet (older builds) so the
        // SPA's checkbox doesn't silently default to OFF on a fresh
        // upgrade; matches `NOTIFY_MEDIUM_DEFAULTS` server-side.
        for (const k of (this.notifyMediumKeys || [])) {
          this.settings[k] = (d[k] !== false);
        }
        // Telegram-specific fields. Plain settings (chat_id / thread_id
        // / verify_tls) hydrate directly; the bot token follows the
        // write-only secret contract — `telegram_bot_token` stays
        // empty in the form, the `_set` flag drives the "saved" hint
        // + placeholder copy. Without this hydration the snapshot's
        // chat_id / thread_id would read undefined on first paint and
        // any operator edit would flip dirty against a phantom
        // baseline (and the operator's actual saved values wouldn't
        // appear in the input boxes).
        this.settings.telegram_bot_token = '';
        this.settings.telegram_bot_token_set = !!d.telegram_bot_token_set;
        this.settings.telegram_chat_id = (d.telegram_chat_id || '').toString();
        this.settings.telegram_thread_id = (d.telegram_thread_id || '').toString();
        this.settings.telegram_verify_tls = (d.telegram_verify_tls !== false);
        // Operator-tunable Bot API base URL — blank = upstream default.
        this.settings.telegram_api_base = (d.telegram_api_base || '').toString();
        // Phase 2 — listener config.
        this.settings.telegram_listener_enabled = !!d.telegram_listener_enabled;
        this.settings.telegram_allow_destructive = !!d.telegram_allow_destructive;
        this.settings.telegram_authorized_user_ids = (d.telegram_authorized_user_ids || '').toString();
        // TOTP / 2FA policy. Hydrate the five fields so the
        // Admin -> Config tab can render the inputs + the existing
        // saveSettings flow can ship the values back.
        this.settings.totp_allowed = (d.totp_allowed !== false);
        this.settings.totp_required_for_admins = !!d.totp_required_for_admins;
        this.settings.totp_required_for_users = !!d.totp_required_for_users;
        this.settings.totp_lockout_max_failures =
          Number.isFinite(d.totp_lockout_max_failures) ? d.totp_lockout_max_failures : 5;
        this.settings.totp_lockout_minutes =
          Number.isFinite(d.totp_lockout_minutes) ? d.totp_lockout_minutes : 15;
        // Passkey master toggle. Hydrated alongside the TOTP
        // group because both Save through the same totpPolicySnapshot
        // dirty tracker. Pre-fix the checkbox bound to a never-set
        // `settings.passkeys_allowed`, so on every page load it
        // appeared unchecked even when the DB value was true. Default
        // when the backend omits the key matches the backend's own
        // default (`_TOTP_POLICY_DEFAULTS` → True).
        this.settings.passkeys_allowed = (d.passkeys_allowed !== false);
        // Capture baseline for the host-stats dirty indicator.
        // Passwords/tokens are always blank in the live form (write-
        // only on the wire) so any typed value flips dirty.
        this._hostStatsBaseline = this._hostStatsSnapshot();
        this.endpointId = d.endpoint_id || 1;

        // --- OIDC panel state ---
        this.oidcStatus = d.oidc || null;
        if (this.oidcStatus) {
          this.oidcForm = {
            enabled: !!this.oidcStatus.enabled,
            issuer_url: this.oidcStatus.issuer_url || '',
            client_id: this.oidcStatus.client_id || '',
            client_secret: '',  // write-only — never prefill
            redirect_uri: this.oidcStatus.redirect_uri || this.oidcStatus.redirect_uri_default || '',
            scopes: this.oidcStatus.scopes || 'openid email profile groups',
            admin_group: this.oidcStatus.admin_group || 'omnigrid-admins',
            // Default ON when the backend hasn't surfaced it yet (first load
            // after the migration); otherwise reflect whatever's persisted.
            verify_tls: this.oidcStatus.verify_tls !== false,
            // case-insensitive admin-group claim match.
            // Default true (legacy exact-match contract) so existing
            // deploys are no-ops; flip false in the form when the IdP
            // returns mixed-case group names that don't match the
            // operator-typed value verbatim.
            group_case_sensitive: this.oidcStatus.group_case_sensitive !== false,
          };
        }

        // --- Portainer connection panel state ---
        this.portainerStatus = d.portainer || null;
        if (this.portainerStatus) {
          this.portainerForm = {
            url: this.portainerStatus.url || '',
            endpoint_id: this.portainerStatus.endpoint_id || 1,
            verify_tls: !!this.portainerStatus.verify_tls,
            api_key: '',  // write-only — never prefill
          };
        }
        // Capture portainer-public-url baseline for the dirty getter
        // (separate from portainerForm because the public URL lives on
        // the broader `settings` object, not the form).
        this._portainerPublicBaseline = (this.settings || {}).portainer_public_url || '';
        // Capture all 5 unified-pattern baselines AFTER the form/settings
        // are fully populated. Subsequent edits compare against these.
        this._appriseBaseline = this._appriseSnapshot();
        // Split-Save baselines — providers + per-event have their
        // own dirty trackers + Save handlers so functionality stays
        // separated (see saveProviders / savePerEvent).
        try {
          this._providersBaseline = this._providersSnapshot();
          this._perEventBaseline = this._perEventSnapshot();
        } catch (_) {
        }
        // Optimistic Test-pass stamp on load. Operator-flagged: after a
        // page reload the per-channel Save was disabled forever because
        // `_<medium>LastPassedTest` resets to '' on every refresh — so
        // flipping ANY field (even non-test-relevant ones like
        // `telegram_allow_destructive`) couldn't unlock Save until the
        // operator ran the per-channel Test button again. Since the
        // settings just loaded WERE persisted through a Save (which
        // requires Test to pass), we can reasonably stamp the current
        // test-snapshot as "passed" at hydrate time. The Test gate then
        // only re-fires when a test-RELEVANT field (URL / token / chat
        // ID / verify_tls / enabled) actually changes from the loaded
        // baseline — non-test-relevant edits (allow_destructive,
        // listener_enabled, authorized_user_ids, etc.) Save freely.
        try {
          this._appriseLastPassedTest = this._appriseTestSnapshot();
          this._telegramLastPassedTest = this._telegramTestSnapshot();
        } catch (_) {
        }
        this._openMeteoBaseline = this._openMeteoSnapshot();
        this._portainerBaseline = this._portainerSnapshot();
        this._oidcBaseline = this._oidcSnapshot();
        this._debugBaseline = this._debugSnapshot();
        this._totpPolicyBaseline = this._totpPolicySnapshot();
        // AI integration — hydrate the per-provider form state +
        // capture its baseline. Mirrors the pattern above for the
        // other admin-tab forms.
        this.hydrateAiFromSettings(d);

        // --- Admin → SSH panel state ---
        this.hydrateSshSettings(d);

        // --- Host groups ---
        // Server returns a clean list of {name, range_start, range_end,
        // order}. We keep the order-field for round-trip but also sort
        // here so the editor renders in the same order as the Hosts
        // view will. Fresh load resets dirty flag.
        this.hostGroups = Array.isArray(d.host_groups) ? d.host_groups.map(g => ({
          // Stable id minted server-side on first save (fix).
          // Round-trips unchanged so renames preserve the persisted
          // SSH password while a new same-named group can't inherit
          // it. Blank for any row that hasn't been saved yet.
          id: String(g.id || ''),
          name: String(g.name || ''),
          range_start: Number.isFinite(+g.range_start) ? +g.range_start : 0,
          range_end: Number.isFinite(+g.range_end) ? +g.range_end : 0,
          // Optional display-prefix number. Empty string in the form
          // when unset; a positive integer otherwise. Sent through to
          // the server unchanged on save.
          number: (g.number != null && +g.number > 0) ? +g.number : '',
          parent_name: String(g.parent_name || ''),
          ip_range: String(g.ip_range || ''),
          // Per-group SSH overrides — same shape as `hosts_config[].ssh`.
          // Password is write-only (server returns `password_set`
          // flag instead of the value); UI surfaces "set" badge so
          // operators can see whether one's configured without
          // exposing it.
          ssh: {
            user: String((g.ssh && g.ssh.user) || ''),
            port: (g.ssh && g.ssh.port) || '',
            password: '',
            password_set: !!(g.ssh && g.ssh.password_set),
            clear_password: false,
          },
          order: Number.isFinite(+g.order) ? +g.order : 0,
        })) : [];
        this.hostGroupsDirty = false;
        // Bust groupedHosts() cache on every load.
        this.hostGroupsRevision = (this.hostGroupsRevision || 0) + 1;

        // --- Asset inventory ---
        this.assetStatus = d.asset_inventory || null;
        if (this.assetStatus) {
          this.assetForm = {
            auth_mode: (this.assetStatus.auth_mode === 'lifetime_token')
              ? 'lifetime_token' : 'oauth2',
            base_url: this.assetStatus.base_url || '',
            token_url: this.assetStatus.token_url || '',
            client_id: this.assetStatus.client_id || '',
            client_secret: '',  // write-only — never prefill
            scope: this.assetStatus.scope || '',
            lifetime_token: '',  // write-only — never prefill
            service: this.assetStatus.service || '',
            action: this.assetStatus.action || '',
            min_value: (this.assetStatus.min_value != null) ? String(this.assetStatus.min_value) : '',
            max_value: (this.assetStatus.max_value != null) ? String(this.assetStatus.max_value) : '',
            edit_url_template: this.assetStatus.edit_url_template || '',
            // / — default true if backend omits the key
            // (legacy deploy seeing first read).
            verify_tls: (this.assetStatus.verify_tls !== false),
          };
        }
      } catch (e) {
        console.error(e);
      }
    },

    async copyRedirectUri() {
      const uri = (this.oidcStatus && this.oidcStatus.redirect_uri_default)
        || (this.oidcForm && this.oidcForm.redirect_uri) || '';
      if (!uri) {
        this.showToast(this.t('toasts.no_redirect_uri'), 'error');
        return;
      }
      try {
        await navigator.clipboard.writeText(uri);
        this.showToast(this.t('toasts.redirect_uri_copied'));
      } catch (_) {
        this.showToast(this.t('toasts.copy_failed'), 'error');
      }
    },

    // Optimistic stamp of the last-test-success cache when a Test
    // endpoint reports ok=true. Backend ALSO writes the same timestamp
    // to the `settings` KV (`last_test_success_<provider>`) — that's
    // the cross-browser source of truth, hydrated into
    // `_lastTestSuccess` on every /api/me load via
    // `client_config.last_test_success`. The optimistic stamp here is
    // just so the label updates IMMEDIATELY on Test-success without
    // waiting for the next /api/me round-trip.
    recordTestSuccess(key) {
      if (!key) {
        return;
      }
      const ts = Math.floor(Date.now() / 1000);
      this._lastTestSuccess = {...(this._lastTestSuccess || {}), [key]: ts};
    },
    // Wrap every `<input type="password">` whose closest ancestor
    // `<form>` is missing in a hidden `<form>` so Chromium stops
    // emitting "Password field is not contained in a form" DevTools
    // warnings (~17 instances across admin tabs — Beszel / Pulse /
    // Webmin / Portainer / OIDC / SSH / Asset / SNMP / host-groups
    // secrets). The form uses `display: contents` so layout is
    // unaffected, AND `onsubmit="return false"` so an accidental
    // Enter-key submit doesn't navigate the page (the SPA POSTs every
    // secret via fetch, never form-submission). Idempotent — runs on
    // init + on every Alpine DOM mutation; already-wrapped inputs
    // skip cleanly via the `closest('form')` guard.
    _wrapOrphanPasswordFields() {
      const inputs = document.querySelectorAll('input[type="password"]');
      for (const input of inputs) {
        let form = input.closest('form');
        if (!form) {
          form = document.createElement('form');
          form.style.display = 'contents';
          form.setAttribute('onsubmit', 'return false');
          form.dataset.passwordWrap = '1';
          input.parentNode.insertBefore(form, input);
          form.appendChild(input);
        }
        // Chrome / Edge fire `[DOM] Password forms should have
        // (optionally hidden) username fields for accessibility`
        // for every password input that lives in a form WITHOUT a
        // matching username field — password managers + AT need
        // an account identifier paired with the secret. The login
        // page already has explicit username + password inputs;
        // every OTHER password field in the SPA (Beszel password,
        // Webmin password, OIDC client_secret, Portainer api_key,
        // SSH passphrase, SNMP v3 keys, etc.) is for a SERVICE
        // credential, not a personal login — so there's no real
        // username to pair. Inject ONE hidden disabled username
        // input per form to satisfy the heuristic; disabled keeps
        // it out of the form's submitted-fields set so it can't
        // accidentally leak into a future POST. Idempotent —
        // re-runs of this helper find the existing field and skip.
        if (form.dataset.usernameInjected === '1') {
          continue;
        }
        if (form.querySelector('input[autocomplete="username"]')) {
          form.dataset.usernameInjected = '1';
          continue;
        }
        const u = document.createElement('input');
        u.type = 'text';
        u.name = 'username';
        u.autocomplete = 'username';
        u.disabled = true;
        u.setAttribute('aria-hidden', 'true');
        u.style.display = 'none';
        form.insertBefore(u, input);
        form.dataset.usernameInjected = '1';
      }
    },
    // Returns the formatted "Last connected: <relative time>" label
    // for a provider key, or '' when no successful test has been
    // recorded yet (so the consumer's `x-show` collapses cleanly).
    // The relative-time math reads `_lastTestSuccessNow` so a 60s
    // tick refreshes every label without reloading.
    lastTestSuccessLabel(key) {
      const ts = (this._lastTestSuccess || {})[key];
      if (!ts) {
        return '';
      }
      const now = this._lastTestSuccessNow || Math.floor(Date.now() / 1000);
      const delta = Math.max(0, now - ts);
      let rel;
      if (delta < 60) {
        rel = this.t('common.just_now') || 'just now';
      } else {
        if (delta < 3600) {
          rel = this.t('common.minutes_ago', {count: Math.floor(delta / 60)}) || `${Math.floor(delta / 60)}m ago`;
        } else {
          if (delta < 86400) {
            rel = this.t('common.hours_ago', {count: Math.floor(delta / 3600)}) || `${Math.floor(delta / 3600)}h ago`;
          } else {
            rel = this.t('common.days_ago', {count: Math.floor(delta / 86400)}) || `${Math.floor(delta / 86400)}d ago`;
          }
        }
      }
      return this.t('admin.last_connected_label', {rel: rel}) || `Last connected ${rel}`;
    },

    // list of curated hosts that have ping enabled. Pulled from
    // the in-memory `hostsConfig` (loaded by the Hosts admin tab) so
    // the picker stays in sync with the row-level toggles without an
    // extra round-trip.
    pingEnabledHosts() {
      const rows = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
      return rows
        .filter(h => h && h.ping && h.ping.enabled && h.enabled !== false && h.id)
        .map(h => ({id: h.id, label: this.hostDisplayName(h) || h.id}));
    },
    // / list of curated hosts that have SNMP mapped (a
    // non-empty `snmp_name` row field). Pulled from the in-memory
    // `hostsConfig` (loaded by the Hosts admin tab) so the picker
    // stays in sync with the row-level config without an extra
    // round-trip. Mirrors `pingEnabledHosts()` exactly so the two
    // providers' test UX is unified.
    snmpEnabledHosts() {
      const rows = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
      return rows
        .filter(h => h && h.enabled !== false && h.id
          // explicit opt-in: SNMP probes only run
          // when the operator checks the per-host enable
          // box. Default-OFF mirrors ping.enabled.
          && !!(h.snmp && h.snmp.enabled === true)
          // Same canonical chain (snmp_name → address) the
          // live sampler uses — a host with `address` set
          // and `snmp_name` blank IS valid SNMP target.
          && ((h.snmp_name || '').trim() || (h.address || '').trim()))
        .map(h => ({id: h.id, label: this.hostDisplayName(h) || h.id}));
    },
    // User-side convenience handlers — operate on profileForm.notify_events
    // (per-user opt-in map keyed by BARE event name — no
    // notify_event_ prefix). Per-medium granularity: each event's value
    // is `{medium: bool}` after syncProfileForm normalises it, so each
    // helper writes uniformly across every medium that's globally
    // enabled. Admin-disabled events skip — the backend rejects an
    // opt-in attempt for them with 400.
    _bareEventName(k) {
      return String(k || '').replace(/^notify_event_/, '');
    },
    // Read / write a single (event, medium) checkbox.
    userNotifyEventValue(eventKey, medium) {
      const bare = this._bareEventName(eventKey);
      const slot = (this.profileForm && this.profileForm.notify_events)
        ? this.profileForm.notify_events[bare] : null;
      if (slot && typeof slot === 'object') {
        // Missing medium key defaults to true (matches dispatcher's
        // default-on-missing-medium contract).
        return (slot[medium] !== false);
      }
      // Defensive: profileForm not yet hydrated.
      return false;
    },
    // Returns true when the event is enabled across EVERY medium —
    // used to render the row's master-toggle in its checked state.
    userNotifyEventRowAll(eventKey) {
      const bare = this._bareEventName(eventKey);
      const slot = (this.profileForm && this.profileForm.notify_events)
        ? this.profileForm.notify_events[bare] : null;
      if (!slot || typeof slot !== 'object') {
        return false;
      }
      const mediums = this.notifyMediumNames();
      for (const m of mediums) {
        if (slot[m] === false) {
          return false;
        }
      }
      return true;
    },
    userNotifyBulkSuccessMedium: '',
    userNotifyBulkFailureMedium: '',
    _debugSnapshot() {
      const s = this.settings || {};
      return JSON.stringify({
        enabled: !!s.debug_panel_enabled,
      });
    },
    debugDirty() {
      return this._debugBaseline !== this._debugSnapshot();
    },
    _openMeteoSnapshot() {
      const s = this.settings || {};
      return JSON.stringify({
        enabled: !!s.open_meteo_enabled,
        url: (s.open_meteo_url || '').trim().replace(/\/+$/, ''),
      });
    },
    openMeteoDirty() {
      return this._openMeteoBaseline !== this._openMeteoSnapshot();
    },
    markOpenMeteoDirty() {
    },
    // Auto-save a single per-service "enabled" master switch.
    // Wired to the @change of the toggle checkbox for Apprise /
    // Open-Meteo / Portainer / SSH so the operator doesn't have to
    // hunt for a Save button just to flip the master switch. Sends
    // ONLY the one field so it doesn't drag along whatever else is
    // dirty in the form. Toast confirms with the resulting state.
    async saveServiceEnabled(name) {
      const allowed = ['apprise', 'open_meteo', 'portainer', 'ssh'];
      if (!allowed.includes(name)) {
        return;
      }
      const key = name + '_enabled';
      const value = !!this.settings[key];
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({[key]: value}),
        });
        if (!r.ok) {
          throw new Error(await r.text());
        }
        const stateKey = value
          ? 'admin_integrations.toggle_enabled_toast'
          : 'admin_integrations.toggle_disabled_toast';
        this.showToast(this.t(stateKey, {name}), 'success');
      } catch (_) {
        // Roll the in-memory toggle back so UI matches server state.
        this.settings[key] = !value;
        this.showToast(this.t('toasts_extra.save_failed_generic'), 'error');
      }
    },
    // ---- Providers vs per-event Save split --------------------------
    // The notifications page has TWO functionally separate Save
    // buttons. The first ("Save channel configuration", in the
    // providers section) commits Apprise URL/tag, Telegram credentials,
    // medium toggles, listener config, and in-app tunables. The second
    // ("Save event toggles", in the per-event sibling section)
    // commits ONLY the per-event notification opt-ins. They have:
    //   - independent dirty trackers (`providersDirty()` vs
    //     `perEventDirty()`) backed by disjoint snapshot helpers
    //     (`_providersSnapshot` vs `_perEventSnapshot`).
    //   - independent save handlers (`saveProviders` builds a payload
    //     containing only Apprise/Telegram/medium/tunable keys;
    //     `savePerEvent` builds one containing only `notify_event_*`
    //     keys). They never share a POST.
    //   - independent success/error chips (`providersSaveResult` vs
    //     `perEventSaveResult`).
    //   - independent button labels ("Save channel configuration" vs
    //     "Save event toggles") + inline scope-hint paragraphs so the
    //     operator can never mistake one for the other.
    // Result: editing a per-event toggle ONLY enables the per-event
    // Save button; editing a channel field ONLY enables the channel
    // Save button. An action on one button never visually conflates
    // with the other.
    _providersSnapshot() {
      const s = this.settings || {};
      const tf = this.tuningForm || {};
      return JSON.stringify({
        // Apprise
        apprise_enabled: !!s.apprise_enabled,
        apprise_url: (s.apprise_url || '').trim(),
        apprise_tag: (s.apprise_tag || '').trim(),
        // Telegram core
        telegram_chat_id: (s.telegram_chat_id || '').trim(),
        telegram_thread_id: (s.telegram_thread_id || '').trim(),
        telegram_verify_tls: !!s.telegram_verify_tls,
        // Write-only secret — non-empty form value = dirty.
        telegram_token_pending: (s.telegram_bot_token || '').trim() ? '<pending>' : '',
        // Telegram Phase 2 listener config
        telegram_listener_enabled: !!s.telegram_listener_enabled,
        telegram_allow_destructive: !!s.telegram_allow_destructive,
        telegram_authorized_user_ids: (s.telegram_authorized_user_ids || '').trim(),
        // Per-medium fan-out toggles
        medium_app: !!s.notify_medium_app,
        medium_apprise: !!s.notify_medium_apprise,
        medium_telegram: !!s.notify_medium_telegram,
        // In-app tunables (live in the In-app tab body)
        tuning_retention: (tf.tuning_notification_retention_days ?? '').toString(),
        tuning_page: (tf.tuning_notification_page_size ?? '').toString(),
        tuning_poll: (tf.tuning_notifications_poll_interval_seconds ?? '').toString(),
      });
    },
    providersDirty() {
      return this._providersBaseline !== this._providersSnapshot();
    },
    _perEventSnapshot() {
      const events = {};
      for (const k of (this.notifyEventKeys || [])) {
        events[k] = !!(this.settings || {})[k];
      }
      return JSON.stringify(events);
    },
    perEventDirty() {
      return this._perEventBaseline !== this._perEventSnapshot();
    },
    async savePerEvent() {
      // Per-event-only POST — strictly the notify_event_* keys. No
      // Test-before-Save gate (per-event toggles don't round-trip).
      if (this.settingsSaving) {
        return;
      }
      this.settingsSaving = true;
      this.perEventSaveResult = null;
      try {
        const payload = {};
        for (const k of (this.notifyEventKeys || [])) {
          if (k in this.settings) {
            payload[k] = this.settings[k] ? 'true' : 'false';
          }
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        await this.loadSettings();
        this._perEventBaseline = this._perEventSnapshot();
        this._appriseBaseline = this._appriseSnapshot();
        this.perEventSaveResult = {
          ok: true,
          detail: this.t('admin.notifications.per_event_save_success') || 'Per-event toggles saved',
        };
      } catch (e) {
        this.perEventSaveResult = {
          ok: false,
          detail: String(e && e.message ? e.message : e),
        };
      } finally {
        this.settingsSaving = false;
      }
    },
    async loadIgnores() {
      try {
        const r = await fetch('/api/ignores');
        this.ignores = (await r.json()).ignores || [];
      } catch (e) {
        console.error(e);
      }
    },
    async addIgnore() {
      if (!this.newIgnore.pattern.trim()) {
        return;
      }
      await fetch('/api/ignores', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(this.newIgnore),
      });
      this.newIgnore.pattern = '';
      await this.loadIgnores();
      await this.refresh(true);
    },
    async delIgnore(pattern) {
      await fetch('/api/ignores/' + encodeURIComponent(pattern), {method: 'DELETE'});
      await this.loadIgnores();
      await this.refresh(true);
    },
    async toggleIgnore(item) {
      if (item.ignored) {
        const match = this.ignores.find(ig =>
          (ig.kind === 'image' && (item.image || '').includes(ig.pattern)) ||
          (ig.kind === 'stack' && ig.pattern === item.stack)
        );
        if (match) {
          await this.delIgnore(match.pattern);
        }
      } else {
        this.newIgnore = {kind: 'image', pattern: item.image};
        await this.addIgnore();
      }
      this.drawerItem = null;
    },
    hasHistoryFilter() {
      const f = this.historyFilters;
      return !!(f.q || f.stack || f.op_type || f.status || f.actor || f.fromDate || f.toDate);
    },
    resetHistoryFilters() {
      this.historyFilters = {q: '', stack: '', op_type: '', status: '', actor: '', fromDate: '', toDate: ''};
    },
    _persistHistoryPaging() {
      try {
        localStorage.setItem('historyPage', String(this.historyPage));
        localStorage.setItem('historyPerPage', String(this.historyPerPage));
      } catch (_) {
      }
    },
    // Open the AI sidebar pre-loaded with a "diagnose this history row"
    // user-turn. The seeded prompt carries the row's op_type, target,
    // status, error text, and recent events so the AI can answer
    // root-cause questions without the operator typing them all out.
    // Falls back to the AI palette when the sidebar surface is not
    // available (unlikely — both gates check the same toggles).
    _diagnoseHistoryRowWithAi(h) {
      if (!h) {
        return;
      }
      const parts = [];
      parts.push(`Diagnose this history row and suggest the most likely root cause + one specific remediation step.`);
      parts.push(`Op: ${h.op_type || 'unknown'}`);
      if (h.target_name) {
        parts.push(`Target: ${h.target_name}`);
      }
      if (h.target_stack) {
        parts.push(`Stack: ${h.target_stack}`);
      }
      parts.push(`Status: ${h.status || 'unknown'}`);
      parts.push(`When: ${this.formatTime(h.ts) || '—'}`);
      if (h.duration) {
        parts.push(`Duration: ${(h.duration || 0).toFixed(2)}s`);
      }
      if (h.actor) {
        parts.push(`Actor: ${h.actor}`);
      }
      if (h.error) {
        parts.push(`Error: ${h.error}`);
      }
      const events = this.parseEvents(h.events) || [];
      if (events.length) {
        const tail = events.slice(-8).map(ev =>
          `[${this.formatTimeShort(ev.ts)} ${ev.level || 'info'}] ${ev.msg || ''}`
        ).join('\n');
        parts.push(`Recent events:\n${tail}`);
      }
      const prompt = parts.join('\n');
      try {
        if (typeof this.openAiSidebar === 'function') {
          this.openAiSidebar();
        }
        // Stash the prompt into the sidebar's query state and fire its
        // existing send pipeline. The pipeline records turns + persists
        // to ui_prefs.ai_conversation, so we route through it rather
        // than re-implementing send-on-mount logic here. Use `$nextTick`
        // so the textarea has time to bind to the new value before the
        // send fires (the sidebar's textarea reads from the state field
        // at submit time, but the DOM element may need one tick to
        // reflect the prefill visually).
        if (typeof this._setAiSidebarQuery === 'function') {
          this._setAiSidebarQuery(prompt);
        } else {
          this.aiSidebarQuery = prompt;
        }
        const fire = () => {
          if (typeof this.sendAiSidebarMessage === 'function') {
            this.sendAiSidebarMessage();
          }
        };
        if (this.$nextTick) {
          this.$nextTick(fire);
        } else {
          setTimeout(fire, 0);
        }
      } catch (e) {
        console.warn('[history] diagnose-with-ai failed', e);
      }
    },
    async clearHistory() {
      const ok = await this.confirmDialog({
        title: this.t('history.clear_confirm_title'),
        html: this.t('history.clear_confirm_html'),
        icon: 'warning',
        confirmText: this.t('history.clear_confirm_button'),
        confirmColor: this._cssVar('--danger'),
        focusConfirm: true,
      });
      if (!ok) {
        return;
      }
      await fetch('/api/history', {method: 'DELETE'});
      await this.loadHistory();
      this.showToast(this.t('toasts.history_cleared'));
    },
    // Mark every unread notification in one cluster read. Bulk version
    // of `markNotificationRead` — fires one PATCH per row through the
    // existing handler so the SSE notification:read event publishes per
    // notification (other tabs reconcile each row independently).
    async markClusterRead(cluster) {
      if (!cluster || !cluster.items) {
        return;
      }
      const unread = cluster.items.filter(n => n && n.read_at == null);
      for (const n of unread) {
        try {
          await this.markNotificationRead(n.id);
        } catch (_) {
        }
      }
    },

    // ===================================================================
    // Real-time event stream
    // ===================================================================
    // EventSource connects to /api/events on cookie-authed browsers and
    // dispatches one handler per server-side event type. Every existing
    // poll loop (pollOps / pollStats / refresh / loadHosts / loadHistory)
    // checks `_sseConnected` and skips its self-scheduled work while the
    // stream is healthy. EventSource handles reconnect natively; we only
    // track the connection state for the toolbar indicator + poll-gate.
    //
    // Reactive updates use the existing in-place reconcile contract —
    // never reassign reactive arrays from an event handler (would tear
    // every chart SVG / <details> / inline-style node down on each
    // event, defeating the entire purpose of moving from poll → push).
    // explicit disconnect so the cadence picker can fully turn
    // SSE off when the operator chooses "Off" or an interval. Without
    // this, picking Off left the SSE pipe alive and the Live pill
    // stayed green even though the operator's mental model is "no
    // updates at all".
    _disconnectSSE() {
      if (this._sse) {
        console.log('[live] SSE disconnect: closing stream (operator picked Off/interval)');
        try {
          this._sse.close();
        } catch (_) {
        }
        this._sse = null;
      }
      this._sseConnected = false;
      this._sseLastEventTs = 0;
    },
    // Multi-tab activity ------------------------------------------------
    // Snapshot of THIS tab's current location for the heartbeat payload.
    // Walks the SPA's reactive view-state and emits a compact dict that
    // the backend stamps into the in-process tab registry + broadcasts to
    // sibling tabs.
    _tabActivitySnapshot() {
      const drawerHostId = (this.drawerHost && this.drawerHost.id) || null;
      // Operator-friendly title — i18n'd so non-en operators see
      // localised text in the topbar tab-activity popover. Top-level
      // view labels come from the existing `nav.*` keys the topbar
      // sidebar already consumes; the leg-join uses a dedicated
      // `topbar.tabs.path_separator` key so a locale that wants a
      // different glyph (or a plain dash) can override it without
      // touching this code.
      const v = (this.view || '').toString();
      const _navLabel = (key) => {
        const out = this.t('nav.' + key);
        // Fallback to capitalised view id if the locale doesn't define
        // the key — keeps the popover useful even on incomplete bundles.
        return (out && out !== 'nav.' + key) ? out : (key.charAt(0).toUpperCase() + key.slice(1));
      };
      const _join = (leg, target) => {
        const tmpl = this.t('topbar.tabs.path_separator', {leg, target});
        return (tmpl && tmpl !== 'topbar.tabs.path_separator') ? tmpl : (leg + ' → ' + target);
      };
      let title = v ? _navLabel(v) : '';
      if (v === 'admin' && this.adminTab) {
        title = _join(_navLabel('admin'), this.adminTab);
      } else {
        if (v === 'settings' && this.settingsSection) {
          title = _join(_navLabel('settings'), this.settingsSection);
        } else {
          if (v === 'stats' && this.statsTab) {
            title = _join(_navLabel('stats'), this.statsTab);
          } else {
            if (v === 'hosts' && drawerHostId) {
              title = _join(_navLabel('hosts'), drawerHostId);
            }
          }
        }
      }

      // Richer state — filter chip state + selected hosts + selected
      // items. Lets the popover show "Hosts → 12 selected, paused
      // filter on" instead of just "Hosts". Powers the "Reproduce
      // here" handoff so the operator on the phone can copy the
      // desktop tab's filter/drawer state into the current tab.
      const _filters = {
        search: (this.search || '').toString() || null,
        statusFilter: (this.statusFilter || '').toString() || null,
        healthFilter: (this.healthFilter || '').toString() || null,
        hostsProblemFilter: !!this.hostsProblemFilter || null,
        hostsHideUnconfigured: !!this.hostsHideUnconfigured || null,
        hostsProviderFilter: (this.hostsProviderFilter
          && this.hostsProviderFilter.size)
          ? Array.from(this.hostsProviderFilter)
          : null,
      };
      // Strip null/false entries so the snapshot stays compact on the
      // wire (heartbeat fires every 30s — keep the payload small).
      const filters = {};
      for (const k of Object.keys(_filters)) {
        if (_filters[k]) {
          filters[k] = _filters[k];
        }
      }
      const selectionIds = Array.isArray(this.selected)
        ? this.selected.slice(0, 50)  // cap to bound the heartbeat size
        : [];
      const _hasRichState = Object.keys(filters).length > 0
        || selectionIds.length > 0;

      return {
        view: v || null,
        drawer_host: drawerHostId,
        drawer_item: (this.drawerItem && (this.drawerItem.id || this.drawerItem.name)) || null,
        admin_tab: (this.adminTab || '').toString() || null,
        settings_section: (this.settingsSection || '').toString() || null,
        stats_tab: (this.statsTab || '').toString() || null,
        title: title || null,
        // Compact richer state — only emitted when actually populated
        // so idle tabs don't waste heartbeat bytes.
        filters: Object.keys(filters).length ? filters : null,
        selection: selectionIds.length ? selectionIds : null,
        // Pre-formatted summary label the popover renders if the
        // operator wants more than the bare title. e.g. "Hosts → 12
        // selected · paused filter on".
        rich_label: _hasRichState ? this._tabActivityRichLabel(filters, selectionIds) : null,
      };
    },

    // Render a richer popover label for a snapshot — e.g.
    // "Hosts → 12 selected · paused filter on". Powers the
    // operator's "what's open on the other tab?" glance + the
    // Reproduce-here handoff. All fragments go through `t()` so
    // non-en locales see the localised count + filter names.
    _tabActivityRichLabel(filters, selection) {
      const fragments = [];
      if (selection && selection.length) {
        fragments.push(this.t('topbar.tabs.rich.selection', {count: selection.length}) || (selection.length + ' selected'));
      }
      if (filters.hostsProblemFilter) {
        fragments.push(this.t('topbar.tabs.rich.problem_filter') || 'problem filter');
      }
      if (filters.hostsHideUnconfigured) {
        fragments.push(this.t('topbar.tabs.rich.hide_unconfigured_filter') || 'hide unconfigured');
      }
      if (filters.hostsProviderFilter && filters.hostsProviderFilter.length) {
        fragments.push(this.t('topbar.tabs.rich.provider_filter', {providers: filters.hostsProviderFilter.join(', ')})
          || ('providers: ' + filters.hostsProviderFilter.join(', ')));
      }
      if (filters.statusFilter) {
        fragments.push(this.t('topbar.tabs.rich.status_filter', {status: filters.statusFilter})
          || ('status: ' + filters.statusFilter));
      }
      if (filters.healthFilter) {
        fragments.push(this.t('topbar.tabs.rich.health_filter', {health: filters.healthFilter})
          || ('health: ' + filters.healthFilter));
      }
      if (filters.search) {
        fragments.push(this.t('topbar.tabs.rich.search', {query: filters.search})
          || ('search: ' + filters.search));
      }
      return fragments.join(' · ');
    },

    // Device descriptor renderers — drive the small "📱 iPhone · Safari"
    // chip in the tab-activity popover so the operator can tell which
    // machine the OTHER tab is on. Backend stamps `device =
    // {form_factor, platform, browser, ua}` on every heartbeat entry
    // (see `_parse_tab_activity_device` in main.py); these helpers
    // map that descriptor into renderable text + an emoji prefix.
    _tabActivityDeviceEmoji(device) {
      if (!device || typeof device !== 'object') {
        return '';
      }
      const ff = String(device.form_factor || '').toLowerCase();
      if (ff === 'mobile') {
        return '📱';
      }
      if (ff === 'tablet') {
        return '💻';
      }
      // 'desktop' or anything else falls through to the desktop glyph
      // — better than rendering no emoji at all (operator can still
      // disambiguate via the platform / browser label).
      return '🖥️';
    },
    _tabActivityDeviceLabel(device) {
      if (!device || typeof device !== 'object') {
        return '';
      }
      // Platform + browser pair via the i18n bundle so non-en locales
      // get localised labels. Backend tags are stable English keys
      // (`iOS` / `Mac` / `Chrome` / etc.) — the i18n key encodes the
      // same as a lower-cased slug under `topbar.tabs.device.platform.*`
      // / `topbar.tabs.device.browser.*`. Fallback to the raw English
      // tag when the locale doesn't define the key (forward-compat
      // for novel UA detection cases).
      const platformKey = String(device.platform || 'Other').toLowerCase().replace(/[^a-z0-9]+/g, '_');
      const browserKey = String(device.browser || 'Other').toLowerCase().replace(/[^a-z0-9]+/g, '_');
      const platformLabel = this.t('topbar.tabs.device.platform.' + platformKey);
      const browserLabel = this.t('topbar.tabs.device.browser.' + browserKey);
      const p = (platformLabel && platformLabel !== 'topbar.tabs.device.platform.' + platformKey)
        ? platformLabel
        : String(device.platform || '');
      const b = (browserLabel && browserLabel !== 'topbar.tabs.device.browser.' + browserKey)
        ? browserLabel
        : String(device.browser || '');
      if (p && b) {
        return p + ' · ' + b;
      }
      return p || b || '';
    },

    // "Reproduce here" handoff — pull the other tab's filter + drawer
    // state into the CURRENT tab. Operator clicks a row in the
    // tab-activity popover when they want to mirror state from
    // laptop → phone (or vice versa). The popover passes the row's
    // snapshot dict to this helper; we mutate the current tab's
    // state in place + persist where applicable.
    reproduceTabHere(snapshot) {
      if (!snapshot || typeof snapshot !== 'object') {
        return;
      }
      // Top-level view + sub-tab navigation.
      if (snapshot.view) {
        this.view = snapshot.view;
      }
      if (snapshot.admin_tab && typeof this.openAdminTab === 'function') {
        this.openAdminTab(snapshot.admin_tab);
      } else if (snapshot.admin_tab) {
        this.adminTab = snapshot.admin_tab;
      }
      if (snapshot.settings_section) {
        this.settingsSection = snapshot.settings_section;
      }
      if (snapshot.stats_tab) {
        this.statsTab = snapshot.stats_tab;
      }
      // Filter restore — each filter flag uses the SAME mutator
      // helpers the toolbar chips do so persistence + downstream
      // reactivity (sessionStorage / SSE chip refresh) stay coherent.
      const f = snapshot.filters || {};
      this.search = f.search || '';
      this.statusFilter = f.statusFilter || '';
      this.healthFilter = f.healthFilter || '';
      if (this.hostsProblemFilter !== !!f.hostsProblemFilter) {
        this.toggleProblemHostsFilter();
      }
      this.hostsHideUnconfigured = !!f.hostsHideUnconfigured;
      // Provider filter — replace the whole set in one mutation so we
      // don't bounce sessionStorage writes on every chip.
      this.hostsProviderFilter = new Set(Array.isArray(f.hostsProviderFilter) ? f.hostsProviderFilter : []);
      try {
        if (typeof sessionStorage !== 'undefined') {
          if (this.hostsProviderFilter.size) {
            sessionStorage.setItem('hostsProviderFilter', [...this.hostsProviderFilter].join(','));
          } else {
            sessionStorage.removeItem('hostsProviderFilter');
          }
        }
      } catch (_) { /* ignore */
      }
      // Drawer state — open the same host / item if the source tab
      // had one open. Items / hosts must exist locally; if the source
      // tab had a drawer for a host we don't know about yet, the
      // open silently no-ops (the operator can refresh first).
      if (snapshot.drawer_host && Array.isArray(this.hosts)) {
        const target = this.hosts.find(h => h && h.id === snapshot.drawer_host);
        if (target && typeof this.openHostDrawer === 'function') {
          this.openHostDrawer(target);
        }
      }
      if (snapshot.drawer_item && Array.isArray(this.items)) {
        const target = this.items.find(it => it && (it.id === snapshot.drawer_item || it.name === snapshot.drawer_item));
        if (target) {
          this.drawerItem = target;
        }
      }
      // Toast confirmation so the operator sees the mirror landed.
      if (typeof this.showToast === 'function') {
        this.showToast(this.t('topbar.tabs.rich.reproduced') || 'Mirrored other tab\'s state', 'success');
      }
    },
    // Heartbeat publisher — POSTs the current snapshot to the backend.
    // Short-circuits when nothing changed AND the last post was < 25s ago
    // (idle-tab path; the backend's 90s TTL still keeps the entry alive).
    async _tabActivityHeartbeat() {
      if (this._tabHeartbeatBusy) {
        return;
      }
      const snap = this._tabActivitySnapshot();
      const sig = JSON.stringify(snap);
      const now = Date.now();
      const stale = (now - (this._tabHeartbeatLast.ts || 0)) > 25000;
      if (sig === this._tabHeartbeatLast.signature && !stale) {
        return;
      }
      this._tabHeartbeatBusy = true;
      try {
        await fetch('/api/tabs/activity', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(snap),
        });
        this._tabHeartbeatLast = {ts: now, signature: sig};
      } catch (_) { /* best-effort; next tick retries */
      } finally {
        this._tabHeartbeatBusy = false;
      }
    },
    // Boot-time hydration of the local map from the backend's snapshot
    // so the topbar widget paints sibling tabs immediately, before the
    // first SSE event lands.
    async _tabActivityHydrate() {
      try {
        const r = await fetch('/api/tabs/activity', {cache: 'no-store'});
        if (!r.ok) {
          return;
        }
        const d = await r.json();
        const fresh = {};
        for (const t of (d.tabs || [])) {
          if (t && t.client_id) {
            fresh[t.client_id] = t;
          }
        }
        this.tabActivity = fresh;
      } catch (_) { /* best-effort */
      }
    },
    // Click-to-focus a sibling tab. Uses BroadcastChannel where available
    // (every modern browser since 2022); receivers self-match on the id
    // and call `window.focus()`. Best-effort — cross-window focus is
    // browser-discretionary, but works reliably when both tabs are in
    // the same browser process group.
    focusTabByClientId(cid) {
      if (!cid || cid === window.__ogClientId) {
        return;
      }
      try {
        if (!this._tabFocusChannel && typeof BroadcastChannel === 'function') {
          this._tabFocusChannel = new BroadcastChannel('omnigrid-tab-focus');
        }
        if (this._tabFocusChannel) {
          this._tabFocusChannel.postMessage({client_id: cid});
        }
      } catch (_) { /* BroadcastChannel disabled / sandboxed */
      }
    },
    // Sorted view of `tabActivity` for the topbar popover. Newest tab
    // first (largest `ts`).
    tabActivityList() {
      const out = [];
      const map = this.tabActivity || {};
      for (const cid of Object.keys(map)) {
        out.push(map[cid]);
      }
      out.sort((a, b) => (Number(b.ts || 0) - Number(a.ts || 0)));
      return out;
    },
    tabActivityCount() {
      return this.tabActivityList().length;
    },
    // Single-parse SSE event unwrap. Returns the parsed event object
    // ({type, ts, payload}) when the event should be processed,
    // OR null when self-filter wins (caller should early-return).
    // Eliminates the per-handler "JSON.parse(e.data) twice" pattern —
    // _isSelfEvent does its own parse, then handlers parse again.
    // 13 handlers × 2 parses per event = unnecessary overhead on
    // fleet-wide ticks. Use:
    // const evt = this._unwrapEventOrNull(e); if (!evt) return;
    // const id = (evt.payload || {}).id;  // already parsed
    _unwrapEventOrNull(e) {
      if (!e || !e.data) {
        return null;
      }
      try {
        const data = JSON.parse(e.data);
        const myId = window.__ogClientId;
        const cid = data && data.payload && data.payload.client_id;
        if (myId && cid && cid === myId) {
          return null;
        }  // self-event, skip
        return data;
      } catch (_) {
        return null;  // malformed event, treat as skip
      }
    },

    // enhancement — bracket every interval-poll fetch so the
    // topbar pill flashes green for the EXACT duration of the network
    // request (start of fetch → response landed). Counter-based so
    // concurrent polls (e.g. /api/items + /api/stats firing in the
    // same tick) don't end the flash prematurely on the first one
    // that returns. Off / Live modes short-circuit — Off shouldn't
    // poll, Live's green-on-event UX is already implicit in the SSE
    // pill colour.
    _pollStart() {
      if (this.refreshInterval === -1 || this.refreshInterval === 0) {
        return;
      }
      this._pollFlashCount = (this._pollFlashCount || 0) + 1;
      this._pollFlashing = true;
    },
    _pollEnd() {
      if (!this._pollFlashCount) {
        return;
      }
      this._pollFlashCount = Math.max(0, this._pollFlashCount - 1);
      if (this._pollFlashCount === 0) {
        this._pollFlashing = false;
      }
    },
    // Convenience wrapper — `await this._pollWrap(this.refresh(true))`
    // sets the flash on, awaits the promise, clears the flash in
    // finally. Returns the promise's resolved value so callers can
    // chain naturally.
    _pollWrap(promise) {
      this._pollStart();
      return Promise.resolve(promise).finally(() => this._pollEnd());
    },

    setAutoRefresh(seconds) {
      this.autoRefresh = seconds;
      try {
        localStorage.setItem('autoRefresh', String(seconds));
      } catch {
      }
      if (this._autoTimer) {
        clearInterval(this._autoTimer);
      }
      if (seconds > 0) {
        this._autoTimer = setInterval(() => {
          // Wrap in poll-flash brackets so the topbar pill stays green
          // for the duration of the actual /api/items round-trip.
          this._pollWrap(this.refresh(true));
        }, seconds * 1000);
      }
    },

    // single canonical cadence-setter. Three modes mapped to
    // the picker's five buttons:
    //
    // -1   "Live"   — SSE connection ON, every chart updates via
    //                 push events. Polling timers sleep.
    //  0   "Off"    — SSE connection CLOSED, polling sleeps. The
    //                 dashboard becomes a static snapshot of the
    //                 current state. Operator sees no more updates
    //                 until they pick another mode (or refresh).
    // 30/60/300     — SSE connection CLOSED, polling at the chosen
    //                 cadence drives every chart uniformly.
    //
    // Closing SSE for Off + interval modes is the load-bearing UX
    // fix — pre-fix the picker selected Off but the Live pill stayed
    // green because SSE was still pushing events; operators reported
    // "the picker doesn't do what it says". Now the SSE pill colour
    // is a direct read of the picker's choice.
    //
    // Mirrors the chosen polling cadence into legacy state vars
    // (`autoRefresh` for items poll + `statsInterval` for stats /
    // hosts polls) so the existing pollers don't need to be rewired.
    // Live and Off both map to legacy=0; only intervals drive the
    // pollers.
    setRefreshInterval(seconds) {
      const modeLabel = seconds === -1 ? 'Live (SSE)' : seconds === 0 ? 'Off' : seconds + 's interval';
      console.log('[live] setRefreshInterval: mode=' + modeLabel + ' (raw=' + seconds + ')');
      this.refreshInterval = seconds;
      try {
        localStorage.setItem('refreshInterval', String(seconds));
      } catch {
      }
      const legacy = seconds === -1 ? 0 : seconds;
      this.setStatsInterval(legacy);
      this.setAutoRefresh(legacy);
      // SSE management — Live opens (or keeps open) the stream;
      // Off / interval modes close it so the picker is the single
      // source of truth for "is this dashboard receiving updates?".
      if (seconds === -1) {
        if (!this._sse) {
          this._initSSE();
        }
      } else {
        this._disconnectSSE();
      }
      // pollOps gates on `refreshInterval === 0` (see pollOps tick
      // body) — re-kick it whenever we transition AWAY from Off so
      // the panel comes back without waiting for a manual interaction.
      if (seconds !== 0 && !this._opsTimer) {
        try {
          this.pollOps();
        } catch (_) {
        }
      }
      // Host-drawer history chart timer also follows the picker now
      //. Re-arm it under the new cadence whenever the operator
      // switches modes while the drawer is open. Off → clear; Live →
      // 30s baseline; interval → operator's chosen cadence.
      if (this._drawerHistoryTimer) {
        clearInterval(this._drawerHistoryTimer);
        this._drawerHistoryTimer = null;
      }
      // NE-only Live mode → push-driven → no timer.
      // Beszel / NE+Beszel Live → 30s timer (push event covers NE
      // half but Beszel data still needs polling). Interval modes
      // use the picker cadence regardless. Off → no timer.
      const dh = this.drawerHost;
      if (dh && (dh.beszel_id || dh.ne_url) && seconds !== 0) {
        const liveMode = seconds === -1;
        const pushOnly = liveMode && dh.ne_url && !dh.beszel_id;
        if (!pushOnly) {
          const ms = (liveMode ? 30 : seconds) * 1000;
          this._drawerHistoryTimer = setInterval(() => {
            if (!this.drawerHost) {
              return;
            }
            this._pollWrap(this.loadHostHistory(
              this.drawerHost.beszel_id || '',
              this.drawerHost.id,
            ));
          }, ms);
        }
      }
      // ping history timer follows the same picker cadence.
      // Live mode is push-driven by the `host:ping_sampled` SSE
      // handler so no timer needed; Off → no timer; interval → poll
      // at the operator's chosen cadence.
      if (this._drawerPingTimer) {
        clearInterval(this._drawerPingTimer);
        this._drawerPingTimer = null;
      }
      if (dh && dh.ping_enabled && seconds !== 0 && seconds !== -1) {
        const pingMs = seconds * 1000;
        this._drawerPingTimer = setInterval(() => {
          if (!this.drawerHost || !this.drawerHost.ping_enabled) {
            return;
          }
          this._pollWrap(this.loadHostPingHistory(this.drawerHost.id));
        }, pingMs);
      }
      // SNMP iface / temp / host history follow the same picker
      // cadence as the main history timer. Self-healing for the
      // initial-fetch-empty case (sampler hadn't ticked yet, transient
      // backend error). Off → no timer; otherwise (Live OR interval)
      // re-fetch on tick. Same `_snmpHasProbeTarget` gate as every
      // other SNMP fetch site so hosts with the sampler running via
      // `snmp_name` but no curated UI checkbox ticked still self-heal.
      if (this._drawerSnmpHistoryTimer) {
        clearInterval(this._drawerSnmpHistoryTimer);
        this._drawerSnmpHistoryTimer = null;
      }
      if (dh && this._snmpHasProbeTarget(dh) && seconds !== 0) {
        const liveMode = seconds === -1;
        const snmpMs = (liveMode ? 30 : seconds) * 1000;
        this._drawerSnmpHistoryTimer = setInterval(() => {
          if (!this.drawerHost) {
            return;
          }
          if (!this._snmpHasProbeTarget(this.drawerHost)) {
            return;
          }
          const hrs = this.hostHistoryRange || 1;
          if (typeof this.loadHostSnmpHistory === 'function') {
            this._pollWrap(this.loadHostSnmpHistory(this.drawerHost.id, hrs));
          }
          if (typeof this.loadHostSnmpIfaceHistory === 'function') {
            this._pollWrap(this.loadHostSnmpIfaceHistory(this.drawerHost.id, hrs));
          }
          if (typeof this.loadHostSnmpTempHistory === 'function') {
            this._pollWrap(this.loadHostSnmpTempHistory(this.drawerHost.id, hrs));
          }
        }, snmpMs);
      }
      // Sparklines — Off kills the timer; Live / interval
      // modes restart at the 5min baseline (sparklines are coarse
      // 24h aggregates, the picker doesn't change their cadence).
      if (this._sparksTimer) {
        clearInterval(this._sparksTimer);
        this._sparksTimer = null;
      }
      if (seconds !== 0) {
        try {
          this.pollSparks();
        } catch (_) {
        }
      }
    },

    get counts() {
      // `update` counts ONLY actionable live updates — running items
      // with a newer remote digest. Offline / orphan containers with
      // stale digests get tracked separately under `update_offline`
      // so the topbar nav badge + filter chip + any other consumer
      // doesn't show "1 pending update" for a stack whose only stale
      // item is an exited orphan that the Update-stack button can't
      // fix anyway. Mirrors the per-stack rollup in `logic/gather.py`
      // and the per-node rollup in `nodesView` so all three count
      // sources read consistently.
      const c = {update: 0, update_offline: 0, uptodate: 0, unknown: 0, error: 0, ignored: 0, healthy: 0, degraded: 0, offline: 0};
      for (const i of this.items) {
        if (i.status === 'update') {
          if (i.health === 'offline') {
            c.update_offline++;
          } else {
            c.update++;
          }
        } else if (i.status === 'up-to-date') {
          c.uptodate++;
        } else {
          if (i.status === 'unknown') {
            c.unknown++;
          } else {
            if (i.status === 'error') {
              c.error++;
            } else {
              if (i.status === 'ignored') {
                c.ignored++;
              }
            }
          }
        }
        if (i.health === 'healthy') {
          c.healthy++;
        } else {
          if (i.health === 'degraded') {
            c.degraded++;
          } else {
            if (i.health === 'offline') {
              c.offline++;
            }
          }
        }
      }
      return c;
    },
    get filteredStacks() {
      const q = this.search.toLowerCase();
      return this.stacks
        .map(s => ({...s, items: s.items.filter(i => this.matches(i, q))}))
        .filter(s => s.items.length > 0);
    },
    get filteredItems() {
      const q = this.search.toLowerCase();
      return this.items.filter(i => this.matches(i, q));
    },
    get sortedFiltered() {
      const arr = [...this.filteredItems];
      const f = this.sortField, dir = this.sortDir === 'asc' ? 1 : -1;
      const statusRank = {update: 0, error: 1, unknown: 2, 'up-to-date': 3, ignored: 4};
      arr.sort((a, b) => {
        let va, vb;
        if (f === 'status') {
          va = statusRank[a.status] ?? 99;
          vb = statusRank[b.status] ?? 99;
        } else if (f === 'uptime') {
          // uptimeFor returns ms since epoch (start time). Newer starts = larger
          // number, and we want "youngest first" when ascending. Missing values
          // sort last regardless of direction.
          const ua = this.uptimeFor(a);
          const ub = this.uptimeFor(b);
          if (ua == null && ub == null) {
            return 0;
          }
          if (ua == null) {
            return 1;
          }
          if (ub == null) {
            return -1;
          }
          va = ua;
          vb = ub;
        } else {
          va = (a[f] || '').toString().toLowerCase();
          vb = (b[f] || '').toString().toLowerCase();
        }
        if (va < vb) {
          return -1 * dir;
        }
        if (va > vb) {
          return 1 * dir;
        }
        return 0;
      });
      return arr;
    },
    matches(item, q) {
      if (q) {
        const hay = [item.name, item.image, item.stack, item.tag].filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(q)) {
          return false;
        }
      }
      if (this.statusFilter && item.status !== this.statusFilter) {
        return false;
      }
      if (this.healthFilter && item.health !== this.healthFilter) {
        return false;
      }
      return true;
    },

    applyTheme() {
      const sysLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
      const resolved = this.themePref === 'auto' ? (sysLight ? 'light' : 'dark') : this.themePref;
      document.documentElement.setAttribute('data-theme', resolved);
    },
    cycleTheme() {
      const order = ['auto', 'light', 'dark'];
      this.themePref = order[(order.indexOf(this.themePref) + 1) % order.length];
      // Cache in localStorage for fast-path first-paint on the next
      // page load (avoids a flash of wrong-theme while /api/me round-
      // trips). The DB is the cross-browser / cross-machine source
      // of truth — write through to the user's `ui_prefs.theme` so the
      // operator's preference follows them across browsers.
      try {
        localStorage.setItem('theme', this.themePref);
      } catch (_) {
      }
      this.applyTheme();
      this.persistThemePref(this.themePref);
    },
    // Write-through to /api/me/ui-prefs so the theme follows the
    // operator across browsers. Best-effort — a network blip leaves
    // the localStorage cache as the fallback. Skipped for API-token
    // pseudo-users (negative ids) since /api/me/ui-prefs returns 400
    // for them.
    async persistThemePref(value) {
      if (!this.me || !this.me.id || this.me.id < 0) {
        return;
      }
      try {
        await fetch('/api/me/ui-prefs', {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({prefs: {theme: value}}),
        });
      } catch (e) {
        // Localised cache still has the new value — operator sees the
        // theme they picked on this browser; the next /api/me load will
        // re-sync if the DB write eventually succeeds.
        if (window.console && console.warn) {
          console.warn('[theme] persist to DB failed:', e);
        }
      }
    },
    async persistHostHistoryRange(value) {
      if (!this.me || !this.me.id || this.me.id < 0) {
        return;
      }
      const n = Number(value);
      if (!Number.isFinite(n) || n <= 0) {
        return;
      }
      try {
        await fetch('/api/me/ui-prefs', {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({prefs: {host_history_range: n}}),
        });
      } catch (e) {
        if (window.console && console.warn) {
          console.warn('[host_history_range] persist to DB failed:', e);
        }
      }
    },
    _cssVar(name) {
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    },

    _busyKey(kind, id) {
      return `${kind}:${id}`;
    },
    isStackBusy(stack) {
      if (!stack || !stack.stack_id) {
        return false;
      }
      if (this.busy[this._busyKey('stack', stack.stack_id)]) {
        return true;
      }
      return this.activeOps.some(o => o.op_type === 'update_stack' && String(o.target_id) === String(stack.stack_id));
    },
    isItemBusy(item) {
      if (!item) {
        return false;
      }
      if (this.busy[this._busyKey('ctn', item.raw_id)]) {
        return true;
      }
      if (item.type === 'orphan') {
        return this.activeOps.some(o => o.op_type === 'remove_container' && o.target_id === item.raw_id);
      }
      if (item.stack_id) {
        return this.isStackBusy({stack_id: item.stack_id});
      }
      if (item.type === 'container') {
        return this.activeOps.some(o => ['update_container', 'remove_container', 'restart_container'].includes(o.op_type) && o.target_id === item.raw_id);
      }
      return false;
    },
    isServiceBusy(item) {
      if (!item) {
        return false;
      }
      if (this.busy[this._busyKey('svc', item.raw_id)]) {
        return true;
      }
      return this.activeOps.some(o => o.op_type === 'restart_service' && o.target_id === item.raw_id);
    },
    isRestartBusy(item) {
      if (!item) {
        return false;
      }
      if (item.type === 'service') {
        return this.isServiceBusy(item);
      }
      return this.isItemBusy(item);
    },
    _busyTimers: {},
    _markBusy(key) {
      this.busy = {...this.busy, [key]: true};
      if (this._busyTimers[key]) {
        clearTimeout(this._busyTimers[key]);
      }
      this._busyTimers[key] = setTimeout(() => {
        delete this._busyTimers[key];
        if (this.busy[key]) {
          const n = {...this.busy};
          delete n[key];
          this.busy = n;
        }
      }, 3000);
    },
    _holdBusy(key) {
      if (this._busyTimers[key]) {
        clearTimeout(this._busyTimers[key]);
        delete this._busyTimers[key];
      }
      if (!this.busy[key]) {
        this.busy = {...this.busy, [key]: true};
      }
    },
    _clearBusy(key) {
      if (this._busyTimers[key]) {
        clearTimeout(this._busyTimers[key]);
        delete this._busyTimers[key];
      }
      if (this.busy[key]) {
        const n = {...this.busy};
        delete n[key];
        this.busy = n;
      }
    },
    _opBusyKey(op) {
      if (!op) {
        return null;
      }
      if (op.op_type === 'update_stack') {
        return this._busyKey('stack', op.target_id);
      }
      if (['update_container', 'remove_container', 'restart_container'].includes(op.op_type)) {
        return this._busyKey('ctn', op.target_id);
      }
      if (op.op_type === 'restart_service') {
        return this._busyKey('svc', op.target_id);
      }
      return null;
    },

    statusKey(s) {
      return (s || 'unknown').replace('up-to-date', 'ok');
    },
    // Split a host's network_ifaces into "real" (public-facing physical /
    // virtual NICs the operator cares about) and "internal" (docker veth
    // pairs, br-<id> bridges, docker0, docker_gwbridge, lo). On a Docker
    // host with N running containers there are N+ veths plus the bridge
    // pool — this turned the drawer's Network card into a 50-row wall.
    // The "real" list renders by default; the "internal" group is hidden
    // behind a toggle keyed by host.id in `networkIfacesShowDocker` so
    // each host can be expanded independently.
    networkIfacesPartition(h) {
      const ifaces = (h && h.network_ifaces) || [];
      const ifaceName = x => (typeof x === 'string' ? x : (x && x.name) || '');
      const isInternal = name => {
        const n = (name || '').toLowerCase();
        return n === 'lo'
          || n === 'docker0'
          || n === 'docker_gwbridge'
          || n.startsWith('veth')
          || n.startsWith('br-')      // docker bridge networks (br-<12hex>)
          || n.startsWith('cni')      // k8s pod network
          || n.startsWith('flannel')
          || n.startsWith('cali')     // calico
          || n.startsWith('kube-')
          || n.startsWith('vxlan')
          || n.startsWith('vmbr');    // proxmox bridge (when surfaced)
      };
      const real = [], internal = [];
      for (const iface of ifaces) {
        (isInternal(ifaceName(iface)) ? internal : real).push(iface);
      }
      return {real, internal};
    },
    networkIfacesShowDocker: {},  // {host_id: bool} — toggle map per host
    toggleNetworkIfacesDocker(h) {
      if (!h || !h.id) {
        return;
      }
      this.networkIfacesShowDocker[h.id] = !this.networkIfacesShowDocker[h.id];
    },
    // busy / idle split for switches that expose 30+ ports via
    // SNMP. The default "real" list mixes meaningful interfaces
    // (eth0 / vlan2 / tun1 — has traffic OR an IP) with idle ports
    // (Port-Channel21..32, unused TwoPointFiveGigabitEthernet ports —
    // no traffic, no addrs). Without this split a Cisco SG300's drawer
    // is a 50-row wall of mostly-empty rows. `busy` = at least one of
    // (rx_bytes, tx_bytes, addrs.length) is non-zero; everything else
    // falls into `idle` and is hidden behind a per-host toggle.
    networkIfacesActivityPartition(h) {
      const partition = this.networkIfacesPartition(h);
      const busy = [], idle = [];
      for (const iface of partition.real) {
        const hasTraffic = this.hostIfaceHasTraffic(iface);
        const hasAddrs = (typeof iface === 'object' && (iface.addrs || []).length > 0);
        // Treat the loopback as "busy" so it always renders; operators
        // expect to see it and it's a useful sanity-check signal.
        const name = (typeof iface === 'object' ? (iface.name || '') : iface).toLowerCase();
        const isLoopback = name === 'lo' || name === 'loopback';
        (hasTraffic || hasAddrs || isLoopback ? busy : idle).push(iface);
      }
      // Sort busy by total traffic descending so the dominant NIC
      // floats to the top — operator's eye reads the meaningful rows
      // first instead of having to scan a long alphabetical list.
      busy.sort((a, b) => {
        const ta = (+(a && a.rx_bytes) || 0) + (+(a && a.tx_bytes) || 0);
        const tb = (+(b && b.rx_bytes) || 0) + (+(b && b.tx_bytes) || 0);
        return tb - ta;
      });
      return {busy, idle, internal: partition.internal};
    },
    networkIfacesShowIdle: {},  // per-host toggle for the idle group
    toggleNetworkIfacesIdle(h) {
      if (!h || !h.id) {
        return;
      }
      this.networkIfacesShowIdle[h.id] = !this.networkIfacesShowIdle[h.id];
    },
    // Cap the busy-iface list to the top 10 by traffic, with a
    // per-host "Show all (N)" toggle. Switches with 52+ ports overflow
    // the drawer even after the show-idle filter; the operator wants
    // the loudest 10 by default and can opt into the rest.
    networkIfacesBusyCap: 10,
    networkIfacesShowAllBusy: {},  // per-host toggle for the busy-cap group
    toggleNetworkIfacesBusyAll(h) {
      if (!h || !h.id) {
        return;
      }
      this.networkIfacesShowAllBusy[h.id] = !this.networkIfacesShowAllBusy[h.id];
    },
    networkIfacesBusyVisible(h) {
      const busy = this.networkIfacesActivityPartition(h).busy;
      if (this.networkIfacesShowAllBusy[h.id]) {
        return busy;
      }
      return busy.slice(0, this.networkIfacesBusyCap);
    },
    networkIfacesBusyHiddenCount(h) {
      const busy = this.networkIfacesActivityPartition(h).busy;
      return Math.max(0, busy.length - this.networkIfacesBusyCap);
    },
    // per-interface SNMP traffic helpers. SNMP-derived
    // `network_ifaces[]` rows carry `rx_bytes` / `tx_bytes` /
    // `oper_status`; node-exporter / Beszel / Pulse rows have
    // `name` + `mac` + `addrs` but no traffic counters. Helpers
    // gracefully no-op when the rows lack the SNMP fields so
    // non-SNMP hosts don't see empty traffic rows.
    hostIfaceHasTraffic(iface) {
      if (!iface || typeof iface !== 'object') {
        return false;
      }
      const rx = +iface.rx_bytes || 0;
      const tx = +iface.tx_bytes || 0;
      return rx > 0 || tx > 0;
    },
    // Largest rx+tx total across the host's REAL interfaces — used to
    // normalise per-interface bar widths so the busiest NIC fills 100%
    // and the rest scale relative. Internal (docker / veth / etc.)
    // interfaces are excluded from the max so a noisy docker0 doesn't
    // drown the legitimate eth0/wlan0 traffic visually.
    hostIfaceMaxTotal(h) {
      const ifaces = this.networkIfacesPartition(h).real || [];
      let max = 0;
      for (const i of ifaces) {
        if (!this.hostIfaceHasTraffic(i)) {
          continue;
        }
        const t = (+i.rx_bytes || 0) + (+i.tx_bytes || 0);
        if (t > max) {
          max = t;
        }
      }
      return max;
    },
    // Inline `:style` for an interface's stacked rx/tx bar. `--rx-pct`
    // and `--tx-pct` are rendered as widths inside the bar. Returns
    // empty string when no traffic data is available so the template
    // can short-circuit without rendering an empty bar.
    hostIfaceBarStyle(iface, maxTotal) {
      if (!this.hostIfaceHasTraffic(iface) || !(maxTotal > 0)) {
        return '';
      }
      const total = (+iface.rx_bytes || 0) + (+iface.tx_bytes || 0);
      const totalPct = (total / maxTotal) * 100;
      const rxShare = total > 0 ? (+iface.rx_bytes || 0) / total : 0;
      const rxPct = totalPct * rxShare;
      const txPct = totalPct - rxPct;
      return `--rx-pct: ${rxPct.toFixed(2)}%; --tx-pct: ${txPct.toFixed(2)}%;`;
    },
    // UPS card helpers (APC PowerNet-MIB). Pill class for the
    // status badge, level class for the battery gauge (matching the
    // .stat-bar warn/crit convention), and human-readable runtime
    // formatter. All gracefully handle missing data; the card itself
    // is gated on `h.host_ups_status || h.host_battery_percent` in
    // the template.
    upsStatusPillClass(status) {
      const s = String(status || '').toLowerCase();
      if (s === 'online') {
        return 'pill-ok';
      }
      if (s === 'on-battery' || s === 'on-smart-boost' || s === 'on-smart-trim') {
        return 'pill-update';
      }
      if (s === 'off' || s === 'rebooting' || s.includes('bypass')
        || s === 'hardware-failure-bypass' || s === 'sleeping-until') {
        return 'pill-error';
      }
      return 'pill-unknown';
    },
    upsStatusLabel(status) {
      // Pretty-print the snake-case enum from PowerNet-MIB. i18n keys
      // exist for the canonical set; unknown values fall through to
      // the raw enum string. Short-circuit when the status is empty
      // / null so the i18n loader doesn't see a missing-key probe
      // for `host_drawer.ups.status_` (no enum value).
      const s = String(status || '').toLowerCase();
      if (!s) {
        return '';
      }
      const key = `host_drawer.ups.status_${s.replace(/-/g, '_')}`;
      const translated = this.t(key);
      return (translated && translated !== key) ? translated : (status || '');
    },
    upsBatteryLevel(pct) {
      // Inverse of the .stat-bar warn/crit semantics — for batteries,
      // LOW is bad. <20% = crit (red), <50% = warn (amber), else ok.
      const n = +pct;
      if (!Number.isFinite(n)) {
        return '';
      }
      if (n < 20) {
        return 'crit';
      }
      if (n < 50) {
        return 'warn';
      }
      return '';
    },
    // Battery status enum (from PowerNet-MIB upsBasicBatteryStatus).
    // Operator-requested: render as a coloured pill instead of the
    // raw "battery-normal" string. battery-normal → green (pill-ok),
    // battery-low → amber (pill-update), battery-in-fault → red
    // (pill-error), unknown → grey (pill-unknown). Mirror of
    // `upsStatusPillClass` for the output-status badge.
    upsBatteryStatusPillClass(status) {
      const s = String(status || '').toLowerCase();
      if (s === 'battery-normal') {
        return 'pill-ok';
      }
      if (s === 'battery-low') {
        return 'pill-update';
      }
      if (s === 'battery-in-fault') {
        return 'pill-error';
      }
      return 'pill-unknown';
    },
    upsBatteryStatusLabel(status) {
      // Pretty-print the snake-case enum from PowerNet-MIB. i18n keys
      // exist for the canonical set; unknown values fall through to
      // the raw enum string. Same shape as `upsStatusLabel` so the
      // label / pill pair stays consistent across translations.
      const s = String(status || '').toLowerCase();
      const key = `host_drawer.ups.battery_${s.replace(/-/g, '_')}`;
      const translated = this.t(key);
      return (translated && translated !== key) ? translated : (status || '');
    },
    // Dell server-health pill helpers. All four lean
    // on the standard Dell Systems Management Server Health enum
    // (ok / non-critical / critical / non-recoverable / unknown /
    // other). Two flavours: server-health row status (fans / temps /
    // PSUs / voltages — string label like "ok" / "critical") and
    // physical/virtual disk state (string label like "online" /
    // "rebuild" / "failed"). The pill-* token family is reused so
    // the colour family matches every other status pill in the SPA.
    dellHealthPillClass(status) {
      const s = String(status || '').toLowerCase();
      if (s === 'ok') {
        return 'pill-ok';
      }
      if (s === 'non-critical') {
        return 'pill-update';
      }
      if (s === 'critical' || s === 'non-recoverable') {
        return 'pill-error';
      }
      return 'pill-unknown';
    },
    // HTTP probe TLS expiry pill class. Three-state: red when expired
    // (negative days remaining), amber when within the operator-tunable
    // warning window (`me.client_config.http_probe_cert_warning_days`,
    // default 30 days), green otherwise. Mirrors `upsStatusPillClass`
    // / `dellHealthPillClass` shape.
    httpProbeTlsPillClass(daysRemaining) {
      const d = Number(daysRemaining);
      if (!Number.isFinite(d)) {
        return 'pill-muted';
      }
      if (d < 0) {
        return 'pill-error';
      }
      const warn = Number((this.me && this.me.client_config && this.me.client_config.http_probe_cert_warning_days) || 30);
      if (d <= warn) {
        return 'pill-warning';
      }
      return 'pill-ok';
    },
    dellHealthLabel(status) {
      // i18n key family mirrors upsBatteryStatusLabel — unknown values
      // capitalise the raw string instead of crashing OR rendering the
      // raw lowercase wire form ("ok" / "critical").
      const s = String(status || '').toLowerCase();
      if (!s) {
        return '';
      }
      const key = `host_drawer.server_health.status_${s.replace(/-/g, '_')}`;
      const translated = this.t(key);
      if (translated && translated !== key) {
        return translated;
      }
      return s.charAt(0).toUpperCase() + s.slice(1);
    },
    // Stack-update convergence summary — pulls the latest "Convergence
    // poll: N service(s) still updating: …" event line off a running
    // op's event log and returns a tidy one-line summary string for
    // the active-ops panel. Backend emits these lines from
    // `logic/ops.py:_await_stack_convergence`; the SPA renders them
    // inline so the operator sees rolling-update progress instead of
    // staring at a 300s spinner. Returns '' when the op is NOT in a
    // convergence wait (other op_types, or stack-update before any
    // poll has logged), so the consumer can gate via `x-if`.
    stackConvergenceSummary(op) {
      if (!op || op.status !== 'running') {
        return '';
      }
      if (op.op_type !== 'update_stack') {
        return '';
      }
      const events = Array.isArray(op.events) ? op.events : [];
      let latestPoll = '';
      let latestWaiting = '';
      // Walk newest → oldest. The convergence-poll line is what we
      // most want; fall back to the "Waiting for stack convergence
      // (timeout=…, poll=…)" line if no poll has fired yet so the
      // operator at least sees "convergence wait started".
      for (let i = events.length - 1; i >= 0; i--) {
        const msg = String((events[i] || {}).msg || '');
        if (!latestPoll && msg.startsWith('Convergence poll:')) {
          latestPoll = msg;
          break;
        }
        if (!latestWaiting && msg.startsWith('Waiting for stack convergence')) {
          latestWaiting = msg;
        }
      }
      if (latestPoll) {
        // Strip the "Convergence poll: " prefix so the chip reads as
        // a clean one-liner: "2 service(s) still updating: …". Keeps
        // the operator's eye on the actionable part.
        return latestPoll.replace(/^Convergence poll:\s*/, '');
      }
      if (latestWaiting) {
        return this.t('active_ops.convergence_starting') || 'Waiting for stack convergence…';
      }
      return '';
    },
    // Helper: index of a provider in the fallback chain, or -1 if not in chain.
    fallbackPriority(name) {
      const order = Array.isArray(this.settings.ai_fallback_order)
        ? this.settings.ai_fallback_order : [];
      return order.indexOf(name);
    },
    // Toggle a provider's membership in the fallback chain. Adding
    // appends to the end (lowest priority); removing splices out and
    // shifts the rest up. Operator can re-order via the up/down chips
    // surfaced in the per-card UI below.
    toggleFallbackProvider(name) {
      const order = Array.isArray(this.settings.ai_fallback_order)
        ? this.settings.ai_fallback_order.slice() : [];
      const i = order.indexOf(name);
      if (i >= 0) {
        order.splice(i, 1);
      } else {
        order.push(name);
      }
      this.settings.ai_fallback_order = order;
      this.markAiFormDirty();
    },
    // Move a provider up (-1) or down (+1) in the fallback order.
    moveFallbackProvider(name, delta) {
      const order = Array.isArray(this.settings.ai_fallback_order)
        ? this.settings.ai_fallback_order.slice() : [];
      const i = order.indexOf(name);
      if (i < 0) {
        return;
      }
      const j = i + delta;
      if (j < 0 || j >= order.length) {
        return;
      }
      [order[i], order[j]] = [order[j], order[i]];
      this.settings.ai_fallback_order = order;
      this.markAiFormDirty();
    },
    // Physical-disk state pill — Dell OMSA arrayDiskState labels.
    // Visual encoding:
    // green  (pill-ok)     — `online` (active in a RAID array, healthy)
    // blue   (pill-info)   — `ready` (present, idle, available — standby)
    // red    (pill-error)  — `failed` / `offline` / `degraded` / `removed` / `fault`
    // amber  (pill-update) — every transient / advisory state.
    // Distinguishing online (active member) from ready (idle spare) lets
    // operators tell at a glance which disks are CARRYING the array vs
    // which are sitting idle for hot-swap / hot-spare duty.
    dellPdStatePillClass(state) {
      const s = String(state || '').toLowerCase();
      if (s === 'online') {
        return 'pill-ok';
      }
      if (s === 'ready') {
        return 'pill-info';
      }
      if (s === 'failed' || s === 'offline' || s === 'degraded'
        || s === 'removed' || s === 'fault') {
        return 'pill-error';
      }
      if (s === 'rebuild' || s === 'rebuilding' || s === 'recovering'
        || s === 'replacing' || s === 'replaced'
        || s === 'foreign' || s === 'blocked' || s === 'clear'
        || s === 'non-raid' || s === 'ready-foreign'
        || s === 'read-only' || s === 'uncertified'
        || s === 'smart-alert' || s === 'predictive-failure') {
        return 'pill-update';
      }
      return 'pill-unknown';
    },
    // Virtual-disk state pill — same online/ready split as physical
    // disks. `online` = array carrying I/O; `ready` = array initialised
    // but idle / standby.
    dellVdStatePillClass(state) {
      const s = String(state || '').toLowerCase();
      if (s === 'online') {
        return 'pill-ok';
      }
      if (s === 'ready') {
        return 'pill-info';
      }
      if (s === 'failed' || s === 'offline'
        || s === 'failed-redundancy'
        || s === 'permanently-degraded') {
        return 'pill-error';
      }
      if (s === 'degraded' || s === 'verifying' || s === 'resynching'
        || s === 'regenerating' || s === 'rebuilding'
        || s === 'formatting' || s === 'reconstructing'
        || s === 'initializing' || s === 'background-init'
        || s === 'degraded-redundancy') {
        return 'pill-update';
      }
      return 'pill-unknown';
    },
    // Disk-state pill label resolver — try i18n first, fall back to a
    // capitalised version of the raw enum string. Mirrors the
    // dellHealthLabel pattern: `host_drawer.server_health.status_<name>`
    // is the canonical key family (hyphens become underscores so
    // `non-raid` → `status_non_raid`). Lower-case at the source-of-truth
    // (Dell OMSA enum constants in logic/snmp.py) stays the wire format;
    // this helper applies translation per locale + capitalisation
    // fallback for novel enum values that don't have a key yet.
    dellStateLabel(state) {
      const s = String(state || '').toLowerCase();
      if (!s) {
        return '';
      }
      const key = 'host_drawer.server_health.status_' + s.replace(/-/g, '_');
      const tr = this.t(key);
      if (tr && tr !== key) {
        return tr;
      }
      return s.charAt(0).toUpperCase() + s.slice(1);
    },
    // Threshold at which Server health sub-sections (Physical disks /
    // Voltages) collapse by default. Above this count the dense
    // multi-column layout kicks in AND a "Show all (N) / Show fewer"
    // toggle gates the visible row count to keep the panel compact.
    SERVER_HEALTH_COLLAPSE_THRESHOLD: 12,
    // First-N rows shown when the section is collapsed. Tuned so the
    // collapsed view spans roughly two-three rows of the dense
    // multi-column grid (auto-fit minmax(190px, 1fr) → 2-4 columns
    // depending on drawer width × ~2 rows ≈ 6 visible items).
    SERVER_HEALTH_COLLAPSED_LIMIT: 6,
    // Narrow-viewport row cap. Below 480px the dense multi-column grid
    // collapses to a single column AND the outer card collapses to a
    // single column too — leaving the collapsed view at 6 rows still
    // feels long on a phone. Drop to 3 below 480px so the collapsed
    // section fits one screen on a phone. Pair this with the existing
    // `SERVER_HEALTH_COLLAPSED_LIMIT` desktop default in
    // `effectiveCollapsedLimit()`.
    SERVER_HEALTH_COLLAPSED_LIMIT_NARROW: 3,
    SERVER_HEALTH_NARROW_BREAKPOINT_PX: 480,
    // Resolve the collapsed-row limit per current viewport width. Reads
    // `window.innerWidth` so it auto-adjusts on rotate / resize without
    // requiring a manual refresh. Caller (serverHealthVisibleRows) reads
    // this on every render so the slice tracks the live viewport.
    effectiveCollapsedLimit() {
      const w = (typeof window !== 'undefined') ? (window.innerWidth || 0) : 0;
      if (w > 0 && w < this.SERVER_HEALTH_NARROW_BREAKPOINT_PX) {
        return this.SERVER_HEALTH_COLLAPSED_LIMIT_NARROW;
      }
      return this.SERVER_HEALTH_COLLAPSED_LIMIT;
    },
    // Returns the slice of rows to render given the host id + section
    // key + the underlying full-list array. When count > threshold AND
    // the section isn't expanded, returns the first N; otherwise
    // returns the full array. Section key namespaces the expand state
    // (`pd` / `volt`) so toggling Physical disks doesn't also expand
    // Voltages on the same host.
    serverHealthVisibleRows(hostId, section, rows) {
      if (!Array.isArray(rows)) {
        return [];
      }
      if (rows.length <= this.SERVER_HEALTH_COLLAPSE_THRESHOLD) {
        return rows;
      }
      const key = `${hostId}:${section}`;
      if (this.serverHealthExpanded[key]) {
        return rows;
      }
      return rows.slice(0, this.effectiveCollapsedLimit());
    },
    // True when the section has more rows than fit in the collapsed
    // view (so the toggle should render). False when count is at or
    // below the threshold (no collapse needed).
    serverHealthCollapsible(rows) {
      return Array.isArray(rows) && rows.length > this.SERVER_HEALTH_COLLAPSE_THRESHOLD;
    },
    serverHealthIsExpanded(hostId, section) {
      return !!this.serverHealthExpanded[`${hostId}:${section}`];
    },
    toggleServerHealthExpanded(hostId, section) {
      const key = `${hostId}:${section}`;
      this.serverHealthExpanded[key] = !this.serverHealthExpanded[key];
    },
    // Printer-MIB supply card helpers. Per-supply colour is
    // hand-mapped from common toner names (cyan / magenta / yellow /
    // black / waste); falls through to a neutral colour for unmapped
    // supplies. Level class follows the .stat-bar warn/crit
    // convention with INVERSE semantics (low fill = bad).
    printerSupplyColor(supply) {
      const name = String(supply && supply.name || '').toLowerCase();
      // Each colour token comes from :root so light + dark themes stay
      // consistent. CMYK-style names get their named colours; "waste"
      // / "drum" / "fuser" / etc. fall to text-dim.
      if (name.includes('cyan')) {
        return 'var(--info)';
      }
      if (name.includes('magenta')) {
        return '#ec4899';
      }
      if (name.includes('yellow')) {
        return 'var(--warning)';
      }
      if (name.includes('black')) {
        return 'var(--text)';
      }
      if (name.includes('waste')) {
        return 'var(--text-faint)';
      }
      return 'var(--text-dim)';
    },
    printerSupplyLevel(supply) {
      // Inverse semantics — low fill = warn / crit. Operator wants a
      // "running out" signal, not a "running high" one.
      const pct = supply && supply.percent;
      const n = +pct;
      if (!Number.isFinite(n)) {
        return '';
      }
      if (n < 10) {
        return 'crit';
      }
      if (n < 25) {
        return 'warn';
      }
      return '';
    },
    // Set of providers that returned data for AT LEAST ONE host on
    // the most recent /api/hosts response. Used by `providerStates()`
    // to suppress chips for globally-broken providers — if pulse
    // failed cluster-wide (operator typo'd the URL, hub container
    // down), we DON'T blame every individual host with `pulse_name`
    // set; those are global-config issues, not per-host problems.
    // Recomputed cheaply each time providerStates() runs since the
    // hosts list isn't huge.
    providersWorkingGlobally() {
      const seen = new Set();
      for (const h of (this.hosts || [])) {
        for (const p of (h.providers || [])) {
          seen.add(p);
        }
      }
      return seen;
    },
    // Per-host provider chip states. Returns an array of
    // { name: <provider>, state: 'ok' | 'failing' }
    // for chips that should render. Rules:
    // 1. Provider must be mapped on this host (the relevant
    //    `<provider>_name` / `ne_url` field is set).
    // 2. Provider must be globally enabled.
    // 3. Provider must be GLOBALLY HEALTHY (returned data for at
    //    least one host on this fleet). If pulse fails cluster-
    //    wide, the chip disappears from every host — that's a
    //    global-config issue, not a per-host one. The operator
    //    fixes the hub URL once, not on N hosts.
    // 4. State derivation:
    //    - 'ok'      → provider hit on THIS host AND its self-
    //                  reported status (when applicable) is not
    //                  paused/down/unreachable.
    //    - 'failing' → mapped on this host but provider didn't hit
    //                  here OR returned data with a paused/down
    //                  self-status. Chip turns red.
    // Per-provider chip colour resolver. Resolution order:
    // 1. `this.settings.provider_color_<name>` — live admin form
    //    state, hydrated by `loadSettings()` at init AND on every
    //    Save. This is what makes the chip-style update REACTIVELY
    //    as the operator drags the colour input around (Alpine
    //    tracks `settings` as a dependency of every binding that
    //    calls into here).
    // 2. `me.client_config.provider_colors[<name>]` — fallback for
    //    readonly viewers who don't fetch /api/settings. Reads from
    //    the snapshot the server stamped at the most recent
    //    /api/me round-trip, so a colour change here only takes
    //    effect on the next /api/me — fine for "view-only" users.
    // 3. Built-in distinct default so an unconfigured deploy still
    //    shows five different chip colours instead of two-or-three
    //    shared hues (pre-fix, ping shared node-exporter's amber
    //    and operators couldn't tell them apart in the row chips).
    providerColor(name) {
      const defaults = {
        beszel: '#22c55e',  // green  (matches pill-ok hue)
        pulse: '#3b82f6',  // blue   (matches pill-info hue)
        node_exporter: '#f59e0b',  // amber  (matches pill-update hue)
        webmin: '#a78bfa',  // purple (distinct slot for the 4th provider)
        ping: '#06b6d4',  // cyan   (distinct from amber + green; was conflating with exporter)
        snmp: '#ec4899',  // pink   (sixth provider; distinct from the existing five)
        port_scan: '#8b5cf6',  // violet (port-scan on-demand provider)
        http_probe: '#fb923c',  // orange (seventh host-stats provider — HTTP / TLS / DNS health probe)
        service_probe: '#14b8a6',  // teal (per-service reachability probe — distinct from http_probe's per-host orange)
      };
      // Live admin-form value first (reactive on every keystroke / save).
      const live = ((this.settings || {})['provider_color_' + name] || '').trim();
      if (live) {
        return live;
      }
      // Server-stamped snapshot for non-admin viewers.
      const map = (this.me && this.me.client_config && this.me.client_config.provider_colors) || {};
      const v = (map[name] || '').trim();
      return v || defaults[name] || 'currentColor';
    },
    // Inline style triplet for .chip.pill-custom — three CSS variables
    // (--chip-bg, --chip-br, --chip-fg) derived from the provider
    // colour via color-mix so the chip stays a soft tinted token rather
    // than a saturated background. The same pattern works for any
    // future dynamic-colour chip (asset categories, group badges, etc.).
    providerChipStyle(name) {
      const c = this.providerColor(name);
      return (
        '--chip-bg: color-mix(in srgb, ' + c + ' 18%, transparent); ' +
        '--chip-br: color-mix(in srgb, ' + c + ' 40%, transparent); ' +
        '--chip-fg: ' + c + ';'
      );
    },
    // Provider name → /img/icons/<slug>.svg filename. Mostly
    // identity except for `node_exporter` → `node-exporter` (the
    // resolver convention prefers hyphens over underscores in icon
    // filenames). Returns the bare slug; the consumer wraps it in
    // `url(/img/icons/<slug>.svg)` for the mask-image binding.
    providerIconSlug(name) {
      if (name === 'node_exporter') {
        return 'node-exporter';
      }
      if (name === 'http_probe') {
        return 'http-probe';
      }
      if (name === 'service_probe') {
        return 'service-probe';
      }
      return name;
    },
    // Inline style for `.provider-icon` — paints a mono SVG
    // mask in the per-provider chip colour. Use this on a `<span>`
    // when you want the provider's icon recoloured by the operator's
    // chip-colour customisation. Pairs naturally with
    // `providerChipStyle()` when the icon sits inside a `pill-custom`
    // chip (the parent's `--chip-fg` already provides the colour, so
    // the icon picks it up via `currentColor` automatically). Use this
    // helper when the icon stands ALONE (e.g. tab strip) where there's
    // no surrounding chip to inherit from.
    providerIconStyle(name) {
      return (
        '--provider-icon-url: url(/img/icons/' + this.providerIconSlug(name) + '.svg); '
        + 'color: ' + this.providerColor(name) + ';'
      );
    },
    // Stale-marker helpers for the UI.
    //
    // Backend stamps two markers on cache-seeded entries:
    // 1. `_stats_cache[id]._stale: true`              ← per-item stats
    // 2. `nodes_info[host]._stale_fields: [..]`       ← per-host telemetry
    // 3. `_stale_ts: <epoch_seconds>`                 ← persistence write
    //
    // The SPA dims any element bound to a stale value AND surfaces an
    // "X minutes ago" tooltip via `staleAge()`. This makes the
    // "provider went down" case visually explicit instead of letting
    // last-known-good values silently masquerade as live.
    isStale(obj) {
      if (!obj) {
        return false;
      }
      if (obj._stale === true) {
        return true;
      }
      const sf = obj._stale_fields;
      return Array.isArray(sf) && sf.length > 0;
    },
    isStaleField(obj, field) {
      if (!obj || !field) {
        return false;
      }
      const sf = obj._stale_fields;
      return Array.isArray(sf) && sf.indexOf(field) !== -1;
    },
    // Stale-grace countdown — surfaces when at least ONE field is
    // within 20% of its 24h grace expiry. Backend stamps
    // `_meta_stale_grace_remaining_s` (a `{key: seconds_remaining}`
    // map) at apply-time when any field crosses the warning
    // threshold. Returns the smallest remaining seconds across all
    // fields (worst-case for the operator), or null when no field
    // is in the warning window. The drawer banner reads this so
    // operators get a "data will be discarded in X" countdown
    // BEFORE the silent drop.
    staleGraceRemainingSeconds(obj) {
      if (!obj) {
        return null;
      }
      const m = obj._meta_stale_grace_remaining_s;
      if (!m || typeof m !== 'object') {
        return null;
      }
      let smallest = null;
      for (const k in m) {
        const v = +m[k];
        if (Number.isFinite(v) && v >= 0) {
          if (smallest === null || v < smallest) {
            smallest = v;
          }
        }
      }
      return smallest;
    },
    // Operator-facing human label for the smallest remaining grace
    // window. Returns "" when no field is in the warning window
    // (banner suppresses cleanly).
    staleGraceRemainingLabel(obj) {
      const s = this.staleGraceRemainingSeconds(obj);
      if (s === null || s === undefined) {
        return '';
      }
      if (s < 60) {
        return Math.round(s) + 's';
      }
      if (s < 3600) {
        return Math.round(s / 60) + 'm';
      }
      if (s < 86400) {
        return (s / 3600).toFixed(1) + 'h';
      }
      return (s / 86400).toFixed(1) + 'd';
    },
    // True when the bulk of this host's snapshot-eligible fields are
    // stale (i.e. an entire provider went down vs a transient one-key
    // blip). The drawer's banner widens to a more explicit message
    // and the per-field warning triangles are SUPPRESSED so the
    // operator gets one clear signal instead of every <dl> row
    // carrying a triangle.
    //
    // Threshold: ≥ 6 stale fields (matches the typical count covered
    // by a single-provider outage — host_cpu_percent, host_mem_total,
    // host_mem_used, host_disk_total, host_disk_used, host_uptime_s
    // is six). Fewer than that = partial / transient — keep the
    // per-field triangles for actionable detail.
    isAllStale(obj) {
      if (!obj) {
        return false;
      }
      const sf = obj._stale_fields;
      if (!Array.isArray(sf)) {
        return false;
      }
      return sf.length >= 6;
    },
    // Provider-level stale enumeration. A provider is "stale" when
    // it's MAPPED on the curated row (`*_name` / `ne_url` /
    // `snmp_enabled`) but missing from `h.providers` (the live-hits
    // list this gather cycle produced). The stale banner can surface
    // these names so operators see "Beszel + Pulse cached, NE live"
    // rather than just a generic "N field(s) restored" count. Order
    // mirrors the merge order documented in CLAUDE.md (Pulse → SNMP →
    // Beszel → NE → Webmin → Ping).
    staleProviders(h) {
      if (!h) {
        return [];
      }
      const got = new Set(h.providers || []);
      const out = [];
      const trim = v => String(v || '').trim();
      const push = (name, mapped) => {
        if (!mapped) {
          return;
        }
        if (got.has(name)) {
          return;
        }
        out.push(name);
      };
      push('pulse', !!trim(h.pulse_name));
      // SNMP: enabled + EITHER snmp_name OR address — same canonical
      // chain (aliases → snmp_name → address → SKIP) as the live
      // sampler / `_merge_one_host` / `rowHasProviderMapping`.
      push('snmp', h.snmp_enabled === true
        && (!!trim(h.snmp_name) || !!trim(h.address)));
      push('beszel', !!trim(h.beszel_name));
      push('node_exporter', !!trim(h.ne_url));
      push('webmin', !!trim(h.webmin_name));
      push('ping', !!h.ping_enabled);
      return out;
    },
    // Display label for a provider id — "node_exporter" → "exporter"
    // matches the existing chip rendering convention.
    providerDisplayName(name) {
      if (name === 'node_exporter') {
        return 'exporter';
      }
      return name;
    },
    staleAge(obj) {
      // return a clean fallback when `_stale_ts`
      // is 0 / missing / non-numeric. Pre-fix `fmtAgo` would either
      // render "Updated NaN ago" or empty string, depending on which
      // branch hit first; either way it's noise on a tooltip.
      // **Tooltip surface only** — returns the FULL i18n sentence
      // (`Last live data 4s ago — value restored from cache snapshot`).
      // For inline substitution into a larger template (e.g. the
      // drawer banner whose copy already wraps the time in its own
      // sentence), use `staleAgeShort(obj)` instead — passing
      // staleAge(h) into a {age} placeholder double-wraps the time.
      if (!obj) {
        return '';
      }
      const tsRaw = obj._stale_ts;
      const ts = Number(tsRaw);
      if (!Number.isFinite(ts) || ts <= 0) {
        try {
          return (window.t && window.t('stale_marker.never')) || '';
        } catch (_) {
          return '';
        }
      }
      const ms = ts * 1000;
      const ago = this.fmtAgo(ms);
      // i18n: tooltip surface, not visible label. Translators handle
      // the "stale_marker.tooltip" key with the {age} placeholder.
      try {
        return (window.t && window.t('stale_marker.tooltip', {age: ago})) || ('Last live data ' + ago + ' ago');
      } catch (_) {
        return 'Last live data ' + ago + ' ago';
      }
    },
    // Bare relative-time (e.g. `4s`, `12m`, `3h`) for the snapshot
    // timestamp on `obj._stale_ts`. Use this when injecting into a
    // larger i18n template that supplies its own `... ago` wrapping
    // (the host-drawer "Showing cached data" banner is the canonical
    // consumer). `staleAge(obj)` is for tooltip surfaces — passing it
    // into a {age} placeholder double-wraps the time. Empty string
    // when `_stale_ts` is missing / 0 / non-numeric so the outer
    // template still renders cleanly.
    staleAgeShort(obj) {
      if (!obj) {
        return '';
      }
      const ts = Number(obj._stale_ts);
      if (!Number.isFinite(ts) || ts <= 0) {
        return '';
      }
      return this.fmtAgo(ts * 1000);
    },
    // -----------------------------------------------------------------
    // Per-node aggregates for the Nodes view. Everything is computed
    // client-side off the same `stats` / `sparks` state the other views
    // use — no new backend endpoints. Items count toward a node iff
    // any of their `placements` match (services with multiple replicas
    // contribute to every node they run on).
    // -----------------------------------------------------------------
    itemsForNode(host) {
      return this.items.filter(it => {
        if (Array.isArray(it.placements) && it.placements.length) {
          return it.placements.some(p => p && p.node === host);
        }
        return it.node === host;
      });
    },

    nodeInfoFor(host) {
      return (this.nodesInfo && this.nodesInfo[host]) || {};
    },
    // Label for the green/red chip on a node row — reflects the
    // providers that actually probed THIS node. Falls back to
    // the global active set on rows missing per-node tracking (e.g.
    // before the first post-upgrade gather).
    nodeProviderChip(host) {
      const st = this.nodeStats(host);
      const arr = (st.nodeProvidersHit && st.nodeProvidersHit.length)
        ? st.nodeProvidersHit
        : (st.hostStatsSources || []);
      if (arr.length === 0) {
        return 'host';
      }
      if (arr.length === 1) {
        return arr[0] === 'node_exporter' ? 'exporter' : arr[0];
      }
      return `${arr.length} sources`;
    },
    // Hover tooltip for the chip — lists the providers that contributed
    // data for this specific node.
    nodeProviderList(host) {
      const st = this.nodeStats(host);
      const src = (st.nodeProvidersHit && st.nodeProvidersHit.length)
        ? st.nodeProvidersHit
        : (st.hostStatsSources || []);
      const arr = src.map(s => s === 'node_exporter' ? 'node-exporter' : s);
      return arr.length ? arr.join(', ') : 'none';
    },

    nodeCpuPercent(host) {
      // Prefer the host-provider's CPU% (Beszel/Pulse/NE) when present —
      // it's already a 0..100 number derived from /proc/stat (or
      // equivalent) and reflects total host load including processes
      // outside Docker. Falls back to the container-aggregate cpuRaw
      // (sum of per-container Docker stats) divided by core count.
      // Clamp at 100 — brief spikes over a single tick can exceed
      // cores*100 due to sub-second bursts, and a bar that pokes past
      // 100% looks broken.
      const {cpuRaw, hostCpuRaw, hasHostCpu, cores} = this.nodeStats(host);
      if (hasHostCpu) {
        return Math.min(100, hostCpuRaw);
      }
      if (!cores) {
        return 0;
      }
      return Math.min(100, cpuRaw / cores);
    },

    nodeMemPercent(host) {
      const {memUsage, memLimit} = this.nodeStats(host);
      if (!memLimit) {
        return 0;
      }
      return Math.min(100, (memUsage / memLimit) * 100);
    },

    nodeDiskPercent(host) {
      // No "of N" denominator available without a host-agent — render
      // a proportional bar against the fleet's busiest Docker daemon
      // so operators see which node is carrying the most Docker disk.
      const {dockerDisk} = this.nodeStats(host);
      if (!dockerDisk) {
        return 0;
      }
      let max = 0;
      const infos = this.nodesInfo || {};
      for (const k in infos) {
        const v = Number(infos[k] && infos[k].docker_disk_bytes) || 0;
        if (v > max) {
          max = v;
        }
      }
      if (!max) {
        return 0;
      }
      return Math.min(100, (dockerDisk / max) * 100);
    },

    nodeUptime(host) {
      // Prefer real host boot time (node-exporter). Fall back to the
      // "oldest still-running task" proxy when the exporter isn't
      // available — still per-node, still meaningful, just measuring
      // workload uptime instead of host uptime.
      const info = this.nodeInfoFor(host);
      const bootTs = info.host_boot_ts;
      const ts = (Number.isFinite(bootTs) && bootTs > 0) ? bootTs : info.oldest_running_ts;
      if (!ts) {
        return null;
      }
      return Math.max(0, Math.floor(Date.now() / 1000) - Math.floor(ts));
    },
    nodeUptimeKind(host) {
      // 'host' when sourced from node-exporter, 'docker' otherwise —
      // drives the caption on the Uptime tile.
      const info = this.nodeInfoFor(host);
      return (Number.isFinite(info.host_boot_ts) && info.host_boot_ts > 0) ? 'host' : 'docker';
    },

    stackStats(stack) {
      // Aggregate CPU / memory / image-size across every item in the stack so
      // collapsed stacks still display meaningful numbers on the group row.
      let cpu = 0, memUsage = 0, sizeRoot = 0;
      let hasStats = false, hasSize = false;
      for (const item of (stack.items || [])) {
        const s = this.statsFor(item);
        if (s.has_stats) {
          cpu += s.cpu_percent;
          memUsage += s.mem_usage;
          hasStats = true;
        }
        if (s.has_size) {
          sizeRoot += s.size_root;
          hasSize = true;
        }
      }
      return {cpu, memUsage, sizeRoot, hasStats, hasSize};
    },
    // Title Case a free-text string. Handles ALL-CAPS replies from
    // SNMP printer agents (HP / Brother often return "BLACK
    // CARTRIDGE") and mixed case alike. Each word's first letter
    // upper, rest lower; whitespace and punctuation preserved.
    // Two exceptions: known short brand acronyms (HP, IBM, ...) and
    // alphanumeric SKU codes (e.g. "3JA27A", "Q3960A") render ALL CAPS
    // — operator request 2026-05-01 for printer supply labels like
    // "Cyan Ink Hp 3ja27a" → "Cyan Ink HP 3JA27A".
    titleCase(s) {
      if (!s || typeof s !== 'string') {
        return s || '';
      }
      const brands = new Set(['hp', 'hpe', 'ibm', 'amd', 'arm', 'lg', 'rgb', 'rfid', 'usb', 'pci', 'io', 'smb', 'ftp', 'http', 'https', 'tls', 'ssl', 'nfc', 'vpn', 'dns', 'dhcp', 'ip', 'tcp', 'udp', 'rj45', 'poe', 'sfp', 'sas', 'sata', 'nvme', 'ssd', 'hdd', 'iot', 'ai', 'ml', 'gpu', 'cpu', 'ram', 'rom', 'vrm', 'bmc', 'ipmi', 'sff']);
      return s.replace(/\w\S*/g, w => {
        const lo = w.toLowerCase();
        if (brands.has(lo)) {
          return w.toUpperCase();
        }
        if (/[a-z]/i.test(w) && /[0-9]/.test(w)) {
          return w.toUpperCase();
        }
        return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
      });
    },
    memPercent(item) {
      const s = this.statsFor(item);
      if (!s.mem_limit) {
        return 0;
      }
      return Math.min(100, (s.mem_usage / s.mem_limit) * 100);
    },
    diskPercent(item) {
      const s = this.statsFor(item);
      if (!this._maxSize) {
        return 0;
      }
      return Math.min(100, (s.size_root / this._maxSize) * 100);
    },
    // LOW-VISUAL — stat-bar thresholds are operator-tunable.
    // Pre-fix the 60 / 85 thresholds were hardcoded; CLAUDE.md's
    // no-static-config rule says operator-tunable visual thresholds
    // belong in TUNABLES. Now sourced from `client_config` (per-call
    // read so an Admin → Config save lands on the next render).
    _statBarWarnPct() {
      const v = this.me && this.me.client_config && this.me.client_config.stat_bar_warn_pct;
      const n = parseInt(v, 10);
      return Number.isFinite(n) && n >= 30 && n <= 90 ? n : 60;
    },
    // Resolved global SNMP per-host walk concurrency — drives the
    // Admin → Hosts editor's per-row "Walk concurrency" input
    // placeholder. Shows the actual effective default value prefixed
    // with "Inherited: " so an empty input + placeholder "Inherited: 1"
    // visually distinguishes itself from a real value of 1 (pre-fix
    // operator couldn't tell whether they'd left the field blank or
    // typed 1). Sourced from `me.client_config.snmp_per_host_walk_concurrency`
    // so an Admin → Config save takes effect on the next /api/me round-trip.
    snmpWalkConcurrencyPlaceholder() {
      const v = this.me && this.me.client_config
        && this.me.client_config.snmp_per_host_walk_concurrency;
      const n = parseInt(v, 10);
      const num = Number.isFinite(n) && n >= 1 ? n : 1;
      return this.t('admin_hosts.snmp_walk_concurrency_placeholder', {value: num});
    },
    // Per-host wall-clock-budget input placeholder — same "Inherited: <N>"
    // pattern as walk_concurrency. Sourced from
    // me.client_config.snmp_wall_clock_budget_seconds (surfaced from the
    // tunable on /api/me).
    snmpWallClockBudgetPlaceholder() {
      const v = this.me && this.me.client_config
        && this.me.client_config.snmp_wall_clock_budget_seconds;
      const n = parseInt(v, 10);
      const num = Number.isFinite(n) && n >= 5 ? n : 60;
      return this.t('admin_hosts.snmp_walk_concurrency_placeholder', {value: num});
    },
    // Per-host SNMP vendor MIB selector. Empty list = auto-detect from
    // sysDescr (the common case; covers 95% of agents). Operator can
    // declare explicit vendors to bypass auto-detect — useful for
    // agents with stripped sysDescr or to force a vendor's walks. The
    // backend's _clean_host_snmp validates against the same vendor key
    // set: dell / cisco / apc / ucd / synology / printer.
    snmpVendorChecked(row, vendor) {
      const list = (row && row.snmp && Array.isArray(row.snmp.vendors))
        ? row.snmp.vendors : [];
      return list.includes(vendor);
    },
    // Apply or clear ``vendor`` on EVERY row that passes the current
    // hostsConfig filter. ``add=true`` = check the vendor on each row's
    // snmp.vendors array; ``add=false`` = remove it. Skips rows where
    // `snmp.enabled !== true` (the per-row checkbox group is gated on
    // that flag and wouldn't accept the click). Marks each touched row
    // dirty so the existing Save flow picks them up.
    bulkApplySnmpVendor(vendor, add) {
      if (!vendor || this.isReadonly()) {
        return;
      }
      const validSet = new Set(this.snmpVendorKeys());
      if (!validSet.has(vendor)) {
        return;
      }
      let touched = 0;
      const rows = this.filteredHostsConfig();
      for (const entry of rows) {
        const idx = (entry && typeof entry.idx === 'number') ? entry.idx : -1;
        if (idx < 0) {
          continue;
        }
        const cur = this.hostsConfig[idx];
        if (!cur) {
          continue;
        }
        const snmpIn = cur.snmp || {};
        if (snmpIn.enabled !== true) {
          continue;
        }
        const list = Array.isArray(snmpIn.vendors) ? snmpIn.vendors.slice() : [];
        const set = new Set(list);
        const had = set.has(vendor);
        if (add) {
          if (had) {
            continue;
          }
          set.add(vendor);
        } else {
          if (!had) {
            continue;
          }
          set.delete(vendor);
        }
        this.hostsConfig[idx].snmp = Object.assign({}, snmpIn, {vendors: Array.from(set).sort()});
        this.markHostRowDirty(idx);
        touched += 1;
      }
      if (touched > 0) {
        this.showToast(this.t(
          add ? 'admin_hosts.snmp_vendors_bulk_applied'
            : 'admin_hosts.snmp_vendors_bulk_cleared',
          {vendor: this.snmpVendorLabel(vendor), count: touched}
        ));
      }
    },
    // Last auto-detected vendor set for THIS curated row, sourced
    // from the matching live host's `host_snmp_active_vendors` field
    // (populated by `_merge_one_host` from the most recent successful
    // probe's diagnostic). Returns an empty list when (a) the host has
    // never been probed successfully, OR (b) the host doesn't appear
    // in the loaded `this.hosts` array (Admin → Hosts editor is open
    // but the Hosts view hasn't loaded yet — the helper just returns
    // empty until the row's host data lands). Helps operators new to
    // SNMP see what auto-detect picked before deciding whether to set
    // an explicit override.
    snmpAutoDetectedVendors(row) {
      if (!row || !row.id) {
        return [];
      }
      const list = Array.isArray(this.hosts) ? this.hosts : [];
      const live = list.find(h => h && h.id === row.id);
      const av = live && live.host_snmp_active_vendors;
      return Array.isArray(av) ? av.slice() : [];
    },
    // Canonical SNMP vendor key set sourced from /api/me's
    // client_config.snmp_vendor_keys (single source of truth — backed by
    // logic/snmp.py:_VALID_VENDOR_KEYS server-side). Adding a vendor in
    // _VENDOR_SIGNATURES surfaces a checkbox here on the next /api/me
    // round-trip without any frontend edit. Defence-in-depth fallback to
    // the historical six keys when /api/me hasn't hydrated yet OR the
    // server is older than this SPA build.
    snmpVendorKeys() {
      const cc = (this.me && this.me.client_config) || {};
      if (Array.isArray(cc.snmp_vendor_keys) && cc.snmp_vendor_keys.length) {
        return cc.snmp_vendor_keys;
      }
      return ['apc', 'cisco', 'dell', 'printer', 'synology', 'ucd'];
    },
    // Operator-friendly label for an SNMP vendor key. The persisted
    // values stay lowercase to match `_VALID_VENDOR_KEYS` server-side,
    // but the rendered checkbox text uses the brand-canonical case
    // (Dell / Cisco / APC / Synology / Printer / UCD/net-snmp).
    // Unknown keys fall through to a Title-Case fallback so a future
    // vendor added on the backend still renders sensibly without a
    // SPA edit.
    snmpVendorLabel(key) {
      const k = String(key || '').trim().toLowerCase();
      const map = {
        'apc': 'APC',
        'cisco': 'Cisco',
        'dell': 'Dell',
        'synology': 'Synology',
        'printer': 'Printer',
        'ucd': 'UCD/net-snmp',
      };
      if (k in map) {
        return map[k];
      }
      return k ? k[0].toUpperCase() + k.slice(1) : '';
    },
    toggleSnmpVendor(idx, vendor, checked) {
      const cur = this.hostsConfig[idx];
      const snmp = Object.assign({}, cur.snmp || {});
      const list = Array.isArray(snmp.vendors) ? snmp.vendors.slice() : [];
      const set = new Set(list);
      if (checked) {
        set.add(vendor);
      } else {
        set.delete(vendor);
      }
      snmp.vendors = Array.from(set).sort();
      this.hostsConfig[idx].snmp = snmp;
      this.markHostRowDirty(idx);
    },
    _statBarCritPct() {
      const v = this.me && this.me.client_config && this.me.client_config.stat_bar_crit_pct;
      const n = parseInt(v, 10);
      return Number.isFinite(n) && n >= 50 && n <= 99 ? n : 85;
    },
    barColor(pct) {
      // Kept for backward compat; prefer barLevel() which returns a CSS class.
      if (pct > this._statBarCritPct()) {
        return 'var(--danger)';
      }
      if (pct > this._statBarWarnPct()) {
        return 'var(--warning)';
      }
      return 'var(--success)';
    },
    barLevel(pct) {
      // Maps a percentage to the `.warn` / `.crit` class on `.stat-bar`, which
      // drives the fill colour from the stylesheet. Empty string = default green.
      if (pct > this._statBarCritPct()) {
        return 'crit';
      }
      if (pct > this._statBarWarnPct()) {
        return 'warn';
      }
      return '';
    },
    // Single source of truth for stat-bar a11y attrs. Returns a plain
    // object spread via x-bind so every `.stat-bar` consumer announces
    // as a real progressbar instead of an empty div. The label key
    // resolves through `t()` so screen readers get a localised hint
    // (e.g. "CPU 73%"). Pass null/undefined `pct` for "unknown" — the
    // resulting valuenow is omitted but valuetext still names the
    // metric. Replaces 20+ inline rewrites with one helper.
    statBarBind(pct, labelKey) {
      const numeric = (typeof pct === 'number' && isFinite(pct))
        ? Math.max(0, Math.min(100, Math.round(pct)))
        : null;
      const label = (labelKey && typeof this.t === 'function') ? this.t(labelKey) : '';
      const text = numeric == null
        ? (label || '—')
        : (label ? `${label} ${numeric}%` : `${numeric}%`);
      return {
        role: 'progressbar',
        'aria-valuemin': '0',
        'aria-valuemax': '100',
        'aria-valuenow': numeric == null ? null : String(numeric),
        'aria-valuetext': text,
      };
    },
    cpuLabel(pct) {
      return pct >= 10 ? pct.toFixed(0) + '%' : pct.toFixed(1) + '%';
    },
    imageRepo(item) {
      if (!item || !item.image) {
        return '';
      }
      const img = item.image;
      const tag = item.tag || '';
      if (tag && img.endsWith(':' + tag)) {
        return img.slice(0, -(tag.length + 1));
      }
      return img;
    },
    nodeSummary(item) {
      const ns = (item.placements || []).map(p => p.node).filter(n => n && n !== '?');
      return [...new Set(ns)].join(', ');
    },

    // Resolve the http://<host>:<published> URL for an item's exposed
    // port. Returns '' when no host can be determined OR the port
    // protocol isn't tcp (UDP / SCTP have no in-browser navigation).
    //
    // Host resolution chain:
    //   1. Direct `item.node` (only standalone containers carry this).
    //   2. First running placement's node from `item.placements` —
    //      this is where Swarm services live. Pre-fix this step was
    //      missing so services fell through to the Portainer URL
    //      (user-flagged: a service on a worker node linked to
    //      `portainer.example.com:9618` instead of
    //      `<worker-hostname>:<published>`).
    //   3. Any placement's node from `item.placements` (running pref
    //      first, but accept stopped placements as a last hint).
    //   4. Hostname extracted from the Portainer public URL — works
    //      for ingress-mode publishes that genuinely route on every
    //      Swarm node.
    //   5. window.location.hostname so the link still navigates
    //      somewhere reasonable in a dev / single-host setup.
    //
    // For steps 1-3, the resolved node-hostname is mapped against
    // curated hosts (id / host_hostname / label) and the curated row's
    // `address` field is preferred when available. When no curated
    // match exists, the raw node hostname itself is used (Swarm node
    // hostnames typically resolve in the operator's local DNS / hosts
    // file, otherwise the operator types the IP in Admin → Hosts and
    // the curated match kicks in).
    //
    // Protocol is always http:// — Docker port publish doesn't imply
    // TLS; operators with TLS-fronted services reach them via their
    // own ingress separately.
    itemPortLink(item, port) {
      if (!item || !port || !port.published) {
        return '';
      }
      const proto = (port.protocol || 'tcp').toLowerCase();
      if (proto !== 'tcp') {
        return '';
      }
      // Build the candidate node-hostname list in priority order.
      const candidateNodes = [];
      if (item.node) {
        candidateNodes.push(item.node);
      }
      if (Array.isArray(item.placements)) {
        // Running placements first, then everything else — same node
        // may appear twice but the curated-host lookup is identity-
        // tolerant so the duplicate is harmless.
        for (const p of item.placements) {
          if (p && p.node && p.state === 'running') {
            candidateNodes.push(p.node);
          }
        }
        for (const p of item.placements) {
          if (p && p.node) {
            candidateNodes.push(p.node);
          }
        }
      }
      const _hostsArr = Array.isArray(this.hosts) ? this.hosts : [];
      const _findCurated = (nodeName) => {
        if (!nodeName) {
          return null;
        }
        const lower = String(nodeName).toLowerCase();
        return _hostsArr.find(h => {
          if (!h) {
            return false;
          }
          return [h.id, h.host_hostname, h.label].some(c => c && String(c).toLowerCase() === lower);
        }) || null;
      };
      // Walk candidates in order, resolving via curated-host match
      // when possible. The raw node hostname is acceptable too (Swarm
      // hostnames usually resolve in the operator's local DNS) — we
      // just prefer the curated `address` when it's set.
      let host = '';
      for (const nodeName of candidateNodes) {
        if (!nodeName) {
          continue;
        }
        const match = _findCurated(nodeName);
        if (match) {
          host = (match.address || match.host_hostname || match.id || nodeName || '').trim();
          if (host) {
            break;
          }
        }
        // No curated match — fall back to the raw node hostname.
        host = String(nodeName).trim();
        if (host) {
          break;
        }
      }
      // Portainer public URL fallback — ingress-mode publishes land
      // on every Swarm node, so the public URL hostname is reasonable
      // when no placement-derived candidate worked.
      if (!host) {
        const pubUrl = (this.settings && this.settings.portainer_public_url) || '';
        if (pubUrl) {
          try {
            host = new URL(pubUrl).hostname;
          } catch (_) {
            // ignore
          }
        }
      }
      // Last resort: the SPA's own window hostname.
      if (!host) {
        host = window.location.hostname || '';
      }
      if (!host) {
        return '';
      }
      // Short-hostname → FQDN promotion. Swarm reports node names as
      // bare hostnames (e.g. `worker01`, `web01`) which don't always
      // resolve in the browser unless the user's DNS handles short
      // names. When the resolved host has NO dots, promote it to a
      // FQDN by learning the LAN domain suffix.
      //
      // Resolution order for the suffix:
      //   1. **Inspect curated hosts** — find any other curated row
      //      whose `address` OR `host_hostname` is a FQDN (multi-
      //      label, non-IP). Use the longest-common-suffix across
      //      those FQDNs as the operator's actual LAN domain. This
      //      is the authoritative source — if the operator has set
      //      `worker01.example.lan` as another host's address,
      //      we know `example.lan` is the right suffix.
      //   2. Fallback: last TWO labels of `window.location.hostname`.
      //      Naive "everything after the first dot" fails when the
      //      SPA itself sits on a sub-subdomain (e.g. SPA at
      //      `omnigrid.www.example.lan` — the LAN is still
      //      `example.lan`, not `www.example.lan`). Using the
      //      trailing two labels handles both shapes:
      //      `omnigrid.example.lan` → `example.lan`, AND
      //      `omnigrid.www.example.lan` → `example.lan`.
      //   3. When neither yields a multi-label suffix (e.g. browser
      //      at `localhost` or a bare-host setup), skip the promotion
      //      and use the bare hostname.
      // Skip the promotion entirely when host is an IPv4 literal —
      // appending a DNS suffix to an IP makes no sense.
      if (host && host.indexOf('.') < 0 && !/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) {
        let suffix = this._resolveLanSuffixFromCuratedHosts(host);
        if (!suffix) {
          // Fallback: last-2-labels of window.location.hostname.
          const winHost = window.location.hostname || '';
          const parts = winHost.split('.').filter(Boolean);
          if (parts.length >= 2 && !/^\d+$/.test(parts[parts.length - 1])) {
            suffix = parts.slice(-2).join('.');
          }
        }
        if (suffix && suffix.indexOf('.') >= 0) {
          host = host + '.' + suffix;
        }
      }
      return 'http://' + host + ':' + port.published;
    },

    // Learn the operator's LAN domain suffix from curated host
    // records. Walks `this.hosts` looking for any FQDN in `address` /
    // `host_hostname` / `label` (multi-label string, not an IPv4
    // literal, not matching the current host's bare name we're
    // promoting). Returns the longest-common-suffix shared by every
    // FQDN found — that's the operator's authoritative LAN domain.
    // Returns '' when no curated FQDN exists.
    //
    // Why longest-common-suffix: if the operator has
    // `worker01.example.lan` AND `web01.example.lan` AND
    // `mail.corp.example.lan`, the LCS is `example.lan` — the right
    // value. A single curated FQDN's suffix would falsely match
    // `mail.corp.example.lan` → `corp.example.lan` which isn't the
    // bare LAN root. With multiple FQDNs we hit `example.lan` correctly.
    _resolveLanSuffixFromCuratedHosts(promotingHost) {
      if (!Array.isArray(this.hosts)) {
        return '';
      }
      const promotingLower = String(promotingHost || '').toLowerCase();
      const fqdns = [];
      for (const h of this.hosts) {
        if (!h) {
          continue;
        }
        for (const fieldVal of [h.address, h.host_hostname, h.label]) {
          const v = String(fieldVal || '').trim().toLowerCase();
          if (!v || v.indexOf('.') < 0) {
            continue;
          }
          // Skip IPv4 literals — they're not FQDNs.
          if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(v)) {
            continue;
          }
          // Skip URL-shaped values (some `address` rows are full URLs).
          if (v.indexOf('://') >= 0 || v.indexOf('/') >= 0 || v.indexOf(':') >= 0) {
            continue;
          }
          // Skip the bare-host we're TRYING to promote (defence
          // against circular learning if the operator's `address`
          // already happens to be the bare short hostname).
          if (v === promotingLower) {
            continue;
          }
          // Only consider strings that look hostname-shaped.
          if (!/^[a-z0-9.-]+$/.test(v)) {
            continue;
          }
          fqdns.push(v);
        }
      }
      if (fqdns.length === 0) {
        return '';
      }
      // Compute longest-common-suffix label-wise across every FQDN.
      // Reverse the labels of each, find the common prefix, reverse
      // back. Truncate any leading single-label result — we need at
      // least 2 labels for a meaningful suffix.
      const splitRev = fqdns.map(f => f.split('.').reverse());
      let common = splitRev[0].slice();
      for (let i = 1; i < splitRev.length; i++) {
        const next = splitRev[i];
        const lim = Math.min(common.length, next.length);
        const out = [];
        for (let j = 0; j < lim; j++) {
          if (common[j] === next[j]) {
            out.push(common[j]);
          } else {
            break;
          }
        }
        common = out;
        if (common.length === 0) {
          break;
        }
      }
      if (common.length < 2) {
        return '';
      }
      return common.reverse().join('.');
    },

    // Resolve a Swarm node hostname to a curated host row so the SSH
    // terminal can target it. Tries (in order): exact id match, exact
    // host match, prefix match (so `host01` matches
    // `host01.example.com`). Returns null when nothing matches.
    _findHostByNodeName(nodeName) {
      if (!nodeName || !Array.isArray(this.hosts)) {
        return null;
      }
      const needle = String(nodeName).trim().toLowerCase();
      if (!needle) {
        return null;
      }
      const exactId = this.hosts.find(h => h && (h.id || '').toLowerCase() === needle);
      if (exactId) {
        return exactId;
      }
      const exactHost = this.hosts.find(h => h && (h.host || '').toLowerCase() === needle);
      if (exactHost) {
        return exactHost;
      }
      // Prefix match — the node hostname's first label often equals
      // the curated id's first label. Both sides split on '.' and
      // compared so `host01` ↔ `host01.example.com`.
      const stem = needle.split('.')[0];
      const stemMatch = this.hosts.find(h => {
        const hid = (h && h.id || '').toLowerCase().split('.')[0];
        const hh = (h && h.host || '').toLowerCase().split('.')[0];
        return stem && (hid === stem || hh === stem);
      });
      return stemMatch || null;
    },
    uptimeFor(item) {
      // Services: Swarm reports ISO-8601 `updated` — last spec change, a good
      // proxy for "running since". Containers: Unix seconds `created`.
      if (!item) {
        return null;
      }
      const raw = item.type === 'service' ? item.updated : item.created;
      if (raw == null || raw === '') {
        return null;
      }
      const ms = typeof raw === 'number' ? raw * 1000 : Date.parse(raw);
      return isNaN(ms) ? null : ms;
    },
    itemSubline(item) {
      // Node hostname is rendered by the topology chip strip below,
      // not here — avoids duplicating the information in two places.
      const bits = [];
      if (item.type) {
        bits.push(item.type);
      }
      if (item.stack) {
        bits.push(item.stack);
      }
      if (item.state && item.state !== 'running') {
        bits.push(item.state);
      }
      return bits.join(' · ');
    },
    canUpdate(item) {
      if (!item) {
        return false;
      }
      if (item.type === 'orphan') {
        return false;
      }
      if (item.stack_id) {
        return true;
      }
      if (item.type === 'container') {
        return true;
      }
      return false;
    },
    actionLabel(item) {
      if (item.status !== 'update') {
        return '—';
      }
      if (item.stack_id) {
        return this.t('actions.update_stack');
      }
      if (item.type === 'container') {
        return this.t('actions.recreate');
      }
      return this.t('actions.no_stack');
    },
    isSelectable(item) {
      // Selectable if updatable, restartable (service/container), or removable.
      if (item.status === 'update' && this.canUpdate(item)) {
        return true;
      }
      if (item.removable) {
        return true;
      }
      if (item.type === 'service' || item.type === 'container') {
        return true;
      }
      return false;
    },
    isRestartable(item) {
      return item && (item.type === 'service' || item.type === 'container');
    },
    sortBy(field) {
      if (this.sortField === field) {
        this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        this.sortField = field;
        this.sortDir = 'asc';
      }
    },
    sortIndicator(field) {
      if (this.sortField !== field) {
        return '';
      }
      return this.sortDir === 'asc' ? '▲' : '▼';
    },
    // WAI-ARIA `aria-sort` value resolver. Returns 'ascending' /
    // 'descending' for the active sort column, 'none' otherwise. Bind
    // on the `<th>` (NOT the inner button) so screen-reader users hear
    // sort direction alongside the column name. Generic over any
    // (currentField, currentDir) pair so AI / Stats / fleet sortable
    // tables can all reuse it without duplicating the ternary.
    _sortAria(field, currentField, currentDir) {
      if (currentField !== field) {
        return 'none';
      }
      return currentDir === 'asc' ? 'ascending' : 'descending';
    },
    toggleStack(name) {
      if (this.expanded.includes(name)) {
        this.expanded = this.expanded.filter(n => n !== name);
      } else {
        this.expanded = [...this.expanded, name];
      }
    },
    expandAllStacks() {
      this.expanded = this.filteredStacks.map(s => s.name);
    },
    collapseAllStacks() {
      this.expanded = [];
    },
    toggleSelectAll() {
      const selectable = this.filteredItems.filter(i => this.isSelectable(i));
      if (this.selected.length === selectable.length) {
        this.selected = [];
      } else {
        this.selected = selectable.map(i => i.id);
      }
    },
    selectAllVisible() {
      this.selected = this.filteredItems.filter(i => this.isSelectable(i)).map(i => i.id);
    },
    selectUpdatesOnly() {
      this.selected = this.filteredItems
        .filter(i => this.isSelectable(i) && i.status === 'update' && this.canUpdate(i))
        .map(i => i.id);
    },
    clearSelection() {
      this.selected = [];
    },
    clearFilters() {
      this.search = '';
      this.statusFilter = '';
      this.healthFilter = '';
    },
    topologyGroups(item) {
      // Returns [{node, chips: [{state, err}, …]}, …] for rendering the
      // node + coloured-dot strip. Placements with a synthetic fallback
      // node ("local" / "?") are dropped — the strip would just show
      // noise for single-node setups where no real hostname was
      // resolved. Empty result => caller hides the strip.
      if (!item || !Array.isArray(item.placements) || !item.placements.length) {
        return [];
      }
      const by = new Map();
      for (const p of item.placements) {
        const node = p.node || '?';
        if (node === 'local' || node === '?') {
          continue;
        }
        if (!by.has(node)) {
          by.set(node, []);
        }
        by.get(node).push(p);
      }
      return Array.from(by.entries()).map(([node, chips]) => ({node, chips}));
    },
    // i18n-aware topology pill tooltips. Pre-fix
    // both the Stacks and Services views inlined `:title="group.node
    // + ' — ' + group.chips.length + ' replica' + (count===1 ? '' :
    // 's')"` — the JS template-literal i18n leak that travels with
    // helper-reuse (the Services view's copy duplicated the
    // pre-existing Stacks tooltip). Plural concatenation never
    // translates cleanly. Singular / plural pick at call-time so
    // languages with non-binary plural rules can extend via locale-
    // override.
    topologyNodeTooltip(group) {
      const count = (group && group.chips && group.chips.length) || 0;
      const node = (group && group.node) || '';
      // Reuses existing topology.node_title / node_title_many keys
      // (added pre-fix for a different consumer; the i18n bundle
      // already covers the singular/plural split). Pluralization
      // picks at call-time so non-binary plural locales can extend
      // their bundle without touching JS.
      const key = count === 1 ? 'topology.node_title' : 'topology.node_title_many';
      return this.t(key, {node, count});
    },
    topologyChipTooltip(chip) {
      const state = (chip && chip.state) || 'unknown';
      const err = chip && chip.err ? String(chip.err) : '';
      // i18n-translated state label (chip.state is one of the Swarm
      // task-state strings — translate via a known-state key family;
      // unknown values fall through to the raw enum). When an error
      // string is present, append it inline.
      const stateKey = `topology.state_${state.replace(/-/g, '_')}`;
      let stateLabel = this.t(stateKey);
      if (stateLabel === stateKey) {
        stateLabel = state;
      }
      return err
        ? this.t('topology.chip_tooltip_with_error', {state: stateLabel, err})
        : stateLabel;
    },

    // ---- Command Palette (Cmd-K / Ctrl-K) ---------------------------
    // Universal "go anywhere" search dropping the operator into any
    // drawer / view / setting. Pure-client, fuzzy-matched against the
    // existing data shapes (this.hosts / this.items / this.stacks)
    // plus a static admin-route map and the hotkeys list. Activation
    // dispatches by the result's `kind`: host → openHostDrawer, item
    // → openItemDrawer, admin → setAdminTab + view='admin', view →
    // setView, hotkey → no-op (info only). Results are scored:
    // exact-id match wins, prefix beats substring, and the displayed
    // label gets a small bonus over secondary fields.
    openCommandPalette() {
      // Defensive: each step wrapped so a failure in $nextTick /
      // input.focus() can't prevent the state flip from landing.
      // The capture-phase keydown handler relied on this method;
      // an unhandled throw inside `this.$nextTick` (e.g. when the
      // method is invoked from a non-Alpine context where $nextTick
      // isn't defined) used to swallow the entire palette open.
      try {
        this.commandPaletteOpen = true;
      } catch (e) {
        console.error('[cmdpal] open: state flip failed', e);
      }
      try {
        this.commandPaletteQuery = '';
        this.commandPaletteSelectedIdx = 0;
      } catch (_) {
      }
      // Bulk-mode exclusion set is per-session; clear it on every
      // open so a previous run's deselections don't bleed into the
      // next bulk action.
      try {
        this.commandPaletteBulkExcluded = new Set();
      } catch (_) {
      }
      // Focus the input on the next tick (after Alpine renders the
      // x-show / :style branch). Without rAF the input isn't in the
      // DOM yet and the focus call no-ops.
      try {
        const tick = (this && typeof this.$nextTick === 'function')
          ? this.$nextTick.bind(this)
          : (fn) => requestAnimationFrame(fn);
        tick(() => {
          try {
            const input = document.getElementById('cmdpal-input');
            if (input) {
              input.focus();
            }
          } catch (_) {
          }
        });
      } catch (_) {
      }
    },
    closeCommandPalette() {
      this.commandPaletteOpen = false;
      this.commandPaletteQuery = '';
      this.commandPaletteSelectedIdx = 0;
      this.commandPaletteBulkExcluded = new Set();
    },
    // Static admin-route map. Each entry navigates to the matching
    // Admin → <tab> view via setAdminTab. Adding a new admin tab here
    // surfaces it in the palette without touching anywhere else.
    _commandAdminRoutes() {
      // Auto-derive the admin-tab list from `this.adminSections` —
      // the canonical source of truth that drives the Admin → sub-nav.
      // Pre-fix the tab list was hardcoded here as a separate literal,
      // so adding a new admin tab needed two coordinated edits AND the
      // IDs drifted (the prior literal had stale `auth` / `tuning` /
      // `asset` aliases vs the current `authentication` / `config` /
      // `assets` IDs in `adminSections`). The i18n bundle keeps the
      // legacy aliases pointing at the same strings as the new IDs so
      // operators on older locale files still see correct labels
      // during the upgrade window. Adding a new admin tab now means
      // adding ONE entry to `adminSections` plus ONE key to
      // `i18n/en.json` under `command_palette.admin.<id>`.
      const sections = Array.isArray(this.adminSections) ? this.adminSections : [];
      return sections
        // Sidebar separators (`{separator: true}`) are visual-only
        // dividers in the Admin nav — they have no tab to navigate to,
        // so skip them here or the command palette would surface an
        // empty-label phantom entry per separator.
        .filter(s => !s.separator)
        .map(s => ({
          tab: s.id,
          // Translate via `t()`; if the locale doesn't have the key, fall
          // back to the section's static `label` (which the admin sub-nav
          // already renders elsewhere).
          label: (this.t('command_palette.admin.' + s.id) || s.label || s.id),
        }));
    },
    _commandTopViews() {
      const views = ['stacks', 'services', 'nodes', 'hosts', 'history'];
      return views.map(view => ({
        view,
        label: this.t('command_palette.view.' + view),
      }));
    },
    // Score a candidate label against the lowercase query. Returns
    // 0 for no match, 1..100 for varying match quality. Exact = 100,
    // prefix = 80, word-prefix = 60, substring = 40. Empty query
    // matches everything at score 1 (so all groups render in default
    // alphabetical order until the operator types).
    _commandScoreLabel(label, q) {
      if (!q) {
        return 1;
      }
      const lc = String(label || '').toLowerCase();
      if (!lc) {
        return 0;
      }
      // Multi-word query: every token must match the label
      // somewhere; the result is the MIN of per-token scores so a
      // strong match on one token doesn't drown a weak / missing
      // match on another. Operator-reported on the Cisco SG300
      // switch where the asset name is "Cisco SG300-52MP 52-Port
      // Gigabit PoE Managed Switch" — typing "cisco switch"
      // previously failed because the literal substring "cisco
      // switch" doesn't appear (the words are at opposite ends of
      // the label). Tokenizing splits the query, scores each
      // token's best match against the label independently, and
      // returns the worst of those — so any token that's
      // genuinely absent from the label drops the score to 0.
      const qTokens = q.split(/\s+/).filter(Boolean);
      if (qTokens.length > 1) {
        let minScore = Infinity;
        for (const t of qTokens) {
          const s = this._scoreSingleToken(lc, t);
          if (s === 0) {
            return 0;
          }
          if (s < minScore) {
            minScore = s;
          }
        }
        return minScore === Infinity ? 0 : minScore;
      }
      return this._scoreSingleToken(lc, q);
    },
    // Per-token scoring — same rules the original
    // `_commandScoreLabel` applied to the whole query before the
    // multi-word tokenization. Exact match 100, prefix 80, word-
    // prefix 60, substring 40, miss 0.
    _scoreSingleToken(lc, q) {
      if (lc === q) {
        return 100;
      }
      if (lc.startsWith(q)) {
        return 80;
      }
      const tokens = lc.split(/[\s_\-./:]+/);
      for (const t of tokens) {
        if (t === q) {
          return 90;
        }
        if (t.startsWith(q)) {
          return 60;
        }
      }
      if (lc.includes(q)) {
        return 40;
      }
      return 0;
    },
    // Multi-field scorer — picks the BEST score across N candidate
    // strings. Lets a host's id match more strongly than its asset
    // type without each contributing separately.
    _commandScoreFields(q, ...fields) {
      let best = 0;
      for (const f of fields) {
        const s = this._commandScoreLabel(f, q);
        if (s > best) {
          best = s;
        }
      }
      return best;
    },
    // ---------------------------------------------------------------
    // BULK PALETTE MODE — Phase 1.
    //
    // Entry: query starts with `<verb>:` where verb is one of the
    //        canonical bulk verbs (`pause` / `resume`). Everything
    //        after the colon is the SELECTOR DSL — a list of
    //        whitespace-separated tokens, ANDed together. Each token
    //        is one of:
    //          - wildcard pattern  : matches host.id OR host.label
    //                                (case-insensitive; `*` is the
    //                                only wildcard glyph supported in
    //                                phase 1; `*nas` / `nas*` / `*nas*`
    //                                / bare `nas` all do substring
    //                                contains).
    //          - `provider:<name>` : host.providers includes <name>
    //          - `status:<value>`  : host.status === <value>
    //          - `paused`          : host.sampling_paused is true
    //                                (useful for `resume: paused`).
    //
    // The chip strip + run-row UI is driven entirely by the parsed
    // selector + this.hosts; no persistent server state until the
    // operator confirms the run.
    //
    // Phase 2+ (planned): add an AI-translated path that takes a
    // natural-language phrase and returns the same selector shape
    // (so the AI never directly invokes destructive ops, just
    // proposes a filter the operator confirms).
    // ---------------------------------------------------------------
    _BULK_VERBS: ['pause', 'resume'],
    isCommandPaletteBulkMode() {
      return this.commandPaletteBulkState() !== null;
    },
    toggleCommandPaletteBulkChip(hostId) {
      // Toggle a host's exclusion. Reactivity: re-create the Set so
      // Alpine sees the change (Set mutation in place doesn't trip
      // the reactive proxy).
      const next = new Set(this.commandPaletteBulkExcluded || []);
      if (next.has(hostId)) {
        next.delete(hostId);
      } else {
        next.add(hostId);
      }
      this.commandPaletteBulkExcluded = next;
    },
    async runCommandPaletteBulk() {
      const state = this.commandPaletteBulkState();
      if (!state) {
        return;
      }
      const ids = state.selected.map(h => h.id || h.host).filter(Boolean);
      if (!ids.length) {
        return;
      }
      const verb = state.verb;
      // SweetAlert confirm — same shape as `bulkPauseHosts` so the
      // operator gets a consistent two-stage gate (chip-strip preview
      // + final destructive confirm). Body shows up to 10 host names.
      const swal = (window.Swal || (typeof Swal !== 'undefined' && Swal));
      if (!swal) {
        if (typeof this.showToast === 'function') {
          this.showToast('SweetAlert unavailable', 'error');
        }
        return;
      }
      const sample = ids.slice(0, 10);
      const more = ids.length - sample.length;
      const sampleHtml = sample.map(id => '<code>' + this._logEscape(id) + '</code>').join(', ');
      const moreHtml = more > 0
        ? ' ' + (this.t('hosts_extra.bulk.pause_confirm_more', {more}) || ('… and ' + more + ' more'))
        : '';
      const titleKey = 'command_palette.bulk.confirm_title_' + verb;
      const bodyKey = 'command_palette.bulk.confirm_body_' + verb;
      const okKey = 'command_palette.bulk.confirm_ok_' + verb;
      const fallbackTitle = (verb === 'pause' ? 'Pause sampling on selected hosts?' : 'Resume sampling on selected hosts?');
      const fallbackBody = (verb === 'pause' ? 'Pause sampling on ' : 'Resume sampling on ') + ids.length + ' host(s)?';
      const fallbackOk = (verb === 'pause' ? 'Pause' : 'Resume');
      try {
        const res = await swal.fire({
          title: this.t(titleKey) || fallbackTitle,
          html: (this.t(bodyKey, {count: ids.length}) || fallbackBody)
            + '<br><br><div class="text-[11.5px] text-[var(--text-dim)] mono break-words">' + sampleHtml + moreHtml + '</div>',
          icon: 'warning',
          showCancelButton: true,
          confirmButtonText: this.t(okKey) || fallbackOk,
          cancelButtonText: this.t('actions.cancel') || 'Cancel',
        });
        if (!res.isConfirmed) {
          return;
        }
      } catch {
        return;
      }
      // Pause requires step-up reauth (matches the per-host bulk
      // pause contract); resume does not.
      let reauthToken = null;
      if (verb === 'pause') {
        reauthToken = await this._mintReauthToken();
        if (reauthToken === null) {
          return;
        }
      }
      try {
        const headers = {'Content-Type': 'application/json'};
        if (reauthToken) {
          headers['X-Reauth-Token'] = reauthToken;
        }
        const r = await fetch('/api/hosts/bulk/' + verb, {
          method: 'POST',
          headers,
          body: JSON.stringify({host_ids: ids}),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.showToast((data && data.detail) || ('HTTP ' + r.status), 'error');
          return;
        }
        const okKey2 = 'command_palette.bulk.success_' + verb;
        this.showToast(
          this.t(okKey2, {count: (data.applied || []).length || ids.length})
          || ((verb === 'pause' ? 'Paused' : 'Resumed') + ' ' + ((data.applied || []).length || ids.length) + ' host(s)'),
          'success',
        );
        // Clear the palette so a back-to-back bulk doesn't carry the
        // exclusion set across runs.
        this.closeCommandPalette && this.closeCommandPalette();
        if (typeof this.loadHosts === 'function') {
          this.loadHosts(true);
        }
      } catch (e) {
        this.showToast(String(e && e.message || e), 'error');
      }
    },
    // Inline confirmation handlers for destructive actions invoked
    // from the AI sidebar. The chat turn carries `pending_confirm:
    // true` + `pending_action: <descriptor>` until the operator
    // clicks one of the two buttons in the bubble.
    async confirmInlineAction(turnIdx) {
      const turn = this.aiConversation[turnIdx];
      if (!turn || !turn.pending_confirm || !turn.pending_action) {
        return;
      }
      const action = turn.pending_action;
      turn.pending_confirm = false;
      turn.pending_action = null;
      turn.action_ran = true;
      this.persistAiConversation();
      this._scrollAiSidebarToBottom();
      try {
        // `skipConfirm: true` propagates to the action's inner
        // implementation (bulkRemoveAll / bulkUpdateAll / etc.) so
        // the rich-data SweetAlert it would otherwise raise is
        // bypassed. The operator already approved inline; a second
        // popup would defeat the no-popup contract.
        // `tag` + `actionItem` are forwarded for parameterised
        // actions (currently retag_image only). Other actions
        // ignore them.
        await action.run({
          skipConfirm: true,
          tag: (turn.action_tag || '').toString(),
          actionItem: (turn.action_item || '').toString(),
          data: (turn.action_data && typeof turn.action_data === 'object') ? turn.action_data : null,
        });
      } catch (e) {
        if (typeof this.showToast === 'function') {
          this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
        }
      }
    },
    cancelInlineAction(turnIdx) {
      const turn = this.aiConversation[turnIdx];
      if (!turn) {
        return;
      }
      turn.pending_confirm = false;
      turn.pending_action = null;
      turn.cancelled = true;
      this.persistAiConversation();
    },

    // Tool-dispatch confirm handler. Distinct from confirmInlineAction
    // because we're not running a SPA-side action descriptor; we're
    // re-POSTing to /api/ai/palette with tool_confirm_granted=true so
    // the backend dispatcher actually fires the confirm-required tools
    // (ssh_diag / docker_container_du) and returns the second-round AI
    // reply composed from the tool output.
    async confirmInlineToolDispatch(turnIdx) {
      const turn = this.aiConversation[turnIdx];
      if (!turn || !turn.pending_tool_confirms || !turn.pending_query) {
        return;
      }
      const origQuery = turn.pending_query;
      // Clear the pending state immediately so the chip disappears and
      // double-click can't fire twice. We'll replace turn.text once
      // the second-round reply lands.
      turn.pending_tool_confirms = null;
      turn.pending_query = null;
      this.aiSidebarBusy = true;
      this.persistAiConversation();
      this._scrollAiSidebarToBottom();
      try {
        const ctx = this._buildAiPaletteContext();
        // Conversation history up to (but not including) THIS pending
        // turn — so the AI doesn't re-see its own first-round reply
        // and confuse "tool already pending" with "ask again".
        const priorTurns = this.aiConversation
          .slice(0, turnIdx)
          .filter(t => t && (t.role === 'user' || t.role === 'assistant') && t.text)
          .map(t => ({role: t.role, text: t.text}));
        const r = await fetch('/api/ai/palette', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            query: origQuery,
            context: ctx,
            conversation: priorTurns,
            tool_confirm_granted: true,
          }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.ok) {
          let detail = (j && j.detail) || (this.t('toasts.failed') || 'Failed');
          // Operator-helpful hint: when the AI provider timed out AND
          // the fallback chain wasn't engaged (either disabled OR no
          // viable secondary providers configured), tell the operator
          // where to fix it. Pre-fix the bare "Request timed out"
          // error left the operator wondering why they configured a
          // fallback at all.
          const isTimeout = typeof detail === 'string' && /timed out after/i.test(detail);
          const noFallback = j && j.fallback_used === false
            && Array.isArray(j.fallback_chain) && j.fallback_chain.length <= 1;
          if (isTimeout && noFallback) {
            detail = detail + ' ' + (this.t('toasts.ai_no_fallback_hint')
              || '(Tip: configure a fallback provider in Admin → AI Integration → Fallback chain so the next provider can pick up when the primary times out.)');
          }
          turn.error = detail;
        } else {
          // Replace the first-round prose with the second-round reply
          // composed from the tool results. Keep the same `ts` so the
          // bubble's DOM identity is stable.
          turn.text = (j.text || '').trim() || (this.t('command_palette.ai.empty_response') || '(empty response)');
          turn.provider = j.provider || turn.provider;
          turn.model = j.model || turn.model;
          turn.response_time_ms = (turn.response_time_ms || 0) + (j.response_time_ms || 0);
          turn.tokens = (turn.tokens || 0) + ((j.tokens && (j.tokens.prompt + j.tokens.completion)) || 0);
          turn.job_id = (j.job_id !== undefined && j.job_id !== null) ? j.job_id : turn.job_id;
          // tool_calls + tool_results are surfaced on the response for
          // operator-visible "what did we actually run?" affordance.
          if (Array.isArray(j.tool_calls)) {
            turn.tool_calls = j.tool_calls;
          }
          if (j.tool_results && typeof j.tool_results === 'object') {
            turn.tool_results = j.tool_results;
          }
          // Multi-round chain — the second-round reply itself may emit
          // NEW TOOL directives (common when the first round surfaced
          // a container ID and the AI now wants to drill into it).
          // Re-stamp the pending state from the new response so the
          // chip appears for the next round. In autonomous mode, the
          // post-push autonomous gate at the bottom of this function
          // will re-fire `confirmInlineToolDispatch` again, walking
          // the chain to completion. Hard-cap chain depth at
          // ~5 rounds via `turn.tool_chain_depth` so a buggy model
          // can't infinite-loop us through the dispatcher.
          const chainDepth = (turn.tool_chain_depth || 0) + 1;
          turn.tool_chain_depth = chainDepth;
          if (Array.isArray(j.pending_tool_confirms) && j.pending_tool_confirms.length
            && chainDepth < 5) {
            turn.pending_tool_confirms = j.pending_tool_confirms;
            turn.pending_query = origQuery;
            // Autonomous mode auto-chains the next round; approval
            // mode renders the chip again and waits for the operator.
            if (this.aiSidebarMode === 'autonomous') {
              this.$nextTick(() => {
                this.confirmInlineToolDispatch(turnIdx);
              });
            }
          }
        }
      } catch (e) {
        turn.error = (e && e.message) ? e.message : 'Tool dispatch failed';
      } finally {
        this.aiSidebarBusy = false;
        this._scrollAiSidebarToBottom();
        this.persistAiConversation();
      }
    },

    cancelInlineToolDispatch(turnIdx) {
      const turn = this.aiConversation[turnIdx];
      if (!turn) {
        return;
      }
      turn.pending_tool_confirms = null;
      turn.pending_query = null;
      turn.cancelled = true;
      this.persistAiConversation();
    },

    // Generic focus-trap keydown handler. Bind via
    // ``@keydown="open && _focusTrapKeydown($event, $el)"`` on the
    // dialog root. Intercepts Tab / Shift-Tab to cycle focus within
    // the dialog. Other keys pass through. Tab leak was a fleet-wide
    // gap (CLAUDE.md "Drawers + modals need ... focus-trap helper" —
    // documented since 2026-04-30, never built fleet-wide). This helper
    // is the building block; existing dialogs (host drawer, item drawer,
    // terminal modal, hotkeys modal, schedule edit modal) can adopt it
    // by adding the same `@keydown` binding to their root elements.
    _focusTrapKeydown(e, root) {
      if (!root || !e || e.key !== 'Tab') {
        return;
      }
      // Build the focusable-within-dialog set on every keydown rather
      // than caching — Alpine renders / removes nodes on state changes
      // (slash-picker, inline-confirm chip, feedback chips), so a
      // cached list goes stale. Cost is one querySelectorAll + a small
      // visibility filter on Tab — negligible.
      const candidates = root.querySelectorAll(
        'a[href], button:not([disabled]), textarea:not([disabled]), ' +
        'input:not([disabled]):not([type="hidden"]), select:not([disabled]), ' +
        '[tabindex]:not([tabindex="-1"])'
      );
      const focusables = [];
      for (const node of candidates) {
        if (node.offsetParent === null) {
          continue;
        } // hidden via display:none
        if (node.getAttribute('aria-hidden') === 'true') {
          continue;
        }
        focusables.push(node);
      }
      if (!focusables.length) {
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (e.shiftKey) {
        if (active === first || !root.contains(active)) {
          last.focus();
          e.preventDefault();
        }
      } else {
        if (active === last) {
          first.focus();
          e.preventDefault();
        }
      }
    },
    // Persist `aiRecentSlashActions` to /api/me/ui-prefs. Mirrors
    // `persistThemePref` / `persistAiConversation` — fire-and-forget,
    // localStorage doubles as the fast-path read since it's already
    // in `this.aiRecentSlashActions`.
    async _persistRecentSlashActions() {
      if (!this.me || !this.me.id || this.me.id < 0) {
        return;
      }
      try {
        await fetch('/api/me/ui-prefs', {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({prefs: {ai_recent_slash_actions: this.aiRecentSlashActions.slice(0, 5)}}),
        });
      } catch (e) {
        if (window.console && console.warn) {
          console.warn('[ai_sidebar] persist recents failed:', e);
        }
      }
    },

    // Push an action id onto the recents FIFO. Dedupes by removing
    // the id if already present, then unshifts to the head, then caps
    // at 5. Persists asynchronously.
    _recordSlashRecent(actionId) {
      if (!actionId || typeof actionId !== 'string') {
        return;
      }
      const existing = this.aiRecentSlashActions.indexOf(actionId);
      if (existing >= 0) {
        this.aiRecentSlashActions.splice(existing, 1);
      }
      this.aiRecentSlashActions.unshift(actionId);
      if (this.aiRecentSlashActions.length > 5) {
        this.aiRecentSlashActions.length = 5;
      }
      this._persistRecentSlashActions();
    },
    _appendActionChatTurn(action, slash) {
      // Push a synthetic assistant turn so the chat log shows what
      // the operator just invoked. For destructive actions we leave
      // `action_ran: false` initially — the inline confirmation chip
      // flips it to true on Yes click. Non-destructive auto-runs go
      // straight to true.
      const isDestructive = !!(action && action.destructive);
      this.aiConversation.push({
        role: 'assistant',
        text: '',
        action_id: action.id,
        action_label: action.label || action.id,
        action_ran: !isDestructive,
        slash: !!slash,
        ts: Date.now(),
      });
      this._scrollAiSidebarToBottom();
      this.persistAiConversation();
    },

    selectionUpdatable() {
      return this.items.filter(i => this.selected.includes(i.id) && i.status === 'update' && this.canUpdate(i));
    },
    selectionRemovable() {
      return this.items.filter(i => this.selected.includes(i.id) && i.removable);
    },
    removableAll() {
      // Everything currently removable, regardless of selection. Drives the
      // topbar "Cleanup N" fast-action button.
      return this.items.filter(i => i.removable);
    },
    updatableAll() {
      // Every item with an available update, regardless of selection. Drives
      // the AI palette `update_all_updatable` action.
      return this.items.filter(i => i.status === 'update' && this.canUpdate(i));
    },
    selectionRestartable() {
      return this.items.filter(i => this.selected.includes(i.id) && this.isRestartable(i));
    },
    selectionSummary() {
      const upd = this.selectionUpdatable().length;
      const rst = this.selectionRestartable().length;
      const rem = this.selectionRemovable().length;
      const parts = [];
      if (upd) {
        parts.push(this.t('bulk.summary_updatable', {count: upd}));
      }
      if (rst) {
        parts.push(this.t('bulk.summary_restartable', {count: rst}));
      }
      if (rem) {
        parts.push(this.t('bulk.summary_removable', {count: rem}));
      }
      return parts.length ? parts.join(' · ') : '';
    },
    openDrawer(item) {
      this.drawerItem = item;
    },
    // Body-scroll lock helper. Called from the Alpine root's
    // `x-effect` whenever any drawer-state changes — sets / clears a
    // `.drawer-scroll-lock` class on BOTH html and body so the scroll
    // viewport (which varies by browser — Chrome on html, Safari on
    // body) doesn't accept wheel input while a drawer is open. The
    // CSS rule pairs `overflow: hidden !important` on the class so it
    // wins against the existing `overflow-x: clip` rule. Three reactive
    // arguments (`drawerHost` / `drawerItem` / `drawerNode`) are
    // passed in so Alpine's effect tracker registers reads of all
    // three; without that an IIFE wrapping the same logic might
    // miss the dependency tracking.
    _applyDrawerScrollLock(host, item, node, sidebar, sidebarPinned) {
      // `sidebar` is `aiSidebarOpen` — added so opening the AI Assistant
      // drawer ALSO locks the body scroll. Pre-fix the AI sidebar slid
      // in over a still-scrollable page beneath; the cascade was scoped
      // to the three classic drawers only. Future drawer-style overlays
      // MUST extend this signature with another bound reactive flag —
      // do NOT compute `aiSidebarOpen` inside the function body, that
      // breaks Alpine's dependency tracking and the lock fires once
      // then goes silent.
      // `sidebarPinned` is `aiSidebarPinned` — when the AI sidebar is
      // pinned (split-pane mode), it's NOT a modal overlay anymore;
      // the operator wants to interact with the rest of the page
      // (scroll, click buttons, open drawers) WHILE the sidebar stays
      // docked. So `sidebar=true && sidebarPinned=true` does NOT
      // contribute to the lock — only the modal classic-drawer state
      // (host / item / node) plus an UNPINNED open sidebar count.
      const lock = !!(host || item || node || (sidebar && !sidebarPinned));
      const html = document.documentElement;
      const body = document.body;
      if (lock) {
        html.classList.add('drawer-scroll-lock');
        body.classList.add('drawer-scroll-lock');
      } else {
        html.classList.remove('drawer-scroll-lock');
        body.classList.remove('drawer-scroll-lock');
      }
    },

    // WAI-ARIA radiogroup keyboard pattern. Bind on the wrapper
    // element via `@keydown="_radiogroupArrowKey($event)"`. Implements
    // the canonical contract: ArrowLeft/Up → previous radio (with
    // wrap), ArrowRight/Down → next radio, Home → first, End → last.
    // RTL flips Left/Right semantics so the LOGICAL direction stays
    // intact (visual-left in RTL is "next"). Skips disabled radios.
    // Each move both focuses the next radio and triggers its click
    // handler so selection follows focus per the radiogroup pattern.
    // Roving tabindex (only the checked radio is tab-reachable) is
    // wired per-radio at the markup level via `:tabindex="..."`.
    _radiogroupArrowKey(ev) {
      const key = ev.key;
      if (key !== 'ArrowLeft' && key !== 'ArrowRight'
        && key !== 'ArrowUp' && key !== 'ArrowDown'
        && key !== 'Home' && key !== 'End') {
        return;
      }
      const group = ev.currentTarget;
      const radios = Array.from(group.querySelectorAll('[role="radio"]')).filter(r => !r.disabled);
      if (!radios.length) {
        return;
      }
      ev.preventDefault();
      let isRtl = false;
      try {
        isRtl = group.matches(':dir(rtl)');
      } catch (_e) {
        isRtl = (document.documentElement.dir === 'rtl' || document.body.dir === 'rtl');
      }
      let idx = radios.indexOf(document.activeElement);
      if (idx < 0) {
        idx = radios.findIndex(r => r.getAttribute('aria-checked') === 'true');
      }
      if (idx < 0) {
        idx = 0;
      }
      let next = idx;
      if (key === 'Home') {
        next = 0;
      } else {
        if (key === 'End') {
          next = radios.length - 1;
        } else {
          let dir = (key === 'ArrowRight' || key === 'ArrowDown') ? 1 : -1;
          if (isRtl && (key === 'ArrowLeft' || key === 'ArrowRight')) {
            dir = -dir;
          }
          next = (idx + dir + radios.length) % radios.length;
        }
      }
      const target = radios[next];
      target.focus();
      target.click();
    },

    // WAI-ARIA tab pattern for VERTICAL page-sidebar tablists (Settings
    // / Admin / Stats). Each sidebar carries `role="tablist"
    // aria-orientation="vertical"` + per-button `role="tab"` already;
    // this helper adds the missing arrow-key navigation. Bind on the
    // sidebar wrapper via `@keydown="_sidebarTablistArrowKey($event)"`.
    // Contract: ArrowUp / ArrowDown / Home / End move focus between
    // tabs (with wrap), focus follows by clicking the target so the
    // bound section model updates. Skips disabled tabs.
    _sidebarTablistArrowKey(ev) {
      const key = ev.key;
      if (key !== 'ArrowUp' && key !== 'ArrowDown'
        && key !== 'Home' && key !== 'End') {
        return;
      }
      const group = ev.currentTarget;
      const tabs = Array.from(group.querySelectorAll('[role="tab"]'))
        .filter(t => !t.disabled);
      if (!tabs.length) {
        return;
      }
      ev.preventDefault();
      let idx = tabs.indexOf(document.activeElement);
      if (idx < 0) {
        idx = tabs.findIndex(t => t.getAttribute('aria-selected') === 'true');
      }
      if (idx < 0) {
        idx = 0;
      }
      let next = idx;
      if (key === 'Home') {
        next = 0;
      } else {
        if (key === 'End') {
          next = tabs.length - 1;
        } else {
          const dir = (key === 'ArrowDown') ? 1 : -1;
          next = (idx + dir + tabs.length) % tabs.length;
        }
      }
      const target = tabs[next];
      target.focus();
      target.click();
    },

    // fired on `@focusout` of the host-card wrapper. Defers
    // the cn-driven sort/page-jump until the operator has fully left
    // the row card (not just blurred the cn input mid-edit).
    //
    // Without this, the previous behaviour was: type a new cn, tab to
    // the next field → the row immediately re-sorts into its numeric
    // position → on a paged editor the row may have moved to a
    // DIFFERENT page → operator has to find it again to keep editing
    // label / ne_url / beszel_name. Now: cn `@input` sets
    // `row._cnDirty = true`; the rebuild + page-jump happens here
    // ONLY when focus genuinely leaves the entire card.
    onHostCardFocusOut(idx, event, cardEl) {
      const newFocus = event && event.relatedTarget;
      // Focus moved within the same card → still editing → no-op.
      if (newFocus && cardEl && cardEl.contains(newFocus)) {
        return;
      }
      const row = (this.hostsConfig || [])[idx];
      if (!row || !row._cnDirty) {
        return;
      }
      row._cnDirty = false;
      const uid = row._uid;
      this.rebuildHostsConfigOrder();
      // Keep the row visible after the sort lands. Without this, a
      // cn that pushed the row to another page would silently move it
      // off-screen — exactly the bug we're fixing for the inline-cn
      // case, but for the post-edit case too.
      this.$nextTick(() => {
        const all = this.filteredHostsConfig();
        const pos = all.findIndex(({row: r}) => r._uid === uid);
        if (pos >= 0) {
          const per = this.hostsConfigPerPage || 50;
          const targetPage = Math.floor(pos / per) + 1;
          if (targetPage !== this.hostsConfigPage) {
            this.hostsConfigGoToPage(targetPage);
          }
        }
      });
    },

    // Count of discovered names not already present in hostsConfig —
    // drives the "Import N discovered" button label / visibility so
    // the operator doesn't import duplicates by accident.
    discoveredMissingCount() {
      const seen = new Set((this.hostsConfig || []).map(r =>
        (r.beszel_name || r.pulse_name || r.webmin_name || r.id || '').toLowerCase()
      ));
      let n = 0;
      for (const name of (this.hostsDiscovery.beszel || [])) {
        if (!seen.has(name.toLowerCase())) {
          n++;
        }
      }
      for (const name of (this.hostsDiscovery.pulse || [])) {
        if (!seen.has(name.toLowerCase())) {
          n++;
        }
      }
      for (const name of (this.hostsDiscovery.webmin || [])) {
        if (!seen.has(name.toLowerCase())) {
          n++;
        }
      }
      return n;
    },
    // Bulk-create host rows from every discovered name that isn't
    // already curated. Each new row uses the discovered name as both
    // the id/label and the matching provider's name field; the
    // operator tweaks from there. A name in BOTH providers creates
    // a single row with both fields filled.
    // Helper used by importDiscoveredHosts + anywhere else that
    // programmatically mutates hostsConfig.
    _markHostsDirty() {
      this.hostsConfigDirty = true;
    },
    importDiscoveredHosts() {
      const existing = new Set((this.hostsConfig || []).map(r =>
        (r.id || '').toLowerCase()
      ));
      const added = {};
      const addOrMerge = (name, field) => {
        const key = name.toLowerCase();
        if (existing.has(key)) {
          return;
        }
        if (!added[key]) {
          added[key] = {
            id: name,
            label: name,
            ne_url: '',
            beszel_name: '',
            pulse_name: '',
            webmin_name: '',
            webmin_url: '',
            snmp_name: '',
            // Init http_probe sub-dict so the per-host editor's
            // textarea x-model binding doesn't read `urls_text` off
            // undefined when this discovery-imported row is opened.
            http_probe: {},
            enabled: true,
          };
        }
        added[key][field] = name;
      };
      for (const n of (this.hostsDiscovery.beszel || [])) {
        addOrMerge(n, 'beszel_name');
      }
      for (const n of (this.hostsDiscovery.pulse || [])) {
        addOrMerge(n, 'pulse_name');
      }
      for (const n of (this.hostsDiscovery.webmin || [])) {
        addOrMerge(n, 'webmin_name');
      }
      for (const n of (this.hostsDiscovery.snmp || [])) {
        addOrMerge(n, 'snmp_name');
      }
      const rows = Object.values(added);
      if (!rows.length) {
        this.showToast(this.t('admin_hosts.import.nothing_new'), 'success');
        return;
      }
      this.hostsConfig.push(...rows);
      this.hostsConfigDirty = true;
      this.showToast(this.t('admin_hosts.added_n', {count: rows.length}), 'success');
    },
    // True when at least one provider (Beszel / Pulse / node-exporter
    // / Webmin / Ping / SNMP) is mapped on this row. The "Test providers"
    // button disables when none are set — there's nothing to probe and
    // the backend would return all-skipped anyway. SNMP + Ping added
    // : pre-fix an SNMP-only or Ping-only row showed the button
    // greyed out even with the row's snmp_name set or ping.enabled=true,
    // so the operator had no way to test those providers from the
    // Admin → Hosts editor.
    rowHasProviderMapping(row) {
      if (!row) {
        return false;
      }
      // SNMP gating mirrors ping's explicit opt-in: probe only when
      // `snmp.enabled === true`. The probe target falls through the
      // canonical resolver chain (aliases → snmp_name → address →
      // SKIP), so the gate must accept EITHER an explicit `snmp_name`
      // OR the shared `address` field. Pre-fix only `snmp_name` was
      // checked, so a host with `snmp.enabled=true` + `address`
      // populated + `snmp_name` blank reported "no provider mapping"
      // and the Test Providers button stayed disabled even though
      // the live sampler would have probed it correctly via the
      // address fallback.
      const snmpActive = !!(row.snmp && row.snmp.enabled === true)
        && !!((row.snmp_name || '').trim() || (row.address || '').trim());
      // HTTP probe gating mirrors ping/snmp's explicit opt-in. Either
      // the operator-set per-row `http_probe.urls` list OR the
      // fallback chain (top-level `url` + `services[].url`) gives the
      // probe a target. Without this branch, a host with ONLY
      // http_probe configured reported "no provider mapping" and the
      // Test button stayed disabled.
      const httpProbeActive = !!(row.http_probe && row.http_probe.enabled === true)
        && (
          (Array.isArray(row.http_probe.urls) && row.http_probe.urls.some(u => (u || '').trim()))
          || (row.url || '').trim()
          || (Array.isArray(row.services) && row.services.some(s => (s && s.url || '').trim()))
        );
      return !!(
        (row.beszel_name || '').trim() ||
        (row.pulse_name || '').trim() ||
        (row.ne_url || '').trim() ||
        (row.webmin_name || '').trim() ||
        (row.webmin_url || '').trim() ||
        snmpActive ||
        (row.ping && row.ping.enabled) ||
        httpProbeActive
      );
    },
    // Collapse / expand helpers for the Admin → Hosts editor. With SSH
    // + provider + icon + URL fields on every row, a 20-host list is
    // a 2000-line scroll. Collapsed rows show only the summary; the
    // field grid renders behind x-show when expanded.
    //
    // Keyed on the row's stable `_uid` (assigned on load / add) — NOT
    // on `row.id`. Earlier the lookup used `row.id` as the key, so
    // typing into the ID field changed the key mid-keystroke and the
    // row collapsed (`hostsConfigExpanded[oldId]` was true but
    // `hostsConfigExpanded[newId]` was undefined). Operators reported
    // "typing in ID closes the host" — this is that bug. _uid never
    // changes for the lifetime of the row, so the expansion state
    // sticks regardless of edits to any field.
    isHostConfigExpanded(row) {
      // Backwards-compatible: callers may pass either a row OR a
      // bare id (legacy paths). When given a string, fall back to
      // looking it up via id then resolve to _uid.
      if (typeof row === 'string') {
        if (!row) {
          return true;
        }
        const found = (this.hostsConfig || []).find(r => r && r.id === row);
        return found ? !!this.hostsConfigExpanded[found._uid] : false;
      }
      if (!row) {
        return false;
      }
      // Empty-id rows (fresh adds) always expand so the operator
      // sees the form fields without hunting for a chevron.
      if (!row.id) {
        return true;
      }
      return !!this.hostsConfigExpanded[row._uid];
    },
    toggleHostConfigRow(row) {
      if (typeof row === 'string') {
        row = (this.hostsConfig || []).find(r => r && r.id === row);
      }
      if (!row || !row._uid) {
        return;
      }
      const next = {...this.hostsConfigExpanded};
      if (next[row._uid]) {
        delete next[row._uid];
      } else {
        next[row._uid] = true;
      }
      this.hostsConfigExpanded = next;
    },
    expandAllHostConfigRows() {
      const next = {};
      for (const h of (this.hostsConfig || [])) {
        if (h && h._uid) {
          next[h._uid] = true;
        }
      }
      this.hostsConfigExpanded = next;
    },
    collapseAllHostConfigRows() {
      // Wipe the whole map — every row that had an id drops back to
      // its summary line. Empty-id rows (fresh adds) stay visible
      // because isHostConfigExpanded(id) returns true for them by
      // design.
      this.hostsConfigExpanded = {};
    },
    // DISKS card — toggle the "show zero-usage mounts" state. Keyed
    // by host.host so each host's toggle is independent.
    toggleHostShowEmptyDisks(hostKey) {
      this.hostDisksShowEmpty = {
        ...this.hostDisksShowEmpty,
        [hostKey]: !this.hostDisksShowEmpty[hostKey],
      };
    },
    // Helper returning the mounts that SHOULD render for this host.
    // Thresholds: a mount is "active" when its usage % is >= 0.5. The
    // UI shows active mounts by default; zero-usage mounts collapse
    // behind a toggle so hosts with 20 ZFS datasetsdon't
    // blow out the drawer. When the operator has flipped the toggle
    // on, every mount is returned.
    visibleMounts(h) {
      const all = (h && h.mounts) || [];
      if (!all.length) {
        return [];
      }
      if (this.hostDisksShowEmpty[h.host]) {
        return all;
      }
      const active = all.filter(m => (+m.dp || 0) >= 0.5);
      // Always keep AT LEAST the root ("/" or "C:\\") so operators
      // see something even when every partition is near-empty.
      if (active.length === 0 && all[0]) {
        return [all[0]];
      }
      return active;
    },
    emptyMountCount(h) {
      const all = (h && h.mounts) || [];
      const active = all.filter(m => (+m.dp || 0) >= 0.5);
      return Math.max(0, all.length - active.length);
    },
    // Asset-inventory autofill — called from the host-row editor when
    // the operator clicks "Load from asset inventory". Looks up the
    // row's `custom_number` against the loaded asset cache (via the
    // shared `assetForHost` helper so backend-injected + client-cache
    // paths both work) and populates EMPTY fields on the row:
    // id (Docker hostname) ← first entry in asset.hostnames (or asset.name)
    // label              ← asset.name / vendor+model fallback
    // url                ← first port.service_name starting with http(s)
    // Never overwrites a value the operator already typed — blank
    // fields only. Toast reports what was filled (or why nothing was).
    // Returns an array of field names that were filled, for the UI.
    autofillHostRowFromAsset(idx) {
      const row = (this.hostsConfig || [])[idx];
      if (!row) {
        return [];
      }
      const asset = this.assetForHost({custom_number: row.custom_number});
      if (!asset) {
        this.showToast(this.t('admin_hosts.autofill.no_match', {n: row.custom_number}), 'warning');
        return [];
      }
      const filled = [];
      // Strip the FQDN's domain suffix when populating the host id.
      // The global `ssh_fqdn_suffix` setting (e.g. ".example.com") is
      // appended at SSH-resolve time, so storing the SHORT hostname
      // here keeps the global suffix authoritative — different deploys
      // can swap suffixes without re-typing every host. IPs and
      // hostnames-without-dots are returned unchanged.
      const _stripDomain = (raw) => {
        const v = String(raw || '').trim();
        if (!v) {
          return '';
        }
        // Bare hostname (no dot) — nothing to strip.
        if (v.indexOf('.') === -1) {
          return v;
        }
        // IPv4 — leave intact.
        if (/^\d+\.\d+\.\d+\.\d+$/.test(v)) {
          return v;
        }
        // IPv6 — leave intact (contains `:`, no other shape collides).
        if (v.indexOf(':') !== -1) {
          return v;
        }
        // FQDN — keep the leading label only.
        return v.split('.')[0];
      };
      // id / hostname — prefer the first FQDN in the asset's Hostname
      // CSV; fall back to asset.name (device label, often lowercase).
      // Strip the domain suffix so the global `ssh_fqdn_suffix`
      // setting remains the single source of truth for what gets
      // appended at resolution time.
      if (!(row.id || '').trim()) {
        const primary = (Array.isArray(asset.hostnames) && asset.hostnames[0])
          || asset.name || '';
        if (primary) {
          row.id = _stripDomain(primary);
          filled.push('id');
        }
      }
      // NOTE: `row.label` deliberately NOT auto-populated from the
      // asset record. Operator-flagged: pre-fix the label was filled
      // from `asset.name` / `vendor model` / `vendor` on import, but
      // operators want to fill it themselves so it captures their
      // own naming convention rather than whatever shape the upstream
      // asset DB happens to carry. Empty label falls through cleanly
      // — `hostDisplayName(h)` already prefers `id` when label is
      // blank, and `iconUrlFor()` walks id + label + provider names
      // for icon resolution. If you ever want bulk-prefill behaviour
      // back, add a separate "Auto-fill labels from asset" button
      // rather than re-enabling on import.
      // URL — first port whose service_name looks like an http(s) link.
      // Ports without a URL-shaped service_name are skipped.
      if (!(row.url || '').trim() && Array.isArray(asset.ports)) {
        for (const p of asset.ports) {
          const sn = (p && p.service_name) || '';
          if (/^https?:\/\//i.test(sn)) {
            row.url = sn;
            filled.push('url');
            break;
          }
        }
      }
      // IP — primary IP from the asset's interfaces. Helpful for
      // hosts where node-exporter scrape templates reference {ip}.
      if (!(row.ip || '').trim() && asset.primary_ip) {
        row.ip = asset.primary_ip;
        filled.push('ip');
      }
      if (!filled.length) {
        this.showToast(this.t('admin_hosts.autofill.nothing_to_fill'), 'info');
        return [];
      }
      this.markHostRowDirty(idx);
      // Keep the row visibly expanded after the fill — same sticky
      // rule used for typed input (see onHostRowEdit).
      if (row._uid) {
        this.hostsConfigExpanded = {
          ...this.hostsConfigExpanded,
          [row._uid]: true,
        };
      }
      this.showToast(this.t('admin_hosts.autofill.filled', {
        fields: filled.join(', '),
      }), 'success');
      return filled;
    },
    // Dirty-tracking: any input change flips the unsaved-changes
    // flag (so the Save button can flash the warning + beforeunload
    // guards kick in) AND clears any stale per-row Test result so
    // green ticks don't linger next to fields the operator is
    // currently fixing.
    // Mint a stable, collision-free per-row UID for the Admin → Hosts
    // editor. Used as the `<template x-for :key>` so Alpine never tears
    // down + re-mounts a row mid-edit (which would lose input focus +
    // collapse expanded sections). Prefer `crypto.randomUUID()` —
    // universally supported in modern browsers + cryptographically
    // strong, which silences the `js/insecure-randomness` CodeQL flag
    // even though the value is UI-only and not a security secret.
    // Fallback path covers the (vanishingly rare) case of a browser
    // without `crypto.randomUUID` — uses `crypto.getRandomValues` (a
    // cryptographically strong PRNG, the primitive that backs randomUUID
    // itself), available in every browser that ships modern JS. No
    // `Math.random` anywhere so CodeQL js/insecure-randomness has no
    // surface left to flag.
    _mintRowUid() {
      try {
        if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
          return 'r' + crypto.randomUUID();
        }
        if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
          const buf = new Uint8Array(8);
          crypto.getRandomValues(buf);
          let hex = '';
          for (let i = 0; i < buf.length; i++) {
            hex += buf[i].toString(16).padStart(2, '0');
          }
          return 'r' + hex;
        }
      } catch (_) { /* unreachable on any spec-compliant browser */
      }
      // Last-resort: a monotonic counter scoped to the page session.
      // Not random, but unique within one tab — good enough for an Alpine
      // x-for :key that only needs intra-render uniqueness.
      this._uidCounter = (this._uidCounter | 0) + 1;
      return 'r' + Date.now().toString(36) + '_' + this._uidCounter.toString(36);
    },
    markHostRowDirty(idx) {
      this.hostsConfigDirty = true;
      if (this.hostsTestResults && this.hostsTestResults[idx]) {
        delete this.hostsTestResults[idx];
      }
    },
    // Convenience auto-fill: when the operator first types the ID
    // and the Label is still blank, mirror the ID into Label so they
    // don't have to type it twice. Respects any Label they later
    // type — once it's populated, it stays.
    onHostRowEdit(idx, field, _value) {
      this.markHostRowDirty(idx);
      const row = this.hostsConfig[idx];
      if (!row) {
        return;
      }
      if (field === 'id') {
        // Operator-flagged: do NOT auto-fill `row.label` from the
        // typed id. Pre-fix the editor mirrored the id into the label
        // on the first keystroke so a blank label always defaulted to
        // the id value; operator wants to leave the label alone so it
        // reflects their own naming convention rather than the host
        // identifier. Empty label falls through cleanly — every
        // consumer (`hostDisplayName(h)` / `iconUrlFor()` / the
        // keyword-scan icon resolver) prefers `id` when label is
        // blank.
        // Promote the implicit "empty-id rows always render expanded"
        // rule to an EXPLICIT entry in hostsConfigExpanded the moment
        // any character lands in the ID field. Without this, typing
        // the very first character flips `!row.id` from true to false
        // and `isHostConfigExpanded(row)` falls through to the map —
        // which has no key for a fresh row — returning false and
        // collapsing the panel mid-keystroke. Sticky-expand on first
        // edit survives typing AND copy-paste.
        if (row._uid && !this.hostsConfigExpanded[row._uid]) {
          this.hostsConfigExpanded = {
            ...this.hostsConfigExpanded,
            [row._uid]: true,
          };
        }
      }
    },

    topLevelGroupNames(excludeIdx) {
      return (this.hostGroups || [])
        .map((g, i) => ({g, i}))
        .filter(({g, i}) => i !== excludeIdx
          && !g.parent_name
          && (g.name || '').trim())
        .map(({g}) => g.name);
    },

    // Total hosts in a top-level bucket = its own direct hosts +
    // every host in its sub-groups. Used for the heading count so
    // operators see the parent group's "true" reach (otherwise a
    // parent that has only sub-groups looks like 0 hosts).
    bucketTotalHosts(bucket) {
      const own = (bucket && bucket.hosts && bucket.hosts.length) || 0;
      const sub = ((bucket && bucket.children) || [])
        .reduce((acc, c) => acc + ((c && c.hosts && c.hosts.length) || 0), 0);
      return own + sub;
    },
    // Flat render-list for the Hosts view's host-row template.
    // Parent-direct hosts first, then each sub-group's hosts. The
    // host object is SPREAD into the entry (so `h.label`, `h.host`,
    // `h.providers` etc. all pass through unchanged) and two extra
    // markers are attached:
    // _sub_group   — the sub-group this row belongs to (null for
    //                parent-direct rows).
    // _sub_heading — true on the FIRST row of a sub-group; the
    //                template emits the heading once before that
    //                row, then false on subsequent rows.
    // Result: the SAME full host row markup (custom_number chip,
    // provider chips, asset location subline, drawer-expandable
    // content) renders for both parent and sub-group hosts. No
    // duplicated template — adding a feature in one place picks it
    // up everywhere.
    bucketRenderList(bucket) {
      const out = [];
      for (const h of (bucket.hosts || [])) {
        out.push(Object.assign({}, h, {_sub_group: null, _sub_heading: false}));
      }
      for (const sub of (bucket.children || [])) {
        const subHosts = sub.hosts || [];
        if (subHosts.length === 0) {
          // Empty sub-group. With "Hide hosts without agents" ON the
          // operator wants the noise reduced — skip the heading entirely
          // (the group has nothing to show). With the filter OFF, we
          // STILL skip the heading because the previous heading-only
          // marker created confusion (operator's preference iterated
          // multiple times — final landing: hide empties always; the
          // group definition is still visible in Admin → Host Groups).
          continue;
        }
        for (let i = 0; i < subHosts.length; i++) {
          out.push(Object.assign({}, subHosts[i], {
            _sub_group: sub.group,
            _sub_heading: i === 0,
          }));
        }
      }
      return out;
    },

    // Visual order for the groups editor — each top-level group
    // immediately followed by its sub-groups. Operator's `order`
    // field still drives top-level ordering; sub-group order within
    // a parent cluster is preserved by raw-array insertion order.
    // Keeps `origIdx` (the position in the raw `hostGroups` array)
    // so save-validation + per-row button handlers can reach back
    // to the storage without rebuilding the list.
    sortedGroupsForEditor() {
      const arr = (this.hostGroups || []).map((g, i) => ({g, origIdx: i}));
      // Pass 1: top-level rows in original order (preserving the
      // operator's move-up/move-down choices).
      const tops = arr.filter(e => !e.g.parent_name);
      // Pass 2: group sub-rows by their parent_name.
      const subs = new Map();
      for (const e of arr) {
        if (!e.g.parent_name) {
          continue;
        }
        const key = e.g.parent_name;
        if (!subs.has(key)) {
          subs.set(key, []);
        }
        subs.get(key).push(e);
      }
      // Weave: each top-level row followed by its children — but
      // skip the children when their parent is collapsed via the
      // editor-side toggle. Operator can still expand to edit them.
      //
      // `seen` MUST include kids whose parent exists even when we
      // skip rendering them (collapsed). Otherwise the orphan-catch
      // below re-adds every hidden child at the bottom of the list,
      // which is what produced the "hides first parent's kids only"
      // bug — collapsing parent B simply relocated B's kids to the
      // trailing orphan bucket instead of hiding them.
      const collapsed = new Set(this.hostGroupsEditorChildrenCollapsed || []);
      const out = [];
      const seen = new Set();
      for (const t of tops) {
        out.push(t);
        seen.add(t.origIdx);
        const kids = subs.get(t.g.name);
        if (!kids) {
          continue;
        }
        for (const k of kids) {
          seen.add(k.origIdx);
        }
        if (!collapsed.has(t.g.name)) {
          out.push(...kids);
        }
      }
      // True orphaned sub-groups (parent_name set but no matching
      // top-level group) still sink to the bottom so they stay
      // visible for repair rather than silently disappearing.
      // `saveHostGroups` rejects them with inline errors, but the
      // operator has to SEE them first.
      for (const e of arr) {
        if (!seen.has(e.origIdx)) {
          out.push(e);
        }
      }
      return out;
    },

    // Page slice of `sortedGroupsForEditor()` — keeps the same
    // `{g, origIdx, listIdx}` shape so the per-row buttons (move /
    // delete / sub-group add) reach back to the unsliced list with
    // their original indices intact. Page is clamped lazily so a
    // delete that drops the total below the current page falls back
    // to the new last page rather than rendering empty.
    pagedGroupsForEditor() {
      const all = this.sortedGroupsForEditor();
      const per = this.hostGroupsPerPage || 50;
      const totalPages = Math.max(1, Math.ceil(all.length / per));
      const page = Math.min(Math.max(1, this.hostGroupsPage), totalPages);
      const start = (page - 1) * per;
      // Re-emit as {g, origIdx, listIdx} so the template's existing
      // destructure pattern continues to work — listIdx points at the
      // ABSOLUTE position in the unpaged list, not the slice, so
      // move-up / move-down arithmetic stays correct.
      return all
        .slice(start, start + per)
        .map((entry, sliceIdx) => ({
          g: entry.g,
          origIdx: entry.origIdx,
          listIdx: start + sliceIdx,
        }));
    },
    // Bulk-collapse + scroll-to-top for the sticky action bar — the
    // groups editor has its own collapse state (per parent name)
    // separate from the Hosts editor; reuse the existing
    // collapseAllHostGroupChildren handler for the action bar's
    // "Collapse all" button.
    scrollToHostGroupsTop() {
      try {
        window.scrollTo({top: 0, behavior: 'smooth'});
      } catch {
        window.scrollTo(0, 0);
      }
    },

    // ---- Inline-field-error helpers ----
    // Keyed storage lives on `fieldErrors`. Callers set a specific
    // error text for a specific input (keyed by a stable "scope_idx_field"
    // id) and the templates render red-bordered inputs + an error
    // hint beneath. Clearing on @input means the red cue goes away
    // as soon as the operator starts fixing it — classic
    // jQuery-validate feel without the dependency.
    setFieldError(key, msg) {
      this.fieldErrors = {...this.fieldErrors, [key]: msg};
    },
    clearFieldError(key) {
      if (key in this.fieldErrors) {
        const next = {...this.fieldErrors};
        delete next[key];
        this.fieldErrors = next;
      }
    },
    clearFieldErrorsByPrefix(prefix) {
      const next = {};
      for (const k of Object.keys(this.fieldErrors || {})) {
        if (!k.startsWith(prefix)) {
          next[k] = this.fieldErrors[k];
        }
      }
      this.fieldErrors = next;
    },
    hasFieldError(key) {
      return !!(this.fieldErrors && this.fieldErrors[key]);
    },
    fieldError(key) {
      return (this.fieldErrors || {})[key] || '';
    },
    // Focus the DOM input whose x-model ends in the given field name
    // on the given row. Used after validation sets an error so the
    // operator's cursor lands on the first failing field.
    focusFirstFieldError() {
      const first = Object.keys(this.fieldErrors || {})[0];
      if (!first) {
        return;
      }
      // If the first error is keyed against a hostsConfig row that
      // lives on a different page,
      // navigate to that page BEFORE the DOM query — otherwise the
      // .field-invalid element doesn't exist and focus silently
      // no-ops, leaving the operator confused about why save failed.
      const m = first.match(/^host_(?<idx>\d+)_/);
      if (m) {
        const rowIdx = parseInt(m.groups.idx, 10);
        const all = this.filteredHostsConfig();
        const pos = all.findIndex(({idx}) => idx === rowIdx);
        if (pos >= 0) {
          const per = this.hostsConfigPerPage || 50;
          this.hostsConfigGoToPage(Math.floor(pos / per) + 1);
        } else if ((this.hostsConfigFilter || '').trim()) {
          // Field error lives on a row that's been filtered out — the
          // page-jump above silently fails and the operator sees a
          // generic "Save failed" toast with no actionable target. Show
          // a SweetAlert with a one-click "Clear filter" action so they
          // can reach the offending row.
          if (typeof Swal !== 'undefined') {
            Swal.fire({
              icon: 'warning',
              title: this.t('admin_hosts.errors.filtered_title'),
              text: this.t('admin_hosts.errors.filtered_body'),
              confirmButtonText: this.t('admin_hosts.errors.filtered_clear'),
              showCancelButton: true,
              cancelButtonText: this.t('actions.cancel'),
            }).then((result) => {
              if (result.isConfirmed) {
                this.hostsConfigFilter = '';
                this.$nextTick(() => this.focusFirstFieldError());
              }
            });
            return;
          }
          this.showToast(
            this.t('admin_hosts.errors.filtered_body'),
            'error',
          );
          return;
        }
      }
      // Best-effort DOM lookup — errors are keyed by a stable id and
      // the templates bind `:class="hasFieldError('...')"` on the
      // input. A short delay lets Alpine finish rendering the error
      // state before we try to scroll into view. Bumped from 30 → 80
      // ms because we may have just changed the page above and Alpine
      // needs an extra tick to mount the new slice.
      setTimeout(() => {
        const el = document.querySelector('.field-invalid');
        if (el && typeof el.focus === 'function') {
          el.focus();
          if (typeof el.scrollIntoView === 'function') {
            el.scrollIntoView({block: 'center', behavior: 'smooth'});
          }
        }
      }, 80);
    },

    // Hosts view — group-related helpers.
    isGroupCollapsed(name) {
      return (this.hostGroupsCollapsed || []).includes(name);
    },
    toggleGroup(name) {
      const set = new Set(this.hostGroupsCollapsed || []);
      if (set.has(name)) {
        set.delete(name);
      } else {
        set.add(name);
      }
      this.hostGroupsCollapsed = Array.from(set);
      try {
        localStorage.setItem(
          'hostGroupsCollapsed',
          JSON.stringify(this.hostGroupsCollapsed),
        );
      } catch {
      }
    },
    expandAllGroups() {
      this.hostGroupsCollapsed = [];
      try {
        localStorage.setItem('hostGroupsCollapsed', '[]');
      } catch {
      }
    },
    collapseAllGroups() {
      const names = (this.hostGroups || []).map(g => g.name).filter(Boolean);
      // Also collapse the "ungrouped" bucket so the operator can fully
      // minimise the whole view. "" is the stable key for that bucket.
      names.push('');
      this.hostGroupsCollapsed = names;
      try {
        localStorage.setItem(
          'hostGroupsCollapsed',
          JSON.stringify(this.hostGroupsCollapsed),
        );
      } catch {
      }
    },
    // Build the grouped view: iterate the (already filtered+sorted)
    // host list, bucket each into the first group whose range covers
    // its custom_number. Anything unmatched lands in the trailing
    // ungrouped bucket. Groups are rendered in `order` order; the
    // ungrouped bucket always comes last.
    // Build the nested {parent → children → hosts} structure the
    // Hosts view renders. 2-level nesting only: a group with
    // `parent_name` set is a SUB-GROUP of that named top-level
    // group. Most-specific-match-wins: a host whose custom_number
    // falls inside a sub-group's range lands under that sub-group,
    // NOT under the parent (even though the parent's range also
    // contains it). Hosts that match a top-level group directly
    // but no sub-group appear in the parent's own host list.
    //
    // Shape:
    // [
    //   { group: {...top-level...}, hosts: [h, h],
    //     children: [
    //       { group: {...sub-group...}, hosts: [h, h] },
    //     ],
    //   },
    //   { group: null, hosts: [h, h] }   // Ungrouped, trailing
    // ]
    // Memoised result. — `groupedHosts()` is called on
    // every Alpine re-render. With 500 hosts × 30 groups the inner
    // O(N×M) walk is 15k comparisons per tick. Cache keyed on the
    // identities of the source arrays + the host list's length and the
    // groups list's length + a counter that increments on group save
    // (`hostGroupsRevision`) so a save explicitly busts even when the
    // length is unchanged.
    _groupedHostsCache: {key: '', value: null},
    async clearAssetClientSecret() {
      try {
        const ok = await (window.Swal ? Swal.fire({
          icon: 'warning',
          title: this.t('admin_assets.clear_secret_title'),
          text: this.t('admin_assets.clear_secret_text'),
          showCancelButton: true,
          confirmButtonText: this.t('actions.confirm'),
          cancelButtonText: this.t('actions.cancel'),
        }).then(r => !!r.isConfirmed) : confirm(this.t('toasts_extra.asset_clear_secret_prompt')));
        if (!ok) {
          return;
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({clear_asset_inventory_client_secret: true}),
        });
        if (!r.ok) {
          throw new Error(`HTTP ${r.status}`);
        }
        await this.loadSettings();
        this.showToast(this.t('admin_assets.secret_cleared'), 'success');
      } catch (e) {
        this.showToast(this.t('admin_assets.save_failed') + ': ' + e.message, 'error');
      }
    },
    async clearAssetLifetimeToken() {
      try {
        const ok = await (window.Swal ? Swal.fire({
          icon: 'warning',
          title: this.t('admin_assets.clear_lifetime_token_title'),
          text: this.t('admin_assets.clear_lifetime_token_text'),
          showCancelButton: true,
          confirmButtonText: this.t('actions.confirm'),
          cancelButtonText: this.t('actions.cancel'),
        }).then(r => !!r.isConfirmed) : confirm(this.t('toasts_extra.asset_clear_token_prompt')));
        if (!ok) {
          return;
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({clear_asset_inventory_lifetime_token: true}),
        });
        if (!r.ok) {
          throw new Error(`HTTP ${r.status}`);
        }
        await this.loadSettings();
        this.showToast(this.t('admin_assets.lifetime_token_cleared'), 'success');
      } catch (e) {
        this.showToast(this.t('admin_assets.save_failed') + ': ' + e.message, 'error');
      }
    },
    // Generic secret-clear helper — the seven admin-tab secret inputs
    // (asset client_secret / asset lifetime_token / ssh password / ssh
    // passphrase / ssh private_key / beszel_password / pulse_token /
    // webmin_password / portainer_api_key / oidc_client_secret) all
    // share the same SweetAlert-confirm + POST-clear-flag shape.
    // Pre-fix each had its own `clearXxx` function with copy-pasted
    // body. This canonical helper takes the i18n key family +
    // backend-flag name + post-clear toast key and runs the flow.
    // Kept the existing `clearAssetClientSecret` / etc. wrappers so
    // their callers stay one-line.
    async _clearSecret({flag, titleKey, textKey, toastKey}) {
      try {
        const ok = await (window.Swal ? Swal.fire({
          icon: 'warning',
          title: this.t(titleKey),
          text: this.t(textKey),
          showCancelButton: true,
          confirmButtonText: this.t('actions.confirm'),
          cancelButtonText: this.t('actions.cancel'),
        }).then(r => !!r.isConfirmed) : confirm(this.t(textKey)));
        if (!ok) {
          return;
        }
        const body = {};
        body[flag] = true;
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          throw new Error(`HTTP ${r.status}`);
        }
        await this.loadSettings();
        this.showToast(this.t(toastKey), 'success');
      } catch (e) {
        this.showToast((this.t('toasts.save_failed') || 'Save failed') + ': ' + e.message, 'error');
      }
    },
    async clearBeszelPassword() {
      return this._clearSecret({
        flag: 'clear_beszel_password',
        titleKey: 'settings.host_stats.clear_secret_title',
        textKey: 'settings.host_stats.beszel_password_clear_text',
        toastKey: 'settings.host_stats.beszel_password_cleared',
      });
    },
    async clearPulseToken() {
      return this._clearSecret({
        flag: 'clear_pulse_token',
        titleKey: 'settings.host_stats.clear_secret_title',
        textKey: 'settings.host_stats.pulse_token_clear_text',
        toastKey: 'settings.host_stats.pulse_token_cleared',
      });
    },
    async clearWebminPassword() {
      return this._clearSecret({
        flag: 'clear_webmin_password',
        titleKey: 'settings.host_stats.clear_secret_title',
        textKey: 'settings.host_stats.webmin_password_clear_text',
        toastKey: 'settings.host_stats.webmin_password_cleared',
      });
    },
    /** Walk the asset cache for cert children of one host asset.
     *
     * Cert rows are identified by Type.ShortName (case-insensitive) in
     * the set {CERT, TLS, SSL} OR Type.Name matching /cert|ssl|tls/i,
     * AND a parent reference matching the host asset's ID. The parent
     * field's casing varies upstream; we walk every plausible key. The
     * returned shape is `{name, issuer, expires_at, asset_id, link_url}` —
     * `link_url` falls back to the cert asset's own ID-as-URL so the
     * drawer can always link somewhere useful even when upstream
     * doesn't surface a dedicated URL field.
     */
    _certsForAsset(hostAsset, allAssets) {
      if (!hostAsset || !Array.isArray(allAssets) || !allAssets.length) {
        return [];
      }
      const hostId = hostAsset.ID ?? hostAsset.id ?? null;
      if (hostId == null) {
        return [];
      }
      const out = [];
      const isCertType = (t) => {
        if (!t) {
          return false;
        }
        if (typeof t === 'string') {
          return /cert|ssl|tls/i.test(t);
        }
        if (typeof t === 'object') {
          const short = String(t.ShortName || t.shortname || t.short || t.Code || '').trim().toUpperCase();
          if (short === 'CERT' || short === 'TLS' || short === 'SSL') {
            return true;
          }
          const name = String(t.Name || t.name || t.CalculatedName || '').trim();
          return /cert|ssl|tls/i.test(name);
        }
        return false;
      };
      const matchParent = (row) => {
        // Try every plausible parent-reference field shape.
        const candidates = [
          row.ParentID, row.parent_id, row.ParentId,
          row.Parent, row.parent,
          row.ParentAsset, row.parent_asset,
        ];
        for (const v of candidates) {
          if (v == null) {
            continue;
          }
          // Direct scalar match (parent id literal).
          if (typeof v === 'number' || typeof v === 'string') {
            if (String(v) === String(hostId)) {
              return true;
            }
            continue;
          }
          // Nested object — pull the ID out of it.
          if (typeof v === 'object') {
            const inner = v.ID ?? v.id;
            if (inner != null && String(inner) === String(hostId)) {
              return true;
            }
          }
        }
        return false;
      };
      for (const row of allAssets) {
        if (!row || row === hostAsset) {
          continue;
        }
        if (!isCertType(row.Type || row.type)) {
          continue;
        }
        if (!matchParent(row)) {
          continue;
        }
        const certId = row.ID ?? row.id ?? null;
        const name = String(row.Name || row.name || row.CommonName || row.common_name || '').trim();
        const issuer = String(row.Issuer || row.issuer || row.IssuedBy || row.issued_by || '').trim();
        const expiresAt = String(row.ExpiresOn || row.expires_on || row.NotAfter || row.not_after || '').trim();
        // Link URL — prefer an explicit field, fall back to the asset
        // record's own URL alias. Drawer renders a chevron; without a
        // URL the link still surfaces (best-effort).
        const linkUrl = String(row.URL || row.Url || row.url || row.Link || row.link || '').trim();
        out.push({
          asset_id: certId,
          name: name || (certId != null ? String(certId) : ''),
          issuer,
          expires_at: expiresAt,
          link_url: linkUrl,
        });
      }
      return out;
    },
    // no `type_short`, log the available keys ONCE so the operator can
    // tell us the correct upstream field name. The set is process-wide
    // so we don't flood the console — first asset that misses logs.
    hostTypePrefix(h) {
      const a = this.assetForHost(h);
      if (!a) {
        return '';
      }
      // Diagnostic — fires once per asset id when type_short is empty
      // but type IS present, suggesting the upstream Type object uses
      // a field name we don't recognise yet.
      if (!a.type_short && a.type && a._raw && a._raw.Type
        && typeof a._raw.Type === 'object') {
        if (!this._loggedMissingTypeShort) {
          this._loggedMissingTypeShort = new Set();
        }
        const aid = String(a.id || a.type || '');
        if (!this._loggedMissingTypeShort.has(aid)) {
          this._loggedMissingTypeShort.add(aid);

          console.info(
            '[asset] type has no recognised short-name field; available keys:',
            Object.keys(a._raw.Type || {}),
            '— type:', a.type,
            '— full Type object:', a._raw.Type,
            '— falling back to derived acronym.',
          );
        }
      }
      // Source of truth is the asset's `ShortName` (<asset-api-host> MDI
      // §5.2.4, exposed by `shape_asset` as `type_short`). When the
      // operator hasn't set a ShortName upstream, fall back to the
      // long Type.Name verbatim — never invent an abbreviation,
      // since the operator's asset record is authoritative.
      const label = (a.type_short || a.type || '').trim();
      return label ? '[' + label + '] ' : '';
    },
    // Raw asset row from the cached snapshot — for the debug panel.
    // The shaped resolver (`assetForHost`) drops fields the drawer
    // doesn't render; this returns the unfiltered upstream object.
    rawAssetForHost(h) {
      if (!h || h.custom_number == null || h.custom_number === '') {
        return null;
      }
      const assets = (this.assetCache && Array.isArray(this.assetCache.assets))
        ? this.assetCache.assets : null;
      if (!assets || !assets.length) {
        return null;
      }
      const n = parseInt(h.custom_number, 10);
      if (!Number.isFinite(n)) {
        return null;
      }
      for (const a of assets) {
        if (!a) {
          continue;
        }
        const cn = a.CustomNumber ?? a.custom_number ?? a.number ?? a.id;
        if (parseInt(cn, 10) === n) {
          return a;
        }
      }
      return null;
    },
    observeHostRow(el, id) {
      if (!el || !id) {
        return;
      }
      const obs = this._ensureHostRowObserver();
      if (!obs) {
        // Browser without IntersectionObserver — fall back to eager
        // fetch so functionality is preserved (some old WebViews lack
        // IO). This path is unreachable on every modern browser.
        if (!this._hostSeenIds.has(id)) {
          this._hostSeenIds.add(id);
          this.refreshHostRow(id).catch(() => {
          });
        }
        return;
      }
      obs.observe(el);
    },
    // concurrency-capped queue runner shared between the IO
    // observer's debounced flush and loadHosts' poll-driven fan-out.
    // Resolves PARALLEL the same way loadHosts does (per-call read of
    // `me.client_config.hosts_parallel_fetch`, fallback 6) so an
    // operator's Admin → Config Save takes effect on the next call.
    async _runHostRefreshQueue(ids) {
      // Push every requested id onto the SHARED queue and spawn
      // workers only up to the cap. Pre-fix this had its own private
      // queue + pool independent of the polling-path pool, so a
      // burst of `_runHostRefreshQueue(...)` during an active
      // `loadHosts(...)` doubled the in-flight count beyond the cap.
      // Same anti-pattern applies to direct `refreshHostRow` calls
      // from SSE event handlers — those should also push here via
      // `_hostObserverPending.add(id) + scheduleFlush()` so every
      // path shares one cap.
      for (const id of (ids || [])) {
        if (id) {
          this._enqueueHostRefresh(id);
        }
      }
      await this._ensureHostRefreshWorkers();
    },
    // Shared queue + worker count used by ALL host-refresh call sites
    // (lazy IO observer, SSE event handlers, polling fan-out). Capped
    // by `me.client_config.hosts_parallel_fetch` regardless of caller.
    _hostRefreshQueue: null,
    _hostRefreshWorkerCount: 0,
    _enqueueHostRefresh(id) {
      this._hostRefreshQueue = this._hostRefreshQueue || [];
      // Cheap dedupe: skip if already queued. Uses indexOf since the
      // queue is bounded by host count; for fleets > 200 hosts a
      // Set-backed mirror would be a future optimisation.
      if (this._hostRefreshQueue.indexOf(id) === -1) {
        this._hostRefreshQueue.push(id);
      }
    },
    async _ensureHostRefreshWorkers() {
      const PARALLEL = (this.me && this.me.client_config
        && this.me.client_config.hosts_parallel_fetch) || 6;
      const need = Math.max(0, PARALLEL - this._hostRefreshWorkerCount);
      if (!need) {
        return;
      }
      const queue = this._hostRefreshQueue || [];
      const slots = Math.min(need, queue.length);
      if (!slots) {
        return;
      }
      const worker = async () => {
        this._hostRefreshWorkerCount += 1;
        try {
          while (this._hostRefreshQueue && this._hostRefreshQueue.length) {
            const id = this._hostRefreshQueue.shift();
            if (!id) {
              break;
            }
            try {
              await this.refreshHostRow(id);
            } catch (_) { /* per-row failure stays isolated */
            }
          }
        } finally {
          this._hostRefreshWorkerCount -= 1;
        }
      };
      const workers = [];
      for (let i = 0; i < slots; i++) {
        workers.push(worker());
      }
      await Promise.all(workers);
    },

    // Translate an HTTP error response (status + body text) into a
    // friendly, operator-actionable message. Centralised so any future
    // long-running endpoint that can hit a reverse-proxy timeout
    // (504 from openresty / nginx / NPM) gets the same UX.
    //
    // Detection rules:
    // 1. Status 504 / 502 / 503 → reverse-proxy timeout/error. The
    //    backend may still be running; show an actionable hint.
    // 2. Body starts with `<` (HTML / XML) → upstream proxy page that
    //    has nothing operator-readable in it. Suppress entirely.
    // 3. Body looks like JSON `{"detail":"..."}` → extract the detail.
    // 4. Body is plain text shorter than 200 chars → pass through.
    // 5. Anything longer is suspicious; fall back to status-only.
    _friendlyHttpError(status, body, i18nNamespace) {
      const ns = i18nNamespace || 'common.errors';
      const sCode = String(status || 0);
      const trimmed = (body || '').trim();
      // 1. Reverse-proxy timeouts (504 / 502 / 503).
      if (status === 504) {
        return this.t(ns + '.gateway_timeout')
          || this.t('common.errors.gateway_timeout')
          || 'The reverse proxy timed out — the request may still be running on the backend. Wait ~30s and refresh; results will appear if the backend completed.';
      }
      if (status === 502) {
        return this.t(ns + '.bad_gateway')
          || this.t('common.errors.bad_gateway')
          || 'The reverse proxy returned a bad-gateway error — the backend may be restarting. Try again in a moment.';
      }
      if (status === 503) {
        return this.t(ns + '.service_unavailable')
          || this.t('common.errors.service_unavailable')
          || 'Service temporarily unavailable. Try again in a moment.';
      }
      // 2. HTML body — strip and surface status only.
      if (trimmed.startsWith('<')) {
        return this.t('common.errors.http_status', {status: sCode})
          || ('HTTP ' + sCode);
      }
      // 3. JSON `{"detail": "..."}`.
      if (trimmed.startsWith('{')) {
        try {
          const parsed = JSON.parse(trimmed);
          if (parsed && typeof parsed.detail === 'string' && parsed.detail) {
            return parsed.detail;
          }
        } catch (_) { /* not JSON — fall through */
        }
      }
      // 4. Short plain text — pass through.
      if (trimmed && trimmed.length <= 200) {
        return trimmed;
      }
      // 5. Fallback.
      return this.t('common.errors.http_status', {status: sCode})
        || ('HTTP ' + sCode);
    },

    // Diff helper: ports listed in `hosts_config[].services[]` but
    // NOT seen open in the latest scan. Surfaces "expected listener is
    // down right now" without raising it as a failure (the operator
    // could have disabled the service; surfacing as info-grey is the
    // honest signal).
    curatedOnlyServices(host) {
      if (!host) {
        return [];
      }
      const detected = new Set((host.detected_ports || []).map(p => Number(p.port)));
      const curated = Array.isArray(host.services) ? host.services : [];
      return curated.filter(s => {
        const port = Number(s && s.port);
        return port > 0 && !detected.has(port);
      }).map(s => ({port: Number(s.port), name: s.name || s.label || ''}));
    },

    // Detected ports sorted by port-number ascending, regardless of
    // protocol — TCP and UDP interleave so the chip strip reads as
    // a numeric sequence (`22/tcp · 53/udp · 80/tcp · 443/tcp · …`)
    // instead of grouping all TCP first then all UDP. Operator-
    // requested for at-a-glance scanning of which port numbers are
    // open. Stable: ports tie-break on protocol so UDP/TCP duplicates
    // (rare but possible — e.g. dual-stack DNS on 53) order
    // deterministically across renders.
    sortedDetectedPorts(host) {
      const arr = (host && Array.isArray(host.detected_ports)) ? host.detected_ports : [];
      if (arr.length < 2) {
        return arr;
      }
      return [...arr].sort((a, b) => {
        const pa = Number(a && a.port) || 0;
        const pb = Number(b && b.port) || 0;
        if (pa !== pb) {
          return pa - pb;
        }
        const ta = (a && a.protocol) === 'udp' ? 1 : 0;
        const tb = (b && b.protocol) === 'udp' ? 1 : 0;
        return ta - tb;
      });
    },
    // Convenience: clear the provider filter ("All" pill click).
    clearHostsProviderFilter() {
      this.hostsProviderFilter = new Set();
      try {
        if (typeof sessionStorage !== 'undefined') {
          sessionStorage.removeItem('hostsProviderFilter');
        }
      } catch (_) { /* ignore */
      }
    },
    // Whether `name` is currently in the active filter set.
    isHostsProviderFilterActive(name) {
      return !!(this.hostsProviderFilter && this.hostsProviderFilter.has(name));
    },

    // Status taxonomy considered "in trouble" — matches the Telegram
    // AI context's `problem_hosts` block for symmetry. `unconfigured`
    // hosts are intentionally NOT in this set per CLAUDE.md (curated
    // rows with no provider mapped are inventory-only entries, not
    // outages).
    _PROBLEM_HOST_STATUSES: new Set(['down', 'paused', 'unknown']),
    isProblemHost(h) {
      if (!h) {
        return false;
      }
      const st = String(h.status || '').toLowerCase();
      return this._PROBLEM_HOST_STATUSES.has(st);
    },
    // Count of hosts currently in trouble — drives the chip badge AND
    // the Stats dashboard "Problem hosts" tile.
    problemHostCount() {
      const list = this.hosts || [];
      let n = 0;
      for (const h of list) {
        if (this.isProblemHost(h)) {
          n++;
        }
      }
      return n;
    },
    toggleProblemHostsFilter() {
      this.hostsProblemFilter = !this.hostsProblemFilter;
      try {
        if (typeof sessionStorage !== 'undefined') {
          if (this.hostsProblemFilter) {
            sessionStorage.setItem('hostsProblemFilter', '1');
          } else {
            sessionStorage.removeItem('hostsProblemFilter');
          }
        }
      } catch (_) { /* private mode / quota — ignore */
      }
    },
    // Count of curated rows that have NO provider field mapped.
    // Used by the synthetic 'none' chip to surface "how many
    // inventory-only hosts are sitting on the page right now?".
    hostsWithNoProviderCount() {
      return (this.hosts || []).filter(h => !this.hostHasAgent(h)).length;
    },
    // Status chip for a provider in the Hosts toolbar. Combines
    // "enabled in settings" with "actually returned data for at least
    // one host" so operators spot misconfigs fast.
    hostsProviderState(name) {
      const active = (this.hostsActiveSources || []).includes(name);
      const err = (this.hostsProviderErrors || {})[name];
      // matchCount = hosts where the probe successfully returned data
      // (drives the ✓ N tooltip subtitle).
      const matchCount = (this.hosts || [])
        .filter(h => (h.providers || []).includes(name)).length;
      // configuredCount = hosts whose curated config maps them to this
      // provider, regardless of whether the latest probe succeeded.
      // This is the load-bearing signal for chip visibility: "any host
      // CARES about this provider" rather than "the provider is
      // currently returning data". The former survives a transient
      // hub outage so the red ✗ chip still surfaces; the latter would
      // hide on every outage and silently lose the visibility the
      // operator most needs.
      const configuredCount = this._hostsConfiguredForProvider(name);
      // Hide the chip entirely when no curated host has the provider
      // mapped — that's noise on the toolbar regardless of error state.
      // Operator-flagged: Webmin chip kept rendering as a red ✗ even
      // when zero hosts had `webmin_name` set, because the backend
      // stamps `provider_errors["webmin"] = "missing user / password"`
      // whenever the CSV lists "webmin" without credentials. That
      // error is about configuration absence, not about probe failures
      // against active hosts — when no host is using the provider,
      // the toolbar chip has nothing to report. Settings → Host stats
      // is the right surface for setup-gap nagging; the toolbar chip
      // is for "providers I care about" at a glance.
      if (configuredCount === 0) {
        return {visible: false, cls: '', icon: '', title: '', styled: false};
      }
      // Configured-but-not-active state — at least one host has the
      // provider mapped in its curated config BUT the operator hasn't
      // added the provider to `host_stats_source`. The provider's
      // master toggle in Admin → Host stats is OFF, so the sampler
      // never runs against it. Surface as a muted (amber) chip with
      // a tooltip explaining the gap so the operator notices without
      // having to remember which sub-tab to click. Without this
      // branch the chip stayed hidden and a freshly-enrolled provider
      // (e.g. http_probe with URLs configured per-host but the master
      // toggle never flipped) looked like the per-row chip was
      // broken.
      if (!active && !err) {
        return {
          visible: true, cls: 'pill-warning', icon: '⚠',
          title: this.t('hosts_extra.provider_filter.title_configured_inactive',
              {name, count: configuredCount})
            || (`${name} — ${configuredCount} host(s) mapped but provider not enabled in Admin → Host stats`),
          styled: false,
        };
      }
      // tooltip titles routed through i18n.
      if (err) {
        return {
          visible: true, cls: 'pill-error', icon: '✗',
          title: this.t('hosts_extra.provider_filter.title_error', {name, error: err}),
          styled: false,
        };
      }
      // Healthy state — use the operator-customised provider colour
      // via `pill-custom` + `providerChipStyle()`.
      // The fixed `pill-ok` green ignored Settings → Providers colour
      // overrides; flip to pill-custom so the toolbar chip matches the
      // per-row chip's colouring.
      const titleKey = matchCount === 1
        ? 'hosts_extra.provider_filter.title_match_one'
        : 'hosts_extra.provider_filter.title_match_many';
      return {
        visible: true, cls: 'pill-custom', icon: '✓',
        title: this.t(titleKey, {name, count: matchCount}),
        styled: true,
      };
    },
    // ----- Stacks / Services / Nodes drawer debug panel ----------
    // Shared helpers keyed by `kind:id`. `kind` is 'item' (covers
    // services / standalone containers / orphans / stack rollups
    // surfaced via drawerItem) or 'node' (drawerNode). Same fetch +
    // open-toggle + loading-state shape as the host-debug panel, so
    // the markup can reuse the existing `.host-debug-*` CSS family
    // verbatim for visual consistency.
    subjectDebugKey(kind, id) {
      return `${kind}:${id || ''}`;
    },
    async toggleSubjectDebug(kind, id) {
      if (!kind || !id) {
        return;
      }
      const key = this.subjectDebugKey(kind, id);
      const open = !this.subjectsDebugOpen[key];
      this.subjectsDebugOpen = {...this.subjectsDebugOpen, [key]: open};
      if (open && !this.subjectsDebug[key] && !this.subjectsDebugLoading[key]) {
        await this.loadSubjectDebug(kind, id);
      }
    },
    async loadSubjectDebug(kind, id) {
      if (!kind || !id) {
        return;
      }
      const key = this.subjectDebugKey(kind, id);
      this.subjectsDebugLoading = {...this.subjectsDebugLoading, [key]: true};
      try {
        const r = await fetch(
          '/api/debug/subject'
          + '?kind=' + encodeURIComponent(kind)
          + '&id=' + encodeURIComponent(id)
          + '&since_hours=1'
        );
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.subjectsDebug = {
            ...this.subjectsDebug,
            [key]: {_error: j.detail || `HTTP ${r.status}`},
          };
          return;
        }
        const d = await r.json();
        this.subjectsDebug = {...this.subjectsDebug, [key]: d};
      } catch (e) {
        this.subjectsDebug = {
          ...this.subjectsDebug,
          [key]: {_error: `Network: ${e.message}`},
        };
      } finally {
        this.subjectsDebugLoading = {
          ...this.subjectsDebugLoading, [key]: false,
        };
      }
    },
    // Jump to the host-drawer debug panel from another surface
    //. Forces the panel OPEN (no toggle, since the operator's
    // intent here is "show me", not "flip"), triggers the lazy load
    // if cold, then scrolls. Idempotent — calling twice on an already-
    // open panel is a no-op except for the re-scroll, which is what
    // you want when the operator clicks the link a second time.
    async jumpToHostDebug(hostId) {
      if (!hostId) {
        return;
      }
      if (!this.hostsDebugOpen[hostId]) {
        this.hostsDebugOpen = {...this.hostsDebugOpen, [hostId]: true};
        if (!this.hostsDebug[hostId] && !this.hostsDebugLoading[hostId]) {
          // Don't await — let the scroll happen now, the panel will
          // populate when the fetch lands.
          this.loadHostDebug(hostId).catch(() => {
          });
        }
      }
      this._scrollHostSectionIntoView(`debug-${hostId}`);
    },
    // Smooth-scrolls the host-drawer's inner scroller so the named
    // section (`data-host-section="<kind>-<host_id>"`) lands near the
    // top. Plain `scrollIntoView({block:'start'})` worked in
    // Chrome but Safari was scrolling the page instead of the drawer
    // — so this helper finds the drawer's scrollable ancestor
    // explicitly and sets `scrollTop` directly. Two rAFs after the
    // x-show flip ensure layout has been painted before we measure.
    _scrollHostSectionIntoView(sectionKey) {
      const sel = `[data-host-section="${sectionKey}"]`;
      this.$nextTick(() => {
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const el = document.querySelector(sel);
          if (!el) {
            return;
          }
          // Walk up to the nearest scrollable ancestor — the
          // host-drawer panel itself in practice.
          let scroller = el.parentElement;
          while (scroller) {
            const cs = window.getComputedStyle(scroller);
            if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll')
              && scroller.scrollHeight > scroller.clientHeight) {
              break;
            }
            scroller = scroller.parentElement;
          }
          if (scroller) {
            const rect = el.getBoundingClientRect();
            const srect = scroller.getBoundingClientRect();
            const target = scroller.scrollTop + (rect.top - srect.top) - 12;
            try {
              scroller.scrollTo({top: target, behavior: 'smooth'});
            } catch (_) {
              scroller.scrollTop = target;
            }
          } else {
            try {
              el.scrollIntoView({behavior: 'smooth', block: 'start'});
            } catch (_) {
            }
          }
        }));
      });
    },
    async loadHostDebug(hostId) {
      if (!hostId) {
        return;
      }
      this.hostsDebugLoading = {...this.hostsDebugLoading, [hostId]: true};
      try {
        // Pass `since_hours` so the backend's `samples_in_window`
        // block matches the chart range picker. Operator selecting
        // "1h" sees how many samples landed in the past hour for
        // each time-series table — the diagnostic for "why is the
        // past-hour chart cut?". Falls through to 1 when the
        // picker hasn't hydrated yet.
        const sinceHours = Math.max(1, Math.min(168, +this.hostHistoryRange || 1));
        const r = await fetch(
          '/api/hosts/debug?id=' + encodeURIComponent(hostId)
          + '&since_hours=' + sinceHours
        );
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.hostsDebug = {
            ...this.hostsDebug,
            [hostId]: {_error: j.detail || `HTTP ${r.status}`},
          };
          return;
        }
        const d = await r.json();
        this.hostsDebug = {...this.hostsDebug, [hostId]: d};
      } catch (e) {
        this.hostsDebug = {
          ...this.hostsDebug,
          [hostId]: {_error: `Network: ${e.message}`},
        };
      } finally {
        this.hostsDebugLoading = {...this.hostsDebugLoading, [hostId]: false};
      }
    },
    // Drawer per-unit pane — toggle open/closed. First open lazy-
    // loads the data via `loadHostBeszelServices`. Subsequent
    // opens reuse the cached snapshot; the cache invalidates only
    // when the operator explicitly re-fetches (rare; the data
    // moves slowly compared to chart cadence).
    async toggleHostBeszelServices(hostId) {
      if (!hostId) {
        return;
      }
      const open = !this.hostBeszelServicesOpen[hostId];
      this.hostBeszelServicesOpen = {...this.hostBeszelServicesOpen, [hostId]: open};
      if (open) {
        if (!this.hostsBeszelServices[hostId]
          && !this.hostsBeszelServicesLoading[hostId]) {
          await this.loadHostBeszelServices(hostId);
        }
      }
    },
    // Map systemd ActiveState int → human label. Uses i18n keys with
    // a capitalisation fallback so future Beszel agents that report
    // a state value we haven't translated yet still render readably.
    beszelServiceStateLabel(state) {
      const map = {
        0: 'active', 1: 'reloading', 2: 'inactive',
        3: 'failed', 4: 'activating', 5: 'deactivating',
      };
      const slug = map[+state];
      if (slug) {
        const key = 'host_drawer.beszel_services.state_' + slug;
        const tr = this.t(key);
        if (tr && tr !== key) {
          return tr;
        }
        return slug.charAt(0).toUpperCase() + slug.slice(1);
      }
      return state == null ? '—' : String(state);
    },
    // Pill colour class — failed = red, active = green, inactive +
    // transitional states = muted. Matches the existing pill
    // taxonomy (pill-error / pill-ok / pill-muted) used elsewhere
    // in the drawer.
    beszelServicePillClass(state) {
      const s = +state;
      if (s === 3) {
        return 'pill-error';
      }   // failed
      if (s === 0) {
        return 'pill-ok';
      }      // active
      return 'pill-muted';                // everything else (inactive, transitional)
    },
    // Relative-age formatter for "last_change_ts" — same shape as
    // the samples-in-window panel's age helper. Inputs: seconds
    // (number). Outputs: "30s" / "5m" / "2.3h" / "1.2d".
    relativeAge(seconds) {
      const n = Math.max(0, Math.round(+seconds || 0));
      if (n < 60) {
        return n + 's';
      }
      if (n < 3600) {
        return Math.round(n / 60) + 'm';
      }
      if (n < 86400) {
        return (n / 3600).toFixed(1) + 'h';
      }
      return (n / 86400).toFixed(1) + 'd';
    },
    // "Is this debug payload worth rendering?" — wrapper x-show for
    // each box in the grid. null / undefined → false (hide); empty
    // object / empty array → false; anything else → true.
    hasDebugData(v) {
      if (v === null || v === undefined) {
        return false;
      }
      if (typeof v === 'object') {
        return Array.isArray(v) ? v.length > 0 : Object.keys(v).length > 0;
      }
      return true;
    },
    // Clipboard copy button — pretty-prints the payload and shows a
    // quick toast. Falls back to a prompt() if the Clipboard API is
    // unavailable (old Safari, file:// protocol).
    // aria-label helper for the host-debug-copy buttons — resolves
    // the inner label key (e.g. `debug_panel.labels.counters`) and
    // wraps in the `debug_panel.copy_aria` template ("Copy {label}
    // to clipboard"). Pre-fix the copy buttons were icon-only with
    // a `:title` (hover-only) — screen readers announced "button
    // button button" because the SVG is `aria-hidden="true"` and no
    // accessible name was bound. Per CLAUDE.md "icon-only buttons
    // need aria-label". This helper lets every copy button bind a
    // single one-line aria-label without duplicating the t()
    // composition at every site.
    copyAriaLabel(labelKey) {
      const inner = labelKey ? this.t(labelKey) : this.t('debug_panel.copy_default_label');
      return this.t('debug_panel.copy_aria', {label: inner});
    },
    async copyDebugJson(v, label) {
      const text = this.fmtDebugJson(v);
      if (!text) {
        this.showToast(this.t('toasts_extra.nothing_to_copy'), 'warning');
        return;
      }
      // Resolve the label through i18n. Pre-fix call
      // sites passed English literals like 'Counters' / 'Raw · Pulse'
      // — both the toast AND the prompt() fallback embedded that raw
      // English regardless of locale. Now: call sites pass an i18n
      // KEY (e.g. 'debug_panel.labels.counters') and the helper
      // resolves it. Backwards-compat: if the resolved value matches
      // the raw key (i.e. no translation found), fall back to a
      // generic "debug data" string — also i18n'd. Empty / falsy
      // labels also fall through to that default.
      let resolvedLabel = '';
      if (label) {
        const candidate = this.t(label);
        // `t()` returns the key itself when no translation is found
        // — treat that as "label was a raw string, not an i18n key"
        // and use it verbatim so old callers keep working.
        resolvedLabel = (candidate === label && !label.includes('.'))
          ? label
          : candidate;
      }
      if (!resolvedLabel) {
        resolvedLabel = this.t('debug_panel.copy_default_label');
      }
      try {
        await navigator.clipboard.writeText(text);
        this.showToast(this.t('toasts_extra.copied', {label: resolvedLabel}), 'success');
      } catch (_) {
        // Fallback — let the user copy manually. Wrap the entire
        // English "Copy <label> (Cmd/Ctrl+C):" string in i18n too so
        // non-en operators get a translated prompt header.
        const promptHead = this.t('debug_panel.copy_prompt_fallback', {label: resolvedLabel});
        window.prompt(promptHead, text);
      }
    },
    // Copy ALL debug panes for one host into a single multi-section
    // payload, headed by host_id + ts. Each section is `## <label>`
    // followed by a fenced JSON block, joined by blank lines so the
    // operator can paste a complete bug report into chat / issue
    // tracker without clicking the per-pane copy button N times.
    // Operator-requested: "I copy a lot of panes when debugging
    // issues together — one button to grab them all".
    async copyAllDebug(hostId) {
      const dbg = this.hostsDebug && this.hostsDebug[hostId];
      if (!dbg) {
        this.showToast(this.t('toasts_extra.nothing_to_copy'), 'warning');
        return;
      }
      const sections = [];
      // Header line — host_id + capture timestamp so the recipient
      // can correlate against logs / SSE events.
      const ts = new Date().toISOString();
      sections.push(`# OmniGrid host debug — ${hostId} — ${ts}`);
      // Resolve the host record from the in-memory list so the
      // chart-data bundle can use the same cache-key helpers the
      // drawer chart cards use. Falls through to a synthetic
      // `{id: hostId}` when the host isn't in `this.hosts` (e.g.
      // operator opened debug for a host that's been removed).
      const host = (this.hosts || []).find(h => h && h.id === hostId) || {id: hostId};
      const charts = this.chartDataBundle(host);
      // Sections in the same canonical order the panes render.
      const blocks = [
        ['Active providers', dbg.active_providers],
        ['Counters & state', dbg.counters],
        ['Samples in window', (dbg.counters || {}).samples_in_window],
        ['Failure state', (dbg.counters || {}).failure_state],
        ['Provider pause state', (dbg.counters || {}).provider_pause_state],
        ['Tunables', (dbg.counters || {}).tunables],
        ['Merged host', dbg.merged],
        ['Raw · Pulse', (dbg.providers_raw || {}).pulse],
        ['Raw · Beszel', (dbg.providers_raw || {}).beszel],
        ['Raw · Node-exporter', (dbg.providers_raw || {}).node_exporter],
        ['Raw · Webmin', (dbg.providers_raw || {}).webmin],
        ['Raw · Ping', (dbg.providers_raw || {}).ping],
        ['Raw · SNMP', (dbg.providers_raw || {}).snmp],
        // Chart data — main history, ping, SNMP host / iface / temps.
        // Last so the verbose points blob doesn't push narrow panes
        // off-screen in the paste preview.
        ['Chart data', charts],
      ];
      for (const [label, value] of blocks) {
        if (value === undefined || value === null) {
          continue;
        }
        // Skip empty objects/arrays — keeps the payload tight.
        if (typeof value === 'object'
          && !Array.isArray(value)
          && Object.keys(value).length === 0) {
          continue;
        }
        if (Array.isArray(value) && value.length === 0) {
          continue;
        }
        const body = this.fmtDebugJson(value);
        if (!body) {
          continue;
        }
        sections.push(`## ${label}\n\`\`\`json\n${body}\n\`\`\``);
      }
      const payload = sections.join('\n\n');
      try {
        await navigator.clipboard.writeText(payload);
        this.showToast(this.t('toasts_extra.copied_all_debug', {count: sections.length - 1}), 'success');
      } catch (_) {
        const promptHead = this.t('debug_panel.copy_all_prompt_fallback');
        window.prompt(promptHead, payload);
      }
    },
    // Telemetry = any provider that contributes CPU / Memory / Disk
    // gauges. Ping is reachability + latency only; a ping-only host
    // shouldn't render CPU/Mem/Disk bars (every value would be zero,
    // which looks like "loading" or a broken row). Use this to gate
    // the bars on the host rows (desktop + mobile) and the per-card
    // CPU/Mem/Disk/Net/DiskIO charts in the drawer.
    //
    // Per-metric telemetry gates. Each axis (CPU / Memory / Disk)
    // is gated INDEPENDENTLY so a host that only reports one axis
    // (Cisco SG300 switch via SNMP exposes CPU but no mem/disk;
    // chassis BMCs like Dell iDRAC + APC UPS report ZERO of these
    // axes — only chassis sensors / UPS battery state) shows just
    // the bars it can fill instead of three together (or none).
    // Beszel / Pulse / NE / Webmin always count for all three
    // because they're host-OS agents that emit CPU + Mem + Disk
    // uniformly.
    _hostUnixAgent(h) {
      return !!(h && (h.beszel_name || h.pulse_name || h.ne_url || h.webmin_name));
    },
    // Has the SNMP history series ever recorded a NON-ZERO value
    // for this metric in the loaded window? Used to gate the bar
    // visibility so chassis BMCs (iDRAC / APC UPS / managed
    // switches without hrProcessorLoad) — whose probe writes flat
    // null / zero cpu_used_pct rows on every tick — don't surface
    // a CPU bar that looks "loading" but never moves. The previous
    // gate `hostHasInlineSpark(h, metric)` only required ≥2 finite
    // values; 0 is finite, so flat-zero history was passing as
    // "telemetry available". Stricter check requires at least one
    // strictly-positive sample so a host genuinely at idle (cpu=0
    // sustained) is correctly hidden alongside hosts that never
    // reported the metric at all.
    // Gate semantic: "this host's SNMP agent CAN report CPU/memory"
    // (capability), NOT "this host has had non-zero CPU recently"
    // (value). The DB-level distinction already exists:
    // host_metrics_sampler writes `cpu_used_pct = float(v) if v is
    // not None else None` — so an agent that returns 0% stores 0.0
    // and an agent that doesn't expose CPU stores NULL. Mirror that
    // here: any finite (non-null) value flips the gate, including 0.
    // Hides bars only for genuine no-data hosts (APC UPS, iDRAC
    // chassis BMC, basic switches that don't expose hrProcessorLoad
    // / cpmCPUTotal*); shows bars for idle-at-0 hosts (Cisco SG300
    // switches at 0% CPU) which is the truthful representation.
    _hostHasFiniteSnmpHistory(h, metric) {
      if (!h) {
        return false;
      }
      const entry = this.hostSnmpHistory && this.hostSnmpHistory[h.id];
      const points = entry && Array.isArray(entry.points) ? entry.points : null;
      if (!points || points.length < 2) {
        return false;
      }
      if (metric === 'cpu') {
        for (const p of points) {
          const v = p && p.cpu_used_pct;
          if (v !== null && v !== undefined && Number.isFinite(Number(v))) {
            return true;
          }
        }
        return false;
      }
      if (metric === 'memory') {
        for (const p of points) {
          const tot = Number(p && p.mem_total);
          if (Number.isFinite(tot) && tot > 0) {
            return true;
          }
        }
        return false;
      }
      // SNMP history doesn't currently carry disk_percent — disk
      // axis falls through to live h.disk_total / h.disk_percent.
      return false;
    },
    // Display list of agents enabled on a host — used by the drawer's
    // dedicated "Enabled agents" card. Returns rich objects so the
    // template can render colored pills:
    // { name: 'beszel', label: 'Beszel', pill: 'pill-ok' }
    // Each pill class is hand-mapped for visual distinctness across
    // the five providers (the four ok/info/update colors that
    // `providerStates` uses don't have enough variance for five
    // distinct chips, so pill-primary is added for one slot). Ping
    // shows alongside the four telemetry providers because from the
    // operator's POV it's a distinct opt-in agent — even though it
    // doesn't contribute CPU / Mem / Disk gauges.
    hostEnabledAgents(h) {
      if (!h) {
        return [];
      }
      // Each chip is `pill-custom` so it picks up the configured
      // per-provider colour via providerChipStyle. The
      // hand-mapped pill class names left over from the original
      // implementation are deliberately dropped — the colour now
      // flows from the operator-settable provider_color_* settings,
      // not from a fixed visual mapping that conflated providers.
      //
      //: every per-host enable check is paired with a fleet-level
      // `hasHostStatsSource(<provider>)` gate. A provider that's
      // disabled at the fleet level (operator un-ticked it in
      // Settings → Host stats) MUST NOT render its chip even when
      // the per-host alias / enable flag are still populated —
      // pre-fix a stale SNMP chip with a Paused state still appeared
      // on hosts where SNMP was globally disabled, while the per-chip
      // Resume was disabled (busy-flag stuck) and the rollup-Resume
      // was enabled. Filtering at the chip-render gate makes the
      // contradictory state impossible by construction.
      if (!h) {
        return [];
      }
      const out = [];
      if (h.beszel_name && this.hasHostStatsSource('beszel')) {
        out.push({name: 'beszel', label: 'Beszel'});
      }
      if (h.pulse_name && this.hasHostStatsSource('pulse')) {
        out.push({name: 'pulse', label: 'Pulse'});
      }
      if (h.ne_url && this.hasHostStatsSource('node_exporter')) {
        out.push({name: 'node_exporter', label: 'node-exporter'});
      }
      if (h.webmin_name && this.hasHostStatsSource('webmin')) {
        out.push({name: 'webmin', label: 'Webmin'});
      }
      if (h.ping_enabled && this.hasHostStatsSource('ping')) {
        out.push({name: 'ping', label: 'Ping'});
      }
      // SNMP chip — the gate must MIRROR the SNMP sampler's actual
      // probe-target resolver chain: `aliases[id] → snmp_name →
      // address → SKIP`. Use the existing `_snmpHasProbeTarget`
      // helper (also used by the SNMP chart-mount gate fleet-wide)
      // so a host whose sampler IS probing — because `snmp_name` or
      // `address` is set, even without the explicit per-host opt-in
      // tick — renders the SNMP chip in the drawer.
      //
      // Earlier iteration of this gate required `h.snmp_enabled ===
      // true` strictly. That was tighter than the sampler so the
      // operator's APC UPS (`UPS_10K`, snmp_name set, sampler
      // returning fresh APC vendor data) showed only the Ping chip
      // in the drawer's "Enabled agents" card — SNMP was actively
      // probing but invisible. Same drift class as the chart-mount
      // gate fix that introduced `_snmpHasProbeTarget`. Fleet-wide
      // `hasHostStatsSource('snmp')` still suppresses the chip when
      // SNMP is globally disabled, so a host can't render the chip
      // without something actually probing it.
      if (this._snmpHasProbeTarget(h) && this.hasHostStatsSource('snmp')) {
        out.push({name: 'snmp', label: 'SNMP'});
      }
      // HTTP probe — eighth provider. Surfaces when the host has the
      // per-host http_probe.enabled flag set AND the master toggle is
      // ON (the dual-toggle was collapsed to a single master in the
      // Providers admin, so hasHostStatsSource('http_probe') now reads
      // settings.http_probe_enabled directly).
      if (h.http_probe_enabled === true && this.hasHostStatsSource('http_probe')) {
        out.push({name: 'http_probe', label: 'HTTP probe'});
      }
      // Service probe — per-service-chip reachability sampler. Surfaces
      // when ANY entry in the host's services[] carries probe.enabled
      // === true AND the global master toggle is ON. The sampler's
      // contract mirrors this gate (logic/service_sampler.py only
      // probes opted-in entries when the master setting is ON).
      const hasServiceProbe = Array.isArray(h.services)
        && h.services.some(s => s && s.probe && s.probe.enabled === true);
      if (hasServiceProbe && this.hasHostStatsSource('service_probe')) {
        out.push({name: 'service_probe', label: 'Service probe'});
      }
      return out;
    },
    // List of paused provider names for one host.
    // Used by the drawer's "Resume all (N)" rollup button to enumerate
    // every chip currently in Paused state. Returns an empty array
    // when no provider is paused — the rollup hides cleanly.
    //
    // FILTERS against `hostEnabledAgents(h)` so providers that are
    // NOT currently enabled on this host don't surface as paused —
    // the orphan-row sweep on host-config save handles
    // host-level orphans, but this guard handles the per-host
    // provider-toggle case (operator disabled SNMP on this row but
    // the failure-state row from the previous probes still exists).
    // Without this filter, "Resume all (2)" can render on a host
    // that only has Ping enabled because old SNMP / Webmin failure
    // rows still match the host_id suffix.
    pausedProvidersFor(h) {
      if (!h) {
        return [];
      }
      const map = h.provider_pause_state;
      if (!map || typeof map !== 'object') {
        return [];
      }
      const enabledNames = new Set(
        (this.hostEnabledAgents(h) || []).map((a) => a && a.name).filter(Boolean),
      );
      const out = [];
      for (const name of Object.keys(map)) {
        if (!enabledNames.has(name)) {
          continue;
        }
        const row = map[name];
        if (row && row.paused) {
          out.push(name);
        }
      }
      return out;
    },
    // — Single-source-of-truth predicates for every Resume affordance
    // in the host drawer. Pre-fix the per-chip Resume gated on the busy
    // flag (`providerResumeBusy[host:provider]`), the rollup gated on
    // pausedProvidersFor.length, and the whole-host Resume sampling
    // gated on `h._resumeBusy`. Three independent predicates produced
    // the operator-visible bug where the per-chip button was disabled
    // while the rollup remained enabled, even though both targeted the
    // same provider on the same host.
    //
    // Contract:
    // canResume(h, name)  — per-chip / per-provider Resume button.
    //                        Allowed when admin + the chip is paused
    //                        + neither the per-provider busy flag NOR
    //                        the whole-host busy flag is set.
    // canResumeAny(h)     — "Resume all" rollup buttons (top banner +
    //                        bottom of card). Allowed when admin + at
    //                        least one provider is paused + NO per-
    //                        provider busy is set across the paused
    //                        set + whole-host isn't busy. So clicking
    //                        one per-chip Resume disables the rollup
    //                        for the duration, and vice versa — the
    //                        two affordances can never disagree.
    // canResumeHost(h)    — whole-host Resume sampling button.
    //                        Allowed when admin + the host is paused +
    //                        neither host-busy NOR any per-provider
    //                        busy on this host is set.
    canResume(h, name) {
      if (!h || !name || !this.isAdmin()) {
        return false;
      }
      if (!this.agentPauseInfo(h, name)) {
        return false;
      }
      if (this.providerResumeBusy[h.id + ':' + name]) {
        return false;
      }
      if (h._resumeBusy) {
        return false;
      }
      return true;
    },
    canResumeAny(h) {
      if (!h || !this.isAdmin()) {
        return false;
      }
      const paused = this.pausedProvidersFor(h);
      if (!paused.length) {
        return false;
      }
      if (h._resumeBusy) {
        return false;
      }
      const hostId = h.id;
      for (const name of paused) {
        if (this.providerResumeBusy[hostId + ':' + name]) {
          return false;
        }
      }
      return true;
    },
    canResumeHost(h) {
      if (!h || !this.isAdmin()) {
        return false;
      }
      if (!h.sampling_paused) {
        return false;
      }
      if (h._resumeBusy) {
        return false;
      }
      const hostId = h.id;
      const paused = this.pausedProvidersFor(h);
      for (const name of paused) {
        if (this.providerResumeBusy[hostId + ':' + name]) {
          return false;
        }
      }
      return true;
    },
    // Resume-all action for the drawer rollup. Fans out
    // `resumeProvider(h, name)` calls in parallel for every currently-
    // paused provider on this host. Optimistic UI clears each row's
    // pause state immediately so the chips flip back without waiting
    // for the per-call SSE round-trip; per-call errors land in the
    // toast layer individually so a partial-failure case is visible
    // ("Resumed 4 of 6 providers; 2 failed"). Admin-only — the button
    // hides when the operator isn't admin.
    async resumeAllProviders(host) {
      if (!host || !host.id) {
        return;
      }
      const paused = this.pausedProvidersFor(host);
      if (!paused.length) {
        return;
      }
      const total = paused.length;
      const results = await Promise.allSettled(
        paused.map((name) => this.resumeProvider(host, name)),
      );
      const failed = results.filter((r) => r.status === 'rejected').length;
      const ok = total - failed;
      if (failed === 0) {
        this.showToast(this.t('hosts_extra.provider_resume_all_done', {
          count: ok, host: this.hostDisplayName(host) || host.id,
        }), 'success');
      } else {
        this.showToast(this.t('hosts_extra.provider_resume_all_partial', {
          ok, total, failed, host: this.hostDisplayName(host) || host.id,
        }), 'warning');
      }
    },
    // Per-(provider, host) auto-pause lookup. Returns the
    // pause-state row for `name` on `h`, or null when the provider
    // isn't paused for this host. Backend populates
    // `provider_pause_state: {snmp: {paused, consecutive_failures,
    // last_error, paused_at, last_ok_ts, ...}}` on every host API
    // row via `_provider_pause_state_for_host(host_id)`. Used by the
    // host drawer's Enabled-agents card to render Paused styling +
    // the Resume button (admin-only).
    // Drawer-chip state-class resolver — mirrors the outer Hosts-row
    // provider chip (`providerStates(h)` → 'failing'/'paused'/'ok')
    // so the drawer's "ENABLED AGENTS" pills reflect the SAME state
    // colour the operator sees outside the drawer. Failing → pill-error
    // (red), paused → pill-warning (orange), otherwise pill-custom
    // (operator-customised brand colour via providerChipStyle).
    _agentStateFor(h, name) {
      if (!h || !name) {
        return 'ok';
      }
      // Try the providerStates list first — same data the outer chip
      // strip consumes. Falls back to agentPauseInfo for older code
      // paths that don't populate providerStates.
      try {
        const states = (typeof this.providerStates === 'function')
          ? this.providerStates(h) : [];
        if (Array.isArray(states)) {
          const match = states.find(p => p && p.name === name);
          if (match && (match.state === 'failing' || match.state === 'paused')) {
            return match.state;
          }
        }
      } catch (_) { /* fall through to pause-info fallback */
      }
      if (this.agentPauseInfo(h, name)) {
        return 'paused';
      }
      return 'ok';
    },
    agentStateClass(h, name) {
      const s = this._agentStateFor(h, name);
      if (s === 'failing') {
        return 'pill-error';
      }
      if (s === 'paused') {
        return 'pill-warning';
      }
      return 'pill-custom';
    },
    agentStateStyle(h, name) {
      const s = this._agentStateFor(h, name);
      if (s === 'failing' || s === 'paused') {
        return '';
      }
      return this.providerChipStyle(name);
    },
    agentStateTitle(h, name) {
      const s = this._agentStateFor(h, name);
      if (s === 'paused') {
        const info = this.agentPauseInfo(h, name) || {};
        return this.t('hosts_extra.provider_paused', {
          provider: name,
          count: info.consecutive_failures || 0,
          error: info.last_error || '—',
        });
      }
      if (s === 'failing') {
        return this.t('hosts_extra.provider_failing', {provider: name});
      }
      return '';
    },
    agentPauseInfo(h, name) {
      if (!h || !name) {
        return null;
      }
      const map = h.provider_pause_state;
      if (!map || typeof map !== 'object') {
        return null;
      }
      const row = map[name];
      if (!row || !row.paused) {
        return null;
      }
      // Match `pausedProvidersFor` — only surface paused state for
      // providers that are CURRENTLY enabled on this host. Stale
      // failure-state rows from a previously-enabled provider that
      // the operator has since disabled would otherwise render an
      // amber chip + Resume button on a chip that wouldn't render
      // at all. Cheap enabledNames lookup; called per-chip so this
      // is hot path.
      const enabledNames = new Set(
        (this.hostEnabledAgents(h) || []).map((a) => a && a.name).filter(Boolean),
      );
      if (!enabledNames.has(name)) {
        return null;
      }
      return row;
    },
    // Last-OK timestamp for a (host, provider) pair. Returns 0 when
    // the provider has never had a successful probe recorded for this
    // host on the current schema (host hasn't been seen
    // shipped, or this is the first probe ever). The chip subtitle
    // hides on 0.
    providerLastOkSeconds(h, name) {
      if (!h || !name) {
        return 0;
      }
      const map = h.provider_pause_state;
      if (!map || typeof map !== 'object') {
        return 0;
      }
      const row = map[name];
      if (!row) {
        return 0;
      }
      return Number(row.last_ok_ts || 0);
    },
    // Human-friendly "Xm ago" / "Xh ago" age string for the chip
    // subtitle. Returns empty when there's nothing to render (the
    // x-show gate hides the span anyway, but the helper stays
    // defensive).
    providerLastOkAge(h, name) {
      const ts = this.providerLastOkSeconds(h, name);
      if (!ts) {
        return '';
      }
      return this.fmtAgo(ts * 1000);
    },
    // Per-(provider, host) row count from the provider's local samples
    // table. Reads `h.provider_sample_counts[<name>]` populated by the
    // backend's `_merge_one_host` (per-host probe path only — bulk
    // /api/hosts/list path leaves the map empty so the chip subtitle
    // hides until the drawer triggers /api/hosts/one/{id}). 0 when the
    // probe hasn't run yet OR the provider's sample table is empty.
    providerSampleCount(h, name) {
      if (!h || !name) {
        return 0;
      }
      const map = h.provider_sample_counts;
      if (!map || typeof map !== 'object') {
        return 0;
      }
      const v = map[name];
      return Number.isFinite(+v) ? +v : 0;
    },
    // Per-provider effective sampler interval in seconds. Backend
    // (`_provider_sample_intervals`) has already resolved the
    // "0 = inherit" sentinel + applied each sampler's floor, so this
    // value matches the actual asyncio.sleep cadence the loop ticks at.
    // 0 when the per-host probe hasn't run yet (cold-load skeleton);
    // chip subtitle hides on 0.
    providerSampleInterval(h, name) {
      if (!h || !name) {
        return 0;
      }
      const map = h.provider_sample_intervals;
      if (!map || typeof map !== 'object') {
        return 0;
      }
      const v = map[name];
      return Number.isFinite(+v) ? +v : 0;
    },
    // Human-friendly "Every Ns" / "Every Nm" / "Every Nm Ks" cadence
    // label for the chip subtitle. Routes through the same locale
    // formatter the SPA uses for last-OK ages so it reads naturally
    // alongside "Updated 24s ago". Returns empty string on 0 so the
    // caller's x-show gate hides cleanly.
    providerSampleIntervalLabel(h, name) {
      const s = this.providerSampleInterval(h, name);
      if (!s) {
        return '';
      }
      if (s < 60) {
        return s + 's';
      }
      const m = Math.floor(s / 60);
      const rem = s - m * 60;
      if (rem === 0) {
        return m + 'm';
      }
      return m + 'm ' + rem + 's';
    },
    // Resume-button busy-state map. Keyed `<host_id>:<provider>` so
    // simultaneous resumes on different providers don't collide.
    providerResumeBusy: {},
    // Manual resume action for the per-provider auto-pause.
    // POSTs /api/hosts/{id}/provider/{name}/resume which clears the
    // failure-state row + the in-memory cool-down for that provider.
    // Optimistic UI: clear the local pause row immediately so the
    // chip flips back without waiting for the next poll, then refresh
    // the row via the shared queue to confirm.
    async resumeProvider(host, name) {
      if (!host || !host.id || !name) {
        return;
      }
      const key = host.id + ':' + name;
      if (this.providerResumeBusy[key]) {
        return;
      }
      this.providerResumeBusy[key] = true;
      // Safety timer — even if `await fetch` hangs forever (browser
      // network freeze, broken proxy holding the connection, etc.),
      // the button gets re-enabled after 30s. Prevents the
      // "click-once-stuck-forever" footgun that operator hit.
      const safetyTimer = setTimeout(() => {
        this.providerResumeBusy[key] = false;
      }, 30000);
      try {
        const r = await fetch(
          '/api/hosts/' + encodeURIComponent(host.id)
          + '/provider/' + encodeURIComponent(name) + '/resume',
          {method: 'POST'},
        );
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          this.showToast(this.t('hosts_extra.provider_resume_failed', {
            provider: name,
            error: (j && j.detail) || ('HTTP ' + r.status),
          }), 'error');
          return;
        }
        // Optimistic clear so the chip flips immediately. The next
        // refresh from the shared host-refresh queue confirms via the
        // backend's authoritative state.
        if (host.provider_pause_state && host.provider_pause_state[name]) {
          host.provider_pause_state[name].paused = false;
        }
        this.showToast(this.t('hosts_extra.provider_resumed', {
          provider: name,
          host: this.hostDisplayName(host) || host.id,
        }), 'success');
        // Force-refresh the row so backend's authoritative state lands.
        if (typeof this.refreshHostRow === 'function') {
          this.refreshHostRow(host.id, {force: true}).catch(() => {
          });
        }
      } catch (e) {
        this.showToast(this.t('hosts_extra.provider_resume_failed', {
          provider: name,
          error: String(e),
        }), 'error');
      } finally {
        clearTimeout(safetyTimer);
        this.providerResumeBusy[key] = false;
      }
    },
    toggleHost(name) {
      const host = (this.hosts || []).find(h => h.host === name);
      // Already-open drawer can always close (e.g. by clicking the
      // same row again) even if the host flipped from "up" to "down"
      // since the last open — otherwise the operator would be stuck
      // looking at stale detail cards.
      const already = this.drawerHost && this.drawerHost.host === name;
      if (already) {
        this.closeHostDrawer();
        return;
      }
      if (!this.isHostExpandable(host)) {
        return;  // dead / unmatched host — header click is a no-op
      }
      this.openHostDrawer(host);
    },
    // SNMP time-series state. Keyed per-host (no Beszel-id
    // fallback because SNMP probes are always per-host). Reads from
    // the new `/api/hosts/{id}/snmp/history` endpoint that wraps the
    // `host_snmp_samples` table written by the sampler. Same loading-
    // flag pattern as `hostHistory` so chart cards don't flicker on
    // range-picker clicks.
    // HTTP probe latency history — per-host slot. Each entry is the
    // shape returned by `GET /api/hosts/{id}/http-probe/history`:
    //   { series: [{t, url, latency_ms, status_ok, tls_expires_in_days}, ...],
    //     collectors: {sample_count, urls},
    //     loadedAt: <epoch ms>, hours: <1|6|24|168>, error: <str|null> }
    // Wired into `chartFreshness(h)` above so the drawer's "Last sample
    // Xm ago" label includes the HTTP probe series alongside CPU / Mem /
    // SNMP. UI render uses the per-URL grouping captured on each point.
    hostHttpProbeHistory: {},
    // Per-host "Show all" toggle state for the URL strip. Keyed on
    // host id; default off means the collapsed cap applies.
    httpProbeShowAll: {},
    // Per-host row Test button — busy flag + last-result cache.
    // Keyed on host id so multiple drawers (or arrow-key drawer nav)
    // each carry their own state independently.
    httpProbeRowTestBusy: {},
    httpProbeRowTestResult: {},
    /** Viewport-aware collapsed cap for the URL list. Mirrors the
     * `effectiveCollapsedLimit()` pattern from the server-health
     * dense-list rule: tighter cap below 480px so mobile cards
     * stay compact.
     */
    _httpProbeUrlCap() {
      const narrow = (typeof window !== 'undefined' && window.innerWidth && window.innerWidth < 480);
      return narrow ? 3 : 8;
    },
    /** Sort URLs by status weight (failing → warning → ok) so the
     * most actionable URL is always above the fold when the strip is
     * collapsed. Stable secondary order by url so the layout doesn't
     * jitter on every poll.
     */
    _httpProbeSortedUrls(h) {
      if (!h || !Array.isArray(h.host_http_urls) || !h.host_http_urls.length) {
        return [];
      }
      const arr = h.host_http_urls.slice();
      arr.sort((a, b) => {
        // weight: 0=ok (status_ok && content_match_ok); 1=warning
        // (status_ok but content mismatch); 2=fail. Lower wins early.
        const wa = (a.status_ok && a.content_match_ok) ? 0
          : (a.status_ok ? 1 : 2);
        const wb = (b.status_ok && b.content_match_ok) ? 0
          : (b.status_ok ? 1 : 2);
        // Sort failures FIRST in the rendered output — so weight=2
        // comes before weight=0.
        if (wa !== wb) {
          return wb - wa;
        }
        return String(a.url || '').localeCompare(String(b.url || ''));
      });
      return arr;
    },
    httpProbeVisibleUrls(h) {
      const all = this._httpProbeSortedUrls(h);
      if (!all.length) {
        return all;
      }
      if (h && h.id && this.httpProbeShowAll[h.id]) {
        return all;
      }
      return all.slice(0, this._httpProbeUrlCap());
    },
    httpProbeHasMoreUrls(h) {
      const all = this._httpProbeSortedUrls(h);
      return all.length > this._httpProbeUrlCap();
    },
    httpProbeHiddenUrlCount(h) {
      const all = this._httpProbeSortedUrls(h);
      const cap = this._httpProbeUrlCap();
      return Math.max(0, all.length - cap);
    },
    toggleHttpProbeShowAll(h) {
      if (!h || !h.id) {
        return;
      }
      this.httpProbeShowAll[h.id] = !this.httpProbeShowAll[h.id];
    },
    /** Inline SVG mini-chart for the HTTP probe latency series. One
     * line per URL — points carry `t` (epoch s) and `latency_ms`. The
     * x-axis maps the active window (1h / 6h / 24h / 7d); the y-axis
     * auto-scales to the maximum observed latency. Skip points with
     * latency_ms === null (probe failed) so the line renders as a real
     * gap rather than a vertical drop.
     *
     * The output is a small `<svg>` blob — much simpler than the full
     * `hostChart` helper (which carries axes / legends / tooltips), but
     * sufficient for the "is the latency trend stable" question this
     * card needs to answer.
     */
    renderHttpProbeLatencyMiniChart(h) {
      try {
        const entry = (h && this.hostHttpProbeHistory) ? this.hostHttpProbeHistory[h.id] : null;
        if (!entry || !Array.isArray(entry.series) || !entry.series.length) {
          return '';
        }
        const pts = entry.series.filter(p => p && p.latency_ms != null && p.latency_ms >= 0);
        if (!pts.length) {
          return '';
        }
        // Group by URL so each line is independent.
        const byUrl = new Map();
        for (const p of pts) {
          const u = p.url || '';
          if (!byUrl.has(u)) {
            byUrl.set(u, []);
          }
          byUrl.get(u).push(p);
        }
        const urls = Array.from(byUrl.keys()).sort();
        if (!urls.length) {
          return '';
        }
        // Time range — fall back to min/max of points when window
        // is short. SVG coords are 0..100 logical w, 0..40 h (compact).
        let minT = Infinity;
        let maxT = -Infinity;
        let maxV = 0;
        for (const p of pts) {
          if (p.t < minT) {
            minT = p.t;
          }
          if (p.t > maxT) {
            maxT = p.t;
          }
          if (p.latency_ms > maxV) {
            maxV = p.latency_ms;
          }
        }
        if (!isFinite(minT) || !isFinite(maxT) || maxT === minT) {
          return '';
        }
        if (maxV <= 0) {
          maxV = 1;
        }
        const W = 100;
        const H = 36;
        // Deterministic per-URL hue via simple hash so colours stay
        // stable across re-renders. CSS variables can't be used inline
        // in SVG stroke= per the token discipline rule, but the SPA's
        // provider-colour scheme already uses computed hex literals
        // here; we hash to one of a small token palette.
        const _hue = (s) => {
          let h2 = 0;
          for (let i = 0; i < s.length; i++) {
            h2 = (h2 * 31 + s.charCodeAt(i)) >>> 0;
          }
          return h2 % 360;
        };
        const lines = [];
        for (const url of urls) {
          const series = byUrl.get(url).slice().sort((a, b) => a.t - b.t);
          if (series.length < 2) {
            continue;
          }
          let path = '';
          for (let i = 0; i < series.length; i++) {
            const s = series[i];
            const x = ((s.t - minT) / (maxT - minT)) * W;
            const y = H - ((s.latency_ms / maxV) * (H - 2)) - 1;
            path += (i === 0 ? 'M' : ' L') + x.toFixed(2) + ',' + y.toFixed(2);
          }
          const hue = _hue(url);
          lines.push('<path d="' + path + '" fill="none" stroke="hsl(' + hue + ', 65%, 55%)" stroke-width="1.2" vector-effect="non-scaling-stroke"/>');
        }
        if (!lines.length) {
          return '';
        }
        const ariaLabel = this._logEscape(this.t('host_drawer.http_probe.history_heading') || 'Latency history');
        return '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" '
          + 'width="100%" height="44" aria-label="' + ariaLabel + '" role="img">'
          + lines.join('')
          + '</svg>';
      } catch (_) {
        return '';
      }
    },
    /** Derived cache key for the HTTP latency series inside the
     * standard `hostHistory[key]` cache slot. Lets the existing chart
     * helpers (`hostChart`, `hostChartMax`, `hostMetricStats`,
     * `xAxisFromSeries`) work unchanged against the http-probe data. */
    httpLatencyKey(h) {
      return 'http:' + (h && h.id ? h.id : '');
    },
    hostSnmpHistory: {},
    // Per-temperature-probe history for Dell server hosts. Same shape as hostSnmpIfaceHistory but keyed by
    // probe_idx. `probes: { idx: { name, points: [{ts, c}, …] } }`.
    hostSnmpTempHistory: {},
    // Loose SNMP-target gate — true when the curated host has
    // ANY SNMP probe target, regardless of the per-host UI checkbox
    // state. The SPA's `snmp_enabled` flag is the curated-UI toggle;
    // the operational sampler runs whenever `snmp_name` or `address`
    // resolves a target. Pre-fix every SNMP-fetch site checked the
    // strict UI flag → SNMP-tracked hosts whose UI checkbox wasn't
    // ticked never had their history fetched → "Collecting data"
    // placeholder forever despite fresh `host_snmp_iface_samples`
    // rows. Used by the host-list polling loop, drawer-open prefetch,
    // IntersectionObserver lazy-fetch, and per-row probe-arrival
    // kicker — single source of truth so the gate stays consistent.
    _snmpHasProbeTarget(host) {
      if (!host) {
        return false;
      }
      // STRICT opt-in: `snmp_enabled === true` is the master gate —
      // a host that hasn't been explicitly opted-in to SNMP via the
      // per-host checkbox is hidden regardless of which target
      // fields are populated. Same opt-in rigour as
      // `hosts_config[].ssh.enabled` and `hosts_config[].ping.enabled`.
      if (host.snmp_enabled !== true) {
        return false;
      }
      // Once opted in, the gate ALSO requires a resolvable target —
      // either the explicit per-provider `snmp_name` OR the canonical
      // per-host `address` field that the SNMP sampler's resolver
      // chain falls back to (`aliases[id] → snmp_name → address →
      // SKIP` — see logic/snmp.py + CLAUDE.md "address is the
      // canonical provider-independent probe target"). Operator
      // pattern: tick SNMP on a host, leave `snmp_name` blank,
      // populate `address` once with the LAN hostname/IP — the
      // sampler probes against `address`, this chip renders.
      // Without a resolvable target the chip stays hidden so the
      // operator isn't told "SNMP is on" when the sampler has
      // nothing to probe.
      const hasName = !!(host.snmp_name && String(host.snmp_name).trim());
      const hasAddr = !!(host.address && String(host.address).trim());
      return hasName || hasAddr;
    },
    // Per-interface SNMP counter history. One entry per host,
    // each storing { ifaces: { ifname: [points] }, loading, error,
    // loadedAt }. Powers the per-port throughput chart on switches /
    // routers. Same loading-flag + back-compat pattern as
    // `hostSnmpHistory` so chart cards don't flicker.
    hostSnmpIfaceHistory: {},
    // Compute per-interface bps series from the cumulative counters.
    // Returns { in: [bps...], out: [bps...], times: [ts...] } aligned
    // to the points length. Skip-don't-synthesize on out-of-bounds
    // deltas: same bounds as `snmpThroughputBpsSeries`.
    snmpIfaceBpsSeries(hostId, ifname) {
      const entry = this.hostSnmpIfaceHistory[hostId] || {};
      const ifaces = entry.ifaces || {};
      const series = ifaces[ifname] || [];
      if (series.length < 2) {
        return {in: [], out: [], times: []};
      }
      // Bucket-aware dt cap — pre-fix the hardcoded 3600s cap zeroed
      // every delta on 7d windows once the backend started bucketing
      // iface_history (5040s buckets at 7d × 120 target points).
      // Same fix shape as snmpThroughputBpsSeries: cap scales to
      // max(3600, bucket × 3) so one missed bucket is still tolerated
      // but multi-bucket outage gaps are still skipped.
      const bucketS = Number(entry.bucket_seconds) || 0;
      const dtCap = Math.max(3600, bucketS * 3);
      // Byte-delta cap scales with dt — same reasoning as the
      // snmpThroughputBpsSeries fix. Static 10 GB ceiling at 5min raw
      // = generous; at 7d buckets it's a per-bucket-MB/s straitjacket
      // that filters every delta on hosts pushing >16 Mbit/s sustained.
      // Scaled cap = max(10 GB, dt × 125 MB/s) gives 1 Gbit/s headroom.
      const _BYTE_RATE_CEILING = 125 * 1024 * 1024;
      const _STATIC_BYTE_FLOOR = 10 * 1024 * 1024 * 1024;
      // skip-don't-synthesize. Same null-slot pattern as
      // `snmpThroughputBpsSeries`. First slot null (no predecessor);
      // any out-of-bounds delta (wrap / reboot / gap > dtCap / null
      // counter / > scaled byteCap) leaves the slot null instead of
      // plotting a synthesized 0 that visually merges with a real
      // idle iface.
      const inBps = new Array(series.length).fill(null);
      const outBps = new Array(series.length).fill(null);
      const times = series.map(p => p.ts);
      for (let i = 1; i < series.length; i++) {
        const a = series[i - 1], b = series[i];
        const dt = (b.ts || 0) - (a.ts || 0);
        if (dt < 1 || dt > dtCap) {
          continue;
        }
        const byteCap = Math.max(_STATIC_BYTE_FLOOR, dt * _BYTE_RATE_CEILING);
        const ai = a.in_bytes, bi = b.in_bytes;
        if (ai != null && bi != null) {
          const di = bi - ai;
          if (di >= 0 && di <= byteCap) {
            inBps[i] = di / dt;
          }
        }
        const ao = a.out_bytes, bo = b.out_bytes;
        if (ao != null && bo != null) {
          const dout = bo - ao;
          if (dout >= 0 && dout <= byteCap) {
            outBps[i] = dout / dt;
          }
        }
      }
      return {in: inBps, out: outBps, times};
    },
    // Top N interfaces by latest combined throughput. Returns array of
    // { name, lastIn, lastOut, total } sorted desc. Used to pick which
    // ports to plot on the per-port chart so 48-port switches don't
    // produce 96 noisy lines.
    snmpTopIfacesByThroughput(hostId, n) {
      const ifaces = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
      const out = [];
      for (const name of Object.keys(ifaces)) {
        const s = this.snmpIfaceBpsSeries(hostId, name);
        let lastIn = 0, lastOut = 0;
        for (let i = s.in.length - 1; i >= 0; i--) {
          if (s.in[i] > 0) {
            lastIn = s.in[i];
            break;
          }
        }
        for (let i = s.out.length - 1; i >= 0; i--) {
          if (s.out[i] > 0) {
            lastOut = s.out[i];
            break;
          }
        }
        const total = lastIn + lastOut;
        if (total > 0) {
          out.push({name, lastIn, lastOut, total});
        }
      }
      out.sort((a, b) => b.total - a.total);
      return out.slice(0, n || 5);
    },
    snmpHasIfaceHistory(hostId) {
      const ifaces = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
      for (const name of Object.keys(ifaces)) {
        if ((ifaces[name] || []).length >= 2) {
          return true;
        }
      }
      return false;
    },
    // true when this host's SNMP history has accumulated enough
    // points to draw a polyline (≥ 2 ticks). Used to gate the
    // "Collecting data..." spinner block that every SNMP chart card
    // shows during warm-up — operator-flagged that pre-fix every
    // SNMP chart rendered an empty grid + axis labels with no
    // indication that data was being collected.
    snmpHasEnoughHistory(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      return series.length >= 2;
    },
    // true when at least one interface has computable
    // utilization % (history sample with link_speed_mbps known). The
    // heatmap card uses this to decide between rendering chips with
    // util% colours vs the "Collecting data..." spinner. Pre-fix the
    // chips rendered from live ifaces immediately but always grey
    // (link speed null) — looked broken. Now waits until at least
    // one chip can show a meaningful colour.
    snmpHasIfaceUtilization(hostId, h) {
      // True when at least one iface has ≥ 2 ticks of bps history.
      // Link speed used for the divisor is either the agent-reported
      // ifHighSpeed (preferred) or a 100 Mbps fallback assumption
      // — printers / embedded gear that don't expose
      // ifHighSpeed previously had this card stuck at "Collecting…"
      // forever. The fallback divisor is announced in the legend
      // tooltip so operators know the percentages are an
      // approximation on those hosts.
      const names = this.snmpAllIfacesSorted(hostId, h);
      for (const n of names) {
        const s = this.snmpIfaceBpsSeries(hostId, n);
        if (s.in.length >= 2 || s.out.length >= 2) {
          return true;
        }
      }
      return false;
    },
    // per-iface utilization series (% of link capacity) over
    // time. Walks `snmpIfaceBpsSeries` and divides each point by the
    // iface's link capacity (Mbps × 1e6 / 8 = bytes/sec). Returns []
    // when link speed unknown — caller guards on length before
    // rendering. Used by the per-port utilization LINE chart that
    // replaced the chip-strip heatmap (operator-flagged that the
    // chip layout was misread as a broken chart).
    snmpIfaceUtilizationSeries(hostId, ifname, h) {
      // — fall back to a 100 Mbps assumption when ifHighSpeed
      // isn't exposed (printers / embedded gear). The percentages
      // are then approximate but the polyline RENDERS instead of
      // staying empty forever. snmpIfaceLinkSpeedAssumed() flags
      // the assumption for legend display.
      const link = this.snmpIfaceLinkSpeedMbps(hostId, ifname, h)
        || this._DEFAULT_IFACE_LINK_MBPS;
      const linkBps = link * 1000000 / 8;
      if (linkBps <= 0) {
        return [];
      }
      const s = this.snmpIfaceBpsSeries(hostId, ifname);
      // null-aware. When the underlying bps series has a
      // counter-wrap / gap slot (null), propagate the null so
      // `_snmpPolyPoints` skips it instead of plotting 0 %.
      const out = new Array(s.in.length).fill(null);
      for (let i = 0; i < s.in.length; i++) {
        const inV = s.in[i], outV = s.out[i];
        if (inV == null && outV == null) {
          continue;
        }
        const peak = Math.max(inV || 0, outV || 0);
        out[i] = Math.min(100, (peak / linkBps) * 100);
      }
      return out;
    },
    snmpIfaceUtilizationLine(hostId, ifname, h) {
      const vals = this.snmpIfaceUtilizationSeries(hostId, ifname, h);
      if (!vals.length) {
        return '';
      }
      // — pull timestamps from the underlying iface series so the
      // utilization polyline renders against the drawer-shared time
      // domain. snmpIfaceUtilizationSeries derives from snmpIfaceBpsSeries
      // which exposes parallel `times`, identical length to the values
      // array.
      const s = this.snmpIfaceBpsSeries(hostId, ifname);
      return this._snmpPathGapped(vals, 100, {times: s.times});
    },
    // Operator-flagged: APC UPS lance interface at ~37 B/s on a
    // 100 Mbps fallback link = 0.0003% utilization — flat at the
    // bottom of a hardcoded 0..100% Y-axis. The chart looked empty
    // even though the data was correct. Auto-rescale fixes it.
    //
    // Returns the Y-axis max (% of link capacity) for ONE host's
    // utilization chart — the smallest "nice" round number ≥ the
    // max value across the top-5 visible ifaces. Snaps to 100 / 10 /
    // 1 / 0.1 / 0.01 / 0.001 so the Y-axis labels read cleanly.
    // High-traffic switches still render 0..100% as before.
    snmpIfaceUtilizationYMax(hostId, h) {
      const top = this.snmpTopIfacesByThroughput(hostId, 5);
      if (!top || !top.length) {
        return 100;
      }
      let peak = 0;
      for (const t of top) {
        const vals = this.snmpIfaceUtilizationSeries(hostId, t.name, h);
        for (const v of vals) {
          if (v != null && v > peak) {
            peak = v;
          }
        }
      }
      if (peak <= 0) {
        return 100;
      }            // truly idle → keep traditional scale
      if (peak >= 50) {
        return 100;
      }            // typical busy switch / router
      if (peak >= 10) {
        return 50;
      }
      if (peak >= 1) {
        return 10;
      }
      if (peak >= 0.1) {
        return 1;
      }
      if (peak >= 0.01) {
        return 0.1;
      }
      if (peak >= 0.001) {
        return 0.01;
      }
      return 0.001;
    },
    // Y-axis tick labels for the auto-rescaled util chart. Three
    // ticks (top / mid / bottom) so they line up with the existing
    // `.metric-y-axis` flex `justify-content: space-between` rhythm.
    // Format adapts: integer % at ≥ 1, two decimals at < 1, three
    // decimals at < 0.1 — operators reading "0.001%" on the
    // bottom-of-card UPS chart still see real precision.
    snmpIfaceUtilizationYAxisLabels(hostId, h) {
      const yMax = this.snmpIfaceUtilizationYMax(hostId, h);
      const fmt = (v) => {
        if (v >= 1) {
          return Math.round(v) + '%';
        }
        if (v >= 0.1) {
          return v.toFixed(2) + '%';
        }
        return v.toFixed(3) + '%';
      };
      return [fmt(yMax), fmt(yMax / 2), '0%'];
    },
    // Variant of `snmpIfaceUtilizationLine` that scales 0..yMax
    // instead of 0..100. The SVG y-coordinate system is still
    // 0..100 (the `metric-svg` viewBox uses 100 as the height-
    // reference for the polyline path generator), so we pass
    // `refMax=yMax` to `_snmpPathGapped` to stretch the data to
    // fill the chart vertically.
    snmpIfaceUtilizationLineScaled(hostId, ifname, h) {
      const vals = this.snmpIfaceUtilizationSeries(hostId, ifname, h);
      if (!vals.length) {
        return '';
      }
      const yMax = this.snmpIfaceUtilizationYMax(hostId, h);
      const s = this.snmpIfaceBpsSeries(hostId, ifname);
      return this._snmpPathGapped(vals, yMax, {times: s.times});
    },
    // Format helper for the legend's per-iface util % chip. Pre-fix
    // `Math.round(pct)` rendered "~0%" for any sub-1% value, masking
    // the real activity. Now: ≥ 1 → integer %, < 1 → two decimals,
    // < 0.1 → three decimals.
    snmpIfaceUtilizationPctLabel(hostId, ifname, h) {
      const pct = this.snmpIfaceUtilizationPct(hostId, ifname, h);
      if (pct == null) {
        return '';
      }
      if (pct >= 1) {
        return Math.round(pct) + '%';
      }
      if (pct >= 0.1) {
        return pct.toFixed(2) + '%';
      }
      return pct.toFixed(3) + '%';
    },
    // Gap-aware path string for one iface's bps series scaled to refMax.
    // Consumer renders via SVG `<path :d>` not `<polyline :points>` so
    // counter-wrap / reboot / gap nulls show as visual breaks.
    snmpIfaceLine(hostId, ifname, dir, refMax) {
      const s = this.snmpIfaceBpsSeries(hostId, ifname);
      const vals = (dir === 'in' ? s.in : s.out);
      if (!vals.length) {
        return '';
      }
      return this._snmpPathGapped(vals, refMax || 1, {times: s.times});
    },
    snmpIfaceMaxBps(hostId) {
      const ifaces = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
      let m = 0;
      for (const name of Object.keys(ifaces)) {
        const s = this.snmpIfaceBpsSeries(hostId, name);
        for (const v of s.in) {
          if (v > m) {
            m = v;
          }
        }
        for (const v of s.out) {
          if (v > m) {
            m = v;
          }
        }
      }
      return m;
    },
    // slice 4 — link speed (Mbps) for one iface. Tries history
    // first (newest non-null), then falls back to the live
    // `host.network_ifaces[].link_speed_mbps` from the latest probe.
    // Returns null when ifHighSpeed isn't exposed on this device.
    snmpIfaceLinkSpeedMbps(hostId, ifname, h) {
      const series = ((this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {})[ifname] || [];
      for (let i = series.length - 1; i >= 0; i--) {
        const s = series[i].link_speed_mbps;
        if (s != null && s > 0) {
          return s;
        }
      }
      const host = h || (this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null);
      if (host && Array.isArray(host.network_ifaces)) {
        const live = host.network_ifaces.find(i => i && i.name === ifname);
        if (live && live.link_speed_mbps && live.link_speed_mbps > 0) {
          return live.link_speed_mbps;
        }
      }
      return null;
    },
    // 100 Mbps fallback when ifHighSpeed isn't exposed —
    // printers / embedded gear with no managed-NIC reporting still
    // produce a percentage on the per-port utilization chart instead
    // of leaving the card stuck at "Collecting data…". The chart's
    // legend tooltip surfaces "(assumed 100 Mbps)" via
    // `snmpIfaceLinkSpeedAssumed` so operators know the divisor is
    // approximate on those hosts.
    _DEFAULT_IFACE_LINK_MBPS: 100,
    snmpIfaceLinkSpeedAssumed(hostId, ifname, h) {
      return !this.snmpIfaceLinkSpeedMbps(hostId, ifname, h);
    },
    // Utilization % for one iface = max(in, out) bps × 8 ÷ link_bps × 100.
    // Falls back to a 100 Mbps assumption when link speed unknown
    // — pre-fix this returned null so the percent legend stayed
    // blank on printers and the line chart stayed empty forever.
    snmpIfaceUtilizationPct(hostId, ifname, h) {
      const link = this.snmpIfaceLinkSpeedMbps(hostId, ifname, h)
        || this._DEFAULT_IFACE_LINK_MBPS;
      if (!link) {
        return null;
      }
      const s = this.snmpIfaceBpsSeries(hostId, ifname);
      let lastIn = 0, lastOut = 0;
      for (let i = s.in.length - 1; i >= 0; i--) {
        if (s.in[i] > 0) {
          lastIn = s.in[i];
          break;
        }
      }
      for (let i = s.out.length - 1; i >= 0; i--) {
        if (s.out[i] > 0) {
          lastOut = s.out[i];
          break;
        }
      }
      const peakBps = Math.max(lastIn, lastOut);
      const linkBps = link * 1000000 / 8;       // Mbps → bytes/sec capacity
      if (linkBps <= 0) {
        return null;
      }
      return Math.min(100, (peakBps / linkBps) * 100);
    },
    // Full iface list for the heatmap. Tries history first, falls
    // back to the LIVE `host.network_ifaces[]` so chips render
    // immediately when the history table is still empty (fresh
    // SNMP enrolment, or before the first sampler tick lands).
    // Excludes loopback / docker / veth / bridge / cni / flannel /
    // cali / vmnet / tap / tun / ovs prefixes — same exclusion set
    // the sampler uses, so chip count matches what the throughput
    // chart graphs.
    snmpAllIfacesSorted(hostId, h) {
      const exclude = ['lo', 'docker', 'veth', 'br-', 'cni',
        'flannel', 'cali', 'vmnet', 'tap', 'tun', 'ovs'];
      const isExcluded = (name) => {
        const n = (name || '').toLowerCase();
        return exclude.some(p => n.startsWith(p));
      };
      const ifacesHist = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
      let names = Object.keys(ifacesHist).filter(n => !isExcluded(n));
      if (!names.length) {
        const host = h || (this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null);
        if (host && Array.isArray(host.network_ifaces)) {
          names = host.network_ifaces
            .map(i => i && i.name)
            .filter(n => n && !isExcluded(n));
        }
      }
      return names.sort((a, b) =>
        a.localeCompare(b, undefined, {numeric: true, sensitivity: 'base'})
      );
    },
    // Color mapping for the heatmap cell: green < 50 < amber < 85 < red.
    // Same thresholds as the .stat-bar fill (CLAUDE.md: 60/85 split was
    // for CPU/mem; ports tend to alarm earlier so 50/85). Returns a
    // CSS color literal — heatmap uses inline style because there's
    // no pre-existing token for the per-cell shade.
    snmpIfaceHeatmapColor(pct) {
      if (pct == null) {
        return 'var(--surface-3)';
      }      // unknown speed
      if (pct >= 85) {
        return 'var(--danger)';
      }
      if (pct >= 50) {
        return 'var(--warning)';
      }
      if (pct > 0) {
        return 'var(--success)';
      }
      return 'var(--surface-3)';                       // 0% / idle
    },
    // Detect a reboot in the SNMP uptime history. Walks the
    // points pairwise from latest to oldest looking for the LAST
    // backwards-jump in `uptime_s` (each adjacent pair where N's value
    // is less than N-1's = a reboot in that window). Returns
    // `{ts, prev_uptime_s, age_s}` of the most recent detected reboot,
    // or null when no reboot is in the history window. The drawer
    // surfaces a compact "Rebooted Xh ago" badge when this is non-null
    // AND the age is under 24h (older reboots aren't surfaced to keep
    // the badge actionable for fresh anomalies; full uptime is always
    // shown via the live `host_uptime_s` field).
    snmpRebootInfo(h) {
      if (!h) {
        return null;
      }
      const hist = this.hostSnmpHistory[h.id];
      const points = (hist && hist.points) || [];
      if (points.length < 2) {
        return null;
      }
      let detected = null;
      for (let i = 1; i < points.length; i++) {
        const prev = points[i - 1];
        const curr = points[i];
        if (prev.uptime_s == null || curr.uptime_s == null) {
          continue;
        }
        // Reboot fingerprint: uptime went BACKWARDS between samples.
        // Allow a small slack (60s) to absorb counter-precision noise
        // when sampler ticks are tightly spaced.
        if (curr.uptime_s + 60 < prev.uptime_s) {
          detected = {
            ts: curr.ts,
            prev_uptime_s: prev.uptime_s,
            age_s: Math.max(0, Math.round(Date.now() / 1000 - curr.ts)),
          };
        }
      }
      // Only surface reboots within the last 24h — older reboots are
      // archaeology, not actionable.
      if (detected && detected.age_s > 86400) {
        return null;
      }
      return detected;
    },
    // Memory chart Y-axis upper bound. Prefer the LIVE
    // `host_mem_total` (Beszel/NE-style absolute), fall back to the
    // max `mem_total` in history points so the axis renders sensibly
    // before the live probe completes. Final fallback is the highest
    // observed mem_used + mem_buffers + mem_cached + mem_free across
    // history (if mem_total field was never populated).
    snmpMemMax(h) {
      if (!h) {
        return 0;
      }
      const live = +h.host_mem_total || 0;
      if (live > 0) {
        return live;
      }
      const hist = this.hostSnmpHistory[h.id];
      const points = (hist && hist.points) || [];
      let max = 0;
      for (const p of points) {
        if (p.mem_total && +p.mem_total > max) {
          max = +p.mem_total;
        }
      }
      if (max > 0) {
        return max;
      }
      // Synthesise from the layer sum as a last resort.
      for (const p of points) {
        const sum = (+p.mem_used || 0) + (+p.mem_buffers || 0)
          + (+p.mem_cached || 0) + (+p.mem_free || 0);
        if (sum > max) {
          max = sum;
        }
      }
      return max;
    },
    // Freshness label for the SNMP chart section. Returns
    // `{age_s, label, stale}` or null when there's no data yet.
    // `stale` is true once age exceeds 2× the host_snmp sampler
    // cadence (~5min), used to amber-tint the label so the
    // operator knows the data hasn't refreshed in a while.
    //
    // — Combined freshness across the two writers feeding the
    // host drawer: the LIFESPAN sampler (writes `host_snmp_samples`,
    // surfaces here as `hist.points[last].ts`) AND the per-request
    // gather path (writes the snapshot `_stale_ts`). Pre-fix this
    // helper read ONLY the sampler's most-recent row, so when the
    // gather path successfully merged live data 7m ago but the
    // sampler hadn't written a row in 9h (sampler paused / gated /
    // its INSERT condition not met for this host), the label said
    // "Last sample 9h ago" while the snapshot banner — sourced from
    // `_stale_ts` — said "Last live data 7m ago". Two surfaces
    // disagreed about the SAME host. Post-fix: take the
    // most-recent of (sampler ts, snapshot ts) so both surfaces
    // report the same value. The downstream root cause (sampler
    // lagging the gather path) is a separate concern; this is the
    // honest-UI fix that always reflects the operator's freshest
    // signal.
    snmpHistoryFreshness(h) {
      if (!h) {
        return null;
      }
      const hist = this.hostSnmpHistory[h.id];
      const samplerTs = (hist && Array.isArray(hist.points) && hist.points.length)
        ? Number((hist.points[hist.points.length - 1] || {}).ts
          || (hist.points[hist.points.length - 1] || {}).t || 0)
        : 0;
      const snapshotTs = Number(h._stale_ts || 0);
      const ts = Math.max(samplerTs, snapshotTs);
      if (!ts || !Number.isFinite(ts) || ts <= 0) {
        return null;
      }
      const ageS = Math.max(0, Math.round(Date.now() / 1000 - ts));
      let label;
      if (ageS < 60) {
        label = ageS + 's';
      } else {
        if (ageS < 3600) {
          label = Math.round(ageS / 60) + 'm';
        } else {
          label = Math.round(ageS / 3600) + 'h';
        }
      }
      // `source` lets the template render a tooltip explaining which
      // writer the timestamp came from — operators tracing
      // freshness disagreements can see at a glance whether the
      // value came from the sampler or the snapshot.
      const source = (samplerTs >= snapshotTs) ? 'sampler' : 'snapshot';
      return {age_s: ageS, label, stale: ageS > 600, source};
    },
    // Build a polyline `points` attribute from a series of values.
    // Normalises against `max` (default = max value in series) so the
    // chart spans the full SVG viewBox. ViewBox 420×120 matches the
    // existing Beszel / NE chart cards so the SNMP charts
    // render at the same scale + gridline density as their cousins.
    // — Unified drawer time-domain for every host-drawer chart.
    // Returns the [tMinSec, tMaxSec] window the picker has selected
    // (1h / 6h / 24h / 7d) anchored to "now" so every chart renders
    // against the SAME visual x-axis. Pre-fix each helper computed
    // x by sample-INDEX (`x = i / (n-1) * w`), which made each
    // chart's leftmost pixel mean "the oldest sample I have" — varied
    // across providers (NE 4 samples / 38 min, Beszel 12 / 55 min,
    // SNMP 60 / 60 min) so spikes never aligned vertically across
    // cards. Time-based x makes leftmost = "now - rangeMs" universally
    // and a sparse provider's polyline simply starts mid-axis where
    // its earliest sample landed. Width / height kept at the existing
    // `_snmpPolyPoints` constants (w=420 hh=120) so card paddings &
    // axis labels stay calibrated.
    _drawerTimeDomain() {
      const rangeHours = Number(this.hostHistoryRange) || 1;
      const tMaxSec = Math.floor(Date.now() / 1000);
      const tMinSec = tMaxSec - (rangeHours * 3600);
      return {tMinSec, tMaxSec, w: 420, hh: 120};
    },
    // Auto-derive a "this is a gap" threshold (seconds) from the actual
    // sample cadence. Median Δt × 2.5 covers natural sampler jitter
    // (tick alignment / skew, occasional doubled ticks) but flags
    // genuine outage-class gaps. 60s floor so a fast sampler doesn't
    // false-positive on a one-tick hiccup. Used by every chart helper
    // to break the rendered line at long gaps so a multi-hour host
    // outage (power failure, network drop, manual shutdown) renders as
    // a visual discontinuity instead of one fake-smooth line bridging
    // the dead period. Provider-agnostic — works whether the series
    // came from Beszel (variable tier cadence), NE (5min), Ping
    // (configurable), or SNMP (5min default), because the threshold is
    // derived from the data itself rather than hard-coded per source.
    _detectGapThresholdSec(times) {
      if (!times || times.length < 3) {
        return null;
      }
      const deltas = [];
      let prev = 0;
      for (const t of times) {
        const ts = Number(t) || 0;
        if (!ts) {
          continue;
        }
        if (prev > 0) {
          const dt = ts - prev;
          if (dt > 0) {
            deltas.push(dt);
          }
        }
        prev = ts;
      }
      if (deltas.length < 2) {
        return null;
      }
      deltas.sort((a, b) => a - b);
      const median = deltas[Math.floor(deltas.length / 2)];
      return Math.max(60, median * 2.5);
    },
    _snmpPolyPoints(values, max, opts) {
      // null-aware. Skip-don't-synthesize: when a counter-rate
      // helper passes a null at a wrap / reboot / gap point, OMIT it
      // from the polyline points string instead of plotting it as 0.
      // CPU per-core / load polylines that fill empty slots with 0
      // still work because 0 IS a meaningful "load=0" value for those
      // series. Pre-fix the polyline string contained only the valid
      // points so the rendered line bridged across nulls — visually
      // identical to a steady ramp, hiding the gap. Most chart cards
      // now use `_snmpPathGapped` for the SVG `<path d>` attribute
      // (M commands at every gap so genuine outages render as breaks
      // instead of bridges). This helper stays for the legacy
      // `<polyline points>` consumers (CPU per-core / load) which
      // never emit nulls.
      //
      // unified time-domain — when `opts.times` (parallel array
      // of epoch SECONDS) is supplied, x is computed against the
      // drawer-shared [tMin, tMax] window so this chart's pixel
      // coordinates match every other chart in the open drawer. When
      // `times` is absent, falls back to the legacy index-based
      // scaling for un-migrated callers.
      if (!values || !values.length) {
        return '';
      }
      const m = max !== undefined ? max : Math.max(0.0001, ...values.filter(v => v != null));
      const n = values.length;
      const times = opts && opts.times;
      const dom = (times && times.length === n) ? this._drawerTimeDomain() : null;
      const w = dom ? dom.w : 420;
      const hh = dom ? dom.hh : 120;
      const span = dom ? Math.max(1, dom.tMaxSec - dom.tMinSec) : 1;
      const out = [];
      for (let i = 0; i < n; i++) {
        const v = values[i];
        if (v == null) {
          continue;
        }
        let x;
        if (dom) {
          const ts = Number(times[i]) || 0;
          if (!ts) {
            continue;
          }
          x = ((ts - dom.tMinSec) / span) * w;
          if (x < 0 || x > w) {
            continue;
          }
        } else {
          x = (i / Math.max(1, n - 1)) * w;
        }
        const y = hh - ((+v || 0) / m) * hh;
        out.push(`${x.toFixed(1)},${y.toFixed(1)}`);
      }
      return out.join(' ');
    },
    // Gap-aware SVG path builder. Same scaling as `_snmpPolyPoints`
    // but emits an SVG path `d` string with `M` (moveto) at every gap
    // so a single `<path>` element renders as multiple disconnected
    // segments — genuine null gaps appear as visual breaks instead of
    // straight-line bridges. Cheaper than rendering N `<polyline>`
    // elements when the series has many gaps. Consumers swap their
    // `<polyline points="...">` for `<path d="...">` and bind the
    // result here.
    //
    // unified time-domain — same `opts.times` contract as
    // `_snmpPolyPoints`.
    _snmpPathGapped(values, max, opts) {
      if (!values || !values.length) {
        return '';
      }
      const m = max !== undefined ? max : Math.max(0.0001, ...values.filter(v => v != null));
      const n = values.length;
      const times = opts && opts.times;
      const dom = (times && times.length === n) ? this._drawerTimeDomain() : null;
      const gapThr = times ? this._detectGapThresholdSec(times) : null;
      const w = dom ? dom.w : 420;
      const hh = dom ? dom.hh : 120;
      const span = dom ? Math.max(1, dom.tMaxSec - dom.tMinSec) : 1;
      const out = [];
      let needMove = true;
      let prevTs = 0;
      for (let i = 0; i < n; i++) {
        const v = values[i];
        if (v == null) {
          // Null = gap. Next valid point starts a fresh sub-path.
          needMove = true;
          prevTs = 0;
          continue;
        }
        let x;
        let curTs = 0;
        if (dom) {
          curTs = Number(times[i]) || 0;
          if (!curTs) {
            needMove = true;
            prevTs = 0;
            continue;
          }
          x = ((curTs - dom.tMinSec) / span) * w;
          if (x < 0 || x > w) {
            needMove = true;
            prevTs = 0;
            continue;
          }
        } else {
          x = (i / Math.max(1, n - 1)) * w;
        }
        // Time-gap break — when consecutive valid samples are separated
        // by > gapThreshold seconds, emit M (moveto) instead of L
        // (lineto) so the rendered line breaks. Catches multi-hour host
        // outages where the underlying sampler simply stopped writing
        // rows for a stretch — pre-fix the line bridged the dead period
        // as a single fake-smooth segment, painting "down for hours" as
        // "fading from X to Y".
        if (!needMove && gapThr && prevTs > 0 && curTs > 0 && (curTs - prevTs) > gapThr) {
          needMove = true;
        }
        const y = hh - ((+v || 0) / m) * hh;
        out.push(`${needMove ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`);
        needMove = false;
        prevTs = curTs;
      }
      return out.join(' ');
    },
    // Min/Max/Last over a series field. Returns null when the
    // series is empty so the legend's `x-show` short-circuits to
    // hidden. Mirrors the shape of `hostMetricStats(...)` so the
    // template binding reads the same.
    snmpStats(hostId, key, idx) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (!series.length) {
        return null;
      }
      let pick;
      if (key === 'cpu_per_core' && typeof idx === 'number') {
        pick = (p) => (p.cpu_per_core || [])[idx];
      } else {
        pick = (p) => p[key];
      }
      const vals = series.map(pick).filter(v => v !== null && v !== undefined);
      if (!vals.length) {
        return null;
      }
      let min = Infinity, max = -Infinity;
      for (const v of vals) {
        if (v < min) {
          min = v;
        }
        if (v > max) {
          max = v;
        }
      }
      // Operator-reported on a Ubiquiti USW Enterprise switch:
      // drawer "Used X%" legend showed 100% while the row CPU bar
      // read 0%. Root cause: this helper used to return the last
      // NON-NULL value from `vals` (the filtered list), which on
      // hosts whose probes intermittently return CPU and then go
      // null can be a stale 100% from hours ago — disconnected
      // from the latest actual probe. The chart line correctly
      // plots fresh nulls as 0; the legend should match. Now scan
      // the FULL series back-to-front for the most recent
      // non-null point — this is "the most recent KNOWN value at
      // or before now" rather than "the last value we ever
      // observed". For all-non-null series the result is
      // identical to the previous behaviour. `lastIdx` exposed
      // alongside so callers can detect "last sample is stale"
      // (when `lastIdx < series.length - 1`).
      let last = null, lastIdx = -1;
      for (let i = series.length - 1; i >= 0; i--) {
        const v = pick(series[i]);
        if (v !== null && v !== undefined) {
          last = v;
          lastIdx = i;
          break;
        }
      }
      const stale = lastIdx >= 0 && lastIdx < series.length - 1;
      return {min, max, last, lastIdx, stale};
    },
    // Five evenly-spaced X-axis timestamp labels for the
    // bottom of the chart. Matches the existing `xAxisFromSeries`
    // call shape on Beszel / NE cards.
    //
    // — Switched to drawer-unified [tMin, tMax] window so SNMP
    // chart axis labels match Beszel / NE / Ping cards on the same
    // pixel positions. Pre-fix SNMP labels reflected actual sample
    // timestamps; post-fix they reflect the picker's selected range
    // (1h / 6h / 24h / 7d) anchored to "now".
    snmpXAxis(hostId, n) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      n = n || 5;
      if (series.length < 2) {
        return Array(n).fill('');
      }
      const dom = this._drawerTimeDomain();
      const span = Math.max(1, dom.tMaxSec - dom.tMinSec);
      const out = [];
      for (let i = 0; i < n; i++) {
        const ts = dom.tMinSec + Math.round((i / (n - 1)) * span);
        out.push(this._fmtAxisTime(ts));
      }
      return out;
    },
    // _snmpFmtAxisTime was a parallel copy of `_fmtAxisTime` —
    // consolidated 2026-05-10 per user feedback ("cant we unify and
    // use generic helper for all charts to unify?"). Every chart card
    // in the drawer (Beszel / NE / Pulse / Webmin / Ping / SNMP host
    // / SNMP per-iface / SNMP per-temp probe) routes its X-axis label
    // formatting through `_fmtAxisTime` now. Drift-prevention: if a
    // future chart needs a different format, add an opts arg to
    // `_fmtAxisTime` rather than forking another `_xFmtAxisTime`.
    // CPU per-core lines — one polyline string per core index.
    snmpCpuPerCoreLines(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (!series.length) {
        return [];
      }
      // Determine core count from the FIRST point that has a non-empty
      // cpu_per_core. Older samples may have fewer cores (e.g. host
      // reboot changed core count); render only the consistent prefix.
      const numCores = (series.find(p => (p.cpu_per_core || []).length) || {}).cpu_per_core?.length || 0;
      const out = [];
      const times = series.map(p => p.ts);
      for (let i = 0; i < numCores; i++) {
        const vals = series.map(p => (p.cpu_per_core || [])[i] ?? 0);
        out.push(this._snmpPathGapped(vals, 100, {times}));
      }
      return out;
    },
    // Returns a SINGLE SVG path-d string with one subpath per core.
    // Each subpath starts with `M` (the gapped-path builder already
    // emits `M ... L ...`), so concatenating them produces a valid
    // path with N disconnected polylines. Avoids the `<template x-for>`
    // inside SVG where Alpine 3.x's x-for scope doesn't always
    // establish the iteration variable cleanly (browser HTML parsers
    // don't treat `<template>` as a real template element when it's
    // inside the SVG namespace, which can leave the inner directive
    // evaluated against the parent scope where the iteration var is
    // undefined).
    snmpCpuPerCoreCombinedLine(hostId) {
      const lines = this.snmpCpuPerCoreLines(hostId);
      return lines && lines.length ? lines.join(' ') : '';
    },
    snmpCpuUsedPctLine(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      const vals = series.map(p => p.cpu_used_pct ?? 0);
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, 100, {times});
    },
    // Operator-flagged: SNMP Load chart should render as % of cores
    // rather than raw `load_1m=0.18` numbers. Converts each load
    // value to a percentage via cores resolved from the host (live
    // `host_cpu_per_core` length OR cores) — falls back to 1 (treat
    // as single-core) when cores is unknown so behaviour matches
    // the pre-conversion chart for hosts that don't expose cores.
    // 100 % cap so a busy 4-core box (load=8 → 200%) still fits the
    // chart without auto-rescaling the Y-axis.
    snmpCoresFor(hostId) {
      const h = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
      if (h) {
        const c = (h.host_cpu_per_core || []).length || h.cpu_cores || h.cores;
        if (c && c > 0) {
          return c;
        }
      }
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      for (const p of series) {
        const c = (p.cpu_per_core || []).length;
        if (c > 0) {
          return c;
        }
      }
      return 1;
    },
    snmpLoadLine(hostId, key) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      const cores = this.snmpCoresFor(hostId);
      const vals = series.map(p => Math.min(100, ((p[key] ?? 0) / cores) * 100));
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, 100, {times});
    },
    snmpLoadMax(_hostId) {
      // Always 100 % now — chart Y-axis stays fixed so a busy machine
      // doesn't auto-rescale every tick.
      return 100;
    },
    // Legend / live value as percent (0..100, capped). Operator wants
    // "12 %" not "0.18".
    snmpLoadPctLive(hostId, liveLoad) {
      const cores = this.snmpCoresFor(hostId);
      return Math.max(0, Math.min(100, ((+liveLoad || 0) / cores) * 100));
    },
    snmpMemArea(hostId, key) {
      // For the memory chart — render each layer as a polyline, scaled
      // against mem_total. `key` ∈ {used, buffers, cached, free}.
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (!series.length) {
        return '';
      }
      // Normalise against the largest mem_total seen (handles probes
      // pre/post a memory hot-add cleanly).
      let maxTotal = 0;
      for (const p of series) {
        maxTotal = Math.max(maxTotal, p.mem_total || 0);
      }
      if (!maxTotal) {
        return '';
      }
      const fieldKey = 'mem_' + key;
      const vals = series.map(p => p[fieldKey] || 0);
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, maxTotal, {times});
    },
    // derive per-tick throughput series in bytes/sec from the
    // cumulative IF-MIB ifHCInOctets / ifHCOutOctets samples. Skip-
    // don't-synthesize: out-of-bounds deltas (negative = counter
    // reset / reboot, near-zero timespan, hour-plus gap, absurd byte
    // delta) become 0 in the rendered series so a flat segment is
    // visibly distinct from a real "host idle" zero. First point is
    // always 0 because there's no predecessor to diff against. dir ∈
    // {'rx', 'tx'}.
    snmpThroughputBpsSeries(hostId, dir) {
      const entry = this.hostSnmpHistory[hostId] || {};
      const series = entry.points || [];
      if (series.length < 2) {
        return [];
      }
      const fieldKey = 'net_' + dir + '_total_bytes';
      // Dt cap scales with the server-side bucket cadence. Pre-fix a
      // hardcoded 3600s cap rejected every delta on 7d windows where
      // buckets are 5040s+ wide. Bumped to `bucket × 6` after operators
      // reported empty 7d charts on hosts where the SNMP sampler
      // intermittently fails (APC UPS / OPNsense-on-flaky-mgmt-LAN
      // pattern) — gaps of 3-5 missing buckets are normal and SHOULD
      // bridge cleanly. The 10 GB delta cap below still catches genuine
      // counter wraps / reboots. dtCap = max(7200, bucketS × 6) lets
      // 5-bucket gaps render as a continuous line; longer outages
      // (≥ 6 missing buckets) still break the polyline as a real gap.
      const bucketS = Number(entry.bucket_seconds) || 0;
      const dtCap = Math.max(7200, bucketS * 6);
      // Byte-delta cap also scales with `dt`. Pre-fix the static 10 GB
      // ceiling was tuned for 5-min raw cadence (~35 MB/s sustained per
      // delta) — comfortable for typical home-LAN. At 7d buckets (5040s)
      // 10 GB allows only ~16 Mbit/s sustained; anything busier got
      // filtered as if it were a counter wrap, leaving the chart empty
      // on hosts whose gateway pushes >100 Mbit/s during peak hours.
      // Cap is now `max(10 GB, dt × 125 MB/s)` — 125 MB/s = 1 Gbit/s
      // headroom per second of bucket width. Counter wrap (negative
      // delta or 2^64-scale jumps) is still caught by `db < 0` and the
      // generous-but-bounded ceiling.
      const _BYTE_RATE_CEILING = 125 * 1024 * 1024;          // 125 MB/s = 1 Gbit/s
      const _STATIC_BYTE_FLOOR = 10 * 1024 * 1024 * 1024;    // 10 GB preserves prior behaviour for raw cadence
      // skip-don't-synthesize: out-of-bounds deltas (counter
      // wrap, reboot, gap, null) emit `null` so `_snmpPolyPoints`
      // omits the point from the polyline. Pre-fix this filled with
      // 0 — visually identical to a real "0 bps idle" segment,
      // burying the wrap signal. First-sample slot stays null too
      // (no predecessor to diff against).
      const out = new Array(series.length).fill(null);
      for (let i = 1; i < series.length; i++) {
        const a = series[i - 1], b = series[i];
        const dt = (b.ts || 0) - (a.ts || 0);
        const av = a[fieldKey], bv = b[fieldKey];
        if (av == null || bv == null) {
          continue;
        }
        if (dt < 1 || dt > dtCap) {
          continue;
        }       // gap or doubled tick
        const db = bv - av;
        const byteCap = Math.max(_STATIC_BYTE_FLOOR, dt * _BYTE_RATE_CEILING);
        if (db < 0 || db > byteCap) {
          continue;
        }     // wrap / reboot / scaled cap
        out[i] = db / dt;
      }
      return out;
    },
    // APC UPS Output Load % over the picker window.
    // Renders the percentage of UPS capacity in use — e.g. 13% on a
    // 10 kVA Smart-UPS RT means the connected gear is drawing ~1.3 kVA.
    // Reads `load_percent` from `host_snmp_samples` rows; NULL slots
    // (host wasn't a UPS yet, or sample didn't include the OID) are
    // omitted via `_snmpPolyPoints`. Y-axis pinned to 100% so a busy
    // UPS doesn't auto-rescale.
    snmpUpsLoadLine(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (!series.length) {
        return '';
      }
      const vals = series.map(p => (p.load_percent != null ? +p.load_percent : null));
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, 100, {times});
    },
    // True when at least 2 samples have a non-null load_percent —
    // used internally by the chart-body template to switch between
    // the polyline and the "Collecting data" placeholder.
    snmpHasUpsLoadHistory(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      let n = 0;
      for (const p of series) {
        if (p.load_percent != null) {
          n++;
          if (n >= 2) {
            return true;
          }
        }
      }
      return false;
    },
    // True when the UPS Load card should RENDER. Returns true on
    // live host_load_percent OR ≥1 historical sample. Card body uses
    // `snmpHasUpsLoadHistory` to decide between polyline and the
    // "Collecting…" hint. Pre-fix follow-up the card hid until the
    // sampler accumulated 2 ticks (~10 min on default cadence) — long
    // enough for operators to assume nothing was being recorded.
    snmpHasUpsLoad(hostId) {
      const drawer = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
      if (drawer && typeof drawer.host_load_percent === 'number') {
        return true;
      }
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      for (const p of series) {
        if (p.load_percent != null) {
          return true;
        }
      }
      return false;
    },
    // APC UPS Battery % over the picker window. Same shape as the
    // load helper; pinned to 100%. Renders the discharge curve when
    // the UPS is on battery + the recharge curve afterwards.
    snmpUpsBatteryLine(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (!series.length) {
        return '';
      }
      const vals = series.map(p => (p.battery_percent != null ? +p.battery_percent : null));
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, 100, {times});
    },
    snmpHasUpsBatteryHistory(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      let n = 0;
      for (const p of series) {
        if (p.battery_percent != null) {
          n++;
          if (n >= 2) {
            return true;
          }
        }
      }
      return false;
    },
    snmpHasUpsBattery(hostId) {
      const drawer = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
      if (drawer && typeof drawer.host_battery_percent === 'number') {
        return true;
      }
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      for (const p of series) {
        if (p.battery_percent != null) {
          return true;
        }
      }
      return false;
    },
    // APC UPS Battery temperature (°C) over the picker window. Auto-
    // ranges so a flat-ish 36°C line still has vertical movement
    // — caller passes Math.max(50, observed_max) so a normal-range
    // host renders ~36 / 40 / 50 ticks instead of all-0..100.
    snmpUpsBatteryTempLine(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (!series.length) {
        return '';
      }
      const vals = series.map(p => (p.battery_temp_c != null ? +p.battery_temp_c : null));
      const times = series.map(p => p.ts);
      let m = 0;
      for (const v of vals) {
        if (v != null && v > m) {
          m = v;
        }
      }
      return this._snmpPathGapped(vals, Math.max(50, m), {times});
    },
    snmpUpsBatteryTempMax(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      let m = 0;
      for (const p of series) {
        const v = p.battery_temp_c;
        if (v != null && v > m) {
          m = v;
        }
      }
      return Math.max(50, m);
    },
    snmpHasUpsBatteryTempHistory(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      let n = 0;
      for (const p of series) {
        if (p.battery_temp_c != null) {
          n++;
          if (n >= 2) {
            return true;
          }
        }
      }
      return false;
    },
    snmpHasUpsBatteryTemp(hostId) {
      const drawer = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
      if (drawer && typeof drawer.host_battery_temp_c === 'number') {
        return true;
      }
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      for (const p of series) {
        if (p.battery_temp_c != null) {
          return true;
        }
      }
      return false;
    },
    // Dell server temperature-probe chart helpers.
    // Multi-line — one polyline per probe, sharing a single y-axis
    // (max across all probes) so spikes on Inlet vs Exhaust are
    // visually comparable. Each probe gets a distinct hue from a small
    // palette; the legend below the chart pairs name + last reading.
    dellTempProbes(hostId) {
      // Sorted probe metadata for the chart + legend. Returns an array
      // of `{idx, name, points, last_c, color}`. Two sources merge:
      // 1) hostSnmpTempHistory — persisted samples from the sampler
      //    (provides the time-series points for the chart)
      // 2) drawerHost.host_dell_temps — live probe payload from the
      //    most recent /api/hosts/one fetch (provides immediate
      //    readings while history accumulates)
      // The merge lets the chart slot render as soon as the drawer is
      // open — pre-fix the chart card sat at "Collecting data..." until
      // 2 sampler ticks (~10 min) had landed two persisted samples.
      const entry = this.hostSnmpTempHistory[hostId] || {};
      const probes = entry.probes || {};
      const drawer = (this.drawerHost && this.drawerHost.id === hostId)
        ? this.drawerHost : null;
      const liveTemps = drawer && Array.isArray(drawer.host_dell_temps)
        ? drawer.host_dell_temps : [];
      const palette = [
        'var(--info)', 'var(--warning)', 'var(--success)',
        'var(--danger)', 'var(--primary)', '#a78bfa',
        '#ec4899', '#06b6d4',
      ];
      // Build a unified probe map keyed by idx — history rows define
      // the probe set when present; live rows top up any probe that
      // history doesn't know about yet (first-tick scenario where the
      // sampler hasn't run but the drawer probe just landed).
      const merged = {};
      for (const idx of Object.keys(probes)) {
        merged[idx] = {
          idx, name: (probes[idx] || {}).name || `temp-${idx}`,
          points: Array.isArray((probes[idx] || {}).points)
            ? (probes[idx] || {}).points : []
        };
      }
      const nowTs = Math.floor(Date.now() / 1000);
      for (const t of liveTemps) {
        const idx = String(t.idx || '');
        if (!idx) {
          continue;
        }
        if (!merged[idx]) {
          merged[idx] = {idx, name: t.name || `temp-${idx}`, points: []};
        }
        // Append the live reading as a synthetic "now" sample only
        // when history is empty for this probe — otherwise the
        // sampler-persisted points are authoritative for time series.
        if (!merged[idx].points.length && t.celsius != null) {
          merged[idx].points = [{ts: nowTs, c: +t.celsius}];
        }
      }
      const idxs = Object.keys(merged).sort((a, b) => {
        const na = a.split('.').map(n => +n || 0);
        const nb = b.split('.').map(n => +n || 0);
        for (let k = 0; k < Math.max(na.length, nb.length); k++) {
          const da = na[k] || 0, db = nb[k] || 0;
          if (da !== db) {
            return da - db;
          }
        }
        return 0;
      });
      const out = [];
      let i = 0;
      for (const idx of idxs) {
        const p = merged[idx];
        const pts = p.points;
        let lastC = null;
        for (let j = pts.length - 1; j >= 0; j--) {
          if (pts[j].c != null) {
            lastC = pts[j].c;
            break;
          }
        }
        out.push({
          idx,
          name: p.name,
          points: pts,
          last_c: lastC,
          color: palette[i % palette.length],
        });
        i++;
      }
      return out;
    },
    dellTempMaxC(hostId) {
      let m = 0;
      const entry = this.hostSnmpTempHistory[hostId] || {};
      const probes = entry.probes || {};
      for (const idx of Object.keys(probes)) {
        const pts = (probes[idx] || {}).points || [];
        for (const pt of pts) {
          if (pt.c != null && pt.c > m) {
            m = pt.c;
          }
        }
      }
      // Also consider live drawer readings so the y-axis max stays
      // correct on first-tick scenarios where history is empty but
      // dellTempProbes synthesised single "now" points from the live
      // host_dell_temps payload.
      const drawer = (this.drawerHost && this.drawerHost.id === hostId)
        ? this.drawerHost : null;
      if (drawer && Array.isArray(drawer.host_dell_temps)) {
        for (const t of drawer.host_dell_temps) {
          if (t && t.celsius != null && +t.celsius > m) {
            m = +t.celsius;
          }
        }
      }
      // Domain floor — 60°C is a sensible upper bound for an
      // idle / lightly-loaded server so a flat 30-40°C line still has
      // visible vertical movement against the same axis as a loaded
      // host hitting 65-70°C.
      return Math.max(60, m);
    },
    dellTempLine(hostId, points) {
      // SVG path `d` for one probe's series, normalised against the
      // chart's shared y-max. The viewBox is 0 0 420 120 with
      // preserveAspectRatio="none", so x ∈ [0, 420] spans the full
      // chart width and y ∈ [0, 120] spans the full chart height.
      //
      // Single-point fallback: when only one valid sample exists,
      // _snmpPathGapped maps the synthetic "now" timestamp to the right
      // edge of the time domain — producing `M ~420,y` with no `L`
      // follow-up, which draws nothing AND a 4-pixel nub extension
      // would clip past the right edge. Instead, when the series has
      // exactly one valid point, render a full-width horizontal line
      // at the current temperature so the user sees an actual reading.
      // The full polyline replaces this once a second sample lands.
      if (!Array.isArray(points) || !points.length) {
        return '';
      }
      const validPts = points.filter(p => p && p.c != null);
      const max = this.dellTempMaxC(hostId);
      if (validPts.length === 1) {
        const c = +validPts[0].c;
        const y = Math.max(0, Math.min(120, 120 - (c / max) * 120));
        return `M 0,${y.toFixed(1)} L 420,${y.toFixed(1)}`;
      }
      const vals = points.map(p => (p && p.c != null ? +p.c : null));
      const times = points.map(p => p && p.ts);
      return this._snmpPathGapped(vals, max, {times});
    },
    dellHasTempHistory(hostId) {
      // Mount the chart slot whenever a probe has at least one sample
      // OR the live drawer payload has a `host_dell_temps` reading we
      // can synthesize a "now" point from (`dellTempProbes` does the
      // merge). dellTempLine handles single-point series by appending a
      // 4-pixel horizontal nub so the line is visible before history
      // accumulates the second point. Pre-fix this required ≥2 persisted
      // points per probe, leaving the "Collecting data..." placeholder
      // up for 5-10 minutes after deploy — the legend kept showing live
      // values while the chart slot stayed empty, which read as broken.
      const entry = this.hostSnmpTempHistory[hostId] || {};
      const probes = entry.probes || {};
      for (const idx of Object.keys(probes)) {
        if (((probes[idx] || {}).points || []).length >= 1) {
          return true;
        }
      }
      const drawer = (this.drawerHost && this.drawerHost.id === hostId)
        ? this.drawerHost : null;
      if (drawer && Array.isArray(drawer.host_dell_temps)) {
        for (const t of drawer.host_dell_temps) {
          if (t && t.celsius != null) {
            return true;
          }
        }
      }
      return false;
    },
    dellHasTemps(hostId) {
      // Card-render gate. True when the live drawer payload has
      // host_dell_temps OR the temp history has any rows. Mirrors the
      // UPS gate's "live OR history" predicate so the card stays
      // mounted when the live probe is briefly empty.
      const drawer = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
      if (drawer && Array.isArray(drawer.host_dell_temps) && drawer.host_dell_temps.length) {
        return true;
      }
      const entry = this.hostSnmpTempHistory[hostId] || {};
      const probes = entry.probes || {};
      return Object.keys(probes).length > 0;
    },
    snmpThroughputLine(hostId, dir) {
      const vals = this.snmpThroughputBpsSeries(hostId, dir);
      if (!vals.length) {
        return '';
      }
      const m = this.snmpThroughputMaxBps(hostId);
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      const times = series.map(p => p.ts);
      // Gap-aware path so wrap / reboot / gap nulls render as visual
      // breaks instead of straight-line bridges. Consumer must use
      // SVG <path :d> not <polyline :points>.
      return this._snmpPathGapped(vals, m || 1, {times});
    },
    snmpThroughputMaxBps(hostId) {
      const rx = this.snmpThroughputBpsSeries(hostId, 'rx');
      const tx = this.snmpThroughputBpsSeries(hostId, 'tx');
      let m = 0;
      for (const v of rx) {
        if (v > m) {
          m = v;
        }
      }
      for (const v of tx) {
        if (v > m) {
          m = v;
        }
      }
      return m;
    },
    snmpThroughputLast(hostId, dir) {
      const vals = this.snmpThroughputBpsSeries(hostId, dir);
      for (let i = vals.length - 1; i >= 0; i--) {
        if (vals[i] > 0) {
          return vals[i];
        }
      }
      return 0;
    },
    snmpHasThroughput(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      if (series.length < 2) {
        return false;
      }
      for (const p of series) {
        if (p.net_rx_total_bytes != null || p.net_tx_total_bytes != null) {
          return true;
        }
      }
      return false;
    },
    // printer pages-printed sparkline. Series of pages-per-day
    // rates derived from adjacent samples of `printer_page_count`
    // (Printer-MIB prtMarkerLifeCount, monotonic). Skip-don't-
    // synthesize on out-of-bounds deltas (negative = printer reset
    // / counter rollover, near-zero timespan, hour-plus gap, > 10 000
    // pages = absurd-rate guard against agent glitches).
    snmpPagesPerDaySeries(hostId) {
      const entry = this.hostSnmpHistory[hostId] || {};
      const series = entry.points || [];
      if (series.length < 2) {
        return [];
      }
      // Same bucket-aware dt cap as snmpThroughputBpsSeries — 7d windows
      // bucket to 5040s, blowing past a static 3600s cap and zeroing
      // the entire series.
      const bucketS = Number(entry.bucket_seconds) || 0;
      const dtCap = Math.max(3600, bucketS * 3);
      const out = new Array(series.length).fill(0);
      for (let i = 1; i < series.length; i++) {
        const a = series[i - 1], b = series[i];
        const dt = (b.ts || 0) - (a.ts || 0);
        const av = a.printer_page_count, bv = b.printer_page_count;
        if (av == null || bv == null) {
          continue;
        }
        if (dt < 1 || dt > dtCap) {
          continue;
        }
        const dp = bv - av;
        if (dp < 0 || dp > 10000) {
          continue;
        }
        out[i] = (dp / dt) * 86400;     // pages per day
      }
      return out;
    },
    snmpPagesPerDayLine(hostId) {
      const vals = this.snmpPagesPerDaySeries(hostId);
      if (!vals.length) {
        return '';
      }
      const m = Math.max(0.0001, ...vals);
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, m || 1, {times});
    },
    snmpPagesPerDayMax(hostId) {
      const vals = this.snmpPagesPerDaySeries(hostId);
      let m = 0;
      for (const v of vals) {
        if (v > m) {
          m = v;
        }
      }
      return m;
    },
    snmpPagesPerDayLast(hostId) {
      const vals = this.snmpPagesPerDaySeries(hostId);
      for (let i = vals.length - 1; i >= 0; i--) {
        if (vals[i] > 0) {
          return vals[i];
        }
      }
      return 0;
    },
    // Banner copy — derives the per-tick interval from the SAME tunable
    // the SNMP sampler uses. Resolution order :
    // 1. tuning_snmp_sample_interval_seconds when > 0 (SNMP runs at
    //    its own cadence, distinct from the global Beszel/NE one).
    // 2. tuning_stats_sample_interval_seconds (legacy / inherited
    //    cadence when the SNMP-specific knob is 0).
    // 3. 300s fallback when client_config hasn't hydrated.
    // Both knobs are surfaced via /api/me's client_config; the literal
    // `{minutes}` placeholder never reaches the rendered DOM.
    snmpWarmingUpText() {
      const cc = (this.me && this.me.client_config) || {};
      const snmpSec = +cc.snmp_sample_interval_seconds || 0;
      const statsSec = +cc.stats_sample_interval_seconds || 0;
      const sec = snmpSec > 0 ? snmpSec : (statsSec || 300);
      const minutes = Math.max(1, Math.round(sec / 60));
      let s = this.t('host_drawer.snmp_charts.warming_up', {minutes});
      // Defensive: if i18n's interpolation didn't substitute (older
      // browser-cached bundle, helper called pre-load, etc.) — replace
      // manually so the literal `{minutes}` placeholder never reaches
      // the operator's screen.
      if (typeof s === 'string' && s.indexOf('{minutes}') >= 0) {
        s = s.split('{minutes}').join(String(minutes));
      }
      return s;
    },
    // Legend value for SNMP load lines. Operator-flagged: reading
    // `last` showed 0.00 while the chart line clearly had a non-zero
    // peak (the most recent tick happened to be 0). Show MAX over the
    // window so the legend matches what the eye sees on the chart.
    snmpLoadLegendValue(hostId, key, liveValue) {
      const live = +liveValue;
      if (Number.isFinite(live) && live > 0) {
        return live.toFixed(2);
      }
      const stats = this.snmpStats(hostId, key);
      if (stats && stats.max > 0) {
        return stats.max.toFixed(2);
      }
      return (live || 0).toFixed(2);
    },
    // List of providers actually wired ON THIS HOST — drives the
    // "no data" banner so it only names providers the operator has
    // configured a target for on the specific row. Pre-fix the
    // banner read from the global `host_stats_source` CSV which
    // claimed Webmin/SNMP were checked even on hosts that had no
    // Webmin/SNMP target name set — confusing.
    enabledProvidersList(h) {
      if (!h) {
        return this.t('hosts_extra.no_data.no_providers') || 'any provider';
      }
      const out = [];
      if ((h.beszel_id || h.beszel_name || '').trim()) {
        out.push('Beszel');
      }
      if ((h.pulse_name || '').trim()) {
        out.push('Pulse');
      }
      if ((h.ne_url || '').trim()) {
        out.push('node-exporter');
      }
      if ((h.webmin_name || h.webmin_url || '').trim()) {
        out.push('Webmin');
      }
      if (h.snmp_enabled === true && (h.snmp_name || '').trim()) {
        out.push('SNMP');
      }
      if (h.ping_enabled === true) {
        out.push('Ping');
      }
      if (!out.length) {
        return this.t('hosts_extra.no_data.no_providers') || 'any provider';
      }
      if (out.length === 1) {
        return out[0];
      }
      if (out.length === 2) {
        return out[0] + ' or ' + out[1];
      }
      return out.slice(0, -1).join(', ') + ', or ' + out[out.length - 1];
    },
    snmpHasPageCount(hostId, h) {
      // Show the printer pages chart ONLY when the host looks like a
      // real printer — APC UPSes, switches and other non-printer SNMP
      // gear can occasionally answer OID 1.3.6.1.2.1.43.10.2.1.4.1.1
      // (prtMarkerLifeCount) with 0, which previously triggered the
      // chart card on a UPS. Gate on a printer signature: at least one
      // supply row OR a console message OR a NON-ZERO page count from
      // EITHER the live row OR history (DB-backed fast-path so the
      // card appears immediately on drawer open instead of waiting
      // for the 10-30s live SNMP probe to land).
      if (!h) {
        return false;
      }
      const supplies = h.printer_supplies || [];
      const hasSupplies = Array.isArray(supplies) && supplies.length > 0;
      const hasConsole = !!(h.printer_console_msg && String(h.printer_console_msg).trim());
      const hasNonZeroLive = (+h.printer_page_count || 0) > 0;
      const hasNonZeroHist = this.snmpLatestPageCount(hostId) > 0;
      if (!hasSupplies && !hasConsole && !hasNonZeroLive && !hasNonZeroHist) {
        return false;
      }
      if (h.printer_page_count != null) {
        return true;
      }
      return hasNonZeroHist;
    },
    // read the most-recent non-null `printer_page_count` from
    // the persisted SNMP history. Lets the Printer card surface a
    // lifetime page count immediately on drawer open from DB-backed
    // history instead of waiting for the live SNMP probe (10-30s
    // round trip). Falls back to 0 when no history exists.
    snmpLatestPageCount(hostId) {
      const series = (this.hostSnmpHistory[hostId] || {}).points || [];
      for (let i = series.length - 1; i >= 0; i--) {
        const v = series[i].printer_page_count;
        if (v != null && v > 0) {
          return v;
        }
      }
      return 0;
    },
    // Decide whether to show the "Cached" pill on the chart strip.
    // The pill claims the chart line is bounded by the last successful
    // sampler tick — so the gate must check the CHART data's actual
    // freshness, not the merged-host scalar stale state. Pre-fix the
    // pill was gated on `isStale(h)`, which fires whenever ANY merged
    // scalar field came from snapshot — including hosts where the
    // sampler was still actively writing chart points but a different
    // provider was down (e.g. Beszel sampler keeps writing, but NE
    // went down so `host_kernel` is from snapshot). User-flagged: chart
    // showing "Updated 25s ago" alongside "Cached" pill — confusing.
    //
    // True chart-stale signal: the newest sample timestamp in the
    // primary hostHistory cache is older than ~2× the sampler cadence.
    // Default sampler interval is 300s, so threshold 600s. Below that
    // → sampler is still writing → chart is live → pill HIDDEN.
    // Above → sampler has stopped → chart line is bounded by the last
    // tick → pill VISIBLE.
    //
    // Empty cache (no data at all) returns false — the chart's own
    // "Collecting data" placeholder handles that signal; the pill
    // would be redundant noise on a host that hasn't loaded data yet.
    isHostChartStale(h) {
      if (!h) {
        return false;
      }
      const key = this.hostHistoryKey ? this.hostHistoryKey(h) : '';
      if (!key) {
        return false;
      }
      const entry = this.hostHistory && this.hostHistory[key];
      const series = (entry && Array.isArray(entry.series)) ? entry.series : [];
      if (series.length < 2) {
        return false;
      }
      const last = series[series.length - 1];
      const newestSec = Number((last && (last.t || last.ts)) || 0);
      if (!newestSec) {
        return false;
      }
      // `hostHistoryNow` is the same 30s-ticked timer the freshness
      // label reads — touching it here makes Alpine re-evaluate the
      // gate on every tick so the pill flips on/off without operator
      // action when the sampler stalls.
      const nowMs = this.hostHistoryNow || Date.now();
      const ageS = Math.max(0, (nowMs / 1000) - newestSec);
      return ageS > 600;
    },
    async resumeHostSampling(h) {
      if (!h || !h.id || h._resumeBusy) {
        return;
      }
      h._resumeBusy = true;
      // Safety timer — mirrors per-provider safety. Even if
      // `await fetch` hangs forever (browser network freeze, broken
      // proxy holding the connection), the button gets re-enabled
      // after 30s. Prevents the stuck-disabled state operators hit
      // when the page came back from a network blip with the busy
      // flag still set.
      const safetyTimer = setTimeout(() => {
        h._resumeBusy = false;
      }, 30000);
      try {
        const r = await fetch('/api/hosts/' + encodeURIComponent(h.id) + '/resume-sampling', {
          method: 'POST',
        });
        if (r.ok) {
          // Optimistic: clear the marker locally so the banner /
          // table icon disappear before the next host-list poll.
          h.sampling_paused = false;
          h.failure_window_started_at = 0;
          h.consecutive_failures = 0;
          h.last_error = '';
          this.showToast(this.t('hosts_extra.permanent_fail.resumed_toast', {host: this.hostDisplayName(h) || h.id}), 'success');
          // — whole-host pause supersedes per-provider pause. When
          // the operator clicks Resume on the whole-host banner, also
          // walk the paused-providers set on this host and clear each
          // (parallel via resumeAllProviders). One click clears every
          // pause layer for the host — pre-fix the operator had to click
          // both Resume sampling AND Resume all to fully recover.
          const stillPaused = this.pausedProvidersFor(h);
          if (stillPaused.length > 0) {
            this.resumeAllProviders(h).catch(() => {
            });
          }
          // Refresh the host record to pick up backend's view + any
          // new probe results that landed during the API roundtrip.
          // ``force: true`` busts the 10s provider-state cache so the
          // operator sees the post-resume probe immediately instead
          // of waiting out the TTL.
          if (typeof this.refreshHostRow === 'function') {
            this.refreshHostRow(h.id, {force: true}).catch(() => {
            });
          }
        } else {
          const j = await r.json().catch(() => ({}));
          const detail = j.detail || ('HTTP ' + r.status);
          this.showToast(this.t('hosts_extra.permanent_fail.resume_failed_toast', {host: this.hostDisplayName(h) || h.id, error: detail}), 'error');
        }
      } catch (err) {
        this.showToast(this.t('hosts_extra.permanent_fail.resume_failed_toast', {host: this.hostDisplayName(h) || h.id, error: String(err)}), 'error');
      } finally {
        clearTimeout(safetyTimer);
        h._resumeBusy = false;
      }
    },

    // Tap-driven tooltip state for the chart `?` icons. Holds
    // a `<host_id>:<metric_key>` string when a tooltip is open, null
    // when nothing is showing. Mobile lacks hover so the native :title
    // never fires; this Alpine state powers a click-to-toggle tooltip
    // body that ALSO works on desktop. ESC + click-outside close.
    metricTooltipOpen: null,
    toggleMetricTooltip(h, key) {
      const slot = (h && h.id ? h.id : '') + ':' + key;
      this.metricTooltipOpen = (this.metricTooltipOpen === slot) ? null : slot;
      // After Alpine renders the visible tooltip, smart-place it so a
      // left-column chart card doesn't crop the body off the drawer's
      // start edge. The helper measures and applies an --align-start
      // or --align-center modifier when the default end-anchor would
      // overflow.
      if (this.metricTooltipOpen) {
        this.$nextTick(() => this._adjustMetricTooltipPlacement());
      }
    },
    metricTooltipKey(h, key) {
      return (h && h.id ? h.id : '') + ':' + key;
    },
    _adjustMetricTooltipPlacement() {
      // Find the just-opened tooltip body. Alpine renders x-show via
      // display:none, so the visible one is whichever has computed
      // display !== 'none'.
      const all = document.querySelectorAll('.metric-source-tooltip');
      for (const el of all) {
        // Reset any previous modifier so the measurement reflects the
        // default end-anchored placement, then re-apply if needed.
        el.classList.remove('metric-source-tooltip--align-start');
        el.classList.remove('metric-source-tooltip--align-center');
        if (getComputedStyle(el).display === 'none') {
          continue;
        }
        // Use the host drawer as the clipping reference when present;
        // fall back to the viewport. 8px breathing room on each side.
        const drawer = el.closest('.host-drawer');
        const bounds = drawer ? drawer.getBoundingClientRect() : {left: 0, right: window.innerWidth};
        const PAD = 8;
        let rect = el.getBoundingClientRect();
        if (rect.left < bounds.left + PAD) {
          // Overflowing the start edge — flip to start-anchored.
          el.classList.add('metric-source-tooltip--align-start');
          rect = el.getBoundingClientRect();
          if (rect.right > bounds.right - PAD) {
            // Flipped overflow on the opposite side too — centre it as
            // a final fallback (rare; only on very narrow drawers).
            el.classList.remove('metric-source-tooltip--align-start');
            el.classList.add('metric-source-tooltip--align-center');
          }
        }
      }
    },
    // Pick up to 5 evenly-spaced timestamps from the series and format
    // them as HH:MM strings for the X-axis below the chart.
    //
    // — Switched from "evenly-spaced points across the actual
    // sample series" to "evenly-spaced ticks across the drawer's
    // unified [tMin, tMax] window". Pre-fix a chart with 4 sparse
    // samples got 4 axis labels equal to those sample times; a chart
    // with 60 dense samples got 5 labels evenly spaced through them.
    // Two cards next to each other showed different label times for
    // the same horizontal pixel — making "where was my spike" hard to
    // read across providers. Post-fix every chart's axis labels are
    // [tMin, …, tMax] so the same pixel position means the same
    // wall-clock time across every drawer chart.
    // Tick-count resolver for host-drawer charts. Operator-flagged:
    // 6h should show 6 ticks (one per hour), 7d should show 7 ticks
    // (one per day). Pre-fix every chart used a hardcoded `slots=5`
    // regardless of range. The map below pairs each picker range
    // with a tick count that lines up with the unit-time interval:
    //   1h  → 6 ticks (one per ~10 min)
    //   6h  → 6 ticks (one per hour)
    //   24h → 6 ticks (one per 4 hours)
    //   7d  → 7 ticks (one per day)
    // Any future range (or call site that explicitly overrides the
    // default) falls back to the passed value.
    _hostChartTickCount(rangeHours) {
      const r = Number(rangeHours || this.hostHistoryRange) || 1;
      if (r === 1) {
        return 6;
      }
      if (r === 6) {
        return 6;
      }
      if (r === 24) {
        return 6;
      }
      if (r === 168) {
        return 7;
      }
      return 5;
    },
    // "Is this net-series flat at zero?" — used by the Net In / Net
    // Out cards to swap the chart for an actionable hint when Beszel's
    // agent isn't tracking any NIC (the agent needs NICS=<iface> env
    // set before it emits nr/ns numbers). Distinct from "no data yet"
    // because hostHistory[].series IS populated — every point is 0.
    isNetSeriesFlat(systemId, key) {
      const stats = this.hostMetricStats(systemId, key, false);
      return !!(stats && stats.maxRaw === 0);
    },
    memPercentOf(h) {
      if (!h) {
        return 0;
      }
      // Same unification rule as `diskPercentOf` — prefer the
      // backend's recomputed `host_mem_percent` (derived from merged
      // used/total in `_merge_one_host`) so the drawer chart, drawer
      // total-usage label, and the outside host card ALL bind to the
      // same number. Operator-flagged: outside read 7% while inside
      // chart read 6.6-6.7% — same data, two different numbers.
      // Falls back to live computation when the backend value is
      // missing; renders with 1-decimal precision so 6.7% reads as
      // "6.7%" instead of either "7" (round-up) or "6.65812..." (raw).
      if (h.mem_percent !== undefined && h.mem_percent !== null
        && Number.isFinite(Number(h.mem_percent))) {
        return Number(h.mem_percent);
      }
      if (!h.mem_total) {
        return 0;
      }
      return Math.round((h.mem_used / h.mem_total) * 1000) / 10;
    },
    diskPercentOf(h) {
      if (!h) {
        return 0;
      }
      // Prefer the backend's recomputed `host_disk_percent` (derived
      // from merged used/total in `_merge_one_host`) so the drawer
      // chart, drawer Total-usage label, and the outside host card
      // ALL bind to the same number. Pre-fix the drawer used
      // `Math.round(used/total * 100)` (integer) while the outside
      // used `host_disk_percent` (1 decimal) — same data, different
      // numbers (9.0% vs 8.7%). Fall back to live computation when
      // the backend value is missing.
      if (h.disk_percent !== undefined && h.disk_percent !== null
        && Number.isFinite(Number(h.disk_percent))) {
        return Number(h.disk_percent);
      }
      if (!h.disk_total) {
        return 0;
      }
      return Math.round((h.disk_used / h.disk_total) * 1000) / 10;
    },
    // Worst-axis-wins: returns the lowest sub-score across all valid
    // axes, or null if no axis is computable for this host.
    healthScore(h) {
      const axes = this.healthAxes(h);
      if (!axes.length) {
        return null;
      }
      let worst = 100;
      for (const a of axes) {
        if (a.score != null && a.score < worst) {
          worst = a.score;
        }
      }
      return worst;
    },
    // Returns the single lowest-scoring axis — populates the chip
    // tooltip + the breakdown popover's "Worst axis" callout.
    healthWorstAxis(h) {
      const axes = this.healthAxes(h);
      if (!axes.length) {
        return null;
      }
      let worst = null;
      for (const a of axes) {
        if (a.score == null) {
          continue;
        }
        if (worst == null || a.score < worst.score) {
          worst = a;
        }
      }
      return worst;
    },
    // Threshold tier for the chip background colour. 80+ green,
    // 50-79 amber, <50 red. Aligns with operator's "anything <80
    // gets attention" mental model — anything green is healthy,
    // amber is "look later", red is "look now".
    healthChipClass(score) {
      if (score == null) {
        return '';
      }
      if (score < 50) {
        return 'health-chip-bad';
      }
      if (score < 80) {
        return 'health-chip-warn';
      }
      return 'health-chip-ok';
    },
    // Toggle the breakdown popover open at host-drawer scope. Click
    // the chip in the drawer header → flips the panel; click again
    // (or close drawer) closes. State stays on the app() instance
    // because the popover lives inside the drawer template tree.
    toggleHealthPopover() {
      this.healthPopoverOpen = !this.healthPopoverOpen;
    },
    // ===== HOST TIMELINE =============================================
    // Triage view inside the drawer aggregating ops, notifications,
    // provider auto-pause + recovery markers per host. Backed by
    // GET /api/hosts/{id}/timeline?hours=N. Cached per-host with
    // a 30s TTL (faster invalidation when the operator clicks
    // refresh or changes the range). Reads + writes
    // `this.hostTimeline[hid]` and `this.hostTimelineRange[hid]`.
    toggleHostTimeline(hostId) {
      const id = (hostId || '').toString();
      if (!id) {
        return;
      }
      const wasOpen = !!this.timelineExpanded[id];
      this.timelineExpanded[id] = !wasOpen;
      // First open → kick off the fetch. Subsequent opens use the
      // cache unless it's stale.
      if (!wasOpen) {
        const cache = this.hostTimeline[id];
        const now = Date.now();
        const stale = !cache
          || !cache.loadedAt
          || (now - cache.loadedAt) > 30000;
        if (stale) {
          this.loadHostTimeline(id, false);
        }
        // Triage panel rides the same expand state — fetch it
        // alongside the timeline so the operator doesn't see a
        // half-rendered drawer when the timeline lands first.
        const tcache = this.hostTriage[id];
        const tstale = !tcache
          || !tcache.loadedAt
          || (now - tcache.loadedAt) > 30000;
        if (tstale) {
          this.loadHostTriage(id);
        }
      }
    },
    isTriageGroupExpanded(hostId, groupIdx) {
      const map = this.triageExpanded[hostId];
      return !!(map && map[groupIdx]);
    },
    toggleTriageGroup(hostId, groupIdx) {
      const id = (hostId || '').toString();
      if (!id) {
        return;
      }
      if (!this.triageExpanded[id]) {
        this.triageExpanded[id] = {};
      }
      this.triageExpanded[id][groupIdx] = !this.triageExpanded[id][groupIdx];
    },
    triagePatternLabel(pattern) {
      const p = (pattern || 'other').toString();
      const key = 'host_drawer.triage.pattern.' + p.replace(/-/g, '_');
      const tr = this.t(key);
      if (tr && tr !== key) {
        return tr;
      }
      // Forward-compat: humanise the enum so a future pattern shows
      // up readable until the i18n key catches up.
      return p.replace(/-/g, ' ').replace(/^\w/, c => c.toUpperCase());
    },
    triagePatternChipClass(pattern) {
      switch ((pattern || '').toString()) {
        case 'auth':
          return 'pill-error';
        case 'tls':
          return 'pill-error';
        case 'timeout':
          return 'pill-warning';
        case 'refused':
          return 'pill-error';
        case 'dns':
          return 'pill-warning';
        case 'network':
          return 'pill-warning';
        case 'server-error':
          return 'pill-error';
        case 'rate-limit':
          return 'pill-warning';
        case 'not-found':
          return 'pill-info';
        case 'parse':
          return 'pill-info';
        default:
          return 'pill-muted';
      }
    },
    triageAvgRecoveryLabel(seconds) {
      const n = Number(seconds);
      if (!Number.isFinite(n) || n <= 0) {
        return '—';
      }
      if (n < 60) {
        return Math.round(n) + 's';
      }
      if (n < 3600) {
        return Math.round(n / 60) + 'm';
      }
      if (n < 86400) {
        return Math.round(n / 3600) + 'h';
      }
      return Math.round(n / 86400) + 'd';
    },
    // ===== HOSTS BULK SELECTION ======================================
    // Selection helpers. The Hosts main view's row checkbox stops
    // propagation so click on the row body still opens the drawer
    // but click on the checkbox only toggles selection.
    isHostSelected(hostId) {
      return this.selectedHosts.has((hostId || '').toString());
    },
    toggleHostSelection(hostId) {
      const id = (hostId || '').toString();
      if (!id) {
        return;
      }
      if (this.selectedHosts.has(id)) {
        this.selectedHosts.delete(id);
      } else {
        this.selectedHosts.add(id);
      }
      // Re-assign so Alpine sees the mutation (Set mutations don't
      // trigger reactivity by themselves).
      this.selectedHosts = new Set(this.selectedHosts);
    },
    clearHostSelection() {
      this.selectedHosts = new Set();
    },
    selectedHostCount() {
      return this.selectedHosts.size;
    },
    selectedHostsArray() {
      return Array.from(this.selectedHosts);
    },
    // Select-all-visible (filtered by current Hosts toolbar state).
    selectAllVisibleHosts() {
      const ids = (this.filteredHosts() || [])
        .map(h => h && h.id)
        .filter(Boolean)
        .map(String);
      this.selectedHosts = new Set(ids);
    },
    // ===== HOSTS BULK ACTIONS ========================================
    // Each action POSTs to a `/api/hosts/bulk/<action>` endpoint and
    // surfaces the partial-success response via the existing toast
    // helpers. Selection is preserved on success so the operator can
    // chain actions; cleared on operator click of "Clear".
    // Step-up reauth — prompts the operator for their local password,
    // POSTs to /api/admin/reauth, returns the short-lived token. SSO
    // users (no local password) get a `OG_REAUTH_NO_LOCAL_PASSWORD`
    // response; the caller falls back to a typed-count confirm in
    // that case. Cancel returns null.
    async _mintReauthToken() {
      try {
        const result = await Swal.fire({
          title: this.t('reauth.title') || 'Confirm with your password',
          text: this.t('reauth.text')
            || 'This action affects multiple hosts. Re-enter your password to proceed.',
          input: 'password',
          inputAttributes: {
            autocapitalize: 'off',
            autocomplete: 'current-password',
          },
          inputPlaceholder: this.t('reauth.placeholder') || 'Password',
          showCancelButton: true,
          confirmButtonText: this.t('reauth.confirm') || 'Confirm',
          cancelButtonText: this.t('actions.cancel') || 'Cancel',
        });
        if (!result.isConfirmed) {
          return null;
        }
        const pw = (result.value || '').trim();
        if (!pw) {
          return null;
        }
        const r = await fetch('/api/admin/reauth', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          credentials: 'same-origin',
          body: JSON.stringify({password: pw}),
        });
        const j = await r.json().catch(() => ({}));
        if (j && j.error_code === 'OG_REAUTH_NO_LOCAL_PASSWORD') {
          // SSO user — backend bypasses the reauth gate, so an empty
          // string is the agreed signal "skip the header".
          return '';
        }
        if (!r.ok || !j.ok) {
          this.showToast(this.t('reauth.failed') || 'Re-authentication failed.', 'error');
          return null;
        }
        return String(j.token || '');
      } catch {
        return null;
      }
    },
    async bulkPauseHosts(opts) {
      if (this.selectedHostCount() === 0) {
        return;
      }
      const skipConfirm = !!(opts && opts.skipConfirm);
      // SweetAlert confirm — destructive (sampler will skip these
      // hosts until manually resumed). Body shows the actual host
      // names so the operator can verify the selection before
      // committing — for >10 hosts the list is truncated to the first
      // 10 + "...and N more" so a 200-host pause confirm doesn't fill
      // the entire screen with hostnames. The AI sidebar inline-
      // confirm path passes skipConfirm=true so the SwAl is bypassed
      // (the operator approved inline; a second popup defeats the
      // no-popup contract).
      if (!skipConfirm) {
        const ids = this.selectedHostsArray();
        const sample = ids.slice(0, 10);
        const more = ids.length - sample.length;
        const sampleHtml = sample.map(id => '<code>' + this._logEscape(id) + '</code>').join(', ');
        const moreHtml = more > 0
          ? ' ' + (this.t('hosts_extra.bulk.pause_confirm_more', {more}) || ('… and ' + more + ' more'))
          : '';
        try {
          const result = await Swal.fire({
            title: this.t('hosts_extra.bulk.pause_confirm_title') || 'Pause sampling?',
            html: (this.t('hosts_extra.bulk.pause_confirm_body', {count: this.selectedHostCount()})
                || ('Pause sampling on ' + this.selectedHostCount() + ' host(s)?'))
              + '<br><br><div class="text-[11.5px] text-[var(--text-dim)] mono break-words">' + sampleHtml + moreHtml + '</div>',
            icon: 'warning',
            showCancelButton: true,
            confirmButtonText: this.t('hosts_extra.bulk.pause_confirm_ok') || 'Pause',
            cancelButtonText: this.t('actions.cancel') || 'Cancel',
          });
          if (!result.isConfirmed) {
            return;
          }
        } catch {
          return;
        }
      }
      // Step-up reauth — bulk pause is the most destructive bulk
      // action (sampler stops probing every selected host until
      // manually resumed), so the backend gates it behind a short-
      // lived reauth token. Mint one now via `/api/admin/reauth`.
      // Cancel / wrong password / network error = abort silently
      // (the SweetAlert toast already explained the failure).
      const reauthToken = await this._mintReauthToken();
      if (reauthToken === null) {
        return;
      }
      await this._hostsBulkPost(
        'pause', null, 'hosts_extra.bulk.pause_success',
        {reauthToken},
      );
    },
    async bulkResumeHosts(opts) {
      // Resume is non-destructive (re-enables sampler probes) — no
      // inner SwAl to bypass — but accept opts.skipConfirm for API
      // symmetry with bulkPauseHosts. Currently a no-op.
      void opts;
      if (this.selectedHostCount() === 0) {
        return;
      }
      await this._hostsBulkPost('resume', null, 'hosts_extra.bulk.resume_success');
    },
    openBulkSnmpVendorsModal() {
      if (this.selectedHostCount() === 0) {
        return;
      }
      this.bulkSnmpVendorsModal = {open: true, vendors: [], mode: 'set'};
    },
    closeBulkSnmpVendorsModal() {
      this.bulkSnmpVendorsModal = {open: false, vendors: [], mode: 'set'};
    },
    toggleBulkVendor(v) {
      const cur = this.bulkSnmpVendorsModal.vendors || [];
      const idx = cur.indexOf(v);
      if (idx >= 0) {
        cur.splice(idx, 1);
      } else {
        cur.push(v);
      }
      this.bulkSnmpVendorsModal = {...this.bulkSnmpVendorsModal, vendors: [...cur]};
    },
    async submitBulkSnmpVendors() {
      const m = this.bulkSnmpVendorsModal || {};
      await this._hostsBulkPost(
        'snmp_vendors',
        {vendors: m.vendors || [], mode: m.mode || 'set'},
        'hosts_extra.bulk.snmp_vendors_success',
      );
      this.closeBulkSnmpVendorsModal();
    },
    openBulkSnmpTunablesModal() {
      if (this.selectedHostCount() === 0) {
        return;
      }
      this.bulkSnmpTunablesModal = {
        open: true,
        walk_concurrency: '',
        wall_clock_budget: '',
        clear: false,
      };
    },
    closeBulkSnmpTunablesModal() {
      this.bulkSnmpTunablesModal = {
        open: false,
        walk_concurrency: '',
        wall_clock_budget: '',
        clear: false,
      };
    },
    async submitBulkSnmpTunables() {
      const m = this.bulkSnmpTunablesModal || {};
      const payload = {clear: !!m.clear};
      if (!m.clear) {
        const wc = parseInt(m.walk_concurrency, 10);
        if (Number.isFinite(wc)) {
          payload.walk_concurrency = wc;
        }
        const wcb = parseInt(m.wall_clock_budget, 10);
        if (Number.isFinite(wcb)) {
          payload.wall_clock_budget = wcb;
        }
        if (payload.walk_concurrency == null && payload.wall_clock_budget == null) {
          this.showToast(
            this.t('hosts_extra.bulk.snmp_tunables_empty')
            || 'Set at least one tunable or enable Clear',
            'warning',
          );
          return;
        }
      }
      await this._hostsBulkPost(
        'snmp_tunables',
        payload,
        'hosts_extra.bulk.snmp_tunables_success',
      );
      this.closeBulkSnmpTunablesModal();
    },
    // Threshold tier for a single mount's fill — drives the segmented
    // disk bar's per-segment colour (kept for callers; row no longer
    // uses the segmented variant). Mirrors `barLevel(pct)` so a
    // full mount visually screams the same way a single-mount bar
    // would. Empty / unknown returns 'ok' (green) so the bar reads
    // as healthy at rest.
    mountFillLevel(m) {
      const pct = this.mountFillPercent(m);
      if (pct > this._statBarCritPct()) {
        return 'crit';
      }
      if (pct > this._statBarWarnPct()) {
        return 'warn';
      }
      return 'ok';
    },
    mountFillPercent(m) {
      if (!m) {
        return 0;
      }
      const dp = Number(m.dp);
      if (Number.isFinite(dp) && dp > 0) {
        return Math.min(100, Math.max(0, dp));
      }
      const size = Number(m.d) || 0;
      const used = Number(m.du) || 0;
      if (size <= 0) {
        return 0;
      }
      return Math.min(100, Math.max(0, (used / size) * 100));
    },
    // Green → amber → red by threshold, matching how nodeStats renders.
    pctColor(pct) {
      if (pct >= 85) {
        return 'var(--danger)';
      }
      if (pct >= 60) {
        return 'var(--warning)';
      }
      return 'var(--success)';
    },
    statusDotColor(status) {
      if (status === 'up') {
        return 'var(--success)';
      }
      if (status === 'down' || status === 'unreachable') {
        return 'var(--danger)';
      }
      if (status === 'paused') {
        return 'var(--warning)';
      }
      // Grey dots — no signal (yet) worth alerting on:
      // 'loading'      — skeleton state, probe hasn't returned
      // 'unconfigured' — curated row has NO provider fields set,
      //                  so there's literally nothing to probe
      if (status === 'loading' || status === 'unconfigured') {
        return 'var(--text-faint)';
      }
      // 'unknown' — providers ARE mapped but none returned data.
      // Red because this IS a real failure to reach the host.
      if (status === 'unknown') {
        return 'var(--danger)';
      }
      return 'var(--text-faint)';
    },

    // Stacks/Services row status indicator — drives the small dot
    // before the row icon. Returns one of `is-up` (green, all healthy),
    // `is-degraded` (amber, has updates pending OR partial degraded),
    // `is-down` (red, any item offline / errored), or `is-unknown`
    // (grey, nothing actionable). The corresponding spinner state is
    // gated on `statsLoaded` — until the first /api/stats response
    // lands, the row renders a spinner instead of a dot so operators
    // see the "still fetching data in background" affordance the user
    // requested. Once stats are in, the dot reflects the rolled-up
    // state of the stack's items.
    stackStatusDotClass(stack) {
      if (!stack) {
        return 'is-unknown';
      }
      if ((stack.offline || 0) > 0) {
        return 'is-down';
      }
      if ((stack.errors || 0) > 0) {
        return 'is-down';
      }
      if ((stack.degraded || 0) > 0 || (stack.updates || 0) > 0) {
        return 'is-degraded';
      }
      if ((stack.unknowns || 0) > 0) {
        return 'is-unknown';
      }
      if ((stack.uptodate || 0) > 0 || (stack.total || 0) > 0) {
        return 'is-up';
      }
      return 'is-unknown';
    },
    // Per-item dot — used by the Services view's leading column.
    // Mirrors the stack helper's tiers off item.status / item.health
    // so the same colour family lights up across both views.
    itemStatusDotClass(item) {
      if (!item) {
        return 'is-unknown';
      }
      const status = String(item.status || '').toLowerCase();
      const health = String(item.health || '').toLowerCase();
      if (status === 'error' || health === 'offline') {
        return 'is-down';
      }
      if (status === 'update' || health === 'degraded') {
        return 'is-degraded';
      }
      if (status === 'up-to-date' && health === 'healthy') {
        return 'is-up';
      }
      if (status === 'unknown' || !status) {
        return 'is-unknown';
      }
      return 'is-unknown';
    },

    // hover-title for the host status dot. Surfaces the probe
    // wall-clock so operators can tell whether an `unknown` status
    // came from a fast 5xx or a slow 30s hang. Backend stamps
    // `_probe_elapsed_ms` on every `/api/hosts/one/{id}` response;
    // missing on the legacy `/api/hosts` path (returns empty string).
    hostProbeTitle(h) {
      if (!h || typeof h._probe_elapsed_ms !== 'number') {
        return '';
      }
      const ms = h._probe_elapsed_ms;
      const status = h.status || '';
      const human = ms < 1000
        ? `${ms} ms`
        : `${(ms / 1000).toFixed(1)} s`;
      return this.t('hosts_extra.probe_title', {elapsed: human, status}) || `Probe took ${human}`;
    },

    // --- Node drawer (Nodes view → click a node) ---
    openNodeDrawer(node) {
      // Seed the drawer with the currently stored alias (if any) so the
      // input is pre-populated with whatever's in the DB. The identity
      // default (node.name) is shown as a placeholder, not a value, so
      // saving an empty string clears the mapping.
      const current = (this.settings.beszel_aliases || {})[node.name] || '';
      this.drawerNode = {name: node.name, aliasInput: current};
    },
    async saveNodeBeszelMapping() {
      if (!this.drawerNode) {
        return;
      }
      const name = this.drawerNode.name;
      const val = (this.drawerNode.aliasInput || '').trim();
      // Merge into the existing map: blank = delete entry, otherwise set.
      const map = {...(this.settings.beszel_aliases || {})};
      if (val) {
        map[name] = val;
      } else {
        delete map[name];
      }
      this.drawerNodeSaving = true;
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({beszel_aliases: map}),
        });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          throw new Error(d.detail || `HTTP ${r.status}`);
        }
        this.settings.beszel_aliases = map;
        this.showToast(
          val
            ? `Beszel mapping saved: ${name} → ${val}`
            : `Beszel mapping cleared for ${name}`,
          'success',
        );
        this.drawerNode = null;
        // Force the next gather to re-read the alias map and re-match
        // Beszel systems. Without force=true the cached items stay
        // attached to the old alias resolution until CACHE_TTL lapses.
        await this.refresh(true);
      } catch (e) {
        this.showToast(`Save failed: ${e.message}`, 'error');
      } finally {
        this.drawerNodeSaving = false;
      }
    },
    parseEvents(j) {
      try {
        return JSON.parse(j || '[]');
      } catch (_) {
        return [];
      }
    },
    // formatTime + formatTimeShort are kept for backwards compatibility
    // with any template bindings — both now delegate to the unified
    // fmt* helpers so every date in the UI renders in dd/mm/yyyy format.
    formatTime(ts) {
      return this.fmtDate(ts);
    },
    formatTimeShort(ts) {
      if (!ts) {
        return '—';
      }
      const d = new Date(ts * 1000);
      if (isNaN(d.getTime())) {
        return '—';
      }
      const pad = n => String(n).padStart(2, '0');
      return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    },
    // Parse a user-format date string (matching `_userDateOnlyFormat`)
    // back to an ISO `YYYY-MM-DD` string for the history-filter state.
    // Returns null when the input doesn't match the expected token shape;
    // empty input returns the empty string (clears the filter). Tokens
    // supported: yyyy / yy / MMMM / MMM / MM / M / dd / d.
    _parseUserDate(text) {
      const raw = (text || '').toString().trim();
      if (!raw) {
        return '';
      }
      const fmt = this._userDateOnlyFormat();
      const monthsLong = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
      const monthsShort = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
      // Build a regex from the format string. Order matters — match
      // longer tokens first so `MM` doesn't get split into two `M`s.
      const tokenOrder = ['yyyy', 'yy', 'MMMM', 'MMM', 'MM', 'M', 'dd', 'd'];
      const tokenPatterns = {
        yyyy: '(?<y>\\d{4})',
        yy: '(?<y>\\d{2})',
        MMMM: '(?<mn>[A-Za-z]+)',
        MMM: '(?<mn>[A-Za-z]{3})',
        MM: '(?<m>\\d{2})',
        M: '(?<m>\\d{1,2})',
        dd: '(?<d>\\d{2})',
        d: '(?<d>\\d{1,2})',
      };
      // Escape non-token characters, then replace tokens with capture groups.
      // Walk the format left-to-right matching the longest token at each
      // position to avoid `M` consuming the start of `MMMM`.
      let pattern = '';
      let i = 0;
      while (i < fmt.length) {
        let matched = false;
        for (const tk of tokenOrder) {
          if (fmt.slice(i, i + tk.length) === tk) {
            pattern += tokenPatterns[tk];
            i += tk.length;
            matched = true;
            break;
          }
        }
        if (!matched) {
          pattern += fmt[i].replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          i += 1;
        }
      }
      let m;
      try {
        m = new RegExp('^' + pattern + '$').exec(raw);
      } catch (_) {
        return null;
      }
      if (!m) {
        return null;
      }
      const g = m.groups || {};
      let y = parseInt(g.y || '', 10);
      let mo = parseInt(g.m || '', 10);
      const day = parseInt(g.d || '', 10);
      if (g.mn) {
        const lower = g.mn.toLowerCase();
        const li = monthsLong.findIndex(x => x.toLowerCase() === lower);
        const si = monthsShort.findIndex(x => x.toLowerCase() === lower);
        mo = (li >= 0 ? li + 1 : (si >= 0 ? si + 1 : NaN));
      }
      if (!Number.isFinite(y) || !Number.isFinite(mo) || !Number.isFinite(day)) {
        return null;
      }
      if (y < 100) {
        y += 2000;
      }  // yy → 20yy
      if (mo < 1 || mo > 12 || day < 1 || day > 31) {
        return null;
      }
      return `${y.toString().padStart(4, '0')}-${mo.toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`;
    },
    // History-filter `@change` glue. Parses the operator-typed text via
    // `_parseUserDate`, writes the ISO string into `historyFilters.<key>`,
    // and triggers the existing `historyApplyFilter` debounce. Empty
    // input clears the filter; malformed input keeps the field's old
    // ISO value (so the filter doesn't silently drop on a typo) but the
    // text input gets re-rendered with the canonical format.
    setHistoryDateFromText(which, text) {
      const parsed = this._parseUserDate(text);
      if (parsed === null) {
        // Malformed — leave underlying ISO untouched. The render will
        // re-display the existing parsed value so the input snaps back.
        return;
      }
      if (!this.historyFilters) {
        return;
      }
      const key = (which === 'from') ? 'fromDate' : 'toDate';
      this.historyFilters[key] = parsed;
      this.historyApplyFilter && this.historyApplyFilter();
    },
    copy(text) {
      navigator.clipboard?.writeText(text);
      this.showToast(this.t('toasts.copied'));
    },
    async confirmDialog({title, html, icon = 'warning', confirmText, confirmColor, focusConfirm = false}) {
      // No hex fallbacks — every token below is declared in BOTH :root
      // blocks. If `_cssVar` returns "" something is genuinely broken
      // at the token level and we want it to surface visibly rather
      // than be silently papered over by a literal that diverges from
      // the rest of the theme. Per CLAUDE.md's "no fallback literals"
      // rule (extended to JS-side reads).
      //
      // `focusCancel` defaults to true — the safe-by-default behaviour
      // for "are you sure?" dialogs surfaced by inadvertent clicks
      // (Enter confirms the cancel, no destructive action fires).
      // Call sites where the operator has ALREADY typed the intent
      // explicitly (Cmd-K palette + AI-proposed actions, where the
      // user just pressed Enter on a clearly-named action) pass
      // `focusConfirm: true` to flip the focus — Enter on the
      // confirm dialog now activates Confirm, matching the
      // operator's typing rhythm. Defaults preserve every other
      // call site's existing safety.
      const r = await Swal.fire({
        title, html, icon,
        showCancelButton: true,
        confirmButtonText: confirmText || this.t('actions.confirm'),
        cancelButtonText: this.t('actions.cancel'),
        reverseButtons: true,
        focusCancel: !focusConfirm,
        focusConfirm: !!focusConfirm,
        background: this._cssVar('--surface'),
        color: this._cssVar('--text'),
        confirmButtonColor: confirmColor || this._cssVar('--warning'),
        cancelButtonColor: this._cssVar('--btn-cancel-bg'),
      });
      return r.isConfirmed;
    },
    // Toast lifecycle. The 4 s auto-dismiss is paused while the
    // operator's pointer is over the toast OR a child element holds
    // focus — common case is "I want to copy the error text for
    // debugging but the toast disappears before I can select it".
    // Hovering / focusing arms `_toastHold = true`; the auto-dismiss
    // timer drains its remaining budget into a paused state and
    // resumes on mouseleave / blur. Clicking the close × dismisses
    // immediately. Default duration bumped from 4 s to 6 s and
    // error toasts get 10 s — operators triage errors more often
    // than success confirmations and need extra time to read +
    // decide whether to copy.
    showToast(msg, type = 'success') {
      this.toast = msg;
      this.toastType = type;
      this._toastHold = false;
      this._toastDuration = (type === 'error') ? 10000 : 6000;
      this._toastDeadline = Date.now() + this._toastDuration;
      clearTimeout(this._tt);
      this._tt = setTimeout(() => this._dismissToast(), this._toastDuration);
    },
    _dismissToast() {
      this.toast = '';
      clearTimeout(this._tt);
      this._tt = null;
      this._toastHold = false;
      this._toastDeadline = 0;
    },
    // Mouse / focus enters the toast — pause the auto-dismiss
    // timer. The remaining duration is preserved so when the
    // operator's mouse leaves / focus moves away, the timer
    // resumes from where it left off rather than restarting.
    _holdToast() {
      if (!this.toast) {
        return;
      }
      this._toastHold = true;
      // Capture how much time was left when the hold began so
      // resumeToast can re-arm with the same budget.
      this._toastRemaining = Math.max(500, (this._toastDeadline || 0) - Date.now());
      clearTimeout(this._tt);
      this._tt = null;
    },
    _resumeToast() {
      if (!this.toast) {
        return;
      }
      if (!this._toastHold) {
        return;
      }
      this._toastHold = false;
      const left = Math.max(1500, this._toastRemaining || this._toastDuration || 6000);
      this._toastDeadline = Date.now() + left;
      clearTimeout(this._tt);
      this._tt = setTimeout(() => this._dismissToast(), left);
    },
    // Copy the toast text to the clipboard so the operator can
    // paste into a bug report / chat / search box. Falls back to
    // a manual selection-copy when the Clipboard API isn't
    // available (file:// origins, older WebViews).
    async copyToastText() {
      const text = String(this.toast || '');
      if (!text) {
        return;
      }
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
        } else {
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          try {
            document.execCommand('copy');
          } finally {
            ta.remove();
          }
        }
        // Brief affordance — flip the body to "Copied!" then back.
        const original = this.toast;
        this.toast = (this.t('toasts.copied') || 'Copied to clipboard.') + ' ' + original;
        clearTimeout(this._tt);
        this._tt = setTimeout(() => {
          if (this.toast.endsWith(original)) {
            this.toast = original;
          }
          this._tt = setTimeout(() => this._dismissToast(),
            Math.max(2500, this._toastRemaining || 4000));
        }, 1200);
      } catch (_) { /* best-effort */
      }
    },

    // Error formatter — prefers the structured `error_code` + i18n
    // lookup (`errors.OG####`) over the backend's raw `error` string
    // when the backend returned a code. Falls back to the English
    // `error` text when no code is present or translation is missing.
    // See logic/errors.py for the catalogue.
    formatError(resp, fallback) {
      if (resp && resp.error_code) {
        const key = 'errors.' + String(resp.error_code);
        const localized = this.t(key, resp.error_params || {});
        // window.I18N.load falls back to English text when the key is
        // missing; the English text IS the DEFAULT_MESSAGES entry. If
        // the backend sent a more specific override_message (e.g.
        // upstream's `details` field), prefer that so operators see
        // the concrete failure, not the generic catalog text.
        if (resp.error && resp.error !== localized && !key.startsWith('errors.undefined')) {
          return resp.error;
        }
        if (localized && localized !== key) {
          return localized;
        }
      }
      return (resp && resp.error) || fallback || '';
    },

    // When a stack carries EXACTLY ONE item with `status==='update'`,
    // return that item's image so the update-popup can surface release
    // notes for it. Multi-service stacks return '' so the caller skips
    // the fetch — "what's new for stack X" with N images doesn't have
    // a single answer and the popup would mislead.
    _stackSingleUpdateImage(stack) {
      if (!stack) {
        return '';
      }
      const items = (this.items || []).filter(it =>
        it && it.stack_id === stack.stack_id && it.status === 'update' && it.image
      );
      if (items.length !== 1) {
        return '';
      }
      return items[0].image || '';
    },
    // ID anchor for the async release-notes block. The popup HTML
    // opens with a placeholder bearing this id; once the fetch
    // resolves, we replace the element's outerHTML with the real
    // notes block via `_replaceReleaseNotesAsync`. ID is stable so the
    // DOM lookup is cheap and unambiguous — SweetAlert2 renders one
    // popup at a time so the single-instance assumption holds.
    _RELEASE_NOTES_ASYNC_ID: 'omnigrid-release-notes-async',
    // GitHub-flavoured release notes carry many URL variants — commit
    // hashes, PR refs, user mentions, doc links, heading anchors —
    // which read as noise inside the preformatted view. Strategy: keep
    // the link TEXT, drop the URL. Pre-fix only commit-hash links were
    // stripped; netdata-style release notes (`[v2.10.2](https://...)`,
    // `[#22232](https://...)`, `[@ktsaou](https://...)`, `[Netdata
    // Learn](https://...)`) survived as `[text](url)` literals in the
    // rendered `<pre>` block.
    _scrubReleaseNotesBody(body) {
      if (!body) {
        return '';
      }
      let out = String(body);
      // Empty HTML heading anchors GitHub adds for permalinks: `<a id="x"></a>`
      out = out.replace(/<a\s+id=["'][^"']*["']\s*><\/a>/gi, '');
      // HTML <a href="..."> with inner text → keep text only.
      out = out.replace(/<a\s+href=["'][^"']*["'][^>]*>(?<txt>[\s\S]*?)<\/a>/gi, '$<txt>');
      // Markdown image refs (rare in release notes but worth handling
      // before the generic link rule so we don't keep `![alt]` orphans).
      // `![alt](url)` → `alt`.
      out = out.replace(/!\[([^\]]*)\]\(https?:\/\/[^)]+\)/g, '$1');
      // Markdown links: `[text](url)` → `text`. Empty text → drop the
      // whole thing (e.g. `[](url)` is just a wrapper around a URL).
      out = out.replace(/\[([^\]]*)\]\(https?:\/\/[^)]+\)/g, (m, txt) => {
        return (txt || '').trim() || '';
      });
      // Bare URLs in prose (`https://example.com/x` standalone): drop
      // the URL but leave the surrounding punctuation. Catches things
      // like "see https://...for details".
      out = out.replace(/https?:\/\/[^\s)]+/g, '');
      // Tidy: collapse the spaces a URL strip can leave (e.g. "see  for
      // details") + collapse triple+ newlines + drop trailing whitespace
      // on each line + tidy empty `()` left by bare-URL inside parens.
      out = out.replace(/\(\s*\)/g, '');
      out = out.replace(/[ \t]{2,}/g, ' ');
      out = out.replace(/\n{3,}/g, '\n\n');
      out = out.split('\n').map(l => l.replace(/[ \t]+$/, '')).join('\n');
      return out.trim();
    },
    // Build the static placeholder block that the popup opens with.
    // Shows a centred spinner + "Loading release notes..." copy.
    // Carries the `_RELEASE_NOTES_ASYNC_ID` id so the async fill can
    // find + replace it.
    _releaseNotesPlaceholderHtml() {
      const lbl = this._escapeReleaseHtml(this.t('dialogs.release_notes_label') || "What's new");
      const loading = this._escapeReleaseHtml(this.t('dialogs.release_notes_loading') || 'Loading release notes…');
      return [
        `<div id="${this._RELEASE_NOTES_ASYNC_ID}" class="release-notes-block release-notes-block--loading">`,
        `<div class="release-notes-head">`,
        `<span class="release-notes-label">${lbl}</span>`,
        `<span class="release-notes-spinner" aria-hidden="true"></span>`,
        `<span class="release-notes-loading-text">${loading}</span>`,
        '</div>',
        '</div>',
      ].join('');
    },
    // Render the resolved release-notes block from one /api/registry/
    // release-notes response payload. Returns the final HTML string the
    // async filler injects in place of the placeholder. Empty string
    // when the lookup yielded nothing actionable — caller removes the
    // placeholder entirely in that case.
    _buildReleaseNotesHtml(d) {
      const esc = this._escapeReleaseHtml.bind(this);
      const lbl = esc(this.t('dialogs.release_notes_label') || "What's new");
      if (d && d.ok && d.body) {
        const scrubbed = this._scrubReleaseNotesBody(d.body);
        if (!scrubbed.trim()) {
          return '';
        }
        const linkOut = d.html_url
          ? `<a href="${esc(d.html_url)}" target="_blank" rel="noopener" class="release-notes-link">${esc(this.t('dialogs.release_notes_view_on_source') || 'View on source')}</a>`
          : '';
        return [
          `<div id="${this._RELEASE_NOTES_ASYNC_ID}" class="release-notes-block">`,
          `<div class="release-notes-head">`,
          `<span class="release-notes-label">${lbl}</span>`,
          `<span class="release-notes-tag mono">${esc(d.tag || '')}</span>`,
          linkOut,
          '</div>',
          `<pre class="release-notes-body scrollbar">${esc(scrubbed)}</pre>`,
          '</div>',
        ].join('');
      }
      // No release body — surface the source link only when we have
      // it, so the operator can still investigate. Skip the block
      // entirely on a hard miss (no source label) to avoid clutter.
      if (d && d.source_url) {
        return [
          `<div id="${this._RELEASE_NOTES_ASYNC_ID}" class="release-notes-block release-notes-block--empty">`,
          `<span class="release-notes-label">${lbl}</span>`,
          `<a href="${esc(d.source_url)}" target="_blank" rel="noopener" class="release-notes-link">${esc(d.source_url)}</a>`,
          '</div>',
        ].join('');
      }
      return '';
    },
    // Fire the release-notes fetch + replace the placeholder block in
    // the open SwAl popup's DOM. Fire-and-forget — caller is the
    // synchronous `await confirmDialog(...)` path; the popup is already
    // open by the time this resolves. Defensive: when the operator
    // closes the popup before the fetch lands, `document.getElementById`
    // returns null and the replace is a no-op (no error). When the
    // server returns no body AND no source URL, the placeholder is
    // removed entirely so the popup doesn't carry a dangling spinner.
    async _replaceReleaseNotesAsync(image) {
      if (!image) {
        return;
      }
      try {
        const r = await fetch(`/api/registry/release-notes?image=${encodeURIComponent(image)}`);
        if (!r.ok) {
          // HTTP failure — remove the placeholder so the popup doesn't
          // hang on the spinner indefinitely.
          const el = document.getElementById(this._RELEASE_NOTES_ASYNC_ID);
          if (el) {
            el.remove();
          }
          return;
        }
        const d = await r.json();
        const html = this._buildReleaseNotesHtml(d);
        const el = document.getElementById(this._RELEASE_NOTES_ASYNC_ID);
        if (!el) {
          return;
        }   // popup closed before fetch resolved
        if (!html) {
          el.remove();
          return;
        }
        el.outerHTML = html;
      } catch (_) {
        // Silent — placeholder removed so popup doesn't carry a
        // stuck spinner. Operator still gets the actual update path.
        const el = document.getElementById(this._RELEASE_NOTES_ASYNC_ID);
        if (el) {
          el.remove();
        }
      }
    },
    async updateStack(stack, opts) {
      if (this.isStackBusy(stack)) {
        return;
      }
      const skipConfirm = !!(opts && opts.skipConfirm);
      if (!skipConfirm) {
        // Release notes only fire when the stack has EXACTLY ONE
        // updateable item — multi-service stacks don't have a single
        // "what's new" to surface and the popup would mislead. Pick
        // the lone item's image if it qualifies; else skip the
        // placeholder entirely. Popup opens INSTANTLY either way;
        // async filler replaces the placeholder on resolve.
        const stackImage = this._stackSingleUpdateImage(stack);
        // Blast-radius preview (MVP) — surfaces the
        // services / containers the stack update will touch so the
        // operator sees the full scope BEFORE confirming. Pure SPA
        // composition over the already-cached stack.items — no extra
        // fetch.
        const blastHtml = this._renderStackBlastRadius(stack);
        const html = this.t('dialogs.update_stack_html', {name: stack.name})
          + blastHtml
          + (stackImage ? this._releaseNotesPlaceholderHtml() : '');
        if (stackImage) {
          this._replaceReleaseNotesAsync(stackImage);
        }
        const ok = await this.confirmDialog({
          title: this.t('dialogs.update_stack_title'),
          html: html,
          icon: 'warning', confirmText: this.t('actions.update_stack'),
          focusConfirm: true,
        });
        if (!ok) {
          return;
        }
      }
      if (this.isStackBusy(stack)) {
        return;
      }
      const key = this._busyKey('stack', stack.stack_id);
      this._markBusy(key);
      try {
        const r = await fetch(`/api/update/stack/${stack.stack_id}`, {method: 'POST'});
        if (!r.ok) {
          throw new Error(await r.text());
        }
        this.showToast(this.t('toasts.queued', {name: stack.name}));
        this.pollOpsNow();
      } catch (e) {
        this._clearBusy(key);
        this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
      }
    },
    // Eligibility gate for the drawer's "Switch to tag…" inline-popover.
    // True iff the item is a container OR stack-managed item with an
    // image. The original gate required tag !== 'latest' (single-target
    // retag); the generalised "switch to ANY tag" feature accepts
    // moves both ways (e.g. :latest → :2 to pin a major-line, OR
    // :2.0.0-dev → :2 to leave a snapshot tag for the moving v2 line),
    // so the only requirements are:
    //   - has an image
    //   - has stack_id (Portainer-managed compose) OR raw_id (container
    //     we can recreate via Docker)
    //   - is a container/stack-managed item, NOT a Swarm service
    //     (services need `docker service update --image --force` —
    //     different flow).
    // Digest-only images (no :tag) are still eligible — the operator
    // can pin a tag where none was set before.
    canRetagToLatest(item) {
      if (!item || !item.image) {
        return false;
      }
      if (!item.stack_id && !item.raw_id) {
        return false;
      }
      // Orphans (Swarm task containers left behind on a previous task
      // replacement) can't be retagged — they're meant to be removed,
      // not rolled forward.
      if (item.type === 'orphan') {
        return false;
      }
      // Swarm services qualify ONLY when they belong to a Portainer-
      // managed stack (stack_id present). The `submitRetagPopover`
      // wiring routes those to the existing `/api/update/stack/{id}/
      // retag-latest` endpoint with `image_repo` set to the service's
      // own image so the compose-file mutation touches just THAT line,
      // not every image in the stack. Services without stack_id (rare
      // — Swarm services deployed outside Portainer) stay ineligible
      // because the backend has no compose file to mutate.
      if (item.type === 'service' && !item.stack_id) {
        return false;
      }
      return true;
    },
    isRetagPopoverOpen(item) {
      if (!item || !this._retagPopoverItemId) {
        return false;
      }
      return this._retagPopoverItemId === (item.raw_id || item.id);
    },
    closeRetagPopover() {
      this._retagPopoverItemId = null;
      this._retagDraft = '';
      this._retagBusy = false;
      this._retagPopoverPos = null;
      if (this._retagScrollOff) {
        this._retagScrollOff();
        this._retagScrollOff = null;
      }
    },
    async restartService(item, opts) {
      return this.restartItem(item, opts);
    },
    async restartItem(item, opts) {
      if (!this.isRestartable(item)) {
        return;
      }
      if (this.isRestartBusy(item)) {
        return;
      }
      const skipConfirm = !!(opts && opts.skipConfirm);
      const isService = item.type === 'service';
      if (!skipConfirm) {
        const body = isService
          ? this.t('dialogs.restart_service_html', {name: item.name})
          : this.t('dialogs.restart_container_html', {name: item.name});
        const ok = await this.confirmDialog({
          title: isService ? this.t('dialogs.restart_service_title') : this.t('dialogs.restart_container_title'),
          html: body,
          icon: 'question', confirmText: this.t('actions.restart'), confirmColor: this._cssVar('--primary'),
          focusConfirm: true,
        });
        if (!ok) {
          return;
        }
      }
      if (this.isRestartBusy(item)) {
        return;
      }
      const key = isService ? this._busyKey('svc', item.raw_id) : this._busyKey('ctn', item.raw_id);
      const url = isService
        ? `/api/restart/service/${item.raw_id}`
        : `/api/restart/container/${item.raw_id}`;
      this._markBusy(key);
      try {
        const r = await fetch(url, {method: 'POST'});
        if (!r.ok) {
          throw new Error(await r.text());
        }
        this.showToast(this.t('toasts.restart_queued', {name: item.name}));
        this.drawerItem = null;
        this.pollOpsNow();
      } catch (e) {
        this._clearBusy(key);
        this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
      }
    },
    // Unhealthy-agent banner's one-click force-restart of the
    // Portainer agent service. Backend auto-discovers the service
    // by image-prefix + name pattern; on ambiguous discovery the op
    // surfaces the candidate list and refuses to auto-pick.
    async restartSwarmAgent() {
      const ok = await this.confirmDialog({
        title: this.t('swarm_agent_banner.confirm_title'),
        text: this.t('swarm_agent_banner.confirm_text'),
        icon: 'warning',
        confirmText: this.t('swarm_agent_banner.restart_button'),
        confirmColor: this._cssVar('--warning'),
        focusConfirm: true,
      });
      if (!ok) {
        return;
      }
      if (this.swarmAgentRestartBusy) {
        return;
      }
      this.swarmAgentRestartBusy = true;
      try {
        const r = await fetch('/api/swarm/restart-agent', {method: 'POST'});
        if (!r.ok) {
          throw new Error(await r.text());
        }
        this.showToast(this.t('swarm_agent_banner.restart_queued'));
        this.pollOpsNow();
      } catch (e) {
        this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
      } finally {
        // Cleared after a short delay so the spinner stays visible
        // long enough for the operator to see it fired (the actual
        // op runs in the background and surfaces in the ops queue).
        setTimeout(() => {
          this.swarmAgentRestartBusy = false;
        }, 1200);
      }
    },
    async removeContainer(item, opts) {
      if (this.isItemBusy(item)) {
        return;
      }
      const skipConfirm = !!(opts && opts.skipConfirm);
      if (!skipConfirm) {
        const ok = await this.confirmDialog({
          title: this.t('dialogs.remove_container_title'),
          html: this.t('dialogs.remove_container_html', {name: item.name}),
          icon: 'warning', confirmText: this.t('actions.remove'), confirmColor: this._cssVar('--danger'),
          focusConfirm: true,
        });
        if (!ok) {
          return;
        }
      }
      if (this.isItemBusy(item)) {
        return;
      }
      const key = this._busyKey('ctn', item.raw_id);
      this._markBusy(key);
      try {
        const r = await fetch(`/api/remove/container/${item.raw_id}`, {method: 'POST'});
        if (!r.ok) {
          throw new Error(await r.text());
        }
        this.showToast(this.t('toasts.remove_queued', {name: item.name}));
        this.drawerItem = null;
        this.pollOpsNow();
      } catch (e) {
        this._clearBusy(key);
        this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
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
