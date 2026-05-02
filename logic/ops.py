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
"""
import asyncio
import json
import time
import uuid
from typing import Awaitable, Callable, Optional

import httpx

from logic import events, gather, metrics, portainer
from logic.db import db_conn, get_setting, get_setting_bool

MAX_OPS = 50


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
    "prune_success",
    "prune_failure",
    "user_login",
    "host_paused",
)
NOTIFY_EVENT_DEFAULTS = {
    name: (False if name == "user_login" else True)
    for name in NOTIFY_EVENT_NAMES
}


# Per-medium default state. Mirrors the per-event defaults map above so
# `api_get_settings` has a single source of truth + the dispatcher
# below can short-circuit a missing-row read to the same value the
# admin form would render. Both mediums default ON so existing deploys
# upgrade with both channels live; operators flip individually from
# Admin → Notifications.
NOTIFY_MEDIUM_NAMES = ("app", "apprise")
NOTIFY_MEDIUM_DEFAULTS = {name: True for name in NOTIFY_MEDIUM_NAMES}


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
        self.events.append({"ts": time.time(), "level": level, "msg": msg})
        print(f"[op {self.id}] {level}: {msg}")
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
            "(ts,op_type,target_name,target_id,target_stack,status,duration,events,error,actor) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (op.started, op.op_type, op.target_name, op.target_id, op.target_stack,
             op.status, duration,
             json.dumps(op.events), op.error, op.actor),
        )
        history_id = cur.lastrowid
    try:
        metrics.OPS_TOTAL.labels(op_type=op.op_type, status=op.status).inc()
    except Exception as e:
        print(f"[metrics] OPS_TOTAL inc failed: {e}")
    # SSE — fire AFTER the row commits so the SPA's prepend lands a
    # row that's already visible to /api/history.
    events.publish("history:appended", {
        "id": history_id, "ts": op.started, "op_type": op.op_type,
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


async def _notify_medium_apprise(
    *, title: str, body: str, severity: str,
    event: Optional[str], actor_username: Optional[str],
    target_kind: Optional[str], target_id: Optional[str],
    metadata: Optional[dict],
) -> dict:
    """Existing fire-and-forget Apprise dispatcher, lifted from the
    original :func:`notify`. Returns a structured ``{ok, skipped, ...}``
    dict so the caller can log per-medium outcomes.
    """
    if (get_setting("apprise_enabled", "true") or "true").lower() != "true":
        # Master toggle keeps the legacy short-circuit semantics; the
        # operator might have wanted to keep the app medium live while
        # silencing Apprise without flipping the per-medium switch.
        print("[notify] apprise skipped — apprise disabled in Admin → Notifications")
        return {"ok": False, "skipped": "apprise_disabled"}
    url = get_setting("apprise_url", "")
    if not url:
        print("[notify] apprise skipped — no apprise_url configured")
        return {"ok": False, "skipped": "no_url"}
    # Per-user routing override (#356) — mailto recipient lookup. The
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
        except Exception as _e:
            print(f"[notify] apprise user-email lookup failed for '{actor_username}': {_e}")
    tag = get_setting("apprise_tag", "")
    body = body or title  # Apprise rejects empty bodies.
    try:
        async with httpx.AsyncClient(verify=portainer.VERIFY_TLS, timeout=15.0) as client:
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
    except Exception as e:
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
    """
    ts = int(time.time())
    body = body or title
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
    except Exception as e:
        print(f"[notify] app INSERT failed: {e}")
        return {"ok": False, "error": str(e)}
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
    except Exception as e:
        # SSE publish failures must not break the dispatch — the DB row
        # is the source of truth, the SPA's polling fallback will pick
        # it up on the next /api/notifications round-trip. Verb stays
        # off the ERROR-severity regex per CLAUDE.md.
        print(f"[notify] app SSE publish dropped: {e}")
    print(f"[notify] app ok id={new_id} event={event!r} severity={severity}")
    return {"ok": True, "id": new_id, "unread_count": unread_count}


