# Telegram integration

OmniGrid talks to Telegram in three layered ways:

1. **Outbound notifications** — `logic/notify_telegram.py` fans out
   per-event messages (host paused, swarm-agent autoheal, schedule
   fired, etc.) as a notification medium alongside the in-app inbox
   and Apprise.
2. **Inbound slash commands** — `logic/telegram_listener.py` long-polls
   the Bot API for incoming messages and dispatches `/help`, `/hosts`,
   `/host <target>`, `/restart <target>`, `/cleanup`, etc.
3. **Inbound free-form AI chat** — any non-`/` text in an authorised
   chat is grounded against fleet context and answered by the
   configured AI provider (Claude / Gemini / ChatGPT / DeepSeek),
   subject to a strict read-only override for the Telegram surface.

This document covers the full Telegram surface so you can configure it,
extend it with a new command, and understand how the AI integration is
grounded against the canonical command roster.

---

## Quickstart

1. **Create the bot.** Talk to [@BotFather](https://t.me/BotFather) on
   Telegram. `/newbot` → choose a name + username, copy the token it
   returns (`123456789:ABC-DEF...`).
2. **Open the destination chat.** Either:
   - **DM** the bot directly (simplest — you become the only authorised
     sender), OR
   - **Add the bot to a supergroup** and grant it "send messages"
     permission. Optionally enable forum topics if you want
     notifications routed to a specific topic.
3. **Find the chat id.** Send any message to the bot, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser. The
   chat id is `result[0].message.chat.id` — a positive int for DMs,
   negative int for supergroups (note: actual supergroups start with
   `-100`).
4. **Configure in OmniGrid.** Admin → Notifications → Telegram tab:
   - **Bot token** — paste the BotFather token (write-only secret; the
     UI shows `<set>` after save).
   - **Chat id** — paste the id from step 3.
   - **Thread id** — optional, for supergroups with forum topics
     enabled. Leave blank to post to the supergroup's main feed.
   - **Verify TLS** — leave ON unless you're testing through a proxy
     with a self-signed cert.
   - **Bot API base URL** — leave blank for the official
     `https://api.telegram.org` upstream. Override only when running a
     self-hosted Bot API server (tdlight / local TDLib) or routing
     through a proxy.
   - Toggle **Enable Telegram notifications** ON, then **Save**.
5. **Send a test.** The "Send test" button at the bottom of the
   Telegram tab fires one real notification through the dispatcher so
   you can confirm delivery end-to-end.
6. **(Optional) Enable inbound commands.** Scroll down to **Inbound
   commands** in the same Telegram tab:
   - **Enable inbound command listener** — flips the long-poll loop ON.
   - **Allow destructive Telegram commands** — when ON, `/restart`
     executes immediately. When OFF (default), `/restart` requires a
     second typed message `/restart confirm <target>`.
   - **Authorized user ids (CSV)** — restrict commands to specific
     Telegram user_ids regardless of chat membership. Empty list =
     any sender in the authorised chat can issue commands (correct for
     personal DMs where you ARE the only chat member).

---

## Outbound notifications

Telegram is one of three notification mediums (in-app inbox / Apprise /
Telegram). Each event has its own per-event enable toggle (Admin →
Notifications → event grid) AND a per-medium master switch
(`notify_medium_telegram` for Telegram). An event fires through
Telegram when BOTH the event is enabled AND the medium master switch
is on.

The dispatcher lives in `logic/notify_telegram.py:send` (called from
`logic/ops.py:notify`). Each message:

- Renders the configured template (Admin → Notifications → event row →
  template editor) with `{name}` / `{type}` / `{actor}` / `{host}` /
  `{time}` / `{error}` / `{status}` placeholders.
- Prefixes a severity emoji (`🔴` error / `🟠` warning / `🟢` success /
  `ℹ️` info / `🔵` general) UNLESS the rendered title already starts
  with a non-ASCII character (the operator-flagged "double checkmark"
  case where a template already says `✅ Stack updated:`).
- Posts with `parse_mode=HTML` and `disable_web_page_preview=true` so
  links don't blow up the bubble layout in mobile clients.
- Honours `telegram_thread_id` when set (routes the post to a specific
  supergroup topic).

The Bot API base URL is read per-call via `_telegram_api_base()` so an
Admin UI edit lands on the next send without restart.

---

## Inbound slash commands

The long-poll loop in `logic/telegram_listener.py:listener_loop`
ticks at `tuning_telegram_long_poll_timeout_seconds` (default 25s,
Telegram caps at 50). It calls `/getUpdates` with a long-poll
timeout — Telegram holds the connection open until either an update
arrives or the timeout elapses, so the loop's effective sleep is the
poll timeout, not a constant `asyncio.sleep`. Outer HTTP wall-clock
is `tuning_telegram_http_timeout_seconds` (default 35s, slightly
longer than the long-poll so Telegram has time to flush the response).

Restart-safe via the persisted `telegram_last_update_id` offset in the
settings table — after a container restart the listener resumes from
exactly the last processed update.

### Authorization

Two layers of defence:

- **Layer 1 — chat-id gate.** `update.message.chat.id` must be in the
  configured `telegram_chat_id` set. The setting accepts a single ID
  OR a comma-separated list (`-1001234567890, 987654321`), so the same
  bot can serve a group AND 1:1 DMs from operators simultaneously.
  Messages from any chat NOT in the list are silently ignored (an
  attacker probing a public bot gets no useful feedback). Replies are
  routed back to the chat the command came from; outbound
  notifications fan out to every configured chat.
- **Layer 2 — authorized user-ids allowlist.** Optional CSV of
  Telegram user_ids in `telegram_authorized_user_ids`. Empty list =
  any sender in the authorised chats is allowed. Non-empty list
  restricts commands to those user_ids regardless of chat membership.
  Correct for supergroups with multiple members; unnecessary for
  personal DMs.

Beyond the two chat-level layers there is a separate **mapping gate**:
commands NOT in the "open" set require the sender to be linked to an
OmniGrid user first (`_lookup_omnigrid_user(sender_id)` non-None). The
"open" commands — derived from `_COMMANDS[*].access == "open"`, so the
set never drifts from the per-command metadata — are `/help`, `/start`,
`/link`, `/whoami`, `/myid`, `/version` (alias `/ver`), and `/ip`.
These stay usable before linking so an unlinked user can discover the
bot and mint a link code (`/ip` is gated separately by the
`public_ip_enabled` setting). Every other command (`/hosts`, `/host`,
`/weather`, `/time`, `/upcoming`, …) returns a "Link your account
first" prompt until the sender is linked; `/restart` additionally
requires admin role.

### Command roster

`/help` (and `/start`) renders the roster grouped into categories
with bold section headers + emoji prefixes:

- **📖 Getting started**
  - `/help` (aliases: `/start`) — Show this command list
- **🖥️ Fleet**
  - `/hosts` — List curated hosts with their status
  - `/host <target>` — Show last-known stats for one host (CPU /
    memory / disk / uptime + extended provider stats). Cached
    readings only — no live probes.
- **⚙️ Operations**
  - `/restart <target>` (aliases: `/reboot`) — Restart a host via SSH
    (destructive — requires confirm)
  - `/cleanup [confirm]` — List (or remove with `confirm`)
    stopped / failed / orphan containers — same surface as the SPA's
    topbar Cleanup button. SPA tabs auto-refresh as each removal
    lands.
  - `/update [all | <name>] [confirm]` — List items with pending
    updates (no args), update ONE item by name, or `/update all` to
    pull-and-recreate every item flagged `update_available`. Same
    per-row update path the SPA uses; respects the destructive gate
    (`telegram_allow_destructive` or the `confirm` suffix).
  - `/skills` — List the per-app skill roster, one tappable entry per
    pinned app. Each pinned app whose module declares skills also gets
    a DYNAMIC `/<skill_id>` command (e.g. `/run_speedtest`,
    `/adguard_status`) — routed and shown in `/help` but kept OUT of the
    BotFather autocomplete menu so it doesn't balloon. Destructive
    skills ride the same typed-confirm gate; fleet skills (AdGuard /
    Pi-hole) run host-less.
- **🔗 Account**
  - `/link <code>` — Link your Telegram account to an OmniGrid user
    (code minted in Profile → Telegram)
  - `/unlink` — Remove the Telegram → OmniGrid user link
  - `/whoami` (aliases: `/myid`) — Show your access level & ID
- **ℹ️ Info & weather**
  - `/upcoming [days] [movies|series|music|books]` — Upcoming releases
    across your *arr apps (movies / episodes / albums / books) with
    dates + synopsis. Requires a linked account + at least one pinned
    *arr app.
  - `/weather` — Show the weather for your saved location (set it in
    Profile → Weather)
  - `/moon` — Show today's moon phase + illumination (requires the
    WeatherAPI.com provider; Open-Meteo is moon-blind)
  - `/prayer` — Show today's five prayer times + the next prayer
    (requires Prayer Times enabled in Admin → Prayer Times)
  - `/hijri` — Show today's Hijri (Islamic) calendar date
  - `/time` — Show the local time at your saved weather location
  - `/version` (aliases: `/ver`) — Show the running OmniGrid version
  - `/ip` — Show the deployment's public IP + ISP / ASN / country
    (requires `public_ip_enabled` in Admin → Public IP)

