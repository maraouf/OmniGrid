"""Native tool-call schemas for the diagnostic tools, per provider wire format.

Today a tool call is PROSE: the model is asked to emit two consecutive lines
(``TOOL: <name>`` then ``TOOL_ARGS: {<json>}``) which are regex-parsed back out.
Nothing constrains that output, which is why ``PALETTE_SYSTEM_PROMPT`` carries a
whole "TOOL DIRECTIVE FORMAT — STRICT" paragraph listing three concrete WRONG
shapes it keeps producing anyway, and why the parser needs an orphan-TOOL_ARGS
recovery path. Every provider we support (Claude / Gemini / ChatGPT / DeepSeek)
can instead be handed a JSON Schema per tool, after which a malformed or
argument-less call is not something the model can emit.

This module is the schema half: one declaration per tool plus the three wire
formats. It is deliberately scoped to the DIAGNOSTIC TOOLS, not the ~43 palette
ACTIONS — tools already have precise argument specs, they are read-only (bar the
two confirm-gated ones), and they are where malformed output actually hurts. The
actions can follow once this path is proven against a live provider; the shape
here (`logic.ai_actions` metadata -> schema) is the same one they will use.

Wire formats differ only in envelope, not content:
  Claude   ``{"name", "description", "input_schema"}``
  OpenAI   ``{"type": "function", "function": {"name", "description", "parameters"}}``
           (DeepSeek is OpenAI-compatible and uses the same shape)
  Gemini   ``[{"functionDeclarations": [{"name", "description", "parameters"}]}]``

NOTE — the wire formats below are written from each provider's documented API
and are covered by offline tests, but they have NOT been exercised against a
live endpoint from this environment (no keys). Treat the first real call per
provider as the validation step; that is why the caller keeps the text-directive
path as a fallback rather than deleting it.
"""
from __future__ import annotations

_STR = "string"
_INT = "integer"


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    """A JSON-Schema object node. ``additionalProperties`` is false so a model
    can't smuggle an unexpected key past the tool's own arg validation."""
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


