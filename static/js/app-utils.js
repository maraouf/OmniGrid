// SPA pure-utility helpers — formatters, escapers, classifiers, small DOM
// helpers that read no `this`-state of their own.
//
// Every property declared here is merged into the top-level `app()` Alpine
// component via `Object.assign({}, appUtils, ...)` in `app.js`. Helpers MAY
// call `this.foo(...)` for siblings because at runtime `this` resolves to
// the same component proxy regardless of which module the method literally
// lives in. The split is purely for readability + IDE navigation — runtime
// behaviour is identical to the pre-split monolith.
//
// Phase 2, Batch 1 of the static/js/app.js modularisation. See the
// extraction runbook in CLAUDE.md (Phase 2 spec) for the full plan.
// noinspection ChainedFunctionCallJS

export default {
  avatarHue() {
    if (!this.me || !this.me.username) {
      return 210;
    }
    let h = 0;
    for (const ch of this.me.username) {
      h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    }
    return h % 360;
  },
  // Format a bytes-per-second rate into a human-readable string
  // (e.g. "12.4 MB/s"). Wraps `fmtBytes` for the value, appends "/s".
  fmtBps(bps) {
    if (bps == null || !Number.isFinite(+bps) || +bps <= 0) {
      return '0 B/s';
    }
    return this.fmtBytes(bps) + '/s';
  },
  // Format a duration in seconds → human-readable short form
  // (e.g. "2m 14s" / "1h 23m" / "—"). Used by the Stats → Incidents
  // MTTR cells.
  fmtDurationShort(seconds) {
    if (seconds == null || !Number.isFinite(+seconds) || +seconds <= 0) {
      return '—';
    }
    const s = Math.round(+seconds);
    if (s < 60) {
      return s + 's';
    }
    if (s < 3600) {
      return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
    }
    if (s < 86400) {
      return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
    }
    return Math.floor(s / 86400) + 'd ' + Math.floor((s % 86400) / 3600) + 'h';
  },
  // Escape HTML-unsafe characters before wrapping known prefixes in
  // coloured spans. Without this, a log line that happened to
  // contain `<img onerror=...>` would execute on render (Alpine's
  // x-html is unsandboxed — we have to be strict here).
  _logEscape(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  },
  // Wrap recognised tags like [webmin] / [beszel] in coloured
  // spans. Returns safe HTML (already escaped) for x-html. Tag
  // map below keeps the list explicit — new backend prefixes need
  // to be added here to get a distinct colour (otherwise they
  // fall through to the default tag colour).
  colorizeLogText(l) {
    const raw = (l && l.text) || '';
    const esc = this._logEscape(raw);
    // Tags known to carry a distinct colour class. Falls through
    // to `log-tag` (neutral accent) for unknown tag names so ALL
    // bracketed prefixes get highlighted even if uncategorised.
    const tagColors = new Set([
      'webmin', 'beszel', 'pulse', 'hosts', 'host_net_sampler',
      'ssh', 'portainer', 'i18n', 'ops', 'schedules', 'gather',
      'node_exporter', 'ne', 'oidc', 'auth', 'backup', 'stats',
      'deploy', 'version',
      // TOTP / 2FA state changes (enrol / verify / lockout
      // / admin-disable). Shares the OIDC accent token via CSS.
      'totp',
      // WebAuthn / passkey state changes. Same OIDC accent
      // family — both share the security domain.
      'webauthn',
      // SNMP host-stats provider diagnostics.
      'snmp',
      // Bulk-action operations on the Hosts view (pause /
      // resume / vendors / tunables). Distinct sub-tag so
      // operators can grep all bulk activity in one shot.
      'hosts:bulk',
    ]);
    // Replace [xxx] at the start of (or inside) the line. Allow
    // underscores / hyphens / colons for tag names like
    // [host_net_sampler] and the [hosts:bulk] sub-tag family.
    const withTags = esc.replace(/\[([a-z][a-z0-9_.:-]*?)\]/gi, (_m, tag) => {
      const key = tag.toLowerCase();
      // Colons in tag names map to hyphens in the CSS class so
      // selectors stay simple (no escape required for `:`).
      const cssKey = key.replace(/:/g, '-');
      const cls = tagColors.has(key) ? ('log-tag log-tag--' + cssKey) : 'log-tag';
      return '<span class="' + cls + '">[' + tag + ']</span>';
    });
    return withTags;
  },
  // "X seconds / minutes / hours ago" — short relative-time label
  // used by the HTTP probe card's freshness pill. Mirrors the
  // `hosts_extra.metrics.last_updated_*` pattern from the chart
  // freshness helper without forcing a callsite to know which unit
  // to render.
  fmtSecondsAgoLabel(deltaSeconds) {
    const d = Math.max(0, Math.floor(Number(deltaSeconds) || 0));
    if (d < 60) {
      return this.t('hosts_extra.metrics.last_updated_seconds', {count: d});
    }
    if (d < 3600) {
      return this.t('hosts_extra.metrics.last_updated_minutes', {count: Math.floor(d / 60)});
    }
    return this.t('hosts_extra.metrics.last_updated_hours', {count: Math.floor(d / 3600)});
  },
  fmtUpsRuntime(seconds) {
    const s = +seconds;
    if (!Number.isFinite(s) || s <= 0) {
      return '—';
    }
    if (s < 60) {
      return s.toFixed(0) + 's';
    }
    const mins = Math.floor(s / 60);
    if (mins < 60) {
      const rem = Math.floor(s % 60);
      return rem ? `${mins}m ${rem}s` : `${mins}m`;
    }
    const hrs = Math.floor(mins / 60);
    const remMins = mins % 60;
    return remMins ? `${hrs}h ${remMins}m` : `${hrs}h`;
  },
  // Hex-colour normaliser — used by the per-provider chip
  // colour text input alongside the native colour picker. Operators
  // can paste any of these forms and have them coerced into the
  // canonical `#rrggbb` shape the backend's `^#[0-9a-fA-F]{6}$`
  // validator accepts:
  // - blank / whitespace → "" (means "use the SPA's default")
  // - "1e63d4" / "1E63D4" → "#1e63d4"  (auto-prepend #, lowercase)
  // - "#1E63D4" / "#1e63d4" → "#1e63d4" (lowercase only)
  // - anything else → returned verbatim so the input's `pattern`
  //   attribute can flag it as invalid (red ring) without us
  //   silently swallowing operator-typed garbage.
  // The native `<input type="color">` writes its own `#rrggbb` when
  // the operator drags the picker; this helper only fires when the
  // operator types directly into the text input. Both inputs
  // x-model the same `settings.provider_color_<name>` field so they
  // stay synced regardless of which one was edited last.
  normalizeHexColor(raw) {
    const v = (raw || '').trim();
    if (!v) {
      return '';
    }
    const bareHex = /^[0-9a-fA-F]{6}$/;
    const fullHex = /^#[0-9a-fA-F]{6}$/;
    if (bareHex.test(v)) {
      return '#' + v.toLowerCase();
    }
    if (fullHex.test(v)) {
      return v.toLowerCase();
    }
    return v;  // invalid — let the input's `pattern` flag it
  },

  fmtDuration(seconds) {
    if (!seconds || seconds <= 0) {
      return '—';
    }
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) {
      return d + 'd ' + h + 'h';
    }
    if (h > 0) {
      return h + 'h ' + m + 'm';
    }
    return m + 'm';
  },
  fmtBytes(n) {
    if (n == null) {
      return '—';
    }
    if (n <= 0) {
      return '0 B';
    }
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) {
      n /= 1024;
      i++;
    }
    return (n >= 10 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i];
  },
  // Like fmtBytes but the unit is FIXED based on `refMax` (the upper
  // bound of the chart / legend group). Use this for any chart where
  // you want every value to read in the same unit family — without
  // it, fmtBytes picks per-value (e.g. "1012 MB" next to "1.9 GB"
  // looks misaligned because the per-value picks land on different
  // tiers). Operator-flagged for the SNMP Memory chart at 2026-05-01.
  // Return the unit symbol (B / KB / MB / GB / TB) that `fmtBytes` /
  // `fmtBytesAt` would pick for a value of magnitude `n`. Used by
  // chart title chips so the chip always matches what the legend +
  // Y-axis actually render — operator-flagged that a static `B/s` /
  // `B` chip looked wrong next to a `1.2 MB/s` legend value.
  unitForBytes(n) {
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let v = Math.max(0, +n || 0), i = 0;
    while (v >= 1024 && i < u.length - 1) {
      v /= 1024;
      i++;
    }
    return u[i];
  },
  fmtBytesAt(n, refMax) {
    if (n == null) {
      return '—';
    }
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    let m = Math.max(0, +refMax || 0);
    while (m >= 1024 && i < u.length - 1) {
      m /= 1024;
      i++;
    }
    const v = (+n || 0) / Math.pow(1024, i);
    if (v <= 0 && (+n || 0) === 0) {
      return '0 ' + u[i];
    }
    return (v >= 10 ? v.toFixed(0) : v.toFixed(1)) + ' ' + u[i];
  },
  fmtAgo(ms) {
    if (ms == null) {
      return '—';
    }
    const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
    if (sec < 60) {
      return sec + 's';
    }
    if (sec < 3600) {
      return Math.floor(sec / 60) + 'm';
    }
    if (sec < 86400) {
      const h = Math.floor(sec / 3600);
      const m = Math.floor((sec % 3600) / 60);
      return m > 0 ? `${h}h ${m}m` : `${h}h`;
    }
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    return h > 0 ? `${d}d ${h}h` : `${d}d`;
  },
  // Short-form interval label — matches the topbar stats picker
  // values exactly (5s / 15s / 30s / 1m / 5m). Used by the Hosts
  // subtitle's "polled every X" so it reflects the operator's
  // actual setting instead of a hardcoded "15s".
  fmtIntervalShort(seconds) {
    const s = Number(seconds) || 0;
    if (s <= 0) {
      return '';
    }
    if (s < 60) {
      return s + 's';
    }
    if (s % 60 === 0) {
      return (s / 60) + 'm';
    }
    return s + 's';
  },
  // Pretty-print JSON for the Debug panel's <pre> blocks. Empty
  // payloads return "" — the panel wrapper x-show's on truthy data
  // so the block doesn't render at all instead of showing a stub
  // "(not collected)" string that clutters the grid.
  fmtDebugJson(v) {
    if (v === null || v === undefined) {
      return '';
    }
    try {
      return JSON.stringify(v, null, 2);
    } catch {
      return String(v);
    }
  },
  /** Axis-label formatter for millisecond latency. Mirrors
   * `_fmtAxisPct` / `_fmtAxisBytes` shape — short string, no
   * decimal noise. Sub-1ms shows as "<1ms" rather than 0; values
   * 1000+ get a fractional `s` suffix so a 1234ms label reads
   * "1.2s" not "1234ms". */
  _fmtAxisMs(v) {
    if (!Number.isFinite(+v)) return '';
    const n = +v;
    if (n <= 0) return '0ms';
    if (n < 1) return '<1ms';
    if (n < 1000) return Math.round(n) + 'ms';
    return (n / 1000).toFixed(n < 10000 ? 1 : 0) + 's';
  },
  // --- Axis-label helpers used by the metric-card template ---
  _fmtAxisPct(v) {
    return Math.round(v) + '%';
  },
  _fmtAxisBytes(v) {
    if (v <= 0) {
      return '0 B/s';
    }
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let u = 0;
    let n = v;
    while (n >= 1024 && u < units.length - 1) {
      n /= 1024;
      u++;
    }
    const digits = n >= 100 ? 0 : n >= 10 ? 1 : 2;
    return n.toFixed(digits) + ' ' + units[u] + '/s';
  },
  // return a Y-axis bytes formatter pinned to the unit
  // family of `refMax`. Use as `yAxisAuto(max, _fmtAxisBytesAt(max))`
  // so every tick (top + interpolated middles + 0) renders in the
  // same unit, instead of `_fmtAxisBytes`'s per-value auto-scale
  // (operator-flagged: `MB/s` chip with ticks `4.0 MB/s / 2.0 MB/s
  // / 0` is fine, but `KB/s` near-zero ticks mixed with MB/s top
  // tick is broken).
  _fmtAxisBytesAt(refMax) {
    const fmtAt = this.fmtBytesAt.bind(this);
    return (v) => (v <= 0 ? '0 B/s' : fmtAt(v, refMax) + '/s');
  },
  _fmtAxisTime(ts) {
    if (!ts) {
      return '';
    }
    const d = new Date(ts * 1000);
    // 7d (168h) range — every chart's X-axis labels in DAYS not
    // hours, unified across the drawer. User-flagged: "when the
    // time selected is 7d, the x-axis has to be days rather than
    // hours across all charts and unified". Threshold ≥ 48h so 1h
    // / 6h / 24h all keep HH:MM (within-a-day windows where time-
    // of-day is the right granularity) and only the 7d picker
    // (168h) flips to MMM-d (e.g. `May 8`). Date `toLocaleDateString`
    // via `Intl.DateTimeFormat` honours the browser locale for
    // month abbreviation, which is the right default for an axis
    // label that wants to be terse + readable.
    const rangeHours = Number(this.hostHistoryRange) || 1;
    if (rangeHours >= 48) {
      // Short month-day form. Two-digit day so widths line up;
      // short month name (Jan, Feb,...) is locale-aware via
      // Intl.DateTimeFormat. Same shape every chart receives.
      try {
        return new Intl.DateTimeFormat(undefined, {
          month: 'short',
          day: '2-digit',
        }).format(d);
      } catch (_) {
        // Fallback if Intl is missing for any reason.
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        return `${m}/${dd}`;
      }
    }
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `${hh}:${mm}`;
  },
  // Seconds → "6d 3h" / "5h 12m" / "34m 12s" — matches img_10's format.
  fmtUptimeShort(s) {
    if (!s || s <= 0) {
      return '—';
    }
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d > 0) {
      return `${d}d ${h}h`;
    }
    if (h > 0) {
      return `${h}h ${m}m`;
    }
    return `${m}m`;
  },
  // ISO string → "Updated 2s ago" / "2d ago"
  fmtUpdatedAgo(iso) {
    if (!iso) {
      return '';
    }
    const t = Date.parse(iso);
    if (isNaN(t)) {
      return '';
    }
    const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
    if (s < 60) {
      return `Updated ${s}s ago`;
    }
    if (s < 3600) {
      return `Updated ${Math.floor(s / 60)}m ago`;
    }
    if (s < 86400) {
      return `Updated ${Math.floor(s / 3600)}h ago`;
    }
    return `Updated ${Math.floor(s / 86400)}d ago`;
  },
  // Percent label that distinguishes "genuinely zero" from "small
  // but non-zero" by rendering "<1%" when 0 < v < 1 (e.g. 39 MB
  // used on a 232 GB disk = 0.016% which rounds to 0%, hiding the
  // signal that there IS data). Operator-reported on a dd-wrt
  // host whose /opt mount had a few MB used out of 232 GB and the
  // bar label read "0%" alongside a non-empty fill — confused
  // "is this loading?" vs "is this near-empty?". Uses 1-decimal
  // precision for fractional values < 10 so 1.7% reads as "1.7%"
  // instead of "2%". Integers above 10. Negative / NaN / falsy
  // → "0%" (defensive — shouldn't happen but keeps the label
  // shape consistent).
  fmtPercentLabel(v) {
    // 1 decimal everywhere so the outside host card and the drawer
    // chart display IDENTICAL precision (operator-flagged "82%" vs
    // "82.0% / 82.2%" mismatch — same data, two formats). Keep the
    // "<1%" sentinel for genuinely-tiny values so a 0.016% mount
    // doesn't read as "0.0%".
    const n = +v;
    if (!Number.isFinite(n) || n <= 0) {
      return '0%';
    }
    if (n < 1) {
      return '<1%';
    }
    return n.toFixed(1) + '%';
  },

  // ---- User-configurable datetime format -------------------------
  // The operator picks a single datetime format string in
  // Settings → Profile → Formats. Stored at
  // `me.ui_prefs.datetime_format`. The shared `_applyDateTimeFormat`
  // helper applies that format to a Date object via a small token
  // grammar. `fmtDate` uses the format verbatim; `fmtDateOnly`
  // strips time tokens; `fmtDateTimeShort` strips seconds.
  //
  // Token grammar (subset of Unicode LDML — close enough that the
  // operator's expectations from common date-format strings carry
  // over without surprises):
  //   yyyy  4-digit year
  //   yy    2-digit year
  //   MMMM  full month name (January)
  //   MMM   short month name (Jan)
  //   MM    2-digit month
  //   M     1-2 digit month
  //   dd    2-digit day
  //   d     1-2 digit day
  //   HH    24-hour 2-digit
  //   H     24-hour 1-2 digit
  //   hh    12-hour 2-digit
  //   h     12-hour 1-2 digit
  //   mm    2-digit minute
  //   m     1-2 digit minute
  //   ss    2-digit second
  //   s     1-2 digit second
  //   a     AM/PM marker (uppercase)
  //   '...' literal (anything inside single quotes is emitted
  //         as-is, so the operator can include letters that would
  //         otherwise be parsed as tokens, e.g. "yyyy-MM-dd'T'HH:mm:ss")
  DEFAULT_DATETIME_FORMAT: 'dd/MM/yyyy, HH:mm:ss',
  _userDateTimeFormat() {
    const pref = this.me && this.me.ui_prefs && this.me.ui_prefs.datetime_format;
    const s = (pref || '').toString().trim();
    return s || this.DEFAULT_DATETIME_FORMAT;
  },
  _applyDateTimeFormat(d, fmt) {
    if (!d || isNaN(d.getTime())) {
      return '—';
    }
    const pad = (n, w) => String(n).padStart(w, '0');
    const monthsLong = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    const monthsShort = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const Y = d.getFullYear();
    const M = d.getMonth() + 1;
    const D = d.getDate();
    const H = d.getHours();
    const m = d.getMinutes();
    const s = d.getSeconds();
    const ampm = H >= 12 ? 'PM' : 'AM';
    const h12 = ((H + 11) % 12) + 1;
    // Token order matters — longer tokens BEFORE shorter so `MM`
    // doesn't get matched as two separate `M` tokens. Single-quote
    // literals are extracted first to a placeholder so token-replace
    // doesn't see their content.
    const literals = [];
    const work = String(fmt || this.DEFAULT_DATETIME_FORMAT)
      .replace(/'([^']*)'/g, (_, lit) => {
        literals.push(lit);
        return `\x00${literals.length - 1}\x00`;
      });
    const replacements = [
      ['yyyy', String(Y)],
      ['yy', pad(Y % 100, 2)],
      ['MMMM', monthsLong[M - 1]],
      ['MMM', monthsShort[M - 1]],
      ['MM', pad(M, 2)],
      ['M', String(M)],
      ['dd', pad(D, 2)],
      ['d', String(D)],
      ['HH', pad(H, 2)],
      ['H', String(H)],
      ['hh', pad(h12, 2)],
      ['h', String(h12)],
      ['mm', pad(m, 2)],
      ['m', String(m)],
      ['ss', pad(s, 2)],
      ['s', String(s)],
      ['a', ampm],
    ];
    // Walk the format char-by-char, greedily matching the longest
    // token at each position. Anything not a token is passed
    // through verbatim (commas / slashes / colons / spaces).
    let out = '';
    let i = 0;
    while (i < work.length) {
      // Literal placeholder?
      if (work.charCodeAt(i) === 0) {
        const end = work.indexOf('\x00', i + 1);
        if (end > 0) {
          const idx = parseInt(work.slice(i + 1, end), 10);
          out += literals[idx] || '';
          i = end + 1;
          continue;
        }
      }
      let matched = false;
      for (const [tok, val] of replacements) {
        if (work.startsWith(tok, i)) {
          out += val;
          i += tok.length;
          matched = true;
          break;
        }
      }
      if (!matched) {
        out += work[i];
        i += 1;
      }
    }
    return out;
  },
  // Date + time using the operator's preferred format. Used
  // everywhere a full timestamp is shown (history, sessions, asset
  // cache, drawer timeline, etc). Default is `dd/MM/yyyy, HH:mm:ss`.
  fmtDate(ts) {
    if (!ts) {
      return '—';
    }
    const d = new Date(ts * 1000);
    return this._applyDateTimeFormat(d, this._userDateTimeFormat());
  },
  // Date only — derived from the user's full format by stripping
  // every time-related token (`H`/`h`/`m`/`s`/`a`) and any leading
  // / trailing whitespace, commas, dashes the strip leaves behind.
  fmtDateOnly(ts) {
    if (!ts) {
      return '—';
    }
    const d = new Date(ts * 1000);
    return this._applyDateTimeFormat(d, this._userDateOnlyFormat());
  },
  // Derive the date-only portion of the user's full datetime format
  // (strip H/h/m/s/a tokens + orphan separators). Shared by `fmtDateOnly`
  // and the history-filter input rendering so one source of truth
  // governs how dates surface across the app.
  _userDateOnlyFormat() {
    const full = this._userDateTimeFormat();
    let datePart = full
      .replace(/HH|H|hh|h/g, '')
      .replace(/mm/g, '')
      .replace(/(?<![A-Za-z])m(?![A-Za-z])/g, '')
      .replace(/ss/g, '')
      .replace(/(?<![A-Za-z])s(?![A-Za-z])/g, '')
      .replace(/(?<![A-Za-z])a(?![A-Za-z])/g, '');
    datePart = datePart
      .replace(/[,;:\s]+$/g, '')
      .replace(/^[,;:\s]+/g, '')
      .replace(/\s{2,}/g, ' ')
      .replace(/[,;:]\s*$/g, '');
    return datePart || 'dd/MM/yyyy';
  },
  // Time-only sibling of `_userDateOnlyFormat` — strips every
  // date-related token (`y`/`M`/`d`) from the user's full datetime
  // format and tidies the orphan separators the strip leaves
  // behind. Falls back to `HH:mm` when the user's pref had no time
  // component at all. Used by `tickHeaderClock` + the Stats chart
  // x-axis label paths so hour buckets honour the user's chosen
  // 24-h vs 12-h convention.
  _userTimeOnlyFormat() {
    const full = this._userDateTimeFormat();
    let timePart = full
      .replace(/yyyy/g, '').replace(/yy/g, '')
      .replace(/MMMM/g, '').replace(/MMM/g, '').replace(/MM/g, '')
      .replace(/(?<![A-Za-z])M(?![A-Za-z])/g, '')
      .replace(/dd/g, '')
      .replace(/(?<![A-Za-z])d(?![A-Za-z])/g, '');
    timePart = timePart
      .replace(/[,;:\s/-]+$/g, '')
      .replace(/^[,;:\s/-]+/g, '')
      .replace(/\s{2,}/g, ' ')
      .replace(/[/-]\s*[/-]/g, '')
      .trim();
    return timePart || 'HH:mm';
  },
  // Render an ISO `YYYY-MM-DD` string back through the user's date-only
  // format. Empty / invalid input returns empty string for use as input
  // value. Mirror of `_parseUserDate`.
  _formatIsoDate(iso) {
    if (!iso || typeof iso !== 'string') {
      return '';
    }
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    if (!m) {
      return '';
    }
    const d = new Date(parseInt(m[1], 10), parseInt(m[2], 10) - 1, parseInt(m[3], 10));
    if (isNaN(d.getTime())) {
      return '';
    }
    return this._applyDateTimeFormat(d, this._userDateOnlyFormat());
  },
  // Date + short time — drops only the seconds component from the
  // user's format (token `ss` → ''; token `:ss` cleans up the
  // dangling colon).
  fmtDateTimeShort(ts) {
    if (!ts) {
      return '—';
    }
    const d = new Date(ts * 1000);
    const full = this._userDateTimeFormat();
    const noSec = full
      .replace(/:ss/g, '')
      .replace(/\.ss/g, '')
      .replace(/(?<![A-Za-z])ss(?![A-Za-z])/g, '')
      .replace(/(?<![A-Za-z])s(?![A-Za-z])/g, '');
    return this._applyDateTimeFormat(d, noSec);
  },
  // HTML escape — XSS-safe for untrusted markdown body. Centralised
  // so the placeholder + final-render paths share the same
  // implementation.
  _escapeReleaseHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[c]);
  },
};
