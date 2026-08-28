"""Regression tests for weak-key merge semantics and the Beszel disk figures.

The defect these pin: host-stats providers merge last-wins, which is correct
only while every provider is measuring the same thing. A Beszel agent without
``EXTRA_FILESYSTEMS`` reports the total of its own primary filesystem; SNMP and
node-exporter report the total across every mounted filesystem. Beszel merges
after SNMP, so on a NAS the narrower number won on ordering alone — a TrueNAS
host showed 394 GB (its root dataset) while SNMP had already supplied 13.4 TB
across the pools.

The fix lets a provider mark its own values low-confidence, so they seed an
empty field but never displace a value an earlier provider established. These
tests cover both halves: the generic merge rule, and Beszel actually declaring
the right keys in the right circumstances.
"""
from __future__ import annotations

from logic.beszel import extract_stats
from logic.merge import WEAK_KEYS_FIELD, merge_best

GIB = 1024 ** 3


def test_weak_value_does_not_displace_an_established_one():
    """The whole point: a narrower measurement must not win on merge order."""
    dst = {"host_disk_total": 13_400 * GIB}
    merge_best(dst, {
        WEAK_KEYS_FIELD: ["host_disk_total"],
        "host_disk_total": 394 * GIB,
    })
    assert dst["host_disk_total"] == 13_400 * GIB


def test_weak_value_still_seeds_an_empty_destination():
    """A weak value is better than none — Beszel-only hosts keep their disk."""
    dst: dict = {}
    merge_best(dst, {
        WEAK_KEYS_FIELD: ["host_disk_total"],
        "host_disk_total": 394 * GIB,
    })
    assert dst["host_disk_total"] == 394 * GIB


def test_weak_value_replaces_a_non_meaningful_one():
    """Zero is "no signal", not an established measurement."""
    dst = {"host_disk_total": 0}
    merge_best(dst, {
        WEAK_KEYS_FIELD: ["host_disk_total"],
        "host_disk_total": 394 * GIB,
    })
    assert dst["host_disk_total"] == 394 * GIB


def test_keys_not_listed_weak_still_overwrite():
    """The marker scopes to the keys named — everything else is unchanged."""
    dst = {"host_disk_total": 13_400 * GIB, "host_cpu_percent": 5.0}
    merge_best(dst, {
        WEAK_KEYS_FIELD: ["host_disk_total"],
        "host_disk_total": 394 * GIB,
        "host_cpu_percent": 42.0,
    })
    assert dst["host_cpu_percent"] == 42.0


def test_marker_never_lands_in_the_destination():
    """It is merge plumbing; it must not reach a snapshot or an API row."""
    dst: dict = {}
    merge_best(dst, {WEAK_KEYS_FIELD: ["host_disk_total"], "host_disk_total": 1})
    assert WEAK_KEYS_FIELD not in dst


def test_marker_survives_being_merged_twice():
    """Read, never popped.

    The same extracted-stats dict is merged by both gather and the per-host
    path. Consuming the marker on first use would silently disarm it for the
    second, which is the shape of bug that only appears in production.
    """
    src = {WEAK_KEYS_FIELD: ["host_disk_total"], "host_disk_total": 394 * GIB}
    merge_best({}, src)
    dst = {"host_disk_total": 13_400 * GIB}
    merge_best(dst, src)
    assert dst["host_disk_total"] == 13_400 * GIB


def test_absent_marker_keeps_plain_last_wins():
    """Providers that say nothing behave exactly as they did before."""
    dst = {"host_disk_total": 13_400 * GIB}
    merge_best(dst, {"host_disk_total": 394 * GIB})
    assert dst["host_disk_total"] == 394 * GIB


def test_beszel_declares_disk_weak_without_extra_filesystems():
    """No EFS list means stats.d describes one filesystem, not the machine."""
    stats = extract_stats({"h": "nas"}, {"d": 394.0, "du": 1.4, "dp": 0.35})
    assert set(stats[WEAK_KEYS_FIELD]) == {
        "host_disk_total", "host_disk_used", "host_disk_free",
        "host_disk_percent",
    }


def test_beszel_disk_is_strong_once_extra_filesystems_are_configured():
    """With an EFS list the sum IS the whole-machine view, so it should win."""
    stats = extract_stats({"h": "nas"}, {
        "d": 394.0, "du": 1.4,
        "efs": {"/mnt/POOL1": {"d": 5200.0, "du": 4400.0}},
    })
    assert stats[WEAK_KEYS_FIELD] == []
    assert stats["host_disk_total"] == int(5200.0 * GIB)


def test_truenas_case_end_to_end():
    """The reported failure, in merge order: SNMP then Beszel."""
    merged: dict = {}
    merge_best(merged, {                       # SNMP walked every pool
        "host_disk_total": 14_747_981_041_664,
        "host_disk_used": 12_000_000_000_000,
    })
    merge_best(merged, extract_stats(          # Beszel saw the root dataset
        {"h": "truenas", "dp": 0.35},
        {"d": 394.77, "du": 1.4},
    ))
    assert merged["host_disk_total"] == 14_747_981_041_664
