"""Make sure an image is on its node BEFORE anything running is stopped.

Every operation that replaces what a service or container is running —
stack update, container update / recreate, retag, rollback — goes through
here first, and only stops the old thing once the new image is local.

Why this exists: Swarm's default update order is stop-first. Portainer's
stack redeploy (`PullImage=true`) hands the pull to each node's Docker, and
Swarm stops the running task BEFORE that pull starts. On a slow link the whole
download is an outage: a Tracearr update stopped a working service and then
spent more than 25 minutes in `preparing` fetching a ~400 MB image from
ghcr.io, while OmniGrid reported the update as a success. Fetching first turns
that into an ordinary restart of a few seconds, whatever the link speed.

Two properties are load-bearing:

* **A failed pull is detected from the stream, not the status.** Docker
  answers `POST /images/create` with HTTP 200 and streams progress; a failure
  arrives as an `{"error": ...}` line at the END of that stream. Checking the
  status alone — which every call site did before this module — treats a
  failed pull as success and goes on to stop and remove the container.
* **Nothing is stopped when the fetch fails or runs out of time.** Callers
  raise before their stop / redeploy step, so the running service is left
  exactly as it was. The one softening is `strict=False` for an AUTH failure
  on the Swarm paths: Portainer's own redeploy may hold registry credentials
  this pre-fetch was not given, so refusing would make updates worse than
  before. Those paths log a warning and fall back to the old behaviour.
  Everywhere OmniGrid does the pull itself (recreate, retag) there is nothing
  to fall back to, and any failure aborts.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import quote

import httpx

from logic import portainer

if TYPE_CHECKING:  # pragma: no cover
    from logic.ops import Operation

# Substrings of a registry's refusal. Kept to phrases registries actually
# send; "denied" covers `requested access to the resource is denied`. No bare
# "401": it matches inside digests and byte counts, and an HTTP 401 status is
# classified separately where the status is visible.
_AUTH_MARKERS = (
    "unauthorized", "authentication required", "access to the resource is denied",
    "no basic auth credentials", "denied: ", "pull access denied",
)


class PullFailure(RuntimeError):
    """An image could not be made local. ``kind`` is one of ``auth`` (the
    registry refused), ``timeout`` (the budget ran out) or ``failed``."""

    def __init__(self, kind: str, image: str, node: str, detail: str):
        self.kind = kind
        self.image = image
        self.node = node
        self.detail = detail
        where = f" on {node}" if node else ""
        super().__init__(f"{image}{where}: {detail}")


def split_ref(ref: str, *, keep_digest: bool = False) -> tuple[str, str]:
    """``(repository, tag)`` for ``images/create``.

    The tag is looked for in the LAST path segment only, so a registry port
    (``registry:5000/team/app``) is never mistaken for one.

    By default the digest is dropped and the pull goes by tag — an update
    wants whatever the tag names NOW, not the digest the old task was pinned
    to. ``keep_digest=True`` is the opposite need: a ROLLBACK must fetch the
    exact bytes it ran before, and pulling its tag would fetch the new image
    it is rolling away from. Docker accepts a digest in the ``tag`` field.
    """
    ref = (ref or "").strip()
    if "@" in ref:
        name_part, digest = ref.split("@", 1)
        if keep_digest and digest:
            repo, _tag = split_ref(name_part)
            return repo, digest
        ref = name_part
    head, _, last = ref.rpartition("/")
    if ":" in last:
        name, tag = last.rsplit(":", 1)
        return (f"{head}/{name}" if head else name), (tag or "latest")
    return ref, "latest"


def stream_error(text: str) -> Optional[str]:
    """The error a Docker pull stream ended with, or None if it succeeded."""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        detail = obj.get("errorDetail")
        if isinstance(detail, dict) and detail.get("message"):
            return str(detail["message"])
        if obj.get("error"):
            return str(obj["error"])
    return None


def is_auth_error(message: str) -> bool:
    """True when a pull failure reads as the registry refusing credentials."""
    m = (message or "").lower()
    return any(marker in m for marker in _AUTH_MARKERS)


def _registry_auth_header(repository: str) -> dict[str, str]:
    """``X-Registry-Auth`` for a registry OmniGrid holds credentials for.

    Reuses Admin → Registries, so a private registry the update check can
    already read is also one the pre-fetch can pull from. Empty when none are
    stored — Docker then pulls anonymously, which is what it did before.
    """
    first = repository.split("/", 1)[0]
    # A first segment is a registry only if it looks like a host; otherwise
    # the image is on Docker Hub (`library/nginx`, `team/app`).
    if "." not in first and ":" not in first and first != "localhost":
        return {}
    from logic import registry  # noqa: PLC0415 — leaf import, avoids a cycle
    creds = registry.credentials_for(first)
    if not creds:
        return {}
    payload = json.dumps({"username": creds[0], "password": creds[1],
                          "serveraddress": first}).encode()
    return {"X-Registry-Auth": base64.urlsafe_b64encode(payload).decode("ascii")}


async def _pull_via_portainer(client: httpx.AsyncClient, op: Operation,
                              node: str, image_ref: str, budget_s: float, *,
                              keep_digest: bool = False) -> None:
    """Stream one pull through Portainer's Docker proxy onto ``node``."""
    repo, tag = split_ref(image_ref, keep_digest=keep_digest)
    url = (f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
           f"/docker/images/create?fromImage={quote(repo, safe='/:.-_')}"
           f"&tag={quote(tag, safe='.-_:')}")
    headers = {**portainer.headers(agent_target=node or None), **_registry_auth_header(repo)}
    label = f"{repo}@{tag[:19]}…" if tag.startswith("sha256:") else f"{repo}:{tag}"
    started = time.monotonic()

    op.set_phase("fetching", label)

    async def _run() -> None:
        seen: set[str] = set()
        done: set[str] = set()
        reported = 0  # last quarter logged, so progress lands 4 times at most
        shown = (0, 0)  # last (done, total) published to the row
        tail: list[str] = []
        async with client.stream("POST", url, headers=headers,
                                 timeout=httpx.Timeout(budget_s)) as r:
            if r.status_code >= 400:
                body = (await r.aread()).decode(errors="replace")
                if r.status_code in (502, 503, 504) and "<html" in body.lower():
                    # A reverse proxy in front of Portainer answered, not
                    # Portainer: it gave up waiting because a pull sends
                    # nothing back until it finishes. Say that instead of
                    # pasting the proxy's error page into the op log.
                    raise PullFailure(
                        "failed", label, node,
                        f"the proxy in front of Portainer ended the download with "
                        f"HTTP {r.status_code} after "
                        f"{time.monotonic() - started:,.0f}s — it gives up on a request "
                        f"that stays silent that long. Raise its read timeout, or point "
                        f"OmniGrid at Portainer directly (Admin → Portainer)")
                raise PullFailure(
                    "auth" if r.status_code == 401 or is_auth_error(body) else "failed",
                    label, node, f"HTTP {r.status_code}: {body[:300]}")
            async for line in r.aiter_lines():
                if not line:
                    continue
                tail = (tail + [line])[-5:]
                try:
                    evt: Any = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(evt, dict):
                    continue
                err = stream_error(line)
                if err:
                    raise PullFailure("auth" if is_auth_error(err) else "failed",
                                      label, node, err)
                lid = evt.get("id")
                status = str(evt.get("status") or "")
                if lid and status and lid not in (tag, repo):
                    seen.add(lid)
                    if status in ("Pull complete", "Already exists"):
                        done.add(lid)
                if seen and (len(done), len(seen)) != shown:
                    # Only when a count moves: Docker streams many progress
                    # lines per layer, and each publish makes every open tab
                    # re-read the op list.
                    shown = (len(done), len(seen))
                    op.set_phase("fetching", label, done=len(done), total=len(seen))
                if seen:
                    quarter = (4 * len(done)) // len(seen)
                    if 0 < quarter < 4 and quarter > reported:
                        reported = quarter
                        op.log(f"  … {label}{' on ' + node if node else ''}: "
                               f"{len(done)} of {len(seen)} layers ready")
        # A stream that ends without an error line is a completed pull. Check
        # the tail once more in case the error was the very last, partial line.
        err = stream_error("\n".join(tail))
        if err:
            raise PullFailure("auth" if is_auth_error(err) else "failed", label, node, err)

    try:
        await asyncio.wait_for(_run(), timeout=budget_s)
    except asyncio.TimeoutError:
        raise PullFailure("timeout", label, node,
                          f"still downloading after {int(budget_s)}s")
    except httpx.TimeoutException:
        raise PullFailure("timeout", label, node,
                          f"no progress from the registry within {int(budget_s)}s")
    except httpx.HTTPError as e:
        raise PullFailure("failed", label, node, f"{type(e).__name__}: {e}")
    op.log(f"Fetched {label}{' on ' + node if node else ''} "
           f"in {time.monotonic() - started:,.1f}s")


