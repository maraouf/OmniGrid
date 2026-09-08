"""Tests for the MAC-table lookup surviving a switch's pager.

Why these exist: "find the port holding this MAC and bounce it" reported the
address as absent from a switch it was plainly on. The switch — a Cisco
SG300-52MP — answers `show mac address-table` one screen at a time and then
waits at `More: <space>`. The reader ends on a quiet timer, so what came back
was the banner, the echoed command, and the table's HEADER, with the row
underneath it still unsent. Every reply was cut at the same place: the logged
output ran 477-492 bytes and stopped one character into the word "Port".

Nothing next to it caught this. The bounce commands that share the same shell
(`configure` / `interface` / `shutdown` / `end`) print nothing at all, so they
never meet the pager, and the lookup was the only caller that could.

The fixture below is the real captured shape, banner included.
"""
from __future__ import annotations

import inspect

from logic import ssh

# What the switch sends once paging is OFF: banner, echoed command, header,
# then the row. Reproduced from the logged transcript.
_FULL = """
Welcome to Cisco SG300-52MP 52-Port Gigabit PoE Managed Switch (Server Room / Rack)

Cisco SG300-52MP 52-Port Gigabit PoE Managed Switch (Server Room / Rack)
S/N: PSZ20311MN7, PID VID: SG300-52MP-K9 V03
Server Room / Rack
switch52mp01#show mac address-table address dc:a6:32:b4:4d:29
Flags: I - Internal usage VLAN

Aging time is 300 sec

    Vlan          Mac Address         Port       Type
------------ --------------------- ---------- ----------
     1        dc:a6:32:b4:4d:29        gi3      dynamic

switch52mp01#
"""

# What actually came back WITH paging on — and it is not a separate capture,
# it is the same reply cut short. Deriving it says so: the switch printed as
# far as the header, stopped at its `More:` prompt, and the reader ended on
# the quiet timer one character into the word "Port".
_PAGED = _FULL[:_FULL.index("Port") + 3]

_MAC = "DC:A6:32:B4:4D:29"


def test_the_port_is_read_out_of_a_complete_reply():
    assert ssh.parse_mac_table(_FULL, _MAC) == ["gi3"]


def test_the_banner_and_echoed_command_are_not_mistaken_for_ports():
    """The echoed command line mentions the address, so it reaches the token
    scan — the port must come from the table row, never from the echo."""
    ports = ssh.parse_mac_table(_FULL, _MAC)
    assert ports == ["gi3"], f"parsed {ports!r} — something in the chrome scanned as a port"


def test_the_truncated_reply_yields_nothing():
    """Pins the reported failure. Paging on, the row never arrives, so there
    is genuinely no port to find — which is why the tool concluded the address
    was absent. The fix belongs at the pager, not in the parser."""
    assert ssh.parse_mac_table(_PAGED, _MAC) == []


def test_paging_is_turned_off_before_the_query():
    """The actual fix. Without this the reply is cut at the header."""
    cmds = ssh.DEFAULT_MAC_LOOKUP_COMMANDS
    assert any("datadump" in c for c in cmds), (
        "the small-business Cisco paging-off command is gone — SG300-class "
        "switches will truncate the table again")
    assert any("length 0" in c for c in cmds), (
        "the classic-IOS paging-off command is gone")
    assert cmds[-1].startswith("show mac address-table"), (
        "the query must run AFTER paging is disabled")


def test_the_commands_are_sent_one_per_line():
    """A switch CLI has no statement separator: `a; b` is one unrecognised
    command. This was invisible while the default held a single command."""
    src = inspect.getsource(ssh.find_mac_interface)
    assert '"\\n".join(cmds)' in src, (
        "lookup commands are joined with something other than a newline — "
        "with paging-off now in the list, they would be typed as one line")
    assert '"; ".join(cmds)' not in src


def test_the_query_preview_is_wide_enough_to_show_a_truncation():
    """The 400-char preview ended one character into "Port", so a cut-off
    reply looked like a complete header and hid the missing rows."""
    src = inspect.getsource(ssh._run_shell_query)
    assert "out[:800]" in src, (
        "the query-output preview narrowed again — a truncated switch reply "
        "will look complete in the log")
