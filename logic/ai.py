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
