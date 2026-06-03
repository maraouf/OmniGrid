"""Continuation of `logic.ai` — extracted to keep that
module under the line-count "uncomfortable to navigate" threshold.
Imported back via `from logic.ai_extras import *` at the bottom
of `logic/ai.py` so every existing `from logic.ai
import X` consumer keeps resolving without changes.
"""
from __future__ import annotations

"""AI integration helpers — Stage 1 foundation.

Stage 1 ships ONLY the per-provider test probe used by the Admin → AI
tab's "Test connection" button. The actual call wrapper that Stage 2+
will use to record into ``ai_jobs`` is NOT in this module yet — it
lands in a follow-up that we'll build once the contract is settled.

Auth model reconnaissance (per the project's provider-checklist rule):

  Claude  — Anthropic API key in `x-api-key` header + ``anthropic-version``
            constant. Default endpoint: https://api.anthropic.com.
  Gemini  — API key in the URL query string (``?key=<key>``) or in
            ``x-goog-api-key`` header (we use the header — keeps the
            URL clean in logs, mirrors the SDK's behaviour). Default
            endpoint: https://generativelanguage.googleapis.com.
  ChatGPT — OpenAI Bearer token in ``Authorization`` header. Default
            endpoint: https://api.openai.com.
  DeepSeek — OpenAI-compatible API; same Bearer-token shape. Default
             endpoint: https://api.deepseek.com.

The test probe sends a one-token "ping" to verify the auth + model id
are valid. We deliberately use ``max_tokens=1`` (or the provider
equivalent) so the test is cheap; a well-formed 200 response is the
success signal regardless of generated content. Any 4xx with auth-
specific detail is surfaced verbatim so admins can fix typos directly
from the toast.
"""

import re as _re
import time
from typing import Optional, TypeVar

# Cyclic-import note: `logic.ai` loads this module from its tail via
# `from logic.ai_extras import *`. By that point `logic.ai`'s body has
# finished defining `compute_cost_usd` + `score_accuracy`, so this
# explicit import resolves correctly via the partially-loaded parent.
# Underscore-prefixed names (`_re`) DON'T propagate via the star-import
# re-export round-trip, so they're imported here directly. Inner
# helpers that need `json` / `asyncio` / `httpx` re-import them inside
# their own function body — keeps the module top clean and matches
# the existing local-import pattern.
from logic.ai import compute_cost_usd, score_accuracy  # noqa: E402


def parse_palette_action_hosts(text: str, known_ids: set[str] | None = None) -> tuple[list[str], str]:
    """Extract the optional `ACTION_HOSTS: <id1>, <id2>, ...` trailer
    from a palette response. Returns ``(host_ids, cleaned_text)``.

    Distinct from :func:`parse_palette_hosts` — that one's HOSTS line
    drives disk-projection chart rendering on the SPA. ACTION_HOSTS
    is the action-target channel: when the AI emits `ACTION:
    scan_ports` paired with `ACTION_HOSTS: opnsense`, the SPA fires
    the scan against `opnsense` WITHOUT rendering disk charts. Pre-
    fix the AI was instructed to overload HOSTS for action-target
    hosts — operators saw a confusing disk-projection chart appear
    when they asked for a port scan.

    Same matcher / tokeniser shape as `parse_palette_hosts` so the
    cap (8 ids) + known_ids filter behave identically.
    """
    if not text:
        return [], text or ""
    m = _re.search(
        r"(?:^|\n)[\s`*]*ACTION_HOSTS\s*:\s*(?P<body>.+?)[\s`.*]*$",
        text, _re.IGNORECASE | _re.MULTILINE,
    )
    if not m:
        return [], text
    raw = m.group("body")
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
    else:
        parts = raw.split()
    cleaned_ids: list[str] = []
    seen: set[str] = set()
    for p in parts:
        token = p.strip().strip("`'\"*.,;").strip()
        if not token:
            continue
        if known_ids is not None and token not in known_ids:
            continue
        if token in seen:
            continue
        seen.add(token)
        cleaned_ids.append(token)
        if len(cleaned_ids) >= 8:
            break
    cleaned_text = text[: m.start()].rstrip()
    return cleaned_ids, cleaned_text


def parse_palette_action_tag(text: str) -> tuple[str, str]:
    """Extract the optional ``ACTION_TAG: <new_tag>`` trailer from a
    palette response. Returns ``(tag, cleaned_text)``.

    Used by the ``retag_image`` action — the AI emits
    ``ACTION: retag_image`` paired with ``ACTION_TAG: 2`` (or
    ``latest`` / ``v2-stable`` / etc.) and the SPA threads the tag
    into the same retag endpoint the drawer's inline popover uses.
    Validates against the Docker tag charset
    (``[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}``); invalid → empty string +
    untouched text so the caller can fall back to the operator-typed
    default. Sibling of ``parse_palette_action_hosts`` — same
    cleanup-text contract so the SPA's downstream renderer doesn't
    surface the directive line as prose.
    """
    if not text:
        return "", text or ""
    m = _re.search(
        r"(?:^|\n)[\s`*]*ACTION_TAG\s*:\s*(?P<body>.+?)[\s`.*]*$",
        text, _re.IGNORECASE | _re.MULTILINE,
    )
    if not m:
        return "", text
    raw = m.group("body").strip().strip("`'\"*.,;").strip()
    cleaned_text = text[: m.start()].rstrip()
    if not raw:
        return "", cleaned_text
    if len(raw) > 128 or not _re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", raw):
        return "", cleaned_text
    return raw, cleaned_text


def parse_palette_action_item(text: str) -> tuple[str, str]:
    """Extract the optional ``ACTION_ITEM: <name-or-id>`` trailer from
    a palette response. Returns ``(item_token, cleaned_text)``.

    Used by the ``retag_image`` action when the operator names a
    specific container/stack in the query and the AI surfaces the
    target explicitly. The SPA resolves the token by exact-match
    against item ids first, then by case-insensitive name match.
    Sibling of ``parse_palette_action_hosts`` but for items (not
    hosts) — keeps the action-target channel separated from the
    HOSTS line that drives disk-projection charts.
    """
    if not text:
        return "", text or ""
    m = _re.search(
        r"(?:^|\n)[\s`*]*ACTION_ITEM\s*:\s*(?P<body>.+?)[\s`.*]*$",
        text, _re.IGNORECASE | _re.MULTILINE,
    )
    if not m:
        return "", text
    raw = m.group("body").strip().strip("`'\"*.,;").strip()
    cleaned_text = text[: m.start()].rstrip()
    return raw, cleaned_text


def parse_palette_action_data(text: str) -> tuple[Optional[dict], str]:
    """Extract the optional ``ACTION_DATA: {<json>}`` trailer from a
    palette response. Returns ``(payload_dict_or_None, cleaned_text)``.

    Used by parameterised actions whose payload is a JSON object
    (currently `schedule_create` / `schedule_update` / `schedule_delete`
    — others may follow). Distinct from `ACTION_TAG` / `ACTION_HOSTS`
    / `ACTION_ITEM` which carry single-value strings; ACTION_DATA is
    the structured-payload channel.

    The matcher accepts JSON delimited by `{` / `}` braces with naive
    brace-balancing — sufficient for one-line payloads the prompt
    teaches the AI to emit. Validates via `json.loads`; invalid JSON
    → returns None + cleans the directive line out of the text so
    the SPA-side renderer doesn't surface the malformed payload as
    prose.
    """
    if not text:
        return None, text or ""
    import json as _json
    m = _re.search(
        r"(?:^|\n)[\s`*]*ACTION_DATA\s*:\s*(?P<body>\{.+?})\s*$",
        text, _re.IGNORECASE | _re.MULTILINE | _re.DOTALL,
    )
    if not m:
        return None, text
    raw = m.group("body").strip()
    cleaned_text = text[: m.start()].rstrip()
    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError:
        return None, cleaned_text
    if not isinstance(data, dict):
        return None, cleaned_text
    return data, cleaned_text


def _infer_tool_from_args(args: dict) -> str:
    """Best-effort inference of the tool name from an orphan TOOL_ARGS
    payload (when the AI emitted args but forgot the leading
    ``TOOL: <name>`` line). Returns the canonical tool name when one
    discriminator key uniquely identifies it; empty string otherwise.

    Discriminator map: each tool's signature carries at least one key
    that no other tool in :data:`PALETTE_TOOL_CATALOGUE` accepts, so
    the args dict alone disambiguates. When multiple discriminators
    fire we return the most-specific match; when none fire we return
    "" and the caller treats the orphan as un-parseable.
    """
    if not isinstance(args, dict):
        return ""
    keys = set(args.keys())
    # Most-specific discriminators first.
    if "preset" in keys:
        return "ssh_diag"
    if "container_name" in keys:
        return "docker_container_du"
    if "metric" in keys:
        return "get_host_metrics_recent"
    if "severity_min" in keys or "tag_prefix" in keys:
        return "get_recent_logs"
    if "name_prefix" in keys:
        return "get_container_events"
    if "op_type" in keys or "target_kind" in keys:
        return "get_recent_history"
    # Last-resort: a bare host_id-only payload matches get_failure_events
    # (the only tool that takes host_id alone as a meaningful query).
    if keys == {"host_id"} or (keys.issubset({"host_id", "hours", "limit"}) and "host_id" in keys):
        return "get_failure_events"
    return ""


