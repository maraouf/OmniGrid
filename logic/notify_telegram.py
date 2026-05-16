"""Telegram notification medium (Phase 1: send-only).

Architecture
------------
Third notification medium alongside the in-app store (``app``) and Apprise
(``apprise``). Implements the canonical ``async def send(...)`` contract
documented in ``logic/ops.py``'s ``NOTIFY_MEDIUMS`` registration.

Auth model
----------
Bot-token auth: the operator creates a Telegram bot via @BotFather on
Telegram, gets a token of the shape ``<bot_id>:<random-base64>``, and
adds the bot to the destination chat (DM / group / supergroup / forum
topic). The bot needs at minimum "send messages" permission inside the
chat — group/supergroup admins control that via the Telegram client.

Chat targeting
--------------
Telegram chat IDs have three flavours:

  - **Direct messages**: positive integers (the user's Telegram user_id).
  - **Groups**: negative integers (`-100...` range historically;
    Telegram migrates legacy groups to supergroups automatically).
  - **Supergroups + channels**: negative integers prefixed with ``-100``
    (e.g. ``-1001234567890``). Most modern groups end up here.

Forum topics
------------
A supergroup with "topics" enabled splits messages into named threads
(Telegram forum mode). To post into a specific topic, the Bot API
requires the optional ``message_thread_id`` int parameter alongside
``chat_id``. Topic IDs are surfaced in Telegram clients as
``message?thread=<id>`` query params; we expose this as
``telegram_thread_id`` in settings (a single global default — phase 1
keeps the configuration shape minimal).

Phase 1 scope (send-only)
-------------------------
Just ``POST /bot<token>/sendMessage`` with ``chat_id`` + optional
``message_thread_id`` + the rendered ``title`` / ``body`` text. Honours
the standard severity → emoji prefix convention used by Apprise so
notifications stay visually distinguishable.

Phase 2 (deferred): listen for inbound Telegram commands via webhook
or long-poll, so operators can ack / pause / resume from Telegram
itself. Not part of this module's surface yet.

Defence-in-depth
----------------
- Bot token is a write-only secret (``_set`` flag pattern in settings).
- HTTP errors fail the send but never raise — Telegram outages must
  not block the dispatcher.
- No retry inside this module — the dispatcher's ``asyncio.gather`` will
  log the failure outcome; transient retries belong to a higher layer.
"""
from __future__ import annotations

import asyncio
from typing import Optional
from logic.settings_keys import Settings

import httpx


# Telegram API base default. Tokens are appended as ``/bot<token>/<method>``.
# Operators can override via the ``telegram_api_base`` setting (Admin →
# Notifications → Telegram) for self-hosted Bot API gateways or proxy
# endpoints. Read at use-time via ``_telegram_api_base()`` so a UI edit
# takes effect on the next send without restart.
_TELEGRAM_API_BASE_DEFAULT = "https://api.telegram.org"


def _telegram_api_base() -> str:
    """Resolve the Telegram Bot API base URL.

    Reads the operator-tunable ``telegram_api_base`` setting; falls
    back to the official upstream when blank. Trailing slashes are
    stripped so the per-send URL composition stays clean.
    """
    from logic.db import get_setting
    raw = (get_setting(Settings.TELEGRAM_API_BASE, "") or "").strip()
    if not raw:
        return _TELEGRAM_API_BASE_DEFAULT
    return raw.rstrip("/")


def _severity_emoji(severity: str) -> str:
    """Match the Apprise prefix convention so operators see the same
    visual key across both channels."""
    sev = (severity or "").strip().lower()
    if sev in ("error", "critical", "fatal"):
        return "🔴"
    if sev in ("warn", "warning"):
        return "🟡"
    if sev in ("success", "ok"):
        return "✅"
    return "ℹ️"


