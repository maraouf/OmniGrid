// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ExceptionCaughtLocallyJS,JSReusedLocalVariable,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,RedundantLocalVariableJS,JSMissingAwait,JSAsyncFunctionMissingAwait
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSCheckNamingConventionsInspection,UnnecessaryLocalVariableJS
// Sibling-file canonical noinspection block — same shape as
// app-admin.js / app-charts.js / app-ai.js / app-stats.js so the
// suppressed warning classes stay consistent across the SPA. Real
// bugs (typos / dead assignments / wrong types) are fixed inline,
// NOT suppressed.
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Portainer connection config (Admin → Portainer).

export default {
  portainerSaving: false,      // Admin → Portainer

  // Portainer connection — same DB-backed / UI-managed pattern as OIDC.
  // API key is write-only; blank on save means "keep current".
  portainerStatus: null,
  // lightweight "is Portainer configured?" flag refreshed on
  // every /api/items poll. Distinct from `portainerStatus` which is
  // populated from the heavier /api/settings response only when the
  // settings page loads. null = unknown (initial paint), true/false =
  // explicit. Drives the empty-state copy on stacks/services/nodes.
  portainerConfigured: null,
  portainerForm: {
    url: '', endpoint_id: 1, verify_tls: true, api_key: '',
  },
  portainerTestResult: null,
  _portainerBaseline: '',
  // Snapshot of the form values that successfully passed a Test
  // probe. Set to the current `_portainerSnapshot()` value on
  // every successful `testPortainerConnection()`; cleared when the
  // form is loaded fresh from the server (so the operator must
  // re-test after edits). Used by `canSavePortainer()` to gate the
  // Save button — admins can't Save an enabled Portainer config
  // without first proving the URL / API key / endpoint round-trip
  // works, so a typo'd config can't ship and break /api/items.
  _portainerLastPassedTest: '',

  async savePortainerSettings() {
    if (this.portainerSaving) {
      return;
    }
    this.portainerSaving = true;
    try {
      await this._savePortainerSettingsImpl();
    } finally {
      this.portainerSaving = false;
    }
  },
  async _savePortainerSettingsImpl() {
    const body = {
      // Master switch saved alongside the URL / API key /
      // endpoint / verify_tls so the operator's toggle persists on
      // Save (no per-checkbox auto-save).
      portainer_enabled: !!this.settings.portainer_enabled,
      portainer_url: (this.portainerForm.url || '').trim(),
      portainer_endpoint_id: parseInt(String(this.portainerForm.endpoint_id || ''), 10) || 1,
      portainer_verify_tls: !!this.portainerForm.verify_tls,
      // Public URL — folded into this Save button so the operator
      // doesn't have to hit two Save buttons after editing the
      // Portainer admin tab. Backend treats it as keep-current-if-
      // missing, so sending an empty string is a deliberate clear.
      portainer_public_url: (this.settings.portainer_public_url || '').trim(),
      // Swarm autoheal action — moved to the Portainer admin panel
      // (the action targets Portainer's agent service). Saved
      // through this same POST so the operator's choice flips on
      // the same Save click as the rest of the Portainer config.
      swarm_autoheal_action: (this.settings.swarm_autoheal_action === 'restart') ? 'restart' : 'notify',
      // First-boot auto-bootstrap toggle for the default
      // swarm_agent_health schedule. Same Save click as the action
      // selector so the operator's intent applies in one round-trip.
      // Stored as a "true"/"false" string per the existing settings
      // shape (Pydantic Optional[str]).
      swarm_autoheal_bootstrap_enabled:
        this.settings.swarm_autoheal_bootstrap_enabled ? 'true' : 'false',
    };
    if (this.portainerForm.api_key && this.portainerForm.api_key.trim()) {
      body.portainer_api_key = this.portainerForm.api_key;
    }
    // Portainer-scoped tunables — included in the same POST body so
    // editing them flips dirty + saves through THIS section, not the
    // generic Admin → Config form. Validate int + bounds locally
    // first so a typo'd value doesn't make the whole Save fail; the
    // backend re-clamps but explicit local errors are friendlier.
    const tf = this.tuningForm || {};
    const tunableKeys = [
      'tuning_portainer_op_timeout_short_seconds',
      'tuning_portainer_op_timeout_medium_seconds',
      'tuning_portainer_op_timeout_long_seconds',
      'tuning_gather_client_timeout_seconds',
      'tuning_gather_orphan_probe_timeout_seconds',
    ];
    for (const k of tunableKeys) {
      const raw = tf[k];
      if (raw == null || String(raw).trim() === '') {
        continue;
      }
      const n = parseInt(raw, 10);
      // `tuningEffective` (NOT `tuningBounds`) is the canonical
      // shape — pre-fix typo'd to `tuningBounds` which doesn't
      // exist on the component, so the validator silently never
      // bounds-checked any value (out-of-range tunables would
      // POST through without front-end rejection; backend's
      // tuning_int clamp was the only defence). The shape has
      // `{min, max, default, effective, source}` per tunable.
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
        // loadSettings() below re-captures the baseline.
        this.showToast(this.t('toasts.portainer_saved'));
        await this.loadSettings();
        // Re-read tuning baseline so portainerDirty() flips back to
        // false after a successful save that included tunable
        // changes.
        await this.loadTuning();
        this.portainerTestResult = null;
        // Trigger a forced refresh so the dashboard populates with data
        // from the newly-configured endpoint.
        this.refresh(true);
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  async testPortainerConnection() {
    this.portainerTestResult = {pending: true};
    // Snapshot the form NOW so we can stamp it on success. Compared
    // to the live snapshot at Save-time so any edit between Test
    // and Save invalidates the test result.
    const probedSnapshot = this._portainerSnapshot();
    try {
      const r = await fetch('/api/portainer/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          url: (this.portainerForm.url || '').trim(),
          endpoint_id: parseInt(String(this.portainerForm.endpoint_id || ''), 10) || 1,
          verify_tls: !!this.portainerForm.verify_tls,
          api_key: this.portainerForm.api_key || '',
        }),
      });
      const j = await r.json().catch(() => ({}));
      this.portainerTestResult = {ok: !!j.ok, status: j.status || 0, detail: j.detail || ''};
      if (j && j.ok) {
        this._portainerLastPassedTest = probedSnapshot;
        this.recordTestSuccess('portainer');
      }
    } catch (_) {
      this.portainerTestResult = {ok: false, status: 0, detail: this.t('toasts.network_error')};
    }
  },
  // Save-button gate. When portainer_enabled is OFF, no test required
  // (the service won't be probed by the backend either). When ON,
  // the operator must run a successful Test against the CURRENT
  // form values before Save unlocks — typed values can't ship without
  // proving the round-trip works. Any edit after a successful Test
  // mutates `_portainerSnapshot()` away from `_portainerLastPassedTest`,
  // re-locking Save and prompting the operator to re-test.
  canSavePortainer() {
    if (!(this.settings || {}).portainer_enabled) {
      return true;
    }
    return this._portainerLastPassedTest === this._portainerSnapshot()
      && !!this._portainerLastPassedTest;
  },
  _portainerSnapshot() {
    const f = this.portainerForm || {};
    // ONLY the connection-validating fields. The Test endpoint at
    // /api/portainer/test probes `{url, endpoint_id, verify_tls,
    // api_key}` against `{base}/api/status` + `{base}/api/endpoints/
    // {id}`; the `enabled` toggle is the gate that controls whether
    // we ATTEMPT the probe at all. Anything else (publicUrl,
    // swarm_autoheal_action, swarm_autoheal_bootstrap_enabled) is
    // SPA-side UI policy — flipping those between Test + Save
    // should NOT re-lock Save because the connection itself hasn't
    // changed. Pre-fix the snapshot folded in autoheal /
    // autohealBootstrap / publicUrl / portainerStatus fields, which
    // caused the operator-reported "edit URL → Test → Save still
    // locked" regression when ANY of those drifted between the two
    // clicks. api_key is write-only — any typed value (any non-empty
    // string) is dirty; baseline always has empty api_key.
    return JSON.stringify({
      enabled: !!(this.settings || {}).portainer_enabled,
      url: (f.url || '').trim(),
      endpoint_id: f.endpoint_id || 1,
      verify_tls: !!f.verify_tls,
      api_key: f.api_key ? '<set>' : '',
    });
  },
  portainerDirty() {
    if (this._portainerBaseline !== this._portainerSnapshot()) {
      return true;
    }
    // Portainer-scoped tunables wired into THIS section's Save so
    // editing them flips the same amber ring as the rest of the
    // Portainer form. Mirror of the asset_inventory + AI / NE
    // patterns. Compare tuningForm against the previously-saved
    // `_tuningBaseline`; Save body includes these keys + the
    // post-save `loadTuning()` resets the baseline so dirty
    // flips back to false on success.
    const tf = this.tuningForm || {};
    const baselineStr = this._tuningBaseline || '';
    let baseline = {};
    try {
      baseline = baselineStr ? JSON.parse(baselineStr) : {};
    } catch (_e) {
      baseline = {};
    }
    const tunableKeys = [
      'tuning_portainer_op_timeout_short_seconds',
      'tuning_portainer_op_timeout_medium_seconds',
      'tuning_portainer_op_timeout_long_seconds',
      'tuning_gather_client_timeout_seconds',
      'tuning_gather_orphan_probe_timeout_seconds',
    ];
    for (const k of tunableKeys) {
      const cur = (tf[k] == null ? '' : String(tf[k]).trim());
      const base = (baseline[k] == null ? '' : String(baseline[k]).trim());
      if (cur !== base) {
        return true;
      }
    }
    return false;
  },
  markPortainerFormDirty() {
  },
  portainerDeepLink(x) {
    const base = (this.settings.portainer_public_url || '').replace(/\/$/, '');
    if (!base) {
      return '#';
    }
    if (x.stack_id) {
      const stackName = x.stack || x.name;
      return `${base}/#!/${this.endpointId}/docker/stacks/${stackName}?id=${x.stack_id}&type=1&external=false`;
    }
    return `${base}/#!/${this.endpointId}/docker/dashboard`;
  },
  async clearPortainerApiKey() {
    return this._clearSecret({
      flag: 'clear_portainer_api_key',
      titleKey: 'settings.portainer.clear_secret_title',
      textKey: 'settings.portainer.api_key_clear_text',
      toastKey: 'settings.portainer.api_key_cleared',
    });
  },
};
