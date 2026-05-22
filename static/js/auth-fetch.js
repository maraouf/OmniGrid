// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// Global fetch wrapper installed before Alpine init. Does four things:
// 1. Auto-attaches X-CSRF-Token on state-changing requests by copying
//    the og_csrf cookie (double-submit defense). Server enforces this
//    for cookie-authed callers on every POST/PUT/PATCH/DELETE.
// 2. Recovers from a "CSRF token mismatch" 403 by re-fetching /api/me
//    (which mints a fresh og_csrf cookie via the auth middleware) then
//    replaying the original request once. Keeps a stale-cookie scenario
//    (operator cleared cookies, lifespan restart cleared in-memory
//    session that hadn't yet seeded the cookie, etc.) self-healing
//    instead of asking the operator to refresh.
// 3. Redirects to /login on a 401 response so session expiry is
//    self-healing — user lands on the login page, authenticates,
//    comes back to where they were.
// 4. Attaches X-OmniGrid-Client-Id (UUID per tab, persisted in
//    sessionStorage). Backend echoes this in any SSE event published
//    off the same request; SSE handlers skip self-originated events
//    so a write from THIS tab doesn't loop back as a redundant
//    refresh / flicker. Read by `window.__ogClientId` from anywhere
//    that needs to compare an incoming SSE event's client_id.

(function () {
  const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
  const orig = window.fetch;

  // Per-tab UUID — generated on first read, persisted in
  // sessionStorage so an Alpine reload-without-tab-close keeps the
  // same id (otherwise an in-flight fetch from before the reload
  // would echo with a different id and fail self-filter). Falls back
  // to crypto.randomUUID where available; otherwise a quick
  // RFC4122-shaped string from Math.random — id collision risk is
  // limited to "two tabs on the same operator's browser hitting the
  // same backend within milliseconds AND both rolling the same 36-
  // char string", which is essentially zero for the use case.
  function readOrMintClientId() {
    try {
      let cid = sessionStorage.getItem('og_client_id');
      if (cid && typeof cid === 'string' && cid.length >= 8) return cid;
      if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        cid = window.crypto.randomUUID();
      } else {
        cid = ('xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx').replace(/[xy]/g, c => {
          const r = (Math.random() * 16) | 0;
          const v = c === 'x' ? r : (r & 0x3) | 0x8;
          return v.toString(16);
        });
      }
      sessionStorage.setItem('og_client_id', cid);
      return cid;
    } catch (_) {
      // Private mode / quota — generate a per-load fallback. Loses
      // the across-reload stability guarantee but keeps the contract.
      return 'fallback-' + Math.random().toString(36).slice(2);
    }
  }

  // Surface globally so SSE handlers + any future code that wants
  // "is this event from me?" can compare without re-reading
  // sessionStorage on every event.
  window.__ogClientId = readOrMintClientId();

  function readCsrfCookie() {
    const m = document.cookie.match(/(?:^|; )og_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  // Single Headers construction + every header set on it + assigned
  // back ONCE. Avoids the order-coupling bug where a future refactor
  // makes one helper mutate-in-place and the other constructs a fresh
  // Headers, silently dropping the in-place header on the second call.
  // Applies to both the initial fetch AND the CSRF retry path.
  function attachAllHeaders(init) {
    const headers = new Headers(init.headers || {});
    // X-OmniGrid-Client-Id : per-tab UUID echoed by backend SSE
    // publishers so the originating tab can self-filter. Applied on
    // every method (read GETs that trigger SSE-on-the-side need it too).
    if (window.__ogClientId) {
      headers.set('X-OmniGrid-Client-Id', window.__ogClientId);
    }
    // X-CSRF-Token: double-submit defence on state-changing requests
    // for cookie-authed callers. Bearer-token clients don't need it
    // (they don't have cookies); the token-mint path runs through
    // /api/me which auto-issues og_csrf on first GET.
    const method = (init.method || 'GET').toUpperCase();
    if (WRITE_METHODS.has(method)) {
      const token = readCsrfCookie();
      if (token) headers.set('X-CSRF-Token', token);
    }
    init.headers = headers;
    return init;
  }

  // Body shapes that survive a re-send: strings, FormData, URLSearchParams,
  // Blobs, and ArrayBuffers can be passed twice. ReadableStream cannot,
  // and (init.body instanceof ReadableStream) is the rare path we'd refuse
  // to retry. Anything else (object literal that the caller forgot to
  // JSON.stringify) is treated as cloneable — it's already a primitive
  // serialised by Headers.
  function isReplayable(body) {
    if (body == null) return true;
    if (typeof body === 'string') return true;
    if (typeof FormData !== 'undefined' && body instanceof FormData) return true;
    if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) return true;
    if (typeof Blob !== 'undefined' && body instanceof Blob) return true;
    if (typeof ArrayBuffer !== 'undefined' && body instanceof ArrayBuffer) return true;
    return false;
  }

  window.fetch = async function (input, init) {
    init = init || {};
    const method = (init.method || 'GET').toUpperCase();
    init = attachAllHeaders(init);
    let r = await orig.call(this, input, init);

    // CSRF mismatch recovery — only on cookie-authed write requests. Don't
    // loop: we retry exactly once after pinging /api/me to mint a fresh
    // CSRF cookie. If the second attempt also fails, surface the error.
    if (
      r.status === 403
      && WRITE_METHODS.has(method)
      && isReplayable(init.body)
      && !init._csrfRetried
    ) {
      let detail = '';
      // Tee the body (clone()) so we can both inspect AND let the caller
      // still read r.body downstream if we choose not to retry.
      try {
        detail = (await r.clone().json()).detail || '';
      } catch (_) {
      }
      if (typeof detail === 'string' && detail.toLowerCase().includes('csrf')) {
        try {
          // /api/me is auth-optional + GET, so it always responds without
          // requiring a CSRF token, AND it triggers the middleware's
          // "issue og_csrf if missing" branch on the response.
          await orig.call(this, '/api/me', {credentials: 'same-origin'});
        } catch (_) { /* fall through — retry will surface the real error */
        }
        init._csrfRetried = true;
        // Re-attach all headers (the new CSRF token is now valid in the cookie).
        init = attachAllHeaders(init);
        r = await orig.call(this, input, init);
      }
    }

    if (r.status === 401) {
      const path = location.pathname + location.search;
      location.href = '/login?next=' + encodeURIComponent(path);
    }
    return r;
  };
})();