def _format_message(title: str, body: str, severity: str) -> str:
    """Render the Telegram message body.

    Title goes on the first line in bold (HTML parse_mode), body
    follows. Severity emoji prefixes the title for at-a-glance
    triage — but ONLY when the title doesn't already lead with an
    emoji of its own. Many notification templates carry a
    semantic-specific emoji (🔄 restart, 🗑 remove, 🧹 prune,
    🔓 sign-in, 🔍 scan, etc.) that ISN'T just a severity marker;
    prepending the severity emoji on top of those produced
    "✅ ✅ Stack updated: ..." style double-emoji output flagged
    by operators.
    """
    emoji = _severity_emoji(severity)
    title_clean = (title or "").strip()
    body_clean = (body or "").strip()
    # Telegram's HTML mode requires &, <, > escapes. Body stays
    # plain-text (no markdown rendering) so operator-authored
    # messages don't accidentally break parse_mode.
    def _esc(s: str) -> str:
        return (s.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;"))
    # Detect an existing leading emoji. Cheap heuristic: any non-ASCII
    # first character (codepoint > 0x7F) is treated as "title already
    # has its own marker — don't prepend ours". Catches every emoji /
    # symbol used in NOTIFY_TEMPLATE_DEFAULTS without enumerating them.
    has_leading_emoji = bool(title_clean) and ord(title_clean[0]) > 0x7F
    if has_leading_emoji:
        head = f"<b>{_esc(title_clean)}</b>"
    elif title_clean:
        head = f"{emoji} <b>{_esc(title_clean)}</b>"
    else:
        head = emoji
    if body_clean:
        return f"{head}\n{_esc(body_clean)}"
    return head


async def send(
    *,
    title: str,
    body: str,
    severity: str,
    event: str,
    actor_username: Optional[str] = None,
    target_kind: Optional[str] = None,
    target_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    # Caller-supplied overrides (kept for parity with future per-event
    # routing — phase 1 only ever uses the global settings values).
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> dict:
    """Send one Telegram message via the Bot API.

    Returns ``{"ok": bool, "detail": str, "status": int}`` matching the
    shape every other medium uses. Never raises.
    """
    # Lazy import — keeps module import cheap when Telegram isn't used.
    from logic.db import get_setting

    token = (bot_token or get_setting(Settings.TELEGRAM_BOT_TOKEN, "") or "").strip()
    chat = (chat_id or get_setting(Settings.TELEGRAM_CHAT_ID, "") or "").strip()
    thread = (thread_id or get_setting(Settings.TELEGRAM_THREAD_ID, "") or "").strip()
    verify_tls = (get_setting(Settings.TELEGRAM_VERIFY_TLS, "true") or "true").strip().lower() != "false"

    if not token:
        return {"ok": False, "detail": "telegram: bot token not configured", "status": 0}
    if not chat:
        return {"ok": False, "detail": "telegram: chat id not configured", "status": 0}

    text = _format_message(title, body, severity)
    payload: dict = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        # Don't preview link URLs in the message body — the rendered
        # notification stays compact in mobile clients.
        "disable_web_page_preview": True,
    }
    if thread:
        # Telegram expects the thread id as an int; tolerate a stringy
        # setting value here. Invalid (non-int) thread ids silently
        # downgrade to "post to the supergroup root" rather than failing
        # the send.
        try:
            payload["message_thread_id"] = int(thread)
        except (TypeError, ValueError):
            pass

    url = f"{_telegram_api_base()}/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(verify=verify_tls, timeout=15.0) as client:
            r = await client.post(url, json=payload)
        if r.status_code == 200:
            try:
                j = r.json()
                if isinstance(j, dict) and j.get("ok"):
                    print(f"[notify] telegram ok event={event!r} severity={severity}")
                    return {"ok": True, "detail": "sent", "status": 200}
                detail = (j.get("description") if isinstance(j, dict) else "") or "telegram returned ok=false"
                print(f"[notify] telegram failed event={event!r}: {detail}")
                return {"ok": False, "detail": f"telegram: {detail}", "status": 200}
            except (ValueError, TypeError):
                # Body wasn't JSON — Telegram returned HTTP 200 with an
                # unexpected body. Treat as success but log the oddity.
                print(f"[notify] telegram ok-but-not-json event={event!r}")
                return {"ok": True, "detail": "sent (non-JSON body)", "status": 200}
        # Non-200: pull Telegram's structured error if present.
        try:
            j = r.json()
            detail = (j.get("description") if isinstance(j, dict) else "") or f"HTTP {r.status_code}"
        except (ValueError, TypeError):
            detail = f"HTTP {r.status_code}"
        # 401 / 404 typically mean a bad token; 400 means a bad chat_id
        # or thread_id; 403 means the bot was kicked from the chat. All
        # of those are operator-fixable in Admin → Notifications.
        print(f"[notify] telegram failed event={event!r}: {detail}")
        return {"ok": False, "detail": f"telegram: {detail}", "status": r.status_code}
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001 — log + drop
        print(f"[notify] telegram exception event={event!r}: {e}")
        return {"ok": False, "detail": f"telegram: {e}", "status": 0}


async def probe(
    *,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> dict:
    """One-shot "Test connection" probe used by the admin Test button.

    Sends a minimal "OmniGrid connection test" message to the configured
    chat / thread. Returns the same ``{ok, detail, status}`` shape as
    ``send()``. The probe message is short and clearly labelled so it
    doesn't get mistaken for a real alert when the operator runs it
    from Admin → Notifications.
    """
    return await send(
        title="OmniGrid",
        body="Telegram test message — if you see this, the integration is wired correctly.",
        severity="info",
        event="telegram_test",
        bot_token=bot_token,
        chat_id=chat_id,
        thread_id=thread_id,
    )
