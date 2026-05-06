"""AI integration helpers — Stage 1 foundation.

Stage 1 ships ONLY the per-provider test probe used by the Admin → AI
tab's "Test connection" button. The actual call wrapper that Stage 2+
will use to record into ``ai_jobs`` is NOT in this module yet — it
lands in a follow-up that we'll build once the contract is settled.

Auth model reconnaissance (per the CLAUDE.md provider-checklist rule):

  Claude  — Anthropic API key in `x-api-key` header + ``anthropic-version``
            constant. Default endpoint: https://api.anthropic.com.
  Gemini  — API key in the URL query string (``?key=<key>``) or in
            ``x-goog-api-key`` header (we use the header — keeps the
            URL clean in logs, mirrors the SDK's behaviour). Default
            endpoint: https://generativelanguage.googleapis.com.
  ChatGPT — OpenAI Bearer token in ``Authorization`` header. Default
            endpoint: https://api.openai.com.
  DeepSeek — OpenAI-compatible API; same Bearer-token shape. Default
             endpoint: https://api.deepseek.com.

The test probe sends a one-token "ping" to verify the auth + model id
are valid. We deliberately use ``max_tokens=1`` (or the provider
equivalent) so the test is cheap; a well-formed 200 response is the
success signal regardless of generated content. Any 4xx with auth-
specific detail is surfaced verbatim so admins can fix typos directly
from the toast.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx


# Canonical, ordered tuple of every AI provider OmniGrid speaks to.
# CANONICAL source of truth — every other module that needs the list
# (settings validators, /api/me's `client_config.ai.provider_names`,
# the SPA's `aiProviderNames`) imports from HERE rather than declaring
# a parallel literal. Adding a fifth provider is a one-line edit to
# this tuple plus per-provider plumbing in `_DEFAULT_BASE_URLS` /
# `_DEFAULT_MODELS` / probe + ask helpers below; consumers pick up
# the new entry automatically.
SUPPORTED_PROVIDERS = ("claude", "gemini", "chatgpt", "deepseek")


_DEFAULT_BASE_URLS = {
    "claude":   "https://api.anthropic.com",
    "gemini":   "https://generativelanguage.googleapis.com",
    "chatgpt":  "https://api.openai.com",
    "deepseek": "https://api.deepseek.com",
}

_DEFAULT_MODELS = {
    "claude":   "claude-opus-4-7",
    "gemini":   "gemini-2.5-pro",
    "chatgpt":  "gpt-4o",
    "deepseek": "deepseek-chat",
}


def _resolve_endpoint(provider: str, base_url: str | None) -> str:
    """Strip trailing slashes; fall back to canonical default if empty."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = _DEFAULT_BASE_URLS.get(provider, "")
    return base


async def _probe_claude(api_key: str, model: str, base_url: str, timeout: float) -> dict:
    base = _resolve_endpoint("claude", base_url)
    url = f"{base}/v1/messages"
    headers = {
        "x-api-key":         api_key,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    body = {
        "model":      model or _DEFAULT_MODELS["claude"],
        "max_tokens": 1,
        "messages":   [{"role": "user", "content": "ping"}],
    }
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, headers=headers, json=body)
    return _interpret_http(r, "claude")


async def _probe_gemini(api_key: str, model: str, base_url: str, timeout: float) -> dict:
    base = _resolve_endpoint("gemini", base_url)
    mdl = model or _DEFAULT_MODELS["gemini"]
    url = f"{base}/v1beta/models/{mdl}:generateContent"
    # Gemini accepts the key either in ``?key=`` OR `x-goog-api-key`.
    # Header form is preferred — keeps the URL out of logs.
    headers = {
        "x-goog-api-key": api_key,
        "content-type":   "application/json",
    }
    body = {
        "contents":          [{"parts": [{"text": "ping"}]}],
        "generationConfig":  {"maxOutputTokens": 1},
    }
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, headers=headers, json=body)
    return _interpret_http(r, "gemini")