# Medium dispatcher map. Add a new medium by writing
# ``logic/notify_<medium>.py`` exposing an ``async def send(...)`` of
# the same shape and registering here. CLAUDE.md "Canonical extension
# pattern: add a notification medium" is the full contract.
MediumSender = Callable[..., Awaitable[dict]]
NOTIFY_MEDIUMS: dict[str, MediumSender] = {
    "app":     _notify_medium_app,
    "apprise": _notify_medium_apprise,
}


def _is_medium_enabled(medium: str) -> bool:
    """Per-medium master switch lookup. Defaults from
    :data:`NOTIFY_MEDIUM_DEFAULTS` so a fresh deploy fires every medium
    until the operator opts out from Admin → Notifications.
    """
    default_on = NOTIFY_MEDIUM_DEFAULTS.get(medium, True)
    return get_setting_bool(f"notify_medium_{medium}", default=default_on)


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

    Mediums fire via ``asyncio.gather(return_exceptions=True)`` so a
    failure in one (Apprise host down, DB write race) doesn't drop the
    delivery on the others.
    """
    severity = _coerce_severity(status)
    # Per-event admin gate.
    if event:
        default_on = NOTIFY_EVENT_DEFAULTS.get(event, True)
        if get_setting_bool(f"notify_event_{event}", default=default_on) is False:
            print(f"[notify] skipped — event '{event}' disabled by operator")
            return
    # Per-user opt-out (#357). Logged once, gates ALL mediums for THIS
    # actor — mirrors the previous behaviour. Token / system actors
    # (negative ids) skip the per-user lookup so scheduler-fired
    # notifications still land.
    if event and actor_username:
        try:
            from logic import auth as _auth
            with db_conn() as _c:
                _u = _auth.get_user_by_username(_c, actor_username)
                if _u and _u.id >= 0:
                    user_prefs = _auth.get_user_notify_prefs(_c, _u.id)
                    if user_prefs and user_prefs.get(event, True) is False:
                        print(
                            f"[notify] skipped — user '{actor_username}' "
                            f"opted out of '{event}'"
                        )
                        return
        except Exception as _e:
            # Defensive: never let a pref lookup failure break the
            # admin-gate decision.
            print(f"[notify] user-pref lookup failed for '{actor_username}': {_e}")
    # Build the per-medium dispatch list (skip disabled).
    senders: list[Awaitable[dict]] = []
    fired_mediums: list[str] = []
    for medium_name, sender in NOTIFY_MEDIUMS.items():
        if not _is_medium_enabled(medium_name):
            print(f"[notify] medium '{medium_name}' disabled — skipped")
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


async def notify_with_retry(
    title: str, body: str, status: str = "info", *,
    event: Optional[str] = None,
    actor_username: Optional[str] = None,
    target_kind: Optional[str] = None,
    target_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    retries: int = 1,
    retry_after: float = 60.0,
    label: str = "notify",
) -> None:
    """Fire-and-forget `notify` with bounded retry on dispatch failure.

    ENH-009 / extracted from `host_metrics_sampler._record_failure`'s
    inner closure so other callers (login event, future schedule kinds,
    anomaly watchers) get the same retry semantics without copy-pasting.
    `label` is a short tag prepended to error logs so the operator can
    tell two parallel notify chains apart in Admin → Logs.

    Retries on ANY exception from `notify()` after `retry_after` seconds;
    capped at `retries` extra attempts (default 1 = at most two total
    dispatches). Caller is expected to spawn this via
    `asyncio.create_task(...)` — running inline would block the
    triggering path on the retry sleep.
    """
    for attempt in range(retries + 1):
        try:
            await notify(
                title, body, status,
                event=event, actor_username=actor_username,
                target_kind=target_kind, target_id=target_id,
                metadata=metadata,
            )
            if attempt > 0:
                print(f"[{label}] retry succeeded on attempt {attempt + 1}")
            return
        except Exception as e:
            if attempt >= retries:
                # `dropped` keeps the persistent-log severity classifier
                # off the ERROR bucket — caller already sees a
                # per-medium ERROR line on the actual delivery failure.
                print(f"[{label}] notify dropped (giving up after "
                      f"{attempt + 1} attempts): {e}")
                return
            print(f"[{label}] notify primary deferred: {e} — "
                  f"retrying in {retry_after:.0f}s")
            try:
                await asyncio.sleep(retry_after)
            except Exception:
                return


# ---------------------------------------------------------------------
# Write ops. Each follows the same pattern: try/except/finally with
# persist_history + cache invalidation in finally.
# ---------------------------------------------------------------------
async def do_update_stack(op: Operation, stack_id: int) -> None:
    try:
        op.log(f"Starting stack update (id={stack_id})")
        async with httpx.AsyncClient(verify=portainer.VERIFY_TLS, timeout=600.0) as client:
            stack = await portainer.pg(client, f"/api/stacks/{stack_id}")
            op.log(f"Resolved stack: {stack['Name']}")
            try:
                file_data = await portainer.pg(client, f"/api/stacks/{stack_id}/file")
            except httpx.HTTPError as e:
                raise RuntimeError(f"Can't fetch compose file (external stack?): {e}")
            op.log("Fetched compose file from Portainer")
            body = {
                "StackFileContent": file_data["StackFileContent"],
                "Env": stack.get("Env") or [],
                "Prune": True,
                "PullImage": True,
            }
            op.log("Calling Portainer: Prune=true, PullImage=true")
            r = await client.put(
                f"{portainer.PORTAINER_URL}/api/stacks/{stack_id}"
                f"?endpointId={portainer.PORTAINER_ENDPOINT_ID}",
                json=body, headers=portainer.headers(),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log(f"Portainer accepted update (HTTP {r.status_code})", "success")
        op.done("success")
        await notify(
            f"✅ Stack updated: {op.target_name}",
            f"Duration: {op.to_dict()['duration']:.1f}s", "success",
            event="stack_update_success", actor_username=op.actor,
            target_kind="stack", target_id=str(op.target_id),
        )
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Stack update failed: {op.target_name}", str(e)[:500], "error",
                     event="stack_update_failure", actor_username=op.actor,
                     target_kind="stack", target_id=str(op.target_id))
    finally:
        persist_history(op)
        gather.invalidate_cache()


async def do_update_container(op: Operation, container_id: str) -> None:
    try:
        node = portainer.node_for_container(gather.get_cache(), container_id)
        op.log("Recreating container with PullImage=true"
               + (f" on node '{node}'" if node else ""))
        async with httpx.AsyncClient(verify=portainer.VERIFY_TLS, timeout=600.0) as client:
            r = await client.post(
                f"{portainer.PORTAINER_URL}/api/docker/{portainer.PORTAINER_ENDPOINT_ID}"
                f"/containers/{container_id}/recreate?PullImage=true",
                headers=portainer.headers(agent_target=node),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log("Container recreated", "success")
        op.done("success")
        await notify(f"✅ Container updated: {op.target_name}", "", "success",
                     event="container_update_success", actor_username=op.actor,
                     target_kind="container", target_id=str(op.target_id))
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Container update failed: {op.target_name}", str(e)[:500], "error",
                     event="container_update_failure", actor_username=op.actor,
                     target_kind="container", target_id=str(op.target_id))
    finally:
        persist_history(op)
        gather.invalidate_cache()


async def do_restart_container(op: Operation, container_id: str) -> None:
    try:
        node = portainer.node_for_container(gather.get_cache(), container_id)
        op.log("Restarting container" + (f" on node '{node}'" if node else ""))
        async with httpx.AsyncClient(verify=portainer.VERIFY_TLS, timeout=120.0) as client:
            r = await client.post(
                f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                f"/docker/containers/{container_id}/restart",
                headers=portainer.headers(agent_target=node),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log("Container restarted", "success")
        op.done("success")
        await notify(f"🔄 Container restarted: {op.target_name}", "", "success",
                     event="container_restart_success", actor_username=op.actor,
                     target_kind="container", target_id=str(op.target_id))
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Container restart failed: {op.target_name}", str(e)[:500], "error",
                     event="container_restart_failure", actor_username=op.actor,
                     target_kind="container", target_id=str(op.target_id))
    finally:
        persist_history(op)
        gather.invalidate_cache()


async def do_remove_container(op: Operation, container_id: str) -> None:
    try:
        node = portainer.node_for_container(gather.get_cache(), container_id)
        if node:
            op.log(f"Removing container on node '{node}' (force=true, v=true)")
        else:
            op.log("Removing container (force=true, v=true)")
        async with httpx.AsyncClient(verify=portainer.VERIFY_TLS, timeout=120.0) as client:
            r = await client.delete(
                f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                f"/docker/containers/{container_id}?force=true&v=true",
                headers=portainer.headers(agent_target=node),
            )
            # Idempotent removal: if the container is already gone (Swarm
            # cleanup, another operator, a previous click that succeeded
            # after a cache snapshot), 404 is the SAME end-state as a fresh
            # delete. Treat it as success so the operator doesn't see a
            # scary red toast for a no-op. The cache is invalidated in the
            # finally-block regardless, so the row will disappear on the
            # next refresh.
            if r.status_code == 404:
                op.log("Container already gone — treating as success", "success")
            elif r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            else:
                op.log("Container removed", "success")
        op.done("success")
        await notify(f"🗑 Container removed: {op.target_name}", "", "success",
                     event="container_remove_success", actor_username=op.actor,
                     target_kind="container", target_id=str(op.target_id))
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Container remove failed: {op.target_name}", str(e)[:500], "error",
                     event="container_remove_failure", actor_username=op.actor,
                     target_kind="container", target_id=str(op.target_id))
    finally:
        persist_history(op)
        gather.invalidate_cache()


async def do_restart_service(op: Operation, service_id: str) -> None:
    try:
        op.log("Fetching current service spec")
        async with httpx.AsyncClient(verify=portainer.VERIFY_TLS, timeout=300.0) as client:
            svc = await portainer.pg(
                client,
                f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker/services/{service_id}",
            )
            version = svc["Version"]["Index"]
            spec = svc["Spec"]
            tt = spec.setdefault("TaskTemplate", {})
            tt["ForceUpdate"] = int(tt.get("ForceUpdate", 0)) + 1
            op.log(f"Bumping ForceUpdate to {tt['ForceUpdate']}")
            r = await client.post(
                f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                f"/docker/services/{service_id}/update?version={version}",
                json=spec, headers=portainer.headers(),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log("Service restart triggered", "success")
        op.done("success")
        await notify(f"🔄 Service restarted: {op.target_name}", "", "success",
                     event="service_restart_success", actor_username=op.actor,
                     target_kind="service", target_id=str(op.target_id))
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Service restart failed: {op.target_name}", str(e)[:500], "error",
                     event="service_restart_failure", actor_username=op.actor,
                     target_kind="service", target_id=str(op.target_id))
    finally:
        persist_history(op)
        gather.invalidate_cache()


async def discover_swarm_agent_service(client: httpx.AsyncClient) -> tuple[Optional[str], Optional[str], list[dict]]:
    """Walk every Swarm service, identify the Portainer agent service.

    Returns ``(service_id, service_name, matches)`` —
      - On exactly one match: ``(id, name, [match_summary])``.
      - On zero matches: ``(None, None, [])``.
      - On multiple matches: ``(None, None, [{id, name, image}, ...])``
        so the caller can render a clear error listing every candidate
        and let the operator pick — auto-restarting the wrong service
        is not safe.

    Match heuristic:
      1. Image starts with one of the canonical Portainer agent
         repositories (``portainer/agent``, ``portainer/agent-ce``,
         ``portainer-ee/agent``). The image is the strongest signal —
         operator-renamed services keep their image label.
      2. Fallback: service name CONTAINS ``portainer`` AND ``agent``
         (case-insensitive). Catches operator-renamed services that
         use a non-canonical image (e.g. a pinned digest with no tag).
    """
    ep = f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"
    services = await portainer.pg(client, f"{ep}/services")
    canonical_image_prefixes = (
        "portainer/agent", "portainer/agent-ce",
        "portainer-ee/agent", "portainer-ce/agent",
    )
    matches: list[dict] = []
    for svc in services or []:
        spec = svc.get("Spec") or {}
        name = spec.get("Name") or ""
        cs = ((spec.get("TaskTemplate") or {}).get("ContainerSpec") or {})
        image = cs.get("Image") or ""
        # Image-prefix match — strip any tag / digest suffix first.
        image_repo = image.split("@", 1)[0].split(":", 1)[0].lower()
        is_canonical = any(image_repo.startswith(p) for p in canonical_image_prefixes)
        # Name fallback — case-insensitive substring match on both
        # `portainer` and `agent`. Avoids false-positives on services
        # named just `agent` or just `portainer` (the latter is
        # typically Portainer SERVER, not the per-node agent).
        nm = name.lower()
        is_name_match = ("portainer" in nm) and ("agent" in nm)
        if is_canonical or is_name_match:
            matches.append({"id": svc.get("ID"), "name": name, "image": image})
    if not matches:
        return None, None, []
    if len(matches) > 1:
        return None, None, matches
    return matches[0]["id"], matches[0]["name"], matches


async def do_restart_swarm_agent(op: Operation) -> None:
    """Force-update the Portainer agent global service so every node
    restart-spawns its agent task and re-registers with the manager.

    Wraps the same `service update` mechanic as `do_restart_service`
    but discovers the target service automatically. On ambiguous
    discovery (multiple Portainer-agent services), records the
    candidates in the op log + errors out so the operator can pick
    rather than risk restarting the wrong service.
    """
    try:
        async with httpx.AsyncClient(verify=portainer.VERIFY_TLS, timeout=300.0) as client:
            op.log("Discovering Portainer agent service")
            sid, sname, matches = await discover_swarm_agent_service(client)
            if not matches:
                raise RuntimeError(
                    "No Portainer agent service found — looked for image "
                    "prefix portainer/agent OR service name containing both "
                    "'portainer' and 'agent'. If you renamed the service or "
                    "use a non-canonical image, restart it manually via "
                    "`docker service update --force <service-name>` on the manager.")
            if len(matches) > 1:
                listing = "; ".join(f"{m['name']} ({m['image']})" for m in matches)
                raise RuntimeError(
                    f"Multiple Portainer agent candidates found — refusing "
                    f"to auto-pick. Candidates: {listing}. Restart manually "
                    f"via `docker service update --force <name>`.")
            # Single match — proceed.
            op.target_id = str(sid)
            op.target_name = sname or "<portainer-agent>"
            op.log(f"Match: {sname} (id {sid})")
            ep = f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"
            svc = await portainer.pg(client, f"{ep}/services/{sid}")
            version = svc["Version"]["Index"]
            spec = svc["Spec"]
            tt = spec.setdefault("TaskTemplate", {})
            tt["ForceUpdate"] = int(tt.get("ForceUpdate", 0)) + 1
            op.log(f"Bumping ForceUpdate to {tt['ForceUpdate']}")
            r = await client.post(
                f"{portainer.PORTAINER_URL}{ep}/services/{sid}/update?version={version}",
                json=spec, headers=portainer.headers(),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log("Agent service restart triggered — re-registration "
                   "happens as each node's task respawns", "success")
        op.done("success")
        await notify(
            f"🔄 Portainer agent restarted: {op.target_name}",
            "Force-update applied; agents on every node will respawn "
            "and re-register with the manager.",
            "success",
            event="swarm_agent_restart_success", actor_username=op.actor,
            target_kind="service", target_id=str(op.target_id),
        )
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(
            f"❌ Portainer agent restart failed: {op.target_name or '<discovery>'}",
            str(e)[:500], "error",
            event="swarm_agent_restart_failure", actor_username=op.actor,
            target_kind="service", target_id=str(op.target_id or ""),
        )
    finally:
        persist_history(op)
        gather.invalidate_cache()


async def do_prune_node(op: Operation, hostname: str) -> dict:
    """Run a ``docker system prune``-equivalent on a single Swarm node.

    Matches ``docker system prune -f --volumes``: stopped containers,
    dangling images (not ``-a``), unused networks, unused local volumes,
    build cache. Targeted via ``X-PortainerAgent-Target`` so calls land
    on the right worker's daemon.

    Returns the aggregated totals dict so the caller can surface it
    (response payload, toast, Apprise message).
    """
    totals = {
        "containers": 0, "images": 0, "networks": 0, "volumes": 0,
        "space_reclaimed": 0,  # bytes
    }
    try:
        op.log(f"Starting docker prune on node '{hostname}' "
               "(stopped containers, dangling images, unused networks + volumes, build cache)")
        ep = f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"
        h = portainer.headers(agent_target=hostname)

        async with httpx.AsyncClient(verify=portainer.VERIFY_TLS, timeout=300.0) as client:
            async def _prune(path: str, label: str, counter_key):
                """POST one of Docker's /prune endpoints. Log per step;
                one failing sub-call (e.g. volumes/prune with nothing
                eligible) shouldn't abort the rest of the pass.
                """
                try:
                    r = await client.post(f"{portainer.PORTAINER_URL}{path}", headers=h)
                    if r.status_code >= 400:
                        op.log(f"{label}: HTTP {r.status_code} — {r.text[:200]}", "error")
                        return
                    j = r.json() if r.content else {}
                    deleted_list = (
                        j.get("ContainersDeleted")
                        or j.get("ImagesDeleted")
                        or j.get("NetworksDeleted")
                        or j.get("VolumesDeleted")
                        or []
                    )
                    deleted = len(deleted_list) if isinstance(deleted_list, list) else 0
                    reclaimed = int(j.get("SpaceReclaimed") or 0)
                    if counter_key:
                        totals[counter_key] += deleted
                    totals["space_reclaimed"] += reclaimed
                    op.log(f"{label}: removed {deleted}, reclaimed {reclaimed:,} B")
                except Exception as e:
                    op.log(f"{label}: {e}", "error")

            # Order matches `docker system prune`: containers first (frees
            # their images), then images, networks, volumes, build cache.
            await _prune(f"{ep}/containers/prune", "containers/prune", "containers")
            # Dangling-only mirrors `docker system prune` (no `-a`). Filter
            # expressed in Portainer's accepted form (same as Docker CLI).
            await _prune(
                f'{ep}/images/prune?filters={{"dangling":["true"]}}',
                "images/prune (dangling)", "images",
            )
            await _prune(f"{ep}/networks/prune", "networks/prune", "networks")
            await _prune(f"{ep}/volumes/prune", "volumes/prune (unused)", "volumes")
            await _prune(f"{ep}/build/prune", "builder/prune", None)

        op.done("success")
        await notify(
            f"🧹 Prune complete on {hostname}",
            f"Reclaimed {_human_bytes(totals['space_reclaimed'])} across "
            f"{totals['containers']} containers / "
            f"{totals['images']} images / "
            f"{totals['networks']} networks / "
            f"{totals['volumes']} volumes",
            "success",
            event="prune_success", actor_username=op.actor,
            target_kind="host", target_id=hostname,
            metadata={"reclaimed_bytes": totals["space_reclaimed"], **totals},
        )
        return totals
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Prune failed on {hostname}", str(e)[:500], "error",
                     event="prune_failure", actor_username=op.actor,
                     target_kind="host", target_id=hostname)
        return totals
    finally:
        persist_history(op)
        gather.invalidate_cache()
