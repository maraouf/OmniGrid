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
# noinspection SpellCheckingInspection
from __future__ import annotations

import asyncio
import sqlite3
from collections import OrderedDict as _OrderedDict
# `contextvars` is a Python 3.7+ stdlib module — no requirements.txt entry
# needed; PyCharm's package-not-in-requirements lint mis-identifies it as
# a third-party package, hence the inline suppression directive.
# noinspection PyPackageRequirements
from contextvars import ContextVar
from typing import Any, Optional

import httpx

from logic.cooldown import Cooldown as _Cooldown
from logic.settings_keys import Settings

# ----------------------------------------------------------------------------
# Telegram Bot API base + long-poll defaults — operator-tunable
# ----------------------------------------------------------------------------
# Defaults below are the fallback values when the corresponding tunable
# / setting is blank in the DB. The actual values flow through the
# helpers in `_telegram_api_base()` / `_telegram_long_poll_timeout()` /
# `_telegram_http_timeout()` (per-call reads so a UI edit takes effect
# on the next iteration without restart).
_TELEGRAM_API_BASE_DEFAULT = "https://api.telegram.org"


def _telegram_api_base() -> str:
    """Resolve the Telegram Bot API base URL from the
    "telegram_api_base" setting (Admin → Notifications → Telegram).
    Falls back to the official upstream when blank."""
    from logic.db import get_setting
    raw = (get_setting(Settings.TELEGRAM_API_BASE) or "").strip()
    if not raw:
        return _TELEGRAM_API_BASE_DEFAULT
    return raw.rstrip("/")


def _telegram_long_poll_timeout() -> int:
    """Long-poll timeout (seconds) read via
    ``tuning_telegram_long_poll_timeout_seconds``. Range-clamped to
    1..50 (Telegram server-side cap) by the resolver."""
    from logic.tuning import Tunable, tuning_int
    return tuning_int(Tunable.TELEGRAM_LONG_POLL_TIMEOUT_SECONDS)


def _telegram_http_timeout() -> int:
    """Outer HTTP wall-clock (seconds) for the listener's `getUpdates`
    call. Read via ``tuning_telegram_http_timeout_seconds``. Should be
    larger than the long-poll timeout so Telegram has time to flush
    the response after a long-poll wake-up."""
    from logic.tuning import Tunable, tuning_int
    return tuning_int(Tunable.TELEGRAM_HTTP_TIMEOUT_SECONDS)


def _resolved_bot_token() -> str:
    """Pull the bot-token from settings (Telegram bots are 1:1 with tokens
    — chats are per-message, never per-token). Single-purpose helper for
    callers that only need the token; legacy aliases keep the previous
    tuple shape working without forcing a fleet-wide rewrite."""
    from logic.db import get_setting
    return (get_setting(Settings.TELEGRAM_BOT_TOKEN) or "").strip()


def _resolved_primary_chat() -> str:
    """First CSV entry of ``telegram_chat_id`` (the "primary" destination).
    Empty when no chat is configured. Outbound fan-out should use
    :func:`_outbound_chat_ids`; this returns ONE chat for legacy single-
    chat callers + the listener-loop bootstrap gate."""
    from logic.db import get_setting
    chats = _parse_chat_id_csv(get_setting(Settings.TELEGRAM_CHAT_ID) or "")
    return chats[0] if chats else ""


def _resolved_token_and_chat() -> tuple[str, str]:
    """Back-compat alias — returns ``(token, primary_chat)`` for callers
    that still want the tuple shape. New call sites should prefer
    :func:`_resolved_bot_token` (token-only) or :func:`_resolved_primary_chat`
    (chat-only) directly. The "primary" semantic is documented on
    :func:`_resolved_primary_chat`."""
    return _resolved_bot_token(), _resolved_primary_chat()


def _csv_pieces(raw: str):
    """Yield non-empty trimmed pieces from a comma-separated string.
    Shared by the chat-id parser + the authorized-user-ids parser so
    both walk identical CSV-shape input without duplicating the
    split / strip / skip-empty loop. ``raw=None`` is treated as ``""``."""
    for piece in (raw or "").split(","):
        s = piece.strip()
        if s:
            yield s


def _parse_chat_id_csv(raw: str) -> list[str]:
    """Parse the `telegram_chat_id` setting into an ordered list of chat
    IDs. Accepts the legacy single-value shape (no comma — returns a
    one-element list) AND the CSV multi-chat shape (`-100123,456` —
    returns `["-100123", "456"]`). Whitespace + empty segments are
    dropped silently; order is preserved so the FIRST entry stays the
    canonical "primary" destination for `_resolved_token_and_chat`."""
    out: list[str] = []
    seen: set[str] = set()
    for s in _csv_pieces(raw):
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _authorized_chat_ids() -> set[str]:
    """Set of every authorised inbound chat ID (set membership matches
    against `update.message.chat.id` in :func:`_is_authorized`). The
    legacy single-value setting auto-promotes to a one-element set so
    existing deploys upgrade silently."""
    from logic.db import get_setting
    return set(_parse_chat_id_csv(get_setting(Settings.TELEGRAM_CHAT_ID) or ""))


def _outbound_chat_ids() -> list[str]:
    """Ordered list of every configured outbound destination. Outbound
    notification fan-out (Apprise medium, scheduler-fired alerts)
    iterates this list so a deploy with `chat_id = "<group>,<dm>"`
    delivers to both surfaces. Order matches the operator's CSV input
    so the "primary" destination stays predictable."""
    from logic.db import get_setting
    return _parse_chat_id_csv(get_setting(Settings.TELEGRAM_CHAT_ID) or "")


# Inbound-chat context — set by `_process_update` BEFORE dispatching to
# a command handler, so the reply / edit / typing paths can route back to
# the SAME chat the command came from (rather than always sending to the
# primary configured chat). ContextVar over an explicit arg threads
# cleanly through asyncio + avoids touching ~50 `_send_reply(client, text)`
# call sites. Default empty string = "no inbound chat known" (e.g.
# scheduler-fired outbound paths where we want the primary destination).
# `ContextVar` is imported at the top of the file (stdlib, no requirements.txt entry).
_inbound_chat_id: ContextVar[str] = ContextVar("_inbound_chat_id", default="")


def _reply_destination() -> str:
    """Resolve the chat ID a reply should target. Inbound context (set
    by `_process_update` when handling a command) wins; outbound paths
    (scheduler, lifespan startup notifications) fall through to the
    primary CSV entry from :func:`_resolved_primary_chat`."""
    inbound = _inbound_chat_id.get()
    if inbound:
        return inbound
    return _resolved_primary_chat()


def _listener_enabled() -> bool:
    from logic.db import get_setting_bool
    return get_setting_bool(Settings.TELEGRAM_LISTENER_ENABLED)


def _allow_destructive() -> bool:
    from logic.db import get_setting_bool
    return get_setting_bool(Settings.TELEGRAM_ALLOW_DESTRUCTIVE)


def _destructive_confirm_text(confirm_command: str, action_phrase: str) -> str:
    """Build the canonical "Reply with /CMD confirm ... to ACTION"
    prompt + admin-pointer hint that gates destructive commands.

    Shared by ``/restart`` / ``/cleanup`` / ``/update`` so future
    operator-flagged phrasing tweaks (i18n, copy edits, link-target
    changes) land in ONE place instead of three near-duplicates.

    Args:
        confirm_command: The full confirm-form including the leading
            slash AND the `confirm` keyword AND any target args, pre-
            escaped — e.g. ``/restart confirm web01.example.com`` or
            ``/cleanup confirm``. The helper wraps it in
            ``<code>...</code>``.
        action_phrase: Descriptive phrase that completes "to <phrase>"
            — e.g. ``reboot <b>web01</b>`` (HTML allowed) or
            ``remove all 5 container(s)`` or ``proceed``.

    Returns a two-line HTML string ready to drop into ``_send_reply``.
    """
    return (
        f"⚠️ Reply with <code>{confirm_command}</code> to {action_phrase}.\n"
        f"<i>(Or enable 'Allow destructive Telegram commands' in "
        f"Admin → Notifications → Telegram to skip this step.)</i>"
    )


