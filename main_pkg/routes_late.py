"""Middle chunk in the main → main_pkg.routes → main_pkg.routes_late
→ main_pkg.routes_extra star-import chain. See routes_extra's
docstring for the full loading-order explanation.
"""
"""Continuation of `main` (routes module under `main_pkg/`) — extracted to keep main.py under the
line-count "uncomfortable to navigate" threshold. Re-exported via
`from main_routes import *` at the bottom of `main.py`, which pulls
every public symbol (including FastAPI routes registered through
`@app.<verb>(...)`) back into the main namespace.

Loading order:
  1. main.py runs top-to-bottom, defining `app`, every helper,
     and roughly the first half of the routes.
  2. main.py end: `from main_routes import *` triggers main_routes load.
  3. main_routes.py top: `from main import *` pulls EVERY symbol
     main has defined so far (`app`, helpers, Pydantic models,
     etc.) into main_routes's namespace so the route decorators
     below can reference them.
  4. main_routes.py body runs; routes register against the shared
     `app` instance.
  5. main_routes.py finishes; control returns to main.py's star-
     import which now has every main_routes symbol available.
"""
"""
OmniGrid — Portainer-native update dashboard.

Endpoints:
  GET  /api/items                     - All services + containers with status
  GET  /api/item/{raw_id}             - Single item detail
  POST /api/update/stack/{id}         - Update stack (Prune+PullImage)
  POST /api/update/container/{id}     - Recreate standalone container
  POST /api/restart/service/{id}      - Force restart a Swarm service
  GET  /api/ops   /  /api/ops/{id}    - Live operation status
  GET  /api/history                   - Persisted history
  GET  /api/ignores  /  POST  /  DELETE
  GET  /api/settings /  POST
  POST /api/notify-test
  GET  /api/healthz
  GET  /metrics                       - Prometheus scrape endpoint
"""
# Module-wide suppression for the recurring project-pattern lint noise that
# the operator validates and accepts: defensive broad-except guards (project
# convention is to catch + log + continue at API-boundary sites so a single
# broken provider can't 500 the whole route); cross-module `_protected_member`
# access (helpers like `_node_attr` / `_node_matches` / `_load_mappings` /
# `_PROVIDER_PREFIXES` are deliberately shared by main.py without a public
# alias because the indirection isn't worth a re-export); local `e` / `_events`
# / `_gather_mod` / `_stats_mod` shadow names inside `except` clauses and
# lazy-import blocks; explicit `arg=default` kwargs at call sites kept for
# readability of the intended value; missing docstrings on internal FastAPI
# route handlers whose function name + signature is self-describing; the
# `Member 'None' of 'Any | None'` chain reported on every `_admin: auth.User
# = Depends(auth.require_admin)` parameter (PyCharm cannot narrow through
# FastAPI's Depends() injection). Real bugs OUTSIDE these noise classes are
# fixed inline.
import asyncio
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any, Iterable, Optional, Set, cast

# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).
from dotenv import load_dotenv

from logic.env_keys import EnvKey, env_get  # noqa: E402

# Re-import parent's namespace so decorators below find `app`,
# helpers, Pydantic models, etc.
from main import *  # noqa: E402,F401,F403

# Re-import parent's namespace so decorators below find every
# symbol from main + main_pkg.routes.
from main_pkg.routes import *  # noqa: E402,F401,F403



@app.get("/api/hosts/{host_id}/http-probe/history")
async def api_hosts_http_probe_history(
    host_id: str, hours: int = 1,
    *,
    _admin: AdminUser,
):
    """HTTP / TLS / DNS probe time-series for one curated host.

    Returns ``{series: [{t, url, latency_ms, status_ok,
    tls_expires_in_days}, ...], collectors: {...}, error: None}``
    bucketed via the standard `_bucket_drawer_series` helper so the
    chart tail lands at the latest raw-sample timestamp inside each
    bucket (the freshness-label contract — see CLAUDE.md). Window
    clamped to 1..168 hours.

    The ``series`` shape is a flat list of points (NOT a dict
    keyed by URL) — each point carries the URL it came from so the
    SPA can group client-side into one line per URL. Up to ~120
    buckets per URL after bucketing; with N URLs per host the
    response can grow to N×120 rows but the per-row payload is
    tiny (5 fields) so this stays well under 100KB even for
    pathological 20-URL hosts.

    Empty ``series`` when the sampler hasn't written for this host
    yet (newly enabled, master toggle off, no resolvable URLs).
    """
    from logic import host_http_sampler as _hp_sampler
    h = max(1, min(168, int(hours or 1)))
    hid = (host_id or "").strip()
    if not hid:
        return {"series": [], "collectors": {}, "error": "host_id required"}
    since = int(time.time() - h * 3600)
    # Raw row limit — at 5min cadence × ~10 URLs/host × 168h = ~20k
    # rows. Cap at 20k so the SQL query stays cheap; bucketing collapses
    # to ~120 per URL anyway.
    raw_limit = max(500, h * 600)
    try:
        rows = _hp_sampler.recent_samples(hid, since, limit=raw_limit)
    except Exception as e:  # noqa: BLE001
        return {"series": [], "collectors": {}, "error": f"host_http_sampler: {e}"}
    if not rows:
        return {"series": [], "collectors": {}, "error": None}
    # Group raw rows by URL so each URL's series can be independently
    # bucketed — uniform density per line regardless of how many URLs
    # the host probes.
    by_url: dict[str, list] = {}
    for r in rows:
        url = r.get("url") or ""
        if not url:
            continue
        by_url.setdefault(url, []).append({
            "t": int(r.get("ts") or 0),
            "url": url,
            "latency_ms": r.get("latency_ms"),
            "status_ok": bool(r.get("status_ok")),
            "tls_expires_in_days": r.get("tls_expires_in_days"),
        })
    series_out: list[dict] = []
    for url, pts in by_url.items():
        # Each URL gets its own pass through `_bucket_drawer_series` so
        # the bucketing handles per-URL density independently. The
        # helper already emits each bucket's point at the latest raw-ts
        # inside that bucket — the freshness-label contract holds.
        bucketed = _bucket_drawer_series(pts, h)
        series_out.extend(bucketed)
    # Sort by ts ascending overall so consumers don't need to. URL
    # grouping is preserved by the SPA via point.url, not by order.
    series_out.sort(key=lambda p: p.get("t") or 0)
    return {"series": series_out, "collectors": {"sample_count": len(rows), "urls": len(by_url)}, "error": None}


