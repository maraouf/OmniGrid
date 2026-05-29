"""User-triggered write operations and the in-memory op log.

Five ``_do_*`` handlers (update stack, update container, restart service,
restart container, remove container) wrap Portainer calls with:

  - structured event logging via :class:`Operation.log`
  - persistent history row on completion (``persist_history``)
  - notification fan-out via :func:`notify` (Apprise + in-app store)
  - gather-cache invalidation so the UI re-polls after the mutation

The ``ops`` dict + ``ops_order`` list hold the last 50 operations in
memory for the ``/api/ops`` live-status polling loop — they're NOT the
source of truth for history (the ``history`` SQLite table is). If ops
ever need to outlive a process restart, wire a persistence hook in
:func:`new_op`, but the single-replica invariant (CLAUDE.md) makes
in-memory fine for now.

Notification dispatcher
-----------------------
:func:`notify` is the single entry point used by every _do_* handler
plus the host_metrics_sampler / login paths. It resolves the per-event
toggle (``notify_event_<name>``), then fans out to every enabled
medium in :data:`NOTIFY_MEDIUMS`. Mediums today: ``app`` (in-app
store backed by the ``notifications`` table) and ``apprise`` (HTTP
POST to the operator's Apprise instance). Each medium honours its own
admin-side enable flag (``notify_medium_<name>``) — the per-event
toggle gates the WHOLE notification, the per-medium toggle gates ONE
delivery channel without disabling the event entirely.

Adding a medium: see CLAUDE.md "Canonical extension pattern: add a
notification medium" — six steps (module + dispatcher + toggle + UI
+ i18n + CHANGELOG).

Notification templates
----------------------
Each event has a hard-coded default title + body baked into the
``NOTIFY_TEMPLATE_DEFAULTS`` map below; admins can override either via
the DB-backed ``notify_template_<event>_title`` /
``notify_template_<event>_body`` settings. :func:`render_template`
runs ``str.format_map`` against a :class:`SafeDict` so unknown
``{placeholder}`` tokens render verbatim instead of crashing the
notification dispatch. The set of placeholders supplied per call lives
in :data:`NOTIFY_PLACEHOLDERS` (curated whitelist) — see
``main.api_admin_notify_templates`` for the full surface.
"""
# noinspection PyUnresolvedReferences
import asyncio  # noqa: F401 — used at asyncio.gather()/create_task()
# callsites below; IDE marks unused because `logic.ops_extras` (loaded
# via the tail star-import on line ~1682) ALSO imports asyncio,
# re-binding the name in ops's namespace after the star-import. Same
# shadow pattern applies to `time` / `httpx` / `portainer` below — see
# the ai.py block for the full rationale.
import json
import sqlite3 as _sqlite3
# noinspection PyUnresolvedReferences
import time  # noqa: F401 — used at 11 `time.time()` callsites; same
# shadow-from-ops_extras false-positive as `asyncio` above.
import uuid
from typing import Awaitable, Callable, Optional, Union

# noinspection PyUnresolvedReferences
import httpx  # noqa: F401 — used at `except httpx.HTTPError`; same
# shadow-from-ops_extras false-positive as `asyncio` / `time` above.

# `portainer` is used at `portainer.write_client(...)`; same
# shadow-from-ops_extras false-positive as `asyncio` / `time` / `httpx`
# above (IDE marks unused due to the tail star-import re-binding).
# noinspection PyUnresolvedReferences
from logic import events, metrics, portainer  # noqa: F401
from logic.db import db_conn, get_setting, get_setting_bool
from logic.settings_keys import Settings, notify_event_key, notify_medium_key, notify_template_body_key, notify_template_title_key
from logic.tuning import tuning_int as _tuning_int


def _portainer_op_timeout(tier: str) -> float:
    """Resolve a Portainer write-op timeout via the TUNABLES tier
    knobs (short / medium / long). Per-use read so Admin → Config
    edits take effect on the next op without a restart. Defensive
    fallback to the previous hardcoded value if the tunable read
    raises (corrupt DB state)."""
    fallback = {"short": 120.0, "medium": 300.0, "long": 600.0}.get(tier, 300.0)
    try:
        return float(_tuning_int(f"tuning_portainer_op_timeout_{tier}_seconds"))
    except (KeyError, ValueError, TypeError):
        return fallback


def _truncate_for_log(text: Optional[str], n: int = 200) -> str:
    """Truncate ``text`` to at most ``n`` chars for an operator-facing
    log line. Returns an empty string for ``None`` / falsy input so the
    caller can interpolate without a `or '(empty)'` dance at every
    site. Appends an ellipsis marker (`…`) when truncation actually
    happened so reviewers know the line was clipped.

    Centralised so the three different cap sizes (200 / 300 / 500)
    scattered across the recreate-response handler in
    ``do_update_container`` stay readable + future log lines pick up
    the same convention. ``n`` is the visible-chars cap including
    the ellipsis marker (when added) so the log row stays under a
    predictable width regardless of source length.
    """
    if not text:
        return ""
    s = str(text)
    if len(s) <= n:
        return s
    # -1 for the ellipsis character so the visible-width budget is
    # respected exactly.
    return s[: max(0, n - 1)] + "…"


MAX_OPS = 50