def parse_palette_tool_calls(text: str) -> tuple[list[dict], str]:
    """Parse `TOOL: <name>` + `TOOL_ARGS: {<json>}` directive pairs.

    Mirrors the ACTION / ACTION_DATA shape but for diagnostic READ
    tool calls (vs WRITE actions). Each pair lands as a dict
    ``{"name": <str>, "args": <dict>}`` so the dispatcher can iterate
    them in emission order. Strips the matched directive lines from
    the conversational body so the SPA / Telegram renderer doesn't
    surface them.

    Pair-matching rule: a TOOL: line is paired with the IMMEDIATELY-
    FOLLOWING TOOL_ARGS line (skipping blank lines). A TOOL: with no
    matching TOOL_ARGS is paired with an empty ``args={}`` dict so the
    dispatcher can fall back to default args.

    Forgiving path: when the AI emits a `TOOL_ARGS:` line WITHOUT the
    leading `TOOL: <name>` line (a common LLM mis-formatting), we
    infer the tool name from the args' discriminator keys via
    :func:`_infer_tool_from_args`. This keeps the diagnostic flow
    moving instead of dropping the call silently — see
    `_infer_tool_from_args` for the discriminator map.

    Returns ``(tool_calls, cleaned_text)``. Returns an empty list when
    no TOOL directives are present.
    """
    if not text:
        return [], text or ""
    import json as _json
    # Pattern: TOOL: name optionally followed by TOOL_ARGS: {json}.
    # Anchored multi-line so we match per-line and strip surgically.
    # NB: the post-name whitespace class is `[ \t]*` (horizontal-only) —
    # `\s*` would eagerly consume the newline that the optional
    # TOOL_ARGS group needs to anchor, breaking the canonical
    # two-line pairing.
    tool_re = _re.compile(
        r"(?:^|\n)[\s`*]*TOOL\s*:\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*"
        r"(?:\n[\s`*]*TOOL_ARGS\s*:\s*(?P<args>\{.+?}))?",
        _re.IGNORECASE | _re.DOTALL,
    )
    tool_calls: list[dict] = []
    cleaned = text

    def _safe_parse_args_dict(raw: str) -> dict:
        """Parse a JSON-object string into a dict; return {} on any
        failure. Extracted because the same try / isinstance / except
        chain is needed for both the primary `TOOL_ARGS:` pass and the
        orphan-recovery pass below."""
        if not raw:
            return {}
        try:
            parsed = _json.loads(raw)
        except _json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _consume_matches(buf: str, pattern, on_match) -> str:
        """Iterate every regex match in `buf`; for each, call
        `on_match(m, args_dict)` (mutating an outer collection),
        then snip the match site out of `buf` and re-normalize the
        surrounding whitespace. Returns the cleaned buffer after every
        match has been processed. Extracted because the
        `while True / search / break / strip-and-rejoin` shape is
        needed for both the primary `TOOL:` pass and the orphan
        `TOOL_ARGS:` recovery pass — keeping it as one helper avoids
        the two-loop duplication the IDE was flagging. Parameter is
        `buf` (not `text`) to avoid shadowing the outer
        ``parse_palette_tool_calls`` function's `text` argument."""
        while True:
            m = pattern.search(buf)
            if not m:
                break
            args = _safe_parse_args_dict((m.group("args") or "").strip())
            on_match(m, args)
            buf = buf[: m.start()].rstrip() + "\n" + buf[m.end():].lstrip()
            buf = buf.strip() + ("\n" if not buf.endswith("\n") else "")
        return buf

    def _on_tool_match(m, args: dict) -> None:
        name = (m.group("name") or "").strip()
        if name:
            tool_calls.append({"name": name, "args": args})

    def _on_orphan_match(_m, args: dict) -> None:
        inferred = _infer_tool_from_args(args)
        if inferred:
            tool_calls.append({"name": inferred, "args": args})

    # Walk + extract primary TOOL: blocks until no more matches.
    cleaned = _consume_matches(cleaned, tool_re, _on_tool_match)
    # Forgiving second pass: pick up orphan `TOOL_ARGS:` lines whose
    # leading `TOOL:` line the model forgot to emit. Discriminator
    # inference on args keys gives us the tool name; un-inferable
    # orphans are stripped from the prose so they don't leak into the
    # user-visible reply but no call fires.
    orphan_re = _re.compile(
        r"(?:^|\n)[\s`*]*TOOL_ARGS\s*:\s*(?P<args>\{.+?})",
        _re.IGNORECASE | _re.DOTALL,
    )
    cleaned = _consume_matches(cleaned, orphan_re, _on_orphan_match)
    return tool_calls, cleaned.strip()


# Tool catalogue — read-only diagnostic queries the AI can dispatch
# to get richer grounding for "why is X failing" questions without
# making the operator paste shell output. Each tool is a callable
# accepting `args: dict` and `ctx: dict` and returning a dict (the
# `tool_results[<name>]` entry). Adding a tool means one entry here
# + one paragraph in PALETTE_SYSTEM_PROMPT teaching the model when
# to emit `TOOL: <name>`.
def _tool_get_recent_history(args: dict, _ctx: dict) -> dict:
    """Return recent `history` rows filtered by target_kind / target_id
    over the last N hours. Used when the AI needs to answer "how
    many times has X restarted in the last day?" / "what ops have
    fired against this stack?".
    """
    from logic.db import db_conn
    hours = max(1, min(720, int(args.get("hours") or 24)))  # cap 30d
    target_kind = (args.get("target_kind") or "").strip()
    target_id = (args.get("target_id") or "").strip()
    op_type = (args.get("op_type") or "").strip()
    limit = max(1, min(200, int(args.get("limit") or 50)))
    cutoff = int(time.time() - hours * 3600)
    sql = "SELECT ts, op_type, target_kind, target_id, target_name, status, duration, actor FROM history WHERE ts >= ?"
    params: list = [cutoff]
    if target_kind:
        sql += " AND target_kind = ?"
        params.append(target_kind)
    if target_id:
        sql += " AND target_id = ?"
        params.append(target_id)
    if op_type:
        sql += " AND op_type = ?"
        params.append(op_type)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    try:
        with db_conn() as c:
            rows = c.execute(sql, params).fetchall()
    except Exception as e:  # noqa: BLE001
        return {"error": f"history query failed: {e}", "rows": []}
    out = []
    for r in rows:
        out.append({
            "ts": int(r[0] or 0),
            "op_type": r[1] or "",
            "target_kind": r[2] or "",
            "target_id": r[3] or "",
            "target_name": r[4] or "",
            "status": r[5] or "",
            "duration": int(r[6] or 0),
            "actor": r[7] or "",
        })
    return {"rows": out, "count": len(out), "window_hours": hours,
            "filters": {"target_kind": target_kind, "target_id": target_id, "op_type": op_type}}


def _tool_get_recent_logs(args: dict, _ctx: dict) -> dict:
    """Return recent persistent-log entries filtered by severity floor
    + tag prefix. Used when the AI needs to answer "any errors in the
    last hour from <X>?" / "what's in the logs around the incident?".

    Reads through `recent_lines_window` (NOT `recent_lines`) so the
    `hours` arg actually scopes the read to the persistent log files
    on disk rather than the in-memory ring buffer's ~last-N-minutes
    window. `recent_lines` only takes `(levels, limit)`; the
    `_window` variant adds time-range support.
    """
    from logic.logs import recent_lines_window
    hours = max(1, min(168, int(args.get("hours") or 1)))
    line_cap = max(1, min(500, int(args.get("line_cap") or 100)))
    severity_min = (args.get("severity_min") or "WARN").upper()
    tag_prefix = (args.get("tag_prefix") or "").strip()
    severity_order = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
    sev_floor = severity_order.get(severity_min, 2)
    try:
        lines = recent_lines_window(hours=hours, limit=line_cap * 4)  # over-fetch then filter
    except Exception as e:  # noqa: BLE001
        return {"error": f"logs read failed: {e}", "lines": []}
    out = []
    for ln in lines:
        # `recent_lines_window` returns `{ts, level, text}`. Level is
        # lowercase (`error` / `warn` / `info` / `success`) per the
        # in-memory ring buffer schema; uppercase here for parity with
        # the `severity_min` user-facing arg. Tag is embedded in the
        # text as a leading `[tag] ` prefix; extract on the fly for
        # the `tag_prefix` filter so callers don't need to know the
        # storage format.
        sev = (ln.get("level") or "info").upper()
        if severity_order.get(sev, 1) < sev_floor:
            continue
        text = ln.get("text") or ""
        # Extract `[tag]` prefix if present so the filter matches
        # what operators see in Admin → Logs (tag colouring there
        # also keys off the bracketed prefix).
        tag = ""
        if text.startswith("[") and "]" in text:
            tag = text[1:text.index("]")]
        if tag_prefix and not tag.startswith(tag_prefix):
            continue
        out.append({
            "ts": int(ln.get("ts") or 0),
            "severity": sev,
            "tag": tag,
            "message": text[:500],
        })
        if len(out) >= line_cap:
            break
    return {"lines": out, "count": len(out), "window_hours": hours,
            "filters": {"severity_min": severity_min, "tag_prefix": tag_prefix}}


def _tool_get_failure_events(args: dict, _ctx: dict) -> dict:
    """Return rows from `host_failure_events` for a specific host (or
    fleet-wide) over the last N hours. Each row is a state transition
    — provider paused / resumed / recovered. Used when the AI needs
    to answer 'how many times has X gone offline today?' / 'what
    failure events does this host have?'.
    """
    from logic.db import db_conn
    hours = max(1, min(720, int(args.get("hours") or 24)))
    host_id = (args.get("host_id") or "").strip()
    limit = max(1, min(200, int(args.get("limit") or 50)))
    cutoff = int(time.time() - hours * 3600)
    sql = ("SELECT ts, host_id, provider, kind, reason, severity "
           "FROM host_failure_events WHERE ts >= ?")
    params: list = [cutoff]
    if host_id:
        sql += " AND host_id = ?"
        params.append(host_id)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    try:
        with db_conn() as c:
            rows = c.execute(sql, params).fetchall()
    except Exception as e:  # noqa: BLE001
        return {"error": f"failure_events query failed: {e}", "rows": []}
    out = []
    for r in rows:
        out.append({
            "ts": int(r[0] or 0),
            "host_id": r[1] or "",
            "provider": r[2] or "",
            "kind": r[3] or "",
            "reason": (r[4] or "")[:300],
            "severity": r[5] or "",
        })
    return {"rows": out, "count": len(out), "window_hours": hours,
            "filters": {"host_id": host_id}}


def _tool_get_host_metrics_recent(args: dict, _ctx: dict) -> dict:
    """Return recent time-series points from `host_metrics_samples` for
    a specific host + metric. Used for 'show me the cpu spike at
    02:00' / 'has memory been creeping up on this host all week?'
    type questions where a chart isn't sufficient and the AI needs
    raw samples to reason from.
    """
    from logic.db import db_conn
    hours = max(1, min(720, int(args.get("hours") or 6)))
    host_id = (args.get("host_id") or "").strip()
    metric = (args.get("metric") or "cpu_percent").strip()
    limit = max(1, min(500, int(args.get("limit") or 200)))
    if not host_id:
        return {"error": "host_id is required", "samples": []}
    # Whitelist the column name so a malicious metric arg can't
    # smuggle SQL injection into the query.
    allowed_metrics = {
        "cpu_percent", "mem_percent", "disk_percent",
        "load_1m", "load_5m", "load_15m",
        "host_uptime_s", "host_swap_percent",
    }
    if metric not in allowed_metrics:
        return {"error": f"metric '{metric}' not in whitelist — valid: "
                         + ", ".join(sorted(allowed_metrics)), "samples": []}
    cutoff = int(time.time() - hours * 3600)
    sql = (f"SELECT ts, {metric} FROM host_metrics_samples "
           f"WHERE host_id = ? AND ts >= ? ORDER BY ts DESC LIMIT ?")
    try:
        with db_conn() as c:
            rows = c.execute(sql, (host_id, cutoff, limit)).fetchall()
    except Exception as e:  # noqa: BLE001
        return {"error": f"host_metrics query failed: {e}", "samples": []}
    samples = [{"ts": int(r[0] or 0), "value": r[1]} for r in rows]
    return {"samples": samples, "count": len(samples),
            "host_id": host_id, "metric": metric, "window_hours": hours}


