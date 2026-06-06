"""Telegram command handlers — split out of `logic.telegram_listener`
to keep both modules under the "uncomfortable to navigate" line-count
threshold.

Loading contract (mirrors `telegram_ai.py`):
  * This module is imported by `logic.telegram_listener` AT TOP LEVEL
    so the listener's `_COMMANDS` dispatch dict can reference each
    `_cmd_*` handler by name.
  * Listener helpers (`_listener()._send_reply`, `_listener()._resolve_target`, etc.) are
    accessed via the `_listener()` lazy shim — a one-attribute-access
    indirection per call site. The shim defers the actual import to
    call time so this module can finish loading BEFORE the listener
    is fully initialised (top-level cross-import would deadlock).
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any, Optional, cast

import httpx

_TL_CACHE: Any = None


def _listener() -> Any:
    """Return the loaded logic.telegram_listener module.

    Resolved lazily on the FIRST call (so cross-module imports don't
    deadlock at startup), then cached in a module-level slot so the
    ~140 per-dispatched-command lookups under steady-state Telegram
    traffic don't each run through Python's import + sys.modules dict
    lookup. Use as ``_listener()._helper_name(...)`` from every handler
    that needs a listener helper. Return annotation is ``Any`` deliberately
    so PyCharm/Pyright don't try to statically resolve attribute access
    through the shim — the names are stable per the cross-module
    contract but the IDE can't trace them (every `_listener()._foo` is
    a runtime lookup against the loaded module). Without `Any` the
    file would flood with ~140 false-positive "Access to a protected
    member" warnings.
    """
    global _TL_CACHE
    if _TL_CACHE is None:
        from logic import telegram_listener as _tl
        _TL_CACHE = _tl
    return _TL_CACHE


# ----------------------------------------------------------------------------
# Shared handler primitives — extracted to dedupe boilerplate that
# multiple `_cmd_*` handlers would otherwise repeat verbatim.
# ----------------------------------------------------------------------------
# noinspection PyProtectedMember
async def _gate_destructive(
    client: httpx.AsyncClient,
    msg: dict,
    *,
    command: str,
    confirm_command: str,
    confirm_action_html: str,
    is_confirm: bool,
) -> bool:
    """Shared destructive-command gate: confirm-required check + per-
    (sender, command) cooldown. Returns True when the caller should
    ABORT (helper already sent the appropriate reply); False when the
    caller should PROCEED with the destructive op.

    Captures the 2-stage pattern repeated across `_cmd_restart` /
    `_cmd_cleanup` / `_cmd_update`:

      1. If not is_confirm AND not _allow_destructive(): reply with
         the typed-confirm prompt ("reply with `<confirm_command>` to
         proceed"); return True.
      2. Else: check the per-(sender, command) cooldown; reply with
         the wait-time message if blocked; return True.
      3. Otherwise return False — caller proceeds with the actual
         destructive operation.

    `command` is the verb name without the leading slash (used as the
    cooldown key + as the user-facing label in the cooldown reply).
    `confirm_command` is the full "/restart confirm <target>" string
    the operator should reply with. `confirm_action_html` is the
    description that goes inside the typed-confirm prompt ("reboot
    <b>{label}</b>"). `is_confirm` is the caller's parsed
    "this IS the confirm reply" boolean.

    The 3 destructive handlers each had their own copy of this
    sequence — extracting kept them aligned + halved the regression
    risk class (a bug in the cooldown wording lands once, not three
    times)."""
    if not is_confirm and not _listener()._allow_destructive():
        await _listener()._send_reply(client, _listener()._destructive_confirm_text(
            confirm_command,
            confirm_action_html,
        ))
        return True
    sender_id_cd = (msg.get("from") or {}).get("id")
    allowed, wait_s = _listener()._destructive_cooldown_check(sender_id_cd, "/" + command)
    if not allowed:
        await _listener()._send_reply(
            client,
            f"⏳ <code>/{command}</code> is on cooldown — wait "
            f"{int(wait_s) + 1}s before re-running."
        )
        return True
    return False


# noinspection PyProtectedMember
async def _try_dispatch_skill_command(
    client: httpx.AsyncClient, head: str, args: list, msg: dict,
) -> bool:
    """Dynamic per-app SKILL slash command (``/run_speedtest`` /
    ``/adguard_status`` / ``/adguard_disable_5m`` / …).

    These commands are NOT registered in ``_COMMANDS`` — that keeps them
    OUT of the Telegram ``setMyCommands`` autocomplete menu (which would
    balloon with dozens of per-app skill verbs) while STILL being routed
    here and listed in ``/help``. The command name IS the skill ``id``
    (skill ids are app-prefixed + unique, e.g. ``adguard_status``), so a
    new skill auto-gets a command with no extra wiring — the routing is
    derived per skill from ``available_app_skills_context()``.

    Returns ``True`` when ``head`` matched a skill command (handled —
    including every error / disambiguation reply); ``False`` when it's
    not a skill command at all (the caller falls through to its generic
    "Unknown command" reply).
    """
    cmd = head.lstrip("/").strip().lower()
    if not cmd:
        return False
    from logic.apps.registry import (  # noqa: PLC0415
        available_app_skills_context, skills_for_slug, resolve_chip, run_app_skill,
    )
    esc = _listener()._escape
    ctx = available_app_skills_context()
    # Every pinned chip whose app declares a skill with id == cmd.
    matches = [
        ent for ent in ctx
        if any(isinstance(sk, dict) and str(sk.get("id") or "").lower() == cmd
               for sk in (ent.get("skills") or []))
    ]
    if not matches:
        return False  # not a skill command — let the caller 404 it

    # Skill commands dispatch real actions — require the sender be linked
    # (mirrors the non-open _COMMANDS gate + the AI dispatch path).
    sender_id = (msg.get("from") or {}).get("id")
    mapped = _listener()._lookup_omnigrid_user(sender_id) if sender_id is not None else None
    if not mapped:
        await _listener()._send_reply(
            client,
            "🔒 Link your account first. Generate a code in OmniGrid → "
            "Profile → Telegram, then reply with <code>/link &lt;code&gt;</code>.",
        )
        return True

    # Fleet skills aggregate across EVERY instance (run_skill ignores the
    # targeted chip), so they run HOST-LESS — no per-host arg, no "specify
    # a host" disambiguation. Per-instance skills (e.g. Speedtest) keep the
    # host resolution below.
    is_fleet = any(
        isinstance(sk, dict) and str(sk.get("id") or "").lower() == cmd
        and bool(sk.get("fleet"))
        for ent in matches for sk in (ent.get("skills") or []))
    # Arg-taking skills (e.g. Seerr's `seerr_request_movie <title>`) consume
    # their trailing text as a free-form ARGUMENT, not a host token — so we
    # don't try to host-disambiguate on the title. They run on the single
    # (or first) matching instance; multi-instance operators use the AI.
    takes_arg = any(
        isinstance(sk, dict) and str(sk.get("id") or "").lower() == cmd
        and bool(sk.get("arg"))
        for ent in matches for sk in (ent.get("skills") or []))

    # Resolve the target chip. A trailing `confirm` arg (destructive
    # two-step) is NOT a host token, so strip it before host matching.
    host_args = [a for a in args if str(a).strip().lower() != "confirm"]
    skill_arg = ""
    if takes_arg:
        # The whole trailing text IS the argument; pick the first instance.
        skill_arg = " ".join(str(a) for a in host_args).strip()[:512]
        host_args = []
    target = str(host_args[0]).strip().lower() if host_args else ""
    chosen = None
    if is_fleet or takes_arg:
        # Any instance — run_skill fans the action out (fleet) OR the arg
        # carries the real target (arg-taking), so the chip choice is moot.
        chosen = matches[0]
    elif target:
        for ent in matches:
            if target in (str(ent.get("host") or "").lower(),
                          str(ent.get("host_id") or "").lower()):
                chosen = ent
                break
        if chosen is None:
            hosts = ", ".join(
                f"<code>{esc(str(e.get('host') or e.get('host_id')))}</code>"
                for e in matches)
            await _listener()._send_reply(
                client,
                f"<code>/{esc(cmd)}</code> isn't available on "
                f"<code>{esc(target)}</code>. Available on: {hosts}.",
            )
            return True
    elif len(matches) == 1:
        chosen = matches[0]
    else:
        hosts = ", ".join(
            f"<code>{esc(str(e.get('host') or e.get('host_id')))}</code>"
            for e in matches)
        await _listener()._send_reply(
            client,
            f"<code>/{esc(cmd)}</code> runs on multiple hosts: {hosts}. "
            f"Specify one — e.g. <code>/{esc(cmd)} "
            f"{esc(str(matches[0].get('host') or matches[0].get('host_id')))}</code>.",
        )
        return True

    # Every branch above either set `chosen` to a dict or returned; this
    # guard makes that invariant explicit (and narrows `chosen` from
    # Any|None to dict for the .get() calls below).
    if not isinstance(chosen, dict):
        return True
    slug = str(chosen.get("slug") or "")
    host_id = str(chosen.get("host_id") or "")
    svc_idx = chosen.get("service_idx")
    # Action-target label: a fleet skill acts on EVERY instance, so name
    # the fleet ("all N hosts") rather than the single (arbitrary) chip we
    # dispatch against; a per-instance skill names its one host.
    if is_fleet and len(matches) > 1:
        host_label = f"all {len(matches)} hosts"
    else:
        host_label = str(chosen.get("host") or host_id)
    # Pull the FULL skill dict (available_app_skills_context only carries
    # {id, name}) so we can read the `destructive` flag for the gate.
    full_skill = next(
        (s for s in skills_for_slug(slug)
         if str(s.get("id") or "").lower() == cmd),
        {},
    )
    skill_name = str(full_skill.get("name") or cmd)

    # Destructive skills ride the same typed-confirm + cooldown gate as
    # /restart etc. The confirm reply appends `confirm`; for multi-host
    # apps the host token stays in the confirm command.
    if bool(full_skill.get("destructive")):
        is_confirm = any(str(a).strip().lower() == "confirm" for a in args)
        # Fleet skills are host-less, so the confirm reply is just
        # `/<cmd> confirm` (no host token).
        host_seg = "" if is_fleet else (f" {host_label}" if (target or len(matches) > 1) else "")
        abort = await _gate_destructive(
            client, msg,
            command=cmd,
            confirm_command=f"/{cmd}{host_seg} confirm",
            confirm_action_html=(f"run <b>{esc(skill_name)}</b> on "
                                 f"<b>{esc(host_label)}</b>"),
            is_confirm=is_confirm,
        )
        if abort:
            return True

    host_row, chip, rslug = resolve_chip(
        host_id, svc_idx if isinstance(svc_idx, int) else -1)
    if not (isinstance(chip, dict) and rslug):
        await _listener()._send_reply(
            client,
            f"Couldn't resolve the app instance for "
            f"<code>/{esc(cmd)}</code> on <code>{esc(host_label)}</code>.",
        )
        return True
    # Some skills take a few seconds (e.g. Seerr suggest queries the library +
    # TMDB across several pages). Show a VISIBLE "🤖 Thinking…" placeholder
    # bubble (more obvious than the typing indicator alone) and EDIT it in
    # place with the final result — same UX as the free-text AI path. Capture
    # its id; None when the send failed (the helpers then send fresh).
    _ph_id = await _listener()._send_reply(client, "🤖 <i>Thinking…</i>")
    # ALSO keep the native "Bot is typing…" indicator alive — Telegram clears
    # it after ~5s, so re-send every ~4s for the whole run. The keep-alive
    # task inherits this handler's chat ContextVar and is stopped in finally.
    _typing_stop = asyncio.Event()

    # noinspection PyProtectedMember
    async def _keep_typing() -> None:
        while not _typing_stop.is_set():
            await _listener()._send_chat_action(client, "typing")
            try:
                await asyncio.wait_for(_typing_stop.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                pass

    _typing_task = asyncio.create_task(_keep_typing())
    # Bound up front so it's always defined even if run_app_skill raises a
    # non-ValueError (which propagates out via the finally below — the
    # result-handling block past the try is then skipped).
    result: dict = {"ok": False, "detail": "skill did not run"}
    try:
        result = await run_app_skill(
            rslug, cmd, host_row, chip, host_id=host_id, service_idx=svc_idx,
            arg=skill_arg, actor_username=mapped)
    except ValueError as ve:
        result = {"ok": False, "detail": str(ve)}
    finally:
        _typing_stop.set()
        _typing_task.cancel()
        try:
            await _typing_task
        except asyncio.CancelledError:
            pass
    if isinstance(result, dict) and result.get("ok"):
        detail = str(result.get("detail") or "").strip()
        image_url = str(result.get("image_url") or "").strip()
        # Drop the image URL out of the TEXT body (the speedtest detail
        # appends it) — text replies set disable_web_page_preview so a bare
        # URL wouldn't render as a preview anyway. We send the image itself
        # inline via _send_photo below (same path the AI reply uses).
        if image_url and detail:
            detail = "\n".join(
                ln for ln in detail.split("\n") if ln.strip() != image_url
            ).strip()
        # Header on its OWN line, then the detail block on the lines below
        # (NOT inline after "—", which crammed the first stat row onto the
        # "Ran …" line and wrapped badly).
        body = f"✅ Ran <b>{esc(skill_name)}</b> on <code>{esc(host_label)}</code>"
        if detail:
            body += "\n" + esc(detail)
        # A skill result may carry a `followup` (e.g. Seerr suggest → request)
        # we surface as a one-tap inline button — same mechanism the AI reply
        # path uses. The full action is stashed server-side under a short
        # token (callback_data is 64-byte capped); the callback handler in
        # telegram_listener pops it + dispatches.
        reply_markup = None
        _fu = result.get("followup")
        if isinstance(_fu, dict) and _fu.get("skill_id"):
            _token = _listener().register_pending_action({
                "host_id": host_id, "service_idx": svc_idx,
                "skill_id": str(_fu.get("skill_id") or ""),
                "arg": str(_fu.get("arg") or ""),
            })
            reply_markup = {"inline_keyboard": [[{
                # Telegram inline buttons can't be colour-styled via the Bot
                # API, so a leading emoji makes the button stand out.
                "text": "🎬 " + str(_fu.get("label") or "Request on Seerr")[:116],
                "callback_data": "ssr:" + _token,
            }]]}
        # Replace the "🤖 Thinking…" bubble with the result text in place.
        await _listener()._replace_placeholder(client, _ph_id, body)
        if image_url:
            # Poster (with the follow-up button, if any) lands below the text.
            await _listener()._send_photo(client, image_url, reply_markup=reply_markup)
        elif reply_markup:
            # No poster but a follow-up button (rare) — _edit_message can't
            # carry a reply_markup, so send the button on a tiny follow-up.
            await _listener()._send_reply(client, "🎬 Tap to request:", reply_markup=reply_markup)
    else:
        detail = (result or {}).get("detail") or "failed"
        await _listener()._replace_placeholder(
            client, _ph_id,
            f"❌ <b>{esc(skill_name)}</b> failed: <code>{esc(str(detail))}</code>",
        )
    # Audit-trail parity with the web skill route (apps_routes.py writes a
    # `services_skill` row for every dispatch) — a Telegram-dispatched app
    # skill (e.g. a Pi-hole / AdGuard fleet enable/disable) is a state
    # mutation that MUST land in History so the operator can trace
    # "who ran what" across every actor. Telegram actor encodes the linked
    # OmniGrid user. Best-effort: never let an audit failure break the
    # reply (mirrors the dispatcher-level audit's defensive wrap).
    _ran_ok = bool(isinstance(result, dict) and result.get("ok"))
    # noinspection PyBroadException
    try:
        from logic.db import db_conn as _db_conn
        from logic.ops import write_admin_audit as _write_admin_audit
        with _db_conn() as _c:
            _write_admin_audit(
                _c, "services_skill",
                target_kind="host", target_name=host_label, target_id=host_id,
                actor=(f"telegram:{mapped}" if mapped else "telegram"),
                status=("success" if _ran_ok else "error"),
                message=(f"Ran skill '{cmd}' on {host_label} "
                         f"(service_idx={svc_idx}, fleet={is_fleet}, ok={_ran_ok})"),
            )
    except Exception:  # noqa: BLE001
        pass
    return True


# noinspection PyProtectedMember
async def _resolve_telegram_sender_id_int(client: httpx.AsyncClient, msg: dict) -> Optional[int]:
    """Pull `msg.from.id` out of an incoming Telegram update + coerce
    to int. Returns the int on success. On failure, sends the matching
    operator-facing reply + returns None — caller should return early.

    Used by `_cmd_link` + `_cmd_unlink` (both need the sender's
    numeric Telegram user_id before doing anything else). Extracted
    because the 11-line `(msg.get("from") or {}).get("id")` + None-check
    + `int(...)` + TypeError-handle pattern was duplicated."""
    sender_id = (msg.get("from") or {}).get("id")
    if sender_id is None:
        await _listener()._send_reply(client, "Can't read your Telegram user_id from the message.")
        return None
    try:
        # `sender_id` is `Any` after the None-check above; int() handles
        # str / int / float inputs uniformly and raises on the rest.
        return int(sender_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        await _listener()._send_reply(client, "Telegram user_id is not numeric — refusing.")
        return None


# noinspection PyProtectedMember
async def _require_user_weather_pref(
    client: httpx.AsyncClient, msg: dict,
) -> Optional[tuple[str, dict]]:
    """Resolve the linked OmniGrid user + their saved weather pref in
    one round-trip. Returns ``(username, loc)`` on success, or ``None``
    on every failure path (sender not linked, no saved location).

    The helper sends the matching operator-facing reply itself on every
    failure path, so the caller's only job is to early-return when the
    return value is None. Used by `_cmd_time` + `_cmd_weather` — both
    need the same lookup + the same error wording, and the 18-line
    boilerplate was previously duplicated verbatim across the two
    handlers. Return shape is ``Optional[tuple[str, dict]]`` (not a
    pair of Optionals) so the caller's narrowing `if result is None`
    propagates to BOTH unpacked names — without that PyCharm can't
    prove `username is not None` after the check."""
    sender_id = (msg.get("from") or {}).get("id")
    username = _listener()._lookup_omnigrid_user(sender_id) if sender_id is not None else None
    if not username:
        await _listener()._send_reply(
            client,
            "Link your account first. Generate a code in OmniGrid → "
            "Profile → Telegram, then reply with <code>/link &lt;code&gt;</code>."
        )
        return None
    loc = _listener()._load_user_weather_pref(username)
    if loc is None:
        await _listener()._send_reply(
            client,
            f"OmniGrid user <b>{_listener()._escape(username)}</b> has no weather "
            f"location saved. Open the topbar weather widget in "
            f"OmniGrid → click a city → Save."
        )
        return None
    # `_lookup_omnigrid_user` + `_load_user_weather_pref` return Any —
    # cast to the documented types so the caller's narrowing propagates
    # cleanly through the unpacked tuple.
    return cast(str, username), cast(dict, loc)


def _app_menu_cmd(slug: str) -> str:
    """Telegram-safe per-app skill-MENU command name derived from an app
    slug (lowercase, only ``[a-z0-9_]``). Used by BOTH ``/help`` (to print
    one tappable entry per app) AND the menu dispatcher below — they MUST
    agree, so the derivation lives here once. E.g. ``speedtest-tracker`` ->
    ``speedtest_tracker``, ``adguardhome`` -> ``adguardhome``."""
    import re as _re  # noqa: PLC0415
    return _re.sub(r"[^a-z0-9_]", "_", str(slug or "").lower()).strip("_") or "app"


def _grouped_app_skills() -> "list[tuple[str, dict]]":
    """Aggregate ``available_app_skills_context()`` per app: one group per
    app slug carrying its display name, the (id, name, fleet, arg) skills,
    and the hosts it's pinned on. Returns ``[(menu_cmd, group), …]`` in a
    stable order. Shared by ``/help`` + the menu dispatcher so they render
    the identical set."""
    from logic.apps.registry import available_app_skills_context  # noqa: PLC0415
    by_app: dict[str, dict[str, Any]] = {}
    order: list = []
    for ent in available_app_skills_context() or []:
        skills = [(str(s.get("id") or ""),
                   str(s.get("name") or s.get("id") or ""),
                   bool(s.get("fleet")), bool(s.get("arg")))
                  for s in (ent.get("skills") or [])
                  if isinstance(s, dict) and s.get("id")]
        if not skills:
            continue
        slug = str(ent.get("slug") or ent.get("app") or "")
        menu = _app_menu_cmd(slug)
        host = str(ent.get("host") or ent.get("host_id") or "").strip()
        g = by_app.get(menu)
        if g is None:
            g = {"app": str(ent.get("app") or ent.get("slug") or "app"),
                 "skills": skills, "hosts": []}
            by_app[menu] = g
            order.append(menu)
        if host and host not in g["hosts"]:
            g["hosts"].append(host)
    return [(m, by_app[m]) for m in order]


# noinspection PyProtectedMember
async def _try_dispatch_skill_menu_command(
    client: httpx.AsyncClient, head: str, msg: dict,
) -> bool:
    """Per-app skill MENU command (``/adguardhome`` / ``/seerr`` / …) — lists
    THAT app's skill commands. Keeps ``/help`` to ONE tappable entry per app
    instead of every skill verb. Like the skill commands themselves, these
    are NOT in ``_COMMANDS`` (so they stay out of the setMyCommands menu) and
    are routed from the ``meta is None`` branch AFTER the skill dispatcher.

    Returns ``True`` when ``head`` matched an app menu command (handled,
    incl. the link gate); ``False`` otherwise so the caller can 404 it."""
    cmd = head.lstrip("/").strip().lower()
    if not cmd:
        return False
    groups = dict(_grouped_app_skills())
    g = groups.get(cmd)
    if g is None:
        return False  # not an app menu command
    # The skill commands it lists are linked-only, so gate the menu too.
    sender_id = (msg.get("from") or {}).get("id")
    mapped = _listener()._lookup_omnigrid_user(sender_id) if sender_id is not None else None
    if not mapped:
        await _listener()._send_reply(
            client,
            "🔒 Link your account first. Generate a code in OmniGrid → "
            "Profile → Telegram, then reply with <code>/link &lt;code&gt;</code>.",
        )
        return True
    esc = _listener()._escape
    multi = len(g["hosts"]) > 1
    loc = (" @ " + ", ".join(esc(h) for h in g["hosts"])) if g["hosts"] else ""
    lines = [f"<b>🧠 {esc(g['app'])} skills</b>{loc}"]
    for sid, sname, sfleet, sarg in g["skills"]:
        if sarg:
            # Takes a free-form argument (e.g. a movie title) — render as
            # copyable code the user edits, not a one-tap command.
            lines.append(f"<code>/{esc(sid)} &lt;…&gt;</code> — {esc(sname)}")
        else:
            # No-arg (fleet OR host-disambiguated) — bare so Telegram makes
            # it a one-tap command; a multi-host per-instance skill tapped
            # bare just prompts for the host.
            hostarg = "" if sfleet else (" &lt;host&gt;" if multi else "")
            lines.append(f"/{esc(sid)}{hostarg} — {esc(sname)}")
    lines.append("<i>Tap a command above, or just ask me in plain text.</i>")
    await _listener()._send_reply(client, "\n".join(lines))
    return True


# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
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
    # in `_COMMANDS` with the matching key. The validation block below
    # WARN-logs any `_COMMANDS` entry whose `category` key is missing
    # from this list so the silent-fallthrough-to-"Other" failure mode
    # becomes operator-visible.
    categories: list[tuple[str, str]] = [
        ("getting_started", "📖 Getting started"),
        ("fleet", "🖥️ Fleet"),
        ("ops", "⚙️ Operations"),
        ("account", "🔗 Account"),
        ("info", "ℹ️ Info & weather"),
        ("misc", "🧩 Other"),
    ]
    cat_order = {key: idx for idx, (key, _) in enumerate(categories)}
    cat_headings = dict(categories)

    # Derive the in-use category set from `_COMMANDS` and surface a
    # one-shot WARN when a key has no heading metadata. Without this,
    # adding a new category to `_COMMANDS` without extending the
    # `categories` list above silently buckets the affected commands
    # under "🧩 Other" (line below uses `cat_headings.get(..., "🧩 Other")`
    # as the fallback). Visible failure mode > invisible drift.
    _tl = _listener()
    _used_cats = {
        (meta.get("category") or "misc")
        for meta in _tl._COMMANDS.values()
        if meta.get("handler") is not None
    }
    _missing_cats = _used_cats - set(cat_headings)
    new_warns = _missing_cats - _tl._WARNED_MISSING_CATS
    if new_warns:
        print(
            f"[telegram_listener] /help: category key(s) "
            f"{sorted(new_warns)!r} used in _COMMANDS have no entry in "
            f"the `categories` list — those commands will render under "
            f"'🧩 Other'. Add `(<key>, '<emoji> <heading>')` to the "
            f"`categories` list in `_cmd_help`."
        )
        _tl._WARNED_MISSING_CATS.update(new_warns)

    # Resolve the sender's identity so we can curate the menu:
    #   - is_linked: True when the sender's telegram_user_id maps to
    #     an OmniGrid user (via `ui_prefs.telegram_link_*`).
    #   - is_admin: True when that user's role is "admin".
    # Locked-command markers (🔓) are HIDDEN for linked users —
    # those operators already have access to everything they're
    # allowed to run, so the "open vs gated" distinction adds noise
    # rather than clarity. The trailing 🔓-legend paragraph is
    # likewise omitted when the sender is linked.
    sender_id = (msg.get("from") or {}).get("id") if isinstance(msg, dict) else None
    linked_user = _tl._lookup_omnigrid_user(sender_id) if sender_id is not None else None
    user_role = _tl._lookup_user_role(linked_user) if linked_user else None
    is_linked = bool(linked_user)
    is_admin = (user_role == "admin")

    # Weather-provider gate: when the weather feature is disabled
    # OR not configured (no key for WeatherAPI / master toggle off
    # for Open-Meteo) the /weather and /moon commands have nothing
    # to show and just produce "configure it" error messages on
    # invocation. Skip them entirely from /help in that case so
    # the menu stays accurate to what the operator can actually
    # use. When WeatherAPI is the active provider the "(requires
    # WeatherAPI.com provider)" qualifier on /moon's description
    # is also redundant — the requirement is met — so strip it.
    try:
        from logic import weather as _weather
        weather_enabled = bool(_weather.is_enabled())
        weather_has_moon = bool(_weather.supports_moon())
    except (ImportError, AttributeError):
        # `logic.weather` missing / API renamed: treat as unconfigured
        # so weather commands hide rather than render a misleading entry.
        weather_enabled = False
        weather_has_moon = False
    _WEATHER_GATED = {"/weather", "/moon"}

    # Public-IP gate: drop /ip from the help when the operator hasn't
    # enabled `public_ip_enabled` (Admin → Public IP). The command itself
    # stays registered so a /ip dispatch when the gate is off renders a
    # friendly "configure it first" message — but listing it under /help
    # is misleading when there's nothing to show.
    try:
        from logic.db import get_setting_bool as _get_setting_bool_pi
        from logic.settings_keys import Settings as _Settings_pi
        public_ip_enabled = _get_setting_bool_pi(_Settings_pi.PUBLIC_IP_ENABLED)
    except (ImportError, AttributeError, KeyError, ValueError, TypeError):
        public_ip_enabled = False

    # Prayer-times gate: drop /prayer + /hijri from help when the
    # operator hasn't enabled `prayer_times_enabled` (Admin → Prayer
    # Times). The commands stay registered (a dispatch when off renders a
    # friendly "ask an admin to enable it" message) but listing them is
    # misleading when there's nothing to show.
    try:
        from logic.db import get_setting_bool as _get_setting_bool2
        from logic.settings_keys import Settings as _Settings2
        prayer_enabled = _get_setting_bool2(_Settings2.PRAYER_TIMES_ENABLED)
    except (ImportError, AttributeError, KeyError, ValueError, TypeError):
        prayer_enabled = False
    _PRAYER_GATED = {"/prayer", "/hijri"}

    # First pass: group commands by handler (dedup aliases). Records
    # the FIRST occurrence as the primary for that handler — subsequent
    # entries become aliases regardless of `hidden`.
    groups: list[dict] = []
    handler_to_group: dict[Any, dict[str, Any]] = {}
    for name, meta in _tl._COMMANDS.items():
        handler = meta.get("handler")
        if handler is None:
            continue
        # Weather-provider curation: drop /weather + /moon when the
        # feature is fully disabled / unconfigured; drop /moon
        # specifically when the active provider doesn't supply
        # moon data (Open-Meteo). Aliases for the same handler
        # follow the primary command's verdict via the
        # handler-grouping below, so this gate only needs to
        # match the primary name.
        if not weather_enabled and name in _WEATHER_GATED:
            continue
        if name == "/moon" and not weather_has_moon:
            continue
        # Public-IP gate (parallel to the weather curation above).
        if name == "/ip" and not public_ip_enabled:
            continue
        # Prayer-times gate (parallel to the weather + public-IP curation).
        if not prayer_enabled and name in _PRAYER_GATED:
            continue
        # Linked-user gate: /link is the bootstrap for unmapped
        # senders ("paste the code from Profile → Telegram to claim
        # this chat-id"). Once linked, the command is noise — show
        # nothing to keep the menu accurate to what the operator
        # would actually run. The handler stays registered so a
        # /link dispatch from a linked user still responds with the
        # canonical "already linked" message.
        if name == "/link" and is_linked:
            continue
        existing = handler_to_group.get(handler)
        if existing is None:
            # WARN-log when a _COMMANDS entry is missing the `category`
            # key OR carries a falsy value. The dispatcher buckets the
            # command under "misc" (rendered as "🧩 Other") regardless
            # so the command still works — but the silent-default
            # behaviour masks "I forgot to tag the new command" during
            # authoring. Dedupe per (command-name) so each instance
            # WARNs exactly once per process.
            raw_cat = meta.get("category")
            if not raw_cat and name not in _tl._WARNED_MISSING_CMD_CAT:
                print(
                    f"[telegram_listener] /help: command {name!r} has "
                    f"no `category` key (or empty) in `_COMMANDS` — "
                    f"will render under '🧩 Other'. Add a category tag "
                    f"to the `_COMMANDS` entry."
                )
                _tl._WARNED_MISSING_CMD_CAT.add(name)
            group = {
                "primary_name": name,
                "primary": meta,
                "aliases": [],
                "category": raw_cat or "misc",
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
    # surfaces visually instead of silently dropping. Groups
    # containing ONLY `_OPEN_COMMANDS` entries render FIRST so
    # unmapped first-time users see what they can
    # actually run before scrolling past the gated commands. Stable
    # secondary sort by the declared `cat_order` so the within-tier
    # ordering still respects the operator-curated category order.
    # noinspection PyProtectedMember
    def _category_open_count(cat_key: str) -> int:
        return sum(
            1 for grp in by_cat.get(cat_key, [])
            if grp.get("primary_name") in _tl._OPEN_COMMANDS
        )

    rendered_cats = sorted(
        by_cat.keys(),
        key=lambda c: (
            # First key: NEGATIVE open-command ratio so categories
            # with more open commands surface first.
            -(_category_open_count(c) / max(1, len(by_cat.get(c, [])))),
            # Second key: original declared order.
            cat_order.get(c, len(cat_order)),
        ),
    )
    for cat in rendered_cats:
        heading = cat_headings.get(cat, "🧩 Other")
        # Annotate the heading with how many commands in this category
        # need a link, so an unmapped first-timer can skip gated
        # categories at a glance.
        cat_groups = by_cat[cat]
        # Linked users have access to everything they're allowed to
        # run, so the per-category "X of Y open" suffix is noise.
        # Only render the suffix for unmapped senders, where the
        # open / gated distinction actually drives "which commands
        # can I try right now".
        if is_linked:
            heading_suffix = ""
        else:
            open_count = sum(
                1 for g in cat_groups if g.get("primary_name") in _tl._OPEN_COMMANDS
            )
            if open_count == len(cat_groups):
                heading_suffix = " <i>(no link required)</i>"
            elif open_count == 0:
                heading_suffix = " <i>(/link required)</i>"
            else:
                heading_suffix = f" <i>({open_count} of {len(cat_groups)} open)</i>"
        lines.append(f"<b>{_listener()._escape(heading)}</b>{heading_suffix}")
        for g in cat_groups:
            primary_meta = g["primary"]
            primary_name = g["primary_name"]
            usage = _listener()._escape(primary_meta.get("usage") or primary_name)
            aliases = g["aliases"]
            # 🔓 marker on commands that bypass the omnigrid-user-mapping
            # gate so unmapped senders can see at a glance which ones
            # actually work pre-link. Read from `_OPEN_COMMANDS` (the
            # same set `_process_update` consults at dispatch time), so
            # adding / removing an open command is a one-line edit
            # that propagates to both the gate AND the help menu.
            #
            # HIDDEN for linked users — once the sender is mapped to an
            # OmniGrid account, the "open vs gated" distinction is
            # meaningless (they have access to everything they're
            # allowed to run). Suppressing the marker removes visual
            # noise for the most common operator case.
            if is_linked:
                open_marker = ""
            else:
                open_marker = "🔓 " if primary_name in _tl._OPEN_COMMANDS else ""
            if aliases:
                alias_text = ", ".join(_listener()._escape(a) for a in aliases)
                head = f"  {open_marker}<b>{usage}</b> <i>(aliases: {alias_text})</i>"
            else:
                head = f"  {open_marker}<b>{usage}</b>"
            # Double-escape guard: some legacy `_COMMANDS` descriptions
            # carry `&amp;` literally (e.g. `/whoami` / `/myid` stored
            # "level &amp; ID" pre-fix). Re-escaping them via `_listener()._escape`
            # produced `&amp;amp;` → visible as literal `&amp;` in
            # chat. Un-escape FIRST, then re-escape so the round-trip
            # collapses to a single `&amp;` regardless of source state.
            _raw_desc = (primary_meta.get("description") or "").replace("&amp;", "&")
            # Strip the "(requires WeatherAPI.com provider)" qualifier
            # from /moon's description when WeatherAPI IS the active
            # provider — the requirement is already met, so showing
            # it is redundant noise.
            if primary_name == "/moon" and weather_has_moon:
                _raw_desc = _raw_desc.replace(
                    " (requires WeatherAPI.com provider)", ""
                )
            description = _listener()._escape(_raw_desc)
            if description:
                lines.append(f"{head} — {description}")
            else:
                lines.append(head)
        lines.append("")  # blank line between categories

    # App skills — every per-app SKILL the AI can actually run for this
    # fleet (the app declares SKILLS AND its api_key is set on a pinned
    # chip). Mirrors the exact set the Telegram-AI context builds via
    # `available_app_skills_context()`, so the menu matches what a plain-
    # text request can trigger. Linked-only: the AI palette + skill
    # dispatch are /link-gated, so listing them to an unmapped sender
    # (who can't invoke them) would be misleading. The helper never
    # raises (returns [] on any failure), so no guard is needed.
    if is_linked:
        # ONE tappable entry per app — tapping the bare /<menu> command lists
        # that app's skill commands (handled by
        # _try_dispatch_skill_menu_command) instead of dumping every skill
        # verb here (which ballooned /help with a dozen+ /<app>_disable_*
        # lines per ad-blocker). Bare (not <code>) so Telegram renders the
        # menu command as a one-tap command. Grouping is shared with the menu
        # dispatcher via _grouped_app_skills() so the two always agree.
        _groups = _grouped_app_skills()
        if _groups:
            esc = _listener()._escape
            lines.append(
                "<b>🧠 App skills</b> <i>(tap an app to see its commands, "
                "or just ask — I'll run them)</i>"
            )
            for _menu, _g in _groups:
                _loc = (" @ " + ", ".join(esc(h) for h in _g["hosts"])) if _g["hosts"] else ""
                _n = len(_g["skills"])
                lines.append(
                    f"  • <b>{esc(_g['app'])}</b>{_loc} → /{esc(_menu)} "
                    f"<i>({_n} command{'s' if _n != 1 else ''})</i>"
                )
            lines.append("")

    # Trailing legend — the 🔓 paragraph is for unmapped senders
    # ONLY (linked users already know they have access). Linked
    # users see a shorter footer covering the rest of the
    # operationally-relevant context (targets, destructive-gate,
    # AI fallback).
    if is_linked:
        admin_note = (
            " You're signed in as <b>admin</b>." if is_admin
            else " You're signed in with <b>read-only</b> access "
                 "(write operations will decline)."
        )
        lines.append(
            "<i>🎯 Targets resolve by IP, host id, label, or asset "
            "short-name. ⚠️ Destructive commands (e.g. /restart) "
            "require a typed confirm step unless 'Allow destructive "
            "Telegram commands' is enabled in Admin. 💬 Any non-slash "
            "text is routed through the AI palette for a "
            f"conversational reply.{admin_note}</i>"
        )
    else:
        lines.append(
            "<i>🔓 = available without /link (everything else needs your "
            "Telegram account mapped to an OmniGrid user). 🎯 Targets "
            "resolve by IP, host id, label, or asset short-name. "
            "⚠️ Destructive commands (e.g. /restart) require a typed "
            "confirm step unless 'Allow destructive Telegram commands' "
            "is enabled in Admin. 💬 Any non-slash text is routed through "
            "the AI palette for a conversational reply — also gated on "
            "/link.</i>"
        )
    await _listener()._send_reply(client, "\n".join(lines))


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
# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
async def _cmd_hosts(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/hosts`` — split the curated fleet into three grouped lists:
    Active (enabled + no failure-state markers), Down (enabled but has
    at least one failure-state row — whole-host or per-provider — i.e.
    actually failing), and Disabled (turned off by config; this wins
    over a failure marker). Each group caps at 50 rows with a
    `…and N more` overflow line so a large fleet still fits inside
    Telegram's 4096-char message cap.
    """
    hosts = _listener()._load_hosts_config()
    if not hosts:
        await _listener()._send_reply(client, "No curated hosts configured.")
        return
    # /hosts now resolves the CANONICAL status (snapshot + reconcile + a
    # bounded per-host re-probe), which takes a couple seconds — show a
    # "working" placeholder so the chat isn't silent, then edit it in place
    # with the result (None-safe: a failed send just falls through to a
    # fresh reply at the end).
    placeholder_id = await _listener()._send_reply(
        client, "🔄 <i>Checking host status…</i>")
    paused_set = _load_host_paused_set()
    # Classify by the CANONICAL host status — the SAME effective view the web
    # Hosts page shows (snapshot + reconcile + per-host re-probe), via the
    # shared resolver. Pre-fix this command grouped "down" as ANY host with a
    # failure-state marker (whole-host OR per-provider), so a host that failed
    # on ONE provider but is reachable via another read as "down" here while
    # the web showed it up — operator-flagged 7-down-in-Telegram vs 1-on-web.
    status_by_id: dict[str, str] = {}
    # noinspection PyBroadException
    try:
        from logic.telegram_ai import resolve_host_status_rows
        _resp, _rows = await resolve_host_status_rows()
        status_by_id = {str(r.get("id") or ""): str(r.get("status") or "").lower()
                        for r in _rows if isinstance(r, dict)}
    except Exception as _e:  # noqa: BLE001
        print(f"[telegram_listener] /hosts status resolve failed: {_e}")
    active: list[dict] = []
    down: list[dict] = []
    unknown: list[dict] = []
    paused: list[dict] = []
    disabled: list[dict] = []
    for h in hosts:
        enabled = h.get("enabled", True)
        hid = h.get("id") or ""
        st = status_by_id.get(hid)
        # Disabled-by-config wins: an operator who turned a host OFF cares
        # that it's disabled, not its probe status.
        if not enabled:
            disabled.append(h)
        elif st == "down":
            down.append(h)
        elif st == "paused":
            paused.append(h)
        elif st == "unknown":
            unknown.append(h)
        elif st == "unconfigured":
            # Inventory-only row (no provider mapped) — not an outage.
            disabled.append(h)
        elif st == "up":
            active.append(h)
        else:
            # Status unavailable (resolver failed / host not returned) — fall
            # back to the failure-marker heuristic so the command still works.
            (down if hid in paused_set else active).append(h)

    # noinspection PyProtectedMember
    def _render_row(host_row: dict, status_emoji: str) -> str:
        row_id = host_row.get("id") or "(no-id)"
        label = host_row.get("label") or row_id
        addr = host_row.get("address") or ""
        return (f"{status_emoji} <code>{_listener()._escape(row_id)}</code> — {_listener()._escape(label)}"
                + (f" ({_listener()._escape(addr)})" if addr else ""))

    _hdr_bits = [f"{len(active)} active", f"{len(down)} down"]
    if unknown:
        _hdr_bits.append(f"{len(unknown)} unknown")
    if paused:
        _hdr_bits.append(f"{len(paused)} paused")
    _hdr_bits.append(f"{len(disabled)} disabled")
    out_lines: list[str] = ["<b>Curated hosts</b> — " + ", ".join(_hdr_bits)]

    # Three groups, each rendered only when non-empty. Active first, then
    # Down (enabled but failing — actionable), then Disabled (turned off
    # by config — informational). Each capped at 50 with an overflow line.
    # noinspection PyProtectedMember
    def _render_group(rows: list[dict], heading: str, emoji: str) -> None:
        if not rows:
            return
        out_lines.append("")
        out_lines.append(f"{emoji} <b>{heading}</b> ({len(rows)})")
        for host_row in rows[:50]:
            out_lines.append(_render_row(host_row, emoji))
        if len(rows) > 50:
            out_lines.append(f"<i>…and {len(rows) - 50} more.</i>")

    _render_group(active, "Active", "🟢")
    _render_group(down, "Down", "🔴")
    _render_group(unknown, "Unknown", "❓")
    _render_group(paused, "Paused", "⏸️")
    _render_group(disabled, "Disabled", "⚪")

    # Footer — disclose the cap dimension so operators understand WHY 50.
    # Telegram caps a single message at 4096 chars and the bot's
    # `_listener()._send_reply` would HTTP-400 above that. Surfacing the
    # SPA's Hosts view as the alternative gives operators a clear path to
    # the full list. Only fires when at least one group was truncated.
    if any(len(g) > 50 for g in (active, down, unknown, paused, disabled)):
        out_lines.append("")
        out_lines.append(
            "<i>Cap is 50 per group to fit Telegram's 4096-char message "
            "limit — use the SPA's Hosts view for the full list.</i>"
        )

    # Edit the working placeholder in place with the result (falls back to a
    # fresh reply if the placeholder send failed / the edit is rejected).
    await _listener()._replace_placeholder(client, placeholder_id, "\n".join(out_lines))


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
# noinspection SpellCheckingInspection
# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
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
        await _listener()._send_reply(client, "Usage: <code>/host &lt;target&gt;</code>")
        return
    target = " ".join(args)
    matched, candidates = _listener()._resolve_target(target)
    if await _listener()._reply_no_match_or_candidates(client, target, matched, candidates):
        return
    assert matched is not None  # narrowed by the helper's False branch

    host_id = matched.get("id") or ""
    label = matched.get("label") or host_id

    # Placeholder reply — operator sees acknowledgement immediately
    # while the live merge fans out across every configured provider
    # for this host (NE + Webmin + SNMP inline; Beszel + Pulse from
    # the cached batch maps). Capture the message_id so we can edit
    # in place when the final data is ready; if the send fails (rate
    # limit, transient HTTP), we fall through and the final body
    # arrives as a new message.
    placeholder_id = await _listener()._send_reply(
        client,
        f"🔄 Probing live providers for <b>{_listener()._escape(label)}</b>…",
    )

    # Cycle the placeholder emoji every ~3s so the operator gets a
    # visible "still working" signal during the 5-30s probe window.
    # Telegram's edit-rate limit accommodates ~1 edit/sec; 3s is well
    # under that. The task is cancelled below as soon as the live
    # merge resolves (success path edits the placeholder anyway).
    # noinspection PyProtectedMember
    async def _cycle_placeholder() -> None:
        if placeholder_id is None:
            return
        glyphs = ("⏳", "🔀", "🔄")
        idx = 0
        try:
            while True:
                await asyncio.sleep(3)
                idx = (idx + 1) % len(glyphs)
                # noinspection PyBroadException
                try:
                    await _listener()._edit_message(
                        client, placeholder_id,
                        f"{glyphs[idx]} Probing live providers for "
                        f"<b>{_listener()._escape(label)}</b>…",
                    )
                except Exception:  # noqa: BLE001
                    # Edit-rate hits / transient HTTP — silently stop
                    # cycling, the next handler-final edit will catch up.
                    return
        except asyncio.CancelledError:
            # Normal: the await-task path cancels us when probing
            # completes. Don't propagate — there's nothing meaningful
            # to do other than exit cleanly.
            return

    _placeholder_cycle_task = asyncio.create_task(_cycle_placeholder())

    async def _stop_cycle() -> None:
        """Cancel + await the placeholder-cycle task so it doesn't
        keep editing the final reply bubble after the handler exits.
        Safe to call multiple times; the task's `_done` flag short-
        circuits the second cancel."""
        if _placeholder_cycle_task.done():
            return
        _placeholder_cycle_task.cancel()
        try:
            await _placeholder_cycle_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            return

    # Live per-host merge — same code path /api/hosts/one/{id} runs.
    # Lazy-import dodges the circular dependency (main → listener via
    # lifespan; listener → main only at call-time). On any exception
    # (settings missing, hub outage, timeout) we fall back to the
    # cached snapshot below so the operator still gets a reply.
    data: dict | None = None
    snap_ts: float | None = None
    live_ok = False
    import_failed = False
    try:
        from main import _merge_one_host, _get_host_provider_state  # lazy
    except ImportError as imp_err:
        # Race with lifespan startup — main isn't fully loaded yet.
        # Tell the operator instead of silently falling through to the
        # snapshot path (which may also be empty on a fresh deploy).
        import_failed = True
        err = (f"⚠️ Backend still warming up. Try <code>/host {_listener()._escape(target)}</code> "
               f"again in a moment.\n<i>Internal: {_listener()._escape(str(imp_err))}</i>")
        await _stop_cycle()
        await _listener()._replace_placeholder(client, placeholder_id, err)
        return
    if not import_failed:
        try:
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
            err = f"❌ Snapshot read failed: <code>{_listener()._escape(str(e))}</code>"
            await _stop_cycle()
            await _listener()._replace_placeholder(client, placeholder_id, err)
            return
        entry = snap_map.get(host_id) or {}
        if isinstance(entry, dict):
            _ts = entry.get("ts")
            snap_ts = float(_ts) if isinstance(_ts, (int, float)) else None
            data = entry.get("data")
    if not isinstance(data, dict) or not data:
        # Surface the list of providers CONFIGURED on this host so
        # the operator can self-diagnose "wait, why no readings?".
        # Common cases: a `<provider>_name` typo (alias doesn't
        # resolve), the provider's master toggle is off in Admin →
        # Host stats, or `enabled: false` on the per-host sub-dict.
        # Without this hint the operator has to open the SPA's
        # host drawer to see which providers are mapped.
        # Provider name fields → human-readable labels for the hint.
        # Tuple-on-one-line form (vs the multi-line literal) to keep
        # PyCharm's "Incorrect whitespace" inspector happy on the
        # continuation indent.
        _provider_fields = (("snmp_name", "SNMP"), ("beszel_name", "Beszel"),
                            ("pulse_name", "Pulse"), ("webmin_name", "Webmin"),
                            ("ne_url", "node-exporter"))
        provider_hints: list[str] = []
        for field, label_human in _provider_fields:
            field_val = matched.get(field)
            if isinstance(field_val, str) and field_val.strip():
                provider_hints.append(label_human)
        ssh_raw = matched.get("ssh")
        if isinstance(ssh_raw, dict) and ssh_raw.get("enabled"):
            provider_hints.append("SSH")
        ping_raw = matched.get("ping")
        if isinstance(ping_raw, dict) and ping_raw.get("enabled"):
            provider_hints.append("Ping")
        if provider_hints:
            providers_line = (
                f"\n<i>Providers configured for this host: "
                f"{', '.join(_listener()._escape(p) for p in provider_hints)}. "
                f"Check Admin → Host stats master toggles + the per-"
                f"host name fields if this persists.</i>"
            )
        else:
            providers_line = (
                f"\n<i>No host-stats providers are mapped on this row. "
                f"Open Admin → Hosts and set at least one of "
                f"<code>snmp_name</code> / <code>beszel_name</code> / "
                f"<code>pulse_name</code> / <code>webmin_name</code> / "
                f"<code>ne_url</code>.</i>"
            )
        warn = (
            f"⚠️ No readings for <b>{_listener()._escape(label)}</b> yet. "
            f"Wait for the next probe cycle and try again."
            + providers_line
        )
        await _stop_cycle()
        await _listener()._replace_placeholder(client, placeholder_id, warn)
        return

    out: list[str] = [f"📊 <b>{_listener()._escape(label)}</b> "
                      f"(<code>{_listener()._escape(host_id)}</code>)"]

    # Optional system identity sub-line.
    plat = data.get("host_platform") or ""
    kern = data.get("host_kernel") or ""
    if plat or kern:
        bits = [b for b in (plat, kern) if b]
        out.append(f"<i>{_listener()._escape(' · '.join(bits))}</i>")
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
    # Canonical key is `host_uptime_s` (set by every provider's extractor).
    # `host_uptime_seconds` was a typo in the original implementation —
    # check both so legacy snapshots that happen to carry the older name
    # don't render as "no uptime".
    uptime_str = _fmt_uptime(
        data.get("host_uptime_s") or data.get("host_uptime_seconds")
    )
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
    if (
        isinstance(rx_total, (int, float))
        and isinstance(tx_total, (int, float))
        and (rx_total > 0 or tx_total > 0)
    ):
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
                bits.append(f"{_listener()._escape(str(tn))} {tc:.0f}°C")
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
            seg = _listener()._escape(str(name))
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
            extended.append(f"🔋 <b>UPS:</b>    {_listener()._escape(str(ups_status))}")
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
            extended.append(f"   <b>Battery state:</b> {_listener()._escape(str(bat_state))}")

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
    # placeholder handling. Cancel the emoji-cycle task first so it
    # doesn't race a final-edit and re-stamp the bubble with the
    # spinner emoji AFTER the actual reply lands.
    await _stop_cycle()
    await _listener()._replace_placeholder(client, placeholder_id, body)


# noinspection PyUnusedLocal
# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
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
        await _listener()._send_reply(client, "Usage: <code>/restart &lt;target&gt;</code>")
        return
    is_confirm = (args[0].lower() == "confirm")
    if is_confirm:
        if len(args) < 2:
            await _listener()._send_reply(client, "Usage: <code>/restart confirm &lt;target&gt;</code>")
            return
        target = " ".join(args[1:])
    else:
        target = " ".join(args)
    matched, candidates = _listener()._resolve_target(target)
    if await _listener()._reply_no_match_or_candidates(client, target, matched, candidates):
        return
    assert matched is not None  # narrowed by the helper's False branch

    # Destructive gate + per-(sender, command) cooldown — both routed
    # through the shared `_gate_destructive` helper so the typed-
    # confirm prompt + cooldown wait-time message stay aligned with
    # `_cmd_cleanup` and `_cmd_update`.
    host_id = matched.get("id") or ""
    if await _gate_destructive(
        client, msg,
        command="restart",
        confirm_command=f"/restart confirm {_listener()._escape(host_id)}",
        confirm_action_html=f"reboot <b>{_listener()._escape(matched.get('label') or host_id)}</b>",
        is_confirm=is_confirm,
    ):
        return
    # Execute via the standard SSH runner
    host_id = matched.get("id") or ""
    label = matched.get("label") or host_id
    await _listener()._send_reply(client, f"🔄 Restarting <b>{_listener()._escape(label)}</b>…")

    from logic import ssh as _ssh
    hosts = _listener()._load_hosts_config()
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
        await _listener()._send_reply(client, f"✅ Reboot command sent to <b>{_listener()._escape(label)}</b>.")
    else:
        await _listener()._send_reply(
            client,
            f"❌ Restart failed for <b>{_listener()._escape(label)}</b>: "
            f"<code>{_listener()._escape(result.get('error') or 'unknown error')}</code>"
        )


# (Imports for the handlers below live in the top-of-file block —
# the splitter previously duplicated them mid-file; that block is
# now consolidated upstream.)


# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
async def _cmd_version(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/version`` (aliased as ``/ver``) — show the running OmniGrid
    version. Reads the version baked into the image at build time
    (`/app/VERSION.txt` populated by the deploy pipeline's
    ``--build-arg VERSION=<X.Y.Z>``). Non-sensitive — works pre-link
    so unmapped operators can confirm which build they're talking to.

    Augmented with the baked image's build time + a
    short git SHA when available — operator scrolling Telegram for
    "is my deploy live yet?" gets the answer in one message without
    needing to also hit `/api/version`. Both fields are best-effort:
    build time comes from the `/app/VERSION.txt` file mtime (set by
    the Dockerfile's `RUN echo ... > /app/VERSION.txt`), git SHA
    from an optional `/app/GIT_SHA` file the deploy pipeline writes.
    Missing either → that field's line is omitted, never errors.
    """
    try:
        from logic.version import read_version
        version = read_version()
    # noinspection PyBroadException
    except Exception as e:
        await _listener()._send_reply(client, f"❌ Version lookup failed: <code>{_listener()._escape(str(e))}</code>")
        return
    # Build-time hint from VERSION.txt mtime. The Dockerfile writes
    # the file at build time so its mtime IS the build timestamp.
    # Defensive: if the file doesn't exist (running outside the
    # container) we skip the line entirely.
    #
    # Render the timestamp in the SENDER's preferred datetime format
    # (Profile → Formats `ui_prefs.datetime_format`) and in the
    # deployment's configured timezone (`scheduler_timezone` setting,
    # the canonical "what day is it for OmniGrid?" knob per the project conventions).
    # Falls back to UTC when neither side is available so unmapped
    # senders still see a coherent timestamp. The TZ abbreviation
    # (`CET` / `EEST` / `UTC` / etc.) is appended so operators across
    # time zones don't have to mentally translate.
    build_time_line = ""
    try:
        import os as _os
        from datetime import datetime as _dt, timezone as _tz
        version_file = "/app/VERSION.txt"
        if _os.path.exists(version_file):
            mtime = _os.path.getmtime(version_file)
            build_dt_utc = _dt.fromtimestamp(mtime, tz=_tz.utc)
            # Resolve TZ via the canonical scheduler_timezone setting;
            # fall back to UTC when unset or invalid.
            from logic.schedules import _scheduler_tz as _sched_tz_fn
            sched_tz = _sched_tz_fn()
            build_dt_local = build_dt_utc.astimezone(sched_tz) if sched_tz else build_dt_utc
            # Per-user format pref (falls back to the deployment-wide
            # default inside `get_user_datetime_format` when the sender
            # isn't mapped or hasn't set a pref).
            sender_id_v = (msg.get("from") or {}).get("id")
            username_v = _listener()._lookup_omnigrid_user(sender_id_v) if sender_id_v is not None else None
            from logic.datetime_fmt import (
                apply_datetime_format as _apply_fmt,
                get_user_datetime_format as _get_user_fmt,
            )
            user_fmt = _get_user_fmt(username_v or "")
            formatted = _apply_fmt(build_dt_local, user_fmt)
            tz_abbrev = build_dt_local.strftime("%Z") or "UTC"
            build_time_line = (
                f"\n🕓 Built: <i>{_listener()._escape(formatted)}</i> "
                f"<code>{_listener()._escape(tz_abbrev)}</code>"
            )
    except (OSError, ValueError, ImportError):
        build_time_line = ""
    # Optional git SHA — the deploy pipeline may write a `/app/GIT_SHA`
    # file (one line, short SHA). When absent, skip cleanly.
    sha_line = ""
    try:
        from pathlib import Path as _Path
        sha_path = _Path("/app/GIT_SHA")
        if sha_path.exists():
            sha_raw = sha_path.read_text(encoding="utf-8").strip()
            # Defensive: only accept hex SHA up to 40 chars so a
            # corrupted file doesn't render arbitrary text.
            if sha_raw and len(sha_raw) <= 40 and all(c in "0123456789abcdefABCDEF" for c in sha_raw):
                sha_line = f"\n🔖 SHA: <code>{_listener()._escape(sha_raw[:12])}</code>"
    except (OSError, ValueError):
        sha_line = ""

    if not version or version == "0.0.0-dev":
        # Dev build (no --build-arg VERSION passed) — call it out so
        # the operator knows they're not on a tagged release.
        await _listener()._send_reply(
            client,
            f"📦 OmniGrid <b><code>{_listener()._escape(version or '0.0.0-dev')}</code></b>\n"
            f"<i>Unversioned build — built locally without "
            f"<code>--build-arg VERSION</code>.</i>"
            + build_time_line + sha_line
        )
        return
    await _listener()._send_reply(
        client,
        f"📦 OmniGrid <b><code>{_listener()._escape(version)}</code></b>"
        + build_time_line + sha_line
    )


# noinspection PyUnusedLocal
# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
async def _cmd_ip(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/ip`` — show the deployment's public IP + ISP / ASN / country
    via the same lookup the AI palette uses (ifconfig.co JSON). Gated
    on the `public_ip_enabled` setting (default OFF for privacy);
    refuses cleanly with a link to Admin → Public IP when off.
    Non-sensitive command — works pre-link so unmapped operators can
    confirm the deploy's external network identity for support
    purposes."""
    from logic import public_ip as _public_ip
    if not _public_ip.is_enabled():
        await _listener()._send_reply(
            client,
            "🔒 Public-IP lookup is disabled. Enable it in OmniGrid → "
            "Admin → Public IP first (it gates the outbound "
            "ifconfig.co call)."
        )
        return
    data = await _public_ip.fetch()
    if data is None:
        await _listener()._send_reply(
            client,
            "❌ Public-IP lookup failed (network blip or ifconfig.co "
            "outage). Check Admin → Logs for the [public_ip] line."
        )
        return
    bits: list[str] = []
    if data.get("ip"):
        bits.append(f"🌐 <b>IP:</b>      <code>{_listener()._escape(data['ip'])}</code>")
    if data.get("isp"):
        bits.append(f"🏢 <b>ISP:</b>     {_listener()._escape(data['isp'])}")
    if data.get("asn"):
        bits.append(f"🔢 <b>ASN:</b>     {_listener()._escape(data['asn'])}")
    if data.get("city") or data.get("country"):
        loc_parts: list[str] = []
        for field in ("city", "country"):
            v = data.get(field)
            if isinstance(v, str) and v.strip():
                loc_parts.append(v)
        if loc_parts:
            bits.append(f"📍 <b>Location:</b> {_listener()._escape(', '.join(loc_parts))}")
    if not bits:
        await _listener()._send_reply(
            client,
            "⚠️ Public-IP lookup returned empty — ifconfig.co may have "
            "rate-limited or changed its schema."
        )
        return
    await _listener()._send_reply(client, "\n".join(bits))


# noinspection PyUnusedLocal
# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
async def _cmd_whoami(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """Debug aid — tells the user their Telegram user_id + the
    OmniGrid username they're linked to (or that they aren't) + their
    access level (role). Aliased as /myid."""
    sender = (msg.get("from") or {})
    sender_id = sender.get("id")
    sender_name = (sender.get("username") or sender.get("first_name") or "").strip()
    mapped = _listener()._lookup_omnigrid_user(sender_id) if sender_id is not None else None
    if mapped:
        role = _listener()._lookup_user_role(mapped) or "unknown"
        # Map the role to a friendly access-level label + emoji so the
        # operator's permissions are immediately legible.
        role_emoji = {"admin": "🛡", "readonly": "👁"}.get(role, "❓")
        role_label = {
            "admin": "Admin (full access)",
            "readonly": "Read-only (no write actions)",
        }.get(role, role)
        await _listener()._send_reply(
            client,
            f"You are linked to OmniGrid user <b>{_listener()._escape(mapped)}</b>.\n"
            f"{role_emoji} Access level: <b>{_listener()._escape(role_label)}</b>\n"
            f"<i>Telegram user_id: <code>{sender_id}</code></i>"
        )
    else:
        await _listener()._send_reply(
            client,
            f"You aren't linked to any OmniGrid user yet.\n\n"
            f"<i>Telegram user_id: <code>{sender_id}</code></i>\n"
            f"<i>Telegram username: @{_listener()._escape(sender_name) or 'unknown'}</i>\n"
            f"❓ Access level: <b>none</b> — unlinked\n\n"
            f"Generate a link code in OmniGrid → Profile → Telegram, then "
            f"reply with <code>/link &lt;code&gt;</code>."
        )


# noinspection PyUnusedLocal
# noinspection SpellCheckingInspection
# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
async def _cmd_time(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/time`` — show the current local time at the linked user's
    saved weather location. Uses Open-Meteo's resolved IANA timezone
    (returned alongside the weather response) so daylight-saving + tz
    boundaries stay accurate without a separate geocoder lookup."""
    result = await _require_user_weather_pref(client, msg)
    if result is None:
        return
    username, loc = result
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
        await _listener()._send_reply(client, f"❌ Time lookup failed: <code>{_listener()._escape(str(e))}</code>")
        return
    if not isinstance(data, dict) or data.get("error"):
        err = (data or {}).get("error") if isinstance(data, dict) else "no response"
        await _listener()._send_reply(
            client,
            f"❌ Time lookup upstream error: <code>{_listener()._escape(str(err))}</code>"
        )
        return
    # Cross-provider timezone resolution — api_weather's response
    # shape differs by provider:
    #   - Open-Meteo: top-level `timezone` (IANA name) + `timezone_abbrev`
    #   - WeatherAPI.com: nested under `location.tz_id` (IANA name)
    # Reading both keys covers either provider; first non-empty wins.
    _loc = data.get("location") if isinstance(data.get("location"), dict) else {}
    tz_name = (
        str(data.get("timezone") or "").strip()
        or str(_loc.get("tz_id") or "").strip()
    )
    tz_abbrev = str(data.get("timezone_abbrev") or "").strip()
    if not tz_name:
        await _listener()._send_reply(
            client,
            f"<b>{_listener()._escape(label)}</b>: no timezone returned by the "
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
            await _listener()._send_reply(client, f"❌ Time format failed: <code>{_listener()._escape(str(e2))}</code>")
            return
    formatted = _apply_fmt(now_local, user_fmt)
    tz_suffix = f" ({_listener()._escape(tz_abbrev)})" if tz_abbrev else f" ({_listener()._escape(tz_name)})"
    await _listener()._send_reply(
        client,
        f"🕒 <b>{_listener()._escape(label)}</b>\n"
        f"<code>{_listener()._escape(formatted)}</code>{tz_suffix}{_listener()._escape(offset_note)}"
    )


# noinspection PyUnusedLocal
# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
def _cleanup_list_lines(removables: list, max_shown: int = 40) -> list[str]:
    """Grouped-by-stack ``• <name> [stopped|orphan]`` lines for the cleanup
    preview AND the execute reply, so the user always sees WHICH containers
    are being removed (not just a count). Capped at ``max_shown`` for
    Telegram's 4096-char wire limit; stacks sorted by descending member
    count so the biggest groups surface first."""
    by_stack: dict[str, list[dict]] = {}
    for i in removables:
        stack = i.get("stack") or "(no stack)"
        by_stack.setdefault(stack, []).append(i)
    out: list[str] = []
    shown = 0
    for stack in sorted(by_stack.keys(), key=lambda s: (-len(by_stack[s]), s)):
        group = by_stack[stack]
        out.append(f"<b>{_listener()._escape(stack)}</b> <i>({len(group)})</i>")
        for i in group:
            if shown >= max_shown:
                break
            name = i.get("name") or i.get("raw_id") or "(unknown)"
            kind = i.get("type") or "container"
            tag = "orphan" if kind == "orphan" else "stopped"
            out.append(f"  • <code>{_listener()._escape(name)}</code> <i>[{tag}]</i>")
            shown += 1
        if shown >= max_shown:
            break
    if len(removables) > shown:
        out.append(f"<i>…and {len(removables) - shown} more.</i>")
    return out


# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
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
        await _listener()._send_reply(client, f"❌ gather import failed: <code>{_listener()._escape(str(e))}</code>")
        return
    # noinspection PyProtectedMember
    items = list(_gather._cache.get("items") or [])
    removables = [i for i in items if i.get("removable")]

    if not removables:
        await _listener()._send_reply(
            client,
            "✅ Nothing to clean up — no stopped / failed / orphan "
            "containers in the current snapshot."
        )
        return

    # Destructive gate
    if not is_confirm and not _listener()._allow_destructive():
        # Render preview list, prompt for /cleanup confirm.
        lines = [
            f"🧹 <b>{len(removables)} container(s) eligible for cleanup</b>",
            "",
        ]
        # Group-by-stack list (shared with the execute reply so both show
        # WHICH containers, not just a count).
        lines.extend(_cleanup_list_lines(removables))
        lines.append("")
        lines.append(_listener()._destructive_confirm_text(
            "/cleanup confirm",
            f"remove all {len(removables)} container(s)",
        ))
        await _listener()._send_reply(client, "\n".join(lines))
        return

    # Execute path — same in-process Operations pipeline the SPA uses.
    # Per-(sender, command) cooldown via the shared `_gate_destructive`
    # helper — passes is_confirm=True since the multi-line preview-list
    # branch above already handled the typed-confirm prompt path.
    # Returns True when the caller should ABORT (helper sent the wait-
    # time reply).
    sender_id = (msg.get("from") or {}).get("id")
    if await _gate_destructive(
        client, msg,
        command="cleanup",
        confirm_command="/cleanup confirm",
        confirm_action_html=f"remove all {len(removables)} container(s)",
        is_confirm=True,
    ):
        return
    # Resolve the actor (linked OmniGrid username) so the history rows
    # the Ops persist carry the right attribution.
    actor = _listener()._lookup_omnigrid_user(sender_id) if sender_id is not None else None
    actor = actor or "telegram"

    try:
        from logic import ops as _ops_mod
    # noinspection PyBroadException
    except Exception as e:
        await _listener()._send_reply(client, f"❌ ops import failed: <code>{_listener()._escape(str(e))}</code>")
        return

    await _listener()._send_reply(
        client,
        f"🧹 <b>Removing {len(removables)} container(s)…</b>\n"
        + "\n".join(_cleanup_list_lines(removables))
        + "\n<i>(SPA tabs will refresh as each one completes)</i>"
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
            # exactly what the SPA listens for. Lazy main import +
            # `spawn_background_task` honours the strong-ref + done-
            # callback contract (see the project conventions "Background-task
            # lifecycle") so the spawn survives asyncio GC.
            import main as _main
            _main.spawn_background_task(
                _ops_mod.do_remove_container(op, raw_id),
                label=f"telegram-cleanup-{raw_id[:12]}",
            )
            spawned += 1
        # noinspection PyBroadException
        except Exception as e:
            print(f"[telegram_listener] spawn remove for {raw_id[:12]} failed: {e}")

    # Per-container `remove_container` ops already write their own
    # history rows via the do_remove_container path; the dispatcher-
    # level `telegram_command` row covers the batch entry-point.
    await _listener()._send_reply(
        client,
        f"✅ Spawned {spawned} cleanup Operation(s). Watch the SPA's "
        f"Live panel or History tab to follow progress."
    )


# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
async def _cmd_update(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/update`` — pull-and-recreate stacks / containers whose remote
    image digest differs from what's running locally.

    Usage:
      - ``/update`` (no args) — preview list of every item flagged
        with ``update_available=true`` in the current gather snapshot.
      - ``/update all`` — spawn an update Operation for EVERY updatable
        item (stack → ``update_stack``, container → ``update_container``).
        Same destructive-gate as ``/cleanup`` / ``/restart``: requires
        ``telegram_allow_destructive=true`` OR a typed
        ``/update all confirm`` follow-up.
      - ``/update <name>`` — single-item update. Resolves by exact
        item name (stack name OR container name) — multiple matches
        return the candidate list and abort. Destructive-gate also
        applies; supply ``/update <name> confirm`` to skip when the
        global toggle is off.

    Background:
      Spawns Operations the same way the SPA's per-row "Update" button
      does. Each Operation publishes ``op:created`` / ``op:updated`` /
      ``op:completed`` SSE events so every open SPA tab sees the
      progress + the gather cache invalidates on completion.
    """
    try:
        from logic import gather as _gather
    except (ImportError, AttributeError) as e:
        await _listener()._send_reply(client, f"❌ gather import failed: <code>{_listener()._escape(str(e))}</code>")
        return

    # Pull the live snapshot. Cold-cache path: tell the user to wait
    # rather than triggering a refresh inline (the cleanup command
    # has the same convention).
    # noinspection PyProtectedMember
    items = list(_gather._cache.get("items") or [])
    # Canonical "needs an update" signal is `status == "update"` —
    # gather.py:enrich() sets the status when the remote-digest
    # comparison shows drift. There's no separate `update_available`
    # field on items; the Telegram filter must read `status`. Orphan-
    # type containers (Swarm task containers left over from the
    # PREVIOUS image — already replaced by Swarm via /cleanup target)
    # are EXCLUDED — they're scheduled for removal, not for re-update,
    # and the operator-reported regression was them appearing here.
    updatable = [
        i for i in items
        if (
            (i.get("status") or "") == "update"
            and (i.get("type") or "") != "orphan"
        )
    ]
    if not items:
        await _listener()._send_reply(
            client,
            "⏳ Cache is empty — open the SPA once or wait for the next "
            "gather tick (~15 min), then re-run <code>/update</code>."
        )
        return

    # No args: render preview list, return.
    if not args:
        if not updatable:
            await _listener()._send_reply(
                client,
                "✅ Nothing to update — every stack and container is on its "
                "latest remote digest."
            )
            return
        lines = [
            f"🔄 <b>{len(updatable)} item(s) with updates available</b>",
            "",
            "Send <code>/update all</code> to update every item, OR "
            "<code>/update &lt;name&gt;</code> for a single item.",
            "",
        ]
        # Group by stack for readability, mirror /cleanup's pattern.
        by_stack: dict[str, list[dict]] = {}
        for i in updatable:
            stack = i.get("stack") or "(no stack)"
            by_stack.setdefault(stack, []).append(i)
        shown = 0
        max_shown = 40
        for stack in sorted(by_stack.keys()):
            group = by_stack[stack]
            lines.append(f"<b>{_listener()._escape(stack)}</b>")
            for i in group:
                if shown >= max_shown:
                    break
                kind = i.get("type") or "item"
                name = i.get("name") or "?"
                lines.append(f"  • <code>{_listener()._escape(str(name))}</code> <i>({_listener()._escape(str(kind))})</i>")
                shown += 1
            lines.append("")
            if shown >= max_shown:
                break
        if len(updatable) > max_shown:
            lines.append(f"<i>…and {len(updatable) - max_shown} more</i>")
        await _listener()._send_reply(client, "\n".join(lines))
        return

    # Args present — `all` or `<name>`. Look at the LAST arg for the
    # "confirm" hint so both `/update <name> confirm` and `/update all
    # confirm` work.
    is_confirm = bool(args) and args[-1].lower() == "confirm"
    body_args = args[:-1] if is_confirm else list(args)
    target = " ".join(body_args).strip().lower()

    # Resolve the target set.
    if target == "all":
        if not updatable:
            await _listener()._send_reply(
                client,
                "✅ Nothing to update — every stack and container is on its "
                "latest remote digest."
            )
            return
        targets = updatable
    else:
        # Exact-name match against updatable items first; fall through
        # to substring across ALL items if no exact hit.
        exact = [i for i in updatable if (i.get("name") or "").lower() == target]
        if exact:
            targets = exact
        else:
            partial = [
                i for i in items
                if (
                    (i.get("status") or "") == "update"
                    and (i.get("type") or "") != "orphan"
                    and target in (i.get("name") or "").lower()
                )
            ]
            if not partial:
                # Last-resort: tell the operator nothing matched.
                await _listener()._send_reply(
                    client,
                    f"🤷 No updatable item matches <code>{_listener()._escape(target)}</code>. "
                    f"Send <code>/update</code> with no args to see the list."
                )
                return
            if len(partial) > 1:
                names = ", ".join(
                    f"<code>{_listener()._escape(str(i.get('name')))}</code>"
                    for i in partial[:8]
                )
                more = f" (and {len(partial) - 8} more)" if len(partial) > 8 else ""
                await _listener()._send_reply(
                    client,
                    f"❓ Multiple matches for <code>{_listener()._escape(target)}</code>: "
                    f"{names}{more}. Re-send with the EXACT item name."
                )
                return
            targets = partial

    # Destructive gate. `n` is hoisted out of the `if not is_confirm`
    # branch because the post-confirm cooldown call below references
    # it too — otherwise `n` would be referenced before assignment on
    # the is_confirm=True path.
    n = len(targets)
    if not is_confirm and not _listener()._allow_destructive():
        lines = [
            f"⚠️ <b>{n} item(s) will be updated</b> — pull-and-recreate, "
            "brief downtime for each.",
            "",
        ]
        for i in targets[:10]:
            stack = i.get("stack") or "(no stack)"
            lines.append(
                f"  • <code>{_listener()._escape(str(i.get('name')))}</code> "
                f"<i>({_listener()._escape(stack)})</i>"
            )
        if n > 10:
            lines.append(f"  <i>…and {n - 10} more</i>")
        lines.append("")
        confirm_command = (
            "/update all confirm" if target == "all"
            else f"/update {_listener()._escape(target)} confirm"
        )
        lines.append(_listener()._destructive_confirm_text(confirm_command, "proceed"))
        await _listener()._send_reply(client, "\n".join(lines))
        return

    # Per-(sender, command) cooldown via the shared `_gate_destructive`
    # helper — passes is_confirm=True since the multi-line preview-list
    # branch above already handled the typed-confirm prompt path.
    sender_id = (msg.get("from") or {}).get("id")
    if await _gate_destructive(
        client, msg,
        command="update",
        confirm_command=(
            "/update all confirm" if target == "all"
            else f"/update {_listener()._escape(target)} confirm"
        ),
        confirm_action_html=f"update {n} item(s)",
        is_confirm=True,
    ):
        return
    # Fire the updates. Each item gets its own Operation. Mirrors
    # the SPA's per-row "Update" button.
    try:
        from logic.ops import new_op, do_update_stack, do_update_container
    except (ImportError, AttributeError) as e:
        await _listener()._send_reply(client, f"❌ ops import failed: <code>{_listener()._escape(str(e))}</code>")
        return

    spawned = 0
    skipped = 0
    actor_username = _listener()._lookup_omnigrid_user(sender_id) or "telegram"
    # Dedupe stacks across targets — multiple items can share a parent
    # stack (e.g. a service item + its orphan task containers + the
    # stack item itself), and triple-firing the same do_update_stack
    # races against itself. Track every stack we've already spawned.
    spawned_stacks: set[int] = set()
    # Bound /update all fan-out via a semaphore. Pre-bound this was
    # unbounded — N=50 pending updates fanned out 50 parallel
    # Portainer PUTs, overwhelming the daemon and starving operator-
    # triggered updates. Each spawned op now goes through
    # `_bounded_op_coro(coro)` which acquires the semaphore before
    # firing the actual Portainer write. Cap is operator-tunable via
    # `tuning_telegram_bulk_update_concurrency` (default 4, range
    # 1..16). Per-call read so Admin → Config edits take effect on
    # the next /update invocation without a listener restart.
    from logic.tuning import Tunable, tuning_int
    bulk_cap = max(1, tuning_int(Tunable.TELEGRAM_BULK_UPDATE_CONCURRENCY))
    _bulk_sem = asyncio.Semaphore(bulk_cap)

    async def _bounded_op_coro(coro):
        """Acquire the bulk-update semaphore before awaiting the op
        coroutine. Spawn sites wrap their `do_update_*(op, target)`
        coro in this so the actual Portainer fan-out stays bounded
        at the operator's chosen concurrency cap.
        """
        async with _bulk_sem:
            await coro

    for i in targets:
        kind = i.get("type") or ""
        name = i.get("name") or ""
        raw_id = i.get("raw_id") or i.get("id") or ""
        stack_name = i.get("stack") or ""
        stack_id = i.get("stack_id")
        if not name or not raw_id:
            skipped += 1
            continue
        try:
            if kind == "stack":
                # Stack id is numeric; coerce defensively.
                try:
                    sid = int(raw_id)
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                if sid in spawned_stacks:
                    continue
                spawned_stacks.add(sid)
                op = new_op(
                    "update_stack", str(sid), name,
                    target_stack=name, actor=f"telegram:{actor_username}",
                )
                # Lazy main import + `spawn_background_task` (see the project conventions
                # "Background-task lifecycle") — strong-ref + done-callback
                # so the spawn survives asyncio GC mid-execution. The
                # coro is wrapped in `_bounded_op_coro` so the
                # actual Portainer fan-out stays under the operator-
                # tunable cap.
                import main as _main
                _main.spawn_background_task(
                    _bounded_op_coro(do_update_stack(op, sid)),
                    label=f"telegram-update-stack-{sid}",
                )
                spawned += 1
            elif kind == "container":
                # Standalone (non-Swarm) container — direct recreate
                # works because there's no overlay network with
                # attachable=false to fight.
                op = new_op(
                    "update_container", str(raw_id), name,
                    target_stack=stack_name or None,
                    actor=f"telegram:{actor_username}",
                )
                import main as _main
                _main.spawn_background_task(
                    _bounded_op_coro(do_update_container(op, str(raw_id))),
                    label=f"telegram-update-container-{str(raw_id)[:12]}",
                )
                spawned += 1
            elif kind in ("service", "orphan"):
                # Swarm services + orphan Swarm task containers can't
                # be updated via the container-recreate endpoint —
                # Docker rejects attach to overlay networks that
                # aren't manually attachable. The canonical path is
                # to re-deploy the PARENT STACK. Dedupe by stack_id
                # so /update all on a stack with 3 services + 2 orphans
                # fires ONE stack update, not five racing operations.
                try:
                    sid = int(stack_id) if stack_id is not None else 0
                except (TypeError, ValueError):
                    sid = 0
                if not sid:
                    # No stack association — nothing we can route to.
                    # Skip gracefully so a mixed batch still fires
                    # the others.
                    skipped += 1
                    continue
                if sid in spawned_stacks:
                    continue
                spawned_stacks.add(sid)
                target_label = stack_name or name
                op = new_op(
                    "update_stack", str(sid), target_label,
                    target_stack=target_label,
                    actor=f"telegram:{actor_username}",
                )
                import main as _main
                _main.spawn_background_task(
                    _bounded_op_coro(do_update_stack(op, sid)),
                    label=f"telegram-update-stack-from-service-{sid}",
                )
                spawned += 1
            else:
                # Unknown item type — skip rather than guess.
                skipped += 1
        except (RuntimeError, ValueError, KeyError) as e:
            print(f"[telegram_listener] update spawn failed for {name!r}: {e}")
            skipped += 1

    skipped_note = f" ({skipped} skipped)" if skipped else ""
    await _listener()._send_reply(
        client,
        f"✅ Spawned {spawned} update Operation(s){skipped_note}. Watch "
        f"the SPA's Live panel or History tab to follow progress."
    )


# noinspection PyUnusedLocal
# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
async def _cmd_link(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/link <code>`` — bind the sender's Telegram user_id to an
    OmniGrid user. Code is minted by the SPA's Profile section and
    valid for 15 minutes, single-use."""
    sender_id_int = await _resolve_telegram_sender_id_int(client, msg)
    if sender_id_int is None:
        return
    # If the sender is already linked, refuse and point them at
    # /unlink — re-linking without unlinking first would silently
    # overwrite the existing mapping, which is confusing if the
    # operator forgot they were already linked or if multiple users
    # share the same Telegram account (rare but observed). Same
    # short-circuit whether they typed `/link` bare OR `/link <code>`.
    existing_username = _listener()._lookup_omnigrid_user(sender_id_int)
    if existing_username:
        await _listener()._send_reply(
            client,
            f"ℹ️ You're already linked to OmniGrid user "
            f"<b>{_listener()._escape(existing_username)}</b>. Run "
            f"<code>/unlink</code> first if you want to re-link "
            f"with a fresh code."
        )
        return
    if not args:
        await _listener()._send_reply(client, "Usage: <code>/link &lt;code&gt;</code>")
        return
    code = args[0].strip()
    username = _listener()._consume_link_code(code)
    if not username:
        await _listener()._send_reply(
            client,
            "❌ Invalid or expired link code. Generate a fresh one in "
            "OmniGrid → Profile → Telegram and try again."
        )
        return
    import time as _time
    linked_at_ms = int(_time.time() * 1000)
    mappings = _listener()._load_mappings()
    mappings[str(sender_id_int)] = {
        "username": username,
        "linked_at_ms": linked_at_ms,
    }
    _listener()._save_mappings(mappings)
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
    await _listener()._send_reply(
        client,
        f"✅ Linked to OmniGrid user <b>{_listener()._escape(username)}</b>. "
        f"You can now run authenticated commands."
    )


# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
async def _cmd_unlink(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/unlink`` — drop the sender's Telegram → OmniGrid mapping."""
    sender_id_int = await _resolve_telegram_sender_id_int(client, msg)
    if sender_id_int is None:
        return
    mappings = _listener()._load_mappings()
    key = str(sender_id_int)
    if key not in mappings:
        await _listener()._send_reply(client, "You weren't linked. Nothing to unlink.")
        return
    removed = mappings.pop(key)
    _listener()._save_mappings(mappings)
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
    await _listener()._send_reply(
        client,
        f"✅ Unlinked from OmniGrid user <b>{_listener()._escape(removed_username)}</b>. "
        f"Re-link via Profile → Telegram in OmniGrid."
    )


# noinspection PyUnusedLocal
# noinspection PyUnusedLocal,PyProtectedMember,PyUnresolvedReferences
# Telegram handlers have a fixed (client, args, msg) signature
# set by the dispatcher; not every handler uses all three. Every
# `_listener()._X` access is the documented cross-module shim — see
# the `_listener()` docstring at the top of this file.
# noinspection PyProtectedMember
async def _cmd_weather(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/weather`` — fetch the linked OmniGrid user's saved weather
    location and return current conditions + a 3-day forecast snippet.
    """
    result = await _require_user_weather_pref(client, msg)
    if result is None:
        return
    username, loc = result
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
        await _listener()._send_reply(client, f"❌ Weather lookup failed: <code>{_listener()._escape(str(e))}</code>")
        return
    if not isinstance(data, dict):
        await _listener()._send_reply(
            client, "❌ Weather lookup failed: no response from upstream"
        )
        return
    if data.get("error"):
        err = data.get("error")
        await _listener()._send_reply(
            client,
            f"❌ Weather upstream error: <code>{_listener()._escape(str(err))}</code>"
        )
        return
    # Master toggle off / no API key / no URL configured — the
    # `/api/weather` route returns `{configured: False}` in those
    # cases. Surface a clear, actionable message pointing operators
    # at the right admin surface instead of letting downstream
    # rendering produce "(no current data)".
    if data.get("configured") is False:
        await _listener()._send_reply(
            client,
            "⚙️ Weather provider not configured or disabled.\n"
            "<i>An admin needs to enable the feature in <b>Admin → Weather</b>, "
            "pick a provider, and (for WeatherAPI.com) paste an API key.</i>"
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
    # `Any | bool | None`, which then breaks `_listener()._escape(cond)` /
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
        if temp_c is None:
            return ""
        if temp_c <= 0:
            return " — freezing, bundle up"
        if temp_c < 10:
            return " — cold, layer up"
        if temp_c < 18:
            return " — cool"
        if temp_c < 25:
            return " — mild and comfortable"
        if temp_c < 32:
            return " — warm"
        if temp_c < 38:
            return " — hot, hydrate often"
        return " — extreme heat, limit outdoor time"

    def _humid_feel(h: Optional[float]) -> str:
        if h is None:
            return ""
        if h < 25:
            return " — dry, watch for static"
        if h < 50:
            return " — feels balanced"
        if h < 70:
            return " — comfortable to slightly humid"
        if h < 85:
            return " — humid"
        return " — sticky and muggy"

    def _wind_strength(k: Optional[float]) -> str:
        if k is None:
            return ""
        if k < 5:
            return " — barely a breeze"
        if k < 12:
            return " — light breeze"
        if k < 20:
            return " — noticeable wind"
        if k < 30:
            return " — flags snapping"
        if k < 50:
            return " — gusty"
        return " — strong wind, secure loose objects"

    def _cond_emoji(c_str: str) -> str:
        lc = (c_str or "").lower()
        if "thunder" in lc:
            return "⛈️"
        if "snow" in lc:
            return "❄️"
        if "rain" in lc or "drizzle" in lc or "shower" in lc:
            return "🌧️"
        if "fog" in lc or "mist" in lc:
            return "🌫️"
        if "overcast" in lc:
            return "☁️"
        if "cloud" in lc:
            return "⛅"
        if "clear" in lc or "sunny" in lc:
            return "☀️"
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

    head = f"<b>{_listener()._escape(label)}</b>"
    body_lines: list[str] = []
    emoji = _cond_emoji(cond)
    if cond:
        prefix = f"{emoji} " if emoji else ""
        body_lines.append(f"{prefix}<b>{_listener()._escape(cond)}</b> overhead.")
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
            f"  • {_listener()._escape(date_str)}: {hi or '?'} / {lo or '?'}  {_listener()._escape(c)}"
        )
    # Sunrise / sunset — pull TODAY'S daylight window from the
    # forecast block so the operator gets a "is it light out / when
    # does it get dark" hint without a separate /time query.
    # Open-Meteo returns ISO timestamps in the resolved IANA
    # timezone; we surface just HH:MM since the date is implicit
    # ("today's" daylight window). First forecast entry is always
    # the current calendar day per `timezone=auto`.
    sunrise_str = ""
    sunset_str = ""
    if forecast and isinstance(forecast[0], dict):
        for src_key, target in (("sunrise", "sunrise_str"), ("sunset", "sunset_str")):
            raw = forecast[0].get(src_key)
            if not isinstance(raw, str) or "T" not in raw:
                continue
            # "2026-05-17T05:47" → "05:47"
            hhmm = raw.split("T", 1)[1][:5]
            if target == "sunrise_str":
                sunrise_str = hhmm
            else:
                sunset_str = hhmm
    daylight_line = ""
    if sunrise_str and sunset_str:
        daylight_line = f"\n☀️ Sunrise <b>{sunrise_str}</b> · 🌙 Sunset <b>{sunset_str}</b>"
    elif sunrise_str:
        daylight_line = f"\n☀️ Sunrise <b>{sunrise_str}</b>"
    elif sunset_str:
        daylight_line = f"\n🌙 Sunset <b>{sunset_str}</b>"

    text = head + "\n" + line1 + daylight_line
    if forecast_lines:
        text += "\n\n<b>Next 3 days:</b>\n" + "\n".join(forecast_lines)
    await _listener()._send_reply(client, text)


_PRAYER_EMOJI = {
    "fajr": "🌅",
    "sunrise": "☀️",
    "dhuhr": "🌞",
    "asr": "🌤️",
    "maghrib": "🌇",
    "isha": "🌙",
}


# noinspection PyProtectedMember
async def _fetch_user_prayer(client: httpx.AsyncClient, msg: dict):
    """Shared helper for /prayer + /hijri — resolves the linked user's
    saved weather location + fetches today's prayer times. Sends the
    operator-facing reply itself on every failure path (not linked, no
    location, feature disabled, upstream error) and returns None then;
    returns the prayer-data dict on success."""
    from logic import prayer_times as _pt
    if not _pt.is_enabled():
        await _listener()._send_reply(
            client,
            "⚙️ Prayer Times is disabled.\n"
            "<i>An admin can enable it in <b>Admin → Prayer Times</b>.</i>"
        )
        return None
    result = await _require_user_weather_pref(client, msg)
    if result is None:
        return None
    _username, loc = result
    label = (loc.get("label") or "").strip() or "your location"
    try:
        data = await _pt.fetch(
            float(loc["lat"]), float(loc["lon"]), label=label,
        )
    # noinspection PyBroadException
    except Exception as e:  # noqa: BLE001
        await _listener()._send_reply(
            client, f"❌ Prayer times lookup failed: <code>{_listener()._escape(str(e))}</code>"
        )
        return None
    if not isinstance(data, dict) or data.get("configured") is False:
        await _listener()._send_reply(
            client,
            "⚙️ Prayer Times is disabled.\n"
            "<i>An admin can enable it in <b>Admin → Prayer Times</b>.</i>"
        )
        return None
    if data.get("error") or not data.get("timings"):
        await _listener()._send_reply(
            client,
            f"❌ Prayer times upstream error: "
            f"<code>{_listener()._escape(str(data.get('error') or 'no data'))}</code>"
        )
        return None
    return _username, data


def _user_dt_fmt(username) -> str:
    """The linked user's ``datetime_format`` (Settings → Profile → Formats),
    falling back to the canonical default when unset / on any error. So a
    Telegram reply renders dates / clock times the way the user set them in
    the UI — same contract as the AI surfaces + notification ``{time}``."""
    # noinspection PyBroadException
    try:
        from logic.datetime_fmt import get_user_datetime_format
        return get_user_datetime_format(username or "")
    except Exception:  # noqa: BLE001
        from logic.datetime_fmt import DEFAULT_DATETIME_FORMAT
        return DEFAULT_DATETIME_FORMAT


def _fmt_clock_user(time_str, fmt) -> str:
    """Reformat a clock-time string into the user's TIME-ONLY format (12h vs
    24h per their datetime_format). Accepts 24h ``"04:10"`` / ``"04:10:00"``
    and 12h ``"12:05 AM"`` (an optional trailing `` (EET)`` timezone suffix is
    tolerated). Returns the input UNCHANGED when it can't be parsed, so an
    odd upstream value is never mangled."""
    import re as _re
    from datetime import datetime as _dt
    s = (time_str or "").strip()
    if not s:
        return s
    s = _re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()  # drop a trailing "(EET)"
    parsed = None
    for f in ("%I:%M %p", "%I:%M%p", "%H:%M:%S", "%H:%M"):
        try:
            parsed = _dt.strptime(s.upper(), f)
            break
        except ValueError:
            continue
    if parsed is None:
        return time_str
    # noinspection PyBroadException
    try:
        from logic.datetime_fmt import apply_datetime_format, strip_date_tokens
        tfmt = strip_date_tokens(fmt)
        # Clock sources (prayer / moon rise-set) have MINUTE granularity, so
        # drop any seconds token — a default 'HH:mm:ss' user format would
        # otherwise render a meaningless ':00' on every time.
        for tok in (":ss", ".ss", " ss", "ss"):
            tfmt = tfmt.replace(tok, "")
        tfmt = _re.sub(r"\bs\b", "", tfmt)
        tfmt = _re.sub(r"\s{2,}", " ", tfmt).strip() or "HH:mm"
        return apply_datetime_format(parsed, tfmt)
    except Exception:  # noqa: BLE001
        return time_str


def _fmt_date_user(date_str, fmt) -> str:
    """Reformat a GREGORIAN date string (ISO ``"YYYY-MM-DD"`` / ``"DD-MM-YYYY"``
    / ``"DD/MM/YYYY"``) into the user's DATE-ONLY format. Returns the input
    UNCHANGED when it can't be parsed — so a Hijri date or any non-Gregorian
    string passes through untouched (a Hijri month number must NEVER be
    rendered with a Gregorian month name)."""
    from datetime import datetime as _dt
    s = (date_str or "").strip()
    if not s:
        return s
    parsed = None
    for f in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            parsed = _dt.strptime(s, f)
            break
        except ValueError:
            continue
    if parsed is None:
        return date_str
    # noinspection PyBroadException
    try:
        from logic.datetime_fmt import apply_datetime_format, strip_time_tokens
        return apply_datetime_format(parsed, strip_time_tokens(fmt))
    except Exception:  # noqa: BLE001
        return date_str


def _fmt_prayer_countdown(secs) -> str:
    """'2h 14m' / '7m' from an in-seconds value; '' on bad input."""
    try:
        s = int(secs)
    except (TypeError, ValueError):
        return ""
    if s < 0:
        s = 0
    h = s // 3600
    m = (s % 3600) // 60
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{s % 60}s"


# noinspection PyUnusedLocal,PyProtectedMember,PyBroadException
async def _cmd_prayer(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/prayer`` — today's five prayer times + the next prayer +
    countdown for the linked user's saved location, plus the Hijri date.
    Location comes from the same saved Weather location /weather uses."""
    res = await _fetch_user_prayer(client, msg)
    if res is None:
        return
    username, data = res
    fmt = _user_dt_fmt(username)
    timings = data.get("timings") or {}
    loc = (data.get("location") or {})
    label = (loc.get("label") or "").strip() or "your location"
    nxt = data.get("next") or {}
    hijri = data.get("hijri") or {}

    head = f"🕌 <b>Prayer times — {_listener()._escape(label)}</b>"
    method = str(data.get("method_name") or "").strip()
    sub = []
    if hijri.get("text") or hijri.get("day"):
        hijri_txt = hijri.get("text") or (
            f"{hijri.get('day')} {hijri.get('month_en')} "
            f"{hijri.get('year')} {hijri.get('designation')}"
        )
        sub.append(f"📅 {_listener()._escape(str(hijri_txt).strip())}")
    if method:
        sub.append(f"<i>{_listener()._escape(method)}</i>")

    next_line = ""
    if nxt.get("key") and nxt.get("name"):
        cd = _fmt_prayer_countdown(nxt.get("in_seconds"))
        tom = " (tomorrow)" if nxt.get("tomorrow") else ""
        ntime = _fmt_clock_user((timings.get(nxt["key"]) or {}).get("time") or "", fmt)
        emoji = _PRAYER_EMOJI.get(nxt["key"], "🕋")
        next_line = (
            f"\n\n{emoji} <b>Next: {_listener()._escape(str(nxt['name']))}</b> "
            f"at <b>{_listener()._escape(ntime)}</b>{tom}"
            + (f" — in {cd}" if cd else "")
        )

    rows = []
    for key in ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"):
        r = timings.get(key) or {}
        if not r.get("time"):
            continue
        emoji = _PRAYER_EMOJI.get(key, "•")
        name = str(r.get("name") or key.title())
        is_next = nxt.get("key") == key and r.get("prayer")
        rtime = _fmt_clock_user(r["time"], fmt)
        line = f"{emoji} {_listener()._escape(name)}: <b>{_listener()._escape(rtime)}</b>"
        if is_next:
            line = "▶️ " + line
        rows.append(line)

    text = head
    if sub:
        text += "\n" + "  ".join(sub)
    text += next_line + "\n\n" + "\n".join(rows)
    await _listener()._send_reply(client, text)


# noinspection PyUnusedLocal,PyProtectedMember,PyBroadException
async def _cmd_hijri(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/hijri`` — today's Hijri (Islamic) calendar date for the
    linked user's saved location."""
    res = await _fetch_user_prayer(client, msg)
    if res is None:
        return
    username, data = res
    fmt = _user_dt_fmt(username)
    hijri = data.get("hijri") or {}
    greg = data.get("gregorian") or {}
    if not (hijri.get("text") or hijri.get("day")):
        await _listener()._send_reply(client, "❌ No Hijri date available right now.")
        return
    hijri_txt = hijri.get("text") or (
        f"{hijri.get('day')} {hijri.get('month_en')} "
        f"{hijri.get('year')} {hijri.get('designation')}"
    )
    lines = [f"📅 <b>Hijri date</b>", f"🌙 <b>{_listener()._escape(str(hijri_txt).strip())}</b>"]
    if hijri.get("month_ar"):
        ar = f"{hijri.get('day')} {hijri.get('month_ar')} {hijri.get('year')}"
        lines.append(_listener()._escape(ar.strip()))
    if hijri.get("weekday_en"):
        lines.append(f"<i>{_listener()._escape(str(hijri.get('weekday_en')))}</i>")
    if greg.get("date"):
        gline = _fmt_date_user(str(greg.get("date")), fmt)
        if greg.get("weekday"):
            gline += f" ({greg.get('weekday')})"
        lines.append(f"🗓️ {_listener()._escape(gline)}")
    await _listener()._send_reply(client, "\n".join(lines))


# noinspection PyUnusedLocal,PyProtectedMember,PyBroadException
async def _cmd_moon(client: httpx.AsyncClient, args: list[str], msg: dict) -> None:
    """``/moon`` — moon-phase summary for the linked user's saved
    location. Requires the WeatherAPI.com provider (Open-Meteo does
    not return moon data); declines with a clear "switch provider"
    message when Open-Meteo is active. Shows today's phase name +
    illumination % + moonrise / moonset, plus a 3-day outlook.
    """
    result = await _require_user_weather_pref(client, msg)
    if result is None:
        return
    username, loc = result
    fmt = _user_dt_fmt(username)
    label = (loc.get("label") or "").strip() or "your location"
    from main import api_weather as _api_weather
    try:
        data = await _api_weather(
            lat=loc["lat"], lon=loc["lon"], label=label,
        )
    except Exception as e:
        await _listener()._send_reply(
            client, f"❌ Moon lookup failed: <code>{_listener()._escape(str(e))}</code>"
        )
        return
    if not isinstance(data, dict):
        await _listener()._send_reply(
            client, "❌ Moon lookup failed: no response from upstream"
        )
        return
    if data.get("error"):
        err = str(data.get("error") or "")
        await _listener()._send_reply(
            client,
            f"❌ Moon upstream error: <code>{_listener()._escape(err)}</code>"
        )
        return
    if data.get("configured") is False:
        await _listener()._send_reply(
            client,
            "⚙️ Weather provider not configured or disabled.\n"
            "<i>An admin needs to enable the feature in <b>Admin → Weather</b>, "
            "pick a provider, and (for WeatherAPI.com) paste an API key.</i>"
        )
        return
    if not data.get("supports_moon"):
        await _listener()._send_reply(
            client,
            "🌙 Moon-phase data is not available with the active weather provider.\n"
            "<i>Switch to <b>WeatherAPI.com</b> in Admin → Weather (free key from "
            "weatherapi.com, 1M calls/month) to enable moon phases, illumination, "
            "moonrise, and moonset.</i>"
        )
        return
    forecast = data.get("forecast") or []
    if not isinstance(forecast, list) or not forecast:
        await _listener()._send_reply(
            client,
            "❌ Moon data missing from upstream response — try again in a few minutes."
        )
        return
    today = forecast[0] or {}
    phase = str(today.get("moon_phase") or "").strip()
    illum_raw = today.get("moon_illumination")
    moonrise = str(today.get("moonrise") or "").strip()
    moonset = str(today.get("moonset") or "").strip()
    try:
        # str() coerces `Any | None` from .get() to a definite str
        # so float() type-checks cleanly; non-numeric / empty falls
        # through to the except branch.
        illum_pct = int(round(float(str(illum_raw)))) if illum_raw is not None else None
    except (TypeError, ValueError):
        illum_pct = None
    # Phase-to-emoji map for visual polish at the head.
    phase_lower = phase.lower()
    if "new" in phase_lower:
        emoji = "🌑"
    elif "waxing crescent" in phase_lower:
        emoji = "🌒"
    elif "first quarter" in phase_lower:
        emoji = "🌓"
    elif "waxing gibbous" in phase_lower:
        emoji = "🌔"
    elif "full" in phase_lower:
        emoji = "🌕"
    elif "waning gibbous" in phase_lower:
        emoji = "🌖"
    elif "last quarter" in phase_lower or "third quarter" in phase_lower:
        emoji = "🌗"
    elif "waning crescent" in phase_lower:
        emoji = "🌘"
    else:
        emoji = "🌙"
    safe_phase = _listener()._escape(phase or "Unknown")
    head = f"{emoji} <b>{_listener()._escape(label)}</b>"
    illum_line = ""
    if illum_pct is not None:
        illum_line = f" — <b>{illum_pct}%</b> illuminated"
    body = f"{emoji} <b>{safe_phase}</b>{illum_line}"
    rise_set_parts = []
    if moonrise:
        rise_set_parts.append(f"🌅 Rise <b>{_listener()._escape(_fmt_clock_user(moonrise, fmt))}</b>")
    if moonset:
        rise_set_parts.append(f"🌌 Set <b>{_listener()._escape(_fmt_clock_user(moonset, fmt))}</b>")
    rise_set_line = "\n" + " · ".join(rise_set_parts) if rise_set_parts else ""
    # Next-2-days outlook (forecast[1] / forecast[2]).
    outlook_lines = []
    for fc in forecast[1:3]:
        if not isinstance(fc, dict):
            continue
        date_s = str(fc.get("date") or "").strip()
        ph = str(fc.get("moon_phase") or "").strip()
        il_raw = fc.get("moon_illumination")
        try:
            # `fc.get(...)` widens to `Any | None`. Coerce through
            # `str(...)` so float() gets a definite str input (Pyright
            # narrowing); empty string / non-numeric land in the
            # except branch.
            il_pct = int(round(float(str(il_raw)))) if il_raw is not None else None
        except (TypeError, ValueError):
            il_pct = None
        if not ph and il_pct is None:
            continue
        bits = [_listener()._escape(_fmt_date_user(date_s, fmt))] if date_s else []
        if ph:
            bits.append(_listener()._escape(ph))
        if il_pct is not None:
            bits.append(f"{il_pct}%")
        outlook_lines.append(" · ".join(bits))
    text = head + "\n" + body + rise_set_line
    if outlook_lines:
        text += "\n\n<b>Next 2 days:</b>\n" + "\n".join(outlook_lines)
    await _listener()._send_reply(client, text)
