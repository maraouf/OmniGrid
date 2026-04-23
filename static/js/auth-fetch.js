// Global fetch wrapper installed before Alpine init. Does two things:
//   1. Auto-attaches X-CSRF-Token on state-changing requests by copying
//      the pu_csrf cookie (double-submit defense). Server enforces this
//      for cookie-authed callers on every POST/PUT/PATCH/DELETE.
//   2. Redirects to /login on a 401 response so session expiry is
//      self-healing — user lands on the login page, authenticates,
//      comes back to where they were.
(function () {
  const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
  const orig = window.fetch;
  window.fetch = async function (input, init) {
    init = init || {};
    const method = (init.method || 'GET').toUpperCase();
    if (WRITE_METHODS.has(method)) {
      const m = document.cookie.match(/(?:^|; )pu_csrf=([^;]+)/);
      if (m) {
        const headers = new Headers(init.headers || {});
        if (!headers.has('X-CSRF-Token')) {
          headers.set('X-CSRF-Token', decodeURIComponent(m[1]));
        }
        init.headers = headers;
      }
    }
    const r = await orig.call(this, input, init);
    if (r.status === 401) {
      const path = location.pathname + location.search;
      location.href = '/login?next=' + encodeURIComponent(path);
    }
    return r;
  };
})();
