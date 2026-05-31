"""Per-host provider-history endpoints — `/api/hosts/{id}/http-probe/*`,
`/beszel/services`, `/snmp/history`, `/disk-projection`,
`/snmp/iface_history`. Reads from the persisted provider
sample tables (host_*_samples).

Loads via the star-import chain anchored at `main.py` — every
symbol re-exports into `main`'s namespace so route
decorators reach the shared `app` instance.
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
from main import *  # noqa: E402,F401,F403
# IDE contract: PyCharm/Pyright can't trace `from X import *`, so
# every name resolved through the wildcard above would be flagged as
# "Unresolved reference". The explicit imports below resolve at runtime
# too (Python's import system caches; second-import is a dict lookup),
# so they're safe + they silence the IDE in every scope (TYPE_CHECKING
# blocks DON'T propagate into nested function/closure scopes).
from main import (  # noqa: E402,F401 — explicit for IDE; runtime via the * above
    AdminUser,
    BaseModel,
    Depends,
    HTTPException,
    Request,
    Tunable,
    _actor_from,
    _events,
    _ops_mod,
    _request_client_id,
    app,
    auth,
    db_conn,
    tuning,
)

# Sibling-module names — defined in other main_pkg/* files that end up
# in main's namespace via the chain but PyCharm doesn't trace that.
from main_pkg.hosts_routes import (  # noqa: E402,F401
    _full_host_cache_bust,
    _load_hosts_config,
    _save_hosts_config,
)
from main_pkg.hosts_ssh_routes import _bucket_drawer_series  # noqa: E402,F401
# Note: `Any` / `Optional` come through `from main import *` (main.py
# imports `typing` symbols at its own top); per-file re-imports are
# shadowed + flagged unused by the IDE.

# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).


# Re-import parent's namespace so decorators below find `app`,
# helpers, Pydantic models, etc.

# Re-import parent's namespace so decorators below find every
# symbol from main + main_pkg.hosts_routes.
from main_pkg.hosts_routes import *  # noqa: E402,F401,F403


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
    bucket (the freshness-label contract — see the project conventions). Window
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


@app.post("/api/hosts/{host_id}/http-probe/refresh")
async def api_hosts_http_probe_refresh_row(
    host_id: str,
    _admin: AdminUser,
):
    """Probe THIS host's HTTP-probe URLs NOW and PERSIST to
    ``host_http_samples`` (unlike ``/test`` which is diagnostic-only).

    Drives the host-drawer Refresh button: the drawer's HTTP-probe card
    reads from the sample table, so a freshly-added URL (or a verdict
    that just flipped green) only shows after a sampler tick — this
    on-demand probe+persist makes it show immediately. Returns the
    persist summary; the SPA then re-reads the row via /api/hosts/one.
    """
    from logic.host_http_sampler import probe_and_persist_host as _probe_persist
    hid = (host_id or "").strip()
    if not hid:
        raise HTTPException(400, "host_id required")
    return await _probe_persist(hid)


@app.post("/api/hosts/{host_id}/http-probe/test")
async def api_hosts_http_probe_test_row(
    host_id: str,
    request: Request,
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

    The SPA's editor posts the row's CURRENTLY-EDITED (possibly
    unsaved) http_probe values in the body — ``urls`` / ``verify_tls`` /
    ``content_match`` / ``accepted_status_codes`` (CSV string or list).
    When a field is present it OVERRIDES the saved config so the Test
    button reflects the values currently on screen (e.g. an unchecked
    verify-TLS or a just-typed ``404`` accepted code) without needing a
    Save first. An empty body falls back entirely to the saved config.

    Returns ``{results: [{url, ok, status_code, latency_ms,
    tls_expires_in_days, error}, ...], elapsed_ms, error}``.
    """
    from logic import http_probe as _http_probe
    from logic.host_http_sampler import curated_http_probe_hosts as _curated
    hid = (host_id or "").strip()
    if not hid:
        raise HTTPException(400, "host_id required")
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except (ValueError, TypeError):
        # ValueError (incl. JSONDecodeError, which subclasses
        # ValueError): body wasn't valid JSON. TypeError: body was
        # JSON but not the expected type. Either case: degrade to
        # an empty body so the rest of the handler runs with
        # defaults.
        body = {}
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
    # URLs: the editor's unsaved form list wins when it sent a non-empty
    # one; else fall back to the saved-config resolver (override OR
    # url + services[].url chain).
    body_urls = body.get("urls")
    if isinstance(body_urls, list):
        urls = [str(u).strip() for u in body_urls if str(u or "").strip()]
    else:
        urls = list(host_cfg.get("urls") or [])
    if not urls:
        return {"results": [], "elapsed_ms": 0, "error": "no URLs resolved for this host"}
    timeout = float(tuning.tuning_int(Tunable.HTTP_PROBE_TIMEOUT_SECONDS))
    dns_timeout = float(tuning.tuning_int(Tunable.HTTP_PROBE_DNS_TIMEOUT_SECONDS))
    # content_match / accepted_status_codes / verify_tls: prefer the
    # body's edited value when the field was sent, else the saved config.
    if "content_match" in body:
        content_match = (str(body.get("content_match") or "").strip()) or None
    else:
        content_match = host_cfg.get("content_match")
    if "accepted_status_codes" in body:
        # CSV string OR list — parse_status_codes_csv accepts both.
        codes = _http_probe.parse_status_codes_csv(body.get("accepted_status_codes"))
    else:
        codes = host_cfg.get("accepted_status_codes") or []
    if "verify_tls" in body:
        verify_tls = bool(body.get("verify_tls"))
    else:
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
    refresh: bool = False,
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
        # `?refresh=1` → live re-probe the Beszel hub + persist this
        # host's per-unit rows before reading, so an operator who just
        # fixed a failed unit sees it flip to active immediately instead
        # of waiting for the next sampler tick. Default reads the cached
        # (sampler-written) table.
        if refresh:
            services = await _hbs.refresh_services_now(hid)
        else:
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


