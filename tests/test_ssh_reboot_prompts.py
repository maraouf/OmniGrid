"""Regression tests for the SSH reboot prompt-answering loop.

The bug these lock down: a Cisco SG300 with unsaved changes asks TWO questions
before reloading, and the old code wrote the whole answer ("Y" plus a newline)
in a single write. The "Y" answered question 1; the newline was still buffered
when question 2 appeared, and a bare Enter at a `(Y/N)[N]` prompt takes the
bolded DEFAULT — N. The switch cancelled its own reload, and OmniGrid reported
"device did not reboot within 15s".

The fake switch below reproduces that exact behaviour: it consumes stdin one
character at a time and treats Enter at a prompt as "take the default".
"""
from __future__ import annotations

import asyncio

import pytest

from logic import ssh

ESC = "\x1b"
UNSAVED = ("\r\nYou haven't saved your changes. Are you sure you want to "
           f"continue ? (Y/N)[{ESC}[1mN{ESC}[0m] ")
RESET = ("\r\nThis command will reset the whole system and disconnect your "
         f"current session. Do you want to continue ? (Y/N)[{ESC}[1mN{ESC}[0m] ")
OVERWRITE = f"\r\nOverwrite file [startup-config] .... (Y/N)[{ESC}[1mN{ESC}[0m] "

SAVE_COMMANDS = ("write memory", "wr mem", "copy running-config startup-config")


class FakeSwitch:
    """Character-at-a-time CLI. Enter at a prompt == accept the default (N)."""

    def __init__(self, *, dirty: bool = True, needs_enter: bool = False) -> None:
        self.out: asyncio.Queue = asyncio.Queue()
        self.line = ""
        self.prompts: list[tuple[str, str]] = []
        self.stage = "cli"
        self.dirty = dirty
        self.needs_enter = needs_enter
        self.saved = False
        self.rebooted = False
        self.aborted = False
        self.closed = False
        self.answers: list[tuple[str, str]] = []
        self._pending_key: str | None = None

    def _ask(self, *questions: tuple[str, str]) -> None:
        self.prompts = list(questions)
        self.out.put_nowait(self.prompts[0][1])
        self.stage = "ask"

    def feed(self, data: str) -> None:
        for ch in data:
            if self.stage == "cli":
                if ch in "\r\n":
                    cmd, self.line = self.line.strip(), ""
                    self._run(cmd)
                else:
                    self.line += ch
                    self.out.put_nowait(ch)
            elif self.stage == "ask":
                if self.needs_enter:
                    # Commits only on Enter; a bare key is echoed and buffered.
                    if ch in "\r\n":
                        key = self._pending_key or "N"
                        self._pending_key = None
                        self._answer(key)
                    else:
                        self._pending_key = ch
                        self.out.put_nowait(ch)
                else:
                    self._answer("N" if ch in "\r\n" else ch)

    def _answer(self, key: str) -> None:
        name = self.prompts[0][0]
        self.answers.append((name, key.upper()))
        self.out.put_nowait(key)
        if key.upper() != "Y":
            self.aborted = True
            self.prompts = []
            self.out.put_nowait("\r\nswitch52mp01#")
            self.stage = "cli"
            return
        self.prompts.pop(0)
        if self.prompts:
            self.out.put_nowait(self.prompts[0][1])
            return
        if name == "overwrite":
            self.saved = True
            self.dirty = False
            self.out.put_nowait("\r\nCopy succeeded\r\nswitch52mp01#")
            self.stage = "cli"
        else:
            self.rebooted = True
            self.out.put_nowait("\r\nShutting down ...\r\n")
            self.closed = True

    def _run(self, cmd: str) -> None:
        if cmd in SAVE_COMMANDS:
            self._ask(("overwrite", OVERWRITE))
        elif cmd == "reload":
            questions = []
            if self.dirty:
                questions.append(("unsaved", UNSAVED))
            questions.append(("reset", RESET))
            self._ask(*questions)
        else:
            self.out.put_nowait("\r\nswitch52mp01#")


