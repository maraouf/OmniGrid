"""A failed task's exit code is not a reason; its log is.

The drawer showed `task: non-zero exit (255)` under LATEST TASK ERROR and
nothing else — the exit status of a process whose actual complaint went
to its own stdout and died with the container. `logic.task_diagnosis`
reads those logs and names the cause, and because it is a pure function
over `(task_error, logs)` the whole classifier is testable here without a
Swarm, a Portainer, or a service that will not start.

What these pin, beyond "the regexes match":

  * **Rule ORDER.** A Go service with a broken config panics ABOUT the
    config. `crashed` sits last in the table for exactly that reason, and
    a future edit that moves it up would turn every specific diagnosis
    into "it crashed" — true, useless, and silently so.

  * **`unknown` survives.** A classifier that always names something is
    worse than one that admits it does not know, because the operator
    stops trusting it the first time a confident label sends them the
    wrong way.

  * **Rollback is only offered when Swarm can actually do it.** The
    button asks for `?rollback=previous`; a service with no PreviousSpec
    answers that with a 500 whose body does not explain itself.
"""
from __future__ import annotations

import pathlib

import pytest

from logic.task_diagnosis import diagnose, exit_code_of

_EXIT_255 = "task: non-zero exit (255)"


def test_the_exit_code_is_read_off_swarms_own_string():
    assert exit_code_of(_EXIT_255) == 255
    assert exit_code_of("task: non-zero exit (137)") == 137
    # Swarm says plenty of other things; none of them carry a code.
    assert exit_code_of("no suitable node (scheduling constraints not satisfied)") is None
    assert exit_code_of("") is None


@pytest.mark.parametrize("cause,line", [
    ("exec_format", "standard_init_linux.go:228: exec user process caused: exec format error"),
    ("entrypoint_missing", 'OCI runtime create failed: executable file not found in $PATH'),
    ("config_invalid", "yaml: line 12: did not find expected key"),
    ("config_missing", "Error: environment variable TRACEARR_API_KEY is required"),
    ("permission_denied", "open /config/app.db: permission denied"),
    ("port_conflict", "listen tcp 0.0.0.0:3000: bind: address already in use"),
    ("auth_failed", 'FATAL: password authentication failed for user "app"'),
    ("migration_failed", 'relation "users" does not exist'),
    ("dependency_unreachable", "dial tcp 10.0.1.5:5432: connect: connection refused"),
    ("out_of_memory", "fatal: runtime: out of memory"),
    ("crashed", "panic: runtime error: invalid memory address"),
])
def test_each_cause_is_recognised_from_one_real_log_line(cause, line):
    out = diagnose(_EXIT_255, f"starting up\n{line}\n")
    assert out["cause"] == cause, out
    assert out["confidence"] == "high"
    assert line in out["evidence"][-1] or out["evidence"][-1] in line


def test_a_specific_cause_wins_over_the_generic_crash():
    """The ordering guard. A Go app with a bad config panics ABOUT the
    config — reporting 'it crashed' would be true and useless."""
    logs = ("panic: failed to load config: yaml: line 3: mapping values are "
            "not allowed in this context\n")
    assert diagnose(_EXIT_255, logs)["cause"] == "config_invalid"


def test_nothing_recognised_stays_honest():
    out = diagnose(_EXIT_255, "listening on :8080\nshutting down\n")
    assert out["cause"] == "unknown"
    assert out["confidence"] == "low"
    # Even with no verdict the tail comes back — the operator can read it.
    assert out["evidence"], "gave up without showing the operator anything"


def test_the_exit_code_is_a_weaker_answer_than_a_log_line():
    """127 means command-not-found, but only when the log said nothing —
    a real log line always beats a number, and the confidence says so."""
    by_code = diagnose("task: non-zero exit (127)", "")
    assert by_code["cause"] == "entrypoint_missing"
    assert by_code["confidence"] == "low"
    by_log = diagnose("task: non-zero exit (127)", "open /data: permission denied")
    assert by_log["cause"] == "permission_denied"
    assert by_log["confidence"] == "high"


def test_a_cause_named_from_the_exit_code_still_owns_its_actions():
    """The drawer offered a red "Roll back to previous version" for an
    out-of-memory kill on a service whose local and remote digests were
    IDENTICAL — no update had happened, and the cause's own rule declares
    no action because nothing about swapping the spec addresses it. The
    exit-code path was handing out rollback for any cause it named."""
    for err, cause in (("task: non-zero exit (137)", "killed_by_signal"),
                       ("task: non-zero exit (126)", "permission_denied")):
        out = diagnose(err, "nothing here matches a rule",
                       update_state="updating", rollback_available=True)
        assert out["cause"] == cause, out
        assert out["actions"] == [], f"{cause} was offered {out['actions']}"


