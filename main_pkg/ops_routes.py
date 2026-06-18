"""Write-op endpoints for stacks, containers, and services.
Hosts `/api/update/*` + `/api/restart/*` + `/api/remove/*`
route handlers that each spawn an Operation via `new_op` and
delegate to the matching `do_*` helper in `logic/ops.py`.

Loads via the star-import chain anchored at `main.py` — every
symbol re-exports into `main`'s namespace so route
decorators reach the shared `app` instance.
"""
"""
OmniGrid — Portainer-native update dashboard.

Endpoints:
  GET  /api/items                     - All services + containers with status
  GET  /api/item/{raw_id}             - Single item detail
  POST /api/update/stack/{id}         - Update stack (Prune+PullImage)
  POST /api/update/container/{id}     - Recreate standalone container
  POST /api/restart/service/{id}      - Force restart a Swarm service
  GET  /api/ops   /  /api/ops/{id}    - Live operation status
  GET  /api/history                   - Persisted history
  GET  /api/ignores  /  POST  /  DELETE
  GET  /api/settings /  POST
  POST /api/notify-test
  GET  /api/healthz
  GET  /metrics                       - Prometheus scrape endpoint
"""
# Module-wide suppression for the recurring project-pattern lint noise that
# the operator validates and accepts: defensive broad-except guards (project
# convention is to catch + log + continue at API-boundary sites so a single
# broken provider can't 500 the whole route); cross-module `_protected_member`
# access (helpers like `_node_attr` / `_node_matches` / `_load_mappings` /
# `_PROVIDER_PREFIXES` are deliberately shared by main.py without a public
# alias because the indirection isn't worth a re-export); local `e` / `_events`
# / `_gather_mod` / `_stats_mod` shadow names inside `except` clauses and
# lazy-import blocks; explicit `arg=default` kwargs at call sites kept for
# readability of the intended value; missing docstrings on internal FastAPI
# route handlers whose function name + signature is self-describing; the
# `Member 'None' of 'Any | None'` chain reported on every `_admin: auth.User
# = Depends(auth.require_admin)` parameter (PyCharm cannot narrow through
# FastAPI's Depends() injection). Real bugs OUTSIDE these noise classes are
# fixed inline.
from main import *  # noqa: E402,F401,F403
# IDE contract: PyCharm/Pyright can't trace `from X import *`, so
# every name resolved through the wildcard above would be flagged as
# "Unresolved reference". The explicit imports below resolve at runtime
# too (Python's import system caches; second-import is a dict lookup),
# so they're safe + they silence the IDE in every scope (TYPE_CHECKING
# blocks DON'T propagate into nested function/closure scopes).
from main import (  # noqa: E402,F401 — explicit for IDE; runtime via the * above
    sqlite3,
    AdminUser,
    BaseModel,
    HTTPException,
    Request,
    Response,
    Tunable,
    _actor_from,
    _cache,
    _do_prune_node,
    _do_remove_container,
    _do_restart_container,
    _do_restart_service,
    _do_restart_swarm_agent,
    _do_update_container,
    _do_update_stack,
    _events,
    _gather_mod,
    _item_context,
    _ops_mod,
    _validate_retag_tag,
    app,
    db_conn,
    httpx,
    new_op,
    portainer,
    tuning,
)

from typing import TYPE_CHECKING as _TYPE_CHECKING  # noqa: E402

if _TYPE_CHECKING:
    # IDE-only — `schedules` arrives at runtime via main.py's
    # `from logic import schedules` re-exported through the
    # `from main import *` star-import at the top of this file.
    # Used by the `schedules.UNKNOWN_ACTOR` fallback constant in
    # admin-required write routes (3 sites in this file).
    from logic import schedules  # noqa: F401
import asyncio
import json
import time
from typing import Optional


# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).


@app.post("/api/update/stack/{stack_id}/retag-latest")
async def api_update_stack_retag_latest(
    stack_id: int, body: StackRetagIn, bg: BackgroundTasks, request: Request,
    _admin: AdminUser,
):
    """Switch the stack's compose-file image references to ``:latest``.

    Mutates the compose file in-place via ``_retag_compose_to_latest``,
    then runs the standard update path (Prune=true, PullImage=true) so
    Portainer pulls the new ``:latest`` digest and rolls the
    container(s). Useful for stack-managed standalone containers that
    were originally pinned to a version tag (e.g. ``ghcr.io/foo/bar:2.0.0-dev``)
    and the operator now wants the moving ``:latest`` tag.

    Optional ``image_repo`` filter — when supplied, only image: lines
    whose repo matches that prefix are retagged (for stacks with
    multiple services where only one needs the switch). Otherwise
    every image: line in the compose file flips.

    Note: the digest from the original tag is dropped on retag — pinning
    a digest defeats the point of switching to a moving tag. Operators
    who want to re-pin can manually edit the compose file in Portainer.
    """
    new_tag = _validate_retag_tag(body.tag)
    name = f"stack-{stack_id}"
    for s in _cache["stacks"]:
        if s.get("stack_id") == stack_id:
            name = s["name"]
            break
    op = new_op("update_stack", str(stack_id), name,
                target_stack=name, actor=_actor_from(request))
    bg.add_task(
        _do_update_stack, op, stack_id,
        retag_to_latest=True,
        target_image_repo=body.image_repo,
        new_tag=new_tag,
    )
    return {"op_id": op.id, "new_tag": new_tag}


@app.post("/api/update/container/{container_id}")
async def api_update_container(
    container_id: str, bg: BackgroundTasks, request: Request,
    _admin: AdminUser,
):
    """Trigger a single-container pull + recreate via Portainer's recreate endpoint."""
    name, stack = _item_context(container_id)
    op = new_op("update_container", container_id, name,
                target_stack=stack, actor=_actor_from(request))
    bg.add_task(_do_update_container, op, container_id)
    return {"op_id": op.id}


@app.post("/api/restart/service/{service_id}")
async def api_restart_service(
    service_id: str, bg: BackgroundTasks, request: Request,
    _admin: AdminUser,
):
    """Force a Swarm service to roll its tasks (no image pull)."""
    name, stack = _item_context(service_id)
    op = new_op("restart_service", service_id, name,
                target_stack=stack, actor=_actor_from(request))
    bg.add_task(_do_restart_service, op, service_id)
    return {"op_id": op.id}


@app.post("/api/restart/container/{container_id}")
async def api_restart_container(
    container_id: str, bg: BackgroundTasks, request: Request,
    _admin: AdminUser,
):
    """Restart a standalone container via Portainer."""
    name, stack = _item_context(container_id)
    op = new_op("restart_container", container_id, name,
                target_stack=stack, actor=_actor_from(request))
    bg.add_task(_do_restart_container, op, container_id)
    return {"op_id": op.id}


@app.post("/api/remove/container/{container_id}")
async def api_remove_container(
    container_id: str, bg: BackgroundTasks, request: Request,
    _admin: AdminUser,
):
    """Force-remove a container (idempotent — 404 from upstream is success)."""
    name, stack = _item_context(container_id)
    op = new_op("remove_container", container_id, name,
                target_stack=stack, actor=_actor_from(request))
    bg.add_task(_do_remove_container, op, container_id)
    return {"op_id": op.id}


class CleanupOverlayIn(BaseModel):
    """Request body for the cleanup-overlay-network op — the leaked overlay
    ``subnet`` + the ``service_id`` whose VXLAN sandbox-join failed."""
    subnet: str
    service_id: str


@app.post("/api/cleanup-overlay-network")
async def api_cleanup_overlay_network(
    body: CleanupOverlayIn,
    bg: BackgroundTasks,
    request: Request,
    _admin: AdminUser,
):
    """Auto-fix for the Docker Swarm VXLAN sandbox-join error.

    Walks Portainer's network list to find the overlay matching the
    failing subnet, verifies the network has no live containers, and
    removes it. Docker recreates the overlay + a fresh VXLAN
    interface when the affected service is force-updated immediately
    after. Pure Portainer-API path — no SSH required, no kernel
    access needed (the daemon owns the vxlan as long as the network
    is registered, so `network rm` cleans it up cleanly).

    Aborts when:
      - Subnet is empty / not in CIDR shape.
      - No overlay network matches the subnet (the error may have
        already been resolved between the operator clicking and the
        request landing).
      - Multiple networks match (refuse rather than guess).
      - The matching network has any container in its `Containers`
        map (means another stack is actively using it; operator
        needs to handle that explicitly).
    """
    import re as _re
    subnet = (body.subnet or "").strip()
    service_id = (body.service_id or "").strip()
    if not subnet or not _re.match(r"^\d+\.\d+\.\d+\.\d+/\d+$", subnet):
        raise HTTPException(400, "subnet must be a CIDR (e.g. 10.X.X.0/24)")
    if not service_id:
        raise HTTPException(400, "service_id is required")
    name, stack = _item_context(service_id)
    op = new_op("cleanup_overlay_network", service_id, name,
                target_stack=stack, actor=_actor_from(request))
    bg.add_task(_do_cleanup_overlay_network, op, subnet, service_id)
    return {"ok": True, "op_id": op.id}


async def _service_set_networks(
    client, *, sid: str, spec: dict, version: int,
    networks, op, log_msg: str,
):
    """Update a Swarm service's `Networks` list with a ForceUpdate
    bump + POST to Portainer. Extracted because the strip-then-rm-then-
    restore overlay-network cleanup flow repeats this same shape three
    times (strip the offending net, restore-on-rm-failure, restore-on-
    rm-success). Logs `log_msg` before posting; returns the POST
    response so the caller can check status / handle errors."""
    new_spec = dict(spec)
    new_tt = dict(spec.get("TaskTemplate") or {})
    new_tt["Networks"] = networks
    new_tt["ForceUpdate"] = int(new_tt.get("ForceUpdate", 0)) + 1
    new_spec["TaskTemplate"] = new_tt
    op.log(log_msg)
    return await client.post(
        f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
        f"/docker/services/{sid}/update?version={version}",
        headers=portainer.headers(),
        json=new_spec,
    )


