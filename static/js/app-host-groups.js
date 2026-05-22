/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// noinspection ElementNotExported,JSUnusedGlobalSymbols,CheckTagEmptyBody,HtmlUnknownTag,HtmlExtraClosingTag
// SPA Host Groups admin (Admin → Host Groups) — CRUD + tree
// management for the curated-host grouping taxonomy.
//
// Operators bucket hosts into named groups (e.g. "ISP Routers",
// "Gateways") via `custom_number` ranges. The editor supports 2-level
// nesting (parent → sub-group) with collapse / expand state persisted to
// localStorage. Paginated for fleets with many groups.
//
// Phase 2, Batch 16 of the static/js/app.js modularisation.

export default {
    // Host grouping. Operator-defined ranges over the
    // custom_number field. Loaded in loadSettings; edited inline in
    // Admin → Hosts (below the row editor); persisted via POST
    // /api/settings {host_groups: [...]}. Collapse state is keyed
    // by group name, persisted to localStorage.
    hostGroups: [],
    hostGroupsDirty: false,
    hostGroupsSaving: false,
    hostGroupsCollapsed: (() => {
      try {
        const raw = typeof localStorage !== 'undefined'
          ? localStorage.getItem('hostGroupsCollapsed') : null;
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.filter(x => typeof x === 'string') : [];
      } catch { return []; }
    })(),
    // Editor-side collapse for parent rows in Admin → Host Groups —
    // distinct from `hostGroupsCollapsed` (which is the Hosts-view
    // bucket collapse). When a parent name is in this set, the editor
    // hides its child rows so the page doesn't grow to 50+ cards on
    // deep nesting. Persisted by parent_name so it survives reloads.
    hostGroupsEditorChildrenCollapsed: (() => {
      try {
        const raw = typeof localStorage !== 'undefined'
          ? localStorage.getItem('hostGroupsEditorChildrenCollapsed') : null;
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.filter(x => typeof x === 'string') : [];
      } catch { return []; }
    })(),
    // Parent-self collapse — distinct from the children-collapse set
    // above. When a parent name is in THIS set, the editor hides the
    // parent's full edit form and shows only a one-line summary
    // (number / name / range start–end) so the page stays compact when
    // the operator's editing further down. Children stay independently
    // visible (or hidden via the children set). Persisted separately
    // so the two collapse states don't entangle.
    hostGroupsEditorParentCollapsed: (() => {
      try {
        const raw = typeof localStorage !== 'undefined'
          ? localStorage.getItem('hostGroupsEditorParentCollapsed') : null;
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.filter(x => typeof x === 'string') : [];
      } catch { return []; }
    })(),
    // Pagination state for the Admin → Host groups editor. Mirrors the
    // hostsConfigPage / hostsConfigPerPage pattern so the
    // admin tab can scale past ~50 groups without a 100-card scroll.
    // Persisted to localStorage so a refresh / tab nav lands the
    // operator on the same page they left.
    hostGroupsPage: (() => {
      try {
        const raw = localStorage.getItem('hostGroupsPage');
        const n = parseInt(raw, 10);
        if (Number.isFinite(n) && n >= 1) {
          return n;
        }
      } catch {}
      return 1;
    })(),
    hostGroupsPerPage: (() => {
      try {
        const raw = localStorage.getItem('hostGroupsPerPage');
        const n = parseInt(raw, 10);
        if (Number.isFinite(n) && [10, 25, 50, 100, 200].includes(n)) {
          return n;
        }
      } catch {}
      return 50;
    })(),

    // --- Host groups ---
    // Operator-defined custom_number ranges that bucket hosts into
    // collapsible sections in the Hosts view. Supports 2-level
    // nesting via `parent_name` + a free-text `ip_range`.
    // Append a SUB-GROUP under a specific top-level parent. Same
    // shape as addHostGroup() but pre-fills `parent_name` so the
    // operator doesn't have to navigate the parent dropdown — the
    // Hosts-groups admin tab grew a `+` chip on each top-level
    // group's header that fires this. Range pre-fill walks every
    // EXISTING sub-group of that parent to land at
    // `max(child range_end) + 1`, falling back to the parent's
    // `range_start` so a fresh-no-children parent gets a sensible
    // first sub-group window.
    addHostSubGroup(parentName) {
      const name = (parentName || '').trim();
      if (!name) {
        return;
      }
      const groups = this.hostGroups || [];
      const parent = groups.find(g => !(g && g.parent_name) && (g.name || '').trim() === name);
      if (!parent) {
        return;
      }
      const childEnds = groups
        .filter(g => g && (g.parent_name || '').trim() === name)
        .map(g => parseInt(g.range_end, 10))
        .filter(n => Number.isFinite(n) && n >= 0);
      const startAt = childEnds.length
        ? Math.max(...childEnds) + 1
        : (parseInt(parent.range_start, 10) || 1);
      const SPAN = 4;
      // Cap end at the parent's range_end so the sub-group's range
      // CAN'T spill outside its parent (the save-side validator
      // already enforces this; pre-filling within bounds avoids
      // an immediate inline error after click).
      const parentEnd = parseInt(parent.range_end, 10);
      let endAt = startAt + SPAN - 1;
      if (Number.isFinite(parentEnd)) {
        endAt = Math.min(endAt, parentEnd);
      }
      const next = {
        name: '',
        range_start: startAt,
        range_end:   endAt,
        order: groups.length,
        // parent_name is intentionally empty here — set inside
        // $nextTick below so the <select>'s <option> list (populated
        // by an x-for over `topLevelGroupNames`) finishes rendering
        // BEFORE x-model tries to find a match. Without the defer,
        // browsers showed the dropdown falling back to "— top-level —"
        // even though `g.parent_name` was set in state, because the
        // matching <option value="Switches"> didn't exist yet at the
        // moment the select committed its initial value.
        parent_name: '',
        ip_range: '',
        ssh: { user: '', port: '', password: '', password_set: false, clear_password: false },
      };
      // Capture the index BEFORE the array reassignment so we can
      // reach the new row through `this.hostGroups[newIdx]` later.
      // Reference-based lookup (`find(g => g === next)`) doesn't
      // work: Alpine wraps `this.hostGroups` in a reactive Proxy on
      // assignment, so iterating the array yields proxied entries
      // that compare unequal to the raw `next` literal — find()
      // returns undefined and the deferred parent_name assignment
      // silently no-ops, leaving the dropdown stuck on "— top-level —".
      const newIdx = groups.length;
      this.hostGroups = [...groups, next];
      this.hostGroupsDirty = true;
      // Make sure the parent's children block is EXPANDED so the new
      // sub-group is immediately visible — operators just clicked on
      // that parent's "+", they expect to see the result.
      const collapsed = new Set(this.hostGroupsEditorChildrenCollapsed || []);
      if (collapsed.has(name)) {
        collapsed.delete(name);
        this.hostGroupsEditorChildrenCollapsed = [...collapsed];
        try {
          if (typeof localStorage !== 'undefined') {
            localStorage.setItem(
              'hostGroupsEditorChildrenCollapsed',
              JSON.stringify(this.hostGroupsEditorChildrenCollapsed),
            );
          }
        } catch (_) {}
      }
      // Now that the new row is in the DOM and its <option> elements
      // exist, set parent_name. The select's x-model picks up the
      // new value, the matching <option> is in place, and the
      // dropdown displays "<parentName>" correctly. Mutating through
      // `this.hostGroups[newIdx]` goes via Alpine's Proxy so the
      // reactivity flush triggers the select-binding effect.
      this.$nextTick(() => {
        const list = this.hostGroups || [];
        if (list[newIdx]) {
          list[newIdx].parent_name = name;
        }
        // Then page-jump to the new sub-group's row so it scrolls
        // into view alongside its parent. Same calculation as
        // addHostGroup — done after the parent_name set so the
        // sortedGroupsForEditor() output reflects the updated hierarchy.
        const sorted = this.sortedGroupsForEditor();
        const pos = sorted.findIndex(e => e.origIdx === newIdx);
        if (pos >= 0) {
          const per = this.hostGroupsPerPage || 50;
          this.hostGroupsGoToPage(Math.floor(pos / per) + 1);
        }
        // Scroll the new sub-group card into view + focus its name
        // input. setTimeout(0) waits for Alpine's $nextTick after the
        // page-jump to mount the row's DOM element on the new page;
        // querying immediately would miss the freshly-rendered card.
        setTimeout(() => {
          const card = document.querySelector(`[data-host-group-card="${newIdx}"]`);
          if (card && typeof card.scrollIntoView === 'function') {
            card.scrollIntoView({ behavior: 'smooth', block: 'center' });
            const nameInput = card.querySelector('input[type="text"]');
            if (nameInput && typeof nameInput.focus === 'function') {
              nameInput.focus();
            }
          }
        }, 50);
      });
    },

    addHostGroup() {
      // Smart range pre-fill — look at every existing top-level group's
      // range_end and start the new group at `max(range_end) + 1`.
      // Spans 10 by default (same as before), so a fresh deploy with
      // zero groups still gets 1–10; a deploy with "ISP Routers 1–4",
      // "Gateways 5–8", "Switches 9–16" lands the new group at 17–26
      // rather than colliding at 1–10. Sub-groups don't influence the
      // top-level walk — they live inside a parent's range. Operator
      // is always free to overwrite; this just removes the busywork.
      const tops = (this.hostGroups || []).filter(g => !(g && g.parent_name));
      const ends = tops.map(g => {
        const n = parseInt(g && g.range_end, 10);
        return Number.isFinite(n) && n >= 0 ? n : null;
      }).filter(n => n != null);
      const startAt = ends.length ? Math.max(...ends) + 1 : 1;
      const SPAN = 10;
      const next = {
        name: '',
        range_start: startAt,
        range_end:   startAt + SPAN - 1,
        order: (this.hostGroups || []).length,
        parent_name: '',
        ip_range: '',
        ssh: { user: '', port: '', password: '', password_set: false, clear_password: false },
      };
      this.hostGroups = [...(this.hostGroups || []), next];
      this.hostGroupsDirty = true;
      // Jump to whichever page the new top-level row landed on so
      // the operator's focus follows the click. Mirrors the
      // addHostRow → page-jump pattern in the Hosts editor.
      this.$nextTick(() => {
        const sorted = this.sortedGroupsForEditor();
        const newOrigIdx = (this.hostGroups || []).length - 1;
        const pos = sorted.findIndex(e => e.origIdx === newOrigIdx);
        if (pos >= 0) {
          const per = this.hostGroupsPerPage || 50;
          this.hostGroupsGoToPage(Math.floor(pos / per) + 1);
        }
      });
    },
    // Top-level group names (for the parent <select> options). The
    // current row is excluded — a group cannot be its own parent. A
    // group that's already a sub-group is also excluded since
    // nesting is capped at 2 levels.
    // Render a host-group heading for the Hosts view. Prepends the
    // operator's optional `number` prefix when set so groups read as
    // "32 Smart & IOT Routers" rather than just "Smart & IOT Routers".
    hostGroupHeading(g) {
      if (!g) {
        return '';
      }
      const name = String(g.name || '');
      const num = (g.number != null && +g.number > 0) ? +g.number : null;
      // Format: "<number>. <name>" — dot separator for visual
      // clarity in the Hosts view headings ("32. ISP Routers" reads
      // cleaner than "32 ISP Routers" when the name itself contains
      // numbers).
      return num != null ? `${num}. ${name}` : name;
    },
    hostGroupsTotalPages() {
      const total = this.sortedGroupsForEditor().length;
      const per = this.hostGroupsPerPage || 50;
      return Math.max(1, Math.ceil(total / per));
    },
    hostGroupsGoToPage(n) {
      const tp = this.hostGroupsTotalPages();
      this.hostGroupsPage = Math.min(Math.max(1, parseInt(n, 10) || 1), tp);
    },
    hostGroupsPrevPage() { this.hostGroupsGoToPage(this.hostGroupsPage - 1); },
    hostGroupsNextPage() { this.hostGroupsGoToPage(this.hostGroupsPage + 1); },
    hostGroupsSetPerPage(n) {
      const v = parseInt(n, 10);
      if (!Number.isFinite(v) || v < 1) {
        return;
      }
      this.hostGroupsPerPage = v;
      try { localStorage.setItem('hostGroupsPerPage', String(v)); } catch {}
      this.hostGroupsPage = Math.min(this.hostGroupsPage, this.hostGroupsTotalPages());
    },
    // Number of sub-group rows under a given top-level group name.
    // Used by the editor's collapse toggle so the operator sees
    // "(N children)" before deciding whether to expand.
    hostGroupChildCount(parentName) {
      const name = (parentName || '').trim();
      if (!name) {
        return 0;
      }
      return (this.hostGroups || [])
        .filter(g => (g.parent_name || '').trim() === name)
        .length;
    },
    // Editor-side collapse predicate. Top-level groups whose name is
    // in `hostGroupsEditorChildrenCollapsed` hide their sub-rows.
    isHostGroupChildrenCollapsed(parentName) {
      return (this.hostGroupsEditorChildrenCollapsed || [])
        .includes(parentName || '');
    },
    // Toggle the editor-side collapse for one top-level group +
    // persist to localStorage so it survives reloads. The Hosts
    // VIEW collapse (hostGroupsCollapsed) is intentionally separate
    // — operators may want to keep the sidebar compact without
    // affecting the editor, or vice versa.
    // Bulk toggle: hide / show every top-level group's sub-rows in
    // one click. Useful when the editor has 10+ parents and the
    // operator wants to scan only the top-level rows. Persists to
    // the same localStorage key as the per-parent toggles so the
    // state survives reloads.
    collapseAllHostGroupChildren() {
      const names = (this.hostGroups || [])
        .filter(g => g && !g.parent_name && (g.name || '').trim()
                     && this.hostGroupChildCount(g.name) > 0)
        .map(g => g.name);
      this.hostGroupsEditorChildrenCollapsed = [...new Set(names)];
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(
            'hostGroupsEditorChildrenCollapsed',
            JSON.stringify(this.hostGroupsEditorChildrenCollapsed),
          );
        }
      } catch (_) {}
    },
    expandAllHostGroupChildren() {
      // Capture which parents had their children hidden BEFORE
      // wiping the set — those rows are about to re-enter the DOM
      // and need the same select-mount-race fix as
      // toggleHostGroupChildrenCollapsed.
      const wereCollapsed = new Set(this.hostGroupsEditorChildrenCollapsed || []);
      this.hostGroupsEditorChildrenCollapsed = [];
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(
            'hostGroupsEditorChildrenCollapsed',
            JSON.stringify(this.hostGroupsEditorChildrenCollapsed),
          );
        }
      } catch (_) {}
      // Re-touch every now-visible sub-group's parent_name with the
      // empty→set dance (same as toggleHostGroupChildrenCollapsed).
      // Self-assignment is elided by Alpine; we must clear the value
      // first, let Alpine render with the empty placeholder, then in
      // a double-nextTick reassign the real value so x-model rebinds
      // after the <option> x-for has finished inserting its children.
      if (wereCollapsed.size === 0) {
        return;
      }
      const groups = this.hostGroups || [];
      const restore = [];
      for (let i = 0; i < groups.length; i++) {
        const g = groups[i];
        const parent = (g && g.parent_name || '').trim();
        if (parent && wereCollapsed.has(parent)) {
          restore.push({ idx: i, value: g.parent_name });
          g.parent_name = '';
        }
      }
      if (restore.length === 0) {
        return;
      }
      this.$nextTick(() => {
        this.$nextTick(() => {
          const list = this.hostGroups || [];
          for (const { idx, value } of restore) {
            if (list[idx]) {
              list[idx].parent_name = value;
            }
          }
        });
      });
    },
    // Parent-self collapse predicate. Top-level groups whose name is
    // in `hostGroupsEditorParentCollapsed` hide their full edit form;
    // only the compact summary header (number / name / range start-end)
    // remains.
    isHostGroupParentCollapsed(parentName) {
      return (this.hostGroupsEditorParentCollapsed || [])
        .includes(parentName || '');
    },
    // Bulk collapse: hide every top-level group's edit form in one
    // click. Useful when the editor has 10+ parents and the operator
    // wants to scan only the summary headers. Persists to the same
    // localStorage key as the per-parent toggle so the state survives
    // reloads. Mirrors `collapseAllHostGroupChildren` for the children
    // axis.
    collapseAllHostGroupParents() {
      const names = (this.hostGroups || [])
        .filter(g => g && !g.parent_name && (g.name || '').trim())
        .map(g => g.name);
      this.hostGroupsEditorParentCollapsed = [...new Set(names)];
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(
            'hostGroupsEditorParentCollapsed',
            JSON.stringify(this.hostGroupsEditorParentCollapsed),
          );
        }
      } catch (_) {}
    },
    // Bulk expand: clear the parent-collapse set so every top-level
    // group's edit form re-renders. Mirror of
    // `expandAllHostGroupChildren`.
    expandAllHostGroupParents() {
      this.hostGroupsEditorParentCollapsed = [];
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(
            'hostGroupsEditorParentCollapsed',
            JSON.stringify(this.hostGroupsEditorParentCollapsed),
          );
        }
      } catch (_) {}
    },
    // Toggle parent-self collapse for one top-level group. Independent
    // from the children-collapse set so an operator can hide the parent
    // form while keeping the children visible (or vice versa) when
    // editing nested rows further down the page.
    toggleHostGroupParentCollapsed(parentName) {
      const name = (parentName || '').trim();
      if (!name) {
        return;
      }
      const set = new Set(this.hostGroupsEditorParentCollapsed || []);
      if (set.has(name)) {
        set.delete(name);
      } else {
        set.add(name);
      }
      this.hostGroupsEditorParentCollapsed = [...set];
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(
            'hostGroupsEditorParentCollapsed',
            JSON.stringify(this.hostGroupsEditorParentCollapsed),
          );
        }
      } catch (_) {}
    },
    // Compact one-line summary used in the collapsed parent header:
    // "<number>. <name> · <range_start>-<range_end>". Number prefix
    // and range tail are both optional; missing parts collapse cleanly
    // (e.g. just "<name>" if number + range are blank).
    hostGroupCollapsedSummary(g) {
      if (!g) {
        return '';
      }
      const parts = [];
      const num = g.number;
      const name = (g.name || '').trim();
      if (Number.isFinite(num) && num > 0) {
        parts.push(num + '. ' + name);
      } else {
        parts.push(name);
      }
      const rs = g.range_start;
      const re = g.range_end;
      if (Number.isFinite(rs) && Number.isFinite(re)) {
        parts.push(rs + '-' + re);
      } else if (Number.isFinite(rs)) {
        parts.push(rs + '–');
      } else if (Number.isFinite(re)) {
        parts.push('–' + re);
      }
      return parts.filter(Boolean).join(' · ');
    },
    toggleHostGroupChildrenCollapsed(parentName) {
      const name = (parentName || '').trim();
      if (!name) {
        return;
      }
      const set = new Set(this.hostGroupsEditorChildrenCollapsed || []);
      const wasCollapsed = set.has(name);
      if (wasCollapsed) {
        set.delete(name);
      } else {
        set.add(name);
      }
      this.hostGroupsEditorChildrenCollapsed = [...set];
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(
            'hostGroupsEditorChildrenCollapsed',
            JSON.stringify(this.hostGroupsEditorChildrenCollapsed),
          );
        }
      } catch (_) {}
      // Same Alpine select-mount race as when sub-group rows
      // re-enter the DOM after un-collapsing, each row's <select>
      // mounts BEFORE the inner x-for finishes rendering its
      // <option> elements (populated from `topLevelGroupNames`).
      // Without intervention the select silently falls back to the
      // empty "— top-level —" placeholder even though `g.parent_name`
      // is set in state. Fix: clear parent_name to '' synchronously
      // (forces Alpine to render the empty placeholder), then in a
      // double-nextTick (after Alpine flushes the new DOM AND the
      // <option> x-for finishes inserting its children) reassign
      // the original value. The empty→set dance is what makes the
      // x-model effect fire on a TRUE value change and rebind the
      // select. Self-assignment doesn't work because Alpine elides
      // identical-value writes.
      if (!wasCollapsed) {
        return;
      }  // we just collapsed — no children visible to fix
      const groups = this.hostGroups || [];
      const restore = [];
      for (let i = 0; i < groups.length; i++) {
        const g = groups[i];
        const parent = (g && g.parent_name || '').trim();
        if (parent && parent === name) {
          restore.push({ idx: i, value: g.parent_name });
          g.parent_name = '';
        }
      }
      if (restore.length === 0) {
        return;
      }
      // First $nextTick: Alpine has rendered each sub-group's row
      // with parent_name='' so the <select> mounts uncontroversially
      // on the empty option. Second $nextTick: the inner <option>
      // x-for has now finished, so reassigning the real parent_name
      // lets x-model find the matching <option value="...">.
      this.$nextTick(() => {
        this.$nextTick(() => {
          const list = this.hostGroups || [];
          for (const { idx, value } of restore) {
            if (list[idx]) {
              list[idx].parent_name = value;
            }
          }
        });
      });
    },
    // Move a row up/down in the VISIBLE order. Translates to a raw-
    // array swap so sub-groups stick next to their parent after the
    // sort re-runs. Clamped to same bucket: we don't allow moving a
    // sub-group above its parent or past a sibling's range boundary
    // via move buttons alone — that'd break the containment rule
    // silently. Move across buckets via re-parenting (the dropdown).
    moveHostGroupByListIdx(listIdx, dir) {
      const sorted = this.sortedGroupsForEditor();
      const j = listIdx + dir;
      if (j < 0 || j >= sorted.length) {
        return;
      }
      const src = sorted[listIdx];
      const dst = sorted[j];
      // Refuse cross-bucket moves: a top-level row can't swap with a
      // sub-group and vice-versa, because the sort would immediately
      // undo it. Silent no-op keeps the button harmless.
      const srcParent = src.g.parent_name || src.g.name;
      const dstParent = dst.g.parent_name || dst.g.name;
      if (srcParent !== dstParent) {
        return;
      }
      const arr = [...this.hostGroups];
      [arr[src.origIdx], arr[dst.origIdx]] = [arr[dst.origIdx], arr[src.origIdx]];
      arr.forEach((g, i) => { g.order = i; });
      this.hostGroups = arr;
      this.hostGroupsDirty = true;
    },
    removeHostGroup(idx) {
      this.hostGroups = (this.hostGroups || []).filter((_, i) => i !== idx);
      this.hostGroupsDirty = true;
    },
    moveHostGroup(idx, dir) {
      const arr = [...(this.hostGroups || [])];
      const j = idx + dir;
      if (j < 0 || j >= arr.length) {
        return;
      }
      [arr[idx], arr[j]] = [arr[j], arr[idx]];
      // Renumber `order` to match new array positions so the server
      // round-trips the change on next load.
      arr.forEach((g, i) => { g.order = i; });
      this.hostGroups = arr;
      this.hostGroupsDirty = true;
    },
    markHostGroupDirty() { this.hostGroupsDirty = true; },

    async saveHostGroups() {
      // Clear any inline errors from a previous attempt before
      // re-validating.
      this.clearFieldErrorsByPrefix('group_');

      const clean = [];
      const indexMap = []; // clean[j] came from hostGroups[indexMap[j]]
      let hadError = false;
      (this.hostGroups || []).forEach((g, gi) => {
        const name = String(g.name || '').trim();
        if (!name) {
          // Silently skip empty-name rows — matches legacy behaviour.
          return;
        }
        const rs = parseInt(g.range_start, 10);
        const re_ = parseInt(g.range_end, 10);
        if (!Number.isFinite(rs) || !Number.isFinite(re_)) {
          this.setFieldError('group_' + gi + '_range',
            this.t('admin_hosts.groups.range_required'));
          hadError = true;
          return;
        }
        if (rs < 0 || re_ < rs) {
          this.setFieldError('group_' + gi + '_range',
            this.t('admin_hosts.groups.invalid_range', { name }));
          hadError = true;
          return;
        }
        const parent_name = String(g.parent_name || '').trim();
        const ip_range = String(g.ip_range || '').trim();
        // SSH block — only send fields the operator actually touched.
        // `password` is keep-current-if-blank (matches the global
        // secret store contract); `clear_password: true` is the
        // explicit-clear escape hatch.
        const sshIn = (g && g.ssh && typeof g.ssh === 'object') ? g.ssh : {};
        const ssh = {};
        const sUser = String(sshIn.user || '').trim();
        if (sUser) {
          ssh.user = sUser;
        }
        const sPort = sshIn.port;
        if (sPort != null && sPort !== '') {
          const pi = parseInt(sPort, 10);
          if (Number.isFinite(pi) && pi >= 1 && pi <= 65535) {
            ssh.port = pi;
          }
        }
        const sPw = String(sshIn.password || '').trim();
        if (sPw) {
          ssh.password = sPw;
        }
        if (sshIn.clear_password) {
          ssh.clear_password = true;
        }
        // Optional display-prefix number. Validated as a positive
        // integer when set; uniqueness check fires later (after every
        // row is parsed so we can name the conflicting group).
        let numberVal = null;
        const numberRaw = g.number;
        if (numberRaw !== '' && numberRaw != null) {
          const n = parseInt(numberRaw, 10);
          if (!Number.isFinite(n) || n <= 0) {
            this.setFieldError('group_' + gi + '_number',
              this.t('admin_hosts.groups.invalid_number') || 'Number must be a positive integer.');
            hadError = true;
            return;
          }
          numberVal = n;
        }
        clean.push({
          // Round-trip the stable id (server mints it on first save;
          // null/blank for fresh rows so the backend assigns one).
          // Without this, a rename would lose the password keep-
          // current carryover. See fix.
          id: String(g.id || ''),
          name, range_start: rs, range_end: re_,
          order: Number.isFinite(+g.order) ? +g.order : clean.length,
          parent_name: parent_name || null,
          ip_range,
          number: numberVal,
          ssh,
        });
        indexMap.push(gi);
      });
      if (hadError) {
        this.focusFirstFieldError();
        return;
      }

      // Number uniqueness — when set, no two groups may share the
      // same prefix. Reports both names so the operator knows where
      // the conflict is without scrolling.
      const seenNumbers = new Map();
      for (let j = 0; j < clean.length; j++) {
        const g = clean[j];
        if (g.number == null) {
          continue;
        }
        const prior = seenNumbers.get(g.number);
        if (prior !== undefined) {
          const gi = indexMap[j];
          this.setFieldError('group_' + gi + '_number',
            this.t('admin_hosts.groups.err_number_dupe', {
              other: prior.name,
            }) || `Number ${g.number} already used by "${prior.name}".`);
          hadError = true;
          continue;
        }
        seenNumbers.set(g.number, { name: g.name, idx: indexMap[j] });
      }
      if (hadError) { this.focusFirstFieldError(); return; }

      // Parent existence + self-parent + depth-1 checks.
      const byName = new Map();
      clean.forEach(g => byName.set(g.name, g));
      for (let j = 0; j < clean.length; j++) {
        const g = clean[j];
        const gi = indexMap[j];
        if (!g.parent_name) {
          continue;
        }
        if (g.parent_name === g.name) {
          this.setFieldError('group_' + gi + '_parent',
            this.t('admin_hosts.groups.err_self_parent'));
          hadError = true;
          continue;
        }
        const p = byName.get(g.parent_name);
        if (!p) {
          this.setFieldError('group_' + gi + '_parent',
            this.t('admin_hosts.groups.err_parent_missing', { name: g.parent_name }));
          hadError = true;
          continue;
        }
        if (p.parent_name) {
          this.setFieldError('group_' + gi + '_parent',
            this.t('admin_hosts.groups.err_parent_is_sub', { name: g.parent_name }));
          hadError = true;
          continue;
        }
        // Containment: sub-group range must be inside parent range.
        if (!(p.range_start <= g.range_start && g.range_end <= p.range_end)) {
          this.setFieldError('group_' + gi + '_range',
            this.t('admin_hosts.groups.err_not_contained', {
              parent: p.name,
              ps: p.range_start, pe: p.range_end,
            }));
          hadError = true;
        }
      }
      if (hadError) { this.focusFirstFieldError(); return; }

      // Overlap: every pair that is NOT parent-child must be disjoint.
      for (let i = 0; i < clean.length; i++) {
        for (let j = i + 1; j < clean.length; j++) {
          const a = clean[i], b = clean[j];
          const pc = (a.parent_name === b.name) || (b.parent_name === a.name);
          if (pc) {
            continue;
          }
          if (a.range_start <= b.range_end && b.range_start <= a.range_end) {
            const firstIdx = indexMap[i];
            this.setFieldError('group_' + firstIdx + '_range',
              this.t('admin_hosts.groups.err_overlap', {
                other: b.name, os: b.range_start, oe: b.range_end,
              }));
            hadError = true;
          }
        }
      }
      if (hadError) { this.focusFirstFieldError(); return; }

      this.hostGroupsSaving = true;
      try {
        const r = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ host_groups: clean }),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${r.status}`);
        }
        // Reload so the server's cleaned / sorted view replaces ours.
        await this.loadSettings();
        // Bust the groupedHosts() memo. loadSettings
        // changes hostGroups in place; bumping the revision counter
        // forces the next access to recompute even if the array
        // identity / length didn't change.
        this.hostGroupsRevision = (this.hostGroupsRevision || 0) + 1;
        this.showToast(this.t('admin_hosts.groups.saved'), 'success');
      } catch (e) {
        this.showToast(this.t('admin_hosts.groups.save_failed') + ': ' + e.message, 'error');
      } finally {
        this.hostGroupsSaving = false;
      }
    },
    hostGroupsRevision: 0,
};
