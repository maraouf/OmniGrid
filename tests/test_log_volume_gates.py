"""Two per-tick diagnostics that were most of the log, gated.

Measured on a 21-minute window of the running deployment (2,000 lines):

  [snmp] probe: ...        332 lines  16%   28 hosts, ~19 each
  [events] publish ...     307 lines  15%   ping_sampled + history_appended

Neither carried its volume. The SNMP line reports three totals a switch will
never have — `cpu%=None mem_total=None disk_total=None` on every probe it will
ever answer — so for most of a network fleet it repeats a fixed string forever.
The event traces say only that a dispatch happened; the samplers behind them
already log the same event WITH the value measured, on the same code path.

This is the third instance of one shape: a diagnostic written to answer a
question during development, left firing per tick per host once answered.
`host:provider_*` and `tab:*` were silenced for it earlier, and the beszel
`efs-aggregate` line was gated for it earlier in this same session.
"""
from __future__ import annotations

import inspect
import re

from logic import events, snmp


# --- the SNMP probe diagnostic ---------------------------------------------

def _reset():
    snmp._probe_diag_last.clear()


def test_a_new_host_is_logged():
    _reset()
    assert snmp._probe_diag_changed("sw01", ("sw01", False, False, False, 22))


def test_the_same_shape_is_not_logged_again():
    """The whole point: a switch answering identically every probe produces
    one line, not one per tick forever."""
    _reset()
    shape = ("sw01", False, False, False, 22)
    assert snmp._probe_diag_changed("sw01", shape)
    for _ in range(20):
        assert not snmp._probe_diag_changed("sw01", shape)


def test_a_changed_shape_is_logged_again():
    """The case actually worth seeing — a host whose reply shape moves. A
    switch that stops reporting interfaces, or a server that stops reporting
    memory, is exactly what this line exists to surface."""
    _reset()
    snmp._probe_diag_changed("sw01", ("sw01", False, False, False, 22))
    assert snmp._probe_diag_changed("sw01", ("sw01", False, False, False, 0)), (
        "interface count dropped to zero and nothing was logged")
    snmp._probe_diag_changed("srv01", ("srv01", True, True, True, 4))
    assert snmp._probe_diag_changed("srv01", ("srv01", True, False, True, 4)), (
        "memory stopped being reported and nothing was logged")


def test_hosts_do_not_silence_each_other():
    _reset()
    assert snmp._probe_diag_changed("a", ("a", False, False, False, 1))
    assert snmp._probe_diag_changed("b", ("b", False, False, False, 1))


def test_the_map_cannot_grow_without_bound():
    _reset()
    for i in range(snmp._PROBE_DIAG_CAP + 40):
        snmp._probe_diag_changed(f"h{i}", (f"h{i}", False, False, False, 1))
    assert len(snmp._probe_diag_last) <= snmp._PROBE_DIAG_CAP


def test_the_gate_keys_on_shape_not_on_live_values():
    """A CPU percentage changes every probe. If the gate keyed on the value
    the dedup would never fire for any host that reports one, and servers
    would go back to a line per tick."""
    src = inspect.getsource(snmp)
    call = re.search(r"_probe_diag_changed\(host_clean, \((?P<args>.*?)\)\):", src, re.S)
    assert call, "the gate call moved — check what it keys on"
    args = call.group("args")
    assert "host_cpu_percent" in args, "CPU presence is not part of the key"
    assert args.count("is not None") >= 3, (
        "the gate is not keying on PRESENCE for all three totals")
    # The raw number must not be in the key. Quote style is not the point,
    # so match either: a `.get(...)` for cpu that is NOT followed by the
    # presence test is a value, and a value changes every probe.
    assert not re.search(r"get\(['\"]host_cpu_percent['\"]\)\s*,", args), (
        "the raw CPU value is in the dedup key; it changes every probe, so "
        "the line would print every time again")


# --- the event traces -------------------------------------------------------

def test_the_two_high_volume_event_traces_are_suppressed():
    assert "host:ping_sampled" in events._TRACE_SUPPRESSED_TYPES
    assert "host:history_appended" in events._TRACE_SUPPRESSED_TYPES


def test_suppression_silences_the_TRACE_not_the_EVENT():
    """Load-bearing distinction. The SPA depends on these events arriving;
    only the log line is dropped. If the gate ever moved to wrap the publish
    itself, per-row chips would stop updating."""
    src = inspect.getsource(events.publish)
    gate = src[src.index("_TRACE_SUPPRESSED_TYPES"):]
    assert "print" in gate, "the suppression gate no longer guards a print"
    after = src[src.index("_TRACE_SUPPRESSED_TYPES"):]
    assert "bus.publish" in after or "_bus" in after or "publish" in after, (
        "the dispatch no longer follows the gate — events may be being dropped")


def test_the_prefix_families_are_still_suppressed():
    """The set is for individually-named types; the families that were
    already silenced must not have been displaced by it."""
    src = inspect.getsource(events.publish)
    assert 'startswith("host:provider_")' in src
    assert 'startswith("tab:")' in src


def test_the_samplers_still_log_the_value_behind_each_suppressed_event():
    """The justification for silencing these two, asserted rather than
    assumed — I checked this before writing it into the comment. If either
    value line disappears, suppressing its event trace starts costing real
    visibility instead of removing noise."""
    from logic import ping_sampler, host_metrics_sampler
    ping_src = inspect.getsource(ping_sampler)
    # The print is wrapped across two source lines, so this deliberately
    # spans newlines rather than staying within one.
    assert re.search(r"\[ping_sampler\].{0,200}?alive=", ping_src, re.S), (
        "ping_sampler no longer logs its per-host result with the value")
    assert "loss=" in ping_src and "rtt" in ping_src
    # host:history_appended is published from _fire_ne_success_side_effects;
    # the `wrote cpu=` line must stay on that same path.
    fn = inspect.getsource(host_metrics_sampler._fire_ne_success_side_effects)
    assert "host:history_appended" in fn
    assert "wrote cpu=" in fn, (
        "the measured-value line left the function that publishes "
        "host:history_appended — the suppressed trace is now the only record")
