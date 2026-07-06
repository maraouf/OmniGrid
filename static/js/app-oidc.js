// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ExceptionCaughtLocallyJS,JSReusedLocalVariable,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,RedundantLocalVariableJS
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSCheckNamingConventionsInspection,UnnecessaryLocalVariableJS
// Sibling-file canonical noinspection block — same shape as
// app-admin.js / app-charts.js / app-ai.js / app-stats.js so the
// suppressed warning classes stay consistent across the SPA. Real
// bugs (typos / dead assignments / wrong types) are fixed inline,
// NOT suppressed.
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Authentik / OIDC integration (Admin → Authentik OIDC).
//
// All OIDC settings are DB-backed, edited via this form. The flow
// follows the canonical Test-before-Save gate pattern (see the project conventions):
// snapshot the form on Test success, compare on every keystroke, lock
// Save until snapshot matches live form.


export default {
  oidcSaving: false,           // Admin → OIDC
  // Multi-provider OIDC settings. The registry can define N providers
  // (Authentik = group-mode, UnifiedSSO = role-mode); the admin tab shows a
  // provider selector and binds ONE form (`oidcForm`) + status (`oidcStatus`)
  // to the SELECTED provider. Per-provider forms / baselines / passed-test
  // snapshots live in the maps below, keyed by provider id, so switching the
  // selector preserves unsaved edits + the Test-before-Save gate per provider.
  // Client secret is write-only — never populated back from the server; blank
  // on save = "keep existing".
  oidcProviderId: 'authentik',
  oidcProviders: [],           // list of status blocks (descriptors) from /api/settings
  oidcStatuses: {},            // id -> server status block
  oidcForms: {},               // id -> editable form object
  oidcStatus: null,            // == oidcStatuses[oidcProviderId]
  oidcForm: {
    enabled: false, issuer_url: '', client_id: '', client_secret: '',
    redirect_uri: '', scopes: 'openid email profile groups',
    admin_group: 'omnigrid-admins', group_case_sensitive: true, verify_tls: true,
  },
  oidcTestResult: null,
  // Per-provider Test-before-Save gating + dirty baseline (id -> snapshot).
  _oidcLastPassedTest: {},
  _oidcBaseline: {},
  // Dynamic-client-registration (RFC 7591) input state for the selected
  // provider's Auto-register button.
  oidcAutoReg: { token: '', running: false },

  // ---- provider-scoped helpers --------------------------------------------
  oidcSelectedMode() {
    return (this.oidcStatus && this.oidcStatus.admin_mode) || 'group';
  },
  oidcSupportsRegistration() {
    return !!(this.oidcStatus && this.oidcStatus.supports_registration);
  },
  // Settings-key prefix mirrors the backend registry (authentik = empty
  // prefix / bare oidc_* keys; every other provider = <id>_).
  _oidcKeyPrefix() {
    return this.oidcProviderId === 'authentik' ? '' : (this.oidcProviderId + '_');
  },
  _oidcFormFromStatus(st) {
    st = st || {};
    const f = {
      enabled: !!st.enabled,
      issuer_url: st.issuer_url || '',
      client_id: st.client_id || '',
      client_secret: '',  // write-only — never prefill
      redirect_uri: st.redirect_uri || st.redirect_uri_default || '',
      scopes: st.scopes || 'openid email profile groups',
      verify_tls: st.verify_tls !== false,
    };
    if ((st.admin_mode || 'group') === 'group') {
      f.admin_group = st.admin_group || 'omnigrid-admins';
      f.group_case_sensitive = st.group_case_sensitive !== false;
    } else {
      f.admin_role_claim = st.admin_role_claim || 'role';
      f.admin_role_value = st.admin_role_value || 'ADMIN';
    }
    return f;
  },
  // Rebuild the per-provider maps from a /api/settings response. Falls back
  // to the legacy single `d.oidc` block when an older backend doesn't send
  // `d.oidc_providers`. Called from loadSettings (app-admin.js).
  _hydrateOidcProviders(d) {
    let list = Array.isArray(d.oidc_providers) ? d.oidc_providers : null;
    if (!list && d.oidc) {
      list = [Object.assign({ id: 'authentik', label: 'Authentik', icon: 'authentik',
        admin_mode: 'group', supports_registration: false }, d.oidc)];
    }
    list = list || [];
    this.oidcProviders = list;
    this.oidcStatuses = {};
    this.oidcForms = {};
    for (const st of list) {
      this.oidcStatuses[st.id] = st;
      this.oidcForms[st.id] = this._oidcFormFromStatus(st);
    }
    // Keep the current selection if it still exists, else default.
    if (!this.oidcStatuses[this.oidcProviderId]) {
      this.oidcProviderId = list.length ? list[0].id : 'authentik';
    }
    this.oidcStatus = this.oidcStatuses[this.oidcProviderId] || null;
    this.oidcForm = this.oidcForms[this.oidcProviderId]
      || this._oidcFormFromStatus(this.oidcStatus || {});
    this.oidcTestResult = null;
    this.oidcAutoReg = { token: '', running: false };
    // Capture the dirty baseline for every provider.
    this._oidcBaseline = {};
    const savedId = this.oidcProviderId;
    for (const st of list) {
      this.oidcProviderId = st.id;
      this.oidcStatus = this.oidcStatuses[st.id];
      this.oidcForm = this.oidcForms[st.id];
      this._oidcBaseline[st.id] = this._oidcSnapshot();
    }
    this.oidcProviderId = savedId;
    this.oidcStatus = this.oidcStatuses[savedId] || null;
    this.oidcForm = this.oidcForms[savedId] || this.oidcForm;
  },
  selectOidcProvider(id) {
    if (!id || id === this.oidcProviderId) {
      return;
    }
    // Persist current edits back into the map so switching back keeps them.
    this.oidcForms[this.oidcProviderId] = this.oidcForm;
    this.oidcProviderId = id;
    this.oidcStatus = this.oidcStatuses[id] || null;
    this.oidcForm = this.oidcForms[id] || this._oidcFormFromStatus(this.oidcStatus || {});
    this.oidcTestResult = null;
    this.oidcAutoReg = { token: '', running: false };
  },

  async saveOidcSettings() {
    if (this.oidcSaving) {
      return;
    }
    this.oidcSaving = true;
    try {
      await this._saveOidcSettingsImpl();
    } finally {
      this.oidcSaving = false;
    }
  },
  async _saveOidcSettingsImpl() {
    // Build the SELECTED provider's namespaced settings keys. `K(name)`
    // resolves to oidc_<name> for Authentik and oidc_<id>_<name> otherwise,
    // matching the backend registry + SettingsIn field names exactly.
    const prefix = this._oidcKeyPrefix();
    const K = (n) => 'oidc_' + prefix + n;
    const f = this.oidcForm;
    const body = {};
    body[K('enabled')] = !!f.enabled;
    body[K('issuer_url')] = (f.issuer_url || '').trim();
    body[K('client_id')] = (f.client_id || '').trim();
    body[K('redirect_uri')] = (f.redirect_uri || '').trim();
    body[K('scopes')] = (f.scopes || '').trim();
    body[K('verify_tls')] = !!f.verify_tls;
    if (this.oidcSelectedMode() === 'group') {
      body[K('admin_group')] = (f.admin_group || '').trim();
      body[K('group_case_sensitive')] = !!f.group_case_sensitive;
    } else {
      body[K('admin_role_claim')] = (f.admin_role_claim || '').trim();
      body[K('admin_role_value')] = (f.admin_role_value || '').trim();
    }
    // Client secret: only send when the admin actually typed one.
    // Empty / whitespace-only = "keep current" per the backend contract.
    if (f.client_secret && f.client_secret.trim()) {
      body[K('client_secret')] = f.client_secret;
    }
    // OIDC-scoped tunables — included in the same POST body so editing
    // them flips dirty + saves through THIS section, not the generic
    // Admin → Config form. Validate int + bounds locally first so a
    // typo'd value doesn't make the whole Save fail; the backend
    // re-clamps but explicit local errors are friendlier.
    const tf = this.tuningForm || {};
    const tunableKeys = ['tuning_oidc_http_timeout_seconds'];
    for (const k of tunableKeys) {
      const raw = tf[k];
      if (raw == null || String(raw).trim() === '') {
        continue;
      }
      const n = parseInt(raw, 10);
      // `tuningEffective` (NOT `tuningBounds`) is the canonical
      // shape — same typo that pre-fix existed in app-portainer.js.
      // `tuningBounds` doesn't exist on the component so the
      // validator silently never bounds-checked any value
      // (out-of-range tunables would POST through without front-end
      // rejection; backend's `tuning_int` clamp was the only
      // defence). Shape: `{min, max, default, effective, source}`
      // per tunable.
      const eff = (this.tuningEffective || {})[k] || {};
      if (!Number.isFinite(n)
        || (Number.isFinite(eff.min) && n < eff.min)
        || (Number.isFinite(eff.max) && n > eff.max)) {
        this.showToast(this.t('toasts.save_failed'), 'error');
        return;
      }
      body[k] = String(n);
    }
    try {
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (r.ok) {
        // loadSettings() below re-captures the baseline; nothing to
        // clear here under the unified pattern.
        this.showToast(this.t('toasts.oidc_saved'));
        await this.loadSettings();
        // Re-read tuning baseline so oidcDirty() flips back to false
        // after a successful save that included tunable changes.
        await this.loadTuning();
        this.oidcTestResult = null;
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  async testOidcConnection() {
    this.oidcTestResult = {pending: true};
    // Snapshot the form NOW so we can stamp it on success — same
    // pattern as `testPortainerConnection`.
    const probedSnapshot = this._oidcSnapshot();
    try {
      // Send the in-flight verify_tls so an admin can flip the
      // checkbox OFF and Test a self-signed issuer before saving.
      // Backend falls back to the saved DB value when the key is
      // missing.
      const r = await fetch('/api/oidc/' + encodeURIComponent(this.oidcProviderId) + '/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          issuer_url: (this.oidcForm.issuer_url || '').trim(),
          verify_tls: !!this.oidcForm.verify_tls,
        }),
      });
      // HTTP non-2xx (proxy 504, FastAPI 422 / 500, etc.) goes
      // through fmtResponseError so HTML proxy bodies surface their
      // <title> instead of dumping raw HTML, and Pydantic validation
      // arrays render as "loc: msg" strings instead of "[object
      // Object]". Identical pattern to testPulseConnection /
      // testPortainerConnection — Authentik fails the same way they
      // do (the backend can crash, NPM can 504, etc.).
      if (!r.ok) {
        const detail = await this.fmtResponseError(r);
        // `pending: false` is EXPLICIT on every branch so the
        // disabled-binding `oidcTestResult.pending === true` reads
        // cleanly. Pre-fix the result object omitted `pending`
        // entirely, leaving the field `undefined` — falsy in JS
        // but ambiguous on visual inspection AND brittle if any
        // downstream consumer naively reads `.pending` and gets
        // undefined-instead-of-false. Same shape applied to the
        // 200-path + the catch-block below.
        this.oidcTestResult = {
          pending: false,
          ok: false,
          status: r.status,
          detail: detail || this.t('toasts_extra.test_result_failed'),
        };
        return;
      }
      const j = await r.json().catch(() => ({}));
      // Every test_discovery branch returns a `detail` string; the
      // fallback i18n key only fires if the backend regresses or
      // returns an empty body. Without it the user saw a bare ✗
      // glyph with no message — actively hostile UX.
      this.oidcTestResult = {
        pending: false,
        ok: !!j.ok,
        status: j.status || 0,
        detail: j.detail
          || this.t(j.ok ? 'toasts_extra.test_result_ok' : 'toasts_extra.test_result_failed'),
      };
      if (j && j.ok) {
        this._oidcLastPassedTest[this.oidcProviderId] = probedSnapshot;
        this.recordTestSuccess('oidc');
      }
    } catch (_) {
      this.oidcTestResult = {
        pending: false,
        ok: false,
        status: 0,
        detail: this.t('toasts.network_error'),
      };
    }
  },
  // Save-button gate for OIDC — same shape as `canSavePortainer()`.
  // When the OIDC master toggle is OFF, Save is unconstrained
  // (no IdP probe will run anyway). When ON, the operator must
  // run a successful Test against the CURRENT form values before
  // Save unlocks; any edit after a passing Test re-locks Save by
  // mutating `_oidcSnapshot()` away from `_oidcLastPassedTest`.
  canSaveOidc() {
    const enabled = !!(this.oidcForm && this.oidcForm.enabled);
    if (!enabled) {
      return true;
    }
    const passed = this._oidcLastPassedTest[this.oidcProviderId] || '';
    return !!passed && passed === this._oidcSnapshot();
  },
  _oidcSnapshot() {
    const f = this.oidcForm || {};
    const s = this.oidcStatus || {};
    const mode = this.oidcSelectedMode();
    const snap = {
      id: this.oidcProviderId,
      enabled: !!f.enabled,
      issuer: (f.issuer_url || '').trim(),
      client_id: (f.client_id || '').trim(),
      secret: f.client_secret ? '<set>' : '',
      redirect: (f.redirect_uri || '').trim(),
      scopes: (f.scopes || '').trim(),
      verify_tls: f.verify_tls !== false,
      baseEnabled: !!s.enabled,
      baseIssuer: s.issuer_url || '',
      baseCid: s.client_id || '',
      baseRedir: s.redirect_uri || '',
      baseScopes: s.scopes || '',
      baseVerify: s.verify_tls !== false,
    };
    if (mode === 'group') {
      snap.admin_group = (f.admin_group || '').trim();
      snap.group_case_sensitive = f.group_case_sensitive !== false;
      snap.baseGrp = s.admin_group || '';
      snap.baseGroupCS = s.group_case_sensitive !== false;
    } else {
      snap.admin_role_claim = (f.admin_role_claim || '').trim();
      snap.admin_role_value = (f.admin_role_value || '').trim();
      snap.baseRoleClaim = s.admin_role_claim || '';
      snap.baseRoleValue = s.admin_role_value || '';
    }
    return JSON.stringify(snap);
  },
  oidcDirty() {
    const base = (this._oidcBaseline || {})[this.oidcProviderId] || '';
    if (base !== this._oidcSnapshot()) {
      return true;
    }
    // OIDC-scoped tunables wired into THIS section's Save so editing
    // them flips the same amber ring as the rest of the OIDC form.
    // Mirror of the Portainer / asset-inventory / AI / NE patterns.
    // Compare tuningForm against the previously-saved `_tuningBaseline`;
    // Save body includes these keys + the post-save `loadTuning()`
    // resets the baseline so dirty flips back to false on success.
    const tf = this.tuningForm || {};
    const baselineStr = this._tuningBaseline || '';
    let baseline;
    try {
      baseline = baselineStr ? JSON.parse(baselineStr) : {};
    } catch (_e) {
      baseline = {};
    }
    const tunableKeys = ['tuning_oidc_http_timeout_seconds'];
    for (const k of tunableKeys) {
      const cur = (tf[k] == null ? '' : String(tf[k]).trim());
      const base = (baseline[k] == null ? '' : String(baseline[k]).trim());
      if (cur !== base) {
        return true;
      }
    }
    return false;
  },
  markOidcFormDirty() {
  },
  async clearOidcClientSecret() {
    // Per-provider clear flag: clear_oidc_client_secret (Authentik) or
    // clear_oidc_<id>_client_secret (namespaced providers).
    return this._clearSecret({
      flag: 'clear_oidc_' + this._oidcKeyPrefix() + 'client_secret',
      titleKey: 'settings.oidc.clear_secret_title',
      textKey: 'settings.oidc.client_secret_clear_text',
      toastKey: 'settings.oidc.client_secret_cleared',
    });
  },
  // RFC 7591 dynamic client registration for the selected provider. POSTs the
  // admin's initial-access-token to /api/oidc/<id>/register; the backend reads
  // the discovery registration_endpoint, registers OmniGrid, and persists the
  // issued client_id / client_secret. Reloads settings so the form reflects
  // the new client_id + the secret-set flag.
  async oidcAutoRegister() {
    if (!this.oidcSupportsRegistration() || this.oidcAutoReg.running) {
      return;
    }
    this.oidcAutoReg.running = true;
    try {
      const r = await fetch('/api/oidc/' + encodeURIComponent(this.oidcProviderId) + '/register', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ initial_access_token: (this.oidcAutoReg.token || '').trim() }),
      });
      if (r.ok) {
        const j = await r.json().catch(() => ({}));
        this.showToast(this.t('settings.oidc.register_ok') || 'Client registered');
        this.oidcAutoReg.token = '';
        if (j && j.client_id) {
          this.oidcForm.client_id = j.client_id;
        }
        // Credentials were persisted server-side — reload so the form + the
        // client_secret_set flag reflect the new state.
        await this.loadSettings();
      } else {
        const detail = await this.fmtResponseError(r);
        this.showToast(detail || this.t('toasts.save_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.oidcAutoReg.running = false;
    }
  },
};