### Target resolution

Commands that take a `<target>` (e.g. `/restart`, `/host`) resolve in
this priority order:

1. **Exact IP match** against curated host `address` / `snmp_name` /
   `beszel_name` / `pulse_name` / `webmin_name` / `ssh.host` fields.
2. **Exact host_id** (curated row primary key).
3. **Exact label** (case-insensitive).
4. **Asset `short_name`** via the asset-inventory cache joined to
   hosts by `custom_number`.
5. **Asset `serial` / `model` substring** match (also joined).
6. **Substring fallback** across `host_id` / label / per-provider
   targets.

Multiple matches → the bot replies with the candidate list and
aborts so the user can narrow the target.

### Destructive-command gate

Three commands trigger the destructive-command gate: `/restart` (with
its `/reboot` alias), `/cleanup` (when called with `confirm`), and
`/update` (any in-band action — `/update <name>` or `/update all`).
The gate works as follows:

- **`telegram_allow_destructive = true`** — the command fires
  immediately. Use this in personal-DM deploys where the typed-confirm
  step is just friction.
- **`telegram_allow_destructive = false`** (default) — the command
  returns a confirm prompt; the user must re-send the matching
  `<command> confirm <args>` within a short window to actually
  execute. This matches the SSH terminal's typed-hostname confirm
  pattern in the SPA.

