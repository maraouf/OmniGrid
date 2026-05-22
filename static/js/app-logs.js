// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Logs admin (Admin → Logs) — live in-memory buffer + persistent
// log files browser.

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
  logFileTailLines: 500,
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
        throw new Error(await r.text());
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
      // `replace`, we pull the full tail and reset client state.
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
          this.logLines = [...this.logLines, ...lines];
        }
      }
      // Cap the client-side buffer at 2× server MAX so the UI doesn't
      // grow forever even if the session stays on the tab for hours.
      const cap = (d.max || 2000) * 2;
      if (this.logLines.length > cap) {
        this.logLines = this.logLines.slice(-cap);
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
    const q = (this.logFilter || '').toLowerCase();
    const sev = this.logSeverityFilter || {};
    const allSevOn = this.logSeverityLevels.every(k => sev[k]);
    if (!q && allSevOn) {
      return this.logLines;
    }
    return this.logLines.filter(l => {
      if (!allSevOn && !sev[this.logSeverity(l)]) {
        return false;
      }
      if (q && !l.text.toLowerCase().includes(q)) {
        return false;
      }
      return true;
    });
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
    let n = 0;
    for (const l of (this.logLines || [])) {
      if (this.logSeverity(l) === level) {
        n++;
      }
    }
    return n;
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
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
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
    const text = (l.text || '').toLowerCase();
    // stderr AND a tell-tale tag beats "happy-looking" body — but
    // a stderr line with no negative keywords stays at 'info' (our
    // own noisy prints go to stderr all the time).
    if (/\berror\b|\bfail(?:ed|ure)?\b|\btraceback\b|\bcritical\b|\bfatal\b/.test(text)) {
      return 'error';
    }
    if (/\bwarn(?:ing)?\b|deprecat/.test(text)) {
      return 'warn';
    }
    // Explicit success/OK lines get their own class so
    // "[xxx] probe SUCCESS" / "OK —" read as green.
    if (/\bsuccess\b|\bok —|→ ok\b/i.test(l.text || '')) {
      return 'ok';
    }
    return 'info';
  },
};