# ---- Fetching through a Swarm job ------------------------------------------
#
# Why not just POST /images/create: through Portainer that is ONE long HTTP
# request, and Portainer's agent proxy sends nothing back until the pull
# completes. Any reverse proxy in front of Portainer (Nginx Proxy Manager's
# default read timeout is 90 s) then cuts it with a 504 — on a slow link
# every fetch failed at exactly 90 s, so the pre-fetch refused every update
# of anything big. Swarm's own pull runs inside the node's daemon with no
# request held open, which is how Portainer's stack redeploy never hit this.
#
# So the image is fetched by a one-shot job pinned to the node. Its command
# is a path that cannot exist, so the image's own code NEVER RUNS: the task
# can only get past `preparing` once the image is local (the pull), and then
# fails to start — which is the signal that the download is done. The job is
# always removed afterwards.

_JOB_LABEL = "omnigrid.prefetch"
# Deliberately nonexistent: guarantees nothing from the image is executed.
_JOB_COMMAND = ["/omnigrid-prefetch-never-runs"]
# Task states that mean "still getting ready to run" — the download happens
# in `preparing`.
_JOB_WAITING = {"new", "allocated", "pending", "assigned", "accepted", "preparing"}
# Any state past preparing means the image made it onto the node. `failed` is
# the expected one: the nonexistent command can't start.
_JOB_PULLED = {"ready", "starting", "running", "complete", "failed", "shutdown"}