@app.post("/api/hosts/{host_id}/http-probe/test")
async def api_hosts_http_probe_test_row(
    host_id: str,
    _admin: AdminUser,
):
    """Per-row HTTP / TLS / DNS test — fires against THIS row's
    configured URLs.

    Backend resolves the URL list from `hosts_config[host_id]`
    using the same chain the sampler uses
    (``http_probe.urls`` override OR ``url + services[].url``
    fallback). Probes each URL via :func:`probe_http_health` under
    the existing :class:`Cooldown` protection so a misconfigured
    host can't lock its auth out. Does NOT write to
    ``host_http_samples`` — the test is a diagnostic affordance,
    not a sampler tick.

    Returns ``{results: [{url, ok, status_code, latency_ms,
    tls_expires_in_days, error}, ...], elapsed_ms, error}``.
    """
    from logic import http_probe as _http_probe
    from logic.host_http_sampler import curated_http_probe_hosts as _curated
    hid = (host_id or "").strip()
    if not hid:
        raise HTTPException(400, "host_id required")
    # Find the curated row for this host via the sampler's shared
    # URL-resolver helper. Keeps the sampler + manual-test paths in
    # sync on resolution rules (override vs fallback) without
    # duplicating the walk logic.
    matching = [h for h in _curated() if h.get("id") == hid]
    if not matching:
        return {
            "results": [],
            "elapsed_ms": 0,
            "error": "host has no HTTP probe URLs configured (enable http_probe + add URLs OR ensure url / services[].url is set)",
        }
    host_cfg = matching[0]
    urls = list(host_cfg.get("urls") or [])
    if not urls:
        return {"results": [], "elapsed_ms": 0, "error": "no URLs resolved for this host"}
    timeout = float(tuning.tuning_int(Tunable.HTTP_PROBE_TIMEOUT_SECONDS))
    dns_timeout = float(tuning.tuning_int(Tunable.HTTP_PROBE_DNS_TIMEOUT_SECONDS))
    content_match = host_cfg.get("content_match")
    codes = host_cfg.get("accepted_status_codes") or []
    verify_tls = bool(host_cfg.get("verify_tls", True))
    started = time.monotonic()
    # Probe each URL in parallel — same bounded shape the sampler uses.
    sem = asyncio.Semaphore(max(1, tuning.tuning_int(Tunable.HTTP_PROBE_CONCURRENCY)))

    async def _one(url: str) -> dict:
        async with sem:
            try:
                r = await _http_probe.probe_http_health(
                    url,
                    timeout=timeout,
                    dns_timeout=dns_timeout,
                    content_match=content_match,
                    accepted_status_codes=codes,
                    verify_tls=verify_tls,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                r = {
                    "ok": False,
                    "status_code": None,
                    "latency_ms": None,
                    "tls_expires_in_days": None,
                    "error": f"{type(e).__name__}: {str(e)[:120]}",
                }
            r["url"] = url
            return r

    results = await asyncio.gather(*(_one(u) for u in urls))
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {"results": results, "elapsed_ms": elapsed_ms, "error": None}


@app.get("/api/hosts/{host_id}/beszel/services")
async def api_hosts_beszel_services(
    host_id: str,
    _admin: AdminUser,
):
    """Per-unit Beszel systemd-services snapshot for one curated host.

    Surfaces the data the lifespan ``host_beszel_sampler`` writes
    into ``host_beszel_services``: one row per (host, unit) pair with
    ``state`` (systemd ActiveState enum: 0=active, 1=reloading,
    2=inactive, 3=failed, 4=activating, 5=deactivating), ``sub_state``
    (systemd SubState enum), ``last_seen_ts`` (most recent tick that
    observed the unit), and ``last_change_ts`` (most recent tick where
    the state value changed — drives "failed since 2h ago" copy in
    the drawer without keeping a transition log).

    Returns ``{services: [...], error: None}``. Failed units come
    first (state=3), then alphabetical by unit name. Hosts that have
    never been Beszel-probed OR whose Beszel agent doesn't track
    systemd units return an empty list.

    Distinct from the rolled summary on ``host.services`` (total /
    failed count / failed_names) which the host API row already
    carries — this endpoint is for the drawer's per-unit detail
    pane + the AI palette's "what's the state of nginx on web01?"
    questions.
    """
    hid = (host_id or "").strip()
    if not hid:
        raise HTTPException(400, "host_id required")
    from logic import host_beszel_sampler as _hbs
    try:
        services = _hbs.services_for_host(hid)
    except Exception as e:  # noqa: BLE001
        return {"services": [], "error": f"host_beszel_services: {e}"}
    return {"services": services, "error": None}


@app.get("/api/hosts/{host_id}/snmp/history")
async def api_hosts_snmp_history(
    host_id: str, hours: int = 1,
    *,
    _admin: AdminUser,
):
    """SNMP time-series for one curated host.

    Returns ``{points: [...], error: None}`` with one row per
    ``host_snmp_samples`` entry in the window. Each point carries
    ``ts``, ``cpu_per_core`` (parsed JSON list of int 0..100),
    ``cpu_used_pct`` (UCD ssCpuIdle-derived smoother value),
    ``load_1m`` / ``load_5m`` / ``load_15m`` (floats), ``mem_total`` /
    ``mem_used`` / ``mem_buffers`` / ``mem_cached`` / ``mem_free``
    (bytes). Empty list when this host has never been SNMP-probed.
    Window clamped to 1..168 hours.
    """
    import json as _json
    h = max(1, min(168, int(hours or 1)))
    hid = (host_id or "").strip()
    if not hid:
        return {"points": [], "error": "host_id required"}
    since = int(time.time() - h * 3600)
    # Target ~200 points per chart regardless of window. Raw samples at
    # the default 5-min cadence produce ~2000 points over the 7d window
    # — too many to render legibly, and the line reads as noise. For
    # `hours > 24` bucket the rows server-side via GROUP BY (ts/bucket)
    # so the SPA receives a smoothed, sensibly-spaced series. Beszel's
    # PocketBase already does this via its `_pick_stat_type` aggregation
    # tier; this brings the local sampler-backed providers in line.
    bucket = 0
    # Bucket more aggressively: any window where the natural raw count
    # exceeds the target density gets server-side aggregation. At the
    # default 5-min SNMP cadence (300s), 6h+ windows accumulate >72 raw
    # points and start crowding the 420px-wide SVG. Bucketing to a
    # uniform target of ~120 points hides micro-gaps and produces a
    # consistent visual density across 1h / 6h / 24h / 7d. The 2h
    # threshold below mirrors the ping endpoint — short windows stay
    # raw because they're already chart-friendly.
    if h > 2:
        target_points = 120
        bucket = max(60, int(h * 3600 / target_points))
    try:
        with db_conn() as c:
            if bucket > 0:
                # SQL-level bucketing — AVG numeric fields, drop the
                # JSON cpu_per_core (can't average a list cell). The
                # bucket key is `ts / bucket` integer-divided; we emit
                # the bucket's MIN(ts) as the canonical timestamp.
                rows = c.execute(
                    "SELECT MAX(ts) AS ts, NULL AS cpu_per_core, "
                    "AVG(cpu_used_pct) AS cpu_used_pct, "
                    "AVG(load_1m) AS load_1m, AVG(load_5m) AS load_5m, AVG(load_15m) AS load_15m, "
                    "AVG(mem_total) AS mem_total, AVG(mem_used) AS mem_used, "
                    "AVG(mem_buffers) AS mem_buffers, AVG(mem_cached) AS mem_cached, AVG(mem_free) AS mem_free, "
                    "MAX(uptime_s) AS uptime_s, "
                    "MAX(net_rx_total_bytes) AS net_rx_total_bytes, MAX(net_tx_total_bytes) AS net_tx_total_bytes, "
                    "MAX(printer_page_count) AS printer_page_count, "
                    "AVG(load_percent) AS load_percent, AVG(battery_percent) AS battery_percent, "
                    "AVG(battery_temp_c) AS battery_temp_c, "
                    "AVG(disk_total) AS disk_total, AVG(disk_used) AS disk_used "
                    "FROM host_snmp_samples "
                    "WHERE host_id=? AND ts >= ? "
                    "GROUP BY ts / ? "
                    "ORDER BY ts ASC LIMIT ?",
                    (hid, since, bucket, h * 60),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT ts, cpu_per_core, cpu_used_pct, "
                    "load_1m, load_5m, load_15m, "
                    "mem_total, mem_used, mem_buffers, mem_cached, mem_free, "
                    "uptime_s, net_rx_total_bytes, net_tx_total_bytes, "
                    "printer_page_count, load_percent, battery_percent, "
                    "battery_temp_c, disk_total, disk_used "
                    "FROM host_snmp_samples "
                    "WHERE host_id=? AND ts >= ? "
                    "ORDER BY ts ASC LIMIT ?",
                    (hid, since, h * 60),
                ).fetchall()
    except Exception as e:
        return {"points": [], "error": f"snmp_history: {e}"}
    points = []
    for r in rows:
        try:
            cores = _json.loads(r[1]) if r[1] else []
        except (ValueError, TypeError):
            cores = []
        points.append({
            "ts": int(r[0]),
            "cpu_per_core": cores,
            "cpu_used_pct": (float(r[2]) if r[2] is not None else None),
            "load_1m": (float(r[3]) if r[3] is not None else None),
            "load_5m": (float(r[4]) if r[4] is not None else None),
            "load_15m": (float(r[5]) if r[5] is not None else None),
            "mem_total": (int(r[6]) if r[6] is not None else None),
            "mem_used": (int(r[7]) if r[7] is not None else None),
            "mem_buffers": (int(r[8]) if r[8] is not None else None),
            "mem_cached": (int(r[9]) if r[9] is not None else None),
            "mem_free": (int(r[10]) if r[10] is not None else None),
            # uptime in seconds; NULL for pre-uptime-column rows.
            "uptime_s": (int(r[11]) if r[11] is not None else None),
            # cumulative IF-MIB ifHCInOctets / ifHCOutOctets
            # sums; NULL for pre-throughput-column rows or when SNMP didn't return
            # the counters (e.g. switch with hrStorage but no IF-MIB).
            # Chart layer computes per-pair deltas → bps; out-of-bounds
            # / negative deltas (counter wrap, reboot) are skipped.
            "net_rx_total_bytes": (int(r[12]) if r[12] is not None else None),
            "net_tx_total_bytes": (int(r[13]) if r[13] is not None else None),
            # printer lifetime page count (Printer-MIB
            # prtMarkerLifeCount). Cumulative monotonic counter; the
            # SPA computes deltas → pages-per-day.
            "printer_page_count": (int(r[14]) if r[14] is not None else None),
            # APC UPS time-series fields. NULL for non-UPS hosts
            # or pre-fix rows. Drives the Output Load / Battery /
            # Battery temperature charts in the host drawer's UPS card.
            "load_percent": (float(r[15]) if r[15] is not None else None),
            "battery_percent": (float(r[16]) if r[16] is not None else None),
            "battery_temp_c": (float(r[17]) if r[17] is not None else None),
            # Aggregate disk totals (bytes). Drives the Hosts-row
            # disk sparkline for SNMP-only hosts. NULL for pre-fix
            # rows; SPA computes percent as (used / total) * 100
            # and treats null/0 totals as "no signal".
            "disk_total": (int(r[18]) if r[18] is not None else None),
            "disk_used": (int(r[19]) if r[19] is not None else None),
        })
    # Surface `bucket_seconds` to the SPA so rate-computation helpers
    # (`snmpThroughputBpsSeries` / iface throughput / etc.) can scale
    # their gap-detection threshold to the actual server-side bucket
    # cadence. 0 when the response wasn't bucketed (≤2h windows pass
    # raw samples through). Without this, the SPA's static 3600s cap
    # rejected EVERY consecutive-pair delta on 7d windows (5040s
    # buckets) and the throughput chart fell through to "Collecting
    # data" forever.
    return {"points": points, "error": None, "bucket_seconds": bucket}


@app.get("/api/hosts/{host_id}/disk-projection")
async def api_hosts_disk_projection(
    host_id: str, hours: int = 720,
    *,
    _admin: AdminUser,
):
    """Linear-projection disk-fill forecast for one curated host.

    Reads `disk_used` / `disk_total` history from whichever sampler
    table has rows for this host (priority: ``host_metrics_samples``
    → ``host_pulse_samples`` → ``host_webmin_samples``). Computes
    ordinary-least-squares linear regression of ``used_pct`` over time;
    projects forward by the same window as the lookback. Confidence
    pill derives from R² + sample density:
        high   ≥ 0.85 R²  AND  ≥ 60 samples
        medium ≥ 0.60 R²  OR   ≥ 30 samples
        low    otherwise
    Exhaustion timestamp = first projected point where used_pct ≥ 100;
    None when slope ≤ 0 (host is shrinking / stable) OR projection
    stays under 100% within the forward window (operator has time).
    Lookback ``hours`` clamped 24..2160 (1d..90d).
    """
    h = max(24, min(2160, int(hours or 720)))
    hid = (host_id or "").strip()
    if not hid:
        return {"error": "host_id required"}
    since = int(time.time() - h * 3600)
    # Pick the sampler source whose latest `disk_total` is LARGEST
    # — that matches the canonical "this is what my pool looks like"
    # value the operator (and the AI palette context) sees on the
    # host row, rather than picking a root-disk-only NE slice when
    # the host's primary surface is a multi-TB Pulse-reported pool.
    # Pre-fix the priority order was (NE → Pulse → Webmin) first-wins
    # on count ≥ 5, which silently chose NE's root-disk view on a
    # PVE host whose actual storage is the ZFS pool reported via Pulse.
    # Operator-facing symptom: "AI says 96% / 975 GB but the chart
    # says 12% / 195 GB" — different sources, same host.
    sources = (
        ("host_metrics_samples", "node_exporter"),
        ("host_pulse_samples", "pulse"),
        ("host_webmin_samples", "webmin"),
    )
    candidates: list[tuple[str, list]] = []  # (label, rows) for each source with ≥ 5 rows
    try:
        with db_conn() as c:
            for table, label in sources:
                cur = c.execute(
                    f"SELECT ts, disk_used, disk_total FROM {table} "
                    f"WHERE host_id=? AND ts>=? AND disk_used IS NOT NULL "
                    f"  AND disk_total IS NOT NULL AND disk_total > 0 "
                    f"ORDER BY ts ASC",
                    (hid, since),
                )
                fetched = cur.fetchall()
                if len(fetched) >= 5:
                    candidates.append((label, fetched))
    except Exception as e:
        return {"error": f"db read failed: {e}", "samples": [], "projection": []}
    rows: list = []
    source_used = None
    if candidates:
        # Pick by largest most-recent `disk_total` — the "biggest pool"
        # heuristic. Tie-break by sample count (more samples = more
        # signal). The previous source-priority list is the final
        # fallback when totals + counts are identical.
        priority_order = {src_label: i for i, (_, src_label) in enumerate(sources)}

        def _rank(c_label_rows):
            """Sort key for candidate (label, rows) tuples — bigger
            latest total first, then more samples, then source-priority."""
            rank_label, rs = c_label_rows
            latest_total = int(rs[-1][2] or 0)
            return -latest_total, -len(rs), priority_order.get(rank_label, 999)

        candidates.sort(key=_rank)
        source_used, rows = candidates[0]
    if not rows:
        return {
            "host_id": hid,
            "samples": [],
            "projection": [],
            "exhaustion_ts": None,
            "confidence": "low",
            "current": None,
            "slope_pct_per_day": 0.0,
            "total_bytes": None,
            "source": None,
            "error": None,
        }
    # Build the (ts, used_pct, used_bytes, total_bytes) series.
    series = [
        (int(r[0]), (int(r[1]) / int(r[2])) * 100.0, int(r[1]), int(r[2]))
        for r in rows
    ]
    n = len(series)
    # OLS regression on (ts, used_pct). Anchor x at the first sample
    # so the magnitudes stay small (otherwise int(time.time())^2 in
    # the sums overflows float64 precision noticeably).
    t0 = series[0][0]
    xs = [(s[0] - t0) for s in series]
    ys = [s[1] for s in series]
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = (n * sxx) - (sx * sx)
    if denom == 0:
        slope = 0.0
        intercept = sy / n
    else:
        slope = (n * sxy - sx * sy) / denom
        intercept = (sy - slope * sx) / n
    # R² on the same series.
    mean_y = sy / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 0.0 if ss_tot == 0 else max(0.0, 1.0 - (ss_res / ss_tot))
    # Residual standard error + Sxx for the prediction-interval band.
    # The band widens as we extrapolate further from the historical
    # mean (`mean_x`) — it's the classical OLS forecast cone shape.
    # Uses 1.96 (≈ 95% one-tailed normal critical value) as the
    # multiplier; with n typically ≥ 30 the t-distribution is close
    # enough to the normal that the simpler constant reads cleanly.
    mean_x = sx / n
    sxx_dev = sum((x - mean_x) ** 2 for x in xs)
    sigma_resid = math.sqrt(max(0.0, ss_res) / max(1, n - 2)) if n >= 3 else 0.0
    # Confidence pill — needs both R² AND sample density.
    if r2 >= 0.85 and n >= 60:
        confidence = "high"
    elif r2 >= 0.60 or n >= 30:
        confidence = "medium"
    else:
        confidence = "low"
    # Exhaustion: solve intercept + slope * (t - t0) = 100 for t.
    exhaustion_ts: int | None = None
    if slope > 1e-12:
        t_exhaust_offset = (100.0 - intercept) / slope
        t_exhaust = t0 + t_exhaust_offset
        # Only meaningful if it's in the future (post-now) AND within
        # a "reasonable" forecast window (10× the lookback — beyond
        # that the linear-extrapolation assumption is too fragile).
        now_ts = int(time.time())
        max_horizon = now_ts + (h * 3600 * 10)
        if now_ts < t_exhaust < max_horizon:
            exhaustion_ts = int(t_exhaust)
    # Slope rendered as pct-per-day for the operator-friendly summary.
    slope_pct_per_day = slope * 86400.0
    # Build the response samples — downsample to ≤ 120 points so the
    # chart payload stays small. Take evenly-spaced indices.
    if n <= 120:
        out_samples = series
    else:
        step = n / 120.0
        idxs = sorted({int(i * step) for i in range(120)} | {n - 1})
        out_samples = [series[i] for i in idxs]
    samples_payload = [
        {"ts": s[0], "used_pct": round(s[1], 2),
         "used_bytes": s[2], "total_bytes": s[3]}
        for s in out_samples
    ]
    # Projection — same forward window length as lookback, ~30 points.
    # Each point carries the central prediction PLUS a 95% prediction
    # interval (low_pct / high_pct) so the SPA can render the classic
    # forecast-cone "fork" widening with extrapolation distance —
    # operators see uncertainty at a glance instead of a misleading
    # single-line prediction.
    forward_secs = h * 3600
    projection_payload = []
    proj_steps = 30
    now_ts = int(time.time())
    z = 1.96  # ≈ 95% one-tailed normal critical value
    for i in range(proj_steps + 1):
        t_offset = (forward_secs * i) / proj_steps
        ts_proj = now_ts + int(t_offset)
        x_proj = ts_proj - t0
        used_pct_proj = intercept + slope * x_proj
        # Standard error of prediction at this x — widens with
        # distance from the historical mean. When sxx_dev is 0 (all
        # samples at one timestamp, degenerate), fall back to a flat
        # band based on the residual std alone.
        if sxx_dev > 0 and n >= 3:
            se_pred = sigma_resid * math.sqrt(
                1.0 + (1.0 / n) + ((x_proj - mean_x) ** 2) / sxx_dev
            )
        else:
            se_pred = sigma_resid
        margin = z * se_pred
        used_pct_clamped = max(0.0, min(100.0, used_pct_proj))
        low_clamped = max(0.0, min(100.0, used_pct_proj - margin))
        high_clamped = max(0.0, min(100.0, used_pct_proj + margin))
        projection_payload.append({
            "ts": ts_proj,
            "used_pct": round(used_pct_clamped, 2),
            "low_pct": round(low_clamped, 2),
            "high_pct": round(high_clamped, 2),
        })
    last = series[-1]
    # Stale-data hint — the latest sample's age vs now. If the most
    # recent sample is older than the typical sampler interval × N,
    # the underlying provider has likely stopped reporting (paused /
    # auth failure / network outage) and the projection is operating
    # on a frozen snapshot. The frontend renders a badge when
    # `stale=True`. Threshold = 30 minutes; samplers run every 5 min
    # by default so anything older than 30 min is suspect.
    now_ts = int(time.time())
    last_ts = int(last[0] or 0)
    age_seconds = max(0, now_ts - last_ts) if last_ts > 0 else 0
    is_stale = age_seconds > 1800  # 30 minutes
    return {
        "host_id": hid,
        "samples": samples_payload,
        "projection": projection_payload,
        "exhaustion_ts": exhaustion_ts,
        "confidence": confidence,
        "current": {
            "ts": last[0],
            "used_pct": round(last[1], 2),
            "used_bytes": last[2],
            "total_bytes": last[3],
        },
        "slope_pct_per_day": round(slope_pct_per_day, 4),
        "total_bytes": last[3],
        "source": source_used,
        "lookback_hours": h,
        "r2": round(r2, 4),
        "sample_count": n,
        "stale": is_stale,
        "stale_age_seconds": age_seconds,
        "error": None,
    }


@app.get("/api/hosts/{host_id}/snmp/iface_history")
async def api_hosts_snmp_iface_history(
    host_id: str, hours: int = 1,
    *,
    _admin: AdminUser,
):
    """Per-interface SNMP counter history for one host.

    Returns ``{ifaces: {ifname: [points...]}, error: null}`` with one
    series per interface. Each point carries ``ts`` + ``in_bytes`` +
    ``out_bytes`` (cumulative IF-MIB counters; the chart layer
    computes per-pair deltas → bps with skip-don't-synthesize on
    out-of-bounds). Empty dict when this host has never been
    SNMP-probed for interfaces. Window clamped to 1..168 hours.
    """
    h = max(1, min(168, int(hours or 1)))
    hid = (host_id or "").strip()
    if not hid:
        return {"ifaces": {}, "error": "host_id required"}
    since = int(time.time() - h * 3600)
    # Server-side bucketing on long windows so the per-port chart
    # doesn't accumulate ~2000 points per iface × 5 ifaces ≈ 10k points
    # at 7d. Same shape as the SNMP main-history endpoint: bucket
    # threshold is `h > 2`, target ~120 points per iface, bucket
    # cadence = max(60, hours*3600/120). MAX of in_bytes / out_bytes /
    # link_speed_mbps in each bucket because the counters are
    # MONOTONIC — last-value-in-bucket is what the rate computation
    # needs.
    bucket = 0
    if h > 2:
        target_points = 120
        bucket = max(60, int(h * 3600 / target_points))
    try:
        with db_conn() as c:
            if bucket > 0:
                # Bucket key includes `ifname` so each interface gets
                # its own ~120-point series (otherwise the GROUP BY
                # would mix samples across ifaces in the same time
                # bucket).
                rows = c.execute(
                    "SELECT MAX(ts) AS ts, ifname, "
                    "MAX(in_bytes) AS in_bytes, MAX(out_bytes) AS out_bytes, "
                    "MAX(link_speed_mbps) AS link_speed_mbps "
                    "FROM host_snmp_iface_samples "
                    "WHERE host_id=? AND ts >= ? "
                    "GROUP BY ifname, ts / ? "
                    "ORDER BY ifname ASC, ts ASC LIMIT ?",
                    (hid, since, bucket, h * 60 * 64),
                ).fetchall()
            else:
                # Row-count ceiling — guard against runaway payload on a
                # 48-port switch × 168-hour window. h * 60 samples/hr × 64
                # ifaces is a safe upper bound; the index on (host_id, ts
                # DESC) makes this read fast but the JSON payload + chart-
                # build loop are still linear in row count.
                rows = c.execute(
                    "SELECT ts, ifname, in_bytes, out_bytes, link_speed_mbps "
                    "FROM host_snmp_iface_samples "
                    "WHERE host_id=? AND ts >= ? "
                    "ORDER BY ifname ASC, ts ASC LIMIT ?",
                    (hid, since, h * 60 * 64),
                ).fetchall()
    except Exception as e:
        return {"ifaces": {}, "error": f"snmp_iface_history: {e}"}
    ifaces: dict = {}
    for r in rows:
        ifname = r[1]
        if ifname not in ifaces:
            ifaces[ifname] = []
        ifaces[ifname].append({
            "ts": int(r[0]),
            "in_bytes": (int(r[2]) if r[2] is not None else None),
            "out_bytes": (int(r[3]) if r[3] is not None else None),
            # slice 4 — IF-MIB ifHighSpeed (Mbps); NULL when the
            # device doesn't expose it.
            "link_speed_mbps": (int(r[4]) if r[4] is not None else None),
        })
    # Surface `bucket_seconds` so the SPA's rate-computation helper
    # (`snmpIfaceBpsSeries`) can scale its dt cap to the bucket cadence.
    # 0 when the response wasn't bucketed (≤2h windows pass raw rows
    # through).
    return {"ifaces": ifaces, "error": None, "bucket_seconds": bucket}


@app.get("/api/hosts/{host_id}/snmp/temp_history")
async def api_hosts_snmp_temp_history(
    host_id: str, hours: int = 1,
    *,
    _admin: AdminUser,
):
    """Per-temperature-probe SNMP history for one host.

    Returns ``{probes: {probe_idx: {name, points: [...]}}, error: null}``
    with one series per probe (probe_idx is the trailing OID index,
    stable across ticks; probe_name is the human-readable label like
    "Inlet Temp" / "CPU1 Temp"). Each point carries ``ts`` + ``c``
    (degrees Celsius). Window clamped to 1..168 hours; row-count ceiling
    is hours × 60 × 16 (assume up to 16 probes per server which covers
    every Dell PowerEdge generation we've seen).
    """
    h = max(1, min(168, int(hours or 1)))
    hid = (host_id or "").strip()
    if not hid:
        return {"probes": {}, "error": "host_id required"}
    since = int(time.time() - h * 3600)
    # Server-side bucketing — same pattern as snmp_history / iface_history.
    # 7d × 12 samples/hr × 16 probes = ~32k raw points before bucketing.
    # Temperature readings are INSTANTANEOUS (not counters), so AVG per
    # bucket is the right aggregate. Threshold `h > 2`; target ~120
    # points per probe.
    bucket = 0
    if h > 2:
        target_points = 120
        bucket = max(60, int(h * 3600 / target_points))
    try:
        with db_conn() as c:
            if bucket > 0:
                # Bucket key includes `probe_idx` so each probe's series
                # stays separate. MAX(probe_name) picks any value within
                # the bucket — operator-renames are rare so consistency
                # across the bucket is preserved.
                rows = c.execute(
                    "SELECT MAX(ts) AS ts, probe_idx, "
                    "MAX(probe_name) AS probe_name, AVG(value_c) AS value_c "
                    "FROM host_snmp_temp_samples "
                    "WHERE host_id=? AND ts >= ? "
                    "GROUP BY probe_idx, ts / ? "
                    "ORDER BY probe_idx ASC, ts ASC LIMIT ?",
                    (hid, since, bucket, h * 60 * 16),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT ts, probe_idx, probe_name, value_c "
                    "FROM host_snmp_temp_samples "
                    "WHERE host_id=? AND ts >= ? "
                    "ORDER BY probe_idx ASC, ts ASC LIMIT ?",
                    (hid, since, h * 60 * 16),
                ).fetchall()
    except Exception as e:
        return {"probes": {}, "error": f"snmp_temp_history: {e}"}
    probes: dict = {}
    for r in rows:
        idx = str(r[1] or "")
        if not idx:
            continue
        name = r[2] or f"temp-{idx}"
        probe_bucket: dict = probes.setdefault(idx, {"name": name, "points": []})
        # Pick the freshest probe_name we've seen — operator-renamed
        # probes (rare) propagate forward this way.
        probe_bucket["name"] = name
        probe_bucket["points"].append({
            "ts": int(r[0]),
            "c": (float(r[3]) if r[3] is not None else None),
        })
    # Surface bucket cadence for consistency with the sibling endpoints
    # (SPA doesn't currently consume it for temp charts — temperatures
    # are instantaneous values not counter rates — but the field is
    # available for future symmetry).
    return {"probes": probes, "error": None, "bucket_seconds": bucket}


@app.get("/api/hosts/{host_id}/triage")
async def api_hosts_triage(
    host_id: str,
    hours: int = 720,
    *,
    _admin: AdminUser,
):
    """Similar-incidents grouping for one host. Walks history /
    notifications / host_failure_events for the last ``hours``
    (default 720 = 30 days, range 1..2160), classifies each error via
    `logic.triage._classify_error()` into one of ~10 buckets (timeout
    / auth / refused / dns / tls / not-found / server-error / network
    / parse / rate-limit / other), and groups by (provider, pattern).

    Each group returns: count, first_ts, last_ts, avg_duration_s
    (computed by pairing host_failure_events paused→recovered rows),
    sample_errors (capped at 3 distinct strings), occurrences (capped
    at 50 ts+error pairs). Sorted newest-first (last_ts DESC + count
    DESC as tiebreaker).

    Drives the host drawer's "Similar incidents" panel — operators
    used to scroll-hunt across History tab + the per-host Timeline +
    Admin → Logs to triangulate "is this the same problem"; the
    panel does the work upfront.
    """
    curated = _load_hosts_config()
    if not next((x for x in curated if x.get("id") == (host_id or "").strip()), None):
        raise HTTPException(404, f"Host not found: {host_id}")
    from logic import triage as _triage
    return _triage.triage_host(host_id, hours=hours)


@app.get("/api/hosts/{host_id}/timeline")
async def api_hosts_timeline(
    host_id: str,
    hours: int = 168,
    limit: int = 200,
    *,
    _admin: AdminUser,
):
    """Unified per-host event timeline for incident triage.

    Aggregates four signal sources keyed to ``host_id``:

    * ``history`` rows where ``target_id`` matches the host id OR the
      target name resolves to the host (covers the ``op_type='ssh_run'``
      / ``'snmp_resume'`` / etc. surfaces that target the host
      directly).
    * ``notifications`` rows whose ``target_kind == 'host'`` AND
      ``target_id == host_id``.
    * ``host_failure_state`` snapshots for both the bare host_id row
      and every per-provider ``<provider>:<host_id>`` row — these
      surface as ``provider_paused`` events keyed off the row's
      ``paused_at`` (or ``last_failure_ts`` when present).
    * ``host_provider_last_ok`` rows — surfaces as ``provider_recovered``
      events (last successful probe per provider).

    Returns the merged stream sorted newest-first with a unified
    envelope per event:

    .. code-block:: json

        {
          "events": [
            {
              "ts": 1714500000,
              "kind": "op",                  // op | notification | provider_paused | provider_recovered
              "severity": "success",         // success | error | warning | info
              "title": "Container update",
              "body": "stack/web — pulled :latest",
              "actor": "alice",
              "metadata": {...}
            }
          ],
          "counts": {"ops": 12, "notifications": 5, "failures": 2, "recoveries": 3},
          "host_id": "<id>",
          "hours": 168
        }
    """
    h = max(1, min(720, int(hours or 168)))  # 30-day max
    lim = max(10, min(2000, int(limit or 200)))
    hid = (host_id or "").strip()
    if not hid:
        raise HTTPException(400, "host_id required")
    curated = _load_hosts_config()
    row = next((r for r in curated if r.get("id") == hid), None)
    if row is None:
        raise HTTPException(404, f"Host not found: {hid}")
    # Display name candidates — used for fuzzy-matching history rows
    # whose `target_id` doesn't exactly match (e.g. older
    # ssh_run rows persisted target_name=hostname instead of host_id).
    # Free-form fields (label / *_name) can collide across hosts —
    # operator types "Web server" as the label of host A AND host B,
    # the OR clause downstream would surface B's history rows in A's
    # timeline. Filter the extras to only names THIS host has actually
    # used in past history rows (target_id=hid) so cross-host
    # collisions can't bleed across timelines. The bare hid is always
    # preserved; legacy rows with target_id=NULL but target_name=label
    # can still match iff the same label has been associated with this
    # host_id at some point.
    name_candidates: set[str] = {hid}
    extras: set[str] = set()
    for k in ("label", "snmp_name", "beszel_name", "pulse_name", "webmin_name"):
        v = (row.get(k) or "").strip()
        if v:
            extras.add(v)
    since = int(time.time() - h * 3600)
    events: list[dict] = []
    counts = {"ops": 0, "notifications": 0, "failures": 0, "recoveries": 0}

    try:
        with db_conn() as c:
            # Pre-filter the free-form name candidates. On a DB error,
            # fall back to including every extra (legacy behaviour) —
            # over-matching is preferable to under-matching on a
            # transient DB blip.
            if extras:
                try:
                    used_rows = c.execute(
                        "SELECT DISTINCT target_name FROM history "
                        "WHERE target_id=? AND target_name IS NOT NULL",
                        (hid,),
                    ).fetchall()
                    used = {r[0] for r in used_rows if r[0]}
                    name_candidates |= (extras & used)
                except (sqlite3.Error, ValueError, TypeError):
                    name_candidates |= extras
            # ---- ops history ------------------------------------------
            # The placeholders literal is built from the constant `?`
            # character — `name_candidates` only controls the COUNT,
            # never the placeholder STRING. Static analysers
            # (CodeQL, semgrep python.django.security.audit.sqli) flag
            # any f-string SQL builder regardless. The value is safely
            # parameterised below; we suppress with bandit's `# nosec`
            # marker so the audit trail makes the rationale explicit
            # at the call site instead of forcing a contrived rebuild
            # without f-strings (which would just make the SQL harder
            # to read).
            ph_count = max(1, len(name_candidates))
            placeholders = ",".join(["?"] * ph_count)
            hist_sql = (
                "SELECT ts, op_type, status, actor, target_name, target_id, "
                "target_stack, error, duration "
                "FROM history "
                "WHERE ts >= ? AND ("
                "  target_id IN (" + placeholders + ") "
                                                    "  OR target_name IN (" + placeholders + ")"
                                                                                             ") "
                                                                                             "ORDER BY ts DESC LIMIT ?"
            )  # nosec B608 — placeholders is a constant `?` string built from len(name_candidates); no taint flows from name_candidates into the SQL string itself.
            hist_rows = c.execute(
                hist_sql,
                (since, *name_candidates, *name_candidates, lim),
            ).fetchall()
            for r in hist_rows:
                status = (r["status"] or "").lower()
                severity = "success" if status == "success" else "error" if status == "error" else "info"
                op_type = r["op_type"] or "op"
                title_target = r["target_name"] or r["target_id"] or hid
                # Specialised event-kind for port_scan / port_scan_refresh
                # so the SPA's chip + icon helpers render them distinctly
                # instead of as generic "Op". Both surface as a single
                # `port_scan` kind on the timeline (the
                # `port_scan_refresh` aggregate row is per-tick / per-
                # schedule and isn't keyed by host_id, so it doesn't
                # appear here — only the per-host `port_scan` rows from
                # the shared scan helper do, regardless of whether the
                # scan was operator-initiated or schedule-fired).
                event_kind = "port_scan" if op_type == "port_scan" else "op"
                events.append({
                    "ts": int(r["ts"]),
                    "kind": event_kind,
                    "severity": severity,
                    "title": f"{op_type}",
                    "body": f"{title_target}" + (f" — {r['error']}" if r['error'] else ""),
                    "actor": r["actor"] or "system",
                    "metadata": {
                        "op_type": op_type,
                        "status": status,
                        "target_name": r["target_name"],
                        "target_id": r["target_id"],
                        "target_stack": r["target_stack"],
                        "duration": r["duration"],
                    },
                })
                counts["ops"] += 1

            # ---- notifications ----------------------------------------
            notif_rows = c.execute(
                "SELECT id, ts, event, severity, title, body, actor, "
                "target_kind, target_id, metadata "
                "FROM notifications "
                "WHERE ts >= ? AND target_kind = 'host' AND target_id = ? "
                "ORDER BY ts DESC LIMIT ?",
                (since, hid, lim),
            ).fetchall()
            for r in notif_rows:
                events.append({
                    "ts": int(r["ts"]),
                    "kind": "notification",
                    "severity": (r["severity"] or "info").lower(),
                    "title": r["title"] or r["event"] or "notification",
                    "body": r["body"] or "",
                    "actor": r["actor"] or "",
                    "metadata": {
                        "id": int(r["id"]),
                        "event": r["event"] or "",
                    },
                })
                counts["notifications"] += 1

            # ---- failure transition events ----------------------------
            # Pre-fix this synthesised `provider_paused` /
            # `provider_recovered` events from the CURRENT snapshot of
            # `host_failure_state` + `host_provider_last_ok`, which
            # meant a host that paused → resumed → paused → resumed
            # over the requested window only showed the LATEST state,
            # not the sequence. The new `host_failure_events`
            # append-only table captures every transition and replaces
            # both the failure-state and last-ok branches with one
            # ordered query. Schema-fallback path: if the table is
            # missing (e.g. a pre-migration deploy), we silently fall
            # back to the legacy current-state snapshot logic so the
            # timeline doesn't break during the rollout window.
            try:
                transition_rows = c.execute(
                    "SELECT ts, host_id, provider, kind, error, actor "
                    "FROM host_failure_events "
                    "WHERE host_id = ? AND ts >= ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (hid, since, lim),
                ).fetchall()
            except sqlite3.OperationalError:
                # Pre-migration deploy where host_failure_events table
                # doesn't exist yet — fall back to empty + the legacy
                # current-state snapshot path below.
                transition_rows = []
            if transition_rows:
                for r in transition_rows:
                    kind_raw = (r["kind"] or "").lower()
                    provider = (r["provider"] or "").strip() or "host"
                    if kind_raw == "paused":
                        events.append({
                            "ts": int(r["ts"]),
                            "kind": "provider_paused",
                            "severity": "warning",
                            "title": f"{provider} sampling paused",
                            "body": (r["error"] or "auto-paused after consecutive failures")[:300],
                            "actor": r["actor"] or "sampler",
                            "metadata": {"provider": provider},
                        })
                        counts["failures"] += 1
                    elif kind_raw == "recovered":
                        events.append({
                            "ts": int(r["ts"]),
                            "kind": "provider_recovered",
                            "severity": "success",
                            "title": f"{provider} sampling recovered",
                            "body": "Probe succeeded — auto-pause cleared",
                            "actor": r["actor"] or "sampler",
                            "metadata": {"provider": provider},
                        })
                        counts["recoveries"] += 1
            else:
                # ---- legacy fallback: current-state snapshot only -----
                # Used when host_failure_events is empty (fresh install
                # before any transitions have been logged) OR when the
                # SELECT raised (table missing pre-migration). Keeps the
                # timeline functional during rollout; once the new table
                # has events the fallback never fires.
                try:
                    fail_rows = c.execute(
                        "SELECT provider, paused, paused_at, last_failure_ts, "
                        "last_error, consecutive_failures "
                        "FROM host_failure_state WHERE host_id = ?",
                        (hid,),
                    ).fetchall()
                except (sqlite3.Error, ValueError, TypeError):
                    fail_rows = []
                for r in fail_rows:
                    provider = (r["provider"] or "").strip() or "host"
                    if not r["paused"]:
                        continue
                    ts = int(r["paused_at"] or r["last_failure_ts"] or 0)
                    if ts < since:
                        continue
                    events.append({
                        "ts": ts,
                        "kind": "provider_paused",
                        "severity": "warning",
                        "title": f"{provider} sampling paused",
                        "body": (r["last_error"] or "auto-paused after consecutive failures")[:300],
                        "actor": "sampler",
                        "metadata": {
                            "provider": provider,
                            "consecutive_failures": int(r["consecutive_failures"] or 0),
                        },
                    })
                    counts["failures"] += 1
                try:
                    ok_rows = c.execute(
                        "SELECT provider, last_ok_ts "
                        "FROM host_provider_last_ok WHERE host_id = ?",
                        (hid,),
                    ).fetchall()
                except (sqlite3.Error, ValueError, TypeError):
                    ok_rows = []
                for r in ok_rows:
                    provider = (r["provider"] or "").strip() or "host"
                    ts = int(r["last_ok_ts"] or 0)
                    if ts < since:
                        continue
                    events.append({
                        "ts": ts,
                        "kind": "provider_recovered",
                        "severity": "success",
                        "title": f"{provider} probe ok",
                        "body": "Last successful probe",
                        "actor": "sampler",
                        "metadata": {"provider": provider},
                    })
                    counts["recoveries"] += 1
    except (sqlite3.Error, OSError, RuntimeError) as agg_err:
        print(f"[hosts] timeline {hid!r} aggregation error: {agg_err}")
        # Partial result — return what we have so far rather than 500.

    events.sort(key=lambda evt_row: evt_row["ts"], reverse=True)
    if len(events) > lim:
        events = events[:lim]

    return {
        "host_id": hid,
        "hours": h,
        "events": events,
        "counts": counts,
    }


# ---- Multi-host bulk-action endpoints --------------------------------
# Powers the Hosts main view's sticky bulk-action bar. Each endpoint
# accepts ``{host_ids: [...]}`` plus action-specific payload, validates
# every id against the curated ``hosts_config`` list, applies the
# change, persists the list, and returns
# ``{ok: bool, applied: [ids...], skipped: [ids...], errors: {id: msg}}``
# so the SPA can surface partial-success states.


class HostsBulkPauseIn(BaseModel):
    host_ids: list[str]


class HostsBulkResumeIn(BaseModel):
    host_ids: list[str]


class HostsBulkSnmpVendorsIn(BaseModel):
    host_ids: list[str]
    vendors: list[str]  # subset of _VALID_VENDOR_KEYS, [] = clear (auto-detect)
    mode: str = "set"  # "set" (replace) | "add" (union) | "remove" (difference)


class HostsBulkSnmpTunablesIn(BaseModel):
    host_ids: list[str]
    walk_concurrency: Optional[int] = None
    wall_clock_budget: Optional[int] = None
    clear: bool = False  # when true, REMOVES the per-host override (falls back to global tunable)


def _bulk_write_history_rows(
    host_ids: list[str], *,
    op_type: str, actor: str, started_ts: float,
) -> None:
    """Write one history audit row per host for a bulk action.

    Pre-fix the bulk-pause / bulk-resume endpoints left no audit trail
    — only the per-host endpoints wrote rows. Bulk callers therefore
    couldn't trace "who paused which 50 hosts at 03:14" through the
    Admin → History or per-host Timeline surfaces. This helper closes
    that gap by writing one row per matched host with a 'hosts' kind
    so the existing `target_kind` filter buckets them correctly.

    Best-effort — a per-row insert failure is logged and skipped so a
    single corrupt row never makes the bulk response 500. Same shape
    as the SCHEDULER_ACTOR / `_run_*` runners' history-row pattern.
    """
    if not host_ids:
        return
    _ops_mod.assert_op_type(op_type)
    try:
        with db_conn() as c:
            c.executemany(
                "INSERT INTO history "
                "(ts, op_type, target_kind, target_name, target_id, "
                " target_stack, status, duration, events, error, actor) "
                "VALUES (?, ?, 'hosts', ?, ?, NULL, 'success', 0.0, ?, NULL, ?)",
                [
                    (started_ts, op_type, hid, hid, "[]", actor)
                    for hid in host_ids
                ],
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts:bulk] history write failed for {op_type}: {e}")


def _bulk_resolve_host_ids(host_ids: list[str], curated: list[dict]) -> tuple[list[str], list[str]]:
    """Return (matched_ids, missing_ids) by intersecting the requested
    set against the curated hosts_config index. Order preserved from
    the input list. De-duplicated."""
    by_id = {h.get("id"): h for h in curated}
    seen: set[str] = set()
    matched: list[str] = []
    missing: list[str] = []
    for raw in (host_ids or []):
        hid = (raw or "").strip()
        if not hid or hid in seen:
            continue
        seen.add(hid)
        if hid in by_id:
            matched.append(hid)
        else:
            missing.append(hid)
    return matched, missing


# ---------------------------------------------------------------------------
# Step-up reauth — single-flight short-lived tokens for bulk-destructive
# admin actions. Operator-stressed: a SweetAlert-only "are you sure?"
# is too easy to click through when the action affects N hosts at once.
# Higher-stakes endpoints (currently only `/api/hosts/bulk/pause`) take
# a `X-OmniGrid-Reauth-Token` header that the SPA mints by POSTing to
# `/api/admin/reauth` with the operator's local password just before
# the action. Tokens are single-use and TTL'd at 300s so a leaked
# header on a long-lived tab doesn't unlock arbitrary writes.
#
# Authentik / SSO users have no local password — the reauth endpoint
# returns a specific error code (`OG_REAUTH_NO_LOCAL_PASSWORD`) so the
# SPA can fall back to the existing typed-hostname / SweetAlert confirm
# without surfacing a misleading "wrong password" toast.
# ---------------------------------------------------------------------------
import secrets as _reauth_secrets  # noqa: E402

_REAUTH_TTL_SECONDS = 300

# Map of token → (user_id, expires_at). Single-replica safe (no
# horizontal scale), so an in-memory map is fine. Tokens are deleted on
# first successful use (single-use semantics).
_reauth_tokens: dict[str, tuple[int, float]] = {}


def _reauth_prune() -> None:
    """Drop expired tokens from the in-memory map. Called opportunistically
    on every mint + verify so the map can't grow unbounded. Single-pass
    over the map; cheap (typical N is single-digit per-process).
    """
    now = time.time()
    expired = [t for t, (_uid, exp) in _reauth_tokens.items() if exp <= now]
    for t in expired:
        _reauth_tokens.pop(t, None)


class ReauthIn(BaseModel):
    password: str


@app.post("/api/admin/reauth")
async def api_admin_reauth(
    body: ReauthIn,
    _request: Request,
    u: AdminUser,
):
    """Mint a short-lived reauth token for the calling admin.

    Verifies the supplied password against the user's stored bcrypt
    hash. SSO / Authentik users have no local password; the response
    code (`OG_REAUTH_NO_LOCAL_PASSWORD`) lets the SPA fall back to
    the typed-hostname confirm path on bulk pause.
    """
    _reauth_prune()
    pw = (body.password or "").strip()
    if not pw:
        raise HTTPException(400, detail="password required")
    # Pull the user's local password hash. Authentik / SSO users have
    # NULL or empty hashes — surface the distinct error so the SPA can
    # branch UI paths.
    try:
        with db_conn() as c:
            row = c.execute(
                "SELECT password_hash FROM users WHERE id = ?",
                (u.id,),
            ).fetchone()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, detail=f"reauth lookup failed: {e}") from e
    stored = (row and row["password_hash"]) or ""
    if not stored:
        return {
            "ok": False,
            "error_code": "OG_REAUTH_NO_LOCAL_PASSWORD",
            "detail": "Local password is not set for this account "
                      "(SSO user). Use the typed-hostname confirm path.",
        }
    if not auth.verify_password(pw, stored):
        # Audit the failure path — success is invisible by design (reauth
        # is a stepping stone). A per-event audit row surfaces who-tried-
        # when so the operator can spot brute-force-like patterns without
        # spelunking the per-IP login limiter's logs.
        try:
            with db_conn() as c:
                _ops_mod.write_admin_audit(
                    c, "admin_reauth_failed",
                    target_kind="auth", target_name=u.username, target_id=str(u.id),
                    actor=u.username, status="error", error="reauth failed",
                    message=f"admin reauth failed for {u.username}",
                )
        except Exception as e:
            print(f"[auth] admin_reauth_failed audit-row write failed: {e}")
        # Don't differentiate "wrong password" from "no user" — same
        # generic message reduces password-probing signal. The local-
        # auth login rate-limiter already covers brute force; we
        # don't double-rate-limit here because the reauth endpoint
        # requires an already-authenticated admin session.
        raise HTTPException(403, detail="reauth failed")
    token = _reauth_secrets.token_urlsafe(32)
    _reauth_tokens[token] = (int(u.id), time.time() + _REAUTH_TTL_SECONDS)
    return {
        "ok": True,
        "token": token,
        "expires_in": _REAUTH_TTL_SECONDS,
    }


