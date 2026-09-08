"""Tests for the corrective re-ask when the AI narrates instead of acting.

The failure, reproduced against a real switch across several days: the
operator asks to find the port holding a MAC and bounce it. The lookup runs
and SUCCEEDS — the audit row reads `find_mac_port ... -> ok`, the log reads
`mac lookup on switch52mp01: dc:a6:32:b4:4d:29 is on gi3`, and the port even
renders in the chat. The reply is still:

    "I will search for the MAC address DC:A6:32:B4:4D:29 on switch52mp01 to
     locate the correct port before initiating the bounce."

An announcement, with the answer already in hand, and no directive under it.
Nothing fires and the exchange ends there. `interface_bounce` in the audit
trail stayed at exactly one row for days — and that one was a Telegram slash
command where the operator supplied the port themselves.

The system prompt already carries a worked locate-then-bounce example and a
rule forbidding precisely this, so louder instructions up front are not the
lever; the model stops mid-task and cannot be warned about it in advance.
What it never had was its own reply quoted back with the results still
present. That is what the re-ask supplies, once, bounded.
"""
from __future__ import annotations

import inspect
import pathlib
import re

from logic import ai_extras

ROOT = pathlib.Path(__file__).resolve().parents[1]

OBSERVED = ("I will search for the MAC address DC:A6:32:B4:4D:29 on "
            "switch52mp01 to locate the correct port before initiating the bounce.")


def _route_src() -> str:
    return (ROOT / "main_pkg" / "admin_ai_routes.py").read_text(encoding="utf-8")


def test_the_observed_reply_is_recognised_as_an_announcement():
    assert ai_extras.reply_announces_without_acting(OBSERVED)


def test_an_answer_that_states_a_result_is_not_an_announcement():
    """A false positive here costs a wasted provider call on every good
    reply, so the detector has to stay narrow."""
    for good in (
        "dc:a6:32:b4:4d:29 is on gi3 of switch52mp01.",
        "The port is gi3 and it is already up.",
        "Three hosts are paused: dns01, dns02 and switch52.",
        "I could not reach the switch, so there is nothing to bounce.",
    ):
        assert not ai_extras.reply_announces_without_acting(good), good


def test_an_empty_reply_is_not_an_announcement():
    assert not ai_extras.reply_announces_without_acting("")
    assert not ai_extras.reply_announces_without_acting(None)


def test_the_backend_and_the_browser_agree_on_what_an_announcement_is():
    """Both sides answer the same question — the backend to decide whether to
    re-ask, the browser to decide whether to warn. If they drift, one of them
    is silently wrong about the same reply."""
    py = inspect.getsource(ai_extras).split("_ANNOUNCE_RE")[1]
    js = (ROOT / "static" / "js" / "app-views-helpers.js").read_text(encoding="utf-8")
    js_body = js[js.index("aiTurnStalled(turn, idx) {"):]
    js_body = js_body[:js_body.index("\n  },")]
    for verb in ("query", "check", "look", "find", "fetch", "retrieve", "ask",
                 "proceed", "run", "bounce", "restart", "reboot", "resolve",
                 "locate", "search"):
        assert verb in py, f"backend detector lost the verb {verb!r}"
        assert verb in js_body, f"SPA detector lost the verb {verb!r}"
    assert "{0,80}" in py and "{0,80}" in js_body, (
        "the two detectors no longer bound the pronoun-to-verb window the same way")


def test_the_reask_only_fires_with_results_in_hand():
    """Re-asking a round that fetched NOTHING would just be arguing with the
    model about a lookup it has not run yet — the point is that the answer is
    already present and it stopped anyway."""
    src = _route_src()
    assert "if isinstance(out, dict) and tool_results:" in src, (
        "the re-ask no longer requires tool results, so it can fire on a "
        "first round that legitimately still needs to fetch")


def test_the_reask_does_not_fire_when_the_model_did_act():
    src = _route_src()
    seg = src[src.index("re-asking once") - 2000:src.index("re-asking once")]
    assert "not _acts" in seg and "not _more" in seg, (
        "the re-ask fires even when an action or a follow-up tool was emitted")