# ---------------------------------------------------------------------------
# Canonical op_type registry — single source of truth for every value that
# can land in `history.op_type`. Each new writer (whether via `new_op` or a
# direct `INSERT INTO history`) MUST emit one of these literals; the
# `assert_op_type` validator below is called by `new_op` to catch typos /
# divergent names at write time.
#
# Why: the 2026-05-08 audit caught a `swarm_agent_restart` (API path) vs
# `restart_swarm_agent` (schedules path) drift that an audit-time check
# would have prevented. Centralising the names here makes the drift
# impossible — a fresh writer either uses an existing literal or trips the
# assert + has to add the literal here first (forcing a thought about i18n
# parity and audit coverage in the same edit).
#
# Adding a new op_type is a four-step contract:
#   1. Add the literal here.
#   2. Add `history.op_types.<name>` to `static/i18n/en.json` so the
#      History tab can label it.
#   3. Add the entry to the SPA's history `op_type` filter dropdown
#      (`static/js/app.js:historyOpTypeFilter` array).
#   4. Audit grep before shipping:
#      ```
#      grep -rohE 'op_type\s*=\s*"[a-z_]+"|new_op\("[a-z_]+"|started, "[a-z_]+",' main.py logic/ \
#        | sed -E 's/.*"([a-z_]+)".*/\1/' | sort -u
#      ```
#      Diff against this set; any missing name is a write-site that bypassed
#      the registry and needs adding.
#
# Out of scope: renaming any existing op_type literals — back-compat with
# the on-disk `history` table preserves shipped names. New names go through
# this registry; legacy names stay until the next MAJOR.
OP_TYPES: frozenset[str] = frozenset({
    # Item write-ops (Operation-backed; admin write-routes).
    "update_stack",
    "update_container",
    "restart_service",
    "restart_container",
    "remove_container",
    "restart_swarm_agent",
    # Drawer auto-fix — Portainer-API path that removes a stale
    # overlay network (matched by failing-subnet) and force-updates
    # the affected service so Docker recreates the overlay + a fresh
    # VXLAN interface. SSH-free.
    "cleanup_overlay_network",
    # Bulk host-state ops (api_hosts_bulk_*).
    "hosts_bulk_pause",
    "hosts_bulk_resume",
    # SSH surfaces.
    "ssh_run",
    "ssh_terminal",
    # Port-scan provider.
    "port_scan",
    # Schedule kinds (each `_run_<kind>` runner stamps history with the
    # kind's name).
    "prune_node",
    "prune_all_nodes",
    "gather_refresh",
    "backup",
    "config_backup",
    "asset_inventory_refresh",
    "prune_logs",
    "prune_notifications",
    "prune_config_backups",
    "swarm_agent_health",
    "port_scan_refresh",
    # AI surfaces — kind is dynamic in the call site (`f"ai_{kind}"`); the
    # values that actually fire today are the three below. Adding a new AI
    # kind requires a new literal here AND in the i18n + filter dropdown.
    "ai_palette",
    "ai_host_filter",
    "ai_telegram",
    # TOTP admin actions — written via raw SQL INSERT in
    # api_admin_user_disable_totp / api_admin_user_force_totp_set. Both
    # bypass `new_op` because they don't spawn an Operation; they're
    # audit-only history rows. The names are still under the registry
    # so the assert_op_type validator catches typos AND the History tab
    # filter / i18n bundle pick them up consistently with everything
    # else.
    "totp_admin_disabled",
    "totp_force_set",
    # Admin write-action audit-trail — every admin POST/PATCH/DELETE
    # that's NOT an Operation (and isn't a high-volume / low-stakes
    # path like notification mark-as-read) writes a synchronous direct
    # INSERT INTO history at the success path's top via
    # `assert_op_type(<canonical>)`. See CLAUDE.md "Admin write-actions
    # audit-trail gap" rule for the full contract — including which
    # paths are intentionally exempt and which canonical helper to use
    # for new audit rows. notification_read intentionally OUT
    # (high-volume + low-stakes per the UX review).
    "user_create",
    "user_update",
    "user_delete",
    "user_pw_reset",
    "session_revoke",
    "token_create",
    "token_revoke",
    "backup_create",
    "backup_delete",
    "backup_restore",
    "config_backup_save",
    "config_backup_import",
    "config_backup_restore",
    "config_backup_delete",
    "schedule_create",
    "schedule_update",
    "schedule_delete",
    "schedule_run_now",
    "notification_delete",
    "settings_update",
    "ai_memory_create",
    "ai_memory_delete",
    # Apps feature — catalog template CRUD + pin / discover-apply / probe.
    # Every Apps write endpoint writes a `history` row through
    # `_ops_mod.write_admin_audit` per the canonical audit-trail rule.
    # Probe-now is included despite higher volume because operators
    # explicitly trigger it (so each fire IS a tracked action, unlike
    # the lifespan-sampler ticks which write nothing).
    "services_catalog_create",
    "services_catalog_update",
    "services_catalog_delete",
    # Both `services_catalog_seed` (imperative — matches the AI palette
    # action name + the sibling create/update/delete style) and
    # `services_catalog_seeded` (past-tense — legacy audit-row label kept
    # for back-compat with existing history rows) are accepted. New
    # code should prefer the imperative form so the action name + op_type
    # name match. The i18n bundle aliases both keys to the same label so
    # the History filter dropdown shows one option for the pair.
    "services_catalog_seed",
    "services_catalog_seeded",
    "services_catalog_import",
    "services_pin",
    "services_unpin",
    "services_edit",
    "services_discover_apply",
    "services_probe_now",
    # Per-(table, host_id) sample-row prune. Drives the
    # Stats → Samples drill-down "Delete orphan rows" button so
    # operators can clean up rows left behind when a curated host
    # is deleted from Admin → Hosts. Audit row carries the table
    # name + host_id + deleted-row count in the events JSON.
    "samples_prune_orphan",
    # Host sampling resume — operator-initiated unpause. The matching
    # auto-pause path fires from the sampler (no operator) so the pause
    # itself isn't an audit event; the resume IS.
    "host_resume_sampling",
    "host_provider_resume",
    # Diagnostic data destruction — DELETE /api/logs wipes the in-memory
    # buffer. Audit before the clear so the forensic anchor survives.
    "logs_clear",
    # User self-service 2FA enrolment — admin-driven equivalents already
    # audited (totp_admin_disabled / totp_force_set). The self-service
    # paths mutate auth state and belong on the audit trail too.
    "totp_self_enroll",
    "totp_self_disable",
    "totp_self_regenerate_codes",
    "passkey_self_register",
    "passkey_self_delete",
    # Step-up auth FAILURE path. Success is invisible by design (reauth
    # is a stepping stone). Failures are operator-visible attempts that
    # the per-IP login limiter catches in aggregate; a per-event audit row
    # surfaces who-tried-when.
    "admin_reauth_failed",
    # Notification side-channels.
    "notify_test",
    # Operator-typed custom notification routed to ONE medium (POST
    # /api/notify/send + AI palette `send_notification` action). Distinct
    # from `notify_test` (fixed payload, fan-out to ALL enabled mediums).
    "notify_send",
    # AI palette diagnostic-tool dispatch — fired when the AI emits a
    # `TOOL: <name>` directive during a multi-round palette conversation
    # (`logic/ai.py:PALETTE_TOOL_CATALOGUE`). Forensic anchor so the
    # operator can trace "the AI ran a query on my behalf at 02:00 UTC"
    # back to the prompt + result without re-creating the conversation.
    # One row per tool call; the `target_kind` carries the tool name
    # ("get_recent_history" / "ssh_diag" / etc.) and `target_id` carries
    # the primary scope arg (host_id / target_id / preset name).
    "ai_tool_call",
    # Audit-trail destruction — DELETE /api/history wipes every row.
    # The trailing audit row is written AFTER the bulk delete so it
    # survives; the row's actor surfaces who-cleared-when.
    "history_cleared",
    # Ignore-pattern CRUD — affects gather filtering, operator-visible
    # behaviour change.
    "ignore_create",
    "ignore_delete",
    # Notification template overrides — admin-edited title/body that
    # changes the copy on every subsequent event firing.
    "notify_template_update",
    # Curated host list full-replace — single largest single-shot
    # mutation; rebuilds provider mappings, may rotate SNMP credentials.
    "hosts_config_update",
    # Bulk SNMP config mutators — already audited siblings to the
    # `hosts_bulk_pause` / `hosts_bulk_resume` pair.
    "hosts_bulk_snmp_vendors",
    "hosts_bulk_snmp_tunables",
    # Authentication audit-trail — login / logout / OIDC-login. The
    # Apprise `user_login` notification event is a SEPARATE channel
    # (operator-toggleable side-channel); the history row is the
    # first-class forensic record ("who signed in at 2am from IP X
    # yesterday?"). Both write paths need INSERT INTO history at the
    # success path's top. Logout writes a row too — the session-revoke
    # audit row only covers admin-initiated revokes, not self-logout.
    "user_login",
    "user_logout",
    "oidc_login",
    # Telegram surfaces — every /command and every authorised text
    # message routed through the Telegram listener writes ONE history
    # row at the dispatcher level via write_admin_audit(). The actor is
    # the linked OmniGrid username (or "telegram" for an unmapped
    # sender that somehow reached the dispatcher — should never happen
    # under the mapping gate but defended against). The events JSON
    # carries `{command, args, status, error?}` so the History tab's
    # row-detail pane shows which command was invoked + outcome. AI
    # free-text continues to flow through `ai_telegram` via
    # record_ai_call, which writes its own richer row to history AND
    # the ai_jobs table for the AI Usage dashboard — the dispatcher
    # SKIPS the generic audit row for AI traffic to avoid double-
    # logging.
    "telegram_command",
})

# Canonical op-status enum.
#
# Backend writes one of these into the `history.status` column. The History tab
# filter chips + i18n labels iterate this set so a new status added here only
# needs a matching `history.status_<name>` key + a filter chip; consumers stay
# in lock-step.
#
# Currently emitted:
#   - "running"   — Operation in-flight (set in Operation.__init__, replaced on
#                   completion with "success" or "error").
#   - "success"   — Op completed without exception.
#   - "error"     — Op raised; `error` column carries the exception text.
#   - "dry_run"   — SSH-preview / port-scan-preview path that intentionally did
#                   not perform the destructive side-effect.
OP_STATUSES: frozenset[str] = frozenset({
    "running",
    "success",
    "error",
    "dry_run",
})


def assert_op_type(op_type: str) -> None:
    """Validate that `op_type` is in the canonical registry. Logs a WARN
    line for unknown values rather than raising, so a typo in a new
    writer is operator-visible (Admin → Logs) without crashing the
    request — the row still lands in `history` so the audit trail is
    complete; only the i18n label / filter row are missing.
    """
    if not op_type or op_type in OP_TYPES:
        return
    print(
        f"[ops] warning — unknown op_type {op_type!r} written to history; "
        f"add to logic.ops.OP_TYPES + static/i18n/en.json:history.op_types"
    )


def write_admin_audit(
    conn,
    op_type: str,
    *,
    target_kind: str | None = None,
    target_name: str | None = None,
    target_id: str | None = None,
    actor: str = "ui",
    status: str = "success",
    message: str | None = None,
    error: str | None = None,
    events_dict: dict | None = None,
) -> None:
    """Synchronous audit-trail writer for admin write-actions that
    don't go through `new_op` / `Operation`. Used by the 18 admin
    write-routes covered by the CLAUDE.md "Admin write-actions
    audit-trail gap" rule (user / session / token / backup /
    config-backup / schedule / notification CRUD), AND by the
    Telegram listener for command audit rows — pre-helper the
    listener's `_audit_telegram` carried a parallel INSERT helper;
    now a thin call site here with `target_kind="telegram"` +
    structured `events_dict`.

    Mirrors the TOTP audit pattern (`api_admin_user_disable_totp` /
    `api_admin_user_force_totp_set`) — calls `assert_op_type` for
    typo-detection then INSERTs directly. Failures are swallowed +
    logged so a bad audit row can't roll back the actual admin
    action; the operator sees the failure in Admin → Logs.

    Two `events` JSON shapes supported: when ``events_dict`` is
    provided it's serialised directly (used by the Telegram listener
    to record structured command / args / sender fields); otherwise
    the auto-built single-line `[{ts, level, msg}]` shape is used
    when ``message`` is non-empty. Both forms produce a row body the
    History UI can expand.
    """
    import time as _time
    import json as _json
    assert_op_type(op_type)
    try:
        events_json: str | None
        if events_dict is not None:
            try:
                events_json = _json.dumps(events_dict, ensure_ascii=False)
            except (TypeError, ValueError):
                events_json = None
        elif message:
            events_json = _json.dumps([{
                "ts": _time.time(),
                "level": "error" if status == "error" else "info",
                "msg": message,
            }])
        else:
            events_json = None
        conn.execute(
            "INSERT INTO history "
            "(ts, op_type, target_kind, target_name, target_id, "
            " target_stack, status, duration, events, error, actor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _time.time(), op_type,
                target_kind, target_name, target_id,
                None, status, 0.0,
                events_json, error, actor or "ui",
            ),
        )
    except (_sqlite3.Error, TypeError, ValueError) as e:
        print(f"[ops] warning — failed to write admin audit row {op_type!r}: {e!r}")


