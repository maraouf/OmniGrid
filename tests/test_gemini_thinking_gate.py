"""Gemini's thinking gate must follow the version, not a literal.

Observed on the running deployment after the operator moved Gemini from
`gemini-3.5-flash` to `gemini-3.8-flash`: a Flash model that is supposed to
skip thinking was thinking on every call, because the gate read

    if "2.5" in mdl_lc and "pro" not in mdl_lc:

and neither `gemini-3.5-flash` nor `gemini-3.8-flash` contains "2.5". The rule
had expired without telling anyone — the same name-matching trap as the OpenAI
token-cap parameter, one file apart.

`pro` still gets no thinkingConfig (the API rejects budget 0 for a model that
only operates in thinking mode), and anything whose version cannot be read is
left alone, because sending a field an older rev does not know is a worse
failure than a slow answer.
"""
from __future__ import annotations

import asyncio

import httpx

from logic import ai


# --- the version reader -----------------------------------------------------

def test_it_reads_the_version_out_of_a_model_id():
    assert ai._gemini_version_at_least("gemini-2.5-flash", 2.5)
    assert ai._gemini_version_at_least("gemini-3.8-flash", 2.5)
    assert ai._gemini_version_at_least("gemini-3.5-flash", 2.5)
    assert ai._gemini_version_at_least("gemini-10.0-flash", 2.5)


def test_older_lines_stay_below_the_floor():
    """The reason the gate cannot simply always fire: these revs have no
    thinkingConfig, so the field must not be sent."""
    assert not ai._gemini_version_at_least("gemini-1.5-flash", 2.5)
    assert not ai._gemini_version_at_least("gemini-1.0-pro", 2.5)
    assert not ai._gemini_version_at_least("gemini-2.0-flash", 2.5)


def test_an_unreadable_id_is_not_assumed_new():
    """Conservative direction on purpose — an unknown id is as likely to be an
    old rev as a new one, and guessing wrong breaks the request rather than
    merely slowing it."""
    for mdl in ("gemini-flash", "", "some-custom-proxy-model", "gemini"):
        assert not ai._gemini_version_at_least(mdl, 2.5), mdl


# --- what the gate actually does with it ------------------------------------

class _BodyCapture:
    """Stands in for httpx.AsyncClient and keeps the request body."""

    def __init__(self):
        self.body = None

    def __call__(self, *_a, **_kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, _url, headers=None, json=None):  # noqa: A002
        self.body = json or {}
        req = httpx.Request("POST", "https://example.com")
        return httpx.Response(
            200, request=req,
            json={"candidates": [{"content": {"parts": [{"text": "hi"}]},
                                  "finishReason": "STOP"}],
                  "usageMetadata": {"promptTokenCount": 1,
                                    "candidatesTokenCount": 1}})


def _thinking_disabled_for(model: str, monkeypatch) -> bool:
    """Ask the REAL `_chat_gemini` what it puts on the wire.

    Deliberately NOT a copy of the condition. A mirrored rule cannot detect the
    thing this file exists to catch — I wrote one first, and it passed happily
    with the old broken literal restored, because the mirror and the code were
    free to disagree. Reading the actual request body is the only version of
    this test that fails when the gate regresses.
    """
    cap = _BodyCapture()
    monkeypatch.setattr(ai.httpx, "AsyncClient", cap)
    asyncio.run(ai._chat_gemini("k", model, "", "hello", "sys", 512, 5.0))
    gen = (cap.body or {}).get("generationConfig") or {}
    tc = gen.get("thinkingConfig")
    return isinstance(tc, dict) and tc.get("thinkingBudget") == 0


def test_flash_and_lite_skip_thinking_across_version_lines(monkeypatch):
    """The regression this file exists for: 3.8-flash must behave like
    2.5-flash, not fall out of the rule because of its version number."""
    for mdl in ("gemini-2.5-flash", "gemini-2.5-flash-lite",
                "gemini-3.5-flash", "gemini-3.8-flash"):
        assert _thinking_disabled_for(mdl, monkeypatch), mdl


def test_pro_never_gets_a_zero_budget(monkeypatch):
    """The API answers HTTP 400 — Pro only operates in thinking mode, so the
    budget must be left to the model."""
    for mdl in ("gemini-2.5-pro", "gemini-3.8-pro", "gemini-1.5-pro"):
        assert not _thinking_disabled_for(mdl, monkeypatch), mdl


def test_pre_thinking_revs_are_left_alone(monkeypatch):
    for mdl in ("gemini-1.5-flash", "gemini-2.0-flash"):
        assert not _thinking_disabled_for(mdl, monkeypatch), mdl


def test_the_old_literal_would_fail_this(monkeypatch):
    """Pins WHY the rule changed shape. The previous condition passes for
    2.5-flash and fails for 3.8-flash; if anyone reintroduces a version
    literal, this states plainly what breaks."""
    def _old_gate(model: str) -> bool:
        mdl_lc = (model or "").lower()
        return "2.5" in mdl_lc and "pro" not in mdl_lc

    assert _old_gate("gemini-2.5-flash")
    assert not _old_gate("gemini-3.8-flash"), (
        "the old literal would have covered 3.8 — the premise of this fix is wrong")
    assert _thinking_disabled_for("gemini-3.8-flash", monkeypatch), (
        "the new gate does not cover the model that exposed the bug")