def _authorized_user_ids() -> set[int]:
    """Parse the comma-separated allow-list of Telegram user_ids.

    Empty list means "any sender in the authorized chat is allowed"
    (chat-id gate is the only check). Non-empty list means commands
    are restricted to those user_ids regardless of chat membership.
    """
    from logic.db import get_setting
    raw = (get_setting(Settings.TELEGRAM_AUTHORIZED_USER_IDS) or "")
    out: set[int] = set()
    for piece in _csv_pieces(raw):
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
    authorized_chats = _authorized_chat_ids()
    if not authorized_chats:
        return False, "no telegram_chat_id configured"
    msg = update.get("message") or update.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None:
        return False, "no chat.id in message"
    # CSV-aware membership check — `telegram_chat_id` accepts a
    # comma-separated list so a single bot can serve both a group AND
    # 1:1 DMs from operators. Single-value legacy setting parses as a
    # one-element set so deploys upgrade silently.
    if str(chat_id) not in authorized_chats:
        return False, f"chat_id {chat_id} not in configured set {sorted(authorized_chats)}"
    allow_list = _authorized_user_ids()
    if allow_list:
        sender_id = (msg.get("from") or {}).get("id")
        if sender_id is None:
            return False, "no from.id in message"
        try:
            sender_int = int(sender_id)  # type: ignore[arg-type]  # narrowed via try/except
        except (TypeError, ValueError):
            return False, f"sender_id {sender_id!r} not coercible to int"
        if sender_int not in allow_list:
            return False, f"sender {sender_id} not in allow-list"
    return True, ""


async def _telegram_post(
    client: httpx.AsyncClient,
    method: str,
    payload: dict,
    *,
    timeout: float = 15.0,
    log_label: str = "request",
    silent_failure: bool = False,
) -> tuple[bool, dict]:
    """Shared POST skeleton for Telegram Bot API calls. Pre-helper
    `_send_reply` / `_edit_message` / `_send_chat_action` each
    carried the same ~20-line shape "build URL → POST → log on
    non-200 → re-raise cancellation → swallow + log on broad
    exception" inline; that's ~60 LOC of duplicate plumbing.

    Per-method specifics (payload composition, body parsing, return
    type translation) stay at the call site — only the POST + error
    plumbing is shared. Returns ``(ok, body)`` where ``ok`` is True
    on HTTP 200; ``body`` is the parsed JSON dict (empty on parse
    failure or non-200). Cancellation propagates per the cancellation-
    semantics rule; every other exception is logged + swallowed.

    ``silent_failure=True`` suppresses the non-200 log line (used by
    decorative calls like the typing indicator where the operator
    doesn't care about a hiccup)."""
    token = _resolved_bot_token()
    if not token:
        return False, {}
    try:
        r = await client.post(
            f"{_telegram_api_base()}/bot{token}/{method}",
            json=payload, timeout=timeout,
        )
        if r.status_code != 200:
            if not silent_failure:
                print(f"[telegram_listener] {log_label} failed: HTTP {r.status_code}: {r.text[:200]}")
            return False, {}
        try:
            return True, (r.json() or {})
        except (ValueError, TypeError):
            return True, {}
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    # noinspection PyBroadException
    except Exception as e:  # noqa: BLE001
        if not silent_failure:
            print(f"[telegram_listener] {log_label} exception: {e}")
        return False, {}


def _reply_text(text: str) -> dict:
    """Build a sendMessage payload targeted at the chat the inbound
    command came from (per the `_inbound_chat_id` ContextVar set by
    `_process_update`) — falls back to the primary CSV entry for
    outbound paths with no inbound context (scheduler / lifespan
    startup notifications).

    Re-uses Phase 1's HTML parse_mode + thread_id behaviour so replies
    land in the same forum topic the original command came from (when
    applicable).
    """
    from logic.db import get_setting
    chat = _reply_destination()
    thread = (get_setting(Settings.TELEGRAM_THREAD_ID) or "").strip()
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


async def _send_reply(client: httpx.AsyncClient, text: str) -> Optional[int]:
    """Send a reply to the configured chat. Returns the Telegram
    ``message_id`` on success (so callers can later edit the message
    via :func:`_edit_message`); returns None on any failure. Existing
    fire-and-forget callers discard the return value silently."""
    ok, body = await _telegram_post(
        client, "sendMessage", _reply_text(text),
        log_label="reply",
    )
    if not ok:
        return None
    try:
        msg_id = ((body.get("result") or {}).get("message_id"))
        return int(msg_id) if msg_id is not None else None  # type: ignore[arg-type]
    except (ValueError, TypeError, AttributeError):
        return None


async def _send_chat_action(
    client: httpx.AsyncClient, action: str = "typing",
) -> None:
    """Fire the native Telegram "Bot is typing…" indicator. Lasts
    about 5 seconds on the client. For longer-running ops we ALSO
    send a placeholder reply that gets edited in place — the typing
    indicator is just immediate feedback while the placeholder is in
    flight. Fire-and-forget; never raises."""
    from logic.db import get_setting
    chat = _reply_destination()
    thread = (get_setting(Settings.TELEGRAM_THREAD_ID) or "").strip()
    payload: dict = {"chat_id": chat, "action": action}
    if thread:
        try:
            payload["message_thread_id"] = int(thread)
        except (TypeError, ValueError):
            pass
    # `silent_failure=True` — typing indicator is decorative; a
    # transient hiccup must not surface in Admin → Logs.
    await _telegram_post(
        client, "sendChatAction", payload,
        timeout=5.0, log_label="chat_action", silent_failure=True,
    )


