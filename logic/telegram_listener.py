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
import sqlite3
import time
from typing import Any, Optional

import httpx

from logic.settings_keys import (
    Settings,
    ai_provider_api_key_key,
    ai_provider_base_url_key,
    ai_provider_model_key,
)

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


def _resolved_token_and_chat() -> tuple[str, str]:
    """Pull bot-token + destination chat-id from the settings store."""
    from logic.db import get_setting
    token = (get_setting(Settings.TELEGRAM_BOT_TOKEN) or "").strip()
    chat = (get_setting(Settings.TELEGRAM_CHAT_ID) or "").strip()
    return token, chat


def _listener_enabled() -> bool:
    from logic.db import get_setting_bool
    return get_setting_bool(Settings.TELEGRAM_LISTENER_ENABLED)


def _allow_destructive() -> bool:
    from logic.db import get_setting_bool
    return get_setting_bool(Settings.TELEGRAM_ALLOW_DESTRUCTIVE)


def _authorized_user_ids() -> set[int]:
    """Parse the comma-separated allow-list of Telegram user_ids.

    Empty list means "any sender in the authorized chat is allowed"
    (chat-id gate is the only check). Non-empty list means commands
    are restricted to those user_ids regardless of chat membership.
    """
    from logic.db import get_setting
    raw = (get_setting(Settings.TELEGRAM_AUTHORIZED_USER_IDS) or "").strip()
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
        try:
            sender_int = int(sender_id)  # type: ignore[arg-type]  # narrowed via try/except
        except (TypeError, ValueError):
            return False, f"sender_id {sender_id!r} not coercible to int"
        if sender_int not in allow_list:
            return False, f"sender {sender_id} not in allow-list"
    return True, ""


def _reply_text(text: str) -> dict:
    """Build a sendMessage payload targeted at the configured chat.

    Re-uses Phase 1's HTML parse_mode + thread_id behaviour so replies
    land in the same forum topic the original command came from (when
    applicable).
    """
    from logic.db import get_setting
    chat = (get_setting(Settings.TELEGRAM_CHAT_ID) or "").strip()
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
    token, _ = _resolved_token_and_chat()
    if not token:
        return None
    try:
        r = await client.post(
            f"{_telegram_api_base()}/bot{token}/sendMessage",
            json=_reply_text(text),
            timeout=15.0,
        )
        if r.status_code != 200:
            print(f"[telegram_listener] reply failed: HTTP {r.status_code}")
            return None
        try:
            body = r.json() or {}
            msg_id = ((body.get("result") or {}).get("message_id"))
            return int(msg_id) if msg_id is not None else None  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return None
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    # noinspection PyBroadException
    except Exception as e:  # noqa: BLE001
        print(f"[telegram_listener] reply exception: {e}")
        return None


