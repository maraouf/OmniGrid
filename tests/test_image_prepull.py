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
import contextlib
import json

import httpx
import pytest

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

async def _fetch(handler, *, strict, targets=((NODE, IMAGE),)):
    op = _Op()
    async with _client(handler) as client:
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

def _stack_portainer(*, pull, rollout="completed", calls):
    """A fake Portainer for one stack, `tracearr`, with one service on NODE."""
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
            return httpx.Response(200, json=[{"NodeID": "n1"}])
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


@pytest.fixture
def anyio_backend():
    return "asyncio"
