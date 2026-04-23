// Login-page controller. Kept separate from /js/app.js because this page
// has no Alpine — it's a vanilla form submission, plus a data-i18n DOM
// sweep, plus an SSO-button toggle that polls /api/auth/providers.
//
// The main i18n helper (window.I18N + window.t) is already loaded via
// /js/i18n.js by the time we run; we just layer a data-i18n applier on
// top so markup like `<h1 data-i18n="app.name">OmniGrid</h1>` renders
// translated text without Alpine.

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
  const form = document.getElementById('login');
  const btn = document.getElementById('btn');
  const err = document.getElementById('err');
  const ver = document.getElementById('ver');
  const ssoWrap = document.getElementById('ssoWrap');
  const ssoBtn = document.getElementById('ssoBtn');

  // Show version so operators can sanity-check which build is live.
  fetch('/api/version').then(r => r.ok ? r.json() : null).then(v => {
    if (v && v.version) ver.textContent = 'v' + v.version;
  }).catch(() => {});

  function showErr(msg) {
    err.textContent = msg;
    err.classList.add('show');
  }
  function clearErr() {
    err.classList.remove('show');
    err.textContent = '';
  }

  function nextPath() {
    // Default to "/" but honor a ?next=<path> if it's a same-origin path.
    const p = new URLSearchParams(location.search).get('next') || '/';
    // Reject absolute / protocol-relative URLs — open-redirect defense.
    if (!p.startsWith('/') || p.startsWith('//')) return '/';
    return p;
  }

  // Advertise SSO once we know it's configured. Hidden by default so a
  // misconfigured deployment never shows a dead button; the endpoint is
  // public (no auth required) so the call completes even for anonymous
  // visitors.
  fetch('/api/auth/providers').then(r => r.ok ? r.json() : null).then(p => {
    if (p && p.oidc) {
      ssoBtn.href = '/api/oidc/login?next=' + encodeURIComponent(nextPath());
      ssoWrap.hidden = false;
    }
  }).catch(() => {});

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearErr();
    btn.disabled = true;
    btn.textContent = window.t ? window.t('login.signing_in') : 'Signing in…';
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
        location.href = nextPath();
        return;
      }
      if (r.status === 429) {
        const j = await r.json().catch(() => ({}));
        showErr(j.detail || (window.t ? window.t('login.too_many_attempts') : 'Too many attempts. Try again shortly.'));
      } else if (r.status === 401) {
        showErr(window.t ? window.t('login.invalid_credentials') : 'Invalid username or password.');
      } else {
        showErr(window.t ? window.t('login.sign_in_failed', { status: r.status }) : ('Sign-in failed (' + r.status + '). Try again.'));
      }
    } catch (_) {
      showErr(window.t ? window.t('login.network_error') : 'Network error. Try again.');
    } finally {
      btn.disabled = false;
      btn.textContent = window.t ? window.t('login.sign_in') : 'Sign in';
    }
  });
})();
