"""Tests for locating a MAC address on a switch, which feeds the port bounce.

The capability these support: "find the port on switch52 holding
6c:63:f8:53:bd:1f and bounce it". Bouncing a port was already possible from
every surface, but nothing could answer the first half, so the request stopped
before it started.

The parsing is deliberately vendor-agnostic — it reads the rows mentioning the
address and picks the tokens that could be a port name — so these cover the two
table formats in front of us plus the shapes that would fool a naive parser:
the heading row, the type column, and a MAC that is on more than one port.
"""
from __future__ import annotations

from logic.ssh import mac_renderings, normalize_mac, parse_mac_table

# A small-business Cisco, which prints colon-separated addresses and short
# port names.
SG300 = """
          Aging time is 300 sec

  Vlan          Mac Address         Port       Type
------------ --------------------- ---------- ----------
    1         6c:63:f8:53:bd:1f     gi12       dynamic
"""

# Classic IOS, which prints dotted quads and slashed port names.
IOS = """
          Mac Address Table
-------------------------------------------

Vlan    Mac Address       Type        Ports
----    -----------       --------    -----
   1    6c63.f853.bd1f    DYNAMIC     Gi1/0/12
"""


def test_any_written_form_of_an_address_is_accepted():
    """It gets copied from a label, a lease, or another switch's output."""
    for written in ("6c:63:f8:53:bd:1f", "6C-63-F8-53-BD-1F",
                    "6c63.f853.bd1f", "6C63F853BD1F", "6c 63 f8 53 bd 1f"):
        assert normalize_mac(written) == "6c63f853bd1f", written


def test_a_non_address_is_refused():
    """Returning "" is what stops a malformed value reaching a switch."""
    for bad in ("", "not-a-mac", "6c:63:f8:53:bd", "6c:63:f8:53:bd:1f:00",
                "zz:zz:zz:zz:zz:zz", None):
        assert normalize_mac(bad) == ""


def test_every_spelling_is_offered_to_the_command_template():
    """A vendor accepts the form its own CLI prints and rejects the others."""
    forms = mac_renderings("6C63F853BD1F")
    assert forms["mac"] == "6c:63:f8:53:bd:1f"
    assert forms["mac_dash"] == "6c-63-f8-53-bd-1f"
    assert forms["mac_dot"] == "6c63.f853.bd1f"
    assert forms["mac_bare"] == "6c63f853bd1f"


def test_reads_a_small_business_cisco_table():
    assert parse_mac_table(SG300, "6c:63:f8:53:bd:1f") == ["gi12"]


def test_reads_a_classic_ios_table():
    assert parse_mac_table(IOS, "6c:63:f8:53:bd:1f") == ["Gi1/0/12"]


def test_the_address_is_matched_however_the_switch_prints_it():
    """Asked with colons, answered with dots: still the same address."""
    assert parse_mac_table(IOS, "6C-63-F8-53-BD-1F") == ["Gi1/0/12"]


def test_the_type_column_is_not_mistaken_for_a_port():
    """"dynamic" sits right next to the port and looks like a word, not a port."""
    assert "dynamic" not in parse_mac_table(SG300, "6c:63:f8:53:bd:1f")
    assert "DYNAMIC" not in parse_mac_table(IOS, "6c:63:f8:53:bd:1f")


def test_an_absent_address_yields_nothing():
    """The device is off, on another switch, or has aged out of the table."""
    assert parse_mac_table(SG300, "aa:bb:cc:dd:ee:ff") == []


def test_every_port_is_reported_when_an_address_is_on_several():
    """The caller refuses to choose, so it has to see all of them.

    One of these is usually a trunk toward another switch, and bouncing that
    because a device behind it misbehaved would take out everything else
    behind it too.
    """
    table = """
  Vlan          Mac Address         Port       Type
    1         6c:63:f8:53:bd:1f     gi12       dynamic
    1         6c:63:f8:53:bd:1f     gi48       dynamic
"""
    assert parse_mac_table(table, "6c:63:f8:53:bd:1f") == ["gi12", "gi48"]


def test_a_repeated_port_is_reported_once():
    """The same port on two VLANs is still one port to bounce."""
    table = """
    1         6c:63:f8:53:bd:1f     gi12       dynamic
   20         6c:63:f8:53:bd:1f     gi12       dynamic
"""
    assert parse_mac_table(table, "6c:63:f8:53:bd:1f") == ["gi12"]


def test_empty_output_and_a_bad_address_are_survivable():
    """A switch that answered with nothing must not raise."""
    assert parse_mac_table("", "6c:63:f8:53:bd:1f") == []
    assert parse_mac_table(SG300, "nonsense") == []


def test_the_lookup_is_registered_as_a_confirm_gated_ai_tool():
    """It is read-only but it opens a session to the switch, like ssh_diag."""
    from logic.ai_extras import (PALETTE_TOOL_CATALOGUE,
                                 PALETTE_TOOLS_REQUIRING_CONFIRM)
    assert "find_mac_port" in PALETTE_TOOL_CATALOGUE
    assert "find_mac_port" in PALETTE_TOOLS_REQUIRING_CONFIRM


def test_the_model_is_told_to_look_the_port_up_before_bouncing():
    """The whole point is the chain; a prompt that omits it leaves the AI
    guessing a port number, which bounces the wrong device."""
    from logic.ai import PALETTE_SYSTEM_PROMPT
    assert "find_mac_port(args:" in PALETTE_SYSTEM_PROMPT
    assert "locate-then-bounce" in PALETTE_SYSTEM_PROMPT