def test_137_is_a_signal_not_a_memory_verdict():
    """128+9 is SIGKILL and says nothing about the sender. Claiming OOM
    sent an operator to raise a memory limit that was never the problem.
    A log that DOES say so is still named out_of_memory, with the higher
    confidence that comes from having read it."""
    guess = diagnose("task: non-zero exit (137)", "starting up\nlistening on :8181")
    assert guess["cause"] == "killed_by_signal"
    assert guess["confidence"] == "low"
    real = diagnose("task: non-zero exit (137)", "fatal: runtime: out of memory")
    assert real["cause"] == "out_of_memory"
    assert real["confidence"] == "high"


def test_unmatched_evidence_is_not_presented_as_support_for_the_cause():
    """The panel showed a benign 'Disabled HTTPS because of missing
    certificate and key' warning directly under 'Killed — out of memory'.
    It was picked only because the error-shaped scan matches the word
    `missing`. The lines are still worth showing; the flag is what stops
    the drawer captioning them as the reason."""
    benign = ("2026-09-18 10:25:04 - WARNING :: MainThread : Tautulli WebStart "
              ":: Disabled HTTPS because of missing certificate and key.")
    out = diagnose("task: non-zero exit (137)", benign, rollback_available=True)
    assert out["evidence_supports_cause"] is False
    assert out["evidence"], "still shows the operator the tail"
    matched = diagnose(_EXIT_255, "open /config: permission denied")
    assert matched["evidence_supports_cause"] is True


def test_a_genuinely_unknown_failure_keeps_rollback_as_a_last_resort():
    """Narrowing the exit-code path must not take the escape hatch away
    from the case where nothing at all is known."""
    out = diagnose("task: non-zero exit (99)", "no rule matches this",
                   rollback_available=True)
    assert out["cause"] == "unknown"
    assert out["actions"] == ["rollback"]


def test_rollback_is_offered_only_when_swarm_kept_a_previous_spec():
    logs = "Error: config key API_URL is required"
    assert diagnose(_EXIT_255, logs, rollback_available=True)["actions"] == ["rollback"]
    # No PreviousSpec: the action would 500 on a body that explains nothing.
    assert diagnose(_EXIT_255, logs, rollback_available=False)["actions"] == []


def test_a_cause_no_retry_can_fix_offers_no_retry():
    """Restarting into a permissions problem or a rejected password just
    burns another task. The classifier owns that judgement, not the UI."""
    for logs in ("open /config: permission denied",
                 'FATAL: password authentication failed for user "app"'):
        out = diagnose(_EXIT_255, logs, rollback_available=True)
        assert "restart" not in out["actions"], out


def test_an_ordering_failure_does_offer_a_retry():
    out = diagnose(_EXIT_255, "dial tcp 10.0.1.5:5432: connect: connection refused")
    assert out["actions"] == ["restart"]


def test_after_update_needs_both_a_rollback_target_and_swarms_own_verdict():
    logs = "panic: boom"
    assert diagnose(_EXIT_255, logs, update_state="updating",
                    rollback_available=True)["after_update"] is True
    # Update long finished: the failure is not evidence about the update.
    assert diagnose(_EXIT_255, logs, update_state="completed",
                    rollback_available=True)["after_update"] is False
    # Nothing to roll back to — the claim would have no action behind it.
    assert diagnose(_EXIT_255, logs, update_state="updating",
                    rollback_available=False)["after_update"] is False


def test_evidence_is_the_end_of_the_log_not_the_start():
    """A chatty startup banner must not crowd out the failure. Evidence is
    collected bottom-up for that reason."""
    logs = "\n".join([f"line {i} connecting" for i in range(200)]
                     + ["FATAL: could not resolve host db.internal"])
    out = diagnose(_EXIT_255, logs)
    assert out["cause"] == "dependency_unreachable"
    assert "db.internal" in out["evidence"][-1]


def test_evidence_keeps_the_LAST_matches_when_many_lines_match():
    """The direction guard, which the banner case above cannot make: when
    every line matches the rule, a top-down scan would fill the quota with
    the FIRST four retries and hide the attempt that actually gave up."""
    logs = "\n".join(f"attempt {i}: connection refused" for i in range(1, 21))
    out = diagnose(_EXIT_255, logs)
    assert out["cause"] == "dependency_unreachable"
    assert out["evidence"][-1] == "attempt 20: connection refused", out["evidence"]
    assert "attempt 1:" not in " ".join(out["evidence"])