def _user_has_local_password(user_id: int) -> bool:
    """Return True iff the user has a stored bcrypt password hash.

    SSO / Authentik users have NULL or empty hashes in the local
    `users` table (auth happens upstream via OIDC). For those users
    the reauth gate is meaningless — there's no password to verify
    against — so the dependency falls back to the typed-confirm path
    in the SPA without requiring a token.
    """
    try:
        with db_conn() as c:
            row = c.execute(
                "SELECT password_hash FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
    except (sqlite3.Error, ValueError, TypeError):
        return False
    return bool(row and (row["password_hash"] or "").strip())


def _require_reauth(request: Request, u: AdminUser) -> auth.User:
    """Dependency that consumes a `X-OmniGrid-Reauth-Token` header.

    Single-use: the token is deleted on first successful verify so a
    tab that leaks the header to JS console output can't replay.
    Bound to the originating user's id, so a token minted for admin A
    can't be replayed by admin B even if it leaked across sessions.

    SSO users (no local password hash) bypass the reauth check —
    there's no local password to verify against. The SPA's typed-
    hostname / typed-count confirm path is the sole gate for those
    callers; this dependency degrades gracefully so an Authentik
    admin doesn't get locked out of bulk operations.
    """
    if not _user_has_local_password(u.id):
        return u
    _reauth_prune()
    token = request.headers.get("X-OmniGrid-Reauth-Token", "").strip()
    if not token:
        raise HTTPException(
            401,
            detail="reauth required — POST /api/admin/reauth with your password",
        )
    pair = _reauth_tokens.get(token)
    if pair is None:
        raise HTTPException(401, detail="reauth token invalid or expired")
    user_id, expires_at = pair
    if time.time() >= expires_at:
        _reauth_tokens.pop(token, None)
        raise HTTPException(401, detail="reauth token expired")
    if int(user_id) != int(u.id):
        # Token doesn't match this caller — mismatch is a security
        # signal worth surfacing as a distinct 403 rather than the
        # generic 401, but only for debug. Operators don't routinely
        # see this without something genuinely off.
        raise HTTPException(403, detail="reauth token mismatch")
    # Single-use — burn the token now. A second attempt (legitimate
    # retry on an idempotent endpoint) needs a fresh reauth round-trip.
    _reauth_tokens.pop(token, None)
    return u
"""Continuation of `main` (routes module under `main_pkg/`) — extracted to keep main.py under the
line-count "uncomfortable to navigate" threshold. Re-exported via
`from main_routes import *` at the bottom of `main.py`, which pulls
every public symbol (including FastAPI routes registered through
`@app.<verb>(...)`) back into the main namespace.

Loading order:
  1. main.py runs top-to-bottom, defining `app`, every helper,
     and roughly the first half of the routes.
  2. main.py end: `from main_routes import *` triggers main_routes load.
  3. main_routes.py top: `from main import *` pulls EVERY symbol
     main has defined so far (`app`, helpers, Pydantic models,
     etc.) into main_routes's namespace so the route decorators
     below can reference them.
  4. main_routes.py body runs; routes register against the shared
     `app` instance.
  5. main_routes.py finishes; control returns to main.py's star-
     import which now has every main_routes symbol available.
"""
"""
OmniGrid — Portainer-native update dashboard.

Endpoints:
  GET  /api/items                     - All services + containers with status
  GET  /api/item/{raw_id}             - Single item detail
  POST /api/update/stack/{id}         - Update stack (Prune+PullImage)
  POST /api/update/container/{id}     - Recreate standalone container
  POST /api/restart/service/{id}      - Force restart a Swarm service
  GET  /api/ops   /  /api/ops/{id}    - Live operation status
  GET  /api/history                   - Persisted history
  GET  /api/ignores  /  POST  /  DELETE
  GET  /api/settings /  POST
  POST /api/notify-test
  GET  /api/healthz
  GET  /metrics                       - Prometheus scrape endpoint
"""
# Module-wide suppression for the recurring project-pattern lint noise that
# the operator validates and accepts: defensive broad-except guards (project
# convention is to catch + log + continue at API-boundary sites so a single
# broken provider can't 500 the whole route); cross-module `_protected_member`
# access (helpers like `_node_attr` / `_node_matches` / `_load_mappings` /
# `_PROVIDER_PREFIXES` are deliberately shared by main.py without a public
# alias because the indirection isn't worth a re-export); local `e` / `_events`
# / `_gather_mod` / `_stats_mod` shadow names inside `except` clauses and
# lazy-import blocks; explicit `arg=default` kwargs at call sites kept for
# readability of the intended value; missing docstrings on internal FastAPI
# route handlers whose function name + signature is self-describing; the
# `Member 'None' of 'Any | None'` chain reported on every `_admin: auth.User
# = Depends(auth.require_admin)` parameter (PyCharm cannot narrow through
# FastAPI's Depends() injection). Real bugs OUTSIDE these noise classes are
# fixed inline.
import asyncio
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any, Iterable, Optional, Set, cast

# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).
from dotenv import load_dotenv

from logic.env_keys import EnvKey, env_get  # noqa: E402

# Re-import parent's namespace so decorators below find `app`,
# helpers, Pydantic models, etc.
from main import *  # noqa: E402,F401,F403



@app.post("/api/hosts/bulk/pause")
async def api_hosts_bulk_pause(
    body: HostsBulkPauseIn,
    request: Request,
    _u: auth.User = Depends(_require_reauth),
):
    """Mark every host in the request as auto-paused. Inserts/updates
    a row in ``host_failure_state`` with ``paused=1`` and
    ``paused_at=now`` so the lifespan-managed sampler short-circuits
    on the next tick. Idempotent — already-paused hosts return as
    ``applied`` so the bar's count badge stays consistent.
    """
    curated = _load_hosts_config()
    matched, missing = _bulk_resolve_host_ids(body.host_ids, curated)
    now = int(time.time())
    applied: list[str] = []
    errors: dict[str, str] = {}
    actor = _actor_from(request) or "admin"
    # Single transaction for the whole batch — pre-fix this opened
    # one db_conn() per host inside the loop. For 200 selected hosts
    # that was 200 SQLite write transactions; SQLite's WAL handles it
    # but the round-trip cost adds up. One outer connection +
    # `executemany` for the bulk INSERT-OR-UPDATE batches all writes
    # into a single transaction. Per-row failures (rare — schema-
    # constraint violations on a row whose hid was de-duped at boundary)
    # fall back to the per-row try/except path so partial-success
    # error reporting still works without losing a whole batch on one
    # bad row.
    pause_tag = f"manually paused by {actor}"
    if matched:
        try:
            with db_conn() as c:
                rows = [
                    (hid, float(now), pause_tag)
                    for hid in matched
                ]
                # ``first_failure_ts`` is NOT NULL on the schema. On the
                # INSERT path (host had no prior streak) we use the
                # SENTINEL ``0.0`` rather than ``now`` — a manual pause
                # is not a real failure event, so the
                # host_metrics_sampler's "is the failure window
                # expired?" math should not treat this row as a fresh
                # streak. ON CONFLICT path leaves ``first_failure_ts``
                # untouched so an EXISTING failure streak's start-time
                # isn't rewritten by a manual click.
                c.executemany(
                    "INSERT INTO host_failure_state "
                    "(host_id, provider, first_failure_ts, "
                    " consecutive_failures, paused, paused_at, last_error) "
                    "VALUES (?, '', 0.0, 0, 1, ?, ?) "
                    "ON CONFLICT(host_id, provider) DO UPDATE SET "
                    "paused = 1, paused_at = excluded.paused_at, "
                    "last_error = excluded.last_error",
                    rows,
                )
            applied = list(matched)
        except Exception as batch_err:  # noqa: BLE001
            # Batch failed (rare — likely DB-level error like disk
            # full). Fall back to per-row writes so partial success
            # is still possible + we get per-id error attribution.
            print(f"[hosts:bulk] pause batch failed, falling back to "
                  f"per-row: {batch_err}")
            for hid in matched:
                try:
                    with db_conn() as c:
                        c.execute(
                            "INSERT INTO host_failure_state "
                            "(host_id, provider, first_failure_ts, "
                            " consecutive_failures, paused, paused_at, last_error) "
                            "VALUES (?, '', 0.0, 0, 1, ?, ?) "
                            "ON CONFLICT(host_id, provider) DO UPDATE SET "
                            "paused = 1, paused_at = excluded.paused_at, "
                            "last_error = excluded.last_error",
                            (hid, float(now), pause_tag),
                        )
                    applied.append(hid)
                except Exception as e:  # noqa: BLE001
                    errors[hid] = str(e)
    # Per-host audit rows in `history` so Admin → History + the host
    # drawer's Timeline tab pick up bulk actions (pre-fix bulk pause/
    # resume left no audit trail — only the per-host endpoints did).
    # `target_kind='hosts'` matches the migration-#3 backfill rule for
    # `hosts_bulk_*` op_types. Best-effort; one bad row doesn't break
    # the response.
    if applied:
        _bulk_write_history_rows(
            applied, op_type="hosts_bulk_pause",
            actor=actor, started_ts=float(now),
        )
    # Publish ONE bulk SSE event so cross-tab observers reconcile N
    # rows from a single frame instead of N separate
    # `host:failure_state_changed` events. The SPA handler iterates
    # `host_ids` and triggers refreshHostRow per id (same effect,
    # single SSE write).
    try:
        client_id = _request_client_id(request)
        if applied:
            _events.publish(
                "host:bulk_action_applied",
                {
                    "action": "pause",
                    "host_ids": applied,
                    "actor": actor,
                },
                client_id=client_id,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts:bulk] pause SSE publish failed: {e}")
    _full_host_cache_bust()
    print(f"[hosts:bulk] pause by {actor}: {len(applied)} applied, "
          f"{len(missing)} missing, {len(errors)} errors")
    return {
        "ok": not errors,
        "applied": applied,
        "skipped": missing,
        "errors": errors,
    }


@app.post("/api/hosts/bulk/resume")
async def api_hosts_bulk_resume(
    body: HostsBulkResumeIn,
    request: Request,
    _u: AdminUser,
):
    """Clear the auto-pause marker for every host in the request.
    Mirrors `/api/hosts/{host_id}/resume-sampling` per-row with the
    same cool-down clearing semantics, but skips the per-provider
    cool-down probes for speed — bulk callers that need full cool-down
    cleanup can fall back to the per-host endpoint.
    """
    curated = _load_hosts_config()
    matched, missing = _bulk_resolve_host_ids(body.host_ids, curated)
    applied: list[str] = []
    errors: dict[str, str] = {}
    actor = _actor_from(request) or "admin"
    # Single transaction for the whole batch — pre-fix this opened
    # one db_conn() per host inside the loop. For 200 selected hosts
    # that was 200 SQLite write transactions. After migration the
    # composite PK lets us DELETE every row (whole-host + every
    # per-provider variant) for a host in a single statement: the
    # IN list matches both ``host_id='hid' AND provider=''`` and
    # ``host_id='hid' AND provider='snmp'`` rows together. Per-row
    # failure (rare) falls back to per-host loop for partial success +
    # per-id error attribution.
    if matched:
        try:
            with db_conn() as c:
                placeholders = ",".join(["?"] * len(matched))
                c.execute(
                    "DELETE FROM host_failure_state WHERE host_id IN ("
                    + placeholders + ")",  # nosec B608 — placeholders is constant `?` literals
                    list(matched),
                )
            applied = list(matched)
        except Exception as batch_err:  # noqa: BLE001
            print(f"[hosts:bulk] resume batch failed, falling back "
                  f"to per-row: {batch_err}")
            for hid in matched:
                try:
                    with db_conn() as c:
                        c.execute(
                            "DELETE FROM host_failure_state WHERE host_id = ?",
                            (hid,),
                        )
                    applied.append(hid)
                except Exception as e:  # noqa: BLE001
                    errors[hid] = str(e)
    _full_host_cache_bust()
    # Per-host audit rows in `history` so Admin → History + the host
    # drawer's Timeline tab pick up bulk resumes (mirrors bulk-pause).
    if applied:
        _bulk_write_history_rows(
            applied, op_type="hosts_bulk_resume",
            actor=actor, started_ts=time.time(),
        )
    # ONE bulk SSE event covers every applied id — same contract as
    # the bulk-pause sister endpoint above. SPA's
    # `host:bulk_action_applied` handler iterates and refreshes each
    # row in place.
    try:
        client_id = _request_client_id(request)
        if applied:
            _events.publish(
                "host:bulk_action_applied",
                {
                    "action": "resume",
                    "host_ids": applied,
                    "actor": actor,
                },
                client_id=client_id,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts:bulk] resume SSE publish failed: {e}")
    print(f"[hosts:bulk] resume by {actor}: {len(applied)} applied, "
          f"{len(missing)} missing, {len(errors)} errors")
    return {
        "ok": not errors,
        "applied": applied,
        "skipped": missing,
        "errors": errors,
    }


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/hosts/bulk/snmp_vendors")
async def api_hosts_bulk_snmp_vendors(
    body: HostsBulkSnmpVendorsIn,
    request: Request,
    _u: AdminUser,
):
    """Apply an SNMP vendor MIB selection to every host in the request.

    ``mode``:
      * ``"set"`` (default) — replace each row's ``snmp.vendors`` with
        the supplied list. Empty list clears the override → resume
        auto-detect from sysDescr.
      * ``"add"`` — union the supplied vendors into each row's existing
        list. Useful for "also enable Cisco MIBs on these hosts" without
        clobbering existing per-host selections.
      * ``"remove"`` — difference. Drops each supplied vendor from the
        existing list; empty result removes the override (auto-detect).
    """
    curated = _load_hosts_config()
    matched, missing = _bulk_resolve_host_ids(body.host_ids, curated)
    cleaned_input = _clean_vendors_input(body.vendors) or set()
    mode = (body.mode or "set").lower()
    if mode not in ("set", "add", "remove"):
        raise HTTPException(400, f"Unsupported mode: {mode}")
    applied: list[str] = []
    errors: dict[str, str] = {}
    new_curated: list[dict] = []
    for h in curated:
        hid = h.get("id")
        if hid not in matched:
            new_curated.append(h)
            continue
        try:
            _raw_snmp_block = h.get("snmp")
            snmp_block: dict = _raw_snmp_block if isinstance(_raw_snmp_block, dict) else {}
            existing = set(snmp_block.get("vendors") or [])
            if mode == "set":
                next_vendors = set(cleaned_input)
            elif mode == "add":
                next_vendors = existing | cleaned_input
            else:  # remove
                next_vendors = existing - cleaned_input
            new_block = dict(snmp_block)
            if next_vendors:
                new_block["vendors"] = sorted(next_vendors)
            else:
                new_block.pop("vendors", None)
            new_h = dict(h)
            new_h["snmp"] = new_block
            new_curated.append(new_h)
            applied.append(hid)
        except Exception as e:
            errors[hid] = str(e)
            new_curated.append(h)
    if applied:
        try:
            _save_hosts_config(new_curated)
            _full_host_cache_bust()
        except HTTPException as e:
            return {
                "ok": False,
                "applied": [],
                "skipped": missing + applied,
                "errors": {"_save": e.detail},
            }
    actor = _actor_from(request) or "admin"
    # Bulk SSE event so cross-tab observers reload `hosts_config` +
    # refresh each affected row. Vendors edit curated config (NOT
    # failure state) so the SPA handler does a `loadHosts(true)` for
    # this action variant rather than per-row refresh.
    try:
        client_id = _request_client_id(request)
        if applied:
            _events.publish(
                "host:bulk_action_applied",
                {
                    "action": "snmp_vendors",
                    "host_ids": applied,
                    "actor": actor,
                    "mode": mode,
                    "vendors": sorted(cleaned_input),
                },
                client_id=client_id,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts:bulk] snmp-vendors SSE publish failed: {e}")
    # Audit rows — one row per affected host so the History tab + per-host
    # Timeline both surface the change. Same shape as the pause/resume
    # bulk paths.
    _bulk_write_history_rows(
        applied,
        op_type="hosts_bulk_snmp_vendors",
        actor=actor,
        started_ts=time.time(),
    )
    print(f"[hosts:bulk] snmp-vendors by {actor} mode={mode} "
          f"vendors={sorted(cleaned_input)}: {len(applied)} applied, "
          f"{len(missing)} missing, {len(errors)} errors")
    return {
        "ok": not errors,
        "applied": applied,
        "skipped": missing,
        "errors": errors,
        "mode": mode,
        "vendors": sorted(cleaned_input),
    }


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/hosts/bulk/snmp_tunables")
async def api_hosts_bulk_snmp_tunables(
    body: HostsBulkSnmpTunablesIn,
    request: Request,
    _u: AdminUser,
):
    """Apply per-host SNMP tunable overrides to every host in the request.

    Supported fields: ``walk_concurrency`` (1..16), ``wall_clock_budget``
    (5..600 seconds). Both optional — only fields present in the request
    are touched. ``clear=true`` REMOVES the override fields from each
    row's snmp block so the row falls back to the global tunable.
    """
    curated = _load_hosts_config()
    matched, missing = _bulk_resolve_host_ids(body.host_ids, curated)
    # Validate inputs against the same bounds _clean_host_snmp uses.
    wc: Optional[int] = None
    if body.walk_concurrency is not None and not body.clear:
        try:
            wc_val = int(body.walk_concurrency)
            if not (1 <= wc_val <= 16):
                raise HTTPException(400, "walk_concurrency must be in [1, 16]")
            wc = wc_val
        except (TypeError, ValueError):
            raise HTTPException(400, "walk_concurrency must be an integer")
    wcb: Optional[int] = None
    if body.wall_clock_budget is not None and not body.clear:
        try:
            wcb_val = int(body.wall_clock_budget)
            if not (5 <= wcb_val <= 600):
                raise HTTPException(400, "wall_clock_budget must be in [5, 600]")
            wcb = wcb_val
        except (TypeError, ValueError):
            raise HTTPException(400, "wall_clock_budget must be an integer")
    if not body.clear and wc is None and wcb is None:
        raise HTTPException(400, "supply walk_concurrency, wall_clock_budget, or clear=true")
    applied: list[str] = []
    errors: dict[str, str] = {}
    new_curated: list[dict] = []
    for h in curated:
        hid = h.get("id")
        if hid not in matched:
            new_curated.append(h)
            continue
        try:
            snmp_block = dict(h.get("snmp") or {}) if isinstance(h.get("snmp"), dict) else {}
            if body.clear:
                snmp_block.pop("walk_concurrency", None)
                snmp_block.pop("wall_clock_budget", None)
            else:
                if wc is not None:
                    snmp_block["walk_concurrency"] = wc
                if wcb is not None:
                    snmp_block["wall_clock_budget"] = wcb
            new_h = dict(h)
            new_h["snmp"] = snmp_block
            new_curated.append(new_h)
            applied.append(hid)
        except Exception as e:
            errors[hid] = str(e)
            new_curated.append(h)
    if applied:
        try:
            _save_hosts_config(new_curated)
            _full_host_cache_bust()
        except HTTPException as e:
            return {
                "ok": False,
                "applied": [],
                "skipped": missing + applied,
                "errors": {"_save": e.detail},
            }
    actor = _actor_from(request) or "admin"
    # Bulk SSE event — same shape as the snmp-vendors sister, edits
    # curated config not failure state.
    try:
        client_id = _request_client_id(request)
        if applied:
            _events.publish(
                "host:bulk_action_applied",
                {
                    "action": "snmp_tunables",
                    "host_ids": applied,
                    "actor": actor,
                    "clear": bool(body.clear),
                    "walk_concurrency": wc,
                    "wall_clock_budget": wcb,
                },
                client_id=client_id,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts:bulk] snmp-tunables SSE publish failed: {e}")
    # Audit rows — one row per affected host; same shape as the
    # snmp-vendors sister + the pause/resume bulk paths.
    _bulk_write_history_rows(
        applied,
        op_type="hosts_bulk_snmp_tunables",
        actor=actor,
        started_ts=time.time(),
    )
    print(f"[hosts:bulk] snmp-tunables by {actor} "
          f"clear={body.clear} wc={wc} wcb={wcb}: "
          f"{len(applied)} applied, {len(missing)} missing, {len(errors)} errors")
    return {
        "ok": not errors,
        "applied": applied,
        "skipped": missing,
        "errors": errors,
        "walk_concurrency": wc,
        "wall_clock_budget": wcb,
        "clear": body.clear,
    }


class PingTestIn(BaseModel):
    host_id: str
    # Optional ad-hoc overrides — when blank, the test honours the
    # host's persisted ping config (or the global defaults). Used by
    # the Settings-tab "Test ping" button when the operator has typed
    # values that haven't been saved yet.
    port: Optional[int] = None
    transport: Optional[str] = None
    timeout_seconds: Optional[float] = None


class HttpProbeTestIn(BaseModel):
    """Body for the one-shot HTTP / TLS / DNS probe test endpoint.

    ``url`` is mandatory — every other field is optional and falls
    back to the tunable defaults / curated row's ``http_probe``
    config when blank. ``accepted_status_codes`` accepts CSV
    ("200,301,302") or a list.
    """
    url: str
    timeout: Optional[float] = None
    dns_timeout: Optional[float] = None
    content_match: Optional[str] = None
    accepted_status_codes: Optional[str] = None  # CSV or single code
    verify_tls: Optional[bool] = None


class PortScanIn(BaseModel):
    """Optional override knobs for a one-shot port scan. Empty body
    is fine — the endpoint resolves every value from the host's
    effective config (per-host override → global default → built-in
    fallback) when not supplied.
    """
    ports: Optional[str] = None  # TCP CSV/range syntax
    timeout_s: Optional[int] = None
    concurrency: Optional[int] = None
    banner_grab: Optional[bool] = None  # Stage 2 default-OFF
    # UDP companion (Stage 2). When `udp` is true the endpoint runs
    # the UDP scanner alongside TCP via asyncio.gather and merges
    # the results with a `protocol` annotation per port. `udp_ports`
    # is an optional CSV/range override for the UDP target list;
    # empty falls back to the global setting then to
    # `port_scanner_udp.DEFAULT_UDP_PORTS`.
    udp: Optional[bool] = None
    udp_ports: Optional[str] = None
    udp_timeout_s: Optional[int] = None
    udp_concurrency: Optional[int] = None


async def _run_port_scan_async(
    *,
    hid: str,
    target: str,
    ports_list: list,
    timeout_s: int,
    concurrency: int,
    banner_grab: bool,
    udp_enabled: bool,
    udp_ports_list: list,
    udp_timeout_s: int,
    udp_concurrency: int,
    snmp_community: str,
    max_seconds: int,
    scan_id: str,
    started: float,
    h: dict,
    actor: str,
    client_id: Optional[str] = None,
) -> None:
    """Run a port scan + persist results out-of-band from the request.

    Fire-and-forget task spawned by ``api_hosts_port_scan`` so the
    HTTP request returns immediately (HTTP 202) instead of blocking
    for the full scan duration. Wide port-range scans (the 11000-port
    cap) can run minutes; reverse proxies (NPM / openresty) typically
    cap at 60s ``proxy_read_timeout`` and would 504 the synchronous
    path. By kicking the scan off here, the request budget stays
    short and the scan continues independently.

    Errors are caught + logged — there's no caller to raise them
    back to. Persistence + the ``port_scan:completed`` SSE publish
    happen at the end so the SPA picks up results without polling.
    """
    from logic import port_scanner as _ps
    try:
        if udp_enabled:
            from logic import port_scanner_udp as _ps_udp
            tcp_scan, udp_scan = await asyncio.wait_for(
                asyncio.gather(
                    _ps.scan_host(
                        target,
                        ports_list,
                        timeout_s=float(timeout_s),
                        concurrency=int(concurrency),
                        banner_grab=bool(banner_grab),
                    ),
                    _ps_udp.udp_scan_host(
                        target,
                        udp_ports_list,
                        timeout_s=float(udp_timeout_s),
                        concurrency=int(udp_concurrency),
                        snmp_community=str(snmp_community),
                    ),
                ),
                timeout=float(max_seconds),
            )
            scan = tcp_scan
        else:
            scan = await asyncio.wait_for(
                _ps.scan_host(
                    target,
                    ports_list,
                    timeout_s=float(timeout_s),
                    concurrency=int(concurrency),
                    banner_grab=bool(banner_grab),
                ),
                timeout=float(max_seconds),
            )
            udp_scan = None
    except asyncio.TimeoutError:
        # TCP scan timed out at the wall-clock budget. UDP scan
        # may have completed already (UDP defaults are friendlier
        # — 19 ports × 3 s / 8 concurrency ≈ 9 s) and `_run_port_scan_async`
        # was about to merge results when the gather timed out.
        # Pre-fix the timeout branch returned immediately with
        # ZERO persistence — the host row's `last_port_scan_ts`
        # never updated, the drawer kept showing "Last scanned 7h
        # ago" indefinitely, and the partial UDP discovery was
        # discarded. Now we salvage what we have: run the UDP scan
        # SYNCHRONOUSLY (its own short budget capped it already)
        # and persist its open ports under a new scan_id so the
        # drawer at least surfaces the UDP-only findings AND the
        # timestamp updates so the user sees the scan attempt happened.
        print(
            f"[port_scan] failed host_id={hid!r} target={target!r} "
            f"reason=timeout (>{max_seconds}s budget) scan_id={scan_id}"
        )
        partial_udp_open: list[dict] = []
        if udp_enabled:
            try:
                from logic import port_scanner_udp as _ps_udp
                udp_partial = await asyncio.wait_for(
                    _ps_udp.udp_scan_host(
                        target,
                        udp_ports_list,
                        timeout_s=float(udp_timeout_s),
                        concurrency=int(udp_concurrency),
                        snmp_community=str(snmp_community),
                    ),
                    timeout=30.0,  # bounded recovery — never block long
                )
                partial_udp_open = _ps_udp.open_udp_ports_only(udp_partial)
                print(
                    f"[port_scan] timeout-salvage host_id={hid!r} "
                    f"udp_open={len(partial_udp_open)} scan_id={scan_id}"
                )
            except Exception as e:  # noqa: BLE001
                print(
                    f"[port_scan] timeout-salvage failed host_id={hid!r} "
                    f"reason={type(e).__name__}: {e} scan_id={scan_id}"
                )
        try:
            with db_conn() as c:
                # Carry forward the PREVIOUS scan's open ports under
                # the new scan_id BEFORE adding the recovered UDP
                # findings. Pre-fix the chip strip read the latest
                # scan_id and saw ONLY the recovered UDP rows — the
                # earlier TCP discovery (22 / 80 / 443 / etc.) silently
                # disappeared because the new scan replaced the old.
                # Now the new scan_id inherits every row from the
                # most-recent prior scan; the recovered UDP rows then
                # extend / dedupe over that baseline so a sticky
                # listener stays visible AND a freshly-found one
                # surfaces. `(port, protocol)` tuple deduping prevents
                # double-rows when the same UDP/161 was already in
                # the previous scan.
                prev_head = c.execute(
                    "SELECT scan_id FROM host_port_scans "
                    "WHERE host_id = ? AND scan_id != ? "
                    "GROUP BY scan_id ORDER BY MAX(ts) DESC LIMIT 1",
                    (hid, scan_id),
                ).fetchone()
                carried_keys: set[tuple[int, str]] = set()
                if prev_head and prev_head["scan_id"]:
                    prev_rows = c.execute(
                        "SELECT port, service_hint, banner_excerpt, protocol "
                        "FROM host_port_scans WHERE scan_id = ?",
                        (prev_head["scan_id"],),
                    ).fetchall()
                    for r in prev_rows:
                        proto = r["protocol"] or "tcp"
                        port_n = int(r["port"])
                        carried_keys.add((port_n, proto))
                        c.execute(
                            "INSERT INTO host_port_scans "
                            "(ts, host_id, scan_id, port, service_hint, "
                            " banner_excerpt, protocol) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                int(time.time()),
                                hid,
                                scan_id,
                                port_n,
                                r["service_hint"] or "",
                                r["banner_excerpt"] or "",
                                proto,
                            ),
                        )
                    print(
                        f"[port_scan] timeout-salvage carried-forward "
                        f"host_id={hid!r} from-scan={prev_head['scan_id']!r} "
                        f"rows={len(prev_rows)}"
                    )
                # Now add the recovered UDP findings, skipping ones
                # already present in the carried-forward set so the
                # row count stays clean.
                for entry in partial_udp_open:
                    port_n = int(entry.get("port") or 0)
                    if (port_n, "udp") in carried_keys:
                        continue
                    c.execute(
                        "INSERT INTO host_port_scans "
                        "(ts, host_id, scan_id, port, service_hint, "
                        " banner_excerpt, protocol) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            int(time.time()),
                            hid,
                            scan_id,
                            port_n,
                            entry.get("service_hint") or "",
                            entry.get("banner_excerpt") or "",
                            "udp",
                        ),
                    )
                _ops_mod.assert_op_type("port_scan")
                c.execute(
                    "INSERT INTO history "
                    "(ts, op_type, target_kind, target_name, target_id, "
                    " status, duration, events, error, actor) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        float(time.time()),
                        "port_scan",
                        "host",
                        hid,
                        hid,
                        "error",
                        float(max_seconds),
                        json.dumps({
                            "scan_id": scan_id,
                            "target": target,
                            "udp_open_partial": len(partial_udp_open),
                            "tcp_timeout": True,
                        }),
                        f"timeout (>{max_seconds}s budget) — TCP scan exceeded budget; UDP partial results persisted ({len(partial_udp_open)} open)",
                        actor,
                    ),
                )
                c.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[port_scan] history-insert failed after timeout for {hid}: {e}")
        try:
            _events.publish("port_scan:completed", {
                "host_id": hid,
                "scan_id": scan_id,
                "ok": False,
                "target": target,
                "error": "timeout",
                "ports_open": 0,
                "udp_open": len(partial_udp_open),
            }, client_id=client_id)
        except (RuntimeError, OSError):
            pass
        return
    except Exception as e:  # noqa: BLE001
        print(
            f"[port_scan] failed host_id={hid!r} target={target!r} "
            f"reason={type(e).__name__}: {e} scan_id={scan_id}"
        )
        try:
            _events.publish("port_scan:completed", {
                "host_id": hid, "scan_id": scan_id, "ok": False,
                "target": target,
                "error": f"{type(e).__name__}: {e}",
            }, client_id=client_id)
        except (RuntimeError, OSError):
            pass
        return
    duration_ms = scan.get("duration_ms") or int((time.time() - started) * 1000)
    open_entries = _ps.open_ports_only(scan)
    for e in open_entries:
        e.setdefault("protocol", "tcp")
    udp_open_entries: list[dict] = []
    if udp_enabled and udp_scan is not None:
        from logic import port_scanner_udp as _ps_udp
        udp_open_entries = _ps_udp.open_udp_ports_only(udp_scan)
        udp_duration_ms = udp_scan.get("duration_ms") or 0
        if udp_scan.get("error"):
            print(
                f"[port_scan] udp failed host_id={hid!r} target={target!r} "
                f"reason={udp_scan.get('error')!r} scan_id={scan_id} "
                f"udp_duration_ms={udp_duration_ms}"
            )
        else:
            print(
                f"[port_scan] udp ok host_id={hid!r} target={target!r} "
                f"udp_ports_scanned={len(udp_scan.get('ports') or [])} "
                f"udp_ports_open={len(udp_open_entries)} "
                f"udp_duration_ms={udp_duration_ms} scan_id={scan_id}"
            )
    if scan.get("error"):
        print(
            f"[port_scan] failed host_id={hid!r} target={target!r} "
            f"reason={scan.get('error')!r} scan_id={scan_id} "
            f"duration_ms={duration_ms}"
        )
    else:
        print(
            f"[port_scan] ok host_id={hid!r} target={target!r} "
            f"ports_scanned={len(scan.get('ports') or [])} "
            f"ports_open={len(open_entries)} duration_ms={duration_ms} "
            f"scan_id={scan_id}"
        )
    prev_open_ports: set[tuple[int, str]] = set()
    is_first_scan = True  # default-true; flipped to False if a prior scan_id row exists
    try:
        with db_conn() as c:
            prev_head = c.execute(
                "SELECT scan_id, MAX(ts) AS ts FROM host_port_scans "
                "WHERE host_id = ? AND scan_id != ? "
                "GROUP BY scan_id ORDER BY ts DESC LIMIT 1",
                (hid, scan_id),
            ).fetchone()
            if prev_head and prev_head["scan_id"]:
                is_first_scan = False
                prev_rows = c.execute(
                    "SELECT port, protocol FROM host_port_scans WHERE scan_id = ?",
                    (prev_head["scan_id"],),
                ).fetchall()
                prev_open_ports = {
                    (int(r["port"]), (r["protocol"] or "tcp"))
                    for r in prev_rows
                }
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        prev_open_ports = set()
    _raw_curated_services_for_diff = h.get("services")
    curated_services_for_diff: list = _raw_curated_services_for_diff if isinstance(_raw_curated_services_for_diff, list) else []
    curated_ports_set = {int(s.get("port") or 0)
                         for s in curated_services_for_diff if isinstance(s, dict)}
    try:
        with db_conn() as c:
            for entry in open_entries:
                c.execute(
                    "INSERT INTO host_port_scans "
                    "(ts, host_id, scan_id, port, service_hint, "
                    " banner_excerpt, protocol) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(scan.get("scanned_at") or time.time()),
                        hid,
                        scan_id,
                        int(entry.get("port") or 0),
                        entry.get("service_hint") or "",
                        entry.get("banner_excerpt") or "",
                        "tcp",
                    ),
                )
            for entry in udp_open_entries:
                c.execute(
                    "INSERT INTO host_port_scans "
                    "(ts, host_id, scan_id, port, service_hint, "
                    " banner_excerpt, protocol) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        int((udp_scan or {}).get("scanned_at") or time.time()),
                        hid,
                        scan_id,
                        int(entry.get("port") or 0),
                        entry.get("service_hint") or "",
                        entry.get("banner_excerpt") or "",
                        "udp",
                    ),
                )
            events_payload = {
                "scan_id": scan_id,
                "ports_scanned": len(scan.get("ports") or []),
                "ports_open": len(open_entries),
                "scan_duration_ms": duration_ms,
                "target": target,
                "udp_enabled": bool(udp_enabled),
                "udp_ports_scanned": len((udp_scan or {}).get("ports") or []) if udp_enabled else 0,
                "udp_ports_open": len(udp_open_entries) if udp_enabled else 0,
                "udp_scan_duration_ms": int((udp_scan or {}).get("duration_ms") or 0) if udp_enabled else 0,
            }
            try:
                events_json = json.dumps(events_payload, ensure_ascii=False)
            except (TypeError, ValueError):
                events_json = "{}"
            _ops_mod.assert_op_type("port_scan")
            c.execute(
                "INSERT INTO history "
                "(ts, op_type, target_kind, target_name, target_id, "
                " status, duration, events, error, actor) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    float(time.time()),
                    "port_scan",
                    "host",
                    hid,
                    hid,
                    "success" if not scan.get("error") else "error",
                    float(duration_ms) / 1000.0,
                    events_json,
                    scan.get("error") or None,
                    actor,
                ),
            )
            c.commit()
    except Exception as e:  # noqa: BLE001
        print(f"[port_scan] persist failed for {hid}: {e}")

    # Compute "new since last scan" — the SPA's completion toast
    # surfaces this so the operator sees at-a-glance whether the
    # current scan found anything different. Computed BEFORE the
    # notify-only `new_ports` filter below (which additionally
    # excludes curated ports for notification noise) so the toast
    # count reflects the raw "new this scan" tally regardless of
    # whether the new ports are curated.
    all_open = list(open_entries) + list(udp_open_entries)
    new_count_for_toast = 0
    if prev_open_ports and not scan.get("error"):
        new_count_for_toast = sum(
            1 for e in all_open
            if (int(e.get("port") or 0), (e.get("protocol") or "tcp")) not in prev_open_ports
        )
    if prev_open_ports and not scan.get("error"):
        new_ports = [
            e for e in all_open
            if (int(e.get("port") or 0), (e.get("protocol") or "tcp")) not in prev_open_ports
               and int(e.get("port") or 0) not in curated_ports_set
        ]
        if new_ports:
            try:
                from logic import ops as _ops
                for entry in new_ports:
                    pnum = int(entry.get("port") or 0)
                    hint = entry.get("service_hint") or ""
                    proto = (entry.get("protocol") or "tcp").lower()
                    label = f"{pnum}/{proto}" + (f" ({hint})" if hint else "")
                    await _ops.notify(
                        f"🆕 New open port on {target}: {label}",
                        (
                            f"{label} listening on {target} — not in the previous "
                            f"scan and not in this host's curated services. "
                            f"Promote to curated in the host drawer if expected."
                        ),
                        event="port_scan_new_port",
                        actor_username=actor,
                        target_kind="host",
                        target_id=hid,
                        metadata={
                            "port": pnum,
                            "protocol": proto,
                            "service_hint": hint,
                            "scan_id": scan_id,
                        },
                    )
            except Exception as e:  # noqa: BLE001
                print(f"[port_scan] notify failed for {hid}: {e}")

    # Notify any open SPA tabs that the scan completed so they can
    # refresh `host.detected_ports` without polling. The publisher
    # carries the scan summary so the SPA's handler can show a toast
    # without a follow-up GET.
    try:
        _events.publish("port_scan:completed", {
            "host_id": hid,
            "scan_id": scan_id,
            "ok": not bool(scan.get("error")),
            "target": target,
            # Wire-level IP the scanner's OS resolver returned for
            # `target` BEFORE the first probe fired. Surfaced so the
            # toast / history can show what was actually hit at the
            # network layer when the host_id is a friendly alias
            # (e.g. `opnsense` → `192.X.X.X` via container's
            # search-domain resolution). None when getaddrinfo failed
            # OR the scanner couldn't extract it; SPA falls back to
            # `target` then `host_id` in that case.
            "resolved_ip": scan.get("resolved_ip"),
            "ports_open": len(open_entries),
            "udp_open": len(udp_open_entries),
            "duration_ms": duration_ms,
            "error": scan.get("error") or None,
            # Count of (port, protocol) tuples present in this scan
            # but ABSENT from the previous scan's open-set. Drives
            # the "(N new since last scan)" parenthetical in the
            # completion toast. 0 when first scan OR when nothing
            # opened since the last run. Distinct from the
            # `port_scan_new_port` notify path's `new_ports` list
            # — that filter additionally excludes curated ports to
            # cut notification noise; the toast count reflects the
            # raw diff so the user sees exactly what the scan saw.
            "new_count": int(new_count_for_toast),
            # Lets the SPA pick a "first scan" vs "diff vs prior scan"
            # toast wording. True when this is the host's very first
            # scan (no prior scan_id rows in host_port_scans). Saves
            # the SPA from showing "(0 new since last scan)" when
            # there IS no last scan.
            "is_first_scan": bool(is_first_scan),
        }, client_id=client_id)
    except (RuntimeError, OSError):
        pass


