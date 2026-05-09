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

from logic.merge import normalize_arch as _normalize_arch


# In-process token cache so every gather doesn't re-auth. Keyed by
# (base_url, identity) — an operator changing the Hub URL or identity
# in Settings will miss the cache and re-auth, which is correct.
_token_cache: dict[tuple[str, str], dict] = {}

# Module-level dedupe for the per-host "mounts=0 — agent not reporting
# efs" diagnostic. Without this set, every gather cycle re-prints the
# same warning for every host whose Beszel agent lacks
# ``EXTRA_FILESYSTEMS`` — ~30 hosts × every tick = flooded logs. We
# instead print the warning ONCE per (host_key) for the lifetime of the
# process. If mounts start appearing later (operator fixed the agent),
# we clear the host's entry so a future regression would re-warn.
# The "sample system_id has no efs key" line uses its own one-shot
# guard (`_warned_sample_no_efs`) so it doesn't fire every cycle either.
#
# Cardinality cap: a fleet with rotating ephemeral
# hostnames (uncommon but possible — short-lived k8s pods, dev VMs)
# could grow this set unbounded over the lifetime of the process. Cap
# at `_WARNED_NO_MOUNTS_CAP` entries with FIFO eviction via an OrderedDict
# (set has no insertion-ordered eviction). When a NEW host warning is
# about to push the count over the cap, drop the oldest. The warn-once
# semantics survive: a host that was evicted simply gets re-warned the
# next time it shows mounts=0 — fine, since eviction means the process
# has been up long enough that one duplicate log line in N thousand is
# acceptable.
from collections import OrderedDict as _OrderedDict
_WARNED_NO_MOUNTS_CAP = 1024
_warned_no_mounts: "_OrderedDict[str, None]" = _OrderedDict()
_warned_sample_no_efs: bool = False


