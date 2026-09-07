"""Tests for the Pulse probe surviving its own parser.

Two things went wrong together on 2026-09-08 and each is pinned here.

The bug: removing the spent guest schema-dump took the line that assigned
`g0` but left the loop that read it, and re-indented that loop into the
array-dump above -- so it ran on every probe and raised
``UnboundLocalError: cannot access local variable 'g0'``. The hub was
perfectly healthy; the fault was entirely in the parser.

The blast radius: `probe_pulse` documents "never raises", and both callers
rely on it -- `logic.gather` and the per-host fan-out, which gathers it
WITHOUT ``return_exceptions``. Only the fetch was guarded, so the parse
fault escaped and every `/api/hosts/one/{id}` returned HTTP 500. Every host
on the page read as an error because of one unbound name.

So the first test runs the real parser end to end, and the second pins the
contract that keeps a future parser fault from reaching the page at all.
"""
from __future__ import annotations

import asyncio
import inspect

from logic import pulse

# A state envelope carrying the arrays whose presence triggered the fault:
# the orphaned loop lived inside the `hosts` / `containers` / `dockerHosts`
# dump, so a payload without them would have passed while production broke.
STATE = {
    "nodes": [{"node": "pve1", "status": "online", "cpu": 0.12,
               "maxmem": 34359738368, "mem": 8589934592}],
    "vms": [{"vmid": 101, "name": "docker", "type": "qemu", "status": "running",
             "cpu": 0.04, "maxmem": 4294967296, "mem": 1073741824,
             "maxdisk": 53687091200, "disk": 10737418240, "node": "pve1",
             "info": {"osName": "debian"}}],
    "hosts": [{"host": "nas", "hostname": "nas", "cpuUsage": 7.5,
               "memory": {"total": 17179869184, "used": 4294967296},
               "uptimeSeconds": 90000, "osName": "TrueNAS"}],
    "containers": [{"vmid": 202, "name": "lxc-a", "type": "lxc",
                    "status": "running", "cpu": 0.01, "maxmem": 1073741824,
                    "mem": 268435456, "node": "pve1"}],
    "dockerHosts": [{"hostname": "dockerhost", "cpuUsagePercent": 3.0,
                     "totalMemoryBytes": 8589934592, "cpus": 4}],
}


def _run_probe(monkeypatch, state):
    async def _fake_version(_client, _base, _token):
        return "4.2.1"

    async def _fake_state(_client, _base, _token):
        return state

    monkeypatch.setattr(pulse, "_fetch_version", _fake_version)
    monkeypatch.setattr(pulse, "_fetch_state", _fake_state)
    return asyncio.run(pulse.probe_pulse("https://pulse.example.com", "tok"))


def test_a_real_state_payload_parses_without_raising(monkeypatch):
    """The regression itself: this raised UnboundLocalError in 1.6.14."""
    res = _run_probe(monkeypatch, STATE)
    assert res["error"] is None, f"probe reported an error: {res['error']}"
    assert res["hosts"], "nothing was indexed from a populated state payload"


def test_the_indexed_names_are_the_ones_operators_map_to(monkeypatch):
    """Guards the parse actually completed rather than returning early."""
    hosts = _run_probe(monkeypatch, STATE)["hosts"]
    for name in ("pve1", "docker", "nas", "lxc-a", "dockerhost"):
        assert name in hosts, f"{name!r} missing — the walk stopped short"


def test_a_parser_fault_becomes_a_down_provider_not_an_exception(monkeypatch):
    """The contract that bounds the blast radius.

    Neither caller guards this, and the fan-out gathers it without
    ``return_exceptions``, so an escape here is an HTTP 500 on every host
    row. A parser fault must degrade Pulse to "down" and leave the page
    standing.
    """
    def _boom(_host):
        raise RuntimeError("simulated parser fault")

    monkeypatch.setattr(pulse, "extract_node_stats", _boom)
    res = _run_probe(monkeypatch, STATE)
    assert res["hosts"] == {}
    assert "simulated parser fault" in res["error"]


def test_the_docstring_still_promises_never_raises():
    """Both callers are written against this sentence; if it is ever
    softened, they need guards of their own in the same change."""
    doc = inspect.getdoc(pulse.probe_pulse) or ""
    assert "Never raises" in doc or "never raises" in doc.lower()
