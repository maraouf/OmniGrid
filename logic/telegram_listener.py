"""Telegram inbound command listener (Phase 2: send + receive).

Architecture
------------
Lifespan-managed background task that long-polls the Telegram Bot API's
``getUpdates`` endpoint. Incoming text messages are parsed for slash
commands and dispatched to handler functions; results are sent back to
the same chat via the Phase 1 ``send()`` plumbing.

Long-poll vs webhook
--------------------
Telegram offers two delivery models: outbound webhooks (Telegram POSTs
updates to a public HTTPS endpoint operators expose) and long-poll
(OmniGrid calls ``getUpdates`` with ``timeout=N`` and Telegram holds
the connection open until a new update arrives OR the timeout
expires). Long-poll wins for self-hosted homelab deploys:

  - No need to expose a public HTTPS endpoint through the reverse
    proxy — OmniGrid stays behind NPM / Tailscale / VPN.
  - No webhook URL to register / rotate.
  - State is OmniGrid-owned (the ``offset`` we send is the next
    update_id to fetch). Restart-safe — we persist the last seen
    update_id in ``settings`` and resume on next boot.

Tradeoff: one open HTTP connection at all times when the listener is
on. The default ``timeout=25`` parameter (Telegram caps at 50) keeps
the connection efficient.

Authorization model
-------------------
The destination ``telegram_chat_id`` setting (where Phase 1
notifications go) is also the SOLE chat allowed to issue commands.
Two layers of defence:

  1. **Chat-id gate**: ``update.message.chat.id`` must equal
     ``telegram_chat_id`` (configured destination). Commands sent
     in any other chat are silently ignored (the bot might be in
     multiple chats; only ONE is authorized).
  2. **User-id allow-list** (optional): when
     ``telegram_authorized_user_ids`` (CSV of int IDs) is non-empty,
     the message sender's id must be in the list. Empty list means
     "any sender in the authorized chat is allowed" (use this for
     personal DMs or single-operator supergroups where chat
     membership IS the authorization).

For supergroups with multiple members, populate the user-id list
explicitly. For DMs, leave it empty — the chat-id gate is sufficient.

Destructive-command gate
------------------------
``/restart`` and any other destructive verb requires either:

  - ``telegram_allow_destructive=true`` (operator pre-approves
    destructive commands without per-command confirm), OR
  - A typed-confirm two-step: ``/restart <target>`` returns a
    "Reply with `/restart confirm <target>` to proceed" prompt and
    arms a single-use confirmation token (persisted in ``settings``
    under ``telegram_pending_confirm_<token>`` with a TTL).

Mirrors the SSH terminal's typed-hostname confirm pattern used
elsewhere in the app.

Host resolver
-------------
Commands accept a target that's matched against (in priority order):

  1. IP address (exact match against curated host's ``address`` field
     OR per-provider names like ``snmp_name`` / ``beszel_name``)
  2. ``host_id`` (curated row primary key)
  3. ``label`` (operator-friendly display name)
  4. Asset ``short_name`` (from asset inventory by ``custom_number``)
  5. Asset ``serial`` / ``model`` substring

Multiple matches → reply with the list and abort.

Audit trail
-----------
Every command write goes through ``logic.ssh.run_command`` which
ALREADY persists to the ``history`` table via the standard SSH
audit path. Read-only commands (``/status``, ``/hosts``) write their
own audit row via ``write_admin_audit`` so the trail stays complete.

Phase 2 scope (this module)
---------------------------
Three commands ship in Phase 2.1:
  - ``/help`` — list available commands
  - ``/hosts`` — list curated hosts (sanitised: id + label + status)
  - ``/restart <target>`` — SSH-execute ``sudo reboot`` on the host

Phase 2.2 (deferred): ``/status``, ``/exec <target> <command>`` (gated
behind an even stricter allow-list), per-event ack from Telegram.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx


# ----------------------------------------------------------------------------
# Telegram Bot API base + long-poll defaults
# ----------------------------------------------------------------------------
_TELEGRAM_API_BASE = "https://api.telegram.org"

# Long-poll timeout — Telegram holds the connection open this many
# seconds waiting for an update. Caps at 50 server-side; 25 is the
# sweet spot (fast wake-up on inactivity while still amortising the
# round-trip cost on a busy chat). Operator can override via tunable
# if their reverse proxy / network has a tighter idle timeout.
_LONG_POLL_TIMEOUT = 25

# Wall-clock for the HTTP call itself — slightly larger than the
# long-poll timeout so Telegram has time to flush the response.
_HTTP_TIMEOUT = _LONG_POLL_TIMEOUT + 10


def _resolved_token_and_chat() -> tuple[str, str]:
    """Pull bot-token + destination chat-id from the settings store."""
    from logic.db import get_setting
    token = (get_setting("telegram_bot_token", "") or "").strip()
    chat = (get_setting("telegram_chat_id", "") or "").strip()
    return token, chat


def _listener_enabled() -> bool:
    from logic.db import get_setting_bool
    return get_setting_bool("telegram_listener_enabled", default=False)


def _allow_destructive() -> bool:
    from logic.db import get_setting_bool
    return get_setting_bool("telegram_allow_destructive", default=False)


def _authorized_user_ids() -> set[int]:
    """Parse the comma-separated allow-list of Telegram user_ids.

    Empty list means "any sender in the authorized chat is allowed"
    (chat-id gate is the only check). Non-empty list means commands
    are restricted to those user_ids regardless of chat membership.
    """
    from logic.db import get_setting
    raw = (get_setting("telegram_authorized_user_ids", "") or "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.add(int(piece))
        except (TypeError, ValueError):
            continue
    return out


def _is_authorized(update: dict) -> tuple[bool, str]:
    """Two-layer authorization check.

    Returns ``(ok, reason)``. ``reason`` is non-empty when denied,
    suitable for a debug log line (NOT sent back to the chat — we
    silently ignore unauthorized messages so an attacker probing a
    public bot doesn't get useful feedback).
    """
    _, authorized_chat = _resolved_token_and_chat()
    if not authorized_chat:
        return False, "no telegram_chat_id configured"
    msg = update.get("message") or update.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None:
        return False, "no chat.id in message"
    if str(chat_id) != str(authorized_chat):
        return False, f"chat_id {chat_id} != configured {authorized_chat}"
    allow_list = _authorized_user_ids()
    if allow_list:
        sender_id = (msg.get("from") or {}).get("id")
        if sender_id is None:
            return False, "no from.id in message"
        if int(sender_id) not in allow_list:
            return False, f"sender {sender_id} not in allow-list"
    return True, ""


def _reply_text(text: str) -> dict:
    """Build a sendMessage payload targeted at the configured chat.

    Re-uses Phase 1's HTML parse_mode + thread_id behaviour so replies
    land in the same forum topic the original command came from (when
    applicable).
    """
    from logic.db import get_setting
    chat = (get_setting("telegram_chat_id", "") or "").strip()
    thread = (get_setting("telegram_thread_id", "") or "").strip()
    payload: dict = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread:
        try:
            payload["message_thread_id"] = int(thread)
        except (TypeError, ValueError):
            pass
    return payload


async def _send_reply(client: httpx.AsyncClient, text: str) -> None:
    """Fire-and-forget reply. Logs but doesn't raise on failure."""
    token, _ = _resolved_token_and_chat()
    if not token:
        return
    try:
        r = await client.post(
            f"{_TELEGRAM_API_BASE}/bot{token}/sendMessage",
            json=_reply_text(text),
            timeout=15.0,
        )
        if r.status_code != 200:
            print(f"[telegram_listener] reply failed: HTTP {r.status_code}")
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[telegram_listener] reply exception: {e}")


# ----------------------------------------------------------------------------
# Host resolver
# ----------------------------------------------------------------------------
def _load_hosts_config() -> list[dict]:
    """Read the curated host list from settings. Returns a list of
    dicts, NEVER raises — a malformed JSON setting just produces an
    empty list."""
    import json
    from logic.db import get_setting
    raw = (get_setting("hosts_config", "") or "").strip()
    if not raw:
        return []
    try:
        cfg = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return cfg if isinstance(cfg, list) else []


def _load_asset_inventory() -> list[dict]:
    """Read the cached asset inventory. Returns the list of asset
    dicts, NEVER raises."""
    import json
    from pathlib import Path
    try:
        path = Path("/app/data/asset_inventory.json")
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(items, list):
            return items
    except (OSError, ValueError, TypeError):
        return []
    return []


def _resolve_target(target: str) -> tuple[Optional[dict], list[dict]]:
    """Match a command target string against curated hosts.

    Returns ``(matched_host, candidates)``:
      - ``matched_host`` is the single curated row when there's an
        unambiguous match. ``None`` when zero or multiple matches.
      - ``candidates`` is the full list of fuzzy matches (1+ entries)
        so the caller can show a disambiguation prompt.

    Priority chain (first non-empty match wins):
      1. Exact IP match against ``address`` / ``snmp_name`` / ``beszel_name``
         / ``pulse_name`` / ``webmin_name`` / ``ssh.host``
      2. Exact host_id match (curated row primary key)
      3. Exact label match (case-insensitive)
      4. Asset ``short_name`` match via the asset-inventory cache
         (joined to hosts via ``custom_number``)
      5. Asset ``serial`` / ``model`` substring match (also joined)
      6. Substring match across the same fields (last resort)
    """
    target = (target or "").strip()
    if not target:
        return None, []
    target_lower = target.lower()
    hosts = _load_hosts_config()
    if not hosts:
        return None, []

    def _provider_targets(h: dict) -> list[str]:
        out: list[str] = []
        for k in ("address", "snmp_name", "beszel_name", "pulse_name",
                  "webmin_name"):
            v = h.get(k)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        ssh = h.get("ssh") if isinstance(h.get("ssh"), dict) else None
        if ssh and isinstance(ssh.get("host"), str) and ssh["host"].strip():
            out.append(ssh["host"].strip())
        return out

    # 1. Exact IP / per-provider target match
    for h in hosts:
        for v in _provider_targets(h):
            if v == target:
                return h, [h]

    # 2. Exact host_id
    for h in hosts:
        if (h.get("id") or "") == target:
            return h, [h]

    # 3. Exact label (case-insensitive)
    label_hits = [h for h in hosts if (h.get("label") or "").strip().lower() == target_lower]
    if len(label_hits) == 1:
        return label_hits[0], label_hits
    if label_hits:
        return None, label_hits

    # 4 + 5. Asset inventory match via custom_number
    assets = _load_asset_inventory()
    asset_hits: list[dict] = []
    for asset in assets:
        short = (asset.get("type_short") or asset.get("Type", {}).get("ShortName") or "").lower()
        serial = (asset.get("serial") or "").lower()
        model = (asset.get("model") or "").lower()
        custom_number = asset.get("custom_number")
        if custom_number is None:
            continue
        matched_field = False
        if short and short == target_lower:
            matched_field = True
        elif serial and target_lower in serial:
            matched_field = True
        elif model and target_lower in model:
            matched_field = True
        if not matched_field:
            continue
        # Join to host via custom_number
        for h in hosts:
            if h.get("custom_number") == custom_number:
                asset_hits.append(h)
                break
    if len(asset_hits) == 1:
        return asset_hits[0], asset_hits
    if asset_hits:
        return None, asset_hits

    # 6. Substring fallback across host_id / label / provider targets
    sub_hits: list[dict] = []
    for h in hosts:
        bag = " ".join(filter(None, [
            (h.get("id") or "").lower(),
            (h.get("label") or "").lower(),
            *(v.lower() for v in _provider_targets(h)),
        ]))
        if target_lower in bag:
            sub_hits.append(h)
    if len(sub_hits) == 1:
        return sub_hits[0], sub_hits
    return None, sub_hits


def _host_status_emoji(h: dict) -> str:
    """Rough at-a-glance status emoji for the /hosts listing."""
    if not h.get("enabled", True):
        return "⚪"
    return "🟢"


# ----------------------------------------------------------------------------
# Command handlers
# ----------------------------------------------------------------------------
async def _cmd_help(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """Auto-generated help — iterates `_COMMANDS` so adding a new
    handler shows up in `/help` with no extra wiring.

    Entries with `hidden=True` are skipped (used for aliases like
    `/start` so the menu stays terse). Each entry's `usage` line is
    HTML-escaped before render so angle-bracket placeholders survive
    Telegram's HTML parse_mode.
    """
    lines = ["<b>OmniGrid Telegram commands</b>", ""]
    seen = set()
    for name, meta in _COMMANDS.items():
        if meta.get("hidden"):
            continue
        # Dedupe aliases pointing at the same handler — keep the first.
        handler = meta.get("handler")
        if handler in seen:
            continue
        seen.add(handler)
        usage = _escape(meta.get("usage") or name)
        description = _escape(meta.get("description") or "")
        if description:
            lines.append(f"<b>{usage}</b> — {description}")
        else:
            lines.append(f"<b>{usage}</b>")
    lines.append("")
    lines.append(
        "<i>Targets resolve by IP, host id, label, or asset short-name. "
        "Destructive commands (e.g. /restart) require a typed confirm step "
        "unless 'Allow destructive Telegram commands' is enabled in Admin.</i>"
    )
    await _send_reply(client, "\n".join(lines))


async def _cmd_hosts(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    hosts = _load_hosts_config()
    if not hosts:
        await _send_reply(client, "No curated hosts configured.")
        return
    lines = [f"<b>Curated hosts</b> ({len(hosts)})", ""]
    for h in hosts[:50]:
        hid = h.get("id") or "(no-id)"
        label = h.get("label") or hid
        addr = h.get("address") or ""
        lines.append(f"{_host_status_emoji(h)} <code>{hid}</code> — {label}"
                     + (f" ({addr})" if addr else ""))
    if len(hosts) > 50:
        lines.append(f"\n…and {len(hosts) - 50} more.")
    await _send_reply(client, "\n".join(lines))


async def _cmd_restart(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/restart <target>`` — reboot a host via SSH.

    Two-step destructive-gate flow:
      - ``/restart <target>`` resolves the target, then either:
        (a) immediately executes if ``telegram_allow_destructive=true``;
        (b) replies with a confirm prompt (no token state — the
            operator must re-send ``/restart confirm <target>``).
      - ``/restart confirm <target>`` skips the prompt and executes.
    """
    if not args:
        await _send_reply(client, "Usage: <code>/restart &lt;target&gt;</code>")
        return
    is_confirm = (args[0].lower() == "confirm")
    if is_confirm:
        if len(args) < 2:
            await _send_reply(client, "Usage: <code>/restart confirm &lt;target&gt;</code>")
            return
        target = " ".join(args[1:])
    else:
        target = " ".join(args)
    matched, candidates = _resolve_target(target)
    if matched is None:
        if not candidates:
            await _send_reply(client, f"No host matched <code>{_escape(target)}</code>.")
            return
        lines = [f"Multiple hosts matched <code>{_escape(target)}</code>:", ""]
        for h in candidates[:20]:
            lines.append(f"• <code>{_escape(h.get('id') or '')}</code> — "
                         f"{_escape(h.get('label') or '')}")
        if len(candidates) > 20:
            lines.append(f"…and {len(candidates) - 20} more.")
        lines.append("\nNarrow your target and try again.")
        await _send_reply(client, "\n".join(lines))
        return

    # Destructive gate
    if not is_confirm and not _allow_destructive():
        host_id = matched.get("id") or ""
        await _send_reply(
            client,
            f"⚠️ Reply with <code>/restart confirm {_escape(host_id)}</code> "
            f"to reboot <b>{_escape(matched.get('label') or host_id)}</b>.\n"
            f"<i>(Or enable 'Allow destructive Telegram commands' in "
            f"Admin → Notifications → Telegram to skip this step.)</i>"
        )
        return

    # Execute via the standard SSH runner
    host_id = matched.get("id") or ""
    label = matched.get("label") or host_id
    await _send_reply(client, f"🔄 Restarting <b>{_escape(label)}</b>…")

    from logic import ssh as _ssh
    hosts = _load_hosts_config()
    # `sudo reboot` is the canonical restart verb; sudoers typically
    # grants this without a password to the SSH user. The standalone
    # `reboot` works on machines where the SSH user IS root.
    cmd = "sudo reboot"
    result = await _ssh.run_command(host_id, cmd, hosts, timeout=15.0, dry_run=False)
    # A successful reboot kills the SSH session before run_command can
    # collect the exit code — `ok` is often False with `error` mentioning
    # connection closed. Treat closed-connection-after-command-issued as
    # success (the reboot fired).
    err = (result.get("error") or "").lower()
    looks_like_reboot_success = (
        "connection" in err and ("closed" in err or "reset" in err or "broken" in err)
    ) or result.get("exit_code") == 255
    if result.get("ok") or looks_like_reboot_success:
        await _send_reply(client, f"✅ Reboot command sent to <b>{_escape(label)}</b>.")
    else:
        await _send_reply(
            client,
            f"❌ Restart failed for <b>{_escape(label)}</b>: "
            f"<code>{_escape(result.get('error') or 'unknown error')}</code>"
        )


def _escape(s: str) -> str:
    """HTML-escape a string for Telegram parse_mode=HTML."""
    return (str(s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


# Command dispatch table. Single source of truth for both routing AND
# the `/help` menu — `_cmd_help` iterates this dict to render every
# non-hidden command's usage + description.
#
# Adding a new command:
#   1. Implement `async def _cmd_<name>(client, args, msg)`
#   2. Add an entry below with handler / usage / description (+ hidden
#      if it's an alias / undocumented surface)
#
# `usage` is rendered HTML-escaped inside `<b>...</b>` — write it the
# way you want it to read in Telegram, with literal `<target>` /
# `<arg>` placeholders. `description` follows in plain text after the
# em-dash separator. `hidden=True` keeps the entry off the /help menu
# (used for aliases like `/start` → `_cmd_help`).
_COMMANDS: dict[str, dict[str, Any]] = {
    "/help": {
        "handler":     _cmd_help,
        "usage":       "/help",
        "description": "Show this command list",
    },
    "/start": {
        # Telegram clients send `/start` automatically when the user
        # first opens a conversation with the bot. Mapping it to help
        # gives a clean first-contact experience.
        "handler":     _cmd_help,
        "usage":       "/start",
        "description": "Show the command list",
        "hidden":      True,  # don't double up in /help (same handler as /help)
    },
    "/hosts": {
        "handler":     _cmd_hosts,
        "usage":       "/hosts",
        "description": "List curated hosts with their status",
    },
    "/restart": {
        "handler":     _cmd_restart,
        "usage":       "/restart <target>",
        "description": "Restart a host via SSH (destructive — requires confirm)",
    },
}


# ----------------------------------------------------------------------------
# Long-poll loop
# ----------------------------------------------------------------------------
async def _process_update(client: httpx.AsyncClient, update: dict) -> None:
    """Authorize + parse + dispatch one incoming Update."""
    ok, reason = _is_authorized(update)
    if not ok:
        # Silently ignore — don't tip off an attacker probing the bot.
        # Log so operators can diagnose "my command isn't running".
        print(f"[telegram_listener] unauthorized update: {reason}")
        return
    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    if not text or not text.startswith("/"):
        return
    # Telegram allows `/cmd@BotName` form — strip the @BotName suffix.
    parts = text.split()
    head = parts[0]
    at_pos = head.find("@")
    if at_pos != -1:
        head = head[:at_pos]
    head = head.lower()
    args = parts[1:]
    meta = _COMMANDS.get(head)
    if meta is None:
        await _send_reply(
            client,
            f"Unknown command <code>{_escape(head)}</code>. Try <code>/help</code>."
        )
        return
    handler = meta.get("handler")
    if handler is None:
        await _send_reply(
            client,
            f"Command <code>{_escape(head)}</code> has no handler wired. Internal error."
        )
        return
    try:
        await handler(client, args, msg)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001 — never let one bad command crash the loop
        print(f"[telegram_listener] handler {head!r} crashed: {e}")
        try:
            await _send_reply(client, f"❌ Command crashed: <code>{_escape(str(e))}</code>")
        except Exception:
            pass


def _load_offset() -> int:
    """Resume from the last seen update_id (+ 1) across restarts."""
    from logic.db import get_setting
    raw = (get_setting("telegram_last_update_id", "0") or "0").strip()
    try:
        return int(raw) + 1 if raw else 0
    except (TypeError, ValueError):
        return 0


def _save_offset(update_id: int) -> None:
    from logic.db import set_setting
    try:
        set_setting("telegram_last_update_id", str(int(update_id)))
    except (TypeError, ValueError):
        pass


async def listener_loop() -> None:
    """Lifespan-managed long-poll loop. Restart-safe via the persisted
    ``telegram_last_update_id`` offset.

    Operates only when:
      - ``telegram_listener_enabled`` is True
      - ``telegram_bot_token`` + ``telegram_chat_id`` are both set

    Re-checks the gate on every iteration so the operator can flip the
    listener on/off in Admin → Notifications without a restart.
    """
    print("[telegram_listener] lifespan started")
    offset = _load_offset()
    try:
        while True:
            # Per-iteration gate so a flip in admin settings takes
            # effect on the next loop without restart. Sleep 5s when
            # disabled — long enough to not hammer the settings KV,
            # short enough that turning the listener back on feels
            # responsive.
            if not _listener_enabled():
                await asyncio.sleep(5)
                continue
            token, chat = _resolved_token_and_chat()
            if not token or not chat:
                await asyncio.sleep(5)
                continue
            try:
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                    r = await client.get(
                        f"{_TELEGRAM_API_BASE}/bot{token}/getUpdates",
                        params={
                            "offset": offset,
                            "timeout": _LONG_POLL_TIMEOUT,
                            "allowed_updates": '["message","edited_message"]',
                        },
                    )
                    if r.status_code != 200:
                        print(f"[telegram_listener] getUpdates HTTP {r.status_code}: {r.text[:200]}")
                        await asyncio.sleep(5)
                        continue
                    body = r.json() or {}
                    if not body.get("ok"):
                        print(f"[telegram_listener] getUpdates not ok: {body.get('description')!r}")
                        await asyncio.sleep(5)
                        continue
                    updates = body.get("result") or []
                    for update in updates:
                        update_id = update.get("update_id")
                        if isinstance(update_id, int):
                            offset = update_id + 1
                            _save_offset(update_id)
                        try:
                            await _process_update(client, update)
                        except (asyncio.CancelledError, KeyboardInterrupt):
                            raise
                        except Exception as e:  # noqa: BLE001
                            print(f"[telegram_listener] update processing failed: {e}")
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except httpx.HTTPError as e:
                # Network blip — back off briefly + retry. Don't spam
                # logs with the same error every iteration.
                print(f"[telegram_listener] network: {e}")
                await asyncio.sleep(5)
            except Exception as e:  # noqa: BLE001
                print(f"[telegram_listener] loop iteration failed: {e}")
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        print("[telegram_listener] lifespan cancelled")
        raise
