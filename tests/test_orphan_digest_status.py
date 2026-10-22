"""An exited task container whose tag moved on is "unknown", not an ERROR.

The case: after a `portainer_agent` update, Swarm left three exited task
containers behind, one per node. Docker reports their image as a bare
`sha256:<digest>` because the `portainer/agent:latest` tag now points at the
newer image. OmniGrid handed that to the registry code, which parses it as
Docker Hub's `library/sha256` repository with the digest as its tag. That
doesn't exist, so the lookup came back empty and the row was marked ERROR —
three red rows (and three pointless Hub requests per refresh) for containers
that had simply been replaced.

Nothing failed there: there is no repository to ask. "unknown" is the status
for "nothing to compare against"; "error" has to keep meaning a lookup that
genuinely failed, so that is pinned here too.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from logic import gather, registry

# 64 hex characters — the shape Docker reports for an untagged image.
BARE = "sha256:" + "0ae5ebfa96054918aa00991d07e1544f11f9434378de6cfb423764037785544f"
assert len(BARE) == 71


@pytest.mark.parametrize("ref", [
    BARE,
    BARE.removeprefix("sha256:"),
    "  " + BARE + "  ",
])
def test_a_bare_digest_is_recognised(ref):
    assert registry.is_digest_only_ref(ref)


@pytest.mark.parametrize("ref", [
    "portainer/agent:latest",
    "nginx",
    "ghcr.io/connorgallopo/tracearr:supervised",
    "portainer/agent@" + BARE,            # a repository WITH a digest has something to ask
    "sha256:" + "0" * 63,                 # not a full digest
    "sha256:" + "G" * 64,                 # not hex
    "",
])
def test_anything_with_a_repository_is_not(ref):
    assert not registry.is_digest_only_ref(ref)


def test_a_bare_digest_gets_no_hub_link_but_a_real_image_does():
    assert registry.hub_link(BARE) is None
    assert registry.hub_link("portainer/agent:latest") == "https://hub.docker.com/r/portainer/agent/tags"


def test_a_bare_digest_never_reaches_the_network():
    calls: list = []

    def handler(req: httpx.Request):
        calls.append(str(req.url))
        return httpx.Response(404)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await registry.get_remote_digest(c, BARE)

    assert asyncio.run(run()) is None
    assert calls == []                      # it used to ask Docker Hub for library/sha256


def _item(image, *, current: str | None = "sha256:" + "a" * 64, ignored=False):
    return {"image": image, "current_digest": current, "ignored": ignored, "placements": []}


async def _classify(item, *, remote):
    """Run the real classifier against a registry that answers `remote`."""
    calls: list = []

    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if remote is None:
            return httpx.Response(404)
        return httpx.Response(200, headers={"Docker-Content-Digest": remote})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        await gather._classify_item_status(c, item)
    return item["status"], calls


@pytest.mark.anyio
async def test_an_orphan_with_a_bare_digest_image_is_unknown_and_asks_nobody():
    status, calls = await _classify(_item(BARE), remote=None)
    assert status == "unknown"
    assert calls == []


@pytest.mark.anyio
async def test_a_real_lookup_failure_is_still_an_error():
    """The fix must not hide genuine failures: a normal image whose registry
    answers nothing keeps the ERROR it always had."""
    status, _ = await _classify(_item("portainer/agent:latest"), remote=None)
    assert status == "error"


@pytest.mark.anyio
async def test_the_ordinary_outcomes_are_unchanged():
    same = "sha256:" + "a" * 64
    img = "portainer/agent:latest"
    assert (await _classify(_item(img, current=same), remote=same))[0] == "up-to-date"
    registry._digest_cache.clear()          # the next answer differs; don't serve the cached one
    assert (await _classify(_item(img, current=same), remote="sha256:" + "b" * 64))[0] == "update"
    assert (await _classify(_item(img, current=None), remote=same))[0] == "unknown"
    assert (await _classify(_item(img, ignored=True), remote=same))[0] == "ignored"


def test_the_portainer_enrich_step_uses_the_same_rule():
    """Both backends decide this through ONE function, so they cannot drift."""
    import inspect
    src = inspect.getsource(gather)
    assert src.count("_status_without_registry(") == 3     # the definition + the two call sites


@pytest.fixture(autouse=True)
def _fresh_digest_cache():
    """The registry memoises digests per image; a stale answer from one case
    would be served to the next."""
    registry._digest_cache.clear()
    yield
    registry._digest_cache.clear()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_a_registry_hidden_behind_docker_io_is_read_as_that_registry():
    from logic.registry import parse_image_ref
    assert parse_image_ref("docker.io/ghcr.io/moghtech/komodo-core:2") == (
        "ghcr.io", "moghtech/komodo-core", "2")
    assert parse_image_ref("docker.io/registry.example:5000/team/app") == (
        "registry.example:5000", "team/app", "latest")
    # Real Hub references are untouched.
    assert parse_image_ref("docker.io/library/nginx:1.27")[1:] == ("library/nginx", "1.27")
    assert parse_image_ref("docker.io/team/app:3")[1:] == ("team/app", "3")
