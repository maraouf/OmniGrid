"""An action emitted AFTER a tool call must still be dispatched.

The bug this pins took four investigations to find, because the backend was
right every time. Across three attempts the log said:

    ai_tool_call  switch52mp01  success          <- the MAC lookup ran
    [ai] palette ok ... action=bounce_interface  <- the action was parsed

and no `interface_bounce` op ever followed. `confirmInlineToolDispatch` — the
SECOND round, the one that composes a reply out of tool results — never read
`j.action` at all. It set the text, chained any further tool calls, and
dropped the directive on the floor.

Asking for the same bounce WITHOUT a lookup worked, because that needs no tool
round and goes through `sendAiSidebarMessage` instead. One request shape
worked and its close cousin silently did nothing, which is why this read as an
intermittent confirm-chip problem for two days.

The chip was never missing. The action never reached the code that offers it.

These tests read the source. That is weaker than exercising it, and it is the
same weakness that let the earlier version of this fix ship covering only the
first-round path — so they assert on the SECOND-ROUND function specifically,
by name, rather than on the file as a whole.
"""
from __future__ import annotations

import pathlib
import re

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "static" / "js" / "app-views-helpers.js").read_text(encoding="utf-8")


def _second_round() -> str:
    """The body of `confirmInlineToolDispatch` and nothing else.

    Scoped deliberately: asserting against the whole file would pass on the
    FIRST-round dispatch living in another function, which is precisely the
    mistake that let the original fix look complete while the path the
    operator actually hits stayed broken.
    """
    start = SRC.index("async confirmInlineToolDispatch(")
    end = SRC.index("cancelInlineToolDispatch(", start)
    return SRC[start:end]


def test_the_second_round_reads_the_action():
    body = _second_round()
    assert re.search(r"\bj\.action\b", body), (
        "confirmInlineToolDispatch ignores j.action again — an action emitted "
        "after a tool call will be silently discarded")


def test_the_second_round_dispatches_it():
    """Asserts the call is REACHABLE, not merely present.

    The first version of this test only checked that the string
    `_runCommandPaletteAction` appeared somewhere in the function — which it
    still does when the branch is disabled with `false &&`, so the test passed
    while the dispatch was dead. Presence is not reachability, and a test that
    cannot tell them apart reports safety it does not have.
    """
    body = _second_round()
    guard = "} else if (_actionDesc) {"
    assert guard in body, (
        "the dispatch guard is not the plain `else if (_actionDesc)` — if a "
        "condition was added in front of it, the dispatch may be unreachable")
    # Slice from AFTER the guard header — including it would match the guard's
    # own `if (` in the short-circuit check below.
    branch = body[body.index(guard) + len(guard):]
    call = branch.find("this._runCommandPaletteAction(")
    assert call != -1, "the action is read but never dispatched"
    # Nothing may short-circuit between the guard and the call.
    between = branch[:call]
    assert "return" not in between and "if (" not in between, (
        "something short-circuits between the guard and the dispatch")
    assert "surface: 'sidebar'" in branch[call:call + 400], (
        "dispatched without surface:'sidebar' — a destructive action would "
        "fire a SweetAlert popup into the sidebar instead of the inline chip")


def test_no_dead_guard_disables_the_dispatch():
    """Names the specific way the previous test was fooled, so the next person
    to reach for a quick disable trips a test that says why."""
    body = _second_round()
    for dead in ("false &&", "true &&", "0 &&"):
        assert dead not in body, f"dispatch guarded by a constant ({dead})"


def test_a_destructive_action_is_not_marked_as_already_run():
    """`action_ran` gates the green "Ran" chip. Marking a destructive action
    as run before the operator confirms would show it as done while it waits."""
    body = _second_round()
    assert "turn.action_ran = !_actionDesc.destructive" in body, (
        "action_ran no longer tracks the destructive flag")


def test_the_action_waits_for_the_tool_chain_to_finish():
    """A round still asking for another tool call has decided nothing. Acting
    there would fire on half-gathered evidence."""
    body = _second_round()
    chain = body.index("pending_tool_confirms = j.pending_tool_confirms")
    dispatch = body.index("_runCommandPaletteAction")
    assert chain < dispatch, "the action fires before the tool chain settles"
    assert "} else if (_actionDesc) {" in body, (
        "the dispatch is no longer the else-branch of the chain check, so a "
        "chained round could act early")


def test_the_action_params_are_carried_onto_the_turn():
    """The inline-confirm chip re-fires from the TURN, not from the response,
    so anything the action needs has to be stored there or 'Yes' runs a
    parameterless action — the class of bug that made a web reboot abort with
    'No host selected' while the same reboot worked over Telegram."""
    body = _second_round()
    for field in ("turn.action_id", "turn.action_label", "turn.action_data",
                  "turn.action_hosts", "turn.action_tag", "turn.action_item"):
        assert field in body, f"{field} is not carried onto the turn"


def test_a_directive_only_second_round_names_the_action():
    body = _second_round()
    assert "action_only_proposed" in body and "action_only_running" in body, (
        "a directive-only second round still renders '(empty response)'")
    head = body[body.index("if (!_answer) {"):][:700]
    assert head.index("action_only_proposed") < head.index("action_only_running"), (
        "the destructive branch must say 'Proposed' — the ternary looks inverted")


def test_the_placeholder_survives_for_a_genuinely_empty_reply():
    body = _second_round()
    assert "empty_response" in body, (
        "the placeholder was removed rather than narrowed — a reply with no "
        "text AND no directive should still read as empty")


def test_per_app_skill_destructive_is_resolved_here_too():
    """`run_app_skill` is declared non-destructive generically; the real flag
    lives on the skill. Without this the confirm gate disagrees with what the
    action does — on the tool-round path as much as the first-round one."""
    body = _second_round()
    assert "_appSkillIsDestructive" in body, (
        "a destructive per-app skill reached via a tool round would fire "
        "without a confirm")
