"""Nothing running is stopped until every image it needs is on the node.

The case that prompted this: a Tracearr stack update. Swarm's default order is
stop-first, so the working task was stopped immediately and its replacement
then sat in `preparing` for 25+ minutes pulling ~400 MB from ghcr.io over a
slow link — while OmniGrid reported the update as a SUCCESS, because the
convergence wait timed out, logged a warning, and returned normally.

These tests drive the real code against a fake Portainer and pin:
  * the stack update fetches images BEFORE it asks Portainer to redeploy;
  * a fetch that times out or fails means the redeploy is never sent;
  * a pull failure inside Docker's HTTP-200 stream is caught (checking the
    status alone, as every call site used to, treats it as success);
  * a rollout that never finishes is reported as a failure, naming the
    service, instead of a success;
  * a rollback fetches the image it is rolling back TO by digest — pulling
    its tag would fetch the image being rolled away from.
"""
from __future__ import annotations

import asyncio
import time
import contextlib
import json

import httpx
import pytest

# `main` first, as at app start: it wires the logic.ops <-> ops_extras
# re-exports, which a bare `import logic.ops_extras` leaves half-done.
import main  # noqa: F401,E402
from logic import image_pull, ops_extras, portainer
from logic.ops import Operation

BASE = "https://portainer.test"
EP = f"{BASE}/api/endpoints/1/docker"
IMAGE = "ghcr.io/connorgallopo/tracearr:supervised"
NODE = "debian13docker"

PULL_OK = "\n".join(json.dumps(e) for e in [
    {"status": "Pulling from connorgallopo/tracearr", "id": "supervised"},
    {"status": "Pulling fs layer", "id": "aaa"},
    {"status": "Pulling fs layer", "id": "bbb"},
    {"status": "Already exists", "id": "aaa"},
    {"status": "Pull complete", "id": "bbb"},
    {"status": "Digest: sha256:4006ae55e583"},
    {"status": "Status: Downloaded newer image for " + IMAGE},
])
# Docker's pull failure: HTTP 200, with the error as the LAST stream line.
PULL_FAILS_IN_STREAM = "\n".join(json.dumps(e) for e in [
    {"status": "Pulling fs layer", "id": "aaa"},
    {"errorDetail": {"message": "manifest for x:nope not found"},
     "error": "manifest for x:nope not found"},
])
PULL_AUTH = json.dumps({"errorDetail": {"message": "unauthorized: authentication required"},
                        "error": "unauthorized: authentication required"})


class _Op:
    """Just enough of an Operation for the helper — it logs and reports phase."""

    def __init__(self):
        self.id = "op0test"
        self.lines: list[tuple[str, str]] = []
        self.phases: list[tuple] = []

    def log(self, msg, level="info"):
        self.lines.append((level, msg))

    def set_phase(self, phase, detail="", *, done=None, total=None):
        self.phases.append((phase, detail, done, total))


@pytest.fixture(autouse=True)
def _portainer_stub(monkeypatch):
    # PORTAINER_URL / _ENDPOINT_ID are served by a module __getattr__ that
    # reads the settings table; stub the functions behind it, not the names.
    monkeypatch.setattr(portainer, "_url", lambda: BASE)
    monkeypatch.setattr(portainer, "_endpoint_id", lambda: 1)
    monkeypatch.setattr(portainer, "headers", lambda agent_target=None: (
        {"X-PortainerAgent-Target": agent_target} if agent_target else {}))
    monkeypatch.setattr(portainer, "ensure_reachable", lambda: None)
    monkeypatch.setattr(image_pull, "_budget_seconds", lambda: 5.0)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- the pure parts -----------------------------------------------------------

@pytest.mark.parametrize("ref,expected", [
    (IMAGE, ("ghcr.io/connorgallopo/tracearr", "supervised")),
    ("nginx", ("nginx", "latest")),
    ("registry:5000/team/app", ("registry:5000/team/app", "latest")),      # port ≠ tag
    ("registry:5000/team/app:1.2", ("registry:5000/team/app", "1.2")),
    (IMAGE + "@sha256:abc", ("ghcr.io/connorgallopo/tracearr", "supervised")),  # by tag
])
def test_split_ref_pulls_by_tag(ref, expected):
    assert image_pull.split_ref(ref) == expected


