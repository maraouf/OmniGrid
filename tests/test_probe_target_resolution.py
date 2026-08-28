"""Regression tests for the shared probe-target resolution chain.

The defect these pin: the Hosts view and the Nodes view resolve a curated
host's probe target through different code. The per-host path walked
alias -> snmp_name -> address -> SKIP; the gather path that builds the Node
cards stopped at snmp_name. A host configured with `address` and no
`snmp_name` therefore had full CPU / memory / disk figures in the Hosts view
and none at all on its Node card, which instead fell back to showing Docker's
own view of the machine.

Both paths now call one resolver. These tests cover the chain itself and the
two properties that make it safe to share: an unconfigured host is skipped
rather than probed, and the bare host id is never used as a target.
"""
from __future__ import annotations

import inspect

from logic import gather as gather_mod
from logic.merge import resolve_probe_target
from main_pkg import hosts_merge_routes


def test_alias_wins_over_everything():
    """The global override map is the most specific thing configured."""
    row = {"id": "nas", "snmp_name": "nas.lan", "address": "192.0.2.9"}
    assert resolve_probe_target("nas", {"nas": "10.0.0.5"}, row, "snmp_name") == "10.0.0.5"


def test_provider_name_wins_over_address():
    """A provider-specific override beats the generic one."""
    row = {"id": "nas", "snmp_name": "nas.lan", "address": "192.0.2.9"}
    assert resolve_probe_target("nas", {}, row, "snmp_name") == "nas.lan"


def test_address_is_used_when_no_provider_name_is_set():
    """The reported failure: this step is what the gather path was missing."""
    row = {"id": "truenas", "address": "192.0.2.9"}
    assert resolve_probe_target("truenas", {}, row, "snmp_name") == "192.0.2.9"


def test_unconfigured_host_is_skipped_not_probed():
    """The hard gate. Falling through to the host id once fanned a fleet-wide
    enable out into a probe per host, nearly all of which timed out."""
    assert resolve_probe_target("truenas", {}, {"id": "truenas"}, "snmp_name") == ""


def test_blank_and_whitespace_values_do_not_count_as_configured():
    """An emptied field means "not set", not "probe an empty target"."""
    row = {"id": "nas", "snmp_name": "   ", "address": ""}
    assert resolve_probe_target("nas", {"nas": ""}, row, "snmp_name") == ""


def test_resolved_target_is_trimmed():
    """A pasted value with stray whitespace still resolves to a usable host."""
    assert resolve_probe_target("nas", {}, {"address": "  192.0.2.9 "},
                                "snmp_name") == "192.0.2.9"


def test_missing_row_degrades_to_the_alias_map():
    """A Docker node with no curated row at all must not raise."""
    assert resolve_probe_target("nas", {"nas": "10.0.0.5"}, None, "snmp_name") == "10.0.0.5"
    assert resolve_probe_target("nas", {}, None, "snmp_name") == ""


def test_chain_is_reusable_for_other_providers():
    """The resolver is parameterised by field name, not hardcoded to SNMP."""
    row = {"id": "nas", "webmin_name": "nas.lan", "address": "192.0.2.9"}
    assert resolve_probe_target("nas", {}, row, "webmin_name") == "nas.lan"
    assert resolve_probe_target("nas", {}, row, "pulse_name") == "192.0.2.9"


def test_both_paths_use_the_shared_resolver():
    """The divergence is what caused this, so pin that it cannot come back.

    An inline chain in either path is how the two drifted the first time: each
    was individually correct-looking and only differed in its last step.
    """
    for mod in (gather_mod, hosts_merge_routes):
        src = inspect.getsource(mod)
        assert "_resolve_probe_target(" in src, (
            f"{mod.__name__} no longer calls the shared resolver")
        assert 'snmp_row.get("snmp_name")' not in src, (
            f"{mod.__name__} has grown an inline SNMP target chain again")
