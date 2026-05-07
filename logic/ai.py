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
    ("claude", "claude-opus-4-7"):    (15.00, 75.00),
    ("claude", "claude-opus-4"):      (15.00, 75.00),
    ("claude", "claude-opus"):        (15.00, 75.00),
    ("claude", "claude-sonnet-4-6"):  (3.00, 15.00),
    ("claude", "claude-sonnet-4"):    (3.00, 15.00),
    ("claude", "claude-sonnet"):      (3.00, 15.00),
    ("claude", "claude-haiku-4-5"):   (1.00, 5.00),
    ("claude", "claude-haiku-4"):     (1.00, 5.00),
    ("claude", "claude-haiku"):       (1.00, 5.00),
    # Google Gemini — 2.5 family pricing tiers
    ("gemini", "gemini-2.5-pro"):         (1.25, 10.00),
    ("gemini", "gemini-2.5-flash-lite"):  (0.0375, 0.15),
    ("gemini", "gemini-2.5-flash"):       (0.075, 0.30),
    ("gemini", "gemini-1.5-pro"):         (1.25, 5.00),
    ("gemini", "gemini-1.5-flash"):       (0.075, 0.30),
    # OpenAI ChatGPT — gpt-5 / gpt-4o family
    ("chatgpt", "gpt-5-mini"):    (0.25, 2.00),
    ("chatgpt", "gpt-5"):         (1.25, 10.00),
    ("chatgpt", "gpt-4o-mini"):   (0.15, 0.60),
    ("chatgpt", "gpt-4o"):        (2.50, 10.00),
    ("chatgpt", "gpt-4-turbo"):   (10.00, 30.00),
    # DeepSeek — chat (V3) + reasoner (R1)
    ("deepseek", "deepseek-reasoner"): (0.55, 2.19),
    ("deepseek", "deepseek-chat"):     (0.27, 1.10),
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
    r"\b(host|admin|drawer|stack|service|portainer|webmin|beszel|pulse|"
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
    from logic.tuning import tuning_int
    enabled = bool(tuning_int("tuning_ai_retry_enabled"))
    if not enabled:
        return await call_factory()
    backoff_ms = tuning_int("tuning_ai_retry_backoff_ms")
    first_max_ms = tuning_int("tuning_ai_retry_first_attempt_max_ms")
    transient_statuses = (429, 502, 503, 504)
    import time as _time
    t0 = _time.monotonic()
    out = await call_factory()
    elapsed_ms = (_time.monotonic() - t0) * 1000.0
    # Only consider retry on a non-OK transient-overload outcome.
    if not (isinstance(out, dict) and not out.get("ok")
            and out.get("status") in transient_statuses):
        return out
    status = out.get("status")
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

    # Build a thunk for the per-provider call so the retry wrapper
    # can re-invoke it identically on a transient-overload retry.
    async def _do_call() -> dict:
        if p == "claude":
            return await _chat_claude(api_key, model or "", base_url or "",
                                      prompt, system_prompt, max_tokens, timeout)
        elif p == "gemini":
            return await _chat_gemini(api_key, model or "", base_url or "",
                                      prompt, system_prompt, max_tokens, timeout)
        else:
            return await _chat_openai_compatible(p, api_key, model or "", base_url or "",
                                                 prompt, system_prompt, max_tokens, timeout)

    started = time.time()
    try:
        out = await _with_retry(_do_call, provider=p, model=(model or _DEFAULT_MODELS.get(p, "")))
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
        creds = provider_creds.get(provider_id) or {}
        out = await ask_provider(
            provider_id,
            api_key=creds.get("api_key", ""),
            prompt=prompt,
            system_prompt=system_prompt,
            model=creds.get("model"),
            base_url=creds.get("base_url"),
            max_tokens=max_tokens,
            timeout=timeout,
        )
        history.append({
            "provider":         provider_id,
            "status":           out.get("status"),
            "response_time_ms": out.get("response_time_ms", 0),
            "succeeded":        bool(out.get("ok")),
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
    if primary_status not in _FALLBACK_RETRY_STATUSES:
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
    "  3. dockerpve — 76% used (240 GB free of 1.0 TB)\n"
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
    "Example single-action reply: 'I'll mark every notification as read for you.\\n"
    "ACTION: mark_all_notifications_read'\n"
    "Example multi-action reply (\"refresh and cleanup\"): 'Refreshing the dashboard, then opening the cleanup confirm.\\n"
    "ACTION: refresh\\n"
    "ACTION: cleanup_stopped'\n"
    "If no action fits, omit the ACTION line entirely.\n\n"
    "HOSTS PROTOCOL — when your reply identifies SPECIFIC hosts BY "
    "NAME (e.g. answering 'top hosts low on disk', 'which hosts are "
    "running out of space', 'which hosts are at high CPU', 'top N "
    "by memory'), you MUST end your reply with a SINGLE line "
    "`HOSTS: <id1>, <id2>, <id3>` listing each referenced host's "
    "curated `id` field — NOT its label, alias, or display name — "
    "in the order they appear in your prose. Maximum 8 ids. The "
    "SPA picks up the HOSTS line, strips it from the visible text, "
    "and renders inline disk-projection charts for each host below "
    "the answer (historical usage + linear-projection forecast of "
    "when the disk fills up). Use ONLY ids that appear in the "
    "supplied 'Hosts:' context block — never invent ids the SPA "
    "doesn't know about. The HOSTS line and the ACTION line are "
    "INDEPENDENT — both can appear (HOSTS first, then ACTION). "
    "Skip the HOSTS line entirely when your answer is generic / "
    "doesn't reference specific named hosts. Example reply: "
    "'Top 3 hosts low on disk: 1. nas01 — 92% used, 2. db02 — 88%, "
    "3. cache03 — 85%.\\nHOSTS: nas01, db02, cache03'"
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
    import re as _re
    actions: list[str] = []
    seen: set[str] = set()
    # Find every ACTION: <body> line — body may be a single id or a
    # comma/whitespace-separated list. Re-tokenise per line so
    # mixed shapes work (`ACTION: refresh\nACTION: cleanup_stopped`
    # OR `ACTION: refresh, cleanup_stopped`).
    line_re = _re.compile(
        r"(?:^|\n)[\s`*]*ACTION\s*:\s*([^\n]+?)[\s`.*]*$",
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
    import re as _re
    m = _re.search(
        r"(?:^|\n)[\s`*]*HOSTS\s*:\s*(.+?)[\s`.*]*$",
        text, _re.IGNORECASE | _re.MULTILINE,
    )
    if not m:
        return [], text
    raw = m.group(1)
    # Split on commas first (preferred), fall back to whitespace.
    parts: list[str] = []
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
        dsl_esc = dsl.replace("\n", " ").strip()[:80]
        parts.append(f"dsl={dsl_esc!r}")
    if (fallback_from or "").strip():
        parts.append(f"fallback_from={fallback_from}")
    if hosts_count is not None and hosts_count > 0:
        parts.append(f"hosts={int(hosts_count)}")
    if (actor or "").strip():
        parts.append(f"actor={actor}")
    if (prompt_excerpt or "").strip():
        excerpt = prompt_excerpt.replace("\n", " ").strip()[:80]
        if len(prompt_excerpt) > 80:
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
    transient = s in (429, 502, 503, 504)
    if transient:
        # Word "warning" + no "failed/error" → classifier picks WARN.
        print(f"[ai] {kind} warning — provider={provider} model={model} "
              f"HTTP={s} upstream-overloaded (transient, retry later){tail}")
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