def _apply_snmp_block_update(
    curated: list[dict], matched: list[str], transform,
) -> tuple[list[dict], list[str], dict[str, str]]:
    """Walk every curated host, apply ``transform(snmp_block)`` to each
    matched row's `snmp` sub-dict, and return the updated curated list
    plus the applied / errored host-id buckets.

    Extracted because the bulk-SNMP-edit endpoints
    (`/api/hosts/bulk/snmp_vendors` + `/api/hosts/bulk/snmp_tunables`)
    share the same iterate-then-try/except wrapper around the per-host
    `snmp` block update — only the inner mutation differs. The
    callback receives the existing snmp dict (always a dict — None /
    malformed values are coerced upstream) and returns the new dict
    to store; raise on any per-host failure and the loop captures the
    error message in ``errors[hid]`` instead of aborting.
    """
    applied: list[str] = []
    errors: dict[str, str] = {}
    new_curated: list[dict] = []
    for h in curated:
        raw_hid = h.get("id")
        # Non-str host ids never match `matched` (a list[str]) — guard
        # explicitly + `continue` early so PyCharm narrows `hid` to
        # str on every reachable line below (the ternary-then-falsy
        # pattern leaves `hid` typed `str | str-literal-""` which the
        # analyzer doesn't fully collapse on `applied.append(...)`).
        if not isinstance(raw_hid, str) or raw_hid not in matched:
            new_curated.append(h)
            continue
        hid: str = raw_hid
        try:
            raw = h.get("snmp")
            snmp_block: dict = raw if isinstance(raw, dict) else {}
            new_block = transform(snmp_block)
            new_h = dict(h)
            new_h["snmp"] = new_block
            new_curated.append(new_h)
            applied.append(hid)
        except Exception as e:
            errors[hid] = str(e)
            new_curated.append(h)
    return new_curated, applied, errors


