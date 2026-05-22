// noinspection ElementNotExported,JSUnusedGlobalSymbols,JSUnusedLocalSymbols,JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,CheckTagEmptyBody,HtmlUnknownTag,HtmlExtraClosingTag,MagicNumberJS,UnusedCatchParameterJS,OverlyComplexBooleanExpressionJS,FunctionWithMultipleReturnPointsJS,FunctionWithMoreThanThreeNegationsJS,OverlyNestedFunctionJS,OverlyLongFunctionJS,OverlyComplexFunctionJS,FunctionWithInconsistentReturnsJS,ChainedFunctionCallJS,NestedFunctionCallJS,NestedAssignmentJS,JSVariableNamingConventionJS,FunctionNamingConventionJS,JSStringConcatenationToES6Template,JSPotentiallyInvalidUsageOfThis,ContinueStatementJS,BreakStatementJS,AssignmentToFunctionParameterJS,IfStatementWithoutBlockJS,IfStatementWithIdenticalBranchesJS,AnonymousFunctionJS,AnonymousCapturingGroupJS,AnonymousFunctionRegExpJS,NamedFunctionExpressionJS,ConditionalExpressionJS,NestedConditionalExpressionJS,ConstantOnRightSideOfComparisonJS,ConstantOnLeftSideOfComparisonJS,EmptyCatchBlockJS,StatementWithEmptyBodyJS,RedundantConditionalExpressionJS,RedundantLocalVariableJS,JSValidateTypes,JSCheckFunctionSignatures,JSPrimitiveTypeWrapperUsage,JSDuplicatedDeclaration,TooManyFunctionParametersJS,NestedTemplateLiteralJS,AssignmentToForLoopParameterJS,AssignmentResultUsedJS,ConditionalCanBeReplacedWithEarlyExitJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA topbar widgets — clock, weather, public-IP.
//
// All three are operator-opt-in via Settings → Profile / Admin → Public IP.
// State + lifecycle for each: ticker loop, fetch helper, persistence.
//
// Phase 2, Batch 23 of the static/js/app.js modularisation.

