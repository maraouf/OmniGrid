// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ExceptionCaughtLocallyJS,JSReusedLocalVariable,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,RedundantLocalVariableJS,JSMissingAwait,JSAsyncFunctionMissingAwait
/* global Alpine, Swal, I18N, t */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */

// SPA — Admin → Docker Nodes (direct-Docker, Portainer-less, managed over SSH).
// Per-node list editor mirroring the curated-hosts editor: load / add / remove /
// Test-connection / Save with a dirty ring. SSH password follows the keep-
// current-if-blank contract (the GET returns a `password_set` flag, never the
// cleartext). Merged into the Alpine component via `_mergeKeepDescriptors`.

export default {
  dockerNodes: [],            // the editable rows
  dockerNodesSaving: false,
  dockerNodesLoaded: false,
  _dockerNodesBaseline: '',   // dirty-tracking snapshot
  dockerNodeTests: {},        // per-row test result keyed by row index
  // Opt-in: use the direct-Docker SSH client as a STATS fallback for
  // Portainer-managed worker nodes whose /stats Portainer can't fetch. Default
  // off (preserves the no-direct-socket contract); the per-host ssh.enabled
  // flag in Admin → Hosts is the per-node opt-in on top of this master toggle.
  dockerStatsFallbackEnabled: false,

  // Snapshot of the rows (+ the fallback toggle) for dirty tracking.
  // ssh.password is folded in only as a "typed" marker (the secret itself
  // never round-trips from the server).
  _dockerNodesSnapshot() {
    return JSON.stringify({
      fallback: !!this.dockerStatsFallbackEnabled,
      nodes: (this.dockerNodes || []).map((n) => ({
      id: (n.id || '').trim(),
      label: (n.label || '').trim(),
      address: (n.address || '').trim(),
      socket_path: (n.socket_path || '').trim(),
      icon: (n.icon || '').trim(),
      enabled: !!n.enabled,
      ssh: {
        user: ((n.ssh || {}).user || '').trim(),
        port: (n.ssh || {}).port || '',
        pw_typed: !!((n.ssh || {}).password && String((n.ssh || {}).password).length),
        pw_set: !!((n.ssh || {}).password_set),
        enabled: !!((n.ssh || {}).enabled),
      },
      compose_projects: (n.compose_projects || [])
        .map((c) => ({project: (c.project || '').trim(), path: (c.path || '').trim()})),
      transport: n.transport === 'tls' ? 'tls' : 'ssh',
      tls_port: n.tls_port || '',
      tls_ca: (n.tls_ca || '').trim(),
      tls_cert: (n.tls_cert || '').trim(),
      tls_key_typed: !!(n.tls_key && String(n.tls_key).length),
      tls_key_set: !!n.tls_key_set,
      })),
    });
  },
  dockerNodesDirty() {
    return this._dockerNodesBaseline !== this._dockerNodesSnapshot();
  },

  async loadDockerNodes() {
    try {
      const r = await fetch('/api/docker-nodes');
      if (!r.ok) {
        throw new Error(await this.fmtResponseError(r));
      }
      const j = await r.json();
      this.dockerNodes = (Array.isArray(j.docker_nodes) ? j.docker_nodes : []).map((n) => ({
        id: n.id || '',
        label: n.label || '',
        address: n.address || '',
        socket_path: n.socket_path || '',
        icon: n.icon || '',
        enabled: n.enabled !== false,
        // Editable ssh block — password starts blank (write-only); password_set
        // tells the UI a secret is stored (keep-current on save).
        ssh: {
          user: (n.ssh || {}).user || '',
          port: (n.ssh || {}).port || '',
          password: '',
          password_set: !!((n.ssh || {}).password_set),
          enabled: (n.ssh || {}).enabled !== false,
        },
        // Compose project → compose-file path rows (makes a docker compose
        // stack on this node updatable over SSH).
        compose_projects: (Array.isArray(n.compose_projects) ? n.compose_projects : [])
          .map((c) => ({project: (c && c.project) || '', path: (c && c.path) || ''})),
        // Transport: 'ssh' (default) or 'tls' (TCP+TLS to the daemon). TLS PEM:
        // CA + client cert round-trip; the private key is write-only (blank +
        // tls_key_set flag, keep-current on save like the SSH password).
        transport: n.transport === 'tls' ? 'tls' : 'ssh',
        tls_port: n.tls_port || '',
        tls_ca: n.tls_ca || '',
        tls_cert: n.tls_cert || '',
        tls_key: '',
        tls_key_set: !!n.tls_key_set,
      }));
      this.dockerStatsFallbackEnabled = !!j.stats_fallback_enabled;
      this.dockerNodeTests = {};
      this._dockerNodesBaseline = this._dockerNodesSnapshot();
      this.dockerNodesLoaded = true;
    } catch (e) {
      this.showToast((e && e.message) || this.t('toasts.network_error'), 'error');
    }
  },

  // Generate a stable, URL-safe id for a fresh row so the operator only has to
  // pick a label (the id is the routing/cooldown key, not operator-facing).
  _newDockerNodeId() {
    const rnd = Math.random().toString(36).slice(2, 8);
    const ts = Date.now().toString(36).slice(-4);
    return 'node-' + ts + rnd;
  },
  addDockerNode() {
    this.dockerNodes.push({
      id: this._newDockerNodeId(),
      label: '', address: '', socket_path: '', icon: '',
      enabled: true,
      ssh: {user: '', port: '', password: '', password_set: false, enabled: true},
      compose_projects: [],
      transport: 'ssh', tls_port: '', tls_ca: '', tls_cert: '', tls_key: '', tls_key_set: false,
    });
  },
  removeDockerNode(i) {
    if (i >= 0 && i < this.dockerNodes.length) {
      this.dockerNodes.splice(i, 1);
      delete this.dockerNodeTests[i];
    }
  },
  // Per-node compose-project → path rows (the compose-update-over-SSH targets).
  addComposeProject(i) {
    const n = this.dockerNodes[i];
    if (!n) {
      return;
    }
    if (!Array.isArray(n.compose_projects)) {
      n.compose_projects = [];
    }
    n.compose_projects.push({project: '', path: ''});
  },
  removeComposeProject(i, j) {
    const n = this.dockerNodes[i];
    if (n && Array.isArray(n.compose_projects) && j >= 0 && j < n.compose_projects.length) {
      n.compose_projects.splice(j, 1);
    }
  },

  // Build the POST/Test body for one row — ssh.password only when typed
  // (keep-current contract).
  _dockerNodeBody(n) {
    const ssh = {
      user: ((n.ssh || {}).user || '').trim(),
      port: parseInt(String((n.ssh || {}).port || ''), 10) || undefined,
      enabled: ((n.ssh || {}).enabled !== false),
    };
    const pw = (n.ssh || {}).password;
    if (pw && String(pw).trim()) {
      ssh.password = pw;
    }
    const body = {
      id: (n.id || '').trim(),
      label: (n.label || '').trim(),
      address: (n.address || '').trim(),
      socket_path: (n.socket_path || '').trim(),
      icon: (n.icon || '').trim(),
      enabled: !!n.enabled,
      ssh,
      compose_projects: (n.compose_projects || [])
        .map((c) => ({project: (c.project || '').trim(), path: (c.path || '').trim()}))
        .filter((c) => c.project && c.path),
      transport: n.transport === 'tls' ? 'tls' : 'ssh',
      tls_port: n.tls_port || '',
      tls_ca: (n.tls_ca || '').trim(),
      tls_cert: (n.tls_cert || '').trim(),
    };
    // Private key only when typed (keep-current contract — blank preserves the stored key).
    const k = (n.tls_key || '').trim();
    if (k) {
      body.tls_key = k;
    }
    return body;
  },

  async testDockerNode(i) {
    const n = this.dockerNodes[i];
    if (!n) {
      return;
    }
    this.dockerNodeTests[i] = {pending: true};
    try {
      const r = await fetch('/api/docker-nodes/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(this._dockerNodeBody(n)),
      });
      const j = await r.json().catch(() => ({}));
      this.dockerNodeTests[i] = {
        pending: false, ok: !!j.ok, status: j.status || 0,
        detail: j.detail || '', version: j.version || '',
      };
    } catch (_) {
      this.dockerNodeTests[i] = {pending: false, ok: false, status: 0,
        detail: this.t('toasts.network_error')};
    }
  },

  async saveDockerNodes() {
    if (this.dockerNodesSaving) {
      return;
    }
    // Cheap client-side guard — every enabled node needs an address.
    const bad = (this.dockerNodes || []).find((n) => n.enabled && !((n.address || '').trim()));
    if (bad) {
      this.showToast(this.t('admin.docker_nodes.address_required') || 'Each enabled Docker node needs an address.', 'error');
      return;
    }
    this.dockerNodesSaving = true;
    try {
      const r = await fetch('/api/docker-nodes', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          docker_nodes: (this.dockerNodes || []).map((n) => this._dockerNodeBody(n)),
          stats_fallback_enabled: !!this.dockerStatsFallbackEnabled,
        }),
      });
      if (!r.ok) {
        this.showToast(await this.fmtResponseError(r), 'error');
        return;
      }
      this.showToast(this.t('admin.docker_nodes.saved') || 'Docker nodes saved');
      await this.loadDockerNodes();   // re-baseline + re-redact
      this.refresh(true);             // force a gather so the node's containers appear
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.dockerNodesSaving = false;
    }
  },
};
