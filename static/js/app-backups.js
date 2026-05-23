// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
// "Unused function" + "Element is not exported" warnings cluster on
// every Alpine-template-consumed method (`sortedBackups` /
// `saveRetention` / `deleteBackup` / `restoreBackup` /
// `restoreBackupFromFile`) — spread-imported into the Alpine root,
// templates dispatch via `@click` the static analyser can't see.
// Constant-on-RHS covers natural `x === 'Foo'` AND `x == null` /
// `x === ''` idioms (project convention, not Yoda). Nested `t()` /
// `encodeURIComponent()` / `parseInt()` are idiomatic pipelines.
// `throw new Error(await r.text())` inside try/catch is the canonical
// unified-error pattern — the throw constructs the error object,
// the catch surfaces it via toast. Empty catch + unused `_` are
// the ignore-and-move-on shape on localStorage persistence + Swal
// dismissals. `continue` is the section-saves-its-own-tunables
// dirty-check pattern. Overly-complex bool is the multi-condition
// section-dirty / retention-changed predicate.
// noinspection JSUnusedGlobalSymbols,UnusedFunctionJS,ElementNotExported
// noinspection ConstantOnRightSideOfComparisonJS,JSConstantOnRightSideOfComparison
// noinspection AnonymousFunctionJS
// noinspection ContinueStatementJS,BreakStatementJS
// noinspection UnusedCatchParameterJS,EmptyCatchBlockJS
// noinspection OverlyComplexBooleanExpressionJS,OverlyComplexBooleanExpression
// noinspection NestedFunctionCallJS
// noinspection ExceptionCaughtLocallyJS,JSExceptionCaughtLocally
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Backups + Config-Backup surfaces (Admin → Backups).