@app.post("/api/hosts/{host_id}/port-scan")
async def api_hosts_port_scan(
    host_id: str,
    request: Request,
    body: Optional[PortScanIn] = None,
    *,
    _admin: AdminUser,
):
    """On-demand port scan for one curated host. Admin-only.

    The actual scan runs as a fire-and-forget asyncio task spawned
    from this handler — pre-fix the endpoint blocked for the FULL
    scan duration (up to ``tuning_port_scan_max_seconds`` = 120 s)
    and tripped reverse-proxy timeouts (NPM / openresty default
    ``proxy_read_timeout`` is typically 60 s) on wide port-range
    scans, surfacing as a raw 504 HTML page in the SPA's toast.
    Now: pre-validate + resolve target / config synchronously,
    spawn the scan, return ``{scan_id, status: 'queued'}`` (HTTP 202)
    immediately. The scan persists to ``host_port_scans`` + writes a
    history row + emits a ``port_scan:completed`` SSE event when
    done; SPA picks up the new ``detected_ports`` via the SSE
    handler (or its 30 s polling fallback).
    """
    hid = (host_id or "").strip()
    if not get_setting_bool(Settings.PORT_SCAN_ENABLED):
        print(f"[port_scan] skipped host_id={hid!r} — provider disabled (master toggle off)")
        raise HTTPException(
            status_code=400,
            detail="Port-scan provider is disabled. Enable it in "
                   "Admin → Providers → Port Scan first.",
        )
    if not hid:
        print("[port_scan] skipped — host_id required")
        raise HTTPException(400, "host_id required")
    curated = _load_hosts_config()
    h = next((x for x in curated if x.get("id") == hid), None)
    if h is None:
        print(f"[port_scan] skipped host_id={hid!r} — not found in curated list")
        raise HTTPException(404, f"Host not found: {hid}")
    _raw_ps_cfg = h.get("port_scan")
    ps_cfg: dict = _raw_ps_cfg if isinstance(_raw_ps_cfg, dict) else {}
    # Per-host enabled flag wins; otherwise inherit the master.
    if "enabled" in ps_cfg and not ps_cfg.get("enabled"):
        print(f"[port_scan] skipped host_id={hid!r} — per-host enable flag is False")
        raise HTTPException(
            status_code=400,
            detail=f"Port scan is disabled for host {hid}. "
                   f"Enable it in Admin → Hosts.",
        )
    # Resolve the scan target. Resolution chain (FIRST non-empty wins):
    # curated `address` field (dedicated, provider-independent) →
    # per-host `ping.host` override → `ssh.fqdn` → `ssh.host` → bare
    # host_id. The curated `url` field is DELIBERATELY excluded —
    # it carries the clickable web-UI link the operator wants to
    # surface on the host card. Probing that would target the public
    # service relay instead of the LAN host (wrong data + privacy).
    #
    # The `address` field is the canonical dedicated probe target.
    # User-flagged: provider fields (snmp_name / ping.host / ssh.host)
    # can all be DISABLED independently — relying on any of them as
    # the primary probe target leaves port-scan broken when a provider
    # is turned off. The `address` field is independent of any
    # provider and survives provider toggles. Operators set it in
    # Admin → Hosts. If left blank, the chain falls through to
    # provider-specific overrides then the bare host_id.
    _raw_ssh_cfg = h.get("ssh")
    ssh_cfg: dict = _raw_ssh_cfg if isinstance(_raw_ssh_cfg, dict) else {}
    _raw_ping_cfg = h.get("ping")
    ping_cfg: dict = _raw_ping_cfg if isinstance(_raw_ping_cfg, dict) else {}
    target = (
        (h.get("address") or "").strip()
        or (ping_cfg.get("host") or "").strip()
        or (ssh_cfg.get("fqdn") or "").strip()
        or (ssh_cfg.get("host") or "").strip()
        or hid
    )
    target = target.strip() or hid
    # Effective config: request body → per-host → global → built-in.
    # Narrow `body` to PortScanIn (drop None) so every `body.X` access
    # below doesn't trip "Member 'None' of 'PortScanIn | None'" lint
    # diagnostics. The if-branch reassignment is the only form the
    # type-checker reliably narrows from `T | None` to `T`.
    if body is None:
        body = PortScanIn()
    from logic import port_scanner as _ps
    ports_csv = (
        (body.ports or "").strip()
        or (ps_cfg.get("ports") or "").strip()
        or (get_setting(Settings.PORT_SCAN_DEFAULT_PORTS) or "").strip()
    )
    ports_list = _ps.parse_port_csv(ports_csv) if ports_csv else list(_ps.DEFAULT_PORTS)
    _timeout_raw = (
        body.timeout_s
        if body.timeout_s is not None else
        ps_cfg.get("timeout_s")
        if ps_cfg.get("timeout_s") is not None else
        tuning.tuning_int(Tunable.PORT_SCAN_DEFAULT_TIMEOUT_SECONDS)
    )
    timeout_s: int = int(_timeout_raw) if _timeout_raw is not None else 0
    _concurrency_raw = (
        body.concurrency
        if body.concurrency is not None else
        ps_cfg.get("concurrency")
        if ps_cfg.get("concurrency") is not None else
        tuning.tuning_int(Tunable.PORT_SCAN_DEFAULT_CONCURRENCY)
    )
    concurrency: int = int(_concurrency_raw) if _concurrency_raw is not None else 0
    # UDP companion (Stage 2). Operator-flagged 2026-05-10: TCP and UDP
    # share a single master toggle (`port_scan_enabled`) — there's no
    # separate `port_scan_udp_enabled` flag anymore. UDP runs alongside
    # TCP whenever port scanning is enabled. The legacy `body.udp=true`
    # per-call override is preserved as an explicit "skip UDP this call"
    # escape hatch (`body.udp=false` disables UDP for this scan only;
    # otherwise UDP defaults to ON when the master toggle is on).
    # Results merge into a single `host_port_scans` write with the
    # `protocol` column distinguishing the families.
    udp_enabled = bool(body.udp) if body.udp is not None else True
    udp_ports_list: list[int] = []
    udp_timeout_s = 0
    udp_concurrency = 0
    if udp_enabled:
        from logic import port_scanner_udp as _ps_udp
        udp_ports_csv = (
            (body.udp_ports or "").strip()
            or (ps_cfg.get("udp_ports") or "").strip()
            or (get_setting(Settings.PORT_SCAN_UDP_DEFAULT_PORTS) or "").strip()
        )
        udp_ports_list = (
            _ps.parse_port_csv(udp_ports_csv) if udp_ports_csv
            else list(_ps_udp.DEFAULT_UDP_PORTS)
        )
        _udp_timeout_raw = (
            body.udp_timeout_s
            if body.udp_timeout_s is not None else
            ps_cfg.get("udp_timeout_s")
            if ps_cfg.get("udp_timeout_s") is not None else
            tuning.tuning_int(Tunable.PORT_SCAN_UDP_DEFAULT_TIMEOUT_SECONDS)
        )
        udp_timeout_s = int(_udp_timeout_raw) if _udp_timeout_raw is not None else 0
        _udp_concurrency_raw = (
            body.udp_concurrency
            if body.udp_concurrency is not None else
            ps_cfg.get("udp_concurrency")
            if ps_cfg.get("udp_concurrency") is not None else
            tuning.tuning_int(Tunable.PORT_SCAN_UDP_DEFAULT_CONCURRENCY)
        )
        udp_concurrency = int(_udp_concurrency_raw) if _udp_concurrency_raw is not None else 0
    # Hard bound the scan duration. Outer wall-clock budget flows
    # through TUNABLES so the operator can raise it for large ranges
    # (the 11000-port range cap can reach 10-15 minutes on a slow link).
    # The endpoint NO LONGER blocks on the budget — it spawns the scan
    # as a fire-and-forget asyncio task and returns 202 immediately.
    scan_id = str(uuid.uuid4())
    started = time.time()
    max_seconds = tuning.tuning_int(Tunable.PORT_SCAN_MAX_SECONDS)
    _raw_snmp_cfg = h.get("snmp")
    snmp_cfg: dict = _raw_snmp_cfg if isinstance(_raw_snmp_cfg, dict) else {}
    snmp_community = (
        snmp_cfg.get("community")
        or get_setting(Settings.SNMP_DEFAULT_COMMUNITY)
        or "public"
    )
    print(
        f"[port_scan] queued host_id={hid!r} target={target!r} "
        f"ports={len(ports_list)} timeout_s={timeout_s} "
        f"concurrency={concurrency} banner_grab={bool(body.banner_grab)} "
        f"udp_enabled={udp_enabled} udp_ports={len(udp_ports_list)} "
        f"max_seconds={max_seconds} scan_id={scan_id}"
    )
    actor = getattr(_admin, "username", "ui") or "ui"
    spawn_background_task(
        _run_port_scan_async(
            hid=hid,
            target=target,
            ports_list=ports_list,
            timeout_s=int(timeout_s),
            concurrency=int(concurrency),
            banner_grab=bool(body.banner_grab),
            udp_enabled=bool(udp_enabled),
            udp_ports_list=udp_ports_list,
            udp_timeout_s=int(udp_timeout_s),
            udp_concurrency=int(udp_concurrency),
            snmp_community=str(snmp_community),
            max_seconds=int(max_seconds),
            scan_id=scan_id,
            started=started,
            h=h,
            actor=actor,
            client_id=_request_client_id(request),
        ),
        label=f"port_scan:{hid}:{scan_id[:8]}",
    )
    config_used = {
        "ports_count": len(ports_list),
        "timeout_s": int(timeout_s),
        "concurrency": int(concurrency),
        "banner_grab": bool(body.banner_grab),
        "udp_enabled": bool(udp_enabled),
    }
    if udp_enabled:
        config_used["udp_ports_count"] = len(udp_ports_list)
        config_used["udp_timeout_s"] = int(udp_timeout_s)
        config_used["udp_concurrency"] = int(udp_concurrency)
    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "status": "queued",
            "host_id": hid,
            "target": target,
            "scan_id": scan_id,
            "scanned_at": int(started),
            "config_used": config_used,
        },
    )


