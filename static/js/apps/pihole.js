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
    topBlocked: null,
    protOn: 0, protTotal: 0, protOffHosts: [],
    failedHosts: [], version: '',
  };
  const insts = (app && Array.isArray(app.instances)) ? app.instances : [];
  out.n = insts.length;
  for (const inst of insts) {
    const d = (typeof this.appsAppData === 'function') ? this.appsAppData(inst) : null;
    if (d == null || d.__pending) {
      out.loading = true;
      continue;
    }
    if (d.__error) {
      out.failedHosts.push(inst.host_address || inst.host_id || '?');
      continue;
    }
    if (!d.ok) {
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
    const t = d.top_blocked_domain;
    if (t && t.name && (!out.topBlocked || _num(t.count) > _num(out.topBlocked.count))) {
      out.topBlocked = {name: String(t.name), count: _num(t.count)};
    }
  }
  out.pct = out.queries > 0 ? (out.blocked / out.queries) * 100 : 0;
  out.ready = out.okN > 0;
  return out;
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
    const r = await fetch('/api/services/' + encodeURIComponent(target.host_id)
      + '/' + encodeURIComponent(target.service_idx)
      + '/skill/' + encodeURIComponent(skillId), {method: 'POST'});
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
  piholeDisablePresets: piholeDisablePresets,
  piholeSkillBusy: piholeSkillBusy,
  piholeFleetSkill: piholeFleetSkill,
};
