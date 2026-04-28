"""User-triggered write operations and the in-memory op log.

Five ``_do_*`` handlers (update stack, update container, restart service,
restart container, remove container) wrap Portainer calls with:

  - structured event logging via :class:`Operation.log`
  - persistent history row on completion (``persist_history``)
  - Apprise notification on success/failure
  - gather-cache invalidation so the UI re-polls after the mutation

The ``ops`` dict + ``ops_order`` list hold the last 50 operations in
memory for the ``/api/ops`` live-status polling loop — they're NOT the
source of truth for history (the ``history`` SQLite table is). If ops
ever need to outlive a process restart, wire a persistence hook in
:func:`new_op`, but the single-replica invariant (CLAUDE.md) makes
in-memory fine for now.
"""
import json
import time
import uuid
from typing import Optional

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
    "prune_success",
    "prune_failure",
    "user_login",
    "host_paused",
)
NOTIFY_EVENT_DEFAULTS = {
    name: (False if name == "user_login" else True)
    for name in NOTIFY_EVENT_NAMES
}


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
# Apprise notifications — fired on success/failure of every _do_* op.
# Settings come from the DB (get_setting) so operators can change the
# Apprise URL/tag live without restart.
# ---------------------------------------------------------------------
async def notify(title: str, body: str, status: str = "info", *,
                 event: Optional[str] = None,
                 actor_username: Optional[str] = None) -> None:
    # Honour the per-service master switch (#204). When apprise is
    # disabled in Admin → Notifications, short-circuit BEFORE the
    # configured-url check so an operator with a stored URL but the
    # toggle off doesn't fire notifications. The URL stays in the
    # settings table — flipping the toggle back on resumes service
    # without requiring re-typing.
    if (get_setting("apprise_enabled", "true") or "true").lower() != "true":
        print("[notify] skipped — apprise disabled in Admin → Notifications")
        return
    url = get_setting("apprise_url", "")
    if not url:
        print("[notify] skipped — no apprise_url configured")
        return
    # Per-event opt-out. When event is provided AND the matching
    # setting is "false", short-circuit. None = always-send (legacy
    # callers + the test button).
    if event:
        default_on = NOTIFY_EVENT_DEFAULTS.get(event, True)
        if get_setting_bool(f"notify_event_{event}", default=default_on) is False:
            print(f"[notify] skipped — event '{event}' disabled by operator")
            return
    # Per-user opt-out (#357). Only consulted when an actor is supplied
    # AND the admin gate above passed. A user who hasn't touched their
    # prefs defaults to the admin state (i.e. send) — meaningful to
    # opt-out of an event the admin allows. Token "actors" (negative
    # ids in the User model — username "token:NAME") and unknown users
    # don't carry per-user prefs and fall through to the legacy path.
    # Per-user routing override (#356). When an actor is supplied AND the
    # user has an `email` set on their record, override the configured
    # Apprise URL's recipient via the POST body's `to=` field — Apprise's
    # mailto:// handler treats `to=` as a query-time recipient override
    # so a single configured `mailto://relay@host` URL can fan out to
    # different addresses per actor. For non-recipient-aware schemes
    # (Discord webhook, Slack incoming, Telegram bot) Apprise just
    # ignores `to=` so this is safe to always send.
    user_email: Optional[str] = None
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
                    user_email = (getattr(_u, "email", "") or "").strip() or None
        except Exception as _e:
            # Defensive: never let a pref lookup failure break the
            # admin-gate decision. Falls through to the legacy
            # admin-only path.
            print(f"[notify] user-pref lookup failed for '{actor_username}': {_e}")
    tag = get_setting("apprise_tag", "")
    # Apprise requires a non-empty body. If our ops didn't produce one, echo
    # the title so the notification isn't rejected as malformed.
    body = body or title
    try:
        async with httpx.AsyncClient(verify=portainer.VERIFY_TLS, timeout=15.0) as client:
            payload = {
                "title": title,
                "body": body,
                "type": "success" if status == "success" else "failure" if status == "error" else "info",
            }
            if tag:
                # Apprise-API accepts `tag` (splits on comma/space internally).
                payload["tag"] = tag
            if user_email:
                # Apprise mailto handler honours `to=` as a recipient
                # override; non-mailto schemes silently ignore it.
                payload["to"] = user_email
            r = await client.post(url, json=payload)
            if r.status_code >= 400:
                print(f"[notify] FAILED {r.status_code} → {url} body={r.text[:200]}")
            else:
                print(f"[notify] ok {r.status_code} → {url} tag={tag!r}")
    except Exception as e:
        print(f"[notify] ERROR → {url}: {e}")


async def notify_with_retry(
    title: str, body: str, status: str = "info", *,
    event: Optional[str] = None,
    actor_username: Optional[str] = None,
    retries: int = 1,
    retry_after: float = 60.0,
    label: str = "notify",
) -> None:
    """Fire-and-forget `notify` with bounded retry on dispatch failure.

    ENH-009 / #475 — extracted from `host_metrics_sampler._record_failure`'s
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
            )
            if attempt > 0:
                print(f"[{label}] retry succeeded on attempt {attempt + 1}")
            return
        except Exception as e:
            if attempt >= retries:
                print(f"[{label}] notify failed (giving up after "
                      f"{attempt + 1} attempts): {e}")
                return
            print(f"[{label}] notify primary failed: {e} — "
                  f"retrying in {retry_after:.0f}s")
            try:
                import asyncio as _asyncio
                await _asyncio.sleep(retry_after)
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
        )
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Stack update failed: {op.target_name}", str(e)[:500], "error",
                     event="stack_update_failure", actor_username=op.actor)
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
                     event="container_update_success", actor_username=op.actor)
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Container update failed: {op.target_name}", str(e)[:500], "error",
                     event="container_update_failure", actor_username=op.actor)
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
                     event="container_restart_success", actor_username=op.actor)
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Container restart failed: {op.target_name}", str(e)[:500], "error",
                     event="container_restart_failure", actor_username=op.actor)
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
                     event="container_remove_success", actor_username=op.actor)
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Container remove failed: {op.target_name}", str(e)[:500], "error",
                     event="container_remove_failure", actor_username=op.actor)
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
                     event="service_restart_success", actor_username=op.actor)
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Service restart failed: {op.target_name}", str(e)[:500], "error",
                     event="service_restart_failure", actor_username=op.actor)
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
        )
        return totals
    except Exception as e:
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Prune failed on {hostname}", str(e)[:500], "error",
                     event="prune_failure", actor_username=op.actor)
        return totals
    finally:
        persist_history(op)
        gather.invalidate_cache()