def _tool_get_container_events(args: dict, _ctx: dict) -> dict:
    """Return container state / health transitions from the gather
    cache for items whose name starts with `name_prefix`. Used for
    'is X still restarting?' / 'when did this container go unhealthy'
    questions where the most recent gather snapshot tells the story.
    """
    hours = max(1, min(168, int(args.get("hours") or 1)))
    name_prefix = (args.get("name_prefix") or "").strip().lower()
    try:
        from logic import gather as _gather
        # noinspection PyProtectedMember
        cache = _gather._cache or {}
    except Exception as e:  # noqa: BLE001
        return {"error": f"gather cache unavailable: {e}", "items": []}
    items = cache.get("items") or []
    cutoff = int(time.time() - hours * 3600)
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").lower()
        if name_prefix and not name.startswith(name_prefix):
            continue
        # `last_state_change_ts` may not exist on every gather schema
        # version; fall back to `last_started_at` or skip.
        ts = it.get("last_state_change_ts") or it.get("last_started_at") or 0
        if ts and int(ts) < cutoff:
            continue
        out.append({
            "name": it.get("name") or "",
            "type": it.get("type") or "",
            "stack": it.get("stack") or "",
            "status": it.get("status") or "",
            "health": it.get("health") or "",
            "state": it.get("state") or "",
            "replicas": it.get("replicas"),
            "desired": it.get("desired"),
            "last_state_change_ts": int(ts) if ts else 0,
            "error": (it.get("error") or "")[:200],
        })
    # Sort newest-state-change first so the AI sees the most recent
    # transitions at the top of the list.
    out.sort(key=lambda r: r.get("last_state_change_ts") or 0, reverse=True)
    return {"items": out[:100], "count": min(len(out), 100),
            "window_hours": hours, "filters": {"name_prefix": name_prefix}}


# SSH-gated diagnostic preset whitelist. Operator-readable command
# names map to actual one-liners; the AI MAY emit `TOOL: ssh_diag`
# with `args.preset` only — never a free-form `command` string. This
# keeps the attack surface bounded (a confused or malicious model
# can't run arbitrary commands). Per the AI palette / SSH gate
# convention, ssh_diag dispatch ALSO routes through the inline-confirm
# chip in the SPA AI sidebar BEFORE the SSH session opens — so even a
# whitelisted preset doesn't fire silently.
SSH_DIAG_PRESETS: dict[str, str] = {
    "journalctl_docker_last_hour": "journalctl -u docker.service --since '1 hour ago' --no-pager | tail -200",
    "journalctl_containerd_last_hour": "journalctl -u containerd.service --since '1 hour ago' --no-pager | tail -200",
    "ps_failed": "docker ps -a --filter 'status=exited' --filter 'status=dead' --format 'table {{.Names}}\\t{{.Image}}\\t{{.Status}}\\t{{.Command}}' | head -50",
    "df_h": "df -h | head -30",
    "dmesg_tail": "dmesg --time-format=iso 2>/dev/null | tail -100 || dmesg | tail -100",
    "uptime": "uptime",
    # Disk-usage diagnostics — operators ask "why is X growing?" /
    # "which container is eating disk?" routinely. These let the AI
    # gather the actual data instead of dispensing shell commands.
    "docker_system_df": "docker system df -v 2>/dev/null | head -200 || sudo docker system df -v | head -200",
    "docker_ps_with_sizes": "docker ps -a -s --format 'table {{.Names}}\\t{{.Size}}\\t{{.Status}}\\t{{.Image}}' 2>/dev/null | head -80 || sudo docker ps -a -s --format 'table {{.Names}}\\t{{.Size}}\\t{{.Status}}\\t{{.Image}}' | head -80",
    "du_root_top": "du -sh /var/lib/docker /var/log /home /opt /tmp 2>/dev/null | sort -rh | head -20",
    # Systemd-agent diagnostics — when a host-stats provider chip
    # goes red (Beszel / node_exporter / Webmin) the agent is OFTEN
    # a native systemd service on the target box rather than a
    # container. These presets let the AI autonomously inspect the
    # service state + tail the journal + check listening ports
    # instead of telling the operator to SSH and run the commands
    # manually. Each preset tolerates the agent NOT being installed
    # (the systemctl / journalctl exit non-zero → captured into
    # stderr, AI reads the error and reports "service not present").
    "systemctl_status_beszel": "systemctl status beszel-agent --no-pager 2>&1 | head -60 || systemctl status beszel --no-pager 2>&1 | head -60",
    "journalctl_beszel_recent": "journalctl -u beszel-agent --since '1 hour ago' --no-pager 2>&1 | tail -200 || journalctl -u beszel --since '1 hour ago' --no-pager 2>&1 | tail -200",
    "systemctl_status_node_exporter": "systemctl status node_exporter --no-pager 2>&1 | head -60 || systemctl status prometheus-node-exporter --no-pager 2>&1 | head -60",
    "journalctl_node_exporter_recent": "journalctl -u node_exporter --since '1 hour ago' --no-pager 2>&1 | tail -200 || journalctl -u prometheus-node-exporter --since '1 hour ago' --no-pager 2>&1 | tail -200",
    "systemctl_status_webmin": "systemctl status webmin --no-pager 2>&1 | head -60",
    "journalctl_webmin_recent": "journalctl -u webmin --since '1 hour ago' --no-pager 2>&1 | tail -200",
    # Generic-but-bounded systemd queries — when the AI knows a unit
    # name from prior context (e.g. operator typed it, or a tool
    # result revealed it). The unit name is whitelisted at dispatch
    # time via the `unit` arg + regex `^[A-Za-z0-9._@-]+$` so a
    # confused model can't smuggle shell metachars.
    "systemctl_status_unit": "systemctl status {unit} --no-pager 2>&1 | head -60",
    "journalctl_unit_recent": "journalctl -u {unit} --since '1 hour ago' --no-pager 2>&1 | tail -200",
    # Network / service-discovery — answers "is the agent listening
    # on the expected port?" without operator intervention. The two
    # variants cover hosts where ss requires sudo vs. where it
    # doesn't; AI tries the unprivileged form first then escalates.
    "listening_ports": "ss -tlnp 2>/dev/null | head -80 || sudo ss -tlnp 2>&1 | head -80 || netstat -tlnp 2>/dev/null | head -80",
    # Failed-services sweep — useful BEFORE drilling into a specific
    # unit, because it surfaces every systemd unit currently in the
    # `failed` state, not just the one the operator named.
    "failed_services": "systemctl list-units --type=service --state=failed --no-pager 2>&1 | head -40",
    # Per-unit override directory — when an operator says "I set
    # NICS=eth0 on beszel-agent and it's still not picking it up"
    # this preset shows what override.conf actually contains on
    # disk + what env vars the unit ended up resolved with. Pairs
    # well with `systemctl_status_beszel` and the AI's mental
    # model of "operator said X, did X actually land?"
    "beszel_agent_env": "echo '=== drop-in dir ===' && ls -la /etc/systemd/system/beszel-agent.service.d/ 2>&1 || echo '(no drop-ins)'; echo '=== drop-in contents ===' && cat /etc/systemd/system/beszel-agent.service.d/*.conf 2>&1 || true; echo '=== resolved Environment ===' && systemctl show beszel-agent -p Environment 2>&1 || true",
}


async def _tool_docker_container_du(args: dict, _ctx: dict) -> dict:
    """Run ``du -ah <path> | sort -rh | head -<n>`` inside a named
    container on the target host. Used by the AI palette to identify
    what's eating disk inside a specific container (the most-common
    answer to "why is X growing?"). Parametric (unlike the fixed
    presets in :data:`SSH_DIAG_PRESETS`) because the container name +
    path vary per question.

    Defensive shape: the container name is shell-escaped via
    :func:`shlex.quote` before composition, the path is restricted to
    safe characters (``[A-Za-z0-9/_.-]``) so a confused / malicious
    model can't smuggle shell metacharacters, and the ``du`` runs
    inside the container so the path resolves against the container's
    own filesystem (where the bloat usually is).
    """
    import shlex as _shlex
    from logic import ssh as _ssh
    host_id = (args.get("host_id") or "").strip()
    container = (args.get("container_name") or "").strip()
    path = (args.get("path") or "/").strip() or "/"
    try:
        n = max(5, min(int(args.get("limit") or 20), 50))
    except (TypeError, ValueError):
        n = 20
    if not host_id:
        return {"error": "host_id is required"}
    if not container:
        return {"error": "container_name is required"}
    if not _re.fullmatch(r"[A-Za-z0-9/_.-]+", path):
        return {"error": f"path must match [A-Za-z0-9/_.-]+ (got {path!r})"}
    if not _re.fullmatch(r"[A-Za-z0-9_.-]+", container):
        return {"error": f"container_name must match [A-Za-z0-9_.-]+ (got {container!r})"}
    cmd = (
        f"docker exec {_shlex.quote(container)} sh -c "
        f"\"du -ah {path} 2>/dev/null | sort -rh | head -n {n}\" "
        f"2>/dev/null || sudo docker exec {_shlex.quote(container)} sh -c "
        f"\"du -ah {path} 2>/dev/null | sort -rh | head -n {n}\""
    )
    try:
        from logic.db import get_setting
        from logic.settings_keys import Settings
        import json as _json_ssh
        try:
            hosts_cfg_raw = _json_ssh.loads(get_setting(Settings.HOSTS_CONFIG) or "[]")
        except (TypeError, ValueError):
            hosts_cfg_raw = []
        result = await _ssh.run_command(
            host_id=host_id,
            command=cmd,
            hosts_config=hosts_cfg_raw if isinstance(hosts_cfg_raw, list) else [],
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"docker_container_du failed: {type(e).__name__}: {e}"}
    if not isinstance(result, dict):
        return {"error": "docker_container_du returned non-dict result"}
    return {
        "host_id": host_id,
        "container_name": container,
        "path": path,
        "limit": n,
        "exit_code": result.get("exit_code"),
        "stdout": (result.get("stdout") or "")[:4000],
        "stderr": (result.get("stderr") or "")[:1000],
        "error": result.get("error") or "",
        "duration_ms": result.get("duration_ms"),
    }