async def _probe_openai_compatible(provider: str, api_key: str, model: str,
                                    base_url: str, timeout: float) -> dict:
    """Shared probe for OpenAI-shaped APIs (chatgpt + deepseek)."""
    base = _resolve_endpoint(provider, base_url)
    url = f"{base}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "content-type":  "application/json",
    }
    body = {
        "model":      model or _DEFAULT_MODELS.get(provider, ""),
        "messages":   [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, headers=headers, json=body)
    return _interpret_http(r, provider)


def _interpret_http(r: httpx.Response, provider: str) -> dict:
    """Translate one provider's HTTP response into ``{ok, detail}``.

    Status 200 → success; non-200 → ``ok=False`` with the body's
    ``error.message`` (or whatever the provider's standard shape is)
    surfaced verbatim. We deliberately don't try to normalise error
    codes across providers — admins want to see the actual upstream
    message so they can paste it into a doc / search.
    """
    if r.status_code == 200:
        return {"ok": True, "status": r.status_code, "detail": "OK"}
    detail = ""
    try:
        body = r.json()
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                detail = str(err.get("message") or err.get("detail") or err.get("code") or "")
            elif isinstance(err, str):
                detail = err
            if not detail:
                detail = str(body.get("message") or body.get("detail") or "")
    except (ValueError, json.JSONDecodeError):
        # Non-JSON response (rare — mostly when a misconfigured proxy
        # answers HTML). Truncate to keep the toast readable.
        detail = (r.text or "")[:300]
    if not detail:
        detail = f"HTTP {r.status_code}"
    return {"ok": False, "status": r.status_code, "detail": detail, "provider": provider}


async def test_provider(
    provider: str,
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 15.0,
) -> dict:
    """Single entry-point used by `/api/admin/ai/{provider}/test`.

    Validates the inputs, dispatches to the per-provider probe, and
    returns ``{ok, status, detail, response_time_ms, provider}``. Any
    network / library error is caught and reported as ``ok=False``
    with the exception text in ``detail`` — the SPA renders it inline.

    The caller is responsible for pulling the API key out of the saved
    settings (see CLAUDE.md's keep-current-if-blank contract) — this
    function takes the cleartext key as an argument.
    """
    p = (provider or "").strip().lower()
    if p not in SUPPORTED_PROVIDERS:
        return {
            "ok": False, "status": 0, "provider": p,
            "detail": f"Unsupported provider: {provider}",
            "response_time_ms": 0,
        }
    if not (api_key or "").strip():
        return {
            "ok": False, "status": 0, "provider": p,
            "detail": "API key is not set. Save a key first or paste one into the form before testing.",
            "response_time_ms": 0,
        }

    started = time.time()
    try:
        if p == "claude":
            out = await _probe_claude(api_key, model or "", base_url or "", timeout)
        elif p == "gemini":
            out = await _probe_gemini(api_key, model or "", base_url or "", timeout)
        else:
            # chatgpt + deepseek share the OpenAI-compatible shape.
            out = await _probe_openai_compatible(p, api_key, model or "", base_url or "", timeout)
    except httpx.TimeoutException:
        out = {"ok": False, "status": 0,
               "detail": f"Probe timed out after {timeout:.0f}s — the provider's API may be slow or unreachable from here.",
               "provider": p}
    except httpx.RequestError as e:
        out = {"ok": False, "status": 0,
               "detail": f"Network error: {type(e).__name__}: {e}",
               "provider": p}
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "status": 0,
               "detail": f"{type(e).__name__}: {e}",
               "provider": p}

    out.setdefault("provider", p)
    out["response_time_ms"] = int((time.time() - started) * 1000)
    return out


# ----------------------------------------------------------------------
# Chat completion — full conversational request, used by surfaces that
# need a real model response (not just a one-token health probe).
# Currently the Cmd-K palette's "Ask AI" row dispatches here. Same
# four-provider matrix as ``test_provider``; same response shape
# (`{ok, text, detail, response_time_ms, provider, model, tokens}`).
# Errors flow through `_interpret_http` so the operator sees the raw
# upstream message (rate-limit / quota / auth) verbatim.
# ----------------------------------------------------------------------


