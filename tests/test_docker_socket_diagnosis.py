"""Regression tests for the Docker-node socket diagnosis.

When the socket forward is refused but a plain TCP forward through the same
SSH connection succeeds, the block is the socket rather than sshd. Two very
different causes remain — the SSH user cannot use the socket, or Docker is not
running / the path is wrong — and they want opposite fixes, so the diagnostic
resolves which one it is over the session it already holds.

These tests also pin the two properties that make that safe: it must stay
read-only, and it must degrade to the previous generic guidance rather than
raising when the probe itself fails.
"""
from __future__ import annotations

import asyncio

import pytest

from logic import docker_direct as dd

SOCK = "/var/run/docker.sock"
USER = "admin"


class _Res:
    """Stand-in for an asyncssh process result."""
    def __init__(self, out: str) -> None:
        self.stdout = out
        self.stderr = ""
        self.exit_status = 0


class FakeConn:
    """Records the script it was asked to run, so a test can assert on it."""

    def __init__(self, out: str = "", raise_exc: BaseException | None = None) -> None:
        self.out = out
        self.raise_exc = raise_exc
        self.script: str | None = None

    async def run(self, script: str, **_kw) -> _Res:
        self.script = script
        if self.raise_exc is not None:
            raise self.raise_exc
        return _Res(self.out)


def reply(*lines: str, banner: str = "") -> str:
    """Wrap fake node output in the markers the parser looks for."""
    return banner + "OG-SOCK-BEGIN\n" + "\n".join(lines) + "\nOG-SOCK-END\n"


PERMISSION_DENIED = reply(
    "exists=yes", "owner=root group=docker mode=660",
    "groups=admin wheel", "uid=950", "access=no")
SOCKET_MISSING = reply(
    "exists=no", "owner=? group=? mode=?", "groups=admin", "uid=950", "access=no")
ACCESS_OK = reply(
    "exists=yes", "owner=root group=docker mode=660",
    "groups=admin docker", "uid=950", "access=yes")
ROOT_REFUSED = reply(
    "exists=yes", "owner=root group=root mode=660",
    "groups=root", "uid=0", "access=no")


def _diagnose(conn: FakeConn) -> str:
    """Run the diagnosis against a fake connection."""
    return asyncio.run(dd._diagnose_socket(conn, SOCK, USER, 20.0))


def test_permission_denied_names_the_real_group_and_both_fixes():
    """The common case: socket present, this user locked out of it."""
    hint = _diagnose(FakeConn(PERMISSION_DENIED))
    assert "block is permissions" in hint
    assert "'docker' group" in hint          # the ACTUAL owning group
    assert "root SSH user" in hint           # fix with no change on the node
    assert "NEW SSH session" in hint         # group membership needs a re-login


def test_socket_missing_points_at_the_daemon_not_at_permissions():
    """No socket means a daemon problem — group advice would mislead."""
    hint = _diagnose(FakeConn(SOCKET_MISSING))
    assert "DOES NOT EXIST" in hint
    assert "isn't running" in hint
    assert "group" not in hint.split("DOES NOT EXIST")[0]


def test_access_fine_points_at_a_daemon_that_is_not_listening():
    """Readable and writable, yet refused — that is a dead daemon."""
    hint = _diagnose(FakeConn(ACCESS_OK))
    assert "CAN read and write" in hint
    assert "stale socket" in hint


def test_root_but_refused_blames_acls_not_group_membership():
    """Group advice cannot apply to uid 0, so it must not be repeated there."""
    hint = _diagnose(FakeConn(ROOT_REFUSED))
    assert "uid 0" in hint
    assert "ACLs or a read-only mount" in hint


def test_login_banner_before_the_markers_still_parses():
    """A MOTD ahead of the markers must not derail the parse."""
    hint = _diagnose(FakeConn(reply(
        "exists=yes", "owner=root group=docker mode=660",
        "groups=admin", "uid=950", "access=no",
        banner="Welcome to TrueNAS SCALE\nLast login: Mon\n")))
    assert "block is permissions" in hint


@pytest.mark.parametrize("conn", [
    FakeConn("", raise_exc=OSError("boom")),
    FakeConn("command not found"),
    FakeConn(""),
])
def test_probe_failure_degrades_to_the_generic_guidance(conn):
    """A broken probe must never make the diagnostic WORSE than it was."""
    hint = _diagnose(conn)
    assert "SSH forwarding IS enabled" in hint
    assert "ls -l" in hint


def test_cancellation_propagates():
    """The broad except must not swallow task cancellation."""
    with pytest.raises(asyncio.CancelledError):
        _diagnose(FakeConn("", raise_exc=asyncio.CancelledError()))


@pytest.mark.parametrize("verb", ["usermod", "chmod", "chown", "systemctl", "sudo", "rm "])
def test_probe_is_read_only(verb):
    """It diagnoses someone else's machine — it must not change it."""
    conn = FakeConn(PERMISSION_DENIED)
    _diagnose(conn)
    assert verb not in (conn.script or "")
