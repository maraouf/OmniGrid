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

// Custom-dashboard *arr RELEASE-CALENDAR widget module.
//
// Self-contained month-grid calendar of upcoming movie / series / album
// / book releases from the configured Radarr / Sonarr / Lidarr / Readarr
// instances. Data: GET /api/apps/arr-calendar (per-month cache + de-dup
// guard). The reactive state (`arrCalendar` + `_arrCal*` + `arrCalViewYM`
// / `arrCalOpenDay` / `_arrCalPopRect`) stays declared in app-apps.js;
// this module holds the render + fetch FUNCTIONS.
//
// See clock.js for the per-widget module contract.

export const helpers = {
  // True when ≥1 *arr service is configured (drives picker + tile gating).
  arrCalAvailable() {
    return !!(this.me && this.me.client_config
      && this.me.client_config.arr_calendar_available === true);
  },
  // 'YYYY-MM-DD' for a Date in LOCAL time (avoids toISOString's UTC shift,
  // which would bucket a release on the wrong calendar day near midnight).
  _ymd(d) {
    const p = (n) => (n < 10 ? '0' + n : '' + n);
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  },
  // The displayed month as 'YYYY-MM' (defaults to the current month).
  arrCalViewMonth() {
    if (this.arrCalViewYM && /^\d{4}-\d{2}$/.test(this.arrCalViewYM)) {
      return this.arrCalViewYM;
    }
    const n = new Date();
    return n.getFullYear() + '-' + (n.getMonth() + 1 < 10 ? '0' : '') + (n.getMonth() + 1);
  },
  // Locale month + year heading, e.g. "June 2026".
  arrCalMonthLabel() {
    const parts = this.arrCalViewMonth().split('-');
    const y = Number(parts[0]);
    const m = Number(parts[1]);
    try {
      return new Intl.DateTimeFormat(undefined, {month: 'long', year: 'numeric'})
        .format(new Date(y, m - 1, 1));
    } catch (_e) {
      return this.arrCalViewMonth();
    }
  },
  // Locale single-letter weekday headers, Sunday-first (2023-01-01 is a Sun).
  arrCalWeekdays() {
    if (this._arrCalWeekdaysMemo) {
      return this._arrCalWeekdaysMemo;
    }
    const out = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(2023, 0, 1 + i);
      try {
        out.push(new Intl.DateTimeFormat(undefined, {weekday: 'narrow'}).format(d));
      } catch (_e) {
        out.push(['S', 'M', 'T', 'W', 'T', 'F', 'S'][i]);
      }
    }
    this._arrCalWeekdaysMemo = out;
    return out;
  },
  // The displayed month's 6×7 day grid. Memoised on (month | data identity)
  // so the dense x-for doesn't rebuild every reactive flush. The key reads
  // the reactive deps (arrCalViewYM + arrCalendar) so Alpine keeps the
  // subscription on a cache hit.
  arrCalendarGrid() {
    const ym = this.arrCalViewMonth();
    const cal = this.arrCalendar || {};
    const items = Array.isArray(cal.items) ? cal.items : [];
    const key = ym + '|' + (cal.fetched_at || 0) + '|' + items.length;
    if (this._arrCalGridMemo && this._arrCalGridMemo.key === key) {
      return this._arrCalGridMemo.val;
    }
    const parts = ym.split('-');
    const y = Number(parts[0]);
    const m = Number(parts[1]);  // 1-12
    const first = new Date(y, m - 1, 1);
    const gridStart = new Date(y, m - 1, 1 - first.getDay());  // back to Sunday
    const todayStr = this._ymd(new Date());
    // Bucket items by date once (O(items), not O(cells × items)).
    const byDate = {};
    items.forEach((it) => {
      const ds = (it && it.date) || '';
      if (!ds) {
        return;
      }
      (byDate[ds] = byDate[ds] || []).push(it);
    });
    const weeks = [];
    for (let w = 0; w < 6; w++) {
      const days = [];
      for (let dd = 0; dd < 7; dd++) {
        const cur = new Date(gridStart.getFullYear(), gridStart.getMonth(),
          gridStart.getDate() + (w * 7 + dd));
        const ds = this._ymd(cur);
        const dayItems = byDate[ds] || [];
        const services = [];
        dayItems.forEach((it) => {
          if (it.service_slug && services.indexOf(it.service_slug) === -1) {
            services.push(it.service_slug);
          }
        });
        days.push({
          date: ds,
          day: cur.getDate(),
          col: dd,        // 0-6 (Sun-Sat) — drives popover left/right anchoring
          row: w,         // 0-5 — drives popover up/down anchoring
          inMonth: cur.getMonth() === (m - 1),
          isToday: ds === todayStr,
          count: dayItems.length,
          services: services,
        });
      }
      weeks.push(days);
    }
    this._arrCalGridMemo = {key: key, val: weeks};
    return weeks;
  },
  // Releases on one day for the popover — grouped by MEDIA TYPE (Movies /
  // Series / Albums / Books), NOT by service. Sorted by a fixed type order
  // then title, with each item annotated `_group_head` (true for the FIRST
  // item of its type) so the template renders a type header (type icon +
  // label + count) before each group. The per-item app brand is carried by
  // each row's own app-link pill instead (Homarr-style).
  arrCalDayItems(ds) {
    const cal = this.arrCalendar || {};
    const order = {movie: 0, episode: 1, album: 2, book: 3};
    const rank = (it) => {
      const r = order[String((it && it.type) || '').toLowerCase()];
      return r == null ? 9 : r;
    };
    const items = (Array.isArray(cal.items) ? cal.items : [])
      .filter((it) => it && it.date === ds)
      .sort((a, b) => {
        const d = rank(a) - rank(b);
        return d !== 0 ? d : String(a.title || '').localeCompare(String(b.title || ''));
      });
    const counts = {};
    items.forEach((it) => {
      counts[it.type] = (counts[it.type] || 0) + 1;
    });
    // Plain loop (no side-effecting closure) — `_group_head` reads the PREVIOUS
    // item directly, so there's no outer variable reassigned inside a callback.
    const out = [];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      out.push(Object.assign({}, it, {
        _group_head: i === 0 || items[i - 1].type !== it.type,
        _group_count: counts[it.type] || 0,
      }));
    }
    return out;
  },
  // Localised media-type group label (Movies / Series / Albums / Books);
  // TitleCase fallback for an unknown type.
  arrCalTypeLabel(type) {
    const k = 'apps.custom.widget_arr_calendar_type_' + String(type || '').toLowerCase();
    const tr = this.t(k);
    if (tr && tr !== k) {
      return tr;
    }
    const s = String(type || '');
    return s ? (s.charAt(0).toUpperCase() + s.slice(1)) : '';
  },
  // Sprite icon-id for a media type's group header.
  arrCalTypeIcon(type) {
    const map = {movie: 'icon-film', episode: 'icon-tv', album: 'icon-music', book: 'icon-book'};
    return map[String(type || '').toLowerCase()] || 'icon-calendar';
  },
  // "1h 58m" / "58m" runtime label from minutes; '' for 0 / missing.
  arrCalRuntimeLabel(min) {
    const m = Math.max(0, Math.round(Number(min) || 0));
    if (!m) {
      return '';
    }
    if (m >= 60) {
      const h = Math.floor(m / 60);
      const r = m % 60;
      return r ? (h + 'h ' + r + 'm') : (h + 'h');
    }
    return m + 'm';
  },
  // Proxied poster URL for a calendar item (per-app image proxy keeps the
  // app's api_key server-side). '' when the item has no poster.
  arrCalItemPoster(it) {
    if (!it || !it.poster || it.poster_proxy !== true
      || it.host_id == null || it.service_idx == null) {
      return '';
    }
    return '/api/services/' + encodeURIComponent(it.host_id) + '/'
      + it.service_idx + '/image-proxy?path=' + encodeURIComponent(it.poster);
  },
  // Brand icon for a service slug (the real Radarr/Sonarr/... logo).
  arrCalServiceIcon(slug) {
    return '/img/icons/' + String(slug || '').toLowerCase() + '.svg';
  },
  // Localised label for a service slug (TitleCase fallback).
  arrCalServiceLabel(slug) {
    const k = 'apps.custom.widget_arr_calendar_svc_' + String(slug || '').toLowerCase();
    const tr = this.t(k);
    if (tr && tr !== k) {
      return tr;
    }
    const s = String(slug || '');
    return s ? (s.charAt(0).toUpperCase() + s.slice(1)) : '';
  },
  // "Open in <app>" href for a calendar item. The backend deep-links against
  // the INTEGRATION base (machine host:port) in `rel.app_url`; if the operator
  // set a friendly reverse-proxy override for this service in the widget
  // settings (opts key `arr_link_<slug>`, e.g. https://sonarr.example.com),
  // rebuild the link as <override> + rel.app_path so the redirect targets the
  // user-friendly URL while the probe keeps using the machine address.
  //
  // Reads the active widget's opts from `this._arrCalActiveOpts` (stashed when
  // the day popover opens) rather than a passed `item` — the popover is
  // TELEPORTED to <body>, where the per-tile `item` x-for variable is NOT in
  // scope, so referencing it would throw and the pill's `x-show` would go
  // falsy (the pill vanishing regression).
  arrCalItemAppUrl(rel) {
    if (!rel) {
      return '';
    }
    const slug = String(rel.service_slug || '').toLowerCase();
    const opts = this._arrCalActiveOpts || {};
    const ovr = String(opts['arr_link_' + slug] || '').trim();
    if (ovr) {
      const base = ovr.replace(/\/+$/, '');
      const path = String(rel.app_path || '');
      return path ? (base + path) : base;
    }
    return rel.app_url || '';
  },
  // The day whose popover is showing (click-pinned only).
  arrCalActiveDay() {
    return this.arrCalOpenDay || '';
  },
  // Toggle a day's popover. Captures the clicked cell's viewport rect so the
  // popover can render as a position:fixed layer TELEPORTED to <body> —
  // escaping the tile's `container-type: size` + `overflow: hidden` clip (so
  // a small tile no longer crops it) and staying put so the cursor can move
  // INTO it and scroll its list. Click-pinned, not hover.
  arrCalToggleDay(ds, ev, item) {
    if (this.arrCalOpenDay === ds) {
      this.arrCalCloseDay();
      return;
    }
    const el = ev && (ev.currentTarget || ev.target);
    if (el && el.getBoundingClientRect) {
      const r = el.getBoundingClientRect();
      this._arrCalPopRect = {
        top: r.top, bottom: r.bottom, left: r.left, right: r.right,
        cx: r.left + (r.width / 2),
      };
    } else {
      this._arrCalPopRect = null;
    }
    // Stash THIS widget's opts (link overrides) — the grid button is in the
    // tile's `item` scope, but the teleported popover that reads it is not, so
    // capture the opts here for arrCalItemAppUrl to read.
    this._arrCalActiveOpts = (item && item.opts) || {};
    this.arrCalOpenDay = ds;
  },
  // Close the day popover + drop its anchor rect.
  arrCalCloseDay() {
    this.arrCalOpenDay = '';
    this._arrCalPopRect = null;
    this._arrCalActiveOpts = null;
  },
  // Inline position for the teleported fixed-layer day popover, computed from
  // the clicked cell's captured rect. Returns an OBJECT (Alpine `:style`
  // applies object keys reliably — a returned STRING was not being applied,
  // leaving the popover in static flow at the page bottom). Prefers below the
  // cell; flips above when there isn't room; clamps horizontally to the
  // viewport. The internal list scrolls within the computed max-height. The
  // unused vertical edge is set to 'auto' so a top↔bottom flip doesn't leave a
  // stale offset on the element from a previous render.
  arrCalPopStyle() {
    const r = this._arrCalPopRect;
    if (!r || typeof window === 'undefined') {
      return {display: 'none'};
    }
    const vw = window.innerWidth || 360;
    const vh = window.innerHeight || 640;
    const w = Math.min(300, vw - 16);
    let left = Math.round(r.cx - (w / 2));
    left = Math.max(8, Math.min(left, vw - w - 8));
    const below = vh - r.bottom;
    const above = r.top;
    const cap = Math.min(360, Math.round(vh * 0.6));
    const style = {position: 'fixed', left: left + 'px', width: w + 'px'};
    if (below >= 180 || below >= above) {
      style.top = Math.round(r.bottom + 6) + 'px';
      style.bottom = 'auto';
      style.maxHeight = Math.min(cap, Math.max(120, Math.round(below - 12))) + 'px';
    } else {
      style.bottom = Math.round(vh - r.top + 6) + 'px';
      style.top = 'auto';
      style.maxHeight = Math.min(cap, Math.max(120, Math.round(above - 12))) + 'px';
    }
    return style;
  },
  arrCalShiftMonth(delta) {
    const parts = this.arrCalViewMonth().split('-');
    const d = new Date(Number(parts[0]), Number(parts[1]) - 1 + delta, 1);
    this.arrCalViewYM = d.getFullYear() + '-'
      + (d.getMonth() + 1 < 10 ? '0' : '') + (d.getMonth() + 1);
    this.arrCalCloseDay();
    // The loading state (in-grid overlay + the tile's refresh-pill spin) is
    // managed inside _ensureArrCalendar at the network-fetch boundary, so a
    // cached month resolves instantly with no flicker and a real fetch shows
    // the spinner. The card keeps the previous month's data until the new
    // window lands (so widgetHasData stays true and the pill stays visible).
    this._ensureArrCalendar();
  },
  arrCalPrevMonth() {
    this.arrCalShiftMonth(-1);
  },
  arrCalNextMonth() {
    this.arrCalShiftMonth(1);
  },
  // Self-fetch the displayed month's release calendar. Per-month 10-min
  // cache + per-month de-dup guard (mirrors _ensurePrayerTimes). Fetches the
  // FULL visible 6-week grid window so edge-of-month rows populate.
  _ensureArrCalendar(force = false) {
    const cc = (this.me && this.me.client_config) || {};
    if (cc.arr_calendar_available === false) {
      if (!this.arrCalendar) {
        this.arrCalendar = {configured: false, items: [], services: []};
      }
      return;
    }
    const ym = this.arrCalViewMonth();
    if (!this._arrCalMonthCache) {
      this._arrCalMonthCache = {};
    }
    const now = Date.now();
    const cached = this._arrCalMonthCache[ym];
    if (!force && cached && (now - cached.ts) < 10 * 60 * 1000) {
      this.arrCalendar = cached.data;
      this._arrCalFetchedAt = cached.ts;
      return;
    }
    if (this._arrCalFetching === ym) {
      return;  // a fetch for this month is already in flight
    }
    this._arrCalFetching = ym;
    // Spin the tile's refresh pill for EVERY real network fetch (mount /
    // month-nav / refresh) from one place — a cached month early-returns above,
    // so the pill only spins on an actual fetch.
    if (!this.appsWidgetRefreshing) {
      this.appsWidgetRefreshing = {};
    }
    this.appsWidgetRefreshing.arr_calendar = true;
    const parts = ym.split('-');
    const y = Number(parts[0]);
    const m = Number(parts[1]);
    const first = new Date(y, m - 1, 1);
    const gridStart = new Date(y, m - 1, 1 - first.getDay());
    const gridEnd = new Date(gridStart.getFullYear(), gridStart.getMonth(),
      gridStart.getDate() + 41);
    const qs = 'start=' + this._ymd(gridStart) + '&end=' + this._ymd(gridEnd)
      + (force ? ('&_=' + now) : '');
    return fetch('/api/apps/arr-calendar?' + qs)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (!j) {
          return;
        }
        this._arrCalMonthCache[ym] = {ts: now, data: j};
        this.arrCalendar = j;
        this._arrCalFetchedAt = now;
        this._arrCalGridMemo = null;  // force a rebuild against fresh data
      })
      .catch(() => { /* silent — tile shows its empty state */
      })
      .finally(() => {
        if (this._arrCalFetching === ym) {
          this._arrCalFetching = '';
        }
        if (this.appsWidgetRefreshing) {
          this.appsWidgetRefreshing.arr_calendar = false;
        }
      });
  },
};

export const widget = {
  kind: 'arr_calendar',
  supportsRefresh: true,
  decorationIcon() {
    return 'icon-calendar';
  },
  // Hidden from the Add-widget picker when no *arr service is configured.
  available(c) {
    return c.arrCalAvailable();
  },
  freshnessObj(c) {
    return c.arrCalendar;
  },
  hasData(c) {
    if (!c.arrCalendar || c.arrCalendar.configured === false) {
      return false;
    }
    return !!c.arrCalendar.fetched_at;
  },
  refresh(c) {
    return c._ensureArrCalendar(true);
  },
};