# Single source of truth for notification event names + per-event default
# state. Mirrored into the DB by `api_get_settings` so the admin form has
# a value to render; consulted directly here so a fresh deploy (where the
# row doesn't exist yet) honours the same default the form would. Mismatch
# between this map and `notify()`'s default — every event was firing on first
# boot regardless of operator preference.
NOTIFY_EVENT_NAMES = (
    "stack_update_success",
    "stack_update_failure",
    "container_update_success",
    "container_update_failure",
    "container_restart_success",
    "container_restart_failure",
    "container_remove_success",
    "container_remove_failure",
    "service_restart_success",
    "service_restart_failure",
    "swarm_agent_restart_success",
    "swarm_agent_restart_failure",
    "swarm_agent_unhealthy",
    "swarm_agent_recovered",
    "prune_success",
    "prune_failure",
    "user_login",
    "host_paused",
    # Port-scan provider — fires when a scan reveals an open port not
    # in the previous scan AND not in `hosts_config[].services[]`.
    # Default OFF so a freshly-enabled scanner doesn't flood the
    # operator with first-run notifications for every existing port.
    "port_scan_new_port",
    # HTTP probe provider — fires when a per-host HTTP/TLS/DNS probe
    # transitions healthy → failing. Default OFF so a freshly-enabled
    # probe doesn't flood the operator with first-run failures.
    "http_probe_failure",
    # Per-service reachability probe — fires when a service-level
    # probe (one per curated `services[]` chip with `probe.enabled`)
    # transitions healthy → failing. Default OFF — service chips are
    # noisier than host chips and operators opt in selectively.
    "service_probe_failure",
    # TOTP audit-row INSERT failure — when an admin disables / force
    # -sets a user's TOTP enrolment but the audit-history row INSERT
    # fails (SQLite locked, FK violation, etc.). Defensive log +
    # continue is correct for the credential change itself, but the
    # operator looking at History sees no record of the change.
    # Fires WARNING severity so the operator knows the audit trail
    # is missing AND can manually note the change.
    "totp_audit_log_failed",
    # Drawer auto-fix — Portainer-API VXLAN overlay cleanup events
    # (success / failure variants). Fires when the operator clicks
    # the "Cleanup stale overlay network" button on a service's
    # task-error remediation panel.
    "overlay_cleanup_success",
    "overlay_cleanup_failure",
)
NOTIFY_EVENT_DEFAULTS: dict[str, bool] = {
    name: (False if name in ("user_login", "port_scan_new_port", "http_probe_failure", "service_probe_failure") else True)
    for name in NOTIFY_EVENT_NAMES
}

# Per-medium default state. Mirrors the per-event defaults map above so
# `api_get_settings` has a single source of truth + the dispatcher
# below can short-circuit a missing-row read to the same value the
# admin form would render. Both mediums default ON so existing deploys
# upgrade with both channels live; operators flip individually from
# Admin → Notifications.
NOTIFY_MEDIUM_NAMES = ("app", "apprise", "telegram")
# Telegram defaults OFF because it requires bot-token + chat-id config
# before it can fire — defaulting ON would spam start-up errors until
# the operator configures it. App + Apprise default ON to preserve
# legacy behaviour for upgrades from pre-Telegram deploys.
NOTIFY_MEDIUM_DEFAULTS = {
    "app": True,
    "apprise": True,
    "telegram": False,
}


# ---------------------------------------------------------------------
# Template engine — admin-editable per-event title/body templates with
# a curated placeholder whitelist. Resolution order at fire time:
# 1. DB setting `notify_template_<event>_<kind>` (kind in {title, body}).
# 2. NOTIFY_TEMPLATE_DEFAULTS[event][kind] — the hard-coded baseline that
#    mirrors the literals previously baked into each `_do_*` handler.
# 3. Empty string (defence in depth — should never hit if DEFAULTS is
#    complete; the audit gate logs a WARN if an event ships without one).
# Renders via `str.format_map(SafeDict(values))` so a mistyped placeholder
# (`{xxx}`) renders verbatim as `{xxx}` instead of raising
# KeyError — the operator sees the typo in the rendered output.
# ---------------------------------------------------------------------


class SafeDict(dict):
    """``str.format_map``-compatible dict that returns ``{key}`` literal
    for missing keys. Lets a typo in an admin-edited template render
    visibly in the output (e.g. ``"hi {xxx}"`` → ``"hi {xxx}"``)
    rather than raising ``KeyError`` mid-dispatch.
    """

    def __missing__(self, key: str) -> str:  # noqa: D401
        return "{" + key + "}"


# Curated placeholder whitelist. Keys are placeholder NAMES (without
# the surrounding braces); the value is a short documentation string
# the admin UI surfaces alongside the chip. The actual values get
# resolved per-event at render time by :func:`build_template_values`.
#
# Keep this list small and stable — every entry adds operator-facing
# surface area (i18n key, chip in the editor, sample data). When
# adding a new placeholder, register it here AND in
# :data:`NOTIFY_TEMPLATE_SAMPLES` AND in the admin-tab editor's chip
# strip. Prefer "structural" tokens (`{name}`, `{type}`) over
# operation-specific ones (`{stack_id}` would only apply to one op).
# Placeholders that USED to be valid but have been retired. The
# preview endpoint surfaces these in a separate `deprecated_placeholders`
# array (distinct from `unknown_placeholders`) so the editor SPA can
# render them inline with a warning marker AND a "deprecated since X.Y"
# tooltip — distinguishes "you typed something we never knew about"
# (probable typo, red) from "we used to support this but no longer do"
# (operator-action: rebind to the supported equivalent, amber). Empty
# by default — entries get added when a placeholder is retired through
# the standard deprecation cycle. Format: each entry maps the legacy
# token to the recommended replacement (or ``None`` if no direct
# replacement exists; the SPA renders "removed; no equivalent" in
# that case).
NOTIFY_DEPRECATED_PLACEHOLDERS: dict[str, str | None] = {
    # Example shape (no actual deprecations yet):
    # "old_token": "new_token",
    # "removed_token": None,
}

NOTIFY_PLACEHOLDERS = (
    "name",
    "type",
    "actor",
    "host",
    "time",
    # ``error`` is the legacy slot, populated only when severity ==
    # "error" so success / warning templates that bind {error} render
    # empty. ``message`` is the always-populated counterpart — caller's
    # body verbatim regardless of severity. Templates for warning-
    # severity events (e.g. swarm_agent_unhealthy) MUST bind {message}
    # rather than {error} or the body renders empty and operators get
    # an unfilled placeholder visible in the notification.
    "error",
    "message",
    "status",
    # ``url`` is populated by HTTP-probe-class events so the body can
    # name the failing URL inline. Empty for non-probe events.
    "url",
)

# Sample placeholder values for the live-preview pane in the admin
# editor. The shape mirrors what `build_template_values` produces at
# real render time. Kept short / readable so previews don't wrap.
NOTIFY_TEMPLATE_SAMPLES: dict = {
    "name": "example-stack",
    "type": "update_stack",
    "actor": "alice",
    "host": "swarm-mgr-01",
    "time": "2026-05-04T12:34:56Z",
    "error": "HTTP 500: connection refused",
    "message": "Probe ran, 3 nodes flagged unhealthy",
    "status": "success",
    "url": "https://example.com/health",
}

