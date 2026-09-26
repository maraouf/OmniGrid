"""Run a Swarm service on an image that is already on its node.

The case: a Tracearr update whose new ~400 MB image sat downloading for hours.
"Roll back to previous version" couldn't help, because Swarm keeps ONE previous
spec, and after the update and a restart that spec already pointed at the new
image. The older images it had run were still on the node. These tests pin
that OmniGrid can list them, run one by digest WITHOUT downloading anything,
refuses when the image isn't actually there, and keeps the new version
downloading in the background when asked — as its own op, under its own
longer limit, without blocking a restart of the same service.
"""
from __future__ import annotations

import contextlib
import json

import httpx
import pytest

# `main` first, as at app start (see tests/test_image_prepull.py).
import main  # noqa: F401,E402
from logic import image_pull, ops_extras, portainer
from logic.ops import Operation

BASE = "https://portainer.test"
NODE = "debian13docker"
REPO = "ghcr.io/connorgallopo/tracearr"
NEW = "sha256:" + "4" * 64     # what the update pointed at — still downloading
OLD = "sha256:" + "f" * 64     # ran until the update; still on the node
OLDER = "sha256:" + "8" * 64   # ran two days ago; still on the node
GONE = "sha256:" + "0" * 64    # ran once; pruned since


@pytest.fixture(autouse=True)
def _portainer_stub(monkeypatch):
    monkeypatch.setattr(portainer, "_url", lambda: BASE)
    monkeypatch.setattr(portainer, "_endpoint_id", lambda: 1)
    monkeypatch.setattr(portainer, "headers", lambda agent_target=None: (
        {"X-PortainerAgent-Target": agent_target} if agent_target else {}))
    monkeypatch.setattr(portainer, "ensure_reachable", lambda: None)


def _fake_portainer(*, calls, running_digest=OLD):
    """One Swarm service, `tracearr_tracearr` (svc1), on NODE.

    Its spec points at NEW (the stuck update); its task history shows the
    digests it ran; the node holds OLD and OLDER but not GONE. After a spec
    update, the running task reports ``running_digest``.
    """
    state = {"updated_to": None}

    def task(digest, st, ts, desired="shutdown"):
        return {"NodeID": "n1", "DesiredState": desired, "Status": {"State": st, "Timestamp": ts},
                "Spec": {"ContainerSpec": {"Image": f"{REPO}:supervised@{digest}"}}}

    async def handler(req: httpx.Request):
        path, q = req.url.path, dict(req.url.params)
        calls.append((req.method, path))
        if req.method == "GET" and path.endswith("/docker/services/svc1"):
            image = state["updated_to"] or f"{REPO}:supervised@{NEW}"
            return httpx.Response(200, json={
                "ID": "svc1", "Version": {"Index": 42},
                "Spec": {"Name": "tracearr_tracearr",
                         "TaskTemplate": {"ContainerSpec": {"Image": image}}}})
        if req.method == "GET" and path.endswith("/docker/tasks"):
            f = json.loads(q.get("filters") or "{}")
            if "desired-state" in f:
                if state["updated_to"]:
                    return httpx.Response(200, json=[task(running_digest, "running",
                                                          "2026-09-26T23:00:00Z", "running")])
                return httpx.Response(200, json=[task(NEW, "preparing", "2026-09-26T20:40:30Z", "running")])
            return httpx.Response(200, json=[
                task(NEW, "preparing", "2026-09-26T20:40:30Z", "running"),
                task(OLD, "shutdown", "2026-09-26T20:40:25Z"),
                task(OLDER, "shutdown", "2026-09-24T21:00:00Z"),
                task(GONE, "shutdown", "2026-09-20T09:00:00Z"),
            ])
        if req.method == "GET" and path.endswith("/docker/nodes"):
            return httpx.Response(200, json=[{"ID": "n1", "Description": {"Hostname": NODE}}])
        if req.method == "GET" and path.endswith("/docker/images/json"):
            return httpx.Response(200, json=[
                {"Id": "a", "RepoDigests": [f"{REPO}@{OLD}"], "RepoTags": [f"{REPO}:supervised"],
                 "Created": 1790000000, "Size": 400_000_000},
                {"Id": "b", "RepoDigests": [f"{REPO}@{OLDER}"], "RepoTags": [],
                 "Created": 1789800000, "Size": 390_000_000},
                {"Id": "c", "RepoDigests": ["some/other@sha256:" + "1" * 64], "RepoTags": [],
                 "Created": 1790000001, "Size": 1},
            ])
        if req.method == "POST" and path.endswith("/docker/services/svc1/update"):
            state["updated_to"] = json.loads(req.content)["TaskTemplate"]["ContainerSpec"]["Image"]
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"message": f"unexpected {req.method} {path}"})

    return handler, state


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_the_picker_lists_what_is_on_the_node_and_when_it_last_ran():
    calls: list = []
    handler, _ = _fake_portainer(calls=calls)
    async with _client(handler) as client:
        info = await ops_extras.service_image_candidates(client, "svc1")
    assert info["repository"] == REPO and info["tag"] == "supervised"
    assert info["current_digest"] == NEW and info["nodes"] == [NODE]
    digests = [c["digest"] for c in info["candidates"]]
    assert digests == [OLD, OLDER]                    # newest first; GONE and other repos absent
    old = info["candidates"][0]
    assert old["last_ran"] == "2026-09-26T20:40:25Z"  # from the service's own task history
    assert old["on_all_nodes"] and not old["current"] and old["tags"] == ["supervised"]


