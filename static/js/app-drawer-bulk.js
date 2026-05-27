// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,JSUnusedGlobalSymbols,JSUnusedLocalSymbols,ElementNotExported
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName
// noinspection OverlyComplexBooleanExpressionJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression,NegatedIfStatementJS
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSIfStatementsCanBeSimplified,IfStatementSimplifyable,RedundantIfStatementJS
// noinspection HtmlUnknownTag,HtmlEmptyContent,HtmlEmptyTagsRecommendation,InnerHTMLJS,VoidExpressionJS,JSVoidExpression
// noinspection RedundantLocalVariableJS,JSReusedLocalVariable,RegExpRedundantEscape,AnonymousCapturingGroupJS,RegExpAnonymousGroup
// Comprehensive per-inspection suppressions mirror app-ai-admin.js.
// SPA-wide idioms covered: constants on the right of comparisons
// (modern ESLint default); anonymous arrow callbacks; chained map+
// filter; ternaries; magic numbers for unit-conversion constants
// (60/3600/86400 seconds, percentage thresholds 50/85, byte-size
// rates); short uppercase locals (W/H/PAD) for SVG geometry;
// Alpine-called methods PyCharm can't trace through x-on:click;
// `throw new Error(...)` for unified error handling; `<y>/<m>/<d>`
// placeholders inside date-format JS strings that PyCharm's HTML
// parser mistakes for markup.
/* global Alpine, Swal, I18N, t, OG_VERSION */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA drawer debug panel + host timeline + hosts bulk actions
//
// SPLIT FROM `app.js`: that file crossed 14k lines and was carrying
// every Alpine component method in one inline `{ ... }` literal.
// Each extracted chunk becomes an `export default { ... }` module
// merged back into the component via `_mergeKeepDescriptors`. Cross-
// chunk method references (`this.X`) keep working without any
// binding gymnastics because they all merge onto the same target
// object before Alpine instantiation.

