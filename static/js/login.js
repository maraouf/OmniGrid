/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// Login-page controller. Kept separate from /js/app.js because this page
// has no Alpine — it's a vanilla form submission, plus a data-i18n DOM
// sweep, plus an SSO-button toggle that polls /api/auth/providers.
//
// The main i18n helper (window.I18N + window.t) is already loaded via
// /js/i18n.js by the time we run; we just layer a data-i18n applier on
// top so markup like `<h1 data-i18n="app.name">OmniGrid</h1>` renders
// translated text without Alpine.
//
// The form supports a multi-step TOTP flow when the backend
// returns step="totp_required" or step="totp_setup_required" instead
// of an immediate cookie. State machine:
// password -> {ok}                                    -> redirect
//           -> {step: totp_required, challenge_id}     -> totp form
//           -> {step: totp_setup_required, secret,
//               provisioning_uri, challenge_id}        -> setup form
// totp -> {ok}              -> redirect
//      -> {ok, backup_codes} -> reveal codes -> redirect on continue
// noinspection NestedFunctionCallJS,FunctionTooLongJS,OverlyComplexFunctionJS,AnonymousFunctionJS,ConstantOnRightSideOfComparisonJS,MagicNumberJS,ChainedFunctionCallJS,DuplicatedCode,NestedFunctionJS,FunctionWithMultipleLoopsJS,IfStatementWithTooManyBranchesJS,FunctionWithMultipleReturnPointsJS,ConditionalExpressionJS,OverlyComplexBooleanExpressionJS

function applyI18nDom() {
  const els = document.querySelectorAll('[data-i18n]');
  for (let i = 0; i < els.length; i++) {
    const el = els[i];
    const key = el.getAttribute('data-i18n');
    const v = window.t ? window.t(key) : key;
    if (v && v !== key) {
      el.textContent = v;
    }
  }
  // The <title> element only carries `data-i18n-title` (writing
  // `data-i18n` on it would clobber the textContent path with the same
  // string but skip translating the tab title once it's first cached
  // by the browser). Update both the title element AND `document.title`
  // so the tab label refreshes immediately on language change.
  const titleEls = document.querySelectorAll('[data-i18n-title]');
  for (let i = 0; i < titleEls.length; i++) {
    const el = titleEls[i];
    const key = el.getAttribute('data-i18n-title');
    const v = window.t ? window.t(key) : key;
    if (v && v !== key) {
      el.textContent = v;
      if (el.tagName && el.tagName.toLowerCase() === 'title') {
        document.title = v;
      }
    }
  }
}

(async function bootI18nDom() {
  // Wait for the i18n helper's boot promise so the DOM sweep runs after
  // the user's preferred language is loaded. Silent on failure — English
  // markup stays in place as a fallback.
  try {
    await window.__i18nReady;
  } catch (_) {
  }
  applyI18nDom();
})();

