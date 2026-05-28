// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedGlobalSymbols
// noinspection EmptyCatchBlockJS,UnusedCatchParameterJS,StatementWithEmptyBodyJS
// noinspection JSUnfilteredForInLoop,IfStatementWithIdenticalBranchesJS,SingleStatementBlockJS,UnnecessaryLocalVariableJS,UnnecessaryContinueJS
// Per-inspection suppressions match the sibling SPA files. Covered idioms:
// constants on the right of comparisons (modern ESLint default); `for-in` over
// flat dicts where we control the source (no inherited members possible — every
// loop here iterates JSON-parse output OR our own `this.dict`); nested t() /
// flatten() / String() calls inside `||` chains; empty-catch fire-and-forget
// pattern; `_` unused catch parameter; non-block if-bodies (`if (r.ok) ...`
// one-liners). Real findings (function-parameter reassignment) are FIXED below,
// not suppressed.
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// ------------------------------------------------------------------
// i18n helper — vanilla JS, no external library. Pulls language files
// from /i18n/<code>.json at boot and exposes `window.t(key, vars)` for
// use in Alpine templates (`x-text="t('nav.stacks')"`) and JS.
// ------------------------------------------------------------------

const I18N = {
  dict: {},
  code: 'en',
  dir: 'ltr',
  languages: [],            // populated from /i18n/index.json
  _warned: new Set(),       // de-dupe console.warn for missing keys
  // `prefix` defaults via `= ''` parameter default instead of an
  // in-body reassignment — the IDE flagged the reassignment as a
  // function-parameter mutation. Same effect, cleaner shape.
  flatten(obj, prefix = '') {
    const out = {};
    for (const k in obj) {
      const v = obj[k];
      const key = prefix ? prefix + '.' + k : k;
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        Object.assign(out, this.flatten(v, key));
      } else {
        out[key] = v;
      }
    }
    return out;
  },
  async loadIndex() {
    try {
      const r = await fetch('/i18n/index.json', {cache: 'no-cache'});
      if (r.ok) {
        this.languages = await r.json();
      }
    } catch (_) {
      // Fallback: at least offer English if the index is missing.
      this.languages = [{code: 'en', name: 'English', dir: 'ltr'}];
    }
  },
  async load(code) {
    const r = await fetch('/i18n/' + code + '.json', {cache: 'no-cache'});
    if (!r.ok) {
      throw new Error('language ' + code + ' not found');
    }
    const doc = await r.json();
    this.dict = this.flatten(doc);
    this.code = code;
    this.dir = (doc._meta && doc._meta.dir) || 'ltr';
    document.documentElement.setAttribute('lang', code);
    document.documentElement.setAttribute('dir', this.dir);
    localStorage.setItem('lang', code);
    // Clear the warned-keys set so a language swap re-surfaces the ones
    // that are still missing in the new dict.
    this._warned = new Set();
  },
  t(key, vars) {
    // Honour intentionally-empty values. The key being present in the
    // dict (even with `""`) signals "the operator wants to render
    // nothing here", which differs from a MISSING key (typo, not yet
    // translated). Only `undefined` cascades through the fallback chain
    // + warn-log; explicit `""` returns immediately as the truthful
    // render. Same semantic on the English fallback dict.
    const hasKey = Object.prototype.hasOwnProperty.call(this.dict, key);
    let s = hasKey ? this.dict[key] : undefined;
    if (s === undefined) {
      const enHas = !!(window.__i18nEn && Object.prototype.hasOwnProperty.call(window.__i18nEn, key));
      s = enHas ? window.__i18nEn[key] : undefined;
      if (s === undefined && !this._warned.has(key)) {
        this._warned.add(key);
        if (typeof console !== 'undefined' && console.warn) {
          console.warn('[i18n] Missing key:', key);
        }
      }
      if (s === undefined) {
        s = key;
      }
    }
    if (vars) {
      for (const k in vars) {
        s = String(s).replaceAll('{' + k + '}', String(vars[k]));
      }
    }
    return s;
  },
};
window.I18N = I18N;
window.t = I18N.t.bind(I18N);

// Boot sequence — always preload English (so the missing-key fallback
// has a dict) before applying the user's preferred language.
window.__i18nReady = (async function () {
  try {
    const r = await fetch('/i18n/en.json', {cache: 'no-cache'});
    if (r.ok) {
      const doc = await r.json();
      window.__i18nEn = I18N.flatten(doc);
    }
  } catch (_) {
    window.__i18nEn = {};
  }
  await I18N.loadIndex();
  const pref = localStorage.getItem('lang') || 'en';
  if (pref === 'en') {
    I18N.dict = window.__i18nEn || {};
    I18N.dir = 'ltr';
    I18N.code = 'en';
    document.documentElement.setAttribute('lang', 'en');
    document.documentElement.setAttribute('dir', 'ltr');
  } else {
    try {
      await I18N.load(pref);
    } catch (_) {
      I18N.dict = window.__i18nEn || {};
    }
  }
})();
