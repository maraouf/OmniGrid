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
// Admin → Apps surface (Templates + Instances tabs): catalog CRUD,
// per-template editor + port editor, instance editor (Docker-link
// autocomplete + per-port editor + api_key field), bulk select +
// delete, per-template pin-to-host modal, discover wizard. Split
// from app-apps.js when it crossed the 3000-line split-candidate
// threshold.

export default {
  // App-extras freshness TTL — relocated from Admin → Config into this
  // Admin → Apps tab. Section-owned save mirrors the public-IP / provider
  // sections: editing the tunable flips the same amber Save ring, and
  // saveAppsSettingsSection() POSTs it in one body so the dirty/undirty
  // round-trip is clean. State + the three helpers live here (the Admin →
  // Apps module) so the partial's bindings resolve on the merged component.
  appsSettingsSaving: false,
  _appsSettingsSectionTuningKeys() {
    return ['tuning_apps_extras_ttl_seconds', 'tuning_apps_tile_render_batch'];
  },
  appsSettingsSectionDirty() {
    try {
      const baseline = this._tuningBaselineMap();
      for (const k of this._appsSettingsSectionTuningKeys()) {
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
  async saveAppsSettingsSection() {
    if (this.appsSettingsSaving) {
      return;
    }
    // Validate every tunable against its declared (min, max) bounds BEFORE
    // the POST so a typo lands a toast instead of a partial save.
    for (const k of this._appsSettingsSectionTuningKeys()) {
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
    this.appsSettingsSaving = true;
    try {
      const body = {};
      for (const k of this._appsSettingsSectionTuningKeys()) {
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
        throw new Error(this.fmtApiError(j, r.status));
      }
      await Promise.all([this.loadSettings(), this.loadTuning()]);
      this.showToast(this.t('toasts.saved') || 'Saved', 'success');
    } catch (e) {
      this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed') + ': ' + (e.message || e), 'error');
    } finally {
      this.appsSettingsSaving = false;
    }
  },
  setAppsInstancesGroupBy(mode) {
    if (!['none', 'host', 'service'].includes(mode)) {
      return;
    }
    this.appsInstancesGroupBy = mode;
    try {
      localStorage.setItem('appsInstancesGroupBy', mode);
    } catch (_) {
      // private mode — in-memory only
    }
  },

  toggleAppsInstanceGroup(key) {
    this.appsInstancesCollapsed = Object.assign({}, this.appsInstancesCollapsed,
      {[key]: !this.appsInstancesCollapsed[key]});
  },

  // Collapse / expand every instance group at once. Operate over the
  // currently-rendered groups (respects the active group-by mode) so
  // the buttons only ever touch real header keys; the '__all__'
  // single-group ('none' mode) has no collapsible header so it's
  // skipped. Replace the whole map (Alpine proxy reactivity) rather
  // than mutating in place.
  collapseAllAppsInstanceGroups() {
    const next = {};
    for (const grp of this.appsInstancesGroups()) {
      if (grp.key !== '__all__') {
        next[grp.key] = true;
      }
    }
    this.appsInstancesCollapsed = next;
  },

  expandAllAppsInstanceGroups() {
    this.appsInstancesCollapsed = {};
  },

  // Grouped render structure for the instances table — one entry per
  // group: {key, label, count, items}. Rendered as one <tbody> per
  // group (valid HTML, keeps column alignment with the shared thead)
  // with a collapsible header row. 'none' returns a single group with
  // an empty key/label so the markup suppresses its header row.
  // Free-text filter for the Instances table — matches name / catalog /
  // host (id / label / address) / port number / protocol, case-insensitive.
  appsInstancesMatch(inst, q) {
    if (!inst) {
      return false;
    }
    const hay = [
      inst.name || '', inst.catalog_name || '', inst.catalog_slug || '',
      inst.host_id || '', inst.host_label || '', inst.host_address || '',
      (inst.ports || []).map((p) => (p && p.port != null ? p.port : '')).join(' '),
      (inst.ports || []).map((p) => (p && p.protocol) || '').join(' '),
    ].join(' ').toLowerCase();
    return hay.includes(q);
  },

  appsInstancesMatchCount() {
    const q = (this.appsInstancesSearch || '').trim().toLowerCase();
    const list = Array.isArray(this.appsInstances) ? this.appsInstances : [];
    if (!q) {
      return list.length;
    }
    return list.filter((inst) => this.appsInstancesMatch(inst, q)).length;
  },

  appsInstancesGroups() {
    let list = Array.isArray(this.appsInstances) ? this.appsInstances : [];
    const q = (this.appsInstancesSearch || '').trim().toLowerCase();
    if (q) {
      list = list.filter((inst) => this.appsInstancesMatch(inst, q));
    }
    const mode = this.appsInstancesGroupBy || 'host';
    if (mode === 'none') {
      return [{key: '__all__', label: '', count: list.length, items: list}];
    }
    const groups = {};
    const order = [];
    for (const inst of list) {
      let key;
      let label;
      if (mode === 'service') {
        label = inst.catalog_name || inst.name || (this.t('admin_apps.unlinked') || '— unlinked —');
        key = 'svc:' + label.toLowerCase();
      } else {
        key = 'host:' + (inst.host_id || '');
        label = (typeof this.appsInstanceHostTitle === 'function' ? this.appsInstanceHostTitle(inst) : '')
          || inst.host_address || inst.host_id;
      }
      if (!groups[key]) {
        groups[key] = {key, label, items: []};
        order.push(key);
      }
      groups[key].items.push(inst);
    }
    order.sort((a, b) => (groups[a].label || '').toLowerCase().localeCompare((groups[b].label || '').toLowerCase()));
    return order.map((key) => ({
      key, label: groups[key].label, count: groups[key].items.length, items: groups[key].items,
    }));
  },

  // ----------------------------------------------------------------
  // Admin → Apps — catalog template CRUD.
  // ----------------------------------------------------------------
  async loadAppsCatalog() {
    try {
      const r = await fetch('/api/services/catalog');
      if (!r.ok) {
        if (r.status === 401 || r.status === 403) {
          return;
        }
        throw new Error('HTTP ' + r.status);
      }
      const j = await r.json();
      this.appsCatalog = Array.isArray(j.entries) ? j.entries : [];
      this.appsCatalogLoaded = true;
    } catch (err) {
      this.appsCatalogStatus = err && err.message ? err.message : String(err);
    }
  },

  async loadAppsInstances() {
    try {
      const r = await fetch('/api/apps/instances');
      if (!r.ok) {
        if (r.status === 401 || r.status === 403) {
          return;
        }
        throw new Error('HTTP ' + r.status);
      }
      const j = await r.json();
      this.appsInstances = Array.isArray(j.instances) ? j.instances : [];
      this.appsInstancesStatus = '';
      this.appsInstancesLoaded = true;
    } catch (err) {
      // Mirror loadAppsCatalog: on a transient blip, record the error
      // but LEAVE appsInstances intact so a prior good list survives
      // (don't blank the table). Mark loaded so the skeleton clears.
      this.appsInstancesStatus = err && err.message ? err.message : String(err);
      this.appsInstancesLoaded = true;
    }
  },

  openAppCatalogNew() {
    this.appsCatalogEdit = {
      id: null,
      name: '',
      slug: '',
      icon: '',
      description: '',
      show_extras: false,
      default_ports: [],
    };
    this.appsCatalogEditError = '';
    this.appsCatalogEditOpen = true;
    // Templates tab is the only place the editor lives. Force the
    // tab strip back to Templates when opening so the editor is
    // visible even if the operator is currently on Instances.
    this.appsAdminTab = 'templates';
    // Snapshot the (empty) new-template form so the first typed field
    // dirties Save. See `appsCatalogDirty()`.
    this._appsCatalogEditSnapshot = this._appsCatalogFormSig();
    this._scrollAppsEditorIntoView();
  },

  openAppCatalogEdit(entry) {
    if (!entry) {
      return;
    }
    this.appsCatalogEdit = {
      id: entry.id,
      name: entry.name || '',
      slug: entry.slug || '',
      icon: entry.icon || '',
      description: entry.description || '',
      show_extras: !!entry.show_extras,
      // Deep-clone the port list so editor edits don't mutate the
      // table's reactive row before Save.
      default_ports: JSON.parse(JSON.stringify(entry.default_ports || [])),
    };
    this.appsCatalogEditError = '';
    this.appsCatalogEditOpen = true;
    this.appsAdminTab = 'templates';
    this._appsCatalogEditSnapshot = this._appsCatalogFormSig();
    this._scrollAppsEditorIntoView();
  },

  // Stable signature of the template (catalog) editor form for dirty
  // tracking — folds name / slug / icon / description / show_extras +
  // the default_ports array into one string. Drives `appsCatalogDirty()`.
  _appsCatalogFormSig() {
    const e = this.appsCatalogEdit || {};
    const ports = Array.isArray(e.default_ports) ? e.default_ports.map((p) => ({
      port: p.port, protocol: p.protocol, label: p.label,
      probe_path: p.probe_path, probe_status: p.probe_status, open_url: p.open_url,
    })) : [];
    return JSON.stringify({
      name: e.name || '', slug: e.slug || '', icon: e.icon || '',
      description: e.description || '',
      show_extras: !!e.show_extras,
      ports,
    });
  },

  // True when the template editor form differs from its open-time
  // snapshot — gates the template Save button (operator-flagged: Save
  // didn't react to field edits). A brand-new template (openAppCatalogNew)
  // snapshots an empty form, so typing a name immediately dirties it.
  appsCatalogDirty() {
    if (!this.appsCatalogEditOpen) {
      return false;
    }
    return this._appsCatalogFormSig() !== (this._appsCatalogEditSnapshot || '');
  },

  // Smooth-scroll the editor anchor into view after Alpine renders the
  // x-show'd block. Double `$nextTick` covers the case where the
  // editor's parent (the Templates tab) was hidden when the click
  // landed; first tick reveals the tab, second tick measures the
  // newly-laid-out editor. Best-effort — silent no-op if the anchor
  // isn't in the DOM (template not loaded, ancestor display:none, etc.).
  _scrollAppsEditorIntoView() {
    if (!this.$nextTick) {
      return;
    }
    this.$nextTick(() => {
      this.$nextTick(() => {
        const anchor = document.querySelector('[data-apps-editor-anchor]');
        if (anchor && anchor.scrollIntoView) {
          anchor.scrollIntoView({behavior: 'smooth', block: 'start'});
        }
        // Move keyboard focus to the first text input inside the
        // editor so the operator can start typing immediately.
        const firstInput = anchor && anchor.querySelector('input[type="text"]');
        if (firstInput && firstInput.focus) {
          firstInput.focus({preventScroll: true});
        }
      });
    });
  },

  // Tab strip click handler — close the editor when leaving Templates
  // so the form doesn't linger on the Instances tab (where it would
  // sit awkwardly below the instance table with no visible header).
  setAppsAdminTab(tab) {
    if (this.appsAdminTab === tab) {
      return;
    }
    if (tab !== 'templates' && this.appsCatalogEditOpen) {
      this.closeAppCatalogEditor();
    }
    this.appsAdminTab = tab;
  },

  closeAppCatalogEditor() {
    this.appsCatalogEditOpen = false;
    this.appsCatalogEdit = {};
    this.appsCatalogEditError = '';
  },

  addAppCatalogPort() {
    if (!this.appsCatalogEdit.default_ports) {
      this.appsCatalogEdit.default_ports = [];
    }
    this.appsCatalogEdit.default_ports.push({
      port: 80,
      protocol: 'tcp',
      label: '',
      probe_path: '/',
      probe_status: 0,
      open_url: false,
    });
  },

  removeAppCatalogPort(idx) {
    if (!this.appsCatalogEdit.default_ports) {
      return;
    }
    if (idx < 0 || idx >= this.appsCatalogEdit.default_ports.length) {
      return;
    }
    this.appsCatalogEdit.default_ports.splice(idx, 1);
  },

  async saveAppCatalogEntry() {
    if (this.appsCatalogSaving) {
      return;
    }
    const entry = this.appsCatalogEdit || {};
    if (!entry.name || !entry.name.trim()) {
      this.appsCatalogEditError = this.t('admin_apps.error_name_required') || 'Name is required';
      return;
    }
    this.appsCatalogSaving = true;
    this.appsCatalogEditError = '';
    const body = {
      name: entry.name.trim(),
      slug: (entry.slug || '').trim(),
      icon: (entry.icon || '').trim(),
      description: (entry.description || '').trim(),
      default_ports: entry.default_ports || [],
    };
    try {
      const url = entry.id
        ? `/api/services/catalog/${entry.id}`
        : '/api/services/catalog';
      const method = entry.id ? 'PATCH' : 'POST';
      const r = await fetch(url, {
        method,
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      await this.loadAppsCatalog();
      this.closeAppCatalogEditor();
      if (typeof this.toast === 'function') {
        this.toast(this.t('admin_apps.saved') || 'Template saved', 'success');
      }
    } catch (err) {
      this.appsCatalogEditError = err && err.message ? err.message : String(err);
    } finally {
      this.appsCatalogSaving = false;
    }
  },

  async deleteAppCatalogEntry(entry) {
    if (!entry || !entry.id) {
      return;
    }
    const confirmed = typeof this.confirmDialog === 'function'
      ? await this.confirmDialog({
        title: this.t('admin_apps.delete_confirm_title', {name: entry.name || ''})
          || ('Delete ' + (entry.name || 'template') + '?'),
        text: this.t('admin_apps.delete_confirm_text')
          || 'Per-host chips linked to this template will keep their own name + icon, just lose the catalog binding.',
        icon: 'warning',
        confirmButtonText: this.t('actions.delete') || 'Delete',
      })
      : window.confirm('Delete "' + (entry.name || 'template') + '"?');
    if (!confirmed) {
      return;
    }
    try {
      const r = await fetch(`/api/services/catalog/${entry.id}`, {method: 'DELETE'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      await this.loadAppsCatalog();
      if (typeof this.toast === 'function') {
        this.toast(this.t('admin_apps.deleted') || 'Template deleted', 'success');
      }
    } catch (err) {
      if (typeof this.toast === 'function') {
        this.toast((this.t('admin_apps.delete_failed') || 'Delete failed: ') + err.message, 'error');
      }
    }
  },

  // Free-text filter for the Templates table — matches the search term
  // (case-insensitive) against the template name, slug, description, and
  // any of its default-port numbers. Empty search returns the full list.
  filteredAppsCatalog() {
    const q = (this.appsCatalogSearch || '').trim().toLowerCase();
    const list = Array.isArray(this.appsCatalog) ? this.appsCatalog : [];
    if (!q) {
      return list;
    }
    return list.filter((e) => {
      if (!e) {
        return false;
      }
      const hay = [
        e.name || '', e.slug || '', e.description || '',
        (e.default_ports || []).map((p) => (p && p.port != null ? p.port : '')).join(' '),
        (e.default_ports || []).map((p) => (p && p.protocol) || '').join(' '),
      ].join(' ').toLowerCase();
      return hay.includes(q);
    });
  },

  // True when a template port number is in the proposal's matched set
  // (detected open on the host). Number-coerced both sides so an int
  // template port matches a string/number matched_ports entry.
  discoverPortIsMatched(prop, portNum) {
    const m = (prop && prop.matched_ports) || [];
    const n = Number(portNum);
    return m.some((p) => Number(p) === n);
  },

  // Resolve a Discover-proposal port NUMBER to its catalog-template port
  // definition so the matched / unmatched chips can show the protocol kind
  // (/http, /https, /tcp) + the open-as-URL marker like every other port
  // view. matched_ports / unmatched_ports are bare numbers; the metadata
  // lives on prop.catalog.default_ports. Falls back to tcp / no-url.
  discoverPortMeta(prop, port) {
    const dp = (prop && prop.catalog && prop.catalog.default_ports) || [];
    const found = dp.find((p) => p && Number(p.port) === Number(port));
    return found || {protocol: 'tcp', open_url: false};
  },

  // ----------------------------------------------------------------
  // Templates bulk-delete — multi-select rows in the Admin → Apps →
  // Templates table, delete every selected catalog template in one
  // action. Keyed by catalog id; reassigning appsCatalogSelected (not
  // mutating in place) keeps Alpine's row checkboxes + bulk bar reactive.
  // Template delete is by id (no index-shift), so order doesn't matter.
  // ----------------------------------------------------------------
  catalogSelKey(entry) {
    return entry && entry.id != null ? String(entry.id) : '';
  },

  isCatalogSelected(entry) {
    return !!this.appsCatalogSelected[this.catalogSelKey(entry)];
  },

  toggleCatalogSelected(entry) {
    const k = this.catalogSelKey(entry);
    if (!k) {
      return;
    }
    const next = Object.assign({}, this.appsCatalogSelected);
    if (next[k]) {
      delete next[k];
    } else {
      next[k] = true;
    }
    this.appsCatalogSelected = next;
  },

  appsCatalogSelectedCount() {
    const s = this.appsCatalogSelected || {};
    return Object.keys(s).filter((k) => s[k]).length;
  },

  appsCatalogAllSelected() {
    // Reflects the VISIBLE (filtered) rows so select-all + the header
    // checkbox track what the operator can actually see.
    const list = this.filteredAppsCatalog();
    if (!list.length) {
      return false;
    }
    return list.every((e) => !!this.appsCatalogSelected[this.catalogSelKey(e)]);
  },

  toggleSelectAllCatalog() {
    const list = this.filteredAppsCatalog();
    if (this.appsCatalogAllSelected()) {
      // Deselect only the visible rows; selections outside the current
      // filter are left intact.
      const next = Object.assign({}, this.appsCatalogSelected);
      for (const e of list) {
        delete next[this.catalogSelKey(e)];
      }
      this.appsCatalogSelected = next;
      return;
    }
    const next = Object.assign({}, this.appsCatalogSelected);
    for (const e of list) {
      next[this.catalogSelKey(e)] = true;
    }
    this.appsCatalogSelected = next;
  },

  clearCatalogSelection() {
    this.appsCatalogSelected = {};
  },

  async bulkDeleteCatalog() {
    const sel = (this.appsCatalog || []).filter(
      (e) => e && this.appsCatalogSelected[this.catalogSelKey(e)]);
    const n = sel.length;
    if (!n) {
      return;
    }
    const confirmed = typeof this.confirmDialog === 'function'
      ? await this.confirmDialog({
        title: this.t('admin_apps.bulk_delete_templates_confirm_title', {n})
          || ('Delete ' + n + ' template' + (n === 1 ? '' : 's') + '?'),
        text: this.t('admin_apps.bulk_delete_templates_confirm_text')
          || 'Per-host chips linked to these templates keep their own name + icon, just lose the catalog binding.',
        icon: 'warning',
        confirmButtonText: this.t('actions.delete') || 'Delete',
      })
      : window.confirm('Delete ' + n + ' templates?');
    if (!confirmed) {
      return;
    }
    this.appsCatalogBulkDeleting = true;
    let ok = 0;
    let failed = 0;
    for (const entry of sel) {
      try {
        const r = await fetch(`/api/services/catalog/${entry.id}`, {method: 'DELETE'});
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(this.fmtApiError(j, r.status));
        }
        ok += 1;
      } catch (_e) {
        failed += 1;
      }
    }
    this.appsCatalogSelected = {};
    this.appsCatalogBulkDeleting = false;
    await this.loadAppsCatalog();
    if (typeof this.toast === 'function') {
      if (failed) {
        const msg = this.t('admin_apps.bulk_delete_partial', {ok, failed})
          || (ok + ' deleted, ' + failed + ' failed');
        this.toast(msg, ok ? 'warning' : 'error');
      } else {
        this.toast(this.t('admin_apps.bulk_deleted_templates', {n: ok})
          || (ok + ' templates deleted'), 'success');
      }
    }
  },

  // ----------------------------------------------------------------
  // Apps → Instances tab — edit / delete a pinned chip in place. Chips
  // are otherwise create-only (pin / discovery); this is the per-app
  // editor the Hosts tab deferred to Apps. Routes through the per-chip
  // PATCH / DELETE endpoints (validated persist + audit). For a
  // catalog-linked chip, clearing name / icon re-inherits from the
  // template.
  // ----------------------------------------------------------------
  appsInstanceEditOpen: false,
  appsInstanceEditSaving: false,
  appsInstanceEditError: '',
  // Open-time snapshot signature for dirty tracking — see
  // `appsInstanceDirty()` / `_appsInstanceFormSig()`.
  _appsInstanceEditSnapshot: '',
  appsInstanceEditForm: {
    host_id: '', service_idx: -1, host_label: '',
    catalog_name: '', catalog_slug: '',
    name: '', url: '', icon: '', probe_enabled: true, probe_type: 'tcp',
    ports: [], docker_stack: '', docker_container: '', docker_host: '',
    // Per-instance show_extras tri-state (null = inherit, true /
    // false = explicit override) — consumed by every per-app
    // extras partial under `static/_partials/_components/apps/`
    // via the shared `appsShowExtras(app, inst)` resolver.
    show_extras: null,
    // Per-instance api_key — only consumed by templates that
    // declare it (currently Speedtest Tracker). Empty string =
    // unset (clears any stored value on save). Backend stamps
    // `api_key_set` on the instance row so the SPA can render
    // the "Saved" indicator without round-tripping the secret.
    api_key: '',
    api_key_set: false,
    // Seerr TMDB config (for the AI "suggest a movie" skill). tmdb_api_key
    // is a secret (keep-current-if-blank, never returned in the clear —
    // only the `tmdb_api_key_set` flag); the two base URLs round-trip in
    // the clear with public defaults applied server-side when blank.
    tmdb_api_key: '',
    tmdb_api_key_set: false,
    tmdb_base_url: '',
    tmdb_image_base_url: '',
  },

  openInstanceEdit(inst) {
    if (!inst) {
      return;
    }
    // Seed the per-port rows from the chip's probe.ports[] (inherited
    // from the catalog template at pin time, e.g. AdGuard's 3 ports),
    // normalising each entry so the inputs bind cleanly.
    const ports = (Array.isArray(inst.ports) ? inst.ports : []).map((p) => ({
      port: (p && p.port != null) ? p.port : '',
      protocol: (p && p.protocol) || 'tcp',
      label: (p && p.label) || '',
      probe_path: (p && p.probe_path) || '',
      probe_status: (p && p.probe_status != null) ? p.probe_status : 0,
      open_url: !!(p && p.open_url),
      // Stable per-row key so the x-for keys on identity, NOT the array
      // index — index keys made Alpine reuse port-row DOM nodes by
      // position, so deleting a row appeared to do nothing / removed the
      // wrong one. Stripped on save.
      _uid: this._mintInstancePortUid(),
    }));
    this.appsInstanceEditForm = {
      host_id: inst.host_id,
      service_idx: inst.service_idx,
      host_label: inst.host_address || inst.host_id,
      catalog_name: inst.catalog_name || '',
      catalog_slug: inst.catalog_slug || '',
      name: inst.name || '',
      url: inst.url || '',
      icon: inst.icon || '',
      probe_enabled: inst.probe_enabled !== false,
      probe_type: inst.probe_type || 'tcp',
      ports: ports,
      docker_stack: inst.docker_stack || '',
      docker_container: inst.docker_container || '',
      docker_host: inst.docker_host || '',
      // Per-instance opt-in to render the per-app extras panel
      // (each per-app SPA module under `static/js/apps/<slug>.js`
      // owns its own panel template + helpers). Tri-state:
      // null = inherit from template's `show_extras` (default
      // true), true = always show, false = always hide regardless
      // of template. The SPA's `appsShowExtras(app, inst)` helper
      // resolves the chain before the per-app `<template x-if>`
      // gates fire; null / true → render, false → hide.
      show_extras: (inst.show_extras !== undefined && inst.show_extras !== null)
        ? !!inst.show_extras
        : null,
      // Per-instance api_key — never returned in the clear from
      // the backend (only `api_key_set: bool` flag). Editor starts
      // blank; a non-empty value on save overwrites, empty +
      // unchanged preserves the stored secret (keep-current
      // pattern from the global-secrets convention).
      api_key: '',
      api_key_set: !!inst.api_key_set,
      // Non-secret Basic-auth username half (e.g. AdGuard Home). Unlike
      // api_key, the backend returns this in the clear, so it seeds the
      // input directly and round-trips on save.
      username: inst.username || '',
      // Per-instance averages window (Speedtest "Avg of last N tests").
      // Returned in the clear; seeds the number input as a string. Blank
      // = use the app default (10); backend clamps 2..60 + drops blanks.
      avg_window: (inst.avg_window != null && inst.avg_window !== '')
        ? String(inst.avg_window) : '',
      // Per-instance data-cache TTL (seconds). Blank = the app's default
      // (AdGuard / Pi-hole 30, Speedtest 60); backend clamps 5..3600.
      cache_ttl: (inst.cache_ttl != null && inst.cache_ttl !== '')
        ? String(inst.cache_ttl) : '',
      // Seerr TMDB config. tmdb_api_key starts blank (secret, keep-current);
      // the base URLs seed from the backend (returned in the clear).
      tmdb_api_key: '',
      tmdb_api_key_set: !!inst.tmdb_api_key_set,
      tmdb_base_url: inst.tmdb_base_url || '',
      tmdb_image_base_url: inst.tmdb_image_base_url || '',
    };
    this.appsInstanceEditError = '';
    // Clear any Test-connection result from a PREVIOUSLY-edited instance —
    // the shared og-test-connection box renders whenever appsInstanceTestResult
    // is truthy, so a stale result would otherwise carry over into the newly
    // opened instance's editor until the operator re-tests.
    this.appsInstanceTestResult = null;
    this.appsInstanceTestBusy = false;
    // Seed the Link-to-Docker combobox input with the current link's label
    // (so it shows what's linked) + start with the dropdown closed.
    this.appsDockerLinkSearch = this.appsInstanceDockerLinkLabel();
    this.appsDockerLinkDropdownOpen = false;
    // Snapshot the form at open so `appsInstanceDirty()` can gate the
    // Save button — every checkbox / textbox / dropdown change toggles
    // dirty, and reverting a field back un-dirties (operator-flagged:
    // Save didn't react to field edits). Captured AFTER the form is
    // fully built above.
    this._appsInstanceEditSnapshot = this._appsInstanceFormSig();
    this.appsInstanceEditOpen = true;
  },

  // Stable signature of the instance-edit form for dirty tracking. Folds
  // every operator-editable field — incl. the ports array + the api_key
  // input — into one JSON string. The api_key starts blank and any typed
  // value makes the form dirty (a fresh secret is always a change).
  _appsInstanceFormSig() {
    const f = this.appsInstanceEditForm || {};
    const ports = Array.isArray(f.ports) ? f.ports.map((p) => ({
      port: p.port, protocol: p.protocol, label: p.label,
      probe_path: p.probe_path, probe_status: p.probe_status, open_url: p.open_url,
    })) : [];
    return JSON.stringify({
      name: f.name || '', url: f.url || '', icon: f.icon || '',
      probe_enabled: f.probe_enabled !== false,
      probe_type: f.probe_type || 'tcp',
      docker_stack: f.docker_stack || '', docker_container: f.docker_container || '',
      docker_host: f.docker_host || '',
      show_extras: (f.show_extras === true || f.show_extras === false) ? f.show_extras : null,
      api_key: (typeof f.api_key === 'string') ? f.api_key : '',
      username: f.username || '',
      avg_window: f.avg_window || '',
      cache_ttl: f.cache_ttl || '',
      tmdb_api_key: (typeof f.tmdb_api_key === 'string') ? f.tmdb_api_key : '',
      tmdb_base_url: f.tmdb_base_url || '',
      tmdb_image_base_url: f.tmdb_image_base_url || '',
      ports,
    });
  },

  // True when the instance-edit form differs from its open-time snapshot
  // — gates the Save button so it only enables on a real change.
  appsInstanceDirty() {
    if (!this.appsInstanceEditOpen) {
      return false;
    }
    return this._appsInstanceFormSig() !== (this._appsInstanceEditSnapshot || '');
  },

  // Datalist candidates for the "Link to Docker" picker, sourced from
  // the already-loaded /api/items snapshot (this.items). Stacks =
  // service names + stack namespaces; containers = standalone /orphan
  // container names. Free-text inputs back these so the operator can
  // also type an id the snapshot doesn't list.
  // Docker-link picker options — one grouped list from the live
  // /api/items snapshot. CONTAINERS (host-specific = the precise
  // restart/update target) come first WITH their host; SERVICES (span
  // hosts) follow. The option value is the item id (svc:/ctn:) — a
  // transient picker key; selecting one derives the STABLE stored
  // fields (name + host for a container, name for a service) via
  // setAppsInstanceDockerLink, so a container recreate (new id) doesn't
  // break the link.
  appsDockerLinkOptions() {
    const containers = [];
    const services = [];
    for (const it of (this.items || [])) {
      if (!it || !it.id || !it.name) {
        continue;
      }
      if (it.type === 'container' || it.type === 'orphan') {
        // Only offer RUNNING containers as link targets. Exclude stopped /
        // exited / dead / removed (orphan task) + offline ("faulty")
        // containers — none of them are a useful restart / update target
        // and they only clutter the picker.
        if ((it.state || '').toLowerCase() !== 'running') {
          continue;
        }
        containers.push({id: it.id, label: it.name + (it.node ? ' · ' + it.node : ''), name: it.name, host: it.node || ''});
      } else if (it.type === 'service') {
        services.push({id: it.id, label: it.name + (it.stack ? ' · ' + it.stack : ''), name: it.name, host: ''});
      }
    }
    const byLabel = (a, b) => (a.label || '').toLowerCase().localeCompare((b.label || '').toLowerCase());
    containers.sort(byLabel);
    services.sort(byLabel);
    return {containers, services};
  },

  // Searchable combobox support for the Link-to-Docker picker. Filters the
  // grouped options by the typed text so a host with many containers /
  // services isn't a long unscrollable dropdown.
  appsDockerLinkFiltered() {
    const opts = this.appsDockerLinkOptions();
    const q = (this.appsDockerLinkSearch || '').trim().toLowerCase();
    if (!q) {
      return opts;
    }
    const f = (arr) => (arr || []).filter((o) => (o.label || '').toLowerCase().includes(q));
    return {containers: f(opts.containers), services: f(opts.services)};
  },

  // Display label of the CURRENTLY-linked item (seeds the combobox input
  // on open + restored after a pick) — '' when not linked.
  appsInstanceDockerLinkLabel() {
    const v = this.appsInstanceDockerLinkValue();
    if (!v) {
      return '';
    }
    const opts = this.appsDockerLinkOptions();
    const found = (opts.containers || []).concat(opts.services || []).find((o) => o.id === v);
    return found ? found.label : '';
  },

  // Apply a combobox pick: set the link + show the chosen label in the
  // input + close the dropdown.
  appsDockerLinkPick(itemId, label) {
    this.setAppsInstanceDockerLink(itemId);
    this.appsDockerLinkSearch = label || '';
    this.appsDockerLinkDropdownOpen = false;
  },

  // Close the picker + DISCARD any unmatched free text. The link is ONLY
  // ever set by clicking an option (appsDockerLinkPick) — typed text is a
  // filter, never a saved value — so on close we snap the input back to
  // the currently-linked item's label (or '' when not linked). This stops
  // the box from showing free text that looks like a selection but won't
  // persist; to actually change the link the user must pick an option (or
  // the "— Not linked —" row).
  appsDockerLinkClose() {
    this.appsDockerLinkDropdownOpen = false;
    this.appsDockerLinkSearch = this.appsInstanceDockerLinkLabel();
  },

  // Keyboard-nav support for the Link-to-Docker combobox — mirrors the
  // pin-host / discover-host comboboxes so all three share the same
  // contract (arrow move + Enter select + Escape close + aria-
  // activedescendant). The dropdown spans two groups (containers +
  // services) plus the "Not linked" row, so nav walks a FLATTENED list;
  // each entry carries a stable dom id for aria-activedescendant + the
  // per-row highlight.
  appsDockerLinkFlat() {
    const f = this.appsDockerLinkFiltered();
    const out = [{
      id: '',
      label: this.t('admin_apps.instance_edit_docker_none') || '— Not linked —',
      dom: 'apps-docker-link-opt-none',
    }];
    (f.containers || []).forEach((o, i) => out.push({id: o.id, label: o.label, dom: 'apps-docker-link-opt-c' + i}));
    (f.services || []).forEach((o, i) => out.push({id: o.id, label: o.label, dom: 'apps-docker-link-opt-s' + i}));
    return out;
  },

  appsDockerLinkActiveDom() {
    const flat = this.appsDockerLinkFlat();
    const i = this.appsDockerLinkActiveIdx;
    return (i >= 0 && i < flat.length) ? flat[i].dom : '';
  },

  appsDockerLinkFocusDom(dom) {
    const i = this.appsDockerLinkFlat().findIndex(x => x.dom === dom);
    if (i >= 0) {
      this.appsDockerLinkActiveIdx = i;
    }
  },

  appsDockerLinkMove(delta) {
    this.appsDockerLinkDropdownOpen = true;
    const n = this.appsDockerLinkFlat().length;
    if (!n) {
      this.appsDockerLinkActiveIdx = -1;
      return;
    }
    let idx = this.appsDockerLinkActiveIdx + delta;
    if (idx < 0) {
      idx = n - 1;
    }
    if (idx >= n) {
      idx = 0;
    }
    this.appsDockerLinkActiveIdx = idx;
  },

  appsDockerLinkEnter() {
    const flat = this.appsDockerLinkFlat();
    const i = this.appsDockerLinkActiveIdx;
    if (i < 0 || i >= flat.length) {
      return;
    }
    const it = flat[i];
    // The "Not linked" row clears the link (label '' so the input blanks);
    // every other row links to its item id + shows its label.
    this.appsDockerLinkPick(it.id, it.id ? it.label : '');
  },

  // Current <select> value for the edit form: the item id matching the
  // stored link (container name + host wins; falls back to name-only
  // across snapshots; else the service), or '' when unlinked / the
  // linked item isn't in the current snapshot.
  appsInstanceDockerLinkValue() {
    const f = this.appsInstanceEditForm || {};
    const items = Array.isArray(this.items) ? this.items : [];
    if (f.docker_container) {
      const c = items.find((it) => it && (it.type === 'container' || it.type === 'orphan')
        && it.name === f.docker_container && (!f.docker_host || (it.node || '') === f.docker_host));
      if (c) {
        return c.id;
      }
      const c2 = items.find((it) => it && (it.type === 'container' || it.type === 'orphan') && it.name === f.docker_container);
      if (c2) {
        return c2.id;
      }
    }
    if (f.docker_stack) {
      const s = items.find((it) => it && it.type === 'service'
        && (it.name === f.docker_stack || it.stack === f.docker_stack));
      if (s) {
        return s.id;
      }
    }
    return '';
  },

  // Apply a picker selection to the edit form's stable fields. A
  // container sets docker_container + docker_host (clears docker_stack);
  // a service sets docker_stack (clears docker_container + docker_host);
  // '' clears the link entirely.
  setAppsInstanceDockerLink(itemId) {
    const f = this.appsInstanceEditForm;
    if (!f) {
      return;
    }
    if (!itemId) {
      f.docker_container = '';
      f.docker_stack = '';
      f.docker_host = '';
      return;
    }
    const it = (this.items || []).find((x) => x && x.id === itemId);
    if (!it) {
      return;
    }
    if (it.type === 'container' || it.type === 'orphan') {
      f.docker_container = it.name || '';
      f.docker_host = it.node || '';
      f.docker_stack = '';
    } else if (it.type === 'service') {
      f.docker_stack = it.name || '';
      f.docker_container = '';
      f.docker_host = '';
    }
  },

  // One-line confirmation of what the current edit-form link resolves to
  // in the live snapshot — surfaces the live status so the operator sees
  // the mapping is real (uses the mapping "to do something"). Empty when
  // unlinked.
  appsInstanceDockerLinkSummary() {
    const f = this.appsInstanceEditForm || {};
    if (!f.docker_container && !f.docker_stack) {
      return '';
    }
    const target = this._appDrawerResolveItem(f);
    if (!target) {
      return this.t('admin_apps.instance_edit_docker_unresolved') || 'Linked target not in the current items snapshot';
    }
    const where = target.node ? (' · ' + target.node) : '';
    const status = target.status || target.health || '';
    return (this.t('admin_apps.instance_edit_docker_linked') || 'Linked')
      + ' → ' + (target.name || '') + where + (status ? ' (' + status + ')' : '');
  },

  // Stable id for an instance-editor port row — used as the x-for :key so
  // splicing a row out doesn't make Alpine reuse DOM nodes by index.
  // lgtm[js/insecure-randomness]  — Same suppression contract as
  // `_newId` above. This id is a UI-only Alpine `:key` for per-port
  // row stable identity (NOT a security secret). The fallback
  // chain is crypto-only by design; the lgtm marker handles
  // CodeQL stale-cache where a prior revision used a PRNG source.
  _mintInstancePortUid() {
    // Primary path — crypto.randomUUID() (universally supported in
    // modern browsers). Returns a 36-char UUID prefixed with `pp_`.
    // The id is a UI-only Alpine x-for key (per-port row stable
    // identity); not a security secret. Crypto-strength source
    // keeps the CodeQL `js/insecure-randomness` audit clean and
    // matches the same shape used by `_newId` above.
    try {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return 'pp_' + window.crypto.randomUUID();
      }
      // Secondary path — crypto.getRandomValues() for older
      // browsers without the .randomUUID shortcut.
      if (window.crypto && typeof window.crypto.getRandomValues === 'function') {
        const buf = new Uint8Array(8);
        window.crypto.getRandomValues(buf);
        let hex = '';
        for (let i = 0; i < buf.length; i++) {
          hex += buf[i].toString(16).padStart(2, '0');
        }
        return 'pp_' + Date.now().toString(36) + '-' + hex;
      }
    } catch (_e) { /* fall through to the deterministic counter */
    }
    // Tertiary fallback — Web Crypto truly unavailable. Use a
    // monotonically-increasing counter scoped to the
    // component instance, NOT a PRNG. The id needs collision-
    // resistance + uniqueness within the session, not entropy.
    this._instancePortUidCounter = (this._instancePortUidCounter || 0) + 1;
    return 'pp_' + Date.now().toString(36) + '-c' + this._instancePortUidCounter.toString(36);
  },

  addInstancePort() {
    if (!Array.isArray(this.appsInstanceEditForm.ports)) {
      this.appsInstanceEditForm.ports = [];
    }
    this.appsInstanceEditForm.ports.push({
      port: '', protocol: 'tcp', label: '', probe_path: '', probe_status: 0,
      open_url: false, _uid: this._mintInstancePortUid(),
    });
  },

  removeInstancePort(i) {
    if (Array.isArray(this.appsInstanceEditForm.ports)) {
      this.appsInstanceEditForm.ports.splice(i, 1);
    }
  },

  closeInstanceEdit() {
    this.appsInstanceEditOpen = false;
    // Drop the Test-connection result so reopening any instance starts clean.
    this.appsInstanceTestResult = null;
    this.appsInstanceTestBusy = false;
  },

  async saveInstanceEdit() {
    const f = this.appsInstanceEditForm;
    if (!f.host_id || f.service_idx < 0) {
      return;
    }
    this.appsInstanceEditSaving = true;
    this.appsInstanceEditError = '';
    try {
      const r = await fetch('/api/services/' + encodeURIComponent(f.host_id)
        + '/' + encodeURIComponent(f.service_idx), {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          name: f.name, url: f.url, icon: f.icon,
          probe_enabled: f.probe_enabled, probe_type: f.probe_type,
          // Strip the editor-only `_uid` (x-for key) from each port row.
          ports: Array.isArray(f.ports) ? f.ports.map((p) => ({
            port: p.port, protocol: p.protocol, label: p.label,
            probe_path: p.probe_path, probe_status: p.probe_status, open_url: p.open_url,
          })) : [],
          docker_stack: f.docker_stack || '', docker_container: f.docker_container || '',
          docker_host: f.docker_host || '',
          // Per-instance `show_extras` tri-state — true / false /
          // null (= inherit template default). The backend
          // `services[].show_extras` field is the source of truth;
          // the SPA editor's checkbox `:indeterminate` reflects
          // null on load + becomes true / false on click. Only
          // sent when the operator made an explicit choice (true
          // or false); null keeps the field absent so a template
          // default-flip propagates without a per-instance save.
          show_extras: (f.show_extras === true || f.show_extras === false)
            ? f.show_extras
            : null,
          // Per-instance api_key — keep-current-if-blank contract
          // shared with every other secret in OmniGrid (Beszel
          // password, Portainer token, etc.). Non-empty value
          // overwrites the stored secret; empty string + an
          // already-set flag keeps the existing value. Backend
          // returns `api_key_set: bool` so the SPA can render the
          // "Saved" indicator without round-tripping the secret.
          api_key: (typeof f.api_key === 'string') ? f.api_key : '',
          // Non-secret Basic-auth username half — surfaced ONLY by the
          // apps whose editor partial declares it (e.g. AdGuard Home);
          // empty for single-secret apps (Speedtest), so the backend
          // simply drops it. Each app owns its own auth fields.
          username: (typeof f.username === 'string') ? f.username : '',
          // Per-instance averages window (Speedtest). Blank => backend
          // drops it => app default (10); a value is clamped 2..60.
          avg_window: (f.avg_window != null) ? f.avg_window : '',
          // Per-instance data-cache TTL (seconds). Blank => backend drops
          // it => the app module's default; a value is clamped 5..3600.
          cache_ttl: (f.cache_ttl != null) ? f.cache_ttl : '',
          // Seerr TMDB config. tmdb_api_key is a secret (keep-current-if-
          // blank); the two base URLs round-trip in the clear (blank =>
          // backend clears => app falls back to the public TMDB defaults).
          tmdb_api_key: (typeof f.tmdb_api_key === 'string') ? f.tmdb_api_key : '',
          tmdb_base_url: (typeof f.tmdb_base_url === 'string') ? f.tmdb_base_url : '',
          tmdb_image_base_url: (typeof f.tmdb_image_base_url === 'string') ? f.tmdb_image_base_url : '',
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      this.appsInstanceEditOpen = false;
      await this.loadAppsInstances();
      // Await the Apps-view refresh + re-sync an open drawer so the edit
      // (URL / ports / icon / link) reflects in the top-level Apps view
      // and the App drawer, not just the admin instances table.
      if (typeof this.loadAppsList === 'function') {
        await this.loadAppsList(true);
        this._resyncDrawerApp();
        this._resyncDrawerAppHost();
      }
    } catch (err) {
      this.appsInstanceEditError = (err && err.message) ? err.message : String(err);
    } finally {
      this.appsInstanceEditSaving = false;
    }
  },

  async deleteInstance(inst) {
    if (!inst || !inst.host_id) {
      return;
    }
    const label = inst.name || inst.catalog_name
      || (inst.catalog && inst.catalog.name) || ('service ' + inst.service_idx);
    const hostName = inst.host_label || inst.host_id || '';
    const confirmed = typeof this.confirmDialog === 'function'
      ? await this.confirmDialog({
        // Name the app + host prominently in the title (was a generic
        // "Remove app instance?" with the name only tucked into the body).
        title: this.t('admin_apps.instance_delete_confirm_title', {name: label, host: hostName})
          || ('Remove ' + label + ' from ' + hostName + '?'),
        text: this.t('admin_apps.instance_delete_confirm_text')
          || 'This unpins the chip from the host. The catalog template is unaffected.',
        icon: 'warning',
        confirmButtonText: this.t('actions.delete') || 'Delete',
      })
      : window.confirm('Remove "' + label + '" from ' + hostName + '?');
    if (!confirmed) {
      return;
    }
    try {
      const r = await fetch('/api/services/' + encodeURIComponent(inst.host_id)
        + '/' + encodeURIComponent(inst.service_idx), {method: 'DELETE'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      await this.loadAppsInstances();
      if (typeof this.loadAppsList === 'function') {
        await this.loadAppsList(true);
        this._resyncDrawerApp();
        this._resyncDrawerAppHost();
      }
      if (typeof this.toast === 'function') {
        this.toast(this.t('admin_apps.instance_deleted') || 'App instance removed', 'success');
      }
    } catch (err) {
      if (typeof this.toast === 'function') {
        this.toast((this.t('admin_apps.instance_delete_failed') || 'Remove failed: ') + err.message, 'error');
      }
    }
  },

  // ----------------------------------------------------------------
  // Instances bulk-delete — multi-select rows in the Admin → Apps →
  // Instances table, delete every selected chip in one action. Selection
  // is keyed by `host_id:service_idx`; reassigning appsInstancesSelected
  // (rather than mutating in place) guarantees Alpine re-evaluates the
  // row checkboxes + bulk bar.
  // ----------------------------------------------------------------
  instanceSelKey(inst) {
    return inst ? ((inst.host_id || '') + ':' + inst.service_idx) : '';
  },

  isInstanceSelected(inst) {
    return !!this.appsInstancesSelected[this.instanceSelKey(inst)];
  },

  toggleInstanceSelected(inst) {
    const k = this.instanceSelKey(inst);
    if (!k) {
      return;
    }
    const next = Object.assign({}, this.appsInstancesSelected);
    if (next[k]) {
      delete next[k];
    } else {
      next[k] = true;
    }
    this.appsInstancesSelected = next;
  },

  appsInstancesSelectedCount() {
    const s = this.appsInstancesSelected || {};
    return Object.keys(s).filter((k) => s[k]).length;
  },

  appsInstancesAllSelected() {
    const list = this.appsInstances || [];
    if (!list.length) {
      return false;
    }
    return list.every((i) => !!this.appsInstancesSelected[this.instanceSelKey(i)]);
  },

  toggleSelectAllInstances() {
    if (this.appsInstancesAllSelected()) {
      this.appsInstancesSelected = {};
      return;
    }
    const next = {};
    for (const i of (this.appsInstances || [])) {
      next[this.instanceSelKey(i)] = true;
    }
    this.appsInstancesSelected = next;
  },

  clearInstanceSelection() {
    this.appsInstancesSelected = {};
  },

  async bulkDeleteInstances() {
    const sel = (this.appsInstances || []).filter(
      (i) => i && this.appsInstancesSelected[this.instanceSelKey(i)]);
    const n = sel.length;
    if (!n) {
      return;
    }
    const confirmed = typeof this.confirmDialog === 'function'
      ? await this.confirmDialog({
        title: this.t('admin_apps.bulk_delete_confirm_title', {n})
          || ('Remove ' + n + ' app instance' + (n === 1 ? '' : 's') + '?'),
        text: this.t('admin_apps.bulk_delete_confirm_text')
          || 'This unpins the selected chips from their hosts. Catalog templates are unaffected.',
        icon: 'warning',
        confirmButtonText: this.t('actions.delete') || 'Delete',
      })
      : window.confirm('Remove ' + n + ' app instances?');
    if (!confirmed) {
      return;
    }
    this.appsInstancesBulkDeleting = true;
    // The DELETE endpoint removes from each host's services[] BY INDEX,
    // so deleting must go DESCENDING by service_idx per host — removing a
    // higher index first keeps every lower index valid. Deleting low-first
    // would shift the higher rows down and mis-target the next delete.
    const byHost = {};
    for (const inst of sel) {
      const hid = inst.host_id || '';
      (byHost[hid] = byHost[hid] || []).push(inst);
    }
    let ok = 0;
    let failed = 0;
    for (const hid of Object.keys(byHost)) {
      const rows = byHost[hid].slice().sort((a, b) => b.service_idx - a.service_idx);
      for (const inst of rows) {
        try {
          const r = await fetch('/api/services/' + encodeURIComponent(inst.host_id)
            + '/' + encodeURIComponent(inst.service_idx), {method: 'DELETE'});
          if (!r.ok) {
            const j = await r.json().catch(() => ({}));
            throw new Error(this.fmtApiError(j, r.status));
          }
          ok += 1;
        } catch (_e) {
          failed += 1;
        }
      }
    }
    this.appsInstancesSelected = {};
    this.appsInstancesBulkDeleting = false;
    await this.loadAppsInstances();
    if (typeof this.loadAppsList === 'function') {
      await this.loadAppsList(true);
      this._resyncDrawerApp();
      this._resyncDrawerAppHost();
    }
    if (typeof this.toast === 'function') {
      if (failed) {
        const msg = this.t('admin_apps.bulk_delete_partial', {ok, failed})
          || (ok + ' removed, ' + failed + ' failed');
        this.toast(msg, ok ? 'warning' : 'error');
      } else {
        this.toast(this.t('admin_apps.bulk_deleted', {n: ok})
          || (ok + ' app instances removed'), 'success');
      }
    }
  },

  // ----------------------------------------------------------------
  // Pin-to-host modal — operator picks a curated host from the existing
  // hostsConfig list + optional URL override + probe-enable flag; the
  // POST to /api/services/catalog/{cid}/pin creates the chip server-
  // side via the same _clean_host_services validator that Admin →
  // Hosts uses. After save the Instances tab refresh picks up the new
  // chip automatically.
  // ----------------------------------------------------------------
  openAppCatalogPin(entry) {
    if (!entry || !entry.id) {
      return;
    }
    this.appsPinForm = {
      template: entry,
      host_ids: [],
      url: '',
      probe_enabled: true,
    };
    this.appsPinError = '';
    this.appsPinHostSearch = '';
    this.appsPinHostDropdownOpen = false;
    this.appsPinHostActiveIdx = -1;
    this.appsPinModalOpen = true;
    // The host picker reads from `hostsConfig`. The Admin → Apps tab
    // loader doesn't fetch it (only the Hosts tab does), so the picker
    // is empty if the operator hasn't visited Hosts in this session.
    // Lazy-load on first open.
    if (!Array.isArray(this.hostsConfig) || !this.hostsConfig.length) {
      if (typeof this.loadHostsConfig === 'function') {
        this.loadHostsConfig().catch(() => undefined);
      }
    }
  },

  closeAppCatalogPin() {
    this.appsPinModalOpen = false;
    this.appsPinForm = {template: null, host_ids: [], url: '', probe_enabled: true};
    this.appsPinError = '';
    this.appsPinHostSearch = '';
    this.appsPinHostDropdownOpen = false;
    this.appsPinHostActiveIdx = -1;
  },

  // ---- Pin-to-host searchable picker -----------------------------
  // Mirrors the discovery wizard's host picker (appsDiscoverFilteredHosts
  // / appsDiscoverHostMove / etc.) but writes the selection to
  // appsPinForm.host_id. Reuses the shared appsHostLabel(h) formatter so
  // the input reads identically to the old <option> text. Kept as a
  // separate set of helpers (not shared with the discovery picker)
  // because selecting a host here does NOT trigger a side-effect probe —
  // it only fills the form field.
  appsPinFilteredHosts() {
    const all = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
    const q = (this.appsPinHostSearch || '').trim().toLowerCase();
    let out = all;
    if (q) {
      out = all.filter((h) => {
        if (!h) {
          return false;
        }
        const hay = ((h.label || '') + ' ' + (h.id || '') + ' ' + (h.address || '')).toLowerCase();
        return hay.includes(q);
      });
    }
    return out.slice(0, 50);
  },

  // Multi-host pin: clicking a host TOGGLES it into the selected set
  // (host_ids) and keeps the picker open + clears the filter so the
  // operator can keep adding more. Selected hosts render as removable
  // chips above the input.
  toggleAppsPinHost(h) {
    if (!h || !h.id) {
      return;
    }
    if (!this.appsPinForm || !Array.isArray(this.appsPinForm.host_ids)) {
      this.appsPinForm.host_ids = [];
    }
    const i = this.appsPinForm.host_ids.indexOf(h.id);
    if (i >= 0) {
      this.appsPinForm.host_ids.splice(i, 1);
    } else {
      this.appsPinForm.host_ids.push(h.id);
    }
    this.appsPinHostSearch = '';
    this.appsPinHostActiveIdx = -1;
  },

  appsPinHostChosen(h) {
    return !!(h && this.appsPinForm
      && Array.isArray(this.appsPinForm.host_ids)
      && this.appsPinForm.host_ids.includes(h.id));
  },

  removeAppsPinHost(id) {
    if (!this.appsPinForm || !Array.isArray(this.appsPinForm.host_ids)) {
      return;
    }
    const i = this.appsPinForm.host_ids.indexOf(id);
    if (i >= 0) {
      this.appsPinForm.host_ids.splice(i, 1);
    }
  },

  // Selected host rows (objects) for the removable chip strip.
  appsPinSelectedHosts() {
    const ids = (this.appsPinForm && Array.isArray(this.appsPinForm.host_ids))
      ? this.appsPinForm.host_ids : [];
    const cfg = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
    return ids.map((id) => cfg.find((h) => h && h.id === id) || {id});
  },

  appsPinHostMove(delta) {
    this.appsPinHostDropdownOpen = true;
    const n = this.appsPinFilteredHosts().length;
    if (!n) {
      this.appsPinHostActiveIdx = -1;
      return;
    }
    let idx = this.appsPinHostActiveIdx + delta;
    if (idx < 0) {
      idx = n - 1;
    }
    if (idx >= n) {
      idx = 0;
    }
    this.appsPinHostActiveIdx = idx;
  },

  appsPinHostEnter() {
    const list = this.appsPinFilteredHosts();
    if (this.appsPinHostActiveIdx >= 0 && this.appsPinHostActiveIdx < list.length) {
      this.toggleAppsPinHost(list[this.appsPinHostActiveIdx]);
    } else if (list.length === 1) {
      this.toggleAppsPinHost(list[0]);
    }
  },

  async submitAppCatalogPin() {
    if (this.appsPinSaving) {
      return;
    }
    const form = this.appsPinForm || {};
    const tpl = form.template;
    if (!tpl || !tpl.id) {
      this.appsPinError = this.t('admin_apps.pin_no_template') || 'No template selected';
      return;
    }
    const hostIds = Array.isArray(form.host_ids) ? form.host_ids.slice() : [];
    if (!hostIds.length) {
      this.appsPinError = this.t('admin_apps.pin_pick_host') || 'Pick at least one host';
      return;
    }
    this.appsPinSaving = true;
    this.appsPinError = '';
    // Multi-host: pin to each selected host in turn. 409 = already pinned
    // (the backend's duplicate guard) — counted separately, not a failure.
    let pinned = 0;
    let already = 0;
    let failed = 0;
    const failMsgs = [];
    try {
      for (const hid of hostIds) {
        try {
          const r = await fetch(`/api/services/catalog/${tpl.id}/pin`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              host_id: hid,
              url: (form.url || '').trim(),
              probe_enabled: !!form.probe_enabled,
            }),
          });
          if (r.status === 409) {
            already += 1;
            continue;
          }
          if (!r.ok) {
            const j = await r.json().catch(() => ({}));
            failed += 1;
            failMsgs.push(hid + ': ' + (j.detail || ('HTTP ' + r.status)));
            continue;
          }
          pinned += 1;
        } catch (e) {
          failed += 1;
          failMsgs.push(hid + ': ' + ((e && e.message) ? e.message : e));
        }
      }
      // Refresh the instance list so the new chips appear.
      await this.loadAppsInstances();
      if (typeof this.toast === 'function') {
        const parts = [];
        if (pinned) {
          parts.push(this.t('admin_apps.pin_result_pinned', {n: pinned}) || (pinned + ' pinned'));
        }
        if (already) {
          parts.push(this.t('admin_apps.pin_result_already', {n: already}) || (already + ' already pinned'));
        }
        if (failed) {
          parts.push(this.t('admin_apps.pin_result_failed', {n: failed}) || (failed + ' failed'));
        }
        const pinMsg = (this.t('admin_apps.pin_success_multi', {name: tpl.name || ''})
          || ('Pinned ' + (tpl.name || '') + ': ')) + parts.join(', ');
        this.toast(pinMsg, failed ? 'warning' : 'success');
      }
      if (failed) {
        // Keep the modal open so failures stay visible.
        this.appsPinError = failMsgs.slice(0, 5).join('; ');
      } else {
        this.closeAppCatalogPin();
      }
    } finally {
      this.appsPinSaving = false;
    }
  },

  // ----------------------------------------------------------------
  // Discovery wizard — port-scan + catalog match → bulk-bind.
  // ----------------------------------------------------------------
  // Jump from the top-level Apps view's empty-state CTA into the
  // discovery wizard. The wizard markup lives in the Admin → Apps
  // partial (hidden via x-show on the admin page-content), so we have
  // to navigate to that view + tab first, then open the modal on the
  // next tick once the partial is visible.
  openDiscoveryFromAppsView() {
    this.view = 'admin';
    this.adminTab = 'apps';
    if (typeof this.setAppsAdminTab === 'function') {
      this.setAppsAdminTab('templates');
    }
    this.$nextTick(() => this.openAppsDiscoverWizard());
  },

  openAppsDiscoverWizard() {
    this.appsDiscoverOpen = true;
    this.appsDiscoverForm = {host_id: ''};
    this.appsDiscoverResult = {detected_ports: [], proposals: [], scanned_at: 0};
    this.appsDiscoverError = '';
    this.appsDiscoverApplyError = '';
    this.appsDiscoverSelected = new Set();
    this.appsDiscoverHostSearch = '';
    this.appsDiscoverHostDropdownOpen = false;
    this.appsDiscoverHostActiveIdx = -1;
    // Host picker reads from hostsConfig — lazy-load if the operator
    // hasn't visited Admin → Hosts in this session.
    if (!Array.isArray(this.hostsConfig) || !this.hostsConfig.length) {
      if (typeof this.loadHostsConfig === 'function') {
        this.loadHostsConfig().catch(() => undefined);
      }
    }
  },

  closeAppsDiscoverWizard() {
    this.appsDiscoverOpen = false;
    this.appsDiscoverForm = {host_id: ''};
    this.appsDiscoverResult = {detected_ports: [], proposals: [], scanned_at: 0};
    this.appsDiscoverError = '';
    this.appsDiscoverApplyError = '';
    this.appsDiscoverSelected = new Set();
    this.appsDiscoverHostSearch = '';
    this.appsDiscoverHostDropdownOpen = false;
    this.appsDiscoverHostActiveIdx = -1;
  },

  // Display label for a curated host row in the discovery host picker —
  // canonical hostname/IP first, then the operator label when it differs.
  // Mirrors the old <option> text so the searchable input reads identically.
  appsHostLabel(h) {
    if (!h) {
      return '';
    }
    const base = (h.address || h.id || '').trim();
    const label = (h.label || '').trim();
    return base + (label && label !== base ? ' — ' + label : '');
  },

  // Filtered + capped host list for the searchable picker. Matches the
  // query (case-insensitive substring) against label + id + address so
  // the operator can type any identifier they remember. Empty query
  // returns the whole list (capped) so focusing the field shows options.
  appsDiscoverFilteredHosts() {
    const all = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
    const q = (this.appsDiscoverHostSearch || '').trim().toLowerCase();
    let out = all;
    if (q) {
      out = all.filter((h) => {
        if (!h) {
          return false;
        }
        const hay = ((h.label || '') + ' ' + (h.id || '') + ' ' + (h.address || '')).toLowerCase();
        return hay.includes(q);
      });
    }
    // Cap the rendered list so a huge fleet doesn't paint hundreds of
    // <li> nodes; the operator narrows with the query rather than scroll.
    return out.slice(0, 50);
  },

  // Commit a host selection from the dropdown (click or keyboard Enter).
  selectAppsDiscoverHost(h) {
    if (!h || !h.id) {
      return;
    }
    this.appsDiscoverForm.host_id = h.id;
    this.appsDiscoverHostSearch = this.appsHostLabel(h);
    this.appsDiscoverHostDropdownOpen = false;
    this.appsDiscoverHostActiveIdx = -1;
    this.runAppsDiscovery();
  },

  // Arrow-key navigation through the filtered match list. Opens the
  // dropdown on first keypress and clamps the highlight index in range.
  appsDiscoverHostMove(delta) {
    this.appsDiscoverHostDropdownOpen = true;
    const n = this.appsDiscoverFilteredHosts().length;
    if (!n) {
      this.appsDiscoverHostActiveIdx = -1;
      return;
    }
    let idx = this.appsDiscoverHostActiveIdx + delta;
    if (idx < 0) {
      idx = n - 1;
    }
    if (idx >= n) {
      idx = 0;
    }
    this.appsDiscoverHostActiveIdx = idx;
  },

  // Enter key: select the highlighted match, or — when nothing is
  // highlighted but the filter narrows to exactly one host — that host.
  appsDiscoverHostEnter() {
    const list = this.appsDiscoverFilteredHosts();
    if (this.appsDiscoverHostActiveIdx >= 0 && this.appsDiscoverHostActiveIdx < list.length) {
      this.selectAppsDiscoverHost(list[this.appsDiscoverHostActiveIdx]);
    } else if (list.length === 1) {
      this.selectAppsDiscoverHost(list[0]);
    }
  },

  async runAppsDiscovery() {
    const hostId = (this.appsDiscoverForm && this.appsDiscoverForm.host_id) || '';
    if (!hostId) {
      this.appsDiscoverResult = {detected_ports: [], proposals: [], scanned_at: 0};
      return;
    }
    this.appsDiscoverLoading = true;
    this.appsDiscoverError = '';
    this.appsDiscoverResult = {detected_ports: [], proposals: [], scanned_at: 0};
    this.appsDiscoverSelected = new Set();
    try {
      const r = await fetch(`/api/services/discover/${encodeURIComponent(hostId)}`, {
        method: 'POST',
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      const j = await r.json();
      this.appsDiscoverResult = j;
      // Count how many proposals match each detected port. A port matched
      // by 2+ templates is AMBIGUOUS (e.g. several apps share 80 / 443) —
      // auto-checking them would pin multiple apps competing for the same
      // port, so leave them UNCHECKED — the shared port's owner must be
      // picked by hand rather than auto-bound to multiple apps.
      const portMatchCount = {};
      for (const prop of (j.proposals || [])) {
        for (const port of (prop.matched_ports || [])) {
          portMatchCount[port] = (portMatchCount[port] || 0) + 1;
        }
      }
      // Pre-select proposals with confidence >= 0.9 (all template ports
      // detected + name match OR multi-port exact match) — the safe
      // bulk-bind candidates — EXCEPT those whose matched ports collide
      // with another proposal.
      const preSelected = new Set();
      for (const prop of (j.proposals || [])) {
        if (!prop || prop.confidence < 0.9 || !prop.catalog || !prop.catalog.id) {
          continue;
        }
        const ambiguous = (prop.matched_ports || []).some(
          (port) => (portMatchCount[port] || 0) >= 2);
        if (!ambiguous) {
          preSelected.add(prop.catalog.id);
        }
      }
      this.appsDiscoverSelected = preSelected;
    } catch (err) {
      this.appsDiscoverError = err && err.message ? err.message : String(err);
    } finally {
      this.appsDiscoverLoading = false;
    }
  },

  toggleAppsDiscoverSelection(catalogId) {
    if (!catalogId) {
      return;
    }
    // Alpine 3 reacts to Set assignment, not mutation. Clone, mutate,
    // reassign so :checked bindings re-evaluate on toggle.
    const next = new Set(this.appsDiscoverSelected || []);
    if (next.has(catalogId)) {
      next.delete(catalogId);
    } else {
      next.add(catalogId);
    }
    this.appsDiscoverSelected = next;
  },

  toggleAllAppsDiscoverSelections(checked) {
    if (!this.appsDiscoverResult) {
      return;
    }
    if (checked) {
      const next = new Set();
      for (const prop of (this.appsDiscoverResult.proposals || [])) {
        if (prop && prop.catalog && prop.catalog.id) {
          next.add(prop.catalog.id);
        }
      }
      this.appsDiscoverSelected = next;
    } else {
      this.appsDiscoverSelected = new Set();
    }
  },

  async submitAppsDiscoverApply() {
    if (this.appsDiscoverApplying) {
      return;
    }
    const hostId = (this.appsDiscoverForm && this.appsDiscoverForm.host_id) || '';
    if (!hostId) {
      this.appsDiscoverApplyError = this.t('admin_apps.pin_pick_host') || 'Pick a host';
      return;
    }
    const ids = Array.from(this.appsDiscoverSelected || []);
    if (!ids.length) {
      this.appsDiscoverApplyError = this.t('admin_apps.discover_select_at_least_one') || 'Select at least one proposal';
      return;
    }
    this.appsDiscoverApplying = true;
    this.appsDiscoverApplyError = '';
    try {
      const r = await fetch(`/api/services/discover/${encodeURIComponent(hostId)}/apply`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({catalog_ids: ids, probe_enabled: true}),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      const j = await r.json();
      await this.loadAppsInstances();
      if (typeof this.toast === 'function') {
        const nApplied = (j.applied || []).length;
        const nSkipped = (j.skipped || []).length;
        let msg = (this.t('admin_apps.discover_applied') || 'Pinned ') + nApplied;
        if (nSkipped) {
          msg += ' · ' + nSkipped + ' ' + (this.t('admin_apps.discover_skipped') || 'skipped');
        }
        this.toast(msg, 'success');
      }
      this.closeAppsDiscoverWizard();
    } catch (err) {
      this.appsDiscoverApplyError = err && err.message ? err.message : String(err);
    } finally {
      this.appsDiscoverApplying = false;
    }
  },

  async reseedAppCatalog() {
    if (this.appsCatalogReseeding) {
      return;
    }
    this.appsCatalogReseeding = true;
    this.appsCatalogStatus = '';
    try {
      const r = await fetch('/api/services/catalog/seed', {method: 'POST'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      const j = await r.json();
      await this.loadAppsCatalog();
      this.appsCatalogStatus = (this.t('admin_apps.reseed_done') || 'Re-seeded') + ': +' + (j.added || 0);
      if (typeof this.toast === 'function') {
        this.toast(this.appsCatalogStatus, 'success');
      }
    } catch (err) {
      this.appsCatalogStatus = err && err.message ? err.message : String(err);
    } finally {
      this.appsCatalogReseeding = false;
    }
  },

  // Export the whole catalog as a portable JSON pack (download). The
  // backend strips install-specific id/timestamps + keys on slug so the
  // pack re-imports cleanly on any install.
  async exportAppCatalog() {
    this.appsCatalogStatus = '';
    try {
      const r = await fetch('/api/services/catalog/export');
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      const pack = await r.json();
      const blob = new Blob([JSON.stringify(pack, null, 2)], {type: 'application/json'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'omnigrid-catalog-pack.json';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      this.appsCatalogStatus = (this.t('admin_apps.export_done') || 'Exported') + ': ' + (pack.count || 0);
    } catch (err) {
      this.appsCatalogStatus = err && err.message ? err.message : String(err);
      if (typeof this.toast === 'function') {
        this.toast(this.appsCatalogStatus, 'error');
      }
    }
  },

  // Import a catalog pack from a chosen JSON file. Accepts a full export
  // pack ({entries:[...]}) or a bare array; the backend upserts by slug.
  async importAppCatalog(ev) {
    const input = ev && ev.target;
    const file = input && input.files && input.files[0];
    if (!file) {
      return;
    }
    if (this.appsCatalogImporting) {
      return;
    }
    this.appsCatalogImporting = true;
    this.appsCatalogStatus = '';
    try {
      const text = await file.text();
      let parsed;
      try {
        parsed = JSON.parse(text);
      } catch (pe) {
        throw new Error((this.t('admin_apps.import_bad_json') || 'Not valid JSON') + ': ' + (pe && pe.message ? pe.message : pe));
      }
      // Accept either a full pack {entries:[...]} or a bare array.
      const body = Array.isArray(parsed) ? {entries: parsed} : {entries: (parsed && parsed.entries) || []};
      const r = await fetch('/api/services/catalog/import', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      const j = await r.json();
      await this.loadAppsCatalog();
      const errs = (j.errors || []).length;
      this.appsCatalogStatus = (this.t('admin_apps.import_done') || 'Imported')
        + ': +' + (j.created || 0) + ' / ~' + (j.updated || 0) + (errs ? (' / ' + errs + ' err') : '');
      if (typeof this.toast === 'function') {
        this.toast(this.appsCatalogStatus, errs ? 'warning' : 'success');
      }
    } catch (err) {
      this.appsCatalogStatus = err && err.message ? err.message : String(err);
      if (typeof this.toast === 'function') {
        this.toast(this.appsCatalogStatus, 'error');
      }
    } finally {
      this.appsCatalogImporting = false;
      // Reset the file input so re-selecting the same file re-fires change.
      if (input) {
        input.value = '';
      }
    }
  },
};
