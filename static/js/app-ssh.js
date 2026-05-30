// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ExceptionCaughtLocallyJS,JSReusedLocalVariable,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,RedundantLocalVariableJS,JSMissingAwait,JSAsyncFunctionMissingAwait
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSCheckNamingConventionsInspection,UnnecessaryLocalVariableJS,RegExpRedundantEscape,JSPotentiallyInvalidUsageOfThis,JSUnfilteredForInLoop,IfStatementWithoutBlockJS
// Sibling-file canonical noinspection block — same shape as
// app-admin.js / app-charts.js / app-ai.js / app-stats.js so the
// suppressed warning classes stay consistent across the SPA. Real
// bugs (typos / dead assignments / wrong types) are fixed inline,
// NOT suppressed.
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA SSH surface — admin SSH terminal modal + per-host SSH card +
// global SSH settings (Admin → SSH).

export default {
  // ---- SSH console state ----
  // sshStatus[host.id]       = { configured, enabled, resolved }
  // sshOpen[host.id]         = drawer card expanded?
  // sshResult[host.id]       = { ok, exit_code, stdout, stderr, duration_ms, dry_run, resolved, destructive }
  // sshCommand[host.id]      = textarea contents (in-memory only)
  // sshDryRun[host.id]       = "preview only" checkbox (defaults to false —
  //                           operator explicitly opts INTO dry-run; destructive
  //                           commands still gate on typed-hostname confirm)
  // sshBusy[host.id]         = bool — a run is in flight
  // sshTestBusy[host.id]     = bool — Test connection in flight
  // sshLastTested[host.id]   = epoch ms of last successful status probe
  // sshSettings / sshSettingsDirty / sshSettingsBusy — Admin → SSH form state.
  // sshTestOnHost            = { host_id, result, pending } for the Admin widget.
  sshStatus: {},
  sshOpen: {},
  sshResult: {},
  sshCommand: {},
  sshDryRun: {},
  sshBusy: {},
  sshTestBusy: {},
  sshLastTested: {},
  // ---- Interactive SSH terminal modal state ----
  // terminalModalOpen     bool     visibility flag (drives the modal x-show).
  // terminalHost          object   the host row currently being driven.
  // terminalState         string   'connecting' | 'connected' | 'disconnected' | 'error'
  // terminalCloseReason   string   short message shown alongside the status pill.
  // terminalResolved      object   { user, host, port, ... } from the ws "ready" frame.
  // terminal              xterm.js Terminal instance — assigned after $nextTick.
  // terminalFit           FitAddon instance for window-resize handling.
  // terminalSocket        WebSocket — null when no session active.
  // terminalResizeBound   bound resize listener — kept so close() can detach it.
  // terminalResizeObs     ResizeObserver on the .terminal-host element — refits
  //                     whenever the container's box changes (modal animation
  //                     commit, parent reflow). The window-resize listener
  //                     alone misses the initial open because the modal has
  //                     no resize event of its own.
  terminalModalOpen: false,
  terminalHost: null,
  terminalState: 'connecting',
  terminalCloseReason: '',
  terminalResolved: null,
  terminal: null,
  terminalFit: null,
  terminalSocket: null,
  terminalResizeBound: null,
  terminalResizeObs: null,
  terminalFitTimers: null,
  sshSettings: {
    user: '', port: 22, private_key: '', passphrase: '',
    password: '', fqdn_suffix: '',
    known_hosts: '', destructive_patterns: '',
    private_key_set: false, passphrase_set: false, password_set: false,
    // custom_actions: [{id, title, command}] — rendered as preset
    // buttons in the host drawer SSH card. Seeded from the legacy
    // hardcoded 5 when the DB row is empty (see seedDefaultSshActions).
    custom_actions: [],
  },
  sshSettingsDirty: false,
  sshSettingsBusy: false,
  sshTestOnHost: {host_id: '', result: null, pending: false},
  // -------- SSH console ----------------------------------------------
  // Hydrate the Admin → SSH form from /api/settings. Called from
  // loadSettings() alongside the other provider-specific pulls so the
  // form has values ready when the admin opens the section.
  hydrateSshSettings(apiSettings) {
    const s = (apiSettings && apiSettings.ssh) || {};
    const actions = Array.isArray(s.custom_actions) ? s.custom_actions : [];
    this.sshSettings = {
      user: s.user || '',
      port: s.port || 22,
      private_key: '',               // write-only — never hydrated
      passphrase: '',               // write-only — never hydrated
      password: '',               // write-only — never hydrated
      fqdn_suffix: s.fqdn_suffix || '',
      known_hosts: s.known_hosts || '',
      destructive_patterns: s.destructive_patterns || '',
      private_key_set: !!s.private_key_set,
      passphrase_set: !!s.passphrase_set,
      password_set: !!s.password_set,
      // Seed with the historical 5 presets when the DB row is empty
      // so fresh installs don't start with a bare SSH card. Operators
      // can edit / remove any of them from Admin → SSH.
      custom_actions: actions.length ? actions : this.defaultSshCustomActions(),
    };
    this.sshSettingsDirty = false;
  },
  markSshSettingsDirty() {
    this.sshSettingsDirty = true;
  },
  // The 5 baked-in commands OmniGrid used before custom actions
  // became editable. Used only as a first-boot seed — once the
  // operator saves anything, the DB row wins.
  defaultSshCustomActions() {
    // Titles are resolved through t() at SEED TIME (first boot with
    // an empty ssh_custom_actions row). Once the operator saves,
    // titles become plain DB strings — switching UI language later
    // does NOT retranslate persisted actions (they're editable
    // per-install strings now, not static UI chrome). IDs stay
    // stable so a "reset to defaults" flow could still match them.
    const tr = (k, fallback) => {
      const v = this.t('admin_ssh.default_actions.' + k);
      return (v && v !== ('admin_ssh.default_actions.' + k)) ? v : fallback;
    };
    return [
      {
        id: 'restart-beszel',
        title: tr('restart_beszel', 'Restart Beszel agent'),
        command: 'systemctl restart beszel-agent || docker restart beszel-agent'
      },
      {
        id: 'show-beszel-env',
        title: tr('show_beszel_env', 'Show Beszel agent env'),
        command: "systemctl show beszel-agent -p Environment || docker inspect beszel-agent --format '{{range .Config.Env}}{{println .}}{{end}}'"
      },
      {
        id: 'set-beszel-nics',
        title: tr('set_beszel_nics', 'Set Beszel NICS (edit eth0 first)'),
        command:
          "mkdir -p /etc/systemd/system/beszel-agent.service.d && " +
          "printf '[Service]\\nEnvironment=NICS=eth0\\n' > /etc/systemd/system/beszel-agent.service.d/nics.conf && " +
          "systemctl daemon-reload && systemctl restart beszel-agent"
      },
      {
        id: 'verify-beszel-nics',
        title: tr('verify_beszel_nics', 'Verify Beszel NICS setup'),
        command:
          "echo '=== override.conf (systemd) ===' && " +
          "ls -la /etc/systemd/system/beszel-agent.service.d/ 2>&1 || true; " +
          "cat /etc/systemd/system/beszel-agent.service.d/*.conf 2>&1 || true; " +
          "echo '=== beszel-agent unit Environment ===' && " +
          "systemctl show beszel-agent -p Environment 2>&1 || true; " +
          "echo '=== beszel-agent process env (if running) ===' && " +
          "(ps -eo pid,comm,args 2>/dev/null | grep -E 'beszel.?agent' | grep -v grep || echo 'no beszel process found'); " +
          "echo '=== docker fallback ===' && " +
          "(docker inspect beszel-agent --format '{{range .Config.Env}}{{println .}}{{end}}' 2>&1 || echo 'no docker beszel-agent container'); " +
          "echo '=== sudo sanity ===' && sudo -n whoami 2>&1"
      },
      {
        id: 'journal-beszel',
        title: tr('journal_beszel', 'Journal: Beszel agent (last 40)'),
        command: 'journalctl -u beszel-agent -n 40 --no-pager'
      },
      {
        id: 'ip-link',
        title: tr('ip_link', 'List NICs (ip link)'),
        command: 'ip -o link show'
      },
      {
        id: 'uptime',
        title: tr('uptime', 'Uptime + load'),
        command: 'uptime'
      },
    ];
  },
  addSshCustomAction() {
    this.sshSettings.custom_actions = [
      ...(this.sshSettings.custom_actions || []),
      {id: '', title: '', command: ''},
    ];
    this.markSshSettingsDirty();
  },
  removeSshCustomAction(idx) {
    const arr = (this.sshSettings.custom_actions || []).slice();
    arr.splice(idx, 1);
    this.sshSettings.custom_actions = arr;
    this.markSshSettingsDirty();
  },
  moveSshCustomAction(idx, delta) {
    const arr = (this.sshSettings.custom_actions || []).slice();
    const target = idx + delta;
    if (target < 0 || target >= arr.length) {
      return;
    }
    const [row] = arr.splice(idx, 1);
    arr.splice(target, 0, row);
    this.sshSettings.custom_actions = arr;
    this.markSshSettingsDirty();
  },
  // Erase a stored SSH secret (key / passphrase / password). The
  // normal save path preserves secrets on blank input (so ops don't
  // accidentally wipe a stored key by saving unrelated changes),
  // which means there's no other way to clear them. Confirm first
  // so a misclick doesn't lock the operator out of every host.
  async clearSshSecret(kind) {
    const label = kind === 'private_key' ? 'SSH private key'
      : kind === 'passphrase' ? 'passphrase'
        : 'default password';
    const promptTitle = this.t('toasts_extra.ssh_clear_secret_prompt_title', {label});
    const ok = typeof Swal !== 'undefined'
      ? (await Swal.fire({
        title: promptTitle,
        text: kind === 'private_key'
          ? 'This also clears the passphrase. You will need to paste a new key before any SSH action will work.'
          : kind === 'passphrase'
            ? 'The private key stays, but will be tried unprotected. If it needs a passphrase, SSH auth will fail.'
            : 'Hosts with a per-host password override will still work; everything else will need a new default.',
        icon: 'warning',
        showCancelButton: true,
        confirmButtonText: this.t('actions.clear'),
        cancelButtonText: this.t('actions.cancel'),
      })).isConfirmed
      : window.confirm(promptTitle);
    if (!ok) {
      return;
    }
    try {
      const body = {};
      if (kind === 'private_key') {
        body.clear_ssh_private_key = true;
      }
      if (kind === 'passphrase') {
        body.clear_ssh_passphrase = true;
      }
      if (kind === 'password') {
        body.clear_ssh_password = true;
      }
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts_extra.clear_failed_generic'), 'error');
        return;
      }
      await this.loadSettings();
      this.showToast(label[0].toUpperCase() + label.slice(1) + ' cleared', 'success');
    } catch (e) {
      this.showToast(this.t('toasts_extra.clear_failed_with_error', {error: e.message}), 'error');
    }
  },
  async saveSshSettings() {
    this.sshSettingsBusy = true;
    try {
      const body = {
        // Master switch saved alongside the rest of the form
        // — flipping the toggle marks the form dirty; clicking Save
        // commits both the toggle and any field edits in one go.
        ssh_enabled: !!this.settings.ssh_enabled,
        ssh_default_user: this.sshSettings.user || '',
        ssh_default_port: parseInt(this.sshSettings.port, 10) || 22,
        ssh_fqdn_suffix: this.sshSettings.fqdn_suffix || '',
        ssh_default_known_hosts: this.sshSettings.known_hosts || '',
        ssh_destructive_patterns: this.sshSettings.destructive_patterns || '',
        // Backend drops rows with empty title or command, so clean
        // slots the operator left blank simply vanish on save.
        ssh_custom_actions: (this.sshSettings.custom_actions || [])
          .map(a => ({
            id: (a.id || '').trim(),
            title: (a.title || '').trim(),
            command: (a.command || '').trim(),
          })),
      };
      // Write-only: only send when the operator typed a new value.
      if ((this.sshSettings.private_key || '').trim() !== '') {
        body.ssh_default_private_key = this.sshSettings.private_key;
      }
      if ((this.sshSettings.passphrase || '').trim() !== '') {
        body.ssh_default_private_key_passphrase = this.sshSettings.passphrase;
      }
      if ((this.sshSettings.password || '').trim() !== '') {
        body.ssh_default_password = this.sshSettings.password;
      }
      // SSH-section tunables — included in the same POST so the
      // SSH Save commits them alongside the rest of the SSH config.
      for (const k of this._sshSectionTuningKeys()) {
        const v = (this.tuningForm || {})[k];
        body[k] = (v == null ? '' : String(v).trim());
      }
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(this.fmtApiError(j, r.status));
      }
      // Re-pull settings + tuning baseline so the section's dirty
      // tracker resets after a clean save. Pre-fix `loadTuning` was
      // skipped here, so any tuning the section owns
      // (`tuning_ssh_ws_heartbeat_seconds`) kept reporting dirty
      // forever because `_tuningBaseline` was stale.
      await Promise.all([this.loadSettings(), this.loadTuning()]);
      this.showToast(this.t('toasts_extra.ssh.settings_saved'), 'success');
    } catch (e) {
      this.showToast(
        this.t('toasts_extra.save_failed_generic') + ': ' + e.message,
        'error',
      );
    } finally {
      this.sshSettingsBusy = false;
    }
  },
  // Per-host drawer card — lazy fetch ssh/status when the card opens
  // so the drawer still paints instantly for hosts where the admin
  // never touches SSH. `refresh: true` bypasses the short-circuit so
  // "Test connection" can force a fresh status read.
  async loadSshStatus(hostId, {refresh = false} = {}) {
    if (!hostId) {
      return;
    }
    if (!refresh && this.sshStatus[hostId]) {
      return this.sshStatus[hostId];
    }
    try {
      const r = await fetch('/api/hosts/' + encodeURIComponent(hostId) + '/ssh/status');
      if (!r.ok) {
        this.sshStatus = {...this.sshStatus, [hostId]: {error: `HTTP ${r.status}`}};
        return;
      }
      const d = await r.json();
      this.sshStatus = {...this.sshStatus, [hostId]: d};
      return d;
    } catch (e) {
      this.sshStatus = {...this.sshStatus, [hostId]: {error: e.message}};
    }
  },
  async toggleSshCard(hostId) {
    if (!hostId) {
      return;
    }
    const open = !this.sshOpen[hostId];
    this.sshOpen = {...this.sshOpen, [hostId]: open};
    if (open) {
      // Default to dry-run OFF — operator explicitly opts in. The
      // destructive-command gate (typed-hostname confirm) still
      // fires for `rm`/`dd`/`reboot`/etc. regardless of this
      // checkbox, so accidental nukes remain blocked.
      if (!(hostId in this.sshDryRun)) {
        this.sshDryRun = {...this.sshDryRun, [hostId]: false};
      }
      // Scroll the just-expanded SSH-run body to the top of the
      // drawer viewport so the operator doesn't have to scroll
      // down on a long drawer. $nextTick lets x-show flip
      // before we measure; data-host-section keeps the selector
      // stable across host changes.
      this.$nextTick(() => {
        const el = document.querySelector(
          `[data-host-section="ssh-${hostId}"]`,
        );
        if (el) {
          try {
            el.scrollIntoView({behavior: 'smooth', block: 'start'});
          } catch (_) {
          }
        }
      });
      await this.loadSshStatus(hostId);
    }
  },
  async testSshConnection(hostId) {
    if (!hostId) {
      return;
    }
    this.sshTestBusy = {...this.sshTestBusy, [hostId]: true};
    try {
      const r = await fetch('/api/hosts/' + encodeURIComponent(hostId) + '/ssh/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: '{}',
      });
      const j = await r.json().catch(() => ({}));
      this.sshResult = {...this.sshResult, [hostId]: j};
      this.sshLastTested = {...this.sshLastTested, [hostId]: Date.now()};
      if (j.ok) {
        const resolved = j.resolved || {};
        this.showToast(
          this.t('toasts_extra.ssh.test_ok', {
            user: resolved.user || '?',
            host: resolved.host || hostId,
          }),
          'success',
        );
      } else {
        this.showToast(
          this.t('toasts_extra.ssh.test_failed', {detail: j.error || 'unknown'}),
          'error',
        );
      }
      // Re-hydrate the status card so the fingerprint / last-tested
      // fields reflect whatever this probe learned.
      await this.loadSshStatus(hostId, {refresh: true});
    } catch (e) {
      this.showToast(this.t('toasts_extra.ssh.test_failed', {detail: e.message}), 'error');
    } finally {
      this.sshTestBusy = {...this.sshTestBusy, [hostId]: false};
    }
  },
  // DB-backed custom action → drop the rendered command into the
  // textarea so the operator can review before hitting Run. `{host}`
  // is substituted with the resolved target hostname (falls back to
  // the row id when the status probe hasn't populated yet). We
  // DELIBERATELY don't auto-execute — destructive commands go
  // through the Run button's confirmation gate.
  // True when SSH actions are safe to run against this host. Gated
  // on live provider status: if the host isn't `up` (or we haven't
  // heard from a provider yet — `loading` / `unconfigured` /
  // `unknown` / `down` / `paused`), trying to SSH in will just
  // produce an OSError 113 ("No route to host") after a long
  // timeout. The drawer's UI binds Run + custom-action buttons +
  // the command textarea to this so operators can't fire dead
  // commands. Backend still enforces via classify_exception →
  // HOST_UNREACHABLE for defense in depth.
  isSshAllowed(h) {
    return !!(h && h.status === 'up');
  },
  sshDisabledReason(h) {
    if (!h) {
      return '';
    }
    if (h.status === 'up') {
      return '';
    }
    // Human-readable hint for the disabled tooltip — reason varies
    // by status so the operator knows whether to wait (loading),
    // configure a provider (unconfigured), or investigate (down /
    // unknown / paused).
    return this.t('hosts_extra_ssh.disabled_not_up', {
      status: String(h.status || '—'),
    });
  },
  runSshCustomAction(hostId, action) {
    if (!hostId || !action || !action.command) {
      return;
    }
    const resolved = (this.sshStatus[hostId] && this.sshStatus[hostId].resolved) || {};
    const host = resolved.host || hostId;
    const cmd = String(action.command).replace(/\{host}/g, host);
    this.sshCommand = {...this.sshCommand, [hostId]: cmd};
  },
  async runSshCommand(hostId) {
    const h = (this.hosts || []).find(x => x && x.id === hostId);
    if (!this.isSshAllowed(h)) {
      this.showToast(this.sshDisabledReason(h), 'error');
      return;
    }
    const command = (this.sshCommand[hostId] || '').trim();
    if (!command) {
      this.showToast(this.t('toasts_extra.ssh.command_required'), 'error');
      return;
    }
    const dryRun = this.sshDryRun[hostId] === true;
    // Destructive-command gate. The backend flags + returns
    // `destructive: [patterns]` but we check up-front too so we can
    // raise the bar BEFORE sending the payload.
    const destructiveRegex = [
      /\brm\s/i, /\bmkfs\b/i, /\bdd\s/i, />\s*\//, /\bsystemctl\s+stop\b/i,
      /\breboot\b/i, /\bpoweroff\b/i, /\bshutdown\b/i,
    ];
    const looksDestructive = !dryRun && destructiveRegex.some(r => r.test(command));
    if (looksDestructive) {
      const host = (this.sshStatus[hostId] && this.sshStatus[hostId].resolved && this.sshStatus[hostId].resolved.host) || hostId;
      const typed = window.prompt(
        this.t('hosts_extra_ssh.confirm_prompt', {host}),
        '',
      );
      if ((typed || '').trim() !== host) {
        this.showToast(this.t('toasts_extra.ssh.confirm_wrong_host'), 'error');
        return;
      }
    }
    this.sshBusy = {...this.sshBusy, [hostId]: true};
    try {
      const r = await fetch('/api/hosts/' + encodeURIComponent(hostId) + '/ssh/run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({command, dry_run: dryRun}),
      });
      const j = await r.json().catch(() => ({}));
      this.sshResult = {...this.sshResult, [hostId]: j};
      if (j.ok) {
        this.showToast(
          dryRun
            ? this.t('toasts_extra.ssh.dry_run_ok')
            : this.t('toasts_extra.ssh.run_ok', {code: j.exit_code}),
          'success',
        );
      } else {
        this.showToast(
          (dryRun
            ? this.t('toasts_extra.ssh.dry_run_failed', {detail: j.error || 'unknown'})
            : this.t('toasts_extra.ssh.run_failed', {detail: j.error || 'unknown'})),
          'error',
        );
      }
    } catch (e) {
      this.showToast(this.t('toasts_extra.ssh.run_failed', {detail: e.message}), 'error');
    } finally {
      this.sshBusy = {...this.sshBusy, [hostId]: false};
    }
  },
  async copySshOutput(hostId) {
    const r = this.sshResult[hostId];
    if (!r) {
      return;
    }
    const blob = [
      '$ ' + (this.sshCommand[hostId] || ''),
      '--- exit ---',
      String(r.exit_code),
      '--- stdout ---',
      r.stdout || '',
      '--- stderr ---',
      r.stderr || '',
    ].join('\n');
    try {
      await navigator.clipboard.writeText(blob);
      this.showToast(this.t('toasts_extra.ssh.copied_output'), 'success');
    } catch (_) {
      window.prompt('Copy output', blob);
    }
  },

  // ---- Interactive SSH terminal modal ----
  // Browser <-WSS-> backend <-asyncssh shell-> target host. Uses
  // xterm.js for the viewport (UMD bundles preloaded in <head>).
  // The modal lives at top-level so its WS survives drawer-state
  // transitions; closing drops the socket cleanly.
  // Lazy-load xterm.js + its addons + stylesheet on first use. xterm
  // registers wheel + touch handlers without `{passive: true}` because
  // it needs preventDefault for its custom scrollback behaviour; we
  // can't change that without forking. The practical mitigation is to
  // defer the registration until the SSH terminal is actually opened
  // — so steady-state browsing (Hosts / Apps / Admin) doesn't trip
  // the Chromium `[Violation] Added non-passive event listener to a
  // scroll-blocking 'mousewheel' event` warning. Returns a promise
  // that resolves to `true` once `window.Terminal` is callable; on
  // failure resolves to `false` (the caller surfaces the same toast
  // the pre-load path used to surface when CSP blocked the bundle).
  // Memoized via `window.__ogXtermLoadPromise` so a fast double-click
  // doesn't fan out two injections.
  _loadXtermDeps() {
    if (typeof window.Terminal === 'function'
      && typeof window.FitAddon === 'object'
      && typeof window.WebLinksAddon === 'object') {
      return Promise.resolve(true);
    }
    if (window.__ogXtermLoadPromise) {
      return window.__ogXtermLoadPromise;
    }
    const version = (window.OG_VERSION || Date.now());
    const cssHref = '/node_modules/@xterm/xterm/css/xterm.css?v=' + version;
    const scripts = [
      '/node_modules/@xterm/xterm/lib/xterm.js?v=' + version,
      '/node_modules/@xterm/addon-fit/lib/addon-fit.js?v=' + version,
      '/node_modules/@xterm/addon-web-links/lib/addon-web-links.js?v=' + version,
    ];

    function _loadScript(src) {
      return new Promise((resolve, reject) => {
        // Already injected for some reason? Resolve immediately.
        const found = document.querySelector('script[data-og-xterm-src="' + src + '"]');
        if (found) {
          resolve(true);
          return;
        }
        const s = document.createElement('script');
        s.src = src;
        s.async = false; // ordering matters — addons depend on xterm.js
        s.setAttribute('data-og-xterm-src', src);
        s.onload = () => resolve(true);
        s.onerror = () => reject(new Error('Failed to load: ' + src));
        document.head.appendChild(s);
      });
    }

    function _loadCss(href) {
      if (document.querySelector('link[data-og-xterm-css]')) {
        return;
      }
      const l = document.createElement('link');
      l.rel = 'stylesheet';
      l.href = href;
      l.setAttribute('data-og-xterm-css', '1');
      document.head.appendChild(l);
    }

    _loadCss(cssHref);
    window.__ogXtermLoadPromise = (async () => {
      try {
        // Sequential load: xterm.js MUST finish before its addons
        // register (they reference window.Terminal at parse time).
        for (const src of scripts) {
          await _loadScript(src);
        }
        return typeof window.Terminal === 'function';
      } catch (_e) {
        return false;
      }
    })();
    return window.__ogXtermLoadPromise;
  },

  openHostTerminal(host) {
    if (!host || !host.id) {
      return;
    }
    // Admin-only is also enforced server-side; this UI gate just
    // avoids "click button → 4403" friction.
    if (!this.me || this.me.role !== 'admin') {
      this.showToast(this.t('hosts_extra_ssh.terminal.not_admin'), 'error');
      return;
    }
    // Tear down any existing session before opening a new one — prevents
    // resource leaks if the operator switches hosts mid-session.
    this._teardownTerminalSession();
    this.terminalHost = host;
    this.terminalResolved = null;
    this.terminalCloseReason = '';
    this.terminalState = 'connecting';
    this.terminalModalOpen = true;
    // Lazy-load xterm + addons on first use so the steady-state shell
    // never registers xterm's non-passive wheel handlers. Subsequent
    // opens resolve immediately via the memoized promise.
    this._loadXtermDeps().then((ok) => {
      if (!ok) {
        // xterm.js failed to load (CSP block, 404, blocked by an
        // adblocker filter, etc.)
        this.terminalState = 'error';
        this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.xterm_missing') || 'xterm.js failed to load';
        this.showToast(this.terminalCloseReason, 'error');
        return;
      }
      // $nextTick so x-ref="terminalHost" exists in the DOM.
      this.$nextTick(() => {
        try {
          this._spawnTerminal(host);
        } catch (e) {
          this.terminalState = 'error';
          this.terminalCloseReason = (e && e.message) || String(e);
        }
      });
    });
  },
  _spawnTerminal(host) {
    const container = this.$refs.terminalHost;
    if (!container) {
      return;
    }
    // ---- 1) xterm.js setup ----
    const term = new window.Terminal({
      fontFamily: 'Menlo, Consolas, "DejaVu Sans Mono", monospace',
      fontSize: 13,
      cursorBlink: true,
      scrollback: 5000,
      // Background pulled from a CSS token so it tracks the theme.
      // xterm.js renders directly to canvas / DOM so we have to read
      // the resolved colour at instantiation time.
      theme: this._terminalTheme(),
      // Convert paste to bracketed paste so the shell knows what's
      // happening; remote programs that don't grok it still work.
      macOptionIsMeta: true,
    });
    const fit = (typeof window.FitAddon === 'function')
      ? new window.FitAddon()
      : null;
    const wlAddon = (typeof window.WebLinksAddon === 'function')
      ? new window.WebLinksAddon()
      : null;
    if (fit) {
      term.loadAddon(fit);
    }
    if (wlAddon) {
      term.loadAddon(wlAddon);
    }
    term.open(container);
    // ---- Resize-to-container helper. ----
    // The earlier "staircase of fit()" approach kept failing because
    // xterm's FitAddon.proposeDimensions() returns `undefined` when
    // the parent's computed style isn't a clean px value (which can
    // happen briefly with `flex: 1 1 auto; min-height: 0`), and
    // fit.fit() silently no-ops on undefined. Result: cols stuck at
    // the default 80 and the shell wraps mid-line forever. The fix:
    // try FitAddon first (it's the most accurate when it works),
    // then if `term.cols` is still the default 80 after a short
    // wait, MANUALLY measure the container and call term.resize()
    // directly. xterm's onResize handler pipes the new size down
    // the WS so the backend PTY follows.
    //
    // Manual cell metrics for the configured Menlo / Consolas /
    // DejaVu Mono 13px font: ~7.85px wide × ~17.5px tall (Chromium
    // / WebKit / Firefox all within ±0.2px). Slight over-estimation
    // is fine — xterm tolerates one-cell rounding error and the
    // first WS resize callback will correct it.
    const _MANUAL_CELL_W = 7.85;
    const _MANUAL_CELL_H = 17.5;
    // Reserve room for xterm's vertical scrollbar so the rightmost
    // glyph cell never tucks behind the scrollbar gutter when the
    // pty has filled past the visible rows. 18px is generous enough
    // to cover all default-styled scrollbars (Chromium ~15px,
    // Firefox ~12px, Safari ~14px) without making the gap visible.
    const _MANUAL_SCROLLBAR_RESERVE = 18;
    const measureAndResize = () => {
      if (!term || !container || !container.isConnected) {
        return false;
      }
      // Manual measurement path (canonical). FitAddon was the
      // original approach but its `proposeDimensions()` silently
      // returns `undefined` on flex children with `min-height: 0`,
      // AND when it does work it doesn't reserve room for xterm's
      // own vertical scrollbar — so the rightmost glyph tucks
      // behind the scrollbar gutter. Manual measurement reads the
      // container's bounding rect, subtracts CSS padding + the
      // scrollbar reserve, and divides by known cell metrics for
      // the configured 13px monospace font. Idempotent: only calls
      // term.resize() when the computed cols/rows actually differ
      // from the current value, so ResizeObserver re-fires don't
      // ratchet the size down.
      const rect = container.getBoundingClientRect();
      if (rect.width < 100 || rect.height < 50) {
        return false;
      }
      const cs = window.getComputedStyle(container);
      const padX = (parseFloat(cs.paddingLeft) || 0)
        + (parseFloat(cs.paddingRight) || 0);
      const padY = (parseFloat(cs.paddingTop) || 0)
        + (parseFloat(cs.paddingBottom) || 0);
      const usableW = rect.width - padX - _MANUAL_SCROLLBAR_RESERVE;
      const usableH = rect.height - padY;
      const cols = Math.max(20, Math.floor(usableW / _MANUAL_CELL_W));
      const rows = Math.max(5, Math.floor(usableH / _MANUAL_CELL_H));
      if (cols !== term.cols || rows !== term.rows) {
        try {
          term.resize(cols, rows);
        } catch (_) {
        }
      }
      return true;
    };
    // Run the helper on a staircase + ResizeObserver. First successful
    // measurement halts the polling. The 50/250/600/1200ms retries
    // cover Alpine micro-batch, .fade-in animation completion, and a
    // long-tail safety net for slow first-paint.
    requestAnimationFrame(() => requestAnimationFrame(measureAndResize));
    const fitTimers = [
      setTimeout(measureAndResize, 50),
      setTimeout(measureAndResize, 250),
      setTimeout(measureAndResize, 600),
      setTimeout(measureAndResize, 1200),
    ];
    this.terminalFitTimers = fitTimers;
    this.terminal = term;
    this.terminalFit = fit;

    // ---- 2) WebSocket to /api/hosts/{id}/ssh/terminal ----
    const proto = (location.protocol === 'https:') ? 'wss://' : 'ws://';
    const cols = term.cols || 80;
    const rows = term.rows || 24;
    const url = proto + location.host
      + '/api/hosts/' + encodeURIComponent(host.id) + '/ssh/terminal'
      + '?cols=' + cols + '&rows=' + rows;
    let ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      this.terminalState = 'error';
      this.terminalCloseReason = (e && e.message) || String(e);
      return;
    }
    ws.binaryType = 'arraybuffer';
    this.terminalSocket = ws;

    ws.addEventListener('open', () => {
      // Send an explicit resize so the backend's PTY matches the
      // viewport (the query-string size is just a first-frame hint).
      try {
        ws.send(JSON.stringify({type: 'resize', cols: term.cols, rows: term.rows}));
      } catch (_) {
      }
    });
    ws.addEventListener('message', (ev) => {
      if (typeof ev.data === 'string') {
        // Control frame — JSON.
        let ctl;
        try {
          ctl = JSON.parse(ev.data);
        } catch (_) {
          return;
        }
        if (!ctl || typeof ctl !== 'object') {
          return;
        }
        if (ctl.type === 'ready') {
          this.terminalState = 'connected';
          this.terminalResolved = ctl.resolved || null;
          // Resize on the 'ready' control frame — the modal has been
          // visible for at least one round-trip by now, so the
          // .terminal-host's box is definitely committed. The helper
          // tries FitAddon first and falls back to a manual
          // getBoundingClientRect() measurement if fit silently
          // no-ops; either way the new cols/rows pipe down the WS
          // via xterm's onResize handler.
          try {
            measureAndResize();
          } catch (_) {
          }
          term.focus();
        } else if (ctl.type === 'error') {
          this.terminalState = 'error';
          this.terminalCloseReason = ctl.message || ctl.code || 'error';
        } else if (ctl.type === 'exit') {
          this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.exit_code', {
            code: (ctl.code == null ? '?' : ctl.code),
          });
        }
        // 'keepalive' frames — ignored; their job is done at the proxy layer.
      } else if (ev.data instanceof ArrayBuffer) {
        // Binary frame — raw shell output.
        const bytes = new Uint8Array(ev.data);
        term.write(bytes);
      }
    });
    ws.addEventListener('close', (ev) => {
      if (this.terminalState === 'connecting') {
        this.terminalState = 'error';
      } else if (this.terminalState !== 'error') {
        this.terminalState = 'disconnected';
      }
      // map close codes to user-friendly i18n
      // reasons. 4400-4403 each have distinct backend-side meanings;
      // surfacing the close-reason string via specific keys lets
      // operators behind a misconfigured NPM see "X-Forwarded-Host
      // mismatch" instead of just "connection closed".
      const reasonText = (ev.reason || '').toLowerCase();
      if (ev.code === 4400) {
        this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.bad_request');
      } else if (ev.code === 4401) {
        this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.cookie_expired');
      } else if (ev.code === 4403) {
        // 4403 covers both "origin mismatch" and "not admin".
        // The reason string distinguishes them so the operator gets
        // the right diagnostic.
        if (reasonText.includes('origin')) {
          this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.origin_mismatch');
        } else {
          this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.not_admin');
        }
      } else if (ev.code === 4402) {
        this.terminalCloseReason = this.t('hosts_extra_ssh.terminal.csrf_failed');
      } else if (!this.terminalCloseReason && ev.reason) {
        this.terminalCloseReason = String(ev.reason);
      }
    });
    ws.addEventListener('error', () => {
      if (this.terminalState !== 'error' && this.terminalState !== 'disconnected') {
        this.terminalState = 'error';
      }
    });

    // ---- 3) Keystrokes -> WS (binary frames). xterm emits utf-8
    //       strings via onData; encode + send. ----
    const enc = new TextEncoder();
    term.onData((data) => {
      if (!ws || ws.readyState !== 1) {
        return;
      }
      try {
        ws.send(enc.encode(data));
      } catch (_) {
      }
    });
    term.onResize(({cols, rows}) => {
      if (!ws || ws.readyState !== 1) {
        return;
      }
      try {
        ws.send(JSON.stringify({type: 'resize', cols, rows}));
      } catch (_) {
      }
    });

    // ---- 4) Window-resize -> measure-and-resize -> WS resize ----
    const onWinResize = () => {
      try {
        measureAndResize();
      } catch (_) {
      }
    };
    window.addEventListener('resize', onWinResize);
    this.terminalResizeBound = onWinResize;
    // ---- 5) ResizeObserver on the host element ----
    // Catches dimension changes the window-resize listener misses:
    // the initial modal open (when the deferred rAF retries might
    // still race against in-flight CSS transitions), font-size
    // changes, sidebar / drawer toggles, etc.
    if (typeof window.ResizeObserver === 'function') {
      const obs = new window.ResizeObserver(() => {
        try {
          measureAndResize();
        } catch (_) {
        }
      });
      try {
        obs.observe(container);
      } catch (_) {
      }
      this.terminalResizeObs = obs;
    }
  },
  _terminalTheme() {
    // Read tokens from the live theme so light vs dark match. No
    // hex fallback — both --terminal-bg and --terminal-fg are
    // declared in BOTH :root blocks; an empty value here would be
    // a token-definition bug we want to see immediately rather
    // than silently mask with a literal that diverges from theme.
    const cs = getComputedStyle(document.documentElement);
    const bg = cs.getPropertyValue('--terminal-bg').trim();
    const fg = cs.getPropertyValue('--terminal-fg').trim();
    return {background: bg, foreground: fg};
  },
  _teardownTerminalSession() {
    // Idempotent — safe to call from multiple paths (close button,
    // Esc key, modal-backdrop click, openHostTerminal switching hosts,
    // beforeunload).
    if (this.terminalResizeBound) {
      try {
        window.removeEventListener('resize', this.terminalResizeBound);
      } catch (_) {
      }
      this.terminalResizeBound = null;
    }
    if (this.terminalResizeObs) {
      try {
        this.terminalResizeObs.disconnect();
      } catch (_) {
      }
      this.terminalResizeObs = null;
    }
    if (this.terminalFitTimers) {
      for (const id of this.terminalFitTimers) {
        try {
          clearTimeout(id);
        } catch (_) {
        }
      }
      this.terminalFitTimers = null;
    }
    if (this.terminalSocket) {
      try {
        this.terminalSocket.close(1000, 'client closed');
      } catch (_) {
      }
      this.terminalSocket = null;
    }
    if (this.terminal) {
      try {
        this.terminal.dispose();
      } catch (_) {
      }
      this.terminal = null;
    }
    this.terminalFit = null;
  },
  closeHostTerminal() {
    this._teardownTerminalSession();
    this.terminalModalOpen = false;
    this.terminalHost = null;
    this.terminalResolved = null;
    this.terminalState = 'connecting';
    this.terminalCloseReason = '';
  },
  reconnectHostTerminal() {
    const host = this.terminalHost;
    if (!host) {
      return;
    }
    // Re-spawn against the same host. _teardownTerminalSession in
    // openHostTerminal handles the cleanup of the previous session.
    this.openHostTerminal(host);
  },
  // Admin → SSH "Test on a host" widget — picks a curated host,
  // runs /ssh/test (whoami), surfaces the result inline.
  async runSshAdminTest() {
    const hostId = (this.sshTestOnHost.host_id || '').trim();
    if (!hostId) {
      this.showToast(this.t('toasts_extra.ssh.command_required'), 'error');
      return;
    }
    this.sshTestOnHost = {...this.sshTestOnHost, pending: true, result: null};
    try {
      const r = await fetch('/api/hosts/' + encodeURIComponent(hostId) + '/ssh/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: '{}',
      });
      const j = await r.json().catch(() => ({}));
      this.sshTestOnHost = {...this.sshTestOnHost, pending: false, result: j};
    } catch (e) {
      this.sshTestOnHost = {
        ...this.sshTestOnHost, pending: false,
        result: {ok: false, error: e.message},
      };
    }
  },
  // SSH section's own tunables — `saveSshSettings` includes these
  // in its `/api/settings` POST body so the SSH Save button commits
  // them along with the rest of the SSH config (master toggle,
  // default user / port / FQDN suffix, known-hosts, destructive
  // patterns, custom actions). Adding a new SSH tunable: add it here
  // AND to `relocatedTuningKeys` AND to the SettingsIn backend model.
  _sshSectionTuningKeys() {
    return [
      // WS heartbeat cadence (server ping interval keeping the
      // terminal websocket alive past upstream-proxy idle timers).
      'tuning_ssh_ws_heartbeat_seconds',
      // Terminal connect + login timeouts (TCP / auth handshake caps).
      'tuning_ssh_terminal_connect_timeout_seconds',
      'tuning_ssh_terminal_login_timeout_seconds',
      // Connection-close wait timeout — caps how long
      // `conn.wait_closed()` blocks after a terminal session ends.
      // Per-use read inside `ws_ssh_terminal` so a Save here takes
      // effect on the next session teardown without restart.
      'tuning_ssh_close_timeout_seconds',
    ];
  },
};
