/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// Alpine 3 dropped v2's `deferLoadingAlpine` hook, so we dynamically
// inject the Alpine <script> AFTER __i18nReady resolves. That keeps the
// first render free of untranslated strings without needing a build
// step. Alpine registers its own MutationObserver so adding an Alpine
// directive to the DOM after its script finishes still wires up.
// noinspection AnonymousFunctionJS,EmptyCatchBlockJS,UnusedCatchParameterJS

(async function () {
  try {
    await window.__i18nReady;
  } catch (_) {
  }
  const s = document.createElement('script');
  s.src = '/node_modules/alpinejs/dist/cdn.min.js';
  s.defer = true;
  document.head.appendChild(s);
})();