async def _send_chat_action(
    client: httpx.AsyncClient, action: str = "typing",
) -> None:
    """Fire the native Telegram "Bot is typing…" indicator. Lasts
    about 5 seconds on the client. For longer-running ops we ALSO
    send a placeholder reply that gets edited in place — the typing
    indicator is just immediate feedback while the placeholder is in
    flight. Fire-and-forget; never raises."""
    token, _ = _resolved_token_and_chat()
    if not token:
        return
    from logic.db import get_setting
    chat = (get_setting(Settings.TELEGRAM_CHAT_ID) or "").strip()
    thread = (get_setting(Settings.TELEGRAM_THREAD_ID) or "").strip()
    payload: dict = {"chat_id": chat, "action": action}
    if thread:
        try:
            payload["message_thread_id"] = int(thread)
        except (TypeError, ValueError):
            pass
    try:
        await client.post(
            f"{_telegram_api_base()}/bot{token}/sendChatAction",
            json=payload, timeout=5.0,
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (httpx.HTTPError, OSError):
        # Typing indicator is decorative — never surface the failure.
        pass


async def _edit_message(
    client: httpx.AsyncClient, message_id: int, text: str,
) -> bool:
    """Edit a previously-sent placeholder message in place. Used by
    the AI reply path to replace "🤖 Thinking…" with the final answer
    so the operator sees the response land in the same bubble. Caller
    is expected to have pre-truncated to 4096 chars. Returns True on
    success; on failure the caller falls back to a fresh ``_send_reply``."""
    token, _ = _resolved_token_and_chat()
    if not token or not message_id:
        return False
    from logic.db import get_setting
    chat = (get_setting(Settings.TELEGRAM_CHAT_ID) or "").strip()
    payload: dict = {
        "chat_id": chat,
        "message_id": int(message_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = await client.post(
            f"{_telegram_api_base()}/bot{token}/editMessageText",
            json=payload, timeout=10.0,
        )
        if r.status_code != 200:
            print(f"[telegram_listener] edit failed: HTTP {r.status_code}: {r.text[:200]}")
            return False
        return True
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    # noinspection PyBroadException
    except Exception as e:  # noqa: BLE001
        print(f"[telegram_listener] edit exception: {e}")
        return False


# ----------------------------------------------------------------------------
# Host resolver
# ----------------------------------------------------------------------------
def _audit_telegram(
    op_type: str,
    *,
    actor: str,
    target_id: str = "",
    target_name: str = "",
    status: str = "success",
    error: Optional[str] = None,
    events: Optional[dict] = None,
) -> None:
    """Write one row into the `history` table for a Telegram-issued
    state-mutating command. Read-only commands (/hosts / /version /
    /whoami / /myid / /weather / /time) are EXEMPT under the same
    "high-volume / low-stakes" carve-out as notification_read; only
    state-mutating commands (cleanup / restart / link / unlink) and
    the AI free-text path (recorded via record_ai_call) need audit
    rows. Never raises — a logging failure must not swallow the
    operator's command reply."""
    try:
        import json as _json
        import time as _time
        from logic.db import db_conn
        from logic.ops import assert_op_type
        assert_op_type(op_type)
        events_json = None
        if events:
            try:
                events_json = _json.dumps(events, ensure_ascii=False)
            except (TypeError, ValueError):
                events_json = None
        with db_conn() as c:
            c.execute(
                "INSERT INTO history ("
                "  ts, op_type, target_kind, target_name, target_id,"
                "  status, duration, events, error, actor"
                ") VALUES (?, ?, 'telegram', ?, ?, ?, 0.0, ?, ?, ?)",
                (
                    float(_time.time()), op_type,
                    target_name or "", target_id or "",
                    status, events_json, error,
                    actor or "telegram",
                ),
            )
            c.commit()
    # noinspection PyBroadException
    except Exception as _e:
        print(f"[telegram_listener] audit row write failed ({op_type}): {_e}")


def _load_hosts_config() -> list[dict]:
    """Read the curated host list from settings. Returns a list of
    dicts, NEVER raises — a malformed JSON setting just produces an
    empty list."""
    import json
    from logic.db import get_setting
    raw = (get_setting(Settings.HOSTS_CONFIG) or "").strip()
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
        with path.open(encoding="utf-8") as f:
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
    import json
    from logic.db import get_setting
    raw = (get_setting(Settings.TELEGRAM_USER_MAPPINGS) or "").strip()
    if not raw:
        return {}
    try:
        m = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(m, dict):
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


def _save_mappings(mappings: dict[str, dict]) -> None:
    """Persist the mapping dict back to settings. Quiet on failure.
    Always writes the new schema (``{username, linked_at_ms}`` per
    entry) so a legacy on-disk record gets upgraded on first write."""
    import json
    from logic.db import set_setting
    try:
        # Defensive normalisation in case a caller hands us a stale
        # string-shaped value.
        clean: dict[str, dict] = {}
        for tg_id, value in mappings.items():
            if isinstance(value, dict) and value.get("username"):
                clean[tg_id] = {
                    "username": value["username"],
                    "linked_at_ms": int(value.get("linked_at_ms") or 0),  # type: ignore[arg-type]  # `or 0` falls through to literal int when missing
                }
            elif isinstance(value, str) and value:
                clean[tg_id] = {"username": value, "linked_at_ms": 0}
        set_setting(Settings.TELEGRAM_USER_MAPPINGS, json.dumps(clean))
    except (TypeError, ValueError) as e:
        print(f"[telegram_listener] mapping save failed: {e}")


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
# noinspection PyUnusedLocal
async def _cmd_help(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """Auto-generated help — iterates `_COMMANDS` so adding a new
    handler shows up in `/help` with no extra wiring.

    Commands are grouped by ``category`` (each entry in `_COMMANDS`
    carries one) and each category renders as a bold section header
    with a category-specific emoji prefix so the menu scans at a
    glance. Within a category, commands sharing a handler are GROUPED
    — the primary command's usage stays as-is, and any alias names
    are appended in a comma-separated suffix on the same line
    (``/whoami, /myid``).
    """
    # Category metadata — ordered list so the rendered menu stays
    # stable + a single source of truth for the emoji + heading copy.
    # Adding a new category: append a tuple here AND tag every command
    # in `_COMMANDS` with the matching key.
    categories: list[tuple[str, str]] = [
        ("getting_started", "📖 Getting started"),
        ("fleet",           "🖥️ Fleet"),
        ("ops",             "⚙️ Operations"),
        ("account",         "🔗 Account"),
        ("info",            "ℹ️ Info & weather"),
        ("misc",             "🧩 Other"),
    ]
    cat_order = {key: idx for idx, (key, _) in enumerate(categories)}
    cat_headings = dict(categories)

    # First pass: group commands by handler (dedup aliases). Records
    # the FIRST occurrence as the primary for that handler — subsequent
    # entries become aliases regardless of `hidden`.
    groups: list[dict] = []
    handler_to_group: dict[Any, dict[str, Any]] = {}
    for name, meta in _COMMANDS.items():
        handler = meta.get("handler")
        if handler is None:
            continue
        existing = handler_to_group.get(handler)
        if existing is None:
            group = {
                "primary_name": name,
                "primary":      meta,
                "aliases":      [],
                "category":     meta.get("category") or "misc",
            }
            groups.append(group)
            handler_to_group[handler] = group
        else:
            existing["aliases"].append(name)

    # Bucket by category preserving original insertion order within each.
    by_cat: dict[str, list[dict]] = {}
    for g in groups:
        by_cat.setdefault(g["category"], []).append(g)

    lines = ["<b>🤖 OmniGrid Telegram commands</b>", ""]
    # Render categories in declared order; an unknown category (typo
    # or new tag without a heading) renders last under "Other" so it
    # surfaces visually instead of silently dropping.
    rendered_cats = sorted(
        by_cat.keys(),
        key=lambda c: cat_order.get(c, len(cat_order)),
    )
    for cat in rendered_cats:
        heading = cat_headings.get(cat, "🧩 Other")
        lines.append(f"<b>{_escape(heading)}</b>")
        for g in by_cat[cat]:
            primary_meta = g["primary"]
            primary_name = g["primary_name"]
            usage = _escape(primary_meta.get("usage") or primary_name)
            aliases = g["aliases"]
            if aliases:
                alias_text = ", ".join(_escape(a) for a in aliases)
                head = f"  <b>{usage}</b> <i>(aliases: {alias_text})</i>"
            else:
                head = f"  <b>{usage}</b>"
            description = _escape(primary_meta.get("description") or "")
            if description:
                lines.append(f"{head} — {description}")
            else:
                lines.append(head)
        lines.append("")  # blank line between categories

    lines.append(
        "<i>🎯 Targets resolve by IP, host id, label, or asset "
        "short-name. ⚠️ Destructive commands (e.g. /restart) require "
        "a typed confirm step unless 'Allow destructive Telegram "
        "commands' is enabled in Admin.</i>"
    )
    await _send_reply(client, "\n".join(lines))


def _load_host_paused_set() -> set[str]:
    """Read every host_id that has at least one row in
    `host_failure_state` (whole-host pauses OR per-provider pauses).
    Returns a set of bare host_ids — per-provider rows store the key
    as `<provider>:<host_id>` so we split on ':' and take the suffix.
    """
    from logic.db import db_conn
    paused: set[str] = set()
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT host_id FROM host_failure_state"
            ).fetchall()
    except (sqlite3.DatabaseError, sqlite3.OperationalError):
        return paused
    for row in rows:
        try:
            key = row["host_id"] if hasattr(row, "keys") else row[0]
        except (KeyError, IndexError):
            continue
        if not key:
            continue
        # Per-provider rows look like `snmp:web01` — strip the prefix
        # so the result is the bare host_id.
        if ":" in key:
            key = key.split(":", 1)[1]
        paused.add(key)
    return paused


# noinspection PyUnusedLocal
async def _cmd_hosts(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/hosts`` — split the curated fleet into two grouped lists:
    Active (enabled + no failure-state markers) and Down (disabled OR
    has at least one failure-state row, whole-host or per-provider).
    Each group caps at 50 rows with a `…and N more` overflow line so
    a large fleet still fits inside Telegram's 4096-char message cap.
    """
    hosts = _load_hosts_config()
    if not hosts:
        await _send_reply(client, "No curated hosts configured.")
        return
    paused_set = _load_host_paused_set()
    active: list[dict] = []
    down: list[dict] = []
    for h in hosts:
        enabled = h.get("enabled", True)
        hid = h.get("id") or ""
        if not enabled or hid in paused_set:
            down.append(h)
        else:
            active.append(h)

    def _render_row(host_row: dict, status_emoji: str) -> str:
        row_id = host_row.get("id") or "(no-id)"
        label = host_row.get("label") or row_id
        addr = host_row.get("address") or ""
        return (f"{status_emoji} <code>{_escape(row_id)}</code> — {_escape(label)}"
                + (f" ({_escape(addr)})" if addr else ""))

    out_lines: list[str] = [
        f"<b>Curated hosts</b> — {len(active)} active, {len(down)} down/disabled",
    ]

    # Active group — only render the heading + list when non-empty.
    if active:
        out_lines.append("")
        out_lines.append(f"🟢 <b>Active</b> ({len(active)})")
        for h in active[:50]:
            out_lines.append(_render_row(h, "🟢"))
        if len(active) > 50:
            out_lines.append(f"<i>…and {len(active) - 50} more.</i>")

    # Down / disabled group.
    if down:
        out_lines.append("")
        out_lines.append(f"🔴 <b>Down / disabled</b> ({len(down)})")
        for h in down[:50]:
            # Per-host emoji disambiguation: ⚪ for disabled-by-config,
            # 🔴 for actually-failing. Matches the original
            # `_host_status_emoji` semantics so operators reading the
            # reply can distinguish "we turned this off" vs "this is
            # broken".
            emoji = "⚪" if not h.get("enabled", True) else "🔴"
            out_lines.append(_render_row(h, emoji))
        if len(down) > 50:
            out_lines.append(f"<i>…and {len(down) - 50} more.</i>")

    await _send_reply(client, "\n".join(out_lines))


def _fmt_uptime(seconds: float | int | None) -> str:
    """Render an uptime span as ``Xd Yh`` / ``Xh Ym`` / ``Xm Ys``."""
    # Explicit None check so the type-checker narrows the Optional
    # before the `< 0` comparison.
    if seconds is None or not seconds or seconds < 0:
        return ""
    s = int(seconds)
    if s >= 86400:
        d = s // 86400
        h = (s % 86400) // 3600
        return f"{d}d {h}h"
    if s >= 3600:
        h = s // 3600
        m = (s % 3600) // 60
        return f"{h}h {m}m"
    if s >= 60:
        m = s // 60
        sec = s % 60
        return f"{m}m {sec}s"
    return f"{s}s"


def _fmt_bytes(n: float | int | None) -> str:
    """Render a byte count as the largest sensible unit (GB / MB / KB / B)."""
    # Explicit None check before the `< 0` comparison so the type-checker
    # narrows the Optional (PyCharm flagged 4× "Member 'None' of
    # 'float | int | None' does not have attribute '__ge__' / __truediv__'"
    # because `if not n` doesn't narrow `int | None` → `int` for it).
    if n is None or not n or n < 0:
        return ""
    # Re-bind to a non-Optional local — `n = float(n)` would re-use the
    # Optional-typed name and pyright wouldn't propagate the narrowing
    # past the try/except boundary.
    try:
        nf: float = float(n)
    except (TypeError, ValueError):
        return ""
    if nf >= 1024 ** 4:
        return f"{nf / 1024 ** 4:.1f} TB"
    if nf >= 1024 ** 3:
        return f"{nf / 1024 ** 3:.1f} GB"
    if nf >= 1024 ** 2:
        return f"{nf / 1024 ** 2:.1f} MB"
    if nf >= 1024:
        return f"{nf / 1024:.0f} KB"
    return f"{int(nf)} B"


def _fmt_age(ts: float | int | None) -> str:
    """Render "Updated Xs/m/h ago" relative to now."""
    if not ts:
        return ""
    import time as _t
    age = max(0, int(_t.time() - float(ts)))
    if age < 60:
        return f"{age} seconds ago"
    if age < 3600:
        return f"{age // 60} minutes ago"
    if age < 86400:
        return f"{age // 3600} hours ago"
    return f"{age // 86400} days ago"


# noinspection PyUnusedLocal
async def _cmd_host(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/host <target>`` — probe live, then show fresh stats for one
    curated host (CPU / memory / disk / uptime + extended provider
    stats when present). Strategy: send a "🔄 Probing live data…"
    placeholder immediately, run the same per-host live-merge path the
    SPA's `/api/hosts/one/{id}` uses (lazy-imported from main to side-
    step the circular import — the listener is started by main's
    lifespan, so `main` is already loaded by the time this fires), then
    EDIT the placeholder with the final data. Falls back to the cached
    `host_snapshots` row when the live probe raises (so a hub outage or
    auth failure still yields a useful reply).

    Target resolution reuses the same fuzzy-match resolver `/restart`
    uses (id / label / address / per-provider aliases).
    """
    if not args:
        await _send_reply(client, "Usage: <code>/host &lt;target&gt;</code>")
        return
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

    host_id = matched.get("id") or ""
    label = matched.get("label") or host_id

    # Placeholder reply — operator sees acknowledgement immediately
    # while the live merge fans out across every configured provider
    # for this host (NE + Webmin + SNMP inline; Beszel + Pulse from
    # the cached batch maps). Capture the message_id so we can edit
    # in place when the final data is ready; if the send fails (rate
    # limit, transient HTTP), we fall through and the final body
    # arrives as a new message.
    placeholder_id = await _send_reply(
        client,
        f"🔄 Probing live providers for <b>{_escape(label)}</b>…",
    )

    # Live per-host merge — same code path /api/hosts/one/{id} runs.
    # Lazy-import dodges the circular dependency (main → listener via
    # lifespan; listener → main only at call-time). On any exception
    # (settings missing, hub outage, timeout) we fall back to the
    # cached snapshot below so the operator still gets a reply.
    data: dict | None = None
    snap_ts: float | None = None
    live_ok = False
    try:
        from main import _merge_one_host, _get_host_provider_state  # lazy
        state = await _get_host_provider_state(force=True)
        merged, _hits = await _merge_one_host(matched, state, force=True)
        if isinstance(merged, dict) and merged:
            data = merged
            snap_ts = float(time.time())
            live_ok = True
    # noinspection PyBroadException
    except Exception as e:  # noqa: BLE001
        print(f"[telegram_listener] /host live merge failed for {host_id!r}: {e}")

    # Snapshot fallback — same shape as the pre-live-merge implementation.
    # Triggers when the live probe raised OR returned empty.
    if not live_ok:
        try:
            from logic.gather import load_host_snapshots
            snap_map = load_host_snapshots()
        # noinspection PyBroadException
        except Exception as e:
            err = f"❌ Snapshot read failed: <code>{_escape(str(e))}</code>"
            if placeholder_id is not None:
                ok = await _edit_message(client, placeholder_id, err)
                if not ok:
                    await _send_reply(client, err)
            else:
                await _send_reply(client, err)
            return
        entry = snap_map.get(host_id) or {}
        if isinstance(entry, dict):
            _ts = entry.get("ts")
            snap_ts = float(_ts) if isinstance(_ts, (int, float)) else None
            data = entry.get("data")
    if not isinstance(data, dict) or not data:
        warn = (f"⚠️ No readings for <b>{_escape(label)}</b> yet. "
                f"Wait for the next probe cycle and try again.")
        if placeholder_id is not None:
            ok = await _edit_message(client, placeholder_id, warn)
            if not ok:
                await _send_reply(client, warn)
        else:
            await _send_reply(client, warn)
        return

    out: list[str] = [f"📊 <b>{_escape(label)}</b> "
                      f"(<code>{_escape(host_id)}</code>)"]

    # Optional system identity sub-line.
    plat = data.get("host_platform") or ""
    kern = data.get("host_kernel") or ""
    if plat or kern:
        bits = [b for b in (plat, kern) if b]
        out.append(f"<i>{_escape(' · '.join(bits))}</i>")
    out.append("")

    # ---- Core stats: CPU / memory / disk / uptime ------------------
    def _fmt_pct(v):
        if v is None:
            return None
        try:
            return f"{float(v):.1f}%"
        except (TypeError, ValueError):
            return None

    cpu_p = _fmt_pct(data.get("host_cpu_percent"))
    if cpu_p:
        out.append(f"🖥 <b>CPU:</b>   {cpu_p}")
    mem_p = _fmt_pct(data.get("host_mem_percent"))
    mem_used = data.get("host_mem_used")
    mem_total = data.get("host_mem_total")
    if mem_p or mem_used:
        mu = _fmt_bytes(mem_used)
        mt = _fmt_bytes(mem_total)
        if mu and mt:
            out.append(f"💾 <b>Memory:</b> {mu} / {mt}" + (f"  ({mem_p})" if mem_p else ""))
        elif mem_p:
            out.append(f"💾 <b>Memory:</b> {mem_p}")
    disk_p = _fmt_pct(data.get("host_disk_percent"))
    disk_used = data.get("host_disk_used")
    disk_total = data.get("host_disk_total")
    if disk_p or disk_used:
        du = _fmt_bytes(disk_used)
        dt = _fmt_bytes(disk_total)
        if du and dt:
            out.append(f"💿 <b>Disk:</b>   {du} / {dt}" + (f"  ({disk_p})" if disk_p else ""))
        elif disk_p:
            out.append(f"💿 <b>Disk:</b>   {disk_p}")
    uptime_str = _fmt_uptime(data.get("host_uptime_seconds"))
    if uptime_str:
        out.append(f"⏱ <b>Uptime:</b> {uptime_str}")
    # Ping reachability — RTT in ms when alive, loss% when not.
    ping_alive = data.get("host_ping_alive")
    ping_rtt = data.get("host_ping_rtt_ms")
    ping_loss = data.get("host_ping_loss_pct")
    if ping_alive is True and isinstance(ping_rtt, (int, float)):
        loss_seg = (f", {float(ping_loss):.0f}% loss"
                    if isinstance(ping_loss, (int, float)) and ping_loss > 0 else "")
        out.append(f"📡 <b>Ping:</b>   {float(ping_rtt):.1f} ms{loss_seg}")
    elif ping_alive is False:
        out.append(f"📡 <b>Ping:</b>   unreachable")

    # ---- Extended stats — only emit sections with meaningful data --
    extended: list[str] = []
    l1 = data.get("host_load_1m")
    l5 = data.get("host_load_5m")
    l15 = data.get("host_load_15m")
    if any(isinstance(v, (int, float)) and v > 0 for v in (l1, l5, l15)):
        load_bits = [f"{float(v):.2f}" for v in (l1, l5, l15) if isinstance(v, (int, float))]
        extended.append(f"📈 <b>Load:</b>   {', '.join(load_bits)}")
    swap_p = _fmt_pct(data.get("host_swap_percent"))
    swap_used = data.get("host_swap_used")
    if swap_p and (data.get("host_swap_percent") or 0) > 0:
        # Swap_used in Beszel is GB; render directly.
        if isinstance(swap_used, (int, float)) and swap_used > 0:
            extended.append(f"🔄 <b>Swap:</b>   {swap_p}  ({swap_used:.1f} GB used)")
        else:
            extended.append(f"🔄 <b>Swap:</b>   {swap_p}")
    bw = data.get("host_bandwidth")
    if isinstance(bw, (int, float)) and bw > 0:
        extended.append(f"🌐 <b>Bandwidth:</b> {_fmt_bytes(bw)}/s")
    # Cumulative network counters (total throughput since boot / counter reset).
    rx_total = data.get("host_net_rx_total") or data.get("host_net_rx_total_bytes")
    tx_total = data.get("host_net_tx_total") or data.get("host_net_tx_total_bytes")
    if isinstance(rx_total, (int, float)) and isinstance(tx_total, (int, float)) \
        and (rx_total > 0 or tx_total > 0):
        extended.append(
            f"📊 <b>Net total:</b> ↓ {_fmt_bytes(rx_total)} / ↑ {_fmt_bytes(tx_total)}"
        )
    # Temperatures — Beszel emits a list of {name, temp_c} after _flatten.
    temps = data.get("host_temperatures")
    if isinstance(temps, list) and temps:
        # Cap at 5 sensors so the message stays readable.
        bits = []
        for t in temps[:5]:
            if not isinstance(t, dict):
                continue
            tn = t.get("name") or t.get("sensor") or ""
            tc = t.get("temp_c") or t.get("c") or t.get("value")
            if tn and isinstance(tc, (int, float)):
                bits.append(f"{_escape(str(tn))} {tc:.0f}°C")
        if bits:
            extra = f" + {len(temps) - 5} more" if len(temps) > 5 else ""
            extended.append(f"🌡 <b>Temp:</b>   " + ", ".join(bits) + extra)
    # GPUs
    gpus = data.get("host_gpus")
    if isinstance(gpus, list) and gpus:
        bits = []
        for g in gpus[:3]:
            if not isinstance(g, dict):
                continue
            name = g.get("name") or g.get("n") or "GPU"
            util = g.get("usage_pct") or g.get("u")
            seg = _escape(str(name))
            if isinstance(util, (int, float)):
                seg += f" {float(util):.0f}%"
            bits.append(seg)
        if bits:
            extended.append(f"🎮 <b>GPU:</b>    " + ", ".join(bits))
    # Containers
    ct = data.get("host_containers")
    if isinstance(ct, int) and ct > 0:
        extended.append(f"🐳 <b>Containers:</b> {ct}")
    # Services summary (Beszel systemd_services rollup)
    svcs = data.get("host_services")
    if isinstance(svcs, dict) and (svcs.get("total") or 0) > 0:
        total = int(svcs.get("total") or 0)
        failed = int(svcs.get("failed") or 0)
        if failed > 0:
            extended.append(f"⚙️ <b>Services:</b> {failed} failed / {total} total")
        else:
            extended.append(f"⚙️ <b>Services:</b> {total} healthy")
    # Pending package updates (Webmin)
    pkg_count = data.get("package_updates_count")
    if isinstance(pkg_count, int) and pkg_count > 0:
        extended.append(f"📦 <b>Updates:</b> {pkg_count} pending")
    # UPS (SNMP / PowerNet) — full card: output status, battery %, output
    # load, runtime remaining, battery temperature, battery state.
    # Field names match `logic/snmp.py`'s APC extractor: host_ups_status,
    # host_battery_percent, host_battery_temp_c, host_battery_runtime_s,
    # host_battery_status, host_load_percent.
    ups_status = data.get("host_ups_status")
    bat_pct = data.get("host_battery_percent")
    load_pct = data.get("host_load_percent")
    runtime_s = data.get("host_battery_runtime_s")
    bat_temp = data.get("host_battery_temp_c")
    bat_state = data.get("host_battery_status")
    if ups_status or isinstance(bat_pct, (int, float)) or isinstance(load_pct, (int, float)):
        if ups_status:
            extended.append(f"🔋 <b>UPS:</b>    {_escape(str(ups_status))}")
        if isinstance(bat_pct, (int, float)):
            extended.append(f"   <b>Battery:</b>   {float(bat_pct):.0f}%")
        if isinstance(load_pct, (int, float)):
            extended.append(f"   <b>Output load:</b> {float(load_pct):.0f}%")
        runtime_str = _fmt_uptime(runtime_s) if isinstance(runtime_s, (int, float)) else ""
        if runtime_str:
            extended.append(f"   <b>Runtime:</b>   {runtime_str}")
        if isinstance(bat_temp, (int, float)):
            extended.append(f"   <b>Battery temp:</b> {float(bat_temp):.0f}°C")
        if bat_state:
            extended.append(f"   <b>Battery state:</b> {_escape(str(bat_state))}")

    if extended:
        out.append("")
        out.extend(extended)

    # ---- Footer: last-updated marker ------------------------------
    # Two states: LIVE (we just probed every provider — age is seconds)
    # vs SNAPSHOT (live probe failed, we fell back to host_snapshots —
    # age is whatever the snapshot row says). The marker copy makes the
    # source obvious so the operator can tell at a glance whether they
    # need to investigate a stale snapshot or trust the values.
    out.append("")
    if live_ok:
        out.append("<i>Live probe — just now</i>")
    else:
        age = _fmt_age(snap_ts)
        if age:
            out.append(f"<i>Updated {age} (cached snapshot — live probe failed)</i>")
        else:
            out.append("<i>Cached snapshot — live probe failed</i>")

    body = "\n".join(out)
    # Edit the placeholder in place when we have its message_id; falls
    # back to a fresh reply on edit failure (rate limit, message too
    # old, etc.). Same pattern as the AI reply path's "🤖 Thinking…"
    # placeholder handling.
    if placeholder_id is not None:
        ok = await _edit_message(client, placeholder_id, body)
        if not ok:
            await _send_reply(client, body)
    else:
        await _send_reply(client, body)


# noinspection PyUnusedLocal
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
    result = await _ssh.run_command(host_id, cmd, hosts, timeout=15.0)
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


# noinspection PyUnusedLocal
async def _cmd_version(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/version`` (aliased as ``/ver``) — show the running OmniGrid
    version. Reads the version baked into the image at build time
    (`/app/VERSION.txt` populated by the deploy pipeline's
    ``--build-arg VERSION=<X.Y.Z>``). Non-sensitive — works pre-link
    so unmapped operators can confirm which build they're talking to."""
    try:
        from logic.version import read_version
        version = read_version()
    # noinspection PyBroadException
    except Exception as e:
        await _send_reply(client, f"❌ Version lookup failed: <code>{_escape(str(e))}</code>")
        return
    if not version or version == "0.0.0-dev":
        # Dev build (no --build-arg VERSION passed) — call it out so
        # the operator knows they're not on a tagged release.
        await _send_reply(
            client,
            f"📦 OmniGrid <b><code>{_escape(version or '0.0.0-dev')}</code></b>\n"
            f"<i>Unversioned build — built locally without "
            f"<code>--build-arg VERSION</code>.</i>"
        )
        return
    await _send_reply(
        client,
        f"📦 OmniGrid <b><code>{_escape(version)}</code></b>"
    )


# noinspection PyUnusedLocal
async def _cmd_ip(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/ip`` — show the deployment's public IP + ISP / ASN / country
    via the same lookup the AI palette uses (ifconfig.co JSON). Gated
    on the `tuning_public_ip_enabled` tunable (default OFF for
    privacy); refuses cleanly with a link to Admin → Public IP when
    off. Non-sensitive command — works pre-link so unmapped operators
    can confirm the deploy's external network identity for support
    purposes."""
    from logic import public_ip as _public_ip
    if not _public_ip.is_enabled():
        await _send_reply(
            client,
            "🔒 Public-IP lookup is disabled. Enable "
            "<code>tuning_public_ip_enabled</code> in OmniGrid → "
            "Admin → Public IP first (it gates the outbound "
            "ifconfig.co call)."
        )
        return
    data = await _public_ip.fetch()
    if data is None:
        await _send_reply(
            client,
            "❌ Public-IP lookup failed (network blip or ifconfig.co "
            "outage). Check Admin → Logs for the [public_ip] line."
        )
        return
    bits: list[str] = []
    if data.get("ip"):
        bits.append(f"🌐 <b>IP:</b>      <code>{_escape(data['ip'])}</code>")
    if data.get("isp"):
        bits.append(f"🏢 <b>ISP:</b>     {_escape(data['isp'])}")
    if data.get("asn"):
        bits.append(f"🔢 <b>ASN:</b>     {_escape(data['asn'])}")
    if data.get("city") or data.get("country"):
        loc_parts: list[str] = []
        for field in ("city", "country"):
            v = data.get(field)
            if isinstance(v, str) and v.strip():
                loc_parts.append(v)
        if loc_parts:
            bits.append(f"📍 <b>Location:</b> {_escape(', '.join(loc_parts))}")
    if not bits:
        await _send_reply(
            client,
            "⚠️ Public-IP lookup returned empty — ifconfig.co may have "
            "rate-limited or changed its schema."
        )
        return
    await _send_reply(client, "\n".join(bits))


# noinspection PyUnusedLocal
async def _cmd_whoami(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """Debug aid — tells the user their Telegram user_id + the
    OmniGrid username they're linked to (or that they aren't) + their
    access level (role). Aliased as /myid."""
    sender = (msg.get("from") or {})
    sender_id = sender.get("id")
    sender_name = (sender.get("username") or sender.get("first_name") or "").strip()
    mapped = _lookup_omnigrid_user(sender_id) if sender_id is not None else None
    if mapped:
        role = _lookup_user_role(mapped) or "unknown"
        # Map the role to a friendly access-level label + emoji so the
        # operator's permissions are immediately legible.
        role_emoji = {"admin": "🛡", "readonly": "👁"}.get(role, "❓")
        role_label = {
            "admin": "Admin (full access)",
            "readonly": "Read-only (no write actions)",
        }.get(role, role)
        await _send_reply(
            client,
            f"You are linked to OmniGrid user <b>{_escape(mapped)}</b>.\n"
            f"{role_emoji} Access level: <b>{_escape(role_label)}</b>\n"
            f"<i>Telegram user_id: <code>{sender_id}</code></i>"
        )
    else:
        await _send_reply(
            client,
            f"You aren't linked to any OmniGrid user yet.\n\n"
            f"<i>Telegram user_id: <code>{sender_id}</code></i>\n"
            f"<i>Telegram username: @{_escape(sender_name) or 'unknown'}</i>\n"
            f"❓ Access level: <b>none</b> — unlinked\n\n"
            f"Generate a link code in OmniGrid → Profile → Telegram, then "
            f"reply with <code>/link &lt;code&gt;</code>."
        )


# noinspection PyUnusedLocal
async def _cmd_time(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/time`` — show the current local time at the linked user's
    saved weather location. Uses Open-Meteo's resolved IANA timezone
    (returned alongside the weather response) so daylight-saving + tz
    boundaries stay accurate without a separate geocoder lookup."""
    sender_id = (msg.get("from") or {}).get("id")
    username = _lookup_omnigrid_user(sender_id) if sender_id is not None else None
    if not username:
        await _send_reply(
            client,
            "Link your account first. Generate a code in OmniGrid → "
            "Profile → Telegram, then reply with <code>/link &lt;code&gt;</code>."
        )
        return
    loc = _load_user_weather_pref(username)
    if loc is None:
        await _send_reply(
            client,
            f"OmniGrid user <b>{_escape(username)}</b> has no weather "
            f"location saved. Open the topbar weather widget in "
            f"OmniGrid → click a city → Save."
        )
        return
    from main import api_weather as _api_weather
    label = (loc.get("label") or "").strip() or "your location"
    try:
        data = await _api_weather(
            lat=float(loc["lat"]),
            lon=float(loc["lon"]),
            label=label,
        )
    # noinspection PyBroadException
    except Exception as e:
        await _send_reply(client, f"❌ Time lookup failed: <code>{_escape(str(e))}</code>")
        return
    if not isinstance(data, dict) or data.get("error"):
        err = (data or {}).get("error") if isinstance(data, dict) else "no response"
        await _send_reply(
            client,
            f"❌ Time lookup upstream error: <code>{_escape(str(err))}</code>"
        )
        return
    # api_weather's untyped return-shape lets dict values widen to
    # `str | bool | None` in pyright's view; coerce to str at the
    # boundary so .strip() / index access below stay well-typed.
    tz_name = str(data.get("timezone") or "").strip()
    tz_abbrev = str(data.get("timezone_abbrev") or "").strip()
    if not tz_name:
        await _send_reply(
            client,
            f"<b>{_escape(label)}</b>: no timezone returned by the "
            f"weather upstream. Try again later."
        )
        return
    # Render local time using zoneinfo for accurate DST handling. If
    # the IANA tz isn't installed in the container (rare — Python 3.9+
    # ships with zoneinfo + the OS-provided tzdata), fall back to the
    # UTC offset Open-Meteo returned. Format follows the user's
    # `ui_prefs.datetime_format` preference so the Telegram render
    # matches what they see in the SPA — same token grammar via the
    # shared `logic.datetime_fmt` module.
    from logic.datetime_fmt import (
        apply_datetime_format as _apply_fmt,
        get_user_datetime_format as _get_user_fmt,
    )
    user_fmt = _get_user_fmt(username)
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now_local = datetime.now(ZoneInfo(tz_name))
        offset_note = ""
    except (ImportError, KeyError, ValueError):
        # Fallback: utc_offset_seconds-based math, ignoring DST.
        from datetime import datetime, timezone, timedelta
        try:
            offset = int(data.get("utc_offset_seconds") or 0)
            now_local = datetime.now(timezone.utc) + timedelta(seconds=offset)
            offset_note = " (UTC offset — IANA tz unavailable)"
        # noinspection PyBroadException
        except Exception as e2:
            await _send_reply(client, f"❌ Time format failed: <code>{_escape(str(e2))}</code>")
            return
    formatted = _apply_fmt(now_local, user_fmt)
    tz_suffix = f" ({_escape(tz_abbrev)})" if tz_abbrev else f" ({_escape(tz_name)})"
    await _send_reply(
        client,
        f"🕒 <b>{_escape(label)}</b>\n"
        f"<code>{_escape(formatted)}</code>{tz_suffix}{_escape(offset_note)}"
    )


# noinspection PyUnusedLocal
async def _cmd_cleanup(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/cleanup`` (dry-run) — list every stopped / failed / orphan
    container the dashboard's cleanup button would remove. ``/cleanup
    confirm`` actually fires the removals as background Operations,
    same path the SPA's topbar "Cleanup N" button uses. Every removal
    publishes ``op:created`` / ``op:updated`` / ``op:completed`` SSE
    events + invalidates the gather cache, so every open SPA tab
    auto-refreshes within seconds of a Telegram-driven cleanup.

    Gating:
      - mapping gate (already enforced at the dispatcher level — only
        mapped senders reach this handler)
      - destructive gate: ``telegram_allow_destructive=true`` OR the
        operator re-sends ``/cleanup confirm`` to skip the prompt
    """
    is_confirm = bool(args) and args[0].lower() == "confirm"

    # Read the live gather cache for the removable set. `_cache["items"]`
    # is populated by the regular gather loop; if it's empty (cold start)
    # we trigger a refresh inline so the operator gets data on first
    # call rather than an unhelpful "nothing to clean up".
    try:
        from logic import gather as _gather
    # noinspection PyBroadException
    except Exception as e:
        await _send_reply(client, f"❌ gather import failed: <code>{_escape(str(e))}</code>")
        return
    # noinspection PyProtectedMember
    items = list(_gather._cache.get("items") or [])
    removables = [i for i in items if i.get("removable")]

    if not removables:
        await _send_reply(
            client,
            "✅ Nothing to clean up — no stopped / failed / orphan "
            "containers in the current snapshot."
        )
        return

    # Destructive gate
    if not is_confirm and not _allow_destructive():
        # Render preview list, prompt for /cleanup confirm.
        lines = [
            f"🧹 <b>{len(removables)} container(s) eligible for cleanup</b>",
            "",
        ]
        # Group by stack for readability — matches the SPA's grouping.
        by_stack: dict[str, list[dict]] = {}
        for i in removables:
            stack = i.get("stack") or "(no stack)"
            by_stack.setdefault(stack, []).append(i)
        # Cap visible items so the message stays under Telegram's 4096
        # char wire limit. 40 is comfortable headroom.
        shown = 0
        max_shown = 40
        for stack in sorted(by_stack.keys()):
            group = by_stack[stack]
            lines.append(f"<b>{_escape(stack)}</b>")
            for i in group:
                if shown >= max_shown:
                    break
                name = i.get("name") or i.get("raw_id") or "(unknown)"
                kind = i.get("type") or "container"
                tag = "orphan" if kind == "orphan" else "stopped"
                lines.append(f"  • <code>{_escape(name)}</code> "
                             f"<i>[{tag}]</i>")
                shown += 1
            if shown >= max_shown:
                break
        if len(removables) > shown:
            lines.append(f"<i>…and {len(removables) - shown} more.</i>")
        lines.append("")
        lines.append(
            "⚠️ Reply with <code>/cleanup confirm</code> to remove all "
            f"{len(removables)} container(s).\n"
            "<i>(Or enable 'Allow destructive Telegram commands' in "
            "Admin → Notifications → Telegram to skip this step.)</i>"
        )
        await _send_reply(client, "\n".join(lines))
        return

    # Execute path — same in-process Operations pipeline the SPA uses.
    # Resolve the actor (linked OmniGrid username) so the history rows
    # the Ops persist carry the right attribution.
    sender_id = (msg.get("from") or {}).get("id")
    actor = _lookup_omnigrid_user(sender_id) if sender_id is not None else None
    actor = actor or "telegram"

    try:
        from logic import ops as _ops_mod
    # noinspection PyBroadException
    except Exception as e:
        await _send_reply(client, f"❌ ops import failed: <code>{_escape(str(e))}</code>")
        return

    await _send_reply(
        client,
        f"🧹 Removing {len(removables)} container(s)… "
        f"<i>(SPA tabs will refresh as each one completes)</i>"
    )

    spawned = 0
    for i in removables:
        raw_id = i.get("raw_id") or ""
        if not raw_id:
            continue
        name = i.get("name") or raw_id[:12]
        stack = i.get("stack")
        try:
            op = _ops_mod.new_op(
                "remove_container", raw_id, name,
                target_stack=stack, actor=actor,
            )
            # Fire-and-forget — each op publishes its own SSE events as
            # it progresses (op:created / op:updated / op:completed)
            # and invalidates the gather cache on completion, which is
            # exactly what the SPA listens for.
            asyncio.create_task(
                _ops_mod.do_remove_container(op, raw_id),
                name=f"telegram-cleanup-{raw_id[:12]}",
            )
            spawned += 1
        # noinspection PyBroadException
        except Exception as e:
            print(f"[telegram_listener] spawn remove for {raw_id[:12]} failed: {e}")

    # Per-container `remove_container` ops already write their own
    # history rows via the do_remove_container path; the dispatcher-
    # level `telegram_command` row covers the batch entry-point.
    await _send_reply(
        client,
        f"✅ Spawned {spawned} cleanup Operation(s). Watch the SPA's "
        f"Live panel or History tab to follow progress."
    )


# noinspection PyUnusedLocal
async def _cmd_link(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/link <code>`` — bind the sender's Telegram user_id to an
    OmniGrid user. Code is minted by the SPA's Profile section and
    valid for 15 minutes, single-use."""
    sender_id = (msg.get("from") or {}).get("id")
    if sender_id is None:
        await _send_reply(client, "Can't read your Telegram user_id from the message.")
        return
    # Narrow sender_id from `Any | None` to int once at function entry
    # so every downstream int()-call site stays well-typed.
    try:
        sender_id_int: int = int(sender_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        await _send_reply(client, "Telegram user_id is not numeric — refusing.")
        return
    # If the sender is already linked, refuse and point them at
    # /unlink — re-linking without unlinking first would silently
    # overwrite the existing mapping, which is confusing if the
    # operator forgot they were already linked or if multiple users
    # share the same Telegram account (rare but observed). Same
    # short-circuit whether they typed `/link` bare OR `/link <code>`.
    existing_username = _lookup_omnigrid_user(sender_id_int)
    if existing_username:
        await _send_reply(
            client,
            f"ℹ️ You're already linked to OmniGrid user "
            f"<b>{_escape(existing_username)}</b>. Run "
            f"<code>/unlink</code> first if you want to re-link "
            f"with a fresh code."
        )
        return
    if not args:
        await _send_reply(client, "Usage: <code>/link &lt;code&gt;</code>")
        return
    code = args[0].strip()
    username = _consume_link_code(code)
    if not username:
        await _send_reply(
            client,
            "❌ Invalid or expired link code. Generate a fresh one in "
            "OmniGrid → Profile → Telegram and try again."
        )
        return
    import time as _time
    linked_at_ms = int(_time.time() * 1000)
    mappings = _load_mappings()
    mappings[str(sender_id_int)] = {
        "username": username,
        "linked_at_ms": linked_at_ms,
    }
    _save_mappings(mappings)
    # SSE event so the SPA's Profile → Telegram card flips from
    # "Generate code" to the linked-state banner without a manual
    # page reload. Payload carries `username` so the SPA can scope
    # the refresh to the matching tab.
    try:
        from logic import events as _events
        _events.publish("telegram:linked", {
            "username": username,
            "telegram_user_id": sender_id_int,
            "linked_at_ms": linked_at_ms,
        })
    # noinspection PyBroadException
    except Exception as _e:
        print(f"[telegram_listener] publish telegram:linked failed: {_e}")
    await _send_reply(
        client,
        f"✅ Linked to OmniGrid user <b>{_escape(username)}</b>. "
        f"You can now run authenticated commands."
    )


# noinspection PyUnusedLocal
async def _cmd_unlink(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/unlink`` — drop the sender's Telegram → OmniGrid mapping."""
    sender_id = (msg.get("from") or {}).get("id")
    if sender_id is None:
        await _send_reply(client, "Can't read your Telegram user_id from the message.")
        return
    try:
        sender_id_int: int = int(sender_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        await _send_reply(client, "Telegram user_id is not numeric — refusing.")
        return
    mappings = _load_mappings()
    key = str(sender_id_int)
    if key not in mappings:
        await _send_reply(client, "You weren't linked. Nothing to unlink.")
        return
    removed = mappings.pop(key)
    _save_mappings(mappings)
    # Mapping schema is `{username, linked_at_ms}` post-migration;
    # legacy entries may still be a bare username string.
    removed_username: str = str(
        removed.get("username") if isinstance(removed, dict) else removed
    )
    # SSE event so any OmniGrid tab the operator has open re-renders
    # the Profile → Telegram card without a manual reload.
    try:
        from logic import events as _events
        _events.publish("telegram:unlinked", {
            "username": removed_username,
            "telegram_user_id": sender_id_int,
        })
    # noinspection PyBroadException
    except Exception as _e:
        print(f"[telegram_listener] publish telegram:unlinked failed: {_e}")
    await _send_reply(
        client,
        f"✅ Unlinked from OmniGrid user <b>{_escape(removed_username)}</b>. "
        f"Re-link via Profile → Telegram in OmniGrid."
    )


# noinspection PyUnusedLocal
async def _cmd_weather(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/weather`` — fetch the linked OmniGrid user's saved weather
    location and return current conditions + a 3-day forecast snippet.
    """
    sender_id = (msg.get("from") or {}).get("id")
    username = _lookup_omnigrid_user(sender_id) if sender_id is not None else None
    if not username:
        await _send_reply(
            client,
            "Link your account first. Generate a code in OmniGrid → "
            "Profile → Telegram, then reply with <code>/link &lt;code&gt;</code>."
        )
        return
    loc = _load_user_weather_pref(username)
    if loc is None:
        await _send_reply(
            client,
            f"OmniGrid user <b>{_escape(username)}</b> has no weather "
            f"location saved. Open the topbar weather widget in "
            f"OmniGrid → click a city → Save."
        )
        return
    # Re-use the existing /api/weather upstream by calling the
    # in-process handler. We could call the API endpoint over HTTP,
    # but that adds an unnecessary round-trip when both run in the
    # same process.
    from main import api_weather as _api_weather  # local import to avoid circular at module load
    label = (loc.get("label") or "").strip() or "weather"
    unit = loc.get("unit") or "c"
    try:
        data = await _api_weather(
            lat=loc["lat"],
            lon=loc["lon"],
            label=label,
        )
    # noinspection PyBroadException
    except Exception as e:
        await _send_reply(client, f"❌ Weather lookup failed: <code>{_escape(str(e))}</code>")
        return
    if not isinstance(data, dict) or data.get("error"):
        err = (data or {}).get("error") if isinstance(data, dict) else "no response"
        await _send_reply(
            client,
            f"❌ Weather upstream error: <code>{_escape(str(err))}</code>"
        )
        return

    def _fmt_temp(c_val):
        """Render a Celsius temperature in the user's preferred unit.
        `api_weather` always returns Celsius; convert at render time."""
        if c_val is None:
            return None
        try:
            celsius = float(c_val)
        except (TypeError, ValueError):
            return None
        if unit == "f":
            return f"{round(celsius * 9 / 5 + 32, 1)}°F"
        return f"{round(celsius, 1)}°C"

    # Coerce each dict-extracted field to a known shape — api_weather's
    # untyped return-shape lets pyright widen dict values to
    # `Any | bool | None`, which then breaks `_escape(cond)` /
    # `f"..."` interpolation downstream.
    temp = _fmt_temp(data.get("temp_c"))
    humid = data.get("humidity")
    wind = data.get("wind_kmh")
    cond = str(data.get("condition") or "")

    # Operator-flagged: the prior render was a single em-dash chain
    # ("🌡 24°C — Clear — 💧 52% — 💨 3 km/h") which read as a data
    # dump. Rebuild as an EXPLANATORY narrative — same shape the AI
    # palette emits for weather questions — with per-metric comfort
    # / feel / strength verdicts so the operator gets context, not
    # just numbers.
    def _to_float(v) -> Optional[float]:
        """Coerce a possibly-None / possibly-untyped value to float, or
        None on failure. Centralised so the pyright-narrowing burden
        sits in one place."""
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    temp_c_num = _to_float(data.get("temp_c"))
    humid_num = _to_float(humid)
    wind_num = _to_float(wind)

    def _temp_verdict(temp_c: Optional[float]) -> str:
        if temp_c is None: return ""
        if temp_c <= 0:    return " — freezing, bundle up"
        if temp_c < 10:    return " — cold, layer up"
        if temp_c < 18:    return " — cool"
        if temp_c < 25:    return " — mild and comfortable"
        if temp_c < 32:    return " — warm"
        if temp_c < 38:    return " — hot, hydrate often"
        return " — extreme heat, limit outdoor time"

    def _humid_feel(h: Optional[float]) -> str:
        if h is None: return ""
        if h < 25:   return " — dry, watch for static"
        if h < 50:   return " — feels balanced"
        if h < 70:   return " — comfortable to slightly humid"
        if h < 85:   return " — humid"
        return " — sticky and muggy"

    def _wind_strength(k: Optional[float]) -> str:
        if k is None: return ""
        if k < 5:    return " — barely a breeze"
        if k < 12:   return " — light breeze"
        if k < 20:   return " — noticeable wind"
        if k < 30:   return " — flags snapping"
        if k < 50:   return " — gusty"
        return " — strong wind, secure loose objects"

    def _cond_emoji(c_str: str) -> str:
        lc = (c_str or "").lower()
        if "thunder" in lc:     return "⛈️"
        if "snow" in lc:        return "❄️"
        if "rain" in lc or "drizzle" in lc or "shower" in lc: return "🌧️"
        if "fog" in lc or "mist" in lc:                       return "🌫️"
        if "overcast" in lc:    return "☁️"
        if "cloud" in lc:       return "⛅"
        if "clear" in lc or "sunny" in lc: return "☀️"
        return ""

    def _takeaway(c_str: str, c_temp: Optional[float], k_wind: Optional[float]) -> str:
        lc = (c_str or "").lower()
        if "rain" in lc or "shower" in lc or "drizzle" in lc or "thunder" in lc:
            return "Bring an umbrella."
        if "snow" in lc:
            return "Watch for slippery surfaces."
        if c_temp is not None and c_temp >= 35:
            return "AC will earn its keep today."
        if c_temp is not None and c_temp <= 5:
            return "Dress in layers and warm up the engine before driving."
        if k_wind is not None and k_wind >= 40:
            return "Skip the open-flame BBQ — embers travel."
        return "Good time to be outside."

    head = f"<b>{_escape(label)}</b>"
    body_lines: list[str] = []
    emoji = _cond_emoji(cond)
    if cond:
        prefix = f"{emoji} " if emoji else ""
        body_lines.append(f"{prefix}<b>{_escape(cond)}</b> overhead.")
    if temp is not None:
        body_lines.append(f"🌡 <b>{temp}</b>{_temp_verdict(temp_c_num)}.")
    if humid is not None:
        body_lines.append(f"💧 Humidity <b>{humid}%</b>{_humid_feel(humid_num)}.")
    if wind is not None:
        body_lines.append(f"💨 Wind <b>{wind} km/h</b>{_wind_strength(wind_num)}.")
    if not body_lines:
        body_lines.append("(no current data)")
    else:
        body_lines.append(_takeaway(cond, temp_c_num, wind_num))
    line1 = "\n".join(body_lines)
    # Forecast dates render using the user's `ui_prefs.datetime_format`
    # preference, stripped of time tokens (Open-Meteo returns ISO
    # dates so there's no time component). Falls back to a sensible
    # default if the user hasn't set a custom format.
    from logic.datetime_fmt import (
        apply_datetime_format as _apply_fmt,
        get_user_datetime_format as _get_user_fmt,
        strip_time_tokens as _strip_time,
    )
    from datetime import datetime as _dt
    date_only_fmt = _strip_time(_get_user_fmt(username))
    _fc_raw = data.get("forecast")
    forecast: list = _fc_raw if isinstance(_fc_raw, list) else []
    forecast_lines: list[str] = []
    for day in forecast[:3]:
        if not isinstance(day, dict):
            continue
        raw_date = day.get("date") or ""
        try:
            day_dt = _dt.strptime(raw_date, "%Y-%m-%d")
            date_str = _apply_fmt(day_dt, date_only_fmt)
        except (TypeError, ValueError):
            date_str = raw_date or "?"
        hi = _fmt_temp(day.get("temp_max_c"))
        lo = _fmt_temp(day.get("temp_min_c"))
        c = day.get("condition") or ""
        forecast_lines.append(
            f"  • {_escape(date_str)}: {hi or '?'} / {lo or '?'}  {_escape(c)}"
        )
    text = head + "\n" + line1
    if forecast_lines:
        text += "\n\n<b>Next 3 days:</b>\n" + "\n".join(forecast_lines)
    await _send_reply(client, text)


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
        "handler": _cmd_help,
        "usage": "/help",
        "description": "Show this command list",
        "category": "getting_started",
    },
    "/start": {
        # Telegram clients send `/start` automatically when the user
        # first opens a conversation with the bot. Mapping it to help
        # gives a clean first-contact experience.
        "handler": _cmd_help,
        "usage": "/start",
        "description": "Show the command list",
        "category": "getting_started",
        "hidden": True,  # don't double up in /help (same handler as /help)
    },
    "/hosts": {
        "handler": _cmd_hosts,
        "usage": "/hosts",
        "description": "List curated hosts with their status",
        "category": "fleet",
    },
    "/host": {
        "handler": _cmd_host,
        "usage": "/host <target>",
        "description": "Show last-known stats for one host (CPU / memory / disk / uptime + extended provider stats: load, swap, bandwidth, temperatures, GPUs, containers, UPS). Cached readings only — no live probes.",
        "category": "fleet",
    },
    "/restart": {
        "handler": _cmd_restart,
        "usage": "/restart <target>",
        "description": "Restart a host via SSH (destructive — requires confirm)",
        "category": "ops",
    },
    "/cleanup": {
        "handler": _cmd_cleanup,
        "usage": "/cleanup [confirm]",
        "description": "List (or remove with `confirm`) stopped / failed / orphan containers — same surface as the SPA's topbar Cleanup button. SPA tabs auto-refresh as each removal lands.",
        "category": "ops",
    },
    "/link": {
        "handler": _cmd_link,
        "usage": "/link <code>",
        "description": "Link your Telegram account to an OmniGrid user (code minted in Profile → Telegram)",
        "category": "account",
    },
    "/unlink": {
        "handler": _cmd_unlink,
        "usage": "/unlink",
        "description": "Remove the Telegram → OmniGrid user link",
        "category": "account",
    },
    "/whoami": {
        "handler": _cmd_whoami,
        "usage": "/whoami",
        "description": "Show your access level &amp; ID (which OmniGrid user you're linked to)",
        "category": "account",
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
        "hidden": True,
    },
    "/weather": {
        "handler": _cmd_weather,
        "usage": "/weather",
        "description": "Show the weather for your saved location (set it in Profile → Weather)",
        "category": "info",
    },
    "/time": {
        "handler": _cmd_time,
        "usage": "/time",
        "description": "Show the local time at your saved weather location",
        "category": "info",
    },
    "/version": {
        "handler": _cmd_version,
        "usage": "/version",
        "description": "Show the running OmniGrid version",
        "category": "info",
    },
    "/ip": {
        "handler": _cmd_ip,
        "usage": "/ip",
        "description": "Show the deployment's public IP + ISP / ASN / country (requires tuning_public_ip_enabled in Admin → Public IP)",
        "category": "info",
    },
    "/ver": {
        # Alias for /version — same handler, hidden so the /help menu
        # doesn't double up. Dedup-by-handler in _cmd_help drops it
        # automatically; `hidden: True` makes intent explicit.
        "handler": _cmd_version,
        "usage": "/ver",
        "description": "Show the running OmniGrid version (alias for /version)",
        "category": "info",
        "hidden": True,
    },
}

# ----------------------------------------------------------------------------
# AI fallback for non-`/` text
# ----------------------------------------------------------------------------
import re as _re

# Strip every action / memory directive the AI palette knows about
# BEFORE rendering text back to Telegram. Telegram is read-only for
# AI in this phase — slash-commands are the only path that can
# trigger side effects.
_AI_DIRECTIVE_LINE = _re.compile(
    r"^\s*(?:ACTION|ACTION_HOSTS|MEMORY|MEMORY-FORGET|CHART_KIND)\s*:.*$",
    flags=_re.IGNORECASE | _re.MULTILINE,
)


def _strip_ai_directives(text: str) -> str:
    """Remove every AI-palette directive line from `text` and collapse
    excess whitespace. Returns the conversational body only."""
    if not text:
        return ""
    cleaned = _AI_DIRECTIVE_LINE.sub("", text)
    # Collapse 3+ newlines to a paragraph break.
    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


async def _build_telegram_ai_context(username: Optional[str] = None) -> dict:
    """Build the fleet-context block fed to the AI palette so it
    answers from real OmniGrid state instead of hallucinating host
    names from training data. Mirrors the SPA's
    ``_buildAiPaletteContext`` shape — host telemetry + recent items
    + weather + current server/scheduler time — so the same grounding
    directives in :data:`ai.PALETTE_SYSTEM_PROMPT` produce the same
    quality of answer on Telegram as in the SPA's command palette.

    ``username`` is the linked OmniGrid user (when known) — used to
    look up the per-user weather preference so "what's the weather"
    answers from the operator's saved city.

    Returns ``{view, hosts: [...], items: [...], weather: {...},
    time: {...}}``. Never raises; missing data sources degrade to
    empty dicts / lists.
    """
    ctx: dict = {"view": "telegram"}
    # ---- Current time + scheduler timezone -------------------------
    # The AI needs current-time grounding the same way it needs host
    # grounding: without it, "what time is it" / "what's today's date"
    # questions get the canned "I can't access a live clock" refusal.
    # Stamps UTC ISO + local-tz ISO (per `scheduler_timezone`) +
    # operator-resolved tz name + UTC offset, so the model can answer
    # in either reference frame.
    try:
        from datetime import datetime, timezone
        from logic.schedules import scheduler_tz_state
        tz_state = scheduler_tz_state() or {}
        resolved_tz_name = tz_state.get("resolved") or "UTC"
        now_utc = datetime.now(timezone.utc)
        try:
            from zoneinfo import ZoneInfo
            now_local = now_utc.astimezone(ZoneInfo(resolved_tz_name))
            local_iso = now_local.isoformat(timespec="seconds")
            offset = now_local.utcoffset()
            offset_str = ""
            if offset is not None:
                total = int(offset.total_seconds())
                sign = "+" if total >= 0 else "-"
                offset_str = f"{sign}{abs(total) // 3600:02d}:{(abs(total) % 3600) // 60:02d}"
        except (ImportError, ValueError, KeyError):
            local_iso = now_utc.isoformat(timespec="seconds")
            offset_str = "+00:00"
        ctx["time"] = {
            "utc_iso": now_utc.isoformat(timespec="seconds"),
            "local_iso": local_iso,
            "timezone": resolved_tz_name,
            "utc_offset": offset_str,
            "weekday": now_utc.strftime("%A"),
        }
    # noinspection PyBroadException
    except Exception as e:
        print(f"[telegram_listener] context time build failed: {e}")
        ctx["time"] = {}
    # ---- Weather: per-user saved location via api_weather ----------
    # When the linked operator has a topbar weather location saved,
    # fetch current conditions + 7-day forecast and inline them. Uses
    # the same in-memory cache the topbar widget hits, so a burst of
    # AI calls won't multiply upstream traffic.
    if username:
        try:
            loc = _load_user_weather_pref(username)
            if loc and loc.get("lat") is not None and loc.get("lon") is not None:
                from main import api_weather as _api_weather
                wx = await _api_weather(
                    lat=float(loc["lat"]),
                    lon=float(loc["lon"]),
                    label=(loc.get("label") or "").strip(),
                )
                if isinstance(wx, dict) and not wx.get("error"):
                    forecast = wx.get("forecast") or []
                    ctx["weather"] = {
                        "label": wx.get("label") or loc.get("label") or "",
                        "temp_c": wx.get("temp_c"),
                        "humidity": wx.get("humidity"),
                        "wind_kmh": wx.get("wind_kmh"),
                        "condition": wx.get("condition"),
                        "weather_code": wx.get("code"),
                        "forecast": forecast[:7] if isinstance(forecast, list) else [],
                    }
        # noinspection PyBroadException
        except Exception as e:
            print(f"[telegram_listener] context weather build failed: {e}")
    # ---- Public IP / ISP / ASN — operator-opt-in ifconfig.co lookup.
    # Gated behind `tuning_public_ip_enabled` (default OFF for
    # privacy); cached in-process via `tuning_public_ip_cache_ttl_seconds`
    # so a burst of AI calls hits the upstream at most once per cache
    # window. Disabled state -> no `public_ip` key in ctx, prompt-
    # builder skips the block cleanly.
    try:
        from logic.public_ip import fetch as _public_ip_fetch
        _pip = await _public_ip_fetch()
        if _pip:
            ctx["public_ip"] = _pip
    # noinspection PyBroadException
    except Exception as e:  # noqa: BLE001
        print(f"[telegram_listener] context public_ip build failed: {e}")
    # ---- Items: live gather cache, same shape the SPA reads --------
    try:
        from logic import gather as _gather
        # noinspection PyProtectedMember
        items = list(_gather._cache.get("items") or [])
        ctx["items"] = [
            {
                "name": i.get("name"),
                "status": i.get("status"),
                "health": i.get("health"),
                "type": i.get("type"),
                "replicas": i.get("replicas"),
                "desired": i.get("desired"),
                "update_available": bool(i.get("update_available")),
                "stack": i.get("stack"),
            }
            for i in items[:30]
        ]
    # noinspection PyBroadException
    except Exception as e:
        print(f"[telegram_listener] context items build failed: {e}")
        ctx["items"] = []
    # ---- Hosts: curated config + last-known snapshot ---------------
    try:
        import json as _json
        from logic.db import db_conn
        hosts_cfg = _load_hosts_config()
        paused_set = _load_host_paused_set()
        # Read last-known host_snapshots in one round-trip so we can
        # surface stale-data fields when a provider is currently down.
        snap_map: dict[str, dict] = {}
        try:
            with db_conn() as c:
                for row in c.execute(
                    "SELECT host_id, snapshot FROM host_snapshots"
                ):
                    try:
                        snap_map[row[0]] = _json.loads(row[1])
                    except (ValueError, TypeError):
                        pass
        except (sqlite3.DatabaseError, sqlite3.OperationalError):
            # Snapshot table may not exist on a fresh DB; just continue.
            pass
        host_records: list[dict] = []
        for h in hosts_cfg[:30]:
            if not h.get("enabled", True):
                continue
            hid = h.get("id") or ""
            snap = snap_map.get(hid) or {}
            status = "paused" if hid in paused_set else (
                "up" if snap else "unknown"
            )
            host_records.append({
                "id": hid,
                "label": h.get("label") or hid,
                "status": status,
                "paused": hid in paused_set,
                "address": h.get("address") or "",
                "cpu_pct": snap.get("host_cpu_percent"),
                "mem_pct": snap.get("host_mem_percent"),
                "disk_pct": snap.get("host_disk_percent"),
                "uptime_s": snap.get("host_uptime_seconds"),
                "host_hostname": snap.get("host_hostname"),
                "platform": snap.get("host_platform"),
                "kernel": snap.get("host_kernel"),
                # Operator-typed aliases — the AI uses these to match
                # "the qotom" / "the r730" against the right host.
                "beszel_name": h.get("beszel_name") or "",
                "pulse_name": h.get("pulse_name") or "",
                "webmin_name": h.get("webmin_name") or "",
                "snmp_name": h.get("snmp_name") or "",
            })
        ctx["hosts"] = host_records
        # Authoritative counts — the AI must answer "how many hosts"
        # from these, NOT from len(hosts) (which it sees as the
        # sample cap of 30). Operator-flagged: with 183 configured
        # hosts the AI replied "30 hosts" because that's all it
        # could see in the sample block.
        ctx["hosts_total"] = len(hosts_cfg)
        ctx["hosts_enabled"] = sum(
            1 for h in hosts_cfg if h.get("enabled", True)
        )
        ctx["hosts_sample_cap"] = 30
    # noinspection PyBroadException
    except Exception as e:
        print(f"[telegram_listener] context hosts build failed: {e}")
        ctx["hosts"] = []
        ctx["hosts_total"] = 0
        ctx["hosts_enabled"] = 0
        ctx["hosts_sample_cap"] = 30
    return ctx


# noinspection PyUnusedLocal
async def _ai_reply(
    client: httpx.AsyncClient,
    text: str,
    msg: dict,
    omnigrid_username: str,
) -> None:
    """Route a non-`/` Telegram message through the AI palette and
    reply with the conversational response.

    Flow:
      1. Fire native Telegram "typing…" indicator + send a "🤖
         Thinking…" placeholder. Capture the placeholder's
         ``message_id`` so the final reply can be edited in place
         (same chat bubble — better UX than a new message).
      2. Build a fleet context block (hosts + items) and feed it to
         :func:`logic.ai.build_palette_user_prompt` so the AI grounds
         its answer in actual OmniGrid state instead of hallucinating
         hostnames from training data.
      3. Run the AI call with :data:`logic.ai.PALETTE_SYSTEM_PROMPT`
         (which carries the GROUNDING-STRICT / fuzzy-matching
         directives) plus a Telegram-specific override telling the
         model it's on a read-only surface.
      4. Strip any ACTION/MEMORY/CHART_KIND directives the AI emits
         anyway (defence in depth — the read-only override should
         prevent them, but bugs happen).
      5. Edit the placeholder in place with the final answer; fall
         back to a fresh ``_send_reply`` if the edit fails.
    """
    try:
        from logic import ai as _ai
        from logic.db import get_setting, get_setting_bool
    # noinspection PyBroadException
    except Exception as e:
        print(f"[telegram_listener] _ai_reply import failed: {e}")
        return
    if not get_setting_bool(Settings.AI_ENABLED):
        await _send_reply(
            client,
            "AI integration is disabled. Enable it in OmniGrid → "
            "Admin → AI Integration, or use <code>/help</code> for "
            "available commands."
        )
        return
    provider = (get_setting(Settings.AI_ACTIVE_PROVIDER) or "").strip().lower()
    if not provider:
        await _send_reply(client, "No AI provider configured. Set one in Admin → AI Integration.")
        return
    # Per-provider API key lookup.
    api_key = (get_setting(ai_provider_api_key_key(provider)) or "").strip()
    if not api_key:
        await _send_reply(
            client,
            f"AI provider <b>{_escape(provider)}</b> is selected but has no API key configured."
        )
        return
    model = (get_setting(ai_provider_model_key(provider)) or "").strip() or None
    base_url = (get_setting(ai_provider_base_url_key(provider)) or "").strip() or None

    # ---- Immediate user feedback: typing indicator + placeholder ---
    # The typing indicator is decorative (~5s); the placeholder is
    # the durable bubble we edit in place when the AI returns.
    await _send_chat_action(client)
    placeholder_id = await _send_reply(client, "🤖 <i>Thinking…</i>")

    # ---- Build grounded prompt -------------------------------------
    # Reuse the SPA's `build_palette_user_prompt` so Telegram and the
    # command palette feed the AI an identical record-shape. The
    # PALETTE_SYSTEM_PROMPT then enforces grounding (no hallucinated
    # hostnames) via the same GROUNDING-STRICT block both surfaces
    # share.
    ctx = await _build_telegram_ai_context(omnigrid_username)
    user_prompt = _ai.build_palette_user_prompt(text, ctx)

    # Snapshot the REAL Telegram command roster from `_COMMANDS` so the
    # AI grounds its replies in actual commands instead of hallucinating
    # SPA-style names (`/status`, `/services`, `/updates`, `/errors`,
    # `/update`, `/prune`, `/forecast` are common hallucinations the
    # operator flagged when the prompt didn't carry the canonical list).
    # Dedup by handler so aliases (`/start` → `_cmd_help`, `/myid` →
    # `_cmd_whoami`, `/ver` → `_cmd_version`) render alongside their
    # primary rather than as separate phantom commands.
    _seen_handlers: set = set()
    _roster_lines: list[str] = []
    for _name, _meta in _COMMANDS.items():
        _h = _meta.get("handler")
        if _h is None or _h in _seen_handlers:
            continue
        _seen_handlers.add(_h)
        _usage = _meta.get("usage") or _name
        _desc = (_meta.get("description") or "").strip()
        # Collect aliases for the same handler so the AI sees the full
        # set of valid invocations.
        _aliases = [
            n for n, m in _COMMANDS.items()
            if n != _name and m.get("handler") is _h
        ]
        _alias_suffix = (
            f" (aliases: {', '.join(_aliases)})" if _aliases else ""
        )
        _roster_lines.append(
            f"  - `{_usage}`{_alias_suffix} — {_desc}"
            if _desc else
            f"  - `{_usage}`{_alias_suffix}"
        )
    _command_roster = "\n".join(_roster_lines)

    # Telegram-specific override: PALETTE_SYSTEM_PROMPT tells the AI
    # to emit ACTION: directives for the SPA's command palette to
    # execute. Telegram is a READ-ONLY surface — append an override
    # that strips that license. The strip pass below is defence in
    # depth in case the model emits them anyway. The COMMAND ROSTER
    # block injects the canonical `_COMMANDS` list so the AI can only
    # reference real commands (operator-reported hallucinations like
    # `/status` / `/services` / `/updates` / `/errors` / `/forecast`
    # came from the AI inventing SPA-style commands without grounding).
    system_prompt = (
        _ai.PALETTE_SYSTEM_PROMPT
        + "\n\n"
        + "TELEGRAM SURFACE OVERRIDE. You are replying to operator "
          f"'{omnigrid_username}' via Telegram, which is a READ-ONLY "
          "channel in this deployment. NEVER emit ACTION: / "
          "ACTION_HOSTS: / MEMORY: / MEMORY-FORGET: / CHART_KIND: "
          "directives — those are silently stripped before the reply "
          "reaches the user, so emitting them just wastes tokens. If "
          "the operator asks you to DO something (restart, pause, "
          "configure), tell them to use the matching slash command "
          "(/restart <target>, /cleanup, etc.) or the SPA. "
          "**Length guidance — DO NOT BLINDLY COMPRESS.** Telegram "
          "messages stay readable up to 4096 characters; aim for "
          "under 800 on terse fleet-state replies (host counts, "
          "status lookups, single-fact answers). EXCEPTION: when the "
          "operator asks an explanatory question (weather / why is "
          "X down / what does this metric mean / explain this incident), "
          "follow the FULL render contract from the upstream block "
          "above — emit the 3-5 sentence narrative paragraph the "
          "block requests instead of collapsing it into a one-line "
          "summary. The 800-character soft target is for fleet-data "
          "questions, NOT a global ceiling on every reply. Use the "
          "supplied JSON records (hosts / items) to answer fleet-state "
          "questions rather than inventing names from training data."
        + "\n\n"
        + "**TELEGRAM COMMAND ROSTER — AUTHORITATIVE GROUND TRUTH.** "
          "The list below is the COMPLETE set of slash commands this "
          "bot supports right now. When the operator asks for the "
          "command list / help / what they can do, render EXACTLY "
          "these commands — DO NOT add, invent, or extrapolate. "
          "Commands that DO NOT appear in this list DO NOT EXIST in "
          "this deployment. Specifically NEVER mention: `/status`, "
          "`/services`, `/updates`, `/errors`, `/update`, `/prune`, "
          "`/forecast`, `/stacks`, `/logs`, `/exec`, `/ssh`, `/scan`, "
          "`/backup`, or any other SPA-style or Docker-style command "
          "name — those are common hallucinations from training data, "
          "not real OmniGrid commands. If the operator asks for a "
          "capability the roster doesn't cover, say so honestly + "
          "redirect them to the SPA (where the action probably "
          "exists). Render the roster in your reply using the SAME "
          "groupings the /help command uses (📖 Getting started / "
          "🖥️ Fleet / ⚙️ Operations / 🔗 Account / ℹ️ Info & weather) "
          "when the user asks for the full menu; for a one-off "
          "'how do I X' question cite ONLY the single relevant "
          "command from the roster.\n\n"
          "Canonical command list (handler-deduped, aliases grouped):\n"
        + _command_roster
    )
    # Token budget honours the operator's `tuning_ai_max_tokens`
    # setting (Admin → AI Integration → "Max response tokens"). Hard-
    # coding a low cap here breaks "thinking" models like Gemini 2.5
    # which spend the budget on internal reasoning BEFORE producing
    # output tokens — a 512-token cap can return zero visible text
    # with finish_reason=MAX_TOKENS. The 4096-char per-message
    # Telegram cap is still enforced post-render by the truncation
    # block below, so an excessive setting can't push past the wire
    # limit. Defence in depth: clamp to a reasonable upper bound.
    try:
        from logic import tuning as _tuning
        from logic.tuning import Tunable
        max_toks = _tuning.tuning_int(Tunable.AI_MAX_TOKENS)
    except (ImportError, KeyError, ValueError, TypeError):
        max_toks = 1024
    try:
        max_toks = max(256, int(max_toks))
    except (TypeError, ValueError):
        max_toks = 1024

    # Helper: deliver `final` to the operator. Tries the in-place
    # edit first (replaces the "🤖 Thinking…" bubble); on any failure
    # falls back to a fresh sendMessage so the operator never ends up
    # without a visible reply.
    async def _deliver(final: str) -> None:
        edited = False
        if placeholder_id:
            edited = await _edit_message(client, placeholder_id, final)
        if not edited:
            await _send_reply(client, final)

    # Inner helper: record the AI call into `ai_jobs` AND `history`
    # so Telegram queries show up on the Admin → AI Usage dashboard
    # and the History tab alongside SPA palette / host-filter calls.
    # Same `kind` naming convention the SPA uses (palette → ai_palette,
    # host_filter → ai_host_filter); Telegram → ai_telegram.
    def _record_call(ok: bool, raw_result: dict | None, answer_text: str) -> None:
        try:
            from logic.db import db_conn
            _ai.record_ai_call(
                db_conn_factory=db_conn,
                provider=provider,
                model=(raw_result or {}).get("model") or model or "",
                kind="telegram",
                ok=ok,
                response_time_ms=int((raw_result or {}).get("response_time_ms") or 0),
                tokens=(raw_result or {}).get("tokens"),
                error_detail=(None if ok else ((raw_result or {}).get("detail") or "")),
                history_actor=omnigrid_username or "telegram",
                history_events={
                    "prompt": text,
                    "answer": answer_text,
                    "surface": "telegram",
                    "context": {
                        "view": ctx.get("view") if isinstance(ctx, dict) else "telegram",
                        "hosts_count": len(ctx.get("hosts") or []) if isinstance(ctx, dict) else 0,
                        "items_count": len(ctx.get("items") or []) if isinstance(ctx, dict) else 0,
                    },
                },
            )
        # noinspection PyBroadException
        except Exception as _rec_err:
            # Never let a recording failure swallow the operator's reply.
            print(f"[telegram_listener] record_ai_call failed: {_rec_err}")

    try:
        result = await _ai.ask_provider(
            provider,
            api_key=api_key,
            prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            base_url=base_url,
            max_tokens=max_toks,
        )
    # noinspection PyBroadException
    except Exception as e:
        _record_call(False, {"detail": str(e)}, "")
        await _deliver(f"❌ AI call failed: <code>{_escape(str(e))}</code>")
        return
    if not isinstance(result, dict) or not result.get("ok"):
        detail = (result or {}).get("detail") if isinstance(result, dict) else "no response"
        _record_call(False, result if isinstance(result, dict) else None, "")
        await _deliver(
            f"❌ AI provider error: <code>{_escape(str(detail))}</code>"
        )
        return
    raw_text = (result.get("text") or "").strip()
    clean = _strip_ai_directives(raw_text)
    if not clean:
        _record_call(True, result, "")
        await _deliver("<i>(empty AI response)</i>")
        return
    # Record BEFORE truncation so the persisted answer matches what
    # the model actually returned (truncation is purely a Telegram
    # wire-limit accommodation, not the canonical record).
    _record_call(True, result, clean)
    # Telegram caps a single message at 4096 chars including HTML
    # tags. _send_reply will fail HTTP-400 if we exceed; pre-trim
    # with a clear "(truncated)" marker so the operator knows.
    max_chars = 3800  # leave headroom for HTML overhead
    if len(clean) > max_chars:
        clean = clean[:max_chars] + "\n\n<i>…(truncated)</i>"
    # Escape for HTML parse_mode — the AI's response might contain
    # &, <, > that Telegram's parser would otherwise reject.
    await _deliver(_escape(clean))


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
    open_commands = {"/link", "/help", "/start", "/whoami", "/myid"}
    if head not in open_commands:
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
    sender_id_audit = (msg.get("from") or {}).get("id")
    actor_audit = (
                      _lookup_omnigrid_user(sender_id_audit)
                      if sender_id_audit is not None else None
                  ) or "telegram"
    # Sanitise args BEFORE persisting — `/link <code>` carries a
    # single-use 6-digit code that would leak via the audit log
    # otherwise. Same redaction class as auth-secret masking in SSH
    # audit rows.
    safe_args = list(args)
    if head == "/link" and safe_args:
        safe_args = ["<redacted>"]
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
    # the outcome. Best-effort: a logging failure inside
    # `_audit_telegram` is already swallowed there.
    _audit_telegram(
        "telegram_command",
        actor=actor_audit,
        target_name=head,
        status=handler_status,
        error=handler_error,
        events={
            "command": head,
            "args": safe_args,
            "telegram_user_id": int(sender_id_audit) if sender_id_audit is not None else None,  # type: ignore[arg-type]  # guard above narrows None branch
        },
    )


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
                    r = await client.get(
                        f"{_telegram_api_base()}/bot{token}/getUpdates",
                        params={
                            "offset": offset,
                            "timeout": long_poll_to,
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
                        # noinspection PyBroadException
                        except Exception as e:  # noqa: BLE001
                            print(f"[telegram_listener] update processing failed: {e}")
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except httpx.HTTPError as e:
                # Network blip — back off briefly + retry. Don't spam
                # logs with the same error every iteration.
                print(f"[telegram_listener] network: {e}")
                await asyncio.sleep(5)
            # noinspection PyBroadException
            except Exception as e:  # noqa: BLE001
                print(f"[telegram_listener] loop iteration failed: {e}")
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        print("[telegram_listener] lifespan cancelled")
        raise
