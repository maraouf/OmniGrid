// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
// Constant-on-RHS covers natural `x === 'Foo'` form + the canonical
// `x == null` "is null or undefined" idiom (project uses the two-equals
// shape deliberately to catch both). Empty catch + unused catch `_`
// are the ignore-and-move-on shape on localStorage persistence + clipboard
// fallback. Anonymous functions are inline predicate / map callbacks.
// `Nested call to function 't'` fires because the i18n helper takes a
// dynamic string built by another helper — every `t(' + var)` site is
// flagged, but the wrapper IS the point of the helper. Negated-conditional
// + 4-negation count is the multi-condition filter predicate
// (`!q && allSevOn && !activePats.length` shape). `throw of exception
// caught locally` fires on the canonical `if (!r.ok) throw new Error(await r.text())`
// shape inside a try/catch — the throw constructs a unified error
// object that the catch then surfaces to the operator via toast. The
// alternative (inline `showToast` call directly in the if-block)
// duplicates the error-handling logic. The clipboard-copy fallback
// path uses `document.body.appendChild` + `document.execCommand('copy')`
// — both are legacy but deliberately gated behind a `navigator.clipboard`
// feature-detect for older / non-secure-context browsers. Alpine
// `$nextTick`, helper methods imported via the spread component,
// and runtime-bound state fields aren't visible to the static
// analyser at edit time so the WEAK warnings cluster.
// noinspection ConstantOnRightSideOfComparisonJS,JSConstantOnRightSideOfComparison
// noinspection AnonymousFunctionJS
// noinspection ContinueStatementJS,BreakStatementJS
// noinspection UnusedCatchParameterJS,EmptyCatchBlockJS
// noinspection OverlyComplexBooleanExpressionJS,OverlyComplexBooleanExpression
// noinspection NestedFunctionCallJS
// noinspection NegatedConditionalExpressionJS,JSNegatedConditionalExpression,FunctionWithMoreThanThreeNegationsJS
// noinspection ExceptionCaughtLocallyJS,JSExceptionCaughtLocally
// noinspection RedundantIfStatementJS,JSRedundantIfStatement
// noinspection JSDeprecatedSymbols
// noinspection ElementNotExported,JSUnusedGlobalSymbols,JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Logs admin (Admin → Logs) — live in-memory buffer + persistent
// log files browser.

// Per-flush memo for the Live-tab derived state. filteredLogLines() is the
// x-for source AND is read 3 more times in the same template, and the
// severity (x4) + pattern (x8) chip counts each walked logLines independently
// — up to ~16 passes over the ring buffer per reactive flush (the 2s poll
// append + any interaction). _logLiveStats() computes the filtered list + ALL
// chip counts in ONE pass, keyed on a cheap signature of the inputs (logLines
// length + last-row identity + filter text + severity snapshot + active
// patterns). Cleared on the next microtask (= next flush) like the other
// per-flush memos; the signature also busts mid-flush if a poll appended rows.
let _logLiveMemo = null;
let _logLiveMemoScheduled = false;