async def _tool_ssh_diag(args: dict, _ctx: dict) -> dict:
    """Run a WHITELISTED read-only diagnostic command via SSH on the
    target host. Distinct from the read-only DB tools because it
    touches the actual machine — per the SSH gate convention the
    SPA-side dispatcher ALSO routes this through the inline-confirm
    chip BEFORE the call lands. The backend re-checks the preset is
    whitelisted (defence-in-depth) and reads SSH credentials via the
    existing `logic.ssh.resolve_ssh_params` chain. Returns the
    command's stdout (capped) + exit code; never raises.
    """
    from logic import ssh as _ssh
    import re as _re_unit
    host_id = (args.get("host_id") or "").strip()
    preset = (args.get("preset") or "").strip()
    if not host_id:
        return {"error": "host_id is required"}
    cmd = SSH_DIAG_PRESETS.get(preset)
    if cmd is None:
        return {"error": f"preset '{preset}' not in whitelist — valid: "
                         + ", ".join(sorted(SSH_DIAG_PRESETS.keys()))}
    # Parameterised presets — substitute the `{unit}` placeholder
    # from a strictly-validated `unit` arg. The regex blocks every
    # shell-metachar so a confused / hostile model can't smuggle
    # arbitrary commands via the unit name. Reject the call when
    # the preset contains a `{unit}` placeholder but no valid arg.
    if "{unit}" in cmd:
        unit = (args.get("unit") or "").strip()
        if not unit:
            return {"error": f"preset '{preset}' requires a `unit` arg (the systemd unit name)"}
        if not _re_unit.match(r"^[A-Za-z0-9._@-]+$", unit):
            return {"error": f"unit '{unit}' contains disallowed characters; only A-Za-z0-9._@- accepted"}
        # Cap length defence-in-depth — systemd unit names rarely
        # exceed ~80 chars; anything past that is a smell.
        if len(unit) > 128:
            return {"error": "unit name too long (>128 chars)"}
        cmd = cmd.replace("{unit}", unit)
    try:
        from logic.db import get_setting
        from logic.settings_keys import Settings
        import json as _json_ssh
        try:
            hosts_cfg_raw = _json_ssh.loads(get_setting(Settings.HOSTS_CONFIG) or "[]")
        except (TypeError, ValueError):
            hosts_cfg_raw = []
        # NB: `run_command`'s signature is (host_id, command, hosts_config,
        # timeout=30, dry_run=False) — no actor kwarg. Actor capture for
        # this call lands in the `ai_tool_call` audit row written by
        # `dispatch_palette_tool` below, NOT in the `ssh_run` row
        # `run_command` writes itself. Two rows, two perspectives.
        result = await _ssh.run_command(
            host_id=host_id,
            command=cmd,
            hosts_config=hosts_cfg_raw if isinstance(hosts_cfg_raw, list) else [],
            timeout=20.0,
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"ssh_diag failed: {type(e).__name__}: {e}"}
    if not isinstance(result, dict):
        return {"error": "ssh_diag returned non-dict result"}
    return {
        "preset": preset,
        "host_id": host_id,
        "exit_code": result.get("exit_code"),
        "stdout": (result.get("stdout") or "")[:4000],
        "stderr": (result.get("stderr") or "")[:1000],
        "error": result.get("error") or "",
        "duration_ms": result.get("duration_ms"),
    }


PALETTE_TOOL_CATALOGUE: dict = {
    "get_recent_history": _tool_get_recent_history,
    "get_recent_logs": _tool_get_recent_logs,
    "get_failure_events": _tool_get_failure_events,
    "get_host_metrics_recent": _tool_get_host_metrics_recent,
    "get_container_events": _tool_get_container_events,
    "ssh_diag": _tool_ssh_diag,
    "docker_container_du": _tool_docker_container_du,
}

# Tools whose dispatch is DESTRUCTIVE-adjacent — they touch the
# target host (even for reads) and so should route through the
# SPA's inline-confirm chip BEFORE the backend fires them. The
# orchestrator skips these on the SPA fast-path; the SPA must
# emit them via the same confirm flow used for ACTION-class
# destructive ops.
PALETTE_TOOLS_REQUIRING_CONFIRM: frozenset[str] = frozenset({"ssh_diag", "docker_container_du"})


async def dispatch_palette_tool(call: dict, ctx: Optional[dict] = None) -> dict:
    """Dispatch one tool call from `parse_palette_tool_calls`. Returns
    the tool's result dict OR an error envelope if the tool name isn't
    in the catalogue / the call raises. Never raises itself — every
    failure path returns a dict so the orchestrator's batch can keep
    other tool calls intact.

    Tools may be sync or async — the dispatcher awaits if the handler
    returns a coroutine. Confirm-required tools (currently `ssh_diag`)
    short-circuit here with an envelope the SPA dispatcher reads to
    surface the inline-confirm chip; backend never fires the tool
    without that confirm round-trip.

    Side-effect: writes a `history` audit row under
    ``op_type='ai_tool_call'`` so the AI's diagnostic queries are
    traceable in Admin → History. Best-effort — a history write
    failure doesn't drop the tool result.
    """
    import asyncio as _asyncio
    name = (call or {}).get("name") or ""
    args = (call or {}).get("args") or {}
    handler = PALETTE_TOOL_CATALOGUE.get(name)
    if handler is None:
        return {"error": f"unknown tool '{name}' — valid: " + ", ".join(sorted(PALETTE_TOOL_CATALOGUE.keys()))}
    # Confirm-gate the tools that touch a host (SSH-class). Returns
    # an envelope flag the SPA orchestrator reads to surface the
    # inline-confirm chip without firing the backend tool. The
    # second-round AI re-invocation runs AFTER the operator confirms.
    if name in PALETTE_TOOLS_REQUIRING_CONFIRM and not (ctx or {}).get("_tool_confirm_granted"):
        return {
            "_pending_confirm": True,
            "tool": name,
            "args": args,
            "reason": (f"{name} touches a target host (even for reads) "
                       f"— operator must confirm via the inline chip "
                       f"in the AI sidebar before this fires."),
        }
    try:
        result = handler(args, ctx or {})
        if _asyncio.iscoroutine(result):
            result = await result
        if not isinstance(result, dict):
            result = {"value": result}
    except Exception as e:  # noqa: BLE001
        result = {"error": f"{type(e).__name__}: {e}"}
    # Audit-row write. Forensic anchor for "the AI ran a query on
    # the operator's behalf at <ts>". target_kind = tool name,
    # target_id = primary scope arg (host_id / target_id / preset),
    # status = ok / error based on whether the tool's result carries
    # an `error` key. Best-effort — a history write failure doesn't
    # drop the tool result.
    try:
        from logic.db import db_conn
        from logic.ops import assert_op_type as _assert_op_type, write_admin_audit as _write_admin_audit
        _assert_op_type("ai_tool_call")
        target_id = (args.get("host_id") or args.get("target_id")
                     or args.get("preset") or "")
        status = "ok" if not (isinstance(result, dict) and result.get("error")) else "error"
        actor = (ctx.get("actor") if isinstance(ctx, dict) else None) or "ai_palette"
        with db_conn() as c:
            _write_admin_audit(
                c, "ai_tool_call",
                target_kind=name, target_name=str(target_id)[:128],
                actor=actor,
                message=f"AI dispatched {name}({str(args)[:200]}) → {status}",
            )
    except Exception as _audit_err:  # noqa: BLE001
        print(f"[ai] ai_tool_call audit-row write failed: {_audit_err}")
    return result


def parse_host_filter_response(text: str) -> tuple[str, str, str]:
    """Parse the host-filter model response. Returns
    ``(dsl, explanation, error)`` — empty `dsl` means the response
    was invalid; `error` carries a one-line reason for the SPA toast.

    Validates against the Phase 1 grammar (`pause:` / `resume:`).
    Strips markdown fences the model might add despite instructions
    to the contrary.
    """
    if not text:
        return "", "", "Model returned an empty response."
    cleaned = text.strip().strip("`").strip()
    if cleaned.lower().startswith("error:"):
        msg = cleaned[6:].strip() or "AI couldn't translate that into a Phase 1 DSL filter."
        return "", "", msg
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if not lines:
        return "", "", "Model returned an empty response."
    cand = lines[0]
    m = _re.match(r"^(?P<verb>pause|resume)\s*:\s*(?P<scope>.*)$", cand, _re.IGNORECASE)
    if not m:
        return "", "", f"Model didn't return a valid DSL line — got: {cand[:120]}"
    dsl = f"{m.group('verb').lower()}: {m.group('scope').strip()}".rstrip()
    explanation = lines[1] if len(lines) > 1 else ""
    return dsl, explanation, ""


def parse_palette_memories(text: str) -> tuple[list[str], list[str], str]:
    """Parse trailing ``MEMORY: ...`` and ``MEMORY-FORGET: ...`` lines
    off the assistant reply. Returns ``(memories_to_save, memories_to_forget,
    cleaned_text)``. Each list element is the raw single-line text the AI
    emitted (one memory per line). Lines that pass through with the
    `MEMORY:` / `MEMORY-FORGET:` prefix are stripped from the visible
    reply; everything else is preserved verbatim.

    Defensive: caps memory body at 500 chars to discourage prose-bloat
    from a chatty model. Empty bodies after the colon are ignored.
    """
    if not text:
        return [], [], text or ""
    saves: list[str] = []
    forgets: list[str] = []
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        # Match `MEMORY-FORGET:` BEFORE `MEMORY:` (longest-prefix wins).
        upper = stripped.upper()
        if upper.startswith("MEMORY-FORGET:"):
            body = stripped.split(":", 1)[1].strip()
            if body:
                forgets.append(body[:500])
            continue
        if upper.startswith("MEMORY:"):
            body = stripped.split(":", 1)[1].strip()
            if body:
                saves.append(body[:500])
            continue
        out_lines.append(line)
    cleaned = "\n".join(out_lines).rstrip()
    return saves, forgets, cleaned


def _format_records_block(label: str, fields: str, records: list) -> str:
    """Helper — turn a list of host/item records into a JSON-lines
    block prefixed with a one-line schema description. Falls back to
    the legacy bare-string CSV when the SPA sends an older payload
    (every entry is a string)."""
    if not records:
        return ""
    if all(isinstance(r, str) for r in records):
        return f"{label}: " + ", ".join(records)
    import json as _json
    body = "\n".join(_json.dumps(r, separators=(",", ":")) for r in records)
    return f"{label} (one JSON record per line, fields: {fields}):\n{body}"


_T_FIELD = TypeVar("_T_FIELD")


def _typed_field(src, key: str, expected_type: type[_T_FIELD]) -> _T_FIELD | None:
    """Return ``src[key]`` when ``src`` is a dict AND the value is an
    instance of ``expected_type``; otherwise None. Used in place of the
    inline ``d.get(k) if isinstance(d.get(k), T) else None`` pattern so
    the type-checker narrows cleanly at every consumer site (the inline
    ternary version returns Any | None which then poisons every
    downstream `.get()` / subscript call with "member None doesn't
    have attribute" diagnostics).

    Generic ``_T_FIELD`` bound to ``expected_type`` so callers get a
    properly-narrowed ``dict | None`` / ``list | None`` at the call
    site without inlining the isinstance dance."""
    if not isinstance(src, dict):
        return None
    v = src.get(key)
    return v if isinstance(v, expected_type) else None