async def _chat_claude(api_key: str, model: str, base_url: str,
                        prompt: str, system_prompt: str, max_tokens: int,
                        timeout: float) -> dict:
    base = _resolve_endpoint("claude", base_url)
    url = f"{base}/v1/messages"
    headers = {
        "x-api-key":         api_key,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    body: dict = {
        "model":      model or _DEFAULT_MODELS["claude"],
        "max_tokens": max_tokens,
        "messages":   [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        body["system"] = system_prompt
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, headers=headers, json=body)
    if r.status_code != 200:
        return _interpret_http(r, "claude")
    try:
        j = r.json()
        # Claude responds with content as a list of blocks.
        blocks = j.get("content") or []
        text_parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        text = "".join(text_parts).strip()
        usage = j.get("usage") or {}
        return {
            "ok": True, "status": 200, "text": text,
            "tokens": {"prompt": int(usage.get("input_tokens", 0)),
                       "completion": int(usage.get("output_tokens", 0))},
            "model": j.get("model") or model,
        }
    except (ValueError, json.JSONDecodeError) as e:
        return {"ok": False, "status": r.status_code,
                "detail": f"claude response parse error: {e}", "provider": "claude"}


async def _chat_gemini(api_key: str, model: str, base_url: str,
                        prompt: str, system_prompt: str, max_tokens: int,
                        timeout: float) -> dict:
    base = _resolve_endpoint("gemini", base_url)
    mdl = model or _DEFAULT_MODELS["gemini"]
    url = f"{base}/v1beta/models/{mdl}:generateContent"
    headers = {
        "x-goog-api-key": api_key,
        "content-type":   "application/json",
    }
    # Gemini 2.5 model family: Flash + Lite accept `thinkingBudget: 0`
    # (disables thinking, gives the operator a fast palette response).
    # Pro REJECTS budget=0 with HTTP 400 — the API enforces a minimum
    # positive budget for Pro because the model only operates in
    # thinking mode. So gate the budget by model:
    #   * `2.5-pro`: omit thinkingConfig entirely (model picks budget)
    #   * any other 2.5: budget=0 (skip thinking, fast cheap response)
    #   * pre-2.5: omit (no thinking config in older API revs)
    # If thinking eats the entire `max_tokens` budget, the operator can
    # bump it via Admin → AI Integration's max_tokens field.
    mdl_lc = (mdl or "").lower()
    gen_config: dict = {"maxOutputTokens": max_tokens}
    if "2.5" in mdl_lc and "pro" not in mdl_lc:
        gen_config["thinkingConfig"] = {"thinkingBudget": 0}
    body: dict = {
        "contents":         [{"parts": [{"text": prompt}]}],
        "generationConfig": gen_config,
    }
    if system_prompt:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, headers=headers, json=body)
    if r.status_code != 200:
        return _interpret_http(r, "gemini")
    try:
        j = r.json()
        candidates = j.get("candidates") or []
        text = ""
        finish_reason = ""
        if candidates:
            cand0 = candidates[0] or {}
            finish_reason = (cand0.get("finishReason") or "").upper()
            parts = (cand0.get("content") or {}).get("parts") or []
            # Skip parts marked as `thought` (Gemini 2.5+ thinking
            # blocks); keep only text parts the model meant to expose.
            text = "".join(
                p.get("text", "")
                for p in parts
                if isinstance(p, dict) and not p.get("thought")
            ).strip()
        usage = j.get("usageMetadata") or {}
        # When the model produced nothing visible AND finish_reason
        # signals a budget exhaustion, surface that fact verbatim so
        # the operator knows to bump max_tokens or pick a non-thinking
        # model — much better than the silent "(empty response)" they
        # were seeing pre-fix.
        if not text:
            if finish_reason in ("MAX_TOKENS", "STOP_SEQUENCE"):
                detail = (f"Empty response — Gemini hit {finish_reason} "
                          f"after {int(usage.get('candidatesTokenCount', 0))} "
                          f"output tokens. Try a shorter prompt or a "
                          f"non-thinking model (gemini-1.5-flash / "
                          f"gemini-2.0-flash).")
                return {"ok": False, "status": r.status_code,
                        "detail": detail, "provider": "gemini",
                        "finish_reason": finish_reason}
        return {
            "ok": True, "status": 200, "text": text,
            "tokens": {"prompt": int(usage.get("promptTokenCount", 0)),
                       "completion": int(usage.get("candidatesTokenCount", 0))},
            "model":  mdl,
            "finish_reason": finish_reason,
        }
    except (ValueError, json.JSONDecodeError) as e:
        return {"ok": False, "status": r.status_code,
                "detail": f"gemini response parse error: {e}", "provider": "gemini"}


async def _chat_openai_compatible(provider: str, api_key: str, model: str,
                                   base_url: str, prompt: str, system_prompt: str,
                                   max_tokens: int, timeout: float) -> dict:
    base = _resolve_endpoint(provider, base_url)
    url = f"{base}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "content-type":  "application/json",
    }
    messages: list = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model":      model or _DEFAULT_MODELS.get(provider, ""),
        "messages":   messages,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, headers=headers, json=body)
    if r.status_code != 200:
        return _interpret_http(r, provider)
    try:
        j = r.json()
        choices = j.get("choices") or []
        text = ""
        if choices:
            msg = (choices[0] or {}).get("message") or {}
            text = (msg.get("content") or "").strip()
        usage = j.get("usage") or {}
        return {
            "ok": True, "status": 200, "text": text,
            "tokens": {"prompt": int(usage.get("prompt_tokens", 0)),
                       "completion": int(usage.get("completion_tokens", 0))},
            "model": j.get("model") or model,
        }
    except (ValueError, json.JSONDecodeError) as e:
        return {"ok": False, "status": r.status_code,
                "detail": f"{provider} response parse error: {e}", "provider": provider}