export default {
  // App-logs viewer state. Polled when the Logs tab is visible.
  // `logLines` is append-only during a session; clear() wipes both
  // the UI list and the server-side ring.
  logLines: [],
  // Sub-tab state for the Logs admin view. 'live' shows the
  // existing in-memory ring viewer; 'files' shows the persistent
  // daily log files with a download button + live-tail of a
  // selected file.
  logsSubTab: 'live',
  logFiles: [],
  logFilesDir: '',
  logSelectedFile: '',
  logFileBody: '',
  logFileAutoTail: true,
  // Default file-view window. Bumped 500 -> 2000 so opening a file
  // shows a fuller picture by default (the operator was missing
  // errors deeper than the last 500 lines); "All lines" stays
  // available via the selector for the whole file.
  logFileTailLines: 2000,
  // Tail-window options for the file viewer. 0 == "All lines" — the
  // backend reads the whole file when tail <= 0. Larger windows (or
  // All) let the operator investigate errors beyond the last 500
  // lines; the viewer was previously hard-capped at 500 with no way
  // to see more.
  logFileTailOptions: [500, 2000, 10000, 0],
  _logFileTimer: null,
  logFilesLoaded: false,

  // Admin → Logs owns the persistent-log retention tunable. Per
  // the section-saves-its-own-tunables convention, this handler
  // posts ONLY the Logs section's tunable in its own body —
  // never chains to saveTuning().
  _logsSectionTuningKeys() {
    return ['tuning_log_retention_days'];
  },
  logsSectionDirty() {
    try {
      const baseline = this._tuningBaselineMap();
      for (const k of this._logsSectionTuningKeys()) {
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
  async saveLogsSection() {
    if (this.tuningSaving) {
      return;
    }
    // Validate the section's tunables against TUNABLES bounds first.
    for (const k of this._logsSectionTuningKeys()) {
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
    this.tuningSaving = true;
    try {
      const body = {};
      for (const k of this._logsSectionTuningKeys()) {
        const v = (this.tuningForm || {})[k];
        body[k] = (v == null ? '' : String(v).trim());
      }
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      // Refresh tuning baseline so the section's dirty cue clears.
      await this.loadTuning();
      this.showToast(this.t('admin.config.saved_toast'));
    } catch (e) {
      this.showToast(this.t('admin.config.save_failed', {error: e.message}), 'error');
    } finally {
      this.tuningSaving = false;
    }
  },

  // ----- App logs -----------------------------------------------------
  async loadLogs(replace = false) {
    try {
      // `since` makes repeated polls cheap — backend only returns
      // lines newer than the last one we've already rendered. On
      // `replace`, pull the last 500 — the Live (stdout) tab stays at
      // 500 deliberately for render performance (operator preference);
      // for the full buffer / deeper history use the Files tab, which
      // serves the persistent file with its own (larger) line window.
      const qs = replace ? '?limit=500' : ('?since=' + this.logSinceTs);
      const r = await fetch('/api/logs' + qs);
      if (!r.ok) {
        return;
      }
      const d = await r.json();
      const lines = d.logs || [];
      if (replace) {
        this.logLines = lines;
      } else {
        if (lines.length) {
          // In-place push (NOT `this.logLines = [...old, ...new]`) so the
          // array identity is preserved — a wholesale reassign makes Alpine
          // re-evaluate every row + re-run colorizeLogText() per row each
          // 2s poll. The `since` delta is small so the spread is bounded.
          // (ADMIN-PERF-11.)
          this.logLines.push(...lines);
        }
      }
      // Cap the client-side buffer at 2× server MAX so the UI doesn't
      // grow forever even if the session stays on the tab for hours.
      // Trim in place (splice from the front) so the array identity survives.
      const cap = (d.max || 2000) * 2;
      if (this.logLines.length > cap) {
        this.logLines.splice(0, this.logLines.length - cap);
      }
      if (this.logLines.length) {
        this.logSinceTs = this.logLines[this.logLines.length - 1].ts;
      }
      // Autoscroll to bottom when the viewer is open and the user
      // hasn't scrolled up. $nextTick so the DOM has the new rows.
      this.$nextTick(() => {
        const box = document.getElementById('log-viewer');
        if (!box) {
          return;
        }
        const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
        if (replace || atBottom) {
          box.scrollTop = box.scrollHeight;
        }
      });
    } catch (_) {
    }
  },

  async clearLogs() {
    const res = await Swal.fire({
      title: this.t('admin.logs.clear_prompt_title'),
      text: this.t('admin.logs.clear_prompt_text'),
      icon: 'warning', showCancelButton: true,
      confirmButtonText: this.t('actions.clear'),
      cancelButtonText: this.t('actions.cancel'),
      confirmButtonColor: this._cssVar('--danger'),
    });
    if (!res.isConfirmed) {
      return;
    }
    try {
      const r = await fetch('/api/logs', {method: 'DELETE'});
      if (r.ok) {
        this.logLines = [];
        this.logSinceTs = 0;
        this.showToast(this.t('admin.logs.cleared'));
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  filteredLogLines() {
    return this._logLiveStats().filtered;
  },
  // Single-pass per-flush computation of the Live-tab derived state:
  // {filtered, sevCounts, patCounts}. Memoized on a cheap input signature so
  // the x-for source + the 4 severity chips + the 8 pattern chips all read
  // ONE walk of logLines per flush instead of ~16. (ADMIN-PERF-03.)
  _logLiveStats() {
    const lines = this.logLines || [];
    const q = (this.logFilter || '').toLowerCase();
    const sev = this.logSeverityFilter || {};
    const levels = this.logSeverityLevels || [];
    const allSevOn = levels.every(k => sev[k]);
    const activePats = this._activeLogPatterns();
    const last = lines.length ? lines[lines.length - 1] : null;
    const sig = lines.length + '|'
      + (last ? (last.ts + ':' + ((last.text || '') + '').length) : '') + '|'
      + q + '|'
      + levels.map(k => (sev[k] ? '1' : '0')).join('') + '|'
      + activePats.join(',');
    if (_logLiveMemo && _logLiveMemo.sig === sig) {
      return _logLiveMemo;
    }
    const patDefs = this.logPatternDefs || [];
    const patRe = {};
    const patCounts = {};
    for (const def of patDefs) {
      const re = this._getLogPatternRegex(def.id);
      if (re) {
        patRe[def.id] = re;
      }
      patCounts[def.id] = 0;
    }
    const sevCounts = {};
    for (const k of levels) {
      sevCounts[k] = 0;
    }
    const filtered = [];
    for (const l of lines) {
      const s = this.logSeverity(l);
      if (sevCounts[s] != null) {
        sevCounts[s]++;
      }
      const text = (l && (l.text || l.body || l.msg || '')) + '';
      for (const def of patDefs) {
        const re = patRe[def.id];
        if (re && re.test(text)) {
          patCounts[def.id]++;
        }
      }
      // Apply the active filter (same predicate the old filteredLogLines used).
      if (!allSevOn && !sev[s]) {
        continue;
      }
      if (q && !text.toLowerCase().includes(q)) {
        continue;
      }
      if (activePats.length && !this._lineMatchesAnyPattern(l, activePats)) {
        continue;
      }
      filtered.push(l);
    }
    _logLiveMemo = {sig, filtered, sevCounts, patCounts};
    if (!_logLiveMemoScheduled) {
      _logLiveMemoScheduled = true;
      queueMicrotask(() => {
        _logLiveMemo = null;
        _logLiveMemoScheduled = false;
      });
    }
    return _logLiveMemo;
  },
  // ---- Log pattern chip filter ---------------------------------
  // Resolve the set of currently-active pattern IDs (those whose
  // chip is ON). Empty array = no pattern filter applied. The
  // canonical filter check is "ANY-of-N" so multiple selected chips
  // act as a union, matching the operator's mental model: "show me
  // auth-cooldown OR probe-timeout lines".
  _activeLogPatterns() {
    const f = this.logPatternFilter || {};
    const out = [];
    for (const def of (this.logPatternDefs || [])) {
      if (f[def.id]) {
        out.push(def.id);
      }
    }
    return out;
  },
  // Compiled-regex cache. Build on first use; refresh when the
  // pattern set ever changes (no UI today alters it but a future
  // operator-custom-pattern feature would). Case-insensitive +
  // multiline-tolerant because some log lines wrap.
  _getLogPatternRegex(id) {
    let cache = this._logPatternRegexCache;
    if (!cache) {
      cache = {};
      for (const def of (this.logPatternDefs || [])) {
        try {
          cache[def.id] = new RegExp(def.regex_str, 'i');
        } catch (_) {
          // Skip a malformed pattern rather than crashing the
          // entire filter — operator-custom-pattern future-proofing.
          cache[def.id] = null;
        }
      }
      this._logPatternRegexCache = cache;
    }
    return cache[id] || null;
  },
  _lineMatchesAnyPattern(l, activeIds) {
    const text = (l && (l.text || l.body || l.msg || '')) + '';
    for (const id of activeIds) {
      const re = this._getLogPatternRegex(id);
      if (re && re.test(text)) {
        return true;
      }
    }
    return false;
  },
  // Multi-select pattern controls. Persist to localStorage so the
  // view survives a reload. setAllPatterns is bulk-on/off; the
  // canonical "no pattern filter" state is all OFF (every line
  // matches; same behaviour as severity-all-ON).
  toggleLogPattern(id) {
    if (!Object.prototype.hasOwnProperty.call(this.logPatternFilter, id)) {
      return;
    }
    this.logPatternFilter[id] = !this.logPatternFilter[id];
    this._persistLogPattern();
  },
  setAllLogPatterns(on) {
    for (const def of (this.logPatternDefs || [])) {
      this.logPatternFilter[def.id] = !!on;
    }
    this._persistLogPattern();
  },
  // Count of lines (in the LIVE ring buffer) matching one specific
  // pattern. Walks logLines once per chip render; cheap on the
  // capped ring. Returns the COUNT regardless of severity / text
  // filter so the chip's number reflects "how many lines could
  // this chip surface if you click it" rather than "how many lines
  // are visible right now".
  logPatternCount(id) {
    // Reads the per-flush single-pass memo (ADMIN-PERF-03) instead of walking
    // logLines per chip. Falls back to 0 for an unknown id.
    const c = this._logLiveStats().patCounts;
    return (c && c[id]) || 0;
  },
  // Same shape for the Files tab — counts parsed file rows
  // matching the pattern.
  logFilePatternCount(id) {
    const re = this._getLogPatternRegex(id);
    if (!re) {
      return 0;
    }
    let n = 0;
    for (const l of this.parsedLogFileLines()) {
      const text = (l && (l.text || l.body || l.msg || '')) + '';
      if (re.test(text)) {
        n++;
      }
    }
    return n;
  },
  logAnyPatternOn() {
    return this._activeLogPatterns().length > 0;
  },
  _persistLogPattern() {
    try {
      localStorage.setItem('logPatternFilter', JSON.stringify(this.logPatternFilter));
    } catch (_) {
    }
  },
  _restoreLogPattern() {
    try {
      const raw = localStorage.getItem('logPatternFilter');
      if (!raw) {
        return;
      }
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') {
        for (const def of (this.logPatternDefs || [])) {
          if (typeof parsed[def.id] === 'boolean') {
            this.logPatternFilter[def.id] = parsed[def.id];
          }
        }
      }
    } catch (_) {
    }
  },
  // Multi-select severity controls. Persist to localStorage so
  // the view survives a reload. setAll/errorsOnly mirror the same
  // shape as the Notifications event grid's bulk buttons.
  toggleLogSeverity(level) {
    this.logSeverityFilter[level] = !this.logSeverityFilter[level];
    this._persistLogSeverity();
  },
  // Count of log lines that resolve to a given severity level (used
  // in the per-pill counter chips). Walks logLines once per call —
  // the list is small (capped ring buffer) so this is cheap.
  logSeverityCount(level) {
    // Reads the per-flush single-pass memo (ADMIN-PERF-03).
    const c = this._logLiveStats().sevCounts;
    return (c && c[level]) || 0;
  },
  setAllLogSeverity(on) {
    for (const k of this.logSeverityLevels) {
      this.logSeverityFilter[k] = !!on;
    }
    this._persistLogSeverity();
  },
  setLogSeverityErrorsOnly() {
    for (const k of this.logSeverityLevels) {
      this.logSeverityFilter[k] = (k === 'error');
    }
    this._persistLogSeverity();
  },
  _persistLogSeverity() {
    try {
      localStorage.setItem('logSeverityFilter', JSON.stringify(this.logSeverityFilter));
    } catch (_) {
    }
  },
  _restoreLogSeverity() {
    try {
      const raw = localStorage.getItem('logSeverityFilter');
      if (!raw) {
        return;
      }
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') {
        for (const k of this.logSeverityLevels) {
          if (typeof parsed[k] === 'boolean') {
            this.logSeverityFilter[k] = parsed[k];
          }
        }
      }
    } catch (_) {
    }
  },
  // Persistent log files. Lists / views / live-tails the
  // daily files under /app/data/logs/. Download URL is the same
  // route, no streaming — the file is small (one day's logs).
  async loadLogFiles() {
    try {
      const r = await fetch('/api/admin/logs/files');
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      const d = await r.json();
      this.logFiles = Array.isArray(d.files) ? d.files : [];
      this.logFilesDir = d.log_dir || '';
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.logFilesLoaded = true;
    }
  },
  // The Live tab's template hands each row to `logSeverity(l)` to
  // pick the `log-line--<sev>` class. Parsed file rows already
  // carry an explicit `level` (extracted from the file line) — use
  // it directly when present so we don't run the regex scan a
  // second time. Falls back to logSeverity for raw INFO rows that
  // didn't match the regex.
  logSeverityFor(l) {
    return (l && l.level) ? l.level : this.logSeverity(l);
  },
  // Per-level count for the Files-tab pill chips.
  logFileSeverityCount(level) {
    let n = 0;
    for (const l of this.parsedLogFileLines()) {
      if (this.logSeverityFor(l) === level) {
        n++;
      }
    }
    return n;
  },
  // Copy the currently-filtered log view to the clipboard as plain
  // text. Format: "YYYY-MM-DD HH:MM:SS [stream] body" per line, so
  // the paste lands cleanly in issue trackers / chat apps with the
  // same visual shape as the viewer. Falls back to a selection-based
  // copy when navigator.clipboard isn't available (older browsers /
  // insecure contexts).
  async copyFilteredLogs() {
    const lines = this.filteredLogLines();
    if (!lines.length) {
      return;
    }
    const body = lines.map(l => {
      const ts = this.fmtDate ? this.fmtDate(l.ts) : String(l.ts);
      const stream = (l.stream || '').toUpperCase();
      return `${ts} [${stream}] ${l.text}`;
    }).join('\n');
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(body);
      } else {
        // Fallback — dispatch a textarea + execCommand('copy').
        // `position: fixed` + `opacity: 0` + `pointer-events: none`
        // is RTL-clean (no inset / left to flip) AND off-screen for
        // sighted users. Pre-fix used `left: -9999px` which placed
        // the textarea off the wrong edge under RTL — invisible
        // both ways but the pattern is non-RTL-safe and could be
        // copied as a template.
        const ta = document.createElement('textarea');
        ta.value = body;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        ta.style.pointerEvents = 'none';
        // Use `getElementsByTagName('body')[0]` instead of
        // `document.body` — the textarea must be in the body (not
        // documentElement) for selection + execCommand('copy') to
        // work, but the IDE's "document.body may produce inconsistent
        // results for XHTML" inspection flags every direct
        // `document.body` access. The TagName lookup is the IDE's
        // recommended XHTML-safe alternative.
        const bodyEl = document.getElementsByTagName('body')[0];
        bodyEl.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        bodyEl.removeChild(ta);
      }
      this.showToast(this.t('admin.logs.copied', {n: lines.length}), 'success');
    } catch (_) {
      this.showToast(this.t('admin.logs.copy_failed'), 'error');
    }
  },
  // Severity derived from the line body — stderr alone is coarse
  // (backend error prints go to stdout too via print()). We look
  // for textual markers anywhere in the line: ERROR / FAIL[ED|URE]
  // → 'error'; WARN / WARNING → 'warn'; otherwise 'info'.
  // Lowercase compare so "Error:", "error:", "ERROR:" all match.
  logSeverity(l) {
    if (!l) {
      return 'info';
    }
    const raw = l.text || '';
    const text = raw.toLowerCase();
    // stderr AND a tell-tale tag beats "happy-looking" body — but
    // a stderr line with no negative keywords stays at 'info' (our
    // own noisy prints go to stderr all the time).
    // PascalCase exception names (NameError / ValueError / HTTPException
    // / etc.) and traceback frame lines (`  File "...", line N`) also
    // classify as error so the operator's ERROR-filtered view shows
    // the full traceback body instead of just the header — mirrors
    // the backend's `_severity_for` regex in `logic/logs.py`.
    if (/\berror\b|\bfail(?:ed|ure)?\b|\btraceback\b|\bcritical\b|\bfatal\b/.test(text)) {
      return 'error';
    }
    // PascalCase exception names (NameError / HTTPException / etc.).
    if (/[A-Za-z]\w*(?:Error|Exception)\b/.test(raw)) {
      return 'error';
    }
    // Traceback frame lines + Python 3.11+ ExceptionGroup continuation
    // markers (`  | ...`, `  + ----- N -----`). Matches the backend's
    // `_severity_for` regex in `logic/logs.py`. Without these, the
    // operator's ERROR-filtered log viewer showed only the
    // `Traceback (most recent call last):` header.
    if (/^\s+(?:[|+]\s+)?File "[^"]+", line \d+/.test(raw)) {
      return 'error';
    }
    if (/^\s+[|+][-+\s]*\d*[-+\s]*$/.test(raw)) {
      return 'error';
    }
    if (/^\s+\|\s+\S/.test(raw)) {
      return 'error';
    }
    if (/\bwarn(?:ing)?\b|deprecat/.test(text)) {
      return 'warn';
    }
    // Explicit success/OK lines get their own class so
    // "[xxx] probe SUCCESS" / "OK —" read as green.
    if (/\bsuccess\b|\bok —|→ ok\b/i.test(raw)) {
      return 'ok';
    }
    return 'info';
  },
};
