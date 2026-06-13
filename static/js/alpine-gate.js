// noinspection AnonymousFunctionJS,ConstantOnRightSideOfComparisonJS,MagicNumberJS,EmptyCatchBlockJS,UnusedCatchParameterJS,NestedFunctionJS
// noinspection FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,DuplicatedCodeFragmentJS
// noinspection DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS,RedundantConditionalExpressionJS
// noinspection JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS,NestedTemplateLiteralJS,FunctionWithMoreThanThreeNegationsJS,NegatedIfStatementJS
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// Alpine 3 dropped v2's `deferLoadingAlpine` hook, so we dynamically
// inject the Alpine <script> AFTER __i18nReady resolves. That keeps the
// first render free of untranslated strings without needing a build
// step. Alpine registers its own MutationObserver so adding an Alpine
// directive to the DOM after its script finishes still wires up.

(async function () {
  try {
    await window.__i18nReady;
  } catch (_) {
  }
  // app.js is a `<script type="module">` — module scripts are
  // deferred and evaluate just before `DOMContentLoaded` fires. We
  // MUST wait for them or Alpine will scan the DOM, find that
  // `window.app` (the `x-data="app()"` factory) is undefined, catch
  // the throw, and instantiate an empty `{}` component. Every
  // template expression then throws "X is not defined" against the
  // empty scope and the UI freezes on the loading skeleton.
  if (document.readyState === 'loading') {
    await new Promise(function (r) {
      document.addEventListener('DOMContentLoaded', r, {once: true});
    });
  }
  // Defence-in-depth: if for some reason the module is still in
  // flight (slow import resolution, browser quirk), spin until
  // `window.app` lands. Cap at ~2s — `window.app = app` runs at the END
  // of app.js's ES-module top-level (after its full static import graph
  // of ~60 versioned files evaluates), and the static graph is
  // guaranteed evaluated before the DOMContentLoaded we already awaited
  // above. So in the success case this loop exits on the first tick; it
  // only runs its full length when the graph FAILED to evaluate.
  let waited = 0;
  while (typeof window.app !== 'function' && waited < 2000) {
    await new Promise(function (r) {
      setTimeout(r, 25);
    });
    waited += 25;
  }
  // If `window.app` STILL isn't a function, the app.js module graph
  // failed to finish — almost always a transient on the very first load
  // after a new release: the page reloads onto the new `?v=<ver>`
  // markers while the rolling Swarm deploy is mid-swap, so one versioned
  // module file momentarily 404s / stalls and the whole static import
  // graph fails to evaluate (a single failed static import aborts the
  // entire graph, so `window.app = app` never runs). Injecting Alpine
  // now would evaluate `x-data="app()"` against an undefined factory,
  // throw "app is not defined", and freeze on the loading skeleton until
  // the user manually refreshes. A single automatic hard reload reliably
  // recovers (the backend is fully live by then) — turning the manual
  // refresh into invisible auto-recovery. Guarded by a sessionStorage
  // flag so a GENUINE persistent module failure reloads exactly once,
  // then falls through to inject Alpine so the error surfaces visibly for
  // diagnosis instead of an infinite reload loop. sessionStorage is
  // wrapped so private-mode / disabled-storage never throws in the gate.
  const reloadFlag = 'og_app_gate_reloaded';
  if (typeof window.app !== 'function') {
    let alreadyRetried = true;  // fail safe: if storage is unreadable, don't reload
    try {
      alreadyRetried = !!sessionStorage.getItem(reloadFlag);
    } catch (_) {
    }
    if (!alreadyRetried) {
      try {
        sessionStorage.setItem(reloadFlag, '1');
      } catch (_) {
      }
      location.reload();
      return;
    }
    // Already retried once and still no `app()` — fall through and inject
    // Alpine so the underlying error is visible rather than hidden behind
    // an endless reload.
  } else {
    // Loaded cleanly — clear the retry flag so a future transient on this
    // tab gets its own one-shot retry.
    try {
      sessionStorage.removeItem(reloadFlag);
    } catch (_) {
    }
  }
  const s = document.createElement('script');
  s.src = '/node_modules/alpinejs/dist/cdn.min.js';
  s.defer = true;
  document.head.appendChild(s);
})();
