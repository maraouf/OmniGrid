"""Regression tests for host figures on direct-Docker Node cards.

The reported symptom: a direct-Docker node card showed its core count and
Docker's own disk usage and nothing else — no CPU percentage, no memory, no
uptime — while the Hosts view had the full figures for the same machine.

Two independent causes, both pinned here.

First, direct-Docker cards are added to ``nodes_info`` at the tail of the
gather, after every host-stats provider has already merged, so nothing
upstream ever populates their host fields. They are now filled from the same
persisted snapshot the rest of the pipeline falls back to.

Second, that snapshot lookup was case-sensitive. Docker reports whatever the
machine calls itself (``TrueNAS``) while the curated id is whatever was typed
(``truenas``), so the lookup missed and the fallback found nothing to apply.
Every other node-name-to-curated-host lookup in the codebase already folds
case; this was the odd one out.
"""
from __future__ import annotations

import inspect
import time

from logic.gather import apply_host_snapshot_fallback, merge_docker_nodes_into_cache

GIB = 1024 ** 3


def _snapshot(**fields):
    """One persisted snapshot row, as save_host_snapshots writes them."""
    return {"ts": time.time(), "data": dict(fields)}


def test_snapshot_lookup_folds_case():
    """The reported mismatch: Docker says TrueNAS, the curated id is truenas."""
    nodes = {"TrueNAS": {"state": "ready", "cpu_cores": 32}}
    apply_host_snapshot_fallback(
        nodes, {"truenas": _snapshot(host_disk_total=13_400 * GIB)})
    assert nodes["TrueNAS"]["host_disk_total"] == 13_400 * GIB


def test_snapshot_lookup_still_matches_exactly():
    """Folding case must not break the case that already worked."""
    nodes = {"truenas": {"state": "ready"}}
    apply_host_snapshot_fallback(
        nodes, {"truenas": _snapshot(host_cpu_percent=7.5)})
    assert nodes["truenas"]["host_cpu_percent"] == 7.5


def test_snapshot_lookup_still_matches_short_hostname():
    """The FQDN-vs-short-name case the fold was layered on top of."""
    nodes = {"docker01.example.com": {"state": "ready"}}
    apply_host_snapshot_fallback(
        nodes, {"docker01": _snapshot(host_mem_total=32 * GIB)})
    assert nodes["docker01.example.com"]["host_mem_total"] == 32 * GIB


def test_snapshot_lookup_does_not_match_a_different_host():
    """Case folding widens matching, so pin that it does not over-match."""
    nodes = {"TrueNAS": {"state": "ready"}}
    apply_host_snapshot_fallback(
        nodes, {"synology": _snapshot(host_disk_total=99 * GIB)})
    assert "host_disk_total" not in nodes["TrueNAS"]


def test_fallback_never_overwrites_a_live_value():
    """Seed-only. Docker's own reading wins wherever it has one."""
    nodes = {"TrueNAS": {"state": "ready", "host_cpu_percent": 42.0}}
    apply_host_snapshot_fallback(
        nodes, {"truenas": _snapshot(host_cpu_percent=1.0)})
    assert nodes["TrueNAS"]["host_cpu_percent"] == 42.0


def test_filled_fields_are_marked_as_coming_from_a_snapshot():
    """The UI needs to be able to say where a value came from."""
    nodes = {"TrueNAS": {"state": "ready"}}
    apply_host_snapshot_fallback(
        nodes, {"truenas": _snapshot(host_disk_total=13_400 * GIB)})
    assert "host_disk_total" in nodes["TrueNAS"].get("_stale_fields", [])


def test_direct_docker_merge_applies_the_fallback():
    """The second cause: nothing was calling this for direct-Docker cards.

    Structural, because the merge itself needs a reachable Docker node. It
    pins that the call exists and reuses one snapshot read for the loop, which
    is the shape a future edit is most likely to undo.
    """
    src = inspect.getsource(merge_docker_nodes_into_cache)
    assert "apply_host_snapshot_fallback(ninfo_map, _snapshots)" in src, (
        "direct-Docker cards are no longer fed the snapshot fallback")
    assert src.count("load_host_snapshots()") == 1, (
        "the snapshot read belongs outside the per-node loop")
