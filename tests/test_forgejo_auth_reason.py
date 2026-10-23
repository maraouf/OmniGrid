"""What the Forgejo card says when Forgejo refuses its token.

A private Forgejo answers an anonymous caller 403 and a token it doesn't know 401.
Both used to surface as "check the Forgejo token", which gives no way to tell a
revoked token from a missing scope from an instance that went private. Forgejo's
own message says which, so it is shown — with one precaution: Forgejo echoes the
credential it rejected back as ``[sha: <the token>]``, and that must never reach
a card, a test result or a log line.
"""
from __future__ import annotations

import asyncio

import httpx

from logic.apps import forgejo

TOKEN = "d3adbeef" * 5

# The shape of a real 401: one line per authentication method it tried.
_UNKNOWN_TOKEN = {"message": (
    "token is malformed: token contains an invalid number of segments\n"
    f"access token does not exist [sha: {TOKEN}]\n"
    "token is malformed: token contains an invalid number of segments\n"
    f'task with token "{TOKEN}" does not exist')}


def _resp(status: int, body) -> httpx.Response:
    return httpx.Response(status, json=body) if body is not None else httpx.Response(status, text="<html>")


def test_the_useful_line_is_picked_out_of_the_stack_of_attempts():
    assert forgejo._upstream_reason(_resp(401, _UNKNOWN_TOKEN), TOKEN) == "access token does not exist"


def test_the_token_never_appears_in_the_reason():
    reason = forgejo._upstream_reason(_resp(401, _UNKNOWN_TOKEN), TOKEN)
    assert TOKEN not in reason
    # and it is masked wherever else it turns up, even outside the [sha: …] form
    leaked = {"message": f"bad credential {TOKEN} for user"}
    assert TOKEN not in forgejo._upstream_reason(_resp(401, leaked), TOKEN)


def test_an_anonymous_refusal_reads_as_the_instance_being_private():
    body = {"message": "Only signed in user is allowed to call APIs."}
    assert forgejo._upstream_reason(_resp(403, body)) == "Only signed in user is allowed to call APIs."


def test_a_missing_scope_keeps_the_scope_list_that_names_the_fix():
    body = {"message": "token does not have at least one of required scope(s): [read:repository]"}
    assert "read:repository" in forgejo._upstream_reason(_resp(403, body), TOKEN)


def test_a_body_that_is_not_forgejos_json_gives_no_reason():
    assert forgejo._upstream_reason(_resp(502, None)) == ""
    assert forgejo._upstream_reason(_resp(401, {"nope": 1})) == ""


def test_the_connection_test_reports_why_without_the_token(monkeypatch):
    """The Test button is where an operator reads this, so check it end to end."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"version": "11.0"})
        return httpx.Response(401, json=_UNKNOWN_TOKEN)

    real = httpx.AsyncClient
    monkeypatch.setattr(forgejo.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(forgejo, "resolve_credential_target",
                        lambda host_row, chip, key: (TOKEN, "http://git.example:3000", None))
    out = asyncio.run(forgejo.test_credential({}, {}, TOKEN))
    assert out["ok"] is False and out["status"] == 401
    assert "access token does not exist" in out["detail"]
    assert TOKEN not in out["detail"]
