// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA keyboard hotkey + button-group arrow-key navigation.

export default {
  showHotkeys: false,
  // --- Keyboard shortcuts ---------------------------------------------
  // Single source of truth — the help modal renders straight from this.
  // Each entry: { keys: ['r'], label: 'Refresh', run: () => {...} }.
  hotkeyGroups() {
    // Titles + labels resolve through t() so the entire help modal
    // translates in place. Keys + run handlers stay the same across
    // languages — shortcut bindings are locale-independent.
    //
    // CONVENTION (post-migration, 2026-05-09): every binding requires
    // Cmd/Ctrl (matches the Cmd-K palette pattern) so a bare typed
    // character — `/`, `1`, `?`, `r`, `t`, etc. — never fires a
    // shortcut by accident from an input or focused element. Bulk
    // operations layer Shift on top (Cmd/Ctrl+Shift+<key>) to
    // distinguish destructive bulk actions from single-item / nav
    // actions. The display column uses the literal modifier list
    // (`['Cmd/Ctrl', 'K']` etc.); the matcher in `handleHotkey`
    // reads the LAST element as the character key and the
    // preceding elements as modifiers (matched via `e.ctrlKey ||
    // e.metaKey` for Cmd/Ctrl + `e.shiftKey` for Shift).
    //
    // Operator-validated rule (2026-05-09): we DO NOT claim
    // browser-default keys via capture-phase intercept; instead
    // we pick alternate non-colliding keys. Mac browsers handle
    // some Cmd combos at a level the page can't preventDefault:
    // Cmd+R / Cmd+Shift+R (reload) and Cmd+Shift+L / Cmd+J (some
    // OS or Chrome feature) silently fail. Picks below avoid
    // those.
    //
    // Cmd/Ctrl+A (browser select-all) is INTENTIONALLY NOT BOUND —
    // operators select-all-text in inputs frequently; the previous
    // bare `a` "select all visible" binding migrated to Cmd/Ctrl+I
    // (I = "items" / "available updates") to preserve text-edit
    // ergonomics.
    const _t = (k) => this.t(k);
    return [
      {
        title: _t('hotkeys.groups.navigate'),
        items: [
          {keys: ['Cmd/Ctrl', '/'], label: _t('hotkeys.items.focus_search'), run: () => this.$refs.searchBox?.focus()},
          {keys: ['Cmd/Ctrl', '1'], label: _t('hotkeys.items.view_stacks'), run: () => this.view = 'stacks'},
          {keys: ['Cmd/Ctrl', '2'], label: _t('hotkeys.items.view_services'), run: () => this.view = 'services'},
          {keys: ['Cmd/Ctrl', '3'], label: _t('hotkeys.items.view_nodes'), run: () => this.view = 'nodes'},
          {keys: ['Cmd/Ctrl', '4'], label: _t('hotkeys.items.view_hosts'), run: () => this.view = 'hosts'},
          {keys: ['Cmd/Ctrl', '5'], label: _t('hotkeys.items.view_history'), run: () => this.view = 'history'},
          {keys: ['Cmd/Ctrl', 'Shift', '/'], label: _t('hotkeys.items.show_help'), run: () => this.showHotkeys = true},
          {keys: ['Cmd/Ctrl', 'B'], label: _t('hotkeys.items.notifications'), run: () => this.openNotificationsPopup()},
          {keys: ['Cmd/Ctrl', 'K'], label: _t('hotkeys.items.command_palette'), run: () => this.openCommandPalette()},
          {keys: ['Esc'], label: _t('hotkeys.items.close_clear'), run: null, note: _t('hotkeys.items.close_clear_note')},
        ],
      },
      {
        title: _t('hotkeys.groups.refresh_theme'),
        items: [
          // Cmd/Ctrl+R is browser reload — uninterceptable on Mac.
          // Cmd/Ctrl+; (semicolon) is collision-free.
          {keys: ['Cmd/Ctrl', ';'], label: _t('hotkeys.items.refresh_cached'), run: () => this.refresh(false)},
          // Cmd/Ctrl+Shift+R is browser hard-reload — same story.
          {keys: ['Cmd/Ctrl', 'Shift', ';'], label: _t('hotkeys.items.refresh_force'), run: () => this.refresh(true)},
          // Cmd/Ctrl+J is Chrome "Downloads" on Mac; Cmd+, is
          // typically the macOS app-preferences shortcut but is
          // browser-free for keystrokes delivered to the page.
          {keys: ['Cmd/Ctrl', ','], label: _t('hotkeys.items.cycle_theme'), run: () => this.cycleTheme()},
        ],
      },
      {
        title: _t('hotkeys.groups.selection'),
        items: [
          {keys: ['Cmd/Ctrl', 'I'], label: _t('hotkeys.items.select_all_visible'), run: () => this.selectAllVisible()},
          {keys: ['Cmd/Ctrl', 'U'], label: _t('hotkeys.items.select_updates'), run: () => this.selectUpdatesOnly()},
          {keys: ['Cmd/Ctrl', '.'], label: _t('hotkeys.items.clear_selection'), run: () => this.clearSelection()},
        ],
      },
      {
        title: _t('hotkeys.groups.bulk'),
        items: [
          {keys: ['Cmd/Ctrl', 'Shift', 'U'], label: _t('hotkeys.items.bulk_update'), run: () => this.selectionUpdatable().length && this.bulkUpdate()},
          {keys: ['Cmd/Ctrl', 'Shift', 'T'], label: _t('hotkeys.items.bulk_restart'), run: () => this.selectionRestartable().length && this.bulkRestart()},
          {keys: ['Cmd/Ctrl', 'Shift', 'D'], label: _t('hotkeys.items.bulk_remove'), run: () => this.selectionRemovable().length && this.bulkRemove()},
          // Cleanup all stopped/failed/orphaned containers — the
          // topbar red Cleanup button. Operator-confirmed Mac
          // shortcut Cmd+Shift+L is intercepted by an OS-level
          // binding (couldn't reach the page); swapped to
          // Cmd+Shift+, (comma) — collision-free on every major
          // platform, keeps the bulk = Cmd+Shift+<key> pattern
          // that distinguishes destructive bulk from single-item
          // / nav actions. Cmd+. (period) is reserved for
          // clear-selection (universal "abort current action"
          // convention).
          {keys: ['Cmd/Ctrl', 'Shift', ','], label: _t('hotkeys.items.bulk_cleanup'), run: () => (typeof this.bulkRemoveAll === 'function') && this.bulkRemoveAll()},
        ],
      },
      {
        title: _t('hotkeys.groups.stacks_view'),
        items: [
          {keys: ['Cmd/Ctrl', 'E'], label: _t('hotkeys.items.expand_all'), run: () => this.expandAllStacks()},
          {keys: ['Cmd/Ctrl', 'Shift', 'E'], label: _t('hotkeys.items.collapse_all'), run: () => this.collapseAllStacks()},
        ],
      },
    ];
  },
  handleHotkey(e) {
    const target = e.target || null;
    const active = document.activeElement || null;
    const el = active;
    // Escape works everywhere, including from inside an input — it's the
    // universal "get me out of here" key. Handle it BEFORE any guards.
    if (e.key === 'Escape') {
      if (this.commandPaletteOpen) {
        this.closeCommandPalette();
        e.preventDefault();
        return;
      }
      if (this.userMenuOpen) {
        this.userMenuOpen = false;
        e.preventDefault();
        return;
      }
      if (this.showHotkeys) {
        this.showHotkeys = false;
        e.preventDefault();
        return;
      }
      if (this.showNotificationsPopup) {
        this.showNotificationsPopup = false;
        e.preventDefault();
        return;
      }
      // Terminal modal owns ALL keystrokes when active EXCEPT Esc.
      // Closing on Esc is the universal "get me out" affordance even
      // though it would otherwise be a legitimate keystroke for the
      // shell — operators can always reopen, and the alternative is
      // a Trap with no fallback.
      if (this.terminalModalOpen) {
        this.closeHostTerminal();
        e.preventDefault();
        return;
      }
      // AI dashboard modal (Admin → AI) and schedule-editor modal
      // weren't in the cascade pre-fix — clicking the X / backdrop
      // was the only way out. Both sit above the drawers because
      // they're full-screen overlays the operator opened explicitly.
      if (this.aiModalKey) {
        this.closeAiModal();
        e.preventDefault();
        return;
      }
      if (this.editingSchedule) {
        this.cancelEditSchedule();
        e.preventDefault();
        return;
      }
      // AI sidebar inline-confirm chip — pending destructive
      // confirmation. WCAG 3.2.4 + standard alertdialog UX
      // expectation: Escape dismisses the confirm without firing
      // the action. Pre-fix the operator had to click Cancel to
      // dismiss; Esc now triggers `cancelInlineAction` for the
      // most-recent pending-confirm turn. Slots ABOVE the sidebar-
      // close branch so the confirm dismisses without also closing
      // the chat.
      if (this.aiSidebarOpen && Array.isArray(this.aiConversation)) {
        const _pendingIdx = this.aiConversation
          .map((t, idx) => (t && t.pending_confirm) ? idx : -1)
          .filter(idx => idx >= 0)
          .pop();  // most-recent pending — there should be at most one
        if (_pendingIdx !== undefined && _pendingIdx >= 0
          && typeof this.cancelInlineAction === 'function') {
          this.cancelInlineAction(_pendingIdx);
          e.preventDefault();
          return;
        }
      }
      // AI sidebar slots above drawerHost / drawerItem so ESC closes the
      // chat first when both are open — same priority order as
      // commandPaletteOpen (which is the AI sidebar's mode-cousin).
      // The textarea-local ESC handler in `aiSidebarHandleKeydown`
      // covers the focus-on-input case; this branch covers Send /
      // slash-picker / feedback chip / anywhere else the operator's
      // focus lands while the drawer is open.
      if (this.aiSidebarOpen) {
        this.closeAiSidebar();
        e.preventDefault();
        return;
      }
      if (this.drawerHost) {
        this.closeHostDrawer();
        e.preventDefault();
        return;
      }
      if (this.drawerItem) {
        this.drawerItem = null;
        e.preventDefault();
        return;
      }
      if (this.selected.length) {
        this.clearSelection();
        e.preventDefault();
        return;
      }
      if (this.search || this.statusFilter || this.healthFilter) {
        this.clearFilters();
        e.preventDefault();
        return;
      }
      if (el && typeof el.blur === 'function') {
        el.blur();
      }
      return;
    }

    // Drawer arrow-key navigation — Left / Right step through the
    // currently-VISIBLE filtered list, no wrap. Fires BEFORE the
    // body-focused gate so the operator can press arrows while
    // focus lives anywhere inside the drawer (close button, tab
    // strip, etc.) — but ONLY when a drawer is open AND no
    // modifier is held AND not key-repeat (the user-input guards
    // for normal typing don't apply here because no text-input has
    // focus when the drawer's outermost element is focused; Alpine
    // re-targets clicks on the drawer body to the drawer root).
    // Filter-respecting: `filteredHosts()` for the host drawer,
    // `filteredItems` for the service / container drawer. Boundaries
    // stop (no wrap) so the operator gets a clear "end of list"
    // signal when they hit the edge.
    if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight')
      && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey
      && !e.repeat && !e.isComposing) {
      // Single-fire sentinel — diagnostic proved two `[drawer-nav]`
      // events per single ArrowRight keypress (handler invoked
      // twice, advancing drawerItem by 2 instead of 1, producing
      // the visible "skipping every-other entry" pattern). Stamp
      // the event the FIRST time we handle it so a second handler
      // run on the same event bails out cleanly. The sentinel
      // strategy mirrors `_cmdpal_handled` for Cmd-K which had
      // the identical double-fire problem.
      if (e._drawer_nav_handled) {
        return;
      }
      e._drawer_nav_handled = true;
      // Skip if focus is in a real text input — the operator is
      // editing text inside the drawer (e.g. the SSH command
      // textarea, the AI sidebar input, an admin-form field).
      const inText = !!target && (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        (target.getAttribute && (
          target.getAttribute('contenteditable') === 'true' ||
          target.getAttribute('contenteditable') === '' ||
          target.getAttribute('role') === 'textbox'
        )) ||
        (target.closest && target.closest(
          'input, textarea, [contenteditable="true"], [contenteditable=""], [role="textbox"]'
        ))
      );
      if (!inText) {
        if (this.drawerHost) {
          // Visible-order list: flatten `groupedHosts()` in
          // bucket order, then by sub-group, then by ungrouped.
          // The Hosts view renders this exact order, so
          // ArrowLeft / ArrowRight follow the cursor's eye
          // line by line. Pre-fix this used `filteredHosts()`
          // (sorted by status/name/custom_number) which DIDN'T
          // match the bucketed UI — operators saw arrows skip
          // hosts because the linear list jumped across
          // bucket boundaries unpredictably.
          //
          // Collapsed-group filter: hosts inside a collapsed
          // parent group OR a collapsed sub-group are HIDDEN in
          // the markup (the row's `x-show` gate combines both
          // checks). Keyboard nav must skip those too — landing
          // the drawer on an invisible host reads as "skipped"
          // because the operator sees the drawer open with no
          // matching row in view.
          let list = [];
          if (typeof this.groupedHosts === 'function') {
            try {
              for (const bucket of (this.groupedHosts() || [])) {
                const parentName = bucket.group ? bucket.group.name : '';
                const parentCollapsed = this.isGroupCollapsed
                  ? this.isGroupCollapsed(parentName) : false;
                if (parentCollapsed) {
                  continue;
                }
                for (const h of (bucket.hosts || [])) {
                  list.push(h);
                }
                for (const child of (bucket.children || [])) {
                  const childName = child.group ? child.group.name : '';
                  const childCollapsed = this.isGroupCollapsed
                    ? this.isGroupCollapsed(childName) : false;
                  if (childCollapsed) {
                    continue;
                  }
                  for (const h of (child.hosts || [])) {
                    list.push(h);
                  }
                }
              }
            } catch (_) { /* fall through to filtered list */
            }
          }
          // Fallback: the operator might be on a view path that
          // doesn't render groups (no `groupedHosts` available),
          // OR a defensive guard for the edge case where
          // groupedHosts errored. filteredHosts is sort-aware
          // and filter-aware so it's still a sane default.
          if (!list.length) {
            list = (typeof this.filteredHosts === 'function')
              ? this.filteredHosts() : (this.hosts || []);
          }
          // Defensive dedupe by id — same idiom as the service-
          // drawer arrow nav. A host placed by groupedHosts into
          // both a parent bucket and a child sub-group (operator
          // misconfiguration: overlapping ranges) would otherwise
          // cause arrow nav to advance by 2.
          if (list.length) {
            const seenH = new Set();
            const dedupedH = [];
            for (const h of list) {
              const k = h && h.id;
              if (!k || seenH.has(k)) {
                continue;
              }
              seenH.add(k);
              dedupedH.push(h);
            }
            list = dedupedH;
          }
          const idx = list.findIndex(h => h && h.id === this.drawerHost.id);
          if (idx >= 0) {
            const next = e.key === 'ArrowRight' ? idx + 1 : idx - 1;
            if (next >= 0 && next < list.length) {
              e.preventDefault();
              // Swap drawerHost in place. Calling `openHostDrawer`
              // here would also kick off the heavy side-effects
              // (history fetch, ping fetch, providerResumeBusy
              // reset, drawer-history poll setup) that already ran
              // when the operator FIRST opened the drawer. With
              // the singleton-keyed template the DOM is reused on
              // identity change, so the bound `h` updates without
              // a remount. The 30s drawer-history poll covers the
              // chart refresh for the new host.
              this.drawerHost = list[next];
              // Per-host chart fetches via the shared helper —
              // mirrors openHostDrawer. Pre-fix this block only
              // kicked `loadHostHistory`, leaving ping / SNMP host
              // / SNMP iface / SNMP temp caches stale-or-empty for
              // the new host so charts sat on "Collecting data"
              // until the next 30s drawer-poll tick or 60s SSE
              // sample event. The helper bundles every fetch with
              // its empty-cache + stale-cache guards.
              this._kickPerHostChartFetches(list[next]);
            } else {
              // Boundary — stop, no wrap. Eat the keystroke so it
              // doesn't scroll the drawer body or the page.
              e.preventDefault();
            }
            return;
          }
        }
        if (this.drawerItem) {
          // View-aware visible-order list. On Stacks view, items
          // render INSIDE expanded stacks — `filteredStacks` walked
          // in order, items of each EXPANDED stack interleaved.
          // Items inside COLLAPSED stacks are hidden in the markup
          // (`x-show="expanded.includes(stack.name)"`); the
          // keyboard nav must skip those too — landing the drawer
          // on a hidden item reads as "skipped" because the
          // operator sees the drawer open with no matching row in
          // view. On Services / Nodes views the flat
          // `sortedFiltered` list IS the visible order.
          let list = [];
          if (this.view === 'stacks' && Array.isArray(this.filteredStacks)) {
            try {
              for (const stack of this.filteredStacks) {
                if (!stack) {
                  continue;
                }
                if (!(this.expanded || []).includes(stack.name)) {
                  continue;
                }
                for (const it of (stack.items || [])) {
                  list.push(it);
                }
              }
            } catch (_) { /* fall through */
            }
          }
          if (!list.length) {
            list = (this.sortedFiltered && this.sortedFiltered.length)
              ? this.sortedFiltered
              : (this.filteredItems || this.items || []);
          }
          // Defensive dedupe by id — the per-view walks above can
          // double-include an item if a future schema lands the
          // same item across two stacks (multi-network manifest,
          // etc.) or if reconcile races leave a stale duplicate
          // in the array. Keeping the FIRST occurrence preserves
          // visible order; collapsing duplicates means arrow nav
          // never advances by more than one row at a time.
          if (list.length) {
            const seen = new Set();
            const deduped = [];
            for (const it of list) {
              const k = it && it.id;
              if (!k || seen.has(k)) {
                continue;
              }
              seen.add(k);
              deduped.push(it);
            }
            list = deduped;
          }
          const idx = list.findIndex(it => it && it.id === this.drawerItem.id);
          if (idx >= 0) {
            const next = e.key === 'ArrowRight' ? idx + 1 : idx - 1;
            if (next >= 0 && next < list.length) {
              e.preventDefault();
              // In-place swap (not openDrawer) so the drawer DOM
              // doesn't tear down between presses — same pattern
              // as the host-drawer arrow nav.
              this.drawerItem = list[next];
            } else {
              e.preventDefault();
            }
            return;
          }
        }
      }
    }

    // Cmd+K / Ctrl+K palette toggle is owned by the capture-phase
    // listener registered in `init()` (look for `_cmdpal_handled`).
    // Originally handled here in bubble phase too, which caused a
    // double-fire when both listeners ran on the same keydown
    // (operator-reported `was=true` on first press). The sentinel
    // is the canonical authority now; this branch was REMOVED to
    // make the keystroke ownership unambiguous regardless of where
    // focus lives. Capture-phase fires first regardless of focused
    // element, so the toggle works from inputs / drawers / any
    // interactive area.
    // Browser / OS combos — never intercept.
    // Post-migration: every binding REQUIRES Cmd/Ctrl. Bail on Alt
    // (system / accessibility shortcuts) but NOT on Ctrl/Meta — the
    // matcher loop below explicitly checks for them. Pre-fix this
    // bailed on any modifier, which made the entire catalog
    // unreachable; the bare-key matching that followed was the
    // legacy path. The Cmd-K palette already uses the capture-phase
    // listener (`_cmdpal_handled`) and lands here as a no-op.
    if (e.altKey) {
      return;
    }
    // Key repeat (holding a key) or IME composition — user is
    // typing, never a hotkey.
    if (e.repeat || e.isComposing) {
      return;
    }

    // Cmd/Ctrl shortcuts fire from ANYWHERE — including inside
    // inputs and textareas — because the modifier means the
    // operator made the explicit "this is a shortcut" choice. The
    // old body-focused gate was needed when bare keys (`/`, `1`,
    // `r`) could collide with typing; with mandatory Cmd/Ctrl the
    // gate is redundant and harmful (operators editing a search
    // filter would have to click out before pressing Cmd/Ctrl+K
    // / Cmd/Ctrl+1 / etc.). One conscious exception: Cmd/Ctrl+A
    // is INTENTIONALLY NOT BOUND in the catalog so it falls
    // through to the browser's native select-all-text behaviour
    // inside text fields.

    // Map a catalog character key (e.g. '/', '1', 'r') to its
    // physical `e.code` value. `e.code` is keyboard-layout-
    // independent and NOT mangled by Shift — pressing Shift+/
    // produces `e.key === '?'` on US keyboards but `e.code` stays
    // 'Slash' regardless. Pre-2026-05-09 the matcher compared
    // `e.key.toLowerCase()` which silently broke every
    // Cmd/Ctrl+Shift+<symbol> binding (Cmd/Ctrl+Shift+/ for help
    // never matched because '/' !== '?'). This mapper plus an
    // `e.code` comparison fixes the family. Returns null for keys
    // we don't have a code mapping for (caller falls back to
    // `e.key.toLowerCase()`).
    const _hotkeyCharToCode = (ch) => {
      if (!ch) {
        return null;
      }
      if (/^[a-zA-Z]$/.test(ch)) {
        return 'Key' + ch.toUpperCase();
      }
      if (/^[0-9]$/.test(ch)) {
        return 'Digit' + ch;
      }
      const map = {
        '/': 'Slash', '.': 'Period', ',': 'Comma',
        ';': 'Semicolon', "'": 'Quote', '\\': 'Backslash',
        '[': 'BracketLeft', ']': 'BracketRight',
        '-': 'Minus', '=': 'Equal', '`': 'Backquote',
      };
      return map[ch] || null;
    };

    // Walk the catalog once, match on (modifiers + physical key).
    for (const group of this.hotkeyGroups()) {
      for (const entry of group.items) {
        if (!entry.run) {
          continue;
        }
        if (!Array.isArray(entry.keys) || entry.keys.length === 0) {
          continue;
        }
        const last = entry.keys[entry.keys.length - 1];
        if (last === 'Esc') {
          continue;
        }
        // Modifiers: every key in `entry.keys` except the last is
        // a modifier name. `Cmd/Ctrl` matches `e.ctrlKey ||
        // e.metaKey`; `Shift` matches `e.shiftKey`. Strict match —
        // an entry that DOESN'T list Shift requires `e.shiftKey ===
        // false` so Cmd/Ctrl+E doesn't accidentally match
        // Cmd/Ctrl+Shift+E (collapse-all vs expand-all).
        const mods = entry.keys.slice(0, -1);
        const wantsCtrl = mods.includes('Cmd/Ctrl');
        const wantsShift = mods.includes('Shift');
        if (wantsCtrl !== (e.ctrlKey || e.metaKey)) {
          continue;
        }
        if (wantsShift !== e.shiftKey) {
          continue;
        }
        // Prefer e.code (physical key, Shift-independent). Fall
        // back to e.key.toLowerCase() when no code mapping exists
        // (rare — only obscure punctuation).
        const wantCode = _hotkeyCharToCode(last);
        const matched = wantCode
          ? (e.code === wantCode)
          : (String(last).toLowerCase() === String(e.key || '').toLowerCase());
        if (matched) {
          e.preventDefault();
          entry.run();
          return;
        }
      }
    }
  },

  // Generic horizontal button-group arrow-key navigation. For
  // chip-strip patterns (stats range-pickers, refresh-interval
  // picker, host-stats provider chip strip) that use
  // `role="group"` + per-button `aria-pressed` rather than the
  // full radio-group semantics. Bind on the wrapper via
  // `@keydown="_buttonGroupArrowKey($event)"`. ArrowLeft/Right move
  // focus + click (focus-follows-selection); Home / End jump to
  // ends. ArrowUp / Down also wired so up-down keyboards work
  // (common on operator workflows where the chip strip sits in
  // a vertical-flow page). RTL-aware.
  _buttonGroupArrowKey(ev) {
    const key = ev.key;
    if (key !== 'ArrowLeft' && key !== 'ArrowRight'
      && key !== 'ArrowUp' && key !== 'ArrowDown'
      && key !== 'Home' && key !== 'End') {
      return;
    }
    const group = ev.currentTarget;
    const btns = Array.from(group.querySelectorAll('button')).filter(b => !b.disabled);
    if (!btns.length) {
      return;
    }
    ev.preventDefault();
    let isRtl = false;
    try {
      isRtl = group.matches(':dir(rtl)');
    } catch (_e) {
      isRtl = (document.documentElement.dir === 'rtl' || document.body.dir === 'rtl');
    }
    let idx = btns.indexOf(document.activeElement);
    if (idx < 0) {
      idx = btns.findIndex(b => b.getAttribute('aria-pressed') === 'true');
    }
    if (idx < 0) {
      idx = 0;
    }
    let next = idx;
    if (key === 'Home') {
      next = 0;
    } else {
      if (key === 'End') {
        next = btns.length - 1;
      } else {
        let dir = (key === 'ArrowRight' || key === 'ArrowDown') ? 1 : -1;
        if (isRtl && (key === 'ArrowLeft' || key === 'ArrowRight')) {
          dir = -dir;
        }
        next = (idx + dir + btns.length) % btns.length;
      }
    }
    const target = btns[next];
    target.focus();
    target.click();
  },
};