### Restart flow quirk

A successful `sudo reboot` kills the SSH session before
`logic.ssh.run_command` can collect the exit code. The Telegram
`/restart` handler treats specific failure signals (`connection
closed`, exit_code 255) as success — the reboot fired even though
the runner couldn't observe completion.

### Adding a new command

One entry in `_COMMANDS` + one handler function:

```text
async def _cmd_status(client: httpx.AsyncClient,
                      args: list[str], msg: dict) -> None:
    """`/status` — fleet health summary."""
    # ... build reply ...
    await _send_reply(client, reply_text)


# add to _COMMANDS dict:
"/status": {
    "handler": _cmd_status,
    "usage": "/status",
    "description": "Fleet health summary",
    "category": "fleet",  # MUST tag a category — else it falls into "Other"
},
```

`/help` picks it up automatically (it iterates `_COMMANDS`). The
matching `category` MUST be one of the keys declared in `_cmd_help`'s
`categories` list (`getting_started` / `fleet` / `ops` / `account` /
`info`). Untagged commands silently fall into "Other".

For aliases, add a second entry with the same `handler` value + `"hidden":
True`. The `/help` renderer dedupes by handler and lists aliases as
`(aliases: /alt1, /alt2)`.

---

## Inbound AI chat

Any non-`/` text from the authorised chat falls through to the AI
palette via `_build_telegram_ai_context()` + `_ai_reply()`. The
listener:

1. Sends `sendChatAction(typing)` immediately for visual feedback.
2. Posts a `🤖 Thinking…` placeholder so the user sees something
   instantly.
3. Builds the fleet context block (hosts + items + weather + time +
   public IP if enabled).
