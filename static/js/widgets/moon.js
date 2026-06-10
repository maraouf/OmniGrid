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

// Custom-dashboard MOON-PHASE widget module.
//
// Pure render helpers over the shared `this.weather.forecast[0]` data
// (populated by app-topbar.js's `loadHeaderWeather`). Moon data only
// exists when WeatherAPI.com is the active provider; the tile's empty
// state covers the Open-Meteo case.
//
// See clock.js for the per-widget module contract.

export const helpers = {
  // Moon-phase data for the moon widget — pulls from the active
  // weather provider's `forecast[0].moon_*` block. Returns null
  // when the provider doesn't support moon data (Open-Meteo) or
  // when there's no weather payload yet. Caller in the widget
  // template gates on null so the empty state renders cleanly.
  // SVG ring: circle perimeter at r=24 ≈ 150.8 (2 * pi * 24).
  // Offset = circumference * (1 - illumination/100) so a New Moon
  // (0%) shows the full dim track and a Full Moon (100%) shows
  // the full bright arc.
  // Character-length bucket for the moon-phase label so the font can be
  // sized to FIT the phrase. Short names ("New" / "Full") get the big
  // cqmin ceiling; the long two-word phases ("Waning Gibbous" / "Waxing
  // Crescent" = 14-15 chars) overflowed / ellipsed at that ceiling on the
  // tall tiles, so they drop to a smaller ceiling. Returns '' for short
  // names (no class -> default large sizing).
  moonPhaseLenClass(phase) {
    const n = String(phase == null ? '' : phase).trim().length;
    if (n >= 14) {
      return 'apps-widget-moon-phase--xs';
    }
    if (n >= 11) {
      return 'apps-widget-moon-phase--sm';
    }
    if (n >= 8) {
      return 'apps-widget-moon-phase--md';
    }
    return '';
  },
  appsWidgetMoonPhase() {
    const w = this.weather;
    if (!w || !w.supports_moon) {
      return null;
    }
    const fc = (w.forecast || [])[0];
    if (!fc) {
      return null;
    }
    const illum = (fc.moon_illumination != null) ? Number(fc.moon_illumination) : null;
    if (!Number.isFinite(illum)) {
      return null;
    }
    const r = 24;
    const circumference = 2 * Math.PI * r;
    const pct = Math.max(0, Math.min(100, illum));
    const phaseFull = (fc.moon_phase || '').trim();
    // Phase short label — first word of the WeatherAPI phase name
    // (e.g. "Waxing Crescent" → "Waxing", "Full Moon" → "Full")
    // so the hero stays compact. Falls back to the full label
    // when no space (e.g. "Full").
    const phaseShort = phaseFull
      ? (phaseFull.split(/\s+/)[0] || phaseFull)
      : '';
    return {
      circumference,
      offset: circumference * (1 - pct / 100),
      illumination_pct: Math.round(pct),
      phase_full: phaseFull,
      phase_short: phaseShort,
      moonrise: fc.moonrise || '',
      moonset: fc.moonset || '',
    };
  },
};

export const widget = {
  kind: 'moon',
  supportsRefresh: true,
  decorationIcon() {
    return 'icon-moon';
  },
  // Moon data rides the same /api/weather response as weather.
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
