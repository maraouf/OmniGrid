// Global fetch wrapper installed before Alpine init. Does three things:
//   1. Auto-attaches X-CSRF-Token on state-changing requests by copying
//      the og_csrf cookie (double-submit defense). Server enforces this
//      for cookie-authed callers on every POST/PUT/PATCH/DELETE.
//   2. Recovers from a "CSRF token mismatch" 403 by re-fetching /api/me
//      (which mints a fresh og_csrf cookie via the auth middleware) then
//      replaying the original request once. Keeps a stale-cookie scenario
//      (operator cleared cookies, lifespan restart cleared in-memory
//      session that hadn't yet seeded the cookie, etc.) self-healing
//      instead of asking the operator to refresh.
//   3. Redirects to /login on a 401 response so session expiry is
//      self-healing — user lands on the login page, authenticates,
//      comes back to where they were.
(function () {
  const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
  const orig = window.fetch;

  function readCsrfCookie() {
    const m = document.cookie.match(/(?:^|; )og_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function attachCsrf(init) {
    const method = (init.method || 'GET').toUpperCase();
    if (!WRITE_METHODS.has(method)) return init;
    const token = readCsrfCookie();
    if (!token) return init;
    const headers = new Headers(init.headers || {});
    headers.set('X-CSRF-Token', token);
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
    init = attachCsrf(init);
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
      try { detail = (await r.clone().json()).detail || ''; } catch (_) {}
      if (typeof detail === 'string' && detail.toLowerCase().includes('csrf')) {
        try {
          // /api/me is auth-optional + GET, so it always responds without
          // requiring a CSRF token, AND it triggers the middleware's
          // "issue og_csrf if missing" branch on the response.
          await orig.call(this, '/api/me', { credentials: 'same-origin' });
        } catch (_) { /* fall through — retry will surface the real error */ }
        init._csrfRetried = true;
        // Re-attach the (now hopefully valid) CSRF token from the new cookie.
        init = attachCsrf(init);
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
