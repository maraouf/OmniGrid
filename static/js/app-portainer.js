// SPA Portainer connection config (Admin → Portainer).
//
// Settings: URL / API key / endpoint ID / TLS verify. All DB-backed,
// edited via this form. Follows the Test-before-Save gate pattern.
//
// Also includes `portainerDeepLink` — the helper that builds clickable
// "open in Portainer" URLs from item / stack IDs, consumed by row
// action menus throughout the SPA.
//
// Phase 2, Batch 15 of the static/js/app.js modularisation.

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
        portainer_enabled:     !!this.settings.portainer_enabled,
        portainer_url:         (this.portainerForm.url || '').trim(),
        portainer_endpoint_id: parseInt(this.portainerForm.endpoint_id) || 1,
        portainer_verify_tls:  !!this.portainerForm.verify_tls,
        // Public URL — folded into this Save button so the operator
        // doesn't have to hit two Save buttons after editing the
        // Portainer admin tab. Backend treats it as keep-current-if-
        // missing, so sending an empty string is a deliberate clear.
        portainer_public_url:  (this.settings.portainer_public_url || '').trim(),
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
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    async testPortainerConnection() {
      this.portainerTestResult = { pending: true };
      // Snapshot the form NOW so we can stamp it on success. Compared
      // to the live snapshot at Save-time so any edit between Test
      // and Save invalidates the test result.
      const probedSnapshot = this._portainerSnapshot();
      try {
        const r = await fetch('/api/portainer/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url:         (this.portainerForm.url || '').trim(),
            endpoint_id: parseInt(this.portainerForm.endpoint_id) || 1,
            verify_tls:  !!this.portainerForm.verify_tls,
            api_key:     this.portainerForm.api_key || '',
          }),
        });
        const j = await r.json().catch(() => ({}));
        this.portainerTestResult = { ok: !!j.ok, status: j.status || 0, detail: j.detail || '' };
        if (j && j.ok) {
          this._portainerLastPassedTest = probedSnapshot;
          this.recordTestSuccess('portainer');
        }
      } catch (_) {
        this.portainerTestResult = { ok: false, status: 0, detail: this.t('toasts.network_error') };
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
      const s = this.portainerStatus || {};
      // Form vs status. api_key is write-only — any typed value (any
      // non-empty string) is dirty; baseline always has empty api_key.
      return JSON.stringify({
        enabled:     !!(this.settings || {}).portainer_enabled,
        url:         (f.url || '').trim(),
        endpoint_id: f.endpoint_id || 1,
        verify_tls:  !!f.verify_tls,
        api_key:     f.api_key ? '<set>' : '',
        baseEnabled: !!s.enabled,
        baseUrl:     s.url || '',
        baseEpId:    s.endpoint_id || 1,
        baseVerify:  !!s.verify_tls,
        publicUrl:   (this.settings || {}).portainer_public_url || '',
        basePublic:  this._portainerPublicBaseline || '',
        // Swarm autoheal action lives in the Portainer panel
        // (the action targets Portainer's agent service, not a
        // notifications routing concern). Folded into this snapshot
        // so toggling the dropdown flips the Portainer Save button's
        // amber ring.
        autoheal:    (this.settings || {}).swarm_autoheal_action || 'notify',
        // First-boot auto-bootstrap toggle for the default
        // swarm_agent_health schedule. Folded in alongside the action
        // selector so toggling either dirties the Portainer panel.
        autohealBootstrap: !!(this.settings || {}).swarm_autoheal_bootstrap_enabled,
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
      try { baseline = baselineStr ? JSON.parse(baselineStr) : {}; }
      catch (_e) { baseline = {}; }
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
    markPortainerFormDirty() {},
    portainerDeepLink(x) {
      const base = (this.settings.portainer_public_url || '').replace(/\/$/,'');
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
        textKey:  'settings.portainer.api_key_clear_text',
        toastKey: 'settings.portainer.api_key_cleared',
      });
    },
};
