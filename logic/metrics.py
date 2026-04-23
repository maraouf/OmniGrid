"""Prometheus metrics for OmniGrid.

Owns the registry and all metric objects. main.py imports the names it
needs at call sites (e.g. ``metrics.OPS_TOTAL.labels(...).inc()``) and
registers a cache-age collector once at startup by passing a getter —
that avoids a circular import where this module would need ``_cache``
from main.

Metric names match ``notes/grafana_dashboard_omnigrid.json`` — do NOT
rename without updating the dashboard in the same commit.
"""
import time
from typing import Callable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily


# Exported so main.py can do `Response(content=generate_latest(metrics.REGISTRY), ...)`.
__all__ = [
    "REGISTRY",
    "ITEMS_TOTAL", "STACK_OUTDATED", "STACK_OFFLINE",
    "OPS_TOTAL", "REGISTRY_ERRORS", "REGISTRY_LATENCY", "GATHER_DURATION",
    "register_cache_age_collector", "populate_from_cache",
    "generate_latest", "CONTENT_TYPE_LATEST",
]


REGISTRY = CollectorRegistry()

ITEMS_TOTAL = Gauge(
    "omnigrid_items_total",
    "Items by status and type",
    ["status", "type"],
    registry=REGISTRY,
)
STACK_OUTDATED = Gauge(
    "omnigrid_stack_outdated",
    "Outdated items per stack",
    ["stack"],
    registry=REGISTRY,
)
STACK_OFFLINE = Gauge(
    "omnigrid_stack_offline",
    "Offline items per stack",
    ["stack"],
    registry=REGISTRY,
)
OPS_TOTAL = Counter(
    "omnigrid_ops_total",
    "One-click operations performed",
    ["op_type", "status"],
    registry=REGISTRY,
)
REGISTRY_ERRORS = Counter(
    "omnigrid_registry_errors_total",
    "Remote-registry probe failures (per registry host)",
    ["registry"],
    registry=REGISTRY,
)
REGISTRY_LATENCY = Histogram(
    "omnigrid_registry_latency_seconds",
    "Remote-registry HEAD/GET latency",
    ["registry"],
    registry=REGISTRY,
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
GATHER_DURATION = Histogram(
    "omnigrid_gather_duration_seconds",
    "End-to-end _gather() duration",
    registry=REGISTRY,
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120),
)


class _CacheAgeCollector:
    """Reports ``omnigrid_cache_age_seconds`` at scrape time.

    Uses a custom Collector (not a Gauge) so the value reflects NOW even
    between ``_gather()`` calls — Prometheus gets a fresh reading on every
    scrape without any event needing to fire.
    """

    def __init__(self, cache_getter: Callable[[], dict]):
        self._get = cache_getter

    def collect(self):
        g = GaugeMetricFamily(
            "omnigrid_cache_age_seconds",
            "Seconds since items cache was last populated",
        )
        cache = self._get() or {}
        age = (time.time() - cache["ts"]) if cache.get("ts") else 0.0
        g.add_metric([], age)
        yield g


def register_cache_age_collector(cache_getter: Callable[[], dict]) -> None:
    """Wire the cache-age collector. Call once at startup from main.py."""
    REGISTRY.register(_CacheAgeCollector(cache_getter))


def populate_from_cache(cache: dict) -> None:
    """Re-populate label-keyed gauges from the just-built cache.

    Called at the end of ``_gather()``. Clears first so stacks that
    disappeared don't linger as stale label sets — Prometheus gauges never
    decay on their own and would otherwise report ghost values forever.

    Every known (status, type) combo is pre-initialised to zero so the
    resulting series always exist, even when the fleet has nothing in
    that bucket. Without this, queries like
    ``sum(omnigrid_items_total{status="error"})`` return no series
    (not zero) when all items are healthy, and Grafana stat panels
    render that as "No data" instead of 0.
    """
    from collections import Counter as _C

    ITEMS_TOTAL.clear()
    STACK_OUTDATED.clear()
    STACK_OFFLINE.clear()

    # Pre-seed every known (status, type) at 0 so queries against specific
    # label combinations always have a series to match. The backend's set of
    # valid statuses / types is small and stable — see main.py item-building
    # code. Keep this list in sync if new values are introduced.
    for status in ("up-to-date", "update", "error", "unknown", "ignored"):
        for typ in ("service", "container", "orphan"):
            ITEMS_TOTAL.labels(status=status, type=typ).set(0)

    counts = _C(
        (i.get("status", "unknown"), i.get("type", "unknown"))
        for i in cache.get("items", [])
    )
    for (status, typ), n in counts.items():
        ITEMS_TOTAL.labels(status=status, type=typ).set(n)

    for s in cache.get("stacks", []):
        name = s.get("name") or "?"
        STACK_OUTDATED.labels(stack=name).set(s.get("updates", 0))
        STACK_OFFLINE.labels(stack=name).set(s.get("offline", 0))
