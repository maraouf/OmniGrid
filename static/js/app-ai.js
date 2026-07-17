// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS,NestedFunctionCallJS,ConstantOnRightSideOfComparisonJS,AnonymousFunctionJS,FunctionTooLongJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS,JSUnusedLocalSymbols,JSUnusedGlobalSymbols,ElementNotExported,EmptyCatchBlockJS,UnusedCatchParameterJS,ContinueStatementJS,BreakStatementJS
// noinspection JSVariableNamingConventionJS,LocalVariableNamingConventionJS,FunctionNamingConventionJS,BadName,BadVariableName,FunctionWithMoreThanThreeNegationsJS
// noinspection NegatedIfStatementJS,OverlyComplexBooleanExpressionJS,ExceptionCaughtLocallyJS,PointlessBitwiseExpressionJS,AnonymousCapturingGroupJS,RegExpAnonymousGroup
// noinspection JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,JSAsyncFunctionMissingAwait,JSMissingAwait
// noinspection NegatedConditionalExpressionJS,JSNegatedConditionalExpression,JSIfStatementsCanBeSimplified,IfStatementSimplifyable,RedundantIfStatementJS
// noinspection OverlyLongMethodJS,OverlyLargeMethodJS,OverlyComplexMethodJS,OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,JSCheckFunctionSignatures
// noinspection JSValidateTypes,JSPotentiallyInvalidUsageOfThis,RegExpRedundantEscape,JSDeprecatedSymbols,VoidExpressionJS,JSVoidExpression
// noinspection RedundantLocalVariableJS,JSPossiblyAssignedToNullVariable,JSObjectNullOrUndefined,JSReusedLocalVariable,XHTMLIncompatabilitiesJS,JSAccessInconsistentInXHTML
// noinspection HtmlUnknownTag,HtmlEmptyContent,HtmlEmptyTagsRecommendation,InnerHTMLJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA AI Integration — sidebar chat, command-palette AI mode, AI
// dashboard, AI memory, AI provider settings.

// Module-scope memo for `_renderAiAnswerMd` — the AI-sidebar conversation
// x-for binds each assistant bubble via `x-html="_renderAiAnswerMd(turn.text)"`,
// so Alpine re-runs the (expensive, multi-pass regex) markdown parser on EVERY
// reactive flush (1s host ticker, /api/ops poll, every SSE event…). Keying on
// the raw text string means a stable committed turn is parsed ONCE then served
// from cache — and because the SAME string object is returned on a hit, Alpine
// sees an unchanged x-html value and SKIPS the innerHTML rewrite (no DOM
// rebuild, no lost text-selection in code blocks). A streaming turn whose text
// mutates per chunk gets a fresh key each chunk → re-renders correctly, then
// settles to a cache hit once complete. Cleared wholesale past a cap so a
// long-lived session can't grow it unbounded (conversation is ≤50 turns).
const _aiMdCache = new Map();

