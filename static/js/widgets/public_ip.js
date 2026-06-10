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

// Custom-dashboard PUBLIC-IP widget module.
//
// Render helpers (ISP brand icon + ASN formatting). The `publicIp` state
// + `_ensurePublicIp` fetch live in app-topbar.js (the AI palette + topbar
// share them); this module references them via `this.`.
//
// See clock.js for the per-widget module contract.

export const helpers = {
  // (Country-flag rendering moved to a local SVG `<img>` from
  // `/img/flags/<cc>.svg` in apps-widget-tile.html — the previous
  // regional-indicator emoji approach rendered as bare "EG" letters on
  // Windows, which has no flag-emoji glyphs. The image-based flag
  // renders identically on every OS.)
  // ISP brand-icon resolver for the Public-IP widget. Maps a raw
  // ISP / ASN-org string (e.g. "Cloudflare, Inc.", "Google LLC",
  // "Comcast Cable Communications") to a brand-icon slug the
  // existing `iconUrlFor` resolver handles. Returns empty string
  // for unknown ISPs so the SPA falls back to the generic globe
  // icon. Match table is intentionally small + well-known brands
  // only — operators add custom mappings via the SPA's iconUrlFor
  // alias map (`static/img/icons/<slug>.svg`).
  appsWidgetIspIconUrl(isp) {
    const raw = String(isp || '').trim().toLowerCase();
    if (!raw) {
      return '';
    }
    // Token-based lookup — match the FIRST recognisable brand in
    // the ISP string (the raw values are often "Cloudflare, Inc." /
    // "Amazon.com, Inc." / "Google LLC" / etc.).
    const tokens = [
      ['cloudflare', 'cloudflare'],
      ['google', 'google'],
      ['amazon', 'amazon'],
      ['microsoft', 'microsoft'],
      ['apple', 'apple'],
      ['comcast', 'comcast'],
      ['verizon', 'verizon'],
      ['at&t', 'att'],
      ['att ', 'att'],
      ['t-mobile', 't-mobile'],
      ['vodafone', 'vodafone'],
      ['orange', 'orange'],
      ['telefonica', 'telefonica'],
      ['deutsche telekom', 'deutsche-telekom'],
      ['british telecom', 'bt'],
      ['ovh', 'ovh'],
      ['hetzner', 'hetzner'],
      ['digitalocean', 'digitalocean'],
      ['linode', 'linode'],
      ['vultr', 'vultr'],
      // Egyptian carriers — operator-flagged. Each maps to the
      // canonical brand slug (drop the matching SVG into
      // `static/img/icons/<slug>.svg` from an official source per
      // the project conventions "Brand-icon onboarding").
      //   - e& (formerly Etisalat — UAE parent, present across
      //     Egypt / Saudi / etc.) — e& and Etisalat share the
      //     SAME canonical brand identity post-rebrand; both
      //     match the `etisalat` icon (single SVG covers both).
      //   - WE (Telecom Egypt, formerly TE Data) — all three
      //     market names point at the SAME operator post-merger;
      //     they all map to the `we` brand icon (single SVG
      //     covers the unified brand). ASN.org may still surface
      //     any of the legacy names depending on the upstream
      //     registry's age, so the match table carries all of
      //     them but resolves to one canonical slug.
      //   Vodafone + Orange already covered above and serve
      //   Egypt too; no per-region branch needed for those.
      ['e&', 'etisalat'],  // ampersand is fine in a substring needle
      ['eand', 'etisalat'],  // ASN-registry rendering of the rebrand
      ['etisalat', 'etisalat'],
      [' we ', 'we'],  // whitespace-padded — avoid matching "we are" / "swe" / etc.
      ['telecom egypt', 'we'],
      ['te data', 'we'],
      ['te-data', 'we'],
    ];
    for (const [needle, slug] of tokens) {
      if (raw.indexOf(needle) !== -1) {
        try {
          return this.iconUrlFor(slug);
        } catch (_) {
          return '';
        }
      }
    }
    return '';
  },
  // Test the per-app credential for the currently-edited chip.
  // Routed through the generic `/api/services/{host_id}/
  // {service_idx}/test-credential` dispatcher, which dispatches
  // to the chip's per-app module (slug-keyed via
  // `logic/apps/registry`). The candidate api_key is shipped in
  // the request body; blank falls back to the stored value on
  // the backend side. Mirrors the canonical test-before-save
  // pattern (admin tabs with a probe path gate Save behind a
  // successful test).
  // `testInstanceCredential` + `appsInstanceTestBusy` / `appsInstanceTestResult`
  // live in `app-apps-data.js` (the canonical copy). It's merged AFTER this
  // module in app.js's `_mergeKeepDescriptors` chain, so its version wins —
  // this divergent duplicate was dead code and was removed to avoid drift
  // (it lacked the `username` + `url` payload fields and the `{pending}`
  // result shape the shared og-test-connection component needs).
  // Public-IP detail value formatter — strips the "AS" prefix from
  // the ASN field so the chip reads cleanly. Backend returns "AS15169"
  // (Google), "AS13335" (Cloudflare), etc.; the visual chip looks
  // better without the redundant `AS` since it's also pill-shaped.
  // Caller passes the raw asn; this returns the number portion.
  appsWidgetIpAsnNumber(asn) {
    if (!asn) {
      return '';
    }
    const s = String(asn).trim();
    // Test-then-slice instead of capture-group. Two birds: the IDE's
    // "Anonymous capturing group" inspection has nothing to flag (no
    // group at all), AND JSHint stops choking on named-capture syntax
    // (E016 invalid-regex on `(?<num>\d+)` — JSHint hasn't shipped
    // ES2018 named-group support). Same return semantics.
    if (/^AS\d+$/i.test(s)) {
      return s.slice(2);
    }
    return s;
  },
};

export const widget = {
  kind: 'public_ip',
  supportsRefresh: true,
  decorationIcon() {
    return 'icon-globe';
  },
  freshnessObj(c) {
    return c.publicIp;
  },
  hasData(c) {
    if (c.publicIp && c.publicIp.enabled === false) {
      return false;
    }
    return !!(c.publicIp && c.publicIp.ip);
  },
  refresh(c) {
    return c._ensurePublicIp(true);
  },
};