# noinspection DuplicatedCode
async def _do_cleanup_overlay_network(op, subnet: str, service_id: str) -> None:
    """Background task — find overlay matching subnet, remove it,
    force-update the service. Single Operation row + history entry."""
    try:
        op.log(f"Cleanup overlay matching subnet {subnet}")
        async with httpx.AsyncClient(verify=bool(portainer.VERIFY_TLS), timeout=60.0) as client:
            # Step 1 — list every overlay network on the endpoint.
            nets = await portainer.pg(
                client,
                f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                f"/docker/networks?filters="
                + httpx.QueryParams({"filters": '{"driver":["overlay"]}'}).get("filters", "")
            )
            if not isinstance(nets, list):
                raise RuntimeError("Portainer returned non-list for /docker/networks")
            # Step 2 — find every match. Walks IPAM.Config[].Subnet.
            matches = []
            for net in nets:
                ipam = (net.get("IPAM") or {}).get("Config") or []
                for cfg in ipam:
                    if (cfg.get("Subnet") or "") == subnet:
                        matches.append(net)
                        break
            if not matches:
                op.log("No overlay network matches that subnet — error may have already cleared", "warn")
                op.done("error", f"no overlay network matched subnet {subnet}")
                return
            # Multiple-matches handling. Two real-world cases:
            # 1. ALL matches share the SAME name (e.g. `netdata_default`
            #    × 3 on the same subnet) — this IS the VXLAN-orphan
            #    signature: Docker accumulated orphan overlays from
            #    failed redeploys. Safe to delete all of them; Swarm
            #    auto-creates ONE fresh overlay on the next service
            #    deploy. Process every match through the full
            #    strip-rm-restore flow below.
            # 2. Matches with DIFFERENT names → genuinely ambiguous
            #    (subnet collision across unrelated stacks). Refuse so
            #    the operator can pick the right one manually.
            if len(matches) > 1:
                unique_names = {n.get("Name", "?") for n in matches}
                if len(unique_names) > 1:
                    names = ", ".join(n.get("Name", "?") for n in matches)
                    raise RuntimeError(
                        f"refusing — multiple DIFFERENT networks match "
                        f"subnet {subnet}: {names}. Resolve manually."
                    )
                op.log(
                    f"Found {len(matches)} orphan overlays matching "
                    f"subnet {subnet} all named {next(iter(unique_names))!r}"
                    f" — processing each through strip-rm-restore",
                    "warn",
                )
            # Process the FIRST match through the full flow below; if
            # there were extras (orphan family), the trailing for-loop
            # after step 7 cleans them up too (they're guaranteed not
            # to be referenced by any service spec since only ONE
            # network can be the live reference for the service that
            # named it, the others are by definition orphans).
            net = matches[0]
            net_id = net.get("Id") or ""
            net_name = net.get("Name") or net_id[:12]
            extra_orphans = matches[1:]  # cleaned up after step 7
            op.log(f"Matched overlay network {net_name!r} (id={net_id[:12]})")
            # Step 3 — list every service in the endpoint and find
            # which ones reference the target network in their spec.
            # Pure-`Containers`-check is insufficient: Swarm rejects
            # `network rm` while ANY service references the network in
            # its spec, even with zero running tasks. We need to know
            # the full reference set so the recovery flow can edit
            # exactly those services + restore them after.
            op.log("Scanning services for network references")
            all_services = await portainer.pg(
                client,
                f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker/services",
            )
            if not isinstance(all_services, list):
                raise RuntimeError("Portainer returned non-list for /docker/services")
            ref_services: list[dict] = []  # services to strip + restore
            for s in all_services:
                spec = (s or {}).get("Spec") or {}
                nets = ((spec.get("TaskTemplate") or {}).get("Networks") or [])
                if any(
                    (n.get("Target") in (net_id, net_name)) for n in nets
                ):
                    ref_services.append(s)
            ref_names = [s.get("Spec", {}).get("Name", s.get("ID", "?")[:12])
                         for s in ref_services]
            op.log(f"Network referenced by {len(ref_services)} service(s): "
                   f"{', '.join(ref_names) if ref_names else '(none)'}")

            # If the referencing set is empty AND no containers, the
            # original direct-rm path works. Try it first.
            inspect = await portainer.pg(
                client,
                f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                f"/docker/networks/{net_id}",
            )
            containers = (inspect.get("Containers") or {}) if isinstance(inspect, dict) else {}
            if containers and not ref_services:
                names = ", ".join(c.get("Name", "?") for c in containers.values())
                raise RuntimeError(
                    f"refusing — network {net_name!r} still has live "
                    f"containers (and no Swarm service spec references): "
                    f"{names}. Manually stop those containers first."
                )

            # Step 4 — if services reference the network, strip the
            # reference from each, perform the rm, then restore. This
            # is the Swarm-native recovery for "VXLAN sandbox-join
            # failed" / "stale overlay" — Docker auto-creates a fresh
            # overlay (with a new VXLAN id) on the next deploy after we
            # restore the reference. The intermediate spec edit lets
            # the `network rm` succeed where the operator's manual
            # force-restart can't (because force-restart doesn't drop
            # the spec reference).
            originals: dict[str, dict] = {}  # service_id → {"version", "networks"}
            for s in ref_services:
                sid = s.get("ID") or ""
                spec = s.get("Spec") or {}
                version = (s.get("Version") or {}).get("Index", 0)
                tt = spec.get("TaskTemplate") or {}
                nets = list(tt.get("Networks") or [])
                originals[sid] = {"version": version, "networks": nets}
                # Strip the offending network from the spec.
                stripped = [n for n in nets
                            if n.get("Target") not in (net_id, net_name)]
                r = await _service_set_networks(
                    client, sid=sid, spec=spec, version=version,
                    networks=stripped, op=op,
                    log_msg=f"Stripping network from service {spec.get('Name', sid[:12])!r}",
                )
                if r.status_code >= 400:
                    raise RuntimeError(
                        f"failed to strip network from service "
                        f"{spec.get('Name', sid[:12])}: HTTP "
                        f"{r.status_code}: {r.text[:200]}"
                    )

            # Step 5 — wait for Swarm to converge the spec edits.
            # Containers-empty is only HALF the signal — Swarm tasks
            # in "shutting down" state can still hold the network for
            # a few extra seconds after the orchestrator acks the
            # spec update. Poll up to 30s total at 0.5s ticks.
            if ref_services:
                op.log("Waiting for service convergence (network reference to drop)")
                for attempt in range(60):  # ~30s total at 0.5s
                    await asyncio.sleep(0.5)
                    try:
                        net_check = await portainer.pg(
                            client,
                            f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                            f"/docker/networks/{net_id}",
                        )
                        if not (net_check.get("Containers") or {}):
                            break
                    except (httpx.HTTPError, KeyError, AttributeError):
                        # network may already be gone (rare; another
                        # concurrent caller could have raced us). Treat
                        # as success and proceed.
                        break

            # Step 6 — remove the network. Retry-on-"in use by task"
            # because Swarm tasks transition through a "shutdown"
            # state where they STILL hold the network resource for a
            # few seconds after the orchestrator marked them for
            # removal. The `Containers` check above only sees DOCKER
            # containers; Swarm tasks are a separate layer. So even
            # after Containers={} the rm may still 400. Retry every
            # 1s for up to 30s — typically a single task drains in
            # 5-15s.
            op.log(f"Removing overlay {net_name!r}")
            r = None
            for rm_attempt in range(31):  # initial + 30 retries
                r = await client.delete(
                    f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                    f"/docker/networks/{net_id}",
                    headers=portainer.headers(),
                )
                if r.status_code < 400:
                    break
                raw_txt = r.text[:300]
                # Only retry the "in use by task" transient — other
                # 400s (e.g. permission denied, network not found)
                # break out immediately.
                if "in use by task" in raw_txt and rm_attempt < 30:
                    if rm_attempt == 0:
                        op.log(
                            "Network still held by a draining Swarm task — "
                            "polling until released (up to 30s)",
                            "warn",
                        )
                    await asyncio.sleep(1.0)
                    continue
                break
            if r is not None and r.status_code >= 400:
                raw_text = r.text[:300]
                # If the rm STILL fails after stripping the spec refs
                # (e.g. a service we couldn't reach is holding the
                # ref, or convergence didn't land in time), restore
                # the spec edits before raising so the operator's
                # services aren't left network-less.
                _restore_errors: list[str] = []
                for sid, orig in originals.items():
                    try:
                        cur = await portainer.pg(
                            client,
                            f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                            f"/docker/services/{sid}",
                        )
                        cur_spec = cur.get("Spec") or {}
                        cur_version = (cur.get("Version") or {}).get("Index", orig["version"])
                        await _service_set_networks(
                            client, sid=sid, spec=cur_spec, version=cur_version,
                            networks=orig["networks"], op=op,
                            log_msg=f"Restoring network reference on service {cur_spec.get('Name', sid[:12])!r} (after rm failure)",
                        )
                    except Exception as _re:  # noqa: BLE001
                        _restore_errors.append(f"{sid[:12]}: {_re}")
                err_tail = (" RESTORE-FAILED for: " + "; ".join(_restore_errors)
                            if _restore_errors else
                            " (service network references restored)")
                raise RuntimeError(
                    f"network rm HTTP {r.status_code}: {raw_text}"
                    + err_tail
                )
            op.log("Overlay network removed", "success")

            # Step 7 — restore the network references on every service
            # we stripped. Swarm auto-creates the overlay on the next
            # deploy (with a fresh VXLAN id, which is the whole point
            # of this exercise).
            for sid, orig in originals.items():
                try:
                    cur = await portainer.pg(
                        client,
                        f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                        f"/docker/services/{sid}",
                    )
                    cur_spec = cur.get("Spec") or {}
                    cur_version = (cur.get("Version") or {}).get("Index", orig["version"])
                    r2 = await _service_set_networks(
                        client, sid=sid, spec=cur_spec, version=cur_version,
                        networks=orig["networks"], op=op,
                        log_msg=f"Restoring network reference on service {cur_spec.get('Name', sid[:12])!r}",
                    )
                    if r2.status_code >= 400:
                        op.log(
                            f"WARN — restore failed for service "
                            f"{cur_spec.get('Name', sid[:12])}: "
                            f"HTTP {r2.status_code}: {r2.text[:200]}",
                            "error",
                        )
                except Exception as e:  # noqa: BLE001
                    op.log(f"WARN — restore exception for service {sid[:12]}: {e}", "error")

            # Step 7.5 — orphan-family cleanup. When multiple
            # networks matched the same subnet AND shared the same
            # name, the loop above processed the first match through
            # the full strip-rm-restore; the remaining matches are
            # by definition orphans (Docker only routes service
            # references to ONE network, the rest are dead overlays
            # left behind by failed redeploys). Best-effort direct
            # rm — log + skip on per-orphan failure so one stuck
            # orphan doesn't block the others.
            for orphan in extra_orphans:
                orphan_id = orphan.get("Id") or ""
                orphan_name = orphan.get("Name") or orphan_id[:12]
                if not orphan_id:
                    continue
                op.log(f"Removing orphan overlay {orphan_name!r} (id={orphan_id[:12]})")
                try:
                    r = await client.delete(
                        f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                        f"/docker/networks/{orphan_id}",
                        headers=portainer.headers(),
                    )
                    if r.status_code >= 400:
                        op.log(
                            f"WARN — orphan rm failed for {orphan_name!r}: "
                            f"HTTP {r.status_code}: {r.text[:200]}",
                            "error",
                        )
                    else:
                        op.log(f"Orphan {orphan_name!r} removed", "success")
                except Exception as e:  # noqa: BLE001
                    op.log(f"WARN — orphan rm exception for {orphan_name!r}: {e}", "error")

            # Step 8 — force-update the originally-targeted service
            # one more time so the operator sees an immediate redeploy
            # attempt (in case the strip+restore dance already
            # converged the service's tasks back to running). The
            # restore step above only fires for services in
            # `ref_services`; the targeted service may or may not have
            # been one of them. Re-fetch and bump ForceUpdate so the
            # task respawns onto the freshly-created overlay.
            if service_id and service_id not in originals:
                op.log("Force-updating originally-targeted service")
                svc = await portainer.pg(
                    client,
                    f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker/services/{service_id}",
                )
                version = svc["Version"]["Index"]
                spec = svc["Spec"]
                spec.setdefault("TaskTemplate", {})
                spec["TaskTemplate"]["ForceUpdate"] = int(spec["TaskTemplate"].get("ForceUpdate", 0)) + 1
                r = await client.post(
                    f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                    f"/docker/services/{service_id}/update?version={version}",
                    headers=portainer.headers(),
                    json=spec,
                )
                if r.status_code >= 400:
                    raise RuntimeError(f"service update HTTP {r.status_code}: {r.text[:300]}")
        op.log("Cleanup complete — watch for new task to come up", "success")
        op.done("success")
        # net_name is already inlined into the notify body below; no
        # need to stamp it onto `op` (Operation uses __slots__ which
        # rejects attribute assignment outside the declared set, AND
        # nothing downstream reads `op.network_name` anyway).
        from logic.ops import notify as _notify
        await _notify(
            f"🧹 Stale overlay cleaned: {net_name}",
            f"Removed overlay {net_name} (subnet {subnet}) and force-updated {op.target_name}.",
            "success",
            event="overlay_cleanup_success",
            actor_username=op.actor,
            target_kind="service",
            target_id=str(op.target_id),
        )
    except Exception as e:  # noqa: BLE001
        op.log(str(e), "error")
        op.done("error", str(e))
        from logic.ops import notify as _notify
        await _notify(
            f"❌ Overlay cleanup failed: {op.target_name}",
            str(e)[:500],
            "error",
            event="overlay_cleanup_failure",
            actor_username=op.actor,
            target_kind="service",
            target_id=str(op.target_id),
        )
    finally:
        from logic.ops import persist_history
        persist_history(op)
        _gather_mod.invalidate_cache()


@app.post("/api/swarm/restart-agent")
async def api_swarm_restart_agent(
    bg: BackgroundTasks, request: Request,
    _admin: AdminUser,
):
    """Admin-only: force-restart the Portainer agent global service.

    Operator-triggered companion to the unhealthy-agent banner. The
    agent service is auto-discovered (image-prefix + name fallback —
    see `logic/ops.py:discover_swarm_agent_service`); on ambiguous
    discovery the op fails with a listing of every candidate so the
    operator can pick rather than risk auto-restarting the wrong
    service. Same Operation flow as `/api/restart/service/<id>` —
    op_id polling, history row, Apprise + in-app notifications.
    """
    # Provisional target_id / target_name — discover_swarm_agent_service
    # fills in the real values as part of the op's logged steps.
    op = new_op("restart_swarm_agent", "", "<portainer-agent>",
                actor=_actor_from(request))
    bg.add_task(_do_restart_swarm_agent, op)
    return {"op_id": op.id}


