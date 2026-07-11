// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,JSUnusedGlobalSymbols,JSUnusedLocalSymbols,ElementNotExported
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName
// noinspection OverlyComplexBooleanExpressionJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression,NegatedIfStatementJS
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSIfStatementsCanBeSimplified,IfStatementSimplifyable,RedundantIfStatementJS,RedundantLocalVariableJS,JSReusedLocalVariable
// noinspection HtmlUnknownTag,HtmlEmptyContent,HtmlEmptyTagsRecommendation,InnerHTMLJS,VoidExpressionJS,JSVoidExpression
// noinspection RegExpRedundantEscape,AnonymousCapturingGroupJS,RegExpAnonymousGroup,JSDeprecatedSymbols,JSPotentiallyInvalidUsageOfThis,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML
// Comprehensive per-inspection suppressions mirror app-ai-admin.js.
// Same SPA-wide rationale as the sibling SSE module.
/* global Alpine, Swal, I18N, t */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA SSE late handlers + node items + bulk-palette helpers
//
// SPLIT FROM `app-sse-stream.js`. Cross-method `this.X` references keep
// working through the `_mergeKeepDescriptors` chain in app.js.

// memo topologyGroups(item) per item.placements ARRAY REFERENCE. The
// Stacks/Services templates read it 2-3x per item per flush (x-show length +
// x-for source + x-if), and it allocated a fresh array every call -> the
// `<template x-for>` re-diffed even when placements were unchanged. _reconcileById
// assigns the incoming placements array on each items refresh (fresh ref ->
// busts), and the ref is stable between refreshes (-> hits). Reactivity-safe
// with NO freeze risk: the lookup reads item.placements to compute the key, so
// the binding subscribes to it even on a cache hit (unlike a no-arg getter
// memo). WeakMap auto-GCs when an item's placements array is replaced/dropped.
const _topologyGroupsMemo = new WeakMap();

// memo sortedHostApps(host) per host.apps ARRAY REFERENCE — same shape +
// reactivity-safety rationale as _topologyGroupsMemo above. The Hosts view
// reads it from 3 bindings per host per flush and it allocated a fresh
// `[...apps].sort(...)` every call, so the keyed `<template x-for>` re-diffed
// even when the apps strip was unchanged. The in-place host reconcile
// (refreshHostRow) replaces the apps array when it changes (fresh ref ->
// busts) and keeps the ref stable between refreshes (-> hits); the lookup
// reads host.apps to key, so the binding still subscribes on a cache hit
// (no freeze). WeakMap auto-GCs when a host's apps array is replaced/dropped.
const _sortedHostAppsMemo = new WeakMap();

// Per-flush memo for _sortRows() — the admin tables (sortedUsers/Sessions/
// Tokens) + backups/schedules call it as their x-for source every flush,
// re-allocating + re-sorting a copy each time. Keyed on the rows array ref
// (sub-checked by col+dir); rows reconcile in place so the ref is stable
// within a flush and busts on wholesale reload. Cleared on the next
// microtask. (ADMIN-PERF-10.)
let _sortRowsFlushCache = null;
let _sortRowsFlushScheduled = false;