@app.get("/api/history/port-scan/{scan_id}/ports")
async def api_history_port_scan_ports(
    scan_id: str,
    _admin: AdminUser,
):
    """Return the open ports recorded for a specific historical
    scan_id. Powers the History-tab detail popup for `op_type='port_scan'`
    rows so an operator clicking a past scan sees WHICH ports were
    open, not just the summary counts.

    Admin-only (the rest of the port-scan surface is admin-only too).
    """
    sid = (scan_id or "").strip()
    if not sid:
        raise HTTPException(400, "scan_id required")
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT port, service_hint, banner_excerpt, ts, protocol "
                "FROM host_port_scans WHERE scan_id = ? "
                "ORDER BY protocol ASC, port ASC",
                (sid,),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"db read failed: {e}")
    return {
        "scan_id": sid,
        "ports": [
            {
                "port": int(r["port"]),
                "protocol": (r["protocol"] or "tcp"),
                "service_hint": r["service_hint"] or "",
                "banner_excerpt": r["banner_excerpt"] or "",
                "ts": int(r["ts"] or 0),
            }
            for r in rows
        ],
    }


@app.post("/api/ping/test")
async def api_ping_test(
    body: PingTestIn,
    _admin: AdminUser,
):
    """One-shot ping probe against a curated host. Used by the
    "Test ping" button in Settings → Host stats and the per-host test
    in Admin → Hosts. Always live (no cache); does NOT write to
    ``ping_samples`` so test-clicks don't pollute the chart series.
    """
    hid = (body.host_id or "").strip()
    if not hid:
        raise HTTPException(400, "host_id required")
    curated = _load_hosts_config()
    h = next((x for x in curated if x.get("id") == hid), None)
    if h is None:
        raise HTTPException(404, f"Host not found: {hid}")
    # Resolve the probe target the same way the sampler does so the
    # test result reflects what the sampler will actually probe. Chain:
    # `address → ping.host → ssh.fqdn → ssh.host → id`. Pre-fix this
    # used `ssh.fqdn → ssh.host → id` only — it skipped BOTH the
    # curated `address` field (the canonical dedicated probe target)
    # AND the per-host `ping.host` override, so a host that pinged
    # successfully via the live sampler reported "Test failed" here
    # because the test connect()ed to an unrelated `ssh.host` (or the
    # bare `id`, often unresolvable).
    _raw_ssh_cfg = h.get("ssh")
    ssh_cfg: dict = _raw_ssh_cfg if isinstance(_raw_ssh_cfg, dict) else {}
    _raw_pcfg_for_target = h.get("ping")
    pcfg_for_target: dict = _raw_pcfg_for_target if isinstance(_raw_pcfg_for_target, dict) else {}
    target = (
        (h.get("address") or "").strip()
        or (pcfg_for_target.get("host") or "").strip()
        or (ssh_cfg.get("fqdn") or "").strip()
        or (ssh_cfg.get("host") or "").strip()
        or hid
    )
    _raw_pcfg = h.get("ping")
    pcfg: dict = _raw_pcfg if isinstance(_raw_pcfg, dict) else {}
    default_port = tuning.tuning_int(Tunable.PING_DEFAULT_PORT) or 443
    port = body.port if body.port is not None else (pcfg.get("port") or default_port)
    use_icmp_global = get_setting_bool(Settings.PING_USE_ICMP)
    transport = (body.transport or pcfg.get("transport") or "").strip().lower()
    if transport not in ("tcp", "icmp"):
        transport = "icmp" if use_icmp_global else "tcp"
    timeout = float(body.timeout_seconds) if body.timeout_seconds is not None \
        else float(tuning.tuning_int(Tunable.PING_PROBE_TIMEOUT_SECONDS))
    from logic import ping as _ping_mod
    if transport == "icmp" and not _ping_mod.has_icmp_support():
        transport = "tcp"
    result = await _ping_mod.probe_ping(
        target, port=int(port), transport=transport,
        timeout_seconds=timeout,
    )
    return _stamp_test_success("ping", {
        "ok": bool(result.get("alive")),
        "host": target,
        "port": int(port),
        "transport": transport,
        **result,
    })