4. Calls `build_palette_user_prompt` (shared with the SPA palette)
   so Telegram and the SPA palette feed the AI an identical record
   shape.
5. Calls the AI provider with `PALETTE_SYSTEM_PROMPT` + a
   Telegram-specific override block.
6. Strips every `ACTION:` / `MEMORY:` / `CHART_KIND:` directive line
   the AI emits (defence-in-depth — the override forbids them).
7. Runs the Markdown → Telegram-HTML rescue
   (`_markdown_to_telegram_html`) so `**bold**` / `## Header` /
   triple-backtick fences become real HTML tags.
8. Runs `_telegram_safe_escape` — preserves the recognised
   formatting tag set, escapes every other `<` / `>` so the parser
   doesn't HTTP-400 on stray markup.
9. Edits the placeholder in place with the final answer; falls back
   to a fresh `sendMessage` if the edit fails.

### Why a Telegram-surface override?

The SPA palette emits structured directives (`ACTION:`, `MEMORY:`,
etc.) that the SPA dispatches as inline command-palette executions.
Telegram is a READ-ONLY surface — the listener can't run inline SPA
actions. The override layer:

- **Strips the action license** — explicitly forbids the AI from
  emitting any directive prefixed `ACTION:` / `ACTION_HOSTS:` /
  `MEMORY:` / `MEMORY-FORGET:` / `CHART_KIND:`. If the user asks the
  AI to DO something (restart, pause, configure), the AI is told to
  redirect to the matching slash command or the SPA.
- **Injects the canonical `_COMMANDS` roster** as authoritative
  ground truth. Without this block, the AI hallucinates SPA-style
  commands (`/status`, `/services`, `/updates`, `/errors`,
  `/forecast`, `/update`, `/prune`) from training data because those
  feel plausible for a "fleet bot". With it, the AI cites ONLY real
  commands from the live roster.
- **Sets formatting expectations** — Telegram uses `parse_mode=HTML`,
  so the AI is told to use `<b>` / `<i>` / `<code>` tags rather than
  Markdown `**bold**` / `*italic*` / backticks. The
  `_markdown_to_telegram_html` rescue layer catches the leakage when
  the AI ignores this rule.
- **Sets length guidance** — terse fleet-state replies aim for under
  800 characters, but explanatory questions (weather / why-is-X-down
  / explain-this-metric / explain-this-incident) follow the
  upstream render contract's 3-5 sentence narrative paragraph.

### Account linking

Telegram users start unlinked — every command works without a link,
but the link associates the Telegram user_id with an OmniGrid local
user account so the AI can respect per-user weather location, the
audit trail attributes commands correctly, and admin-only commands
honour the user's role.

Linking flow:

1. In OmniGrid: Profile → Telegram → **Generate link code**. A
   single-use 8-character code is minted.
2. In Telegram: send `/link <code>` to the bot. The bot validates
   the code (one-shot, expires after ~10 minutes) and stores the
   `(telegram_user_id, omnigrid_username, linked_at)` triple.
3. `/whoami` (or `/myid`) confirms the link.
4. `/unlink` revokes the link (Telegram-side); the OmniGrid SPA
   shows the unlink in the Profile → Telegram card in real time
   via SSE.

---

## Tunables

Operator-tunable knobs for the Telegram surface. All read at point of
use so an Admin → Config edit takes effect on the next loop iteration
or send call without restart.

| Tunable                                     | Default | Range  | Where                    |
| ------------------------------------------- | ------- | ------ | ------------------------ |
| `tuning_telegram_long_poll_timeout_seconds`   | 25      | 1..50   | Notifications → Telegram |
| `tuning_telegram_http_timeout_seconds`        | 35      | 5..120  | Notifications → Telegram |
| `tuning_telegram_destructive_cooldown_seconds`| 30      | 1..600  | Notifications → Telegram — cool-down between typed-confirm destructive-command prompts per chat. |
| `tuning_telegram_ai_calls_per_minute`         | 6       | 1..120  | Notifications → Telegram — per-chat rate cap on free-text AI replies. |
| `tuning_telegram_bulk_update_concurrency`     | 4       | 1..16   | Notifications → Telegram — fan-out concurrency for `/update all`. |