def test_split_ref_keeps_the_digest_for_a_rollback():
    assert image_pull.split_ref(IMAGE + "@sha256:abc", keep_digest=True) == (
        "ghcr.io/connorgallopo/tracearr", "sha256:abc")


def test_stream_error_reads_the_failure_docker_hides_in_a_200():
    assert image_pull.stream_error(PULL_OK) is None
    assert image_pull.stream_error(PULL_FAILS_IN_STREAM) == "manifest for x:nope not found"


def test_auth_classification_does_not_fire_on_a_digest_containing_401():
    assert image_pull.is_auth_error("unauthorized: authentication required")
    assert not image_pull.is_auth_error("manifest sha256:401abc not found")


# --- the helper's policy ------------------------------------------------------

def _standalone(handler):
    """Wrap a pull handler as a NON-Swarm endpoint, so the streamed pull is
    the path under test. `/info` answers instantly — a slow pull handler must
    not also slow the "which method?" check."""
    async def wrapped(req: httpx.Request):
        if req.url.path.endswith("/docker/info"):
            return httpx.Response(200, json={"Swarm": {"LocalNodeState": "inactive"}})
        out = handler(req)
        return await out if asyncio.iscoroutine(out) else out
    return wrapped


async def _fetch(handler, *, strict, targets=((NODE, IMAGE),)):
    op = _Op()
    async with _client(_standalone(handler)) as client:
        await image_pull.ensure_images_on_nodes(client, op, list(targets), strict=strict)
    return op


@pytest.mark.anyio
async def test_a_clean_pull_is_routed_to_the_task_node():
    seen = {}

    def handler(req: httpx.Request):
        seen["node"] = req.headers.get("X-PortainerAgent-Target")
        seen["query"] = dict(req.url.params)
        return httpx.Response(200, text=PULL_OK)

    op = await _fetch(handler, strict=True)
    assert seen["node"] == NODE
    assert seen["query"] == {"fromImage": "ghcr.io/connorgallopo/tracearr", "tag": "supervised"}
    assert any("Fetched" in m for _, m in op.lines)


@pytest.mark.anyio
async def test_the_row_can_see_download_progress_while_nothing_is_stopped():
    """The fetch can take many minutes before anything is swapped. The op
    reports a `fetching` phase with live layer counts so the row can say
    "downloading image 1/2" instead of looking hung."""
    op = await _fetch(lambda r: httpx.Response(200, text=PULL_OK), strict=True)
    fetching = [p for p in op.phases if p[0] == "fetching"]
    assert fetching, "no fetching phase reported"
    assert fetching[0][1] == "ghcr.io/connorgallopo/tracearr:supervised"
    counted = [(d, t) for _, _, d, t in fetching if t]
    assert counted[-1] == (2, 2)                      # finished: both layers ready
    assert len(counted) == len(set(counted))          # published only when a count moved


def test_phase_is_part_of_the_op_every_tab_reads_and_clears_when_done():
    op = Operation("update_stack", "7", "tracearr", actor="tester")
    op.set_phase("fetching", IMAGE, done=3, total=30)
    d = op.to_dict()
    assert (d["phase"], d["phase_detail"], d["progress"]) == ("fetching", IMAGE, {"done": 3, "total": 30})
    op.done("success")
    assert op.to_dict()["phase"] == "" and op.to_dict()["progress"] is None


def test_an_unknown_phase_fails_loudly_rather_than_render_a_raw_key():
    with pytest.raises(ValueError):
        Operation("update_stack", "7", "tracearr").set_phase("downloading")


def test_every_phase_has_a_translation():
    import pathlib
    from logic.ops import OP_PHASES
    en = json.loads((pathlib.Path(__file__).resolve().parents[1] / "static/i18n/en.json")
                    .read_text(encoding="utf-8"))
    phases = en["ops_status"]["phase"]
    assert OP_PHASES <= set(phases), OP_PHASES - set(phases)


@pytest.mark.anyio
@pytest.mark.parametrize("strict", [True, False])
async def test_a_failure_inside_the_200_stream_stops_everything(strict):
    with pytest.raises(RuntimeError, match="Nothing was stopped"):
        await _fetch(lambda r: httpx.Response(200, text=PULL_FAILS_IN_STREAM), strict=strict)


