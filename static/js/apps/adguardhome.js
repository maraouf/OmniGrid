// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection JSPotentiallyInvalidUsageOfThis,JSUnresolvedReference,JSUnresolvedVariable,JSUnresolvedFunction,OverlyComplexBooleanExpressionJS,ContinueStatementJS,JSValidateTypes,NegatedIfStatementJS
/* global fetch, Number, Math, isFinite, encodeURIComponent */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W116 */

// Per-app SPA module -- AdGuard Home.
//
// Unlike Speedtest (per-instance extras), AdGuard renders ONE
// APP-LEVEL aggregated block per card: the operator runs N AdGuard
// hosts and wants the combined fleet stats, not a row per host. So the
// extender sets `instanceExtras: false` (skip the per-instance extras
// loop) + `appLevelExtras: true` (render once per app via the app-level
// slot in `apps-card.html`). `adguardAggregate(app)` sums each
// instance's per-host `app-data` fetch client-side.
//
// Actions are fleet-wide (enable / disable / disable-for-X / refresh /
// re-enable). They dispatch ONE skill (the backend `run_skill` fans out
// to every instance), so the SPA targets the first credentialed
// instance and lets the module loop the fleet.
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the
// full rationale -- same per-file conventions apply here.

// Timed-disable presets surfaced as buttons. The seconds map to the
// backend's `adguard_disable_<label>` skill ids (logic/apps/adguardhome.py
// DISABLE_PRESETS). Keep the two lists in lock-step.
const DISABLE_PRESETS = [
  {label: '1m', skill: 'adguard_disable_1m'},
  {label: '5m', skill: 'adguard_disable_5m'},
  {label: '10m', skill: 'adguard_disable_10m'},
  {label: '30m', skill: 'adguard_disable_30m'},
  {label: '1h', skill: 'adguard_disable_1h'},
  {label: '2h', skill: 'adguard_disable_2h'},
  {label: '24h', skill: 'adguard_disable_24h'},
];

function isAdguardApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'adguard-home' || slug === 'adguardhome' || slug === 'adguard') {
    return true;
  }
  const name = String(app.name || '').toLowerCase();
  // Exclude the separate AdGuard Home SYNC app — its name also contains
  // 'adguard', and a loose substring match would bleed the AdGuard fleet
  // card + enable/disable/refresh actions onto a sync chip. The sync app has
  // its OWN module (adguardhome_sync.js / isAdguardSyncApp), so a name with
  // 'sync' is never plain AdGuard here.
  if (name.indexOf('sync') !== -1) {
    return false;
  }
  return (name.indexOf('adguard') !== -1);
}

function _num(v) {
  const n = Number(v);
  return isFinite(n) ? n : 0;
}

