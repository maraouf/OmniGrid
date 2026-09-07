"""Tests for the AI sidebar reporting a port bounce honestly.

The report that prompted this: asking the assistant to find the port holding a
MAC and bounce it produced a reply saying it would do that, and then nothing
visible — no port, no progress, no outcome.

Three separate gaps sat behind it, and these pin the two that are structural:

1. The resolved port WAS reaching the browser. `find_mac_port` returns the
   interface and the SPA already stamped the whole tool result on the turn;
   nothing rendered it, so the port showed up only if the model happened to
   repeat it in prose.

2. The bounce route returns as soon as the Operation is SPAWNED, because the
   port is deliberately held down for `down_seconds` before coming back. So a
   200 means "started", not "bounced" — but the action discarded `op_id`,
   `interface` and `down_seconds` and returned a bare ok/detail, leaving the
   chat to render a green "Ran" instantly and never speak again.

The route half is asserted against the real handler; the browser half is
asserted against the shipped JS, since there is no JS test runner here.
"""
from __future__ import annotations

import importlib
import inspect
import json
import pathlib
import re

# `main` must be built FIRST: the main_pkg modules resolve through its
# star-import chain, so importing one directly is a circular import. Imported
# for the side effect only — via import_module so it doesn't leave an unused
# bound name behind, which is a lint finding in its own right.
importlib.import_module("main")
from main_pkg import hosts_ssh_routes  # noqa: E402  (must follow the line above)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_the_bounce_route_returns_what_a_watcher_needs():
    """op_id is what makes the outcome knowable; without it the caller can
    only ever report that it dispatched something."""
    src = inspect.getsource(hosts_ssh_routes.api_host_interface_bounce)
    for field in ('"op_id"', '"interface"', '"down_seconds"'):
        assert field in src, f"the bounce route no longer returns {field}"


def test_the_route_spawns_an_operation_rather_than_awaiting_the_hold():
    """Why the two-state UI is needed at all: the request returns during the
    hold, so 'responded' and 'finished' are genuinely different moments."""
    src = inspect.getsource(hosts_ssh_routes.api_host_interface_bounce)
    assert "bg.add_task" in src, (
        "the bounce no longer runs as a background op — if it now blocks until "
        "the port is back, the sidebar's running/done split is redundant")


def test_the_action_forwards_the_operation_detail_instead_of_dropping_it():
    src = _read("static/js/app-minor-tools.js")
    body = src[src.index("async bounceInterfaceAction("):]
    body = body[:body.index("\n  async ", 10)]
    for field in ("op_id", "down_seconds", "host_label"):
        assert field in body, (
            f"bounceInterfaceAction stopped returning {field} — the chat can no "
            f"longer follow the bounce to its result")


def test_a_result_without_an_op_id_is_left_alone():
    """Every other action's run() returns a plain toast shape; the watcher must
    not attach itself to those."""
    src = _read("static/js/app-ai.js")
    body = src[src.index("_stampOpWatchFromResult(turn, ret) {"):]
    body = body[:body.index("\n  },")]
    assert "!ret.op_id" in body and "return;" in body


def test_the_watcher_never_reports_success_it_did_not_observe():
    """A timeout, a 404 from the capped op log, and a failed op must each be
    distinguishable from a bounce that actually came back."""
    src = _read("static/js/app-ai.js")
    body = src[src.index("async _watchOpUntilDone(turnIdx) {"):]
    body = body[:body.index("\n  },")]
    assert "'unknown'" in body, "the give-up path no longer has its own state"
    assert "status === 'success' ? 'done' : 'failed'" in body, (
        "op status is no longer mapped through — a failed bounce could read as done")
    assert "deadline" in body, "the watcher lost its budget and can spin forever"


def test_the_watcher_budget_outlasts_the_hold():
    """A 30s hold polled with a 20s budget would always report unknown."""
    src = _read("static/js/app-ai.js")
    body = src[src.index("async _watchOpUntilDone(turnIdx) {"):]
    body = body[:body.index("\n  },")]
    m = re.search(r"deadline = Date\.now\(\) \+ holdMs \+ (?P<margin>\d+)", body)
    assert m, "the budget is no longer hold-plus-margin"
    margin = m.group("margin")
    assert int(margin) >= 30000, (
        f"margin {margin}ms over the hold is too tight to see a slow switch "
        f"finish")


def test_the_resolved_port_is_rendered_not_just_stored():
    """The bug was a value present in state and absent from the screen."""
    src = _read("static/js/app-views-helpers.js")
    body = src[src.index("aiToolFacts(turn) {"):]
    body = body[:body.index("\n  },")]
    assert "find_mac_port" in body
    assert "r.interface" in body
    html = _read("static/index.html")
    assert "aiToolFacts(turn)" in html, (
        "nothing in the markup calls aiToolFacts — the port is back to being "
        "stored and never shown")


