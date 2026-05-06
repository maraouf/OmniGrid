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