export default {
  // ----- Stacks / Services / Nodes drawer debug panel ----------
  // Shared helpers keyed by `kind:id`. `kind` is 'item' (covers
  // services / standalone containers / orphans / stack rollups
  // surfaced via drawerItem) or 'node' (drawerNode). Same fetch +
  // open-toggle + loading-state shape as the host-debug panel, so
  // the markup can reuse the existing `.host-debug-*` CSS family
  // verbatim for visual consistency.
  subjectDebugKey(kind, id) {
    return `${kind}:${id || ''}`;
  },
  async toggleSubjectDebug(kind, id) {
    if (!kind || !id) {
      return;
    }
    const key = this.subjectDebugKey(kind, id);
    const open = !this.subjectsDebugOpen[key];
    this.subjectsDebugOpen = {...this.subjectsDebugOpen, [key]: open};
    if (open && !this.subjectsDebug[key] && !this.subjectsDebugLoading[key]) {
      await this.loadSubjectDebug(kind, id);
    }
  },
  async loadSubjectDebug(kind, id) {
    if (!kind || !id) {
      return;
    }
    const key = this.subjectDebugKey(kind, id);
    this.subjectsDebugLoading = {...this.subjectsDebugLoading, [key]: true};
    try {
      const r = await fetch(
        '/api/debug/subject'
        + '?kind=' + encodeURIComponent(kind)
        + '&id=' + encodeURIComponent(id)
        + '&since_hours=1'
      );
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        this.subjectsDebug = {
          ...this.subjectsDebug,
          [key]: {_error: j.detail || `HTTP ${r.status}`},
        };
        return;
      }
      const d = await r.json();
      this.subjectsDebug = {...this.subjectsDebug, [key]: d};
    } catch (e) {
      this.subjectsDebug = {
        ...this.subjectsDebug,
        [key]: {_error: `Network: ${e.message}`},
      };
    } finally {
      this.subjectsDebugLoading = {
        ...this.subjectsDebugLoading, [key]: false,
      };
    }
  },
  // Jump to the host-drawer debug panel from another surface
  //. Forces the panel OPEN (no toggle, since the operator's
  // intent here is "show me", not "flip"), triggers the lazy load
  // if cold, then scrolls. Idempotent — calling twice on an already-
  // open panel is a no-op except for the re-scroll, which is what
  // you want when the operator clicks the link a second time.
  async jumpToHostDebug(hostId) {
    if (!hostId) {
      return;
    }
    if (!this.hostsDebugOpen[hostId]) {
      this.hostsDebugOpen = {...this.hostsDebugOpen, [hostId]: true};
      if (!this.hostsDebug[hostId] && !this.hostsDebugLoading[hostId]) {
        // Don't await — let the scroll happen now, the panel will
        // populate when the fetch lands.
        this.loadHostDebug(hostId).catch(() => {
        });
      }
    }
    this._scrollHostSectionIntoView(`debug-${hostId}`);
  },
  // Smooth-scrolls the host-drawer's inner scroller so the named
  // section (`data-host-section="<kind>-<host_id>"`) lands near the
  // top. Plain `scrollIntoView({block:'start'})` worked in
  // Chrome but Safari was scrolling the page instead of the drawer
  // — so this helper finds the drawer's scrollable ancestor
  // explicitly and sets `scrollTop` directly. Two rAFs after the
  // x-show flip ensure layout has been painted before we measure.
  _scrollHostSectionIntoView(sectionKey) {
    const sel = `[data-host-section="${sectionKey}"]`;
    this.$nextTick(() => {
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const el = document.querySelector(sel);
        if (!el) {
          return;
        }
        // Walk up to the nearest scrollable ancestor — the
        // host-drawer panel itself in practice.
        let scroller = el.parentElement;
        while (scroller) {
          const cs = window.getComputedStyle(scroller);
          if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll')
            && scroller.scrollHeight > scroller.clientHeight) {
            break;
          }
          scroller = scroller.parentElement;
        }
        if (scroller) {
          const rect = el.getBoundingClientRect();
          const srect = scroller.getBoundingClientRect();
          const target = scroller.scrollTop + (rect.top - srect.top) - 12;
          try {
            scroller.scrollTo({top: target, behavior: 'smooth'});
          } catch {
            // Older browsers don't accept the options-object form; fall back to direct scrollTop.
            scroller.scrollTop = target;
          }
        } else {
          try {
            el.scrollIntoView({behavior: 'smooth', block: 'start'});
          } catch {
            // Older browsers don't accept the options-object form; non-fatal.
          }
        }
      }));
    });
  },
  async loadHostDebug(hostId) {
    if (!hostId) {
      return;
    }
    this.hostsDebugLoading = {...this.hostsDebugLoading, [hostId]: true};
    try {
      // Pass `since_hours` so the backend's `samples_in_window`
      // block matches the chart range picker. Operator selecting
      // "1h" sees how many samples landed in the past hour for
      // each time-series table — the diagnostic for "why is the
      // past-hour chart cut?". Falls through to 1 when the
      // picker hasn't hydrated yet.
      const sinceHours = Math.max(1, Math.min(168, +this.hostHistoryRange || 1));
      const r = await fetch(
        '/api/hosts/debug?id=' + encodeURIComponent(hostId)
        + '&since_hours=' + sinceHours
      );
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        this.hostsDebug = {
          ...this.hostsDebug,
          [hostId]: {_error: j.detail || `HTTP ${r.status}`},
        };
        return;
      }
      const d = await r.json();
      this.hostsDebug = {...this.hostsDebug, [hostId]: d};
    } catch (e) {
      this.hostsDebug = {
        ...this.hostsDebug,
        [hostId]: {_error: `Network: ${e.message}`},
      };
    } finally {
      this.hostsDebugLoading = {...this.hostsDebugLoading, [hostId]: false};
    }
  },
  // Drawer per-unit pane — toggle open/closed. First open lazy-
  // loads the data via `loadHostBeszelServices`. Subsequent
  // opens reuse the cached snapshot; the cache invalidates only
  // when the operator explicitly re-fetches (rare; the data
  // moves slowly compared to chart cadence).
  async toggleHostBeszelServices(hostId) {
    if (!hostId) {
      return;
    }
    const open = !this.hostBeszelServicesOpen[hostId];
    this.hostBeszelServicesOpen = {...this.hostBeszelServicesOpen, [hostId]: open};
    if (open) {
      if (!this.hostsBeszelServices[hostId]
        && !this.hostsBeszelServicesLoading[hostId]) {
        await this.loadHostBeszelServices(hostId);
      }
    }
  },
  // Map systemd ActiveState int → human label. Uses i18n keys with
  // a capitalisation fallback so future Beszel agents that report
  // a state value we haven't translated yet still render readably.
  beszelServiceStateLabel(state) {
    const map = {
      0: 'active', 1: 'reloading', 2: 'inactive',
      3: 'failed', 4: 'activating', 5: 'deactivating',
    };
    const slug = map[+state];
    if (slug) {
      const key = 'host_drawer.beszel_services.state_' + slug;
      const tr = this.t(key);
      if (tr && tr !== key) {
        return tr;
      }
      return slug.charAt(0).toUpperCase() + slug.slice(1);
    }
    return state == null ? '—' : String(state);
  },
  // Pill colour class — failed = red, active = green, inactive +
  // transitional states = muted. Matches the existing pill
  // taxonomy (pill-error / pill-ok / pill-muted) used elsewhere
  // in the drawer.
  beszelServicePillClass(state) {
    const s = +state;
    if (s === 3) {
      return 'pill-error';
    }   // failed
    if (s === 0) {
      return 'pill-ok';
    }      // active
    return 'pill-muted';                // everything else (inactive, transitional)
  },
  // Relative-age formatter for "last_change_ts" — same shape as
  // the samples-in-window panel's age helper. Inputs: seconds
  // (number). Outputs: "30s" / "5m" / "2.3h" / "1.2d".
  relativeAge(seconds) {
    const n = Math.max(0, Math.round(+seconds || 0));
    if (n < 60) {
      return n + 's';
    }
    if (n < 3600) {
      return Math.round(n / 60) + 'm';
    }
    if (n < 86400) {
      return (n / 3600).toFixed(1) + 'h';
    }
    return (n / 86400).toFixed(1) + 'd';
  },
  // "Is this debug payload worth rendering?" — wrapper x-show for
  // each box in the grid. null / undefined → false (hide); empty
  // object / empty array → false; anything else → true.
  hasDebugData(v) {
    if (v === null || v === undefined) {
      return false;
    }
    if (typeof v === 'object') {
      return Array.isArray(v) ? v.length > 0 : Object.keys(v).length > 0;
    }
    return true;
  },
  // Clipboard copy button — pretty-prints the payload and shows a
  // quick toast. Falls back to a prompt() if the Clipboard API is
  // unavailable (old Safari, file:// protocol).
  // aria-label helper for the host-debug-copy buttons — resolves
  // the inner label key (e.g. `debug_panel.labels.counters`) and
  // wraps in the `debug_panel.copy_aria` template ("Copy {label}
  // to clipboard"). Pre-fix the copy buttons were icon-only with
  // a `:title` (hover-only) — screen readers announced "button
  // button button" because the SVG is `aria-hidden="true"` and no
  // accessible name was bound. Per CLAUDE.md "icon-only buttons
  // need aria-label". This helper lets every copy button bind a
  // single one-line aria-label without duplicating the t()
  // composition at every site.
  copyAriaLabel(labelKey) {
    const inner = labelKey ? this.t(labelKey) : this.t('debug_panel.copy_default_label');
    return this.t('debug_panel.copy_aria', {label: inner});
  },
  async copyDebugJson(v, label) {
    const text = this.fmtDebugJson(v);
    if (!text) {
      this.showToast(this.t('toasts_extra.nothing_to_copy'), 'warning');
      return;
    }
    // Resolve the label through i18n. Pre-fix call
    // sites passed English literals like 'Counters' / 'Raw · Pulse'
    // — both the toast AND the prompt() fallback embedded that raw
    // English regardless of locale. Now: call sites pass an i18n
    // KEY (e.g. 'debug_panel.labels.counters') and the helper
    // resolves it. Backwards-compat: if the resolved value matches
    // the raw key (i.e. no translation found), fall back to a
    // generic "debug data" string — also i18n'd. Empty / falsy
    // labels also fall through to that default.
    let resolvedLabel = '';
    if (label) {
      const candidate = this.t(label);
      // `t()` returns the key itself when no translation is found
      // — treat that as "label was a raw string, not an i18n key"
      // and use it verbatim so old callers keep working.
      resolvedLabel = (candidate === label && !label.includes('.'))
        ? label
        : candidate;
    }
    if (!resolvedLabel) {
      resolvedLabel = this.t('debug_panel.copy_default_label');
    }
    try {
      await navigator.clipboard.writeText(text);
      this.showToast(this.t('toasts_extra.copied', {label: resolvedLabel}), 'success');
    } catch {
      // Fallback — let the user copy manually. Wrap the entire
      // English "Copy <label> (Cmd/Ctrl+C):" string in i18n too so
      // non-en operators get a translated prompt header.
      const promptHead = this.t('debug_panel.copy_prompt_fallback', {label: resolvedLabel});
      window.prompt(promptHead, text);
    }
  },
  // Copy ALL debug panes for one host into a single multi-section
  // payload, headed by host_id + ts. Each section is `## <label>`
  // followed by a fenced JSON block, joined by blank lines so the
  // operator can paste a complete bug report into chat / issue
  // tracker without clicking the per-pane copy button N times.
  // Operator-requested: "I copy a lot of panes when debugging
  // issues together — one button to grab them all".
  async copyAllDebug(hostId) {
    const dbg = this.hostsDebug && this.hostsDebug[hostId];
    if (!dbg) {
      this.showToast(this.t('toasts_extra.nothing_to_copy'), 'warning');
      return;
    }
    const sections = [];
    // Header line — host_id + capture timestamp so the recipient
    // can correlate against logs / SSE events.
    const ts = new Date().toISOString();
    sections.push(`# OmniGrid host debug — ${hostId} — ${ts}`);
    // Resolve the host record from the in-memory list so the
    // chart-data bundle can use the same cache-key helpers the
    // drawer chart cards use. Falls through to a synthetic
    // `{id: hostId}` when the host isn't in `this.hosts` (e.g.
    // operator opened debug for a host that's been removed).
    const host = (this.hosts || []).find(h => h && h.id === hostId) || {id: hostId};
    const charts = this.chartDataBundle(host);
    // Sections in the same canonical order the panes render.
    const blocks = [
      ['Active providers', dbg.active_providers],
      ['Counters & state', dbg.counters],
      ['Samples in window', (dbg.counters || {}).samples_in_window],
      ['Failure state', (dbg.counters || {}).failure_state],
      ['Provider pause state', (dbg.counters || {}).provider_pause_state],
      ['Tunables', (dbg.counters || {}).tunables],
      ['Merged host', dbg.merged],
      ['Raw · Pulse', (dbg.providers_raw || {}).pulse],
      ['Raw · Beszel', (dbg.providers_raw || {}).beszel],
      ['Raw · Node-exporter', (dbg.providers_raw || {}).node_exporter],
      ['Raw · Webmin', (dbg.providers_raw || {}).webmin],
      ['Raw · Ping', (dbg.providers_raw || {}).ping],
      ['Raw · SNMP', (dbg.providers_raw || {}).snmp],
      // Chart data — main history, ping, SNMP host / iface / temps.
      // Last so the verbose points blob doesn't push narrow panes
      // off-screen in the paste preview.
      ['Chart data', charts],
    ];
    for (const [label, value] of blocks) {
      if (value === undefined || value === null) {
        continue;
      }
      // Skip empty objects/arrays — keeps the payload tight.
      if (typeof value === 'object'
        && !Array.isArray(value)
        && Object.keys(value).length === 0) {
        continue;
      }
      if (Array.isArray(value) && value.length === 0) {
        continue;
      }
      const body = this.fmtDebugJson(value);
      if (!body) {
        continue;
      }
      sections.push(`## ${label}\n\`\`\`json\n${body}\n\`\`\``);
    }
    const payload = sections.join('\n\n');
    try {
      await navigator.clipboard.writeText(payload);
      this.showToast(this.t('toasts_extra.copied_all_debug', {count: sections.length - 1}), 'success');
    } catch {
      // Clipboard write blocked (non-https / insecure context); prompt() lets the operator copy manually.
      const promptHead = this.t('debug_panel.copy_all_prompt_fallback');
      window.prompt(promptHead, payload);
    }
  },
  // Telemetry = any provider that contributes CPU / Memory / Disk
  // gauges. Ping is reachability + latency only; a ping-only host
  // shouldn't render CPU/Mem/Disk bars (every value would be zero,
  // which looks like "loading" or a broken row). Use this to gate
  // the bars on the host rows (desktop + mobile) and the per-card
  // CPU/Mem/Disk/Net/DiskIO charts in the drawer.
  //
  // Per-metric telemetry gates. Each axis (CPU / Memory / Disk)
  // is gated INDEPENDENTLY so a host that only reports one axis
  // (Cisco SG300 switch via SNMP exposes CPU but no mem/disk;
  // chassis BMCs like Dell iDRAC + APC UPS report ZERO of these
  // axes — only chassis sensors / UPS battery state) shows just
  // the bars it can fill instead of three together (or none).
  // Beszel / Pulse / NE / Webmin always count for all three
  // because they're host-OS agents that emit CPU + Mem + Disk
  // uniformly.
  _hostUnixAgent(h) {
    return !!(h && (h.beszel_name || h.pulse_name || h.ne_url || h.webmin_name));
  },
  // Has the SNMP history series ever recorded a NON-ZERO value
  // for this metric in the loaded window? Used to gate the bar
  // visibility so chassis BMCs (iDRAC / APC UPS / managed
  // switches without hrProcessorLoad) — whose probe writes flat
  // null / zero cpu_used_pct rows on every tick — don't surface
  // a CPU bar that looks "loading" but never moves. The previous
  // gate `hostHasInlineSpark(h, metric)` only required ≥2 finite
  // values; 0 is finite, so flat-zero history was passing as
  // "telemetry available". Stricter check requires at least one
  // strictly-positive sample so a host genuinely at idle (cpu=0
  // sustained) is correctly hidden alongside hosts that never
  // reported the metric at all.
  // Gate semantic: "this host's SNMP agent CAN report CPU/memory"
  // (capability), NOT "this host has had non-zero CPU recently"
  // (value). The DB-level distinction already exists:
  // host_metrics_sampler writes `cpu_used_pct = float(v) if v is
  // not None else None` — so an agent that returns 0% stores 0.0
  // and an agent that doesn't expose CPU stores NULL. Mirror that
  // here: any finite (non-null) value flips the gate, including 0.
  // Hides bars only for genuine no-data hosts (APC UPS, iDRAC
  // chassis BMC, basic switches that don't expose hrProcessorLoad
  // / cpmCPUTotal*); shows bars for idle-at-0 hosts (Cisco SG300
  // switches at 0% CPU) which is the truthful representation.
  _hostHasFiniteSnmpHistory(h, metric) {
    if (!h) {
      return false;
    }
    const entry = this.hostSnmpHistory && this.hostSnmpHistory[h.id];
    const points = entry && Array.isArray(entry.points) ? entry.points : null;
    if (!points || points.length < 2) {
      return false;
    }
    if (metric === 'cpu') {
      for (const p of points) {
        const v = p && p.cpu_used_pct;
        if (v !== null && v !== undefined && Number.isFinite(Number(v))) {
          return true;
        }
      }
      return false;
    }
    if (metric === 'memory') {
      for (const p of points) {
        const tot = Number(p && p.mem_total);
        if (Number.isFinite(tot) && tot > 0) {
          return true;
        }
      }
      return false;
    }
    // SNMP history doesn't currently carry disk_percent — disk
    // axis falls through to live h.disk_total / h.disk_percent.
    return false;
  },
  // SINGLE SOURCE OF TRUTH for chip-rendering across the THREE host
  // chip-strip surfaces (Hosts view, drawer, Admin → Hosts row).
  // Pre-consolidation each surface walked its own hardcoded provider
  // list and they drifted — service_probe was caught rendering in 2
  // of 3 paths (HIGH finding in 2026-05-23 UX review). Now: this
  // registry is canonical; each surface iterates it via its own
  // entry-point helper (curatedHostProviderChips for hosts_config[]
  // shape, hostEnabledAgents for API-row shape with global gate,
  // providerStates for API-row shape with full state machine +
  // memoization). Adding a new provider = ONE entry here.
  //
  // Per-entry contract:
  //   name        — canonical provider name (matches host_stats_source CSV)
  //   label       — display string. Operator-facing; eventually i18n via
  //                  `t('settings.host_stats.source_<name>')`.
  //   curatedGate — predicate over a `hosts_config[]` row (raw curated
  //                  shape — `row.beszel_name`, `row.ping?.enabled`,
  //                  `row.http_probe?.enabled`, etc.). True = the
  //                  curated row has the per-host config to opt this
  //                  provider in. Consumed by curatedHostProviderChips
  //                  for the Admin → Hosts row chip strip.
  //   apiGate     — predicate over an API-row `h` (post-shape,
  //                  `h.beszel_name`, `h.ping_enabled`,
  //                  `h.http_probe_has_targets`, etc.). True = the
  //                  host has the per-host config to opt this provider
  //                  in. Consumed by hostEnabledAgents (drawer) +
  //                  providerStates (Hosts view).
  //   apiStatus   — getter for the provider's self-status used by the
  //                  providerStates state machine. Returns 'down' on a
  //                  hard failure signal, null otherwise. For
  //                  providers without a self-status field (ne /
  //                  webmin / snmp / service_probe), return null.
  //   fpFields    — fingerprint contribution for providerStates's memo.
  //                  Returns a string concatenating every field this
  //                  def reads from `h`; mismatch invalidates the cache.
  //                  Centralised here so adding a new field to a gate
  //                  also adds it to the memo key in one edit.
  _PROVIDER_DEFS: [
    {
      name: 'beszel', label: 'Beszel',
      curatedGate: r => !!(r.beszel_name && String(r.beszel_name).trim()),
      apiGate: h => !!(h.beszel_name && String(h.beszel_name).trim()),
      apiStatus: h => h.beszel_status,
      fpFields: h => (h.beszel_name || '') + '|' + (h.beszel_status || ''),
    },
    {
      name: 'pulse', label: 'Pulse',
      curatedGate: r => !!(r.pulse_name && String(r.pulse_name).trim()),
      apiGate: h => !!(h.pulse_name && String(h.pulse_name).trim()),
      apiStatus: h => h.pulse_status,
      fpFields: h => (h.pulse_name || '') + '|' + (h.pulse_status || ''),
    },
    {
      name: 'node_exporter', label: 'node-exporter',
      curatedGate: r => !!(r.ne_url && String(r.ne_url).trim()),
      apiGate: h => !!(h.ne_url && String(h.ne_url).trim()),
      apiStatus: () => null,
      fpFields: h => (h.ne_url || ''),
    },
    {
      name: 'webmin', label: 'Webmin',
      // Admin row sees curated `webmin_url` (operator-typed Miniserv
      // URL); API row sees `webmin_name` (resolved alias after
      // webmin_aliases lookup). Both gates target the same logical
      // question — the SHAPES differ because the curated config and
      // the post-shape API output flatten the field differently.
      curatedGate: r => !!(r.webmin_url && String(r.webmin_url).trim()),
      apiGate: h => !!(h.webmin_name && String(h.webmin_name).trim()),
      apiStatus: () => null,
      fpFields: h => (h.webmin_name || ''),
    },
    {
      name: 'ping', label: 'Ping',
      // Curated shape: `row.ping = {enabled: true, ...}`. API shape:
      // `h.ping_enabled` (flattened boolean post-shape).
      curatedGate: r => !!(r.ping && r.ping.enabled === true),
      apiGate: h => h.ping_enabled === true,
      // `ping_alive === false` is the only definitive "down" signal —
      // null means "no sample yet" (don't render red on first paint).
      apiStatus: h => (h.ping_alive === false ? 'down' : null),
      fpFields: h => (h.ping_enabled === true ? '1' : '0') + '|'
        + (h.ping_alive === false ? '1' : '0'),
    },
    {
      name: 'snmp', label: 'SNMP',
      // Mirrors the SNMP sampler's actual target resolution chain:
      // `snmp_aliases[id] → snmp_name → address → SKIP`. Chip renders
      // whenever SNMP is per-host enabled AND any valid target exists
      // (either `snmp_name` provider-specific override OR the curated
      // `address` field — the dedicated probe target shared across
      // port-scan / ping / SSH). Earlier iteration required strict
      // `snmp_enabled === true` which dropped chips on hosts where
      // the sampler WAS probing via `address` only.
      curatedGate: r => !!(r.snmp && r.snmp.enabled === true
        && ((r.snmp_name && String(r.snmp_name).trim())
          || (r.address && String(r.address).trim()))),
      apiGate: h => !!(h.snmp_enabled === true
        && ((h.snmp_name && String(h.snmp_name).trim())
          || (h.address && String(h.address).trim()))),
      apiStatus: () => null,
      fpFields: h => (h.snmp_enabled === true ? '1' : '0') + '|'
        + (h.snmp_name || '') + '|' + (h.address || ''),
    },
    {
      name: 'http_probe', label: 'HTTP probe',
      // Curated shape carries the per-host `http_probe.enabled` flag;
      // API shape exposes the same flag flattened to `http_probe_enabled`
      // PLUS the backend-computed `http_probe_has_targets` boolean
      // (resolves the URL chain `http_probe.urls → row.url →
      // services[].url` once on the backend so the SPA doesn't have
      // to walk `h.services` — the API row's `h.services` carries the
      // Beszel systemd ROLLUP object, not the curated services list).
      curatedGate: r => !!(r.http_probe && r.http_probe.enabled === true),
      apiGate: h => !!(h.http_probe_enabled === true && h.http_probe_has_targets),
      // `host_http_status_ok === false` is the explicit fail signal;
      // every other value (null / true / undefined) is benign.
      apiStatus: h => (h.host_http_status_ok === false ? 'down' : null),
      fpFields: h => (h.http_probe_enabled === true ? '1' : '0') + '|'
        + (h.http_probe_has_targets === true ? '1' : '0') + '|'
        + (h.host_http_status_ok === false ? '1' : '0'),
    },
    {
      name: 'service_probe', label: 'Service probe',
      // Per-service-chip reachability sampler — surfaces when ANY
      // `services[].probe.enabled === true`. The curated path walks
      // `row.services` (raw config array). The API path CANNOT do the
      // same: `_shape_host_api_row` overwrites the row's `services` key
      // with the Beszel systemd ROLLUP object ({total, failed, ...}),
      // not the curated array — so it stamps a backend-computed
      // `service_probe_has_targets` boolean instead (mirroring
      // `http_probe_has_targets`), which the API gate reads here.
      curatedGate: r => !!(Array.isArray(r.services)
        && r.services.some(s => s && s.probe && s.probe.enabled === true)),
      apiGate: h => !!h.service_probe_has_targets,
      apiStatus: () => null,
      // Fingerprint the backend has-targets boolean — the API row carries
      // the Beszel rollup under `services`, not the curated array, so the
      // per-chip digest can't be computed here; the boolean flips whenever
      // the host gains/loses a probe-enabled service chip.
      fpFields: h => (h.service_probe_has_targets ? '1' : ''),
    },
  ],

  // Returns `[{name, label}]` for chips representing providers the
  // CURATED row has configured. NO global gate, NO state machine —
  // pure per-row config inspection. Consumed by Admin → Hosts row
  // chip strip (`static/_partials/admin/hosts.html`). Each consumer
  // surface that wants a "what's configured on this curated row"
  // signal should route through this helper instead of inlining the
  // gate logic (see CLAUDE.md "SPA chip-rendering parity").
  curatedHostProviderChips(row) {
    if (!row) {
      return [];
    }
    const out = [];
    for (const def of this._PROVIDER_DEFS) {
      if (def.curatedGate(row)) {
        out.push({name: def.name, label: this._providerLabel(def)});
      }
    }
    return out;
  },
  // Resolve a provider def's display label via i18n. The canonical
  // key is `settings.host_stats.source_<name>` (already populated for
  // every wired provider in en.json:967-974); the registry's bare
  // `def.label` stays as a defensive fallback for any future provider
  // that ships without an i18n key. Routing here (not at registry
  // declaration) lets every consumer pick up locale changes without
  // re-importing the registry on each `t()` re-resolve.
  _providerLabel(def) {
    const key = 'settings.host_stats.source_' + def.name;
    const tr = this.t(key);
    return (tr && tr !== key) ? tr : def.label;
  },

  // Display list of providers enabled on a host — used by the drawer's
  // dedicated "Enabled providers" card. Returns `[{name, label}]`.
  //
  // Per-host enable check is paired with a fleet-level
  // `hasHostStatsSource(<provider>)` gate. A provider that's
  // disabled at the fleet level (operator un-ticked it in Admin →
  // Providers) MUST NOT render its chip even when the per-host
  // alias / enable flag are still populated — pre-fix a stale SNMP
  // chip with a Paused state still appeared on hosts where SNMP was
  // globally disabled, while the per-chip Resume was disabled
  // (busy-flag stuck) and the rollup-Resume was enabled. Filtering
  // at the chip-render gate makes the contradictory state impossible
  // by construction.
  //
  // Adding a new provider — ONE entry in `_PROVIDER_DEFS` is sufficient;
  // this helper picks it up automatically. See CLAUDE.md "SPA chip-
  // rendering parity" for the canonical contract.
  hostEnabledAgents(h) {
    if (!h) {
      return [];
    }
    const out = [];
    for (const def of this._PROVIDER_DEFS) {
      if (def.apiGate(h) && this.hasHostStatsSource(def.name)) {
        out.push({name: def.name, label: this._providerLabel(def)});
      }
    }
    return out;
  },
  // List of paused provider names for one host.
  // Used by the drawer's "Resume all (N)" rollup button to enumerate
  // every chip currently in Paused state. Returns an empty array
  // when no provider is paused — the rollup hides cleanly.
  //
  // FILTERS against `hostEnabledAgents(h)` so providers that are
  // NOT currently enabled on this host don't surface as paused —
  // the orphan-row sweep on host-config save handles
  // host-level orphans, but this guard handles the per-host
  // provider-toggle case (operator disabled SNMP on this row but
  // the failure-state row from the previous probes still exists).
  // Without this filter, "Resume all (2)" can render on a host
  // that only has Ping enabled because old SNMP / Webmin failure
  // rows still match the host_id suffix.
  pausedProvidersFor(h) {
    if (!h) {
      return [];
    }
    const map = h.provider_pause_state;
    if (!map || typeof map !== 'object') {
      return [];
    }
    const enabledNames = new Set(
      (this.hostEnabledAgents(h) || []).map((a) => a && a.name).filter(Boolean),
    );
    const out = [];
    for (const name of Object.keys(map)) {
      if (!enabledNames.has(name)) {
        continue;
      }
      const row = map[name];
      if (row && row.paused) {
        out.push(name);
      }
    }
    return out;
  },
  // — Single-source-of-truth predicates for every Resume affordance
  // in the host drawer. Pre-fix the per-chip Resume gated on the busy
  // flag (`providerResumeBusy[host:provider]`), the rollup gated on
  // pausedProvidersFor.length, and the whole-host Resume sampling
  // gated on `h._resumeBusy`. Three independent predicates produced
  // the operator-visible bug where the per-chip button was disabled
  // while the rollup remained enabled, even though both targeted the
  // same provider on the same host.
  //
  // Contract:
  // canResume(h, name)  — per-chip / per-provider Resume button.
  //                        Allowed when admin + the chip is paused
  //                        + neither the per-provider busy flag NOR
  //                        the whole-host busy flag is set.
  // canResumeAny(h)     — "Resume all" rollup buttons (top banner +
  //                        bottom of card). Allowed when admin + at
  //                        least one provider is paused + NO per-
  //                        provider busy is set across the paused
  //                        set + whole-host isn't busy. So clicking
  //                        one per-chip Resume disables the rollup
  //                        for the duration, and vice versa — the
  //                        two affordances can never disagree.
  // canResumeHost(h)    — whole-host Resume sampling button.
  //                        Allowed when admin + the host is paused +
  //                        neither host-busy NOR any per-provider
  //                        busy on this host is set.
  canResume(h, name) {
    if (!h || !name || !this.isAdmin()) {
      return false;
    }
    if (!this.agentPauseInfo(h, name)) {
      return false;
    }
    if (this.providerResumeBusy[h.id + ':' + name]) {
      return false;
    }
    if (h._resumeBusy) {
      return false;
    }
    return true;
  },
  canResumeAny(h) {
    if (!h || !this.isAdmin()) {
      return false;
    }
    const paused = this.pausedProvidersFor(h);
    if (!paused.length) {
      return false;
    }
    if (h._resumeBusy) {
      return false;
    }
    const hostId = h.id;
    for (const name of paused) {
      if (this.providerResumeBusy[hostId + ':' + name]) {
        return false;
      }
    }
    return true;
  },
  canResumeHost(h) {
    if (!h || !this.isAdmin()) {
      return false;
    }
    if (!h.sampling_paused) {
      return false;
    }
    if (h._resumeBusy) {
      return false;
    }
    const hostId = h.id;
    const paused = this.pausedProvidersFor(h);
    for (const name of paused) {
      if (this.providerResumeBusy[hostId + ':' + name]) {
        return false;
      }
    }
    return true;
  },
  // Resume-all action for the drawer rollup. Fans out
  // `resumeProvider(h, name)` calls in parallel for every currently-
  // paused provider on this host. Optimistic UI clears each row's
  // pause state immediately so the chips flip back without waiting
  // for the per-call SSE round-trip; per-call errors land in the
  // toast layer individually so a partial-failure case is visible
  // ("Resumed 4 of 6 providers; 2 failed"). Admin-only — the button
  // hides when the operator isn't admin.
  async resumeAllProviders(host) {
    if (!host || !host.id) {
      return;
    }
    const paused = this.pausedProvidersFor(host);
    if (!paused.length) {
      return;
    }
    const total = paused.length;
    const results = await Promise.allSettled(
      paused.map((name) => this.resumeProvider(host, name)),
    );
    const failed = results.filter((r) => r.status === 'rejected').length;
    const ok = total - failed;
    if (failed === 0) {
      this.showToast(this.t('hosts_extra.provider_resume_all_done', {
        count: ok, host: this.hostDisplayName(host) || host.id,
      }), 'success');
    } else {
      this.showToast(this.t('hosts_extra.provider_resume_all_partial', {
        ok, total, failed, host: this.hostDisplayName(host) || host.id,
      }), 'warning');
    }
  },
  // Per-(provider, host) auto-pause lookup. Returns the
  // pause-state row for `name` on `h`, or null when the provider
  // isn't paused for this host. Backend populates
  // `provider_pause_state: {snmp: {paused, consecutive_failures,
  // last_error, paused_at, last_ok_ts, ...}}` on every host API
  // row via `_provider_pause_state_for_host(host_id)`. Used by the
  // host drawer's Enabled-agents card to render Paused styling +
  // the Resume button (admin-only).
  // Drawer-chip state-class resolver — mirrors the outer Hosts-row
  // provider chip (`providerStates(h)` → 'failing'/'paused'/'ok')
  // so the drawer's "ENABLED AGENTS" pills reflect the SAME state
  // colour the operator sees outside the drawer. Failing → pill-error
  // (red), paused → pill-warning (orange), otherwise pill-custom
  // (operator-customised brand colour via providerChipStyle).
  _agentStateFor(h, name) {
    if (!h || !name) {
      return 'ok';
    }
    // Try the providerStates list first — same data the outer chip
    // strip consumes. Falls back to agentPauseInfo for older code
    // paths that don't populate providerStates.
    try {
      const states = (typeof this.providerStates === 'function')
        ? this.providerStates(h) : [];
      if (Array.isArray(states)) {
        const match = states.find(p => p && p.name === name);
        if (match && (match.state === 'failing' || match.state === 'paused')) {
          return match.state;
        }
      }
    } catch { /* fall through to pause-info fallback */
    }
    if (this.agentPauseInfo(h, name)) {
      return 'paused';
    }
    return 'ok';
  },
  agentStateClass(h, name) {
    const s = this._agentStateFor(h, name);
    // Both failing AND paused render RED (pill-error) — unified with the
    // Hosts-page chip strip. A paused provider is a fault state that needs
    // action, so it shouldn't read as a softer orange/amber warning here
    // while the page shows red.
    if (s === 'failing' || s === 'paused') {
      return 'pill-error';
    }
    return 'pill-custom';
  },
  agentStateStyle(h, name) {
    const s = this._agentStateFor(h, name);
    if (s === 'failing' || s === 'paused') {
      return '';
    }
    return this.providerChipStyle(name);
  },
  agentStateTitle(h, name) {
    const s = this._agentStateFor(h, name);
    // Resolve the provider's i18n'd label (settings.host_stats.source_<name>)
    // so the tooltip reads the same display name as the chip — never the
    // raw DB enum (node_exporter / service_probe / ...).
    const _k = 'settings.host_stats.source_' + name;
    const _lbl = this.t(_k);
    const provider = (_lbl && _lbl !== _k) ? _lbl : name;
    if (s === 'paused') {
      const info = this.agentPauseInfo(h, name) || {};
      return this.t('hosts_extra.provider_paused', {
        provider,
        count: info.consecutive_failures || 0,
        error: info.last_error || '—',
      });
    }
    if (s === 'failing') {
      return this.t('hosts_extra.provider_failing', {provider});
    }
    return '';
  },
  agentPauseInfo(h, name) {
    if (!h || !name) {
      return null;
    }
    const map = h.provider_pause_state;
    if (!map || typeof map !== 'object') {
      return null;
    }
    const row = map[name];
    if (!row || !row.paused) {
      return null;
    }
    // Match `pausedProvidersFor` — only surface paused state for
    // providers that are CURRENTLY enabled on this host. Stale
    // failure-state rows from a previously-enabled provider that
    // the operator has since disabled would otherwise render an
    // amber chip + Resume button on a chip that wouldn't render
    // at all. Cheap enabledNames lookup; called per-chip so this
    // is hot path.
    const enabledNames = new Set(
      (this.hostEnabledAgents(h) || []).map((a) => a && a.name).filter(Boolean),
    );
    if (!enabledNames.has(name)) {
      return null;
    }
    return row;
  },
  // Last-OK timestamp for a (host, provider) pair. Returns 0 when
  // the provider has never had a successful probe recorded for this
  // host on the current schema (host hasn't been seen
  // shipped, or this is the first probe ever). The chip subtitle
  // hides on 0.
  providerLastOkSeconds(h, name) {
    if (!h || !name) {
      return 0;
    }
    const map = h.provider_pause_state;
    if (!map || typeof map !== 'object') {
      return 0;
    }
    const row = map[name];
    if (!row) {
      return 0;
    }
    return Number(row.last_ok_ts || 0);
  },
  // Human-friendly "Xm ago" / "Xh ago" age string for the chip
  // subtitle. Returns empty when there's nothing to render (the
  // x-show gate hides the span anyway, but the helper stays
  // defensive).
  providerLastOkAge(h, name) {
    const ts = this.providerLastOkSeconds(h, name);
    if (!ts) {
      return '';
    }
    return this.fmtAgo(ts * 1000);
  },
  // Per-(provider, host) row count from the provider's local samples
  // table. Reads `h.provider_sample_counts[<name>]` populated by the
  // backend's `_merge_one_host` (per-host probe path only — bulk
  // /api/hosts/list path leaves the map empty so the chip subtitle
  // hides until the drawer triggers /api/hosts/one/{id}). 0 when the
  // probe hasn't run yet OR the provider's sample table is empty.
  providerSampleCount(h, name) {
    if (!h || !name) {
      return 0;
    }
    const map = h.provider_sample_counts;
    if (!map || typeof map !== 'object') {
      return 0;
    }
    const v = map[name];
    return Number.isFinite(+v) ? +v : 0;
  },
  // Per-provider effective sampler interval in seconds. Backend
  // (`_provider_sample_intervals`) has already resolved the
  // "0 = inherit" sentinel + applied each sampler's floor, so this
  // value matches the actual asyncio.sleep cadence the loop ticks at.
  // 0 when the per-host probe hasn't run yet (cold-load skeleton);
  // chip subtitle hides on 0.
  providerSampleInterval(h, name) {
    if (!h || !name) {
      return 0;
    }
    const map = h.provider_sample_intervals;
    if (!map || typeof map !== 'object') {
      return 0;
    }
    const v = map[name];
    return Number.isFinite(+v) ? +v : 0;
  },
  // Human-friendly "Ns" / "Nm" / "Nm Ks" cadence label for the chip
  // subtitle. The seconds / minutes unit abbreviations are routed through
  // t() (common.unit_seconds_short / unit_minutes_short) so a locale can
  // localise them; the numbers stay as-is. Returns empty string on 0 so
  // the caller's x-show gate hides cleanly.
  providerSampleIntervalLabel(h, name) {
    const s = this.providerSampleInterval(h, name);
    if (!s) {
      return '';
    }
    const us = this.t('common.unit_seconds_short') || 's';
    const um = this.t('common.unit_minutes_short') || 'm';
    if (s < 60) {
      return s + us;
    }
    const m = Math.floor(s / 60);
    const rem = s - m * 60;
    if (rem === 0) {
      return m + um;
    }
    return m + um + ' ' + rem + us;
  },
  // Resume-button busy-state map. Keyed `<host_id>:<provider>` so
  // simultaneous resumes on different providers don't collide.
  providerResumeBusy: {},
  // Manual resume action for the per-provider auto-pause.
  // POSTs /api/hosts/{id}/provider/{name}/resume which clears the
  // failure-state row + the in-memory cool-down for that provider.
  // Optimistic UI: clear the local pause row immediately so the
  // chip flips back without waiting for the next poll, then refresh
  // the row via the shared queue to confirm.
  async resumeProvider(host, name) {
    if (!host || !host.id || !name) {
      return;
    }
    const key = host.id + ':' + name;
    if (this.providerResumeBusy[key]) {
      return;
    }
    this.providerResumeBusy[key] = true;
    // Safety timer — even if `await fetch` hangs forever (browser
    // network freeze, broken proxy holding the connection, etc.),
    // the button gets re-enabled after 30s. Prevents the
    // "click-once-stuck-forever" footgun that operator hit.
    const safetyTimer = setTimeout(() => {
      this.providerResumeBusy[key] = false;
    }, 30000);
    try {
      const r = await fetch(
        '/api/hosts/' + encodeURIComponent(host.id)
        + '/provider/' + encodeURIComponent(name) + '/resume',
        {method: 'POST'},
      );
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        this.showToast(this.t('hosts_extra.provider_resume_failed', {
          provider: name,
          error: (j && j.detail) || ('HTTP ' + r.status),
        }), 'error');
        return;
      }
      // Optimistic clear so the chip flips immediately. The next
      // refresh from the shared host-refresh queue confirms via the
      // backend's authoritative state.
      if (host.provider_pause_state && host.provider_pause_state[name]) {
        host.provider_pause_state[name].paused = false;
      }
      this.showToast(this.t('hosts_extra.provider_resumed', {
        provider: name,
        host: this.hostDisplayName(host) || host.id,
      }), 'success');
      // Force-refresh the row so backend's authoritative state lands.
      if (typeof this.refreshHostRow === 'function') {
        this.refreshHostRow(host.id, {force: true}).catch(() => {
        });
      }
    } catch (e) {
      this.showToast(this.t('hosts_extra.provider_resume_failed', {
        provider: name,
        error: String(e),
      }), 'error');
    } finally {
      clearTimeout(safetyTimer);
      this.providerResumeBusy[key] = false;
    }
  },
  toggleHost(name) {
    const host = (this.hosts || []).find(h => h.host === name);
    // Already-open drawer can always close (e.g. by clicking the
    // same row again) even if the host flipped from "up" to "down"
    // since the last open — otherwise the operator would be stuck
    // looking at stale detail cards.
    const already = this.drawerHost && this.drawerHost.host === name;
    if (already) {
      this.closeHostDrawer();
      return;
    }
    if (!this.isHostExpandable(host)) {
      return;  // dead / unmatched host — header click is a no-op
    }
    this.openHostDrawer(host);
  },
  // SNMP time-series state. Keyed per-host (no Beszel-id
  // fallback because SNMP probes are always per-host). Reads from
  // the new `/api/hosts/{id}/snmp/history` endpoint that wraps the
  // `host_snmp_samples` table written by the sampler. Same loading-
  // flag pattern as `hostHistory` so chart cards don't flicker on
  // range-picker clicks.
  // HTTP probe latency history — per-host slot. Each entry is the
  // shape returned by `GET /api/hosts/{id}/http-probe/history`:
  //   { series: [{t, url, latency_ms, status_ok, tls_expires_in_days}, ...],
  //     collectors: {sample_count, urls},
  //     loadedAt: <epoch ms>, hours: <1|6|24|168>, error: <str|null> }
  // Wired into `chartFreshness(h)` above so the drawer's "Last sample
  // Xm ago" label includes the HTTP probe series alongside CPU / Mem /
  // SNMP. UI render uses the per-URL grouping captured on each point.
  hostHttpProbeHistory: {},
  // Per-host "Show all" toggle state for the URL strip. Keyed on
  // host id; default off means the collapsed cap applies.
  httpProbeShowAll: {},
  // Per-host row Test button — busy flag + last-result cache.
  // Keyed on host id so multiple drawers (or arrow-key drawer nav)
  // each carry their own state independently.
  httpProbeRowTestBusy: {},
  httpProbeRowTestResult: {},
  /** Viewport-aware collapsed cap for the URL list. Mirrors the
   * `effectiveCollapsedLimit()` pattern from the server-health
   * dense-list rule: tighter cap below 480px so mobile cards
   * stay compact.
   */
  _httpProbeUrlCap() {
    const narrow = (typeof window !== 'undefined' && window.innerWidth && window.innerWidth < 480);
    return narrow ? 3 : 8;
  },
  /** Sort URLs by status weight (failing → warning → ok) so the
   * most actionable URL is always above the fold when the strip is
   * collapsed. Stable secondary order by url so the layout doesn't
   * jitter on every poll.
   */
  _httpProbeSortedUrls(h) {
    if (!h || !Array.isArray(h.host_http_urls) || !h.host_http_urls.length) {
      return [];
    }
    const arr = h.host_http_urls.slice();
    arr.sort((a, b) => {
      // weight: 0=ok (status_ok && content_match_ok); 1=warning
      // (status_ok but content mismatch); 2=fail. Lower wins early.
      const wa = (a.status_ok && a.content_match_ok) ? 0
        : (a.status_ok ? 1 : 2);
      const wb = (b.status_ok && b.content_match_ok) ? 0
        : (b.status_ok ? 1 : 2);
      // Sort failures FIRST in the rendered output — so weight=2
      // comes before weight=0.
      if (wa !== wb) {
        return wb - wa;
      }
      return String(a.url || '').localeCompare(String(b.url || ''));
    });
    return arr;
  },
  httpProbeVisibleUrls(h) {
    const all = this._httpProbeSortedUrls(h);
    if (!all.length) {
      return all;
    }
    if (h && h.id && this.httpProbeShowAll[h.id]) {
      return all;
    }
    return all.slice(0, this._httpProbeUrlCap());
  },
  httpProbeHasMoreUrls(h) {
    const all = this._httpProbeSortedUrls(h);
    return all.length > this._httpProbeUrlCap();
  },
  httpProbeHiddenUrlCount(h) {
    const all = this._httpProbeSortedUrls(h);
    const cap = this._httpProbeUrlCap();
    return Math.max(0, all.length - cap);
  },
  toggleHttpProbeShowAll(h) {
    if (!h || !h.id) {
      return;
    }
    this.httpProbeShowAll[h.id] = !this.httpProbeShowAll[h.id];
  },
  /** Inline SVG mini-chart for the HTTP probe latency series. One
   * line per URL — points carry `t` (epoch s) and `latency_ms`. The
   * x-axis maps the active window (1h / 6h / 24h / 7d); the y-axis
   * auto-scales to the maximum observed latency. Skip points with
   * latency_ms === null (probe failed) so the line renders as a real
   * gap rather than a vertical drop.
   *
   * The output is a small `<svg>` blob — much simpler than the full
   * `hostChart` helper (which carries axes / legends / tooltips), but
   * sufficient for the "is the latency trend stable" question this
   * card needs to answer.
   */
  renderHttpProbeLatencyMiniChart(h) {
    try {
      const entry = (h && this.hostHttpProbeHistory) ? this.hostHttpProbeHistory[h.id] : null;
      if (!entry || !Array.isArray(entry.series) || !entry.series.length) {
        return '';
      }
      const pts = entry.series.filter(p => p && p.latency_ms != null && p.latency_ms >= 0);
      if (!pts.length) {
        return '';
      }
      // Group by URL so each line is independent.
      const byUrl = new Map();
      for (const p of pts) {
        const u = p.url || '';
        if (!byUrl.has(u)) {
          byUrl.set(u, []);
        }
        byUrl.get(u).push(p);
      }
      const urls = Array.from(byUrl.keys()).sort();
      if (!urls.length) {
        return '';
      }
      // Time range — fall back to min/max of points when window
      // is short. SVG coords are 0..100 logical w, 0..40 h (compact).
      let minT = Infinity;
      let maxT = -Infinity;
      let maxV = 0;
      for (const p of pts) {
        if (p.t < minT) {
          minT = p.t;
        }
        if (p.t > maxT) {
          maxT = p.t;
        }
        if (p.latency_ms > maxV) {
          maxV = p.latency_ms;
        }
      }
      if (!isFinite(minT) || !isFinite(maxT) || maxT === minT) {
        return '';
      }
      if (maxV <= 0) {
        maxV = 1;
      }
      const W = 100;
      const H = 36;
      // Deterministic per-URL hue via simple hash so colours stay
      // stable across re-renders. CSS variables can't be used inline
      // in SVG stroke= per the token discipline rule, but the SPA's
      // provider-colour scheme already uses computed hex literals
      // here; we hash to one of a small token palette.
      const _hue = (s) => {
        let h2 = 0;
        for (let i = 0; i < s.length; i++) {
          // `>>> 0` clamps to unsigned 32-bit — load-bearing, NOT pointless.
          // Dropping it lets `h2` grow toward Number.MAX_SAFE_INTEGER which
          // changes the hash's distribution. PyCharm's "can be replaced"
          // suggestion is wrong in this context.
          // noinspection PointlessBitwiseExpressionJS
          h2 = (h2 * 31 + s.charCodeAt(i)) >>> 0;
        }
        return h2 % 360;
      };
      const lines = [];
      for (const url of urls) {
        const series = byUrl.get(url).slice().sort((a, b) => a.t - b.t);
        if (series.length < 2) {
          continue;
        }
        let path = '';
        for (let i = 0; i < series.length; i++) {
          const s = series[i];
          const x = ((s.t - minT) / (maxT - minT)) * W;
          const y = H - ((s.latency_ms / maxV) * (H - 2)) - 1;
          path += (i === 0 ? 'M' : ' L') + x.toFixed(2) + ',' + y.toFixed(2);
        }
        const hue = _hue(url);
        lines.push('<path d="' + path + '" fill="none" stroke="hsl(' + hue + ', 65%, 55%)" stroke-width="1.2" vector-effect="non-scaling-stroke"></path>');
      }
      if (!lines.length) {
        return '';
      }
      const ariaLabel = this._logEscape(this.t('host_drawer.http_probe.history_heading') || 'Latency history');
      return '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" '
        + 'width="100%" height="44" aria-label="' + ariaLabel + '" role="img">'
        + lines.join('')
        + '</svg>';
    } catch {
      // Malformed payload / empty series — render no sparkline rather than crash the parent row.
      return '';
    }
  },
  /** Derived cache key for the HTTP latency series inside the
   * standard `hostHistory[key]` cache slot. Lets the existing chart
   * helpers (`hostChart`, `hostChartMax`, `hostMetricStats`,
   * `xAxisFromSeries`) work unchanged against the http-probe data. */
  httpLatencyKey(h) {
    return 'http:' + (h && h.id ? h.id : '');
  },
  hostSnmpHistory: {},
  // Per-temperature-probe history for Dell server hosts. Same shape as hostSnmpIfaceHistory but keyed by
  // probe_idx. `probes: { idx: { name, points: [{ts, c}, …] } }`.
  hostSnmpTempHistory: {},
  // Loose SNMP-target gate — true when the curated host has
  // ANY SNMP probe target, regardless of the per-host UI checkbox
  // state. The SPA's `snmp_enabled` flag is the curated-UI toggle;
  // the operational sampler runs whenever `snmp_name` or `address`
  // resolves a target. Pre-fix every SNMP-fetch site checked the
  // strict UI flag → SNMP-tracked hosts whose UI checkbox wasn't
  // ticked never had their history fetched → "Collecting data"
  // placeholder forever despite fresh `host_snmp_iface_samples`
  // rows. Used by the host-list polling loop, drawer-open prefetch,
  // IntersectionObserver lazy-fetch, and per-row probe-arrival
  // kicker — single source of truth so the gate stays consistent.
  _snmpHasProbeTarget(host) {
    if (!host) {
      return false;
    }
    // STRICT opt-in: `snmp_enabled === true` is the master gate —
    // a host that hasn't been explicitly opted-in to SNMP via the
    // per-host checkbox is hidden regardless of which target
    // fields are populated. Same opt-in rigour as
    // `hosts_config[].ssh.enabled` and `hosts_config[].ping.enabled`.
    if (host.snmp_enabled !== true) {
      return false;
    }
    // Once opted in, the gate ALSO requires a resolvable target —
    // either the explicit per-provider `snmp_name` OR the canonical
    // per-host `address` field that the SNMP sampler's resolver
    // chain falls back to (`aliases[id] → snmp_name → address →
    // SKIP` — see logic/snmp.py + CLAUDE.md "address is the
    // canonical provider-independent probe target"). Operator
    // pattern: tick SNMP on a host, leave `snmp_name` blank,
    // populate `address` once with the LAN hostname/IP — the
    // sampler probes against `address`, this chip renders.
    // Without a resolvable target the chip stays hidden so the
    // operator isn't told "SNMP is on" when the sampler has
    // nothing to probe.
    const hasName = !!(host.snmp_name && String(host.snmp_name).trim());
    const hasAddr = !!(host.address && String(host.address).trim());
    return hasName || hasAddr;
  },
  // Per-interface SNMP counter history. One entry per host,
  // each storing { ifaces: { ifname: [points] }, loading, error,
  // loadedAt }. Powers the per-port throughput chart on switches /
  // routers. Same loading-flag + back-compat pattern as
  // `hostSnmpHistory` so chart cards don't flicker.
  hostSnmpIfaceHistory: {},
  // Compute per-interface bps series from the cumulative counters.
  // Returns { in: [bps...], out: [bps...], times: [ts...] } aligned
  // to the points length. Skip-don't-synthesize on out-of-bounds
  // deltas: same bounds as `snmpThroughputBpsSeries`.
  snmpIfaceBpsSeries(hostId, ifname) {
    const entry = this.hostSnmpIfaceHistory[hostId] || {};
    const ifaces = entry.ifaces || {};
    const series = ifaces[ifname] || [];
    if (series.length < 2) {
      return {in: [], out: [], times: []};
    }
    // Bucket-aware dt cap — pre-fix the hardcoded 3600s cap zeroed
    // every delta on 7d windows once the backend started bucketing
    // iface_history (5040s buckets at 7d × 120 target points).
    // Same fix shape as snmpThroughputBpsSeries: cap scales to
    // max(3600, bucket × 3) so one missed bucket is still tolerated
    // but multi-bucket outage gaps are still skipped.
    const bucketS = Number(entry.bucket_seconds) || 0;
    const dtCap = Math.max(3600, bucketS * 3);
    // Byte-delta cap scales with dt — same reasoning as the
    // snmpThroughputBpsSeries fix. Static 10 GB ceiling at 5min raw
    // = generous; at 7d buckets it's a per-bucket-MB/s straitjacket
    // that filters every delta on hosts pushing >16 Mbit/s sustained.
    // Scaled cap = max(10 GB, dt × 125 MB/s) gives 1 Gbit/s headroom.
    const _BYTE_RATE_CEILING = 125 * 1024 * 1024;
    const _STATIC_BYTE_FLOOR = 10 * 1024 * 1024 * 1024;
    // skip-don't-synthesize. Same null-slot pattern as
    // `snmpThroughputBpsSeries`. First slot null (no predecessor);
    // any out-of-bounds delta (wrap / reboot / gap > dtCap / null
    // counter / > scaled byteCap) leaves the slot null instead of
    // plotting a synthesized 0 that visually merges with a real
    // idle iface.
    const inBps = new Array(series.length).fill(null);
    const outBps = new Array(series.length).fill(null);
    const times = series.map(p => p.ts);
    for (let i = 1; i < series.length; i++) {
      const a = series[i - 1], b = series[i];
      const dt = (b.ts || 0) - (a.ts || 0);
      if (dt < 1 || dt > dtCap) {
        continue;
      }
      const byteCap = Math.max(_STATIC_BYTE_FLOOR, dt * _BYTE_RATE_CEILING);
      const ai = a.in_bytes, bi = b.in_bytes;
      if (ai != null && bi != null) {
        const di = bi - ai;
        if (di >= 0 && di <= byteCap) {
          inBps[i] = di / dt;
        }
      }
      const ao = a.out_bytes, bo = b.out_bytes;
      if (ao != null && bo != null) {
        const dout = bo - ao;
        if (dout >= 0 && dout <= byteCap) {
          outBps[i] = dout / dt;
        }
      }
    }
    return {in: inBps, out: outBps, times};
  },
  // Top N interfaces by latest combined throughput. Returns array of
  // { name, lastIn, lastOut, total } sorted desc. Used to pick which
  // ports to plot on the per-port chart so 48-port switches don't
  // produce 96 noisy lines.
  snmpTopIfacesByThroughput(hostId, n) {
    const ifaces = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
    const out = [];
    for (const name of Object.keys(ifaces)) {
      const s = this.snmpIfaceBpsSeries(hostId, name);
      let lastIn = 0, lastOut = 0;
      for (let i = s.in.length - 1; i >= 0; i--) {
        if (s.in[i] > 0) {
          lastIn = s.in[i];
          break;
        }
      }
      for (let i = s.out.length - 1; i >= 0; i--) {
        if (s.out[i] > 0) {
          lastOut = s.out[i];
          break;
        }
      }
      const total = lastIn + lastOut;
      if (total > 0) {
        out.push({name, lastIn, lastOut, total});
      }
    }
    out.sort((a, b) => b.total - a.total);
    return out.slice(0, n || 5);
  },
  snmpHasIfaceHistory(hostId) {
    const ifaces = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
    for (const name of Object.keys(ifaces)) {
      if ((ifaces[name] || []).length >= 2) {
        return true;
      }
    }
    return false;
  },
  // true when this host's SNMP history has accumulated enough
  // points to draw a polyline (≥ 2 ticks). Used to gate the
  // "Collecting data..." spinner block that every SNMP chart card
  // shows during warm-up — operator-flagged that pre-fix every
  // SNMP chart rendered an empty grid + axis labels with no
  // indication that data was being collected.
  snmpHasEnoughHistory(hostId) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    return series.length >= 2;
  },
  // true when at least one interface has computable
  // utilization % (history sample with link_speed_mbps known). The
  // heatmap card uses this to decide between rendering chips with
  // util% colours vs the "Collecting data..." spinner. Pre-fix the
  // chips rendered from live ifaces immediately but always grey
  // (link speed null) — looked broken. Now waits until at least
  // one chip can show a meaningful colour.
  snmpHasIfaceUtilization(hostId, h) {
    // True when at least one iface has ≥ 2 ticks of bps history.
    // Link speed used for the divisor is either the agent-reported
    // ifHighSpeed (preferred) or a 100 Mbps fallback assumption
    // — printers / embedded gear that don't expose
    // ifHighSpeed previously had this card stuck at "Collecting…"
    // forever. The fallback divisor is announced in the legend
    // tooltip so operators know the percentages are an
    // approximation on those hosts.
    const names = this.snmpAllIfacesSorted(hostId, h);
    for (const n of names) {
      const s = this.snmpIfaceBpsSeries(hostId, n);
      if (s.in.length >= 2 || s.out.length >= 2) {
        return true;
      }
    }
    return false;
  },
  // per-iface utilization series (% of link capacity) over
  // time. Walks `snmpIfaceBpsSeries` and divides each point by the
  // iface's link capacity (Mbps × 1e6 / 8 = bytes/sec). Returns []
  // when link speed unknown — caller guards on length before
  // rendering. Used by the per-port utilization LINE chart that
  // replaced the chip-strip heatmap (operator-flagged that the
  // chip layout was misread as a broken chart).
  snmpIfaceUtilizationSeries(hostId, ifname, h) {
    // — fall back to a 100 Mbps assumption when ifHighSpeed
    // isn't exposed (printers / embedded gear). The percentages
    // are then approximate but the polyline RENDERS instead of
    // staying empty forever. snmpIfaceLinkSpeedAssumed() flags
    // the assumption for legend display.
    const link = this.snmpIfaceLinkSpeedMbps(hostId, ifname, h)
      || this._DEFAULT_IFACE_LINK_MBPS;
    const linkBps = link * 1000000 / 8;
    if (linkBps <= 0) {
      return [];
    }
    const s = this.snmpIfaceBpsSeries(hostId, ifname);
    // null-aware. When the underlying bps series has a
    // counter-wrap / gap slot (null), propagate the null so
    // `_snmpPolyPoints` skips it instead of plotting 0 %.
    const out = new Array(s.in.length).fill(null);
    for (let i = 0; i < s.in.length; i++) {
      const inV = s.in[i], outV = s.out[i];
      if (inV == null && outV == null) {
        continue;
      }
      const peak = Math.max(inV || 0, outV || 0);
      out[i] = Math.min(100, (peak / linkBps) * 100);
    }
    return out;
  },
  snmpIfaceUtilizationLine(hostId, ifname, h) {
    const vals = this.snmpIfaceUtilizationSeries(hostId, ifname, h);
    if (!vals.length) {
      return '';
    }
    // — pull timestamps from the underlying iface series so the
    // utilization polyline renders against the drawer-shared time
    // domain. snmpIfaceUtilizationSeries derives from snmpIfaceBpsSeries
    // which exposes parallel `times`, identical length to the values
    // array.
    const s = this.snmpIfaceBpsSeries(hostId, ifname);
    return this._snmpPathGapped(vals, 100, {times: s.times});
  },
  // Operator-flagged: APC UPS lance interface at ~37 B/s on a
  // 100 Mbps fallback link = 0.0003% utilization — flat at the
  // bottom of a hardcoded 0..100% Y-axis. The chart looked empty
  // even though the data was correct. Auto-rescale fixes it.
  //
  // Returns the Y-axis max (% of link capacity) for ONE host's
  // utilization chart — the smallest "nice" round number ≥ the
  // max value across the top-5 visible ifaces. Snaps to 100 / 10 /
  // 1 / 0.1 / 0.01 / 0.001 so the Y-axis labels read cleanly.
  // High-traffic switches still render 0..100% as before.
  snmpIfaceUtilizationYMax(hostId, h) {
    const top = this.snmpTopIfacesByThroughput(hostId, 5);
    if (!top || !top.length) {
      return 100;
    }
    let peak = 0;
    for (const t of top) {
      const vals = this.snmpIfaceUtilizationSeries(hostId, t.name, h);
      for (const v of vals) {
        if (v != null && v > peak) {
          peak = v;
        }
      }
    }
    if (peak <= 0) {
      return 100;
    }            // truly idle → keep traditional scale
    if (peak >= 50) {
      return 100;
    }            // typical busy switch / router
    if (peak >= 10) {
      return 50;
    }
    if (peak >= 1) {
      return 10;
    }
    if (peak >= 0.1) {
      return 1;
    }
    if (peak >= 0.01) {
      return 0.1;
    }
    if (peak >= 0.001) {
      return 0.01;
    }
    return 0.001;
  },
  // Y-axis tick labels for the auto-rescaled util chart. Three
  // ticks (top / mid / bottom) so they line up with the existing
  // `.metric-y-axis` flex `justify-content: space-between` rhythm.
  // Format adapts: integer % at ≥ 1, two decimals at < 1, three
  // decimals at < 0.1 — operators reading "0.001%" on the
  // bottom-of-card UPS chart still see real precision.
  snmpIfaceUtilizationYAxisLabels(hostId, h) {
    const yMax = this.snmpIfaceUtilizationYMax(hostId, h);
    const fmt = (v) => {
      if (v >= 1) {
        return Math.round(v) + '%';
      }
      if (v >= 0.1) {
        return v.toFixed(2) + '%';
      }
      return v.toFixed(3) + '%';
    };
    return [fmt(yMax), fmt(yMax / 2), '0%'];
  },
  // Variant of `snmpIfaceUtilizationLine` that scales 0..yMax
  // instead of 0..100. The SVG y-coordinate system is still
  // 0..100 (the `metric-svg` viewBox uses 100 as the height-
  // reference for the polyline path generator), so we pass
  // `refMax=yMax` to `_snmpPathGapped` to stretch the data to
  // fill the chart vertically.
  snmpIfaceUtilizationLineScaled(hostId, ifname, h) {
    const vals = this.snmpIfaceUtilizationSeries(hostId, ifname, h);
    if (!vals.length) {
      return '';
    }
    const yMax = this.snmpIfaceUtilizationYMax(hostId, h);
    const s = this.snmpIfaceBpsSeries(hostId, ifname);
    return this._snmpPathGapped(vals, yMax, {times: s.times});
  },
  // Format helper for the legend's per-iface util % chip. Pre-fix
  // `Math.round(pct)` rendered "~0%" for any sub-1% value, masking
  // the real activity. Now: ≥ 1 → integer %, < 1 → two decimals,
  // < 0.1 → three decimals.
  snmpIfaceUtilizationPctLabel(hostId, ifname, h) {
    const pct = this.snmpIfaceUtilizationPct(hostId, ifname, h);
    if (pct == null) {
      return '';
    }
    if (pct >= 1) {
      return Math.round(pct) + '%';
    }
    if (pct >= 0.1) {
      return pct.toFixed(2) + '%';
    }
    return pct.toFixed(3) + '%';
  },
  // Gap-aware path string for one iface's bps series scaled to refMax.
  // Consumer renders via SVG `<path :d>` not `<polyline :points>` so
  // counter-wrap / reboot / gap nulls show as visual breaks.
  snmpIfaceLine(hostId, ifname, dir, refMax) {
    const s = this.snmpIfaceBpsSeries(hostId, ifname);
    const vals = (dir === 'in' ? s.in : s.out);
    if (!vals.length) {
      return '';
    }
    return this._snmpPathGapped(vals, refMax || 1, {times: s.times});
  },
  snmpIfaceMaxBps(hostId) {
    const ifaces = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
    let m = 0;
    for (const name of Object.keys(ifaces)) {
      const s = this.snmpIfaceBpsSeries(hostId, name);
      for (const v of s.in) {
        if (v > m) {
          m = v;
        }
      }
      for (const v of s.out) {
        if (v > m) {
          m = v;
        }
      }
    }
    return m;
  },
  // slice 4 — link speed (Mbps) for one iface. Tries history
  // first (newest non-null), then falls back to the live
  // `host.network_ifaces[].link_speed_mbps` from the latest probe.
  // Returns null when ifHighSpeed isn't exposed on this device.
  snmpIfaceLinkSpeedMbps(hostId, ifname, h) {
    const series = ((this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {})[ifname] || [];
    for (let i = series.length - 1; i >= 0; i--) {
      const s = series[i].link_speed_mbps;
      if (s != null && s > 0) {
        return s;
      }
    }
    const host = h || (this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null);
    if (host && Array.isArray(host.network_ifaces)) {
      const live = host.network_ifaces.find(i => i && i.name === ifname);
      if (live && live.link_speed_mbps && live.link_speed_mbps > 0) {
        return live.link_speed_mbps;
      }
    }
    return null;
  },
  // 100 Mbps fallback when ifHighSpeed isn't exposed —
  // printers / embedded gear with no managed-NIC reporting still
  // produce a percentage on the per-port utilization chart instead
  // of leaving the card stuck at "Collecting data…". The chart's
  // legend tooltip surfaces "(assumed 100 Mbps)" via
  // `snmpIfaceLinkSpeedAssumed` so operators know the divisor is
  // approximate on those hosts.
  _DEFAULT_IFACE_LINK_MBPS: 100,
  snmpIfaceLinkSpeedAssumed(hostId, ifname, h) {
    return !this.snmpIfaceLinkSpeedMbps(hostId, ifname, h);
  },
  // Utilization % for one iface = max(in, out) bps × 8 ÷ link_bps × 100.
  // Falls back to a 100 Mbps assumption when link speed unknown
  // — pre-fix this returned null so the percent legend stayed
  // blank on printers and the line chart stayed empty forever.
  snmpIfaceUtilizationPct(hostId, ifname, h) {
    const link = this.snmpIfaceLinkSpeedMbps(hostId, ifname, h)
      || this._DEFAULT_IFACE_LINK_MBPS;
    if (!link) {
      return null;
    }
    const s = this.snmpIfaceBpsSeries(hostId, ifname);
    let lastIn = 0, lastOut = 0;
    for (let i = s.in.length - 1; i >= 0; i--) {
      if (s.in[i] > 0) {
        lastIn = s.in[i];
        break;
      }
    }
    for (let i = s.out.length - 1; i >= 0; i--) {
      if (s.out[i] > 0) {
        lastOut = s.out[i];
        break;
      }
    }
    const peakBps = Math.max(lastIn, lastOut);
    const linkBps = link * 1000000 / 8;       // Mbps → bytes/sec capacity
    if (linkBps <= 0) {
      return null;
    }
    return Math.min(100, (peakBps / linkBps) * 100);
  },
  // Full iface list for the heatmap. Tries history first, falls
  // back to the LIVE `host.network_ifaces[]` so chips render
  // immediately when the history table is still empty (fresh
  // SNMP enrolment, or before the first sampler tick lands).
  // Excludes loopback / docker / veth / bridge / cni / flannel /
  // cali / vmnet / tap / tun / ovs prefixes — same exclusion set
  // the sampler uses, so chip count matches what the throughput
  // chart graphs.
  snmpAllIfacesSorted(hostId, h) {
    const exclude = ['lo', 'docker', 'veth', 'br-', 'cni',
      'flannel', 'cali', 'vmnet', 'tap', 'tun', 'ovs'];
    const isExcluded = (name) => {
      const n = (name || '').toLowerCase();
      return exclude.some(p => n.startsWith(p));
    };
    const ifacesHist = (this.hostSnmpIfaceHistory[hostId] || {}).ifaces || {};
    let names = Object.keys(ifacesHist).filter(n => !isExcluded(n));
    if (!names.length) {
      const host = h || (this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null);
      if (host && Array.isArray(host.network_ifaces)) {
        names = host.network_ifaces
          .map(i => i && i.name)
          .filter(n => n && !isExcluded(n));
      }
    }
    return names.sort((a, b) =>
      a.localeCompare(b, undefined, {numeric: true, sensitivity: 'base'})
    );
  },
  // Color mapping for the heatmap cell: green < 50 < amber < 85 < red.
  // Same thresholds as the .stat-bar fill (CLAUDE.md: 60/85 split was
  // for CPU/mem; ports tend to alarm earlier so 50/85). Returns a
  // CSS color literal — heatmap uses inline style because there's
  // no pre-existing token for the per-cell shade.
  snmpIfaceHeatmapColor(pct) {
    if (pct == null) {
      return 'var(--surface-3)';
    }      // unknown speed
    if (pct >= 85) {
      return 'var(--danger)';
    }
    if (pct >= 50) {
      return 'var(--warning)';
    }
    if (pct > 0) {
      return 'var(--success)';
    }
    return 'var(--surface-3)';                       // 0% / idle
  },
  // Detect a reboot in the SNMP uptime history. Walks the
  // points pairwise from latest to oldest looking for the LAST
  // backwards-jump in `uptime_s` (each adjacent pair where N's value
  // is less than N-1's = a reboot in that window). Returns
  // `{ts, prev_uptime_s, age_s}` of the most recent detected reboot,
  // or null when no reboot is in the history window. The drawer
  // surfaces a compact "Rebooted Xh ago" badge when this is non-null
  // AND the age is under 24h (older reboots aren't surfaced to keep
  // the badge actionable for fresh anomalies; full uptime is always
  // shown via the live `host_uptime_s` field).
  snmpRebootInfo(h) {
    if (!h) {
      return null;
    }
    const hist = this.hostSnmpHistory[h.id];
    const points = (hist && hist.points) || [];
    if (points.length < 2) {
      return null;
    }
    let detected = null;
    for (let i = 1; i < points.length; i++) {
      const prev = points[i - 1];
      const curr = points[i];
      if (prev.uptime_s == null || curr.uptime_s == null) {
        continue;
      }
      // Reboot fingerprint: uptime went BACKWARDS between samples.
      // Allow a small slack (60s) to absorb counter-precision noise
      // when sampler ticks are tightly spaced.
      if (curr.uptime_s + 60 < prev.uptime_s) {
        detected = {
          ts: curr.ts,
          prev_uptime_s: prev.uptime_s,
          age_s: Math.max(0, Math.round(Date.now() / 1000 - curr.ts)),
        };
      }
    }
    // Only surface reboots within the last 24h — older reboots are
    // archaeology, not actionable.
    if (detected && detected.age_s > 86400) {
      return null;
    }
    return detected;
  },
  // Memory chart Y-axis upper bound. Prefer the LIVE
  // `host_mem_total` (Beszel/NE-style absolute), fall back to the
  // max `mem_total` in history points so the axis renders sensibly
  // before the live probe completes. Final fallback is the highest
  // observed mem_used + mem_buffers + mem_cached + mem_free across
  // history (if mem_total field was never populated).
  snmpMemMax(h) {
    if (!h) {
      return 0;
    }
    const live = +h.host_mem_total || 0;
    if (live > 0) {
      return live;
    }
    const hist = this.hostSnmpHistory[h.id];
    const points = (hist && hist.points) || [];
    let max = 0;
    for (const p of points) {
      if (p.mem_total && +p.mem_total > max) {
        max = +p.mem_total;
      }
    }
    if (max > 0) {
      return max;
    }
    // Synthesise from the layer sum as a last resort.
    for (const p of points) {
      const sum = (+p.mem_used || 0) + (+p.mem_buffers || 0)
        + (+p.mem_cached || 0) + (+p.mem_free || 0);
      if (sum > max) {
        max = sum;
      }
    }
    return max;
  },
  // Freshness label for the SNMP chart section. Returns
  // `{age_s, label, stale}` or null when there's no data yet.
  // `stale` is true once age exceeds 2× the host_snmp sampler
  // cadence (~5min), used to amber-tint the label so the
  // operator knows the data hasn't refreshed in a while.
  //
  // — Combined freshness across the two writers feeding the
  // host drawer: the LIFESPAN sampler (writes `host_snmp_samples`,
  // surfaces here as `hist.points[last].ts`) AND the per-request
  // gather path (writes the snapshot `_stale_ts`). Pre-fix this
  // helper read ONLY the sampler's most-recent row, so when the
  // gather path successfully merged live data 7m ago but the
  // sampler hadn't written a row in 9h (sampler paused / gated /
  // its INSERT condition not met for this host), the label said
  // "Last sample 9h ago" while the snapshot banner — sourced from
  // `_stale_ts` — said "Last live data 7m ago". Two surfaces
  // disagreed about the SAME host. Post-fix: take the
  // most-recent of (sampler ts, snapshot ts) so both surfaces
  // report the same value. The downstream root cause (sampler
  // lagging the gather path) is a separate concern; this is the
  // honest-UI fix that always reflects the operator's freshest
  // signal.
  snmpHistoryFreshness(h) {
    if (!h) {
      return null;
    }
    const hist = this.hostSnmpHistory[h.id];
    const samplerTs = (hist && Array.isArray(hist.points) && hist.points.length)
      ? Number((hist.points[hist.points.length - 1] || {}).ts
        || (hist.points[hist.points.length - 1] || {}).t || 0)
      : 0;
    const snapshotTs = Number(h._stale_ts || 0);
    const ts = Math.max(samplerTs, snapshotTs);
    if (!ts || !Number.isFinite(ts) || ts <= 0) {
      return null;
    }
    const ageS = Math.max(0, Math.round(Date.now() / 1000 - ts));
    let label;
    if (ageS < 60) {
      label = ageS + 's';
    } else {
      if (ageS < 3600) {
        label = Math.round(ageS / 60) + 'm';
      } else {
        label = Math.round(ageS / 3600) + 'h';
      }
    }
    // `source` lets the template render a tooltip explaining which
    // writer the timestamp came from — operators tracing
    // freshness disagreements can see at a glance whether the
    // value came from the sampler or the snapshot.
    const source = (samplerTs >= snapshotTs) ? 'sampler' : 'snapshot';
    return {age_s: ageS, label, stale: ageS > 600, source};
  },
  // Build a polyline `points` attribute from a series of values.
  // Normalises against `max` (default = max value in series) so the
  // chart spans the full SVG viewBox. ViewBox 420×120 matches the
  // existing Beszel / NE chart cards so the SNMP charts
  // render at the same scale + gridline density as their cousins.
  // — Unified drawer time-domain for every host-drawer chart.
  // Returns the [tMinSec, tMaxSec] window the picker has selected
  // (1h / 6h / 24h / 7d) anchored to "now" so every chart renders
  // against the SAME visual x-axis. Pre-fix each helper computed
  // x by sample-INDEX (`x = i / (n-1) * w`), which made each
  // chart's leftmost pixel mean "the oldest sample I have" — varied
  // across providers (NE 4 samples / 38 min, Beszel 12 / 55 min,
  // SNMP 60 / 60 min) so spikes never aligned vertically across
  // cards. Time-based x makes leftmost = "now - rangeMs" universally
  // and a sparse provider's polyline simply starts mid-axis where
  // its earliest sample landed. Width / height kept at the existing
  // `_snmpPolyPoints` constants (w=420 hh=120) so card paddings &
  // axis labels stay calibrated.
  _drawerTimeDomain() {
    const rangeHours = Number(this.hostHistoryRange) || 1;
    const tMaxSec = Math.floor(Date.now() / 1000);
    const tMinSec = tMaxSec - (rangeHours * 3600);
    return {tMinSec, tMaxSec, w: 420, hh: 120};
  },
  // Auto-derive a "this is a gap" threshold (seconds) from the actual
  // sample cadence. Median Δt × 2.5 covers natural sampler jitter
  // (tick alignment / skew, occasional doubled ticks) but flags
  // genuine outage-class gaps. 60s floor so a fast sampler doesn't
  // false-positive on a one-tick hiccup. Used by every chart helper
  // to break the rendered line at long gaps so a multi-hour host
  // outage (power failure, network drop, manual shutdown) renders as
  // a visual discontinuity instead of one fake-smooth line bridging
  // the dead period. Provider-agnostic — works whether the series
  // came from Beszel (variable tier cadence), NE (5min), Ping
  // (configurable), or SNMP (5min default), because the threshold is
  // derived from the data itself rather than hard-coded per source.
  _detectGapThresholdSec(times) {
    if (!times || times.length < 3) {
      return null;
    }
    const deltas = [];
    let prev = 0;
    for (const t of times) {
      const ts = Number(t) || 0;
      if (!ts) {
        continue;
      }
      if (prev > 0) {
        const dt = ts - prev;
        if (dt > 0) {
          deltas.push(dt);
        }
      }
      prev = ts;
    }
    if (deltas.length < 2) {
      return null;
    }
    deltas.sort((a, b) => a - b);
    const median = deltas[Math.floor(deltas.length / 2)];
    return Math.max(60, median * 2.5);
  },
  _snmpPolyPoints(values, max, opts) {
    // null-aware. Skip-don't-synthesize: when a counter-rate
    // helper passes a null at a wrap / reboot / gap point, OMIT it
    // from the polyline points string instead of plotting it as 0.
    // CPU per-core / load polylines that fill empty slots with 0
    // still work because 0 IS a meaningful "load=0" value for those
    // series. Pre-fix the polyline string contained only the valid
    // points so the rendered line bridged across nulls — visually
    // identical to a steady ramp, hiding the gap. Most chart cards
    // now use `_snmpPathGapped` for the SVG `<path d>` attribute
    // (M commands at every gap so genuine outages render as breaks
    // instead of bridges). This helper stays for the legacy
    // `<polyline points>` consumers (CPU per-core / load) which
    // never emit nulls.
    //
    // unified time-domain — when `opts.times` (parallel array
    // of epoch SECONDS) is supplied, x is computed against the
    // drawer-shared [tMin, tMax] window so this chart's pixel
    // coordinates match every other chart in the open drawer. When
    // `times` is absent, falls back to the legacy index-based
    // scaling for un-migrated callers.
    if (!values || !values.length) {
      return '';
    }
    const m = max !== undefined ? max : Math.max(0.0001, ...values.filter(v => v != null));
    const n = values.length;
    const times = opts && opts.times;
    const dom = (times && times.length === n) ? this._drawerTimeDomain() : null;
    const w = dom ? dom.w : 420;
    const hh = dom ? dom.hh : 120;
    const span = dom ? Math.max(1, dom.tMaxSec - dom.tMinSec) : 1;
    const out = [];
    for (let i = 0; i < n; i++) {
      const v = values[i];
      if (v == null) {
        continue;
      }
      let x;
      if (dom) {
        const ts = Number(times[i]) || 0;
        if (!ts) {
          continue;
        }
        x = ((ts - dom.tMinSec) / span) * w;
        if (x < 0 || x > w) {
          continue;
        }
      } else {
        x = (i / Math.max(1, n - 1)) * w;
      }
      const y = hh - ((+v || 0) / m) * hh;
      out.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return out.join(' ');
  },
  // Gap-aware SVG path builder. Same scaling as `_snmpPolyPoints`
  // but emits an SVG path `d` string with `M` (moveto) at every gap
  // so a single `<path>` element renders as multiple disconnected
  // segments — genuine null gaps appear as visual breaks instead of
  // straight-line bridges. Cheaper than rendering N `<polyline>`
  // elements when the series has many gaps. Consumers swap their
  // `<polyline points="...">` for `<path d="...">` and bind the
  // result here.
  //
  // unified time-domain — same `opts.times` contract as
  // `_snmpPolyPoints`.
  _snmpPathGapped(values, max, opts) {
    if (!values || !values.length) {
      return '';
    }
    const m = max !== undefined ? max : Math.max(0.0001, ...values.filter(v => v != null));
    const n = values.length;
    const times = opts && opts.times;
    const dom = (times && times.length === n) ? this._drawerTimeDomain() : null;
    const gapThr = times ? this._detectGapThresholdSec(times) : null;
    const w = dom ? dom.w : 420;
    const hh = dom ? dom.hh : 120;
    const span = dom ? Math.max(1, dom.tMaxSec - dom.tMinSec) : 1;
    const out = [];
    let needMove = true;
    let prevTs = 0;
    for (let i = 0; i < n; i++) {
      const v = values[i];
      if (v == null) {
        // Null = gap. Next valid point starts a fresh sub-path.
        needMove = true;
        prevTs = 0;
        continue;
      }
      let x;
      let curTs = 0;
      if (dom) {
        curTs = Number(times[i]) || 0;
        if (!curTs) {
          needMove = true;
          prevTs = 0;
          continue;
        }
        x = ((curTs - dom.tMinSec) / span) * w;
        if (x < 0 || x > w) {
          needMove = true;
          prevTs = 0;
          continue;
        }
      } else {
        x = (i / Math.max(1, n - 1)) * w;
      }
      // Time-gap break — when consecutive valid samples are separated
      // by > gapThreshold seconds, emit M (moveto) instead of L
      // (lineto) so the rendered line breaks. Catches multi-hour host
      // outages where the underlying sampler simply stopped writing
      // rows for a stretch — pre-fix the line bridged the dead period
      // as a single fake-smooth segment, painting "down for hours" as
      // "fading from X to Y".
      if (!needMove && gapThr && prevTs > 0 && curTs > 0 && (curTs - prevTs) > gapThr) {
        needMove = true;
      }
      const y = hh - ((+v || 0) / m) * hh;
      out.push(`${needMove ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`);
      needMove = false;
      prevTs = curTs;
    }
    return out.join(' ');
  },
  // Min/Max/Last over a series field. Returns null when the
  // series is empty so the legend's `x-show` short-circuits to
  // hidden. Mirrors the shape of `hostMetricStats(...)` so the
  // template binding reads the same.
  snmpStats(hostId, key, idx) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    if (!series.length) {
      return null;
    }
    let pick;
    if (key === 'cpu_per_core' && typeof idx === 'number') {
      pick = (p) => (p.cpu_per_core || [])[idx];
    } else {
      pick = (p) => p[key];
    }
    const vals = series.map(pick).filter(v => v !== null && v !== undefined);
    if (!vals.length) {
      return null;
    }
    let min = Infinity, max = -Infinity;
    for (const v of vals) {
      if (v < min) {
        min = v;
      }
      if (v > max) {
        max = v;
      }
    }
    // Operator-reported on a Ubiquiti USW Enterprise switch:
    // drawer "Used X%" legend showed 100% while the row CPU bar
    // read 0%. Root cause: this helper used to return the last
    // NON-NULL value from `vals` (the filtered list), which on
    // hosts whose probes intermittently return CPU and then go
    // null can be a stale 100% from hours ago — disconnected
    // from the latest actual probe. The chart line correctly
    // plots fresh nulls as 0; the legend should match. Now scan
    // the FULL series back-to-front for the most recent
    // non-null point — this is "the most recent KNOWN value at
    // or before now" rather than "the last value we ever
    // observed". For all-non-null series the result is
    // identical to the previous behaviour. `lastIdx` exposed
    // alongside so callers can detect "last sample is stale"
    // (when `lastIdx < series.length - 1`).
    let last = null, lastIdx = -1;
    for (let i = series.length - 1; i >= 0; i--) {
      const v = pick(series[i]);
      if (v !== null && v !== undefined) {
        last = v;
        lastIdx = i;
        break;
      }
    }
    const stale = lastIdx >= 0 && lastIdx < series.length - 1;
    return {min, max, last, lastIdx, stale};
  },
  // Five evenly-spaced X-axis timestamp labels for the
  // bottom of the chart. Matches the existing `xAxisFromSeries`
  // call shape on Beszel / NE cards.
  //
  // — Switched to drawer-unified [tMin, tMax] window so SNMP
  // chart axis labels match Beszel / NE / Ping cards on the same
  // pixel positions. Pre-fix SNMP labels reflected actual sample
  // timestamps; post-fix they reflect the picker's selected range
  // (1h / 6h / 24h / 7d) anchored to "now".
  snmpXAxis(hostId, n) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    const ticks = n || 5;
    if (series.length < 2) {
      return Array(ticks).fill('');
    }
    const dom = this._drawerTimeDomain();
    const span = Math.max(1, dom.tMaxSec - dom.tMinSec);
    const out = [];
    for (let i = 0; i < ticks; i++) {
      const ts = dom.tMinSec + Math.round((i / (ticks - 1)) * span);
      out.push(this._fmtAxisTime(ts));
    }
    return out;
  },
  // _snmpFmtAxisTime was a parallel copy of `_fmtAxisTime` —
  // consolidated 2026-05-10 per user feedback ("cant we unify and
  // use generic helper for all charts to unify?"). Every chart card
  // in the drawer (Beszel / NE / Pulse / Webmin / Ping / SNMP host
  // / SNMP per-iface / SNMP per-temp probe) routes its X-axis label
  // formatting through `_fmtAxisTime` now. Drift-prevention: if a
  // future chart needs a different format, add an opts arg to
  // `_fmtAxisTime` rather than forking another `_xFmtAxisTime`.
  // CPU per-core lines — one polyline string per core index.
  snmpCpuPerCoreLines(hostId) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    if (!series.length) {
      return [];
    }
    // Determine core count from the FIRST point that has a non-empty
    // cpu_per_core. Older samples may have fewer cores (e.g. host
    // reboot changed core count); render only the consistent prefix.
    const numCores = (series.find(p => (p.cpu_per_core || []).length) || {}).cpu_per_core?.length || 0;
    const out = [];
    const times = series.map(p => p.ts);
    // The per-core map() closes over `i` via the loop variable. `let`
    // gives each iteration its own binding (so JSHint W083's classic
    // var-hoisting concern doesn't apply here), but JSHint flags the
    // shape regardless. Per-line ignore + we hand-verify the closure
    // captures the right `i` on every iteration.
    for (let i = 0; i < numCores; i++) {
      const idx = i;  // explicit per-iteration capture for JSHint
      const vals = series.map(p => (p.cpu_per_core || [])[idx] ?? 0);
      out.push(this._snmpPathGapped(vals, 100, {times}));
    }
    return out;
  },
  // Returns a SINGLE SVG path-d string with one subpath per core.
  // Each subpath starts with `M` (the gapped-path builder already
  // emits `M ... L ...`), so concatenating them produces a valid
  // path with N disconnected polylines. Avoids the `<template x-for>`
  // inside SVG where Alpine 3.x's x-for scope doesn't always
  // establish the iteration variable cleanly (browser HTML parsers
  // don't treat `<template>` as a real template element when it's
  // inside the SVG namespace, which can leave the inner directive
  // evaluated against the parent scope where the iteration var is
  // undefined).
  snmpCpuPerCoreCombinedLine(hostId) {
    const lines = this.snmpCpuPerCoreLines(hostId);
    return lines && lines.length ? lines.join(' ') : '';
  },
  snmpCpuUsedPctLine(hostId) {
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    const vals = series.map(p => p.cpu_used_pct ?? 0);
    const times = series.map(p => p.ts);
    return this._snmpPathGapped(vals, 100, {times});
  },
  // Operator-flagged: SNMP Load chart should render as % of cores
  // rather than raw `load_1m=0.18` numbers. Converts each load
  // value to a percentage via cores resolved from the host (live
  // `host_cpu_per_core` length OR cores) — falls back to 1 (treat
  // as single-core) when cores is unknown so behaviour matches
  // the pre-conversion chart for hosts that don't expose cores.
  // 100 % cap so a busy 4-core box (load=8 → 200%) still fits the
  // chart without auto-rescaling the Y-axis.
  snmpCoresFor(hostId) {
    const h = this.drawerHost && this.drawerHost.id === hostId ? this.drawerHost : null;
    if (h) {
      const c = (h.host_cpu_per_core || []).length || h.cpu_cores || h.cores;
      if (c && c > 0) {
        return c;
      }
    }
    const series = (this.hostSnmpHistory[hostId] || {}).points || [];
    for (const p of series) {
      const c = (p.cpu_per_core || []).length;
      if (c > 0) {
        return c;
      }
    }
    return 1;
  },
};
