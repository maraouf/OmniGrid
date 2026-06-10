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
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, module: true, -W069 */

// Custom-dashboard WEATHER widget module.
//
// Per-widget render helpers (hero temp + forecast strip + per-card
// location/unit overrides). The shared weather STATE (`this.weather`) +
// fetch (`loadHeaderWeather`) live in app-topbar.js (topbar chip shares
// them); `appsWeatherIconId` stays in app-apps.js (the generic tile
// decoration icon + topbar read it). This module references those via
// `this.` — everything merges into one Alpine component.
//
// See clock.js for the per-widget module contract.

export const helpers = {
  // Per-card weather payload — when the item carries a follow_user=false
  // override with custom (lat,lng), returns the per-(lat,lng) fetch
  // from `_perCardWeatherCache`; else returns the global `this.weather`.
  // The fetch helper `_ensurePerCardWeather(item)` triggers a one-shot
  // GET on first read; subsequent reads serve from cache until the
  // TTL expires (matching the existing 10-min topbar cache window).
  // Module-scope cache keyed on `<lat>:<lng>` so multiple cards at the
  // SAME location share one fetch.
  effectiveWeather(item) {
    const opts = this.effectiveWeatherOpts(item);
    if (opts.follow || opts.lat === null || opts.lng === null) {
      return this.weather || null;
    }
    if (!this._perCardWeatherCache) {
      this._perCardWeatherCache = {};
    }
    const key = opts.lat.toFixed(4) + ':' + opts.lng.toFixed(4);
    const entry = this._perCardWeatherCache[key];
    return (entry && entry.data) || null;
  },
  _ensurePerCardWeather(item) {
    const opts = this.effectiveWeatherOpts(item);
    if (opts.follow || opts.lat === null || opts.lng === null) {
      return;
    }
    if (!this._perCardWeatherCache) {
      this._perCardWeatherCache = {};
    }
    const key = opts.lat.toFixed(4) + ':' + opts.lng.toFixed(4);
    const now = Date.now();
    const entry = this._perCardWeatherCache[key];
    if (entry && (now - entry.ts) < 10 * 60 * 1000) {
      return;
    }
    // Mark in-flight so a rapid re-render doesn't fan out N fetches
    // for the same key during the first paint.
    if (entry && entry.pending) {
      return;
    }
    this._perCardWeatherCache[key] = {
      ts: now, data: (entry && entry.data) || null, pending: true,
    };
    try {
      const labelParam = opts.label ? ('&label=' + encodeURIComponent(opts.label)) : '';
      fetch('/api/weather?lat=' + opts.lat + '&lon=' + opts.lng + labelParam)
        .then(r => r.ok ? r.json() : null)
        .then(j => {
          // A null body (HTTP error) or a configured+error / null-temp
          // body (e.g. WeatherAPI quota) is NOT a usable reading — keep
          // the last-known-good (stale) so an override-mode card shows the
          // last real values instead of going blank. Mark stale + preserve
          // the prior `ts` so the freshness label keeps aging from the last
          // good fetch. Only overwrite when the prior value wasn't good
          // either (so a genuine first-load empty still surfaces).
          const jOk = j && typeof j === 'object' && j.temp_c != null;
          const priorEntry = this._perCardWeatherCache[key] || {};
          const priorData = priorEntry.data;
          if (!jOk && priorData && priorData.temp_c != null) {
            priorData._stale = true;
            if (j && j.error) {
              priorData._stale_error = j.error;
            }
            this._perCardWeatherCache[key] = {
              ts: priorEntry.ts || now, data: priorData, pending: false,
            };
          } else {
            this._perCardWeatherCache[key] = {
              ts: Date.now(), data: j || null, pending: false,
            };
          }
        })
        .catch(() => {
          // Leave the stale data + clear pending so a future re-render
          // can retry the fetch on the next cache window.
          this._perCardWeatherCache[key] = {
            ts: now, data: (entry && entry.data) || null, pending: false,
          };
        });
    } catch (_) {
      this._perCardWeatherCache[key] = {
        ts: now, data: null, pending: false,
      };
    }
  },
  // Format a temperature value (the backend stores Celsius) honouring
  // the per-card units override (Fahrenheit when units=imperial). Used
  // by the hero temp + the forecast strip so every °-display reflects
  // the operator's choice.
  appsWidgetWeatherTemp(item, tempC) {
    if (tempC === null || tempC === undefined || isNaN(tempC)) {
      return '';
    }
    const opts = this.effectiveWeatherOpts(item);
    if (!opts.follow && opts.units === 'imperial') {
      const f = (tempC * 9 / 5) + 32;
      return Math.round(f) + '°F';
    }
    return Math.round(tempC) + '°C';
  },
  // Wind value + unit — mirrors appsWidgetWeatherTemp's per-card units
  // handling so an imperial-units card shows mph (not km/h), and routes
  // the unit through t() (the binding previously hard-appended a raw
  // English/metric ' km/h' with no i18n + ignored the units toggle).
  appsWidgetWindLabel(item, kmh) {
    if (kmh === null || kmh === undefined || isNaN(kmh)) {
      return '';
    }
    const opts = this.effectiveWeatherOpts(item);
    if (!opts.follow && opts.units === 'imperial') {
      const mph = Math.round(kmh * 0.621371);
      return this.t('apps.custom.widget_weather_wind_mph', {n: mph}) || (mph + ' mph');
    }
    const k = Math.round(kmh);
    return this.t('apps.custom.widget_weather_wind_kmh', {n: k}) || (k + ' km/h');
  },
  // N-day rollup for the weather widget's forecast strip. Cap N from
  // the per-card override (`weather_forecast_days`, default 4, range
  // 0-7). N=0 → empty array → markup hides the forecast strip
  // entirely. The forecast source is `effectiveWeather(item).forecast`
  // so override-mode cards see THEIR location's forecast.
  appsWidgetWeatherForecast(item) {
    const w = this.effectiveWeather(item) || {};
    const f = Array.isArray(w.forecast) ? w.forecast : [];
    if (!f.length) {
      return [];
    }
    const opts = item ? this.effectiveWeatherOpts(item) : null;
    const cap = opts ? opts.forecast_days : 4;
    if (cap <= 0) {
      return [];
    }
    // Skip today (forecast[0]) when there are MORE than `cap` future
    // days available — today's hero already shows today. When the
    // forecast is short (only today + a few), include today in the
    // strip so the card isn't empty.
    const start = f.length > cap ? 1 : 0;
    const out = [];
    for (let i = start; i < start + cap && i < f.length; i++) {
      const day = f[i] || {};
      let name = '';
      try {
        if (day.date) {
          name = new Date(day.date).toLocaleDateString([], {weekday: 'short'});
        }
      } catch (_) {
        name = '';
      }
      // Pre-format hi/lo using the per-card units helper so the markup
      // can bind `x-text="d.hi_label"` regardless of source.
      const hi = day.temp_max_c != null ? day.temp_max_c : null;
      const lo = day.temp_min_c != null ? day.temp_min_c : null;
      out.push({
        name: name || ('+' + (i - start + 1) + 'd'),
        hi: hi != null ? Math.round(hi) : null,
        lo: lo != null ? Math.round(lo) : null,
        hi_label: this.appsWidgetWeatherTemp(item, hi),
        lo_label: this.appsWidgetWeatherTemp(item, lo),
        icon: this.appsWeatherIconId(day.condition || w.condition || ''),
      });
    }
    return out;
  },
  // True when the weather widget should render the condition row
  // (rain / cloudy / wind chips). Per-card opt; default true.
  appsWidgetWeatherShowConditions(item) {
    if (!item) {
      return true;
    }
    return this.effectiveWeatherOpts(item).show_conditions;
  },
  // Effective Weather-widget options — same shape as clock. Falls back
  // to the global weather settings (this.weather + scheduler defaults)
  // when follow_user=true OR the override field is unset.
  effectiveWeatherOpts(item) {
    const opts = (item && item.opts) || {};
    const follow = (opts.follow_user !== false);
    const units = (!follow && opts.weather_units) || '';  // '' = follow user prefs
    const lat = (!follow && typeof opts.weather_lat === 'number') ? opts.weather_lat : null;
    const lng = (!follow && typeof opts.weather_lng === 'number') ? opts.weather_lng : null;
    const label = (!follow && opts.weather_label) || '';
    const fcdRaw = (typeof opts.weather_forecast_days === 'number')
      ? opts.weather_forecast_days : 4;  // default = 4 days
    const showCond = (opts.weather_show_conditions !== false);  // default = show
    return {
      follow,
      units: (units === 'imperial' ? 'imperial' : (units === 'metric' ? 'metric' : '')),
      lat,
      lng,
      label,
      forecast_days: Math.max(0, Math.min(7, fcdRaw)),
      show_conditions: showCond,
    };
  },
};

export const widget = {
  kind: 'weather',
  supportsRefresh: true,
  // Dynamic decoration: the current condition's weather glyph.
  decorationIcon(c) {
    const condition = (c.weather && c.weather.condition) || '';
    const slug = (typeof c.appsWeatherIconId === 'function')
      ? c.appsWeatherIconId(condition)
      : 'icon-weather-cloud';
    return slug.startsWith('icon-') ? slug : ('icon-' + slug);
  },
  freshnessObj(c) {
    return c.weather;
  },
  hasData(c) {
    if (c.weather && c.weather.configured === false) {
      return false;
    }
    return !!(c.weather && c.weather.temp_c != null);
  },
  refresh(c) {
    return c.loadHeaderWeather(true);
  },
};
