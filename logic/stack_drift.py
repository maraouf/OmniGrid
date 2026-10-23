"""Would redeploying a stack change what is running?

A stack update sends Portainer's STORED copy of the compose file back to
Swarm. When that copy has fallen behind what was last deployed — a CI job
runs ``docker stack deploy`` straight from the repository and never touches
the YAML saved in Portainer — the redeploy quietly puts the old definition
back. A service that had gained a start-up step loses it and crashes with
``ModuleNotFoundError``, and nothing in the redeploy dialog hinted at it.

This compares the two and names what would change. It is a PURE function over
``(stored YAML, live service specs, stack name)`` so it is testable without a
Portainer or a Swarm, and it only compares what it can compare honestly:

  * ``image`` — the reference, ignoring a pinned digest and an implicit
    ``:latest``.
  * ``command`` / ``entrypoint`` — compose ``command`` becomes the service's
    ``Args`` and ``entrypoint`` becomes its ``Command``; a string form is split
    the way compose splits it.
  * services present on only one side — ``prune`` is on, so a service missing
    from the stored file is REMOVED by the redeploy, and one missing from the
    cluster is created.

A value containing ``${VAR}`` is skipped, never guessed at: it resolves from
the stack's environment, and a guess would warn about a difference that is not
there — which teaches the operator to click through the warning that matters.
Environment variables, volumes and networks are not compared for the same
reason: too much of each is rewritten by compose on the way in.
"""
from __future__ import annotations

import re
import shlex
from typing import Any, Optional

import yaml

# A compose value that still holds an unresolved `${VAR}` / `$VAR`.
_INTERPOLATED = re.compile(r"\$\{?[A-Za-z_]")

# An argument that carries a credential inline (`--password=…`, `API_TOKEN=…`).
# Commands are shown to every role that can read an item, so the value is
# masked rather than shown. This catches the `name=value` shapes only — a
# secret passed as a separate positional argument is not recognisable.
_SECRET_ARG = re.compile(
    r"^(?P<head>.*(?:pass(?:word|wd)?|secret|token|api[-_]?key|credential)[\w-]*[=:]).+$",
    re.I | re.S)

# Long enough for a `sh -c` one-liner, short enough that a script pasted into
# a command cannot bloat every item in the list.
MAX_COMMAND_CHARS = 600


def _display_arg(arg: str) -> str:
    """One argument as shown: a credential masked, anything else shell-quoted."""
    m = _SECRET_ARG.match(arg)
    return f"{m.group('head')}***" if m else shlex.quote(arg)


def command_text(argv: Optional[list]) -> str:
    """One display string for an argv: credentials masked, length capped."""
    text = " ".join(_display_arg(str(a)) for a in (argv or []))
    if len(text) > MAX_COMMAND_CHARS:
        text = text[:MAX_COMMAND_CHARS - 1] + "…"
    return text


def _argv(value: Any) -> Optional[list[str]]:
    """A compose ``command`` / ``entrypoint`` as an argv; None when it is unset."""
    if value is None:
        return None
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        try:
            return shlex.split(value)
        except ValueError:
            return [value]
    return None


def _norm_image(ref: str) -> str:
    """The reference without its pinned digest, with an explicit tag."""
    ref = (ref or "").split("@", 1)[0].strip()
    if ref and ":" not in ref.rsplit("/", 1)[-1]:
        ref += ":latest"
    return ref


def _change(service: str, field: str, running: str, stored: str) -> dict:
    return {"service": service, "field": field, "running": running, "stored": stored}


def _service_changes(name: str, conf: dict, cs: dict) -> list[dict]:
    out: list[dict] = []
    image = conf.get("image")
    if isinstance(image, str) and not _INTERPOLATED.search(image):
        live_image = (cs.get("Image") or "").split("@", 1)[0]
        if _norm_image(image) != _norm_image(live_image):
            out.append(_change(name, "image", live_image, image))
    for field, stored_val, live_val in (
            ("entrypoint", conf.get("entrypoint"), cs.get("Command")),
            ("command", conf.get("command"), cs.get("Args"))):
        argv = _argv(stored_val)
        if argv is not None and any(_INTERPOLATED.search(a) for a in argv):
            continue
        want = argv or []
        have = [str(x) for x in (live_val or [])]
        if want != have:
            out.append(_change(name, field, command_text(have), command_text(want)))
    return out


def compute_drift(stored_yaml: str, live_services: list, stack_name: str) -> dict:
    """What a redeploy from ``stored_yaml`` would change on ``stack_name``.

    Returns ``{"ok": True, "changes": [...]}``, each change being
    ``{service, field, running, stored}`` with ``field`` one of ``image`` /
    ``command`` / ``entrypoint`` / ``added`` / ``removed``. ``ok`` is False
    (with a ``reason``) when the stored file cannot be read as a compose file —
    the caller treats that as "nothing to say", never as a block.
    """
    try:
        doc = yaml.safe_load(stored_yaml or "")
    except yaml.YAMLError:
        return {"ok": False, "reason": "unparseable", "changes": []}
    stored = doc.get("services") if isinstance(doc, dict) else None
    if not isinstance(stored, dict):
        return {"ok": False, "reason": "no_services", "changes": []}

    prefix = f"{stack_name}_"
    live: dict[str, dict] = {}
    for svc in live_services or []:
        spec = (svc or {}).get("Spec") or {}
        if (spec.get("Labels") or {}).get("com.docker.stack.namespace") != stack_name:
            continue
        full = spec.get("Name") or ""
        live[full[len(prefix):] if full.startswith(prefix) else full] = spec

    changes: list[dict] = []
    for name in sorted(stored):
        conf = stored[name] if isinstance(stored[name], dict) else {}
        spec = live.get(name)
        if spec is None:
            changes.append(_change(name, "added", "", ""))
            continue
        cs = (spec.get("TaskTemplate") or {}).get("ContainerSpec") or {}
        changes.extend(_service_changes(name, conf, cs))
    for name in sorted(set(live) - set(stored)):
        changes.append(_change(name, "removed", "", ""))
    return {"ok": True, "changes": changes}
