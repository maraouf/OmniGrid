// noinspection AnonymousFunctionJS,ConstantOnRightSideOfComparisonJS,MagicNumberJS,EmptyCatchBlockJS,UnusedCatchParameterJS,NestedFunctionJS
// noinspection FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,DuplicatedCodeFragmentJS
// noinspection DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS,RedundantConditionalExpressionJS
// noinspection JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS,NestedTemplateLiteralJS
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
  // `window.app` lands. Cap at ~2s so a genuine module-load failure
  // surfaces visibly instead of hanging silently.
  let waited = 0;
  while (typeof window.app !== 'function' && waited < 2000) {
    await new Promise(function (r) {
      setTimeout(r, 25);
    });
    waited += 25;
  }
  const s = document.createElement('script');
  s.src = '/node_modules/alpinejs/dist/cdn.min.js';
  s.defer = true;
  document.head.appendChild(s);
})();
