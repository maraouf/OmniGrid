"""Per-service reachability sampler — lifespan-managed time-series writer.

Walks every curated host's ``services[]`` looking for entries whose
``probe.enabled === true`` and writes one row per (host_id, service_idx,
ts) into ``service_samples``. Distinct from the host-level HTTP probe:
THIS one runs at the service-CHIP granularity so an operator who wants
to know "is the Plex chip on host01 still reachable" gets a per-chip
green/red dot driven by recent samples.

Probe contract per chip:

    services[].probe = {
        enabled: bool,
        type: "tcp" | "http",  # default "tcp"
        port: int | null,       # derived from `url` when blank
        path: str,              # "/" default, http-only
        expected_status: int,   # 2xx default, http-only
    }

The TCP path is just a connect probe with a timeout. The HTTP path
fetches `url` (overriding port / path when the operator set them) and
checks for `expected_status`. Both paths return rtt_ms on success +
error on failure.

Lifespan-managed via :func:`service_sampler_loop`; stays dormant when
the master ``service_probe_enabled`` setting is off OR no host has
any service-probe entry enabled. Each tick respects ``tuning_service_
probe_sample_interval_seconds`` (0 = inherit global stats interval).

Per-(provider, host) auto-pause: failure threshold is ``tuning_
service_probe_failure_pause_rounds`` (default 5). "All services on
this host failed" counts as one round; mixed success / failure within
a host stays out of the pause counter (operator wants visibility into
"one service is broken" without losing the rest).
"""
from __future__ import annotations

import asyncio
import socket
import sqlite3
import time
from typing import Optional
from urllib.parse import urlparse

from logic import tuning
from logic.tuning import Tunable as _Tunable
from logic.db import (
    db_conn,
    get_setting_bool,
    iter_curated_hosts,
)
from logic.settings_keys import Settings