def build_palette_user_prompt(query: str, ctx: dict | None,
                              conversation: list | None = None) -> str:
    """Per-call user prompt for `/api/ai/palette`. Caps host + item
    lists at 30 each (~3k tokens for a fully-populated 30-host fleet).

    ``conversation`` carries prior turns of the multi-turn AI sidebar
    session as ``[{role: "user"|"assistant", text: "..."}]`` pairs.
    Capped at the last 12 turns server-side to keep token budget
    reasonable on long chats; the SPA also caps client-side. Each
    turn is rendered as a `User:` / `Assistant:` line so the model
    sees the chat history before the new query.
    """
    parts: list[str] = []
    if isinstance(conversation, list) and conversation:
        history_lines: list[str] = []
        for turn in conversation[-12:]:
            if not isinstance(turn, dict):
                continue
            role = (turn.get("role") or "").strip().lower()
            text = (turn.get("text") or "").strip()
            if not text or role not in ("user", "assistant"):
                continue
            label = "User" if role == "user" else "Assistant"
            # Cap each prior turn at 600 chars so an old long
            # response doesn't dominate the prompt.
            if len(text) > 600:
                text = text[:600] + "…"
            history_lines.append(f"{label}: {text}")
        if history_lines:
            parts.append("Prior conversation:\n" + "\n".join(history_lines))
    parts.append(f"Operator query: {query}")
    if isinstance(ctx, dict):
        view = ctx.get("view")
        if view:
            parts.append(f"Current view: {view}")
        # Tool results — populated on the SECOND ROUND of a tool-using
        # conversation. The first-round reply emitted `TOOL: <name>`
        # directives; the backend dispatched them + re-invoked the AI
        # with this block populated. Each result is a dict (chronological
        # rows / log lines / metric points etc.). The model is
        # instructed in `PALETTE_SYSTEM_PROMPT` to compose a real
        # diagnostic answer FROM these results, not fabricate values.
        # JSON-encoded for compact serialisation; cap at 6000 chars
        # per tool so a noisy log fetch doesn't blow the context
        # window.
        tool_results = ctx.get("tool_results")
        if isinstance(tool_results, dict) and tool_results:
            import json as _json_tr
            tr_lines = ["Tool results (compose your answer from THESE — don't fabricate values):"]
            for tname, tresult in tool_results.items():
                try:
                    encoded = _json_tr.dumps(tresult, default=str)
                except (TypeError, ValueError):
                    encoded = str(tresult)
                if len(encoded) > 6000:
                    encoded = encoded[:6000] + "…(truncated for token budget)"
                tr_lines.append(f"  - {tname}: {encoded}")
            parts.append("\n".join(tr_lines))
        # Current time block. Threaded by the Telegram listener (per
        # `_build_telegram_ai_context`) so the AI can answer
        # "what time is it" / "what's today's date" without falling
        # back to its training-cutoff guess. Same opt-in shape as
        # weather: when absent the model should say "I don't see a
        # current-time block from this surface" rather than guessing.
        # Fields: utc_iso / local_iso / timezone / utc_offset / weekday.
        tinfo = _typed_field(ctx, "time", dict)
        if tinfo and (tinfo.get("local_iso") or tinfo.get("utc_iso")):
            bits = []
            if tinfo.get("weekday"):
                bits.append(str(tinfo["weekday"]))
            if tinfo.get("local_iso"):
                bits.append(str(tinfo["local_iso"]))
            tz_seg = ""
            if tinfo.get("timezone"):
                tz_seg = f" ({tinfo['timezone']}"
                if tinfo.get("utc_offset"):
                    tz_seg += f" UTC{tinfo['utc_offset']}"
                tz_seg += ")"
            line = " · ".join(bits) + tz_seg
            utc_seg = ""
            if tinfo.get("utc_iso") and tinfo.get("local_iso") \
                and tinfo["utc_iso"] != tinfo["local_iso"]:
                utc_seg = f" / UTC {tinfo['utc_iso']}"
            parts.append(
                "Current time (server clock — answer naturally using this, do NOT refuse "
                "'I don't have a real-time clock'): " + line + utc_seg
            )
        # Authoritative fleet counts — both callers (SPA palette +
        # Telegram listener) thread these so the AI doesn't answer
        # "how many hosts" with the sample-block size. Operator-flagged:
        # 183 hosts configured, AI replied "30" because that's all it
        # could see in the sample. Emit BEFORE the sample block so the
        # model reads the count before the records.
        hosts_total = ctx.get("hosts_total")
        hosts_enabled = ctx.get("hosts_enabled")
        hosts_sample_cap = ctx.get("hosts_sample_cap") or 30
        hosts_summary = _typed_field(ctx, "hosts_summary", dict) or {}
        # Status-breakdown grounding — Telegram-context-builder emits
        # `hosts_summary.up / .paused / .unknown` so the AI can answer
        # "how many hosts are up" / "any down hosts?" from authoritative
        # counts instead of extrapolating from the truncated sample.
        # Operator-flagged: pre-fix the AI replied "all 182 hosts are in
        # an unknown state" because the 30-row sample happened to be
        # entirely 'unknown' (snapshot table sparse) — even though the
        # actual fleet had most hosts up. With this block the AI sees
        # the real up/paused/unknown counts before reading the sample.
        up_count = hosts_summary.get("up")
        down_count = hosts_summary.get("down")
        paused_count = hosts_summary.get("paused")
        unconfigured_count = hosts_summary.get("unconfigured")
        unknown_count = hosts_summary.get("unknown")
        loading_count = hosts_summary.get("loading")
        if isinstance(hosts_total, int) and hosts_total > 0:
            enabled_seg = (f" ({hosts_enabled} enabled)"
                           if isinstance(hosts_enabled, int) else "")
            # Status-breakdown block. The Telegram context-builder now
            # routes through `api_hosts_list` so the FULL canonical
            # status taxonomy lands in `hosts_summary`: up / down /
            # paused / unconfigured / unknown / loading. Pre-fix only
            # up/paused/unknown were emitted and `unconfigured` rows
            # (operator-curated inventory entries with no telemetry
            # mapped on purpose — FTTH routers, dumb PDUs etc.) were
            # reported as "unknown", which read as "monitoring broken"
            # instead of "monitoring not configured for this row".
            status_lines: list[str] = []
            if isinstance(up_count, int):
                status_lines.append(
                    f"  - hosts_up: {up_count} (reporting live telemetry "
                    f"OR recently snapshotted)"
                )
            if isinstance(down_count, int) and down_count > 0:
                status_lines.append(
                    f"  - hosts_down: {down_count} (provider reports "
                    f"unreachable — Beszel/Pulse says paused-down OR "
                    f"ping says no echo)"
                )
            if isinstance(paused_count, int):
                status_lines.append(
                    f"  - hosts_paused: {paused_count} (sampling "
                    f"explicitly paused by operator / auto-pause)"
                )
            if isinstance(unconfigured_count, int) and unconfigured_count > 0:
                status_lines.append(
                    f"  - hosts_unconfigured: {unconfigured_count} "
                    f"(curated row has NO provider mapped — operator "
                    f"chose to list this host without telemetry; NOT "
                    f"an outage, just an inventory-only entry)"
                )
            if isinstance(unknown_count, int):
                status_lines.append(
                    f"  - hosts_unknown: {unknown_count} (providers ARE "
                    f"mapped but none answered AND no snapshot exists "
                    f"— REAL outage signal OR host never probed since "
                    f"boot)"
                )
            if isinstance(loading_count, int) and loading_count > 0:
                status_lines.append(
                    f"  - hosts_loading: {loading_count} (transient "
                    f"SPA-only state — should be 0 from the backend)"
                )
            status_seg = ("\n" + "\n".join(status_lines)) if status_lines else ""
            parts.append(
                f"Fleet counts (AUTHORITATIVE — use these to answer 'how many "
                f"hosts' / 'count' / 'total' / 'up' / 'down' / 'unknown' / "
                f"'paused' / 'unconfigured' questions, NOT the sample-records "
                f"block below):\n"
                f"  - hosts_total: {hosts_total}{enabled_seg}"
                f"{status_seg}\n"
                f"  - hosts shown below: capped at top {hosts_sample_cap} for "
                f"prompt-token budget; the rest exist but aren't enumerated. "
                f"NEVER answer 'we have N hosts' / 'all N are unknown' where "
                f"N is the visible-sample size — always cite the matching "
                f"counter above. CRITICAL: `unconfigured` hosts are NOT "
                f"a problem — they're operator-curated inventory entries "
                f"with no telemetry mapped on purpose (network gear / dumb "
                f"PDUs / FTTH routers). Only `unknown` and `down` are "
                f"actual outage signals; `unconfigured` is intentional. If "
                f"hosts_up + hosts_down + hosts_paused > 0 the fleet IS "
                f"reporting telemetry; a sample dominated by 'unconfigured' "
                f"or 'unknown' rows is a sampling-order artefact, NOT a "
                f"fleet-wide outage."
            )
        hosts = _typed_field(ctx, "hosts", list)
        if hosts:
            parts.append(_format_records_block(
                "Available hosts (sample — top "
                + str(hosts_sample_cap) + " of " + str(hosts_total or len(hosts))
                + " total)",
                "id, label, status, cpu_pct, mem_pct, disk_pct, "
                "disk_free_gb, disk_total_gb, uptime_s, paused, providers, "
                "plus per-host telemetry WHEN PRESENT: ups_status, "
                "battery_pct, battery_status, battery_runtime_s, "
                "battery_temp_c, load_pct, model, serial, firmware, vendor, "
                "package_updates",
                hosts[:hosts_sample_cap],
            ))
        # Problem-hosts block — the FULL list of every host whose status
        # is anything other than `up` (down / unknown / paused), capped
        # at 200. Telegram context-builder emits this so the AI can
        # NAME the affected hosts when asked "list the unknown hosts" /
        # "which hosts are paused?" / "show me the down ones". Pre-fix
        # the AI saw the count in `hosts_summary` but couldn't identify
        # any of the hosts by ID because they sat past the
        # sample_cap=60 on a fleet with many `up` rows ahead of them.
        # With this block the AI has the IDs + labels + per-provider
        # aliases for every problem host, regardless of sample bias.
        problem_hosts = _typed_field(ctx, "problem_hosts", list)
        if problem_hosts:
            parts.append(_format_records_block(
                "Problem hosts (FULL list — every host with status != "
                "'up'; cap 200). USE THIS when the operator asks "
                "'which hosts are down/unknown/paused?', 'list the "
                "problem hosts', 'name the unknowns' — emit a short "
                "bullet list of `id (status)` for the relevant subset. "
                "DO NOT say 'there are N unknown hosts' without naming "
                "them when this block is present.",
                "id, label, status, address, paused, beszel_name, "
                "pulse_name, webmin_name, snmp_name",
                problem_hosts[:200],
            ))
        # Items counts — mirror the hosts_total pattern so the model
        # can answer "how many stacks need updating" / "any updates?"
        # accurately even when the sample is truncated. Telegram
        # context-builder emits `items_summary` (`total` /
        # `updatable_total` / `running_total`) alongside the partitioned
        # `updatable_items` + `other_items` lists; SPA context-builder
        # emits `items_total` / `items_sample_cap` directly. Accept
        # either shape so neither caller has to massage payloads.
        items_summary = _typed_field(ctx, "items_summary", dict) or {}
        items_total = (
            ctx.get("items_total")
            or items_summary.get("total")
        )
        items_updatable_total = items_summary.get("updatable_total")
        items_running_total = items_summary.get("running_total")
        items_sample_cap = ctx.get("items_sample_cap") or 60
        if isinstance(items_total, int) and items_total > 0:
            extra_seg = ""
            if isinstance(items_updatable_total, int):
                extra_seg += f", updatable_total: {items_updatable_total}"
            if isinstance(items_running_total, int):
                extra_seg += f", running_total: {items_running_total}"
            parts.append(
                f"Items counts (AUTHORITATIVE — use these to answer "
                f"'how many items' / 'any pending updates' / 'how many "
                f"running' questions, NOT the sample-records block "
                f"below):\n"
                f"  - items_total: {items_total}{extra_seg}\n"
                f"  - items shown below: capped at top {items_sample_cap} for "
                f"prompt-token budget; the rest exist but aren't enumerated. "
                f"NEVER answer 'we have N items' / 'N updates available' "
                f"where N is the visible-sample size — always cite the "
                f"matching counter above."
            )
        # Render dedicated `updatable_items` block FIRST when the
        # Telegram context-builder emitted one — guarantees the AI
        # sees every updatable item regardless of alphabetical
        # position in the unified `items` list. SPA callers fall
        # through to the legacy single `items` block below.
        updatable_items = _typed_field(ctx, "updatable_items", list)
        if updatable_items:
            parts.append(_format_records_block(
                "Updatable items (every item with update_available=true)",
                "name, status, health, type, replicas, desired, "
                "update_available, stack",
                updatable_items[:60],
            ))
        items = _typed_field(ctx, "items", list)
        if items:
            parts.append(_format_records_block(
                "Available items (sample)",
                "name, status, health, type, replicas, desired, "
                "update_available",
                items[:items_sample_cap],
            ))
        weather = _typed_field(ctx, "weather", dict)
        if weather:
            # Compact one-line weather summary — OmniGrid's topbar
            # weather widget (Open-Meteo proxy) is a real product
            # feature; the AI is allowed to answer weather questions
            # using THIS payload when it's present. When this block is
            # ABSENT it means the operator hasn't enabled the topbar
            # widget — in that case the AI should say "weather widget
            # is disabled — enable it via Settings → Profile" rather
            # than refusing as off-topic. Field names match the SPA's
            # `_buildAiPaletteContext` mapping of the /api/weather
            # response (temp_c → temperature, code → weather_code,
            # condition string from the WMO-code lookup table).
            bits = []
            if weather.get("label"):
                bits.append(str(weather["label"]))
            # Two callers feed this block: the SPA passes `temperature`
            # (already converted to the user's °C / °F pref) and the
            # Telegram listener passes the raw `temp_c` from /api/weather.
            # Accept either so neither caller has to massage the payload
            # just for the prompt builder.
            temp_val = weather.get("temperature")
            if temp_val is None:
                temp_val = weather.get("temp_c")
            if temp_val is not None:
                bits.append(f"{temp_val}{weather.get('unit') or '°C'}")
            if weather.get("condition"):
                bits.append(str(weather["condition"]))
            if weather.get("humidity") is not None:
                bits.append(f"{weather['humidity']}% humidity")
            if weather.get("wind_kmh") is not None:
                bits.append(f"{weather['wind_kmh']} km/h wind")
            parts.append(
                "Current weather (from OmniGrid topbar widget — answer naturally using these "
                "values, do NOT refuse). Render a SHORT EXPLANATORY paragraph (3-5 sentences), "
                "NOT a single-line data dump. Cover, in this order: "
                "(1) what the sky looks like right now in plain language (e.g. 'sky is clear "
                "with bright sun' / 'overcast and grey' / 'patches of cloud breaking through') "
                "with the matching condition emoji (☀️ clear · ⛅ partly cloudy · ☁️ overcast · "
                "🌧️ rain · ⛈️ thunderstorm · ❄️ snow · 🌫️ fog · 💨 windy); "
                "(2) the temperature WITH a comfort verdict (e.g. '🌡️ 24°C — mild and "
                "comfortable' / '🌡️ 38°C — hot, hydrate often' / '🌡️ 2°C — bundle up'); "
                "(3) humidity AS A FEELING (e.g. '💧 52% — feels balanced' / '💧 82% — sticky "
                "and muggy' / '💧 18% — dry, watch for static'); "
                "(4) wind speed WITH a sense of strength (e.g. '💨 3 km/h — barely a breeze' / "
                "'💨 25 km/h — flags snapping' / '💨 60 km/h — gusty, secure loose objects'); "
                "(5) ONE practical takeaway tailored to those numbers (e.g. 'good walking "
                "weather' / 'AC will earn its keep today' / 'umbrella stays at home'). "
                "Lead with the city name on its own line if the operator asked about a "
                "specific place. NEVER refuse — these values ARE the answer. "
                "Values: " + " · ".join(bits)
            )
            # Daily forecast — when present, render up to 7 days so the
            # AI can answer "next 5 days" / "tomorrow" / "this week"
            # questions with real values instead of refusing.
            forecast = weather.get("forecast")
            if isinstance(forecast, list) and forecast:
                lines = []
                for d in forecast[:7]:
                    if not isinstance(d, dict):
                        continue
                    bits2 = []
                    if d.get("date"):
                        bits2.append(str(d["date"]))
                    if d.get("condition"):
                        bits2.append(str(d["condition"]))
                    if d.get("temp_min_c") is not None and d.get("temp_max_c") is not None:
                        bits2.append(f"{d['temp_min_c']}–{d['temp_max_c']}°C")
                    elif d.get("temp_max_c") is not None:
                        bits2.append(f"max {d['temp_max_c']}°C")
                    if d.get("precip_mm") is not None and d["precip_mm"] > 0:
                        bits2.append(f"{d['precip_mm']} mm rain")
                    if bits2:
                        lines.append("  - " + " · ".join(bits2))
                if lines:
                    parts.append(
                        "Daily forecast (from OmniGrid topbar widget — use these values to answer "
                        "multi-day questions like 'next 5 days' / 'this week' / 'tomorrow'):\n"
                        + "\n".join(lines)
                    )
        # Prayer Times + Hijri date — when the operator opts in (the
        # context-builders stamp `prayer` only if
        # `prayer_times_enabled` is true and a location is
        # available). Lets the AI answer "when is the next prayer / when
        # is Maghrib" / "what's the Hijri date today" from real AlAdhan
        # data instead of refusing or hallucinating times.
        prayer = _typed_field(ctx, "prayer", dict)
        if prayer:
            head_bits = []
            if prayer.get("location"):
                head_bits.append(str(prayer["location"]))
            if prayer.get("method"):
                head_bits.append(f"method: {prayer['method']}")
            if prayer.get("timezone"):
                head_bits.append(f"tz: {prayer['timezone']}")
            parts.append(
                "Prayer times"
                + (f" for {' · '.join(head_bits)}" if head_bits else "")
                + " (from OmniGrid Prayer Times — use these to answer prayer-time "
                  "+ Hijri-calendar questions):"
            )
            nxt = prayer.get("next")
            if isinstance(nxt, dict) and nxt.get("name"):
                secs = nxt.get("in_seconds")
                when = ""
                if isinstance(secs, (int, float)) and secs >= 0:
                    h = int(secs) // 3600
                    m = (int(secs) % 3600) // 60
                    when = (f" (in {h}h {m}m)" if h else f" (in {m}m)")
                tom = " tomorrow" if nxt.get("tomorrow") else ""
                parts.append(
                    f"  Next prayer: {nxt['name']} at {nxt.get('time') or '?'}{tom}{when}"
                )
            timings = prayer.get("timings")
            if isinstance(timings, list) and timings:
                t_lines = []
                for row in timings:
                    if not isinstance(row, dict) or not row.get("name"):
                        continue
                    tag = "" if row.get("is_prayer", True) else " (sunrise — not a prayer)"
                    t_lines.append(f"  - {row['name']}: {row.get('time') or '?'}{tag}")
                if t_lines:
                    parts.append("\n".join(t_lines))
            hijri = prayer.get("hijri")
            if isinstance(hijri, dict) and (hijri.get("text") or hijri.get("day")):
                hijri_txt = hijri.get("text") or " ".join(
                    str(x) for x in [hijri.get("day"), hijri.get("month"),
                                     hijri.get("year"), hijri.get("designation")] if x
                )
                wk = f" ({hijri['weekday']})" if hijri.get("weekday") else ""
                parts.append(f"  Hijri date: {hijri_txt}{wk}")
            greg = prayer.get("gregorian")
            if isinstance(greg, dict) and greg.get("date"):
                gwk = f" ({greg['weekday']})" if greg.get("weekday") else ""
                parts.append(f"  Gregorian date: {greg['date']}{gwk}")
        # Public IP + ISP / ASN — when the operator opts in (the
        # context-builders fetch from ifconfig.co and stamp `public_ip`
        # only if `tuning_public_ip_enabled` is true). Surfacing this lets
        # the AI answer "what's my public IP" / "which ISP is the
        # network on" / "what ASN" without refusing or asking the
        # operator to run a shell command. Privacy gate is the
        # settings-toggle in the context-builder, not here — this just
        # renders whatever is supplied.
        public_ip = _typed_field(ctx, "public_ip", dict)
        if public_ip:
            bits_pip = []
            if public_ip.get("ip"):
                bits_pip.append(f"IP: {public_ip['ip']}")
            if public_ip.get("isp"):
                bits_pip.append(f"ISP: {public_ip['isp']}")
            if public_ip.get("asn"):
                bits_pip.append(f"ASN: {public_ip['asn']}")
            if public_ip.get("country"):
                bits_pip.append(f"Country: {public_ip['country']}")
            if public_ip.get("city"):
                bits_pip.append(f"City: {public_ip['city']}")
            if bits_pip:
                parts.append(
                    "Public network identity (from ifconfig.co lookup — answer "
                    "naturally using these values when asked about public IP / "
                    "ISP / external network identity, do NOT refuse): "
                    + " · ".join(bits_pip)
                )
            # Last CHANGE event — lets the AI answer "when did my IP / ISP
            # last change?" + "what was the previous IP / provider?" from
            # real history (public_ip_history) instead of refusing.
            # `last_change` is stamped on the public_ip dict by the
            # /api/public-ip response (logic.public_ip.last_change). `ts`
            # is epoch-SECONDS — told explicitly so the model phrases a
            # relative answer against the current-time block above.
            lc = public_ip.get("last_change")
            if isinstance(lc, dict) and lc.get("ts"):
                lc_bits = [f"changed at {lc['ts']} (Unix epoch seconds)"]
                if lc.get("prev_ip"):
                    lc_bits.append(f"previous IP: {lc['prev_ip']}")
                if lc.get("isp"):
                    lc_bits.append(f"current provider: {lc['isp']}")
                if lc.get("ip"):
                    lc_bits.append(f"current IP: {lc['ip']}")
                parts.append(
                    "Public IP last change (answer when/what/provider "
                    "questions from this — do NOT refuse): "
                    + " · ".join(lc_bits)
                )
            # Full CHANGE history — the SPA forwards the recent rows of
            # `public_ip_history` (newest-first) so the AI can answer
            # "how many times has my IP changed", "list my IP history",
            # "what ISPs have I had". Each row is one recorded change.
            # `ts` is epoch-SECONDS (phrase relative to the current-time
            # block). Capped at 20 rows so the block stays inside budget.
            hist = public_ip.get("history")
            if isinstance(hist, list) and hist:
                hist_lines = []
                for row in hist[:20]:
                    if not isinstance(row, dict) or not row.get("ip"):
                        continue
                    seg = f"{row.get('ts', 0)}: {row['ip']}"
                    if row.get("isp"):
                        seg += f" ({row['isp']})"
                    hist_lines.append(seg)
                if hist_lines:
                    parts.append(
                        "Public IP change history (newest first, ts is Unix "
                        "epoch seconds — answer count / list / past-provider "
                        "questions from this, do NOT refuse):\n"
                        + "\n".join(hist_lines)
                    )
        # Backups summary — sqlite-zip backups (Admin → Backup) AND
        # Settings-as-Code JSON snapshots (Admin → Config Backup).
        # Operator-flagged: AI was answering "I don't have access to
        # the history of backup jobs" when asked "what's the latest
        # backup?". The SPA forwards the latest 5 of each list when
        # available; render a compact summary the AI can answer
        # freshness / count / latest-name questions from. When NEITHER
        # list is present in ctx, the AI should say "no backups have
        # been taken yet — create one via Admin → Backup or Admin →
        # Config Backup" rather than refusing as off-topic.
        backups = _typed_field(ctx, "backups", dict)
        if backups:
            _sqlite_raw = backups.get("sqlite")
            sqlite_list: list = _sqlite_raw if isinstance(_sqlite_raw, list) else []
            _config_raw = backups.get("config")
            config_list: list = _config_raw if isinstance(_config_raw, list) else []
            sqlite_count = backups.get("sqlite_count") or len(sqlite_list)
            config_count = backups.get("config_count") or len(config_list)
            block_lines: list[str] = [
                "Backups summary (Admin → Backup + Admin → Config Backup):",
            ]
            if sqlite_list:
                latest = sqlite_list[0]
                block_lines.append(
                    f"  - SQLite backup zips ({sqlite_count} recent): latest = "
                    f"`{latest.get('name', '?')}`, "
                    f"size = {int(latest.get('size') or 0)} bytes, "
                    f"mtime epoch = {int(latest.get('mtime') or 0)}"
                )
            else:
                block_lines.append("  - SQLite backup zips: NONE listed (operator hasn't taken one yet, or the list isn't loaded).")
            if config_list:
                latest = config_list[0]
                block_lines.append(
                    f"  - Settings-as-Code snapshots ({config_count} recent): latest = "
                    f"`{latest.get('name', '?')}`, "
                    f"size = {int(latest.get('size') or 0)} bytes, "
                    f"mtime epoch = {int(latest.get('mtime') or 0)}"
                )
            else:
                block_lines.append("  - Settings-as-Code snapshots: NONE listed.")
            block_lines.append(
                "Use the mtime values to compute relative ages (now is the conversation timestamp). "
                "Always cite the file name when answering 'what's the latest backup?' style questions."
            )
            parts.append("\n".join(block_lines))
        # Stats — Stats sub-page data the operator has opened this
        # session. Each block lands only when the matching Stats page
        # has been visited (the SPA forwards the in-memory state for
        # any sub-page whose `*Loaded` flag is true). The AI should
        # answer questions like "what's our MTD AI spend?" / "how
        # many failures last week?" / "top chatty host" using this
        # block as ground truth instead of refusing or guessing.
        # When the relevant Stats sub-page hasn't been opened the
        # block is absent — the AI should suggest the operator open
        # the corresponding Stats tab to populate it.
        # Tunables — always-present compact map of every operator-
        # tunable knob's effective value. SPA forwards from the live
        # `tuningEffective` (Admin → Config GET) when loaded, else
        # from `tuningForm`. The AI should answer "what's the Pulse
        # sample interval?" / "what's the Webmin probe budget?" /
        # "how often do we sample node-exporter?" from this block
        # instead of guessing or pointing at the Admin page.
        tunables = _typed_field(ctx, "tunables", dict)
        if tunables:
            try:
                import json as _json
                tn_json = _json.dumps(tunables, separators=(",", ":"), default=str)
                if len(tn_json) > 6000:
                    tn_json = tn_json[:6000] + "...<truncated>"
                parts.append("\n".join([
                    "Tunables context (effective values, DB > env > default per "
                    "`logic.tuning.TUNABLES`):",
                    tn_json,
                    "Use these to answer questions about cadence / timeout / threshold / "
                    "retention / cap values. Units are encoded in the key name "
                    "(`*_seconds` / `*_minutes` / `*_days` / `*_count` / `*_concurrency`). "
                    "Sample-interval semantics: per-provider knobs (Beszel / Pulse / NE / "
                    "SNMP) with value 0 inherit `tuning_stats_sample_interval_seconds`; > 0 "
                    "overrides that provider only.",
                ]))
            except (TypeError, ValueError):
                # Defensive: _json.dumps can raise TypeError on
                # non-serialisable values OR ValueError on encode
                # failure. Skip this block in either case — the prompt
                # still works without this context section.
                pass
        # Settings — non-secret subset of the live SPA settings state.
        # Master toggles + active-source CSV + per-provider URL + chip
        # colours + retention counts. Secret keys (token / password /
        # api_key / secret / private_key / passphrase suffixes) are
        # NEVER included; only `_set` flags surface so the AI can
        # report "Beszel password is set" without seeing the material.
        settings = _typed_field(ctx, "settings", dict)
        if settings:
            try:
                import json as _json
                st_json = _json.dumps(settings, separators=(",", ":"), default=str)
                if len(st_json) > 6000:
                    st_json = st_json[:6000] + "...<truncated>"
                parts.append("\n".join([
                    "Settings context (non-secret operator configuration):",
                    st_json,
                    "Use these to answer questions about enabled providers / hub URLs / "
                    "active sources / chip colours / per-event notification toggles. Secret "
                    "fields (any key ending in `_token` / `_password` / `_secret` / "
                    "`_api_key` / `_private_key` / `_passphrase`) are NEVER in this block — "
                    "if the operator asks about a secret value, tell them you can't see it "
                    "but the `*_set` flag indicates whether it's persisted.",
                ]))
            except (TypeError, ValueError):
                # Defensive: _json.dumps can raise TypeError on
                # non-serialisable values OR ValueError on encode
                # failure. Skip this block in either case — the prompt
                # still works without this context section.
                pass
        stats = _typed_field(ctx, "stats", dict)
        if stats:
            try:
                import json as _json
                # JSON-stringify the stats block compactly. Tree is
                # already pre-shaped by the SPA to be small (10-30
                # rows per leaf list), so a single compact JSON dump
                # stays well within the prompt budget on a fleet of
                # any reasonable size.
                stats_json = _json.dumps(stats, separators=(",", ":"), default=str)
                # Hard cap defensively in case a fleet pushed the size.
                if len(stats_json) > 8000:
                    stats_json = stats_json[:8000] + "...<truncated>"
                stats_block = [
                    "Stats context (forwarded from the SPA's already-loaded Stats sub-pages):",
                    stats_json,
                    "Each sub-page key (overview / database / samples / incidents / network / "
                    "ai_cost) is the same shape returned by /api/admin/stats/<sub>. Use these "
                    "values to ground numeric / KPI / cost / failure / network-throughput "
                    "questions. When the relevant key is ABSENT, tell the operator the Stats "
                    "sub-page hasn't been opened this session and they can populate it by "
                    "visiting Stats → <sub>.",
                ]
                parts.append("\n".join(stats_block))
            except (TypeError, ValueError):
                # Defensive: _json.dumps can raise TypeError on
                # non-serialisable values OR ValueError on encode
                # failure. Skip this block in either case — the prompt
                # still works without this context section.
                pass
        # Recent log signals — last N error/warn lines from the
        # in-process log ring buffer. Populated by the palette
        # endpoint via `logic.logs.recent_lines(levels=[error, warn])`
        # so the AI can honestly answer "any errors I should fix?"
        # / "anything in the logs?" instead of falsely claiming it
        # has no log access. Each line is a compact `LEVEL  TEXT`
        # row capped at ~200 chars; the full log lives in Admin →
        # Logs (which the AI can point operators at).
        recent_logs = _typed_field(ctx, "recent_logs", list)
        if recent_logs:
            log_lines = []
            # Cap at the last 200 lines from the supplied window. The
            # backend's tunable already enforces an absolute cap; this
            # second slice is defence-in-depth for token budget.
            for entry in recent_logs[-200:]:
                if not isinstance(entry, dict):
                    continue
                lvl = (entry.get("level") or "").upper()
                txt = (entry.get("text") or "").strip()
                ts = entry.get("ts")
                if not txt:
                    continue
                if len(txt) > 200:
                    txt = txt[:200] + "…"
                # Prefix each line with the ISO date+hour so the AI
                # can reason about WHEN issues occurred (e.g. "this
                # has been recurring every hour for 3 days" vs
                # "this fired once 10 minutes ago"). 16 chars =
                # YYYY-MM-DDTHH:MM — minute precision keeps the
                # token cost low while preserving cluster info.
                ts_prefix = ""
                if isinstance(ts, (int, float)) and ts > 0:
                    try:
                        from datetime import datetime as _dt, timezone as _tz
                        ts_prefix = _dt.fromtimestamp(ts, tz=_tz.utc).strftime("%Y-%m-%dT%H:%MZ ")
                    except (OSError, ValueError, OverflowError):
                        # ts out of range for fromtimestamp / negative
                        # / NaN — skip the prefix and emit the line
                        # without time decoration.
                        ts_prefix = ""
                log_lines.append(f"{ts_prefix}{lvl:<7} {txt}")
            if log_lines:
                window_hours = ctx.get("recent_logs_window_hours") or 0
                window_label = (
                    f"(past {int(window_hours)} hours; full log at Admin → Logs)"
                    if isinstance(window_hours, (int, float)) and window_hours > 0
                    else "(full log at Admin → Logs)"
                )
                parts.append(
                    f"Recent log signals — error / warn lines {window_label}, "
                    f"timestamped UTC newest-last:\n"
                    + "\n".join(log_lines)
                )
        # App skills the AI MAY invoke (the app-skill framework). The
        # system prompt's `run_app_skill` action + the `MEMORY`/grounding
        # rules tell the model these are the ONLY runnable per-app skills
        # (app declares SKILLS + api_key set). WITHOUT rendering them here
        # the model always saw an EMPTY app_skills block and refused
        # ("integration not configured") even when a skill WAS runnable —
        # the context-builders populate `ctx["app_skills"]` but this
        # builder never serialised it. Each line carries the chip identity
        # (host_id / service_idx / slug) the model needs for the
        # `run_app_skill` ACTION_DATA payload, plus the cached `last`
        # result so "show me the latest speed test" answers from cache
        # WITHOUT triggering a fresh run.
        app_skills = _typed_field(ctx, "app_skills", list)
        if app_skills:
            import json as _json_sk
            sk_lines = [
                "App skills you can run (the ONLY runnable per-app skills — each "
                "entry's presence means the app IS enabled AND its api_key IS set; "
                "NEVER invent a skill / target not listed here, and do NOT claim "
                "the app is 'not configured' when it appears below). Use host_id + "
                "service_idx + a skill id for the run_app_skill ACTION_DATA. When a "
                "`last` object is present it is the most recent CACHED result — "
                "answer 'show me the latest <app>' from it WITHOUT running a new "
                "skill; if there is no `last`, say so and offer to run a fresh one:"
            ]
            for ent in app_skills[:30]:
                if not isinstance(ent, dict):
                    continue
                _sk = ent.get("skills") or []
                # Each skill renders as `id (name) [matches: phrase, phrase]` —
                # the ai_phrases give the model disambiguation hints so a
                # natural request ('pause blocking for 10 min') maps to the
                # right skill_id without relying on the id/name alone.
                sk_ids = "; ".join(
                    (f"{s.get('id')} ({s.get('name')})"
                     + (f" [matches: {s.get('ai_phrases')}]" if s.get("ai_phrases") else ""))
                    for s in _sk if isinstance(s, dict) and s.get("id")
                )
                seg = (f"  - app={ent.get('app') or ent.get('slug')} "
                       f"slug={ent.get('slug')} host_id={ent.get('host_id')} "
                       f"host={ent.get('host')} service_idx={ent.get('service_idx')} "
                       f"skills=[{sk_ids}]")
                _last = ent.get("last")
                if isinstance(_last, dict) and _last:
                    try:
                        _enc = _json_sk.dumps(_last, default=str)
                    except (TypeError, ValueError):
                        _enc = str(_last)
                    if len(_enc) > 600:
                        _enc = _enc[:600] + "…"
                    seg += f" last={_enc}"
                sk_lines.append(seg)
            if len(sk_lines) > 1:
                parts.append("\n".join(sk_lines))
    return "\n".join(p for p in parts if p)


