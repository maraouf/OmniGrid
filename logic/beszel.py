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

# Module-level dedupe for the per-host "mounts=0 — agent not reporting
# efs" diagnostic. Without this set, every gather cycle re-prints the
# same warning for every host whose Beszel agent lacks
# ``EXTRA_FILESYSTEMS`` — ~30 hosts × every tick = flooded logs. We
# instead print the warning ONCE per (host_key) for the lifetime of the
# process. If mounts start appearing later (operator fixed the agent),
# we clear the host's entry so a future regression would re-warn.
# The "sample system_id has no efs key" line uses its own one-shot
# guard (`_warned_sample_no_efs`) so it doesn't fire every cycle either.
_warned_no_mounts: set[str] = set()
_warned_sample_no_efs: bool = False


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
    """
    url = (base_url.rstrip("/")
           + "/api/collections/systemd_services/records?perPage=500")
    try:
        r = await client.get(url, headers={"Authorization": token})
        if r.status_code == 401:
            raise PermissionError("401")
        if r.status_code == 404:
            # Beszel hub without the systemd_services collection (older
            # version, or feature not enabled). Treat as "no services."
            return []
        if r.status_code >= 400:
            return []
        return list((r.json() or {}).get("items") or [])
    except Exception:
        # Network hiccup → empty. Same treatment as 4xx — silent.
        return []


async def fetch_system_history(
    base_url: str,
    identity: str,
    password: str,
    system_id: str,
    hours: int = 1,
    stat_type: str = "1m",
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
    """
    if not (base_url and identity and password and system_id):
        return {"series": [], "error": "missing hub credentials or system id"}
    # Limit to a sane number — 1h * 60 = 60 rows for type=1m, etc.
    per_page = max(10, min(500, hours * 60))
    filt = f"(system='{system_id}'&&type='{stat_type}')"
    url = base_url.rstrip("/") + "/api/collections/system_stats/records"
    params = {"filter": filt, "sort": "created", "perPage": str(per_page)}
    try:
        async with httpx.AsyncClient(verify=verify_tls, timeout=timeout) as client:
            token = await _get_token(client, base_url, identity, password)
            r = await client.get(url, params=params, headers={"Authorization": token})
            if r.status_code == 401:
                token = await _get_token(
                    client, base_url, identity, password, force_refresh=True,
                )
                r = await client.get(url, params=params, headers={"Authorization": token})
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
            "la1":  la1,  # load avg 1m
            "la5":  la5,  # load avg 5m
            "la15": la15, # load avg 15m
            # Swap usage % — Beszel agents emit `s` for swap percent
            # used (0..100). Hosts without a swap configured emit 0
            # consistently → chart hides on the frontend gate.
            "s":   _num(stats.get("s")),
            # Swap used in GiB — `su` field. Pair with `s` for the chart.
            "su":  _num(stats.get("su")),
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
    r = await client.get(url, params=params, headers={"Authorization": token})
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
    """Pull an architecture suffix (``amd64`` / ``arm64`` / ...) out of a
    kernel string. Returns ``""`` on no match. Matches Beszel's own
    frontend which parses the kernel token for arch because the agent
    doesn't emit arch as a separate field.
    """
    if not kernel:
        return ""
    tail = kernel.rsplit("-", 1)[-1].lower()
    known = ("amd64", "x86_64", "arm64", "aarch64", "armv7l", "armv6l",
             "armhf", "i686", "i386", "riscv64", "ppc64le", "s390x")
    if tail in known:
        return tail
    # Common substring fallback — some distros decorate the kernel with
    # extra tags after the arch (``-pve``, ``-generic``).
    for a in known:
        if a in kernel.lower():
            return a
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
        # the chart (#320) and the SYSTEM card. Empty / missing →
        # zeros (containers and embedded systems often skip this).
        "host_load_1m":     _load_window(stats.get("la"), 0),
        "host_load_5m":     _load_window(stats.get("la"), 1),
        "host_load_15m":    _load_window(stats.get("la"), 2),
        # Swap — Beszel agents emit `s` (swap percent 0..100) and `su`
        # (swap used GiB). Hosts with no swap configured emit 0 and
        # the swap chart hides on the frontend gate.
        "host_swap_percent": _num(stats.get("s")),
        "host_swap_used":    _num(stats.get("su")),
        # Service info (#321). Beszel agents emit the systemd-services
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
            # systemd_services collection (#321). One record per
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
                    print(f"[beszel] systemd_services: {len(svc_records)} records "
                          f"across {len(services_by_system)} systems")
            except Exception as e:
                print(f"[beszel] warn: fetch systemd_services failed: {e}")
                services_by_system = {}
    except Exception as e:
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
                _warned_no_mounts.discard(host_key)
        else:
            # One-shot warning per host for the lifetime of the process.
            # Keeps the "set EXTRA_FILESYSTEMS" hint visible on first
            # probe without flooding the log every gather cycle.
            if host_key not in _warned_no_mounts:
                print(f"[beszel] host={host_key!r} mounts=0 — agent not reporting efs "
                      f"(set EXTRA_FILESYSTEMS env on the Beszel agent to enable "
                      f"multi-mount; this warning is suppressed on subsequent ticks)")
                _warned_no_mounts.add(host_key)
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
