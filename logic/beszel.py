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


async def _fetch_latest_stats(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
) -> dict[str, dict]:
    """Return the newest ``system_stats`` record per system id.

    Beszel keeps absolute values (mem_total, disk_total, mem_used,
    disk_used in GiB) in the ``system_stats`` collection — ``system.info``
    only has percentages (mp, dp) and metadata. To populate our
    ``host_*`` byte fields we need to look at the stats table.

    Strategy: pull the most recent 500 ``1m``-type rows (each system
    gets one per minute), then group by system id and keep the newest.
    That's a single HTTP call regardless of fleet size, and for a
    homelab with <=20 systems gives us ~25 minutes of headroom before
    any system's newest row rolls off the buffer.
    """
    url = (base_url.rstrip("/")
           + "/api/collections/system_stats/records"
           + "?filter=(type%3D%271m%27)&sort=-created&perPage=500")
    r = await client.get(url, headers={"Authorization": token})
    if r.status_code == 401:
        raise PermissionError("401")
    if r.status_code >= 400:
        # Stats table failure is non-fatal — we still have percentages
        # from info. Returning {} means the caller degrades gracefully.
        return {}
    items = (r.json() or {}).get("items") or []
    # Items are sorted newest-first; first sighting of a system id wins.
    latest: dict[str, dict] = {}
    for it in items:
        sid = it.get("system")
        if not sid or sid in latest:
            continue
        latest[sid] = it.get("stats") or {}
    return latest


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


def extract_stats(info: dict, stats: Optional[dict] = None) -> dict:
    """Map one Beszel ``info`` (+ latest ``stats``) dict → nodes_info shape.

    Beszel splits data across two places:

    - ``system.info`` holds metadata (hostname, kernel, cores, agent
      version, platform) and PERCENTAGES (``mp``/``dp``/``cpu``). No
      absolute memory or disk totals live here — ``info.m`` is the CPU
      model *string*, not memory used, and ``info.d`` is the dashboard
      version, not disk used. Reading those as numbers silently
      produced zeros, which is why Beszel-mapped nodes used to fall
      back to Docker-only disk / mem in the UI.
    - ``system_stats`` rows hold absolute values in GiB:
      ``m``=mem_total, ``mu``=mem_used, ``d``=disk_total, ``du``=disk_used.
      Fetched separately by :func:`_fetch_latest_stats`.

    This function combines both sources so downstream code gets one
    dict with every ``host_*`` field populated. Either argument may be
    missing or partial — empty fields degrade to 0 / "".
    """
    if not isinstance(info, dict):
        info = {}
    if not isinstance(stats, dict):
        stats = {}
    gib = 1024 ** 3
    # Absolute totals come from the system_stats row's GiB fields.
    mem_total  = _num(stats.get("m"))  * gib
    mem_used   = _num(stats.get("mu")) * gib
    disk_total = _num(stats.get("d"))  * gib
    disk_used  = _num(stats.get("du")) * gib
    # Percentages fallback: if the stats row is absent but info has
    # mp/dp percentages, we still cannot derive absolute bytes — leave
    # them at 0 and let the UI show "—" for those cells.
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
        # and the SYSTEM / HARDWARE cards when expanded. All come from
        # ``info``; ``stats`` is only for absolute numbers above.
        "host_cpu_percent": _num(stats.get("cpu")) or _num(info.get("cpu")),
        "host_mem_percent": _num(info.get("mp")),
        "host_disk_percent": _num(info.get("dp")),
        "host_cores":       int(_num(info.get("c"))),
        "host_threads":     int(_num(info.get("t"))),
        "host_cpu_model":   str(info.get("m") or ""),
        "host_platform":    str(info.get("p") or info.get("platform") or ""),
        "host_os":          str(info.get("os") or ""),
        "host_kernel":      str(info.get("k") or info.get("kernel") or ""),
        "host_arch":        str(info.get("a") or info.get("arch") or ""),
        "host_agent":       str(info.get("v") or info.get("agent") or ""),
        # Per-mount detail is in the stats row under ``efs`` in newer
        # Beszel versions; surface the raw list for future drill-down UIs.
        "mounts":           list(stats.get("efs") or []),
        "exporter_error":   None,
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
            # Absolute mem/disk totals live in a separate collection.
            # Non-fatal — a failure here just means no host_*_total
            # values (UI falls back to percentages / Docker numbers).
            try:
                latest_stats = await _fetch_latest_stats(client, base_url, token)
            except Exception as e:
                print(f"[beszel] warn: fetch stats failed: {e}")
                latest_stats = {}
    except Exception as e:
        return {"systems": {}, "error": str(e)}

    out: dict[str, dict] = {}
    for rec in records:
        # Match against ``host`` first (the hostname the Beszel agent
        # reports from the machine itself — stable and typically what
        # Docker sees too). Fall back to the user-editable ``name``
        # field (just a friendly label in Beszel's UI) and to
        # ``info.h`` (agent-reported hostname) so we never drop a
        # record just because of one missing field.
        info = rec.get("info") or {}
        host_key = (
            (rec.get("host") or "").strip()
            or (info.get("h") or "").strip()
            or (rec.get("name") or "").strip()
        )
        if not host_key:
            continue
        # Merge the latest stats row (if any) into the extract — gives
        # us absolute mem_total / disk_total in bytes, which ``info``
        # alone doesn't carry.
        rec_id = rec.get("id") or ""
        stats = extract_stats(info, latest_stats.get(rec_id))
        # Carry the top-level status so callers can tell a paused /
        # down system from one that's actually fresh.
        stats["beszel_status"] = rec.get("status") or "unknown"
        # Record id + last-updated ISO string power the Hosts view's
        # "Updated Xs ago" sub-line and the deep-link back to Beszel.
        stats["beszel_id"] = rec.get("id") or ""
        stats["beszel_updated"] = rec.get("updated") or ""
        # Friendly name from Beszel (operator-editable). Used as the
        # display label in the Hosts tab while ``host_key`` is the
        # stable identity for alias lookups.
        stats["beszel_name"] = (rec.get("name") or "").strip()
        stats["beszel_host"] = host_key
        out[host_key] = stats
    return {"systems": out, "error": None}
