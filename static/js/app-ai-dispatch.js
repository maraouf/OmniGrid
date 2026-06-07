// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName
// noinspection OverlyComplexBooleanExpressionJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression,ExceptionCaughtLocallyJS
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,RedundantLocalVariableJS,JSReusedLocalVariable
// noinspection HtmlUnknownTag,HtmlEmptyContent,HtmlEmptyTagsRecommendation,InnerHTMLJS,VoidExpressionJS,JSVoidExpression
// noinspection JSIfStatementsCanBeSimplified,IfStatementSimplifyable,RedundantIfStatementJS,JSPotentiallyInvalidUsageOfThis
// Comprehensive per-inspection suppressions mirror the shape
// app-ai-admin.js settled on. Idioms covered: constants on the right
// of comparisons (modern ESLint default); anonymous arrow callbacks
// for Alpine bindings; chained map+filter / nested t() lookups;
// short uppercase locals (W/H/X/Y/PAD/RX) for SVG geometry; magic
// numbers for unit-conversion constants (60/3600/86400 seconds,
// HTTP status codes, RFC port numbers); Alpine-called methods that
// PyCharm can't trace through x-on:click; `throw new Error(...)`
// inside try blocks for unified error handling; `<hostname>` /
// `<empty>` / `<x>` literal placeholders inside JS strings that
// PyCharm's HTML parser mistakes for markup.
/* global Alpine, Swal, I18N, t */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA AI palette dispatch wrappers + per-host disk-projection charts.
//
// SPLIT FROM `app-ai.js`. Cross-method `this.X` references keep
// working through the `_mergeKeepDescriptors` chain in app.js.

