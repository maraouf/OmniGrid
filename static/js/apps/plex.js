// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSUnresolvedReference,JSUnresolvedVariable
//   ^ the OAuth start/poll responses are parsed JSON (no static type), so the
//     analyzer can't resolve `.auth_url` / `.pin_id` / `.token` — they're real.
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
/* jshint esversion: 11, module: true, eqeqeq: false, -W116 */

// Per-app SPA module -- Plex (media server).
//
// Encapsulates every Plex-specific helper so the generic
// `static/js/app-apps.js` stays app-agnostic. Loaded by
// `static/js/apps/_registry.js`, which merges every per-app module's
// `helpers` into the Alpine component AND exposes the extender record
// (slugs / requiresApiKey / cardSpan) to the generic helpers via
// `window.OG_APPS_EXTENDERS`.
//
// Data source
//   Plex chips render a 4-stat panel (movies / shows / music / now playing)
//   sourced from the PMS HTTP API (library sections + per-section totalSize +
//   /status/sessions) via `logic/apps/plex.py:fetch_data`. The card reads the
//   per-app data the generic dispatcher fetched, so it never triggers a
//   per-card round-trip on the hot path. `plexData(inst)` reads it via the
//   cache-backed `appsAppData(inst)` helper (same path Bazarr / Seerr use).
//
// File-scope IDE directives: see `speedtest_tracker.js`'s header for the full
// rationale -- same per-file JSHint + PyCharm conventions.

// True when `app` is the Plex catalog template (matched via slug; falls back
// to a substring check on `app.name` so an operator-edited chip that dropped
// the catalog link but kept the brand still resolves).
function isPlexApp(app) {
  if (!app) {
    return false;
  }
  const cat = app.catalog || {};
  const slug = String(cat.slug || '').trim().toLowerCase();
  if (slug === 'plex') {
    return true;
  }
  return (String(app.name || '').toLowerCase().indexOf('plex') !== -1);
}

// Per-instance Plex data lookup -- reads the per-app data the generic
// dispatcher fetched (via `logic/apps/plex.py:fetch_data`). Returns null while
// idle / pending / errored OR when the payload isn't available, so the panel
// gate hides cleanly.
function plexData(inst) {
  // `this` is the Alpine component (merged in via `appsHelpers`).
  /* jshint validthis: true */
  if (!inst || !this.appsAppData) {
    return null;
  }
  const d = this.appsAppData(inst);
  if (!d || !d.available) {
    return null;
  }
  return d;
}

// Format an integer count with thousand separators; '—' for missing.
function plexCount(v) {
  if (v == null) {
    return '—';
  }
  const n = Number(v);
  if (!isFinite(n)) {
    return '—';
  }
  return Math.round(n).toLocaleString();
}

// "Sign in to Plex" — runs the Plex OAuth PIN device flow so the operator
// never pastes an X-Plex-Token by hand (the same seamless flow Tautulli /
// Overseerr use). POSTs /api/apps/plex/auth/start (the backend asks plex.tv
// for a PIN), opens the returned auth URL in a popup for the user to sign in,
// then polls /api/apps/plex/auth/poll until plex.tv hands back the token --
// which is filled straight into the editor's api_key field. Returns a promise
// so the button can show a busy spinner via its local x-data while it runs.
// `this` is the Alpine component (merged in via `appsHelpers`).
function plexSignIn() {
  /* jshint validthis: true */
  const self = this;
  const tr = (k, fb) => (self.t && self.t(k)) || fb;

  function _toast(msg, kind) {
    if (typeof self.showToast === 'function') {
      self.showToast(msg, kind);
    }
  }

  // Best-effort popup close (cross-origin access can throw — swallow it).
  function _closePopup(win) {
    try {
      if (win && !win.closed) {
        win.close();
      }
    } catch (_) { /* cross-origin */
    }
  }

  let popup = null;
  return (async () => {
    try {
      const r = await fetch('/api/apps/plex/auth/start', {method: 'POST'});
      const d = await r.json().catch(() => ({}));
      const started = r.ok && d && d.ok;
      if (!started || !d.auth_url) {
        _toast((d && d.detail) || tr('apps.plex.signin_start_failed', 'Couldn’t start Plex sign-in'), 'error');
        return;
      }
      // Open the plex.tv auth page for the user to sign in + authorise.
      popup = window.open(d.auth_url, 'plexauth', 'width=820,height=740');
      _toast(tr('apps.plex.signin_opened', 'Sign in to Plex in the new window…'), 'info');
      // Poll the PIN for up to ~2 minutes (2s cadence). Build the poll URL once
      // (single string — no line-break-before-+ readability nit).
      const pollUrl = '/api/apps/plex/auth/poll?pin_id=' + encodeURIComponent(d.pin_id) + '&code=' + encodeURIComponent(d.code);
      const deadline = Date.now() + 120000;
      while (Date.now() < deadline) {
        await new Promise((res) => setTimeout(res, 2000));
        const pr = await fetch(pollUrl);
        const pd = await pr.json().catch(() => ({}));
        const pok = pr.ok && pd && pd.ok;
        if (pok && pd.token) {
          self.appsInstanceEditForm.api_key = pd.token;
          self.appsInstanceEditForm.api_key_set = true;
          _closePopup(popup);
          _toast(tr('apps.plex.signin_ok', 'Signed in to Plex — token filled. Test + Save to finish.'), 'success');
          return;
        }
        if (!(pok && pd.pending)) {
          // A hard error (expired PIN / plex.tv unreachable) — not just "still
          // waiting" — so stop polling and report it.
          _toast((pd && pd.detail) || tr('apps.plex.signin_failed', 'Plex sign-in failed'), 'error');
          return;
        }
        // else: still pending → keep polling until the deadline.
      }
      _toast(tr('apps.plex.signin_timeout', 'Plex sign-in timed out — try again'), 'error');
    } catch (_e) {
      _toast(tr('apps.plex.signin_failed', 'Plex sign-in failed'), 'error');
    } finally {
      _closePopup(popup);
    }
  })();
}

// Extender record -- consumed by the generic helpers in
// `static/js/app-apps.js` via `window.OG_APPS_EXTENDERS`. Plex gets a 2-column
// span so the 4-stat panel doesn't squeeze the per-instance host list, and a
// vertical telemetry-card layout like Bazarr / APC.
export const extender = {
  slugs: ['plex'],
  requiresApiKey: true,
  eyebrowTitle: true,
  verticalLayout: true,
  cardSpan(app) {
    return isPlexApp(app) ? 2 : 1;
  },
};

// Helpers attached to the Alpine `app()` component via the merge in
// `static/js/apps/_registry.js`. Names are prefixed `plex*` so they don't
// collide with other per-app modules' helpers.
export const helpers = {
  plexIsApp: isPlexApp,
  plexData: plexData,
  plexCount: plexCount,
  plexSignIn: plexSignIn,
};
