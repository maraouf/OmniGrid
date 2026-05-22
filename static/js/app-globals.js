// noinspection ElementNotExported,JSUnusedGlobalSymbols,JSUnusedLocalSymbols,JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,CheckTagEmptyBody,HtmlUnknownTag,HtmlExtraClosingTag,MagicNumberJS,UnusedCatchParameterJS,OverlyComplexBooleanExpressionJS,FunctionWithMultipleReturnPointsJS,FunctionWithMoreThanThreeNegationsJS,OverlyNestedFunctionJS,OverlyLongFunctionJS,OverlyComplexFunctionJS,FunctionWithInconsistentReturnsJS,ChainedFunctionCallJS,NestedFunctionCallJS,NestedAssignmentJS,JSVariableNamingConventionJS,FunctionNamingConventionJS,JSStringConcatenationToES6Template,JSPotentiallyInvalidUsageOfThis,ContinueStatementJS,BreakStatementJS,AssignmentToFunctionParameterJS,IfStatementWithoutBlockJS,IfStatementWithIdenticalBranchesJS,AnonymousFunctionJS,AnonymousCapturingGroupJS,AnonymousFunctionRegExpJS,NamedFunctionExpressionJS,ConditionalExpressionJS,NestedConditionalExpressionJS,ConstantOnRightSideOfComparisonJS,ConstantOnLeftSideOfComparisonJS,EmptyCatchBlockJS,StatementWithEmptyBodyJS,RedundantConditionalExpressionJS,RedundantLocalVariableJS,JSValidateTypes,JSCheckFunctionSignatures,JSPrimitiveTypeWrapperUsage,JSDuplicatedDeclaration,TooManyFunctionParametersJS,NestedTemplateLiteralJS,AssignmentToForLoopParameterJS,AssignmentResultUsedJS,ConditionalCanBeReplacedWithEarlyExitJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// Top-level browser globals installed at module-load time. Side-effect
// import only — the body of this file mutates `window` directly and has
// nothing to export.
//
// `window.__ogCopyAiCode` — copy handler for the AI fenced-code-block
// copy button. Lives on `window` because the button is rendered via
// `x-html` (string-built markup) so it can't bind a method via Alpine's
// `@click`. The `data-code` attribute carries the JSON-encoded raw
// block body — JSON-decoding survives HTML-entity round-tripping that
// would otherwise corrupt the original whitespace / quote characters.
// Falls back to legacy `document.execCommand('copy')` when the async
// clipboard API isn't available (older browsers / iframes without the
// clipboard permission).

window.__ogCopyAiCode = function (btn) {
  if (!btn) {
    return;
  }
  const wrapper = btn.closest('.ai-resp-code-block');
  if (!wrapper) {
    return;
  }
  let body = '';
  try {
    body = JSON.parse(wrapper.dataset.code || '""');
  } catch (_) {
    body = wrapper.dataset.code || '';
  }
  const flashCopied = () => {
    btn.classList.add('ai-resp-code-copy--copied');
    setTimeout(() => btn.classList.remove('ai-resp-code-copy--copied'), 1200);
  };
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    navigator.clipboard.writeText(body).then(flashCopied).catch(() => {
      try {
        const ta = document.createElement('textarea');
        ta.value = body;
        ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        flashCopied();
      } catch (_) { /* clipboard not available — silently no-op */ }
    });
  } else {
    try {
      const ta = document.createElement('textarea');
      ta.value = body;
      ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      flashCopied();
    } catch (_) {}
  }
};
