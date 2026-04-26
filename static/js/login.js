// Login-page controller. Kept separate from /js/app.js because this page
// has no Alpine — it's a vanilla form submission, plus a data-i18n DOM
// sweep, plus an SSO-button toggle that polls /api/auth/providers.
//
// The main i18n helper (window.I18N + window.t) is already loaded via
// /js/i18n.js by the time we run; we just layer a data-i18n applier on
// top so markup like `<h1 data-i18n="app.name">OmniGrid</h1>` renders
// translated text without Alpine.
//
// The form supports a multi-step TOTP flow (#345) when the backend
// returns step="totp_required" or step="totp_setup_required" instead
// of an immediate cookie. State machine:
//   password -> {ok}                                    -> redirect
//             -> {step: totp_required, challenge_id}     -> totp form
//             -> {step: totp_setup_required, secret,
//                 provisioning_uri, challenge_id}        -> setup form
//   totp -> {ok}              -> redirect
//        -> {ok, backup_codes} -> reveal codes -> redirect on continue

function applyI18nDom() {
  const els = document.querySelectorAll('[data-i18n]');
  for (let i = 0; i < els.length; i++) {
    const el = els[i];
    const key = el.getAttribute('data-i18n');
    const v = window.t ? window.t(key) : key;
    if (v && v !== key) el.textContent = v;
  }
}

(async function bootI18nDom() {
  // Wait for the i18n helper's boot promise so the DOM sweep runs after
  // the user's preferred language is loaded. Silent on failure — English
  // markup stays in place as a fallback.
  try { await window.__i18nReady; } catch (_) {}
  applyI18nDom();
})();

(function () {
  // Mutable refs — these point at the live DOM nodes after each form
  // swap. The TOTP step swaps the form to drop the password-handler
  // listener; rebind these so showErr / button mutations still target
  // the visible card.
  const refs = {
    form:    document.getElementById('login'),
    btn:     document.getElementById('btn'),
    err:     document.getElementById('err'),
    ver:     document.getElementById('ver'),
    ssoWrap: document.getElementById('ssoWrap'),
    ssoBtn:  document.getElementById('ssoBtn'),
  };

  // Multi-step state (#345). Holds the payload from the password step
  // so the second-step submit can post the challenge_id back.
  let totpState = null;

  function tx(key, fallback, args) {
    return window.t ? window.t(key, args) : fallback;
  }

  function showErr(msg) {
    if (!refs.err) return;
    refs.err.textContent = msg;
    refs.err.classList.add('show');
  }
  function clearErr() {
    if (!refs.err) return;
    refs.err.classList.remove('show');
    refs.err.textContent = '';
  }

  function nextPath() {
    const p = new URLSearchParams(location.search).get('next') || '/';
    // Reject absolute / protocol-relative URLs — open-redirect defense.
    if (!p.startsWith('/') || p.startsWith('//')) return '/';
    return p;
  }

  // Show version so operators can sanity-check which build is live.
  fetch('/api/version').then(r => r.ok ? r.json() : null).then(v => {
    if (v && v.version && refs.ver) refs.ver.textContent = 'v' + v.version;
  }).catch(() => {});

  // Advertise SSO once we know it's configured.
  fetch('/api/auth/providers').then(r => r.ok ? r.json() : null).then(p => {
    if (p && p.oidc && refs.ssoBtn && refs.ssoWrap) {
      refs.ssoBtn.href = '/api/oidc/login?next=' + encodeURIComponent(nextPath());
      refs.ssoWrap.hidden = false;
    }
  }).catch(() => {});

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
          username: document.getElementById('u').value,
          password: document.getElementById('p').value,
        });
        const r = await fetch('/api/local-auth/login', {
          method: 'POST',
          body,
          credentials: 'same-origin',
        });
        if (r.ok) {
          let j = null;
          try { j = await r.json(); } catch (_) {}
          if (j && j.step === 'totp_required') {
            totpState = {
              kind: 'totp_required',
              challenge_id: j.challenge_id,
              username: j.username,
            };
            renderTotpForm();
            return;
          }
          if (j && j.step === 'totp_setup_required') {
            totpState = {
              kind: 'totp_setup_required',
              challenge_id: j.challenge_id,
              username: j.username,
              secret: j.secret,
              provisioning_uri: j.provisioning_uri,
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
        } else if (r.status === 401) {
          showErr(tx('login.invalid_credentials', 'Invalid username or password.'));
        } else {
          showErr(tx('login.sign_in_failed', 'Sign-in failed (' + r.status + '). Try again.', { status: r.status }));
        }
      } catch (_) {
        showErr(tx('login.network_error', 'Network error. Try again.'));
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

  function hideEl(el) { if (el) el.style.display = 'none'; }

  function renderTotpForm() {
    swapForm(async () => {
      clearErr();
      refs.btn.disabled = true;
      refs.btn.textContent = tx('login.verifying', 'Verifying…');
      try {
        const body = new URLSearchParams({
          challenge_id: totpState.challenge_id,
          code: document.getElementById('totp-code').value,
        });
        const r = await fetch('/api/local-auth/totp', {
          method: 'POST',
          body,
          credentials: 'same-origin',
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
          showErr(tx('login.sign_in_failed', 'Sign-in failed (' + r.status + ').', { status: r.status }));
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

    const block = document.createElement('div');
    block.id = 'totp-block';
    block.innerHTML =
      '<p class="sub" id="totp-hint"></p>' +
      '<label for="totp-code" id="totp-code-label"></label>' +
      '<input id="totp-code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" autofocus required />' +
      '<a href="#" id="totp-toggle-mode" class="totp-link"></a>';
    refs.form.insertBefore(block, refs.btn);

    document.getElementById('totp-hint').textContent = tx('login.totp_required_hint', 'Enter the 6-digit code from your authenticator app.');
    document.getElementById('totp-code-label').textContent = tx('login.totp_code_label', 'Authenticator code');
    document.getElementById('totp-toggle-mode').textContent = tx('login.totp_use_backup', 'Use a backup code instead');
    refs.btn.textContent = tx('login.verify', 'Verify');

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

  function renderTotpSetupForm() {
    swapForm(async () => {
      clearErr();
      refs.btn.disabled = true;
      refs.btn.textContent = tx('login.verifying', 'Verifying…');
      try {
        const body = new URLSearchParams({
          challenge_id: totpState.challenge_id,
          code: document.getElementById('totp-code').value,
        });
        const r = await fetch('/api/local-auth/totp-setup-confirm', {
          method: 'POST',
          body,
          credentials: 'same-origin',
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
          showErr(tx('login.sign_in_failed', 'Sign-in failed (' + r.status + ').', { status: r.status }));
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
    block.innerHTML =
      '<p class="sub" id="totp-setup-hint"></p>' +
      '<div id="totp-qr" class="totp-qr"></div>' +
      '<div class="totp-secret-row">' +
        '<span id="totp-secret-label"></span>' +
        '<code id="totp-secret-value" class="mono"></code>' +
      '</div>' +
      '<label for="totp-code" id="totp-code-label"></label>' +
      '<input id="totp-code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" autofocus required />';
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
    while (refs.form.firstChild) refs.form.removeChild(refs.form.firstChild);

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
      const blob = new Blob([codes.join('\n') + '\n'], { type: 'text/plain' });
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
      qr.addData(uri);
      qr.make();
      el.innerHTML = qr.createSvgTag({ cellSize: 6, margin: 4, scalable: true });
    } catch (_) {
      const fallback = document.createElement('code');
      fallback.className = 'mono totp-qr-fallback';
      fallback.textContent = uri;
      el.appendChild(fallback);
    }
  }
})();