async def ask_provider(
    provider: str,
    *,
    api_key: str,
    prompt: str,
    system_prompt: str = "",
    model: str | None = None,
    base_url: str | None = None,
    max_tokens: int = 512,
    timeout: float = 30.0,
) -> dict:
    """Ask one provider for a chat completion. Returns
    ``{ok, text, detail, response_time_ms, provider, model, tokens}``.

    Sibling of :func:`test_provider` — same dispatch matrix, but issues
    a real conversational request rather than a one-token ping. Used
    by the Cmd-K palette's "Ask AI" surface and any future feature
    that needs a model response. Preserves the keep-current-if-blank
    secret contract — the caller resolves the saved API key.
    """
    p = (provider or "").strip().lower()
    if p not in SUPPORTED_PROVIDERS:
        return {"ok": False, "status": 0, "provider": p,
                "detail": f"Unsupported provider: {provider}",
                "response_time_ms": 0}
    if not (api_key or "").strip():
        return {"ok": False, "status": 0, "provider": p,
                "detail": "API key is not set. Configure it in Admin → AI Integration first.",
                "response_time_ms": 0}
    if not (prompt or "").strip():
        return {"ok": False, "status": 0, "provider": p,
                "detail": "prompt is required", "response_time_ms": 0}

    started = time.time()
    try:
        if p == "claude":
            out = await _chat_claude(api_key, model or "", base_url or "",
                                     prompt, system_prompt, max_tokens, timeout)
        elif p == "gemini":
            out = await _chat_gemini(api_key, model or "", base_url or "",
                                     prompt, system_prompt, max_tokens, timeout)
        else:
            out = await _chat_openai_compatible(p, api_key, model or "", base_url or "",
                                                prompt, system_prompt, max_tokens, timeout)
    except httpx.TimeoutException:
        out = {"ok": False, "status": 0,
               "detail": f"Request timed out after {timeout:.0f}s — the provider's API may be slow.",
               "provider": p}
    except httpx.RequestError as e:
        out = {"ok": False, "status": 0,
               "detail": f"Network error: {type(e).__name__}: {e}",
               "provider": p}
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "status": 0,
               "detail": f"{type(e).__name__}: {e}",
               "provider": p}
    out.setdefault("provider", p)
    out["response_time_ms"] = int((time.time() - started) * 1000)
    return out


# ---------------------------------------------------------------------------
# Cmd-K palette assistant (Phase 1 + Phase 2) — system prompts, allowed
# action whitelist, parsers, user-prompt builders, recorder helper.
#
# Pulled out of `main.py` so the route handlers stay thin orchestration
# (auth → settings → call provider → record). All behaviour-shaping
# strings + parsing rules live here so a future agent updating the AI
# tone / DSL grammar / action list edits ONE module instead of hunting
# them across the FastAPI routes file.
# ---------------------------------------------------------------------------

ALLOWED_PALETTE_ACTIONS: frozenset[str] = frozenset({
    "mark_all_notifications_read",
    "refresh",
    "reload",
    "theme_dark",
    "theme_light",
    "theme_auto",
    "open_notifications",
    "show_hotkeys",
    "cleanup_stopped",
    "sign_out",
})


