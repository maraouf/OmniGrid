"""Tests for a failed AI tool call reaching the operator.

What prompted these: asking the assistant to find the port holding a MAC and
bounce it produced a confident sentence and nothing else. The lookup HAD run
and HAD failed — the live audit row read

    AI dispatched find_mac_port({'host_id': 'switch52mp01', ...}) -> error

— but every path that could have said so was closed at once:

1. The fact chip rendered only on success, on the assumption that the reply
   would explain a failure. It does not; the model tends to re-announce the
   lookup it just made, so a failed probe reached the screen as a plain
   statement of intent.

2. The stalled-turn chip stood down whenever `tool_results` was set, reading
   "a tool ran" as "the turn acted". Running a lookup is not acting on what it
   returned, and that veto silenced precisely the turns worth flagging.

3. `find_mac_interface` logged nothing at all, so the reason the switch gave
   was unrecoverable afterwards — the truncated "-> error" above was the only
   trace that existed anywhere.

And the audit row that carried that text was filed with status "success",
because the computed status was spent on the message and never written to the
column, so filtering History for failures hid every failed tool call.

The Python halves are asserted against the real functions; the browser halves
against the shipped JS, since there is no JS test runner here.
"""
from __future__ import annotations

import inspect
import json
import pathlib

from logic import ai_extras, ssh

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _fact_helper() -> str:
    src = _read("static/js/app-views-helpers.js")
    body = src[src.index("aiToolFacts(turn) {"):]
    return body[:body.index("\n  },")]


def _stall_helper() -> str:
    src = _read("static/js/app-views-helpers.js")
    body = src[src.index("aiTurnStalled(turn, idx) {"):]
    return body[:body.index("\n  },")]


def test_a_failed_mac_lookup_renders_instead_of_vanishing():
    """The exact regression: no interface came back, so nothing was drawn."""
    body = _fact_helper()
    assert "fact_mac_port_failed" in body, (
        "a find_mac_port error no longer produces a chip — a failed lookup is "
        "back to reaching the operator as silence")
    assert "failed: true" in body


def test_any_other_tool_failure_is_surfaced_too():
    """The silence was a property of the surface, not of one tool."""
    body = _fact_helper()
    assert "fact_tool_failed" in body, (
        "only find_mac_port reports failures again; every other tool can fail "
        "invisibly")


def test_a_successful_lookup_is_not_dressed_as_a_failure():
    body = _fact_helper()
    assert "failed: false" in body, "the success chip lost its explicit state"
    # The success path must still be the one that names the port.
    assert "ai_sidebar.fact_mac_port'" in body or 'ai_sidebar.fact_mac_port"' in body


def test_a_tool_that_succeeded_quietly_adds_no_chip():
    """Only errors are surfaced — narrating good results is the model's job,
    and a chip per successful fetch would bury the ones that matter."""
    body = _fact_helper()
    assert "if (!err) {" in body and "continue;" in body


def test_running_a_lookup_no_longer_silences_the_stalled_chip():
    """The veto that hid the reported failure."""
    body = _stall_helper()
    veto = body[:body.index("const text")]
    assert "turn.tool_results" not in veto, (
        "tool_results vetoes the stalled chip again — a turn that fetched and "
        "then only described what it would do is silent once more")


def test_the_other_outcome_markers_still_veto():
    """Removing one veto must not have removed the rest: a turn that really
    did act must never be labelled stalled."""
    body = _stall_helper()
    for marker in ("action_label", "pending_confirm", "pending_tool_confirms",
                   "op_watch", "skill_panel", "cancelled"):
        assert marker in body, f"{marker} no longer vetoes the stalled chip"


def test_the_failed_chip_is_styled_as_a_failure():
    html = _read("static/index.html")
    assert "fact.failed ? 'ai-bubble-action--failed'" in html, (
        "a failed tool chip renders in the neutral fact colour again")


def test_every_new_string_is_translatable():
    bundle = json.loads(_read("static/i18n/en.json"))["ai_sidebar"]
    for key in ("fact_mac_port_failed", "fact_tool_failed"):
        assert key in bundle, f"ai_sidebar.{key} missing from en.json"
    assert "{error}" in bundle["fact_mac_port_failed"], (
        "the failed-lookup chip stopped carrying the switch's own reason")
    assert "{error}" in bundle["fact_tool_failed"]


def test_the_mac_lookup_records_why_it_failed():
    """It logged nothing, which is why the production failure could not be
    diagnosed after the fact."""
    src = inspect.getsource(ssh.find_mac_interface)
    assert src.count("print(") >= 3, (
        "find_mac_interface stopped logging its outcomes — the next failure "
        "will be as unreadable as the one that prompted this")
    assert "not present" in src, "the not-in-table path lost its log line"


def test_an_absent_mac_is_not_logged_as_an_error():
    """An address that simply is not in the table is an ordinary answer. The
    log classifier reads the tag-level prefix first, so these must declare
    INFO or they colour the log red on a normal outcome."""
    src = inspect.getsource(ssh.find_mac_interface)
    assert '[ssh] INFO mac lookup' in src, (
        "the benign not-found paths no longer self-declare INFO and will be "
        "bucketed as errors")
    # The one genuinely-broken path — the command itself not running — must
    # NOT be dressed as INFO, or a real fault stops showing as one.
    ran = src[src.index("could not run"):]
    assert "INFO" not in ran[:ran.index("\n")], (
        "the command-failure log line now self-declares INFO and a real "
        "failure would no longer surface as an error")


def test_a_failed_tool_call_is_audited_as_failed():
    """The row said success while its own message said error, so History
    could not be filtered for the calls worth finding."""
    src = inspect.getsource(ai_extras.dispatch_palette_tool)
    assert 'status="error" if failed else "success"' in src, (
        "the ai_tool_call audit row no longer records the real status")
    assert 'error=(str(result.get("error", ""))' in src, (
        "the audit row stopped carrying the tool's error text")