# Coercion helpers — same shape as host_metrics_sampler / host_pulse_sampler.
def _safe_int(v, default: int = 0) -> int:
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _int_or_none(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _resolve_service_probe_interval() -> int:
    """Sampler tick cadence — thin wrapper for binary-compat. The
    canonical implementation lives at `tuning.resolve_provider_interval`
    (shared across http_probe / service_probe samplers per CLAUDE.md
    priority L duplicate-code rule).
    """
    return tuning.resolve_provider_interval(_Tunable.SERVICE_PROBE_SAMPLE_INTERVAL_SECONDS)


def _curated_service_probe_targets() -> list[dict]:
    """Walk every curated host's services[] for probe-enabled entries.

    Returns one target per opted-in service chip:

        {
            "host_id": str,
            "service_idx": int,
            "service_name": str,
            "probe_type": "tcp" | "http",
            "url": str,                # raw URL from the chip
            "host": str,               # parsed hostname
            "port": int | None,
            "path": str,               # http-only
            "expected_status": int,    # http-only
        }

    Targets with neither `url` nor resolvable host/port are dropped
    (cannot probe an empty target).
    """
    out: list[dict] = []
    for row in iter_curated_hosts():
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        services = row.get("services")
        if not isinstance(services, list):
            continue
        for idx, svc in enumerate(services):
            if not isinstance(svc, dict):
                continue
            probe_cfg = svc.get("probe")
            if not isinstance(probe_cfg, dict):
                continue
            if not probe_cfg.get("enabled"):
                continue
            probe_type = (probe_cfg.get("type") or "tcp").strip().lower()
            if probe_type not in ("tcp", "http"):
                probe_type = "tcp"
            url = (svc.get("url") or "").strip()
            # Parse URL to extract host / port / path. Operator-set
            # `probe.port` overrides URL-derived port; same for path.
            parsed_host = ""
            parsed_port = None
            parsed_path = "/"
            if url:
                try:
                    pu = urlparse(url if "://" in url else "tcp://" + url)
                    parsed_host = (pu.hostname or "").strip()
                    parsed_port = pu.port
                    parsed_path = (pu.path or "/").strip() or "/"
                except (ValueError, AttributeError):
                    pass
            override_port = _int_or_none(probe_cfg.get("port"))
            override_path = (probe_cfg.get("path") or "").strip()
            port = override_port if override_port and override_port > 0 else parsed_port
            path = override_path or parsed_path
            # No host AND no resolvable target → skip; row carries no
            # probeable data.
            if not parsed_host:
                continue
            if probe_type == "tcp" and not port:
                # Fall back to default web ports based on URL scheme.
                lc = url.lower()
                if lc.startswith("https://"):
                    port = 443
                elif lc.startswith("http://"):
                    port = 80
                else:
                    continue
            expected_status = _safe_int(probe_cfg.get("expected_status"))
            svc_name = (svc.get("name") or svc.get("label") or url or f"service-{idx}").strip()
            # Multi-port shape (Apps feature). When `probe.ports[]` is
            # populated, each port becomes a sub-target probed
            # independently; the chip's final status is rolled up
            # ("any port up = chip alive, all down = chip dead") at
            # row-persist time so historical schema stays at one row
            # per (host_id, service_idx). Per-port HISTORICAL detail
            # is a follow-up — current view is the live per-tick
            # snapshot.
            ports_raw = probe_cfg.get("ports")
            sub_ports: list[dict] = []
            if isinstance(ports_raw, list):
                for p in ports_raw:
                    if not isinstance(p, dict):
                        continue
                    pi = _int_or_none(p.get("port"))
                    if not pi or not (1 <= pi <= 65535):
                        continue
                    proto = (p.get("protocol") or "tcp")
                    proto = proto.strip().lower() if isinstance(proto, str) else "tcp"
                    sub_path = (p.get("probe_path") or "").strip() or "/"
                    sub_status = _safe_int(p.get("probe_status"))
                    sub_label = (p.get("label") or "").strip()
                    # Per-port probe type = http when protocol is http/https
                    # OR a probe_path was supplied (operator opted into
                    # HTTP-level check); TCP otherwise.
                    sub_type = ("http" if proto in ("http", "https") or sub_path != "/"
                                else "tcp")
                    sub_ports.append({
                        "port": pi,
                        "protocol": proto,
                        "label": sub_label,
                        "probe_path": sub_path,
                        "probe_status": sub_status,
                        "probe_type": sub_type,
                    })
            out.append({
                "host_id": hid,
                "service_idx": idx,
                "service_name": svc_name,
                "probe_type": probe_type,
                "url": url,
                "host": parsed_host,
                "port": port,
                "path": path,
                "expected_status": expected_status,
                "sub_ports": sub_ports,
            })
    return out


async def _probe_tcp(host: str, port: int, timeout: float) -> dict:
    """One-shot TCP-connect probe. Returns ``{alive, rtt_ms, error}``."""
    started = time.monotonic()
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        try:
            writer.close()
            await writer.wait_closed()
        except (OSError, ConnectionError):
            pass
        rtt_ms = int((time.monotonic() - started) * 1000)
        return {"alive": True, "rtt_ms": rtt_ms, "error": None}
    except asyncio.TimeoutError:
        return {"alive": False, "rtt_ms": None, "error": "timeout"}
    except asyncio.CancelledError:
        raise
    except (OSError, ConnectionError, socket.gaierror) as e:
        return {"alive": False, "rtt_ms": None, "error": f"{type(e).__name__}: {str(e)[:120]}"}


async def _probe_http(url: str, expected_status: int, timeout: float) -> dict:
    """One-shot HTTP HEAD/GET probe. Uses the lazy-imported httpx
    client to stay consistent with the rest of the codebase.
    """
    try:
        import httpx  # type: ignore
    except ImportError:
        return {"alive": False, "rtt_ms": None, "error": "httpx not available"}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            verify=False,  # operator opted into a probe; cert validity isn't the question
        ) as client:
            # Try HEAD first; fall back to GET on 405. Lots of services
            # don't implement HEAD.
            r = await client.head(url)
            if r.status_code == 405:
                r = await client.get(url)
        rtt_ms = int((time.monotonic() - started) * 1000)
        if expected_status:
            ok = r.status_code == expected_status
        else:
            ok = 200 <= r.status_code < 400
        if ok:
            return {"alive": True, "rtt_ms": rtt_ms, "error": None}
        return {
            "alive": False,
            "rtt_ms": rtt_ms,
            "error": f"unexpected status {r.status_code}",
        }
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        return {"alive": False, "rtt_ms": None, "error": f"{type(e).__name__}: {str(e)[:120]}"}