export default {
  itemSubline(item) {
    // Node hostname is rendered by the topology chip strip below,
    // not here — avoids duplicating the information in two places.
    const bits = [];
    if (item.type) {
      bits.push(item.type);
    }
    if (item.stack) {
      bits.push(item.stack);
    }
    if (item.state && item.state !== 'running') {
      bits.push(item.state);
    }
    return bits.join(' · ');
  },
  canUpdate(item) {
    if (!item) {
      return false;
    }
    if (item.type === 'orphan') {
      return false;
    }
    if (item.stack_id) {
      return true;
    }
    if (item.type === 'container') {
      return true;
    }
    return false;
  },
  actionLabel(item) {
    if (item.status !== 'update') {
      return '—';
    }
    if (item.stack_id) {
      return this.t('actions.update_stack');
    }
    if (item.type === 'container') {
      return this.t('actions.recreate');
    }
    return this.t('actions.no_stack');
  },
  isSelectable(item) {
    // Selectable if updatable, restartable (service/container), or removable.
    if (item.status === 'update' && this.canUpdate(item)) {
      return true;
    }
    if (item.removable) {
      return true;
    }
    if (item.type === 'service' || item.type === 'container') {
      return true;
    }
    return false;
  },
  isRestartable(item) {
    return item && (item.type === 'service' || item.type === 'container');
  },
  sortBy(field) {
    if (this.sortField === field) {
      this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      this.sortField = field;
      this.sortDir = 'asc';
    }
  },
  sortIndicator(field) {
    if (this.sortField !== field) {
      return '';
    }
    return this.sortDir === 'asc' ? '▲' : '▼';
  },
  // WAI-ARIA `aria-sort` value resolver. Returns 'ascending' /
  // 'descending' for the active sort column, 'none' otherwise. Bind
  // on the `<th>` (NOT the inner button) so screen-reader users hear
  // sort direction alongside the column name. Generic over any
  // (currentField, currentDir) pair so AI / Stats / fleet sortable
  // tables can all reuse it without duplicating the ternary.
  _sortAria(field, currentField, currentDir) {
    if (currentField !== field) {
      return 'none';
    }
    return currentDir === 'asc' ? 'ascending' : 'descending';
  },
  // Shared sort-direction indicator. Returns ' ▲' / ' ▼' / '' so
  // any sortable table can show a unicode caret next to the column
  // header without dragging in an SVG asset. Bind via x-text on a
  // sibling span so the header text stays in i18n while the
  // indicator is purely visual.
  _sortIndicator(field, currentField, currentDir) {
    if (currentField !== field) {
      return '';
    }
    return currentDir === 'asc' ? ' ▲' : ' ▼';
  },
  // Shared sort-toggle helper. Mutates the sortObj `{col, dir}`
  // in place: clicking the active column flips direction; clicking
  // a different column resets to descending (matches the AI tab's
  // ergonomics — operators usually want newest / largest first).
  _sortToggle(sortObj, col) {
    if (!sortObj || !col) {
      return;
    }
    if (sortObj.col === col) {
      sortObj.dir = (sortObj.dir === 'asc') ? 'desc' : 'asc';
    } else {
      sortObj.col = col;
      sortObj.dir = 'desc';
    }
  },
  // Stable mixed-type comparator. Numbers compare as numbers;
  // numeric strings (digits / dots / sign / exponent only) are
  // promoted to numbers so "100" sorts after "9"; everything else
  // compares as a string. Nulls (and empty strings) sink to the
  // bottom regardless of direction so partial datasets don't
  // disrupt sort ergonomics. Mirrors `_aiSortValue` in app-ai.js
  // — could consolidate later, but the AI module ships with its
  // own copy to stay independently loadable.
  _sortValue(row, col) {
    if (!row || !col) {
      return null;
    }
    const v = row[col];
    if (v == null || v === '') {
      return null;
    }
    if (typeof v === 'number') {
      return v;
    }
    if (typeof v === 'string' && /^[\d.\-+eE]+$/.test(v)) {
      const n = Number(v);
      if (Number.isFinite(n)) {
        return n;
      }
    }
    return String(v);
  },
  // Generic sort over a `{col, dir}` SortObj. Returns the same
  // array reference when no sort is active (col is empty) so
  // Alpine's reactive bindings don't rebuild the DOM on every poll
  // tick when nothing changed; produces a new sorted copy when
  // sort IS active.
  _sortRows(rows, sortObj) {
    if (!sortObj || !sortObj.col || !Array.isArray(rows) || rows.length < 2) {
      return rows || [];
    }
    const col = sortObj.col;
    const dir = (sortObj.dir === 'asc') ? 1 : -1;
    // Per-flush memo (see _sortRowsFlushCache decl): reuse the sorted copy
    // while the rows ref + sort spec are unchanged within the flush.
    if (_sortRowsFlushCache === null) {
      _sortRowsFlushCache = new Map();
      if (!_sortRowsFlushScheduled) {
        _sortRowsFlushScheduled = true;
        queueMicrotask(() => {
          _sortRowsFlushCache = null;
          _sortRowsFlushScheduled = false;
        });
      }
    }
    const cached = _sortRowsFlushCache.get(rows);
    if (cached && cached.col === col && cached.dir === dir) {
      return cached.result;
    }
    const out = rows.slice();
    out.sort((a, b) => {
      const av = this._sortValue(a, col);
      const bv = this._sortValue(b, col);
      if (av == null && bv == null) {
        return 0;
      }
      if (av == null) {
        return 1;
      }
      if (bv == null) {
        return -1;
      }
      if (av < bv) {
        return -1 * dir;
      }
      if (av > bv) {
        return 1 * dir;
      }
      return 0;
    });
    _sortRowsFlushCache.set(rows, {col, dir, result: out});
    return out;
  },
  toggleStack(name) {
    if (this.expanded.includes(name)) {
      this.expanded = this.expanded.filter(n => n !== name);
    } else {
      this.expanded = [...this.expanded, name];
    }
  },
  expandAllStacks() {
    this.expanded = this.filteredStacks.map(s => s.name);
  },
  collapseAllStacks() {
    this.expanded = [];
  },
  toggleSelectAll() {
    const selectable = this.filteredItems.filter(i => this.isSelectable(i));
    if (this.selected.length === selectable.length) {
      this.selected = [];
    } else {
      this.selected = selectable.map(i => i.id);
    }
  },
  selectAllVisible() {
    this.selected = this.filteredItems.filter(i => this.isSelectable(i)).map(i => i.id);
  },
  selectUpdatesOnly() {
    this.selected = this.filteredItems
      .filter(i => this.isSelectable(i) && i.status === 'update' && this.canUpdate(i))
      .map(i => i.id);
  },
  clearSelection() {
    this.selected = [];
  },
  clearFilters() {
    this.search = '';
    this.statusFilter = '';
    this.healthFilter = '';
  },
  topologyGroups(item) {
    // Returns [{node, chips: [{state, err}, …]}, …] for rendering the
    // node + coloured-dot strip. Placements with a synthetic fallback
    // node ("local" / "?") are dropped — the strip would just show
    // noise for single-node setups where no real hostname was
    // resolved. Empty result => caller hides the strip.
    if (!item || !Array.isArray(item.placements) || !item.placements.length) {
      return [];
    }
    // serve from the per-placements memo (see _topologyGroupsMemo).
    // Reading item.placements here doubles as the reactive subscription, so
    // the x-for/x-show bindings still re-run when placements change.
    const placements = item.placements;
    const hit = _topologyGroupsMemo.get(placements);
    if (hit !== undefined) {
      return hit;
    }
    const by = new Map();
    for (const p of placements) {
      const node = p.node || '?';
      if (node === 'local' || node === '?') {
        continue;
      }
      if (!by.has(node)) {
        by.set(node, []);
      }
      by.get(node).push(p);
    }
    const out = Array.from(by.entries()).map(([node, chips]) => ({node, chips}));
    _topologyGroupsMemo.set(placements, out);
    return out;
  },
  // i18n-aware topology pill tooltips. Pre-fix
  // both the Stacks and Services views inlined `:title="group.node
  // + ' — ' + group.chips.length + ' replica' + (count===1 ? '' :
  // 's')"` — the JS template-literal i18n leak that travels with
  // helper-reuse (the Services view's copy duplicated the
  // pre-existing Stacks tooltip). Plural concatenation never
  // translates cleanly. Singular / plural pick at call-time so
  // languages with non-binary plural rules can extend via locale-
  // override.
  topologyNodeTooltip(group) {
    const count = (group && group.chips && group.chips.length) || 0;
    const node = (group && group.node) || '';
    // Reuses existing topology.node_title / node_title_many keys
    // (added pre-fix for a different consumer; the i18n bundle
    // already covers the singular/plural split). Pluralization
    // picks at call-time so non-binary plural locales can extend
    // their bundle without touching JS.
    const key = count === 1 ? 'topology.node_title' : 'topology.node_title_many';
    return this.t(key, {node, count});
  },
  topologyChipTooltip(chip) {
    const state = (chip && chip.state) || 'unknown';
    const err = chip && chip.err ? String(chip.err) : '';
    // i18n-translated state label (chip.state is one of the Swarm
    // task-state strings — translate via a known-state key family;
    // unknown values fall through to the raw enum). When an error
    // string is present, append it inline.
    const stateKey = `topology.state_${state.replace(/-/g, '_')}`;
    let stateLabel = this.t(stateKey);
    if (stateLabel === stateKey) {
      stateLabel = state;
    }
    return err
      ? this.t('topology.chip_tooltip_with_error', {state: stateLabel, err})
      : stateLabel;
  },

  // ---- Command Palette (Cmd-K / Ctrl-K) ---------------------------
  // Universal "go anywhere" search dropping the operator into any
  // drawer / view / setting. Pure-client, fuzzy-matched against the
  // existing data shapes (this.hosts / this.items / this.stacks)
  // plus a static admin-route map and the hotkeys list. Activation
  // dispatches by the result's `kind`: host → openHostDrawer, item
  // → openItemDrawer, admin → setAdminTab + view='admin', view →
  // setView, hotkey → no-op (info only). Results are scored:
  // exact-id match wins, prefix beats substring, and the displayed
  // label gets a small bonus over secondary fields.
  openCommandPalette() {
    // Defensive: each step wrapped so a failure in $nextTick /
    // input.focus() can't prevent the state flip from landing.
    // The capture-phase keydown handler relied on this method;
    // an unhandled throw inside `this.$nextTick` (e.g. when the
    // method is invoked from a non-Alpine context where $nextTick
    // isn't defined) used to swallow the entire palette open.
    try {
      this.commandPaletteOpen = true;
    } catch (e) {
      console.error('[cmdpal] open: state flip failed', e);
    }
    try {
      this.commandPaletteQuery = '';
      this.commandPaletteSelectedIdx = 0;
    } catch {
    }
    // Bulk-mode exclusion set is per-session; clear it on every
    // open so a previous run's deselections don't bleed into the
    // next bulk action.
    try {
      this.commandPaletteBulkExcluded = new Set();
    } catch {
    }
    // Focus the input on the next tick (after Alpine renders the
    // x-show / :style branch). Without rAF the input isn't in the
    // DOM yet and the focus call no-ops.
    try {
      const tick = (this && typeof this.$nextTick === 'function')
        ? this.$nextTick.bind(this)
        : (fn) => requestAnimationFrame(fn);
      tick(() => {
        try {
          const input = document.getElementById('cmdpal-input');
          if (input) {
            input.focus();
          }
        } catch {
        }
      });
    } catch {
    }
    // Lazy-load the apps list so the palette's per-app SKILL actions
    // (e.g. Speedtest "Run speed test") populate even when the operator
    // hasn't visited the Apps view yet this session. Fire-and-forget +
    // guarded: only when the user is an admin (skills are admin-gated),
    // the loader exists, and the list isn't already populated. The
    // palette's action list re-renders reactively once `appsList` lands.
    try {
      if ((typeof this.isAdmin === 'function' && this.isAdmin())
        && typeof this.loadAppsList === 'function'
        && !(Array.isArray(this.appsList) && this.appsList.length)) {
        this.loadAppsList();
      }
    } catch {
    }
  },
  closeCommandPalette() {
    this.commandPaletteOpen = false;
    this.commandPaletteQuery = '';
    this.commandPaletteSelectedIdx = 0;
    this.commandPaletteBulkExcluded = new Set();
  },
  // Static admin-route map. Each entry navigates to the matching
  // Admin → <tab> view via setAdminTab. Adding a new admin tab here
  // surfaces it in the palette without touching anywhere else.
  _commandAdminRoutes() {
    // Auto-derive the admin-tab list from `this.adminSections` —
    // the canonical source of truth that drives the Admin → sub-nav.
    // Pre-fix the tab list was hardcoded here as a separate literal,
    // so adding a new admin tab needed two coordinated edits AND the
    // IDs drifted (the prior literal had stale `auth` / `tuning` /
    // `asset` aliases vs the current `authentication` / `config` /
    // `assets` IDs in `adminSections`). The i18n bundle keeps the
    // legacy aliases pointing at the same strings as the new IDs so
    // operators on older locale files still see correct labels
    // during the upgrade window. Adding a new admin tab now means
    // adding ONE entry to `adminSections` plus ONE key to
    // `i18n/en.json` under `command_palette.admin.<id>`.
    const sections = Array.isArray(this.adminSections) ? this.adminSections : [];
    return sections
      // Sidebar separators (`{separator: true}`) are visual-only
      // dividers in the Admin nav — they have no tab to navigate to,
      // so skip them here or the command palette would surface an
      // empty-label phantom entry per separator.
      .filter(s => !s.separator)
      .map(s => ({
        tab: s.id,
        // Translate via `t()`; if the locale doesn't have the key, fall
        // back to the section's static `label` (which the admin sub-nav
        // already renders elsewhere).
        label: (this.t('command_palette.admin.' + s.id) || s.label || s.id),
      }));
  },
  _commandTopViews() {
    const views = ['stacks', 'services', 'nodes', 'hosts', 'history'];
    return views.map(view => ({
      view,
      label: this.t('command_palette.view.' + view),
    }));
  },
  // Score a candidate label against the lowercase query. Returns
  // 0 for no match, 1..100 for varying match quality. Exact = 100,
  // prefix = 80, word-prefix = 60, substring = 40. Empty query
  // matches everything at score 1 (so all groups render in default
  // alphabetical order until the operator types).
  _commandScoreLabel(label, q) {
    if (!q) {
      return 1;
    }
    const lc = String(label || '').toLowerCase();
    if (!lc) {
      return 0;
    }
    // Multi-word query: every token must match the label
    // somewhere; the result is the MIN of per-token scores so a
    // strong match on one token doesn't drown a weak / missing
    // match on another. Operator-reported on the Cisco SG300
    // switch where the asset name is "Cisco SG300-52MP 52-Port
    // Gigabit PoE Managed Switch" — typing "cisco switch"
    // previously failed because the literal substring "cisco
    // switch" doesn't appear (the words are at opposite ends of
    // the label). Tokenizing splits the query, scores each
    // token's best match against the label independently, and
    // returns the worst of those — so any token that's
    // genuinely absent from the label drops the score to 0.
    const qTokens = q.split(/\s+/).filter(Boolean);
    if (qTokens.length > 1) {
      let minScore = Infinity;
      for (const t of qTokens) {
        const s = this._scoreSingleToken(lc, t);
        if (s === 0) {
          return 0;
        }
        if (s < minScore) {
          minScore = s;
        }
      }
      return minScore === Infinity ? 0 : minScore;
    }
    return this._scoreSingleToken(lc, q);
  },
  // Per-token scoring — same rules the original
  // `_commandScoreLabel` applied to the whole query before the
  // multi-word tokenization. Exact match 100, prefix 80, word-
  // prefix 60, substring 40, miss 0.
  _scoreSingleToken(lc, q) {
    if (lc === q) {
      return 100;
    }
    if (lc.startsWith(q)) {
      return 80;
    }
    const tokens = lc.split(/[\s_\-./:]+/);
    for (const t of tokens) {
      if (t === q) {
        return 90;
      }
      if (t.startsWith(q)) {
        return 60;
      }
    }
    if (lc.includes(q)) {
      return 40;
    }
    return 0;
  },
  // Multi-field scorer — picks the BEST score across N candidate
  // strings. Lets a host's id match more strongly than its asset
  // type without each contributing separately.
  _commandScoreFields(q, ...fields) {
    let best = 0;
    for (const f of fields) {
      const s = this._commandScoreLabel(f, q);
      if (s > best) {
        best = s;
      }
    }
    return best;
  },
  // ---------------------------------------------------------------
  // BULK PALETTE MODE — Phase 1.
  //
  // Entry: query starts with `<verb>:` where verb is one of the
  //        canonical bulk verbs (`pause` / `resume`). Everything
  //        after the colon is the SELECTOR DSL — a list of
  //        whitespace-separated tokens, ANDed together. Each token
  //        is one of:
  //          - wildcard pattern  : matches host.id OR host.label
  //                                (case-insensitive; `*` is the
  //                                only wildcard glyph supported in
  //                                phase 1; `*nas` / `nas*` / `*nas*`
  //                                / bare `nas` all do substring
  //                                contains).
  //          - `provider:<name>` : host.providers includes <name>
  //          - `status:<value>`  : host.status === <value>
  //          - `paused`          : host.sampling_paused is true
  //                                (useful for `resume: paused`).
  //
  // The chip strip + run-row UI is driven entirely by the parsed
  // selector + this.hosts; no persistent server state until the
  // operator confirms the run.
  //
  // Phase 2+ (planned): add an AI-translated path that takes a
  // natural-language phrase and returns the same selector shape
  // (so the AI never directly invokes destructive ops, just
  // proposes a filter the operator confirms).
  // ---------------------------------------------------------------
  _BULK_VERBS: ['pause', 'resume'],
  isCommandPaletteBulkMode() {
    return this.commandPaletteBulkState() !== null;
  },
  toggleCommandPaletteBulkChip(hostId) {
    // Toggle a host's exclusion. Reactivity: re-create the Set so
    // Alpine sees the change (Set mutation in place doesn't trip
    // the reactive proxy).
    const next = new Set(this.commandPaletteBulkExcluded || []);
    if (next.has(hostId)) {
      next.delete(hostId);
    } else {
      next.add(hostId);
    }
    this.commandPaletteBulkExcluded = next;
  },
  async runCommandPaletteBulk() {
    const state = this.commandPaletteBulkState();
    if (!state) {
      return;
    }
    const ids = state.selected.map(h => h.id || h.host).filter(Boolean);
    if (!ids.length) {
      return;
    }
    const verb = state.verb;
    // SweetAlert confirm — same shape as `bulkPauseHosts` so the
    // operator gets a consistent two-stage gate (chip-strip preview
    // + final destructive confirm). Body shows up to 10 host names.
    const swal = (window.Swal || (typeof Swal !== 'undefined' && Swal));
    if (!swal) {
      if (typeof this.showToast === 'function') {
        this.showToast('SweetAlert unavailable', 'error');
      }
      return;
    }
    const sample = ids.slice(0, 10);
    const more = ids.length - sample.length;
    const sampleHtml = sample.map(id => '<code>' + this._logEscape(id) + '</code>').join(', ');
    const moreHtml = more > 0
      ? ' ' + (this.t('hosts_extra.bulk.pause_confirm_more', {more}) || ('… and ' + more + ' more'))
      : '';
    const titleKey = 'command_palette.bulk.confirm_title_' + verb;
    const bodyKey = 'command_palette.bulk.confirm_body_' + verb;
    const okKey = 'command_palette.bulk.confirm_ok_' + verb;
    const fallbackTitle = (verb === 'pause' ? 'Pause sampling on selected hosts?' : 'Resume sampling on selected hosts?');
    const fallbackBody = (verb === 'pause' ? 'Pause sampling on ' : 'Resume sampling on ') + ids.length + ' host(s)?';
    const fallbackOk = (verb === 'pause' ? 'Pause' : 'Resume');
    try {
      const res = await swal.fire({
        title: this.t(titleKey) || fallbackTitle,
        html: (this.t(bodyKey, {count: ids.length}) || fallbackBody)
          + '<br><br><div class="fs-xs text-[var(--text-dim)] mono break-words">' + sampleHtml + moreHtml + '</div>',
        icon: 'warning',
        showCancelButton: true,
        confirmButtonText: this.t(okKey) || fallbackOk,
        cancelButtonText: this.t('actions.cancel') || 'Cancel',
      });
      if (!res.isConfirmed) {
        return;
      }
    } catch {
      return;
    }
    // Pause requires step-up reauth (matches the per-host bulk
    // pause contract); resume does not.
    let reauthToken = null;
    if (verb === 'pause') {
      reauthToken = await this._mintReauthToken();
      if (reauthToken === null) {
        return;
      }
    }
    try {
      const headers = {'Content-Type': 'application/json'};
      if (reauthToken) {
        headers['X-Reauth-Token'] = reauthToken;
      }
      const r = await fetch('/api/hosts/bulk/' + verb, {
        method: 'POST',
        headers,
        body: JSON.stringify({host_ids: ids}),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        this.showToast((data && data.detail) || ('HTTP ' + r.status), 'error');
        return;
      }
      const okKey2 = 'command_palette.bulk.success_' + verb;
      this.showToast(
        this.t(okKey2, {count: (data.applied || []).length || ids.length})
        || ((verb === 'pause' ? 'Paused' : 'Resumed') + ' ' + ((data.applied || []).length || ids.length) + ' host(s)'),
        'success',
      );
      // Clear the palette so a back-to-back bulk doesn't carry the
      // exclusion set across runs.
      if (this.closeCommandPalette) {
        this.closeCommandPalette();
      }
      if (typeof this.loadHosts === 'function') {
        this.loadHosts(true);
      }
    } catch (e) {
      this.showToast(String(e && e.message || e), 'error');
    }
  },
  // Inline confirmation handlers for destructive actions invoked
  // from the AI sidebar. The chat turn carries `pending_confirm:
  // true` + `pending_action: <descriptor>` until the operator
  // clicks one of the two buttons in the bubble.
  async confirmInlineAction(turnIdx) {
    const turn = this.aiConversation[turnIdx];
    if (!turn || !turn.pending_confirm || !turn.pending_action) {
      return;
    }
    const action = turn.pending_action;
    turn.pending_confirm = false;
    turn.pending_action = null;
    turn.action_ran = true;
    this.persistAiConversation();
    this._scrollAiSidebarToBottom();
    try {
      // Special-case: `memory_forget` doesn't have an `action.run`
      // descriptor — it carries `forget_texts: [...]` straight from
      // the AI's MEMORY-FORGET directives, which the SPA persists
      // verbatim across reloads (action.run would have been a
      // function reference, lost on JSON-stringify). Walk each text
      // and DELETE via /api/ai/memory/forget; toast each outcome.
      if (action.kind === 'memory_forget' && Array.isArray(action.forget_texts)) {
        for (const txt of action.forget_texts) {
          try {
            const r = await fetch('/api/ai/memory/forget', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({text: txt}),
            });
            if (r.ok && typeof this.showToast === 'function') {
              this.showToast(this.t('ai_memory.toast_forgotten') || 'Memory forgotten', 'success');
            }
          } catch (_) { /* swallow — operator can retry from the AI's next reply */
          }
        }
        return;
      }
      // `skipConfirm: true` propagates to the action's inner
      // implementation (bulkRemoveAll / bulkUpdateAll / etc.) so
      // the rich-data SweetAlert it would otherwise raise is
      // bypassed. The operator already approved inline; a second
      // popup would defeat the no-popup contract.
      // `tag` + `actionItem` are forwarded for parameterised
      // actions (currently retag_image only). Other actions
      // ignore them. `surface: 'sidebar'` + `confirm: true` let a
      // per-app skill run silently + pass the backend destructive gate
      // (the operator just clicked Yes — that IS the confirmation).
      const _runRet = await action.run({
        skipConfirm: true,
        tag: (turn.action_tag || '').toString(),
        actionItem: (turn.action_item || '').toString(),
        data: (turn.action_data && typeof turn.action_data === 'object') ? turn.action_data : null,
        surface: 'sidebar',
        confirm: true,
      });
      // Surface a per-app skill's output inline in the chat (parity with the
      // non-destructive _aiSidebarRunSkill path) — e.g. a confirmed
      // radarr_remove_movie shows its "Removed X" detail, not just the chip.
      if (typeof this._stampSkillPanelFromResult === 'function') {
        this._stampSkillPanelFromResult(turn, _runRet);
        this.persistAiConversation();
      }
    } catch (e) {
      if (typeof this.showToast === 'function') {
        this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
      }
    }
  },
  cancelInlineAction(turnIdx) {
    const turn = this.aiConversation[turnIdx];
    if (!turn) {
      return;
    }
    turn.pending_confirm = false;
    turn.pending_action = null;
    turn.cancelled = true;
    this.persistAiConversation();
  },

  // Tool-dispatch confirm handler. Distinct from confirmInlineAction
  // because we're not running a SPA-side action descriptor; we're
  // re-POSTing to /api/ai/palette with tool_confirm_granted=true so
  // the backend dispatcher actually fires the confirm-required tools
  // (ssh_diag / docker_container_du) and returns the second-round AI
  // reply composed from the tool output.
  async confirmInlineToolDispatch(turnIdx) {
    const turn = this.aiConversation[turnIdx];
    if (!turn || !turn.pending_tool_confirms || !turn.pending_query) {
      return;
    }
    const origQuery = turn.pending_query;
    // Clear the pending state immediately so the chip disappears and
    // double-click can't fire twice. We'll replace turn.text once
    // the second-round reply lands.
    turn.pending_tool_confirms = null;
    turn.pending_query = null;
    this.aiSidebarBusy = true;
    this.persistAiConversation();
    this._scrollAiSidebarToBottom();
    try {
      const ctx = this._buildAiPaletteContext();
      // Conversation history up to (but not including) THIS pending
      // turn — so the AI doesn't re-see its own first-round reply
      // and confuse "tool already pending" with "ask again".
      const priorTurns = this.aiConversation
        .slice(0, turnIdx)
        .filter(t => t && (t.role === 'user' || t.role === 'assistant') && t.text)
        .map(t => ({role: t.role, text: t.text}));
      const r = await fetch('/api/ai/palette', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          query: origQuery,
          context: ctx,
          conversation: priorTurns,
          tool_confirm_granted: true,
        }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) {
        let detail = (j && j.detail) || (this.t('toasts.failed') || 'Failed');
        // Operator-helpful hint: when the AI provider timed out AND
        // the fallback chain wasn't engaged (either disabled OR no
        // viable secondary providers configured), tell the operator
        // where to fix it. Pre-fix the bare "Request timed out"
        // error left the operator wondering why they configured a
        // fallback at all.
        const isTimeout = typeof detail === 'string' && /timed out after/i.test(detail);
        const noFallback = j && j.fallback_used === false
          && Array.isArray(j.fallback_chain) && j.fallback_chain.length <= 1;
        if (isTimeout && noFallback) {
          detail = detail + ' ' + (this.t('toasts.ai_no_fallback_hint')
            || '(Tip: configure a fallback provider in Admin → AI Integration → Fallback chain so the next provider can pick up when the primary times out.)');
        }
        turn.error = detail;
      } else {
        // Replace the first-round prose with the second-round reply
        // composed from the tool results. Keep the same `ts` so the
        // bubble's DOM identity is stable.
        turn.text = (j.text || '').trim() || (this.t('command_palette.ai.empty_response') || '(empty response)');
        turn.provider = j.provider || turn.provider;
        turn.model = j.model || turn.model;
        turn.response_time_ms = (turn.response_time_ms || 0) + (j.response_time_ms || 0);
        turn.tokens = (turn.tokens || 0) + ((j.tokens && (j.tokens.prompt + j.tokens.completion)) || 0);
        turn.job_id = (j.job_id !== undefined && j.job_id !== null) ? j.job_id : turn.job_id;
        // tool_calls + tool_results are surfaced on the response for
        // operator-visible "what did we actually run?" affordance.
        if (Array.isArray(j.tool_calls)) {
          turn.tool_calls = j.tool_calls;
        }
        if (j.tool_results && typeof j.tool_results === 'object') {
          turn.tool_results = j.tool_results;
        }
        // Multi-round chain — the second-round reply itself may emit
        // NEW TOOL directives (common when the first round surfaced
        // a container ID and the AI now wants to drill into it).
        // Re-stamp the pending state from the new response so the
        // chip appears for the next round. In autonomous mode, the
        // post-push autonomous gate at the bottom of this function
        // will re-fire `confirmInlineToolDispatch` again, walking
        // the chain to completion. Hard-cap chain depth at
        // ~5 rounds via `turn.tool_chain_depth` so a buggy model
        // can't infinite-loop us through the dispatcher.
        const chainDepth = (turn.tool_chain_depth || 0) + 1;
        turn.tool_chain_depth = chainDepth;
        if (Array.isArray(j.pending_tool_confirms) && j.pending_tool_confirms.length
          && chainDepth < 5) {
          turn.pending_tool_confirms = j.pending_tool_confirms;
          turn.pending_query = origQuery;
          // Autonomous mode auto-chains the next round; approval
          // mode renders the chip again and waits for the operator.
          if (this.aiSidebarMode === 'autonomous') {
            this.$nextTick(() => {
              this.confirmInlineToolDispatch(turnIdx);
            });
          }
        }
      }
    } catch (e) {
      turn.error = (e && e.message) ? e.message : 'Tool dispatch failed';
    } finally {
      this.aiSidebarBusy = false;
      this._scrollAiSidebarToBottom();
      this.persistAiConversation();
    }
  },

  cancelInlineToolDispatch(turnIdx) {
    const turn = this.aiConversation[turnIdx];
    if (!turn) {
      return;
    }
    turn.pending_tool_confirms = null;
    turn.pending_query = null;
    turn.cancelled = true;
    this.persistAiConversation();
  },

  // Generic focus-trap keydown handler. Bind via
  // ``@keydown="open && _focusTrapKeydown($event, $el)"`` on the
  // dialog root. Intercepts Tab / Shift-Tab to cycle focus within
  // the dialog. Other keys pass through. Tab leak was a fleet-wide
  // gap (the project conventions "Drawers + modals need ... focus-trap helper" —
  // documented since 2026-04-30, never built fleet-wide). This helper
  // is the building block; existing dialogs (host drawer, item drawer,
  // terminal modal, hotkeys modal, schedule edit modal) can adopt it
  // by adding the same `@keydown` binding to their root elements.
  _focusTrapKeydown(e, root) {
    if (!root || !e || e.key !== 'Tab') {
      return;
    }
    // Build the focusable-within-dialog set on every keydown rather
    // than caching — Alpine renders / removes nodes on state changes
    // (slash-picker, inline-confirm chip, feedback chips), so a
    // cached list goes stale. Cost is one querySelectorAll + a small
    // visibility filter on Tab — negligible.
    const candidates = root.querySelectorAll(
      'a[href], button:not([disabled]), textarea:not([disabled]), ' +
      'input:not([disabled]):not([type="hidden"]), select:not([disabled]), ' +
      '[tabindex]:not([tabindex="-1"])'
    );
    const focusables = [];
    for (const node of candidates) {
      if (node.offsetParent === null) {
        continue;
      } // hidden via display:none
      if (node.getAttribute('aria-hidden') === 'true') {
        continue;
      }
      focusables.push(node);
    }
    if (!focusables.length) {
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (e.shiftKey) {
      if (active === first || !root.contains(active)) {
        last.focus();
        e.preventDefault();
      }
    } else {
      if (active === last) {
        first.focus();
        e.preventDefault();
      }
    }
  },
  // Persist `aiRecentSlashActions` to /api/me/ui-prefs. Mirrors
  // `persistThemePref` / `persistAiConversation` — fire-and-forget,
  // localStorage doubles as the fast-path read since it's already
  // in `this.aiRecentSlashActions`.
  async _persistRecentSlashActions() {
    if (!this.me || !this.me.id || this.me.id < 0) {
      return;
    }
    try {
      await fetch('/api/me/ui-prefs', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prefs: {ai_recent_slash_actions: this.aiRecentSlashActions.slice(0, 5)}}),
      });
    } catch (e) {
      if (window.console && console.warn) {
        console.warn('[ai_sidebar] persist recents failed:', e);
      }
    }
  },

  // Push an action id onto the recents FIFO. Dedupes by removing
  // the id if already present, then unshifts to the head, then caps
  // at 5. Persists asynchronously.
  _recordSlashRecent(actionId) {
    if (!actionId || typeof actionId !== 'string') {
      return;
    }
    const existing = this.aiRecentSlashActions.indexOf(actionId);
    if (existing >= 0) {
      this.aiRecentSlashActions.splice(existing, 1);
    }
    this.aiRecentSlashActions.unshift(actionId);
    if (this.aiRecentSlashActions.length > 5) {
      this.aiRecentSlashActions.length = 5;
    }
    this._persistRecentSlashActions();
  },
  _appendActionChatTurn(action, slash) {
    // Push a synthetic assistant turn so the chat log shows what
    // the operator just invoked. For destructive actions we leave
    // `action_ran: false` initially — the inline confirmation chip
    // flips it to true on Yes click. Non-destructive auto-runs go
    // straight to true.
    const isDestructive = !!(action && action.destructive);
    this.aiConversation.push({
      role: 'assistant',
      text: '',
      action_id: action.id,
      action_label: action.label || action.id,
      action_ran: !isDestructive,
      // Pre-declared so the "Working on it…" spinner (x-show on
      // turn.skill_running) tracks the key from creation when
      // `_runCommandPaletteAction` flips it during the run.
      skill_running: false,
      slash: !!slash,
      ts: Date.now(),
    });
    this._scrollAiSidebarToBottom();
    this.persistAiConversation();
  },

  selectionUpdatable() {
    return this.items.filter(i => this.selected.includes(i.id) && i.status === 'update' && this.canUpdate(i));
  },
  selectionRemovable() {
    return this.items.filter(i => this.selected.includes(i.id) && i.removable);
  },
  removableAll() {
    // Everything currently removable, regardless of selection. Drives the
    // topbar "Cleanup N" fast-action button.
    return this.items.filter(i => i.removable);
  },
  updatableAll() {
    // Every item with an available update, regardless of selection. Drives
    // the AI palette `update_all_updatable` action.
    return this.items.filter(i => i.status === 'update' && this.canUpdate(i));
  },
  selectionRestartable() {
    return this.items.filter(i => this.selected.includes(i.id) && this.isRestartable(i));
  },
  selectionSummary() {
    const upd = this.selectionUpdatable().length;
    const rst = this.selectionRestartable().length;
    const rem = this.selectionRemovable().length;
    const parts = [];
    if (upd) {
      parts.push(this.t('bulk.summary_updatable', {count: upd}));
    }
    if (rst) {
      parts.push(this.t('bulk.summary_restartable', {count: rst}));
    }
    if (rem) {
      parts.push(this.t('bulk.summary_removable', {count: rem}));
    }
    return parts.length ? parts.join(' · ') : '';
  },
  openDrawer(item) {
    this.drawerItem = item;
  },
  // Body-scroll lock helper. Called from the Alpine root's
  // `x-effect` whenever any drawer-state changes — sets / clears a
  // `.drawer-scroll-lock` class on BOTH html and body so the scroll
  // viewport (which varies by browser — Chrome on html, Safari on
  // body) doesn't accept wheel input while a drawer is open. The
  // CSS rule pairs `overflow: hidden !important` on the class so it
  // wins against the existing `overflow-x: clip` rule. Three reactive
  // arguments (`drawerHost` / `drawerItem` / `drawerNode`) are
  // passed in so Alpine's effect tracker registers reads of all
  // three; without that an IIFE wrapping the same logic might
  // miss the dependency tracking.
  _applyDrawerScrollLock(host, item, node, sidebar, sidebarPinned, app, appHost) {
    // `sidebar` is `aiSidebarOpen` — added so opening the AI Assistant
    // drawer ALSO locks the body scroll. Pre-fix the AI sidebar slid
    // in over a still-scrollable page beneath; the cascade was scoped
    // to the three classic drawers only. Future drawer-style overlays
    // MUST extend this signature with another bound reactive flag —
    // do NOT compute `aiSidebarOpen` inside the function body, that
    // breaks Alpine's dependency tracking and the lock fires once
    // then goes silent.
    // `sidebarPinned` is `aiSidebarPinned` — when the AI sidebar is
    // pinned (split-pane mode), it's NOT a modal overlay anymore;
    // the operator wants to interact with the rest of the page
    // (scroll, click buttons, open drawers) WHILE the sidebar stays
    // docked. So `sidebar=true && sidebarPinned=true` does NOT
    // contribute to the lock — only the modal classic-drawer state
    // (host / item / node) plus an UNPINNED open sidebar count.
    // `app` is `drawerApp` (the Apps detail drawer) — a modal overlay
    // like the classic three drawers, so it locks the body scroll too.
    // `appHost` is `drawerAppHost` (the Apps-by-host drawer) — same
    // modal-overlay treatment.
    const lock = !!(host || item || node || app || appHost || (sidebar && !sidebarPinned));
    const html = document.documentElement;
    const body = document.body;
    if (lock) {
      html.classList.add('drawer-scroll-lock');
      body.classList.add('drawer-scroll-lock');
    } else {
      html.classList.remove('drawer-scroll-lock');
      body.classList.remove('drawer-scroll-lock');
    }
  },

  // WAI-ARIA radiogroup keyboard pattern. Bind on the wrapper
  // element via `@keydown="_radiogroupArrowKey($event)"`. Implements
  // the canonical contract: ArrowLeft/Up → previous radio (with
  // wrap), ArrowRight/Down → next radio, Home → first, End → last.
  // RTL flips Left/Right semantics so the LOGICAL direction stays
  // intact (visual-left in RTL is "next"). Skips disabled radios.
  // Each move both focuses the next radio and triggers its click
  // handler so selection follows focus per the radiogroup pattern.
  // Roving tabindex (only the checked radio is tab-reachable) is
  // wired per-radio at the markup level via `:tabindex="..."`.
  _radiogroupArrowKey(ev) {
    const key = ev.key;
    if (key !== 'ArrowLeft' && key !== 'ArrowRight'
      && key !== 'ArrowUp' && key !== 'ArrowDown'
      && key !== 'Home' && key !== 'End') {
      return;
    }
    const group = ev.currentTarget;
    const radios = Array.from(group.querySelectorAll('[role="radio"]')).filter(r => !r.disabled);
    if (!radios.length) {
      return;
    }
    ev.preventDefault();
    let isRtl;
    try {
      isRtl = group.matches(':dir(rtl)');
    } catch {
      // `:dir()` pseudo-class isn't universally supported (Chromium gated it
      // behind a flag for years); fall back to reading the document direction directly.
      isRtl = document.documentElement.dir === 'rtl';
    }
    let idx = radios.indexOf(document.activeElement);
    if (idx < 0) {
      idx = radios.findIndex(r => r.getAttribute('aria-checked') === 'true');
    }
    if (idx < 0) {
      idx = 0;
    }
    let next;
    if (key === 'Home') {
      next = 0;
    } else {
      if (key === 'End') {
        next = radios.length - 1;
      } else {
        let dir = (key === 'ArrowRight' || key === 'ArrowDown') ? 1 : -1;
        if (isRtl && (key === 'ArrowLeft' || key === 'ArrowRight')) {
          dir = -dir;
        }
        next = (idx + dir + radios.length) % radios.length;
      }
    }
    const target = radios[next];
    target.focus();
    target.click();
  },

  // WAI-ARIA tab pattern for VERTICAL page-sidebar tablists (Settings
  // / Admin / Stats). Each sidebar carries `role="tablist"
  // aria-orientation="vertical"` + per-button `role="tab"` already;
  // this helper adds the missing arrow-key navigation. Bind on the
  // sidebar wrapper via `@keydown="_sidebarTablistArrowKey($event)"`.
  // Contract: ArrowUp / ArrowDown / Home / End move focus between
  // tabs (with wrap), focus follows by clicking the target so the
  // bound section model updates. Skips disabled tabs.
  _sidebarTablistArrowKey(ev) {
    const key = ev.key;
    if (key !== 'ArrowUp' && key !== 'ArrowDown'
      && key !== 'Home' && key !== 'End') {
      return;
    }
    const group = ev.currentTarget;
    const tabs = Array.from(group.querySelectorAll('[role="tab"]'))
      .filter(t => !t.disabled);
    if (!tabs.length) {
      return;
    }
    ev.preventDefault();
    let idx = tabs.indexOf(document.activeElement);
    if (idx < 0) {
      idx = tabs.findIndex(t => t.getAttribute('aria-selected') === 'true');
    }
    if (idx < 0) {
      idx = 0;
    }
    let next;
    if (key === 'Home') {
      next = 0;
    } else {
      if (key === 'End') {
        next = tabs.length - 1;
      } else {
        const dir = (key === 'ArrowDown') ? 1 : -1;
        next = (idx + dir + tabs.length) % tabs.length;
      }
    }
    const target = tabs[next];
    target.focus();
    target.click();
  },

  // fired on `@focusout` of the host-card wrapper. Defers
  // the cn-driven sort/page-jump until the operator has fully left
  // the row card (not just blurred the cn input mid-edit).
  //
  // Without this, the previous behaviour was: type a new cn, tab to
  // the next field → the row immediately re-sorts into its numeric
  // position → on a paged editor the row may have moved to a
  // DIFFERENT page → operator has to find it again to keep editing
  // label / ne_url / beszel_name. Now: cn `@input` sets
  // `row._cnDirty = true`; the rebuild + page-jump happens here
  // ONLY when focus genuinely leaves the entire card.
  onHostCardFocusOut(idx, event, cardEl) {
    const newFocus = event && event.relatedTarget;
    // Focus moved within the same card → still editing → no-op.
    if (newFocus && cardEl && cardEl.contains(newFocus)) {
      return;
    }
    const row = (this.hostsConfig || [])[idx];
    if (!row || !row._cnDirty) {
      return;
    }
    row._cnDirty = false;
    const uid = row._uid;
    this.rebuildHostsConfigOrder();
    // Keep the row visible after the sort lands. Without this, a
    // cn that pushed the row to another page would silently move it
    // off-screen — exactly the bug we're fixing for the inline-cn
    // case, but for the post-edit case too.
    this.$nextTick(() => {
      const all = this.filteredHostsConfig();
      const pos = all.findIndex(({row: r}) => r._uid === uid);
      if (pos >= 0) {
        const per = this.hostsConfigPerPage || 50;
        const targetPage = Math.floor(pos / per) + 1;
        if (targetPage !== this.hostsConfigPage) {
          this.hostsConfigGoToPage(targetPage);
        }
      }
    });
  },

  // Count of discovered names not already present in hostsConfig —
  // drives the "Import N discovered" button label / visibility so
  // the operator doesn't import duplicates by accident.
  discoveredMissingCount() {
    const seen = new Set((this.hostsConfig || []).map(r =>
      (r.beszel_name || r.pulse_name || r.webmin_name || r.id || '').toLowerCase()
    ));
    let n = 0;
    for (const name of (this.hostsDiscovery.beszel || [])) {
      if (!seen.has(name.toLowerCase())) {
        n++;
      }
    }
    for (const name of (this.hostsDiscovery.pulse || [])) {
      if (!seen.has(name.toLowerCase())) {
        n++;
      }
    }
    for (const name of (this.hostsDiscovery.webmin || [])) {
      if (!seen.has(name.toLowerCase())) {
        n++;
      }
    }
    return n;
  },
  // Bulk-create host rows from every discovered name that isn't
  // already curated. Each new row uses the discovered name as both
  // the id/label and the matching provider's name field; the
  // operator tweaks from there. A name in BOTH providers creates
  // a single row with both fields filled.
  // Helper used by importDiscoveredHosts + anywhere else that
  // programmatically mutates hostsConfig.
  _markHostsDirty() {
    this.hostsConfigDirty = true;
  },
  importDiscoveredHosts() {
    const existing = new Set((this.hostsConfig || []).map(r =>
      (r.id || '').toLowerCase()
    ));
    const added = {};
    const addOrMerge = (name, field) => {
      const key = name.toLowerCase();
      if (existing.has(key)) {
        return;
      }
      if (!added[key]) {
        added[key] = {
          id: name,
          label: name,
          ne_url: '',
          beszel_name: '',
          pulse_name: '',
          webmin_name: '',
          webmin_url: '',
          snmp_name: '',
          // Init http_probe sub-dict so the per-host editor's
          // textarea x-model binding doesn't read `urls_text` off
          // undefined when this discovery-imported row is opened.
          http_probe: {},
          enabled: true,
        };
      }
      added[key][field] = name;
    };
    for (const n of (this.hostsDiscovery.beszel || [])) {
      addOrMerge(n, 'beszel_name');
    }
    for (const n of (this.hostsDiscovery.pulse || [])) {
      addOrMerge(n, 'pulse_name');
    }
    for (const n of (this.hostsDiscovery.webmin || [])) {
      addOrMerge(n, 'webmin_name');
    }
    for (const n of (this.hostsDiscovery.snmp || [])) {
      addOrMerge(n, 'snmp_name');
    }
    const rows = Object.values(added);
    if (!rows.length) {
      this.showToast(this.t('admin_hosts.import.nothing_new'), 'success');
      return;
    }
    this.hostsConfig.push(...rows);
    this.hostsConfigDirty = true;
    this.showToast(this.t('admin_hosts.added_n', {count: rows.length}), 'success');
  },
  // True when at least one provider (Beszel / Pulse / node-exporter
  // / Webmin / Ping / SNMP) is mapped on this row. The "Test providers"
  // button disables when none are set — there's nothing to probe and
  // the backend would return all-skipped anyway. SNMP + Ping added
  // : pre-fix an SNMP-only or Ping-only row showed the button
  // greyed out even with the row's snmp_name set or ping.enabled=true,
  // so the operator had no way to test those providers from the
  // Admin → Hosts editor.
  rowHasProviderMapping(row) {
    if (!row) {
      return false;
    }
    // SNMP gating mirrors ping's explicit opt-in: probe only when
    // `snmp.enabled === true`. The probe target falls through the
    // canonical resolver chain (aliases → snmp_name → address →
    // SKIP), so the gate must accept EITHER an explicit `snmp_name`
    // OR the shared `address` field. Pre-fix only `snmp_name` was
    // checked, so a host with `snmp.enabled=true` + `address`
    // populated + `snmp_name` blank reported "no provider mapping"
    // and the Test Providers button stayed disabled even though
    // the live sampler would have probed it correctly via the
    // address fallback.
    const snmpActive = !!(row.snmp && row.snmp.enabled === true)
      && !!((row.snmp_name || '').trim() || (row.address || '').trim());
    // HTTP probe gating mirrors ping/snmp's explicit opt-in. Either
    // the operator-set per-row `http_probe.urls` list OR the
    // fallback chain (top-level `url` + `services[].url`) gives the
    // probe a target. Without this branch, a host with ONLY
    // http_probe configured reported "no provider mapping" and the
    // Test button stayed disabled.
    const httpProbeActive = !!(row.http_probe && row.http_probe.enabled === true)
      && (
        (Array.isArray(row.http_probe.urls) && row.http_probe.urls.some(u => (u || '').trim()))
        || (row.url || '').trim()
        || (Array.isArray(row.services) && row.services.some(s => (s && s.url || '').trim()))
      );
    return !!(
      (row.beszel_name || '').trim() ||
      (row.pulse_name || '').trim() ||
      (row.ne_url || '').trim() ||
      (row.webmin_name || '').trim() ||
      (row.webmin_url || '').trim() ||
      snmpActive ||
      (row.ping && row.ping.enabled) ||
      httpProbeActive
    );
  },
  // Collapse / expand helpers for the Admin → Hosts editor. With SSH
  // + provider + icon + URL fields on every row, a 20-host list is
  // a 2000-line scroll. Collapsed rows show only the summary; the
  // field grid renders behind x-show when expanded.
  //
  // Keyed on the row's stable `_uid` (assigned on load / add) — NOT
  // on `row.id`. Earlier the lookup used `row.id` as the key, so
  // typing into the ID field changed the key mid-keystroke and the
  // row collapsed (`hostsConfigExpanded[oldId]` was true but
  // `hostsConfigExpanded[newId]` was undefined). Operators reported
  // "typing in ID closes the host" — this is that bug. _uid never
  // changes for the lifetime of the row, so the expansion state
  // sticks regardless of edits to any field.
  isHostConfigExpanded(row) {
    // Backwards-compatible: callers may pass either a row OR a
    // bare id (legacy paths). When given a string, fall back to
    // looking it up via id then resolve to _uid.
    if (typeof row === 'string') {
      if (!row) {
        return true;
      }
      const found = (this.hostsConfig || []).find(r => r && r.id === row);
      return found ? !!this.hostsConfigExpanded[found._uid] : false;
    }
    if (!row) {
      return false;
    }
    // Empty-id rows (fresh adds) always expand so the operator
    // sees the form fields without hunting for a chevron.
    if (!row.id) {
      return true;
    }
    return !!this.hostsConfigExpanded[row._uid];
  },
  toggleHostConfigRow(row) {
    const resolved = (typeof row === 'string')
      ? (this.hostsConfig || []).find(r => r && r.id === row)
      : row;
    if (!resolved || !resolved._uid) {
      return;
    }
    const next = {...this.hostsConfigExpanded};
    if (next[resolved._uid]) {
      delete next[resolved._uid];
    } else {
      next[resolved._uid] = true;
    }
    this.hostsConfigExpanded = next;
  },
  expandAllHostConfigRows() {
    const next = {};
    for (const h of (this.hostsConfig || [])) {
      if (h && h._uid) {
        next[h._uid] = true;
      }
    }
    this.hostsConfigExpanded = next;
  },
  collapseAllHostConfigRows() {
    // Wipe the whole map — every row that had an id drops back to
    // its summary line. Empty-id rows (fresh adds) stay visible
    // because isHostConfigExpanded(id) returns true for them by
    // design.
    this.hostsConfigExpanded = {};
  },
  // DISKS card — toggle the "show zero-usage mounts" state. Keyed
  // by host.host so each host's toggle is independent.
  toggleHostShowEmptyDisks(hostKey) {
    this.hostDisksShowEmpty = {
      ...this.hostDisksShowEmpty,
      [hostKey]: !this.hostDisksShowEmpty[hostKey],
    };
  },
  // Helper returning the mounts that SHOULD render for this host.
  // Thresholds: a mount is "active" when its usage % is >= 0.5. The
  // UI shows active mounts by default; zero-usage mounts collapse
  // behind a toggle so hosts with 20 ZFS datasetsdon't
  // blow out the drawer. When the operator has flipped the toggle
  // on, every mount is returned.
  visibleMounts(h) {
    const all = (h && h.mounts) || [];
    if (!all.length) {
      return [];
    }
    if (this.hostDisksShowEmpty[h.host]) {
      return all;
    }
    const active = all.filter(m => (+m.dp || 0) >= 0.5);
    // Always keep AT LEAST the root ("/" or "C:\\") so operators
    // see something even when every partition is near-empty.
    if (active.length === 0 && all[0]) {
      return [all[0]];
    }
    return active;
  },
  emptyMountCount(h) {
    const all = (h && h.mounts) || [];
    const active = all.filter(m => (+m.dp || 0) >= 0.5);
    return Math.max(0, all.length - active.length);
  },
  // Asset-inventory autofill — called from the host-row editor when
  // the operator clicks "Load from asset inventory". Looks up the
  // row's `custom_number` against the loaded asset cache (via the
  // shared `assetForHost` helper so backend-injected + client-cache
  // paths both work) and populates EMPTY fields on the row:
  // id (Docker hostname) ← first entry in asset.hostnames (or asset.name)
  // label              ← asset.name / vendor+model fallback
  // url                ← first port.service_name starting with http(s)
  // Never overwrites a value the operator already typed — blank
  // fields only. Toast reports what was filled (or why nothing was).
  // Returns an array of field names that were filled, for the UI.
  autofillHostRowFromAsset(idx) {
    const row = (this.hostsConfig || [])[idx];
    if (!row) {
      return [];
    }
    const asset = this.assetForHost({custom_number: row.custom_number});
    if (!asset) {
      this.showToast(this.t('admin_hosts.autofill.no_match', {n: row.custom_number}), 'warning');
      return [];
    }
    const filled = [];
    // Strip the FQDN's domain suffix when populating the host id.
    // The global `ssh_fqdn_suffix` setting (e.g. ".example.com") is
    // appended at SSH-resolve time, so storing the SHORT hostname
    // here keeps the global suffix authoritative — different deploys
    // can swap suffixes without re-typing every host. IPs and
    // hostnames-without-dots are returned unchanged.
    const _stripDomain = (raw) => {
      const v = String(raw || '').trim();
      if (!v) {
        return '';
      }
      // Bare hostname (no dot) — nothing to strip.
      if (v.indexOf('.') === -1) {
        return v;
      }
      // IPv4 — leave intact.
      if (/^\d+\.\d+\.\d+\.\d+$/.test(v)) {
        return v;
      }
      // IPv6 — leave intact (contains `:`, no other shape collides).
      if (v.indexOf(':') !== -1) {
        return v;
      }
      // FQDN — keep the leading label only.
      return v.split('.')[0];
    };
    // id / hostname — prefer the first FQDN in the asset's Hostname
    // CSV; fall back to asset.name (device label, often lowercase).
    // Strip the domain suffix so the global `ssh_fqdn_suffix`
    // setting remains the single source of truth for what gets
    // appended at resolution time.
    if (!(row.id || '').trim()) {
      const primary = (Array.isArray(asset.hostnames) && asset.hostnames[0])
        || asset.name || '';
      if (primary) {
        row.id = _stripDomain(primary);
        filled.push('id');
      }
    }
    // NOTE: `row.label` deliberately NOT auto-populated from the
    // asset record. Operator-flagged: pre-fix the label was filled
    // from `asset.name` / `vendor model` / `vendor` on import, but
    // operators want to fill it themselves so it captures their
    // own naming convention rather than whatever shape the upstream
    // asset DB happens to carry. Empty label falls through cleanly
    // — `hostDisplayName(h)` already prefers `id` when label is
    // blank, and `iconUrlFor()` walks id + label + provider names
    // for icon resolution. If you ever want bulk-prefill behaviour
    // back, add a separate "Auto-fill labels from asset" button
    // rather than re-enabling on import.
    // URL — first port whose service_name looks like an http(s) link.
    // Ports without a URL-shaped service_name are skipped.
    if (!(row.url || '').trim() && Array.isArray(asset.ports)) {
      for (const p of asset.ports) {
        const sn = (p && p.service_name) || '';
        if (/^https?:\/\//i.test(sn)) {
          row.url = sn;
          filled.push('url');
          break;
        }
      }
    }
    // IP — primary IP from the asset's interfaces. Helpful for
    // hosts where node-exporter scrape templates reference {ip}.
    if (!(row.ip || '').trim() && asset.primary_ip) {
      row.ip = asset.primary_ip;
      filled.push('ip');
    }
    if (!filled.length) {
      this.showToast(this.t('admin_hosts.autofill.nothing_to_fill'), 'info');
      return [];
    }
    this.markHostRowDirty(idx);
    // Keep the row visibly expanded after the fill — same sticky
    // rule used for typed input (see onHostRowEdit).
    if (row._uid) {
      this.hostsConfigExpanded = {
        ...this.hostsConfigExpanded,
        [row._uid]: true,
      };
    }
    this.showToast(this.t('admin_hosts.autofill.filled', {
      fields: filled.join(', '),
    }), 'success');
    return filled;
  },
  // Dirty-tracking: any input change flips the unsaved-changes
  // flag (so the Save button can flash the warning + beforeunload
  // guards kick in) AND clears any stale per-row Test result so
  // green ticks don't linger next to fields the operator is
  // currently fixing.
  // Mint a stable, collision-free per-row UID for the Admin → Hosts
  // editor. Used as the `<template x-for :key>` so Alpine never tears
  // down + re-mounts a row mid-edit (which would lose input focus +
  // collapse expanded sections). Prefer `crypto.randomUUID()` —
  // universally supported in modern browsers + cryptographically
  // strong, which silences the `js/insecure-randomness` CodeQL flag
  // even though the value is UI-only and not a security secret.
  // Fallback path covers the (vanishingly rare) case of a browser
  // without `crypto.randomUUID` — uses `crypto.getRandomValues` (a
  // cryptographically strong PRNG, the primitive that backs randomUUID
  // itself), available in every browser that ships modern JS. No
  // `Math.random` anywhere so CodeQL js/insecure-randomness has no
  // surface left to flag.
  _mintRowUid() {
    try {
      // `crypto` is a browser-global (Web Crypto API). JSHint's
      // default env list doesn't include it, so the per-line ignore
      // markers below silence W117 without adding it to a
      // project-wide `/* global */` declaration that would mask real
      // missing-import bugs elsewhere.
      if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') { // jshint ignore:line
        return 'r' + crypto.randomUUID(); // jshint ignore:line
      }
      if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') { // jshint ignore:line
        const buf = new Uint8Array(8);
        crypto.getRandomValues(buf); // jshint ignore:line
        let hex = '';
        for (let i = 0; i < buf.length; i++) {
          hex += buf[i].toString(16).padStart(2, '0');
        }
        return 'r' + hex;
      }
    } catch { /* unreachable on any spec-compliant browser */
    }
    // Last-resort: a monotonic counter scoped to the page session.
    // Not random, but unique within one tab — good enough for an Alpine
    // x-for :key that only needs intra-render uniqueness.
    this._uidCounter = (this._uidCounter | 0) + 1;
    return 'r' + Date.now().toString(36) + '_' + this._uidCounter.toString(36);
  },
  markHostRowDirty(idx) {
    // Snapshot-based dirty tracker — diff the current `hostsConfig`
    // against `_hostsConfigBaseline` (captured on load + after each
    // successful save). This makes the dirty flag BIDIRECTIONAL:
    // bulk Apply vendor X → dirty=true; bulk Clear vendor X back to
    // baseline → dirty=false again. Pre-fix the flag latched to
    // true on first mutation and only Save / Load could un-set it,
    // so reverting changes via the UI didn't clear the amber Save
    // ring + "Unsaved" pulse-dot (user-flagged regression — bulk
    // Apply / Clear in Admin → Hosts didn't trigger dirty/undirty
    // cleanly).
    //
    // Microtask-debounce the JSON.stringify so bulk operations
    // (bulkApplySnmpVendor walks N rows and calls markHostRowDirty
    // per touched row) collapse to ONE snapshot compute per turn
    // instead of N × O(rows) stringify calls. Per-row test-result
    // clearing still runs SYNCHRONOUSLY because operators need it
    // immediate. Fallback to plain `true` if JSON serialisation
    // raises (circular ref / extremely large list) so we never
    // silently DROP a dirty state — the operator gets at-least the
    // latching boolean behaviour on the rare failure path.
    if (this.hostsTestResults && this.hostsTestResults[idx]) {
      delete this.hostsTestResults[idx];
    }
    if (this._hostsConfigDirtyPending) {
      return;
    }
    this._hostsConfigDirtyPending = true;
    queueMicrotask(() => {
      this._hostsConfigDirtyPending = false;
      try {
        this.hostsConfigDirty = (
          this._hostsConfigSnapshot() !== (this._hostsConfigBaseline || '')
        );
      } catch (_) {
        this.hostsConfigDirty = true;
      }
    });
  },
  // Snapshot helper — single source of truth for what's compared
  // against `_hostsConfigBaseline`. Strips per-row UI bookkeeping
  // fields that aren't part of the saved payload (`_uid` so a
  // freshly-minted row doesn't perpetually read as dirty against
  // an empty baseline; any other `_*` field added in future
  // should be added to the strip list here so it doesn't pollute
  // the dirty comparison).
  _hostsConfigSnapshot() {
    const list = Array.isArray(this.hostsConfig) ? this.hostsConfig : [];
    const stripped = list.map((row) => {
      const out = {};
      for (const k of Object.keys(row || {})) {
        if (k.charAt(0) === '_') {
          continue;
        }
        out[k] = row[k];
      }
      return out;
    });
    return JSON.stringify(stripped);
  },
  // Capture the load-time / post-save baseline. Mutations route
  // through `markHostRowDirty` which diffs against this snapshot.
  _captureHostsConfigBaseline() {
    try {
      this._hostsConfigBaseline = this._hostsConfigSnapshot();
    } catch (_) {
      this._hostsConfigBaseline = '';
    }
  },
  // Set ONE field on a row's http_probe sub-dict IN PLACE, then mark the
  // row dirty. The enable / content_match / accepted_status_codes inputs
  // used to each REPLACE row.http_probe via Object.assign({}, ...), but the
  // URLs textarea binds x-model="row.http_probe.urls_text" which mutates the
  // SAME object in place — so the per-keystroke object-reference churn from
  // the sibling inputs raced the textarea's binding and could drop the typed
  // URLs on save (cleared-on-save bug). Mutating one stable object reference
  // everywhere (matching the proven SNMP exclude_mounts pattern) removes the
  // hazard. Seeds urls_text on first creation so the textarea's x-model
  // always has a string to bind to rather than reading off undefined.
  setHttpProbeField(idx, key, value) {
    const row = this.hostsConfig && this.hostsConfig[idx];
    if (!row) {
      return;
    }
    if (!row.http_probe || typeof row.http_probe !== 'object') {
      row.http_probe = {urls_text: '', accepted_status_codes_text: ''};
    }
    row.http_probe[key] = value;
    this.markHostRowDirty(idx);
  },
  // Convenience auto-fill: when the operator first types the ID
  // and the Label is still blank, mirror the ID into Label so they
  // don't have to type it twice. Respects any Label they later
  // type — once it's populated, it stays.
  onHostRowEdit(idx, field, _value) {
    this.markHostRowDirty(idx);
    const row = this.hostsConfig[idx];
    if (!row) {
      return;
    }
    if (field === 'id') {
      // Operator-flagged: do NOT auto-fill `row.label` from the
      // typed id. Pre-fix the editor mirrored the id into the label
      // on the first keystroke so a blank label always defaulted to
      // the id value; operator wants to leave the label alone so it
      // reflects their own naming convention rather than the host
      // identifier. Empty label falls through cleanly — every
      // consumer (`hostDisplayName(h)` / `iconUrlFor()` / the
      // keyword-scan icon resolver) prefers `id` when label is
      // blank.
      // Promote the implicit "empty-id rows always render expanded"
      // rule to an EXPLICIT entry in hostsConfigExpanded the moment
      // any character lands in the ID field. Without this, typing
      // the very first character flips `!row.id` from true to false
      // and `isHostConfigExpanded(row)` falls through to the map —
      // which has no key for a fresh row — returning false and
      // collapsing the panel mid-keystroke. Sticky-expand on first
      // edit survives typing AND copy-paste.
      if (row._uid && !this.hostsConfigExpanded[row._uid]) {
        this.hostsConfigExpanded = {
          ...this.hostsConfigExpanded,
          [row._uid]: true,
        };
      }
    }
  },

  topLevelGroupNames(excludeIdx) {
    return (this.hostGroups || [])
      .map((g, i) => ({g, i}))
      .filter(({g, i}) => i !== excludeIdx
        && !g.parent_name
        && (g.name || '').trim())
      .map(({g}) => g.name);
  },

  // Total hosts in a top-level bucket = its own direct hosts +
  // every host in its sub-groups. Used for the heading count so
  // operators see the parent group's "true" reach (otherwise a
  // parent that has only sub-groups looks like 0 hosts).
  bucketTotalHosts(bucket) {
    const own = (bucket && bucket.hosts && bucket.hosts.length) || 0;
    const sub = ((bucket && bucket.children) || [])
      .reduce((acc, c) => acc + ((c && c.hosts && c.hosts.length) || 0), 0);
    return own + sub;
  },
  // Flat render-list for the Hosts view's host-row template.
  // Parent-direct hosts first, then each sub-group's hosts. The
  // host object is SPREAD into the entry (so `h.label`, `h.host`,
  // `h.providers` etc. all pass through unchanged) and two extra
  // markers are attached:
  // _sub_group   — the sub-group this row belongs to (null for
  //                parent-direct rows).
  // _sub_heading — true on the FIRST row of a sub-group; the
  //                template emits the heading once before that
  //                row, then false on subsequent rows.
  // Result: the SAME full host row markup (custom_number chip,
  // provider chips, asset location subline, drawer-expandable
  // content) renders for both parent and sub-group hosts. No
  // duplicated template — adding a feature in one place picks it
  // up everywhere.
  bucketRenderList(bucket) {
    const out = [];
    for (const h of (bucket.hosts || [])) {
      out.push(Object.assign({}, h, {_sub_group: null, _sub_heading: false}));
    }
    for (const sub of (bucket.children || [])) {
      const subHosts = sub.hosts || [];
      if (subHosts.length === 0) {
        // Empty sub-group. With "Hide hosts without agents" ON the
        // operator wants the noise reduced — skip the heading entirely
        // (the group has nothing to show). With the filter OFF, we
        // STILL skip the heading because the previous heading-only
        // marker created confusion (operator's preference iterated
        // multiple times — final landing: hide empties always; the
        // group definition is still visible in Admin → Host Groups).
        continue;
      }
      for (let i = 0; i < subHosts.length; i++) {
        out.push(Object.assign({}, subHosts[i], {
          _sub_group: sub.group,
          _sub_heading: i === 0,
        }));
      }
    }
    return out;
  },

  // Visual order for the groups editor — each top-level group
  // immediately followed by its sub-groups. Operator's `order`
  // field still drives top-level ordering; sub-group order within
  // a parent cluster is preserved by raw-array insertion order.
  // Keeps `origIdx` (the position in the raw `hostGroups` array)
  // so save-validation + per-row button handlers can reach back
  // to the storage without rebuilding the list.
  sortedGroupsForEditor() {
    const arr = (this.hostGroups || []).map((g, i) => ({g, origIdx: i}));
    // Pass 1: top-level rows in original order (preserving the
    // operator's move-up/move-down choices).
    const tops = arr.filter(e => !e.g.parent_name);
    // Pass 2: group sub-rows by their parent_name.
    const subs = new Map();
    for (const e of arr) {
      if (!e.g.parent_name) {
        continue;
      }
      const key = e.g.parent_name;
      if (!subs.has(key)) {
        subs.set(key, []);
      }
      subs.get(key).push(e);
    }
    // Weave: each top-level row followed by its children — but
    // skip the children when their parent is collapsed via the
    // editor-side toggle. Operator can still expand to edit them.
    //
    // `seen` MUST include kids whose parent exists even when we
    // skip rendering them (collapsed). Otherwise the orphan-catch
    // below re-adds every hidden child at the bottom of the list,
    // which is what produced the "hides first parent's kids only"
    // bug — collapsing parent B simply relocated B's kids to the
    // trailing orphan bucket instead of hiding them.
    const collapsed = new Set(this.hostGroupsEditorChildrenCollapsed || []);
    const out = [];
    const seen = new Set();
    for (const t of tops) {
      out.push(t);
      seen.add(t.origIdx);
      const kids = subs.get(t.g.name);
      if (!kids) {
        continue;
      }
      for (const k of kids) {
        seen.add(k.origIdx);
      }
      if (!collapsed.has(t.g.name)) {
        out.push(...kids);
      }
    }
    // True orphaned sub-groups (parent_name set but no matching
    // top-level group) still sink to the bottom so they stay
    // visible for repair rather than silently disappearing.
    // `saveHostGroups` rejects them with inline errors, but the
    // operator has to SEE them first.
    for (const e of arr) {
      if (!seen.has(e.origIdx)) {
        out.push(e);
      }
    }
    return out;
  },

  // Page slice of `sortedGroupsForEditor()` — keeps the same
  // `{g, origIdx, listIdx}` shape so the per-row buttons (move /
  // delete / sub-group add) reach back to the unsliced list with
  // their original indices intact. Page is clamped lazily so a
  // delete that drops the total below the current page falls back
  // to the new last page rather than rendering empty.
  pagedGroupsForEditor() {
    const all = this.sortedGroupsForEditor();
    const per = this.hostGroupsPerPage || 50;
    const totalPages = Math.max(1, Math.ceil(all.length / per));
    const page = Math.min(Math.max(1, this.hostGroupsPage), totalPages);
    const start = (page - 1) * per;
    // Re-emit as {g, origIdx, listIdx} so the template's existing
    // destructure pattern continues to work — listIdx points at the
    // ABSOLUTE position in the unpaged list, not the slice, so
    // move-up / move-down arithmetic stays correct.
    return all
      .slice(start, start + per)
      .map((entry, sliceIdx) => ({
        g: entry.g,
        origIdx: entry.origIdx,
        listIdx: start + sliceIdx,
      }));
  },
  // Bulk-collapse + scroll-to-top for the sticky action bar — the
  // groups editor has its own collapse state (per parent name)
  // separate from the Hosts editor; reuse the existing
  // collapseAllHostGroupChildren handler for the action bar's
  // "Collapse all" button.
  scrollToHostGroupsTop() {
    try {
      window.scrollTo({top: 0, behavior: 'smooth'});
    } catch {
      window.scrollTo(0, 0);
    }
  },

  // ---- Inline-field-error helpers ----
  // Keyed storage lives on `fieldErrors`. Callers set a specific
  // error text for a specific input (keyed by a stable "scope_idx_field"
  // id) and the templates render red-bordered inputs + an error
  // hint beneath. Clearing on @input means the red cue goes away
  // as soon as the operator starts fixing it — classic
  // jQuery-validate feel without the dependency.
  setFieldError(key, msg) {
    this.fieldErrors = {...this.fieldErrors, [key]: msg};
  },
  clearFieldError(key) {
    if (key in this.fieldErrors) {
      const next = {...this.fieldErrors};
      delete next[key];
      this.fieldErrors = next;
    }
  },
  clearFieldErrorsByPrefix(prefix) {
    const next = {};
    for (const k of Object.keys(this.fieldErrors || {})) {
      if (!k.startsWith(prefix)) {
        next[k] = this.fieldErrors[k];
      }
    }
    this.fieldErrors = next;
  },
  hasFieldError(key) {
    return !!(this.fieldErrors && this.fieldErrors[key]);
  },
  fieldError(key) {
    return (this.fieldErrors || {})[key] || '';
  },
  // Focus the DOM input whose x-model ends in the given field name
  // on the given row. Used after validation sets an error so the
  // operator's cursor lands on the first failing field.
  focusFirstFieldError() {
    const first = Object.keys(this.fieldErrors || {})[0];
    if (!first) {
      return;
    }
    // If the first error is keyed against a hostsConfig row that
    // lives on a different page,
    // navigate to that page BEFORE the DOM query — otherwise the
    // .field-invalid element doesn't exist and focus silently
    // no-ops, leaving the operator confused about why save failed.
    // Named capture group (?<idx>...) is ES2018 — JSHint's E016 predates it. // jshint ignore:line
    const m = first.match(/^host_(?<idx>\d+)_/); // jshint ignore:line
    if (m) {
      const rowIdx = parseInt(m.groups.idx, 10);
      const all = this.filteredHostsConfig();
      const pos = all.findIndex(({idx}) => idx === rowIdx);
      if (pos >= 0) {
        const per = this.hostsConfigPerPage || 50;
        this.hostsConfigGoToPage(Math.floor(pos / per) + 1);
      } else if ((this.hostsConfigFilter || '').trim()) {
        // Field error lives on a row that's been filtered out — the
        // page-jump above silently fails and the operator sees a
        // generic "Save failed" toast with no actionable target. Show
        // a SweetAlert with a one-click "Clear filter" action so they
        // can reach the offending row.
        if (typeof Swal !== 'undefined') {
          Swal.fire({
            icon: 'warning',
            title: this.t('admin_hosts.errors.filtered_title'),
            text: this.t('admin_hosts.errors.filtered_body'),
            confirmButtonText: this.t('admin_hosts.errors.filtered_clear'),
            showCancelButton: true,
            cancelButtonText: this.t('actions.cancel'),
          }).then((result) => {
            if (result.isConfirmed) {
              this.hostsConfigFilter = '';
              this.$nextTick(() => this.focusFirstFieldError());
            }
          });
          return;
        }
        this.showToast(
          this.t('admin_hosts.errors.filtered_body'),
          'error',
        );
        return;
      }
    }
    // Best-effort DOM lookup — errors are keyed by a stable id and
    // the templates bind `:class="hasFieldError('...')"` on the
    // input. A short delay lets Alpine finish rendering the error
    // state before we try to scroll into view. Bumped from 30 → 80
    // ms because we may have just changed the page above and Alpine
    // needs an extra tick to mount the new slice.
    setTimeout(() => {
      const el = document.querySelector('.field-invalid');
      if (el && typeof el.focus === 'function') {
        el.focus();
        if (typeof el.scrollIntoView === 'function') {
          el.scrollIntoView({block: 'center', behavior: 'smooth'});
        }
      }
    }, 80);
  },

  // Hosts view — group-related helpers.
  isGroupCollapsed(name) {
    return (this.hostGroupsCollapsed || []).includes(name);
  },
  toggleGroup(name) {
    const set = new Set(this.hostGroupsCollapsed || []);
    if (set.has(name)) {
      set.delete(name);
    } else {
      set.add(name);
    }
    this.hostGroupsCollapsed = Array.from(set);
    try {
      localStorage.setItem(
        'hostGroupsCollapsed',
        JSON.stringify(this.hostGroupsCollapsed),
      );
    } catch {
    }
  },
  expandAllGroups() {
    this.hostGroupsCollapsed = [];
    try {
      localStorage.setItem('hostGroupsCollapsed', '[]');
    } catch {
    }
  },
  collapseAllGroups() {
    const names = (this.hostGroups || []).map(g => g.name).filter(Boolean);
    // Also collapse the "ungrouped" bucket so the operator can fully
    // minimise the whole view. "" is the stable key for that bucket.
    names.push('');
    this.hostGroupsCollapsed = names;
    try {
      localStorage.setItem(
        'hostGroupsCollapsed',
        JSON.stringify(this.hostGroupsCollapsed),
      );
    } catch {
    }
  },
  // Build the grouped view: iterate the (already filtered+sorted)
  // host list, bucket each into the first group whose range covers
  // its custom_number. Anything unmatched lands in the trailing
  // ungrouped bucket. Groups are rendered in `order` order; the
  // ungrouped bucket always comes last.
  // Build the nested {parent → children → hosts} structure the
  // Hosts view renders. 2-level nesting only: a group with
  // `parent_name` set is a SUB-GROUP of that named top-level
  // group. Most-specific-match-wins: a host whose custom_number
  // falls inside a sub-group's range lands under that sub-group,
  // NOT under the parent (even though the parent's range also
  // contains it). Hosts that match a top-level group directly
  // but no sub-group appear in the parent's own host list.
  //
  // Shape:
  // [
  //   { group: {...top-level...}, hosts: [h, h],
  //     children: [
  //       { group: {...sub-group...}, hosts: [h, h] },
  //     ],
  //   },
  //   { group: null, hosts: [h, h] }   // Ungrouped, trailing
  // ]
  // Memoised result. — `groupedHosts()` is called on
  // every Alpine re-render. With 500 hosts × 30 groups the inner
  // O(N×M) walk is 15k comparisons per tick. Cache keyed on the
  // identities of the source arrays + the host list's length and the
  // groups list's length + a counter that increments on group save
  // (`hostGroupsRevision`) so a save explicitly busts even when the
  // length is unchanged.
  _groupedHostsCache: {key: '', value: null},
  async clearAssetClientSecret() {
    try {
      const ok = await (window.Swal ? Swal.fire({
        icon: 'warning',
        title: this.t('admin_assets.clear_secret_title'),
        text: this.t('admin_assets.clear_secret_text'),
        showCancelButton: true,
        confirmButtonText: this.t('actions.confirm'),
        cancelButtonText: this.t('actions.cancel'),
      }).then(r => !!r.isConfirmed) : confirm(this.t('toasts_extra.asset_clear_secret_prompt')));
      if (!ok) {
        return;
      }
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({clear_asset_inventory_client_secret: true}),
      });
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      await this.loadSettings();
      this.showToast(this.t('admin_assets.secret_cleared'), 'success');
    } catch (e) {
      this.showToast(this.t('admin_assets.save_failed') + ': ' + e.message, 'error');
    }
  },
  async clearAssetLifetimeToken() {
    try {
      const ok = await (window.Swal ? Swal.fire({
        icon: 'warning',
        title: this.t('admin_assets.clear_lifetime_token_title'),
        text: this.t('admin_assets.clear_lifetime_token_text'),
        showCancelButton: true,
        confirmButtonText: this.t('actions.confirm'),
        cancelButtonText: this.t('actions.cancel'),
      }).then(r => !!r.isConfirmed) : confirm(this.t('toasts_extra.asset_clear_token_prompt')));
      if (!ok) {
        return;
      }
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({clear_asset_inventory_lifetime_token: true}),
      });
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      await this.loadSettings();
      this.showToast(this.t('admin_assets.lifetime_token_cleared'), 'success');
    } catch (e) {
      this.showToast(this.t('admin_assets.save_failed') + ': ' + e.message, 'error');
    }
  },
  // Generic secret-clear helper — the seven admin-tab secret inputs
  // (asset client_secret / asset lifetime_token / ssh password / ssh
  // passphrase / ssh private_key / beszel_password / pulse_token /
  // webmin_password / portainer_api_key / oidc_client_secret) all
  // share the same SweetAlert-confirm + POST-clear-flag shape.
  // Pre-fix each had its own `clearXxx` function with copy-pasted
  // body. This canonical helper takes the i18n key family +
  // backend-flag name + post-clear toast key and runs the flow.
  // Kept the existing `clearAssetClientSecret` / etc. wrappers so
  // their callers stay one-line.
  async _clearSecret({flag, titleKey, textKey, toastKey}) {
    try {
      const ok = await (window.Swal ? Swal.fire({
        icon: 'warning',
        title: this.t(titleKey),
        text: this.t(textKey),
        showCancelButton: true,
        confirmButtonText: this.t('actions.confirm'),
        cancelButtonText: this.t('actions.cancel'),
      }).then(r => !!r.isConfirmed) : confirm(this.t(textKey)));
      if (!ok) {
        return;
      }
      const body = {};
      body[flag] = true;
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      await this.loadSettings();
      this.showToast(this.t(toastKey), 'success');
    } catch (e) {
      this.showToast((this.t('toasts.save_failed') || 'Save failed') + ': ' + e.message, 'error');
    }
  },
  async clearBeszelPassword() {
    return this._clearSecret({
      flag: 'clear_beszel_password',
      titleKey: 'settings.host_stats.clear_secret_title',
      textKey: 'settings.host_stats.beszel_password_clear_text',
      toastKey: 'settings.host_stats.beszel_password_cleared',
    });
  },
  async clearPulseToken() {
    return this._clearSecret({
      flag: 'clear_pulse_token',
      titleKey: 'settings.host_stats.clear_secret_title',
      textKey: 'settings.host_stats.pulse_token_clear_text',
      toastKey: 'settings.host_stats.pulse_token_cleared',
    });
  },
  async clearWebminPassword() {
    return this._clearSecret({
      flag: 'clear_webmin_password',
      titleKey: 'settings.host_stats.clear_secret_title',
      textKey: 'settings.host_stats.webmin_password_clear_text',
      toastKey: 'settings.host_stats.webmin_password_cleared',
    });
  },
  /** Walk the asset cache for cert children of one host asset.
   *
   * Cert rows are identified by Type.ShortName (case-insensitive) in
   * the set {CERT, TLS, SSL} OR Type.Name matching /cert|ssl|tls/i,
   * AND a parent reference matching the host asset's ID. The parent
   * field's casing varies upstream; we walk every plausible key. The
   * returned shape is `{name, issuer, expires_at, asset_id, link_url}` —
   * `link_url` falls back to the cert asset's own ID-as-URL so the
   * drawer can always link somewhere useful even when upstream
   * doesn't surface a dedicated URL field.
   */
  _certsForAsset(hostAsset, allAssets) {
    if (!hostAsset || !Array.isArray(allAssets) || !allAssets.length) {
      return [];
    }
    const hostId = hostAsset.ID ?? hostAsset.id ?? null;
    if (hostId == null) {
      return [];
    }
    const out = [];
    const isCertType = (t) => {
      if (!t) {
        return false;
      }
      if (typeof t === 'string') {
        return /cert|ssl|tls/i.test(t);
      }
      if (typeof t === 'object') {
        const short = String(t.ShortName || t.shortname || t.short || t.Code || '').trim().toUpperCase();
        if (short === 'CERT' || short === 'TLS' || short === 'SSL') {
          return true;
        }
        const name = String(t.Name || t.name || t.CalculatedName || '').trim();
        return /cert|ssl|tls/i.test(name);
      }
      return false;
    };
    const matchParent = (row) => {
      // Try every plausible parent-reference field shape.
      const candidates = [
        row.ParentID, row.parent_id, row.ParentId,
        row.Parent, row.parent,
        row.ParentAsset, row.parent_asset,
      ];
      for (const v of candidates) {
        if (v == null) {
          continue;
        }
        // Direct scalar match (parent id literal).
        if (typeof v === 'number' || typeof v === 'string') {
          if (String(v) === String(hostId)) {
            return true;
          }
          continue;
        }
        // Nested object — pull the ID out of it.
        if (typeof v === 'object') {
          const inner = v.ID ?? v.id;
          if (inner != null && String(inner) === String(hostId)) {
            return true;
          }
        }
      }
      return false;
    };
    for (const row of allAssets) {
      if (!row || row === hostAsset) {
        continue;
      }
      if (!isCertType(row.Type || row.type)) {
        continue;
      }
      if (!matchParent(row)) {
        continue;
      }
      const certId = row.ID ?? row.id ?? null;
      const name = String(row.Name || row.name || row.CommonName || row.common_name || '').trim();
      const issuer = String(row.Issuer || row.issuer || row.IssuedBy || row.issued_by || '').trim();
      const expiresAt = String(row.ExpiresOn || row.expires_on || row.NotAfter || row.not_after || '').trim();
      // Link URL — prefer an explicit field, fall back to the asset
      // record's own URL alias. Drawer renders a chevron; without a
      // URL the link still surfaces (best-effort).
      const linkUrl = String(row.URL || row.Url || row.url || row.Link || row.link || '').trim();
      out.push({
        asset_id: certId,
        name: name || (certId != null ? String(certId) : ''),
        issuer,
        expires_at: expiresAt,
        link_url: linkUrl,
      });
    }
    return out;
  },
  // no `type_short`, log the available keys ONCE so the operator can
  // tell us the correct upstream field name. The set is process-wide
  // so we don't flood the console — first asset that misses logs.
  hostTypePrefix(h) {
    const a = this.assetForHost(h);
    if (!a) {
      return '';
    }
    // Diagnostic — fires once per asset id when type_short is empty
    // but type IS present, suggesting the upstream Type object uses
    // a field name we don't recognise yet.
    if (!a.type_short && a.type && a._raw && a._raw.Type
      && typeof a._raw.Type === 'object') {
      if (!this._loggedMissingTypeShort) {
        this._loggedMissingTypeShort = new Set();
      }
      // Diagnostic console.info that previously dumped the full Type
      // object on every page load was operator-flagged as noise --
      // the fallback derived-acronym path is the correct behaviour
      // when the upstream Type record has no ShortName, not a bug
      // worth surfacing. The dedup Set is kept (in case we want to
      // restore the log under a debug flag later) but the log is
      // intentionally elided.
      const aid = String(a.id || a.type || '');
      this._loggedMissingTypeShort.add(aid);
    }
    // Source of truth is the asset's `ShortName` (<asset-api-host> MDI
    // §5.2.4, exposed by `shape_asset` as `type_short`). When the
    // operator hasn't set a ShortName upstream, fall back to the
    // long Type.Name verbatim — never invent an abbreviation,
    // since the operator's asset record is authoritative.
    const label = (a.type_short || a.type || '').trim();
    return label ? '[' + label + '] ' : '';
  },
  // Raw asset row from the cached snapshot — for the debug panel.
  // The shaped resolver (`assetForHost`) drops fields the drawer
  // doesn't render; this returns the unfiltered upstream object.
  rawAssetForHost(h) {
    if (!h || h.custom_number == null || h.custom_number === '') {
      return null;
    }
    const assets = (this.assetCache && Array.isArray(this.assetCache.assets))
      ? this.assetCache.assets : null;
    if (!assets || !assets.length) {
      return null;
    }
    const n = parseInt(h.custom_number, 10);
    if (!Number.isFinite(n)) {
      return null;
    }
    for (const a of assets) {
      if (!a) {
        continue;
      }
      const cn = a.CustomNumber ?? a.custom_number ?? a.number ?? a.id;
      if (parseInt(cn, 10) === n) {
        return a;
      }
    }
    return null;
  },
  observeHostRow(el, id) {
    if (!el || !id) {
      return;
    }
    const obs = this._ensureHostRowObserver();
    if (!obs) {
      // Browser without IntersectionObserver — fall back to eager
      // fetch so functionality is preserved (some old WebViews lack
      // IO). This path is unreachable on every modern browser.
      if (!this._hostSeenIds.has(id)) {
        this._hostSeenIds.add(id);
        this.refreshHostRow(id).catch(() => {
        });
      }
      return;
    }
    obs.observe(el);
  },
  // concurrency-capped queue runner shared between the IO
  // observer's debounced flush and loadHosts' poll-driven fan-out.
  // Resolves PARALLEL the same way loadHosts does (per-call read of
  // `me.client_config.hosts_parallel_fetch`, fallback 6) so an
  // operator's Admin → Config Save takes effect on the next call.
  async _runHostRefreshQueue(ids) {
    // Push every requested id onto the SHARED queue and spawn
    // workers only up to the cap. Pre-fix this had its own private
    // queue + pool independent of the polling-path pool, so a
    // burst of `_runHostRefreshQueue(...)` during an active
    // `loadHosts(...)` doubled the in-flight count beyond the cap.
    // Same anti-pattern applies to direct `refreshHostRow` calls
    // from SSE event handlers — those should also push here via
    // `_hostObserverPending.add(id) + scheduleFlush()` so every
    // path shares one cap.
    for (const id of (ids || [])) {
      if (id) {
        this._enqueueHostRefresh(id);
      }
    }
    await this._ensureHostRefreshWorkers();
  },
  // Shared queue + worker count used by ALL host-refresh call sites
  // (lazy IO observer, SSE event handlers, polling fan-out). Capped
  // by `me.client_config.hosts_parallel_fetch` regardless of caller.
  _hostRefreshQueue: null,
  _hostRefreshWorkerCount: 0,
  _enqueueHostRefresh(id) {
    this._hostRefreshQueue = this._hostRefreshQueue || [];
    // Cheap dedupe: skip if already queued. Uses indexOf since the
    // queue is bounded by host count; for fleets > 200 hosts a
    // Set-backed mirror would be a future optimisation.
    if (this._hostRefreshQueue.indexOf(id) === -1) {
      this._hostRefreshQueue.push(id);
    }
  },
  async _ensureHostRefreshWorkers() {
    const PARALLEL = (this.me && this.me.client_config
      && this.me.client_config.hosts_parallel_fetch) || 6;
    const need = Math.max(0, PARALLEL - this._hostRefreshWorkerCount);
    if (!need) {
      return;
    }
    const queue = this._hostRefreshQueue || [];
    const slots = Math.min(need, queue.length);
    if (!slots) {
      return;
    }
    const worker = async () => {
      this._hostRefreshWorkerCount += 1;
      try {
        while (this._hostRefreshQueue && this._hostRefreshQueue.length) {
          const id = this._hostRefreshQueue.shift();
          if (!id) {
            break;
          }
          try {
            await this.refreshHostRow(id);
          } catch { /* per-row failure stays isolated */
          }
        }
      } finally {
        this._hostRefreshWorkerCount -= 1;
      }
    };
    const workers = [];
    for (let i = 0; i < slots; i++) {
      workers.push(worker());
    }
    await Promise.all(workers);
  },

  // Translate an HTTP error response (status + body text) into a
  // friendly, operator-actionable message. Centralised so any future
  // long-running endpoint that can hit a reverse-proxy timeout
  // (504 from openresty / nginx / NPM) gets the same UX.
  //
  // Detection rules:
  // 1. Status 504 / 502 / 503 → reverse-proxy timeout/error. The
  //    backend may still be running; show an actionable hint.
  // 2. Body starts with `<` (HTML / XML) → upstream proxy page that
  //    has nothing operator-readable in it. Suppress entirely.
  // 3. Body looks like JSON `{"detail":"..."}` → extract the detail.
  // 4. Body is plain text shorter than 200 chars → pass through.
  // 5. Anything longer is suspicious; fall back to status-only.
  _friendlyHttpError(status, body, i18nNamespace) {
    const ns = i18nNamespace || 'common.errors';
    const sCode = String(status || 0);
    const trimmed = (body || '').trim();
    // 1. Reverse-proxy timeouts (504 / 502 / 503).
    if (status === 504) {
      return this.t(ns + '.gateway_timeout')
        || this.t('common.errors.gateway_timeout')
        || 'The reverse proxy timed out — the request may still be running on the backend. Wait ~30s and refresh; results will appear if the backend completed.';
    }
    if (status === 502) {
      return this.t(ns + '.bad_gateway')
        || this.t('common.errors.bad_gateway')
        || 'The reverse proxy returned a bad-gateway error — the backend may be restarting. Try again in a moment.';
    }
    if (status === 503) {
      return this.t(ns + '.service_unavailable')
        || this.t('common.errors.service_unavailable')
        || 'Service temporarily unavailable. Try again in a moment.';
    }
    // 2. HTML body — strip and surface status only.
    if (trimmed.startsWith('<')) {
      return this.t('common.errors.http_status', {status: sCode})
        || ('HTTP ' + sCode);
    }
    // 3. JSON `{"detail": "..."}`.
    if (trimmed.startsWith('{')) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed.detail === 'string' && parsed.detail) {
          return parsed.detail;
        }
      } catch { /* not JSON — fall through */
      }
    }
    // 4. Short plain text — pass through.
    if (trimmed && trimmed.length <= 200) {
      return trimmed;
    }
    // 5. Fallback.
    return this.t('common.errors.http_status', {status: sCode})
      || ('HTTP ' + sCode);
  },

  // Diff helper: ports listed in `hosts_config[].services[]` but
  // NOT seen open in the latest scan. Surfaces "expected listener is
  // down right now" without raising it as a failure (the operator
  // could have disabled the service; surfacing as info-grey is the
  // honest signal).
  curatedOnlyServices(host) {
    if (!host) {
      return [];
    }
    const detected = new Set((host.detected_ports || []).map(p => Number(p.port)));
    const curated = Array.isArray(host.services) ? host.services : [];
    return curated.filter(s => {
      const port = Number(s && s.port);
      return port > 0 && !detected.has(port);
    }).map(s => ({port: Number(s.port), name: s.name || s.label || ''}));
  },

  // Detected ports sorted by port-number ascending, regardless of
  // protocol — TCP and UDP interleave so the chip strip reads as
  // a numeric sequence (`22/tcp · 53/udp · 80/tcp · 443/tcp · …`)
  // instead of grouping all TCP first then all UDP. Operator-
  // requested for at-a-glance scanning of which port numbers are
  // open. Stable: ports tie-break on protocol so UDP/TCP duplicates
  // (rare but possible — e.g. dual-stack DNS on 53) order
  // deterministically across renders.
  sortedDetectedPorts(host) {
    const arr = (host && Array.isArray(host.detected_ports)) ? host.detected_ports : [];
    if (arr.length < 2) {
      return arr;
    }
    return [...arr].sort((a, b) => {
      const pa = Number(a && a.port) || 0;
      const pb = Number(b && b.port) || 0;
      if (pa !== pb) {
        return pa - pb;
      }
      const ta = (a && a.protocol) === 'udp' ? 1 : 0;
      const tb = (b && b.protocol) === 'udp' ? 1 : 0;
      return ta - tb;
    });
  },

  // Host-drawer apps sorted by their primary (lowest) configured port,
  // ascending — operator-requested so the Apps card + chip strip read
  // as a port-ordered list instead of services[] array order. Primary
  // port resolves from the chip's probe.ports[], then the bound catalog
  // template's default_ports[], then the chip's single top-level port.
  // Apps with no resolvable port sink to the bottom (tie-break by name).
  // Does NOT mutate host.apps — returns a sorted copy.
  sortedHostApps(host) {
    const apps = (host && Array.isArray(host.apps)) ? host.apps : [];
    if (apps.length < 2) {
      return apps;
    }
    // Per-(apps array) memo — one sort per apps-array identity, shared
    // across the 3 Hosts-view bindings + re-renders until the host's apps
    // array is replaced by the next reconcile. Returns a STABLE reference
    // so the keyed x-for skips its re-diff when nothing changed.
    const _memo = _sortedHostAppsMemo.get(apps);
    if (_memo !== undefined) {
      return _memo;
    }
    const services = (host && Array.isArray(host.services)) ? host.services : [];
    const portOf = (app) => {
      const ports = [];
      const pb = (app.probe && Array.isArray(app.probe.ports)) ? app.probe.ports : [];
      for (const pp of pb) {
        const n = Number(pp && pp.port);
        if (n > 0) {
          ports.push(n);
        }
      }
      if (!ports.length && app.catalog && Array.isArray(app.catalog.default_ports)) {
        for (const pp of app.catalog.default_ports) {
          const n = Number(pp && pp.port);
          if (n > 0) {
            ports.push(n);
          }
        }
      }
      const svc = services[app.service_idx];
      if (!ports.length && svc && Number(svc.port) > 0) {
        ports.push(Number(svc.port));
      }
      return ports.length ? Math.min(...ports) : Number.MAX_SAFE_INTEGER;
    };
    const sorted = [...apps].sort((a, b) => {
      const pa = portOf(a);
      const pb = portOf(b);
      if (pa !== pb) {
        return pa - pb;
      }
      return (a.name || '').toLowerCase().localeCompare((b.name || '').toLowerCase());
    });
    _sortedHostAppsMemo.set(apps, sorted);
    return sorted;
  },
  // Convenience: clear the provider filter ("All" pill click).
  clearHostsProviderFilter() {
    // "Show all" clears EVERY active host filter — the provider set AND
    // the Problem filter — so one click returns to the full list (pre-fix
    // it only cleared the provider set, so with the Problem pill active
    // "Show all" appeared to do nothing and the operator had to re-press
    // Problem to toggle it off). Deliberately does NOT touch
    // `hostsHideUnconfigured` — that's a separate persistent preference,
    // not a filter "Show all" should override.
    this.hostsProviderFilter = new Set();
    this.hostsProblemFilter = false;
    try {
      if (typeof sessionStorage !== 'undefined') {
        sessionStorage.removeItem('hostsProviderFilter');
        sessionStorage.removeItem('hostsProblemFilter');
      }
    } catch { /* ignore */
    }
  },
  // Whether `name` is currently in the active filter set.
  isHostsProviderFilterActive(name) {
    return !!(this.hostsProviderFilter && this.hostsProviderFilter.has(name));
  },

  // Status taxonomy considered "in trouble" — matches the Telegram
  // AI context's `problem_hosts` block for symmetry. `unconfigured`
  // hosts are intentionally NOT in this set per the project conventions (curated
  // rows with no provider mapped are inventory-only entries, not
  // outages).
  _PROBLEM_HOST_STATUSES: new Set(['down', 'paused', 'unknown']),
  isProblemHost(h) {
    if (!h) {
      return false;
    }
    const st = String(h.status || '').toLowerCase();
    return this._PROBLEM_HOST_STATUSES.has(st);
  },
  // Count of hosts currently in trouble — drives the chip badge AND
  // the Stats dashboard "Problem hosts" tile.
  problemHostCount() {
    const list = this.hosts || [];
    let n = 0;
    for (const h of list) {
      if (this.isProblemHost(h)) {
        n++;
      }
    }
    return n;
  },
  toggleProblemHostsFilter() {
    this.hostsProblemFilter = !this.hostsProblemFilter;
    try {
      if (typeof sessionStorage !== 'undefined') {
        if (this.hostsProblemFilter) {
          sessionStorage.setItem('hostsProblemFilter', '1');
        } else {
          sessionStorage.removeItem('hostsProblemFilter');
        }
      }
    } catch { /* private mode / quota — ignore */
    }
  },
  // True when at least one PER-PROVIDER pause is active on this host
  // (provider_pause_state[x].paused). Distinct from whole-host
  // sampling_paused: a host can have e.g. SNMP auto-paused while the rest
  // of its providers keep reporting.
  hostHasPausedProvider(h) {
    const p = h && h.provider_pause_state;
    if (!p || typeof p !== 'object') {
      return false;
    }
    for (const k in p) {
      if (p[k] && p[k].paused) {
        return true;
      }
    }
    return false;
  },
  // True when the host is whole-host paused OR has any provider paused —
  // the set the "Paused (N)" filter + the paused badge key on.
  hostIsPaused(h) {
    return !!(h && (h.sampling_paused || this.hostHasPausedProvider(h)));
  },
  // Count of hosts with a paused sampler / provider — drives the "Paused (N)"
  // toolbar chip badge.
  pausedHostCount() {
    const list = this.hosts || [];
    let n = 0;
    for (const h of list) {
      if (this.hostIsPaused(h)) {
        n++;
      }
    }
    return n;
  },
  toggleHostsPausedFilter() {
    this.hostsPausedFilter = !this.hostsPausedFilter;
    try {
      if (typeof sessionStorage !== 'undefined') {
        if (this.hostsPausedFilter) {
          sessionStorage.setItem('hostsPausedFilter', '1');
        } else {
          sessionStorage.removeItem('hostsPausedFilter');
        }
      }
    } catch { /* private mode / quota — ignore */
    }
  },
  // Count of curated rows that have NO provider field mapped.
  // Used by the synthetic 'none' chip to surface "how many
  // inventory-only hosts are sitting on the page right now?".
  hostsWithNoProviderCount() {
    return (this.hosts || []).filter(h => !this.hostHasAgent(h)).length;
  },
  // Status chip for a provider in the Hosts toolbar. Combines
  // "enabled in settings" with "actually returned data for at least
  // one host" so operators spot misconfigs fast.
  hostsProviderState(name) {
    const active = (this.hostsActiveSources || []).includes(name);
    const err = (this.hostsProviderErrors || {})[name];
    // matchCount = hosts where the probe successfully returned data
    // (drives the ✓ N tooltip subtitle).
    const matchCount = (this.hosts || [])
      .filter(h => (h.providers || []).includes(name)).length;
    // configuredCount = hosts whose curated config maps them to this
    // provider, regardless of whether the latest probe succeeded.
    // This is the load-bearing signal for chip visibility: "any host
    // CARES about this provider" rather than "the provider is
    // currently returning data". The former survives a transient
    // hub outage so the red ✗ chip still surfaces; the latter would
    // hide on every outage and silently lose the visibility the
    // operator most needs.
    const configuredCount = this._hostsConfiguredForProvider(name);
    // Hide the chip entirely when no curated host has the provider
    // mapped — that's noise on the toolbar regardless of error state.
    // Operator-flagged: Webmin chip kept rendering as a red ✗ even
    // when zero hosts had `webmin_name` set, because the backend
    // stamps `provider_errors["webmin"] = "missing user / password"`
    // whenever the CSV lists "webmin" without credentials. That
    // error is about configuration absence, not about probe failures
    // against active hosts — when no host is using the provider,
    // the toolbar chip has nothing to report. Admin → Providers
    // is the right surface for setup-gap nagging; the toolbar chip
    // is for "providers I care about" at a glance.
    if (configuredCount === 0) {
      return {visible: false, cls: '', icon: '', title: '', styled: false};
    }
    // Configured-but-not-active state — at least one host has the
    // provider mapped in its curated config BUT the operator hasn't
    // added the provider to `host_stats_source`. The provider's
    // master toggle in Admin → Providers is OFF, so the sampler
    // never runs against it. Surface as a muted (amber) chip with
    // a tooltip explaining the gap so the operator notices without
    // having to remember which sub-tab to click. Without this
    // branch the chip stayed hidden and a freshly-enrolled provider
    // (e.g. http_probe with URLs configured per-host but the master
    // toggle never flipped) looked like the per-row chip was
    // broken.
    if (!active && !err) {
      return {
        visible: true, cls: 'pill-warning', icon: '⚠',
        title: this.t('hosts_extra.provider_filter.title_configured_inactive',
            {name, count: configuredCount})
          || (`${name} — ${configuredCount} host(s) mapped but provider not enabled in Admin → Providers`),
        styled: false,
      };
    }
    // tooltip titles routed through i18n.
    if (err) {
      return {
        visible: true, cls: 'pill-error', icon: '✗',
        title: this.t('hosts_extra.provider_filter.title_error', {name, error: err}),
        styled: false,
      };
    }
    // Healthy state — use the operator-customised provider colour
    // via `pill-custom` + `providerChipStyle()`.
    // The fixed `pill-ok` green ignored Settings → Providers colour
    // overrides; flip to pill-custom so the toolbar chip matches the
    // per-row chip's colouring.
    const titleKey = matchCount === 1
      ? 'hosts_extra.provider_filter.title_match_one'
      : 'hosts_extra.provider_filter.title_match_many';
    return {
      visible: true, cls: 'pill-custom', icon: '✓',
      title: this.t(titleKey, {name, count: matchCount}),
      styled: true,
    };
  },
};