async def _is_swarm_manager(client: httpx.AsyncClient) -> bool:
    """True when the Portainer endpoint is a Swarm manager — the only place a
    job can be created. Anything else falls back to the streamed pull."""
    ep = f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"
    try:
        info = await portainer.pg(client, f"{ep}/info") or {}
    except (httpx.HTTPError, ValueError):
        return False
    swarm = info.get("Swarm") or {}
    return swarm.get("LocalNodeState") == "active" and bool(swarm.get("ControlAvailable"))


def _poll_seconds() -> float:
    """Reuses the stack-update poll cadence: same Portainer, same kind of
    "is it done yet" question, so one knob governs both."""
    from logic.tuning import Tunable, tuning_int  # noqa: PLC0415
    try:
        return float(tuning_int(Tunable.STACK_UPDATE_OBSERVE_POLL_SECONDS))
    except (KeyError, ValueError, TypeError):
        return 15.0


async def _sweep_stale_jobs(client: httpx.AsyncClient, budget_s: float) -> None:
    """Remove prefetch jobs left behind by a crash.

    A live job is never older than the pre-pull limit — its own op gives up
    and removes it by then — so anything older is an orphan. Best-effort: a
    failure here must never block the fetch.
    """
    ep = f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"
    try:
        filters = quote(json.dumps({"label": [f"{_JOB_LABEL}=1"]}))
        r = await client.get(f"{ep}/services?filters={filters}", headers=portainer.headers())
        if r.status_code >= 400:
            return
        cutoff = time.time() - budget_s
        from datetime import datetime  # noqa: PLC0415
        for svc in r.json() or []:
            created = str((svc or {}).get("CreatedAt") or "")
            try:
                ts = datetime.fromisoformat(created[:26].rstrip("Z") + "+00:00").timestamp()
            except ValueError:
                continue
            if ts < cutoff and svc.get("ID"):
                await client.delete(f"{ep}/services/{svc['ID']}", headers=portainer.headers())
    except (httpx.HTTPError, ValueError, TypeError):
        return