export default {
    // Topbar clock + weather widgets. Per-browser preferences kept in
    // localStorage so each operator's city survives refresh. Clock is
    // purely client-side; weather goes through the backend proxy at
    // /api/weather (Open-Meteo, no API key, 10-min server cache).
    headerClockEnabled: (typeof localStorage !== 'undefined' && localStorage.getItem('headerClockEnabled') !== 'false'),
    headerWeatherEnabled: (typeof localStorage !== 'undefined' && localStorage.getItem('headerWeatherEnabled') === 'true'),
    headerWeatherLat:   (typeof localStorage !== 'undefined' ? (parseFloat(localStorage.getItem('headerWeatherLat') || '') || null) : null),
    headerWeatherLon:   (typeof localStorage !== 'undefined' ? (parseFloat(localStorage.getItem('headerWeatherLon') || '') || null) : null),
    headerWeatherLabel: (typeof localStorage !== 'undefined' ? (localStorage.getItem('headerWeatherLabel') || '') : ''),
    // Operator's preferred temperature unit ('c' | 'f', default 'c').
    // Backend `/api/weather` always returns Celsius (`temp_c`,
    // forecast `temp_max_c` / `temp_min_c`); the SPA converts at the
    // render boundary via `formatTempPref(c)`. Persisted to
    // `ui_prefs.weather_unit` cross-device, with localStorage as the
    // fast-path cache. Routes through the same headerPrefs save flow
    // as the rest of the topbar weather widget — no separate Save.
    headerWeatherUnit:  (typeof localStorage !== 'undefined' ? ((localStorage.getItem('headerWeatherUnit') || 'c').toLowerCase() === 'f' ? 'f' : 'c') : 'c'),
    currentClock: '',
    weather: null,
    // Public IP + ISP lookup cache for AI palette context.
    // `publicIp.enabled` is the backend's `tuning_public_ip_enabled`
    // gate; `_publicIpFetchedAt` is the local last-fetch ts so we
    // don't re-probe more than once per cache window (matches the
    // backend's `tuning_public_ip_cache_ttl_seconds` TTL).
    publicIp: null,
    _publicIpFetchedAt: 0,
    _clockTimer: null,
    _weatherTimer: null,
    // Admin → Public IP state. `publicIpSaving` gates the section
    // Save button; `publicIpTestResult` carries the most-recent
    // /api/public-ip JSON response for the operator-visible result
    // panel under the buttons row.
    publicIpSaving: false,
    publicIpTesting: false,
    publicIpTestOk: false,
    publicIpTestResult: '',

    // Admin → Public IP — section-owned save for the three
    // public-IP tunables. Master toggle is the
    // `tuning_public_ip_enabled` int (1/0); cache TTL + fetch
    // timeout are operator-tunable numerics. No plain settings — the
    // entire feature config is in TUNABLES so the migration shape is
    // clean.
    _publicIpSectionTuningKeys() {
      return [
        'tuning_public_ip_enabled',
        'tuning_public_ip_cache_ttl_seconds',
        'tuning_public_ip_fetch_timeout_seconds',
      ];
    },
    publicIpSectionDirty() {
      try {
        const baseline = this._tuningBaselineMap();
        for (const k of this._publicIpSectionTuningKeys()) {
          const cur = (this.tuningForm || {})[k];
          const curStr = (cur == null ? '' : String(cur).trim());
          const baseStr = (baseline[k] == null ? '' : String(baseline[k]).trim());
          if (curStr !== baseStr) {
            return true;
          }
        }
      } catch (_) {}
      return false;
    },
    async savePublicIpSection() {
      if (this.publicIpSaving) {
        return;
      }
      // Validate every tunable against its declared (min, max) bounds
      // BEFORE the POST so a typo lands a toast instead of a partial
      // save.
      for (const k of this._publicIpSectionTuningKeys()) {
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
      this.publicIpSaving = true;
      try {
        const body = {};
        for (const k of this._publicIpSectionTuningKeys()) {
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
        // Re-baseline tunables + invalidate the SPA's public-IP
        // cache so the next AI palette call sees the new gate state.
        await Promise.all([this.loadSettings(), this.loadTuning()]);
        try { this.publicIp = null; this._publicIpFetchedAt = 0; } catch (_) {}
        this.showToast(this.t('toasts.saved') || 'Saved', 'success');
      } catch (e) {
        this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed') + ': ' + (e.message || e), 'error');
      } finally {
        this.publicIpSaving = false;
      }
    },
    async testPublicIpLookup() {
      // Surface the canonical lookup output (the same shape every
      // consumer sees) so the operator can confirm end-to-end without
      // leaving the admin tab. Spinner + ok/error chip-tone mirror
      // the Asset Inventory "Test connection" UX shape.
      if (this.publicIpTesting) {
        return;
      }
      this.publicIpTesting = true;
      this.publicIpTestResult = '';
      this.publicIpTestOk = false;
      try {
        const r = await fetch('/api/public-ip', { credentials: 'same-origin' });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.publicIpTestOk = false;
          this.publicIpTestResult = 'HTTP ' + r.status + ': ' + (j.detail || JSON.stringify(j));
        } else if (j && j.error) {
          this.publicIpTestOk = false;
          this.publicIpTestResult = j.error;
        } else if (j && j.enabled === false) {
          this.publicIpTestOk = false;
          this.publicIpTestResult = (this.t('admin_public_ip.test_disabled_hint')
            || 'Public-IP lookup is disabled — enable + Save first, then re-test.');
        } else {
          this.publicIpTestOk = true;
          this.publicIpTestResult = JSON.stringify(j, null, 2);
        }
      } catch (e) {
        this.publicIpTestOk = false;
        this.publicIpTestResult = 'fetch failed: ' + (e && e.message ? e.message : String(e));
      } finally {
        this.publicIpTesting = false;
      }
    },

    // --- Topbar clock + weather ---
    tickHeaderClock() {
      const now = new Date();
      // Routes through the user's Formats preference (Settings →
      // Profile → Formats) via the shared `_userTimeOnlyFormat`
      // stripper — single source of truth for "derive time-only from
      // the user's full pref" across the topbar clock + Stats chart
      // axes. Falls back to `HH:mm` when the user's pref had no time
      // component at all so the clock never goes blank.
      this.currentClock = this._applyDateTimeFormat(now, this._userTimeOnlyFormat());
    },
    startHeaderClock() {
      if (this._clockTimer) {
        return;
      }
      this.tickHeaderClock();
      // 10s cadence — granular enough to keep minutes synced without
      // hammering the render loop.
      this._clockTimer = setInterval(() => this.tickHeaderClock(), 10000);
    },
    async loadHeaderWeather() {
      if (!this.headerWeatherEnabled
          || this.headerWeatherLat == null
          || this.headerWeatherLon == null) {
        this.weather = null;
        return;
      }
      try {
        const p = new URLSearchParams({
          lat:   String(this.headerWeatherLat),
          lon:   String(this.headerWeatherLon),
          label: this.headerWeatherLabel || '',
        });
        const r = await fetch('/api/weather?' + p.toString());
        if (!r.ok) { this.weather = null; return; }
        this.weather = await r.json();
      } catch (_) {
        this.weather = null;
      }
    },
    startHeaderWeather() {
      if (this._weatherTimer) {
        return;
      }
      this.loadHeaderWeather();
      // 10 min cadence — backend already caches 10 min per coord, so
      // this matches the server-side TTL. Even if the operator has ten
      // tabs open they hit the cache after the first.
      this._weatherTimer = setInterval(() => this.loadHeaderWeather(), 600000);
    },
    // Inline SVG path(s) per WMO-icon slug. Kept tiny — the topbar chip
    // is 16px so detail is wasted. Backend maps WMO codes to slugs in
    // main.py:_WMO_CODES so the mapping has ONE source of truth.
    weatherIconPath(slug) {
      const icons = {
        'sun':       '<circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
        'cloud-sun': '<circle cx="7" cy="8" r="3"/><path d="M7 2v2M2 8h2M12 8h-.5M4 4l1 1M10 4L9 5"/><path d="M20 17h-10.5a3.5 3.5 0 1 1 .8-6.9"/>',
        'cloud':     '<path d="M17 18H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 18z"/>',
        'fog':       '<path d="M3 9h18M3 13h18M3 17h12M7 5h14"/>',
        'drizzle':   '<path d="M17 14H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 14z"/><path d="M9 18v2M13 18v2M17 18v2"/>',
        'rain':      '<path d="M17 14H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 14z"/><path d="M8 16v4M12 18v4M16 16v4"/>',
        'snow':      '<path d="M17 14H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 14z"/><path d="M8 17v1M12 19v1M16 17v1M8 20v1M12 22v.01M16 20v1"/>',
        'sleet':     '<path d="M17 14H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 14z"/><path d="M9 17v2M13 19v2M17 17v2M10 20h.01M14 18h.01"/>',
        'thunder':   '<path d="M19 16a5 5 0 0 0-1-9h-1.3a7 7 0 0 0-13.4 2"/><polyline points="13 11 9 17 14 17 10 22"/>',
      };
      return icons[slug] || icons['cloud'];
    },
    // Build the rich `{view, hosts, items}` context object the AI
    // palette + sidebar + any future AI surface ship to /api/ai/palette.
    // Compact primitives only — no nested objects — so the prompt
    // stays inside the token budget (30 hosts × ~12 fields ≈ 3k
    // tokens). The backend's `build_palette_user_prompt` renders these
    // as a "Available hosts" / "Available items" block. WITHOUT the
    // metric fields here, the AI has nothing to answer "which hosts
    // are out of disk?" with and falls back to fabricating host names
    // + values — observed regression on the AI sidebar before this
    // helper was extracted.
    // Fetch the public-IP block lazily before each AI palette call.
    // Backend gates on `tuning_public_ip_enabled` (default OFF) and
    // caches per `tuning_public_ip_cache_ttl_seconds` — this SPA-side
    // cache layers on top so a burst of palette calls inside the
    // same cache window doesn't re-fetch even from the warm backend
    // cache. Fire-and-forget;
    // a slow/failing fetch never blocks the AI call.
    async _ensurePublicIp() {
      const now = Date.now();
      if (this.publicIp && (now - this._publicIpFetchedAt) < 10 * 60 * 1000) {
        return;
      }
      try {
        const r = await fetch('/api/public-ip');
        if (!r.ok) {
          return;
        }
        this.publicIp = await r.json();
        this._publicIpFetchedAt = now;
      } catch (_) { /* silent — AI prompt just omits the block */ }
    },
};
