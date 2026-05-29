// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS,AnonymousCapturingGroupJS,RegExpAnonymousGroup
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,JSAsyncFunctionMissingAwait,JSMissingAwait
// noinspection NegatedConditionalExpressionJS,JSNegatedConditionalExpression,JSIfStatementsCanBeSimplified,IfStatementSimplifyable,RedundantIfStatementJS
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSPotentiallyInvalidUsageOfThis,RegExpRedundantEscape,JSDeprecatedSymbols,VoidExpressionJS,JSVoidExpression
// noinspection RedundantLocalVariableJS,JSPossiblyAssignedToNullVariable,JSObjectNullOrUndefined,JSReusedLocalVariable,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML
// noinspection HtmlUnknownTag,HtmlEmptyContent,HtmlEmptyTagsRecommendation,InnerHTMLJS
/* global Alpine, Swal, I18N, t, OG_VERSION */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA AI Integration — ADMIN tab surface only (provider config,
// dashboard, memory pane, settings form).
//
// SPLIT FROM `app-ai.js`: that file was 4250 lines covering AI
// sidebar + palette dispatch + admin tab. The admin block
// (`aiProviderNames` through the AI section save / memory CRUD)
// extracted here keeps the sidebar / palette chunk under the
// "uncomfortable to navigate" threshold. Both sub-modules merge
// into the same Alpine component via `_mergeKeepDescriptors` in
// `app.js`, so cross-method references (`this.X`) keep working
// without any binding gymnastics.

export default {
  // ---------------------------------------------------------------
  // AI integration (Stage 1 foundation) — provider config + dashboard.
  // No actual AI calls are made yet; this is the surface that future
  // stages will write into via `logic/ai.py`.
  // ---------------------------------------------------------------
  // Defensive fallback — overwritten from `/api/me`'s
  // `client_config.ai.provider_names` (canonical
  // `logic.ai.SUPPORTED_PROVIDERS`) the moment that response
  // resolves. If `loadMe()` has just kicked off and Alpine
  // bindings render before the response lands, every consumer
  // (provider grid, settings form, active-provider dropdown)
  // sees this literal and renders cleanly. Don't rely on this
  // literal as the source of truth — edit the backend tuple.
  aiProviderNames: ['claude', 'gemini', 'chatgpt', 'deepseek'],
  aiProviderDisplayName(name) {
    // Brand-stable casing for the four providers; unknown names get
    // a capitalisation fallback so a future provider rendered before
    // the i18n bundle catches up still reads correctly.
    const known = {
      claude: 'Claude',
      gemini: 'Gemini',
      chatgpt: 'ChatGPT',
      deepseek: 'DeepSeek',
    };
    const k = String(name || '').toLowerCase();
    if (known[k]) {
      return known[k];
    }
    return k.charAt(0).toUpperCase() + k.slice(1);
  },
  aiProviderModelPlaceholder(name) {
    // Defaults are sourced from the backend's `ai.defaults` block
    // (api_get_settings) so a future endpoint rotation lands in
    // ONE place. Local fallback string applies until the first
    // settings GET resolves.
    return ((this.aiDefaults || {})[name] || {}).model || '';
  },
  aiProviderModelHint(name) {
    const key = `admin.ai.model_hint_${name}`;
    const translated = this.t(key);
    if (translated && translated !== key) {
      return translated;
    }
    return this.t('admin.ai.model_hint_generic');
  },
  aiProviderBaseUrlPlaceholder(name) {
    return ((this.aiDefaults || {})[name] || {}).base_url || '';
  },
  aiFormatNumber(n) {
    const v = Number(n || 0);
    if (!Number.isFinite(v)) {
      return '0';
    }
    if (Math.abs(v) >= 1000000) {
      return (v / 1000000).toFixed(2) + 'M';
    }
    if (Math.abs(v) >= 1000) {
      return (v / 1000).toFixed(1) + 'k';
    }
    return String(Math.round(v));
  },
  aiFormatPct01(n) {
    // 0..1 score → "NN%" string. Returns "—" when null / undefined
    // / NaN / 0 because we don't yet wire any accuracy validator —
    // every row's accuracy_score is null, AVG over null returns
    // null, but a single 0 would still render misleadingly. Once
    // accuracy validation lands and 0 becomes a real value, gate
    // the zero-as-dash branch on a `score_known` carrier.
    if (n === null || n === undefined) {
      return '—';
    }
    const v = Number(n);
    if (!Number.isFinite(v) || v === 0) {
      return '—';
    }
    return Math.round(v * 100) + '%';
  },
  aiFormatCost(n) {
    // Differentiates "no cost data recorded" (—) from a real $0.00.
    // Backend writes `cost_usd=NULL` until per-provider rate-card
    // lookup lands; rollups summing only-null rows return 0, which
    // would mislead operators if rendered as "$0.0000". Render "—"
    // for null / undefined / 0 so the column reads as honest until
    // the rate-card plumbing arrives. Once cost is genuinely
    // computed and a row legitimately reads $0 (free-tier provider,
    // cached response, etc.), this helper can be tightened to
    // distinguish via a `cost_known: bool` carrier from the
    // backend.
    if (n === null || n === undefined) {
      return '—';
    }
    const v = Number(n);
    if (!Number.isFinite(v) || v === 0) {
      return '—';
    }
    return '$' + v.toFixed(4);
  },
  aiFormatTime(ts) {
    if (!ts) {
      return '—';
    }
    const d = new Date(ts * 1000);
    // Compact YYYY-MM-DD HH:mm format; locale string is unstable
    // across browsers / locales so we hand-format in UTC-equivalent
    // local time the SPA's clock already uses elsewhere.
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  },

  // Form state — `aiForm` mirrors `settings.ai.providers[*]` in a
  // shape the inputs can bind directly to (api_key as a write-only
  // string the user types into, api_key_set as the read-only flag
  // for the placeholder/hint). `_aiBaselineSnapshot` captures the
  // shape at form-load so dirty-tracking is precise (mirrors the
  // Portainer / OIDC dirty pattern).
  aiForm: {
    providers: {
      claude: {enabled: false, model: '', base_url: '', api_key: '', api_key_set: false},
      gemini: {enabled: false, model: '', base_url: '', api_key: '', api_key_set: false},
      chatgpt: {enabled: false, model: '', base_url: '', api_key: '', api_key_set: false},
      deepseek: {enabled: false, model: '', base_url: '', api_key: '', api_key_set: false},
    },
  },
  // Canonical model + base URL defaults, hydrated from the backend's
  // `ai.defaults` block on every settings load. Used as placeholder
  // text + as the pre-fill value when the saved setting is empty.
  aiDefaults: {},
  _aiBaselineSnapshot: '',
  aiSaving: false,
  aiDashboard: null,
  aiDashboardLoading: false,
  // `aiDashboardLoaded` / `aiJobsLoaded` follow the canonical `*Loaded`
  // boolean pattern used by every other async-loaded admin table
  // (`usersLoaded` / `sessionsLoaded` / `tokensLoaded` / `schedulesLoaded`
  // / `backupsLoaded` / etc.). Flip true once on first successful
  // fetch and STAY true — distinguishes "still fetching, show spinner"
  // from "fetch completed, table is genuinely empty". Without these,
  // the AI tab's dashboard + jobs tables flashed their "No data" empty
  // state during the in-flight window before /api/admin/ai/dashboard +
  // /api/admin/ai/jobs landed.
  aiDashboardLoaded: false,
  // AI memory — durable lessons the AI emits via MEMORY: directives.
  // Surfaced in Admin → AI memory; injected into every palette
  // call's system prompt so the AI accumulates knowledge over the
  // deployment's lifetime.
  aiMemories: [],
  aiMemoryAddText: '',
  aiMemoryBusy: false,
  aiRange: 24,                     // 1 / 24 / 168 / 720 hours
  aiModalKey: null,                 // 'jobs' / 'cost' / 'tokens' / 'response_time' / 'accuracy' / 'passrate'
  aiJobs: null,                     // { total, jobs: [...] }
  // Canonical `*Loaded` flag for the jobs table — see the matching
  // comment on `aiDashboardLoaded` above. Flipped true on first
  // successful `/api/admin/ai/jobs` response.
  aiJobsLoaded: false,
  aiJobsFilterProvider: '',
  aiJobsFilterStatus: '',
  // Paging + sorting for the dashboard popups (jobs + every trend
  // table — cost / tokens / response_time / accuracy / passrate).
  // 25 per page across the lot; click-to-sort headers. Reset on
  // modal open so navigating between modals always starts at page
  // 1 with the default sort. JOBS pagination is server-side via
  // `/api/admin/ai/jobs?limit=25&offset=N`; trend pagination is
  // client-side because the trend dataset is the in-memory bucketed
  // aggregate from `/api/admin/ai/dashboard` — already capped at
  // ~720 rows even for the 30d window, well under the threshold
  // where extra round-trips beat slicing locally.
  aiModalPage: 1,
  aiModalPageSize: 20,
  aiModalSortCol: '',               // empty = default order (server / source order)
  aiModalSortDir: 'desc',           // 'asc' | 'desc'
  // Per-provider Test state. `loading` while the probe is in
  // flight; `result` is the most recent {ok, status, detail,
  // response_time_ms, provider} dict from /api/admin/ai/{p}/test.
  // Result is sticky until the next test fires so the success/fail
  // chip stays visible during edits, mirroring the Portainer Test
  // pattern.
  aiTestState: {
    claude: {loading: false, result: null},
    gemini: {loading: false, result: null},
    chatgpt: {loading: false, result: null},
    deepseek: {loading: false, result: null},
  },

  _aiSnapshot() {
    // Stable shape — `master_enabled` + `active_provider` + per-provider
    // mirror. API key is stamped via the `api_key_set` boolean OR the
    // typed string; either change marks the form dirty.
    const p = this.aiForm.providers;
    return JSON.stringify({
      master: !!this.settings.ai_enabled,
      active_provider: this.settings.ai_active_provider || '',
      max_tokens: Number.isFinite(+this.settings.ai_max_tokens) ? +this.settings.ai_max_tokens : 1024,
      fb_enabled: !!this.settings.ai_fallback_enabled,
      fb_order: (Array.isArray(this.settings.ai_fallback_order)
        ? this.settings.ai_fallback_order : []).join(','),
      fb_max_depth: Math.max(1, Math.min(2, +this.settings.ai_fallback_max_depth || 1)),
      claude: {en: !!p.claude.enabled, m: p.claude.model || '', u: p.claude.base_url || '', k: p.claude.api_key || '', s: !!p.claude.api_key_set},
      gemini: {en: !!p.gemini.enabled, m: p.gemini.model || '', u: p.gemini.base_url || '', k: p.gemini.api_key || '', s: !!p.gemini.api_key_set},
      chatgpt: {en: !!p.chatgpt.enabled, m: p.chatgpt.model || '', u: p.chatgpt.base_url || '', k: p.chatgpt.api_key || '', s: !!p.chatgpt.api_key_set},
      deepseek: {en: !!p.deepseek.enabled, m: p.deepseek.model || '', u: p.deepseek.base_url || '', k: p.deepseek.api_key || '', s: !!p.deepseek.api_key_set},
    });
  },
  // Helper: count of providers eligible to be fallback (master-enabled
  // + has API key + NOT the active primary). Used to gate the
  // fallback-config section visibility / hint text.
  aiFallbackEligibleCount() {
    const active = (this.settings.ai_active_provider || '').toLowerCase();
    let n = 0;
    for (const name of this.aiProviderNames) {
      const p = this.aiForm.providers[name];
      if (!p || !p.enabled || name === active) {
        continue;
      }
      // API key set OR newly-typed counts as "has key".
      if (p.api_key_set || (p.api_key || '').trim()) {
        n++;
      }
    }
    return n;
  },
  markAiFormDirty() {
    // No-op — the watcher reads aiFormDirty() lazily off the snapshot.
    // Function exists so the @input / @change bindings have a stable
    // call site we can extend later (e.g. clearing per-provider
    // last-test stamps when fields change).
  },
  aiFormDirty() {
    // Section-scoped dirty: the AI form fields (master toggle /
    // active provider / per-provider creds / fallback chain) AND
    // the AI section's owned tunables (output-token cap / fallback
    // depth / retry knobs). Either dirty side enables the Save
    // button — the section's Save commits both in one POST.
    const ownDirty = this._aiBaselineSnapshot !== '' && this._aiSnapshot() !== this._aiBaselineSnapshot;
    if (ownDirty) {
      return true;
    }
    // Tunable dirty — compare each AI-section tunable against the
    // tuning baseline. Falls back to the global tuningDirty path
    // when the helper isn't available, but the keys-list path is
    // cleaner because it only flags THIS section's own tunables.
    try {
      const baseline = this._tuningBaselineMap();
      for (const k of this._aiSectionTuningKeys()) {
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
  hydrateAiFromSettings(d) {
    // Called from loadSettings() after the GET resolves. Mirrors the
    // round-trip shape into `aiForm.providers[*]`. Resets the
    // baseline snapshot so the dirty cue stays clean post-load.
    //
    // Empty `model` / `base_url` fields are pre-filled from
    // `ai.defaults` so a fresh deploy renders the canonical model id
    // + API host already typed in (admin can override and Save, OR
    // delete to opt out and have the placeholder show through).
    // Either path persists deliberately — no surprises on the next
    // load. The pre-fill happens BEFORE the baseline is captured so
    // the dirty cue stays clean unless the operator actually edits.
    const ai = (d && d.ai) || {};
    const provs = ai.providers || {};
    this.aiDefaults = ai.defaults || {};
    this.settings.ai_enabled = !!ai.enabled;
    this.settings.ai_active_provider = ai.active_provider || 'claude';
    this.settings.ai_max_tokens = (Number.isFinite(+ai.max_tokens) && +ai.max_tokens > 0) ? +ai.max_tokens : 1024;
    // Provider fallback chain — opt-in resilience surface. Empty
    // `fallback_order` means no chain configured; UI renders the
    // "no backup providers" hint. `fallback_max_depth` clamps 1..2
    // server-side so any garbage from settings is harmless.
    this.settings.ai_fallback_enabled = !!ai.fallback_enabled;
    this.settings.ai_fallback_order = (ai.fallback_order || '').split(',')
      .map(s => s.trim().toLowerCase())
      .filter(Boolean);
    this.settings.ai_fallback_max_depth = Math.max(1, Math.min(2,
      +ai.fallback_max_depth || 1));
    this.aiProviderNames.forEach(name => {
      const p = provs[name] || {};
      const dflt = (ai.defaults || {})[name] || {};
      this.aiForm.providers[name] = {
        enabled: !!p.enabled,
        // Prefer saved setting; fall back to canonical default so
        // the field renders pre-filled instead of blank on first
        // open.
        model: p.model || dflt.model || '',
        base_url: p.base_url || dflt.base_url || '',
        api_key: '',
        api_key_set: !!p.api_key_set,
      };
    });
    // Defer baseline capture one tick so Alpine's reactive
    // assignments above are reflected in the snapshot.
    this.$nextTick(() => {
      this._aiBaselineSnapshot = this._aiSnapshot();
    });
  },
  // AI section's own tunables — saveAiSettings includes these in
  // its `/api/settings` POST body so the AI Save button commits
  // them along with the rest of the AI config (master toggle,
  // active provider, per-provider creds, retry knobs, output-
  // token cap). Adding a new AI tunable: add it here AND to the
  // matching dirty-tracker keys list AND to the SettingsIn
  // backend model.
  _aiSectionTuningKeys() {
    return [
      // Auto-retry knobs (already-shipped, were saved via the
      // generic saveTuning before; now ride along with the AI Save).
      'tuning_ai_retry_enabled',
      'tuning_ai_retry_backoff_ms',
      'tuning_ai_retry_first_attempt_max_ms',
      // Output-token cap (replaces legacy `ai_max_tokens` plain
      // settings field; bound to the bounds-chips form row).
      'tuning_ai_max_tokens',
      // Fallback chain depth (replaces legacy `ai_fallback_max_depth`).
      'tuning_ai_fallback_max_depth',
      // Sidebar layout + export toggle — both rendered inside the
      // AI Integration partial so they belong on the AI Save.
      'tuning_ai_sidebar_width_px',
      'tuning_ai_conversation_export_enabled',
      // Sidebar conversation-persist cadence (ms). Same admin section.
      'tuning_ai_conversation_persist_interval_ms',
      // Log-context window + cap — how many hours / lines of
      // persistent logs the palette injects per call.
      'tuning_ai_log_context_hours',
      'tuning_ai_log_context_lines',
      // Outbound HTTP wall-clocks for the Test-connection probe
      // (one-token ping, 15s default) + real chat-completion call
      // (30s default). Per-use reads inside `logic.ai.test_provider`
      // / `ask_provider` so a Save here takes effect on the next
      // round-trip without restart.
      'tuning_ai_http_timeout_seconds',
      'tuning_ai_extended_http_timeout_seconds',
    ];
  },
  async saveAiSettings() {
    if (this.aiSaving || this.isReadonly()) {
      return;
    }
    this.aiSaving = true;
    try {
      const body = {
        ai_enabled: !!this.settings.ai_enabled,
        ai_active_provider: this.settings.ai_active_provider || 'claude',
        ai_max_tokens: (Number.isFinite(+this.settings.ai_max_tokens) && +this.settings.ai_max_tokens > 0) ? +this.settings.ai_max_tokens : 1024,
        // Fallback chain — backend re-validates the CSV against
        // SUPPORTED_PROVIDERS so an unknown id can't slip through.
        ai_fallback_enabled: !!this.settings.ai_fallback_enabled,
        ai_fallback_order: (Array.isArray(this.settings.ai_fallback_order)
          ? this.settings.ai_fallback_order : [])
          .filter(Boolean).join(','),
        ai_fallback_max_depth: Math.max(1, Math.min(2,
          +this.settings.ai_fallback_max_depth || 1)),
      };
      // Section-owned tunables ride along in the same POST. The
      // backend's api_set_settings handler processes plain settings
      // + tunables uniformly; per-section save = one round-trip.
      for (const k of this._aiSectionTuningKeys()) {
        const v = (this.tuningForm || {})[k];
        body[k] = (v == null ? '' : String(v).trim());
      }
      this.aiProviderNames.forEach(name => {
        const p = this.aiForm.providers[name];
        body[`ai_provider_${name}_enabled`] = !!p.enabled;
        body[`ai_provider_${name}_model`] = p.model || '';
        body[`ai_provider_${name}_base_url`] = p.base_url || '';
        // API key — keep-current-if-blank. Only send when the user
        // typed something so an unchanged form doesn't blank the key.
        if ((p.api_key || '').trim()) {
          body[`ai_provider_${name}_api_key`] = p.api_key.trim();
        }
      });
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${r.status}`);
      }
      // Reload settings to pick up the freshly-flipped api_key_set
      // booleans + reset the dirty baseline. Also reload the
      // dashboard so the per-provider breakdown reflects the new
      // enabled state without a page refresh — pre-fix the chip
      // colour in the breakdown was driven by `aiDashboard.providers[i].enabled`
      // (set at dashboard fetch time), so a freshly-enabled
      // provider stayed greyed out until the next manual reload.
      // Both fetches are independent — fan out in parallel.
      // `loadTuning()` is in the parallel set too because the AI
      // section now owns tunables (`tuning_ai_max_tokens`, etc.)
      // — without re-fetching the tuning state, `_tuningBaseline`
      // stays stale and `aiFormDirty()` reports dirty forever even
      // after a successful save.
      await Promise.all([
        this.loadSettings(),
        this.loadAiDashboard(true),
        this.loadTuning(),
      ]);
      // Clear the user-typed api keys so the inputs don't carry the
      // value across the dirty boundary (the GET response only
      // surfaces api_key_set; the input ought to be blank again).
      this.aiProviderNames.forEach(name => {
        this.aiForm.providers[name].api_key = '';
      });
      this.$nextTick(() => {
        this._aiBaselineSnapshot = this._aiSnapshot();
      });
      this.showToast(this.t('admin.ai.save_ok'), 'success');
    } catch (e) {
      console.error('[ai] saveAiSettings failed:', e);
      this.showToast(this.t('admin.ai.save_failed') + ': ' + (e.message || e), 'error');
    } finally {
      this.aiSaving = false;
    }
  },

  setAiRange(hours) {
    this.aiRange = hours;
    this.loadAiDashboard(true);
  },
  async loadAiDashboard(_force) {
    if (this.aiDashboardLoading) {
      return;
    }
    this.aiDashboardLoading = true;
    try {
      const r = await fetch(`/api/admin/ai/dashboard?hours=${encodeURIComponent(this.aiRange || 24)}`);
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      this.aiDashboard = await r.json();
    } catch (e) {
      console.error('[ai] loadAiDashboard failed:', e);
      this.aiDashboard = null;
    } finally {
      this.aiDashboardLoading = false;
      // Mark loaded regardless of fetch outcome — the empty-state
      // gate in the partial expects this flag to distinguish "still
      // fetching" from "fetch completed, result is empty / failed".
      this.aiDashboardLoaded = true;
    }
    // Always refresh the memory list when the dashboard loads — the
    // AI tab and the memory pane share the same scope.
    try {
      await this.loadAiMemories();
    } catch (_) { /* noop */
    }
  },
  async loadAiMemories() {
    try {
      const r = await fetch('/api/ai/memory');
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      const j = await r.json();
      this.aiMemories = Array.isArray(j.memories) ? j.memories : [];
    } catch (e) {
      console.error('[ai] loadAiMemories failed:', e);
      this.aiMemories = [];
    }
  },
  async addAiMemory() {
    const text = (this.aiMemoryAddText || '').trim();
    if (!text || this.aiMemoryBusy) {
      return;
    }
    this.aiMemoryBusy = true;
    try {
      const r = await fetch('/api/ai/memory', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text, source: 'operator'}),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      this.aiMemoryAddText = '';
      if (typeof this.showToast === 'function') {
        this.showToast(this.t('ai_memory.toast_added') || 'Memory added', 'success');
      }
      await this.loadAiMemories();
    } catch (e) {
      if (typeof this.showToast === 'function') {
        this.showToast((e && e.message) || this.t('actions.failed_generic') || 'Failed', 'error');
      }
    } finally {
      this.aiMemoryBusy = false;
    }
  },
  async deleteAiMemory(memId) {
    const ok = await this.confirmDialog({
      title: this.t('ai_memory.delete_confirm') || 'Delete this memory?',
      confirmButtonText: this.t('actions.delete') || 'Delete',
      cancelButtonText: this.t('actions.cancel') || 'Cancel',
    });
    if (!ok) {
      return;
    }
    try {
      const r = await fetch('/api/ai/memory/' + encodeURIComponent(memId), {
        method: 'DELETE',
      });
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      await this.loadAiMemories();
      if (typeof this.showToast === 'function') {
        this.showToast(this.t('ai_memory.toast_forgotten') || 'Memory forgotten', 'success');
      }
    } catch (e) {
      if (typeof this.showToast === 'function') {
        this.showToast((e && e.message) || this.t('actions.failed_generic') || 'Failed', 'error');
      }
    }
  },
  async loadAiJobs() {
    const params = new URLSearchParams();
    params.set('hours', String(this.aiRange || 24));
    const page = Math.max(1, Number(this.aiModalPage) || 1);
    const size = Math.max(1, Number(this.aiModalPageSize) || 20);
    params.set('limit', String(size));
    params.set('offset', String((page - 1) * size));
    if (this.aiJobsFilterProvider) {
      params.set('provider', this.aiJobsFilterProvider);
    }
    if (this.aiJobsFilterStatus) {
      params.set('status', this.aiJobsFilterStatus);
    }
    try {
      const r = await fetch(`/api/admin/ai/jobs?${params.toString()}`);
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      this.aiJobs = await r.json();
    } catch (e) {
      console.error('[ai] loadAiJobs failed:', e);
      this.aiJobs = null;
    } finally {
      // Canonical `*Loaded` flag — flip true once on first response so
      // the partial's empty-state gate can distinguish "still fetching"
      // from "result is genuinely empty / failed".
      this.aiJobsLoaded = true;
    }
  },
  openAiModal(key) {
    this.aiModalKey = key;
    // Reset paging + sort on every open so navigating Jobs → Tokens
    // → Cost doesn't carry one modal's last-page through to the next.
    // Default sort is date DESC across every modal — every table
    // exposes a `ts` column (jobs row timestamp / trend bucket
    // timestamp) and newest-first matches operator expectation.
    this.aiModalPage = 1;
    this.aiModalSortCol = 'ts';
    this.aiModalSortDir = 'desc';
    if (key === 'jobs') {
      this.loadAiJobs();
    }
  },
  // Click-to-sort helper. Toggles direction when clicking the same
  // column; switches column + defaults to 'desc' (numeric biggest-first
  // / dates newest-first) on a different column. Resets to page 1 so
  // the first row of the new sort is visible. Server-fetched JOBS
  // table re-fetches the first page; client-paged trend table just
  // re-slices on the next render.
  aiSortBy(col) {
    if (!col) {
      return;
    }
    if (this.aiModalSortCol === col) {
      this.aiModalSortDir = (this.aiModalSortDir === 'asc') ? 'desc' : 'asc';
    } else {
      this.aiModalSortCol = col;
      this.aiModalSortDir = 'desc';
    }
    this.aiModalPage = 1;
    if (this.aiModalKey === 'jobs') {
      this.loadAiJobs();
    }
  },
  // Stable comparator across mixed-type fields (number / string /
  // null). Nulls sink to the bottom regardless of direction so a
  // partial dataset doesn't disrupt sort ergonomics.
  _aiSortValue(row, col) {
    if (!row) {
      return null;
    }
    const v = row[col];
    if (v == null || v === '') {
      return null;
    }
    if (typeof v === 'number') {
      return v;
    }
    const n = Number(v);
    if (Number.isFinite(n) && (typeof v === 'string' && /^[\d.\-+eE]+$/.test(v))) {
      return n;
    }
    return String(v);
  },
  _aiSortRows(rows) {
    const col = this.aiModalSortCol;
    if (!col || !Array.isArray(rows) || rows.length < 2) {
      return rows || [];
    }
    const dir = (this.aiModalSortDir === 'asc') ? 1 : -1;
    const out = rows.slice();
    out.sort((a, b) => {
      const av = this._aiSortValue(a, col);
      const bv = this._aiSortValue(b, col);
      if (av == null && bv == null) {
        return 0;
      }
      if (av == null) {
        return 1;
      }   // nulls last regardless of dir
      if (bv == null) {
        return -1;
      }
      if (typeof av === 'number' && typeof bv === 'number') {
        return (av - bv) * dir;
      }
      return String(av).localeCompare(String(bv)) * dir;
    });
    return out;
  },
  // Returns the SORTED page-slice of `aiDashboard.trend` for the
  // current page + sort state. Trend modals (cost / tokens /
  // response_time / accuracy / passrate) all share the same
  // bucketed table structure so one helper covers them.
  aiTrendRows() {
    const arr = (this.aiDashboard && Array.isArray(this.aiDashboard.trend))
      ? this.aiDashboard.trend
      : [];
    const sorted = this._aiSortRows(arr);
    const page = Math.max(1, Number(this.aiModalPage) || 1);
    const size = Math.max(1, Number(this.aiModalPageSize) || 20);
    const start = (page - 1) * size;
    return sorted.slice(start, start + size);
  },
  aiTrendTotal() {
    return (this.aiDashboard && Array.isArray(this.aiDashboard.trend))
      ? this.aiDashboard.trend.length
      : 0;
  },
  // Server-fetched JOBS table — `aiJobs.jobs` is already the current
  // page from the backend, so the client-side step is just sort.
  aiJobsRows() {
    const arr = (this.aiJobs && Array.isArray(this.aiJobs.jobs))
      ? this.aiJobs.jobs
      : [];
    return this._aiSortRows(arr);
  },
  aiJobsTotal() {
    return (this.aiJobs && Number.isFinite(Number(this.aiJobs.total)))
      ? Number(this.aiJobs.total)
      : 0;
  },
  // Total pages for the active modal — used by the paginator footer.
  aiModalTotalPages() {
    const total = (this.aiModalKey === 'jobs') ? this.aiJobsTotal() : this.aiTrendTotal();
    const size = Math.max(1, Number(this.aiModalPageSize) || 20);
    return Math.max(1, Math.ceil(total / size));
  },
  aiModalGoPage(n) {
    const last = this.aiModalTotalPages();
    const next = Math.max(1, Math.min(last, Number(n) || 1));
    if (next === this.aiModalPage) {
      return;
    }
    this.aiModalPage = next;
    if (this.aiModalKey === 'jobs') {
      this.loadAiJobs();
    }
  },
  // Header-render helper: returns ' ▲' / ' ▼' / '' for the active
  // sort column. Bound via x-text on a sibling span so the header
  // text stays in i18n while the indicator is purely visual. Use
  // the unicode chars (U+25B2 / U+25BC) so no extra SVG asset.
  aiSortIndicator(col) {
    if (this.aiModalSortCol !== col) {
      return '';
    }
    return (this.aiModalSortDir === 'asc') ? ' ▲' : ' ▼';
  },
  closeAiModal() {
    this.aiModalKey = null;
  },
  // Resolve the modal's title against the i18n bundle. Falls back to
  // a generic "AI dashboard" label when (a) no key is set yet, OR
  // (b) the bundle is missing the per-kind title (so screen readers
  // hear something meaningful instead of "dialog" with no name OR a
  // raw key path like `admin.ai.modal.passrate_title`).
  aiModalTitle() {
    if (!this.aiModalKey) {
      return this.t('admin.ai.modal.title_fallback');
    }
    const key = 'admin.ai.modal.' + this.aiModalKey + '_title';
    const resolved = this.t(key);
    return (resolved && resolved !== key) ? resolved : this.t('admin.ai.modal.title_fallback');
  },
  async testAiProvider(name) {
    // Per-provider Test connection probe. POSTs the typed-but-not-
    // yet-saved api_key + model + base_url so admins can validate
    // changes BEFORE committing them to the DB. When the api_key
    // input is blank we send no key in the body and the backend
    // falls back to the saved value — re-tests after Save don't
    // require re-pasting the secret.
    if (!this.aiTestState[name]) {
      this.aiTestState[name] = {loading: false, result: null};
    }
    if (this.aiTestState[name].loading) {
      return;
    }
    this.aiTestState[name].loading = true;
    this.aiTestState[name].result = null;
    try {
      const p = this.aiForm.providers[name] || {};
      const body = {
        model: p.model || '',
        base_url: p.base_url || '',
      };
      // Only send api_key when the user typed something. Blank →
      // backend uses the saved key (if any).
      if ((p.api_key || '').trim()) {
        body.api_key = p.api_key.trim();
      }
      const r = await fetch(`/api/admin/ai/${encodeURIComponent(name)}/test`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok && !('ok' in data)) {
        // Backend raised before reaching the probe (auth / 4xx).
        this.aiTestState[name].result = {
          ok: false,
          detail: (data && data.detail) || `HTTP ${r.status}`,
          response_time_ms: 0,
          provider: name,
        };
      } else {
        this.aiTestState[name].result = data;
      }
      // Toast a one-line summary so the operator sees the outcome
      // even without scrolling to the per-card chip.
      if (this.aiTestState[name].result.ok) {
        this.showToast(this.t('admin.ai.test_ok') + ' · ' + this.aiProviderDisplayName(name), 'success');
      } else {
        this.showToast(
          this.aiProviderDisplayName(name) + ' · ' + (this.aiTestState[name].result.detail || this.t('admin.ai.test_failed')),
          'error');
      }
    } catch (e) {
      this.aiTestState[name].result = {
        ok: false, detail: String(e && e.message || e),
        response_time_ms: 0, provider: name,
      };
      this.showToast(this.aiProviderDisplayName(name) + ' · ' + (e.message || e), 'error');
    } finally {
      this.aiTestState[name].loading = false;
    }
  },
  async _runCommandPaletteAiBulk(payload) {
    if (!payload || !payload.query) {
      return;
    }
    const original = payload.query;
    // Cheap visible signal — swap the input placeholder while the
    // call is in flight. Don't overwrite the query text since the
    // operator might cancel and continue typing.
    const prevPlaceholder = this.commandPaletteAiBulkBusy;
    this.commandPaletteAiBulkBusy = true;
    try {
      const r = await fetch('/api/ai/host-filter', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({query: original}),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok || !j.dsl) {
        const msg = (j && (j.detail || j.error)) || ('HTTP ' + r.status);
        if (typeof this.showToast === 'function') {
          this.showToast(this.t('toasts.failed_with_error', {error: msg}), 'error');
        }
        return;
      }
      // Set the query — the watcher / re-render will pick up the
      // bulk DSL and flip the palette into bulk mode.
      this.commandPaletteQuery = j.dsl;
    } catch (e) {
      if (typeof this.showToast === 'function') {
        this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
      }
    } finally {
      this.commandPaletteAiBulkBusy = prevPlaceholder ? true : false;
    }
  },
  // Single source of truth for "should the AI surface be wired into
  // Cmd-K right now?" Consulted both at result-build time (deciding
  // whether to show the synthetic row) AND at activation time
  // (defence-in-depth so a stale cached row from a recent admin
  // toggle-off can't fire). When ANY condition fails (master switch,
  // active provider, presence of /api/me's client_config block) the
  // function returns false and the palette behaves exactly like the
  // pre-AI version.
  _aiPaletteSurfaceEnabled() {
    try {
      // Read from reactive `this.settings` first so toggling AI on /
      // off in Admin → AI Integration updates the sidebar / launcher
      // / palette gates immediately on Save (no full page reload
      // required). Falls back to `me.client_config.ai` for the
      // brief window between SPA boot and the first /api/settings
      // hydration.
      const sEnabled = this.settings && this.settings.ai_enabled;
      const sProvider = (this.settings && this.settings.ai_active_provider) || '';
      if (sEnabled !== undefined && sEnabled !== null) {
        if (!sEnabled) {
          return false;
        }
        return !!sProvider;
      }
      const aiCfg = this.me && this.me.client_config && this.me.client_config.ai;
      if (!aiCfg) {
        return false;
      }
      if (!aiCfg.enabled) {
        return false;
      }
      if (!aiCfg.active_provider) {
        return false;
      }
      return true;
    } catch (_) {
      return false;
    }
  },
};
