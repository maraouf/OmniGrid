// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSUnusedGlobalSymbols,JSUnusedLocalSymbols
// noinspection EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,ExceptionCaughtLocallyJS
// noinspection NegatedConditionalExpressionJS,JSNegatedConditionalExpression,NegatedIfStatementJS,IfStatementWithIdenticalBranchesJS
// noinspection HtmlUnknownTag,HtmlEmptyTagsRecommendation,VoidExpressionJS,JSVoidExpression,UnnecessaryReturnStatementJS,JSValidateTypes
// Per-inspection suppressions match the sibling SPA files (app-drawer-bulk.js et al). Covered SPA idioms:
// constants on the right of comparisons (modern ESLint default); arrow / anonymous callbacks;
// chained map+filter; nested t() / toString() / loadSettings() / loadTuning() calls; magic numbers
// for unit conversions + thresholds; empty catch blocks holding the fire-and-forget try/catch shape;
// `_` unused catch parameter; `continue` inside for-of guards; Alpine-called methods PyCharm can't
// trace through `@click` / `:disabled` bindings; `<table>` / `<svg>` substrings inside JS template
// literals the HTML inspector mis-parses. Real findings (ignored Promise, exception caught locally)
// are fixed below, not suppressed.
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA topbar widgets — clock, weather, public-IP.

