# noinspection PyProtectedMember
# noinspection PyUnresolvedReferences
"""Telegram AI fallback — markdown / escape helpers + `_ai_reply`.

Split out of ``logic.telegram_listener`` to keep that module under the
"uncomfortable to navigate" threshold. The whole bundle is a single
domain: rendering AI palette replies into Telegram-safe HTML +
shipping them through the bot.

Loading contract:
  * This module imports from ``logic.telegram_listener`` at top level.
  * ``telegram_listener`` MUST NOT import from this module at top
    level — every consumer (`_ai_reply`, `_strip_ai_directives`,
    `_telegram_safe_escape`, `_markdown_to_telegram_html`) is
    lazy-imported inside ``_process_update`` so the listener finishes
    loading BEFORE this module's top-level imports resolve. Without
    that discipline the two modules would deadlock at import time
    (each would block waiting for the other's top-level statements
    to finish).
  * Module-level regex compilations + the in-process AI rate-limit
    bucket (`_AI_CALL_BUCKETS`) live here too — they're only consumed
    by the symbols defined below.

The cooldown for destructive commands (`_DESTRUCTIVE_COOLDOWN` +
`_destructive_cooldown_check`) deliberately STAYS in telegram_listener
because the command handlers (`/restart`, `/cleanup`, `/update`) are
its consumers and those still live in listener.
"""
from __future__ import annotations

import asyncio
import re as _re
import time
from typing import Any, Optional

import httpx

# Note: `get_setting` / `get_setting_bool` are NOT imported at module
# scope. Their only consumer (`_ai_reply`) re-imports them locally
# inside its defensive try/except block — same shape as `_ai`, `Tunable`,
# `_tuning_int` — so a stray missing-dep at startup can't kill the
# module's import. Top-level import would just be shadowed + flagged
# unused by the IDE.
from logic.settings_keys import (
    Settings,
    ai_provider_api_key_key,
    ai_provider_base_url_key,
    ai_provider_model_key,
)


# Listener helpers are accessed via lazy attribute lookup so this
# module can be imported WHILE the listener is still loading without
# deadlocking. Single attribute-access indirection per call site —
# `_listener()._send_reply(...)`. Python caches sys.modules so the
# import after first use costs nothing. Return type is `Any` (not the
# concrete `ModuleType` of `telegram_listener`) so static analyzers
# DON'T try to resolve attribute access through the shim — every
# `_listener()._foo` site is a runtime lookup against the loaded
# module, and the names are stable per the cross-module contract
# but PyCharm/Pyright can't see them statically. Without the `Any`
# return type the IDE floods this file with ~70 false-positive
# "Cannot find reference X in telegram_listener" / "Access to a
# protected member _X" warnings.
def _listener() -> Any:
    """Return the loaded logic.telegram_listener module.

    Resolved lazily so cross-module imports don't deadlock at startup.
    Use as ``_listener()._helper_name(...)`` from every site in this
    module that needs a listener helper. Return annotation is ``Any``
    deliberately — see the block-comment above the function for why.
    """
    from logic import telegram_listener as _tl
    return _tl


# ----------------------------------------------------------------------------
# AI fallback for non-`/` text
# ----------------------------------------------------------------------------
# Strip every action / memory directive the AI palette knows about
# BEFORE rendering text back to Telegram. Telegram is read-only for
# AI in this phase — slash-commands are the only path that can
# trigger side effects.
_AI_DIRECTIVE_LINE = _re.compile(
    r"^\s*(?:ACTION(?:_HOSTS|_ITEM|_TAG|_DATA)?|MEMORY|MEMORY-FORGET|CHART_KIND|TOOL|TOOL_ARGS)\s*:.*$",
    flags=_re.IGNORECASE | _re.MULTILINE,
)


def _strip_ai_directives(text: str) -> str:
    """Remove every AI-palette directive line from `text` and collapse
    excess whitespace. Returns the conversational body only."""
    if not text:
        return ""
    cleaned = _AI_DIRECTIVE_LINE.sub("", text)
    # Collapse 3+ newlines to a paragraph break.
    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# Markdown → Telegram-HTML rescue patterns. Defence-in-depth for the
# Telegram-surface system-prompt rule "use HTML tags, not Markdown" —
# the AI sometimes leaks `**bold**` / `__bold__` / `` `code` `` / triple-
# backtick fences / `## Header` / Markdown list bullets despite the
# explicit prompt instruction. With `parse_mode=HTML` Telegram renders
# those as literal characters, breaking the reply's visual structure.
# Conversions are intentionally narrow (anchored, multiline-friendly)
# so legitimate prose containing asterisks / underscores survives.
# Named capture groups (operator lint preference — no anonymous
# `(...)` groups) + drop the redundant `\*` escape inside the
# `[*-]` character class (literal `*` doesn't need escaping there).
_MD_FENCE = _re.compile(r"```(?:[a-zA-Z0-9_-]*)?\s*\n?(?P<inner>.*?)```", flags=_re.DOTALL)
_MD_INLINE_CODE = _re.compile(r"(?<!`)`(?P<inner>[^`\n]+?)`(?!`)")
_MD_BOLD_STAR = _re.compile(r"\*\*(?P<inner>[^\s*][^*\n]*?[^\s*]|\S)\*\*")
_MD_BOLD_UNDER = _re.compile(r"__(?P<inner>[^\s_][^_\n]*?[^\s_]|\S)__")
# Italic-star is tightened against two surface bugs:
# (a) asymmetric nested italic — `**bold **then *italic*** here` used
#     to leak a stray `<i>*</i>` because the bold regex's
#     forbid-`*`-in-inner makes it skip + italic matches the inner three
#     asterisks the wrong way. The bold pre-pass at line below now runs
#     a SECOND star-bold regex that allows nested italic specifically
#     for that shape, so by the time italic-star fires every legitimate
#     `*...*` is unwrapped first. Telegram's HTML parser also tolerates
#     stray `*` chars now via `_telegram_safe_escape` so a residual
#     unmatched `*` renders as literal.
# (b) arithmetic-shorthand — `a*b*c` (no spaces) used to render
#     as `a<i>b</i>c`. The lookbehind+lookahead now require WHITESPACE
#     OR START/END-OF-STRING around the asterisks (operators rarely
#     type `*italic*` jammed against alphanum on both sides; markdown
#     authors universally space it). Word-boundary-jammed `*` survives
#     as literal.
_MD_ITALIC_STAR = _re.compile(
    r"(?<![*A-Za-z0-9_])\*(?P<inner>[^\s*][^*\n]*?[^\s*]|\S)\*(?![*A-Za-z0-9_])"
)
_MD_ITALIC_UNDER = _re.compile(r"(?<![A-Za-z0-9_])_(?P<inner>[^\s_][^_\n]*?[^\s_]|\S)_(?![A-Za-z0-9_])")
_MD_HEADING = _re.compile(r"^\s*#{1,6}\s+(?P<inner>.+?)\s*$", flags=_re.MULTILINE)
_MD_LIST_BULLET = _re.compile(r"^(?P<indent>\s*)[*-]\s+", flags=_re.MULTILINE)

# Telegram's `parse_mode=HTML` recognises a fixed tag set. Every other
# `<` / `>` MUST be escaped or the parser rejects the whole message
# with HTTP 400. The plain `_listener()._escape` helper escapes ALL `<` / `>`
# (correct for purely user-supplied strings); for AI-rendered prose
# we want to PRESERVE the formatting tags introduced by
# `_markdown_to_telegram_html` while still neutralising stray
# `<unknown>` markup the AI might have emitted directly.
#
# Includes `<a>` so AI-generated hyperlinks survive the escape pass.
# Telegram accepts ONE attribute on `<a>` — `href` — and rejects the
# whole message if any other attribute is present OR if the scheme
# isn't `http(s)` / `tg://`. The `_sanitize_a_tag` helper below
# strips every attribute except a whitelisted `href` so an AI emit of
# `<a href="..." target="_blank" onclick="...">` reduces to a
# Telegram-acceptable shape.
_TELEGRAM_OK_TAG = _re.compile(
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|tg-spoiler|br|a)\b[^>]*>",
    flags=_re.IGNORECASE,
)

# Capture the `href` value (double-quoted, single-quoted, or unquoted).
# Used by `_sanitize_a_tag` to extract the URL from an arbitrary
# attribute list. Named-group syntax to comply with the operator-lint
# preference against anonymous capture groups.
_TELEGRAM_A_HREF = _re.compile(
    r"""href\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\s>]+))""",
    flags=_re.IGNORECASE,
)