export default {
  retentionSaving: false,  // Backups → retention save
  backups: [],
  backupBusy: false,
  // Settings-as-Code (Admin → Config backup) state. `configBackupSaved`
  // is the list of saved snapshot files from
  // /api/admin/config-backup/list. `configBackupBusy` gates every
  // button on the tab to prevent overlapping requests.
  configBackupSaved: [],
  configBackupBusy: false,
  backupsLoaded: false,
  // Sort state for the Backups admin table. Default `col: ''` = no
  // sort (server-supplied order is newest-first by mtime). Routes
  // through the shared `_sortToggle` + `_sortRows` helpers.
  backupsSort: {col: '', dir: 'desc'},
  sortedBackups() {
    return this._sortRows(this.backups || [], this.backupsSort);
  },

  // ----- Backups ------------------------------------------------------
  async loadBackups() {
    try {
      const r = await fetch('/api/backups');
      if (r.ok) {
        const d = await r.json();
        this.backups = d.backups || [];
      }
    } catch (_) {
    } finally {
      this.backupsLoaded = true;
    }
  },
  async createBackup() {
    if (this.backupBusy) {
      return;
    }
    this.backupBusy = true;
    try {
      const r = await fetch('/api/backups', {method: 'POST'});
      if (r.ok) {
        const d = await r.json().catch(() => ({}));
        if (Array.isArray(d.pruned) && d.pruned.length) {
          // Distinct toast when retention actually trimmed something;
          // operators care about being told what disappeared.
          this.showToast(this.t('toasts.backup_created_pruned', {count: d.pruned.length}));
        } else {
          this.showToast(this.t('toasts.backup_created'));
        }
        await this.loadBackups();
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.backup_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.backupBusy = false;
    }
  },

  // ---------------------------------------------------------------
  // Settings-as-Code (Admin → Config backup)
  // ---------------------------------------------------------------
  // Trigger a download of the current admin configuration as a JSON
  // file. Goes through the same /api/admin/config-backup/export
  // endpoint that emits Content-Disposition: attachment; the browser
  // does the rest. Operators commit the file to a private git repo
  // for change tracking. Secrets are redacted server-side to the
  // sentinel "__OMITTED__"; on import those entries are skipped so
  // the live DB's secret material is preserved.
  async downloadConfigBackup() {
    if (this.configBackupBusy) {
      return;
    }
    this.configBackupBusy = true;
    try {
      // Anchor-click pattern matches every other download path in
      // the SPA (logs export, AI conversation export, etc.) so the
      // browser handles the filename + content-type correctly.
      const a = document.createElement('a');
      a.href = '/api/admin/config-backup/export';
      // The endpoint sets Content-Disposition with a timestamped
      // name; setting `download` here guarantees the prompt even
      // if a future version drops the header.
      a.download = '';
      // `getElementsByTagName('body')[0]` is the IDE's XHTML-safe
      // alternative to `document.body` — the anchor must be in the
      // body for `.click()` to dispatch a real navigation. Capture
      // into a local once so the append + remove pair share one
      // lookup AND the static analyser doesn't flag two property
      // accesses on `document.body`.
      const bodyEl = document.getElementsByTagName('body')[0];
      bodyEl.appendChild(a);
      a.click();
      bodyEl.removeChild(a);
    } finally {
      // Tiny delay so the browser registers the click before the
      // button re-enables; otherwise rapid double-click can fire
      // two downloads.
      setTimeout(() => {
        this.configBackupBusy = false;
      }, 250);
    }
  },
  async saveConfigBackupToDisk() {
    if (this.configBackupBusy) {
      return;
    }
    this.configBackupBusy = true;
    try {
      const r = await fetch('/api/admin/config-backup/save', {method: 'POST'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      const d = await r.json().catch(() => ({}));
      this.showToast(this.t('toasts_extra.config_backup_saved', {name: d.name || ''}), 'success');
      await this.loadConfigBackupSaved();
    } catch (e) {
      this.showToast(this.t('toasts_extra.config_backup_save_failed', {error: (e && e.message) || ''}), 'error');
    } finally {
      this.configBackupBusy = false;
    }
  },
  async loadConfigBackupSaved() {
    try {
      const r = await fetch('/api/admin/config-backup/list');
      if (!r.ok) {
        return;
      }
      const d = await r.json();
      this.configBackupSaved = Array.isArray(d.files) ? d.files : [];
    } catch (_) {
    } finally {
      this.snapshotsLoaded = true;
    }
  },
  // File-input handler — operator picks a JSON snapshot, we parse
  // client-side (so we can show a helpful error before the round-
  // trip), then POST to /api/admin/config-backup/import. The
  // confirm here is the inline-popover pattern's modal SwAl
  // counterpart — restoring config is destructive enough that an
  // explicit "yes" is appropriate even though the file picker
  // already implies intent.
  async importConfigBackupFile(ev) {
    const file = ev && ev.target && ev.target.files && ev.target.files[0];
    if (!file) {
      return;
    }
    // Reset the input so the same file can be re-picked after a
    // failed import (browsers don't fire `change` for the same
    // value twice in a row).
    ev.target.value = '';
    if (this.configBackupBusy) {
      return;
    }
    let payload;
    try {
      const text = await file.text();
      payload = JSON.parse(text);
    } catch (e) {
      this.showToast(this.t('toasts_extra.config_backup_invalid_json', {error: (e && e.message) || ''}), 'error');
      return;
    }
    const ok = await this.confirmDialog({
      title: this.t('admin.config_backup.import_confirm_title'),
      html: this.t('admin.config_backup.import_confirm_body', {name: file.name}),
      icon: 'warning',
      confirmText: this.t('admin.config_backup.import_confirm_button'),
      focusConfirm: false,
    });
    if (!ok) {
      return;
    }
    this.configBackupBusy = true;
    try {
      const r = await fetch('/api/admin/config-backup/import', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({payload}),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      const d = await r.json().catch(() => ({}));
      this.showToast(this.t('toasts_extra.config_backup_imported', {
        settings: d.settings_applied || 0,
        schedules: d.schedules_replaced || 0,
      }), 'success');
      // Reload settings + schedules so the UI reflects the imported
      // state immediately (no manual refresh).
      await this.loadSettings();
      if (typeof this.loadSchedules === 'function') {
        await this.loadSchedules();
      }
      if (typeof this.loadTuning === 'function') {
        await this.loadTuning();
      }
    } catch (e) {
      this.showToast(this.t('toasts_extra.config_backup_import_failed', {error: (e && e.message) || ''}), 'error');
    } finally {
      this.configBackupBusy = false;
    }
  },
  async restoreConfigBackupSaved(name) {
    if (this.configBackupBusy) {
      return;
    }
    const ok = await this.confirmDialog({
      title: this.t('admin.config_backup.import_confirm_title'),
      html: this.t('admin.config_backup.import_confirm_body', {name: name}),
      icon: 'warning',
      confirmText: this.t('admin.config_backup.import_confirm_button'),
      focusConfirm: false,
    });
    if (!ok) {
      return;
    }
    this.configBackupBusy = true;
    try {
      const r = await fetch('/api/admin/config-backup/saved/' + encodeURIComponent(name) + '/restore', {
        method: 'POST',
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      const d = await r.json().catch(() => ({}));
      this.showToast(this.t('toasts_extra.config_backup_imported', {
        settings: d.settings_applied || 0,
        schedules: d.schedules_replaced || 0,
      }), 'success');
      await this.loadSettings();
      if (typeof this.loadSchedules === 'function') {
        await this.loadSchedules();
      }
      if (typeof this.loadTuning === 'function') {
        await this.loadTuning();
      }
    } catch (e) {
      this.showToast(this.t('toasts_extra.config_backup_import_failed', {error: (e && e.message) || ''}), 'error');
    } finally {
      this.configBackupBusy = false;
    }
  },
  async deleteConfigBackupSaved(name) {
    if (this.configBackupBusy) {
      return;
    }
    const ok = await this.confirmDialog({
      title: this.t('admin.config_backup.delete_confirm_title'),
      html: this.t('admin.config_backup.delete_confirm_body', {name: name}),
      icon: 'warning',
      confirmText: this.t('actions.delete'),
      focusConfirm: false,
    });
    if (!ok) {
      return;
    }
    this.configBackupBusy = true;
    try {
      const r = await fetch('/api/admin/config-backup/saved/' + encodeURIComponent(name), {
        method: 'DELETE',
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || ('HTTP ' + r.status));
      }
      this.showToast(this.t('toasts_extra.config_backup_deleted', {name}), 'success');
      await this.loadConfigBackupSaved();
    } catch (e) {
      this.showToast(this.t('toasts_extra.config_backup_delete_failed', {error: (e && e.message) || ''}), 'error');
    } finally {
      this.configBackupBusy = false;
    }
  },

  // Admin → Config backup — section-owned save for the retention
  // tunable. Only one tuning key (`tuning_config_backup_retention_count`)
  // and zero plain settings, so the helper is a degenerate version
  // of the SNMP / Port Scan section pattern. Dirty-flag pulses the
  // amber Save ring + "Unsaved" indicator next to the input; Save
  // commits via the existing additive `/api/settings` POST and then
  // re-baselines via `loadTuning()`.
  _configBackupSectionTuningKeys() {
    return ['tuning_config_backup_retention_count'];
  },
  configBackupSectionDirty() {
    try {
      const baseline = this._tuningBaselineMap();
      for (const k of this._configBackupSectionTuningKeys()) {
        const cur = (this.tuningForm || {})[k];
        const curStr = (cur == null ? '' : String(cur).trim());
        const baseStr = (baseline[k] == null ? '' : String(baseline[k]).trim());
        if (curStr !== baseStr) {
          return true;
        }
      }
    } catch (_) {
    }
    return false;
  },
  async saveConfigBackupSection() {
    if (this.configBackupBusy) {
      return;
    }
    // Per-key int + bounds validation. Blank input clears the DB
    // override (falls back to env / default) — same contract every
    // other section's save uses.
    for (const k of this._configBackupSectionTuningKeys()) {
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
    this.configBackupBusy = true;
    try {
      const body = {};
      for (const k of this._configBackupSectionTuningKeys()) {
        const v = (this.tuningForm || {})[k];
        body[k] = (v == null ? '' : String(v).trim());
      }
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      await this.loadTuning();
      this.showToast(this.t('toasts.saved') || 'Saved', 'success');
    } catch (e) {
      this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed') + ': ' + (e.message || e), 'error');
    } finally {
      this.configBackupBusy = false;
    }
  },
  async saveRetention() {
    if (this.retentionSaving) {
      return;
    }
    this.retentionSaving = true;
    // Backups section owns its retention tunable. Per the
    // section-saves-its-own-tunables convention, this handler
    // posts BOTH the legacy `backup_retention_count` (back-compat
    // for any old code path still reading it) AND the canonical
    // `tuning_backup_retention_count` in one body — no chain to
    // saveTuning.
    const tuningV = (this.tuningForm || {})['tuning_backup_retention_count'];
    const n = Math.max(0, parseInt(this.settings.backup_retention_count, 10) || 0);
    try {
      const body = {backup_retention_count: n};
      body['tuning_backup_retention_count'] = (tuningV == null ? '' : String(tuningV).trim());
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (r.ok) {
        this.settings.backup_retention_count = n;
        // Refresh both legacy settings + tuning baseline so the
        // section's dirty tracker resets cleanly after the save.
        await this.loadTuning();
        this.showToast(this.t('toasts.retention_saved'));
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.save_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.retentionSaving = false;
    }
  },

  async deleteBackup(b) {
    const res = await Swal.fire({
      title: this.t('admin.backups.delete_prompt_title'), text: b.name, icon: 'warning',
      showCancelButton: true, confirmButtonText: this.t('actions.delete'),
      cancelButtonText: this.t('actions.cancel'),
    });
    if (!res.isConfirmed) {
      return;
    }
    try {
      const r = await fetch('/api/backups/' + encodeURIComponent(b.name), {method: 'DELETE'});
      if (r.ok) {
        this.showToast(this.t('toasts.backup_deleted'));
        await this.loadBackups();
      } else {
        this.showToast(this.t('toasts.delete_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },
  async restoreBackup(b) {
    const res = await Swal.fire({
      title: this.t('admin.backups.restore_prompt_title', {name: b.name}),
      html: this.t('admin.backups.restore_prompt_html'),
      icon: 'warning', showCancelButton: true,
      confirmButtonText: this.t('admin.backups.restore_button'),
      cancelButtonText: this.t('actions.cancel'),
      confirmButtonColor: this._cssVar('--danger'),
    });
    if (!res.isConfirmed) {
      return;
    }
    try {
      const r = await fetch('/api/backups/' + encodeURIComponent(b.name) + '/restore', {method: 'POST'});
      if (r.ok) {
        const d = await r.json();
        const safety = d.safety_snapshot
          ? this.t('admin.backups.restore_complete_safety', {name: d.safety_snapshot})
          : '';
        await Swal.fire({
          title: this.t('admin.backups.restore_complete_title'),
          html: this.t('admin.backups.restore_complete_html', {
            from: d.restored_from, count: d.avatar_count, safety,
          }) + this.t('admin.backups.restore_complete_signout'),
          icon: 'success',
        });
        await this.loadBackups();
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.restore_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },
  async restoreBackupFromFile(ev) {
    const file = ev.target.files && ev.target.files[0];
    if (!file) {
      return;
    }
    const res = await Swal.fire({
      title: this.t('admin.backups.restore_upload_title'),
      html: this.t('admin.backups.restore_upload_html', {
        name: file.name, size: (file.size / 1000000).toFixed(1),
      }),
      icon: 'warning', showCancelButton: true,
      confirmButtonText: this.t('admin.backups.restore_button'),
      cancelButtonText: this.t('actions.cancel'),
      confirmButtonColor: this._cssVar('--danger'),
    });
    if (!res.isConfirmed) {
      ev.target.value = '';
      return;
    }
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/api/backups/restore', {method: 'POST', body: fd});
      if (r.ok) {
        const d = await r.json();
        const safety = d.safety_snapshot
          ? this.t('admin.backups.restore_complete_safety', {name: d.safety_snapshot})
          : '';
        await Swal.fire({
          title: this.t('admin.backups.restore_complete_title'),
          html: this.t('admin.backups.restore_complete_html', {
            from: d.restored_from, count: d.avatar_count, safety,
          }) + this.t('admin.backups.restore_complete_signout'),
          icon: 'success',
        });
        await this.loadBackups();
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.restore_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      ev.target.value = '';
    }
  },
};
