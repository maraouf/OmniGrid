// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection JSPotentiallyInvalidUsageOfThis,JSUnresolvedReference,JSUnresolvedVariable,JSUnresolvedFunction,OverlyComplexBooleanExpressionJS,ContinueStatementJS,JSValidateTypes,NegatedIfStatementJS
/* global fetch, Number, Math, isFinite, encodeURIComponent */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W116 */

// Per-app SPA module -- Pi-hole (v6 FTL).
//
// Fleet app, identical shape to AdGuard Home: the operator runs N
// Pi-holes and wants the COMBINED fleet stats in ONE card, not a row
// per host. So the extender sets `instanceExtras: false` (skip the
// per-instance extras loop) + `appLevelExtras: true` (render once per
// app via the app-level slot in `apps-card.html`). `piholeAggregate(app)`
// sums each instance's per-host `app-data` fetch client-side.
//
// Actions are fleet-wide (enable / disable / disable-for-X / refresh /
// re-enable). They dispatch ONE skill (the backend `run_skill` fans out
// to every instance), so the SPA targets the first credentialed instance
// and lets the module loop the fleet.
//
// Auth note: Pi-hole v6 uses a PASSWORD only (no username) — the chip's
// `api_key` field carries the password (see logic/apps/pihole.py). So the
// editor renders a single password input, and `requiresApiKey: true`.
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the
// full rationale -- same per-file conventions apply here.

// Timed-disable presets surfaced as buttons. The seconds map to the
// backend's `pihole_disable_<label>` skill ids (logic/apps/pihole.py
// DISABLE_PRESETS). Keep the two lists in lock-step.
const DISABLE_PRESETS = [
  {label: '1m', skill: 'pihole_disable_1m'},
  {label: '5m', skill: 'pihole_disable_5m'},
  {label: '10m', skill: 'pihole_disable_10m'},
  {label: '30m', skill: 'pihole_disable_30m'},
  {label: '1h', skill: 'pihole_disable_1h'},
  {label: '2h', skill: 'pihole_disable_2h'},
  {label: '24h', skill: 'pihole_disable_24h'},
];

function isPiholeApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'pihole' || slug === 'pi-hole' || slug === 'pihole-v6') {
    return true;
  }
  const name = String(app.name || '').toLowerCase();
  return (name.indexOf('pi-hole') !== -1 || name.indexOf('pihole') !== -1);
}

function _num(v) {
  const n = Number(v);
  return isFinite(n) ? n : 0;
}

// Format an integer count with thousands separators; '—' for nothing.
function piholeInt(v) {
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
function piholePct(v) {
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return n.toLocaleString(undefined, {maximumFractionDigits: 1}) + '%';
}

// Aggregate every instance's per-host app-data into ONE fleet summary.
// Reads `this.appsAppData(inst)` per instance (triggers the per-host
// fetch + returns cached data / {__pending} / {__error} / null). Returns
// a render-ready object the app-level extras partial binds to.
//   blocked / queries / clients -> SUM across hosts
//   blocklist rules             -> MAX across hosts (operator decision —
//                                  hosts share roughly the same lists)
//   top blocked domain          -> the single max-count across hosts
//   protection                  -> count of hosts with protection ON
// Pi-hole has NO avg-processing-ms metric (unlike AdGuard), so the
// aggregate omits it. Loading / failed hosts are tracked so the block
// can show partial totals honestly (footnote) instead of failing
// wholesale.
// noinspection OverlyLongFunctionJS,FunctionTooLongJS,JSFunctionTooLong
function piholeAggregate(app) {
  // Bound onto the Alpine component (merged via helpers), so `this` is the
  // component — JSHint can't infer the bind for a standalone function.
  /* jshint validthis: true */
  const out = {
    ready: false, loading: false,
    n: 0, okN: 0,
    queries: 0, blocked: 0, pct: 0, rules: 0, clients: 0,
    topBlocked: null, topQueried: null, topClient: null,
    queriesSeries: [], blockedSeries: [],
    trend: null,
    protOn: 0, protTotal: 0, protOffHosts: [],
    failedHosts: [], version: '',
    // P1 — query-weighted cache-vs-forwarded split + busiest upstream resolver.
    cachePct: 0, forwardedPct: 0, topUpstream: null,
    // P2 — merged top-blocked / top-permitted distributions (top 10 each).
    topBlockedList: [], topPermittedList: [],
  };
  const insts = (app && Array.isArray(app.instances)) ? app.instances : [];
  out.n = insts.length;
  const qSeriesList = [];
  const bSeriesList = [];
  let cacheWeighted = 0;  // P1 query-weighted cache% numerator
  let fwdWeighted = 0;
  const blockedAcc = {};  // P2 domain -> summed blocked count
  const permittedAcc = {};
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
    out.rules = Math.max(out.rules, _num(d.blocklist_rules));
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
    out.topUpstream = pickTop(out.topUpstream, d.top_upstream);
    cacheWeighted += _num(d.cache_pct) * _num(d.queries_today);
    fwdWeighted += _num(d.forwarded_pct) * _num(d.queries_today);
    if (Array.isArray(d.top_blocked_list)) {
      for (const row of d.top_blocked_list) {
        if (row && row.name) {
          blockedAcc[row.name] = (blockedAcc[row.name] || 0) + _num(row.count);
        }
      }
    }
    if (Array.isArray(d.top_permitted_list)) {
      for (const row of d.top_permitted_list) {
        if (row && row.name) {
          permittedAcc[row.name] = (permittedAcc[row.name] || 0) + _num(row.count);
        }
      }
    }
    if (Array.isArray(d.queries_series) && d.queries_series.length) {
      qSeriesList.push(d.queries_series);
    }
    if (Array.isArray(d.blocked_series) && d.blocked_series.length) {
      bSeriesList.push(d.blocked_series);
    }
    if (!out.trend && d.fleet_trend && typeof d.fleet_trend === 'object') {
      out.trend = d.fleet_trend;
    }
  }
  out.queriesSeries = _sumAlign(qSeriesList);
  out.blockedSeries = _sumAlign(bSeriesList);
  out.pct = out.queries > 0 ? (out.blocked / out.queries) * 100 : 0;
  // P1 — query-weighted cache / forwarded percentages across the fleet.
  out.cachePct = out.queries > 0 ? (cacheWeighted / out.queries) : 0;
  out.forwardedPct = out.queries > 0 ? (fwdWeighted / out.queries) : 0;
  // P2 — finalise the merged top-blocked / top-permitted distributions.
  const _finalise = (acc) => Object.keys(acc)
    .map((name) => ({name: name, count: acc[name]}))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);
  out.topBlockedList = _finalise(blockedAcc);
  out.topPermittedList = _finalise(permittedAcc);
  out.ready = out.okN > 0;
  return out;
}

