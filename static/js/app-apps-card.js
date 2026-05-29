// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ContinueStatementJS,BreakStatementJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML,JSAsyncFunctionMissingAwait
// noinspection JSMissingAwait,JSUnfilteredForInLoop,IfStatementWithoutBlockJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSPotentiallyInvalidUsageOfThis,JSIgnoredPromiseFromCall
/* global Alpine, Swal, I18N, t, AbortController, setTimeout, clearTimeout */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// Per-app-card helpers (status pill class + reason text + latency
// labels + per-instance sparkline + chip aria/title + per-port pill
// state + clickable port URLs + Show-all toggle + per-card extras
// gate + card-span + per-host apps-pill rollup). Plus the bulk
// per-card probe-all + open-all pill handlers. Split from
// app-apps.js (the orchestration file) when it crossed the
// 3000-line split-candidate threshold.
//
// Module-scope state (per-flush memos + sparkline cache) lives in the
// shared `app-apps-state.js` module so every sibling app-apps* file
// observes the same cache identity (a WeakMap declared per-file would
// see different keys even though the source app object is the same).
// `_set*` helpers are used to mutate `let`-bindings exported across
// the module boundary — ESM `export let` re-exports a LIVE binding
// but importers can't assign through it, so the setters wrap the
// re-assignment.
import {
  _appsSparkCache,
  _appsVisibleInstancesCache,
  _appsVisibleInstancesFlushScheduled,
  _setAppsVisibleInstancesFlushScheduled,
  _clearAppsVisibleInstancesFlushCache,
  _anyAppExtrasMatchCache,
  _hostAppsHealthFlushCache,
  _setHostAppsHealthFlushCache,
  _hostAppsHealthFlushScheduled,
  _setHostAppsHealthFlushScheduled,
  _clearHostAppsHealthFlushCache,
} from './app-apps-state.js?v=__APP_VERSION__';