# One entry per tool in ``logic.ai_extras.PALETTE_TOOL_CATALOGUE``. Descriptions
# are deliberately short — WHEN to reach for a tool is taught at length in
# PALETTE_SYSTEM_PROMPT; this text only has to disambiguate the tools from each
# other at selection time.
TOOL_SCHEMAS: dict[str, dict] = {
    "get_host_detail": {
        "description": (
            "Full record for ONE host: live CPU / memory / per-mount disk, OS and "
            "hardware identity, uptime, interfaces, temperatures, UPS/battery, "
            "pending package updates, detected ports, per-provider state including "
            "why a provider is paused, and recent failure transitions. Use for any "
            "question about a specific named host."),
        "parameters": _obj({
            "host_id": {"type": _STR, "description":
                        "Curated host id, label, or any provider alias."},
        }, ["host_id"]),
    },
    "get_host_metrics_recent": {
        "description": (
            "Recent time-series samples for ONE host and ONE metric — for "
            "'was there a spike at 02:00' / 'has memory been creeping up'."),
        "parameters": _obj({
            "host_id": {"type": _STR, "description": "Curated host id."},
            "metric": {"type": _STR, "description":
                       "Metric column, e.g. cpu_percent / mem_percent / disk_percent.",
                       "default": "cpu_percent"},
            "hours": {"type": _INT, "description": "Look-back window in hours.",
                      "default": 6},
            "limit": {"type": _INT, "description": "Max samples to return.",
                      "default": 200},
        }, ["host_id"]),
    },
    "get_failure_events": {
        "description": (
            "Provider availability transitions per host (paused / resumed / "
            "recovered) — for 'how often does X drop' / 'what's flapping'."),
        "parameters": _obj({
            "host_id": {"type": _STR, "description":
                        "Curated host id. Omit for fleet-wide."},
            "hours": {"type": _INT, "description": "Look-back window.", "default": 24},
            "limit": {"type": _INT, "description": "Max rows.", "default": 50},
        }),
    },
    "get_recent_history": {
        "description": (
            "Recent rows from the operation history table (updates, restarts, "
            "removals, reboots, scheduled runs) with their outcome."),
        "parameters": _obj({
            "target_kind": {"type": _STR, "description":
                            "service / container / orphan / host."},
            "target_id": {"type": _STR, "description": "Specific target id."},
            "op_type": {"type": _STR, "description":
                        "Operation type, e.g. update_stack / host_reboot."},
            "hours": {"type": _INT, "description": "Look-back window.", "default": 24},
            "limit": {"type": _INT, "description": "Max rows.", "default": 50},
        }),
    },
    "get_recent_logs": {
        "description": (
            "Recent persistent-log lines filtered by severity floor and optional "
            "log-tag prefix — for 'what errors have there been'."),
        "parameters": _obj({
            "severity_min": {"type": _STR, "description":
                             "DEBUG / INFO / WARN / ERROR.", "default": "WARN"},
            "tag_prefix": {"type": _STR, "description":
                           "Log tag prefix, e.g. snmp / webmin / ssh."},
            "hours": {"type": _INT, "description": "Look-back window.", "default": 1},
            "line_cap": {"type": _INT, "description": "Max lines.", "default": 100},
        }),
    },
    "get_container_events": {
        "description": (
            "Container state / health transitions from the live gather cache, "
            "optionally filtered by name prefix."),
        "parameters": _obj({
            "name_prefix": {"type": _STR, "description":
                            "Container name prefix, e.g. omnigrid_."},
            "hours": {"type": _INT, "description": "Look-back window.", "default": 1},
        }),
    },
    "upcoming_releases": {
        "description": (
            "Upcoming releases aggregated across every configured Radarr / Sonarr "
            "/ Lidarr / Readarr instance."),
        "parameters": _obj({
            "days": {"type": _INT, "description": "Look-ahead window.", "default": 14},
            "media_type": {"type": _STR, "description":
                           "movie / episode / album / book."},
            "title": {"type": _STR, "description": "Filter by title substring."},
        }),
    },
    "ssh_diag": {
        "description": (
            "Run ONE whitelisted read-only diagnostic command on a host over SSH "
            "(journals, disk usage, failed units, listening ports, agent status). "
            "Touches the host, so the operator confirms before it runs."),
        "parameters": _obj({
            "host_id": {"type": _STR, "description": "Curated host id."},
            "preset": {"type": _STR, "description":
                       "Whitelisted preset name — free-form commands are rejected."},
            "unit": {"type": _STR, "description":
                     "systemd unit, for the generic systemctl_status_unit / "
                     "journalctl_unit_recent presets."},
        }, ["host_id", "preset"]),
    },
    "docker_container_du": {
        "description": (
            "Disk usage of the largest paths INSIDE a named container on a host — "
            "for 'what is filling this container up'. Touches the host, so the "
            "operator confirms before it runs."),
        "parameters": _obj({
            "host_id": {"type": _STR, "description": "Curated host id."},
            "container_name": {"type": _STR, "description": "Container name."},
            "path": {"type": _STR, "description": "Path to measure.", "default": "/"},
            "limit": {"type": _INT, "description": "Top-N paths.", "default": 20},
        }, ["host_id", "container_name"]),
    },
    "find_mac_port": {
        "description": (
            "Which port on a switch has seen a MAC address. Call this FIRST "
            "when asked to bounce the port a device or MAC is on, then bounce "
            "the port it returns \u2014 never guess a port. Returns `interface` "
            "when the address is on exactly one port; when it is on several, "
            "`interfaces` lists them and you must ask which rather than choose, "
            "because one is usually a trunk to another switch. Touches the "
            "switch, so the operator confirms before it runs."),
        "parameters": _obj({
            "host_id": {"type": _STR,
                        "description": "Curated host id of the SWITCH to ask."},
            "mac": {"type": _STR,
                    "description": ("MAC address in any spelling \u2014 colons, "
                                    "dashes, dotted quads or bare hex.")},
        }, ["host_id", "mac"]),
    },
}


def for_claude() -> list[dict]:
    """Anthropic ``tools`` array — schema key is ``input_schema``."""
    return [
        {"name": name, "description": spec["description"],
         "input_schema": spec["parameters"]}
        for name, spec in TOOL_SCHEMAS.items()
    ]


def for_openai() -> list[dict]:
    """OpenAI / DeepSeek ``tools`` array — each wrapped in a ``function`` node."""
    return [
        {"type": "function",
         "function": {"name": name, "description": spec["description"],
                      "parameters": spec["parameters"]}}
        for name, spec in TOOL_SCHEMAS.items()
    ]


# Gemini's function-declaration ``parameters`` is an OpenAPI-3.0 SUBSET, not
# full JSON Schema, and it rejects keys outside that subset with a 400 rather
# than ignoring them. A 400 is NOT in the fallback-retry set (that covers
# transient overload: 429/502/503/504), so a rejected tools block surfaces as a
# hard error on every request — the assistant simply stops answering. Emitting
# only known-accepted keys is the difference between "works" and "the AI is
# down until someone unticks the box".
_GEMINI_SCHEMA_KEYS = frozenset({
    "type", "format", "description", "nullable", "enum",
    "items", "properties", "required",
})


