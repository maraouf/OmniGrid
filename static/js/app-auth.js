// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Auth + 2FA surface — TOTP enrol / disable / regenerate / policy,
// WebAuthn passkey registration + revocation, password change flow.


export default {
  // Password change form (lives in the Profile settings section now, not a modal)
  passwordForm: {current: '', next: '', confirm: ''},
  passwordBusy: false,
  _totpPolicyBaseline: '',
  // TOTP / 2FA enrolment state. Mirrors the /api/me/totp shape
  // plus a few transient enrolment fields used during the QR -> verify
  // step. backup_codes is `[{code, used_at}]` plain after the GET; the
  // hide/unhide eye is purely client-side (`totpCodesRevealed`).
  totp: {
    loaded: false, allowed: true, enabled: false, required: false,
    auth_source: 'local', backup_codes: [], policy: {},
  },
  totpCodesRevealed: false,
  totpEnrol: {secret: '', uri: '', code: ''},
  totpEnrolStage: 'idle',  // idle | qr | reveal
  totpEnrolBusy: false,
  totpRevealCodes: [],     // one-time plaintext list right after enrol/regen
  totpDisableForm: {password: ''},
  totpDisableBusy: false,
  // WebAuthn / passkeys. Mirrors the /api/me/webauthn shape.
  // `list` is the array of enrolled credentials; `busy` covers either
  // the register or revoke ceremony so the operator can't double-tap.
  // `supported` is the SERVER capability flag (false on builds where
  // the webauthn library isn't installed); `passkeyBrowserSupported()`
  // covers the CLIENT side separately.
  passkeys: {
    loaded: false, supported: false, list: [], busy: false,
    // server-derived effective rp_id (request.url.hostname or
    // X-Forwarded-Host). Profile → Security compares each
    // credential's stored rp_id against this so cross-domain
    // passkeys (orphaned by a domain migration) get the red
    // "different domain" badge inline.
    current_rp_id: '',
  },

  // --- Profile: password strength meter -------------------------------
  // Pure UX affordance; the backend still enforces the 8-char minimum.
  // Scoring weights length more than character-class diversity — a
  // 12-char lowercase passphrase is stronger than `Aa1!` and the
  // meter should reflect that. Under 8 chars is always clamped to Weak
  // regardless of other criteria because the backend will reject it.
  get passwordStrength() {
    const pw = (this.passwordForm && this.passwordForm.next) || '';
    const criteria = {
      length8: pw.length >= 8,
      length12: pw.length >= 12,
      lower: /[a-z]/.test(pw),
      upper: /[A-Z]/.test(pw),
      digit: /\d/.test(pw),
      symbol: /[^A-Za-z0-9]/.test(pw),
    };
    if (!pw) {
      return {score: 0, label: '', color: 'faint', criteria};
    }
    let score = 0;
    if (criteria.length8) {
      score += 1;
    }
    if (criteria.length12) {
      score += 1;
    }
    if (criteria.lower && criteria.upper) {
      score += 1;
    }
    if (criteria.digit) {
      score += 1;
    }
    if (criteria.symbol) {
      score += 1;
    }
    if (score > 4) {
      score = 4;
    }
    if (!criteria.length8) {
      score = Math.min(score, 1);
    }
    const labelKeys = [
      'password.strength.too_short',
      'password.strength.weak',
      'password.strength.fair',
      'password.strength.good',
      'password.strength.strong',
    ];
    const colors = ['danger', 'danger', 'warning', 'primary', 'success'];
    return {
      score,
      label: this.t(labelKeys[score]),
      color: colors[score],
      criteria,
    };
  },

  // --- Profile: password change ---------------------------------------
  async changePassword() {
    if (this.passwordBusy) {
      return;
    }
    const f = this.passwordForm;
    if (f.next !== f.confirm) {
      this.showToast(this.t('toasts.password_mismatch'), 'error');
      return;
    }
    if ((f.next || '').length < 8) {
      this.showToast(this.t('toasts.password_too_short'), 'error');
      return;
    }
    this.passwordBusy = true;
    try {
      const body = new URLSearchParams({
        current_password: f.current,
        new_password: f.next,
        confirm_password: f.confirm,
      });
      const r = await fetch('/api/local-auth/change-password', {method: 'POST', body});
      if (r.ok) {
        this.showToast(this.t('toasts.password_changed'), 'success');
        this.passwordForm = {current: '', next: '', confirm: ''};
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.change_failed', {status: r.status}), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.passwordBusy = false;
    }
  },

  totpPolicySaving: false,
  async saveTotpPolicy() {
    if (this.totpPolicySaving) {
      return;
    }
    this.totpPolicySaving = true;
    try {
      const s = this.settings || {};
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          totp_allowed: !!s.totp_allowed,
          totp_required_for_admins: !!s.totp_required_for_admins,
          totp_required_for_users: !!s.totp_required_for_users,
          totp_lockout_max_failures: +s.totp_lockout_max_failures || 5,
          totp_lockout_minutes: +s.totp_lockout_minutes || 15,
          passkeys_allowed: !!s.passkeys_allowed,
        }),
      });
      if (r.ok) {
        this._totpPolicyBaseline = this._totpPolicySnapshot();
        this.showToast(this.t('toasts.settings_saved'), 'success');
      } else {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts_extra.save_failed_generic'), 'error');
      }
    } catch (_) {
      this.showToast(this.t('toasts_extra.network_error_generic'), 'error');
    } finally {
      this.totpPolicySaving = false;
    }
  },

  // ----------------------------------------------------------------
  // TOTP / 2FA management. Three call sites:
  // - Profile (self): loadTotpStatus / startTotpEnrol / confirmTotpEnrol
  //                   / disableTotpSelf / regenerateTotpCodes
  // - Admin -> Users: adminDisableTotp(u)
  // - Login page does its own thing in /js/login.js
  // The /api/me/totp call returns plaintext backup codes; the
  // hide/unhide eye is purely client-side.
  // ----------------------------------------------------------------
  // ----------------------------------------------------------------
  // WebAuthn / passkeys. Mirror the TOTP UX shape: load on
  // page open, expose register/revoke from the Profile -> Security
  // card, surface count + browser-support hint in the SPA's state.
  // ----------------------------------------------------------------
  passkeyBrowserSupported() {
    return !!(window.PublicKeyCredential && navigator.credentials);
  },

  _passkeyOptionsForCreate(opts) {
    const out = Object.assign({}, opts);
    out.challenge = this._b64uDecode(opts.challenge);
    if (opts.user && opts.user.id) {
      out.user = Object.assign({}, opts.user);
      out.user.id = this._b64uDecode(opts.user.id);
    }
    if (Array.isArray(opts.excludeCredentials)) {
      out.excludeCredentials = opts.excludeCredentials.map(c => ({
        type: c.type,
        id: this._b64uDecode(c.id),
        transports: c.transports,
      }));
    }
    return out;
  },

  _passkeyAttestationResponse(cred) {
    return {
      id: cred.id,
      rawId: this._b64uEncode(cred.rawId),
      type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || null,
      clientExtensionResults: cred.getClientExtensionResults
        ? cred.getClientExtensionResults() : {},
      response: {
        attestationObject: this._b64uEncode(cred.response.attestationObject),
        clientDataJSON: this._b64uEncode(cred.response.clientDataJSON),
        transports: cred.response.getTransports
          ? cred.response.getTransports() : [],
      },
    };
  },

  async loadPasskeys() {
    try {
      const r = await fetch('/api/me/webauthn');
      if (!r.ok) {
        this.passkeys = {loaded: true, supported: false, list: [], busy: false, current_rp_id: ''};
        return;
      }
      const j = await r.json();
      this.passkeys = {
        loaded: true,
        supported: !!j.supported,
        list: Array.isArray(j.credentials) ? j.credentials : [],
        busy: false,
        // server's effective rp_id; Profile → Security uses
        // it as the comparison anchor for the orphaned-credential
        // badge. Lower-cased so the SPA's `pk.rp_id !== current`
        // check is stable when storage trimmed-and-lowered the
        // credential rp_id at registration but the request resolves
        // to a mixed-case Host header.
        current_rp_id: ((j.current_rp_id || '') + '').toLowerCase(),
      };
    } catch (_) {
      this.passkeys = {loaded: true, supported: false, list: [], busy: false, current_rp_id: ''};
    }
  },

  async addPasskey() {
    if (!this.passkeyBrowserSupported()) {
      this.showToast(this.t('toasts.passkey_browser_unsupported'), 'error');
      return;
    }
    if (!this.passkeys.supported) {
      this.showToast(this.t('toasts.passkey_server_unsupported'), 'error');
      return;
    }
    // Friendly-name first via SweetAlert so the user types it BEFORE
    // we touch the WebAuthn API. The server-side challenge mint +
    // navigator.credentials.create() then run back-to-back without
    // any further user interaction in between, which keeps the
    // user-gesture chain intact for password-manager extensions
    // (1Password / Bitwarden / iCloud Keychain) so they get a chance
    // to offer the "save passkey" sheet alongside the OS-native
    // picker.
    const nameRes = await Swal.fire({
      title: this.t('settings.profile.passkeys.name_prompt_title'),
      text: this.t('settings.profile.passkeys.name_prompt_body'),
      input: 'text',
      inputPlaceholder: this.t('settings.profile.passkeys.name_placeholder'),
      showCancelButton: true,
      confirmButtonText: this.t('settings.profile.passkeys.add_button'),
      cancelButtonText: this.t('actions.cancel'),
      inputValidator: (val) => {
        if (val && val.length > 64) {
          return this.t('settings.profile.passkeys.name_prompt_body');
        }
        return null;
      },
    });
    if (!nameRes.isConfirmed) {
      return;
    }
    const friendlyName = (nameRes.value || '').trim();
    this.passkeys.busy = true;
    try {
      const startResp = await fetch('/api/me/webauthn/register-start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: '{}',
      });
      if (!startResp.ok) {
        const j = await startResp.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.passkey_register_failed'), 'error');
        return;
      }
      const startJ = await startResp.json();
      const publicKey = this._passkeyOptionsForCreate(startJ.options);
      let cred;
      try {
        cred = await navigator.credentials.create({publicKey});
      } catch (e) {
        // Surface the real error reason — silent toast made it
        // impossible to tell apart "user dismissed the picker",
        // "device declined", "extension blocked", "RP ID mismatch"
        //. DOMException.name is the canonical key
        // (NotAllowedError / SecurityError / InvalidStateError /
        // AbortError); fall through to the generic message when the
        // browser threw a non-DOMException.
        const detail = (e && (e.name || e.message)) || '';
        if (detail) {
          this.showToast(
            this.t('toasts.passkey_register_failed') + ' (' + detail + ')',
            'error',
          );
        } else {
          this.showToast(this.t('toasts.passkey_register_failed'), 'error');
        }
        // POST the failure to the server so it lands in Admin → Logs
        // alongside the matching register-start line. Fire-
        // and-forget — a logging endpoint shouldn't be able to
        // cascade into the user-visible flow.
        try {
          fetch('/api/me/webauthn/client-error', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              phase: 'register',
              error_name: (e && e.name) || '',
              error_message: (e && e.message) || '',
              rp_id: (publicKey && publicKey.rp && publicKey.rp.id) || '',
              origin: window.location.origin,
            }),
          }).catch(() => {
          });
        } catch (_) {
        }
        return;
      }
      if (!cred) {
        this.showToast(this.t('toasts.passkey_register_failed'), 'error');
        return;
      }
      const finishResp = await fetch('/api/me/webauthn/register-finish', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          credential: this._passkeyAttestationResponse(cred),
          friendly_name: friendlyName,
        }),
      });
      if (!finishResp.ok) {
        const j = await finishResp.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.passkey_register_failed'), 'error');
        return;
      }
      await this.loadPasskeys();
      if (this.me) {
        this.me.passkeys = {
          ...(this.me.passkeys || {}),
          count: (this.passkeys.list || []).length,
          supported: true,
        };
      }
      this.showToast(this.t('toasts.passkey_added'));
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.passkeys.busy = false;
    }
  },

  async revokePasskey(pk) {
    const res = await Swal.fire({
      title: this.t('settings.profile.passkeys.revoke_confirm_title'),
      text: this.t('settings.profile.passkeys.revoke_confirm_body', {
        name: pk.friendly_name || this.t('settings.profile.passkeys.name_default'),
      }),
      icon: 'warning', showCancelButton: true,
      confirmButtonText: this.t('settings.profile.passkeys.revoke_confirm_button'),
      cancelButtonText: this.t('actions.cancel'),
      confirmButtonColor: this._cssVar('--danger'),
    });
    if (!res.isConfirmed) {
      return;
    }
    this.passkeys.busy = true;
    try {
      const r = await fetch('/api/me/webauthn/' + encodeURIComponent(pk.id), {
        method: 'DELETE',
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.passkey_revoke_failed'), 'error');
        return;
      }
      await this.loadPasskeys();
      if (this.me) {
        this.me.passkeys = {
          ...(this.me.passkeys || {}),
          count: (this.passkeys.list || []).length,
        };
      }
      this.showToast(this.t('toasts.passkey_revoked'));
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.passkeys.busy = false;
    }
  },

  async loadTotpStatus() {
    try {
      const r = await fetch('/api/me/totp');
      if (!r.ok) {
        this.totp.loaded = true;
        return;
      }
      const j = await r.json();
      this.totp = {
        loaded: true,
        allowed: !!j.allowed,
        enabled: !!j.enabled,
        required: !!j.required,
        auth_source: j.auth_source || 'local',
        backup_codes: Array.isArray(j.backup_codes) ? j.backup_codes : [],
        policy: j.policy || {},
      };
    } catch (_) {
      this.totp.loaded = true;
    }
  },

  totpDisplayCode(c) {
    if (!c || !c.code) {
      return '';
    }
    if (this.totpCodesRevealed) {
      return c.code;
    }
    // Same character count + the space, masked.
    const len = String(c.code).replace(/\s/g, '').length;
    const half = Math.floor(len / 2);
    return ('•'.repeat(half)) + ' ' + ('•'.repeat(len - half));
  },

  async startTotpEnrol() {
    this.totpEnrolBusy = true;
    try {
      const r = await fetch('/api/me/totp/enroll-start', {method: 'POST'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.totp_enroll_failed'), 'error');
        return;
      }
      const j = await r.json();
      this.totpEnrol = {secret: j.secret, uri: j.provisioning_uri, code: ''};
      this.totpEnrolStage = 'qr';
      // Defer to the next tick so the <div id="totp-enrol-qr"> exists.
      this.$nextTick(() => this._renderTotpQr());
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.totpEnrolBusy = false;
    }
  },

  _renderTotpQr() {
    const el = document.getElementById('totp-enrol-qr');
    if (!el) {
      return;
    }
    el.textContent = '';
    const uri = this.totpEnrol.uri;
    if (!uri) {
      return;
    }
    if (!window.qrcode) {
      const code = document.createElement('code');
      code.className = 'mono totp-qr-fallback';
      code.textContent = uri;
      el.appendChild(code);
      return;
    }
    try {
      const qr = window.qrcode(0, 'M');
      qr.addData(uri);
      qr.make();
      // parse the SVG via DOMParser + adopt its <svg> root
      // instead of `el.innerHTML = ...`. qrcode-generator's output
      // is trusted local lib data, but `innerHTML` is the
      // documented red-flag pattern for content-from-data flows;
      // matches how the rest of the SPA injects DOM.
      const svgText = qr.createSvgTag({cellSize: 6, margin: 4, scalable: true});
      const parsed = new DOMParser().parseFromString(svgText, 'image/svg+xml');
      const root = parsed.documentElement;
      // DOMParser surfaces parse errors via a <parsererror> root —
      // fall back to the textContent-fallback below in that case
      // so the operator still sees the otpauth URI to type by hand.
      if (!root || root.tagName.toLowerCase() === 'parsererror') {
        throw new Error('SVG parse error');
      }
      el.replaceChildren(document.adoptNode(root));
    } catch (_) {
      const code = document.createElement('code');
      code.className = 'mono totp-qr-fallback';
      code.textContent = uri;
      el.appendChild(code);
    }
  },

  cancelTotpEnrol() {
    this.totpEnrol = {secret: '', uri: '', code: ''};
    this.totpEnrolStage = 'idle';
  },

  async confirmTotpEnrol() {
    const code = (this.totpEnrol.code || '').replace(/\s/g, '').trim();
    if (!/^\d{6}$/.test(code)) {
      this.showToast(this.t('toasts.totp_invalid_code'), 'error');
      return;
    }
    this.totpEnrolBusy = true;
    try {
      const r = await fetch('/api/me/totp/enroll-confirm', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({secret: this.totpEnrol.secret, code}),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.totp_enroll_failed'), 'error');
        return;
      }
      const j = await r.json();
      this.totpRevealCodes = j.backup_codes || [];
      this.totpEnrolStage = 'reveal';
      this.totpEnrol = {secret: '', uri: '', code: ''};
      await this.loadTotpStatus();
      if (this.me) {
        this.me.totp = {...(this.me.totp || {}), enabled: true};
      }
      this.showToast(this.t('toasts.totp_enabled'));
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.totpEnrolBusy = false;
    }
  },

  closeTotpReveal() {
    this.totpRevealCodes = [];
    this.totpEnrolStage = 'idle';
  },

  downloadBackupCodes(codes) {
    const list = (codes && codes.length) ? codes : this.totpRevealCodes;
    if (!list || !list.length) {
      return;
    }
    const blob = new Blob([list.join('\n') + '\n'], {type: 'text/plain'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'omnigrid-backup-codes.txt';
    a.click();
    URL.revokeObjectURL(url);
  },

  async regenerateTotpCodes() {
    const res = await Swal.fire({
      title: this.t('settings.profile.totp.regen_confirm_title'),
      text: this.t('settings.profile.totp.regen_confirm_body'),
      icon: 'warning', showCancelButton: true,
      confirmButtonText: this.t('settings.profile.totp.regen_confirm_button'),
      cancelButtonText: this.t('actions.cancel'),
    });
    if (!res.isConfirmed) {
      return;
    }
    try {
      const r = await fetch('/api/me/totp/regenerate-codes', {method: 'POST'});
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.totp_regen_failed'), 'error');
        return;
      }
      const j = await r.json();
      this.totpRevealCodes = j.backup_codes || [];
      this.totpEnrolStage = 'reveal';
      await this.loadTotpStatus();
      this.showToast(this.t('toasts.totp_regen_ok'));
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  async disableTotpSelf() {
    if (this.totp.required) {
      this.showToast(this.t('toasts.totp_required_no_disable'), 'error');
      return;
    }
    const res = await Swal.fire({
      title: this.t('settings.profile.totp.disable_confirm_title'),
      text: this.t('settings.profile.totp.disable_confirm_body'),
      icon: 'warning',
      input: 'password',
      inputLabel: this.t('settings.profile.totp.disable_password_label'),
      inputAttributes: {autocapitalize: 'off', autocorrect: 'off'},
      showCancelButton: true,
      confirmButtonText: this.t('settings.profile.totp.disable_confirm_button'),
      cancelButtonText: this.t('actions.cancel'),
    });
    if (!res.isConfirmed || !res.value) {
      return;
    }
    this.totpDisableBusy = true;
    try {
      const r = await fetch('/api/me/totp/disable', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({password: res.value}),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.totp_disable_failed'), 'error');
        return;
      }
      await this.loadTotpStatus();
      if (this.me) {
        this.me.totp = {...(this.me.totp || {}), enabled: false};
      }
      this.showToast(this.t('toasts.totp_disabled'));
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    } finally {
      this.totpDisableBusy = false;
    }
  },

  async adminDisableTotp(u) {
    if (u.auth_source !== 'local') {
      this.showToast(this.t('toasts.authentik_change_pw_here'), 'error');
      return;
    }
    const res = await Swal.fire({
      title: this.t('admin.users.totp_disable_confirm_title', {name: u.username}),
      text: this.t('admin.users.totp_disable_confirm_body'),
      icon: 'warning', showCancelButton: true,
      confirmButtonText: this.t('admin.users.totp_disable_confirm_button'),
      cancelButtonText: this.t('actions.cancel'),
      confirmButtonColor: this._cssVar('--danger'),
    });
    if (!res.isConfirmed) {
      return;
    }
    try {
      const r = await fetch('/api/users/' + u.id + '/disable-totp', {
        method: 'POST',
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.totp_disable_failed'), 'error');
        return;
      }
      this.showToast(this.t('toasts.totp_admin_disabled'));
      await this.loadUsers();
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },

  // Per-user force-2FA toggle. Admin flips this flag to make
  // a specific user MUST have 2FA on regardless of the global
  // role-policy. Forcing on a user who hasn't enrolled yet causes
  // their next login to land in the forced-enrolment QR flow.
  async adminForceTotp(u, force) {
    if (u.auth_source !== 'local') {
      this.showToast(this.t('toasts.authentik_change_pw_here'), 'error');
      return;
    }
    const titleKey = force
      ? 'admin.users.totp_force_confirm_title'
      : 'admin.users.totp_unforce_confirm_title';
    const bodyKey = force
      ? 'admin.users.totp_force_confirm_body'
      : 'admin.users.totp_unforce_confirm_body';
    const res = await Swal.fire({
      title: this.t(titleKey, {name: u.username}),
      text: this.t(bodyKey),
      icon: 'question', showCancelButton: true,
      confirmButtonText: this.t('actions.confirm'),
      cancelButtonText: this.t('actions.cancel'),
      confirmButtonColor: this._cssVar('--primary'),
    });
    if (!res.isConfirmed) {
      return;
    }
    try {
      const r = await fetch('/api/users/' + u.id + '/totp-force', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({force: !!force}),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        this.showToast(j.detail || this.t('toasts.totp_force_failed'), 'error');
        return;
      }
      this.showToast(this.t(force ? 'toasts.totp_forced_on' : 'toasts.totp_force_cleared'));
      await this.loadUsers();
    } catch (_) {
      this.showToast(this.t('toasts.network_error'), 'error');
    }
  },
  _totpPolicySnapshot() {
    const s = this.settings || {};
    return JSON.stringify({
      allowed: !!s.totp_allowed,
      required_for_admins: !!s.totp_required_for_admins,
      required_for_users: !!s.totp_required_for_users,
      lockout_max_failures: +s.totp_lockout_max_failures || 5,
      lockout_minutes: +s.totp_lockout_minutes || 15,
      // Passkey master toggle joins the same dirty/baseline group
      // as the TOTP policy fields so a single Save covers both
      // sub-systems.
      passkeys_allowed: !!s.passkeys_allowed,
    });
  },
  totpPolicyDirty() {
    return this._totpPolicyBaseline !== this._totpPolicySnapshot();
  },
};