async def _edit_message(
    client: httpx.AsyncClient, message_id: int, text: str,
) -> bool:
    """Edit a previously-sent placeholder message in place. Used by
    the AI reply path to replace "🤖 Thinking…" with the final answer
    so the operator sees the response land in the same bubble. Caller
    is expected to have pre-truncated to 4096 chars. Returns True on
    success; on failure the caller falls back to a fresh ``_send_reply``."""
    if not message_id:
        return False
    # `_reply_destination()` reads the per-handler ContextVar so an
    # edit lands in the SAME chat as the original placeholder reply
    # (the placeholder was sent with the same contextvar — they must
    # agree, otherwise Telegram returns "message to edit not found").
    chat = _reply_destination()
    payload: dict = {
        "chat_id": chat,
        "message_id": int(message_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    ok, _body = await _telegram_post(
        client, "editMessageText", payload,
        timeout=10.0, log_label="edit",
    )
    return ok


async def _replace_placeholder(
    client: httpx.AsyncClient,
    placeholder_id: Optional[int],
    body: str,
) -> None:
    """Replace a "🤖 Thinking…" placeholder with the final ``body``.

    If the edit succeeds: the operator sees the answer land in the
    same bubble (preferred UX — chat-app convention).

    If the edit FAILS (Telegram rejected the HTML, message was
    deleted, network blip): we send the body as a fresh reply AND
    stamp the placeholder with "(edit failed — see reply below)" so
    the operator can tell which bubble is current. Pre-fix the fresh
    reply would land below an UN-stamped "Thinking…" placeholder,
    which an operator could mistake for "still in progress".

    No placeholder → just send a fresh reply.
    """
    if placeholder_id is None:
        await _send_reply(client, body)
        return
    ok = await _edit_message(client, placeholder_id, body)
    if ok:
        return
    # Stamp the placeholder first so the about-to-arrive fresh reply
    # has unambiguous context. Best-effort — a second edit failure
    # falls through to the original "two bubbles" problem, but the
    # body still lands so the operator gets the answer.
    await _edit_message(
        client, placeholder_id,
        "<i>(edit failed — see reply below)</i>",
    )
    await _send_reply(client, body)


# ----------------------------------------------------------------------------
# Host resolver
# ----------------------------------------------------------------------------
def _load_hosts_config() -> list[dict]:
    """Read the curated host list from settings. Returns a list of
    dicts, NEVER raises — a malformed JSON setting just produces an
    empty list. Thin wrapper over :func:`logic.db.load_settings_json`
    so the parse / isinstance / fallback dance lives in one place."""
    from logic.db import load_settings_json
    return load_settings_json(Settings.HOSTS_CONFIG, default=[], expected_type=list)


# In-process cache for the asset-inventory file read. `_resolve_target`
# hits the priority-4 asset-join on every `/host` / `/restart` / etc.
# match-by-asset-short-name; the underlying JSON cache file is operator-
# manually-refreshed, so reading it on every command was wasteful disk
# IO. Cache layered on file mtime + 60s TTL so an operator-driven
# refresh (POST /api/asset-inventory/refresh) AND a slow drift both
# trigger a re-read on the next hit. Cleared via _asset_inventory_cache[0]
# reset if needed.
_ASSET_INVENTORY_CACHE_TTL_S = 60.0
_asset_inventory_cache: list = [0.0, 0.0, []]  # [last_check_ts, file_mtime_ns, items]


def _load_asset_inventory() -> list[dict]:
    """Read the cached asset inventory. Returns the list of asset
    dicts, NEVER raises.

    Two-layer cache:
      1. Short-TTL (60s) cache holds the parsed `items` list.
      2. File mtime check on every TTL expiry — if mtime hasn't
         changed since the last read, skip re-parsing the JSON.

    Operator-driven refresh (`POST /api/asset-inventory/refresh`)
    rewrites the file atomically via `.tmp + os.replace`, so the
    mtime advances and the cache invalidates on the next hit.
    """
    import json
    import time as _time
    from pathlib import Path
    now = _time.time()
    last_check_ts, cached_mtime_ns, cached_items = _asset_inventory_cache
    if (now - last_check_ts) < _ASSET_INVENTORY_CACHE_TTL_S and cached_items:
        return cached_items  # type: ignore[return-value]
    try:
        path = Path("/app/data/asset_inventory.json")
        if not path.exists():
            _asset_inventory_cache[0] = now
            _asset_inventory_cache[2] = []
            return []
        # mtime gate — if the file hasn't changed since the last
        # successful read, skip the JSON parse entirely and just
        # bump the TTL anchor.
        try:
            stat = path.stat()
            current_mtime_ns = int(stat.st_mtime_ns)
        except OSError:
            current_mtime_ns = 0
        if current_mtime_ns == cached_mtime_ns and cached_items:
            _asset_inventory_cache[0] = now
            return cached_items  # type: ignore[return-value]
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items") if isinstance(data, dict) else None
        result: list[dict] = items if isinstance(items, list) else []
        _asset_inventory_cache[0] = now
        _asset_inventory_cache[1] = current_mtime_ns
        _asset_inventory_cache[2] = result
        return result
    except (OSError, ValueError, TypeError):
        # Defensive: keep the previous cache on transient read errors
        # so a brief disk hiccup doesn't blank `/host` resolution.
        _asset_inventory_cache[0] = now
        return cached_items if isinstance(cached_items, list) else []  # type: ignore[return-value]


def _rank_candidates(candidates: list[dict]) -> list[dict]:
    """Sort an ambiguous-match list so the most actionable rows surface
    first. Operator-flagged: prior order was whatever insertion order
    ``hosts_config`` happened to have, which made disambiguation feel
    arbitrary.

    Ranking key (lower is better, stable secondary by host_id):
      0. enabled hosts before disabled ones (operator usually wants
         the live host when typing an ambiguous match)
      1. last-known status — up < paused < down / unknown — when the
         curated row carries a recent probe outcome
      2. host_id ascending for stable tie-break
    """

    def _status_weight(row: dict) -> int:
        status = (row.get("_last_status") or row.get("status") or "").lower()
        if status == "up":
            return 0
        if status == "paused":
            return 1
        if status in ("down", "unknown", "unconfigured"):
            return 2
        return 3  # no status info available

    return sorted(
        candidates,
        key=lambda h: (
            0 if h.get("enabled", True) else 1,
            _status_weight(h),
            h.get("id") or "",
        ),
    )


async def _reply_no_match_or_candidates(
    client: httpx.AsyncClient,
    target: str,
    matched: Optional[dict],
    candidates: list[dict],
) -> bool:
    """Render the disambiguation / no-match reply for `/host`-style
    target resolution. Returns ``True`` when the caller should bail
    (reply sent), ``False`` when ``matched`` is a single host and the
    caller should proceed.

    Shared by ``_cmd_host`` / ``_cmd_restart`` (and any future command
    that takes a target arg) so the disambiguation copy + cap + footer
    are defined once. Pre-helper drift here required two-site edits
    (e.g. when the cap of 20 changed, or the "Narrow your target"
    copy was tweaked).

    The candidates list is already ranked via ``_rank_candidates`` at
    the ``_resolve_target`` exit, so the first 20 are the most
    actionable rows.
    """
    if matched is not None:
        return False
    if not candidates:
        await _send_reply(
            client, f"No host matched <code>{_escape(target)}</code>."
        )
        return True
    lines = [f"Multiple hosts matched <code>{_escape(target)}</code>:", ""]
    for h in candidates[:20]:
        lines.append(
            f"• <code>{_escape(h.get('id') or '')}</code> — "
            f"{_escape(h.get('label') or '')}"
        )
    if len(candidates) > 20:
        lines.append(f"…and {len(candidates) - 20} more.")
    lines.append("\nNarrow your target and try again.")
    await _send_reply(client, "\n".join(lines))
    return True


# noinspection SpellCheckingInspection
def _resolve_target(target: str) -> tuple[Optional[dict], list[dict]]:
    """Match a command target string against curated hosts.

    Returns ``(matched_host, candidates)``:
      - ``matched_host`` is the single curated row when there's an
        unambiguous match. ``None`` when zero or multiple matches.
      - ``candidates`` is the full list of fuzzy matches (1+ entries)
        so the caller can show a disambiguation prompt — sorted via
        :func:`_rank_candidates` so enabled / up hosts surface first
        regardless of ``hosts_config`` insertion order.

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

    def _provider_targets(host_row: dict) -> list[str]:
        out: list[str] = []
        for field_name in ("address", "snmp_name", "beszel_name", "pulse_name",
                           "webmin_name"):
            field_val = host_row.get(field_name)
            if isinstance(field_val, str) and field_val.strip():
                out.append(field_val.strip())
        _raw_ssh = host_row.get("ssh")
        ssh: dict = _raw_ssh if isinstance(_raw_ssh, dict) else {}
        ssh_host = ssh.get("host")
        if isinstance(ssh_host, str) and ssh_host.strip():
            out.append(ssh_host.strip())
        return out

    # 1. Exact IP / per-provider target match. Gather ALL matches before
    # returning so a duplicate address (two hosts sharing one IP via
    # typo OR via overlapping snmp_name / beszel_name aliases) doesn't
    # silently pick the first one — same disambiguation contract as
    # the label-path below.
    provider_hits: list[dict] = []
    seen_ids: set[str] = set()
    for h in hosts:
        for v in _provider_targets(h):
            if v == target:
                hid = (h.get("id") or "").strip()
                if hid in seen_ids:
                    continue
                seen_ids.add(hid)
                provider_hits.append(h)
                break
    if len(provider_hits) == 1:
        return provider_hits[0], provider_hits
    if provider_hits:
        return None, _rank_candidates(provider_hits)

    # 2. Exact host_id
    for h in hosts:
        if (h.get("id") or "") == target:
            return h, [h]

    # 3. Exact label (case-insensitive)
    label_hits = [h for h in hosts if (h.get("label") or "").strip().lower() == target_lower]
    if len(label_hits) == 1:
        return label_hits[0], label_hits
    if label_hits:
        return None, _rank_candidates(label_hits)

    # 4 + 5. Asset inventory match via custom_number
    assets = _load_asset_inventory()
    asset_hits: list[dict] = []
    seen_asset_host_ids: set[str] = set()
    for asset in assets:
        # Defensive: malformed asset rows can carry `"Type": null` or a
        # bare string — `.get("ShortName")` would AttributeError on
        # either. Coerce to a dict before reaching in.
        _type_raw = asset.get("Type")
        type_dict: dict = _type_raw if isinstance(_type_raw, dict) else {}
        short = (asset.get("type_short") or type_dict.get("ShortName") or "").lower()
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
        # Join to host via custom_number. `custom_number` SHOULD be 1:1
        # but `hosts_config` doesn't enforce uniqueness, so gather every
        # match (id-deduped) — same disambiguation contract as the
        # priority-1 IP-match path.
        for h in hosts:
            if h.get("custom_number") == custom_number:
                hid = (h.get("id") or "").strip()
                if hid and hid not in seen_asset_host_ids:
                    seen_asset_host_ids.add(hid)
                    asset_hits.append(h)
    if len(asset_hits) == 1:
        return asset_hits[0], asset_hits
    if asset_hits:
        return None, _rank_candidates(asset_hits)

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
    return None, _rank_candidates(sub_hits)


def _host_status_emoji(h: dict) -> str:
    """Rough at-a-glance status emoji for the /hosts listing."""
    if not h.get("enabled", True):
        return "⚪"
    return "🟢"


# ----------------------------------------------------------------------------
# Telegram user_id ↔ OmniGrid username mapping
# ----------------------------------------------------------------------------
def _load_mappings() -> dict[str, dict]:
    """Return the persisted mapping ``{telegram_user_id_str: {username, linked_at_ms}}``.

    Stored as JSON in settings KV under ``telegram_user_mappings``.
    Empty / corrupt rows produce an empty dict. Telegram user_ids are
    keyed as strings (JSON has no int keys) — callers should `str(...)`
    the int before lookup.

    **Schema migration**: legacy entries stored just the username
    string (``{tg_id: "alice"}``). Reading those produces a dict
    shape ``{tg_id: {"username": "alice", "linked_at_ms": 0}}``
    automatically — first save through `_save_mappings` upgrades the
    on-disk shape. ``linked_at_ms=0`` is the "unknown / pre-migration"
    sentinel; consumers render "—" instead of a real date when they
    see it.
    """
    from logic.db import load_settings_json
    m = load_settings_json(
        Settings.TELEGRAM_USER_MAPPINGS, default={}, expected_type=dict,
    )
    if not m:
        return {}
    # Normalise legacy string-value entries to the new dict shape.
    out: dict[str, dict] = {}
    for tg_id, value in m.items():
        if isinstance(value, dict):
            if value.get("username"):
                out[tg_id] = {
                    "username": value.get("username"),
                    "linked_at_ms": int(value.get("linked_at_ms") or 0),
                }
        elif isinstance(value, str) and value:
            # Legacy schema — just the username. Mark linked_at as
            # unknown (sentinel 0) so the UI renders "—".
            out[tg_id] = {"username": value, "linked_at_ms": 0}
    return out


# Public alias — cross-module callers import `load_mappings`. The
# leading-underscore form stays as the canonical definition for
# back-compat with anything still importing `_load_mappings`.
load_mappings = _load_mappings


def _save_mappings(mappings: dict[str, dict]) -> None:
    """Persist the mapping dict back to settings. Quiet on failure.
    Always writes the new schema (``{username, linked_at_ms}`` per
    entry) so a legacy on-disk record gets upgraded on first write.

    `linked_at_ms` is clamped to `[0, now_ms + 86_400_000]` (24h
    future window) so a corrupted setting row OR a clock-skewed
    incoming value can't render a year-2999 timestamp in the
    Profile → Telegram UI. The future-window allowance accommodates
    NTP-skewed agents up to a day off; anything beyond that gets
    floored to "now".
    """
    import json
    import time as _time
    from logic.db import set_setting
    try:
        now_ms = int(_time.time() * 1000)
        max_ms = now_ms + 86_400_000  # 24h future window for clock skew
        # Defensive normalisation in case a caller hands us a stale
        # string-shaped value.
        clean: dict[str, dict] = {}
        for tg_id, value in mappings.items():
            if isinstance(value, dict) and value.get("username"):
                raw_ms = int(value.get("linked_at_ms") or 0)  # type: ignore[arg-type]
                # Clamp: below 0 -> 0 (sentinel "unknown"); above the
                # 24h-future cap -> floor to `now_ms`.
                if raw_ms < 0:
                    raw_ms = 0
                elif raw_ms > max_ms:
                    raw_ms = now_ms
                clean[tg_id] = {
                    "username": value["username"],
                    "linked_at_ms": raw_ms,
                }
            elif isinstance(value, str) and value:
                clean[tg_id] = {"username": value, "linked_at_ms": 0}
        set_setting(Settings.TELEGRAM_USER_MAPPINGS, json.dumps(clean))
    except (TypeError, ValueError) as e:
        print(f"[telegram_listener] mapping save failed: {e}")


# Public alias — cross-module callers import `save_mappings`. The
# underscore form stays as the canonical definition for back-compat.
save_mappings = _save_mappings


def _lookup_omnigrid_user(telegram_user_id: object) -> Optional[str]:
    """Return the OmniGrid username for one Telegram user_id, or None
    if the user hasn't linked yet. Accepts ``object`` so every caller
    can pass the raw ``msg["from"]["id"]`` without first coercing it
    (the Telegram API returns numeric ids but pyright + PyCharm widen
    them to ``Any | None`` via the chained ``.get()`` calls)."""
    if telegram_user_id is None:
        return None
    try:
        key = str(int(telegram_user_id))  # type: ignore[arg-type]  # narrowed via try/except
    except (TypeError, ValueError):
        return None
    entry = _load_mappings().get(key)
    if not entry:
        return None
    # Mapping schema is `{username, linked_at_ms}` post-migration; legacy
    # entries may still be a bare username string. Coerce to str for the
    # caller — dict values are Any-typed so the type-checker can't narrow.
    if isinstance(entry, dict):
        username = entry.get("username")
        return str(username) if isinstance(username, str) else None
    if isinstance(entry, str):
        return entry
    return None


def _consume_link_code(code: str) -> Optional[str]:
    """Look up the Profile-minted one-time link code.

    Walks every user's ``ui_prefs.telegram_link_code`` field. Returns
    the username on match + TTL-OK, deletes the row from that user's
    ui_prefs so the code can't be replayed. Returns None on miss /
    expired / no-such-user.
    """
    import json
    import time as _time
    from logic.db import db_conn
    code = (code or "").strip()
    if not code:
        return None
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT username, ui_prefs FROM users WHERE ui_prefs IS NOT NULL"
            ).fetchall()
    # noinspection PyBroadException
    except Exception as e:
        print(f"[telegram_listener] _consume_link_code lookup failed: {e}")
        return None
    now_ms = int(_time.time() * 1000)
    for row in rows:
        try:
            username = row["username"] if hasattr(row, "keys") else row[0]
            ui_prefs_raw = row["ui_prefs"] if hasattr(row, "keys") else row[1]
        except (KeyError, IndexError):
            continue
        if not ui_prefs_raw:
            continue
        try:
            prefs = json.loads(ui_prefs_raw)
        except (ValueError, TypeError):
            continue
        stored = (prefs.get("telegram_link_code") or "").strip()
        expires = int(prefs.get("telegram_link_code_expires_ms") or 0)
        if stored and stored == code and expires > now_ms:
            # Consume the code — single-use, regardless of whether
            # mapping persistence succeeds below.
            prefs.pop("telegram_link_code", None)
            prefs.pop("telegram_link_code_expires_ms", None)
            try:
                with db_conn() as c:
                    c.execute(
                        "UPDATE users SET ui_prefs = ? WHERE username = ?",
                        (json.dumps(prefs), username),
                    )
            # noinspection PyBroadException
            except Exception as e:
                print(f"[telegram_listener] _consume_link_code wipe failed: {e}")
            return username
    return None


def _lookup_user_role(username: str) -> Optional[str]:
    """Return the OmniGrid role (``admin`` / ``readonly``) for one
    username, or None when the user can't be found. Read-only DB
    query; never raises."""
    from logic.db import db_conn
    if not username:
        return None
    try:
        with db_conn() as c:
            row = c.execute(
                "SELECT role FROM users WHERE username = ?", (username,)
            ).fetchone()
    except (sqlite3.DatabaseError, sqlite3.OperationalError):
        return None
    if not row:
        return None
    return row[0] if not hasattr(row, "keys") else row["role"]


# Public alias — cross-module callers import `lookup_user_role`. The
# underscore form stays as the canonical definition for back-compat.
lookup_user_role = _lookup_user_role


def _load_user_weather_pref(username: str) -> Optional[dict]:
    """Read the topbar weather widget's persisted settings for one
    user. The SPA stores these as flat keys on ``ui_prefs`` (not
    nested under ``weather_location``) — they're written by
    ``saveHeaderPrefs()`` via PATCH /api/me/ui-prefs alongside the
    other topbar-widget preferences. Returns a normalised dict:

        {"lat": float, "lon": float, "label": str, "unit": "c" | "f"}

    or None when unset / malformed. ``unit`` defaults to "c" when the
    user hasn't picked one.
    """
    import json
    from logic.db import db_conn
    if not username:
        return None
    try:
        with db_conn() as c:
            row = c.execute(
                "SELECT ui_prefs FROM users WHERE username = ?", (username,)
            ).fetchone()
    except (sqlite3.DatabaseError, sqlite3.OperationalError):
        return None
    if not row:
        return None
    raw = row[0] if not hasattr(row, "keys") else row["ui_prefs"]
    if not raw:
        return None
    try:
        prefs = json.loads(raw)
    except (ValueError, TypeError):
        return None
    lat = prefs.get("headerWeatherLat")
    lon = prefs.get("headerWeatherLon")
    if lat is None or lon is None:
        return None
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return None
    label = (prefs.get("headerWeatherLabel") or "").strip()
    unit_raw = (prefs.get("headerWeatherUnit") or "c")
    unit = "f" if str(unit_raw).strip().lower() == "f" else "c"
    return {
        "lat": lat_f,
        "lon": lon_f,
        "label": label,
        "unit": unit,
    }


# ----------------------------------------------------------------------------
# Command handlers
# ----------------------------------------------------------------------------
# One-shot dedupe sets so `_cmd_help` only WARN-logs each kind of
# missing-metadata once per process — otherwise every /help fired
# against a misconfigured _COMMANDS entry would re-spam the log.
# `_WARNED_MISSING_CATS` tracks unknown category keys (used in
# _COMMANDS but missing from the `categories` heading list).
# `_WARNED_MISSING_CMD_CAT` tracks commands whose `_COMMANDS` entry
# omits the `category` field entirely (silent fall-through to "misc").
_WARNED_MISSING_CATS: set[str] = set()
_WARNED_MISSING_CMD_CAT: set[str] = set()

# ----------------------------------------------------------------------------
# Command handlers extracted to logic.telegram_handlers. Imported by
# name here so the _COMMANDS dispatch dict below can reference each
# handler. The handlers themselves go through a lazy `_listener()`
# shim to call back into this module's helpers (avoids deadlock at
# top-level cross-import — see telegram_handlers.py docstring).
# ----------------------------------------------------------------------------
from logic.telegram_handlers import (  # noqa: E402
    _cmd_cleanup,
    _cmd_help,
    _cmd_host,
    _cmd_hosts,
    _cmd_ip,
    _cmd_link,
    _cmd_moon,
    _cmd_restart,
    _cmd_time,
    _cmd_unlink,
    _cmd_update,
    _cmd_version,
    _cmd_weather,
    _cmd_whoami,
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
# Commands that bypass the omnigrid-user-mapping gate in
# `_process_update`. An unmapped operator can run these without first
# `/link`-ing their account — used for discovery / self-service:
#   /link, /help, /start — mapping flow itself
#   /whoami, /myid       — show the sender's own telegram_user_id
#   /version, /ver       — non-sensitive build identifier
#   /ip                  — gated separately by `tuning_public_ip_enabled`
# `_cmd_help` reads this set to render a 🔓 marker next to open commands
# so unmapped senders can see at a glance which ones actually work
# pre-link (operator-flagged: prior /help output had no distinction,
# leading to "tried /host and got 🔒 Link your account first" confusion).
# Derived from `_COMMANDS[*].access == "open"` after the dict literal
# is constructed below. Single source of truth — pre-derived this was a
# hand-maintained set that could drift from the per-command metadata
# (operator adding a new open command had to update BOTH places). The
# helper `_open_command_names()` walks `_COMMANDS` lazily so the order
# of declaration doesn't matter.
def _open_command_names() -> set[str]:
    """Return every `/cmd` name with `access == "open"`. Computed on
    demand so the result tracks live `_COMMANDS` state if a future
    test mutates it.
    """
    return {name for name, meta in _COMMANDS.items()
            if (meta or {}).get("access") == "open"}


# Backward-compat alias — every existing reference to `_OPEN_COMMANDS`
# keeps working. New code SHOULD call `_open_command_names()` directly
# OR consult `_command_access(name)` for finer-grained tier checks.
# Initialised AFTER the `_COMMANDS` literal so the names exist.

# `usage` is rendered HTML-escaped inside `<b>...</b>` — write it the
# way you want it to read in Telegram, with literal `<target>` /
# `<arg>` placeholders. `description` follows in plain text after the
# em-dash separator. `hidden=True` keeps the entry off the /help menu
# (used for aliases like `/start` → `_cmd_help`).
#
# Per-command auth tier:
#   "access": "open"   → available before linking (mapping / discovery /
#                        public-info surface). Published in the DEFAULT
#                        setMyCommands scope so unmapped chats see only
#                        these commands in the `/` autocomplete.
#   "access": "linked" → requires a Telegram → OmniGrid user link
#                        (`_lookup_omnigrid_user(sender_id)` non-None).
#                        Published only in per-chat scopes for chats
#                        listed in `telegram_chat_id`.
#   "access": "admin"  → currently same wire behaviour as `linked` (the
#                        Telegram API can't filter setMyCommands per
#                        user role, only per chat). Future-proofs the
#                        metadata so a future per-user-scope feature
#                        can hide admin-only commands from non-admin
#                        members of a shared chat.
#
# `_OPEN_COMMANDS` is derived from this metadata at module load so the
# two never drift. New commands MUST declare `access` explicitly — a
# missing key is treated as `"linked"` (the safest default).
_COMMANDS: dict[str, dict[str, Any]] = {
    "/help": {
        "handler": _cmd_help,
        "usage": "/help",
        "description": "Show this command list",
        "category": "getting_started",
        "access": "open",
    },
    "/start": {
        # Telegram clients send `/start` automatically when the user
        # first opens a conversation with the bot. Mapping it to help
        # gives a clean first-contact experience.
        "handler": _cmd_help,
        "usage": "/start",
        "description": "Show the command list",
        "category": "getting_started",
        "access": "open",
        "hidden": True,  # don't double up in /help (same handler as /help)
    },
    "/hosts": {
        "handler": _cmd_hosts,
        "usage": "/hosts",
        "description": "List curated hosts with their status",
        "category": "fleet",
        "access": "linked",
    },
    "/host": {
        "handler": _cmd_host,
        "usage": "/host <target>",
        "description": "Show last-known stats for one host (CPU / memory / disk / uptime + extended provider stats: load, swap, bandwidth, temperatures, GPUs, containers, UPS). Cached readings only — no live probes.",
        "category": "fleet",
        "access": "linked",
    },
    "/restart": {
        "handler": _cmd_restart,
        "usage": "/restart <target>",
        "description": "Restart a host via SSH (destructive — requires confirm)",
        "category": "ops",
        "access": "admin",
    },
    "/reboot": {
        # Alias for /restart — same handler, same usage shape, same
        # destructive-confirm gate. Operators type "reboot" reflexively
        # when they want the OS-level restart (vs Docker service
        # restart); accept both verbs so the muscle-memory works
        # either way. The canonical command stays /restart (matches
        # the SPA + docs surface); /reboot is an alias only — same
        # `_cmd_restart` handler so any fix to one applies to both.
        "handler": _cmd_restart,
        "usage": "/reboot <target>",
        "description": "Alias of /restart — reboot a host via SSH (destructive — requires confirm)",
        "category": "ops",
        "access": "admin",
    },
    "/cleanup": {
        "handler": _cmd_cleanup,
        "usage": "/cleanup [confirm]",
        "description": "List (or remove with `confirm`) stopped / failed / orphan containers — same surface as the SPA's topbar Cleanup button. SPA tabs auto-refresh as each removal lands.",
        "category": "ops",
        "access": "linked",
    },
    "/update": {
        "handler": _cmd_update,
        "usage": "/update [all | <name>] [confirm]",
        "description": "List items with pending updates (no args), update ONE item by name, or `/update all` to pull-and-recreate every item flagged `update_available`.",
        "category": "ops",
        "access": "linked",
    },
    "/link": {
        "handler": _cmd_link,
        "usage": "/link <code>",
        "description": "Link your Telegram account to an OmniGrid user (code minted in Profile → Telegram)",
        "category": "account",
        "access": "open",
    },
    "/unlink": {
        "handler": _cmd_unlink,
        "usage": "/unlink",
        "description": "Remove the Telegram → OmniGrid user link",
        "category": "account",
        # `linked` (NOT `open`) — an unmapped user has no link to
        # remove. Surfacing `/unlink` in the default-scope autocomplete
        # would be noise on the first contact, and the bypass-gate
        # for `_OPEN_COMMANDS` shouldn't let unmapped chats invoke
        # `/unlink` against another user's link.
        "access": "linked",
    },
    "/whoami": {
        "handler": _cmd_whoami,
        "usage": "/whoami",
        "description": "Show your access level &amp; ID (which OmniGrid user you're linked to)",
        "category": "account",
        "access": "open",
    },
    "/myid": {
        # Alias for /whoami — the most common phrasing operators reach
        # for when they want to know "who am I as far as the bot is
        # concerned". Same handler, hidden from /help so the menu
        # doesn't double up (the dedup-by-handler logic in _cmd_help
        # already handles this — `hidden: True` makes intent explicit).
        "handler": _cmd_whoami,
        "usage": "/myid",
        "description": "Show your access level &amp; ID (alias for /whoami)",
        "category": "account",
        "access": "open",
        "hidden": True,
    },
    "/weather": {
        "handler": _cmd_weather,
        "usage": "/weather",
        "description": "Show the weather for your saved location (set it in Profile → Weather)",
        "category": "info",
        "access": "linked",
    },
    "/moon": {
        "handler": _cmd_moon,
        "usage": "/moon",
        "description": "Show today's moon phase + illumination (requires WeatherAPI.com provider)",
        "category": "info",
        "access": "linked",
    },
    "/time": {
        "handler": _cmd_time,
        "usage": "/time",
        "description": "Show the local time at your saved weather location",
        "category": "info",
        "access": "linked",
    },
    "/version": {
        "handler": _cmd_version,
        "usage": "/version",
        "description": "Show the running OmniGrid version",
        "category": "info",
        "access": "open",
    },
    "/ip": {
        "handler": _cmd_ip,
        "usage": "/ip",
        "description": "Show the deployment's public IP + ISP / ASN / country (requires tuning_public_ip_enabled in Admin → Public IP)",
        "category": "info",
        "access": "open",
    },
    "/ver": {
        # Alias for /version — same handler, hidden so the /help menu
        # doesn't double up. Dedup-by-handler in _cmd_help drops it
        # automatically; `hidden: True` makes intent explicit.
        "handler": _cmd_version,
        "usage": "/ver",
        "description": "Show the running OmniGrid version (alias for /version)",
        "category": "info",
        "access": "open",
        "hidden": True,
    },
}


def _command_access(name: str) -> str:
    """Return the auth tier for a `/cmd` name. Missing key defaults to
    `"linked"` — the safer fallback so a forgotten declaration doesn't
    accidentally leak a new command into the unmapped-chat default scope.
    """
    meta = _COMMANDS.get(name) or {}
    return str(meta.get("access") or "linked")


# Initialised here (after `_COMMANDS` is fully constructed) so the
# back-compat reference stays correct. Module-level snapshot — any
# runtime mutation of `_COMMANDS` (tests, future plug-in) should
# call `_open_command_names()` directly rather than reading this set.
_OPEN_COMMANDS: set[str] = _open_command_names()

# ----------------------------------------------------------------------------
# AI-fallback helpers (markdown / escape / build_context) extracted to
# logic.telegram_ai. Lazy-imported inside `_process_update` to avoid
# top-level circular imports. See file header for the canonical split.
# ----------------------------------------------------------------------------
_AI_CALL_BUCKETS: dict[int, list[float]] = {}


# Per-(telegram_user_id, command_head) cooldown for destructive verbs
# (`/cleanup confirm`, `/restart <target> confirm`, `/update <target>
# confirm`). Operator-flagged: a confirmed-destructive command can be
# replayed by simply re-sending the same line — bot has no memory of
# "you just ran this 5 seconds ago". The cooldown stops accidental /
# malicious rapid-fire reuse while still letting an operator typing
# fast intentionally re-send after a real wait. Operator-tunable via
# `tuning_telegram_destructive_cooldown_seconds` (default 30s; multi-
# admin chats may want higher to prevent two admins firing the same
# destructive verb back-to-back). Shares the `logic.cooldown.Cooldown`
# class (imported at the top of the file) so the arming / remaining-
# time protocol matches Webmin's auth-failure cooldown. Passes the
# resolver callable so `.seconds` re-reads the TUNABLE on every
# `arm()` / `remaining()` call — same per-use contract as other
# Cooldown consumers that want runtime tunable hot-reload.


def _destructive_cooldown_seconds() -> float:
    """Operator-tunable destructive-command cooldown window (seconds).
    Per-use read so an Admin → Config edit lands on the next command
    without restart. Defensive fallback to the 30s default if the
    `tuning_int` call raises (corrupt DB row). Returns float so the
    Cooldown resolver-callable contract is satisfied."""
    from logic.tuning import Tunable, tuning_int
    try:
        return float(tuning_int(Tunable.TELEGRAM_DESTRUCTIVE_COOLDOWN_SECONDS))
    except (KeyError, ValueError, TypeError):
        return 30.0


# Resolver-callable form: Cooldown's `.seconds` @property invokes the
# resolver on every call, so runtime TUNABLE edits land on the next
# destructive command without restart. Forward-ref-safe because the
# lambda only evaluates at call time, after the module has fully
# loaded.
_DESTRUCTIVE_COOLDOWN = _Cooldown(seconds=_destructive_cooldown_seconds)


def _destructive_cooldown_check(
    sender_id: Optional[int], head: str,
) -> tuple[bool, float]:
    """Return ``(allowed, wait_s)`` for a destructive command replay.

    ``allowed=False`` returns the seconds remaining on the cooldown
    so the reply can tell the operator how long to wait. Arms the
    cooldown as a side effect on ``allowed=True`` so subsequent calls
    within the window are rate-limited.

    Anonymous senders (no numeric user_id — rare; edited_message from
    an anonymous-admin channel) bypass — no stable key to use. Same
    bypass shape as :func:`_ai_rate_limit_check`.
    """
    if not isinstance(sender_id, int):
        return True, 0.0
    # Cooldown's resolver-callable (set in the constructor above) re-
    # reads the TUNABLE on every `.arm()` / `.remaining()` call —
    # runtime tunable hot-reload comes for free. No manual override
    # needed; pre-fix tried to assign `.seconds` directly but
    # `.seconds` is a @property without a setter.
    remaining = _DESTRUCTIVE_COOLDOWN.remaining(sender_id, head)
    if remaining is not None and remaining > 0:
        return False, float(remaining)
    _DESTRUCTIVE_COOLDOWN.arm(sender_id, head)
    return True, 0.0


# ----------------------------------------------------------------------------
# AI rate-limit + `_ai_reply` extracted to logic.telegram_ai. Lazy-
# imported inside `_process_update` (the only consumer).
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
# Long-poll loop
# ----------------------------------------------------------------------------

# In-process de-dup of recently-seen update_ids. Belt-and-braces guard
# against a duplicate-dispatch path producing two identical replies
# for one operator command. The outer loop's offset cursor SHOULD make
# this impossible (Telegram won't redeliver updates whose ids are <
# the offset we passed), but operator-reported "I typed /weather once
# and got two identical replies" symptoms imply some edge — likely a
# Telegram-side redelivery during a long-poll-window crossing OR an
# offset-save race after a network blip — slips through. A bounded
# FIFO of recently-processed update_ids catches that without needing
# to identify the root cause: re-dispatch sees the id in the set,
# logs once, and returns silently.
_SEEN_UPDATE_IDS_CAP = 512
_seen_update_ids: "_OrderedDict[int, None]" = _OrderedDict()


def _mark_update_seen(update_id: int) -> bool:
    """Return True if this update_id is NEW (not seen before in this
    process). Side-effect: records the id with FIFO eviction at
    `_SEEN_UPDATE_IDS_CAP`. Returns False on a duplicate so callers
    can short-circuit.
    """
    if update_id in _seen_update_ids:
        # Re-insert at the tail so a duplicate within the LRU window
        # doesn't immediately age out — keeps the dedupe sticky for
        # repeated rapid replays.
        _seen_update_ids.move_to_end(update_id)
        return False
    _seen_update_ids[update_id] = None
    while len(_seen_update_ids) > _SEEN_UPDATE_IDS_CAP:
        _seen_update_ids.popitem(last=False)
    return True


# noinspection SpellCheckingInspection
async def _process_update(client: httpx.AsyncClient, update: dict) -> None:
    """Authorize + parse + dispatch one incoming Update."""
    # Belt-and-braces dedupe: if we've already processed this exact
    # update_id in this process, silently drop. Without this, an
    # operator-reported "typed /weather once, got two identical
    # replies" path slips through whatever offset-cursor race or
    # Telegram redelivery surfaces it.
    update_id_raw = update.get("update_id")
    if isinstance(update_id_raw, int):
        if not _mark_update_seen(update_id_raw):
            print(
                f"[telegram_listener] duplicate update_id={update_id_raw} "
                f"— already processed in this lifespan, skipping"
            )
            return
    ok, reason = _is_authorized(update)
    # Stamp the inbound chat.id onto the contextvar BEFORE any reply
    # path fires (including the unauthorized log line below — though
    # that path returns without replying). Set even when `ok=False` so
    # if a future change adds a reply for some rejected-but-replied
    # path it lands in the right chat. Stringified for the same reason
    # `_is_authorized` stringifies: Telegram numeric IDs survive the
    # JSON round-trip as ints, but the CSV setting + ContextVar are
    # strings everywhere else.
    _msg_for_chat = update.get("message") or update.get("edited_message") or {}
    _inbound_chat = (_msg_for_chat.get("chat") or {}).get("id")
    if _inbound_chat is not None:
        _inbound_chat_id.set(str(_inbound_chat))
    if not ok:
        # Silently ignore — don't tip off an attacker probing the bot.
        # Log so operators can diagnose "my command isn't running".
        print(f"[telegram_listener] unauthorized update: {reason}")
        return
    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    if not text:
        return
    if not text.startswith("/"):
        # Non-slash text → route to AI for a conversational reply.
        # CRITICAL: Telegram NEVER triggers actions through AI in this
        # phase — `/commands` are the ONLY action surface. The AI's
        # `ACTION:` / `ACTION_HOSTS:` / `MEMORY:` directives are
        # stripped from the response before posting back to Telegram.
        # Mapping gate applies here too — unmapped Telegram users get
        # NO AI access (would leak fleet context to an unauthenticated
        # sender). Reply prompts them to /link first.
        sender_id = (msg.get("from") or {}).get("id")
        mapped = _lookup_omnigrid_user(sender_id) if sender_id is not None else None
        if not mapped:
            await _send_reply(
                client,
                "🔒 Link your account first. Generate a code in OmniGrid → "
                "Profile → Telegram, then reply with <code>/link &lt;code&gt;</code>."
            )
            return
        # Lazy import — `_ai_reply` lives in `logic.telegram_ai` (see
        # the AI-fallback extraction). Loading at call time keeps the
        # cross-module import safe even though telegram_ai imports
        # from this listener via its own `_listener()` shim.
        from logic.telegram_ai import _ai_reply
        await _ai_reply(client, text, msg, mapped)
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
    # Mapping gate — commands NOT in the "open" set require the sender
    # to be linked to an OmniGrid user first. /link / /help / /start /
    # /whoami stay open so an unmapped operator can discover the
    # mapping flow + verify their user_id.
    # /version + /ver + /ip are open per their docstrings: version is
    # non-sensitive (lets an unmapped operator confirm which build
    # they're talking to before linking), and /ip is gated separately
    # by the `tuning_public_ip_enabled` tunable so the dispatcher gate
    # doesn't need to second-guess it.
    if head not in _OPEN_COMMANDS:
        sender_id = (msg.get("from") or {}).get("id")
        mapped = _lookup_omnigrid_user(sender_id) if sender_id is not None else None
        if not mapped:
            await _send_reply(
                client,
                "🔒 Link your account first. Generate a code in OmniGrid → "
                "Profile → Telegram, then reply with <code>/link &lt;code&gt;</code>. "
                "Use <code>/help</code> for the command list, <code>/whoami</code> "
                "to see your Telegram user_id."
            )
            return
    handler = meta.get("handler")
    if handler is None:
        await _send_reply(
            client,
            f"Command <code>{_escape(head)}</code> has no handler wired. Internal error."
        )
        return
    # Dispatcher-level audit — every authorised /command writes ONE
    # history row under `op_type=telegram_command` so Admin → History
    # shows a complete trail of who used the bot, when, and for what.
    # AI free-text traffic is logged separately by `_ai_reply` via
    # `record_ai_call` (op_type=`ai_telegram`) to avoid double-logging.
    from_obj = msg.get("from") or {}
    sender_id_audit = from_obj.get("id")
    actor_audit = (
                      _lookup_omnigrid_user(sender_id_audit)
                      if sender_id_audit is not None else None
                  ) or "telegram"
    # Forensic-review fields — surface the operator's Telegram handle
    # alongside the numeric user_id so Admin → History rows are
    # readable without a separate user_id → @handle lookup. `username`
    # is the @-handle (may be absent for users who haven't set one);
    # `display_name` falls back to `first_name + last_name` so a
    # bot-only account without a @handle still shows SOMETHING.
    tg_username = str(from_obj.get("username") or "").strip()
    tg_first = str(from_obj.get("first_name") or "").strip()
    tg_last = str(from_obj.get("last_name") or "").strip()
    tg_display_name = (tg_first + (" " + tg_last if tg_last else "")).strip()
    # Sanitise args BEFORE persisting — `/link <code>` carries a
    # single-use 6-digit code that would leak via the audit log
    # otherwise. Same redaction class as auth-secret masking in SSH
    # audit rows. Stamp the `target_name` with an inline redaction
    # hint too so operators reading Admin → History see WHY the row
    # has no arg detail (defensive — the redacted `events.args=[...]`
    # IS the canonical record; this is just a friendlier surface
    # label).
    safe_args = list(args)
    # Preserve the first positional arg in `target_name` so the
    # History tab's row preview shows "/host web01" / "/restart svc-x"
    # instead of bare "/host" / "/restart" for every invocation.
    # Operator-triage usecase: scanning Admin → History for "what did
    # the bot touch at 2am?" reads cleanly when the target identifier
    # is on the row. Cap at 64 chars so a pasted megablob doesn't
    # blow up the column width. `/link` keeps the redacted-context
    # stamp because its arg IS the secret.
    if safe_args:
        first_arg = str(safe_args[0])[:64]
        target_name_audit = f"{head} {first_arg}"
    else:
        target_name_audit = head
    if head == "/link" and safe_args:
        safe_args = ["<redacted>"]
        target_name_audit = "/link (code redacted)"
    handler_status = "success"
    handler_error: Optional[str] = None
    try:
        await handler(client, args, msg)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    # noinspection PyBroadException
    except Exception as e:  # noqa: BLE001 — never let one bad command crash the loop
        handler_status = "error"
        handler_error = f"{type(e).__name__}: {e}"
        print(f"[telegram_listener] handler {head!r} crashed: {e}")
        try:
            await _send_reply(client, f"❌ Command crashed: <code>{_escape(str(e))}</code>")
        except (httpx.HTTPError, OSError):
            # Reply path also broken — last-resort silent fail; the
            # outer except already logged the original crash via the
            # audit row, so swallowing here just keeps the dispatcher
            # loop alive for the next update.
            pass
    # Write the audit row AFTER the handler completes so we capture
    # the outcome. Best-effort: `write_admin_audit` swallows + logs its
    # own SQL errors; the outer `db_conn()` wrap is caught defensively
    # so an import / connection failure can't block the command reply.
    try:
        from logic.db import db_conn as _db_conn
        from logic.ops import write_admin_audit as _write_admin_audit
        with _db_conn() as _c:
            _write_admin_audit(
                _c, "telegram_command",
                target_kind="telegram",
                target_name=target_name_audit or "",
                target_id="",
                actor=actor_audit or "telegram",
                status=handler_status,
                error=handler_error,
                events_dict={
                    "command": head,
                    "args": safe_args,
                    "telegram_user_id": int(sender_id_audit) if sender_id_audit is not None else None,  # type: ignore[arg-type]  # guard above narrows None branch
                    "telegram_username": tg_username or None,
                    "telegram_display_name": tg_display_name or None,
                },
            )
    # noinspection PyBroadException
    except Exception as _audit_err:
        print(f"[telegram_listener] audit row write failed (telegram_command): {_audit_err}")


def _load_offset() -> int:
    """Resume from the last seen update_id (+ 1) across restarts."""
    from logic.db import get_setting
    raw = (get_setting(Settings.TELEGRAM_LAST_UPDATE_ID, "0") or "0").strip()
    try:
        return int(raw) + 1 if raw else 0
    except (TypeError, ValueError):
        return 0


def _save_offset(update_id: int) -> None:
    from logic.db import set_setting
    try:
        set_setting(Settings.TELEGRAM_LAST_UPDATE_ID, str(int(update_id)))
    except (TypeError, ValueError):
        pass


# Category → leading-emoji map for the `setMyCommands` description
# prefix. Telegram doesn't support per-command icons natively in the
# `/` autocomplete menu, but emoji glyphs in the description string
# render as inline characters, which is the closest available
# affordance. Keys match `_COMMANDS[*].category`.
_TELEGRAM_MENU_EMOJIS: dict[str, str] = {
    "getting_started": "📖",
    "fleet": "🖥",
    "ops": "⚙️",
    "account": "🔗",
    "info": "ℹ️",
    "misc": "🧩",
}

# One-shot guard so we don't re-call `setMyCommands` on every loop
# iteration — Telegram's setMyCommands has a soft rate limit and the
# registration is per-bot, not per-update. Reset on:
#   - bot-token change (operator rotated the token in admin settings)
# detected by comparing the live token against the last registered
# token hash. Failure to register is non-fatal — the bot still works,
# just without the `/` autocomplete menu.
_LAST_REGISTERED_TOKEN_HASH: list[Optional[str]] = [None]


async def _register_telegram_commands(client: httpx.AsyncClient) -> None:
    """Push the current `_COMMANDS` set to Telegram's `setMyCommands`
    so the `/` autocomplete menu in Telegram clients shows every
    available command + description.

    Behaviour:
      - One-shot per `(token,)` — skipped if we've already registered
        with the same token in this process. Operator can force a
        re-register by restarting the listener (cooperative with
        the per-tick gate at the top of `listener_loop`).
      - Skips `hidden=True` entries (aliases, internal commands).
      - Dedupes by handler so alias commands don't appear twice.
      - Prefixes each description with the category's emoji glyph
        (Telegram's `/` menu doesn't support icons natively but
        renders emoji inline as the closest substitute).
      - Truncates each description at 240 chars (Telegram cap is 256,
        leaving headroom for the emoji prefix + separator).
      - Strips the leading `/` from each command name (Telegram's
        `command` field expects bare names like `help`, not `/help`).
      - Best-effort: failure logs once and returns; the bot still
        responds to commands without the menu.
    """
    import hashlib as _hashlib
    token = _resolved_bot_token()
    if not token:
        return
    token_hash = _hashlib.sha256(token.encode()).hexdigest()[:16]
    if _LAST_REGISTERED_TOKEN_HASH[0] == token_hash:
        return
    # Dedupe by handler — `/start` shares `_cmd_help`'s handler, so
    # we only register the primary `/help` entry.
    seen_handlers: set[Any] = set()
    commands_payload: list[dict] = []
    for name, meta in _COMMANDS.items():
        if meta.get("hidden"):
            continue
        handler = meta.get("handler")
        if handler is None or handler in seen_handlers:
            continue
        seen_handlers.add(handler)
        # Telegram expects bare command names without the leading `/`.
        cmd_name = name.lstrip("/").lower()
        if not cmd_name or len(cmd_name) > 32:
            continue
        cat = meta.get("category") or "misc"
        emoji = _TELEGRAM_MENU_EMOJIS.get(cat, "🧩")
        # Strip any pre-escaped `&amp;` from descriptions stored
        # for the /help HTML render path; the
        # `setMyCommands` API expects plain text.
        raw_desc = (meta.get("description") or "").replace("&amp;", "&")
        # Compact description: emoji prefix + plain description text.
        # Telegram's hard limit is 256 BYTES on the description, not
        # 256 chars — multi-byte UTF-8 (emoji, accented characters,
        # CJK) could push a 250-char description past the byte cap
        # silently. Pre-fix this used a 240-CHAR cap which conflated
        # the two; an emoji-heavy operator-written description could
        # exceed the byte limit. Truncate-by-bytes with a 240-byte
        # budget (~16 bytes of headroom under the 256 limit) so we
        # never split a multi-byte codepoint mid-sequence (the
        # encode→slice→decode pattern keeps boundaries safe via
        # `errors='ignore'`).
        full = f"{emoji} {raw_desc}".strip()
        # `encode()` / `decode(...)` rely on the stdlib UTF-8 default
        # (stable since Py3) — explicit "utf-8" literal would just
        # trigger the IDE's "Argument equals default" warning.
        encoded = full.encode()
        if len(encoded) > 240:
            desc = encoded[:240].decode(errors="ignore").strip()
        else:
            desc = full
        if not desc:
            desc = cmd_name
        commands_payload.append({
            "command": cmd_name,
            "description": desc,
            "_is_open": name in _OPEN_COMMANDS,
        })
    if not commands_payload:
        return
    # Telegram caps at 100 commands. We've got ~20 today; defensive
    # truncation in case the roster grows past that one day.
    commands_payload = commands_payload[:100]

    # Per-scope strategy — solves the "/ autocomplete shows every
    # command, most error with 'unauthorised'" UX wart. Two payloads:
    #
    #   DEFAULT scope (every chat without a more-specific override) →
    #       open commands only (`_OPEN_COMMANDS` set). Unmapped users
    #       see only the commands that actually work for them
    #       (/help, /start, /link, /unlink, /version) instead of the
    #       full roster that 4xx's on auth.
    #
    #   CHAT scope (every authorised chat from
    #       `telegram_chat_id` CSV) → full roster. Authorised users
    #       still see every command they can actually invoke.
    #
    # If the bot has NO authorised chat configured (fresh deploy)
    # the chat-scope step is skipped and the default carries the full
    # roster as a fallback — operator linking the first chat will
    # see every command + the listener's per-loop register re-fires
    # the proper per-scope split on the next iteration.
    full_payload = [
        {"command": c["command"], "description": c["description"]}
        for c in commands_payload
    ]
    open_payload = [
        {"command": c["command"], "description": c["description"]}
        for c in commands_payload if c["_is_open"]
    ]
    authorized_chats = sorted(_authorized_chat_ids())
    default_payload = open_payload if (authorized_chats and open_payload) else full_payload

    api_base = _telegram_api_base()
    set_url = f"{api_base}/bot{token}/setMyCommands"

    async def _push(payload: list[dict], scope_obj: Optional[dict], label: str) -> bool:
        body: dict = {"commands": payload}
        if scope_obj is not None:
            body["scope"] = scope_obj
        try:
            r = await client.post(set_url, json=body, timeout=10.0)
            if r.status_code == 200 and (r.json() or {}).get("ok"):
                print(
                    f"[telegram_listener] setMyCommands OK ({label}) — "
                    f"{len(payload)} commands registered"
                )
                return True
            print(
                f"[telegram_listener] setMyCommands failed ({label}): "
                f"HTTP {r.status_code} body={r.text[:200]}"
            )
            return False
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        # noinspection PyBroadException
        except Exception as exc:  # noqa: BLE001
            print(f"[telegram_listener] setMyCommands exception ({label}): {exc}")
            return False

    default_ok = await _push(default_payload, None, "default")
    chat_results = []
    # Telegram's bot API enforces a soft 30 calls/sec rate limit on
    # setMyCommands. With 1 default scope + N chat scopes, a fleet of
    # 30+ authorised chats could theoretically 429 if pushed back-to-
    # back. Tiny per-call sleep keeps the per-deploy registration
    # comfortably under the cap. Sleep is OUTSIDE _push so the helper
    # stays single-purpose; chats < 30 effectively pay 0ms.
    for chat_id in authorized_chats:
        try:
            chat_scope = {"type": "chat", "chat_id": int(chat_id)}
        except (TypeError, ValueError):
            # Telegram's chat scope requires an int chat_id. Channel
            # IDs of the form `@channel_name` aren't supported here —
            # skip rather than 400.
            continue
        chat_results.append(await _push(full_payload, chat_scope, f"chat={chat_id}"))
        await asyncio.sleep(0.05)

    # Token-hash gate only re-fires when EITHER push fails — so a
    # transient Telegram error doesn't lock the registration out for
    # the rest of the process lifetime.
    if default_ok and all(chat_results):
        _LAST_REGISTERED_TOKEN_HASH[0] = token_hash


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
                http_to = _telegram_http_timeout()
                long_poll_to = _telegram_long_poll_timeout()
                async with httpx.AsyncClient(timeout=http_to) as client:
                    # Register the `/` autocomplete menu via Telegram's
                    # `setMyCommands` API on the first iteration of
                    # each (token,) lifetime. The helper guards against
                    # re-calling on every iteration via a token-hash
                    # check, so this is effectively one-shot per
                    # listener-process unless the operator rotates the
                    # bot token. Non-fatal: a failure logs and the loop
                    # continues without the menu.
                    await _register_telegram_commands(client)
                    r = await client.get(
                        f"{_telegram_api_base()}/bot{token}/getUpdates",
                        params={
                            "offset": offset,
                            "timeout": long_poll_to,
                            "allowed_updates": '["message","edited_message"]',
                        },
                    )
                    if r.status_code != 200:
                        # Transient 5xx — common signature of Cloudflare /
                        # Authentik / Telegram-side blip. Logged at the
                        # "skipped" severity (the persistent-log classifier
                        # paints "fail" / "error" red; "skipped" stays
                        # info-coloured) so a brief upstream hiccup doesn't
                        # bury actionable signal under repeated ERROR rows.
                        # Permanent failures (4xx) keep the louder wording.
                        verb = "skipped (transient)" if 500 <= r.status_code < 600 else "non-ok"
                        print(f"[telegram_listener] getUpdates {verb}: HTTP {r.status_code}: {r.text[:200]}")
                        await asyncio.sleep(5)
                        continue
                    body = r.json() or {}
                    if not body.get("ok"):
                        # Same noise-down treatment as the HTTP-5xx branch.
                        # body.get('description') without `ok=true` is
                        # typically a transient hiccup ("Bad Gateway" /
                        # "Retry after N seconds") that resolves on the
                        # next iteration without operator action.
                        print(f"[telegram_listener] getUpdates skipped (not ok): {body.get('description')!r}")
                        await asyncio.sleep(5)
                        continue
                    updates = body.get("result") or []
                    # Wrap per-update writes in defer_settings_version_bump
                    # so the per-message `_save_offset` (which calls
                    # `set_setting(TELEGRAM_LAST_UPDATE_ID, ...)`) doesn't
                    # bump `_settings_version` once per message — a chatty
                    # group at 5 msgs/sec would otherwise fan out 5 SSE
                    # `settings:updated` events per second to every other
                    # tab. Inside the context, N saves collapse to ONE
                    # bump on exit (and `_save_offset` is the only
                    # settings-writer in this code path, so collapsing
                    # here is exhaustive). The offset cursor isn't
                    # cross-tab-relevant on its own, but the version
                    # exclusion-list approach (per-key suppression) is
                    # heavier than this single-line wrap.
                    from logic.db import defer_settings_version_bump as _defer
                    with _defer():
                        for update in updates:
                            update_id = update.get("update_id")
                            if isinstance(update_id, int):
                                offset = update_id + 1
                                _save_offset(update_id)
                            try:
                                await _process_update(client, update)
                            except (asyncio.CancelledError, KeyboardInterrupt):
                                raise
                            # noinspection PyBroadException
                            except Exception as e:  # noqa: BLE001
                                print(f"[telegram_listener] update processing failed: {e}")
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except httpx.HTTPError as e:
                # Network blip — back off briefly + retry. Don't spam
                # logs with the same error every iteration. httpx
                # exceptions sometimes carry an empty `str(e)` (e.g.
                # bare ConnectError before any details are populated),
                # so include the exception class name as a fallback
                # so the operator's log doesn't show `[telegram_listener]
                # network:` with nothing after the colon.
                # `warning:` token forces the WARN bucket in
                # logic/logs.py:_severity_for so transient network
                # blips surface in Admin → Logs as amber instead of
                # disappearing into the INFO stream. They're not
                # ERROR (the loop retries cleanly on the next tick)
                # but they're not just informational either.
                detail = str(e).strip() or e.__class__.__name__
                print(f"[telegram_listener] network warning: {detail}")
                await asyncio.sleep(5)
            # noinspection PyBroadException
            except Exception as e:  # noqa: BLE001
                print(f"[telegram_listener] loop iteration failed: {e}")
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        print("[telegram_listener] lifespan cancelled")
        raise
