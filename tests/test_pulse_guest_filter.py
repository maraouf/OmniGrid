"""Tests for not harvesting Pulse alert records as guests.

Live data showed this arriving inside a guest-shaped array and being harvested
as a guest every tick::

    {"id": "ceph-...::alertspec:provider-incident:...",
     "type": "resource-incident", "level": "warning",
     "resourceName": "proxmox Ceph", "node": "proxmox Ceph"}

It matches because the guest predicate is deliberately loose, and that
looseness is load-bearing. Tightening it on 2026-05-09 also excluded Docker
container records, every probe for those hosts missed, and the fleet
auto-paused. So the fix is a narrow DENYLIST of types that are explicitly not
guests, never a tighter allowlist.

The test that matters is the last one. It asserts the denylist cannot catch the
record shape whose exclusion caused the cascade — which is the whole reason
this approach is safe where the previous one was not.
"""
from __future__ import annotations

import inspect

from logic import pulse


def test_the_observed_alert_type_is_excluded():
    assert "resource-incident" in pulse._NOT_GUEST_TYPES


def test_the_predicate_actually_consults_the_denylist():
    """The constant existing is not the same as it being used.

    Reads the parser, not the public entry point: `probe_pulse` is a thin
    never-raises wrapper around `_probe_pulse_impl`, which is where the
    guest predicate lives.
    """
    src = inspect.getsource(pulse._probe_pulse_impl)
    assert "_NOT_GUEST_TYPES" in src, (
        "the guest predicate no longer checks the denylist")


def test_the_denylist_cannot_exclude_a_docker_container():
    """The property that makes this safe where tightening was not.

    A Docker container record's `type` is never one of these, so this filter
    cannot reproduce the 2026-05-09 cascade in which container records were
    excluded and their hosts auto-paused.
    """
    for kind in ("docker", "container", "lxc", "qemu", "vm", ""):
        assert kind not in pulse._NOT_GUEST_TYPES, (
            f"type {kind!r} is on the denylist — this can now exclude real "
            f"guests, which is the failure mode the denylist exists to avoid")


def test_the_denylist_stays_narrow():
    """An over-broad denylist is the same failure wearing a different hat."""
    assert len(pulse._NOT_GUEST_TYPES) <= 8, (
        "the denylist is growing past the handful of record kinds actually "
        "observed; each entry can exclude a real guest if a vendor reuses the "
        "word")
