"""Shared DNS-failure skip cache for the per-host samplers.

When `_dns_probe_all` at boot logs `WARN [boot] DNS startup check: N
of M curated host targets are unresolved`, every sampler tick STILL
probes those unresolvable hosts, paying the full per-host timeout
because `socket.getaddrinfo` blocks on the libc resolver. With 58
unresolvable hosts on a 172-host fleet and 4-6 samplers each running
their probe loop, that's hundreds of seconds of executor-thread
capacity wasted per minute — leaving /api/healthz + /api/me queued
behind the dead-host probes and producing the operator-visible 504
storm.

This module exposes ONE helper — :func:`should_skip_dns` — that
samplers call BEFORE the probe. It tries `socket.gethostbyname` on
the target. On success it returns ``False`` (proceed) AND wipes any
prior negative cache so a recovered host rejoins immediately. On
failure it stamps the cache and returns ``True``; subsequent calls
within ``tuning_dns_failed_skip_seconds`` return ``True`` immediately
without re-touching the resolver. IP literals (anything that parses
via `socket.inet_aton`) ALWAYS return ``False`` — no DNS involved.

Single source of truth so every sampler observes the same policy
without copy-pasting the gate. Single-process single-replica
deployment means a plain module dict is correct (same justification
as the settings cache + the host-provider cache).
"""
from __future__ import annotations

import socket
import time

from logic import tuning
from logic.tuning import Tunable

# {hostname: epoch_ts_of_last_failure}. Lookup is O(1); the TTL
# check happens on read so a recovered host transparently latches
# off (we don't need a background sweeper to age out entries —
# they self-clear via the `pop` on success).
_dns_fail_cache: dict[str, float] = {}


def _ttl_seconds() -> int:
    """Per-use read so an Admin → Config edit takes effect on the
    next sampler tick. Defensive fallback to the documented default
    if the tunable read raises (corrupt DB state)."""
    try:
        return int(tuning.tuning_int(Tunable.DNS_FAILED_SKIP_SECONDS))
    except (KeyError, ValueError, TypeError):
        return 300


def should_skip_dns(target: str) -> bool:
    """Return True iff the caller should SKIP probing `target` because
    its DNS resolution is known to be failing within the TTL window.

    Returns False (proceed) when:
      - target is an IP literal (no DNS involved)
      - target is empty or None
      - DNS resolution succeeds (and clears any prior negative cache)
      - the cached failure is older than the TTL window

    Returns True (skip) when:
      - DNS resolution failed and the failure is recent

    Per-call cost when no cache entry exists: one `socket.gethostbyname`
    syscall (the resolver does its own caching — typically a few µs
    on cache hit, up to the resolver's own timeout on miss). Per-call
    cost when an entry exists and is within TTL: one dict lookup +
    one subtract. Per-call cost when entry is expired: same as cold
    (resolver call + cache write).
    """
    if not target:
        return False
    # IP literal short-circuit — both IPv4 and IPv6. inet_aton covers
    # IPv4; the IPv6 path uses inet_pton which is also available.
    try:
        socket.inet_aton(target)
        return False
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, target)
        return False
    except (OSError, ValueError):
        pass
    now = time.time()
    last_fail = _dns_fail_cache.get(target)
    ttl = _ttl_seconds()
    if last_fail is not None and (now - last_fail) < ttl:
        return True
    try:
        socket.gethostbyname(target)
    except (socket.gaierror, OSError):
        _dns_fail_cache[target] = now
        return True
    # Resolution succeeded — latch off any prior failure so a
    # recovered host rejoins immediately.
    _dns_fail_cache.pop(target, None)
    return False


def clear_dns_skip_cache() -> None:
    """Drop every cached entry. Called from tests + by the operator
    via a future Admin → Tunables button if it ever lands."""
    _dns_fail_cache.clear()


def dns_skip_diag() -> dict:
    """Return a snapshot of the cache for diagnostic surfaces.
    Shape: {ttl_seconds, entry_count, sample_failing: [...]}.
    Used by the host-debug API + the future samplers-health panel."""
    items = list(_dns_fail_cache.items())
    items.sort(key=lambda kv: kv[1], reverse=True)
    return {
        "ttl_seconds": _ttl_seconds(),
        "entry_count": len(items),
        "sample_failing": [k for k, _ in items[:10]],
    }
