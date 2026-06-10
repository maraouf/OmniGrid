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

// Custom-dashboard CLOCK widget module.
//
// Per-widget render helpers (digital + analog clock face, per-card TZ /
// format overrides). Mirrors the per-app model: this file holds the
// widget-only FUNCTIONS; the reactive state (`appsClockNow`) stays in
// app-apps.js, and the shared date-format helpers (`_applyDateTimeFormat`
// / `_userTimeOnlyFormat` / `_userDateOnlyFormat`) stay on the component
// — referenced via `this.` (everything merges into one Alpine component).
//
// Loaded by static/js/widgets/_registry.js, which merges every widget
// module's `helpers` into the component AND collects the `widget` record
// (kind / decorationIcon / supportsRefresh / ...) into `window.OG_WIDGETS`
// so the generic widget helpers in app-apps.js / app-topbar.js dispatch
// per-kind behaviour without a hardcoded if/else ladder.

export const helpers = {
  // Live HH:MM for the clock widget — reads the 1s-ticked `appsClockNow`
  // so it updates reactively. Routes through `_applyDateTimeFormat` +
  // `_userTimeOnlyFormat` so the operator's Settings → Profile → Formats
  // preference applies (single source of truth for time rendering across
  // the SPA); falls back to the browser's locale `toLocaleTimeString`
  // when either helper is unavailable (e.g. early-paint before the
  // user's prefs have loaded).
  // Per-item override gate. Returns the Date adjusted to the item's
  // override TZ (when item.opts.clock_tz is set and follow_user=false)
  // OR the raw Date in browser-local TZ (the default — every existing
  // call site that doesn't pass `item` gets the legacy behaviour).
  // The TZ adjustment uses `Intl.DateTimeFormat` to compute the
  // wall-clock parts in the override TZ + reassembles a Date so
  // downstream `_applyDateTimeFormat(d, fmt)` (which reads
  // `d.getHours()` etc.) renders in the override TZ without each
  // formatter having to know about timezones.
  _clockDateForItem(item) {
    // `appsClockNow` is the Apps-view-gated 1s ticker (NOT the
    // drawer-scoped hostHistoryNow, which froze the clock at the
    // last value whenever no drawer was open). `|| Date.now()`
    // covers the brief window before the ticker's first tick.
    const ms = this.appsClockNow || Date.now();
    const opts = item ? this.effectiveClockOpts(item) : null;
    const d = new Date(ms);
    if (!opts || opts.follow || !opts.tz) {
      return d;
    }
    try {
      // Build a Date whose UTC fields equal the target TZ's wall-clock
      // fields — that way getHours() / getMinutes() / etc. (which
      // _applyDateTimeFormat reads) return the override-TZ values
      // when the formatter pulls them in browser-local mode.
      const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: opts.tz,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false,
      }).formatToParts(d);
      const get = (k) => {
        const p = parts.find(pt => pt.type === k);
        return p ? parseInt(p.value, 10) : 0;
      };
      // Build the shifted Date in browser-local TZ so its getX()
      // accessors mirror the override-TZ wall-clock parts.
      return new Date(
        get('year'), get('month') - 1, get('day'),
        get('hour') % 24, get('minute'), get('second'),
      );
    } catch (_) {
      return d;
    }
  },
  // Resolve the format string for one clock item (user override OR
  // user-profile time-only format). Empty string when neither is
  // available — caller's `try/_applyDateTimeFormat` chain falls
  // through to `toLocaleTimeString` in that case.
  _clockFormatForItem(item) {
    const opts = item ? this.effectiveClockOpts(item) : null;
    if (opts && !opts.follow && opts.format) {
      return opts.format;
    }
    if (typeof this._userTimeOnlyFormat === 'function') {
      try {
        return this._userTimeOnlyFormat();
      } catch (_) {
      }
    }
    return '';
  },
  // HH part of the clock for the redesigned tile — the markup
  // splits HH and MM around an animated colon so the colon
  // can pulse independently. Returns just the hour digits in
  // 2-digit zero-padded form (matches HH token of the user's
  // pref / the per-card override).
  // True when the resolved clock format is 12-hour — a lowercase `h`
  // token present and no uppercase `H`. Drives 12h hour rendering +
  // whether the AM/PM segment shows. Defaults to 24h when no format.
  _clockIs12Hour(item) {
    const fmt = this._clockFormatForItem(item) || '';
    return /h/.test(fmt) && !/H/.test(fmt);
  },
  // Clock helpers — each now accepts an optional `item` arg. When
  // omitted (the default for the legacy topbar / non-Custom callers),
  // every helper reads user-profile TZ + format. When the Custom
  // layout passes `item`, the helper consults `effectiveClockOpts(item)`
  // for the override chain.
  appsWidgetClock(item) {
    try {
      const d = this._clockDateForItem(item);
      const fmt = this._clockFormatForItem(item);
      if (fmt && typeof this._applyDateTimeFormat === 'function') {
        return this._applyDateTimeFormat(d, fmt);
      }
      return d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    } catch (_) {
      return '';
    }
  },
  // Date subtitle for the clock widget — weekday + month + day. Format
  // follows the browser locale so an operator in es-MX sees "lun, 28
  // may" while en-US sees "Mon, May 28". When `item` carries a TZ
  // override, the Date passed to `toLocaleDateString` is shifted so
  // weekday / month / day reflect the override TZ.
  appsWidgetClockDate(item) {
    try {
      const d = this._clockDateForItem(item);
      // Follow the operator's Settings → Profile date format (the
      // date-only portion, e.g. `dd/MM/yyyy` -> "30/05/2026") instead
      // of the browser-locale "Sat, May 30" — the clock claims to
      // follow the user format, so the date subtitle must too. Falls
      // back to the locale long-date only when the helpers aren't ready.
      if (typeof this._userDateOnlyFormat === 'function'
        && typeof this._applyDateTimeFormat === 'function') {
        return this._applyDateTimeFormat(d, this._userDateOnlyFormat());
      }
      return d.toLocaleDateString([], {
        weekday: 'short', month: 'short', day: 'numeric',
      });
    } catch (_) {
      return '';
    }
  },
  // Human-friendly timezone label for the clock widget — derives a
  // city name from the effective IANA zone ("Africa/Cairo" → "Cairo",
  // "America/New_York" → "New York", "America/Argentina/Buenos_Aires"
  // → "Buenos Aires"). In follow-user mode the zone comes from the
  // browser (`Intl…resolvedOptions().timeZone`); with a per-card
  // override it's `opts.tz`. Deliberately a CITY name, never a UTC
  // offset ("+2") or abbreviation ("EEST"). Returns '' when the zone
  // can't be resolved so the bound label collapses cleanly.
  appsWidgetClockTzLabel(item) {
    try {
      const opts = item ? this.effectiveClockOpts(item) : null;
      let tz = (opts && !opts.follow && opts.tz) ? opts.tz : '';
      if (!tz) {
        // follow-user / no override → the browser's resolved zone.
        tz = (Intl.DateTimeFormat().resolvedOptions().timeZone) || '';
      }
      if (!tz) {
        return '';
      }
      if (tz === 'UTC' || tz === 'Etc/UTC') {
        return 'UTC';
      }
      // City portion = last path segment, underscores → spaces.
      const seg = tz.split('/').pop() || tz;
      return seg.replace(/_/g, ' ');
    } catch (_) {
      return '';
    }
  },
  appsWidgetClockHH(item) {
    try {
      const d = this._clockDateForItem(item);
      // Respect the user's / per-card 12h-vs-24h preference (was
      // hardcoded 'HH' = 24h, so a 12h profile like `hh:mm a` wrongly
      // showed 00:07 instead of 12:07). `hh` renders the zero-padded
      // 12-hour hour; `HH` the 24-hour one.
      return this._applyDateTimeFormat(d, this._clockIs12Hour(item) ? 'hh' : 'HH');
    } catch (_) {
      return '';
    }
  },
  appsWidgetClockMM(item) {
    try {
      return this._applyDateTimeFormat(this._clockDateForItem(item), 'mm');
    } catch (_) {
      return '';
    }
  },
  // AM/PM segment for the digital clock tile — empty string for a
  // 24-hour format so the markup renders nothing. Rendered next to the
  // HH:MM digits when the resolved format is 12-hour.
  appsWidgetClockAMPM(item) {
    try {
      if (!this._clockIs12Hour(item)) {
        return '';
      }
      return this._applyDateTimeFormat(this._clockDateForItem(item), 'a');
    } catch (_) {
      return '';
    }
  },
  // True when the clock widget should render as an analog face.
  // Default 'digital' from `effectiveClockOpts` so existing widget
  // tiles (and the topbar clock) stay digital unless the per-card
  // setting flips them.
  appsWidgetClockIsAnalog(item) {
    if (!item) {
      return false;
    }
    return this.effectiveClockOpts(item).style === 'analog';
  },
  // Hand rotations for the analog clock face. Each angle is in
  // degrees, measured clockwise from 12-o'clock (`transform:
  // rotate(<deg>deg)` on each SVG `<line>`). NO seconds hand — it was
  // removed to avoid a per-second dial repaint; the hour hand still
  // advances fractionally across the minute so the face reads
  // naturally, while the minute hand ticks discretely (it only needs
  // to move once a minute now that there's no seconds sweep above it).
  appsWidgetClockHands(item) {
    const d = this._clockDateForItem(item);
    const h = d.getHours();
    const m = d.getMinutes();
    // Hour: 360 deg / 12 hours = 30 deg/hr; smooth across the minute.
    const hourDeg = ((h % 12) * 30) + (m * 0.5);
    // Minute: 360 / 60 = 6 deg/min; discrete per minute (no seconds
    // smoothing — the seconds hand is gone).
    const minuteDeg = m * 6;
    return {hour: hourDeg, minute: minuteDeg};
  },
  // Effective Clock-widget options — resolves the override-or-fallback
  // chain so the clock-rendering helpers can read ONE shape regardless
  // of whether the operator set per-card overrides. follow_user=true
  // (default when opts.follow_user is undefined) means EVERY field
  // falls back to the user's profile prefs (TZ via scheduler_tz or the
  // browser, format via `_userDateTimeFormat`). follow_user=false
  // lets each override field win when set; absent override fields STILL
  // fall back to the user's profile (so a partial override is sensible).
  effectiveClockOpts(item) {
    const opts = (item && item.opts) || {};
    const follow = (opts.follow_user !== false);  // default = follow
    const tz = (!follow && opts.clock_tz) || '';
    const fmt = (!follow && opts.clock_format) || '';
    const style = opts.clock_style || 'digital';
    return {
      follow,
      tz,
      format: fmt,
      style: (style === 'analog' ? 'analog' : 'digital'),
    };
  },
  // List of common IANA timezones for the clock-widget back-face
  // dropdown. Operator can also type a free-form value (the input is
  // a `<datalist>`-backed text input, so the list is suggestions, not
  // an enum gate). Kept small + alphabetised for stable diffs.
  appsClockTimezones() {
    return [
      'UTC',
      'Africa/Cairo', 'Africa/Johannesburg', 'Africa/Lagos',
      'America/Chicago', 'America/Denver', 'America/Los_Angeles',
      'America/New_York', 'America/Sao_Paulo', 'America/Toronto',
      'Asia/Dubai', 'Asia/Hong_Kong', 'Asia/Jerusalem', 'Asia/Kolkata',
      'Asia/Shanghai', 'Asia/Singapore', 'Asia/Tokyo',
      'Australia/Sydney',
      'Europe/Amsterdam', 'Europe/Berlin', 'Europe/London',
      'Europe/Madrid', 'Europe/Moscow', 'Europe/Paris',
      'Europe/Rome', 'Europe/Stockholm',
      'Pacific/Auckland', 'Pacific/Honolulu',
    ];
  },
};

// Widget extender record — consumed by the generic widget helpers via
// window.OG_WIDGETS. Clock is a pure client-side derivation: no refresh.
export const widget = {
  kind: 'clock',
  supportsRefresh: false,
  decorationIcon() {
    return 'icon-clock';
  },
};