@app.post("/api/http-probe/test")
async def api_http_probe_test(
    body: HttpProbeTestIn,
    _admin: AdminUser,
):
    """One-shot HTTP / TLS / DNS probe against an arbitrary URL.

    Used by the "Test connection" button in the Admin → Host stats
    HTTP probe section + the per-host editor's row-level test.
    Always live — bypasses the persisted-cache lookup. Does NOT
    write to ``host_http_samples`` so test-clicks don't pollute the
    chart series. No history row (consistent with the other
    one-shot test endpoints).
    """
    from logic import http_probe as _http_probe
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    timeout = float(body.timeout) if body.timeout is not None \
        else float(tuning.tuning_int(Tunable.HTTP_PROBE_TIMEOUT_SECONDS))
    dns_timeout = float(body.dns_timeout) if body.dns_timeout is not None \
        else float(tuning.tuning_int(Tunable.HTTP_PROBE_DNS_TIMEOUT_SECONDS))
    accepted = _http_probe.parse_status_codes_csv(body.accepted_status_codes)
    verify_tls = True if body.verify_tls is None else bool(body.verify_tls)
    content_match = (body.content_match or "").strip() or None
    result = await _http_probe.probe_http_health(
        url,
        timeout=timeout,
        dns_timeout=dns_timeout,
        content_match=content_match,
        accepted_status_codes=accepted,
        verify_tls=verify_tls,
    )
    return _stamp_test_success("http_probe", {
        "ok": bool(result.get("ok")),
        "url": url,
        **result,
    })


@app.get("/api/auth/providers")
async def api_auth_providers(request: Request):
    """Public endpoint: advertises which login paths are live. The login
    page queries this before rendering the SSO button so unconfigured
    deployments don't show a dead button that 503s.

    Multi-URL deployments: OIDC is reported `False` when the request's
    hostname doesn't match the configured `oidc_redirect_uri`'s host.
    OmniGrid is often reachable via multiple FQDNs (LAN /
    Cloudflare-tunnel / VPN), but Authentik will only honour the SSO
    flow for ONE registered redirect URI — opening the login page from
    any other URL would show a button that fails the round-trip with a
    "redirect_uri_mismatch" error. Hiding it on mismatched hostnames
    saves the operator a confusing trip into Authentik's logs.
    Hostname comparison is case-insensitive and ignores the port +
    path; an unparseable redirect URI falls back to "show the button"
    (defensive — better a useless button than hiding the SSO path on
    a config typo).
    """
    oidc_live = oidc.is_configured()
    if oidc_live:
        try:
            redirect_uri = (get_setting(Settings.OIDC_REDIRECT_URI) or "").strip()
            if redirect_uri:
                from urllib.parse import urlparse
                expected_host = (urlparse(redirect_uri).hostname or "").strip().lower()
                request_host = (request.url.hostname or "").strip().lower()
                # Both populated AND mismatched → hide the button.
                # Either side blank → fall through to "show" (don't lock
                # operators out on a misconfigured redirect URI).
                if expected_host and request_host and expected_host != request_host:
                    oidc_live = False
        except Exception as e:  # noqa: BLE001
            print(f"[auth] providers redirect_uri host-match check failed: {e}")
    return {
        "local": True,
        "oidc": oidc_live,
    }


@app.post("/api/notify-test")
async def api_notify_test(_admin: AdminUser):
    """Combined Test — fans out to EVERY enabled medium (app + apprise
    + telegram). Kept for back-compat with the legacy single-button UX;
    the Notifications admin tab now ALSO exposes per-channel Test
    buttons so operators can verify each channel independently."""
    await notify("🔔 OmniGrid test", "Notifications are wired up correctly!", "success")
    # Audit row — test-fires of real notifications (Apprise / app medium)
    # are side-effects on subscribers; the audit trail surfaces who-fired-
    # when so a noise complaint can be triaged back to the source.
    try:
        with db_conn() as c:
            _ops_mod.write_admin_audit(
                c, "notify_test",
                target_kind="notify", target_name="test",
                actor=_admin.username or "operator",
                message=f"test notification fired by {_admin.username or 'operator'}",
            )
    except Exception as e:
        print(f"[notify] notify_test audit-row write failed: {e}")
    return {"status": "sent"}


@app.post("/api/apprise/test")
async def api_apprise_test(_admin: AdminUser):
    """Per-channel Apprise Test — fires ONLY the Apprise medium.
    Per-channel siblings: `/api/telegram/test` (already exists). The
    combined `/api/notify-test` route stays for back-compat. Result
    shape matches the Telegram probe contract so the SPA can render
    the inline result chip identically across channels."""
    result = await _ops_mod.notify_medium_apprise(
        title="🔔 OmniGrid test",
        body="Apprise channel test — if you see this, the integration is wired correctly.",
        severity="success",
        event="apprise_test",
        actor_username=_admin.username,
        target_kind="notify", target_id="apprise_test",
        metadata=None,
    )
    try:
        with db_conn() as c:
            _ops_mod.write_admin_audit(
                c, "notify_test",
                target_kind="notify", target_name="apprise_test",
                actor=_admin.username or "operator",
                message=f"apprise channel test fired by {_admin.username or 'operator'}",
            )
    except Exception as e:
        print(f"[notify] apprise_test audit-row write failed: {e}")
    return _stamp_test_success("apprise", {
        "ok": bool(result.get("ok")),
        "detail": result.get("error") or result.get("skipped") or ("sent" if result.get("ok") else "failed"),
        "status": int(result.get("status") or 0),
    })