def _persist_row(host_id: str, service_idx: int, alive: bool,
                 rtt_ms: Optional[int], error: Optional[str], ts: int,
                 port: int = 0) -> None:
    """Write one row into ``service_samples``.

    ``port=0`` is the rollup sentinel — the chip-level any-port-up
    result. Non-zero ``port`` carries per-port detail rows that
    multi-port chips emit alongside their rollup. Single-port chips
    only write the rollup row.

    Schema PK is ``(ts, host_id, service_idx, port)`` so rollup and
    per-port rows at the same tick coexist without conflict.
    """
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO service_samples "
                "(ts, host_id, service_idx, port, alive, rtt_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, host_id, service_idx, int(port or 0),
                 1 if alive else 0,
                 rtt_ms, (error or "")[:200] or None),
            )
    except (sqlite3.Error, OSError) as e:
        print(f"[service_sampler] {host_id!r}/{service_idx}/port={port} DB insert skipped: {e}")


def _prune_old_samples() -> int:
    days = tuning.tuning_int(_Tunable.STATS_HISTORY_DAYS)
    cutoff = int(time.time() - days * 86400)
    try:
        with db_conn() as c:
            cur = c.execute("DELETE FROM service_samples WHERE ts < ?", (cutoff,))
            return cur.rowcount or 0
    except (sqlite3.Error, OSError) as e:
        print(f"[service_sampler] prune skipped: {e}")
        return 0


