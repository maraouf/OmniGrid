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
# Coercion helpers — shared logic.coerce leaf module, aliased to the
# legacy underscore names so call sites are unchanged.
from logic.coerce import (
    safe_int as _safe_int,
    int_or_none as _int_or_none,
)


def _resolve_service_probe_interval() -> int:
    """Sampler tick cadence — thin wrapper for binary-compat. The
    canonical implementation lives at `tuning.resolve_provider_interval`
    (shared across http_probe / service_probe samplers per the project conventions
    priority L duplicate-code rule).
    """
    return tuning.resolve_provider_interval(_Tunable.SERVICE_PROBE_SAMPLE_INTERVAL_SECONDS)


def resolve_chip_probe_target(host_row: dict, service_idx: int, svc: dict,
                              require_enabled: bool = True) -> Optional[dict]:
    """Resolve ONE service chip into a probe target — or ``None`` when it
    can't be probed.

    ``require_enabled`` (default True) gates on the chip's ``probe.enabled``
    flag — the master switch for the *continuous* lifespan sampler. The
    manual probe-now path passes ``require_enabled=False`` so an explicit
    operator click probes any chip that has a resolvable target (configured
    ``probe.ports[]`` / ``probe.port`` / URL + host ``address``) even when
    continuous sampling is off — the click IS the opt-in for that one run.

    Single source of truth for "given a curated host row + one services[]
    chip, what do we probe?" — shared by ``_curated_service_probe_targets``
    (the lifespan sampler's fan-out), the manual probe-now endpoint, and
    the Apps debug endpoint, so all three resolve IDENTICALLY:

      * probe host: the chip's own ``url`` wins; when it carries none
        (e.g. a catalog-pinned chip that only has ``probe.ports[]``),
        fall back to the host's curated ``address`` — the canonical
        per-host probe target. Before this fallback, a no-URL chip was
        silently dropped, never probed, and surfaced as an "unknown"
        instance (which rolls up to a "degraded" app group).
      * port: operator ``probe.port`` overrides the URL-derived port;
        single-port TCP chips with no port fall back to the scheme
        default (80 / 443). Multi-port chips (``probe.ports[]``) carry
        their ports as sub-targets and don't need a top-level port.

    Returns the target dict shape documented below, or ``None`` when the
    chip is probe-disabled / has no resolvable host / has no resolvable
    port:

        {
            "host_id": str, "service_idx": int, "service_name": str,
            "probe_type": "tcp" | "http", "url": str, "host": str,
            "port": int | None, "path": str, "expected_status": int,
            "sub_ports": [ {port, protocol, label, probe_path,
                            probe_status, probe_type}, ... ],
        }
    """
    if not isinstance(svc, dict):
        return None
    probe_cfg = svc.get("probe")
    if not isinstance(probe_cfg, dict):
        return None
    if require_enabled and not probe_cfg.get("enabled"):
        return None
    probe_type = (probe_cfg.get("type") or "tcp").strip().lower()
    if probe_type not in ("tcp", "http"):
        probe_type = "tcp"
    url = (svc.get("url") or "").strip()
    # Parse URL to extract host / port / path. Operator-set `probe.port`
    # overrides URL-derived port; same for path.
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
    if not parsed_host:
        parsed_host = (host_row.get("address") or "").strip()
    if not parsed_host:
        return None
    ports_raw = probe_cfg.get("ports")
    has_sub_ports = isinstance(ports_raw, list) and any(
        isinstance(p, dict) and _int_or_none(p.get("port")) for p in ports_raw
    )
    if probe_type == "tcp" and not port and not has_sub_ports:
        # Fall back to default web ports based on URL scheme.
        lc = url.lower()
        if lc.startswith("https://"):
            port = 443
        elif lc.startswith("http://"):
            port = 80
        else:
            return None
    expected_status = _safe_int(probe_cfg.get("expected_status"))
    svc_name = (svc.get("name") or svc.get("label") or url or f"service-{service_idx}").strip()
    # Multi-port shape (Apps feature). When `probe.ports[]` is populated,
    # each port becomes a sub-target probed independently; the chip's
    # final status is rolled up ("any port up = chip alive, all down =
    # chip dead") at row-persist time so historical schema stays at one
    # row per (host_id, service_idx).
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
            # Per-port probe type: udp ⇒ a real UDP probe (NOT a TCP
            # connect — TCP-connecting a UDP-only port like OpenVPN 1194
            # always fails, which is why udp chips read perpetually down);
            # http when protocol is http/https OR a probe_path was supplied
            # (HTTP-level check); TCP otherwise.
            if proto == "udp":
                sub_type = "udp"
            elif proto in ("http", "https") or sub_path != "/":
                sub_type = "http"
            else:
                sub_type = "tcp"
            sub_ports.append({
                "port": pi,
                "protocol": proto,
                "label": sub_label,
                "probe_path": sub_path,
                "probe_status": sub_status,
                "probe_type": sub_type,
            })
    return {
        "host_id": (host_row.get("id") or "").strip(),
        "service_idx": service_idx,
        "service_name": svc_name,
        "probe_type": probe_type,
        "url": url,
        "host": parsed_host,
        "port": port,
        "path": path,
        "expected_status": expected_status,
        "sub_ports": sub_ports,
    }


