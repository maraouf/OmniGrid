// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Admin → Hosts editor — CRUD on the curated `hosts_config` array
// (Beszel/Pulse/Webmin/SNMP/NodeExporter/Ping aliases + per-host SSH).

export default {
  // Admin → Hosts editor state. ``hostsConfig`` is the curated list
  // pulled from /api/hosts/config (array of host records with
  // per-provider name mappings). ``hostsConfigSaving`` gates the
  // Save button's spinner.
  hostsConfig: [],
  // Stable display-order snapshot for the hostsConfig editor.
  // Rebuilt on load / add / remove / blur of custom_number — NOT on
  // every keystroke. Keeps rows from re-sorting mid-typing. See
  // filteredHostsConfig() + rebuildHostsConfigOrder() for the rule.
  hostsConfigSortedOrder: [],
  hostsConfigLoading: false,
  hostsConfigSaving: false,
  hostsConfigDirty: false,
  hostsConfigFilter: '',
  // Client-side pagination for the Admin → Hosts editor. At
  // ~200 hosts the rendered DOM (each row is a multi-input form
  // card) becomes heavy; slicing the rendered list to one page at
  // a time keeps tab switches + filter typing snappy. Full array
  // still lives in `hostsConfig`, so dirty tracking + duplicate-id
  // validator + save-path are untouched. Page index persists across
  // reloads so the operator returns to the same page after
  // tab navigation, full reload, or browser restart. A `$watch` in
  // `init()` writes the value back; a clamp in `loadHostsConfig`
  // catches the case where the stored page is now beyond the data.
  hostsConfigPage: (() => {
    try {
      const raw = localStorage.getItem('hostsConfigPage');
      const n = parseInt(raw, 10);
      if (Number.isFinite(n) && n >= 1) {
        return n;
      }
    } catch {
    }
    return 1;
  })(),
  hostsConfigPerPage: (() => {
    try {
      const raw = localStorage.getItem('hostsConfigPerPage');
      const n = parseInt(raw, 10);
      if (Number.isFinite(n) && [10, 25, 50, 100, 200].includes(n)) {
        return n;
      }
    } catch {
    }
    return 50;
  })(),
  // Datalist-backed autocomplete source for the Hosts editor.
  // Filled by discoverHosts() on demand; stays empty until the
  // operator asks.
  hostsDiscovery: {beszel: [], pulse: [], webmin: [], snmp: []},
  // Per-row test results keyed by row index. Each entry has
  // ``pending: bool`` and the provider payloads {beszel, pulse,
  // node_exporter} each with {ok, skipped, detail}. Cleared when
  // a row's fields change so stale results aren't shown.
  hostsTestResults: {},
  hostsTestingAll: false,
  // sshSettingsSaving — already declared elsewhere as sshSettingsBusy.
  // totpPolicySaving — already declared at line ~2580 with its save fn.
  // assetSaving — already declared at line ~431.
  // Admin → Hosts per-row collapse state. Keyed by host id so
  // expand/collapse survives reorders. Fresh rows auto-expand on
  // addHostRow so the operator sees the fields. Saved into
  // localStorage so a page reload doesn't re-collapse everything.
  hostsConfigExpanded: (() => {
    try {
      const raw = typeof localStorage !== 'undefined'
        ? localStorage.getItem('hostsConfigExpanded') : null;
      const parsed = raw ? JSON.parse(raw) : {};
      return (parsed && typeof parsed === 'object') ? parsed : {};
    } catch {
      return {};
    }
  })(),
  // Bulk vendor applicator state — selected vendor for the
  // "Apply vendor MIB to all visible rows" picker. Empty = "no
  // vendor chosen, both Apply / Clear disabled".
  hostsConfigBulkVendor: '',

  // --- Admin → Hosts: curated host list editor ---
  async loadHostsConfig() {
    // Guard against clobbering unsaved edits. The operator can hit
    // Reload deliberately (confirms via SweetAlert) or bypass when
    // clean. Native confirm() was the original implementation but
    // it doesn't translate / RTL-flip and can't theme to the dark
    // surface tokens.
    if (this.hostsConfigDirty) {
      const ok = await this.confirmDialog({
        title: this.t('admin_hosts.unsaved_confirm_title'),
        html: this.t('admin_hosts.unsaved_confirm_html'),
        icon: 'warning',
        confirmText: this.t('admin_hosts.unsaved_confirm_button'),
        confirmColor: this._cssVar('--danger'),
      });
      if (!ok) {
        return;
      }
    }
    this.hostsConfigLoading = true;
    // Watchdog cap — mirrors `_runWithBusy`. If `fetch` hangs (server
    // unreachable / dead probe), `await` never returns and finally
    // never runs; the Admin → Hosts reload button would stay stuck
    // disabled across the session. Watchdog clears `hostsConfigLoading`
    // after `_LOAD_BUSY_MAX_MS` so the UI recovers on its own.
    const _wd = setTimeout(() => {
        if (this.hostsConfigLoading) {
          this.hostsConfigLoading = false;
        }
      },
      this._LOAD_BUSY_MAX_MS || 30000);
    try {
      const r = await fetch('/api/hosts/config');
      if (!r.ok) {
        this.showToast(this.t('admin_hosts.load_failed_status', {status: r.status}), 'error');
        return;
      }
      const d = await r.json();
      this.hostsConfig = Array.isArray(d.hosts) ? d.hosts : [];
      // Invalidate filtered-list cache — see saveHostsConfig
      // for the full rationale. Cache key doesn't notice an array
      // identity swap, so without this `pagedHostsConfig()` would
      // keep returning pre-load row references on the next render.
      this._filteredHostsConfigCache.key = '';
      this._filteredHostsConfigCache.value = null;
      // Hydrate each row's webmin_url from settings.webmin_aliases —
      // the hosts_config endpoint doesn't carry the URL, the settings
      // table does. Keeps the editor's per-row field in sync with
      // what the probe pipeline actually reads.
      const aliases = (this.settings && this.settings.webmin_aliases) || {};
      for (const row of this.hostsConfig) {
        if (row && row.id && !row.webmin_url) {
          row.webmin_url = aliases[row.id] || '';
        }
        // Ensure every row has an ssh sub-object so Alpine's x-model
        // doesn't reactively create it piecemeal (which would break
        // the dirty tracker).
        if (!row.ssh || typeof row.ssh !== 'object') {
          row.ssh = {};
        }
        // same defensive default for the per-host ping
        // sub-object so Alpine bindings can read row.ping.enabled
        // without an undefined-chain on first render.
        if (!row.ping || typeof row.ping !== 'object') {
          row.ping = {};
        }
        // same defensive default for the per-host SNMP
        // override sub-object. Bare `snmp_name` (string) is separate
        // and lives on the row directly; the `snmp` dict is only used
        // when the operator wants to override the global community /
        // version / port / v3 keys for THIS host.
        if (!row.snmp || typeof row.snmp !== 'object') {
          row.snmp = {};
        }
        if (typeof row.snmp_name !== 'string') {
          row.snmp_name = '';
        }
        // Dedicated probe target — defaults to "" so Alpine's
        // x-model has something concrete to bind to on first render
        // (avoids the "row.address is undefined" reactive flicker
        // when the operator types into the field before the API
        // response shape settles). Same defensive idiom used for
        // every other free-text field on the row.
        if (typeof row.address !== 'string') {
          row.address = '';
        }
        // Hydrate the per-host mount-exclusion textarea from the
        // persisted `snmp.exclude_mounts` array. The editor binds
        // a virtual `exclude_mounts_text` field (one path per
        // line, easier to type than maintaining a list); save-
        // side splits + dedupes back into the array shape the
        // backend stores. Keeps the underlying array around so
        // a load-without-edit save round-trips cleanly.
        if (Array.isArray(row.snmp.exclude_mounts) && !row.snmp.exclude_mounts_text) {
          row.snmp.exclude_mounts_text = row.snmp.exclude_mounts.join('\n');
        }
        if (typeof row.snmp.exclude_mounts_text !== 'string') {
          row.snmp.exclude_mounts_text = '';
        }
        // Hydrate the per-host HTTP-probe URL textarea from the
        // persisted `http_probe.urls` array. Same virtual-string-
        // field pattern as `snmp.exclude_mounts_text` above. The
        // old `:value="urls.join('\n')"` + `@input` round-trip ate
        // every Enter key because `.split('\n').filter(Boolean)`
        // dropped the trailing empty element of `"a\n"` → array
        // `["a"]` → re-render → "a" (no newline) → cursor jump.
        // Storing the raw textarea string here + parsing it on
        // save lets Alpine's `x-model` handle DOM state natively
        // without any per-keystroke round-trip.
        if (!row.http_probe || typeof row.http_probe !== 'object') {
          row.http_probe = {};
        }
        if (Array.isArray(row.http_probe.urls) && !row.http_probe.urls_text) {
          row.http_probe.urls_text = row.http_probe.urls.join('\n');
        }
        if (typeof row.http_probe.urls_text !== 'string') {
          row.http_probe.urls_text = '';
        }
        // Stamp a stable per-row uid the first time we see this
        // row. Used as the x-for :key so DOM elements never tear
        // down + re-mount mid-typing (which loses input focus and
        // triggers the "still typing in ID is causing refresh"
        // symptom). Persisted into hostsConfig so subsequent
        // reconciliation passes preserve identity even when the
        // sort order changes. Uses `crypto.randomUUID()` (universally
        // supported in modern browsers) — the value is a UI-only
        // identifier, not a security secret, but the
        // crypto-strength source silences the CodeQL
        // `js/insecure-randomness` flag and removes any chance of
        // collision in fleets with thousands of rows.
        if (!row._uid) {
          row._uid = this._mintRowUid();
        }
      }
      this.hostsConfigDirty = false;
      this.rebuildHostsConfigOrder();
      // Clamp paging to the loaded data — preserves the persisted
      // page when valid, and falls back to the new last page
      // when the data has shrunk. Don't unconditionally reset to 1:
      // the operator expects to return to the same page after reload.
      this.hostsConfigPage = Math.min(
        Math.max(1, this.hostsConfigPage),
        this.hostsConfigTotalPages(),
      );
    } catch (e) {
      this.showToast(this.t('admin_hosts.load_failed', {error: e.message}), 'error');
    } finally {
      this.hostsConfigLoading = false;
      try {
        clearTimeout(_wd);
      } catch (_) {
      }
    }
  },
  async discoverHosts() {
    // Pull every name each enabled provider knows about so the
    // editor's datalist inputs can offer native autocomplete. We
    // don't auto-add rows — the operator still decides which names
    // to curate. Reports per-provider errors inline.
    this.hostsDiscovering = true;
    try {
      const r = await fetch('/api/hosts/discover');
      if (!r.ok) {
        this.showToast(this.t('admin_hosts.discover.failed_status', {status: r.status}), 'error');
        return;
      }
      const d = await r.json();
      this.hostsDiscovery = {
        beszel: Array.isArray(d.beszel) ? d.beszel : [],
        pulse: Array.isArray(d.pulse) ? d.pulse : [],
        webmin: Array.isArray(d.webmin) ? d.webmin : [],
        // SNMP discovery surfaces the configured aliases'
        // values (TARGETS, not curated row ids). Empty by default.
        snmp: Array.isArray(d.snmp) ? d.snmp : [],
      };
      const errs = d.errors || {};
      const errKeys = Object.keys(errs);
      const bTotal = this.hostsDiscovery.beszel.length;
      const pTotal = this.hostsDiscovery.pulse.length;
      const wTotal = this.hostsDiscovery.webmin.length;
      if (errKeys.length && (bTotal + pTotal + wTotal) === 0) {
        this.showToast(
          this.t('admin_hosts.discover.no_response', {detail: errKeys.map(k => k + '=' + errs[k]).join(' · ')}),
          'error',
        );
      } else {
        const parts = [];
        if (bTotal) {
          parts.push(`${bTotal} Beszel`);
        }
        if (pTotal) {
          parts.push(`${pTotal} Pulse`);
        }
        if (wTotal) {
          parts.push(`${wTotal} Webmin`);
        }
        this.showToast(
          parts.length
            ? this.t('admin_hosts.discover.found', {detail: parts.join(', ')})
            : this.t('admin_hosts.discover.no_results'),
          parts.length ? 'success' : 'error',
        );
      }
    } catch (e) {
      this.showToast(this.t('admin_hosts.discover.failed', {error: e.message}), 'error');
    } finally {
      this.hostsDiscovering = false;
    }
  },
  // Admin-editor view filter. We return a list of ``{row, idx}``
  // tuples so the template can render only matching rows while
  // still having the original index for move/remove/test actions.
  // Memoised filter result. — `pagedHostsConfig` and
  // `hostsConfigTotalPages` both call this getter on every Alpine
  // re-evaluation. With 500 hosts + a typing-driven filter that's
  // 1000+ walks per keystroke. Cache the result keyed on
  // `(filter, hostsConfig.length, hostsConfigSortedOrder.length)`
  // so repeated access in one tick is O(1). The cache busts naturally
  // when ANY of those inputs changes (add / remove / sort-rebuild /
  // typed filter character) so it's always fresh.
  _filteredHostsConfigCache: {key: '', value: null},
  filteredHostsConfig() {
    const q = (this.hostsConfigFilter || '').trim().toLowerCase();
    const order = (this.hostsConfigSortedOrder || []);
    const cfg = this.hostsConfig || [];
    const cacheKey = q + '|' + cfg.length + '|' + order.length;
    const cached = this._filteredHostsConfigCache;
    if (cached.key === cacheKey && cached.value) {
      return cached.value;
    }
    // Display order is a SNAPSHOT rebuilt only by
    // `rebuildHostsConfigOrder()` (called on load / add / remove /
    // blur of custom_number). Sorting reactively on every keystroke
    // was breaking input focus: typing "2" into a custom_number
    // field before finishing "24" would move the row mid-typing,
    // tearing down the DOM node and killing focus. Using a stable
    // snapshot means the sort applies on commit (blur), not on
    // keystroke — the operator can type "24" uninterrupted and the
    // row re-sorts when they tab away.
    const all = [];
    if (order.length === cfg.length && order.every(i => i < cfg.length)) {
      for (const idx of order) {
        all.push({row: cfg[idx], idx});
      }
    } else {
      // Fallback: snapshot is stale (hostsConfig grew/shrank since
      // last rebuild). Show in original order so nothing is lost —
      // next rebuild will re-sort.
      for (let idx = 0; idx < cfg.length; idx++) {
        all.push({row: cfg[idx], idx});
      }
    }
    let value;
    if (!q) {
      value = all;
    } else {
      value = all.filter(({row}) => {
        const hay = [
          row.id, row.label, row.ne_url,
          row.beszel_name, row.pulse_name,
          row.webmin_name, row.webmin_url,
          row.url, row.icon, row.ip,
        ].filter(Boolean).join(' ').toLowerCase();
        return hay.includes(q);
      });
    }
    cached.key = cacheKey;
    cached.value = value;
    return value;
  },

  // Page-of-N slice of `filteredHostsConfig()` for rendering.
  // Returns a *windowed* `[{row, idx}, ...]` array — the same shape
  // as `filteredHostsConfig` so the template iterator is unchanged
  // (still has the original `idx` for move/remove/test actions).
  // Page is clamped to the valid range so removing the last row
  // on page N doesn't leave the user staring at an empty list —
  // they auto-fall back to the new last page.
  pagedHostsConfig() {
    const all = this.filteredHostsConfig();
    const per = this.hostsConfigPerPage || 50;
    const total = all.length;
    const totalPages = Math.max(1, Math.ceil(total / per));
    // Clamp lazily — mutating state inside a getter would break
    // Alpine reactivity, so we just compute the safe page. The
    // visible state is normalised separately via `_clampHostsConfigPage`
    // on a $watch tick so `hostsConfigPage` itself eventually
    // catches up to the truth.
    const page = Math.min(Math.max(1, this.hostsConfigPage), totalPages);
    const start = (page - 1) * per;
    return all.slice(start, start + per);
  },
  // normaliser invoked from $watch handlers in `init()`.
  // Reads the same total-pages math `pagedHostsConfig` uses and
  // resets `hostsConfigPage` if it's out of bounds, so the visible
  // state (Page X / Y indicator) matches the rendered slice.
  _clampHostsConfigPage() {
    const per = this.hostsConfigPerPage || 50;
    const total = this.filteredHostsConfig().length;
    const totalPages = Math.max(1, Math.ceil(total / per));
    const safe = Math.min(Math.max(1, this.hostsConfigPage), totalPages);
    if (safe !== this.hostsConfigPage) {
      this.hostsConfigPage = safe;
    }
  },

  // Total page count given the current filter + per-page.
  // Returns at least 1 so the "Page 1 of 1" indicator renders even
  // with an empty list (matches the empty-state cards' tone).
  hostsConfigTotalPages() {
    const total = this.filteredHostsConfig().length;
    const per = this.hostsConfigPerPage || 50;
    return Math.max(1, Math.ceil(total / per));
  },

  // Pagination actions. Goto/clamps internally; a no-op call (e.g.
  // Next on the last page) leaves the page unchanged so the button
  // can be safely visible-but-disabled rather than hidden.
  hostsConfigGoToPage(n) {
    const tp = this.hostsConfigTotalPages();
    this.hostsConfigPage = Math.min(Math.max(1, parseInt(n, 10) || 1), tp);
  },
  hostsConfigPrevPage() {
    this.hostsConfigGoToPage(this.hostsConfigPage - 1);
  },
  hostsConfigNextPage() {
    this.hostsConfigGoToPage(this.hostsConfigPage + 1);
  },
  // Jump to and expand a specific row by id in the
  // Admin → Hosts editor. Used by deep-link affordances like the
  // host drawer's "+ Add URL" link so the operator lands directly
  // on the row they wanted to edit (page-skipping + chevron-
  // expanding handled in one call). No-op if the id isn't found.
  focusHostsConfigRow(id) {
    if (!id) {
      return;
    }
    const cfg = this.hostsConfig || [];
    const cfgIdx = cfg.findIndex(r => r && r.id === id);
    if (cfgIdx < 0) {
      return;
    }
    const row = cfg[cfgIdx];
    // Expand the row so the URL input is visible.
    if (row && row._uid) {
      this.hostsConfigExpanded = {...this.hostsConfigExpanded, [row._uid]: true};
    }
    // Find the row's position in the filtered list and page-jump.
    const filtered = this.filteredHostsConfig();
    const filteredIdx = filtered.findIndex(({idx}) => idx === cfgIdx);
    if (filteredIdx >= 0) {
      const per = this.hostsConfigPerPage || 50;
      this.hostsConfigPage = Math.floor(filteredIdx / per) + 1;
    }
    // Scroll the row's DOM node into view after Alpine renders the
    // page change. Falls back gracefully if the selector misses.
    this.$nextTick(() => {
      const sel = `[data-host-row-id="${row && row.id}"]`;
      const el = document.querySelector(sel);
      if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({behavior: 'smooth', block: 'center'});
      }
    });
  },
  hostsConfigSetPerPage(n) {
    const v = parseInt(n, 10);
    if (!Number.isFinite(v) || v < 1) {
      return;
    }
    this.hostsConfigPerPage = v;
    try {
      localStorage.setItem('hostsConfigPerPage', String(v));
    } catch {
    }
    // Clamp page to the new layout so a 100→25 switch from page 2
    // doesn't leave us on a stale page index.
    this.hostsConfigPage = Math.min(this.hostsConfigPage, this.hostsConfigTotalPages());
  },

  // Recompute the hostsConfig display-order snapshot. Called on
  // load / add / remove and on blur of the custom_number input
  // (see `@change` handler in the editor). Orders by custom_number
  // ascending; rows without a number sink to the bottom; id is
  // tiebreaker for determinism.
  rebuildHostsConfigOrder() {
    const cfg = this.hostsConfig || [];
    const idxs = cfg.map((_, idx) => idx);
    idxs.sort((a, b) => {
      const ca = parseInt(cfg[a].custom_number, 10);
      const cb = parseInt(cfg[b].custom_number, 10);
      const sa = Number.isFinite(ca) ? ca : Number.MAX_SAFE_INTEGER;
      const sb = Number.isFinite(cb) ? cb : Number.MAX_SAFE_INTEGER;
      if (sa !== sb) {
        return sa - sb;
      }
      return String(cfg[a].id || '').localeCompare(String(cfg[b].id || ''));
    });
    this.hostsConfigSortedOrder = idxs;
  },
  // Bulk "test every host" — fires testHostRow for each enabled
  // row in parallel. Skips rows without any provider mapping
  // (nothing to probe). Progress state (`hostsTestingAll`) drives
  // the toolbar button's spinner.
  // Export the curated Hosts list as a JSON file download. The
  // shape matches exactly what the importer consumes, so
  // round-tripping keeps every field (id / label / provider
  // mappings / url / icon / enabled) intact. Secrets aren't part
  // of this payload — Beszel/Pulse tokens are in Settings, not
  // per-host.
  exportHostsConfig() {
    const body = {
      version: 1,
      exported_at: new Date().toISOString(),
      hosts: this.hostsConfig || [],
    };
    const blob = new Blob([JSON.stringify(body, null, 2)],
      {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    a.href = url;
    a.download = `omnigrid-hosts-${stamp}.json`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(url);
      a.remove();
    }, 500);
    this.showToast(
      this.t('admin_hosts.exported_n', {count: (this.hostsConfig || []).length}),
      'success'
    );
  },

  // Import a hosts JSON file. Two strategies — ``merge`` keeps
  // existing rows and adds/updates by id; ``replace`` wipes the
  // current list. The operator picks via confirm() so neither
  // flow is a surprise. Files saved by exportHostsConfig round-
  // trip cleanly; hand-edited JSON that matches the same schema
  // also works.
  async importHostsConfig(evt) {
    const file = evt && evt.target && evt.target.files && evt.target.files[0];
    if (!file) {
      return;
    }
    evt.target.value = '';  // reset so the same file can re-trigger
    let payload;
    try {
      const text = await file.text();
      payload = JSON.parse(text);
    } catch (e) {
      this.showToast(this.t('admin_hosts.import_invalid_json', {error: e.message}), 'error');
      return;
    }
    const incoming = Array.isArray(payload.hosts) ? payload.hosts
      : (Array.isArray(payload) ? payload : []);
    if (!incoming.length) {
      this.showToast(this.t('admin_hosts.import.no_hosts_in_file'), 'error');
      return;
    }
    const existing = this.hostsConfig || [];
    let mode = 'merge';
    if (existing.length) {
      // SweetAlert with two buttons — OK = replace, Cancel = merge.
      // Replaces the original native confirm() so the dialog
      // theme-matches the dark surface tokens and i18n's
      //.
      const replace = await this.confirmDialog({
        title: this.t('admin_hosts.import_replace_confirm_title') || this.t('actions.confirm'),
        html: this.t('admin_hosts.import_replace_confirm_html', {existing: existing.length, incoming: incoming.length}),
        icon: 'warning',
        confirmText: this.t('admin_hosts.import_replace_confirm_replace'),
        confirmColor: this._cssVar('--danger'),
      });
      mode = replace ? 'replace' : 'merge';
    }
    const norm = (h) => {
      // HTTP probe sub-dict — preserve through import so a fleet-
      // export → fleet-import round-trip retains the per-host
      // enable + URL override + content_match + status codes +
      // verify_tls. Treat the input forgivingly (accept array or
      // CSV-string for status codes; arrays of strings for URLs).
      let httpProbe;
      if (h.http_probe && typeof h.http_probe === 'object') {
        httpProbe = {};
        if (h.http_probe.enabled === true) {
          httpProbe.enabled = true;
        }
        if (Array.isArray(h.http_probe.urls)) {
          const us = h.http_probe.urls
            .map(u => String(u || '').trim())
            .filter(u => /^https?:\/\//i.test(u));
          if (us.length) {
            httpProbe.urls = Array.from(new Set(us));
          }
        }
        if (typeof h.http_probe.content_match === 'string') {
          const cm = h.http_probe.content_match.trim();
          if (cm) {
            httpProbe.content_match = cm.slice(0, 256);
          }
        }
        if (Array.isArray(h.http_probe.accepted_status_codes)) {
          const cs = h.http_probe.accepted_status_codes
            .map(c => parseInt(c, 10))
            .filter(c => Number.isFinite(c) && c >= 100 && c <= 599);
          if (cs.length) {
            httpProbe.accepted_status_codes = Array.from(new Set(cs)).sort((a, b) => a - b);
          }
        } else if (typeof h.http_probe.accepted_status_codes === 'string' && h.http_probe.accepted_status_codes.trim()) {
          httpProbe.accepted_status_codes = h.http_probe.accepted_status_codes.trim();
        }
        if (h.http_probe.verify_tls === false) {
          httpProbe.verify_tls = false;
        }
      }
      const out = {
        id: String(h.id || h.name || '').trim(),
        label: String(h.label || '').trim() || String(h.id || h.name || ''),
        ne_url: String(h.ne_url || '').trim(),
        beszel_name: String(h.beszel_name || '').trim(),
        pulse_name: String(h.pulse_name || '').trim(),
        url: String(h.url || '').trim(),
        icon: String(h.icon || '').trim(),
        enabled: h.enabled !== false,
      };
      // Always stamp `http_probe: {}` even when the import file
      // carried no http_probe block — the per-host editor's textarea
      // binds `x-model="row.http_probe.urls_text"` and crashes if the
      // sub-dict is missing. Empty {} is functionally identical to
      // missing for the save round-trip (saveHostsConfig only emits
      // http_probe keys for explicit overrides).
      if (httpProbe && Object.keys(httpProbe).length) {
        out.http_probe = httpProbe;
      } else {
        out.http_probe = {};
      }
      return out;
    };
    const cleanIncoming = incoming.map(norm).filter(h => h.id);
    if (mode === 'replace') {
      this.hostsConfig = cleanIncoming;
    } else {
      const byId = {};
      for (const row of existing) {
        byId[row.id] = row;
      }
      for (const row of cleanIncoming) {
        byId[row.id] = row;
      }  // overwrite on id collision
      this.hostsConfig = Object.values(byId);
    }
    // Invalidate filtered-list cache — array identity swap
    // doesn't move the cache key.
    this._filteredHostsConfigCache.key = '';
    this._filteredHostsConfigCache.value = null;
    this.hostsConfigDirty = true;
    this.showToast(
      this.t('admin_hosts.imported_n', {count: cleanIncoming.length}),
      'success'
    );
  },

  async testAllHostRows() {
    // Eligibility uses the canonical `rowHasProviderMapping(row)`
    // helper so the bulk-test set matches the per-row "Test
    // providers" button's enable rule. Pre-fix the filter only
    // accepted Beszel / Pulse / NE rows — SNMP-only switches /
    // Webmin-only Linux boxes / Ping-only routers were silently
    // excluded from "Test all" even though their per-row Test
    // button worked. Now any of the six provider mappings (Beszel
    // / Pulse / NE / Webmin / Ping / SNMP) qualifies.
    const rows = (this.hostsConfig || [])
      .map((row, idx) => ({row, idx}))
      .filter(({row}) => row.enabled !== false && this.rowHasProviderMapping(row));
    if (!rows.length) {
      this.showToast(this.t('admin_hosts.test.no_eligible'), 'error');
      return;
    }
    this.hostsTestingAll = true;
    // Auto-expand every row about to be tested so the per-row
    // result strip (✓/✗ icons + reason text) is visible without
    // the operator having to manually expand each row after the
    // bulk test fires. Mirrors the per-host Test workflow where
    // the operator is already on the row's expanded body when
    // they click Test. Pre-fix: results landed in
    // `hostsTestResults[idx]` correctly but rows that were
    // collapsed never showed the strip — operator saw only the
    // aggregate "Tested N rows" toast with no per-row detail.
    const next = {...(this.hostsConfigExpanded || {})};
    for (const {row} of rows) {
      if (row && row._uid) {
        next[row._uid] = true;
      }
    }
    this.hostsConfigExpanded = next;
    try {
      await Promise.all(rows.map(({idx}) => this.testHostRow(idx)));
      this.showToast(this.t('admin_hosts.test.tested_n', {count: rows.length}), 'success');
    } finally {
      this.hostsTestingAll = false;
    }
  },
  async testHostRow(idx) {
    const row = this.hostsConfig[idx];
    if (!row) {
      return;
    }
    this.hostsTestResults = {
      ...this.hostsTestResults,
      [idx]: {pending: true},
    };
    try {
      // Forward the row's per-host SNMP overrides so the test
      // probe runs under the SAME config the live probe / sampler
      // would use. Without this an iDRAC row with an explicit
      // walk_concurrency=4 + vendors=["dell"] override still tested
      // at the safety-floor concurrency=1 + walk-all default,
      // surfacing as "Test failed: HTTP 504" because the full
      // 67-OID walk exceeded NPM's proxy_read_timeout. Backend
      // honours these or falls through to globals when blank.
      const snmp = (row.snmp || {});
      const r = await fetch('/api/hosts/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          beszel_name: (row.beszel_name || '').trim(),
          pulse_name: (row.pulse_name || '').trim(),
          ne_url: (row.ne_url || '').trim(),
          webmin_url: (row.webmin_url || '').trim(),
          // SNMP + Ping forwarded to /api/hosts/test so the
          // per-row test reflects the SAME providers the live probe
          // chain runs. Without this, SNMP-only rows reported "all
          // skipped" + ping-only rows skipped their reachability check.
          // `snmp_target` carries the resolver-chain result so the
          // backend hits the same target the live sampler /
          // _merge_one_host would (snmp_name → address fallback).
          // `snmp_name` is kept for legacy back-compat on the
          // backend's body parsing.
          snmp_name: (row.snmp_name || '').trim(),
          snmp_target: ((row.snmp_name || '').trim() || (row.address || '').trim()),
          // Send the row's IN-FORM address so the backend's resolver
          // fallback uses what's currently in the editor — operators
          // editing a row may have changed `address` without saving;
          // the test should reflect what's about to be saved, not
          // the stale persisted value.
          address: (row.address || '').trim(),
          // Per-host SNMP overrides (community / version / port /
          // v3 / walk_concurrency / vendors / wall_clock_budget).
          // Each is forwarded only when set so blanks fall through
          // to the global defaults server-side.
          snmp_community: (snmp.community || '').trim(),
          snmp_version: (snmp.version || '').trim(),
          snmp_port: snmp.port || 0,
          snmp_v3_user: (snmp.v3_user || '').trim(),
          snmp_v3_auth_key: (snmp.v3_auth_key || '').trim(),
          snmp_v3_priv_key: (snmp.v3_priv_key || '').trim(),
          snmp_walk_concurrency: snmp.walk_concurrency || 0,
          snmp_wall_clock_budget: snmp.wall_clock_budget || 0,
          snmp_vendors: Array.isArray(snmp.vendors) ? snmp.vendors : [],
          ping_enabled: !!(row.ping && row.ping.enabled),
          // HTTP probe — same per-row opt-in shape as ping/snmp.
          // Backend resolves the URL list server-side (operator
          // override → top-level url + services[].url fallback)
          // so the SPA only forwards the enable flag + the optional
          // override list when explicitly set.
          http_probe_enabled: !!(row.http_probe && row.http_probe.enabled === true),
          // Resolve URLs from the in-flight textarea content first so
          // the test reflects what the operator typed, NOT the
          // last-saved array. Mirrors the save-time serialization in
          // `saveHostsConfig` (split-on-newline → trim → drop
          // non-http(s) → Set-dedup). When `urls_text` was never
          // rendered (load-then-test without textarea interaction)
          // fall back to the persisted `urls` array so a save-then-
          // test round-trip stays correct.
          http_probe_urls: (function () {
            const hp = row.http_probe || {};
            if (typeof hp.urls_text === 'string') {
              const out = [];
              const seen = new Set();
              for (const raw of hp.urls_text.split(/\r?\n/)) {
                const s = String(raw || '').trim();
                const sl = s.toLowerCase();
                if (s && (sl.startsWith('http://') || sl.startsWith('https://')) && !seen.has(s)) {
                  seen.add(s);
                  out.push(s);
                }
              }
              return out;
            }
            return Array.isArray(hp.urls) ? hp.urls : [];
          })(),
          // Per-row TLS verification override — operator unticks
          // this for self-signed homelab certs. Forwarded so the
          // test path honours the same verify flag the sampler
          // uses. Default true; explicit false opts into
          // self-signed-cert acceptance.
          http_probe_verify_tls: (row.http_probe
            && row.http_probe.verify_tls === false)
            ? false : true,
          host_id: (row.id || '').trim(),
        }),
      });
      if (!r.ok) {
        this.hostsTestResults[idx] = {pending: false, error: `HTTP ${r.status}`};
        return;
      }
      const d = await r.json();
      this.hostsTestResults[idx] = {pending: false, ...d};
    } catch (e) {
      this.hostsTestResults[idx] = {pending: false, error: e.message};
    }
  },

  addHostRow() {
    // Pre-fill custom_number with the next unused integer so a fresh
    // row slots into the catalogue sequence without manual thought.
    // Operator can still overwrite / clear it.
    const existing = (this.hostsConfig || [])
      .map(r => parseInt(r.custom_number, 10))
      .filter(n => Number.isFinite(n) && n > 0);
    const nextNum = existing.length ? Math.max(...existing) + 1 : 1;
    this.hostsConfig.push({
      id: '',
      label: '',
      custom_number: nextNum,
      ne_url: '',
      beszel_name: '',
      pulse_name: '',
      webmin_name: '',
      webmin_url: '',
      // SNMP target alias. Blank = no SNMP for this host.
      snmp_name: '',
      snmp: {},
      url: '',
      icon: '',
      // Free-text IP field — operator-maintained, not derived.
      ip: '',
      // Per-host SSH overrides — empty object = use global defaults.
      ssh: {},
      // Per-host ping opt-in. Default OFF; operator flips to
      // probe this host. Empty object = use global defaults
      // (ping_default_port + ping_use_icmp).
      ping: {},
      // Per-host HTTP-probe sub-dict. Default empty; loadHostsConfig
      // hydration block defensively stamps `urls_text: ''` on every
      // existing row, but fresh rows added here need the object so
      // the textarea's `x-model="row.http_probe.urls_text"` doesn't
      // read from `undefined.urls_text` when the operator ticks the
      // http_probe enable checkbox.
      http_probe: {},
      enabled: true,
      // Stable identity for x-for keying (matches the loadHostsConfig
      // hydration path).
      _uid: this._mintRowUid(),
    });
    this.hostsConfigDirty = true;
    this.rebuildHostsConfigOrder();
    // Jump to whichever page the new row landed on. After the
    // sort, the new row's display position depends on its
    // custom_number; without this jump, an "+ Add" on page 1
    // could push focus to a row only visible on page 4.
    this.$nextTick(() => {
      const newUid = this.hostsConfig[this.hostsConfig.length - 1]._uid;
      const all = this.filteredHostsConfig();
      const pos = all.findIndex(({row}) => row._uid === newUid);
      if (pos >= 0) {
        const per = this.hostsConfigPerPage || 50;
        this.hostsConfigGoToPage(Math.floor(pos / per) + 1);
      }
    });
    // Scroll to the new (last) host card so the operator sees it
    // immediately — useful when the editor list is long enough to
    // require scrolling. A short delay lets Alpine finish rendering
    // the new row before we try to find + scroll to it.
    this.$nextTick(() => {
      setTimeout(() => {
        const cards = document.querySelectorAll('[data-host-card]');
        const last = cards[cards.length - 1];
        if (last && typeof last.scrollIntoView === 'function') {
          last.scrollIntoView({behavior: 'smooth', block: 'center'});
          // Focus the ID input for immediate typing.
          const idInput = last.querySelector('input[placeholder*="host01"], input[placeholder*="example"]');
          if (idInput && typeof idInput.focus === 'function') {
            idInput.focus();
          }
        }
      }, 50);
    });
  },
  removeHostRow(idx) {
    this.hostsConfig.splice(idx, 1);
    this.rebuildHostsConfigOrder();
    this.hostsConfigDirty = true;
    // Clamp page after delete — removing the last row on page N
    // would otherwise leave the operator on an empty page.
    this.hostsConfigPage = Math.min(this.hostsConfigPage, this.hostsConfigTotalPages());
  },
  // Manual reorder — simpler than drag-and-drop and works on
  // touch. Wraps around at the ends so the buttons stay useful
  // when a row is already top/bottom (no-op there, so we guard
  // instead of wrapping to avoid surprise). Clears any per-index
  // test result since the row's idx just moved.
  moveHostRow(idx, delta) {
    const dest = idx + delta;
    if (dest < 0 || dest >= this.hostsConfig.length) {
      return;
    }
    const [row] = this.hostsConfig.splice(idx, 1);
    this.hostsConfig.splice(dest, 0, row);
    this.hostsTestResults = {};
    this.hostsConfigDirty = true;
  },
  // Clone an existing host row — preserves every field except the
  // ID (prefixed with "copy-of-" to satisfy the "unique id"
  // constraint server-side) and the enabled flag (off by default
  // so the clone doesn't silently start pulling data before the
  // operator renames it). Inserted right below the source row so
  // the visual relationship is obvious.
  duplicateHostRow(idx) {
    const src = this.hostsConfig[idx];
    if (!src) {
      return;
    }
    const copy = {
      ...src,
      id: (src.id ? 'copy-of-' + src.id : ''),
      label: (src.label ? src.label + ' (copy)' : ''),
      enabled: false,
    };
    this.hostsConfig.splice(idx + 1, 0, copy);
    this.hostsTestResults = {};
    this.hostsConfigDirty = true;
  },
  async saveHostsConfig() {
    // Clear any inline errors from a prior save attempt before
    // re-validating. makes these per-field (red border +
    // error text beneath the bad input) instead of a toast.
    this.clearFieldErrorsByPrefix('host_');

    let hadError = false;
    const ensureExpanded = (id) => {
      // Expand the row carrying this id so the error-decorated
      // field is visible. Looks up the row by id to find its
      // stable `_uid` (the actual key in hostsConfigExpanded
      // since the typing-collapse fix).
      if (!id) {
        return;
      }
      const row = (this.hostsConfig || []).find(r => r && r.id === id);
      const uid = row && row._uid;
      if (uid && !this.hostsConfigExpanded[uid]) {
        this.hostsConfigExpanded = {...this.hostsConfigExpanded, [uid]: true};
      }
    };

    // Pre-save validation: if a Webmin URL is set, the webmin_name
    // must also be set. Otherwise the probe has a target but no key
    // to look the returned host up against — would silently produce
    // empty drawer cards.
    for (let i = 0; i < (this.hostsConfig || []).length; i++) {
      const h = this.hostsConfig[i] || {};
      const wurl = (h.webmin_url || '').trim();
      const wname = (h.webmin_name || '').trim();
      if (wurl && !wname) {
        this.setFieldError('host_' + i + '_webmin_name',
          this.t('toasts_extra.webmin_url_without_name_inline'));
        ensureExpanded(h.id);
        hadError = true;
      }
    }
    // Empty id + other data — surface on the id input itself.
    for (let i = 0; i < (this.hostsConfig || []).length; i++) {
      const h = this.hostsConfig[i] || {};
      if ((h.id || '').trim() !== '') {
        continue;
      }
      const hasOtherData = (
        (h.label || '').trim() ||
        (h.ne_url || '').trim() ||
        (h.beszel_name || '').trim() ||
        (h.pulse_name || '').trim() ||
        (h.webmin_name || '').trim() ||
        (h.webmin_url || '').trim() ||
        (h.url || '').trim() ||
        (h.icon || '').trim()
      );
      if (hasOtherData) {
        this.setFieldError('host_' + i + '_id',
          this.t('toasts_extra.id_required_inline'));
        hadError = true;
      }
    }
    // Duplicate custom_number — tag every offending row, not just
    // one. Operator can see the whole collision group at a glance.
    const byCn = new Map();
    (this.hostsConfig || []).forEach((h, i) => {
      const cn = parseInt(h.custom_number, 10);
      if (!Number.isFinite(cn)) {
        return;
      }
      if (!byCn.has(cn)) {
        byCn.set(cn, []);
      }
      byCn.get(cn).push(i);
    });
    for (const [cn, idxs] of byCn.entries()) {
      if (idxs.length < 2) {
        continue;
      }
      for (const i of idxs) {
        this.setFieldError('host_' + i + '_cn',
          this.t('toasts_extra.custom_number_duplicate_inline', {cn}));
        ensureExpanded((this.hostsConfig[i] || {}).id);
      }
      hadError = true;
    }

    if (hadError) {
      this.focusFirstFieldError();
      return;
    }
    // Strip empty rows (no ID) so saving doesn't persist placeholder
    // blanks. The server dedupes by ID in case the same one was
    // typed twice.
    const clean = (this.hostsConfig || []).filter(
      h => (h.id || '').trim() !== '',
    ).map(h => {
      // custom_number: accept integer, numeric string, or blank.
      // Blank / non-numeric → null so the backend stores no value
      // (rows without a number sort last in the "Custom #" view).
      const rawNum = h.custom_number;
      let num = null;
      if (rawNum !== '' && rawNum !== null && rawNum !== undefined) {
        const parsed = parseInt(rawNum, 10);
        if (Number.isFinite(parsed)) {
          num = parsed;
        }
      }
      // Per-host SSH — strip falsy / blank keys so the DB doesn't
      // persist empty strings that would shadow the global default.
      // `fqdn` is the new preferred key; `host` stays for back-compat
      // (the backend's _clean_host_ssh accepts both and the resolver
      // reads `fqdn` first, then `host`).
      const sshIn = h.ssh || {};
      const sshOut = {};
      if ((sshIn.user || '').trim()) {
        sshOut.user = sshIn.user.trim();
      }
      if ((sshIn.fqdn || '').trim()) {
        sshOut.fqdn = sshIn.fqdn.trim();
      }
      if ((sshIn.host || '').trim()) {
        sshOut.host = sshIn.host.trim();
      }
      if (sshIn.port) {
        const p = parseInt(sshIn.port, 10);
        if (Number.isFinite(p) && p >= 1 && p <= 65535) {
          sshOut.port = p;
        }
      }
      // Passwords are write-only — any non-empty string overwrites.
      // Empty = "no override" (fall back to global default password).
      if (typeof sshIn.password === 'string' && sshIn.password !== '') {
        sshOut.password = sshIn.password;
      }
      // Per-host SSH is OPT-IN as of only the explicit
      // `enabled: true` flag survives the round-trip. Absence (or
      // `enabled: false`) means SSH is OFF for the host.
      //
      // **DO NOT add a legacy `disabled=true` fallback here** :
      // this `norm()` runs on EVERY save, not just at import time.
      // A defensive `else if (sshIn.disabled !== true) sshOut.enabled
      // = true` would auto-enable every row that lacks an explicit
      // flag — which is the exact symptom of "enabling host A
      // re-enables host C that was previously disabled". The schema
      // migration in `logic/migrations.py:_migration_001` handles
      // legacy `disabled` → `enabled` ONCE on first boot post-fix;
      // re-applying that conversion per-save corrupts subsequent
      // operator edits.
      if (sshIn.enabled === true) {
        sshOut.enabled = true;
      }
      // Per-host ping. Same shape contract as ssh — strip
      // falsy / blank keys so empty strings don't poison the merge.
      const pingIn = h.ping || {};
      const pingOut = {};
      if (pingIn.enabled) {
        pingOut.enabled = true;
      }
      if (pingIn.port) {
        const pp = parseInt(pingIn.port, 10);
        if (Number.isFinite(pp) && pp >= 1 && pp <= 65535) {
          pingOut.port = pp;
        }
      }
      const pt = String(pingIn.transport || '').trim().toLowerCase();
      if (pt === 'tcp' || pt === 'icmp') {
        pingOut.transport = pt;
      }
      // Per-host SNMP override. Same strip-blanks pattern as
      // ssh / ping — every key falls back to the global default when
      // empty, so we only persist explicit overrides.
      const snmpIn = h.snmp || {};
      const snmpOut = {};
      // explicit opt-IN. Persist `enabled: true` only when
      // the operator checked the box; drop the field otherwise so
      // the persisted JSON stays tight. Backend's _clean_host_snmp
      // mirrors this contract (only persists when raw value is
      // explicitly truthy) and _merge_one_host gates the probe on
      // `enabled is True` (no default-true fallback).
      if (snmpIn.enabled === true) {
        snmpOut.enabled = true;
      }
      const sc = String(snmpIn.community || '').trim();
      if (sc) {
        snmpOut.community = sc;
      }
      const sv = String(snmpIn.version || '').trim().toLowerCase();
      if (sv === 'v2c' || sv === 'v3') {
        snmpOut.version = sv;
      }
      if (snmpIn.port) {
        const sp = parseInt(snmpIn.port, 10);
        if (Number.isFinite(sp) && sp >= 1 && sp <= 65535) {
          snmpOut.port = sp;
        }
      }
      for (const k of ['v3_user', 'v3_auth_key', 'v3_priv_key']) {
        const sval = String(snmpIn[k] || '').trim();
        if (sval) {
          snmpOut[k] = sval;
        }
      }
      // Per-host SNMP walk_concurrency override. Server-class BMCs
      // (Dell iDRAC, Cisco IMC, Supermicro IPMI) handle parallel
      // queries fine and need > 1 to fit pysnmp's per-walk overhead
      // inside the probe budget. Blank / missing → fall through to
      // the global tunable default. Range 1..16 mirrors the
      // backend's _clean_host_snmp validator AND the global
      // tuning_snmp_per_host_walk_concurrency bounds.
      if (snmpIn.walk_concurrency) {
        const wc = parseInt(snmpIn.walk_concurrency, 10);
        if (Number.isFinite(wc) && wc >= 1 && wc <= 16) {
          snmpOut.walk_concurrency = wc;
        }
      }
      // Per-host wall_clock_budget override. Same shape as
      // walk_concurrency — overrides
      // tuning_snmp_wall_clock_budget_seconds when supplied.
      // Range 5..600 mirrors the global tunable's bounds.
      if (snmpIn.wall_clock_budget) {
        const wcb = parseInt(snmpIn.wall_clock_budget, 10);
        if (Number.isFinite(wcb) && wcb >= 5 && wcb <= 600) {
          snmpOut.wall_clock_budget = wcb;
        }
      }
      // Per-host vendor MIB selector. Operator-declared list of
      // vendor MIBs to walk for THIS host (subset of the canonical
      // vendor key set sourced from /api/me's
      // client_config.snmp_vendor_keys — single source of truth at
      // logic/snmp.py:_VALID_VENDOR_KEYS). Empty / missing = auto-
      // detect from sysDescr (the common case). Useful for agents
      // with stripped sysDescr or to force a vendor's walks even
      // when auto-detect would skip them.
      if (Array.isArray(snmpIn.vendors)) {
        const validSet = new Set(this.snmpVendorKeys());
        const cleanVendors = Array.from(new Set(
          snmpIn.vendors
            .map(v => String(v || '').trim().toLowerCase())
            .filter(v => validSet.has(v))
        )).sort();
        if (cleanVendors.length) {
          snmpOut.vendors = cleanVendors;
        }
      }
      // Per-host mount-exclusion list. Operator-supplied paths to
      // drop from the SNMP storage extractor output — covers
      // device-specific phantoms (dd-wrt's `/opt` reporting
      // 232 GB on a 16 MB router) on top of the universal
      // pseudo-fs prefixes that the backend filters by default.
      // Editor renders one entry per line in a textarea; we
      // split + trim + dedupe + cap at 32 entries here.
      if (typeof snmpIn.exclude_mounts_text === 'string') {
        const lines = snmpIn.exclude_mounts_text
          .split(/\r?\n|,/)
          .map(s => s.trim())
          .filter(s => s.length > 0);
        const cleanExcl = Array.from(new Set(lines)).slice(0, 32);
        if (cleanExcl.length) {
          snmpOut.exclude_mounts = cleanExcl;
        }
      } else if (Array.isArray(snmpIn.exclude_mounts)) {
        const cleanExcl = Array.from(new Set(
          snmpIn.exclude_mounts
            .map(s => String(s || '').trim())
            .filter(s => s.length > 0)
        )).slice(0, 32);
        if (cleanExcl.length) {
          snmpOut.exclude_mounts = cleanExcl;
        }
      }
      // Per-host HTTP probe override. Same strip-blanks pattern as
      // ssh / ping / snmp — every key falls back to the default when
      // empty, so we only persist explicit overrides. URLs go in as
      // an array of trimmed http(s) URLs; non-http(s) entries are
      // dropped silently (mirrors backend `_clean_host_http_probe`).
      const httpIn = h.http_probe || {};
      const httpOut = {};
      if (httpIn.enabled === true) {
        httpOut.enabled = true;
      }
      // URLs source-of-truth: the textarea-bound `urls_text` string
      // (one URL per line — see the load-time hydration block above
      // for why the editor uses a virtual string field instead of
      // round-tripping the array on every keystroke). Gate on
      // `typeof === 'string'` (NOT `.length > 0`) so that an
      // operator who CLEARS the textarea (intent: "stop probing —
      // I removed every URL") actually drops the persisted URLs
      // on Save. Only fall back to the persisted `urls` array on
      // a load-then-save round-trip where the textarea was never
      // rendered (no string value at all) — that path hits the
      // legacy array shape directly without going through the
      // textarea reactive scope. The hydrate block above stamps
      // `urls_text` from the array on first render, so the typeof
      // gate works on load-then-clear-then-save flows.
      const _httpUrlSource = (typeof httpIn.urls_text === 'string')
        ? httpIn.urls_text.split(/\r?\n/).map(s => s.trim())
        : (Array.isArray(httpIn.urls) ? httpIn.urls : []);
      const cleanUrls = [];
      for (const u of _httpUrlSource) {
        const s = String(u || '').trim();
        const sl = s.toLowerCase();
        if (s && (sl.startsWith('http://') || sl.startsWith('https://'))) {
          cleanUrls.push(s);
        }
      }
      if (cleanUrls.length) {
        // Dedupe inside the SPA so the backend doesn't have to.
        httpOut.urls = Array.from(new Set(cleanUrls));
      }
      const cm = String(httpIn.content_match || '').trim();
      if (cm && cm.length <= 256) {
        httpOut.content_match = cm;
      }
      // accepted_status_codes: accept array (already parsed) OR CSV
      // string. The backend's `parse_status_codes_csv` accepts both,
      // so we just trim and pass through when non-empty.
      if (Array.isArray(httpIn.accepted_status_codes)) {
        const codes = httpIn.accepted_status_codes
          .map(c => parseInt(c, 10))
          .filter(c => Number.isFinite(c) && c >= 100 && c <= 599);
        if (codes.length) {
          httpOut.accepted_status_codes = Array.from(new Set(codes)).sort((a, b) => a - b);
        }
      } else if (typeof httpIn.accepted_status_codes === 'string' && httpIn.accepted_status_codes.trim()) {
        // Operator typed a CSV — let backend parse.
        httpOut.accepted_status_codes = httpIn.accepted_status_codes.trim();
      }
      // verify_tls: explicit boolean only when the operator unticked
      // (defaults to true at the backend). Persisting `true`
      // explicitly keeps the JSON tight without sacrificing the
      // round-trip — a missing field reads as default-true downstream.
      if (httpIn.verify_tls === false) {
        httpOut.verify_tls = false;
      }
      // host-level enable gates every per-provider enable.
      // A disabled host cannot have any provider enabled. Strip
      // each per-provider `enabled` flag here so reload comes back
      // consistent (no stale `ssh.enabled: true` on a row whose main
      // checkbox is off). Backend `_clean_host_*` validators mirror
      // this defence-in-depth contract — see _save_hosts_config.
      if (h.enabled === false) {
        delete sshOut.enabled;
        delete pingOut.enabled;
        delete snmpOut.enabled;
        delete httpOut.enabled;
      }
      return {
        id: (h.id || '').trim(),
        // Empty label is INTENTIONAL — `hostDisplayName(h)` falls
        // back to `assetForHost(h).name`. Don't auto-fill
        // with id here, because that would PIN the id forever and
        // the operator's intent to "use the asset's name" would be
        // silently dropped on every save.
        label: (h.label || '').trim(),
        custom_number: num,
        ne_url: (h.ne_url || '').trim(),
        beszel_name: (h.beszel_name || '').trim(),
        pulse_name: (h.pulse_name || '').trim(),
        webmin_name: (h.webmin_name || '').trim(),
        // SNMP target alias. Empty means "no SNMP for this
        // host". Backend's _clean_host_snmp validates the override
        // dict; bare snmp_name flows through as a string.
        snmp_name: (h.snmp_name || '').trim(),
        snmp: snmpOut,
        url: (h.url || '').trim(),
        icon: (h.icon || '').trim(),
        // Dedicated probe target — hostname OR IP. Used as the
        // default by port-scan / ping / SNMP / SSH when no
        // provider-specific override is set. Independent of any
        // provider so disabling SNMP / ping / SSH never leaves the
        // other probes without a target. Backend caps at 64 chars.
        address: (h.address || '').trim(),
        ssh: sshOut,
        ping: pingOut,
        http_probe: httpOut,
        enabled: h.enabled !== false,
      };
    });
    // Derive webmin_aliases from each row's webmin_url. Single
    // source of truth in the editor — operator types the URL on
    // the row, we sync it into settings.webmin_aliases on save.
    const webminAliases = {};
    for (const h of (this.hostsConfig || [])) {
      const id = (h.id || '').trim();
      const url = (h.webmin_url || '').trim().replace(/\/$/, '');
      if (id && url) {
        webminAliases[id] = url;
      }
    }
    this.hostsConfigSaving = true;
    try {
      const r = await fetch('/api/hosts/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({hosts: clean}),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      const d = await r.json();
      // Capture the previous id→_uid map BEFORE replacing the
      // array. Server-side rows don't carry `_uid` (it's a
      // client-only key), so a naive replace would strip every
      // row's stable identity → `hostsConfigExpanded` keys go
      // stale → chevron clicks early-exit at `!row._uid` →
      // operator can't expand any row. Preserving the existing
      // uids by id keeps the expansion state alive across saves.
      // The same fix protects fresh `+Add` rows (their _uid was
      // minted in `addHostRow`) from being clobbered the moment
      // the operator hits Save.
      const oldUidById = {};
      for (const r of (this.hostsConfig || [])) {
        if (r && r.id && r._uid) {
          oldUidById[r.id] = r._uid;
        }
      }
      this.hostsConfig = d.hosts || [];
      // Invalidate the filtered-list cache: the
      // cache key is `filter + length + order.length`, which DOESN'T
      // change when the array elements are replaced via `=`. Without
      // the explicit reset, `pagedHostsConfig()` keeps returning the
      // pre-save `{row, idx}` objects whose `row` references point
      // at the OLD array's elements — bindings like the SSH icon's
      // `row.ssh && row.ssh.enabled` read STALE state until a hard
      // refresh rebuilds the cache. Mutate the cache field in place
      // (don't reassign — Alpine's reactivity tracks the existing
      // proxy slot) so the next `filteredHostsConfig()` call sees
      // the empty key and rebuilds against the fresh array.
      this._filteredHostsConfigCache.key = '';
      this._filteredHostsConfigCache.value = null;
      // Re-stamp each row with its webmin_url (the hosts_config
      // endpoint doesn't know about aliases, so the field is
      // load-bearing for the editor UI only) AND its _uid (re-use
      // the previous one when the id matches; mint fresh otherwise).
      for (const row of this.hostsConfig) {
        row.webmin_url = webminAliases[row.id] || '';
        if (!row.ssh || typeof row.ssh !== 'object') {
          row.ssh = {};
        }
        row._uid = oldUidById[row.id] || this._mintRowUid();
      }
      this.rebuildHostsConfigOrder();
      // Persist the derived webmin_aliases in settings so the probe
      // pipeline can read it next gather.
      await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({webmin_aliases: webminAliases}),
      }).catch(() => {
      });
      if (this.settings) {
        this.settings.webmin_aliases = webminAliases;
      }
      this.hostsConfigDirty = false;
      this.showToast(this.t('admin_hosts.saved_n', {count: d.count}), 'success');
      // Refresh the Hosts tab data unconditionally — was previously
      // gated on ``this.view === 'hosts'`` so an Admin → Hosts save
      // followed by switching to the Hosts page rendered with a
      // STALE `this.hosts` (e.g. ping_enabled flipped to true in the
      // DB but `hostHasAgent(h)` still saw the pre-save snapshot, so
      // "Hide hosts without agents" hid the row until the next 15s
      // polling tick reconciled). The fetch is cheap; running it
      // regardless of current view keeps the per-host provider /
      // ping_enabled state consistent across the whole SPA. ``force=true``
      // busts the backend's 10s provider-state cache so the new
      // aliases / SSH config produce a fresh probe immediately.
      this.loadHosts(true);
    } catch (e) {
      this.showToast(this.t('admin_hosts.save_failed', {error: e.message}), 'error');
    } finally {
      this.hostsConfigSaving = false;
    }
  },
  // Count of curated hosts whose CONFIG maps them to a given
  // provider, regardless of whether the latest probe returned data.
  // Used by `hostsProviderState` as the visibility gate so the chip
  // surfaces an outage (red ✗) even when every probe is currently
  // failing — but stays hidden when no curated row has been
  // configured for the provider at all. Mapping predicates mirror
  // `providerStates(h)` exactly so the toolbar chip and the per-row
  // chip share one source of truth for "is this host configured
  // for provider X".
  _hostsConfiguredForProvider(name) {
    const list = this.hosts || [];
    const trim = (v) => (v && String(v).trim()) || '';
    if (name === 'beszel') {
      return list.filter(h => trim(h.beszel_name)).length;
    }
    if (name === 'pulse') {
      return list.filter(h => trim(h.pulse_name)).length;
    }
    if (name === 'node_exporter') {
      return list.filter(h => trim(h.ne_url)).length;
    }
    if (name === 'webmin') {
      return list.filter(h => trim(h.webmin_name)).length;
    }
    if (name === 'ping') {
      return list.filter(h => h.ping_enabled === true).length;
    }
    if (name === 'snmp') {
      return list.filter(h => h.snmp_enabled === true
        && (trim(h.snmp_name) || trim(h.address))).length;
    }
    if (name === 'http_probe') {
      // Mirrors `providerStates(h)`'s http_probe gate exactly so the
      // toolbar chip's configured-count matches the per-row chip
      // visibility. `http_probe_has_targets` is the backend-stamped
      // boolean that resolves the same URL chain the sampler uses
      // (http_probe.urls → row.url → row.services[].url).
      return list.filter(h => h.http_probe_enabled === true
        && h.http_probe_has_targets).length;
    }
    return 0;
  },
  /** Per-row Test button handler — fires POST /api/hosts/{id}/http-probe/test
   * and stamps the result into `httpProbeRowTestResult[host.id]` for
   * inline display. Busy flag prevents double-firing. Failure path
   * surfaces the error via the result cache so the operator sees
   * what went wrong without needing to open the console.
   */
  async testHostHttpProbe(h) {
    if (!h || !h.id || this.httpProbeRowTestBusy[h.id]) {
      return;
    }
    this.httpProbeRowTestBusy[h.id] = true;
    this.httpProbeRowTestResult[h.id] = {pending: true, results: [], error: null};
    try {
      const resp = await fetch('/api/hosts/' + encodeURIComponent(h.id) + '/http-probe/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: '{}',
      });
      const j = await resp.json().catch(() => ({}));
      this.httpProbeRowTestResult[h.id] = {
        pending: false,
        results: Array.isArray(j && j.results) ? j.results : [],
        elapsed_ms: (j && j.elapsed_ms) || 0,
        error: (j && j.error) || (!resp.ok ? ('HTTP ' + resp.status) : null),
      };
    } catch (e) {
      this.httpProbeRowTestResult[h.id] = {
        pending: false,
        results: [],
        error: String(e && e.message || e),
      };
    } finally {
      this.httpProbeRowTestBusy[h.id] = false;
    }
  },
};