@app.post("/api/prune/node/{hostname}")
async def api_prune_node(
    hostname: str, bg: BackgroundTasks, request: Request,
    _admin: AdminUser,
):
    """Run a Docker-system-prune equivalent on a specific Swarm node.

    Matches `docker system prune -f --volumes` — stopped containers,
    dangling images, unused networks + volumes, build cache. Same model
    as the existing update/restart ops: kicks off a BackgroundTask,
    returns the op id, UI polls /api/ops for progress. Admin-only.
    """
    # Light sanity on the hostname so we don't send garbage through to
    # Portainer's agent-target header. node_for_container validates against
    # the cache; do the same for explicit hostnames.
    known = set(_cache.get("nodes", {}).values())
    if known and hostname not in known:
        raise HTTPException(status_code=400, detail=f"Unknown node: {hostname}")
    op = new_op(
        "prune_node", hostname, hostname,
        actor=_actor_from(request),
    )
    bg.add_task(_do_prune_node, op, hostname)
    return {"op_id": op.id}


@app.get("/api/ops")
async def api_ops():
    """Return the in-memory op log (newest-first, capped at 50)."""
    return {"ops": [ops[oid].to_dict() for oid in ops_order if oid in ops]}


@app.get("/api/ops/{op_id}")
async def api_op(op_id: str):
    """Return one operation by id, or 404."""
    op = ops.get(op_id)
    if not op:
        raise HTTPException(404, "Op not found")
    return op.to_dict()


# ============================================================================
# Real-time event stream (SSE)
# ----------------------------------------------------------------------------
# Replaces the SPA's polling cadence on cookie-authed callers. Bearer-token
# machine clients can't easily set custom request headers via EventSource so
# they keep polling — that's documented in `docs/guidelines/api.md`.
#
# Auth: middleware enforces 401 on missing identity for every /api/* path
# except the documented public/auth-optional set, so this route inherits the
# standard cookie-OR-bearer check just like /api/ops or /api/items.
#
# CSRF: SSE is GET-only; no CSRF cookie check applies (the global middleware
# only runs CSRF on state-changing methods).
#
# Heartbeat: emit a real ``event: keepalive`` line every 25s. NOT a
# comment line — `EventSource.onmessage` doesn't fire for SSE comments,
# so a comment-only heartbeat keeps the TCP socket alive but never
# reaches the SPA's freshness watchdog (which advances
# `_sseLastEventTs` only on real events). Pre-fix the comment-form
# caused a 30s-quiet-window false-flip into polling-fallback mode even
# though the connection was healthy. The real-event form lets the
# generic onmessage listener bump the timestamp on every heartbeat,
# AND keeps the socket-warm property the comment had.
# ============================================================================
# / both moved to TUNABLES (tuning_sse_heartbeat_seconds,
# tuning_sse_max_lifetime_seconds). Resolve at the consumer site via
# `tuning.tuning_int(...)` so a Save in Admin → Config takes effect on
# the next /api/events reconnect — no module-level constants here so a
# stale import-time read can't pin the old value. The historical
# defaults (25s heartbeat, 6h lifetime — 1h margin before session 8h
# hard cap) are preserved as the TUNABLES defaults; bounds on the
# lifetime knob (3600-25200s = 1h-7h) prevent an operator from racing
# past the session hard cap.


def _format_sse(evt: dict) -> str:
    """One SSE record per event. ``event:`` carries the type, ``data:``
    is JSON.

    Handles the special ``:overflow`` synthetic emitted by
    ``logic.events`` when a subscriber's queue dropped events — the
    SPA reacts by doing a one-shot REST refresh to catch up.
    """
    ev_type = evt.get("type") or "message"
    payload = {
        "type": ev_type,
        "ts": evt.get("ts"),
        "payload": evt.get("payload") or {},
    }
    return f"event: {ev_type}\ndata: {json.dumps(payload, default=str)}\n\n"


@app.get("/api/events")
async def api_events(request: Request):
    """Server-sent events stream — one connection per SPA tab.

    The SPA's polling loops idle while this connection is healthy; if
    the connection drops, polling resumes within ~30s as the fallback
    safety net (see static/js/app.js:_sseConnected).
    """

    async def event_stream():
        """Per-connection event generator — yields a `hello` frame,
        then forwards each `events.bus` message as an SSE `data:` line
        until the consumer disconnects."""
        # ``hello`` lands as the first frame so the client can confirm
        # the upgrade succeeded BEFORE waiting for the first organic
        # event. Carries process-level diagnostics that the connection-
        # state indicator surfaces in its tooltip.
        # heartbeat cadence is operator-tunable; resolve per
        # connection-open so a Save takes effect on the next reconnect.
        heartbeat_seconds = tuning.tuning_int(Tunable.SSE_HEARTBEAT_SECONDS)
        max_lifetime_seconds = tuning.tuning_int(Tunable.SSE_MAX_LIFETIME_SECONDS)
        yield _format_sse({
            "type": "hello",
            "ts": time.time(),
            "payload": {
                "subscriber_count": _events.subscriber_count(),
                "heartbeat_seconds": heartbeat_seconds,
            },
        })

        async def producer(queue: asyncio.Queue):
            """Consume the event-bus iterator and forward into a local
            queue. Runs as a task so we can race it against the
            heartbeat timer + the disconnect check.

            `queue.put_nowait` with overflow synthesis: pre-fix
            this awaited an unbounded `queue.put`, so a slow client
            could let the local queue grow without bound while the
            bus's drop-oldest cap (256) stayed satisfied (because we
            moved events off the bus queue immediately). Now we mirror
            the bus's bound; on `QueueFull`, drop the new event and
            emit a synthetic `:local-overflow` hint so the SPA can
            reconcile via REST (same recovery path the existing
            `:overflow` triggers).
            """
            try:
                async for sub_evt in _events.bus.subscribe():
                    try:
                        queue.put_nowait(sub_evt)
                    except asyncio.QueueFull:
                        try:
                            queue.put_nowait({
                                "type": ":local-overflow",
                                "ts": time.time(),
                                "payload": {"dropped_type": sub_evt.get("type")},
                            })
                        except asyncio.QueueFull:
                            # Even the overflow signal didn't fit —
                            # the consumer is stuck. Drop silently;
                            # the outer disconnect-check will reap
                            # the connection on the next iteration.
                            pass
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                # Defensive — log and signal end-of-stream so the
                # outer loop exits cleanly on bus malfunction.
                print(f"[events] subscribe iterator failed: {e}")
                await queue.put(None)

        local: asyncio.Queue = asyncio.Queue(maxsize=256)
        task = asyncio.create_task(producer(local))
        started_at = time.time()
        try:
            while True:
                if await request.is_disconnected():
                    break
                # Cap the connection's wall-clock lifetime so the auth
                # middleware re-fires on the EventSource reconnect and
                # the session cookie's sliding-window refresh has a
                # chance to land before the 8h hard cap. Emit a synthetic `reconnect` hint so the
                # SPA logs the cycle in dev-tools network tab; the
                # `EventSource` API itself reconnects automatically on
                # any normal end-of-stream.
                if (time.time() - started_at) > max_lifetime_seconds:
                    yield _format_sse({
                        "type": "reconnect",
                        "ts": time.time(),
                        "payload": {"reason": "lifetime_cap"},
                    })
                    break
                try:
                    evt = await asyncio.wait_for(
                        local.get(), timeout=heartbeat_seconds,
                    )
                except asyncio.TimeoutError:
                    # No traffic for the heartbeat window — keep the
                    # socket warm AND give the SPA's freshness watchdog
                    # something to consume so it doesn't false-flip
                    # to polling-fallback during quiet periods.
                    # Emitted as a real `event: keepalive` line (NOT a
                    # `: comment` line) because EventSource fires
                    # `onmessage` only for real events; comment lines
                    # arrive at the socket but never reach the SPA's
                    # event handler that advances `_sseLastEventTs`.
                    # Empty JSON payload — the event's existence is
                    # the signal, no fields to carry.
                    yield "event: keepalive\ndata: {}\n\n"
                    continue
                if evt is None:
                    # Bus signalled end-of-stream; propagate cleanly.
                    break
                yield _format_sse(evt)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # Disable upstream buffering — nginx + NPM both proxy SSE
            # by default but the X-Accel-Buffering hint guarantees the
            # bytes flush per event instead of being chunked.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _history_query(
    stack: Optional[str], op_type: Optional[str], status: Optional[str],
    actor: Optional[str], q: Optional[str],
    since: Optional[float], until: Optional[float],
    limit: int,
    offset: int = 0,
    *,
    with_total: bool = False,
    target_kind: Optional[str] = None,
):
    """Shared builder for filterable history queries. All filters are
    optional; missing ones degrade gracefully to an unfiltered scan.

    When ``with_total=True`` the return value is ``(rows, total)`` —
    ``total`` is the unpaginated COUNT(*) for the same WHERE clause,
    used by the SPA's server-side pager. Default ``with_total=False``
    preserves the legacy list-only return shape so the export endpoints
    don't pay the extra query.
    """
    where, params = [], []
    if stack:
        # Match ops whose recorded target_stack is this stack, plus historical
        # rows (pre-column) where target_name happens to equal it.
        where.append("(target_stack = ? OR target_name = ?)")
        params.extend([stack, stack])
    if op_type:
        where.append("op_type = ?")
        params.append(op_type)
    if target_kind:
        where.append("target_kind = ?")
        params.append(target_kind)
    if status:
        where.append("status = ?")
        params.append(status)
    if actor:
        where.append("actor = ?")
        params.append(actor)
    if q:
        # Escape SQLite LIKE meta-chars (%, _) so a search query
        # containing those characters doesn't get treated as wildcards.
        # Pairs with `LIKE ? ESCAPE '\\'`. Same security drift class
        # as the host-id sites earlier — any `LIKE` against operator-
        # influenced input goes through the helper.
        # Lazy import — hosts_routes loads LATER in the main_pkg chain
        # so a top-level import here would trigger its tail chain (the
        # StaticFiles catch-all mounts there) BEFORE ops_routes finishes
        # registering its decorators. By call time main is fully loaded.
        from main_pkg.hosts_routes import _sqlite_like_escape
        like = "%" + _sqlite_like_escape(q) + "%"
        where.append(
            "(target_name LIKE ? ESCAPE '\\' "
            "OR target_id LIKE ? ESCAPE '\\' "
            "OR error LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like])
    if since is not None:
        where.append("ts >= ?")
        params.append(since)
    if until is not None:
        where.append("ts <= ?")
        params.append(until)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    eff_limit = max(1, min(limit, 5000))
    eff_offset = max(0, int(offset or 0))
    data_sql = f"SELECT * FROM history{where_sql} ORDER BY ts DESC LIMIT ? OFFSET ?"
    data_params = list(params) + [eff_limit, eff_offset]
    # Pre-bind `total` so the type-checker sees a single assignment
    # path: either it's set inside the `if with_total` block OR it
    # stays 0 (unread on the non-`with_total` return). Without this
    # the linter flags `total` as possibly-unbound at the return.
    total = 0
    with db_conn() as c:
        rows = c.execute(data_sql, data_params).fetchall()
        if with_total:
            count_sql = f"SELECT COUNT(*) AS n FROM history{where_sql}"
            total_row = c.execute(count_sql, params).fetchone()
            total = int(total_row["n"] if total_row else 0)
    rows_out = [dict(r) for r in rows]
    if with_total:
        return rows_out, total
    return rows_out


@app.get("/api/history")
async def api_history(
    limit: int = 100,
    offset: int = 0,
    stack: Optional[str] = None,
    op_type: Optional[str] = None,
    status: Optional[str] = None,
    actor: Optional[str] = None,
    q: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
    target_kind: Optional[str] = None,
):
    """Return paginated history rows with optional filters."""
    rows, total = _history_query(
        stack, op_type, status, actor, q, since, until,
        limit, offset=offset, with_total=True,
        target_kind=target_kind,
    )
    return {
        "history": rows,
        "total": total,
        "offset": max(0, int(offset or 0)),
        "limit": max(1, min(int(limit or 100), 5000)),
    }


# noinspection DuplicatedCode
@app.get("/api/history.json")
async def api_history_json_export(
    limit: int = 5000,
    stack: Optional[str] = None,
    op_type: Optional[str] = None,
    status: Optional[str] = None,
    actor: Optional[str] = None,
    q: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
):
    """Stream history rows as a downloadable JSON file."""
    rows = _history_query(stack, op_type, status, actor, q, since, until, limit)
    return Response(
        content=json.dumps(rows, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="omnigrid-history.json"'},
    )


# noinspection DuplicatedCode
@app.get("/api/history.csv")
async def api_history_csv_export(
    limit: int = 5000,
    stack: Optional[str] = None,
    op_type: Optional[str] = None,
    status: Optional[str] = None,
    actor: Optional[str] = None,
    q: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
):
    """Stream history rows as a downloadable CSV file."""
    import csv
    import io

    _raw_rows = _history_query(stack, op_type, status, actor, q, since, until, limit)
    rows: list[dict] = _raw_rows if isinstance(_raw_rows, list) else []
    # Fixed column order — stable for spreadsheet pivots. `events` is
    # omitted from CSV (multi-line JSON doesn't round-trip cleanly); users
    # needing full event logs should export JSON.
    cols = ["ts", "op_type", "target_kind", "status", "actor", "target_stack",
            "target_name", "target_id", "duration", "error"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c, "") for c in cols])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="omnigrid-history.csv"'},
    )


