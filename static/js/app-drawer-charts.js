// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,ExceptionCaughtLocallyJS
// noinspection OverlyComplexBooleanExpressionJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression,NegatedIfStatementJS
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,JSMissingAwait
// noinspection JSIfStatementsCanBeSimplified,IfStatementSimplifyable,RedundantIfStatementJS,RedundantLocalVariableJS,JSReusedLocalVariable
// noinspection HtmlUnknownTag,HtmlEmptyContent,HtmlEmptyTagsRecommendation,InnerHTMLJS,VoidExpressionJS,JSVoidExpression
// noinspection CssConvertColorToRgb,CssReplaceWithShorthandSafely,JSConvertColorToRgb,JSConvertColorToHex,JSConvertColorToHsl
// noinspection RegExpRedundantEscape,AnonymousCapturingGroupJS,RegExpAnonymousGroup,RegExpDuplicateCharacterInClass
// noinspection JSDeprecatedSymbols,DOMNotInherited,JSPotentiallyInvalidUsageOfThis,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML
// `document.body` works correctly under HTML5 — OmniGrid serves an
// HTML5 doctype, never XHTML, so the "may produce inconsistent results"
// note is a non-issue for this deployment.
// Comprehensive per-inspection suppressions mirror app-ai-admin.js.
// SPA-wide idioms covered: constants on the right of comparisons;
// anonymous arrow callbacks; chained map+filter; ternaries; magic
// numbers for unit-conversion (60/3600/86400 seconds, percentage
// thresholds 50/80/85, HTTP status codes 502/503/504); short
// uppercase locals (W/H/PAD) for SVG geometry; Alpine-called methods
// PyCharm can't trace; `throw new Error(...)` inside try blocks for
// unified error handling; date-format JS strings with `<y>/<m>/<d>`
// placeholders; `color-mix(in srgb,...)` CSS Color Module Level 5
// syntax (theme-aware translucent tints).
/* global Alpine, Swal, I18N, t */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA bulk-hosts actions + item-action handlers
//
// SPLIT FROM `app-drawer-bulk.js`. Cross-method `this.X` references keep
// working through the `_mergeKeepDescriptors` chain in app.js.