# Per-event hard-coded defaults. Each value mirrors the string the
# corresponding `_do_*` handler used to pass to `notify()` BEFORE the
# template feature shipped, so a deploy with no template settings
# behaves byte-for-byte identically to the legacy code.
#
# Keys:
# title — single-line headline (Apprise title, in-app row title).
# body  — multi-line body. Empty string is allowed; some events
#         (success-shape container ops) historically had no body.
#
# Audit invariant: every entry in ``NOTIFY_EVENT_NAMES`` MUST have a
# matching entry here. The :func:`audit_template_coverage` helper
# scans this map at boot + on settings save and logs a WARN line for
# any drift; the admin UI surfaces missing defaults under the
# top-level ``unbound_events`` array so the operator can SEE the gap
# without grepping logs.
NOTIFY_TEMPLATE_DEFAULTS: dict = {
    "stack_update_success": {
        "title": "✅ Stack updated: {name}",
        "body": "",  # body filled at fire time with duration; see do_update_stack
    },
    "stack_update_failure": {
        "title": "❌ Stack update failed: {name}",
        "body": "{error}",
    },
    "container_update_success": {
        "title": "✅ Container updated: {name}",
        "body": "",
    },
    "container_update_failure": {
        "title": "❌ Container update failed: {name}",
        "body": "{error}",
    },
    "container_restart_success": {
        "title": "🔄 Container restarted: {name}",
        "body": "",
    },
    "container_restart_failure": {
        "title": "❌ Container restart failed: {name}",
        "body": "{error}",
    },
    "container_remove_success": {
        "title": "🗑 Container removed: {name}",
        "body": "",
    },
    "container_remove_failure": {
        "title": "❌ Container remove failed: {name}",
        "body": "{error}",
    },
    "service_restart_success": {
        "title": "🔄 Service restarted: {name}",
        "body": "",
    },
    "service_restart_failure": {
        "title": "❌ Service restart failed: {name}",
        "body": "{error}",
    },
    "swarm_agent_restart_success": {
        "title": "🔄 Portainer agent restarted: {name}",
        "body": "Force-update applied; agents on every node will respawn "
                "and re-register with the manager.",
    },
    "swarm_agent_restart_failure": {
        "title": "❌ Portainer agent restart failed: {name}",
        "body": "{error}",
    },
    "swarm_agent_unhealthy": {
        "title": "⚠️ Swarm agent unhealthy: {name}",
        # ``{message}`` is always-populated (caller's body verbatim)
        # regardless of severity, vs ``{error}`` which is only set on
        # severity=="error". Warnings (this event's typical severity)
        # would render an empty body otherwise.
        "body": "{message}",
    },
    "swarm_agent_recovered": {
        "title": "✅ Swarm agent recovered: {name}",
        # Recovered events use {message} for the same reason as the
        # paired unhealthy event — severity is "success" so {error}
        # would resolve to empty.
        "body": "{message}",
    },
    "prune_success": {
        "title": "🧹 Prune complete on {name}",
        "body": "",  # body filled at fire time with reclaimed-bytes summary.
    },
    "prune_failure": {
        "title": "❌ Prune failed on {name}",
        "body": "{error}",
    },
    "user_login": {
        "title": "🔓 {actor} signed in",
        "body": "",
    },
    "host_paused": {
        "title": "⚠️ Host sampling paused: {name}",
        "body": "{error}",
    },
    # Port-scan provider — fires when a scan reveals an open port not
    # in the previous scan AND not in the host's curated services.
    # ``{name}`` resolves to host id; the body uses ``{message}`` so
    # the caller can supply a one-line description ("port 8080
    # (http-alt) is now listening on host01").
    "port_scan_new_port": {
        "title": "🔍 New open port on {name}",
        "body": "{message}",
    },
    # HTTP/TLS/DNS probe provider — fires on the healthy→failing
    # transition for a per-host probe. ``{name}`` resolves to the
    # host id; the body surfaces the failing URL + the probe error.
    "http_probe_failure": {
        "title": "🌐 HTTP probe failed: {name}",
        "body": "URL: {url}\nError: {error}",
    },
    # Per-service reachability probe — one chip per curated
    # `services[]` entry with `probe.enabled === true` on a host.
    # ``{name}`` resolves to the host id, ``{url}`` to the service's
    # URL (typically port:host or http(s) link), and ``{message}`` to
    # the specific service name (e.g. "Plex :32400").
    "service_probe_failure": {
        "title": "🔌 Service probe failed: {name}",
        "body": "Service: {message}\nURL: {url}\nError: {error}",
    },
    "totp_audit_log_failed": {
        "title": "TOTP audit-row missing for {name}",
        "body": "{message}",
    },
    "overlay_cleanup_success": {
        "title": "Stale overlay cleaned: {name}",
        "body": "{message}",
    },
    "overlay_cleanup_failure": {
        "title": "Overlay cleanup failed: {name}",
        "body": "{message}",
    },
}


def template_setting_keys(event: str) -> tuple[str, str]:
    """Return the `(title_key, body_key)` settings-table key pair for
    one event. Centralised so the resolver, the validator, and the
    audit gate all agree on the spelling.
    """
    return (
        notify_template_title_key(event),
        notify_template_body_key(event),
    )


def template_default(event: str, kind: str, locale: str = "en") -> str:
    """Return the default template for ``(event, kind)`` resolved
    against the operator's locale.

    ``kind`` is ``"title"`` or ``"body"``. Resolution order:

      1. ``static/i18n/<locale>.json`` → ``notifications.events.<event>.<kind>``
         via :mod:`logic.i18n`. Falls back to ``en`` when the locale
         doesn't have the key.
      2. Hard-coded :data:`NOTIFY_TEMPLATE_DEFAULTS` dict (legacy
         back-compat — if the i18n bundle is somehow missing the key,
         the Python literal still ships a sensible default so
         notifications never go blank).
      3. Empty string when the event isn't registered anywhere.

    The i18n bundle is the canonical source of truth post-migration;
    the dict is the safety net for missing-bundle / corrupt-load
    cases. New events MUST be added to BOTH the dict AND the en.json
    bundle (the audit gate will be extended to verify both).
    """
    # Try the i18n bundle first.
    try:
        from logic.i18n import tr as _tr
        i18n_key = f"notifications.events.{event}.{kind}"
        resolved = _tr(i18n_key, locale)
        # `tr` returns the key itself when missing — treat that as a
        # cache-miss so we fall through to the dict.
        if resolved and resolved != i18n_key:
            return resolved
    except (ImportError, KeyError, ValueError, TypeError) as e:
        print(f"[notify] i18n lookup failed for {event}.{kind}: {e}")
    # Legacy dict fallback.
    entry = NOTIFY_TEMPLATE_DEFAULTS.get(event)
    if not isinstance(entry, dict):
        return ""
    return entry.get(kind) or ""


def resolve_template(event: str, kind: str, locale: str = "en") -> str:
    """Resolve the live template for ``(event, kind, locale)``.

    Operator-set DB override wins when present + non-empty (verbatim
    — no i18n applied; operators want exact wording control).
    Otherwise falls back to the locale-aware
    :func:`template_default`.
    """
    title_key, body_key = template_setting_keys(event)
    db_key = title_key if kind == "title" else body_key
    raw = (get_setting(db_key) or "").strip()
    if raw:
        return raw
    return template_default(event, kind, locale)


def resolve_actor_locale(actor_username: Optional[str]) -> str:
    """Look up the actor's stored UI locale from
    ``users.ui_prefs.lang``. Falls back to ``"en"`` for the system /
    scheduler / unauthenticated path. Used by :func:`notify` to pick
    the right bundle for template resolution.
    """
    if not actor_username:
        return "en"
    try:
        import logic.auth as _auth
        from logic.db import db_conn as _db_conn
        with _db_conn() as c:
            row = c.execute(
                "SELECT id FROM users WHERE username = ?",
                (actor_username,),
            ).fetchone()
            if not row:
                return "en"
            profile = _auth.get_user_profile(c, int(row["id"]))
        prefs = (profile or {}).get("ui_prefs") or {}
        lang = (prefs.get("lang") or "").strip().lower()
        if lang:
            from logic.i18n import pick_locale as _pick
            return _pick(lang)
    except (_sqlite3.Error, ImportError, ValueError, TypeError, AttributeError):
        pass
    return "en"


def render_template(template: str, values: dict) -> str:
    """Render a template against ``values`` via ``str.format_map`` +
    :class:`SafeDict`. Missing placeholders render verbatim (``{key}``)
    so a typo doesn't drop the notification on the floor.
    """
    if not template:
        return ""
    try:
        return template.format_map(SafeDict(values))
    except (ValueError, IndexError):
        # `{` followed by garbage / unbalanced braces. Operator typo
        # surfaces verbatim as a fallback rather than masking the
        # whole notification.
        return template


def build_template_values(
    *,
    event: Optional[str],
    target_name: Optional[str],
    op_type: Optional[str],
    actor: Optional[str],
    host: Optional[str],
    error: Optional[str],
    status: Optional[str],
    when: Optional[float] = None,
    message: Optional[str] = None,
    actor_username: Optional[str] = None,
    url: Optional[str] = None,
) -> dict:
    """Build the placeholder->value dict consumed by
    :func:`render_template`. Every key in :data:`NOTIFY_PLACEHOLDERS`
    is populated (None-safe; missing values render as the empty
    string). ``error`` and ``message`` are truncated to 500 chars to
    match the legacy body-cap behaviour.

    ``{time}`` renders against ``actor_username``'s
    ``ui_prefs.datetime_format`` so notification body / title strings
    match the SPA's ``fmtDate`` output for that recipient. Falls back
    to the canonical default when ``actor_username`` is empty / the
    user has no custom preference. The preview endpoint passes its own
    static sample dict, so live render = per-user format, preview =
    stable sample.

    ``error`` is the legacy slot — only populated when severity is
    "error" by the caller (callers pre-fix passed ``""`` for success /
    warning). ``message`` is the always-populated counterpart for
    warning / success templates that need a non-empty body.
    """
    import datetime as _dt
    from logic.datetime_fmt import apply_datetime_format, get_user_datetime_format

    ts = when if when is not None else time.time()
    # Render against the actor's `ui_prefs.datetime_format` so the
    # notification `{time}` placeholder matches what they'd see in the
    # SPA via `fmtDate`. Empty / missing username falls through to
    # `DEFAULT_DATETIME_FORMAT` ("dd/MM/yyyy, HH:mm:ss").
    user_fmt = get_user_datetime_format(actor_username or "")
    rendered_time = apply_datetime_format(
        _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc),
        user_fmt,
    )
    err_str = (error or "")
    if len(err_str) > 500:
        err_str = err_str[:500]
    msg_str = (message or "")
    if len(msg_str) > 500:
        msg_str = msg_str[:500]
    return {
        "name": target_name or "",
        "type": op_type or event or "",
        "actor": actor or "system",
        "host": host or "",
        "time": rendered_time,
        "error": err_str,
        "message": msg_str,
        "status": status or "",
        "url": url or "",
    }