@pytest.mark.anyio
async def test_auth_refusal_falls_back_only_where_portainer_can_still_pull():
    handler = lambda r: httpx.Response(200, text=PULL_AUTH)  # noqa: E731
    op = await _fetch(handler, strict=False)          # Swarm paths: warn, carry on
    assert any(level == "warning" and "Couldn't fetch" in m for level, m in op.lines)
    with pytest.raises(RuntimeError, match="Nothing was stopped"):
        await _fetch(handler, strict=True)            # OmniGrid pulls itself: abort


@pytest.mark.anyio
async def test_a_pull_that_outlasts_the_limit_stops_everything(monkeypatch):
    monkeypatch.setattr(image_pull, "_budget_seconds", lambda: 0.2)

    async def slow(req):
        await asyncio.sleep(5)
        return httpx.Response(200, text=PULL_OK)

    with pytest.raises(RuntimeError, match="still downloading.*nothing was stopped"):
        await _fetch(slow, strict=False)   # a timeout aborts even when non-strict


@pytest.mark.anyio
async def test_direct_docker_reads_the_whole_body_not_the_snippet():
    class _Cli:
        last_body = ""

        async def post(self, path):
            self.last_body = PULL_FAILS_IN_STREAM
            return 200, None, PULL_FAILS_IN_STREAM[:40]   # the snippet hides it

    with pytest.raises(RuntimeError, match="Nothing was stopped"):
        await image_pull.ensure_image_direct(_Cli(), _Op(), "x:nope")


# --- the stack update, end to end ---------------------------------------------

def _stack_portainer(*, pull, rollout="completed", calls, tasks=None):
    """A fake Portainer for one stack, `tracearr`, with one service on NODE.

    ``tasks`` — the service's tasks as the rollout wait sees them: a list of
    task lists, one per poll (the last repeats). Default: one task with no
    status, as before."""
    task_polls = list(tasks or [[{"NodeID": "n1"}]])
    service = {
        "ID": "svc1",
        "Spec": {"Name": "tracearr_tracearr",
                 "Labels": {"com.docker.stack.namespace": "tracearr"},
                 "TaskTemplate": {"ContainerSpec": {"Image": IMAGE + "@sha256:old"}}},
        "UpdateStatus": {"State": rollout, "Message": "update in progress"},
    }

    async def handler(req: httpx.Request):
        path = req.url.path
        calls.append((req.method, path))
        if req.method == "GET" and path == "/api/stacks/7":
            return httpx.Response(200, json={"Id": 7, "Name": "tracearr", "Env": []})
        if req.method == "GET" and path == "/api/stacks/7/file":
            return httpx.Response(200, json={"StackFileContent": f"services:\n  tracearr:\n    image: {IMAGE}\n"})
        if req.method == "GET" and path.endswith("/docker/services"):
            return httpx.Response(200, json=[service])
        if req.method == "GET" and path.endswith("/docker/tasks"):
            if "desired-state" not in (req.url.params.get("filters") or ""):
                return httpx.Response(200, json=[{"NodeID": "n1"}])   # node lookup
            now = task_polls.pop(0) if len(task_polls) > 1 else task_polls[0]
            return httpx.Response(200, json=now)
        if req.method == "GET" and path.endswith("/docker/nodes"):
            return httpx.Response(200, json=[{"ID": "n1", "Description": {"Hostname": NODE}}])
        if req.method == "POST" and path.endswith("/docker/images/create"):
            return await pull(req)
        if req.method == "PUT" and path == "/api/stacks/7":
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"message": f"unexpected {req.method} {path}"})

    return handler