PALETTE_SYSTEM_PROMPT: str = (
    "You are the Cmd-K palette assistant for OmniGrid, a Docker-Swarm "
    "management dashboard. The operator just typed a query into the "
    "command palette and wants help navigating, diagnosing, or acting. "
    "\n\n"
    "ANSWER WITH DATA WHEN YOU HAVE IT. The user_prompt below carries "
    "JSON records for every visible host (id, label, status, cpu_pct, "
    "mem_pct, disk_pct, disk_free_gb, disk_total_gb, uptime_s, paused, "
    "providers) and every visible item (name, status, health, type, "
    "replicas, desired, update_available). When the operator asks a "
    "DATA question (\"which hosts are running out of space soon?\", "
    "\"top 5 hosts by CPU\", \"any services degraded?\", \"what's "
    "stopped?\", \"any updates pending?\"), DON'T point them at a UI "
    "column to sort — RANK / COUNT / AGGREGATE the records yourself "
    "and reply with a short list of the top 3-5 specifics including "
    "the actual numbers. Example shape:\n"
    "  Top 3 hosts low on disk:\n"
    "  1. nas01 — 92% used (8 GB free of 100 GB)\n"
    "  2. web03 — 87% used (52 GB free of 400 GB)\n"
    "  3. dockerpve — 76% used (240 GB free of 1.0 TB)\n"
    "Use the EXACT id/label from the JSON. When the data shows nothing "
    "of concern, say so explicitly (e.g. \"no host above 80% disk — "
    "you're fine\").\n\n"
    "When the question is HOW-TO (\"how do I update a stack?\"), name "
    "the exact OmniGrid surface ('Admin → Hosts', 'host drawer', 'item "
    "drawer', 'Stacks view') and the exact button or action ('click "
    "Switch to :latest', 'click Resume sampling'). Don't invent "
    "features that aren't real. Don't tell the operator to POST to "
    "API endpoints — they're using the UI; pick an ACTION below if "
    "one matches. If the operator's query is ambiguous, ask one "
    "short clarifying question. Reply concisely — bullet list with "
    "the data is fine; no markdown headers.\n\n"
    "ACTION PROTOCOL — CRITICAL. When the operator's query is a "
    "REQUEST to do something that matches one of the actions "
    "below, you MUST end your reply with a SINGLE line "
    "`ACTION: <id>` and nothing after it. The reply text BEFORE "
    "the ACTION line should be a short one-liner confirmation "
    "(\"Opening notifications.\", \"Switching to dark theme.\", "
    "\"I'll mark every notification as read for you.\"). The SPA "
    "parses the ACTION line, strips it from the visible text, "
    "and INVOKES the action. Without the ACTION line, the action "
    "DOES NOT FIRE — operators see the prose but nothing happens. "
    "Bias toward emitting an ACTION when the query is an "
    "imperative verb (open / show / refresh / cleanup / sign out / "
    "switch / mark) targeting one of the listed action ids.\n\n"
    "AVAILABLE ACTIONS (end reply with `ACTION: <id>` for each):\n"
    " - mark_all_notifications_read — mark every notification as read\n"
    " - refresh — refresh the current view's data from the backend\n"
    " - reload — full SPA reload (Ctrl-R equivalent)\n"
    " - theme_dark — switch UI to dark theme\n"
    " - theme_light — switch UI to light theme\n"
    " - theme_auto — let UI follow OS theme\n"
    " - open_notifications — open the notifications drawer\n"
    " - show_hotkeys — show the keyboard-shortcuts modal\n"
    " - cleanup_stopped — remove every stopped / failed / orphaned container the dashboard can see. Operator-friendly synonyms: 'cleanup', 'clean up', 'purge', 'prune', 'remove stopped containers'. (Destructive — the SPA still confirms before issuing the rm batch, so picking this is safe.)\n"
    " - sign_out — log out of OmniGrid\n"
    "Example reply: 'I'll mark every notification as read for you.\\n"
    "ACTION: mark_all_notifications_read'\n"
    "If no action fits, omit the ACTION line entirely."
)