def _curated_service_probe_targets() -> list[dict]:
    """Walk every curated host's services[] for probe-enabled entries,
    returning one target per opted-in chip.

    Thin fan-out over :func:`resolve_chip_probe_target` (the shared
    chip → target resolver) — targets that can't be probed (disabled,
    no resolvable host, no resolvable port) are dropped.
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
            tgt = resolve_chip_probe_target(row, idx, svc)
            if tgt is not None:
                out.append(tgt)
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
            # Try HEAD first; fall back to GET whenever the service
            # doesn't honour HEAD. Many endpoints reply 400 / 403 / 405 /
            # 501 to a HEAD instead of the spec-correct 405 — NetData's
            # /api/v1/info returns 400 — so retry with GET whenever the
            # HEAD status isn't the success/expected one. A HEAD-hostile
            # endpoint that serves GET fine must not read as down.
            r = await client.head(url)
            head_ok = (r.status_code == expected_status) if expected_status else (200 <= r.status_code < 500)
            if not head_ok:
                r = await client.get(url)
        rtt_ms = int((time.monotonic() - started) * 1000)
        if expected_status:
            ok = r.status_code == expected_status
        else:
            # `expected_status == 0` means "accept any response" — a probe
            # is a REACHABILITY check, so ANY HTTP answer (2xx, a 3xx
            # redirect to a login, OR a 4xx like 401/403/404 from an
            # auth-gated UI / API — e.g. Proxmox 8006, UniFi 8443) proves
            # the service is up. Only a 5xx (server error / dead backend
            # behind a live proxy) counts as down. Operators wanting a
            # stricter health check set an explicit expected_status.
            ok = 200 <= r.status_code < 500
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
    # Operator-visible startup line — surfaces the EFFECTIVE
    # interval the sampler will sleep for. Operators who set the
    # per-provider tunable to a non-default value want to confirm
    # the sampler actually picked it up; pre-fix the only place
    # the effective value showed was the host-drawer chip subtitle
    # via `_provider_sample_intervals`, which is a separate
    # resolver call that could mask drift from the actual sampler
    # cadence. This line proves the sampler's own loop sees the
    # value the operator saved. The raw tunable + the resolved
    # value are both included so an "inherit" (raw=0 → resolved =
    # global) is visually distinct from an explicit override.
    try:
        _raw = int(tuning.tuning_int(_Tunable.SERVICE_PROBE_SAMPLE_INTERVAL_SECONDS) or 0)
    except (ValueError, TypeError, KeyError):
        _raw = 0
    print(
        f"[service_sampler] effective interval={interval}s "
        f"(tunable raw={_raw}s — "
        f"{'inherited from STATS_SAMPLE_INTERVAL_SECONDS' if _raw <= 0 else 'explicit override'})"
    )
    # First-tick delay — let DB migrations land + give the rest of
    # the lifespan a chance to come up.
    await asyncio.sleep(min(45, interval))
    tick = 0
    from logic.sampler_metrics import record_tick as _record_tick
    while True:
        _tick_t0 = time.perf_counter()
        _tick_ok = True
        _tick_err = ""
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
                            #
                            # ROW_NUMBER() window — O(N) fleet-wide
                            # with seek through the (host_id, ts DESC)
                            # composite index. Pre-fix used a
                            # CORRELATED SUBQUERY (`AND ts = (SELECT
                            # MAX(ts) FROM s2 WHERE host_id + idx +
                            # port match)`) that ran per outer row →
                            # O(N²) full-table scan. On a busy fleet
                            # with tens of thousands of service_samples
                            # rows this single sampler-tick prologue
                            # took seconds, blocking the sampler from
                            # ticking on schedule + bottlenecking the
                            # SQLite writer pool against /api/apps
                            # readers. Same fix shape as the per-host
                            # `latest_for_host` / `latest_per_port_all`
                            # rewrites below.
                            rows = c.execute(
                                "SELECT host_id, service_idx, alive FROM ("
                                "  SELECT host_id, service_idx, alive, "
                                "         ROW_NUMBER() OVER ("
                                "             PARTITION BY host_id, service_idx ORDER BY ts DESC"
                                "         ) AS rn "
                                "  FROM service_samples "
                                "  WHERE port = 0"
                                ") WHERE rn = 1"
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
                                    elif sp.get("probe_type") == "udp":
                                        # Real UDP probe (protocol-correct).
                                        # A reply = up; silence (open|filtered)
                                        # reads down because we can't confirm —
                                        # a UDP VPN tunnel (OpenVPN/WireGuard)
                                        # never answers an unsolicited probe, so
                                        # disable the chip's probe to make such
                                        # a service inventory-only rather than a
                                        # perpetual red.
                                        from logic.port_scanner_udp import _probe_one_udp as _udp_probe
                                        _udp_out = await _udp_probe(
                                            tgt["host"], int(sp["port"]), timeout_s,
                                        )
                                        port_outcome = {
                                            "alive": bool(_udp_out.get("open")),
                                            "rtt_ms": None,
                                            "error": None if _udp_out.get("open")
                                            else "udp: no response (open|filtered — can't confirm)",
                                        }
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
                # Offload prune to worker thread — same pattern as
                # host_metrics_sampler. Keeps the event loop hot for
                # /api/* requests during the hourly DELETE.
                from logic.sampler_metrics import prune_with_metrics
                n = await prune_with_metrics("service_sampler", _prune_old_samples)
                if n:
                    print(f"[service_sampler] pruned {n} rows older than {days}d")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _tick_ok = False
            _tick_err = type(e).__name__
            print(f"[service_sampler] tick error: {e}")
        finally:
            _record_tick(
                "service_sampler",
                (time.perf_counter() - _tick_t0) * 1000.0,
                ok=_tick_ok,
                error=_tick_err,
            )
        tick += 1
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def bulk_latest_for_hosts(host_ids: list) -> dict:
    """bulk version of :func:`latest_for_host` for many hosts.

    Returns ``{host_id: {service_idx: {alive, rtt_ms, ts, error}}}``. Hosts
    with no rollup rows are absent. ONE SQL query independent of fleet size.

    Used by ``/api/hosts/list``'s bulk loop to replace the per-host correlated
    subquery (one DB round-trip per host) with a single aggregated read.
    """
    ids = [hid for hid in (host_ids or []) if hid]
    if not ids:
        return {}
    try:
        with db_conn() as c:
            ph = ",".join(["?"] * len(ids))
            rows = c.execute(
                f"SELECT s.host_id, s.service_idx, s.ts, s.alive, s.rtt_ms, s.error "
                f"FROM service_samples s "
                f"INNER JOIN (SELECT host_id, service_idx, MAX(ts) AS mts "
                f"            FROM service_samples "
                f"            WHERE host_id IN ({ph}) AND port = 0 "
                f"            GROUP BY host_id, service_idx) m "
                f"  ON s.host_id = m.host_id "
                f" AND s.service_idx = m.service_idx "
                f" AND s.ts = m.mts "
                f"WHERE s.port = 0",
                ids,
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[service_sampler] bulk_latest_for_hosts skipped: {e}")
        return {}
    out: dict = {}
    for r in rows:
        hid = r[0]
        idx = int(r[1])
        per_host = out.setdefault(hid, {})
        per_host[idx] = {
            "alive": bool(r[3]),
            "rtt_ms": r[4],
            "ts": int(r[2]),
            "error": r[5],
        }
    return out


def bulk_latest_per_port_for_hosts(host_ids: list) -> dict:
    """bulk version of :func:`latest_per_port_for_host` for many hosts.

    Returns ``{(host_id, service_idx): [{port, alive, rtt_ms, ts, error}, ...]}``
    — one entry per (host, chip) that has multi-port history; absent for
    chips without per-port samples. ONE SQL query for the whole fleet.
    """
    ids = [hid for hid in (host_ids or []) if hid]
    if not ids:
        return {}
    try:
        with db_conn() as c:
            ph = ",".join(["?"] * len(ids))
            rows = c.execute(
                f"SELECT s.host_id, s.service_idx, s.port, s.ts, s.alive, s.rtt_ms, s.error "
                f"FROM service_samples s "
                f"INNER JOIN (SELECT host_id, service_idx, port, MAX(ts) AS mts "
                f"            FROM service_samples "
                f"            WHERE host_id IN ({ph}) AND port > 0 "
                f"            GROUP BY host_id, service_idx, port) m "
                f"  ON s.host_id = m.host_id "
                f" AND s.service_idx = m.service_idx "
                f" AND s.port = m.port "
                f" AND s.ts = m.mts "
                f"WHERE s.port > 0 "
                f"ORDER BY s.host_id, s.service_idx, s.port ASC",
                ids,
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[service_sampler] bulk_latest_per_port_for_hosts skipped: {e}")
        return {}
    out: dict = {}
    for r in rows:
        key = (r[0], int(r[1]))
        out.setdefault(key, []).append({
            "port": int(r[2]),
            "ts": int(r[3]),
            "alive": bool(r[4]),
            "rtt_ms": r[5],
            "error": r[6],
        })
    return out


def latest_for_host(host_id: str) -> dict:
    """Latest per-service ROLLUP probe outcome for one host — keyed
    by `service_idx`. Filters to ``port=0`` rows so multi-port chips
    surface their chip-level rollup, not whichever port happened to
    sort latest. Per-port detail is exposed via
    :func:`latest_per_port_for_host`.

    Returns ``{service_idx: {alive, rtt_ms, ts, error}, ...}``. Empty
    dict when no samples found.

    **Query shape:** ROW_NUMBER() window partitioned by
    ``service_idx`` ordered by ``ts DESC``, then filtered to rn=1.
    O(N) per host with index seek via
    ``idx_service_samples_host_idx_ts``. Pre-fix this used a
    CORRELATED SUBQUERY (`AND ts = (SELECT MAX(ts) FROM
    service_samples s2 WHERE s2.host_id = s1.host_id AND
    s2.service_idx = s1.service_idx AND s2.port = 0)`) which made
    the SQLite query planner run the inner SELECT MAX(ts) for
    EACH outer row → O(N²) per host. On a busy fleet (200 hosts
    × thousands of samples each) the cumulative wall-clock made
    `/api/apps` 504 even WITH the `asyncio.to_thread` offload
    (the worker thread still serialised on SQLite). User-flagged
    crash class — "apps page keeps loading without any end" /
    "Failed to load apps: HTTP 504". Same anti-pattern fixed
    in `latest_per_port_all_for_host` below.
    """
    if not host_id:
        return {}
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT service_idx, ts, alive, rtt_ms, error FROM ("
                "  SELECT service_idx, ts, alive, rtt_ms, error, "
                "         ROW_NUMBER() OVER ("
                "             PARTITION BY service_idx ORDER BY ts DESC"
                "         ) AS rn "
                "  FROM service_samples "
                "  WHERE host_id = ? AND port = 0"
                ") WHERE rn = 1",
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
    # ROW_NUMBER() window — O(N) per chip with seek through
    # idx_service_samples_host_idx_ts. Same fix as the batched
    # `latest_per_port_all_for_host` + `latest_for_host` above —
    # pre-fix all three used a CORRELATED SUBQUERY that made the
    # inner MAX(ts) re-run per outer row → O(N²) per chip → on a
    # multi-port service with thousands of samples, each drawer
    # open took seconds. The drawer-open path is less hot than
    # /api/apps but still operator-visible; fix the same way for
    # consistency.
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT port, ts, alive, rtt_ms, error FROM ("
                "  SELECT port, ts, alive, rtt_ms, error, "
                "         ROW_NUMBER() OVER ("
                "             PARTITION BY port ORDER BY ts DESC"
                "         ) AS rn "
                "  FROM service_samples "
                "  WHERE host_id = ? AND service_idx = ? AND port > 0"
                ") WHERE rn = 1 "
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

    **Query shape:** ROW_NUMBER() window partitioned by
    ``(service_idx, port)`` ordered by ``ts DESC``, filtered to
    rn=1. O(N) per host with index seek. Pre-fix used the same
    correlated-subquery anti-pattern as `latest_for_host` (now
    fixed above) — the inner SELECT MAX(ts) ran per outer row →
    O(N²) per host → on a busy fleet `/api/apps` 504'd because
    the worker thread serialised on SQLite even with the
    `asyncio.to_thread` offload. See `latest_for_host` docstring
    for the full crash-class explanation.
    """
    if not host_id:
        return {}
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT service_idx, port, ts, alive, rtt_ms, error FROM ("
                "  SELECT service_idx, port, ts, alive, rtt_ms, error, "
                "         ROW_NUMBER() OVER ("
                "             PARTITION BY service_idx, port ORDER BY ts DESC"
                "         ) AS rn "
                "  FROM service_samples "
                "  WHERE host_id = ? AND port > 0"
                ") WHERE rn = 1 "
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


# Presentational: how many of the MOST-RECENT rollup samples a per-chip
# Apps-card uptime sparkline draws. A point-count for a fixed-width spark
# (same class as the CSS-only chart-coordinate constants), NOT an operator
# behaviour knob — so it stays a code constant rather than a TUNABLE.
_APPS_SPARK_MAX_POINTS = 24


def history_rollup_all_for_host(host_id: str,
                                max_points: int = _APPS_SPARK_MAX_POINTS) -> dict:
    """Recent per-chip ROLLUP uptime history for EVERY chip on one host,
    in a SINGLE query — keyed by ``service_idx``.

    Returns ``{service_idx: [{ts, up}, ...], ...}`` where each list holds
    up to ``max_points`` of the MOST-RECENT rollup samples (``port=0``)
    ordered OLDEST->NEWEST, so the Apps card can draw a tiny per-instance
    uptime sparkline without an extra DB hit per chip — one query per host,
    same profile as :func:`latest_for_host` (NOT per-tile fan-out). ``up``
    is the boolean alive flag. Empty dict when the host has no rollup
    history.

    Uses a ``ROW_NUMBER()`` window partitioned by ``service_idx`` so the
    per-chip cap is applied IN SQL (the ``idx_service_samples_host_idx_ts``
    index covers the partition) rather than fetching the whole retention
    window and slicing in Python.
    """
    if not host_id:
        return {}
    try:
        n = max(1, int(max_points))
    except (TypeError, ValueError):
        n = _APPS_SPARK_MAX_POINTS
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT service_idx, ts, alive FROM ("
                "  SELECT service_idx, ts, alive, "
                "         ROW_NUMBER() OVER ("
                "             PARTITION BY service_idx ORDER BY ts DESC"
                "         ) AS rn "
                "  FROM service_samples "
                "  WHERE host_id = ? AND port = 0"
                ") WHERE rn <= ? "
                "ORDER BY service_idx ASC, ts ASC",
                (host_id, n),
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[service_sampler] history_rollup_all_for_host({host_id!r}) skipped: {e}")
        return {}
    out: dict = {}
    for r in rows:
        out.setdefault(int(r[0]), []).append({
            "ts": int(r[1]),
            "up": bool(r[2]),
        })
    return out


# ---------------------------------------------------------------------------
# Multi-host batched companions — fleet-wide aggregators for /api/apps
# ---------------------------------------------------------------------------
#
# Pre-batch shape: a cross-host aggregator like `service_catalog.list_apps`
# walked N curated hosts and called the per-host helpers above for EACH
# host: 3 queries × N hosts = 3N DB round-trips per `/api/apps` request.
# On a 200-host fleet that's 600 round-trips (~5ms each in WAL mode =
# ~3s wall-clock JUST in DB dispatch, before any Python iteration).
#
# Batched shape: ONE query each fleet-wide. The aggregator passes the
# curated host_id list once + slices the result dict by host_id. Result
# count is the same (one bucket per (host, service_idx) pair); the DB
# round-trip count drops from 3N → 3.
#
# All three helpers reuse the ROW_NUMBER() window from their single-host
# siblings — the only schema change is adding `host_id` to the
# PARTITION BY clause so the window splits per-(host, service_idx[, port])
# instead of per-(service_idx[, port]). The existing
# `idx_service_samples_host_idx_ts` composite index covers the partition
# scan, so the multi-host query stays seek-bound.


def _hostid_in_clause(host_ids: list[str]) -> tuple[str, list[str]]:
    """Build a `host_id IN (?, ?, ...)` clause + param list for an
    arbitrary host-id list. Empty list yields a clause that matches
    nothing (`host_id IN ('__none__')` rather than the syntax-error
    bare `IN ()`). Caller passes the param list into `execute(sql, params)`
    directly. SQLite's limit on `?` placeholders is 999 by default
    (compile-time `SQLITE_MAX_VARIABLE_NUMBER`); fleets approaching
    that should chunk the lookup themselves — current OmniGrid fleets
    cap at ~250 hosts so a single batched query stays comfortable.
    """
    if not host_ids:
        return "host_id IN ('__none__')", []
    placeholders = ",".join("?" * len(host_ids))
    return f"host_id IN ({placeholders})", list(host_ids)


def latest_for_hosts(host_ids: list[str]) -> dict:
    """Multi-host batched companion to :func:`latest_for_host`. Returns
    ``{host_id: {service_idx: {alive, rtt_ms, ts, error}, ...}, ...}``
    — same nested per-chip shape, one extra outer-key level by host.

    ONE query for the WHOLE fleet instead of N per-host queries. The
    `idx_service_samples_host_idx_ts` composite covers the partition
    scan; the ROW_NUMBER() window picks the newest ROLLUP row (port=0)
    per (host_id, service_idx). Empty dict when no host has any sample
    OR the input list is empty. Hosts with no rollup history are
    OMITTED from the result (callers should treat missing keys as
    "no samples yet").
    """
    if not host_ids:
        return {}
    in_clause, params = _hostid_in_clause(host_ids)
    try:
        with db_conn() as c:
            # Latest row per (host_id, service_idx) — same GROUP BY + MAX(ts)
            # index-seek pattern as latest_per_port_all_for_hosts (uses
            # idx_service_samples_host_idx_ts; no window-function sort). Bare
            # columns take the MAX(ts) row's values; trailing MAX(ts) read is
            # discarded (columns 0-5 consumed below).
            rows = c.execute(
                "SELECT host_id, service_idx, ts, alive, rtt_ms, error, MAX(ts) "
                f"FROM service_samples WHERE {in_clause} AND port = 0 "
                "GROUP BY host_id, service_idx",
                params,
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[service_sampler] latest_for_hosts(n={len(host_ids)}) skipped: {e}")
        return {}
    out: dict = {}
    for r in rows:
        hid = str(r[0])
        idx = int(r[1])
        out.setdefault(hid, {})[idx] = {
            "alive": bool(r[3]),
            "rtt_ms": r[4],
            "ts": int(r[2]),
            "error": r[5],
        }
    return out


def latest_per_port_all_for_hosts(host_ids: list[str]) -> dict:
    """Multi-host batched companion to
    :func:`latest_per_port_all_for_host`. Returns
    ``{host_id: {service_idx: [{port, alive, rtt_ms, ts, error}, ...], ...}, ...}``.

    ONE query for the WHOLE fleet. Same ROW_NUMBER() window pattern
    (partitioned by `host_id, service_idx, port`). Empty list / empty
    result → empty dict.
    """
    if not host_ids:
        return {}
    in_clause, params = _hostid_in_clause(host_ids)
    try:
        with db_conn() as c:
            # Latest row per (host_id, service_idx, port). Uses SQLite's
            # "bare columns take the value from the MAX() row" behaviour so a
            # single GROUP BY + MAX(ts) returns each group's newest sample —
            # this collapses to ONE index range-scan over
            # `idx_service_samples_chip_port_ts (host_id, service_idx, port,
            # ts DESC)` (EXPLAIN: SEARCH USING INDEX, no sort). The previous
            # ROW_NUMBER() window form materialised + sorted every matching
            # row (SQLite window functions always sort, even with a perfect
            # index) — a multi-second slow_query on a multi-million-row
            # sample table. The trailing MAX(ts) column is only there to
            # trigger the bare-column behaviour; we read columns 0-6.
            rows = c.execute(
                "SELECT host_id, service_idx, port, ts, alive, rtt_ms, error, MAX(ts) "
                f"FROM service_samples WHERE {in_clause} AND port > 0 "
                "GROUP BY host_id, service_idx, port "
                "ORDER BY host_id ASC, service_idx ASC, port ASC",
                params,
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[service_sampler] latest_per_port_all_for_hosts(n={len(host_ids)}) skipped: {e}")
        return {}
    out: dict = {}
    for r in rows:
        hid = str(r[0])
        idx = int(r[1])
        out.setdefault(hid, {}).setdefault(idx, []).append({
            "port": int(r[2]),
            "ts": int(r[3]),
            "alive": bool(r[4]),
            "rtt_ms": r[5],
            "error": r[6],
        })
    return out


def history_rollup_all_for_hosts(host_ids: list[str],
                                 max_points: int = _APPS_SPARK_MAX_POINTS) -> dict:
    """Multi-host batched companion to
    :func:`history_rollup_all_for_host`. Returns
    ``{host_id: {service_idx: [{ts, up}, ...], ...}, ...}``.

    ONE query for the WHOLE fleet. ROW_NUMBER() window partitioned by
    (host_id, service_idx) with the per-chip cap applied IN SQL
    (`WHERE rn <= ?`). Empty list / empty result → empty dict.
    """
    if not host_ids:
        return {}
    try:
        n = max(1, int(max_points))
    except (TypeError, ValueError):
        n = _APPS_SPARK_MAX_POINTS
    in_clause, params = _hostid_in_clause(host_ids)
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT host_id, service_idx, ts, alive FROM ("
                "  SELECT host_id, service_idx, ts, alive, "
                "         ROW_NUMBER() OVER ("
                "             PARTITION BY host_id, service_idx ORDER BY ts DESC"
                "         ) AS rn "
                f"  FROM service_samples WHERE {in_clause} AND port = 0"
                ") WHERE rn <= ? "
                "ORDER BY host_id ASC, service_idx ASC, ts ASC",
                params + [n],
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[service_sampler] history_rollup_all_for_hosts(n={len(host_ids)}) skipped: {e}")
        return {}
    out: dict = {}
    for r in rows:
        hid = str(r[0])
        idx = int(r[1])
        out.setdefault(hid, {}).setdefault(idx, []).append({
            "ts": int(r[2]),
            "up": bool(r[3]),
        })
    return out


# NOTE: a former `populate_host_service_merge(host_id, merged)` helper was
# removed — it gated on `merged["services"]` being the curated chip array,
# but the provider-merged dict's service key holds the Beszel systemd
# rollup (never the curated array), so the body never executed. Per-service
# `last_probe` is delivered by `_shape_host_apps(h)` in apps_routes.py
# (curated `h["services"]` + service_samples). See the project conventions "Backend
# populate_host_X_merge / shared list+detail helpers keyed on the WRONG
# merged-dict field silently no-op".


# Public aliases — main.py's manual probe-now endpoint reuses these
# helpers to share probe semantics with the lifespan sampler. The
# underscore-prefixed originals remain for in-module callers; the
# aliases are the documented entry points for cross-module callers
# so static analysis doesn't flag protected-member access.
probe_http = _probe_http
probe_tcp = _probe_tcp
persist_row = _persist_row
