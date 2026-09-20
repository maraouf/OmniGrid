// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,JSUnresolvedReference,JSIgnoredPromiseFromCall
/* global Alpine, Swal, I18N, t, OG_VERSION */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Registries admin (Admin → Registries) — per-registry pull
// credentials for the update-check digest probe.
//
// Why this tab exists: OmniGrid asks each image's registry for the current
// manifest digest to decide "update available / up-to-date". Docker Hub had
// credentials (DOCKERHUB_USER / DOCKERHUB_TOKEN); every other registry was
// probed anonymously, so a private one answered 401 and the row went red with
// `status=error` — on services that were running perfectly.

export default {
  // Editor rows: {host, username, password, password_set, enabled,
  // clear_password}. `password` is the TYPED value only and is never
  // populated from the server — the API returns `password_set` instead.
  registryRows: [],
  registrySaving: false,
  // Per-row test state, keyed by row index: {pending, ok, detail}.
  registryTests: {},
  // Optional per-row repository used by Test to prove pull access.
  registryTestRepo: {},

  // Rebuild the editor rows from the loaded settings payload. Called from
  // loadSettings so a save / reload round-trips cleanly.
  hydrateRegistryRows() {
    const rows = (this.settings || {}).registry_credentials;
    this.registryRows = (Array.isArray(rows) ? rows : []).map(r => ({
      host: String((r && r.host) || ''),
      username: String((r && r.username) || ''),
      password: '',
      password_set: !!(r && r.password_set),
      enabled: (r && r.enabled) !== false,
      clear_password: false,
    }));
    this.registryTests = {};
  },
  addRegistryRow() {
    this.registryRows.push({
      host: '', username: '', password: '',
      password_set: false, enabled: true, clear_password: false,
    });
  },
  removeRegistryRow(idx) {
    this.registryRows.splice(idx, 1);
    this.registryTests = {};
  },
  // Dirty when the editor differs from what the server returned. A typed
  // password counts even though the server never sends one back.
  registriesSectionDirty() {
    const saved = Array.isArray((this.settings || {}).registry_credentials)
      ? this.settings.registry_credentials : [];
    const rows = this.registryRows || [];
    if (rows.length !== saved.length) {
      return true;
    }
    for (let i = 0; i < rows.length; i++) {
      const a = rows[i];
      const b = saved[i] || {};
      if (String(a.host || '').trim().toLowerCase() !== String(b.host || '').trim().toLowerCase()
        || String(a.username || '').trim() !== String(b.username || '').trim()
        || (a.enabled !== false) !== (b.enabled !== false)
        || String(a.password || '') !== ''
        || a.clear_password) {
        return true;
      }
    }
    return false;
  },
  async saveRegistriesSection() {
    if (this.registrySaving) {
      return;
    }
    // Validate before the POST so a half-filled row lands a toast rather
    // than a 400 the operator has to decode.
    for (const r of (this.registryRows || [])) {
      if (!String(r.host || '').trim()) {
        this.showToast(this.t('admin_registries.errors.host_required'), 'error');
        return;
      }
      if (!String(r.username || '').trim()) {
        this.showToast(this.t('admin_registries.errors.username_required',
          {host: r.host}), 'error');
        return;
      }
      if (!r.password_set && !String(r.password || '').trim()) {
        this.showToast(this.t('admin_registries.errors.password_required',
          {host: r.host}), 'error');
        return;
      }
    }
    this.registrySaving = true;
    try {
      const body = {
        registry_credentials: (this.registryRows || []).map(r => ({
          host: String(r.host || '').trim(),
          username: String(r.username || '').trim(),
          // Blank password = keep the stored one (server-side contract).
          password: String(r.password || ''),
          clear_password: !!r.clear_password,
          enabled: r.enabled !== false,
        })),
      };
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, res.status));
      }
      await this.loadSettings();
      this.hydrateRegistryRows();
      this.showToast(this.t('toasts.saved') || 'Saved', 'success');
      // The digest probe's caches were dropped server-side, so a refresh
      // now re-checks every image with the new credentials — otherwise the
      // rows stay red until the next gather and the save looks ineffective.
      try {
        await this.refresh(true);
      } catch (_) { /* best-effort live-apply; the next gather still picks it up */
      }
    } catch (e) {
      this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed')
        + ': ' + (e.message || e), 'error');
    } finally {
      this.registrySaving = false;
    }
  },
  async testRegistryRow(idx) {
    const row = (this.registryRows || [])[idx];
    if (!row || (this.registryTests[idx] || {}).pending) {
      return;
    }
    this.registryTests = {...this.registryTests, [idx]: {pending: true}};
    try {
      const res = await fetch('/api/registry/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify({
          host: String(row.host || '').trim(),
          username: String(row.username || '').trim(),
          // Blank = test the stored credential for this host.
          password: String(row.password || ''),
          repository: String(this.registryTestRepo[idx] || '').trim(),
        }),
      });
      const j = await res.json().catch(() => ({}));
      this.registryTests = {
        ...this.registryTests,
        [idx]: {
          pending: false,
          ok: !!(res.ok && j && j.ok),
          detail: String((j && j.detail) || ('HTTP ' + res.status)),
        },
      };
    } catch (e) {
      this.registryTests = {
        ...this.registryTests,
        [idx]: {pending: false, ok: false, detail: String((e && e.message) || e)},
      };
    }
  },
};