HOST_FILTER_SYSTEM_PROMPT: str = (
    "You translate operator natural-language queries into a "
    "structured filter DSL for OmniGrid's bulk Cmd-K palette. "
    "Your job is ONE thing: emit a DSL string that the operator "
    "will then review (with a chip-strip preview) and confirm. "
    "You DO NOT invoke any action — you only propose the filter.\n\n"
    "DSL grammar (case-insensitive):\n"
    "  <verb>: <token1> [<token2> ...]\n"
    "  verb     := pause | resume\n"
    "  token    := wildcard | provider:<name> | status:<value> | paused\n"
    "  wildcard := substring match on host id OR label (no glob "
    "syntax beyond bare-text contains; `web` matches `web01` / "
    "`my-web-server` / `WebDB`).\n\n"
    "Provider names: beszel, pulse, node_exporter, webmin, ping, snmp\n"
    "Status values: up, down, paused, unknown\n\n"
    "OUTPUT FORMAT (strict):\n"
    "  Line 1: the DSL string, starting with `pause:` or `resume:`\n"
    "  Line 2: one short sentence explaining what the filter matches\n"
    "  Output nothing else — no markdown, no preamble, no code fences.\n\n"
    "Examples:\n"
    "  Operator: \"pause every host that's down\"\n"
    "    → pause: status:down\n"
    "      Pauses sampling on every host whose status is down.\n"
    "  Operator: \"resume the beszel hosts that are paused\"\n"
    "    → resume: provider:beszel paused\n"
    "      Resumes every Beszel-monitored host that is currently paused.\n"
    "  Operator: \"pause every web host with low disk\"\n"
    "    → pause: web\n"
    "      Pauses every host whose id or label contains 'web'. "
    "(Disk-percent thresholds aren't part of the Phase 1 DSL — "
    "the operator can refine the chip strip manually.)\n\n"
    "If the request can't be expressed in the DSL (e.g. \"pause "
    "every host using more than 50% CPU\" — there's no numeric "
    "threshold token), reply with `ERROR: <one-line reason>` "
    "instead. The SPA surfaces the reason inline."
)


def parse_palette_action(text: str) -> tuple[str, str]:
    """Extract the optional `ACTION: <id>` trailer from a palette
    response. Returns ``(action_id, cleaned_text)`` — empty
    `action_id` when no whitelisted action found; `cleaned_text` is
    the visible body with the ACTION line stripped.

    Forgiving: matches the line at the strict end of the body OR
    anywhere-mid-body, with optional surrounding whitespace,
    backticks, asterisks, or trailing punctuation.
    """
    if not text:
        return "", text or ""
    import re as _re
    m = _re.search(
        r"(?:^|\n)[\s`*]*ACTION\s*:\s*([a-z_]+)\b[\s`.*]*$",
        text, _re.IGNORECASE | _re.MULTILINE,
    )
    if not m:
        m = _re.search(
            r"(?:^|\n)[\s`*]*ACTION\s*:\s*([a-z_]+)",
            text, _re.IGNORECASE | _re.MULTILINE,
        )
    if not m:
        return "", text
    candidate = m.group(1).strip().lower()
    if candidate not in ALLOWED_PALETTE_ACTIONS:
        return "", text
    cleaned = text[: m.start()].rstrip()
    return candidate, cleaned


def parse_host_filter_response(text: str) -> tuple[str, str, str]:
    """Parse the host-filter model response. Returns
    ``(dsl, explanation, error)`` — empty `dsl` means the response
    was invalid; `error` carries a one-line reason for the SPA toast.

    Validates against the Phase 1 grammar (`pause:` / `resume:`).
    Strips markdown fences the model might add despite instructions
    to the contrary.
    """
    if not text:
        return "", "", "Model returned an empty response."
    cleaned = text.strip().strip("`").strip()
    if cleaned.lower().startswith("error:"):
        msg = cleaned[6:].strip() or "AI couldn't translate that into a Phase 1 DSL filter."
        return "", "", msg
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if not lines:
        return "", "", "Model returned an empty response."
    cand = lines[0]
    import re as _re
    m = _re.match(r"^(pause|resume)\s*:\s*(.*)$", cand, _re.IGNORECASE)
    if not m:
        return "", "", f"Model didn't return a valid DSL line — got: {cand[:120]}"
    dsl = f"{m.group(1).lower()}: {m.group(2).strip()}".rstrip()
    explanation = lines[1] if len(lines) > 1 else ""
    return dsl, explanation, ""


def _format_records_block(label: str, fields: str, records: list) -> str:
    """Helper — turn a list of host/item records into a JSON-lines
    block prefixed with a one-line schema description. Falls back to
    the legacy bare-string CSV when the SPA sends an older payload
    (every entry is a string)."""
    if not records:
        return ""
    if all(isinstance(r, str) for r in records):
        return f"{label}: " + ", ".join(records)
    import json as _json
    body = "\n".join(_json.dumps(r, separators=(",", ":")) for r in records)
    return f"{label} (one JSON record per line, fields: {fields}):\n{body}"


