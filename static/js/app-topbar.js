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
  userLat: (typeof localStorage !== 'undefined' ? (parseFloat(localStorage.getItem('userLat') || '') || null) : null),
  userLon: (typeof localStorage !== 'undefined' ? (parseFloat(localStorage.getItem('userLon') || '') || null) : null),
  userLabel: (typeof localStorage !== 'undefined' ? (localStorage.getItem('userLabel') || '') : ''),
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
  // `publicIp.enabled` is the backend's `public_ip_enabled`
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
  // Admin → Prayer Times state (section-owned save spanning the
  // enable/cache/timeout tunables AND the method/school/location
  // settings). Same Save-button-dirty + Test-connection shape.
  prayerTimesSaving: false,
  prayerTimesTesting: false,
  prayerTimesTestOk: false,
  prayerTimesTestResult: '',
  // Admin → Public IP "Recent samples" table state (mirrors weatherHistory).
  publicIpHistory: [],
  publicIpHistoryLoading: false,

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
  // Admin → Prayer Times "Recent samples" table — newest-first rows
  // from prayer_times_samples (one row per day per location). Lazy-loaded
  // on first expand of the samples <details> + by its refresh button.
  prayerHistory: [],
  prayerHistoryLoading: false,
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

  // Admin → Public IP — section-owned save. The master enable toggle is
  // the plain `public_ip_enabled` SETTING (like weather_enabled — loads
  // with settingsLoaded, NOT a tunable); cache TTL + fetch timeout +
  // sample interval are operator-tunable numerics. One Save POST carries
  // both groups.
  _publicIpSectionTuningKeys() {
    return [
      'tuning_public_ip_cache_ttl_seconds',
      'tuning_public_ip_fetch_timeout_seconds',
      'tuning_public_ip_sample_interval_seconds',
    ];
  },
  // [editable flat settings key, loaded nested key under settings.public_ip].
  _publicIpSettingFields() {
    return [
      ['public_ip_enabled', 'enabled'],
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
    const s = this.settings || {};
    const nested = s.public_ip || {};
    for (const pair of this._publicIpSettingFields()) {
      const cur = (s[pair[0]] == null ? '' : String(s[pair[0]]).trim());
      const base = (nested[pair[1]] == null ? '' : String(nested[pair[1]]).trim());
      if (cur !== base) {
        return true;
      }
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
      const s = this.settings || {};
      for (const pair of this._publicIpSettingFields()) {
        const v = s[pair[0]];
        body[pair[0]] = (v == null ? '' : String(v).trim());
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
      // Re-baseline settings + tunables, then FORCE-re-probe /api/public-ip
      // so the apps-page public-IP widget tile repopulates immediately with
      // the new gate state — without this it kept the stale pre-save
      // {enabled:false} (or null) and showed "no data" until a full reload
      // (the widget gates on this.publicIp, which only refreshes on its own
      // 10-min cycle / re-mount). No /api/me refresh needed — the widget
      // reads this.publicIp, not a client_config flag.
      await Promise.all([this.loadSettings(), this.loadTuning()]);
      try {
        this._publicIpFetchedAt = 0;
        await this._ensurePublicIp(true);
      } catch (_) { /* best-effort live-apply; reload still works */
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
      // `?test=1` tells the backend this is an explicit Test action so it
      // stamps last_test_success_public_ip (the widget's own background
      // fetches omit it, so the "Last tested" label only advances on a
      // real operator test — not on every widget refresh).
      const r = await fetch('/api/public-ip?test=1', {credentials: 'same-origin'});
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        this.publicIpTestOk = false;
        this.publicIpTestResult = 'HTTP ' + r.status + ': ' + (j.detail || JSON.stringify(j));
      } else if (j && j.error) {
        this.publicIpTestOk = false;
        this.publicIpTestResult = j.error;
      } else {
        this.publicIpTestOk = true;
        this.publicIpTestResult = JSON.stringify(j, null, 2);
        // Optimistic last-tested stamp so the "Last tested" label appears
        // immediately; the backend (?test=1) also persists it for reload.
        if (this.recordTestSuccess) {
          this.recordTestSuccess('public_ip');
        }
      }
    } catch (e) {
      this.publicIpTestOk = false;
      this.publicIpTestResult = 'fetch failed: ' + (e && e.message ? e.message : String(e));
    } finally {
      this.publicIpTesting = false;
    }
  },

  // --- Admin → Prayer Times (section-owned save: tunables + settings) ---
  // The cache-TTL / fetch-timeout are TUNABLES; the enable toggle +
  // method / Asr school / fallback-location / base-URL are DB-backed
  // settings (enable is a plain bool like weather_enabled, so it loads
  // with settingsLoaded — NOT a tunable). One Save POST carries both.
  // Dirty = any of either group differs from its loaded baseline.
  _prayerTimesSectionTuningKeys() {
    return [
      'tuning_prayer_times_cache_ttl_seconds',
      'tuning_prayer_times_fetch_timeout_seconds',
      'tuning_prayer_times_sampler_interval_seconds',
      'tuning_prayer_times_history_retention_days',
      'tuning_prayer_times_reminder_lead_minutes',
      'tuning_prayer_times_reminder_check_interval_seconds',
    ];
  },
  // [editable flat settings key, loaded nested key under settings.prayer_times].
  _prayerTimesSettingFields() {
    return [
      ['prayer_times_enabled', 'enabled'],
      ['prayer_times_method', 'method'],
      ['prayer_times_school', 'school'],
      ['prayer_times_default_label', 'default_label'],
      ['prayer_times_default_lat', 'default_lat'],
      ['prayer_times_default_lon', 'default_lon'],
      ['prayer_times_api_base_url', 'api_base_url'],
    ];
  },
  prayerTimesSectionDirty() {
    try {
      const baseline = this._tuningBaselineMap();
      for (const k of this._prayerTimesSectionTuningKeys()) {
        const cur = (this.tuningForm || {})[k];
        const curStr = (cur == null ? '' : String(cur).trim());
        const baseStr = (baseline[k] == null ? '' : String(baseline[k]).trim());
        if (curStr !== baseStr) {
          return true;
        }
      }
    } catch (_) {
    }
    const s = this.settings || {};
    const nested = s.prayer_times || {};
    for (const pair of this._prayerTimesSettingFields()) {
      const cur = (s[pair[0]] == null ? '' : String(s[pair[0]]).trim());
      const base = (nested[pair[1]] == null ? '' : String(nested[pair[1]]).trim());
      if (cur !== base) {
        return true;
      }
    }
    return false;
  },
  async savePrayerTimesSection() {
    if (this.prayerTimesSaving) {
      return;
    }
    // Validate the numeric tunables against their (min, max) bounds first.
    for (const k of this._prayerTimesSectionTuningKeys()) {
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
    this.prayerTimesSaving = true;
    try {
      const body = {};
      for (const k of this._prayerTimesSectionTuningKeys()) {
        const v = (this.tuningForm || {})[k];
        body[k] = (v == null ? '' : String(v).trim());
      }
      const s = this.settings || {};
      for (const pair of this._prayerTimesSettingFields()) {
        const v = s[pair[0]];
        body[pair[0]] = (v == null ? '' : String(v).trim());
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
      // Refresh me.client_config so the apps-page prayer widget tile +
      // topbar widget pick up the new enabled state immediately. The
      // widget gates on `me.client_config.prayer_times_enabled` (set from
      // /api/me at page-load); without this re-fetch it stays stale-false
      // until a full reload, so a just-enabled feature reads as disabled.
      try {
        const rm = await fetch('/api/me');
        if (rm.ok) {
          const me = await rm.json();
          if (me && me.client_config) {
            this.me = me;
          }
        }
      } catch (_) { /* live-apply best-effort; reload still works */
      }
      // Invalidate the SPA's prayer cache so the widget re-fetches with
      // the new method / school / location on its next mount.
      try {
        this.prayer = null;
        this._prayerFetchedAt = 0;
      } catch (_) {
      }
      this.showToast(this.t('toasts.saved') || 'Saved', 'success');
    } catch (e) {
      this.showToast((this.t('toasts_extra.save_failed_generic') || 'Save failed') + ': ' + (e.message || e), 'error');
    } finally {
      this.prayerTimesSaving = false;
    }
  },
  async testPrayerTimes() {
    if (this.prayerTimesTesting) {
      return;
    }
    this.prayerTimesTesting = true;
    this.prayerTimesTestResult = '';
    this.prayerTimesTestOk = false;
    try {
      const s = this.settings || {};
      // Resolve test coords: the Admin global default first, then fall
      // back to the admin's own saved location (the same
      // source the widget uses) so a Test works even before a global
      // default is set — matching the operator's "the data is in the
      // topbar widget" expectation.
      const lat = s.prayer_times_default_lat
        || (this.userLat != null ? String(this.userLat) : '');
      const lon = s.prayer_times_default_lon
        || (this.userLon != null ? String(this.userLon) : '');
      const label = s.prayer_times_default_label || this.userLabel || '';
      const body = {
        lat: lat,
        lon: lon,
        label: label,
        method: s.prayer_times_method || '',
        school: s.prayer_times_school || '',
      };
      const r = await fetch('/api/prayer-times/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify(body),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        this.prayerTimesTestOk = false;
        this.prayerTimesTestResult = 'HTTP ' + r.status + ': ' + (j.detail || JSON.stringify(j));
      } else if (j && j.ok) {
        this.prayerTimesTestOk = true;
        this.prayerTimesTestResult = j.detail || 'OK';
        if (this.recordTestSuccess) {
          this.recordTestSuccess('prayer_times');
        }
      } else {
        this.prayerTimesTestOk = false;
        this.prayerTimesTestResult = (j && j.detail) || 'Test failed';
      }
    } catch (e) {
      this.prayerTimesTestOk = false;
      this.prayerTimesTestResult = 'fetch failed: ' + (e && e.message ? e.message : String(e));
    } finally {
      this.prayerTimesTesting = false;
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
      || this.userLat == null
      || this.userLon == null) {
      this.weather = null;
      return;
    }
    try {
      const p = new URLSearchParams({
        lat: String(this.userLat),
        lon: String(this.userLon),
        label: this.userLabel || '',
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
        this._markWeatherStale();
        return;
      }
      const fresh = await r.json();
      // The backend can return a configured + in-body-error / null-temp
      // body (e.g. a WeatherAPI quota / key error surfaced in-body) — that
      // is NOT a usable reading. KEEP the last-known-good this.weather
      // (stale) instead of overwriting it with the empty result, so the
      // widget still shows the last real reading and the freshness footer
      // ages it ("Updated 2h ago") instead of going blank.
      if (!fresh || typeof fresh !== 'object' || fresh.temp_c == null) {
        this._markWeatherStale(fresh && fresh.error);
        return;
      }
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
      this._markWeatherStale();
    }
  },
  // Backend weather fetch failed (HTTP error, network error, OR a
  // configured+error / null-temp body like a WeatherAPI quota error).
  // Keep the last-known-good `this.weather` so the widget still shows the
  // last real reading, mark it `_stale`, and DON'T bump `_weatherFetchedAt`
  // so the "Updated X ago" freshness footer keeps aging from the last GOOD
  // fetch — that growing age is the "this data is a bit old" signal the
  // user asked for. Only blank when there's no prior good value to fall
  // back to (first-ever fetch failed). The `_stale` / `_stale_error` keys
  // auto-clear on the next good fetch via loadHeaderWeather's reconcile
  // (keys absent from the fresh body are deleted). Moon reads the same
  // `this.weather`, so it inherits the stale-but-shown behaviour for free.
  _markWeatherStale(err) {
    if (this.weather && typeof this.weather === 'object' && this.weather.temp_c != null) {
      this.weather._stale = true;
      if (err) {
        this.weather._stale_error = String(err);
      }
    } else {
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
        // Pass `force` so BOTH the client TTL gate AND the backend cache
        // are bypassed, WITHOUT zeroing `_publicIpFetchedAt` (the old
        // approach blanked the "Updated Xs ago" label mid-fetch — the
        // same flicker fixed for weather). The stamp holds its prior
        // value until the fresh response lands; the in-place reconcile in
        // `_ensurePublicIp` keeps the card subtree mounted.
        await this._ensurePublicIp(true);
      } else if (kind === 'prayer_times') {
        // force=True bypasses both the client TTL gate + the backend
        // per-(coord, method, school) cache, without zeroing
        // `_prayerFetchedAt` (avoids the freshness-label flicker). The
        // in-place reconcile in `_ensurePrayerTimes` keeps the subtree
        // mounted.
        await this._ensurePrayerTimes(true);
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
    } else if (kind === 'prayer_times') {
      ts = this._prayerFetchedAt || 0;
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
  // "<N><unit> ago" label for an epoch-SECONDS timestamp (the public-IP
  // last_change.ts shape). Distinct from widgetFreshnessLabel (kind-keyed
  // + reads ms fetched-at stamps). Adds a day tier since an IP can sit
  // unchanged for weeks. Empty string for a missing / zero ts so the
  // bound element collapses. Reuses hostHistoryNow (1s tick) so the label
  // counts up live without its own timer.
  appsRelativeTime(tsSeconds) {
    const ts = Number(tsSeconds) || 0;
    if (ts <= 0) {
      return '';
    }
    const nowSec = Math.floor((this.hostHistoryNow || Date.now()) / 1000);
    const sec = Math.max(0, nowSec - ts);
    const suffix = ' ' + (this.t('common.ago') || 'ago');
    if (sec < 60) {
      return sec + 's' + suffix;
    }
    if (sec < 3600) {
      return Math.floor(sec / 60) + 'm' + suffix;
    }
    if (sec < 86400) {
      return Math.floor(sec / 3600) + 'h' + suffix;
    }
    return Math.floor(sec / 86400) + 'd' + suffix;
  },
  // Whether a given widget kind has a refresh button. Clock + system_stats
  // are client-side derivations; refresh wouldn't change anything.
  widgetSupportsRefresh(kind) {
    return ['weather', 'moon', 'public_ip', 'prayer_times'].includes(kind);
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
    if (kind === 'prayer_times') {
      if (!this.prayer || this.prayer.configured === false) {
        return false;
      }
      if (!this.prayer.timings) {
        return false;
      }
      if (typeof this.prayerWidgetRows !== 'function') {
        return false;
      }
      return this.prayerWidgetRows().length > 0;
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
  // Backend gates on `public_ip_enabled` (default OFF) and
  // caches per `tuning_public_ip_cache_ttl_seconds` — this SPA-side
  // cache layers on top so a burst of palette calls inside the
  // same cache window doesn't re-fetch even from the warm backend
  // cache. Fire-and-forget;
  // a slow/failing fetch never blocks the AI call.
  async _ensurePublicIp(force = false) {
    const now = Date.now();
    if (!force && this.publicIp && (now - this._publicIpFetchedAt) < 10 * 60 * 1000) {
      return;
    }
    try {
      const r = await fetch('/api/public-ip' + (force ? '?force=1' : ''));
      if (!r.ok) {
        return;
      }
      const fresh = await r.json();
      // In-place reconcile (same anti-flicker pattern as loadHeaderWeather):
      // mutate the existing object instead of replacing the reference, so
      // Alpine keeps the bound subtree (incl. the "Updated Xs ago"
      // freshness label) mounted + just updates fields. Replacing the
      // reference tore the subtree down + rebuilt it, blanking the
      // freshness label for a frame (operator-flagged flicker on Refresh).
      const haveCurrent = this.publicIp && typeof this.publicIp === 'object';
      const haveFresh = fresh && typeof fresh === 'object';
      if (haveCurrent && haveFresh) {
        Object.keys(this.publicIp).forEach((k) => {
          if (!(k in fresh)) {
            delete this.publicIp[k];
          }
        });
        Object.assign(this.publicIp, fresh);
      } else {
        this.publicIp = fresh;
      }
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
    // location is now per-user (Settings → Profile) and
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
  // The SPA saves the user's location under `userLat/userLon/userLabel`
  // camelCase keys (see save-prefs payload in app-admin.js). Read
  // those FIRST with legacy `weather_*` fallback for back-compat
  // with older preference shapes.
  weatherProfileLocationAvailable() {
    const p = (this.me && this.me.ui_prefs) || {};
    const lat = Number(p.userLat != null ? p.userLat : p.weather_lat);
    const lon = Number(p.userLon != null ? p.userLon : p.weather_lon);
    return Number.isFinite(lat) && Number.isFinite(lon);
  },
  weatherUseProfileLocation() {
    const p = (this.me && this.me.ui_prefs) || {};
    if (!this.weatherProfileLocationAvailable()) {
      return;
    }
    const lat = p.userLat != null ? p.userLat : p.weather_lat;
    const lon = p.userLon != null ? p.userLon : p.weather_lon;
    const label = p.userLabel || p.weather_label || '';
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
          // Optimistic last-tested stamp (backend /api/weather/test also
          // persists it via _stamp_test_success for cross-reload).
          if (this.recordTestSuccess) {
            this.recordTestSuccess('weather');
          }
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

  // Admin → Prayer Times "Recent samples" table loader — mirrors
  // loadWeatherHistory. Reads the admin-only /api/prayer-times/history
  // (newest-first prayer_times_samples rows). Lazy-loaded on first
  // expand of the samples <details> + by its refresh button.
  async loadPrayerHistory() {
    if (this.prayerHistoryLoading) {
      return;
    }
    this.prayerHistoryLoading = true;
    try {
      const r = await fetch('/api/prayer-times/history?limit=30');
      if (!r.ok) {
        this.prayerHistory = [];
        return;
      }
      const data = await r.json();
      this.prayerHistory = Array.isArray(data && data.history)
        ? data.history : [];
    } catch (_) {
      this.prayerHistory = [];
    } finally {
      this.prayerHistoryLoading = false;
    }
  },

  // ── Prayer reminders (Profile → Notifications card) ──────────────
  // Per-user opt-in + medium selection, stored in ui_prefs.prayer_reminders
  // = {enabled, mediums:{app,telegram,apprise}}. Saved immediately on each
  // toggle via the free-form /api/me/ui-prefs PATCH (same fire-and-forget
  // pattern as the other ui_prefs toggles — no page-level Save needed).
  // The lead time is the admin tunable; the backend reminder loop reads
  // this pref per user.
  _prayerReminderPref() {
    const prefs = (this.me && this.me.ui_prefs) || {};
    const p = prefs.prayer_reminders || {};
    const med = p.mediums || {};
    return {
      enabled: !!p.enabled,
      mediums: {app: !!med.app, telegram: !!med.telegram, apprise: !!med.apprise},
    };
  },
  prayerReminderEnabled() {
    return this._prayerReminderPref().enabled;
  },
  prayerReminderMedium(m) {
    return !!this._prayerReminderPref().mediums[m];
  },
  _persistPrayerReminders(obj) {
    if (!this.me) {
      return;
    }
    if (!this.me.ui_prefs) {
      this.me.ui_prefs = {};
    }
    this.me.ui_prefs.prayer_reminders = obj;
    // Fire-and-forget — the CSRF header is attached by the global fetch
    // wrapper; failure leaves the in-memory pref set so the next toggle
    // re-attempts the write.
    fetch('/api/me/ui-prefs', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prefs: {prayer_reminders: obj}}),
    }).catch(() => {
      // best-effort persistence — next toggle re-attempts the write
    });
  },
  togglePrayerReminderEnabled(on) {
    const cur = this._prayerReminderPref();
    cur.enabled = !!on;
    this._persistPrayerReminders(cur);
  },
  togglePrayerReminderMedium(m, on) {
    const cur = this._prayerReminderPref();
    cur.mediums[m] = !!on;
    this._persistPrayerReminders(cur);
  },

  // Admin → Public IP "Recent samples" table loader — mirrors
  // loadWeatherHistory. Reads the admin-only /api/public-ip/history
  // (newest-first public_ip_history rows). Lazy-loaded on first expand of
  // the samples <details> + by its refresh button. `publicIpHistory` /
  // `publicIpHistoryLoading` are the table-bound state (distinct from the
  // AI-palette `_publicIpHistoryCache` so a manual refresh here doesn't
  // perturb the palette's 10-min cache).
  async loadPublicIpHistory() {
    if (this.publicIpHistoryLoading) {
      return;
    }
    this.publicIpHistoryLoading = true;
    try {
      const r = await fetch('/api/public-ip/history?limit=50');
      if (!r.ok) {
        this.publicIpHistory = [];
        return;
      }
      const data = await r.json();
      this.publicIpHistory = Array.isArray(data && data.history)
        ? data.history : [];
    } catch (_) {
      this.publicIpHistory = [];
    } finally {
      this.publicIpHistoryLoading = false;
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