@pytest.fixture
def run_op(monkeypatch):
    monkeypatch.setattr(ops_extras, "_portainer_op_timeout", lambda tier: 10.0)
    monkeypatch.setattr(ops_extras, "_tuning_int", lambda key: (
        0.3 if key == ops_extras._Tunable.STACK_UPDATE_OBSERVE_TIMEOUT_SECONDS else 0.01))

    async def _no_notify(*a, **kw):
        return None

    monkeypatch.setattr(ops_extras, "notify", _no_notify)
    monkeypatch.setattr(ops_extras, "persist_history", lambda op: None)
    monkeypatch.setattr(ops_extras.gather, "invalidate_cache", lambda: None)
    spawned: list = []

    def _spawn(coro, *, label=""):
        spawned.append(label)
        coro.close()                                   # don't run it; record that it was asked for

    monkeypatch.setattr(main, "spawn_background_task", _spawn)

    async def _run(handler, digest, **kw) -> Operation:
        @contextlib.asynccontextmanager
        async def _write_client(timeout=60.0):
            async with _client(handler) as c:
                yield c

        monkeypatch.setattr(portainer, "write_client", _write_client)
        op = Operation("rollback_service", "svc1", "tracearr_tracearr", actor="tester")
        await ops_extras.do_rollback_to_image(op, "svc1", digest, **kw)
        return op

    _run.spawned = spawned
    return _run


@pytest.mark.anyio
async def test_rolling_back_runs_the_local_digest_without_downloading_anything(run_op):
    calls: list = []
    handler, state = _fake_portainer(calls=calls)
    op = await run_op(handler, OLD, background_fetch=False)
    assert op.status == "success", op.error
    assert state["updated_to"] == f"{REPO}:supervised@{OLD}"   # tag kept, digest pinned
    assert not any(p.endswith(("/images/create", "/services/create")) for _, p in calls)
    assert run_op.spawned == []                                 # no background fetch asked


@pytest.mark.anyio
async def test_the_new_version_keeps_downloading_in_the_background_when_asked(run_op, monkeypatch):
    from logic import ops as ops_mod
    monkeypatch.setattr(ops_mod, "ops", {})
    monkeypatch.setattr(ops_mod, "ops_order", [])
    calls: list = []
    handler, _ = _fake_portainer(calls=calls)
    op = await run_op(handler, OLD, background_fetch=True)
    assert op.status == "success", op.error
    assert run_op.spawned == ["prefetch-svc1"]
    child = [o for o in ops_mod.ops.values() if o.op_type == "prefetch_image"]
    assert len(child) == 1 and child[0].target_id == "svc1"