@pytest.fixture
def run_stack_update(monkeypatch):
    monkeypatch.setattr(ops_extras, "_portainer_op_timeout", lambda tier: 10.0)
    monkeypatch.setattr(ops_extras, "_tuning_int", lambda key: (
        0.3 if key == ops_extras._Tunable.STACK_UPDATE_OBSERVE_TIMEOUT_SECONDS else 0.02))

    async def _no_notify(*a, **kw):
        return None

    monkeypatch.setattr(ops_extras, "notify", _no_notify)
    monkeypatch.setattr(ops_extras, "persist_history", lambda op: None)
    monkeypatch.setattr(ops_extras.gather, "invalidate_cache", lambda: None)

    async def _run(handler) -> Operation:
        @contextlib.asynccontextmanager
        async def _write_client(timeout=60.0):
            async with _client(handler) as c:
                yield c

        monkeypatch.setattr(portainer, "write_client", _write_client)
        op = Operation("update_stack", "7", "tracearr", actor="tester")
        await ops_extras.do_update_stack(op, 7)
        return op

    return _run


def _order(calls):
    return [m for m, p in calls if (m, p.rsplit("/", 1)[-1]) in {("POST", "create"), ("PUT", "7")}]


@pytest.mark.anyio
async def test_stack_update_fetches_before_it_redeploys(run_stack_update):
    calls: list = []

    async def pull(req):
        return httpx.Response(200, text=PULL_OK)

    op = await run_stack_update(_stack_portainer(pull=pull, calls=calls))
    assert op.status == "success", op.error
    assert _order(calls) == ["POST", "PUT"]   # image fetched, THEN redeployed


@pytest.mark.anyio
async def test_a_slow_pull_leaves_the_running_service_untouched(run_stack_update, monkeypatch):
    """The Tracearr case: the pull outlasts the limit, so the redeploy — the
    step that makes Swarm stop the working task — is never sent."""
    monkeypatch.setattr(image_pull, "_budget_seconds", lambda: 0.2)
    calls: list = []

    async def pull(req):
        await asyncio.sleep(5)
        return httpx.Response(200, text=PULL_OK)

    op = await run_stack_update(_stack_portainer(pull=pull, calls=calls))
    assert op.status == "error"
    assert "nothing was stopped" in (op.error or "")
    assert ("PUT", "/api/stacks/7") not in calls


@pytest.mark.anyio
async def test_a_rollout_that_never_finishes_is_a_failure_not_a_success(run_stack_update):
    calls: list = []

    async def pull(req):
        return httpx.Response(200, text=PULL_OK)

    op = await run_stack_update(_stack_portainer(pull=pull, rollout="updating", calls=calls))
    assert op.status == "error"
    assert "tracearr_tracearr" in (op.error or "")
    assert "didn't finish" in (op.error or "")


# The cloudflared case: 3 replicas, one per node, on 3 nodes, updating
# start-first. The new task can never be placed, the old ones keep serving,
# and the service's UpdateStatus only ever says "update in progress" — the
# reason is on the pending TASK.
RUNNING = {"Status": {"State": "running"}}
UNPLACEABLE = {"Status": {"State": "pending",
                          "Err": "no suitable node (max replicas per node limit exceed)"}}


async def _ok_pull(req):
    return httpx.Response(200, text=PULL_OK)


@pytest.mark.anyio
async def test_an_unplaceable_task_fails_fast_with_the_reason_and_the_fix(run_stack_update,
                                                                          monkeypatch):
    # A long window: the failure must come from the reason, not the timeout.
    monkeypatch.setattr(ops_extras, "_tuning_int", lambda key: (
        30 if key == ops_extras._Tunable.STACK_UPDATE_OBSERVE_TIMEOUT_SECONDS else 0.01))
    calls: list = []
    handler = _stack_portainer(pull=_ok_pull, rollout="updating", calls=calls,
                               tasks=[[RUNNING, RUNNING, RUNNING, UNPLACEABLE]])
    started = time.monotonic()
    op = await run_stack_update(handler)
    assert time.monotonic() - started < 5
    assert op.status == "error"
    err = op.error or ""
    assert "max replicas per node" in err and "stop-first" in err
    assert "3 running replica(s) keep serving" in err
    assert "may be down" not in err


@pytest.mark.anyio
async def test_one_unplaceable_poll_is_not_enough(run_stack_update):
    """A task can be pending for one poll while an old one frees its slot."""
    calls: list = []
    handler = _stack_portainer(pull=_ok_pull, rollout="updating", calls=calls,
                               tasks=[[RUNNING, UNPLACEABLE], [RUNNING, RUNNING]])
    op = await run_stack_update(handler)
    assert "can't place" not in (op.error or "")          # timed out the ordinary way


