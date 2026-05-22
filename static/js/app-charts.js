// SPA chart renderers + chart-data helpers.
//
// Pure SVG-renderer functions invoked from the markup via x-html
// bindings. Each helper takes a sample series (or per-host data) and
// returns an SVG string ready for injection.
//
// Includes the per-host inline sparkline (`hostInlineSparkline*`),
// per-host detail charts (`hostChart`, `hostTempChart`), the
// `metricSource` info-bubble helper that explains which provider
// supplied a metric, and the axis helpers (`xAxisFromSeries`,
// `yAxisAuto`, `yAxisPercent`).
//
// Phase 2, Batch 20 of the static/js/app.js modularisation.

export default {
    // Stacked-area chart for fleet network throughput. Two series:
    // rx (incoming, primary colour) and tx (outgoing, success colour),
    // stacked so the top edge is the sum. Same gridline + axis style
    // as the 90d growth chart so the Stats family reads as a coherent
    // visual treatment.
    _renderFleetNetChart(points, hours) {
      if (!Array.isArray(points) || points.length < 2) {
        return '';
      }
      // 90d range (hours >= 2160) — rotate x-axis labels -45° + reserve
      // wider bottom padding so the dense date labels don't overlap.
      // Same treatment as the Samples + AI Cost bar charts.
      const W = 720, H = 220;
      const rotateXLabels = (Number(hours) || 0) >= 2160;
      const PAD_L = 80, PAD_R = 16, PAD_T = 12, PAD_B = rotateXLabels ? 60 : 24;
      const plotW = W - PAD_L - PAD_R;
      const plotH = H - PAD_T - PAD_B;
      const tsMin = points[0].bucket_ts;
      const tsMax = points[points.length - 1].bucket_ts;
      const tsRange = Math.max(1, tsMax - tsMin);
      let yMax = 0;
      for (const p of points) {
        const sum = (Number(p.rx_bps) || 0) + (Number(p.tx_bps) || 0);
        if (sum > yMax) {
          yMax = sum;
        }
      }
      const padded = yMax * 1.1;
      if (padded > 0) {
        const exp = Math.floor(Math.log10(padded));
        const base = Math.pow(10, exp);
        const m = padded / base;
        let snap;
        if (m <= 1) {
          snap = 1;
        }
        else {
          if (m <= 2) {
            snap = 2;
          } else {
            if (m <= 5) {
              snap = 5;
            } else {
              snap = 10;
            }
          }
        }
        yMax = snap * base;
      } else {
        yMax = 1;
      }
      const X = (ts) => PAD_L + ((ts - tsMin) / tsRange) * plotW;
      const Y = (v) => PAD_T + (1 - (v / Math.max(1, yMax))) * plotH;
      // Build the two stacked areas. rx is the BOTTOM band (0 →
      // rx); tx is the TOP band (rx → rx + tx). Closed polygons so
      // fill works correctly.
      let rxArea = '';
      let txArea = '';
      let rxLine = '';
      for (const p of points) {
        const rx = Number(p.rx_bps) || 0;
        const x = X(p.bucket_ts);
        rxLine += (rxLine ? ' L' : 'M') + x.toFixed(1) + ',' + Y(rx).toFixed(1);
      }
      // Close the rx area down to the baseline.
      rxArea = rxLine + ' L' + X(points[points.length - 1].bucket_ts).toFixed(1) + ',' + Y(0).toFixed(1)
        + ' L' + X(points[0].bucket_ts).toFixed(1) + ',' + Y(0).toFixed(1) + ' Z';
      // tx area sits on top of rx. Top edge: rx + tx. Bottom edge:
      // rx (reversed so the polygon closes correctly).
      let txTop = '';
      for (const p of points) {
        const rx = Number(p.rx_bps) || 0;
        const tx = Number(p.tx_bps) || 0;
        const x = X(p.bucket_ts);
        txTop += (txTop ? ' L' : 'M') + x.toFixed(1) + ',' + Y(rx + tx).toFixed(1);
      }
      let txBot = '';
      for (let i = points.length - 1; i >= 0; i--) {
        const p = points[i];
        const rx = Number(p.rx_bps) || 0;
        txBot += ' L' + X(p.bucket_ts).toFixed(1) + ',' + Y(rx).toFixed(1);
      }
      txArea = txTop + txBot + ' Z';
      // Y-axis ticks (5).
      const fmt = (n) => this.fmtBps(n);
      const yTicks = [0, yMax * 0.25, yMax * 0.5, yMax * 0.75, yMax].map(v => ({
        v, y: Y(v).toFixed(1), label: fmt(v),
      }));
      // X-axis ticks — 5 evenly-spaced. Use the user's date-only
      // format for the labels.
      const xTicks = [0, 1, 2, 3, 4].map(i => {
        const idx = Math.min(points.length - 1, Math.round((i / 4) * (points.length - 1)));
        const p = points[idx];
        const dt = new Date(p.bucket_ts * 1000);
        return { x: X(p.bucket_ts).toFixed(1), label: this._applyDateTimeFormat(dt, this._userDateOnlyFormat()) };
      });
      const esc = (s) => this._logEscape(String(s));
      let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" preserveAspectRatio="none" style="display:block">';
      for (const t of yTicks) {
        svg += '<line x1="' + PAD_L + '" y1="' + t.y + '" x2="' + (W - PAD_R) + '" y2="' + t.y
          + '" stroke="var(--border)" stroke-width="0.5" stroke-dasharray="2,2"/>';
        svg += '<text x="' + (PAD_L - 6) + '" y="' + (Number(t.y) + 4) + '" text-anchor="end" fill="var(--text-faint)" font-size="10">' + esc(t.label) + '</text>';
      }
      for (const t of xTicks) {
        svg += '<line x1="' + t.x + '" y1="' + PAD_T + '" x2="' + t.x + '" y2="' + (H - PAD_B)
          + '" stroke="var(--border)" stroke-width="0.5" stroke-dasharray="2,2"/>';
      }
      // RX area (bottom, primary tint).
      svg += '<path d="' + rxArea + '" fill="var(--primary)" fill-opacity="0.35" stroke="var(--primary)" stroke-width="1"/>';
      // TX area (top, success tint).
      svg += '<path d="' + txArea + '" fill="var(--success)" fill-opacity="0.35" stroke="var(--success)" stroke-width="1"/>';
      for (const t of xTicks) {
        if (rotateXLabels) {
          const ly = (H - PAD_B + 16).toFixed(1);
          svg += '<text x="' + t.x + '" y="' + ly + '" text-anchor="end" fill="var(--text-faint)" font-size="10"'
            + ' transform="rotate(-45 ' + t.x + ' ' + ly + ')">' + esc(t.label) + '</text>';
        } else {
          svg += '<text x="' + t.x + '" y="' + (H - 6) + '" text-anchor="middle" fill="var(--text-faint)" font-size="10">' + esc(t.label) + '</text>';
        }
      }
      svg += '</svg>';
      return svg;
    },
    // Inline trend sparkline overlaid on each Hosts-row stat-bar.
    // Pulls from whichever history source the host has — Beszel / NE
    // (`hostHistory[key].series` with cpu / mp / dp percent fields) OR
    // SNMP (`hostSnmpHistory[host_id].points` with cpu_used_pct + raw
    // mem_used / mem_total + raw net counters; we derive percentages
    // here so the path stays in 0..100% Y space). Returns an empty
    // string when no source has at least 2 points — caller's `x-show`
    // gate hides the SVG cleanly.
    //
  // The output path uses a fixed 100×16 viewBox and `preserveAspectRatio="none"`
    // so it stretches to fill whatever width its parent element has —
    // overlaying the .stat-bar (~70-120px) without per-host width math.
    hostInlineSparkline(h, metric) {
      if (!h) {
        return '';
      }
      const W = 100, H = 16;
      const PAD_T = 1, PAD_B = 1;
      const usableH = H - PAD_T - PAD_B;

      // Try Beszel / NE history first (richest dataset).
      const FIELD_BNE = { cpu: 'cpu', memory: 'mp', disk: 'dp' };
      const beszelKey = this.hostHistoryKey ? this.hostHistoryKey(h) : (h.beszel_id || h.id || '');
      let series = null;
      let pickValue = null;
      if (beszelKey) {
        const e = this.hostHistory && this.hostHistory[beszelKey];
        const f = FIELD_BNE[metric];
        if (e && Array.isArray(e.series) && e.series.length >= 2 && f) {
          series = e.series;
          pickValue = (r) => Number(r[f]);
        }
      }

      // Fallback to SNMP history — the SNMP sampler writes a separate
      // `host_snmp_samples` table consumed by `hostSnmpHistory[host.id]`.
      // Field shape differs from Beszel/NE: cpu_used_pct already a
      // percent; memory comes as raw mem_used / mem_total; disk isn't
      // recorded in the SNMP series so disk sparklines for SNMP-only
      // hosts will be empty (correct — the data isn't there).
      if (!series) {
        const snmpEntry = this.hostSnmpHistory && this.hostSnmpHistory[h.id];
        const points = snmpEntry && Array.isArray(snmpEntry.points) ? snmpEntry.points : null;
        if (points && points.length >= 2) {
          if (metric === 'cpu') {
            series = points;
            pickValue = (p) => Number(p.cpu_used_pct);
          } else if (metric === 'memory') {
            series = points;
            pickValue = (p) => {
              const tot = Number(p.mem_total) || 0;
              const used = Number(p.mem_used) || 0;
              return tot > 0 ? (used / tot) * 100 : NaN;
            };
          } else if (metric === 'disk') {
            // Disk added to host_snmp_samples — sampler writes
            // disk_total / disk_used (bytes); we derive percent the
            // same way the memory branch does. NULL or zero total
            // → NaN so the path-builder treats it as a gap rather
            // than a flat-zero hairline.
            series = points;
            pickValue = (p) => {
              const tot = Number(p.disk_total) || 0;
              const used = Number(p.disk_used) || 0;
              return tot > 0 ? (used / tot) * 100 : NaN;
            };
          }
        }
      }

      if (!series || !pickValue) {
        return '';
      }
      const n = series.length;
      const out = [];
      let lastNull = true;
      let sawNonZero = false;
      for (let i = 0; i < n; i++) {
        const v = pickValue(series[i]);
        if (!Number.isFinite(v)) { lastNull = true; continue; }
        if (v > 0) {
          sawNonZero = true;
        }
        const clamped = Math.max(0, Math.min(100, v));
        const x = (i / (n - 1)) * W;
        const y = PAD_T + usableH - (clamped / 100) * usableH;
        out.push(`${lastNull ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`);
        lastNull = false;
      }
      // Skip rendering a flat-zero line — it draws as an invisible
      // hairline at the bottom edge of the .spark element and looks
      // like "no sparkline at all" to the operator. Common cause:
      // Beszel agents that populate `info.dp` (live disk %) but
      // don't emit `stats.dp` in their history blob, so the live
      // bar reads 53% while the historical series is flat-0%. By
      // returning `''` here we let the gate (`hostHasInlineSpark`)
      // hide the SVG cleanly so the operator sees "no spark"
      // unambiguously instead of a misleading hairline. Same rule
      // for any all-zero series across providers.
      if (!sawNonZero) {
        return '';
      }
      return out.join(' ');
    },
    // Area-fill companion for `hostInlineSparkline`. Returns the same
    // line path closed to the baseline so the SVG can render a soft
    // tinted area UNDER the trend line — visually weighty enough to
    // read at a glance even when the stroke is thin. Closes per gap-
    // free run so a NaN-interrupted series doesn't smear the fill
    // across the gap. Same viewBox geometry as `hostInlineSparkline`
    // (W=100, H=16, top/bottom padding=1 each).
    hostInlineSparklineArea(h, metric) {
      if (!h) {
        return '';
      }
      const W = 100, H = 16;
      const PAD_T = 1, PAD_B = 1;
      const usableH = H - PAD_T - PAD_B;
      const baseY = H - PAD_B; // 15
      const FIELD_BNE = { cpu: 'cpu', memory: 'mp', disk: 'dp' };
      const beszelKey = this.hostHistoryKey ? this.hostHistoryKey(h) : (h.beszel_id || h.id || '');
      let series = null;
      let pickValue = null;
      if (beszelKey) {
        const e = this.hostHistory && this.hostHistory[beszelKey];
        const f = FIELD_BNE[metric];
        if (e && Array.isArray(e.series) && e.series.length >= 2 && f) {
          series = e.series;
          pickValue = (r) => Number(r[f]);
        }
      }
      if (!series) {
        const snmpEntry = this.hostSnmpHistory && this.hostSnmpHistory[h.id];
        const points = snmpEntry && Array.isArray(snmpEntry.points) ? snmpEntry.points : null;
        if (points && points.length >= 2) {
          if (metric === 'cpu') {
            series = points; pickValue = (p) => Number(p.cpu_used_pct);
          } else if (metric === 'memory') {
            series = points;
            pickValue = (p) => {
              const tot = Number(p.mem_total) || 0;
              const used = Number(p.mem_used) || 0;
              return tot > 0 ? (used / tot) * 100 : NaN;
            };
          } else if (metric === 'disk') {
            series = points;
            pickValue = (p) => {
              const tot = Number(p.disk_total) || 0;
              const used = Number(p.disk_used) || 0;
              return tot > 0 ? (used / tot) * 100 : NaN;
            };
          }
        }
      }
      if (!series || !pickValue) {
        return '';
      }
      const n = series.length;
      // Build run-segments: each gap-free run becomes its own closed
      // sub-path. Empty segments are skipped — gappy series end up as
      // disconnected filled regions rather than one smeared polygon.
      const subpaths = [];
      let current = null; // { firstX, points: [[x,y], ...] }
      let sawNonZero = false;
      const flush = () => {
        if (!current || current.points.length < 2) { current = null; return; }
        const first = current.points[0];
        const last = current.points[current.points.length - 1];
        const parts = [];
        parts.push(`M${first[0].toFixed(1)},${baseY.toFixed(1)}`);
        for (const [x, y] of current.points) {
          parts.push(`L${x.toFixed(1)},${y.toFixed(1)}`);
        }
        parts.push(`L${last[0].toFixed(1)},${baseY.toFixed(1)}`);
        parts.push('Z');
        subpaths.push(parts.join(' '));
        current = null;
      };
      for (let i = 0; i < n; i++) {
        const v = pickValue(series[i]);
        if (!Number.isFinite(v)) { flush(); continue; }
        if (v > 0) {
          sawNonZero = true;
        }
        const clamped = Math.max(0, Math.min(100, v));
        const x = (i / (n - 1)) * W;
        const y = PAD_T + usableH - (clamped / 100) * usableH;
        if (!current) {
          current = {firstX: x, points: []};
        }
        current.points.push([x, y]);
      }
      flush();
      if (!sawNonZero) {
        return '';
      }
      return subpaths.join(' ');
    },
    // Memory / CPU history renderer — produces the same shell shape
    // as `_renderDiskProjectionInner` (header + body) so the outer
    // chart card is visually identical regardless of which kind the
    // AI requested. Series points come from `/api/hosts/history`'s
    // `data.points[]` with `cp` (cpu %) / `mp` (memory %) numeric
    // fields. Renders a 320x80 SVG path scaled to [0..100] on the
    // y-axis with 1h ticks on x. Empty / null data → "no history
    // for the past 24h" message.
    _renderHostHistoryInner(hostId, kind, data, errorMsg) {
      const t = (k, fb) => this.t(k) || fb;
      const esc = (s) => this._logEscape(s);
      const hidEsc = esc(hostId);
      const isMemory = kind === 'memory_history';
      const titleKey = isMemory ? 'command_palette.ai.history_chart.title_memory'
                                : 'command_palette.ai.history_chart.title_cpu';
      const titleStr = t(titleKey, isMemory ? 'Memory · 24h' : 'CPU · 24h');
      if (errorMsg) {
        return ('<div class="ai-resp-chart-header">'
          + '<span class="ai-resp-chart-title">' + hidEsc + ' — ' + esc(titleStr) + '</span>'
          + '<span class="ai-resp-chart-status ai-resp-chart-status--error">'
          + esc((t('command_palette.ai.history_chart.error', 'Could not load history')) + ': ' + errorMsg)
          + '</span></div>'
          + '<div class="ai-resp-chart-body"></div>');
      }
      // Backend returns `{series: [...], error: ...}` (the same shape
      // both Beszel's `fetch_system_history` and the NE sampler's
      // `history_series` produce). Each row's keys: `t` (epoch
      // seconds), `cpu` (0..100), `mp` (memory percent 0..100).
      // Pre-fix the renderer read `data.points[].ts` + `cp`, which
      // matched neither shape — every response decoded to an empty
      // series and the chart rendered "No history points in the past
      // 24 h" regardless of whether the API returned data. Fixed
      // alongside the system_id fix in `_populateAiSidebarHostChart`.
      const rawSeries = (data && Array.isArray(data.series)) ? data.series : [];
      const fieldKey = isMemory ? 'mp' : 'cpu';
      const series = rawSeries
        .map(p => ({ ts: Number(p && p.t) || 0,
                     v: Number(p && p[fieldKey]) }))
        .filter(p => Number.isFinite(p.v) && p.ts > 0);
      if (series.length < 2) {
        return ('<div class="ai-resp-chart-header">'
          + '<span class="ai-resp-chart-title">' + hidEsc + ' — ' + esc(titleStr) + '</span>'
          + '<span class="ai-resp-chart-status">'
          + esc(t('command_palette.ai.history_chart.empty', 'No history points in the past 24 h'))
          + '</span></div>'
          + '<div class="ai-resp-chart-body"></div>');
      }
      // Build a 360x140 SVG line chart with explicit axes. Y axis
      // labels on the left (0/50/100 with horizontal gridlines); X
      // axis labels on the bottom (start/mid/end relative to now,
      // formatted as "-Nh" so the user reads "-24h … now" left→right).
      // Pre-fix the chart had no axes — line drew correctly but
      // operators couldn't tell what value the line was at OR how
      // much of the past 24h it covered. Removed `preserveAspectRatio
      // ="none"` so axis text doesn't distort when the SVG resizes
      // to its container width.
      const W = 360, H = 140;
      const PAD_L = 30;   // left padding for Y-axis labels (3 chars + gap)
      const PAD_R = 6;
      const PAD_T = 6;
      const PAD_B = 22;   // bottom padding for X-axis labels (one line + gap)
      const plotW = W - PAD_L - PAD_R;
      const plotH = H - PAD_T - PAD_B;
      const ts0 = series[0].ts, ts1 = series[series.length - 1].ts;
      const tspan = Math.max(1, ts1 - ts0);
      const xOf = (ts) => PAD_L + ((ts - ts0) / tspan) * plotW;
      const yOf = (v)  => PAD_T + (1 - Math.max(0, Math.min(100, v)) / 100) * plotH;
      let path = '';
      for (let i = 0; i < series.length; i++) {
        const p = series[i];
        path += (i === 0 ? 'M' : ' L') + xOf(p.ts).toFixed(1) + ' ' + yOf(p.v).toFixed(1);
      }
      // Latest value chip — operator's "what's it at right now" eye.
      const latest = series[series.length - 1].v;
      const latestStr = (Math.round(latest * 10) / 10).toFixed(1) + '%';
      // 24h max + min for the operator's "spike vs flat" eye.
      let vMax = -Infinity, vMin = Infinity;
      for (const p of series) {
        if (p.v > vMax) {
          vMax = p.v;
        }
        if (p.v < vMin) {
          vMin = p.v;
        }
      }
      const rangeStr = (Math.round(vMin * 10) / 10).toFixed(1) + '%–'
                     + (Math.round(vMax * 10) / 10).toFixed(1) + '%';
      // Y-axis: gridlines + labels at 0 / 50 / 100. User-flagged
      // wanting visible-but-subtle dotted gridlines. First pass at
      // opacity 0.4 + `1,3` dasharray was too faint to see on dark
      // surfaces; bumped to opacity 0.85 + `2,3` dasharray (2px dash,
      // 3px gap — still reads as dotted but the dashes carry actual
      // pixel weight) and switched the colour to `var(--text-faint)`
      // which has more contrast against the chart-card background
      // than `var(--border)`.
      const yTicks = [0, 50, 100];
      let yAxis = '';
      for (const v of yTicks) {
        const y = yOf(v).toFixed(1);
        yAxis += '<line x1="' + PAD_L + '" x2="' + (W - PAD_R)
              +  '" y1="' + y + '" y2="' + y
              +  '" stroke="var(--text-faint)" stroke-width="0.6" stroke-dasharray="2,3" opacity="0.85"/>';
        // label aligned right of the gridline's left edge
        yAxis += '<text x="' + (PAD_L - 4) + '" y="' + y
              +  '" fill="var(--text-faint)" font-size="9" '
              +  'text-anchor="end" dominant-baseline="middle" '
              +  'font-family="var(--font-mono, monospace)">' + v + '</text>';
      }
      // X-axis baseline + 3 time ticks (start / mid / end), labelled
      // relative to "now" so the user reads "-24h" / "-12h" / "now"
      // left to right. Uses ts1 (last sample) as "now" so even if the
      // sampler is a few minutes behind real wall-clock, the axis
      // matches what the line actually plots.
      const baseY = (PAD_T + plotH).toFixed(1);
      let xAxis = '<line x1="' + PAD_L + '" x2="' + (W - PAD_R)
                + '" y1="' + baseY + '" y2="' + baseY
                + '" stroke="var(--border)" stroke-width="0.5"/>';
      // 7 evenly-spaced ticks across the window. Pre-fix the chart
      // had only 3 (start / mid / end) which on a 24h memory chart
      // read as just `-24h / -12h / now` — operators scanning the
      // line couldn't pin a peak / trough to a specific time without
      // mental arithmetic. 7 ticks at 4-hour resolution on a 24h
      // window (`-24h / -20h / -16h / -12h / -8h / -4h / now`) keeps
      // each tick label readable at font-size=9 (~30-40px wide) with
      // ~54px between ticks on the 324px-wide plot area. The end-tick
      // anchor is `end` so the `now` label hugs the right edge; start
      // is `start` so `-24h` doesn't overhang the left padding;
      // middle ticks center on their gridline.
      const X_TICKS = 7;
      const xTicks = [];
      for (let i = 0; i < X_TICKS; i++) {
        const frac = i / (X_TICKS - 1);
        xTicks.push({ frac, ts: ts0 + tspan * frac });
      }
      const fmtRel = (ts) => {
        const ago = Math.max(0, ts1 - ts);
        if (ago < 60) {
          return 'now';
        }
        const hours = ago / 3600;
        if (hours < 1) {
          return '-' + Math.round(ago / 60) + 'm';
        }
        return '-' + (Math.round(hours * 10) / 10).toFixed(hours < 10 ? 1 : 0) + 'h';
      };
      for (const tick of xTicks) {
        const x = xOf(tick.ts).toFixed(1);
        // Vertical gridline from top of plot area down to the baseline
        // — same dotted / faint shape as the Y-axis gridlines so the
        // chart reads as a uniform grid. Skip the leftmost (frac=0)
        // because it sits AT the Y-axis label column and would visually
        // clash with the label itself.
        if (tick.frac > 0) {
          xAxis += '<line x1="' + x + '" x2="' + x
                +  '" y1="' + PAD_T.toFixed(1) + '" y2="' + baseY
                +  '" stroke="var(--text-faint)" stroke-width="0.6" stroke-dasharray="2,3" opacity="0.85"/>';
        }
        // Tick mark below the baseline.
        xAxis += '<line x1="' + x + '" x2="' + x
              +  '" y1="' + baseY + '" y2="' + (parseFloat(baseY) + 3).toFixed(1)
              +  '" stroke="var(--border)" stroke-width="0.5"/>';
        const anchor = tick.frac === 0 ? 'start' : (tick.frac === 1 ? 'end' : 'middle');
        xAxis += '<text x="' + x + '" y="' + (parseFloat(baseY) + 14).toFixed(1)
              +  '" fill="var(--text-faint)" font-size="9" '
              +  'text-anchor="' + anchor + '" '
              +  'font-family="var(--font-mono, monospace)">'
              +  esc(fmtRel(tick.ts)) + '</text>';
      }
      // Y-axis unit hint top-left (small "%").
      const yUnit = '<text x="' + (PAD_L - 4) + '" y="' + (PAD_T - 1).toFixed(1)
                  + '" fill="var(--text-faint)" font-size="8" '
                  + 'text-anchor="end" dominant-baseline="hanging" '
                  + 'font-family="var(--font-mono, monospace)">%</text>';
      return ('<div class="ai-resp-chart-header">'
        + '<span class="ai-resp-chart-title">' + hidEsc + ' — ' + esc(titleStr) + '</span>'
        + '<span class="ai-resp-chart-status mono fs-2xs">'
        + esc(latestStr + ' · ' + rangeStr)
        + '</span>'
        + '</div>'
        + '<div class="ai-resp-chart-body">'
        + '<svg viewBox="0 0 ' + W + ' ' + H + '" '
        + 'width="100%" height="' + H + '" aria-hidden="true">'
        + yAxis + xAxis + yUnit
        + '<path d="' + path + '" fill="none" stroke="var(--primary)" '
        + 'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        + '</svg>'
        + '</div>');
    },
    _renderSamplesBucketChart(points, range) {
      if (!Array.isArray(points) || points.length === 0) {
        return '';
      }
      // Operator-flagged: at 90d the ~14 weekly `dd/MM/yyyy` x-axis
      // labels overlap horizontally. Tilt labels -45° at 90d (and any
      // future long range) so dense date labels stay readable; reserve
      // ~60px of bottom padding to fit the rotated text without
      // clipping the SVG viewBox.
      const W = 720, H = 220;
      const rotateXLabels = ((range || '').toString() === '90d');
      const PAD_L = 56, PAD_R = 12, PAD_T = 12, PAD_B = rotateXLabels ? 60 : 28;
      const plotW = W - PAD_L - PAD_R;
      const plotH = H - PAD_T - PAD_B;
      const n = points.length;
      const barW = plotW / n;
      const yMaxRaw = Math.max(1, ...points.map(p => p.total || 0));
      // Round yMax up to a nice number (1, 2, 5 × 10^k) so y-tick
      // labels are clean integers.
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
      // Y-axis ticks — 5 evenly-spaced from 0 to yMax.
      const yTicks = [0, 1, 2, 3, 4].map(i => {
        const v = (yMax / 4) * i;
        return { v, y: Y(v).toFixed(1), label: Math.round(v).toLocaleString() };
      });
      const r = (range || '90d').toString();
      // X-axis tick density per range — operator-flagged that 90d
      // showed labels on only some bars (default tickCount=6 across
      // ~14 weekly buckets left 8 unlabeled). Now: sparse ranges
      // label EVERY bucket; 30d shows ~10 evenly-spaced; 24h shows
      // every 4th hour; 1h is single-bucket.
      let tickCount;
      if (r === '1h')        {
        tickCount = 1;
      }
      else {
        if (r === '24h') {
          tickCount = Math.min(n, 6);
        } else {
          if (r === '7d') {
            tickCount = n;
          }       // all 7 days
          else {
            if (r === '30d') {
              tickCount = Math.min(n, 10);
            } else {
              if (r === '90d') {
                tickCount = n;
              }       // all ~14 weeks
              else {
                tickCount = Math.min(6, n);
              }
            }
          }
        }
      }
      const xIdxs = [];
      if (tickCount === 1) {
        xIdxs.push(0);
      } else {
        for (let i = 0; i < tickCount; i++) {
          xIdxs.push(Math.round((i / (tickCount - 1)) * (n - 1)));
        }
      }
      const dedup = Array.from(new Set(xIdxs));
      // Format the x-tick label per range. Routes through the user's
      // Formats preference (Settings → Profile → Formats) so labels
      // honour the same `dd/MM/yyyy, HH:mm:ss` / `MM/dd/yyyy` / etc.
      // grammar that powers every other date render in the SPA. Per
      // CLAUDE.md "User-pref token grammar with derived variants —
      // single source of truth, strip-don't-store": hour buckets get a
      // time-only format derived by stripping date tokens from the
      // canonical pref; day + week buckets use `_userDateOnlyFormat()`.
      // Backend bucket keys are ISO-shaped UTC anchors:
      //   1h / 24h → `YYYY-MM-DDTHH:00` (hour bucket)
      //   7d / 30d → `YYYY-MM-DD`        (day bucket)
      //   90d      → `YYYY-MM-DD`        (start-of-week anchor)
      const parseBucketKey = (key) => {
        if (!key) {
          return null;
        }
        const t = key.indexOf('T');
        if (t >= 0) {
          const [y, mo, da] = key.slice(0, 10).split('-').map(s => parseInt(s, 10));
          const [hh, mm] = key.slice(t + 1).split(':').map(s => parseInt(s, 10));
          return new Date(Date.UTC(y, (mo || 1) - 1, da || 1, hh || 0, mm || 0));
        }
        const [y, mo, da] = key.split('-').map(s => parseInt(s, 10));
        return new Date(Date.UTC(y, (mo || 1) - 1, da || 1));
      };
      // Date + time format helpers — route through the shared
      // `_user{Date,Time}OnlyFormat` strippers (canonical "derive
      // date-only / time-only from the user's full pref" pattern).
      const dateOnlyFmt = this._userDateOnlyFormat();
      const timeOnlyFmt = this._userTimeOnlyFormat();
      const fmtXLabel = (key) => {
        const d = parseBucketKey(key);
        if (!d) {
          return '';
        }
        if (r === '1h' || r === '24h') {
          return this._applyDateTimeFormat(d, timeOnlyFmt);
        }
        return this._applyDateTimeFormat(d, dateOnlyFmt);
      };
      // Tooltip helper for week buckets — still shows the FULL week
      // range so operators can verify which 7 days a bar represents.
      // Both endpoints route through the user's date-only format.
      const fmtTooltipDate = (key) => {
        const d = parseBucketKey(key);
        if (!d) {
          return '';
        }
        if (r === '1h' || r === '24h') {
          return this.fmtDateTimeShort(Math.floor(d.getTime() / 1000));
        }
        if (r === '90d') {
          const end = new Date(d.getTime());
          end.setUTCDate(end.getUTCDate() + 6);
          return this._applyDateTimeFormat(d, dateOnlyFmt)
            + ' – '
            + this._applyDateTimeFormat(end, dateOnlyFmt);
        }
        return this._applyDateTimeFormat(d, dateOnlyFmt);
      };
      const esc = (s) => this._logEscape(String(s));
      let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" preserveAspectRatio="none" style="display:block">';
      // Horizontal gridlines + Y-axis labels.
      for (const t of yTicks) {
        svg += '<line x1="' + PAD_L + '" y1="' + t.y + '" x2="' + (W - PAD_R) + '" y2="' + t.y
          + '" stroke="var(--chart-grid)" stroke-width="0.5" stroke-dasharray="2,2"/>';
        svg += '<text x="' + (PAD_L - 6) + '" y="' + (Number(t.y) + 4) + '" text-anchor="end" fill="var(--text-faint)" font-size="10">'
          + esc(t.label) + '</text>';
      }
      // Bars — primary tint with darker hover-state via a per-bar
      // <title> for cheap tooltips.
      const totalBarW = Math.max(1, barW - 1);
      for (let i = 0; i < n; i++) {
        const p = points[i];
        const total = Number(p.total || 0);
        const bx = X(i).toFixed(2);
        const by = Y(total).toFixed(2);
        const bh = (PAD_T + plotH - Y(total)).toFixed(2);
        svg += '<rect x="' + bx + '" y="' + by + '" width="' + totalBarW.toFixed(2)
          + '" height="' + bh + '" fill="var(--primary)" fill-opacity="0.7">'
          + '<title>' + esc(fmtTooltipDate(p.date)) + ': ' + total.toLocaleString() + ' rows</title>'
          + '</rect>';
      }
      // X-axis tick labels — rotated -45° at 90d so the ~14 weekly
      // `dd/MM/yyyy` labels stop overlapping. With `text-anchor="end"`
      // + `rotate(-45 cx ly)` the label's RIGHT edge anchors at the
      // bar centre and the text extends up-left from there; the
      // operator's date pref still flows through `fmtXLabel`.
      for (const i of dedup) {
        const p = points[i];
        if (!p) {
          continue;
        }
        const cx = (X(i) + barW / 2).toFixed(1);
        if (rotateXLabels) {
          const ly = (H - PAD_B + 16).toFixed(1);
          svg += '<text x="' + cx + '" y="' + ly + '" text-anchor="end" fill="var(--text-faint)" font-size="10"'
            + ' transform="rotate(-45 ' + cx + ' ' + ly + ')">'
            + esc(fmtXLabel(p.date)) + '</text>';
        } else {
          svg += '<text x="' + cx + '" y="' + (H - 8) + '" text-anchor="middle" fill="var(--text-faint)" font-size="10">'
            + esc(fmtXLabel(p.date)) + '</text>';
        }
      }
      svg += '</svg>';
      return svg;
    },
    _renderDbProjectionChart(points) {
      if (!Array.isArray(points) || points.length < 2) {
        return '';
      }
      const W = 720, H = 220;
      const PAD_L = 64, PAD_R = 16, PAD_T = 12, PAD_B = 24;
      const plotW = W - PAD_L - PAD_R;
      const plotH = H - PAD_T - PAD_B;
      const tsMin = points[0].ts;
      const tsMax = points[points.length - 1].ts;
      const tsRange = Math.max(1, tsMax - tsMin);
      let yMax = 0;
      for (const p of points) {
        const hi = Number(p.high || p.bytes || 0);
        if (hi > yMax) {
          yMax = hi;
        }
      }
      // Pick a Y-axis with EVERY tick a multiple of 100 in whatever
      // unit (B / KB / MB / GB / TB) fits the range. Algorithm:
      // 1. Add 5% headroom so the data line doesn't kiss the top edge.
      // 2. Walk the unit ladder UP while padded/unitBytes ≥ 1024 (same
      //    rule as fmtBytes), then walk DOWN if the value-in-unit is
      //    < 100 so the multiple-of-100 snap is meaningful (a 5 GB
      //    value snaps in MB at 5400 MB instead of 100 GB).
      // 3. Pick a step from {100, 200, 500, 1000, 2000, 5000, 10000}
      //    such that ≤ 6 ticks cover the range. Ticks then land at
      //    0, step, 2×step, …, ceil(value/step)×step — every one a
      //    clean multiple of 100 in the chosen unit.
      let yTicksValues = [0];
      let yMaxCalc = 1;
      const padded = yMax * 1.05;
      if (padded > 0) {
        let unitIdx = 0;
        let inUnit = padded;
        while (inUnit >= 1024 && unitIdx < 4) { inUnit /= 1024; unitIdx++; }
        // If the value-in-unit is < 100, drop to a smaller unit so
        // the 100-snap doesn't overshoot wildly (5 GB → MB = 5120).
        while (inUnit < 100 && unitIdx > 0) {
          inUnit *= 1024;
          unitIdx -= 1;
        }
        const unitBytes = Math.pow(1024, unitIdx);
        const stepCandidates = [100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000];
        let step = stepCandidates[stepCandidates.length - 1];
        for (const s of stepCandidates) {
          if (Math.ceil(inUnit / s) <= 6) { step = s; break; }
        }
        const ticksCount = Math.max(1, Math.ceil(inUnit / step));
        yMaxCalc = ticksCount * step * unitBytes;
        yTicksValues = [];
        for (let i = 0; i <= ticksCount; i++) {
          yTicksValues.push(i * step * unitBytes);
        }
      }
      yMax = yMaxCalc;
      const X = (ts) => PAD_L + ((ts - tsMin) / tsRange) * plotW;
      const Y = (b) => PAD_T + (1 - (b / Math.max(1, yMax))) * plotH;
      // Confidence band — closed polygon (top edge low→high, bottom
      // edge high→low reversed).
      let bandTop = '';
      let bandBot = '';
      for (const p of points) {
        bandTop += (bandTop ? ' L' : 'M') + X(p.ts).toFixed(1) + ',' + Y(p.high).toFixed(1);
      }
      for (let i = points.length - 1; i >= 0; i--) {
        const p = points[i];
        bandBot += ' L' + X(p.ts).toFixed(1) + ',' + Y(p.low).toFixed(1);
      }
      const bandPath = bandTop + bandBot + ' Z';
      // Central line.
      let linePath = '';
      for (const p of points) {
        linePath += (linePath ? ' L' : 'M') + X(p.ts).toFixed(1) + ',' + Y(p.bytes).toFixed(1);
      }
      // Y-axis labels — every tick is a multiple-of-100 in the chosen
      // unit (computed above). `fmtBytesAt(v, yMax)` formats each tick
      // in the SAME unit family so labels read consistently across the
      // axis (no "1000 MB" next to "2 GB" mismatches).
      const fmtB = (n) => this.fmtBytesAt(n, yMax);
      const yTicks = yTicksValues.map(v => ({
        v, y: Y(v).toFixed(1), label: fmtB(v),
      }));
      // X-axis labels: Today / +30 / +60 / +90 — relative day offsets
      // read cleanly for a 90-day projection where the axis itself
      // tells you "this is days from now". Absolute dates would
      // require operators to do mental arithmetic to gauge the
      // projection horizon.
      const xTicks = [0, 30, 60, 90].map(d => {
        const idx = Math.min(points.length - 1, Math.round((d / 90) * (points.length - 1)));
        const p = points[idx];
        const label = d === 0
          ? (this.t('stats.database.projection.today') || 'Today')
          : '+' + d + 'd';
        return { x: X(p.ts).toFixed(1), label };
      });
      const esc = (s) => this._logEscape(String(s));
      let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" preserveAspectRatio="none" style="display:block">';
      // Horizontal gridlines — `var(--chart-grid)` (per-theme tone:
      // dark-theme uses a slightly-brighter-than-border slate; light-
      // theme uses slate-400). Operator-flagged 2026-05-11: the
      // previous `var(--border)` rendered the gridlines as nearly
      // invisible in light theme.
      for (const t of yTicks) {
        svg += '<line x1="' + PAD_L + '" y1="' + t.y + '" x2="' + (W - PAD_R) + '" y2="' + t.y
          + '" stroke="var(--chart-grid)" stroke-width="0.5" stroke-dasharray="2,2"/>';
        svg += '<text x="' + (PAD_L - 6) + '" y="' + (Number(t.y) + 4) + '" text-anchor="end" fill="var(--text-faint)" font-size="10">' + esc(t.label) + '</text>';
      }
      // Vertical gridlines — same `--chart-grid` per-theme tone.
      for (const t of xTicks) {
        svg += '<line x1="' + t.x + '" y1="' + PAD_T + '" x2="' + t.x + '" y2="' + (H - PAD_B)
          + '" stroke="var(--chart-grid)" stroke-width="0.5" stroke-dasharray="2,2"/>';
      }
      // Band.
      svg += '<path d="' + bandPath + '" fill="var(--primary)" fill-opacity="0.12" stroke="none"/>';
      // Central line (dashed for projection feel).
      svg += '<path d="' + linePath + '" fill="none" stroke="var(--primary)" stroke-width="1.5" stroke-dasharray="4,2"/>';
      // X-axis tick labels.
      for (const t of xTicks) {
        svg += '<text x="' + t.x + '" y="' + (H - 6) + '" text-anchor="middle" fill="var(--text-faint)" font-size="10">' + esc(t.label) + '</text>';
      }
      svg += '</svg>';
      return svg;
    },

    _renderDiskProjectionInner(hostId, data, errorMsg) {
      const t = (k, fb) => this.t(k) || fb;
      const esc = (s) => this._logEscape(s);
      const hidEsc = esc(hostId);
      // Error path — couldn't fetch / parse. Wrapped in
      // `.ai-resp-chart-body` so the card height matches the
      // success / loading shells; without the wrapper, the populator's
      // `innerHTML = ...` collapses the body element and the operator
      // sees a sudden 1-line height drop where the spinner used to be.
      if (errorMsg) {
        return ('<div class="ai-resp-chart-header">'
          + '<span class="ai-resp-chart-title">' + hidEsc + '</span>'
          + '<span class="ai-resp-chart-status ai-resp-chart-status--error">'
          + esc(t('command_palette.ai.disk_chart.error', 'Could not load projection') + ': ' + errorMsg)
          + '</span>'
          + '</div>'
          + '<div class="ai-resp-chart-body"></div>');
      }
      const samples = (data && Array.isArray(data.samples)) ? data.samples : [];
      const projection = (data && Array.isArray(data.projection)) ? data.projection : [];
      // Insufficient data — render a header + placeholder. Body
      // wrapper preserved for layout stability (same reason as the
      // error path above).
      if (samples.length < 2) {
        return ('<div class="ai-resp-chart-header">'
          + '<span class="ai-resp-chart-title">' + hidEsc + '</span>'
          + '<span class="ai-resp-chart-status ai-resp-chart-status--muted">'
          + esc(t('command_palette.ai.disk_chart.no_data', 'Not enough sampled history yet'))
          + '</span>'
          + '</div>'
          + '<div class="ai-resp-chart-body"></div>');
      }
      // Compose the SVG. Layout:
      //   total area: 640w × 100h. Chart drawable: 562w × 70h
      //   (left pad 30 for "%" labels, right pad 8, top pad 6, bottom pad 24)
      const W = 640, H = 100, PL = 30, PR = 8, PT = 6, PB = 24;
      const cw = W - PL - PR;
      const ch = H - PT - PB;
      const tFirst = samples[0].ts;
      // Project window — last projection point or last sample if no projection.
      const projLast = projection.length > 0
        ? projection[projection.length - 1].ts : samples[samples.length - 1].ts;
      const tLast = Math.max(projLast, samples[samples.length - 1].ts);
      const tNow = (data && data.current && data.current.ts) || samples[samples.length - 1].ts;
      const xOf = (ts) => PL + ((ts - tFirst) / Math.max(1, (tLast - tFirst))) * cw;
      const yOf = (pct) => PT + (1 - Math.max(0, Math.min(100, pct)) / 100) * ch;
      // Historical area path: closed polygon along the top + back along the baseline.
      let areaPath = 'M ' + xOf(samples[0].ts) + ' ' + yOf(samples[0].used_pct);
      for (let i = 1; i < samples.length; i++) {
        areaPath += ' L ' + xOf(samples[i].ts) + ' ' + yOf(samples[i].used_pct);
      }
      // Close to baseline (y=H-PB) — area fill underneath.
      areaPath += ' L ' + xOf(samples[samples.length - 1].ts) + ' ' + (H - PB);
      areaPath += ' L ' + xOf(samples[0].ts) + ' ' + (H - PB) + ' Z';
      // Historical stroke (top edge of the area, no closing).
      let histStroke = 'M ' + xOf(samples[0].ts) + ' ' + yOf(samples[0].used_pct);
      for (let i = 1; i < samples.length; i++) {
        histStroke += ' L ' + xOf(samples[i].ts) + ' ' + yOf(samples[i].used_pct);
      }
      // Projection stroke (dashed, from "now" forward) + confidence
      // band fork (shaded polygon between high_pct and low_pct for
      // each projected point). The band widens with extrapolation
      // distance — mirrors the classical OLS prediction-interval
      // cone so operators see uncertainty at a glance instead of
      // trusting a single deterministic line.
      let projStroke = '';
      let projBand   = '';
      let projHighEdge = '';
      let projLowEdge  = '';
      if (projection.length >= 2) {
        projStroke = 'M ' + xOf(projection[0].ts) + ' ' + yOf(projection[0].used_pct);
        for (let i = 1; i < projection.length; i++) {
          projStroke += ' L ' + xOf(projection[i].ts) + ' ' + yOf(projection[i].used_pct);
        }
        // Build the band + edge strokes only when the backend
        // supplied bounds (older API versions don't emit `low_pct`
        // / `high_pct`).
        const hasBand = projection.every(p =>
          p.low_pct !== undefined && p.high_pct !== undefined);
        if (hasBand) {
          // Top edge (high_pct) left → right, then bottom edge
          // (low_pct) right → left, closed for a filled polygon.
          let band = 'M ' + xOf(projection[0].ts) + ' ' + yOf(projection[0].high_pct);
          for (let i = 1; i < projection.length; i++) {
            band += ' L ' + xOf(projection[i].ts) + ' ' + yOf(projection[i].high_pct);
          }
          for (let i = projection.length - 1; i >= 0; i--) {
            band += ' L ' + xOf(projection[i].ts) + ' ' + yOf(projection[i].low_pct);
          }
          band += ' Z';
          projBand = band;
          // Separate dashed edge strokes for high + low so the
          // bounds are explicitly visible — even when the
          // confidence cone is shallow (flat slope, small residual
          // variance) the operator can still SEE the upper / lower
          // bound lines next to the central projection.
          let high = 'M ' + xOf(projection[0].ts) + ' ' + yOf(projection[0].high_pct);
          let low  = 'M ' + xOf(projection[0].ts) + ' ' + yOf(projection[0].low_pct);
          for (let i = 1; i < projection.length; i++) {
            high += ' L ' + xOf(projection[i].ts) + ' ' + yOf(projection[i].high_pct);
            low  += ' L ' + xOf(projection[i].ts) + ' ' + yOf(projection[i].low_pct);
          }
          projHighEdge = high;
          projLowEdge  = low;
        }
      }
      // Y-axis ticks at 0/50/100.
      const yTicks = [0, 50, 100].map(p => (
        '<line x1="' + PL + '" y1="' + yOf(p) + '" x2="' + (W - PR) + '" y2="' + yOf(p) + '" '
        + 'stroke="var(--border)" stroke-width="0.5" stroke-dasharray="2,2"/>'
        + '<text x="' + (PL - 4) + '" y="' + (yOf(p) + 3) + '" text-anchor="end" '
        + 'class="ai-resp-chart-axis">' + p + '%</text>'
      )).join('');
      // X-axis labels: first / now / last (formatted as MMM DD).
      // Locale-aware via Intl.DateTimeFormat — pre-fix the month names
      // were a hardcoded English array, so operators in non-en locales
      // saw English ticks regardless of their language setting.
      const fmtDate = (ts) => {
        const d = new Date(ts * 1000);
        try {
          return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
        } catch (_) {
          const m = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()];
          return m + ' ' + d.getDate();
        }
      };
      const xLabels = (
        '<text x="' + xOf(tFirst) + '" y="' + (H - 6) + '" text-anchor="start" class="ai-resp-chart-axis">'
        + esc(fmtDate(tFirst)) + '</text>'
        + '<text x="' + xOf(tNow) + '" y="' + (H - 6) + '" text-anchor="middle" class="ai-resp-chart-axis ai-resp-chart-now-label">'
        + esc(t('common.now', 'Now')) + '</text>'
        + '<text x="' + xOf(tLast) + '" y="' + (H - 6) + '" text-anchor="end" class="ai-resp-chart-axis">'
        + esc(fmtDate(tLast)) + '</text>'
      );
      // "Now" vertical divider.
      const nowLine = (
        '<line x1="' + xOf(tNow) + '" y1="' + PT + '" x2="' + xOf(tNow) + '" y2="' + (H - PB) + '" '
        + 'stroke="var(--text-faint)" stroke-width="1" stroke-dasharray="3,3"/>'
      );
      // i18n-aware accessible name + band tooltip text. Pre-fix the
      // SVG `aria-label` and band-edge `<title>` were bare English
      // literals — same drift class as the AI fenced-code copy button
      // caught in the 2026-05-08 review. Each falls back to its English
      // form when the key isn't translated yet.
      const ariaLabel = (t('ai_sidebar.disk_projection.aria_label', { host: hostId })
                        || (hostId + ' disk usage projection'));
      const bandHighTitle = (t('ai_sidebar.disk_projection.band_high_95')
                             || 'High (95% upper)');
      const bandLowTitle  = (t('ai_sidebar.disk_projection.band_low_95')
                             || 'Low (95% lower)');
      const svg = ('<svg viewBox="0 0 ' + W + ' ' + H + '" '
        + 'preserveAspectRatio="xMidYMid meet" class="ai-resp-chart-svg" '
        + 'role="img" aria-label="' + esc(ariaLabel) + '">'
        + yTicks
        + '<path d="' + areaPath + '" class="ai-resp-chart-area"/>'
        + (projBand ? '<path d="' + projBand + '" class="ai-resp-chart-band"/>' : '')
        + (projHighEdge ? '<path d="' + projHighEdge + '" class="ai-resp-chart-band-edge"><title>' + esc(bandHighTitle) + '</title></path>' : '')
        + (projLowEdge  ? '<path d="' + projLowEdge  + '" class="ai-resp-chart-band-edge"><title>' + esc(bandLowTitle) + '</title></path>' : '')
        + '<path d="' + histStroke + '" class="ai-resp-chart-line ai-resp-chart-line--hist" fill="none"/>'
        + (projStroke ? '<path d="' + projStroke + '" class="ai-resp-chart-line ai-resp-chart-line--proj" fill="none"/>' : '')
        + nowLine
        + xLabels
        + '</svg>');
      // Header bar: host id + summary chip + confidence pill.
      const cur = (data && data.current) || {};
      const currentPct = (cur.used_pct !== undefined && cur.used_pct !== null) ? Math.round(cur.used_pct) : null;
      const fmtBytes = (n) => {
        if (!Number.isFinite(+n) || +n <= 0) {
          return '';
        }
        const v = +n;
        if (v >= 1024**4) {
          return (v / 1024 ** 4).toFixed(1) + ' TB';
        }
        if (v >= 1024**3) {
          return (v / 1024 ** 3).toFixed(1) + ' GB';
        }
        if (v >= 1024**2) {
          return (v / 1024 ** 2).toFixed(1) + ' MB';
        }
        return v + ' B';
      };
      const slope = data && data.slope_pct_per_day;
      const exhaustionTs = data && data.exhaustion_ts;
      const confidence = (data && data.confidence) || 'low';
      const confLabel = t('command_palette.ai.disk_chart.confidence_' + confidence,
        confidence === 'high' ? 'High Confidence'
        : confidence === 'medium' ? 'Medium Confidence' : 'Low Confidence');
      const summaryParts = [];
      if (currentPct !== null) {
        // i18n'd summary fragments. Pre-fix the chart's tick labels +
        // aria-label + band tooltips were migrated to `t()` but this
        // summary row leaked English ('free of' / '% used' / '%/day').
        // Each key takes `{value}` placeholders so locales control the
        // unit ordering (e.g. "45% used" vs "45% utilizado").
        const _freeOfTmpl = t('command_palette.ai.disk_chart.summary_free_of_label',
          '{free} free of {total}');
        const _usedTmpl = t('command_palette.ai.disk_chart.summary_used_label',
          '{pct}% used');
        const freeStr = (cur.total_bytes && cur.used_bytes !== undefined)
          ? esc(_freeOfTmpl
              .replace('{free}', fmtBytes(cur.total_bytes - cur.used_bytes))
              .replace('{total}', fmtBytes(cur.total_bytes)))
          : '';
        const usedStr = _usedTmpl.replace('{pct}', String(currentPct));
        summaryParts.push('<strong>' + esc(usedStr) + '</strong>'
          + (freeStr ? ' · <span class="ai-resp-chart-sub">' + freeStr + '</span>' : ''));
      }
      let trendLabel = '';
      if (typeof slope === 'number') {
        const sign = slope > 0 ? '+' : '';
        const _trendTmpl = t('command_palette.ai.disk_chart.summary_per_day_label',
          '{value}%/day');
        trendLabel = _trendTmpl.replace('{value}', sign + slope.toFixed(2));
      }
      let exhaustionLabel = '';
      if (exhaustionTs) {
        const days = Math.max(0, Math.round((exhaustionTs - Math.floor(Date.now() / 1000)) / 86400));
        exhaustionLabel = days <= 0
          ? esc(t('command_palette.ai.disk_chart.exhaustion_now', 'fills imminently'))
          : esc(t('command_palette.ai.disk_chart.exhaustion_days', 'runs out in ~' + days + ' days')
            .replace('{days}', String(days)));
      } else if (typeof slope === 'number' && slope <= 0) {
        exhaustionLabel = esc(t('command_palette.ai.disk_chart.stable', 'stable / shrinking'));
      } else {
        exhaustionLabel = esc(t('command_palette.ai.disk_chart.long_horizon', 'no exhaustion in window'));
      }
      const summaryRow = (
        '<div class="ai-resp-chart-summary">'
        + (summaryParts.length ? summaryParts.join('') : '')
        + (trendLabel ? ' · <span class="ai-resp-chart-sub">' + esc(trendLabel) + '</span>' : '')
        + ' · <span class="ai-resp-chart-sub">' + exhaustionLabel + '</span>'
        + '</div>'
      );
      // Source label — the operator-friendly name of the sampler
      // table the projection is built from. Helps debug "AI says X
      // but chart says Y" cases where two sources for the same host
      // disagree (e.g. NE root-disk vs Pulse full-pool ZFS view).
      // The endpoint picks the source whose latest disk_total is
      // LARGEST (the canonical pool view), but operators still want
      // to see which one was picked.
      const sourceLabel = (data && data.source)
        ? esc(t('command_palette.ai.disk_chart.source_' + data.source, data.source))
        : '';
      const sourceChip = sourceLabel
        ? ('<span class="ai-resp-chart-source" title="'
           + esc(t('command_palette.ai.disk_chart.source_label', 'Source'))
           + '">'
           + esc(t('command_palette.ai.disk_chart.source_label', 'Source')) + ': ' + sourceLabel
           + '</span>')
        : '';
      // Stale-data badge — backend stamps `stale: true` when the most
      // recent sample is older than 30 minutes (typical sampler runs
      // every 5 min, so > 30 min means the provider has stopped
      // reporting). The chart still renders the projection from the
      // frozen snapshot but the operator gets a visible "data may be
      // stale" cue instead of trusting a quiet projection.
      const isStale = !!(data && data.stale);
      const ageMinutes = Math.round(((data && data.stale_age_seconds) || 0) / 60);
      const staleBadge = isStale
        ? ('<span class="ai-resp-chart-stale" title="'
           + esc(t('command_palette.ai.disk_chart.stale_title', { minutes: ageMinutes }))
           + '">'
           + esc(t('command_palette.ai.disk_chart.stale_label', 'Stale'))
           + '</span>')
        : '';
      // Confidence-pill tooltip — explains the underlying R² + sample
      // count so operators can answer "why is my projection muted?"
      // without guessing. Backend resolution table:
      //   high   ≥ 0.85 R² AND ≥ 60 samples
      //   medium ≥ 0.60 R² OR  ≥ 30 samples
      //   low    otherwise
      const r2 = (data && Number.isFinite(data.r2)) ? data.r2 : null;
      const sampleCount = (data && Number.isFinite(data.sample_count)) ? data.sample_count : null;
      const confidenceTitle = esc(t('command_palette.ai.disk_chart.confidence_title', {
        r2: (r2 !== null) ? r2.toFixed(3) : '—',
        samples: (sampleCount !== null) ? sampleCount : '—',
      }));
      // WCAG 1.1.1 data alternative — a concise sr-only summary the
      // screen reader announces alongside the chart's aria-label. AT
      // users get the numeric current / trend / exhaustion facts that
      // sighted users read from the chart. Pre-fix the chart was
      // labelled "X disk usage projection" but had no data alternative
      // — visually-impaired operators had to query separately.
      const srSummaryParts = [];
      if (currentPct !== null) {
        srSummaryParts.push(t('ai_sidebar.disk_projection.sr_current', { pct: currentPct })
                            || ('Current usage ' + currentPct + '%'));
      }
      if (trendLabel) {
        srSummaryParts.push(t('ai_sidebar.disk_projection.sr_trend', { trend: trendLabel })
                            || ('Trend: ' + trendLabel));
      }
      if (exhaustionLabel) {
        srSummaryParts.push(t('ai_sidebar.disk_projection.sr_exhaustion', { msg: exhaustionLabel })
                            || ('Projection: ' + exhaustionLabel));
      }
      const srSummary = srSummaryParts.length
        ? ('<p class="sr-only">' + esc(srSummaryParts.join('. ')) + '.</p>')
        : '';
      return (
        '<div class="ai-resp-chart-header">'
        + '<span class="ai-resp-chart-title">' + hidEsc + '</span>'
        + sourceChip
        + staleBadge
        + '<span class="ai-resp-chart-confidence ai-resp-chart-confidence--' + esc(confidence) + '"'
        + ' title="' + confidenceTitle + '">'
        + esc(confLabel) + '</span>'
        + '</div>'
        + '<div class="ai-resp-chart-body">' + svg + srSummary + '</div>'
        + summaryRow
      );
    },
    // Build a single dict that aggregates every chart cache the SPA
    // currently holds for one host: main hostHistory series (Beszel /
    // NE / Pulse path), ping history (separate `ping:<id>` namespace),
    // SNMP host-level history, SNMP per-iface throughput history, and
    // SNMP per-temperature-probe history. Each leaf carries the raw
    // points array PLUS a small summary block (count, oldest_ts,
    // newest_ts, newest_age_s) so the operator can spot "this chart is
    // empty" / "this chart is stale" / "this chart's points are
    // bunched in the past 5 min" at a glance without scrolling 1000+
    // points.
    //
    // Returns an empty object when nothing is cached — `hasDebugData`
    // is the gate the new "Chart data" section uses to hide cleanly
    // for hosts whose drawer hasn't fetched any chart yet.
    //
    // Consumed by:
    // (a) the new "Chart data" host-debug-box section in static/index.html
    // (b) `copyAllDebug` so the bundled paste-into-issue payload
    //     includes every signal a chart-related bug needs.
    chartDataBundle(host) {
      if (!host) {
        return {};
      }
      const out = {};
      const nowS = Math.floor(Date.now() / 1000);
      const summarise = (label, entry, pointsKey) => {
        if (!entry) {
          return;
        }
        const points = entry[pointsKey] || entry.series || entry.points || [];
        const arr = Array.isArray(points) ? points : [];
        const tField = (arr[0] && (arr[0].t || arr[0].ts)) ? (arr[0].t ? 't' : 'ts') : 't';
        const oldest = arr.length ? Number(arr[0][tField]) : null;
        const newest = arr.length ? Number(arr[arr.length - 1][tField]) : null;
        out[label] = {
          summary: {
            count: arr.length,
            oldest_ts: oldest,
            newest_ts: newest,
            newest_age_s: (newest != null && Number.isFinite(newest)) ? (nowS - newest) : null,
            loaded_at_ms: entry.loadedAt || null,
            error: entry.error || null,
            range_hours: this.hostHistoryRange || null,
          },
          // Full points array — verbose intentionally; the chart
          // bug under investigation usually sits in the data shape
          // (missing field, wrong key, drift), not in the summary.
          points: arr,
        };
      };
      // Main host history (Beszel / NE / Pulse aggregated path) —
      // shared cache slot keyed by `hostHistoryKey(h)` (beszel_id || id).
      try {
        const key = this.hostHistoryKey ? this.hostHistoryKey(host) : (host.beszel_id || host.id || '');
        if (key && this.hostHistory && this.hostHistory[key]) {
          summarise('main_history', this.hostHistory[key], 'series');
        }
      } catch (_) {}
      // Ping latency history — separate namespace `ping:<id>`.
      try {
        const pingKey = this.hostPingHistoryKey ? this.hostPingHistoryKey(host) : ('ping:' + (host.id || ''));
        if (pingKey && this.hostHistory && this.hostHistory[pingKey]) {
          summarise('ping_history', this.hostHistory[pingKey], 'series');
        }
      } catch (_) {}
      // SNMP per-host history (CPU / Mem / Disk / uptime).
      try {
        if (this.hostSnmpHistory && this.hostSnmpHistory[host.id]) {
          summarise('snmp_history', this.hostSnmpHistory[host.id], 'points');
        }
      } catch (_) {}
      // SNMP per-iface throughput history. Cache shape is
      // `{loading, error, ifaces: {ifname: [points]}, loadedAt}` —
      // walk `.ifaces` (NOT the cache root, that loop level only
      // contains bookkeeping fields). The cache-level metadata
      // (loading / error / loadedAt) lands in the OUTER `_cache_meta`
      // key so a stuck-loading state or fetch error is visible at a
      // glance even when no iface points have arrived yet. Emit the
      // section even when ifaces is empty (`_cache_meta` carries the
      // diagnostic signal).
      try {
        const ifaceCache = this.hostSnmpIfaceHistory && this.hostSnmpIfaceHistory[host.id];
        if (ifaceCache && typeof ifaceCache === 'object') {
          const ifacesMap = (ifaceCache.ifaces && typeof ifaceCache.ifaces === 'object') ? ifaceCache.ifaces : {};
          const ifaces = {};
          for (const [name, pts] of Object.entries(ifacesMap)) {
            const arr = Array.isArray(pts) ? pts : [];
            const oldest = arr.length ? Number(arr[0].ts || arr[0].t) : null;
            const newest = arr.length ? Number(arr[arr.length - 1].ts || arr[arr.length - 1].t) : null;
            ifaces[name] = {
              summary: {
                count: arr.length,
                oldest_ts: oldest,
                newest_ts: newest,
                newest_age_s: (newest != null && Number.isFinite(newest)) ? (nowS - newest) : null,
              },
              points: arr,
            };
          }
          out.snmp_iface_history = {
            _cache_meta: {
              loading: !!ifaceCache.loading,
              error: ifaceCache.error || null,
              loaded_at_ms: ifaceCache.loadedAt || null,
              iface_count: Object.keys(ifaces).length,
            },
            ifaces,
          };
        }
      } catch (e) {
        out.snmp_iface_history = { _cache_meta: { error: 'chartDataBundle iface: ' + String(e) } };
      }
      // SNMP per-temperature-probe history (Dell hardware probes,
      // chassis sensors, etc.). Cache shape mirrors the iface cache:
      // `{loading, error, probes: {probe_name: [points]}, loadedAt}`.
      // Same `_cache_meta` shape for stuck-loading / fetch-error
      // visibility.
      try {
        const tempCache = this.hostSnmpTempHistory && this.hostSnmpTempHistory[host.id];
        if (tempCache && typeof tempCache === 'object') {
          const probesMap = (tempCache.probes && typeof tempCache.probes === 'object') ? tempCache.probes : {};
          const probes = {};
          for (const [name, pts] of Object.entries(probesMap)) {
            const arr = Array.isArray(pts) ? pts : [];
            const oldest = arr.length ? Number(arr[0].ts || arr[0].t) : null;
            const newest = arr.length ? Number(arr[arr.length - 1].ts || arr[arr.length - 1].t) : null;
            probes[name] = {
              summary: {
                count: arr.length,
                oldest_ts: oldest,
                newest_ts: newest,
                newest_age_s: (newest != null && Number.isFinite(newest)) ? (nowS - newest) : null,
              },
              points: arr,
            };
          }
          out.snmp_temp_history = {
            _cache_meta: {
              loading: !!tempCache.loading,
              error: tempCache.error || null,
              loaded_at_ms: tempCache.loadedAt || null,
              probe_count: Object.keys(probes).length,
            },
            probes,
          };
        }
      } catch (e) {
        out.snmp_temp_history = { _cache_meta: { error: 'chartDataBundle temp: ' + String(e) } };
      }
      return out;
    },
    // Generic chart-data freshness — works for ALL provider chart
    // caches, not just SNMP. Returns `{age_s, label, stale}` derived
    // from the MOST RECENT sample timestamp across every chart cache
    // the host might have populated: main `hostHistory[key].series`
    // (Beszel / NE / Pulse / Webmin), ping `hostHistory[ping:id]`,
    // SNMP host `hostSnmpHistory[id].points`, SNMP per-iface
    // `hostSnmpIfaceHistory[id].ifaces[*]`, SNMP per-temperature-probe
    // `hostSnmpTempHistory[id].probes[*]`. Used by the chart-strip
    // header's freshness label so the operator sees ONE unified
    // "Last sample Xm ago" signal regardless of which provider is
    // backing the charts. Pre-fix the strip showed two separate
    // labels — `hostHistoryFreshness` (which reads `loadedAt`, the
    // FETCH time, not the data) AND a SNMP-specific freshness banner
    // — which split the operator's attention and confused them when
    // they disagreed (fetch time fresh but data old, or vice versa).
    //
    // Returns null when no chart cache has any data (chart's own
    // "Collecting data" placeholder handles that signal).
    chartFreshness(h) {
      if (!h) {
        return null;
      }
      let maxTs = 0;
      const collectFromSeries = (entry, field) => {
        if (!entry) {
          return;
        }
        const arr = (entry && Array.isArray(entry[field])) ? entry[field] : [];
        if (!arr.length) {
          return;
        }
        const last = arr[arr.length - 1];
        const ts = Number((last && (last.t || last.ts)) || 0);
        if (ts > maxTs) {
          maxTs = ts;
        }
      };
      try {
        const mainKey = this.hostHistoryKey ? this.hostHistoryKey(h) : '';
        if (mainKey && this.hostHistory) {
          collectFromSeries(this.hostHistory[mainKey], 'series');
        }
        const pingKey = this.hostPingHistoryKey ? this.hostPingHistoryKey(h) : '';
        if (pingKey && this.hostHistory) {
          collectFromSeries(this.hostHistory[pingKey], 'series');
        }
        if (this.hostSnmpHistory) {
          collectFromSeries(this.hostSnmpHistory[h.id], 'points');
        }
        const ih = this.hostSnmpIfaceHistory && this.hostSnmpIfaceHistory[h.id];
        if (ih && ih.ifaces && typeof ih.ifaces === 'object') {
          for (const k of Object.keys(ih.ifaces)) {
            const arr = Array.isArray(ih.ifaces[k]) ? ih.ifaces[k] : [];
            if (!arr.length) {
              continue;
            }
            const ts = Number((arr[arr.length - 1] || {}).ts || (arr[arr.length - 1] || {}).t || 0);
            if (ts > maxTs) {
              maxTs = ts;
            }
          }
        }
        const th = this.hostSnmpTempHistory && this.hostSnmpTempHistory[h.id];
        if (th && th.probes && typeof th.probes === 'object') {
          for (const k of Object.keys(th.probes)) {
            const arr = Array.isArray(th.probes[k]) ? th.probes[k] : [];
            if (!arr.length) {
              continue;
            }
            const ts = Number((arr[arr.length - 1] || {}).ts || (arr[arr.length - 1] || {}).t || 0);
            if (ts > maxTs) {
              maxTs = ts;
            }
          }
        }
        // HTTP probe latency history shares the host drawer's
        // freshness label — every successful sampler tick advances
        // the per-URL `series[].t` so the "Last sample Xm ago" hint
        // also covers the HTTP / TLS / DNS chart.
        const httpEntry = this.hostHttpProbeHistory && this.hostHttpProbeHistory[h.id];
        collectFromSeries(httpEntry, 'series');
      } catch (_) { /* defensive */ }
      if (!maxTs) {
        return null;
      }
      const nowS = (this.hostHistoryNow || Date.now()) / 1000;
      const ageS = Math.max(0, Math.round(nowS - maxTs));
      let label;
      if (ageS < 60) {
        label = ageS + 's';
      }
      else {
        if (ageS < 3600) {
          label = Math.round(ageS / 60) + 'm';
        } else {
          label = Math.round(ageS / 3600) + 'h';
        }
      }
      return { age_s: ageS, label, stale: ageS > 600 };
    },

    // Per-host definitive source label for the chart-help tooltips
    //. Resolves the actual provider that populates a given
    // metric for THIS host, considering what's mapped on the host
    // record + each metric's provider precedence. Falls back to the
    // generic i18n string when nothing is configured. Network has
    // a special path: when both Beszel and NE are mapped, NE rates
    // back-fill the chart whenever Beszel returns zero (host_net
    // sampler) — surface that explicitly.
    metricSource(h, key) {
      const fallback = this.t('hosts_extra.metrics.source_' + key);
      if (!h) {
        return fallback;
      }
      const beszel = (h.beszel_name || '').trim();
      const beszelId = (h.beszel_id || '').trim();
      const pulse = (h.pulse_name || '').trim();
      const ne = (h.ne_url || '').trim();
      const webmin = (h.webmin_name || h.webmin_url || '').trim();
      const snmp = (h.snmp_name || '').trim();
      const beszelLabel = beszel || beszelId;
      const snmpEnabled = h.snmp_enabled === true && snmp;
      const pingEnabled = h.ping_enabled === true;

      // Provider precedence per metric. Order matters: first match wins
      // for "what populates this for this host". NE-only metrics that
      // Beszel doesn't track are flagged when Beszel is the only source.
      const precedence = {
        // CPU is now sampled by NE too — derived from
        // node_cpu_seconds_total deltas — so NE qualifies as a
        // CPU provider alongside Beszel. SNMP fallback for managed
        // network gear / printers / UPSes.
        cpu:        ['beszel', 'ne', 'snmp'],
        memory:     ['pulse', 'beszel', 'ne', 'snmp'],
        disk:       ['beszel', 'ne'],
        disk_io:    ['beszel', 'ne'],
        load_avg:   ['beszel', 'ne', 'snmp'],
        swap:       ['beszel'],
        // Temperature comes from Beszel only today — node-
        // exporter exposes thermal via `node_hwmon_temp_celsius` but
        // OmniGrid's NE sampler doesn't extract those yet. Add 'ne'
        // to the precedence list when that work lands.
        temperature: ['beszel'],
        // GPU comes from Beszel only — agent reads NVIDIA / AMD GPU
        // stats and emits per-GPU power / usage / VRAM in `stats.g`.
        gpu:         ['beszel'],
        // Network + Bandwidth share the same upstream + the NE-fallback
        // when both are present. SNMP throughput chart reads
        // ifHCInOctets / ifHCOutOctets from the device itself.
        network:    ['beszel', 'ne', 'snmp'],
        bandwidth:  ['beszel', 'ne', 'snmp'],
        // SNMP-only metrics — switch / router / printer kit.
        snmp_throughput: ['snmp'],
        snmp_cpu:    ['snmp'],
        snmp_memory: ['snmp'],
        snmp_load:   ['snmp'],
        snmp_pages:  ['snmp'],
        // Dell server temperatures come from the iDRAC's
        // temperatureProbeTable walk (1.3.6.1.4.1.674.10892.5.4.700.20).
        // SNMP-only — no Beszel / NE crossover.
        dell_temps:  ['snmp'],
        // Ping is its own thing — TCP / ICMP probe per host.
        ping:        ['ping'],
      };
      // Ping label resolves the per-host transport + port the user
      // actually configured (or the global defaults when no per-host
      // override is set), AND prefixes the resolved host so the
      // tooltip names WHICH target is being probed. Pre-fix the
      // tooltip read literally "Ping probe (this host)" or "(ICMP)"
      // with no indication of which host the probe targeted.
      // Format: "Ping probe (<host> · ICMP)" or "Ping probe (<host> · TCP :<port>)".
      // Per-host values come from the API row (`ping_transport` /
      // `ping_port`); global defaults come from `me.client_config.ping`;
      // host name comes from `h.label || h.id`.
      let pingLabel = '';
      if (pingEnabled) {
        const cfgGlobal = (this.me && this.me.client_config && this.me.client_config.ping) || {};
        const hostTransport = String((h && h.ping_transport) || '').toLowerCase();
        const useIcmp = (hostTransport === 'icmp')
                     || (hostTransport === '' && !!cfgGlobal.use_icmp);
        // Use the BACKEND-RESOLVED ping target so the tooltip names
        // the actual DNS / IP being probed — not just the curated
        // host_id (which is often a label like "ftth" that doesn't
        // resolve via DNS). `h.ping_target` is computed in
        // `_shape_host_api_row` per the same chain
        // `logic.db.curated_ping_hosts` uses: ssh.fqdn → ssh.host →
        // host.id. Falls back to label/id if the API row predates the
        // ping_target field (defence-in-depth on a stale SPA cache).
        const targetName = (
          (h && h.ping_target)
          || (h && (h.label || h.id || ''))
          || ''
        ).toString().trim();
        let probeStr;
        if (useIcmp) {
          probeStr = 'ICMP';
        } else {
          const port = (h && Number(h.ping_port))
                       || (cfgGlobal.default_port ? Number(cfgGlobal.default_port) : 443);
          probeStr = `TCP :${port}`;
        }
        pingLabel = targetName
          ? `Ping probe (${targetName} · ${probeStr})`
          : `Ping probe (${probeStr})`;
      }
      const providers = {
        beszel: beszelLabel ? `Beszel agent (${beszelLabel})` : '',
        pulse:  pulse       ? `Pulse (${pulse})`              : '',
        ne:     ne          ? `node-exporter (${ne})`         : '',
        webmin: webmin      ? `Webmin (${webmin})`            : '',
        snmp:   snmpEnabled ? `SNMP (${snmp})`                : '',
        ping:   pingLabel,
      };

      const order = precedence[key] || ['beszel', 'pulse', 'ne', 'webmin'];
      const active = order.filter(p => providers[p]);

      // No provider in this metric's precedence is mapped on the host.
      // Two sub-cases:
      // (a) Host has NO providers at all → use the generic i18n hint
      //     so the operator sees what could populate this metric.
      // (b) Host has SOME providers but none that supply THIS metric
      //     (e.g. NE-only host viewing the CPU chart, since the NE
      //     sampler doesn't track CPU yet) → name the providers it
      //     DOES have and explain why this chart will be empty.
      if (active.length === 0) {
        const mapped = Object.values(providers).filter(p => p);
        if (mapped.length === 0) {
          return fallback;
        }
        const summary = mapped.join(', ');
        return `This host is mapped to ${summary}, but ${key} is not surfaced by any of those providers — chart will stay empty until you add a provider that tracks it.`;
      }

      const primary = providers[active[0]];

      // Operator-flagged: just the active source — no fallback chain
      // suffix, no dual-source phrasing. The chart is rendered from
      // ONE provider's data; calling out fallbacks confused operators
      // into thinking the chart was somehow merged. Even the Network
      // chart's NE-back-fill nuance (when Beszel returns zero we
      // overlay NE rates from host_net_samples) is suppressed in the
      // tooltip — too much detail for a one-line chip hint.
      return primary;
    },
    // Produce 3 Y-axis labels (top/middle/bottom) for a chart with a
    // fixed max — percent charts use 100/50/0. Range is [0..100] here.
    yAxisPercent() { return ['100%', '50%', '0%']; },
    // Produce 4 Y-axis labels for an auto-ranged chart (max at top,
    // zero at bottom, two interpolated ticks between).
    yAxisAuto(max, formatter) {
      const fmt = formatter || this._fmtAxisBytes;
      if (!max || max <= 0) {
        return [fmt(0), '', '', fmt(0)];
      }
      return [fmt(max), fmt(max * 0.66), fmt(max * 0.33), fmt(0)];
    },
    xAxisFromSeries(systemId, slots) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series || entry.series.length < 2) {
        return [];
      }
      // Range-aware default — caller may still pin a specific count
      // by passing a non-default integer. The legacy `slots=5` call
      // sites (every drawer-chart consumer pre-fix) intentionally
      // route through the resolver so the new tick counts apply
      // uniformly.
      const _RANGE_DEFAULTED = (slots === undefined || slots === null || slots === 5);
      const n = _RANGE_DEFAULTED ? this._hostChartTickCount() : Math.max(2, Number(slots) || 5);
      const dom = this._drawerTimeDomain();
      const span = Math.max(1, dom.tMaxSec - dom.tMinSec);
      const out = [];
      for (let i = 0; i < n; i++) {
        const ts = dom.tMinSec + Math.round((i / (n - 1)) * span);
        out.push(this._fmtAxisTime(ts));
      }
      return out;
    },

    // Build an SVG path for one metric across the host's history. Native
    // line chart, no Chart.js dependency — the series is small and the
    // shape is simple enough that a polyline does the job. The result
    // carries extra fields (area path, gridlines, axis labels) so the
    // template can render a richer chart than just a single polyline.
    hostChart(systemId, key, opts = {}) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series || entry.series.length < 2) {
        return null;
      }
      const W = opts.width || 420;
      const H = opts.height || 100;
      const PAD_X = 4;
      const PAD_T = 6;   // top pad so the peak doesn't clip against the border
      const PAD_B = 4;
      const pts = entry.series.map(r => r[key]);
      let lo = Infinity, hi = -Infinity;
      for (const v of pts) {
        const n = Number(v) || 0;
        if (n < lo) {
          lo = n;
        }
        if (n > hi) {
          hi = n;
        }
      }
      if (!isFinite(lo)) { lo = 0; hi = 1; }
      if (hi - lo < 0.5) { lo = Math.max(0, lo - 0.5); hi = lo + 1; }
      // Optional forced range — e.g. CPU/Mem/Disk charts clamp to 0..100.
      if (opts.min !== undefined) {
        lo = opts.min;
      }
      if (opts.max !== undefined) {
        hi = opts.max;
      }
      // — Unified drawer time-domain. When every point has a `t`
      // timestamp (epoch seconds — set by the loader on every Beszel/NE/
      // Ping fetch), the leftmost pixel of every host-drawer chart now
      // means "the start of the picker window" (now - rangeMs) and the
      // rightmost means "now". Sparse-sample providers (e.g. NE with 4
      // samples/hour) start mid-axis at their earliest sample instead
      // of stretching to fill the full width — letting operators visually
      // compare spikes across cards on the same time-grid. Falls back
      // to the legacy index-based stepping when no timestamps are
      // available (defence-in-depth: the loader has emitted `t` since
      // Beszel landed; index fallback only fires if a future change to
      // hostHistory loaders forgets to stamp it).
      const usableW = W - PAD_X * 2;
      const usableH = H - PAD_T - PAD_B;
      const dom = this._drawerTimeDomain();
      const tSpan = Math.max(1, dom.tMaxSec - dom.tMinSec);
      const haveTimes = entry.series.every(r => Number(r && r.t) > 0);
      const xy = entry.series.map((r, i) => {
        const n = Number(r[key]) || 0;
        let x;
        if (haveTimes) {
          const ts = Number(r.t) || 0;
          // Out-of-range samples render at the clamped edge so the
          // line meets the axis cleanly; the polyline doesn't extend
          // beyond [PAD_X, W - PAD_X].
          x = PAD_X + Math.max(0, Math.min(1, (ts - dom.tMinSec) / tSpan)) * usableW;
        } else {
          const step = usableW / Math.max(1, entry.series.length - 1);
          x = PAD_X + i * step;
        }
        return {
          x,
          y: PAD_T + usableH - ((n - lo) / (hi - lo || 1)) * usableH,
        };
      });
      const points = xy.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
      // Gap-aware path — same coordinates as `points` but emits M
      // (moveto) instead of L (lineto) at every long sampling gap so
      // the rendered line breaks. Catches multi-hour host outages
      // where Beszel / NE / Ping samplers simply stopped writing rows
      // — pre-fix the line bridged the dead period as one fake-smooth
      // segment, painting "down for hours" as "fading from X to Y".
      // Threshold auto-derived from the median sample interval × 2.5
      // so the same logic works whatever provider produced the series.
      // Consumers swap `<polyline :points="hostChart(...).points">`
      // for `<path class="metric-line" :d="hostChart(...).pathGapped">`.
      const seriesTs = haveTimes ? entry.series.map(r => Number(r.t) || 0) : null;
      const gapThr = seriesTs ? this._detectGapThresholdSec(seriesTs) : null;
      const gapSegs = [];
      let prevSegT = 0;
      for (let i = 0; i < xy.length; i++) {
        const curT = seriesTs ? (seriesTs[i] || 0) : 0;
        const isGap = (gapThr && i > 0 && prevSegT > 0 && curT > 0 && (curT - prevSegT) > gapThr);
        gapSegs.push((i === 0 || isGap ? 'M' : 'L') + xy[i].x.toFixed(1) + ',' + xy[i].y.toFixed(1));
        prevSegT = curT;
      }
      const pathGapped = gapSegs.join(' ');
      // Area path — polyline + closure back to baseline so we can fill
      // under the curve. Baseline is the chart's bottom. Gap-aware:
      // each contiguous run is filled separately (closes back to
      // baseline before the next run starts) so a multi-hour gap
      // doesn't produce a single fake-smooth filled trapezoid bridging
      // the dead period.
      const baseY = (H - PAD_B).toFixed(1);
      const areaSegs = [];
      let runStartIdx = -1;
      const closeRun = (endIdx) => {
        if (runStartIdx < 0 || endIdx < runStartIdx) {
          return;
        }
        let seg = `M${xy[runStartIdx].x.toFixed(1)},${baseY}`;
        for (let j = runStartIdx; j <= endIdx; j++) {
          seg += ` L${xy[j].x.toFixed(1)},${xy[j].y.toFixed(1)}`;
        }
        seg += ` L${xy[endIdx].x.toFixed(1)},${baseY} Z`;
        areaSegs.push(seg);
      };
      let prevAreaT = 0;
      for (let i = 0; i < xy.length; i++) {
        const curT = seriesTs ? (seriesTs[i] || 0) : 0;
        const isGap = (gapThr && i > 0 && prevAreaT > 0 && curT > 0 && (curT - prevAreaT) > gapThr);
        if (isGap) {
          closeRun(i - 1);
          runStartIdx = i;
        } else if (runStartIdx < 0) {
          runStartIdx = i;
        }
        prevAreaT = curT;
      }
      closeRun(xy.length - 1);
      const area = areaSegs.join(' ');
      // Horizontal reference ticks — three evenly-spaced gridlines so
      // the eye has something to anchor the peaks against.
      const ticks = [0.25, 0.5, 0.75].map(frac => ({
        y: (PAD_T + usableH * frac).toFixed(1),
        value: (hi - (hi - lo) * frac),
      }));
      // Pre-rendered gridline path — a single SVG path string with
      // `M0,y H W` for each tick. Used instead of an Alpine
      // `<template x-for>` inside `<svg>` (Alpine's `<template>`
      // doesn't work in the SVG namespace — the browser parses
      // SVG `<template>` as an unknown element, child nodes never
      // attach to its `.content` document fragment, so Alpine
      // throws "Cannot read properties of undefined (reading
      // 'children')" + the inner `tk` reference goes undefined).
      // Single path keeps the SVG renderable + avoids the runtime
      // error; visual outcome is identical (3 horizontal lines).
      // The y coordinate uses the same `* 1.2` multiplier the old
      // template did so the gridline positions match the layout
      // the operator was already seeing.
      const gridPath = ticks
        .map(t => `M0,${(parseFloat(t.y) * 1.2).toFixed(1)} H${W}`)
        .join(' ');
      const cur = Number(pts[pts.length - 1]) || 0;
      return {
        points,
        pathGapped,
        area,
        ticks,
        gridPath,
        width: W,
        height: H,
        min: lo,
        max: hi,
        current: cur,
      };
    },
    // Min/Max label helper — returns both pre-formatted strings
    // (used directly in the chart header) and raw numeric values
    // (used by templates to decide flat-signal collapsing).
    // Shared peak across multiple keys — used by the combined Net I/O
    // and Disk I/O charts so the two polylines render
    // against the same y-axis and are visually comparable.
    // Returns 0 when no data so the caller can short-circuit the chart.
    hostChartMax(systemId, keys) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series) {
        return 0;
      }
      let m = 0;
      for (const k of keys) {
        for (const r of entry.series) {
          const n = Number(r[k]) || 0;
          if (n > m) {
            m = n;
          }
        }
      }
      return m;
    },
    // "permanently flat" detector. Returns true when the chart
    // has accumulated enough history (default ≥ 12 points = 1 hour at
    // a 5-min cadence) AND every point across the listed fields is 0.
    // Caller uses this to HIDE chart cards whose data source is
    // genuinely never going to populate (e.g. SNMP-only TrueNAS host
    // for Disk I/O — SNMP doesn't track per-mount IOPS, so dr/dw stay
    // at 0 forever). Pre-fix the card stayed visible permanently with
    // a "Disk idle" hint, which read like "data is loading" instead of
    // "this provider doesn't surface this metric". Hosts in warmup
    // (< minPoints) get the benefit of the doubt — chart still shows.
    hostChartIsPermanentlyFlat(systemId, keys, minPoints) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series) {
        return false;
      }
      const points = entry.series.length;
      const need = +minPoints || 12;
      if (points < need) {
        return false;
      }     // still warming up
      // hostChartMax === 0 means every point in every key is 0/missing.
      return this.hostChartMax(systemId, keys) === 0;
    },
    // Per-sensor temperature readout for the chart card stats line
    //. Returns [[sensor_name, celsius], ...] sorted hottest-
    // first, capped at 3 — modern hosts can expose 8+ sensors
    // (coretemp_package + nvme_composite + acpitz + per-core +
    // hwmon …) and shoving them all into the inline header
    // overflowed the row, decoupling sensor name from value. The
    // chart line carries `temp_max` (the global peak across ALL
    // sensors at each tick) so nothing hot gets hidden — only the
    // verbose readout is trimmed. The full per-sensor dict is
    // available via `h.host_temperatures` if a future drawer wants
    // to expose it.
    hostTemperatureRows(h) {
      const t = (h && h.host_temperatures) || {};
      const rows = Object.entries(t).filter(([, c]) => Number.isFinite(Number(c)));
      rows.sort((a, b) => Number(b[1]) - Number(a[1]));
      return rows.slice(0, 3);
    },
    // True when the host emits more sensors than we show inline.
    // Drives the "+N more" chip the operator sees when there's a long
    // tail beyond the top 3.
    hostTemperatureExtraCount(h) {
      const t = (h && h.host_temperatures) || {};
      const n = Object.keys(t).length;
      return n > 3 ? (n - 3) : 0;
    },
    // Deterministic per-sensor colour token. Cycles through the
    // five existing pill / accent tokens so we don't introduce new
    // colour literals (CLAUDE.md token discipline). Index comes from
    // a sorted-name lookup so each sensor always gets the same colour
    // across renders, regardless of `Object.entries` iteration order.
    hostTempLineColor(name, sortedNames) {
      const palette = [
        'var(--primary)',
        'var(--warning)',
        'var(--danger)',
        'var(--success)',
        'var(--info)',
      ];
      const idx = sortedNames.indexOf(name);
      return palette[(idx >= 0 ? idx : 0) % palette.length];
    },
    // Multi-line chart helper for the Temperature card. Produces
    // one polyline per sensor with auto-scaled Y axis, padded ±5°C so
    // tight ranges still render with clear vertical movement. Sensors
    // are discovered by walking every point's `temps` dict (the union,
    // since some sensors only appear partway through a session). Each
    // sensor's missing samples are skipped, not zero-padded — that
    // matches CLAUDE.md's "skip-don't-synthesize" rule for time series.
    // Returns null when there's not enough data to draw two points.
    hostTempChart(systemId, opts = {}) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series || entry.series.length < 2) {
        return null;
      }
      const W = opts.width || 420;
      const H = opts.height || 120;
      const PAD_X = 4;
      const PAD_T = 6;
      const PAD_B = 4;
      // Discover the union of sensor names across the whole window
      // and capture both lo and hi so the Y axis auto-fits.
      const sensorNames = new Set();
      let lo = Infinity, hi = -Infinity;
      for (const r of entry.series) {
        const t = r && r.temps;
        if (!t || typeof t !== 'object') {
          continue;
        }
        for (const [name, c] of Object.entries(t)) {
          const n = Number(c);
          if (!Number.isFinite(n)) {
            continue;
          }
          sensorNames.add(name);
          if (n < lo) {
            lo = n;
          }
          if (n > hi) {
            hi = n;
          }
        }
      }
      if (!isFinite(lo) || sensorNames.size === 0) {
        return null;
      }
      // ±5°C breathing room so flat-ish series don't render as a
      // single horizontal pixel. Min anchored at >=0 so a freezer
      // host with subzero readings doesn't leave a Y-axis label
      // showing negative-zero.
      lo = Math.max(0, Math.floor(lo - 5));
      hi = Math.ceil(hi + 5);
      if (hi - lo < 10) {
        hi = lo + 10;
      }
      const sortedNames = Array.from(sensorNames).sort();
      const usableW = W - PAD_X * 2;
      const usableH = H - PAD_T - PAD_B;
      // — Unified drawer time-domain. Same contract as `hostChart`:
      // when every series row has a `t` epoch-seconds timestamp, x is
      // computed against the picker window so this chart's pixels align
      // with every other drawer chart. Index-based fallback for any
      // future loader regression that forgets to stamp `t`.
      const dom = this._drawerTimeDomain();
      const tSpan = Math.max(1, dom.tMaxSec - dom.tMinSec);
      const haveTimes = entry.series.every(r => Number(r && r.t) > 0);
      const stepFallback = usableW / Math.max(1, entry.series.length - 1);
      // Five-token palette, matched to the slug list returned in
      // dByColor below. Index = sensor's position in sortedNames; %5
      // wraps when a host has more sensors than colours.
      const slugs = ['primary', 'warning', 'danger', 'success', 'info'];
      const dByColor = { primary: '', warning: '', danger: '', success: '', info: '' };
      const lines = [];
      // Time-gap break threshold — derived from the series cadence so
      // a multi-hour outage breaks the line for every sensor, not just
      // those whose individual sample happens to be missing at the
      // gap boundary.
      const seriesTs = haveTimes ? entry.series.map(r => Number(r && r.t) || 0) : null;
      const gapThr = seriesTs ? this._detectGapThresholdSec(seriesTs) : null;
      sortedNames.forEach((name, idx) => {
        const segs = [];   // SVG path data — handles missing samples
        let cur = '';
        let prevTs = 0;
        for (let i = 0; i < entry.series.length; i++) {
          const t = entry.series[i] && entry.series[i].temps;
          const v = t && Number(t[name]);
          if (!Number.isFinite(v)) {
            // Sample missing → break the line; next valid point
            // starts a new sub-path so we don't synthesise a slope
            // through a gap.
            if (cur) { segs.push(cur); cur = ''; }
            prevTs = 0;
            continue;
          }
          let x;
          let curTs = 0;
          if (haveTimes) {
            curTs = Number(entry.series[i].t) || 0;
            x = PAD_X + Math.max(0, Math.min(1, (curTs - dom.tMinSec) / tSpan)) * usableW;
          } else {
            x = PAD_X + i * stepFallback;
          }
          // Time-gap break — if the previous valid point was more than
          // `gapThr` seconds ago, start a fresh sub-path so the
          // rendered line doesn't bridge the dead period.
          if (cur && gapThr && prevTs > 0 && curTs > 0 && (curTs - prevTs) > gapThr) {
            segs.push(cur);
            cur = '';
          }
          const y = PAD_T + usableH - ((v - lo) / (hi - lo || 1)) * usableH;
          cur += (cur ? ' L' : 'M') + x.toFixed(1) + ',' + y.toFixed(1);
          prevTs = curTs;
        }
        if (cur) {
          segs.push(cur);
        }
        const d = segs.join(' ');
        if (!d) {
          return;
        }
        const slug = slugs[idx % slugs.length];
        dByColor[slug] = dByColor[slug] ? dByColor[slug] + ' ' + d : d;
        lines.push({ name, d, color: 'var(--' + slug + ')' });
      });
      if (lines.length === 0) {
        return null;
      }
      // Y-axis ticks: 3 labels (top / mid / bottom) so the .metric-y-axis
      // flex `justify-content: space-between` lands them at the same
      // visual rhythm as `yAxisPercent()` (`100% / 50% / 0%`). Earlier
      // ship used 4 labels which made the inner two land at 33%/66%
      // of the y-axis div — visually offset from anything meaningful
      // on the chart and read as "labels out of bounds".
      const mid = lo + (hi - lo) / 2;
      const yAxis = [hi, mid, lo].map(v => Math.round(v) + '°');
      return { lines, dByColor, yAxis, min: lo, max: hi, sortedNames };
    },
    hostMetricStats(systemId, key, asPct = true) {
      const entry = this.hostHistory[systemId];
      if (!entry || !entry.series || entry.series.length === 0) {
        return null;
      }
      let lo = Infinity, hi = -Infinity;
      for (const r of entry.series) {
        const n = Number(r[key]) || 0;
        if (n < lo) {
          lo = n;
        }
        if (n > hi) {
          hi = n;
        }
      }
      if (!isFinite(lo)) {
        return null;
      }
      if (asPct) {
        return {
          min: lo.toFixed(1) + '%',
          max: hi.toFixed(1) + '%',
          minRaw: lo, maxRaw: hi,
        };
      }
      // Operator-flagged: when `lo = 0` and `hi` is a non-zero MB/s
      // value, `fmtBytes` rendered `0 B/s` next to `19 MB/s` —
      // different units in the same chart's legend. Lock both
      // formatted values to the unit family of `hi` (the chart's
      // max) via `fmtBytesAt`.
      return {
        min: this.fmtBytesAt(lo, hi) + '/s',
        max: this.fmtBytesAt(hi, hi) + '/s',
        minRaw: lo, maxRaw: hi,
      };
    },
    // -------------------- Per-host Health Score --------------------
    // Synthesises CPU / Memory / Disk / Provider / Pending-Updates
    // signals into a single 0-100 score per host, with a per-axis
    // breakdown for the drawer popover. Operator quote: "anything
    // <80 gets attention this morning" — so the overall score is
    // worst-axis-wins (min of valid sub-scores), matching that
    // mental model exactly. A weighted-average alternative would
    // dilute a single bad axis and bury the actionable signal.
    //
  // Each axis returns { key, label, score: 0..100 | null, detail }.
    // null score means the axis is N/A for this host (no telemetry,
    // no providers configured, package count missing) and is skipped
    // from both the overall score and the breakdown list.
    //
  // Down / paused hosts short-circuit to a single Status axis
    // with score=0 — synthesising CPU% on a dead host is meaningless
    // and "100 / 100 / 0" averages would lie about reachability.
    healthAxes(h) {
      if (!h) {
        return [];
      }
      // Unconfigured / loading rows have nothing to grade — return []
      // so the chip hides cleanly via `healthScore() == null`.
      if (h.status === 'unconfigured' || h.status === 'loading') {
        return [];
      }
      // Down / paused → status axis dominates. Operator's reaction
      // to a 0 with reason "Sampling paused" is "click in", which
      // matches what they should be doing for a paused host anyway.
      if (h.sampling_paused || h.status === 'down') {
        return [{
          key: 'status',
          label: this.t('hosts_extra.health.axis_status'),
          score: 0,
          detail: h.sampling_paused
            ? this.t('hosts_extra.health.status_paused')
            : this.t('hosts_extra.health.status_down'),
        }];
      }
      const warn = this._statBarWarnPct();
      const crit = this._statBarCritPct();
      // Linear ramp: 100 at <=warn, 0 at >=crit, linear between.
      // Mirrors the existing barLevel() colour cue thresholds so
      // the chip's amber turn-over lines up with the stat-bar's.
      const pctScore = (v) => {
        if (v == null || !Number.isFinite(v)) {
          return null;
        }
        if (v >= crit) {
          return 0;
        }
        if (v <= warn) {
          return 100;
        }
        return Math.round(100 - ((v - warn) / (crit - warn)) * 100);
      };
      const axes = [];
      // CPU
      if (this.hostHasTelemetry(h) && Number.isFinite(h.cpu_percent)) {
        const s = pctScore(h.cpu_percent);
        if (s != null) {
          axes.push({
            key: 'cpu',
            label: this.t('hosts_extra.health.axis_cpu'),
            score: s,
            detail: (h.cpu_percent || 0).toFixed(1) + '%',
          });
        }
      }
      // Memory
      if (this.hostHasTelemetry(h)) {
        const mp = Number.isFinite(h.mem_percent) && h.mem_percent > 0
          ? h.mem_percent : this.memPercentOf(h);
        if (Number.isFinite(mp) && mp > 0) {
          const s = pctScore(mp);
          if (s != null) {
            axes.push({
              key: 'memory',
              label: this.t('hosts_extra.health.axis_memory'),
              score: s,
              detail: Math.round(mp) + '%',
            });
          }
        }
      }
      // Disk — worst-mount-wins. A 95% / 50% pair scores like 95%
      // because that one filling mount will start failing writes
      // while the other looks healthy. Single-mount fallback uses
      // h.disk_percent directly. Picks the mount with the highest
      // fill percent and reports its mountpoint in the detail
      // string so the operator knows which one is hot.
      if (Array.isArray(h.mounts) && h.mounts.length) {
        let worstPct = -1;
        let worstName = '';
        for (const m of h.mounts) {
          const fp = this.mountFillPercent(m);
          if (fp > worstPct) {
            worstPct = fp;
            worstName = m.mp || m.path || m.name || '/';
          }
        }
        if (worstPct >= 0) {
          const s = pctScore(worstPct);
          if (s != null) {
            axes.push({
              key: 'disk',
              label: this.t('hosts_extra.health.axis_disk'),
              score: s,
              detail: Math.round(worstPct) + '% · ' + worstName,
            });
          }
        }
      } else if (this.hostHasTelemetry(h) && Number.isFinite(h.disk_percent) && h.disk_percent > 0) {
        const s = pctScore(h.disk_percent);
        if (s != null) {
          axes.push({
            key: 'disk',
            label: this.t('hosts_extra.health.axis_disk'),
            score: s,
            detail: Math.round(h.disk_percent) + '%',
          });
        }
      }
      // Providers — deduct 25 per failing/paused provider, floored at 0.
      // Empty list (no providers enabled) returns null so the axis
      // doesn't drag a Ping-only host's score down to 100/100/0/N/A.
      const enabledAgents = this.hostEnabledAgents(h);
      if (enabledAgents.length) {
        const states = this.providerStates(h) || [];
        const failing = states.filter(p => p.state === 'failing' || p.state === 'paused');
        const score = Math.max(0, 100 - failing.length * 25);
        let detail;
        if (!failing.length) {
          detail = this.t('hosts_extra.health.providers_ok', { count: enabledAgents.length });
        } else {
          detail = this.t('hosts_extra.health.providers_failing', {
            count: failing.length,
            names: failing.map(p => p.name).join(', '),
          });
        }
        axes.push({
          key: 'providers',
          label: this.t('hosts_extra.health.axis_providers'),
          score, detail,
        });
      }
      // Pending updates — bucketed (0/75/45/15) so a host with 100+
      // pending updates dominates the score even though "pending"
      // doesn't mean "broken". 0 pending → 100 (axis effectively
      // n/a). 1-10 → 75 (mild nudge). 11-50 → 45 (something's been
      // ignored). >50 → 15 (someone hasn't run apt update in months).
      if (Number.isFinite(h.package_updates_count)) {
        const n = h.package_updates_count;
        let score, detail;
        if (n === 0) {
          score = 100;
          detail = this.t('hosts_extra.health.updates_zero');
        } else if (n <= 10) {
          score = 75;
          detail = this.t('hosts_extra.health.updates_pending', { count: n });
        } else if (n <= 50) {
          score = 45;
          detail = this.t('hosts_extra.health.updates_pending', { count: n });
        } else {
          score = 15;
          detail = this.t('hosts_extra.health.updates_pending', { count: n });
        }
        axes.push({
          key: 'updates',
          label: this.t('hosts_extra.health.axis_updates'),
          score, detail,
        });
      }
      return axes;
    },
    // ---- Stack blast-radius preview (MVP) ----
    // Renders an inline "This will affect: ..." block listing every
    // service / container in the stack the operator is about to update.
    // Composed from the already-cached `stack.items` array so there's
    // no extra fetch + the popup stays fast. Counts services + their
    // replicas + standalone containers separately so the operator
    // distinguishes "1 service × 3 replicas restart" from "3 separate
    // services restart". Returns '' for stacks with no items so the
    // dialog body skips the block cleanly. Per the CSS-no-fallbacks
    // rule, every chrome rule lives in `.blast-radius-block` family
    // declared in `static/css/style.css` — not inlined here.
    _renderStackBlastRadius(stack) {
      if (!stack) {
        return '';
      }
      // The stack object itself doesn't carry an `items` array — the
      // SPA's top-level `this.items` is the source of truth (same
      // shape `_stackSingleUpdateImage` uses). Filter by `stack_id`
      // to get every item that belongs to this stack.
      const items = (this.items || []).filter(it => it && it.stack_id === stack.stack_id);
      if (!items.length) {
        return '';
      }
      const esc = (s) => String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
      let services = 0, replicas = 0, containers = 0, orphans = 0;
      const lines = [];
      for (const it of items) {
        if (!it) {
          continue;
        }
        const type = it.type || '';
        if (type === 'service') {
          services += 1;
          replicas += Number(it.desired || 0) || 0;
        } else if (type === 'orphan') {
          orphans += 1;
        } else {
          containers += 1;
        }
        // Orphan-type items are leftover Swarm task containers from
        // the PREVIOUS image — already replaced by Swarm, scheduled
        // for /cleanup removal, NOT pending re-update. Exclude from
        // the update-available chip render to match the Telegram
        // /update preview's contract.
        const updateChip = (((it.status || '') === 'update') && ((it.type || '') !== 'orphan'))
          ? ` <span class="blast-radius-chip blast-radius-chip--update">${esc(this.t('blast_radius.has_update') || 'update available')}</span>`
          : '';
        lines.push(`<li class="blast-radius-item">`
          + `<span class="blast-radius-type">${esc(type || 'item')}</span> `
          + `<span class="blast-radius-name mono">${esc(it.name || '—')}</span>`
          + updateChip
          + `</li>`);
      }
      const summary = this.t('blast_radius.summary', {
        services, replicas, containers, orphans,
        total: items.length,
      }) || `${items.length} item(s): ${services} service(s) × ${replicas} replicas, ${containers} container(s), ${orphans} orphan(s).`;
      const head = esc(this.t('blast_radius.label') || 'This will affect:');
      return [
        '<div class="blast-radius-block">',
        `<div class="blast-radius-head"><span class="blast-radius-label">${head}</span></div>`,
        `<div class="blast-radius-summary">${esc(summary)}</div>`,
        `<ul class="blast-radius-list scrollbar">${lines.join('')}</ul>`,
        '</div>',
      ].join('');
    },
};