def _publish_bulk_event_and_audit(
    *, action: str, request, applied: list[str], actor: str,
    extras: dict, op_type: str,
) -> None:
    """Fan-out a `host:bulk_action_applied` SSE event + write the
    per-host audit-history rows for a bulk host action. Shared by the
    snmp_vendors / snmp_tunables endpoints (and ready for any future
    bulk-edit endpoint that follows the same shape). ``extras`` are
    merged into the SSE payload alongside the common
    `(action, host_ids, actor)` fields. SSE publish errors are
    swallowed + printed so a broken event bus doesn't fail the API."""
    try:
        client_id = _request_client_id(request)
        if applied:
            _events.publish(
                "host:bulk_action_applied",
                {
                    "action": action,
                    "host_ids": applied,
                    "actor": actor,
                    **extras,
                },
                client_id=client_id,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts:bulk] {action} SSE publish failed: {e}")
    _bulk_write_history_rows(
        applied,
        op_type=op_type,
        actor=actor,
        started_ts=time.time(),
    )


def _persist_curated_or_error(
    new_curated: list[dict], missing: list[str], applied: list[str],
) -> "Optional[dict]":
    """Save the updated curated hosts_config + bust the per-host
    provider cache. Returns ``None`` on success OR an error-response
    dict on `HTTPException` (caller early-returns it). Extracted
    because the bulk-SNMP-edit endpoints both follow the same save +
    cache-bust + error-wrapping shape after applying their per-host
    transform — the only difference between the two consumer sites is
    the `action` name in the SSE publish step below the save."""
    if not applied:
        return None
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
    return None


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
# (Note: `from typing import Any, Optional` already at module top — the
#  splitter copied an extra header here; removed to avoid the shadow.)


# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).


# Re-import parent's namespace so decorators below find `app`,
# helpers, Pydantic models, etc.


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

    def _vendor_transform(snmp_block: dict) -> dict:
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
        return new_block

    new_curated, applied, errors = _apply_snmp_block_update(
        curated, matched, _vendor_transform,
    )
    err = _persist_curated_or_error(new_curated, missing, applied)
    if err is not None:
        return err
    actor = _actor_from(request) or "admin"
    # Bulk SSE event so cross-tab observers reload `hosts_config` +
    # refresh each affected row. Vendors edit curated config (NOT
    # failure state) so the SPA handler does a `loadHosts(true)` for
    # this action variant rather than per-row refresh. Audit rows fire
    # one-per-affected-host so the History tab + per-host Timeline
    # both surface the change. Both happen in
    # `_publish_bulk_event_and_audit`.
    _publish_bulk_event_and_audit(
        action="snmp_vendors", request=request,
        applied=applied, actor=actor,
        extras={"mode": mode, "vendors": sorted(cleaned_input)},
        op_type="hosts_bulk_snmp_vendors",
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

    def _tunables_transform(snmp_block: dict) -> dict:
        new_block = dict(snmp_block)
        if body.clear:
            new_block.pop("walk_concurrency", None)
            new_block.pop("wall_clock_budget", None)
        else:
            if wc is not None:
                new_block["walk_concurrency"] = wc
            if wcb is not None:
                new_block["wall_clock_budget"] = wcb
        return new_block

    new_curated, applied, errors = _apply_snmp_block_update(
        curated, matched, _tunables_transform,
    )
    err = _persist_curated_or_error(new_curated, missing, applied)
    if err is not None:
        return err
    actor = _actor_from(request) or "admin"
    # Bulk SSE event + audit rows — same shape as the snmp-vendors
    # sister; both go through `_publish_bulk_event_and_audit`.
    _publish_bulk_event_and_audit(
        action="snmp_tunables", request=request,
        applied=applied, actor=actor,
        extras={
            "clear": bool(body.clear),
            "walk_concurrency": wc,
            "wall_clock_budget": wcb,
        },
        op_type="hosts_bulk_snmp_tunables",
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


# ----------------------------------------------------------------------------
# Split continuation: main_pkg.scan_routes → main_pkg.auth_routes.
# ----------------------------------------------------------------------------
from main_pkg.scan_routes import *  # noqa: E402,F401,F403


# noinspection DuplicatedCode
def __getattr__(name):
    """Module-level resolver for cross-module underscore-prefixed leaks.
    Delegates to the shared helper so the 33-line PEP 562 implementation
    lives in one place. See main_pkg._resolver for the full rationale.
    The 5-line delegator IS duplicated across 12 files — PEP 562 requires
    one __getattr__ per module; suppress the duplicated-code hint."""
    # noinspection PyProtectedMember
    from main_pkg._resolver import resolve
    return resolve(__name__, name)
