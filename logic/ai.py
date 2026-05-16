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

import asyncio
import json
import time
from typing import Optional, TypeVar

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
    "claude": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "chatgpt": "https://api.openai.com",
    "deepseek": "https://api.deepseek.com",
}

_DEFAULT_MODELS = {
    "claude": "claude-opus-4-7",
    "gemini": "gemini-2.5-pro",
    "chatgpt": "gpt-4o",
    "deepseek": "deepseek-chat",
}

# Per-(provider, model-prefix) USD rates per 1,000,000 tokens.
# Prefix-matched longest-first so a versioned model id like
# `claude-opus-4-7-20260101` still resolves to the base entry.
# Numbers are public list rates as of 2026-Q2; operators on enterprise
# / volume agreements should treat the resulting cost as an upper bound
# rather than ground truth. When rates rotate, edit this table; existing
# `ai_jobs.cost_usd` rows are historical (computed at insert time) and
# do NOT retroactively update.
RATE_CARD: dict[tuple[str, str], tuple[float, float]] = {
    # Anthropic Claude — opus / sonnet / haiku
    ("claude", "claude-opus-4-7"): (15.00, 75.00),
    ("claude", "claude-opus-4"): (15.00, 75.00),
    ("claude", "claude-opus"): (15.00, 75.00),
    ("claude", "claude-sonnet-4-6"): (3.00, 15.00),
    ("claude", "claude-sonnet-4"): (3.00, 15.00),
    ("claude", "claude-sonnet"): (3.00, 15.00),
    ("claude", "claude-haiku-4-5"): (1.00, 5.00),
    ("claude", "claude-haiku-4"): (1.00, 5.00),
    ("claude", "claude-haiku"): (1.00, 5.00),
    # Google Gemini — 2.5 family pricing tiers
    ("gemini", "gemini-2.5-pro"): (1.25, 10.00),
    ("gemini", "gemini-2.5-flash-lite"): (0.0375, 0.15),
    ("gemini", "gemini-2.5-flash"): (0.075, 0.30),
    ("gemini", "gemini-1.5-pro"): (1.25, 5.00),
    ("gemini", "gemini-1.5-flash"): (0.075, 0.30),
    # OpenAI ChatGPT — gpt-5 / gpt-4o family
    ("chatgpt", "gpt-5-mini"): (0.25, 2.00),
    ("chatgpt", "gpt-5"): (1.25, 10.00),
    ("chatgpt", "gpt-4o-mini"): (0.15, 0.60),
    ("chatgpt", "gpt-4o"): (2.50, 10.00),
    ("chatgpt", "gpt-4-turbo"): (10.00, 30.00),
    # DeepSeek — chat (V3) + reasoner (R1)
    ("deepseek", "deepseek-reasoner"): (0.55, 2.19),
    ("deepseek", "deepseek-chat"): (0.27, 1.10),
}


def compute_cost_usd(provider: str, model: str,
                     prompt_tokens: int, completion_tokens: int) -> float | None:
    """Resolve `(provider, model)` against `RATE_CARD` (longest-prefix
    wins) and return USD cost for the call. Returns ``None`` when no
    rate-card entry matches — recorder writes NULL so the dashboard
    renders "—" instead of lying with $0.0000 on an unpriced model.
    A genuinely-zero-token call against a known model still yields
    0.0 (truthful — nothing was billed).
    """
    if not provider or not model:
        return None
    p = provider.strip().lower()
    m = model.strip().lower()
    # Longest-prefix match — entries are sorted at lookup time so a
    # new entry can land anywhere in the table without breaking
    # resolution order.
    candidates = [
        (prefix, rates) for (prov, prefix), rates in RATE_CARD.items()
        if prov == p and m.startswith(prefix.lower())
    ]
    if not candidates:
        return None
    # Sort by prefix length DESC, take the longest (most specific) match.
    candidates.sort(key=lambda kv: len(kv[0]), reverse=True)
    in_rate, out_rate = candidates[0][1]
    pt = max(0, int(prompt_tokens or 0))
    ct = max(0, int(completion_tokens or 0))
    return round((pt * in_rate + ct * out_rate) / 1_000_000.0, 6)


# Coarse heuristic for `accuracy_score`. Captures cheap structural
# signals (call succeeded, answer non-empty + substantive, ACTION line
# fired when expected, response references real OmniGrid surface
# vocabulary). Does NOT validate factual correctness — that needs a
# critic model or operator-rated post-hoc UI, both filed as Stage 2+.
# Until then the score is a relative-quality indicator: 0.0 means
# failed, 1.0 means every cheap signal lit up, in-between rows
# indicate partial success.
import re as _re

_OMNIGRID_SURFACE_RE = _re.compile(
    r"\b(?:host|admin|drawer|stack|service|portainer|webmin|beszel|pulse|"
    r"snmp|cpu|memory|disk|/api/|node[ -]?exporter|swarm)\b",
    _re.IGNORECASE,
)


def score_accuracy(*, kind: str, ok: bool, text: str | None,
                   history_events: dict | None) -> tuple[float | None, dict]:
    """
    Returns ``(score, check)`` where:
    - score: 0..1 float, or None when the call shape carries no signal
      (e.g. a kind we don't have heuristics for yet).
    - check: dict of named signal components that contributed to the
      score; lands in ``ai_jobs.accuracy_check`` as JSON for triage.

    Caller passes the same ``history_events`` it hands `record_ai_call`,
    so this helper can read structural signals (resolved action_id,
    extracted DSL) without re-parsing.
    """
    check: dict = {}
    if not ok:
        check["ok"] = False
        return 0.0, check

    if kind == "test":
        # One-token auth ping — binary success.
        check["ok"] = True
        return 1.0, check

    text_str = (text or "").strip()
    events = history_events or {}

    if kind == "host_filter":
        # Caller stuffs the parsed DSL into history_events.dsl. When
        # present we know the response was structurally correct; when
        # absent the call succeeded HTTP-wise but the model didn't
        # emit a parseable filter (treat as a partial answer).
        dsl = (events.get("dsl") or "").strip()
        if dsl:
            check["dsl_extracted"] = True
            return 1.0, check
        check["dsl_extracted"] = False
        # Some text returned but unusable — small positive signal so
        # the dashboard doesn't read a flat 0% on a working endpoint
        # that just got an unparseable response.
        return 0.3, check

    # Default: palette-shaped (and any future free-form kinds).
    if not text_str:
        check["empty_text"] = True
        return 0.3, check
    score = 0.5
    check["non_empty"] = True
    if len(text_str) >= 30:
        score += 0.2
        check["substantive"] = True
    if (events.get("action_id") or "").strip():
        # Model emitted a valid ACTION: line that the backend whitelist
        # accepted — strong structural signal it parsed the user's
        # intent correctly.
        score += 0.15
        check["action_emitted"] = True
    if _OMNIGRID_SURFACE_RE.search(text_str):
        # Model knows it's talking about OmniGrid surfaces, not
        # hand-waving about generic dashboards.
        score += 0.1
        check["surface_referenced"] = True
    # Round to 4dp so floating-point summation noise (0.5 + 0.2 + 0.1
    # = 0.7999999...) doesn't surface in the dashboard or assertions.
    return round(min(score, 1.0), 4), check


