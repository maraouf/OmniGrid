"""Beszel integration — read-only consumer of a Beszel hub.

Beszel is a lightweight cross-platform monitoring tool (Linux / macOS /
Windows) built on PocketBase. It has two pieces:

  - Beszel Hub — web UI + storage (PocketBase backend).
  - Beszel Agent — tiny Go binary per host; pushes metrics to the Hub.

OmniGrid treats the Hub as a data source: one GET per gather fetches
every system's latest snapshot from the Hub's PocketBase REST API. We
map each system to a Docker hostname from :mod:`logic.gather`'s node
list and surface the numbers on the Nodes view alongside everything
else. Advantages over node-exporter scraping:

  - Single HTTP call fetches every host (vs. one per node).
  - Cross-platform out of the box (Beszel agents run on Win/Mac/Linux).
  - Operator already deploys Beszel for a reason; this just reuses it.

Trade-off: requires the operator to run Beszel (hub + agents). Not
suitable for users who'd rather stay in the exporter-only model.

Auth: PocketBase auth-with-password flow. We cache the token in-process
and re-auth on 401. Credentials live in the ``settings`` table (admin
should create a readonly Beszel user for OmniGrid).

Units: Beszel stores memory / disk as floats in GiB (``info.m``,
``info.mt``, ``info.d``, ``info.dt``). Uptime (``info.u``) is seconds.
We convert GiB → bytes with ``* 1024**3`` so the number shape matches
the rest of OmniGrid (which is bytes everywhere).
"""
from __future__ import annotations

import time
from typing import Optional

import httpx


# In-process token cache so every gather doesn't re-auth. Keyed by
# (base_url, identity) — an operator changing the Hub URL or identity
# in Settings will miss the cache and re-auth, which is correct.
_token_cache: dict[tuple[str, str], dict] = {}


def _cache_key(base_url: str, identity: str) -> tuple[str, str]:
    return (base_url.rstrip("/"), identity)


def _pb_err_detail(r: "httpx.Response") -> str:
    """Extract PocketBase's validation-error detail from a 400 response.

    PB wraps field errors in ``{"message": "...", "data": {field: {...}}}``;
    we stringify that into a flat hint so the operator sees *why* auth
    failed (usually "Failed to authenticate" for wrong password or
    "invalid email" for a malformed identity).
    """
    try:
        j = r.json() or {}
        msg = j.get("message") or ""
        data = j.get("data") or {}
        if data:
            parts = []
            for field, info in data.items():
                if isinstance(info, dict):
                    parts.append(f"{field}: {info.get('message') or info.get('code') or info}")
                else:
                    parts.append(f"{field}: {info}")
            if parts:
                return f"{msg} ({'; '.join(parts)})" if msg else "; ".join(parts)
        return msg or f"HTTP {r.status_code}"
    except Exception:
        return f"HTTP {r.status_code}"


async def _authenticate(
    client: httpx.AsyncClient,
    base_url: str,
    identity: str,
    password: str,
) -> str:
    """POST PocketBase's auth-with-password and return a bearer token.

    Tries three endpoints in order — PocketBase renamed things between
    v0.22 and v0.23, and Beszel versions vary:
      1. /api/collections/users/auth-with-password (regular user)
      2. /api/collections/_superusers/auth-with-password (PB v0.23+ admin)
      3. /api/admins/auth-with-password (PB v0.22 and earlier admin)

    Returns the first successful token. On total failure, raises with
    the most informative error message across all attempts so the
    operator can see exactly why (typically "Failed to authenticate"
    for a wrong password, which is actionable).
    """
    endpoints = [
        "/api/collections/users/auth-with-password",
        "/api/collections/_superusers/auth-with-password",
        "/api/admins/auth-with-password",
    ]
    errors: list[str] = []
    for path in endpoints:
        try:
            r = await client.post(
                base_url.rstrip("/") + path,
                json={"identity": identity, "password": password},
                headers={"Content-Type": "application/json"},
            )
        except Exception as e:
            errors.append(f"{path}: {e}")
            continue
        if r.status_code < 400:
            data = r.json() or {}
            token = data.get("token")
            if token:
                return token
            errors.append(f"{path}: 200 but no token in response")
            continue
        errors.append(f"{path}: {_pb_err_detail(r)}")
    # Deduplicate and collapse — operators mostly want the "real" reason
    # (typically the last endpoint's detail, which tends to be the
    # clearest). Include all for completeness.
    raise RuntimeError("beszel auth failed — " + " | ".join(errors))


async def _get_token(
    client: httpx.AsyncClient,
    base_url: str,
    identity: str,
    password: str,
    force_refresh: bool = False,
) -> str:
    key = _cache_key(base_url, identity)
    if not force_refresh:
        entry = _token_cache.get(key)
        if entry and entry.get("expires", 0) > time.time():
            return entry["token"]
    token = await _authenticate(client, base_url, identity, password)
    # PocketBase tokens default to ~1 hour; cache for 45 min to stay safe.
    _token_cache[key] = {"token": token, "expires": time.time() + 45 * 60}
    return token


