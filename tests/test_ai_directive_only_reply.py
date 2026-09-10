"""A reply that is nothing but a directive must say what it will do.

Observed: "find the mac address 6c:63:f8:53:bd:1f on switch52mp and bounce its
interface" produced

    14:54:31  ai_tool_call  switch52mp01  success
    14:54:39  [ai] palette ok  HTTP=200  tokens=73600+91  action=bounce_interface

— the lookup ran, the model wrote 91 tokens, the backend parsed the action —
and the chat rendered "(empty response)". The directive lines are stripped from
the conversational body before the SPA sees them, so a reply carrying ONLY a
directive arrives as empty text. The previous model had padded its directives
with a sentence, which is the only reason this had not shown up before.

These tests read the source, because the behaviour lives in a branch of the
sidebar send path that a unit test cannot reach without standing up Alpine.
That makes them weaker than the Python-side tests here, and they are written to
pin the DECISIONS rather than to simulate the render: that the placeholder is
reached only with no action behind it, and that a destructive action is worded
as a proposal rather than as something already happening.
"""
from __future__ import annotations

import pathlib
import re

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "static" / "js" / "app-ai.js").read_text(encoding="utf-8")
I18N = (pathlib.Path(__file__).resolve().parents[1]
        / "static" / "i18n" / "en.json").read_text(encoding="utf-8")


def test_the_placeholder_is_no_longer_decided_before_the_action_is_known():
    """The whole defect in one line: the fallback used to be computed on the
    same line the text was read, which is before the action exists."""
    bad = re.search(
        r"const answer = \(j\.text \|\| ''\)\.trim\(\) \|\|.*empty_response", SRC)
    assert not bad, (
        "the placeholder is being chosen before the action is resolved again — "
        "a directive-only reply will render as '(empty response)'")


def test_the_answer_is_mutable_so_the_action_can_fill_it():
    assert re.search(r"let answer = \(j\.text \|\| ''\)\.trim\(\);", SRC), (
        "answer is no longer the binding the action-aware fallback writes to")


def test_a_directive_only_reply_uses_the_action_label():
    block = SRC[SRC.index("if (!answer) {"):]
    assert "actionDesc.label" in block[:600], "the action's label is not consulted"
    assert "action_only_proposed" in block[:600]
    assert "action_only_running" in block[:600]


def test_a_destructive_action_is_worded_as_a_proposal_not_as_done():
    """It has NOT run at that point — the inline-confirm chip renders beneath
    and nothing happens until it is clicked. Saying "Running" would describe a
    state the operator has not agreed to."""
    block = SRC[SRC.index("if (!answer) {"):]
    head = block[:600]
    assert "actionDesc.destructive" in head, (
        "destructive and non-destructive actions are worded identically")
    prop = head.index("action_only_proposed")
    run = head.index("action_only_running")
    assert prop < run, (
        "the destructive branch must be the one that says 'Proposed' — the "
        "ternary appears to be inverted")


def test_the_placeholder_still_exists_for_a_genuinely_empty_reply():
    """Not deleted — a reply with no text AND no directive really did say
    nothing, and that should still be visible as such."""
    block = SRC[SRC.index("if (!answer) {"):]
    assert "empty_response" in block[:900], (
        "the empty-reply placeholder was removed rather than narrowed")


def test_both_new_keys_are_in_the_bundle_with_the_action_placeholder():
    for key in ("action_only_proposed", "action_only_running"):
        assert f'"{key}"' in I18N, f"{key} missing from en.json"
    for line in I18N.splitlines():
        if "action_only_proposed" in line or "action_only_running" in line:
            assert "{action}" in line, (
                f"{line.strip()} has no {{action}} placeholder — the label "
                "would be dropped and every action would read alike")