def test_an_unrecognised_failure_still_prefers_its_error_lines():
    """With no rule matched the evidence picker falls back to its own
    error-shaped scan — that scan has to actually match, or the panel
    shows the last four lines of a shutdown banner instead of the fault."""
    logs = "\n".join(
        ["Error: widget subsystem returned -12"]
        + [f"cleanup step {i} ok" for i in range(10)])
    out = diagnose(_EXIT_255, logs)
    assert out["cause"] == "unknown"
    assert any("widget subsystem" in line for line in out["evidence"]), out["evidence"]


def test_docker_timestamps_are_stripped_from_the_evidence():
    out = diagnose(_EXIT_255, "2026-09-15T18:19:26.654599138Z panic: bad config")
    assert out["evidence"] == ["panic: bad config"], out["evidence"]


def test_evidence_is_bounded_in_lines_and_width():
    logs = "\n".join(["error " + "x" * 900] * 40)
    out = diagnose(_EXIT_255, logs)
    assert len(out["evidence"]) <= 4
    assert all(len(line) <= 400 for line in out["evidence"])


def test_empty_input_does_not_raise():
    out = diagnose("", "")
    assert out["cause"] == "unknown"
    assert out["evidence"] == []
    assert out["exit_code"] is None


# --- The surfaces that carry it ---------------------------------------
#
# Source-reading, which is weaker than executing the path — so these
# assert the STRUCTURE the wiring depends on rather than that a string
# appears somewhere in the file.

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_every_cause_the_classifier_can_return_has_a_label_and_a_hint():
    """The backend answers with an ID and the SPA translates it, so a
    cause with no key renders as a blank line in the drawer — the one
    failure mode of that split, and invisible from the Python side."""
    import json
    from logic import task_diagnosis
    bundle = json.loads(_read("static/i18n/en.json"))
    keys = bundle["drawer"]["diagnose"]
    causes = {c for c, _p, _a in task_diagnosis._RULES}
    causes |= set(task_diagnosis._EXIT_CODE_CAUSES.values())
    causes.add("unknown")
    missing = sorted(c for c in causes
                     if f"cause_{c}" not in keys or f"hint_{c}" not in keys)
    assert not missing, f"no i18n label/hint for: {missing}"


def test_the_drawer_asks_for_a_diagnosis_and_renders_the_evidence():
    html = _read("static/index.html")
    assert "diagnoseTaskError(drawerItem)" in html
    assert "drawer.diagnose.cause_" in html, "the cause is not translated"
    assert "diagnosisActions(drawerItem)" in html, "no fix is offered"


def test_the_drawer_captions_unmatched_evidence_differently():
    """`evidence_supports_cause` only earns its place if the panel reads
    it — a flag nothing branches on is decorative, and the misleading
    caption this exists to fix would still be on screen."""
    html = _read("static/index.html")
    assert "evidence_supports_cause" in html, "the drawer ignores the flag"
    assert "drawer.diagnose.evidence_label_unmatched" in html, "no alternate caption"
    # Both captions must survive: the matched one is still correct when a
    # rule fired, and collapsing to one label is how this regressed.
    assert "drawer.diagnose.evidence_label'" in html or \
           'drawer.diagnose.evidence_label"' in html, "lost the matched caption"


def test_both_evidence_captions_exist_in_the_bundle():
    """A branch pointing at a key the bundle lacks renders a blank line —
    the documented failure mode of the ID-not-prose split."""
    import json
    bundle = json.loads(_read("static/i18n/en.json"))
    keys = bundle["drawer"]["diagnose"]
    for k in ("evidence_label", "evidence_label_unmatched"):
        assert keys.get(k), f"missing i18n key: {k}"


def test_the_rollback_button_posts_to_the_rollback_route():
    js = _read("static/js/app-minor-tools.js")
    start = js.index("async runTaskErrorAutoFix(")
    body = js[start:js.index("taskErrorKnownIssue(", start)]
    assert "rollback_service" in body, "the dispatcher cannot run a rollback"
    assert "/api/rollback/service/" in body


def test_rollback_is_marked_destructive_in_the_ui():
    """It replaces a running spec. The confirm gate in the dispatcher keys
    off `danger`, so losing that flag silently removes the confirm."""
    js = _read("static/js/app-minor-tools.js")
    start = js.index("diagnosisActions(item)")
    body = js[start:start + 2000]
    rb = body.index("diag-rollback")
    assert "danger: true" in body[rb:rb + 600], "rollback lost its confirm gate"


def test_the_backend_only_offers_a_rollback_swarm_can_honour():
    """`?rollback=previous` against a service with no PreviousSpec is a
    500 with an unexplained body — the check has to happen first."""
    src = _read("logic/ops_extras.py")
    start = src.index("async def do_rollback_service(")
    body = src[start:src.index("async def discover_swarm_agent_service(", start)]
    guard = body.index("PreviousSpec")
    call = body.index("rollback=previous")
    assert guard < call, "the rollback fires before checking there is one"