def test_no_chip_when_no_port_was_resolved():
    """A lookup that found nothing must not render an empty fact; the reply
    explains why, and a blank chip would read as an answer."""
    src = _read("static/js/app-views-helpers.js")
    body = src[src.index("aiToolFacts(turn) {"):]
    body = body[:body.index("\n  },")]
    assert "if (!iface)" in body and "continue" in body


def test_in_flight_is_not_dressed_as_success():
    """The base chip is green and means 'ran'. Wearing it while the port is
    still DOWN is the exact confusion being fixed."""
    html = _read("static/index.html")
    assert "ai-bubble-action--pending" in html, (
        "the in-flight bounce no longer gets its own styling and renders in the "
        "success colour while the port is down")
    css = _read("static/css/style.css")
    assert ".ai-bubble-action--pending" in css and ".ai-bubble-action--failed" in css


def test_every_new_string_is_translatable():
    keys = ("fact_mac_port", "op_bouncing", "op_bounced",
            "op_bounce_failed", "op_bounce_unknown")
    bundle = json.loads(_read("static/i18n/en.json"))
    sidebar = bundle["ai_sidebar"]
    for k in keys:
        assert k in sidebar, f"ai_sidebar.{k} missing from en.json"
    # A key can be consumed from the markup or from a JS helper that builds
    # the string; both count as used, and neither counts as translatable
    # unless the key exists above.
    surfaces = _read("static/index.html") + _read("static/js/app-views-helpers.js")
    for k in keys:
        assert f"ai_sidebar.{k}" in surfaces, f"{k} is defined but never used"


def test_the_running_label_names_the_port_and_the_wait():
    """'Working…' would not answer either question someone watching a bounce
    has: which port, and how long until it is back."""
    bundle = json.loads(_read("static/i18n/en.json"))
    running = bundle["ai_sidebar"]["op_bouncing"]
    assert "{iface}" in running and "{seconds}" in running
    assert "{iface}" in bundle["ai_sidebar"]["op_bounced"], (
        "the finished label stopped naming the port")


def _stall_detector() -> str:
    src = _read("static/js/app-views-helpers.js")
    body = src[src.index("aiTurnStalled(turn, idx) {"):]
    return body[:body.index("\n  },")]


def _announcement_regex() -> str:
    """Lift the shipped JS pattern out and hand it back as a Python regex.

    Reading the real literal rather than restating it here is the point: a copy
    would keep passing after the shipped one was narrowed or broken, which is
    exactly the drift these tests exist to catch. The two flavours share the
    syntax used in it, so no translation is needed beyond stripping the
    delimiters.
    """
    body = _stall_detector()
    m = re.search(r"return (?P<pat>/\\b\(I.*?/i)\s*\n\s*\.test\(text\);",
                  body, re.S)
    assert m, "the announcement pattern is gone — a stalled reply is silent again"
    literal = m.group("pat")
    return literal[1:literal.rindex("/")]


def test_an_announced_but_undispatched_reply_is_detectable():
    """The observed failure: a reply promising to query a switch and bounce a
    port, with no directive behind it, ending the exchange in silence."""
    body = _stall_detector()
    observed = ("I'll query the switch switch52mp01 to find which port is holding "
                "MAC address DC:A6:32:B4:4D:29. Once the port is resolved, I will "
                "proceed with bouncing it.")
    assert re.search(_announcement_regex(), observed, re.I), (
        "the pattern no longer matches the reply that prompted this")


def test_an_ordinary_answer_is_not_flagged_as_stalled():
    """A false positive puts a 'didn't start it' warning under a perfectly good
    answer, which is worse than the silence it replaces."""
    body = _stall_detector()
    py = _announcement_regex()
    for benign in (
        "opnsense is using 1.01% of its disk.",
        "Three hosts are paused: dns01, dns02 and switch52.",
        "The port is gi12 and it is already up.",
    ):
        assert not re.search(py, benign, re.I), (
            f"ordinary answer flagged as stalled: {benign!r}")


def test_a_turn_that_did_dispatch_is_never_flagged():
    """Every outcome marker must veto the chip, or a successful bounce would
    show 'didn't start it' next to its own progress."""
    body = _stall_detector()
    for marker in ("action_label", "pending_confirm", "pending_tool_confirms",
                   "tool_results", "op_watch", "skill_panel", "cancelled"):
        assert marker in body, (
            f"{marker} no longer vetoes the stalled chip — a turn that DID act "
            f"can now be reported as stalled")


def test_only_the_newest_turn_is_offered_a_retry():
    """Re-asking an older question would run it out of order, behind whatever
    has happened since."""
    body = _stall_detector()
    assert "this.aiConversation.length - 1" in body
    assert "aiSidebarBusy" in body, "a retry can be offered mid-request"
