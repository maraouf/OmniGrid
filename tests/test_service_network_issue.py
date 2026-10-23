"""A stack service attached to no network must not read as healthy.

Swarm counts such a service as running (``1/1``) because its container is up,
yet nothing can reach it and it cannot resolve its siblings by name — the state
a stale overlay network leaves a stack in.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logic import gather  # noqa: E402

STACK = {"com.docker.stack.namespace": "unifiedsso"}


def _spec(networks=None, labels=STACK):
    tt = {"ContainerSpec": {"Image": "caddy:2-alpine"}}
    if networks is not None:
        tt["Networks"] = networks
    return {"Name": "unifiedsso_caddy", "Labels": dict(labels or {}),
            "Mode": {"Replicated": {"Replicas": 1}}, "TaskTemplate": tt}


def test_a_stack_service_with_no_network_is_flagged():
    assert gather.service_network_issue(_spec(), "unifiedsso") == "no_network"
    assert gather.service_network_issue(_spec(networks=[]), "unifiedsso") == "no_network"


def test_a_service_with_a_network_is_not_flagged():
    assert gather.service_network_issue(_spec([{"Target": "abc"}]), "unifiedsso") == ""


def test_a_service_outside_any_stack_is_left_alone():
    # A standalone service may publish through the ingress only.
    assert gather.service_network_issue(_spec(labels={}), None) == ""


def _build(spec):
    svc = {"ID": "s" * 25, "Spec": spec}
    task = {"ServiceID": svc["ID"], "DesiredState": "running",
            "Status": {"State": "running"}}
    return gather._build_direct_service_items([svc], [task], [], "docker", [])[0]


def test_a_running_service_with_no_network_is_degraded_not_healthy():
    item = _build(_spec())
    assert item["network_issue"] == "no_network"
    assert item["health"] == "degraded"


def test_a_running_service_with_a_network_stays_healthy():
    item = _build(_spec([{"Target": "abc"}]))
    assert item["network_issue"] == ""
    assert item["health"] == "healthy"