@app.delete("/api/history")
async def api_history_clear(_admin: AdminUser):
    """Truncate the history table (audit row written BEFORE the DELETE)."""
    with db_conn() as c:
        # Count first so the audit row can carry the size of the wipe.
        try:
            cleared_count = c.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        except sqlite3.Error:
            cleared_count = 0
        c.execute("DELETE FROM history")
        # Audit row written AFTER the bulk DELETE so it survives the
        # truncation. The trailing row is the forensic anchor —
        # destroying the audit trail is itself an audit event.
        _ops_mod.write_admin_audit(
            c, "history_cleared",
            target_kind="history",
            target_name="all",
            actor=_admin.username or schedules.UNKNOWN_ACTOR,
            message=f"history table cleared by {_admin.username or 'operator'} "
                    f"({cleared_count} row(s) destroyed)",
        )
    return {"status": "cleared"}


class IgnoreIn(BaseModel):
    """Request body for adding an ignore rule — a ``pattern`` + its ``kind``
    (``image`` substring / ``stack`` exact match) + an optional ``reason``."""
    pattern: str
    kind: str
    reason: Optional[str] = ""

    @field_validator("kind")
    @classmethod
    def _kind_must_be_known(cls, v: str) -> str:
        # ``logic.gather.is_ignored`` only honours these two values; a
        # typo silently inserted a no-op row before this validator.
        # Reject early with a clear 422 from FastAPI so the operator
        # learns the typo at edit time rather than wondering why their
        # ignore rule isn't taking effect.
        normalised = (v or "").strip().lower()
        if normalised not in ("image", "stack"):
            raise ValueError("kind must be 'image' or 'stack'")
        return normalised


@app.get("/api/ignores")
async def api_ignores():
    """Return the operator-curated ignore patterns."""
    with db_conn() as c:
        rows = c.execute("SELECT * FROM ignores ORDER BY created DESC").fetchall()
    return {"ignores": [dict(r) for r in rows]}


@app.post("/api/ignores")
async def api_add_ignore(
    ig: IgnoreIn,
    _admin: AdminUser,
):
    """Add a new ignore pattern (image substring or stack name)."""
    with db_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO ignores(pattern,kind,reason,created) VALUES (?,?,?,?)",
            (ig.pattern, ig.kind, ig.reason or "", time.time()),
        )
        _ops_mod.write_admin_audit(
            c, "ignore_create",
            target_kind="ignore",
            target_name=ig.pattern,
            target_id=ig.kind,
            actor=_admin.username or schedules.UNKNOWN_ACTOR,
            message=f"ignore added pattern={ig.pattern!r} kind={ig.kind} "
                    f"reason={(ig.reason or '').strip()[:120]!r}",
        )
    _cache["ts"] = 0
    return {"status": "ok"}


# Drop the `:path` converter on this route — `pattern` here is an
# ignore-list literal (image / stack name), never a multi-segment path.
# The converter was a copy-paste from /node_modules and triggers the
# PyCharm FastAPI inspector's "pattern:path not in function parameters"
# false positive. Bare `{pattern}` matches a single segment which is all
# the ignore-pattern surface ever supplies.
@app.delete("/api/ignores/{pattern}")
async def api_del_ignore(
    pattern: str,
    _admin: AdminUser,
):
    """Delete an ignore pattern by (pattern, kind)."""
    with db_conn() as c:
        c.execute("DELETE FROM ignores WHERE pattern=?", (pattern,))
        _ops_mod.write_admin_audit(
            c, "ignore_delete",
            target_kind="ignore",
            target_name=pattern,
            actor=_admin.username or schedules.UNKNOWN_ACTOR,
            message=f"ignore deleted pattern={pattern!r}",
        )
    _cache["ts"] = 0
    return {"status": "ok"}