def _sanitize_a_tag(raw: str) -> str:
    """Reduce a captured `<a ...>` opening tag to `<a href="...">` with
    a single whitelisted `href` attribute, or to the literal `&lt;a&gt;`
    when no usable href exists. Closing `</a>` passes through unchanged.

    Telegram's HTML parser HTTP-400s any `<a>` with attributes other
    than `href` (or with an `href` scheme outside `http(s)` / `tg://`).
    AI-emitted tags occasionally carry `target` / `rel` / `onclick`;
    this helper strips them so the AI's link intent survives without
    poisoning the rest of the reply.
    """
    low = raw.lower().lstrip()
    if low.startswith("</a"):
        return "</a>"
    m = _TELEGRAM_A_HREF.search(raw)
    if not m:
        # No href — Telegram requires it for `<a>`. Render the literal
        # string so the operator sees the missing-href bug.
        return "&lt;a&gt;"
    href = m.group("dq") or m.group("sq") or m.group("bare") or ""
    # Schemes allowed by Telegram's parse_mode=HTML: http, https, tg.
    # Anything else (javascript:, data:, file:, mailto:) gets blanked.
    if not href.startswith(("http://", "https://", "tg://")):
        return "&lt;a&gt;"
    # HTML-escape the href so embedded `"` / `&` / `<` / `>` can't
    # break the tag or smuggle additional attributes.
    safe_href = (
        href.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return f'<a href="{safe_href}">'


def _telegram_safe_escape(text: str) -> str:
    """HTML-escape `text` for Telegram parse_mode=HTML while preserving
    the recognised formatting tag set.

    Strategy: extract recognised-tag spans into placeholder tokens
    (sentinel chars that never appear in legitimate Unicode text),
    HTML-escape the remainder, then swap the tags back. This way
    converter-emitted `<b>` / `<i>` / `<code>` reaches Telegram intact
    while a stray `<unknown>` becomes `&lt;unknown&gt;` and renders
    as literal characters rather than breaking the parser.

    `<a>` tags are run through `_sanitize_a_tag` BEFORE stashing so
    only a whitelisted `href` attribute survives — Telegram rejects
    any other attribute or unsafe scheme."""
    if not text:
        return text or ""
    tokens: list[str] = []

    def _stash(match):
        raw = match.group()
        # Sanitise `<a>` tags so only href survives; everything else
        # in the OK-tag set is recognised verbatim by Telegram.
        if raw[:2].lower() == "<a" and (len(raw) < 3 or not raw[2].isalpha()):
            raw = _sanitize_a_tag(raw)
        tokens.append(raw)
        return f"\x00{len(tokens) - 1}\x01"

    stashed = _TELEGRAM_OK_TAG.sub(_stash, text)
    escaped = (
        stashed
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    def _restore(match):
        idx = int(match.group("idx"))
        return tokens[idx] if 0 <= idx < len(tokens) else match.group()

    return _re.sub(r"\x00(?P<idx>\d+)\x01", _restore, escaped)


# Matches one open / close / self-closing Telegram-recognised tag.
# Re-uses the same allowlist as `_TELEGRAM_OK_TAG` so the truncator
# and the safe-escape stay in lock-step on what counts as a real tag.
_TELEGRAM_TAG_SPAN = _re.compile(
    r"</?(?P<name>b|strong|i|em|u|ins|s|strike|del|code|pre|tg-spoiler|br|a)\b[^>]*>",
    flags=_re.IGNORECASE,
)

# A `<code>…</code>` span whose content is a SINGLE bot-command token
# (`/whoami`, `/run_speedtest`, `/host …`). Telegram does NOT auto-link
# bot commands that sit inside a `code` / `pre` entity — text there is
# rendered verbatim, so the command shows as monospace copy-only text
# instead of a tappable `/command` (operator-reported: per-app skill +
# `/whoami`-style commands in AI replies were copyable but not
# clickable). `<b>`/`<i>` do NOT suppress command detection — only
# `code`/`pre` do — so the fix is to drop the `<code>` wrap on
# single-command spans and let Telegram linkify the bare token.
#
# Shape: optional leading whitespace, a `/cmd` token (Telegram command
# grammar: letter-led, `[A-Za-z0-9_]`, ≤64 chars, optional `@BotName`),
# then EITHER end-of-span OR a whitespace + arbitrary trailing
# (placeholder args like `&lt;target&gt;`). The post-token boundary
# (whitespace-or-end) excludes path-like `/etc/hosts` and
# `/api/foo` strings whose next char is `/` — those keep their wrap.
_CODE_COMMAND_SPAN = _re.compile(
    r"<code>(?P<inner>\s*/[A-Za-z][A-Za-z0-9_]{0,63}(?:@\w+)?(?:\s[^<]*)?)</code>",
    flags=_re.IGNORECASE,
)


def _make_commands_clickable(html: str) -> str:
    """Unwrap single-command `<code>/cmd</code>` spans → bare `/cmd` so
    Telegram renders them as tappable bot commands instead of verbatim
    monospace text. Runs AFTER `_telegram_safe_escape` (so the `<code>`
    tags present are the recognised ones and any `<…>` placeholder args
    are already entity-escaped). Multi-token code spans (e.g. an aliases
    list `/ver, /v` or a real code snippet) don't match and keep their
    wrap."""
    if not html or "<code>" not in html:
        return html
    return _CODE_COMMAND_SPAN.sub(lambda m: m.group("inner"), html)


def _truncate_telegram_html(text: str, max_chars: int) -> str:
    """Trim `text` to at most `max_chars` WITHOUT cutting mid-HTML-tag
    AND with any unclosed tags closed at the end.

    Steps:
      1. Walk every recognised tag span in `text[:max_chars]`,
         tracking the open-stack (`<b>` pushes, `</b>` pops).
      2. If the requested cut would land INSIDE a tag span (between
         `<` and `>`), back the cut up to just before the `<`.
      3. Truncate at the safe boundary.
      4. Emit matching close tags for every tag still open at the
         truncation point, in reverse stack order, so Telegram's
         HTML parser sees well-balanced markup.

    Pre-fix the truncator was a bare `text[:max_chars]` slice — a
    cut mid `<co` / `<b` left Telegram's parser staring at an
    unclosed `<` and the whole message HTTP-400'd, so the user
    saw no reply for any AI response that happened to slice
    awkwardly. With this helper the truncated bubble always
    renders.
    """
    if max_chars <= 0 or not text:
        return ""
    if len(text) <= max_chars:
        return text

    # Back the cut off any tag that straddles the boundary.
    cut = max_chars
    for match in _TELEGRAM_TAG_SPAN.finditer(text):
        if match.start() >= cut:
            break
        if match.end() > cut:
            # Cut would land inside this tag — pull back to before it.
            cut = match.start()
            break

    head = text[:cut]
    # Build the open-stack so we can close anything still open.
    stack: list[str] = []
    for match in _TELEGRAM_TAG_SPAN.finditer(head):
        raw = match.group()
        name = (match.group("name") or "").lower()
        if name == "br":
            continue  # self-closing; no stack pressure
        if raw.startswith("</"):
            # Close — pop the most recent matching open.
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == name:
                    stack.pop(i)
                    break
        else:
            stack.append(name)

    # Close any tags still open, in reverse-of-open order.
    suffix = "".join(f"</{name}>" for name in reversed(stack))
    return head + suffix


def _markdown_to_telegram_html(text: str) -> str:
    """Convert common Markdown leakage to Telegram-HTML equivalents.

    The Telegram-surface system prompt tells the AI to use HTML tags
    instead of Markdown, but models sometimes ignore that and emit
    `**bold**` / `## Header` / Markdown list markers anyway. With
    `parse_mode=HTML` Telegram shows those as literal characters,
    which the user reads as broken formatting. This helper rescues
    the most common patterns:
      - ```triple-backtick fences``` → `<code>...</code>`
      - `inline code` → `<code>...</code>`
      - `**bold**` / `__bold__` → `<b>...</b>`
      - `*italic*` / `_italic_` → `<i>...</i>` (anchored to avoid
        eating asterisks inside prose like `5 * 4`)
      - `## Header` (any 1-6 hashes) → `<b>Header</b>`
      - Markdown list `* item` / `- item` → `• item` (Telegram
        renders the bullet character cleanly without parse-mode help)
    """
    if not text:
        return text
    # Fences first so the inline-code pass doesn't double-process.
    text = _MD_FENCE.sub(lambda m: f"<code>{m.group('inner').strip()}</code>", text)
    text = _MD_INLINE_CODE.sub(r"<code>\g<inner></code>", text)
    text = _MD_BOLD_STAR.sub(r"<b>\g<inner></b>", text)
    text = _MD_BOLD_UNDER.sub(r"<b>\g<inner></b>", text)
    text = _MD_ITALIC_STAR.sub(r"<i>\g<inner></i>", text)
    text = _MD_ITALIC_UNDER.sub(r"<i>\g<inner></i>", text)
    text = _MD_HEADING.sub(r"<b>\g<inner></b>", text)
    text = _MD_LIST_BULLET.sub(r"\g<indent>• ", text)
    return text


# noinspection SpellCheckingInspection
def _shape_prayer_ctx(p: dict) -> dict:
    """Convert a ``logic.prayer_times.fetch`` response into the compact
    ``prayer`` context block ``ai_extras.build_palette_user_prompt``
    renders (same shape the SPA's ``_buildAiPaletteContext`` emits)."""
    timings = p.get("timings") or {}
    order = ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")
    rows = []
    for k in order:
        r = timings.get(k) or {}
        if r.get("time"):
            rows.append({
                "name": r.get("name") or k.title(),
                "key": k,
                "time": r.get("time"),
                "is_prayer": bool(r.get("prayer")),
            })
    nxt = p.get("next") or {}
    next_block = None
    if nxt.get("key"):
        nk = nxt["key"]
        next_block = {
            "name": nxt.get("name") or nk.title(),
            "key": nk,
            "time": (timings.get(nk) or {}).get("time") or "",
            "in_seconds": nxt.get("in_seconds"),
            "tomorrow": bool(nxt.get("tomorrow")),
        }
    h = p.get("hijri") or {}
    hijri_block = None
    if h.get("day"):
        hijri_block = {
            "day": h.get("day"),
            "month": h.get("month_en"),
            "month_ar": h.get("month_ar"),
            "year": h.get("year"),
            "designation": h.get("designation"),
            "weekday": h.get("weekday_en"),
            "text": h.get("text"),
        }
    return {
        "location": (p.get("location") or {}).get("label") or "",
        "method": p.get("method_name") or "",
        "timezone": p.get("timezone") or "",
        "timings": rows,
        "next": next_block,
        "hijri": hijri_block,
        "gregorian": p.get("gregorian"),
    }


# noinspection PyProtectedMember,PyUnresolvedReferences
# All `_listener()._X` accesses below are intentional — see the
# `_listener()` shim docstring at the top of this file for the
# cross-module-shim rationale. The directive above silences both
# the protected-member warning AND the unresolved-reference
# warning that the lazy-import shim triggers in PyCharm.
async def resolve_host_status_rows() -> "tuple[dict, list]":
    """Curated host rows with their EFFECTIVE status — the SAME view the
    SPA Hosts page shows, now SHARED by the /hosts slash command + the AI
    context so all three surfaces agree. Calls api_hosts_list() for the
    snapshot status, reconciles a stale down/unknown to up when a
    provider's latest probe succeeded, then RE-PROBES any still-flagged
    host via the per-host path the SPA fans out. Returns (list_resp,
    rows). Never raises (returns ({}, []) on failure).
    """
    # noinspection PyBroadException
    try:
        from main_pkg.hosts_routes import api_hosts_list
        list_resp = await api_hosts_list()
        if not isinstance(list_resp, dict):
            list_resp = {}
        api_hosts = list_resp.get("hosts") or []

        # `api_hosts_list` is the SKELETON endpoint — it returns
        # snapshot-derived status but doesn't fan out per-host probes
        # (the SPA does that via `/api/hosts/one/{id}` after the list
        # paints). The skeleton returns `unknown` for hosts whose
        # snapshot is empty / stale OR whose providers haven't probed
        # successfully yet — even when those hosts ARE up and being
        # actively monitored. Operator-flagged: 10 of 11 "unknown"
        # hosts (ftth, router5g, server, vcenter, nvr, stream,
        # hdhomerun, winxpx64, win7x64, win10x64) are actually working
        # in the SPA view. Reconcile by promoting `unknown` →
        # `up` when ANY provider on the host has a `last_ok_ts`
        # within the last hour in `host_provider_last_ok`. This
        # matches the SPA Hosts view's effective rendering after
        # the per-host fan-out lands.
        try:
            from main_pkg.hosts_routes import _get_provider_state_index
            provider_state = _get_provider_state_index() or {}
        # noinspection PyBroadException
        except Exception as _idx_err:  # noqa: BLE001
            print(f"[telegram_listener] provider state index unavailable: {_idx_err}")
            provider_state = {}
        # Reconcile a STALE snapshot-derived `down` / `unknown` to `up` when
        # a provider's MOST-RECENT probe actually succeeded — matching the
        # SPA Hosts view's effective rendering after its per-host
        # `/api/hosts/one` fan-out (which the AI context can't afford to do).
        # Operator-flagged TWICE: first `unknown` hosts (ftth / router / …),
        # then `down` hosts (vpn2 / winserver / asusrtac3100 / switches /
        # printers) read as outages in the AI while the web showed them up.
        # Both classes are the same root cause: `/api/hosts/list` returns the
        # cached/snapshot status, which lags the live per-host probe.
        #
        # Precise signal (not a loose recency heuristic): promote ONLY when
        # some provider has `last_ok_ts >= last_failure_ts` (its latest probe
        # was a SUCCESS, so the host is reachable NOW) AND that success is
        # recent. A genuinely-down host has a MORE-recent failure than its
        # last ok, so it is NOT promoted — it correctly stays `down`.
        recent_ok_window_s = 3600  # last-ok must be within the last hour
        _now_ts = time.time()
        _stale_outage_statuses = {"unknown", "down"}
        for row in api_hosts:
            if (row.get("status") or "").lower() not in _stale_outage_statuses:
                continue
            hid = row.get("id") or ""
            providers = provider_state.get(hid) or {}
            up_now = False
            for info in providers.values():
                if not isinstance(info, dict):
                    continue
                ok_ts = int(info.get("last_ok_ts") or 0)
                fail_ts = int(info.get("last_failure_ts") or 0)
                if (ok_ts > 0 and ok_ts >= fail_ts
                    and (_now_ts - ok_ts) < recent_ok_window_s):
                    up_now = True
                    break
            if up_now:
                row["status"] = "up"

        # AUTHORITATIVE pass — re-probe the hosts STILL flagged down/unknown
        # via the SAME per-host path the SPA fans out (`api_hosts_one` →
        # `_hosts_one_inner` + `_shape_host_api_row`). `api_hosts_list` only
        # returns the SNAPSHOT status; the SPA's /hosts page then re-probes
        # each host and that's what the operator actually sees. The cheap
        # last_ok reconcile above resolves the common case without I/O; this
        # closes the rest (hosts that are genuinely up now but had no recent
        # success recorded, e.g. SNMP/ping gear the SPA re-probed live but the
        # AI's snapshot still calls down). Bounded: only the remaining problem
        # hosts, capped, concurrency-limited, each with its own timeout, so a
        # degraded fleet can't stall the Telegram reply. force=False reuses the
        # 10s provider-state cache (cheap on repeat calls).
        _reprobe_ids = [str(r.get("id") or "") for r in api_hosts
                        if (r.get("status") or "").lower() in _stale_outage_statuses
                        and r.get("id")][:25]
        if _reprobe_ids:
            # noinspection PyBroadException
            try:
                from main_pkg.hosts_routes import (
                    _load_hosts_config as _lhc, _hosts_one_inner, _shape_host_api_row)
                curated_by_id = {str(h.get("id") or ""): h for h in _lhc()}
                _sem = asyncio.Semaphore(6)

                async def _reprobe_one(host_id: str):
                    # Local names are underscore-prefixed / uniquely named so
                    # they don't shadow the enclosing function's `hid` / `row` /
                    # `providers`. `client_id` is omitted (its default is None).
                    h = curated_by_id.get(host_id)
                    if not h:
                        return host_id, None
                    # noinspection PyBroadException
                    try:
                        async with _sem:
                            state, (merged, _provs) = await asyncio.wait_for(
                                _hosts_one_inner(h, force=False),
                                timeout=15.0)
                        _row = _shape_host_api_row(
                            h, merged, _provs,
                            any_provider_enabled=bool(state.get("active")),
                            active=state.get("active"))
                        return host_id, str(_row.get("status") or "")
                    except Exception:  # noqa: BLE001
                        return host_id, None

                # Outer budget so the whole re-probe pass can't stall the
                # Telegram reply (a timeout falls through to the skeleton
                # status — no worse than before this pass existed).
                _results = await asyncio.wait_for(
                    asyncio.gather(*[_reprobe_one(i) for i in _reprobe_ids],
                                   return_exceptions=True),
                    timeout=20.0)
                _fresh = {hid: st for r in _results if isinstance(r, tuple)
                          for (hid, st) in [r] if st}
                if _fresh:
                    for row in api_hosts:
                        st = _fresh.get(str(row.get("id") or ""))
                        if st:
                            row["status"] = st
                    print(f"[telegram_listener] host re-probe reconciled "
                          f"{len(_fresh)}/{len(_reprobe_ids)} problem hosts to live status")
            except Exception as _rp_err:  # noqa: BLE001
                print(f"[telegram_listener] host re-probe pass skipped: {_rp_err}")
        return list_resp, api_hosts
    except Exception as _e:  # noqa: BLE001
        print(f"[telegram_listener] resolve_host_status_rows failed: {_e}")
        return {}, []


async def _build_telegram_ai_context(username: Optional[str] = None) -> dict:
    """Build the fleet-context block fed to the AI palette so it
    answers from real OmniGrid state instead of hallucinating host
    names from training data. Mirrors the SPA's
    ``_buildAiPaletteContext`` shape — host telemetry + recent items
    + weather + current server/scheduler time — so the same grounding
    directives in :data:`ai.PALETTE_SYSTEM_PROMPT` produce the same
    quality of answer on Telegram as in the SPA's command palette.

    ``username`` is the linked OmniGrid user (when known) — used to
    look up the per-user weather preference so "what's the weather"
    answers from the operator's saved city.

    Returns ``{view, hosts: [...], items: [...], weather: {...},
    time: {...}}``. Never raises; missing data sources degrade to
    empty dicts / lists.
    """
    ctx: dict = {"view": "telegram"}
    # ---- Current time + scheduler timezone -------------------------
    # The AI needs current-time grounding the same way it needs host
    # grounding: without it, "what time is it" / "what's today's date"
    # questions get the canned "I can't access a live clock" refusal.
    # Stamps UTC ISO + local-tz ISO (per `scheduler_timezone`) +
    # operator-resolved tz name + UTC offset, so the model can answer
    # in either reference frame.
    try:
        from datetime import datetime, timezone
        from logic.schedules import scheduler_tz_state
        tz_state = scheduler_tz_state() or {}
        resolved_tz_name = tz_state.get("resolved") or "UTC"
        now_utc = datetime.now(timezone.utc)
        try:
            from zoneinfo import ZoneInfo
            now_local = now_utc.astimezone(ZoneInfo(resolved_tz_name))
            local_iso = now_local.isoformat(timespec="seconds")
            offset = now_local.utcoffset()
            offset_str = ""
            if offset is not None:
                total = int(offset.total_seconds())
                sign = "+" if total >= 0 else "-"
                offset_str = f"{sign}{abs(total) // 3600:02d}:{(abs(total) % 3600) // 60:02d}"
        except (ImportError, ValueError, KeyError):
            local_iso = now_utc.isoformat(timespec="seconds")
            offset_str = "+00:00"
        ctx["time"] = {
            "utc_iso": now_utc.isoformat(timespec="seconds"),
            "local_iso": local_iso,
            "timezone": resolved_tz_name,
            "utc_offset": offset_str,
            "weekday": now_utc.strftime("%A"),
        }
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        print(f"[telegram_listener] context time build failed: {e}")
        ctx["time"] = {}
    # ---- Weather: per-user saved location via api_weather ----------
    # When the linked operator has a topbar weather location saved,
    # fetch current conditions + 7-day forecast and inline them. Uses
    # the same in-memory cache the topbar widget hits, so a burst of
    # AI calls won't multiply upstream traffic.
    if username:
        try:
            from main_pkg.scan_routes import api_weather
            # noinspection PyProtectedMember
            # Sanctioned cross-module access (same _listener()._<helper>
            # lazy-resolve pattern used file-wide).
            loc = _listener()._load_user_weather_pref(username)
            if loc and loc.get("lat") is not None and loc.get("lon") is not None:
                wx = await api_weather(
                    lat=float(loc["lat"]),
                    lon=float(loc["lon"]),
                    label=(loc.get("label") or "").strip(),
                )
                if isinstance(wx, dict) and not wx.get("error"):
                    forecast = wx.get("forecast") or []
                    ctx["weather"] = {
                        "label": wx.get("label") or loc.get("label") or "",
                        "temp_c": wx.get("temp_c"),
                        "humidity": wx.get("humidity"),
                        "wind_kmh": wx.get("wind_kmh"),
                        "condition": wx.get("condition"),
                        "weather_code": wx.get("code"),
                        "forecast": forecast[:7] if isinstance(forecast, list) else [],
                    }
        # noinspection PyBroadException
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e: # noqa: BLE001
            print(f"[telegram_listener] context weather build failed: {e}")
    # ---- Prayer Times + Hijri date — per-user saved weather location.
    # Operator-opt-in (prayer_times_enabled); method + Asr school
    # come from Admin → Prayer Times. Same in-process cache the widget
    # hits. Lets the Telegram AI answer "when is the next prayer" /
    # "what's the Hijri date" from real data.
    if username:
        try:
            from logic import prayer_times as _pt
            if _pt.is_enabled():
                # noinspection PyProtectedMember
                # Sanctioned cross-module access (the file-wide
                # _listener()._<helper> lazy pattern).
                loc = _listener()._load_user_weather_pref(username)
                if loc and loc.get("lat") is not None and loc.get("lon") is not None:
                    pdata = await _pt.fetch(
                        float(loc["lat"]), float(loc["lon"]),
                        label=(loc.get("label") or "").strip(),
                    )
                else:
                    dloc = _pt.default_location()
                    pdata = await _pt.fetch(
                        float(dloc["lat"]), float(dloc["lon"]),
                        label=dloc.get("label") or "",
                    ) if dloc else None
                if isinstance(pdata, dict) and pdata.get("timings") and not pdata.get("error"):
                    ctx["prayer"] = _shape_prayer_ctx(pdata)
        # noinspection PyBroadException
        except Exception as e:  # noqa: BLE001
            print(f"[telegram_listener] context prayer build failed: {e}")
    # ---- Public IP / ISP / ASN — operator-opt-in ifconfig.co lookup.
    # Gated behind `public_ip_enabled` (default OFF for
    # privacy); cached in-process via `tuning_public_ip_cache_ttl_seconds`
    # so a burst of AI calls hits the upstream at most once per cache
    # window. Disabled state -> no `public_ip` key in ctx, prompt-
    # builder skips the block cleanly.
    try:
        from logic.public_ip import fetch as _public_ip_fetch
        pip = await _public_ip_fetch()
        if pip:
            ctx["public_ip"] = pip
    # noinspection PyBroadException
    except Exception as e:  # noqa: BLE001
        print(f"[telegram_listener] context public_ip build failed: {e}")
    # ---- Items: live gather cache, same shape the SPA reads --------
    # Operator-flagged regression: the AI was answering "no pending
    # updates" when a stack DID have updates available, because the
    # `[:30]` cap dropped items past position 30 (gather sorts alpha,
    # not updates-first) and the AI saw a sample that happened to
    # exclude every updatable item. Fix: render the sample as
    # `updatable_items` (every item whose status is "update") +
    # `other_items` (the truncated rest) + an `items_summary` block
    # carrying the authoritative `total` / `updatable_total` /
    # `running_total` counts so the AI can answer count-style
    # questions ("how many stacks need updating?") accurately even
    # when the sample is truncated.
    # Canonical "needs update" signal is `status == "update"` (set by
    # gather.py:enrich() from the remote-digest comparison). There is
    # no separate `update_available` field on items — earlier code in
    # this module read that key and silently filtered to an empty list.
    try:
        from logic import gather
        # noinspection PyProtectedMember
        # gather._cache is the documented shared in-memory coordinator
        # snapshot (main.py mutates it directly too).
        items = list(gather._cache.get("items") or [])

        def _shape_item(i: dict) -> dict:
            # `update_available` excludes orphans for the same reason
            # the /update preview does — they're leftover Swarm task
            # containers awaiting removal, not items the operator can
            # re-update. AI count-style answers should reflect actionable
            # items only.
            needs_update = (
                (i.get("status") or "") == "update"
                and (i.get("type") or "") != "orphan"
            )
            return {
                "name": i.get("name"),
                "status": i.get("status"),
                "health": i.get("health"),
                "type": i.get("type"),
                "replicas": i.get("replicas"),
                "desired": i.get("desired"),
                "update_available": needs_update,
                "stack": i.get("stack"),
            }

        def _is_updatable(i: dict) -> bool:
            return (
                (i.get("status") or "") == "update"
                and (i.get("type") or "") != "orphan"
            )

        updatable = [_shape_item(i) for i in items if _is_updatable(i)]
        other = [_shape_item(i) for i in items if not _is_updatable(i)]
        # Cap each list independently so a fleet with many updates
        # gets the FULL list even at the cost of "other_items" tail.
        ctx["updatable_items"] = updatable[:60]
        ctx["other_items"] = other[:30]
        ctx["items"] = ctx["updatable_items"] + ctx["other_items"]
        # Cleanup candidates — the AUTHORITATIVE removable set, computed the
        # SAME way the /cleanup command does (`i.get("removable")` over the
        # gather cache). Pre-fix the AI inferred the orphan/cleanup count from
        # the capped `other_items` sample (30), so it under-reported (e.g.
        # "1 orphan" when /cleanup found 3 — the other two fell outside the
        # cap). Surfaced as its own block + `removable_total` so the model
        # answers cleanup questions from the real list, not the sample.
        removables = [i for i in items if i.get("removable")]
        ctx["cleanup_candidates"] = [
            {"name": i.get("name"), "type": i.get("type") or "container",
             "stack": i.get("stack")}
            for i in removables[:60]
        ]
        ctx["items_summary"] = {
            "total": len(items),
            "updatable_total": len(updatable),
            "running_total": sum(1 for i in items if i.get("status") == "running"),
            "removable_total": len(removables),
        }
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        print(f"[telegram_listener] context items build failed: {e}")
        ctx["items"] = []
        ctx["updatable_items"] = []
        ctx["other_items"] = []
        ctx["cleanup_candidates"] = []
        ctx["items_summary"] = {"total": 0, "updatable_total": 0,
                                "running_total": 0, "removable_total": 0}
    # ---- Hosts: route through `api_hosts_list` ---------------------
    # The Telegram AI context now consumes the SAME response shape the
    # SPA's Hosts view does — `api_hosts_list` already runs the
    # canonical `_shape_host_api_row` against every curated host using
    # snapshot fallback + the cached host-provider state, so the
    # status field reflects exactly what the operator sees on the
    # /hosts page. Pre-fix this builder rolled its own snapshot
    # classifier which (a) only recognised hosts with specific
    # telemetry keys (cpu/mem/disk percent, uptime, hostname,
    # platform, kernel) — SNMP-only / Webmin-only hosts that emit
    # different fields fell through to "unknown" even when the
    # snapshot row existed; (b) classified "no snapshot row" the same
    # as "snapshot row with empty data", whereas the SPA differentiates
    # `unconfigured` (no provider mapped) from `unknown` (providers
    # mapped but none answered) from `up` (any live or snapshot-stale
    # telemetry). Calling `api_hosts_list` directly inherits all that
    # logic for free.
    sample_cap = 60
    try:
        # Resolve curated host rows with their EFFECTIVE status (snapshot +
        # reconcile + per-host re-probe) — shared with the /hosts command so
        # all surfaces agree with the web. See resolve_host_status_rows().
        list_resp, api_hosts = await resolve_host_status_rows()

        # Status taxonomy (canonical from `_shape_host_api_row`):
        # up / down / paused / loading / unconfigured / unknown
        status_counts: dict[str, int] = {}
        for row in api_hosts:
            st = (row.get("status") or "unknown").lower()
            status_counts[st] = status_counts.get(st, 0) + 1

        # Pass 1 — order rows so PROBLEM hosts (down / unknown / paused)
        # appear FIRST in the truncated sample. Pre-fix the order put
        # `up` first which on a 100+ host fleet pushed the 11 unknowns
        # past the sample_cap=60 — the AI saw `hosts_unknown: 11` in
        # the summary but ZERO unknown rows in the sample, so it could
        # REPORT the count but couldn't NAME any of them. Operator-
        # flagged: "ai has to list down these hosts to understand
        # more". Sort order now: down → unknown → paused → up →
        # unconfigured → loading. `unconfigured` sits LATE because
        # those are intentional inventory-only rows the operator
        # rarely needs to enumerate; `down` / `unknown` / `paused`
        # are the actionable ones.
        order = {"down": 0, "unknown": 1, "paused": 2,
                 "up": 3, "unconfigured": 4, "loading": 5}
        api_hosts_sorted = sorted(
            api_hosts, key=lambda r: order.get((r.get("status") or "unknown").lower(), 9),
        )

        # Shape rows for the AI — strip the dozens of fields
        # `api_hosts_list` returns down to the ones the AI actually
        # uses for grounding. `address` lets the AI match operator-
        # typed targets; the `*_name` aliases let it match by
        # provider-specific aliases.
        def _shape_host(r: dict) -> dict:
            d = {
                "id": r.get("id") or "",
                "label": r.get("label") or r.get("id") or "",
                "status": r.get("status") or "unknown",
                "paused": bool(r.get("paused")),
                "address": r.get("address") or "",
                "cpu_pct": r.get("cpu_percent"),
                "mem_pct": r.get("mem_percent"),
                "disk_pct": r.get("disk_percent"),
                "uptime_s": r.get("uptime_s"),
                "host_hostname": r.get("host_hostname"),
                "platform": r.get("host_platform"),
                "kernel": r.get("host_kernel"),
                "beszel_name": r.get("beszel_name") or "",
                "pulse_name": r.get("pulse_name") or "",
                "webmin_name": r.get("webmin_name") or "",
                "snmp_name": r.get("snmp_name") or "",
                # Per-host telemetry the user commonly asks the AI about
                # DIRECTLY ("what's the UPS battery %", "load on X?",
                # "how many updates pending on Y") — surfacing it here lets
                # the AI answer from the data instead of deflecting to a
                # "run /host <name>" instruction. Null / empty values are
                # stripped below so a host WITHOUT a field (e.g. no UPS)
                # doesn't carry a dozen empty keys into the prompt; a real
                # 0 (0% load) is kept.
                "ups_status": r.get("host_ups_status"),
                "battery_pct": r.get("host_battery_percent"),
                "battery_status": r.get("host_battery_status"),
                "battery_runtime_s": r.get("host_battery_runtime_s"),
                "battery_temp_c": r.get("host_battery_temp_c"),
                "load_pct": r.get("host_load_percent"),
                "model": r.get("host_model"),
                "serial": r.get("host_serial"),
                "firmware": r.get("host_firmware"),
                "vendor": r.get("host_vendor"),
                "package_updates": r.get("package_updates_count"),
            }
            return {k: v for k, v in d.items() if v is not None and v != ""}

        host_records = [_shape_host(r) for r in api_hosts_sorted[:sample_cap]]

        # `problem_hosts` block — the FULL list of every host whose
        # status is anything OTHER than `up`, regardless of sample
        # cap. The AI needs to NAME unknown / down / paused hosts when
        # the operator asks "which hosts are down?" or "list the
        # problem hosts" — answering "there are 11 unknown" without
        # names is useless. Capped at 200 to bound prompt size on a
        # very degraded fleet; in practice <30 problem hosts is the
        # common case so the cap rarely fires. Each entry carries the
        # minimum fields the AI needs to identify the host (id +
        # label + status + address + provider aliases). Same `_shape_host`
        # function as the main hosts[] sample.
        problem_statuses = {"down", "unknown", "paused"}
        problem_hosts = [
            _shape_host(r) for r in api_hosts
            if (r.get("status") or "unknown").lower() in problem_statuses
        ][:200]

        hosts_total = int(list_resp.get("curated_count") or 0) or len(api_hosts)
        hosts_enabled = int(list_resp.get("enabled_count") or 0) or len(api_hosts)

        ctx["hosts"] = host_records
        ctx["problem_hosts"] = problem_hosts
        ctx["hosts_total"] = hosts_total
        ctx["hosts_enabled"] = hosts_enabled
        ctx["hosts_sample_cap"] = sample_cap
        # Fleet-wide status counts — AUTHORITATIVE for "how many hosts
        # are X" questions. The palette user-prompt builder consumes
        # this block via grounding directives. Includes EVERY status
        # the canonical shaper might emit so the AI has the full
        # picture (`unconfigured` is normal — curated rows with no
        # providers mapped — and SHOULDN'T be reported as "unknown").
        ctx["hosts_summary"] = {
            "total": hosts_total,
            "enabled": hosts_enabled,
            "up": status_counts.get("up", 0),
            "down": status_counts.get("down", 0),
            "paused": status_counts.get("paused", 0),
            "unconfigured": status_counts.get("unconfigured", 0),
            "unknown": status_counts.get("unknown", 0),
            "loading": status_counts.get("loading", 0),
            "sample_cap": sample_cap,
            "sample_size": len(host_records),
            "problem_count": len(problem_hosts),
        }
    # noinspection PyBroadException
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        print(f"[telegram_listener] context hosts build failed: {e}")
        ctx["hosts"] = []
        ctx["problem_hosts"] = []
        ctx["hosts_total"] = 0
        ctx["hosts_enabled"] = 0
        ctx["hosts_sample_cap"] = sample_cap
        ctx["hosts_summary"] = {
            "total": 0, "enabled": 0,
            "up": 0, "down": 0, "paused": 0,
            "unconfigured": 0, "unknown": 0, "loading": 0,
            "sample_cap": sample_cap, "sample_size": 0,
            "problem_count": 0,
        }
    # ---- Tunables: effective per-knob values ----------------------
    # Mirror the SPA's `_buildAiPaletteContext` `tunables` block so AI
    # replies on Telegram have the same grounding for cadence /
    # timeout / threshold questions ("what's the Beszel sample
    # interval?" / "how long is the scheduler cooldown?"). The
    # palette user-prompt builder consumes `ctx["tunables"]` directly
    # when present (see `logic/ai.py:build_palette_user_prompt`).
    try:
        from logic.tuning import TUNABLES, tuning_int
        ctx["tunables"] = {key: tuning_int(key) for key in TUNABLES}
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        print(f"[telegram_listener] context tunables build failed: {e}")
        ctx["tunables"] = {}
    # ---- App skills the AI may invoke (run_app_skill) -------------
    # Every pinned app chip whose app declares SKILLS + (when required)
    # has its api_key set — so the model only offers / runs runnable
    # skills (e.g. Speedtest's run_speedtest) + can read each entry's
    # `last` cached result for "show last speed test".
    try:
        from logic.apps.registry import available_app_skills_context
        from logic.datetime_fmt import get_user_datetime_format
        # Resolve the linked operator's datetime_format so app_skills `last`
        # timestamps render in THEIR chosen format (Settings → Profile →
        # Formats) — falls back to the default when the user is unknown.
        _fmt = get_user_datetime_format(username or "")
        ctx["app_skills"] = available_app_skills_context(datetime_format=_fmt)
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        print(f"[telegram_listener] context app_skills build failed: {e}")
        ctx["app_skills"] = []
    # ---- Other open web tabs (cross-device handoff) ---------------
    # Lets the Telegram AI answer "what was I looking at on the other
    # tab / on my desktop / on my phone?" FROM the operator's phone. The
    # web SPA's tab-activity heartbeat registry (main_pkg/scan_routes:
    # _tab_activity_registry) is server-side, keyed per client_id with an
    # `actor` username, so this linked operator's currently-open browser
    # tabs are visible here. Telegram itself isn't a browser tab, so
    # nothing self-excludes — every live web tab for this user is
    # "another tab". Empty when no web tab is open; the prompt builder
    # then renders nothing and the GROUNDING-STRICT rule has the AI say
    # it sees no other tabs rather than inventing one.
    try:
        import time as _time
        # noinspection PyProtectedMember
        from main_pkg.scan_routes import (
            _tab_activity_registry, _tab_activity_prune,
        )
        _tab_activity_prune()
        now = _time.time()
        _ff_emoji = {"mobile": "📱", "tablet": "💻"}
        other_tabs = []
        for ent in _tab_activity_registry.values():
            if not isinstance(ent, dict):
                continue
            if (ent.get("actor") or "") != (username or ""):
                continue
            _dev = ent.get("device")
            dev = _dev if isinstance(_dev, dict) else {}
            ff = str(dev.get("form_factor") or "").lower()
            plat = str(dev.get("platform") or "").strip()
            brow = str(dev.get("browser") or "").strip()
            dev_bits = [p for p in (plat, brow) if p and p != "Other"]
            dev_label = (_ff_emoji.get(ff, "🖥️") + " " + " · ".join(dev_bits)).strip()
            _ts = ent.get("ts")
            ts = float(_ts) if isinstance(_ts, (int, float)) else 0.0
            other_tabs.append({
                "title": ent.get("title") or ent.get("view") or "",
                "view": ent.get("view") or "",
                "rich_label": ent.get("rich_label") or "",
                "device": dev_label,
                "seconds_ago": max(0, int(now - ts)) if ts else None,
            })
        # newest first; a null seconds_ago (no ts) sinks to the bottom
        other_tabs.sort(key=lambda t: (t["seconds_ago"]
                                       if t["seconds_ago"] is not None else (1 << 30)))
        if other_tabs:
            ctx["other_tabs"] = other_tabs[:8]
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        print(f"[telegram_listener] context other_tabs build failed: {e}")
    return ctx


# Per-Telegram-user AI call bucket — tracks call timestamps per
# sender so `_ai_reply` can short-circuit a runaway user before the
# AI call fires. Survives the lifetime of the listener process;
# resets on container restart (acceptable — a restart is itself a
# rate-limit signal).


# noinspection PyProtectedMember,PyUnresolvedReferences
# All `_listener()._X` accesses below are intentional — see the
# `_listener()` shim docstring at the top of this file for the
# cross-module-shim rationale. The directive above silences both
# the protected-member warning AND the unresolved-reference
# warning that the lazy-import shim triggers in PyCharm.
def _ai_rate_limit_check(user_id: int, calls_per_minute: int) -> tuple[bool, float]:
    """Return ``(allowed, wait_seconds)`` for a sender's next AI call.

    Window is a rolling 60s — counts how many AI calls this user has
    made in the past minute and compares against the configured cap.
    `allowed=False` returns the seconds remaining until the OLDEST
    call in the window ages out (so the SPA reply can tell the
    operator how long to wait).

    Cleans up the bucket as a side effect — every check evicts stale
    (>60s old) timestamps, so the dict stays bounded at
    ``calls_per_minute`` entries per active user.
    """
    now = time.time()
    window_start = now - 60.0
    _buckets = _listener()._AI_CALL_BUCKETS
    bucket = _buckets.get(user_id, [])
    # Evict stale entries up front so the length check below is honest.
    bucket = [t for t in bucket if t >= window_start]
    if len(bucket) >= max(1, int(calls_per_minute)):
        # The oldest call sets the wait — once IT ages out, a new call
        # can fit in the rolling window. Cheap O(1) probe at index 0.
        wait_s = max(0.0, bucket[0] + 60.0 - now)
        _buckets[user_id] = bucket
        return False, wait_s
    bucket.append(now)
    _buckets[user_id] = bucket
    return True, 0.0


# noinspection PyUnusedLocal
# noinspection SpellCheckingInspection
# noinspection PyProtectedMember,PyUnresolvedReferences
# All `_listener()._X` accesses below are intentional — see the
# `_listener()` shim docstring at the top of this file for the
# cross-module-shim rationale. The directive above silences both
# the protected-member warning AND the unresolved-reference
# warning that the lazy-import shim triggers in PyCharm.
async def _ai_reply(
    client: httpx.AsyncClient,
    text: str,
    msg: dict,
    omnigrid_username: str,
) -> None:
    """Route a non-`/` Telegram message through the AI palette and
    reply with the conversational response.

    Flow:
      1. Fire native Telegram "typing…" indicator + send a "🤖
         Thinking…" placeholder. Capture the placeholder's
         ``message_id`` so the final reply can be edited in place
         (same chat bubble — better UX than a new message).
      2. Build a fleet context block (hosts + items) and feed it to
         :func:`logic.ai.build_palette_user_prompt` so the AI grounds
         its answer in actual OmniGrid state instead of hallucinating
         hostnames from training data.
      3. Run the AI call with :data:`logic.ai.PALETTE_SYSTEM_PROMPT`
         (which carries the GROUNDING-STRICT / fuzzy-matching
         directives) plus a Telegram-specific override telling the
         model it's on a read-only surface.
      4. Strip any ACTION/MEMORY/CHART_KIND directives the AI emits
         anyway (defence in depth — the read-only override should
         prevent them, but bugs happen).
      5. Edit the placeholder in place with the final answer; fall
         back to a fresh ``_listener()._send_reply`` if the edit fails.
    """
    try:
        from logic.db import get_setting, get_setting_bool
        from logic.tuning import Tunable, tuning_int
        from logic import ai
        from logic.ops import notify_one_medium
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        print(f"[telegram_listener] _ai_reply import failed: {e}")
        return
    if not get_setting_bool(Settings.AI_ENABLED):
        await _listener()._send_reply(
            client,
            "AI integration is disabled. Enable it in OmniGrid → "
            "Admin → AI Integration, or use <code>/help</code> for "
            "available commands."
        )
        return
    # Per-Telegram-user rate limit — catches runaway "user types fast
    # OR has a script in a loop" before the AI call fires. Cap lives
    # in the `tuning_telegram_ai_calls_per_minute` TUNABLE (default 6).
    # Senders without a numeric user_id (rare; edited_message from
    # anonymous-admin channel?) bypass the limit — there's no stable
    # bucket key to use for them.
    sender_id_raw = (msg.get("from") or {}).get("id")
    if isinstance(sender_id_raw, int):
        cap = tuning_int(Tunable.TELEGRAM_AI_CALLS_PER_MINUTE)
        allowed, wait_s = _ai_rate_limit_check(sender_id_raw, cap)
        if not allowed:
            await _listener()._send_reply(
                client,
                f"⏳ Slow down — you've hit the AI rate limit "
                f"({cap}/min). Try again in {int(wait_s) + 1}s. The "
                f"cap is operator-tunable via "
                f"<code>tuning_telegram_ai_calls_per_minute</code> "
                f"in OmniGrid → Admin → Config."
            )
            return
    provider = (get_setting(Settings.AI_ACTIVE_PROVIDER) or "").strip().lower()
    if not provider:
        await _listener()._send_reply(client, "No AI provider configured. Set one in Admin → AI Integration.")
        return
    # Per-provider API key lookup.
    api_key = (get_setting(ai_provider_api_key_key(provider)) or "").strip()
    if not api_key:
        await _listener()._send_reply(
            client,
            f"AI provider <b>{_listener()._escape(provider)}</b> is selected but has no API key configured."
        )
        return
    model = (get_setting(ai_provider_model_key(provider)) or "").strip() or None
    base_url = (get_setting(ai_provider_base_url_key(provider)) or "").strip() or None

    # ---- Immediate user feedback: typing indicator + placeholder ---
    # The typing indicator is decorative (~5s); the placeholder is
    # the durable bubble we edit in place when the AI returns.
    await _listener()._send_chat_action(client)
    placeholder_id = await _listener()._send_reply(client, "🤖 <i>Thinking…</i>")

    # ---- Build grounded prompt -------------------------------------
    # Reuse the SPA's `build_palette_user_prompt` so Telegram and the
    # command palette feed the AI an identical record-shape. The
    # PALETTE_SYSTEM_PROMPT then enforces grounding (no hallucinated
    # hostnames) via the same GROUNDING-STRICT block both surfaces
    # share.
    ctx = await _build_telegram_ai_context(omnigrid_username)
    user_prompt = ai.build_palette_user_prompt(text, ctx)

    # Snapshot the REAL Telegram command roster from `_listener()._COMMANDS` so the
    # AI grounds its replies in actual commands instead of hallucinating
    # SPA-style names (`/status`, `/services`, `/updates`, `/errors`,
    # `/prune`, `/forecast` are common hallucinations the user
    # flagged when the prompt didn't carry the canonical list — note
    # that the singular `/update` IS a real command now; the plural
    # `/updates` is the hallucination).
    # Dedup by handler so aliases (`/start` → `_cmd_help`, `/myid` →
    # `_cmd_whoami`, `/ver` → `_cmd_version`) render alongside their
    # primary rather than as separate phantom commands.
    # Roster lines wrap each `usage` + alias in `<code>...</code>` so
    # the AI copies the pattern when echoing commands back — the
    # post-render `_telegram_safe_escape` preserves `<code>` tags AND
    # escapes the inner `<target>` to `&lt;target&gt;` correctly inside
    # the monospace span, where Telegram renders them as literal
    # angle-bracket text the way the operator expects. Without the
    # `<code>` wrap, the AI emits `/host <target>` as plain prose and
    # the safe-escape pass turns `<target>` into `&lt;target&gt;`
    # rendered literally outside a monospace context — visible bug.
    # Description field is also un-escaped here (some legacy entries
    # carry `&amp;` from the /help render path); the safe-escape pass
    # re-escapes anything Telegram needs.
    def _unesc(s: str) -> str:
        return (s or "").replace("&amp;", "&")

    _seen_handlers: set = set()
    _roster_lines: list[str] = []
    for _name, _meta in _listener()._COMMANDS.items():
        _h = _meta.get("handler")
        if _h is None or _h in _seen_handlers:
            continue
        _seen_handlers.add(_h)
        _usage = _meta.get("usage") or _name
        _desc = _unesc((_meta.get("description") or "").strip())
        # Collect aliases for the same handler so the AI sees the full
        # set of valid invocations.
        _aliases = [
            n for n, m in _listener()._COMMANDS.items()
            if n != _name and m.get("handler") is _h
        ]
        _alias_suffix = (
            f" (aliases: <code>{', '.join(_aliases)}</code>)" if _aliases else ""
        )
        _roster_lines.append(
            f"  - <code>{_usage}</code>{_alias_suffix} — {_desc}"
            if _desc else
            f"  - <code>{_usage}</code>{_alias_suffix}"
        )
    _listener()._command_roster = "\n".join(_roster_lines)

    # Telegram-specific override: PALETTE_SYSTEM_PROMPT tells the AI
    # to emit ACTION: directives for the SPA's command palette to
    # execute. Telegram is a READ-ONLY surface — append an override
    # that strips that license. The strip pass below is defence in
    # depth in case the model emits them anyway. The COMMAND ROSTER
    # block injects the canonical `_listener()._COMMANDS` list so the AI can only
    # reference real commands (operator-reported hallucinations like
    # `/status` / `/services` / `/updates` / `/errors` / `/forecast`
    # came from the AI inventing SPA-style commands without grounding).
    system_prompt = (
        ai.PALETTE_SYSTEM_PROMPT
        + "\n\n"
        + "TELEGRAM SURFACE OVERRIDE. You are replying to operator "
          f"'{omnigrid_username}' via Telegram. Fleet-MUTATING command-"
          "palette directives are NOT wired on this channel: NEVER emit "
          "MEMORY: / MEMORY-FORGET: / CHART_KIND: directives, and (except "
          "for reboot_host below) NEVER emit an ACTION: for a state-changing "
          "fleet operation (restart_* / update_* / pause / resume / "
          "cleanup_stopped / schedule_*) — those are stripped before the "
          "reply reaches the user, so for THOSE tell the operator to use "
          "the matching slash command (/restart <target>, /cleanup, etc.) "
          "or the SPA. EXCEPTION — THREE ACTION directives ARE wired on "
          "Telegram and you SHOULD emit them when the operator asks, "
          "instead of refusing or redirecting to the SPA: "
          "(1) ACTION: run_app_skill + ACTION_DATA: "
          "{\"host_id\":..,\"service_idx\":..,\"skill_id\":..} to RUN or "
          "SHOW a per-app skill — e.g. the Speedtest Tracker "
          "'run_speedtest' and 'latest_speedtest' skills listed in the "
          "app_skills context block (use the host_id / service_idx / "
          "skill id from that block). The operator's typed request IS the "
          "confirmation; the bot runs the skill server-side and appends "
          "the real result (and any image) to your reply, so DO emit the "
          "directive — NEVER say you 'cannot trigger skills on Telegram'. "
          "When a skill is annotated `[arg: …]` it takes a free-form "
          "argument — extract it from the request and ADD an "
          "\"arg\":\"<value>\" field to the ACTION_DATA. For the Seerr app: "
          "'request <movie>' / 'add <movie>' / 'get <movie>' → skill_id "
          "'seerr_request_movie' with arg set to the title; 'suggest a "
          "movie' / 'what should I watch' → skill_id 'seerr_suggest_movie' "
          "(no arg), then if the operator says 'request it' call "
          "'seerr_request_movie' with arg = the suggested title or its TMDB "
          "id. 'seerr_status' answers request-queue questions. Requesting a "
          "movie is non-destructive — just emit it, no confirm. Per-user "
          "SUGGESTION FILTERS (country / rating / genre, saved per user): to "
          "change one, skill_id 'seerr_set_filter' with arg = ONE directive — "
          "'exclude country France' / 'allow country France' / 'min rating 7' "
          "/ 'exclude genre horror' / 'only genre action' / 'allow genre "
          "horror' / 'clear'. A country directive may name SEVERAL countries "
          "at once — keep them in ONE directive joined by 'and' / commas, e.g. "
          "'exclude movies from Spain and Denmark' → 'exclude country Spain "
          "and Denmark' (do NOT drop any country, do NOT split into two "
          "skill calls). Map 'don't suggest French movies' → 'exclude "
          "country France'; 'only movies rated 7+' → 'min rating 7'; 'no "
          "horror' → 'exclude genre horror'; 'clear my filters' → 'clear'. "
          "skill_id 'seerr_show_filters' (no arg) lists them. "
          "(2) ACTION: send_notification + ACTION_DATA: "
          "{\"medium\":..,\"title\":..,\"body\":..} (medium app / apprise "
          "/ telegram) to send a notification. "
          "(3) ACTION: reboot_host + ACTION_HOSTS: <host_id> to REBOOT a "
          "whole host / switch / box over SSH (the device-aware verb — "
          "`reload` on a Cisco switch, `sudo reboot` on Linux). Use the "
          "curated host id from the context. This is DESTRUCTIVE: it only "
          "fires when the operator has enabled “Allow destructive commands”, "
          "otherwise the bot points them at /restart — emit it anyway when "
          "they ask to reboot a host and let the gate decide. Do NOT use it "
          "for Docker containers/services (that's the forbidden restart_* — "
          "redirect those to the SPA). Example: operator says 'reboot "
          "switch52' → write 'Rebooting switch52.' then ACTION: reboot_host "
          "and ACTION_HOSTS: switch52mp01. For these THREE wired actions, "
          "emit the ACTION (+ ACTION_DATA / ACTION_HOSTS) lines (they are "
          "stripped from the visible text but dispatched) and write a short "
          "natural-language sentence framing what you did. "
          "**DIAGNOSTIC TOOLS ARE ENABLED ON TELEGRAM.** When the "
          "operator asks a 'why is X failing' / 'what's in the logs' / "
          "'how often does Y happen' question that the supplied context "
          "doesn't already answer, EMIT the appropriate TOOL: / "
          "TOOL_ARGS: directives (per the DIAGNOSTIC TOOLS block above). "
          "The bot runs them and re-invokes you with the results in a "
          "Tool results block, and your NEXT reply composes a real "
          "diagnosis from the actual data. Read-only tools "
          "(get_container_events / get_recent_history / get_recent_logs) "
          "always run; host-touching tools (ssh_diag / "
          "docker_container_du) run ONLY when the operator has enabled "
          "destructive Telegram actions — if such a tool didn't run its "
          "result is simply ABSENT from the Tool results, so say you "
          "couldn't reach the host and point the operator to the SPA "
          "host drawer rather than inventing output. Answer the SPECIFIC "
          "question the operator asked — do NOT pad a focused question "
          "(e.g. 'what is wrong with the plex service') with an "
          "unrelated full-fleet summary. NEVER fabricate tool output — "
          "cite only values present in the Tool results block. "
          "**FORMATTING — Telegram HTML, NOT Markdown.** This bot "
          "sends every message with `parse_mode=HTML`. Use ONLY these "
          "tags for formatting: `<b>bold</b>` (NEVER `**bold**` or "
          "`__bold__`), `<i>italic</i>` (NEVER `*italic*` or "
          "`_italic_`), `<code>monospace</code>` (NEVER single or "
          "triple backticks). For lists, use plain `•` or `-` bullets "
          "with line breaks — no Markdown `*` / `-` list marker that "
          "appears as a literal asterisk. For section headers, use "
          "`<b>…</b>` on its own line — NEVER `## Header` / `# Header` "
          "/ `**Header**`. Asterisks and backticks render as LITERAL "
          "characters in Telegram HTML mode, so any Markdown leakage "
          "is visible to the operator as broken formatting. The "
          "post-render strip layer attempts to rescue common Markdown "
          "leaks but the prompt-level rule is the primary line of "
          "defence. "
          "**Length guidance — DO NOT BLINDLY COMPRESS.** Telegram "
          "messages stay readable up to 4096 characters; aim for "
          "under 800 on terse fleet-state replies (host counts, "
          "status lookups, single-fact answers). EXCEPTION: when the "
          "operator asks an explanatory question (weather / why is "
          "X down / what does this metric mean / explain this incident), "
          "follow the FULL render contract from the upstream block "
          "above — emit the 3-5 sentence narrative paragraph the "
          "block requests instead of collapsing it into a one-line "
          "summary. The 800-character soft target is for fleet-data "
          "questions, NOT a global ceiling on every reply. Use the "
          "supplied JSON records (hosts / items) to answer fleet-state "
          "questions rather than inventing names from training data."
        + "\n\n"
        + "**TELEGRAM COMMAND ROSTER — AUTHORITATIVE GROUND TRUTH.** "
          "The list below is the COMPLETE set of slash commands this "
          "bot supports right now. When the operator asks for the "
          "command list / help / what they can do, render EXACTLY "
          "these commands — DO NOT add, invent, or extrapolate. "
          "Commands that DO NOT appear in this list DO NOT EXIST in "
          "this deployment. Specifically NEVER mention: `/status`, "
          "`/services`, `/updates` (note plural — the real command is "
          "`/update` singular), `/errors`, `/prune`, `/forecast`, "
          "`/stacks`, `/logs`, `/exec`, `/ssh`, `/scan`, `/backup`, "
          "or any other SPA-style or Docker-style command name — "
          "those are common hallucinations from training data, not "
          "real OmniGrid commands. If the operator asks for a "
          "capability the roster doesn't cover, say so honestly + "
          "redirect them to the SPA (where the action probably "
          "exists). **Render each slash command wrapped in `<code>...</code>` "
          "tags** when citing it in your reply — e.g. "
          "`<code>/host <target></code>` — and write any angle-bracket "
          "argument placeholders RAW (`<target>`, NOT the pre-escaped "
          "`&lt;target&gt;`). The bot's renderer escapes the inner text "
          "of every `<code>` block EXACTLY ONCE, so a raw `<target>` "
          "displays as monospace `<target>`; a pre-escaped "
          "`&lt;target&gt;` would be double-escaped and show the literal "
          "entities to the operator. Render the "
          "roster in your reply using the SAME groupings the /help "
          "command uses (📖 Getting started / 🖥️ Fleet / ⚙️ Operations "
          "/ 🔗 Account / ℹ️ Info & weather) when the user asks for "
          "the full menu; for a one-off 'how do I X' question cite "
          "ONLY the single relevant command from the roster.\n\n"
          "Canonical command list (handler-deduped, aliases grouped — "
          "each `<code>...</code>` block is a literal command spelling "
          "you should reuse verbatim):\n"
        + _listener()._command_roster
    )
    # Token budget honours the operator's `tuning_ai_max_tokens`
    # setting (Admin → AI Integration → "Max response tokens"). Hard-
    # coding a low cap here breaks "thinking" models like Gemini 2.5
    # which spend the budget on internal reasoning BEFORE producing
    # output tokens — a 512-token cap can return zero visible text
    # with finish_reason=MAX_TOKENS. The 4096-char per-message
    # Telegram cap is still enforced post-render by the truncation
    # block below, so an excessive setting can't push past the wire
    # limit. Defence in depth: clamp to a reasonable upper bound.
    try:
        from logic.tuning import Tunable
        max_toks = tuning_int(Tunable.AI_MAX_TOKENS)
    except (ImportError, KeyError, ValueError, TypeError):
        max_toks = 1024
    try:
        max_toks = max(256, int(max_toks))
    except (TypeError, ValueError):
        max_toks = 1024

    # Helper: deliver `final` to the operator. Tries the in-place
    # edit first (replaces the "🤖 Thinking…" bubble); on any failure
    # falls back to a fresh sendMessage so the operator never ends up
    # without a visible reply.
    # noinspection PyProtectedMember,PyUnresolvedReferences
    # All `_listener()._X` accesses below are intentional — see the
    # `_listener()` shim docstring at the top of this file.
    async def _deliver(final: str) -> None:
        # Route through `_listener()._replace_placeholder` so an edit failure
        # stamps "(edit failed — see reply below)" on the
        # "🤖 Thinking…" bubble before the fresh reply lands —
        # operator can tell which bubble is current.
        await _listener()._replace_placeholder(client, placeholder_id, final)

    # Inner helper: record the AI call into `ai_jobs` AND `history`
    # so Telegram queries show up on the Admin → AI Usage dashboard
    # and the History tab alongside SPA palette / host-filter calls.
    # Same `kind` naming convention the SPA uses (palette → ai_palette,
    # host_filter → ai_host_filter); Telegram → ai_telegram.
    # noinspection PyProtectedMember,PyUnresolvedReferences
    # `_listener()._ai` access is intentional — see the `_listener()`
    # shim docstring at the top of this file.
    def _record_call(ok: bool, raw_result: dict | None, answer_text: str) -> None:
        try:
            from logic.db import db_conn
            ai.record_ai_call(
                db_conn_factory=db_conn,
                provider=provider,
                model=(raw_result or {}).get("model") or model or "",
                kind="telegram",
                ok=ok,
                response_time_ms=int((raw_result or {}).get("response_time_ms") or 0),
                tokens=(raw_result or {}).get("tokens"),
                error_detail=(None if ok else ((raw_result or {}).get("detail") or "")),
                history_actor=omnigrid_username or "telegram",
                history_events={
                    "prompt": text,
                    "answer": answer_text,
                    "surface": "telegram",
                    "context": {
                        "view": ctx.get("view") if isinstance(ctx, dict) else "telegram",
                        "hosts_count": len(ctx.get("hosts") or []) if isinstance(ctx, dict) else 0,
                        "items_count": len(ctx.get("items") or []) if isinstance(ctx, dict) else 0,
                    },
                },
            )
        # noinspection PyBroadException
        except Exception as _rec_err: # noqa: BLE001
            # Never let a recording failure swallow the operator's reply.
            print(f"[telegram_listener] record_ai_call failed: {_rec_err}")

    try:
        result = await ai.ask_provider(
            provider,
            api_key=api_key,
            prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            base_url=base_url,
            max_tokens=max_toks,
        )
    # noinspection PyBroadException
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        _record_call(False, {"detail": str(e)}, "")
        await _deliver(f"❌ AI call failed: <code>{_listener()._escape(str(e))}</code>")
        return
    if not isinstance(result, dict) or not result.get("ok"):
        detail = (result or {}).get("detail") if isinstance(result, dict) else "no response"
        _record_call(False, result if isinstance(result, dict) else None, "")
        await _deliver(
            f"❌ AI provider error: <code>{_listener()._escape(str(detail))}</code>"
        )
        return
    raw_text = (result.get("text") or "").strip()
    # ------------------------------------------------------------------
    # Diagnostic TOOL loop (Telegram) — mirrors the web path's two-round
    # dispatch. If the first-round reply emits `TOOL:` directives, run them
    # backend-side + re-invoke the model with the results so its next reply
    # composes a real diagnosis. Capped at ONE round-trip (bounds latency +
    # tokens). Read-only tools always run; host-touching tools (ssh_diag /
    # docker_container_du) run only when telegram_allow_destructive is set —
    # otherwise dispatch returns a `_pending_confirm` marker (Telegram has no
    # inline-confirm chip) and the tool is skipped, leaving its result absent
    # so the second-round reply tells the operator to use the SPA.
    try:
        tool_calls, _first_cleaned = ai.parse_palette_tool_calls(raw_text)
    except (ValueError, TypeError):
        tool_calls = []
    if tool_calls and isinstance(ctx, dict):
        try:
            from logic.db import get_setting_bool as _get_setting_bool
            _allow_destructive = _get_setting_bool(Settings.TELEGRAM_ALLOW_DESTRUCTIVE)
        except (ImportError, RuntimeError, ValueError, TypeError):
            _allow_destructive = False
        ctx["actor"] = omnigrid_username or "telegram"
        if _allow_destructive:
            ctx["_tool_confirm_granted"] = True
        tool_results = ctx.get("tool_results") or {}
        for _call in tool_calls:
            _tname = _call.get("name") or ""
            try:
                _tres = await ai.dispatch_palette_tool(_call, ctx)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except (RuntimeError, ValueError, TypeError, KeyError, OSError, httpx.HTTPError) as _terr:
                _tres = {"error": f"{type(_terr).__name__}: {_terr}"}
            if isinstance(_tres, dict) and _tres.get("_pending_confirm"):
                # Host-touching tool, not pre-approved on Telegram — skip;
                # its absence in tool_results signals "couldn't reach host".
                continue
            _existing = tool_results.get(_tname)
            if _existing is None:
                tool_results[_tname] = _tres
            elif isinstance(_existing, list):
                _existing.append(_tres)
            else:
                tool_results[_tname] = [_existing, _tres]
        ctx["tool_results"] = tool_results
        # Re-invoke with the tool results folded into the prompt.
        try:
            _result2 = await ai.ask_provider(
                provider,
                api_key=api_key,
                prompt=ai.build_palette_user_prompt(text, ctx),
                system_prompt=system_prompt,
                model=model,
                base_url=base_url,
                max_tokens=max_toks,
            )
            if isinstance(_result2, dict) and _result2.get("ok"):
                result = _result2
                raw_text = (_result2.get("text") or "").strip()
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        # noinspection PyBroadException
        except Exception as _e2: # noqa: BLE001
            # The first-round reply already succeeded; a second-round failure
            # must never sink it — log + fall back to the first-round text.
            print(f"[telegram_ai] second-round tool dispatch failed: {type(_e2).__name__}: {_e2}")
    # ------------------------------------------------------------------
    # AI-directive dispatch — Telegram side.
    #
    # The SPA's AI sidebar parses `ACTION: <name>` + `ACTION_DATA: {...}`
    # directives and dispatches them via the inline-confirm chip in the
    # sidebar. Telegram has no equivalent UI primitive, so this handler
    # was previously SILENT on action directives — it stripped them from
    # the visible reply and discarded them. Operator-flagged: typing
    # "send notification to telegram saying hi" produced the AI reply
    # "I'll send 'hi' to your Telegram channel" but nothing actually
    # arrived. The operator's typed message IS the explicit intent
    # (same destructive-confirm role the SPA chip plays), so we now
    # dispatch SAFE actions inline and append the outcome to the reply
    # so the operator sees the result in one cohesive message.
    #
    # Currently dispatched: `send_notification` only (the operator-typed
    # one this fix targets). Other actions (cleanup_stopped /
    # restart_* / update_* / schedule_*) stay SPA-only because their
    # blast radius justifies a UI-side confirm.
    action_outcome_line = ""
    # A skill result may carry an `image_url` (e.g. Seerr's suggest-a-movie
    # poster). We send it as a real photo (below) and strip the bare URL
    # out of the appended text so it doesn't show as a dead link.
    action_image_url = ""
    # A skill result may carry a `followup` (e.g. Seerr suggest → request) we
    # surface as a Telegram inline-keyboard button so the operator can run it
    # with one tap. {host_id, service_idx, skill_id, arg, label}.
    action_followup = None
    try:
        actions, _ = ai.parse_palette_actions(raw_text)
        action_data, _ = ai.parse_palette_action_data(raw_text)
        if "send_notification" in actions and isinstance(action_data, dict):
            medium = (action_data.get("medium") or "").strip().lower()
            note_body = (action_data.get("body") or "").strip()
            note_title = (action_data.get("title") or "").strip() or "🔔 OmniGrid"
            if medium and note_body and medium in ("app", "apprise", "telegram"):
                send_result = await notify_one_medium(
                    medium=medium,
                    title=note_title,
                    body=note_body,
                    actor_username=omnigrid_username or "telegram-operator",
                    metadata={"source": "telegram_ai_send_notification"},
                )
                if send_result.get("ok"):
                    action_outcome_line = (
                        f"\n\n✅ Sent <code>{_listener()._escape(note_body[:60])}"
                        f"{'…' if len(note_body) > 60 else ''}</code> to "
                        f"<b>{_listener()._escape(medium)}</b>."
                    )
                else:
                    _detail = send_result.get("detail") or send_result.get("error") or "unknown error"
                    action_outcome_line = (
                        f"\n\n❌ Send to <b>{_listener()._escape(medium)}</b> failed: "
                        f"<code>{_listener()._escape(str(_detail))}</code>"
                    )
            else:
                action_outcome_line = (
                    "\n\n⚠️ <i>send_notification action emitted without a "
                    "valid medium + body — nothing dispatched.</i>"
                )
        elif "reboot_host" in actions:
            # Reboot a WHOLE host / switch over SSH from free-text AI — the
            # Telegram-AI twin of the web sidebar's reboot_host action + the
            # /restart command, all sharing logic.ssh.reboot_host (device-aware
            # verb: `reload` on a Cisco SG300, `sudo reboot` on Linux). Host
            # comes from ACTION_HOSTS (preferred) or ACTION_DATA.host_id.
            # DESTRUCTIVE — only fires when telegram_allow_destructive is on;
            # otherwise it points at the explicit /restart command (typed-confirm
            # flow), same contract as the destructive app skills above.
            _hosts_cfg = _listener()._load_hosts_config()
            _rb_hosts, _ = ai.parse_palette_action_hosts(raw_text)
            rb_host = (_rb_hosts[0] if _rb_hosts
                       else str((action_data or {}).get("host_id") or "").strip())
            matched = next(
                (h for h in _hosts_cfg
                 if isinstance(h, dict) and str(h.get("id") or "") == rb_host),
                None,
            )
            try:
                from logic.db import get_setting_bool as _gsb
                _rb_allowed = _gsb(Settings.TELEGRAM_ALLOW_DESTRUCTIVE)
            except (ImportError, RuntimeError, ValueError, TypeError):
                _rb_allowed = False
            if matched is None:
                action_outcome_line = (
                    "\n\n⚠️ <i>Couldn't reboot — no curated host matched "
                    f"<code>{_listener()._escape(rb_host or '?')}</code>.</i>"
                )
            elif not _rb_allowed:
                action_outcome_line = (
                    "\n\n🛑 Rebooting a host is destructive. Run the explicit "
                    f"<code>/restart {_listener()._escape(rb_host)}</code> command "
                    "(it asks you to confirm first), or enable “Allow destructive "
                    "commands” in Admin → Notifications → Telegram."
                )
            else:
                _label = str(matched.get("label") or rb_host)
                from logic import ssh as _ssh
                rb_result = await _ssh.reboot_host(rb_host, _hosts_cfg, timeout=15.0)
                _ssh.write_reboot_audit(
                    rb_host, _label, rb_result,
                    f"telegram-ai:{omnigrid_username}" if omnigrid_username else "telegram-ai",
                )
                if rb_result.get("ok"):
                    action_outcome_line = (
                        f"\n\n✅ Reboot command (<code>{_listener()._escape(rb_result.get('command') or '')}</code>) "
                        f"sent to <b>{_listener()._escape(_label)}</b>."
                    )
                else:
                    _tail = (rb_result.get("transcript") or "")[-400:]
                    action_outcome_line = (
                        f"\n\n❌ Reboot failed for <b>{_listener()._escape(_label)}</b>: "
                        f"<code>{_listener()._escape(rb_result.get('error') or 'unknown error')}</code>"
                    )
                    if _tail:
                        action_outcome_line += f"\n<pre>{_listener()._escape(_tail)}</pre>"
        elif "run_app_skill" in actions and isinstance(action_data, dict):
            # Per-app SKILL invocation from Telegram (e.g. Speedtest's
            # run_speedtest). Resolve the chip server-side + dispatch via the
            # registry; the operator's typed request IS the intent (same role
            # the web inline-confirm chip plays). The api_key / skill-declared
            # gate is re-enforced inside run_app_skill / the module.
            sk_host = str(action_data.get("host_id") or "").strip()
            sk_id = str(action_data.get("skill_id") or "").strip()
            # Optional free-form skill argument (e.g. Seerr's request-a-movie
            # title). Skills that don't take an arg ignore it.
            sk_arg = str(action_data.get("arg") or "").strip()[:512]
            _raw_idx = action_data.get("service_idx")
            try:
                sk_idx = int(_raw_idx) if isinstance(_raw_idx, (int, str)) else -1
            except (TypeError, ValueError):
                sk_idx = -1
            from logic.apps.registry import (resolve_chip, run_app_skill,
                                             skill_is_destructive)
            host_row, chip, slug = (resolve_chip(sk_host, sk_idx)
                                    if (sk_host and sk_idx >= 0) else (None, None, ""))
            # Coerce to a concrete dict (resolve_chip returns host_row + chip
            # together, so when the chip is found host_row is a dict too — the
            # downstream guard gates on `isinstance(chip, dict)`; this keeps the
            # type checker happy on the run_app_skill / poll calls below).
            host_row = host_row if isinstance(host_row, dict) else {}
            print(f"[app_skill] INFO telegram skill request host={sk_host!r} "
                  f"svc_idx={sk_idx} skill={sk_id!r} slug={slug!r} "
                  f"chip_found={isinstance(chip, dict)} actor={omnigrid_username or 'telegram'}")
            # Destructive skills (e.g. an *arr remove, an AdGuard / Pi-hole
            # disable) only fire from free-text AI when the operator has
            # pre-approved via telegram_allow_destructive — same contract the
            # host-touching tools (ssh_diag / docker_container_du) use above.
            # Otherwise the bot refuses + points at the explicit slash command
            # (which rides the typed-confirm flow). Telegram has no inline
            # chip, so there's no mid-reply confirm to fall back to.
            _sk_destructive = bool(slug and sk_id
                                   and skill_is_destructive(slug, sk_id))
            if _sk_destructive:
                try:
                    from logic.db import get_setting_bool as _gsb
                    _sk_allowed = _gsb(Settings.TELEGRAM_ALLOW_DESTRUCTIVE)
                except (ImportError, RuntimeError, ValueError, TypeError):
                    _sk_allowed = False
            else:
                _sk_allowed = True
            if not (sk_host and sk_id and sk_idx >= 0 and isinstance(chip, dict) and slug):
                print(f"[app_skill] warning: telegram skill skipped — unresolved app "
                      f"instance (host={sk_host!r} svc_idx={sk_idx} skill={sk_id!r} "
                      f"slug={slug!r} chip_found={isinstance(chip, dict)})")
                action_outcome_line = ("\n\n⚠️ <i>Couldn't run that skill — missing or "
                                       "unknown app instance.</i>")
            elif not _sk_allowed:
                print(f"[app_skill] warning: telegram skill {sk_id!r} BLOCKED — "
                      f"destructive + telegram_allow_destructive is off "
                      f"(host={sk_host!r} svc_idx={sk_idx})")
                action_outcome_line = (
                    f"\n\n🛑 <b>{_listener()._escape(sk_id)}</b> is a destructive "
                    "action. Run it with the explicit <code>/"
                    f"{_listener()._escape(sk_id)}</code> command (it asks you to "
                    "confirm first), or enable “Allow destructive commands” in "
                    "Admin → Notifications → Telegram."
                )
            else:
                try:
                    sk_result = await run_app_skill(slug, sk_id, host_row, chip,
                                                    host_id=sk_host, service_idx=sk_idx,
                                                    arg=sk_arg,
                                                    actor_username=omnigrid_username)
                except ValueError as _ve:
                    sk_result = {"ok": False, "detail": str(_ve)}
                if isinstance(sk_result, dict) and sk_result.get("ok"):
                    _d = sk_result.get("detail")
                    action_image_url = str(sk_result.get("image_url") or "").strip()
                    # Background job (e.g. Tdarr requeue) that returned pending →
                    # poll it to completion and send the final count to this chat
                    # (the Telegram analogue of the web auto-poll). Spawned
                    # in-context so the inbound-chat ContextVar is inherited.
                    if sk_result.get("pending"):
                        _listener()._spawn_bg(_listener()._poll_pending_skill(
                            client, slug, sk_id, host_row, chip, sk_host, sk_idx,
                            omnigrid_username or ""))
                    # Capture a follow-up action (e.g. Seerr suggest → request)
                    # so we can attach a one-tap inline button to the reply.
                    _fu = sk_result.get("followup")
                    if isinstance(_fu, dict) and _fu.get("skill_id"):
                        # isinstance-guard each field (the followup dict is our
                        # own shape, always strings) instead of str()-coercing
                        # an `object`-typed dict value.
                        _sid = _fu.get("skill_id")
                        _arg = _fu.get("arg")
                        _label = _fu.get("label")
                        _emoji = _fu.get("emoji")
                        action_followup = {
                            "host_id": sk_host, "service_idx": sk_idx,
                            "skill_id": _sid if isinstance(_sid, str) else "",
                            "arg": _arg if isinstance(_arg, str) else "",
                            "label": _label if isinstance(_label, str) else "Request on Seerr",
                            "emoji": _emoji if isinstance(_emoji, str) and _emoji else "🎬",
                        }
                    # Drop the image URL line out of the TEXT (it's sent as a
                    # photo) so the reply doesn't carry a bare dead link.
                    if action_image_url and _d:
                        _d = "\n".join(
                            ln for ln in str(_d).split("\n")
                            if ln.strip() != action_image_url
                        ).strip()
                    action_outcome_line = (
                        f"\n\n✅ Ran <b>{_listener()._escape(sk_id)}</b> on "
                        f"<code>{_listener()._escape(sk_host)}</code>"
                        # _d is NOT pre-escaped here: the final
                        # _telegram_safe_escape pass (further down, which PRESERVES
                        # the framing <b>/<code> tags) escapes it exactly once.
                        # Pre-escaping would DOUBLE-escape the detail's content —
                        # a skill detail's literal "<name>" reached the wire as
                        # "&lt;name&gt;" (the & of &lt; got re-escaped to &amp;).
                        + (f" — {str(_d)}" if _d else "")
                    )
                else:
                    _d = (sk_result or {}).get("detail") or "failed"
                    action_outcome_line = (
                        f"\n\n❌ Skill <b>{_listener()._escape(sk_id)}</b> failed: "
                        # _d NOT pre-escaped — _telegram_safe_escape handles it
                        # once (preserving the <code> wrapper); see the success
                        # branch above for the double-escape rationale.
                        f"<code>{str(_d)}</code>"
                    )
                # Audit-trail parity with the web skill route + the
                # Telegram slash-command dispatch — an AI-initiated app
                # skill (the user asked the bot in free text, the model
                # emitted a skill action) is a state mutation that MUST
                # land in History. Distinct `telegram-ai:` actor so an
                # AI-initiated run is forensically separable from a direct
                # /command run. Best-effort: never break the reply.
                _sk_ok = bool(isinstance(sk_result, dict) and sk_result.get("ok"))
                # noinspection PyBroadException
                try:
                    from logic.db import db_conn as _db_conn
                    from logic.ops import write_admin_audit as _write_admin_audit
                    with _db_conn() as _c:
                        _write_admin_audit(
                            _c, "services_skill",
                            target_kind="host", target_name=sk_host, target_id=sk_host,
                            actor=(f"telegram-ai:{omnigrid_username}" if omnigrid_username else "telegram-ai"),
                            status=("success" if _sk_ok else "error"),
                            message=(f"AI-dispatched skill '{sk_id}' on {sk_host} "
                                     f"(service_idx={sk_idx}, ok={_sk_ok})"),
                        )
                except Exception:  # noqa: BLE001
                    pass
    # noinspection PyBroadException
    except Exception as _act_err:  # noqa: BLE001
        print(f"[telegram_listener] ai action dispatch failed: {_act_err}")
    # ------------------------------------------------------------------

    clean = _strip_ai_directives(raw_text)
    if not clean:
        _record_call(True, result, "")
        await _deliver("<i>(empty AI response)</i>")
        return
    # Append the action-outcome line (if any) to the conversational
    # reply so the operator sees BOTH the AI's natural-language framing
    # AND the actual dispatch result in one bubble.
    if action_outcome_line:
        clean += action_outcome_line
    # Record BEFORE truncation so the persisted answer matches what
    # the model actually returned (truncation is purely a Telegram
    # wire-limit accommodation, not the canonical record).
    _record_call(True, result, clean)
    # Pre-truncate the RAW text BEFORE the Markdown→HTML conversion.
    # Order rationale: the converter inflates the string by ~30-50% in
    # code-heavy responses (`**bold**` → `<b>bold</b>` adds 5 chars per
    # pair; triple-backtick fences add a `<code>` / `</code>` wrap).
    # Slicing AFTER conversion meant a `\n\n<i>…(truncated)</i>` marker
    # had to fight an already-inflated post-conversion string, AND the
    # tag-aware truncator was occasionally backing off past content
    # that would have survived the inflation. Slicing BEFORE conversion
    # at a generous raw-char budget gives the converter the headroom
    # to grow safely and the safer cut point (raw text has no tags).
    raw_max_chars = 2800  # raw budget — leaves ~1300 chars for tag inflation
    if len(clean) > raw_max_chars:
        clean = clean[:raw_max_chars].rstrip() + "\n\n…(truncated)"
    # Defence-in-depth Markdown→HTML rescue. The Telegram-surface
    # system prompt tells the AI to use HTML tags, but models
    # occasionally leak `**bold**` / `## Header` / `` `code` `` /
    # triple-backtick fences anyway. Run the converter so common
    # Markdown patterns become real Telegram-HTML tags BEFORE the
    # escape pass; the escape pass then preserves recognised tags
    # while neutralising any other stray `<...>` markup.
    clean = _markdown_to_telegram_html(clean)
    # Defensive second-pass cap — Telegram itself caps a single message
    # at 4096 chars including HTML tags. The raw-budget cap above plus
    # the converter's typical inflation should keep us well under, but
    # a pathological code-fence-only reply could still cross 4096; the
    # tag-aware `_truncate_telegram_html` backs off to the last safe
    # boundary AND closes any open tags so the marker bubble stays
    # well-formed if it does.
    max_chars = 3800  # leave headroom for HTML overhead
    if len(clean) > max_chars:
        clean = _truncate_telegram_html(clean, max_chars) + "\n\n<i>…(truncated)</i>"
    # Telegram-safe escape preserves the AI's intentional HTML tags
    # (the system prompt instructs HTML, and the Markdown converter
    # just produced more) while escaping every other `<` / `>` so
    # the parser doesn't HTTP-400 the message. `_listener()._escape` would have
    # escaped EVERY tag, killing all formatting.
    # `_make_commands_clickable` runs LAST: it unwraps single-command
    # `<code>/cmd</code>` spans so Telegram linkifies them as tappable
    # commands (a `code` entity suppresses command auto-detection).
    await _deliver(_make_commands_clickable(_telegram_safe_escape(clean)))
    # If the reply references a Speedtest result share image, send it as a
    # PHOTO so it actually displays — text replies set
    # disable_web_page_preview, so a bare URL would only render as a link.
    # Scan the RAW model text AND the appended action-outcome line (the
    # latest_speedtest skill puts the image URL there, not in the model text),
    # since either can carry the URL and `clean` may have truncated it.
    # Fire-and-forget; a photo failure never affects the delivered text reply.
    try:
        # Build the inline-keyboard button for a skill follow-up (e.g. Seerr
        # suggest → "Request on Seerr"). The full action is stashed
        # server-side under a short token (callback_data is 64-byte capped);
        # the callback handler in telegram_listener pops it + dispatches.
        _reply_markup = None
        # Hoisted defaults so the fallback branch below always has them bound
        # (the type checker can't see that `_reply_markup` truthiness implies
        # `action_followup` was truthy).
        _fu_emoji = "🎬"
        _fu_label = ""
        if action_followup:
            _token = _listener().register_pending_action({
                "host_id": action_followup["host_id"],
                "service_idx": action_followup["service_idx"],
                "skill_id": action_followup["skill_id"],
                "arg": action_followup["arg"],
            })
            _fu_emoji = action_followup.get("emoji") or "🎬"
            _fu_label = str(action_followup.get("label") or "")
            _reply_markup = {"inline_keyboard": [[{
                # Telegram inline buttons can't be colour-styled via the Bot
                # API, so a leading emoji makes the button stand out.
                "text": _fu_emoji + " " + _fu_label[:116],
                "callback_data": "ssr:" + _token,
            }]]}
        # A skill that returned an explicit image_url (e.g. Seerr's
        # suggest-a-movie poster) is sent directly — with the button attached
        # when present. Otherwise fall back to the speedtest result-image
        # regex over the reply text.
        if action_image_url:
            await _listener()._send_photo(client, action_image_url,
                                          reply_markup=_reply_markup)
        elif _reply_markup:
            # No poster but we DO have a follow-up — send the button on a
            # minimal prompt so the operator can still tap to act.
            await _listener()._send_reply(
                client, _fu_emoji + " " + _listener()._escape(_fu_label),
                reply_markup=_reply_markup)
        else:
            import re as _re_img
            m = _re_img.search(
                r"https?://[^\s<>\"']*speedtest\.net/result/[^\s<>\"']*\.png",
                raw_text + "\n" + (action_outcome_line or ""),
            )
            if m:
                await _listener()._send_photo(client, m.group())
    # noinspection PyBroadException
    except Exception as _photo_err:  # noqa: BLE001
        print(f"[telegram_listener] skill photo send skipped: {_photo_err}")
