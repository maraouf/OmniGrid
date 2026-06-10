// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,ConstantOnRightSideOfComparisonJS,NestedFunctionCallJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ContinueStatementJS,BreakStatementJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML,JSAsyncFunctionMissingAwait
// noinspection JSMissingAwait,JSUnfilteredForInLoop,IfStatementWithoutBlockJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSPotentiallyInvalidUsageOfThis,JSIgnoredPromiseFromCall
/* global Alpine, Swal, I18N, t, AbortController, setTimeout, clearTimeout */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, module: true, -W069 */

// Custom-dashboard SYSTEM-STATS widget module.
//
// Pure fleet-rollup helpers over the existing `this.hosts` state (up /
// down / paused counts + the SVG progress-ring geometry). No fetch — it
// derives from data the Hosts view already loads.
//
// See clock.js for the per-widget module contract.

export const helpers = {
  // SVG path for the system-stats progress ring — stroke-dasharray /
  // stroke-dashoffset values for a circular fill showing up/total
  // ratio. Circle perimeter = 2*pi*r with r=22 (matches the ring's
  // viewBox + stroke-width). Returns `{circumference, offset}`; the
  // markup binds `stroke-dasharray` / `stroke-dashoffset` to these so
  // the transition animates smoothly as the rollup changes.
  appsWidgetSystemRing() {
    const s = this.appsWidgetSystemStats();
    const r = 22;
    const circumference = 2 * Math.PI * r;
    if (!s.total) {
      return {circumference, offset: circumference, percent: 0};
    }
    const pct = Math.max(0, Math.min(1, s.up / s.total));
    return {
      circumference,
      offset: circumference * (1 - pct),
      percent: Math.round(pct * 100),
    };
  },
  // Fleet rollup for the system-stats widget — {up, total, down, paused}
  // across the curated hosts the SPA already has loaded. Reuses this.hosts;
  // no fetch. `paused` covers hosts whose probing is suspended (auto-paused
  // OR operator-paused) — surfaced separately so the operator can spot a
  // partial-degradation state at a glance.
  appsWidgetSystemStats() {
    const hosts = Array.isArray(this.hosts) ? this.hosts : [];
    let up = 0, down = 0, paused = 0;
    for (const h of hosts) {
      const s = (h && h.status) || '';
      if (s === 'up') {
        up++;
      } else if (s === 'down' || s === 'unknown') {
        down++;
      } else if (s === 'paused') {
        paused++;
      }
    }
    return {up, down, paused, total: hosts.length};
  },
};

export const widget = {
  kind: 'system_stats',
  supportsRefresh: false,
  decorationIcon() {
    return 'icon-server';
  },
};
