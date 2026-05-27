// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,JSUnresolvedReference,JSIgnoredPromiseFromCall,ExceptionCaughtLocallyJS,OverlyComplexBooleanExpressionJS,ContinueStatementJS,RedundantIfStatementJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,EmptyCatchBlockJS,UnusedCatchParameterJS,IfStatementWithoutBlockJS
// noinspection FunctionWithMoreThanThreeNegationsJS,NegatedIfStatementJS,BreakStatementJS,JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS
// noinspection BadName,BadVariableName,JSAsyncFunctionMissingAwait,JSMissingAwait,JSUnfilteredForInLoop,PointlessBitwiseExpressionJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Hosts grid + per-host helpers — the rows + filters + sort
// + group bucketing of the Hosts view.

import {CURATED_REFRESH_FIELDS} from './app-curated-fields.js?v=__APP_VERSION__';

// Per-flush memo for filteredHosts(). The getter is read ~4× per
// reactive flush — groupedHosts() calls it on line 1 of its body
// (desktop AND mobile x-for both invoke groupedHosts), plus the two
// `filteredHosts().length` count bindings in index.html. Each call
// re-runs the full filter + sort + per-host hostEnabledAgents() walk
// (O(N) with per-host array allocations). The cache holds the result
// for exactly ONE synchronous flush and is dropped on the next
// microtask, so it can NEVER serve a stale list across a genuine state
// change: Alpine re-runs the binding on the next flush, by which point
// the cache is already cleared. Pure within-flush dedup, zero staleness
// risk — the safe "measured subset" of the hosts/settings perf work
// (a store migration would NOT help: Alpine reactivity is per-property
// fine-grained and Alpine.store() is the same deep Proxy, so the getter
// would re-run identically regardless of where `hosts` lives). Module-
// scope (non-reactive) so writing the cache can't re-trigger the effect
// that reads it. There is exactly one app() component instance, so a
// module-level singleton is correct.
let _filteredHostsFlushCache = null;
let _filteredHostsFlushScheduled = false;
function _clearFilteredHostsFlushCache() {
  _filteredHostsFlushCache = null;
  _filteredHostsFlushScheduled = false;
}