Plain settings (managed via the Telegram tab UI):

| Setting                        | Default                    | Notes                                                                                                                                                                                                                                   |
| ------------------------------ | -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `telegram_bot_token`           | unset                      | Write-only secret (`_set` flag in API)                                                                                                                                                                                                  |
| `telegram_chat_id`             | unset                      | Destination chat ID, or CSV of multiple chats (e.g. `-1001234567890, 987654321`). Inbound: every listed chat is authorised. Outbound: notifications fan out to every listed chat. Replies route back to the chat the command came from. |
| `telegram_thread_id`           | unset                      | Optional supergroup topic id                                                                                                                                                                                                            |
| `telegram_verify_tls`          | true                       | Leave ON in production                                                                                                                                                                                                                  |
| `telegram_api_base`            | `https://api.telegram.org` | Override for self-hosted Bot API                                                                                                                                                                                                        |
| `telegram_listener_enabled`    | false                      | Master gate for the long-poll loop                                                                                                                                                                                                      |
| `telegram_allow_destructive`   | false                      | Skip typed-confirm on `/restart`                                                                                                                                                                                                        |
| `telegram_authorized_user_ids` | ""                         | CSV of Telegram user_ids                                                                                                                                                                                                                |
| `notify_medium_telegram`       | false                      | Master gate for outbound notifications                                                                                                                                                                                                  |

---

## Troubleshooting

- **Bot doesn't respond to `/help`** — check `telegram_listener_enabled`
  is ON in Admin → Notifications → Telegram, then check Admin → Logs
  for `[telegram_listener]` lines. Common causes: wrong chat id, bot
  not added to the supergroup, listener disabled.
- **Bot delivers notifications but ignores commands** — the inbound
  listener is gated separately from the outbound notification medium.
  Both `notify_medium_telegram` AND `telegram_listener_enabled` need
  to be ON for the full surface.
- **`/restart` returns "confirm required" even though I want it
  immediate** — flip `telegram_allow_destructive` ON. Personal-DM
  deploys typically run with this on; shared groups run with it off.
- **AI mentions a command that doesn't exist (`/status`, `/services`,
  etc.)** — confirm the system prompt's "CANONICAL command list" block
  is present. If you've added a new command, restart isn't required
  (the system prompt rebuilds per call from the live `_COMMANDS` dict).
- **AI replies render with literal `**bold**` instead of bold** — the
  Markdown → Telegram-HTML rescue layer should catch this, but for
  edge cases (nested formatting, unusual symbol sequences) the prompt
  is the primary line of defence. Tighten the prompt's "FORMATTING —
  Telegram HTML, NOT Markdown" block if a new model regresses.
- **HTTP 400 from `sendMessage`** — usually means an unmatched HTML
  tag survived the safe-escape pass. Check Admin → Logs for the
  `[notify] telegram` line + raw response body; common culprits are
  unterminated `<code>` / `<b>` pairs or stray `<` in code samples.
  The safe-escape layer SHOULD neutralise these, but adversarial
  inputs occasionally slip through.

---

## Operator-private deploys

The Bot API uses long-poll, so OmniGrid never exposes a public
webhook endpoint. The container can stay behind a reverse proxy,
Tailscale, or VPN with no port-forward — Telegram's servers initiate
the connection out to api.telegram.org. This is the recommended
deploy model for home-lab installs.

Webhook mode (operator hosts a public HTTPS endpoint, Telegram POSTs
to it) is NOT implemented in the current phase. Long-poll covers
every operator-visible feature without the deploy complexity of a
webhook endpoint.

---

## Related runbooks

- [Notifications + per-medium fan-out](api.md) — covers the in-app
  inbox + Apprise + Telegram medium model.
- [Authentication + WebAuthn + OIDC](auth.md) — the user account that
  Telegram links to is the same one OmniGrid auth manages.
- [Public IP](env_example.md) — `/ip` command consumes the
  `public_ip_enabled` subsystem.
- [Scheduler](scheduler.md) — schedule kinds can fire Apprise +
  Telegram notifications on completion.