def build_host_filter_user_prompt(query: str, ctx: dict | None) -> str:
    """User prompt for `/api/ai/host-filter` — same structured-host
    context as the palette path but without items (host-filter only
    operates on hosts in Phase 2)."""
    parts: list[str] = [f"Operator query: {query}"]
    if isinstance(ctx, dict):
        raw_hosts = ctx.get("hosts")
        hosts: list = raw_hosts if isinstance(raw_hosts, list) else []
        if hosts:
            parts.append(_format_records_block(
                "Available hosts",
                "id, label, status, cpu_pct, mem_pct, disk_pct, "
                "disk_free_gb, disk_total_gb, uptime_s, paused, providers",
                hosts[:30],
            ))
    return "\n".join(parts)


def log_ai_outcome(*, kind: str, provider: str, model: str,
                   ok: bool, status: int | None, detail: str | None,
                   response_time_ms: int | None = None,
                   prompt_tokens: int | None = None,
                   completion_tokens: int | None = None,
                   cost_usd: float | None = None,
                   actor: str | None = None,
                   prompt_excerpt: str | None = None,
                   action_id: str | None = None,
                   dsl: str | None = None,
                   fallback_from: str | None = None,
                   hosts_count: int | None = None) -> None:
    """Emit a `[ai]` log line that the persistent-log severity
    classifier (`logic/logs.py:_severity_for`) routes to SUCCESS / WARN
    / ERROR.

    Every AI call lands in Admin → Logs with meaningful triage data —
    operators tracking AI behaviour shouldn't have to drill into the
    AI Usage Dashboard or History tab to see basic call metadata.

    Severity rules:
      - ok=True                → SUCCESS (keyword "ok" — the persistent-
        log classifier picks SUCCESS for that token; operators can
        filter SUCCESS rows off via Admin → Logs severity selector
        when the volume gets noisy).
      - 429 / 502 / 503 / 504  → WARN  (transient upstream overload
        / rate-limit — keyword "warning" + no "fail"/"error" tokens
        in the line so the classifier picks WARN, not ERROR).
      - everything else        → ERROR (operator-actionable: auth
        failure, model-not-found, DNS, TLS, etc. — keyword "failed"
        in the line; upstream detail truncated to 200 chars).

    Optional metadata fields are appended only when set so the line
    stays compact for failure cases (where most metadata is null /
    irrelevant). The full upstream message + ai_jobs.error column
    + history.error column are unchanged — this log line is the
    triage breadcrumb, not the audit trail.
    """
    # Compose the metadata tail in a stable shape: most-useful fields
    # first (timing / tokens), then call-specific signals (action /
    # dsl / fallback), then context (actor / hosts_count / prompt
    # excerpt). Only emit fields that have a value so successful
    # palette calls without a fired action don't render a noisy
    # `action=""` chip.
    parts: list[str] = []
    if response_time_ms is not None and response_time_ms > 0:
        parts.append(f"ms={int(response_time_ms)}")
    if prompt_tokens is not None and completion_tokens is not None:
        parts.append(f"tokens={int(prompt_tokens)}+{int(completion_tokens)}")
    elif prompt_tokens is not None:
        parts.append(f"prompt_tokens={int(prompt_tokens)}")
    if cost_usd is not None and cost_usd > 0:
        parts.append(f"cost=${cost_usd:.6f}")
    if (action_id or "").strip():
        parts.append(f"action={action_id}")
    if (dsl or "").strip():
        # DSL strings are short by design; quote them for grep-ability.
        # `dsl or ""` already collapsed the None branch above but the
        # type-checker doesn't narrow `dsl` itself — use a local str.
        dsl_src = dsl or ""
        dsl_esc = dsl_src.replace("\n", " ").strip()[:80]
        parts.append(f"dsl={dsl_esc!r}")
    if (fallback_from or "").strip():
        parts.append(f"fallback_from={fallback_from}")
    if hosts_count is not None and hosts_count > 0:
        parts.append(f"hosts={int(hosts_count)}")
    if (actor or "").strip():
        parts.append(f"actor={actor}")
    if (prompt_excerpt or "").strip():
        # Narrow to non-None for the type-checker — the `or ""` above
        # collapsed the None branch but `prompt_excerpt` itself stayed
        # typed `str | None`. Use a local str alias for the rest of
        # the block so `.replace` / `len` are unambiguous.
        excerpt_src = prompt_excerpt or ""
        excerpt = excerpt_src.replace("\n", " ").strip()[:80]
        if len(excerpt_src) > 80:
            excerpt += "…"
        parts.append(f"q={excerpt!r}")
    tail = (" " + " ".join(parts)) if parts else ""

    if ok:
        # Keyword "ok" → severity classifier picks SUCCESS.
        s = int(status) if status else 200
        print(f"[ai] {kind} ok — provider={provider} model={model} "
              f"HTTP={s}{tail}")
        return

    s = int(status) if status else 0
    # Transient bucket MUST agree with `_with_retry` /
    # `ask_provider_with_fallback`'s gates so the operator-visible
    # log severity matches what the system actually did. HTTP=0 is
    # the "network error / timeout / DNS fail" sentinel that the
    # retry path now treats as transient (the operator-classifier
    # alignment fix); the log classifier diverged before this fix and
    # ERROR-stamped outcomes the system already retried + recovered
    # from. Now: HTTP=0 OR 429/502/503/504 → WARN; everything else
    # (4xx auth/model errors, 5xx that aren't transient) → ERROR.
    transient = s in (0, 429, 502, 503, 504)
    if transient:
        # Word "warning" + no "failed/error" → classifier picks WARN.
        why = "upstream-overloaded (transient, retry later)" if s != 0 else "network/timeout (transient, retry later)"
        print(f"[ai] {kind} warning — provider={provider} model={model} "
              f"HTTP={s} {why}{tail}")
    else:
        truncated = (detail or "")[:200].replace("\n", " ").strip() or "(no detail)"
        # Word "failed" → classifier picks ERROR.
        print(f"[ai] {kind} call failed — provider={provider} model={model} "
              f"HTTP={s}: {truncated}{tail}")