class _NotifySendIn(BaseModel):
    """Body for ``POST /api/notify/send`` — operator-driven custom
    message routed to ONE specific medium. Distinct from the per-medium
    Test endpoints (which fire a fixed payload). Backs the AI palette's
    ``send_notification`` action so the operator can say "send to
    telegram <text>" and have the AI dispatch it under their auth.
    """
    medium: str  # "app" | "apprise" | "telegram"
    body: str
    title: Optional[str] = None


@app.post("/api/notify/send")
async def api_notify_send(
    body_in: _NotifySendIn,
    _request: Request,
    _admin: AdminUser,
):
    """Send a custom (operator-typed) notification through ONE specific
    medium. Admin-only. The medium MUST be enabled in Admin →
    Notifications — disabled mediums short-circuit with a clear
    ``ok=False, detail=<reason>`` instead of silently dropping the
    message. Title defaults to ``"🔔 OmniGrid"`` when omitted so the
    AI palette's natural-language input doesn't have to invent one.

    Body length capped at 4096 chars (matches Telegram's per-message
    limit so the wire never rejects on size).

    Audit row written under ``op_type='notify_send'`` so the History
    tab surfaces every operator-driven send alongside the per-medium
    test fires.
    """
    medium = (body_in.medium or "").strip().lower()
    msg = (body_in.body or "").strip()
    title = (body_in.title or "").strip() or "🔔 OmniGrid"
    if not medium:
        raise HTTPException(400, "medium is required")
    if not msg:
        raise HTTPException(400, "body is required")
    if len(msg) > 4096:
        raise HTTPException(400, "body exceeds 4096 chars")
    if medium not in _ops_mod.NOTIFY_MEDIUMS:
        raise HTTPException(
            400,
            f"unknown medium '{medium}' — valid: "
            f"{', '.join(sorted(_ops_mod.NOTIFY_MEDIUMS.keys()))}",
        )
    actor = (_admin.username or "operator")
    result = await _ops_mod.notify_one_medium(
        medium=medium,
        title=title,
        body=msg,
        actor_username=actor,
        metadata={"source": "api_notify_send"},
    )
    # Audit row — same contract as the per-medium Test endpoints. Keeps
    # the History tab honest about who fired what, even when the message
    # is operator-typed rather than event-driven.
    try:
        _ops_mod.assert_op_type("notify_send")
        with db_conn() as c:
            _ops_mod.write_admin_audit(
                c, "notify_send",
                target_kind="notify", target_name=medium,
                actor=actor,
                message=(
                    f"custom notification fired by {actor} via {medium}: "
                    f"{msg[:140]}{'…' if len(msg) > 140 else ''}"
                ),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[notify] notify_send audit-row write failed: {e}")
    return {
        "ok": bool(result.get("ok")),
        "medium": medium,
        "detail": result.get("detail") or result.get("error") or "",
    }


# ============================================================================
# In-app notifications store. Sibling of the Apprise medium —
# `logic.ops:notify` writes a row through the `app` medium on every
# enabled event AND publishes ``notification:created`` over SSE so the
# avatar badge + Notifications page update without polling. Routes are
# admin-only; bearer-token clients can poll on the same cookie/CSRF
# contract every other /api/ endpoint uses.
# ============================================================================
def _shape_notification_row(r) -> dict:
    """Cast a SQLite Row into the API JSON shape. Centralised so the
    list / SSE / mark-read paths all return the same field set.
    """
    md_raw = r["metadata"] if "metadata" in r.keys() else None
    md_obj: Optional[dict] = None
    if md_raw:
        try:
            md_obj = json.loads(str(md_raw))
        except (TypeError, ValueError):
            md_obj = None
    return {
        "id": int(r["id"]),
        "ts": int(r["ts"]),
        "event": r["event"] or "",
        "severity": r["severity"] or "info",
        "title": r["title"] or "",
        "body": r["body"] or "",
        "actor": r["actor"],
        "target_kind": r["target_kind"],
        "target_id": r["target_id"],
        "metadata": md_obj,
        "read_at": int(r["read_at"]) if r["read_at"] is not None else None,
    }


@app.get("/api/notifications")
async def api_notifications_list(
    limit: int = 50,
    offset: int = 0,
    unread_only: bool = False,
    event: Optional[str] = None,
    severity: Optional[str] = None,
    *,
    _admin: AdminUser,
):
    """Paginated list of in-app notifications, newest first.

    Filters compose with AND. ``limit`` is clamped to 1..200 (the SPA's
    default page size is 50; the upper cap keeps a bearer-token client
    from accidentally requesting the full table). Unread badge state is
    surfaced via ``unread_count`` regardless of the active filter so the
    SPA's avatar pill always reflects the global count.
    """
    try:
        limit_i = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        limit_i = 50
    try:
        offset_i = max(0, int(offset))
    except (TypeError, ValueError):
        offset_i = 0
    where_parts: list[str] = []
    params: list = []
    if unread_only:
        where_parts.append("read_at IS NULL")
    if event:
        where_parts.append("event = ?")
        params.append(str(event)[:100])
    if severity:
        sev = str(severity).strip().lower()
        if sev in ("info", "warning", "error", "success"):
            where_parts.append("severity = ?")
            params.append(sev)
    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    with db_conn() as c:
        rows = c.execute(
            "SELECT id, ts, event, severity, title, body, actor, "
            "target_kind, target_id, metadata, read_at "
            f"FROM notifications{where_sql} "
            "ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            (*params, limit_i, offset_i),
        ).fetchall()
        total_row = c.execute(
            f"SELECT COUNT(*) AS n FROM notifications{where_sql}",
            tuple(params),
        ).fetchone()
        unread_row = c.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE read_at IS NULL"
        ).fetchone()
    return {
        "items": [_shape_notification_row(r) for r in rows],
        "total": int(total_row["n"]) if total_row else 0,
        "unread_count": int(unread_row["n"]) if unread_row else 0,
        "limit": limit_i,
        "offset": offset_i,
    }


@app.post("/api/notifications/{nid}/read")
async def api_notifications_mark_read(
    nid: int,
    request: Request,
    _admin: AdminUser,
):
    """Mark one notification row as read. Idempotent — already-read rows
    return 200 with the existing ``read_at``. 404 when the id doesn't
    exist so the SPA can prune ghost rows from a stale local cache.
    """
    now = int(time.time())
    with db_conn() as c:
        row = c.execute(
            "SELECT id, read_at FROM notifications WHERE id = ?", (nid,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="notification not found")
        if row["read_at"] is None:
            c.execute(
                "UPDATE notifications SET read_at = ? WHERE id = ?", (now, nid),
            )
            read_at = now
        else:
            read_at = int(row["read_at"])
        unread_row = c.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE read_at IS NULL"
        ).fetchone()
        unread_count = int(unread_row["n"]) if unread_row else 0
    # Push the new unread count over SSE so other tabs update their
    # badge without a round-trip. Self-filter via X-OmniGrid-Client-Id
    # so the originating tab doesn't echo-flicker its own click.
    try:
        _events.publish(
            "notification:read",
            {"id": nid, "read_at": read_at, "unread_count": unread_count},
            client_id=_request_client_id(request),
        )
    except Exception as _e:
        print(f"[notify] read SSE publish dropped: {_e}")
    return {"id": nid, "read_at": read_at, "unread_count": unread_count}


@app.post("/api/notifications/read-all")
async def api_notifications_mark_all_read(
    request: Request,
    _admin: AdminUser,
):
    """Mark every unread notification as read. Returns the count that
    was flipped so the SPA can show a "Marked N as read" toast and the
    badge zeros out atomically.
    """
    now = int(time.time())
    with db_conn() as c:
        cur = c.execute(
            "UPDATE notifications SET read_at = ? WHERE read_at IS NULL", (now,),
        )
        count = int(cur.rowcount or 0)
    try:
        _events.publish(
            "notification:read",
            {"id": None, "read_at": now, "unread_count": 0, "bulk": True},
            client_id=_request_client_id(request),
        )
    except Exception as _e:
        print(f"[notify] read-all SSE publish dropped: {_e}")
    return {"count": count, "unread_count": 0}


@app.delete("/api/notifications/{nid}")
async def api_notifications_delete(
    nid: int,
    request: Request,
    admin: AdminUser,
):
    """Admin-only delete one notification. Operators rarely need this —
    the prune_notifications schedule sweeps old rows automatically — but
    a one-off "scrub the test row" workflow is occasionally useful.
    """
    with db_conn() as c:
        cur = c.execute("DELETE FROM notifications WHERE id = ?", (nid,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="notification not found")
        unread_row = c.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE read_at IS NULL"
        ).fetchone()
        unread_count = int(unread_row["n"]) if unread_row else 0
        _ops_mod.write_admin_audit(
            c, "notification_delete",
            target_kind="notification", target_name=str(nid), target_id=str(nid),
            actor=admin.username,
            message=f"Deleted notification id={nid}",
        )
    try:
        _events.publish(
            "notification:deleted",
            {"id": nid, "unread_count": unread_count},
            client_id=_request_client_id(request),
        )
    except Exception as _e:
        print(f"[notify] delete SSE publish dropped: {_e}")
    return {"id": nid, "deleted": True, "unread_count": unread_count}


# ============================================================================
# Multi-tab activity registry — operators routinely run 3-5 OmniGrid tabs in
# parallel (one for stacks, one per debugging host, one for AI sidebar).
# Tracking each tab's current location lets the topbar widget show "you have
# 3 tabs open: Tab 2 = Stacks, Tab 3 = web01 drawer" + click-to-focus.
# Multi-tab activity tracking.
#
# Storage: in-process dict. Single-replica deploy = no need for SQLite. TTL
# expiry on stale heartbeats so closed-without-cleanup tabs (browser crash /
# kill -9) don't pile up forever. Read by the topbar widget; written by the
# SPA on every navigation event + on a 30s heartbeat tick.
# ============================================================================
_TAB_ACTIVITY_TTL_SECONDS = 90
# {client_id: {"actor", "view", "drawer_host", "admin_tab", "settings_section",
#              "stats_tab", "title", "ts"}}
_tab_activity_registry: dict[str, dict] = {}


def _tab_activity_prune() -> None:
    """Drop entries whose last heartbeat is older than the TTL. Called on
    every read so the live registry stays clean without a sweeper task."""
    cutoff = time.time() - _TAB_ACTIVITY_TTL_SECONDS
    stale: list[str] = []
    for cid, ent in _tab_activity_registry.items():
        _ts_raw = ent.get("ts") if isinstance(ent, dict) else None
        try:
            _ts_val = float(_ts_raw) if isinstance(_ts_raw, (int, float, str)) else 0.0
        except (TypeError, ValueError):
            _ts_val = 0.0
        if _ts_val < cutoff:
            stale.append(cid)
    for cid in stale:
        _tab_activity_registry.pop(cid, None)


def _parse_tab_activity_device(ua: str) -> dict:
    """Parse a User-Agent string into a compact device descriptor.

    Returns ``{form_factor, platform, browser, ua}`` where every field
    is a short tagged-string from a closed set so the SPA's popover can
    render an emoji + i18n-keyed label without further string juggling.

    Heuristic intentionally simple — UA parsing is messy but the popover
    only needs enough resolution to answer "is the other tab on a
    phone, a Mac, or a Windows laptop?" Field values:
      * ``form_factor`` ∈ {mobile, tablet, desktop}
      * ``platform``    ∈ {iOS, Android, Windows, Mac, Linux, BSD, ChromeOS, Other}
      * ``browser``     ∈ {Chrome, Firefox, Safari, Edge, Opera, Other}

    ``ua`` is the original string capped at 200 chars for the hover
    tooltip (two-laptop disambiguation). Empty input → every field
    ``Other`` / ``desktop`` so the parser never returns ``None``.
    """
    s = (ua or "").strip()[:512]
    low = s.lower()
    # Form-factor first — iPad must NOT match the iPhone branch (its UA
    # carries `Macintosh` on modern Safari for "Request Desktop Site"
    # behaviour, but still includes `iPad` when not toggled).
    if "ipad" in low or ("tablet" in low and "mobile" not in low):
        form_factor = "tablet"
    elif "iphone" in low or "ipod" in low or "mobi" in low or ("android" in low and "mobile" in low):
        form_factor = "mobile"
    else:
        form_factor = "desktop"
    # Platform — order matters because iOS UAs contain "Mac OS X"-ish
    # tokens and ChromeOS UAs contain "Linux".
    if "iphone" in low or "ipad" in low or "ipod" in low:
        platform = "iOS"
    elif "android" in low:
        platform = "Android"
    elif "cros" in low or "chromeos" in low:
        platform = "ChromeOS"
    elif "windows" in low:
        platform = "Windows"
    elif "mac os" in low or "macintosh" in low:
        platform = "Mac"
    elif "freebsd" in low or "openbsd" in low or "netbsd" in low:
        platform = "BSD"
    elif "linux" in low:
        platform = "Linux"
    else:
        platform = "Other"
    # Browser — Edge/Opera must precede Chrome because they ALSO carry
    # `Chrome/` in their UA. Safari last because every WebKit-based
    # browser carries `Safari/` in its UA.
    if "edg/" in low or "edge/" in low:
        browser = "Edge"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    elif "firefox/" in low or "fxios" in low:
        browser = "Firefox"
    elif "chrome/" in low or "crios" in low:
        browser = "Chrome"
    elif "safari/" in low:
        browser = "Safari"
    else:
        browser = "Other"
    return {
        "form_factor": form_factor,
        "platform": platform,
        "browser": browser,
        "ua": s[:200],
    }


class _TabActivityIn(BaseModel):
    """Body for the heartbeat endpoint. Every field optional — the SPA
    sends only what's relevant to the current location. Rich-state
    fields (`drawer_item`, `filters`, `selection`, `rich_label`)
    power the "Reproduce here" handoff: a sibling tab's popover row
    can mirror the source tab's filter / drawer / sub-tab state into
    the current tab in one click. Empty / null = source tab was idle
    so the popover renders a one-line label."""
    view: Optional[str] = None
    drawer_host: Optional[str] = None
    drawer_item: Optional[str] = None
    admin_tab: Optional[str] = None
    settings_section: Optional[str] = None
    stats_tab: Optional[str] = None
    title: Optional[str] = None
    filters: Optional[dict] = None
    selection: Optional[list] = None
    rich_label: Optional[str] = None


@app.post("/api/tabs/activity")
async def api_tabs_activity_heartbeat(
    body: _TabActivityIn,
    request: Request,
):
    """Per-tab heartbeat. Updates the in-process registry + broadcasts a
    `tab:activity` SSE event so OTHER tabs see the location change in
    real time. Originating tab self-filters via the `client_id` echo
    (matches the existing event-bus self-filter pattern).

    Auth — relies on the global middleware's `/api/*` enforcement; no
    explicit dep needed. The middleware sets `request.state.user` when
    auth succeeds; we read the username off it for the `actor` field.
    """
    cid = _request_client_id(request)
    if not cid:
        return {"ok": False, "reason": "no client id"}
    actor = _actor_from(request)
    # Sanitise the rich-state payload BEFORE storing — filters dict
    # should only hold serialisable scalars (booleans, strings, short
    # arrays of strings) so a malicious or buggy SPA payload can't
    # blow the registry up. Selection cap at 50 ids matches the SPA-
    # side cap so wire + storage agree.
    # Explicit `dict` type (not Optional) so pyright can narrow the
    # in-loop writes. We emit None at the END when the caller passed
    # nothing — pre-fix the Optional[dict] declaration made every
    # `filters_clean[k] = v` raise a "Member 'None' of 'dict | None'"
    # warning even inside the isinstance-guarded block.
    filters_clean_dict: dict = {}
    has_filters = isinstance(body.filters, dict)
    if has_filters:
        for k, v in body.filters.items():  # type: ignore[union-attr]
            if not isinstance(k, str) or len(k) > 64:
                continue
            if isinstance(v, (bool, int, float)) or v is None:
                filters_clean_dict[k] = v
            elif isinstance(v, str) and len(v) <= 256:
                filters_clean_dict[k] = v
            elif isinstance(v, list) and len(v) <= 20:
                # CSV-shaped list of short strings (provider names etc.)
                filters_clean_dict[k] = [str(x)[:64] for x in v if isinstance(x, (str, int, float))]
    filters_clean: Optional[dict] = filters_clean_dict if has_filters else None
    selection_clean: Optional[list] = None
    if isinstance(body.selection, list):
        selection_clean = [str(x)[:128] for x in body.selection[:50] if isinstance(x, (str, int))]
    # Device descriptor from the request's User-Agent header — gives
    # operators a "which machine is the OTHER tab on" hint in the
    # popover (📱 iPhone · Safari / 🖥️ Mac · Firefox /...). Backend
    # parse so the SPA payload stays small AND so we don't depend on
    # client-hints API support (Safari lags). Hover-title carries the
    # raw UA (capped) for two-laptop disambiguation.
    device = _parse_tab_activity_device(request.headers.get("user-agent") or "")
    entry = {
        "actor": actor,
        "view": (body.view or "").strip() or None,
        "drawer_host": (body.drawer_host or "").strip() or None,
        "drawer_item": (body.drawer_item or "").strip() or None,
        "admin_tab": (body.admin_tab or "").strip() or None,
        "settings_section": (body.settings_section or "").strip() or None,
        "stats_tab": (body.stats_tab or "").strip() or None,
        "title": (body.title or "").strip() or None,
        "filters": filters_clean if filters_clean else None,
        "selection": selection_clean if selection_clean else None,
        "rich_label": (body.rich_label or "").strip() or None,
        "device": device,
        "ts": time.time(),
    }
    _tab_activity_registry[cid] = entry
    _tab_activity_prune()
    try:
        _events.publish(
            "tab:activity",
            {"client_id": cid, **entry},
            client_id=cid,  # self-filter: originating tab won't echo
        )
    except Exception as _e:
        print(f"[tabs] activity SSE publish dropped: {_e}")
    return {"ok": True}


@app.delete("/api/tabs/activity")
async def api_tabs_activity_close(request: Request):
    """Tab-close cleanup — fired from the SPA's `pagehide` event so
    other tabs see the entry vanish immediately instead of waiting for
    the 90s TTL."""
    cid = _request_client_id(request)
    if not cid:
        return {"ok": False, "reason": "no client id"}
    _tab_activity_registry.pop(cid, None)
    try:
        _events.publish(
            "tab:closed",
            {"client_id": cid},
            client_id=cid,
        )
    except Exception as _e:
        print(f"[tabs] close SSE publish dropped: {_e}")
    return {"ok": True}