class SettingsIn(BaseModel):
    """The additive PUT body for ``POST /api/settings`` — every UI-managed
    setting + ``tuning_*`` knob as an Optional field (``None`` = keep current;
    non-empty overwrites). See CLAUDE.md 'Settings API is additive'."""
    # Per-service "enabled" master switches. Default true (legacy
    # behaviour preserved on first boot). When false, the service's
    # consumer code short-circuits — values stay in the settings
    # table so the operator can flip back on without re-typing. The
    # admin UI also disables the inputs visually so the operator
    # sees the saved config grayed out, not erased.
    apprise_enabled: Optional[bool] = None
    open_meteo_enabled: Optional[bool] = None
    portainer_enabled: Optional[bool] = None
    ssh_enabled: Optional[bool] = None
    asset_inventory_enabled: Optional[bool] = None
    apprise_url: Optional[str] = None
    apprise_tag: Optional[str] = None
    # Telegram notification medium (Phase 1: send-only). Token follows
    # the write-only secret contract (any non-empty value overwrites;
    # blank = keep current). Chat ID and optional thread ID are plain
    # strings (chat IDs are large negative ints for supergroups; we
    # accept strings to avoid int-overflow edge cases on legacy migrations).
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_thread_id: Optional[str] = None
    telegram_verify_tls: Optional[str] = None
    # Telegram Bot API base URL. Defaults to https://api.telegram.org
    # when blank. Operators with a self-hosted Bot API server (e.g. via
    # tdlight or local TDLib) or behind a CGNAT proxy can override.
    telegram_api_base: Optional[str] = None
    # Telegram listener long-poll + outer-HTTP timeouts (TUNABLES).
    # Surfaced in Notifications → Telegram via the section's TUNABLES
    # editor — declared here so the additive POST validator accepts them.
    tuning_telegram_long_poll_timeout_seconds: Optional[str] = None
    tuning_telegram_http_timeout_seconds: Optional[str] = None
    tuning_telegram_destructive_cooldown_seconds: Optional[str] = None
    tuning_telegram_ai_calls_per_minute: Optional[str] = None
    tuning_telegram_bulk_update_concurrency: Optional[str] = None
    tuning_seerr_suggest_cooldown_hours: Optional[str] = None
    notify_medium_telegram: Optional[str] = None
    # Phase 2 — inbound command listener config. `enabled` controls
    # whether the long-poll loop in `logic/telegram_listener.py` fires
    # `getUpdates` against the bot. `allow_destructive` skips the
    # typed-confirm second step on `/restart` (and any future
    # destructive verb). `authorized_user_ids` is a CSV of Telegram
    # user_id ints — empty means "any sender in the authorized chat
    # is allowed" (chat-id gate is the only check).
    telegram_listener_enabled: Optional[str] = None
    telegram_allow_destructive: Optional[str] = None
    telegram_authorized_user_ids: Optional[str] = None
    portainer_public_url: Optional[str] = None
    # Portainer connection (DB-backed, UI-managed). API key follows the
    # write-only / "keep current if blank" contract: the browser never
    # receives the current value, only whether it's set. Pass a non-
    # empty string to overwrite.
    portainer_url: Optional[str] = None
    portainer_api_key: Optional[str] = None
    portainer_endpoint_id: Optional[int] = None
    portainer_verify_tls: Optional[bool] = None
    # OIDC provider settings (DB-backed, UI-managed). Client secret uses
    # the same keep-current-if-blank contract as portainer_api_key.
    oidc_enabled: Optional[bool] = None
    oidc_issuer_url: Optional[str] = None
    oidc_client_id: Optional[str] = None
    oidc_client_secret: Optional[str] = None
    oidc_redirect_uri: Optional[str] = None
    oidc_scopes: Optional[str] = None
    oidc_admin_group: Optional[str] = None
    oidc_verify_tls: Optional[bool] = None
    # case-insensitive admin-group claim match. Default
    # True preserves the legacy exact-match contract.
    oidc_group_case_sensitive: Optional[bool] = None
    # NOTE: the legacy plain `backup_retention_count` field was removed —
    # the value migrated to TUNABLES (tuning_backup_retention_count). The
    # prune consumer (logic/schedules.py) + the GET both read the tunable;
    # nothing reads the plain settings row, so the field + its write path
    # were dead. Per CLAUDE.md "Legacy SettingsIn fields MUST be deleted
    # when their value migrates to TUNABLES".
    # Host-stats integration via node-exporter. When enabled, OmniGrid
    # scrapes each node's /metrics endpoint during gather to surface real
    # host disk / memory / uptime (vs. the Docker-only numbers Portainer
    # exposes). URL template uses {host} → Docker hostname; default
    # http://{host}:9100/metrics works for a typical Swarm global-mode
    # node-exporter deploy.
    node_exporter_enabled: Optional[bool] = None
    node_exporter_url_template: Optional[str] = None
    # Per-hostname URL overrides for nodes where the default template's
    # {host} substitution doesn't resolve (e.g. a node whose Docker
    # hostname isn't reachable via DNS from the OmniGrid container).
    # Stored as a JSON object: {"hostname": "http://explicit:9100/metrics"}.
    node_exporter_overrides: Optional[dict] = None
    # Host-stats source selector — mutually exclusive. "none" disables
    # host-stats entirely, "node_exporter" uses the scrape path, and
    # "beszel" consumes a Beszel Hub's PocketBase API. Kept alongside
    # the per-source settings rather than auto-inferred so an operator
    # can temporarily flip sources without blanking their config.
    host_stats_source: Optional[str] = None
    # Beszel Hub — URL, identity (usually email), password. Password
    # is write-only on the wire like the other secret fields (empty
    # string "keep current", non-empty "replace").
    beszel_hub_url: Optional[str] = None
    beszel_identity: Optional[str] = None
    beszel_password: Optional[str] = None
    beszel_verify_tls: Optional[bool] = None
    # Per-node name aliases — Docker hostname → Beszel system name. Use
    # when the name the operator gave a system in Beszel doesn't match
    # the Docker Swarm hostname. Example:
    # {"docker01": "docker.example.com"}
    # Nodes not listed here fall back to identity mapping.
    beszel_aliases: Optional[dict] = None
    # Pulse (rcourtman/Pulse) — third host-stats provider. PVE-only.
    # Token is write-only on the wire like beszel_password.
    pulse_url: Optional[str] = None
    pulse_token: Optional[str] = None
    pulse_verify_tls: Optional[bool] = None
    # Docker hostname → Pulse node name. Separate from beszel_aliases
    # because Pulse uses PVE node names (e.g. ``pve-1``, ``host01``) which
    # tend to differ from Beszel hostnames.
    pulse_aliases: Optional[dict] = None
    # Webmin — fourth host-stats provider. Each target host runs its
    # own Miniserv instance so the probe URL is per-host, not a hub.
    # ``webmin_aliases`` maps Docker hostname → full Miniserv base URL
    # (e.g. ``{"docker01": "https://docker.example.com:10000"}``).
    # ``webmin_url`` is retained as an optional default/template for
    # future use. Password is write-only like every other secret.
    webmin_url: Optional[str] = None
    webmin_user: Optional[str] = None
    webmin_password: Optional[str] = None
    webmin_verify_tls: Optional[bool] = None
    webmin_aliases: Optional[dict] = None
    # Ping — fifth host-stats provider. Reachability + RTT only,
    # opt-in per host (hosts_config[].ping.enabled). No credentials, no
    # aliases — the provider runs against the host's own id (or the
    # per-host SSH FQDN override). ``ping_default_port`` is the TCP port
    # used when a per-host row doesn't override; ``ping_use_icmp`` flips
    # the global default transport when the icmplib package is present
    # AND the container has CAP_NET_RAW (per-host ``transport``
    # overrides individually). Three matching tunables resolve via
    # logic/tuning.py.
    ping_enabled: Optional[bool] = None
    ping_default_port: Optional[int] = None
    ping_use_icmp: Optional[bool] = None
    # Port-scan provider — on-demand TCP-connect scanner. Triggered
    # from the host drawer or the AI palette; never scheduled in
    # Stage 1. ``port_scan_enabled`` is the master toggle; the
    # default-* keys carry global defaults that per-host
    # ``hosts_config[].port_scan = {enabled, ports?, timeout_s?,
    # concurrency?}`` overrides on a row-by-row basis. Ports list is
    # CSV / range syntax (e.g. "22,80,443,8000-8100").
    port_scan_enabled: Optional[bool] = None
    port_scan_default_ports: Optional[str] = None
    port_scan_default_timeout_seconds: Optional[int] = None
    port_scan_default_concurrency: Optional[int] = None
    # Stage 2 (UDP companion). UDP scanning runs alongside TCP under the
    # SAME master toggle ``port_scan_enabled`` — operator-flagged
    # 2026-05-10 to remove the separate ``port_scan_udp_enabled`` flag
    # and unify the on/off behaviour. The field is kept on the model
    # for back-compat (legacy snapshots / older clients can still POST
    # it without breaking validation) but the value is IGNORED on save.
    # UDP default ports use the same CSV / range syntax as TCP.
    port_scan_udp_enabled: Optional[bool] = None  # DEPRECATED — value ignored on save
    port_scan_udp_default_ports: Optional[str] = None
    # SNMP — sixth host-stats provider. Per-host probe (no
    # central hub). Defaults are global; per-host overrides live on
    # ``hosts_config[].snmp = {community, version, port, v3_*}``.
    # ``snmp_default_community`` defaults to "public" (the common read-
    # only community on home-lab gear); ``snmp_default_version``
    # accepts "v2c" or "v3"; ``snmp_default_port`` defaults to 161.
    # The three v3 keys (user / auth-key / priv-key) follow the same
    # write-only ``_set`` flag contract as every other secret — empty
    # input keeps the current value, non-empty replaces it.
    # ``snmp_aliases`` maps Docker hostname → SNMP target IP/host so
    # the probe can hit a different address than the curated row's id.
    snmp_default_community: Optional[str] = None
    snmp_default_version: Optional[str] = None
    snmp_default_port: Optional[int] = None
    snmp_v3_user: Optional[str] = None
    snmp_v3_auth_key: Optional[str] = None
    snmp_v3_priv_key: Optional[str] = None
    snmp_aliases: Optional[dict] = None
    # HTTP / TLS-cert / DNS health probe — seventh host-stats provider.
    # Master toggle (plain setting, legacy shape matching the other
    # providers) + alias map (Docker hostname → probe URL when the
    # curated row's ``url`` field isn't the right probe target — same
    # use case as ``webmin_aliases`` / ``snmp_aliases``). Per-host
    # opt-in via ``hosts_config[].http_probe = {enabled, urls?,
    # content_match?, accepted_status_codes?, verify_tls?}``.
    http_probe_enabled: Optional[bool] = None
    http_probe_aliases: Optional[str] = None  # CSV: "docker_host=probe_url,..."
    # Per-service reachability probe master toggle — distinct from
    # the host-level HTTP probe. Per-chip opt-in via
    # ``hosts_config[].services[].probe = {enabled, type, port?, path?,
    # expected_status?}``. Default OFF — the sampler stays dormant
    # until the operator flips this AND opts at least one service in.
    service_probe_enabled: Optional[bool] = None
    # Per-provider chip color — operator-customisable hex colour
    # for the per-host provider chip rendered in the Hosts view + the
    # drawer's "Enabled agents" card. Each value is a 7-char `#RRGGBB`
    # string OR blank to fall back to the SPA's built-in default. The
    # `failing` red chip is unaffected (it intentionally stays a
    # uniform error colour regardless of the provider's normal hue).
    provider_color_beszel: Optional[str] = None
    provider_color_pulse: Optional[str] = None
    provider_color_node_exporter: Optional[str] = None
    provider_color_webmin: Optional[str] = None
    provider_color_ping: Optional[str] = None
    provider_color_snmp: Optional[str] = None
    provider_color_http_probe: Optional[str] = None
    provider_color_service_probe: Optional[str] = None
    # Scheduler timezone — IANA name (e.g. "Africa/Cairo"). When set,
    # daily/weekly/monthly schedule anchors are computed in THIS zone
    # instead of the container's localtime. Containers default to UTC;
    # operators in other zones would otherwise see "Daily @ 01:00" fire
    # at the wrong wall-clock moment. Blank = container-local (legacy).
    scheduler_timezone: Optional[str] = None
    # Topbar widgets — lightweight decorative info in the header.
    # ``weather_label`` is what the UI renders alongside the temp
    # ("Cairo"); lat/lon feed Open-Meteo (no API key required). Clock
    # is client-side only — no persistence needed beyond a show/hide.
    weather_label: Optional[str] = None
    weather_lat: Optional[float] = None
    weather_lon: Optional[float] = None
    # Open-Meteo upstream — DEPRECATED in favour of WeatherAPI.com (see
    # `weather_*` fields below). Kept on the model so legacy seed values
    # round-trip cleanly; readers should consult the new keys.
    open_meteo_url: Optional[str] = None
    # ------------------------------------------------------------------
    # Weather — dual-provider dispatch (Open-Meteo or WeatherAPI.com).
    # `weather_provider` selects between "open-meteo" (default — no key,
    # no moon data) and "weatherapi" (requires free key, full moon
    # astronomy). `weather_enabled` is the master toggle. The other
    # `weather_*` fields are SHARED across providers — meaning depends
    # on the active selector. `weather_api_key` follows the
    # secret-suffix + `_set` flag + `clear_weather_api_key` contract;
    # the SPA never receives the raw key, only the `_set` boolean.
    # `weather_default_*` carries the operator-configured fallback
    # coordinates the lifespan sampler + Telegram `/weather` no-arg
    # form + AI palette context use when a per-user location isn't
    # available.
    weather_enabled: Optional[bool] = None
    weather_provider: Optional[str] = None
    weather_api_base_url: Optional[str] = None
    weather_api_key: Optional[str] = None
    clear_weather_api_key: Optional[bool] = None
    weather_default_label: Optional[str] = None
    weather_default_lat: Optional[str] = None
    weather_default_lon: Optional[str] = None
    # Prayer Times (Admin → Prayer Times) — DB-backed like weather.
    # `prayer_times_method` is the AlAdhan calculation-method id (0..23,
    # default 5 = Egyptian); `prayer_times_school` is the Asr school
    # (0 = Standard/Shafi, 1 = Hanafi). `prayer_times_default_*` is the
    # fallback location used when no per-user weather location is set.
    # The master enable toggle is the plain `prayer_times_enabled` setting
    # (like `weather_enabled`); only the cache TTL + fetch timeout are
    # TUNABLES (tuning_prayer_times_*). `prayer_times_api_base_url` is the
    # AlAdhan REST base override (blank = the documented default).
    prayer_times_enabled: Optional[bool] = None
    prayer_times_method: Optional[str] = None
    prayer_times_school: Optional[str] = None
    prayer_times_default_label: Optional[str] = None
    prayer_times_default_lat: Optional[str] = None
    prayer_times_default_lon: Optional[str] = None
    prayer_times_api_base_url: Optional[str] = None
    # Host grouping — JSON array of {name, range_start, range_end, order}
    # that buckets curated hosts into collapsible sections in the Hosts
    # view by their custom_number. Operator-managed under Admin → Hosts.
    host_groups: Optional[list] = None
    # Asset inventory V1 — OAuth2 client_credentials against <asset-api-host>.
    # Secret is write-only (see api_set_settings keep-if-blank rule);
    # admin clears via clear_asset_inventory_client_secret flag.
    asset_inventory_base_url: Optional[str] = None
    asset_inventory_token_url: Optional[str] = None
    asset_inventory_client_id: Optional[str] = None
    asset_inventory_client_secret: Optional[str] = None
    asset_inventory_scope: Optional[str] = None
    clear_asset_inventory_client_secret: Optional[bool] = None
    # / — TLS verification toggle for the asset API.
    # Default True; flip to False for self-signed homelab endpoints.
    asset_inventory_verify_tls: Optional[bool] = None
    # Auth mode selector: "oauth2" (existing client_credentials flow)
    # or "lifetime_token" (static key POSTed to services.php with
    # X-Authorization header). Lifetime key follows the secret suffix
    # + `_set` flag + `clear_*` contract like every other write-only
    # secret (see the project conventions "Secrets in the settings table follow a
    # naming convention").
    asset_inventory_auth_mode: Optional[str] = None
    asset_inventory_lifetime_token: Optional[str] = None
    clear_asset_inventory_lifetime_token: Optional[bool] = None
    # Mandatory `service` and `action` form parameters for the
    # lifetime-token flavour. <asset-api-host>'s services.php routes by these
    # ("service=scheduler&action=run_schedule" is the documented pair
    # for asset fetch). Plain text — these are routing keys, not
    # credentials.
    asset_inventory_service: Optional[str] = None
    asset_inventory_action: Optional[str] = None
    # Range bounds for the `get_assets_custom_number_range` action.
    # String-typed so "" can round-trip as "clear the bound"; field
    # omitted means "don't touch". Pagination kicks in when both are
    # supplied AND the action matches — see
    # logic.asset_inventory.fetch_assets_lifetime_token.
    asset_inventory_min_value: Optional[str] = None
    asset_inventory_max_value: Optional[str] = None
    # Edit-on-upstream URL template used by the host drawer's
    # "Edit on <asset-api-host>" link. Placeholders: {id} (asset DB id),
    # {custom_number} (asset CustomNumber), {base} (the configured
    # base_url). Blank → no link rendered. Operator-configured
    # because <asset-api-host>'s URL scheme isn't part of the API guide.
    asset_inventory_edit_url_template: Optional[str] = None
    # -----------------------------------------------------------------
    # AI integration (Stage 1 foundation — admin surface only). Four
    # supported providers: claude / gemini / chatgpt / deepseek. Each
    # has its own enable / model / base_url / api_key field set. Master
    # `ai_enabled` gates the whole feature; `ai_active_provider` selects
    # which provider any future "use AI" call routes through. API keys
    # follow the keep-current-if-blank contract; the GET response only
    # reports an `api_key_set` boolean, never the material. Stage 2+
    # will introduce the actual call wrapper + writer for `ai_jobs`.
    # -----------------------------------------------------------------
    ai_enabled: Optional[bool] = None
    ai_active_provider: Optional[str] = None
    # Provider fallback chain — opt-in resilience. When `ai_fallback_enabled`
    # is true AND the active provider returns a transient overload (HTTP
    # 429 / 502 / 503 / 504), the call walks `ai_fallback_order` (CSV of
    # provider ids in operator-defined priority) up to the fallback depth
    # tunable. Disabled providers + providers with no API key are skipped at
    # the route layer before the fallback wrapper runs.
    ai_fallback_enabled: Optional[bool] = None
    ai_fallback_order: Optional[str] = None  # CSV, e.g. "claude,chatgpt,deepseek"
    ai_provider_claude_enabled: Optional[bool] = None
    ai_provider_claude_model: Optional[str] = None
    ai_provider_claude_base_url: Optional[str] = None
    ai_provider_claude_api_key: Optional[str] = None
    ai_provider_gemini_enabled: Optional[bool] = None
    ai_provider_gemini_model: Optional[str] = None
    ai_provider_gemini_base_url: Optional[str] = None
    ai_provider_gemini_api_key: Optional[str] = None
    ai_provider_chatgpt_enabled: Optional[bool] = None
    ai_provider_chatgpt_model: Optional[str] = None
    ai_provider_chatgpt_base_url: Optional[str] = None
    ai_provider_chatgpt_api_key: Optional[str] = None
    ai_provider_deepseek_enabled: Optional[bool] = None
    ai_provider_deepseek_model: Optional[str] = None
    ai_provider_deepseek_base_url: Optional[str] = None
    ai_provider_deepseek_api_key: Optional[str] = None
    # -----------------------------------------------------------------
    # SSH console — admin-only remote command runner wired into the
    # host drawer. Global defaults; per-host overrides live in
    # ``hosts_config[].ssh`` (user / port / disabled). Secret fields
    # follow the suffix + ``_set`` flag convention — the browser only
    # learns whether they're set, never the material. See logic/ssh.py.
    # -----------------------------------------------------------------
    ssh_default_user: Optional[str] = None
    ssh_default_port: Optional[int] = None
    ssh_default_private_key: Optional[str] = None
    ssh_default_private_key_passphrase: Optional[str] = None
    # Password auth as an alternative to private key. When both are
    # set, the key wins. Allows operators on hosts that only accept
    # password auth (routers / NAS boxes / vanilla VM images) to still
    # use the SSH console. Write-only on the wire via `_set` flag.
    ssh_default_password: Optional[str] = None
    # FQDN suffix appended to bare hostnames (hosts_config[].id) when
    # SSH resolves the target. Example: id="webserver" +
    # ssh_fqdn_suffix=".example.com" → "webserver.example.com". Host IDs that
    # already contain a dot are used as-is. Blank = no suffix.
    ssh_fqdn_suffix: Optional[str] = None
    ssh_default_known_hosts: Optional[str] = None
    ssh_destructive_patterns: Optional[str] = None
    # Explicit CLEAR flags for SSH secrets. The keep-current-if-blank
    # contract (used by all other secrets) makes it impossible to
    # ERASE a stored secret — blank means "don't change". These bool
    # flags are the escape hatch: when true, the corresponding secret
    # is deleted from the settings table regardless of the paired
    # string field. Admin UI surfaces them as "Clear" buttons.
    clear_ssh_private_key: Optional[bool] = None
    clear_ssh_passphrase: Optional[bool] = None
    clear_ssh_password: Optional[bool] = None
    # Provider-secret clear flags — pair with the existing
    # asset / ssh clear flags so every admin-tab secret input has the
    # same canonical "Clear" affordance. Each flag sets the
    # corresponding settings KV row to "" (empty string), which the
    # respective probe path treats as "no credential configured".
    clear_beszel_password: Optional[bool] = None
    clear_pulse_token: Optional[bool] = None
    clear_webmin_password: Optional[bool] = None
    clear_portainer_api_key: Optional[bool] = None
    clear_oidc_client_secret: Optional[bool] = None
    # JSON array of SSH custom actions. Each element:
    # {"id": "restart-beszel", "title": "Restart Beszel agent",
    #  "command": "systemctl restart beszel-agent"}
    # Empty array or missing = fall back to the hardcoded default
    # action list in the drawer (same 5 presets). {host} placeholder
    # in the command template is substituted at run time.
    ssh_custom_actions: Optional[list] = None
    # Show the host-drawer admin debug panel (raw provider JSON +
    # merged shape). Default ``true`` preserves the legacy behaviour.
    # When false, the panel is hidden for everyone (including admins);
    # other admin tools on the drawer remain visible.
    debug_panel_enabled: Optional[bool] = None
    # -----------------------------------------------------------------
    # Process-level tunables. DB > env > default — see
    # logic/tuning.py:TUNABLES. Every field is Optional[str] so blank
    # ("") clears the override and falls back to the env var; missing
    # = "leave alone". Bounds-checked at write time against TUNABLES.
    # -----------------------------------------------------------------
    tuning_cache_ttl_seconds: Optional[str] = None
    tuning_stats_cache_ttl_seconds: Optional[str] = None
    tuning_registry_concurrency: Optional[str] = None
    tuning_registry_digest_cache_ttl_seconds: Optional[str] = None
    tuning_stats_concurrency: Optional[str] = None
    tuning_stats_targeted_timeout_seconds: Optional[str] = None
    tuning_stats_untargeted_timeout_seconds: Optional[str] = None
    tuning_swarm_agent_unhealthy_threshold: Optional[str] = None
    tuning_swarm_autoheal_cooldown_minutes: Optional[str] = None
    # Swarm autoheal action — `notify` (default; the
    # swarm_agent_health schedule kind only fires the
    # `swarm_agent_unhealthy` notification when the threshold trips)
    # or `restart` (additionally calls do_restart_swarm_agent within
    # the cooldown window). Stored as a settings KV row, not a
    # TUNABLES knob, because it's a categorical choice rather than
    # a numeric range.
    swarm_autoheal_action: Optional[str] = None
    # First-boot auto-bootstrap of a default swarm_agent_health schedule.
    # Default behaviour (unset / "true"): the lifespan boot helper creates
    # one 5-minute schedule when Portainer is configured AND no equivalent
    # row exists yet. Operators who want to opt out flip this to "false"
    # in Admin → Portainer; the bootstrap-done latch
    # (`swarm_autoheal_bootstrap_done`) ensures a deleted-on-purpose row
    # stays deleted across restarts.
    swarm_autoheal_bootstrap_enabled: Optional[str] = None
    tuning_stats_history_days: Optional[str] = None
    tuning_stats_sample_interval_seconds: Optional[str] = None
    # Stats -> Database growth projection knobs. WITHOUT these SettingsIn
    # fields, a POST /api/settings carrying them is silently dropped by
    # Pydantic's extra="ignore" — the green toast lies and the custom
    # value never persists (the documented green-toast-lie drift class).
    tuning_db_size_sample_interval_seconds: Optional[str] = None
    tuning_db_size_history_days: Optional[str] = None
    # Service-probe (8th host-stats provider) knobs — these were rendered
    # in the Config form + dirty-tracked but missing here, so saving them
    # was a silent no-op until this was backfilled.
    tuning_service_probe_sample_interval_seconds: Optional[str] = None
    tuning_service_probe_concurrency: Optional[str] = None
    tuning_service_probe_timeout_seconds: Optional[str] = None
    tuning_service_probe_failure_pause_rounds: Optional[str] = None
    tuning_host_baseline_recompute_interval_seconds: Optional[str] = None
    tuning_host_baseline_first_tick_delay_seconds: Optional[str] = None
    tuning_kick_gather_timeout_seconds: Optional[str] = None
    tuning_portainer_op_timeout_short_seconds: Optional[str] = None
    tuning_portainer_op_timeout_medium_seconds: Optional[str] = None
    tuning_portainer_op_timeout_long_seconds: Optional[str] = None
    tuning_asset_inventory_token_timeout_seconds: Optional[str] = None
    tuning_asset_inventory_fetch_timeout_seconds: Optional[str] = None
    # host_metrics_sampler permanent-fail window. Same DB-key
    # naming + bounds-check via TUNABLES as the others.
    tuning_host_permanent_fail_window_seconds: Optional[str] = None
    # frontend /api/ops poll cadence in SECONDS (was
    # `tuning_ops_poll_interval_ms`; renamed for operator-friendly UI).
    # The SPA reads the effective value (× 1000) via /api/me's
    # `client_config.ops_poll_ms` and uses it as the setTimeout delay
    # between consecutive ops polls.
    tuning_ops_poll_interval_seconds: Optional[str] = None
    # New-version watcher poll cadence (seconds). Drives the SPA's
    # `startVersionWatcher` /api/version poll, delivered via
    # `client_config.version_poll_ms`.
    tuning_version_poll_interval_seconds: Optional[str] = None
    # persistent-log retention in days. Daily files under
    # /app/data/logs/ older than this get deleted by the lifespan
    # _log_pruner_loop().
    tuning_log_retention_days: Optional[str] = None
    # host_failure_events retention window in days. Drives the Stats →
    # Incidents view + Timeline tab + the inline similar-incident
    # grouping. Default 90; set to 0 to disable pruning (legacy "keep
    # every incident forever" behaviour).
    tuning_incidents_retention_days: Optional[str] = None
    # Image-proxy disk cache — TTL (seconds; 0 disables) + max cached entries.
    tuning_image_proxy_cache_ttl_seconds: Optional[str] = None
    tuning_image_proxy_cache_max_entries: Optional[str] = None
    # host-snapshots read-side cache TTL (seconds). The SPA fans
    # out N parallel /api/hosts/one/{id} per refresh; caching the
    # snapshot-table read for a few seconds collapses N reads into 1.
    tuning_host_snapshots_cache_ttl_seconds: Optional[str] = None
    # Per-field "stale grace" cap for the snapshot fallback. Bounds
    # how long a stale field survives in the merged dict / persisted
    # snapshot before being dropped as an orphan. Default 24h.
    tuning_host_snapshot_stale_field_max_age_hours: Optional[str] = None
    # concurrency cap on the SPA's per-host /api/hosts/one/<id>
    # fan-out in `loadHosts()`. Read on /api/me into
    # `me.client_config.hosts_parallel_fetch`.
    tuning_hosts_parallel_fetch: Optional[str] = None
    tuning_hosts_idle_fill_interval_seconds: Optional[str] = None
    # AI Assistant sidebar drawer width (px). Operator-tunable so the
    # same drawer adapts across a 1366 px laptop and a 4K monitor.
    # SPA reads via me.client_config.ai_sidebar_width_px and applies
    # via inline style on the <aside> root.
    tuning_ai_sidebar_width_px: Optional[str] = None
    tuning_ai_conversation_persist_interval_ms: Optional[str] = None
    # AI conversation export — gates the export-to-txt / export-to-json
    # buttons in the AI sidebar header. 0 = hide, 1 = show (default).
    tuning_ai_conversation_export_enabled: Optional[str] = None
    # Port-scan tunables — admin-rendered in Admin → Port Scan, not
    # the generic Config form. Per-port timeout / concurrency
    # supersede the legacy plain-`settings` rows of the same name
    # (those POST keys still accepted for back-compat).
    tuning_port_scan_default_timeout_seconds: Optional[str] = None
    tuning_port_scan_default_concurrency: Optional[str] = None
    tuning_port_scan_max_seconds: Optional[str] = None
    tuning_port_scan_banner_read_seconds: Optional[str] = None
    # host_port_scans retention window (days) — hourly prune sweep cutoff.
    tuning_port_scan_retention_days: Optional[str] = None
    # Port-scan UDP companion (Stage 2).
    tuning_port_scan_udp_default_timeout_seconds: Optional[str] = None
    tuning_port_scan_udp_default_concurrency: Optional[str] = None
    # Scheduled port-scan refresh () — three knobs feed
    # `logic.schedules._run_port_scan_refresh`.
    tuning_port_scan_schedule_max_hosts_per_tick: Optional[str] = None
    tuning_port_scan_schedule_min_age_seconds: Optional[str] = None
    tuning_port_scan_schedule_per_host_concurrency: Optional[str] = None
    # Scheduler wedged-run self-heal threshold (s) — see
    # `logic.schedules._is_previous_run_active`.
    tuning_schedule_stuck_run_threshold_seconds: Optional[str] = None
    # / SSE heartbeat cadence + connection lifetime cap.
    tuning_sse_heartbeat_seconds: Optional[str] = None
    tuning_sse_max_lifetime_seconds: Optional[str] = None
    # Webmin probe outer budget (shared by /api/hosts and
    # /api/hosts/one).
    tuning_webmin_probe_budget_seconds: Optional[str] = None
    # Webmin sampler tick budget — outer wall-clock cap for one
    # `host_webmin_sampler` tick. 0 = auto-derive.
    tuning_webmin_sampler_budget_seconds: Optional[str] = None
    # node-exporter per-host probe timeout (shared by /api/hosts,
    # /api/hosts/one, the debug endpoint, and host_metrics_sampler).
    tuning_node_exporter_probe_timeout_seconds: Optional[str] = None
    # / frontend SSE knobs delivered via /api/me's
    # client_config (× 1000 ms conversion in main.py).
    tuning_sse_idle_threshold_seconds: Optional[str] = None
    tuning_pollops_sse_keepalive_seconds: Optional[str] = None
    tuning_load_busy_max_seconds: Optional[str] = None
    # login rate-limit policy (3 knobs).
    tuning_rate_limit_max_failures: Optional[str] = None
    tuning_rate_limit_window_seconds: Optional[str] = None
    tuning_rate_limit_lockout_seconds: Optional[str] = None
    # / outer host-provider cache + per-host Webmin caches.
    tuning_host_provider_cache_ttl_seconds: Optional[str] = None
    tuning_host_provider_cache_diag_interval: Optional[str] = None
    tuning_stats_per_node_unreachable_ttl_seconds: Optional[str] = None
    tuning_dns_failed_skip_seconds: Optional[str] = None
    tuning_beszel_probe_timeout_unreachable_seconds: Optional[str] = None
    tuning_pulse_probe_timeout_unreachable_seconds: Optional[str] = None
    tuning_slow_query_threshold_ms: Optional[str] = None
    tuning_host_provider_config_cache_ttl_seconds: Optional[str] = None
    tuning_webmin_host_cache_ttl_seconds: Optional[str] = None
    tuning_webmin_host_fail_cache_ttl_seconds: Optional[str] = None
    # host_metrics_sampler per-tick NE probe concurrency.
    tuning_host_metrics_probe_concurrency: Optional[str] = None
    # shared (Webmin + SSH) per-(host, user) auth-failure cool-down.
    tuning_auth_failure_cooldown_seconds: Optional[str] = None
    # Ping host-stats provider knobs.
    tuning_ping_interval_seconds: Optional[str] = None
    tuning_ping_concurrency: Optional[str] = None
    tuning_ping_probe_timeout_seconds: Optional[str] = None
    tuning_ping_cooldown_seconds: Optional[str] = None
    # / SNMP host-stats provider knobs. SettingsIn must list
    # them so the POST /api/settings validator stops Pydantic v2's
    # extra="ignore" default from silently dropping them on save.
    tuning_snmp_probe_timeout_seconds: Optional[str] = None
    tuning_snmp_concurrency: Optional[str] = None
    tuning_snmp_wall_clock_budget_seconds: Optional[str] = None
    tuning_snmp_per_host_walk_concurrency: Optional[str] = None
    # Per-vendor walk_concurrency global defaults — kick in when
    # active_vendors resolves to exactly one vendor AND no per-host
    # override is set AND the vendor's tunable is non-zero.
    tuning_snmp_walk_concurrency_dell: Optional[str] = None
    tuning_snmp_walk_concurrency_cisco: Optional[str] = None
    tuning_snmp_walk_concurrency_synology: Optional[str] = None
    tuning_snmp_walk_concurrency_ucd: Optional[str] = None
    tuning_snmp_walk_concurrency_printer: Optional[str] = None
    # SNMP per-host cache TTLs, distinct from the Webmin pair.
    tuning_snmp_host_cache_ttl_seconds: Optional[str] = None
    tuning_snmp_host_fail_cache_ttl_seconds: Optional[str] = None
    # dedicated SNMP unreachable cool-down (was sharing the
    # auth-failure cool-down with Webmin / SSH).
    tuning_snmp_unreachable_cooldown_seconds: Optional[str] = None
    # SNMP-specific sample interval; 0 = use the global stats
    # interval, > 0 = SNMP probes run on their own cadence.
    tuning_snmp_sample_interval_seconds: Optional[str] = None
    # Per-(provider, host) auto-pause threshold. Counts consecutive
    # failed sampler / probe rounds; flips the (provider, host) row in
    # `host_failure_state` to paused when threshold is met. 0 =
    # disabled. Default 5 ≈ 25 min @ 5-min cadence (Ping default 0
    # because alive=False is the data, not a fault condition).
    tuning_snmp_failure_pause_rounds: Optional[str] = None
    tuning_webmin_failure_pause_rounds: Optional[str] = None
    tuning_beszel_failure_pause_rounds: Optional[str] = None
    tuning_beszel_probe_timeout_seconds: Optional[str] = None
    tuning_beszel_sample_interval_seconds: Optional[str] = None
    tuning_pulse_sample_interval_seconds: Optional[str] = None
    tuning_node_exporter_sample_interval_seconds: Optional[str] = None
    tuning_pulse_failure_pause_rounds: Optional[str] = None
    tuning_pulse_probe_timeout_seconds: Optional[str] = None
    tuning_webmin_probe_timeout_seconds: Optional[str] = None
    tuning_node_exporter_failure_pause_rounds: Optional[str] = None
    tuning_ping_failure_pause_rounds: Optional[str] = None
    # HTTP probe tunables — seventh provider. Operator-tunable per-call
    # wall-clock + sampler concurrency + per-tick cadence + per-(host)
    # auto-pause threshold + DNS sub-probe wall-clock + TLS cert
    # warning days + per-host success / failure cache TTLs.
    tuning_http_probe_timeout_seconds: Optional[str] = None
    tuning_http_probe_concurrency: Optional[str] = None
    tuning_http_probe_sample_interval_seconds: Optional[str] = None
    tuning_http_probe_failure_pause_rounds: Optional[str] = None
    tuning_http_probe_dns_timeout_seconds: Optional[str] = None
    tuning_http_probe_cert_warning_days: Optional[str] = None
    tuning_http_probe_host_cache_ttl_seconds: Optional[str] = None
    tuning_http_probe_host_fail_cache_ttl_seconds: Optional[str] = None
    tuning_http_probe_default_accepted_lo_code: Optional[str] = None
    tuning_http_probe_default_accepted_hi_code: Optional[str] = None
    # stat-bar thresholds (frontend-consumed via /api/me).
    tuning_stat_bar_warn_pct: Optional[str] = None
    tuning_stat_bar_crit_pct: Optional[str] = None
    # Stack-update convergence-poll window — see logic/ops.py:_await_stack_convergence.
    tuning_stack_update_observe_timeout_seconds: Optional[str] = None
    tuning_stack_update_observe_poll_seconds: Optional[str] = None
    # In-app notifications retention window (days). Drives the
    # prune_notifications schedule kind.
    tuning_notification_retention_days: Optional[str] = None
    tuning_notification_page_size: Optional[str] = None
    tuning_notifications_poll_interval_seconds: Optional[str] = None
    # AI provider auto-retry on transient upstream overload (HTTP
    # 429 / 502 / 503 / 504). Rendered in Admin → AI Integration via
    # `relocatedTuningKeys` (NOT the generic Process tunables form).
    tuning_ai_retry_enabled: Optional[str] = None
    tuning_ai_retry_backoff_ms: Optional[str] = None
    tuning_ai_retry_first_attempt_max_ms: Optional[str] = None
    # AI output-token cap + fallback-chain depth (4-step audit-fix
    # promotion from plain `ai_max_tokens` / `ai_fallback_max_depth`
    # plain-settings rows). Both consumed via `tuning_int(...)` so
    # DB > env > default + bounds-clamp applies. Rendered in
    # Admin → AI Integration via `relocatedTuningKeys`.
    tuning_ai_max_tokens: Optional[str] = None
    tuning_ai_fallback_max_depth: Optional[str] = None
    # Public-IP / ISP / ASN lookup module (standalone — NOT
    # AI-related; the AI palette + Telegram /ip command consume it
    # but the feature has its own Admin → Public IP section). The master
    # enable toggle is the plain `public_ip_enabled` setting (like
    # `weather_enabled`), NOT a tunable — default OFF for privacy.
    public_ip_enabled: Optional[bool] = None
    # In-process cache TTL (seconds, default 600).
    tuning_public_ip_cache_ttl_seconds: Optional[str] = None
    # Outbound HTTP wall-clock to ifconfig.co (seconds, default 8).
    tuning_public_ip_fetch_timeout_seconds: Optional[str] = None
    # Background change-sampler cadence (seconds, default 300; 0 = off).
    tuning_public_ip_sample_interval_seconds: Optional[str] = None
    # WeatherAPI.com tunables — in-process cache TTL (default 600s so
    # the public 1M-calls-month free tier stays comfortably under cap),
    # outbound HTTP wall-clock (default 8s), persisted-sample retention
    # (default 90d; 0 disables pruning for "keep every sample forever"
    # deployments), and lifespan-managed sampler cadence (default
    # 3600s; 0 disables the historical-data sampler entirely).
    tuning_weather_cache_ttl_seconds: Optional[str] = None
    tuning_weather_fetch_timeout_seconds: Optional[str] = None
    tuning_weather_history_retention_days: Optional[str] = None
    tuning_weather_sampler_interval_seconds: Optional[str] = None
    # FlareSolverr usage sampler — open-session-count sample cadence (0 =
    # inherit the global stats interval) + retention window for the card's
    # 30-day usage trend.
    tuning_flaresolverr_sample_interval_seconds: Optional[str] = None
    tuning_flaresolverr_history_days: Optional[str] = None
    # RustDesk sampler — online-peers trend + fleet-growth (sample cadence,
    # 0 = inherit global stats interval + retention) + the stale-device window.
    tuning_rustdesk_sample_interval_seconds: Optional[str] = None
    tuning_rustdesk_history_days: Optional[str] = None
    tuning_rustdesk_stale_days: Optional[str] = None
    # Rundeck sampler — recent-execution failure-rate trend (sample cadence,
    # 0 = inherit global stats interval + retention).
    tuning_rundeck_sample_interval_seconds: Optional[str] = None
    tuning_rundeck_history_days: Optional[str] = None
    # ddns-updater sampler — public-IP-change timeline + failing-count
    # sparkline (sample cadence, 0 = inherit global stats interval + retention).
    tuning_ddns_sample_interval_seconds: Optional[str] = None
    tuning_ddns_history_days: Optional[str] = None
    tuning_ddns_stale_record_hours: Optional[str] = None
    # Fing network-occupancy sampler — online-device trend (sample cadence,
    # 0 = inherit global stats interval + retention + the new-device window).
    tuning_fing_sample_interval_seconds: Optional[str] = None
    tuning_fing_history_days: Optional[str] = None
    tuning_fing_new_device_hours: Optional[str] = None
    # Speedtest Tracker long-horizon sampler — ingest cadence (0 = inherit
    # global stats interval) + retention for the independent trend that
    # survives the upstream's own pruning.
    tuning_speedtest_sample_interval_seconds: Optional[str] = None
    tuning_speedtest_history_days: Optional[str] = None
    # AdGuard Home blocked-% history sampler — snapshot cadence (0 = inherit
    # global stats interval) + retention for the fleet blocked-% trend.
    tuning_adguard_sample_interval_seconds: Optional[str] = None
    tuning_adguard_history_days: Optional[str] = None
    tuning_adguardsync_sample_interval_seconds: Optional[str] = None
    tuning_adguardsync_history_days: Optional[str] = None
    # Pi-hole blocked-% history sampler — snapshot cadence (0 = inherit global
    # stats interval) + retention for the fleet cross-restart blocked-% trend.
    tuning_pihole_sample_interval_seconds: Optional[str] = None
    tuning_pihole_history_days: Optional[str] = None
    # Seerr request-backlog sampler — snapshot cadence (0 = inherit global
    # stats interval) + retention for the pending-backlog trend.
    tuning_seerr_sample_interval_seconds: Optional[str] = None
    tuning_seerr_history_days: Optional[str] = None
    # Shared Servarr-family (Radarr / Sonarr / Lidarr / Readarr) retention
    # sampler — snapshot cadence (0 = inherit global stats interval) + retention
    # for the library-growth + missing-backlog + disk-runway trend.
    tuning_servarr_sample_interval_seconds: Optional[str] = None
    tuning_servarr_history_days: Optional[str] = None
    # qBittorrent transfer-speed + free-disk retention sampler — snapshot cadence
    # (0 = inherit global stats interval) + retention for the speed sparkline +
    # disk-free-runway projection.
    tuning_qbittorrent_sample_interval_seconds: Optional[str] = None
    tuning_qbittorrent_history_days: Optional[str] = None
    # UniFi client-occupancy sampler — snapshot cadence (0 = inherit global stats
    # interval) + retention for the "clients over time" sparkline.
    tuning_unifi_sample_interval_seconds: Optional[str] = None
    tuning_unifi_history_days: Optional[str] = None
    # Proxmox cluster-resource sampler — snapshot cadence (0 = inherit global
    # stats interval) + retention for the cluster CPU/mem/storage trend.
    tuning_proxmox_sample_interval_seconds: Optional[str] = None
    tuning_proxmox_history_days: Optional[str] = None
    # Direct-Docker node (Portainer-less, over SSH) per-call wall-clock budget.
    tuning_docker_direct_timeout_seconds: Optional[str] = None
    # Bazarr subtitle-backlog sampler — snapshot cadence (0 = inherit global
    # stats interval) + retention for the backlog-over-time sparkline.
    tuning_bazarr_sample_interval_seconds: Optional[str] = None
    tuning_bazarr_history_days: Optional[str] = None
    # Plex concurrent-stream sampler — snapshot cadence (0 = inherit global stats
    # interval) + retention for the streams-over-time sparkline.
    tuning_plex_sample_interval_seconds: Optional[str] = None
    tuning_plex_history_days: Optional[str] = None
    # Tautulli concurrent-stream sampler — snapshot cadence (0 = inherit global
    # stats interval) + retention for the streams-over-time sparkline.
    tuning_tautulli_sample_interval_seconds: Optional[str] = None
    tuning_tautulli_history_days: Optional[str] = None
    tuning_tracearr_sample_interval_seconds: Optional[str] = None
    tuning_tracearr_history_days: Optional[str] = None
    # Tdarr retention sampler — cumulative space-saved + queue burn-down + per-day
    # throughput.
    tuning_tdarr_sample_interval_seconds: Optional[str] = None
    tuning_tdarr_history_days: Optional[str] = None
    # Emby / Jellyfin streaming retention sampler (shared by both brands).
    tuning_emby_sample_interval_seconds: Optional[str] = None
    tuning_emby_history_days: Optional[str] = None
    # Forgejo review-queue retention sampler.
    tuning_forgejo_sample_interval_seconds: Optional[str] = None
    tuning_forgejo_history_days: Optional[str] = None
    # GitSync Connector retention sampler + stale-pair threshold.
    tuning_gitsync_sample_interval_seconds: Optional[str] = None
    tuning_gitsync_history_days: Optional[str] = None
    tuning_gitsync_stale_pair_hours: Optional[str] = None
    # Grafana meta-monitor retention sampler.
    tuning_grafana_sample_interval_seconds: Optional[str] = None
    tuning_grafana_history_days: Optional[str] = None
    # Nginx Proxy Manager config-drift retention sampler.
    tuning_npm_sample_interval_seconds: Optional[str] = None
    tuning_npm_history_days: Optional[str] = None
    # OPNsense interface-throughput retention sampler.
    tuning_opnsense_sample_interval_seconds: Optional[str] = None
    tuning_opnsense_history_days: Optional[str] = None
    # Kavita library-growth retention sampler.
    tuning_kavita_sample_interval_seconds: Optional[str] = None
    tuning_kavita_history_days: Optional[str] = None
    # Prowlarr counter-rate retention sampler.
    tuning_prowlarr_sample_interval_seconds: Optional[str] = None
    tuning_prowlarr_history_days: Optional[str] = None
    # Favicon proxy (bookmark / app tile icon fallback) — disk-cache TTL +
    # per-fetch wall-clock.
    tuning_favicon_cache_days: Optional[str] = None
    tuning_favicon_fetch_timeout_seconds: Optional[str] = None
    # AI log context — how many hours back to read persistent logs
    # for the palette's user-prompt context, capped at N lines.
    tuning_ai_log_context_hours: Optional[str] = None
    tuning_ai_log_context_lines: Optional[str] = None
    # AI provider outbound HTTP wall-clocks — Test-connection probe
    # (lightweight one-token ping, 15s default) + real chat-completion
    # call (30s default). Per-use reads inside `logic.ai.test_provider`
    # / `ask_provider`. Rendered in Admin → AI Integration via
    # `relocatedTuningKeys` (NOT the generic Process tunables form).
    tuning_ai_http_timeout_seconds: Optional[str] = None
    tuning_ai_extended_http_timeout_seconds: Optional[str] = None
    # Backup retention count + SSH WebSocket heartbeat — same
    # 4-step audit-fix promotion; consumed via `tuning_int(...)`;
    # rendered in Admin → Backups and Admin → SSH respectively.
    tuning_backup_retention_count: Optional[str] = None
    # Threshold (seconds) before the SPA's top-of-page "backend unreachable"
    # banner appears after the last successful backend signal (SSE event or
    # REST 2xx). 0 disables the banner entirely.
    tuning_backend_unreachable_threshold_seconds: Optional[str] = None
    # Per-app extras (Speedtest / APC) freshness TTL — SPA stale-while-
    # revalidate window for the expanded-card /app-data cache (0 = fetch-once).
    tuning_apps_extras_ttl_seconds: Optional[str] = None
    # APC UPS card battery/load/runtime sparkline display window (days).
    tuning_apc_history_days: Optional[str] = None
    # Per-app route wall-clock budget — app-data fetch + skill dispatch each
    # fail with OmniGrid's own logged 504 under the reverse-proxy timeout.
    tuning_apps_route_budget_seconds: Optional[str] = None
    tuning_apps_tile_render_batch: Optional[str] = None
    # Settings-as-Code (config_backup schedule kind) snapshot retention.
    tuning_config_backup_retention_count: Optional[str] = None
    tuning_ssh_ws_heartbeat_seconds: Optional[str] = None
    # Per-provider default ports — promoted out of the plain settings
    # table per the project's "Plain-`settings`-row escape hatch" rule.
    # Bounds 1..65535 (TCP / UDP port range) enforced by TUNABLES.
    tuning_ssh_default_port: Optional[str] = None
    tuning_snmp_default_port: Optional[str] = None
    tuning_ping_default_port: Optional[str] = None
    # ICMP inter-packet spacing (ms) — operator-tunable so commercial
    # firewall anti-flood rules don't reject the burst. Consumed in
    # `logic/ping.py:_icmp_ping`.
    tuning_ping_packet_interval_ms: Optional[str] = None
    # SSH terminal entrypoint wall-clocks.
    tuning_ssh_terminal_connect_timeout_seconds: Optional[str] = None
    tuning_ssh_terminal_login_timeout_seconds: Optional[str] = None
    # SSH terminal connection-close wait timeout — caps how long
    # `conn.wait_closed()` blocks after a terminal session ends.
    # Rendered in Admin → SSH via `relocatedTuningKeys`.
    tuning_ssh_close_timeout_seconds: Optional[str] = None
    # OIDC outbound HTTP wall-clock — covers discovery / JWKS / token
    # exchange / Test-connection probe. Per-use reads inside oidc.py
    # at every call site. Rendered in Admin → Authentik OIDC via
    # `relocatedTuningKeys` (NOT the generic Process tunables form).
    tuning_oidc_http_timeout_seconds: Optional[str] = None
    # Gather fan-out HTTP wall-clock + orphan-probe per-call timeout —
    # both consumed inside `logic/gather.py`. Rendered in Admin →
    # Portainer alongside the existing op-timeout tiers via
    # `relocatedTuningKeys` (NOT the generic Process tunables form).
    tuning_gather_client_timeout_seconds: Optional[str] = None
    tuning_gather_orphan_probe_timeout_seconds: Optional[str] = None
    # -----------------------------------------------------------------
    # Per-event notification toggles. Each maps to one of the
    # 12 (event group × success/failure) notify() call sites in
    # logic/ops.py; gated inside notify() via the event= kwarg. Default
    # behaviour is "send" so existing deploys keep all notifications on.
    # Stored as "true"/"false" strings; "" clears (read-side falls back
    # to the default-true). The /api/notify-test endpoint always sends
    # regardless of these toggles.
    # -----------------------------------------------------------------
    notify_event_stack_update_success: Optional[str] = None
    notify_event_stack_update_failure: Optional[str] = None
    notify_event_container_update_success: Optional[str] = None
    notify_event_container_update_failure: Optional[str] = None
    notify_event_container_restart_success: Optional[str] = None
    notify_event_container_restart_failure: Optional[str] = None
    notify_event_container_remove_success: Optional[str] = None
    notify_event_container_remove_failure: Optional[str] = None
    notify_event_service_restart_success: Optional[str] = None
    notify_event_service_restart_failure: Optional[str] = None
    # Swarm autoheal — restart success / failure / unhealthy detection.
    # The first two are fired by `do_restart_swarm_agent` directly;
    # the third is fired by the `swarm_agent_health` schedule kind
    # in notify-only mode. All three default ON.
    notify_event_swarm_agent_restart_success: Optional[str] = None
    notify_event_swarm_agent_restart_failure: Optional[str] = None
    notify_event_swarm_agent_unhealthy: Optional[str] = None
    notify_event_swarm_agent_recovered: Optional[str] = None
    notify_event_prune_success: Optional[str] = None
    notify_event_prune_failure: Optional[str] = None
    # Security event — defaults to OFF (login traffic is noisy).
    notify_event_user_login: Optional[str] = None
    # System event — fires when host_metrics_sampler auto-pauses
    # a host after the configured failure window. Default ON.
    notify_event_host_paused: Optional[str] = None
    # Port-scan provider — fires when a scan reveals a new open port
    # not in the previous scan AND not in the host's curated services.
    # Defaults OFF (NOTIFY_EVENT_DEFAULTS) so a freshly-enabled scanner
    # doesn't flood the operator with first-run notifications.
    notify_event_port_scan_new_port: Optional[str] = None
    # HTTP probe — fires on the healthy → failing transition for a
    # per-host HTTP / TLS / DNS probe. Defaults OFF (NOTIFY_EVENT_DEFAULTS)
    # so a freshly-enabled probe doesn't flood the operator with first-
    # run failures on hosts that happen to be intentionally down.
    notify_event_http_probe_failure: Optional[str] = None
    # Per-service reachability probe — fires on healthy → failing
    # transitions for service-level chips. Defaults OFF.
    notify_event_service_probe_failure: Optional[str] = None
    # TOTP audit-row INSERT failure — fires WARNING when the credential
    # change persisted but the audit row didn't (operator sees missing
    # History trail otherwise).
    notify_event_totp_audit_log_failed: Optional[str] = None
    # Drawer auto-fix — Portainer-API VXLAN overlay cleanup events.
    notify_event_overlay_cleanup_success: Optional[str] = None
    notify_event_overlay_cleanup_failure: Optional[str] = None
    notify_event_prayer_reminder: Optional[str] = None
    # -----------------------------------------------------------------
    # Per-medium master switches. The dispatcher in `logic/ops.py:notify`
    # fans out to every enabled medium; flipping one of these false
    # silences that channel WITHOUT disabling the event entirely. Both
    # default true for back-compat. Stored as "true" / "false" strings
    # alongside notify_event_* so they share the same hydration drift
    # audit (the project conventions "Settings hydration drift class").
    # -----------------------------------------------------------------
    notify_medium_app: Optional[str] = None
    notify_medium_apprise: Optional[str] = None
    # -----------------------------------------------------------------
    # TOTP / 2FA policies. Master toggle plus role-scoped
    # required-flags plus lockout knobs. Authentik users are excluded
    # from every TOTP path -- their IdP handles MFA.
    # -----------------------------------------------------------------
    totp_allowed: Optional[bool] = None
    totp_required_for_admins: Optional[bool] = None
    totp_required_for_users: Optional[bool] = None
    totp_lockout_max_failures: Optional[int] = None
    totp_lockout_minutes: Optional[int] = None
    # Passkey master toggle. Mirrors totp_allowed.
    passkeys_allowed: Optional[bool] = None


# noinspection DuplicatedCode
def __getattr__(name):
    """Module-level resolver for cross-module underscore-prefixed leaks.
    Delegates to the shared helper so the 33-line PEP 562 implementation
    lives in one place. See main_pkg._resolver for the full rationale.
    The 5-line delegator IS duplicated across 12 files — PEP 562 requires
    one __getattr__ per module; suppress the duplicated-code hint."""
    # noinspection PyProtectedMember
    from main_pkg._resolver import resolve
    return resolve(__name__, name)