def _placeholder_tokens_in(template: str) -> set[str]:
    """Extract `{name}` placeholder tokens from a template string.

    Returns the set of token names (without the surrounding braces).
    Skips numeric placeholders (`{0}`, `{1}`) and Python-format-style
    field expressions (`{x.y}`, `{x[0]}`, `{x:>5}`) — those don't
    apply to the curated `NOTIFY_PLACEHOLDERS` whitelist and we don't
    want spurious WARN noise. Empty `{}` is also skipped.
    """
    if not template:
        return set()
    out: set[str] = set()
    i = 0
    n = len(template)
    while i < n:
        ch = template[i]
        if ch == "{":
            # Escaped `{{` — skip both chars.
            if i + 1 < n and template[i + 1] == "{":
                i += 2
                continue
            j = template.find("}", i + 1)
            if j == -1:
                break
            tok = template[i + 1:j]
            # Trim format spec / attribute access / index access — only
            # the bare name matters for whitelist validation.
            for sep in (":", ".", "["):
                k = tok.find(sep)
                if k != -1:
                    tok = tok[:k]
            tok = tok.strip()
            # Skip empty + numeric tokens.
            if tok and not tok.isdigit():
                out.add(tok)
            i = j + 1
            continue
        if ch == "}" and i + 1 < n and template[i + 1] == "}":
            # Escaped `}}` — skip both chars.
            i += 2
            continue
        i += 1
    return out


def audit_template_data() -> dict:
    """Pure audit — returns the drift report without logging.

    Used by `/api/admin/notify-templates` (called on every GET, so
    must NOT log). Boot-time path uses :func:`audit_template_and_log`
    instead.

    Checks:
      - Every ``NOTIFY_EVENT_NAMES`` entry has a default title in
        :data:`NOTIFY_TEMPLATE_DEFAULTS`.
      - Every default entry's keys are recognised event names (catches
        a stale `NOTIFY_TEMPLATE_DEFAULTS` row that survives a rename).
      - Every `{token}` referenced by a default template is in the
        :data:`NOTIFY_PLACEHOLDERS` whitelist (catches a typo'd
        `{actor}` → `{atcor}` at boot rather than at first
        notification fire).

    Returns ``{missing_defaults: [...], unknown_defaults: [...],
    unknown_placeholders: [{event, kind, token}, ...]}``.
    """
    registered = set(NOTIFY_EVENT_NAMES)
    have_defaults = set(NOTIFY_TEMPLATE_DEFAULTS.keys())
    missing = sorted(registered - have_defaults)
    unknown = sorted(have_defaults - registered)
    # Walk every default template body and flag any `{token}` that
    # isn't on the curated whitelist. Unknown tokens still render
    # verbatim via `SafeDict.__missing__` (no crash) but operators
    # rarely intend to ship a literal `{atcor}` in a notification.
    whitelist = set(NOTIFY_PLACEHOLDERS)
    unknown_placeholders: list[dict] = []
    for event, body_map in (NOTIFY_TEMPLATE_DEFAULTS or {}).items():
        if not isinstance(body_map, dict):
            continue
        for kind, template in body_map.items():
            if not isinstance(template, str):
                continue
            for tok in sorted(_placeholder_tokens_in(template)):
                if tok not in whitelist:
                    unknown_placeholders.append({
                        "event": event,
                        "kind": kind,
                        "token": tok,
                    })
    return {
        "missing_defaults": missing,
        "unknown_defaults": unknown,
        "unknown_placeholders": unknown_placeholders,
    }


def audit_template_and_log() -> dict:
    """Boot-only audit — runs :func:`audit_template_data` AND prints a
    WARN line for each kind of drift.

    Pre-fix the single ``audit_template_coverage`` helper logged on
    EVERY call, so a healthy GET path was silent but a drift deploy
    flooded the log on every Admin → Notifications visit. Splitting
    log-side effects into this helper keeps the boot trace
    informative without re-emitting the same lines per request.
    """
    result = audit_template_data()
    missing = result.get("missing_defaults") or []
    unknown = result.get("unknown_defaults") or []
    unknown_ph = result.get("unknown_placeholders") or []
    if missing:
        print(
            f"[notify] WARN — events registered without a default template: "
            f"{missing}"
        )
    if unknown:
        print(
            f"[notify] WARN — default templates for unregistered events: "
            f"{unknown}"
        )
    if unknown_ph:
        # Group by token for a tighter log. Operators care more about
        # "which placeholder is misspelled" than about the per-event
        # listing (that's available on the JSON payload for the
        # admin UI).
        seen: dict[str, list[str]] = {}
        for row in unknown_ph:
            seen.setdefault(row["token"], []).append(f"{row['event']}.{row['kind']}")
        for tok, sites in sorted(seen.items()):
            print(
                f"[notify] WARN — unknown placeholder {{{tok}}} referenced by "
                f"{len(sites)} default template(s): {sites[:5]}"
                + (f" + {len(sites) - 5} more" if len(sites) > 5 else "")
            )
    return result


# Backwards-compat alias — every caller in main.py now uses one of the
# two more-specific helpers above. Kept for any out-of-tree consumer
# that imported the original name; calls fall through to the data-only
# variant (silent) rather than re-flooding the log.
audit_template_coverage = audit_template_data

# Mapping from operation status hints to the four-level severity
# taxonomy used by the in-app store + log viewer. Kept narrow on
# purpose — every caller passes one of "info" / "success" / "error"
# / "warning" today; anything outside that set falls through to
# "info" so a typo doesn't leak into the DB.
_VALID_SEVERITIES = ("info", "warning", "error", "success")


def _coerce_severity(status: Optional[str]) -> str:
    s = (status or "info").strip().lower()
    if s in _VALID_SEVERITIES:
        return s
    # legacy / Apprise-side "failure" alias.
    if s in ("fail", "failure", "err", "danger"):
        return "error"
    if s in ("warn", "alert"):
        return "warning"
    if s == "ok":
        return "success"
    return "info"


def _human_bytes(n: int) -> str:
    """Format a byte count for operator-facing notification copy.

    Picks the largest unit that keeps the number readable (≥1 of that
    unit, < 1024 of it). Uses powers of 1024 (binary) since these are
    storage-side numbers; matches the convention already used by the
    Hosts view's disk / mem cards. Returns e.g. ``"61.1 MB"`` for
    64,049,314 bytes — the human-readable form of what was previously
    rendered as ``"64,049,314 B"`` in prune notifications.
    """
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        n /= 1024.0
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} EB"


class Operation:
    """In-memory record of one write operation. Carries the event log
    until completion, then `persist_history` flushes the durable row to
    the SQLite `history` table. Lives in the process-local `ops` dict
    until eviction (`MAX_OPS` cap, finished ops drop first)."""

    __slots__ = ("id", "op_type", "target_id", "target_name", "target_stack",
                 "started", "ended", "status", "events", "error", "actor")

    def __init__(self, op_type: str, target_id: str, target_name: str,
                 target_stack: Optional[str] = None, actor: str = "ui"):
        self.id = uuid.uuid4().hex[:12]
        self.op_type = op_type
        self.target_id = target_id
        self.target_name = target_name
        self.target_stack = target_stack
        self.started = time.time()
        self.ended: Optional[float] = None
        self.status = "running"
        self.events: list[dict] = []
        self.error: Optional[str] = None
        self.actor = actor

    def log(self, msg: str, level: str = "info"):
        """Append one event to the live op log + publish an `op:updated`
        SSE frame so the live panel updates in-place without a poll.

        The printed line's level-prefix is NEUTRALIZED for the
        persistent-log classifier (per CLAUDE.md "pick verbs
        carefully" — `_RE_OK` matches `\\bsuccess\\b`). Mapping:
        info → info, success → step (intra-op step succeeded; the
        OP'S overall completion still classifies via op.done →
        history row level), error → error (correctly triggers
        ERROR), warn → warning (correctly triggers WARN). The SUCCESS
        bucket is reserved for operator-visible state changes
        recorded at op-COMPLETION via `persist_history`, not for
        every internal-step log line within an op.
        """
        self.events.append({"ts": time.time(), "level": level, "msg": msg})
        # Map the in-band level token to a non-classifier-triggering
        # display token so a 'success'-level intra-op event doesn't
        # pollute the persistent log's SUCCESS bucket. Unknown
        # levels (caller's free-form string) fall through verbatim.
        _LEVEL_DISPLAY = {
            "info": "info",
            "success": "step",
            "error": "error",
            "warn": "warning",
            "warning": "warning",
        }
        _display = _LEVEL_DISPLAY.get(level, level)
        print(f"[op {self.id}] {_display}: {msg}")
        # SSE — publish a minimal delta. Full op shape is available
        # via /api/ops/{id} if the consumer wants it; the live panel
        # only needs id + status + last-event so it can update the
        # row in place without re-fetching the world.
        events.publish("op:updated", {
            "id": self.id, "op_type": self.op_type, "status": self.status,
            "target_name": self.target_name, "last_event": {
                "ts": time.time(), "level": level, "msg": msg,
            },
        })

    def done(self, status: str, error: Optional[str] = None):
        """Mark this op as finished. Publishes the terminal `op:completed`
        SSE frame; caller is responsible for `persist_history(op)` to
        flush the durable row."""
        self.status = status
        self.ended = time.time()
        self.error = error
        # SSE — terminal transition. Consumer correlates by id.
        events.publish("op:completed", {
            "id": self.id, "op_type": self.op_type, "status": status,
            "target_name": self.target_name, "error": error,
            "duration": (self.ended or time.time()) - self.started,
        })

    def to_dict(self):
        """Serialise this op to the dict shape consumed by `/api/ops`."""
        return {
            "id": self.id, "op_type": self.op_type, "target_id": self.target_id,
            "target_name": self.target_name, "target_stack": self.target_stack,
            "started": self.started, "ended": self.ended,
            "status": self.status, "events": self.events, "error": self.error,
            "duration": (self.ended or time.time()) - self.started,
            "actor": self.actor,
        }


