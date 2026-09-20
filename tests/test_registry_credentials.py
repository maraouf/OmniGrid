"""Per-registry pull credentials for the update-check digest probe.

Before these existed only Docker Hub had credentials, so a private registry
answered the token request with 401, the digest never resolved, and the row
went red with `status=error` — on services that were running perfectly. The
live case that prompted this: a Forgejo registry that challenges with
``scope="*"`` and then refuses to issue a token for that scope, while the
repository-scoped request every Docker client sends succeeds.

These tests drive the real code through a fake registry, because the parts
worth pinning are exactly the ones a live probe would exercise: which scope is
asked for, whether the credential is attached, and — load-bearing — that it is
never attached for a registry it was not stored for.
"""
from __future__ import annotations

import base64

import httpx
import pytest

from logic import registry

HOST = "registry.example.com"
REPO = "team/app"
DIGEST = "sha256:" + "ab" * 32
MANIFEST = f"https://{HOST}/v2/{REPO}/manifests/latest"


def _basic(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


@pytest.fixture(autouse=True)
def clean_registry_state(monkeypatch):
    """No stored credentials and no warm caches unless a test says so."""
    monkeypatch.setattr(registry, "_creds_cache", ("", {}))
    monkeypatch.setattr(registry, "_creds_override", {})
    monkeypatch.setattr(registry, "DOCKERHUB_USER", "")
    monkeypatch.setattr(registry, "DOCKERHUB_TOKEN", "")
    registry._token_cache.clear()
    registry._digest_cache.clear()
    registry._platform_cache.clear()
    yield
    registry._token_cache.clear()
    registry._digest_cache.clear()


def _with_credentials(monkeypatch, rows: list[dict]):
    """Point the credential lookup at `rows` without touching a database."""
    import json
    raw = json.dumps(rows)
    monkeypatch.setattr(registry, "_creds_cache", ("", {}))
    monkeypatch.setattr("logic.db.get_setting", lambda *_a, **_kw: raw)


def _bearer_registry(*, accept_scopes: set[str], token: str = "tok",
                     expect_auth: str | None = None, seen: dict | None = None):
    """A token-auth registry. Only issues tokens for `accept_scopes`, and only
    to a request carrying `expect_auth` (None = anonymous is enough)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.setdefault("scopes", []).append(request.url.params.get("scope"))
            seen.setdefault("auth", []).append(request.headers.get("authorization"))
        if request.url.path == "/v2/token":
            scope = request.url.params.get("scope")
            got = request.headers.get("authorization")
            if expect_auth is not None and got != expect_auth:
                return httpx.Response(401, json={"errors": [{"code": "UNAUTHORIZED"}]})
            if scope not in accept_scopes:
                return httpx.Response(401, json={"errors": [{"code": "UNAUTHORIZED"}]})
            return httpx.Response(200, json={"token": token, "expires_in": 300})
        if request.headers.get("authorization") == f"Bearer {token}":
            return httpx.Response(200, headers={"docker-content-digest": DIGEST})
        return httpx.Response(401, headers={
            "www-authenticate": (f'Bearer realm="https://{HOST}/v2/token",'
                                 f'service="container_registry",scope="*"'),
        })

    return httpx.MockTransport(handler)


async def _digest(transport, image=f"{HOST}/{REPO}:latest"):
    async with httpx.AsyncClient(transport=transport) as client:
        return await registry.get_remote_digest(client, image)


@pytest.mark.anyio
async def test_private_registry_without_credentials_stays_anonymous_and_fails():
    """The behaviour this feature replaces — pinned so the fallback survives."""
    seen: dict = {}
    got = await _digest(_bearer_registry(
        accept_scopes={f"repository:{REPO}:pull"}, expect_auth=_basic("ci", "pw"),
        seen=seen))
    assert got is None
    # Nothing was offered to the token endpoint, so the registry had no
    # reason to let us in.
    assert not [a for a in seen["auth"] if a and a.startswith("Basic ")]


@pytest.mark.anyio
async def test_stored_credentials_are_used_for_that_registry(monkeypatch):
    _with_credentials(monkeypatch, [
        {"host": HOST, "username": "ci", "password": "pw", "enabled": True}])
    seen: dict = {}
    got = await _digest(_bearer_registry(
        accept_scopes={f"repository:{REPO}:pull"}, expect_auth=_basic("ci", "pw"),
        seen=seen))
    assert got == DIGEST
    assert _basic("ci", "pw") in [a for a in seen["auth"] if a]


@pytest.mark.anyio
async def test_catch_all_scope_falls_back_to_repository_scope(monkeypatch):
    """The live Forgejo shape: challenge says scope="*", token endpoint
    refuses it, and the repository-scoped request is the one that works."""
    _with_credentials(monkeypatch, [
        {"host": HOST, "username": "ci", "password": "pw", "enabled": True}])
    seen: dict = {}
    got = await _digest(_bearer_registry(
        accept_scopes={f"repository:{REPO}:pull"},  # "*" is NOT accepted
        expect_auth=_basic("ci", "pw"), seen=seen))
    assert got == DIGEST
    # Only the token requests carry a scope; the manifest fetches don't.
    token_scopes = [s for s in seen["scopes"] if s]
    assert token_scopes[:2] == ["*", f"repository:{REPO}:pull"]


@pytest.mark.anyio
async def test_credentials_never_travel_to_another_registry(monkeypatch):
    """A credential stored for one host must not be sent to a different one —
    including a lookalike that merely ends with the configured hostname."""
    _with_credentials(monkeypatch, [
        {"host": HOST, "username": "ci", "password": "pw", "enabled": True}])
    assert registry.credentials_for("evil." + HOST) is None
    assert registry.credentials_for("other.example.com") is None
    seen: dict = {}
    other = "other.example.com"
    transport = _bearer_registry(accept_scopes={f"repository:{REPO}:pull"},
                                 expect_auth=None, seen=seen)
    async with httpx.AsyncClient(transport=transport) as client:
        await registry.get_remote_digest(client, f"{other}/{REPO}:latest")
    assert all(a is None or a.startswith("Bearer") for a in seen["auth"])


@pytest.mark.anyio
async def test_disabled_row_is_ignored(monkeypatch):
    _with_credentials(monkeypatch, [
        {"host": HOST, "username": "ci", "password": "pw", "enabled": False}])
    assert registry.credentials_for(HOST) is None


@pytest.mark.anyio
async def test_basic_auth_registry(monkeypatch):
    """A registry that challenges with Basic issues no tokens at all."""
    _with_credentials(monkeypatch, [
        {"host": HOST, "username": "ci", "password": "pw", "enabled": True}])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") == _basic("ci", "pw"):
            return httpx.Response(200, headers={"docker-content-digest": DIGEST})
        return httpx.Response(401, headers={"www-authenticate": 'Basic realm="registry"'})

    assert await _digest(httpx.MockTransport(handler)) == DIGEST


@pytest.mark.anyio
async def test_malformed_setting_degrades_to_anonymous(monkeypatch):
    monkeypatch.setattr(registry, "_creds_cache", ("", {}))
    monkeypatch.setattr("logic.db.get_setting", lambda *_a, **_kw: "{not json")
    assert registry.credentials_for(HOST) is None


@pytest.fixture
def anyio_backend():
    return "asyncio"