export default {
  // Topbar clock + weather widgets. Per-browser preferences kept in
  // localStorage so each operator's city survives refresh. Clock is
  // purely client-side; weather goes through the backend proxy at
  // /api/weather (Open-Meteo, no API key, 10-min server cache).
  headerClockEnabled: (typeof localStorage !== 'undefined' && localStorage.getItem('headerClockEnabled') !== 'false'),
  headerWeatherEnabled: (typeof localStorage !== 'undefined' && localStorage.getItem('headerWeatherEnabled') === 'true'),
  headerWeatherLat: (typeof localStorage !== 'undefined' ? (parseFloat(localStorage.getItem('headerWeatherLat') || '') || null) : null),
  headerWeatherLon: (typeof localStorage !== 'undefined' ? (parseFloat(localStorage.getItem('headerWeatherLon') || '') || null) : null),
  headerWeatherLabel: (typeof localStorage !== 'undefined' ? (localStorage.getItem('headerWeatherLabel') || '') : ''),
  // Operator's preferred temperature unit ('c' | 'f', default 'c').
  // Backend `/api/weather` always returns Celsius (`temp_c`,
  // forecast `temp_max_c` / `temp_min_c`); the SPA converts at the
  // render boundary via `formatTempPref(c)`. Persisted to
  // `ui_prefs.weather_unit` cross-device, with localStorage as the
  // fast-path cache. Routes through the same headerPrefs save flow
  // as the rest of the topbar weather widget — no separate Save.
  headerWeatherUnit: (typeof localStorage !== 'undefined' ? ((localStorage.getItem('headerWeatherUnit') || 'c').toLowerCase() === 'f' ? 'f' : 'c') : 'c'),
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

  // Admin → Weather (WeatherAPI.com). `weatherForm` holds the
  // transient secret-input + clear-flag so the persisted SettingsIn
  // round-trip doesn't echo the API key. `_weatherLastPassedTest`
  // is the snapshot stamped when Test connection succeeded — the
  // Test-before-Save gate compares the live snapshot against it.
  // `_weatherBaselineSnapshot` is the post-Save baseline so the
  // dirty-cue clears on a clean save.
  weatherForm: {api_key: '', clear_api_key: false},
  weatherSaving: false,
  weatherTesting: false,
  weatherTestResult: {ok: false, detail: ''},
  _weatherLastPassedTest: '',
  _weatherBaselineSnapshot: '',
  // Weather-history cache for the AI palette context-builder.
  // Mirrors `_publicIpHistoryCache` — populated by
  // `_ensureWeatherHistory()` on AI palette open. 10-min refresh TTL.
  _weatherHistoryCache: null,
  _weatherHistoryFetchedAt: 0,
  // Admin > Weather "Recent samples" panel state — separate from
  // the AI-context cache above because it's admin-only + uses a
  // smaller row cap + fetches on operator click rather than on
  // every AI palette open.
  weatherHistory: [],
  weatherHistoryLoading: false,
  // Per-widget refresh-in-flight gate. `appsWidgetRefreshing[kind] =
  // true` while a manual refresh is mid-fetch — drives the spinner
  // on the per-widget refresh button + disables the button to prevent
  // double-clicks. Keyed by widget kind so multiple widgets refreshing
  // in parallel don't block each other.
  appsWidgetRefreshing: {},
  // Stamp the local last-fetch ts on the weather block so the Apps
  // widget freshness label has a clock to count from. Updated by
  // `loadHeaderWeather` on every successful poll AND on the manual
  // refresh button.
  _weatherFetchedAt: 0,

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
    } catch (_) {
    }
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
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      // Re-baseline tunables + invalidate the SPA's public-IP
      // cache so the next AI palette call sees the new gate state.
      await Promise.all([this.loadSettings(), this.loadTuning()]);
      try {
        this.publicIp = null;
        this._publicIpFetchedAt = 0;
      } catch (_) {
      }
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
      const r = await fetch('/api/public-ip', {credentials: 'same-origin'});
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
  async loadHeaderWeather(force = false) {
    if (!this.headerWeatherEnabled
      || this.headerWeatherLat == null
      || this.headerWeatherLon == null) {
      this.weather = null;
      return;
    }
    try {
      const p = new URLSearchParams({
        lat: String(this.headerWeatherLat),
        lon: String(this.headerWeatherLon),
        label: this.headerWeatherLabel || '',
      });
      // Explicit Refresh bypasses the backend per-coord TTL cache so the
      // operator gets current data on demand. NOT done by zeroing the
      // freshness stamp (the old approach) — that blanked the "Updated
      // Xs ago" label mid-fetch and flickered the card. The stamp now
      // holds its previous value until the fresh response lands.
      if (force) {
        p.set('force', '1');
      }
      const r = await fetch('/api/weather?' + p.toString());
      if (!r.ok) {
        this.weather = null;
        return;
      }
      const fresh = await r.json();
      // In-place reconcile instead of `this.weather = fresh`: replacing
      // the object reference makes Alpine tear down + rebuild the whole
      // widget subtree (the "updated section" visibly removed + re-added
      // = flicker on every refresh). Mutating the existing object keeps
      // its identity so Alpine updates the bound fields in place — the
      // refresh spinner is the only visible "loading" cue. Falls back to
      // assignment on first load when there's no object yet.
      const haveCurrent = this.weather && typeof this.weather === 'object';
      const haveFresh = fresh && typeof fresh === 'object';
      if (haveCurrent && haveFresh) {
        Object.keys(this.weather).forEach((k) => {
          if (!(k in fresh)) {
            delete this.weather[k];
          }
        });
        Object.assign(this.weather, fresh);
      } else {
        this.weather = fresh;
      }
      // Stamp the local "fetched at" so the Apps widget tile can
      // render a freshness label ("Updated 2m ago"). The backend
      // already sets `weather.fetched_at` from its own clock, but
      // the SPA stamps its own value too so the rendered relative
      // label always uses the SPA's clock (avoids tz / drift surprises
      // when the upstream returned a value from N minutes ago via
      // its cache).
      this._weatherFetchedAt = Date.now();
    } catch (_) {
      this.weather = null;
    }
  },
  // Manual refresh — same code path as the periodic ticker but
  // gated by `appsWidgetRefreshing.weather` so the spinner shows
  // while in-flight. Called from the Apps widget tile's refresh
  // button + the Public IP widget's matching button.
  async refreshWidget(kind) {
    if (!this.appsWidgetRefreshing) {
      this.appsWidgetRefreshing = {};
    }
    if (this.appsWidgetRefreshing[kind]) {
      return;
    }
    this.appsWidgetRefreshing[kind] = true;
    try {
      if (kind === 'weather' || kind === 'moon') {
        // Moon data comes from the same /api/weather response, so
        // refreshing weather refreshes the moon widget too. Pass
        // `force` so the BACKEND bypasses its per-coord TTL cache and
        // returns fresh data. Do NOT zero `_weatherFetchedAt` here (the
        // old approach): that blanked the "Updated Xs ago" freshness
        // label for the duration of the fetch, so the label text was
        // removed then re-added — a visible flicker on the card. The
        // stamp now holds its prior value until the fresh response
        // lands + `loadHeaderWeather` updates it.
        await this.loadHeaderWeather(true);
      } else if (kind === 'public_ip') {
        // Force-bypass the in-process cache by zeroing the stamp
        // BEFORE the call, so the helper's TTL gate doesn't
        // short-circuit on a still-warm cache.
        this._publicIpFetchedAt = 0;
        await this._ensurePublicIp();
      }
    } catch (_) {
    } finally {
      // Brief 250ms hold so the spinner animation reads as a real
      // event rather than a flash — feel-good UX, no functional
      // impact.
      setTimeout(() => {
        if (this.appsWidgetRefreshing) {
          this.appsWidgetRefreshing[kind] = false;
        }
      }, 250);
    }
  },
  // "Updated Ns/Nm/Nh ago" relative-time label for the per-widget
  // freshness chip. Reads from per-kind fetched-at stamps; returns
  // an empty string when no fetch has happened yet (the widget's
  // empty state covers that case).
  widgetFreshnessLabel(kind) {
    const now = this.hostHistoryNow || Date.now();
    let ts = 0;
    if (kind === 'weather' || kind === 'moon') {
      ts = this._weatherFetchedAt || 0;
    } else if (kind === 'public_ip') {
      ts = this._publicIpFetchedAt || 0;
    }
    if (!ts) {
      return '';
    }
    const delta = Math.max(0, Math.floor((now - ts) / 1000));
    if (delta < 60) {
      return (this.t('apps.custom.widget_freshness_seconds', {n: delta})
        || ('Updated ' + delta + 's ago'));
    }
    const m = Math.floor(delta / 60);
    if (m < 60) {
      return (this.t('apps.custom.widget_freshness_minutes', {n: m})
        || ('Updated ' + m + 'm ago'));
    }
    const h = Math.floor(m / 60);
    return (this.t('apps.custom.widget_freshness_hours', {n: h})
      || ('Updated ' + h + 'h ago'));
  },
  // Whether a given widget kind has a refresh button. Clock + system_stats
  // are client-side derivations; refresh wouldn't change anything.
  widgetSupportsRefresh(kind) {
    return kind === 'weather' || kind === 'moon' || kind === 'public_ip';
  },
  // Whether the widget actually has data to refresh / timestamp.
  // When the master feature is disabled, the upstream is unreachable,
  // or no data has loaded yet, showing the refresh button + "Updated
  // Xm ago" chip is meaningless and confusing — gate them on this.
  // Operator-flagged UX issue: "if the weather is disabled and no
  // data is displayed, meaningless to show refresh and updated when".
  widgetHasData(kind) {
    if (kind === 'weather' || kind === 'moon') {
      // configured === false means admin-disabled OR no API key OR
      // no URL — no point offering refresh. Otherwise need real temp.
      if (this.weather && this.weather.configured === false) {
        return false;
      }
      return !!(this.weather && this.weather.temp_c != null);
    }
    if (kind === 'public_ip') {
      if (this.publicIp && this.publicIp.enabled === false) {
        return false;
      }
      return !!(this.publicIp && this.publicIp.ip);
    }
    return true;
  },
  startHeaderWeather() {
    if (this._weatherTimer) {
      return;
    }
    // Fire-and-forget — the initial paint doesn't await; the 10-min
    // refresh ticker (below) drives subsequent fetches. `void` makes
    // the missing-await intent explicit to the IDE.
    void this.loadHeaderWeather();
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
      'sun': '<circle cx="12" cy="12" r="4.5"></circle><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"></path>',
      'cloud-sun': '<circle cx="7" cy="8" r="3"></circle><path d="M7 2v2M2 8h2M12 8h-.5M4 4l1 1M10 4L9 5"></path><path d="M20 17h-10.5a3.5 3.5 0 1 1 .8-6.9"></path>',
      'cloud': '<path d="M17 18H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 18z"></path>',
      'fog': '<path d="M3 9h18M3 13h18M3 17h12M7 5h14"></path>',
      'drizzle': '<path d="M17 14H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 14z"></path><path d="M9 18v2M13 18v2M17 18v2"></path>',
      'rain': '<path d="M17 14H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 14z"></path><path d="M8 16v4M12 18v4M16 16v4"></path>',
      'snow': '<path d="M17 14H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 14z"></path><path d="M8 17v1M12 19v1M16 17v1M8 20v1M12 22v.01M16 20v1"></path>',
      'sleet': '<path d="M17 14H7a5 5 0 0 1-.6-9.96 7 7 0 0 1 13.5 2.16A4 4 0 0 1 17 14z"></path><path d="M9 17v2M13 19v2M17 17v2M10 20h.01M14 18h.01"></path>',
      'thunder': '<path d="M19 16a5 5 0 0 0-1-9h-1.3a7 7 0 0 0-13.4 2"></path><polyline points="13 11 9 17 14 17 10 22"></polyline>',
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
    } catch (_) { /* silent — AI prompt just omits the block */
    }
  },

  // 10-min cache window matching `_ensurePublicIp`. Stored on
  // `this._publicIpHistoryCache` so the synchronous
  // `_buildAiPaletteContext` can fold it into the prompt block. Silent
  // failure leaves the cache empty / null — AI sees no history, says
  // it doesn't know, no hallucination. Backed by the admin-only
  // `/api/public-ip/history` endpoint.
  async _ensurePublicIpHistory() {
    const now = Date.now();
    if (Array.isArray(this._publicIpHistoryCache)
      && (now - (this._publicIpHistoryFetchedAt || 0)) < 10 * 60 * 1000) {
      return;
    }
    try {
      const r = await fetch('/api/public-ip/history?limit=10');
      if (!r.ok) {
        return;
      }
      const data = await r.json();
      this._publicIpHistoryCache = Array.isArray(data && data.history)
        ? data.history : [];
      this._publicIpHistoryFetchedAt = now;
    } catch (_) { /* silent — see comment above */
    }
  },

  // ============================================================
  // Weather — WeatherAPI.com section helpers + history cache for
  // the AI palette context-builder. The Admin → Weather tab follows
  // the canonical Test-before-Save gate pattern (see the project conventions
  // "Test-before-Save gate"); the section-owned save bundles the
  // per-section tunables + plain settings into ONE POST.
  // ============================================================
  _weatherSectionTuningKeys() {
    return [
      'tuning_weather_cache_ttl_seconds',
      'tuning_weather_fetch_timeout_seconds',
      'tuning_weather_history_retention_days',
      'tuning_weather_sampler_interval_seconds',
    ];
  },
  _weatherSectionPlainKeys() {
    // `weather_default_*` (label / lat / lon) intentionally OMITTED —
    // location is now per-user (Settings → Profile → Weather) and
    // the admin Weather tab no longer surfaces those fields. The
    // legacy SettingsIn fields stay on the wire for back-compat
    // seed round-trip but no SPA save writes to them.
    return [
      'weather_enabled',
      'weather_provider',
      'weather_api_base_url',
    ];
  },
  // Snapshot for dirty tracking AND for the Test-stamp comparison.
  // API key is intentionally INCLUDED — entering a new key OR clearing
  // an existing one MUST re-trigger a Test pass before Save unlocks.
  _weatherSnapshot() {
    const tuning = {};
    for (const k of this._weatherSectionTuningKeys()) {
      const v = (this.tuningForm || {})[k];
      tuning[k] = (v == null ? '' : String(v).trim());
    }
    const plain = {};
    for (const k of this._weatherSectionPlainKeys()) {
      plain[k] = (this.settings || {})[k];
    }
    return JSON.stringify({
      tuning,
      plain,
      api_key_dirty: !!((this.weatherForm || {}).api_key || ''),
      clear_api_key: !!((this.weatherForm || {}).clear_api_key),
    });
  },
  weatherTuningKeys() {
    return this._weatherSectionTuningKeys();
  },
  weatherSectionDirty() {
    try {
      const baseline = this._weatherBaselineSnapshot || '';
      return this._weatherSnapshot() !== baseline;
    } catch (_) {
      return false;
    }
  },
  canSaveWeather() {
    // Master toggle OFF — Save unconditional (operator may want to
    // commit the toggle change without re-typing the API key).
    if (!(this.settings && this.settings.weather_enabled)) {
      return true;
    }
    // Open-Meteo path — no API key, no Test gate. Save unconditional
    // when this provider is selected (it just hits the public endpoint
    // which is always reachable from the operator's network or not).
    if ((this.settings && this.settings.weather_provider) !== 'weatherapi') {
      return true;
    }
    // WeatherAPI path — Test-before-Save gate. Last passing-Test
    // snapshot MUST match the live snapshot.
    return !!(this._weatherLastPassedTest
      && this._weatherLastPassedTest === this._weatherSnapshot());
  },
  markWeatherDirty() {
    // Stub — the snapshot diff drives dirtiness; this method exists
    // for symmetry with the other section helpers and so future
    // hooks (debounced auto-validate, live preview) have a single
    // attach point.
  },
  // The SPA saves the user's weather location under `headerWeather*`
  // camelCase keys (see save-prefs payload in app-admin.js). Read
  // those FIRST with legacy `weather_*` fallback for back-compat
  // with older preference shapes.
  weatherProfileLocationAvailable() {
    const p = (this.me && this.me.ui_prefs) || {};
    const lat = Number(p.headerWeatherLat != null ? p.headerWeatherLat : p.weather_lat);
    const lon = Number(p.headerWeatherLon != null ? p.headerWeatherLon : p.weather_lon);
    return Number.isFinite(lat) && Number.isFinite(lon);
  },
  weatherUseProfileLocation() {
    const p = (this.me && this.me.ui_prefs) || {};
    if (!this.weatherProfileLocationAvailable()) {
      return;
    }
    const lat = p.headerWeatherLat != null ? p.headerWeatherLat : p.weather_lat;
    const lon = p.headerWeatherLon != null ? p.headerWeatherLon : p.weather_lon;
    const label = p.headerWeatherLabel || p.weather_label || '';
    this.settings.weather_default_lat = String(lat || '');
    this.settings.weather_default_lon = String(lon || '');
    if (label) {
      this.settings.weather_default_label = String(label);
    }
    this.markWeatherDirty();
  },
  async testWeather() {
    if (this.weatherTesting) {
      return;
    }
    this.weatherTesting = true;
    this.weatherTestResult = {ok: false, detail: ''};
    try {
      const body = {
        provider: (this.settings && this.settings.weather_provider) || 'open-meteo',
        api_key: (this.weatherForm && this.weatherForm.api_key) || '',
        base_url: this.settings.weather_api_base_url || '',
        lat: this.settings.weather_default_lat || '',
        lon: this.settings.weather_default_lon || '',
      };
      const r = await fetch('/api/weather/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        this.weatherTestResult = {
          ok: false,
          detail: (j && j.detail) || ('HTTP ' + r.status),
        };
      } else {
        this.weatherTestResult = {
          ok: !!(j && j.ok),
          detail: (j && j.detail) || '',
        };
        if (j && j.ok) {
          // Stamp the passing snapshot so canSaveWeather() unlocks.
          // Only meaningful for WeatherAPI — Open-Meteo path bypasses
          // the gate via canSaveWeather()'s provider check.
          this._weatherLastPassedTest = this._weatherSnapshot();
        }
      }
    } catch (e) {
      this.weatherTestResult = {ok: false, detail: String(e.message || e)};
    } finally {
      this.weatherTesting = false;
    }
  },
  async saveWeatherSection() {
    if (this.weatherSaving) {
      return;
    }
    // Validate each tunable against its declared bounds before POST.
    for (const k of this._weatherSectionTuningKeys()) {
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
    this.weatherSaving = true;
    try {
      const body = {};
      // Per-section tunables + plain settings + secret bits all
      // ride one POST so a single Save commits the whole weather
      // configuration (the project conventions "Section-owned save pattern").
      for (const k of this._weatherSectionTuningKeys()) {
        const v = (this.tuningForm || {})[k];
        body[k] = (v == null ? '' : String(v).trim());
      }
      for (const k of this._weatherSectionPlainKeys()) {
        body[k] = (this.settings || {})[k];
      }
      // API key — keep-current-if-blank contract. Clearing the key
      // requires the explicit clear flag set from the UI Clear button.
      const wf = this.weatherForm || {};
      if (wf.clear_api_key) {
        body.clear_weather_api_key = true;
      } else if ((wf.api_key || '').trim()) {
        body.weather_api_key = wf.api_key.trim();
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
      // Reset the form's secret-input box + clear flag so the next
      // edit starts clean. Persisted `api_key_set` flag drives the
      // input placeholder going forward.
      this.weatherForm = {api_key: '', clear_api_key: false};
      // Drop the SPA topbar widget's local cache so the next
      // fetch lands the fresh provider output.
      this.weather = null;
      this._weatherFetchedAt = 0;
      this._weatherBaselineSnapshot = this._weatherSnapshot();
      this.showToast(this.t('toasts.saved') || 'Saved', 'success');
    } catch (e) {
      this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed')
        + ': ' + (e.message || e), 'error');
    } finally {
      this.weatherSaving = false;
    }
  },

  // Admin > Weather "Recent samples" panel — admin-only display
  // of the last N rows from `weather_samples`. Triggered by the
  // `<details>` toggle on first expand; no auto-poll (operator
  // clicks expand → fetch fires once). 25-row cap so the panel
  // stays compact even on a long-running deploy.
  async loadWeatherHistory() {
    if (this.weatherHistoryLoading) {
      return;
    }
    this.weatherHistoryLoading = true;
    try {
      const r = await fetch('/api/weather/history?limit=25');
      if (!r.ok) {
        this.weatherHistory = [];
        return;
      }
      const data = await r.json();
      this.weatherHistory = Array.isArray(data && data.history)
        ? data.history : [];
    } catch (_) {
      this.weatherHistory = [];
    } finally {
      this.weatherHistoryLoading = false;
    }
  },

  // 10-min cache window matching `_ensurePublicIpHistory`. Stored on
  // `this._weatherHistoryCache` so the synchronous
  // `_buildAiPaletteContext` can fold it into the prompt block. The
  // AI consumer reads "what was the weather yesterday" / "moon phase
  // last night" against this cache — silent failure leaves it empty,
  // AI says it doesn't know, no hallucination.
  async _ensureWeatherHistory() {
    const now = Date.now();
    if (Array.isArray(this._weatherHistoryCache)
      && (now - (this._weatherHistoryFetchedAt || 0)) < 10 * 60 * 1000) {
      return;
    }
    try {
      // 168 = one full week of hourly samples; gives the AI enough
      // resolution to answer "highest temp this week", "rainiest day",
      // and "when was the last full moon" without re-fetching.
      const r = await fetch('/api/weather/history?limit=168');
      if (!r.ok) {
        return;
      }
      const data = await r.json();
      this._weatherHistoryCache = Array.isArray(data && data.history)
        ? data.history : [];
      this._weatherHistoryFetchedAt = now;
    } catch (_) { /* silent — see _ensurePublicIpHistory */
    }
  },
};
