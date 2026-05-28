// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,ExceptionCaughtLocallyJS
// noinspection OverlyComplexBooleanExpressionJS,NegatedConditionalExpressionJS,JSNegatedConditionalExpression,NegatedIfStatementJS
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall
// noinspection JSIfStatementsCanBeSimplified,IfStatementSimplifyable,RedundantIfStatementJS,RedundantLocalVariableJS,JSReusedLocalVariable
// noinspection HtmlUnknownTag,HtmlEmptyContent,HtmlEmptyTagsRecommendation,InnerHTMLJS,VoidExpressionJS,JSVoidExpression
// noinspection ContinueStatementJS,BreakStatementJS,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML
// Comprehensive per-inspection suppressions mirror app-ai-admin.js.
// Same SPA-wide conventions: constants on right of comparisons (ESLint
// default — opposite of Yoda); anonymous arrow callbacks; chained
// map+filter; ternaries; Alpine-called methods PyCharm can't trace
// through x-on:click; nested t() i18n lookups; `Unresolved variable
// notify_medium_telegram` / `telegram_user_id` fire because PyCharm
// can't trace SettingsIn-shape dict fields back to the Pydantic
// model (server-side schema).
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA Telegram-link / Telegram-notify integration.

export default {
  telegramTestResult: null,
  // Admin → Notifications → Telegram → datatable of every linked
  // user. Populated by loadTelegramLinks() on first render of the
  // Telegram tab + re-loaded after admin-side unlink so the row
  // disappears immediately. Each row: {telegram_user_id, username,
  // role}.
  telegramLinks: [],
  telegramLinksLoading: false,
  telegramLinksError: '',
  // Set to true on first successful (or failed) `loadTelegramLinks`
  // completion. Gates the empty-state message + table-show in the
  // partial so the brief flash of "no links yet" between tab-open
  // and the first fetch landing doesn't appear (matches the
  // Users / Sessions / Tokens admin tables' loading-gate pattern).
  telegramLinksLoaded: false,
  _telegramLastPassedTest: '',
  // Profile → Telegram link card state. Code is the 6-digit minted
  // value the user types into Telegram as `/link <code>`. Expires
  // 15 minutes after mint; the SPA shows a relative-minutes
  // countdown next to the code so the user knows when to regenerate.
  telegramLinkCode: '',
  telegramLinkExpiresMs: 0,
  telegramLinkBusy: false,
  telegramLinkError: '',
  telegramLinkSuccess: '',

  // Profile → Telegram link handlers.
  async generateTelegramLinkCode() {
    this.telegramLinkBusy = true;
    this.telegramLinkError = '';
    this.telegramLinkSuccess = '';
    try {
      const r = await fetch('/api/me/telegram-link-code', {method: 'POST'});
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        this.telegramLinkError = j.detail || `HTTP ${r.status}`;
        return;
      }
      this.telegramLinkCode = j.code || '';
      this.telegramLinkExpiresMs = j.expires_ms || 0;
    } catch (e) {
      this.telegramLinkError = String(e && e.message ? e.message : e);
    } finally {
      this.telegramLinkBusy = false;
    }
  },
  async unlinkTelegram() {
    this.telegramLinkBusy = true;
    this.telegramLinkError = '';
    this.telegramLinkSuccess = '';
    try {
      const r = await fetch('/api/me/telegram-link', {method: 'DELETE'});
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        this.telegramLinkError = j.detail || `HTTP ${r.status}`;
        return;
      }
      const removed = Array.isArray(j.removed) ? j.removed.length : 0;
      this.telegramLinkSuccess = removed
        ? this.t('profile.telegram.unlink_success', {count: removed})
        : this.t('profile.telegram.unlink_nothing');
      // Reload /api/me so the Profile card flips back to the
      // unlinked state (Generate button visible, linked banner
      // hidden). Same pattern other Profile mutations use.
      try {
        const rm = await fetch('/api/me', {cache: 'no-store'});
        if (rm.ok) {
          const meData = await rm.json();
          if (meData && meData.authenticated !== false) {
            Object.assign(this.me, meData);
          }
        }
      } catch {
      }
      // Clear any stale code from the prior session — once unlinked
      // the operator should generate a fresh code if they want to
      // re-link rather than reuse the old one.
      this.telegramLinkCode = '';
      this.telegramLinkExpiresMs = 0;
    } catch (e) {
      this.telegramLinkError = String(e && e.message ? e.message : e);
    } finally {
      this.telegramLinkBusy = false;
    }
  },
  async copyTelegramLinkCode() {
    try {
      await navigator.clipboard.writeText(this.telegramLinkCode || '');
      this.showToast(this.t('toasts.copied') || 'Copied', 'success');
    } catch {
      this.showToast(this.t('toasts.copy_failed') || 'Copy failed', 'error');
    }
  },
  telegramLinkMinsRemaining() {
    if (!this.telegramLinkExpiresMs) {
      return 0;
    }
    const ms = this.telegramLinkExpiresMs - Date.now();
    return Math.max(0, Math.ceil(ms / 60000));
  },
  // Admin → Notifications → Telegram → datatable of links.
  async loadTelegramLinks() {
    this.telegramLinksLoading = true;
    this.telegramLinksError = '';
    try {
      const r = await fetch('/api/telegram/links');
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        this.telegramLinksError = j.detail || `HTTP ${r.status}`;
        this.telegramLinks = [];
        return;
      }
      this.telegramLinks = Array.isArray(j.links) ? j.links : [];
    } catch (e) {
      this.telegramLinksError = String(e && e.message ? e.message : e);
    } finally {
      this.telegramLinksLoading = false;
      // Flag the first completion (success OR failure) so the
      // empty-state / table-show gates can flip from "loading
      // skeleton" to the real content without a flash of
      // "no links yet" in between.
      this.telegramLinksLoaded = true;
    }
  },
  async adminUnlinkTelegramRow(row) {
    // Row-action handler — admin clicks the trash button on a
    // datatable row. Confirm dialog mirrors the Users admin's
    // delete-user pattern.
    const u = (row && row.username) || '';
    const tgId = row && row.telegram_user_id;
    if (!tgId) {
      return;
    }
    const confirmed = await this.confirmDialog({
      title: this.t('admin.notifications.telegram_links_unlink_confirm_title') || 'Unlink Telegram user?',
      text: this.t('admin.notifications.telegram_links_unlink_confirm_text', {user: u, tg_id: tgId}),
      icon: 'warning',
      confirmButtonText: this.t('admin.notifications.telegram_links_unlink_button') || 'Unlink',
      cancelButtonText: this.t('actions.cancel'),
    });
    if (!confirmed) {
      return;
    }
    try {
      const r = await fetch('/api/telegram/links/' + encodeURIComponent(String(tgId)), {
        method: 'DELETE',
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        this.showToast(j.detail || `HTTP ${r.status}`, 'error');
        return;
      }
      // Optimistic: drop the row locally + reload to confirm.
      this.telegramLinks = (this.telegramLinks || []).filter(l =>
        l.telegram_user_id !== tgId);
      await this.loadTelegramLinks();
      this.showToast(
        this.t('admin.notifications.telegram_links_unlink_success', {user: j.removed || u}) || 'Unlinked',
        'success'
      );
    } catch (e) {
      this.showToast(String(e && e.message ? e.message : e), 'error');
    }
  },
  async testTelegramConnection() {
    // Mirrors testBeszelConnection — sends a one-shot probe message
    // to the configured Telegram chat / thread, using the in-form
    // values (bot_token / chat_id / thread_id). Falls back to the
    // saved bot_token when the form field is blank (keep-current
    // contract). Phase 1 send-only — result shown inline below the
    // Test button.
    this.telegramTestResult = {pending: true};
    try {
      const r = await fetch('/api/telegram/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          bot_token: this.settings.telegram_bot_token || '',
          chat_id: (this.settings.telegram_chat_id || '').trim(),
          thread_id: (this.settings.telegram_thread_id || '').trim(),
        }),
      });
      const j = await r.json().catch(() => ({}));
      this.telegramTestResult = {
        pending: false,
        ok: !!j.ok,
        detail: j.detail || this.t(j.ok ? 'toasts_extra.test_result_ok' : 'toasts_extra.test_result_failed'),
        status: j.status || 0,
      };
      if (j && j.ok) {
        this.recordTestSuccess('telegram');
        // Stamp the snapshot of form values that passed the test —
        // `canSaveTelegram()` requires this to match the CURRENT form
        // values for Save to unlock. Pre-fix the stamp was only set
        // on settings hydration; a Test against new credentials passed
        // but the Save gate still required a separate `recordTestSuccess`
        // path (broken). Matches the canonical test-before-Save shape
        // shipped for Portainer / OIDC / Asset Inventory.
        this._telegramLastPassedTest = this._telegramTestSnapshot();
      }
    } catch {
      this.telegramTestResult = {pending: false, ok: false, detail: this.t('toasts.network_error')};
    }
  },
  _telegramTestSnapshot() {
    const s = this.settings || {};
    return JSON.stringify({
      enabled: !!s.notify_medium_telegram,
      chat_id: (s.telegram_chat_id || '').trim(),
      thread_id: (s.telegram_thread_id || '').trim(),
      verify_tls: !!s.telegram_verify_tls,
      // Write-only secret — non-empty form value flags "pending"
      // so a typed-but-unsaved token re-locks Save (operator must
      // re-test before committing the new token).
      token_pending: (s.telegram_bot_token || '').trim() ? 'pending' : '',
    });
  },
  canSaveTelegram() {
    if (!(this.settings || {}).notify_medium_telegram) {
      return true;
    }
    return this._telegramLastPassedTest === this._telegramTestSnapshot()
      && !!this._telegramLastPassedTest;
  },
};