async def _with_retry(call_factory, *, provider: str, model: str) -> dict:
    """Run an AI provider call with one optional retry on transient
    upstream overload. The retry policy is fully configurable via the
    three `tuning_ai_retry_*` knobs (Admin → AI Integration):
      - `tuning_ai_retry_enabled` (0/1)        — master gate
      - `tuning_ai_retry_backoff_ms`           — sleep before retry
      - `tuning_ai_retry_first_attempt_max_ms` — gate: only retry when
        the first attempt resolved within this many ms (slow first
        attempts mean the upstream is genuinely struggling and a retry
        won't help — just doubles the wait).

    Retry-classified statuses: 429 / 502 / 503 / 504. Other failures
    (auth, model-not-found, payload-too-large, real backend bugs)
    propagate unchanged — retrying them either can't help or risks
    double-charge if the first call was actually billed.

    Logs a `[ai] warning ... retrying` line when the retry fires AND
    a `[ai] warning ... retry-skipped` line when the first attempt
    was too slow to retry — both classify WARN per the persistent-log
    severity regex.
    """
    from logic.tuning import Tunable, tuning_int
    enabled = bool(tuning_int(Tunable.AI_RETRY_ENABLED))
    if not enabled:
        return await call_factory()
    backoff_ms = tuning_int(Tunable.AI_RETRY_BACKOFF_MS)
    first_max_ms = tuning_int(Tunable.AI_RETRY_FIRST_ATTEMPT_MAX_MS)
    transient_statuses = (429, 502, 503, 504)
    import time as _time
    t0 = _time.monotonic()
    out = await call_factory()
    elapsed_ms = (_time.monotonic() - t0) * 1000.0
    # Retry-eligible: transient-overload HTTP statuses (429 / 502 /
    # 503 / 504) OR a `transient: True` flag set by the timeout /
    # network-error branches in `ask_provider`. Pre-fix only the
    # status-based check ran, so timeouts (status=0 with the
    # transient flag) skipped retry entirely — defeating the whole
    # point of `tuning_ai_retry_*` for the most common failure mode.
    is_transient_status = (
        isinstance(out, dict) and not out.get("ok")
        and out.get("status") in transient_statuses
    )
    is_transient_flag = (
        isinstance(out, dict) and not out.get("ok")
        and bool(out.get("transient"))
    )
    if not (is_transient_status or is_transient_flag):
        return out
    status = out.get("status") or "TIMEOUT"
    if elapsed_ms >= first_max_ms:
        # Slow first attempt — log + propagate without retrying.
        # Word "warning" + no "fail/error" tokens → classifier picks WARN.
        print(f"[ai] retry-skipped warning — provider={provider} model={model} "
              f"HTTP={status} first-attempt-{elapsed_ms:.0f}ms >= threshold-{first_max_ms}ms "
              f"(upstream slow, retry would only double wait)")
        return out
    print(f"[ai] retrying-after-{backoff_ms}ms warning — provider={provider} model={model} "
          f"HTTP={status} upstream-overloaded (transient)")
    if backoff_ms > 0:
        await asyncio.sleep(backoff_ms / 1000.0)
    return await call_factory()


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
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model or _DEFAULT_MODELS["claude"],
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
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
        "content-type": "application/json",
    }
    body = {
        "contents": [{"parts": [{"text": "ping"}]}],
        "generationConfig": {"maxOutputTokens": 1},
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
        "content-type": "application/json",
    }
    body = {
        "model": model or _DEFAULT_MODELS.get(provider, ""),
        "messages": [{"role": "user", "content": "ping"}],
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
    timeout: float | None = None,
) -> dict:
    """Single entry-point used by `/api/admin/ai/{provider}/test`.

    Validates the inputs, dispatches to the per-provider probe, and
    returns ``{ok, status, detail, response_time_ms, provider}``. Any
    network / library error is caught and reported as ``ok=False``
    with the exception text in ``detail`` — the SPA renders it inline.

    The caller is responsible for pulling the API key out of the saved
    settings (see CLAUDE.md's keep-current-if-blank contract) — this
    function takes the cleartext key as an argument.

    ``timeout`` defaults to the live ``tuning_ai_http_timeout_seconds``
    TUNABLE (15s default) so a Save in Admin → AI Integration takes
    effect on the next Test click without restart. Defensive fallback
    to legacy 15s on tunable-resolver failure.
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

    if timeout is None:
        try:
            from logic.tuning import Tunable, tuning_int as _tuning_int
            timeout = float(_tuning_int(Tunable.AI_HTTP_TIMEOUT_SECONDS))
        except (ImportError, KeyError, ValueError, TypeError):
            timeout = 15.0
    # Type-narrow: every branch above sets `timeout` to a real float.
    # Assert it so the type-checker stops flagging `float | None` at the
    # _probe_* call sites below (their signatures want a plain float).
    assert timeout is not None

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
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body: dict = {
        "model": model or _DEFAULT_MODELS["claude"],
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
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
        "content-type": "application/json",
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
        "contents": [{"parts": [{"text": prompt}]}],
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
            "model": mdl,
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
        "content-type": "application/json",
    }
    messages: list = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": model or _DEFAULT_MODELS.get(provider, ""),
        "messages": messages,
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
    timeout: float | None = None,
) -> dict:
    """Ask one provider for a chat completion. Returns
    ``{ok, text, detail, response_time_ms, provider, model, tokens}``.

    Sibling of :func:`test_provider` — same dispatch matrix, but issues
    a real conversational request rather than a one-token ping. Used
    by the Cmd-K palette's "Ask AI" surface and any future feature
    that needs a model response. Preserves the keep-current-if-blank
    secret contract — the caller resolves the saved API key.

    ``timeout`` defaults to the live
    ``tuning_ai_extended_http_timeout_seconds`` TUNABLE (30s default)
    so a Save in Admin → AI Integration takes effect on the next call
    without restart. Defensive fallback to legacy 30s on tunable-resolver
    failure.
    """
    p = (provider or "").strip().lower()
    if p not in SUPPORTED_PROVIDERS:
        return {"ok": False, "status": 0, "provider": p,
                "detail": f"Unsupported provider: {provider}",
                "response_time_ms": 0}
    if timeout is None:
        try:
            from logic.tuning import Tunable, tuning_int as _tuning_int
            timeout = float(_tuning_int(Tunable.AI_EXTENDED_HTTP_TIMEOUT_SECONDS))
        except (ImportError, KeyError, ValueError, TypeError):
            timeout = 30.0
    # Type-narrow: every branch above sets `timeout` to a real float.
    # Capture as a non-Optional local so the closure below propagates
    # the narrowed type cleanly — type-checkers don't follow control-
    # flow narrowing across closure boundaries.
    assert timeout is not None
    timeout_f: float = timeout
    if not (api_key or "").strip():
        return {"ok": False, "status": 0, "provider": p,
                "detail": "API key is not set. Configure it in Admin → AI Integration first.",
                "response_time_ms": 0}
    if not (prompt or "").strip():
        return {"ok": False, "status": 0, "provider": p,
                "detail": "prompt is required", "response_time_ms": 0}

    # Build a thunk for the per-provider call so the retry wrapper
    # can re-invoke it identically on a transient-overload retry.
    async def _do_call() -> dict:
        if p == "claude":
            return await _chat_claude(api_key, model or "", base_url or "",
                                      prompt, system_prompt, max_tokens, timeout_f)
        elif p == "gemini":
            return await _chat_gemini(api_key, model or "", base_url or "",
                                      prompt, system_prompt, max_tokens, timeout_f)
        else:
            return await _chat_openai_compatible(p, api_key, model or "", base_url or "",
                                                 prompt, system_prompt, max_tokens, timeout_f)

    started = time.time()
    try:
        out = await _with_retry(_do_call, provider=p, model=(model or _DEFAULT_MODELS.get(p, "")))
    except httpx.TimeoutException:
        # `transient: True` flag tells `_with_retry` (one level above)
        # AND the fallback wrapper that this is a retry-eligible
        # failure even though `status` is 0. Pre-fix the 0 status
        # made retry + fallback skip entirely — defeating the whole
        # point of a configured fallback chain when the provider's
        # API was slow.
        out = {"ok": False, "status": 0,
               "detail": f"Request timed out after {timeout:.0f}s — the provider's API may be slow.",
               "provider": p, "transient": True}
    except httpx.RequestError as e:
        # Same `transient` flag — network errors (DNS, connection
        # refused, TLS handshake failure mid-flight) are retry-
        # eligible: the upstream is briefly unavailable but a
        # second attempt or a different provider may succeed.
        out = {"ok": False, "status": 0,
               "detail": f"Network error: {type(e).__name__}: {e}",
               "provider": p, "transient": True}
    except Exception as e:  # noqa: BLE001
        # Generic exceptions are NOT marked transient — they're
        # usually code bugs (NameError / TypeError / JSON decode
        # failure) that retrying can't help. Fallback would just
        # double-charge for the same broken request.
        out = {"ok": False, "status": 0,
               "detail": f"{type(e).__name__}: {e}",
               "provider": p}
    out.setdefault("provider", p)
    out["response_time_ms"] = int((time.time() - started) * 1000)
    return out


# ---------------------------------------------------------------------------
# Provider fallback chain — when the active provider returns a transient-
# overload status (429 / 502 / 503 / 504), transparently try the next
# operator-ordered provider before propagating the failure to the route
# handler. Builds on `_with_retry` (each provider attempt still gets the
# same single-retry treatment) — so a 503 from Gemini retries Gemini once
# THEN falls back to Claude (which retries Claude once if Claude also
# 503s, etc.).
#
# Operator controls the order via `ai_fallback_order` (CSV of provider ids
# in priority order) and the master switch via `ai_fallback_enabled`. Cap
# the depth via `ai_fallback_max_depth` (1 or 2 — beyond that a multi-
# provider outage cascades into 4× latency for no real recovery upside).
# Skip providers in the chain that are disabled OR have no API key
# configured (filter happens at the route layer before calling this; the
# wrapper just walks the list given to it).
#
# Capability-mismatch guard: only fall back when the prompt is small (≤
# `prompt_size_cap_chars`, default ~8k tokens × 4 chars). Larger prompts
# might choke a fallback provider with a smaller context window or weaker
# JSON-format compliance — better to return the original transient
# failure than serve a hallucinated answer from a less-capable provider.
# ---------------------------------------------------------------------------
_FALLBACK_RETRY_STATUSES = (429, 502, 503, 504)


async def ask_provider_with_fallback(
    primary: str,
    *,
    fallback_chain: list[str],
    provider_creds: dict[str, dict],
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 512,
    timeout: float = 30.0,
    fallback_enabled: bool = False,
    max_depth: int = 1,
    prompt_size_cap_chars: int = 32000,
) -> dict:
    """Try `primary` first; on transient overload, walk `fallback_chain`
    (operator-ordered list of provider ids) up to `max_depth` deep.

    `provider_creds` carries `{provider_id: {api_key, model, base_url}}`
    — the caller fetches these from settings + filters to enabled
    providers BEFORE calling. The wrapper trusts the chain it's given.

    Response shape extends `ask_provider`'s with two fields:
      - `fallback_used: bool`   — True iff the response came from a
        provider OTHER than `primary`.
      - `fallback_chain: [{provider, status, response_time_ms,
        succeeded}, ...]` — every attempt's outcome (primary first,
        then each fallback hop) so the SPA can render
        "Fell back from gemini-2.5-pro to claude-opus-4-7 due to
        upstream overload" in the answer modal.
    """
    history: list[dict] = []

    async def _attempt(provider_id: str) -> dict:
        # Narrow `creds` to a real dict; `or {}` collapses the None
        # branch of `provider_creds.get(...)`. The explicit type hint
        # keeps the type-checker honest at the .get() call sites below
        # (otherwise it widens to `dict | str` because the values are
        # Any-typed).
        creds: dict = provider_creds.get(provider_id) or {}
        api_key_v = creds.get("api_key") or ""
        model_v = creds.get("model")
        base_url_v = creds.get("base_url")
        out = await ask_provider(
            provider_id,
            api_key=str(api_key_v),
            prompt=prompt,
            system_prompt=system_prompt,
            model=str(model_v) if isinstance(model_v, str) else None,
            base_url=str(base_url_v) if isinstance(base_url_v, str) else None,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        history.append({
            "provider": provider_id,
            "status": out.get("status"),
            "response_time_ms": out.get("response_time_ms", 0),
            "succeeded": bool(out.get("ok")),
        })
        return out

    primary_out = await _attempt(primary)

    # Bail-out conditions — return primary verbatim:
    #   - primary succeeded (no need to try fallbacks)
    #   - fallback master switch off
    #   - failure isn't a transient-overload class (auth / payload-too-
    #     large / model-not-found are operator-fixable, fallback can't help)
    #   - prompt is too large for safe capability mismatch
    #   - no fallbacks configured
    primary_status = primary_out.get("status")
    if primary_out.get("ok"):
        primary_out["fallback_used"] = False
        primary_out["fallback_chain"] = history
        return primary_out
    if not fallback_enabled:
        primary_out["fallback_used"] = False
        primary_out["fallback_chain"] = history
        return primary_out
    # Eligible for fallback when the primary returned a transient-
    # overload HTTP status OR when the call timed out / hit a network
    # error (`transient: True` flag set by `ask_provider`'s exception
    # branches). Pre-fix timeouts skipped the fallback chain entirely
    # because their status was 0, not 429/502/503/504 — defeating the
    # configured fallback for the most common failure mode.
    is_transient_primary = (
        primary_status in _FALLBACK_RETRY_STATUSES
        or bool(primary_out.get("transient"))
    )
    if not is_transient_primary:
        primary_out["fallback_used"] = False
        primary_out["fallback_chain"] = history
        return primary_out
    if len(prompt or "") > prompt_size_cap_chars:
        # Prompt too large — capability-mismatch risk; surface a
        # transparent skip log line + return primary failure unchanged.
        print(f"[ai] fallback-skipped warning — primary={primary} HTTP={primary_status} "
              f"prompt-{len(prompt)}-chars > cap-{prompt_size_cap_chars} "
              f"(capability-mismatch guard, fallback would risk less-capable provider)")
        primary_out["fallback_used"] = False
        primary_out["fallback_chain"] = history
        return primary_out
    if not fallback_chain:
        primary_out["fallback_used"] = False
        primary_out["fallback_chain"] = history
        return primary_out

    # Walk the chain — first success wins. Cap by max_depth so a multi-
    # provider outage doesn't cascade.
    for hop, alt_provider in enumerate(fallback_chain[:max_depth], start=1):
        if alt_provider == primary:
            continue  # don't re-try the primary
        if alt_provider not in provider_creds:
            continue  # caller filtered it out (disabled / no API key)
        print(f"[ai] fallback-attempt-{hop} warning — primary={primary} HTTP={primary_status} "
              f"trying-fallback={alt_provider} upstream-overloaded")
        alt_out = await _attempt(alt_provider)
        if alt_out.get("ok"):
            # Stamp the response so the route + SPA can show "Fell
            # back from <primary> to <alt_provider>".
            alt_out["fallback_used"] = True
            alt_out["fallback_from"] = primary
            alt_out["fallback_chain"] = history
            return alt_out

    # Every fallback also failed — return primary's failure with the
    # chain history so the operator sees what was tried.
    primary_out["fallback_used"] = False
    primary_out["fallback_chain"] = history
    return primary_out


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
    "update_all_updatable",
    "sign_out",
    # On-demand port scan against the currently-open host drawer.
    # SPA-side gate (master toggle + drawer-open + admin role) lives
    # in `_commandActions()`; the AI emitting `ACTION: scan_ports` is
    # honoured only when the SPA-side gate would otherwise pass.
    "scan_ports",
    # Re-test connection actions for each integration. SPA-callable
    # via `_commandActions()` (one entry per provider when the SPA
    # has the matching test handler). The AI can fire them when the
    # operator says "test the Portainer connection" / "re-test
    # Beszel". Each handler navigates to the relevant Admin tab AND
    # kicks off the probe so the result chip appears in context.
    "test_portainer",
    "test_oidc",
    "test_beszel",
    "test_pulse",
    "test_webmin",
    "test_snmp",
    "test_ping",
    "test_asset_inventory",
    "test_apprise",
    # Switch a stack/container's image tag to a different floating tag.
    # Operator pattern: pin a container deployed against `:2.0.0-dev` to
    # the moving `:2` tag for v2-line patch updates without bumping to
    # `:latest` (which on some images still tracks v1). The AI emits
    # `ACTION: retag_image` paired with `ACTION_TAG: <new_tag>` (and
    # OPTIONALLY `ACTION_ITEM: <name-or-id>`). When ACTION_ITEM is
    # omitted, the SPA defaults to the open item drawer; if no drawer
    # is open AND no ACTION_ITEM, the operator gets a toast asking to
    # open the drawer or name the item explicitly. Destructive — gates
    # behind the inline-confirm chip in approval mode.
    "retag_image",
    # Schedule CRUD via AI palette.
    # Each action consumes `ACTION_DATA: <json>` carrying the payload
    # (name, kind, interval_seconds, etc.). The SPA dispatches to the
    # SAME `/api/schedules` endpoints the Admin → Schedules table uses,
    # so backend authorization + bounds-clamping + skip-if-running gates
    # all apply. delete is the only destructive action — gates behind
    # the inline-confirm chip in the AI sidebar.
    "schedule_create",
    "schedule_update",
    "schedule_delete",
    # Item write-ops via AI palette — destructive, gated by inline-
    # confirm chip in the sidebar (same shape as `cleanup_stopped` /
    # `update_all_updatable`). Each requires `ACTION_ITEM: <name-or-id>`
    # to identify the target; the SPA defaults to the open drawer if
    # ACTION_ITEM is omitted (toast asks the operator to name the item
    # if no drawer is open either). Same authorisation as the row-level
    # action button: bearer / cookie + admin role.
    "update_stack",
    "update_container",
    "restart_service",
    "restart_container",
    "remove_container",
    # Bulk node prune via AI palette — same destructive treatment.
    # Operator phrases: "prune docker on web01" / "prune the cluster".
    # ACTION_HOSTS: <ids> can target a subset; omitting it falls through
    # to the SPA's bulk-prune flow.
    "prune_node",
    # Bulk host-pause / resume via AI palette. The Cmd-K palette
    # already has a verb-prefix DSL for these (`pause:` / `resume:`);
    # exposing the snake_case action IDs makes the cmd-K route
    # consistent for sidebar dispatching.
    "hosts_bulk_pause",
    "hosts_bulk_resume",
    # On-demand backup snapshot via AI palette. Non-destructive
    # (creates a new zip; retention prune fires under the existing
    # `tuning_backup_retention_count` knob).
    "backup_create",
    # AI memory write actions — already exposed via the MEMORY: /
    # MEMORY-FORGET: directives in AI replies, but adding the explicit
    # snake_case action IDs makes the cmd-K route consistent so
    # operator phrases like "remember that X" / "forget about Y"
    # parse to the same dispatch path regardless of whether the AI
    # emits MEMORY: or ACTION: ai_memory_create.
    "ai_memory_create",
    "ai_memory_delete",
    # Fire any schedule on-demand. Operator phrase: "run the backup
    # schedule now". Requires `ACTION_ITEM: <name>` to identify the
    # schedule by its operator-visible name. Same endpoint
    # (POST /api/schedules/{id}/run) the Admin table's "Run now"
    # button uses.
    "schedule_run_now",
    # Synonym IDs — same SPA descriptors as the canonical entries
    # above, accepted by the backend so the AI can emit whichever
    # operator-natural phrasing the user typed without the SPA's
    # `_actionDescriptorById` alias needing to know about a fresh
    # snake_case variant first. SPA's alias map resolves these to
    # the canonical descriptor (`hosts-bulk-pause`,
    # `mark-all-notifications-read`, etc.).
    "bulk_pause_hosts",
    "bulk_resume_hosts",
    "prune_stopped",
    "clear_notifications",
    "notifications_clear_all",
})

PALETTE_SYSTEM_PROMPT: str = (
    "You are the Cmd-K palette assistant for OmniGrid, a Docker-Swarm "
    "management dashboard. The operator just typed a query into the "
    "command palette and wants help navigating, diagnosing, or acting. "
    "\n\n"
    "ANSWER WITH DATA WHEN YOU HAVE IT. The user_prompt below carries "
    "JSON records for every visible host (id, label, status, cpu_pct, "
    "mem_pct, disk_pct, disk_free_gb, disk_total_gb, uptime_s, paused, "
    "providers) AND every visible item (name, status, health, type, "
    "replicas, desired, update_available). Hosts and items are ALWAYS "
    "in the payload regardless of which UI surface the operator is "
    "currently on — the `Current view: <name>` line at the top of the "
    "user_prompt is INFORMATIONAL ONLY (it tells you what the operator "
    "is currently looking at), NEVER a constraint on which data you "
    "can answer about. NEVER refuse a host question with \"that's not "
    "available in the Stacks view\" or similar — the host records are "
    "right there in the supplied JSON. Same in reverse: if the "
    "operator is on the Hosts page and asks about stacks/services, "
    "the items[] block is supplied — answer from it. When the "
    "operator asks a DATA question (\"which hosts are running out of "
    "space soon?\", \"top 5 hosts by CPU\", \"any services "
    "degraded?\", \"what's stopped?\", \"any updates pending?\"), "
    "DON'T point them at a UI column to sort — RANK / COUNT / "
    "AGGREGATE the records yourself and reply with a short list of "
    "the top 3-5 specifics including the actual numbers. Example "
    "shape:\n"
    "  Top 3 hosts low on disk:\n"
    "  1. nas01 — 92% used (8 GB free of 100 GB)\n"
    "  2. web03 — 87% used (52 GB free of 400 GB)\n"
    "  3. host01 — 76% used (240 GB free of 1.0 TB)\n"
    "Use the EXACT id/label from the JSON. When the data shows nothing "
    "of concern, say so explicitly (e.g. \"no host above 80% disk — "
    "you're fine\").\n\n"
    "GROUNDING — STRICT. NEVER invent, hallucinate, or guess host "
    "names, item names, or metric values. The ONLY hosts that exist "
    "are those in the supplied `Available hosts` JSON; the ONLY items "
    "that exist are those in `Available items`. If the JSON shows 4 "
    "hosts, the operator has 4 hosts — not 7, not pve-01, not "
    "rpi-cluster-N. If the question can't be answered from the "
    "supplied data, say so explicitly (\"I don't see any host with "
    "X in the data I have — try refreshing or check Admin → Hosts\"). "
    "Never reply with placeholder names like 'host01' / 'pve-01' / "
    "'web03' — those are illustrative ONLY in this system prompt and "
    "must NEVER appear verbatim in your output. ALWAYS quote the "
    "exact `id` field from the JSON.\n\n"
    "ASSET ALIASES. Hosts may carry an `asset` sub-object with "
    "operator-curated metadata: `asset.name` (display name from the "
    "asset inventory — often a short nickname like 'qotom' or "
    "'r730xd-1'), `asset.type` (short type code), `asset.vendor`, "
    "`asset.model`, `asset.serial`, `asset.location`, "
    "`asset.custom_number` (operator's numeric ordering). When the "
    "user names a host using ANY of these alias fields (\"how's "
    "qotom doing?\" / \"the Dell R730 in rack 3\" / \"host #5\"), "
    "match it against `id` / `label` AND every populated field on "
    "the `asset` sub-object. Once matched, ALWAYS answer using the "
    "host's actual `id` so the operator can correlate with the rest "
    "of OmniGrid; quoting the asset name alongside in parens is "
    "fine (\"webserver (qotom)\"). Hosts WITHOUT an `asset` sub-"
    "object only match on `id` / `label` (asset inventory not "
    "configured for that host yet).\n\n"
    "HOST IDENTITY — MULTIPLE NAMES PER HOST. The same physical "
    "machine can carry SEVERAL operator-recognisable names; you must "
    "match across ALL of them, not just `id` / `label`. The host JSON "
    "may include any of these in addition to `id` / `label`:\n"
    " - `host_hostname` — kernel-reported hostname (uname -n). When "
    "the user pastes `df -h`, `hostname`, or any shell prompt like "
    "`user@host01:~$`, match that hostname back to "
    "`host_hostname` to identify the curated host. The curated `id` "
    "and `host_hostname` OFTEN DIVERGE (id `web01` ↔ kernel "
    "hostname `host01.example.com`) because operators use roles / aliases "
    "for `id` while the machine reports its real name. NEVER assume "
    "id == hostname.\n"
    " - `beszel_name` / `pulse_name` / `webmin_name` / `snmp_name` — "
    "per-provider name aliases the operator typed in Admin → Hosts. "
    "May or may not match the kernel hostname.\n"
    " - `vendor` / `model` / `serial` — DMI hardware identity. "
    "Useful when the user describes the hardware (\"the Pi 4\" / "
    "\"the R730xd\").\n"
    " - `platform` / `kernel` / `arch` — `uname -a` output. Useful "
    "when the user describes the OS family (\"the FreeBSD one\" / "
    "\"the ARM box\").\n"
    "Workflow when the user pastes shell output: SCAN every line for "
    "hostnames / mount sizes / kernel versions; cross-reference "
    "against the host JSON's identity fields above; name the matched "
    "host using its curated `id` with the matched alias in parens "
    "for clarity (e.g. \"web01 (kernel hostname host01.example.com, "
    "939 GB root)\"). When NOTHING in the JSON matches, say so "
    "explicitly — don't guess.\n\n"
    "FUZZY HOST MATCHING. The same partial / phonetic / abbreviated "
    "matching that applies to items applies to host names too. "
    "Substring + case-insensitive match across `id`, `label`, "
    "`host_hostname`, every `*_name` alias field, AND every "
    "populated `asset.*` field. When the operator types \"qotom\", "
    "\"the pi\", \"nas\", \"r730\", run the search before refusing. "
    "Multiple matches → list all and ask which one. Zero matches → "
    "say so explicitly with the closest candidates surfaced (\"I "
    "don't see a host named X — closest matches in your fleet are "
    "<list of 3 nearest>\").\n\n"
    "ITEM NAME MATCHING — FUZZY. When the operator names a stack / "
    "service / container that's not an EXACT match against any item "
    "in the `Available items` JSON, before refusing, do a substring + "
    "phonetic-style fuzzy search against every item's `name` AND "
    "`stack` field. Operators routinely type partial / phonetic / "
    "abbreviated names — \"Seer\" or \"Seerr\" matches `overseerr` / "
    "`jellyseerr`; \"homarr\" matches `homarr_homarr`; \"npm\" matches "
    "`nginx-proxy-manager`; \"watch\" matches `watchtower`. Rules: "
    "case-insensitive; substring match against name OR stack; if "
    "MULTIPLE items match, list them all (\"I see two matches — "
    "jellyseerr and overseerr; which one?\") so the operator can "
    "disambiguate; if NO items match even after fuzzy search, "
    "explicitly say so and pivot to a related signal (degraded "
    "services, recent errors in the log block) so the answer is still "
    "actionable. NEVER refuse a query that has an obvious fuzzy match "
    "in the supplied items list. Always quote the matched item's "
    "ACTUAL name from the JSON in your answer, not the operator's "
    "typed approximation.\n\n"
    "OMNIGRID VOCABULARY. \"Hosts\" in OmniGrid means the curated "
    "machines listed in Admin → Hosts (monitored by Beszel / Pulse / "
    "node-exporter / Webmin / SNMP / Ping). \"Nodes\" means Docker "
    "Swarm cluster members surfaced from Portainer (with role=manager "
    "or worker). They are SEPARATE concepts: a single physical box can "
    "appear in BOTH lists, but they don't overlap automatically. When "
    "the operator says 'hosts' they mean the Hosts list (Available "
    "hosts JSON below); when they say 'nodes' they mean Swarm nodes. "
    "If the operator corrects you (\"I asked for hosts not nodes\"), "
    "do NOT explain that they're the same thing — they're not — "
    "answer from the Available hosts JSON.\n\n"
    "RECENT LOG SIGNALS. The user_prompt may carry a 'Recent log "
    "signals' block listing the last 30 ERROR / WARN lines from "
    "OmniGrid's in-process log ring (same source as Admin → Logs). "
    "When the operator asks 'any errors I should fix?' / 'check "
    "logs' / 'anything wrong?', READ THAT BLOCK and answer with "
    "concrete log evidence — quote the actual log lines (truncated "
    "to a sentence each) and group by tag (`[beszel]` / `[snmp]` / "
    "`[port_scan]` / etc.) so the operator can correlate. Don't "
    "claim 'I can't check logs directly' — the block IS your log "
    "view. When the block is ABSENT (rare — only on a fresh boot "
    "with empty buffer) say so explicitly: 'No errors or warnings "
    "in the last 30 entries — your fleet looks clean.' Pointer "
    "operators at Admin → Logs for the full history; the block "
    "you see is the most-recent slice.\n\n"
    "STALE-DATA HINTS. Some host records carry `stale: true` plus "
    "`stale_age_s` (age of the snapshot in seconds) and `stale_fields` "
    "(which axes — cpu_pct / mem_pct / disk_pct / uptime_s — were "
    "filled from a snapshot rather than live data because the upstream "
    "provider stopped reporting). When you cite a stale field for a "
    "host, QUALIFY the answer: prefix \"last known\" or append \"(as "
    "of N min ago — provider currently unavailable)\" so the operator "
    "knows it's cached state, not live. Do NOT confidently report "
    "stale numbers as current. If every host the answer would "
    "reference is stale, say so up front (\"every reachable host is "
    "currently un-probed; here's the last known state\").\n\n"
    "\"COLLECTING DATA\" SPARKLINES — DISTINCT FROM STALE HOST DATA. "
    "When the operator asks why an item / service / container is "
    "showing \"Collecting data\" (or \"Collecting data…\") next to "
    "its CPU / Memory / Disk row, this is the SPARKLINE placeholder, "
    "NOT a stale-host signal. Diagnose against the item's own data "
    "first — do NOT default to blaming Beszel / Pulse / Portainer "
    "stale state for it. The legitimate causes, in order of "
    "frequency:\n"
    " 1. The sampler hasn't written ≥2 rows yet for this item_id "
    "in `stats_samples` (fresh deploy, just-created service, OR a "
    "redeployed container whose new id replaced the old one — the "
    "old samples are under a different item_id and the new id has "
    "0/1 rows). Wait one more sampler tick (`tuning_stats_sample_"
    "interval_seconds`, default 180s).\n"
    " 2. The Portainer /stats call returned `has_stats=false` for "
    "this container (agent unreachable on the item's node OR "
    "container not running). The item drawer's Debug panel surfaces "
    "this as `live_stats.has_stats=false` in the diagnostics list.\n"
    " 3. For DISK specifically: `size_root=0` for every row — "
    "pre-migration deploys before the `stats_samples.size_root` "
    "column shipped. The CPU / Mem sparklines render normally even "
    "for IDLE items (post-fix 2026-05); only disk specifically "
    "suppresses on all-zero because 0 there means \"no data\" not "
    "\"idle\".\n"
    "Idle services / containers (cpu_percent=0 for the whole window) "
    "DO NOT show \"Collecting data\" anymore — the SPA renders a "
    "truthful flat-line sparkline at the baseline. If the operator "
    "reports the placeholder ON an idle-but-healthy item, ask them "
    "to hard-reload (Cmd-Shift-R / Ctrl-Shift-R) to pick up the "
    "fixed SPA build. The admin-only debug panel inside the drawer "
    "(Stacks / Services / Containers / Nodes) carries an explicit "
    "`diagnostics[]` list that names the exact reason — point "
    "operators at it for self-service triage. The host-stale-data "
    "block above is a SEPARATE concept (host CPU / Mem / Disk bars "
    "in the Hosts view showing snapshot-restored values when the "
    "upstream host-stats provider went down). Do NOT conflate the "
    "two diagnostics.\n\n"
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
    "below, you MUST end your reply with one or more lines of the "
    "form `ACTION: <id>` (one action per line). The reply text "
    "BEFORE the first ACTION line should be a short one-liner "
    "confirmation (\"Opening notifications.\", \"Switching to dark "
    "theme.\", \"I'll mark every notification as read for you.\"). "
    "The SPA parses every ACTION line, strips them from the visible "
    "text, and INVOKES the actions IN ORDER. Without an ACTION "
    "line, the action DOES NOT FIRE — operators see the prose but "
    "nothing happens. Bias toward emitting an ACTION when the "
    "query is an imperative verb (open / show / refresh / cleanup "
    "/ sign out / switch / mark) targeting one of the listed action "
    "ids.\n\n"
    "MULTI-ACTION QUERIES. When the operator chains multiple "
    "imperatives (\"refresh and cleanup\", \"clean up containers and "
    "mark all notifications read\", \"switch to dark theme then "
    "refresh\"), emit ONE ACTION line per action, in the order they "
    "should fire. Order matters: non-destructive actions like "
    "`refresh` should run BEFORE destructive ones like "
    "`cleanup_stopped` so the operator sees the freshest container "
    "list before the cleanup confirm popup. The SPA fires actions "
    "sequentially and pauses for each destructive action's "
    "confirmation popup before continuing.\n\n"
    "DIAGNOSTIC INTENT MAPPING. When the operator asks a "
    "diagnostic question about an integration's health (\"is "
    "Portainer working?\", \"is Authentik / SSO up?\", \"can you "
    "reach Beszel?\", \"check Pulse\", \"test Webmin connection\", "
    "\"verify OIDC\"), do NOT narrate the UI path (\"go to Admin → "
    "Providers and click Test\") — INSTEAD emit the matching "
    "`ACTION: test_<provider>` line so the SPA fires the probe "
    "directly and surfaces the result chip in context. Mapping: "
    "Portainer → `test_portainer`; Authentik / OIDC / SSO → "
    "`test_oidc`; Beszel → `test_beszel`; Pulse → `test_pulse`; "
    "Webmin → `test_webmin`. The Test endpoints are idempotent + "
    "audited via `last_test_success` stamps so firing them in "
    "response to a diagnostic question is safe; the SPA navigates "
    "to the relevant Admin tab AND shows the result so the "
    "operator sees the answer in one click instead of three. Same "
    "principle for port-scan questions targeting the OPEN host "
    "drawer (\"what's listening on this host?\", \"scan its "
    "ports\", \"any open ports here?\") → emit `ACTION: scan_ports` "
    "instead of describing the drawer button. If no host drawer is "
    "open the SPA's gate fails gracefully — narrate the open-the-"
    "drawer step in that case.\n\n"
    "WRITE-ACTION DISPATCH (DESTRUCTIVE). When the operator asks "
    "to update / restart / remove / prune / pause / resume / back "
    "up / forget / fire-schedule, emit the matching ACTION line "
    "INSTEAD of narrating the UI path. The SPA gates every "
    "destructive action behind an inline-confirm chip in the AI "
    "sidebar — operator clicks Yes before the action fires, so "
    "emitting the ACTION is always safe. Verb mappings:\n"
    " - \"update <stack-name>\" / \"upgrade the <name> stack\" → "
    "`ACTION: update_stack` paired with `ACTION_ITEM: <stack-name>`\n"
    " - \"update <container-name>\" / \"recreate <name>\" → "
    "`ACTION: update_container` + `ACTION_ITEM: <name>`\n"
    " - \"restart <service-or-container-name>\" / \"bounce <name>\" "
    "→ `ACTION: restart_service` for Swarm services OR "
    "`ACTION: restart_container` for plain containers + "
    "`ACTION_ITEM: <name>`\n"
    " - \"remove <container-name>\" / \"delete that orphan\" → "
    "`ACTION: remove_container` + `ACTION_ITEM: <name>`\n"
    " - \"prune docker on <host>\" / \"prune <host>\" → "
    "`ACTION: prune_node` + `ACTION_ITEM: <hostname>`\n"
    " - \"pause every host in <group>\" / \"suspend the <group> "
    "hosts\" → `ACTION: hosts_bulk_pause` (operator picks the group "
    "from the selection chip strip)\n"
    " - \"resume every host\" / \"unpause hosts\" → "
    "`ACTION: hosts_bulk_resume`\n"
    " - \"back up the database now\" / \"create a backup\" / "
    "\"snapshot\" → `ACTION: backup_create`\n"
    " - \"remember that <X>\" / \"add a memory: <X>\" → "
    "`ACTION: ai_memory_create` + `ACTION_ITEM: <memory text>`\n"
    " - \"forget about <X>\" / \"delete the memory <X>\" → "
    "`ACTION: ai_memory_delete` + `ACTION_ITEM: <exact memory "
    "text>`\n"
    " - \"run the <name> schedule now\" / \"fire the <name> "
    "schedule\" → `ACTION: schedule_run_now` + `ACTION_ITEM: "
    "<schedule-name>`\n"
    " - \"clean up stopped containers\" / \"prune stopped\" → "
    "`ACTION: cleanup_stopped` (or `prune_stopped` synonym — both "
    "route to the same SPA flow with inline-confirm chip)\n"
    " - \"mark every notification as read\" / \"clear "
    "notifications\" → `ACTION: mark_all_notifications_read` "
    "(synonyms `clear_notifications` / `notifications_clear_all` "
    "also accepted)\n"
    " - \"pause all hosts\" / \"bulk pause hosts\" → `ACTION: "
    "hosts_bulk_pause` OR `ACTION: bulk_pause_hosts` (same "
    "dispatch — emit whichever feels natural; mirror for resume)\n"
    "ALWAYS pair the destructive action with ACTION_ITEM (or "
    "ACTION_HOSTS for bulk-host ops). If you don't know the exact "
    "name the operator means, ASK them to clarify rather than "
    "emitting a misdirected ACTION.\n\n"
    "MARKDOWN FORMATTING — the SPA renders your reply through a "
    "safe-Markdown subset: fenced code blocks (```), inline code "
    "(`...`), bold (**...**), bullet lists (* / -), and numbered "
    "lists (1. 2. 3.). Use them deliberately:\n"
    " - EVERY shell / linux / bash command goes inside a fenced "
    "code block with the language hint `bash` (or `sh` for POSIX-"
    "only snippets, `yaml` / `json` / `ini` / `toml` for config "
    "snippets). This is NON-NEGOTIABLE — even a single one-line "
    "command (`docker ps`, `systemctl restart sshd`, `apt update`) "
    "goes in a fenced block, NEVER as inline backticks. The SPA "
    "renders fenced blocks with a monospace font, horizontal "
    "scroll, AND a one-click copy button — that experience only "
    "triggers for triple-backtick fenced blocks, NOT inline "
    "backticks. Inline backticks for a command rob the operator "
    "of the copy button.\n"
    " - DO NOT INDENT lines inside a fenced code block. The opening "
    "``` and closing ``` go at column 0, and every line of the "
    "code body starts at column 0 too — even when the fenced block "
    "follows a colon, a bullet, or a numbered-list item. Wrong: "
    "`    Environment=\"FOO=bar\"` (4 leading spaces). Right: "
    "`Environment=\"FOO=bar\"` (no leading spaces). Indent inside "
    "the body ONLY when the language genuinely requires it (e.g. "
    "Python function bodies, YAML nested keys) — never as a "
    "stylistic prefix to align with surrounding prose.\n"
    " - When the answer involves multiple commands run in sequence, "
    "put them in ONE fenced block separated by `&&` (so a single "
    "Copy click + paste runs the chain) OR as separate consecutive "
    "fenced blocks (when each command needs to be evaluated "
    "individually before running the next). Default to `&&` chaining "
    "for the common 'apply config + restart + verify' shape.\n"
    " - INLINE code (`...`) is for short non-command references "
    "inside prose: a flag name (`--no-trunc`), a file path "
    "(`/etc/docker/daemon.json`), an env var (`DOCKER_HOST`), a "
    "field name from a JSON response. Commands DO NOT belong in "
    "inline backticks — even short ones. The split is: 'thing the "
    "operator would type / copy' = fenced; 'thing the operator "
    "would read / refer to' = inline.\n"
    " - Use bold sparingly for section headers inside a longer "
    "answer. Avoid bold-spam.\n\n"
    "MEMORY PROTOCOL — when you learn something specific to THIS "
    "OmniGrid deployment that future you should remember to avoid "
    "repeating mistakes (a recurring command pattern, a quirk of "
    "the operator's environment, a non-obvious lesson from a fix "
    "that just landed, an alias / mapping the operator uses, a "
    "host-specific gotcha), emit a `MEMORY: <one-line note>` line "
    "at the END of your reply (after any ACTION / HOSTS / "
    "ACTION_HOSTS lines). The SPA persists every MEMORY line "
    "server-side and re-injects them into your system prompt for "
    "every subsequent palette call, so you can self-improve over "
    "the deployment's lifetime. Rules:\n"
    " - ONLY emit a MEMORY when there's a NEW durable lesson — not "
    "for every reply. Most replies should not emit one. If the "
    "knowledge is already obvious from the existing system prompt "
    "or a previous memory, do NOT re-emit.\n"
    " - One memory per `MEMORY:` line. Multiple lessons from the "
    "same reply emit multiple `MEMORY:` lines.\n"
    " - Lead with the lesson. Format: `MEMORY: <imperative or "
    "fact>. Why: <one phrase>.` Examples: `MEMORY: When restarting "
    "Beszel agent, always SSH to its container host (not the "
    "Swarm manager). Why: agent runs as a docker container on the "
    "target machine, not in Swarm.` / `MEMORY: Operator's PVE "
    "node 'pve01.example.com' uses ZFS pool 'tank' for VM disks. "
    "Why: monitoring queries should target tank/* not local-lvm.`\n"
    " - DO NOT emit a MEMORY containing operator-private "
    "information (passwords, API tokens, IP addresses outside "
    "192.X.X.X / 10.X.X.X / *.example.com placeholders). Memories "
    "ship to the persisted store as plain text — treat them as "
    "shareable facts about the deployment.\n"
    " - When you receive a memory in the system prompt that turns "
    "out to be wrong, emit `MEMORY-FORGET: <exact memory text>` "
    "to flag it for removal (the SPA confirms with the operator "
    "before deleting).\n\n"
    "AVAILABLE ACTIONS (end reply with `ACTION: <id>` for each):\n"
    " - mark_all_notifications_read — mark every notification as read\n"
    " - refresh — refresh the current view's data from the backend\n"
    " - reload — full SPA reload (Ctrl-R equivalent)\n"
    " - theme_dark — switch UI to dark theme\n"
    " - theme_light — switch UI to light theme\n"
    " - theme_auto — let UI follow OS theme\n"
    " - open_notifications — open the notifications drawer\n"
    " - show_hotkeys — show the keyboard-shortcuts modal\n"
    " - cleanup_stopped — remove every stopped / failed / orphaned container the dashboard can see. Operator-friendly synonyms: 'cleanup', 'clean up', 'purge', 'prune', 'remove stopped containers', 'package cleanup' (loose match — there is no package-level cleanup, only container cleanup). (Destructive — the SPA still confirms before issuing the rm batch, so picking this is safe.)\n"
    " - update_all_updatable — pull updates for every stack and standalone container that currently has an available update. Operator synonyms: 'update stacks', 'update all', 'update everything', 'pull updates', 'upgrade', 'upgrade everything', 'deploy updates', 'apply updates'. The SPA dedupes by stack id (one POST per stack, not per service), shows a confirm popup listing each affected stack/container, then issues the batch. (Destructive — the SPA confirms before issuing the update batch, so picking this is safe.)\n"
    " - sign_out — log out of OmniGrid\n"
    " - scan_ports — run an on-demand TCP-connect port scan. Synonyms: 'scan ports', 'port scan', 'tcp scan', 'discover open ports', 'nmap'. When the operator names a host to scan, emit BOTH `ACTION: scan_ports` AND a SEPARATE `ACTION_HOSTS: <host_id>` line (NOT a `HOSTS:` line — that one is reserved for disk-projection charts and would render an unrelated chart on the response). The SPA resolves the scan target via: (1) ACTION_HOSTS first id, (2) host drawer if open, (3) operator toast. The host drawer does NOT need to be open. Example reply: 'Scanning ports on opnsense.\\nACTION: scan_ports\\nACTION_HOSTS: opnsense'.\n"
    " - test_portainer — re-test the Portainer connection. Navigates to Admin → Portainer and kicks the probe. Synonyms: 'test portainer', 'portainer test', 'check portainer'.\n"
    " - test_oidc — re-test the Authentik OIDC connection. Navigates to Admin → Authentik OIDC and kicks the probe. Synonyms: 'test oidc', 'test authentik', 'test sso'.\n"
    " - test_beszel — re-test the Beszel hub connection. Navigates to Admin → Providers → Beszel and kicks the probe. Synonyms: 'test beszel', 'check beszel'.\n"
    " - test_pulse — re-test the Pulse connection. Navigates to Admin → Providers → Pulse and kicks the probe. Synonyms: 'test pulse', 'check pulse'.\n"
    " - test_webmin — re-test the Webmin connection. Navigates to Admin → Providers → Webmin and kicks the probe. Synonyms: 'test webmin', 'check webmin'.\n"
    " - test_snmp — re-test the SNMP host-stats provider against the host's configured target. Navigates to Admin → Hosts and kicks the per-row test. Synonyms: 'test snmp', 'check snmp'.\n"
    " - test_ping — re-test the Ping host-stats provider for the host. Navigates to Admin → Hosts and kicks the per-row test. Synonyms: 'test ping', 'check reachability'.\n"
    " - test_asset_inventory — refresh + verify the upstream asset-inventory connection. Navigates to Admin → Asset Inventory and kicks the probe. Synonyms: 'test asset inventory', 'check assets'.\n"
    " - test_apprise — send a test notification through every enabled medium so the operator confirms Apprise / in-app delivery is working. Synonyms: 'test notification', 'send test', 'test apprise'.\n"
    " - retag_image — switch a container or stack-managed item's image tag to a different floating tag (e.g. switch `komodo-core:2.0.0-dev` to `:2` for v2-line patch updates without bumping to `:latest`). Synonyms: 'switch tag', 'retag', 'pin to tag', 'change image tag', 'track tag'. ALWAYS pair with `ACTION_TAG: <new_tag>` (the destination tag — bare value, e.g. `2`, no leading `:`). When the operator names a specific item in the query (e.g. \"switch komodo-core to :2\"), ALSO emit `ACTION_ITEM: <name-or-id>` so the SPA targets that item directly; otherwise the SPA defaults to the open item drawer. Example reply: 'Switching komodo-core from :2.0.0-dev to :2.\\nACTION: retag_image\\nACTION_ITEM: komodo-core\\nACTION_TAG: 2'. (Destructive — recreates the container or redeploys the stack; operator confirms via the inline-confirm chip in the AI sidebar before it fires.)\n"
    " - schedule_create — create a new recurring schedule. ALWAYS pair with `ACTION_DATA: {<json>}` carrying `{name, kind, interval_seconds, enabled?, params?, run_at_hhmm?, cadence_mode?, days_of_week?, day_of_month?}`. `kind` MUST be one of the registered schedule kinds (see Admin → Schedules → Kind dropdown for the canonical list). Example reply: 'Creating a daily 01:00 backup schedule.\\nACTION: schedule_create\\nACTION_DATA: {\"name\":\"nightly-backup\",\"kind\":\"backup\",\"interval_seconds\":86400,\"cadence_mode\":\"daily\",\"run_at_hhmm\":\"01:00\"}'. Non-destructive — fires immediately without an inline confirm.\n"
    " - schedule_update — update an existing schedule's fields. ALWAYS pair with `ACTION_DATA: {<json>}` carrying `{id?: int, name?: str, ...changed fields}`. Either `id` OR `name` identifies the schedule; the rest are the fields to overwrite. Example reply: 'Bumping the gather refresh to every 10 minutes.\\nACTION: schedule_update\\nACTION_DATA: {\"name\":\"gather-refresh\",\"interval_seconds\":600}'. Non-destructive — fires immediately without an inline confirm.\n"
    " - schedule_delete — delete an existing schedule. ALWAYS pair with `ACTION_DATA: {<json>}` carrying `{id?: int, name?: str}` to identify the schedule to remove. Example reply: 'Deleting the experimental schedule.\\nACTION: schedule_delete\\nACTION_DATA: {\"name\":\"experimental-prune\"}'. (DESTRUCTIVE — the operator confirms via the inline-confirm chip in the AI sidebar before the delete fires.)\n"
    "Example single-action reply: 'I'll mark every notification as read for you.\\n"
    "ACTION: mark_all_notifications_read'\n"
    "Example multi-action reply (\"refresh and cleanup\"): 'Refreshing the dashboard, then opening the cleanup confirm.\\n"
    "ACTION: refresh\\n"
    "ACTION: cleanup_stopped'\n"
    "If no action fits, omit the ACTION line entirely.\n\n"
    "HOSTS PROTOCOL — emit a `HOSTS: <id1>, <id2>, <id3>` line when "
    "the operator's question can be enriched by an INLINE CHART for "
    "the named hosts. Pair it with a `CHART: <kind>` line on the "
    "next line to pick the chart type. SUPPORTED KINDS:\n"
    " - `disk_projection` (default — emitted when CHART: is omitted) "
    "for DISK USAGE / STORAGE EXHAUSTION / DISK CAPACITY questions. "
    "Trigger phrases: 'low on disk', 'out of disk space', 'running "
    "out of storage', 'disk fill', 'disk usage', 'disk projection', "
    "'when will X run out', 'top hosts by disk'. Renders historical "
    "disk-used % + linear-projection forecast of when the disk fills "
    "up.\n"
    " - `memory_history` for MEMORY USAGE TIME-SERIES questions. "
    "Trigger phrases: 'memory graph', 'show ram usage over time', "
    "'memory usage in the past 24 hours', 'plot memory for X', "
    "'memory chart'. Renders memory-used % over the last 24 hours "
    "for each named host.\n"
    " - `cpu_history` for CPU USAGE TIME-SERIES questions. Trigger "
    "phrases: 'cpu graph', 'show cpu usage over time', 'plot cpu "
    "for X'. Same shape as memory_history but for cpu_percent.\n"
    "The SPA picks up the HOSTS + CHART lines, strips them from the "
    "visible text, and renders the requested inline charts for each "
    "host below the answer. EMIT THE CHART KIND THAT MATCHES THE "
    "QUESTION — rendering a disk projection next to a memory question "
    "is a UX bug. DO NOT emit HOSTS at all for: log questions ('any "
    "errors?', 'which hosts have warnings?'), network questions, "
    "port-scan questions, general-status questions ('which hosts are "
    "down?', 'which are paused?'), or any question that lists hosts "
    "but doesn't ask about disk / memory / CPU time-series. When in "
    "doubt, OMIT the HOSTS line — the prose answer alone is correct; "
    "the chart is an opt-in enrichment. When you do emit HOSTS, list "
    "each host's curated `id` field — NOT its label, alias, or "
    "display name — in the order they appear in your prose. Maximum "
    "8 ids. Use ONLY ids that appear in the supplied 'Hosts:' "
    "context block — never invent ids the SPA doesn't know about. "
    "The HOSTS / CHART lines and the ACTION line are INDEPENDENT — "
    "all can appear (HOSTS first, then CHART, then ACTION). "
    "Example DISK reply: 'Top 3 hosts low on disk: 1. nas01 — 92% "
    "used, 2. db02 — 88%, 3. cache03 — 85%.\\nHOSTS: nas01, db02, "
    "cache03'. Example MEMORY-CHART reply: 'Here is opnsense's "
    "memory usage over the past 24 hours.\\nHOSTS: opnsense\\nCHART: "
    "memory_history'. Example CPU-CHART reply: 'Here is opnsense's "
    "CPU usage over the past 24 hours.\\nHOSTS: opnsense\\nCHART: "
    "cpu_history'.\n\n"
    "MANDATORY CHART KIND PAIRING — when you emit `HOSTS:` for ANY "
    "memory / RAM / CPU / load question, you MUST also emit the "
    "matching `CHART: <kind>` line on the line right after HOSTS. "
    "OMITTING the CHART line for a memory / CPU question makes the "
    "SPA render a disk-projection chart by default — visibly wrong, "
    "user-flagged repeatedly. Hard rule: 'memory' / 'ram' / 'mem' "
    "in the question → `CHART: memory_history`. 'cpu' / 'load' / "
    "'processor' in the question → `CHART: cpu_history`. 'disk' / "
    "'storage' / 'space' in the question → `CHART: disk_projection` "
    "(or omit CHART since disk is the default). When unsure which "
    "kind, OMIT THE HOSTS LINE ENTIRELY rather than picking the "
    "wrong chart kind — a prose-only answer is always better than "
    "a wrong chart."
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


def parse_palette_actions(text: str) -> tuple[list[str], str]:
    """Extract every `ACTION: <id>` (and `ACTION: <id1>, <id2>` CSV)
    trailer from a palette response. Returns ``(action_ids,
    cleaned_text)`` — empty list when no whitelisted action found;
    `cleaned_text` is the visible body with every ACTION block
    stripped from the first ACTION line onwards.

    Multi-action support: the model can emit multiple ACTION: lines
    (one per action, fired in order) for combined queries like
    "refresh and cleanup". Comma-separated single lines also accepted
    (`ACTION: refresh, cleanup_stopped`). Unknown / mistyped action
    ids are silently dropped from the list. Duplicates collapse to
    first occurrence.

    Forgiving: matches lines at the strict end of the body OR
    anywhere-mid-body, with optional surrounding whitespace,
    backticks, asterisks, or trailing punctuation.
    """
    if not text:
        return [], text or ""
    actions: list[str] = []
    seen: set[str] = set()
    # Find every ACTION: <body> line — body may be a single id or a
    # comma/whitespace-separated list. Re-tokenise per line so
    # mixed shapes work (`ACTION: refresh\nACTION: cleanup_stopped`
    # OR `ACTION: refresh, cleanup_stopped`).
    line_re = _re.compile(
        r"(?:^|\n)[\s`*]*ACTION\s*:\s*(?P<body>[^\n]+?)[\s`.*]*$",
        _re.IGNORECASE | _re.MULTILINE,
    )
    first_idx: int | None = None
    for m in line_re.finditer(text):
        if first_idx is None:
            first_idx = m.start()
        body = m.group(1).strip()
        for tok in _re.split(r"[,\s]+", body):
            cand = tok.strip().lower()
            if not cand:
                continue
            if cand in ALLOWED_PALETTE_ACTIONS and cand not in seen:
                seen.add(cand)
                actions.append(cand)
    if not actions:
        return [], text
    # Strip from the first ACTION: line onwards so the visible
    # answer doesn't carry the protocol trailer.
    cleaned = text[: first_idx].rstrip() if first_idx is not None else text
    return actions, cleaned


def parse_palette_action(text: str) -> tuple[str, str]:
    """Backward-compatible single-action wrapper around
    :func:`parse_palette_actions`. Returns the FIRST action id (or
    empty string if none found) + the cleaned text. Existing callers
    that only handle one action keep working; new callers should
    use `parse_palette_actions` directly.
    """
    actions, cleaned = parse_palette_actions(text)
    return (actions[0] if actions else ""), cleaned


def parse_palette_hosts(text: str, known_ids: set[str] | None = None) -> tuple[list[str], str]:
    """Extract the optional `HOSTS: <id1>, <id2>, ...` trailer from a
    palette response. Returns ``(host_ids, cleaned_text)``.

    Tolerant matcher mirroring `parse_palette_action`: matches the
    line at the strict end OR anywhere-mid-body, optional surrounding
    whitespace / backticks / asterisks. Tokens split on commas /
    whitespace; trailing punctuation stripped. When ``known_ids`` is
    supplied, IDs not in that set are dropped (the model occasionally
    invents names the SPA doesn't have curated rows for — better to
    silently drop than render a chart for a phantom host).

    Cap of 8 hosts mirrors the prompt's instruction; extra ids past
    8 are dropped.
    """
    if not text:
        return [], text or ""
    m = _re.search(
        r"(?:^|\n)[\s`*]*HOSTS\s*:\s*(?P<body>.+?)[\s`.*]*$",
        text, _re.IGNORECASE | _re.MULTILINE,
    )
    if not m:
        return [], text
    raw = m.group("body")
    # Split on commas first (preferred), fall back to whitespace.
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
    else:
        parts = raw.split()
    # Strip trailing punctuation / quote chars / backticks per token.
    cleaned_ids: list[str] = []
    seen: set[str] = set()
    for p in parts:
        token = p.strip().strip("`'\"*.,;").strip()
        if not token:
            continue
        if known_ids is not None and token not in known_ids:
            continue
        if token in seen:
            continue
        seen.add(token)
        cleaned_ids.append(token)
        if len(cleaned_ids) >= 8:
            break
    cleaned_text = text[: m.start()].rstrip()
    return cleaned_ids, cleaned_text


_VALID_CHART_KINDS: frozenset[str] = frozenset({
    "disk_projection",  # default — historical disk-used % + linear forecast
    "memory_history",  # mem-used % time-series, last 24 h
    "cpu_history",  # cpu_percent time-series, last 24 h
})

# AI-emitted aliases mapped to canonical kind names. The model often
# emits short forms ("memory", "cpu", "ram") despite the prompt telling
# it to use the full _history suffix. Treat these as equivalent so a
# minor compliance miss doesn't fall through to the default disk-
# projection (visibly wrong chart for a memory question). Disk synonyms
# stay mapped to disk_projection — they're the legacy default.
_CHART_KIND_ALIASES: dict[str, str] = {
    "memory": "memory_history",
    "memory-usage": "memory_history",
    "memory_usage": "memory_history",
    "ram": "memory_history",
    "ram_history": "memory_history",
    "mem": "memory_history",
    "mem_history": "memory_history",
    "cpu": "cpu_history",
    "cpu-usage": "cpu_history",
    "cpu_usage": "cpu_history",
    "cpu_load": "cpu_history",
    "processor": "cpu_history",
    "processor_history": "cpu_history",
    "disk": "disk_projection",
    "disk-projection": "disk_projection",
    "disk_usage": "disk_projection",
    "storage": "disk_projection",
}


def parse_palette_chart_kind(text: str) -> tuple[str, str]:
    """Extract the optional `CHART: <kind>` trailer from a palette
    response. Returns ``(chart_kind, cleaned_text)``.

    Pairs with :func:`parse_palette_hosts` — when the AI emits BOTH
    HOSTS + CHART, the SPA renders the requested chart kind for each
    listed host instead of the default disk-projection. When CHART is
    omitted (or unrecognised), the caller defaults to
    `disk_projection` for back-compat with existing AI training.

    Validation: the value must be one of `_VALID_CHART_KINDS`. An
    invalid kind (typo, hallucinated kind) is silently dropped — the
    SPA falls back to the default rather than rendering an empty
    "unknown chart" shell.

    Tolerant matcher mirrors `parse_palette_hosts`: matches end-of-line
    or anywhere-mid-body with optional surrounding whitespace /
    backticks / asterisks. Strips the matched line from the cleaned
    text whether or not the kind is recognised, so a typo'd CHART:
    line never leaks into the visible answer.
    """
    if not text:
        return "", text or ""
    m = _re.search(
        r"(?:^|\n)[\s`*]*CHART\s*:\s*(?P<kind>[A-Za-z_][A-Za-z0-9_-]*)[\s`.*]*$",
        text, _re.IGNORECASE | _re.MULTILINE,
    )
    if not m:
        return "", text
    raw = m.group("kind").strip().lower()
    cleaned_text = text[: m.start()].rstrip()
    if raw in _VALID_CHART_KINDS:
        return raw, cleaned_text
    # Tolerate operator-friendly aliases the AI sometimes emits
    # ("memory" / "ram" / "cpu" / "disk") instead of the canonical
    # `*_history` / `*_projection` form. Falls through to "" only
    # when the alias is genuinely unrecognised.
    aliased = _CHART_KIND_ALIASES.get(raw)
    if aliased:
        return aliased, cleaned_text
    return "", cleaned_text


def parse_palette_action_hosts(text: str, known_ids: set[str] | None = None) -> tuple[list[str], str]:
    """Extract the optional `ACTION_HOSTS: <id1>, <id2>, ...` trailer
    from a palette response. Returns ``(host_ids, cleaned_text)``.

    Distinct from :func:`parse_palette_hosts` — that one's HOSTS line
    drives disk-projection chart rendering on the SPA. ACTION_HOSTS
    is the action-target channel: when the AI emits `ACTION:
    scan_ports` paired with `ACTION_HOSTS: opnsense`, the SPA fires
    the scan against `opnsense` WITHOUT rendering disk charts. Pre-
    fix the AI was instructed to overload HOSTS for action-target
    hosts — operators saw a confusing disk-projection chart appear
    when they asked for a port scan.

    Same matcher / tokeniser shape as `parse_palette_hosts` so the
    cap (8 ids) + known_ids filter behave identically.
    """
    if not text:
        return [], text or ""
    m = _re.search(
        r"(?:^|\n)[\s`*]*ACTION_HOSTS\s*:\s*(?P<body>.+?)[\s`.*]*$",
        text, _re.IGNORECASE | _re.MULTILINE,
    )
    if not m:
        return [], text
    raw = m.group("body")
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
    else:
        parts = raw.split()
    cleaned_ids: list[str] = []
    seen: set[str] = set()
    for p in parts:
        token = p.strip().strip("`'\"*.,;").strip()
        if not token:
            continue
        if known_ids is not None and token not in known_ids:
            continue
        if token in seen:
            continue
        seen.add(token)
        cleaned_ids.append(token)
        if len(cleaned_ids) >= 8:
            break
    cleaned_text = text[: m.start()].rstrip()
    return cleaned_ids, cleaned_text


def parse_palette_action_tag(text: str) -> tuple[str, str]:
    """Extract the optional ``ACTION_TAG: <new_tag>`` trailer from a
    palette response. Returns ``(tag, cleaned_text)``.

    Used by the ``retag_image`` action — the AI emits
    ``ACTION: retag_image`` paired with ``ACTION_TAG: 2`` (or
    ``latest`` / ``v2-stable`` / etc.) and the SPA threads the tag
    into the same retag endpoint the drawer's inline popover uses.
    Validates against the Docker tag charset
    (``[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}``); invalid → empty string +
    untouched text so the caller can fall back to the operator-typed
    default. Sibling of ``parse_palette_action_hosts`` — same
    cleanup-text contract so the SPA's downstream renderer doesn't
    surface the directive line as prose.
    """
    if not text:
        return "", text or ""
    m = _re.search(
        r"(?:^|\n)[\s`*]*ACTION_TAG\s*:\s*(?P<body>.+?)[\s`.*]*$",
        text, _re.IGNORECASE | _re.MULTILINE,
    )
    if not m:
        return "", text
    raw = m.group("body").strip().strip("`'\"*.,;").strip()
    cleaned_text = text[: m.start()].rstrip()
    if not raw:
        return "", cleaned_text
    if len(raw) > 128 or not _re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", raw):
        return "", cleaned_text
    return raw, cleaned_text


def parse_palette_action_item(text: str) -> tuple[str, str]:
    """Extract the optional ``ACTION_ITEM: <name-or-id>`` trailer from
    a palette response. Returns ``(item_token, cleaned_text)``.

    Used by the ``retag_image`` action when the operator names a
    specific container/stack in the query and the AI surfaces the
    target explicitly. The SPA resolves the token by exact-match
    against item ids first, then by case-insensitive name match.
    Sibling of ``parse_palette_action_hosts`` but for items (not
    hosts) — keeps the action-target channel separated from the
    HOSTS line that drives disk-projection charts.
    """
    if not text:
        return "", text or ""
    m = _re.search(
        r"(?:^|\n)[\s`*]*ACTION_ITEM\s*:\s*(?P<body>.+?)[\s`.*]*$",
        text, _re.IGNORECASE | _re.MULTILINE,
    )
    if not m:
        return "", text
    raw = m.group("body").strip().strip("`'\"*.,;").strip()
    cleaned_text = text[: m.start()].rstrip()
    return raw, cleaned_text


def parse_palette_action_data(text: str) -> tuple[Optional[dict], str]:
    """Extract the optional ``ACTION_DATA: {<json>}`` trailer from a
    palette response. Returns ``(payload_dict_or_None, cleaned_text)``.

    Used by parameterised actions whose payload is a JSON object
    (currently `schedule_create` / `schedule_update` / `schedule_delete`
    — others may follow). Distinct from `ACTION_TAG` / `ACTION_HOSTS`
    / `ACTION_ITEM` which carry single-value strings; ACTION_DATA is
    the structured-payload channel.

    The matcher accepts JSON delimited by `{` / `}` braces with naive
    brace-balancing — sufficient for one-line payloads the prompt
    teaches the AI to emit. Validates via `json.loads`; invalid JSON
    → returns None + cleans the directive line out of the text so
    the SPA-side renderer doesn't surface the malformed payload as
    prose.
    """
    if not text:
        return None, text or ""
    import json as _json
    m = _re.search(
        r"(?:^|\n)[\s`*]*ACTION_DATA\s*:\s*(?P<body>\{.+?})\s*$",
        text, _re.IGNORECASE | _re.MULTILINE | _re.DOTALL,
    )
    if not m:
        return None, text
    raw = m.group("body").strip()
    cleaned_text = text[: m.start()].rstrip()
    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError:
        return None, cleaned_text
    if not isinstance(data, dict):
        return None, cleaned_text
    return data, cleaned_text


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
    m = _re.match(r"^(?P<verb>pause|resume)\s*:\s*(?P<scope>.*)$", cand, _re.IGNORECASE)
    if not m:
        return "", "", f"Model didn't return a valid DSL line — got: {cand[:120]}"
    dsl = f"{m.group('verb').lower()}: {m.group('scope').strip()}".rstrip()
    explanation = lines[1] if len(lines) > 1 else ""
    return dsl, explanation, ""


def parse_palette_memories(text: str) -> tuple[list[str], list[str], str]:
    """Parse trailing ``MEMORY: ...`` and ``MEMORY-FORGET: ...`` lines
    off the assistant reply. Returns ``(memories_to_save, memories_to_forget,
    cleaned_text)``. Each list element is the raw single-line text the AI
    emitted (one memory per line). Lines that pass through with the
    `MEMORY:` / `MEMORY-FORGET:` prefix are stripped from the visible
    reply; everything else is preserved verbatim.

    Defensive: caps memory body at 500 chars to discourage prose-bloat
    from a chatty model. Empty bodies after the colon are ignored.
    """
    if not text:
        return [], [], text or ""
    saves: list[str] = []
    forgets: list[str] = []
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        # Match `MEMORY-FORGET:` BEFORE `MEMORY:` (longest-prefix wins).
        upper = stripped.upper()
        if upper.startswith("MEMORY-FORGET:"):
            body = stripped.split(":", 1)[1].strip()
            if body:
                forgets.append(body[:500])
            continue
        if upper.startswith("MEMORY:"):
            body = stripped.split(":", 1)[1].strip()
            if body:
                saves.append(body[:500])
            continue
        out_lines.append(line)
    cleaned = "\n".join(out_lines).rstrip()
    return saves, forgets, cleaned


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


_T_FIELD = TypeVar("_T_FIELD")


def _typed_field(src, key: str, expected_type: type[_T_FIELD]) -> _T_FIELD | None:
    """Return ``src[key]`` when ``src`` is a dict AND the value is an
    instance of ``expected_type``; otherwise None. Used in place of the
    inline ``d.get(k) if isinstance(d.get(k), T) else None`` pattern so
    the type-checker narrows cleanly at every consumer site (the inline
    ternary version returns Any | None which then poisons every
    downstream `.get()` / subscript call with "member None doesn't
    have attribute" diagnostics).

    Generic ``_T_FIELD`` bound to ``expected_type`` so callers get a
    properly-narrowed ``dict | None`` / ``list | None`` at the call
    site without inlining the isinstance dance."""
    if not isinstance(src, dict):
        return None
    v = src.get(key)
    return v if isinstance(v, expected_type) else None


def build_palette_user_prompt(query: str, ctx: dict | None,
                              conversation: list | None = None) -> str:
    """Per-call user prompt for `/api/ai/palette`. Caps host + item
    lists at 30 each (~3k tokens for a fully-populated 30-host fleet).

    ``conversation`` carries prior turns of the multi-turn AI sidebar
    session as ``[{role: "user"|"assistant", text: "..."}]`` pairs.
    Capped at the last 12 turns server-side to keep token budget
    reasonable on long chats; the SPA also caps client-side. Each
    turn is rendered as a `User:` / `Assistant:` line so the model
    sees the chat history before the new query.
    """
    parts: list[str] = []
    if isinstance(conversation, list) and conversation:
        history_lines: list[str] = []
        for turn in conversation[-12:]:
            if not isinstance(turn, dict):
                continue
            role = (turn.get("role") or "").strip().lower()
            text = (turn.get("text") or "").strip()
            if not text or role not in ("user", "assistant"):
                continue
            label = "User" if role == "user" else "Assistant"
            # Cap each prior turn at 600 chars so an old long
            # response doesn't dominate the prompt.
            if len(text) > 600:
                text = text[:600] + "…"
            history_lines.append(f"{label}: {text}")
        if history_lines:
            parts.append("Prior conversation:\n" + "\n".join(history_lines))
    parts.append(f"Operator query: {query}")
    if isinstance(ctx, dict):
        view = ctx.get("view")
        if view:
            parts.append(f"Current view: {view}")
        # Current time block. Threaded by the Telegram listener (per
        # `_build_telegram_ai_context`) so the AI can answer
        # "what time is it" / "what's today's date" without falling
        # back to its training-cutoff guess. Same opt-in shape as
        # weather: when absent the model should say "I don't see a
        # current-time block from this surface" rather than guessing.
        # Fields: utc_iso / local_iso / timezone / utc_offset / weekday.
        tinfo = _typed_field(ctx, "time", dict)
        if tinfo and (tinfo.get("local_iso") or tinfo.get("utc_iso")):
            bits = []
            if tinfo.get("weekday"):
                bits.append(str(tinfo["weekday"]))
            if tinfo.get("local_iso"):
                bits.append(str(tinfo["local_iso"]))
            tz_seg = ""
            if tinfo.get("timezone"):
                tz_seg = f" ({tinfo['timezone']}"
                if tinfo.get("utc_offset"):
                    tz_seg += f" UTC{tinfo['utc_offset']}"
                tz_seg += ")"
            line = " · ".join(bits) + tz_seg
            utc_seg = ""
            if tinfo.get("utc_iso") and tinfo.get("local_iso") \
                and tinfo["utc_iso"] != tinfo["local_iso"]:
                utc_seg = f" / UTC {tinfo['utc_iso']}"
            parts.append(
                "Current time (server clock — answer naturally using this, do NOT refuse "
                "'I don't have a real-time clock'): " + line + utc_seg
            )
        # Authoritative fleet counts — both callers (SPA palette +
        # Telegram listener) thread these so the AI doesn't answer
        # "how many hosts" with the sample-block size. Operator-flagged:
        # 183 hosts configured, AI replied "30" because that's all it
        # could see in the sample. Emit BEFORE the sample block so the
        # model reads the count before the records.
        hosts_total = ctx.get("hosts_total")
        hosts_enabled = ctx.get("hosts_enabled")
        hosts_sample_cap = ctx.get("hosts_sample_cap") or 30
        if isinstance(hosts_total, int) and hosts_total > 0:
            enabled_seg = (f" ({hosts_enabled} enabled)"
                           if isinstance(hosts_enabled, int) else "")
            parts.append(
                f"Fleet counts (AUTHORITATIVE — use these to answer 'how many "
                f"hosts' / 'count' / 'total' questions, NOT the sample-records "
                f"block below):\n"
                f"  - hosts_total: {hosts_total}{enabled_seg}\n"
                f"  - hosts shown below: capped at top {hosts_sample_cap} for "
                f"prompt-token budget; the rest exist but aren't enumerated. "
                f"NEVER answer 'we have N hosts' where N is the visible-sample "
                f"size — always cite hosts_total."
            )
        hosts = _typed_field(ctx, "hosts", list)
        if hosts:
            parts.append(_format_records_block(
                "Available hosts (sample — top "
                + str(hosts_sample_cap) + " of " + str(hosts_total or len(hosts))
                + " total)",
                "id, label, status, cpu_pct, mem_pct, disk_pct, "
                "disk_free_gb, disk_total_gb, uptime_s, paused, providers",
                hosts[:30],
            ))
        items = _typed_field(ctx, "items", list)
        if items:
            parts.append(_format_records_block(
                "Available items",
                "name, status, health, type, replicas, desired, "
                "update_available",
                items[:30],
            ))
        weather = _typed_field(ctx, "weather", dict)
        if weather:
            # Compact one-line weather summary — OmniGrid's topbar
            # weather widget (Open-Meteo proxy) is a real product
            # feature; the AI is allowed to answer weather questions
            # using THIS payload when it's present. When this block is
            # ABSENT it means the operator hasn't enabled the topbar
            # widget — in that case the AI should say "weather widget
            # is disabled — enable it via Settings → Profile" rather
            # than refusing as off-topic. Field names match the SPA's
            # `_buildAiPaletteContext` mapping of the /api/weather
            # response (temp_c → temperature, code → weather_code,
            # condition string from the WMO-code lookup table).
            bits = []
            if weather.get("label"):
                bits.append(str(weather["label"]))
            # Two callers feed this block: the SPA passes `temperature`
            # (already converted to the user's °C / °F pref) and the
            # Telegram listener passes the raw `temp_c` from /api/weather.
            # Accept either so neither caller has to massage the payload
            # just for the prompt builder.
            temp_val = weather.get("temperature")
            if temp_val is None:
                temp_val = weather.get("temp_c")
            if temp_val is not None:
                bits.append(f"{temp_val}{weather.get('unit') or '°C'}")
            if weather.get("condition"):
                bits.append(str(weather["condition"]))
            if weather.get("humidity") is not None:
                bits.append(f"{weather['humidity']}% humidity")
            if weather.get("wind_kmh") is not None:
                bits.append(f"{weather['wind_kmh']} km/h wind")
            parts.append(
                "Current weather (from OmniGrid topbar widget — answer naturally using these "
                "values, do NOT refuse). USE WEATHER-RELEVANT EMOJIS when describing conditions "
                "(☀️ clear / ⛅ partly cloudy / ☁️ overcast / 🌧️ rain / ⛈️ thunderstorm / "
                "❄️ snow / 🌫️ fog / 💨 windy) and a 🌡️ before the temperature, 💧 before "
                "humidity, 💨 before wind speed — keep the prose itself natural, just sprinkle "
                "the emojis where they reinforce the value. Example shape: "
                "'Currently in Cairo: ☀️ clear · 🌡️ 29.7°C · 💧 26% humidity · 💨 10 km/h'. "
                "Values: " + " · ".join(bits)
            )
            # Daily forecast — when present, render up to 7 days so the
            # AI can answer "next 5 days" / "tomorrow" / "this week"
            # questions with real values instead of refusing.
            forecast = weather.get("forecast")
            if isinstance(forecast, list) and forecast:
                lines = []
                for d in forecast[:7]:
                    if not isinstance(d, dict):
                        continue
                    bits2 = []
                    if d.get("date"):
                        bits2.append(str(d["date"]))
                    if d.get("condition"):
                        bits2.append(str(d["condition"]))
                    if d.get("temp_min_c") is not None and d.get("temp_max_c") is not None:
                        bits2.append(f"{d['temp_min_c']}–{d['temp_max_c']}°C")
                    elif d.get("temp_max_c") is not None:
                        bits2.append(f"max {d['temp_max_c']}°C")
                    if d.get("precip_mm") is not None and d["precip_mm"] > 0:
                        bits2.append(f"{d['precip_mm']} mm rain")
                    if bits2:
                        lines.append("  - " + " · ".join(bits2))
                if lines:
                    parts.append(
                        "Daily forecast (from OmniGrid topbar widget — use these values to answer "
                        "multi-day questions like 'next 5 days' / 'this week' / 'tomorrow'):\n"
                        + "\n".join(lines)
                    )
        # Backups summary — sqlite-zip backups (Admin → Backup) AND
        # Settings-as-Code JSON snapshots (Admin → Config Backup).
        # Operator-flagged: AI was answering "I don't have access to
        # the history of backup jobs" when asked "what's the latest
        # backup?". The SPA forwards the latest 5 of each list when
        # available; render a compact summary the AI can answer
        # freshness / count / latest-name questions from. When NEITHER
        # list is present in ctx, the AI should say "no backups have
        # been taken yet — create one via Admin → Backup or Admin →
        # Config Backup" rather than refusing as off-topic.
        backups = _typed_field(ctx, "backups", dict)
        if backups:
            _sqlite_raw = backups.get("sqlite")
            sqlite_list: list = _sqlite_raw if isinstance(_sqlite_raw, list) else []
            _config_raw = backups.get("config")
            config_list: list = _config_raw if isinstance(_config_raw, list) else []
            sqlite_count = backups.get("sqlite_count") or len(sqlite_list)
            config_count = backups.get("config_count") or len(config_list)
            block_lines: list[str] = [
                "Backups summary (Admin → Backup + Admin → Config Backup):",
            ]
            if sqlite_list:
                latest = sqlite_list[0]
                block_lines.append(
                    f"  - SQLite backup zips ({sqlite_count} recent): latest = "
                    f"`{latest.get('name', '?')}`, "
                    f"size = {int(latest.get('size') or 0)} bytes, "
                    f"mtime epoch = {int(latest.get('mtime') or 0)}"
                )
            else:
                block_lines.append("  - SQLite backup zips: NONE listed (operator hasn't taken one yet, or the list isn't loaded).")
            if config_list:
                latest = config_list[0]
                block_lines.append(
                    f"  - Settings-as-Code snapshots ({config_count} recent): latest = "
                    f"`{latest.get('name', '?')}`, "
                    f"size = {int(latest.get('size') or 0)} bytes, "
                    f"mtime epoch = {int(latest.get('mtime') or 0)}"
                )
            else:
                block_lines.append("  - Settings-as-Code snapshots: NONE listed.")
            block_lines.append(
                "Use the mtime values to compute relative ages (now is the conversation timestamp). "
                "Always cite the file name when answering 'what's the latest backup?' style questions."
            )
            parts.append("\n".join(block_lines))
        # Stats — Stats sub-page data the operator has opened this
        # session. Each block lands only when the matching Stats page
        # has been visited (the SPA forwards the in-memory state for
        # any sub-page whose `*Loaded` flag is true). The AI should
        # answer questions like "what's our MTD AI spend?" / "how
        # many failures last week?" / "top chatty host" using this
        # block as ground truth instead of refusing or guessing.
        # When the relevant Stats sub-page hasn't been opened the
        # block is absent — the AI should suggest the operator open
        # the corresponding Stats tab to populate it.
        # Tunables — always-present compact map of every operator-
        # tunable knob's effective value. SPA forwards from the live
        # `tuningEffective` (Admin → Config GET) when loaded, else
        # from `tuningForm`. The AI should answer "what's the Pulse
        # sample interval?" / "what's the Webmin probe budget?" /
        # "how often do we sample node-exporter?" from this block
        # instead of guessing or pointing at the Admin page.
        tunables = _typed_field(ctx, "tunables", dict)
        if tunables:
            try:
                import json as _json
                tn_json = _json.dumps(tunables, separators=(",", ":"), default=str)
                if len(tn_json) > 6000:
                    tn_json = tn_json[:6000] + "...<truncated>"
                parts.append("\n".join([
                    "Tunables context (effective values, DB > env > default per "
                    "`logic.tuning.TUNABLES`):",
                    tn_json,
                    "Use these to answer questions about cadence / timeout / threshold / "
                    "retention / cap values. Units are encoded in the key name "
                    "(`*_seconds` / `*_minutes` / `*_days` / `*_count` / `*_concurrency`). "
                    "Sample-interval semantics: per-provider knobs (Beszel / Pulse / NE / "
                    "SNMP) with value 0 inherit `tuning_stats_sample_interval_seconds`; > 0 "
                    "overrides that provider only.",
                ]))
            except (TypeError, ValueError):
                # Defensive: _json.dumps can raise TypeError on
                # non-serialisable values OR ValueError on encode
                # failure. Skip this block in either case — the prompt
                # still works without this context section.
                pass
        # Settings — non-secret subset of the live SPA settings state.
        # Master toggles + active-source CSV + per-provider URL + chip
        # colours + retention counts. Secret keys (token / password /
        # api_key / secret / private_key / passphrase suffixes) are
        # NEVER included; only `_set` flags surface so the AI can
        # report "Beszel password is set" without seeing the material.
        settings = _typed_field(ctx, "settings", dict)
        if settings:
            try:
                import json as _json
                st_json = _json.dumps(settings, separators=(",", ":"), default=str)
                if len(st_json) > 6000:
                    st_json = st_json[:6000] + "...<truncated>"
                parts.append("\n".join([
                    "Settings context (non-secret operator configuration):",
                    st_json,
                    "Use these to answer questions about enabled providers / hub URLs / "
                    "active sources / chip colours / per-event notification toggles. Secret "
                    "fields (any key ending in `_token` / `_password` / `_secret` / "
                    "`_api_key` / `_private_key` / `_passphrase`) are NEVER in this block — "
                    "if the operator asks about a secret value, tell them you can't see it "
                    "but the `*_set` flag indicates whether it's persisted.",
                ]))
            except (TypeError, ValueError):
                # Defensive: _json.dumps can raise TypeError on
                # non-serialisable values OR ValueError on encode
                # failure. Skip this block in either case — the prompt
                # still works without this context section.
                pass
        stats = _typed_field(ctx, "stats", dict)
        if stats:
            try:
                import json as _json
                # JSON-stringify the stats block compactly. Tree is
                # already pre-shaped by the SPA to be small (10-30
                # rows per leaf list), so a single compact JSON dump
                # stays well within the prompt budget on a fleet of
                # any reasonable size.
                stats_json = _json.dumps(stats, separators=(",", ":"), default=str)
                # Hard cap defensively in case a fleet pushed the size.
                if len(stats_json) > 8000:
                    stats_json = stats_json[:8000] + "...<truncated>"
                stats_block = [
                    "Stats context (forwarded from the SPA's already-loaded Stats sub-pages):",
                    stats_json,
                    "Each sub-page key (overview / database / samples / incidents / network / "
                    "ai_cost) is the same shape returned by /api/admin/stats/<sub>. Use these "
                    "values to ground numeric / KPI / cost / failure / network-throughput "
                    "questions. When the relevant key is ABSENT, tell the operator the Stats "
                    "sub-page hasn't been opened this session and they can populate it by "
                    "visiting Stats → <sub>.",
                ]
                parts.append("\n".join(stats_block))
            except (TypeError, ValueError):
                # Defensive: _json.dumps can raise TypeError on
                # non-serialisable values OR ValueError on encode
                # failure. Skip this block in either case — the prompt
                # still works without this context section.
                pass
        # Recent log signals — last N error/warn lines from the
        # in-process log ring buffer. Populated by the palette
        # endpoint via `logic.logs.recent_lines(levels=[error, warn])`
        # so the AI can honestly answer "any errors I should fix?"
        # / "anything in the logs?" instead of falsely claiming it
        # has no log access. Each line is a compact `LEVEL  TEXT`
        # row capped at ~200 chars; the full log lives in Admin →
        # Logs (which the AI can point operators at).
        recent_logs = _typed_field(ctx, "recent_logs", list)
        if recent_logs:
            log_lines = []
            # Cap at the last 200 lines from the supplied window. The
            # backend's tunable already enforces an absolute cap; this
            # second slice is defence-in-depth for token budget.
            for entry in recent_logs[-200:]:
                if not isinstance(entry, dict):
                    continue
                lvl = (entry.get("level") or "").upper()
                txt = (entry.get("text") or "").strip()
                ts = entry.get("ts")
                if not txt:
                    continue
                if len(txt) > 200:
                    txt = txt[:200] + "…"
                # Prefix each line with the ISO date+hour so the AI
                # can reason about WHEN issues occurred (e.g. "this
                # has been recurring every hour for 3 days" vs
                # "this fired once 10 minutes ago"). 16 chars =
                # YYYY-MM-DDTHH:MM — minute precision keeps the
                # token cost low while preserving cluster info.
                ts_prefix = ""
                if isinstance(ts, (int, float)) and ts > 0:
                    try:
                        from datetime import datetime as _dt, timezone as _tz
                        ts_prefix = _dt.fromtimestamp(ts, tz=_tz.utc).strftime("%Y-%m-%dT%H:%MZ ")
                    except (OSError, ValueError, OverflowError):
                        # ts out of range for fromtimestamp / negative
                        # / NaN — skip the prefix and emit the line
                        # without time decoration.
                        ts_prefix = ""
                log_lines.append(f"{ts_prefix}{lvl:<7} {txt}")
            if log_lines:
                window_hours = ctx.get("recent_logs_window_hours") or 0
                window_label = (
                    f"(past {int(window_hours)} hours; full log at Admin → Logs)"
                    if isinstance(window_hours, (int, float)) and window_hours > 0
                    else "(full log at Admin → Logs)"
                )
                parts.append(
                    f"Recent log signals — error / warn lines {window_label}, "
                    f"timestamped UTC newest-last:\n"
                    + "\n".join(log_lines)
                )
    return "\n".join(p for p in parts if p)


def build_host_filter_user_prompt(query: str, ctx: dict | None) -> str:
    """User prompt for `/api/ai/host-filter` — same structured-host
    context as the palette path but without items (host-filter only
    operates on hosts in Phase 2)."""
    parts: list[str] = [f"Operator query: {query}"]
    if isinstance(ctx, dict):
        raw_hosts = ctx.get("hosts")
        hosts: list = raw_hosts if isinstance(raw_hosts, list) else []
        if hosts:
            parts.append(_format_records_block(
                "Available hosts",
                "id, label, status, cpu_pct, mem_pct, disk_pct, "
                "disk_free_gb, disk_total_gb, uptime_s, paused, providers",
                hosts[:30],
            ))
    return "\n".join(parts)


def log_ai_outcome(*, kind: str, provider: str, model: str,
                   ok: bool, status: int | None, detail: str | None,
                   response_time_ms: int | None = None,
                   prompt_tokens: int | None = None,
                   completion_tokens: int | None = None,
                   cost_usd: float | None = None,
                   actor: str | None = None,
                   prompt_excerpt: str | None = None,
                   action_id: str | None = None,
                   dsl: str | None = None,
                   fallback_from: str | None = None,
                   hosts_count: int | None = None) -> None:
    """Emit a `[ai]` log line that the persistent-log severity
    classifier (`logic/logs.py:_severity_for`) routes to SUCCESS / WARN
    / ERROR.

    Every AI call lands in Admin → Logs with meaningful triage data —
    operators tracking AI behaviour shouldn't have to drill into the
    AI Usage Dashboard or History tab to see basic call metadata.

    Severity rules:
      - ok=True                → SUCCESS (keyword "ok" — the persistent-
        log classifier picks SUCCESS for that token; operators can
        filter SUCCESS rows off via Admin → Logs severity selector
        when the volume gets noisy).
      - 429 / 502 / 503 / 504  → WARN  (transient upstream overload
        / rate-limit — keyword "warning" + no "fail"/"error" tokens
        in the line so the classifier picks WARN, not ERROR).
      - everything else        → ERROR (operator-actionable: auth
        failure, model-not-found, DNS, TLS, etc. — keyword "failed"
        in the line; upstream detail truncated to 200 chars).

    Optional metadata fields are appended only when set so the line
    stays compact for failure cases (where most metadata is null /
    irrelevant). The full upstream message + ai_jobs.error column
    + history.error column are unchanged — this log line is the
    triage breadcrumb, not the audit trail.
    """
    # Compose the metadata tail in a stable shape: most-useful fields
    # first (timing / tokens), then call-specific signals (action /
    # dsl / fallback), then context (actor / hosts_count / prompt
    # excerpt). Only emit fields that have a value so successful
    # palette calls without a fired action don't render a noisy
    # `action=""` chip.
    parts: list[str] = []
    if response_time_ms is not None and response_time_ms > 0:
        parts.append(f"ms={int(response_time_ms)}")
    if prompt_tokens is not None and completion_tokens is not None:
        parts.append(f"tokens={int(prompt_tokens)}+{int(completion_tokens)}")
    elif prompt_tokens is not None:
        parts.append(f"prompt_tokens={int(prompt_tokens)}")
    if cost_usd is not None and cost_usd > 0:
        parts.append(f"cost=${cost_usd:.6f}")
    if (action_id or "").strip():
        parts.append(f"action={action_id}")
    if (dsl or "").strip():
        # DSL strings are short by design; quote them for grep-ability.
        # `dsl or ""` already collapsed the None branch above but the
        # type-checker doesn't narrow `dsl` itself — use a local str.
        dsl_src = dsl or ""
        dsl_esc = dsl_src.replace("\n", " ").strip()[:80]
        parts.append(f"dsl={dsl_esc!r}")
    if (fallback_from or "").strip():
        parts.append(f"fallback_from={fallback_from}")
    if hosts_count is not None and hosts_count > 0:
        parts.append(f"hosts={int(hosts_count)}")
    if (actor or "").strip():
        parts.append(f"actor={actor}")
    if (prompt_excerpt or "").strip():
        # Narrow to non-None for the type-checker — the `or ""` above
        # collapsed the None branch but `prompt_excerpt` itself stayed
        # typed `str | None`. Use a local str alias for the rest of
        # the block so `.replace` / `len` are unambiguous.
        excerpt_src = prompt_excerpt or ""
        excerpt = excerpt_src.replace("\n", " ").strip()[:80]
        if len(excerpt_src) > 80:
            excerpt += "…"
        parts.append(f"q={excerpt!r}")
    tail = (" " + " ".join(parts)) if parts else ""

    if ok:
        # Keyword "ok" → severity classifier picks SUCCESS.
        s = int(status) if status else 200
        print(f"[ai] {kind} ok — provider={provider} model={model} "
              f"HTTP={s}{tail}")
        return

    s = int(status) if status else 0
    # Transient bucket MUST agree with `_with_retry` /
    # `ask_provider_with_fallback`'s gates so the operator-visible
    # log severity matches what the system actually did. HTTP=0 is
    # the "network error / timeout / DNS fail" sentinel that the
    # retry path now treats as transient (the operator-classifier
    # alignment fix); the log classifier diverged before this fix and
    # ERROR-stamped outcomes the system already retried + recovered
    # from. Now: HTTP=0 OR 429/502/503/504 → WARN; everything else
    # (4xx auth/model errors, 5xx that aren't transient) → ERROR.
    transient = s in (0, 429, 502, 503, 504)
    if transient:
        # Word "warning" + no "failed/error" → classifier picks WARN.
        why = "upstream-overloaded (transient, retry later)" if s != 0 else "network/timeout (transient, retry later)"
        print(f"[ai] {kind} warning — provider={provider} model={model} "
              f"HTTP={s} {why}{tail}")
    else:
        truncated = (detail or "")[:200].replace("\n", " ").strip() or "(no detail)"
        # Word "failed" → classifier picks ERROR.
        print(f"[ai] {kind} call failed — provider={provider} model={model} "
              f"HTTP={s}: {truncated}{tail}")


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
) -> int | None:
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
        # Cost computed at insert time so historical rows survive a
        # rate-card edit. None when no entry matches (model not in
        # RATE_CARD) — dashboard renders "—" via aiFormatCost null
        # branch instead of misleading $0.0000.
        cost_usd = compute_cost_usd(provider, model or "", prompt_t, completion_t)
        # Coarse accuracy signal — see logic/ai.py:score_accuracy.
        # `text` is read from history_events for the heuristic; the
        # caller already passes it in events for both palette + filter
        # kinds.
        _text_for_score = (history_events or {}).get("answer") or ""
        accuracy_score, accuracy_check = score_accuracy(
            kind=kind, ok=ok, text=_text_for_score,
            history_events=history_events,
        )
        try:
            accuracy_check_json = _json.dumps(accuracy_check, ensure_ascii=False)
        except (TypeError, ValueError):
            accuracy_check_json = None
        ai_job_id: int | None = None
        with db_conn_factory() as c:
            cur = c.execute(
                "INSERT INTO ai_jobs ("
                "  ts, provider, model, kind, status,"
                "  prompt_tokens, completion_tokens, total_tokens,"
                "  cost_usd, response_time_ms, accuracy_score,"
                "  accuracy_check, error, metadata"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    now_ts, provider, model or "", kind,
                    "success" if ok else "error",
                    prompt_t, completion_t, total_t,
                    cost_usd,
                    int(response_time_ms or 0),
                    accuracy_score,
                    accuracy_check_json,
                    error_detail or None,
                    None,
                ),
            )
            try:
                ai_job_id = int(cur.lastrowid) if cur.lastrowid else None
            except (TypeError, ValueError):
                ai_job_id = None
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
            # Defence-in-depth assert — raw INSERT bypasses `new_op`,
            # so the OP_TYPES validator wouldn't otherwise fire. A
            # typo'd `kind` (e.g. record_ai_call(kind="paletteX"))
            # would otherwise land `ai_paletteX` silently in history.
            from logic.ops import assert_op_type as _assert_op_type
            _assert_op_type(f"ai_{kind}")
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
        return ai_job_id
    except Exception as e:  # noqa: BLE001
        print(f"[ai] record_ai_call({kind}) failed: {e}")
        return None