@pytest.mark.anyio
async def test_a_timed_out_rollout_says_the_old_replicas_are_still_serving(run_stack_update):
    calls: list = []
    handler = _stack_portainer(pull=_ok_pull, rollout="updating", calls=calls,
                               tasks=[[RUNNING, RUNNING]])
    op = await run_stack_update(handler)
    assert "still serving the previous version" in (op.error or "")
    assert "may be down" not in (op.error or "")


# --- the Swarm-job fetch ------------------------------------------------------
#
# Through Portainer, a pull is one long request that sends nothing back until
# it finishes, so the reverse proxy in front of Portainer (90 s by default)
# cut every slow fetch with a 504 — the apprise update failed at exactly 90 s.
# On a Swarm manager the fetch is a one-shot job instead: the node's daemon
# downloads, no request is held open, and the job never runs the image.

NEVER_STARTS = 'starting container failed: exec: "/omnigrid-prefetch-never-runs": no such file'


def _swarm(task_states, *, create_first=None, seen=None):
    """A fake Swarm-manager Portainer whose fetch job's task walks through
    ``task_states`` (one per poll; the last repeats)."""
    seen = seen if seen is not None else {}
    seen.setdefault("creates", [])
    seen.setdefault("deleted", [])
    states = list(task_states)

    async def handler(req: httpx.Request):
        path = req.url.path
        if path.endswith("/docker/info"):
            return httpx.Response(200, json={"Swarm": {"LocalNodeState": "active",
                                                       "ControlAvailable": True}})
        if req.method == "GET" and path.endswith("/docker/services"):
            return httpx.Response(200, json=[])          # stale-job sweep: nothing
        if req.method == "POST" and path.endswith("/docker/services/create"):
            seen["creates"].append(json.loads(req.content))
            if create_first is not None and len(seen["creates"]) == 1:
                return create_first
            return httpx.Response(201, json={"ID": "job1"})
        if req.method == "GET" and path.endswith("/docker/tasks"):
            state, err = states.pop(0) if len(states) > 1 else states[0]
            return httpx.Response(200, json=[{"CreatedAt": "2026-09-26T22:00:00Z",
                                              "Status": {"State": state, "Err": err}}])
        if req.method == "DELETE" and "/docker/services/" in path:
            seen["deleted"].append(path.rsplit("/", 1)[-1])
            return httpx.Response(200)
        return httpx.Response(404)

    return handler, seen


@pytest.fixture
def fast_poll(monkeypatch):
    monkeypatch.setattr(image_pull, "_poll_seconds", lambda: 0.01)


async def _job_fetch(handler, *, strict=True, keep_digest=False, ref=IMAGE):
    op = _Op()
    async with _client(handler) as client:
        await image_pull.ensure_images_on_nodes(client, op, [(NODE, ref)],
                                                strict=strict, keep_digest=keep_digest)
    return op


@pytest.mark.anyio
async def test_swarm_fetch_is_a_pinned_job_that_never_runs_the_image(fast_poll):
    # `preparing` = downloading; then the nonexistent command can't start,
    # which is how we know the image is local.
    handler, seen = _swarm([("preparing", ""), ("preparing", ""), ("failed", NEVER_STARTS)])
    op = await _job_fetch(handler)
    spec = seen["creates"][0]
    assert spec["TaskTemplate"]["Placement"]["Constraints"] == [f"node.hostname=={NODE}"]
    assert spec["TaskTemplate"]["ContainerSpec"]["Command"] == ["/omnigrid-prefetch-never-runs"]
    assert spec["TaskTemplate"]["ContainerSpec"]["Image"] == IMAGE
    assert spec["TaskTemplate"]["RestartPolicy"] == {"Condition": "none"}
    assert "ReplicatedJob" in spec["Mode"]
    assert seen["deleted"] == ["job1"]                    # always cleaned up
    assert any("Fetched" in m for _, m in op.lines)
    assert ("fetching", "ghcr.io/connorgallopo/tracearr:supervised", None, None) in op.phases


