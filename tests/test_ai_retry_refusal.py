"""Tests for recovering from a transient AI-provider refusal.

What happened: Gemini answered a palette call with HTTP 503 "upstream
overloaded". Both recovery paths then declined it, each for its own reason, and
between them turned a blip into a visible failure:

    retry-skipped    first-attempt-5509ms >= threshold-5000ms
    fallback-skipped prompt-184154-chars > cap-32000

The retry guard's reasoning holds for a status where the upstream was WORKING —
a 504, or a client-side timeout — because there the elapsed time measures effort
and repeating it doubles the wait. It does not hold for 429 or 503, which are
the upstream declining to start: the elapsed time is how long it took to say no.

The fallback cap was a hardcoded 32,000 characters. The palette prompt is
~184,000, so on that surface the cap was not a guard at all — it was an
unconditional "never fall back", and a configured fallback provider could not
engage no matter what the operator did.
"""
from __future__ import annotations

import inspect

from logic import ai
from logic.tuning import TUNABLES, Tunable, tuning_int


def _guard_source() -> str:
    return inspect.getsource(ai._with_retry)


def test_a_refusal_is_exempt_from_the_slow_first_attempt_gate():
    """429 and 503 mean "not now", not "still working"."""
    src = _guard_source()
    assert "is_refusal" in src, "the refusal carve-out is gone"
    assert "(429, 503)" in src, "the refusal statuses are no longer named"
    assert "elapsed_ms >= first_max_ms and not is_refusal" in src, (
        "the elapsed-time gate no longer exempts refusals — a fast 503 will be "
        "declined again")


def test_the_gate_still_applies_to_everything_else():
    """The guard is right for a genuinely slow upstream; only the refusal
    statuses are carved out, not the whole check."""
    src = _guard_source()
    assert "elapsed_ms >= first_max_ms" in src, (
        "the slow-first-attempt gate was removed entirely rather than narrowed")


def test_the_fallback_prompt_cap_is_operator_tunable():
    """It was hardcoded, and hardcoded at a value that made the feature
    unreachable on the surface that needs it most."""
    assert "tuning_ai_fallback_prompt_cap_chars" in TUNABLES
    env, default, lo, hi = TUNABLES["tuning_ai_fallback_prompt_cap_chars"]
    assert env == "AI_FALLBACK_PROMPT_CAP_CHARS"
    assert lo < default < hi
    assert tuning_int(Tunable.AI_FALLBACK_PROMPT_CAP_CHARS) == default


def test_the_default_cap_clears_a_real_palette_prompt():
    """The number that matters.

    A default that sits under the prompt this guard is asked about every day
    is not a guard, it is an off switch. The observed prompt was 184,154
    characters.
    """
    _, default, _, _ = TUNABLES["tuning_ai_fallback_prompt_cap_chars"]
    assert default > 184_154, (
        f"default {default} is below a real observed palette prompt, so "
        f"fallback still cannot engage there")


def test_the_cap_is_resolved_rather_than_hardcoded():
    src = inspect.getsource(ai.ask_provider_with_fallback)
    assert "AI_FALLBACK_PROMPT_CAP_CHARS" in src, (
        "the cap is no longer read from settings")
    assert "prompt_size_cap_chars: int = 32000" not in src, (
        "the old hardcoded 32000 default is back")