ops: dict[str, Operation] = {}
ops_order: list[str] = []


def new_op(op_type: str, target_id: str, target_name: str,
           target_stack: Optional[str] = None, actor: str = "ui") -> Operation:
    """Construct a new :class:`Operation`, register it in the in-memory
    `ops` dict, publish the `op:created` SSE frame, and evict the
    oldest completed op when the cap is hit. Returns the Operation —
    callers stamp events via `op.log(...)` and finish with `op.done(...)
    + persist_history(op)`."""
    # Validate against the canonical registry — logs a WARN line when
    # `op_type` isn't recognised. Doesn't raise (so existing behaviour
    # is back-compat); the WARN surfaces in Admin → Logs so a new
    # writer with a typo is operator-visible.
    assert_op_type(op_type)
    op = Operation(op_type, target_id, target_name,
                   target_stack=target_stack, actor=actor)
    ops[op.id] = op
    ops_order.insert(0, op.id)
    # Cap the in-memory log. Completed ops are GC'd first; running ones
    # hang around regardless of position so /api/ops always shows them.
    while len(ops_order) > MAX_OPS:
        dead = ops_order.pop()
        if ops.get(dead) and ops[dead].status != "running":
            ops.pop(dead, None)
    # SSE — surface the new op so the live panel slides it in
    # immediately rather than waiting for the next 1.5s poll cycle.
    events.publish("op:created", {
        "id": op.id, "op_type": op.op_type, "status": op.status,
        "target_name": op.target_name, "target_stack": op.target_stack,
        "actor": op.actor, "started": op.started,
    })
    return op


def persist_history(op: Operation) -> None:
    """Write a finished op to the ``history`` table and bump the
    Prometheus ops counter. Called from every _do_* handler's
    finally-block so there's a single instrumentation point."""
    duration = (op.ended or time.time()) - op.started
    with db_conn() as c:
        cur = c.execute(
            "INSERT INTO history "
            "(ts,op_type,target_kind,target_name,target_id,target_stack,status,duration,events,error,actor) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (op.started, op.op_type, "op",
             op.target_name, op.target_id, op.target_stack,
             op.status, duration,
             json.dumps(op.events), op.error, op.actor),
        )
        history_id = cur.lastrowid
    try:
        metrics.OPS_TOTAL.labels(op_type=op.op_type, status=op.status).inc()
    except (ValueError, AttributeError) as e:
        print(f"[metrics] OPS_TOTAL inc failed: {e}")
    # SSE — fire AFTER the row commits so the SPA's prepend lands a
    # row that's already visible to /api/history.
    events.publish("history:appended", {
        "id": history_id, "ts": op.started, "op_type": op.op_type,
        "target_kind": "op",
        "target_name": op.target_name, "target_id": op.target_id,
        "target_stack": op.target_stack, "status": op.status,
        "duration": duration, "error": op.error, "actor": op.actor,
    })


# ---------------------------------------------------------------------
# Notification dispatcher — fired on success/failure of every _do_* op,
# the host_metrics_sampler auto-pause path, and login events. Resolves
# the per-event toggle, then fans out to every enabled MEDIUM. Each
# medium has its own enable flag in the DB (notify_medium_<name>); the
# admin form in Admin → Notifications drives both flags. App-medium
# writes a row + publishes notification:created over SSE; Apprise
# medium does the legacy HTTP POST.
# ---------------------------------------------------------------------


# noinspection PyUnusedLocal
async def _notify_medium_apprise(
    *, title: str, body: str, severity: str,
    event: Optional[str], actor_username: Optional[str],
    target_kind: Optional[str], target_id: Optional[str],
    metadata: Optional[dict],
) -> dict:
    """Existing fire-and-forget Apprise dispatcher, lifted from the
    original :func:`notify`. Returns a structured ``{ok, skipped, ...}``
    dict so the caller can log per-medium outcomes.

    ``target_kind`` / ``target_id`` / ``metadata`` parameters are
    intentionally unused here — they're part of the
    :data:`MediumSender` shape so the app + telegram mediums can
    consume them. The ``# noinspection PyUnusedLocal`` directive
    above silences PyCharm's unused-parameter warning for the whole
    function.
    """
    if (get_setting(Settings.APPRISE_ENABLED, "true") or "true").lower() != "true":
        # Master toggle keeps the legacy short-circuit semantics; the
        # operator might have wanted to keep the app medium live while
        # silencing Apprise without flipping the per-medium switch.
        print("[notify] apprise skipped — apprise disabled in Admin → Notifications")
        return {"ok": False, "skipped": "apprise_disabled"}
    url = get_setting(Settings.APPRISE_URL)
    if not url:
        print("[notify] apprise skipped — no apprise_url configured")
        return {"ok": False, "skipped": "no_url"}
    # Per-user routing override — mailto recipient lookup. The
    # per-event + per-user opt-out gates have already fired in the outer
    # dispatcher; here we only need the email lookup. Defensive try so a
    # DB blip on the user lookup doesn't tank the dispatch.
    user_email: Optional[str] = None
    if event and actor_username:
        try:
            from logic import auth as _auth
            with db_conn() as _c:
                _u = _auth.get_user_by_username(_c, actor_username)
                if _u and _u.id >= 0:
                    user_email = (getattr(_u, "email", "") or "").strip() or None
        except (_sqlite3.Error, ImportError, AttributeError, ValueError) as _e:
            print(f"[notify] apprise user-email lookup failed for '{actor_username}': {_e}")
    tag = get_setting(Settings.APPRISE_TAG)
    body = body or title  # Apprise rejects empty bodies.
    try:
        # Apprise piggy-backs on Portainer's `VERIFY_TLS` for HTTPS
        # verify — same canonical helper. Timeout is independent of
        # the Portainer-write-op tiers because Apprise is a fire-and-
        # forget notify channel, not a write op.
        async with portainer.write_client(timeout=15.0) as client:
            payload = {
                "title": title,
                "body": body,
                "type": (
                    "success" if severity == "success"
                    else "failure" if severity == "error"
                    else "warning" if severity == "warning"
                    else "info"
                ),
            }
            if tag:
                payload["tag"] = tag
            if user_email:
                payload["to"] = user_email
            r = await client.post(url, json=payload)
            if r.status_code >= 400:
                print(f"[notify] apprise FAILED {r.status_code} → {url} body={r.text[:200]}")
                return {"ok": False, "status": r.status_code, "body": r.text[:200]}
            print(f"[notify] apprise ok {r.status_code} → {url} tag={tag!r}")
            return {"ok": True, "status": r.status_code}
    except (httpx.HTTPError, OSError, ValueError) as e:
        print(f"[notify] apprise ERROR → {url}: {e}")
        return {"ok": False, "error": str(e)}