export default {
  // ----- AI palette: per-host disk-projection charts ------------------
  // Backend HOSTS protocol returns `j.hosts: [<id>, ...]` alongside the
  // answer text. We render a placeholder shell per id inline in the
  // SweetAlert body, then async-fan-out fetches and inject the SVG
  // chart once each `/api/hosts/{id}/disk-projection` resolves.
  _renderAiHostChartShells(hostIds) {
    if (!Array.isArray(hostIds) || hostIds.length === 0) {
      return '';
    }
    const shells = hostIds.map(hid => {
      const safeAttr = String(hid).replace(/[^A-Za-z0-9_.-]/g, '_');
      return ('<div class="ai-resp-chart" data-disk-host="' + safeAttr + '">'
        + '<div class="ai-resp-chart-header">'
        + '<span class="ai-resp-chart-title">' + this._logEscape(hid) + '</span>'
        + '<span class="ai-resp-chart-status">'
        + this._logEscape(this.t('command_palette.ai.disk_chart.loading') || 'Loading projection…')
        + '</span>'
        + '</div>'
        + '<div class="ai-resp-chart-body">'
        + '<span class="spin" aria-hidden="true"></span>'
        + '</div>'
        + '</div>');
    }).join('');
    return '<div class="ai-resp-charts">' + shells + '</div>';
  },
  // AI sidebar version — same fetch + render pipeline as
  // `_populateAiHostChart` but scoped by turn ts so multi-turn
  // chats don't collide on the same `[data-disk-host=X]` element.
  // ``chartKind`` (optional) drives the endpoint dispatch:
  //   ""  / "disk_projection" → /api/hosts/{id}/disk-projection
  //   "memory_history"        → /api/hosts/history?host_id={id}&hours=24, mp series
  //   "cpu_history"           → /api/hosts/history?host_id={id}&hours=24, cp series
  // Default kind is disk_projection for back-compat with legacy
  // turns that pre-date the CHART: directive.
  async _populateAiSidebarHostChart(hostId, turnTs, chartKind) {
    const safeAttr = String(hostId).replace(/[^A-Za-z0-9_.-]/g, '_');
    const outerSel = '[data-disk-host="' + safeAttr + '"][data-turn-ts="' + turnTs + '"]';
    let outer = document.querySelector(outerSel);
    let waited = 0;
    while (!outer && waited < 3000) {
      await new Promise(resolve => setTimeout(resolve, 200));
      waited += 200;
      outer = document.querySelector(outerSel);
    }
    if (!outer) {
      return;
    }
    const slot = outer.querySelector('[data-chart-slot]') || outer;
    const kind = chartKind || 'disk_projection';
    // Dispatch on chart_kind. Memory + CPU history charts share the
    // same `/api/hosts/history` endpoint — they only differ on
    // which series we plot (mp = memory percent, cp = cpu percent).
    // Keep them in one branch with a small kind-aware renderer.
    if (kind === 'memory_history' || kind === 'cpu_history') {
      try {
        // Match the drawer's loadHostHistory contract — pass BOTH
        // `system_id` (the host's beszel_id, when known) AND
        // `host_id`. Pre-fix only `host_id` was sent, which made
        // `/api/hosts/history` take the NE-only branch in
        // `api_hosts_history`. Beszel-monitored hosts (like
        // opnsense) have their CPU/Mem time-series in the Beszel
        // collection — the NE branch returned an empty series and
        // the AI chart rendered "No history points in the past 24
        // h". Looking up beszel_id from `this.hosts` mirrors what
        // every drawer chart helper does.
        const _hostRow = (this.hosts || []).find(h => h && h.id === hostId);
        const _beszelId = (_hostRow && _hostRow.beszel_id) || '';
        const _qs = new URLSearchParams({
          host_id: hostId,
          hours: '24',
        });
        if (_beszelId) {
          _qs.set('system_id', _beszelId);
        }
        const r = await fetch('/api/hosts/history?' + _qs.toString());
        if (!r.ok) {
          slot.innerHTML = this._renderHostHistoryInner(hostId, kind, null,
            this.t('command_palette.ai.history_chart.error') || 'Could not load history');
          return;
        }
        const data = await r.json();
        // User-flagged: when there are no usable history points,
        // don't draw an empty chart shell — remove the outer
        // element entirely so the AI response reads cleanly without
        // a visible "No history points in the past 24 h" placeholder.
        // The conversation-text answer already states the current
        // value ("memory usage is 79%"); a blank chart card under
        // it is noise rather than signal. Same field shape the
        // renderer uses below — keep the predicate in lock-step
        // with `_renderHostHistoryInner`'s extraction.
        const isMemoryKind = kind === 'memory_history';
        const _seriesPreview = (data && Array.isArray(data.series)) ? data.series : [];
        const _fieldKey = isMemoryKind ? 'mp' : 'cpu';
        const _usable = _seriesPreview.filter(p => {
          const v = Number(p && p[_fieldKey]);
          const ts = Number(p && p.t) || 0;
          return Number.isFinite(v) && ts > 0;
        });
        if (_usable.length < 2) {
          try {
            outer.remove();
          } catch {
            // DOM node already detached / parent gone — nothing to remove.
          }
          return;
        }
        slot.innerHTML = this._renderHostHistoryInner(hostId, kind, data, null);
      } catch (e) {
        slot.innerHTML = this._renderHostHistoryInner(hostId, kind, null,
          e.message || String(e));
      }
      return;
    }
    // Default — disk projection (legacy + explicit "disk_projection").
    try {
      const r = await fetch('/api/hosts/' + encodeURIComponent(hostId) + '/disk-projection');
      if (!r.ok) {
        slot.innerHTML = this._renderDiskProjectionInner(hostId, null,
          this.t('command_palette.ai.disk_chart.error') || 'Could not load projection');
        return;
      }
      const data = await r.json();
      slot.innerHTML = this._renderDiskProjectionInner(hostId, data, null);
    } catch (e) {
      slot.innerHTML = this._renderDiskProjectionInner(hostId, null, e.message || String(e));
    }
  },
  // Render shells for the sidebar — same look as the modal palette
  // shells but tagged with `data-turn-ts` so the populator can
  // disambiguate multi-turn charts referencing the same host.
  // Extension point used by the sidebar populator when it walks
  // multi-turn host references; PyCharm can't trace those call
  // sites because they're dispatched dynamically through Alpine.
  // noinspection JSUnusedGlobalSymbols
  _renderAiSidebarHostChartShells(hostIds, turnTs) {
    if (!Array.isArray(hostIds) || hostIds.length === 0) {
      return '';
    }
    const shells = hostIds.map(hid => {
      const safeAttr = String(hid).replace(/[^A-Za-z0-9_.-]/g, '_');
      return ('<div class="ai-resp-chart" data-disk-host="' + safeAttr + '" data-turn-ts="' + turnTs + '">'
        + '<div class="ai-resp-chart-header">'
        + '<span class="ai-resp-chart-title">' + this._logEscape(hid) + '</span>'
        + '<span class="ai-resp-chart-status">'
        + this._logEscape(this.t('command_palette.ai.disk_chart.loading') || 'Loading projection…')
        + '</span>'
        + '</div>'
        + '<div class="ai-resp-chart-body">'
        + '<span class="spin" aria-hidden="true"></span>'
        + '</div>'
        + '</div>');
    }).join('');
    return '<div class="ai-resp-charts">' + shells + '</div>';
  },
  async _populateAiHostChart(hostId) {
    const safeAttr = String(hostId).replace(/[^A-Za-z0-9_.-]/g, '_');
    const shell = document.querySelector('[data-disk-host="' + safeAttr + '"]');
    if (!shell) {
      return;
    }
    try {
      const r = await fetch(
        '/api/hosts/' + encodeURIComponent(hostId) + '/disk-projection?hours=720'
      );
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        shell.innerHTML = this._renderDiskProjectionInner(hostId, null,
          (data && data.error) || ('HTTP ' + r.status));
        return;
      }
      shell.innerHTML = this._renderDiskProjectionInner(hostId, data, null);
    } catch (e) {
      shell.innerHTML = this._renderDiskProjectionInner(hostId, null, e.message || String(e));
    }
  },
  // Stats → Database 90-day growth chart. Takes the projection
  // array from `/api/admin/stats/database` (each point: {ts, bytes,
  // low, high}) and emits an SVG with a central line + a confidence
  // band. Width is responsive (100% of container); height fixed at
  // 220px. Mirrors the disk-projection chart visual treatment but
  // standalone since the data shape and the calling surface are
  // distinct.
  // SVG bar chart for the Stats → Samples "Samples written per
  // bucket" panel. Replaces the earlier pure-CSS bar chart so x +
  // y axes can render proper tick labels (operator-flagged
  // 2026-05-11). Backend `/api/admin/stats/samples?range=X` returns
  // `bucket_totals: [{date: <bucket-key>, total: N}, ...]`
  // ascending; bucket-key is `YYYY-MM-DD` for daily windows,
  // `YYYY-MM-DDTHH:00` for hourly (24h), `YYYY-MM-DDTHH:MM` for
  // minutely (1h). Y-axis: 5 evenly-spaced ticks rounded to nice
  // numbers. X-axis: ~6 tick labels, evenly-spaced across the
  // window (first/last always shown). Hover on a bar reveals
  // `<bucket>: N rows` via :title for cheap tooltip without a
  // separate tooltip layer.
  // Avg-response-time trend chart for Stats → AI Cost. Same SVG
  // shape as `_renderSamplesBucketChart` (axes + gridlines + bars)
  // so the visual treatment lands consistently across Stats charts
  // per the operator-flagged unification ask. Points
  // carry `{bucket_ts, avg_ms, jobs}` from the backend's bucketed
  // SQL query; bucket size adapts per the unified rule (1h/24h →
  // hour, 7d/30d → day, 90d → week).
  // Called from `static/_partials/stats/ai_cost.html` via x-html —
  // PyCharm can't trace HTML-attribute calls into ESM modules.
  // noinspection JSUnusedGlobalSymbols
  _renderAiCostTrendChart(points, range) {
    if (!Array.isArray(points) || points.length === 0) {
      return '';
    }
    // 90d range packs the X-axis with dense `dd/MM/yyyy` labels.
    // Rotate -45° + reserve a wider bottom pad to keep dates legible
    // (parallels `_renderSamplesBucketChart`'s treatment).
    const W = 720, H = 220;
    const rotateXLabels = ((range || '').toString() === '90d');
    const PAD_L = 56, PAD_R = 12, PAD_T = 12, PAD_B = rotateXLabels ? 60 : 28;
    const plotW = W - PAD_L - PAD_R;
    const plotH = H - PAD_T - PAD_B;
    const n = points.length;
    const barW = plotW / n;
    const yMaxRaw = Math.max(1, ...points.map(p => p.avg_ms || 0));
    const niceMax = (v) => {
      if (v <= 0) {
        return 1;
      }
      const exp = Math.pow(10, Math.floor(Math.log10(v)));
      const r = v / exp;
      const stepMul = (r <= 1) ? 1 : (r <= 2) ? 2 : (r <= 5) ? 5 : 10;
      return stepMul * exp;
    };
    const yMax = niceMax(yMaxRaw);
    const Y = (v) => PAD_T + (1 - (v / Math.max(1, yMax))) * plotH;
    const X = (i) => PAD_L + i * barW;
    const yTicks = [0, 1, 2, 3, 4].map(i => {
      const v = (yMax / 4) * i;
      return {v, y: Y(v).toFixed(1), label: this.t('common.unit_ms_inline', {n: Math.round(v).toLocaleString()})};
    });
    const tickCount = Math.min(6, n);
    const xIdxs = [];
    if (tickCount === 1) {
      xIdxs.push(0);
    } else {
      for (let i = 0; i < tickCount; i++) {
        xIdxs.push(Math.round((i / (tickCount - 1)) * (n - 1)));
      }
    }
    const dedup = Array.from(new Set(xIdxs));
    const r = (range || '30d').toString();
    // Per-bucket x-tick formatter — hour buckets render the
    // user-pref time-only format, day + week buckets render the
    // user-pref date-only format. Both routes through the shared
    // `_user{Time,Date}OnlyFormat` strippers so the chart honours
    // the operator's Formats preference (Settings → Profile).
    const _timeOnlyFmt = this._userTimeOnlyFormat();
    const _dateOnlyFmt = this._userDateOnlyFormat();
    const fmtXLabel = (ts) => {
      if (!ts) {
        return '';
      }
      const dt = new Date(Number(ts) * 1000);
      if (r === '1h' || r === '24h') {
        return this._applyDateTimeFormat(dt, _timeOnlyFmt);
      }
      return this._applyDateTimeFormat(dt, _dateOnlyFmt);
    };
    const esc = (s) => this._logEscape(String(s));
    let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" preserveAspectRatio="none" style="display:block;">';
    for (const t of yTicks) {
      svg += '<line x1="' + PAD_L + '" y1="' + t.y + '" x2="' + (W - PAD_R) + '" y2="' + t.y
        + '" stroke="var(--chart-grid)" stroke-width="0.5" stroke-dasharray="2,2"></line>';
      svg += '<text x="' + (PAD_L - 6) + '" y="' + (Number(t.y) + 4) + '" text-anchor="end" fill="var(--text-faint)" class="stats-chart-axis">'
        + esc(t.label) + '</text>';
    }
    const totalBarW = Math.max(1, barW - 1);
    for (let i = 0; i < n; i++) {
      const p = points[i];
      const v = Number(p.avg_ms || 0);
      const bx = X(i).toFixed(2);
      const by = Y(v).toFixed(2);
      const bh = (PAD_T + plotH - Y(v)).toFixed(2);
      const tip = this.t('stats.ai_cost.rt_trend.bar_tooltip', {
        date: fmtXLabel(p.bucket_ts),
        ms: Math.round(v).toLocaleString(),
        jobs: Number(p.jobs || 0).toLocaleString(),
      });
      svg += '<rect x="' + bx + '" y="' + by + '" width="' + totalBarW.toFixed(2)
        + '" height="' + bh + '" fill="var(--primary)" fill-opacity="0.7">'
        + '<title>' + esc(tip) + '</title>'
        + '</rect>';
    }
    for (const i of dedup) {
      const p = points[i];
      if (!p) {
        continue;
      }
      const cx = (X(i) + barW / 2).toFixed(1);
      if (rotateXLabels) {
        const ly = (H - PAD_B + 16).toFixed(1);
        svg += '<text x="' + cx + '" y="' + ly + '" text-anchor="end" fill="var(--text-faint)" class="stats-chart-axis"'
          + ' transform="rotate(-45 ' + cx + ' ' + ly + ')">'
          + esc(fmtXLabel(p.bucket_ts)) + '</text>';
      } else {
        svg += '<text x="' + cx + '" y="' + (H - 8) + '" text-anchor="middle" fill="var(--text-faint)" class="stats-chart-axis">'
          + esc(fmtXLabel(p.bucket_ts)) + '</text>';
      }
    }
    svg += '</svg>';
    return svg;
  },
  async _runCommandPaletteAi(payload) {
    const query = (payload && payload.query) || '';
    if (!query) {
      return;
    }
    // Defence-in-depth — re-check the gate at activation time. The
    // result-build gate is already strict, but a stale cached row
    // could survive a master-toggle-off if the operator opens the
    // palette → toggles AI off in another tab → activates the AI
    // row that was already rendered. This re-check + the backend's
    // identical check make sure the AI provider is never called
    // when AI is disabled.
    if (!this._aiPaletteSurfaceEnabled()) {
      if (typeof this.showToast === 'function') {
        this.showToast(this.t('command_palette.ai.disabled')
          || 'AI is disabled — enable it in Admin → AI Integration', 'error');
      }
      return;
    }
    // Rich context — built via the shared `_buildAiPaletteContext`
    // helper so the sidebar + modal palette + any future AI surface
    // all see the same structured-host shape (id / label / status /
    // cpu_pct / mem_pct / disk_pct / disk_free_gb / disk_total_gb /
    // uptime_s / paused / providers). Without these the model
    // hallucinates host names + values when asked data questions.
    const ctx = this._buildAiPaletteContext();
    // Open a SweetAlert immediately so the operator sees feedback
    // even if the round-trip takes 5-10s.
    const swal = (window.Swal || (typeof Swal !== 'undefined' && Swal));
    if (!swal) {
      if (typeof this.showToast === 'function') {
        this.showToast('SweetAlert unavailable', 'error');
      }
      return;
    }
    swal.fire({
      title: this.t('command_palette.ai.thinking_title') || 'Thinking…',
      html: '<div class="fs-sm text-[var(--text-faint)] mono" style="text-align:left;">'
        + this._logEscape(query) + '</div>'
        + '<div class="mt-3"><span class="spin-lg" aria-hidden="true"></span></div>',
      showConfirmButton: false,
      allowOutsideClick: true,
    });
    try {
      const r = await fetch('/api/ai/palette', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({query, context: ctx}),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) {
        const detail = (j && j.detail) || (this.t('toasts.failed') || 'Failed');
        swal.fire({
          icon: 'error',
          title: this.t('command_palette.ai.error_title') || 'AI request failed',
          // role="alert" + aria-live="assertive" — backstops
          // SweetAlert's `icon: 'error'` semantic so screen readers
          // announce the failure body verbatim. Belt-and-braces; the
          // SweetAlert role="dialog" + icon image alone don't always
          // trigger an announcement on every reader.
          html: '<div class="fs-sm mono" role="alert" aria-live="assertive" '
            + 'style="text-align:left;white-space:pre-wrap;">'
            + this._logEscape(detail) + '</div>',
        });
        return;
      }
      const rawAnswer = (j.text || '').trim();
      const answerIsEmpty = !rawAnswer;
      const answer = rawAnswer || (this.t('command_palette.ai.empty_response') || '(empty response)');
      const tokens = (j.tokens && (j.tokens.prompt + j.tokens.completion)) || 0;
      const fmtNum = (n) => Number.isFinite(+n) ? (+n).toLocaleString() : String(n || 0);
      // Backend stamps `j.actions = ["<id>", ...]` (ordered list)
      // when the AI wants to invoke one or more canonical command-
      // palette actions (mark_all_notifications_read / refresh /
      // theme_dark / etc.). Single-action responses also populate
      // the legacy `j.action` field for backward-compat. Multi-
      // action queries ("refresh and cleanup") emit multiple ids
      // — fire each in order. Destructive actions still go through
      // `_runCommandPaletteAction`'s SweetAlert confirm gate (or
      // the action's own popup when `defer_confirm_to_run` is set,
      // e.g. cleanup-stopped's container-list confirm); the
      // sequential dispatch awaits each one so the operator sees
      // the popups in order.
      let actionIds = Array.isArray(j.actions) ? j.actions.map(s => String(s || '').trim()).filter(Boolean) : [];
      if (!actionIds.length && j.action) {
        actionIds = [String(j.action).trim()];
      }
      const actionDescs = actionIds
        .map(id => this._actionDescriptorById(id))
        .filter(Boolean);
      const actionRanLine = actionDescs.length
        ? '<div class="ai-resp-action-ran">'
        + '<span aria-hidden="true">✓</span>'
        + '<span>' + this._logEscape(
          (this.t('command_palette.ai.action_ran') || 'Ran action: ')
          + actionDescs.map(d => d.label || d.id).join(' → '))
        + '</span>'
        + '</div>'
        : '';
      // Build the metadata chip strip — provider / model / timing /
      // tokens — each as its own pill so the eye can pick one out
      // without parsing a single concatenated `·`-separated subline.
      const metaChips = [];
      if (j.provider) {
        metaChips.push('<span class="ai-resp-meta-chip"><strong>'
          + this._logEscape(j.provider) + '</strong></span>');
      }
      if (j.model) {
        metaChips.push('<span class="ai-resp-meta-chip">'
          + this._logEscape(j.model) + '</span>');
      }
      if (j.response_time_ms) {
        metaChips.push('<span class="ai-resp-meta-chip">'
          + this.t('common.unit_ms_inline', {n: fmtNum(j.response_time_ms)})
          + '</span>');
      }
      if (tokens) {
        metaChips.push('<span class="ai-resp-meta-chip">'
          + fmtNum(tokens) + ' tokens</span>');
      }
      swal.fire({
        icon: 'info',
        title: this.t('command_palette.ai.answer_title') || 'AI response',
        // Class-based layout (`.ai-resp*` rules in style.css) instead
        // of inline-style spaghetti — gives us proper Question /
        // Answer hierarchy with distinct accent borders (primary for
        // the operator's question, success for the AI answer), a
        // chip-strip metadata footer, and a contained "Ran action"
        // pill below the answer. CSS adapts to dark / light theme
        // via the existing token system.
        html: '<div class="ai-resp">'
          + '<div class="ai-resp-section">'
          + '<div class="ai-resp-label is-question">'
          + '<span class="ai-resp-label-dot" aria-hidden="true"></span>'
          + this._logEscape(this.t('command_palette.ai.question_label') || 'Question')
          + '</div>'
          + '<div class="ai-resp-question">'
          + this._logEscape(query)
          + '</div>'
          + '</div>'
          + '<div class="ai-resp-section">'
          + '<div class="ai-resp-label is-answer">'
          + '<span class="ai-resp-label-dot" aria-hidden="true"></span>'
          + this._logEscape(this.t('command_palette.ai.answer_label') || 'Answer')
          + '</div>'
          // Empty-response state gets `role="status" aria-live="polite"`
          // so screen readers announce "(empty response)" instead of
          // skipping it as silent body text. Real-content responses
          // stay as plain divs (no aria-live) — re-announcing every
          // multi-paragraph answer would be noisy.
          + (answerIsEmpty
            ? '<div class="ai-resp-answer" role="status" aria-live="polite" style="opacity:0.7;">'
            : '<div class="ai-resp-answer">')
          // Render the AI's answer through the safe-Markdown helper
          // so `**host**` renders as bold, `1. ...` lines render as
          // a real ordered list, etc. Empty-response sentinel stays
          // plain text — the helper short-circuits on falsy input.
          + (answerIsEmpty ? this._logEscape(answer) : this._renderAiAnswerMd(answer))
          + '</div>'
          + actionRanLine
          + '</div>'
          // HOSTS-protocol chart shells. Backend strips the
          // `HOSTS: ...` trailer from the answer text and returns
          // `j.hosts = [...]`; for each id we render a placeholder
          // here, then fan out per-host /api/hosts/{id}/disk-projection
          // fetches AFTER swal.fire() opens the modal and replace each
          // shell with the rendered chart inline.
          + this._renderAiHostChartShells(Array.isArray(j.hosts) ? j.hosts : [])
          + (metaChips.length
            ? '<div class="ai-resp-meta">' + metaChips.join('') + '</div>'
            : '')
          + '</div>',
        width: 720,
      });
      // Kick off the per-host projection fetches. swal.fire() returned
      // synchronously (no `await`) so the modal is mounted; we can
      // querySelector each shell and inject the SVG once the fetch
      // resolves. Errors land as a small "no data" hint per host so
      // one failed projection doesn't poison the whole modal.
      if (Array.isArray(j.hosts) && j.hosts.length > 0) {
        for (const hid of j.hosts) {
          // Fire-and-forget per-host populate — `void` makes the
          // discard-the-promise intent explicit. We deliberately do
          // NOT await so the per-host fetches run in parallel; each
          // _populateAiHostChart manages its own error fallback (a
          // "no data" hint on its shell), so one failure can't poison
          // the others.
          void this._populateAiHostChart(hid);
        }
      }
      // Fire the action(s) AFTER the answer modal renders. Multi-
      // action queries fire each descriptor sequentially so the
      // operator sees popups in the order the AI proposed them —
      // typically non-destructive first (e.g. `refresh`), then
      // destructive (e.g. `cleanup_stopped` which surfaces its own
      // container-list confirm). Each call is awaited so a
      // destructive action's confirm popup blocks the next action
      // until the operator decides; cancelling the confirm short-
      // circuits via `_runCommandPaletteAction`'s `if (!ok) return`
      // and the loop continues to the next action.
      if (actionDescs.length) {
        // Fire-and-forget IIFE — runs the action descriptors
        // sequentially in the background so the answer modal stays
        // responsive while the action chain executes. `void` makes the
        // discard-the-promise intent explicit; the inner loop awaits
        // each action so the operator sees confirm popups in order.
        void (async () => {
          for (const desc of actionDescs) {
            try {
              await this._runCommandPaletteAction(desc);
            } catch (e) {
              if (typeof this.showToast === 'function') {
                this.showToast(this.t('toasts.failed_with_error',
                  {error: (e && e.message) || String(e)}), 'error');
              }
            }
          }
        })();
      }
    } catch (e) {
      swal.fire({
        icon: 'error',
        title: this.t('command_palette.ai.error_title') || 'AI request failed',
        html: '<div class="fs-sm mono" style="text-align:left;">' + this._logEscape(e.message || String(e)) + '</div>',
      });
    }
  },
  // Submit the popover's draft tag. Hits the same backend retag
  // endpoint the original button used; the new `tag` body field
  // carries the operator's chosen target. Empty draft falls back
  // to "latest" (server-side validator also defaults).
  // AI-palette dispatch wrapper for schedule CRUD. Reads the
  // structured payload from `opts.data` (parsed by the backend
  // from the AI's `ACTION_DATA: {<json>}` directive) and dispatches
  // to the existing /api/schedules endpoints — same as the Admin →
  // Schedules table buttons. Three ops:
  //   - 'create': POST /api/schedules with the full payload
  //   - 'update': PATCH /api/schedules/{id} after resolving id
  //               (data.id, OR data.name → match against this.schedules)
  //   - 'delete': DELETE /api/schedules/{id} (same id resolution)
  // Delete gates on the inline-confirm chip via the descriptor's
  // `destructive: true` + `defer_confirm_to_run: true` upstream;
  // by the time the runner is called with skipConfirm=true the
  // operator has already approved.
  // AI palette item-write dispatcher. Resolves the target item /
  // stack / host from `opts.actionItem` (the AI's `ACTION_ITEM:`
  // directive) with a fallback to the currently-open drawer; then
  // calls the matching helper with `{skipConfirm: true}` so the
  // inner SwAl popup is bypassed (the inline-confirm chip in the
  // sidebar OR the modal-palette's outer confirm already handled
  // approval). Verb is one of:
  //   update_stack / update_container / restart_service /
  //   restart_container / remove_container
  async _aiItemDispatch(verb, opts) {
    const params = opts || {};
    const targetName = (params.actionItem || '').toString().trim();
    const skipConfirm = !!params.skipConfirm;
    // Resolve target item — by name (case-insensitive match against
    // raw_id / id / name / stack), else fall through to drawerItem.
    let item = null;
    const items = Array.isArray(this.items) ? this.items : [];
    if (targetName) {
      const needle = targetName.toLowerCase();
      item = items.find(i => i && (
        (i.name && i.name.toLowerCase() === needle) ||
        (i.id && String(i.id).toLowerCase() === needle) ||
        (i.raw_id && String(i.raw_id).toLowerCase() === needle) ||
        (i.stack && i.stack.toLowerCase() === needle)
      )) || null;
    }
    if (!item) {
      item = this.drawerItem;
    }
    if (!item) {
      this.showToast(
        this.t('toasts_extra.ai_action_no_target')
        || ('Couldn\'t resolve target item for ' + verb + '. Open the drawer or pass ACTION_ITEM.'),
        'error',
      );
      return;
    }
    const dispatchOpts = {skipConfirm};
    try {
      if (verb === 'update_stack') {
        // Stack-level update — if the item carries stack_id use
        // itemAction's stack path; otherwise look up the stack object
        // and use updateStack(stack).
        if (item.stack_id) {
          await this.itemAction(item, dispatchOpts);
        } else if (typeof this.updateStack === 'function') {
          const stacks = Array.isArray(this.stacks) ? this.stacks : [];
          const stack = stacks.find(s => s && s.name === (item.stack || item.name));
          if (stack) {
            await this.updateStack(stack, dispatchOpts);
          } else {
            await this.itemAction(item, dispatchOpts);
          }
        }
      } else if (verb === 'update_container') {
        await this.itemAction(item, dispatchOpts);
      } else if (verb === 'restart_service' || verb === 'restart_container') {
        await this.restartItem(item, dispatchOpts);
      } else if (verb === 'remove_container') {
        await this.removeContainer(item, dispatchOpts);
      } else {
        this.showToast('Unknown AI item verb: ' + verb, 'error');
      }
    } catch (e) {
      this.showToast(
        this.t('toasts.failed_with_error', {error: (e && e.message) || String(e)}),
        'error',
      );
    }
  },
  // AI palette host-level dispatcher — covers prune_node /
  // hosts_bulk_pause / hosts_bulk_resume. Target host(s) come from
  // `opts.actionItem` (single host) or the existing selection set.
  async _aiHostDispatch(verb, opts) {
    const params = opts || {};
    const skipConfirm = !!params.skipConfirm;
    const targetName = (params.actionItem || '').toString().trim();
    const dispatchOpts = {skipConfirm};
    try {
      if (verb === 'prune_node') {
        if (!targetName) {
          this.showToast(
            this.t('toasts_extra.ai_action_no_target')
            || 'Specify ACTION_ITEM: [hostname] for prune_node.',
            'error',
          );
          return;
        }
        await this.pruneNode(targetName, dispatchOpts);
      } else if (verb === 'hosts_bulk_pause' && typeof this.bulkPauseHosts === 'function') {
        await this.bulkPauseHosts(dispatchOpts);
      } else if (verb === 'hosts_bulk_resume' && typeof this.bulkResumeHosts === 'function') {
        await this.bulkResumeHosts(dispatchOpts);
      }
    } catch (e) {
      this.showToast(
        this.t('toasts.failed_with_error', {error: (e && e.message) || String(e)}),
        'error',
      );
    }
  },
  async _aiScheduleDispatch(op, opts) {
    const params = opts || {};
    const data = (params.data && typeof params.data === 'object') ? params.data : {};
    // For update + delete: resolve id from data.id, else data.name
    // → look up against this.schedules. Empty data → toast.
    const resolveId = () => {
      if (data.id != null) {
        return Number(data.id);
      }
      const nm = (data.name || '').toString().trim();
      if (!nm) {
        return null;
      }
      const list = Array.isArray(this.schedules) ? this.schedules : [];
      const match = list.find(s => s && s.name === nm);
      return match ? Number(match.id) : null;
    };
    try {
      if (op === 'create') {
        if (!data.name || !data.kind || !data.interval_seconds) {
          this.showToast(this.t('toasts_extra.schedule_missing_fields') || 'Schedule needs name, kind, and interval_seconds.', 'error');
          return;
        }
        const r = await fetch('/api/schedules', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(data),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(this.fmtApiError(j, r.status));
        }
        this.showToast(this.t('toasts_extra.schedule_created', {name: data.name}), 'success');
      } else if (op === 'update') {
        const id = resolveId();
        if (id == null) {
          this.showToast(this.t('toasts_extra.schedule_no_target') || 'Couldn\'t resolve schedule by id or name.', 'error');
          return;
        }
        // Strip id/name from the patch body — those are identity, not fields.
        const patch = Object.assign({}, data);
        delete patch.id;
        // name CAN be in the patch (rename) — only strip when it was used as the lookup key.
        if (data.id == null && patch.name === data.name) {
          delete patch.name;
        }
        const r = await fetch('/api/schedules/' + encodeURIComponent(id), {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(patch),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(this.fmtApiError(j, r.status));
        }
        this.showToast(this.t('toasts_extra.schedule_updated', {name: data.name || ('id ' + id)}), 'success');
      } else if (op === 'delete') {
        const id = resolveId();
        if (id == null) {
          this.showToast(this.t('toasts_extra.schedule_no_target') || 'Couldn\'t resolve schedule by id or name.', 'error');
          return;
        }
        const r = await fetch('/api/schedules/' + encodeURIComponent(id), {
          method: 'DELETE',
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(this.fmtApiError(j, r.status));
        }
        this.showToast(this.t('toasts_extra.schedule_deleted', {name: data.name || ('id ' + id)}), 'success');
      }
      // Refresh the schedules list so the Admin → Schedules table
      // reflects the change immediately if the operator navigates.
      if (typeof this.loadSchedules === 'function') {
        await this.loadSchedules();
      }
    } catch (e) {
      this.showToast(this.t('toasts_extra.schedule_action_failed', {error: (e && e.message) || ''}), 'error');
    }
  },

  // AI-palette dispatch wrapper for the send_notification flow.
  // Consumes `opts.data = {medium, body, title?}` (from the AI's
  // ACTION_DATA directive parsed upstream). Validates the medium is
  // one of the known set BEFORE the POST so the operator gets a
  // clear toast instead of a 400. The endpoint enforces the
  // operator-typed-text length cap + per-medium master-switch gate
  // server-side so this dispatcher stays thin.
  // AI palette / sidebar dispatch for run_app_skill — invoke a per-app SKILL
  // on a specific pinned chip (e.g. Speedtest's run_speedtest). Reads
  // ACTION_DATA {host_id, service_idx, skill_id} + reuses runAppSkill (POSTs
  // the skill endpoint, toasts, refreshes the per-app data). The backend
  // re-enforces the api_key + skill-declared gate, so a bad/unavailable skill
  // surfaces as a toast rather than doing anything unsafe.
  async _aiRunAppSkillDispatch(opts) {
    const data = (opts && opts.data && typeof opts.data === 'object') ? opts.data : {};
    const host = (data.host_id || '').toString().trim();
    const skillId = (data.skill_id || '').toString().trim();
    // Optional free-form skill argument (e.g. Seerr request-a-movie title).
    const arg = (data.arg == null) ? '' : String(data.arg).trim();
    let idx = data.service_idx;
    idx = (typeof idx === 'number') ? idx : parseInt(idx, 10);
    if (!host || !skillId || isNaN(idx) || idx < 0) {
      this.showToast((this.t('apps.skills.failed') || 'Skill failed')
        + ': ACTION_DATA needs host_id + service_idx + skill_id', 'error');
      return null;
    }
    if (typeof this.runAppSkill !== 'function') {
      return null;
    }
    // From the sidebar, run silently (the caller stamps the result inline in
    // the chat via skill_panel) and forward the confirm flag the destructive
    // gate set upstream. From the modal palette, keep the toast + no confirm
    // (non-destructive only there). Return the result so the caller can stamp
    // skill_panel.
    const fromSidebar = !!(opts && opts.surface === 'sidebar');
    return await this.runAppSkill({host_id: host, service_idx: idx}, skillId, arg,
      {silent: fromSidebar, confirm: !!(opts && opts.confirm)});
  },

  async _aiSendNotificationDispatch(opts) {
    const params = opts || {};
    const data = (params.data && typeof params.data === 'object') ? params.data : {};
    const medium = (data.medium || '').toString().trim().toLowerCase();
    const body = (data.body || '').toString().trim();
    const title = (data.title || '').toString().trim();
    // Consume the canonical set surfaced on `/api/me.notify_mediums`
    // (sourced from `logic.ops.NOTIFY_MEDIUMS` server-side). Pre-fix
    // this dispatcher hardcoded `['app', 'apprise', 'telegram']` as
    // a parallel literal that would silently drift when a future
    // medium lands in `logic/ops.py:NOTIFY_MEDIUMS`. Defensive
    // fallback to the historical list when `notifyMediumNames()`
    // isn't available (e.g. /api/me hasn't hydrated yet).
    const KNOWN_MEDIUMS = (typeof this.notifyMediumNames === 'function'
      && this.notifyMediumNames().length)
      ? this.notifyMediumNames()
      : ['app', 'apprise', 'telegram'];
    if (!medium || KNOWN_MEDIUMS.indexOf(medium) < 0) {
      this.showToast(
        this.t('toasts_extra.send_notification_bad_medium')
        || ('Pass ACTION_DATA: {"medium":"telegram|apprise|app","body":"[text]"} — got: ' + (medium || '[empty]')),
        'error',
      );
      return;
    }
    if (!body) {
      this.showToast(
        this.t('toasts_extra.send_notification_no_body')
        || 'Pass ACTION_DATA: {"medium":"[x]","body":"[text]"} — body is required.',
        'error',
      );
      return;
    }
    try {
      const r = await fetch('/api/notify/send', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({medium, body, title: title || undefined}),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        throw new Error(this.fmtApiError(j, r.status));
      }
      if (j.ok) {
        this.showToast(
          this.t('toasts_extra.send_notification_ok', {medium})
          || ('Sent to ' + medium + '.'),
          'success',
        );
      } else {
        this.showToast(
          this.t('toasts_extra.send_notification_failed', {error: j.detail || ''})
          || ('Send failed: ' + (j.detail || 'unknown error')),
          'error',
        );
      }
    } catch (e) {
      this.showToast(
        this.t('toasts_extra.send_notification_failed', {error: (e && e.message) || ''})
        || ('Send failed: ' + ((e && e.message) || 'unknown error')),
        'error',
      );
    }
  },

  // AI-palette dispatch wrapper for the retag flow. Consumes the
  // inline-confirm chip's `opts` envelope: `tag` from the AI's
  // ACTION_TAG directive, `item` from ACTION_ITEM (resolved
  // upstream by the dispatcher) OR the open item drawer when
  // ACTION_ITEM is missing. Falls through to a toast asking the
  // operator to specify when neither resolves. Pre-fills the
  // popover's draft + busy state so `submitRetagPopover` can
  // run untouched (single source of truth — AI dispatch and
  // operator-typed inline use the same backend code path).
  async _aiRetagDispatch(opts) {
    const params = opts || {};
    // Resolve target item: explicit `item` param > AI's ACTION_ITEM
    // (resolved against `this.items` by raw_id / id / case-insensitive
    // name match) > open item drawer.
    let item = params.item || null;
    const tokenRaw = (params.actionItem || '').toString().trim();
    if (!item && tokenRaw) {
      const tok = tokenRaw.toLowerCase();
      const items = Array.isArray(this.items) ? this.items : [];
      item = items.find(i => i && (i.raw_id === tokenRaw || i.id === tokenRaw))
        || items.find(i => i && (i.name || '').toLowerCase() === tok)
        || null;
    }
    if (!item) {
      item = this.drawerItem || null;
    }
    if (!item) {
      this.showToast(this.t('toasts.retag_no_target') || 'Open the item drawer first OR name the container/stack in your query.', 'warning');
      return;
    }
    if (!this.canRetagToLatest(item)) {
      this.showToast(this.t('toasts.retag_ineligible', {name: item.name}) || `Can't retag ${item.name} — not a container/stack-managed item.`, 'warning');
      return;
    }
    // Pre-fill the popover's draft so submitRetagPopover sees the
    // operator's chosen tag (or empty → backend defaults to :latest).
    this._retagDraft = (params.tag || '').toString().trim();
    this._retagPopoverItemId = item.raw_id || item.id;
    this._retagBusy = false;
    // Fire — submitRetagPopover handles validation, busy state,
    // backend dispatch, toast, and popover-close on success.
    try {
      await this.submitRetagPopover(item);
    } catch {
      // submitRetagPopover already toasts on its own failure path;
      // this catch is defence-in-depth so a thrown error doesn't
      // leave the popover state stuck open.
      this.closeRetagPopover();
    }
  },
};