def _warned_no_mounts_add(host_key: str) -> None:
    """Mark a host as having been warned, evicting the oldest entry
    when the cap is exceeded. Idempotent on re-add (re-inserts at the
    tail so it becomes the most-recently-warned)."""
    if host_key in _warned_no_mounts:
        _warned_no_mounts.move_to_end(host_key)
        return
    _warned_no_mounts[host_key] = None
    while len(_warned_no_mounts) > _WARNED_NO_MOUNTS_CAP:
        _warned_no_mounts.popitem(last=False)


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
    # ``base_url`` is admin-set + validated at probe_hub entry via
    # ``is_safe_http_url`` — see ``logic/url_safety.py`` for the
    # threat-model rationale backing every CodeQL suppression below.
    for path in endpoints:
        try:
            r = await client.post(  # lgtm[py/full-ssrf]
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
    r = await client.get(url, headers={"Authorization": token})  # lgtm[py/full-ssrf]
    if r.status_code == 401:
        # Token expired / revoked — caller will re-auth + retry.
        raise PermissionError("401")
    if r.status_code >= 400:
        raise RuntimeError(f"beszel fetch systems: HTTP {r.status_code}")
    data = r.json() or {}
    return list(data.get("items") or [])


async def _fetch_systemd_services(client, base_url: str, token: str) -> list:
    """Fetch every record from the `systemd_services` PocketBase collection.

    Beszel agents that have systemd-tracking enabled emit one record per
    monitored unit, with shape:

        {
          "id": ..., "name": "nginx",
          "system": "<system_record_id>",
          "state": <int>,        # systemd ActiveState enum
          "sub":   <int>,        # systemd SubState enum
          "cpu":   <float>,
          "memory":<int>,
          ...
        }

    The `state` enum is what we care about for failure detection. Per
    sd-bus / Beszel agent code:
        0 = active
        1 = reloading
        2 = inactive
        3 = failed         ← what we count as "failed"
        4 = activating
        5 = deactivating

    We treat state == 3 as failed; everything else (including
    "inactive" disabled units the operator deliberately stopped) is
    considered non-failed so the badge doesn't flag intentional state.

    Returns the raw list of records. Caller groups by `system` field
    to attach per-host. Empty list on a 401/4xx/network error — the
    caller treats "no systemd_services" as "agent doesn't track them"
    and the drawer row hides cleanly.

    Paginates through every page so a fleet with > 500 services across
    many hosts doesn't get truncated to whichever ones happen to land
    on PocketBase's first page. PB uses 1-based page index and returns
    `totalPages` in the envelope; we walk pages until exhausted, with
    a hard cap of 20 pages (10000 records) as a safety net against a
    runaway fleet or a malformed response.
    """
    base = (base_url.rstrip("/")
            + "/api/collections/systemd_services/records")
    out: list = []
    page = 1
    max_pages = 20  # 20 * 500 = 10000-record safety ceiling
    while page <= max_pages:
        url = f"{base}?perPage=500&page={page}"
        try:
            r = await client.get(url, headers={"Authorization": token})  # lgtm[py/full-ssrf]
        except Exception:
            return out
        if r.status_code == 401:
            raise PermissionError("401")
        if r.status_code == 404:
            return []
        if r.status_code >= 400:
            return out
        env = r.json() or {}
        items = list(env.get("items") or [])
        out.extend(items)
        total_pages = int(env.get("totalPages") or 1)
        if page >= total_pages or not items:
            break
        page += 1
    return out


def _pick_stat_type(hours: int) -> str:
    """Pick the Beszel stat aggregation tier appropriate for the
    requested window. Beszel's PocketBase keeps multiple parallel
    aggregations of system_stats keyed by ``type`` — the ``1m`` rows
    are retained for roughly an hour, ``10m`` for ~12 hours, ``20m``
    for ~a day, ``120m`` for the long tail. Querying ``1m`` for a
    24-hour window returns the last ~1 hour of data only (the rest
    has been aggregated away), which is exactly what the operator
    saw as "the chart only shows the last hour even when I pick 24h".

    Tier picks :
      hours ≤ 1   → ``1m``    (60 rows max)
      hours ≤ 12  → ``10m``   (72 rows max for 12h)
      hours ≤ 48  → ``20m``   (72 rows for 24h, 144 for 48h)
      hours > 48  → ``120m``  (84 rows for 168h / 7d)
    """
    h = max(1, int(hours or 1))
    if h <= 1:
        return "1m"
    if h <= 12:
        return "10m"
    if h <= 48:
        return "20m"
    return "120m"


async def fetch_system_history(
    base_url: str,
    identity: str,
    password: str,
    system_id: str,
    hours: int = 1,
    stat_type: Optional[str] = None,
    verify_tls: bool = True,
    timeout: float = 15.0,
    host_id: Optional[str] = None,
) -> dict:
    """Return the last ``hours`` of ``system_stats`` rows for one system.

    Powers the Hosts tab's expanded time-series charts (CPU / Mem /
    Disk / Net). Filter uses PocketBase's ``(system='ID' && type='1m')``
    syntax and sorts oldest-first so the frontend can render left→right
    without reversing. Result shape:

        {"series": [{"t": epoch_s, "cpu": float, "mp": float,
                      "dp": float, "b": bytes_per_sec, ...}, ...],
         "error": None}

    Non-fatal failures (401, 5xx, network) return an empty series and
    the error string so the UI can show "Collecting data…" instead.

    ``stat_type`` defaults to None; when None, ``_pick_stat_type(hours)``
    selects the right aggregation tier so a 24h request hits ``20m`` rows
    (retained for ~24h) instead of ``1m`` rows (retained for ~1h).
    """
    if not (base_url and identity and password and system_id):
        return {"series": [], "error": "missing hub credentials or system id"}
    # Pick aggregation tier from the window when caller didn't override.
    # Explicit value wins so the test endpoints / operator probes still
    # work the legacy way.
    if stat_type is None:
        stat_type = _pick_stat_type(hours)
    # Limit to a sane number — 1h * 60 = 60 rows for type=1m, etc.
    per_page = max(10, min(500, hours * 60))
    # Escape single quotes in user-controlled values before interpolating
    # into the PocketBase filter string
    # Beszel record IDs and the four
    # known stat_type values are alphanumeric in practice, but a malformed
    # paste OR a future PB schema change with operator-controlled fields
    # would break the query and return an empty series silently. PB
    # doesn't support placeholder bind for arbitrary filter expressions,
    # so escape via doubling — `'` → `''` is the standard SQL-style
    # escape that PB's filter parser accepts.
    safe_system  = str(system_id).replace("'", "''")
    safe_type    = str(stat_type).replace("'", "''")
    filt = f"(system='{safe_system}'&&type='{safe_type}')"
    url = base_url.rstrip("/") + "/api/collections/system_stats/records"
    params = {"filter": filt, "sort": "created", "perPage": str(per_page)}
    try:
        async with httpx.AsyncClient(verify=verify_tls, timeout=timeout) as client:
            token = await _get_token(client, base_url, identity, password)
            r = await client.get(url, params=params, headers={"Authorization": token})  # lgtm[py/full-ssrf]
            if r.status_code == 401:
                token = await _get_token(
                    client, base_url, identity, password, force_refresh=True,
                )
                r = await client.get(url, params=params, headers={"Authorization": token})  # lgtm[py/full-ssrf]
            if r.status_code >= 400:
                return {"series": [], "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"series": [], "error": str(e)}

    items = (r.json() or {}).get("items") or []
    # One-shot diagnostic — dump the first row's stats keys + a sample
    # of values so operators can see what the hub actually exposes for
    # this system. The "Net In/Out chart is flat at 0" support request
    # almost always boils down to "Beszel agent isn't tracking NICs"
    # (needs NICS=eth0 env var); this log reveals that in one line.
    if items:
        first_stats = items[0].get("stats") or {}
        sample_keys = sorted(first_stats.keys())
        net_like = {k: first_stats[k] for k in sample_keys
                    if any(tag in k.lower() for tag in ("n", "b", "rx", "tx", "net"))}
        print(f"[beszel] history system_id={system_id!r} rows={len(items)} "
              f"stats_keys={sample_keys[:25]} net_like={net_like}")
    series: list[dict] = []
    for it in items:
        stats = it.get("stats") or {}
        # Created timestamp → epoch seconds for the frontend.
        created = it.get("created") or ""
        try:
            import datetime as _dt
            # PocketBase emits "2026-04-22 12:34:56.789Z" — normalize.
            iso = created.replace(" ", "T")
            if iso.endswith("Z"):
                iso = iso[:-1] + "+00:00"
            ts = int(_dt.datetime.fromisoformat(iso).timestamp())
        except Exception:
            ts = 0
        # Net recv/send — try multiple field names across Beszel schema
        # versions. ``nr``/``ns`` are newer (v0.10+); ``bi``/``bo`` appear
        # in older dumps; some builds emit nested ``net.rx``/``net.tx``.
        # First truthy pair wins.
        net_obj = stats.get("net") if isinstance(stats.get("net"), dict) else {}
        nr = (_num(stats.get("nr"))
              or _num(stats.get("bi"))
              or _num(stats.get("rx"))
              or _num(net_obj.get("rx"))
              or _num(net_obj.get("in")))
        ns = (_num(stats.get("ns"))
              or _num(stats.get("bo"))
              or _num(stats.get("tx"))
              or _num(net_obj.get("tx"))
              or _num(net_obj.get("out")))
        b = _num(stats.get("b")) or _num(stats.get("bb")) or _num(stats.get("bn"))
        # ``net`` — synthesized so the frontend chart doesn't have to
        # probe two fields. Prefer the recv+send sum when we got it,
        # else the combined bandwidth field. When only ``b`` exists, split
        # it half/half so the In/Out charts aren't identically flat —
        # the operator at least gets a visible signal and can fix the
        # agent config if they want the real split.
        if nr or ns:
            net = nr + ns
        elif b:
            net = b
            if not (nr or ns):
                nr = b / 2
                ns = b / 2
        else:
            net = 0
        # Disk I/O — Beszel agents emit `dr` (read bytes/s) + `dw`
        # (write bytes/s). Newer agents may emit aggregates at the
        # top of stats; older ones nest per-mount under `efs`. Walk
        # both shapes and sum across mounts for a host-wide rate.
        # See `_efs_mounts` for the per-mount extraction shape.
        dr = _num(stats.get("dr"))
        dw = _num(stats.get("dw"))
        if not dr and not dw:
            efs = stats.get("efs") if isinstance(stats.get("efs"), dict) else {}
            for _name, mstats in efs.items():
                if isinstance(mstats, dict):
                    dr += _num(mstats.get("dr"))
                    dw += _num(mstats.get("dw"))
        # Load average — Beszel emits `la` as a 3-element list
        # `[1m, 5m, 15m]`. Some builds use `loadavg` instead. Default
        # to zeros so the chart can render even when the agent doesn't
        # populate it (containers, embedded systems).
        la = stats.get("la") or stats.get("loadavg") or []
        if isinstance(la, list):
            la1  = _num(la[0]) if len(la) > 0 else 0.0
            la5  = _num(la[1]) if len(la) > 1 else 0.0
            la15 = _num(la[2]) if len(la) > 2 else 0.0
        else:
            la1 = la5 = la15 = 0.0
        # Load → percent-of-cores so the chart can render with a 0-100
        # Y-axis (operator-flagged: raw load values like 0.18 looked
        # ambiguous next to CPU% / Memory% cards). Same convention the
        # SNMP Load chart uses. Cores resolved from the system_stats
        # row's threads count when present, falling back to 1 (treat
        # as single-core) so a busy load on an unknown-core machine
        # still surfaces.
        # cores resolution: per-tick `cpus` array length is the
        # most-reliable signal in a `system_stats` row (info.c lives in
        # `system.info`, not `stats`); fall back to explicit `c` /
        # `threads` if a future agent emits them, then 1.
        cpus_arr = stats.get("cpus") if isinstance(stats.get("cpus"), list) else []
        cores = max(1, len(cpus_arr) or int(_num(stats.get("c")) or _num(stats.get("threads")) or 1))
        la1_pct  = min(100.0, (la1  / cores) * 100.0)
        la5_pct  = min(100.0, (la5  / cores) * 100.0)
        la15_pct = min(100.0, (la15 / cores) * 100.0)
        # Compute per-sensor temperatures ONCE per point. Earlier ship
        # called ``_flatten_temperatures(stats.get("t"))`` three times
        # (one for ``temps``, two for ``temp_max``). On a 168h history
        # at 1-minute granularity that's 30k+ wasted parses
        temps = _flatten_temperatures(stats.get("t"))
        # GPU per-tick aggregates. Beszel emits per-GPU dict on
        # every history row — average across GPUs for power / usage
        # (operator-flagged: "GPU Power Draw: Average power consumption
        # of GPUs", "GPU Usage: Average utilization of GPU"); sum
        # across GPUs for VRAM totals so multi-GPU rigs surface their
        # combined memory pressure. Missing `g` → zeros; chart hides
        # on `host_gpus.length > 0` gate.
        g_dict = stats.get("g") if isinstance(stats.get("g"), dict) else {}
        gpu_pwr_sum = 0.0
        gpu_usage_sum = 0.0
        gpu_vram_used_sum = 0.0
        gpu_vram_total_sum = 0.0
        gpu_n = 0
        for _idx, _gpu in g_dict.items():
            if not isinstance(_gpu, dict):
                continue
            gpu_pwr_sum += _num(_gpu.get("p"))
            gpu_usage_sum += _num(_gpu.get("u"))
            gpu_vram_used_sum += _num(_gpu.get("mu")) * 1024 ** 3   # GB → bytes
            gpu_vram_total_sum += _num(_gpu.get("mt")) * 1024 ** 2  # MB → bytes
            gpu_n += 1
        gpu_pwr_avg = (gpu_pwr_sum / gpu_n) if gpu_n else 0.0
        gpu_usage_avg = (gpu_usage_sum / gpu_n) if gpu_n else 0.0
        gpu_vram_pct = (
            (gpu_vram_used_sum / gpu_vram_total_sum * 100.0)
            if gpu_vram_total_sum else 0.0
        )
        series.append({
            "t":   ts,
            "cpu": _num(stats.get("cpu")),
            "mp":  _num(stats.get("mp")),
            "dp":  _num(stats.get("dp")),
            "mu":  _num(stats.get("mu")),   # mem used GiB
            "du":  _num(stats.get("du")),   # disk used GiB
            "b":   b,    # network bytes/s (legacy aggregate)
            "nr":  nr,   # net recv bytes/s (newer)
            "ns":  ns,   # net send bytes/s (newer)
            "net": net,  # preferred aggregate for the net chart
            "dr":  dr,   # disk read bytes/s (host-wide, summed across mounts)
            "dw":  dw,   # disk write bytes/s
            "la1":  la1,  # load avg 1m (raw load — backward compat for callers)
            "la5":  la5,  # load avg 5m
            "la15": la15, # load avg 15m
            # percent-of-cores variants for the host-drawer
            # Load chart (operator wants 0-100 % rendering, not raw
            # 0.18 / 0.22 / 0.18 numbers).
            "la1_pct":  la1_pct,
            "la5_pct":  la5_pct,
            "la15_pct": la15_pct,
            # Swap usage % — Beszel agents emit `s` for swap percent
            # used (0..100). Hosts without a swap configured emit 0
            # consistently → chart hides on the frontend gate.
            "s":   _num(stats.get("s")),
            # Swap used in GiB — `su` field. Pair with `s` for the chart.
            "su":  _num(stats.get("su")),
            # Per-sensor temperatures — Beszel agents emit `stats.t` as
            # a flat ``{sensor_name: celsius}`` dict (e.g. cpu_thermal /
            # core_0 / nvme_composite). The frontend's hostChart helper
            # only knows how to render single-key series, so we ALSO
            # synthesise a `temp_max` scalar (peak across all sensors
            # at this tick) for the chart line. The full `temps` dict
            # rides alongside for the metric-card stats display so the
            # operator can see which sensor is hottest. Missing →
            # empty dict + None scalar; the frontend chart card hides
            # on `Object.keys(temps).length > 0`.
            "temps":    temps,
            "temp_max": max(temps.values()) if temps else 0.0,
            # GPU aggregates — gpu_pwr / gpu_usage / gpu_vram_pct
            # power dedicated per-GPU chart cards. Plus the absolute
            # VRAM used / total (bytes) for the legend value formatting.
            "gpu_pwr":             gpu_pwr_avg,
            "gpu_usage":           gpu_usage_avg,
            "gpu_vram_pct":        gpu_vram_pct,
            "gpu_vram_used_bytes": int(gpu_vram_used_sum),
            "gpu_vram_total_bytes": int(gpu_vram_total_sum),
        })

    # ---- Net I/O fallback from node-exporter samples --------------------
    # If the Beszel agent on this host isn't tracking any NIC (the common
    # ``NICS= unset`` case), every point's ``nr`` / ``ns`` / ``net`` is
    # zero and the Net In/Out chart renders as a flat line. When the
    # operator also has node-exporter configured for this host we have
    # pre-computed rx/tx rates in ``host_net_samples``; swap them in so
    # the chart lights up. Skipped entirely when ``host_id`` isn't
    # supplied (non-hosts callers pass it as None) or when the Beszel
    # series already has real numbers.
    if series and host_id and all((p.get("nr") or 0) == 0 and (p.get("ns") or 0) == 0
                                  for p in series):
        try:
            from logic import host_net_sampler as _hns
            since = min(p["t"] for p in series if p.get("t"))
            ne_samples = _hns.recent_samples(host_id, since - 300)
        except Exception as e:
            ne_samples = []
            print(f"[beszel] net-fallback lookup failed for host_id={host_id!r}: {e}")
        if ne_samples:
            # Nearest-neighbour merge. NE samples land every
            # STATS_SAMPLE_INTERVAL_SECONDS (default 300s) while Beszel's
            # 1m series has ~60s spacing, so we need a window >= half
            # the NE interval so every Beszel point can find a
            # neighbour. 300s matches the default interval — if the
            # operator tightens STATS_SAMPLE_INTERVAL_SECONDS below
            # that, the merge still works; if they widen it, bump this
            # constant to match.
            max_skew = 300
            # ne_samples is oldest-first; convert to a sorted list of ts
            # once so each binary-search-adjacent scan is cheap.
            ne_ts = [s["ts"] for s in ne_samples]
            patched = 0
            for p in series:
                t = p.get("t") or 0
                if not t:
                    continue
                # Linear scan — series is <= hours*60 points, ne_samples
                # similarly bounded. At most a few hundred comparisons.
                best = None
                best_d = max_skew + 1
                for s in ne_samples:
                    d = abs(s["ts"] - t)
                    if d < best_d:
                        best_d = d
                        best = s
                if best is not None and best_d <= max_skew:
                    nr_v = float(best["rx_bytes_per_s"])
                    ns_v = float(best["tx_bytes_per_s"])
                    p["nr"] = nr_v
                    p["ns"] = ns_v
                    p["net"] = nr_v + ns_v
                    patched += 1
            print(f"[beszel] net-fallback host_id={host_id!r} system_id={system_id!r} "
                  f"patched {patched}/{len(series)} points from "
                  f"{len(ne_samples)} NE samples "
                  f"(earliest ne_ts={ne_ts[0] if ne_ts else None})")
        else:
            print(f"[beszel] net-fallback host_id={host_id!r} system_id={system_id!r} "
                  f"— no NE samples in window; chart stays flat")

    return {"series": series, "error": None}


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
    url = base_url.rstrip("/") + "/api/collections/system_stats/records"
    params = {"filter": "(type='1m')", "sort": "-created", "perPage": "500"}
    r = await client.get(url, params=params, headers={"Authorization": token})  # lgtm[py/full-ssrf]
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


def _flatten_efs(efs) -> list[dict]:
    """Turn Beszel's ``extra filesystems`` map into a list.

    Input shape from ``system_stats.stats.efs``:
        {"/mnt/data": {"d": 1000.0, "du": 450.0, "dr": 12.3, "dw": 7.8}}

    Output:
        [{"n": "/mnt/data", "d": 1000.0, "du": 450.0,
          "dp": 45.0, "dr": 12.3, "dw": 7.8}, ...]

    ``dp`` is derived (Beszel doesn't store a per-mount percentage)
    so the bar in the Hosts tab can render without extra work on the
    frontend. Non-dict input returns an empty list.
    """
    if not isinstance(efs, dict):
        return []
    out: list[dict] = []
    for name, stats in efs.items():
        if not isinstance(stats, dict):
            continue
        d = _num(stats.get("d"))
        du = _num(stats.get("du"))
        out.append({
            "n":  str(name),
            "d":  d,
            "du": du,
            "dp": (du / d * 100) if d > 0 else 0.0,
            "dr": _num(stats.get("dr")),
            "dw": _num(stats.get("dw")),
        })
    # Most-full first — the noisy mount is usually the one an operator
    # wants to see.
    out.sort(key=lambda r: r["dp"], reverse=True)
    return out


def _flatten_network(ni) -> list[dict]:
    """Normalize Beszel's ``info.ni`` into [{name, mac, addrs: []}].

    Newer agents (~v0.10+) emit a list of dicts with short keys
    (``n``/``m``/``a``). Older agents emitted bare interface names.
    Both shapes are accepted so the UI template doesn't have to branch.
    """
    if not isinstance(ni, list):
        return []
    out: list[dict] = []
    for item in ni:
        if isinstance(item, str):
            out.append({"name": item, "mac": "", "addrs": []})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("n") or item.get("name") or "").strip()
        if not name:
            continue
        mac = str(item.get("m") or item.get("mac") or "").strip()
        addrs = item.get("a") or item.get("addrs") or []
        if not isinstance(addrs, list):
            addrs = []
        out.append({
            "name":  name,
            "mac":   mac,
            "addrs": [str(a) for a in addrs if a],
        })
    return out


def _flatten_temperatures(t) -> dict[str, float]:
    """Normalise Beszel's ``stats.t`` into a clean ``{sensor: celsius}``
    dict. Beszel agents emit ``t`` as a flat dict keyed by
    sensor name (e.g. ``{"cpu_thermal": 48.2}`` on Raspberry Pi or any
    Linux host with the `node_thermal_zone_temp` collector). Hosts
    whose agent doesn't expose thermal data omit the field entirely;
    we return an empty dict in that case so the frontend chart card
    hides via `Object.keys(...).length > 0`. Filters non-numeric values
    so a malformed reading can't crash the chart renderer downstream.
    """
    if not isinstance(t, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in t.items():
        try:
            celsius = float(v)
        except (TypeError, ValueError):
            continue
        if celsius != celsius:  # NaN guard
            continue
        out[str(k)] = celsius
    return out


def _flatten_gpus(g) -> list[dict]:
    """Normalise Beszel's ``stats.g`` into a clean ``[{name, vram_used_bytes,
    vram_total_bytes, usage_percent, power_watts}, ...]`` list.

    Beszel agents emit ``g`` as a dict keyed by GPU index (string) →
    ``{n, mu, mt, u, p}``. Units are inconsistent in the agent payload:
    ``mu`` (VRAM used) is in GB, ``mt`` (VRAM total) is in MB —
    confirmed against the Beszel agent source. We normalise BOTH to
    bytes so the SPA can use ``fmtBytes`` without per-field unit math.
    Hosts without a discrete GPU emit no ``g`` field; we return an
    empty list so the frontend chart cards hide via
    ``host_gpus.length > 0``.
    """
    if not isinstance(g, dict):
        return []
    out: list[dict] = []
    for idx, gpu in g.items():
        if not isinstance(gpu, dict):
            continue
        try:
            vram_used_gb = float(gpu.get("mu") or 0)
            vram_total_mb = float(gpu.get("mt") or 0)
            usage_pct = float(gpu.get("u") or 0)
            power_w = float(gpu.get("p") or 0)
        except (TypeError, ValueError):
            continue
        out.append({
            "index": str(idx),
            "name": str(gpu.get("n") or ""),
            "vram_used_bytes":  int(vram_used_gb * 1024 ** 3),
            "vram_total_bytes": int(vram_total_mb * 1024 ** 2),
            "usage_percent":    usage_pct,
            "power_watts":      power_w,
        })
    return out


def _load_window(la, idx: int) -> float:
    """Pull a load-average window value (1m / 5m / 15m) from Beszel's
    `la` field. Beszel emits a list `[1m, 5m, 15m]` when the agent has
    load reporting; missing / non-list → 0.0 so the field is always
    numeric for the frontend.
    """
    if not isinstance(la, list) or idx >= len(la):
        return 0.0
    try:
        return float(la[idx])
    except (TypeError, ValueError):
        return 0.0


def _services_summary(services) -> dict:
    """Normalize Beszel's services data into a stable summary shape:

        {"total": N, "failed": F, "failed_names": ["nginx", "redis"]}

    Beszel exposes services through the `systemd_services` PocketBase
    collection (one record per unit). Per-record shape:
        {"name": "nginx", "system": "<system_id>",
         "state": <int>, "sub": <int>, ...}

    The `state` enum (systemd ActiveState):
        0=active, 1=reloading, 2=inactive, 3=failed,
        4=activating, 5=deactivating
    We count `state == 3` as failed. Everything else is healthy or
    transitional (including operator-disabled units sitting at
    `inactive`).

    Also accepts the legacy/string shape `{name, status: "failed"}`
    that some forks may use, so the function is robust to either.

    Anything else (missing list, malformed) → empty summary so the
    drawer badge gates on `total > 0` and hides cleanly.
    """
    if not isinstance(services, list):
        return {"total": 0, "failed": 0, "failed_names": []}
    total = 0
    failed_names: list = []
    for s in services:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or s.get("n") or "").strip()
        if not name:
            continue
        total += 1
        # Two ways the failed flag can land:
        # (a) Beszel canonical: integer `state` field == 3
        # (b) Legacy/fork: string `status` == "failed"
        is_failed = False
        state = s.get("state")
        if isinstance(state, (int, float)) and int(state) == 3:
            is_failed = True
        else:
            status = str(s.get("status") or s.get("s") or "").lower().strip()
            if status == "failed":
                is_failed = True
        if is_failed:
            failed_names.append(name)
    return {
        "total":         total,
        "failed":        len(failed_names),
        "failed_names":  failed_names,
    }


def _derive_arch(kernel: str) -> str:
    """Pull an architecture suffix (``x86_64`` / ``arm64`` / ...) out of a
    kernel string. Returns ``""`` on no match. Matches Beszel's own
    frontend which parses the kernel token for arch because the agent
    doesn't emit arch as a separate field. Pipes the result through
    ``logic.merge.normalize_arch`` so callers always see the canonical
    spelling (e.g. ``amd64`` → ``x86_64``); without that, NE-only hosts
    saw ``x86_64`` while Beszel-only hosts saw ``amd64`` for the same
    physical CPU.
    """
    if not kernel:
        return ""
    tail = kernel.rsplit("-", 1)[-1].lower()
    known = ("amd64", "x86_64", "arm64", "aarch64", "armv7l", "armv6l",
             "armhf", "i686", "i386", "riscv64", "ppc64le", "s390x")
    if tail in known:
        return _normalize_arch(tail)
    # Common substring fallback — some distros decorate the kernel with
    # extra tags after the arch (``-pve``, ``-generic``).
    for a in known:
        if a in kernel.lower():
            return _normalize_arch(a)
    return ""


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
    # When the Beszel agent has reported extra filesystems, prefer
    # their sum for the aggregate. Reason: `stats.d` is the agent's
    # primary disk (often a container / incus overlay that the
    # operator doesn't think of as "the host's disk"), while the
    # operator-configured `EXTRA_FILESYSTEMS` list IS the disk view
    # they want to see. Real-world example caught on a TrueNAS host
    # whose agent ran inside an incus container: stats.d = 861 GB
    # (overlay), efs = {/mnt/POOL1: 5.2 TB, /: 47 GB}. Pre-fix
    # `host_disk_total` was 861 GB and the chip read 0.1% used; the
    # accurate roll-up across the operator's chosen mounts is
    # ~84% used. Sum-from-EFS short-circuits when the EFS list is
    # empty, preserving legacy behaviour for hosts without
    # `EXTRA_FILESYSTEMS=` configured.
    efs_raw = stats.get("efs") if isinstance(stats.get("efs"), dict) else None
    disk_pct_efs: float | None = None
    # Always-on probe-entry diagnostic — prints once per extract_stats
    # call so the operator can verify the deployed image carries this
    # code. Pre-fix the deployment has NO `[beszel] extract-stats` log
    # line whatsoever; post-fix every Beszel host probe emits one. If
    # the operator sees the chip showing pre-fix values AND no
    # `[beszel] extract-stats` line, the running container is on the
    # pre-fix image and a redeploy is the answer.
    try:
        _hk = (info.get("h") or info.get("host") or "?")
        _efs_keys = list(efs_raw.keys()) if efs_raw else []
        print(
            f"[beszel] extract-stats {_hk}: stats.d={_num(stats.get('d')):.1f} GiB "
            f"efs_keys={_efs_keys}"
        )
    except Exception:  # noqa: BLE001
        pass
    if efs_raw:
        efs_total_gib = 0.0
        efs_used_gib = 0.0
        for _name, _entry in efs_raw.items():
            if not isinstance(_entry, dict):
                continue
            efs_total_gib += _num(_entry.get("d"))
            efs_used_gib += _num(_entry.get("du"))
        if efs_total_gib > 0:
            disk_total = efs_total_gib * gib
            disk_used = efs_used_gib * gib
            disk_pct_efs = (efs_used_gib / efs_total_gib * 100.0) if efs_total_gib > 0 else 0.0
            # Verbose diagnostic — confirms the EFS aggregation branch
            # fired AND prints the totals so the operator can verify
            # the chip / chart match. Cheap (one print per probe per
            # EFS-configured host); a fleet-wide grep `[beszel] efs-`
            # in Admin → Logs answers "is the fix actually running on
            # this deploy" without requiring a fresh debug-panel paste.
            try:
                _hk = (info.get("h") or info.get("host") or "?")
                print(
                    f"[beszel] efs-aggregate {_hk}: "
                    f"total={efs_total_gib:.1f} GiB used={efs_used_gib:.1f} GiB "
                    f"({disk_pct_efs:.1f}%) overrides stats.d={_num(stats.get('d')):.1f} GiB"
                )
            except Exception:  # noqa: BLE001
                pass
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
        # When the EFS-derived total replaced stats.d above, the
        # info.dp percent (computed by the agent against stats.d/du)
        # no longer matches — recompute from the EFS sum so the chip
        # value agrees with the new total.
        "host_disk_percent": disk_pct_efs if disk_pct_efs is not None else _num(info.get("dp")),
        "host_cores":       int(_num(info.get("c"))),
        "host_threads":     int(_num(info.get("t"))),
        "host_cpu_model":   str(info.get("m") or ""),
        "host_platform":    str(info.get("p") or info.get("platform") or ""),
        "host_os":          str(info.get("os") or ""),
        "host_kernel":      str(info.get("k") or info.get("kernel") or ""),
        # Beszel doesn't emit architecture as its own field — derive it
        # from the kernel suffix the same way Beszel's own UI does
        # (e.g. "6.12.7+deb13+1-amd64" → "amd64"). Empty when the
        # kernel isn't present either.
        "host_arch":        _derive_arch(info.get("k") or info.get("kernel") or "")
                            or str(info.get("a") or info.get("arch") or ""),
        "host_agent":       str(info.get("v") or info.get("agent") or ""),
        # Per-mount detail. Beszel stores ``extra filesystems`` as a
        # map name → {d, du, dr, dw} on the stats row. We flatten into
        # a list so the frontend can ``x-for`` over it without caring
        # that the source was a dict.
        "mounts":           _flatten_efs(stats.get("efs")),
        # Network interfaces — newer Beszel agents emit ``info.ni`` as
        # a list of {n, m, a} objects (name / mac / addrs). Older
        # agents emitted bare strings. ``_flatten_network`` handles
        # both shapes and returns a uniform list the UI can iterate.
        "network_ifaces":   _flatten_network(info.get("ni")),
        # Current in-flight bandwidth (bytes/s) reported by the agent.
        # Used on the Hosts table for a net-I/O indicator.
        "host_bandwidth":   _num(info.get("b")),
        # Container count — homelab-relevant when a host runs Docker.
        "host_containers":  int(_num(info.get("ct"))),
        # Load average — Beszel agents emit `la` as `[1m, 5m, 15m]` in
        # `stats`. Surfaced as 3 separate fields so the SPA can render
        # the chart and the SYSTEM card. Empty / missing →
        # zeros (containers and embedded systems often skip this).
        "host_load_1m":     _load_window(stats.get("la"), 0),
        "host_load_5m":     _load_window(stats.get("la"), 1),
        "host_load_15m":    _load_window(stats.get("la"), 2),
        # Swap — Beszel agents emit `s` (swap percent 0..100) and `su`
        # (swap used GiB). Hosts with no swap configured emit 0 and
        # the swap chart hides on the frontend gate.
        "host_swap_percent": _num(stats.get("s")),
        "host_swap_used":    _num(stats.get("su")),
        # Temperature sensors. Beszel agents emit `stats.t` as a
        # dict of `<sensor_name>: <celsius>` (e.g. {"cpu_thermal": 48.2}
        # on a Pi 4 / `node_thermal_zone_temp` on Linux). Hosts whose
        # agent doesn't expose any thermal sensor get an empty dict and
        # the frontend chart card hides cleanly.
        "host_temperatures": _flatten_temperatures(stats.get("t")),
        # GPUs. Beszel agents emit `stats.g` as a dict keyed by
        # GPU index → {n, mu, mt, u, p} (name / VRAM used / VRAM total /
        # usage % / power W). Beszel stores `mu` in GB and `mt` in MB
        # (yes, inconsistent — confirmed against agent source). We
        # normalise both to bytes here so the SPA can use `fmtBytes`
        # without per-field unit math. Empty list when the host has
        # no discrete GPU.
        "host_gpus":             _flatten_gpus(stats.get("g")),
        # Service info. Beszel agents emit the systemd-services
        # data under the field name `systemd_services` (operator
        # confirmed by inspecting the PocketBase admin — initial
        # implementation guessed `services` and got nothing back).
        # Try both names so legacy / fork agents that DO use `services`
        # still work, but prefer the canonical Beszel name first.
        # `_services_summary` normalises into `{total, failed, failed_names}`.
        # Hosts whose agent doesn't track services get the empty
        # summary `{total: 0, ...}` and the drawer row hides cleanly.
        "host_services":         _services_summary(
            stats.get("systemd_services")
            or info.get("systemd_services")
            or stats.get("services")
            or info.get("services")
        ),
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
    # Defence-in-depth on the admin-only Beszel hub URL setting. CodeQL
    # py/full-ssrf flags every `client.get(url, ...)` below as the URL
    # flows from a settings field — see ``logic/url_safety.py`` for the
    # threat-model rationale.
    from logic.url_safety import is_safe_http_url as _safe_url
    if not _safe_url(base_url):
        return {
            "systems": {},
            "error": "beszel: invalid url — must be http:// or https:// with a hostname",
        }
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
            # systemd_services collection. One record per
            # monitored unit, related to a system via the `system`
            # field. Group here so the per-system loop below can
            # attach a summary in O(1).
            services_by_system: dict[str, list] = {}
            try:
                svc_records = await _fetch_systemd_services(client, base_url, token)
                for svc in svc_records:
                    sid = str(svc.get("system") or "").strip()
                    if not sid:
                        continue
                    services_by_system.setdefault(sid, []).append(svc)
                if svc_records:
                    sample = svc_records[0]
                    sample_keys = sorted(sample.keys()) if isinstance(sample, dict) else []
                    print(f"[beszel] systemd_services: {len(svc_records)} records "
                          f"across {len(services_by_system)} systems; "
                          f"sample_keys={sample_keys}; "
                          f"sample_system_fk={sample.get('system')!r}; "
                          f"sample_name={sample.get('name')!r}; "
                          f"sample_state={sample.get('state')!r}")
            except Exception as e:
                print(f"[beszel] warn: fetch systemd_services failed: {e}")
                services_by_system = {}
    except Exception as e:
        # Surface the probe failure in stdout so it lands in Admin →
        # Logs. Mirrors the Pulse fix — operators should be
        # able to see WHY the provider is down without grepping the
        # raw container log.
        print(f"[beszel] probe failed: {type(e).__name__}: {e} "
              f"url={base_url!r} verify_tls={verify_tls}")
        return {"systems": {}, "error": str(e)}

    # Log the first system_stats row's ``efs`` contents so an
    # operator can confirm what Beszel is actually sending for each
    # host. An empty ``efs`` on a machine with multiple mounts means
    # the Beszel agent wasn't started with ``EXTRA_FILESYSTEMS=...``.
    # The "no efs key" warning is guarded by a one-shot module flag
    # so it doesn't print every gather cycle — the hint is useful at
    # startup but becomes noise on subsequent ticks.
    if latest_stats:
        global _warned_sample_no_efs
        sample_sid = next(iter(latest_stats))
        sample = latest_stats[sample_sid] or {}
        efs = sample.get("efs")
        if efs:
            shape = "dict" if isinstance(efs, dict) else type(efs).__name__
            keys = list(efs.keys()) if isinstance(efs, dict) else (efs if isinstance(efs, list) else [])
            print(f"[beszel] sample efs for system_id={sample_sid!r}: "
                  f"type={shape} count={len(keys)} keys={keys[:8]}")
            # If ``efs`` reappears after a prior warning (operator set
            # EXTRA_FILESYSTEMS), reset the one-shot so a future
            # regression re-warns.
            _warned_sample_no_efs = False
        elif not _warned_sample_no_efs:
            print(f"[beszel] sample system_id={sample_sid!r} has no 'efs' key — "
                  f"agent probably not configured with EXTRA_FILESYSTEMS "
                  f"(this warning is suppressed on subsequent ticks)")
            _warned_sample_no_efs = True

    out: dict[str, dict] = {}
    services_match_count = 0
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
        # Override `host_services` with cross-collection data from
        # `systemd_services`. extract_stats only sees this system's
        # row; the services live in a separate collection that we
        # fetched + grouped in probe_hub. Hosts whose systemd_services
        # collection is empty (Beszel agent not tracking units) keep
        # the empty `{total: 0, ...}` summary from extract_stats —
        # frontend gates on `total > 0` and hides cleanly.
        svc_records_for_system = services_by_system.get(rec_id) or []
        if svc_records_for_system:
            stats["host_services"] = _services_summary(svc_records_for_system)
            # Raw per-unit list for downstream consumers that need the
            # full detail (the lifespan `host_beszel_sampler` writes
            # one row per unit into `host_beszel_services`, which
            # surfaces the per-service drawer chip + the AI palette
            # context's failed-unit names). Same shape Beszel returns
            # — `{name, state, sub, system, ...}` — so the consumer
            # can also `_services_summary(stats["host_services_raw"])`
            # if it just wants the rolled summary again.
            stats["host_services_raw"] = svc_records_for_system
            services_match_count += 1
        mounts = stats.get("mounts") or []
        if mounts:
            # Positive mount line ALSO used to fire every cycle — kept
            # only when something changed (mount count changed since
            # last probe) or the operator just fixed a previously-zero
            # host. Otherwise the steady-state tick is silent for this
            # host and the log stays readable.
            if host_key in _warned_no_mounts:
                print(f"[beszel] host={host_key!r} mounts={len(mounts)} now reporting "
                      f"(previously empty — agent picked up EXTRA_FILESYSTEMS): "
                      + ", ".join(f"{m.get('n')}={m.get('du'):.1f}/{m.get('d'):.1f} GiB"
                                  for m in mounts[:5]))
                _warned_no_mounts.pop(host_key, None)
        else:
            # One-shot warning per host for the lifetime of the process.
            # Keeps the "set EXTRA_FILESYSTEMS" hint visible on first
            # probe without flooding the log every gather cycle.
            if host_key not in _warned_no_mounts:
                print(f"[beszel] host={host_key!r} mounts=0 — agent not reporting efs "
                      f"(set EXTRA_FILESYSTEMS env on the Beszel agent to enable "
                      f"multi-mount; this warning is suppressed on subsequent ticks)")
                _warned_no_mounts_add(host_key)
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
    # Diagnostic — when systemd_services were fetched but didn't
    # match any system, the most likely cause is that the `system`
    # field on the service records doesn't equal the system records'
    # `id`. Print the first few service-keys vs system-ids so the
    # operator can see the mismatch shape.
    if services_by_system:
        sys_ids = [r.get("id") for r in records if r.get("id")]
        if services_match_count == 0:
            print(f"[beszel] systemd_services attached to 0/{len(out)} hosts — "
                  f"system_ids[:5]={sys_ids[:5]} vs "
                  f"service_system_fks[:5]={list(services_by_system.keys())[:5]}")
        else:
            print(f"[beszel] systemd_services attached to "
                  f"{services_match_count}/{len(out)} hosts")
    return {"systems": out, "error": None}


def lookup(systems_map: dict, needle: str) -> Optional[dict]:
    """Find a Beszel system record by name, tolerating case + whitespace.

    Mirrors :func:`logic.pulse.lookup` so per-host samplers using the
    Beszel hub map can resolve operator-typed aliases like
    ``Docker`` / ``docker`` / ``  docker  `` against the agent-reported
    ``host`` field that ``probe_hub`` keys on. Falls through to a
    stripped+lowercased scan when an exact-key hit misses; returns
    ``None`` on no match. Consumers: :mod:`logic.host_beszel_sampler`
    (per-tick lookup of curated ``beszel_name`` against the hub map);
    test endpoints that probe the hub without going through
    ``_merge_one_host``'s direct ``state["beszel_map"].get(...)``.
    """
    if not systems_map or not needle:
        return None
    if needle in systems_map:
        return systems_map[needle]
    key = needle.strip().lower()
    if not key:
        return None
    for k, v in systems_map.items():
        if k.strip().lower() == key:
            return v
    return None
