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
      // __nokey sentinel — the app needs credentials that weren't set, so the
      // fetch was skipped. Self-heal: once the operator saves a key
      // (api_key_set flips true) drop the sentinel + re-fetch; otherwise keep
      // showing the card's "no key" state (return null = data gate false).
      if (v && typeof v === 'object' && v.__nokey) {
        if (!this._appRequiresKeyButUnset(inst)) {
          delete this._appsDataCache[key];
          this.loadAppData(inst, false);
        }
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

  // ---- Generic per-app DIAGNOSTICS helpers — read the standard `out._debug`
  // block ({endpoints, hint, notes}) that any app's fetch_data can stamp (via
  // logic/apps/_common.py:DebugRecorder). The generic drawer debug panel
  // (_components/apps/_debug_panel.html) renders them uniformly, so EVERY app
  // card can self-explain WHY a value is empty / 0 (HTTP status + body snippet
  // + a self-diagnosed hint) without the operator reading logs. ----

  // Per-request diagnostics ([{label, method, path, status, rows, ok, snippet}],
  // [] when the app stamps no _debug).
  appsDebug(inst) {
    const d = this.appsAppData(inst);
    const dbg = (d && d._debug && typeof d._debug === 'object') ? d._debug : null;
    return (dbg && Array.isArray(dbg.endpoints)) ? dbg.endpoints : [];
  },

  // Self-diagnosed actionable hint ('' when none) — e.g. "Services: HTTP 403 —
  // grant the API user the 'Status: Services' privilege".
  appsDebugHint(inst) {
    const d = this.appsAppData(inst);
    const dbg = (d && d._debug && typeof d._debug === 'object') ? d._debug : null;
    return (dbg && typeof dbg.hint === 'string') ? dbg.hint : '';
  },

  // Free-form diagnostic notes ([] when none).
  appsDebugNotes(inst) {
    const d = this.appsAppData(inst);
    const dbg = (d && d._debug && typeof d._debug === 'object') ? d._debug : null;
    return (dbg && Array.isArray(dbg.notes)) ? dbg.notes : [];
  },

  // True when this instance has ANY diagnostics — gates the diagnostics section
  // inside the app drawer's existing debug pane so apps that stamp no _debug
  // render nothing.
  appsHasDebug(inst) {
    return this.appsDebug(inst).length > 0
      || !!this.appsDebugHint(inst)
      || this.appsDebugNotes(inst).length > 0;
  },

  // ---- Generic *arr storage helpers (shared by the radarr / sonarr / lidarr
  // / readarr extras partials via _components/apps/_arr_storage.html). They
  // read the standard {path, free_gb, total_gb} disk shape every *arr
  // fetch_data emits under `disks`, replacing the per-app
  // radarrDisks / sonarrSize / lidarrDiskUsedPct duplicates. ----

  // Per-mount disk list ([] when none).
  appsDisks(inst) {
    const d = this.appsAppData(inst);
    return (d && Array.isArray(d.disks)) ? d.disks : [];
  },

  // Format a GiB float as a human size, promoting to TiB at >= 1024 GiB
  // (matches the *arr GiB / TiB display). '—' for missing / zero.
  appsFmtSize(gib) {
    const n = Number(gib);
    if (gib == null || !isFinite(n) || n <= 0) {
      return '—';
    }
    if (n >= 1024) {
      return (n / 1024).toLocaleString(undefined, {maximumFractionDigits: 1}) + ' TiB';
    }
    return n.toLocaleString(undefined, {maximumFractionDigits: 1}) + ' GiB';
  },

  // Used-percent for a mount's usage bar (0..100); 0 when total is unknown.
  appsDiskUsedPct(m) {
    if (!m) {
      return 0;
    }
    const total = Number(m.total_gb);
    const free = Number(m.free_gb);
    if (!isFinite(total) || total <= 0 || !isFinite(free)) {
      return 0;
    }
    return Math.max(0, Math.min(100, Math.round((1 - free / total) * 100)));
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
    // __nokey → treat as idle so the card's "no key set" branch (gated on
    // idle + !api_key_set) renders instead of a misleading error/loading.
    if (v && typeof v === 'object' && v.__nokey) {
      return 'idle';
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

  // True when the chip's app REQUIRES an api_key / credentials but none is set
  // yet. Used to skip a doomed /app-data fetch (the backend would 400 with a
  // config error) and instead let the card show its own "no key set" state —
  // which it already gates on `appsAppDataStatus === 'idle' && !api_key_set`.
  // Without this guard a wide-span card (cardSpan >= 2, e.g. RustDesk) shows
  // extras by DEFAULT (appsShowExtras → true) and fires the fetch even when the
  // operator never entered credentials, producing a console 400 on every load.
  _appRequiresKeyButUnset(inst) {
    if (!inst || inst.api_key_set) {
      return false;
    }
    const slug = inst.catalog_slug || inst.catalog_name || inst.name || '';
    return !!this.appsTemplateRequiresApiKey(slug);
  },

  async loadAppData(inst, force) {
    const key = this.appsAppDataKey(inst);
    if (!key) {
      return;
    }
    // Credentials-required-but-unset → don't fetch (it would 400). Stamp a
    // __nokey sentinel so the card renders its "no key" state and we don't
    // re-trigger the fetch on every render. Self-heals once a key is saved
    // (appsAppData drops the sentinel + re-fetches when api_key_set flips).
    if (this._appRequiresKeyButUnset(inst)) {
      if (!this._appsDataCache) {
        this._appsDataCache = {};
      }
      this._appsDataCache[key] = {__nokey: true};
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
    // {pending, ok, status, detail} shape so the SHARED og-test-connection
    // component (used by Portainer / OIDC / etc.) drives the per-app editor
    // button + result box identically — pending=true shows the spinner +
    // loading box, then the real outcome replaces it.
    this.appsInstanceTestResult = {pending: true};
    try {
      const r = await fetch('/api/services/'
        + encodeURIComponent(f.host_id) + '/'
        + encodeURIComponent(f.service_idx) + '/test-credential', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        // `username` is forwarded for apps with multi-field credentials
        // (e.g. AdGuard Home: username + password). Single-secret apps
        // (Speedtest) ignore it. `url` is forwarded so the backend can
        // resolve the base URL from the LIVE editor value (test-before-save)
        // instead of the stale saved chip — without it, testing a brand-new
        // or just-edited instance reports "no upstream URL configured" until
        // the operator saves first. The backend reads candidate_key from
        // `api_key` and any extra fields from the same payload.
        body: JSON.stringify({api_key: f.api_key || '', username: f.username || '', url: f.url || '', verify_tls: !!f.verify_tls, totp_secret: f.totp_secret || ''}),
      });
      const j = await r.json().catch(() => ({}));
      const _ok = !!(r.ok && j && j.ok);
      this.appsInstanceTestResult = {
        pending: false,
        ok: _ok,
        status: (j && j.status) || r.status,
        detail: (j && (j.detail || j.error)) || (r.ok ? 'OK' : 'HTTP ' + r.status),
      };
      // Optimistically stamp the "✓ Last tested Xm ago" chip on a passing
      // test (the backend also persists chip.last_test_ok_ts, hydrated via
      // iter_instances on the next load) so the chip updates immediately.
      if (_ok && this.appsInstanceEditForm) {
        const _ts = Math.floor(Date.now() / 1000);
        this.appsInstanceEditForm.last_test_ok_ts = _ts;
        // ALSO stamp the underlying instances list so REOPENING the editor
        // from the Admin → Apps list (which doesn't re-fetch) shows the chip
        // immediately — without this, the form's optimistic value was lost on
        // close and the stale list row showed nothing until a full reload.
        const _f = this.appsInstanceEditForm;
        for (const _inst of (this.appsInstances || [])) {
          if (_inst && _inst.host_id === _f.host_id && _inst.service_idx === _f.service_idx) {
            _inst.last_test_ok_ts = _ts;
            break;
          }
        }
      }
    } catch (err) {
      this.appsInstanceTestResult = {
        pending: false,
        ok: false,
        status: 0,
        detail: (err && err.message) ? err.message : String(err),
      };
    } finally {
      this.appsInstanceTestBusy = false;
    }
  },
  appsInstanceTestBusy: false,
  appsInstanceTestResult: null,
};
