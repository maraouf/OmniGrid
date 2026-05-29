// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ContinueStatementJS,BreakStatementJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML,JSAsyncFunctionMissingAwait
// noinspection JSMissingAwait,JSUnfilteredForInLoop,IfStatementWithoutBlockJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSPotentiallyInvalidUsageOfThis,JSIgnoredPromiseFromCall,AssignmentToFunctionParameterJS,InnerHTMLJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA minor tools — port scan trigger, retag popover, task-error
// auto-fix actions.

export default {
  // In-flight port-scan tracker — global keyed by host_id so the
  // Scan-ports button can disable itself across the whole app
  // (drawer / list row / AI palette dispatch) regardless of which
  // host reference triggered the scan. Pre-fix this was created
  // lazily inside `runPortScan` (`this._inFlightPortScans = this._inFlightPortScans || {}`)
  // which made it non-reactive to Alpine — the `:disabled` binding
  // never picked up the change. Declared at top level so Alpine's
  // Proxy wraps it from the start. Cleared by the SSE
  // `port_scan:completed` handler + 10 min hard timeout fallback.
  _inFlightPortScans: {},
  // History-row detail renderer for `op_type='port_scan'`. The
  // backend writes a JSON OBJECT in `events` carrying the scan
  // summary `{scan_id, target, ports_scanned, ports_open,
  // scan_duration_ms}`; this renderer surfaces it as a SweetAlert
  // popup with the meta block + a fetch of the actual open-port
  // list (per scan_id) so the operator can see WHICH ports were
  // open without bouncing to the host drawer.
  async _openPortScanHistoryDetail(h) {
    let payload = {};
    try {
      payload = JSON.parse(h.events || '{}') || {};
    } catch (_) {
    }
    const esc = (s) => String(s ?? '').replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]));
    const fmtNum = (n) => Number.isFinite(+n) ? (+n).toLocaleString() : String(n || 0);
    const scanId = (payload.scan_id || '').toString();
    const target = (payload.target || h.target_id || h.target_name || '—').toString();
    const portsScanned = Number(payload.ports_scanned) || 0;
    const portsOpen = Number(payload.ports_open) || 0;
    const durationMs = Number(payload.scan_duration_ms) || Math.round((h.duration || 0) * 1000);
    const meta = '<div class="swal-meta mono">'
      + '<div><b>' + esc(this.t('history.detail.when')) + '</b> ' + esc(this.formatTime(h.ts)) + '</div>'
      + '<div><b>' + esc(this.t('history.detail.op')) + '</b> ' + esc(h.op_type) + '</div>'
      + '<div><b>' + esc(this.t('history.detail.target')) + '</b> ' + esc(h.target_id || '—') + '</div>'
      + '<div><b>' + esc(this.t('history.port_scan.target_label') || 'Resolved target:') + '</b> ' + esc(target) + '</div>'
      + '<div><b>' + esc(this.t('history.port_scan.scan_id_label') || 'Scan ID:') + '</b> ' + esc(scanId || '—') + '</div>'
      + '<div><b>' + esc(this.t('history.port_scan.ports_scanned_label') || 'Ports scanned:') + '</b> ' + fmtNum(portsScanned) + '</div>'
      + '<div><b>' + esc(this.t('history.port_scan.ports_open_label') || 'Ports open:') + '</b> ' + fmtNum(portsOpen) + '</div>'
      + '<div><b>' + esc(this.t('history.detail.duration')) + '</b> ' + (durationMs / 1000).toFixed(2) + 's</div>'
      + '<div><b>' + esc(this.t('history.detail.actor')) + '</b> ' + esc(h.actor || 'ui') + '</div>'
      + '<div><b>' + esc(this.t('history.detail.status')) + '</b> ' + esc(h.status) + '</div>'
      + (h.error ? '<div class="swal-err"><b>' + esc(this.t('history.detail.error')) + '</b> ' + esc(h.error) + '</div>' : '')
      + '</div>';
    // Fetch the open-port list for this scan_id so the popup can
    // show the actual ports (chip strip in the host drawer shows
    // only the LATEST scan; this fetch lets the operator see a
    // historical scan's open-port set without time-travel).
    const portsHtml = '<div class="swal-events"><span class="text-[var(--text-faint)]">' +
      esc(this.t('history.port_scan.loading_ports') || 'Loading open ports…') + '</span></div>';
    const containerId = 'port-scan-history-ports-' + (scanId || h.id || 'na');
    Swal.fire({
      title: target + ' — ' + esc(this.t('op_types.port_scan') || 'port scan'),
      html: meta + '<div id="' + containerId + '">' + portsHtml + '</div>',
      width: 720,
      showConfirmButton: false,
      showCloseButton: true,
      background: this._cssVar('--surface'),
      color: this._cssVar('--text'),
    });
    // Fan-out fetch — fire-and-forget so the popup paints
    // immediately. New endpoint /api/history/port-scan/{scan_id}/ports
    // returns the rows from host_port_scans for that scan_id.
    try {
      const r = await fetch('/api/history/port-scan/' + encodeURIComponent(scanId) + '/ports');
      const target_el = document.getElementById(containerId);
      if (!target_el) {
        return;
      }
      if (!r.ok) {
        target_el.innerHTML = '<div class="swal-err">' + esc(this.t('history.port_scan.load_failed') || 'Could not load open ports for this scan.') + '</div>';
        return;
      }
      const j = await r.json();
      const ports = Array.isArray(j.ports) ? j.ports : [];
      if (!ports.length) {
        target_el.innerHTML = '<div class="swal-events"><span class="text-[var(--text-faint)]">' +
          esc(this.t('history.port_scan.no_open') || 'No open ports recorded for this scan.') + '</span></div>';
        return;
      }
      const rows = ports.map(p => {
        const port = p.port || '';
        const proto = (p.protocol || 'tcp').toLowerCase();
        const hint = p.service_hint || '';
        const banner = p.banner_excerpt || '';
        const portLabel = port + '/' + proto;
        return '<div class="swal-ev swal-ev-ok"><span class="swal-ev-ts mono">' + esc(portLabel) + '</span>'
          + '<span class="swal-ev-msg">' + esc(hint) + (banner ? ' — <span class="text-[var(--text-faint)]">' + esc(banner.slice(0, 80)) + '</span>' : '') + '</span></div>';
      }).join('');
      target_el.innerHTML = '<div class="swal-events">' + rows + '</div>';
    } catch (e) {
      const el = document.getElementById(containerId);
      if (el) {
        el.innerHTML = '<div class="swal-err">' + esc(String(e)) + '</div>';
      }
    }
  },

  // Auto-fix actions surfaced in the known-issue panel below the
  // remediation prose. Each action descriptor: { id, label, kind,
  // help, danger? }. `kind` drives the dispatcher in
  // `runTaskErrorAutoFix`:
  //   - 'restart_service' → POST /api/restart/service/{id}; calls
  //     the existing Swarm-friendly force-update path. Lowest blast
  //     radius — sometimes clears transient kernel state without
  //     touching the network or daemon.
  //   - 'ssh_fix_node' → opens the SSH terminal modal targeted at
  //     the failing node (resolved from `task_history[0].node`)
  //     so the user can run the surgical fix (`ip link delete
  //     vx-...` or `systemctl restart docker`) under the existing
  //     destructive-confirm gate. Falls back to a toast when no
  //     curated host matches the node hostname.
  //
  // Returns [] when the error doesn't match a known pattern OR
  // when the user is read-only (the markup also gates on
  // `isAdmin()`, but defence-in-depth here keeps a future caller
  // honest).
  taskErrorAutoFixActions(item) {
    if (!item || !item.task_error) {
      return [];
    }
    if (!this.isAdmin || typeof this.isAdmin !== 'function' || !this.isAdmin()) {
      return [];
    }
    const err = String(item.task_error);
    const out = [];
    // VXLAN sandbox-join — three-tier fix progression:
    //   (1) force-restart (lowest blast radius — sometimes the
    //       kernel clears the stale interface between attempts).
    //   (2) cleanup_overlay_network via Portainer API — finds the
    //       overlay network matching the failing subnet, verifies
    //       no other containers are using it, and removes it.
    //       Docker recreates the network + vxlan with a fresh id
    //       when the service is force-updated immediately after.
    //       SSH-free; safe when the network is single-stack
    //       (`nebula-sync_default`-style "one stack one network"
    //       compose deploys). Surfaced ABOVE the SSH escalation
    //       so the operator sees "try this first" ordering.
    //   (3) ssh_fix_node — kernel-level `ip link delete` for the
    //       orphan-vxlan case where Docker no longer owns the
    //       interface. Only path that handles a truly orphaned
    //       vxlan (vs a network-tracked one).
    if (/network sandbox join failed|subnet sandbox join failed|error creating vxlan interface|file exists/i.test(err)
      && item.type === 'service' && item.raw_id) {
      out.push({
        id: 'force-restart-service',
        label: this.t('drawer.task_error_action_force_restart')
          || 'Force-restart service',
        kind: 'restart_service',
        help: this.t('drawer.task_error_action_force_restart_help')
          || 'Issues a force-update on this service. Sometimes the kernel cleans up the stale VXLAN interface between task attempts; safe first try.',
        danger: false,
      });
      // Parse the failing subnet from the error so the cleanup
      // action can target the right overlay network. Match
      // `for "10.90.24.0/24"` (with quotes) OR the bare CIDR.
      const subnetMatch = err.match(/\d+\.\d+\.\d+\.\d+\/\d+/);
      const failingSubnet = subnetMatch ? subnetMatch[0] : '';
      if (failingSubnet) {
        out.push({
          id: 'cleanup-overlay-network',
          label: this.t('drawer.task_error_action_cleanup_overlay', {subnet: failingSubnet})
            || ('Cleanup stale overlay network (' + failingSubnet + ')'),
          kind: 'cleanup_overlay_network',
          subnet: failingSubnet,
          service_id: item.raw_id,
          help: this.t('drawer.task_error_action_cleanup_overlay_help')
            || 'Uses the Portainer API to find the overlay network matching the failing subnet, verifies no other containers are using it, and removes it. Docker recreates the network + a fresh VXLAN interface when the service is force-updated immediately after. No SSH required. **Precondition:** the network must NOT be actively referenced by any service — Swarm refuses `network rm` while a service spec still names it. If your service is the ONLY consumer, try Force-restart first (rotates the task off the stale VXLAN, sometimes enough on its own); if that fails AND the network is now orphan-ish, this button finishes the job. For a shared / actively-used network, escalate to the SSH path.',
          danger: true,
        });
      }
      // Find the failing node from the task_history. The most-recent
      // failed task's node is the right SSH target. Skip the SSH
      // button when no node info is available OR when no curated
      // host matches the hostname.
      const failingNode = (Array.isArray(item.task_history) && item.task_history[0])
        ? item.task_history[0].node
        : '';
      if (failingNode && this._findHostByNodeName(failingNode)) {
        out.push({
          id: 'ssh-fix-vxlan',
          label: this.t('drawer.task_error_action_ssh_fix', {node: failingNode})
            || ('Open SSH terminal on ' + failingNode),
          kind: 'ssh_fix_node',
          node: failingNode,
          help: this.t('drawer.task_error_action_ssh_fix_help')
            || 'Opens an SSH terminal on the failing node so you can run `ip -d link show type vxlan` and `sudo ip link delete vx-XXXXXX` to remove the leftover interface, or `sudo systemctl restart docker` as a last resort.',
          danger: true,
        });
      }
    }
      // Image-pull failures — force-restart can sometimes pick up a
      // transient registry hiccup; for sticky failures the user has
    // to fix credentials / tag, which is out of scope for an auto.
    else if (/no such image|manifest unknown|pull access denied|requested access to the resource is denied|toomanyrequests/i.test(err)
      && item.type === 'service' && item.raw_id) {
      out.push({
        id: 'force-restart-service',
        label: this.t('drawer.task_error_action_force_restart')
          || 'Force-restart service',
        kind: 'restart_service',
        help: 'Retry the pull. Useful for transient registry hiccups; sticky pull-failures need a credential / tag fix.',
        danger: false,
      });
    }
    return out;
  },

  // Dispatcher for the known-issue panel's auto-fix buttons. Each
  // kind maps to a specific click path; failures surface as a
  // toast. `_auto_fix_running` flag on the item drives the
  // button's spinner / disabled state so the user can't double-click.
  async runTaskErrorAutoFix(item, action) {
    if (!item || !action || !action.kind) {
      return;
    }
    if (item._auto_fix_running) {
      return;
    }
    // Destructive actions confirm via SweetAlert before firing.
    if (action.danger) {
      const ok = await this.confirmDialog({
        title: this.t('drawer.task_error_confirm_title') || 'Run this fix?',
        html: action.help || '',
        confirmText: this.t('actions.continue') || 'Continue',
        cancelText: this.t('actions.cancel') || 'Cancel',
        icon: 'warning',
      });
      if (!ok) {
        return;
      }
    }
    item._auto_fix_running = true;
    try {
      if (action.kind === 'restart_service') {
        const r = await fetch('/api/restart/service/' + encodeURIComponent(item.raw_id), {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: '{}',
        });
        if (!r.ok) {
          const txt = await r.text().catch(() => '');
          const friendly = (typeof this._friendlyHttpError === 'function')
            ? this._friendlyHttpError(r.status, txt, 'drawer')
            : ('HTTP ' + r.status);
          this.showToast(this.t('drawer.task_error_restart_failed', {error: friendly})
            || ('Restart failed: ' + friendly), 'error');
          return;
        }
        this.showToast(this.t('drawer.task_error_restart_queued')
          || 'Force-restart queued. Watch the row for the new task to come up; the error should clear automatically when it does.', 'success');
        // Refresh items so the operation banner appears + the
        // task_error / task_history fields reflect the new attempt.
        if (typeof this.refresh === 'function') {
          this.refresh();
        }
      } else if (action.kind === 'ssh_fix_node') {
        const host = this._findHostByNodeName(action.node || '');
        if (!host) {
          this.showToast(this.t('drawer.task_error_ssh_no_host', {node: action.node})
            || ('No curated host matches "' + action.node + '" — add it under Admin → Hosts to enable the SSH-fix button.'), 'error');
          return;
        }
        // Close the item drawer so the terminal modal isn't
        // stacked on top of it; the user comes back to the drawer
        // automatically when they close the terminal.
        this.drawerItem = null;
        this.openHostTerminal(host);
      } else if (action.kind === 'cleanup_overlay_network') {
        // Portainer-API-only path: backend resolves the network
        // matching the subnet, verifies it's safe to remove, then
        // removes + force-updates the affected service.
        const r = await fetch('/api/cleanup-overlay-network', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            subnet: action.subnet,
            service_id: action.service_id,
          }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.ok) {
          const friendly = (j && j.detail) || ((typeof this._friendlyHttpError === 'function')
            ? this._friendlyHttpError(r.status, '', 'drawer')
            : ('HTTP ' + r.status));
          this.showToast(this.t('drawer.task_error_cleanup_overlay_failed', {error: friendly})
            || ('Cleanup failed: ' + friendly), 'error');
          return;
        }
        this.showToast(this.t('drawer.task_error_cleanup_overlay_done', {network: j.network_name || ''})
          || ('Removed overlay ' + (j.network_name || '') + ' and force-updated the service. Watch for the new task to come up.'), 'success');
        if (typeof this.refresh === 'function') {
          this.refresh();
        }
      }
    } catch (e) {
      const msg = (e && e.message) ? e.message : String(e);
      this.showToast(this.t('drawer.task_error_dispatch_failed', {error: msg})
        || ('Auto-fix failed: ' + msg), 'error');
    } finally {
      item._auto_fix_running = false;
    }
  },

  // Match a Swarm task-error string against known patterns and
  // return localised remediation guidance (HTML-escaped). Returns
  // empty string when the error doesn't match a known pattern —
  // the drawer's known-issue panel hides itself when this returns
  // empty. Add new patterns by adding a `[regex, i18nKey]` pair
  // to the matcher table; the body text lives in the i18n bundle
  // so it can be translated and edited without touching JS.
  taskErrorKnownIssue(errText) {
    if (!errText || typeof errText !== 'string') {
      return '';
    }
    // Patterns ordered most-specific first. Each entry's i18n key
    // points at a body string under `drawer.task_error_known_issue_*`
    // in en.json. The body is rendered via `x-html` so simple
    // inline markup (<code>, <kbd>, <strong>) renders cleanly —
    // values come from the trusted i18n bundle so no XSS surface.
    const matchers = [
      // Docker Swarm overlay-network VXLAN bug — kernel keeps the
      // vx-NNNNNN-XXXX interface from a previous task and the new
      // task can't recreate it. Fix: rm + recreate the network on
      // the affected node, or restart Docker on that node.
      [/network sandbox join failed|subnet sandbox join failed|error creating vxlan interface|file exists/i,
        'task_error_known_issue_vxlan'],
      // Image pull failure — registry auth, network, missing tag.
      [/no such image|manifest unknown|pull access denied|requested access to the resource is denied|toomanyrequests/i,
        'task_error_known_issue_image_pull'],
      // Mount errors — bind path missing on the node, NFS down,
      // permission denied on the mount target.
      [/invalid mount config|mount path .* does not exist|failed to create new os mount|permission denied .* mount/i,
        'task_error_known_issue_mount'],
      // Placement constraint mismatch — service constraints can't
      // be satisfied (no eligible node).
      [/no suitable node|no nodes available that match all .* constraints/i,
        'task_error_known_issue_placement'],
      // Resource limits — node out of memory / CPU / disk.
      [/insufficient resources|no resources available|no node has enough memory/i,
        'task_error_known_issue_resources'],
    ];
    for (const [rx, key] of matchers) {
      if (rx.test(errText)) {
        const body = this.t('drawer.' + key);
        // i18n returns the key itself when missing — treat that
        // as no-blurb so the panel collapses cleanly rather than
        // showing the raw key string.
        if (body && body !== 'drawer.' + key) {
          return body;
        }
      }
    }
    return '';
  },

  // POST /api/hosts/{id}/port-scan — runs an on-demand TCP-connect
  // scan against the host. Stamps `_port_scan_running` while the
  // call is in flight so the button spinner ticks; refreshes the
  // row on completion so `host.detected_ports` repaints. Errors
  // surface via toast — no second confirmation since the scan is
  // read-only against the target.
  async runPortScan(host) {
    // Fallback chain — when no host arg is provided, resolve
    // from (in order): the open host drawer → the most-recent
    // AI assistant turn's `host_ids[0]` → toast asking the
    // operator to specify. So the AI palette can fire
    // `ACTION: scan_ports` after producing a HOSTS: line and
    // the SPA picks the right host without the drawer being
    // open. The drawer is no longer a hard prerequisite.
    if (!host || !host.id) {
      host = this.drawerHost || null;
    }
    if (!host || !host.id) {
      // Walk aiConversation backwards looking for the most-recent
      // assistant turn that named a host via the HOSTS protocol
      // (`turn.host_ids`). First id wins — the AI typically lists
      // the most-relevant host first.
      const turns = Array.isArray(this.aiConversation) ? this.aiConversation : [];
      for (let i = turns.length - 1; i >= 0; i--) {
        const t = turns[i];
        if (t && t.role === 'assistant' && Array.isArray(t.host_ids) && t.host_ids.length) {
          const hid = String(t.host_ids[0]);
          const found = (this.hosts || []).find(h => h && h.id === hid);
          if (found) {
            host = found;
            break;
          }
          // No match in `this.hosts` — synthesize a minimal host
          // shape so the POST still fires (refreshHostRow will
          // fail gracefully if the id isn't curated).
          host = {id: hid};
          break;
        }
      }
    }
    if (!host || !host.id) {
      this.showToast(
        this.t('host_drawer.port_scan.no_target_toast') ||
        'No host selected — open a host drawer or ask the AI to name one before scanning.',
        'error',
      );
      return;
    }
    // Guard against double-queueing the SAME host. Two checks:
    // (a) the host-row's `_port_scan_running` flag (drives the
    // button :disabled + spinner), and (b) the global
    // `_inFlightPortScans` map (covers AI-palette / cross-tab
    // dispatches where the host arg may be a synthesised stub
    // rather than the live row reference). Either being set
    // means a scan is already running and we must short-circuit.
    // `_inFlightPortScans` is now declared as top-level reactive
    // state (see the field declaration); no lazy-init needed.
    if (host._port_scan_running || this._inFlightPortScans[host.id]) {
      this.showToast(this.t('host_drawer.port_scan.scan_already_running', {host: host.id})
        || ('A scan is already running for ' + host.id + '.'), 'info');
      return;
    }
    host._port_scan_running = true;
    let queued = false;  // tracks whether the request returned 202
    try {
      const r = await fetch('/api/hosts/' + encodeURIComponent(host.id) + '/port-scan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: '{}',
      });
      if (!r.ok) {
        const txt = await r.text().catch(() => '');
        const friendly = this._friendlyHttpError(r.status, txt, 'host_drawer.port_scan');
        this.showToast(this.t('host_drawer.port_scan.scan_failed_body', {host: host.id, error: friendly}), 'error');
        // 504 from the reverse proxy — the backend scan may STILL be
        // running. Schedule a single delayed refresh so a successful
        // background completion still updates the chip strip when
        // the operator has the drawer open. Best-effort; no spinner
        // since `_port_scan_running` already cleared.
        if (r.status === 504 || r.status === 502 || r.status === 503) {
          setTimeout(() => {
            try {
              this.refreshHostRow(host.id, {force: true});
            } catch (_) {
            }
          }, 30000);
        }
        return;
      }
      // 202 Accepted — scan is running in the background. The
      // backend emits `port_scan:completed` via SSE when done; the
      // SPA handler refreshes the host row + shows a completion
      // toast AND clears `_port_scan_running` so the button
      // re-enables. We surface a "queued" toast immediately and
      // LEAVE the spinner running until SSE (or the fallback)
      // tells us the scan is done.
      const data = await r.json().catch(() => ({}));
      if (data && data.status === 'queued') {
        queued = true;
        // Track the in-flight scan_id so the SSE handler knows
        // this tab kicked it off (and can show a completion toast
        // even when the drawer has since closed).
        this._inFlightPortScans[host.id] = data.scan_id || true;
        // ALWAYS surface the resolved scan target so the operator
        // can validate the actual address being probed BEFORE
        // results land. The parenthetical is non-negotiable per
        // user-flagged requirement: "I need the actual scan target
        // in notification — whether host DNS record or IP, whatever
        // the probe will run on". Even when target == label the
        // parenthetical confirms the address explicitly, removing
        // any ambiguity about which path the resolver picked
        // (host_id / ssh.fqdn / ssh.host / ping.host).
        const _label = this.hostDisplayName(host) || host.id;
        const _target = (data.target || host.id || '').toString();
        this.showToast(
          this.t('host_drawer.port_scan.scan_queued_body', {
            host: _label, target: _target,
          }) || ('Port scan queued for ' + _label
            + ' (scanning ' + _target + ') — results will appear when complete.'),
          'info',
        );
        // Polling fallback at 60 s — refreshes the host row in case
        // SSE is dropped. Doesn't clear the spinner yet (SSE may
        // still land); the hard timeout below handles that case.
        setTimeout(() => {
          try {
            if (this._inFlightPortScans && this._inFlightPortScans[host.id]) {
              this.refreshHostRow(host.id, {force: true});
            }
          } catch (_) {
          }
        }, 60000);
        // Hard timeout at 10 min — if neither SSE nor polling has
        // cleared the in-flight marker, force-clear so the button
        // doesn't stay disabled forever. 10 min is generous: even
        // a wide-range scan with banner-grab on a slow link
        // should complete well within that. Tracks the host id
        // explicitly so a CONCURRENT scan (different host) doesn't
        // get caught by this clear.
        setTimeout(() => {
          try {
            if (this._inFlightPortScans && this._inFlightPortScans[host.id]) {
              console.warn('[port_scan] hard-timeout clearing in-flight marker for ' + host.id);
              delete this._inFlightPortScans[host.id];
              const row = (this.hosts || []).find(h => h && h.id === host.id);
              if (row) {
                row._port_scan_running = false;
              }
              if (host) {
                host._port_scan_running = false;
              }
            }
          } catch (_) {
          }
        }, 600000);
        return;
      }
      // Legacy synchronous path — kept for forward compatibility
      // with older backends. Refresh + summary toast as before.
      // Surfaces the actual scan target (from `data.target`) in
      // the toast for the same reason the SSE-driven completion
      // path does: the host id can be a friendly alias that
      // doesn't resolve via DNS (`ftth` etc.), and the operator
      // needs to know what the probe actually hit.
      await this.refreshHostRow(host.id, {force: true});
      const openCount = (data && data.open_count) || 0;
      const newCount = (data && data.new_count) || 0;
      const target = (data && data.target) || host.id;
      this.showToast(this.t('host_drawer.port_scan.scan_complete_body', {host: target, open_count: openCount, new_count: newCount}), 'success');
    } catch (e) {
      // `e` here is typically a TypeError ("Failed to fetch") for
      // network drops or browser-side aborts. Surface a friendly
      // message; raw `e.toString()` shows "TypeError" which isn't
      // operator-actionable.
      const msg = (e && e.message) ? e.message : String(e);
      const friendly = this.t('host_drawer.port_scan.error_network') || msg;
      this.showToast(this.t('host_drawer.port_scan.scan_failed_body', {host: host.id, error: friendly}), 'error');
    } finally {
      // Only clear the spinner when the scan was NOT queued. The
      // 202-Accepted path leaves the marker set; SSE
      // `port_scan:completed` (or the 10 min hard-timeout) does
      // the clear so the button stays disabled / spinning while
      // the actual scan runs. Pre-fix this finally always cleared
      // and the button immediately re-enabled — letting the user
      // queue the same host repeatedly.
      if (!queued) {
        host._port_scan_running = false;
        if (this._inFlightPortScans) {
          delete this._inFlightPortScans[host.id];
        }
      }
    }
  },

  // True when asset inventory is enabled, an asset record exists
  // for this host with at least one port defined, AND the detected
  // port number is NOT in the asset's port-number list. Used to
  // render a small round-exclamation marker on the port chip so
  // the operator can spot scanned ports the asset inventory
  // doesn't know about (either: undocumented service running on
  // the host, or asset record needs updating). Returns false
  // (suppressed) when: asset inventory disabled globally, no asset
  // record for this host, OR the asset record has zero port
  // definitions (nothing to compare against — flagging every
  // port would be noise, not signal). Loose match by port number
  // only — the asset's `protocol` field is informational; treating
  // tcp/udp dual-stack on the same port as a mismatch would
  // false-positive routinely.
  portScanShouldFlag(host, port) {
    if (!host || !port) {
      return false;
    }
    const enabled = !this.settings || this.settings.asset_inventory_enabled !== false;
    if (!enabled) {
      return false;
    }
    if (typeof this.assetForHost !== 'function') {
      return false;
    }
    const asset = this.assetForHost(host);
    if (!asset || !Array.isArray(asset.ports) || asset.ports.length === 0) {
      return false;
    }
    const portNum = Number(port.port);
    if (!Number.isFinite(portNum) || portNum <= 0) {
      return false;
    }
    for (const ap of asset.ports) {
      if (Number(ap && ap.number) === portNum) {
        return false;
      }
    }
    return true;
  },

  // Reverse of portScanShouldFlag: the open ports the port SCAN found
  // that are NOT in this host's asset-inventory port list — so the
  // asset port section can surface them (styled like an unmapped port)
  // and the operator knows which scanned ports to ADD to the asset
  // record. Mirrors portScanShouldFlag's gate exactly (asset inventory
  // enabled + asset has >=1 documented port) so the same noise-
  // avoidance holds: an asset with no documented ports yields nothing
  // (suggesting every open port would be noise, not signal). Returns
  // the detected-port objects (port / protocol / service_hint),
  // deduped by port number, sorted ascending.
  assetScanOnlyPorts(host) {
    if (!host) {
      return [];
    }
    const enabled = !this.settings || this.settings.asset_inventory_enabled !== false;
    if (!enabled) {
      return [];
    }
    if (typeof this.assetForHost !== 'function') {
      return [];
    }
    const asset = this.assetForHost(host);
    if (!asset || !Array.isArray(asset.ports) || asset.ports.length === 0) {
      return [];
    }
    const assetNums = new Set();
    for (const ap of asset.ports) {
      const n = Number(ap && ap.number);
      if (Number.isFinite(n) && n > 0) {
        assetNums.add(n);
      }
    }
    const detected = Array.isArray(host.detected_ports) ? host.detected_ports : [];
    const seen = new Set();
    const out = [];
    for (const p of detected) {
      const n = Number(p && p.port);
      if (!Number.isFinite(n) || n <= 0 || assetNums.has(n) || seen.has(n)) {
        continue;
      }
      seen.add(n);
      out.push(p);
    }
    out.sort((a, b) => Number(a.port) - Number(b.port));
    return out;
  },
  // Inverse of assetScanOnlyPorts at the single-port level: is THIS
  // asset/service port absent from the latest port scan's results? Used
  // to flag a documented port the scan didn't find open — the operator
  // can then check the service OR add the port to the scanner's range.
  // Gated like the other asset/scan helpers: asset inventory enabled
  // AND a scan has actually run for this host (`last_port_scan_ts`),
  // so a host that's simply never been scanned doesn't flag every port
  // (that's not signal — there's nothing to compare against yet).
  assetPortNotScanned(host, portNum) {
    if (!host) {
      return false;
    }
    const enabled = !this.settings || this.settings.asset_inventory_enabled !== false;
    if (!enabled) {
      return false;
    }
    // No scan run yet → nothing to compare; don't flag.
    if (!host.last_port_scan_ts) {
      return false;
    }
    const n = Number(portNum);
    if (!Number.isFinite(n) || n <= 0) {
      return false;
    }
    const detected = Array.isArray(host.detected_ports) ? host.detected_ports : [];
    for (const p of detected) {
      if (Number(p && p.port) === n) {
        return false;
      }
    }
    return true;
  },
  // Chip class for a detected port.
  //
  // Two-axis colour scheme:
  //   TCP curated  → pill-ok       (green — known good)
  //   TCP unknown  → pill-warning  (amber — investigate / curate)
  //   UDP (any)    → pill-info     (blue — distinct fill from TCP)
  //
  // UDP rolls up to ONE colour because the operator's primary
  // request is "make UDP visually distinct from TCP", not "preserve
  // curated/unknown within UDP". The curated vs unknown signal for
  // UDP is carried by the tooltip + the curated chip's
  // `service_hint` label suffix; the asset-mismatch icon (when
  // asset inventory is enabled — see `portScanShouldFlag`)
  // surfaces UDP rows that need attention separately.
  //
  // Curated services don't carry a protocol field today, so a
  // `port: 22` curated row matches BOTH families.
  portScanChipClass(host, port) {
    if (!host || !port) {
      return 'pill-muted';
    }
    const isUdp = ((port.protocol || 'tcp').toLowerCase() === 'udp');
    if (isUdp) {
      return 'pill-info';
    }
    const curated = Array.isArray(host.services) ? host.services : [];
    const match = curated.find(s => Number(s && s.port) === Number(port.port));
    return match ? 'pill-ok' : 'pill-warning';
  },

  // Tooltip for a detected port chip. Appends an "asset mismatch"
  // line when the port isn't in the asset inventory's port list
  // (see `portScanShouldFlag`). Two-line tooltip when both apply
  // — operator gets the curated/unknown context AND the asset-
  // mismatch context in one hover.
  portScanChipTitle(host, port) {
    if (!host || !port) {
      return '';
    }
    const curated = Array.isArray(host.services) ? host.services : [];
    const match = curated.find(s => Number(s && s.port) === Number(port.port));
    const proto = (port.protocol || 'tcp').toLowerCase();
    const portLabel = port.port + '/' + proto;
    let head;
    if (match) {
      head = this.t('host_drawer.port_scan.chip_curated_title', {
        name: match.name || match.label || '—',
        port: portLabel,
      });
    } else {
      head = this.t('host_drawer.port_scan.chip_unknown_title', {port: portLabel});
    }
    if (this.portScanShouldFlag(host, port)) {
      const tail = this.t('host_drawer.port_scan.chip_asset_mismatch_title');
      return head + '\n' + tail;
    }
    return head;
  },

  // Resolve a scanned open port to the configured app (chip) on this
  // host whose port list contains it, so the port-scan section can
  // annotate the chip with the app it belongs to (icon + name). Port
  // sources, in order of authority: the chip's multi-port probe list
  // (`probe.ports[]`), the bound catalog template's `default_ports[]`,
  // and the chip's single top-level `port` (raw services entry, matched
  // by service_idx). Match is by port NUMBER only — tcp/udp dual-stack
  // on the same number is treated as the same app (matches the loose
  // match in portScanChipClass / portScanShouldFlag). Returns
  // {name, icon, service_idx} or null when nothing maps.
  portScanMappedApp(host, port) {
    if (!host || !port) {
      return null;
    }
    const pnum = Number(port.port);
    if (!Number.isFinite(pnum) || pnum <= 0) {
      return null;
    }
    const apps = Array.isArray(host.apps) ? host.apps : [];
    const services = Array.isArray(host.services) ? host.services : [];
    for (const app of apps) {
      const ports = new Set();
      const pb = (app.probe && Array.isArray(app.probe.ports)) ? app.probe.ports : [];
      for (const pp of pb) {
        ports.add(Number(pp && pp.port));
      }
      if (app.catalog && Array.isArray(app.catalog.default_ports)) {
        for (const pp of app.catalog.default_ports) {
          ports.add(Number(pp && pp.port));
        }
      }
      const svc = services[app.service_idx];
      if (svc && Number(svc.port) > 0) {
        ports.add(Number(svc.port));
      }
      if (ports.has(pnum)) {
        const name = app.name || (app.catalog && app.catalog.name)
          || (svc && (svc.name || svc.label)) || '';
        if (!name) {
          continue;
        }
        // Per-port probe verdict for THIS port: prefer the per-port
        // sample row (multi-port chips), else fall back to the chip's
        // overall status for a single-port enabled probe. null = no
        // probe configured / no result yet — the chip falls back to
        // the app's overall status for the single status dot.
        let portStatus = null;
        const prMatch = (app.port_results || []).find((pr) => Number(pr && pr.port) === pnum);
        if (prMatch) {
          // Tri-state via the SAME helper the Apps per-port grid uses, so
          // a pending port (configured but not yet probed, alive===null)
          // renders 'unknown' (grey) in BOTH surfaces. The earlier
          // `alive ? 'up' : 'down'` collapsed null → 'down', painting a
          // never-probed port red here while the Apps grid showed grey.
          portStatus = (typeof this.appsPortState === 'function')
            ? this.appsPortState(prMatch)
            : (prMatch.alive === true ? 'up' : (prMatch.alive === false ? 'down' : 'unknown'));
        } else if ((app.probe || {}).enabled && (app.status === 'up' || app.status === 'down')) {
          portStatus = app.status;
        }
        return {
          name,
          icon: app.icon || (app.catalog && app.catalog.slug) || name,
          service_idx: app.service_idx,
          status: app.status || 'unknown',
          port_status: portStatus,
        };
      }
    }
    // No app chip owns this port — fall back to the standalone
    // http_probe provider's URL list. An http:// URL implies port 80
    // (or its explicit :port), https:// implies 443; when a scanned
    // port matches a probed URL's port, surface that URL + its probe
    // verdict (HTTP status_ok + content_match_ok => up). Lets a host
    // with no app chips but configured http_probe URLs (e.g. an
    // OPNsense box probed at http:// + https://) still annotate its
    // 80 / 443 chips.
    const httpUrls = Array.isArray(host.host_http_urls) ? host.host_http_urls : [];
    for (const u of httpUrls) {
      const urlStr = u && u.url;
      if (!urlStr) {
        continue;
      }
      let parsed;
      try {
        parsed = new URL(urlStr);
      } catch (_) {
        continue;
      }
      const scheme = (parsed.protocol || '').replace(':', '').toLowerCase();
      let uport = parsed.port ? Number(parsed.port) : 0;
      if (!uport) {
        uport = scheme === 'https' ? 443 : (scheme === 'http' ? 80 : 0);
      }
      if (uport !== pnum) {
        continue;
      }
      const ok = !!(u.status_ok && u.content_match_ok);
      const st = ok ? 'up' : 'down';
      return {
        name: parsed.hostname || urlStr,
        // iconUrlFor keyword-scans the hostname (e.g. "opnsense" ->
        // opnsense.svg); falls back to a hidden img on no match.
        icon: parsed.hostname || urlStr,
        service_idx: null,
        status: st,
        port_status: st,
        is_http_probe: true,
        url: urlStr,
      };
    }
    return null;
  },
  // Inline-popover state for the drawer's "Switch to tag…" affordance.
  // Single open-popover at a time across the app — `_retagPopoverItemId`
  // holds the currently-open item id (raw_id || id) or null.
  // Replaces the earlier SwAl-prompt path per operator request:
  // "don't make it another popup, more enhanced and modern UI/UX".
  // The popover anchors to the button itself (CSS position: absolute
  // inside a position: relative wrapper) so it visually attaches to
  // the action that opened it.
  _retagPopoverItemId: null,
  _retagDraft: '',
  _retagBusy: false,
  // {left, top, width} — viewport coords for the fixed-position
  // popover. Computed from the trigger button's rect at open time;
  // recomputed on the same tick if the operator re-clicks. Using
  // `position: fixed` (instead of `position: absolute`) escapes the
  // drawer's `overflow: hidden`/`auto` clipping context that was
  // cropping the popover when it landed near the drawer's bottom
  // edge. Trade-off: scroll detaches the popover from the button —
  // mitigated via the scroll-close listener wired in
  // `openRetagPopover`.
  _retagPopoverPos: null,
  _retagScrollOff: null,
  openRetagPopover(item, ev) {
    if (!item) {
      return;
    }
    const id = item.raw_id || item.id;
    // Toggle: clicking the same item's button closes the popover.
    if (this._retagPopoverItemId === id) {
      this.closeRetagPopover();
      return;
    }
    this._retagPopoverItemId = id;
    this._retagDraft = '';
    this._retagBusy = false;
    // Anchor the fixed-position popover to the button's viewport
    // rect. Defensive: when called without an event (e.g. the AI
    // dispatch path), default to a centered viewport position so
    // the panel still renders visibly.
    let anchor = null;
    if (ev && ev.currentTarget && typeof ev.currentTarget.getBoundingClientRect === 'function') {
      anchor = ev.currentTarget.getBoundingClientRect();
    }
    if (anchor) {
      // Position BELOW the button by default; flip to ABOVE when
      // there isn't enough room (button near viewport bottom).
      // Popover is ~260px tall once filled — use 220 as a soft
      // threshold so the input is still in view.
      const POPOVER_EST_HEIGHT = 220;
      const flipUp = (window.innerHeight - anchor.bottom) < POPOVER_EST_HEIGHT
        && anchor.top > POPOVER_EST_HEIGHT;
      const top = flipUp
        ? Math.round(anchor.top - POPOVER_EST_HEIGHT - 4)
        : Math.round(anchor.bottom + 4);
      // Clamp horizontally so the popover never overflows the
      // viewport edge. The CSS sets max-width: min(360px, 90vw),
      // so we anchor to the button's left and let the panel grow
      // rightward unless that would overflow.
      const POPOVER_MAX_WIDTH = Math.min(360, window.innerWidth * 0.9);
      let left = Math.round(anchor.left);
      if (left + POPOVER_MAX_WIDTH > window.innerWidth - 8) {
        left = Math.max(8, window.innerWidth - POPOVER_MAX_WIDTH - 8);
      }
      this._retagPopoverPos = {
        left,
        top,
        width: Math.max(280, Math.round(anchor.width)),
      };
    } else {
      // Fallback: center horizontally near the top of the viewport.
      this._retagPopoverPos = {
        left: Math.round(window.innerWidth / 2 - 160),
        top: 120,
        width: 320,
      };
    }
    // Scroll-close: any scroll event on the page detaches the
    // popover from the button (it's pinned to viewport coords),
    // which looks broken. Close the popover instead. Capture-phase
    // listener catches scrolls inside the drawer's overflow
    // ancestor too.
    if (this._retagScrollOff) {
      this._retagScrollOff();
      this._retagScrollOff = null;
    }
    const onScroll = () => this.closeRetagPopover();
    window.addEventListener('scroll', onScroll, {capture: true, passive: true});
    window.addEventListener('resize', onScroll);
    this._retagScrollOff = () => {
      window.removeEventListener('scroll', onScroll, {capture: true});
      window.removeEventListener('resize', onScroll);
    };
    // Focus the input on the next render so the operator can type
    // immediately. Defensive: ref might not be mounted yet on a
    // newly-toggled popover — wait one tick.
    this.$nextTick(() => {
      try {
        const el = document.querySelector('[data-retag-input="' + id + '"]');
        if (el) {
          el.focus();
          el.select();
        }
      } catch (_) {
      }
    });
  },
  // Inline style emitter for the fixed-position popover. Reads
  // `_retagPopoverPos` and produces the `left:` / `top:` / minwidth
  // declarations the popover binds via `:style`. Returns an empty
  // string when there's no position (popover closed).
  _retagPopoverStyle() {
    const p = this._retagPopoverPos;
    if (!p) {
      return '';
    }
    return 'left:' + p.left + 'px; top:' + p.top + 'px; min-width:' + p.width + 'px;';
  },
  async submitRetagPopover(item) {
    if (!item || this._retagBusy) {
      return;
    }
    let target = (this._retagDraft || '').trim();
    // User-friendly tolerance: if the operator typed something
    // image-shaped (`server:2026.2.3` or
    // `ghcr.io/goauthentik/server:2026.2.3`) instead of just the
    // bare tag, strip everything up to and including the last `:`.
    // Docker's tag spec defines the tag as the text AFTER the last
    // colon, so treating the tail as the tag matches operator
    // intent. Operator-reported pattern: typed `2026.2.3` after the
    // current-image `<code>` block visually adjacent to the input,
    // ended up with `server:2026.2.3` in the field.
    if (target.includes(':')) {
      target = target.slice(target.lastIndexOf(':') + 1).trim();
    }
    // Client-side validation mirrors the backend's _validate_retag_tag.
    // Empty is allowed (server defaults to "latest"); otherwise must
    // match Docker tag charset.
    if (target && !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(target)) {
      this.showToast(this.t('toasts.retag_invalid_tag', {tag: target}), 'error');
      return;
    }
    const newTag = target || 'latest';
    // Idempotence: if image already tracks the requested tag,
    // short-circuit with a friendly toast instead of churning the
    // backend.
    const currentImage = item.image || '';
    let imageRepo = currentImage.split('@')[0];
    const lastColon = imageRepo.lastIndexOf(':');
    const lastSlash = imageRepo.lastIndexOf('/');
    const currentTag = (lastColon > lastSlash) ? imageRepo.slice(lastColon + 1) : '';
    if (lastColon > lastSlash) {
      imageRepo = imageRepo.slice(0, lastColon);
    }
    if (currentTag === newTag && !currentImage.includes('@')) {
      this.showToast(this.t('toasts.retag_already_target', {name: item.name, tag: newTag}), 'info');
      this.closeRetagPopover();
      return;
    }
    this._retagBusy = true;
    const isStackPath = !!item.stack_id;
    const key = isStackPath
      ? this._busyKey('stack', item.stack_id)
      : this._busyKey('item', item.raw_id || item.id);
    this._markBusy(key);
    try {
      const url = isStackPath
        ? `/api/update/stack/${item.stack_id}/retag-latest`
        : `/api/update/container/${item.raw_id || item.id}/retag-latest`;
      const body = isStackPath
        ? {image_repo: (imageRepo || '').trim(), tag: newTag}
        : {tag: newTag};
      const r = await fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      this.showToast(this.t('toasts.retag_queued', {name: item.name, tag: newTag}));
      this.closeRetagPopover();
      this.pollOpsNow();
    } catch (e) {
      this._clearBusy(key);
      this.showToast(this.t('toasts.failed_with_error', {error: e.message}), 'error');
    } finally {
      this._retagBusy = false;
    }
  },
};