export default {
  // ----- AI Assistant sidebar (conversational drawer) ---------------
  // Cmd-K / Ctrl-K + the floating launcher button on the left edge
  // both open this drawer. Multi-turn chat with the active AI
  // provider; each turn carries the prior conversation as context so
  // follow-ups land coherent. Conversation log lives in memory only
  // — closing + reopening keeps it (same SPA load); a hard reload
  // clears it. Each assistant turn carries an optional `action`
  // descriptor (auto-executed) and a `job_id` for thumbs-up/down
  // feedback that writes to ai_jobs.accuracy_score.
  aiSidebarOpen: false,
  aiSidebarQuery: '',
  aiSidebarBusy: false,
  // Count of assistant turns that arrived while the operator was
  // scrolled up reading prior turns — drives the floating
  // "↓ N new" pill at the bottom-right of the log so new arrivals
  // never get missed silently. Cleared when the operator scrolls
  // back near the bottom OR clicks the pill.
  aiSidebarUnseenCount: 0,
  aiConversation: [],          // [{role, text, action?, job_id?, feedback?, ts, error?}]
  aiConversationSearch: '',    // In-conversation scrollback search. Empty = inactive.
  aiConversationSearchMatches: [],   // Indexes into aiConversation[] matching the search.
  aiConversationSearchMatchIdx: 0,   // Cursor into aiConversationSearchMatches.
  aiSidebarFeedbackBusy: {},   // turn-index → bool while POST /api/ai/feedback in flight
  aiSidebarSlashIdx: 0,        // selected index in the slash-command picker
  aiRecentSlashActions: [],    // FIFO of last 5 slash-action ids; persisted to ui_prefs.ai_recent_slash_actions. Only populated for ACTION kinds (not navigation), so "Open host:web01" can't pollute "Pause sampling" recents.
  aiSidebarIncidentChip: null, // {kind, host_id, title, query, ts} — proactive chip rendered above the input when an SSE host-failure / warning-notification event lands AND the sidebar is open. Newest wins (one at a time); click runs the prepared query, X dismisses. Cleared after the query fires so the chip doesn't linger after the operator engaged with it.
  aiSidebarLauncherHidden: false, // Operator preference; hides the floating AI launcher. Cmd-K still opens the sidebar. Persisted to ui_prefs.ai_sidebar_launcher_hidden.
  aiSidebarLauncherHiddenDraft: false, // Draft value for the Settings → Profile checkbox. Marked dirty when it diverges from `aiSidebarLauncherHidden`; Save commits via `setAiSidebarLauncherHidden(draft)`. Hydrated alongside the live value.
  aiSidebarMode: 'approval', // 'approval' (default — destructive actions render an inline-confirm chip) OR 'autonomous' (AI fires every action — including destructive — without prompting). Persisted to ui_prefs.ai_sidebar_mode so the choice follows the operator across browsers / machines. Read by `_runCommandPaletteAction`'s sidebar branch — if mode === 'autonomous', the destructive-confirm path is bypassed entirely and the action fires immediately.
  aiSidebarPinned: false,      // Pin-to-dock mode — sidebar becomes a permanent left-edge split instead of slide-out overlay. Body gets `padding-inline-start: var(--ai-sidebar-width)` via the `body.ai-pinned` class so main view shrinks; backdrop is hidden (no overlay needed). Persisted to ui_prefs.ai_sidebar_pinned. Toggled via the 📌 Pin button in the sidebar header AND by `togglePinAiSidebar()`. Mobile (max-width: 480px) ignores pin (sidebar is 100vw — pinning would hide all content). When pinned, `openAiSidebar()` is implicit (sidebar is always open) and `closeAiSidebar()` un-pins as a side effect.
  // Phase 2 — flips true while /api/ai/host-filter is in flight so
  // the input chrome can show a busy hint without blocking the
  // operator from cancelling and continuing to type.
  commandPaletteAiBulkBusy: false,
  statsAiCost: {},
  statsAiCostLoaded: false,
  // Persisted to `localStorage.statsAiCostRange` so the operator's
  // chosen window survives a reload. Validated against the canonical
  // set in `loadStatsAiCost` below.
  statsAiCostRange: (() => {
    try {
      const v = (typeof localStorage !== 'undefined' && localStorage.getItem('statsAiCostRange')) || '';
      if (['1h', '24h', '7d', '30d', '90d'].includes(v)) {
        return v;
      }
    } catch (_) { /* private mode */
    }
    return '30d';
  })(),
  async loadStatsAiCost(range) {
    // Operator-selectable range applies to BOTH the response-time
    // trend chart AND the Top 10 expensive table — backend wires
    // the cutoff to both. Other sections (MTD / last month / EOM /
    // tokens by provider+model) keep their canonical windows.
    const validRanges = new Set(['1h', '24h', '7d', '30d', '90d']);
    const r0 = (range && validRanges.has(range)) ? range : (this.statsAiCostRange || '30d');
    this.statsAiCostRange = r0;
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('statsAiCostRange', r0);
      }
    } catch (_) { /* private mode */
    }
    try {
      const qs = '?range=' + encodeURIComponent(r0);
      const r = await fetch('/api/admin/stats/ai-cost' + qs);
      if (!r.ok) {
        return;
      }
      this.statsAiCost = await r.json();
    } catch (_) {
    } finally {
      this.statsAiCostLoaded = true;
    }
  },
  // Render a safe subset of Markdown for AI answer bodies. The
  // models routinely return prose with `**bold**` host names,
  // numbered top-N lists, and inline `code`; rendering those as
  // literal asterisks / digits-then-dot looked unfinished. This
  // helper escapes HTML first (XSS guard against any model that
  // tries to inject `<script>` or similar), then translates:
  //   - `**text**`     → <strong>text</strong>
  //   - `` `text` ``  → <code>text</code>
  //   - lines `* x`   → wrapped in <ul class="ai-resp-list"><li>...
  //   - lines `- x`   → wrapped in <ul class="ai-resp-list"><li>...
  //   - lines `1. x`  → wrapped in <ol class="ai-resp-list"><li>...
  //   - blank line    → paragraph break (closes any open list)
  //   - other line    → text + <br>
  // Deliberately tiny — no headings, links, tables, or inline italic
  // (`_text_` would misfire on snake_case identifiers). Output is
  // already-escaped HTML, safe to inject via swal `html:` payload.
  _renderAiAnswerMd(text) {
    if (!text) {
      return '';
    }
    // Per-flush re-parse guard: return the cached render for this exact
    // text (same string object back → Alpine skips the x-html write).
    // See the `_aiMdCache` declaration above for the full rationale.
    const _cacheKey = String(text);
    const _cached = _aiMdCache.get(_cacheKey);
    if (_cached !== undefined) {
      return _cached;
    }
    // Three-pass parser:
    //   1. Extract fenced code blocks (```...```) BEFORE escaping or
    //      inline replacements run. The block body is HTML-escaped
    //      and stashed under a placeholder token; inline / list
    //      processing in pass 2 must not touch the placeholder.
    //   2. Escape + run inline (bold, inline-code) + list / line-break
    //      handling on the placeholder-stitched text.
    //   3. Substitute placeholders back with the final `<pre><code>`
    //      markup PLUS a "Copy" button bound to the block content.
    //
    // Fenced blocks accept an optional language hint after the
    // opening ``` (`bash`, `js`, `yaml`, etc.). The hint is
    // surfaced as `data-language` on the <pre> so a future syntax
    // highlighter can hook in; today the CSS just renders mono.
    const blocks = [];
    const FENCE_RE = /```([a-zA-Z0-9_+\-.]*)\n([\s\S]*?)\n?```/g;
    // Strip common leading whitespace from a fenced block body.
    // AI models often add 4 spaces of indentation inside ``` blocks
    // when the surrounding prose is bullet-indented or colon-led —
    // that becomes spurious indent in the rendered <pre>. Mirrors
    // Python's textwrap.dedent: find the smallest leading-ws prefix
    // across non-empty lines, strip it from every line.
    const dedentBody = (raw) => {
      if (!raw) {
        return raw;
      }
      const lines = raw.split('\n');
      let minIndent = -1;
      for (const line of lines) {
        if (line.trim() === '') {
          continue;
        }
        const m = /^[ \t]*/.exec(line);
        const n = m ? m[0].length : 0;
        if (minIndent < 0 || n < minIndent) {
          minIndent = n;
        }
        if (minIndent === 0) {
          break;
        }
      }
      if (minIndent <= 0) {
        return raw;
      }
      return lines
        .map(line => line.length >= minIndent ? line.slice(minIndent) : line)
        .join('\n');
    };
    const withPlaceholders = text.replace(FENCE_RE, (_m, lang, body) => {
      const idx = blocks.length;
      blocks.push({lang: (lang || '').toLowerCase(), body: dedentBody(body || '')});
      return '\u0000FENCED_CODE_BLOCK_' + idx + '\u0000';
    });
    let s = this._logEscape(withPlaceholders);
    // Inline replacements first so they apply across line groupings.
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    const lines = s.split('\n');
    const parts = [];
    let listKind = null;  // 'ul' | 'ol' | null
    const closeList = () => {
      if (listKind) {
        parts.push('</' + listKind + '>');
        listKind = null;
      }
    };
    for (const line of lines) {
      // Lines that ARE the fenced-block placeholder pass through
      // unwrapped — no <br>, no list match, no enclosing paragraph.
      // That keeps the block as its own visual unit when surrounded
      // by prose and prevents the trailing <br> from injecting an
      // extra blank line under the <pre>.
      // eslint-disable-next-line no-control-regex -- U+0000 is INTENTIONAL: invisible delimiter wrapping the FENCED_CODE_BLOCK marker so it can never collide with operator-typed markdown content.
      if (/^\u0000FENCED_CODE_BLOCK_\d+\u0000$/.test(line.trim())) {
        closeList();
        parts.push(line.trim());
        continue;
      }
      const ulMatch = /^\s*[*-]\s+(.+)$/.exec(line);
      const olMatch = /^\s*(\d+)\.\s+(.+)$/.exec(line);
      if (ulMatch) {
        if (listKind && listKind !== 'ul') {
          closeList();
        }
        if (!listKind) {
          parts.push('<ul class="ai-resp-list">');
          listKind = 'ul';
        }
        parts.push('<li>' + ulMatch[1] + '</li>');
      } else if (olMatch) {
        if (listKind && listKind !== 'ol') {
          closeList();
        }
        if (!listKind) {
          parts.push('<ol class="ai-resp-list">');
          listKind = 'ol';
        }
        parts.push('<li>' + olMatch[2] + '</li>');
      } else {
        closeList();
        if (line.trim() === '') {
          parts.push('');  // blank line = paragraph break
        } else {
          parts.push(line + '<br>');
        }
      }
    }
    closeList();
    let html = parts.join('').replace(/<br>$/, '');
    // Inline-image rendering: a standalone image URL on its own line (e.g.
    // the Speedtest result PNG the AI appends per the palette prompt) becomes
    // a clickable <img> preview instead of a bare text URL. Runs on the
    // already-escaped html, so the matched URL cannot contain a literal " <
    // or > (those were escaped to entities) — no attribute breakout. The
    // strict pattern (anchored https?:// + a known image extension, bounded
    // to its own line) means only a real image URL is ever wrapped; the
    // trailing line break is matched via lookahead so adjacent image lines
    // both convert.
    const AI_IMG_RE = /(^|<br>)\s*(https?:\/\/[^\s<>"']+\.(?:png|jpe?g|gif|webp|svg)(?:\?[^\s<>"']*)?)(?=\s*(?:<br>|$))/gi;
    html = html.replace(AI_IMG_RE, (_m, pre, url) =>
      pre
      + '<a href="' + url + '" target="_blank" rel="noopener" class="ai-resp-img-link">'
      + '<img src="' + url + '" alt="" loading="lazy" class="ai-resp-img"/></a>');
    // Substitute fenced-block placeholders back. Body is escaped here
    // (NOT in pass 1 — escaping pre-replace would corrupt the
    // placeholder regex match). The block element carries
    // `data-code` with the raw body so the copy button can put
    // the original text on the clipboard without re-decoding HTML
    // entities. data-code is JSON-encoded then HTML-escaped to
    // survive the attribute parser; the copy handler reads via
    // dataset and JSON.parse.
    // eslint-disable-next-line no-control-regex -- see FENCED_CODE_BLOCK delimiter comment above (~line 6456).
    html = html.replace(/\u0000FENCED_CODE_BLOCK_(\d+)\u0000/g, (_m, idxStr) => {
      const idx = parseInt(idxStr, 10);
      const blk = blocks[idx];
      if (!blk) {
        return '';
      }
      const escBody = this._logEscape(blk.body);
      const dataCode = this._logEscape(JSON.stringify(blk.body));
      const langAttr = blk.lang
        ? ' data-language="' + this._logEscape(blk.lang) + '"'
        : '';
      return (
        '<div class="ai-resp-code-block"' + langAttr + ' data-code="' + dataCode + '">'
        + '<button type="button" class="ai-resp-code-copy" '
        + 'onclick="window.__ogCopyAiCode(this)" '
        + 'aria-label="' + this._logEscape(this.t('actions.copy') || 'Copy') + '">'
        + '<svg width="12" height="12" aria-hidden="true">'
        + '<use href="/img/ui-sprite.svg#icon-copy"/></svg></button>'
        + '<pre class="ai-resp-pre scrollbar"><code>' + escBody + '</code></pre>'
        + '</div>'
      );
    });
    // Cache the render keyed on the source text. Clear wholesale past a
    // cap so a long session (many distinct turn texts + the response
    // popup) can't grow the Map unbounded — cheap to rebuild on demand.
    if (_aiMdCache.size > 300) {
      _aiMdCache.clear();
    }
    _aiMdCache.set(_cacheKey, html);
    return html;
  },
  _openAiPaletteHistoryDetail(h) {
    // History row's `events` carries the JSON shape the backend
    // wrote in `/api/ai/palette` — `{prompt, answer, action_id,
    // tokens: {prompt, completion, total}, context}`. Render with
    // the same `.ai-resp*` Question / Answer / metadata layout the
    // live response popup uses so an admin clicking through the
    // History tab sees a familiar surface.
    let payload;
    try {
      payload = JSON.parse(h.events || '{}') || {};
    } catch (_) {
      payload = {};
    }
    const esc = (s) => this._logEscape(s ?? '');
    const fmtNum = (n) => Number.isFinite(+n) ? (+n).toLocaleString() : String(n || 0);
    const prompt = (payload.prompt || '').toString().trim() || (this.t('history.ai_palette.no_prompt') || '(no prompt)');
    const answer = (payload.answer || '').toString().trim() || (this.t('history.ai_palette.no_answer') || '(no answer)');
    const actionId = (payload.action_id || '').toString().trim();
    const actionRanLine = actionId
      ? '<div class="ai-resp-action-ran">'
      + '<span aria-hidden="true">✓</span>'
      + '<span>' + esc((this.t('command_palette.ai.action_ran') || 'Ran action: ') + actionId) + '</span>'
      + '</div>'
      : '';
    const tokens = payload.tokens || {};
    const totalTokens = (tokens.total ?? ((tokens.prompt || 0) + (tokens.completion || 0))) || 0;
    const metaChips = [];
    if (h.target_name) {
      metaChips.push('<span class="ai-resp-meta-chip"><strong>' + esc(h.target_name) + '</strong></span>');
    }
    if (h.target_id) {
      metaChips.push('<span class="ai-resp-meta-chip">' + esc(h.target_id) + '</span>');
    }
    if (h.duration) {
      metaChips.push('<span class="ai-resp-meta-chip">'
        + this.t('common.unit_ms_inline', {n: fmtNum(Math.round((h.duration || 0) * 1000))})
        + '</span>');
    }
    if (totalTokens) {
      metaChips.push('<span class="ai-resp-meta-chip">' + fmtNum(totalTokens) + ' tokens</span>');
    }
    if (h.actor) {
      metaChips.push('<span class="ai-resp-meta-chip">'
        + esc(this.t('history.detail.actor').replace(/:$/, '')) + ': <strong>' + esc(h.actor) + '</strong></span>');
    }
    metaChips.push('<span class="ai-resp-meta-chip">' + esc(this.formatTime(h.ts)) + '</span>');
    // Class-based variants (`--error` / `--danger`) move the color-mix()
    // declarations into style.css where they belong. No inline-style
    // blocks here, no PyCharm "Convert color to rgb()" hints firing on
    // JS-string contents, and theme-switch reflows correctly via the
    // existing CSS-variable cascade.
    const errorBlock = h.error
      ? '<div class="ai-resp-section">'
      + '<div class="ai-resp-label ai-resp-label--danger">'
      + '<span class="ai-resp-label-dot ai-resp-label-dot--danger" aria-hidden="true"></span>'
      + esc(this.t('history.detail.error').replace(/:$/, '') || 'Error')
      + '</div>'
      + '<div class="ai-resp-answer ai-resp-answer--error">'
      + esc(h.error)
      + '</div>'
      + '</div>'
      : '';
    Swal.fire({
      title: this.t('command_palette.ai.answer_title') || 'AI response',
      html: '<div class="ai-resp">'
        + '<div class="ai-resp-section">'
        + '<div class="ai-resp-label is-question">'
        + '<span class="ai-resp-label-dot" aria-hidden="true"></span>'
        + esc(this.t('command_palette.ai.question_label') || 'Question')
        + '</div>'
        + '<div class="ai-resp-question">' + esc(prompt) + '</div>'
        + '</div>'
        + '<div class="ai-resp-section">'
        + '<div class="ai-resp-label is-answer">'
        + '<span class="ai-resp-label-dot" aria-hidden="true"></span>'
        + esc(this.t('command_palette.ai.answer_label') || 'Answer')
        + '</div>'
        + '<div class="ai-resp-answer">' + this._renderAiAnswerMd(answer) + '</div>'
        + actionRanLine
        + '</div>'
        + errorBlock
        + (metaChips.length ? '<div class="ai-resp-meta">' + metaChips.join('') + '</div>' : '')
        + '</div>',
      width: 720,
      showConfirmButton: false,
      showCloseButton: true,
      background: this._cssVar('--surface'),
      color: this._cssVar('--text'),
    });
  },
  // Run a per-app SKILL from the AI sidebar and capture its result INTO
  // the assistant turn (so the suggestion / status renders in the chat,
  // not just a toast). Sidebar-only — the modal command palette keeps the
  // generic `_runCommandPaletteAction` → toast path. When the skill returns
  // a `followup` (e.g. Seerr's suggest-a-movie → request-this-movie), it's
  // stamped on the turn's `skill_panel` so the template renders a one-click
  // button. Non-destructive skills only (destructive ones keep the
  // inline-confirm-chip path).
  // True when the per-app skill named in `actionData.skill_id` declares
  // `destructive: true` in the skill registry (me.client_config.app_skills,
  // keyed by catalog slug). Skill ids are app-prefixed (radarr_remove_movie,
  // pihole_disable…) so a flat scan across every slug's list is unambiguous.
  // Used to gate the AI-emitted run_app_skill dispatch — the generic
  // descriptor is destructive:false, so without this a destructive skill
  // would fire with no inline-confirm chip / autonomous gate.
  _appSkillIsDestructive(actionData) {
    const sid = (actionData && actionData.skill_id ? actionData.skill_id : '')
      .toString().trim();
    if (!sid) {
      return false;
    }
    const cc = (this.me && this.me.client_config) || {};
    const map = (cc && cc.app_skills) || {};
    // Object.values (not for…in) so we iterate own enumerable values only —
    // no inherited-member leakage, and the slug key isn't needed here.
    for (const list of Object.values(map)) {
      if (!Array.isArray(list)) {
        continue;
      }
      for (const sk of list) {
        if (sk && sk.id === sid) {
          return !!sk.destructive;
        }
      }
    }
    return false;
  },
  // Stamp a per-app skill's result onto an assistant turn's `skill_panel`
  // so its output (e.g. a Radarr status summary) renders inline in the chat
  // instead of vanishing into a toast. Shared by the slash-selected app-skill
  // path (_runCommandPaletteAction) and the approval-confirm path
  // (confirmInlineAction). No-op when `ret` isn't a skill-result shape.
  _stampSkillPanelFromResult(turn, ret) {
    if (!turn || !ret || typeof ret !== 'object') {
      return;
    }
    // Only a per-app skill result carries `detail` / `ok`; other action
    // run() returns (undefined / navigation objects) are ignored.
    if (typeof ret.detail !== 'string' && !('ok' in ret)) {
      return;
    }
    const panel = {
      ok: !!ret.ok,
      detail: (ret.detail || '').toString(),
      image_url: (ret.image_url || '').toString(),
    };
    if (ret.ok && ret.followup && typeof ret.followup === 'object'
      && ret.followup.skill_id && ret.host_id != null && ret.service_idx != null) {
      panel.followup = {
        skill_id: String(ret.followup.skill_id),
        arg: (ret.followup.arg != null) ? String(ret.followup.arg) : '',
        label: (ret.followup.label || '').toString(),
        destructive: !!ret.followup.destructive,
        host_id: String(ret.host_id),
        service_idx: ret.service_idx,
      };
    }
    turn.skill_panel = panel;
  },
  async _aiSidebarRunSkill(turnIdx) {
    const turn = this.aiConversation[turnIdx];
    if (!turn || !turn.action_data || typeof this.runAppSkill !== 'function') {
      return;
    }
    const d = turn.action_data;
    const host = (d.host_id || '').toString().trim();
    const skillId = (d.skill_id || '').toString().trim();
    let idx = d.service_idx;
    idx = (typeof idx === 'number') ? idx : parseInt(idx, 10);
    const arg = (d.arg == null) ? '' : String(d.arg).trim();
    if (!host || !skillId || isNaN(idx) || idx < 0) {
      return;
    }
    // Show a spinner row while the skill runs — the Seerr suggest skill now
    // queries the library + TMDB, which takes a couple of seconds, so the
    // user gets visible feedback that something's happening.
    turn.skill_running = true;
    // No initializer — `res` is assigned in the try before any read (a throw
    // exits via the propagated exception, never reaching the read below).
    let res;
    try {
      res = await this.runAppSkill({host_id: host, service_idx: idx}, skillId, arg, {silent: true});
    } finally {
      turn.skill_running = false;
    }
    if (!res || typeof res !== 'object') {
      return;
    }
    const panel = {
      ok: !!res.ok,
      detail: (res.detail || '').toString(),
      image_url: (res.image_url || '').toString(),
    };
    if (res.ok && res.followup && typeof res.followup === 'object' && res.followup.skill_id) {
      panel.followup = {
        skill_id: String(res.followup.skill_id),
        arg: (res.followup.arg != null) ? String(res.followup.arg) : '',
        label: (res.followup.label || '').toString(),
        destructive: !!res.followup.destructive,
        host_id: host,
        service_idx: idx,
      };
    }
    turn.skill_panel = panel;
    if (typeof this.persistAiConversation === 'function') {
      void this.persistAiConversation();
    }
  },

  // One-click follow-up button handler (e.g. "Request <movie> on Seerr"
  // after a suggestion). Runs the follow-up skill stamped on the turn's
  // `skill_panel.followup`, then records the outcome so the button is
  // replaced by a success/failure line. No popups (sidebar contract).
  async aiRunSkillFollowup(turnIdx) {
    const turn = this.aiConversation[turnIdx];
    if (!turn || !turn.skill_panel || !turn.skill_panel.followup) {
      return;
    }
    const sp = turn.skill_panel;
    if (sp.followup_busy || sp.followup_done) {
      return;
    }
    const fu = sp.followup;
    // Destructive follow-up (e.g. Tdarr requeue) respects the sidebar mode:
    // in APPROVAL (normal) mode the first click renders an inline Yes/Cancel
    // confirm chip (no popup — sidebar contract) and waits; AUTONOMOUS mode
    // fires immediately. Non-destructive follow-ups (Seerr request) never gate.
    // The chip's Yes button re-invokes this handler — `followup_confirming` is
    // then true so the gate is skipped and the run proceeds.
    if (fu.destructive && this.aiSidebarMode !== 'autonomous' && !sp.followup_confirming) {
      sp.followup_confirming = true;
      if (typeof this.persistAiConversation === 'function') {
        void this.persistAiConversation();
      }
      return;
    }
    sp.followup_confirming = false;
    sp.followup_busy = true;
    // No initializer — `res` is assigned in the try before any read (a throw
    // exits the function via the propagated exception, never reaching the
    // read below). An `= null` here is flagged dead by no-useless-assignment.
    let res;
    try {
      res = await this.runAppSkill(
        {host_id: fu.host_id, service_idx: fu.service_idx},
        // The labelled follow-up button click IS the confirmation for a
        // destructive follow-up (e.g. requeue) — thread confirm so the backend
        // route doesn't 409 it.
        fu.skill_id, fu.arg, {silent: true, confirm: !!fu.destructive});
    } finally {
      sp.followup_busy = false;
    }
    if (res && typeof res === 'object') {
      sp.followup_result = {ok: !!res.ok, detail: (res.detail || '').toString()};
      if (res.ok) {
        sp.followup_done = true;
      }
    }
    if (typeof this.persistAiConversation === 'function') {
      void this.persistAiConversation();
    }
  },

  // Cancel the inline confirm for a destructive follow-up (approval mode) —
  // dismisses the Yes/Cancel chip and restores the follow-up button.
  aiCancelSkillFollowup(turnIdx) {
    const turn = this.aiConversation[turnIdx];
    if (!turn || !turn.skill_panel) {
      return;
    }
    turn.skill_panel.followup_confirming = false;
    if (typeof this.persistAiConversation === 'function') {
      void this.persistAiConversation();
    }
  },

  // Same write-through pattern for the host-drawer time-range
  // picker. Stored under `ui_prefs.host_history_range` as an int so
  // the operator's preferred range follows them across browsers.
  // Cross-browser / cross-machine persistence for the AI assistant
  // conversation log. Stored under `ui_prefs.ai_conversation` as a
  // capped JSON array (last 50 turns) so a hard reload + login on
  // a different browser still restores the session. Called after
  // every conversation mutation (send / slash run / clear). Skipped
  // for API-token pseudo-users (negative ids).
  async persistAiConversation() {
    // STRICT gate — skip API-token pseudo-users (negative ids) AND
    // unauthenticated callers. For VALID users with a numeric id,
    // every push must write through to BOTH the per-browser
    // localStorage cache AND the DB-backed ui_prefs. Pre-fix logic
    // returned early on `!this.me.id` which skipped persistence
    // when `me.id === 0` (defensively unlikely but covered) — the
    // tighter check below preserves "skip API tokens" while
    // correctly persisting for every real user.
    const meId = this.me && this.me.id;
    const isValidUserId = (typeof meId === 'number' && meId >= 0)
      || (typeof meId === 'string' && meId && !meId.startsWith('-'));
    if (!isValidUserId) {
      return;
    }
    // Cap at 50 turns + drop in-flight bookkeeping (feedback-busy
    // flags etc. live elsewhere) so the round-trip stays small.
    const turns = (this.aiConversation || []).slice(-50).map(t => ({
      role: t.role || '',
      text: t.text || '',
      action_id: t.action_id || null,
      action_label: t.action_label || null,
      action_ran: !!t.action_ran,
      slash: !!t.slash,
      job_id: (t.job_id !== undefined && t.job_id !== null) ? t.job_id : null,
      feedback: t.feedback || null,
      provider: t.provider || '',
      model: t.model || '',
      response_time_ms: Number(t.response_time_ms) || 0,
      tokens: Number(t.tokens) || 0,
      error: t.error || null,
      // `host_ids` carries the disk-projection chart targets the
      // AI returned with the answer (HOSTS protocol). Pre-fix this
      // field was OMITTED from the persist mapping, so even though
      // every fresh turn carried it in memory the DB-stored copy
      // didn't — reload found no `host_ids` to repopulate, the
      // `<div x-show>` gate evaluated false, and the chart shells
      // never re-rendered. Same drift class for `pending_confirm` /
      // `pending_action` / `cancelled` — the inline-confirm chip's
      // restore-after-reload contract needs them.
      host_ids: Array.isArray(t.host_ids) ? t.host_ids.slice() : null,
      chart_kind: (typeof t.chart_kind === 'string' && t.chart_kind) ? t.chart_kind : null,
      // Per-action parameters — currently only retag_image consumes
      // them (tag from ACTION_TAG, item-name-or-id from
      // ACTION_ITEM). Persisted so the inline-confirm chip's
      // re-fire after a reload uses the same params.
      action_tag: (t.action_tag || '').toString(),
      action_item: (t.action_item || '').toString(),
      action_data: (t.action_data && typeof t.action_data === 'object') ? t.action_data : null,
      action_hosts: Array.isArray(t.action_hosts) ? t.action_hosts.slice() : [],
      pending_confirm: !!t.pending_confirm,
      pending_action: t.pending_action || null,
      cancelled: !!t.cancelled,
      // Inline per-app skill result + follow-up button state (e.g. a Seerr
      // movie suggestion with its "Request on Seerr" button). Persisted so
      // the suggestion + button survive a reload / cross-browser hydration.
      skill_panel: (t.skill_panel && typeof t.skill_panel === 'object') ? t.skill_panel : null,
      // `ts` is the per-turn epoch-ms stamp set when the turn is
      // pushed onto `aiConversation`. Load-bearing for the
      // cross-device hydration filter at init() — the Clear button
      // stamps `ai_conversation_cleared_at` on the user's
      // ui_prefs, and hydration filters by `Number(t.ts) > cutoff`
      // so older turns hide on screen but stay in the DB. Without
      // `ts` in the async-persist map (this map), every PATCH
      // landed turns with `ts: undefined` → `Number(undefined) =
      // NaN` → `NaN > cutoff = false` → every turn filtered out
      // when the user opened a different browser / machine after
      // ever clicking Clear once. The sync variant
      // (`persistAiConversationSync`) already had the field; the
      // two paths now match. User-flagged: "AI history is not
      // preserved across different computers for the same user".
      ts: t.ts || null,
    }));
    // Write-through localStorage cache — keyed per user so multiple
    // logins on the same browser don't trample each other. Captures
    // the same shape the DB sees so init's hydration helper can
    // restore from EITHER source. This makes the chat survive
    // (a) a PATCH that races with a refresh, (b) a backend that's
    // mid-restart when the SPA tries to persist, (c) the per-tuning
    // /api/me wholesale-replace that briefly overwrites
    // me.ui_prefs.ai_conversation with a stale snapshot. DB stays
    // the cross-browser source of truth; localStorage is the
    // per-browser fast-path. Best-effort: a thrown SecurityError
    // (private mode / quota) just falls back to DB-only persistence.
    try {
      if (typeof localStorage !== 'undefined') {
        const key = 'aiConversation:' + meId;
        localStorage.setItem(key, JSON.stringify(turns));
      }
    } catch (e) {
      if (window.console && console.warn) {
        console.warn('[ai-conv] localStorage write failed:', e);
      }
    }
    try {
      const r = await fetch('/api/me/ui-prefs', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prefs: {ai_conversation: turns}}),
      });
      if (!r.ok && window.console && console.warn) {
        const txt = await r.text().catch(() => '');
        console.warn('[ai-conv] persist to DB returned ' + r.status, txt.slice(0, 200));
      }
    } catch (e) {
      if (window.console && console.warn) {
        console.warn('[ai-conv] persist to DB failed:', e);
      }
    }
  },

  // Synchronous variant for `beforeunload` / `pagehide` /
  // `visibilitychange:hidden`. The async `await fetch` would be
  // killed when the page is unloading; `navigator.sendBeacon`
  // is the spec-correct path: the browser queues the request
  // and ships it after the page is gone. localStorage is
  // synchronous and always lands. Together they make the
  // unload-time save bulletproof.
  persistAiConversationSync() {
    const meId = this.me && this.me.id;
    const isValidUserId = (typeof meId === 'number' && meId >= 0)
      || (typeof meId === 'string' && meId && !meId.startsWith('-'));
    if (!isValidUserId) {
      return;
    }
    const turns = (this.aiConversation || []).slice(-50).map(t => ({
      role: t.role || '',
      text: t.text || '',
      action_id: t.action_id || null,
      action_label: t.action_label || null,
      action_ran: !!t.action_ran,
      slash: !!t.slash,
      job_id: (t.job_id !== undefined && t.job_id !== null) ? t.job_id : null,
      feedback: t.feedback || null,
      provider: t.provider || '',
      model: t.model || '',
      response_time_ms: Number(t.response_time_ms) || 0,
      tokens: Number(t.tokens) || 0,
      error: t.error || null,
      host_ids: Array.isArray(t.host_ids) ? t.host_ids.slice() : null,
      chart_kind: (typeof t.chart_kind === 'string' && t.chart_kind) ? t.chart_kind : null,
      action_tag: (t.action_tag || '').toString(),
      action_item: (t.action_item || '').toString(),
      action_data: (t.action_data && typeof t.action_data === 'object') ? t.action_data : null,
      action_hosts: Array.isArray(t.action_hosts) ? t.action_hosts.slice() : [],
      pending_confirm: !!t.pending_confirm,
      pending_action: t.pending_action || null,
      cancelled: !!t.cancelled,
      // Inline per-app skill result + follow-up button state (e.g. a Seerr
      // movie suggestion with its "Request on Seerr" button). Persisted so
      // the suggestion + button survive a reload / cross-browser hydration.
      skill_panel: (t.skill_panel && typeof t.skill_panel === 'object') ? t.skill_panel : null,
      ts: t.ts || null,
    }));
    // localStorage write — synchronous, always lands.
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('aiConversation:' + meId, JSON.stringify(turns));
      }
    } catch (_) {
    }
    // sendBeacon — browser-queued, survives page unload. The CSRF
    // token must come from the cookie since beacon doesn't allow
    // custom headers. Backend accepts a 'application/json' beacon
    // body the same as a regular PATCH.
    try {
      if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
        const body = new Blob(
          [JSON.stringify({prefs: {ai_conversation: turns}})],
          {type: 'application/json'}
        );
        // sendBeacon uses POST by default. Backend's PATCH-only
        // endpoint won't accept a beacon. Use a dedicated POST
        // endpoint mirror that accepts the same body shape.
        navigator.sendBeacon('/api/me/ui-prefs/beacon', body);
      }
    } catch (_) {
    }
  },
  // ----- AI Assistant sidebar (conversational drawer) ----------------
  //
  // Floating launcher button (left edge) + Cmd-K both open the
  // sidebar. Multi-turn chat: each user query carries the prior
  // conversation as context so follow-ups land coherent. Each
  // assistant turn surfaces the action that ran (auto-executed) and
  // accepts thumbs-up/down feedback that updates the row in
  // ai_jobs.
  openAiSidebar() {
    // Capture the previously-focused element so closing the sidebar
    // returns focus to it. WCAG 2.4.3 (focus order) — without this,
    // closing the dialog loses the operator's keyboard position.
    try {
      this._aiSidebarReturnFocus = document.activeElement;
    } catch (_) {
      this._aiSidebarReturnFocus = null;
    }
    this.aiSidebarOpen = true;
    // Focus the input + scroll the conversation log to the bottom
    // next tick so the operator lands on the LATEST turn (not the
    // top of a long restored chat) and can start typing immediately.
    // Two ticks because Alpine renders the drawer's `.open` class
    // first, then the slide-in transform completes — the log's
    // scrollHeight is only finalized after the layout settles.
    this.$nextTick(() => {
      const el = document.getElementById('og-ai-sidebar-input');
      if (el && typeof el.focus === 'function') {
        el.focus();
      }
      // FORCE the scroll-to-bottom on open. The non-force path
      // honours the user's manual scroll position (chat-app
      // convention — paired with the jump-to-latest pill below), but on a
      // FRESH open we always want to land on the latest turn
      // regardless of where the log was when the drawer last
      // closed. Without `force: true`, a previously-scrolled-up
      // log keeps that position when reopened, which is wrong:
      // re-opening the chat is an explicit user action that
      // should reset their reading position to the most recent
      // assistant reply.
      this._scrollAiSidebarToBottom({force: true});
      this.$nextTick(() => this._scrollAiSidebarToBottom({force: true}));
    });
  },
  closeAiSidebar() {
    this.aiSidebarOpen = false;
    // Restore focus to whatever was focused before the sidebar opened
    // so keyboard navigation continues from where it was. Guarded
    // against the previous element being detached from the DOM by
    // the time we restore (tab close / SPA route change).
    const ret = this._aiSidebarReturnFocus;
    this._aiSidebarReturnFocus = null;
    if (ret && typeof ret.focus === 'function' && document.contains(ret)) {
      try {
        ret.focus();
      } catch (_) { /* noop */
      }
    }
  },
  toggleAiSidebar() {
    if (this.aiSidebarOpen) {
      this.closeAiSidebar();
    } else {
      this.openAiSidebar();
    }
  },
  // Proactive incident chip — surface a one-click investigate
  // affordance when an SSE host-failure or warning-level
  // notification event lands AND the AI sidebar is currently
  // open. Stored as a single field (newest wins) so a flurry of
  // events doesn't stack chips; clicking the chip runs the
  // prepared query, X dismisses without firing.
  _setAiIncidentChip(chip) {
    // Only surface chips when the sidebar is open — otherwise the
    // operator hasn't asked for AI engagement and the chip would
    // sit invisible until next open. The notifications popup
    // already covers the closed-sidebar case.
    if (!this.aiSidebarOpen) {
      return;
    }
    // Don't repeat the same incident chip if it's already showing
    // (de-dupe on host_id + kind so a rapid-fire SSE storm doesn't
    // visually flicker).
    const cur = this.aiSidebarIncidentChip;
    if (cur && cur.host_id === chip.host_id && cur.kind === chip.kind) {
      return;
    }
    this.aiSidebarIncidentChip = Object.assign({}, chip, {ts: Date.now()});
  },
  dismissAiIncidentChip() {
    this.aiSidebarIncidentChip = null;
  },
  runAiIncidentChip() {
    const chip = this.aiSidebarIncidentChip;
    if (!chip || !chip.query) {
      return;
    }
    this.aiSidebarIncidentChip = null;
    this._setAiSidebarQuery(chip.query);
    // Focus the input so the operator sees the populated query
    // before send (allows last-second edit), then fire on the
    // next tick so the textarea reflects the value.
    this.$nextTick(() => {
      if (typeof this.sendAiSidebarMessage === 'function') {
        this.sendAiSidebarMessage();
      }
    });
  },

  // Export the visible AI conversation as a downloadable file.
  // Format = 'txt' (human-readable transcript) or 'json' (full
  // structured payload). Triggered from the AI sidebar header
  // buttons; gated by `me.client_config.ai_conversation_export_enabled`
  // (master toggle in Admin → AI Integration). Only the VISIBLE
  // conversation is exported — turns hidden by a prior Clear stay
  // in `users.ui_prefs.ai_conversation` but aren't included
  // because the operator already declared them "screen-cleared"
  // for the current viewing session.
  //
  // TXT shape — one user / assistant turn per stanza:
  //   ## User · 2026-05-07 22:31:04
  //   how does the disk projection work?
  //
  //   ## Assistant · gemini · gemini-2.5-pro · 4474+183 tokens · 16532ms
  //   The chart projects forward by linear regression over...
  //   [Ran: switch_theme_dark]
  //   [Hosts: web01, nas-zfs]
  //
  // JSON shape — every turn field carried along (export-equivalent
  // to what `persistAiConversation` writes to ui_prefs, plus a
  // top-level metadata block).
  exportAiConversation(format) {
    const turns = (this.aiConversation || []);
    if (!turns.length) {
      this.showToast(this.t('ai_sidebar.export_empty_toast'), 'error');
      return;
    }
    const me = this.me || {};
    const fmtTs = (ms) => {
      if (!ms || !Number.isFinite(Number(ms))) {
        return '';
      }
      try {
        const d = new Date(Number(ms));
        const pad = (n) => String(n).padStart(2, '0');
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
          + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
      } catch (_) {
        return '';
      }
    };
    let blob, filename;
    try {
      if (format === 'json') {
        // Full structured payload — fields match the persist-shape
        // `persistAiConversation` writes plus an `_export_meta` block
        // for context (who exported, when, app version).
        const payload = {
          _export_meta: {
            exported_at: new Date().toISOString(),
            app_version: window.OG_VERSION || null,
            exported_by: me.username || null,
            turn_count: turns.length,
          },
          turns: turns.map(t => ({
            role: t.role || '',
            text: t.text || '',
            ts: Number(t.ts) || 0,
            ts_iso: t.ts ? new Date(Number(t.ts)).toISOString() : null,
            provider: t.provider || null,
            model: t.model || null,
            response_time_ms: Number(t.response_time_ms) || null,
            tokens: Number(t.tokens) || null,
            prompt_tokens: Number(t.prompt_tokens) || null,
            completion_tokens: Number(t.completion_tokens) || null,
            action_id: t.action_id || null,
            action_label: t.action_label || null,
            action_ran: !!t.action_ran,
            slash: !!t.slash,
            host_ids: Array.isArray(t.host_ids) ? t.host_ids.slice() : null,
            chart_kind: (typeof t.chart_kind === 'string' && t.chart_kind) ? t.chart_kind : null,
            action_tag: (t.action_tag || '').toString(),
            action_item: (t.action_item || '').toString(),
            action_data: (t.action_data && typeof t.action_data === 'object') ? t.action_data : null,
            action_hosts: Array.isArray(t.action_hosts) ? t.action_hosts.slice() : [],
            feedback: t.feedback || null,
            error: t.error || null,
            cancelled: !!t.cancelled,
            pending_confirm: !!t.pending_confirm,
            pending_action: t.pending_action || null,
            job_id: (t.job_id !== undefined && t.job_id !== null) ? t.job_id : null,
          })),
        };
        blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
        filename = 'omnigrid-ai-conversation-' + new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19) + '.json';
      } else {
        // Plain-text transcript. One stanza per turn separated by a
        // blank line. Subline carries provider / model / token /
        // duration metadata when present so the export is
        // self-contained for triage / debugging.
        const lines = [];
        lines.push('# OmniGrid AI conversation export');
        lines.push('# Exported: ' + new Date().toISOString());
        if (me.username) {
          lines.push('# By: ' + me.username);
        }
        if (window.OG_VERSION) {
          lines.push('# App version: ' + window.OG_VERSION);
        }
        lines.push('# Turns: ' + turns.length);
        lines.push('');
        for (const t of turns) {
          const role = (t.role === 'user') ? 'User' : (t.role === 'assistant' ? 'Assistant' : (t.role || 'turn'));
          const meta = [];
          if (t.provider) {
            meta.push(t.provider);
          }
          if (t.model) {
            meta.push(t.model);
          }
          if (Number(t.tokens)) {
            meta.push(Number(t.tokens) + ' tokens');
          }
          if (Number(t.response_time_ms)) {
            meta.push(Number(t.response_time_ms) + 'ms');
          }
          const ts = fmtTs(t.ts);
          const head = '## ' + role
            + (ts ? ' · ' + ts : '')
            + (meta.length ? ' · ' + meta.join(' · ') : '');
          lines.push(head);
          if (t.text) {
            lines.push(String(t.text));
          }
          if (t.error) {
            lines.push('[Error: ' + t.error + ']');
          }
          if (t.action_label) {
            lines.push('[' + (t.action_ran ? 'Ran' : 'Proposed') + ': ' + t.action_label + ']');
          }
          if (t.cancelled) {
            lines.push('[Cancelled]');
          }
          if (Array.isArray(t.host_ids) && t.host_ids.length) {
            lines.push('[Hosts: ' + t.host_ids.join(', ') + ']');
          }
          if (t.feedback) {
            lines.push('[Feedback: ' + t.feedback + ']');
          }
          lines.push('');
        }
        blob = new Blob([lines.join('\n')], {type: 'text/plain;charset=utf-8'});
        filename = 'omnigrid-ai-conversation-' + new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19) + '.txt';
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      // Defer revoke until after click so the download path can
      // fully capture the blob URL — some browsers (Safari) drop
      // the download if the URL is revoked synchronously.
      setTimeout(() => {
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }, 100);
      this.showToast(this.t('ai_sidebar.export_done_toast', {filename}), 'success');
    } catch (e) {
      this.showToast(this.t('ai_sidebar.export_failed_toast', {error: String(e && e.message || e)}), 'error');
    }
  },

  clearAiConversation() {
    // SCREEN-clear only: empty the visible chat AND stamp a "cleared
    // at" timestamp into ui_prefs, but DON'T delete the underlying
    // conversation array from the DB. The next /api/me hydration
    // filters turns with `ts <= ai_conversation_cleared_at` so the
    // operator sees a fresh chat going forward, while every prior
    // turn stays preserved in ui_prefs.ai_conversation (and every
    // call stays in ai_jobs) for learning / dashboard analytics.
    // Operator-flagged: "make sure clear button doesnt clear the
    // database, just clear screen, but previous conversations is
    // there so we can learn from it".
    this.aiConversation = [];
    this._setAiSidebarQuery('');
    this.aiSidebarFeedbackBusy = {};
    const cutoff = Date.now();
    // Keep the in-memory cutoff so any turn pushed AFTER Clear (a
    // brand-new send / slash run) renders as expected on the
    // current page; the next reload will re-apply the same cutoff
    // from ui_prefs and filter pre-Clear turns out of the
    // hydration.
    this._aiConversationClearedAt = cutoff;
    // Drop the per-browser write-through cache so the localStorage
    // fallback in init's hydration doesn't restore pre-Clear turns
    // when the DB cutoff PATCH races a refresh. The DB still holds
    // every turn for the analytics / learning surface.
    try {
      if (typeof localStorage !== 'undefined' && this.me && this.me.id) {
        localStorage.removeItem('aiConversation:' + this.me.id);
      }
    } catch (_) { /* private-mode — skip */
    }
    // Best-effort write the cutoff to ui_prefs WITHOUT touching the
    // ai_conversation array. Skip for API-token pseudo-users
    // (negative ids) since /api/me/ui-prefs returns 400 for them.
    if (this.me && this.me.id && this.me.id >= 0) {
      try {
        fetch('/api/me/ui-prefs', {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({prefs: {ai_conversation_cleared_at: cutoff}}),
        });
      } catch (_) { /* fire-and-forget; reload re-applies the cutoff */
      }
    }
    // Mirror the cutoff onto `me.ui_prefs` so a tab that's still
    // open uses the new value if it re-evaluates.
    if (this.me && this.me.ui_prefs) {
      this.me.ui_prefs.ai_conversation_cleared_at = cutoff;
    }
  },
  aiSidebarSurfaceEnabled() {
    // Same gate as the legacy palette's AI fallback row.
    return (typeof this._aiPaletteSurfaceEnabled === 'function')
      ? this._aiPaletteSurfaceEnabled()
      : false;
  },
  async sendAiSidebarMessage() {
    const q = (this.aiSidebarQuery || '').trim();
    if (!q || this.aiSidebarBusy) {
      return;
    }
    if (!this.aiSidebarSurfaceEnabled()) {
      if (typeof this.showToast === 'function') {
        this.showToast(this.t('command_palette.ai.disabled')
          || 'AI is disabled — enable it in Admin → AI Integration', 'error');
      }
      return;
    }
    // Push the user turn immediately so the operator sees feedback.
    this.aiConversation.push({
      role: 'user',
      text: q,
      ts: Date.now(),
    });
    this._setAiSidebarQuery('');
    this.aiSidebarBusy = true;
    this._scrollAiSidebarToBottom();
    // Lazy-fetch the public-IP block so the AI can answer
    // "what's my public IP / ISP" questions in the same turn.
    // Fire-and-forget — failure leaves publicIp null and the
    // prompt-builder skips the block cleanly.
    try {
      await this._ensurePublicIp();
    } catch (_) {
    }
    // Also pre-fetch the IP change history so the AI can answer
    // "when did my IP / ISP last change?" questions in the same turn.
    // Same fire-and-forget contract — cached for 10 minutes; empty
    // cache → AI sees no history block + says it doesn't know rather
    // than hallucinating.
    try {
      await this._ensurePublicIpHistory();
    } catch (_) {
    }
    // Pre-fetch weather history (current + past 7 days of hourly
    // samples + moon phases) so the AI palette can answer "what was
    // the weather yesterday afternoon" / "when was the last full
    // moon" questions in the same turn. Same fire-and-forget
    // contract; empty cache → AI sees no historical block + says it
    // doesn't know rather than hallucinating phases.
    try {
      await this._ensureWeatherHistory();
    } catch (_) {
    }
    // Pre-fetch prayer times + the Hijri date so the AI can answer
    // "when is the next prayer / Maghrib" / "what's the Hijri date"
    // questions in the same turn. Fire-and-forget; gated off / no
    // location → the prompt-builder skips the block cleanly.
    try {
      await this._ensurePrayerTimes();
    } catch (_) {
    }
    // Persist the user turn IMMEDIATELY — pre-fix `persistAiConversation`
    // only fired in the `finally` block after the AI response landed,
    // so a refresh / redeploy that hit during a slow LLM round-trip
    // (Gemini 2.5 Pro typically takes 5-15 s) lost the question
    // entirely. Persisting twice (once now, once after the assistant
    // turn lands) is cheap; both writes round-trip the full capped
    // array, so the second write supersedes the first cleanly.
    try {
      // Fire-and-forget persist — `void` makes the intent explicit to
      // both readers and the IDE's missing-await inspection. The
      // assistant turn that follows will call persist again; both
      // writes round-trip the full capped array so the second
      // supersedes the first cleanly.
      void this.persistAiConversation();
    } catch (_) {
    }

    // Build conversation context for the backend — only role + text
    // pairs (no metadata). Cap at the last 12 turns so token budget
    // doesn't balloon on long chats.
    const priorTurns = this.aiConversation
      .slice(0, -1)  // exclude the user turn we just pushed
      .filter(t => t && t.role && (t.text || '').trim())
      .slice(-12)
      .map(t => ({role: t.role, text: t.text}));

    // Build a RICH context — same shape as the legacy modal palette
    // path. Without the metric fields (cpu_pct / mem_pct / disk_pct
    // / disk_free_gb / status / paused / providers) the AI has
    // nothing to work with when answering "which hosts are out of
    // disk?" and falls back to fabricating host names + values.
    // Lazy-fetch hosts on demand if the array is still empty —
    // covers the edge case where init's pre-fetch hasn't completed
    // yet by the time the operator opens the sidebar and sends a
    // query. Best-effort: a fetch failure leaves `this.hosts`
    // empty and the AI correctly says "no host data available".
    if (!Array.isArray(this.hosts) || this.hosts.length === 0) {
      if (typeof this.loadHosts === 'function') {
        try {
          await this.loadHosts();
        } catch (_) { /* fall through */
        }
      }
    }
    // Pre-fetch sampler-health diagnostic for any host directly
    // named in the user's question. Lets the AI see the
    // samples_in_window block (per-table count, median gap, newest
    // age) for the host being asked about — the canonical "why is
    // chart X cut" diagnostic. Bounded by `MAX_PREFETCH` so a
    // question naming 50 hosts doesn't fan out 50 backend calls;
    // the AI can still ask follow-up questions for the rest.
    // Keyword-gated to avoid pre-fetching on every question — only
    // when the user is plausibly asking a chart / data / sampler
    // question (matches a small whitelist of trigger words). Pre-
    // fetch fires in parallel; failures decay silently to "no
    // sampler_health data" — fmtHost gates on cache presence.
    try {
      const lc = q.toLowerCase();
      // Diagnosis keywords trigger the per-host debug + per-service
      // detail pre-fetch. Two clusters:
      //   chart-diagnosis (chart / cut / missing / sampler / etc.)
      //     — drives "why is X chart cut" investigations.
      //   service-diagnosis (service / unit / failed / running /
      //   nginx / systemd / etc.) — drives "what's failing on X"
      //     investigations and pulls the new
      //     /api/hosts/{id}/beszel/services endpoint into context.
      const isDiag = /\b(chart|cut|missing|empty|silent|paused|stale|sampler|sample|points?|gap|history|drift|stop|stopped|ticks?|services?|units?|failed|running|systemd|daemons?)\b/.test(lc);
      if (isDiag) {
        const MAX_PREFETCH = 5;
        // Resolve hosts mentioned by name in the question — match
        // against id / label / beszel_name / pulse_name / etc.
        // Lowest-cost: substring match on the lowercased question.
        const candidates = (this.hosts || [])
          .filter(h => h && h.id)
          .filter(h => {
            const aliases = [h.id, h.label, h.beszel_name, h.pulse_name,
              h.webmin_name, h.snmp_name, h.host_hostname]
              .filter(Boolean).map(s => String(s).toLowerCase());
            return aliases.some(a => a && lc.indexOf(a) >= 0);
          })
          .slice(0, MAX_PREFETCH);
        if (candidates.length && typeof this.loadHostDebug === 'function') {
          await Promise.allSettled(
            candidates.map(h => this.loadHostDebug(h.id).catch(() => null))
          );
        }
        // Per-service Beszel detail pre-fetch — runs in parallel
        // with the debug fetch above so the AI sees `services_detail`
        // (per-unit state + last_change_ts) for hosts named in
        // service-diagnosis questions. Skips hosts without
        // beszel_id since the endpoint only carries data for
        // Beszel-tracked hosts.
        if (candidates.length && typeof this.loadHostBeszelServices === 'function') {
          await Promise.allSettled(
            candidates
              .filter(h => h.beszel_id)
              .map(h => this.loadHostBeszelServices(h.id).catch(() => null))
          );
        }
      }
    } catch (_) { /* never block the question on prefetch */
    }
    // Pre-fetch backup state when the operator's question mentions
    // backup-style words AND the lists aren't loaded yet (empty
    // because the operator never opened the Backup / Config Backup
    // tabs in this session). Without this the AI replies "I don't
    // have access to the history of backup jobs" — operator-flagged.
    // Fire-and-forget; best-effort; never blocks the question.
    try {
      const lcq = q.toLowerCase();
      const wantsBackupCtx = /\b(backup|backups|snapshot|snapshots|restore|restored|config\s*backup)\b/.test(lcq);
      if (wantsBackupCtx) {
        const tasks = [];
        if (!Array.isArray(this.backups) || this.backups.length === 0) {
          if (typeof this.loadBackups === 'function') {
            tasks.push(this.loadBackups().catch(() => null));
          }
        }
        if (!Array.isArray(this.configBackupSaved) || this.configBackupSaved.length === 0) {
          if (typeof this.loadConfigBackupSaved === 'function') {
            tasks.push(this.loadConfigBackupSaved().catch(() => null));
          }
        }
        if (tasks.length) {
          await Promise.allSettled(tasks);
        }
      }
    } catch (_) { /* never block the question on prefetch */
    }
    // Pre-fetch prayer times + Hijri date so the modal palette can answer
    // prayer / Hijri questions even when no prayer widget is mounted.
    // Self-gating (disabled / fresh-cache → no-op).
    try {
      if (typeof this._ensurePrayerTimes === 'function') {
        await this._ensurePrayerTimes();
      }
    } catch (_) { /* never block the question on prefetch */
    }
    const ctx = this._buildAiPaletteContext();
    try {
      const r = await fetch('/api/ai/palette', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({query: q, context: ctx, conversation: priorTurns}),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) {
        this.aiConversation.push({
          role: 'assistant',
          text: '',
          error: (j && j.detail) || (this.t('toasts.failed') || 'Failed'),
          ts: Date.now(),
        });
        return;
      }
      const answer = (j.text || '').trim() || (this.t('command_palette.ai.empty_response') || '(empty response)');
      const actionId = (j.action || '').toString().trim();
      let actionDesc = actionId ? this._actionDescriptorById(actionId) : null;
      // The generic `run_app_skill` descriptor is destructive:false, but a
      // per-app skill it dispatches (e.g. radarr_remove_movie) can be
      // destructive. Resolve the REAL flag from the skill registry + clone
      // the descriptor so the inline-confirm chip (approval mode) / immediate
      // fire (autonomous mode) gate engages — declaring the flag in SKILLS is
      // necessary but the AI-emitted path must consult it.
      if (actionDesc && actionId === 'run_app_skill'
        && this._appSkillIsDestructive(j.action_data)) {
        actionDesc = Object.assign({}, actionDesc, {destructive: true});
      }
      // `j.hosts` from the HOSTS protocol — when the AI's answer
      // references specific hosts by id, the SPA renders inline
      // disk-projection charts inside the assistant bubble.
      // Validated against the curated host id set server-side, so
      // we trust the array as-is here.
      const hostIds = Array.isArray(j.hosts) ? j.hosts.slice(0, 8) : [];
      // chart_kind dispatch — picks which chart the SPA renders
      // alongside the assistant's answer. Resolution chain:
      //   1. AI's explicit `CHART:` directive → server-side parser
      //      cleans + validates → arrives in `j.chart_kind`.
      //   2. SPA-side heuristic from the user's question text —
      //      Gemini repeatedly omits the CHART: line for memory /
      //      CPU questions even with the prompt's MANDATORY hard
      //      rule, so we infer the kind from the user's words
      //      when the AI also returned `hosts` but no chart_kind.
      //      memory / ram / mem → memory_history; cpu / load /
      //      processor → cpu_history; disk / storage / space →
      //      disk_projection. No match → empty (default disk).
      //   3. Empty falls through to disk_projection in the
      //      populator (legacy default).
      let chartKind = (typeof j.chart_kind === 'string' && j.chart_kind)
        ? j.chart_kind : '';
      if (!chartKind && hostIds.length > 0) {
        const _qLower = String(q || '').toLowerCase();
        if (/\b(memory|ram|mem)\b/.test(_qLower)) {
          chartKind = 'memory_history';
        } else if (/\b(cpu|load|processor)\b/.test(_qLower)) {
          chartKind = 'cpu_history';
        } else if (/\b(disk|storage|space|capacity)\b/.test(_qLower)) {
          chartKind = 'disk_projection';
        }
      }
      // Surface AI-emitted memories. `memories_saved` is the list
      // the backend already persisted via /api/ai/palette; the SPA
      // toasts each one so the operator sees the self-improvement
      // happen in real-time. `memories_to_forget` is operator-
      // confirmed BEFORE the actual delete propagates.
      const memoriesSaved = Array.isArray(j.memories_saved) ? j.memories_saved : [];
      const memoriesToForget = Array.isArray(j.memories_to_forget) ? j.memories_to_forget : [];
      if (memoriesSaved.length > 0) {
        for (const m of memoriesSaved) {
          const head = m.length > 80 ? m.slice(0, 80) + '…' : m;
          if (typeof this.showToast === 'function') {
            this.showToast(
              (this.t('ai_memory.toast_saved') || 'Memory saved') + ': ' + head,
              'success'
            );
          }
        }
      }
      // memoriesToForget is handled below — attached to the assistant
      // turn as a `pending_action` so the inline-confirm chip in the
      // chat bubble renders the operator confirm. The pre-fix path
      // here fired a SweetAlert popup INSIDE the AI sidebar, which
      // violated the "NO popups in the sidebar surface" canonical
      // contract. confirmInlineAction recognises
      // `action.kind === 'memory_forget'` and walks `action.forget_texts`
      // calling /api/ai/memory/forget for each on operator approval.
      const turn = {
        role: 'assistant',
        text: answer,
        provider: j.provider || '',
        model: j.model || '',
        response_time_ms: j.response_time_ms || 0,
        tokens: (j.tokens && (j.tokens.prompt + j.tokens.completion)) || 0,
        job_id: (j.job_id !== undefined && j.job_id !== null) ? j.job_id : null,
        action_id: actionId || null,
        action_label: actionDesc ? (actionDesc.label || actionId) : null,
        action_ran: false,
        // Pre-declared so the "Working on it…" spinner (x-show on
        // turn.skill_running) tracks the key from creation across every
        // skill-dispatch path (_aiSidebarRunSkill + _runCommandPaletteAction).
        skill_running: false,
        host_ids: hostIds,
        // Per-action parameters carried alongside the action_id so
        // confirmInlineAction (the inline-chip "Yes" handler) can
        // re-fire the action with the same params after the
        // operator confirms. retag_image consumes tag + actionItem;
        // schedule_* consumes action_data (structured JSON payload).
        action_tag: (j.action_tag || '').toString(),
        action_item: (j.action_item || '').toString(),
        action_data: (j.action_data && typeof j.action_data === 'object') ? j.action_data : null,
        // ACTION_HOSTS target list (e.g. reboot_host) parsed server-side into
        // j.action_hosts. Plumbed onto the turn so the sidebar dispatch AND
        // the inline-confirm re-fire pass the AI-named host into
        // rebootHostAction — without this the web reboot handler gets no
        // target and aborts with "No host selected" (the Telegram path runs
        // reboot server-side, which is why it works there but not on web).
        action_hosts: Array.isArray(j.action_hosts) ? j.action_hosts.slice(0, 8) : [],
        // Persisted on the turn so re-hydration after a reload
        // (loadAiConversation walks each saved turn and re-fires
        // the populator) picks the right chart kind without a
        // round-trip to the AI. Defaults to "disk_projection" via
        // the populator when absent — preserves legacy turns.
        chart_kind: chartKind,
        // Tool-dispatch confirm — set when the backend short-
        // circuits a confirm-required tool call (ssh_diag /
        // docker_container_du) with a pending_tool_confirms
        // envelope. The inline chip in the assistant bubble's
        // `pending_tool_confirms` branch reads this; clicking Yes
        // re-POSTs to /api/ai/palette with tool_confirm_granted:
        // true + the same original query, so the backend re-parses,
        // dispatches the tools without short-circuiting, and
        // returns the second-round AI reply composed from the
        // actual tool output. We persist the original query on the
        // turn so the re-POST has the exact text the user typed,
        // not whatever's in the input box at click time.
        pending_tool_confirms: Array.isArray(j.pending_tool_confirms) ? j.pending_tool_confirms : null,
        pending_query: (j.pending_tool_confirms && j.pending_tool_confirms.length) ? q : null,
        ts: Date.now(),
      };
      // memoriesToForget → inline-confirm-chip pattern (no SweetAlert
      // popups in sidebar surface). `confirmInlineAction` recognises
      // `action.kind === 'memory_forget'` and walks `forget_texts`
      // calling /api/ai/memory/forget for each entry on Yes; Cancel
      // discards the queue. Operator-flagged anti-pattern: pre-fix
      // fired `confirmDialog` inside `sendAiSidebarMessage`'s reply
      // path, breaking the canonical "no popups in sidebar" contract.
      if (memoriesToForget.length > 0) {
        const previewHead = memoriesToForget[0].length > 80
          ? memoriesToForget[0].slice(0, 80) + '…'
          : memoriesToForget[0];
        turn.pending_confirm = true;
        turn.pending_action = {
          kind: 'memory_forget',
          label: memoriesToForget.length === 1
            ? (this.t('ai_memory.forget_confirm_title') || 'Forget memory?')
            : (this.t('ai_memory.forget_confirm_title_n', {n: memoriesToForget.length})
              || ('Forget ' + memoriesToForget.length + ' memories?')),
          confirm_text: (this.t('ai_memory.forget_confirm_body')
              || 'The AI flagged this memory as wrong. Delete it?')
            + '\n\n"' + previewHead + '"',
          forget_texts: memoriesToForget,
        };
      }
      // Precompute the rendered markdown ONCE on turn-commit so the
      // x-html binding reads the stamped `turn._html` field instead of
      // re-parsing the answer (a multi-pass markdown build + full-text
      // memo hash) on EVERY reactive flush while the sidebar is open —
      // the report's "biggest single frontend win". The binding falls
      // back to the memoized _renderAiAnswerMd for any turn without
      // _html (error turns render via x-text + are skipped here).
      if (turn.text) {
        turn._html = this._renderAiAnswerMd(turn.text);
      }
      this.aiConversation.push(turn);
      // Fire chart population AFTER the bubble renders. Each shell
      // is scoped by `data-turn-ts` so multi-turn chats don't fight
      // for the same DOM slot.
      if (hostIds.length > 0) {
        this.$nextTick(() => {
          for (const hid of hostIds) {
            this._populateAiSidebarHostChart(hid, turn.ts, chartKind);
          }
        });
      }
      // Autonomous-mode auto-dispatch of confirm-required tools
      // (ssh_diag / docker_container_du). Operator-flagged: in
      // autonomous mode the AI sidebar is supposed to act without
      // intervention — the tool-dispatch chip rendering a Yes/Cancel
      // prompt defeats the contract. When the operator has opted
      // into autonomous mode AND the backend returned a
      // pending_tool_confirms envelope, fire confirmInlineToolDispatch
      // programmatically so the tools run + the second-round AI
      // reply lands without any chip ever surfacing. Approval mode
      // (the default) keeps the chip — the SSH-touching contract
      // still requires an operator click there.
      if (turn.pending_tool_confirms && turn.pending_tool_confirms.length
        && this.aiSidebarMode === 'autonomous') {
        const turnIdx = this.aiConversation.length - 1;
        this.$nextTick(() => {
          this.confirmInlineToolDispatch(turnIdx);
        });
      }
      // Auto-run the proposed action. Non-destructive actions fire
      // immediately. Destructive actions (sign_out, etc.) route
      // through `_runCommandPaletteAction` with `surface: 'sidebar'`
      // which converts the SweetAlert popup into an inline
      // confirmation chip rendered on the SAME assistant turn — no
      // popup, no experience disruption. Operator clicks Yes /
      // Cancel right inside the chat. Actions with `defer_confirm_to_run`
      // (cleanup_stopped, update_all_updatable) keep their inner
      // data popup since it lists every container by name — that's
      // legitimate confirmation data, not a disruption.
      if (actionDesc) {
        // Per-app SKILL runs (non-destructive) are dispatched inline so the
        // skill's RESULT (e.g. a Seerr movie suggestion) lands in the chat
        // bubble with any follow-up button — instead of vanishing into a
        // toast. The modal command palette keeps the generic toast path; this
        // capture is sidebar-only. Destructive skills fall through to the
        // inline-confirm-chip path below.
        if (actionId === 'run_app_skill' && turn.action_data && !actionDesc.destructive) {
          turn.action_ran = true;
          const _skillTurnIdx = this.aiConversation.length - 1;
          this.$nextTick(() => {
            this._aiSidebarRunSkill(_skillTurnIdx);
          });
        } else {
          // ALL destructive actions in the sidebar route through the
          // inline-confirm chip — including those marked
          // `defer_confirm_to_run` (cleanup_stopped /
          // update_all_updatable). Operator-flagged: AI said
          // "Opening the confirmation to remove all stopped, failed,
          // and orphaned containers" but no chip appeared because the
          // pre-mark was setting `action_ran: true` for defer-confirm
          // actions (their inner SweetAlert was supposed to show but
          // is now skipped via skipConfirm in confirmInlineAction).
          // Result: turn rendered with the green "Ran:" chip while
          // pending_confirm was true → confirm chip rendered above
          // it but the visual hierarchy made it easy to miss.
          if (actionDesc.destructive) {
            turn.action_ran = false;
          } else {
            turn.action_ran = true;
          }
          this._runCommandPaletteAction(actionDesc, {
            surface: 'sidebar',
            tag: turn.action_tag,
            actionItem: turn.action_item,
            data: turn.action_data,
            actionHosts: turn.action_hosts,
          });
        }
      }
    } catch (e) {
      this.aiConversation.push({
        role: 'assistant',
        text: '',
        error: (e && e.message) ? e.message : 'AI request failed',
        ts: Date.now(),
      });
    } finally {
      this.aiSidebarBusy = false;
      this._scrollAiSidebarToBottom();
      // Fire-and-forget persist on cleanup — `void` makes the
      // discard-the-promise intent explicit. Awaiting here would
      // block the UI's busy-flag teardown on a round-trip we don't
      // care about; failures are tolerated (every send re-persists).
      void this.persistAiConversation();
    }
  },
  _scrollAiSidebarToBottom(opts) {
    // Auto-scroll honours the user's manual scroll position when the
    // operator has paged up to read prior turns. The "force" flag is
    // for explicit user actions (open drawer, click jump-to-latest
    // pill, click Send) that should override the read-mode and snap
    // to the freshest turn.
    const force = !!(opts && opts.force);
    this.$nextTick(() => {
      const el = document.getElementById('og-ai-sidebar-log');
      if (!el) {
        return;
      }
      if (!force) {
        // Only auto-scroll when the operator is near the bottom
        // already — typical chat-app convention. 60px gives a
        // small "I'm reading the last few" buffer; anything past
        // that and the operator is intentionally scrolled up.
        const distFromBottom = el.scrollHeight - (el.scrollTop + el.clientHeight);
        if (distFromBottom > 60) {
          // Operator is reading prior turns — don't yank the view.
          // New content has landed below the fold; bump the unseen
          // counter so the floating "↓ N new" pill becomes visible.
          this.aiSidebarUnseenCount = (this.aiSidebarUnseenCount || 0) + 1;
          return;
        }
      }
      el.scrollTop = el.scrollHeight;
      // Reaching the bottom clears the unseen-count badge.
      if (this.aiSidebarUnseenCount > 0) {
        this.aiSidebarUnseenCount = 0;
      }
    });
  },
  // Bound from the log's @scroll handler — when the operator scrolls
  // back to the bottom on their own, clear the unseen counter so the
  // jump-to-latest pill disappears without needing a click.
  _onAiSidebarLogScroll(ev) {
    try {
      const el = ev && ev.target;
      if (!el) {
        return;
      }
      const distFromBottom = el.scrollHeight - (el.scrollTop + el.clientHeight);
      if (distFromBottom <= 60 && this.aiSidebarUnseenCount > 0) {
        this.aiSidebarUnseenCount = 0;
      }
    } catch (_) { /* ignore */
    }
  },
  // In-conversation scrollback search. Walks aiConversation[] for
  // case-insensitive substring match in turn.text. Match-index
  // cursor (`aiConversationSearchMatchIdx`) cycles via Enter / ↑↓
  // buttons. Recomputed on every keystroke via the input's `@input`
  // binding; cleared on Escape. No-op for short queries (< 2 chars)
  // to keep typing snappy on huge conversations.
  _recomputeAiConversationMatches() {
    const q = (this.aiConversationSearch || '').trim().toLowerCase();
    if (q.length < 2) {
      this.aiConversationSearchMatches = [];
      this.aiConversationSearchMatchIdx = 0;
      return;
    }
    const matches = [];
    for (let i = 0; i < this.aiConversation.length; i++) {
      const t = this.aiConversation[i];
      const text = String((t && t.text) || '').toLowerCase();
      if (text.includes(q)) {
        matches.push(i);
      }
    }
    this.aiConversationSearchMatches = matches;
    // Reset cursor to the LAST match (closest to current scroll
    // position — operators search "what did I just say" more often
    // than "what did I say first") and scroll to it.
    this.aiConversationSearchMatchIdx = Math.max(0, matches.length - 1);
    if (matches.length) {
      this._scrollToAiTurn(matches[this.aiConversationSearchMatchIdx]);
    }
  },
  // Cycle through matches in either direction (wraps at edges).
  // direction = +1 next, -1 previous.
  cycleAiConversationMatch(direction) {
    const total = this.aiConversationSearchMatches.length;
    if (!total) {
      return;
    }
    const step = (direction || 1) > 0 ? 1 : -1;
    let idx = (this.aiConversationSearchMatchIdx + step) % total;
    if (idx < 0) {
      idx += total;
    }
    this.aiConversationSearchMatchIdx = idx;
    this._scrollToAiTurn(this.aiConversationSearchMatches[idx]);
  },
  // Scroll the AI sidebar log so the targeted turn is centred.
  // Used by the search affordance to jump between matches.
  _scrollToAiTurn(turnIdx) {
    try {
      const log = document.getElementById('og-ai-sidebar-log');
      if (!log) {
        return;
      }
      const target = log.querySelector('[data-ai-turn-idx="' + turnIdx + '"]');
      if (target && target.scrollIntoView) {
        target.scrollIntoView({behavior: 'smooth', block: 'center'});
      }
    } catch (_) { /* ignore */
    }
  },
  // Click handler for the floating "↓ N new" pill — snaps to bottom
  // (force=true bypasses the near-bottom gate) and clears the counter.
  jumpToLatestAiTurn() {
    this.aiSidebarUnseenCount = 0;
    this._scrollAiSidebarToBottom({force: true});
  },
  async submitAiFeedback(turnIdx, rating) {
    const turn = this.aiConversation[turnIdx];
    if (!turn || turn.role !== 'assistant') {
      return;
    }
    if (turn.feedback === rating) {
      return;
    }  // already set
    if (this.aiSidebarFeedbackBusy[turnIdx]) {
      return;
    }
    // Optimistic UI — flip immediately, revert on failure.
    const prev = turn.feedback || '';
    turn.feedback = rating;
    this.aiSidebarFeedbackBusy[turnIdx] = true;
    try {
      const r = await fetch('/api/ai/feedback', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({job_id: turn.job_id || null, rating}),
      });
      if (!r.ok) {
        turn.feedback = prev;
        if (typeof this.showToast === 'function') {
          this.showToast(this.t('toasts.failed') || 'Failed', 'error');
        }
      }
    } catch (e) {
      turn.feedback = prev;
      if (typeof this.showToast === 'function') {
        this.showToast((e && e.message) || 'Failed', 'error');
      }
    } finally {
      this.aiSidebarFeedbackBusy[turnIdx] = false;
      // Fire-and-forget persist after recording the 👍 / 👎 — `void`
      // makes the discard-the-promise intent explicit to the IDE.
      void this.persistAiConversation();
    }
  },
  // Scroll the slash-picker's active row into view after Up/Down arrow
  // navigation. The picker caps at 280 px-max-height with up to 12
  // results, so the focused row scrolls out of sight without this.
  // Uses ``block:'nearest'`` so unnecessary scroll-jumps are avoided
  // when the row is already visible. ``$nextTick`` guards against
  // the index changing before Alpine has re-rendered the active class.
  _scrollAiSidebarSlashRowIntoView() {
    this.$nextTick(() => {
      const idx = this.aiSidebarSlashIdx;
      const row = document.querySelector('[data-slash-row-idx="' + idx + '"]');
      if (row && typeof row.scrollIntoView === 'function') {
        try {
          row.scrollIntoView({block: 'nearest', behavior: 'smooth'});
        } catch (_) { /* older browsers — silently skip */
        }
      }
    });
  },

  aiSidebarHandleKeydown(e) {
    // When the slash-command picker is open, route arrow / Enter
    // through it. Esc clears the slash mode (back to chat) without
    // closing the drawer; second Esc closes.
    if (this.aiSidebarSlashOpen()) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        const max = this.aiSidebarSlashResults().length - 1;
        if (max < 0) {
          return;
        }
        this.aiSidebarSlashIdx = Math.min(max, (this.aiSidebarSlashIdx || 0) + 1);
        this._scrollAiSidebarSlashRowIntoView();
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        this.aiSidebarSlashIdx = Math.max(0, (this.aiSidebarSlashIdx || 0) - 1);
        this._scrollAiSidebarSlashRowIntoView();
        return;
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        // Same debounce-flush dance as the default Enter path above —
        // slash-picker results derive from the live query, so a
        // stale `aiSidebarQuery` would point at the wrong action.
        if (e.target && typeof e.target.value === 'string') {
          this.aiSidebarQuery = e.target.value;
        }
        const list = this.aiSidebarSlashResults();
        const sel = list[this.aiSidebarSlashIdx || 0];
        if (sel) {
          this.runAiSidebarSlashAction(sel);
        }
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        this._setAiSidebarQuery('');
        return;
      }
    }
    // Default: Enter sends; Shift+Enter inserts a newline; Esc closes;
    // ↑ recalls the last user-turn text into the input — universal
    // shell-terminal convention. Only triggers when the input is
    // EMPTY (so editing existing text with arrow keys still works)
    // AND the slash picker isn't open (slash-mode handles ↑ as nav).
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      // FLUSH the debounced x-model BEFORE reading aiSidebarQuery in
      // sendAiSidebarMessage. The textarea's `x-model.debounce.150ms`
      // delays state propagation by 150 ms to keep typing smooth;
      // pressing Enter within that window would otherwise read a
      // STALE query string. Read the live DOM value and force-write
      // it onto the reactive state so the send path sees what the
      // operator actually typed.
      if (e.target && typeof e.target.value === 'string') {
        this.aiSidebarQuery = e.target.value;
      }
      this.sendAiSidebarMessage();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      this.closeAiSidebar();
    } else if (e.key === 'ArrowUp' && !this.aiSidebarQuery.trim()) {
      // Walk backwards through aiConversation looking for the most
      // recent user-role turn; copy its text into the input. Cheap
      // — bounded by the 50-turn cap.
      const turns = this.aiConversation || [];
      for (let i = turns.length - 1; i >= 0; i--) {
        if (turns[i] && turns[i].role === 'user' && turns[i].text) {
          e.preventDefault();
          this._setAiSidebarQuery(turns[i].text);
          // Move caret to end after the next tick so the operator
          // can immediately edit / submit without arrow-keying.
          this.$nextTick(() => {
            const el = document.getElementById('og-ai-sidebar-input');
            if (el && typeof el.setSelectionRange === 'function') {
              const len = this.aiSidebarQuery.length;
              el.setSelectionRange(len, len);
            }
          });
          return;
        }
      }
    }
  },
  // Slash-command quick-action picker. Typing `/` at the start of
  // the input switches the dropdown into action-filter mode so the
  // operator can fire any palette action directly without going
  // through the AI. Same `_commandActions()` catalog as Cmd-K, same
  // `_runCommandPaletteAction()` dispatcher (destructive actions
  // still confirm via SweetAlert).
  // Write `value` to BOTH the reactive `aiSidebarQuery` state AND
  // the textarea DOM element. Needed because the textarea is now
  // fully uncontrolled — clearing/recalling state without also
  // updating the DOM would leave stale text in the input. Used by
  // clear / send-success / arrow-up-recall / incident-chip-recall
  // paths.
  _setAiSidebarQuery(value) {
    const v = value || '';
    this.aiSidebarQuery = v;
    try {
      const el = document.getElementById('og-ai-sidebar-input');
      if (el && el.value !== v) {
        el.value = v;
      }
    } catch (_) {
    }
  },
  aiSidebarSlashOpen() {
    const q = (this.aiSidebarQuery || '').trimStart();
    return q.startsWith('/');
  },
  aiSidebarSlashResults() {
    const q = (this.aiSidebarQuery || '').trimStart();
    if (!q.startsWith('/')) {
      return [];
    }
    const needle = q.slice(1).trim();
    // Reuse the modal palette's full result set — actions, hosts,
    // items, admin tabs, top-level views, hotkeys, AI fallback row.
    // Empty needle (just `/`) returns the full action catalog at
    // score 1, mirroring the modal palette's empty-query behaviour.
    // The modal's bulk-NL and other commandPaletteQuery-specific
    // branches are skipped via the override path.
    const results = (typeof this.commandPaletteResults === 'function')
      ? this.commandPaletteResults(needle)
      : [];
    // Hide the AI-bulk row from the slash picker — the bulk-palette
    // surface is keyboard-modal-only; surfacing it inline would just
    // confuse the operator who's already in a chat.
    const filtered = results.filter(r => r.kind !== 'ai-bulk');
    // "Recents" group — when the needle is empty AND the operator
    // has invoked at least one action recently, hoist those rows
    // (in FIFO order — most-recent first) to the TOP of the picker.
    // Only ACTION-kind rows are tracked so navigation results don't
    // pollute the recents list. Each recent row carries the same
    // shape as a normal action result PLUS `_recent: true` for the
    // template to render the "Recents" group label.
    if (!needle && this.aiRecentSlashActions.length) {
      const actionsCatalog = (typeof this._commandActions === 'function')
        ? this._commandActions() : [];
      const byId = new Map(actionsCatalog.map(a => [a.id, a]));
      const recentRows = [];
      for (const id of this.aiRecentSlashActions) {
        const a = byId.get(id);
        if (!a) {
          continue;
        }  // action no longer exists / disabled
        recentRows.push({
          kind: 'action',
          label: a.label,
          sub: a.sub || '',
          payload: a,
          group: 'recents',
          destructive: !!a.destructive,
          _recent: true,
        });
      }
      if (recentRows.length) {
        // De-dupe — when a recent row also appears in the catalog
        // result set, drop the catalog row so the picker doesn't
        // show the same action twice.
        const recentIds = new Set(recentRows.map(r => r.payload && r.payload.id));
        const rest = filtered.filter(r =>
          r.kind !== 'action' || !recentIds.has(r.payload && r.payload.id)
        );
        return [...recentRows, ...rest].slice(0, 12);
      }
    }
    return filtered.slice(0, 12);
  },
  // Switch the AI sidebar's action-confirmation mode and persist
  // the choice to `ui_prefs.ai_sidebar_mode`. Approval =
  // destructive actions render an inline-confirm chip in the
  // chat; Autonomous = AI fires every action immediately
  // (including destructive). Same fire-and-forget shape as
  // `persistThemePref` / `persistAiConversation`.
  async setAiSidebarMode(mode) {
    const next = (mode === 'autonomous') ? 'autonomous' : 'approval';
    if (next === this.aiSidebarMode) {
      return;
    }
    this.aiSidebarMode = next;
    if (!this.me || !this.me.id || this.me.id < 0) {
      return;
    }
    try {
      await fetch('/api/me/ui-prefs', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prefs: {ai_sidebar_mode: next}}),
      });
    } catch (e) {
      if (window.console && console.warn) {
        console.warn('[ai_sidebar_mode] persist failed:', e);
      }
    }
  },
  // Toggle the AI sidebar's pin-to-dock mode. When pinned, the
  // sidebar becomes a left-edge split (main view shrinks via
  // `body.ai-pinned` CSS class) instead of an overlay drawer.
  // Persisted to ui_prefs.ai_sidebar_pinned. Mobile (max-width:
  // 480px) ignores pin via the CSS @media override (sidebar is
  // 100vw — pinning would hide all content).
  //
  // Side effect: pinning forces the sidebar open; un-pinning
  // does NOT close it (operator can still close via X / Esc).
  async togglePinAiSidebar() {
    const next = !this.aiSidebarPinned;
    this.aiSidebarPinned = next;
    if (next) {
      this.aiSidebarOpen = true;
    }  // implicit open when pinning
    if (!this.me || !this.me.id || this.me.id < 0) {
      return;
    }
    try {
      await fetch('/api/me/ui-prefs', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prefs: {ai_sidebar_pinned: next}}),
      });
    } catch (e) {
      if (window.console && console.warn) {
        console.warn('[ai_sidebar_pinned] persist failed:', e);
      }
    }
  },
  // Toggle the AI sidebar launcher's visibility. Cmd-K still opens
  // the sidebar even when the launcher is hidden; this preference
  // is for keyboard-only operators who don't want the floating
  // button in their Tab cycle on every page load. Persisted to
  // ui_prefs.ai_sidebar_launcher_hidden so the choice follows the
  // operator across browsers / machines.
  async setAiSidebarLauncherHidden(hidden) {
    const next = !!hidden;
    if (next === this.aiSidebarLauncherHidden) {
      return;
    }
    this.aiSidebarLauncherHidden = next;
    // Keep the draft in sync so the dirty-tracker (compared via
    // `_headerPrefsSnapshot`) doesn't keep reporting dirty after
    // the live value updates. Also covers the case where the
    // helper is invoked from outside the form (e.g. a future
    // keyboard shortcut or admin-tool action).
    this.aiSidebarLauncherHiddenDraft = next;
    if (!this.me || !this.me.id || this.me.id < 0) {
      return;
    }
    try {
      await fetch('/api/me/ui-prefs', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prefs: {ai_sidebar_launcher_hidden: next}}),
      });
    } catch (e) {
      if (window.console && console.warn) {
        console.warn('[ai_sidebar_launcher_hidden] persist failed:', e);
      }
    }
  },
  runAiSidebarSlashAction(result) {
    // `result` is one row from `commandPaletteResults()` carrying
    // `{kind, label, sub, payload, group}`. Different kinds activate
    // differently — actions fire via the dispatcher, navigation
    // results close the drawer + jump to the surface, AI sends the
    // query into the existing chat. Synthetic conversation turn
    // logs WHAT the operator picked so the chat history is honest
    // ("Ran: Switch to dark theme" / "Opened host: web01.example").
    if (!result) {
      return;
    }
    this._setAiSidebarQuery('');
    this.aiSidebarSlashIdx = 0;
    const kind = result.kind;
    // Helper — close the drawer for navigation kinds so the operator
    // lands on the target surface instead of staring at the sidebar.
    const closeAndNavigate = (fn) => {
      this.closeAiSidebar();
      this.$nextTick(fn);
    };
    switch (kind) {
      case 'action':
        // Destructive actions get an inline confirmation chip
        // instead of a SweetAlert popup — see the inline-confirm
        // branch in `_runCommandPaletteAction`. Non-destructive
        // actions fire immediately.
        this._appendActionChatTurn(result.payload, /* slash= */ true);
        this._runCommandPaletteAction(result.payload, {surface: 'sidebar'});
        // Record into recents AFTER dispatch — only ACTION kinds,
        // not navigation. Cancelled destructive confirms still
        // count: the operator clearly intended to use the action.
        if (result.payload && result.payload.id) {
          this._recordSlashRecent(result.payload.id);
        }
        break;
      case 'host':
        this.aiConversation.push({
          role: 'assistant', text: '',
          action_label: this.t('ai_sidebar.opened_label', {label: result.label || ''}),
          action_ran: true, slash: true, ts: Date.now(),
        });
        this._scrollAiSidebarToBottom();
        this.persistAiConversation();
        closeAndNavigate(() => {
          if (typeof this.openHostDrawer === 'function') {
            this.openHostDrawer(result.payload);
          }
        });
        break;
      case 'item':
        this.aiConversation.push({
          role: 'assistant', text: '',
          action_label: this.t('ai_sidebar.opened_label', {label: result.label || ''}),
          action_ran: true, slash: true, ts: Date.now(),
        });
        this._scrollAiSidebarToBottom();
        this.persistAiConversation();
        closeAndNavigate(() => {
          if (typeof this.openItemDrawer === 'function') {
            this.openItemDrawer(result.payload);
          } else {
            this.drawerItem = result.payload;
          }
        });
        break;
      case 'admin':
        this.aiConversation.push({
          role: 'assistant', text: '',
          action_label: this.t('ai_sidebar.navigated_label', {label: result.label || ''}),
          action_ran: true, slash: true, ts: Date.now(),
        });
        this._scrollAiSidebarToBottom();
        this.persistAiConversation();
        closeAndNavigate(() => {
          this.view = 'admin';
          if (typeof this.setAdminTab === 'function') {
            this.setAdminTab(result.payload);
          } else {
            this.adminTab = result.payload;
          }
        });
        break;
      case 'view':
        this.aiConversation.push({
          role: 'assistant', text: '',
          action_label: this.t('ai_sidebar.navigated_label', {label: result.label || ''}),
          action_ran: true, slash: true, ts: Date.now(),
        });
        this._scrollAiSidebarToBottom();
        this.persistAiConversation();
        closeAndNavigate(() => {
          if (typeof this.setView === 'function') {
            this.setView(result.payload);
          } else {
            this.view = result.payload;
          }
        });
        break;
      case 'hotkey':
        // Selecting a hotkey row opens the cheat sheet — same as
        // the modal palette's behaviour.
        closeAndNavigate(() => {
          this.showHotkeys = true;
        });
        break;
      case 'ai':
        // Operator typed `/<question>` and selected the AI fallback
        // row. Route through the same chat path as a regular send.
        this._setAiSidebarQuery((result.payload && result.payload.query) || '');
        this.sendAiSidebarMessage();
        break;
      default:
        // Forward-compat — surface as a no-op assistant turn so the
        // operator sees something happened.
        break;
    }
  },
  aiTurnSubline(turn) {
    if (!turn) {
      return '';
    }
    const fmtNum = (n) => Number.isFinite(+n) ? (+n).toLocaleString() : String(n || 0);
    const parts = [];
    if (turn.provider) {
      parts.push(turn.provider);
    }
    if (turn.model) {
      parts.push(turn.model);
    }
    if (turn.response_time_ms) {
      parts.push(fmtNum(turn.response_time_ms) + 'ms');
    }
    if (turn.tokens) {
      parts.push(fmtNum(turn.tokens) + ' tokens');
    }
    return parts.join(' · ');
  },
  _buildAiPaletteContext() {
    // `fmtHost` is intentionally long-but-flat (~125 statements). It
    // maps EVERY operator-visible host_* field into the AI palette's
    // single-host context object — each `if (h.X) out.X = …` line is
    // one field. Splitting into sub-helpers (`fmtHostNetwork`,
    // `fmtHostDisk`, `fmtHostInventory`, …) would obscure the
    // canonical "what the AI sees per host" contract by scattering it
    // across 4-5 functions, when the whole function exists to answer
    // exactly one question: which fields cross the SPA→AI boundary?
    // Scoped suppression with documented reason is the correct call —
    // refactoring trades one readable manifest for a tour of helpers.
    // noinspection OverlyLongLambdaJS,OverlyLongAnonymousFunctionJS,FunctionWithMoreThanThreeNegationsJS
    const fmtHost = (h) => {
      const total = Number(h.disk_total || 0);
      const used = Number(h.disk_used || 0);
      const out = {
        id: h.id || h.host || '',
        label: h.label || '',
        status: h.status || '',
      };
      // Kernel-reported hostname (uname -n) — surfaced separately
      // from the curated `id` because the two often DIVERGE: the
      // curated id is typically a role / alias the operator typed
      // in Admin → Hosts (e.g. `adguard2`), while `host_hostname`
      // is what the machine actually calls itself (e.g.
      // `raspberry4tm02`). When the user runs `hostname` or `df -h`
      // on a node and pastes the output, the AI needs to be able
      // to match BACK to the curated host. Populated by node-
      // exporter, SNMP, and Webmin providers; empty when none of
      // those run for this host.
      if (h.host_hostname) {
        out.host_hostname = String(h.host_hostname);
      }
      // Per-provider name aliases the user typed in Admin → Hosts.
      // Surfaces alongside `host_hostname` so the AI can match
      // against ANY of the names the operator might have used in
      // a question or pasted shell output. Each is optional — only
      // included when actually set.
      if (h.beszel_name) {
        out.beszel_name = String(h.beszel_name);
      }
      if (h.pulse_name) {
        out.pulse_name = String(h.pulse_name);
      }
      if (h.webmin_name) {
        out.webmin_name = String(h.webmin_name);
      }
      if (h.snmp_name) {
        out.snmp_name = String(h.snmp_name);
      }
      // Hardware identity from DMI (system_vendor / product_name)
      // — operator-recognisable strings like "Raspberry Pi 4
      // Model B Rev 1.4" or "Dell PowerEdge R730xd". Useful when
      // the user pastes `dmidecode` / `cat /sys/firmware/...`
      // output and the AI needs to match by hardware.
      if (h.host_vendor) {
        out.vendor = String(h.host_vendor);
      }
      if (h.host_model) {
        out.model = String(h.host_model);
      }
      if (h.host_serial) {
        out.serial = String(h.host_serial);
      }
      if (h.host_firmware) {
        out.firmware = String(h.host_firmware);
      }
      // Per-host telemetry the user commonly asks the AI about DIRECTLY
      // ("what's the UPS battery %", "any load on X") — surfacing it
      // here (parity with the Telegram context-builder) lets the AI
      // answer from the data + single-match auto-resolve instead of
      // deflecting. Only stamped when the host actually reports the
      // field, so non-UPS hosts don't carry empty keys.
      if (h.host_ups_status) {
        out.ups_status = String(h.host_ups_status);
      }
      if (h.host_battery_percent != null) {
        out.battery_pct = h.host_battery_percent;
      }
      if (h.host_battery_status) {
        out.battery_status = String(h.host_battery_status);
      }
      if (h.host_battery_runtime_s != null) {
        out.battery_runtime_s = h.host_battery_runtime_s;
      }
      if (h.host_battery_temp_c != null) {
        out.battery_temp_c = h.host_battery_temp_c;
      }
      if (h.host_load_percent != null) {
        out.load_pct = h.host_load_percent;
      }
      if (h.package_updates_count != null) {
        out.package_updates = h.package_updates_count;
      }
      // Platform / kernel — short strings the AI can correlate
      // against `uname -a` output (`Linux raspberry4tm02 5.15...`).
      if (h.host_platform) {
        out.platform = String(h.host_platform);
      }
      if (h.host_kernel) {
        out.kernel = String(h.host_kernel);
      }
      if (h.host_arch) {
        out.arch = String(h.host_arch);
      }
      // Uniform 1-decimal precision across cpu_pct / mem_pct / disk_pct
      // so the AI never reports "memory at 67%" while a chart shows
      // 67.4% (mixed-precision context confused operators on charts
      // vs. text-summary parity). `fmtPercentLabel` would need
      // string output; here we want numbers, so round via `* 10 / 10`.
      const r1 = (n) => Math.round(Number(n) * 10) / 10;
      if (h.cpu_percent !== undefined && h.cpu_percent !== null) {
        out.cpu_pct = r1(h.cpu_percent);
      }
      const memPct = (h.mem_percent !== undefined && h.mem_percent !== null)
        ? Number(h.mem_percent) : (typeof this.memPercentOf === 'function' ? this.memPercentOf(h) : null);
      if (memPct !== null && Number.isFinite(memPct)) {
        out.mem_pct = r1(memPct);
      }
      // Absolute memory in GB. Pre-fix the AI saw `mem_pct` but no
      // total / used / free in GB, so questions like "how much
      // memory does opnsense have in GB" returned "I don't have
      // the total memory size in GB" even when SNMP / NE / Beszel
      // provided byte-precise readings. Same shape as the disk
      // fields below — bytes / 1024^3, rounded to one decimal so
      // 8.1 GB doesn't drift to 8 GB on small fleets where the
      // exactness matters. The API row exposes the bytes under
      // `mem_total` / `mem_used` (NOT `host_mem_total` —
      // `_shape_host_api_row` strips the `host_` prefix during the
      // shape pass for these specific fields), so read THOSE keys
      // here. SPA-side host objects never carry the `host_*`
      // variants for memory / disk totals.
      const memTotal = Number(h.mem_total || 0);
      const memUsed = Number(h.mem_used || 0);
      if (memTotal > 0) {
        out.mem_total_gb = r1(memTotal / (1024 ** 3));
        out.mem_used_gb = r1(memUsed / (1024 ** 3));
        out.mem_free_gb = r1((memTotal - memUsed) / (1024 ** 3));
      }
      if (total > 0) {
        out.disk_pct = r1((used / total) * 100);
        out.disk_free_gb = Math.round((total - used) / (1024 ** 3));
        out.disk_total_gb = Math.round(total / (1024 ** 3));
      }
      if (h.uptime) {
        out.uptime_s = Number(h.uptime);
      }
      if (h.sampling_paused) {
        out.paused = true;
      }
      if (Array.isArray(h.providers) && h.providers.length) {
        out.providers = h.providers.slice(0, 6);
      }
      // Probe-health diagnostic for the AI — answers "why is
      // this host's chart cut / showing collecting data?". Three
      // signals: (1) `_stale_age_s` — how old the snapshot fallback
      // markers are (>0 means at least one host_* field came from
      // snapshot, the live providers haven't refreshed it yet);
      // (2) `provider_pause` — list of providers in auto-pause
      // (those samplers won't tick again until manually resumed);
      // (3) `provider_failing` — providers that ARE configured but
      // their last probe didn't succeed (chip is red). Surfacing
      // these on every host record lets the AI cross-reference a
      // chart-empty question with the actual root cause without
      // a separate fetch.
      try {
        const stale = (h._stale_fields && h._stale_fields.length) ? h._stale_fields : null;
        const staleTs = +(h._stale_ts || 0);
        if (stale && staleTs) {
          out._stale_fields = stale.slice(0, 8).map(String);
          out._stale_age_s = Math.max(0, Math.round(Date.now() / 1000 - staleTs));
        }
        // Per-provider pause / last-ok summary (shape from
        // `_shape_host_api_row.provider_pause_state`).
        const pps = h.provider_pause_state;
        if (pps && typeof pps === 'object') {
          const paused = [];
          const failing = [];
          for (const [name, state] of Object.entries(pps)) {
            if (!state || typeof state !== 'object') {
              continue;
            }
            if (state.paused) {
              paused.push(name);
            } else {
              if (state.consecutive_failures && +state.consecutive_failures > 0) {
                failing.push(name + '(' + state.consecutive_failures + ')');
              }
            }
          }
          if (paused.length) {
            out.provider_paused = paused;
          }
          if (failing.length) {
            out.provider_failing = failing;
          }
        }
        // Per-host debug surface — pulled from the
        // /api/hosts/debug counters block when cached in
        // `hostsDebug[h.id]`. The pre-fetch loop above triggers
        // the load for hosts named in a chart-diagnosis question;
        // hosts whose debug was never loaded ship no debug
        // section. Three sub-blocks attached:
        //
        //   sampler_health — per-table samples_in_window summary
        //     (count / newest_age_s / median_gap_s). Diagnoses
        //     "why is X chart cut".
        //
        //   tunables — full live-resolved tunable map for
        //     probe-behaviour-affecting knobs. Lets the AI answer
        //     "what's the SNMP cool-down?" / "is auto-pause
        //     enabled for Pulse?" / "what's the sample interval"
        //     using ACTUAL current values, not training-data
        //     guesses.
        //
        //   failure / pause state — drives "is host paused?" /
        //     "how many failures has SNMP had?" answers.
        const dbg = (this.hostsDebug || {})[h.id];
        const counters = dbg && dbg.counters;
        if (counters && typeof counters === 'object') {
          const win = counters.samples_in_window;
          if (win && typeof win === 'object') {
            const sh = {hours: +(win.hours || 1)};
            for (const [k, blob] of Object.entries(win)) {
              if (!blob || typeof blob !== 'object' || k === 'hours' || k === 'since_ts') {
                continue;
              }
              if (!('count' in blob)) {
                continue;
              }
              sh[k] = {
                count: +blob.count || 0,
                newest_age_s: blob.newest_age_s == null ? null : +blob.newest_age_s,
                median_gap_s: blob.median_gap_s == null ? null : +blob.median_gap_s,
              };
            }
            out.sampler_health = sh;
          }
          if (counters.tunables && typeof counters.tunables === 'object') {
            // Pass the full map verbatim — small (~36 keys, ints)
            // and the AI may need to answer questions about any
            // of them. Capping keys would create surprise gaps
            // ("the AI doesn't know about X tunable") that defeat
            // the purpose.
            out.tunables = {...counters.tunables};
          }
          if (counters.failure_state && typeof counters.failure_state === 'object') {
            out.failure_state = counters.failure_state;
          }
          // Per-unit Beszel services detail from the cached
          // `hostsBeszelServices[h.id]` (populated by the AI sidebar
          // pre-fetch when the question contains service-related
          // keywords, OR by the drawer per-service pane). Cap at 32
          // entries to bound prompt size while still letting the AI
          // surface specifics on common cases. Failed units come
          // first per the endpoint's sort, so the cap preserves
          // signal — failed units always make the cut even on a
          // host with hundreds of healthy units.
          const svcCache = (this.hostsBeszelServices || {})[h.id];
          if (svcCache && Array.isArray(svcCache.services) && svcCache.services.length) {
            out.services_detail = svcCache.services.slice(0, 32).map(s => ({
              name: String(s.name || ''),
              state: (s.state == null ? null : +s.state),
              sub_state: (s.sub_state == null ? null : +s.sub_state),
              last_change_age_s: (s.last_change_ts
                ? Math.max(0, Math.round(Date.now() / 1000 - +s.last_change_ts))
                : null),
            }));
          }
          if (counters.provider_pause_state && typeof counters.provider_pause_state === 'object') {
            // Already partially captured via `provider_paused` /
            // `provider_failing` above (those use the row-level
            // `h.provider_pause_state`). Surfacing the full
            // counters version here gives the AI per-provider
            // detail like `last_ok_ts`, `first_failure_ts`,
            // `paused_at` so it can answer "when did SNMP last
            // succeed on opnsense?" precisely.
            out.provider_pause_full = counters.provider_pause_state;
          }
        }
      } catch (_) {
      }
      // Per-host services summary — total + failed counts +
      // names of the failed services. Pre-fix the AI saw zero
      // service-state context, so questions like "any failed
      // services?" returned "No services are reported as failed"
      // even when `host.services.failed_names` carried entries
      // like `forgejo-mcp` / `mcp-auth-proxy`. Beszel agent path
      // populates this via systemd_services collection; non-Beszel
      // hosts get `{total: 0, failed: 0, failed_names: []}` and
      // the field is omitted when total is 0 (no signal to ship).
      if (h.services && typeof h.services === 'object'
        && Number(h.services.total) > 0) {
        const svc = {
          total: Number(h.services.total) || 0,
          failed: Number(h.services.failed) || 0,
        };
        if (Array.isArray(h.services.failed_names)
          && h.services.failed_names.length) {
          // Cap at 16 names so a runaway-failing fleet doesn't
          // balloon the AI prompt; first 16 is enough for the
          // AI to surface specifics in the answer.
          svc.failed_names = h.services.failed_names
            .slice(0, 16).map(String);
        }
        out.services = svc;
      }
      // Asset-inventory aliases — surface vendor / model / serial
      // / display-name fields from the curated asset record so the
      // AI can resolve aliases like "qotom" (an asset display
      // name) to the right host even when the host's primary
      // `id` / `label` doesn't carry the substring. The grounding-
      // strict system prompt says "match against any field in the
      // supplied JSON record", so adding these fields lets natural-
      // language queries hit the host without forcing the user to
      // know the canonical id. Each field is a string (or skipped
      // when empty); no PII concerns since asset records are admin-
      // curated. `custom_number` is included for "host #5" style
      // references; `location` so "the rack 3 host" resolves.
      try {
        const asset = (typeof this.assetForHost === 'function')
          ? this.assetForHost(h) : null;
        if (asset && typeof asset === 'object') {
          const a = {};
          if (asset.name) {
            a.name = String(asset.name);
          }
          if (asset.type_short) {
            a.type = String(asset.type_short);
          }
          if (asset.vendor) {
            a.vendor = String(asset.vendor);
          }
          if (asset.model) {
            a.model = String(asset.model);
          }
          if (asset.serial) {
            a.serial = String(asset.serial);
          }
          if (asset.location) {
            a.location = String(asset.location);
          }
          if (asset.custom_number != null && asset.custom_number !== '') {
            a.custom_number = asset.custom_number;
          }
          // Only attach when at least one field is populated —
          // empty asset records aren't useful and just bloat the
          // prompt.
          if (Object.keys(a).length) {
            out.asset = a;
          }
        }
      } catch (_) { /* asset lookup is best-effort context */
      }
      // Stale-data hints — when the host's `_stale_fields` carries
      // any field, the merged shape was filled from a snapshot
      // because the live provider stopped reporting. The AI should
      // qualify its answer ("last known status — host X is paused")
      // rather than confidently reporting cached state as current.
      // `stale_age_s` derives from `_stale_ts` (epoch-seconds when
      // the snapshot was persisted).
      const staleFields = Array.isArray(h._stale_fields) ? h._stale_fields : [];
      if (staleFields.length) {
        out.stale = true;
        const staleTs = Number(h._stale_ts) || 0;
        if (staleTs > 0) {
          out.stale_age_s = Math.max(0, Math.floor(Date.now() / 1000) - staleTs);
        }
        // Cap the field list at 8 so a heavily-stale host doesn't
        // blow the prompt — first 8 is enough signal for the AI to
        // know which axes (cpu / mem / disk / uptime / etc.) are
        // cached.
        out.stale_fields = staleFields.slice(0, 8);
      }
      return out;
    };
    const fmtItem = (i) => {
      const out = {name: i.name || ''};
      // Parent stack name — lets the AI match operator queries like
      // "the Seerr stack" / "homarr stack" against items even when
      // the container's bare name doesn't carry the stack prefix.
      // Pre-fix the field was dropped during context shaping so
      // stack-vs-container disambiguation relied solely on `type`.
      if (i.stack) {
        out.stack = i.stack;
      }
      if (i.status) {
        out.status = i.status;
      }
      if (i.health) {
        out.health = i.health;
      }
      if (i.type) {
        out.type = i.type;
      }
      if (i.replicas !== undefined) {
        out.replicas = i.replicas;
      }
      if (i.desired !== undefined) {
        out.desired = i.desired;
      }
      // Canonical "needs update" signal is `status === 'update'` —
      // gather.py sets that from the remote-digest comparison. There
      // is no separate `update_available` field, but we re-emit one
      // on the AI context so the prompt's "every item with
      // update_available=true" copy stays accurate.
      if ((i.status || '') === 'update') {
        out.update_available = true;
      }
      return out;
    };
    const allHosts = Array.isArray(this.hosts) ? this.hosts : [];
    const hostsCtx = allHosts.slice(0, 30).map(fmtHost).filter(h => h.id);
    // Authoritative counts — the AI must answer "how many hosts" from
    // these, NOT from `hosts.length` (which it sees as the SAMPLE
    // cap of 30). Operator-flagged: with 183 configured hosts the
    // AI replied "30 hosts" because that's all it could see in the
    // sample block. Pass total + enabled separately so the prompt-
    // builder can teach the AI to cite these for count questions.
    const hostsTotal = allHosts.length;
    const hostsEnabled = allHosts.filter(h => h && h.enabled !== false).length;
    // Items cap raised from 30 → 60. On a home-lab fleet with 40+
    // containers and stacks the bare 30-cap silently dropped half
    // the items off the end of the alphabetical-ish slice, so the
    // AI couldn't answer "why did Seerr fail" — overseerr / jellyseerr
    // landed past position 30. 60 entries comfortably fits typical
    // fleet sizes; token cost is bounded by `fmtItem`'s small shape.
    const itemsCtx = (this.items || []).slice(0, 60).map(fmtItem).filter(i => i.name);
    // Topbar weather widget — when the operator has it enabled and
    // the proxy fetched a payload, include the compact summary so
    // the AI can answer "what's the weather like?" without refusing
    // ("we have weather service in the app" — operator-flagged).
    // Field names match `/api/weather`'s actual response shape:
    // `temp_c`, `humidity`, `wind_kmh`, `code`, `condition`,
    // `icon`, `label`, `fetched_at`. Skip cleanly when disabled /
    // not yet loaded so the prompt doesn't carry stale or empty
    // payloads.
    let weatherCtx = null;
    const w = this.weather;
    if (w && w.configured !== false
      && (w.temp_c !== undefined || w.condition !== undefined || w.label)) {
      // Honour the operator's °C / °F preference: payload temps are
      // converted to the user's unit + the unit string ('°C' / '°F')
      // is forwarded explicitly so the AI replies in the matching
      // unit. Backend `/api/weather` always returns Celsius; the
      // SPA converts here at the context-build boundary.
      const unitSuffix = (this.headerWeatherUnit === 'f') ? '°F' : '°C';
      weatherCtx = {
        label: w.label || this.userLabel || '',
        temperature: this.convertTempPref(w.temp_c),
        unit: unitSuffix,
        condition: w.condition || '',
        humidity: Number.isFinite(+w.humidity) ? Math.round(+w.humidity) : null,
        wind_kmh: Number.isFinite(+w.wind_kmh) ? Math.round(+w.wind_kmh) : null,
        weather_code: Number.isFinite(+w.code) ? +w.code : null,
        fetched_at: Number.isFinite(+w.fetched_at) ? +w.fetched_at : null,
        // Provider identity — operators ask the AI questions like
        // "which weather provider are you using?" and "why don't
        // you have moon data?". The AI needs the name + the
        // supports_moon boolean to answer honestly instead of
        // hallucinating moon-phase values.
        provider: w.provider || '',
        supports_moon: !!w.supports_moon,
      };
      // Daily forecast — pass through up to 7 days so the AI can
      // answer "what's the forecast for the next 5 days?" with real
      // data. Field names stay `temp_max_c` / `temp_min_c` even
      // when the active unit is °F so the AI can disambiguate the
      // two if needed; the values themselves are pre-converted to
      // the operator's preferred unit. Same convention used elsewhere
      // (the `unit` field carries the suffix).
      // Moon-phase fields (moon_phase / moon_illumination / moonrise
      // / moonset) ride per-day on the forecast records — only
      // populated when supports_moon is true (WeatherAPI.com). The
      // AI can answer "what's the moon phase tonight?" / "when's
      // moonrise?" / "what's the illumination %?" from this data,
      // OR cleanly refuse with "I don't have moon data — switch to
      // WeatherAPI.com in Admin → Weather" when supports_moon is
      // false.
      if (Array.isArray(w.forecast) && w.forecast.length > 0) {
        weatherCtx.forecast = w.forecast.slice(0, 7).map(d => {
          const day = {
            date: d.date || '',
            temp_max_c: this.convertTempPref(d.temp_max_c),
            temp_min_c: this.convertTempPref(d.temp_min_c),
            condition: d.condition || '',
            precip_mm: Number.isFinite(+d.precip_mm) ? Math.round(+d.precip_mm * 10) / 10 : null,
          };
          if (d.sunrise) {
            day.sunrise = String(d.sunrise);
          }
          if (d.sunset) {
            day.sunset = String(d.sunset);
          }
          // Moon astronomy — WeatherAPI ONLY. Fields are null on
          // Open-Meteo so the AI can see the provider gap honestly.
          if (d.moon_phase) {
            day.moon_phase = String(d.moon_phase);
          }
          if (d.moon_illumination != null) {
            day.moon_illumination_pct = Math.round(+d.moon_illumination);
          }
          if (d.moonrise) {
            day.moonrise = String(d.moonrise);
          }
          if (d.moonset) {
            day.moonset = String(d.moonset);
          }
          return day;
        });
      }
      // Today's moon snapshot — convenience shorthand so the AI
      // doesn't have to dig into forecast[0] for the common
      // "what's the moon phase right now" question. Only populated
      // when supports_moon is true AND forecast[0] has moon data.
      if (w.supports_moon && Array.isArray(w.forecast) && w.forecast[0]) {
        const today = w.forecast[0];
        if (today.moon_phase || today.moon_illumination != null) {
          weatherCtx.moon_today = {
            phase: today.moon_phase || '',
            illumination_pct: today.moon_illumination != null
              ? Math.round(+today.moon_illumination) : null,
            moonrise: today.moonrise || '',
            moonset: today.moonset || '',
          };
        }
      }
      // Weather + moon HISTORY — populated by `_ensureWeatherHistory()`
      // pre-fetched in the AI-sidebar send path. Lets the AI answer
      // "what was the temperature yesterday afternoon?" / "when was
      // the last full moon?" / "what was the rainiest day this week?".
      // Empty cache → AI sees no history block, says it doesn't know,
      // no hallucination. Capped at 168 hourly samples (1 week).
      if (Array.isArray(this._weatherHistoryCache) && this._weatherHistoryCache.length > 0) {
        weatherCtx.history = this._weatherHistoryCache.slice(0, 168).map(s => {
          const row = {
            ts: s.ts,
            temp_c: s.temp_c,
            humidity: s.humidity,
            wind_kmh: s.wind_kmh,
            condition: s.condition || '',
          };
          if (s.moon_phase) {
            row.moon_phase = s.moon_phase;
          }
          if (s.moon_illumination != null) {
            row.moon_illumination_pct = Math.round(+s.moon_illumination);
          }
          return row;
        });
      }
    }
    // Prayer Times — when the operator has the feature enabled and the
    // widget / palette pre-fetch populated `this.prayer`, include the
    // five daily prayers, the next upcoming prayer, and the Hijri date
    // so the AI answers "when is Maghrib?" / "what's the Hijri date?"
    // from real data instead of refusing or hallucinating.
    let prayerCtx = null;
    const pr = this.prayer;
    if (pr && pr.configured !== false && pr.timings
      && typeof this.prayerWidgetRows === 'function') {
      const rows = this.prayerWidgetRows();
      if (rows.length) {
        prayerCtx = {
          location: (pr.location && pr.location.label) || this.userLabel || '',
          timezone: pr.timezone || '',
          method: pr.method_name || '',
          timings: rows.map(r => ({
            name: r.name, key: r.key, time: r.time, is_prayer: r.isPrayer,
          })),
          next: pr.next ? {
            name: this.prayerNextName(),
            key: pr.next.key,
            time: ((pr.timings[pr.next.key] || {}).time) || '',
            in_seconds: pr.next.in_seconds,
            tomorrow: !!pr.next.tomorrow,
          } : null,
          hijri: pr.hijri ? {
            day: pr.hijri.day,
            month: pr.hijri.month_en,
            month_ar: pr.hijri.month_ar,
            year: pr.hijri.year,
            designation: pr.hijri.designation,
            weekday: pr.hijri.weekday_en,
            text: this.prayerHijriLabel(),
          } : null,
          gregorian: pr.gregorian || null,
        };
      }
    }
    // Problem-hosts block — full list of hosts whose status is in
    // {down, paused, unknown}, capped at 200 to bound prompt size on
    // a degraded fleet. Mirrors the Telegram AI context's
    // `problem_hosts` shape so palette grounding answers
    // "list the unknown hosts" / "what's down?" from the same data
    // regardless of which surface the operator typed the question
    // into. SPA path uses `this.hosts` (live array) so the list is
    // current to the most recent /api/hosts/list + per-host probes.
    const problemSet = new Set(['down', 'paused', 'unknown']);
    const problemHostsCtx = (Array.isArray(this.hosts) ? this.hosts : [])
      .filter(h => h && problemSet.has(String(h.status || '').toLowerCase()))
      .map(fmtHost)
      .slice(0, 200);
    // Hosts summary — fleet-wide status counts. Same shape as the
    // Telegram context so the prompt builder renders the same
    // grounding block ("CRITICAL: unconfigured hosts are NOT a
    // problem...") on both surfaces.
    const _statusCounts = {
      up: 0, down: 0, paused: 0,
      unconfigured: 0, unknown: 0, loading: 0
    };
    for (const h of (this.hosts || [])) {
      const st = String((h && h.status) || 'unknown').toLowerCase();
      if (_statusCounts[st] !== undefined) {
        _statusCounts[st]++;
      }
    }
    const ctx = {
      view: this.view || '',
      hosts: hostsCtx,
      hosts_total: hostsTotal,
      hosts_enabled: hostsEnabled,
      hosts_sample_cap: 30,
      problem_hosts: problemHostsCtx,
      hosts_summary: {
        total: hostsTotal,
        enabled: hostsEnabled,
        up: _statusCounts.up,
        down: _statusCounts.down,
        paused: _statusCounts.paused,
        unconfigured: _statusCounts.unconfigured,
        unknown: _statusCounts.unknown,
        loading: _statusCounts.loading,
        sample_cap: 30,
        sample_size: hostsCtx.length,
        problem_count: problemHostsCtx.length,
      },
      items: itemsCtx,
      items_total: (Array.isArray(this.items) ? this.items.length : 0),
      items_sample_cap: 60,
    };
    if (weatherCtx) {
      ctx.weather = weatherCtx;
    }
    if (prayerCtx) {
      ctx.prayer = prayerCtx;
    }
    // App skills the AI may invoke (run_app_skill) — instance-level
    // availability built from the pinned apps + the per-slug skill defs on
    // /api/me's client_config.app_skills. Only chips whose api_key is set are
    // offered (the backend re-enforces). No upstream fetch here; the AI runs
    // the skill, and the freshly-queued result shows on the card.
    try {
      const skillDefs = (this.me && this.me.client_config && this.me.client_config.app_skills) || {};
      const appSkills = [];
      (this.appsList || []).forEach((app) => {
        const slug = String((app && app.catalog && app.catalog.slug) || '').toLowerCase();
        const defs = slug && skillDefs[slug];
        if (!defs || !defs.length) {
          return;
        }
        (app.instances || []).forEach((inst) => {
          if (!inst || !inst.api_key_set) {
            return;
          }
          appSkills.push({
            host_id: inst.host_id,
            service_idx: inst.service_idx,
            host: inst.host_address || inst.host_id,
            slug,
            app: app.name || slug,
            skills: defs.map((s) => ({id: s.id, name: s.name})),
          });
        });
      });
      if (appSkills.length) {
        ctx.app_skills = appSkills;
      }
    } catch (_skillErr) {
      // Context assembly must never break on the skills block.
    }
    // Public IP + ISP / ASN — operator-opt-in via the
    // `public_ip_enabled` tunable. The SPA caches the last
    // /api/public-ip response on `this.publicIp` so repeated AI
    // calls don't re-fetch; the backend has its own cache
    // so even uncached SPA-side calls cost at most one ifconfig.co
    // round-trip per cache window. Skipped cleanly when the setting
    // is off (backend returns enabled:false) so the prompt-builder
    // doesn't render an empty block.
    if (this.publicIp && this.publicIp.enabled && this.publicIp.ip) {
      ctx.public_ip = {
        ip: this.publicIp.ip,
        isp: this.publicIp.isp || '',
        asn: this.publicIp.asn || '',
        country: this.publicIp.country || '',
        city: this.publicIp.city || '',
      };
      // Last CHANGE event — stamped on the /api/public-ip response
      // (backend `logic.public_ip.last_change`) and carried on
      // `this.publicIp.last_change`. The backend prompt-builder reads
      // `public_ip.last_change` to answer "when did my IP last change /
      // what was the previous IP / provider" — without forwarding it
      // here that block never fires. `{ts, ip, isp, asn, country, city,
      // prev_ip, prev_ts}` shape; omitted when there's no history yet.
      if (this.publicIp.last_change && this.publicIp.last_change.ts) {
        const lc = this.publicIp.last_change;
        ctx.public_ip.last_change = {
          ts: lc.ts, ip: lc.ip || '', isp: lc.isp || '', asn: lc.asn || '',
          country: lc.country || '', city: lc.city || '',
          prev_ip: lc.prev_ip || '', prev_ts: lc.prev_ts || 0,
        };
      }
      // Public-IP CHANGE history — lets the AI answer questions like
      // "when did my IP last change?" / "what was my previous ISP?".
      // Reads from the same `this._publicIpHistoryCache` populated by
      // `_ensurePublicIpHistory()` (pre-fetched ALONGSIDE _ensurePublicIp
      // in the AI-sidebar send path so this synchronous context-builder
      // can fold it in). Empty / null → block omitted, AI sees no
      // history and says it doesn't know rather than hallucinating.
      if (Array.isArray(this._publicIpHistoryCache) && this._publicIpHistoryCache.length) {
        ctx.public_ip.history = this._publicIpHistoryCache.map(h => ({
          ts: h.ts, ip: h.ip, isp: h.isp || '', asn: h.asn || '',
          country: h.country || '', city: h.city || '',
        }));
      }
    }
    // Current time context. Mirrors the Telegram listener's `time`
    // block in `_build_telegram_ai_context` so the AI palette in the
    // SPA can answer "what time is it" / "what's today's date"
    // without falling back to the training-cutoff guess. Honours
    // the operator's scheduler timezone via me.client_config when
    // available; otherwise the browser's local TZ is used.
    try {
      const nowUtc = new Date();
      const tzName = (this.me && this.me.client_config
          && this.me.client_config.scheduler_tz)
        || Intl.DateTimeFormat().resolvedOptions().timeZone
        || 'UTC';
      // Build a local-iso string in the resolved timezone via Intl.
      const fmt = new Intl.DateTimeFormat('en-CA', {
        timeZone: tzName, year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
      });
      const parts = fmt.formatToParts(nowUtc);
      const pick = (t) => (parts.find(p => p.type === t) || {}).value || '';
      const localIso = `${pick('year')}-${pick('month')}-${pick('day')}T`
        + `${pick('hour')}:${pick('minute')}:${pick('second')}`;
      // Compute the UTC offset for the resolved tz at this instant.
      // Intl exposes timeZoneName 'shortOffset' (e.g. 'GMT+3') in
      // recent browsers; fall back to a manual diff for older.
      let offset = '';
      try {
        const off = new Intl.DateTimeFormat('en-US', {timeZone: tzName, timeZoneName: 'shortOffset'})
          .formatToParts(nowUtc).find(p => p.type === 'timeZoneName');
        if (off && off.value) {
          offset = off.value.replace(/^GMT/, '').replace(/^UTC/, '') || '+00:00';
          if (!/^[+-]/.test(offset)) {
            offset = '+' + offset;
          }
          if (/^[+-]\d{1,2}$/.test(offset)) {
            offset += ':00';
          }
        }
      } catch (_) {
      }
      const weekday = new Intl.DateTimeFormat('en-US', {weekday: 'long', timeZone: tzName}).format(nowUtc);
      ctx.time = {
        utc_iso: nowUtc.toISOString().replace(/\.\d{3}Z$/, '+00:00'),
        local_iso: localIso + (offset || ''),
        timezone: tzName,
        utc_offset: offset || '',
        weekday: weekday,
      };
    } catch (_) {
    }
    // Backups context — operator-flagged the AI saying "I don't have
    // access to the history of backup jobs" when asked "what's the
    // latest backup?". Forward the latest 5 SQLite-backup zip rows
    // AND the latest 5 Settings-as-Code (config_backup) snapshots
    // when either list is loaded so the AI can answer freshness +
    // pruning questions without a separate fetch. Skip cleanly when
    // the lists are empty / not yet loaded so a fresh tab doesn't
    // pad the prompt with meaningless rows.
    const fmtBackup = (b) => ({
      name: String(b.name || ''),
      size: Number(b.size) || 0,
      mtime: Number(b.mtime) || 0,
    });
    const sqliteBackups = (Array.isArray(this.backups) ? this.backups : [])
      .slice(0, 5).map(fmtBackup).filter(b => b.name);
    const configBackups = (Array.isArray(this.configBackupSaved) ? this.configBackupSaved : [])
      .slice(0, 5).map(fmtBackup).filter(b => b.name);
    if (sqliteBackups.length || configBackups.length) {
      ctx.backups = {
        sqlite: sqliteBackups,         // /api/backups (full DB + avatars zips)
        config: configBackups,         // /api/admin/config-backup/list (Settings-as-Code JSON)
        sqlite_count: sqliteBackups.length,
        config_count: configBackups.length,
      };
    }
    // Stats — every Stats sub-page's already-loaded data, ground-
    // truth context for AI questions like "what's our MTD AI spend?"
    // / "how many failures last week?" / "top chatty host this 7d?"
    // / "how big is the DB?". Each block lands only if that Stats
    // sub-page has been opened AT LEAST ONCE this session (its
    // `*Loaded` flag is true) — keeps the payload small for fresh
    // tabs while still grounding the AI on whatever the operator's
    // actually been looking at. Backend endpoints exist under
    // `/api/admin/stats/*` if the AI ever needs to fetch them
    // directly; this client-side bundle just opportunistically
    // forwards what's already in memory.
    const stats = {};
    if (this.statsOverviewLoaded && this.statsOverview && Object.keys(this.statsOverview).length) {
      // Pluck the headline counts only — the SPA card payload has
      // sub-breakdowns like per-provider lists that bloat the
      // prompt; the AI rarely needs them.
      const o = this.statsOverview;
      stats.overview = {
        users: o.users || null,
        sessions: o.sessions || null,
        providers: o.providers || null,
        hosts: o.hosts || null,
        host_groups: o.host_groups || null,
        assets: o.assets || null,
        nodes: o.nodes || null,
        stacks: o.stacks || null,
        services: o.services || null,
        containers: o.containers || null,
        schedules: o.schedules || null,
        backups: o.backups || null,
        config_backups: o.config_backups || null,
        tunables: o.tunables || null,
      };
    }
    if (this.statsDatabaseLoaded && this.statsDatabase && Object.keys(this.statsDatabase).length) {
      const d = this.statsDatabase;
      stats.database = {
        size_bytes: (d.size && d.size.bytes) || 0,
        top_tables: (d.tables || []).map(t => ({
          name: t.name, bytes: t.bytes, rows: t.rows,
        })),
        top_queries: (d.queries || []).map(q => ({
          table: q.table, rows: q.rows,
        })),
        // Projection — first + last point only (90d window). Full
        // 91-point series would bloat the prompt.
        projection_first: (d.projection || [])[0] || null,
        projection_last: (d.projection || [])[(d.projection || []).length - 1] || null,
      };
    }
    if (this.statsSamplesLoaded && this.statsSamples && Object.keys(this.statsSamples).length) {
      const s = this.statsSamples;
      stats.samples = {
        grand_total: s.grand_total || 0,
        tables: (s.tables || []).map(t => ({
          name: t.name,
          provider: t.provider,
          rows: t.rows,
          unique_hosts: t.unique_hosts,
          oldest_ts: t.oldest_ts,
          newest_ts: t.newest_ts,
        })),
      };
    }
    if (this.statsIncidentsLoaded && this.statsIncidents && Object.keys(this.statsIncidents).length) {
      const i = this.statsIncidents;
      stats.incidents = {
        window_hours: i.window_hours || null,
        total_failures: i.total_failures || 0,
        total_recoveries: i.total_recoveries || 0,
        total_events: i.total_events || 0,
        mttr_overall_seconds: i.mttr_overall_seconds,
        per_provider: i.per_provider || [],
        top_hosts: i.top_hosts || [],
      };
    }
    if (this.statsNetworkLoaded && this.statsNetwork && Object.keys(this.statsNetwork).length) {
      const n = this.statsNetwork;
      stats.network = {
        window_hours: n.window_hours || null,
        total: n.total || null,
        top_24h: (n.top_24h || []).slice(0, 5),
        top_7d: (n.top_7d || []).slice(0, 5),
        top_chatty: (n.top_chatty || []).slice(0, 5),
      };
    }
    if (this.statsAiCostLoaded && this.statsAiCost && Object.keys(this.statsAiCost).length) {
      const a = this.statsAiCost;
      stats.ai_cost = {
        month_to_date: a.month_to_date || null,
        last_month: a.last_month || null,
        projected_eom: a.projected_eom || null,
        mtd_metrics: a.mtd_metrics || null,
        tokens_by_provider_model: (a.tokens_by_provider_model || []).slice(0, 10),
        top_expensive: (a.top_expensive || []).slice(0, 5),
      };
    }
    if (Object.keys(stats).length) {
      ctx.stats = stats;
    }
    // Tunables — always-present compact map of {key: effective_value}
    // so the AI can answer "what's the Pulse sample interval?" /
    // "show me the Webmin probe budget" without the operator having
    // opened Admin → Config. Sourced from `tuningEffective` (Admin →
    // Config GET response) when loaded; falls back to `tuningForm`
    // (live form values) when not. Captures every key in the
    // canonical `_allTuningKeys()` union so a new TUNABLE auto-
    // surfaces here as soon as it's added to the resolver.
    try {
      const effMap = (this.tuningEffective && typeof this.tuningEffective === 'object')
        ? this.tuningEffective : {};
      const formMap = (this.tuningForm && typeof this.tuningForm === 'object')
        ? this.tuningForm : {};
      const allKeys = (typeof this._allTuningKeys === 'function')
        ? this._allTuningKeys() : [];
      const tunables = {};
      for (const k of allKeys) {
        const eff = effMap[k];
        if (eff && (eff.effective !== undefined && eff.effective !== null)) {
          tunables[k] = eff.effective;
        } else if (formMap[k] !== undefined && formMap[k] !== '' && formMap[k] !== null) {
          const n = Number(formMap[k]);
          tunables[k] = Number.isFinite(n) ? n : formMap[k];
        }
      }
      if (Object.keys(tunables).length) {
        ctx.tunables = tunables;
      }
    } catch (_) { /* defensive — never block context build */
    }
    // Settings — non-secret subset of the live `this.settings` so
    // the AI can answer "is Beszel enabled?" / "what's the Apprise
    // tag?". Secret-suffix keys (token / password / api_key /
    // secret / private_key / passphrase) are NEVER included; the
    // SPA only carries `_set` flags for those, so even a wrong
    // iteration here can't leak material. Master toggles + active-
    // source CSV + per-provider URL + verify-tls + the chip-colour
    // overrides are the operator-visible state the AI typically
    // needs to ground.
    try {
      const s = this.settings || {};
      const settingsPicked = {};
      const secretSuffixes = /(_token|_password|_secret|_api_key|_private_key|_passphrase)$/;
      // Nested rollup-objects that DUPLICATE flat keys already in
      // settings — shipping both confuses the AI when the flat
      // and nested versions desync (e.g. operator toggled
      // `weather_enabled` without saving, so `weather.enabled` is
      // stale). The flat keys are the source of truth for AI
      // grounding; the nested rollups exist only for admin-form
      // hydration shape. Skip them here to avoid the AI seeing
      // contradictory state.
      const nestedRollupKeys = new Set(['weather', 'asset_inventory']);
      for (const k of Object.keys(s)) {
        if (secretSuffixes.test(k)) {
          continue;
        }
        if (nestedRollupKeys.has(k)) {
          continue;
        }
        const v = s[k];
        // Skip empty strings + nulls — they're "not set" rather than
        // operator-meaningful state; the AI shouldn't need to know
        // every blank field. Also skip objects / arrays past a small
        // size cap to avoid bloating the prompt with JSON blobs.
        if (v === '' || v === null || v === undefined) {
          continue;
        }
        if (typeof v === 'object') {
          try {
            const j = JSON.stringify(v);
            if (j.length > 400) {
              continue;
            }
            settingsPicked[k] = v;
          } catch (_) { /* skip */
          }
          continue;
        }
        settingsPicked[k] = v;
      }
      if (Object.keys(settingsPicked).length) {
        ctx.settings = settingsPicked;
      }
    } catch (_) { /* defensive */
    }
    // Other open tabs — the cross-device session-handoff signal so the
    // AI can answer "what was I looking at on the other tab / on my
    // desktop?". Built from `this.tabActivity` (the live heartbeat map,
    // keyed by client_id) minus THIS tab's own client_id. Each entry is
    // already JSON-able + sanitised by the backend heartbeat handler
    // (main_pkg/scan_routes.py); we forward a compact subset (title /
    // view / rich_label / device label / seconds-ago) so the prompt
    // stays small. Skipped cleanly when no sibling tab is open.
    try {
      const map = this.tabActivity || {};
      const myId = window.__ogClientId;
      const nowMs = Date.now();
      const otherTabs = [];
      for (const cid of Object.keys(map)) {
        if (!cid || cid === myId) {
          continue;
        }
        const e = map[cid] || {};
        const tsMs = Number(e.ts || 0) * 1000;
        otherTabs.push({
          title: e.title || e.view || '',
          view: e.view || '',
          rich_label: e.rich_label || '',
          device: (typeof this._tabActivityDeviceLabel === 'function')
            ? this._tabActivityDeviceLabel(e.device) : '',
          seconds_ago: tsMs ? Math.max(0, Math.round((nowMs - tsMs) / 1000)) : null,
        });
      }
      // Newest first, capped — a power operator rarely has > a handful.
      // null seconds_ago (no ts) sinks to the bottom via Infinity.
      const _age = (t) => (t.seconds_ago == null ? Infinity : Number(t.seconds_ago));
      otherTabs.sort((a, b) => (_age(a) - _age(b)));
      if (otherTabs.length) {
        ctx.other_tabs = otherTabs.slice(0, 8);
      }
    } catch (_) { /* never break ctx assembly on the tabs block */
    }
    return ctx;
  },
};