async def _pull_via_swarm_job(client: httpx.AsyncClient, op: Operation, node: str,
                              image_ref: str, budget_s: float, *,
                              keep_digest: bool = False) -> None:
    """Fetch ``image_ref`` onto ``node`` through a one-shot Swarm job."""
    repo, tag = split_ref(image_ref, keep_digest=keep_digest)
    image = f"{repo}@{tag}" if tag.startswith("sha256:") else f"{repo}:{tag}"
    label = f"{repo}@{tag[:19]}…" if tag.startswith("sha256:") else f"{repo}:{tag}"
    ep = f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"
    headers = {**portainer.headers(), **_registry_auth_header(repo)}
    spec: dict[str, Any] = {
        "Name": f"omnigrid-prefetch-{op.id}-{abs(hash((node, image))) % 10**6:06d}",
        "Labels": {_JOB_LABEL: "1", f"{_JOB_LABEL}.image": image, f"{_JOB_LABEL}.op": op.id},
        "TaskTemplate": {
            "ContainerSpec": {"Image": image, "Command": _JOB_COMMAND},
            "Placement": {"Constraints": [f"node.hostname=={node}"]},
            "RestartPolicy": {"Condition": "none"},
        },
        "Mode": {"ReplicatedJob": {"MaxConcurrent": 1, "TotalCompletions": 1}},
    }
    op.set_phase("fetching", label)
    started = time.monotonic()
    deadline = started + budget_s
    r = await client.post(f"{ep}/services/create", json=spec, headers=headers)
    if r.status_code == 400 and "mode" in r.text.lower():
        # Docker older than 20.10 has no job mode; one replica that never
        # restarts behaves the same for this purpose.
        spec["Mode"] = {"Replicated": {"Replicas": 1}}
        r = await client.post(f"{ep}/services/create", json=spec, headers=headers)
    if r.status_code >= 400:
        body = r.text[:300]
        raise PullFailure("auth" if r.status_code == 401 or is_auth_error(body) else "failed",
                          label, node, f"couldn't create the fetch job: HTTP {r.status_code}: {body}")
    service_id = str((r.json() or {}).get("ID") or "")
    try:
        filters = quote(json.dumps({"service": [service_id]}))
        poll = _poll_seconds()
        while True:
            tasks = []
            tr = await client.get(f"{ep}/tasks?filters={filters}", headers=portainer.headers())
            if tr.status_code < 400:
                tasks = [t for t in (tr.json() or []) if isinstance(t, dict)]
            if tasks:
                newest = max(tasks, key=lambda t: str(t.get("CreatedAt") or ""))
                status = newest.get("Status") or {}
                state = str(status.get("State") or "").lower()
                err = str(status.get("Err") or "")
                if state in _JOB_PULLED:
                    op.log(f"Fetched {label} on {node} in {time.monotonic() - started:,.1f}s")
                    return
                if state == "rejected":
                    # The pull (or the image reference) failed on the node.
                    raise PullFailure("auth" if is_auth_error(err) else "failed", label, node,
                                      err or "the node rejected the fetch")
                if state == "pending" and "no suitable node" in err.lower():
                    raise PullFailure("failed", label, node,
                                      f"no Swarm node named {node!r} is available: {err}")
                if state not in _JOB_WAITING:
                    raise PullFailure("failed", label, node, f"fetch job ended as {state!r}: {err}")
            if time.monotonic() + poll > deadline:
                raise PullFailure("timeout", label, node,
                                  f"still downloading after {int(budget_s)}s")
            await asyncio.sleep(poll)
    finally:
        # Always — success, failure, timeout or cancellation. Removing the job
        # while it's still preparing also stops its download.
        try:
            await client.delete(f"{ep}/services/{service_id}", headers=portainer.headers())
        except (httpx.HTTPError, OSError) as e:
            op.log(f"Couldn't remove fetch job {spec['Name']} ({type(e).__name__}); it will be "
                   f"swept up by the next fetch", "warning")


