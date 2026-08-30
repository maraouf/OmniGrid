"""Tests for the read-only interactive-shell query path.

Why this path exists: the switches it asks refuse SSH exec requests, so the
ordinary exec runner is not available for them. The reboot runner already
shares their interactive shell, but not the question — it treats the channel
dropping as success, and a `show` command never drops the channel. Sending a
query through it would wait out the entire timeout and then report failure with
the answer sitting unread in the transcript.

So these run the real runner against a scripted device, in the shapes that
matter: a normal reply, a device that answers slowly, one that says nothing,
and one that hangs up mid-answer.
"""
from __future__ import annotations

import asyncio

import pytest

from logic.ssh import _run_shell_query

SG300_REPLY = """show mac address-table address 6c:63:f8:53:bd:1f

  Vlan          Mac Address         Port       Type
------------ --------------------- ---------- ----------
    1         6c:63:f8:53:bd:1f     gi12       dynamic

switch52#"""


class _FakeStdin:
    """Records what was typed at the device."""

    def __init__(self):
        self.written: list[str] = []
        self.broken = False

    def write(self, data):
        if self.broken:
            raise ConnectionResetError("device hung up")
        self.written.append(data)


class _FakeStdout:
    """Replays a scripted device.

    Each script entry is (delay_seconds, text). ``None`` text means the
    channel closed. Once the script runs out the device is silent, which is
    what the runner uses to decide the answer is complete.
    """

    def __init__(self, script):
        self._script = list(script)

    async def read(self, _n):
        if not self._script:
            await asyncio.sleep(10)      # silent; the runner's poll times out
            return ""
        delay, text = self._script.pop(0)
        await asyncio.sleep(delay)
        if text is None:
            raise ConnectionResetError("channel closed")
        return text


class _FakeProc:
    def __init__(self, script):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(script)


class _FakeConn:
    def __init__(self, script):
        self.proc = _FakeProc(script)
        self.term_size = None

    async def create_process(self, **kwargs):
        self.term_size = kwargs.get("term_size")
        return self.proc


def _run(script, timeout=8.0):
    conn = _FakeConn(script)
    result: dict = {}
    asyncio.run(_run_shell_query(
        conn, "show mac address-table address 6c:63:f8:53:bd:1f",
        timeout, result, {"host": "switch52", "user": "admin"}, 0.0))
    return conn, result


def test_a_normal_reply_is_captured_and_reported_ok():
    _, result = _run([(0.05, SG300_REPLY)])
    assert result["ok"] is True
    assert "gi12" in result["stdout"]


def test_a_normal_reply_returns_as_soon_as_the_device_goes_quiet():
    """The reason this runner exists rather than reusing the reboot one.

    That one has no way to tell a finished answer from a slow one, so it
    waits out the whole budget. A lookup that takes the full timeout every
    time would still eventually answer, which is why this needs asserting
    rather than assuming: removing the quiet-window check leaves every test
    here passing and merely slow.
    """
    import time as _t
    t0 = _t.time()
    _, result = _run([(0.05, SG300_REPLY)], timeout=8.0)
    elapsed = _t.time() - t0
    assert result["ok"] is True
    assert elapsed < 4.0, f"waited {elapsed:.1f}s for a reply that arrived at once"


def test_the_command_is_terminated_so_the_device_runs_it():
    """Without a newline the switch just sits there with the line unsubmitted."""
    conn, _ = _run([(0.05, SG300_REPLY)])
    assert conn.proc.stdin.written[0].endswith("\n")


def test_output_arriving_in_pieces_is_reassembled():
    """A device that answers over several reads must not be cut off at the
    first pause, which is why the quiet window is longer than the poll."""
    _, result = _run([(0.05, SG300_REPLY[:60]), (0.3, SG300_REPLY[60:])])
    assert result["ok"] is True
    assert "gi12" in result["stdout"]


def test_a_silent_device_fails_with_an_explanation():
    """It is most likely sitting at a prompt this command did not answer, and
    saying so is the difference between a fixable report and a dead end."""
    _, result = _run([], timeout=2.0)
    assert result["ok"] is False
    assert "prompt" in result["error"]


def test_a_reply_cut_short_still_returns_what_arrived():
    """Losing the channel mid-answer should not discard the answer."""
    _, result = _run([(0.05, SG300_REPLY), (0.05, None)])
    assert result["ok"] is True
    assert "gi12" in result["stdout"]


def test_the_shell_is_left_rather_than_dropped():
    """Hanging up under a switch is impolite and can leave a session behind."""
    conn, _ = _run([(0.05, SG300_REPLY)])
    assert any(w.strip() == "exit" for w in conn.proc.stdin.written)


def test_a_device_that_rejects_the_query_reports_that_not_a_timeout():
    conn = _FakeConn([(0.05, SG300_REPLY)])
    conn.proc.stdin.broken = True
    result: dict = {}
    asyncio.run(_run_shell_query(conn, "show mac address-table", 5.0, result,
                                 {"host": "switch52"}, 0.0))
    assert not result.get("ok")
    assert "could not send" in result["error"]


def test_the_terminal_is_wide_enough_not_to_wrap_a_table_row():
    """A wrapped row splits the port away from the address it belongs to, and
    the parser reads rows."""
    conn, _ = _run([(0.05, SG300_REPLY)])
    assert conn.term_size is not None and conn.term_size[0] >= 132


@pytest.mark.parametrize("timeout", [0.5, 1.0])
def test_the_budget_is_respected(timeout):
    """A device that never answers must not hold the call open indefinitely."""
    import time as _t
    t0 = _t.time()
    _run([], timeout=timeout)
    assert _t.time() - t0 < max(2.0, timeout) + 3.0
