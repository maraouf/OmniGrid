"""Tests for the prompt rules that decide when the AI reaches for a tool.

The reported failure: asked to find a MAC on a switch and bounce its port, the
model replied "I will look up which port ... so that we can proceed" and then
stopped. No tool ran, no confirm chip appeared, and there was no second round
to come back from — the operator was left waiting on a turn that never existed.

It was doing what the prompt said. Every trigger for the tool surface described
a DIAGNOSTIC QUESTION ("why is X failing", "what's in the logs"), and the
request was an ACTION whose target had to be looked up first. The worked
example telling it to emit the lookup sat roughly 14 KB further down the
prompt, so the gate won and the example lost.

These pin the two rules that fix it, and — the part that actually matters —
that they sit at the gate rather than buried in the examples, since ordering is
what decided the original behaviour.
"""
from __future__ import annotations

from logic.ai import PALETTE_SYSTEM_PROMPT as PROMPT


def test_an_action_needing_a_lookup_is_named_as_a_tool_trigger():
    """The whole cause: the model read "diagnostic" as excluding this."""
    assert "AN ACTION WHOSE TARGET YOU DO NOT YET KNOW" in PROMPT


def test_the_bounce_case_is_named_concretely_at_the_gate():
    """An abstract rule did not survive contact; the concrete case is what the
    model matched against."""
    i = PROMPT.find("bounce its port")
    j = PROMPT.find("AN ACTION WHOSE TARGET YOU DO NOT YET KNOW")
    assert i != -1 and j != -1
    assert abs(i - j) < 800, "the worked case drifted away from the rule"


def test_the_diagnostic_wording_is_explicitly_not_exclusive():
    """Listing a second trigger is not enough when the first one reads as a
    closed set — the prompt has to say so."""
    assert "Do NOT read the diagnostic wording as excluding" in PROMPT


def test_announcing_a_lookup_without_emitting_it_is_banned():
    """The exact shape of the failed reply."""
    assert "NEVER ANNOUNCE A LOOKUP YOU HAVE NOT EMITTED" in PROMPT


def test_the_ban_explains_why_there_is_no_later_turn():
    """A rule the model can't reason about is one it will talk itself out of:
    it has to know the backend takes a directive-free reply as final."""
    i = PROMPT.find("NEVER ANNOUNCE A LOOKUP YOU HAVE NOT EMITTED")
    window = PROMPT[i:i + 900]
    assert "no later turn" in window
    assert "final" in window


def test_both_rules_precede_the_worked_examples():
    """Ordering is the whole finding.

    The locate-then-bounce example already said to emit the lookup, and the
    model still did not — because the gate it read first said tools were for
    diagnostic questions. A rule that contradicts an earlier gate loses, so
    these two have to land before the tool list, not after it.
    """
    gate = PROMPT.find("AN ACTION WHOSE TARGET YOU DO NOT YET KNOW")
    ban = PROMPT.find("NEVER ANNOUNCE A LOOKUP YOU HAVE NOT EMITTED")
    tools_list = PROMPT.find("Available tools (use the EXACT names)")
    example = PROMPT.find("locate-then-bounce")
    assert -1 not in (gate, ban, tools_list, example)
    assert gate < tools_list, "the widened trigger must precede the tool list"
    assert ban < tools_list, "the ban must precede the tool list"
    assert ban < example, "the ban must precede the examples that rely on it"


def test_the_lookup_tool_is_still_documented():
    """The tool the failing request needed."""
    assert "find_mac_port(args:" in PROMPT
