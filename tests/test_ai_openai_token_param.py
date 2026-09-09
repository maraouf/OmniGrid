"""OpenAI's newer models reject `max_tokens`; older ones and every shim reject
its replacement. The endpoint decides, not a name pattern.

Observed against a live ChatGPT provider configured with `gpt-6-astra`:

    Unsupported parameter: 'max_tokens' is not supported with this model.
    Use 'max_completion_tokens' instead. (868 ms)

Test connection surfaced it, but the same body is what every real question
sends, so the provider was unusable rather than merely untestable.

The fix asks instead of guessing: send `max_tokens`, and switch only when an
endpoint names the successor in its refusal. These tests pin BOTH directions —
the switch happening when told to, and (the half that actually protects the
operator's self-hosted shims) NOT happening otherwise.
"""
from __future__ import annotations

import asyncio

import httpx

from logic import ai


def _resp(status: int, body: str = "", payload: dict | None = None) -> httpx.Response:
    req = httpx.Request("POST", "https://example.com/v1/chat/completions")
    if payload is not None:
        return httpx.Response(status, json=payload, request=req)
    return httpx.Response(status, text=body, request=req)


_REFUSAL = ("Unsupported parameter: 'max_tokens' is not supported with this "
            "model. Use 'max_completion_tokens' instead.")


def _reset():
    ai._token_param_memo.clear()


# --- the signal ------------------------------------------------------------

def test_the_named_refusal_is_recognised():
    assert ai._wants_completion_tokens(_resp(400, _REFUSAL))


def test_a_generic_client_error_is_not_a_token_param_problem():
    """The retry must not fire on any 400 — an unrelated fault would come back
    as a second, different failure and the real cause would be lost."""
    for body in ("Incorrect API key provided", "model not found",
                 "context_length_exceeded", ""):
        assert not ai._wants_completion_tokens(_resp(400, body)), body


def test_a_server_error_is_never_the_signal():
    assert not ai._wants_completion_tokens(_resp(500, _REFUSAL))
    assert not ai._wants_completion_tokens(_resp(503, _REFUSAL))


# --- the default ------------------------------------------------------------

def test_the_default_is_the_form_everything_accepts():
    """`max_tokens` is what every OpenAI-compatible server has always taken.
    Starting anywhere else would break the shims the Base URL field exists
    for."""
    _reset()
    assert ai._token_param_for("chatgpt", "https://api.openai.com", "gpt-4o") == "max_tokens"


def test_the_preference_is_remembered_per_endpoint():
    _reset()
    ai._remember_token_param("chatgpt", "https://api.openai.com", "gpt-6-astra",
                             "max_completion_tokens")
    assert ai._token_param_for("chatgpt", "https://api.openai.com",
                               "gpt-6-astra") == "max_completion_tokens"
    # A DIFFERENT model on the same host is unaffected — the operator can run
    # a new model and an old one side by side.
    assert ai._token_param_for("chatgpt", "https://api.openai.com",
                               "gpt-4o") == "max_tokens"
    # And a different BASE is unaffected, which is what keeps a self-hosted
    # shim from inheriting OpenAI's answer.
    assert ai._token_param_for("chatgpt", "http://localai.lan",
                               "gpt-6-astra") == "max_tokens"


def test_the_memo_cannot_grow_without_bound():
    _reset()
    for i in range(ai._TOKEN_PARAM_CAP + 25):
        ai._remember_token_param("chatgpt", f"h{i}", "m", "max_completion_tokens")
    assert len(ai._token_param_memo) <= ai._TOKEN_PARAM_CAP


# --- end to end through the probe ------------------------------------------