(function () {
  // Mutable refs — these point at the live DOM nodes after each form
  // swap. The TOTP step swaps the form to drop the password-handler
  // listener; rebind these so showErr / button mutations still target
  // the visible card.
  const refs = {
    form: document.getElementById('login'), btn: document.getElementById('btn'), err: document.getElementById('err'), ver: document.getElementById('ver'), ssoWrap: document.getElementById('ssoWrap'), ssoBtn: document.getElementById('ssoBtn'),
  };

  // Multi-step state. Holds the payload from the password step
  // so the second-step submit can post the challenge_id back.
  let totpState = null;

  function tx(key, fallback, args) {
    return window.t ? window.t(key, args) : fallback;
  }

  function showErr(msg) {
    if (!refs.err) {
      return;
    }
    refs.err.textContent = msg;
    refs.err.classList.add('show');
  }

  function clearErr() {
    if (!refs.err) {
      return;
    }
    refs.err.classList.remove('show');
    refs.err.textContent = '';
  }

  function nextPath() {
    // Open-redirect defence — every consumer of `nextPath()` flows the
    // value into `location.href`, so an attacker-controlled `?next=`
    // query param could otherwise navigate the just-authenticated
    // session at an arbitrary domain (phishing). Hardening (closes
    // CodeQL `js/client-side-unvalidated-url-redirection`):
    // 1. Reject anything that isn't `/`-prefixed (absolute URLs).
    // 2. Reject `//host` (protocol-relative — same risk as 1).
    // 3. Reject `/\\host` and `\\host` (browsers normalise backslashes
    //    to forward slashes in URL parsing — `/\\evil.com` becomes
    //    `//evil.com` and breaks (1) + (2)'s naive substring check).
    // 4. As a final defence-in-depth, resolve the value against
    //    `location.origin` via the `URL` constructor and confirm the
    //    result still lives at the SAME origin — catches every
    //    remaining edge case (e.g. zero-width-space injection
    //    inside the leading slash like `/U+200B//evil.com` —
    //    parses as a relative path until the browser strips the
    //    invisible char, IDN homographs, mixed-encoding nasties).
    // Falls back to `/` (the SPA root) on any rejection.
    const raw = new URLSearchParams(location.search).get('next') || '/';
    if (!raw.startsWith('/')) {
      return '/';
    }
    if (raw.startsWith('//') || raw.startsWith('/\\') || raw.startsWith('\\')) {
      return '/';
    }
    try {
      const resolved = new URL(raw, location.origin);
      if (resolved.origin !== location.origin) {
        return '/';
      }
      // Strip the origin so the return value remains a path-only
      // string that downstream `location.href = nextPath()` consumers
      // treat as a same-origin nav unconditionally.
      return resolved.pathname + resolved.search + resolved.hash;
    } catch (_) {
      return '/';
    }
  }

  // Show version so operators can sanity-check which build is live.
  fetch('/api/version').then(r => r.ok ? r.json() : null).then(v => {
    if (v && v.version && refs.ver) {
      refs.ver.textContent = 'v' + v.version;
    }
  }).catch(() => {
  });

  // Advertise SSO once we know it's configured.
  fetch('/api/auth/providers').then(r => r.ok ? r.json() : null).then(p => {
    if (p && p.oidc && refs.ssoBtn && refs.ssoWrap) {
      refs.ssoBtn.href = '/api/oidc/login?next=' + encodeURIComponent(nextPath());
      refs.ssoWrap.hidden = false;
    }
  }).catch(() => {
  });

  // Bind the password-step submit. Replaced wholesale on totp/setup
  // step via swapForm().
  bindPasswordStep();

  function bindPasswordStep() {
    refs.form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearErr();
      refs.btn.disabled = true;
      refs.btn.textContent = tx('login.signing_in', 'Signing in…');
      try {
        const body = new URLSearchParams({
          username: document.getElementById('u').value, password: document.getElementById('p').value,
        });
        const r = await fetch('/api/local-auth/login', {
          method: 'POST', body, credentials: 'same-origin',
        });
        if (r.ok) {
          let j = null;
          try {
            j = await r.json();
          } catch (_) {
          }
          if (j && j.step === 'totp_required') {
            totpState = {
              kind: 'totp_required', challenge_id: j.challenge_id, username: j.username, methods: Array.isArray(j.methods) ? j.methods : ['totp'],
            };
            renderTotpForm();
            return;
          }
          if (j && j.step === 'totp_setup_required') {
            totpState = {
              kind: 'totp_setup_required', challenge_id: j.challenge_id, username: j.username, secret: j.secret, provisioning_uri: j.provisioning_uri,
            };
            renderTotpSetupForm();
            return;
          }
          // Legacy single-factor success.
          location.href = nextPath();
          return;
        }
        if (r.status === 423) {
          const j = await r.json().catch(() => ({}));
          showErr(j.detail || tx('login.totp_locked', 'Account locked. Try again later.'));
        } else if (r.status === 429) {
          const j = await r.json().catch(() => ({}));
          showErr(j.detail || tx('login.too_many_attempts', 'Too many attempts. Try again shortly.'));
        } else if (r.status === 403) {
          // — disabled-account case (specialised path
          // returns 403 with a clear `detail` message). Surface the
          // backend's text instead of the generic "Sign-in failed".
          // Other 403s would also pass through here — that's fine
          // because they're authorisation-level rejections that DO
          // benefit from the specific message.
          const j = await r.json().catch(() => ({}));
          showErr(j.detail || tx('login.account_disabled', 'Account is disabled. Contact your administrator.'));
        } else if (r.status === 401) {
          showErr(tx('login.invalid_credentials', 'Invalid username or password.'));
        } else {
          showErr(tx('login.sign_in_failed', 'Sign-in failed (' + r.status + '). Try again.', {status: r.status}));
        }
        // — clear password field on ANY non-success response.
        // Operator request: don't leave the failing password sitting
        // in the input where a quick second click would re-submit
        // the same value, OR where it could be glanced at by anyone
        // standing behind the screen. Username stays so the operator
        // doesn't have to re-type both. The password field is also
        // re-focused for the next attempt.
        try {
          const pw = document.getElementById('p');
          if (pw) {
            pw.value = '';
            pw.focus();
          }
        } catch (_) {
        }
      } catch (_) {
        showErr(tx('login.network_error', 'Network error. Try again.'));
        // Same clear-on-error UX for network failures.
        try {
          const pw = document.getElementById('p');
          if (pw) {
            pw.value = '';
            pw.focus();
          }
        } catch (_) {
        }
      } finally {
        refs.btn.disabled = false;
        refs.btn.textContent = tx('login.sign_in', 'Sign in');
      }
    });
  }

  // Replace the form node so any old listeners are gone, then bind a
  // fresh submit handler. Returns the new form so callers can append
  // children before binding.
  function swapForm(newSubmit) {
    const old = refs.form;
    const fresh = old.cloneNode(true);
    old.parentNode.replaceChild(fresh, old);
    refs.form = fresh;
    refs.btn = fresh.querySelector('#btn');
    refs.err = fresh.querySelector('#err');
    refs.ssoWrap = fresh.querySelector('#ssoWrap');
    refs.ssoBtn = fresh.querySelector('#ssoBtn');
    refs.ver = fresh.querySelector('#ver');
    fresh.addEventListener('submit', async (e) => {
      e.preventDefault();
      await newSubmit();
    });
  }

  function hideEl(el) {
    if (el) {
      el.style.display = 'none';
    }
  }

  function _showEl(el) {
    if (el) {
      el.style.display = '';
    }
  }

  // ----------------------------------------------------------------
  // WebAuthn / passkey helpers. The wire shape uses base64url
  // strings for every byte field so JSON-over-fetch round-trips
  // cleanly. Browsers expose the underlying buffers as ArrayBuffer;
  // these helpers hide the conversion at the API boundary.
  // ----------------------------------------------------------------
  function b64uEncode(buf) {
    // — `btoa` rejects bytes whose values are above 0xFF
    // (`InvalidCharacterError`). Real-world: a future server that
    // populates `userHandle` with a UTF-8 marker, or a malformed
    // ArrayBuffer from a non-spec-compliant authenticator. Wrap in
    // try/catch and re-throw a diagnostic that the WebAuthn submit
    // handler surfaces via `showErr` instead of dying as "Sign-in
    // failed" with no hint.
    if (buf == null) {
      throw new Error('WebAuthn: encoder received null/undefined buffer');
    }
    const b = new Uint8Array(buf);
    let s = '';
    for (let i = 0; i < b.length; i++) {
      s += String.fromCharCode(b[i]);
    }
    try {
      return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    } catch (_) {
      throw new Error('WebAuthn: cannot encode buffer (non-Latin1 bytes)');
    }
  }

  function b64uDecode(s) {
    // validate the input before handing it to atob so a
    // malformed `allowCredentials[i].id` from the server surfaces as a
    // diagnostic operator-readable error instead of a generic
    // `InvalidCharacterError`. Empty / non-string is a programming error;
    // characters outside the base64url charset means the server JSON is
    // malformed (regression in the credential serializer); a length that
    // doesn't pad to a multiple of 4 means the same.
    if (typeof s !== 'string' || s.length === 0) {
      throw new Error('WebAuthn: server sent empty / non-string credential id');
    }
    if (!/^[A-Za-z0-9_-]+$/.test(s)) {
      throw new Error('WebAuthn: server sent malformed base64url credential id (charset)');
    }
    let padded = s.replace(/-/g, '+').replace(/_/g, '/');
    while (padded.length % 4) {
      padded += '=';
    }
    let bin;
    try {
      bin = atob(padded);
    } catch (_) {
      throw new Error('WebAuthn: server sent malformed base64url credential id (atob)');
    }
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) {
      out[i] = bin.charCodeAt(i);
    }
    return out.buffer;
  }

  function webauthnSupported() {
    return !!(window.PublicKeyCredential && navigator.credentials);
  }

  function buildPublicKeyOptions(opts) {
    // Convert the JSON options we got from the server (base64url strings)
    // into the ArrayBuffer fields navigator.credentials.get expects.
    const out = Object.assign({}, opts);
    out.challenge = b64uDecode(opts.challenge);
    if (Array.isArray(opts.allowCredentials)) {
      out.allowCredentials = opts.allowCredentials.map(c => ({
        type: c.type, id: b64uDecode(c.id), transports: c.transports,
      }));
    }
    return out;
  }

  function buildAssertionResponse(cred) {
    return {
      id: cred.id, rawId: b64uEncode(cred.rawId), type: cred.type, authenticatorAttachment: cred.authenticatorAttachment || null, clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {}, response: {
        authenticatorData: b64uEncode(cred.response.authenticatorData), clientDataJSON: b64uEncode(cred.response.clientDataJSON), signature: b64uEncode(cred.response.signature), userHandle: cred.response.userHandle ? b64uEncode(cred.response.userHandle) : null,
      },
    };
  }

  async function attemptPasskeyLogin() {
    clearErr();
    if (!webauthnSupported()) {
      showErr(tx('login.passkey_browser_unsupported', "This browser doesn't support passkeys."));
      return;
    }
    const passkeyBtn = document.getElementById('login-passkey-btn');
    if (passkeyBtn) {
      passkeyBtn.disabled = true;
      passkeyBtn.textContent = tx('login.passkey_prompting', 'Confirm on your device…');
    }
    try {
      const startResp = await fetch('/api/local-auth/webauthn-start', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({challenge_id: totpState.challenge_id}), credentials: 'same-origin',
      });
      if (!startResp.ok) {
        const j = await startResp.json().catch(() => ({}));
        showErr(j.detail || tx('login.passkey_failed', 'Passkey sign-in failed.'));
        return;
      }
      const startJ = await startResp.json();
      // — RP-ID mismatch short-circuit. When EVERY stored
      // credential was registered under a different domain, the
      // browser will accept the assertion challenge but its local
      // credential lookup returns nothing — falling through to the
      // hybrid (QR) flow with no explanation. Surface a clear hint
      // BEFORE we trigger navigator.credentials.get so the operator
      // doesn't waste a click on the QR popup. The hint includes
      // the rp_id(s) the orphaned credentials were registered under
      // so the operator can tell which old domain they came from.
      if (startJ.rp_id_mismatch) {
        const orphans = (startJ.orphaned_credentials || [])
          .map(o => o.friendly_name + ' (' + (o.rp_id || 'unknown') + ')')
          .join(', ');
        const cur = startJ.current_rp_id || (location && location.hostname) || '';
        showErr(tx('login.passkey_rp_id_mismatch', 'Your passkeys were registered under a different domain and can\'t be used here. Sign in another way (TOTP / password recovery), then re-enrol them from Profile → Security on the new domain ({current}). Orphaned: {orphans}.', {current: cur, orphans: orphans || '—'},));
        return;
      }
      const publicKey = buildPublicKeyOptions(startJ.options);
      let cred;
      try {
        // noinspection JSCheckFunctionSignatures
        cred = await navigator.credentials.get({publicKey});
      } catch (_) {
        showErr(tx('login.passkey_failed', 'Passkey sign-in failed.'));
        return;
      }
      if (!cred) {
        showErr(tx('login.passkey_failed', 'Passkey sign-in failed.'));
        return;
      }
      const finishResp = await fetch('/api/local-auth/webauthn-finish', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
          challenge_id: startJ.login_id, credential: buildAssertionResponse(cred),
        }), credentials: 'same-origin',
      });
      if (finishResp.ok) {
        location.href = nextPath();
        return;
      }
      const j = await finishResp.json().catch(() => ({}));
      showErr(j.detail || tx('login.passkey_failed', 'Passkey sign-in failed.'));
    } catch (e) {
      // — surface diagnostic WebAuthn errors from the b64u
      // helpers (`buildPublicKeyOptions` / `buildAssertionResponse`)
      // instead of collapsing them into the generic "Network error"
      // toast. Anything else stays generic.
      const msg = (e && typeof e.message === 'string' && e.message.startsWith('WebAuthn:')) ? tx('login.passkey_data_error', 'Passkey data error: {error}', {error: e.message}) : tx('login.network_error', 'Network error.');
      showErr(msg);
    } finally {
      if (passkeyBtn) {
        passkeyBtn.disabled = false;
        passkeyBtn.textContent = tx('login.passkey_use_button', 'Use a passkey');
      }
    }
  }

  function renderTotpForm() {
    swapForm(async () => {
      // No code input rendered → submit was triggered by Enter on a
      // disabled / hidden Verify button. Bail without erroring.
      const codeEl = document.getElementById('totp-code');
      if (!codeEl) {
        return;
      }
      clearErr();
      refs.btn.disabled = true;
      refs.btn.textContent = tx('login.verifying', 'Verifying…');
      try {
        const body = new URLSearchParams({
          challenge_id: totpState.challenge_id, code: codeEl.value,
        });
        const r = await fetch('/api/local-auth/totp', {
          method: 'POST', body, credentials: 'same-origin',
        });
        if (r.ok) {
          location.href = nextPath();
          return;
        }
        if (r.status === 423) {
          const j = await r.json().catch(() => ({}));
          showErr(j.detail || tx('login.totp_locked', 'Account locked.'));
        } else if (r.status === 429) {
          const j = await r.json().catch(() => ({}));
          showErr(j.detail || tx('login.too_many_attempts', 'Too many attempts.'));
        } else if (r.status === 401) {
          showErr(tx('login.totp_invalid_code', 'Invalid code. Try again.'));
        } else if (r.status === 400) {
          showErr(tx('login.totp_expired', 'Verification expired. Sign in again.'));
          setTimeout(() => location.reload(), 2000);
        } else {
          showErr(tx('login.sign_in_failed', 'Sign-in failed (' + r.status + ').', {status: r.status}));
        }
      } catch (_) {
        showErr(tx('login.network_error', 'Network error.'));
      } finally {
        refs.btn.disabled = false;
        refs.btn.textContent = tx('login.verify', 'Verify');
      }
    });

    // After swap: hide the username/password rows that the cloned form
    // still carries, then inject the totp block above the submit btn.
    hideEl(refs.form.querySelector('label[for="u"]'));
    hideEl(refs.form.querySelector('#u'));
    hideEl(refs.form.querySelector('label[for="p"]'));
    hideEl(refs.form.querySelector('#p'));
    hideEl(refs.form.querySelector('#ssoWrap'));

    const methods = (totpState && totpState.methods) || ['totp'];
    const hasTotp = methods.indexOf('totp') >= 0;
    const hasPasskey = methods.indexOf('webauthn') >= 0 && webauthnSupported();
    const onlyPasskey = !hasTotp && hasPasskey;

    const block = document.createElement('div');
    block.id = 'totp-block';

    if (hasPasskey) {
      // Passkey button rendered FIRST so it's the dominant affordance —
      // the operator wants the password-manager / hardware-key path
      // ahead of typed codes when both are available.
      const pkWrap = document.createElement('div');
      pkWrap.id = 'login-passkey-wrap';
      pkWrap.className = 'login-passkey-wrap';
      const pkBtn = document.createElement('button');
      pkBtn.type = 'button';
      pkBtn.id = 'login-passkey-btn';
      pkBtn.className = 'btn-passkey';
      pkBtn.textContent = tx('login.passkey_use_button', 'Use a passkey');
      pkBtn.addEventListener('click', (e) => {
        e.preventDefault();
        attemptPasskeyLogin();
      });
      pkWrap.appendChild(pkBtn);
      block.appendChild(pkWrap);

      if (hasTotp) {
        const sep = document.createElement('div');
        sep.className = 'sso-divider';
        sep.innerHTML = '<span>' + tx('login.or', 'or') + '</span>';
        block.appendChild(sep);
      }
    }

    if (hasTotp) {
      const codeBlock = document.createElement('div');
      codeBlock.innerHTML = '<p class="sub" id="totp-hint"></p>' + '<label for="totp-code" id="totp-code-label"></label>' + '<input id="totp-code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required />' + '<a href="#" id="totp-toggle-mode" class="totp-link"></a>';
      block.appendChild(codeBlock);
    }
    refs.form.insertBefore(block, refs.btn);

    if (hasTotp) {
      document.getElementById('totp-hint').textContent = tx('login.totp_required_hint', 'Enter the 6-digit code from your authenticator app.');
      document.getElementById('totp-code-label').textContent = tx('login.totp_code_label', 'Authenticator code');
      document.getElementById('totp-toggle-mode').textContent = tx('login.totp_use_backup', 'Use a backup code instead');
      const codeInp = document.getElementById('totp-code');
      if (codeInp && !hasPasskey) {
        codeInp.focus();
      }
      refs.btn.textContent = tx('login.verify', 'Verify');
    } else if (onlyPasskey) {
      // No code path -- hide the Verify submit, kick the passkey
      // ceremony immediately so the operator just needs to confirm
      // on their device.
      hideEl(refs.btn);
      const hint = document.createElement('p');
      hint.className = 'sub';
      hint.id = 'passkey-only-hint';
      hint.textContent = tx('login.passkey_only_hint', 'Sign in with the passkey you registered.');
      refs.form.insertBefore(hint, block);
      // Auto-trigger after a tick so any browser focus race settles.
      setTimeout(attemptPasskeyLogin, 50);
    }

    if (hasTotp) {
      let useBackup = false;
      document.getElementById('totp-toggle-mode').addEventListener('click', (ev) => {
        ev.preventDefault();
        useBackup = !useBackup;
        const inp = document.getElementById('totp-code');
        if (useBackup) {
          inp.maxLength = 20;
          inp.removeAttribute('inputmode');
          inp.placeholder = tx('login.totp_backup_placeholder', 'XXXX YYYY');
          document.getElementById('totp-hint').textContent = tx('login.totp_backup_hint', 'Enter one of your saved backup codes.');
          document.getElementById('totp-toggle-mode').textContent = tx('login.totp_use_code', 'Use authenticator code instead');
        } else {
          inp.maxLength = 6;
          inp.setAttribute('inputmode', 'numeric');
          inp.placeholder = '';
          document.getElementById('totp-hint').textContent = tx('login.totp_required_hint', 'Enter the 6-digit code from your authenticator app.');
          document.getElementById('totp-toggle-mode').textContent = tx('login.totp_use_backup', 'Use a backup code instead');
        }
        inp.value = '';
        inp.focus();
      });
    }
  }

  function renderTotpSetupForm() {
    swapForm(async () => {
      clearErr();
      refs.btn.disabled = true;
      refs.btn.textContent = tx('login.verifying', 'Verifying…');
      try {
        const body = new URLSearchParams({
          challenge_id: totpState.challenge_id, code: document.getElementById('totp-code').value,
        });
        const r = await fetch('/api/local-auth/totp-setup-confirm', {
          method: 'POST', body, credentials: 'same-origin',
        });
        if (r.ok) {
          const j = await r.json().catch(() => ({}));
          renderBackupCodesReveal(j.backup_codes || []);
          return;
        }
        if (r.status === 401) {
          showErr(tx('login.totp_invalid_code', 'Invalid code. Try again.'));
        } else if (r.status === 429) {
          const j = await r.json().catch(() => ({}));
          showErr(j.detail || tx('login.too_many_attempts', 'Too many attempts.'));
        } else if (r.status === 400) {
          showErr(tx('login.totp_expired', 'Setup expired. Sign in again.'));
          setTimeout(() => location.reload(), 2000);
        } else {
          showErr(tx('login.sign_in_failed', 'Sign-in failed (' + r.status + ').', {status: r.status}));
        }
      } catch (_) {
        showErr(tx('login.network_error', 'Network error.'));
      } finally {
        refs.btn.disabled = false;
        refs.btn.textContent = tx('login.verify_and_enable', 'Verify and enable');
      }
    });

    hideEl(refs.form.querySelector('label[for="u"]'));
    hideEl(refs.form.querySelector('#u'));
    hideEl(refs.form.querySelector('label[for="p"]'));
    hideEl(refs.form.querySelector('#p'));
    hideEl(refs.form.querySelector('#ssoWrap'));

    const block = document.createElement('div');
    block.id = 'totp-setup-block';
    block.innerHTML = '<p class="sub" id="totp-setup-hint"></p>' + '<div id="totp-qr" class="totp-qr"></div>' + '<div class="totp-secret-row">' + '<span id="totp-secret-label"></span>' + '<code id="totp-secret-value" class="mono"></code>' + '</div>' + '<label for="totp-code" id="totp-code-label"></label>' + '<input id="totp-code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" autofocus required />';
    refs.form.insertBefore(block, refs.btn);

    document.getElementById('totp-setup-hint').textContent = tx('login.totp_setup_hint', 'Two-factor authentication is required. Scan the QR with your authenticator app, then enter the generated code below.');
    document.getElementById('totp-secret-label').textContent = tx('login.totp_secret_label', 'Or enter manually:');
    document.getElementById('totp-secret-value').textContent = totpState.secret;
    document.getElementById('totp-code-label').textContent = tx('login.totp_code_label', 'Authenticator code');
    refs.btn.textContent = tx('login.verify_and_enable', 'Verify and enable');

    renderQr(document.getElementById('totp-qr'), totpState.provisioning_uri);
  }

  // Show the 10 backup codes one time after a successful setup.
  function renderBackupCodesReveal(codes) {
    while (refs.form.firstChild) {
      refs.form.removeChild(refs.form.firstChild);
    }

    const h1 = document.createElement('h1');
    h1.textContent = tx('login.totp_backup_title', 'Save these backup codes');
    refs.form.appendChild(h1);
    const sub = document.createElement('p');
    sub.className = 'sub';
    sub.textContent = tx('login.totp_backup_warning', 'Store these somewhere safe. Each code can be used once if you lose access to your authenticator app.');
    refs.form.appendChild(sub);

    const list = document.createElement('div');
    list.className = 'totp-backup-list mono';
    codes.forEach((c) => {
      const item = document.createElement('div');
      item.className = 'totp-backup-item';
      item.textContent = c;
      list.appendChild(item);
    });
    refs.form.appendChild(list);

    const dlBtn = document.createElement('button');
    dlBtn.type = 'button';
    dlBtn.className = 'btn-link';
    dlBtn.textContent = tx('login.totp_backup_download', 'Download as .txt');
    dlBtn.addEventListener('click', () => {
      const blob = new Blob([codes.join('\n') + '\n'], {type: 'text/plain'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'omnigrid-backup-codes.txt';
      a.click();
      URL.revokeObjectURL(url);
    });
    refs.form.appendChild(dlBtn);

    const continueBtn = document.createElement('button');
    continueBtn.type = 'button';
    continueBtn.textContent = tx('login.totp_continue', 'Continue');
    continueBtn.addEventListener('click', () => {
      location.href = nextPath();
    });
    refs.form.appendChild(continueBtn);
  }

  // Render an otpauth:// URI to a QR via window.qrcode (qrcode-generator).
  // Falls back to a raw text display so operators can still scan via
  // their auth-app's "import URI" mode if the lib failed to load.
  function renderQr(el, uri) {
    el.textContent = '';
    if (!window.qrcode) {
      const fallback = document.createElement('code');
      fallback.className = 'mono totp-qr-fallback';
      fallback.textContent = uri;
      el.appendChild(fallback);
      return;
    }
    try {
      const qr = window.qrcode(0, 'M');
      // noinspection JSCheckFunctionSignatures
      qr.addData(uri);
      qr.make();
      // — parse the SVG via DOMParser + adopt its <svg> root
      // instead of `innerHTML = ...`. qrcode-generator's output is
      // trusted local lib data, but `innerHTML` is the documented
      // red-flag pattern for content-from-data flows. Falls through
      // to the textContent fallback when DOMParser surfaces a
      // <parsererror>.
      const svgText = qr.createSvgTag({cellSize: 6, margin: 4, scalable: true});
      const parsed = new DOMParser().parseFromString(svgText, 'image/svg+xml');
      const root = parsed.documentElement;
      if (!root || root.tagName.toLowerCase() === 'parsererror') {
        throw new Error('SVG parse error');
      }
      el.replaceChildren(document.adoptNode(root));
    } catch (_) {
      const fallback = document.createElement('code');
      fallback.className = 'mono totp-qr-fallback';
      fallback.textContent = uri;
      el.appendChild(fallback);
    }
  }
})();
