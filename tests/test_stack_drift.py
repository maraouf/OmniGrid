"""A redeploy applies Portainer's stored compose file — say what it would change.

The incident this exists for: CI deployed a stack straight from its repository,
the copy saved in Portainer stayed old, and an ordinary redeploy from the
dashboard put the old command back. The service lost its start-up install step
and crashed, and the confirm dialog had said nothing. These pin what is compared
and, as importantly, what is NOT — a warning that fires on a difference that
isn't there teaches the operator to click straight through the one that is.
"""
from __future__ import annotations

from logic import gather
from logic.stack_drift import MAX_COMMAND_CHARS, command_text, compute_drift

STACK = "telegram-web"


def _svc(name, image="python:3.12-alpine", command=None, entrypoint=None, labels=True):
    cs = {"Image": image}
    if command is not None:
        cs["Args"] = command
    if entrypoint is not None:
        cs["Command"] = entrypoint
    return {"Spec": {"Name": f"{STACK}_{name}",
                     "Labels": ({"com.docker.stack.namespace": STACK} if labels else {}),
                     "TaskTemplate": {"ContainerSpec": cs}}}


def _compose(body: str) -> str:
    return "services:\n" + body


def test_the_old_command_running_against_a_newer_saved_one_is_not_reported_here():
    # saved copy matches what runs → nothing to say
    saved = _compose("  auth:\n    image: python:3.12-alpine\n"
                     "    command: python /opt/app.py\n")
    out = compute_drift(saved, [_svc("auth", command=["python", "/opt/app.py"])], STACK)
    assert out == {"ok": True, "changes": []}


def test_a_saved_copy_that_lost_the_install_step_is_reported():
    saved = _compose("  auth:\n    image: python:3.12-alpine\n"
                     "    command: python /opt/app.py\n")
    running = _svc("auth", command=["sh", "-c", "pip install -r r.txt && python /opt/app.py"])
    out = compute_drift(saved, [running], STACK)
    assert out["ok"] is True
    (c,) = out["changes"]
    assert (c["service"], c["field"]) == ("auth", "command")
    assert "pip install" in c["running"]
    assert c["stored"] == "python /opt/app.py"


def test_a_string_command_is_split_the_way_compose_splits_it():
    saved = _compose('  auth:\n    command: sh -c "echo hi && sleep 1"\n')
    assert compute_drift(saved, [_svc("auth", command=["sh", "-c", "echo hi && sleep 1"])],
                         STACK)["changes"] == []


def test_a_command_the_saved_copy_does_not_set_is_reported_when_one_is_running():
    saved = _compose("  auth:\n    image: python:3.12-alpine\n")
    out = compute_drift(saved, [_svc("auth", command=["python", "app.py"])], STACK)
    assert [c["field"] for c in out["changes"]] == ["command"]
    assert out["changes"][0]["stored"] == ""


def test_the_entrypoint_is_compared_against_the_service_command_field():
    saved = _compose("  auth:\n    entrypoint: [/bin/entry]\n")
    same = compute_drift(saved, [_svc("auth", entrypoint=["/bin/entry"])], STACK)
    assert same["changes"] == []
    diff = compute_drift(saved, [_svc("auth", entrypoint=["/bin/other"])], STACK)
    assert [c["field"] for c in diff["changes"]] == ["entrypoint"]


def test_an_image_pin_and_an_implicit_latest_are_not_differences():
    saved = _compose("  web:\n    image: nginx\n")
    running = _svc("web", image="nginx:latest@sha256:" + "a" * 64)
    assert compute_drift(saved, [running], STACK)["changes"] == []


def test_a_different_image_is_reported():
    saved = _compose("  web:\n    image: nginx:1.27-alpine\n")
    out = compute_drift(saved, [_svc("web", image="nginx:1.25-alpine")], STACK)
    (c,) = out["changes"]
    assert (c["field"], c["running"], c["stored"]) == (
        "image", "nginx:1.25-alpine", "nginx:1.27-alpine")


def test_a_value_that_needs_the_stacks_environment_is_never_guessed_at():
    saved = _compose("  web:\n    image: ${WEB_IMAGE}\n    command: run ${MODE}\n")
    running = _svc("web", image="nginx:1", command=["something", "else"])
    assert compute_drift(saved, [running], STACK)["changes"] == []