class _Recorder:
    """Stands in for httpx.AsyncClient, capturing each body it is handed."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.bodies = []

    def __call__(self, *_a, **_kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, _url, headers=None, json=None):  # noqa: A002
        self.bodies.append(json or {})
        return self.replies.pop(0)


def test_probe_retries_once_with_the_successor_when_told_to(monkeypatch):
    _reset()
    rec = _Recorder([_resp(400, _REFUSAL),
                     _resp(200, payload={"choices": [], "usage": {}})])
    monkeypatch.setattr(ai.httpx, "AsyncClient", rec)
    out = asyncio.run(ai._probe_openai_compatible(
        "chatgpt", "sk-x", "gpt-6-astra", "", 5.0))
    assert len(rec.bodies) == 2, "expected exactly one retry, not a loop"
    assert "max_tokens" in rec.bodies[0]
    assert "max_completion_tokens" in rec.bodies[1]
    assert "max_tokens" not in rec.bodies[1], (
        "sent both keys — the endpoints that reject one reject a body carrying both")
    assert out.get("ok")


def test_probe_does_not_retry_when_the_first_call_succeeds(monkeypatch):
    """The compatibility half: a shim that accepts `max_tokens` must never be
    handed `max_completion_tokens`, which it would reject."""
    _reset()
    rec = _Recorder([_resp(200, payload={"choices": [], "usage": {}})])
    monkeypatch.setattr(ai.httpx, "AsyncClient", rec)
    asyncio.run(ai._probe_openai_compatible("deepseek", "sk-x", "deepseek-chat", "", 5.0))
    assert len(rec.bodies) == 1
    assert "max_tokens" in rec.bodies[0]


def test_probe_does_not_retry_on_an_unrelated_error(monkeypatch):
    _reset()
    rec = _Recorder([_resp(401, "Incorrect API key provided")])
    monkeypatch.setattr(ai.httpx, "AsyncClient", rec)
    out = asyncio.run(ai._probe_openai_compatible("chatgpt", "bad", "gpt-4o", "", 5.0))
    assert len(rec.bodies) == 1, "retried on a credential failure"
    assert not out.get("ok")


def test_the_second_call_onward_skips_the_wasted_probe(monkeypatch):
    """The memo is what keeps this one wasted call per endpoint rather than one
    per request."""
    _reset()
    rec = _Recorder([_resp(400, _REFUSAL),
                     _resp(200, payload={"choices": [], "usage": {}}),
                     _resp(200, payload={"choices": [], "usage": {}})])
    monkeypatch.setattr(ai.httpx, "AsyncClient", rec)
    asyncio.run(ai._probe_openai_compatible("chatgpt", "sk-x", "gpt-6-astra", "", 5.0))
    asyncio.run(ai._probe_openai_compatible("chatgpt", "sk-x", "gpt-6-astra", "", 5.0))
    assert len(rec.bodies) == 3, "the remembered preference was not reused"
    assert "max_completion_tokens" in rec.bodies[2]


# --- the real call path, not just the probe --------------------------------

def test_the_chat_path_carries_the_same_fix(monkeypatch):
    """The Test button was only the messenger — every real question goes
    through `_chat_openai_compatible`, and a fix in the probe alone would have
    left the provider just as unusable while reporting a green test."""
    _reset()
    rec = _Recorder([
        _resp(400, _REFUSAL),
        _resp(200, payload={"choices": [{"message": {"content": "hi"}}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                            "model": "gpt-6-astra"}),
    ])
    monkeypatch.setattr(ai.httpx, "AsyncClient", rec)
    out = asyncio.run(ai._chat_openai_compatible(
        "chatgpt", "sk-x", "gpt-6-astra", "", "hello", "sys", 512, 5.0))
    assert out.get("ok"), out
    assert out.get("text") == "hi"
    assert len(rec.bodies) == 2
    assert rec.bodies[1].get("max_completion_tokens") == 512, (
        "the retry dropped the operator's token budget")
    assert rec.bodies[1].get("messages"), "the retry lost the conversation"


# --- the reasoning-model output budget -------------------------------------
#
# Second quirk in the same family, hit the moment the first was fixed: the
# parameter was accepted, and the model then answered
#
#     Could not finish the message because max_tokens or model output limit
#     was reached. Please try again with higher max_tokens. (1,127 ms)
#
# because the credential probe deliberately asks for ONE token and a reasoning
# model spends its budget thinking before it writes anything. For a CREDENTIAL
# test that is a pass; on the chat path the same shape is a real failure.

_BUDGET = ("Could not finish the message because max_tokens or model output "
           "limit was reached. Please try again with higher max_tokens.")


def test_the_budget_refusal_is_recognised():
    assert ai._output_budget_exhausted(_resp(400, _BUDGET))


def test_the_budget_refusal_is_not_confused_with_the_parameter_refusal():
    """Two different faults that both mention `max_tokens`. Treating the
    parameter refusal as 'budget exhausted' would report a green test on a
    provider that cannot take the parameter at all."""
    assert not ai._output_budget_exhausted(_resp(400, _REFUSAL))
    assert ai._wants_completion_tokens(_resp(400, _REFUSAL))


def test_a_credential_failure_is_never_a_budget_pass():
    """The pass rests on 401/404 still failing — if a bad key could reach this
    branch the test button would go green on credentials that do not work."""
    for body in ("Incorrect API key provided", "model `x` does not exist",
                 "insufficient_quota"):
        assert not ai._output_budget_exhausted(_resp(400, body)), body
    assert not ai._output_budget_exhausted(_resp(401, _BUDGET))


def test_probe_passes_when_only_its_own_ceiling_stopped_the_model(monkeypatch):
    _reset()
    rec = _Recorder([_resp(400, _BUDGET)])
    monkeypatch.setattr(ai.httpx, "AsyncClient", rec)
    out = asyncio.run(ai._probe_openai_compatible(
        "chatgpt", "sk-x", "gpt-6-astra", "", 5.0))
    assert out.get("ok"), out
    assert "reasons before it writes" in out.get("detail", ""), (
        "passed silently — the operator cannot tell this from a normal OK")


def test_chat_path_reports_an_exhausted_budget_instead_of_an_empty_bubble(monkeypatch):
    """The same shape on the chat path is a FAILURE — an empty assistant reply
    is indistinguishable from the model having nothing to say."""
    _reset()
    rec = _Recorder([_resp(200, payload={
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 16384},
    })])
    monkeypatch.setattr(ai.httpx, "AsyncClient", rec)
    out = asyncio.run(ai._chat_openai_compatible(
        "chatgpt", "sk-x", "gpt-6-astra", "", "hello", "sys", 16384, 5.0))
    assert not out.get("ok"), "an empty answer was reported as success"
    assert "16384" in out.get("detail", ""), "did not say what was spent"
    assert "Max response tokens" in out.get("detail", ""), (
        "did not name the setting that fixes it")


def _run_tool_call_reply(monkeypatch, finish_reason: str, spent: int) -> dict:
    """One tool-call reply through the chat path, varying only what actually
    distinguishes the two cases below: why the model stopped."""
    _reset()
    rec = _Recorder([_resp(200, payload={
        "choices": [{
            "message": {"content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "find_mac_port", "arguments": "{}"}},
            ]},
            "finish_reason": finish_reason,
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": spent},
    })])
    monkeypatch.setattr(ai.httpx, "AsyncClient", rec)
    return asyncio.run(ai._chat_openai_compatible(
        "chatgpt", "sk-x", "gpt-6-astra", "", "hello", "sys", 512, 5.0,
        tools=[{"type": "function", "function": {"name": "find_mac_port"}}]))


def test_a_native_tool_call_reply_is_not_mistaken_for_an_empty_one(monkeypatch):
    """Load-bearing: a tool-call reply legitimately has empty content. Reading
    it as an exhausted budget would break native tool-calling outright — which
    is ON in the configuration that produced this bug."""
    out = _run_tool_call_reply(monkeypatch, "tool_calls", 5)
    assert out.get("ok"), out
    assert out.get("tool_calls"), "the tool call was dropped"


def test_an_ordinary_short_answer_is_untouched(monkeypatch):
    """A model that stops at the cap having ALREADY written something is fine —
    truncated, but an answer. Only a reply with nothing in it is the failure."""
    _reset()
    rec = _Recorder([_resp(200, payload={
        "choices": [{"message": {"content": "partial ans"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 512},
    })])
    monkeypatch.setattr(ai.httpx, "AsyncClient", rec)
    out = asyncio.run(ai._chat_openai_compatible(
        "chatgpt", "sk-x", "gpt-6-astra", "", "hello", "sys", 512, 5.0))
    assert out.get("ok"), out
    assert out.get("text") == "partial ans"


def test_a_truncated_tool_call_is_still_a_tool_call(monkeypatch):
    """The case the `calls` guard actually protects, which the test above does
    NOT reach: a reply carrying tool calls that ALSO hit the cap, so
    `finish_reason` is `length`. Without the guard this is read as an exhausted
    budget and the tool call is thrown away — verified by deleting the guard
    and watching only this test fail."""
    out = _run_tool_call_reply(monkeypatch, "length", 512)
    assert out.get("ok"), "a truncated tool-call reply was reported as failure"
    assert out.get("tool_calls"), "the tool call was dropped"