export default {
  // Per-card chip class for the status pill.
  appStatusPillClass(status) {
    if (status === 'up') {
      return 'pill-ok';
    }
    if (status === 'down') {
      return 'pill-error';
    }
    if (status === 'degraded') {
      return 'pill-warning';
    }
    return 'pill-muted';
  },

  // Diagnosis reason for a non-up Apps instance — answers "why is this
  // degraded / down?" at a glance. Prefers a specific failing port's
  // error (multi-port chips), then the chip-level rollup error, then a
  // generic fallback. Probe error strings come straight from the
  // sampler (timeout / ConnectionRefusedError / unexpected status 404 /
  // …) and stay un-translated — they're diagnostic, not UI chrome.
  // Returns '' for up instances so the template can gate on it.
  appsInstanceReason(inst) {
    if (!inst || inst.status === 'up') {
      return '';
    }
    if (inst.status === 'unknown') {
      return this.t('apps.reason_no_probe') || 'No probe result yet';
    }
    const pr = (inst.port_results || []).find((p) => p && !p.alive && p.error);
    if (pr) {
      return this.t('apps.reason_port', {port: pr.port, error: pr.error})
        || ('Port ' + pr.port + ': ' + pr.error);
    }
    const lp = inst.last_probe;
    if (lp && lp.error) {
      return lp.error;
    }
    return this.t('apps.reason_unreachable') || 'Probe failed (no detail)';
  },

  // Latency unit — single i18n point for the "Nms" suffix used across every
  // Apps surface (bare form). Parenthesised form via appsLatencyParen().
  // Keeps the unit out of template-literal concatenation so RTL / non-Latin
  // locales can place / translate it (ms vs мс vs ミリ秒).
  appsLatencyMs(n) {
    // Group thousands per the browser locale ("1,234" / "1.234") and let
    // the i18n format own the space + unit ("{n} ms").
    const num = (typeof n === 'number') ? n.toLocaleString() : n;
    return this.t('common.latency_ms', {n: num}) || (num + ' ms');
  },

  appsLatencyParen(n) {
    const num = (typeof n === 'number') ? n.toLocaleString() : n;
    return this.t('common.latency_paren', {n: num}) || ('(' + num + ' ms)');
  },

  // Per-instance uptime sparkline for the Apps card. Reads the backend-
  // supplied `inst.status_history` ([{ts, up}, ...], oldest->newest) and
  // returns a MEMOIZED descriptor {points, pct, n, allUp}:
  //   points = SVG polyline coords in a 0..48 x 0..12 viewBox — up sits at
  //            the top edge, a down sample drops to the baseline, so one
  //            <polyline> reads as "flat = healthy, dip = outage";
  //   pct    = uptime % over the window; n = sample count;
  //   allUp  = no down samples (drives the green-vs-amber colour class).
  // Returns null when there are < 2 points (caller hides the spark). The
  // result is cached against the status_history array ref so :points +
  // :title + the colour class all read it without rebuilding per flush.
  appsInstanceSpark(inst) {
    const hist = inst && inst.status_history;
    if (!Array.isArray(hist) || hist.length < 2) {
      return null;
    }
    const cached = _appsSparkCache.get(hist);
    if (cached) {
      return cached;
    }
    const width = 48;
    const top = 1;
    const bottom = 11;
    const n = hist.length;
    const stepX = width / (n - 1);
    const coords = [];
    let upCount = 0;
    for (let i = 0; i < n; i++) {
      const up = !!(hist[i] && hist[i].up);
      if (up) {
        upCount++;
      }
      coords.push((i * stepX).toFixed(1) + ',' + (up ? top : bottom));
    }
    const out = {
      points: coords.join(' '),
      pct: Math.round((upCount / n) * 100),
      n: n,
      allUp: upCount === n,
    };
    _appsSparkCache.set(hist, out);
    return out;
  },

  // Host-drawer chip-strip tooltip: "<name> — <status> (<rtt>ms)". The
  // name+status base (incl. its separator) lives in the i18n format string
  // so locales control the separator; rtt is appended via the latency key.
  appsChipTitle(app) {
    const name = (app && (app.name || (app.catalog && app.catalog.name))) || '';
    const status = this.t('apps.status_' + (app && app.status)) || (app && app.status) || '';
    let s = this.t('apps.chip_tooltip', {name, status}) || (name + ' — ' + status);
    const rtt = app && app.last_probe && app.last_probe.rtt_ms;
    if (rtt != null) {
      s += ' ' + this.appsLatencyParen(rtt);
    }
    return s;
  },

  // Chip-strip accessible name — name + status only (the latency is noise in
  // a screen-reader announcement).
  appsChipAriaLabel(app) {
    const name = (app && (app.name || (app.catalog && app.catalog.name))) || '';
    const status = this.t('apps.status_' + (app && app.status)) || (app && app.status) || '';
    return this.t('apps.chip_tooltip', {name, status}) || (name + ' — ' + status);
  },

  // Apps instance host-row tooltip: canonical address, plus the operator
  // label when it differs. Separator lives in the i18n format string.
  appsInstanceHostTitle(inst) {
    const base = (inst && (inst.host_address || inst.host_id)) || '';
    const label = (inst && inst.host_label) || '';
    if (label && label !== base) {
      return this.t('apps.host_tooltip', {host: base, label}) || (base + ' — ' + label);
    }
    return base;
  },

  // Tri-state for a per-port pill: 'up' (alive===true), 'down'
  // (alive===false), 'unknown' (alive null/undefined = PENDING — the
  // port is configured on the chip but the sampler hasn't probed it
  // yet). Drives the pill + status-dot colour class.
  appsPortState(pr) {
    if (!pr) {
      return 'unknown';
    }
    if (pr.alive === true) {
      return 'up';
    }
    if (pr.alive === false) {
      return 'down';
    }
    return 'unknown';
  },

  // Clickable-port URL: when a port is flagged `open_url` (per-port
  // checkbox in the catalog template / instance editor), return
  // <scheme>://<host>:<port> so the pill renders as a link. Works on ANY
  // protocol — the operator ticked the box, so they know the port serves
  // a browsable URL even if it's labelled tcp (e.g. an app on a bare-TCP
  // chip that actually speaks HTTP). Scheme follows the protocol: https
  // for `https`, otherwise http. Empty string ⇒ not clickable.
  appsPortHref(pr, ctx) {
    if (!pr || !pr.open_url || !pr.port) {
      return '';
    }
    const host = ctx && (ctx.host_address || ctx.address || ctx.host || ctx.host_id || ctx.id);
    if (!host) {
      return '';
    }
    const proto = String(pr.protocol || '').toLowerCase();
    const base = (proto === 'https' ? 'https' : 'http') + '://' + host + ':' + pr.port;
    // Append the per-port HTTP path (e.g. Pi-hole's /admin/) so the link
    // lands on the app's real entry point, not the bare host root. Uses the
    // same `probe_path` the HTTP health probe hits; normalise to a leading
    // slash. A bare '/' adds nothing, so skip it. BUT health-check endpoints
    // (/ping, /api/v1/info, /healthz, …) are NOT user-facing pages — the
    // probe keeps hitting them, but the clickable link drops them so it
    // opens the app's UI root instead of a JSON/health response.
    let path = String(pr.probe_path || '').trim();
    if (path && path !== '/' && !this._isHealthCheckPath(path)) {
      if (path[0] !== '/') {
        path = '/' + path;
      }
      return base + path;
    }
    return base;
  },

  // True when a probe_path is a health/status endpoint (not a navigable
  // page) — so appsPortHref drops it from the clickable URL while the
  // probe still hits it. Matches the common health paths + the `/api/vN/…`
  // info/health family. Trailing slashes are ignored.
  _isHealthCheckPath(path) {
    const p = String(path || '').trim().toLowerCase().replace(/\/+$/, '');
    if (!p) {
      return false;
    }
    const exact = [
      '/ping', '/health', '/healthz', '/healthcheck', '/api/health', '/api/healthz',
      '/status', '/-/healthy', '/-/ready', '/livez', '/readyz', '/health/ready',
      '/health/live', '/health/ready', '/health/started',
      '/metrics', '/-/metrics',
    ];
    if (exact.includes(p)) {
      return true;
    }
    // /api/v1/info (NetData), /api/v2/info, /api/v1/health, etc.
    if (/^\/api\/v\d+\/(?:info|health|status|ready)$/.test(p)) {
      return true;
    }
    // Prometheus / k8s style: /-/health, /-/health/live, /-/health/ready,
    // /-/live, /-/started, /healthz/live, etc.
    return /^\/-\/health(?:\/(?:live|ready|started))?$/.test(p)
      || /^\/-\/(?:live|started)$/.test(p)
      || /^\/healthz?\/(?:live|ready|started)$/.test(p);
  },

  // ---- Apps-view per-app host-list cap (Show all / Show less) --------
  // An app pinned to many hosts would make its Apps-view card grow very
  // tall and break the grid's uniform rows. Cap the rendered instance
  // list at APPS_INSTANCES_COLLAPSED_LIMIT until the operator expands it.
  _appsInstancesLimit() {
    // Cap visible per-host instances at 3 before the "Show all (N)"
    // toggle. A card pinned to many hosts (e.g. node_exporter on 50+
    // boxes) would otherwise grow tall + break the Apps grid's
    // uniform row heights. Was 6, then 4, now 3 per operator-flagged
    // "decrease the height of app cards with a lot of hosts, display
    // only 3 then show more". Operator clicks "Show all (N)" to
    // expand; per-app expanded state is persisted in
    // `appsInstancesExpanded[group_id]` so a poll-refresh doesn't
    // collapse what the operator just opened.
    return 3;
  },

  appsVisibleInstances(app) {
    if (!app) {
      return [];
    }
    // Per-flush WeakMap memo — called TWICE per card (standard chip
    // list + per-app extras x-for). See _appsVisibleInstancesCache
    // comment block at top.
    const expanded = !!(this.appsInstancesExpanded
      && this.appsInstancesExpanded[app.group_id]);
    const cached = _appsVisibleInstancesCache.get(app);
    if (cached && cached.expanded === expanded
      && cached.src === app.instances) {
      return cached.result;
    }
    const all = Array.isArray(app.instances) ? app.instances : [];
    const result = expanded ? all : all.slice(0, this._appsInstancesLimit());
    _appsVisibleInstancesCache.set(app, {expanded, src: app.instances, result});
    if (!_appsVisibleInstancesFlushScheduled) {
      _setAppsVisibleInstancesFlushScheduled(true);
      queueMicrotask(_clearAppsVisibleInstancesFlushCache);
    }
    return result;
  },

  // True when at least one per-app extras partial matches the app —
  // gates the per-app-extras `<template x-for>` so non-matching apps
  // skip the iteration entirely (saves N empty wrapper divs per
  // non-matching app card). Memoized per-flush via WeakMap so the
  // registry walk is one-shot per app per flush regardless of how
  // many bindings consult it.
  anyAppExtrasMatch(app) {
    if (!app) {
      return false;
    }
    if (_anyAppExtrasMatchCache.has(app)) {
      return _anyAppExtrasMatchCache.get(app);
    }
    const ext = (window.OG_APPS_EXTENDERS || []);
    const cat = app.catalog || {};
    const slug = String(cat.slug || '').trim().toLowerCase();
    const name = String(app.name || '').toLowerCase();
    let matched = false;
    for (const e of ext) {
      if (!e || !Array.isArray(e.slugs)) {
        continue;
      }
      for (const s of e.slugs) {
        const needle = String(s).toLowerCase();
        if (slug === needle || (needle && name.indexOf(needle) !== -1)) {
          matched = true;
          break;
        }
      }
      if (matched) {
        break;
      }
    }
    _anyAppExtrasMatchCache.set(app, matched);
    return matched;
  },

  appsInstancesCollapsible(app) {
    const all = (app && Array.isArray(app.instances)) ? app.instances : [];
    return all.length > this._appsInstancesLimit();
  },

  appsInstancesHiddenCount(app) {
    const all = (app && Array.isArray(app.instances)) ? app.instances : [];
    return Math.max(0, all.length - this._appsInstancesLimit());
  },

  toggleAppsInstances(app) {
    if (!app || !app.group_id) {
      return;
    }
    if (!this.appsInstancesExpanded) {
      this.appsInstancesExpanded = {};
    }
    this.appsInstancesExpanded[app.group_id] = !this.appsInstancesExpanded[app.group_id];
  },

  // Aggregate app-health summary for a host's pinned apps — drives the
  // Hosts-view row "N apps" badge (count) AND its colour (aggregate
  // health). Returns {total, up, down, unknown, state} where state is
  // '' (no apps) / 'warn' (>=1 app down) / 'ok' (all up; pending/unknown
  // apps don't count as a failure). DELIBERATELY a separate signal from
  // the host's reachability status dot: amber on that dot means
  // 'paused', so app-degraded gets its own badge rather than overloading
  // the load-bearing 6-value status enum. Derived purely from h.apps
  // (already reconciled in place), so the badge updates with the row.
  hostAppsHealth(h) {
    const _key = (h && h.id) || null;
    if (_key !== null) {
      if (_hostAppsHealthFlushCache === null) {
        const _fresh = new Map();
        _setHostAppsHealthFlushCache(_fresh);
        if (!_hostAppsHealthFlushScheduled) {
          _setHostAppsHealthFlushScheduled(true);
          queueMicrotask(_clearHostAppsHealthFlushCache);
        }
        const _res0 = this._hostAppsHealthCompute(h);
        _fresh.set(_key, _res0);
        return _res0;
      }
      const _hit = _hostAppsHealthFlushCache.get(_key);
      if (_hit !== undefined) {
        return _hit;
      }
      const _res = this._hostAppsHealthCompute(h);
      _hostAppsHealthFlushCache.set(_key, _res);
      return _res;
    }
    return this._hostAppsHealthCompute(h);
  },

  // Uncached compute behind the per-flush memo above.
  // Tracks `degraded` as a first-class counter (was silently lumped into
  // `unknown`, which hid the count from the host-row apps pill); state is
  // 'warn' when any apps are down OR degraded so the pill flips amber on
  // partial-outage too. Operators reading the pill see the breakdown via
  // the new degraded/down chips beside the total count.
  _hostAppsHealthCompute(h) {
    const apps = (h && Array.isArray(h.apps)) ? h.apps : [];
    const total = apps.length;
    if (!total) {
      return {total: 0, up: 0, down: 0, degraded: 0, unknown: 0, state: ''};
    }
    let up = 0;
    let down = 0;
    let degraded = 0;
    let unknown = 0;
    for (const a of apps) {
      const s = a && a.status;
      if (s === 'up') {
        up += 1;
      } else if (s === 'down') {
        down += 1;
      } else if (s === 'degraded') {
        degraded += 1;
      } else {
        unknown += 1;
      }
    }
    return {
      total,
      up,
      down,
      degraded,
      unknown,
      state: (down > 0 || degraded > 0) ? 'warn' : 'ok',
    };
  },

  // Per-port pill tooltip: "<status> (<rtt>ms) — <error>" (rtt + error both
  // optional). status + the error separator route through i18n; the error
  // text itself is the un-translated probe diagnostic from the sampler.
  // A pending (configured-but-unprobed) port reads "pending".
  appsPortTitle(pr) {
    if (!pr) {
      return '';
    }
    const state = this.appsPortState(pr);
    let s;
    if (state === 'up') {
      s = this.t('apps.status_up') || 'up';
    } else if (state === 'down') {
      s = this.t('apps.status_down') || 'down';
    } else {
      s = this.t('apps.port_pending') || 'pending';
    }
    if (pr.rtt_ms != null) {
      s += ' ' + this.appsLatencyParen(pr.rtt_ms);
    }
    if (pr.error) {
      s += this.t('apps.error_suffix', {error: pr.error}) || (' — ' + pr.error);
    }
    return s;
  },

  // ----------------------------------------------------------------
  // Apps detail / debug drawer — mirrors the host drawer. Clicking an
  // app card opens drawerApp; each instance has a "Show debug" panel
  // that lazy-fetches /api/services/{host}/{idx}/debug (the resolved
  // probe target, chip + catalog config, latest per-port outcomes) so
  // the operator can see WHY an instance on a host isn't reporting.
  // ----------------------------------------------------------------
  drawerApp: null,
  appDebug: {},      // { "<host_id>:<idx>": { loading, error, data } }
  appDebugOpen: {},  // { "<host_id>:<idx>": bool }

  appInstanceKey(inst) {
    return (inst && (inst.host_id + ':' + inst.service_idx)) || '';
  },

  // ----- Per-app extension hooks -------------------------------
  // Generic dispatchers that walk `window.OG_APPS_EXTENDERS`
  // (registered by each per-app SPA module under
  // `static/js/apps/<slug>.js`) to decide per-app variations
  // (card span, api_key support, extras-panel visibility).
  //
  // Resolve the grid-column span for one app card. Default = 1.
  // Per-app modules can request a wider span via the registered
  // ``cardSpan`` extender (see `static/js/apps/*.js`). The first
  // matching extender that returns >1 wins; absent extenders
  // fall through to default 1.
  appsCardSpan(app) {
    const ext = (window.OG_APPS_EXTENDERS || []);
    for (const e of ext) {
      if (e && typeof e.cardSpan === 'function') {
        const span = e.cardSpan(app);
        if (typeof span === 'number' && span > 1) {
          return span;
        }
      }
    }
    return 1;
  },

  // Whether a given app TEMPLATE supports an extras panel — drives
  // the visibility of the per-template + per-instance "Show extras"
  // checkboxes in the Admin → Apps editors. Registry-driven via
  // `window.OG_APPS_EXTENDERS` — any per-app module registered in
  // `static/js/apps/*.js` is treated as extras-capable. The
  // argument is the editor's current slug (catalog `slug` field)
  // OR the chip's catalog_name / app name; substring match against
  // each extender's `slugs` array.
  appsTemplateSupportsExtras(slugOrName) {
    const s = String(slugOrName || '').toLowerCase();
    if (!s) {
      return false;
    }
    const ext = (window.OG_APPS_EXTENDERS || []);
    for (const e of ext) {
      if (e && Array.isArray(e.slugs)) {
        for (const slug of e.slugs) {
          if (s.indexOf(String(slug).toLowerCase()) !== -1) {
            return true;
          }
        }
      }
    }
    return false;
  },

  // Whether a given app TEMPLATE requires a per-instance api_key.
  // Slug-keyed lookup against the backend's per-app registry —
  // delivered via `/api/me`'s `client_config.apps_module_slugs`
  // list. Per-app modules that DECLARE `requires_api_key()` get
  // a positive hit; others fall through to false. Centralised
  // here so the editor's API-key field + Test-credential button
  // can render uniformly without per-app SPA literals.
  appsTemplateRequiresApiKey(slugOrName) {
    const s = String(slugOrName || '').toLowerCase();
    if (!s) {
      return false;
    }
    const ext = (window.OG_APPS_EXTENDERS || []);
    for (const e of ext) {
      if (e && Array.isArray(e.slugs)) {
        for (const slug of e.slugs) {
          if (s.indexOf(String(slug).toLowerCase()) !== -1 && e.requiresApiKey) {
            return true;
          }
        }
      }
    }
    return false;
  },

  // Resolve the effective "show extras" boolean for one instance,
  // honouring the tri-state per-instance override + the template
  // default. Resolution order:
  //   1. Per-instance `inst.show_extras` (true / false) — explicit
  //      operator opt-in or opt-out for THIS instance regardless of
  //      template. null / undefined means "inherit".
  //   2. App-level `app.catalog.show_extras` (true / false) — the
  //      template's default that every uninherited instance follows.
  //   3. Default `true` — when no override + no template setting,
  //      extras render. Backward-compatible with the pre-toggle
  //      behaviour where every extras-capable instance
  //      unconditionally rendered its panel.
  appsShowExtras(app, inst) {
    if (inst && typeof inst.show_extras === 'boolean') {
      return inst.show_extras;
    }
    const cat = (app && app.catalog) || null;
    if (cat && typeof cat.show_extras === 'boolean') {
      return cat.show_extras;
    }
    // Default OFF — extras are OPT-IN (see the matching helper in
    // app-apps.js for the full rationale). Unchecked "Show extras" now
    // means no panel; the operator ticks the box to enable it.
    return false;
  },
  // Per-app expanded-card data — generic dispatcher backed by
  // GET /api/services/{host_id}/{service_idx}/app-data. The
  // backend selects the per-app module via slug; the SPA caches
  // the response per (host_id, service_idx) for the render
  // flush. Per-app modules (static/js/apps/*.js) read this via
  // `cmp.appsAppData(inst)` + drive their template gates on the
  // returned shape.
  _appsDataCache: null,
  _appsDataPending: null,

  // Instances of this app that can be probed (carry a host_id + service_idx
  // — the per-chip probe endpoint targets exactly those). Used for the
  // "Probe all (N)" button count + gate.
  appsProbeTargetCount(app) {
    if (!app || !Array.isArray(app.instances)) {
      return 0;
    }
    return app.instances.filter((i) => i && i.host_id != null && i.service_idx != null).length;
  },

  // Instances of this app that carry a clickable URL — drives the
  // "Open all (N)" button count + gate.
  appsOpenableCount(app) {
    if (!app || !Array.isArray(app.instances)) {
      return 0;
    }
    return app.instances.filter((i) => i && i.url).length;
  },

  // Is this app's per-app probe-all batch currently running? Drives the
  // button spinner + :disabled. Keyed by group_id.
  appsProbingAll(app) {
    return !!(app && this._appsProbingAll && this._appsProbingAll[app.group_id]);
  },

  // Transient summary line ("4/5 up") shown in the card header right after a
  // probe-all finishes; '' when none. Keyed by group_id.
  appsProbeAllSummary(app) {
    return (app && this._appsProbeAllSummary && this._appsProbeAllSummary[app.group_id]) || '';
  },

  // Probe EVERY instance of this app in one click — bounded fan-out over the
  // per-chip probe endpoint, then ONE apps-list reload so each instance's
  // status dot + rtt updates in place (the inline per-host result). A single
  // summary toast + a transient header summary line report the rollup.
  async probeAllInstances(app) {
    if (!app || !Array.isArray(app.instances) || !app.instances.length) {
      return;
    }
    const gid = app.group_id;
    if (!this._appsProbingAll) {
      this._appsProbingAll = {};
    }
    if (this._appsProbingAll[gid]) {
      return;
    }
    // Snapshot the targets up front — loadAppsList(true) reconciles
    // app.instances in place, so iterating it live could skip/repeat.
    const targets = app.instances
      .filter((i) => i && i.host_id != null && i.service_idx != null)
      .map((i) => ({host_id: i.host_id, service_idx: i.service_idx}));
    if (!targets.length) {
      return;
    }
    this._appsProbingAll[gid] = true;
    this._appsProbeAllSummary[gid] = '';
    try {
      const results = await this._fanOutBounded(targets, async (tgt) => {
        const r = await fetch('/api/services/' + encodeURIComponent(tgt.host_id)
          + '/' + encodeURIComponent(tgt.service_idx) + '/probe', {method: 'POST'});
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          return {ok: false, error: j.detail || ('HTTP ' + r.status)};
        }
        const j = await r.json();
        return {ok: true, alive: !!j.alive};
      }, 4);
      const up = results.filter((x) => x && x.ok && x.alive).length;
      const total = targets.length;
      this._appsProbeAllSummary[gid] = this.t('apps.probe_all_summary', {up: up, total: total})
        || (up + '/' + total + ' up');
      // ONE reload so every instance's dot + rtt reflects the fresh probe
      // (the inline per-host result), matching the in-place reconcile
      // discipline (loadAppsList reconciles instances by host_id+service_idx).
      if (typeof this.loadAppsList === 'function') {
        await this.loadAppsList(true);
      }
      if (typeof this.showToast === 'function') {
        this.showToast(
          this.t('apps.probe_all_done', {name: app.name, up: up, total: total})
          || ('Probed ' + total + ' instances — ' + up + ' up'),
          up === total ? 'success' : 'info'
        );
      }
    } finally {
      this._appsProbingAll[gid] = false;
    }
  },

  // Open every instance URL of this app in a new tab. MUST stay synchronous
  // (no await before the window.open loop) so the browser treats each open
  // as user-initiated — an intervening await would break the click-gesture
  // chain and the popup blocker would swallow all but the first.
  openAllInstances(app) {
    if (!app || !Array.isArray(app.instances)) {
      return;
    }
    const urls = [];
    const seen = new Set();
    for (const inst of app.instances) {
      const u = inst && inst.url;
      if (u && !seen.has(u)) {
        seen.add(u);
        urls.push(u);
      }
    }
    if (!urls.length) {
      return;
    }
    for (const u of urls) {
      window.open(u, '_blank', 'noopener');
    }
  },
};
