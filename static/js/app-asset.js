// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ContinueStatementJS,BreakStatementJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML,JSAsyncFunctionMissingAwait
// noinspection JSMissingAwait,JSUnfilteredForInLoop,IfStatementWithoutBlockJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSPotentiallyInvalidUsageOfThis,JSIgnoredPromiseFromCall,JSDeprecatedSymbols,ControlFlowStatementWithoutBracesJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Asset-Inventory integration (Admin → Assets) — load/save/test
// the OAuth2 client_credentials-backed asset API, plus per-host
// lookups consumed by the Hosts drawer.


export default {
  // Asset inventory. `assetForm` is the editable form
  // state; `assetStatus` mirrors the server snapshot (secret `_set`
  // flag etc.). `assetCache` is the loaded /api/asset-inventory
  // payload — drives the preview block + drawer lookups.
  assetForm: {
    auth_mode: 'oauth2',
    base_url: '', token_url: '', client_id: '',
    client_secret: '', scope: '',
    lifetime_token: '',
    service: '', action: '',
    min_value: '', max_value: '',
    edit_url_template: '',
    verify_tls: true,
  },
  assetStatus: null,
  assetTestResult: null,
  assetCache: null,
  assetRefreshing: false,
  assetSaving: false,
  // Test-before-Save gate for Asset Inventory — same pattern as
  // Portainer / OIDC. When asset_inventory_enabled is ON, Save is
  // locked until a successful Test against the CURRENT form values.
  // Any edit after a passing Test mutates `_assetSnapshot()` away
  // from `_assetLastPassedTest`, re-locking Save.
  _assetLastPassedTest: '',

  // Asset Inventory dirty tracker — same shape as
  // `profileDirty()` and the host-stats / OIDC / Portainer /
  // Apprise / SSH dirty flags. Compares the editable `assetForm`
  // against the server-supplied `assetStatus` snapshot. Secret
  // fields (client_secret / lifetime_token) follow the standard
  // "_set" pattern: blank = keep, any non-empty value = dirty.
  assetDirty() {
    const f = this.assetForm || {};
    const s = this.assetStatus || {};
    const norm = v => (v == null ? '' : String(v));
    // Master switch — toggle change is dirty even when no
    // form field changed. Compared against the server-supplied
    // baseline captured into `assetStatus.enabled` by loadSettings.
    const enabledBaseline = (s.enabled !== false);
    if ((this.settings && (this.settings.asset_inventory_enabled !== false)) !== enabledBaseline) {
      return true;
    }
    // Status's auth_mode comes through as 'lifetime_token' or anything-else;
    // form normalises to 'oauth2' as the default fallback.
    const baseAuth = (s.auth_mode === 'lifetime_token') ? 'lifetime_token' : 'oauth2';
    if ((f.auth_mode || 'oauth2') !== baseAuth) {
      return true;
    }
    const fields = [
      'base_url', 'token_url', 'client_id', 'scope',
      'service', 'action', 'edit_url_template',
    ];
    for (const k of fields) {
      if (norm(f[k]) !== norm(s[k])) {
        return true;
      }
    }
    // min_value / max_value — form holds strings, status numbers.
    const sMin = (s.min_value != null) ? String(s.min_value) : '';
    const sMax = (s.max_value != null) ? String(s.max_value) : '';
    if (norm(f.min_value) !== sMin) {
      return true;
    }
    if (norm(f.max_value) !== sMax) {
      return true;
    }
    // Write-only secrets: any non-empty value in the form is a pending
    // change. Blank = keep current; the operator hasn't typed anything.
    if ((f.client_secret || '').length > 0) {
      return true;
    }
    if ((f.lifetime_token || '').length > 0) {
      return true;
    }
    // Asset-inventory-scoped tunables wired into THIS section's
    // Save so editing them flips the same amber ring as the rest of
    // the asset form. Mirror of the notifications panel pattern
    // (`notifTunables` in `_appriseSnapshot`). Comparing tuningForm
    // values to the previously-saved baseline (`_tuningBaseline`
    // captured at loadTuning) — when the operator hits Save, the
    // POST body includes these keys + `loadTuning()` after-save
    // resets the baseline so dirty flips back to false.
    const tf = this.tuningForm || {};
    const baselineStr = this._tuningBaseline || '';
    // Re-parse baseline to read the two asset tunables. Defensive
    // — if the baseline string ever fails to parse, fall back to
    // empty strings so the comparison just looks at current form
    // values.
    let baseline = {};
    try {
      baseline = baselineStr ? JSON.parse(baselineStr) : {};
    } catch (_e) {
      baseline = {};
    }
    const tunableKeys = [
      'tuning_asset_inventory_token_timeout_seconds',
      'tuning_asset_inventory_fetch_timeout_seconds',
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

  // Wrapper for the AI-palette dispatcher — kicks `/api/asset-inventory/test`
  // and surfaces the outcome via toast. Shape mirrors the other
  // test-* methods (`testWebminConnection` / `testBeszelConnection`)
  // so the palette dispatcher can call any of them uniformly via
  // `typeof this.testXConnection === 'function'`. The Admin →
  // Asset Inventory tab also has an inline button bound to the
  // same endpoint; this wrapper exists so the AI palette can fire
  // the test without forcing the user to navigate to that tab
  // first.
  async testAssetInventoryConnection() {
    try {
      const r = await fetch('/api/asset-inventory/test', {method: 'POST'});
      const j = await r.json().catch(() => ({}));
      if (j && j.ok) {
        if (this.recordTestSuccess) {
          this.recordTestSuccess('asset_inventory');
        }
        this.showToast(j.detail || this.t('toasts_extra.test_result_ok'), 'success');
      } else {
        this.showToast(j.detail || this.t('toasts_extra.test_result_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },
  // Test-before-Save snapshot for Asset Inventory. Mirrors the
  // Portainer / OIDC `_xSnapshot()` pattern but covers BOTH auth
  // modes (oauth2 and lifetime_token) so an admin can Test in either
  // shape and the snapshot matches at Save time. Secrets follow the
  // write-only contract — any non-empty form value is treated as a
  // pending write (encoded as the marker `<set>`); blank means
  // "keep current".
  _assetSnapshot() {
    const f = this.assetForm || {};
    const s = this.assetStatus || {};
    const mode = (f.auth_mode === 'lifetime_token') ? 'lifetime_token' : 'oauth2';
    return JSON.stringify({
      enabled: !!(this.settings || {}).asset_inventory_enabled,
      auth_mode: mode,
      base_url: (f.base_url || '').trim(),
      token_url: (f.token_url || '').trim(),
      client_id: (f.client_id || '').trim(),
      scope: (f.scope || '').trim(),
      client_secret: f.client_secret ? '<set>' : '',
      lifetime_token: f.lifetime_token ? '<set>' : '',
      service: (f.service || '').trim(),
      action: (f.action || '').trim(),
      min_value: String(f.min_value ?? '').trim(),
      max_value: String(f.max_value ?? '').trim(),
      edit_url: (f.edit_url_template || '').trim(),
      verify_tls: !!f.verify_tls,
      baseEnabled: (s.enabled !== false),
      baseAuth: (s.auth_mode === 'lifetime_token') ? 'lifetime_token' : 'oauth2',
      baseBaseUrl: s.base_url || '',
      baseTokenUrl: s.token_url || '',
      baseClientId: s.client_id || '',
      baseScope: s.scope || '',
      baseService: s.service || '',
      baseAction: s.action || '',
      baseMin: (s.min_value != null) ? String(s.min_value) : '',
      baseMax: (s.max_value != null) ? String(s.max_value) : '',
      baseEditUrl: s.edit_url_template || '',
      baseVerify: (s.verify_tls !== false),
    });
  },
  // Save-button gate for Asset Inventory — same shape as
  // `canSavePortainer()` / `canSaveOidc()`. When the master toggle
  // is OFF, Save is unconstrained (no upstream probe will run). When
  // ON, the operator must run a successful Test against the CURRENT
  // form values before Save unlocks; any edit after a passing Test
  // re-locks Save by mutating `_assetSnapshot()` away from
  // `_assetLastPassedTest`.
  canSaveAsset() {
    if (!(this.settings || {}).asset_inventory_enabled) {
      return true;
    }
    return this._assetLastPassedTest === this._assetSnapshot()
      && !!this._assetLastPassedTest;
  },

  // --- Asset inventory ---
  async saveAssetSettings() {
    const body = {
      asset_inventory_auth_mode: (this.assetForm.auth_mode === 'lifetime_token')
        ? 'lifetime_token' : 'oauth2',
      asset_inventory_base_url: (this.assetForm.base_url || '').trim(),
      asset_inventory_token_url: (this.assetForm.token_url || '').trim(),
      asset_inventory_client_id: (this.assetForm.client_id || '').trim(),
      asset_inventory_scope: (this.assetForm.scope || '').trim(),
      asset_inventory_service: (this.assetForm.service || '').trim(),
      asset_inventory_action: (this.assetForm.action || '').trim(),
      asset_inventory_min_value: String(this.assetForm.min_value ?? '').trim(),
      asset_inventory_max_value: String(this.assetForm.max_value ?? '').trim(),
      asset_inventory_edit_url_template: String(this.assetForm.edit_url_template ?? '').trim(),
      asset_inventory_verify_tls: !!this.assetForm.verify_tls,
    };
    if (this.assetForm.client_secret && this.assetForm.client_secret.trim()) {
      body.asset_inventory_client_secret = this.assetForm.client_secret;
    }
    if (this.assetForm.lifetime_token && this.assetForm.lifetime_token.trim()) {
      body.asset_inventory_lifetime_token = this.assetForm.lifetime_token.trim();
    }
    // Asset-inventory-scoped tunables — section-owned save commits
    // them alongside the plain settings so editing flips the same
    // amber ring as the rest of the form. Per-key int + bounds
    // validation mirrors `saveTuning()` so we surface a friendly
    // toast on out-of-range values instead of a backend 400.
    const tf = this.tuningForm || {};
    const assetTunableKeys = [
      'tuning_asset_inventory_token_timeout_seconds',
      'tuning_asset_inventory_fetch_timeout_seconds',
    ];
    for (const k of assetTunableKeys) {
      const raw = tf[k];
      if (raw === '' || raw == null) {
        body[k] = '';
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
      body[k] = String(raw).trim();
    }
    this.assetSaving = true;
    try {
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      this.showToast(this.t('admin_assets.saved'), 'success');
      await this.loadSettings();
      // Re-read tuning baseline so `assetDirty()` flips back to
      // false after a successful save (mirrors loadTuning's reset
      // pattern that the global saveTuning path uses).
      await this.loadTuning();
      this.assetTestResult = null;
    } catch (e) {
      this.showToast(this.t('admin_assets.save_failed') + ': ' + e.message, 'error');
    } finally {
      this.assetSaving = false;
    }
  },
  async testAssetConnection() {
    this.assetTestResult = {pending: true};
    // Snapshot the form NOW so we can stamp it on success — same
    // pattern as `testPortainerConnection` / `testOidcConnection`.
    const probedSnapshot = this._assetSnapshot();
    const mode = (this.assetForm.auth_mode === 'lifetime_token')
      ? 'lifetime_token' : 'oauth2';
    // send the in-flight verify_tls so admins can flip the
    // form's checkbox OFF and Test a self-signed asset API before
    // saving (mirrors testOidcConnection's shape).
    const body = {auth_mode: mode, verify_tls: !!this.assetForm.verify_tls};
    if (mode === 'lifetime_token') {
      body.base_url = (this.assetForm.base_url || '').trim();
      body.lifetime_token = this.assetForm.lifetime_token || '';
      body.service = (this.assetForm.service || '').trim();
      body.action = (this.assetForm.action || '').trim();
      body.min_value = String(this.assetForm.min_value ?? '').trim();
      body.max_value = String(this.assetForm.max_value ?? '').trim();
    } else {
      body.token_url = (this.assetForm.token_url || '').trim();
      body.client_id = (this.assetForm.client_id || '').trim();
      body.scope = (this.assetForm.scope || '').trim();
      body.client_secret = this.assetForm.client_secret || '';
    }
    try {
      const r = await fetch('/api/asset-inventory/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const j = await r.json().catch(() => ({}));
      // Prefer the localized catalog message when the backend sent
      // a code; the raw `detail` still wins when it's a specific
      // upstream message (e.g. the upstream's own `details` field).
      const detail = j && j.error_code
        ? this.formatError({error: j.detail, error_code: j.error_code, error_params: j.error_params}, j.detail)
        : (j.detail || '');
      this.assetTestResult = {ok: !!j.ok, detail};
      if (j && j.ok) {
        this._assetLastPassedTest = probedSnapshot;
        this.recordTestSuccess('asset_inventory');
      }
    } catch (_) {
      this.assetTestResult = {ok: false, detail: this.t('toasts.network_error')};
    }
  },
  async loadAssetCache() {
    try {
      const r = await fetch('/api/asset-inventory');
      if (r.ok) {
        this.assetCache = await r.json();
      } else {
        this.assetCache = {ok: false, error: `HTTP ${r.status}`, assets: []};
      }
    } catch (e) {
      this.assetCache = {ok: false, error: String(e), assets: []};
    }
  },
  // Look up an asset by the host's custom_number. Walks the cached
  // asset list each call — N is small (tens of hosts) so a linear
  // scan avoids the staleness risk of a memoised index when the
  // cache reloads. Returns `null` when no match or no cache.
  //
  // Field-name strategy: <asset-api-host>'s MDI ships CamelCase + nested
  // (`Brand: {Name}`, `Location: {CalculatedName}`, `Type: {Name}`,
  // `SerialNumber`, `Model`, `CustomNumber`, `Hostname` (CSV string),
  // `Interfaces[].IP`). We also accept the snake_case aliases
  // (`vendor`/`manufacturer`/`model`/`serial`/`location`/`custom_number`)
  // so a generic non-MDI upstream still works without a schema map.
  assetForHost(h) {
    if (!h) {
      return null;
    }
    const assets = (this.assetCache && Array.isArray(this.assetCache.assets))
      ? this.assetCache.assets : null;
    const n = parseInt(h.custom_number, 10);
    // Prefer the LIVE asset cache when it holds a match for this host's
    // custom_number, so an asset REFRESH is reflected immediately — incl.
    // an OPEN host drawer + arrow-nav, where the backend-injected
    // `h.asset` below is FROZEN at host-fetch time and otherwise left the
    // port-scan "not in asset" mismatch markers stale after a refresh.
    // The stamped `h.asset` stays as the fallback for the pre-cache-load
    // window AND for no-agent hosts the cache doesn't list. (`assets` +
    // `n` are reused by the matching loop below.)
    const hasCacheMatch = !!(assets && assets.length && Number.isFinite(n)
      && assets.some((a) => a
        && parseInt(a.CustomNumber ?? a.custom_number ?? a.number ?? a.id, 10) === n));
    if (!hasCacheMatch && h.asset && typeof h.asset === 'object') {
      return Object.assign({_raw: null}, h.asset);
    }
    if (!assets || !assets.length || !Number.isFinite(n)) {
      return null;
    }
    // Walk-helper: accepts a string OR a {Name}/{CalculatedName}
    // dict and returns the best display string. Catches both flat
    // and nested upstream shapes in one call.
    const pick = (...candidates) => {
      for (const v of candidates) {
        if (v == null) {
          continue;
        }
        if (typeof v === 'string' && v.trim()) {
          return v.trim();
        }
        if (typeof v === 'object') {
          const s = v.CalculatedName || v.Name || v.name || '';
          if (typeof s === 'string' && s.trim()) {
            return s.trim();
          }
        }
      }
      return '';
    };
    for (const a of assets) {
      if (!a) {
        continue;
      }
      const candidate = a.CustomNumber ?? a.custom_number ?? a.number ?? a.id;
      if (parseInt(candidate, 10) !== n) {
        continue;
      }
      // Hostname CSV → array of FQDNs.
      const hostnameStr = String(a.Hostname || a.hostname || '').trim();
      const hostnames = hostnameStr ? hostnameStr.split(',').map(s => s.trim()).filter(Boolean) : [];
      // Interfaces — ordered by `Number` (then `Name`) so the
      // drawer renders them in the operator's intended order
      // rather than insertion order.
      const ifacesRaw = Array.isArray(a.Interfaces) ? a.Interfaces : (a.interfaces || []);
      const ifaces = ifacesRaw.slice().sort((x, y) => {
        const xn = (x && x.Number != null) ? x.Number : Infinity;
        const yn = (y && y.Number != null) ? y.Number : Infinity;
        if (xn !== yn) {
          return xn - yn;
        }
        return String((x && x.Name) || '').localeCompare(String((y && y.Name) || ''));
      }).map(i => ({
        name: String((i && (i.Name || i.name)) || '').trim(),
        ip: String((i && (i.IP || i.ip)) || '').trim(),
        mac: String((i && (i.MacAddress || i.mac_address)) || '').trim(),
        number: (i && i.Number != null) ? i.Number : null,
        comment: String((i && i.Comment) || '').trim(),
        enabled: !i || i.IsEnabled !== false,
        ip_version: String((i && (i.IPVersion || i.ip_version)) || '').trim(),
      }));
      // Primary IP — first enabled iface, then any iface, then a
      // flat `ip` alias on the asset row.
      let primaryIp = '';
      if (ifaces.length) {
        const enabled = ifaces.find(i => i.enabled && i.ip);
        const any = enabled || ifaces.find(i => i.ip);
        if (any) {
          primaryIp = any.ip;
        }
      }
      if (!primaryIp) {
        primaryIp = String(a.ip || '').trim();
      }
      // Ports — flatten the {Port: {...}} nesting MDI uses so the
      // template can read port.name / port.number / port.service_name
      // directly. ServiceName in MDI doubles as a clickable URL
      // when it starts with http(s); we pass it through unchanged.
      const portsRaw = Array.isArray(a.Ports) ? a.Ports : (a.ports || []);
      const ports = portsRaw.map(p => {
        const inner = (p && (p.Port || p.port)) || {};
        return {
          id: p && (p.ID || p.id),
          name: String(inner.Name || inner.name || '').trim(),
          number: (inner.Port != null) ? inner.Port : (inner.port != null ? inner.port : null),
          service_name: String(inner.ServiceName || inner.service_name || '').trim(),
          protocol: String(inner.Protocol || inner.protocol || '').trim(),
        };
      }).filter(p => p.name || p.number != null);
      // Optional sub-fields from the guide's nested objects:
      // - Brand.Link     — vendor URL, clickable from the drawer
      // - Status.Color   — #RRGGBB used to tint the status pill
      // - Location.Details — extra free-text address / detail
      const brandObj = (a.Brand && typeof a.Brand === 'object') ? a.Brand : null;
      const statusObj = (a.Status && typeof a.Status === 'object') ? a.Status : null;
      const locObj = (a.Location && typeof a.Location === 'object') ? a.Location : null;
      return {
        id: a.ID ?? a.id ?? null,
        vendor: pick(a.Brand, a.brand, a.vendor, a.manufacturer),
        brand_link: brandObj ? String(brandObj.Link || brandObj.link || '').trim() : '',
        model: pick(a.Model, a.model, a.product, a.product_name),
        // Serial — placeholder values like "NONE224" / "NONE100" /
        // "NONEXXX" mean "no real serial recorded upstream"
        // (typically a VM that doesn't have a hardware serial).
        // Return empty so the existing `x-if="assetForHost(h).serial"`
        // gate hides the row entirely instead of surfacing a
        // misleading placeholder string.
        serial: (() => {
          const s = pick(a.SerialNumber, a.serial, a.serial_number);
          return /^NONE\d*$/i.test(s) ? '' : s;
        })(),
        location: pick(a.Location, a.location, a.site, a.room),
        location_details: locObj ? String(locObj.Details || locObj.details || '').trim() : '',
        type: pick(a.Type, a.type),
        // Type SHORT-form — render a compact `[VM]` / `[PHY]` / etc.
        // badge when the upstream Type object carries any short-form
        // alias. <asset-api-host>'s payloads have surfaced multiple casings
        // for this field across asset rows ("Virtual Machine" with
        // shortname "VM", "Physical Server" with code "PHY"), so we
        // walk every plausible naming the team has used. Returns ''
        // when only the long Name is present, which lets
        // hostTypePrefix fall back to that long form via `type`.
        type_short: (() => {
          const obj = (a.Type && typeof a.Type === 'object') ? a.Type
            : (a.type && typeof a.type === 'object') ? a.type
              : null;
          if (!obj) {
            return '';
          }
          // Cast a wide net — match every casing variant + every
          // synonym for "short form" we've seen on this kind of
          // payload. First non-blank wins.
          const candidates = [
            obj.shortname, obj.ShortName, obj.SHORTNAME,
            obj.short_name, obj.Shortname, obj.shortName,
            obj.short, obj.Short, obj.SHORT,
            obj.code, obj.Code, obj.CODE,
            obj.abbr, obj.Abbr, obj.ABBR,
            obj.abbreviation, obj.Abbreviation,
            obj.acronym, obj.Acronym, obj.ACRONYM,
            obj.symbol, obj.Symbol,
            obj.tag, obj.Tag, obj.TAG,
            obj.slug, obj.Slug, obj.SLUG,
            obj.alias, obj.Alias,
          ];
          for (const v of candidates) {
            if (v == null) {
              continue;
            }
            const s = String(v).trim();
            if (s) {
              return s;
            }
          }
          return '';
        })(),
        name: pick(a.Name, a.name),
        hostnames,
        primary_ip: primaryIp,
        ram: pick(a.RAM, a.ram, a.memory),
        sku: pick(a.SKU, a.sku),
        firmware: pick(a.Firmware, a.firmware),
        hardware_version: pick(a.HardwareVersion, a.hardware_version),
        barcode: pick(a.Barcode, a.barcode),
        comment: pick(a.Comment, a.comment),
        status_name: pick(a.Status, a.status),
        status_color: statusObj ? String(statusObj.Color || statusObj.color || '').trim() : '',
        // Server emits "Y-m-d H:i:s" strings in its local timezone.
        // The drawer renders them with `fmtAssetDateString` (separate
        // helper since the value is a string, not an epoch).
        last_modified: String(a.LastModifiedOn || a.last_modified_on || '').trim(),
        created_on: String(a.CreatedOn || a.created_on || '').trim(),
        interfaces: ifaces,
        ports,
        // Child cert assets — when the upstream asset DB tracks SSL /
        // TLS certificates as their own Type rows linked back to this
        // host (typically via a parent / parent_id / ParentID field
        // pointing at a.ID), surface them so the drawer's TLS row can
        // render a "renewal record" link next to the cert subject.
        // Walks the full asset list looking for entries whose Type
        // ShortName matches a cert-like signal AND whose parent field
        // resolves to this host's asset id. Empty list when no certs
        // are tracked upstream — the drawer's link gates on length > 0
        // so an empty array is a clean no-op.
        certs: this._certsForAsset(a, assets),
        _raw: a,
      };
    }
    return null;
  },
  // Edit-on-upstream URL for the asset. Resolution order:
  // 1. `asset_inventory.edit_url_template` from settings — the
  //    operator-configured prefix or template. If it contains
  //    a `{id}` placeholder we substitute; otherwise we append
  //    the asset id to the end (so a bare prefix like
  //    "https://<asset-api-host>/admin/pages/assets/asset_management.php?s=edit&si="
  //    produces "...&si=42" with no extra plumbing).
  // 2. Fallback: derive from `assetCache.upstream` by stripping
  //    `/api` and appending `?asset=<id>` — a reasonable guess
  //    that may not work for every deployment.
  // 3. Empty string when no upstream / template is known.
  assetEditUrl(asset) {
    if (!asset || asset.id == null) {
      return '';
    }
    const tpl = ((this.assetStatus && this.assetStatus.edit_url_template) || '').trim();
    if (tpl) {
      if (tpl.includes('{id}') || tpl.includes('{custom_number}') || tpl.includes('{base}')) {
        const upstream = (this.assetCache && this.assetCache.upstream) || '';
        return tpl
          .replace('{id}', String(asset.id))
          .replace('{custom_number}', String(asset._raw && asset._raw.CustomNumber || ''))
          .replace('{base}', upstream);
      }
      // Bare prefix — append the id at the end.
      return tpl + String(asset.id);
    }
    const upstream = (this.assetCache && this.assetCache.upstream) || '';
    if (!upstream) {
      return '';
    }
    const adminBase = upstream.replace(/\/api\/?$/, '');
    return `${adminBase}?asset=${asset.id}`;
  },
  // Resolve a clickable URL for a port chip. Three cases:
  // 1. The port's `service_name` is already an http(s):// URL —
  //    use it as-is (operator-curated full URL).
  // 2. The port's `name` or `protocol` indicates HTTP / HTTPS —
  //    synthesize a URL from the host's FQDN. Picks the first
  //    hostname from the asset row, falling back to `h.host`,
  //    then `h.label`. Skips the explicit `:port` when it's the
  //    protocol's default (80 / 443) so the URL stays clean.
  // 3. Anything else — empty string (renders as a plain chip).
  assetPortServiceUrl(port, h) {
    const s = String((port && port.service_name) || '').trim();
    if (/^https?:\/\//i.test(s)) {
      return s;
    }
    // Substring match on name + protocol — catches obvious HTTP
    // ports ("HTTP", "HTTPS") AND looser labels like "HTTP Admin"
    // / "NetData" (Protocol="HTTP") / "NGINX Admin" without a
    // service_name URL. HTTPS check runs first so a port labelled
    // "HTTPS" doesn't fall into the http bucket.
    const name = String((port && port.name) || '').toUpperCase();
    const proto = String((port && port.protocol) || '').toUpperCase();
    const haystack = name + ' ' + proto;
    const isHttps = haystack.includes('HTTPS');
    const isHttp = !isHttps && haystack.includes('HTTP');
    // Protocol "IP" — use the host's raw IP (not its FQDN) for
    // the URL host part. Common for node-exporter / netdata
    // metric scrapers where DNS isn't reliable. Defaults to http
    // scheme since that's the typical raw-IP use case.
    const isIpProto = !isHttp && !isHttps && proto === 'IP';
    if (!isHttp && !isHttps && !isIpProto) {
      return '';
    }

    const asset = (h && this.assetForHost) ? this.assetForHost(h) : null;
    // Pick host (FQDN) and ip from the asset row + curated host.
    // The asset's `Hostname` CSV is ordered LEAST-specific → MOST-
    // specific by upstream convention (e.g. raw IP first, friendly
    // name last), so we pick the LAST entry — that's the canonical
    // FQDN the operator wants links to land on.
    let fqdn = '';
    if (asset && Array.isArray(asset.hostnames) && asset.hostnames.length) {
      fqdn = asset.hostnames[asset.hostnames.length - 1];
    }
    if (!fqdn && h) {
      fqdn = String(h.host || h.id || h.label || '').trim();
    }
    const ip = (asset && asset.primary_ip) || (h && h.ip) || '';

    // IP-protocol path uses raw IP and falls back to FQDN if no
    // IP is known (better something clickable than nothing).
    if (isIpProto) {
      const target = ip || fqdn;
      if (!target) {
        return '';
      }
      const num = (port && port.number != null) ? port.number : null;
      const portSuffix = (num != null) ? (':' + num) : '';
      return `http://${target}${portSuffix}`;
    }

    if (!fqdn) {
      return '';
    }
    const scheme = isHttps ? 'https' : 'http';
    const defaultPort = isHttps ? 443 : 80;
    const num = (port && port.number != null) ? port.number : null;
    const portSuffix = (num != null && num !== defaultPort) ? (':' + num) : '';
    return `${scheme}://${fqdn}${portSuffix}`;
  },
  async refreshAssetCache() {
    this.assetRefreshing = true;
    try {
      const r = await fetch('/api/asset-inventory/refresh', {method: 'POST'});
      const j = await r.json().catch(() => ({}));
      if (j && j.ok) {
        this.showToast(this.t('admin_assets.refresh_ok', {count: j.count || 0}), 'success');
      } else {
        this.showToast(this.t('admin_assets.refresh_failed') + ': ' + this.formatError(j, 'unknown'), 'error');
      }
      await this.loadAssetCache();
    } catch (e) {
      this.showToast(this.t('admin_assets.refresh_failed') + ': ' + e.message, 'error');
    } finally {
      this.assetRefreshing = false;
    }
  },
};