@pytest.mark.anyio
async def test_an_image_no_longer_on_the_node_is_refused_and_nothing_changes(run_op):
    calls: list = []
    handler, state = _fake_portainer(calls=calls)
    op = await run_op(handler, GONE, background_fetch=False)
    assert op.status == "error"
    assert "isn't on debian13docker any more" in (op.error or "")
    assert state["updated_to"] is None                          # the service was never touched


@pytest.mark.anyio
async def test_a_swap_that_never_comes_up_is_a_failure(run_op):
    calls: list = []
    handler, _ = _fake_portainer(calls=calls, running_digest=OLDER)  # Swarm runs something else
    op = await run_op(handler, OLD, background_fetch=False)
    assert op.status == "error"
    assert "didn't come up on the earlier image" in (op.error or "")


@pytest.mark.anyio
async def test_the_background_download_uses_its_own_longer_limit(monkeypatch):
    seen = {}

    async def fake_ensure(_client, _op, targets, *, strict, keep_digest=False, budget_s=None):
        seen.update(targets=targets, strict=strict, budget_s=budget_s)

    monkeypatch.setattr(image_pull, "ensure_images_on_nodes", fake_ensure)
    import logic.tuning as tuning_mod
    monkeypatch.setattr(tuning_mod, "tuning_int", lambda key: (
        21600 if key == tuning_mod.Tunable.BACKGROUND_PREFETCH_TIMEOUT_SECONDS else 1800))

    async def _no_notify(*a, **kw):
        return None

    monkeypatch.setattr(ops_extras, "notify", _no_notify)
    monkeypatch.setattr(ops_extras, "persist_history", lambda _o: None)
    monkeypatch.setattr(ops_extras, "_portainer_op_timeout", lambda tier: 10.0)

    @contextlib.asynccontextmanager
    async def _write_client(timeout=60.0):
        async with _client(lambda r: httpx.Response(404)) as c:
            yield c

    monkeypatch.setattr(portainer, "write_client", _write_client)
    op = Operation("prefetch_image", "svc1", "tracearr_tracearr")
    await ops_extras.do_prefetch_image(op, "svc1", f"{REPO}:supervised", [NODE])
    assert op.status == "success", op.error
    assert seen == {"targets": [(NODE, f"{REPO}:supervised")], "strict": True, "budget_s": 21600.0}


def test_a_background_download_never_blocks_a_restart_of_the_same_service(monkeypatch):
    from logic import ops as ops_mod
    monkeypatch.setattr(ops_mod, "ops", {})
    monkeypatch.setattr(ops_mod, "ops_order", [])
    ops_mod.new_op("prefetch_image", "svc1", "tracearr_tracearr")
    ops_mod.new_op("restart_service", "svc1", "tracearr_tracearr")     # allowed
    with pytest.raises(ops_mod.OpConflict):
        ops_mod.new_op("prefetch_image", "svc1", "tracearr_tracearr")  # but not a second download


@pytest.mark.anyio
async def test_the_sweep_spares_a_job_whose_op_is_still_running(monkeypatch):
    """A background download legitimately runs for hours; an age cut-off
    would kill it whenever any other fetch started. Only orphans go."""
    from logic import ops as ops_mod
    monkeypatch.setattr(ops_mod, "ops", {})
    monkeypatch.setattr(ops_mod, "ops_order", [])
    live = ops_mod.new_op("prefetch_image", "svc1", "tracearr_tracearr")
    deleted: list = []

    async def handler(req: httpx.Request):
        if req.method == "GET":
            return httpx.Response(200, json=[
                {"ID": "j-live", "Spec": {"Labels": {"omnigrid.prefetch": "1",
                                                     "omnigrid.prefetch.op": live.id}}},
                {"ID": "j-orphan", "Spec": {"Labels": {"omnigrid.prefetch": "1",
                                                       "omnigrid.prefetch.op": "gone00000000"}}},
            ])
        deleted.append(req.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200)

    async with _client(handler) as client:
        await image_pull._sweep_stale_jobs(client)
    assert deleted == ["j-orphan"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