async def _fetch_systems(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
) -> list[dict]:
    """List every system record from the Hub.

    perPage=500 is well above any realistic homelab fleet size, so one
    request suffices. If it ever isn't, we'd iterate pages — but that's
    hypothetical; no one has 500 physical hosts.
    """
    url = (base_url.rstrip("/")
           + "/api/collections/systems/records?perPage=500")
    r = await client.get(url, headers={"Authorization": token})
    if r.status_code == 401:
        # Token expired / revoked — caller will re-auth + retry.
        raise PermissionError("401")
    if r.status_code >= 400:
        raise RuntimeError(f"beszel fetch systems: HTTP {r.status_code}")
    data = r.json() or {}
    return list(data.get("items") or [])


def _num(v) -> float:
    """Coerce anything number-ish to a float, falling back to 0.

    Beszel's JSON has been known to emit numbers as strings in older
    hub versions; be tolerant so a field-type change doesn't blank the
    whole row.
    """
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def extract_stats(info: dict) -> dict:
    """Map one Beszel ``info`` dict → OmniGrid's nodes_info shape.

    Beszel's short-keyed JSON (``m`` = mem used GiB, ``mt`` = mem total
    GiB, ``d`` = disk used GiB, ``dt`` = disk total GiB, ``u`` = uptime
    seconds) is expanded into the same host_* fields that node-exporter
    populates, so the frontend doesn't care which data source is active.

    Missing fields degrade to 0 / None. A Beszel system that's paused
    or offline typically has stale values — the caller should watch
    the record's top-level ``status`` field for 'up' vs. 'down'.

    Also surfaces the extra metadata the Hosts tab renders in its
    SYSTEM + HARDWARE cards (platform, os, kernel, architecture, core
    count, agent version, current cpu %) — all pulled from the same
    ``info`` object so a single /systems call gives us everything.
    """
    if not isinstance(info, dict):
        info = {}
    gib = 1024 ** 3
    mem_total = _num(info.get("mt")) * gib
    mem_used = _num(info.get("m")) * gib
    disk_total = _num(info.get("dt")) * gib
    disk_used = _num(info.get("d")) * gib
    uptime = _num(info.get("u"))
    # host_boot_ts = now - uptime so the frontend's uptime display
    # matches what node-exporter produces (boot-time in epoch seconds).
    host_boot_ts = (time.time() - uptime) if uptime > 0 else None
    return {
        "host_disk_total": int(disk_total),
        "host_disk_used":  int(disk_used),
        "host_disk_free":  max(0, int(disk_total - disk_used)),
        "host_mem_total":  int(mem_total),
        "host_mem_used":   int(mem_used),
        "host_mem_avail":  max(0, int(mem_total - mem_used)),
        "host_boot_ts":    host_boot_ts,
        "host_uptime_s":   int(uptime),
        # Extended metadata — consumed by the Hosts tab's header row
        # and the SYSTEM / HARDWARE cards when expanded.
        "host_cpu_percent": _num(info.get("cpu")),
        "host_cores":       int(_num(info.get("c"))),
        "host_platform":    str(info.get("p") or info.get("platform") or ""),
        "host_os":          str(info.get("os") or ""),
        "host_kernel":      str(info.get("k") or info.get("kernel") or ""),
        "host_arch":        str(info.get("a") or info.get("arch") or ""),
        "host_agent":       str(info.get("v") or info.get("agent") or ""),
        # Beszel exposes per-mount detail in a separate collection
        # (system_stats); we skip it for the fleet overview and can
        # add a drill-down later if operators want it.
        "mounts":          [],
        "exporter_error":  None,
    }


async def probe_hub(
    base_url: str,
    identity: str,
    password: str,
    verify_tls: bool = True,
    timeout: float = 15.0,
) -> dict:
    """Fetch every system from a Beszel hub, keyed by host name.

    Returns ``{"systems": {hostname: stats_dict, ...}, "error": None}``
    on success, or ``{"systems": {}, "error": "..."}`` on failure. Never
    raises — lets gather.py keep going on any hub hiccup.

    The returned dict's keys come from each Beszel record's ``name``
    field (the label the operator gave the system in Beszel's UI). For
    OmniGrid's node mapping to work, operators should name each
    system in Beszel to match the Docker Swarm hostname.
    """
    if not base_url or not identity or not password:
        return {"systems": {}, "error": "beszel: missing url / identity / password"}
    try:
        async with httpx.AsyncClient(verify=verify_tls, timeout=timeout) as client:
            # Auth → fetch. Retry once on 401 with a forced re-auth in
            # case the cached token expired between cache-set and use.
            token = await _get_token(client, base_url, identity, password)
            try:
                records = await _fetch_systems(client, base_url, token)
            except PermissionError:
                token = await _get_token(
                    client, base_url, identity, password, force_refresh=True,
                )
                records = await _fetch_systems(client, base_url, token)
    except Exception as e:
        return {"systems": {}, "error": str(e)}

    out: dict[str, dict] = {}
    for rec in records:
        name = (rec.get("name") or "").strip()
        if not name:
            continue
        stats = extract_stats(rec.get("info") or {})
        # Carry the top-level status so callers can tell a paused /
        # down system from one that's actually fresh.
        stats["beszel_status"] = rec.get("status") or "unknown"
        out[name] = stats
    return {"systems": out, "error": None}