// Element-wise sum a list of numeric arrays, right-aligned to the SHORTEST
// (the most-recent bins line up across hosts whose history windows differ).
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
    const off = a.length - minLen;
    for (let i = 0; i < minLen; i++) {
      out[i] += _num(a[off + i]);
    }
  }
  return out;
}

// SVG path for a numeric array over a 200x32 viewBox, normalised to an explicit
// `max` (so queries + blocked share one scale). '' when < 2 points.
function piholeSeriesPath(arr, max) {
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
function piholeSeriesMax() {
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

// Self-normalised SVG path for the blocked-% daily trend. '' when < 2 points.
function piholeTrendPath(arr) {
  return piholeSeriesPath(arr, piholeSeriesMax(arr));
}

function piholeDisablePresets() {
  return DISABLE_PRESETS;
}

// True while a fleet skill is in flight for an app (disables buttons).
function piholeSkillBusy(app, skillId) {
  /* jshint validthis: true */
  const gid = (app && app.group_id) || '';
  return !!(this._piholeSkillBusy && this._piholeSkillBusy[gid + ':' + skillId]);
}

// Dispatch ONE fleet skill. The backend `run_skill` fans out to every
// Pi-hole instance, so we just POST to the first credentialed instance.
// Destructive (disable*) skills confirm first (this is the Apps card,
// not the AI sidebar, so a SweetAlert confirm is fine here). On success
// every instance's data is force-refreshed so the aggregate updates.
async function piholeFleetSkill(app, skillId, opts) {
  /* jshint validthis: true */
  const o = opts || {};
  const insts = (app && Array.isArray(app.instances)) ? app.instances : [];
  const target = insts.find((x) => x && x.api_key_set) || insts[0];
  if (!target) {
    this.showToast(this.t('apps.pihole.no_instance') || 'No Pi-hole instance to target', 'error');
    return;
  }
  const isDisable = (skillId === 'pihole_disable' || skillId.indexOf('pihole_disable_') === 0);
  if (isDisable && !o.skipConfirm && typeof this.confirmDialog === 'function') {
    const ok = await this.confirmDialog({
      title: this.t('apps.pihole.confirm_disable_title') || 'Disable Pi-hole blocking?',
      text: this.t('apps.pihole.confirm_disable_text')
        || 'This turns off DNS ad/tracker blocking across every Pi-hole host.',
      confirmText: this.t('actions.disable') || 'Disable',
      danger: true,
    });
    if (!ok) {
      return;
    }
  }
  this._piholeSkillBusy = this._piholeSkillBusy || {};
  const gid = (app && app.group_id) || '';
  const busyKey = gid + ':' + skillId;
  if (this._piholeSkillBusy[busyKey]) {
    return;
  }
  this._piholeSkillBusy[busyKey] = true;
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
      this.showToast((this.t('apps.pihole.action_ok') || 'Done')
        + (j.detail ? ' — ' + String(j.detail).split('\n')[0] : ''), 'success');
    } else {
      const detail = (j && (j.detail || j.error)) || ('HTTP ' + r.status);
      this.showToast((this.t('apps.pihole.action_failed') || 'Action failed') + ': ' + detail, 'error');
    }
  } catch (err) {
    this.showToast((this.t('apps.pihole.action_failed') || 'Action failed')
      + ': ' + ((err && err.message) || err), 'error');
  } finally {
    this._piholeSkillBusy[busyKey] = false;
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
  slugs: ['pihole', 'pi-hole', 'pihole-v6'],
  requiresApiKey: true,
  // Pi-hole does NOT render per-instance extras — only the app-level
  // aggregated block. `instanceExtras: false` makes `anyAppExtrasMatch`
  // skip it in the per-instance extras loop; `appLevelExtras: true`
  // opts it into the app-level slot (`anyAppAggExtrasMatch`).
  instanceExtras: false,
  appLevelExtras: true,
  // The aggregated card is wide (stats grid + action rows).
  cardSpan(app) {
    return isPiholeApp(app) ? 2 : 1;
  },
};

export const helpers = {
  piholeIsApp: isPiholeApp,
  piholeAggregate: piholeAggregate,
  piholeInt: piholeInt,
  piholePct: piholePct,
  piholeSeriesPath: piholeSeriesPath,
  piholeSeriesMax: piholeSeriesMax,
  piholeTrendPath: piholeTrendPath,
  piholeDisablePresets: piholeDisablePresets,
  piholeSkillBusy: piholeSkillBusy,
  piholeFleetSkill: piholeFleetSkill,
};