def _budget_seconds() -> float:
    from logic.tuning import Tunable, tuning_int  # noqa: PLC0415
    try:
        return float(tuning_int(Tunable.IMAGE_PREPULL_TIMEOUT_SECONDS))
    except (KeyError, ValueError, TypeError):
        return 1800.0


async def ensure_images_on_nodes(client: httpx.AsyncClient, op: Operation,
                                 targets: list[tuple[str, str]], *,
                                 strict: bool, keep_digest: bool = False) -> None:
    """Pull every ``(node, image_ref)`` before the caller stops anything.

    Raises ``RuntimeError`` — whose message says the running service was left
    alone — on a timeout or a failed pull. With ``strict=False`` an AUTH
    refusal logs a warning and continues instead (see the module docstring).
    One budget covers every target, so a stack of five slow images can't take
    five times the limit.
    """
    unique = list(dict.fromkeys((n or "", r) for n, r in targets if r))
    if not unique:
        return
    budget = _budget_seconds()
    deadline = time.monotonic() + budget
    # A job needs a Swarm manager to create it and a node to pin it to. Where
    # either is missing (a standalone endpoint, a node that couldn't be
    # resolved) the streamed pull is the only option — and the one a reverse
    # proxy can cut, which its error message then says.
    use_jobs = await _is_swarm_manager(client)
    if use_jobs:
        await _sweep_stale_jobs(client, budget)
    op.log(f"Fetching {len(unique)} image(s) onto their node(s) before anything is "
           f"stopped (limit {int(budget)}s"
           f"{', through a Swarm job' if use_jobs else ''})…")
    for node, ref in unique:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"Image fetch ran past the {int(budget)}s limit before {ref} — "
                f"nothing was stopped; the running service is unchanged")
        try:
            if use_jobs and node:
                await _pull_via_swarm_job(client, op, node, ref, remaining,
                                          keep_digest=keep_digest)
            else:
                await _pull_via_portainer(client, op, node, ref, remaining,
                                          keep_digest=keep_digest)
        except PullFailure as e:
            if e.kind == "auth" and not strict:
                op.log(
                    f"Couldn't fetch {e.image} ahead of time — the registry wants "
                    f"credentials this step wasn't given ({e.detail[:160]}). Carrying "
                    f"on with Portainer's own pull, which may leave the service down "
                    f"while it downloads. Store the registry under Admin → Registries "
                    f"to fetch it in advance.", "warning")
                continue
            if e.kind == "timeout":
                raise RuntimeError(
                    f"{e.image} was still downloading{' on ' + e.node if e.node else ''} "
                    f"when the {int(budget)}s limit ran out — nothing was stopped; the "
                    f"running service is unchanged. Raise 'Image pre-pull limit' in "
                    f"Admin → Config for a slow link.") from e
            raise RuntimeError(
                f"Couldn't fetch {e}. Nothing was stopped; the running service is "
                f"unchanged.") from e