def record_ai_call(
    *,
    db_conn_factory,
    provider: str,
    model: str,
    kind: str,
    ok: bool,
    response_time_ms: int,
    tokens: dict | None,
    error_detail: str | None,
    history_actor: str,
    history_target_kind: str = "ai",
    history_events: dict | None = None,
) -> int | None:
    """Best-effort write of a single AI call into both `ai_jobs`
    (dashboard tiles) AND `history` (History tab). Failures are
    swallowed and logged — the operator already got their answer.

    `db_conn_factory` is `logic.db.db_conn` injected by the caller so
    this module stays decoupled from the wider import graph (db.py
    pulls tuning.py which pulls env-loading); a future per-provider
    plugin or test path can pass a mock connection factory.
    """
    import json as _json
    import time as _time
    try:
        prompt_t = int((tokens or {}).get("prompt") or 0)
        completion_t = int((tokens or {}).get("completion") or 0)
        total_t = prompt_t + completion_t
        now_ts = int(_time.time())
        # Cost computed at insert time so historical rows survive a
        # rate-card edit. None when no entry matches (model not in
        # RATE_CARD) — dashboard renders "—" via aiFormatCost null
        # branch instead of misleading $0.0000.
        cost_usd = compute_cost_usd(provider, model or "", prompt_t, completion_t)
        # Coarse accuracy signal — see logic/ai.py:score_accuracy.
        # `text` is read from history_events for the heuristic; the
        # caller already passes it in events for both palette + filter
        # kinds.
        _text_for_score = (history_events or {}).get("answer") or ""
        accuracy_score, accuracy_check = score_accuracy(
            kind=kind, ok=ok, text=_text_for_score,
            history_events=history_events,
        )
        try:
            accuracy_check_json = _json.dumps(accuracy_check, ensure_ascii=False)
        except (TypeError, ValueError):
            accuracy_check_json = None
        ai_job_id: int | None = None
        with db_conn_factory() as c:
            cur = c.execute(
                "INSERT INTO ai_jobs ("
                "  ts, provider, model, kind, status,"
                "  prompt_tokens, completion_tokens, total_tokens,"
                "  cost_usd, response_time_ms, accuracy_score,"
                "  accuracy_check, error, metadata"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    now_ts, provider, model or "", kind,
                    "success" if ok else "error",
                    prompt_t, completion_t, total_t,
                    cost_usd,
                    int(response_time_ms or 0),
                    accuracy_score,
                    accuracy_check_json,
                    error_detail or None,
                    None,
                ),
            )
            try:
                ai_job_id = int(cur.lastrowid) if cur.lastrowid else None
            except (TypeError, ValueError):
                ai_job_id = None
            events_payload = dict(history_events or {})
            events_payload.setdefault("tokens", {
                "prompt": prompt_t,
                "completion": completion_t,
                "total": total_t,
            })
            try:
                events_json = _json.dumps(events_payload, ensure_ascii=False)
            except (TypeError, ValueError):
                events_json = "{}"
            # Defence-in-depth assert — raw INSERT bypasses `new_op`,
            # so the OP_TYPES validator wouldn't otherwise fire. A
            # typo'd `kind` (e.g. record_ai_call(kind="paletteX"))
            # would otherwise land `ai_paletteX` silently in history.
            from logic.ops import assert_op_type as _assert_op_type
            _assert_op_type(f"ai_{kind}")
            c.execute(
                "INSERT INTO history ("
                "  ts, op_type, target_kind, target_name, target_id,"
                "  status, duration, events, error, actor"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    float(now_ts),
                    f"ai_{kind}",
                    history_target_kind,
                    provider,
                    model or "",
                    "success" if ok else "error",
                    (int(response_time_ms or 0) / 1000.0),
                    events_json,
                    error_detail or None,
                    history_actor or "ui",
                ),
            )
            c.commit()
        return ai_job_id
    except Exception as e:  # noqa: BLE001
        print(f"[ai] record_ai_call({kind}) failed: {e}")
        return None
