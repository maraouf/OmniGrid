// noinspection ALL
// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,AnonymousFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS,RedundantLocalVariableJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,JSAsyncFunctionMissingAwait,JSMissingAwait
// noinspection NegatedConditionalExpressionJS,JSNegatedConditionalExpression,OverlyComplexBooleanExpressionJS,AnonymousCapturingGroupJS,JSVariableNamingConventionJS,LocalVariableNamingConventionJS,BadName,BadVariableName
// noinspection HtmlUnknownTag
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Cmd-K command palette — keyboard-first launcher (modal palette).

export default {
  // Command palette state (Cmd-K / Ctrl-K). Drops the operator into
  // any drawer / view / setting from anywhere. Toggle from the
  // hotkey handler; markup at the bottom of static/index.html.
  commandPaletteOpen: false,
  commandPaletteQuery: '',
  commandPaletteSelectedIdx: 0,
  // Bulk palette mode — entered via verb-prefix queries like
  // `pause: web*` or `resume: provider:beszel`. When active the
  // palette renders a chip strip of matched hosts + a single "Run
  // on N hosts" action row instead of the regular results list.
  // `bulkExcluded` is a Set of host_ids the operator has clicked
  // off the chip strip (excluded from the bulk run).
  commandPaletteBulkExcluded: new Set(),
  // Static action catalog — verb-first commands that the operator
  // can invoke directly from the palette without navigating to a
  // tab. Each entry is a { id, label, verbs, run, destructive? }
  // descriptor; `verbs` boosts the score when the query starts with
  // any of them so "restart" / "pause" / "update" / "test" / "theme"
  // surface actions ahead of navigation results. `destructive: true`
  // wraps the run() call in a SweetAlert confirm. Pattern mirrors
  // Linear / Raycast / Notion command palette behaviour: the
  // palette becomes the operator's primary CLI for routine ops
  // (~80% of admin-tab nav can be replaced).
  _commandActions() {
    const t = (k, fb) => this.t(k) || fb;
    // Build conditionally so disabled / unavailable actions don't
    // pollute the result set. Each entry is appended only when its
    // gate passes. `verbs` is a small ordered list of the words an
    // operator types to look for THIS action (prefixes against the
    // query — e.g. typing "ref" matches verbs "refresh" / "reload"
    // → both surface).
    const actions = [
      // Refresh / data
      {
        id: 'refresh-now',
        label: t('command_palette.action.refresh_now', 'Trigger gather refresh now'),
        sub: t('command_palette.action.refresh_now_sub', 'Force /api/items refetch + image-digest probe'),
        verbs: ['refresh', 'gather', 'sync'],
        run: () => {
          this.refresh(true);
        }
      },
      {
        id: 'reload-spa',
        label: t('command_palette.action.reload_spa', 'Reload SPA'),
        sub: t('command_palette.action.reload_spa_sub', 'Hard-reload the page'),
        verbs: ['reload'],
        run: () => {
          location.reload();
        }
      },

      // Re-test connections (only when the helper exists)
      ...(typeof this.testPortainerConnection === 'function' ? [{
        id: 'test-portainer',
        label: t('command_palette.action.test_portainer', 'Force re-test Portainer'),
        verbs: ['test', 'portainer'],
        run: () => {
          this.testPortainerConnection();
          this.view = 'admin';
          this.setAdminTab && this.setAdminTab('portainer');
        }
      }] : []),
      ...(typeof this.testOidcConnection === 'function' ? [{
        id: 'test-oidc',
        label: t('command_palette.action.test_oidc', 'Force re-test Authentik OIDC'),
        verbs: ['test', 'oidc', 'authentik'],
        run: () => {
          this.testOidcConnection();
          this.view = 'admin';
          this.setAdminTab && this.setAdminTab('oidc');
        }
      }] : []),
      ...(typeof this.testBeszelConnection === 'function' ? [{
        id: 'test-beszel',
        label: t('command_palette.action.test_beszel', 'Force re-test Beszel hub'),
        verbs: ['test', 'beszel'],
        run: () => {
          this.testBeszelConnection();
          this.view = 'admin';
          this.setAdminTab && this.setAdminTab('providers');
        }
      }] : []),
      ...(typeof this.testPulseConnection === 'function' ? [{
        id: 'test-pulse',
        label: t('command_palette.action.test_pulse', 'Force re-test Pulse'),
        verbs: ['test', 'pulse'],
        run: () => {
          this.testPulseConnection();
          this.view = 'admin';
          this.setAdminTab && this.setAdminTab('providers');
        }
      }] : []),
      ...(typeof this.testWebminConnection === 'function' ? [{
        id: 'test-webmin',
        label: t('command_palette.action.test_webmin', 'Force re-test Webmin'),
        verbs: ['test', 'webmin'],
        run: () => {
          this.testWebminConnection();
          this.view = 'admin';
          this.setAdminTab && this.setAdminTab('providers');
        }
      }] : []),
      ...(typeof this.testSnmpConnection === 'function' ? [{
        id: 'test-snmp',
        label: t('command_palette.action.test_snmp', 'Force re-test SNMP'),
        verbs: ['test', 'snmp', 'check', 'snmpwalk'],
        run: () => {
          this.testSnmpConnection();
          this.view = 'admin';
          this.setAdminTab && this.setAdminTab('providers');
        }
      }] : []),
      ...(typeof this.testPingConnection === 'function' ? [{
        id: 'test-ping',
        label: t('command_palette.action.test_ping', 'Force re-test Ping'),
        verbs: ['test', 'ping', 'reachability', 'icmp'],
        run: () => {
          this.testPingConnection();
          this.view = 'admin';
          this.setAdminTab && this.setAdminTab('providers');
        }
      }] : []),
      ...(typeof this.testAssetInventoryConnection === 'function' ? [{
        id: 'test-asset-inventory',
        label: t('command_palette.action.test_asset_inventory', 'Force re-test asset inventory'),
        verbs: ['test', 'asset', 'assets', 'inventory'],
        run: () => {
          this.testAssetInventoryConnection();
          this.view = 'admin';
          this.setAdminTab && this.setAdminTab('assets');
        }
      }] : []),
      ...(typeof this.testApprise === 'function' ? [{
        id: 'test-apprise',
        label: t('command_palette.action.test_apprise', 'Send a test notification'),
        verbs: ['test', 'apprise', 'notification', 'notify'],
        run: () => {
          this.testApprise();
          this.view = 'admin';
          this.setAdminTab && this.setAdminTab('notifications');
        }
      }] : []),

      // Theme
      {
        id: 'theme-dark',
        label: t('command_palette.action.theme_dark', 'Switch theme to dark'),
        verbs: ['theme', 'dark'],
        run: () => {
          this.themePref = 'dark';
          try {
            localStorage.setItem('theme', 'dark');
          } catch (_) {
          }
          this.applyTheme();
          this.persistThemePref && this.persistThemePref('dark');
        }
      },
      {
        id: 'theme-light',
        label: t('command_palette.action.theme_light', 'Switch theme to light'),
        verbs: ['theme', 'light'],
        run: () => {
          this.themePref = 'light';
          try {
            localStorage.setItem('theme', 'light');
          } catch (_) {
          }
          this.applyTheme();
          this.persistThemePref && this.persistThemePref('light');
        }
      },
      {
        id: 'theme-auto',
        label: t('command_palette.action.theme_auto', 'Switch theme to auto (system)'),
        verbs: ['theme', 'auto', 'system'],
        run: () => {
          this.themePref = 'auto';
          try {
            localStorage.setItem('theme', 'auto');
          } catch (_) {
          }
          this.applyTheme();
          this.persistThemePref && this.persistThemePref('auto');
        }
      },

      // Modals / panels
      {
        id: 'show-hotkeys',
        label: t('command_palette.action.show_hotkeys', 'Show keyboard shortcuts'),
        verbs: ['hotkeys', 'shortcuts', 'help'],
        run: () => {
          this.showHotkeys = true;
        }
      },
      ...(typeof this.toggleNotificationsPanel === 'function' ? [{
        id: 'open-notifications',
        label: t('command_palette.action.open_notifications', 'Open notifications panel'),
        verbs: ['notifications', 'inbox', 'alerts'],
        run: () => {
          this.toggleNotificationsPanel();
        }
      }] : []),
      ...(typeof this.markAllNotificationsRead === 'function' ? [{
        id: 'mark-all-notifications-read',
        label: t('command_palette.action.mark_all_notifications_read', 'Mark all notifications as read'),
        verbs: ['notifications', 'mark', 'read', 'clear', 'inbox'],
        run: () => {
          this.markAllNotificationsRead();
        }
      }] : []),

      // Cleanup — bulk-remove every stopped / failed / orphaned
      // container the SPA can see (powered by the topbar Cleanup
      // button + the existing `bulkRemoveAll()` flow). Destructive:
      // the underlying handler issues docker rm + volume remove on
      // each picked item, so it routes through the SweetAlert
      // confirm path. Verbs cover the natural-language ways an
      // operator might phrase this — "cleanup" / "purge" / "prune"
      // / "remove stopped" — and the AI prompt's structured-action
      // protocol uses the snake_case `cleanup_stopped` id below.
      ...(typeof this.bulkRemoveAll === 'function' ? [{
        id: 'cleanup-stopped',
        label: t('command_palette.action.cleanup_stopped', 'Cleanup — remove every stopped / failed container'),
        sub: t('command_palette.action.cleanup_stopped_sub', 'Same as the topbar Cleanup button'),
        verbs: ['cleanup', 'clean', 'purge', 'prune', 'remove', 'delete'],
        destructive: true,
        // `bulkRemoveAll()` ALREADY shows its own SweetAlert listing
        // every container by name (first 8 + `+N more` overflow), so
        // the generic destructive-confirm dialog would produce a
        // double-popup. Defer to the inner confirm — the operator
        // sees ONE rich popup with the real container list, not a
        // generic "you'll get one more confirm" wrapper.
        defer_confirm_to_run: true,
        run: (opts) => {
          this.bulkRemoveAll(opts);
        }
      }] : []),

      // Update everything updatable — pull updates for every stack /
      // standalone container with an available update. Same shape as
      // cleanup-stopped: `bulkUpdateAll()` already shows a rich
      // SweetAlert listing every affected stack/container, so defer
      // the confirm to the inner dialog rather than double-prompting.
      ...(typeof this.bulkUpdateAll === 'function' ? [{
        id: 'update-all-updatable',
        label: t('command_palette.action.update_all_updatable', 'Update everything updatable'),
        sub: t('command_palette.action.update_all_updatable_sub', 'Pull updates for every stack and standalone container with an update available'),
        verbs: ['update', 'updates', 'upgrade', 'pull', 'apply', 'deploy', 'stacks', 'all'],
        destructive: true,
        defer_confirm_to_run: true,
        run: (opts) => {
          this.bulkUpdateAll(opts);
        }
      }] : []),

      // Port scan — fires the on-demand TCP-connect scanner against
      // the host whose drawer is currently open. Only surfaces when
      // Gates on master toggle ON; the host drawer is no longer
      // a hard prerequisite. `runPortScan(null)` resolves the
      // target via a fallback chain: explicit arg → drawerHost →
      // most-recent AI assistant turn's `host_ids[0]` → toast
      // asking the operator to specify. So the AI palette can
      // fire `ACTION: scan_ports` paired with a `HOSTS: <id>`
      // line and the SPA hits the right host without forcing
      // the operator to navigate to the drawer first.
      ...(((this.settings || {}).port_scan_enabled && typeof this.runPortScan === 'function') ? [{
        id: 'scan-ports',
        label: t('command_palette.action.scan_ports', 'Scan ports on this host'),
        sub: t('command_palette.action.scan_ports_sub', 'On-demand TCP-connect scan against the open host drawer'),
        verbs: ['scan', 'ports', 'tcp', 'discover', 'nmap'],
        run: () => {
          this.runPortScan(null);
        }
      }] : []),

      // Apps discovery wizard — the one Apps write-flow surfaced to the
      // palette. Admin-only (the discovery endpoint + wizard are). `run`
      // navigates to Admin → Apps and opens the wizard for operator
      // review; the actual pin happens there. Per-instance edit / unpin,
      // per-template pin, and catalog CRUD are deliberately NOT palette
      // actions — they need precise instance/template targeting the
      // one-shot palette can't resolve and live in the Admin → Apps
      // editor with their own confirm flows.
      ...((typeof this.isAdmin === 'function' && this.isAdmin()
        && typeof this.openDiscoveryFromAppsView === 'function') ? [{
        id: 'discover-apps',
        label: t('command_palette.action.discover_apps', 'Discover apps on a host'),
        sub: t('command_palette.action.discover_apps_sub', 'Open the Apps discovery wizard to match a host’s open ports against catalog templates'),
        verbs: ['discover', 'apps', 'find', 'services', 'catalog'],
        run: () => {
          this.openDiscoveryFromAppsView();
        }
      }] : []),

      // Switch image tag — AI-dispatchable wrapper around the same
      // `submitRetagPopover` flow the drawer's inline popover uses.
      // `defer_confirm_to_run: true` means the runner itself decides
      // whether to confirm; the inline-confirm chip path in the AI
      // sidebar handles the destructive-confirm UX (no SwAl popup).
      // `run(opts)` receives `{ skipConfirm, tag, item }` from the
      // dispatcher — `tag` comes from the AI's `ACTION_TAG: <tag>`
      // directive (parsed in main.py and forwarded as
      // `payload.action_tag`); `item` resolves from `ACTION_ITEM:`
      // first, then the open item drawer. Empty tag falls back to
      // "latest" via the same backend validator the popover uses.
      ...(typeof this.submitRetagPopover === 'function' ? [{
        id: 'retag-image',
        label: t('command_palette.action.retag_image', 'Switch image tag'),
        sub: t('command_palette.action.retag_image_sub', 'Switch this container/stack to a different floating tag (e.g. :latest, :2)'),
        verbs: ['retag', 'switch', 'tag', 'pin', 'track'],
        destructive: true,
        // Defer the destructive-confirm gate to the dispatcher's
        // inline-chip path (sidebar) — the runner skips its own
        // popover when called with skipConfirm=true.
        defer_confirm_to_run: true,
        run: (opts) => {
          this._aiRetagDispatch(opts || {});
        },
      }] : []),

      // Schedule CRUD actions — operator says "create a daily backup
      // schedule at 1am" / "delete the experimental prune schedule"
      // and the AI emits one of these with `ACTION_DATA: {...}`
      // carrying the payload. Create + update are non-destructive
      // (operators can always edit / delete after the fact); delete
      // IS destructive (typed-confirm chip in the sidebar). All
      // three reuse the existing /api/schedules CRUD endpoints —
      // no new backend surface; backend bounds-clamping +
      // skip-if-running gates apply uniformly.
      {
        id: 'schedule-create',
        label: t('command_palette.action.schedule_create', 'Create schedule'),
        sub: t('command_palette.action.schedule_create_sub', 'Add a new recurring scheduled job'),
        verbs: ['schedule', 'create', 'add', 'new', 'cron', 'recurring'],
        destructive: false,
        run: (opts) => {
          this._aiScheduleDispatch('create', opts || {});
        },
      },
      {
        id: 'schedule-update',
        label: t('command_palette.action.schedule_update', 'Update schedule'),
        sub: t('command_palette.action.schedule_update_sub', 'Modify an existing scheduled job'),
        verbs: ['schedule', 'update', 'modify', 'change', 'edit'],
        destructive: false,
        run: (opts) => {
          this._aiScheduleDispatch('update', opts || {});
        },
      },
      {
        id: 'schedule-delete',
        label: t('command_palette.action.schedule_delete', 'Delete schedule'),
        sub: t('command_palette.action.schedule_delete_sub', 'Remove a scheduled job permanently'),
        verbs: ['schedule', 'delete', 'remove'],
        destructive: true,
        // Defer the destructive-confirm gate to the dispatcher's
        // inline-chip path (sidebar) — same shape as retag-image.
        defer_confirm_to_run: true,
        run: (opts) => {
          this._aiScheduleDispatch('delete', opts || {});
        },
      },

      // Item write-ops via AI palette — operator says "restart traefik"
      // / "update the auth stack" / "remove that orphan container".
      // Each is destructive; `defer_confirm_to_run: true` means the
      // sidebar inline-confirm chip handles approval and the helper
      // bypasses its inner SwAl when called with skipConfirm=true.
      // `_aiItemDispatch` resolves the target from ACTION_ITEM or
      // the open drawer; toast asks the operator if neither resolves.
      {
        id: 'update-stack',
        label: t('command_palette.action.update_stack', 'Update stack'),
        sub: t('command_palette.action.update_stack_sub', 'Pull updates for the named stack and redeploy'),
        verbs: ['update', 'pull', 'upgrade', 'stack', 'redeploy'],
        destructive: true,
        defer_confirm_to_run: true,
        run: (opts) => {
          this._aiItemDispatch('update_stack', opts || {});
        },
      },
      {
        id: 'update-container',
        label: t('command_palette.action.update_container', 'Update container'),
        sub: t('command_palette.action.update_container_sub', 'Recreate the named container with the latest image'),
        verbs: ['update', 'recreate', 'pull', 'container'],
        destructive: true,
        defer_confirm_to_run: true,
        run: (opts) => {
          this._aiItemDispatch('update_container', opts || {});
        },
      },
      {
        id: 'restart-service',
        label: t('command_palette.action.restart_service', 'Restart service'),
        sub: t('command_palette.action.restart_service_sub', 'Force-update the named Swarm service (no image pull)'),
        verbs: ['restart', 'service', 'bounce'],
        destructive: true,
        defer_confirm_to_run: true,
        run: (opts) => {
          this._aiItemDispatch('restart_service', opts || {});
        },
      },
      {
        id: 'restart-container',
        label: t('command_palette.action.restart_container', 'Restart container'),
        sub: t('command_palette.action.restart_container_sub', 'Restart the named standalone container'),
        verbs: ['restart', 'container', 'bounce'],
        destructive: true,
        defer_confirm_to_run: true,
        run: (opts) => {
          this._aiItemDispatch('restart_container', opts || {});
        },
      },
      {
        id: 'remove-container',
        label: t('command_palette.action.remove_container', 'Remove container'),
        sub: t('command_palette.action.remove_container_sub', 'Delete the named stopped / orphan container'),
        verbs: ['remove', 'delete', 'rm', 'container'],
        destructive: true,
        defer_confirm_to_run: true,
        run: (opts) => {
          this._aiItemDispatch('remove_container', opts || {});
        },
      },

      // prune_node — Docker system prune on a specific node.
      // ACTION_ITEM carries the hostname. Destructive.
      ...(typeof this.pruneNode === 'function' ? [{
        id: 'prune-node',
        label: t('command_palette.action.prune_node', 'Prune node'),
        sub: t('command_palette.action.prune_node_sub', 'docker system prune --volumes on the named node'),
        verbs: ['prune', 'node', 'cleanup', 'docker'],
        destructive: true,
        defer_confirm_to_run: true,
        run: (opts) => {
          this._aiHostDispatch('prune_node', opts || {});
        },
      }] : []),

      // Bulk host pause / resume via AI palette. The Cmd-K palette
      // already supports verb-prefix DSL (`pause:` / `resume:`);
      // these snake_case actions make the route consistent so the
      // sidebar can dispatch via `ACTION: hosts_bulk_pause` too.
      ...(typeof this.bulkPauseHosts === 'function' ? [{
        id: 'hosts-bulk-pause',
        label: t('command_palette.action.hosts_bulk_pause', 'Pause hosts'),
        sub: t('command_palette.action.hosts_bulk_pause_sub', 'Pause sampling on the selected host group'),
        verbs: ['pause', 'hosts', 'sampling', 'suspend'],
        destructive: true,
        defer_confirm_to_run: true,
        run: (opts) => {
          this._aiHostDispatch('hosts_bulk_pause', opts || {});
        },
      }] : []),
      ...(typeof this.bulkResumeHosts === 'function' ? [{
        id: 'hosts-bulk-resume',
        label: t('command_palette.action.hosts_bulk_resume', 'Resume hosts'),
        sub: t('command_palette.action.hosts_bulk_resume_sub', 'Resume sampling on the selected host group'),
        verbs: ['resume', 'unpause', 'hosts', 'sampling'],
        destructive: false,
        run: (opts) => {
          this._aiHostDispatch('hosts_bulk_resume', opts || {});
        },
      }] : []),

      // On-demand backup snapshot. Non-destructive (creates a new
      // zip; retention prune fires under the existing TUNABLE).
      ...(typeof this.createBackup === 'function' ? [{
        id: 'backup-create',
        label: t('command_palette.action.backup_create', 'Create backup'),
        sub: t('command_palette.action.backup_create_sub', 'Snapshot the SQLite DB + avatars to a zip'),
        verbs: ['backup', 'snapshot', 'save'],
        destructive: false,
        run: () => {
          this.createBackup();
        },
      }] : []),

      // Send a custom (operator-typed) message to ONE notification
      // medium. Pairs with `ACTION_DATA: {medium, body, title?}`
      // parsed by the AI. Distinct from `test-apprise` (fixed
      // payload, fan-out to ALL enabled mediums). Destructive in
      // the sense that the message goes to real subscribers, so
      // the inline-confirm chip in the AI sidebar gates it.
      {
        id: 'send-notification',
        label: t('command_palette.action.send_notification', 'Send notification'),
        sub: t('command_palette.action.send_notification_sub', 'Send a custom message to one notification channel'),
        verbs: ['send', 'notify', 'tell', 'message', 'telegram', 'apprise'],
        destructive: true,
        defer_confirm_to_run: true,
        run: (opts) => {
          this._aiSendNotificationDispatch(opts || {});
        },
      },

      // AI memory CRUD via palette. Create + delete already exposed
      // via MEMORY: / MEMORY-FORGET: directives; snake_case ids
      // here make the cmd-K route consistent so operator phrasing
      // like "remember that X" / "forget Y" parses to the same
      // dispatch path. ACTION_ITEM carries the memory text (create)
      // or numeric id (delete).
      {
        id: 'ai-memory-create',
        label: t('command_palette.action.ai_memory_create', 'Remember this'),
        sub: t('command_palette.action.ai_memory_create_sub', 'Add a memory the AI will recall in future conversations'),
        verbs: ['remember', 'memorize', 'memorise', 'memory', 'note'],
        destructive: false,
        run: (opts) => {
          const text = ((opts && opts.actionItem) || '').toString().trim();
          if (!text) {
            this.showToast(
              this.t('toasts_extra.ai_memory_no_text') || 'Pass ACTION_ITEM: <memory text> for ai_memory_create.',
              'error',
            );
            return;
          }
          fetch('/api/ai/memory', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text, source: 'operator'}),
          }).then(r => {
            if (!r.ok) {
              throw new Error('HTTP ' + r.status);
            }
            this.showToast(this.t('toasts_extra.ai_memory_added') || 'Memory added', 'success');
          }).catch(e => this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error'));
        },
      },
      {
        id: 'ai-memory-delete',
        label: t('command_palette.action.ai_memory_delete', 'Forget memory'),
        sub: t('command_palette.action.ai_memory_delete_sub', 'Remove an AI memory by exact text'),
        verbs: ['forget', 'remove', 'memory', 'unremember'],
        destructive: true,
        defer_confirm_to_run: true,
        run: (opts) => {
          const text = ((opts && opts.actionItem) || '').toString().trim();
          if (!text) {
            this.showToast(
              this.t('toasts_extra.ai_memory_no_text') || 'Pass ACTION_ITEM: <exact memory text> for ai_memory_delete.',
              'error',
            );
            return;
          }
          fetch('/api/ai/memory/forget', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text}),
          }).then(r => {
            if (!r.ok) {
              throw new Error('HTTP ' + r.status);
            }
            this.showToast(this.t('toasts_extra.ai_memory_forgotten') || 'Memory forgotten', 'success');
          }).catch(e => this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error'));
        },
      },

      // Fire any schedule on-demand. ACTION_ITEM carries the schedule
      // name; the dispatcher looks it up against this.schedules and
      // POSTs to /api/schedules/{id}/run. Non-destructive (operator
      // can still abort the resulting op via the floating ops panel).
      ...(Array.isArray(this.schedules) ? [{
        id: 'schedule-run-now',
        label: t('command_palette.action.schedule_run_now', 'Run schedule now'),
        sub: t('command_palette.action.schedule_run_now_sub', 'Fire a named schedule immediately, bypassing its interval'),
        verbs: ['run', 'fire', 'trigger', 'schedule'],
        destructive: false,
        run: (opts) => {
          const name = ((opts && opts.actionItem) || '').toString().trim();
          if (!name) {
            this.showToast(
              this.t('toasts_extra.schedule_no_target') || 'Pass ACTION_ITEM: <schedule name> for schedule_run_now.',
              'error',
            );
            return;
          }
          const list = Array.isArray(this.schedules) ? this.schedules : [];
          const match = list.find(s => s && (s.name === name));
          if (!match) {
            this.showToast(
              (this.t('toasts_extra.schedule_no_target') || 'Schedule not found: ') + name,
              'error',
            );
            return;
          }
          this.runSchedule(match);
        },
      }] : []),

      // Sign out — destructive (terminates the session)
      ...(typeof this.logout === 'function' ? [{
        id: 'logout',
        label: t('command_palette.action.logout', 'Sign out'),
        verbs: ['logout', 'signout', 'logoff'],
        destructive: true,
        confirmTitle: t('command_palette.action.logout_confirm_title', 'Sign out?'),
        confirmText: t('command_palette.action.logout_confirm_text', 'End your current session'),
        run: () => {
          this.logout();
        }
      }] : []),
    ];
    return actions;
  },
  _commandPaletteParseBulk(rawQuery) {
    const q = (rawQuery || '').trim();
    if (!q) {
      return null;
    }
    // Match `<verb>:` (case-insensitive) at the very start, with
    // optional whitespace after. Anything else is a regular query.
    const m = q.match(/^([a-z_-]+)\s*:\s*(.*)$/i);
    if (!m) {
      return null;
    }
    const verb = m[1].toLowerCase();
    if (!this._BULK_VERBS.includes(verb)) {
      return null;
    }
    const tail = (m[2] || '').trim();
    // Empty tail = no selector yet — still in bulk mode but
    // matches everything (gives the operator immediate visual
    // feedback that bulk mode is engaged + shows the full host
    // list to refine from).
    const tokens = tail
      ? tail.split(/\s+/).filter(Boolean).map(tok => {
        const lc = tok.toLowerCase();
        // `provider:<name>` / `status:<value>` / `paused`
        if (lc === 'paused') {
          return {kind: 'paused'};
        }
        const colon = tok.indexOf(':');
        if (colon > 0) {
          const k = tok.slice(0, colon).toLowerCase();
          const v = tok.slice(colon + 1).toLowerCase();
          if (k === 'provider') {
            return {kind: 'provider', value: v};
          }
          if (k === 'status') {
            return {kind: 'status', value: v};
          }
          // Unknown key:value — treat as a wildcard literal so
          // the user isn't punished for typos; downstream `match`
          // does the substring search.
          return {kind: 'wildcard', value: lc};
        }
        return {kind: 'wildcard', value: lc.replace(/\*/g, '')};
      })
      : [];
    return {verb, tokens, tail};
  },
  _commandPaletteBulkMatchHost(host, tokens) {
    if (!host) {
      return false;
    }
    if (!tokens.length) {
      return true;
    }
    const id = (host.id || host.host || '').toString().toLowerCase();
    const label = (host.label || '').toString().toLowerCase();
    const status = (host.status || '').toString().toLowerCase();
    const paused = !!host.sampling_paused;
    const provs = Array.isArray(host.providers) ? host.providers.map(p => String(p).toLowerCase()) : [];
    for (const tok of tokens) {
      if (tok.kind === 'wildcard') {
        if (!tok.value) {
          continue;
        }
        if (!id.includes(tok.value) && !label.includes(tok.value)) {
          return false;
        }
      } else if (tok.kind === 'provider') {
        if (!provs.includes(tok.value)) {
          return false;
        }
      } else if (tok.kind === 'status') {
        if (status !== tok.value) {
          return false;
        }
      } else if (tok.kind === 'paused') {
        if (!paused) {
          return false;
        }
      }
    }
    return true;
  },
  commandPaletteBulkState() {
    // Single source of truth for "are we in bulk mode and what's
    // the current selection?" Consumed by the chip-strip render,
    // the run-row label, and the activate path. Returns null when
    // bulk mode is NOT active so callers can short-circuit cheaply.
    const parsed = this._commandPaletteParseBulk(this.commandPaletteQuery);
    if (!parsed) {
      return null;
    }
    const all = this.hosts || [];
    const matched = all.filter(h => this._commandPaletteBulkMatchHost(h, parsed.tokens));
    const excluded = this.commandPaletteBulkExcluded || new Set();
    const selected = matched.filter(h => !excluded.has(h.id || h.host));
    return {
      verb: parsed.verb,
      tokens: parsed.tokens,
      matched,
      selected,
      excluded,
    };
  },
  commandPaletteResults(qOverride) {
    // BULK MODE short-circuit — when the query parses as a bulk
    // verb-prefix, return ZERO regular results. The bulk UI lives
    // in a separate render block above the listbox (driven by
    // `commandPaletteBulkState()`), so the listbox stays empty and
    // the keyboard-arrow navigation focuses the chip strip / run
    // button instead of phantom host rows. Skipped when an
    // override is passed (the AI sidebar slash picker doesn't
    // surface bulk mode — that's modal-palette-only).
    if (qOverride === undefined && this.isCommandPaletteBulkMode()) {
      return [];
    }
    const q = (qOverride !== undefined
      ? String(qOverride || '').trim().toLowerCase()
      : (this.commandPaletteQuery || '').trim().toLowerCase());
    const results = [];
    const MAX_PER_GROUP = 8;
    // ACTIONS — verb-first commands. Score by max(label, verbs);
    // verbs that prefix-match the query get a +20 boost so a
    // verb-led query (`refresh` / `restart` / `theme dark`) ranks
    // actions above navigation results. Empty query still surfaces
    // every action at score 1 — operators see the catalog.
    const actions = (typeof this._commandActions === 'function')
      ? this._commandActions() : [];
    const actionScored = actions.map(a => {
      // Empty query → score 1 so the FULL action catalog surfaces
      // when the operator just types `/` with no follow-on text.
      // Operator-flagged: "quick actions is not showing in actions
      // using /" — the previous filter `score > 0` killed every row
      // because `_commandScoreLabel('', '')` returns 0.
      if (!q) {
        return {score: 1, action: a};
      }
      const labelScore = this._commandScoreLabel(a.label, q);
      let verbBoost = 0;
      if (Array.isArray(a.verbs)) {
        for (const v of a.verbs) {
          if (!v) {
            continue;
          }
          const lv = String(v).toLowerCase();
          if (lv === q) {
            verbBoost = Math.max(verbBoost, 100);
            break;
          }
          if (lv.startsWith(q) || q.startsWith(lv + ' ')) {
            verbBoost = Math.max(verbBoost, 70);
          }
        }
      }
      const score = Math.max(labelScore, verbBoost);
      return {score, action: a};
    }).filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, MAX_PER_GROUP);
    for (const x of actionScored) {
      results.push({
        kind: 'action',
        label: x.action.label,
        sub: x.action.sub || '',
        payload: x.action,
        // Pill colour gates on `destructive` so safe commands (theme
        // switch / refresh) get the info accent and destructive
        // ones (logout / cleanup) keep the danger accent. The
        // template binds `cmdpal-kind-<group>` so the group string
        // itself decides the class.
        group: x.action.destructive ? 'actions-destructive' : 'actions',
      });
    }
    // Hosts — search id / label / asset name / vendor / model.
    const hostsList = (this.hosts || []).slice();
    const scored = hostsList.map(h => {
      const asset = (typeof this.assetForHost === 'function') ? this.assetForHost(h) : null;
      const score = this._commandScoreFields(q,
        h.id, h.label, h.host,
        asset && asset.name, asset && asset.vendor, asset && asset.model,
      );
      return {score, host: h, asset};
    }).filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, MAX_PER_GROUP);
    for (const x of scored) {
      const a = x.asset;
      const sub = a && a.name ? a.name : (x.host.label || '');
      results.push({
        kind: 'host',
        label: this.hostDisplayName(x.host) || x.host.id,
        sub: (sub && sub !== x.host.id) ? sub : '',
        payload: x.host,
        group: 'hosts',
      });
    }
    // Items — stacks, services, containers.
    const itemsList = (this.items || []).slice();
    const scoredItems = itemsList.map(i => ({
      score: this._commandScoreFields(q, i.name, i.target, i.stack),
      item: i,
    })).filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, MAX_PER_GROUP);
    for (const x of scoredItems) {
      results.push({
        kind: 'item',
        label: x.item.name || x.item.target,
        sub: x.item.stack ? ('stack: ' + x.item.stack) : (x.item.type || ''),
        payload: x.item,
        group: 'items',
      });
    }
    // Admin routes.
    const adminScored = this._commandAdminRoutes().map(r => ({
      score: this._commandScoreLabel(r.label, q),
      route: r,
    })).filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, MAX_PER_GROUP);
    for (const x of adminScored) {
      results.push({
        kind: 'admin',
        label: x.route.label,
        sub: '',
        payload: x.route.tab,
        group: 'admin',
      });
    }
    // Top-level views.
    const viewScored = this._commandTopViews().map(v => ({
      score: this._commandScoreLabel(v.label, q),
      view: v,
    })).filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, MAX_PER_GROUP);
    for (const x of viewScored) {
      results.push({
        kind: 'view',
        label: x.view.label,
        sub: '',
        payload: x.view.view,
        group: 'views',
      });
    }
    // Hotkeys (info only — selecting one doesn't fire it; just
    // tells the operator the binding exists).
    const hotkeys = [
      {key: '/', desc: this.t('command_palette.hotkey.focus_search')},
      {key: 'r', desc: this.t('command_palette.hotkey.refresh_cached')},
      {key: 'R', desc: this.t('command_palette.hotkey.refresh_force')},
      {key: 'n', desc: this.t('command_palette.hotkey.open_notifications')},
      {key: '?', desc: this.t('command_palette.hotkey.show_hotkeys')},
      {key: 'Esc', desc: this.t('command_palette.hotkey.escape')},
      {key: 'Cmd-K / Ctrl-K', desc: this.t('command_palette.hotkey.open_palette')},
    ];
    const hkScored = hotkeys.map(hk => ({
      score: this._commandScoreFields(q, hk.key, hk.desc),
      hk,
    })).filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, MAX_PER_GROUP);
    for (const x of hkScored) {
      results.push({
        kind: 'hotkey',
        label: x.hk.desc,
        sub: x.hk.key,
        payload: null,
        group: 'hotkeys',
      });
    }
    // AI assistant fallback row — STRICT gate: when AI is disabled
    // (master `ai_enabled=false`) OR no active provider is configured,
    // the synthetic row MUST NOT appear and the palette behaves
    // exactly like the pre-AI version (just navigation + actions).
    // Three independent conditions all have to pass: (1) the operator
    // typed at least 2 chars (skip stray keystrokes), (2) the master
    // `ai_enabled` flag is true, (3) an `active_provider` is selected.
    // The activation handler `_runCommandPaletteAi` re-checks the same
    // gate as defence-in-depth so a stale cached row from a recent
    // toggle-off can't fire. The backend `/api/ai/palette` ALSO
    // re-checks both flags and returns ok=false if either is missing
    // — three layers, no path to the AI provider when disabled.
    const aiSurfaceEnabled = this._aiPaletteSurfaceEnabled();
    if (aiSurfaceEnabled && q && q.length >= 2) {
      const aiCfg = this.me.client_config.ai;
      // Phase 2 — when the query LEADS with a bulk verb as a
      // natural-language phrase (e.g. "pause every host with low
      // disk", "resume all the down hosts") AND doesn't already
      // parse as a Phase 1 DSL, surface a dedicated "AI translate
      // → bulk filter" row that routes to /api/ai/host-filter and
      // sets the palette query to the returned DSL. The Phase 1
      // chip-strip + confirm path then takes over so AI never
      // directly invokes destructive ops.
      const bulkVerbLead = /^\s*(pause|resume)\b\s+\S/i.test(this.commandPaletteQuery || '');
      const looksLikeBulkNL = bulkVerbLead && !this._commandPaletteParseBulk(this.commandPaletteQuery);
      if (looksLikeBulkNL) {
        results.push({
          kind: 'ai-bulk',
          label: this.t('command_palette.ai.bulk_translate_label', {query: q})
            || ('Translate to bulk filter: ' + q),
          sub: this.t('command_palette.ai.bulk_translate_sub')
            || ('Ask ' + aiCfg.active_provider + ' to propose a Phase 1 DSL — you confirm before any host is touched.'),
          payload: {query: this.commandPaletteQuery, provider: aiCfg.active_provider},
          group: 'ai',
        });
      }
      results.push({
        kind: 'ai',
        label: this.t('command_palette.ai.ask_label', {query: q})
          || ('Ask AI: ' + q),
        sub: this.t('command_palette.ai.ask_sub')
          || ('Route through ' + aiCfg.active_provider),
        payload: {query: q, provider: aiCfg.active_provider},
        group: 'ai',
      });
    }
    // Clamp the selected index so it doesn't point past the end of
    // a fresh result set after the query changed. Skipped when
    // called with an override (slash picker has its own selected-
    // index bookkeeping; mutating `commandPaletteSelectedIdx` here
    // would also trip Alpine reactivity from a getter).
    if (qOverride === undefined && this.commandPaletteSelectedIdx >= results.length) {
      this.commandPaletteSelectedIdx = Math.max(0, results.length - 1);
    }
    return results;
  },
  commandPaletteMove(delta) {
    const r = this.commandPaletteResults();
    if (!r.length) {
      return;
    }
    let i = this.commandPaletteSelectedIdx + delta;
    if (i < 0) {
      i = r.length - 1;
    }
    if (i >= r.length) {
      i = 0;
    }
    this.commandPaletteSelectedIdx = i;
    // Scroll the selected row into view inside the result list.
    this.$nextTick(() => {
      const el = document.querySelector(`[data-cmdpal-idx="${i}"]`);
      if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({block: 'nearest'});
      }
    });
  },
  commandPaletteActivate() {
    // Bulk-mode short-circuit — the regular result list is empty
    // when the query parses as a bulk verb-prefix, so Enter has
    // no row to activate. Route to the bulk-run handler instead.
    // The handler does its own confirm + close; we don't close
    // the palette here so it can show the SweetAlert dialog on
    // top of the still-open palette.
    if (this.isCommandPaletteBulkMode()) {
      this.runCommandPaletteBulk();
      return;
    }
    const r = this.commandPaletteResults();
    const sel = r[this.commandPaletteSelectedIdx];
    if (!sel) {
      return;
    }
    this.closeCommandPalette();
    switch (sel.kind) {
      case 'host':
        if (typeof this.openHostDrawer === 'function') {
          this.openHostDrawer(sel.payload);
        }
        break;
      case 'item':
        if (typeof this.openItemDrawer === 'function') {
          this.openItemDrawer(sel.payload);
        } else {
          this.drawerItem = sel.payload;
        }
        break;
      case 'admin':
        this.view = 'admin';
        if (typeof this.setAdminTab === 'function') {
          this.setAdminTab(sel.payload);
        } else {
          this.adminTab = sel.payload;
        }
        break;
      case 'view':
        if (typeof this.setView === 'function') {
          this.setView(sel.payload);
        } else {
          this.view = sel.payload;
        }
        break;
      case 'hotkey':
        // Info-only — no activation. Could open the hotkeys cheat
        // sheet but operators usually want to actually invoke
        // something here, not just see the binding again.
        this.showHotkeys = true;
        break;
      case 'action':
        // Verb commands — `payload` is the descriptor returned by
        // `_commandActions()`. Destructive actions (logout, etc.)
        // confirm via the existing SweetAlert helper before running.
        this._runCommandPaletteAction(sel.payload);
        break;
      case 'ai':
        // AI assistant — opens a SweetAlert with a loading spinner,
        // POSTs the query + minimal context to /api/ai/palette, and
        // replaces the spinner with the model's response. Best-effort:
        // any error surfaces as the dialog body so the operator can
        // see WHY the AI didn't answer (master switch off, no API
        // key, rate limited, etc.).
        this._runCommandPaletteAi(sel.payload);
        break;
      case 'ai-bulk':
        // Phase 2 bulk-translate — does NOT close the palette; calls
        // /api/ai/host-filter, then writes the returned DSL into
        // commandPaletteQuery so Phase 1's parser flips into bulk
        // mode (chip strip + Run button) for the operator to confirm.
        // Reopen the palette since we already closed it above.
        this.openCommandPalette();
        this._runCommandPaletteAiBulk(sel.payload);
        break;
    }
  },
  async _runCommandPaletteAction(action, opts) {
    if (!action || typeof action.run !== 'function') {
      return;
    }
    const fromSidebar = !!(opts && opts.surface === 'sidebar');
    let skipConfirm = !!(opts && opts.skipConfirm);
    // Per-action params forwarded to the descriptor's `run(opts)`.
    // The retag_image action consumes `tag` (from ACTION_TAG) and
    // `actionItem` (from ACTION_ITEM); other actions today ignore
    // these. Add fields here as new parameterised actions land
    // rather than introducing a parallel kwargs envelope.
    const actionTag = (opts && opts.tag) || (opts && opts.actionTag) || '';
    const actionItem = (opts && opts.item) || (opts && opts.actionItem) || '';
    // Structured payload from the AI's `ACTION_DATA: {<json>}`
    // directive — currently consumed by schedule_create /
    // schedule_update / schedule_delete. Pass-through verbatim.
    const actionData = (opts && opts.data) || (opts && opts.actionData) || null;
    // Some destructive actions wrap their OWN SweetAlert confirm
    // INSIDE `run()` and present a richer payload (e.g. the topbar
    // Cleanup flow lists every container by name). Opting in via
    // `defer_confirm_to_run: true` skips the generic dialog so we
    // don't double-popup.
    if (action.destructive && !skipConfirm) {
      if (fromSidebar) {
        // AI sidebar surface — operator-flagged "no popups in the
        // sidebar, ever". Two sub-modes:
        //   approval (default): inline-confirm chip in the chat;
        //     operator clicks Yes / Cancel before the action fires.
        //   autonomous: action fires IMMEDIATELY with no chip and no
        //     popup — agentic workflow where the AI acts without
        //     intervention.
        // In BOTH modes the inner helper's SwAl popup is bypassed via
        // `skipConfirm=true` so the sidebar never raises a modal.
        // The approval-mode Yes-click path also sets skipConfirm=true
        // via `confirmInlineAction`.
        if (this.aiSidebarMode === 'autonomous') {
          // Autonomous: action runs RIGHT NOW. Force skipConfirm=true
          // so the inner helper's SwAl (cleanup_stopped's listing,
          // update_all_updatable's listing, etc.) doesn't pop.
          skipConfirm = true;
        } else {
          // Approval mode — stash the pending action on the most-
          // recent assistant turn; the inline chip in the chat
          // handles Yes/Cancel. Action does NOT fire here.
          const idx = this.aiConversation.length - 1;
          const turn = this.aiConversation[idx];
          if (turn) {
            turn.pending_confirm = true;
            turn.pending_action = action;
            this.persistAiConversation();
            this._scrollAiSidebarToBottom();
          }
          return;
        }
      } else {
        // Modal palette surface — keep the SweetAlert popup since
        // there's no chat to inline-confirm into. Actions opting into
        // `defer_confirm_to_run` skip the generic dialog so their
        // run() can show a richer data confirm.
        if (!action.defer_confirm_to_run) {
          const ok = await this.confirmDialog({
            title: action.confirmTitle || action.label,
            html: action.confirmText || (this.t('command_palette.action.destructive_confirm')
              || 'This action affects live state — proceed?'),
            icon: 'warning',
            confirmText: action.confirmButton || (this.t('actions.confirm') || 'Confirm'),
            focusConfirm: true,
          });
          if (!ok) {
            return;
          }
        }
      }
    }
    try {
      // Forward parameterised-action params via the run(opts)
      // envelope. `skipConfirm` is forwarded (true in sidebar mode
      // after autonomous fall-through OR after the approval-mode
      // Yes-click; false in modal palette where the generic SwAl
      // already confirmed). Inner helpers honour skipConfirm to
      // bypass their own SwAl popups — no double-confirm anywhere.
      await action.run({skipConfirm: skipConfirm, tag: actionTag, actionItem: actionItem, data: actionData});
      // Sidebar: flip the most-recent assistant turn's `action_ran`
      // to true so the green "Ran:" chip surfaces. Skip when this
      // call is from the modal palette path (no turn to update).
      if (fromSidebar) {
        const idx = this.aiConversation.length - 1;
        const turn = this.aiConversation[idx];
        if (turn && turn.action_id === action.id) {
          turn.action_ran = true;
          this.persistAiConversation();
        }
      }
    } catch (e) {
      if (typeof this.showToast === 'function') {
        this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
      }
    }
  },
  // Resolve a snake_case action ID emitted by the AI palette
  // backend (e.g. `mark_all_notifications_read`) to a descriptor
  // from `_commandActions()` (which uses kebab-case ids like
  // `mark-all-notifications-read`). Returns null when no match.
  //
  // Backend-allowed-set guard — if the resolved snake_case is NOT
  // in `me.client_config.ai.palette_actions` (the canonical
  // `logic.ai.ALLOWED_PALETTE_ACTIONS` whitelist surfaced via
  // /api/me), the dispatch refuses immediately. This catches
  // AI-hallucinated actions before they hit `_commandActions()` /
  // the alias map below — pre-fix an AI emitting an off-whitelist
  // action would silently return null after the alias chain failed,
  // and the operator saw "I'll do X" with no apparent error.
  // Defence-in-depth — the backend also rejects off-whitelist
  // actions; this surfaces the rejection at SPA-dispatch time
  // instead of waiting for the (already-completed) AI response.
  _actionDescriptorById(snakeId) {
    const id = (snakeId || '').toString().trim();
    if (!id) {
      return null;
    }
    // Resolve canonical alias FIRST so the whitelist check sees the
    // post-alias name (e.g. `sign_out` → `logout` → check `logout`
    // in the whitelist, not the synonym). Done by checking BOTH the
    // raw `id` and the alias-resolved name against the backend set.
    const allowed = (this.me && this.me.client_config && this.me.client_config.ai
      && Array.isArray(this.me.client_config.ai.palette_actions))
      ? new Set(this.me.client_config.ai.palette_actions)
      : null;
    const kebab = id.replace(/_/g, '-');
    // Backend's snake_case set maps to kebab-case ids in
    // `_commandActions()`. A few synonyms — `logout` / `sign_out` /
    // `signout` all share one descriptor.
    const aliasMap = {
      sign_out: 'logout',
      sign_off: 'logout',
      logoff: 'logout',
      // Refresh / reload — `ALLOWED_PALETTE_ACTIONS` declares the
      // bare verb forms but the SPA's descriptor ids are kebab-suffixed
      // (`refresh-now` for the gather refresh, `reload-spa` for the
      // page hard-reload). Without these aliases an AI emitting bare
      // `ACTION: refresh` / `ACTION: reload` silently no-ops because
      // the kebab fall-through (`refresh` / `reload`) doesn't match
      // any descriptor.
      refresh: 'refresh-now',
      reload: 'reload-spa',
      // Cleanup synonyms — the AI prompt teaches the model the
      // canonical `cleanup_stopped` id but operators may type the
      // kebab form (`cleanup-stopped`) verbatim too.
      cleanup_stopped: 'cleanup-stopped',
      cleanup: 'cleanup-stopped',
      // Update-all synonyms — canonical id is `update_all_updatable`;
      // accept the looser shapes operators emit when chaining
      // imperatives ("update stacks and refresh", "update all").
      update_all_updatable: 'update-all-updatable',
      update_all_stacks: 'update-all-updatable',
      update_all: 'update-all-updatable',
      update_stacks: 'update-all-updatable',
      upgrade_all: 'update-all-updatable',
      // Port-scan + per-provider Test connection actions —
      // recently added to ALLOWED_PALETTE_ACTIONS in the backend
      // (`logic/ai.py`); the SPA's catalog uses kebab ids so the
      // alias map translates the snake-case the AI emits.
      scan_ports: 'scan-ports',
      discover_apps: 'discover-apps',
      test_portainer: 'test-portainer',
      test_oidc: 'test-oidc',
      test_beszel: 'test-beszel',
      test_pulse: 'test-pulse',
      test_webmin: 'test-webmin',
      // retag_image — switch a container/stack item's image tag to
      // a different floating tag. Snake-case canonical id matches
      // backend's ALLOWED_PALETTE_ACTIONS; kebab descriptor lives
      // in `_commandActions()`. Synonyms cover the natural-language
      // ways the operator might phrase it ("switch tag", "retag",
      // "pin to v2") so the model has a stable map.
      retag_image: 'retag-image',
      switch_tag: 'retag-image',
      pin_to_tag: 'retag-image',
      change_tag: 'retag-image',
      track_tag: 'retag-image',
      // Schedule CRUD via AI palette. Operator synonyms cover
      // common phrasings so the model has a stable map.
      schedule_create: 'schedule-create',
      create_schedule: 'schedule-create',
      add_schedule: 'schedule-create',
      new_schedule: 'schedule-create',
      schedule_update: 'schedule-update',
      update_schedule: 'schedule-update',
      modify_schedule: 'schedule-update',
      change_schedule: 'schedule-update',
      edit_schedule: 'schedule-update',
      schedule_delete: 'schedule-delete',
      delete_schedule: 'schedule-delete',
      remove_schedule: 'schedule-delete',
      // Item write-ops + bulk + memory / schedule_run_now — snake_case
      // canonical ids match `ALLOWED_PALETTE_ACTIONS` in `logic/ai.py`;
      // kebab descriptor lives in `_commandActions()`. Synonyms cover
      // common operator phrasings ("update the auth stack" / "restart
      // traefik" / "back up now" / "remember that X" etc.) so the AI
      // emits whichever snake_case verb feels natural and the SPA
      // resolves it to the right descriptor.
      update_stack: 'update-stack',
      update_container: 'update-container',
      recreate_container: 'update-container',
      restart_service: 'restart-service',
      restart_container: 'restart-container',
      bounce_service: 'restart-service',
      bounce_container: 'restart-container',
      remove_container: 'remove-container',
      delete_container: 'remove-container',
      prune_node: 'prune-node',
      prune_docker: 'prune-node',
      hosts_bulk_pause: 'hosts-bulk-pause',
      pause_hosts: 'hosts-bulk-pause',
      // bulk_pause_hosts / bulk_resume_hosts — operator-natural
      // phrasings the AI emits when the user says "pause all hosts"
      // / "resume all hosts" without the `hosts_*` prefix.
      bulk_pause_hosts: 'hosts-bulk-pause',
      bulk_resume_hosts: 'hosts-bulk-resume',
      hosts_bulk_resume: 'hosts-bulk-resume',
      resume_hosts: 'hosts-bulk-resume',
      unpause_hosts: 'hosts-bulk-resume',
      // prune_stopped / clear_notifications / notifications_clear_all
      // — operator synonyms for the existing cleanup-stopped +
      // mark-all-notifications-read descriptors. The AI emits
      // whichever phrasing feels natural ("prune stopped containers"
      // / "clear notifications" / "mark all read") and the SPA
      // resolves to the same dispatch path.
      prune_stopped: 'cleanup-stopped',
      clear_notifications: 'mark-all-notifications-read',
      notifications_clear_all: 'mark-all-notifications-read',
      backup_create: 'backup-create',
      create_backup: 'backup-create',
      snapshot_backup: 'backup-create',
      ai_memory_create: 'ai-memory-create',
      remember_this: 'ai-memory-create',
      ai_memory_delete: 'ai-memory-delete',
      forget_memory: 'ai-memory-delete',
      schedule_run_now: 'schedule-run-now',
      run_schedule_now: 'schedule-run-now',
      fire_schedule: 'schedule-run-now',
      // send_notification — custom (operator-typed) message routed
      // to ONE medium. Pairs with `ACTION_DATA: {medium, body, title?}`
      // parsed by `parseAiActionData(...)`. Backend endpoint
      // `POST /api/notify/send` is admin-only + audited under
      // `op_type='notify_send'`. Operator phrasings: "send to
      // telegram <text>", "tell apprise <text>", "notify <channel>
      // that <text>".
      send_notification: 'send-notification',
      send_telegram: 'send-notification',
      send_apprise: 'send-notification',
      notify_channel: 'send-notification',
      message_channel: 'send-notification',
    };
    const target = aliasMap[id] || kebab;
    // Backend-allowed-set guard (post-alias) — verify the snake_case
    // pre-image of the resolved descriptor is whitelisted. We check
    // BOTH `id` (the AI's raw emission) AND a re-snake'd `target`
    // (descriptor.id with `-`→`_`) against the whitelist so the
    // synonym aliases (e.g. `sign_out` → `logout`) still pass when
    // `logout` is the whitelisted canonical name. Skip the guard
    // entirely when the backend hasn't supplied the whitelist
    // (older deploy, /api/me race) — graceful degrade to the
    // pre-guard behaviour.
    if (allowed && allowed.size > 0) {
      const targetSnake = target.replace(/-/g, '_');
      if (!allowed.has(id) && !allowed.has(targetSnake)) {
        try {
          // eslint-disable-next-line no-console
          console.warn('[ai-palette] action not in backend whitelist: '
            + id + ' (resolved=' + target + ')');
        } catch (_) { /* console-write must never break dispatch */ }
        return null;
      }
    }
    const all = (typeof this._commandActions === 'function')
      ? this._commandActions() : [];
    return all.find(a => a.id === target) || null;
  },
};