async def _notify_medium_app(
    *, title: str, body: str, severity: str,
    event: Optional[str], actor_username: Optional[str],
    target_kind: Optional[str], target_id: Optional[str],
    metadata: Optional[dict],
) -> dict:
    """In-app notification store medium. Synchronous SQLite INSERT into
    ``notifications`` + SSE publish ``notification:created`` so the
    avatar badge + Notifications page update without a poll round-trip.

    Body may be empty for events whose template defines title-only
    rendering (e.g. ``user_login``: title=``"🔓 {actor} signed in"``,
    body=``""``). The in-app store has no API constraint forcing a
    non-empty body, unlike the Apprise medium — leave empty bodies
    empty so the SPA's notifications panel doesn't render the title
    twice (once as the title, once as a duplicate body line). The
    Apprise medium keeps its ``body = body or title`` fallback because
    Apprise rejects empty bodies at the HTTP layer.
    """
    ts = int(time.time())
    md_json: Optional[str] = None
    if metadata is not None:
        try:
            md_json = json.dumps(metadata, ensure_ascii=False)[:8192]
        except (TypeError, ValueError) as e:
            print(f"[notify] app metadata not JSON-serialisable, dropping: {e}")
            md_json = None
    try:
        with db_conn() as c:
            cur = c.execute(
                "INSERT INTO notifications "
                "(ts, event, severity, title, body, actor, target_kind, target_id, metadata, read_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    ts, event or "", severity, title or "", body,
                    actor_username, target_kind, target_id, md_json,
                ),
            )
            new_id = cur.lastrowid
            unread_row = c.execute(
                "SELECT COUNT(*) AS n FROM notifications WHERE read_at IS NULL"
            ).fetchone()
            unread_count = int(unread_row["n"]) if unread_row else 0
    except (_sqlite3.Error, RuntimeError) as insert_err:
        print(f"[notify] app INSERT failed: {insert_err}")
        return {"ok": False, "error": str(insert_err)}
    payload = {
        "id": new_id,
        "ts": ts,
        "event": event or "",
        "severity": severity,
        "title": title or "",
        "body": body,
        "actor": actor_username,
        "target_kind": target_kind,
        "target_id": target_id,
        "unread_count": unread_count,
    }
    try:
        events.publish("notification:created", payload)
    except (RuntimeError, ValueError, TypeError) as publish_err:
        # SSE publish failures must not break the dispatch — the DB row
        # is the source of truth, the SPA's polling fallback will pick
        # it up on the next /api/notifications round-trip. Verb stays
        # off the ERROR-severity regex per convention.
        print(f"[notify] app SSE publish dropped: {publish_err}")
    print(f"[notify] app ok id={new_id} event={event!r} severity={severity}")
    return {"ok": True, "id": new_id, "unread_count": unread_count}


# Medium dispatcher map. Add a new medium by writing
# ``logic/notify_<medium>.py`` exposing an ``async def send(...)`` of
# the same shape and registering here. CLAUDE.md "Canonical extension
# pattern: add a notification medium" is the full contract.
MediumSender = Callable[..., Awaitable[dict]]


async def _notify_medium_telegram(**kwargs) -> dict:
    """Dispatcher entry for the Telegram medium. Lazy-imports the
    module so a deploy without `telegram_bot_token` configured doesn't
    pay the import cost on every notify() call.
    """
    from logic import notify_telegram as _tg
    return await _tg.send(**kwargs)


NOTIFY_MEDIUMS: dict[str, MediumSender] = {
    "app": _notify_medium_app,
    "apprise": _notify_medium_apprise,
    "telegram": _notify_medium_telegram,
}


def _is_medium_enabled(medium: str) -> bool:
    """Per-medium master switch lookup. Defaults from
    :data:`NOTIFY_MEDIUM_DEFAULTS` so a fresh deploy fires every medium
    until the operator opts out from Admin → Notifications.
    """
    default_on = NOTIFY_MEDIUM_DEFAULTS.get(medium, True)
    return get_setting_bool(notify_medium_key(medium), default=default_on)


# Public aliases for cross-module use. main.py's per-medium Test
# endpoint fires the Apprise dispatcher directly; the SPA's per-event
# resolved-map block consults `is_medium_enabled` to decide which
# columns to surface in Profile → Notifications.
notify_medium_apprise = _notify_medium_apprise
is_medium_enabled = _is_medium_enabled


def _resolve_notify_host_for_log(host_id: str) -> str:
    """Resolve a host id to its operator-recognisable address via
    the canonical chain — `address → ssh.fqdn → ssh.host → ""`.
    Used by the notify-skip log line so operators see WHICH actual
    host triggered the suppressed notification, not just the bare
    alias. Best-effort: returns the empty string on any lookup
    failure (the caller renders just the alias in that case).
    Lazy import on iter_curated_hosts to dodge the import-time
    cycle with logic.db. Matches the resolver shape used by
    host_metrics_sampler._resolve_target_for_log."""
    try:
        from logic.db import iter_curated_hosts as _iter
        for _h in _iter():
            if (_h.get("id") or "").strip() != host_id:
                continue
            _ssh = _h.get("ssh") if isinstance(_h.get("ssh"), dict) else {}
            return (
                (_h.get("address") or "").strip()
                or (_ssh.get("fqdn") or "").strip()
                or (_ssh.get("host") or "").strip()
                or ""
            )
    except Exception:  # noqa: BLE001
        pass
    return ""


