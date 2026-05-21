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
    """Sampler tick cadence. 0 = inherit global stats interval; >0
    overrides per-service-probe. Mirrors the http_probe sampler shape.
    """
    iv = tuning.tuning_int(_Tunable.SERVICE_PROBE_SAMPLE_INTERVAL_SECONDS)
    if iv > 0:
        return max(30, iv)
    global_iv = tuning.tuning_int(_Tunable.STATS_SAMPLE_INTERVAL_SECONDS)
    return max(30, global_iv or 300)


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
            expected_status = _safe_int(probe_cfg.get("expected_status"), 0) or 0
            out.append({
                "host_id": hid,
                "service_idx": idx,
                "service_name": (svc.get("name") or svc.get("label") or url or f"service-{idx}").strip(),
                "probe_type": probe_type,
                "url": url,
                "host": parsed_host,
                "port": port,
                "path": path,
                "expected_status": expected_status,
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
            follow_redirects=False,
        ) as client:
            # Try HEAD first; fall back to GET on 405. Lots of services
            # don't implement HEAD.
            r = await client.head(url)
            if r.status_code == 405:
                r = await client.get(url)
        rtt_ms = int((time.monotonic() - started) * 1000)
        ok = (expected_status > 0
              and r.status_code == expected_status) or \
             (expected_status == 0 and 200 <= r.status_code < 400)
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
                 rtt_ms: Optional[int], error: Optional[str], ts: int) -> None:
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO service_samples "
                "(ts, host_id, service_idx, alive, rtt_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, host_id, service_idx, 1 if alive else 0,
                 rtt_ms, (error or "")[:200] or None),
            )
    except (sqlite3.Error, OSError) as e:
        print(f"[service_sampler] {host_id!r}/{service_idx} DB insert skipped: {e}")


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
                            # Most-recent row per (host_id, service_idx)
                            # — bail to MAX(ts) trick.
                            rows = c.execute(
                                "SELECT host_id, service_idx, alive FROM service_samples s1 "
                                "WHERE ts = (SELECT MAX(ts) FROM service_samples s2 "
                                "WHERE s2.host_id = s1.host_id AND s2.service_idx = s1.service_idx)"
                            ).fetchall()
                            for r in rows:
                                if not r[2]:
                                    previously_failing.add((r[0], int(r[1])))
                    except (sqlite3.Error, OSError):
                        pass

                    async def _probe_target(target: dict) -> tuple[dict, dict]:
                        async with sem:
                            if target["probe_type"] == "http" and target.get("url"):
                                result = await _probe_http(
                                    target["url"],
                                    target["expected_status"],
                                    timeout_s,
                                )
                            else:
                                result = await _probe_tcp(
                                    target["host"],
                                    int(target["port"] or 0),
                                    timeout_s,
                                )
                            return target, result

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
                        _persist_row(
                            host_id, svc_idx, alive,
                            result.get("rtt_ms"),
                            result.get("error"),
                            ts,
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
                                target_name=target["host_id"],
                                metadata={
                                    "url": target.get("url") or "",
                                    "host": target["host_id"],
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
    """Latest per-service probe outcome for one host — keyed by
    `service_idx`. Used by ``populate_host_service_merge`` to stamp
    `services[].last_probe` onto API responses.

    Returns ``{service_idx: {alive, rtt_ms, ts, error}, ...}``. Empty
    dict when no samples found.
    """
    if not host_id:
        return {}
    try:
        with db_conn() as c:
            # Most-recent row per service_idx for this host.
            rows = c.execute(
                "SELECT service_idx, ts, alive, rtt_ms, error "
                "FROM service_samples s1 "
                "WHERE host_id = ? "
                "AND ts = (SELECT MAX(ts) FROM service_samples s2 "
                "          WHERE s2.host_id = s1.host_id "
                "          AND s2.service_idx = s1.service_idx)",
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
