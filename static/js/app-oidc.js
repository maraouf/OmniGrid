// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Authentik / OIDC integration (Admin → Authentik OIDC).
//
// All OIDC settings are DB-backed, edited via this form. The flow
// follows the canonical Test-before-Save gate pattern (see CLAUDE.md):
// snapshot the form on Test success, compare on every keystroke, lock
// Save until snapshot matches live form.


export default {
    oidcSaving: false,           // Admin → OIDC
    // OIDC settings — `oidcStatus` is the server snapshot; `oidcForm` is
    // the editable form state before Save. Client secret is write-only —
    // we never populate it back from the server, and blank-on-save means
    // "keep existing". Same pattern as the Portainer API-key field below.
    oidcStatus: null,
    oidcForm: {
      enabled: false, issuer_url: '', client_id: '', client_secret: '',
      redirect_uri: '', scopes: 'openid email profile groups',
      admin_group: 'omnigrid-admins',
    },
    oidcTestResult: null,
    // Same Test-before-Save gating as Portainer above — when OIDC is
    // enabled, the operator must run a successful Test connection
    // before Save unlocks. Cleared on form load; mutated on form
    // edit (via dirty-tracker mismatch).
    _oidcLastPassedTest: '',
    _oidcBaseline: '',

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
      const body = {
        oidc_enabled:      !!this.oidcForm.enabled,
        oidc_issuer_url:   (this.oidcForm.issuer_url || '').trim(),
        oidc_client_id:    (this.oidcForm.client_id || '').trim(),
        oidc_redirect_uri: (this.oidcForm.redirect_uri || '').trim(),
        oidc_scopes:       (this.oidcForm.scopes || '').trim(),
        oidc_admin_group:  (this.oidcForm.admin_group || '').trim(),
        oidc_verify_tls:   !!this.oidcForm.verify_tls,
        oidc_group_case_sensitive: !!this.oidcForm.group_case_sensitive,
      };
      // Client secret: only send when the admin actually typed one.
      // Empty / whitespace-only = "keep current" per the backend contract.
      if (this.oidcForm.client_secret && this.oidcForm.client_secret.trim()) {
        body.oidc_client_secret = this.oidcForm.client_secret;
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
        const bounds = (this.tuningBounds || {})[k] || {};
        if (!Number.isFinite(n) || (bounds.lo != null && n < bounds.lo) || (bounds.hi != null && n > bounds.hi)) {
          this.showToast(this.t('toasts.save_failed'), 'error');
          return;
        }
        body[k] = String(n);
      }
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
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
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    async testOidcConnection() {
      this.oidcTestResult = { pending: true };
      // Snapshot the form NOW so we can stamp it on success — same
      // pattern as `testPortainerConnection`.
      const probedSnapshot = this._oidcSnapshot();
      try {
        // Send the in-flight verify_tls so an admin can flip the
        // checkbox OFF and Test a self-signed issuer before saving.
        // Backend falls back to the saved DB value when the key is
        // missing.
        const r = await fetch('/api/oidc/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            issuer_url: (this.oidcForm.issuer_url || '').trim(),
            verify_tls: !!this.oidcForm.verify_tls,
          }),
        });
        const j = await r.json().catch(() => ({}));
        this.oidcTestResult = { ok: !!j.ok, status: j.status || 0, detail: j.detail || '' };
        if (j && j.ok) {
          this._oidcLastPassedTest = probedSnapshot;
          this.recordTestSuccess('oidc');
        }
      } catch (_) {
        this.oidcTestResult = { ok: false, status: 0, detail: this.t('toasts.network_error') };
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
      return this._oidcLastPassedTest === this._oidcSnapshot()
             && !!this._oidcLastPassedTest;
    },
    _oidcSnapshot() {
      const f = this.oidcForm || {};
      const s = this.oidcStatus || {};
      return JSON.stringify({
        enabled:     !!f.enabled,
        issuer:      (f.issuer_url || '').trim(),
        client_id:   (f.client_id || '').trim(),
        secret:      f.client_secret ? '<set>' : '',
        redirect:    (f.redirect_uri || '').trim(),
        scopes:      (f.scopes || '').trim(),
        admin_group: (f.admin_group || '').trim(),
        verify_tls:  f.verify_tls !== false,
        group_case_sensitive: f.group_case_sensitive !== false,
        baseEnabled: !!s.enabled,
        baseIssuer:  s.issuer_url || '',
        baseCid:     s.client_id || '',
        baseRedir:   s.redirect_uri || '',
        baseScopes:  s.scopes || '',
        baseGrp:     s.admin_group || '',
        baseVerify:  s.verify_tls !== false,
        baseGroupCS: s.group_case_sensitive !== false,
      });
    },
    oidcDirty() {
      if (this._oidcBaseline !== this._oidcSnapshot()) {
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
      let baseline = {};
      try { baseline = baselineStr ? JSON.parse(baselineStr) : {}; }
      catch (_e) { baseline = {}; }
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
    markOidcFormDirty()   {},
    async clearOidcClientSecret() {
      return this._clearSecret({
        flag: 'clear_oidc_client_secret',
        titleKey: 'settings.oidc.clear_secret_title',
        textKey:  'settings.oidc.client_secret_clear_text',
        toastKey: 'settings.oidc.client_secret_cleared',
      });
    },
};
