// SPA Admin → Providers — per-host-stats-provider settings + test +
// section-owned save for the six host-stats providers (Beszel / Pulse /
// node-exporter / Webmin / SNMP / Ping) plus the HTTP probe + Port Scan
// + Baseline / Drift detection sections.
//
// Each provider follows the same shape: a `_<name>SectionTuningKeys()`
// + `_<name>SectionPlainKeys()` + `<name>SectionDirty()` +
// `save<Name>Section()` quartet, plus a `test<Name>Connection()`
// helper that probes the live API.
//
// Also includes the central `_saveHostStatsImpl` /
// `saveProviders` / `saveOpenMeteoUrl` plus the master "host_stats"
// section save.
//
// Phase 2, Batch 26 of the static/js/app.js modularisation.

export default {
    beszelTestResult: null,
    pulseTestResult: null,
    webminTestResult: null,
    pingTestResult: null,
    snmpTestResult: null,

    async _saveHostStatsImpl() {
      const raw = this.settings.host_stats_source || 'none';
      const active = new Set(
        raw.split(',').map(s => s.trim()).filter(s => s && s !== 'none'),
      );
      const valid = new Set(['beszel', 'node_exporter', 'pulse', 'webmin', 'ping', 'snmp']);
      for (const s of active) {
        if (!valid.has(s)) {
          this.showToast(this.t('settings.host_stats.source_invalid'), 'error');
          return;
        }
      }
      // Source-specific validation — only the active sources' fields
      // are required to be well-formed; the others are persisted as-is
      // so toggling sources doesn't forget prior config.
      const normalized = active.size
        ? Array.from(active).sort().join(',')
        : 'none';
      const payload = {
        host_stats_source: normalized,
        // Keep node_exporter_enabled flag in sync with the selected
        // source for back-compat with anything reading the legacy flag.
        node_exporter_enabled: active.has('node_exporter'),
      };
      if (active.has('node_exporter')) {
        const tpl = (this.settings.node_exporter_url_template || '').trim();
        // Either placeholder is valid — {host} substitutes the Docker
        // hostname, {ip} substitutes the Swarm-advertised IP.
        if (tpl && !tpl.includes('{host}') && !tpl.includes('{ip}')) {
          this.showToast(this.t('settings.host_stats.placeholder_required'), 'error');
          return;
        }
        let overrides = {};
        const raw = (this.settings.node_exporter_overrides_json || '').trim() || '{}';
        try {
          const parsed = JSON.parse(raw);
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            overrides = parsed;
          } else { throw new Error('object expected'); }
        } catch (_) {
          this.showToast(this.t('settings.host_stats.overrides_invalid'), 'error');
          return;
        }
        payload.node_exporter_url_template = tpl;
        payload.node_exporter_overrides = overrides;
      }
      if (active.has('beszel')) {
        const hub = (this.settings.beszel_hub_url || '').trim();
        const ident = (this.settings.beszel_identity || '').trim();
        if (!hub || !ident) {
          this.showToast(this.t('settings.host_stats.beszel_required'), 'error');
          return;
        }
        if (!this.settings.beszel_password_set && !this.settings.beszel_password) {
          this.showToast(this.t('settings.host_stats.beszel_password_required'), 'error');
          return;
        }
        payload.beszel_hub_url = hub;
        payload.beszel_identity = ident;
        payload.beszel_verify_tls = !!this.settings.beszel_verify_tls;
        if (this.settings.beszel_password) {
          payload.beszel_password = this.settings.beszel_password;
        }
      }
      if (active.has('pulse')) {
        const url = (this.settings.pulse_url || '').trim();
        if (!url) {
          this.showToast(this.t('toasts_extra.pulse_url_required'), 'error');
          return;
        }
        if (!this.settings.pulse_token_set && !this.settings.pulse_token) {
          this.showToast(this.t('toasts_extra.pulse_token_required'), 'error');
          return;
        }
        payload.pulse_url = url;
        payload.pulse_verify_tls = !!this.settings.pulse_verify_tls;
        if (this.settings.pulse_token) {
          payload.pulse_token = this.settings.pulse_token;
        }
      }
      if (active.has('webmin')) {
        const user = (this.settings.webmin_user || '').trim();
        if (!user) {
          this.showToast(this.t('toasts_extra.webmin_user_required'), 'error');
          return;
        }
        if (!this.settings.webmin_password_set && !this.settings.webmin_password) {
          this.showToast(this.t('toasts_extra.webmin_password_required'), 'error');
          return;
        }
        // Strip trailing slash(es) — operators paste URLs with or
        // without a trailing slash; normalise here so the backend
        // and the per-host aliases map store one canonical form.
        payload.webmin_url = (this.settings.webmin_url || '').trim().replace(/\/+$/, '');
        payload.webmin_user = user;
        payload.webmin_verify_tls = !!this.settings.webmin_verify_tls;
        if (this.settings.webmin_password) {
          payload.webmin_password = this.settings.webmin_password;
        }
        payload.webmin_aliases = this.settings.webmin_aliases || {};
      }
      // ping provider. No secrets, so we always include the
      // fields if the master toggle is on; backend bounds-checks the
      // port. When the source isn't active, the master toggle still
      // round-trips so the operator's saved port + transport are
      // preserved across enable/disable cycles.
      if (this.settings.ping_enabled !== undefined) {
        payload.ping_enabled = !!this.settings.ping_enabled;
      }
      if (this.settings.ping_default_port) {
        const p = parseInt(this.settings.ping_default_port, 10);
        if (Number.isFinite(p) && p >= 1 && p <= 65535) {
          payload.ping_default_port = p;
        }
      }
      if (this.settings.ping_use_icmp !== undefined) {
        payload.ping_use_icmp = !!this.settings.ping_use_icmp;
      }
      // Port-scan provider. Master toggle + global defaults round-trip
      // even when disabled so the operator's saved values survive
      // enable/disable cycles.
      if (this.settings.port_scan_enabled !== undefined) {
        payload.port_scan_enabled = !!this.settings.port_scan_enabled;
      }
      if (this.settings.port_scan_default_ports !== undefined) {
        payload.port_scan_default_ports = String(this.settings.port_scan_default_ports || '').trim();
      }
      // `port_scan_udp_enabled` is DEPRECATED — UDP runs under the
      // master `port_scan_enabled` toggle (operator-flagged 2026-05-10).
      // No longer included in the save payload.
      if (this.settings.port_scan_udp_default_ports !== undefined) {
        payload.port_scan_udp_default_ports = String(this.settings.port_scan_udp_default_ports || '').trim();
      }
      // NOTE: legacy `port_scan_default_timeout_seconds` /
      // `port_scan_default_concurrency` POST branches GONE — the form
      // never wrote to `settings.port_scan_default_timeout` /
      // `settings.port_scan_default_concurrency` (the UI binds to
      // `tuningForm['tuning_port_scan_default_*']`), so these
      // branches were dead code. Backend's matching write paths were
      // also removed in the same audit fix. Per CLAUDE.md "Plain
      // -settings escape hatch is a drift class".
      // SNMP provider. Defaults always round-trip (so the
      // operator's saved community / version / port survive an
      // enable/disable cycle). v3 keys follow the keep-current-if-blank
      // contract — only POSTed when the user actually types a value.
      // Aliases ride the JSON-textarea pattern from node_exporter
      // overrides; `snmp_aliases_json` holds the textarea string and
      // we parse-and-reject here.
      if (this.settings.snmp_default_community !== undefined) {
        payload.snmp_default_community = (this.settings.snmp_default_community || '').trim();
      }
      if (this.settings.snmp_default_version !== undefined) {
        const v = (this.settings.snmp_default_version || '').trim().toLowerCase();
        if (v && v !== 'v2c' && v !== 'v3') {
          this.showToast(this.t('settings.host_stats.snmp.version_invalid'), 'error');
          return;
        }
        payload.snmp_default_version = v;
      }
      if (this.settings.snmp_default_port !== undefined && this.settings.snmp_default_port !== '') {
        const p = parseInt(this.settings.snmp_default_port, 10);
        if (Number.isFinite(p) && p >= 1 && p <= 65535) {
          payload.snmp_default_port = p;
        }
      }
      if (this.settings.snmp_v3_user !== undefined) {
        payload.snmp_v3_user = (this.settings.snmp_v3_user || '').trim();
      }
      if (this.settings.snmp_v3_auth_key) {
        payload.snmp_v3_auth_key = this.settings.snmp_v3_auth_key;
      }
      if (this.settings.snmp_v3_priv_key) {
        payload.snmp_v3_priv_key = this.settings.snmp_v3_priv_key;
      }
      if (this.settings.snmp_aliases_json !== undefined) {
        const raw = (this.settings.snmp_aliases_json || '').trim() || '{}';
        let aliases = {};
        try {
          const parsed = JSON.parse(raw);
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            aliases = parsed;
          } else { throw new Error('object expected'); }
        } catch (_) {
          this.showToast(this.t('settings.host_stats.snmp.aliases_invalid'), 'error');
          return;
        }
        payload.snmp_aliases = aliases;
      }
      // per-provider chip colour overrides. Always packed into
      // the payload (even when blank → backend treats blank as "clear
      // the override"). Hex validation is done server-side; the colour
      // input element naturally produces #RRGGBB so a malformed value
      // would only arrive via direct API tampering.
      for (const k of [
        'provider_color_beszel', 'provider_color_pulse',
        'provider_color_node_exporter', 'provider_color_webmin',
        'provider_color_ping', 'provider_color_snmp',
      ]) {
        if (this.settings[k] !== undefined) {
          payload[k] = (this.settings[k] || '').trim();
        }
      }
      // fold in any dirty tunables that live on this panel
      // (Webmin probe budget + cache TTLs, NE probe timeout). The
      // backend's /api/settings POST is the same endpoint the
      // dedicated saveTuning() uses, so packaging them in the same
      // payload removes the need for per-section Save buttons.
      // Validate via the same int + bounds check saveTuning does.
      for (const k of this._allTuningKeys()) {
        const raw = (this.tuningForm || {})[k];
        if (raw === '' || raw == null) {
          // Blank means "clear the override" — include it in the
          // payload as an empty string so the backend deletes the
          // setting row.
          payload[k] = '';
          continue;
        }
        const n = Number(raw);
        if (!Number.isFinite(n) || !Number.isInteger(n)) {
          this.showToast(this.t('admin.config.errors.must_be_int', {
            field: this.t('admin.config.fields.' + k + '.label'),
          }), 'error');
          return;
        }
        const eff = (this.tuningEffective || {})[k] || {};
        if (Number.isFinite(eff.min) && n < eff.min) {
          this.showToast(this.t('admin.config.errors.below_min', {
            field: this.t('admin.config.fields.' + k + '.label'),
            min: eff.min,
          }), 'error');
          return;
        }
        if (Number.isFinite(eff.max) && n > eff.max) {
          this.showToast(this.t('admin.config.errors.above_max', {
            field: this.t('admin.config.fields.' + k + '.label'),
            max: eff.max,
          }), 'error');
          return;
        }
        payload[k] = String(raw).trim();
      }
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (r.ok) {
          this.showToast(this.t('settings.host_stats.saved'));
          // Flip the "password is stored" indicator once we've persisted
          // a new password so the UI stops nagging to re-enter it.
          if (payload.beszel_password) {
            this.settings.beszel_password_set = true;
            this.settings.beszel_password = '';
          }
          if (payload.webmin_password) {
            this.settings.webmin_password_set = true;
            this.settings.webmin_password = '';
          }
          // SNMP v3 keys. Same flip-flag-and-clear pattern as
          // every other write-only secret in this form.
          if (payload.snmp_v3_auth_key) {
            this.settings.snmp_v3_auth_key_set = true;
            this.settings.snmp_v3_auth_key = '';
          }
          if (payload.snmp_v3_priv_key) {
            this.settings.snmp_v3_priv_key_set = true;
            this.settings.snmp_v3_priv_key = '';
          }
          // Re-capture baseline so the dirty indicator clears now that
          // the server has the same values we just sent.
          this._hostStatsBaseline = this._hostStatsSnapshot();
          // also re-baseline the tuning form (we just POSTed
          // the same values) so `tuningDirty()` flips back to false
          // and the unified Save button loses its amber ring.
          // loadTuning() does both: re-fetch /api/admin/tuning and
          // reset _tuningBaseline.
          await this.loadTuning();
          // Refresh items so the new nodes_info fields land immediately.
          this.refresh(true);
          // ALSO re-fetch /api/hosts/list with force=true so the next
          // host-data render bypasses the 10s `_host_provider_cache`
          // memo. Without this, host rows could show
          // "Refreshing host data…" or stale provider state for up to
          // 10s after the save toast.
          this.loadHosts(true);
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
        }
      } catch (_) { this.showToast(this.t('toasts.network_error'), 'error'); }
    },

    // Admin → Port Scan — section-owned save.
    // Posts ONLY Port Scan's plain settings + the six Port Scan
    // tunables (4 TCP defaults + 2 UDP defaults). Per the section
    // -saves-its-own-tunables convention.
    _portScanSectionTuningKeys() {
      return [
        'tuning_port_scan_default_timeout_seconds',
        'tuning_port_scan_default_concurrency',
        'tuning_port_scan_max_seconds',
        'tuning_port_scan_banner_read_seconds',
        'tuning_port_scan_udp_default_timeout_seconds',
        'tuning_port_scan_udp_default_concurrency',
        // Scheduled port-scan refresh — knobs feeding
        // logic.schedules._run_port_scan_refresh. Section-saved here
        // alongside the on-demand tunables so the operator dirty-edits
        // them in the SAME admin tab where the master toggle lives.
        'tuning_port_scan_schedule_max_hosts_per_tick',
        'tuning_port_scan_schedule_min_age_seconds',
        'tuning_port_scan_schedule_per_host_concurrency',
      ];
    },
    _portScanSectionPlainKeys() {
      // `port_scan_udp_enabled` is DEPRECATED (operator-flagged
      // 2026-05-10) — UDP runs under the master `port_scan_enabled`
      // toggle. No longer in the dirty-tracker / save payload.
      return [
        'port_scan_enabled', 'port_scan_default_ports',
        'port_scan_udp_default_ports',
      ];
    },
    portScanSectionDirty() {
      try {
        const baseline = this._tuningBaselineMap();
        for (const k of this._portScanSectionTuningKeys()) {
          const cur = (this.tuningForm || {})[k];
          const curStr = (cur == null ? '' : String(cur).trim());
          const baseStr = (baseline[k] == null ? '' : String(baseline[k]).trim());
          if (curStr !== baseStr) {
            return true;
          }
        }
      } catch (_) {}
      // The host_stats baseline JSON-string carries the four Port Scan
      // plain settings (per `_hostStatsSnapshot`'s pick list). Parse
      // and compare each.
      let base = {};
      try {
        if (typeof this._hostStatsBaseline === 'string' && this._hostStatsBaseline) {
          base = JSON.parse(this._hostStatsBaseline) || {};
        }
      } catch (_) { base = {}; }
      try {
        for (const k of this._portScanSectionPlainKeys()) {
          if (String((this.settings || {})[k] || '') !== String(base[k] || '')) {
            return true;
          }
        }
      } catch (_) {}
      return false;
    },
    async savePortScanSection() {
      if (this.hostStatsSaving) {
        return;
      }
      for (const k of this._portScanSectionTuningKeys()) {
        const raw = (this.tuningForm || {})[k];
        if (raw === '' || raw == null) {
          continue;
        }
        const n = Number(raw);
        if (!Number.isFinite(n) || !Number.isInteger(n)) {
          this.showToast(this.t('admin.config.errors.must_be_int', {
            field: this.t('admin.config.fields.' + k + '.label'),
          }), 'error');
          return;
        }
        const eff = this.tuningEffective[k] || {};
        if (Number.isFinite(eff.min) && n < eff.min) {
          this.showToast(this.t('admin.config.errors.below_min', {
            field: this.t('admin.config.fields.' + k + '.label'), min: eff.min,
          }), 'error');
          return;
        }
        if (Number.isFinite(eff.max) && n > eff.max) {
          this.showToast(this.t('admin.config.errors.above_max', {
            field: this.t('admin.config.fields.' + k + '.label'), max: eff.max,
          }), 'error');
          return;
        }
      }
      this.hostStatsSaving = true;
      try {
        const body = {};
        const s = this.settings || {};
        // Plain settings — booleans flow through as booleans, CSVs
        // as strings (server-side validator splits + clamps).
        body.port_scan_enabled = !!s.port_scan_enabled;
        body.port_scan_default_ports = (s.port_scan_default_ports == null ? '' : String(s.port_scan_default_ports));
        // `port_scan_udp_enabled` DEPRECATED — UDP runs under the
        // master toggle (operator-flagged 2026-05-10). Not sent.
        body.port_scan_udp_default_ports = (s.port_scan_udp_default_ports == null ? '' : String(s.port_scan_udp_default_ports));
        // Section-owned tunables.
        for (const k of this._portScanSectionTuningKeys()) {
          const v = (this.tuningForm || {})[k];
          body[k] = (v == null ? '' : String(v).trim());
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        await Promise.all([this.loadSettings(), this.loadTuning()]);
        this.showToast(this.t('toasts.saved') || 'Saved', 'success');
      } catch (e) {
        this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed') + ': ' + (e.message || e), 'error');
      } finally {
        this.hostStatsSaving = false;
      }
    },

    // Settings → Host stats → SNMP sub-tab — section-owned save.
    // Posts ONLY SNMP's plain settings + every SNMP tunable rendered
    // in the sub-tab (probe_timeout / wall_clock_budget /
    // per_host_walk_concurrency / 5 vendor walk-concurrency overrides
    // / global concurrency / sample_interval / unreachable_cooldown /
    // 2 cache TTLs / failure_pause_rounds — 14 in total). Per the
    // section-saves-its-own-tunables convention.
    _snmpSectionTuningKeys() {
      // Mirror the markup's `_perProviderTuneKeys.snmp` so every knob
      // rendered in the sub-tab rides this Save handler.
      return (this._perProviderTuneKeys && this._perProviderTuneKeys.snmp) || [];
    },
    _snmpSectionPlainKeys() {
      // Non-secret plain settings the SNMP sub-tab owns. Secrets
      // (`snmp_v3_auth_key` / `snmp_v3_priv_key`) follow the keep
      // -current-if-blank contract — they dirty when typed, not on
      // baseline diff.
      return [
        'snmp_default_community', 'snmp_default_version',
        'snmp_default_port', 'snmp_v3_user',
        'snmp_aliases_json',
        'provider_color_snmp',
      ];
    },
    snmpSectionDirty() {
      try {
        const baseline = this._tuningBaselineMap();
        for (const k of this._snmpSectionTuningKeys()) {
          const cur = (this.tuningForm || {})[k];
          const curStr = (cur == null ? '' : String(cur).trim());
          const baseStr = (baseline[k] == null ? '' : String(baseline[k]).trim());
          if (curStr !== baseStr) {
            return true;
          }
        }
      } catch (_) {}
      let base = {};
      try {
        if (typeof this._hostStatsBaseline === 'string' && this._hostStatsBaseline) {
          base = JSON.parse(this._hostStatsBaseline) || {};
        }
      } catch (_) { base = {}; }
      try {
        for (const k of this._snmpSectionPlainKeys()) {
          if (String((this.settings || {})[k] || '') !== String(base[k] || '')) {
            return true;
          }
        }
        // Typed-but-not-saved v3 secrets dirty the section even
        // though they're omitted from the baseline diff.
        if (((this.settings || {}).snmp_v3_auth_key || '').trim() !== '') {
          return true;
        }
        if (((this.settings || {}).snmp_v3_priv_key || '').trim() !== '') {
          return true;
        }
      } catch (_) {}
      try {
        const curSrc = String((this.settings || {}).host_stats_source || '');
        const baseSrcStr = String(base.host_stats_source || '');
        if (curSrc !== baseSrcStr) {
          const curHas = curSrc.split(',').map(s => s.trim()).includes('snmp');
          const baseHas = baseSrcStr.split(',').map(s => s.trim()).includes('snmp');
          if (curHas !== baseHas) {
            return true;
          }
        }
      } catch (_) {}
      return false;
    },
    async saveSnmpSection() {
      if (this.hostStatsSaving) {
        return;
      }
      // Tunable bounds-check first — bail before touching the DB.
      for (const k of this._snmpSectionTuningKeys()) {
        const raw = (this.tuningForm || {})[k];
        if (raw === '' || raw == null) {
          continue;
        }
        const n = Number(raw);
        if (!Number.isFinite(n) || !Number.isInteger(n)) {
          this.showToast(this.t('admin.config.errors.must_be_int', {
            field: this.t('admin.config.fields.' + k + '.label'),
          }), 'error');
          return;
        }
        const eff = this.tuningEffective[k] || {};
        if (Number.isFinite(eff.min) && n < eff.min) {
          this.showToast(this.t('admin.config.errors.below_min', {
            field: this.t('admin.config.fields.' + k + '.label'), min: eff.min,
          }), 'error');
          return;
        }
        if (Number.isFinite(eff.max) && n > eff.max) {
          this.showToast(this.t('admin.config.errors.above_max', {
            field: this.t('admin.config.fields.' + k + '.label'), max: eff.max,
          }), 'error');
          return;
        }
      }
      // Plain-settings validation (mirrors the saveHostStats SNMP block).
      const body = {};
      const s = this.settings || {};
      if (s.snmp_default_community !== undefined) {
        body.snmp_default_community = (s.snmp_default_community || '').trim();
      }
      if (s.snmp_default_version !== undefined) {
        const v = (s.snmp_default_version || '').trim().toLowerCase();
        if (v && v !== 'v2c' && v !== 'v3') {
          this.showToast(this.t('settings.host_stats.snmp.version_invalid'), 'error');
          return;
        }
        body.snmp_default_version = v;
      }
      if (s.snmp_default_port !== undefined && s.snmp_default_port !== '') {
        const p = parseInt(s.snmp_default_port, 10);
        if (Number.isFinite(p) && p >= 1 && p <= 65535) {
          body.snmp_default_port = p;
        }
      }
      if (s.snmp_v3_user !== undefined) {
        body.snmp_v3_user = (s.snmp_v3_user || '').trim();
      }
      // v3 secrets follow keep-current-if-blank — only POST when typed.
      if (s.snmp_v3_auth_key) {
        body.snmp_v3_auth_key = s.snmp_v3_auth_key;
      }
      if (s.snmp_v3_priv_key) {
        body.snmp_v3_priv_key = s.snmp_v3_priv_key;
      }
      if (s.snmp_aliases_json !== undefined) {
        const raw = (s.snmp_aliases_json || '').trim() || '{}';
        let aliases = {};
        try {
          const parsed = JSON.parse(raw);
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            aliases = parsed;
          } else { throw new Error('object expected'); }
        } catch (_) {
          this.showToast(this.t('settings.host_stats.snmp.aliases_invalid'), 'error');
          return;
        }
        body.snmp_aliases = aliases;
      }
      if (s.provider_color_snmp !== undefined) {
        body.provider_color_snmp = (s.provider_color_snmp || '').trim();
      }
      this.hostStatsSaving = true;
      try {
        // Master-toggle membership.
        const sources = new Set(
          (s.host_stats_source || '').split(',').map(x => x.trim()).filter(Boolean)
        );
        if (this.hasHostStatsSource('snmp')) {
          sources.add('snmp');
        }
        else {
          sources.delete('snmp');
        }
        body.host_stats_source = [...sources].join(',');
        // Section-owned tunables.
        for (const k of this._snmpSectionTuningKeys()) {
          const v = (this.tuningForm || {})[k];
          body[k] = (v == null ? '' : String(v).trim());
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        await Promise.all([this.loadSettings(), this.loadTuning()]);
        this.showToast(this.t('toasts.saved') || 'Saved', 'success');
      } catch (e) {
        this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed') + ': ' + (e.message || e), 'error');
      } finally {
        this.hostStatsSaving = false;
      }
    },

    // Settings → Host stats → Ping sub-tab — section-owned save.
    // Posts ONLY Ping's plain settings + the five Ping tunables.
    // Per the section-saves-its-own-tunables convention.
    _pingSectionTuningKeys() {
      // Proxy through the central `_perProviderTuneKeys.ping` map so
      // adding a new tunable to the markup auto-extends dirty + save.
      return (this._perProviderTuneKeys && this._perProviderTuneKeys.ping) || [];
    },
    _pingSectionPlainKeys() {
      return [
        'ping_enabled', 'ping_default_port', 'ping_use_icmp',
        'provider_color_ping',
      ];
    },
    pingSectionDirty() {
      try {
        const baseline = this._tuningBaselineMap();
        for (const k of this._pingSectionTuningKeys()) {
          const cur = (this.tuningForm || {})[k];
          const curStr = (cur == null ? '' : String(cur).trim());
          const baseStr = (baseline[k] == null ? '' : String(baseline[k]).trim());
          if (curStr !== baseStr) {
            return true;
          }
        }
      } catch (_) {}
      let base = {};
      try {
        if (typeof this._hostStatsBaseline === 'string' && this._hostStatsBaseline) {
          base = JSON.parse(this._hostStatsBaseline) || {};
        }
      } catch (_) { base = {}; }
      try {
        for (const k of this._pingSectionPlainKeys()) {
          if (String((this.settings || {})[k] || '') !== String(base[k] || '')) {
            return true;
          }
        }
      } catch (_) {}
      try {
        const curSrc = String((this.settings || {}).host_stats_source || '');
        const baseSrcStr = String(base.host_stats_source || '');
        if (curSrc !== baseSrcStr) {
          const curHas = curSrc.split(',').map(s => s.trim()).includes('ping');
          const baseHas = baseSrcStr.split(',').map(s => s.trim()).includes('ping');
          if (curHas !== baseHas) {
            return true;
          }
        }
      } catch (_) {}
      return false;
    },
    async savePingSection() {
      if (this.hostStatsSaving) {
        return;
      }
      for (const k of this._pingSectionTuningKeys()) {
        const raw = (this.tuningForm || {})[k];
        if (raw === '' || raw == null) {
          continue;
        }
        const n = Number(raw);
        if (!Number.isFinite(n) || !Number.isInteger(n)) {
          this.showToast(this.t('admin.config.errors.must_be_int', {
            field: this.t('admin.config.fields.' + k + '.label'),
          }), 'error');
          return;
        }
        const eff = this.tuningEffective[k] || {};
        if (Number.isFinite(eff.min) && n < eff.min) {
          this.showToast(this.t('admin.config.errors.below_min', {
            field: this.t('admin.config.fields.' + k + '.label'), min: eff.min,
          }), 'error');
          return;
        }
        if (Number.isFinite(eff.max) && n > eff.max) {
          this.showToast(this.t('admin.config.errors.above_max', {
            field: this.t('admin.config.fields.' + k + '.label'), max: eff.max,
          }), 'error');
          return;
        }
      }
      this.hostStatsSaving = true;
      try {
        const body = {};
        for (const k of this._pingSectionPlainKeys()) {
          body[k] = this.settings[k] == null ? '' : this.settings[k];
        }
        const sources = new Set(
          (this.settings.host_stats_source || '').split(',').map(s => s.trim()).filter(Boolean)
        );
        if (this.hasHostStatsSource('ping')) {
          sources.add('ping');
        }
        else {
          sources.delete('ping');
        }
        body.host_stats_source = [...sources].join(',');
        for (const k of this._pingSectionTuningKeys()) {
          const v = (this.tuningForm || {})[k];
          body[k] = (v == null ? '' : String(v).trim());
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        await Promise.all([this.loadSettings(), this.loadTuning()]);
        this.showToast(this.t('toasts.saved') || 'Saved', 'success');
      } catch (e) {
        this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed') + ': ' + (e.message || e), 'error');
      } finally {
        this.hostStatsSaving = false;
      }
    },

    // Settings → Host stats → Beszel sub-tab — section-owned save.
    // Mirrors the Ping section pattern (canonical reference). Plain
    // keys cover the hub URL / identity / password-set semantics +
    // verify_tls + the per-provider chip colour; password follows the
    // keep-current-if-blank contract so the dirty check ignores
    // unchanged empty inputs after a successful save.
    _beszelSectionTuningKeys() {
      return (this._perProviderTuneKeys && this._perProviderTuneKeys.beszel) || [];
    },
    _beszelSectionPlainKeys() {
      return [
        'beszel_hub_url', 'beszel_identity', 'beszel_verify_tls',
        'provider_color_beszel',
      ];
    },
    beszelSectionDirty() {
      try {
        const baseline = this._tuningBaselineMap();
        for (const k of this._beszelSectionTuningKeys()) {
          const cur = (this.tuningForm || {})[k];
          const curStr = (cur == null ? '' : String(cur).trim());
          const baseStr = (baseline[k] == null ? '' : String(baseline[k]).trim());
          if (curStr !== baseStr) return true;
        }
      } catch (_) {}
      let base = {};
      try {
        if (typeof this._hostStatsBaseline === 'string' && this._hostStatsBaseline) {
          base = JSON.parse(this._hostStatsBaseline) || {};
        }
      } catch (_) { base = {}; }
      try {
        for (const k of this._beszelSectionPlainKeys()) {
          if (String((this.settings || {})[k] || '') !== String(base[k] || '')) return true;
        }
        // Password — only dirty when the operator typed something.
        // Empty + already-set means "keep current"; not dirty.
        if (((this.settings || {}).beszel_password || '').trim() !== '') return true;
      } catch (_) {}
      try {
        const curSrc = String((this.settings || {}).host_stats_source || '');
        const baseSrcStr = String(base.host_stats_source || '');
        if (curSrc !== baseSrcStr) {
          const curHas = curSrc.split(',').map(s => s.trim()).includes('beszel');
          const baseHas = baseSrcStr.split(',').map(s => s.trim()).includes('beszel');
          if (curHas !== baseHas) return true;
        }
      } catch (_) {}
      return false;
    },
    async saveBeszelSection() {
      if (this.hostStatsSaving) return;
      for (const k of this._beszelSectionTuningKeys()) {
        const raw = (this.tuningForm || {})[k];
        if (raw === '' || raw == null) continue;
        const n = Number(raw);
        if (!Number.isFinite(n) || !Number.isInteger(n)) {
          this.showToast(this.t('admin.config.errors.must_be_int', {
            field: this.t('admin.config.fields.' + k + '.label'),
          }), 'error');
          return;
        }
        const eff = this.tuningEffective[k] || {};
        if (Number.isFinite(eff.min) && n < eff.min) {
          this.showToast(this.t('admin.config.errors.below_min', {
            field: this.t('admin.config.fields.' + k + '.label'), min: eff.min,
          }), 'error');
          return;
        }
        if (Number.isFinite(eff.max) && n > eff.max) {
          this.showToast(this.t('admin.config.errors.above_max', {
            field: this.t('admin.config.fields.' + k + '.label'), max: eff.max,
          }), 'error');
          return;
        }
      }
      this.hostStatsSaving = true;
      try {
        const body = {};
        body.beszel_hub_url = (this.settings.beszel_hub_url || '').trim();
        body.beszel_identity = (this.settings.beszel_identity || '').trim();
        body.beszel_verify_tls = !!this.settings.beszel_verify_tls;
        // Password follows keep-current-if-blank: only POST when the
        // operator has actually typed a new value. Empty + already-set
        // is the "leave it alone" signal honoured by the backend.
        if (this.settings.beszel_password) {
          body.beszel_password = this.settings.beszel_password;
        }
        if (this.settings.provider_color_beszel) {
          body.provider_color_beszel = this.settings.provider_color_beszel;
        }
        const sources = new Set(
          (this.settings.host_stats_source || '').split(',').map(s => s.trim()).filter(Boolean)
        );
        if (this.hasHostStatsSource('beszel')) sources.add('beszel');
        else sources.delete('beszel');
        body.host_stats_source = [...sources].join(',');
        for (const k of this._beszelSectionTuningKeys()) {
          const v = (this.tuningForm || {})[k];
          body[k] = (v == null ? '' : String(v).trim());
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        // Clear the typed-but-not-yet-saved password so the dirty cue
        // resets after a successful save. Matches the global Save's
        // post-save cleanup at line ~5569.
        if (this.settings.beszel_password) {
          this.settings.beszel_password_set = true;
          this.settings.beszel_password = '';
        }
        await Promise.all([this.loadSettings(), this.loadTuning()]);
        this.showToast(this.t('toasts.saved') || 'Saved', 'success');
      } catch (e) {
        this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed') + ': ' + (e.message || e), 'error');
      } finally {
        this.hostStatsSaving = false;
      }
    },

    // Settings → Host stats → Pulse sub-tab — section-owned save.
    // Same shape as Beszel; the token field follows the keep-current-
    // if-blank contract and Pulse-specific `pulse_aliases` is a JSON
    // object (not a CSV) so it's stringified on POST and surfaced via
    // the same /api/settings round-trip the global save uses.
    _pulseSectionTuningKeys() {
      return (this._perProviderTuneKeys && this._perProviderTuneKeys.pulse) || [];
    },
    _pulseSectionPlainKeys() {
      return [
        'pulse_url', 'pulse_verify_tls',
        'provider_color_pulse',
      ];
    },
    pulseSectionDirty() {
      try {
        const baseline = this._tuningBaselineMap();
        for (const k of this._pulseSectionTuningKeys()) {
          const cur = (this.tuningForm || {})[k];
          const curStr = (cur == null ? '' : String(cur).trim());
          const baseStr = (baseline[k] == null ? '' : String(baseline[k]).trim());
          if (curStr !== baseStr) return true;
        }
      } catch (_) {}
      let base = {};
      try {
        if (typeof this._hostStatsBaseline === 'string' && this._hostStatsBaseline) {
          base = JSON.parse(this._hostStatsBaseline) || {};
        }
      } catch (_) { base = {}; }
      try {
        for (const k of this._pulseSectionPlainKeys()) {
          if (String((this.settings || {})[k] || '') !== String(base[k] || '')) return true;
        }
        if (((this.settings || {}).pulse_token || '').trim() !== '') return true;
        // Aliases dict — compare via stringified JSON. Skip when both
        // baseline and current are empty/missing.
        const curAli = JSON.stringify((this.settings || {}).pulse_aliases || {});
        const baseAli = JSON.stringify(base.pulse_aliases || {});
        if (curAli !== baseAli) return true;
      } catch (_) {}
      try {
        const curSrc = String((this.settings || {}).host_stats_source || '');
        const baseSrcStr = String(base.host_stats_source || '');
        if (curSrc !== baseSrcStr) {
          const curHas = curSrc.split(',').map(s => s.trim()).includes('pulse');
          const baseHas = baseSrcStr.split(',').map(s => s.trim()).includes('pulse');
          if (curHas !== baseHas) return true;
        }
      } catch (_) {}
      return false;
    },
    async savePulseSection() {
      if (this.hostStatsSaving) return;
      for (const k of this._pulseSectionTuningKeys()) {
        const raw = (this.tuningForm || {})[k];
        if (raw === '' || raw == null) continue;
        const n = Number(raw);
        if (!Number.isFinite(n) || !Number.isInteger(n)) {
          this.showToast(this.t('admin.config.errors.must_be_int', {
            field: this.t('admin.config.fields.' + k + '.label'),
          }), 'error');
          return;
        }
        const eff = this.tuningEffective[k] || {};
        if (Number.isFinite(eff.min) && n < eff.min) {
          this.showToast(this.t('admin.config.errors.below_min', {
            field: this.t('admin.config.fields.' + k + '.label'), min: eff.min,
          }), 'error');
          return;
        }
        if (Number.isFinite(eff.max) && n > eff.max) {
          this.showToast(this.t('admin.config.errors.above_max', {
            field: this.t('admin.config.fields.' + k + '.label'), max: eff.max,
          }), 'error');
          return;
        }
      }
      this.hostStatsSaving = true;
      try {
        const body = {};
        body.pulse_url = (this.settings.pulse_url || '').trim();
        body.pulse_verify_tls = !!this.settings.pulse_verify_tls;
        if (this.settings.pulse_token) {
          body.pulse_token = this.settings.pulse_token;
        }
        if (this.settings.pulse_aliases && typeof this.settings.pulse_aliases === 'object') {
          body.pulse_aliases = JSON.stringify(this.settings.pulse_aliases);
        }
        if (this.settings.provider_color_pulse) {
          body.provider_color_pulse = this.settings.provider_color_pulse;
        }
        const sources = new Set(
          (this.settings.host_stats_source || '').split(',').map(s => s.trim()).filter(Boolean)
        );
        if (this.hasHostStatsSource('pulse')) sources.add('pulse');
        else sources.delete('pulse');
        body.host_stats_source = [...sources].join(',');
        for (const k of this._pulseSectionTuningKeys()) {
          const v = (this.tuningForm || {})[k];
          body[k] = (v == null ? '' : String(v).trim());
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        if (this.settings.pulse_token) {
          this.settings.pulse_token_set = true;
          this.settings.pulse_token = '';
        }
        await Promise.all([this.loadSettings(), this.loadTuning()]);
        this.showToast(this.t('toasts.saved') || 'Saved', 'success');
      } catch (e) {
        this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed') + ': ' + (e.message || e), 'error');
      } finally {
        this.hostStatsSaving = false;
      }
    },

    // Settings → Host stats → HTTP probe sub-tab — section-owned save.
    // Posts ONLY HTTP probe's plain settings + the 8 HTTP probe
    // tunables. Mirrors the SNMP / Ping section-save pattern: proxies
    // through `_perProviderTuneKeys.http_probe` so adding a new
    // tunable to that array auto-extends the dirty + save scope.
    _httpProbeSectionTuningKeys() {
      return (this._perProviderTuneKeys && this._perProviderTuneKeys.http_probe) || [];
    },
    _httpProbeSectionPlainKeys() {
      // Note: `http_probe_aliases` removed from the section's tracked
      // keys — global aliases UI was redundant with the per-host
      // `http_probe.urls` field on each Admin → Hosts row. The setting
      // still round-trips through the backend for back-compat but the
      // section's dirty / save snapshot no longer references it.
      return [
        'http_probe_enabled',
        'provider_color_http_probe',
      ];
    },
    _httpProbeSnapshot() {
      // Same snapshot shape used by the test-stamp + dirty check —
      // canonicalises the form values so a re-Save without changes
      // is a no-op.
      const tune = {};
      for (const k of this._httpProbeSectionTuningKeys()) {
        tune[k] = (this.tuningForm || {})[k] == null ? '' : String((this.tuningForm || {})[k]).trim();
      }
      const plain = {};
      for (const k of this._httpProbeSectionPlainKeys()) {
        plain[k] = (this.settings || {})[k] == null ? '' : String((this.settings || {})[k]).trim();
      }
      return JSON.stringify({ tune, plain });
    },
    _httpProbeLastPassedTest: '',
    httpProbeSectionDirty() {
      try {
        const baseline = this._tuningBaselineMap();
        for (const k of this._httpProbeSectionTuningKeys()) {
          const cur = (this.tuningForm || {})[k];
          const curStr = (cur == null ? '' : String(cur).trim());
          const baseStr = (baseline[k] == null ? '' : String(baseline[k]).trim());
          if (curStr !== baseStr) {
            return true;
          }
        }
      } catch (_) {}
      let base = {};
      try {
        if (typeof this._hostStatsBaseline === 'string' && this._hostStatsBaseline) {
          base = JSON.parse(this._hostStatsBaseline) || {};
        }
      } catch (_) { base = {}; }
      try {
        for (const k of this._httpProbeSectionPlainKeys()) {
          if (String((this.settings || {})[k] || '') !== String(base[k] || '')) {
            return true;
          }
        }
      } catch (_) {}
      try {
        const curSrc = String((this.settings || {}).host_stats_source || '');
        const baseSrcStr = String(base.host_stats_source || '');
        if (curSrc !== baseSrcStr) {
          const curHas = curSrc.split(',').map(s => s.trim()).includes('http_probe');
          const baseHas = baseSrcStr.split(',').map(s => s.trim()).includes('http_probe');
          if (curHas !== baseHas) {
            return true;
          }
        }
      } catch (_) {}
      return false;
    },
    async saveHttpProbeSection() {
      if (this.hostStatsSaving) {
        return;
      }
      for (const k of this._httpProbeSectionTuningKeys()) {
        const raw = (this.tuningForm || {})[k];
        if (raw === '' || raw == null) {
          continue;
        }
        const n = Number(raw);
        if (!Number.isFinite(n) || !Number.isInteger(n)) {
          this.showToast(this.t('admin.config.errors.must_be_int', {
            field: this.t('admin.config.fields.' + k + '.label'),
          }), 'error');
          return;
        }
        const eff = this.tuningEffective[k] || {};
        if (Number.isFinite(eff.min) && n < eff.min) {
          this.showToast(this.t('admin.config.errors.below_min', {
            field: this.t('admin.config.fields.' + k + '.label'), min: eff.min,
          }), 'error');
          return;
        }
        if (Number.isFinite(eff.max) && n > eff.max) {
          this.showToast(this.t('admin.config.errors.above_max', {
            field: this.t('admin.config.fields.' + k + '.label'), max: eff.max,
          }), 'error');
          return;
        }
      }
      this.hostStatsSaving = true;
      try {
        const body = {};
        for (const k of this._httpProbeSectionPlainKeys()) {
          body[k] = this.settings[k] == null ? '' : this.settings[k];
        }
        const sources = new Set(
          (this.settings.host_stats_source || '').split(',').map(s => s.trim()).filter(Boolean)
        );
        if (this.hasHostStatsSource('http_probe')) {
          sources.add('http_probe');
        }
        else {
          sources.delete('http_probe');
        }
        body.host_stats_source = [...sources].join(',');
        for (const k of this._httpProbeSectionTuningKeys()) {
          const v = (this.tuningForm || {})[k];
          body[k] = (v == null ? '' : String(v).trim());
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        await Promise.all([this.loadSettings(), this.loadTuning()]);
        this.showToast(this.t('toasts.saved') || 'Saved', 'success');
      } catch (e) {
        this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed') + ': ' + (e.message || e), 'error');
      } finally {
        this.hostStatsSaving = false;
      }
    },
    // One-shot HTTP probe test — fires POST /api/http-probe/test
    // with the operator-supplied URL + the current form values.
    // Stamps `_httpProbeLastPassedTest` on success so the Save
    // gate can unlock.
    httpProbeTestResult: null,
    async testHttpProbe() {
      const url = (this.httpProbeTestUrl || '').trim();
      if (!url) {
        this.showToast(this.t('settings.host_stats.http_probe.test_no_url') || 'Enter a URL to test', 'error');
        return;
      }
      this.httpProbeTestResult = { pending: true };
      try {
        const r = await fetch('/api/http-probe/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        this.httpProbeTestResult = j;
        if (j && j.ok) {
          this._httpProbeLastPassedTest = this._httpProbeSnapshot();
        }
      } catch (e) {
        this.httpProbeTestResult = { ok: false, error: String(e.message || e) };
      }
    },

    // Settings → Host stats → node-exporter sub-tab — section-owned save.
    // Posts ONLY NE's plain settings + the NE probe-timeout tunable.
    // Per the section-saves-its-own-tunables convention.
    _neSectionTuningKeys() {
      // Proxy through the central `_perProviderTuneKeys.node_exporter`
      // map so every NE tunable rendered in the sub-tab rides this
      // section's Save handler + dirty cue. Pre-fix the hardcoded
      // single-key list missed the sample_interval + failure_pause
      // tunables — operator edits to those didn't trigger dirty.
      return (this._perProviderTuneKeys && this._perProviderTuneKeys.node_exporter) || [];
    },
    _neSectionPlainKeys() {
      return [
        'node_exporter_enabled',
        'node_exporter_url_template',
        'node_exporter_overrides_json',
        'provider_color_node_exporter',
      ];
    },
    neSectionDirty() {
      try {
        const baseline = this._tuningBaselineMap();
        for (const k of this._neSectionTuningKeys()) {
          const cur = (this.tuningForm || {})[k];
          const curStr = (cur == null ? '' : String(cur).trim());
          const baseStr = (baseline[k] == null ? '' : String(baseline[k]).trim());
          if (curStr !== baseStr) {
            return true;
          }
        }
      } catch (_) {}
      let base = {};
      try {
        if (typeof this._hostStatsBaseline === 'string' && this._hostStatsBaseline) {
          base = JSON.parse(this._hostStatsBaseline) || {};
        }
      } catch (_) { base = {}; }
      try {
        for (const k of this._neSectionPlainKeys()) {
          if (String((this.settings || {})[k] || '') !== String(base[k] || '')) {
            return true;
          }
        }
      } catch (_) {}
      try {
        const curSrc = String((this.settings || {}).host_stats_source || '');
        const baseSrcStr = String(base.host_stats_source || '');
        if (curSrc !== baseSrcStr) {
          const curHas = curSrc.split(',').map(s => s.trim()).includes('node_exporter');
          const baseHas = baseSrcStr.split(',').map(s => s.trim()).includes('node_exporter');
          if (curHas !== baseHas) {
            return true;
          }
        }
      } catch (_) {}
      return false;
    },
    async saveNeSection() {
      if (this.hostStatsSaving) {
        return;
      }
      for (const k of this._neSectionTuningKeys()) {
        const raw = (this.tuningForm || {})[k];
        if (raw === '' || raw == null) {
          continue;
        }
        const n = Number(raw);
        if (!Number.isFinite(n) || !Number.isInteger(n)) {
          this.showToast(this.t('admin.config.errors.must_be_int', {
            field: this.t('admin.config.fields.' + k + '.label'),
          }), 'error');
          return;
        }
        const eff = this.tuningEffective[k] || {};
        if (Number.isFinite(eff.min) && n < eff.min) {
          this.showToast(this.t('admin.config.errors.below_min', {
            field: this.t('admin.config.fields.' + k + '.label'), min: eff.min,
          }), 'error');
          return;
        }
        if (Number.isFinite(eff.max) && n > eff.max) {
          this.showToast(this.t('admin.config.errors.above_max', {
            field: this.t('admin.config.fields.' + k + '.label'), max: eff.max,
          }), 'error');
          return;
        }
      }
      this.hostStatsSaving = true;
      try {
        const body = {};
        for (const k of this._neSectionPlainKeys()) {
          body[k] = this.settings[k] == null ? '' : this.settings[k];
        }
        const sources = new Set(
          (this.settings.host_stats_source || '').split(',').map(s => s.trim()).filter(Boolean)
        );
        if (this.hasHostStatsSource('node_exporter')) {
          sources.add('node_exporter');
        }
        else {
          sources.delete('node_exporter');
        }
        body.host_stats_source = [...sources].join(',');
        for (const k of this._neSectionTuningKeys()) {
          const v = (this.tuningForm || {})[k];
          body[k] = (v == null ? '' : String(v).trim());
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        await Promise.all([this.loadSettings(), this.loadTuning()]);
        this.showToast(this.t('toasts.saved') || 'Saved', 'success');
      } catch (e) {
        this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed') + ': ' + (e.message || e), 'error');
      } finally {
        this.hostStatsSaving = false;
      }
    },

    // Settings → Host stats → Webmin sub-tab — section-owned save.
    // Posts ONLY Webmin's plain settings + the four Webmin tunables in
    // its own body. Per the section-saves-its-own-tunables convention.
    _webminSectionTuningKeys() {
      // Proxy through the central `_perProviderTuneKeys.webmin` map.
      // Pre-fix the hardcoded list missed probe_timeout_seconds +
      // sampler_budget_seconds, so operator edits to those didn't
      // trigger dirty. Single-source via the central map prevents
      // future tunable additions from drifting again.
      return (this._perProviderTuneKeys && this._perProviderTuneKeys.webmin) || [];
    },
    _webminSectionPlainKeys() {
      // Plain settings the Webmin sub-tab owns. Tracked separately
      // from the section's tunables so the dirty diff knows which
      // shape to compare against the loadSettings() baseline.
      // `webmin_aliases` is intentionally NOT in this list — aliases
      // are edited per-host in the Hosts tab, NOT in this sub-tab,
      // and including the dict-typed value here triggered an always
      // -dirty regression (the host_stats baseline JSON-snapshot
      // doesn't carry it, so `String({}) !== String('')` flagged the
      // section dirty even on a fresh page load with Webmin disabled).
      return [
        'webmin_user', 'webmin_verify_tls',
        'provider_color_webmin',
        // `webmin_password` follows the keep-current-if-blank contract
        // — it dirties when typed, not on baseline.
      ];
    },
    webminSectionDirty() {
      // Tunable diff first.
      try {
        const baseline = this._tuningBaselineMap();
        for (const k of this._webminSectionTuningKeys()) {
          const cur = (this.tuningForm || {})[k];
          const curStr = (cur == null ? '' : String(cur).trim());
          const baseStr = (baseline[k] == null ? '' : String(baseline[k]).trim());
          if (curStr !== baseStr) {
            return true;
          }
        }
      } catch (_) {}
      // Plain-settings + secret diff. The host-stats baseline is a
      // JSON STRING (see `_hostStatsSnapshot`) — parse before reading.
      let base = {};
      try {
        if (typeof this._hostStatsBaseline === 'string' && this._hostStatsBaseline) {
          base = JSON.parse(this._hostStatsBaseline) || {};
        }
      } catch (_) { base = {}; }
      try {
        for (const k of this._webminSectionPlainKeys()) {
          if (String((this.settings || {})[k] || '') !== String(base[k] || '')) {
            return true;
          }
        }
        if (((this.settings || {}).webmin_password || '').trim() !== '') {
          return true;
        }
      } catch (_) {}
      // Master-toggle membership diff — flipping the Webmin source on
      // / off via the sub-tab's checkbox marks the section dirty.
      try {
        const curSrc = String((this.settings || {}).host_stats_source || '');
        const baseSrcStr = String(base.host_stats_source || '');
        if (curSrc !== baseSrcStr) {
          const curHas = curSrc.split(',').map(s => s.trim()).includes('webmin');
          const baseHas = baseSrcStr.split(',').map(s => s.trim()).includes('webmin');
          if (curHas !== baseHas) {
            return true;
          }
        }
      } catch (_) {}
      return false;
    },
    async saveWebminSection() {
      if (this.hostStatsSaving) {
        return;
      }
      // Validate the section's tunables against TUNABLES bounds.
      for (const k of this._webminSectionTuningKeys()) {
        const raw = (this.tuningForm || {})[k];
        if (raw === '' || raw == null) {
          continue;
        }
        const n = Number(raw);
        if (!Number.isFinite(n) || !Number.isInteger(n)) {
          this.showToast(this.t('admin.config.errors.must_be_int', {
            field: this.t('admin.config.fields.' + k + '.label'),
          }), 'error');
          return;
        }
        const eff = this.tuningEffective[k] || {};
        if (Number.isFinite(eff.min) && n < eff.min) {
          this.showToast(this.t('admin.config.errors.below_min', {
            field: this.t('admin.config.fields.' + k + '.label'), min: eff.min,
          }), 'error');
          return;
        }
        if (Number.isFinite(eff.max) && n > eff.max) {
          this.showToast(this.t('admin.config.errors.above_max', {
            field: this.t('admin.config.fields.' + k + '.label'), max: eff.max,
          }), 'error');
          return;
        }
      }
      this.hostStatsSaving = true;
      try {
        const body = {};
        // Plain settings.
        for (const k of this._webminSectionPlainKeys()) {
          body[k] = this.settings[k] == null ? '' : this.settings[k];
        }
        // Master-toggle membership for the Webmin source. Read the
        // current set, add / remove `webmin`, and write back as CSV.
        const sources = new Set(
          (this.settings.host_stats_source || '').split(',').map(s => s.trim()).filter(Boolean)
        );
        if (this.hasHostStatsSource('webmin')) {
          sources.add('webmin');
        }
        else {
          sources.delete('webmin');
        }
        body.host_stats_source = [...sources].join(',');
        // Secret (keep-current-if-blank).
        if ((this.settings.webmin_password || '').trim() !== '') {
          body.webmin_password = this.settings.webmin_password;
        }
        // Section-owned tunables.
        for (const k of this._webminSectionTuningKeys()) {
          const v = (this.tuningForm || {})[k];
          body[k] = (v == null ? '' : String(v).trim());
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        // Refresh settings + tuning baselines so the section's dirty
        // tracker resets cleanly.
        await Promise.all([this.loadSettings(), this.loadTuning()]);
        this.showToast(this.t('toasts.saved') || 'Saved', 'success');
      } catch (e) {
        this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed') + ': ' + (e.message || e), 'error');
      } finally {
        this.hostStatsSaving = false;
      }
    },

    async saveOpenMeteoUrl() {
      if (this.openMeteoSaving) {
        return;
      }
      this.openMeteoSaving = true;
      try {
        const url = (this.settings.open_meteo_url || '').trim().replace(/\/+$/, '');
        // Save the enabled flag together with the URL so the operator's
        // toggle-change persists on Save (no per-checkbox auto-save).
        const enabled = !!this.settings.open_meteo_enabled;
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            open_meteo_url: url,
            open_meteo_enabled: enabled,
          }),
        });
        if (r.ok) {
          this.settings.open_meteo_url = url;
          this._openMeteoBaseline = this._openMeteoSnapshot();
          this.showToast(this.t('admin_integrations.open_meteo_saved'), 'success');
          // Re-fetch weather now so the topbar reflects the new upstream.
          if (this.loadHeaderWeather) {
            this.loadHeaderWeather();
          }
        } else {
          const j = await r.json().catch(() => ({}));
          this.showToast(j.detail || this.t('toasts_extra.save_failed_generic'), 'error');
        }
      } catch (_) {
        this.showToast(this.t('toasts_extra.network_error_generic'), 'error');
      } finally {
        this.openMeteoSaving = false;
      }
    },
    async testBeszelConnection() {
      // Mirrors testPortainerConnection — probes the hub with the
      // current form values (or saved password if the field is blank)
      // without mutating any settings. Result shown inline below the
      // Test button so the admin sees what went wrong without a toast.
      this.beszelTestResult = { pending: true };
      try {
        const r = await fetch('/api/beszel/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            hub_url:    (this.settings.beszel_hub_url || '').trim(),
            identity:   (this.settings.beszel_identity || '').trim(),
            password:   this.settings.beszel_password || '',
            verify_tls: !!this.settings.beszel_verify_tls,
          }),
        });
        const j = await r.json().catch(() => ({}));
        this.beszelTestResult = {
          pending: false,
          ok: !!j.ok,
          detail: j.detail || this.t(j.ok ? 'toasts_extra.test_result_ok' : 'toasts_extra.test_result_failed'),
          systems: j.systems || [],
        };
        if (j && j.ok) {
          this.recordTestSuccess('beszel');
        }
      } catch (_) {
        this.beszelTestResult = { pending: false, ok: false, detail: this.t('toasts.network_error') };
      }
    },
    async testPulseConnection() {
      // Mirrors testBeszelConnection — probes Pulse with the current
      // form values (or saved token when the field is blank).
      this.pulseTestResult = { pending: true };
      try {
        const r = await fetch('/api/pulse/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url:        (this.settings.pulse_url || '').trim(),
            token:      this.settings.pulse_token || '',
            verify_tls: !!this.settings.pulse_verify_tls,
          }),
        });
        const j = await r.json().catch(() => ({}));
        this.pulseTestResult = {
          pending: false,
          ok: !!j.ok,
          detail: j.detail || this.t(j.ok ? 'toasts_extra.test_result_ok' : 'toasts_extra.test_result_failed'),
          nodes: j.nodes || [],
        };
        if (j && j.ok) {
          this.recordTestSuccess('pulse');
        }
      } catch (_) {
        this.pulseTestResult = { pending: false, ok: false, detail: this.t('toasts.network_error') };
      }
    },
    async testWebminConnection() {
      // Probes ONE Webmin URL — user types the URL into webminTestUrl
      // since every Miniserv instance is per-host. Credentials come
      // from the settings form (or persisted values when blank).
      const url = (this.webminTestUrl || this.settings.webmin_url || '').trim();
      if (!url) {
        this.showToast(this.t('admin_hosts.webmin_test_url_required'), 'error');
        return;
      }
      this.webminTestResult = { pending: true };
      try {
        const r = await fetch('/api/webmin/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url,
            user:       (this.settings.webmin_user || '').trim(),
            password:   this.settings.webmin_password || '',
            verify_tls: !!this.settings.webmin_verify_tls,
          }),
        });
        const j = await r.json().catch(() => ({}));
        this.webminTestResult = {
          pending: false,
          ok: !!j.ok,
          detail: j.detail || this.t(j.ok ? 'toasts_extra.test_result_ok' : 'toasts_extra.test_result_failed'),
        };
        if (j && j.ok) {
          this.recordTestSuccess('webmin');
        }
      } catch (_) {
        this.webminTestResult = { pending: false, ok: false, detail: this.t('toasts.network_error') };
      }
    },
    async testPingConnection() {
      // One-shot live probe against a curated host. Server-side
      // honours unsaved overrides (port + transport are optional in
      // the body), so the operator can test before committing.
      const hid = (this.pingTestHostId || '').trim();
      if (!hid) {
        this.showToast(this.t('settings.host_stats.ping.test_no_hosts'), 'error');
        return;
      }
      this.pingTestResult = { pending: true };
      try {
        const r = await fetch('/api/ping/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ host_id: hid }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.pingTestResult = {
            pending: false, ok: false,
            detail: j.detail || this.t('toasts.save_failed'),
          };
          return;
        }
        const rtt = (j.rtt_ms != null) ? j.rtt_ms.toFixed(1) : '—';
        const loss = (j.loss_pct != null) ? j.loss_pct.toFixed(0) : '—';
        const detail = j.ok
          ? this.t('settings.host_stats.ping.test_result_alive', {
              host: j.host || hid, port: j.port || '?', rtt_ms: rtt, loss_pct: loss,
            })
          : this.t('settings.host_stats.ping.test_result_down', {
              host: j.host || hid, port: j.port || '?', error: j.error || '—',
            });
        this.pingTestResult = { pending: false, ok: !!j.ok, detail };
        if (j && j.ok) {
          this.recordTestSuccess('ping');
        }
      } catch (_) {
        this.pingTestResult = { pending: false, ok: false, detail: this.t('toasts.network_error') };
      }
    },
    // / SNMP test widget. UX-unified with the Ping test
    // : operator picks a curated SNMP-mapped host from the
    // dropdown, the helper looks up the row's `snmp_name` (target IP /
    // hostname) + per-row overrides (community / version / port /
    // v3 USM), and posts them to `/api/snmp/test`. Falls back to
    // persisted defaults server-side via `_resolve_field` for any
    // field the row doesn't override, so the operator doesn't need
    // to retype the community / v3 keys to validate one box.
    async testSnmpConnection() {
      const hid = (this.snmpTestHostId || '').trim();
      if (!hid) {
        this.showToast(this.t('settings.host_stats.snmp.test_no_hosts'), 'error');
        return;
      }
      const row = (Array.isArray(this.hostsConfig) ? this.hostsConfig : [])
        .find(h => h && h.id === hid);
      // Resolver chain mirrors the live sampler / `_merge_one_host`:
      // snmp_name → address → host_id (last-resort, rarely useful).
      const target = (
        ((row && row.snmp_name) || '').trim()
        || ((row && row.address) || '').trim()
        || hid
      ).trim();
      // Per-row overrides — only forwarded when the operator actually
      // set them on this row; blanks fall through to the global
      // defaults server-side.
      const ovr = (row && row.snmp) || {};
      const body = { host: target };
      if (ovr.community) {
        body.community = ovr.community;
      }
      if (ovr.version)   {
        body.version = ovr.version;
      }
      if (ovr.port)      {
        body.port = ovr.port;
      }
      if (ovr.v3_user)   {
        body.v3_user = ovr.v3_user;
      }
      if (ovr.v3_auth_key) {
        body.v3_auth_key = ovr.v3_auth_key;
      }
      if (ovr.v3_priv_key) {
        body.v3_priv_key = ovr.v3_priv_key;
      }
      this.snmpTestResult = { pending: true };
      try {
        const r = await fetch('/api/snmp/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.snmpTestResult = {
            pending: false, ok: false,
            detail: j.detail || this.t('toasts.save_failed'),
          };
          return;
        }
        this.snmpTestResult = {
          pending: false, ok: !!j.ok,
          detail: j.detail || (j.ok ? 'OK' : this.t('toasts.save_failed')),
        };
        if (j && j.ok) {
          this.recordTestSuccess('snmp');
        }
      } catch (_) {
        this.snmpTestResult = { pending: false, ok: false, detail: this.t('toasts.network_error') };
      }
    },
    async testNotify() {
      // Upgraded from a fire-and-forget toast to the Portainer-style
      // flow used by /api/portainer/test / /api/beszel/test / etc.:
      // populate a result chip + stamp per-medium last-passed-test
      // snapshots so the Save-before-Test gate (canSaveNotifications)
      // unlocks. Snapshots are captured BEFORE the request goes out
      // so any edit between Test-click + Save still re-locks Save.
      this.notifyTestResult = { pending: true };
      // Snapshot the test-relevant subset of each medium's form
      // values. Any edit AFTER a passing test invalidates this stamp
      // (canSaveNotifications compares live snapshot to stamp).
      const appriseSnap = this._appriseTestSnapshot();
      const telegramSnap = this._telegramTestSnapshot();
      try {
        const r = await fetch('/api/notify-test', { method: 'POST' });
        const ok = r.ok;
        let detail = '';
        try {
          const j = await r.json();
          detail = j.detail || j.status || (ok ? 'Test notification fired' : 'Test failed');
        } catch (_) {
          detail = ok ? 'Test notification fired' : 'Test failed';
        }
        this.notifyTestResult = { pending: false, ok, detail, _ts: Date.now() };
        if (ok) {
          // Stamp per-medium snapshots so Save unlocks. Each medium
          // independently — toggling apprise_enabled mid-test won't
          // re-lock telegram and vice versa.
          if (this.settings.apprise_enabled) {
            this._appriseLastPassedTest = appriseSnap;
            this.recordTestSuccess('apprise');
          }
          if (this.settings.notify_medium_telegram) {
            this._telegramLastPassedTest = telegramSnap;
            this.recordTestSuccess('telegram');
          }
          // Clear any stale Save chip so the merged box shows the
          // fresh Test outcome instead of the previous Save success.
          this.providersSaveResult = null;
          this.showToast(this.t('toasts.test_notification_sent'));
        } else {
          this.showToast(this.t('toasts.test_notification_failed'), 'error');
        }
      } catch (_) {
        this.notifyTestResult = { pending: false, ok: false, detail: this.t('toasts.network_error'), _ts: Date.now() };
        this.showToast(this.t('toasts.test_notification_failed'), 'error');
      }
    },
    async saveProviders() {
      // Providers-only POST — Apprise + Telegram + medium toggles +
      // in-app tunables. Test-before-Save gate applies.
      if (this.settingsSaving) {
        return;
      }
      if (!this.canSaveNotifications()) {
        return;
      }
      this.settingsSaving = true;
      this.providersSaveResult = null;
      try {
        const s = this.settings || {};
        const tf = this.tuningForm || {};
        const payload = {};
        // Apprise core
        payload.apprise_enabled = !!s.apprise_enabled;
        if (s.apprise_url != null)  {
          payload.apprise_url = s.apprise_url;
        }
        if (s.apprise_tag != null)  {
          payload.apprise_tag = s.apprise_tag;
        }
        // Telegram core
        if ((s.telegram_bot_token || '').trim()) {
          payload.telegram_bot_token = s.telegram_bot_token;
        }
        if (s.telegram_chat_id   != null) {
          payload.telegram_chat_id = s.telegram_chat_id;
        }
        if (s.telegram_thread_id != null) {
          payload.telegram_thread_id = s.telegram_thread_id;
        }
        payload.telegram_verify_tls = s.telegram_verify_tls ? 'true' : 'false';
        // Operator-tunable Bot API base URL — blank string is the
        // legitimate "clear override / fall back to upstream default"
        // signal, so always send it.
        if (s.telegram_api_base != null) {
          payload.telegram_api_base = (s.telegram_api_base || '').toString();
        }
        // Telegram Phase 2 listener config
        payload.telegram_listener_enabled  = s.telegram_listener_enabled ? 'true' : 'false';
        payload.telegram_allow_destructive = s.telegram_allow_destructive ? 'true' : 'false';
        if (s.telegram_authorized_user_ids != null) {
          payload.telegram_authorized_user_ids = s.telegram_authorized_user_ids;
        }
        // Per-medium fan-out toggles
        for (const k of (this.notifyMediumKeys || [])) {
          if (k in s) {
            payload[k] = s[k] ? 'true' : 'false';
          }
        }
        // In-app tunables
        for (const k of [
          'tuning_notification_retention_days',
          'tuning_notification_page_size',
          'tuning_notifications_poll_interval_seconds',
          // Telegram listener long-poll + outer-HTTP timeouts. Live
          // alongside the Telegram section UI in this same partial; an
          // edit lands through the Providers Save click rather than
          // requiring a round-trip to Admin → Config.
          'tuning_telegram_long_poll_timeout_seconds',
          'tuning_telegram_http_timeout_seconds',
          'tuning_telegram_ai_calls_per_minute',
          'tuning_telegram_bulk_update_concurrency',
        ]) {
          if (tf[k] != null && String(tf[k]).trim() !== '') {
            payload[k] = String(tf[k]).trim();
          }
        }
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        // Reload for fresh baselines + tuningForm reset.
        await Promise.all([this.loadSettings(), this.loadTuning()]);
        this._providersBaseline = this._providersSnapshot();
        this._appriseBaseline   = this._appriseSnapshot();
        this.providersSaveResult = {
          ok: true,
          detail: this.t('admin.notifications.providers_save_success') || 'Channels saved',
          _ts: Date.now(),
        };
        // Clear the stale Test chip so the merged-box helper renders
        // ONLY the fresh Save success — operator-flagged that stacked
        // Test+Save chips read as redundant on the happy path.
        this.notifyTestResult = null;
      } catch (e) {
        this.providersSaveResult = {
          ok: false,
          detail: String(e && e.message ? e.message : e),
          _ts: Date.now(),
        };
      } finally {
        this.settingsSaving = false;
      }
    },
};
