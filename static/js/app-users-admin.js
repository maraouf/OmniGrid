// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
// "Unused function" warnings fire on every Alpine-template-consumed
// method — the SPA spread-imports this module's export into the
// Alpine root and templates call `sortedUsers()` / `createUser()` /
// `setUserNotifyEventValue()` / etc. via `@click` + `x-for` bindings
// the static analyser can't see. Same lineage for `Element is not
// exported` WEAK warnings. Constant-on-RHS covers natural
// `x === 'admin'` form (project convention, not Yoda) AND the
// canonical `x == null` / `(x || '').length < 8` idioms. Nested
// `t()` calls are by design — the i18n helper takes a dynamic key
// built by template-literal concat. Anonymous-function callbacks
// inside Swal confirmation chains are inline + readable in place.
// Empty catch + unused `_` are the ignore-and-move-on shape on
// localStorage persistence + fetch failures (the surrounding toast
// owns the user-visible error). `continue` is the nested-loop
// skip pattern in the notify-events grid bulk handlers.
// noinspection JSUnusedGlobalSymbols,UnusedFunctionJS,ElementNotExported
// noinspection ConstantOnRightSideOfComparisonJS,JSConstantOnRightSideOfComparison
// noinspection AnonymousFunctionJS
// noinspection ContinueStatementJS,BreakStatementJS
// noinspection UnusedCatchParameterJS,EmptyCatchBlockJS
// noinspection NestedFunctionCallJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Users / Sessions / API Tokens admin (Admin → Users, Sessions, Tokens).

