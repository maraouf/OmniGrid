// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
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
  flatten(obj, prefix) {
    prefix = prefix || '';
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
      if (r.ok) this.languages = await r.json();
    } catch (_) {
      // Fallback: at least offer English if the index is missing.
      this.languages = [{code: 'en', name: 'English', dir: 'ltr'}];
    }
  },
  async load(code) {
    const r = await fetch('/i18n/' + code + '.json', {cache: 'no-cache'});
    if (!r.ok) throw new Error('language ' + code + ' not found');
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
    let s = this.dict[key];
    if (s === undefined || s === '') {
      s = (window.__i18nEn && window.__i18nEn[key]);
      if ((s === undefined || s === '') && !this._warned.has(key)) {
        this._warned.add(key);
        if (typeof console !== 'undefined' && console.warn) {
          console.warn('[i18n] Missing key:', key);
        }
      }
      if (s === undefined || s === '') s = key;
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