def test_services_on_only_one_side_are_reported_because_the_update_prunes():
    saved = _compose("  new:\n    image: a\n")
    out = compute_drift(saved, [_svc("old", image="b")], STACK)
    assert {(c["service"], c["field"]) for c in out["changes"]} == {
        ("new", "added"), ("old", "removed")}


def test_another_stacks_services_are_not_this_stacks_services():
    saved = _compose("  auth:\n    image: python:3.12-alpine\n")
    stranger = _svc("auth", image="other:1", labels=False)
    out = compute_drift(saved, [stranger], STACK)
    assert [(c["service"], c["field"]) for c in out["changes"]] == [("auth", "added")]


def test_a_file_that_is_not_compose_is_not_an_answer():
    assert compute_drift("{ not: [valid", [], STACK)["ok"] is False
    assert compute_drift("version: '3'\n", [], STACK)["ok"] is False


def test_an_inline_credential_is_masked_in_the_text_shown():
    text = command_text(["app", "--password=hunter2", "API_TOKEN=abc123", "--verbose"])
    assert "hunter2" not in text and "abc123" not in text
    assert "--password=***" in text and "--verbose" in text


def test_a_very_long_command_is_capped():
    assert len(command_text(["x" * 5000])) <= MAX_COMMAND_CHARS


def _route(monkeypatch, pg):
    """The preview handler with Portainer replaced by ``pg(path)``."""
    import asyncio
    import contextlib

    import importlib
    importlib.import_module("main")  # the route modules import `main`, so it loads first
    from logic import portainer, tuning
    from main_pkg import ops_routes

    @contextlib.asynccontextmanager
    async def fake_client(timeout=60.0):
        yield object()

    async def fake_pg(client, path, agent_target=None):
        return pg(path)

    monkeypatch.setattr(tuning, "tuning_int", lambda *a, **k: 5)
    # `portainer.PORTAINER_ENDPOINT_ID` is resolved from the settings table on
    # access, and there is none here.
    monkeypatch.setattr(portainer, "_endpoint_id", lambda: 1)
    monkeypatch.setattr(portainer, "ensure_reachable", lambda: None)
    monkeypatch.setattr(portainer, "write_client", fake_client)
    monkeypatch.setattr(portainer, "pg", fake_pg)
    return asyncio.run(ops_routes.api_update_stack_preview(5, None))


def test_the_preview_route_reports_what_a_redeploy_would_change(monkeypatch):
    saved = _compose("  auth:\n    image: python:3.12-alpine\n    command: python /opt/app.py\n")

    def pg(path):
        if path.endswith("/file"):
            return {"StackFileContent": saved}
        if "/docker/services" in path:
            return [_svc("auth", command=["sh", "-c", "pip install x && python /opt/app.py"])]
        return {"Name": STACK, "Type": 1}

    out = _route(monkeypatch, pg)
    assert out["checked"] is True and out["stack"] == STACK
    assert [c["field"] for c in out["changes"]] == ["command"]


def test_the_preview_route_never_blocks_when_portainer_cannot_be_read(monkeypatch):
    import httpx

    def pg(path):
        raise httpx.ConnectError("down")

    out = _route(monkeypatch, pg)
    assert out["ok"] is False and out["checked"] is False and out["changes"] == []


def test_a_standalone_compose_stack_is_not_compared(monkeypatch):
    out = _route(monkeypatch, lambda path: {"Name": STACK, "Type": 2})
    assert out["checked"] is False and out["changes"] == []


def test_a_service_item_carries_its_command_with_credentials_masked():
    spec = {"Name": "s_app", "Labels": {"com.docker.stack.namespace": "s"},
            "Mode": {"Replicated": {"Replicas": 1}},
            "TaskTemplate": {"Networks": [{"Target": "n"}],
                             "ContainerSpec": {"Image": "i:1", "Command": ["run"],
                                               "Args": ["--token=secret", "go"]}}}
    svc = {"ID": "s" * 25, "Spec": spec}
    item = gather._build_direct_service_items([svc], [], [], "docker", [])[0]
    assert item["command"] == "run --token=*** go"
