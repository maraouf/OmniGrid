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
    raw = (get_setting("telegram_user_mappings", "") or "").strip()
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
                    "linked_at_ms": int(value.get("linked_at_ms") or 0),
                }
            elif isinstance(value, str) and value:
                clean[tg_id] = {"username": value, "linked_at_ms": 0}
        set_setting("telegram_user_mappings", json.dumps(clean))
    except (TypeError, ValueError) as e:
        print(f"[telegram_listener] mapping save failed: {e}")


def _lookup_omnigrid_user(telegram_user_id: int) -> Optional[str]:
    """Return the OmniGrid username for one Telegram user_id, or None
    if the user hasn't linked yet."""
    if telegram_user_id is None:
        return None
    entry = _load_mappings().get(str(int(telegram_user_id)))
    if not entry:
        return None
    return entry.get("username")


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
    except Exception:
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
    except Exception:
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
        "lat":   lat_f,
        "lon":   lon_f,
        "label": label,
        "unit":  unit,
    }


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
    except Exception:
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

    def _render_row(h: dict, emoji: str) -> str:
        hid = h.get("id") or "(no-id)"
        label = h.get("label") or hid
        addr = h.get("address") or ""
        return (f"{emoji} <code>{_escape(hid)}</code> — {_escape(label)}"
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


async def _cmd_version(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/version`` (aliased as ``/ver``) — show the running OmniGrid
    version. Reads the version baked into the image at build time
    (`/app/VERSION.txt` populated by the deploy pipeline's
    ``--build-arg VERSION=<X.Y.Z>``). Non-sensitive — works pre-link
    so unmapped operators can confirm which build they're talking to."""
    try:
        from logic.version import read_version
        version = read_version()
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
            "admin":    "Admin (full access)",
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
    tz_name = (data.get("timezone") or "").strip()
    tz_abbrev = (data.get("timezone_abbrev") or "").strip()
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
    except Exception:
        # Fallback: utc_offset_seconds-based math, ignoring DST.
        from datetime import datetime, timezone, timedelta
        try:
            offset = int(data.get("utc_offset_seconds") or 0)
            now_local = datetime.now(timezone.utc) + timedelta(seconds=offset)
            offset_note = " (UTC offset — IANA tz unavailable)"
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


async def _cmd_link(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/link <code>`` — bind the sender's Telegram user_id to an
    OmniGrid user. Code is minted by the SPA's Profile section and
    valid for 15 minutes, single-use."""
    if not args:
        await _send_reply(client, "Usage: <code>/link &lt;code&gt;</code>")
        return
    sender_id = (msg.get("from") or {}).get("id")
    if sender_id is None:
        await _send_reply(client, "Can't read your Telegram user_id from the message.")
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
    mappings = _load_mappings()
    mappings[str(int(sender_id))] = {
        "username": username,
        "linked_at_ms": int(_time.time() * 1000),
    }
    _save_mappings(mappings)
    await _send_reply(
        client,
        f"✅ Linked to OmniGrid user <b>{_escape(username)}</b>. "
        f"You can now run authenticated commands."
    )


async def _cmd_unlink(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/unlink`` — drop the sender's Telegram → OmniGrid mapping."""
    sender_id = (msg.get("from") or {}).get("id")
    if sender_id is None:
        await _send_reply(client, "Can't read your Telegram user_id from the message.")
        return
    mappings = _load_mappings()
    key = str(int(sender_id))
    if key not in mappings:
        await _send_reply(client, "You weren't linked. Nothing to unlink.")
        return
    removed = mappings.pop(key)
    _save_mappings(mappings)
    await _send_reply(
        client,
        f"✅ Unlinked from OmniGrid user <b>{_escape(removed)}</b>. "
        f"Re-link via Profile → Telegram in OmniGrid."
    )


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
            c = float(c_val)
        except (TypeError, ValueError):
            return None
        if unit == "f":
            return f"{round(c * 9 / 5 + 32, 1)}°F"
        return f"{round(c, 1)}°C"

    temp = _fmt_temp(data.get("temp_c"))
    humid = data.get("humidity")
    wind = data.get("wind_kmh")
    cond = data.get("condition") or ""
    head = f"<b>{_escape(label)}</b>"
    body_parts: list[str] = []
    if temp is not None:
        body_parts.append(f"🌡 {temp}")
    if cond:
        body_parts.append(_escape(cond))
    if humid is not None:
        body_parts.append(f"💧 {humid}%")
    if wind is not None:
        body_parts.append(f"💨 {wind} km/h")
    line1 = " — ".join(body_parts) if body_parts else "(no current data)"
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
    forecast = data.get("forecast") or []
    forecast_lines: list[str] = []
    for day in forecast[:3]:
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
    "/link": {
        "handler":     _cmd_link,
        "usage":       "/link <code>",
        "description": "Link your Telegram account to an OmniGrid user (code minted in Profile → Telegram)",
    },
    "/unlink": {
        "handler":     _cmd_unlink,
        "usage":       "/unlink",
        "description": "Remove the Telegram → OmniGrid user link",
    },
    "/whoami": {
        "handler":     _cmd_whoami,
        "usage":       "/whoami",
        "description": "Show your access level &amp; ID (which OmniGrid user you're linked to)",
    },
    "/myid": {
        # Alias for /whoami — the most common phrasing operators reach
        # for when they want to know "who am I as far as the bot is
        # concerned". Same handler, hidden from /help so the menu
        # doesn't double up (the dedup-by-handler logic in _cmd_help
        # already handles this — `hidden: True` makes intent explicit).
        "handler":     _cmd_whoami,
        "usage":       "/myid",
        "description": "Show your access level &amp; ID (alias for /whoami)",
        "hidden":      True,
    },
    "/weather": {
        "handler":     _cmd_weather,
        "usage":       "/weather",
        "description": "Show the weather for your saved location (set it in Profile → Weather)",
    },
    "/time": {
        "handler":     _cmd_time,
        "usage":       "/time",
        "description": "Show the local time at your saved weather location",
    },
    "/version": {
        "handler":     _cmd_version,
        "usage":       "/version",
        "description": "Show the running OmniGrid version",
    },
    "/ver": {
        # Alias for /version — same handler, hidden so the /help menu
        # doesn't double up. Dedup-by-handler in _cmd_help drops it
        # automatically; `hidden: True` makes intent explicit.
        "handler":     _cmd_version,
        "usage":       "/ver",
        "description": "Show the running OmniGrid version (alias for /version)",
        "hidden":      True,
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


async def _ai_reply(
    client: httpx.AsyncClient,
    text: str,
    msg: dict,
    omnigrid_username: str,
) -> None:
    """Route a non-`/` Telegram message through the AI palette and
    reply with the conversational response.

    Action directives are stripped from the AI's output — Telegram
    cannot trigger actions through AI in this phase. The AI gets a
    constrained system prompt that tells it Telegram is read-only,
    so it shouldn't emit `ACTION:` directives in the first place; the
    strip pass is defence-in-depth.
    """
    try:
        from logic import ai as _ai
        from logic.db import get_setting, get_setting_bool
    except Exception as e:
        print(f"[telegram_listener] _ai_reply import failed: {e}")
        return
    if not get_setting_bool("ai_enabled", False):
        await _send_reply(
            client,
            "AI integration is disabled. Enable it in OmniGrid → "
            "Admin → AI Integration, or use <code>/help</code> for "
            "available commands."
        )
        return
    provider = (get_setting("ai_active_provider", "") or "").strip().lower()
    if not provider:
        await _send_reply(client, "No AI provider configured. Set one in Admin → AI Integration.")
        return
    # Per-provider API key lookup.
    api_key = (get_setting(f"ai_provider_{provider}_api_key", "") or "").strip()
    if not api_key:
        await _send_reply(
            client,
            f"AI provider <b>{_escape(provider)}</b> is selected but has no API key configured."
        )
        return
    model = (get_setting(f"ai_provider_{provider}_model", "") or "").strip() or None
    base_url = (get_setting(f"ai_provider_{provider}_base_url", "") or "").strip() or None
    system_prompt = (
        "You are OmniGrid's Telegram assistant, replying to operator "
        f"'{omnigrid_username}'. Telegram is a READ-ONLY surface in this "
        "phase: you can answer questions about the fleet, summarise "
        "status, explain features, but you MUST NOT emit ACTION: / "
        "ACTION_HOSTS: / MEMORY: directives — those are silently "
        "stripped before the reply reaches the user. If the operator "
        "asks you to DO something (restart, pause, configure), tell "
        "them to use the slash command (e.g. /restart <target>) or "
        "the SPA. Keep replies brief — Telegram messages stay readable "
        "under 4096 characters; aim for under 500."
    )
    try:
        result = await _ai.ask_provider(
            provider,
            api_key=api_key,
            prompt=text,
            system_prompt=system_prompt,
            model=model,
            base_url=base_url,
            # Bounded so a runaway response can't blow the Telegram
            # 4096-char per-message limit (most prompts fit in 1024
            # output tokens ≈ 3-4k chars).
            max_tokens=512,
        )
    except Exception as e:
        await _send_reply(client, f"❌ AI call failed: <code>{_escape(str(e))}</code>")
        return
    if not isinstance(result, dict) or not result.get("ok"):
        detail = (result or {}).get("detail") if isinstance(result, dict) else "no response"
        await _send_reply(
            client,
            f"❌ AI provider error: <code>{_escape(str(detail))}</code>"
        )
        return
    raw_text = (result.get("text") or "").strip()
    clean = _strip_ai_directives(raw_text)
    if not clean:
        await _send_reply(client, "<i>(empty AI response)</i>")
        return
    # Telegram caps a single message at 4096 chars including HTML
    # tags. _send_reply will fail HTTP-400 if we exceed; pre-trim
    # with a clear "(truncated)" marker so the operator knows.
    MAX = 3800  # leave headroom for HTML overhead
    if len(clean) > MAX:
        clean = clean[:MAX] + "\n\n<i>…(truncated)</i>"
    # Escape for HTML parse_mode — the AI's response might contain
    # &, <, > that Telegram's parser would otherwise reject.
    await _send_reply(client, _escape(clean))


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
