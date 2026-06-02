"""Shared leaf helpers for authenticated per-app modules.

Per-app modules under ``logic/apps/<slug>.py`` stay self-contained for
their APP-SPECIFIC logic (auth probe, payload parsing, SKILLS,
run_skill). The GENERIC plumbing every credentialed app repeats —
resolving a chip's upstream base URL + the per-(host, service) fetch
cache — lives here so the structural duplication doesn't accumulate
across modules. Same precedent as ``logic/coerce.py`` (numeric coercion
shared by the same modules): a dependency-free leaf import, no cycle.
"""
from __future__ import annotations

from typing import Optional


def resolve_base_url(host_row: dict, chip: dict) -> str:
    """Resolve an app chip's upstream base URL.

    Priority: the chip's own ``url`` field (operator-set; includes
    scheme + optional port) wins; else
    ``<proto>://<host.address>:<first http/https probe port>``. Returns
    the URL with trailing slashes stripped so the caller appends its API
    path directly. Empty string when nothing resolves.
    """
    url = (chip.get("url") or "").strip()
    if url:
        return url.rstrip("/")
    address = (host_row.get("address") or "").strip()
    if not address:
        return ""
    probe = chip.get("probe") or {}
    ports = probe.get("ports") or []
    if isinstance(ports, list):
        for p in ports:
            if not isinstance(p, dict):
                continue
            port_n = p.get("port")
            proto = (p.get("protocol") or "").strip().lower()
            if isinstance(port_n, int) and 1 <= port_n <= 65535 and proto in ("http", "https"):
                return f"{proto}://{address}:{port_n}".rstrip("/")
    return ""


def cache_key(host_id: str, service_idx: int) -> str:
    """Canonical per-(host, service) cache key for a per-app data cache."""
    return f"{host_id}:{service_idx}"


def cache_get(cache: dict, key: str, ttl_s: float, now: float,
              force: bool = False) -> Optional[dict]:
    """Return the cached value for ``key`` when it's younger than
    ``ttl_s`` seconds (and ``force`` is false), else ``None``. ``cache``
    maps ``key -> (stored_at_epoch, value)``."""
    if force:
        return None
    cached = cache.get(key)
    if cached is None:
        return None
    stored_at, value = cached
    return value if (now - stored_at) < ttl_s else None


def fetch_preamble(host_row: dict, chip: dict, host_id: str, service_idx: int,
                   cache: dict, ttl_s: float, now: float,
                   force: bool) -> "tuple[str, Optional[dict]]":
    """The shared ``fetch_data`` preamble for credentialed per-app modules:
    resolve the chip's base URL (raise ``ValueError`` when it won't
    resolve) and return any still-fresh cached value. Returns
    ``(base_url, cached_or_None)`` — the caller returns the cached value
    immediately when it's non-None, else proceeds with ``base_url``. The
    per-app CREDENTIAL check stays in each module (it differs per app)."""
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured for this instance")
    return base, cache_get(cache, cache_key(host_id, service_idx), ttl_s, now, force)