def build_palette_user_prompt(query: str, ctx: dict | None) -> str:
    """Per-call user prompt for `/api/ai/palette`. Caps host + item
    lists at 30 each (~3k tokens for a fully-populated 30-host fleet).
    """
    parts: list[str] = [f"Operator query: {query}"]
    if isinstance(ctx, dict):
        view = ctx.get("view")
        if view:
            parts.append(f"Current view: {view}")
        hosts = ctx.get("hosts") if isinstance(ctx.get("hosts"), list) else None
        if hosts:
            parts.append(_format_records_block(
                "Available hosts",
                "id, label, status, cpu_pct, mem_pct, disk_pct, "
                "disk_free_gb, disk_total_gb, uptime_s, paused, providers",
                hosts[:30],
            ))
        items = ctx.get("items") if isinstance(ctx.get("items"), list) else None
        if items:
            parts.append(_format_records_block(
                "Available items",
                "name, status, health, type, replicas, desired, "
                "update_available",
                items[:30],
            ))
    return "\n".join(p for p in parts if p)


def build_host_filter_user_prompt(query: str, ctx: dict | None) -> str:
    """User prompt for `/api/ai/host-filter` — same structured-host
    context as the palette path but without items (host-filter only
    operates on hosts in Phase 2)."""
    parts: list[str] = [f"Operator query: {query}"]
    if isinstance(ctx, dict):
        hosts = ctx.get("hosts") if isinstance(ctx.get("hosts"), list) else None
        if hosts:
            parts.append(_format_records_block(
                "Available hosts",
                "id, label, status, cpu_pct, mem_pct, disk_pct, "
                "disk_free_gb, disk_total_gb, uptime_s, paused, providers",
                hosts[:30],
            ))
    return "\n".join(parts)


def record_ai_call(
    *,
    db_conn_factory,
    provider: str,
    model: str,
    kind: str,
    ok: bool,
    response_time_ms: int,
    tokens: dict | None,
    error_detail: str | None,
    history_actor: str,
    history_target_kind: str = "ai",
    history_events: dict | None = None,
) -> None:
    """Best-effort write of a single AI call into both `ai_jobs`
    (dashboard tiles) AND `history` (History tab). Failures are
    swallowed and logged — the operator already got their answer.

    `db_conn_factory` is `logic.db.db_conn` injected by the caller so
    this module stays decoupled from the wider import graph (db.py
    pulls tuning.py which pulls env-loading); a future per-provider
    plugin or test path can pass a mock connection factory.
    """
    import json as _json
    import time as _time
    try:
        prompt_t = int((tokens or {}).get("prompt") or 0)
        completion_t = int((tokens or {}).get("completion") or 0)
        total_t = prompt_t + completion_t
        now_ts = int(_time.time())
        with db_conn_factory() as c:
            c.execute(
                "INSERT INTO ai_jobs ("
                "  ts, provider, model, kind, status,"
                "  prompt_tokens, completion_tokens, total_tokens,"
                "  cost_usd, response_time_ms, error, metadata"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    now_ts, provider, model or "", kind,
                    "success" if ok else "error",
                    prompt_t, completion_t, total_t,
                    None,  # cost not yet computed — provider rate-card
                           # plumbing is a follow-up.
                    int(response_time_ms or 0),
                    error_detail or None,
                    None,
                ),
            )
            events_payload = dict(history_events or {})
            events_payload.setdefault("tokens", {
                "prompt": prompt_t,
                "completion": completion_t,
                "total": total_t,
            })
            try:
                events_json = _json.dumps(events_payload, ensure_ascii=False)
            except (TypeError, ValueError):
                events_json = "{}"
            c.execute(
                "INSERT INTO history ("
                "  ts, op_type, target_kind, target_name, target_id,"
                "  status, duration, events, error, actor"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    float(now_ts),
                    f"ai_{kind}",
                    history_target_kind,
                    provider,
                    model or "",
                    "success" if ok else "error",
                    (int(response_time_ms or 0) / 1000.0),
                    events_json,
                    error_detail or None,
                    history_actor or "ui",
                ),
            )
            c.commit()
    except Exception as e:  # noqa: BLE001
        print(f"[ai] record_ai_call({kind}) failed: {e}")