@app.get("/api/tabs/activity")
async def api_tabs_activity_list(request: Request):
    """Snapshot of every active tab. Excludes the calling tab via
    `client_id` self-filter so the SPA's first-render doesn't display
    its own entry. Used at SPA boot to seed the local map before the
    SSE stream catches up."""
    cid = _request_client_id(request)
    _tab_activity_prune()
    out = []
    for tcid, ent in _tab_activity_registry.items():
        if cid and tcid == cid:
            continue
        out.append({"client_id": tcid, **ent})
    return {"tabs": out}


@app.get("/api/healthz")
async def healthz():
    """Liveness probe — returns 200 with the running version + uptime."""
    # Re-read VERSION.txt per request so operator edits on the server
    # (e.g. hand-bumping MAJOR/MINOR) show up without restarting the
    # container. File is tiny — a couple-microsecond stat+read each call.
    #
    # The container healthcheck only cares about HTTP 200 vs non-200, so
    # we intentionally keep returning 200 when config is broken — that
    # way Swarm doesn't crash-loop the task and the config-error page
    # stays reachable for the operator. The `ok` and `config_error`
    # fields let any JSON caller (Grafana, Uptime Kuma) distinguish
    # healthy from degraded.
    return {
        "ok": _db.DB_PATH_ERROR is None,
        "version": read_version(),
        "cache_age": int(time.time() - _cache["ts"]) if _cache["ts"] else None,
        "config_error": _db.DB_PATH_ERROR,
    }


@app.get("/api/version")
async def api_version():
    """Return the running OmniGrid version baked into the image at build time."""
    return {"version": read_version()}


# Admin → Version page was removed in 2026-04-30 alongside the deploy
# migration to image-build. Pre-fix the page wrote to /app/VERSION.txt
# via a per-file bind mount; post-fix the file is baked into the image
# at build time and any in-container write lands in the ephemeral
# overlay layer that the next `service update --force` discards. The
# durable seed path is now: edit repo-root VERSION.txt, commit, push —
# deploy.yml's source-B resolver (head -n1 ${DEPLOY_PATH}/VERSION.txt)
# picks it up as the floor for the next PATCH bump.


# ----------------------------------------------------------------------------
# Topbar weather widget — proxies an Open-Meteo-compatible instance so
# the browser dodges CORS and the same coordinate pair gets cached
# across tabs / reloads.
#
# URL is stored in the DB ``settings`` table under ``open_meteo_url``
# and is admin-authoritative (Admin → Notifications). There is NO
# hardcoded fallback — leaving the setting blank disables the weather
# endpoint entirely (returns ``{configured: false}``) so the operator
# isn't silently forwarded to api.open-meteo.com without opting in.
# ----------------------------------------------------------------------------
def _open_meteo_url() -> str:
    """Read the weather-upstream URL from settings.

    Returns the stored URL (trailing slash stripped) or the empty
    string when unset. Callers must treat `""` as "not configured"
    rather than falling back to a default.

    The per-service master switch `open_meteo_enabled` is
    consulted first — when disabled, return `""` regardless of what
    URL is stored. This way the URL stays in the settings table for
    when the operator flips back on, but the weather endpoint cleanly
    reports "not configured" while the switch is off.
    """
    from logic.db import get_setting_bool
    if not get_setting_bool(Settings.OPEN_METEO_ENABLED, default=True):
        return ""
    return (get_setting(Settings.OPEN_METEO_URL) or "").strip().rstrip("/")


_weather_cache: dict[tuple[float, float], tuple[float, dict]] = {}
_WEATHER_CACHE_TTL = 600.0  # 10 minutes — weather changes slowly

# WMO code → (short description, icon slug). Backend owns the mapping
# so i18n of condition strings has ONE source of truth.
_WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("Clear", "sun"),
    1: ("Mainly clear", "sun"),
    2: ("Partly cloudy", "cloud-sun"),
    3: ("Cloudy", "cloud"),
    45: ("Fog", "fog"),
    48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "drizzle"),
    53: ("Drizzle", "drizzle"),
    55: ("Heavy drizzle", "drizzle"),
    56: ("Freezing drizzle", "sleet"),
    57: ("Freezing drizzle", "sleet"),
    61: ("Light rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "sleet"),
    67: ("Freezing rain", "sleet"),
    71: ("Light snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Rain showers", "rain"),
    81: ("Rain showers", "rain"),
    82: ("Heavy showers", "rain"),
    85: ("Snow showers", "snow"),
    86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "thunder"),
    96: ("Thunder + hail", "thunder"),
    99: ("Thunder + hail", "thunder"),
}


@app.get("/api/public-ip")
async def api_public_ip(_admin: AdminUser):
    """Admin-only public-IP + ISP / ASN lookup. Standalone subsystem
    (NOT AI-related). The AI palette + Telegram /ip command both
    consume it but the feature owns its own Admin → Public IP section.

    Gated behind the `tuning_public_ip_enabled` tunable (default OFF
    for privacy — fetching reveals the deploy is reaching ifconfig.co).
    The helper in `logic.public_ip` handles the cache + the gate
    short-circuit; this endpoint just surfaces the result so callers
    can fold it into their context blocks.

    Returns `{enabled: false}` when the gate is off so the SPA knows
    to omit the prompt block and the AI doesn't try to answer "what's
    my public IP" from a refused/empty payload. On a soft fetch
    failure (transient network blip) returns `{enabled: true, ip: null,
    error: <detail>}` so the SPA can render a hint rather than
    silently swallowing.
    """
    from logic import public_ip as _public_ip
    if not _public_ip.is_enabled():
        return {"enabled": False}
    data = await _public_ip.fetch()
    if data is None:
        return {"enabled": True, "error": "lookup failed — see Admin → Logs"}
    return {"enabled": True, **data}


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/weather")
async def api_weather(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    label: str = "",
):
    """Fetch current conditions from Open-Meteo for one lat/lon.

    Caller persists label + coords in localStorage; this endpoint is
    stateless apart from an in-memory 10-min cache keyed by (lat, lon).
    Network errors degrade to ``{configured, error}`` so the topbar
    never breaks when the upstream is unreachable.
    """
    if lat is None or lon is None:
        return {"configured": False}
    upstream = _open_meteo_url()
    if not upstream:
        # Admin → General stores `open_meteo_url` (post-fix split out
        # of the legacy Notifications panel); blank disables the
        # widget entirely rather than forwarding to a hardcoded public
        # endpoint the operator didn't opt into.
        return {
            "configured": False,
            "error": "open_meteo_url not configured",
            "label": label,
        }
    # Quantise to 2 decimals so minor coord differences for the same
    # city hit one cache entry.
    key = (round(float(lat), 2), round(float(lon), 2))
    now = time.time()
    cached = _weather_cache.get(key)
    if cached and (now - cached[0]) < _WEATHER_CACHE_TTL:
        body = dict(cached[1])
        body["label"] = label or body.get("label") or ""
        body["cached"] = True
        return body

    params = {
        "latitude": str(key[0]),
        "longitude": str(key[1]),
        "current": "temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m",
        # Daily forecast — covers the next 7 days. AI sidebar consumers
        # use this for "weather forecast next 5 days" questions; the
        # topbar widget keeps showing current-only and ignores the
        # forecast payload (small enough to ride the same response).
        "daily": (
            "temperature_2m_max,temperature_2m_min,weather_code,"
            "precipitation_sum,sunrise,sunset"
        ),
        "forecast_days": "7",
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(upstream, params=params)
            r.raise_for_status()
            j = r.json() or {}
    except Exception as e:
        return {"configured": True, "error": str(e), "label": label}

    cur = j.get("current") or {}
    code = int(cur.get("weather_code") or 0)
    desc, icon = _WMO_CODES.get(code, ("Unknown", "cloud"))
    # Build the daily forecast list — one entry per day with min/max
    # temp + weather code + precipitation sum. Empty list when the
    # upstream didn't return a `daily` block (degrades cleanly).
    forecast: list[dict] = []
    _raw_daily = j.get("daily")
    daily: dict = _raw_daily if isinstance(_raw_daily, dict) else {}
    times = daily.get("time") or []
    tmaxes = daily.get("temperature_2m_max") or []
    tmines = daily.get("temperature_2m_min") or []
    dcodes = daily.get("weather_code") or []
    precips = daily.get("precipitation_sum") or []
    # Sunrise / sunset surface the day's daylight window — Open-Meteo
    # returns ISO timestamps in the resolved IANA timezone (we pass
    # `timezone=auto`). Consumed by `/weather` for "should I go for a
    # run" / "is it light out" practical questions.
    sunrises = daily.get("sunrise") or []
    sunsets = daily.get("sunset") or []
    for i in range(min(len(times), 7)):
        try:
            d_code = int(dcodes[i]) if i < len(dcodes) else 0
        except (TypeError, ValueError):
            d_code = 0
        d_desc, _d_icon = _WMO_CODES.get(d_code, ("Unknown", "cloud"))
        forecast.append({
            "date": times[i],
            "temp_max_c": tmaxes[i] if i < len(tmaxes) else None,
            "temp_min_c": tmines[i] if i < len(tmines) else None,
            "code": d_code,
            "condition": d_desc,
            "precip_mm": precips[i] if i < len(precips) else None,
            "sunrise": sunrises[i] if i < len(sunrises) else None,
            "sunset": sunsets[i] if i < len(sunsets) else None,
        })
    body = {
        "configured": True,
        "label": label,
        "temp_c": cur.get("temperature_2m"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind_kmh": cur.get("wind_speed_10m"),
        "code": code,
        "condition": desc,
        "icon": icon,
        "forecast": forecast,
        "provider": "open-meteo",
        "upstream": upstream,
        "fetched_at": int(now),
        # Open-Meteo returns the resolved IANA timezone when called
        # with `timezone=auto` — surface it so per-user `/time` in
        # the Telegram bot (and any future UI clock) can render local
        # time at the user's saved weather location.
        "timezone": j.get("timezone") or "",
        "timezone_abbrev": j.get("timezone_abbreviation") or "",
        "utc_offset_seconds": j.get("utc_offset_seconds") or 0,
    }
    _weather_cache[key] = (now, body)
    return body


# ============================================================================
# App logs — in-memory ring buffer of recent stdout/stderr lines.
# Admin-only. Frontend polls /api/logs?since=<ts> to incrementally
# fetch new lines; DELETE clears the buffer (does not affect Docker logs).
# Buffer lives in logic/logs.py; the tee is installed at module-import
# time so uvicorn's own lines are captured too.
# ============================================================================
@app.get("/api/logs")
async def api_logs(
    limit: int = 500,
    since: float = 0.0,
    *,
    _admin: AdminUser,
):
    """Return recent persistent-log lines filtered by severity / tag prefix."""
    # Clamp limit to a sane upper bound so a misconfigured client can't
    # pull the whole buffer repeatedly at poll rate.
    limit = max(1, min(int(limit), _logs.MAX_LINES))
    return {
        "logs": _logs.get_recent(limit=limit, since_ts=float(since)),
        "size": _logs.size(),
        "max": _logs.MAX_LINES,
    }


@app.delete("/api/logs")
async def api_logs_clear(_admin: AdminUser):
    """Truncate the in-memory log buffer (audit row written first)."""
    # Audit row BEFORE the clear so the forensic anchor survives even
    # the very destruction it records. Same pattern as DELETE /api/history.
    try:
        with db_conn() as c:
            _ops_mod.write_admin_audit(
                c, "logs_clear",
                target_kind="logs", target_name="in-memory",
                actor=_admin.username or "operator",
                message=f"in-memory log buffer cleared by {_admin.username or 'operator'}",
            )
    except Exception as e:
        print(f"[logs] audit-row write failed before clear: {e}")
    _logs.clear()
    return {"ok": True}


# ----------------------------------------------------------------------------
# Persistent log files. Daily files under /app/data/logs/.
# Admin-only. Three routes:
# GET /api/admin/logs/files                      — directory listing
# GET /api/admin/logs/files/{name}?tail=N        — text body, last N lines (N optional)
# GET /api/admin/logs/files/{name}/download      — full file as attachment
# Filename is validated against the canonical regex inside `safe_log_path`
# so path-traversal attempts (../, absolute paths) bounce with 404.
# ----------------------------------------------------------------------------
@app.get("/api/admin/logs/files")
async def api_admin_logs_files(_admin: AdminUser):
    """List the persistent log files on disk + the log directory."""
    return {"files": _logs.list_persistent_logs(), "log_dir": _logs.LOG_DIR}


@app.get("/api/admin/logs/files/{name}")
async def api_admin_logs_file_view(
    name: str,
    tail: int = 0,
    *,
    _admin: AdminUser,
):
    """Read one persistent-log file by name (path-traversal guarded)."""
    body = _logs.read_persistent_log(name, tail_lines=tail if tail > 0 else None)
    if body is None:
        return JSONResponse(status_code=404, content={"detail": "log file not found"})
    return Response(content=body, media_type="text/plain; charset=utf-8")


@app.get("/api/admin/logs/files/{name}/download")
async def api_admin_logs_file_download(
    name: str,
    _admin: AdminUser,
):
    """Stream one persistent log file. Name must be `<YYYY-MM-DD>.log` per
    `_logs.safe_log_path`'s validator; anything else 404s."""
    path = _logs.safe_log_path(name)
    if not path or not os.path.isfile(path):  # type: ignore[attr-defined]
        return JSONResponse(status_code=404, content={"detail": "log file not found"})
    return FileResponse(path, filename=name, media_type="text/plain; charset=utf-8")


# ============================================================================
# Auth routes (step 1: local login, logout, one-shot bootstrap, /api/me).
# Registered here — above the StaticFiles catch-all — per CLAUDE.md.
# ============================================================================
# ----------------------------------------------------------------------------
# TOTP / 2FA challenge store. In-memory dict mapping
# challenge_id -> {user_id, kind, secret?, issued_at, expires_at}. Lifespan-
# scoped because the matching cookie isn't issued until the second step
# completes. Single-replica pinning (CLAUDE.md) makes this safe.
# ``kind`` is one of:
# "totp_required"      — user has TOTP enrolled; verifying a code
# "totp_setup_required" — policy forces enrolment; user must set up
#                          TOTP before the cookie is issued.
# ----------------------------------------------------------------------------
_TOTP_CHALLENGE_TTL_SECONDS = 5 * 60
_totp_challenges: dict[str, dict] = {}


def _prune_totp_challenges() -> None:
    now = time.time()
    stale: list[str] = []
    for k, v in _totp_challenges.items():
        _exp_raw = v.get("expires_at", 0) if isinstance(v, dict) else 0
        try:
            _exp_val = float(_exp_raw) if isinstance(_exp_raw, (int, float, str)) else 0.0
        except (TypeError, ValueError):
            _exp_val = 0.0
        if _exp_val <= now:
            stale.append(k)
    for k in stale:
        _totp_challenges.pop(k, None)


def _create_totp_challenge(payload: dict) -> tuple[str, int]:
    _prune_totp_challenges()
    cid = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + _TOTP_CHALLENGE_TTL_SECONDS
    _totp_challenges[cid] = {**payload, "expires_at": expires_at}
    return cid, expires_at


def _consume_totp_challenge(cid: str) -> Optional[dict]:
    _prune_totp_challenges()
    return _totp_challenges.pop(cid, None)


def _peek_totp_challenge(cid: str) -> Optional[dict]:
    _prune_totp_challenges()
    return _totp_challenges.get(cid)


# ----------------------------------------------------------------------------
# WebAuthn (passkey) challenge stores. Two flavours, both the same
# in-memory dict shape as the TOTP store -- single-replica deploy makes it
# safe. Pruned lazily on every read/write.
#
# _webauthn_login_challenges -- raw challenge bytes pending second-
#     factor verification. Keyed by challenge_id (opaque token the
#     SPA echoes back). Created by /api/local-auth/webauthn-start;
#     consumed by /api/local-auth/webauthn-finish. 5-min TTL.
#
# _webauthn_register_challenges -- raw challenge bytes pending
#     enrolment. Keyed by user_id (the call sites are authed and we
#     only allow one in-flight enrolment per user). Created by
#     /api/me/webauthn/register-start; consumed by register-finish.
#     5-min TTL.
#
# RP ID + origin are derived per-request from the URL the SPA hit
# (request.url.hostname / .scheme), so dev (localhost:8088) and prod
# (NPM-fronted domain) both work without a settings entry.
# ----------------------------------------------------------------------------
_WEBAUTHN_CHALLENGE_TTL_SECONDS = 5 * 60
_webauthn_login_challenges: dict[str, dict] = {}
_webauthn_register_challenges: dict[int, dict] = {}


# noinspection PyTypeChecker,PyUnresolvedReferences
def _prune_webauthn_challenges() -> None:
    now = time.time()
    for k in [k for k, v in _webauthn_login_challenges.items()
              if float(v.get("expires_at", 0)) <= now]:
        _webauthn_login_challenges.pop(k, None)
    for k in [k for k, v in _webauthn_register_challenges.items()
              if float(v.get("expires_at", 0)) <= now]:
        _webauthn_register_challenges.pop(k, None)


def _create_webauthn_login_challenge(payload: dict) -> tuple[str, int]:
    _prune_webauthn_challenges()
    cid = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + _WEBAUTHN_CHALLENGE_TTL_SECONDS
    _webauthn_login_challenges[cid] = {**payload, "expires_at": expires_at}
    return cid, expires_at


def _consume_webauthn_login_challenge(cid: str) -> Optional[dict]:
    _prune_webauthn_challenges()
    return _webauthn_login_challenges.pop(cid, None)


def _peek_webauthn_login_challenge(cid: str) -> Optional[dict]:
    _prune_webauthn_challenges()
    return _webauthn_login_challenges.get(cid)


def _set_webauthn_register_challenge(user_id: int, payload: dict) -> int:
    _prune_webauthn_challenges()
    expires_at = int(time.time()) + _WEBAUTHN_CHALLENGE_TTL_SECONDS
    _webauthn_register_challenges[user_id] = {
        **payload, "expires_at": expires_at,
    }
    return expires_at


def _consume_webauthn_register_challenge(user_id: int) -> Optional[dict]:
    _prune_webauthn_challenges()
    return _webauthn_register_challenges.pop(user_id, None)

# ----------------------------------------------------------------------------
# Third chunk extracted to `main_pkg.routes_extra` to keep this file
# under the line-count threshold. Star-import re-exports every symbol.
# ----------------------------------------------------------------------------
from main_pkg.routes_extra import *  # noqa: E402,F401,F403

# Trigger routes_extra's load at the tail of THIS module so the
# chain stays unbroken after the middle-chunk extraction.
from main_pkg.routes_extra import *  # noqa: E402,F401,F403