async def notify(
    title: str, body: str, status: str = "info", *,
    event: Optional[str] = None,
    actor_username: Optional[str] = None,
    target_kind: Optional[str] = None,
    target_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Fire one notification through every enabled medium in parallel.

    Back-compat: every existing call site of ``notify(title, body, status,
    event=..., actor_username=...)`` still works unchanged. The new
    ``target_kind`` / ``target_id`` / ``metadata`` kwargs are optional
    and feed the in-app store's renderer (event icon, deep-link target).

    Resolution order:
      1. Per-event toggle (``notify_event_<event>``) — when the operator
         disabled this event in Admin → Notifications, short-circuit
         BEFORE any medium fires. ``event=None`` (test button, legacy
         callers) skips this gate so the test path always fires.
      2. Per-user opt-out (``user_notify_prefs``) — same legacy contract.
      3. Per-medium master switch (``notify_medium_<medium>``) — fan-out
         skips disabled mediums but other mediums still fire.

    Templates: for any registered event the resolver will substitute the
    admin-edited (or default) template for ``title`` / ``body``. When the
    template references placeholders that the call site supplied (the
    name / actor / host / error / status / time / type set), they're
    interpolated via :func:`render_template`. The legacy literals passed
    by the caller still feed the placeholder values (``body`` typically
    carries the error string for failure events; ``title`` carries the
    target's display name). When the operator has cleared the template
    AND the default is empty, the legacy literal falls through unchanged
    so the notification never goes silent.

    Mediums fire via ``asyncio.gather(return_exceptions=True)`` so a
    failure in one (Apprise host down, DB write race) doesn't drop the
    delivery on the others.
    """
    severity = _coerce_severity(status)
    # Per-event admin gate.
    if event:
        default_on = NOTIFY_EVENT_DEFAULTS.get(event, True)
        if not get_setting_bool(notify_event_key(event), default=default_on):
            # Include target context so operators can see WHICH host /
            # provider tried to fire — `'http_probe_failure' disabled
            # by operator` is ambiguous when 50 hosts are flapping.
            # Resolution order matches the placeholder priority used
            # by the template renderer below: metadata.host →
            # target_id → (none). The host field is then resolved via
            # the canonical address-fallback chain
            # (`address → ssh.fqdn → ssh.host → bare id`) so the log
            # surfaces the OPERATOR-RECOGNISABLE address, not the
            # short alias. Without this, `host='webserver'` is
            # ambiguous when the operator has 5 hosts named
            # `webserver*` with the alias as their bare id.
            _meta = metadata or {}
            _target_bits = []
            if isinstance(_meta, dict):
                _host = (_meta.get("host") or "").strip()
                if _host:
                    _resolved = _resolve_notify_host_for_log(_host)
                    if _resolved and _resolved != _host:
                        _target_bits.append(f"host={_host!r}(target={_resolved!r})")
                    else:
                        _target_bits.append(f"host={_host!r}")
                _provider = (_meta.get("provider") or "").strip()
                if _provider:
                    _target_bits.append(f"provider={_provider!r}")
            if target_id:
                _target_bits.append(f"target={(target_kind or '?')}:{target_id!r}")
            _ctx = (" (" + ", ".join(_target_bits) + ")") if _target_bits else ""
            print(f"[notify] skipped — event '{event}' disabled by operator{_ctx}")
            return
    # Template override. Pull the legacy literal `title` / `body` apart
    # into structured placeholder values so the renderer has something
    # to substitute. The caller's title carries the target name (every
    # _do_* handler builds it as `"<emoji> <kind>: {target_name}"`); the
    # body carries the error string on failure events. We don't mine
    # those further here — instead we feed the structured values from
    # the call's existing kwargs (metadata.host, target_id, actor).
    if event:
        meta = metadata or {}
        # `host` placeholder resolution priority:
        # 1. metadata["host"] (explicit operator-friendly hostname)
        # 2. target_id when target_kind == "host" (e.g. prune ops)
        # 3. metadata["provider"] is NOT a host — left out
        host_value: Optional[str] = None
        if isinstance(meta, dict):
            host_value = (meta.get("host") or meta.get("hostname") or "") or None
        if not host_value and target_kind == "host":
            host_value = target_id or None
        # `name` placeholder priority — target's display name is what
        # the caller already encoded into the title string. We pass the
        # raw target_id so templates can reach it; downstream the
        # default templates use {name} which we pre-fill from the
        # caller's title (extracted below).
        # Strip the leading emoji + ": " from the legacy title so {name}
        # carries just the target. Same regex shape as the Apprise
        # title parser (any non-alnum prefix, then optional space).
        legacy_target_name = title or ""
        # Split on the LITERAL ": " (with the trailing space — emoji-
        # prefix shape `"✅ Stack updated: foo"`). Bare ":" splits would
        # mangle target names that legitimately contain a colon
        # (e.g. an image-as-name like `redis:6.2`), producing
        # `"6.2"` instead of `"redis:6.2"`.
        if ": " in legacy_target_name:
            legacy_target_name = legacy_target_name.split(": ", 1)[1].strip()
        # Handlers that emit "🧹 Prune complete on web01" don't have a
        # colon — fall back to the trailing word(s). Keep the cheap
        # heuristic; templates that need exact target shaping should
        # bind {host} or just live with the raw caller string.
        # `error` placeholder — for failure events the body carries the
        # error message verbatim (legacy convention). For success events
        # the body is empty / a duration string; we still pass it
        # through so a custom template can use {error} as "supplemental
        # body text" if it wants.
        legacy_body = body or ""
        # `actor` placeholder priority — the caller's actor_username
        # (which is the SPA-authenticated user OR "scheduler"); falls
        # through to "system" for sampler-fired events.
        actor_value = actor_username or (
            (meta.get("actor") or "") if isinstance(meta, dict) else ""
        ) or None
        # `status` placeholder — derived from the severity. Failures
        # render as "error" (matches the in-app store + Apprise API).
        status_token = "success" if severity == "success" else (
            "error" if severity == "error" else (
                "warning" if severity == "warning" else "info"
            )
        )
        values = build_template_values(
            event=event,
            target_name=legacy_target_name,
            op_type=event,
            actor=actor_value,
            host=host_value,
            # Legacy `{error}` slot — populated only on severity=="error"
            # so success / warning templates that bind {error} render
            # empty (matches the pre-template-engine convention where
            # the body was the error message ONLY on failure).
            error=legacy_body if severity == "error" else "",
            # New `{message}` slot — caller's body verbatim regardless
            # of severity. Warning / informational templates that need
            # a non-empty body bind {message} instead of {error}.
            message=legacy_body,
            status=status_token,
            actor_username=actor_username,
            # `url` placeholder — populated by HTTP-probe-class events
            # via `meta["url"]`. Empty for non-probe events; the
            # SafeDict renderer leaves `{url}` verbatim if a template
            # binds it on a non-URL event (visible to the operator so
            # the typo / wrong-template situation is caught early).
            url=(meta.get("url") or "") if isinstance(meta, dict) else "",
        )
        # Resolve and render. Empty resolver output falls through to
        # the legacy literal — never go silent on missing template.
        # Locale picked from the actor's `ui_prefs.lang` so a non-en
        # operator firing an action receives the notification in
        # their UI locale (Apprise webhooks AND in-app store get the
        # SAME pre-resolved string — no SPA-side translation race).
        actor_locale = resolve_actor_locale(actor_username)
        rendered_title = render_template(resolve_template(event, "title", actor_locale), values)
        rendered_body = render_template(resolve_template(event, "body", actor_locale), values)
        if rendered_title:
            title = rendered_title
        if rendered_body:
            body = rendered_body
    # Per-user opt-out lookup happens ONCE here — the per-(event, medium)
    # gate is applied inside the medium fan-out below so a user can
    # route, say, success events to Apprise only and failures to In-app
    # only. Token / system actors (negative ids) skip the per-user
    # lookup so scheduler-fired notifications still land.
    user_event_pref: Optional[Union[bool, dict]] = None
    if event and actor_username:
        try:
            from logic import auth as _auth
            with db_conn() as _c:
                _u = _auth.get_user_by_username(_c, actor_username)
                if _u and _u.id >= 0:
                    prefs_map = _auth.get_user_notify_prefs(_c, _u.id) or {}
                    if event in prefs_map:
                        user_event_pref = prefs_map[event]
        except (_sqlite3.Error, ImportError, AttributeError, ValueError) as _e:
            # Defensive: never let a pref lookup failure break the
            # admin-gate decision. user_event_pref stays None ⇒ default
            # to "enabled across every medium" (legacy behaviour).
            print(f"[notify] user-pref lookup failed for '{actor_username}': {_e}")
    # Legacy bare-bool false short-circuits every medium — matches the
    # pre-per-medium behaviour for users who haven't migrated their
    # ui_prefs yet AND for events the user explicitly opted out of in
    # full via the SPA's Disable-all button (still stored as bare bool).
    # Defence in depth: a dict-shape pref with every medium explicitly
    # False is semantically equivalent to a bare-bool False — recognise
    # it here so the log output stays consistent ("opted out across
    # every medium" instead of N per-medium "skipped" lines + a "no
    # mediums enabled" trailer) and the per-medium fan-out below isn't
    # entered just to be entirely skipped. Empty dicts fall through to
    # the per-medium fan-out (every medium defaults to True there) —
    # they're "no explicit choice" rather than "explicit opt-out".
    if user_event_pref is False or (
        isinstance(user_event_pref, dict)
        and user_event_pref
        and not any(bool(v) for v in user_event_pref.values())
    ):
        print(
            f"[notify] skipped — user '{actor_username}' opted out of "
            f"'{event}' across every medium"
        )
        return
    # Build the per-medium dispatch list (skip disabled).
    senders: list[Awaitable[dict]] = []
    fired_mediums: list[str] = []
    for medium_name, sender in NOTIFY_MEDIUMS.items():
        if not _is_medium_enabled(medium_name):
            print(f"[notify] medium '{medium_name}' disabled — skipped")
            continue
        # Per-(event, medium) user gate. Three shapes to handle:
        # - None / not-in-map: default-on for every medium (legacy
        #   behaviour — fresh users with no per-event choice land here)
        # - bool True: enabled across every medium (legacy bare-bool)
        # - dict {medium: bool}: per-medium routing — missing key
        #   defaults to True (medium added after the user's last save
        #   should still fire by default; explicit opt-out is the only
        #   way to silence a medium). bool False already short-circuited
        #   above so we don't see it here.
        if isinstance(user_event_pref, dict):
            if not user_event_pref.get(medium_name, True):
                print(
                    f"[notify] medium '{medium_name}' skipped — user "
                    f"'{actor_username}' routed '{event}' away from "
                    f"this channel"
                )
                continue
        senders.append(sender(
            title=title, body=body, severity=severity,
            event=event, actor_username=actor_username,
            target_kind=target_kind, target_id=target_id,
            metadata=metadata,
        ))
        fired_mediums.append(medium_name)
    if not senders:
        print("[notify] no mediums enabled — every channel dropped")
        return
    results = await asyncio.gather(*senders, return_exceptions=True)
    for medium_name, result in zip(fired_mediums, results):
        if isinstance(result, Exception):
            # Verb avoids the ERROR-severity classifier regex per
            # CLAUDE.md — `dropped` reads as an outcome, not a failure.
            print(f"[notify] medium '{medium_name}' dropped: {result}")


async def notify_one_medium(
    medium: str,
    title: str,
    body: str,
    *,
    status: str = "info",
    actor_username: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Send a notification through ONE specific medium — bypasses the
    fan-out logic in :func:`notify`.

    Used by the AI palette's ``send_notification`` action so the operator
    can ask "send to telegram Hi" and have the message routed ONLY to
    Telegram even when both Apprise and Telegram are enabled. The
    per-medium master switch (``notify_medium_<medium>``) is STILL
    honoured — if the operator disabled the medium in Admin →
    Notifications, the send short-circuits with ``{ok: False,
    detail: "medium '<x>' is disabled"}``. Per-event / per-user gates
    DO NOT apply (this isn't an event-driven notification — it's an
    operator-typed message).

    Returns the medium sender's result dict
    (``{ok: bool, detail?: str, ...}``) so the caller can surface
    per-medium failure detail.
    """
    severity = _coerce_severity(status)
    sender = NOTIFY_MEDIUMS.get(medium)
    if sender is None:
        return {
            "ok": False,
            "detail": f"unknown medium '{medium}' — valid: "
                      f"{', '.join(sorted(NOTIFY_MEDIUMS.keys()))}",
        }
    if not _is_medium_enabled(medium):
        return {
            "ok": False,
            "detail": (f"medium '{medium}' is disabled — enable it in "
                       f"Admin → Notifications first"),
        }
    try:
        result = await sender(
            title=title, body=body, severity=severity,
            event=None,
            actor_username=actor_username,
            target_kind=None, target_id=None,
            metadata=metadata or {},
        )
    except Exception as e:  # noqa: BLE001
        # Same verb discipline as `notify` — "dropped" reads as outcome
        # not failure for the persistent-log severity classifier.
        print(f"[notify] one-medium '{medium}' dropped: {e}")
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    if isinstance(result, dict):
        return result
    return {"ok": True, "detail": ""}


# ----------------------------------------------------------------------------
# Continuation extracted to `logic.ops_extras` to keep this file under
# the line-count threshold. Star-import re-exports every public symbol
# so `from logic.ops import X` consumers keep working without
# any change at the call site.
# ----------------------------------------------------------------------------
from logic.ops_extras import *  # noqa: E402,F401,F403
