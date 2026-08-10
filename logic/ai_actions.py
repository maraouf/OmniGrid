"""Single source of truth for every AI-invocable ACTION.

An action used to be declared in several unrelated places — the parser
whitelist (``logic.ai.ALLOWED_PALETTE_ACTIONS``), the host-targeting set in the
palette route, and the SPA's alias map + descriptor list — and nothing kept them
aligned. The drift was real and measurable: 48 of the 86 entries in the SPA's
alias map could never fire, because the parser filters against the whitelist
BEFORE the SPA ever sees the reply, so an alias registered only in JS was dead
on arrival. Several were added in good faith by someone who reasonably assumed
registering the alias made it work.

Declaring an action HERE now feeds every consumer: the parser whitelist, the
host-targeting set, the SPA's alias -> descriptor mapping and its destructive
flags. Adding an alias is one edit and it works on every surface.

What lives here is the machine-readable CONTRACT. The prose that teaches the
model WHEN to use an action stays in ``PALETTE_SYSTEM_PROMPT`` — that text is
what the model actually reads, and generating it from here is the next step
(the same step that will emit native tool-call JSON schemas, which is why the
metadata is structured rather than free text).

Fields:
  ``id``              canonical id the model should emit; what the parser accepts.
  ``spa``             the SPA descriptor id (kebab) this resolves to.
  ``aliases``         other ids the model may plausibly emit for the same thing.
                      These are now LIVE — accepted by the parser and mapped to
                      the same descriptor.
  ``destructive``     mirrors the SPA descriptor. Gates the confirm flow, so it
                      MUST match the descriptor or the confirm is skipped.
  ``host_targeting``  action operates on one host, so the palette route resolves
                      ``ACTION_HOSTS`` / recovers a target from the query for it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AiAction:
    """One AI-invocable action. See the module docstring for field meanings."""

    id: str
    spa: str
    aliases: tuple[str, ...] = ()
    destructive: bool = False
    host_targeting: bool = False


# Derived from the previously-separate declaration sites, so the registry is a
# faithful snapshot of shipped behaviour; `scripts/audit_ai_actions.py` proves
# the derived sets still match what each consumer expects.
ACTIONS: tuple[AiAction, ...] = (
    AiAction("ai_memory_create", "ai-memory-create", ("remember_this",)),
    AiAction("ai_memory_delete", "ai-memory-delete", ("forget_memory",), destructive=True),
    AiAction("backup_create", "backup-create", ("create_backup", "snapshot_backup")),
    AiAction("bounce_interface", "bounce-interface",
             ("bounce_port", "restart_interface", "flap_interface", "bounce_switch_port"),
             destructive=True, host_targeting=True),
    AiAction("cleanup_stopped", "cleanup-stopped", ("cleanup", "prune_stopped"), destructive=True),
    AiAction("discover_apps", "discover-apps"),
    AiAction("hosts_bulk_pause", "hosts-bulk-pause", ("bulk_pause_hosts", "pause_hosts"), destructive=True),
    AiAction("hosts_bulk_resume", "hosts-bulk-resume", ("bulk_resume_hosts", "resume_hosts", "unpause_hosts")),
    AiAction("sign_out", "logout", ("logoff", "sign_off"), destructive=True),
    AiAction("mark_all_notifications_read", "mark-all-notifications-read", ("clear_notifications", "notifications_clear_all")),
    AiAction("open_notifications", "open-notifications"),
    AiAction("osupdate_host", "osupdate-host", ("os_update_host", "patch_host", "update_host", "upgrade_host"), destructive=True, host_targeting=True),
    AiAction("prune_node", "prune-node", ("prune_docker",), destructive=True),
    AiAction("reboot_host", "reboot-host", ("reboot_switch", "restart_host"), destructive=True, host_targeting=True),
    AiAction("refresh", "refresh-now"),
    AiAction("reload", "reload-spa"),
    AiAction("remove_container", "remove-container", ("delete_container",), destructive=True),
    AiAction("restart_container", "restart-container", ("bounce_container",), destructive=True),
    AiAction("restart_service", "restart-service", ("bounce_service",), destructive=True),
    AiAction("resume_host_sampling", "resume-host-sampling", ("resume_host", "resume_provider", "resume_sampling", "unpause_host"), host_targeting=True),
    AiAction("retag_image", "retag-image", ("change_tag", "pin_to_tag", "switch_tag", "track_tag"), destructive=True),
    AiAction("run_app_skill", "run-app-skill"),
    AiAction("scan_ports", "scan-ports", (), host_targeting=True),
    AiAction("schedule_create", "schedule-create", ("add_schedule", "create_schedule", "new_schedule")),
    AiAction("schedule_delete", "schedule-delete", ("delete_schedule", "remove_schedule"), destructive=True),
    AiAction("schedule_run_now", "schedule-run-now", ("fire_schedule", "run_schedule_now")),
    AiAction("schedule_update", "schedule-update", ("change_schedule", "edit_schedule", "modify_schedule", "update_schedule")),
    AiAction("send_notification", "send-notification", ("message_channel", "notify_channel", "send_apprise", "send_telegram"), destructive=True),
    AiAction("show_hotkeys", "show-hotkeys"),
    AiAction("test_apprise", "test-apprise"),
    AiAction("test_asset_inventory", "test-asset-inventory"),
    AiAction("test_beszel", "test-beszel"),
    AiAction("test_oidc", "test-oidc"),
    AiAction("test_ping", "test-ping"),
    AiAction("test_portainer", "test-portainer"),
    AiAction("test_pulse", "test-pulse"),
    AiAction("test_snmp", "test-snmp"),
    AiAction("test_webmin", "test-webmin"),
    AiAction("theme_auto", "theme-auto"),
    AiAction("theme_dark", "theme-dark"),
    AiAction("theme_light", "theme-light"),
    AiAction("update_all_updatable", "update-all-updatable", ("update_all", "update_all_stacks", "update_stacks", "upgrade_all"), destructive=True),
    AiAction("update_container", "update-container", ("recreate_container",), destructive=True),
    AiAction("update_stack", "update-stack", (), destructive=True),
)


def allowed_action_ids() -> frozenset[str]:
    """Every id the parser accepts — canonical ids AND their aliases."""
    out: set[str] = set()
    for a in ACTIONS:
        out.add(a.id)
        out.update(a.aliases)
    return frozenset(out)


def host_targeting_ids() -> frozenset[str]:
    """Ids (incl. aliases) whose action operates on a single named host."""
    out: set[str] = set()
    for a in ACTIONS:
        if a.host_targeting:
            out.add(a.id)
            out.update(a.aliases)
    return frozenset(out)


def destructive_ids() -> frozenset[str]:
    """Ids (incl. aliases) whose action must go through the confirm flow."""
    out: set[str] = set()
    for a in ACTIONS:
        if a.destructive:
            out.add(a.id)
            out.update(a.aliases)
    return frozenset(out)


def alias_to_spa() -> dict[str, str]:
    """``{ai_id: spa_descriptor_id}`` for every canonical id and alias. Shipped
    to the SPA via ``/api/me`` so the browser stops carrying its own copy."""
    out: dict[str, str] = {}
    for a in ACTIONS:
        out[a.id] = a.spa
        for al in a.aliases:
            out[al] = a.spa
    return out


def action_for(action_id: str) -> AiAction | None:
    """Resolve a canonical id OR an alias to its action."""
    needle = (action_id or "").strip().lower()
    if not needle:
        return None
    for a in ACTIONS:
        if needle == a.id or needle in a.aliases:
            return a
    return None


def client_config_payload() -> dict:
    """Compact registry view for ``/api/me``'s ``client_config`` — the SPA reads
    the alias map + destructive set from here instead of hardcoding them."""
    return {
        "alias_to_spa": alias_to_spa(),
        "destructive": sorted(destructive_ids()),
    }