export default {
  providerStates(h) {
    if (!h) {
      return [];
    }
    const active = this.hostsActiveSources || [];
    const globalOk = this.providersWorkingGlobally();
    // Memoize against a fingerprint of every reactive input. Reading
    // the fields below DURING fingerprint construction means Alpine's
    // reactivity tracker still subscribes to each one, so any future
    // change to those fields re-fires the effect AND invalidates
    // the cache (fingerprint mismatch). Cache hit returns the SAME
    // array reference — downstream `<template x-for="p in ...">`
    // bindings see no change and skip the chip-DOM rebuild on every
    // poll-tick. Big win because providerStates is called per visible
    // host × every Alpine reactive tick (~10/s × 200 hosts = ~2k/s
    // pre-memo).
    const polling = (h._polling && typeof h._polling === 'object') ? h._polling : {};
    const pause = (h && h.provider_pause_state) || {};
    const got = new Set(h.providers || []);
    // Cheap fingerprint — string concat of every field providerStates
    // reads, no JSON.stringify (slow). The keys-filter on polling /
    // pause grabs only truthy entries so e.g. a fresh `{}` and a
    // `{snmp: false}` collapse to the same fp.
    const pollKeys = Object.keys(polling).filter(k => polling[k] === true).sort().join(',');
    const pauseKeys = Object.keys(pause).filter(k => pause[k] && pause[k].paused).sort().join(',');
    const gotKey = (h.providers || []).slice().sort().join(',');
    // Per-provider fingerprint contributions live on `_PROVIDER_DEFS`
    // (single source of truth, declared in `app.js`). Adding a new
    // provider auto-extends the memo key — no parallel edit here.
    const defs = (this._PROVIDER_DEFS || []);
    let perProviderFp = '';
    for (const def of defs) {
      perProviderFp += '|' + def.fpFields(h);
    }
    const fp = active.slice().sort().join(',') + '|'
      + (Array.from(globalOk).sort().join(',')) + '|'
      + gotKey + '|'
      + pollKeys + '|'
      + pauseKeys + '|'
      + (h._loading === true ? '1' : '0')
      + perProviderFp;
    // We also need to invalidate when the per-pause `consecutive_failures`
    // / `last_error` fields change — they're surfaced in the chip's
    // tooltip / pulse counter. Fold them into the fp.
    if (pauseKeys) {
      const pauseDetail = Object.keys(pause).filter(k => pause[k] && pause[k].paused).map(k => {
        const r = pause[k];
        return k + ':' + (r.consecutive_failures || 0) + ':' + (r.last_error || '');
      }).sort().join(';');
      // Append to the fingerprint so pause-row detail changes invalidate too.
      // (kept separate from the boolean-only pauseKeys check above for clarity)
      // The empty-string fallback keeps the fp shape stable when no pauses.
      const fpExtra = '|' + pauseDetail;
      // Concatenate into fp via shadowing — locals are write-once so we
      // build a final string just before the cache check.
      const finalFp = fp + fpExtra;
      const cache = this._providerStatesCache || (this._providerStatesCache = new Map());
      const cached = cache.get(h.id);
      if (cached && cached.fp === finalFp) {
        return cached.result;
      }
      return this._providerStatesCompute(h, active, globalOk, got, pause, polling, finalFp, cache);
    }
    // Fast-path when nothing is paused — skip the detail concat.
    const cache = this._providerStatesCache || (this._providerStatesCache = new Map());
    const cached = cache.get(h.id);
    if (cached && cached.fp === fp) {
      return cached.result;
    }
    return this._providerStatesCompute(h, active, globalOk, got, pause, polling, fp, cache);
  },
  _providerStatesCompute(h, active, globalOk, got, pause, polling, fp, cache) {
    const out = [];
    const badStatus = v => {
      const s = String(v || '').toLowerCase();
      return s === 'paused' || s === 'down' || s === 'unreachable';
    };
    // Row-level loading flag — true while `/api/hosts/one/{id}` is
    // in flight for this row OR before the first such call has
    // landed. We use it to flag "probe hasn't replied yet" as a
    // distinct state from "probe replied with no data" so the chip
    // doesn't render red (= broken) when the truth is "still
    // fetching". Replaced by the real per-provider state once data
    // lands.
    const rowLoading = h._loading === true;
    // Per-(provider, host) polling map populated by the
    // `host:provider_probing` / `host:provider_done` SSE events.
    // Even after the row's overall `_loading` flips false (because
    // the response landed for the providers that finished first),
    // a slow per-provider probe still in flight keeps `_polling[p]`
    // truthy. Chip pulses while EITHER row-loading OR its own
    // per-provider polling flag is set. `polling` is bound as a
    // parameter so the cache-key derivation in the parent
    // providerStates sees the same object reference we read here.
    const add = (name, mapped, selfStatus) => {
      if (!mapped) {
        return;
      }
      // The probe-result providers (http_probe / service_probe) aren't in
      // the `host_stats_source` CSV — they have their OWN master toggles
      // (http_probe_enabled / service_probe_enabled). `active` only carries
      // the CSV providers, so without this the probe-providers always read
      // as configured_inactive (muted). Treat them as active when their
      // master is on (hasHostStatsSource special-cases them), so a host
      // with apps shows a real Service-probe chip + stats like SNMP.
      const isActive = active.includes(name)
        || ((name === 'http_probe' || name === 'service_probe') && this.hasHostStatsSource(name));
      if (!isActive) {
        // Provider is mapped on this row but not active — the master
        // toggle in Admin → Providers is off, so the sampler isn't
        // probing. Surface a muted chip per-row so the gap is visible
        // (paired with the toolbar filter chip's warning surface for the
        // same condition). Without this branch the row chip stayed hidden
        // and a freshly-configured provider (URLs typed into the editor +
        // per-host enabled but master toggle never flipped) looked like
        // the chip was broken.
        out.push({
          name,
          state: 'configured_inactive',
          consecutive_failures: 0,
          last_error: '',
        });
        return;
      }
      // Per-(provider, host) auto-pause wins over every
      // other state — operator has explicitly marked this provider
      // off for this host until they manually resume it. Render the
      // 'paused' chip even when the provider isn't globally-OK so
      // the Resume button stays reachable.
      const pauseRow = pause[name];
      if (pauseRow && pauseRow.paused) {
        out.push({
          name,
          state: 'paused',
          consecutive_failures: Number(pauseRow.consecutive_failures || 0),
          last_error: String(pauseRow.last_error || ''),
          paused_at: Number(pauseRow.paused_at || 0),
        });
        return;
      }
      // Globally-broken provider — suppress the chip entirely so
      // operators see the failure once in Settings (not N times in
      // the Hosts grid). Exception: if THIS host got data from it,
      // the provider IS working — render the ok chip.
      if (!globalOk.has(name) && !got.has(name)) {
        return;
      }
      let state;
      if (!got.has(name)) {
        // Probe hasn't returned a hit for this provider yet. If the
        // row itself is still in flight OR this specific provider's
        // probe is currently in flight (per the SSE polling map),
        // it's not "failing" — it's pending. Render with a subtle
        // pulse animation in the provider's actual color via
        // `.chip-loading`.
        state = (rowLoading || polling[name] === true) ? 'loading' : 'failing';
      } else if (polling[name] === true) {
        // Provider previously hit BUT a fresh probe is currently
        // in flight (e.g. force-refresh, drawer reopen). Pulse the
        // chip to communicate "re-fetching" while keeping the chip
        // in its known-good colour rather than dropping back to
        // loading-grey.
        state = 'loading';
      } else if (badStatus(selfStatus)) {
        state = 'failing';
      } else {
        state = 'ok';
      }
      out.push({name, state});
    };
    // Iterate the canonical `_PROVIDER_DEFS` registry — single source
    // of truth for which providers render. Each def carries its own
    // `apiGate` (per-host config gate) + `apiStatus` (self-status
    // getter for the state machine — 'down' on hard failure, null
    // otherwise). Adding a new provider = ONE entry on `_PROVIDER_DEFS`
    // in app.js; this loop picks it up automatically. See CLAUDE.md
    // "SPA chip-rendering parity" for the canonical contract.
    for (const def of (this._PROVIDER_DEFS || [])) {
      add(def.name, def.apiGate(h), def.apiStatus(h));
    }
    // Stash the computed result keyed by host id; the parent
    // `providerStates` returns this same array reference on future
    // calls until the fingerprint changes.
    cache.set(h.id, {fp, result: out});
    return out;
  },

  // Host disk percent — real number when the exporter is available.
  // Returns 0 if not yet scraped so the bar collapses instead of
  // showing a misleading proportional-to-busiest-node number.
  hostDiskPercent(host) {
    const {hostDiskTotal, hostDiskUsed} = this.nodeStats(host);
    if (!hostDiskTotal) {
      return 0;
    }
    return Math.min(100, (hostDiskUsed / hostDiskTotal) * 100);
  },
  hostMemPercent(host) {
    const {hostMemTotal, hostMemUsed} = this.nodeStats(host);
    if (!hostMemTotal) {
      return 0;
    }
    return Math.min(100, (hostMemUsed / hostMemTotal) * 100);
  },
  // True when the host has at least 2 data points for `metric` so the
  // sparkline has something to draw. Drives the SVG's x-show gate so
  // dead / unloaded rows don't render an empty placeholder.
  hostHasInlineSpark(h, metric) {
    return !!this.hostInlineSparkline(h, metric);
  },
  // Threshold-tier class for a host-row sparkline — mirrors
  // `sparkClass(item, key)` used by stacks/services rows so a host
  // sparkline shares the green/amber/red colour with the stat-bar
  // sitting above it. Falls back to `muted` when telemetry is
  // missing so the line still renders (visible but neutral) instead
  // of inheriting the previous row's colour by mistake.
  hostSparkClass(h, metric) {
    if (!h) {
      return 'muted';
    }
    let v;
    if (metric === 'cpu') {
      v = h.cpu_percent;
    } else {
      if (metric === 'memory') {
        v = h.mem_percent || this.memPercentOf(h);
      } else {
        if (metric === 'disk') {
          v = h.disk_percent || this.diskPercentOf(h);
        } else {
          v = 0;
        }
      }
    }
    return this.barLevel(v);
  },
  // True when the named provider is enabled in
  // settings.host_stats_source (singular CSV string — "beszel,pulse,
  // node_exporter,webmin"). The host editor uses this to hide
  // per-row Beszel / Pulse / Webmin / NE fields whose global
  // provider is disabled — operators don't waste time configuring
  // mappings that won't be probed. Falls back to TRUE (show) when
  // settings haven't loaded yet so we don't strip fields prematurely.
  hostStatsSourceEnabled(name) {
    const raw = (this.settings && this.settings.host_stats_source) || '';
    if (!raw) {
      return true;
    }  // settings not loaded yet → show everything
    if (raw === 'none') {
      return false;
    }
    const parts = String(raw).split(',').map(s => s.trim()).filter(Boolean);
    return parts.includes(name);
  },
  // Quick predicate for the editor UI: "is there an asset-inventory
  // match for this row's custom_number?" — drives whether the
  // autofill button is shown vs hidden. Cheap lookup via the shared
  // `assetForHost` helper.
  hostRowHasAssetMatch(row) {
    if (!row || row.custom_number == null || row.custom_number === '') {
      return false;
    }
    return !!this.assetForHost({custom_number: row.custom_number});
  },
  groupedHosts() {
    const hosts = this.filteredHosts();
    const groups = this.hostGroups || [];
    const cacheKey = hosts.length + '|' + groups.length + '|' + (this.hostGroupsRevision || 0)
      + '|' + (this.hostsFilter || '') + '|' + (this.hideUnconfiguredHosts ? '1' : '0');
    const cached = this._groupedHostsCache;
    if (cached.key === cacheKey && cached.value) {
      return cached.value;
    }

    const all = groups.slice().sort(
      (a, b) => (a.order || 0) - (b.order || 0) || a.name.localeCompare(b.name),
    );
    const topLevel = all.filter(g => !g.parent_name);
    const subByParent = new Map();
    for (const g of all) {
      if (!g.parent_name) {
        continue;
      }
      if (!subByParent.has(g.parent_name)) {
        subByParent.set(g.parent_name, []);
      }
      subByParent.get(g.parent_name).push(g);
    }
    const buckets = topLevel.map(g => ({
      group: g,
      hosts: [],
      children: (subByParent.get(g.name) || []).map(sg => ({
        group: sg, hosts: [],
      })),
    }));
    const ungrouped = {group: null, hosts: [], children: []};

    // Sorted-by-range-start index for binary search. Each entry is
    // `[range_start, range_end, bucketIdx]`. With 30 groups the
    // bisection over a sorted array is O(log N) per host vs the
    // naive O(N) linear scan; saves ~14k comparisons per render
    // with 500 hosts × 30 groups.
    const ranges = buckets
      .map((b, idx) => [b.group.range_start | 0, b.group.range_end | 0, idx])
      .sort((a, b) => a[0] - b[0]);
    const findBucket = (ci) => {
      // Binary search for the largest range whose start ≤ ci, then
      // walk back through any equal-start entries to find one whose
      // end ≥ ci. Falls back to a linear scan for overlapping ranges
      // (operator-defined ranges CAN overlap; first-match-wins
      // matches the prior implementation's iteration order). With
      // typical non-overlapping range sets the early-exit is hit on
      // the bisected entry without any walk.
      let lo = 0, hi = ranges.length;
      while (lo < hi) {
        const mid = (lo + hi) >>> 1;
        if (ranges[mid][0] <= ci) {
          lo = mid + 1;
        } else {
          hi = mid;
        }
      }
      // After the loop `lo` is the count of ranges with start ≤ ci.
      // Walk back through them looking for one whose end ≥ ci.
      for (let i = lo - 1; i >= 0; i--) {
        if (ranges[i][1] >= ci) {
          return ranges[i][2];
        }
      }
      return -1;
    };

    for (const h of hosts) {
      const cn = h.custom_number;
      const ci = (cn === null || cn === undefined || cn === '') ? null
        : (Number.isFinite(+cn) ? +cn : null);
      let placed = false;
      if (ci !== null) {
        const bIdx = findBucket(ci);
        if (bIdx >= 0) {
          const b = buckets[bIdx];
          // Parent matched — now try the sub-groups first
          // (most-specific wins). Break on the first sub-group
          // hit and DON'T also push to the parent's list. Children
          // tend to be a handful per parent so a linear scan here
          // is cheap (no need for a second bisection layer).
          let placedInChild = false;
          for (const c of b.children) {
            const cg = c.group;
            if (ci >= cg.range_start && ci <= cg.range_end) {
              c.hosts.push(h);
              placedInChild = true;
              break;
            }
          }
          if (!placedInChild) {
            b.hosts.push(h);
          }
          placed = true;
        }
      }
      if (!placed) {
        ungrouped.hosts.push(h);
      }
    }
    // Bucket filter — keep parents that have direct hosts OR a sub-group
    // with hosts. Empty parents (zero direct + zero contributing children)
    // are dropped. This matches the operator's preference: with "Hide
    // hosts without agents" ON, empty groups should DISAPPEAR (not show
    // headings of nothing). The previous `|| topLevel.length` clause was
    // a no-op (always truthy) — that's the bug that was keeping empty
    // top-level groups visible regardless of content.
    const out = buckets.filter(b =>
      b.hosts.length > 0
      || b.children.some(c => c.hosts.length > 0),
    );
    if (ungrouped.hosts.length > 0) {
      out.push(ungrouped);
    }
    cached.key = cacheKey;
    cached.value = out;
    return out;
  },
  // Bracketed type prefix for the Hosts-view host title — renders
  // as "[VM]" / "[PHY]" / "[CT]" before the display label so
  // operators can scan a long list and tell physical / virtual /
  // container hosts apart at a glance. Resolution: prefer the
  // asset's `Type.shortname` (compact 2-3 char code), fall back to
  // the long `Type.Name`, return '' when no asset / no type. Empty
  // result skips the prefix entirely so non-asset hosts stay clean.
  //
  // One-time debug: if we have a Type object with a long `type` but
  // Resolve the human-visible display name for a host row, used by
  // the Hosts grid + drawer header + every "open this host" toast.
  // Resolution order :
  // 1. Operator-set `h.label` from Admin → Hosts (highest priority)
  // 2. Asset inventory's `name` (asset.Name / asset.CalculatedName
  //    via `assetForHost(h).name`) — lets operators leave the
  //    display label blank and inherit a meaningful name from the
  //    asset record without typing it twice
  // 3. The Docker hostname `h.host`
  // 4. The curated row id `h.id` (last-resort)
  // Anywhere the UI used `h.label || h.host` it should call this
  // helper instead so the asset-fallback applies consistently.
  hostDisplayName(h) {
    if (!h) {
      return '';
    }
    const op = (h.label || '').toString().trim();
    if (op) {
      return op;
    }
    const asset = (typeof this.assetForHost === 'function') ? this.assetForHost(h) : null;
    const an = (asset && asset.name) ? String(asset.name).trim() : '';
    if (an) {
      return an;
    }
    return String(h.host || h.id || '').trim();
  },

  // --- Hosts view (Beszel-backed) ---
  // Two-phase loader (backend endpoints: /api/hosts/list + /api/hosts/one/{id}):
  // 1. /api/hosts/list — curated list + global state, no probes.
  //    Paints the table instantly with grey "…" status dots.
  // 2. Fan out /api/hosts/one/{id} per row (capped concurrency
  //    so a 30-host fleet doesn't flood the server with 30
  //    simultaneous Webmin+NE probes). Each response splices its
  //    row back into `this.hosts`, flipping _loading false and
  //    filling in stats. Alpine's proxy picks up the mutation.
  // IntersectionObserver-driven lazy fetch for host rows.
  // Called by each row's `x-init` (mobile + desktop templates) so
  // every mounted `[data-host-id]` element registers with a single
  // shared observer. On first intersection, the row's id lands in
  // `_hostSeenIds` and an immediate `refreshHostRow` fires to fill
  // the row's metrics. Re-observing the same element is a no-op
  // (IntersectionObserver dedupes internally). The observer's
  // `rootMargin` is generous (200px above + below) so rows about to
  // scroll into view start fetching slightly early — operators
  // don't see a "loading" flash mid-scroll.
  _ensureHostRowObserver() {
    if (this._hostRowObserver) {
      return this._hostRowObserver;
    }
    if (typeof IntersectionObserver === 'undefined') {
      return null;
    }
    // observer hits collect into a pending Set + debounce
    // by 200 ms before flushing. Rapid scroll past a long list
    // (e.g. 200-host fleet) coalesces into one queue load instead
    // of firing 50+ concurrent fetches in <500 ms (the prior
    // behaviour bypassed `tuning_hosts_parallel_fetch` entirely
    // because each observer entry called refreshHostRow directly).
    // The flush hands every queued id to `_runHostRefreshQueue`
    // which honours the SAME PARALLEL cap as loadHosts'
    // poll-driven fan-out.
    this._hostObserverPending = this._hostObserverPending || new Set();
    // Named method on `this` so SSE event handlers can also call
    // it directly to coalesce bursts through the SAME debounce +
    // shared worker pool. Pre-fix the flush was a closure-local
    // helper that the SSE path couldn't reach.
    this._scheduleHostObserverFlush = () => {
      if (this._hostObserverFlushTimer) {
        clearTimeout(this._hostObserverFlushTimer);
      }
      this._hostObserverFlushTimer = setTimeout(() => {
        this._hostObserverFlushTimer = null;
        const ids = [...(this._hostObserverPending || new Set())];
        this._hostObserverPending = new Set();
        if (!ids.length) {
          return;
        }
        for (const id of ids) {
          this._hostSeenIds.add(id);
        }
        this._runHostRefreshQueue(ids).catch(() => {
        });
        // Warm host history for visible rows so the inline 1h-trend
        // sparkline overlaid on each CPU / Mem / Disk stat-bar has
        // data to render without waiting for the operator to open
        // the drawer. One-shot per host — the loaders are no-ops
        // when history is already present and fresh. Off-screen
        // hosts never enter the observer's queue, so a 200-host
        // fleet doesn't pay the prefetch cost up-front.
        //
        // Two sources covered: (1) Beszel / NE history via
        // `loadHostHistory(beszel_id, host_id)` — feeds the
        // cpu / mp / dp series; (2) SNMP history via
        // `loadHostSnmpHistory(host_id)` for SNMP-monitored hosts
        // (switches / routers / printers) so those rows also get
        // CPU + Memory sparklines even when no Beszel agent / NE
        // exporter is configured.
        for (const id of ids) {
          const h = (this.hosts || []).find(x => x && x.id === id);
          if (!h) {
            continue;
          }
          // Beszel / NE / Pulse / Webmin prefetch. Gate accepts
          // either the post-probe `beszel_id` OR the curated
          // `beszel_name` (operator alias) — pre-fix the gate
          // required `beszel_id` which is only populated AFTER a
          // successful per-host /api/hosts/one/{id} probe lands.
          // For Beszel-only hosts that meant sparklines stayed
          // empty until the per-host probe completed, even though
          // the history time-series in `host_metrics_samples` was
          // already queryable. `loadHostHistory` itself accepts
          // an empty beszel_id and falls back to host_id-keyed
          // history (NE / Pulse / Webmin), so the broader gate is
          // safe.
          if (h.beszel_id || h.beszel_name || h.ne_url || h.pulse_name || h.webmin_name) {
            const key = this.hostHistoryKey(h);
            const cached = key && this.hostHistory && this.hostHistory[key];
            if (!cached
              || !Array.isArray(cached.series)
              || cached.series.length < 2) {
              try {
                this.loadHostHistory(h.beszel_id || '', h.id);
              } catch {
              }
            }
          }
          // SNMP prefetch — independent of Beszel / NE so a host with
          // ALL three providers gets both series loaded in parallel
          // and the helper picks whichever lands first. Loose gate
          // via `_snmpHasProbeTarget` so hosts with the sampler
          // running but no per-host UI checkbox ticked still
          // prefetch.
          if (this._snmpHasProbeTarget(h) && typeof this.loadHostSnmpHistory === 'function') {
            const snmpCached = this.hostSnmpHistory && this.hostSnmpHistory[h.id];
            if (!snmpCached
              || !Array.isArray(snmpCached.points)
              || snmpCached.points.length < 2) {
              try {
                this.loadHostSnmpHistory(h.id, 1);
              } catch {
              }
            }
          }
        }
      }, 200);
    };
    const handle = (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) {
          continue;
        }
        const id = entry.target && entry.target.getAttribute('data-host-id');
        if (!id) {
          continue;
        }
        if (this._hostSeenIds.has(id)) {
          continue;
        }
        this._hostObserverPending.add(id);
      }
      this._scheduleHostObserverFlush();
    };
    this._hostRowObserver = new IntersectionObserver(handle, {
      rootMargin: '200px 0px 200px 0px',
      threshold: 0,
    });
    return this._hostRowObserver;
  },

  async loadHosts(force = false) {
    this.hostsLoading = true;
    // Watchdog cap — mirrors `_runWithBusy`. If `fetch` itself hangs
    // (server unreachable, dead network), `await` never returns and
    // `finally` never runs; the topbar spinner would stay stuck on
    // Hosts view across the session. Watchdog clears `hostsLoading`
    // after `_LOAD_BUSY_MAX_MS` so the UI recovers on its own; the
    // hung fetch keeps running in the background and a later poll
    // recovers state once the network resumes.
    const _wd = setTimeout(() => {
        if (this.hostsLoading) {
          this.hostsLoading = false;
        }
      },
      this._LOAD_BUSY_MAX_MS || 30000);
    try {
      // `force=true` bypasses the backend's 10s `_host_provider_cache`
      // memo so a settings save → next loadHosts immediately reflects
      // the new provider state. Default polling path
      // stays cached.
      const url = force ? '/api/hosts/list?force=true' : '/api/hosts/list';
      const r = await fetch(url);
      if (!r.ok) {
        // Set the error flag but DON'T wipe the array — wholesale
        // replacement violates the in-place reconcile rule and
        // tears down every row's chart SVG on a transient HTTP
        // failure.
        // The next successful poll reconciles the existing rows in
        // place; until then, operators see the previous data with
        // a banner instead of a flicker-then-empty page.
        this.hostsError = `HTTP ${r.status}`;
        return;
      }
      const d = await r.json();
      this.hostsConfigured = !!d.configured;
      this.hostsError = d.error || '';
      this.hostsProviderErrors = d.provider_errors || {};
      this.hostsActiveSources = Array.isArray(d.active) ? d.active : [];
      // Defensive cleanup: drop stale provider entries from the
      // session-persisted filter set when they're no longer active
      // OR no longer configured on any host. Without this, a chip
      // that's supposed to be hidden can leave its `chip-active`
      // state lingering in sessionStorage from a previous tab
      // session — invisible until the next paint pass, and
      // operator-confusing if a downstream render mistakenly keeps
      // the button visible while the filter set still references it.
      try {
        if (this.hostsProviderFilter && this.hostsProviderFilter.size) {
          const active = new Set(this.hostsActiveSources || []);
          const cleaned = new Set();
          for (const name of this.hostsProviderFilter) {
            if (!active.has(name)) {
              continue;
            }
            if (this._hostsConfiguredForProvider
              && this._hostsConfiguredForProvider(name) === 0) {
              continue;
            }
            cleaned.add(name);
          }
          if (cleaned.size !== this.hostsProviderFilter.size) {
            this.hostsProviderFilter = cleaned;
            if (typeof sessionStorage !== 'undefined') {
              if (cleaned.size) {
                sessionStorage.setItem('hostsProviderFilter',
                  [...cleaned].join(','));
              } else {
                sessionStorage.removeItem('hostsProviderFilter');
              }
            }
          }
        }
      } catch (_) { /* defensive — never block the poll on cleanup */
      }
      // Background-refresh indicator. /api/hosts/list returns
      // `hub_probing: true` when it served snapshot rows instantly
      // and a fresh Beszel + Pulse hub probe is running in the
      // background. Drives the topbar refresh button's "Refreshing…"
      // pulse so the operator sees the system is working even
      // after the foreground call completed.
      this._setRefreshingFlag('hubProbing', d.hub_probing);
      // Merge with EXISTING rows to prevent the flicker that
      // happens when the 15s poll re-runs and resets every row to
      // the grey skeleton (hiding graphs / provider chips for a
      // second while refreshHostRow re-fetches). For each server
      // row, find the existing client row by id: if it had real
      // data, keep that data and just mark _loading=true (row stays
      // visually stable); if new, start from the skeleton.
      // Mutate in place to avoid wholesale array replacement —
      // replacing this.hosts causes Alpine to re-evaluate every
      // row's template, which re-computes chart SVGs and makes
      // graphs flicker every 15s poll. Instead, we:
      // 1. Find each existing row by id, UPDATE its fields in
      //    place (Alpine's proxy picks up each assignment so the
      //    DOM stays mounted).
      // 2. Append any new rows that don't exist yet.
      // 3. Remove any rows whose id disappeared from the response.
      const CURATED_FIELDS = [
        'label', 'icon', 'custom_number', 'url',
        'beszel_name', 'pulse_name', 'ne_url', 'webmin_name',
        'ssh_enabled', 'asset',
        // ping_enabled needs to flow through the skeleton
        // path so drawerHost.ping_enabled is truthy when the
        // operator first clicks a ping-enabled row. Otherwise the
        // openHostDrawer gate fails and loadHostPingHistory never
        // fires (chart stays "Collecting data…" forever).
        'ping_enabled',
        // SNMP target alias. Curated overlay so the per-host
        // chip in providerStates(h) renders correctly off the
        // skeleton row (before the per-host probe lands).
        // `snmp_enabled` (per-host opt-in flag) added so an
        // un-ticked save flips the chip off on the next refresh
        // instead of staying stuck on the previous `true` state.
        'snmp_name', 'snmp_enabled',
        // HTTP probe per-host opt-in flag + resolved URL list.
        // Same curated-overlay contract as snmp_enabled / ping_enabled
        // — flowing through the skeleton path means the http_probe
        // chip in providerStates(h) renders correctly off the first
        // /api/hosts/list response before any /api/hosts/one/{id}
        // call lands.
        'http_probe_enabled', 'http_probe_urls', 'http_probe_has_targets',
      ];
      const incoming = Array.isArray(d.hosts) ? d.hosts : [];
      const incomingIds = new Set(incoming.map(h => h.id));
      // A host with NO providers mapped (or running in a deploy with
      // every host-stats source disabled globally) has nothing to
      // probe — the backend already stamps such rows with
      // `status: 'unconfigured'` from `_shape_host_api_row`. Skipping
      // the per-host fetch here means: (a) the dot lands directly
      // on grey with no transient "loading" flash, (b) we don't
      // burn a /api/hosts/one/{id} round-trip for every dead row.
      const isUnconfigured = (h) => h && h.status === 'unconfigured';
      // 1+2: reconcile — update existing, append new.
      for (let i = 0; i < incoming.length; i++) {
        const h = incoming[i];
        const existing = (this.hosts || []).find(r => r.id === h.id);
        const skipProbe = isUnconfigured(h);
        if (existing) {
          // Existing row — overlay curated fields only. Deliberately
          // DO NOT flip `_loading` back to true on re-polls: the
          // previous data stays visible while refreshHostRow patches
          // stats in place, which is the entire point of the in-
          // place reconcile. Toggling `_loading = true` here caused
          // the status-dot template to flash from dot → spinner →
          // dot on every 15s poll cycle (most visible on paused /
          // down hosts whose red dot was the visual anchor).
          for (const k of CURATED_FIELDS) {
            if (k in h) {
              existing[k] = h[k];
            }
          }
          existing._seq = i;
          if (skipProbe) {
            existing.status = 'unconfigured';
            // Unconfigured rows skip the per-host probe entirely, so
            // make sure they aren't stuck on a stale spinner from a
            // previous configured-then-unmapped state.
            if (existing._loading) {
              existing._loading = false;
            }
          }
        } else {
          // Brand-new row — push the full skeleton so it renders.
          // Respect the backend's status when it's already populated —
          // /api/hosts/list now promotes status='up' on cold-load
          // when the snapshot fallback restored host_* runtime
          // fields (the _stale_fields branch in
          // _shape_host_api_row). Pre-fix this branch unconditionally
          // forced status='loading' for every new row, stomping the
          // backend's 'up' with the loading sentinel and hiding the
          // CPU / Mem / Disk bars (their gates require
          // h.status === 'up'). Now we keep whatever the backend
          // sent unless the row is unconfigured (no providers mapped
          // OR every provider disabled globally) or the backend left
          // status blank (legacy responses + first-ever boot with no
          // snapshot, where 'loading' is still the right initial
          // sentinel).
          this.hosts.push({
            ...h,
            _seq: i,
            _loading: !skipProbe,
            status: skipProbe ? 'unconfigured' : (h.status || 'loading'),
          });
        }
      }
      // 3: drop rows whose id is no longer present.
      for (let i = this.hosts.length - 1; i >= 0; i--) {
        if (!incomingIds.has(this.hosts[i].id)) {
          this.hosts.splice(i, 1);
        }
      }
      // Invalidate the `_hostsConfiguredForProvider` memoize cache —
      // the reconcile above just added / removed rows AND overlaid
      // every provider-relevant field on existing rows.
      if (typeof this._bumpHostsConfiguredVersion === 'function') {
        this._bumpHostsConfiguredVersion();
      }
      this.hostsCuratedCount = Number.isFinite(d.curated_count) ? d.curated_count : 0;
      this.hostsEnabledCount = Number.isFinite(d.enabled_count) ? d.enabled_count : 0;
      // Trim persisted expansion state to hosts that actually exist.
      const valid = new Set(this.hosts.map(h => h.host));
      const cleaned = (this.hostsExpanded || []).filter(n => valid.has(n));
      if (cleaned.length !== (this.hostsExpanded || []).length) {
        this.hostsExpanded = cleaned;
      }
      // Trim the bulk-selection set to hosts that still exist —
      // operator-deleted rows would otherwise stay counted in
      // selectedHostCount() and make the bulk bar render a stale
      // count badge until the operator clicks Clear. Bulk POSTs
      // would silently skip the dead id but the count miscount is
      // still confusing UX.
      if (this.selectedHosts && this.selectedHosts.size > 0) {
        let trimmed = 0;
        const next = new Set();
        for (const id of this.selectedHosts) {
          if (incomingIds.has(id)) {
            next.add(id);
          } else {
            trimmed += 1;
          }
        }
        if (trimmed > 0) {
          this.selectedHosts = next;
        }
      }
    } catch (e) {
      // Set the error flag but DON'T wipe the array — same rationale
      // as the HTTP-error branch above. A transient
      // network blip during the 15s poll shouldn't tear down every
      // row's mounted DOM; the next successful poll reconciles
      // in place. Operators see a banner with the existing rows
      // dimmed (visually) by the error chip instead of a flicker.
      this.hostsError = `Network: ${e.message}`;
      return;
    } finally {
      this.hostsLoading = false;
      try {
        clearTimeout(_wd);
      } catch (_) {
      }
      // First /api/hosts/list response landed (success OR error path).
      // Empty-state ladder is now allowed to render — see hostsInitialLoaded
      // gate in static/index.html.
      this.hostsInitialLoaded = true;
    }

    // Fan out per-host fetches. Concurrency cap prevents a 30-host
    // fleet from opening 30 sockets at once (Webmin probes in
    // particular hold connections for several seconds).
    // Unconfigured hosts (no providers mapped or all providers
    // disabled globally) are skipped — refreshHostRow would just
    // re-run the same backend logic that already stamped them
    // unconfigured in the LIST response. Saves a round trip per
    // dead row and prevents the dot from flashing yellow→grey.
    // for fleets larger than ~50 hosts the auto fan-out was
    // a perf cliff: 200 rows × per-probe time burned background
    // CPU + sockets even for off-screen rows the operator never
    // looks at. Filter the fan-out to hosts that the
    // IntersectionObserver-driven lazy fetcher has already
    // marked as "seen in viewport" (via `_hostSeenIds`). On first
    // load the set is empty, so this loop is a no-op; the observer
    // fires for in-viewport rows during initial paint and triggers
    // their first fetch directly. Subsequent 15s polls re-fetch
    // every previously-seen row to keep them fresh while the user
    // is on the page; off-screen rows that have never been viewed
    // never pay the probe cost. Saves dramatic bandwidth +
    // backend load on 200-host fleets.
    // Cleanup: drop seen-ids whose hosts have disappeared.
    const _validIds = new Set(this.hosts.map(h => h.id));
    this._hostSeenIds = new Set(
      [...(this._hostSeenIds || [])].filter(id => _validIds.has(id))
    );
    // Hand the queue to the SHARED worker pool — single source of
    // concurrency truth across polling, IO observer, and SSE event
    // handlers. Pre-fix this had its own worker pool independent
    // of `_runHostRefreshQueue`'s, so combined fan-out could
    // exceed the operator-set cap when both paths fired in the
    // same window.
    const queueIds = this.hosts
      .filter(h => h.status !== 'unconfigured' && this._hostSeenIds.has(h.id))
      .map(h => h.id);
    for (const id of queueIds) {
      this._enqueueHostRefresh(id);
    }
    // Fire-and-forget — page paints as workers complete; history
    // pre-fetch (below) runs in parallel with the per-host stat
    // fetches.
    this._ensureHostRefreshWorkers();
    // Don't await workers — page paints as they complete. But fire
    // the history pre-fetch for pre-expanded hosts right away so
    // the drawer chart populates in parallel with the per-host
    // stat fetches.
    for (const name of this.hostsExpanded || []) {
      const host = this.hosts.find(h => h.host === name);
      if (!host) {
        continue;
      }
      const key = this.hostHistoryKey(host);
      // Fire on Beszel-mapped hosts OR NE-only hosts (ne_url set).
      // Skipping NE-only hosts here was the bug that left the new
      // historical-charts path unreachable from the bulk-expand
      // entry point.
      if (key && (host.beszel_id || host.ne_url || host.pulse_name || host.webmin_name) && !this.hostHistory[key]) {
        this.loadHostHistory(host.beszel_id || '', host.id);
      }
    }
    // Inline-sparkline backfill — every visible host that has been
    // probed at least once (in `_hostSeenIds`) and has Beszel or NE
    // telemetry but no usable history yet gets a one-shot history
    // fetch on every poll cycle. The IntersectionObserver-driven
    // initial prefetch fires only on first-intersect; if that first
    // call returned an empty series (e.g. Beszel hub momentarily
    // unreachable at page load) the row's sparkline would never
    // populate without this retry. Skip when the cache already has
    // ≥2 points so we don't re-fetch every 15s for hosts whose
    // history is fresh. Same SNMP retry layered alongside.
    for (const id of (this._hostSeenIds || [])) {
      const host = this.hosts.find(h => h.id === id);
      if (!host) {
        continue;
      }
      if (host.beszel_id || host.ne_url || host.pulse_name || host.webmin_name) {
        const key = this.hostHistoryKey(host);
        const cached = key && this.hostHistory && this.hostHistory[key];
        if (!cached
          || !Array.isArray(cached.series)
          || cached.series.length < 2) {
          try {
            this.loadHostHistory(host.beszel_id || '', host.id);
          } catch {
          }
        }
      }
      if (this._snmpHasProbeTarget(host) && typeof this.loadHostSnmpHistory === 'function') {
        const snmpCached = this.hostSnmpHistory && this.hostSnmpHistory[host.id];
        if (!snmpCached
          || !Array.isArray(snmpCached.points)
          || snmpCached.points.length < 2) {
          try {
            this.loadHostSnmpHistory(host.id, 1);
          } catch {
          }
        }
      }
    }
    // Refresh the open drawer's chart history on every host-poll
    // tick. The `Updated Xs/m/h ago` freshness label
    // tracks the last successful chart fetch — without this hook
    // the label could read older than the user's host-poll cadence
    // because `loadHostHistory` was only invoked on drawer open
    // and on time-range button clicks. Skipped for the
    // `hostsExpanded` path above because that gate has a
    // `!this.hostHistory[key]` check (only fires the first time);
    // the drawer needs an UNCONDITIONAL re-fetch each tick so the
    // chart values + the freshness stamp stay in sync with the
    // host-list refresh interval.
    if (this.drawerHost
      && (this.drawerHost.beszel_id || this.drawerHost.ne_url || this.drawerHost.pulse_name || this.drawerHost.webmin_name)) {
      this.loadHostHistory(
        this.drawerHost.beszel_id || '',
        this.drawerHost.id,
      );
    }
  },

  // Fetch one host's merged stats and splice it back into the
  // hosts array. Preserves _seq and _loading handling so the UI
  // can distinguish "not yet loaded" from "probed but empty".
  async refreshHostRow(id, opts = {}) {
    // ``opts.force`` propagates to ``/api/hosts/one/{id}?force=true``.
    // Used after a Save in Admin → Hosts / Providers so a
    // re-opened drawer sees fresh provider data without waiting out
    // the 10s provider-state cache. Default false keeps the polling
    // path cheap.
    //
    // 504 back-off — if /api/hosts/one returned 504 in the last
    // ``_hostRow504BackoffMs`` window for this host, skip the call
    // entirely. Stops the console-error spam loop where the SNMP
    // probe budget is exceeded for a slow host (iDRAC / large
    // switch) and the SPA's IntersectionObserver / drawer poll
    // hammers the same endpoint every 30s. Operator-initiated
    // calls (force=true) bypass the back-off so a refresh button
    // always tries.
    this._hostRow504BackoffMs = this._hostRow504BackoffMs || 60000;
    this._hostRow504Until = this._hostRow504Until || {};
    const now = Date.now();
    if (!opts.force && this._hostRow504Until[id] && this._hostRow504Until[id] > now) {
      return;
    }
    try {
      const url = '/api/hosts/one/' + encodeURIComponent(id)
        + (opts && opts.force ? '?force=true' : '');
      const r = await fetch(url);
      if (!r.ok) {
        // Per-host back-off on 504 specifically — the upstream probe
        // budget was exceeded (slow SNMP, big OID set). Mark the
        // host with `_probe_timeout: true` so the row UI can render
        // a "probe slow" badge instead of the generic unknown
        // status. 5xx is treated the same way; 4xx clears the
        // back-off (genuine config error, not a transient slowness).
        if (r.status === 504 || r.status === 502 || r.status === 503) {
          this._hostRow504Until[id] = now + this._hostRow504BackoffMs;
        }
        const row = this.hosts.find(h => h.id === id);
        if (row) {
          row._loading = false;
          row._probe_timeout = (r.status === 504);
          row._probe_error = `HTTP ${r.status}`;
          row.status = 'unknown';
          // Clear any lingering per-provider polling flags — the
          // response failed so no `provider_done` events are coming.
          row._polling = {};
        }
        return;
      }
      // Successful probe — clear any back-off marker so the next
      // tick polls normally.
      delete this._hostRow504Until[id];
      const {host} = await r.json();
      if (!host) {
        return;
      }
      const row = this.hosts.find(h => h.id === id);
      if (!row) {
        return;
      }
      // In-place update: copy every field from the new host dict
      // into the existing row. Alpine's proxy picks up each
      // assignment individually, so the host's :key hasn't
      // changed — the template DOESN'T re-render from scratch,
      // which means embedded chart SVGs and provider pill rows
      // stay mounted. No flicker.
      //
      // the original
      // ``for (k of Object.keys(host)) row[k] = host[k]`` loop only
      // ASSIGNED keys present in the incoming dict, never deleted
      // keys absent from it. So any backend-side omission (provider
      // returns empty `host_temperatures` mid-session, transient DB
      // error trims a key from the response, etc.) left the previous
      // value sticky on the row. Fix: a CURATED_REFRESH_FIELDS
      // whitelist of probe-derived keys gets explicitly written —
      // missing keys in `host` collapse to ``null`` so the row
      // clears cleanly. CURATED_FIELDS-style flow (config / asset)
      // still uses the simple assign loop for whatever else the
      // backend chose to include.
      for (const k of CURATED_REFRESH_FIELDS) {
        row[k] = (host[k] === undefined) ? null : host[k];
      }
      for (const k of Object.keys(host)) {
        if (!CURATED_REFRESH_FIELDS.has(k)) {
          row[k] = host[k];
        }
      }
      row._loading = false;
      row._probe_timeout = false;
      row._probe_error = null;
      // Per-provider polling map is now stale — the response landed
      // with the authoritative state. Drop any lingering flags so a
      // SSE `provider_done` that was lost in transit (rare —
      // replica restart mid-probe) doesn't keep the chip pulsing
      // forever. Future probes will re-populate via fresh
      // `host:provider_probing` events.
      row._polling = {};
      // Invalidate the toolbar's per-provider count memoize — this
      // row may have flipped beszel_name / pulse_name / ne_url /
      // webmin_name / snmp_name / address / http_probe_has_targets
      // / ping_enabled / snmp_enabled / http_probe_enabled in the
      // overlay above.
      if (typeof this._bumpHostsConfiguredVersion === 'function') {
        this._bumpHostsConfiguredVersion();
      }
      // History backfill after first probe lands. The
      // IntersectionObserver-driven prefetch fires when the row
      // FIRST scrolls into view — but at that moment the row is
      // still a skeleton (curated-fields-only from /api/hosts/list)
      // and its `beszel_id` / `ne_url` / `pulse_name` /
      // `webmin_name` aren't populated yet, so the observer's
      // history-loader gate fails and skips. Operator-reported:
      // sparklines were absent on initial page load and only
      // appeared after opening + closing the drawer (which has
      // its own loadHostHistory call). Now that the per-host
      // probe has landed and the provider fields are present,
      // kick off the history fetch immediately if the cache is
      // still empty. Same gate the observer + 15s poll uses.
      try {
        if (row.beszel_id || row.ne_url || row.pulse_name || row.webmin_name) {
          const histKey = this.hostHistoryKey(row);
          const cached = histKey && this.hostHistory && this.hostHistory[histKey];
          if (!cached
            || !Array.isArray(cached.series)
            || cached.series.length < 2) {
            this.loadHostHistory(row.beszel_id || '', row.id);
          }
        }
        // Same backfill for SNMP — operator reported on a Cisco
        // SG300-28P switch that the row CPU bar + sparkline only
        // appeared after drawer-open-then-close. Same root cause
        // as the unix-style providers above: IntersectionObserver
        // fires loadHostSnmpHistory on first scroll-into-view but
        // the row was still a skeleton (snmp_enabled flag from
        // /api/hosts/list arrives before the per-host probe lands
        // — visibility gates depend on history existing AND
        // having a non-zero point, which the observer's first
        // fire might not have populated yet). Drawer-open had its
        // own loadHostSnmpHistory call (line ~15394) so opening
        // the drawer bridged the gap. Now that the per-host
        // probe just landed and `snmp_enabled` is reliably set,
        // kick off the SNMP history fetch immediately if the
        // cache is still empty / sparse.
        if (this._snmpHasProbeTarget(row) && typeof this.loadHostSnmpHistory === 'function') {
          const snmpCached = this.hostSnmpHistory && this.hostSnmpHistory[row.id];
          if (!snmpCached
            || !Array.isArray(snmpCached.points)
            || snmpCached.points.length < 2) {
            this.loadHostSnmpHistory(row.id, 1);
          }
        }
      } catch (_) { /* defensive — never block the probe path */
      }
    } catch (_) {
      // Network failure — leave the row in skeleton state so the
      // next loadHosts cycle retries. Silent (no toast): on a big
      // fleet the spam would be worse than the missing data.
    }
  },

  // toggle a provider in the Hosts-toolbar filter set.
  // Multi-select OR semantics. The synthetic 'none' name filters
  // to hosts without ANY provider mapped (curated rows that exist
  // for inventory but have no live data source).
  toggleHostsProviderFilter(name) {
    if (!name) {
      return;
    }
    const set = new Set(this.hostsProviderFilter || []);
    if (set.has(name)) {
      set.delete(name);
    } else {
      set.add(name);
    }
    this.hostsProviderFilter = set;
    try {
      if (typeof sessionStorage !== 'undefined') {
        if (set.size) {
          sessionStorage.setItem('hostsProviderFilter', [...set].join(','));
        } else {
          sessionStorage.removeItem('hostsProviderFilter');
        }
      }
    } catch (_) { /* private mode / quota — ignore */
    }
  },
  // Drawer mode : row is "expanded" when its host matches
  // the currently-open drawer. Used for chevron rotation +
  // hover-tint suppression on the source row, exactly as the
  // legacy inline-expansion behaved.
  isHostExpanded(name) {
    return !!(this.drawerHost && this.drawerHost.host === name);
  },
  // A host is "expandable" only when it's actually alive AND has
  // enough merged data to justify opening the detail cards. The
  // green dot (``status === 'up'``) is the primary signal; we
  // also require at least one provider to have matched so hosts
  // defined in Admin → Hosts but never hit don't appear
  // interactive (same visual feedback as a dead row).
  isHostExpandable(h) {
    if (!h) {
      return false;
    }
    // Admins can always expand — the drawer's debug panel is the
    // canonical tool for diagnosing "host not reporting any data"
    // and must be reachable even when the host is down or unmatched.
    if (this.me && this.me.role === 'admin') {
      return true;
    }
    // Asset-inventory match → expandable even without live data.
    // The drawer surfaces vendor / model / serial / interfaces /
    // ports from the cached <asset-api-host> row, so a host with NO live
    // providers (FTTH routers / 5G modems / etc. that nothing
    // scrapes) still has something worth opening.
    if (this.assetForHost(h)) {
      return true;
    }
    // Otherwise: any "up" host is expandable. The previous gate
    // ALSO required `h.providers.length > 0`, which rejected
    // node-exporter-only hosts where the providers field arrived
    // a tick later than the status. Status alone is the right
    // signal — a host whose provider replied with `ok` already
    // means there's data behind it.
    return h.status === 'up';
  },
  // --- Debug panel for a single host (admin-only) ---
  // Lazily fetches /api/hosts/debug and caches per host.id. Toggling
  // the panel open triggers the fetch on first use; later opens reuse
  // the cached snapshot (explicit Refresh clears it).
  async toggleHostDebug(hostId) {
    if (!hostId) {
      return;
    }
    const open = !this.hostsDebugOpen[hostId];
    this.hostsDebugOpen = {...this.hostsDebugOpen, [hostId]: open};
    if (open) {
      this._scrollHostSectionIntoView(`debug-${hostId}`);
      if (!this.hostsDebug[hostId] && !this.hostsDebugLoading[hostId]) {
        await this.loadHostDebug(hostId);
      }
    }
  },
  // Per-host Beszel services lazy-loader. Hits
  // `/api/hosts/{id}/beszel/services` and caches the per-unit
  // snapshot in `hostsBeszelServices[host_id]`. Used by the AI
  // palette pre-fetch (when the question contains service-related
  // keywords) and by the host-drawer per-service pane.
  async loadHostBeszelServices(hostId) {
    if (!hostId) {
      return;
    }
    this.hostsBeszelServicesLoading = {
      ...this.hostsBeszelServicesLoading, [hostId]: true,
    };
    try {
      const r = await fetch(
        '/api/hosts/' + encodeURIComponent(hostId) + '/beszel/services'
      );
      if (!r.ok) {
        this.hostsBeszelServices = {
          ...this.hostsBeszelServices,
          [hostId]: {error: `HTTP ${r.status}`, services: []},
        };
        return;
      }
      const d = await r.json();
      this.hostsBeszelServices = {
        ...this.hostsBeszelServices,
        [hostId]: {
          services: Array.isArray(d.services) ? d.services : [],
          error: d.error || '',
          loadedAt: Date.now(),
        },
      };
    } catch (e) {
      this.hostsBeszelServices = {
        ...this.hostsBeszelServices,
        [hostId]: {error: String(e), services: []},
      };
    } finally {
      this.hostsBeszelServicesLoading = {
        ...this.hostsBeszelServicesLoading, [hostId]: false,
      };
    }
  },
  // Filtered view for the Hosts table — search matches host id,
  // label, platform, OS, kernel, and provider names so an operator
  // can find a host by whatever field they remember. Sort order:
  // alive hosts first (green dot), then paused, then down/unknown,
  // each group alphabetical by label/id. Dead rows cluster at the
  // bottom so the top of the view is always the "interesting"
  // stuff.
  // True when the curated row has at least one provider name mapped —
  // i.e. SOMETHING will probe it. Inventory rows (no beszel_name /
  // pulse_name / ne_url / webmin_name) return false. Used both by
  // `filteredHosts()` for the hide-unconfigured filter and by the
  // toolbar count badge.
  hostHasAgent(h) {
    if (!h) {
      return false;
    }
    // SNMP gates on `snmp_enabled === true` per opt-in contract.
    // Counts as having an agent when EITHER `snmp_name` (provider-
    // specific override) is set OR the curated `address` field is
    // set — backend SNMP resolver falls back to `address` when
    // `snmp_name` is empty (chain: aliases → snmp_name → address
    // → SKIP). Pre-fix this gated only on snmp_name, so clearing
    // it (intending to inherit address) made the host appear
    // un-agented in the Hosts page filter + count even though
    // SNMP was actively probing.
    const hasSnmpTarget = h.snmp_enabled === true && (
      (h.snmp_name && String(h.snmp_name).trim())
      || (h.address && String(h.address).trim())
    );
    return !!(h.beszel_name || h.pulse_name || h.ne_url
      || h.webmin_name || h.ping_enabled
      || hasSnmpTarget);
  },
  hostHasCpuMetric(h) {
    if (!h) {
      return false;
    }
    if (this._hostUnixAgent(h)) {
      return true;
    }
    // DATA-FIRST gate — if the live merged row has a non-zero CPU%
    // value, the bar should render regardless of which provider
    // configuration produced it. Pre-fix the gate required
    // `h.snmp_name && h.snmp_enabled === true`, which hid the CPU
    // bar for SNMP-tracked hosts (HP printers, Ubiquiti APs, etc.)
    // whose backend `snmp_enabled` flag was false even though the
    // SNMP probe was running and `host_cpu_percent` was populated.
    // The data IS the strongest signal of capability — if the
    // backend stamped a value, the operator wants to see it.
    // EXCEPTION: snapshot-restored stale values aren't capability
    // signals — they're the LAST seen value from a since-orphaned
    // provider (e.g. host_cpu_percent on an APC UPS that briefly
    // reported via Beszel and now has no provider for it). Trusting
    // those would render a permanent stale bar that never updates.
    // When stale, fall through to the SNMP-history capability check.
    const cpuStale = this.isStaleField(h, 'host_cpu_percent');
    if (!cpuStale && Number(h.cpu_percent) > 0) {
      return true;
    }
    // Capability fallback for SNMP-tracked hosts at 0% (genuine
    // idle vs. agent-doesn't-expose). Defers to SNMP history,
    // which writes NULL when the agent didn't expose host_cpu_percent
    // and a real 0.0 when the agent reported 0. Routed through the
    // same `_snmpHasProbeTarget` strict-opt-in gate that the chip /
    // chart-mount surfaces use, so this stays consistent.
    if (this._snmpHasProbeTarget(h)) {
      return this._hostHasFiniteSnmpHistory(h, 'cpu');
    }
    return false;
  },
  hostHasMemMetric(h) {
    if (!h) {
      return false;
    }
    if (this._hostUnixAgent(h)) {
      return true;
    }
    // Data-first: any host with a non-zero mem_total has capability,
    // regardless of provider config. Same stale exception as the
    // CPU gate above. SNMP fallback uses the same strict
    // `_snmpHasProbeTarget` gate (snmp_enabled === true AND a
    // resolvable target via snmp_name OR address) for consistency
    // with the chip / chart-mount surfaces.
    const memStale = this.isStaleField(h, 'host_mem_total') || this.isStaleField(h, 'host_mem_used');
    if (!memStale && (+h.mem_total || 0) > 0) {
      return true;
    }
    if (this._snmpHasProbeTarget(h)) {
      return this._hostHasFiniteSnmpHistory(h, 'memory');
    }
    return false;
  },
  hostHasDiskMetric(h) {
    if (!h) {
      return false;
    }
    if (this._hostUnixAgent(h)) {
      return true;
    }
    // Data-first: any disk_total or disk_percent > 0 = capability.
    // Same stale exception as the CPU / memory gates above. Disk
    // history isn't currently surfaced by SNMP samples so there's
    // no SNMP-history fallback — when stale and no other signal,
    // the bar hides cleanly until a live provider populates it.
    const diskStale = this.isStaleField(h, 'host_disk_total') || this.isStaleField(h, 'host_disk_used');
    if (diskStale) {
      return false;
    }
    const diskTot = +h.disk_total || 0;
    const diskPct = +h.disk_percent || 0;
    if (diskTot > 0 || diskPct > 0) {
      return true;
    }
    return false;
  },
  // Outer container gate: ANY of the three axes has data. Used
  // by the bars-grid wrapper to mount/unmount the whole block;
  // each individual bar inside ALSO gates on its own axis so
  // a CPU-only host doesn't render empty Mem + Disk bars.
  hostHasTelemetry(h) {
    if (!h) {
      return false;
    }
    return this.hostHasCpuMetric(h) || this.hostHasMemMetric(h) || this.hostHasDiskMetric(h);
  },
  filteredHosts() {
    // Per-flush memo — see _filteredHostsFlushCache at module scope.
    if (_filteredHostsFlushCache !== null) {
      return _filteredHostsFlushCache;
    }
    const q = (this.hostsSearch || '').trim().toLowerCase();
    const statusWeight = (s) => {
      switch ((s || '').toLowerCase()) {
        case 'up':
          return 0;
        case 'paused':
          return 1;
        case 'down':
          return 2;
        default:
          return 3;
      }
    };
    const nameOf = (h) => this.hostDisplayName(h).toLowerCase();
    // Group key for 'type' sort — prefer platform over os so
    // Proxmox/LXC rows cluster distinctly from plain Debian, etc.
    // Empty values sort LAST ('~' > any printable letter).
    const typeOf = (h) => {
      const t = ((h.platform || h.os || '') + '').toLowerCase().trim();
      return t || '~';
    };
    const num = (v) => (Number.isFinite(+v) ? +v : 0);

    let list = (this.hosts || []).slice();
    if (this.hostsHideUnconfigured) {
      list = list.filter(h => this.hostHasAgent(h));
    }
    // provider filter (toolbar chips). Empty set = show all
    // (status quo). Otherwise OR-match across the selected provider
    // names; the synthetic 'none' name matches hosts that have NO
    // provider field configured, so operators can isolate
    // inventory-only rows.
    if (this.hostsProviderFilter && this.hostsProviderFilter.size) {
      const filt = this.hostsProviderFilter;
      const wantNone = filt.has('none');
      list = list.filter(h => {
        if (wantNone && !this.hostHasAgent(h)) {
          return true;
        }
        // hostEnabledAgents() returns ALL configured provider fields
        // on the row — bare alias presence (beszel_name / pulse_name /
        // ...) counts even when the live probe hasn't returned yet,
        // so the filter survives transient probe failures.
        const agents = (this.hostEnabledAgents(h) || []).map(a => a.name);
        for (const a of agents) {
          if (filt.has(a)) {
            return true;
          }
        }
        return false;
      });
    }
    // Problem-hosts filter — toggled via the toolbar "Problem (N)"
    // chip AND the Stats dashboard "Problem hosts" tile. Filters to
    // the same status set the Telegram AI context's `problem_hosts`
    // block carries (down / paused / unknown). `unconfigured` rows
    // are intentionally NOT included — they're inventory-only by
    // design, not outages.
    if (this.hostsProblemFilter) {
      list = list.filter(h => this.isProblemHost(h));
    }
    if (q) {
      list = list.filter(h => {
        // Asset-record fields layered into the haystack so a host
        // whose display label is auto-derived from the asset
        // inventory (operator hasn't set an explicit label) still
        // matches when the operator types the asset's stored
        // name / vendor / model / serial / location / type. Walk
        // EVERY enumerable string-ish key on the asset so any
        // field — including ones added later (asset_tag, ip,
        // notes, location_path, …) — is searchable without
        // re-listing them here.
        const asset = this.assetForHost ? (this.assetForHost(h) || null) : null;
        const assetFields = [];
        if (asset && typeof asset === 'object') {
          for (const k in asset) {
            const v = asset[k];
            if (v == null) {
              continue;
            }
            if (typeof v === 'string' || typeof v === 'number') {
              assetFields.push(String(v));
            }
          }
        }
        const hay = [
          h.host, h.label, h.id,
          h.platform, h.os, h.kernel,
          h.custom_number,
          h.beszel_name, h.pulse_name, h.snmp_name, h.webmin_name,
          h.url, h.icon,
          h.cpu_model, h.model, h.vendor, h.serial,
          ...(h.providers || []),
          ...assetFields,
        ].filter(v => v !== null && v !== undefined && v !== '')
          .join(' ')
          .toLowerCase();
        // Multi-word AND search — every whitespace-separated token
        // must appear somewhere in the haystack, in any order. Pre-
        // fix the query was treated as one substring, so typing
        // "cisco switch" missed a host with asset.name="Cisco SG300
        // 52-Port Gigabit PoE Managed Switch" because the literal
        // "cisco switch" doesn't appear continuously. Splitting on
        // whitespace and requiring every token to hit lets the
        // operator type any combination of words from the host's
        // various fields and find it.
        const tokens = q.split(/\s+/).filter(Boolean);
        return tokens.every(t => hay.includes(t));
      });
    }

    const sortKey = (this.hostsSort || 'status');
    // Every branch breaks ties via (status → name) so the result is
    // deterministic regardless of the array's incoming order.
    const tieBreak = (a, b) => {
      const sw = statusWeight(a.status) - statusWeight(b.status);
      if (sw !== 0) {
        return sw;
      }
      return nameOf(a).localeCompare(nameOf(b));
    };

    let cmp;
    switch (sortKey) {
      case 'seq':
      case 'insertion':
        // Addition order — falls back to name when _seq is missing
        // (older server response without the stamp).
        cmp = (a, b) => {
          const d = num(a._seq) - num(b._seq);
          if (d !== 0) {
            return d;
          }
          return nameOf(a).localeCompare(nameOf(b));
        };
        break;
      case 'custom_number':
        // Operator-assigned catalogue number. Hosts without a number
        // (null / blank) sort LAST so unnumbered machines cluster at
        // the bottom and don't compete with ordered entries.
        cmp = (a, b) => {
          const ax = (a.custom_number == null || a.custom_number === '') ? Number.POSITIVE_INFINITY : num(a.custom_number);
          const bx = (b.custom_number == null || b.custom_number === '') ? Number.POSITIVE_INFINITY : num(b.custom_number);
          if (ax !== bx) {
            return ax - bx;
          }
          return nameOf(a).localeCompare(nameOf(b));
        };
        break;
      case 'name':
        cmp = (a, b) => nameOf(a).localeCompare(nameOf(b));
        break;
      case 'type':
        cmp = (a, b) => {
          const d = typeOf(a).localeCompare(typeOf(b));
          if (d !== 0) {
            return d;
          }
          return tieBreak(a, b);
        };
        break;
      case 'cpu':
        cmp = (a, b) => num(b.cpu_percent) - num(a.cpu_percent) || tieBreak(a, b);
        break;
      case 'mem':
        cmp = (a, b) => num(b.mem_percent) - num(a.mem_percent) || tieBreak(a, b);
        break;
      case 'disk':
        cmp = (a, b) => num(b.disk_percent) - num(a.disk_percent) || tieBreak(a, b);
        break;
      case 'uptime':
        cmp = (a, b) => num(b.uptime_s) - num(a.uptime_s) || tieBreak(a, b);
        break;
      case 'status':
      default:
        cmp = tieBreak;
        break;
    }
    list.sort(cmp);
    // Stash for the rest of THIS flush; drop on the next microtask so
    // the next reactive flush recomputes against fresh state.
    _filteredHostsFlushCache = list;
    if (!_filteredHostsFlushScheduled) {
      _filteredHostsFlushScheduled = true;
      queueMicrotask(_clearFilteredHostsFlushCache);
    }
    return list;
  },
  // True when this host has SNMP data worth charting (per-core CPU
  // OR load avg OR buffers/cached). Drives the gate on the new
  // chart cards so non-SNMP hosts don't see them.
  hostHasSnmpCharts(h) {
    if (!h) {
      return false;
    }
    // SNMP CPU / Load / Memory charts are SUPPRESSED when
    // the host also has Beszel or node-exporter enabled. Both
    // providers carry the same data with a smoother time-series
    // surface AND their own existing chart cards (CPU % / Memory %
    // / Disk % / Load avg) below. Showing the SNMP cards too
    // produced redundant CPU + Memory bars that disagreed with
    // the Beszel/NE values (SNMP's hrProcessorLoad is a 5s
    // average; ssCpuIdle uses snmpd's accounting interval; both
    // are coarser than Beszel/NE's per-tick deltas). Keep the
    // SNMP cards EXCLUSIVE to SNMP-only hosts (managed switches,
    // UPSes, NVRs, embedded routers without an OmniGrid agent).
    if (h.beszel_id || h.ne_url || h.pulse_name || h.webmin_name) {
      return false;
    }
    // also return true when historical SNMP samples exist
    // for this host even before the LIVE probe has populated
    // `host_*` fields. Lets the chart cards render the
    // last-known series during the 10-20s probe window instead
    // of staying blank, with a freshness label so the operator
    // can see how stale the displayed data is.
    const hist = this.hostSnmpHistory[h.id];
    if (hist && Array.isArray(hist.points) && hist.points.length > 0) {
      return true;
    }
    // / also return true when the host reports printer
    // page-count OR IF-MIB net counters (printers / switches /
    // routers without CPU / memory MIBs). Page-count gate requires
    // a real printer signature (supplies / console msg / non-zero
    // count) — APC UPSes and routers can answer prtMarkerLifeCount
    // with 0, which previously triggered the printer chart on
    // non-printer gear.
    const supplies = h.printer_supplies || [];
    const looksLikePrinter = (Array.isArray(supplies) && supplies.length > 0)
      || !!(h.printer_console_msg && String(h.printer_console_msg).trim())
      || ((+h.printer_page_count || 0) > 0);
    if (looksLikePrinter && h.printer_page_count != null) {
      return true;
    }
    if (h.host_net_rx_total_bytes != null || h.host_net_tx_total_bytes != null) {
      return true;
    }
    // — APC UPS hosts (Smart-UPS, Back-UPS, etc.) often expose
    // PowerNet-MIB load / battery / temperature OIDs but neither
    // hrStorage NOR IF-MIB. Without this branch the SNMP chart grid
    // wrapper hid for basic UPS models, taking the new UPS chart
    // cards down with it. Live values arriving on the API row is
    // enough to open the grid; the per-card x-show then decides
    // whether each individual card renders.
    if (typeof h.host_load_percent === 'number'
      || typeof h.host_battery_percent === 'number'
      || typeof h.host_battery_temp_c === 'number'
      || (h.host_ups_status && String(h.host_ups_status).trim())) {
      return true;
    }
    // phase 3 — Dell server hosts whose only SNMP surface is
    // the temperatureProbeTable (no CPU / mem / IF-MIB; the iDRAC's
    // standard MIB-II is locked down). Live host_dell_temps OR
    // historical temp samples is enough to mount the grid wrapper.
    if (Array.isArray(h.host_dell_temps) && h.host_dell_temps.length) {
      return true;
    }
    if (this.dellHasTempHistory && this.dellHasTempHistory(h.id)) {
      return true;
    }
    if ((h.host_cpu_per_core || []).length > 0
      || h.host_load_1m || h.host_load_5m || h.host_load_15m
      || h.host_mem_buffers || h.host_mem_cached) {
      return true;
    }
    // Final fallback: any SNMP-targeted host opens the grid so the
    // chart cards mount immediately on drawer-open. Each card's own
    // x-show still gates on data presence — empty cards just render
    // their "Collecting data..." placeholder until samples accumulate.
    // Loose gate (`_snmpHasProbeTarget`) so hosts whose sampler is
    // running via `snmp_name` / `address` but whose per-host UI
    // checkbox isn't ticked still mount the grid.
    if (this._snmpHasProbeTarget(h)) {
      return true;
    }
    return false;
  },
  // True only when we KNOW the named NE collector is missing for this
  // host (sampler walked the window and never saw a non-null value).
  // Returns false for Beszel hosts (no `ne_url`), Beszel+NE hybrids
  // (history fetched via Beszel path → no collectors dict), and
  // freshly-loaded hosts whose first /api/hosts/history reply hasn't
  // landed yet. Drives the Disk I/O / Network "enable the collector"
  // empty-state branches in the host drawer.
  hostCollectorMissing(h, name) {
    if (!h || !h.ne_url) {
      return false;
    }
    const key = this.hostHistoryKey(h);
    const c = this.hostHistory[key] && this.hostHistory[key].collectors;
    if (!c) {
      return false;
    }
    return c[name] === false;
  },

  // Permanent-fail tracking helpers. Backend sets
  // `h.sampling_paused: true` on the host record once consecutive
  // probe failures exceed the configured window
  // (`tuning_host_permanent_fail_window_seconds`). Frontend renders an
  // icon in the table + a banner in the drawer with a Resume button.
  hostFailureMinutes(h) {
    if (!h || !h.failure_window_started_at) {
      return 0;
    }
    const elapsed = (Date.now() / 1000) - h.failure_window_started_at;
    return Math.max(0, Math.floor(elapsed / 60));
  },
  // render "last probe N seconds/minutes/hours ago" so the
  // operator can decide whether to wait or hit Resume on a paused
  // host whose actual outage may have already cleared. Reads
  // ``last_failure_ts`` populated by the sampler on every
  // _record_failure tick. Returns null when the host has no
  // failure-state row (host has never failed) so the banner copy
  // is omitted entirely.
  hostLastFailureAge(h) {
    if (!h || !h.last_failure_ts) {
      return null;
    }
    const elapsed = Math.max(0, Math.floor((Date.now() / 1000) - h.last_failure_ts));
    if (elapsed < 60) {
      return this.t('hosts_extra.permanent_fail.last_error_age_seconds', {seconds: elapsed});
    }
    if (elapsed < 3600) {
      return this.t('hosts_extra.permanent_fail.last_error_age_minutes', {minutes: Math.floor(elapsed / 60)});
    }
    return this.t('hosts_extra.permanent_fail.last_error_age_hours', {hours: Math.floor(elapsed / 3600)});
  },
  // Window-aggregated packet-loss for the Ping chart's loss chip.
  // Pre-fix the chip read `h.ping_loss_pct` directly — that field
  // reflects the LATEST single probe's loss only, which is
  // meaningless when the operator is looking at a 24h / 7d window
  // (the chip claimed the whole-window loss but described one tick).
  // Window-correct definition: of the samples we have IN the window,
  // how many were `alive=false`? `loss% = down_count / received_count
  // × 100`. Missing samples (sampler not running, OmniGrid down,
  // host outage where the sampler couldn't write rows) count as
  // "no data" — NOT 100% loss — so a multi-hour OmniGrid outage
  // followed by recovery shows 0% over a window where every received
  // sample is alive=true. Returns null when there are no samples
  // (chip hides via the `> 0` gate).
  hostPingWindowLoss(systemId) {
    const entry = this.hostHistory[systemId];
    if (!entry || !entry.series || !entry.series.length) {
      return null;
    }
    let total = 0, down = 0;
    for (const r of entry.series) {
      total++;
      if (r && r.alive === false) {
        down++;
      }
    }
    if (!total) {
      return null;
    }
    return Math.round(100 * down / total);
  },
  // -------------------- Drift-from-baseline indicator --------------------
  // Reads the backend-stamped `h.drift` dict and returns a small
  // `{indicator, title, tone}` descriptor for one metric, or null when
  // no baseline exists. ``indicator`` is the ▲/▼/━ glyph; ``tone``
  // drives the CSS chip colour (`drift-above` / `drift-below` /
  // `drift-normal`); ``title`` is the localised hover-title with the
  // median + IQR + sample-count detail so the operator can verify
  // the baseline state without opening the drawer.
  //
  // Metric keys: 'cpu_pct' | 'mem_pct' | 'disk_pct' | 'ping_rtt_ms'.
  // Returns null when (a) `h.drift` is missing/empty, (b) the metric
  // isn't in the dict (insufficient samples / degenerate IQR), or
  // (c) the indicator field is missing — caller hides the chip.
  // Canonical accessor for the baseline metric roster. Reads from
  // `/api/me`'s `client_config.baseline_metrics` so the SPA iterates
  // the API contract instead of a hardcoded list. Falls back to the
  // four metrics that shipped originally — keeps the call-site
  // working on a stale `me` payload or during the brief init window
  // before `/api/me` lands.
  hostBaselineMetrics() {
    const fromApi = ((this.me && this.me.client_config) || {}).baseline_metrics;
    if (Array.isArray(fromApi) && fromApi.length) {
      return fromApi;
    }
    return ['cpu_pct', 'mem_pct', 'disk_pct', 'ping_rtt_ms'];
  },
  hostDriftIndicator(h, metric) {
    if (!h || !metric) {
      return null;
    }
    const d = h.drift;
    if (!d || typeof d !== 'object') {
      return null;
    }
    const m = d[metric];
    if (!m || !m.indicator) {
      return null;
    }
    const ind = String(m.indicator);
    const tone = ind === '▲' ? 'drift-above'
      : ind === '▼' ? 'drift-below'
        : 'drift-normal';
    // Localised hover-title — operator sees the median + IQR detail
    // and the sample count so they know the baseline isn't being
    // computed from 3 stray samples. `t()` resolves the
    // i18n key with the metric name + numeric fills.
    const formatVal = (v) => {
      if (v === null || v === undefined || !Number.isFinite(+v)) {
        return '—';
      }
      if (metric === 'ping_rtt_ms') {
        return (+v).toFixed(1) + ' ms';
      }
      return (+v).toFixed(1) + '%';
    };
    const liveLabel = formatVal(m.value);
    const medLabel = formatVal(m.median);
    const iqrLabel = formatVal(m.iqr);
    const titleKey = 'hosts_extra.drift.title_' + tone.replace('drift-', '');
    let title = (ind === '▲' ? 'Above baseline'
        : ind === '▼' ? 'Below baseline'
          : 'Within baseline')
      + ` — current ${liveLabel}, median ${medLabel}, IQR ±${iqrLabel}`
      + ` (n=${m.sample_count || 0})`;
    if (typeof this.t === 'function') {
      const tr = this.t(titleKey, {
        live: liveLabel, median: medLabel, iqr: iqrLabel,
        count: m.sample_count || 0,
      });
      if (tr && tr !== titleKey) {
        title = tr;
      }
    }
    return {indicator: ind, tone, title};
  },
  async loadHostTriage(hostId) {
    const id = (hostId || '').toString();
    if (!id) {
      return;
    }
    const hours = this.hostTimelineRange[id] || 168;
    const existing = this.hostTriage[id] || {};
    this.hostTriage[id] = {...existing, loading: true, error: null};
    try {
      const r = await fetch(
        '/api/hosts/' + encodeURIComponent(id) + '/triage?hours=' + hours,
        {credentials: 'same-origin'},
      );
      if (!r.ok) {
        const detail = await r.text().catch(() => '');
        throw new Error('HTTP ' + r.status + (detail ? ': ' + detail.slice(0, 200) : ''));
      }
      const data = await r.json();
      this.hostTriage[id] = {
        groups: Array.isArray(data.groups) ? data.groups : [],
        scope: data.scope || {hours},
        loading: false,
        error: data.error || null,
        loadedAt: Date.now(),
      };
    } catch (e) {
      this.hostTriage[id] = {
        ...(this.hostTriage[id] || {}),
        loading: false,
        error: (e && e.message) ? e.message : 'triage fetch failed',
      };
    }
  },
  setHostTimelineRange(hostId, hours) {
    const id = (hostId || '').toString();
    const h = Math.max(1, Math.min(720, parseInt(hours, 10) || 168));
    if (!id) {
      return;
    }
    this.hostTimelineRange[id] = h;
    // Clear cache so the new range is honoured immediately.
    delete this.hostTimeline[id];
    delete this.hostTriage[id];
    this.loadHostTimeline(id, true);
    this.loadHostTriage(id);
  },
  async loadHostTimeline(hostId, _force) {
    const id = (hostId || '').toString();
    if (!id) {
      return;
    }
    const hours = this.hostTimelineRange[id] || 168;
    // Mark loading flag without clearing the existing event list so
    // the operator sees stale-then-fresh rather than a flash of
    // empty during refetch.
    const existing = this.hostTimeline[id] || {};
    this.hostTimeline[id] = {
      ...existing,
      loading: true,
      error: null,
      hours,
    };
    try {
      const r = await fetch(
        '/api/hosts/' + encodeURIComponent(id) + '/timeline?hours=' + hours,
        {credentials: 'same-origin'},
      );
      if (!r.ok) {
        const detail = await r.text().catch(() => '');
        throw new Error('HTTP ' + r.status + (detail ? ': ' + detail.slice(0, 200) : ''));
      }
      const data = await r.json();
      this.hostTimeline[id] = {
        events: Array.isArray(data.events) ? data.events : [],
        counts: data.counts || {ops: 0, notifications: 0, failures: 0, recoveries: 0},
        loading: false,
        error: null,
        loadedAt: Date.now(),
        hours,
      };
    } catch (e) {
      this.hostTimeline[id] = {
        ...(this.hostTimeline[id] || {}),
        loading: false,
        error: (e && e.message) ? e.message : 'timeline fetch failed',
        hours,
      };
    }
  },
  hostTimelineKindLabel(kind) {
    const k = (kind || '').toString();
    const key = 'host_drawer.timeline.kind_' + k;
    const tr = this.t(key);
    if (tr && tr !== key) {
      return tr;
    }
    // Fallback when i18n key is missing — humanise the enum.
    return k.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
  },
  hostTimelineKindChipClass(kind) {
    switch ((kind || '').toString()) {
      case 'op':
        return 'pill-info';
      case 'notification':
        return 'pill-warning';
      case 'provider_paused':
        return 'pill-error';
      case 'provider_recovered':
        return 'pill-ok';
      case 'port_scan':
        return 'pill-info';
      default:
        return 'pill-muted';
    }
  },
  hostTimelineIconRef(kind, severity) {
    // Per-kind icons distinct enough to read at a glance in a busy
    // timeline — operator can spot a paused-provider entry without
    // hovering for the title.
    const sev = (severity || 'info').toString();
    const k = (kind || '').toString();
    if (k === 'op') {
      return 'icon-history';
    }
    if (k === 'notification') {
      return 'icon-bell';
    }
    if (k === 'provider_paused') {
      return 'icon-pause';
    }
    if (k === 'provider_recovered') {
      return 'icon-check';
    }
    if (k === 'port_scan') {
      return 'icon-search';
    }
    // Unknown kind — fall back on severity for forward-compat.
    if (sev === 'success') {
      return 'icon-activity';
    }
    if (sev === 'error') {
      return 'icon-bug';
    }
    return 'icon-info';
  },
  hostTimelineTimeLabel(ts) {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) {
      return '';
    }
    // Routes through fmtDate so the timeline picks up the user's
    // Formats preference (Settings → Profile → Formats). Default
    // remains the previous dd/MM/yyyy, HH:mm:ss when no override.
    return this.fmtDate(n);
  },
  async _hostsBulkPost(path, payload, successMsgKey, opts = {}) {
    const ids = this.selectedHostsArray();
    if (ids.length === 0) {
      return;
    }
    const body = {host_ids: ids, ...(payload || {})};
    const headers = {'Content-Type': 'application/json'};
    // Caller may have minted a reauth token via `_mintReauthToken`
    // for the destructive bulk-pause endpoint. Empty string =
    // SSO user (backend bypasses the gate); null/undefined = no
    // gate required (recoverable endpoints).
    const reauthToken = (opts && typeof opts.reauthToken === 'string') ? opts.reauthToken : null;
    if (reauthToken) {
      headers['X-OmniGrid-Reauth-Token'] = reauthToken;
    }
    try {
      const r = await fetch('/api/hosts/bulk/' + path, {
        method: 'POST',
        headers,
        credentials: 'same-origin',
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = data && (data.detail || data.error)
          || ('HTTP ' + r.status);
        this.showToast(this.t('hosts_extra.bulk.error', {error: detail}) || detail, 'error');
        return data;
      }
      const appliedIds = Array.isArray(data.applied) ? data.applied : [];
      const applied = appliedIds.length;
      const errors = Object.keys(data.errors || {}).length;
      const skipped = (data.skipped || []).length;
      // Partial-failure / mixed-result toast — surface every
      // category in the summary so the operator can tell at a
      // glance which is which after a 50-host bulk action.
      // Backend always returns {applied, skipped, errors}; the
      // SPA used to show only the first two as "X applied, Y
      // errors" and silently dropped the skipped count.
      if (errors > 0 || skipped > 0) {
        const tone = errors > 0 ? 'warning' : 'success';
        const msg = this.t('hosts_extra.bulk.breakdown',
            {applied, errors, skipped})
          || `${applied} applied · ${skipped} skipped · ${errors} error${errors === 1 ? '' : 's'}`;
        this.showToast(msg, tone);
      } else {
        const msg = this.t(successMsgKey || 'hosts_extra.bulk.success', {applied})
          || (applied + ' hosts updated');
        this.showToast(msg, 'success');
      }
      // Progressive UI feedback — mark each applied row so the
      // per-row check glyph + the summary badge both render
      // immediately. Clears after 5 s via a single shared timer.
      // In-place mutation only (Alpine reactive-array rule).
      const appliedSet = new Set(appliedIds.map(String));
      if (Array.isArray(this.hosts)) {
        for (const row of this.hosts) {
          if (row && appliedSet.has(String(row.id))) {
            row._bulkApplied = true;
          }
        }
      }
      this.bulkAppliedSummary = {
        applied,
        total: ids.length,
        action: path,
        ts: Date.now(),
      };
      if (this._bulkAppliedTimer) {
        clearTimeout(this._bulkAppliedTimer);
      }
      this._bulkAppliedTimer = setTimeout(() => {
        if (Array.isArray(this.hosts)) {
          for (const row of this.hosts) {
            if (row && row._bulkApplied) {
              row._bulkApplied = false;
            }
          }
        }
        this.bulkAppliedSummary = null;
        this._bulkAppliedTimer = null;
      }, 5000);
      // Force a refresh so the row state reflects the change.
      // Fire-and-forget — the bulk-applied toast has already been
      // committed via setTimeout above and the caller doesn't need
      // to wait on the reload before returning `data` to its caller.
      // Explicit `.catch(() => {})` makes the ignored-promise
      // intent clear AND silences IDE missing-await warnings.
      if (typeof this.loadHosts === 'function') {
        this.loadHosts(true).catch(() => undefined);
      }
      return data;
    } catch (e) {
      this.showToast(
        this.t('hosts_extra.bulk.error', {error: (e && e.message) || 'request failed'})
        || 'Bulk action failed',
        'error',
      );
    }
  },
  // Segmented-bar helper — percent-full of a single mount, used as
  // the INNER fill width inside an equal-width slot. Each mount
  // gets its own flex-1 slot in the bar; within that slot, the
  // fill's width reflects that mount's own percent full. This
  // makes small-capacity mounts (e.g. /boot at 252 MB on a 939 GB
  // pool) visible — their slot takes the same share of the bar as
  // a multi-TB /. Previously the bar used "this mount's share of
  // the whole pool" for width, which hid any small partition at
  // sub-pixel widths.
  //
  // Prefers the provider-supplied `dp` (percent full) when present;
  // falls back to computing from GiB floats (.du / .d) so mounts
  // missing a pre-computed percent still render correctly.
  // Tooltip for the disk stat-bar on the Hosts row. Shows the
  // aggregate disk%, the absolute used / total bytes, AND a per-
  // mount breakdown when there's more than one — operators
  // hovering see exactly which mount(s) are full without
  // opening the drawer. Worst-mount callout fires when any
  // mount is over the warn threshold so a critical mount
  // surfaces in the tooltip even when the aggregate is low
  // (dd-wrt's 100%-full squashfs `/` is invisible in the
  // aggregate but shows up here as "WORST: / at 100%").
  hostDiskBarTitle(h) {
    if (!h) {
      return '';
    }
    const aggPct = h.disk_percent || this.diskPercentOf(h);
    const parts = [
      this.t('columns.disk') + ': ' + this.fmtPercentLabel(aggPct),
    ];
    if (h.disk_total) {
      parts.push('(' + this.fmtBytes(h.disk_used) + ' / ' + this.fmtBytes(h.disk_total) + ')');
    }
    const mounts = Array.isArray(h.mounts) ? h.mounts : [];
    if (mounts.length > 1) {
      let worst = null;
      for (const m of mounts) {
        const fp = this.mountFillPercent(m);
        if (worst == null || fp > this.mountFillPercent(worst)) {
          worst = m;
        }
      }
      if (worst && this.mountFillPercent(worst) > this._statBarWarnPct()) {
        parts.push('— Worst: ' + (worst.n || worst.mountpoint || '?')
          + ' ' + Math.round(this.mountFillPercent(worst)) + '%');
      }
      const lines = mounts.map(m => '  ' + (m.n || m.mountpoint || '?')
        + ' · ' + Math.round(this.mountFillPercent(m)) + '%');
      parts.push('\n' + lines.join('\n'));
    }
    if (this.isStaleField(h, 'host_disk_total') || this.isStaleField(h, 'host_disk_used')) {
      parts.push('— ' + this.staleAge(h));
    }
    return parts.join(' ').replace(' \n', '\n');
  },
};
