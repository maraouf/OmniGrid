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

// Custom-dashboard PRAYER-TIMES widget module.
//
// Render helpers (ordered prayer rows + next-prayer highlight + live
// countdown + Hijri date). The `prayer` state + `_ensurePrayerTimes`
// fetch stay in app-apps.js (the AI palette context-builder shares
// them); this module references them via `this.`.
//
// See clock.js for the per-widget module contract.

export const helpers = {
  // Ordered rows for the card: the 5 obligatory prayers PLUS Sunrise
  // (informational). Each row carries the translated name + whether it's
  // the next upcoming prayer (for the highlight). Returns [] until the
  // first successful fetch so the tile renders its empty state.
  // Format a backend "HH:MM" (24h) prayer time to the user's profile
  // time format (12h/24h + AM/PM — Settings → Profile → Formats). Seconds
  // are dropped (prayer times are minute-granularity, so ":00" would be
  // noise). Falls back to the raw string on any parse issue. Pure reformat,
  // no timezone conversion — the value is already the location's local time.
  _fmtPrayerTime(hhmm) {
    // Delegate to the shared clock formatter (app-utils.js) so "format a
    // bare HH:MM the way the user asked" lives in ONE place (also used by
    // the *arr release-calendar popover).
    return this._fmtClockTime(hhmm);
  },
  // Ordered prayer rows for the widget. On wide+short tiles that can only
  // show 3 rows, returns a 3-row CIRCULAR window CENTERED on the next
  // upcoming prayer (prev / upcoming / next) — so as each prayer passes the
  // window rolls up and the upcoming one stays in the middle. The "next"
  // index is computed CLIENT-SIDE from the live `appsClockNow` 1s tick (not
  // the backend `prayer.next`), so the list re-centers within ~1s of a
  // prayer time passing WITHOUT a page refresh / re-fetch. Tiles that fit
  // the full set (normal/half width, or any tall tile) get all rows in
  // natural order with the upcoming one highlighted live.
  prayerWidgetRows(item) {
    const p = this.prayer;
    if (!p || !p.timings || p.configured === false) {
      return [];
    }
    const order = ['fajr', 'sunrise', 'dhuhr', 'asr', 'maghrib', 'isha'];
    const all = [];
    order.forEach((key) => {
      const row = p.timings[key];
      if (!row || !row.time) {
        return;
      }
      all.push({
        key,
        name: this.prayerName(key),
        time: this._fmtPrayerTime(row.time),
        ts: row.ts,
        isPrayer: !!row.prayer,
        isNext: false,
      });
    });
    if (!all.length) {
      return [];
    }
    // Client-side "next upcoming PRAYER" index (skip the informational
    // Sunrise row as a centre — it's a neighbour, never the highlight).
    // Touch `appsClockNow` so Alpine re-runs this each second.
    const nowS = (this.appsClockNow || Date.now()) / 1000;
    let center = -1;
    for (let i = 0; i < all.length; i++) {
      if (all[i].isPrayer && all[i].ts && all[i].ts > nowS) {
        center = i;
        break;
      }
    }
    if (center < 0) {
      // Every prayer today has passed — the next is tomorrow's first
      // prayer; wrap the window to it (Fajr).
      for (let i = 0; i < all.length; i++) {
        if (all[i].isPrayer) {
          center = i;
          break;
        }
      }
    }
    if (center < 0) {
      center = 0;
    }
    const cap = this._prayerWidgetVisibleCap(item);
    if (all.length <= cap) {
      return all.map((r, i) => Object.assign({}, r, {isNext: i === center}));
    }
    // Circular window of `cap` rows centred on `center` (upcoming in the
    // middle), wrapping across the day boundary so it never runs off the end.
    const half = Math.floor(cap / 2);
    const out = [];
    for (let off = -half; off < cap - half; off++) {
      const idx = ((center + off) % all.length + all.length) % all.length;
      out.push(Object.assign({}, all[idx], {isNext: idx === center}));
    }
    return out;
  },
  // How many prayer rows the tile can actually show. The wide+short
  // row-layout (size normal/double/xlarge at short height) clips the list
  // to ~3 rows; every other layout fits the full set. Drives whether
  // `prayerWidgetRows` returns a centred 3-row window (auto-focused on the
  // next prayer) or all rows. `normal` joined the row-layout once the
  // 12-column grid widened it (span 3) enough for the side-by-side view.
  _prayerWidgetVisibleCap(item) {
    const size = (item && item.opts && item.opts.size) || 'normal';
    const tall = (item && item.opts && item.opts.height) === 'tall';
    if (!tall && (size === 'normal' || size === 'double' || size === 'xlarge')) {
      return 3;
    }
    return 6;
  },
  // Translated prayer name (falls back to TitleCase English). Sunrise is
  // included so the card can label the informational row.
  prayerName(key) {
    const tr = this.t('prayer_times.name_' + key);
    if (tr && tr !== 'prayer_times.name_' + key) {
      return tr;
    }
    return (key || '').charAt(0).toUpperCase() + (key || '').slice(1);
  },
  // Per-prayer sprite icon id (dawn → sun-high → sun-low → sunset →
  // moon). Sunrise reuses the sunrise glyph.
  prayerIconId(key) {
    const map = {
      fajr: 'icon-sunrise',
      sunrise: 'icon-sun-medium',
      dhuhr: 'icon-sun',
      asr: 'icon-sun-low',
      maghrib: 'icon-sunset',
      isha: 'icon-moon-stars',
    };
    return map[key] || 'icon-clock';
  },
  // The next upcoming prayer's translated name (for the hero line).
  prayerNextName() {
    const p = this.prayer;
    if (!p || !p.next || !p.next.key) {
      return '';
    }
    return this.prayerName(p.next.key);
  },
  // Live "in 2h 14m" countdown to the next prayer. Reads the shared 1s
  // `appsClockNow` tick so it re-evaluates every second (reactive
  // dependency) and counts down between fetches. Empty when no next
  // prayer is known.
  prayerCountdownLabel() {
    const p = this.prayer;
    const at = p && p.next && p.next.at_ts;
    if (!at) {
      return '';
    }
    // Touch appsClockNow so Alpine re-runs this each tick.
    const nowMs = this.appsClockNow || Date.now();
    let secs = Math.max(0, Math.round(at - (nowMs / 1000)));
    const h = Math.floor(secs / 3600);
    secs -= h * 3600;
    const m = Math.floor(secs / 60);
    const s = secs - m * 60;
    if (h > 0) {
      return h + 'h ' + m + 'm';
    }
    if (m > 0) {
      return m + 'm';
    }
    return s + 's';
  },
  // "17 Dhul Hijjah 1447 AH" — the Hijri date header. Uses the English
  // month name from the API (already localised per AlAdhan); the
  // designation (AH) is appended.
  prayerHijriLabel() {
    const h = (this.prayer && this.prayer.hijri) || null;
    if (!h || !h.day) {
      return '';
    }
    const parts = [h.day, h.month_en, h.year].filter(Boolean).join(' ');
    return (parts + (h.designation ? (' ' + h.designation) : '')).trim();
  },
};

export const widget = {
  kind: 'prayer_times',
  supportsRefresh: true,
  decorationIcon() {
    return 'icon-mosque';
  },
  freshnessObj(c) {
    return c.prayer;
  },
  hasData(c) {
    if (!c.prayer || c.prayer.configured === false) {
      return false;
    }
    if (!c.prayer.timings) {
      return false;
    }
    if (typeof c.prayerWidgetRows !== 'function') {
      return false;
    }
    return c.prayerWidgetRows().length > 0;
  },
  refresh(c) {
    return c._ensurePrayerTimes(true);
  },
};
