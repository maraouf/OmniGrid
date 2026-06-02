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
// Per-app per-instance data cache for the expanded-card extras
// surface (appsAppData + status/error sentinel helpers + the
// loadAppData fetcher) and the per-app upstream-credential test
// (testInstanceCredential) that the per-app editor partials
// dispatch. Split from app-apps.js when it crossed the 3000-line
// split-candidate threshold.

export default {
  appsAppDataKey(inst) {
    if (!inst || !inst.host_id || inst.service_idx == null) {
      return '';
    }
    return inst.host_id + ':' + String(inst.service_idx);
  },

  appsAppData(inst) {
    const key = this.appsAppDataKey(inst);
    if (!key) {
      return null;
    }
    if (!this._appsDataCache) {
      this._appsDataCache = {};
    }
    if (key in this._appsDataCache) {
      const v = this._appsDataCache[key];
      // Sentinel filtering — internal pending / error markers are
      // hidden from the template gate (which expects truthy = render
      // the live data card). The dedicated `appsAppDataStatus(inst)`
      // helper surfaces them for the loading / error empty states.
      if (v && typeof v === 'object' && v.__pending) {
        return null;
      }
      if (v && typeof v === 'object' && v.__error) {
        return null;
      }
      // Stale-while-revalidate (perf finding 4): if the cached value has aged
      // past the operator TTL (me.client_config.apps_extras_ttl_seconds;
      // 0 = off → fetch-once), kick a background force-refetch but RETURN the
      // stale value NOW so the card doesn't re-shimmer. Pre-stamp the fetch
      // time before firing so per-render re-reads during the in-flight
      // revalidate don't fan out a storm (force=true bypasses the pending
      // guard in loadAppData).
      try {
        const ttl = (this.me && this.me.client_config
          && this.me.client_config.apps_extras_ttl_seconds) || 0;
        const ts = (this._appsDataFetchedAt && this._appsDataFetchedAt[key]) || 0;
        if (ttl > 0 && ts && (Date.now() - ts) > ttl * 1000) {
          if (!this._appsDataFetchedAt) {
            this._appsDataFetchedAt = {};
          }
          this._appsDataFetchedAt[key] = Date.now();
          this.loadAppData(inst, true);
        }
      } catch (_e) { /* SWR is best-effort — stale value still renders */
      }
      return v;
    }
    this.loadAppData(inst, false);
    return null;
  },

  // Status of the per-app data fetch for one instance. Drives the
  // template's empty-state branches: `pending` shows "Loading...",
  // `error` shows the upstream failure detail, `ok` is the live
  // card, `idle` is "no api_key set" / "not yet requested".
  appsAppDataStatus(inst) {
    const key = this.appsAppDataKey(inst);
    if (!key || !this._appsDataCache) {
      return 'idle';
    }
    const v = this._appsDataCache[key];
    if (v && typeof v === 'object' && v.__pending) {
      return 'pending';
    }
    if (v && typeof v === 'object' && v.__error) {
      return 'error';
    }
    if (v && typeof v === 'object') {
      return 'ok';
    }
    return 'idle';
  },

  // Human-readable error detail for the per-app data fetch — only
  // populated when appsAppDataStatus(inst) === 'error'. Returns ''
  // otherwise so the template's empty-state branch can render
  // unconditionally.
  appsAppDataError(inst) {
    const key = this.appsAppDataKey(inst);
    if (!key || !this._appsDataCache) {
      return '';
    }
    const v = this._appsDataCache[key];
    if (v && typeof v === 'object' && v.__error) {
      return String(v.__error || '').slice(0, 240);
    }
    return '';
  },

  async loadAppData(inst, force) {
    const key = this.appsAppDataKey(inst);
    if (!key) {
      return;
    }
    if (!this._appsDataPending) {
      this._appsDataPending = {};
    }
    if (this._appsDataPending[key] && !force) {
      return;
    }
    this._appsDataPending[key] = true;
    // Stamp a __pending sentinel into the cache so the template's
    // status helper renders the "Loading..." empty state immediately
    // (vs. cache-miss → null → re-trigger loadAppData → loop). Once
    // the fetch lands the sentinel is replaced by the real response
    // or an __error sentinel.
    if (!this._appsDataCache) {
      this._appsDataCache = {};
    }
    if (!(key in this._appsDataCache)) {
      this._appsDataCache[key] = {__pending: true};
    }
    try {
      const url = '/api/services/' + encodeURIComponent(inst.host_id)
        + '/' + encodeURIComponent(inst.service_idx) + '/app-data'
        + (force ? '?force=true' : '');
      const r = await fetch(url, {cache: 'no-store'});
      if (!r.ok) {
        // Try to surface a useful error detail from the backend's
        // JSON body; fall through to status text on parse failure.
        let detail = 'HTTP ' + r.status;
        try {
          const j = await r.json();
          if (j && j.detail) {
            detail = String(j.detail);
          }
        } catch (_e) {
          // body wasn't JSON; keep the HTTP status fallback.
        }
        this._appsDataCache[key] = {__error: detail};
      } else {
        this._appsDataCache[key] = await r.json();
        // Stamp the fetch time for the stale-while-revalidate check in
        // appsAppData() (perf finding 4).
        if (!this._appsDataFetchedAt) {
          this._appsDataFetchedAt = {};
        }
        this._appsDataFetchedAt[key] = Date.now();
      }
    } catch (err) {
      this._appsDataCache[key] = {__error: (err && err.message) ? err.message : String(err)};
    } finally {
      this._appsDataPending[key] = false;
    }
  },

  // Test the per-app credential for the currently-edited chip.
  // Routed through the generic `/api/services/{host_id}/
  // {service_idx}/test-credential` dispatcher, which dispatches
  // to the chip's per-app module (slug-keyed via
  // `logic/apps/registry`). The candidate api_key is shipped in
  // the request body; blank falls back to the stored value on
  // the backend side. Mirrors the canonical test-before-save
  // pattern (admin tabs with a probe path gate Save behind a
  // successful test).
  async testInstanceCredential() {
    const f = this.appsInstanceEditForm;
    if (!f || !f.host_id || f.service_idx < 0) {
      return;
    }
    this.appsInstanceTestBusy = true;
    this.appsInstanceTestResult = null;
    try {
      const r = await fetch('/api/services/'
        + encodeURIComponent(f.host_id) + '/'
        + encodeURIComponent(f.service_idx) + '/test-credential', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        // `username` is forwarded for apps with multi-field credentials
        // (e.g. AdGuard Home: username + password). Single-secret apps
        // (Speedtest) ignore it. The backend reads candidate_key from
        // `api_key` and any extra fields from the same payload.
        body: JSON.stringify({api_key: f.api_key || '', username: f.username || ''}),
      });
      const j = await r.json().catch(() => ({}));
      this.appsInstanceTestResult = {
        ok: !!(r.ok && j && j.ok),
        detail: (j && (j.detail || j.error)) || (r.ok ? 'OK' : 'HTTP ' + r.status),
      };
    } catch (err) {
      this.appsInstanceTestResult = {
        ok: false,
        detail: (err && err.message) ? err.message : String(err),
      };
    } finally {
      this.appsInstanceTestBusy = false;
    }
  },
  appsInstanceTestBusy: false,
  appsInstanceTestResult: null,
};