export default {
  users: [],
  sessions: [], sessionsLoaded: false,
  usersLoaded: false, tokensLoaded: false,
  tokens: [],
  newUser: {username: '', role: 'readonly', auth_source: 'local', password: '', email: ''},
  newToken: {name: '', role: 'readonly'},
  // Raw new-token payload shown exactly once in a modal after creation.
  lastCreatedToken: null,
  // Sort state for the 3 admin tables. Default `col: ''` = no sort
  // (server / source order). Clicking a column header flips through
  // the shared `_sortToggle` helper; the table render walks
  // `sortedUsers()` / `sortedSessions()` / `sortedTokens()` rather
  // than the raw arrays so the sort is in-place against a copy.
  usersSort: {col: '', dir: 'desc'},
  sessionsSort: {col: '', dir: 'desc'},
  tokensSort: {col: '', dir: 'desc'},
  sortedUsers() {
    return this._sortRows(this.users || [], this.usersSort);
  },
  sortedSessions() {
    return this._sortRows(this.sessions || [], this.sessionsSort);
  },
  sortedTokens() {
    return this._sortRows(this.tokens || [], this.tokensSort);
  },

  async loadUsers() {
    try {
      const r = await fetch('/api/users');
      if (!r.ok) {
        return;
      }
      const d = await r.json();
      this.users = d.users || [];
    } catch (_) {
    } finally {
      this.usersLoaded = true;
    }
  },

  async createUser() {
    const u = this.newUser;
    if (!u.username || !u.username.trim()) {
      this.showToast(this.t('toasts.username_required'), 'error');
      return;
    }
    if (u.auth_source === 'local' && (u.password || '').length < 8) {
      this.showToast(this.t('toasts.password_too_short'), 'error');
      return;
    }
    try {
      const r = await fetch('/api/users', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          username: u.username.trim(),
          role: u.role,
          auth_source: u.auth_source,
          password: u.auth_source === 'local' ? u.password : null,
          email: u.email || null,
        }),
      });
      if (r.ok) {
        this.showToast(this.t('toasts.user_created'));
        this.newUser = {username: '', role: 'readonly', auth_source: 'local', password: '', email: ''};
        await this.loadUsers();
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.create_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  async patchUser(u, patch) {
    try {
      const r = await fetch('/api/users/' + u.id, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(patch),
      });
      if (r.ok) {
        await this.loadUsers();
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.update_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  async toggleUserRole(u) {
    return this.patchUser(u, {role: u.role === 'admin' ? 'readonly' : 'admin'});
  },
  async toggleUserDisabled(u) {
    return this.patchUser(u, {disabled: !u.disabled});
  },

  async deleteUser(u) {
    const res = await Swal.fire({
      title: this.t('admin.backups.delete_user_title'),
      text: this.t('admin.backups.delete_user_confirm', {name: u.username}),
      icon: 'warning', showCancelButton: true,
      confirmButtonText: this.t('actions.delete'),
      cancelButtonText: this.t('actions.cancel'),
      confirmButtonColor: this._cssVar('--danger'),
    });
    if (!res.isConfirmed) {
      return;
    }
    try {
      const r = await fetch('/api/users/' + u.id, {method: 'DELETE'});
      if (r.ok) {
        this.showToast(this.t('toasts.user_deleted'));
        await this.loadUsers();
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.delete_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  async resetUserPassword(u) {
    if (u.auth_source !== 'local') {
      this.showToast(this.t('toasts.authentik_change_pw_here'), 'error');
      return;
    }
    const res = await Swal.fire({
      title: this.t('dialogs.reset_password_title', {name: u.username}),
      input: 'password', inputLabel: this.t('dialogs.reset_password_label'),
      inputAttributes: {minlength: 8, autocapitalize: 'off', autocorrect: 'off'},
      showCancelButton: true,
      confirmButtonText: this.t('dialogs.reset_button'),
      cancelButtonText: this.t('actions.cancel'),
    });
    if (!res.isConfirmed || !res.value) {
      return;
    }
    try {
      const r = await fetch('/api/users/' + u.id + '/reset-password', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({new_password: res.value}),
      });
      if (r.ok) {
        this.showToast(this.t('toasts.password_reset'));
        // Server also clears any TOTP enrolment for the target. Refresh
        // the user list so the 2FA column reflects the new state.
        await this.loadUsers();
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.reset_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  async loadSessions() {
    try {
      const r = await fetch('/api/sessions');
      if (!r.ok) {
        return;
      }
      const d = await r.json();
      this.sessions = d.sessions || [];
    } catch (_) {
    } finally {
      this.sessionsLoaded = true;
    }
  },

  async revokeSession(s) {
    const res = await Swal.fire({
      title: this.t('admin.backups.revoke_session_title'),
      text: this.t('admin.backups.revoke_session_text', {name: s.username || this.t('admin.backups.revoke_session_default')}),
      icon: 'warning', showCancelButton: true,
      confirmButtonText: this.t('admin.sessions.revoke'),
      cancelButtonText: this.t('actions.cancel'),
    });
    if (!res.isConfirmed) {
      return;
    }
    try {
      const r = await fetch('/api/sessions/' + encodeURIComponent(s.token_id), {method: 'DELETE'});
      if (r.ok) {
        this.showToast(this.t('toasts.session_revoked'));
        await this.loadSessions();
      } else {
        this.showToast(this.t('toasts.revoke_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  async loadTokens() {
    try {
      const r = await fetch('/api/tokens');
      if (!r.ok) {
        return;
      }
      const d = await r.json();
      this.tokens = d.tokens || [];
    } catch (_) {
    } finally {
      this.tokensLoaded = true;
    }
  },

  async createToken() {
    const t = this.newToken;
    if (!t.name || !t.name.trim()) {
      this.showToast(this.t('toasts.name_required'), 'error');
      return;
    }
    try {
      const r = await fetch('/api/tokens', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: t.name.trim(), role: t.role}),
      });
      if (r.ok) {
        // Raw token is shown ONCE — surface it in a one-time modal.
        this.lastCreatedToken = await r.json();
        this.newToken = {name: '', role: 'readonly'};
        await this.loadTokens();
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.create_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  async deleteToken(tk) {
    const res = await Swal.fire({
      title: this.t('admin.backups.revoke_token_title'),
      text: this.t('admin.backups.revoke_token_text', {name: tk.name}),
      icon: 'warning', showCancelButton: true,
      confirmButtonText: this.t('admin.tokens.revoke'),
      cancelButtonText: this.t('actions.cancel'),
    });
    if (!res.isConfirmed) {
      return;
    }
    try {
      const r = await fetch('/api/tokens/' + tk.id, {method: 'DELETE'});
      if (r.ok) {
        this.showToast(this.t('toasts.token_revoked'));
        await this.loadTokens();
      } else {
        this.showToast(this.t('toasts.revoke_failed'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },
  setUserNotifyEventValue(eventKey, medium, value) {
    const bare = this._bareEventName(eventKey);
    const f = this.profileForm || {};
    if (!f.notify_events) {
      f.notify_events = {};
    }
    if (!f.notify_events[bare] || typeof f.notify_events[bare] !== 'object') {
      // Coerce legacy bare-bool slot into the per-medium dict shape so
      // the rest of the form sees a uniform structure.
      const prev = !!f.notify_events[bare];
      const slot = {};
      for (const m of this.notifyMediumNames()) {
        slot[m] = prev;
      }
      f.notify_events[bare] = slot;
    }
    f.notify_events[bare][medium] = !!value;
  },
  // Toggle every medium for a given event in one click — clicking
  // the event label itself acts as a row-level master switch.
  toggleUserNotifyEventRow(eventKey, value) {
    if (this.userNotifyEventDisabledByAdmin(eventKey)) {
      return;
    }
    const bare = this._bareEventName(eventKey);
    const f = this.profileForm || {};
    if (!f.notify_events) {
      f.notify_events = {};
    }
    const slot = {};
    for (const m of this.notifyMediumNames()) {
      slot[m] = !!value;
    }
    f.notify_events[bare] = slot;
  },
  // Toggle every event for a given medium in one click — clicking
  // the medium column header acts as a column-level master switch.
  // Skips admin-disabled events.
  toggleUserNotifyMediumColumn(medium, value) {
    const f = this.profileForm || {};
    if (!f.notify_events) {
      f.notify_events = {};
    }
    for (const k of this.notifyEventKeys) {
      if (this.userNotifyEventDisabledByAdmin(k)) {
        continue;
      }
      const bare = this._bareEventName(k);
      if (!f.notify_events[bare] || typeof f.notify_events[bare] !== 'object') {
        const prev = !!f.notify_events[bare];
        const slot = {};
        for (const m of this.notifyMediumNames()) {
          slot[m] = prev;
        }
        f.notify_events[bare] = slot;
      }
      f.notify_events[bare][medium] = !!value;
    }
  },
  setAllUserNotifyEvents(value) {
    const v = !!value;
    const f = this.profileForm || {};
    if (!f.notify_events) {
      f.notify_events = {};
    }
    const mediums = this.notifyMediumNames();
    for (const k of this.notifyEventKeys) {
      if (this.userNotifyEventDisabledByAdmin(k)) {
        continue;
      }
      const bare = this._bareEventName(k);
      const slot = {};
      for (const m of mediums) {
        slot[m] = v;
      }
      f.notify_events[bare] = slot;
    }
  },
  setUserNotifyEventsErrorsOnly() {
    const f = this.profileForm || {};
    if (!f.notify_events) {
      f.notify_events = {};
    }
    const mediums = this.notifyMediumNames();
    for (const g of this.notifyEventGroups) {
      if (!this.userNotifyEventDisabledByAdmin(g.success)) {
        const bareS = this._bareEventName(g.success);
        const slot = {};
        for (const m of mediums) {
          slot[m] = false;
        }
        f.notify_events[bareS] = slot;
      }
      if (!this.userNotifyEventDisabledByAdmin(g.failure)) {
        const bareF = this._bareEventName(g.failure);
        const slot = {};
        for (const m of mediums) {
          slot[m] = true;
        }
        f.notify_events[bareF] = slot;
      }
    }
  },
  // One-click bulk-set: route every success event to
  // ``successMedium`` AND every failure event to ``failureMedium``.
  // Either side can be empty — that side is left untouched. The
  // chosen medium is the ONLY one enabled for those events; every
  // other medium for the same event is set to false (matching the
  // Errors-only pattern's all-or-nothing-per-medium contract).
  // Skips events the admin has globally disabled (the backend would
  // 400 such an opt-in anyway; see api_me_notify_prefs).
  setUserNotifyEventsByMediumPattern(successMedium, failureMedium) {
    const f = this.profileForm || {};
    if (!f.notify_events) {
      f.notify_events = {};
    }
    const mediums = this.notifyMediumNames();
    const apply = (eventKey, chosen) => {
      if (this.userNotifyEventDisabledByAdmin(eventKey)) {
        return;
      }
      const bare = this._bareEventName(eventKey);
      const slot = {};
      for (const m of mediums) {
        slot[m] = (m === chosen);
      }
      f.notify_events[bare] = slot;
    };
    for (const g of this.notifyEventGroups) {
      if (successMedium) {
        apply(g.success, successMedium);
      }
      if (failureMedium) {
        apply(g.failure, failureMedium);
      }
    }
  },
};
