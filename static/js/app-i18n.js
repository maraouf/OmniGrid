// noinspection ElementNotExported,JSUnusedGlobalSymbols,JSUnusedLocalSymbols,JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,CheckTagEmptyBody,HtmlUnknownTag,HtmlExtraClosingTag,MagicNumberJS,UnusedCatchParameterJS,OverlyComplexBooleanExpressionJS,FunctionWithMultipleReturnPointsJS,FunctionWithMoreThanThreeNegationsJS,OverlyNestedFunctionJS,OverlyLongFunctionJS,OverlyComplexFunctionJS,FunctionWithInconsistentReturnsJS,ChainedFunctionCallJS,NestedFunctionCallJS,NestedAssignmentJS,JSVariableNamingConventionJS,FunctionNamingConventionJS,JSStringConcatenationToES6Template,JSPotentiallyInvalidUsageOfThis,ContinueStatementJS,BreakStatementJS,AssignmentToFunctionParameterJS,IfStatementWithoutBlockJS,IfStatementWithIdenticalBranchesJS,AnonymousFunctionJS,AnonymousCapturingGroupJS,AnonymousFunctionRegExpJS,NamedFunctionExpressionJS,ConditionalExpressionJS,NestedConditionalExpressionJS,ConstantOnRightSideOfComparisonJS,ConstantOnLeftSideOfComparisonJS,EmptyCatchBlockJS,StatementWithEmptyBodyJS,RedundantConditionalExpressionJS,RedundantLocalVariableJS,JSValidateTypes,JSCheckFunctionSignatures,JSPrimitiveTypeWrapperUsage,JSDuplicatedDeclaration,TooManyFunctionParametersJS,NestedTemplateLiteralJS,AssignmentToForLoopParameterJS,AssignmentResultUsedJS,ConditionalCanBeReplacedWithEarlyExitJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA i18n bindings — Alpine-reactive language state + the `t()` helper
// wrapper + the language-switch handler.
//
// The actual translation engine is `window.I18N` (installed by
// `static/js/i18n.js`). This module is the THIN component-side wrapper
// that wires Alpine reactivity into language switches and the
// `ui_prefs.lang` persistence path.
//
// Phase 2, Batch 2 of the static/js/app.js modularisation.

export default {
    // i18n reactive state. `lang` is watched to trigger Alpine re-renders
    // when the user swaps languages. `t()` is forwarded to the global
    // helper so every template can do `x-text="t('nav.stacks')"`.
    lang: (window.I18N && window.I18N.code) || 'en',
    dir: (window.I18N && window.I18N.dir) || 'ltr',
    availableLanguages: (window.I18N && window.I18N.languages) || [{ code: 'en', name: 'English', dir: 'ltr' }],
    t(key, vars) {
      // Touch `this.lang` so Alpine tracks this binding as a dependency
      // of the reactive `lang` state — when setLang() updates `lang`,
      // every x-text="t(...)" re-evaluates automatically.
      void this.lang;
      return (window.I18N ? window.I18N.t(key, vars) : key);
    },
    async setLang(code) {
      if (!code || code === this.lang) {
        return;
      }
      try {
        await window.I18N.load(code);
        this.lang = code;
        this.dir  = window.I18N.dir;
        // Persist to ui_prefs so the operator's chosen locale follows
        // them across browsers / machines AND so backend notification
        // template resolution (logic/ops.py:resolve_actor_locale)
        // picks up the right language for notifications fired by
        // this operator's actions. Same write-through pattern as
        // `persistThemePref` / `persistHostHistoryRange`.
        if (typeof this.persistUiLang === 'function') {
          this.persistUiLang(code);
        }
        this.showToast(this.t('toasts.language_changed') || 'Language changed');
      } catch (_) {
        // Language fetch failed — surface a fallback message that doesn't
        // require the new dict to have loaded.
        this.showToast(this.t('toasts_extra.language_load_failed'), 'error');
      }
    },
    async persistUiLang(value) {
      // Skip API-token pseudo-users (negative ids) — /api/me/ui-prefs
      // returns 400 for them.
      if (!this.me || !this.me.id || this.me.id < 0) {
        return;
      }
      const code = String(value || '').trim().toLowerCase();
      if (!code) {
        return;
      }
      try {
        await fetch('/api/me/ui-prefs', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prefs: { lang: code } }),
        });
        if (this.me && this.me.ui_prefs) {
          this.me.ui_prefs.lang = code;
        }
      } catch (e) {
        if (window.console && console.warn) {
          console.warn('[lang] persist to DB failed:', e);
        }
      }
    },
};