class _Stdin:
    """Feeds written characters straight into the fake switch."""
    def __init__(self, sw: FakeSwitch) -> None:
        self.sw = sw

    def write(self, d: str) -> None:
        self.sw.feed(d)

    async def drain(self) -> None:
        pass


class _Stdout:
    """Yields queued device output; blocks forever when idle, like a
    device sitting at a prompt."""
    def __init__(self, sw: FakeSwitch) -> None:
        self.sw = sw

    async def read(self, _n: int) -> str:
        if self.sw.closed and self.sw.out.empty():
            return ""
        try:
            return await asyncio.wait_for(self.sw.out.get(), timeout=0.2)
        except (asyncio.TimeoutError, TimeoutError):
            if self.sw.closed:
                return ""
            await asyncio.sleep(3600)   # device sits idle at its prompt
            return ""


class _Proc:
    """Stand-in for an asyncssh process."""
    def __init__(self, sw: FakeSwitch) -> None:
        self.stdin, self.stdout, self.stderr = _Stdin(sw), _Stdout(sw), _Stdout(sw)

    def close(self) -> None:
        pass


class _Conn:
    """Stand-in for an asyncssh connection."""
    def __init__(self, sw: FakeSwitch) -> None:
        self.sw = sw

    async def create_process(self, **_kw) -> _Proc:
        return _Proc(self.sw)


async def _reboot(sw: FakeSwitch, resolved: dict | None = None) -> dict:
    """Drive the real prompt-answering loop against a fake switch."""
    result: dict = {}
    await ssh._run_shell_sequence(
        _Conn(sw), "reload", "Y\n", 8.0, result, resolved or {}, 0.0)
    return result


def _run(coro):
    """Run one coroutine to completion."""
    return asyncio.run(coro)


def test_two_prompts_both_answered_yes():
    """The reported failure: the second question must not receive the default."""
    sw = FakeSwitch(dirty=True)
    result = _run(_reboot(sw))
    assert [a for _, a in sw.answers] == ["Y", "Y"]
    assert sw.rebooted is True
    assert sw.aborted is False
    assert result.get("ok") is True


def test_single_prompt_when_config_is_clean():
    """With nothing unsaved the switch asks only the reset question."""
    sw = FakeSwitch(dirty=False)
    result = _run(_reboot(sw))
    assert [a for _, a in sw.answers] == ["Y"]
    assert sw.rebooted is True
    assert result.get("ok") is True


def test_save_command_runs_before_the_reboot():
    """A configured save must complete FIRST, so nothing is left unsaved."""
    sw = FakeSwitch(dirty=True)
    result = _run(_reboot(sw, {"save": "write memory"}))
    assert sw.saved is True
    assert sw.rebooted is True
    # Saving clears the unsaved-changes question, so only two prompts appear.
    assert [n for n, _ in sw.answers] == ["overwrite", "reset"]
    assert result.get("ok") is True


def test_device_that_requires_enter_to_commit_each_answer():
    """Such gear echoes the keypress instantly, so an echo cannot be taken as
    proof the answer was accepted — the loop must fall back on quiet time."""
    sw = FakeSwitch(dirty=True, needs_enter=True)
    result = _run(_reboot(sw))
    assert [a for _, a in sw.answers] == ["Y", "Y"]
    assert sw.rebooted is True
    assert result.get("ok") is True


@pytest.mark.parametrize("iface", [
    "gi37", "gigabitethernet37", "gi1/0/12", "Te1/1/1", "eth0", "Vlan10",
])
def test_interface_names_accepted(iface):
    """Real interface names must survive the whitelist unchanged."""
    assert ssh.normalize_interface(iface) == iface


@pytest.mark.parametrize("iface", [
    "gi1; reload", "$(reload)", "gi37\nreload", "", "../etc", "gi37 && x",
    "1gi", "gi|x", "gi`x`", "gi37;", "a" * 80,
])
def test_hostile_interface_names_refused(iface):
    """The name reaches a switch shell AND is AI-reachable, so it is a strict
    whitelist rather than an escape pass."""
    assert not ssh.normalize_interface(iface)