def _gemini_schema(node: dict) -> dict:
    """Recursively reduce a JSON-Schema node to Gemini's accepted subset.

    Dropped constraints aren't lost — ``default`` is folded into the
    description, which is what the model actually reads when choosing
    arguments, so the hint survives in the only form Gemini will accept.
    """
    out: dict = {}
    for key, val in node.items():
        if key not in _GEMINI_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(val, dict):
            out[key] = {k: _gemini_schema(v) if isinstance(v, dict) else v
                        for k, v in val.items()}
        elif key == "items" and isinstance(val, dict):
            out[key] = _gemini_schema(val)
        else:
            out[key] = val
    default = node.get("default")
    if default is not None:
        desc = str(out.get("description") or "").rstrip()
        out["description"] = f"{desc} (default: {default})".strip()
    return out


def for_gemini() -> list[dict]:
    """Gemini ``tools`` array — ONE entry holding every declaration, with each
    schema reduced to the subset Gemini accepts (see :data:`_GEMINI_SCHEMA_KEYS`)."""
    decls = [
        {"name": name, "description": spec["description"],
         "parameters": _gemini_schema(spec["parameters"])}
        for name, spec in TOOL_SCHEMAS.items()
    ]
    return [{"functionDeclarations": decls}]


def tools_for(provider: str) -> list[dict]:
    """Provider-appropriate ``tools`` payload, or ``[]`` when unsupported."""
    p = (provider or "").strip().lower()
    if p == "claude":
        return for_claude()
    if p == "gemini":
        return for_gemini()
    if p in ("chatgpt", "deepseek"):
        return for_openai()
    return []


def native_tools_enabled(provider: str) -> bool:
    """Whether NATIVE tool-calling is switched on for ``provider``.

    Per-provider and default OFF: each provider's wire format differs and none
    have been confirmed against a live endpoint, so the operator turns them on
    one at a time after checking the assistant still answers. While off, the
    existing ``TOOL:`` / ``TOOL_ARGS:`` text path runs unchanged.
    """
    p = (provider or "").strip().lower()
    if not p:
        return False
    try:
        from logic.db import get_setting_bool  # noqa: PLC0415
        from logic.settings_keys import ai_provider_native_tools_key  # noqa: PLC0415
        return bool(get_setting_bool(ai_provider_native_tools_key(p), False))
    except Exception:  # noqa: BLE001
        # Settings unavailable => behave as OFF. Never let a lookup failure
        # switch a provider ONTO an unvalidated code path.
        return False


def active_tools_for(provider: str) -> list[dict]:
    """``tools_for(provider)`` gated on the per-provider opt-in — ``[]`` when
    native tool-calling is off, which is what callers pass to mean "don't send
    a tools block at all"."""
    return tools_for(provider) if native_tools_enabled(provider) else []


def parse_tool_calls(provider: str, payload: dict) -> list[dict]:
    """Extract native tool calls from a provider response.

    Returns ``[{"name": str, "args": dict}, ...]`` — the same shape the
    text-directive parser produces, so the dispatcher is indifferent to which
    path produced them. Returns ``[]`` when the model made no tool call (the
    normal case for a plain question), so a caller can simply fall through to
    the text path. Never raises on a malformed payload — a provider that
    changes shape must degrade to "no tool calls", not break the reply.
    """
    import json  # noqa: PLC0415

    p = (provider or "").strip().lower()
    out: list[dict] = []
    try:
        if p == "claude":
            for block in payload.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    out.append({"name": str(block.get("name") or ""),
                                "args": block.get("input") or {}})
        elif p == "gemini":
            for cand in payload.get("candidates") or []:
                parts = ((cand or {}).get("content") or {}).get("parts") or []
                for part in parts:
                    fc = (part or {}).get("functionCall")
                    if isinstance(fc, dict) and fc.get("name"):
                        out.append({"name": str(fc["name"]),
                                    "args": fc.get("args") or {}})
        else:
            for choice in payload.get("choices") or []:
                msg = (choice or {}).get("message") or {}
                for tc in msg.get("tool_calls") or []:
                    fn = (tc or {}).get("function") or {}
                    if not fn.get("name"):
                        continue
                    raw = fn.get("arguments")
                    # OpenAI sends arguments as a JSON STRING; DeepSeek has been
                    # seen sending an object. Accept either.
                    if isinstance(raw, str):
                        try:
                            args = json.loads(raw) if raw.strip() else {}
                        except (ValueError, json.JSONDecodeError):
                            args = {}
                    else:
                        args = raw or {}
                    out.append({"name": str(fn["name"]),
                                "args": args if isinstance(args, dict) else {}})
    except (AttributeError, TypeError):
        return []
    return [c for c in out if c["name"]]