async def ensure_image_direct(cli: Any, op: Operation, image_ref: str) -> None:
    """The same guarantee over a direct-Docker client (SSH or TLS).

    Always strict: OmniGrid does the pull itself here, so there is no other
    pull to fall back to. Reads ``cli.last_body`` because the client's
    returned snippet is only the first 300 bytes of the stream.
    """
    repo, tag = split_ref(image_ref)
    label = f"{repo}:{tag}"
    budget = _budget_seconds()
    started = time.monotonic()
    op.log(f"[direct] Fetching {label} before anything is stopped (limit {int(budget)}s)…")
    # No layer counts here: the direct client returns the pull in one piece.
    op.set_phase("fetching", label)
    try:
        st, _b, snip = await asyncio.wait_for(
            cli.post(f"/images/create?fromImage={quote(repo, safe='/:.-_')}"
                     f"&tag={quote(tag, safe='.-_')}"),
            timeout=budget)
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"{label} was still downloading when the {int(budget)}s limit ran out — "
            f"nothing was stopped; the container is unchanged")
    if st >= 400:
        raise RuntimeError(f"Couldn't fetch {label}: HTTP {st}: {snip[:200]}. "
                           f"Nothing was stopped; the container is unchanged.")
    err = stream_error(getattr(cli, "last_body", "") or "")
    if err:
        raise RuntimeError(f"Couldn't fetch {label}: {err}. Nothing was stopped; "
                           f"the container is unchanged.")
    op.log(f"[direct] Fetched {label} in {time.monotonic() - started:,.1f}s")


async def swarm_task_nodes(client: httpx.AsyncClient, service_id: str) -> list[str]:
    """Hostnames of the nodes running (or about to run) a service's tasks.

    ``desired-state=running`` includes a task stuck in ``preparing``, so a
    service that is mid-pull still resolves to its node. Hostnames, because
    that is what ``X-PortainerAgent-Target`` routes on.
    """
    ep = f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"
    filters = quote(json.dumps({"service": [service_id], "desired-state": ["running"]}))
    tasks = await portainer.pg(client, f"{ep}/tasks?filters={filters}") or []
    node_ids = list(dict.fromkeys(
        t.get("NodeID") for t in tasks if isinstance(t, dict) and t.get("NodeID")))
    if not node_ids:
        return []
    nodes = await portainer.pg(client, f"{ep}/nodes") or []
    by_id = {n.get("ID"): ((n.get("Description") or {}).get("Hostname") or "")
             for n in nodes if isinstance(n, dict)}
    return [by_id[i] for i in node_ids if by_id.get(i)]


async def stack_targets(client: httpx.AsyncClient, op: Operation, stack_name: str,
                        image_map: Optional[dict[str, str]] = None) -> list[tuple[str, str]]:
    """``(node, image)`` for every service in a Swarm stack.

    ``image_map`` rewrites an image the compose is being retagged away from to
    the one it is being retagged TO, so a "switch to :latest" fetches the new
    tag rather than re-fetching the old one. A service with no task yet (a
    new one, or scaled to zero) has nothing running to disrupt and is skipped.
    """
    ep = f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"
    services = await portainer.pg(client, f"{ep}/services") or []
    image_map = image_map or {}
    out: list[tuple[str, str]] = []
    for svc in services:
        if not isinstance(svc, dict):
            continue
        spec = svc.get("Spec") or {}
        if ((spec.get("Labels") or {}).get("com.docker.stack.namespace") or "") != stack_name:
            continue
        image = (((spec.get("TaskTemplate") or {}).get("ContainerSpec") or {})
                 .get("Image") or "").split("@", 1)[0]
        if not image:
            continue
        image = image_map.get(image, image)
        nodes = await swarm_task_nodes(client, str(svc.get("ID") or ""))
        if not nodes:
            op.log(f"  {spec.get('Name') or image}: no running task — nothing to disrupt, "
                   f"skipping the advance fetch")
            continue
        out.extend((n, image) for n in nodes)
    return out