export default {
  snmpLoadLine(hostId, key) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    const cores = this.snmpCoresFor(hostId);
    // memo keyed on points identity + key + cores (host-static, but
    // included for safety) + length (via _snmpMemo). See _snmpPathMemo.
    return this._snmpMemo(series, 'load|' + key + '|' + cores, () => {
      const vals = series.map(p => Math.min(100, ((p[key] ?? 0) / cores) * 100));
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, 100, {times});
    });
  },
  // Legend / live value as percent (0..100, capped). Operator wants
  // "12 %" not "0.18".
  snmpLoadPctLive(hostId, liveLoad) {
    const cores = this.snmpCoresFor(hostId);
    return Math.max(0, Math.min(100, ((+liveLoad || 0) / cores) * 100));
  },
  snmpMemArea(hostId, key) {
    // For the memory chart — render each layer as a polyline, scaled
    // against mem_total. `key` ∈ {used, buffers, cached, free}.
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    if (!series.length) {
      return '';
    }
    // memo keyed on points identity + mem key + length. maxTotal is
    // derived from the same series so it's captured by the points identity.
    return this._snmpMemo(series, 'mem|' + key, () => {
      // Normalise against the largest mem_total seen (handles probes
      // pre/post a memory hot-add cleanly).
      let maxTotal = 0;
      for (const p of series) {
        maxTotal = Math.max(maxTotal, p.mem_total || 0);
      }
      if (!maxTotal) {
        return '';
      }
      const fieldKey = 'mem_' + key;
      const vals = series.map(p => p[fieldKey] || 0);
      const times = series.map(p => p.ts);
      return this._snmpPathGapped(vals, maxTotal, {times});
    });
  },
  // derive per-tick throughput series in bytes/sec from the
  // cumulative IF-MIB ifHCInOctets / ifHCOutOctets samples. Skip-
  // don't-synthesize: out-of-bounds deltas (negative = counter
  // reset / reboot, near-zero timespan, hour-plus gap, absurd byte
  // delta) become 0 in the rendered series so a flat segment is
  // visibly distinct from a real "host idle" zero. First point is
  // always 0 because there's no predecessor to diff against. dir ∈
  // {'rx', 'tx'}.
  snmpThroughputBpsSeries(hostId, dir) {
    const entry = this.hostSnmpHistory[hostId] || {};
    const series = entry.points || [];
    if (series.length < 2) {
      return [];
    }
    // memo the derived bps array per points identity + dir + length
    // (see _snmpPathMemo in app-drawer-bulk.js). Collapses the per-second
    // re-derivations done by the legend (snmpThroughputLast), the peak
    // (snmpThroughputMaxBps), and snmpThroughputLine while the drawer sits open.
    return this._snmpMemo(series, 'tps|' + dir, () => {
      const fieldKey = 'net_' + dir + '_total_bytes';
      // Dt cap scales with the server-side bucket cadence. Pre-fix a
      // hardcoded 3600s cap rejected every delta on 7d windows where
      // buckets are 5040s+ wide. Bumped to `bucket × 6` after operators
      // reported empty 7d charts on hosts where the SNMP sampler
      // intermittently fails (APC UPS / OPNsense-on-flaky-mgmt-LAN
      // pattern) — gaps of 3-5 missing buckets are normal and SHOULD
      // bridge cleanly. The 10 GB delta cap below still catches genuine
      // counter wraps / reboots. dtCap = max(7200, bucketS × 6) lets
      // 5-bucket gaps render as a continuous line; longer outages
      // (≥ 6 missing buckets) still break the polyline as a real gap.
      const bucketS = Number(entry.bucket_seconds) || 0;
      const dtCap = Math.max(7200, bucketS * 6);
      // Byte-delta cap also scales with `dt`. Pre-fix the static 10 GB
      // ceiling was tuned for 5-min raw cadence (~35 MB/s sustained per
      // delta) — comfortable for typical home-LAN. At 7d buckets (5040s)
      // 10 GB allows only ~16 Mbit/s sustained; anything busier got
      // filtered as if it were a counter wrap, leaving the chart empty
      // on hosts whose gateway pushes >100 Mbit/s during peak hours.
      // Cap is now `max(10 GB, dt × 125 MB/s)` — 125 MB/s = 1 Gbit/s
      // headroom per second of bucket width. Counter wrap (negative
      // delta or 2^64-scale jumps) is still caught by `db < 0` and the
      // generous-but-bounded ceiling.
      const _BYTE_RATE_CEILING = 125 * 1024 * 1024;          // 125 MB/s = 1 Gbit/s
      const _STATIC_BYTE_FLOOR = 10 * 1024 * 1024 * 1024;    // 10 GB preserves prior behaviour for raw cadence
      // skip-don't-synthesize: out-of-bounds deltas (counter
      // wrap, reboot, gap, null) emit `null` so `_snmpPolyPoints`
      // omits the point from the polyline. Pre-fix this filled with
      // 0 — visually identical to a real "0 bps idle" segment,
      // burying the wrap signal. First-sample slot stays null too
      // (no predecessor to diff against).
      const out = new Array(series.length).fill(null);
      for (let i = 1; i < series.length; i++) {
        const a = series[i - 1], b = series[i];
        const dt = (b.ts || 0) - (a.ts || 0);
        const av = a[fieldKey], bv = b[fieldKey];
        if (av == null || bv == null) {
          continue;
        }
        if (dt < 1 || dt > dtCap) {
          continue;
        }       // gap or doubled tick
        const db = bv - av;
        const byteCap = Math.max(_STATIC_BYTE_FLOOR, dt * _BYTE_RATE_CEILING);
        if (db < 0 || db > byteCap) {
          continue;
        }     // wrap / reboot / scaled cap
        out[i] = db / dt;
      }
      return out;
    });
  },
  // APC UPS Output Load % over the picker window.
  // Renders the percentage of UPS capacity in use — e.g. 13% on a
  // 10 kVA Smart-UPS RT means the connected gear is drawing ~1.3 kVA.
  // Reads `load_percent` from `host_snmp_samples` rows; NULL slots
  // (host wasn't a UPS yet, or sample didn't include the OID) are
  // omitted via `_snmpPolyPoints`. Y-axis pinned to 100% so a busy
  // UPS doesn't auto-rescale.
  snmpUpsLoadLine(hostId) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    if (!series.length) {
      return '';
    }
    const vals = series.map(p => (p.load_percent != null ? +p.load_percent : null));
    const times = series.map(p => p.ts);
    return this._snmpPathGapped(vals, 100, {times});
  },
  // True when at least 2 samples have a non-null load_percent —
  // used internally by the chart-body template to switch between
  // the polyline and the "Collecting data" placeholder.
  snmpHasUpsLoadHistory(hostId) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    let n = 0;
    for (const p of series) {
      if (p.load_percent != null) {
        n++;
        if (n >= 2) {
          return true;
        }
      }
    }
    return false;
  },
  // True when the UPS Load card should RENDER. Returns true on
  // live host_load_percent OR ≥1 historical sample. Card body uses
  // `snmpHasUpsLoadHistory` to decide between polyline and the
  // "Collecting…" hint. Pre-fix follow-up the card hid until the
  // sampler accumulated 2 ticks (~10 min on default cadence) — long
  // enough for operators to assume nothing was being recorded.
  snmpHasUpsLoad(hostId) {
    const drawer = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
    if (drawer && typeof drawer.host_load_percent === 'number') {
      return true;
    }
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    for (const p of series) {
      if (p.load_percent != null) {
        return true;
      }
    }
    return false;
  },
  // APC UPS Battery % over the picker window. Same shape as the
  // load helper; pinned to 100%. Renders the discharge curve when
  // the UPS is on battery + the recharge curve afterwards.
  snmpUpsBatteryLine(hostId) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    if (!series.length) {
      return '';
    }
    const vals = series.map(p => (p.battery_percent != null ? +p.battery_percent : null));
    const times = series.map(p => p.ts);
    return this._snmpPathGapped(vals, 100, {times});
  },
  snmpHasUpsBatteryHistory(hostId) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    let n = 0;
    for (const p of series) {
      if (p.battery_percent != null) {
        n++;
        if (n >= 2) {
          return true;
        }
      }
    }
    return false;
  },
  snmpHasUpsBattery(hostId) {
    const drawer = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
    if (drawer && typeof drawer.host_battery_percent === 'number') {
      return true;
    }
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    for (const p of series) {
      if (p.battery_percent != null) {
        return true;
      }
    }
    return false;
  },
  // APC UPS Battery temperature (°C) over the picker window. Auto-
  // ranges so a flat-ish 36°C line still has vertical movement
  // — caller passes Math.max(50, observed_max) so a normal-range
  // host renders ~36 / 40 / 50 ticks instead of all-0..100.
  snmpUpsBatteryTempLine(hostId) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    if (!series.length) {
      return '';
    }
    const vals = series.map(p => (p.battery_temp_c != null ? +p.battery_temp_c : null));
    const times = series.map(p => p.ts);
    let m = 0;
    for (const v of vals) {
      if (v != null && v > m) {
        m = v;
      }
    }
    return this._snmpPathGapped(vals, Math.max(50, m), {times});
  },
  snmpUpsBatteryTempMax(hostId) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    let m = 0;
    for (const p of series) {
      const v = p.battery_temp_c;
      if (v != null && v > m) {
        m = v;
      }
    }
    return Math.max(50, m);
  },
  snmpHasUpsBatteryTempHistory(hostId) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    let n = 0;
    for (const p of series) {
      if (p.battery_temp_c != null) {
        n++;
        if (n >= 2) {
          return true;
        }
      }
    }
    return false;
  },
  snmpHasUpsBatteryTemp(hostId) {
    const drawer = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
    if (drawer && typeof drawer.host_battery_temp_c === 'number') {
      return true;
    }
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    for (const p of series) {
      if (p.battery_temp_c != null) {
        return true;
      }
    }
    return false;
  },
  // Dell server temperature-probe chart helpers.
  // Multi-line — one polyline per probe, sharing a single y-axis
  // (max across all probes) so spikes on Inlet vs Exhaust are
  // visually comparable. Each probe gets a distinct hue from a small
  // palette; the legend below the chart pairs name + last reading.
  dellTempProbes(hostId) {
    // Sorted probe metadata for the chart + legend. Returns an array
    // of `{idx, name, points, last_c, color}`. Two sources merge:
    // 1) hostSnmpTempHistory — persisted samples from the sampler
    //    (provides the time-series points for the chart)
    // 2) drawerHost.host_dell_temps — live probe payload from the
    //    most recent /api/hosts/one fetch (provides immediate
    //    readings while history accumulates)
    // The merge lets the chart slot render as soon as the drawer is
    // open — pre-fix the chart card sat at "Collecting data..." until
    // 2 sampler ticks (~10 min) had landed two persisted samples.
    const entry = this.hostSnmpTempHistory[hostId] || {};
    const probes = entry.probes || {};
    const drawer = (this.drawerHost && this.drawerHost.id === hostId)
      ? this.drawerHost : null;
    const liveTemps = drawer && Array.isArray(drawer.host_dell_temps)
      ? drawer.host_dell_temps : [];
    // All 8 slots route through CSS tokens — the violet / pink / cyan
    // slots are extended-palette tokens added to :root for both themes
    // so the operator's theme switch re-tunes them consistently with
    // the other 5 (no hex literals in the JS, no theme drift).
    const palette = [
      'var(--info)', 'var(--warning)', 'var(--success)',
      'var(--danger)', 'var(--primary)', 'var(--chart-palette-violet)',
      'var(--chart-palette-pink)', 'var(--chart-palette-cyan)',
    ];
    // Build a unified probe map keyed by idx — history rows define
    // the probe set when present; live rows top up any probe that
    // history doesn't know about yet (first-tick scenario where the
    // sampler hasn't run but the drawer probe just landed).
    const merged = {};
    for (const idx of Object.keys(probes)) {
      merged[idx] = {
        idx, name: (probes[idx] || {}).name || `temp-${idx}`,
        points: Array.isArray((probes[idx] || {}).points)
          ? (probes[idx] || {}).points : []
      };
    }
    const nowTs = Math.floor(Date.now() / 1000);
    for (const t of liveTemps) {
      const idx = String(t.idx || '');
      if (!idx) {
        continue;
      }
      if (!merged[idx]) {
        merged[idx] = {idx, name: t.name || `temp-${idx}`, points: []};
      }
      // Append the live reading as a synthetic "now" sample only
      // when history is empty for this probe — otherwise the
      // sampler-persisted points are authoritative for time series.
      if (!merged[idx].points.length && t.celsius != null) {
        merged[idx].points = [{ts: nowTs, c: +t.celsius}];
      }
    }
    const idxs = Object.keys(merged).sort((a, b) => {
      const na = a.split('.').map(n => +n || 0);
      const nb = b.split('.').map(n => +n || 0);
      for (let k = 0; k < Math.max(na.length, nb.length); k++) {
        const da = na[k] || 0, db = nb[k] || 0;
        if (da !== db) {
          return da - db;
        }
      }
      return 0;
    });
    const out = [];
    let i = 0;
    for (const idx of idxs) {
      const p = merged[idx];
      const pts = p.points;
      let lastC = null;
      for (let j = pts.length - 1; j >= 0; j--) {
        if (pts[j].c != null) {
          lastC = pts[j].c;
          break;
        }
      }
      out.push({
        idx,
        name: p.name,
        points: pts,
        last_c: lastC,
        color: palette[i % palette.length],
      });
      i++;
    }
    return out;
  },
  dellTempMaxC(hostId) {
    let m = 0;
    const entry = this.hostSnmpTempHistory[hostId] || {};
    const probes = entry.probes || {};
    for (const idx of Object.keys(probes)) {
      const pts = (probes[idx] || {}).points || [];
      for (const pt of pts) {
        if (pt.c != null && pt.c > m) {
          m = pt.c;
        }
      }
    }
    // Also consider live drawer readings so the y-axis max stays
    // correct on first-tick scenarios where history is empty but
    // dellTempProbes synthesised single "now" points from the live
    // host_dell_temps payload.
    const drawer = (this.drawerHost && this.drawerHost.id === hostId)
      ? this.drawerHost : null;
    if (drawer && Array.isArray(drawer.host_dell_temps)) {
      for (const t of drawer.host_dell_temps) {
        if (t && t.celsius != null && +t.celsius > m) {
          m = +t.celsius;
        }
      }
    }
    // Domain floor — 60°C is a sensible upper bound for an
    // idle / lightly-loaded server so a flat 30-40°C line still has
    // visible vertical movement against the same axis as a loaded
    // host hitting 65-70°C.
    return Math.max(60, m);
  },
  dellTempLine(hostId, points) {
    // SVG path `d` for one probe's series, normalised against the
    // chart's shared y-max. The viewBox is 0 0 420 120 with
    // preserveAspectRatio="none", so x ∈ [0, 420] spans the full
    // chart width and y ∈ [0, 120] spans the full chart height.
    //
    // Single-point fallback: when only one valid sample exists,
    // _snmpPathGapped maps the synthetic "now" timestamp to the right
    // edge of the time domain — producing `M ~420,y` with no `L`
    // follow-up, which draws nothing AND a 4-pixel nub extension
    // would clip past the right edge. Instead, when the series has
    // exactly one valid point, render a full-width horizontal line
    // at the current temperature so the user sees an actual reading.
    // The full polyline replaces this once a second sample lands.
    if (!Array.isArray(points) || !points.length) {
      return '';
    }
    const validPts = points.filter(p => p && p.c != null);
    const max = this.dellTempMaxC(hostId);
    if (validPts.length === 1) {
      const c = +validPts[0].c;
      const y = Math.max(0, Math.min(120, 120 - (c / max) * 120));
      return `M 0,${y.toFixed(1)} L 420,${y.toFixed(1)}`;
    }
    const vals = points.map(p => (p && p.c != null ? +p.c : null));
    const times = points.map(p => p && p.ts);
    return this._snmpPathGapped(vals, max, {times});
  },
  dellHasTempHistory(hostId) {
    // Mount the chart slot whenever a probe has at least one sample
    // OR the live drawer payload has a `host_dell_temps` reading we
    // can synthesize a "now" point from (`dellTempProbes` does the
    // merge). dellTempLine handles single-point series by appending a
    // 4-pixel horizontal nub so the line is visible before history
    // accumulates the second point. Pre-fix this required ≥2 persisted
    // points per probe, leaving the "Collecting data..." placeholder
    // up for 5-10 minutes after deploy — the legend kept showing live
    // values while the chart slot stayed empty, which read as broken.
    const entry = this.hostSnmpTempHistory[hostId] || {};
    const probes = entry.probes || {};
    for (const idx of Object.keys(probes)) {
      if (((probes[idx] || {}).points || []).length >= 1) {
        return true;
      }
    }
    const drawer = (this.drawerHost && this.drawerHost.id === hostId)
      ? this.drawerHost : null;
    if (drawer && Array.isArray(drawer.host_dell_temps)) {
      for (const t of drawer.host_dell_temps) {
        if (t && t.celsius != null) {
          return true;
        }
      }
    }
    return false;
  },
  dellHasTemps(hostId) {
    // Card-render gate. True when the live drawer payload has
    // host_dell_temps OR the temp history has any rows. Mirrors the
    // UPS gate's "live OR history" predicate so the card stays
    // mounted when the live probe is briefly empty.
    const drawer = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
    if (drawer && Array.isArray(drawer.host_dell_temps) && drawer.host_dell_temps.length) {
      return true;
    }
    const entry = this.hostSnmpTempHistory[hostId] || {};
    const probes = entry.probes || {};
    return Object.keys(probes).length > 0;
  },
  snmpThroughputLine(hostId, dir) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    // memo the :d path per points identity + dir + length. The inner
    // bps series + peak are pure functions of the same points array, so the
    // points identity fully captures them. See _snmpPathMemo.
    return this._snmpMemo(series, 'tpsline|' + dir, () => {
      const vals = this.snmpThroughputBpsSeries(hostId, dir);
      if (!vals.length) {
        return '';
      }
      const m = this.snmpThroughputMaxBps(hostId);
      const times = series.map(p => p.ts);
      // Gap-aware path so wrap / reboot / gap nulls render as visual
      // breaks instead of straight-line bridges. Consumer must use
      // SVG <path :d> not <polyline :points>.
      return this._snmpPathGapped(vals, m || 1, {times});
    });
  },
  snmpThroughputMaxBps(hostId) {
    const rx = this.snmpThroughputBpsSeries(hostId, 'rx');
    const tx = this.snmpThroughputBpsSeries(hostId, 'tx');
    let m = 0;
    for (const v of rx) {
      if (v > m) {
        m = v;
      }
    }
    for (const v of tx) {
      if (v > m) {
        m = v;
      }
    }
    return m;
  },
  snmpThroughputLast(hostId, dir) {
    const vals = this.snmpThroughputBpsSeries(hostId, dir);
    for (let i = vals.length - 1; i >= 0; i--) {
      if (vals[i] > 0) {
        return vals[i];
      }
    }
    return 0;
  },
  snmpHasThroughput(hostId) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    if (series.length < 2) {
      return false;
    }
    for (const p of series) {
      if (p.net_rx_total_bytes != null || p.net_tx_total_bytes != null) {
        return true;
      }
    }
    return false;
  },
  // printer pages-printed sparkline. Series of pages-per-day
  // rates derived from adjacent samples of `printer_page_count`
  // (Printer-MIB prtMarkerLifeCount, monotonic). Skip-don't-
  // synthesize on out-of-bounds deltas (negative = printer reset
  // / counter rollover, near-zero timespan, hour-plus gap, > 10 000
  // pages = absurd-rate guard against agent glitches).
  snmpPagesPerDaySeries(hostId) {
    const entry = this.hostSnmpHistory[hostId] || {};
    const series = entry.points || [];
    if (series.length < 2) {
      return [];
    }
    // Same bucket-aware dt cap as snmpThroughputBpsSeries — 7d windows
    // bucket to 5040s, blowing past a static 3600s cap and zeroing
    // the entire series.
    const bucketS = Number(entry.bucket_seconds) || 0;
    const dtCap = Math.max(3600, bucketS * 3);
    const out = new Array(series.length).fill(0);
    for (let i = 1; i < series.length; i++) {
      const a = series[i - 1], b = series[i];
      const dt = (b.ts || 0) - (a.ts || 0);
      const av = a.printer_page_count, bv = b.printer_page_count;
      if (av == null || bv == null) {
        continue;
      }
      if (dt < 1 || dt > dtCap) {
        continue;
      }
      const dp = bv - av;
      if (dp < 0 || dp > 10000) {
        continue;
      }
      out[i] = (dp / dt) * 86400;     // pages per day
    }
    return out;
  },
  // Banner copy — derives the per-tick interval from the SAME tunable
  // the SNMP sampler uses. Resolution order :
  // 1. tuning_snmp_sample_interval_seconds when > 0 (SNMP runs at
  //    its own cadence, distinct from the global Beszel/NE one).
  // 2. tuning_stats_sample_interval_seconds (legacy / inherited
  //    cadence when the SNMP-specific knob is 0).
  // 3. 300s fallback when client_config hasn't hydrated.
  // Both knobs are surfaced via /api/me's client_config; the literal
  // `{minutes}` placeholder never reaches the rendered DOM.
  snmpWarmingUpText() {
    const cc = (this.me && this.me.client_config) || {};
    const snmpSec = +cc.snmp_sample_interval_seconds || 0;
    const statsSec = +cc.stats_sample_interval_seconds || 0;
    const sec = snmpSec > 0 ? snmpSec : (statsSec || 300);
    const minutes = Math.max(1, Math.round(sec / 60));
    let s = this.t('host_drawer.snmp_charts.warming_up', {minutes});
    // Defensive: if i18n's interpolation didn't substitute (older
    // browser-cached bundle, helper called pre-load, etc.) — replace
    // manually so the literal `{minutes}` placeholder never reaches
    // the operator's screen.
    if (typeof s === 'string' && s.indexOf('{minutes}') >= 0) {
      s = s.split('{minutes}').join(String(minutes));
    }
    return s;
  },
  // List of providers actually wired ON THIS HOST — drives the
  // "no data" banner so it only names providers the operator has
  // configured a target for on the specific row. Pre-fix the
  // banner read from the global `host_stats_source` CSV which
  // claimed Webmin/SNMP were checked even on hosts that had no
  // Webmin/SNMP target name set — confusing.
  enabledProvidersList(h) {
    if (!h) {
      return this.t('hosts_extra.no_data.no_providers') || 'any provider';
    }
    const out = [];
    if ((h.beszel_id || h.beszel_name || '').trim()) {
      out.push('Beszel');
    }
    if ((h.pulse_name || '').trim()) {
      out.push('Pulse');
    }
    if ((h.ne_url || '').trim()) {
      out.push('node-exporter');
    }
    if ((h.webmin_name || h.webmin_url || '').trim()) {
      out.push('Webmin');
    }
    if (h.snmp_enabled === true && (h.snmp_name || '').trim()) {
      out.push('SNMP');
    }
    if (h.ping_enabled === true) {
      out.push('Ping');
    }
    if (!out.length) {
      return this.t('hosts_extra.no_data.no_providers') || 'any provider';
    }
    if (out.length === 1) {
      return out[0];
    }
    if (out.length === 2) {
      return out[0] + ' or ' + out[1];
    }
    return out.slice(0, -1).join(', ') + ', or ' + out[out.length - 1];
  },
  // read the most-recent non-null `printer_page_count` from
  // the persisted SNMP history. Lets the Printer card surface a
  // lifetime page count immediately on drawer open from DB-backed
  // history instead of waiting for the live SNMP probe (10-30s
  // round trip). Falls back to 0 when no history exists.
  snmpLatestPageCount(hostId) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    for (let i = series.length - 1; i >= 0; i--) {
      const v = series[i].printer_page_count;
      if (v != null && v > 0) {
        return v;
      }
    }
    return 0;
  },
  // Decide whether to show the "Cached" pill on the chart strip.
  // The pill claims the chart line is bounded by the last successful
  // sampler tick — so the gate must check the CHART data's actual
  // freshness, not the merged-host scalar stale state. Pre-fix the
  // pill was gated on `isStale(h)`, which fires whenever ANY merged
  // scalar field came from snapshot — including hosts where the
  // sampler was still actively writing chart points but a different
  // provider was down (e.g. Beszel sampler keeps writing, but NE
  // went down so `host_kernel` is from snapshot). User-flagged: chart
  // showing "Updated 25s ago" alongside "Cached" pill — confusing.
  //
  // True chart-stale signal: the newest sample timestamp in the
  // primary hostHistory cache is older than ~2× the sampler cadence.
  // Default sampler interval is 300s, so threshold 600s. Below that
  // → sampler is still writing → chart is live → pill HIDDEN.
  // Above → sampler has stopped → chart line is bounded by the last
  // tick → pill VISIBLE.
  //
  // Empty cache (no data at all) returns false — the chart's own
  // "Collecting data" placeholder handles that signal; the pill
  // would be redundant noise on a host that hasn't loaded data yet.
  isHostChartStale(h) {
    if (!h) {
      return false;
    }
    const key = this.hostHistoryKey ? this.hostHistoryKey(h) : '';
    if (!key) {
      return false;
    }
    const entry = this.hostHistory && this.hostHistory[key];
    const series = (entry && Array.isArray(entry.series)) ? entry.series : [];
    if (series.length < 2) {
      return false;
    }
    const last = series[series.length - 1];
    const newestSec = Number((last && (last.t || last.ts)) || 0);
    if (!newestSec) {
      return false;
    }
    // `hostHistoryNow` is the same 30s-ticked timer the freshness
    // label reads — touching it here makes Alpine re-evaluate the
    // gate on every tick so the pill flips on/off without operator
    // action when the sampler stalls.
    const nowMs = this.hostHistoryNow || Date.now();
    const ageS = Math.max(0, (nowMs / 1000) - newestSec);
    return ageS > 600;
  },
  async resumeHostSampling(h) {
    if (!h || !h.id || h._resumeBusy) {
      return;
    }
    h._resumeBusy = true;
    // Safety timer — mirrors per-provider safety. Even if
    // `await fetch` hangs forever (browser network freeze, broken
    // proxy holding the connection), the button gets re-enabled
    // after 30s. Prevents the stuck-disabled state operators hit
    // when the page came back from a network blip with the busy
    // flag still set.
    const safetyTimer = setTimeout(() => {
      h._resumeBusy = false;
    }, 30000);
    try {
      const r = await fetch('/api/hosts/' + encodeURIComponent(h.id) + '/resume-sampling', {
        method: 'POST',
      });
      if (r.ok) {
        // Optimistic: clear the marker locally so the banner /
        // table icon disappear before the next host-list poll.
        h.sampling_paused = false;
        h.failure_window_started_at = 0;
        h.consecutive_failures = 0;
        h.last_error = '';
        // Mark the post-resume "re-probing, collecting data" window so the
        // row shows the distinct blue resuming badge (not red) until the
        // host recovers or the window elapses — see hostResuming().
        h._resumePending = Date.now();
        this.showToast(this.t('hosts_extra.permanent_fail.resumed_toast', {host: this.hostDisplayName(h) || h.id}), 'success');
        // — whole-host pause supersedes per-provider pause. When
        // the operator clicks Resume on the whole-host banner, also
        // walk the paused-providers set on this host and clear each
        // (parallel via resumeAllProviders). One click clears every
        // pause layer for the host — pre-fix the operator had to click
        // both Resume sampling AND Resume all to fully recover.
        const stillPaused = this.pausedProvidersFor(h);
        if (stillPaused.length > 0) {
          this.resumeAllProviders(h).catch(() => {
          });
        }
        // Refresh the host record to pick up backend's view + any
        // new probe results that landed during the API roundtrip.
        // ``force: true`` busts the 10s provider-state cache so the
        // operator sees the post-resume probe immediately instead
        // of waiting out the TTL.
        if (typeof this.refreshHostRow === 'function') {
          this.refreshHostRow(h.id, {force: true}).catch(() => {
          });
        }
      } else {
        const j = await r.json().catch(() => ({}));
        const detail = j.detail || ('HTTP ' + r.status);
        this.showToast(this.t('hosts_extra.permanent_fail.resume_failed_toast', {host: this.hostDisplayName(h) || h.id, error: detail}), 'error');
      }
    } catch (err) {
      this.showToast(this.t('hosts_extra.permanent_fail.resume_failed_toast', {host: this.hostDisplayName(h) || h.id, error: String(err)}), 'error');
    } finally {
      clearTimeout(safetyTimer);
      h._resumeBusy = false;
    }
  },

  // Tap-driven tooltip state for the chart `?` icons. Holds
  // a `<host_id>:<metric_key>` string when a tooltip is open, null
  // when nothing is showing. Mobile lacks hover so the native :title
  // never fires; this Alpine state powers a click-to-toggle tooltip
  // body that ALSO works on desktop. ESC + click-outside close.
  metricTooltipOpen: null,
  toggleMetricTooltip(h, key) {
    const slot = (h && h.id ? h.id : '') + ':' + key;
    this.metricTooltipOpen = (this.metricTooltipOpen === slot) ? null : slot;
    // After Alpine renders the visible tooltip, smart-place it so a
    // left-column chart card doesn't crop the body off the drawer's
    // start edge. The helper measures and applies an --align-start
    // or --align-center modifier when the default end-anchor would
    // overflow.
    if (this.metricTooltipOpen) {
      this.$nextTick(() => this._adjustMetricTooltipPlacement());
    }
  },
  metricTooltipKey(h, key) {
    return (h && h.id ? h.id : '') + ':' + key;
  },
  _adjustMetricTooltipPlacement() {
    // Find the just-opened tooltip body. Alpine renders x-show via
    // display:none, so the visible one is whichever has computed
    // display !== 'none'.
    const all = document.querySelectorAll('.metric-source-tooltip');
    for (const el of all) {
      // Reset any previous modifier so the measurement reflects the
      // default end-anchored placement, then re-apply if needed.
      el.classList.remove('metric-source-tooltip--align-start');
      el.classList.remove('metric-source-tooltip--align-center');
      if (getComputedStyle(el).display === 'none') {
        continue;
      }
      // Use the host drawer as the clipping reference when present;
      // fall back to the viewport. 8px breathing room on each side.
      const drawer = el.closest('.host-drawer');
      const bounds = drawer ? drawer.getBoundingClientRect() : {left: 0, right: window.innerWidth};
      const PAD = 8;
      const initialRect = el.getBoundingClientRect();
      if (initialRect.left < bounds.left + PAD) {
        // Overflowing the start edge — flip to start-anchored.
        el.classList.add('metric-source-tooltip--align-start');
        const flippedRect = el.getBoundingClientRect();
        if (flippedRect.right > bounds.right - PAD) {
          // Flipped overflow on the opposite side too — centre it as
          // a final fallback (rare; only on very narrow drawers).
          el.classList.remove('metric-source-tooltip--align-start');
          el.classList.add('metric-source-tooltip--align-center');
        }
      }
    }
  },
  // Pick up to 5 evenly-spaced timestamps from the series and format
  // them as HH:MM strings for the X-axis below the chart.
  //
  // — Switched from "evenly-spaced points across the actual
  // sample series" to "evenly-spaced ticks across the drawer's
  // unified [tMin, tMax] window". Pre-fix a chart with 4 sparse
  // samples got 4 axis labels equal to those sample times; a chart
  // with 60 dense samples got 5 labels evenly spaced through them.
  // Two cards next to each other showed different label times for
  // the same horizontal pixel — making "where was my spike" hard to
  // read across providers. Post-fix every chart's axis labels are
  // [tMin, …, tMax] so the same pixel position means the same
  // wall-clock time across every drawer chart.
  // Tick-count resolver for host-drawer charts. Operator-flagged:
  // 6h should show 6 ticks (one per hour), 7d should show 7 ticks
  // (one per day). Pre-fix every chart used a hardcoded `slots=5`
  // regardless of range. The map below pairs each picker range
  // with a tick count that lines up with the unit-time interval:
  //   1h  → 6 ticks (one per ~10 min)
  //   6h  → 6 ticks (one per hour)
  //   24h → 6 ticks (one per 4 hours)
  //   7d  → 7 ticks (one per day)
  // Any future range (or call site that explicitly overrides the
  // default) falls back to the passed value.
  _hostChartTickCount(rangeHours) {
    const r = Number(rangeHours || this.hostHistoryRange) || 1;
    if (r === 1) {
      return 6;
    }
    if (r === 6) {
      return 6;
    }
    if (r === 24) {
      return 6;
    }
    if (r === 168) {
      return 7;
    }
    return 5;
  },
  // "Is this net-series flat at zero?" — used by the Net In / Net
  // Out cards to swap the chart for an actionable hint when Beszel's
  // agent isn't tracking any NIC (the agent needs NICS=<iface> env
  // set before it emits nr/ns numbers). Distinct from "no data yet"
  // because hostHistory[].series IS populated — every point is 0.
  isNetSeriesFlat(systemId, key) {
    const stats = this.hostMetricStats(systemId, key, false);
    return !!(stats && stats.maxRaw === 0);
  },
  memPercentOf(h) {
    if (!h) {
      return 0;
    }
    // Same unification rule as `diskPercentOf` — prefer the
    // backend's recomputed `host_mem_percent` (derived from merged
    // used/total in `_merge_one_host`) so the drawer chart, drawer
    // total-usage label, and the outside host card ALL bind to the
    // same number. Operator-flagged: outside read 7% while inside
    // chart read 6.6-6.7% — same data, two different numbers.
    // Falls back to live computation when the backend value is
    // missing; renders with 1-decimal precision so 6.7% reads as
    // "6.7%" instead of either "7" (round-up) or "6.65812..." (raw).
    if (h.mem_percent !== undefined && h.mem_percent !== null
      && Number.isFinite(Number(h.mem_percent))) {
      return Number(h.mem_percent);
    }
    if (!h.mem_total) {
      return 0;
    }
    return Math.round((h.mem_used / h.mem_total) * 1000) / 10;
  },
  diskPercentOf(h) {
    if (!h) {
      return 0;
    }
    // Prefer the backend's recomputed `host_disk_percent` (derived
    // from merged used/total in `_merge_one_host`) so the drawer
    // chart, drawer Total-usage label, and the outside host card
    // ALL bind to the same number. Pre-fix the drawer used
    // `Math.round(used/total * 100)` (integer) while the outside
    // used `host_disk_percent` (1 decimal) — same data, different
    // numbers (9.0% vs 8.7%). Fall back to live computation when
    // the backend value is missing.
    if (h.disk_percent !== undefined && h.disk_percent !== null
      && Number.isFinite(Number(h.disk_percent))) {
      return Number(h.disk_percent);
    }
    if (!h.disk_total) {
      return 0;
    }
    return Math.round((h.disk_used / h.disk_total) * 1000) / 10;
  },
  // Worst-axis-wins: returns the lowest sub-score across all valid
  // axes, or null if no axis is computable for this host.
  healthScore(h) {
    const axes = this.healthAxes(h);
    if (!axes.length) {
      return null;
    }
    let worst = 100;
    for (const a of axes) {
      if (a.score != null && a.score < worst) {
        worst = a.score;
      }
    }
    return worst;
  },
  // Returns the single lowest-scoring axis — populates the chip
  // tooltip + the breakdown popover's "Worst axis" callout.
  healthWorstAxis(h) {
    const axes = this.healthAxes(h);
    if (!axes.length) {
      return null;
    }
    let worst = null;
    for (const a of axes) {
      if (a.score == null) {
        continue;
      }
      if (worst == null || a.score < worst.score) {
        worst = a;
      }
    }
    return worst;
  },
  // Threshold tier for the chip background colour. 80+ green,
  // 50-79 amber, <50 red. Aligns with operator's "anything <80
  // gets attention" mental model — anything green is healthy,
  // amber is "look later", red is "look now".
  healthChipClass(score) {
    if (score == null) {
      return '';
    }
    if (score < 50) {
      return 'health-chip-bad';
    }
    if (score < 80) {
      return 'health-chip-warn';
    }
    return 'health-chip-ok';
  },
  // Toggle the breakdown popover open at host-drawer scope. Click
  // the chip in the drawer header → flips the panel; click again
  // (or close drawer) closes. State stays on the app() instance
  // because the popover lives inside the drawer template tree.
  toggleHealthPopover() {
    this.healthPopoverOpen = !this.healthPopoverOpen;
  },
  // ===== HOST TIMELINE =============================================
  // Triage view inside the drawer aggregating ops, notifications,
  // provider auto-pause + recovery markers per host. Backed by
  // GET /api/hosts/{id}/timeline?hours=N. Cached per-host with
  // a 30s TTL (faster invalidation when the operator clicks
  // refresh or changes the range). Reads + writes
  // `this.hostTimeline[hid]` and `this.hostTimelineRange[hid]`.
  toggleHostTimeline(hostId) {
    const id = (hostId || '').toString();
    if (!id) {
      return;
    }
    const wasOpen = !!this.timelineExpanded[id];
    this.timelineExpanded[id] = !wasOpen;
    // First open → kick off the fetch. Subsequent opens use the
    // cache unless it's stale.
    if (!wasOpen) {
      const cache = this.hostTimeline[id];
      const now = Date.now();
      const stale = !cache
        || !cache.loadedAt
        || (now - cache.loadedAt) > 30000;
      if (stale) {
        // Fire-and-forget — the panel uses a loading-state skeleton bound to
        // the cache shape, so the operator sees a spinner while the fetch lands.
        void this.loadHostTimeline(id, false);
      }
      // Triage panel rides the same expand state — fetch it
      // alongside the timeline so the operator doesn't see a
      // half-rendered drawer when the timeline lands first.
      const tcache = this.hostTriage[id];
      const tstale = !tcache
        || !tcache.loadedAt
        || (now - tcache.loadedAt) > 30000;
      if (tstale) {
        void this.loadHostTriage(id);
      }
    }
  },
  isTriageGroupExpanded(hostId, groupIdx) {
    const map = this.triageExpanded[hostId];
    return !!(map && map[groupIdx]);
  },
  toggleTriageGroup(hostId, groupIdx) {
    const id = (hostId || '').toString();
    if (!id) {
      return;
    }
    if (!this.triageExpanded[id]) {
      this.triageExpanded[id] = {};
    }
    this.triageExpanded[id][groupIdx] = !this.triageExpanded[id][groupIdx];
  },
  triagePatternLabel(pattern) {
    const p = (pattern || 'other').toString();
    const key = 'host_drawer.triage.pattern.' + p.replace(/-/g, '_');
    const tr = this.t(key);
    if (tr && tr !== key) {
      return tr;
    }
    // Forward-compat: humanise the enum so a future pattern shows
    // up readable until the i18n key catches up.
    return p.replace(/-/g, ' ').replace(/^\w/, c => c.toUpperCase());
  },
  triagePatternChipClass(pattern) {
    switch ((pattern || '').toString()) {
      case 'auth':
        return 'pill-error';
      case 'tls':
        return 'pill-error';
      case 'timeout':
        return 'pill-warning';
      case 'refused':
        return 'pill-error';
      case 'dns':
        return 'pill-warning';
      case 'network':
        return 'pill-warning';
      case 'server-error':
        return 'pill-error';
      case 'rate-limit':
        return 'pill-warning';
      case 'not-found':
        return 'pill-info';
      case 'parse':
        return 'pill-info';
      default:
        return 'pill-muted';
    }
  },
  triageAvgRecoveryLabel(seconds) {
    const n = Number(seconds);
    if (!Number.isFinite(n) || n <= 0) {
      return '—';
    }
    if (n < 60) {
      return Math.round(n) + 's';
    }
    if (n < 3600) {
      return Math.round(n / 60) + 'm';
    }
    if (n < 86400) {
      return Math.round(n / 3600) + 'h';
    }
    return Math.round(n / 86400) + 'd';
  },
  // ===== HOSTS BULK SELECTION ======================================
  // Selection helpers. The Hosts main view's row checkbox stops
  // propagation so click on the row body still opens the drawer
  // but click on the checkbox only toggles selection.
  isHostSelected(hostId) {
    return this.selectedHosts.has((hostId || '').toString());
  },
  toggleHostSelection(hostId) {
    const id = (hostId || '').toString();
    if (!id) {
      return;
    }
    if (this.selectedHosts.has(id)) {
      this.selectedHosts.delete(id);
    } else {
      this.selectedHosts.add(id);
    }
    // Re-assign so Alpine sees the mutation (Set mutations don't
    // trigger reactivity by themselves).
    this.selectedHosts = new Set(this.selectedHosts);
  },
  clearHostSelection() {
    this.selectedHosts = new Set();
  },
  selectedHostCount() {
    return this.selectedHosts.size;
  },
  selectedHostsArray() {
    return Array.from(this.selectedHosts);
  },
  // ===== HOSTS BULK ACTIONS ========================================
  // Each action POSTs to a `/api/hosts/bulk/<action>` endpoint and
  // surfaces the partial-success response via the existing toast
  // helpers. Selection is preserved on success so the operator can
  // chain actions; cleared on operator click of "Clear".
  // Step-up reauth — prompts the operator for their local password,
  // POSTs to /api/admin/reauth, returns the short-lived token. SSO
  // users (no local password) get a `OG_REAUTH_NO_LOCAL_PASSWORD`
  // response; the caller falls back to a typed-count confirm in
  // that case. Cancel returns null.
  async _mintReauthToken() {
    try {
      const result = await Swal.fire({
        title: this.t('reauth.title') || 'Confirm with your password',
        text: this.t('reauth.text')
          || 'This action affects multiple hosts. Re-enter your password to proceed.',
        input: 'password',
        inputAttributes: {
          autocapitalize: 'off',
          autocomplete: 'current-password',
        },
        inputPlaceholder: this.t('reauth.placeholder') || 'Password',
        showCancelButton: true,
        confirmButtonText: this.t('reauth.confirm') || 'Confirm',
        cancelButtonText: this.t('actions.cancel') || 'Cancel',
      });
      if (!result.isConfirmed) {
        return null;
      }
      const pw = (result.value || '').trim();
      if (!pw) {
        return null;
      }
      const r = await fetch('/api/admin/reauth', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify({password: pw}),
      });
      const j = await r.json().catch(() => ({}));
      if (j && j.error_code === 'OG_REAUTH_NO_LOCAL_PASSWORD') {
        // SSO user — backend bypasses the reauth gate, so an empty
        // string is the agreed signal "skip the header".
        return '';
      }
      if (!r.ok || !j.ok) {
        this.showToast(this.t('reauth.failed') || 'Re-authentication failed.', 'error');
        return null;
      }
      return String(j.token || '');
    } catch {
      return null;
    }
  },
  async bulkPauseHosts(opts) {
    if (this.selectedHostCount() === 0) {
      return;
    }
    const skipConfirm = !!(opts && opts.skipConfirm);
    // SweetAlert confirm — destructive (sampler will skip these
    // hosts until manually resumed). Body shows the actual host
    // names so the operator can verify the selection before
    // committing — for >10 hosts the list is truncated to the first
    // 10 + "...and N more" so a 200-host pause confirm doesn't fill
    // the entire screen with hostnames. The AI sidebar inline-
    // confirm path passes skipConfirm=true so the SwAl is bypassed
    // (the operator approved inline; a second popup defeats the
    // no-popup contract).
    if (!skipConfirm) {
      const ids = this.selectedHostsArray();
      const sample = ids.slice(0, 10);
      const more = ids.length - sample.length;
      const sampleHtml = sample.map(id => '<code>' + this._logEscape(id) + '</code>').join(', ');
      const moreHtml = more > 0
        ? ' ' + (this.t('hosts_extra.bulk.pause_confirm_more', {more}) || ('… and ' + more + ' more'))
        : '';
      try {
        const result = await Swal.fire({
          title: this.t('hosts_extra.bulk.pause_confirm_title') || 'Pause sampling?',
          html: (this.t('hosts_extra.bulk.pause_confirm_body', {count: this.selectedHostCount()})
              || ('Pause sampling on ' + this.selectedHostCount() + ' host(s)?'))
            + '<br><br><div class="fs-xs text-[var(--text-dim)] mono break-words">' + sampleHtml + moreHtml + '</div>',
          icon: 'warning',
          showCancelButton: true,
          confirmButtonText: this.t('hosts_extra.bulk.pause_confirm_ok') || 'Pause',
          cancelButtonText: this.t('actions.cancel') || 'Cancel',
        });
        if (!result.isConfirmed) {
          return;
        }
      } catch {
        return;
      }
    }
    // Step-up reauth — bulk pause is the most destructive bulk
    // action (sampler stops probing every selected host until
    // manually resumed), so the backend gates it behind a short-
    // lived reauth token. Mint one now via `/api/admin/reauth`.
    // Cancel / wrong password / network error = abort silently
    // (the SweetAlert toast already explained the failure).
    const reauthToken = await this._mintReauthToken();
    if (reauthToken === null) {
      return;
    }
    await this._hostsBulkPost(
      'pause', null, 'hosts_extra.bulk.pause_success',
      {reauthToken},
    );
  },
  async bulkResumeHosts(opts) {
    // Resume is non-destructive (re-enables sampler probes) — no
    // inner SwAl to bypass — but accept opts.skipConfirm for API
    // symmetry with bulkPauseHosts. Currently a no-op.
    void opts;
    if (this.selectedHostCount() === 0) {
      return;
    }
    await this._hostsBulkPost('resume', null, 'hosts_extra.bulk.resume_success');
  },
  openBulkSnmpVendorsModal() {
    if (this.selectedHostCount() === 0) {
      return;
    }
    this.bulkSnmpVendorsModal = {open: true, vendors: [], mode: 'set'};
  },
  closeBulkSnmpVendorsModal() {
    this.bulkSnmpVendorsModal = {open: false, vendors: [], mode: 'set'};
  },
  toggleBulkVendor(v) {
    const cur = this.bulkSnmpVendorsModal.vendors || [];
    const idx = cur.indexOf(v);
    if (idx >= 0) {
      cur.splice(idx, 1);
    } else {
      cur.push(v);
    }
    this.bulkSnmpVendorsModal = {...this.bulkSnmpVendorsModal, vendors: [...cur]};
  },
  async submitBulkSnmpVendors() {
    const m = this.bulkSnmpVendorsModal || {};
    await this._hostsBulkPost(
      'snmp_vendors',
      {vendors: m.vendors || [], mode: m.mode || 'set'},
      'hosts_extra.bulk.snmp_vendors_success',
    );
    this.closeBulkSnmpVendorsModal();
  },
  openBulkSnmpTunablesModal() {
    if (this.selectedHostCount() === 0) {
      return;
    }
    this.bulkSnmpTunablesModal = {
      open: true,
      walk_concurrency: '',
      wall_clock_budget: '',
      clear: false,
    };
  },
  closeBulkSnmpTunablesModal() {
    this.bulkSnmpTunablesModal = {
      open: false,
      walk_concurrency: '',
      wall_clock_budget: '',
      clear: false,
    };
  },
  async submitBulkSnmpTunables() {
    const m = this.bulkSnmpTunablesModal || {};
    const payload = {clear: !!m.clear};
    if (!m.clear) {
      const wc = parseInt(m.walk_concurrency, 10);
      if (Number.isFinite(wc)) {
        payload.walk_concurrency = wc;
      }
      const wcb = parseInt(m.wall_clock_budget, 10);
      if (Number.isFinite(wcb)) {
        payload.wall_clock_budget = wcb;
      }
      if (payload.walk_concurrency == null && payload.wall_clock_budget == null) {
        this.showToast(
          this.t('hosts_extra.bulk.snmp_tunables_empty')
          || 'Set at least one tunable or enable Clear',
          'warning',
        );
        return;
      }
    }
    await this._hostsBulkPost(
      'snmp_tunables',
      payload,
      'hosts_extra.bulk.snmp_tunables_success',
    );
    this.closeBulkSnmpTunablesModal();
  },
  mountFillPercent(m) {
    if (!m) {
      return 0;
    }
    const dp = Number(m.dp);
    if (Number.isFinite(dp) && dp > 0) {
      return Math.min(100, Math.max(0, dp));
    }
    const size = Number(m.d) || 0;
    const used = Number(m.du) || 0;
    if (size <= 0) {
      return 0;
    }
    return Math.min(100, Math.max(0, (used / size) * 100));
  },
  statusDotColor(status) {
    if (status === 'up') {
      return 'var(--success)';
    }
    if (status === 'down' || status === 'unreachable') {
      return 'var(--danger)';
    }
    if (status === 'paused') {
      return 'var(--warning)';
    }
    // Grey dots — no signal (yet) worth alerting on:
    // 'loading'      — skeleton state, probe hasn't returned
    // 'unconfigured' — curated row has NO provider fields set,
    //                  so there's literally nothing to probe
    if (status === 'loading' || status === 'unconfigured') {
      return 'var(--text-faint)';
    }
    // 'unknown' — providers ARE mapped but none returned data.
    // Red because this IS a real failure to reach the host.
    if (status === 'unknown') {
      return 'var(--danger)';
    }
    return 'var(--text-faint)';
  },

  // True while a host is in the post-Resume "re-probing, collecting
  // data" window — distinct from the paused-in-error (red) state so the
  // operator can tell, with the drawer CLOSED, "I just resumed this and
  // it's pending fresh probe data" apart from "this is still paused /
  // down". `resumeHostSampling` / `resumeProvider` stamp `_resumePending`
  // (epoch ms) at resume time; the flag is meaningful only until the host
  // recovers (status === 'up' → cleared signal) or the window elapses
  // (the sampler has had time to re-probe + either recover or re-pause).
  // Window is generous enough for a couple of probe cycles but bounded so
  // a host that never recovers falls back to its real red state. The
  // badge re-evaluates on every reactive flush (15s Hosts poll + SSE
  // failure-state events), so it clears within one poll cadence of the
  // window elapsing — no dedicated ticker needed for a 2-minute badge.
  hostResuming(h) {
    if (!h || !h._resumePending) {
      return false;
    }
    if (h.status === 'up') {
      return false;
    }
    return (Date.now() - h._resumePending) < 120000;
  },

  // Stacks/Services row status indicator — drives the small dot
  // before the row icon. Returns one of `is-up` (green, all healthy),
  // `is-degraded` (amber, has updates pending OR partial degraded),
  // `is-down` (red, any item offline / errored), or `is-unknown`
  // (grey, nothing actionable). The corresponding spinner state is
  // gated on `statsLoaded` — until the first /api/stats response
  // lands, the row renders a spinner instead of a dot so operators
  // see the "still fetching data in background" affordance the user
  // requested. Once stats are in, the dot reflects the rolled-up
  // state of the stack's items.
  stackStatusDotClass(stack) {
    if (!stack) {
      return 'is-unknown';
    }
    if ((stack.offline || 0) > 0) {
      return 'is-down';
    }
    if ((stack.errors || 0) > 0) {
      return 'is-down';
    }
    if ((stack.degraded || 0) > 0 || (stack.updates || 0) > 0) {
      return 'is-degraded';
    }
    if ((stack.unknowns || 0) > 0) {
      return 'is-unknown';
    }
    if ((stack.uptodate || 0) > 0 || (stack.total || 0) > 0) {
      return 'is-up';
    }
    return 'is-unknown';
  },
  // Per-item dot — used by the Services view's leading column.
  // Mirrors the stack helper's tiers off item.status / item.health
  // so the same colour family lights up across both views.
  itemStatusDotClass(item) {
    if (!item) {
      return 'is-unknown';
    }
    const status = String(item.status || '').toLowerCase();
    const health = String(item.health || '').toLowerCase();
    if (status === 'error' || health === 'offline') {
      return 'is-down';
    }
    if (status === 'update' || health === 'degraded') {
      return 'is-degraded';
    }
    if (status === 'up-to-date' && health === 'healthy') {
      return 'is-up';
    }
    if (status === 'unknown' || !status) {
      return 'is-unknown';
    }
    return 'is-unknown';
  },

  // hover-title for the host status dot. Surfaces the probe
  // wall-clock so operators can tell whether an `unknown` status
  // came from a fast 5xx or a slow 30s hang. Backend stamps
  // `_probe_elapsed_ms` on every `/api/hosts/one/{id}` response;
  // missing on the legacy `/api/hosts` path (returns empty string).
  hostProbeTitle(h) {
    if (!h || typeof h._probe_elapsed_ms !== 'number') {
      return '';
    }
    const ms = h._probe_elapsed_ms;
    const status = h.status || '';
    const human = ms < 1000
      ? `${ms} ms`
      : `${(ms / 1000).toFixed(1)} s`;
    return this.t('hosts_extra.probe_title', {elapsed: human, status}) || `Probe took ${human}`;
  },

  async saveNodeBeszelMapping() {
    if (!this.drawerNode) {
      return;
    }
    const name = this.drawerNode.name;
    const val = (this.drawerNode.aliasInput || '').trim();
    // Merge into the existing map: blank = delete entry, otherwise set.
    const map = {...(this.settings.beszel_aliases || {})};
    if (val) {
      map[name] = val;
    } else {
      delete map[name];
    }
    this.drawerNodeSaving = true;
    try {
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({beszel_aliases: map}),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(d, r.status));
      }
      this.settings.beszel_aliases = map;
      this.showToast(
        val
          ? `Beszel mapping saved: ${name} → ${val}`
          : `Beszel mapping cleared for ${name}`,
        'success',
      );
      this.drawerNode = null;
      // Force the next gather to re-read the alias map and re-match
      // Beszel systems. Without force=true the cached items stay
      // attached to the old alias resolution until CACHE_TTL lapses.
      await this.refresh(true);
    } catch (e) {
      this.showToast(`Save failed: ${e.message}`, 'error');
    } finally {
      this.drawerNodeSaving = false;
    }
  },
  parseEvents(j) {
    try {
      return JSON.parse(j || '[]');
    } catch {
      // Malformed JSON from a legacy or partially-written history row — render as empty.
      return [];
    }
  },
  // formatTime + formatTimeShort are kept for backwards compatibility
  // with any template bindings — both now delegate to the unified
  // fmt* helpers so every date in the UI renders in dd/mm/yyyy format.
  formatTime(ts) {
    return this.fmtDate(ts);
  },
  formatTimeShort(ts) {
    if (!ts) {
      return '—';
    }
    const d = new Date(ts * 1000);
    if (isNaN(d.getTime())) {
      return '—';
    }
    const pad = n => String(n).padStart(2, '0');
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  },
  // Parse a user-format date string (matching `_userDateOnlyFormat`)
  // back to an ISO `YYYY-MM-DD` string for the history-filter state.
  // Returns null when the input doesn't match the expected token shape;
  // empty input returns the empty string (clears the filter). Tokens
  // supported: yyyy / yy / MMMM / MMM / MM / M / dd / d.
  _parseUserDate(text) {
    const raw = (text || '').toString().trim();
    if (!raw) {
      return '';
    }
    const fmt = this._userDateOnlyFormat();
    // Parser-symmetry literal arrays — must match the renderer's
    // OUTPUT so a date typed by the operator round-trips back to
    // YYYY-MM-DD. The renderer (_applyDateTimeFormat) is Intl-locale-
    // aware now; making this parser also Intl-aware is the proper
    // follow-up (build the arrays via the same module-scope cache so
    // both directions see the same locale names). Until then, marker
    // exempts these from the audit.
    const monthsLong = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']; // audit: i18n-fallback
    const monthsShort = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']; // audit: i18n-fallback
    // Build a regex from the format string. Order matters — match
    // longer tokens first so `MM` doesn't get split into two `M`s.
    const tokenOrder = ['yyyy', 'yy', 'MMMM', 'MMM', 'MM', 'M', 'dd', 'd'];
    // `(?<name>...)` is ES2018 named-capturing-group regex syntax. PyCharm's
    // HTML language injector sometimes mis-identifies the `<name>` token as
    // markup and flags "Unknown html tag y/m/d/mn"; the syntax is valid JS.
    // noinspection HtmlUnknownTag
    const tokenPatterns = {
      yyyy: '(?<y>\\d{4})',
      yy: '(?<y>\\d{2})',
      MMMM: '(?<mn>[A-Za-z]+)',
      MMM: '(?<mn>[A-Za-z]{3})',
      MM: '(?<m>\\d{2})',
      M: '(?<m>\\d{1,2})',
      dd: '(?<d>\\d{2})',
      d: '(?<d>\\d{1,2})',
    };
    // Escape non-token characters, then replace tokens with capture groups.
    // Walk the format left-to-right matching the longest token at each
    // position to avoid `M` consuming the start of `MMMM`.
    let pattern = '';
    let i = 0;
    while (i < fmt.length) {
      let matched = false;
      for (const tk of tokenOrder) {
        if (fmt.slice(i, i + tk.length) === tk) {
          pattern += tokenPatterns[tk];
          i += tk.length;
          matched = true;
          break;
        }
      }
      if (!matched) {
        pattern += fmt[i].replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        i += 1;
      }
    }
    let m;
    try {
      m = new RegExp('^' + pattern + '$').exec(raw);
    } catch {
      // Malformed user-supplied regex — treat as no-match.
      return null;
    }
    if (!m) {
      return null;
    }
    const g = m.groups || {};
    let y = parseInt(g.y || '', 10);
    let mo = parseInt(g.m || '', 10);
    const day = parseInt(g.d || '', 10);
    if (g.mn) {
      const lower = g.mn.toLowerCase();
      const li = monthsLong.findIndex(x => x.toLowerCase() === lower);
      const si = monthsShort.findIndex(x => x.toLowerCase() === lower);
      mo = (li >= 0 ? li + 1 : (si >= 0 ? si + 1 : NaN));
    }
    if (!Number.isFinite(y) || !Number.isFinite(mo) || !Number.isFinite(day)) {
      return null;
    }
    if (y < 100) {
      y += 2000;
    }  // yy → 20yy
    if (mo < 1 || mo > 12 || day < 1 || day > 31) {
      return null;
    }
    return `${y.toString().padStart(4, '0')}-${mo.toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`;
  },
  // History-filter `@change` glue. Parses the operator-typed text via
  // `_parseUserDate`, writes the ISO string into `historyFilters.<key>`,
  // and triggers the existing `historyApplyFilter` debounce. Empty
  // input clears the filter; malformed input keeps the field's old
  // ISO value (so the filter doesn't silently drop on a typo) but the
  // text input gets re-rendered with the canonical format.
  setHistoryDateFromText(which, text) {
    const parsed = this._parseUserDate(text);
    if (parsed === null) {
      // Malformed — leave underlying ISO untouched. The render will
      // re-display the existing parsed value so the input snaps back.
      return;
    }
    if (!this.historyFilters) {
      return;
    }
    const key = (which === 'from') ? 'fromDate' : 'toDate';
    this.historyFilters[key] = parsed;
    if (this.historyApplyFilter) {
      this.historyApplyFilter();
    }
  },
  copy(text) {
    navigator.clipboard?.writeText(text);
    this.showToast(this.t('toasts.copied'));
  },
  async confirmDialog({title, html, icon = 'warning', confirmText, confirmColor, focusConfirm = false, imageUrl, imageWidth, imageAlt}) {
    // No hex fallbacks — every token below is declared in BOTH :root
    // blocks. If `_cssVar` returns "" something is genuinely broken
    // at the token level and we want it to surface visibly rather
    // than be silently papered over by a literal that diverges from
    // the rest of the theme. Per the project conventions's "no fallback literals"
    // rule (extended to JS-side reads).
    //
    // `focusCancel` defaults to true — the safe-by-default behaviour
    // for "are you sure?" dialogs surfaced by inadvertent clicks
    // (Enter confirms the cancel, no destructive action fires).
    // Call sites where the operator has ALREADY typed the intent
    // explicitly (Cmd-K palette + AI-proposed actions, where the
    // user just pressed Enter on a clearly-named action) pass
    // `focusConfirm: true` to flip the focus — Enter on the
    // confirm dialog now activates Confirm, matching the
    // operator's typing rhythm. Defaults preserve every other
    // call site's existing safety.
    // When an imageUrl is supplied (e.g. the stack's brand logo on the
    // update-stack confirm), SweetAlert shows the image header INSTEAD of the
    // `icon` glyph — so we drop the icon to avoid stacking both. Falls back to
    // the icon when no image is given.
    const r = await Swal.fire({
      title, html,
      ...(imageUrl
        ? {imageUrl, imageWidth: imageWidth || 64, imageHeight: imageWidth || 64, imageAlt: imageAlt || ''}
        : {icon}),
      showCancelButton: true,
      confirmButtonText: confirmText || this.t('actions.confirm'),
      cancelButtonText: this.t('actions.cancel'),
      reverseButtons: true,
      focusCancel: !focusConfirm,
      focusConfirm: !!focusConfirm,
      background: this._cssVar('--surface'),
      color: this._cssVar('--text'),
      confirmButtonColor: confirmColor || this._cssVar('--warning'),
      cancelButtonColor: this._cssVar('--btn-cancel-bg'),
    });
    return r.isConfirmed;
  },
  // Toast lifecycle. The 4 s auto-dismiss is paused while the
  // operator's pointer is over the toast OR a child element holds
  // focus — common case is "I want to copy the error text for
  // debugging but the toast disappears before I can select it".
  // Hovering / focusing arms `_toastHold = true`; the auto-dismiss
  // timer drains its remaining budget into a paused state and
  // resumes on mouseleave / blur. Clicking the close × dismisses
  // immediately. Default duration bumped from 4 s to 6 s and
  // error toasts get 10 s — operators triage errors more often
  // than success confirmations and need extra time to read +
  // decide whether to copy.
  showToast(msg, type = 'success') {
    this.toast = msg;
    this.toastType = type;
    this._toastHold = false;
    this._toastDuration = (type === 'error') ? 10000 : 6000;
    this._toastDeadline = Date.now() + this._toastDuration;
    clearTimeout(this._tt);
    this._tt = setTimeout(() => this._dismissToast(), this._toastDuration);
  },
  _dismissToast() {
    this.toast = '';
    clearTimeout(this._tt);
    this._tt = null;
    this._toastHold = false;
    this._toastDeadline = 0;
  },
  // Mouse / focus enters the toast — pause the auto-dismiss
  // timer. The remaining duration is preserved so when the
  // operator's mouse leaves / focus moves away, the timer
  // resumes from where it left off rather than restarting.
  _holdToast() {
    if (!this.toast) {
      return;
    }
    this._toastHold = true;
    // Capture how much time was left when the hold began so
    // resumeToast can re-arm with the same budget.
    this._toastRemaining = Math.max(500, (this._toastDeadline || 0) - Date.now());
    clearTimeout(this._tt);
    this._tt = null;
  },
  _resumeToast() {
    if (!this.toast) {
      return;
    }
    if (!this._toastHold) {
      return;
    }
    this._toastHold = false;
    const left = Math.max(1500, this._toastRemaining || this._toastDuration || 6000);
    this._toastDeadline = Date.now() + left;
    clearTimeout(this._tt);
    this._tt = setTimeout(() => this._dismissToast(), left);
  },
  // Copy the toast text to the clipboard so the operator can
  // paste into a bug report / chat / search box. Falls back to
  // a manual selection-copy when the Clipboard API isn't
  // available (file:// origins, older WebViews).
  async copyToastText() {
    const text = String(this.toast || '');
    if (!text) {
      return;
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try {
          document.execCommand('copy');
        } finally {
          ta.remove();
        }
      }
      // Brief affordance — flip the body to "Copied!" then back.
      const original = this.toast;
      this.toast = (this.t('toasts.copied') || 'Copied to clipboard.') + ' ' + original;
      clearTimeout(this._tt);
      this._tt = setTimeout(() => {
        if (this.toast.endsWith(original)) {
          this.toast = original;
        }
        this._tt = setTimeout(() => this._dismissToast(),
          Math.max(2500, this._toastRemaining || 4000));
      }, 1200);
    } catch { /* best-effort */
    }
  },

  // Error formatter — prefers the structured `error_code` + i18n
  // lookup (`errors.OG####`) over the backend's raw `error` string
  // when the backend returned a code. Falls back to the English
  // `error` text when no code is present or translation is missing.
  // See logic/errors.py for the catalogue.
  formatError(resp, fallback) {
    if (resp && resp.error_code) {
      const key = 'errors.' + String(resp.error_code);
      const localized = this.t(key, resp.error_params || {});
      // window.I18N.load falls back to English text when the key is
      // missing; the English text IS the DEFAULT_MESSAGES entry. If
      // the backend sent a more specific override_message (e.g.
      // upstream's `details` field), prefer that so operators see
      // the concrete failure, not the generic catalog text.
      if (resp.error && resp.error !== localized && !key.startsWith('errors.undefined')) {
        return resp.error;
      }
      if (localized && localized !== key) {
        return localized;
      }
    }
    return (resp && resp.error) || fallback || '';
  },

  // When a stack carries EXACTLY ONE item with `status==='update'`,
  // return that item's image so the update-popup can surface release
  // notes for it. Multi-service stacks return '' so the caller skips
  // the fetch — "what's new for stack X" with N images doesn't have
  // a single answer and the popup would mislead.
  _stackSingleUpdateImage(stack) {
    if (!stack) {
      return '';
    }
    const items = (this.items || []).filter(it =>
      it && it.stack_id === stack.stack_id && it.status === 'update' && it.image
    );
    if (items.length !== 1) {
      return '';
    }
    return items[0].image || '';
  },
  // ID anchor for the async release-notes block. The popup HTML
  // opens with a placeholder bearing this id; once the fetch
  // resolves, we replace the element's outerHTML with the real
  // notes block via `_replaceReleaseNotesAsync`. ID is stable so the
  // DOM lookup is cheap and unambiguous — SweetAlert2 renders one
  // popup at a time so the single-instance assumption holds.
  _RELEASE_NOTES_ASYNC_ID: 'omnigrid-release-notes-async',
  // GitHub-flavoured release notes carry many URL variants — commit
  // hashes, PR refs, user mentions, doc links, heading anchors —
  // which read as noise inside the preformatted view. Strategy: keep
  // the link TEXT, drop the URL. Pre-fix only commit-hash links were
  // stripped; netdata-style release notes (`[v2.10.2](https://...)`,
  // `[#22232](https://...)`, `[@ktsaou](https://...)`, `[Netdata
  // Learn](https://...)`) survived as `[text](url)` literals in the
  // rendered `<pre>` block.
  _scrubReleaseNotesBody(body) {
    if (!body) {
      return '';
    }
    let out = String(body);
    // Empty HTML heading anchors GitHub adds for permalinks: `<a id="x"></a>`
    out = out.replace(/<a\s+id=["'][^"']*["']\s*><\/a>/gi, '');
    // HTML <a href="..."> with inner text → keep text only.
    // Named capture group (?<txt>...) is ES2018 — JSHint's E016 predates it. // jshint ignore:line
    // noinspection HtmlUnknownTag
    out = out.replace(/<a\s+href=["'][^"']*["'][^>]*>(?<txt>[\s\S]*?)<\/a>/gi, '$<txt>'); // jshint ignore:line
    // Strip the layout HTML GitHub release bodies wrap content in — images
    // (badge rows like `<p align=center><img ...></p>`), <picture>/<source>,
    // <br>, and <p>/<div> wrappers — BEFORE the escape pass, otherwise they
    // render as literal `<img src=...>` / `<p>` text in the notes. Drop
    // <img> entirely (we don't render remote images inline); unwrap block
    // tags to newlines; strip inline layout tags but keep their text. Leave
    // <samp> alone — `_renderReleaseNotesMd` turns it into a commit chip.
    // Strip each tag pattern to a FIXPOINT (loop until the string stops
    // changing) rather than a single pass: a one-shot replace can let a
    // reconstructed tag reappear (e.g. `<<b>b>` → one pass → `<b>`), which
    // CodeQL's js/incomplete-multi-character-sanitization rule flags. Looping
    // removes the residue. Defence-in-depth only — `_renderReleaseNotesMd`
    // ALSO fully HTML-escapes the result before it reaches innerHTML, so no
    // raw tag is ever executed regardless of what survives here.
    const _strip = (re, repl) => {
      let prev;
      do {
        prev = out;
        out = out.replace(re, repl);
      } while (out !== prev);
    };
    _strip(/<img\b[^>]*>/gi, '');
    _strip(/<\/?(?:picture|source|figure|figcaption)\b[^>]*>/gi, '');
    _strip(/<br\s*\/?>/gi, '\n');
    _strip(/<\/(?:p|div)>/gi, '\n');
    _strip(/<(?:p|div)\b[^>]*>/gi, '');
    _strip(/<\/?(?:span|small|sub|sup|kbd|b|i|em|strong|details|summary|h[1-6])\b[^>]*>/gi, '');
    // Markdown image refs (rare in release notes but worth handling
    // before the generic link rule so we don't keep `![alt]` orphans).
    // `![alt](url)` → `alt`. Named capturing group `(?<alt>...)` over
    // anonymous so `RegExpAnonymousGroup` doesn't fire; `\]` inside the
    // negated class is load-bearing (`[^]]` parses as "any char + literal
    // ]" in ECMAScript — different semantics — so RegExpRedundantEscape
    // is a false positive here).
    // noinspection RegExpRedundantEscape
    out = out.replace(/!\[(?<alt>[^\]]*)\]\(https?:\/\/[^)]+\)/g, '$<alt>'); // jshint ignore:line
    // Markdown links: `[text](url)` → `text`. Empty text → drop the
    // whole thing (e.g. `[](url)` is just a wrapper around a URL).
    // noinspection RegExpRedundantEscape
    out = out.replace(/\[(?<txt>[^\]]*)\]\(https?:\/\/[^)]+\)/g, (m, txt) => { // jshint ignore:line
      return (txt || '').trim() || '';
    });
    // Bare URLs in prose (`https://example.com/x` standalone): drop
    // the URL but leave the surrounding punctuation. Catches things
    // like "see https://...for details".
    out = out.replace(/https?:\/\/[^\s)]+/g, '');
    // Tidy: collapse the spaces a URL strip can leave (e.g. "see  for
    // details") + collapse triple+ newlines + drop trailing whitespace
    // on each line + tidy empty `()` left by bare-URL inside parens.
    out = out.replace(/\(\s*\)/g, '');
    out = out.replace(/[ \t]{2,}/g, ' ');
    out = out.replace(/\n{3,}/g, '\n\n');
    // git-cliff / conventional-commit changelogs append a trailing commit-hash
    // artifact to EVERY entry: `subject (PR-ref) - (f093c69)`. Strip the
    // ` - (<7-40 hex>)` suffix per line — it's pure noise in the rendered view.
    // The hash-in-parens shape (preceded by ` - `, all-hex, at line end) is
    // specific enough to never eat a parenthesised PR number or a real
    // parenthetical aside. Run per-line + alongside the trailing-whitespace
    // trim so a GitHub-native body (no such suffix) is untouched.
    out = out.split('\n')
      .map(l => l.replace(/\s+-\s+\([0-9a-f]{7,40}\)\s*$/i, '').replace(/[ \t]+$/, ''))
      .join('\n');
    return out.trim();
  },
  // Build the static placeholder block that the popup opens with.
  // Shows a centred spinner + "Loading release notes..." copy.
  // Carries the `_RELEASE_NOTES_ASYNC_ID` id so the async fill can
  // find + replace it.
  _releaseNotesPlaceholderHtml() {
    const lbl = this._escapeReleaseHtml(this.t('dialogs.release_notes_label') || "What's new");
    const loading = this._escapeReleaseHtml(this.t('dialogs.release_notes_loading') || 'Loading release notes…');
    return [
      `<div id="${this._RELEASE_NOTES_ASYNC_ID}" class="release-notes-block release-notes-block--loading">`,
      `<div class="release-notes-head">`,
      `<span class="release-notes-label">${lbl}</span>`,
      `<span class="release-notes-spinner" aria-hidden="true"></span>`,
      `<span class="release-notes-loading-text">${loading}</span>`,
      '</div>',
      '</div>',
    ].join('');
  },
  // Render the (already-scrubbed) GitHub-flavoured release-notes body into
  // safe, styled HTML instead of dumping it as an escaped <pre> (which left
  // the ### headings / **bold** / <samp> commit hashes / &nbsp; separators /
  // @mentions / list bullets showing as raw markup). XSS-safe by construction:
  // escape EVERYTHING first, then re-introduce a small whitelist of inline
  // formatting on the escaped text. The body comes from a remote registry, so
  // no raw tag from upstream ever reaches innerHTML — only our own spans do.
  _renderReleaseNotesMd(body) {
    const esc = this._escapeReleaseHtml.bind(this);
    // Inline formatting applied to a RAW line: escape, then re-style.
    const inline = (s) => {
      let e = esc(String(s));
      // GitHub uses literal &nbsp; entities as spacers — after escaping they
      // are &amp;nbsp;; restore to a real non-breaking space.
      e = e.replace(/&amp;nbsp;/g, ' ');
      // <samp>(hash)</samp> (escaped to &lt;samp&gt;…&lt;/samp&gt;) → commit chip.
      e = e.replace(/&lt;samp&gt;([\s\S]*?)&lt;\/samp&gt;/g, '<code class="release-notes-commit">$1</code>');
      // `code` → <code>
      e = e.replace(/`([^`]+)`/g, '<code class="release-notes-code">$1</code>');
      // **bold** → <strong> (run BEFORE the single-* italic rule so it
      // doesn't consume bold markers).
      e = e.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
      // *italic* → <em>. Covers git-cliff's `*(scope)*` entry marker (which
      // otherwise rendered as a literal `*(db)*`) plus ordinary markdown
      // emphasis. Runs after bold so any `**` is already gone; the
      // non-greedy `[^*]+?` won't span across a remaining single `*`.
      e = e.replace(/\*([^*\n]+?)\*/g, '<em>$1</em>');
      // @mention → styled (non-link) span. Leading char kept so we don't
      // match an email-ish "a@b".
      e = e.replace(/(^|[\s(])@([A-Za-z0-9][A-Za-z0-9-]{0,38})/g, '$1<span class="release-notes-mention">@$2</span>');
      return e;
    };
    const lines = String(body || '').split('\n');
    const out = [];
    let inList = false;
    const closeList = () => {
      if (inList) {
        out.push('</ul>');
        inList = false;
      }
    };
    // Blockquote / GitHub-alert accumulator. `> [!WARNING]` (also NOTE / TIP
    // / IMPORTANT / CAUTION) opens a typed callout; subsequent `>` lines are
    // its body. A plain `>` block (no marker) renders as a neutral callout.
    let quoteBuf = [];
    let quoteType = '';
    const ALERT_TYPES = new Set(['warning', 'note', 'tip', 'important', 'caution']);
    const flushQuote = () => {
      if (!quoteBuf.length && !quoteType) {
        return;
      }
      const mod = quoteType ? (' release-notes-alert--' + quoteType) : '';
      let block = `<div class="release-notes-alert${mod}">`;
      if (quoteType) {
        const lbl = esc(this.t('dialogs.release_alert_' + quoteType)
          || (quoteType.charAt(0).toUpperCase() + quoteType.slice(1)));
        block += `<div class="release-notes-alert-label">${lbl}</div>`;
      }
      block += quoteBuf.map((q) => inline(q)).join('<br>');
      block += '</div>';
      out.push(block);
      quoteBuf = [];
      quoteType = '';
    };
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, '');
      // Blockquote / GitHub alert lines.
      const bq = line.match(/^\s*>\s?(.*)$/);
      if (bq) {
        closeList();
        const inner = bq[1];
        const alert = inner.match(/^\[!(\w+)\]\s*$/);
        if (alert && ALERT_TYPES.has(alert[1].toLowerCase())) {
          flushQuote();
          quoteType = alert[1].toLowerCase();
        } else {
          quoteBuf.push(inner);
        }
        continue;
      }
      // Any non-blockquote line ends an open quote block.
      flushQuote();
      // A line that is only &nbsp;/space is blank for layout purposes.
      if (!line.replace(/&nbsp;|\s/g, '').trim()) {
        closeList();
        continue;
      }
      const h = line.match(/^\s*(#{1,6})\s+(.*)$/);
      if (h) {
        closeList();
        const lvl = Math.min(6, h[1].length);
        out.push(`<div class="release-notes-h release-notes-h${lvl}">${inline(h[2])}</div>`);
        continue;
      }
      const li = line.match(/^(\s*)[-*]\s+(.*)$/);
      if (li) {
        if (!inList) {
          out.push('<ul class="release-notes-list">');
          inList = true;
        }
        const nested = li[1].length >= 1 ? ' release-notes-li--nested' : '';
        out.push(`<li class="release-notes-li${nested}">${inline(li[2])}</li>`);
        continue;
      }
      closeList();
      out.push(`<p class="release-notes-p">${inline(line)}</p>`);
    }
    closeList();
    flushQuote();
    return `<div class="release-notes-rendered scrollbar">${out.join('')}</div>`;
  },
  // Render the resolved release-notes block from one /api/registry/
  // release-notes response payload. Returns the final HTML string the
  // async filler injects in place of the placeholder. Empty string
  // when the lookup yielded nothing actionable — caller removes the
  // placeholder entirely in that case.
  _buildReleaseNotesHtml(d) {
    const esc = this._escapeReleaseHtml.bind(this);
    const lbl = esc(this.t('dialogs.release_notes_label') || "What's new");
    if (d && d.ok && d.body) {
      const scrubbed = this._scrubReleaseNotesBody(d.body);
      if (!scrubbed.trim()) {
        return '';
      }
      const linkOut = d.html_url
        ? `<a href="${esc(d.html_url)}" target="_blank" rel="noopener" class="release-notes-link">${esc(this.t('dialogs.release_notes_view_on_source') || 'View on source')}</a>`
        : '';
      // Stash the cleaned text for the Copy button (wired via addEventListener
      // in _replaceReleaseNotesAsync — the popup HTML is injected raw, not
      // Alpine-reactive, so @click wouldn't bind). SwAl shows one popup at a
      // time, so a single field is safe.
      this._lastReleaseNotesText = scrubbed;
      const copyLbl = esc(this.t('dialogs.release_notes_copy') || 'Copy');
      const copyBtn = [
        `<button type="button" class="release-notes-copy" title="${copyLbl}" aria-label="${copyLbl}">`,
        '<svg width="13" height="13" aria-hidden="true"><use href="/img/ui-sprite.svg?v=__APP_VERSION__#icon-copy"/></svg>',
        `<span>${copyLbl}</span>`,
        '</button>',
      ].join('');
      return [
        `<div id="${this._RELEASE_NOTES_ASYNC_ID}" class="release-notes-block">`,
        `<div class="release-notes-head">`,
        `<span class="release-notes-label">${lbl}</span>`,
        `<span class="release-notes-tag mono">${esc(d.tag || '')}</span>`,
        linkOut,
        copyBtn,
        '</div>',
        this._renderReleaseNotesMd(scrubbed),
        '</div>',
      ].join('');
    }
    // No release body — surface the source link only when we have
    // it, so the operator can still investigate. Skip the block
    // entirely on a hard miss (no source label) to avoid clutter.
    if (d && d.source_url) {
      return [
        `<div id="${this._RELEASE_NOTES_ASYNC_ID}" class="release-notes-block release-notes-block--empty">`,
        `<span class="release-notes-label">${lbl}</span>`,
        `<a href="${esc(d.source_url)}" target="_blank" rel="noopener" class="release-notes-link">${esc(d.source_url)}</a>`,
        '</div>',
      ].join('');
    }
    return '';
  },
  // Fire the release-notes fetch + replace the placeholder block in
  // the open SwAl popup's DOM. Fire-and-forget — caller is the
  // synchronous `await confirmDialog(...)` path; the popup is already
  // open by the time this resolves. Defensive: when the operator
  // closes the popup before the fetch lands, `document.getElementById`
  // returns null and the replace is a no-op (no error). When the
  // server returns no body AND no source URL, the placeholder is
  // removed entirely so the popup doesn't carry a dangling spinner.
  async _replaceReleaseNotesAsync(image) {
    if (!image) {
      return;
    }
    try {
      const r = await fetch(`/api/registry/release-notes?image=${encodeURIComponent(image)}`);
      if (!r.ok) {
        // HTTP failure — remove the placeholder so the popup doesn't
        // hang on the spinner indefinitely.
        const el = document.getElementById(this._RELEASE_NOTES_ASYNC_ID);
        if (el) {
          el.remove();
        }
        return;
      }
      const d = await r.json();
      const html = this._buildReleaseNotesHtml(d);
      const el = document.getElementById(this._RELEASE_NOTES_ASYNC_ID);
      if (!el) {
        return;
      }   // popup closed before fetch resolved
      if (!html) {
        el.remove();
        return;
      }
      el.outerHTML = html;
      // Wire the Copy button — the block was injected via outerHTML (raw DOM,
      // not Alpine), so bind the click here. Copies the cleaned notes text
      // through the shared clipboard helper (toast on success/fail).
      const cp = document.querySelector('.release-notes-copy');
      if (cp) {
        cp.addEventListener('click', () => this.copyToClipboard(this._lastReleaseNotesText || ''));
      }
    } catch {
      // Silent — placeholder removed so popup doesn't carry a
      // stuck spinner. Operator still gets the actual update path.
      const el = document.getElementById(this._RELEASE_NOTES_ASYNC_ID);
      if (el) {
        el.remove();
      }
    }
  },
  async updateStack(stack, opts) {
    if (this.isStackBusy(stack)) {
      return;
    }
    const skipConfirm = !!(opts && opts.skipConfirm);
    if (!skipConfirm) {
      // Release notes only fire when the stack has EXACTLY ONE
      // updateable item — multi-service stacks don't have a single
      // "what's new" to surface and the popup would mislead. Pick
      // the lone item's image if it qualifies; else skip the
      // placeholder entirely. Popup opens INSTANTLY either way;
      // async filler replaces the placeholder on resolve.
      const stackImage = this._stackSingleUpdateImage(stack);
      // Blast-radius preview (MVP) — surfaces the
      // services / containers the stack update will touch so the
      // operator sees the full scope BEFORE confirming. Pure SPA
      // composition over the already-cached stack.items — no extra
      // fetch.
      const blastHtml = this._renderStackBlastRadius(stack);
      const html = this.t('dialogs.update_stack_html', {name: stack.name})
        + blastHtml
        + (stackImage ? this._releaseNotesPlaceholderHtml() : '');
      if (stackImage) {
        // Intentionally fire-and-forget per the placeholder pattern documented above —
        // the popup opens with a `…` placeholder; this resolves later and patches the DOM.
        void this._replaceReleaseNotesAsync(stackImage);
      }
      const ok = await this.confirmDialog({
        title: this.t('dialogs.update_stack_title'),
        html: html,
        icon: 'warning', confirmText: this.t('actions.update_stack'),
        focusConfirm: true,
      });
      if (!ok) {
        return;
      }
    }
    if (this.isStackBusy(stack)) {
      return;
    }
    const key = this._busyKey('stack', stack.stack_id);
    this._markBusy(key);
    try {
      const r = await fetch(`/api/update/stack/${stack.stack_id}`, {method: 'POST'});
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      this.showToast(this.t('toasts.queued', {name: stack.name}));
      this.pollOpsNow();
    } catch (e) {
      this._clearBusy(key);
      this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
    }
  },
  // Eligibility gate for the drawer's "Switch to tag…" inline-popover.
  // True iff the item is a container OR stack-managed item with an
  // image. The original gate required tag !== 'latest' (single-target
  // retag); the generalised "switch to ANY tag" feature accepts
  // moves both ways (e.g. :latest → :2 to pin a major-line, OR
  // :2.0.0-dev → :2 to leave a snapshot tag for the moving v2 line),
  // so the only requirements are:
  //   - has an image
  //   - has stack_id (Portainer-managed compose) OR raw_id (container
  //     we can recreate via Docker)
  //   - is a container/stack-managed item, NOT a Swarm service
  //     (services need `docker service update --image --force` —
  //     different flow).
  // Digest-only images (no :tag) are still eligible — the operator
  // can pin a tag where none was set before.
  canRetagToLatest(item) {
    if (!item || !item.image) {
      return false;
    }
    if (!item.stack_id && !item.raw_id) {
      return false;
    }
    // Orphans (Swarm task containers left behind on a previous task
    // replacement) can't be retagged — they're meant to be removed,
    // not rolled forward.
    if (item.type === 'orphan') {
      return false;
    }
    // Swarm services qualify ONLY when they belong to a Portainer-
    // managed stack (stack_id present). The `submitRetagPopover`
    // wiring routes those to the existing `/api/update/stack/{id}/
    // retag-latest` endpoint with `image_repo` set to the service's
    // own image so the compose-file mutation touches just THAT line,
    // not every image in the stack. Services without stack_id (rare
    // — Swarm services deployed outside Portainer) stay ineligible
    // because the backend has no compose file to mutate.
    if (item.type === 'service' && !item.stack_id) {
      return false;
    }
    return true;
  },
  isRetagPopoverOpen(item) {
    if (!item || !this._retagPopoverItemId) {
      return false;
    }
    return this._retagPopoverItemId === (item.raw_id || item.id);
  },
  closeRetagPopover() {
    this._retagPopoverItemId = null;
    this._retagDraft = '';
    this._retagBusy = false;
    this._retagPopoverPos = null;
    if (this._retagScrollOff) {
      this._retagScrollOff();
      this._retagScrollOff = null;
    }
  },
  async restartItem(item, opts) {
    if (!this.isRestartable(item)) {
      return;
    }
    if (this.isRestartBusy(item)) {
      return;
    }
    const skipConfirm = !!(opts && opts.skipConfirm);
    const isService = item.type === 'service';
    if (!skipConfirm) {
      const body = isService
        ? this.t('dialogs.restart_service_html', {name: item.name})
        : this.t('dialogs.restart_container_html', {name: item.name});
      const ok = await this.confirmDialog({
        title: isService ? this.t('dialogs.restart_service_title') : this.t('dialogs.restart_container_title'),
        html: body,
        icon: 'question', confirmText: this.t('actions.restart'), confirmColor: this._cssVar('--primary'),
        focusConfirm: true,
      });
      if (!ok) {
        return;
      }
    }
    if (this.isRestartBusy(item)) {
      return;
    }
    const key = isService ? this._busyKey('svc', item.raw_id) : this._busyKey('ctn', item.raw_id);
    const url = isService
      ? `/api/restart/service/${item.raw_id}`
      : `/api/restart/container/${item.raw_id}`;
    this._markBusy(key);
    try {
      const r = await fetch(url, {method: 'POST'});
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      this.showToast(this.t('toasts.restart_queued', {name: item.name}));
      this.drawerItem = null;
      this.pollOpsNow();
    } catch (e) {
      this._clearBusy(key);
      this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
    }
  },
  // Unhealthy-agent banner's one-click force-restart of the
  // Portainer agent service. Backend auto-discovers the service
  // by image-prefix + name pattern; on ambiguous discovery the op
  // surfaces the candidate list and refuses to auto-pick.
  async restartSwarmAgent() {
    const ok = await this.confirmDialog({
      title: this.t('swarm_agent_banner.confirm_title'),
      text: this.t('swarm_agent_banner.confirm_text'),
      icon: 'warning',
      confirmText: this.t('swarm_agent_banner.restart_button'),
      confirmColor: this._cssVar('--warning'),
      focusConfirm: true,
    });
    if (!ok) {
      return;
    }
    if (this.swarmAgentRestartBusy) {
      return;
    }
    this.swarmAgentRestartBusy = true;
    try {
      const r = await fetch('/api/swarm/restart-agent', {method: 'POST'});
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      this.showToast(this.t('swarm_agent_banner.restart_queued'));
      this.pollOpsNow();
    } catch (e) {
      this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
    } finally {
      // Cleared after a short delay so the spinner stays visible
      // long enough for the operator to see it fired (the actual
      // op runs in the background and surfaces in the ops queue).
      setTimeout(() => {
        this.swarmAgentRestartBusy = false;
      }, 1200);
    }
  },
  async removeContainer(item, opts) {
    if (this.isItemBusy(item)) {
      return;
    }
    const skipConfirm = !!(opts && opts.skipConfirm);
    if (!skipConfirm) {
      const ok = await this.confirmDialog({
        title: this.t('dialogs.remove_container_title'),
        html: this.t('dialogs.remove_container_html', {name: item.name}),
        icon: 'warning', confirmText: this.t('actions.remove'), confirmColor: this._cssVar('--danger'),
        focusConfirm: true,
      });
      if (!ok) {
        return;
      }
    }
    if (this.isItemBusy(item)) {
      return;
    }
    const key = this._busyKey('ctn', item.raw_id);
    this._markBusy(key);
    try {
      const r = await fetch(`/api/remove/container/${item.raw_id}`, {method: 'POST'});
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      this.showToast(this.t('toasts.remove_queued', {name: item.name}));
      this.drawerItem = null;
      this.pollOpsNow();
    } catch (e) {
      this._clearBusy(key);
      this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
    }
  },
};
