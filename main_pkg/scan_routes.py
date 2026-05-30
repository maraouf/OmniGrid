"""Port-scan endpoints + small admin-test helpers —
`/api/hosts/{id}/port-scan`, `/api/history/port-scan/*/ports`,
`/api/ping/test`, `/api/http-probe/test`, `/api/auth/providers`,
`/api/notify-test`.

Loads via the star-import chain anchored at `main.py` — every
symbol re-exports into `main`'s namespace so route
decorators reach the shared `app` instance.
"""
"""Middle chunk in the main → main_pkg.hosts_routes → main_pkg.hosts_provider_routes
→ main_pkg.auth_routes star-import chain. See auth_routes's
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
from main import *  # noqa: E402,F401,F403
# IDE contract: PyCharm/Pyright can't trace `from X import *`, so
# every name resolved through the wildcard above would be flagged as
# "Unresolved reference". The explicit imports below resolve at
# runtime too (Python's import system caches; second-import is a dict
# lookup), so they're safe + they silence the IDE in every scope.
from main import (  # noqa: E402,F401 — explicit for IDE; runtime via the * above
    AdminUser,
    BaseModel,
    FileResponse,
    HTTPException,
    JSONResponse,
    Request,
    Response,
    Settings,
    Tunable,
    _actor_from,
    _cache,
    _db,
    _events,
    _logs,
    _ops_mod,
    _request_client_id,
    app,
    db_conn,
    get_setting,
    get_setting_bool,
    notify,
    oidc,
    read_version,
    # `schedules` arrives at runtime via main.py's `from logic
    # import schedules` re-exported through the star-import above.
    # Used by the `schedules.UNKNOWN_ACTOR` fallback constant in
    # admin-required write routes (4 sites in this file).
    schedules,
    spawn_background_task,
    tuning,
    uuid,
)

# `_stamp_test_success` lives in main_pkg.admin_ai_routes which loads
# EARLIER in the chain than scan_routes — at runtime it reaches us
# via the wildcard, but PyCharm doesn't trace that.
from main_pkg.admin_ai_routes import _stamp_test_success, _log_provider_test_start  # noqa: E402,F401
# `PingTestIn` + `PortScanIn` live in main_pkg.hosts_provider_routes —
# also EARLIER in the chain (defined before its tail `from
# main_pkg.scan_routes import *`) so a real import resolves cleanly.
# PortScanIn was previously relied on via `from main import *`, but the
# split load-order meant main hadn't re-exported it yet when scan_routes
# snapshotted main's namespace, so the port-scan route hit a runtime
# NameError on `PortScanIn()` — explicit import fixes it order-independently.
from main_pkg.hosts_provider_routes import PingTestIn, PortScanIn  # noqa: E402,F401

# Sibling-module names — defined in other main_pkg/* files
# that end up in main's namespace via the chain.
from main_pkg.hosts_routes import (  # noqa: E402,F401
    _load_hosts_config,
)
import asyncio  # noqa: F401,F811  (used at runtime; star-import shadow flags as duplicate)
from typing import Optional


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


def _resolve_host_probe_target(h: dict, hid: str) -> str:
    """Resolve the per-host probe target via the canonical fallback
    chain: ``address`` (provider-independent — operator sets in
    Admin → Hosts) → ``ping.host`` → ``ssh.fqdn`` → ``ssh.host`` →
    bare ``host_id``. Defensive extraction of `ssh` / `ping` sub-dicts
    so a non-dict value (corrupt settings JSON, mid-migration row)
    can't crash the resolver. Extracted because the same chain is
    consumed by `_run_port_scan_async` AND the `/api/ping/test` route
    — keeping it in one place avoids the per-fork drift class where
    one site forgets the `address`-first rule."""
    _raw_ssh = h.get("ssh")
    ssh_cfg: dict = _raw_ssh if isinstance(_raw_ssh, dict) else {}
    _raw_ping = h.get("ping")
    ping_cfg: dict = _raw_ping if isinstance(_raw_ping, dict) else {}
    return (
        (h.get("address") or "").strip()
        or (ping_cfg.get("host") or "").strip()
        or (ssh_cfg.get("fqdn") or "").strip()
        or (ssh_cfg.get("host") or "").strip()
        or hid
    )


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
    diagnostic_ports: Optional[set] = None,
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
                        diagnostic_ports=diagnostic_ports,
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
                    diagnostic_ports=diagnostic_ports,
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
    target = _resolve_host_probe_target(h, hid)
    target = target.strip() or hid
    # Effective config: request body → per-host → global → built-in.
    # Narrow `body` to PortScanIn (drop None) so every `body.X` access
    # below doesn't trip "Member 'None' of 'PortScanIn | None'" lint
    # diagnostics. The if-branch reassignment is the only form the
    # type-checker reliably narrows from `T | None` to `T`.
    if body is None:
        body = PortScanIn()
    from logic import port_scanner as _ps
    from logic import service_catalog as _ps_catalog
    _per_call_ports = (body.ports or "").strip()
    if _per_call_ports:
        # Per-call explicit override — a caller that asked for a specific
        # set (e.g. a targeted re-scan) wants ONLY that set, so honour it
        # verbatim with no defaults floor.
        ports_list = _ps.parse_port_csv(_per_call_ports)
    else:
        # Otherwise the code DEFAULT_PORTS are a FLOOR that's ALWAYS
        # scanned, and the per-host / global custom CSV is UNIONED on top
        # (it ADDS ports — it no longer REPLACES the defaults). This fixes
        # the recurring "I added a well-known / app port to the scanner but
        # my scans don't detect it": a saved port_scan_default_ports CSV
        # used to shadow the code defaults entirely, so newly-added ports
        # (Beszel agent 45876, DNS-over-TLS 853, etc.) never reached the
        # scan. Defaults-as-floor means a default port can't be silently
        # dropped by an out-of-date custom list.
        ports_list = list(_ps.DEFAULT_PORTS)
        _custom_csv = (
            (ps_cfg.get("ports") or "").strip()
            or (get_setting(Settings.PORT_SCAN_DEFAULT_PORTS) or "").strip()
        )
        if _custom_csv:
            _seen_base = set(ports_list)
            for _p in _ps.parse_port_csv(_custom_csv):
                if _p not in _seen_base:
                    ports_list.append(_p)
                    _seen_base.add(_p)
    # Always ALSO scan the ports of every app/service configured on THIS
    # host (services[].probe.ports[] + the legacy top-level services[].port),
    # unioned onto the base list. Operator-flagged: a pinned app's port
    # (e.g. Beszel Agent 45876) was missed when the operator ran a custom
    # port list that didn't include it — but if the operator cared enough
    # to pin the app, its port should always be scanned. PROTOCOL-AWARE:
    # a chip port declared `udp` feeds the UDP scan list (below), not the
    # TCP one — so e.g. a Tailscale/WireGuard UDP port is UDP-probed
    # rather than TCP-probed (a TCP connect to a UDP-only port always
    # fails). The legacy top-level `services[].port` has no protocol so
    # it's treated as TCP. Union + dedupe, preserving base order.
    _app_ports_tcp: list[int] = []
    _app_ports_udp: list[int] = []
    for _svc in (h.get("services") or []):
        if not isinstance(_svc, dict):
            continue
        _cands: list[tuple] = [(_svc.get("port"), "tcp")]
        for _pp in ((_svc.get("probe") or {}).get("ports") or []):
            if isinstance(_pp, dict):
                _proto = _pp.get("protocol") or "tcp"
                _proto = _proto.strip().lower() if isinstance(_proto, str) else "tcp"
                _cands.append((_pp.get("port"), _proto))
        # ALSO union the bound catalog TEMPLATE's default ports. A pinned
        # app whose chip never had `probe.ports` populated (e.g. pinned via
        # catalog with the probe left disabled) should STILL have its
        # template ports scanned — matching the "if the operator cared
        # enough to pin the app, its port should always be scanned" intent
        # above. This was the operator-reported Beszel Agent (45876) miss:
        # 45876 is in the catalog template but the chip's own probe.ports
        # was empty, so the union didn't pick it up. Template `http`/`https`
        # protocols map to the TCP scan; `udp` to the UDP scan.
        _cat_id = _svc.get("catalog_id")
        # isinstance (not `is not None`) so the type checker narrows
        # `Any | None` → `int | str` for the int() coercion below.
        if isinstance(_cat_id, (int, str)):
            try:
                _tpl = _ps_catalog.get_catalog_by_id(int(_cat_id))
            except (TypeError, ValueError):
                _tpl = None
            for _dp in ((_tpl or {}).get("default_ports") or []):
                if isinstance(_dp, dict):
                    _dproto = _dp.get("protocol") or "tcp"
                    _dproto = _dproto.strip().lower() if isinstance(_dproto, str) else "tcp"
                    _cands.append((_dp.get("port"), "udp" if _dproto == "udp" else "tcp"))
        for _cand, _proto in _cands:
            if not isinstance(_cand, (int, str)):
                continue
            try:
                _pn = int(_cand)
            except (TypeError, ValueError):
                continue
            if not (1 <= _pn <= 65535):
                continue
            (_app_ports_udp if _proto == "udp" else _app_ports_tcp).append(_pn)
    if _app_ports_tcp:
        _seen = set(ports_list)
        for _pn in _app_ports_tcp:
            if _pn not in _seen:
                ports_list.append(_pn)
                _seen.add(_pn)
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
        _per_call_udp = (body.udp_ports or "").strip()
        if _per_call_udp:
            # Per-call explicit UDP override — honour verbatim.
            udp_ports_list = _ps.parse_port_csv(_per_call_udp)
        else:
            # DEFAULT_UDP_PORTS as a FLOOR + per-host / global custom UDP
            # CSV unioned on top, mirroring the TCP path above so a saved
            # custom UDP list can't shadow well-known UDP ports (DNS 53,
            # DHCP 67, NTP 123, …).
            udp_ports_list = list(_ps_udp.DEFAULT_UDP_PORTS)
            _custom_udp_csv = (
                (ps_cfg.get("udp_ports") or "").strip()
                or (get_setting(Settings.PORT_SCAN_UDP_DEFAULT_PORTS) or "").strip()
            )
            if _custom_udp_csv:
                _seen_udp_base = set(udp_ports_list)
                for _p in _ps.parse_port_csv(_custom_udp_csv):
                    if _p not in _seen_udp_base:
                        udp_ports_list.append(_p)
                        _seen_udp_base.add(_p)
        # Union the host's udp-declared app/service ports (collected above)
        # onto the UDP scan list so a UDP app port (e.g. a WireGuard /
        # Tailscale listener) is actually UDP-probed.
        if _app_ports_udp:
            _useen = set(udp_ports_list)
            for _pn in _app_ports_udp:
                if _pn not in _useen:
                    udp_ports_list.append(_pn)
                    _useen.add(_pn)
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
            diagnostic_ports=set(_app_ports_tcp),
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
    target = _resolve_host_probe_target(h, hid)
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
    # Log target = `host:port/transport` so operators see WHICH host
    # the test resolved to (the bare host_id may differ from the
    # resolved address per the canonical address-fallback chain).
    _ping_target = f"{target}:{int(port)}/{transport}"
    _log_provider_test_start("ping", target=_ping_target)
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
    }, target=_ping_target)


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
    _log_provider_test_start("http_probe", target=url)
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
    }, target=url)


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
                actor=_admin.username or schedules.UNKNOWN_ACTOR,
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
    # Surface the configured Apprise URL as the test target so
    # operators see WHICH endpoint received the test fire.
    from logic.db import get_setting as _get_setting_local
    from logic.settings_keys import Settings as _Settings_local
    _apprise_target = (_get_setting_local(_Settings_local.APPRISE_URL) or "").strip() or "(unset)"
    _log_provider_test_start("apprise", target=_apprise_target)
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
                actor=_admin.username or schedules.UNKNOWN_ACTOR,
                message=f"apprise channel test fired by {_admin.username or 'operator'}",
            )
    except Exception as e:
        print(f"[notify] apprise_test audit-row write failed: {e}")
    return _stamp_test_success("apprise", {
        "ok": bool(result.get("ok")),
        "detail": result.get("error") or result.get("skipped") or ("sent" if result.get("ok") else "failed"),
        "status": int(result.get("status") or 0),
    }, target=_apprise_target)


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
    actor = (_admin.username or schedules.UNKNOWN_ACTOR)
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
    """Snapshot of the CALLING USER's other active tabs. Excludes the
    calling tab via `client_id` self-filter AND scopes to the caller's
    own `actor` so a user only ever sees THEIR OWN tabs (e.g. same
    account on a phone + a laptop) — never another user's. Showing
    cross-user tabs was a privacy leak + a cross-user activity-conflict
    source. Used at SPA boot to seed the local map before the SSE
    stream catches up. Privacy-first: a missing/empty actor (shouldn't
    happen behind the auth middleware) yields an EMPTY list rather than
    leaking every tab."""
    cid = _request_client_id(request)
    actor = _actor_from(request)
    _tab_activity_prune()
    out = []
    for tcid, ent in _tab_activity_registry.items():
        if cid and tcid == cid:
            continue
        # Same-user scope — drop other users' tabs (and drop everything
        # when we can't identify the caller).
        if not actor or ent.get("actor") != actor:
            continue
        out.append({"client_id": tcid, **ent})
    return {"tabs": out}


@app.get("/api/healthz")
async def healthz():
    """Liveness probe — MUST be bulletproof.

    Returns 200 with minimal in-memory state ONLY. No file IO, no DB
    access, no anything that could be blocked by a hung sampler or a
    SQLite contention. The Docker swarm healthcheck only checks for
    HTTP 200 — anything that delays this endpoint past the
    healthcheck timeout triggers a container restart.
    `read_version()` was previously called inline (small file read,
    micro-second-cost in steady state) but under event-loop
    starvation that file IO becomes a multi-second wait. Removed so
    healthz can NEVER be slower than what an in-memory dict lookup
    takes. `/api/version` is the dedicated endpoint for version
    queries (still reads the file — but it's not on the critical
    healthcheck path).
    """
    # cache_age is a single dict lookup; DB_PATH_ERROR is a
    # module-level string. Neither can block. `version` is OMITTED
    # from this response on purpose — keeping healthz under any
    # possible-starvation-budget is more important than reporting
    # the version here (operators use /api/version for that).
    return {
        "ok": _db.DB_PATH_ERROR is None,
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
async def api_public_ip(_admin: AdminUser, force: bool = False):
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
    data = await _public_ip.fetch(force=force)
    if data is None:
        return {"enabled": True, "error": "lookup failed — see Admin → Logs"}
    return {"enabled": True, **data}


@app.get("/api/public-ip/history")
async def api_public_ip_history(_admin: AdminUser, limit: int = 100):
    """Admin-only public-IP change history. Returns the most-recent
    ``limit`` rows (default 100; 1..1000) from ``public_ip_history``,
    newest first. Each row carries the ts + ip + isp + asn + country +
    city snapshot taken at the moment the IP changed.

    Drives the AI palette's "when did my IP / ISP last change?"
    questions + the Admin → Public IP history table. Always allowed
    (no `tuning_public_ip_enabled` gate) so the operator can review
    history even after disabling the active fetch.
    """
    try:
        n = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        n = 100
    rows = []
    try:
        with db_conn() as c:
            for r in c.execute(
                "SELECT ts, ip, isp, asn, country, city "
                "FROM public_ip_history ORDER BY ts DESC LIMIT ?",
                (n,),
            ).fetchall():
                rows.append({
                    "ts": int(r[0]),
                    "ip": r[1] or "",
                    "isp": r[2] or "",
                    "asn": r[3] or "",
                    "country": r[4] or "",
                    "city": r[5] or "",
                })
    except Exception as e:  # noqa: BLE001
        return {"history": [], "error": str(e)}
    return {"history": rows, "count": len(rows)}


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/weather")
async def api_weather(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    label: str = "",
    force: bool = False,
):
    """Fetch current conditions + 7-day forecast + astronomy from
    WeatherAPI.com for one lat/lon.

    Delegates to ``logic.weather.fetch`` which owns the per-coord
    in-process TTL cache and the master-toggle gate. When no
    coordinates are passed, falls back to the operator-configured
    default location (Admin → Weather). Result includes moon-phase
    + moon-illumination fields on every forecast day so the AI
    palette + Telegram bot can answer moon-related questions.
    """
    # No coords from the caller — try the operator's default location
    # so the topbar widget / AI palette context get SOMETHING when the
    # user hasn't pinned coordinates yet.
    if lat is None or lon is None:
        from logic import weather as _weather_mod
        loc = _weather_mod.default_location()
        if not loc:
            return {"configured": False}
        lat = loc["lat"]
        lon = loc["lon"]
        if not label:
            label = loc.get("label") or ""
    from logic import weather as _weather_mod
    return await _weather_mod.fetch(float(lat), float(lon), label=label, force=force)


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/weather/test")
async def api_weather_test(
    body: dict,
    _admin: auth.User = Depends(auth.require_admin),  # noqa: B008
):
    """Test-connection probe — dispatches to Open-Meteo or
    WeatherAPI.com based on the requested ``provider`` (default
    falls through to the persisted ``weather_provider`` setting).
    Admin-only.

    Accepts ``{provider, api_key, base_url, lat, lon}`` (every field
    optional; falls through to the persisted values when blank —
    so an admin can re-test after first save without re-typing
    the secret). Returns ``{ok: bool, detail: str, ...}``.
    """
    from logic import weather as _weather_mod
    from logic.db import get_setting
    # `Settings` is a class — PyCharm flags the `as _s` rename as
    # "CamelCase imported as constant" (one warning class). Use a
    # leading-uppercase rename to mirror Python's convention that a
    # class identifier starts uppercase, so `_S` reads as a class
    # alias rather than a "constant" the linter mistakes it for.
    from logic.settings_keys import Settings as SettingsAlias
    _S = SettingsAlias
    requested_provider = (body.get("provider") or "").strip().lower()
    if requested_provider not in ("open-meteo", "weatherapi"):
        requested_provider = _weather_mod.provider()
    # Log target = provider + base-url-shape (resolved later in
    # the body) — START line goes out now so the operator's click
    # is observable even when the body short-circuits early.
    _wx_target = f"provider={requested_provider}"
    _log_provider_test_start("weather", target=_wx_target)
    # Resolve test coords in priority order:
    #   1. Explicit lat/lon in the request body (operator typing them
    #      into the Test form — power-user override).
    #   2. First available user-profile location (Settings → Profile
    #      → Weather across any active user) — matches the runtime
    #      sampler's source-of-truth.
    #   3. Legacy operator-set default (pre-consolidation back-compat).
    # When all three are empty, return a clear "configure a user
    # location" error pointing operators at the right surface.
    try:
        lat = float(body.get("lat")) if body.get("lat") not in (None, "") else None
        lon = float(body.get("lon")) if body.get("lon") not in (None, "") else None
    except (TypeError, ValueError):
        return _stamp_test_success("weather", {
            "ok": False, "detail": "lat/lon must be numbers",
        }, target=_wx_target)
    if lat is None or lon is None:
        user_locs = _weather_mod.user_locations()
        if user_locs:
            lat = user_locs[0]["lat"]
            lon = user_locs[0]["lon"]
        else:
            legacy = _weather_mod.default_location()
            if legacy:
                lat = legacy["lat"]
                lon = legacy["lon"]
            else:
                return _stamp_test_success("weather", {
                    "ok": False,
                    "detail": "no user has configured a weather location yet — "
                              "set one in Settings → Profile → Weather, then "
                              "re-run Test",
                }, target=_wx_target)
    base = (body.get("base_url") or "").strip().rstrip("/")
    try:
        timeout = float(tuning.tuning_int(Tunable.WEATHER_FETCH_TIMEOUT_SECONDS))
    except (KeyError, ValueError, TypeError):
        timeout = 8.0
    if requested_provider == "weatherapi":
        raw_key = (body.get("api_key") or "").strip()
        if not raw_key:
            raw_key = (get_setting(_S.WEATHER_API_KEY) or "").strip()
        if not raw_key:
            return _stamp_test_success("weather", {
                "ok": False, "detail": "no API key configured or supplied",
            }, target=_wx_target)
        if not base:
            base = _weather_mod.base_url()
        if not base:
            return _stamp_test_success("weather", {
                "ok": False,
                "detail": "no WeatherAPI base URL configured — "
                          "paste one in Admin → Weather or set the "
                          "WEATHER_WEATHERAPI_ENDPOINT env var",
            }, target=_wx_target)
        upstream = base + "/forecast.json"
        params = {
            "key": raw_key,
            "q": f"{round(lat, 2)},{round(lon, 2)}",
            "days": "1",
            "aqi": "no",
            "alerts": "no",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(upstream, params=params)
                if r.status_code == 401 or r.status_code == 403:
                    return _stamp_test_success("weather", {
                        "ok": False,
                        "detail": f"HTTP {r.status_code} — API key rejected",
                        "status_code": r.status_code, "upstream": upstream,
                    }, target=upstream)
                r.raise_for_status()
                j = r.json() or {}
        except httpx.HTTPStatusError as e:
            return _stamp_test_success("weather", {
                "ok": False,
                "detail": f"HTTP {e.response.status_code}",
                "status_code": e.response.status_code,
                "upstream": upstream,
            }, target=upstream)
        except Exception as e:  # noqa: BLE001
            return _stamp_test_success("weather", {
                "ok": False, "detail": str(e), "upstream": upstream,
            }, target=upstream)
        cur = j.get("current") or {}
        loc_obj = j.get("location") or {}
        # Stamp a successful Test sample directly into `weather_samples`
        # so operators see historical data immediately after the first
        # successful Test, instead of waiting up to an hour for the
        # next sampler tick. This is also why the sampler interval
        # default of 3600s is acceptable — the Test path covers the
        # cold-start case + first-time-configured-by-operator UX gap.
        # noinspection PyBroadException
        try:
            from logic import weather as _w
            from logic import weather_sampler as _ws
            # Re-fetch through the dispatcher so we get the FULL
            # normalised shape (forecast + moon data) for the sample
            # write, not just the trimmed test-response.
            full_body = await _w.fetch(lat, lon, label=loc_obj.get("name") or "")
            if full_body and not full_body.get("error") and full_body.get("configured"):
                _ws.write_sample(full_body,
                                 loc={"lat": lat, "lon": lon,
                                      "label": loc_obj.get("name") or ""})
        except Exception:  # noqa: BLE001 — sample-write failure must not break Test
            pass
        return _stamp_test_success("weather", {
            "ok": True,
            "detail": f"Live: {cur.get('temp_c')}°C at "
                      f"{loc_obj.get('name', '')}, {loc_obj.get('country', '')} "
                      f"(WeatherAPI.com — moon-phase data available, sample saved)",
            "temp_c": cur.get("temp_c"),
            "location": loc_obj.get("name") or "",
            "provider": "weatherapi",
            "supports_moon": True,
            "upstream": upstream,
        }, target=upstream)
    # Open-Meteo — no API key, plain GET against the configured endpoint.
    if not base:
        base = _weather_mod.base_url()
    if not base:
        return _stamp_test_success("weather", {
            "ok": False,
            "detail": "no Open-Meteo base URL configured — "
                      "paste one in Admin → Weather or set the "
                      "WEATHER_OPEN_METEO_ENDPOINT env var",
        }, target=_wx_target)
    upstream = base
    params = {
        "latitude": str(round(lat, 2)),
        "longitude": str(round(lon, 2)),
        "current": "temperature_2m",
        "forecast_days": "1",
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(upstream, params=params)
            r.raise_for_status()
            j = r.json() or {}
    except httpx.HTTPStatusError as e:
        return _stamp_test_success("weather", {
            "ok": False,
            "detail": f"HTTP {e.response.status_code}",
            "status_code": e.response.status_code, "upstream": upstream,
        }, target=upstream)
    except Exception as e:  # noqa: BLE001
        return _stamp_test_success("weather", {
            "ok": False, "detail": str(e), "upstream": upstream,
        }, target=upstream)
    cur = j.get("current") or {}
    # Stamp a successful Open-Meteo test sample directly into
    # `weather_samples` so the historical data starts populating
    # immediately rather than waiting for the next sampler tick.
    # noinspection PyBroadException
    try:
        from logic import weather as _w
        from logic import weather_sampler as _ws
        full_body = await _w.fetch(lat, lon)
        if full_body and not full_body.get("error") and full_body.get("configured"):
            _ws.write_sample(full_body,
                             loc={"lat": lat, "lon": lon, "label": ""})
    except Exception:  # noqa: BLE001 — sample-write failure must not break Test
        pass
    return _stamp_test_success("weather", {
        "ok": True,
        "detail": f"Live: {cur.get('temperature_2m')}°C "
                  f"(Open-Meteo — no API key required, NO moon data, sample saved)",
        "temp_c": cur.get("temperature_2m"),
        "provider": "open-meteo",
        "supports_moon": False,
        "upstream": upstream,
    }, target=upstream)


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/weather/history")
async def api_weather_history(
    limit: int = 100,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    _admin: auth.User = Depends(auth.require_admin),  # noqa: B008
):
    """Historical weather + moon samples from the `weather_samples`
    table. Admin-only.

    Optional ``lat`` / ``lon`` narrow to one coordinate; omitted =
    every coordinate. Used by the AI palette context-builder + Admin
    → Weather history table + Telegram historical-comparison
    feature.
    """
    from logic import weather_sampler as _sampler
    rows = _sampler.recent_samples(limit=int(max(1, min(limit, 5000))),
                                   lat=lat, lon=lon)
    return {"history": rows, "count": len(rows)}


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
                actor=_admin.username or schedules.UNKNOWN_ACTOR,
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
    # Defence-in-depth: a read exception (permissions, transient FS
    # error, a huge file that trips an OS limit) would otherwise surface
    # as a bare HTTP 500 in the viewer with no actionable detail. Catch
    # it and return the message as the body so the operator sees WHAT
    # failed instead of a blank 500.
    try:
        body = _logs.read_persistent_log(name, tail_lines=tail if tail > 0 else None)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[logs] read_persistent_log({name!r}, tail={tail}) failed: {e}")
        return Response(
            content=f"(unable to read log file: {type(e).__name__}: {e})",
            media_type="text/plain; charset=utf-8",
        )
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
# Registered here — above the StaticFiles catch-all — per the project conventions.
# ============================================================================
# ----------------------------------------------------------------------------
# TOTP / 2FA challenge store. In-memory dict mapping
# challenge_id -> {user_id, kind, secret?, issued_at, expires_at}. Lifespan-
# scoped because the matching cookie isn't issued until the second step
# completes. Single-replica pinning (the project conventions) makes this safe.
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


# Trigger auth_routes's load at the tail so chain stays intact.

from main_pkg.auth_routes import *  # noqa: E402,F401,F403


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