def test_the_reask_happens_at_most_once():
    """A model that announces twice would otherwise loop, burning a provider
    call each time."""
    src = _route_src()
    seg = src[src.index("re-asking once"):]
    seg = seg[:seg.index("# Split the optional")] if "# Split the optional" in seg else seg
    assert seg.count("ask_provider_with_fallback") == 1, (
        "more than one retry call in the corrective block — this must not loop")


def test_a_retry_that_announces_again_is_discarded():
    """Replacing one announcement with another is not progress, and throwing
    away the first would also throw away the reply the rendered chip matches."""
    src = _route_src()
    assert "keeping the original reply" in src
    assert "_racts or not _ai.reply_announces_without_acting(_rt)" in src, (
        "the retry is accepted unconditionally — a second announcement would "
        "silently replace the first")


def test_the_corrective_tells_the_model_this_is_the_last_round():
    """Without that, the obvious next move for a model that has just been
    told off for promising work is to promise it again more politely."""
    src = _route_src()
    assert "this is the last round" in src
    assert "only promise here will never happen" in src


def test_the_reask_is_visible_in_the_logs():
    """It spends a provider call; an operator reading Admin -> Logs should be
    able to see that it happened and why."""
    src = _route_src()
    assert re.search(r'print\(\s*"\[ai\] palette: reply announced work', src), (
        "the re-ask no longer logs, so the extra call is invisible")
def test_a_polite_offer_to_act_next_is_not_an_announcement():
    r"""The false positive this nearly shipped with, found by running the
    matcher over the replies the switch work actually produced.

    The re-ask only runs on a reply that used a tool and emitted no directive
    — which is exactly the shape of a GOOD final answer: it looked something
    up, said what it found, and offered the next step back to the operator.
    "The port is gi3. Let me know if you want me to bounce it." was matching,
    because `Let me` sat within 80 characters of `bounce`. That is the single
    most ordinary way to close such a reply, so the re-ask would have fired on
    a large share of healthy turns and quietly doubled the spend on them.

    `Let me KNOW` hands the next step to the operator; it is the opposite of
    announcing your own work, and is carved out on both sides of the wire.
    """
    for reply in (
        "The port is gi3. Let me know if you want me to bounce it.",
        "Let me know whether to restart it.",
        "Let me know and I can run it.",
        "gi3. Let me know if you'd like me to check the neighbours too.",
        "Done. Let me know if you want me to look up anything else.",
    ):
        assert not ai_extras.reply_announces_without_acting(reply), reply


def test_other_ways_of_offering_rather_than_announcing_are_also_quiet():
    """Hedges and hand-backs that name an action but do not claim to be
    performing one."""
    for reply in (
        "I'll wait for your go-ahead.",
        "I'll notify you when it's back.",
        "I'll tell you when it finishes.",
        "Tell me if I should restart it.",
        "If you want, I can bounce gi3.",
        "Would you like me to restart it?",
    ):
        assert not ai_extras.reply_announces_without_acting(reply), reply


def test_every_shape_of_the_real_announcement_still_fires():
    """The carve-out must not buy quiet by going deaf. A missed announcement
    is the ORIGINAL bug — narration with nothing behind it — and costs more
    than the wasted call a false positive costs, so these are the assertions
    that constrain how far the exclusions may ever go."""
    for reply in (
        "I'll query the switch switch52mp01 to find which port is holding "
        "MAC address DC:A6:32:B4:4D:29. I will proceed with bouncing it.",
        "I will look up which port has seen that MAC so we can proceed.",
        "I will trigger a reboot for adguard1 shortly.",
        "Let me check the switch for that MAC.",
        "Let me look up the port first.",
        "I'm going to run the lookup now.",
        "I am going to restart the container.",
        "First I'll find the port, then bounce it.",
    ):
        assert ai_extras.reply_announces_without_acting(reply), reply