@pytest.mark.anyio
async def test_a_rollback_job_fetches_the_exact_digest(fast_poll):
    handler, seen = _swarm([("failed", NEVER_STARTS)])
    await _job_fetch(handler, keep_digest=True, ref=IMAGE + "@sha256:old")
    assert seen["creates"][0]["TaskTemplate"]["ContainerSpec"]["Image"] == \
        "ghcr.io/connorgallopo/tracearr@sha256:old"


@pytest.mark.anyio
async def test_a_rejected_fetch_job_stops_everything_and_is_removed(fast_poll):
    handler, seen = _swarm([("preparing", ""),
                            ("rejected", "No such image: ghcr.io/connorgallopo/tracearr:nope")])
    with pytest.raises(RuntimeError, match="Nothing was stopped"):
        await _job_fetch(handler)
    assert seen["deleted"] == ["job1"]


@pytest.mark.anyio
async def test_a_fetch_job_past_the_limit_stops_everything_and_is_removed(fast_poll, monkeypatch):
    monkeypatch.setattr(image_pull, "_budget_seconds", lambda: 0.2)
    handler, seen = _swarm([("preparing", "")])          # never finishes
    with pytest.raises(RuntimeError, match="still downloading.*nothing was stopped"):
        await _job_fetch(handler, strict=False)          # a timeout aborts even non-strict
    assert seen["deleted"] == ["job1"]


@pytest.mark.anyio
async def test_a_job_the_registry_refuses_falls_back_only_where_portainer_can_pull(fast_poll):
    handler, _ = _swarm([("rejected", "pull access denied for private/app")])
    op = await _job_fetch(handler, strict=False)
    assert any(level == "warning" and "Couldn't fetch" in m for level, m in op.lines)
    handler, _ = _swarm([("rejected", "pull access denied for private/app")])
    with pytest.raises(RuntimeError, match="Nothing was stopped"):
        await _job_fetch(handler, strict=True)


@pytest.mark.anyio
async def test_docker_without_job_mode_falls_back_to_one_replica(fast_poll):
    handler, seen = _swarm([("failed", NEVER_STARTS)],
                           create_first=httpx.Response(400, text='{"message":"invalid mode"}'))
    await _job_fetch(handler)
    assert "ReplicatedJob" in seen["creates"][0]["Mode"]
    assert seen["creates"][1]["Mode"] == {"Replicated": {"Replicas": 1}}


@pytest.mark.anyio
async def test_a_proxy_cutting_a_streamed_pull_is_named_not_pasted():
    page = "<html><head><title>504 Gateway Time-out</title></head><body>openresty</body></html>"
    with pytest.raises(RuntimeError) as exc:
        await _fetch(lambda r: httpx.Response(504, text=page), strict=True)
    msg = str(exc.value)
    assert "proxy in front of Portainer" in msg and "<html" not in msg


# --- one op per target at a time ---------------------------------------------

@pytest.fixture
def fresh_ops(monkeypatch):
    from logic import ops as ops_mod
    monkeypatch.setattr(ops_mod, "ops", {})
    monkeypatch.setattr(ops_mod, "ops_order", [])
    return ops_mod


def test_a_second_update_of_the_same_stack_is_refused_while_the_first_runs(fresh_ops):
    first = fresh_ops.new_op("update_stack", "192", "apprise", actor="alice")
    with pytest.raises(fresh_ops.OpConflict, match="apprise is already being worked on"):
        fresh_ops.new_op("update_stack", "192", "apprise", actor="bob")
    fresh_ops.new_op("update_stack", "193", "authentik")          # another stack: fine
    first.done("error", "boom")
    fresh_ops.new_op("update_stack", "192", "apprise")            # finished: allowed again


def test_ops_on_the_same_container_conflict_across_the_family(fresh_ops):
    fresh_ops.new_op("update_container", "c1", "web")
    with pytest.raises(fresh_ops.OpConflict):
        fresh_ops.new_op("remove_container", "c1", "web")
    fresh_ops.new_op("restart_service", "c1", "web")              # a different family


def test_a_conflict_reaches_the_browser_as_409_not_500(fresh_ops):
    running = fresh_ops.new_op("update_stack", "192", "apprise", actor="alice")
    resp = asyncio.run(main._op_conflict(None, fresh_ops.OpConflict(running)))
    assert resp.status_code == 409
    assert "apprise is already being worked on" in json.loads(resp.body)["detail"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