// Format an integer count with thousands separators; '—' for nothing.
function adguardInt(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// Percent with one decimal.
function adguardPct(v) {
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return n.toLocaleString(undefined, {maximumFractionDigits: 1}) + '%';
}

// Milliseconds, one decimal.
function adguardMs(v) {
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return n.toLocaleString(undefined, {maximumFractionDigits: 1}) + ' ms';
}

// Aggregate every instance's per-host app-data into ONE fleet summary.
// Reads `this.appsAppData(inst)` per instance (triggers the per-host
// fetch + returns cached data / {__pending} / {__error} / null). Returns
// a render-ready object the app-level extras partial binds to.
//   blocked / queries / clients -> SUM across hosts
//   blocklist rules             -> MAX across hosts (operator decision —
//                                  hosts share roughly the same lists)
//   avg processing time         -> query-WEIGHTED mean
//   top blocked domain          -> the single max-count across hosts
//   protection                  -> count of hosts with protection ON
// Loading / failed hosts are tracked so the block can show partial
// totals honestly (footnote) instead of failing wholesale.
// noinspection OverlyLongFunctionJS,FunctionTooLongJS,JSFunctionTooLong
function adguardAggregate(app) {
  // Bound onto the Alpine component (merged via helpers), so `this` is the
  // component — JSHint can't infer the bind for a standalone function.
  /* jshint validthis: true */
  const out = {
    ready: false, loading: false,
    n: 0, okN: 0,
    queries: 0, blocked: 0, pct: 0, rules: 0, clients: 0, avgMs: 0,
    safebrowsing: 0, parental: 0,
    topBlocked: null, topQueried: null, topClient: null,
    queriesSeries: [], blockedSeries: [],
    trend: null,
    protOn: 0, protTotal: 0, protOffHosts: [],
    failedHosts: [], version: '',
  };
  const insts = (app && Array.isArray(app.instances)) ? app.instances : [];
  out.n = insts.length;
  let wSum = 0;  // query-weighted avg-ms numerator
  const qSeriesList = [];  // per-host queries time-bucket arrays
  const bSeriesList = [];  // per-host blocked time-bucket arrays
  const pickTop = (cur, t) => (
    (t && t.name && (!cur || _num(t.count) > _num(cur.count)))
      ? {name: String(t.name), count: _num(t.count)} : cur);
  for (const inst of insts) {
    const d = (typeof this.appsAppData === 'function') ? this.appsAppData(inst) : null;
    if (d == null || d.__pending) {
      out.loading = true;
      continue;
    }
    if (d.__error || !d.ok) {
      out.failedHosts.push(inst.host_address || inst.host_id || '?');
      continue;
    }
    out.okN += 1;
    out.queries += _num(d.queries_today);
    out.blocked += _num(d.blocked_today);
    out.clients += _num(d.num_clients);
    out.safebrowsing += _num(d.safebrowsing_today);
    out.parental += _num(d.parental_today);
    out.rules = Math.max(out.rules, _num(d.blocklist_rules));
    wSum += _num(d.avg_processing_ms) * _num(d.queries_today);
    out.protTotal += 1;
    if (d.protection_enabled) {
      out.protOn += 1;
    } else {
      out.protOffHosts.push(inst.host_address || inst.host_id || '?');
    }
    if (!out.version && d.version) {
      out.version = String(d.version);
    }
    out.topBlocked = pickTop(out.topBlocked, d.top_blocked_domain);
    out.topQueried = pickTop(out.topQueried, d.top_queried_domain);
    out.topClient = pickTop(out.topClient, d.top_client);
    if (Array.isArray(d.queries_series) && d.queries_series.length) {
      qSeriesList.push(d.queries_series);
    }
    if (Array.isArray(d.blocked_series) && d.blocked_series.length) {
      bSeriesList.push(d.blocked_series);
    }
    // The fleet blocked-% trend is identical on every host — take the first.
    if (!out.trend && d.fleet_trend && typeof d.fleet_trend === 'object') {
      out.trend = d.fleet_trend;
    }
  }
  out.queriesSeries = _sumAlign(qSeriesList);
  out.blockedSeries = _sumAlign(bSeriesList);
  out.pct = out.queries > 0 ? (out.blocked / out.queries) * 100 : 0;
  out.avgMs = out.queries > 0 ? (wSum / out.queries) : 0;
  out.ready = out.okN > 0;
  return out;
}

// Element-wise sum a list of numeric arrays, right-aligned to the SHORTEST
// (the most-recent buckets line up across hosts whose stats windows differ).
// [] when there's nothing to sum.
function _sumAlign(arrays) {
  if (!Array.isArray(arrays) || !arrays.length) {
    return [];
  }
  let minLen = Infinity;
  for (const a of arrays) {
    if (Array.isArray(a) && a.length < minLen) {
      minLen = a.length;
    }
  }
  if (!isFinite(minLen) || minLen < 1) {
    return [];
  }
  const out = new Array(minLen).fill(0);
  for (const a of arrays) {
    const off = a.length - minLen;  // right-align (drop oldest extra buckets)
    for (let i = 0; i < minLen; i++) {
      out[i] += _num(a[off + i]);
    }
  }
  return out;
}

// SVG path for a numeric array over a 200x32 viewBox, normalised to an
// explicit `max` (so two series — queries + blocked — share one scale and
// blocked reads as a true proportion of queries). '' when < 2 points.
function adguardSeriesPath(arr, max) {
  if (!Array.isArray(arr) || arr.length < 2) {
    return '';
  }
  const width = 200, height = 32, n = arr.length;
  const top = (Number(max) > 0) ? Number(max) : Math.max(1, ...arr.map(_num));
  const stepX = width / Math.max(1, n - 1);
  let d = '';
  for (let i = 0; i < n; i++) {
    const x = (i * stepX).toFixed(1);
    const y = (height - (_num(arr[i]) / top) * height).toFixed(1);
    d += (i === 0 ? 'M' : 'L') + x + ',' + y + ' ';
  }
  return d.trim();
}

// Max across one-or-more numeric arrays (for the shared queries/blocked scale).
function adguardSeriesMax() {
  let m = 0;
  for (let a = 0; a < arguments.length; a++) {
    const arr = arguments[a];
    if (Array.isArray(arr)) {
      for (let i = 0; i < arr.length; i++) {
        const v = _num(arr[i]);
        if (v > m) {
          m = v;
        }
      }
    }
  }
  return m;
}

// Self-normalised SVG path for the blocked-% daily trend (numeric array).
// '' when < 2 points.
function adguardTrendPath(arr) {
  return adguardSeriesPath(arr, adguardSeriesMax(arr));
}

function adguardDisablePresets() {
  return DISABLE_PRESETS;
}

// True while a fleet skill is in flight for an app (disables buttons).
function adguardSkillBusy(app, skillId) {
  /* jshint validthis: true */
  const gid = (app && app.group_id) || '';
  return !!(this._adguardSkillBusy && this._adguardSkillBusy[gid + ':' + skillId]);
}

// Dispatch ONE fleet skill. The backend `run_skill` fans out to every
// AdGuard instance, so we just POST to the first credentialed instance.
// Destructive (disable*) skills confirm first (this is the Apps card,
// not the AI sidebar, so a SweetAlert confirm is fine here). On success
// every instance's data is force-refreshed so the aggregate updates.
async function adguardFleetSkill(app, skillId, opts) {
  /* jshint validthis: true */
  const o = opts || {};
  const insts = (app && Array.isArray(app.instances)) ? app.instances : [];
  const target = insts.find((x) => x && x.api_key_set) || insts[0];
  if (!target) {
    this.showToast(this.t('apps.adguard.no_instance') || 'No AdGuard instance to target', 'error');
    return;
  }
  const isDisable = (skillId === 'adguard_disable' || skillId.indexOf('adguard_disable_') === 0);
  if (isDisable && !o.skipConfirm && typeof this.confirmDialog === 'function') {
    const ok = await this.confirmDialog({
      title: this.t('apps.adguard.confirm_disable_title') || 'Disable AdGuard protection?',
      text: this.t('apps.adguard.confirm_disable_text')
        || 'This turns off DNS ad/tracker blocking across every AdGuard host.',
      confirmText: this.t('actions.disable') || 'Disable',
      danger: true,
    });
    if (!ok) {
      return;
    }
  }
  this._adguardSkillBusy = this._adguardSkillBusy || {};
  const gid = (app && app.group_id) || '';
  const busyKey = gid + ':' + skillId;
  if (this._adguardSkillBusy[busyKey]) {
    return;
  }
  this._adguardSkillBusy[busyKey] = true;
  try {
    // confirm:true — a destructive disable is gated by the SweetAlert above;
    // by the time we POST the operator has confirmed (or it's a non-
    // destructive fleet skill, for which the backend ignores the flag). The
    // backend's destructive-skill gate requires it.
    const r = await fetch('/api/services/' + encodeURIComponent(target.host_id)
      + '/' + encodeURIComponent(target.service_idx)
      + '/skill/' + encodeURIComponent(skillId),
      {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({confirm: true})
      });
    const j = await r.json().catch(() => ({}));
    if (r.ok && j && j.ok) {
      this.showToast((this.t('apps.adguard.action_ok') || 'Done')
        + (j.detail ? ' — ' + String(j.detail).split('\n')[0] : ''), 'success');
    } else {
      const detail = (j && (j.detail || j.error)) || ('HTTP ' + r.status);
      this.showToast((this.t('apps.adguard.action_failed') || 'Action failed') + ': ' + detail, 'error');
    }
  } catch (err) {
    this.showToast((this.t('apps.adguard.action_failed') || 'Action failed')
      + ': ' + ((err && err.message) || err), 'error');
  } finally {
    this._adguardSkillBusy[busyKey] = false;
    // Refresh every instance's data so the aggregate reflects the change.
    if (typeof this.loadAppData === 'function') {
      for (const inst of insts) {
        this.loadAppData(inst, true);
      }
    }
  }
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`.
export const extender = {
  slugs: ['adguard-home', 'adguardhome', 'adguard'],
  requiresApiKey: true,
  // AdGuard does NOT render per-instance extras — only the app-level
  // aggregated block. `instanceExtras: false` makes `anyAppExtrasMatch`
  // skip it in the per-instance extras loop; `appLevelExtras: true`
  // opts it into the app-level slot (`anyAppAggExtrasMatch`).
  instanceExtras: false,
  appLevelExtras: true,
  // The aggregated card is wide (stats grid + action rows).
  cardSpan(app) {
    return isAdguardApp(app) ? 2 : 1;
  },
};

export const helpers = {
  adguardIsApp: isAdguardApp,
  adguardAggregate: adguardAggregate,
  adguardInt: adguardInt,
  adguardPct: adguardPct,
  adguardMs: adguardMs,
  adguardSeriesPath: adguardSeriesPath,
  adguardSeriesMax: adguardSeriesMax,
  adguardTrendPath: adguardTrendPath,
  adguardDisablePresets: adguardDisablePresets,
  adguardSkillBusy: adguardSkillBusy,
  adguardFleetSkill: adguardFleetSkill,
};