async def service_sampler_loop() -> None:
    """Lifespan-managed sampler. Dormant when the master toggle is off
    or no service is opted-in. Re-evaluates both conditions each tick
    so flipping settings at runtime takes effect without restart.
    """
    # Two distinct interval reads:
    # (1) Startup read below — one-time, sets the initial first-tick
    #     delay (`min(45, interval)`) so a fresh container doesn't fire
    #     a sampler tick at exactly t=0 before DB migrations land.
    # (2) Per-tick read at the bottom of the loop body — runs ONCE per
    #     tick (NOT twice; line (1) above is startup-only) so an
    #     Admin → Config edit takes effect on the next tick without
    #     restart. This is the "per-tick" cost the operator might
    #     worry about; clarifying the distinction here so the
    #     reader doesn't conclude the loop reads it twice per tick.
    interval = _resolve_service_probe_interval()
    # First-tick delay — let DB migrations land + give the rest of
    # the lifespan a chance to come up.
    await asyncio.sleep(min(45, interval))
    tick = 0
    while True:
        try:
            master_enabled = get_setting_bool(Settings.SERVICE_PROBE_ENABLED)
            if not master_enabled:
                pass  # globally disabled — stay alive for runtime toggle
            else:
                targets = _curated_service_probe_targets()
                if targets:
                    sem = asyncio.Semaphore(
                        tuning.tuning_int(_Tunable.SERVICE_PROBE_CONCURRENCY)
                    )
                    timeout_s = float(tuning.tuning_int(_Tunable.SERVICE_PROBE_TIMEOUT_SECONDS))
                    pause_threshold = tuning.tuning_int(_Tunable.SERVICE_PROBE_FAILURE_PAUSE_ROUNDS)
                    ts = int(time.time())
                    # Pre-tick snapshot of currently-failing (host, idx)
                    # pairs so the notification only fires on the
                    # healthy → failing transition.
                    previously_failing: set[tuple[str, int]] = set()
                    try:
                        with db_conn() as c:
                            # Most-recent ROLLUP row (port=0) per
                            # (host_id, service_idx). Filtering to the
                            # rollup ensures the "healthy -> failing"
                            # transition gate fires once per chip, not
                            # once per port — per-port rows are detail,
                            # not a separate failure signal.
                            rows = c.execute(
                                "SELECT host_id, service_idx, alive FROM service_samples s1 "
                                "WHERE port = 0 "
                                "AND ts = (SELECT MAX(ts) FROM service_samples s2 "
                                "WHERE s2.host_id = s1.host_id "
                                "AND s2.service_idx = s1.service_idx "
                                "AND s2.port = 0)"
                            ).fetchall()
                            for r in rows:
                                if not r[2]:
                                    previously_failing.add((r[0], int(r[1])))
                    except (sqlite3.Error, OSError):
                        pass

                    async def _probe_target(tgt: dict) -> tuple[dict, dict]:
                        async with sem:
                            sub_ports = tgt.get("sub_ports") or []
                            if sub_ports:
                                # Multi-port chip — probe each port and
                                # roll up. "any alive = alive" matches
                                # the operator mental model (Portainer
                                # responds on EITHER 9000 or 9443; a
                                # chip with both should look up when
                                # one is reachable). The per-port detail
                                # is stamped onto the rollup result via
                                # `sub_port_results` so the SPA can render
                                # a per-port mini-table in the App Drawer.
                                # Inner names deliberately distinct from
                                # the outer-scope `port_results` /
                                # `any_alive` rollup vars below to keep
                                # static analysis honest about scope.
                                sub_port_results: list[dict] = []
                                chip_any_alive = False
                                min_rtt: Optional[int] = None
                                first_error = None
                                for sp in sub_ports:
                                    if sp.get("probe_type") == "http":
                                        # Build URL for this sub-port:
                                        # scheme follows protocol, host
                                        # from parent target, path from
                                        # sub-port.
                                        scheme = "https" if sp.get("protocol") == "https" else "http"
                                        sub_url = f"{scheme}://{tgt['host']}:{sp['port']}{sp.get('probe_path') or '/'}"
                                        port_outcome = await _probe_http(
                                            sub_url,
                                            sp.get("probe_status") or 0,
                                            timeout_s,
                                        )
                                    else:
                                        port_outcome = await _probe_tcp(
                                            tgt["host"],
                                            int(sp["port"]),
                                            timeout_s,
                                        )
                                    sub_port_results.append({
                                        "port": sp["port"],
                                        "label": sp.get("label") or "",
                                        "alive": bool(port_outcome.get("alive")),
                                        "rtt_ms": port_outcome.get("rtt_ms"),
                                        "error": port_outcome.get("error"),
                                    })
                                    if port_outcome.get("alive"):
                                        chip_any_alive = True
                                        # `_int_or_none` narrows the
                                        # Any-typed rtt_ms cell before
                                        # the `<` comparison so static
                                        # analysis sees a concrete int.
                                        rtt_int = _int_or_none(port_outcome.get("rtt_ms"))
                                        if rtt_int is not None and (min_rtt is None or rtt_int < min_rtt):
                                            min_rtt = rtt_int
                                    elif first_error is None:
                                        first_error = port_outcome.get("error")
                                rollup = {
                                    "alive": chip_any_alive,
                                    "rtt_ms": min_rtt,
                                    "error": None if chip_any_alive else (first_error or "all ports down"),
                                    "port_results": sub_port_results,
                                }
                                return tgt, rollup
                            if tgt["probe_type"] == "http" and tgt.get("url"):
                                probe_outcome = await _probe_http(
                                    tgt["url"],
                                    tgt["expected_status"],
                                    timeout_s,
                                )
                            else:
                                probe_outcome = await _probe_tcp(
                                    tgt["host"],
                                    int(tgt["port"] or 0),
                                    timeout_s,
                                )
                            return tgt, probe_outcome

                    n_ok = 0
                    n_err = 0
                    new_failures: list[tuple[dict, dict]] = []
                    # Per-host roll-up for the host-level pause counter.
                    per_host_results: dict[str, list[bool]] = {}
                    outcomes = await asyncio.gather(
                        *(_probe_target(t) for t in targets),
                        return_exceptions=True,
                    )
                    for outcome in outcomes:
                        if isinstance(outcome, BaseException):
                            n_err += 1
                            print(f"[service_sampler] unexpected probe exception: {outcome}")
                            continue
                        target, result = outcome
                        host_id = target["host_id"]
                        svc_idx = target["service_idx"]
                        alive = bool(result.get("alive"))
                        # Rollup row — chip-level status. Always emitted
                        # (single-port chips have ONLY this row). The
                        # explicit `port=0` matches the persistence
                        # contract: 0 is the rollup sentinel even
                        # though it equals the parameter's default —
                        # spelling it out documents the intent at
                        # every call site.
                        # noinspection PyArgumentEqualDefault
                        _persist_row(
                            host_id, svc_idx, alive,
                            _int_or_none(result.get("rtt_ms")),
                            result.get("error"),
                            ts,
                            port=0,
                        )
                        # Per-port rows — only when the probe was multi-port
                        # (sub-port detail stamped onto `result.port_results`
                        # by _probe_target's multi-port branch). One row per
                        # port carrying the port's own alive / rtt / error.
                        port_results = result.get("port_results")
                        if isinstance(port_results, list):
                            for pr in port_results:
                                if not isinstance(pr, dict):
                                    continue
                                # `_int_or_none` narrows the Any-typed
                                # `port`/`rtt_ms` cells before passing
                                # them into the persistence layer's
                                # `int(...)` cast so type-checkers don't
                                # flag the Any|None → int conversion.
                                port_int = _int_or_none(pr.get("port"))
                                if not port_int or port_int <= 0:
                                    continue
                                _persist_row(
                                    host_id, svc_idx,
                                    bool(pr.get("alive")),
                                    _int_or_none(pr.get("rtt_ms")),
                                    pr.get("error"),
                                    ts,
                                    port=port_int,
                                )
                        per_host_results.setdefault(host_id, []).append(alive)
                        if alive:
                            n_ok += 1
                        else:
                            n_err += 1
                            key = (host_id, svc_idx)
                            if key not in previously_failing:
                                new_failures.append((target, result))
                    # Roll up per-host outcomes for the auto-pause +
                    # notification gates. ANY service alive on a host
                    # keeps the host out of the auto-pause counter
                    # (operator's mental model: one broken chip doesn't
                    # fault the whole host).
                    try:
                        from logic.host_metrics_sampler import (
                            record_provider_outcome as _rec_outcome,
                        )
                        for hid, alive_list in per_host_results.items():
                            any_alive = any(alive_list)
                            if any_alive:
                                await _rec_outcome(hid, "service_probe", True)
                            else:
                                await _rec_outcome(
                                    hid, "service_probe", False,
                                    error="all service probes failed",
                                    round_threshold=pause_threshold,
                                )
                    except Exception as rec_err:  # noqa: BLE001
                        print(f"[service_sampler] record_provider_outcome failed: {rec_err}")
                    # Fire notifications for new healthy→failing
                    # transitions only (one per outage, not tick-rate).
                    for target, result in new_failures:
                        try:
                            from logic.ops import notify as _notify
                            await _notify(
                                f"Service probe failed: {target['host_id']}",
                                target.get("service_name", "") or "",
                                "error",
                                event="service_probe_failure",
                                target_kind="host",
                                target_id=target["host_id"],
                                metadata={
                                    "url": target.get("url") or "",
                                    "host": target["host_id"],
                                    "service_name": target.get("service_name") or "",
                                },
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as notify_err:  # noqa: BLE001
                            print(
                                f"[service_sampler] {target.get('host_id')!r} "
                                f"service_probe_failure notify deferred: {notify_err}"
                            )
                    print(
                        f"[service_sampler] tick: {len(targets)} services / "
                        f"{n_ok} alive / {n_err} failing / "
                        f"{len(new_failures)} new failures"
                    )
            interval = _resolve_service_probe_interval()
            days = tuning.tuning_int(_Tunable.STATS_HISTORY_DAYS)
            if tick % max(1, 3600 // interval) == 0:
                n = _prune_old_samples()
                if n:
                    print(f"[service_sampler] pruned {n} rows older than {days}d")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[service_sampler] tick error: {e}")
        tick += 1
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def latest_for_host(host_id: str) -> dict:
    """Latest per-service ROLLUP probe outcome for one host — keyed
    by `service_idx`. Filters to ``port=0`` rows so multi-port chips
    surface their chip-level rollup, not whichever port happened to
    sort latest. Per-port detail is exposed via
    :func:`latest_per_port_for_host`.

    Returns ``{service_idx: {alive, rtt_ms, ts, error}, ...}``. Empty
    dict when no samples found.
    """
    if not host_id:
        return {}
    try:
        with db_conn() as c:
            # Most-recent ROLLUP row (port=0) per service_idx for this host.
            rows = c.execute(
                "SELECT service_idx, ts, alive, rtt_ms, error "
                "FROM service_samples s1 "
                "WHERE host_id = ? AND port = 0 "
                "AND ts = (SELECT MAX(ts) FROM service_samples s2 "
                "          WHERE s2.host_id = s1.host_id "
                "          AND s2.service_idx = s1.service_idx "
                "          AND s2.port = 0)",
                (host_id,),
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[service_sampler] latest_for_host({host_id!r}) skipped: {e}")
        return {}
    out: dict = {}
    for r in rows:
        idx = int(r[0])
        out[idx] = {
            "alive": bool(r[2]),
            "rtt_ms": r[3],
            "ts": int(r[1]),
            "error": r[4],
        }
    return out


def latest_per_port_for_host(host_id: str, service_idx: int) -> list[dict]:
    """Latest per-PORT probe outcomes for one chip on one host.

    Returns a list of ``{port, alive, rtt_ms, ts, error}`` rows — one
    per distinct port that's been probed for this chip. Ordered by
    port ASC. Rollup row (port=0) is EXCLUDED. Empty list when the
    chip has no multi-port history yet.

    Consumed by the host drawer's per-chip detail view and the
    Apps view's expanded card. Cheap lookup — the
    ``idx_service_samples_host_idx_ts`` index covers the path.
    """
    if not host_id or service_idx is None:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT port, ts, alive, rtt_ms, error "
                "FROM service_samples s1 "
                "WHERE host_id = ? AND service_idx = ? AND port > 0 "
                "AND ts = (SELECT MAX(ts) FROM service_samples s2 "
                "          WHERE s2.host_id = s1.host_id "
                "          AND s2.service_idx = s1.service_idx "
                "          AND s2.port = s1.port) "
                "ORDER BY port ASC",
                (host_id, int(service_idx)),
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[service_sampler] latest_per_port_for_host({host_id!r}/{service_idx}) skipped: {e}")
        return []
    return [
        {
            "port": int(r[0]),
            "ts": int(r[1]),
            "alive": bool(r[2]),
            "rtt_ms": r[3],
            "error": r[4],
        }
        for r in rows
    ]


def latest_per_port_all_for_host(host_id: str) -> dict:
    """Latest per-PORT probe outcomes for EVERY chip on one host, in a
    SINGLE query — keyed by ``service_idx``.

    Returns ``{service_idx: [{port, alive, rtt_ms, ts, error}, ...], ...}``
    (port>0 rows only, rollup port=0 excluded, ports ASC within each
    chip). Batched companion to :func:`latest_per_port_for_host` so a
    cross-host aggregator like ``service_catalog.list_apps`` pays ONE
    query per host instead of one per chip — same query-count profile
    as :func:`latest_for_host`. Empty dict when the host has no
    multi-port sample history.
    """
    if not host_id:
        return {}
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT service_idx, port, ts, alive, rtt_ms, error "
                "FROM service_samples s1 "
                "WHERE host_id = ? AND port > 0 "
                "AND ts = (SELECT MAX(ts) FROM service_samples s2 "
                "          WHERE s2.host_id = s1.host_id "
                "          AND s2.service_idx = s1.service_idx "
                "          AND s2.port = s1.port) "
                "ORDER BY service_idx ASC, port ASC",
                (host_id,),
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[service_sampler] latest_per_port_all_for_host({host_id!r}) skipped: {e}")
        return {}
    out: dict = {}
    for r in rows:
        idx = int(r[0])
        out.setdefault(idx, []).append({
            "port": int(r[1]),
            "ts": int(r[2]),
            "alive": bool(r[3]),
            "rtt_ms": r[4],
            "error": r[5],
        })
    return out


def populate_host_service_merge(host_id: str, merged: dict) -> None:
    """Stamp per-service ``last_probe`` fields onto each
    ``merged["services"][i]`` entry.

    Shared helper called from BOTH ``/api/hosts/list`` (skeleton) AND
    ``_merge_one_host`` (per-host detail) so both endpoints surface
    the same on-disk state without duplicate SELECTs.

    No-op when `merged["services"]` is missing or empty. Stamps
    nothing onto services with no recorded probe sample yet —
    `services[i].last_probe` stays absent so the frontend can
    distinguish "never probed" from "probed and down".
    """
    if not host_id:
        return
    services = merged.get("services")
    if not isinstance(services, list) or not services:
        return
    latest = latest_for_host(host_id)
    if not latest:
        return
    for idx, svc in enumerate(services):
        if not isinstance(svc, dict):
            continue
        sample = latest.get(idx)
        if not sample:
            continue
        svc["last_probe"] = sample


# Public aliases — main.py's manual probe-now endpoint reuses these
# helpers to share probe semantics with the lifespan sampler. The
# underscore-prefixed originals remain for in-module callers; the
# aliases are the documented entry points for cross-module callers
# so static analysis doesn't flag protected-member access.
probe_http = _probe_http
probe_tcp = _probe_tcp
persist_row = _persist_row
