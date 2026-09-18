"""Why a Swarm task did not start.

Swarm reports a failed task as `task: non-zero exit (255)` and stops
there. That string is the exit status of a process whose actual
complaint — a config key it could not parse, a volume it could not
write, an architecture it cannot execute — was written to its own
stdout seconds earlier and then discarded with the container. The
operator sees a number; the reason is one Portainer tab away, which is
far enough that nobody looks.

This module reads those logs and names the cause. It is deliberately a
PURE function over ``(task_error, logs)`` so the whole classifier is
testable without Portainer, a Swarm, or a failing container.

Two properties are load-bearing:

  * **It returns a cause ID, never prose.** The SPA renders
    ``drawer.diagnose.cause_<id>`` through ``t()``, so a diagnosis reads
    in the operator's language like everything else. A backend that
    returned an English sentence would be the one untranslated string in
    the drawer.

  * **`unknown` is a real answer.** A classifier that always names
    something is worse than one that admits it does not know: the
    operator stops trusting it the first time a confident label sends
    them down the wrong path. When nothing matches, the evidence lines
    still come back — the last error-shaped lines the container wrote —
    because showing the operator the logs is useful even when we cannot
    interpret them.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Ordered — FIRST match wins, so specific causes precede generic ones.
# `crashed` in particular would swallow half the table if it ran early: a
# Go service with a bad config file panics ABOUT the config file, and
# "your config is invalid" is the useful answer, not "it panicked".
_RULES: tuple[tuple[str, re.Pattern[str], tuple[str, ...]], ...] = (
    # Wrong architecture. Endemic on mixed fleets (an amd64 image pulled
    # onto an arm64 node); the kernel refuses the binary outright.
    ("exec_format", re.compile(r"exec format error", re.I), ("rollback",)),
    # Entrypoint / command does not exist in the image — usually a
    # compose typo or an upstream image that moved its binary.
    ("entrypoint_missing", re.compile(
        r"executable file not found|starting container process caused|"
        r"no such file or directory: unknown|"
        r"OCI runtime (?:create|exec) failed", re.I), ("rollback",)),
    # Config the app refuses to parse. Distinct from a missing value —
    # this one is malformed rather than absent.
    ("config_invalid", re.compile(
        r"invalid config(?:uration)?|failed to (?:parse|load) config|"
        r"yaml: |cannot unmarshal|malformed|unknown flag|"
        r"unrecognized (?:option|argument)|invalid character .{1,3} looking for", re.I),
     ("rollback",)),
    # A required setting the operator has not supplied. A new release
    # adding a mandatory env var is the classic post-update failure.
    ("config_missing", re.compile(
        r"(?:environment variable|env var|config(?:uration)? (?:key|value|option))"
        r"[^\n]{0,60}(?:not set|is required|missing|must be)|"
        r"required (?:environment variable|setting|config)|"
        r"please set [A-Z][A-Z0-9_]{2,}|"
        r"missing required", re.I), ("rollback",)),
    # Volume / bind-mount ownership. Restarting never fixes this, so no
    # action is offered — the fix is on the host filesystem.
    ("permission_denied", re.compile(
        r"permission denied|operation not permitted|read-only file system|"
        r"cannot create directory|unable to open database file", re.I), ()),
    # Something already holds the port. A stale container from the
    # previous task sometimes still owns it, so a restart is worth a try.
    ("port_conflict", re.compile(
        r"address already in use|failed to bind|bind: address|"
        r"listen tcp.*address already", re.I), ("restart",)),
    # Credentials the app was given were refused by ITS upstream (its
    # database, an API). Not something OmniGrid can retry into working.
    ("auth_failed", re.compile(
        r"password authentication failed|access denied for user|"
        r"authentication failed|invalid credentials|401 unauthorized|"
        r"permission denied for (?:database|relation|schema)", re.I), ()),
    # Schema work that did not complete. Rollback is the usual fix when a
    # new version's migration aborts — with the caveat the SPA states: a
    # half-applied migration can leave the old version unhappy too.
    ("migration_failed", re.compile(
        r"migration (?:failed|error)|failed to migrate|"
        r"no such table|relation .{1,80} does not exist|"
        r"schema version|database is locked", re.I), ("rollback",)),
    # It cannot reach something it needs. Often ordering rather than
    # breakage — the database is still starting — so a restart genuinely
    # helps here, unlike most of this table.
    ("dependency_unreachable", re.compile(
        r"connection refused|no route to host|dial tcp|"
        r"name or service not known|could not resolve host|"
        r"temporary failure in name resolution|i/o timeout|"
        r"failed to connect to", re.I), ("restart",)),
    # Killed for memory. The task error's own 137 is a second signal,
    # handled by `_EXIT_CODE_CAUSES` when the logs say nothing.
    ("out_of_memory", re.compile(
        r"out of memory|cannot allocate memory|oomkilled|"
        r"killed process|memory limit", re.I), ()),
    # Generic crash — last in the table on purpose (see the note above).
    ("crashed", re.compile(
        r"panic:|fatal error|traceback .most recent call last.|"
        r"unhandled exception|segmentation fault|"
        r"terminate called after throwing", re.I), ("rollback",)),
)

# Exit codes that carry meaning on their own. Consulted only when the
# logs matched nothing — a log line always beats a number.
_EXIT_CODE_CAUSES: dict[int, str] = {
    126: "permission_denied",   # found but not executable
    127: "entrypoint_missing",  # command not found
    # 137 is 128+9 — SIGKILL, and that is ALL it says. The OOM killer is
    # one sender; a `docker stop` whose grace period expired, a failing
    # healthcheck's kill, an operator, or the orchestrator draining the
    # node are others, and they are not rare. Naming this `out_of_memory`
    # told an operator to raise a memory limit that was never the problem
    # while the panel's own evidence line talked about TLS certificates.
    # Docker records the truth in the dead container's `State.OOMKilled`,
    # which this classifier cannot see — so it says what it knows.
    137: "killed_by_signal",
}

# Which actions a cause allows, derived from the rule table so the two
# can never disagree. The exit-code path used to offer `rollback` for
# ANY cause it named, which handed the operator a destructive button for
# an out-of-memory kill — a cause whose rule declares no action at all,
# because nothing about rolling back a spec addresses it.
_CAUSE_ACTIONS: dict[str, tuple[str, ...]] = {c: a for c, _p, a in _RULES}
# Signalled-kill is only reachable from the exit code, so it carries no
# rule entry. Nothing to retry into and nothing an image swap fixes.
_CAUSE_ACTIONS.setdefault("killed_by_signal", ())

# Lines worth showing when no rule matched. Same idea as the rules, but
# for picking evidence rather than naming a cause.
_ERRORISH = re.compile(
    r"\b(?:error|fatal|panic|fail(?:ed|ure)?|exception|cannot|unable|denied|"
    r"refused|invalid|missing|timeout)\b", re.I)

# Docker log lines carry an RFC3339 timestamp when the API is asked for
# them. Strip it: the operator wants the message, and the stamps make the
# evidence block twice as wide for no benefit.
_TS_PREFIX = re.compile(r"^\S+T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\s+")

_EXIT_RE = re.compile(r"non-zero exit \((?P<code>\d+)\)")
_MAX_EVIDENCE = 4
_MAX_LINE = 400

# Swarm states that mean an update is in flight or was abandoned — see
# `diagnose`'s `after_update`.
_UPDATE_IN_FLIGHT = ("updating", "paused", "rollback_started", "rollback_paused")


def exit_code_of(task_error: str) -> Optional[int]:
    """The exit status Swarm reported, or None when it said something else."""
    m = _EXIT_RE.search(task_error or "")
    if not m:
        return None
    try:
        return int(m.group("code"))
    except (TypeError, ValueError):
        return None


def _clean(line: str) -> str:
    """One log line, timestamp stripped and length-capped."""
    return _TS_PREFIX.sub("", line.rstrip())[:_MAX_LINE]


def _evidence(lines: list[str], pattern: Optional[re.Pattern[str]]) -> list[str]:
    """The lines that justify the verdict, oldest first.

    Scanned bottom-up because the interesting failure is what the
    container said LAST — a long startup banner would otherwise fill the
    quota before the error ever appeared.
    """
    hits: list[str] = []
    for raw in reversed(lines):
        line = _clean(raw)
        if not line:
            continue
        probe = pattern if pattern is not None else _ERRORISH
        if not probe.search(line):
            continue
        hits.append(line)
        if len(hits) >= _MAX_EVIDENCE:
            break
    if not hits:
        # Nothing error-shaped at all: show the tail anyway. A container
        # that exits mid-banner without complaining is itself a finding.
        hits = [c for c in (_clean(x) for x in lines[-_MAX_EVIDENCE:]) if c]
        return hits
    hits.reverse()
    return hits


def diagnose(task_error: str = "", logs: str = "", *,
             update_state: str = "",
             rollback_available: bool = False) -> dict[str, Any]:
    """Name the cause of a failed task from its logs.

    `update_state` is Swarm's own ``UpdateStatus.State`` for the service;
    with `rollback_available` it decides whether this reads as "broke
    after an update", which is the difference between offering a rollback
    and offering nothing.
    """
    lines = [ln for ln in (logs or "").splitlines() if ln.strip()]
    code = exit_code_of(task_error)
    # An update Swarm itself considers unfinished, or one it already gave
    # up on, is the strongest available evidence that the failure arrived
    # WITH the new spec rather than independently of it.
    after_update = bool(rollback_available) and (update_state or "").lower() in _UPDATE_IN_FLIGHT

    for cause, pattern, actions in _RULES:
        if any(pattern.search(ln) for ln in lines):
            acts = [a for a in actions if a != "rollback" or rollback_available]
            return {
                "cause": cause,
                "confidence": "high",
                "exit_code": code,
                "evidence": _evidence(lines, pattern),
                # These lines are the ones that MATCHED the rule, so they
                # are genuinely why this cause was named.
                "evidence_supports_cause": True,
                "actions": acts,
                "after_update": after_update,
            }

    # No log line matched. The exit code is a weaker signal — report it as
    # such rather than dressing it up as a diagnosis.
    fallback = _EXIT_CODE_CAUSES.get(code) if code is not None else None
    if fallback:
        # A named cause owns its actions whichever way it was reached. The
        # alternative — offering rollback because a previous spec happens
        # to exist — contradicts the cause the panel just printed.
        acts = [a for a in _CAUSE_ACTIONS.get(fallback, ())
                if a != "rollback" or rollback_available]
    else:
        # Genuinely unknown. A previous spec is the one lever left, so it
        # stays on offer as a last resort rather than a diagnosis.
        acts = ["rollback"] if rollback_available else []
    return {
        "cause": fallback or "unknown",
        "confidence": "low",
        "exit_code": code,
        "evidence": _evidence(lines, None),
        # Nothing in the log was matched, so the lines returned are the
        # tail's error-SHAPED output, not support for this cause. The
        # panel labels them differently on the strength of this flag —
        # without it a benign "missing certificate" warning renders as
        # the proof of an out-of-memory kill, which is how this was found.
        "evidence_supports_cause": False,
        "actions": acts,
        "after_update": after_update,
    }
